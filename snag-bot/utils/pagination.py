"""
Reusable paginator View for listing results.
Shows LISTINGS_PER_PAGE results per page with Prev / Next navigation.
"""

from __future__ import annotations

from typing import Callable

import discord

from config import LISTINGS_PER_PAGE
from utils.base_view import SnagView


class PaginatorView(SnagView):
    """
    Generic paginator.  Pass a list of discord.Embed objects (one per item)
    or a list of (title, description) tuples — the paginator wraps them into
    a single embed with multiple fields per page.

    action_buttons: optional list, one entry per item (aligned by index).
    Each entry is one of:
      - None          — no action button on this page
      - dict          — one button: {"label": str, "style": ButtonStyle, "callback": async_fn}
      - list[dict]    — up to 2 buttons shown side-by-side (e.g. Edit + Cancel)

    The callback is an async function that takes a single discord.Interaction argument.
    It owns the interaction entirely — the paginator does NOT defer before calling it.
    This allows callbacks to open modals (which require send_modal, not followup)
    as well as defer-then-followup flows for non-modal actions.
    """

    def __init__(
        self,
        items: list[discord.Embed],
        *,
        action_buttons: list[dict | list[dict] | None] | None = None,
        timeout: float = 120.0,
    ):
        super().__init__(timeout=timeout)
        self.items = items
        self.page = 0
        # When items are full listing embeds, show one per page so all fields
        # (price, category, server, rating) are visible — not just a truncated description.
        embed_items = items and isinstance(items[0], discord.Embed)
        self.per_page = 1 if embed_items else LISTINGS_PER_PAGE
        self.total_pages = max(1, (len(items) + self.per_page - 1) // self.per_page)
        # Snapshot each embed's original footer text NOW, before current_page_embed()
        # ever mutates it.  This prevents the "• N/M • N/M…" growth bug where
        # revisiting a page appends another suffix to an already-mutated footer.
        self._original_footers: list[str] = [
            (item.footer.text or "") if isinstance(item, discord.Embed) else ""
            for item in items
        ]
        # Per-page action callbacks.  Always a 2-slot list; None = slot unused.
        self._action_callbacks: list[Callable | None] = [None, None]
        self._action_buttons_config = action_buttons

        if action_buttons is None:
            # No action buttons needed for this paginator — remove the slots entirely
            # so they don't appear as grayed-out phantom buttons in Discord.
            self.remove_item(self.action_btn_1)
            self.remove_item(self.action_btn_2)

        self._update_buttons()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _update_buttons(self) -> None:
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page >= self.total_pages - 1
        if self._action_buttons_config is not None:
            self._refresh_action_buttons()

    def _refresh_action_buttons(self) -> None:
        """
        Relabel and rebind the action button slots for the current page.
        Called from _update_buttons() on every Prev/Next click.
        Mirrors how ListingActionView swaps label/custom_id in __init__,
        but applied per-PAGE rather than per-instance.
        """
        self._action_callbacks = [None, None]
        start = self.page * self.per_page

        if start < len(self._action_buttons_config):
            entry = self._action_buttons_config[start]
            if entry is None:
                configs: list[dict] = []
            elif isinstance(entry, list):
                configs = list(entry[:2])
            else:
                configs = [entry]
        else:
            configs = []

        # Slot 1
        if configs:
            c = configs[0]
            self.action_btn_1.label = c["label"]
            self.action_btn_1.style = c["style"]
            self.action_btn_1.disabled = False
            self._action_callbacks[0] = c["callback"]
        else:
            self.action_btn_1.label = "—"
            self.action_btn_1.style = discord.ButtonStyle.secondary
            self.action_btn_1.disabled = True

        # Slot 2
        if len(configs) >= 2:
            c = configs[1]
            self.action_btn_2.label = c["label"]
            self.action_btn_2.style = c["style"]
            self.action_btn_2.disabled = False
            self._action_callbacks[1] = c["callback"]
        else:
            self.action_btn_2.label = "—"
            self.action_btn_2.style = discord.ButtonStyle.secondary
            self.action_btn_2.disabled = True

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
            # Always build footer from the ORIGINAL text snapshotted at init, not
            # from embed.footer.text which may already carry a previous "• N/M" suffix.
            original_footer = self._original_footers[start]
            embed.set_footer(text=f"{original_footer}  •  {self.page + 1}/{self.total_pages}")
            return embed

        return discord.Embed(
            title=f"📋 Listings — Page {self.page + 1}/{self.total_pages}",
            description="\n".join(str(i) for i in page_items),
            color=0x5865F2,
        )

    # ── navigation buttons (row 0) ────────────────────────────────────────────

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, custom_id="paginator:prev", row=0)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.page = max(0, self.page - 1)
        self._update_buttons()
        await interaction.edit_original_response(embed=self.current_page_embed(), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary, custom_id="paginator:next", row=0)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.page = min(self.total_pages - 1, self.page + 1)
        self._update_buttons()
        await interaction.edit_original_response(embed=self.current_page_embed(), view=self)

    # ── action button slots (row 1, optional) ─────────────────────────────────
    # Declared as class-level buttons; removed from the view in __init__ when
    # no action_buttons were provided.  When present, _refresh_action_buttons()
    # updates their label/style/disabled state on every page change.
    #
    # The callback owns the interaction entirely — the paginator does NOT defer
    # before dispatching, allowing callbacks to call send_modal() if needed.

    @discord.ui.button(label="—", style=discord.ButtonStyle.secondary, custom_id="paginator:action_1", row=1, disabled=True)
    async def action_btn_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        cb = self._action_callbacks[0]
        if cb:
            await cb(interaction)

    @discord.ui.button(label="—", style=discord.ButtonStyle.secondary, custom_id="paginator:action_2", row=1, disabled=True)
    async def action_btn_2(self, interaction: discord.Interaction, button: discord.ui.Button):
        cb = self._action_callbacks[1]
        if cb:
            await cb(interaction)
