"""
/setup command tree — guild preferences editor.
Only usable by members with Manage Guild permission or the guild's configured admin_role_id.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

import utils.cache as guild_cache
from database.engine import AsyncSessionLocal
from database.models import GuildConfig
from utils.checks import admin_only, is_admin
from utils.embeds import build_success_embed, build_error_embed

PRESET_COLORS = [
    ("#5865F2", "Discord Blurple"),
    ("#57F287", "Green"),
    ("#FEE75C", "Yellow"),
    ("#EB459E", "Fuchsia"),
    ("#ED4245", "Red"),
    ("#FFFFFF", "White"),
    ("#23272A", "Dark"),
]


async def _get_or_create_config(guild_id: int, session) -> GuildConfig:
    result = await session.execute(
        select(GuildConfig).where(GuildConfig.guild_id == guild_id)
    )
    config = result.scalar_one_or_none()
    if config is None:
        config = GuildConfig(guild_id=guild_id)
        session.add(config)
        await session.flush()
    return config


class CustomHexModal(discord.ui.Modal, title="Set Custom Embed Color"):
    hex_input = discord.ui.TextInput(
        label="Hex color (e.g. #FF5733)",
        placeholder="#RRGGBB",
        max_length=7,
        min_length=4,
    )

    async def on_submit(self, interaction: discord.Interaction):
        value = self.hex_input.value.strip()
        if not value.startswith("#") or len(value) not in (4, 7):
            await interaction.response.send_message(
                embed=build_error_embed("Invalid hex color. Use format #RRGGBB or #RGB."),
                ephemeral=True,
            )
            return
        async with AsyncSessionLocal() as session:
            async with session.begin():
                config = await _get_or_create_config(interaction.guild_id, session)
                config.embed_color = value
        guild_cache.invalidate(interaction.guild_id)
        await interaction.response.send_message(
            embed=build_success_embed(f"Embed color set to `{value}`."),
            ephemeral=True,
        )


class ManageCategoriesModal(discord.ui.Modal, title="Manage Server Categories"):
    categories_input = discord.ui.TextInput(
        label="Custom categories (comma-separated)",
        placeholder="Diamonds, Netherite, Services, …",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.categories_input.value.strip()
        cats = [c.strip() for c in raw.split(",") if c.strip()] if raw else []
        async with AsyncSessionLocal() as session:
            async with session.begin():
                config = await _get_or_create_config(interaction.guild_id, session)
                config.custom_categories = cats
        guild_cache.invalidate(interaction.guild_id)
        names = ", ".join(cats) if cats else "*(none)*"
        await interaction.response.send_message(
            embed=build_success_embed(f"Categories updated: {names}"),
            ephemeral=True,
        )


class SetLogChannelModal(discord.ui.Modal, title="Set Log Channel ID"):
    channel_id_input = discord.ui.TextInput(
        label="Log Channel ID",
        placeholder="Paste the channel snowflake ID",
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            cid = int(self.channel_id_input.value.strip())
        except ValueError:
            await interaction.response.send_message(
                embed=build_error_embed("That doesn't look like a valid channel ID."),
                ephemeral=True,
            )
            return
        async with AsyncSessionLocal() as session:
            async with session.begin():
                config = await _get_or_create_config(interaction.guild_id, session)
                config.log_channel_id = cid
        guild_cache.invalidate(interaction.guild_id)
        await interaction.response.send_message(
            embed=build_success_embed(f"Log channel set to <#{cid}>."),
            ephemeral=True,
        )


class SetAdminRoleModal(discord.ui.Modal, title="Set Admin Role ID"):
    role_id_input = discord.ui.TextInput(
        label="Admin Role ID",
        placeholder="Paste the role snowflake ID",
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            rid = int(self.role_id_input.value.strip())
        except ValueError:
            await interaction.response.send_message(
                embed=build_error_embed("That doesn't look like a valid role ID."),
                ephemeral=True,
            )
            return
        async with AsyncSessionLocal() as session:
            async with session.begin():
                config = await _get_or_create_config(interaction.guild_id, session)
                config.admin_role_id = rid
        guild_cache.invalidate(interaction.guild_id)
        await interaction.response.send_message(
            embed=build_success_embed(f"Admin role set to <@&{rid}>."),
            ephemeral=True,
        )


class SetupView(discord.ui.View):
    """Ephemeral view shown by /setup — all settings in one place."""

    def __init__(self, config: GuildConfig):
        super().__init__(timeout=300)
        self._config = config
        # Populate color select options
        options = [
            discord.SelectOption(label=name, value=hex_val, default=(hex_val == config.embed_color))
            for hex_val, name in PRESET_COLORS
        ]
        self.color_select.options = options

    @discord.ui.select(
        placeholder="Choose embed color…",
        custom_id="setup:color_select",
        min_values=1,
        max_values=1,
    )
    async def color_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer(ephemeral=True)
        chosen = select.values[0]
        async with AsyncSessionLocal() as session:
            async with session.begin():
                config = await _get_or_create_config(interaction.guild_id, session)
                config.embed_color = chosen
        guild_cache.invalidate(interaction.guild_id)
        await interaction.followup.send(
            embed=discord.Embed(
                description=f"✅ Embed color set to `{chosen}`.",
                color=int(chosen.lstrip("#"), 16),
            ),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Custom Hex Color",
        style=discord.ButtonStyle.secondary,
        custom_id="setup:custom_hex",
        row=1,
    )
    async def custom_hex_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CustomHexModal())

    @discord.ui.button(
        label="Toggle Global Listings",
        style=discord.ButtonStyle.primary,
        custom_id="setup:toggle_global",
        row=2,
    )
    async def toggle_global(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        async with AsyncSessionLocal() as session:
            async with session.begin():
                config = await _get_or_create_config(interaction.guild_id, session)
                config.allow_global_listings = not config.allow_global_listings
                new_val = config.allow_global_listings
        guild_cache.invalidate(interaction.guild_id)
        state = "enabled" if new_val else "disabled"
        await interaction.followup.send(
            embed=build_success_embed(f"Global listings are now **{state}**."),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Toggle Server Listings",
        style=discord.ButtonStyle.primary,
        custom_id="setup:toggle_server",
        row=2,
    )
    async def toggle_server(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        async with AsyncSessionLocal() as session:
            async with session.begin():
                config = await _get_or_create_config(interaction.guild_id, session)
                config.allow_server_listings = not config.allow_server_listings
                new_val = config.allow_server_listings
        guild_cache.invalidate(interaction.guild_id)
        state = "enabled" if new_val else "disabled"
        await interaction.followup.send(
            embed=build_success_embed(f"Server listings are now **{state}**."),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Manage Categories",
        style=discord.ButtonStyle.secondary,
        custom_id="setup:manage_cats",
        row=3,
    )
    async def manage_categories(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ManageCategoriesModal())

    @discord.ui.button(
        label="Set Log Channel",
        style=discord.ButtonStyle.secondary,
        custom_id="setup:log_channel",
        row=3,
    )
    async def set_log_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SetLogChannelModal())

    @discord.ui.button(
        label="Set Admin Role",
        style=discord.ButtonStyle.secondary,
        custom_id="setup:admin_role",
        row=4,
    )
    async def set_admin_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SetAdminRoleModal())


class AdminSetup(commands.Cog):
    """Guild configuration commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    setup_group = app_commands.Group(name="setup", description="Configure Snag for this server")

    @setup_group.command(name="preferences", description="Open the server preferences editor")
    @admin_only()
    async def preferences(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        async with AsyncSessionLocal() as session:
            async with session.begin():
                config = await _get_or_create_config(interaction.guild_id, session)
                guild_cache.set(config)

        embed = discord.Embed(
            title="⚙️ Snag — Server Preferences",
            description=(
                f"**Embed Color:** `{config.embed_color}`\n"
                f"**Global Listings:** {'✅ Enabled' if config.allow_global_listings else '❌ Disabled'}\n"
                f"**Server Listings:** {'✅ Enabled' if config.allow_server_listings else '❌ Disabled'}\n"
                f"**Custom Categories:** {', '.join(config.custom_categories) or '*(none)*'}\n"
                f"**Log Channel:** {'<#' + str(config.log_channel_id) + '>' if config.log_channel_id else '*(not set)*'}\n"
                f"**Admin Role:** {'<@&' + str(config.admin_role_id) + '>' if config.admin_role_id else '*(not set)*'}"
            ),
            color=int(config.embed_color.lstrip("#"), 16),
        )

        await interaction.followup.send(embed=embed, view=SetupView(config), ephemeral=True)

    @setup_group.command(name="panel", description="Send or re-send the marketplace panel to a channel")
    @admin_only()
    async def panel_send(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)

        # Check bot permissions in target channel
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.send_messages and perms.embed_links):
            await interaction.followup.send(
                embed=build_error_embed(
                    f"I need **Send Messages** and **Embed Links** permissions in {channel.mention}."
                ),
                ephemeral=True,
            )
            return

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(GuildConfig).where(GuildConfig.guild_id == interaction.guild_id)
            )
            config = result.scalar_one_or_none()

        # Warn if panel already exists
        if config and config.panel_message_id:
            view_confirm = _ConfirmDuplicatePanelView(channel, interaction)
            await interaction.followup.send(
                embed=discord.Embed(
                    title="⚠️ Panel Already Exists",
                    description=(
                        f"A panel was already sent to <#{config.panel_channel_id}>.\n"
                        "Send a new one anyway?"
                    ),
                    color=discord.Color.yellow(),
                ),
                view=view_confirm,
                ephemeral=True,
            )
        else:
            await _do_send_panel(interaction, channel)


