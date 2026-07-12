"""
Deal creation, persistent DM deal-panel, and dual-confirm completion.
All deal-panel button callbacks re-verify the user is actually a party to that deal.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select, text

import utils.cache as guild_cache
from config import DEFAULT_EMBED_COLOR
from database.engine import AsyncSessionLocal
from database.models import Deal, Listing, UserProfile, GuildConfig, Report
from utils.checks import is_globally_banned, is_guild_banned, get_or_create_profile
from utils.embeds import build_deal_panel_embed, build_error_embed, build_success_embed

logger = logging.getLogger(__name__)


# ─── Listing action view (Start Deal / Place Bid buttons on listing embeds) ───

class ListingActionView(discord.ui.View):
    """Persistent view attached to every posted listing embed."""

    def __init__(self, listing_id: int, format: str):
        super().__init__(timeout=None)
        self._listing_id = listing_id
        # Show the appropriate button based on format
        if format == "auction":
            self.start_deal_btn.label = "🔨 Place Bid"
            self.start_deal_btn.custom_id = f"listing:place_bid:{listing_id}"
        else:
            self.start_deal_btn.custom_id = f"listing:start_deal:{listing_id}"

    @discord.ui.button(
        label="🤝 Start Deal",
        style=discord.ButtonStyle.success,
        custom_id="listing:start_deal:0",  # overridden in __init__
    )
    async def start_deal_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        # Resolve listing_id from custom_id
        parts = button.custom_id.split(":")
        listing_id = int(parts[2])
        action = parts[1]

        if action == "place_bid":
            await _launch_bid_modal(interaction, listing_id)
        else:
            await _create_deal(interaction, listing_id)


async def _launch_bid_modal(interaction: discord.Interaction, listing_id: int):
    from cogs.bidding import PlaceBidModal
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Listing).where(Listing.listing_id == listing_id))
        listing = result.scalar_one_or_none()
    if not listing:
        await interaction.followup.send(embed=build_error_embed("Listing not found."), ephemeral=True)
        return
    modal = PlaceBidModal(
        listing_id=listing_id,
        current_bid=float(listing.highest_bid) if listing.highest_bid else None,
        currency=listing.currency_label,
        guild_id=interaction.guild_id,
    )
    await interaction.followup.send(
        content="Opening bid modal…", ephemeral=True
    )
    # Can't send modal after defer; inform user to click the button fresh
    await interaction.followup.send(
        embed=discord.Embed(
            description="Click the **Place Bid** button on the listing embed (not deferred) to open the bid form.",
            color=0x5865F2,
        ),
        ephemeral=True,
    )


# ─── Direct-bid button that properly opens a modal ────────────────────────────

class BidButtonView(discord.ui.View):
    """Non-persistent bid button that opens a modal directly (no defer)."""

    def __init__(self, listing_id: int, current_bid, currency: str, guild_id: int):
        super().__init__(timeout=None)
        self._listing_id = listing_id
        self._current_bid = current_bid
        self._currency = currency
        self._guild_id = guild_id
        self.bid_btn.custom_id = f"bid:modal:{listing_id}"

    @discord.ui.button(
        label="🔨 Place Bid",
        style=discord.ButtonStyle.primary,
        custom_id="bid:modal:0",
    )
    async def bid_btn(self, interaction: discord.Interaction, _):
        from cogs.bidding import PlaceBidModal
        modal = PlaceBidModal(
            listing_id=self._listing_id,
            current_bid=self._current_bid,
            currency=self._currency,
            guild_id=self._guild_id,
        )
        await interaction.response.send_modal(modal)


# ─── Deal creation ────────────────────────────────────────────────────────────

async def _create_deal(interaction: discord.Interaction, listing_id: int):
    user_id = interaction.user.id

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Listing).where(Listing.listing_id == listing_id))
        listing = result.scalar_one_or_none()

    if not listing:
        await interaction.followup.send(embed=build_error_embed("Listing not found."), ephemeral=True)
        return

    # ── (a) listing status ────────────────────────────────────────────────
    if listing.status != "active":
        await interaction.followup.send(
            embed=build_error_embed("This listing is no longer available."), ephemeral=True
        )
        return

    seller_id = listing.seller_id

    # ── (b) ban checks (both parties) ────────────────────────────────────
    for uid, label in [(user_id, "You are"), (seller_id, "The seller is")]:
        if await is_globally_banned(uid):
            await interaction.followup.send(
                embed=build_error_embed(f"{label} globally banned from the marketplace."),
                ephemeral=True,
            )
            return
        if await is_guild_banned(listing.origin_guild_id, uid):
            await interaction.followup.send(
                embed=build_error_embed(f"{label} banned from this server's marketplace."),
                ephemeral=True,
            )
            return

    # ── (c) one active deal per user ──────────────────────────────────────
    async with AsyncSessionLocal() as session:
        for uid in (user_id, seller_id):
            r = await session.execute(
                select(Deal).where(Deal.status == "active").where(
                    (Deal.initiator_id == uid) | (Deal.seller_id == uid)
                )
            )
            if r.scalar_one_or_none():
                who = "You are" if uid == user_id else "The seller is"
                await interaction.followup.send(
                    embed=build_error_embed(f"{who} already in the middle of a deal."),
                    ephemeral=True,
                )
                return

        # ── (d) pending review gate ───────────────────────────────────────
        for uid in (user_id, seller_id):
            r = await session.execute(
                select(UserProfile.pending_review_deal_id).where(UserProfile.user_id == uid)
            )
            pending = r.scalar_one_or_none()
            if pending:
                who = "You haven't" if uid == user_id else "The seller hasn't"
                await interaction.followup.send(
                    embed=build_error_embed(
                        f"Deal cannot be created — {who} rated the other person from your last deal yet."
                    ),
                    ephemeral=True,
                )
                return

    # ── (e) self-deal prevention ──────────────────────────────────────────
    if user_id == seller_id:
        await interaction.followup.send(
            embed=build_error_embed("You can't start a deal with yourself."), ephemeral=True
        )
        return

    # ── Atomic listing claim ──────────────────────────────────────────────
    async with AsyncSessionLocal() as session:
        async with session.begin():
            upd = await session.execute(
                text(
                    "UPDATE listings SET status='pending_deal' "
                    "WHERE listing_id=:lid AND status='active' RETURNING listing_id"
                ),
                {"lid": listing_id},
            )
            if not upd.fetchone():
                await interaction.followup.send(
                    embed=build_error_embed("This listing was just claimed by someone else."),
                    ephemeral=True,
                )
                return

            deal = Deal(
                listing_id=listing_id,
                initiator_id=user_id,
                seller_id=seller_id,
                status="active",
                last_activity_at=datetime.now(timezone.utc),
            )
            session.add(deal)
            await session.flush()
            deal_id = deal.deal_id

    # ── DM both parties ───────────────────────────────────────────────────
    bot = interaction.client
    initiator_user = interaction.user
    seller_user = bot.get_user(seller_id) or await bot.fetch_user(seller_id)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Listing).where(Listing.listing_id == listing_id))
        db_listing = result.scalar_one()

    view = DealPanelView(deal_id=deal_id)

    # Get guild color
    config = guild_cache.get(interaction.guild_id)
    color = int(config.embed_color.lstrip("#"), 16) if config else DEFAULT_EMBED_COLOR

    deal_embed = build_deal_panel_embed(
        type("D", (), {"deal_id": deal_id, "status": "active"})(),
        db_listing,
        color=color,
    )

    initiator_msg_id = None
    seller_msg_id = None

    try:
        msg_i = await initiator_user.send(embed=deal_embed, view=view)
        initiator_msg_id = msg_i.id
    except discord.Forbidden:
        # Notify seller that initiator's DMs are closed
        try:
            await seller_user.send(
                embed=build_error_embed(
                    f"Deal #{deal_id} was started but I couldn't DM the buyer — their DMs are closed."
                )
            )
        except discord.Forbidden:
            pass

    try:
        msg_s = await seller_user.send(embed=deal_embed, view=view)
        seller_msg_id = msg_s.id
    except discord.Forbidden:
        if initiator_msg_id:
            try:
                await initiator_user.send(
                    embed=build_error_embed(
                        f"Deal #{deal_id} started but I couldn't DM the seller — their DMs are closed."
                    )
                )
            except discord.Forbidden:
                pass

    # Save DM message IDs
    async with AsyncSessionLocal() as session:
        async with session.begin():
            r = await session.execute(select(Deal).where(Deal.deal_id == deal_id))
            deal_db = r.scalar_one()
            deal_db.dm_message_id_initiator = initiator_msg_id
            deal_db.dm_message_id_seller = seller_msg_id

    await interaction.followup.send(
        embed=build_success_embed(
            f"Deal #{deal_id} started! Check your DMs for the deal panel."
        ),
        ephemeral=True,
    )


# ─── Deal panel persistent view ───────────────────────────────────────────────

class DealPanelView(discord.ui.View):
    """
    Persistent DM deal panel.  custom_id encodes deal_id so we can re-register after restart.
    Every callback re-verifies the user is actually a party to this deal.
    """

    def __init__(self, deal_id: int):
        super().__init__(timeout=None)
        self._deal_id = deal_id
        self.mark_complete_btn.custom_id = f"deal:mark_complete:{deal_id}"
        self.cancel_btn.custom_id = f"deal:cancel:{deal_id}"
        self.report_btn.custom_id = f"deal:report:{deal_id}"

    async def _verify_party(self, interaction: discord.Interaction) -> Deal | None:
        """Return the Deal if the user is a party; send error and return None otherwise."""
        async with AsyncSessionLocal() as session:
            r = await session.execute(select(Deal).where(Deal.deal_id == self._deal_id))
            deal = r.scalar_one_or_none()
        if not deal:
            await interaction.followup.send(embed=build_error_embed("Deal not found."), ephemeral=True)
            return None
        if interaction.user.id not in (deal.initiator_id, deal.seller_id):
            await interaction.followup.send(
                embed=build_error_embed("You are not a party to this deal."), ephemeral=True
            )
            return None
        return deal

    @discord.ui.button(
        label="✅ Mark Complete",
        style=discord.ButtonStyle.success,
        custom_id="deal:mark_complete:0",
    )
    async def mark_complete_btn(self, interaction: discord.Interaction, _):
        await interaction.response.defer(ephemeral=True)
        # Update last_activity_at
        parts = interaction.data["custom_id"].split(":")
        self._deal_id = int(parts[2])
        deal = await self._verify_party(interaction)
        if not deal:
            return
        if deal.status != "active":
            await interaction.followup.send(embed=build_error_embed("This deal is no longer active."), ephemeral=True)
            return

        async with AsyncSessionLocal() as session:
            async with session.begin():
                r = await session.execute(select(Deal).where(Deal.deal_id == self._deal_id))
                deal_db = r.scalar_one()
                deal_db.last_activity_at = datetime.now(timezone.utc)

                if interaction.user.id == deal_db.initiator_id:
                    deal_db.initiator_confirmed = True
                else:
                    deal_db.seller_confirmed = True

                both_confirmed = deal_db.initiator_confirmed and deal_db.seller_confirmed

                if both_confirmed:
                    deal_db.status = "completed"
                    deal_db.ended_at = datetime.now(timezone.utc)
                    deal_db.end_reason = "completed"
                    deal_id = deal_db.deal_id
                    initiator_id = deal_db.initiator_id
                    seller_id = deal_db.seller_id
                    listing_id = deal_db.listing_id

                    # Update listing status
                    rl = await session.execute(select(Listing).where(Listing.listing_id == listing_id))
                    listing = rl.scalar_one_or_none()
                    if listing:
                        listing.status = "completed"

                    # Set pending review on both parties
                    for uid in (initiator_id, seller_id):
                        profile = await get_or_create_profile(session, uid)
                        profile.pending_review_deal_id = deal_id

        if both_confirmed:
            bot = interaction.client
            await _send_review_prompts(bot, deal_id, initiator_id, seller_id)
            await interaction.followup.send(
                embed=build_success_embed("🎉 Deal completed! Both parties have confirmed. Check your DMs for a review prompt."),
                ephemeral=True,
            )
        else:
            other = "The seller" if interaction.user.id == deal.initiator_id else "The buyer"
            await interaction.followup.send(
                embed=build_success_embed(f"Your confirmation recorded. Waiting for {other} to confirm."),
                ephemeral=True,
            )

    @discord.ui.button(
        label="❌ Cancel Deal",
        style=discord.ButtonStyle.danger,
        custom_id="deal:cancel:0",
    )
    async def cancel_btn(self, interaction: discord.Interaction, _):
        await interaction.response.defer(ephemeral=True)
        parts = interaction.data["custom_id"].split(":")
        self._deal_id = int(parts[2])
        deal = await self._verify_party(interaction)
        if not deal:
            return
        if deal.status != "active":
            await interaction.followup.send(embed=build_error_embed("This deal is no longer active."), ephemeral=True)
            return

        is_initiator = interaction.user.id == deal.initiator_id
        end_reason = "cancelled_by_initiator" if is_initiator else "cancelled_by_seller"

        async with AsyncSessionLocal() as session:
            async with session.begin():
                r = await session.execute(select(Deal).where(Deal.deal_id == self._deal_id))
                deal_db = r.scalar_one()
                deal_db.status = "cancelled"
                deal_db.ended_at = datetime.now(timezone.utc)
                deal_db.end_reason = end_reason
                listing_id = deal_db.listing_id

                # Reopen listing unless it was an already-ended auction
                rl = await session.execute(select(Listing).where(Listing.listing_id == listing_id))
                listing = rl.scalar_one_or_none()
                if listing and not (listing.format == "auction" and listing.auction_end_at and listing.auction_end_at <= datetime.now(timezone.utc)):
                    listing.status = "active"

        await interaction.followup.send(
            embed=build_success_embed("Deal cancelled. The listing has been re-opened."),
            ephemeral=True,
        )

    @discord.ui.button(
        label="🚨 Report Issue",
        style=discord.ButtonStyle.secondary,
        custom_id="deal:report:0",
    )
    async def report_btn(self, interaction: discord.Interaction, _):
        parts = interaction.data["custom_id"].split(":")
        self._deal_id = int(parts[2])
        await interaction.response.send_modal(ReportIssueModal(self._deal_id))


class ReportIssueModal(discord.ui.Modal, title="Report Issue"):
    reason_input = discord.ui.TextInput(
        label="Describe the issue",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        placeholder="Explain what went wrong…",
    )

    def __init__(self, deal_id: int):
        super().__init__()
        self._deal_id = deal_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        deal_id = self._deal_id
        reporter_id = interaction.user.id

        async with AsyncSessionLocal() as session:
            r = await session.execute(select(Deal).where(Deal.deal_id == deal_id))
            deal = r.scalar_one_or_none()

        if not deal or reporter_id not in (deal.initiator_id, deal.seller_id):
            await interaction.followup.send(embed=build_error_embed("Invalid deal."), ephemeral=True)
            return

        reported_id = deal.seller_id if reporter_id == deal.initiator_id else deal.initiator_id

        async with AsyncSessionLocal() as session:
            r = await session.execute(select(Listing).where(Listing.listing_id == deal.listing_id))
            listing = r.scalar_one_or_none()

        origin_guild = listing.origin_guild_id if listing else 0

        async with AsyncSessionLocal() as session:
            async with session.begin():
                report = Report(
                    deal_id=deal_id,
                    reporter_id=reporter_id,
                    reported_user_id=reported_id,
                    reason=self.reason_input.value,
                    guild_id=origin_guild,
                    status="open",
                )
                session.add(report)
                await session.flush()
                report_id = report.report_id

        # Post to log channel
        config = guild_cache.get(origin_guild)
        if config is None:
            async with AsyncSessionLocal() as session:
                rc = await session.execute(select(GuildConfig).where(GuildConfig.guild_id == origin_guild))
                config = rc.scalar_one_or_none()

        if config and config.log_channel_id:
            channel = interaction.client.get_channel(config.log_channel_id)
            if channel:
                log_embed = discord.Embed(
                    title=f"🚨 Report #{report_id} — Deal #{deal_id}",
                    description=self.reason_input.value,
                    color=discord.Color.orange(),
                )
                log_embed.add_field(name="Reporter", value=f"<@{reporter_id}>", inline=True)
                log_embed.add_field(name="Reported", value=f"<@{reported_id}>", inline=True)
                try:
                    await channel.send(embed=log_embed)
                except (discord.Forbidden, discord.HTTPException) as exc:
                    logger.warning("Failed to post report to log channel: %s", exc)

        await interaction.followup.send(
            embed=build_success_embed(f"Report #{report_id} submitted to server moderators."),
            ephemeral=True,
        )


async def _send_review_prompts(bot, deal_id: int, initiator_id: int, seller_id: int):
    """DM both parties a review prompt after deal completion."""
    for reviewer, reviewee in [(initiator_id, seller_id), (seller_id, initiator_id)]:
        user = bot.get_user(reviewer) or await bot.fetch_user(reviewer)
        if user:
            view = ReviewPromptView(deal_id=deal_id, reviewer_id=reviewer, reviewee_id=reviewee)
            embed = discord.Embed(
                title="⭐ Rate Your Trade",
                description=f"Deal #{deal_id} is complete! Please rate your trading partner.",
                color=0x5865F2,
            )
            try:
                await user.send(embed=embed, view=view)
            except discord.Forbidden:
                pass


class ReviewPromptView(discord.ui.View):
    def __init__(self, deal_id: int, reviewer_id: int, reviewee_id: int):
        super().__init__(timeout=None)
        self._deal_id = deal_id
        self._reviewer_id = reviewer_id
        self._reviewee_id = reviewee_id
        self.rate_btn.custom_id = f"review:open:{deal_id}:{reviewer_id}:{reviewee_id}"

    @discord.ui.button(
        label="⭐ Rate This Trade",
        style=discord.ButtonStyle.primary,
        custom_id="review:open:0:0:0",
    )
    async def rate_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        parts = button.custom_id.split(":")
        deal_id = int(parts[2])
        reviewer_id = int(parts[3])
        reviewee_id = int(parts[4])
        if interaction.user.id != reviewer_id:
            await interaction.response.send_message("This review prompt isn't for you.", ephemeral=True)
            return
        from cogs.reviews import ReviewModal
        await interaction.response.send_modal(ReviewModal(deal_id=deal_id, reviewer_id=reviewer_id, reviewee_id=reviewee_id))


class Deals(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    await bot.add_cog(Deals(bot))
