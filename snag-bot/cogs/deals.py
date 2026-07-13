"""
Deal creation, persistent DM deal-panel, and dual-confirm completion.

Priority 1: on_message listener relays text/attachments between both parties
            for the duration of an active deal.
Priority 2: _close_deal_panels() edits both stored DM panel messages to a
            closed state on every termination path (cancel / complete / expire).
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
from utils.base_view import SnagView
from utils.checks import is_globally_banned, is_guild_banned, get_or_create_profile
from utils.embeds import build_deal_panel_embed, build_error_embed, build_success_embed

logger = logging.getLogger(__name__)


# ─── Listing action view (Start Deal / Place Bid buttons on listing embeds) ───

class ListingActionView(SnagView):
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
        # Resolve listing_id and action from custom_id BEFORE deciding how to respond.
        # Modals MUST be opened via interaction.response.send_modal() — you cannot
        # open a modal after calling response.defer().  So we check the action first.
        parts = button.custom_id.split(":")
        listing_id = int(parts[2])
        action = parts[1]

        if action == "place_bid":
            # For auctions: open the bid modal directly (no defer allowed before send_modal)
            await _open_bid_modal(interaction, listing_id)
        else:
            # For direct sales: defer then create the deal
            await interaction.response.defer(ephemeral=True)
            await _create_deal(interaction, listing_id)


async def _open_bid_modal(interaction: discord.Interaction, listing_id: int):
    """Fetch listing data and open the bid modal directly (no prior defer)."""
    from cogs.bidding import PlaceBidModal
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Listing).where(Listing.listing_id == listing_id))
        listing = result.scalar_one_or_none()
    if not listing:
        await interaction.response.send_message(
            embed=build_error_embed("Listing not found."), ephemeral=True
        )
        return
    if listing.status != "active" or listing.format != "auction":
        await interaction.response.send_message(
            embed=build_error_embed("This auction is no longer accepting bids."), ephemeral=True
        )
        return
    modal = PlaceBidModal(
        listing_id=listing_id,
        current_bid=float(listing.highest_bid) if listing.highest_bid else None,
        currency=listing.currency_label,
        guild_id=interaction.guild_id,
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

    # Re-fetch listing and both profiles in a single session for embed building
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Listing).where(Listing.listing_id == listing_id))
        db_listing = result.scalar_one()
        initiator_profile = await get_or_create_profile(session, user_id)
        seller_profile = await get_or_create_profile(session, seller_id)

    view = DealPanelView(deal_id=deal_id)

    # Get guild color
    config = guild_cache.get(interaction.guild_id)
    try:
        color = int((config.embed_color or "#5865F2").lstrip("#"), 16) if config else DEFAULT_EMBED_COLOR
    except (ValueError, AttributeError):
        color = DEFAULT_EMBED_COLOR

    fake_deal = type("D", (), {"deal_id": deal_id, "status": "active"})()

    # Personalized embed for each recipient
    initiator_embed = build_deal_panel_embed(
        fake_deal, db_listing,
        viewer_role="🛒 Buyer",
        counterpart_user=seller_user,
        counterpart_profile=seller_profile,
        color=color,
    )
    seller_embed = build_deal_panel_embed(
        fake_deal, db_listing,
        viewer_role="💰 Seller",
        counterpart_user=initiator_user,
        counterpart_profile=initiator_profile,
        color=color,
    )

    initiator_msg_id = None
    seller_msg_id = None

    try:
        msg_i = await initiator_user.send(embed=initiator_embed, view=view)
        initiator_msg_id = msg_i.id
    except discord.Forbidden:
        try:
            await seller_user.send(
                embed=build_error_embed(
                    f"Deal #{deal_id} was started but I couldn't DM the buyer — their DMs are closed."
                )
            )
        except discord.Forbidden:
            pass

    try:
        msg_s = await seller_user.send(embed=seller_embed, view=view)
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


# ─── Shared close-panels helper ───────────────────────────────────────────────

async def _close_deal_panels(bot, deal, closing_embed: discord.Embed) -> None:
    """
    Edit both parties' stored DM panel messages to a closed state, removing
    the view so the buttons are gone.  Called from every deal-termination path
    (cancel, complete, expire).  Never raises — each edit is individually guarded.
    If dm_message_id is None (DM originally failed) or fetch raises, we skip
    that side and log at debug level.
    """
    for uid, msg_id in [
        (deal.initiator_id, deal.dm_message_id_initiator),
        (deal.seller_id, deal.dm_message_id_seller),
    ]:
        if msg_id is None:
            continue
        try:
            user = bot.get_user(uid) or await bot.fetch_user(uid)
            dm = user.dm_channel or await user.create_dm()
            msg = await dm.fetch_message(msg_id)
            await msg.edit(embed=closing_embed, view=None)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            logger.debug(
                "_close_deal_panels: could not edit panel for user %d (msg %d): %s",
                uid, msg_id, exc,
            )
        except Exception as exc:
            logger.debug(
                "_close_deal_panels: unexpected error for user %d: %s", uid, exc
            )


# ─── Deal panel persistent view ───────────────────────────────────────────────

class DealPanelView(SnagView):
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
        parts = interaction.data["custom_id"].split(":")
        self._deal_id = int(parts[2])
        deal = await self._verify_party(interaction)
        if not deal:
            return
        if deal.status != "active":
            await interaction.followup.send(embed=build_error_embed("This deal is no longer active."), ephemeral=True)
            return

        # Capture DM message IDs before closing the session
        initiator_dm_id = deal.dm_message_id_initiator
        seller_dm_id = deal.dm_message_id_seller
        initiator_id_val = deal.initiator_id
        seller_id_val = deal.seller_id

        both_confirmed = False
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
                    listing_id = deal_db.listing_id

                    rl = await session.execute(select(Listing).where(Listing.listing_id == listing_id))
                    listing = rl.scalar_one_or_none()
                    if listing:
                        listing.status = "completed"

                    for uid in (initiator_id_val, seller_id_val):
                        profile = await get_or_create_profile(session, uid)
                        profile.pending_review_deal_id = deal_id
                        profile.completed_deals += 1

        bot = interaction.client

        if both_confirmed:
            await _send_review_prompts(bot, deal_id, initiator_id_val, seller_id_val)
            await interaction.followup.send(
                embed=build_success_embed("🎉 Deal completed! Both parties have confirmed. Check your DMs for a review prompt."),
                ephemeral=True,
            )
            # Close both deal panel messages
            closing_embed = discord.Embed(
                title="✅ Deal Completed",
                description=(
                    f"🎉 Deal #{deal_id} is done — both parties confirmed.\n"
                    "Check your DMs for the review prompt."
                ),
                color=discord.Color.green(),
            )
            # Build a minimal object with the DM IDs for _close_deal_panels
            _panel_ref = type("_D", (), {
                "initiator_id": initiator_id_val,
                "seller_id": seller_id_val,
                "dm_message_id_initiator": initiator_dm_id,
                "dm_message_id_seller": seller_dm_id,
            })()
            await _close_deal_panels(bot, _panel_ref, closing_embed)
        else:
            # Partial confirm — nudge the other party
            other_id = seller_id_val if interaction.user.id == initiator_id_val else initiator_id_val
            other_label = "The seller" if interaction.user.id == initiator_id_val else "The buyer"
            await interaction.followup.send(
                embed=build_success_embed(f"Your confirmation recorded. Waiting for {other_label} to confirm."),
                ephemeral=True,
            )
            try:
                other_user = bot.get_user(other_id) or await bot.fetch_user(other_id)
                await other_user.send(
                    embed=discord.Embed(
                        description=(
                            f"✅ **{interaction.user.display_name}** marked Deal #{self._deal_id} "
                            f"as complete on their end! "
                            f"Click **✅ Mark Complete** on your side too to finish the trade."
                        ),
                        color=discord.Color.green(),
                    )
                )
            except (discord.Forbidden, discord.HTTPException, discord.NotFound) as exc:
                logger.debug("Could not nudge other party %d on partial confirm: %s", other_id, exc)

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
        other_id = deal.seller_id if is_initiator else deal.initiator_id
        clicker_name = interaction.user.display_name

        async with AsyncSessionLocal() as session:
            async with session.begin():
                r = await session.execute(select(Deal).where(Deal.deal_id == self._deal_id))
                deal_db = r.scalar_one()
                deal_db.status = "cancelled"
                deal_db.ended_at = datetime.now(timezone.utc)
                deal_db.end_reason = end_reason
                listing_id = deal_db.listing_id

                rl = await session.execute(select(Listing).where(Listing.listing_id == listing_id))
                listing = rl.scalar_one_or_none()
                listing_title = listing.title if listing else "Unknown"
                if listing and not (
                    listing.format == "auction"
                    and listing.auction_end_at
                    and listing.auction_end_at <= datetime.now(timezone.utc)
                ):
                    listing.status = "active"

        # Confirm to clicker
        await interaction.followup.send(
            embed=build_success_embed("Deal cancelled. The listing has been re-opened."),
            ephemeral=True,
        )

        bot = interaction.client

        # DM the other party
        try:
            other_user = bot.get_user(other_id) or await bot.fetch_user(other_id)
            await other_user.send(
                embed=discord.Embed(
                    description=(
                        f"❌ **{clicker_name}** cancelled Deal #{self._deal_id} for "
                        f"**{listing_title}**. "
                        "The listing has been reopened — no action needed from you."
                    ),
                    color=discord.Color.red(),
                )
            )
        except (discord.Forbidden, discord.HTTPException, discord.NotFound) as exc:
            logger.debug("Could not DM other party %d on deal cancel: %s", other_id, exc)

        # Close both panel messages
        closing_embed = discord.Embed(
            title="🔒 Deal Cancelled",
            description=(
                f"❌ **{clicker_name}** cancelled Deal #{self._deal_id} for **{listing_title}**.\n"
                "This deal panel is now closed."
            ),
            color=discord.Color.red(),
        )
        await _close_deal_panels(bot, deal, closing_embed)

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


class ReviewPromptView(SnagView):
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


# ─── Deals cog (on_message relay + cog registration) ─────────────────────────

class Deals(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """
        Relay DMs between both parties of an active deal for the duration it's active.

        Discord's DM exemption (Intents.default() — no privileged intent needed):
        The Message Content privileged intent is exempt for DMs the bot *receives*
        — confirmed current Discord policy for "DMs that it receives", so
        message.content is always available here without any extra intent.

        Design decisions (per spec):
        - Silent if no active deal found (no nagging pointer message).
        - Relay everything — bot is slash-only, no prefix collision risk.
        - Bump last_activity_at on every successful relay so the 48h timeout
          counts real conversation, not just button clicks.
        - Wrap relay send in try/except; DM the original sender on Forbidden.
        """
        if message.author.bot:
            return
        if not isinstance(message.channel, discord.DMChannel):
            return

        user_id = message.author.id

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Deal).where(
                    Deal.status == "active",
                    (Deal.initiator_id == user_id) | (Deal.seller_id == user_id),
                )
            )
            deal = result.scalar_one_or_none()

        if not deal:
            # No active deal — stay silent
            return

        other_id = deal.seller_id if user_id == deal.initiator_id else deal.initiator_id

        # Build relay text
        relay_text = f"💬 **{message.author.display_name}:** {message.content}"
        if message.attachments:
            urls = "\n".join(a.url for a in message.attachments)
            relay_text += f"\n{urls}"

        try:
            other_user = self.bot.get_user(other_id) or await self.bot.fetch_user(other_id)
            await other_user.send(relay_text)

            # Bump last_activity_at — chatting counts as activity, not just button clicks
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    r = await session.execute(
                        select(Deal).where(Deal.deal_id == deal.deal_id)
                    )
                    d = r.scalar_one_or_none()
                    if d and d.status == "active":
                        d.last_activity_at = datetime.now(timezone.utc)

        except (discord.Forbidden, discord.HTTPException):
            try:
                await message.channel.send(
                    "⚠️ Couldn't deliver that — the other party's DMs may be closed. "
                    "Try **Report Issue** if this keeps happening."
                )
            except Exception:
                pass
        except Exception as exc:
            logger.error("on_message relay failed for deal %d: %s", deal.deal_id, exc)


async def setup(bot: commands.Bot):
    await bot.add_cog(Deals(bot))
