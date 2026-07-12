"""
Create-listing wizard, listing embed builder, filter/search UI, and IGN modal.
Wizard state is held entirely in memory on the View — nothing written to DB until final confirm.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select, func, and_, or_

import utils.cache as guild_cache
from config import (
    BOT_NAME,
    GLOBAL_CATEGORIES,
    PRESET_MC_SERVERS,
    LISTING_CREATE_COOLDOWN_SECONDS,
    MAX_ACTIVE_LISTINGS_PER_USER,
    LISTING_DESCRIPTION_MAX_LEN,
    LISTINGS_QUERY_HARD_LIMIT,
    DUPLICATE_LISTING_WINDOW_SECONDS,
    GLOBAL_BROADCAST_DELAY,
    LISTING_EXPIRY_DAYS,
)
from database.engine import AsyncSessionLocal
from database.models import GuildConfig, Listing, UserProfile
from utils.checks import check_marketplace_access, get_or_create_profile
from utils.embeds import build_listing_embed, build_error_embed, build_success_embed, add_invite_branding
from utils.pagination import PaginatorView
from utils.parsing import parse_amount

logger = logging.getLogger(__name__)


# ─── IGN Modal ───────────────────────────────────────────────────────────────

class IGNModal(discord.ui.Modal, title="Set Your In-Game Name"):
    ign_input = discord.ui.TextInput(
        label="Your Minecraft IGN",
        placeholder="e.g. Steve123",
        max_length=32,
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        new_ign = self.ign_input.value.strip() or None
        async with AsyncSessionLocal() as session:
            async with session.begin():
                profile = await get_or_create_profile(session, interaction.user.id)
                profile.ign = new_ign
        label = f"**{new_ign}** *(self-reported)*" if new_ign else "*(cleared)*"
        await interaction.followup.send(
            embed=build_success_embed(f"IGN updated: {label}"),
            ephemeral=True,
        )


# ─── Listing detail Modal ─────────────────────────────────────────────────────

class ListingDetailsModal(discord.ui.Modal, title="Listing Details"):
    title_input = discord.ui.TextInput(
        label="Title",
        placeholder="e.g. Selling 64 diamonds",
        max_length=100,
    )
    description_input = discord.ui.TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,
        placeholder="Describe your listing…",
        max_length=LISTING_DESCRIPTION_MAX_LEN,
    )
    price_input = discord.ui.TextInput(
        label="Price / Starting Bid",
        placeholder="e.g. 500, 10k, 2.5m, 1b",
        max_length=20,
        required=False,
    )
    currency_input = discord.ui.TextInput(
        label="Currency label",
        placeholder="e.g. diamonds, in-game currency, coins",
        max_length=30,
        default="in-game currency",
        required=False,
    )

    def __init__(self, wizard: "ListingWizardState"):
        super().__init__()
        self._wizard = wizard

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        price_raw = self.price_input.value.strip()
        price: float | None = None
        if price_raw:
            try:
                price = parse_amount(price_raw)
            except ValueError:
                await interaction.followup.send(
                    embed=build_error_embed(
                        "Price must be a number, optionally with a k/m/b suffix (e.g. 500, 10k, 2.5m, 1b)."
                    ),
                    ephemeral=True,
                )
                return

        self._wizard.title = self.title_input.value.strip()
        self._wizard.description = self.description_input.value.strip()
        self._wizard.price = price
        self._wizard.currency_label = self.currency_input.value.strip() or "in-game currency"

        # Show confirmation embed
        preview = _build_preview_embed(self._wizard)
        view = ConfirmListingView(self._wizard)
        await interaction.followup.send(
            content="**Preview — confirm to post:**",
            embed=preview,
            view=view,
            ephemeral=True,
        )


# ─── Wizard state (in-memory only until confirmed) ────────────────────────────

class ListingWizardState:
    __slots__ = (
        "scope", "category", "listing_type", "format",
        "mc_server_tag", "title", "description",
        "price", "currency_label",
        "guild_id", "seller_id",
        "auction_duration_hours",
    )

    def __init__(self, guild_id: int, seller_id: int):
        self.guild_id = guild_id
        self.seller_id = seller_id
        self.scope: str | None = None
        self.category: str | None = None
        self.listing_type: str | None = None
        self.format: str | None = None
        self.mc_server_tag: str | None = None
        self.title: str | None = None
        self.description: str | None = None
        self.price: float | None = None
        self.currency_label: str = "in-game currency"
        self.auction_duration_hours: int = 24


def _build_preview_embed(wizard: ListingWizardState) -> discord.Embed:
    embed = discord.Embed(
        title=f"📋 Preview: {wizard.title}",
        description=wizard.description or "*(no description)*",
        color=0x5865F2,
    )
    embed.add_field(name="Scope", value=wizard.scope or "?", inline=True)
    embed.add_field(name="Type", value=wizard.listing_type or "?", inline=True)
    embed.add_field(name="Format", value=wizard.format or "?", inline=True)
    embed.add_field(name="Category", value=wizard.category or "?", inline=True)
    if wizard.mc_server_tag:
        embed.add_field(name="MC Server", value=wizard.mc_server_tag, inline=True)
    price_str = f"{wizard.price} {wizard.currency_label}" if wizard.price else "*(not set)*"
    embed.add_field(name="Price", value=price_str, inline=True)
    return embed


# ─── Scope select ─────────────────────────────────────────────────────────────

class ScopeSelectView(discord.ui.View):
    def __init__(self, wizard: ListingWizardState, config: GuildConfig):
        super().__init__(timeout=300)
        self._wizard = wizard
        options = []
        if config.allow_server_listings:
            options.append(discord.SelectOption(label="🏠 Server-scoped", value="server", description="Visible only in this server"))
        if config.allow_global_listings:
            options.append(discord.SelectOption(label="🌐 Global", value="global", description="Broadcast to all servers with Snag"))
        if not options:
            options.append(discord.SelectOption(label="(disabled)", value="_none"))
        self.scope_select.options = options

    @discord.ui.select(placeholder="Choose listing scope…", custom_id="wizard:scope", min_values=1, max_values=1)
    async def scope_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer(ephemeral=True)
        if select.values[0] == "_none":
            await interaction.followup.send(embed=build_error_embed("All listing types are disabled on this server."), ephemeral=True)
            return
        self._wizard.scope = select.values[0]
        # Move to category select
        cats = GLOBAL_CATEGORIES if self._wizard.scope == "global" else (
            _get_server_cats(self._wizard.guild_id) or GLOBAL_CATEGORIES
        )
        view = CategorySelectView(self._wizard, cats)
        await interaction.followup.send(
            embed=discord.Embed(title="Step 2: Choose a category", color=0x5865F2),
            view=view,
            ephemeral=True,
        )


def _get_server_cats(guild_id: int) -> list[str]:
    config = guild_cache.get(guild_id)
    return config.custom_categories if config else []


# ─── Category select ──────────────────────────────────────────────────────────

class CategorySelectView(discord.ui.View):
    def __init__(self, wizard: ListingWizardState, categories: list[str]):
        super().__init__(timeout=300)
        self._wizard = wizard
        opts = [discord.SelectOption(label=c, value=c) for c in (categories or GLOBAL_CATEGORIES)]
        self.cat_select.options = opts[:25]

    @discord.ui.select(placeholder="Choose category…", custom_id="wizard:category")
    async def cat_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer(ephemeral=True)
        self._wizard.category = select.values[0]
        # MC server tag
        view = MCServerSelectView(self._wizard)
        await interaction.followup.send(
            embed=discord.Embed(title="Step 3: Which MC server?", color=0x5865F2),
            view=view,
            ephemeral=True,
        )


# ─── MC server tag select ─────────────────────────────────────────────────────

class MCServerSelectView(discord.ui.View):
    def __init__(self, wizard: ListingWizardState):
        super().__init__(timeout=300)
        self._wizard = wizard
        opts = [discord.SelectOption(label=s, value=s) for s in PRESET_MC_SERVERS]
        self.mc_select.options = opts

    @discord.ui.select(placeholder="Which Minecraft server?", custom_id="wizard:mc_server")
    async def mc_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer(ephemeral=True)
        val = select.values[0]
        self._wizard.mc_server_tag = None if val == "Any" else val
        view = TypeSelectView(self._wizard)
        await interaction.followup.send(
            embed=discord.Embed(title="Step 4: Buying or Selling?", color=0x5865F2),
            view=view,
            ephemeral=True,
        )


# ─── Listing type select ──────────────────────────────────────────────────────

class TypeSelectView(discord.ui.View):
    def __init__(self, wizard: ListingWizardState):
        super().__init__(timeout=300)
        self._wizard = wizard

    @discord.ui.select(
        placeholder="Buying or Selling?",
        custom_id="wizard:listing_type",
        options=[
            discord.SelectOption(label="💰 Selling", value="selling"),
            discord.SelectOption(label="🛒 Buying", value="buying"),
        ],
    )
    async def type_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer(ephemeral=True)
        self._wizard.listing_type = select.values[0]
        view = FormatSelectView(self._wizard)
        await interaction.followup.send(
            embed=discord.Embed(title="Step 5: Direct Sale or Auction?", color=0x5865F2),
            view=view,
            ephemeral=True,
        )


# ─── Format select ────────────────────────────────────────────────────────────

class FormatSelectView(discord.ui.View):
    def __init__(self, wizard: ListingWizardState):
        super().__init__(timeout=300)
        self._wizard = wizard

    @discord.ui.select(
        placeholder="Direct Sale or Auction?",
        custom_id="wizard:format",
        options=[
            discord.SelectOption(label="🤝 Direct Sale", value="direct_sale"),
            discord.SelectOption(label="🔨 Auction / Bidding", value="auction"),
        ],
    )
    async def format_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self._wizard.format = select.values[0]
        await interaction.response.send_modal(ListingDetailsModal(self._wizard))


# ─── Confirm view ─────────────────────────────────────────────────────────────

class ConfirmListingView(discord.ui.View):
    def __init__(self, wizard: ListingWizardState):
        super().__init__(timeout=300)
        self._wizard = wizard

    @discord.ui.button(label="✅ Confirm & Post", style=discord.ButtonStyle.success, custom_id="wizard:confirm")
    async def confirm(self, interaction: discord.Interaction, _):
        await interaction.response.defer(ephemeral=True)
        await _finalize_listing(interaction, self._wizard)
        self.stop()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger, custom_id="wizard:cancel")
    async def cancel(self, interaction: discord.Interaction, _):
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(embed=build_error_embed("Listing cancelled."), ephemeral=True)
        self.stop()


async def _finalize_listing(interaction: discord.Interaction, wizard: ListingWizardState):
    """Write the listing row to DB and broadcast to guilds."""
    user_id = interaction.user.id
    guild_id = interaction.guild_id

    async with AsyncSessionLocal() as session:
        async with session.begin():
            # Check active listing cap
            count_result = await session.execute(
                select(func.count()).where(
                    Listing.seller_id == user_id,
                    Listing.status == "active",
                )
            )
            active_count = count_result.scalar_one()
            if active_count >= MAX_ACTIVE_LISTINGS_PER_USER:
                await interaction.followup.send(
                    embed=build_error_embed(
                        f"You've hit your active listing limit ({MAX_ACTIVE_LISTINGS_PER_USER}). "
                        "Cancel or complete one first."
                    ),
                    ephemeral=True,
                )
                return

            # Duplicate submission detection
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=DUPLICATE_LISTING_WINDOW_SECONDS)
            dup_result = await session.execute(
                select(Listing).where(
                    Listing.seller_id == user_id,
                    Listing.title == wizard.title,
                    Listing.price == wizard.price,
                    Listing.category == wizard.category,
                    Listing.status == "active",
                    Listing.created_at > cutoff,
                )
            )
            if dup_result.scalar_one_or_none():
                await interaction.followup.send(
                    embed=build_error_embed(
                        "A nearly identical listing was created in the last 10 minutes. "
                        "Please wait before reposting."
                    ),
                    ephemeral=True,
                )
                return

            # Ensure profile exists
            profile = await get_or_create_profile(session, user_id)

            auction_end = None
            if wizard.format == "auction":
                auction_end = datetime.now(timezone.utc) + timedelta(hours=wizard.auction_duration_hours)

            listing = Listing(
                seller_id=user_id,
                origin_guild_id=guild_id,
                scope=wizard.scope,
                mc_server_tag=wizard.mc_server_tag,
                category=wizard.category,
                listing_type=wizard.listing_type,
                format=wizard.format,
                title=wizard.title,
                description=wizard.description,
                price=wizard.price,
                currency_label=wizard.currency_label,
                status="active",
                auction_end_at=auction_end,
            )
            session.add(listing)
            await session.flush()
            listing_id = listing.listing_id

    # Post embed to guild(s)
    bot = interaction.client
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Listing, UserProfile)
            .join(UserProfile, UserProfile.user_id == Listing.seller_id)
            .where(Listing.listing_id == listing_id)
        )
        row = result.first()

    if not row:
        await interaction.followup.send(embed=build_error_embed("Failed to retrieve listing."), ephemeral=True)
        return

    db_listing, db_profile = row

    if wizard.scope == "global":
        await _broadcast_global_listing(bot, db_listing, db_profile, guild_id)
    else:
        await _post_listing_to_guild(bot, guild_id, db_listing, db_profile)

    await interaction.followup.send(
        embed=build_success_embed(
            f"✅ Listing **{wizard.title}** posted! (ID: #{listing_id})"
        ),
        ephemeral=True,
    )


async def _post_listing_to_guild(bot, guild_id: int, listing: Listing, profile: UserProfile):
    config = guild_cache.get(guild_id)
    if config is None:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(GuildConfig).where(GuildConfig.guild_id == guild_id))
            config = result.scalar_one_or_none()
            if config:
                guild_cache.set(config)

    if not config or not config.panel_channel_id:
        return

    channel_id = config.global_feed_channel_id or config.panel_channel_id
    channel = bot.get_channel(channel_id)
    if not channel:
        return

    color = int(config.embed_color.lstrip("#"), 16)
    embed = build_listing_embed(listing, profile, guild_color=color)

    from cogs.deals import ListingActionView
    view = ListingActionView(listing.listing_id, listing.format)

    try:
        await channel.send(embed=embed, view=view)
    except (discord.Forbidden, discord.HTTPException) as exc:
        logger.warning("Failed to post listing to guild %d channel %d: %s", guild_id, channel_id, exc)


async def _broadcast_global_listing(bot, listing: Listing, profile: UserProfile, origin_guild_id: int):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(GuildConfig).where(GuildConfig.allow_global_listings == True)
        )
        configs = result.scalars().all()

    for config in configs:
        if config.guild_id == origin_guild_id:
            await _post_listing_to_guild(bot, config.guild_id, listing, profile)
        else:
            await _post_listing_to_guild(bot, config.guild_id, listing, profile)
        await asyncio.sleep(GLOBAL_BROADCAST_DELAY)


# ─── Check listings / filter flow ────────────────────────────────────────────

class SearchModal(discord.ui.Modal, title="Search Listings"):
    query_input = discord.ui.TextInput(
        label="Search term",
        placeholder="e.g. diamonds, base, service…",
        max_length=100,
    )

    def __init__(self, filters: dict):
        super().__init__()
        self._filters = filters

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        self._filters["search"] = self.query_input.value.strip()
        await _run_listing_query(interaction, self._filters)


class FilterView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self._filters: dict = {}

        mc_opts = [discord.SelectOption(label=s, value=s) for s in PRESET_MC_SERVERS]
        self.mc_filter.options = mc_opts

        cat_opts = [discord.SelectOption(label="Any", value="_any")] + [
            discord.SelectOption(label=c, value=c) for c in GLOBAL_CATEGORIES
        ]
        self.cat_filter.options = cat_opts

    @discord.ui.select(placeholder="MC Server filter…", custom_id="filter:mc_server", row=0)
    async def mc_filter(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer(ephemeral=True)
        val = select.values[0]
        self._filters["mc_server_tag"] = None if val == "Any" else val
        await interaction.followup.send(f"MC Server filter: **{val}**", ephemeral=True)

    @discord.ui.select(placeholder="Category filter…", custom_id="filter:category", row=1)
    async def cat_filter(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer(ephemeral=True)
        val = select.values[0]
        self._filters["category"] = None if val == "_any" else val
        await interaction.followup.send(f"Category filter: **{val}**", ephemeral=True)

    @discord.ui.select(
        placeholder="Buying or Selling?",
        custom_id="filter:type",
        row=2,
        options=[
            discord.SelectOption(label="Any", value="_any"),
            discord.SelectOption(label="Buying", value="buying"),
            discord.SelectOption(label="Selling", value="selling"),
        ],
    )
    async def type_filter(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer(ephemeral=True)
        val = select.values[0]
        self._filters["listing_type"] = None if val == "_any" else val
        await interaction.followup.send(f"Type filter: **{val}**", ephemeral=True)

    @discord.ui.select(
        placeholder="Direct Sale or Auction?",
        custom_id="filter:format",
        row=3,
        options=[
            discord.SelectOption(label="Any", value="_any"),
            discord.SelectOption(label="Direct Sale", value="direct_sale"),
            discord.SelectOption(label="Auction", value="auction"),
        ],
    )
    async def format_filter(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer(ephemeral=True)
        val = select.values[0]
        self._filters["format"] = None if val == "_any" else val
        await interaction.followup.send(f"Format filter: **{val}**", ephemeral=True)

    @discord.ui.button(label="🔍 Search", style=discord.ButtonStyle.primary, custom_id="filter:search", row=4)
    async def search_btn(self, interaction: discord.Interaction, _):
        await interaction.response.send_modal(SearchModal(self._filters))

    @discord.ui.button(label="📋 Browse All", style=discord.ButtonStyle.secondary, custom_id="filter:browse_all", row=4)
    async def browse_all(self, interaction: discord.Interaction, _):
        await interaction.response.defer(ephemeral=True)
        await _run_listing_query(interaction, self._filters)


async def _run_listing_query(interaction: discord.Interaction, filters: dict):
    conditions = [Listing.status == "active"]

    mc = filters.get("mc_server_tag")
    if mc:
        conditions.append(Listing.mc_server_tag == mc)

    cat = filters.get("category")
    if cat:
        conditions.append(Listing.category == cat)

    lt = filters.get("listing_type")
    if lt:
        conditions.append(Listing.listing_type == lt)

    fmt = filters.get("format")
    if fmt:
        conditions.append(Listing.format == fmt)

    search = filters.get("search")

    async with AsyncSessionLocal() as session:
        q = select(Listing, UserProfile).join(
            UserProfile, UserProfile.user_id == Listing.seller_id
        ).where(and_(*conditions))

        if search:
            q = q.where(
                or_(
                    Listing.title.ilike(f"%{search}%"),
                    Listing.description.ilike(f"%{search}%"),
                )
            )

        q = q.limit(LISTINGS_QUERY_HARD_LIMIT)
        result = await session.execute(q)
        rows = result.all()

    if not rows:
        await interaction.followup.send(
            embed=discord.Embed(
                title="No listings found",
                description="Try adjusting your filters.",
                color=discord.Color.greyple(),
            ),
            ephemeral=True,
        )
        return

    embeds = [build_listing_embed(listing, profile) for listing, profile in rows]
    paginator = PaginatorView(embeds)
    await interaction.followup.send(
        embed=paginator.current_page_embed(),
        view=paginator,
        ephemeral=True,
    )


# ─── Entry-point helpers called from panel_views ──────────────────────────────

async def start_create_listing_wizard(interaction: discord.Interaction):
    """Gate checks + wizard start — called from panel Create Listing button."""
    # Global/guild ban check
    if not await check_marketplace_access(interaction):
        return

    guild_id = interaction.guild_id
    user_id = interaction.user.id

    # Active listing cap pre-check
    async with AsyncSessionLocal() as session:
        count_result = await session.execute(
            select(func.count()).where(
                Listing.seller_id == user_id,
                Listing.status == "active",
            )
        )
        active_count = count_result.scalar_one()

    if active_count >= MAX_ACTIVE_LISTINGS_PER_USER:
        await interaction.followup.send(
            embed=build_error_embed(
                f"You've hit your active listing limit ({MAX_ACTIVE_LISTINGS_PER_USER}). "
                "Cancel or complete one first."
            ),
            ephemeral=True,
        )
        return

    config = guild_cache.get(guild_id)
    if config is None:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(GuildConfig).where(GuildConfig.guild_id == guild_id)
            )
            config = result.scalar_one_or_none()
            if config:
                guild_cache.set(config)

    if config is None:
        await interaction.followup.send(
            embed=build_error_embed(
                "This server hasn't been configured yet. Ask an admin to run `/setup preferences`."
            ),
            ephemeral=True,
        )
        return

    wizard = ListingWizardState(guild_id=guild_id, seller_id=user_id)
    view = ScopeSelectView(wizard, config)
    await interaction.followup.send(
        embed=discord.Embed(title="Step 1: Choose listing scope", color=0x5865F2),
        view=view,
        ephemeral=True,
    )


async def start_check_listings(interaction: discord.Interaction):
    """Called from panel Check Listings button."""
    if not await check_marketplace_access(interaction):
        return
    view = FilterView()
    embed = discord.Embed(
        title="🔍 Browse Listings",
        description="Use the menus to filter, then click **Browse All** or **Search**.",
        color=0x5865F2,
    )
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


# ─── Slash commands: /listing edit, /listing cancel ──────────────────────────

class Listings(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    listing_group = app_commands.Group(name="listing", description="Manage your listings")

    @listing_group.command(name="edit", description="Edit one of your active listings")
    @app_commands.describe(listing_id="The listing ID to edit")
    async def listing_edit(self, interaction: discord.Interaction, listing_id: int):
        await interaction.response.defer(ephemeral=True)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Listing).where(Listing.listing_id == listing_id)
            )
            listing = result.scalar_one_or_none()

        if not listing:
            await interaction.followup.send(embed=build_error_embed("Listing not found."), ephemeral=True)
            return
        if listing.seller_id != interaction.user.id:
            await interaction.followup.send(embed=build_error_embed("You can only edit your own listings."), ephemeral=True)
            return
        if listing.status != "active":
            await interaction.followup.send(embed=build_error_embed("Only active listings can be edited."), ephemeral=True)
            return

        modal = EditListingModal(listing)
        await interaction.followup.send(
            embed=build_listing_embed(listing),
            view=_EditListingLaunchView(listing),
            ephemeral=True,
        )

    @listing_group.command(name="cancel", description="Cancel one of your active listings")
    @app_commands.describe(listing_id="The listing ID to cancel")
    async def listing_cancel(self, interaction: discord.Interaction, listing_id: int):
        await interaction.response.defer(ephemeral=True)
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(Listing).where(Listing.listing_id == listing_id)
                )
                listing = result.scalar_one_or_none()
                if not listing:
                    await interaction.followup.send(embed=build_error_embed("Listing not found."), ephemeral=True)
                    return
                if listing.seller_id != interaction.user.id:
                    await interaction.followup.send(embed=build_error_embed("You can only cancel your own listings."), ephemeral=True)
                    return
                if listing.status not in ("active",):
                    await interaction.followup.send(embed=build_error_embed("This listing can't be cancelled."), ephemeral=True)
                    return
                listing.status = "cancelled"

        await interaction.followup.send(
            embed=build_success_embed(f"Listing #{listing_id} cancelled."),
            ephemeral=True,
        )


class _EditListingLaunchView(discord.ui.View):
    def __init__(self, listing: Listing):
        super().__init__(timeout=60)
        self._listing = listing

    @discord.ui.button(label="✏️ Open Edit Form", style=discord.ButtonStyle.primary, custom_id="listing:open_edit")
    async def open_edit(self, interaction: discord.Interaction, _):
        await interaction.response.send_modal(EditListingModal(self._listing))


class EditListingModal(discord.ui.Modal, title="Edit Listing"):
    def __init__(self, listing: Listing):
        super().__init__()
        self._listing_id = listing.listing_id
        self.title_input = discord.ui.TextInput(
            label="Title", default=listing.title, max_length=100
        )
        self.desc_input = discord.ui.TextInput(
            label="Description",
            default=listing.description,
            style=discord.TextStyle.paragraph,
            max_length=LISTING_DESCRIPTION_MAX_LEN,
        )
        self.price_input = discord.ui.TextInput(
            label="Price",
            placeholder="e.g. 500, 10k, 2.5m, 1b",
            default=str(listing.price) if listing.price else "",
            max_length=20,
            required=False,
        )
        self.add_item(self.title_input)
        self.add_item(self.desc_input)
        self.add_item(self.price_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        price = None
        if self.price_input.value.strip():
            try:
                price = parse_amount(self.price_input.value.strip())
            except ValueError:
                await interaction.followup.send(
                    embed=build_error_embed(
                        "Invalid price. Use a number, optionally with a k/m/b suffix (e.g. 500, 10k, 2.5m, 1b)."
                    ),
                    ephemeral=True,
                )
                return
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(Listing).where(
                        Listing.listing_id == self._listing_id,
                        Listing.seller_id == interaction.user.id,
                    )
                )
                listing = result.scalar_one_or_none()
                if not listing:
                    await interaction.followup.send(embed=build_error_embed("Listing not found or access denied."), ephemeral=True)
                    return
                listing.title = self.title_input.value.strip()
                listing.description = self.desc_input.value.strip()
                listing.price = price

        await interaction.followup.send(
            embed=build_success_embed(f"Listing #{self._listing_id} updated."),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Listings(bot))
