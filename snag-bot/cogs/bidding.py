"""
Place Bid flow for auction-format listings.
Includes atomic bid acceptance, anti-snipe extension, per-user cooldowns.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import discord
from discord.ext import commands
from sqlalchemy import select, text

from config import (
    MIN_BID_INCREMENT_PERCENT,
    MIN_BID_INCREMENT_ABSOLUTE,
    BID_COOLDOWN_SECONDS,
    MAX_BIDS_PER_LISTING,
    ANTI_SNIPE_WINDOW_SECONDS,
    ANTI_SNIPE_EXTENSION_SECONDS,
    MAX_AUCTION_EXTENSIONS,
)
from database.engine import AsyncSessionLocal
from database.models import Listing, Bid, UserProfile
from utils.checks import check_marketplace_access, is_globally_banned, is_guild_banned
from utils.embeds import build_error_embed, build_success_embed
from utils.parsing import parse_amount

logger = logging.getLogger(__name__)

# In-memory per-user per-listing bid cooldown: {(user_id, listing_id): last_bid_timestamp}
_bid_cooldowns: dict[tuple[int, int], float] = {}


class PlaceBidModal(discord.ui.Modal, title="Place Bid"):
    def __init__(self, listing_id: int, current_bid: float | None, currency: str, guild_id: int):
        super().__init__()
        self._listing_id = listing_id
        self._current_bid = current_bid
        self._currency = currency
        self._guild_id = guild_id
        self.bid_input = discord.ui.TextInput(
            label=f"Your bid (current: {current_bid or 'none'} {currency})",
            placeholder="e.g. 500, 10k, 2.5m, 1b",
            max_length=20,
        )
        self.add_item(self.bid_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        user_id = interaction.user.id
        listing_id = self._listing_id

        # ── (e) in-memory bid cooldown ─────────────────────────────────────
        now = time.monotonic()
        last = _bid_cooldowns.get((user_id, listing_id), 0)
        if now - last < BID_COOLDOWN_SECONDS:
            remaining = int(BID_COOLDOWN_SECONDS - (now - last))
            await interaction.followup.send(
                embed=build_error_embed(f"Please wait {remaining}s before bidding again."),
                ephemeral=True,
            )
            return

        # Parse amount (supports shorthand like 10k, 2.5m, 1b)
        try:
            amount = parse_amount(self.bid_input.value.strip())
        except ValueError:
            await interaction.followup.send(
                embed=build_error_embed(
                    "Bid must be a number, optionally with a k/m/b suffix (e.g. 500, 10k, 2.5m, 1b)."
                ),
                ephemeral=True,
            )
            return

        result = await _process_bid(interaction, listing_id, user_id, self._guild_id, amount)
        if result:
            _bid_cooldowns[(user_id, listing_id)] = time.monotonic()


async def _process_bid(
    interaction: discord.Interaction,
    listing_id: int,
    user_id: int,
    guild_id: int,
    amount: float,
) -> bool:
    """
    Process a bid with all validation checks.
    Returns True if bid was accepted.
    """
    async with AsyncSessionLocal() as session:
        # ── (a) listing state check ────────────────────────────────────────
        result = await session.execute(
            select(Listing).where(Listing.listing_id == listing_id)
        )
        listing = result.scalar_one_or_none()
        if not listing:
            await interaction.followup.send(embed=build_error_embed("Listing not found."), ephemeral=True)
            return False
        if listing.status != "active" or listing.format != "auction":
            await interaction.followup.send(embed=build_error_embed("This auction has ended."), ephemeral=True)
            return False
        now_utc = datetime.now(timezone.utc)
        if listing.auction_end_at and listing.auction_end_at <= now_utc:
            await interaction.followup.send(embed=build_error_embed("This auction has ended."), ephemeral=True)
            return False

        # ── (b) self-bid prevention ────────────────────────────────────────
        if user_id == listing.seller_id:
            await interaction.followup.send(
                embed=build_error_embed("You can't bid on your own listing."), ephemeral=True
            )
            return False

        # ── (c) ban checks ─────────────────────────────────────────────────
        if await is_globally_banned(user_id):
            await interaction.followup.send(
                embed=build_error_embed("You are globally banned from the marketplace."), ephemeral=True
            )
            return False
        if await is_guild_banned(listing.origin_guild_id, user_id):
            await interaction.followup.send(
                embed=build_error_embed("You are banned from this listing's server."), ephemeral=True
            )
            return False

        # ── (d) bid increment validation ──────────────────────────────────
        current = float(listing.highest_bid) if listing.highest_bid is not None else float(listing.price or 0)
        min_increment = max(
            current * (MIN_BID_INCREMENT_PERCENT / 100.0),
            MIN_BID_INCREMENT_ABSOLUTE,
        )
        min_qualifying = current + min_increment
        if amount < min_qualifying:
            await interaction.followup.send(
                embed=build_error_embed(
                    f"Minimum qualifying bid: **{min_qualifying:.2f} {listing.currency_label}** "
                    f"(must exceed current by ≥{MIN_BID_INCREMENT_PERCENT}% or {MIN_BID_INCREMENT_ABSOLUTE} unit)."
                ),
                ephemeral=True,
            )
            return False

        # ── (f) bid cap ────────────────────────────────────────────────────
        bid_count_result = await session.execute(
            select(Bid).where(Bid.listing_id == listing_id)
        )
        bid_count = len(bid_count_result.scalars().all())
        if bid_count >= MAX_BIDS_PER_LISTING:
            await interaction.followup.send(
                embed=build_error_embed("This auction has reached its bid limit."), ephemeral=True
            )
            return False

        # Capture for post-commit notifications — must happen before session closes.
        # expire_on_commit=False means these are readable after the session exits.
        previous_highest_bidder_id = listing.highest_bidder_id
        listing_seller_id = listing.seller_id
        listing_title = listing.title

    # ── Atomic conditional UPDATE ──────────────────────────────────────────
    async with AsyncSessionLocal() as session:
        async with session.begin():
            result = await session.execute(
                text(
                    """
                    UPDATE listings
                    SET highest_bid = :amt, highest_bidder_id = :uid
                    WHERE listing_id = :lid
                      AND status = 'active'
                      AND (highest_bid IS NULL OR highest_bid < :amt)
                    RETURNING listing_id, auction_end_at, auction_extension_count
                    """
                ),
                {"amt": amount, "uid": user_id, "lid": listing_id},
            )
            updated = result.fetchone()

            # Always insert bid row for audit trail regardless
            session.add(
                Bid(
                    listing_id=listing_id,
                    bidder_id=user_id,
                    amount=amount,
                )
            )

            if not updated:
                # Another bid beat us atomically
                await interaction.followup.send(
                    embed=build_error_embed(
                        "Your bid was outpaced by a concurrent higher bid. Please try again with a higher amount."
                    ),
                    ephemeral=True,
                )
                return False

            listing_id_ret, auction_end_at, extension_count = updated

            # ── Anti-snipe extension ──────────────────────────────────────
            if auction_end_at:
                seconds_left = (auction_end_at - now_utc).total_seconds()
                if (
                    seconds_left <= ANTI_SNIPE_WINDOW_SECONDS
                    and extension_count < MAX_AUCTION_EXTENSIONS
                ):
                    new_end = auction_end_at + timedelta(seconds=ANTI_SNIPE_EXTENSION_SECONDS)
                    await session.execute(
                        text(
                            """
                            UPDATE listings
                            SET auction_end_at = :new_end,
                                auction_extension_count = auction_extension_count + 1
                            WHERE listing_id = :lid
                            """
                        ),
                        {"new_end": new_end, "lid": listing_id},
                    )
                    logger.info(
                        "Anti-snipe extension applied to listing %d (extension #%d)",
                        listing_id,
                        extension_count + 1,
                    )

    await interaction.followup.send(
        embed=build_success_embed(
            f"✅ Bid of **{amount:.2f} {listing.currency_label}** placed on listing #{listing_id}!"
        ),
        ephemeral=True,
    )

    # ── Post-commit notifications ──────────────────────────────────────────
    # These run after the transaction commits — never inside session.begin().
    bot = interaction.client

    # 1. Notify the seller a new bid arrived
    try:
        seller_user = bot.get_user(listing_seller_id) or await bot.fetch_user(listing_seller_id)
        await seller_user.send(
            embed=discord.Embed(
                description=(
                    f"📈 New bid on **{listing_title}** (#{listing_id}): "
                    f"**{amount:.2f} {listing.currency_label}** "
                    f"from **{interaction.user.display_name}**."
                ),
                color=discord.Color.green(),
            )
        )
    except (discord.Forbidden, discord.HTTPException, discord.NotFound) as exc:
        logger.debug("Could not DM seller %d on new bid: %s", listing_seller_id, exc)

    # 2. Notify the previous highest bidder they've been outbid
    if previous_highest_bidder_id is not None and previous_highest_bidder_id != user_id:
        try:
            prev_user = (
                bot.get_user(previous_highest_bidder_id)
                or await bot.fetch_user(previous_highest_bidder_id)
            )
            await prev_user.send(
                embed=discord.Embed(
                    description=(
                        f"📉 You've been outbid on **{listing_title}** (#{listing_id})! "
                        f"New highest bid: **{amount:.2f} {listing.currency_label}**."
                    ),
                    color=discord.Color.orange(),
                )
            )
        except (discord.Forbidden, discord.HTTPException, discord.NotFound) as exc:
            logger.debug("Could not DM outbid user %d: %s", previous_highest_bidder_id, exc)

    return True


class Bidding(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    await bot.add_cog(Bidding(bot))
