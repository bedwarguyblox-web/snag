"""
Moderation commands — two entirely separate ban tiers:
  • Guild-scoped ban: /moderation ban_user (guild admins only, scoped to one server)
  • Global ban: /owner global_ban (bot owner only, blocks everywhere)
A guild admin command must never be able to reach the global one.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select, func, delete

from database.engine import AsyncSessionLocal
from database.models import (
    GuildBan, UserProfile, GuildConfig, Report,
    Listing, Deal,
)
from utils.checks import admin_only, bot_owner_only, get_or_create_profile
from utils.embeds import build_success_embed, build_error_embed, build_profile_embed

logger = logging.getLogger(__name__)


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    mod_group = app_commands.Group(
        name="moderation",
        description="Server moderation tools",
    )

    # ── Guild-scoped ban ───────────────────────────────────────────────────

    @mod_group.command(name="ban_user", description="Ban a user from this server's marketplace")
    @app_commands.describe(user="The user to ban", reason="Reason for the ban")
    @admin_only()
    async def ban_user(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str = "",
    ):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id
        user_id = user.id

        async with AsyncSessionLocal() as session:
            async with session.begin():
                # Check if already banned
                existing = await session.execute(
                    select(GuildBan).where(
                        GuildBan.guild_id == guild_id,
                        GuildBan.user_id == user_id,
                    )
                )
                if existing.scalar_one_or_none():
                    await interaction.followup.send(
                        embed=build_error_embed(f"{user.mention} is already banned on this server."),
                        ephemeral=True,
                    )
                    return

                ban = GuildBan(
                    guild_id=guild_id,
                    user_id=user_id,
                    reason=reason or None,
                    banned_by=interaction.user.id,
                )
                session.add(ban)

        await interaction.followup.send(
            embed=build_success_embed(
                f"{user.mention} has been **banned** from this server's marketplace.\n"
                f"Reason: {reason or '*(none given)*'}"
            ),
            ephemeral=True,
        )

    @mod_group.command(name="unban_user", description="Remove a user's server marketplace ban")
    @app_commands.describe(user_id="The Discord user ID to unban")
    @admin_only()
    async def unban_user(self, interaction: discord.Interaction, user_id: str):
        await interaction.response.defer(ephemeral=True)
        try:
            uid = int(user_id)
        except ValueError:
            await interaction.followup.send(embed=build_error_embed("Invalid user ID."), ephemeral=True)
            return

        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(GuildBan).where(
                        GuildBan.guild_id == interaction.guild_id,
                        GuildBan.user_id == uid,
                    )
                )
                ban = result.scalar_one_or_none()
                if not ban:
                    await interaction.followup.send(
                        embed=build_error_embed(f"User `{uid}` is not banned on this server."),
                        ephemeral=True,
                    )
                    return
                await session.delete(ban)

        await interaction.followup.send(
            embed=build_success_embed(f"User `{uid}` has been **unbanned** from this server's marketplace."),
            ephemeral=True,
        )

    @mod_group.command(name="lookup_user", description="View a user's marketplace profile (this server's context)")
    @app_commands.describe(user="The user to look up")
    @admin_only()
    async def lookup_user(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer(ephemeral=True)
        user_id = user.id
        guild_id = interaction.guild_id

        async with AsyncSessionLocal() as session:
            # Profile
            r = await session.execute(
                select(UserProfile).where(UserProfile.user_id == user_id)
            )
            profile = r.scalar_one_or_none()

            # Open reports in this guild
            r2 = await session.execute(
                select(func.count()).where(
                    Report.reported_user_id == user_id,
                    Report.guild_id == guild_id,
                    Report.status == "open",
                )
            )
            open_reports = r2.scalar_one()

            # Guild ban
            r3 = await session.execute(
                select(GuildBan).where(
                    GuildBan.guild_id == guild_id,
                    GuildBan.user_id == user_id,
                )
            )
            guild_ban = r3.scalar_one_or_none()

        if not profile:
            await interaction.followup.send(
                embed=discord.Embed(
                    title=f"👤 {user.display_name}",
                    description="No marketplace profile found.",
                    color=discord.Color.greyple(),
                ),
                ephemeral=True,
            )
            return

        embed = build_profile_embed(profile, user)
        embed.add_field(name="Open Reports (this server)", value=str(open_reports), inline=True)
        if guild_ban:
            embed.add_field(
                name="🔨 Server Ban",
                value=f"Reason: {guild_ban.reason or 'none'}\nBanned by: <@{guild_ban.banned_by}>",
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @mod_group.command(name="stats", description="Marketplace statistics for this server (admin only)")
    @admin_only()
    async def stats(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id

        async with AsyncSessionLocal() as session:
            # Listings: scoped to this server (origin_guild_id).
            active_listings = (
                await session.execute(
                    select(func.count()).where(
                        Listing.status == "active",
                        Listing.origin_guild_id == guild_id,
                    )
                )
            ).scalar_one()
            # Deals: cross-server by design (a deal may span two guilds) — show
            # network-wide counts and label them clearly so admins aren't confused.
            active_deals = (
                await session.execute(select(func.count()).where(Deal.status == "active"))
            ).scalar_one()
            completed_deals = (
                await session.execute(select(func.count()).where(Deal.status == "completed"))
            ).scalar_one()

            # Top 5 rated users
            top_result = await session.execute(
                select(UserProfile)
                .where(UserProfile.global_rating_count > 0)
                .order_by(
                    (UserProfile.global_rating_sum / UserProfile.global_rating_count).desc()
                )
                .limit(5)
            )
            top_users = top_result.scalars().all()

        embed = discord.Embed(title="📊 Snag Marketplace Stats", color=0x5865F2)
        embed.add_field(name="Active Listings (this server)", value=str(active_listings), inline=True)
        embed.add_field(name="Active Deals (network-wide)", value=str(active_deals), inline=True)
        embed.add_field(name="Completed Deals (network-wide)", value=str(completed_deals), inline=True)

        if top_users:
            top_lines = []
            for i, u in enumerate(top_users, 1):
                avg = u.global_rating_sum / u.global_rating_count
                top_lines.append(f"{i}. <@{u.user_id}> — ⭐ {avg:.1f} ({u.global_rating_count} reviews)")
            embed.add_field(name="Top 5 Rated Traders", value="\n".join(top_lines), inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)


# ─── Owner-only global ban commands ───────────────────────────────────────────

class OwnerCommands(commands.Cog):
    """
    Commands restricted to the bot owner only.
    Guard: await bot.is_owner() — never a guild permission.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    owner_group = app_commands.Group(name="owner", description="Bot owner commands")

    @owner_group.command(name="global_ban", description="[Owner only] Globally ban a user from all marketplaces")
    @app_commands.describe(user_id="Discord user ID to ban", reason="Reason for the global ban")
    @bot_owner_only()
    async def global_ban(
        self,
        interaction: discord.Interaction,
        user_id: str,
        reason: str = "",
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            uid = int(user_id)
        except ValueError:
            await interaction.followup.send(embed=build_error_embed("Invalid user ID."), ephemeral=True)
            return

        async with AsyncSessionLocal() as session:
            async with session.begin():
                profile = await get_or_create_profile(session, uid)
                profile.is_banned = True
                profile.ban_reason = reason or None

        await interaction.followup.send(
            embed=build_success_embed(
                f"User `{uid}` has been **globally banned** from all Snag marketplaces.\n"
                f"Reason: {reason or '*(none given)*'}"
            ),
            ephemeral=True,
        )

    @owner_group.command(name="global_unban", description="[Owner only] Remove a global ban")
    @app_commands.describe(user_id="Discord user ID to unban")
    @bot_owner_only()
    async def global_unban(self, interaction: discord.Interaction, user_id: str):
        await interaction.response.defer(ephemeral=True)
        try:
            uid = int(user_id)
        except ValueError:
            await interaction.followup.send(embed=build_error_embed("Invalid user ID."), ephemeral=True)
            return

        async with AsyncSessionLocal() as session:
            async with session.begin():
                r = await session.execute(select(UserProfile).where(UserProfile.user_id == uid))
                profile = r.scalar_one_or_none()
                if not profile or not profile.is_banned:
                    await interaction.followup.send(
                        embed=build_error_embed(f"User `{uid}` is not globally banned."),
                        ephemeral=True,
                    )
                    return
                profile.is_banned = False
                profile.ban_reason = None

        await interaction.followup.send(
            embed=build_success_embed(f"User `{uid}` has been **globally unbanned**."),
            ephemeral=True,
        )

    @owner_group.command(name="lookup", description="[Owner only] Full profile lookup by user ID")
    @app_commands.describe(user_id="Discord user ID")
    @bot_owner_only()
    async def owner_lookup(self, interaction: discord.Interaction, user_id: str):
        await interaction.response.defer(ephemeral=True)
        try:
            uid = int(user_id)
        except ValueError:
            await interaction.followup.send(embed=build_error_embed("Invalid user ID."), ephemeral=True)
            return

        user = interaction.client.get_user(uid)
        if not user:
            try:
                user = await interaction.client.fetch_user(uid)
            except discord.NotFound:
                pass

        async with AsyncSessionLocal() as session:
            r = await session.execute(select(UserProfile).where(UserProfile.user_id == uid))
            profile = r.scalar_one_or_none()

        if not profile:
            await interaction.followup.send(
                embed=discord.Embed(description=f"No profile for `{uid}`.", color=discord.Color.greyple()),
                ephemeral=True,
            )
            return

        await interaction.followup.send(embed=build_profile_embed(profile, user), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
    await bot.add_cog(OwnerCommands(bot))
