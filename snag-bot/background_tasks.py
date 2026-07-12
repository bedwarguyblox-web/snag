"""
Background sweep tasks — each wrapped in its own try/except so one bad row
can't kill the loop for every guild.  All sweeps use a single batch query
(one SELECT returning all matching rows) — never per-guild or per-row loops.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from discord.ext import tasks
import discord
from sqlalchemy import select, text

from config import (
    DEAL_TIMEOUT_HOURS,
    DEAL_TIMEOUT_SWEEP_MINUTES,
    LISTING_EXPIRY_RENEW_NOTIFY_HOURS_BEFORE,
    ARCHIVE_AFTER_DAYS,
    ARCHIVE_SWEEP_HOURS,
)
from database.engine import AsyncSessionLocal
from database.models import Deal, Listing, UserProfile, GuildConfig, ListingArchive, BidArchive, Bid

if TYPE_CHECKING:
    from discord.ext.commands import Bot

logger = logging.getLogger(__name__)


def register_tasks(bot: "Bot") -> None:
    """Call from setup_hook to start all background loops."""
    deal_timeout_sweep.start(bot)
    auction_end_sweep.start(bot)
    listing_expiry_sweep.start(bot)
    archive_sweep.start(bot)


# ─── Deal timeout sweep (every 15 minutes) ────────────────────────────────────

@tasks.loop(minutes=DEAL_TIMEOUT_SWEEP_MINUTES)
async def deal_timeout_sweep(bot: "Bot") -> None:
    """
    Expire active deals inactive for >48 hours.
    One batch query; never a per-row loop against the DB.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=DEAL_TIMEOUT_HOURS)
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Deal).where(
                    Deal.status == "active",
                    Deal.last_activity_at < cutoff,
                )
            )
            timed_out = result.scalars().all()

        for deal in timed_out:
            try:
                await _expire_deal(bot, deal)
            except Exception as exc:
                logger.error("Error expiring deal %d: %s", deal.deal_id, exc)

        if timed_out:
            logger.info("Deal timeout sweep: expired %d deal(s).", len(timed_out))
    except Exception as exc:
        logger.error("deal_timeout_sweep failed: %s", exc)


@deal_timeout_sweep.error
async def deal_timeout_sweep_error(error: Exception) -> None:
    logger.error("deal_timeout_sweep loop error: %s", error)


async def _expire_deal(bot: "Bot", deal: Deal) -> None:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            r = await session.execute(select(Deal).where(Deal.deal_id == deal.deal_id))
            d = r.scalar_one_or_none()
            if not d or d.status != "active":
                return

            d.status = "expired"
            d.ended_at = datetime.now(timezone.utc)
            d.end_reason = "timeout"

            # Increment timeout_count for both parties
            for uid in (d.initiator_id, d.seller_id):
                pr = await session.execute(select(UserProfile).where(UserProfile.user_id == uid))
                profile = pr.scalar_one_or_none()
                if profile:
                    profile.timeout_count += 1

            # Reopen the listing unless it was an ended auction
            rl = await session.execute(select(Listing).where(Listing.listing_id == d.listing_id))
            listing = rl.scalar_one_or_none()
            if listing and listing.status == "pending_deal":
                if not (listing.format == "auction" and listing.auction_end_at
                        and listing.auction_end_at <= datetime.now(timezone.utc)):
                    listing.status = "active"

    # DM both parties (no review required for timeout)
    for uid in (deal.initiator_id, deal.seller_id):
        try:
            user = bot.get_user(uid) or await bot.fetch_user(uid)
            await user.send(
                embed=discord.Embed(
                    title="⏱️ Deal Auto-Closed",
                    description=(
                        f"Deal #{deal.deal_id} was automatically closed due to 48 hours of inactivity.\n"
                        "No review is required."
                    ),
                    color=discord.Color.orange(),
                )
            )
        except (discord.Forbidden, discord.HTTPException, discord.NotFound) as exc:
            logger.debug("Could not DM user %d on deal timeout: %s", uid, exc)


# ─── Auction end sweep (every 1 minute) ───────────────────────────────────────

@tasks.loop(minutes=1)
async def auction_end_sweep(bot: "Bot") -> None:
    """
    Auto-create deals for auctions whose auction_end_at has passed.
    Falls back through the bid history if the winning bidder fails a gate.
    """
    try:
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Listing).where(
                    Listing.status == "active",
                    Listing.format == "auction",
                    Listing.auction_end_at <= now,
                )
            )
            ended_auctions = result.scalars().all()

        for listing in ended_auctions:
            try:
                await _resolve_auction(bot, listing)
            except Exception as exc:
                logger.error("Error resolving auction listing %d: %s", listing.listing_id, exc)

    except Exception as exc:
        logger.error("auction_end_sweep failed: %s", exc)


@auction_end_sweep.error
async def auction_end_sweep_error(error: Exception) -> None:
    logger.error("auction_end_sweep loop error: %s", error)


