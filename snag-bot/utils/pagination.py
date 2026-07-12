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
        # When items are full listing embeds, show one per page so all fields
        # (price, category, server, rating) are visible — not just a truncated description.
        embed_items = items and isinstance(items[0], discord.Embed)
        self.per_page = 1 if embed_items else LISTINGS_PER_PAGE
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

        # Items are full listing embeds — return them directly so price/category/
        # server/rating fields are preserved.  Page counter goes in the footer.
        if isinstance(page_items[0], discord.Embed):
            embed = page_items[0]
            # Append page info to existing footer text (build_listing_embed sets one)
            existing_footer = embed.footer.text or ""
            embed.set_footer(text=f"{existing_footer}  •  {self.page + 1}/{self.total_pages}")
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
