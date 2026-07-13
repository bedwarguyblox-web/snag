"""
Persistent View classes for the 4 main panel buttons.
Registered in setup_hook() with fixed custom_ids so they survive restarts.
"""

from __future__ import annotations

import discord
from discord.ext import commands


class MainPanelView(discord.ui.View):
    """
    The permanent 4-button panel.  Registered on setup_hook with timeout=None
    so it persists across restarts.
    """

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="✏️ Create Listing",
        style=discord.ButtonStyle.primary,
        custom_id="panel:create_listing",
    )
    async def create_listing(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        from cogs.listings import start_create_listing_wizard
        await start_create_listing_wizard(interaction)

    @discord.ui.button(
        label="🔍 Check Listings",
        style=discord.ButtonStyle.secondary,
        custom_id="panel:check_listings",
    )
    async def check_listings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        from cogs.listings import start_check_listings
        await start_check_listings(interaction)

    @discord.ui.button(
        label="📋 My Listings",
        style=discord.ButtonStyle.secondary,
        custom_id="panel:my_listings",
    )
    async def my_listings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        from cogs.listings import start_my_listings
        await start_my_listings(interaction)

    @discord.ui.button(
        label="🎮 My IGN",
        style=discord.ButtonStyle.secondary,
        custom_id="panel:my_ign",
    )
    async def my_ign(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.listings import IGNModal
        await interaction.response.send_modal(IGNModal())


class PanelViews(commands.Cog):
    """Cog that registers persistent views — no commands here."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    await bot.add_cog(PanelViews(bot))
    # Views are registered in main.py's setup_hook
