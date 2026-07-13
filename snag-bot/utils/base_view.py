"""
Base view class for all Snag UI views.
Provides a shared on_error handler so uncaught exceptions inside button/select
callbacks show the user a friendly message instead of a silent "Interaction failed".

Usage: every `class X(discord.ui.View)` should be `class X(SnagView)` instead.
"""

from __future__ import annotations

import logging

import discord

logger = logging.getLogger(__name__)


class SnagView(discord.ui.View):
    """
    Drop-in replacement for discord.ui.View.  The only addition is on_error —
    every other behaviour (timeout, custom_id, add_item, etc.) is unchanged.
    """

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
    ) -> None:
        logger.exception(
            "Unhandled error in %s (item=%s)",
            type(self).__name__,
            item,
            exc_info=error,
        )
        msg = (
            "⚠️ Something went wrong on my end. Please try again, "
            "and let a server admin know if it keeps happening."
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass
