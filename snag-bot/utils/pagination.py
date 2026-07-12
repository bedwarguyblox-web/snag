"""
Reusable paginator View for listing results.
Shows LISTINGS_PER_PAGE results per page with Prev / Next navigation.
"""

from __future__ import annotations

import discord
from config import LISTINGS_PER_PAGE


class PaginatorView(discord.ui.View):
    """
    Generic paginator.  Pass a list of discord.Embed objects (one per item)
    or a list of (title, description) tuples — the paginator wraps them into
    a single embed with multiple fields per page.
    """

    def __init__(self, items: list[discord.Embed], *, timeout: float = 120.0):
        super().__init__(timeout=timeout)
        self.items = items
        self.page = 0
        self.per_page = LISTINGS_PER_PAGE
        self.total_pages = max(1, (len(items) + self.per_page - 1) // self.per_page)
        self._update_buttons()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _update_buttons(self) -> None:
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page >= self.total_pages - 1

    def current_page_embed(self) -> discord.Embed:
        start = self.page * self.per_page
        end = start + self.per_page
        page_items = self.items[start:end]

        if not page_items:
            return discord.Embed(
                title="No results",
                description="No listings matched your filters.",
                color=discord.Color.greyple(),
            )

        # If items are already full embeds, return the first one with page info
        if isinstance(page_items[0], discord.Embed):
            embed = discord.Embed(
                title=f"📋 Listings — Page {self.page + 1}/{self.total_pages}",
                color=0x5865F2,
            )
            for item_embed in page_items:
                embed.add_field(
                    name=item_embed.title or "Listing",
                    value=(item_embed.description or "")[:200] + "…"
                    if len(item_embed.description or "") > 200
                    else (item_embed.description or ""),
                    inline=False,
                )
            return embed

        return discord.Embed(
            title=f"📋 Listings — Page {self.page + 1}/{self.total_pages}",
            description="\n".join(str(i) for i in page_items),
            color=0x5865F2,
        )

    # ── buttons ──────────────────────────────────────────────────────────────

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, custom_id="paginator:prev")
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.page = max(0, self.page - 1)
        self._update_buttons()
        await interaction.edit_original_response(embed=self.current_page_embed(), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary, custom_id="paginator:next")
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.page = min(self.total_pages - 1, self.page + 1)
        self._update_buttons()
        await interaction.edit_original_response(embed=self.current_page_embed(), view=self)