class _ConfirmDuplicatePanelView(discord.ui.View):
    def __init__(self, channel: discord.TextChannel, original_interaction: discord.Interaction):
        super().__init__(timeout=60)
        self._channel = channel
        self._original = original_interaction

    @discord.ui.button(label="Yes, send it", style=discord.ButtonStyle.danger, custom_id="panel:confirm_dup")
    async def confirm(self, interaction: discord.Interaction, _):
        await interaction.response.defer(ephemeral=True)
        await _do_send_panel(self._original, self._channel)
        await interaction.followup.send("✅ New panel sent.", ephemeral=True)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, custom_id="panel:cancel_dup")
    async def cancel(self, interaction: discord.Interaction, _):
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("Cancelled.", ephemeral=True)
        self.stop()


async def _do_send_panel(interaction: discord.Interaction, channel: discord.TextChannel):
    from cogs.panel_views import MainPanelView
    from utils.embeds import add_invite_branding
    import utils.cache as guild_cache

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(GuildConfig).where(GuildConfig.guild_id == interaction.guild_id)
        )
        config = result.scalar_one_or_none()
    color = int(config.embed_color.lstrip("#"), 16) if config else 0x5865F2

    embed = discord.Embed(
        title="🛒 Snag Marketplace",
        description=(
            "Welcome to the **Snag** cross-server trading marketplace!\n\n"
            "• **Create Listing** — Post an item, service, or bid for sale\n"
            "• **Check Listings** — Browse and filter active listings\n"
            "• **My IGN** — Set your in-game name for buyers/sellers to see"
        ),
        color=color,
    )
    add_invite_branding(embed)

    msg = await channel.send(embed=embed, view=MainPanelView())

    async with AsyncSessionLocal() as session:
        async with session.begin():
            if config is None:
                config = GuildConfig(guild_id=interaction.guild_id)
                session.add(config)
                await session.flush()
            else:
                result = await session.execute(
                    select(GuildConfig).where(GuildConfig.guild_id == interaction.guild_id)
                )
                config = result.scalar_one_or_none()
            config.panel_channel_id = channel.id
            config.panel_message_id = msg.id

    guild_cache.invalidate(interaction.guild_id)
    await interaction.followup.send(
        embed=build_success_embed(f"Panel sent to {channel.mention}."),
        ephemeral=True,
    )


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminSetup(bot))
