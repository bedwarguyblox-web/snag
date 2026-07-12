"""
Shared check decorators and helper functions.
Every panel-button callback checks global ban first (cheap), then guild ban.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from sqlalchemy import select

if TYPE_CHECKING:
    from discord.ext import commands

from database.engine import AsyncSessionLocal
from database.models import GuildConfig, GuildBan, UserProfile

logger = logging.getLogger(__name__)


# ─── Low-level DB helpers ─────────────────────────────────────────────────────

async def is_globally_banned(user_id: int) -> bool:
    """Check user_profiles.is_banned — the only mechanism that blocks all guilds."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(UserProfile.is_banned).where(UserProfile.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        return bool(row)  # None → not banned


async def is_guild_banned(guild_id: int, user_id: int) -> bool:
    """Check guild_bans — scoped to a single server only."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(GuildBan).where(
                GuildBan.guild_id == guild_id,
                GuildBan.user_id == user_id,
            )
        )
        return result.scalar_one_or_none() is not None


async def get_or_create_profile(session, user_id: int) -> UserProfile:
    """Fetch or create a UserProfile row."""
    result = await session.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        profile = UserProfile(user_id=user_id)
        session.add(profile)
        await session.flush()
    return profile


async def check_marketplace_access(interaction: discord.Interaction) -> bool:
    """
    Combined ban gate for interactive entry points.
    Returns True if the user may proceed, False if blocked (response already sent).
    """
    user_id = interaction.user.id
    guild_id = interaction.guild_id

    if await is_globally_banned(user_id):
        await interaction.followup.send(
            embed=_banned_embed("You are globally banned from the Snag marketplace."),
            ephemeral=True,
        )
        return False

    if guild_id and await is_guild_banned(guild_id, user_id):
        await interaction.followup.send(
            embed=_banned_embed("You are banned from the marketplace on this server."),
            ephemeral=True,
        )
        return False

    return True


def _banned_embed(msg: str) -> discord.Embed:
    return discord.Embed(title="⛔ Access Denied", description=msg, color=discord.Color.red())


# ─── Admin guard helper ───────────────────────────────────────────────────────

async def is_admin(interaction: discord.Interaction) -> bool:
    """True if the invoker has Manage Guild or holds the guild's configured admin_role_id."""
    if interaction.guild is None:
        return False
    member = interaction.guild.get_member(interaction.user.id)
    if member is None:
        return False
    if member.guild_permissions.manage_guild:
        return True

    # Check configured admin_role_id
    import utils.cache as cache_mod
    config = cache_mod.get(interaction.guild_id)
    if config is None:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(GuildConfig).where(GuildConfig.guild_id == interaction.guild_id)
            )
            config = result.scalar_one_or_none()
            if config:
                cache_mod.set(config)

    if config and config.admin_role_id:
        return any(r.id == config.admin_role_id for r in member.roles)

    return False


# ─── app_commands check decorators ───────────────────────────────────────────

def admin_only():
    """app_commands check: must be guild admin or have the configured admin role."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if not await is_admin(interaction):
            await interaction.response.send_message(
                "You need the **Manage Server** permission or the bot's admin role to use this.",
                ephemeral=True,
            )
            return False
        return True
    return app_commands.check(predicate)


def bot_owner_only():
    """app_commands check: hardcoded owner ID — only BOT_OWNER_ID may run these."""
    async def predicate(interaction: discord.Interaction) -> bool:
        from config import BOT_OWNER_ID
        if interaction.user.id != BOT_OWNER_ID:
            await interaction.response.send_message(
                "This command is restricted to the bot owner.",
                ephemeral=True,
            )
            return False
        return True
    return app_commands.check(predicate)
