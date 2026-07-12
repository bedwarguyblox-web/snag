"""
Snag — Discord trading marketplace bot entry point.
Boots the bot, loads all cogs, registers persistent Views, starts background tasks.
"""

import asyncio
import logging
import os
import sys

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("snag")

# ─── Intents ──────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.guilds = True
intents.guild_messages = True
intents.members = True          # Privileged — enable in Developer Portal
# message_content only needed for prefix commands (we use slash only, so skip it)

# ─── Bot ──────────────────────────────────────────────────────────────────────
bot = commands.Bot(
    command_prefix="!snag ",    # Fallback prefix (unused in practice — all slash)
    intents=intents,
    help_command=None,
)

COGS = [
    "cogs.admin_setup",
    "cogs.panel_views",
    "cogs.listings",
    "cogs.deals",
    "cogs.bidding",
    "cogs.reviews",
    "cogs.moderation",
]


async def setup_hook() -> None:
    """
    Called once before on_ready.  Load cogs, register persistent Views, start tasks.
    Persistent Views MUST be registered here (not in on_ready) — on_ready can fire
    multiple times across reconnects and duplicate registration must be idempotent.
    """
    # ── Create DB tables ──────────────────────────────────────────────────
    from database.engine import create_all_tables
    await create_all_tables()

    # ── Load cogs ─────────────────────────────────────────────────────────
    for cog in COGS:
        try:
            await bot.load_extension(cog)
            logger.info("Loaded cog: %s", cog)
        except Exception as exc:
            logger.error("Failed to load cog %s: %s", cog, exc)

    # ── Register persistent Views ─────────────────────────────────────────
    # These must have timeout=None and fixed custom_ids set in their __init__.
    from cogs.panel_views import MainPanelView
    bot.add_view(MainPanelView())

    # DealPanelView and ReviewPromptView are registered dynamically — they encode
    # deal_id in custom_id so they need individual instances per deal.
    # On restart, unresolved deals resume when a user clicks a button,
    # which re-triggers the callback with the encoded deal_id.
    # To fully restore after restart, fetch active deals and re-register:
    await _re_register_active_deal_views()

    # Listing action views (per listing)
    await _re_register_listing_views()

    # Renewal views
    await _re_register_renewal_views()

    # ── Start background tasks ────────────────────────────────────────────
    from background_tasks import register_tasks
    register_tasks(bot)

    # ── Sync slash commands ───────────────────────────────────────────────
    try:
        synced = await bot.tree.sync()
        logger.info("Synced %d application command(s).", len(synced))
    except Exception as exc:
        logger.error("Failed to sync commands: %s", exc)


async def _re_register_active_deal_views() -> None:
    """Re-register DealPanelView for every active deal so buttons work after restart."""
    try:
        from database.engine import AsyncSessionLocal
        from database.models import Deal
        from cogs.deals import DealPanelView, ReviewPromptView
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Deal).where(Deal.status == "active")
            )
            active_deals = result.scalars().all()

        for deal in active_deals:
            bot.add_view(DealPanelView(deal_id=deal.deal_id))

        logger.info("Re-registered %d active deal view(s).", len(active_deals))
    except Exception as exc:
        logger.error("Failed to re-register deal views: %s", exc)


async def _re_register_listing_views() -> None:
    """Re-register ListingActionView for every active listing."""
    try:
        from database.engine import AsyncSessionLocal
        from database.models import Listing
        from cogs.deals import ListingActionView
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Listing).where(Listing.status.in_(["active", "pending_deal"]))
            )
            active_listings = result.scalars().all()

        for listing in active_listings:
            bot.add_view(ListingActionView(listing_id=listing.listing_id, format=listing.format))

        logger.info("Re-registered %d listing action view(s).", len(active_listings))
    except Exception as exc:
        logger.error("Failed to re-register listing views: %s", exc)


async def _re_register_renewal_views() -> None:
    """Re-register RenewListingView for listings that haven't expired yet."""
    try:
        from database.engine import AsyncSessionLocal
        from database.models import Listing
        from background_tasks import RenewListingView
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Listing).where(Listing.status == "active")
            )
            active = result.scalars().all()

        for listing in active:
            bot.add_view(RenewListingView(listing_id=listing.listing_id))

        logger.info("Re-registered %d renewal view(s).", len(active))
    except Exception as exc:
        logger.error("Failed to re-register renewal views: %s", exc)


bot.setup_hook = setup_hook  # type: ignore[method-assign]


# ─── Events ───────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    logger.info(
        "✅ %s is online — logged in as %s (ID: %d)",
        bot.user.display_name if bot.user else "Snag",
        bot.user,
        bot.user.id if bot.user else 0,
    )


@bot.event
async def on_guild_join(guild: discord.Guild):
    """Initialize a guild config row when the bot is added to a new server."""
    from database.engine import AsyncSessionLocal
    from database.models import GuildConfig
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        async with session.begin():
            result = await session.execute(
                select(GuildConfig).where(GuildConfig.guild_id == guild.id)
            )
            if not result.scalar_one_or_none():
                session.add(GuildConfig(guild_id=guild.id))
    logger.info("Joined guild '%s' (%d) — config row created.", guild.name, guild.id)


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: discord.app_commands.AppCommandError,
):
    """Global slash command error handler."""
    msg = str(error)
    if isinstance(error, discord.app_commands.CheckFailure):
        # check decorators already responded; swallow silently
        return
    logger.error("App command error for %s: %s", interaction.command, error)
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                f"⚠️ An error occurred: {msg[:200]}",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"⚠️ An error occurred: {msg[:200]}",
                ephemeral=True,
            )
    except Exception:
        pass


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        logger.critical(
            "DISCORD_TOKEN is not set. "
            "Add it to your Replit Secrets (key: DISCORD_TOKEN)."
        )
        sys.exit(1)

    bot.run(token, log_handler=None)  # log_handler=None — we manage logging above


if __name__ == "__main__":
    main()