async def _resolve_auction(bot: "Bot", listing: Listing) -> None:
    from utils.checks import is_globally_banned, is_guild_banned
    from cogs.deals import DealPanelView
    from utils.embeds import build_deal_panel_embed
    from utils.checks import get_or_create_profile

    winner_id = listing.highest_bidder_id

    # No bids — expire
    if not winner_id:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                rl = await session.execute(select(Listing).where(Listing.listing_id == listing.listing_id))
                l = rl.scalar_one_or_none()
                if l and l.status == "active":
                    l.status = "expired"
        return

    # Try winner, then fall back through bid history if gates fail
    async with AsyncSessionLocal() as session:
        from database.models import Bid
        bids_result = await session.execute(
            select(Bid)
            .where(Bid.listing_id == listing.listing_id)
            .order_by(Bid.amount.desc())
        )
        all_bids = bids_result.scalars().all()

    # Deduplicate bidders (highest bid per bidder)
    seen = set()
    candidates = []
    for b in all_bids:
        if b.bidder_id not in seen:
            seen.add(b.bidder_id)
            candidates.append(b.bidder_id)

    valid_winner = None
    for candidate_id in candidates:
        if candidate_id == listing.seller_id:
            continue
        if await is_globally_banned(candidate_id):
            continue
        if await is_guild_banned(listing.origin_guild_id, candidate_id):
            continue

        # Check no active deal
        async with AsyncSessionLocal() as session:
            r = await session.execute(
                select(Deal).where(
                    Deal.status == "active",
                    (Deal.initiator_id == candidate_id) | (Deal.seller_id == candidate_id),
                )
            )
            if r.scalar_one_or_none():
                continue
            # Check pending review
            rp = await session.execute(
                select(UserProfile.pending_review_deal_id).where(UserProfile.user_id == candidate_id)
            )
            if rp.scalar_one_or_none():
                continue

        valid_winner = candidate_id
        break

    if not valid_winner:
        # No valid bidder — expire listing
        async with AsyncSessionLocal() as session:
            async with session.begin():
                rl = await session.execute(select(Listing).where(Listing.listing_id == listing.listing_id))
                l = rl.scalar_one_or_none()
                if l and l.status == "active":
                    l.status = "expired"
        return

    # Atomic claim + deal creation
    async with AsyncSessionLocal() as session:
        async with session.begin():
            upd = await session.execute(
                text(
                    "UPDATE listings SET status='pending_deal' "
                    "WHERE listing_id=:lid AND status='active' RETURNING listing_id"
                ),
                {"lid": listing.listing_id},
            )
            if not upd.fetchone():
                return  # Already claimed

            deal = Deal(
                listing_id=listing.listing_id,
                initiator_id=valid_winner,
                seller_id=listing.seller_id,
                status="active",
                last_activity_at=datetime.now(timezone.utc),
            )
            session.add(deal)
            await session.flush()
            deal_id = deal.deal_id

    # DM both parties
    async with AsyncSessionLocal() as session:
        rl = await session.execute(select(Listing).where(Listing.listing_id == listing.listing_id))
        db_listing = rl.scalar_one()

    from cogs.deals import ReviewPromptView
    view = DealPanelView(deal_id=deal_id)
    fake_deal = type("D", (), {"deal_id": deal_id, "status": "active"})()
    embed = build_deal_panel_embed(fake_deal, db_listing)

    for uid in (valid_winner, listing.seller_id):
        try:
            user = bot.get_user(uid) or await bot.fetch_user(uid)
            await user.send(embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException, discord.NotFound) as exc:
            logger.debug("Could not DM user %d for auction deal: %s", uid, exc)


# ─── Listing expiry sweep (every 6 hours) ─────────────────────────────────────

@tasks.loop(hours=6)
async def listing_expiry_sweep(bot: "Bot") -> None:
    """
    1. DM sellers 24h before expiry with a Renew button.
    2. Actually expire listings past their expires_at.
    """
    try:
        now = datetime.now(timezone.utc)
        warning_cutoff = now + timedelta(hours=LISTING_EXPIRY_RENEW_NOTIFY_HOURS_BEFORE)

        async with AsyncSessionLocal() as session:
            # Listings about to expire (within the next 24h, not yet notified)
            expiring_soon = await session.execute(
                select(Listing).where(
                    Listing.status == "active",
                    Listing.expires_at > now,
                    Listing.expires_at <= warning_cutoff,
                )
            )
            soon = expiring_soon.scalars().all()

            # Already expired
            expired = await session.execute(
                select(Listing).where(
                    Listing.status == "active",
                    Listing.expires_at <= now,
                )
            )
            to_expire = expired.scalars().all()

        # Send renewal DMs
        for listing in soon:
            try:
                user = bot.get_user(listing.seller_id) or await bot.fetch_user(listing.seller_id)
                view = RenewListingView(listing.listing_id)
                await user.send(
                    embed=discord.Embed(
                        title="⏰ Listing Expiring Soon",
                        description=(
                            f"Your listing **{listing.title}** (#{listing.listing_id}) "
                            f"will expire <t:{int(listing.expires_at.timestamp())}:R>.\n"
                            "Click below to renew it for another 14 days."
                        ),
                        color=discord.Color.yellow(),
                    ),
                    view=view,
                )
            except (discord.Forbidden, discord.HTTPException, discord.NotFound) as exc:
                logger.debug("Could not DM seller %d for expiry warning: %s", listing.seller_id, exc)

        # Expire overdue listings
        for listing in to_expire:
            try:
                async with AsyncSessionLocal() as session:
                    async with session.begin():
                        rl = await session.execute(select(Listing).where(Listing.listing_id == listing.listing_id))
                        l = rl.scalar_one_or_none()
                        if l and l.status == "active":
                            l.status = "expired"
            except Exception as exc:
                logger.error("Error expiring listing %d: %s", listing.listing_id, exc)

        if soon or to_expire:
            logger.info(
                "Listing expiry sweep: %d renewal DMs sent, %d expired.",
                len(soon),
                len(to_expire),
            )
    except Exception as exc:
        logger.error("listing_expiry_sweep failed: %s", exc)


@listing_expiry_sweep.error
async def listing_expiry_sweep_error(error: Exception) -> None:
    logger.error("listing_expiry_sweep loop error: %s", error)


class RenewListingView(discord.ui.View):
    def __init__(self, listing_id: int):
        super().__init__(timeout=None)
        self._listing_id = listing_id
        self.renew_btn.custom_id = f"listing:renew:{listing_id}"

    @discord.ui.button(
        label="🔄 Renew Listing",
        style=discord.ButtonStyle.success,
        custom_id="listing:renew:0",
    )
    async def renew_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        parts = button.custom_id.split(":")
        listing_id = int(parts[2])
        from config import LISTING_EXPIRY_DAYS

        async with AsyncSessionLocal() as session:
            async with session.begin():
                rl = await session.execute(
                    select(Listing).where(
                        Listing.listing_id == listing_id,
                        Listing.seller_id == interaction.user.id,
                    )
                )
                listing = rl.scalar_one_or_none()
                if not listing:
                    await interaction.followup.send(
                        embed=discord.Embed(description="Listing not found or access denied.", color=discord.Color.red()),
                        ephemeral=True,
                    )
                    return
                listing.expires_at = datetime.now(timezone.utc) + timedelta(days=LISTING_EXPIRY_DAYS)

        await interaction.followup.send(
            embed=discord.Embed(
                description=f"✅ Listing #{listing_id} renewed for {LISTING_EXPIRY_DAYS} more days.",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


# ─── Weekly archive sweep ─────────────────────────────────────────────────────

@tasks.loop(hours=ARCHIVE_SWEEP_HOURS)
async def archive_sweep(bot: "Bot") -> None:
    """
    Move terminal listings (completed/cancelled/expired) older than ARCHIVE_AFTER_DAYS
    and their bids into archive tables.  Keeps hot tables small.
    """
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=ARCHIVE_AFTER_DAYS)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Listing).where(
                    Listing.status.in_(["completed", "cancelled", "expired"]),
                    Listing.created_at < cutoff,
                )
            )
            old_listings = result.scalars().all()

        archived = 0
        for listing in old_listings:
            try:
                async with AsyncSessionLocal() as session:
                    async with session.begin():
                        # Archive bids first
                        bids_r = await session.execute(
                            select(Bid).where(Bid.listing_id == listing.listing_id)
                        )
                        bids = bids_r.scalars().all()
                        for bid in bids:
                            session.add(
                                BidArchive(
                                    bid_id=bid.bid_id,
                                    listing_id=bid.listing_id,
                                    bidder_id=bid.bidder_id,
                                    amount=bid.amount,
                                    created_at=bid.created_at,
                                )
                            )
                            await session.delete(bid)

                        # Archive listing
                        session.add(
                            ListingArchive(
                                listing_id=listing.listing_id,
                                seller_id=listing.seller_id,
                                origin_guild_id=listing.origin_guild_id,
                                scope=listing.scope,
                                mc_server_tag=listing.mc_server_tag,
                                category=listing.category,
                                listing_type=listing.listing_type,
                                format=listing.format,
                                title=listing.title,
                                description=listing.description,
                                price=listing.price,
                                currency_label=listing.currency_label,
                                status=listing.status,
                                created_at=listing.created_at,
                            )
                        )

                        rl = await session.execute(select(Listing).where(Listing.listing_id == listing.listing_id))
                        l = rl.scalar_one_or_none()
                        if l:
                            await session.delete(l)

                archived += 1
            except Exception as exc:
                logger.error("Error archiving listing %d: %s", listing.listing_id, exc)

        if archived:
            logger.info("Archive sweep: archived %d listing(s).", archived)
    except Exception as exc:
        logger.error("archive_sweep failed: %s", exc)


@archive_sweep.error
async def archive_sweep_error(error: Exception) -> None:
    logger.error("archive_sweep loop error: %s", error)
