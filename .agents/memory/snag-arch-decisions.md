---
name: Snag architecture decisions
description: Key design decisions made during feature additions — channel posting removed, paginator action_buttons pattern, retention policy, Round 4 decisions.
---

# Snag architecture decisions

## Listings no longer post to channels

`_post_listing_to_guild` and `_broadcast_global_listing` were deleted. Listings are now discoverable exclusively via Check Listings (panel button). The `global_feed_channel_id` DB column and model field were kept (no migration needed) but the admin UI button to set it was removed from `admin_setup.py` `SetupView` since the setting now has no effect.

**Why:** The prompt explicitly removed channel posting. Start Deal / Place Bid now work directly from Check Listings via the paginator action_buttons, so no functionality is lost.

**How to apply:** If channel posting ever comes back, add back `_post_listing_to_guild` and wire Phase 4 back into `_finalize_listing_inner`. The `global_feed_channel_id` column is still in the DB and model — just re-add the UI setter in admin_setup.py.

## PaginatorView action_buttons pattern

`PaginatorView` now accepts `action_buttons: list[dict | list[dict] | None] | None` — one entry per item, aligned by index. Each entry is None (no button), a single dict, or a list of up to 2 dicts. Dicts have keys: `label`, `style`, `callback` (async fn taking one Interaction arg).

Critical: the callback owns the interaction entirely — PaginatorView does NOT defer before calling it. This allows callbacks to `send_modal()` (which requires no prior defer) as well as defer-then-followup flows. Action button slots are class-level `@discord.ui.button` items on row=1; removed from the view in `__init__` when `action_buttons=None` so they don't appear as phantom disabled buttons.

**Why:** Check Listings needs one button (Start Deal / Place Bid). My Listings needs two (Edit + Cancel). The paginator must not know about deals or listings — it calls whatever callback the caller passes in.

**How to apply:** When constructing PaginatorView, pass `action_buttons=` with one entry per item. Use `lambda i, lid=listing.listing_id: some_coro(i, lid)` default-arg capture to avoid the closure-in-loop bug.

## Storage retention policy

- `ARCHIVE_PURGE_AFTER_DAYS = 730` — hard-delete archive rows after 2 years (irreversible)
- `VACUUM_SWEEP_HOURS = 720` — reclaim disk space monthly

`archive_purge_sweep` runs weekly (same cadence as `archive_sweep`), deletes `ListingArchive` + their `BidArchive` rows where `archived_at < cutoff`. `vacuum_sweep` runs monthly, uses `engine.connect()` + `execution_options(isolation_level="AUTOCOMMIT")` + `exec_driver_sql("VACUUM")`.

DB path is exported as `DB_PATH: Path` from `database/engine.py` for use in logging and VACUUM size reporting.

**Why:** SQLite VACUUM cannot run inside an explicit transaction — autocommit mode is required. Deleting rows alone does not shrink the file on disk.

**How to apply:** Any future irreversible delete operation should log clearly ("This deletion is irreversible") and run on a conservative schedule. VACUUM should remain monthly at most.

## SnagView base class (all UI views)

All `discord.ui.View` subclasses use `SnagView` from `utils/base_view.py` instead. `SnagView` only adds `on_error`: logs the exception and sends the user a friendly "⚠️ Something went wrong" ephemeral message. Never raises. The `on_error` method correctly checks `interaction.response.is_done()` before choosing `followup.send` vs `response.send_message`.

**Why:** Without `on_error`, any unhandled exception inside a button callback silently shows "Interaction failed" to the user with no context and no log entry.

**How to apply:** Every new `discord.ui.View` subclass should inherit from `SnagView`. Do not inherit from `discord.ui.Modal` — modals have their own separate `on_error` handling and are not covered by SnagView.

## DM relay via on_message (no privileged intents needed)

The `Deals` cog listens to `on_message` to relay DMs between deal parties. Key facts:
- Discord's `message_content` privileged intent is **exempt for DMs the bot receives** — `message.content` is always available in DM channels without any extra intent.
- The listener is silent (returns immediately) if the sender is not in an active deal — no error messages that could confuse users.
- `last_activity_at` is bumped on every successful relay, not just on button clicks.

**Why:** The relay must work without any privileged intents (reduces review/approval risk at scale). Silence-on-no-deal is important so the bot doesn't surprise users who happen to DM it outside of a deal context.

**How to apply:** Keep the `on_message` guard order: `message.author.bot → not DMChannel → no active deal → relay`. Do not flip the order.

## interaction.user is already a full Member in guild interactions

`interaction.user` inside a guild (server) interaction is a full `discord.Member` object — Discord includes complete member data (permissions AND roles) in every interaction payload. The `members` privileged gateway intent and member cache are NOT needed for `is_admin()` checks. Removed `intents.members = True` and `intents.guild_messages = True`.

**Why:** Using `guild.get_member()` requires the member cache, which requires `members` intent — a privileged intent that requires a Discord-side review for large bots. Using `interaction.user` directly is correct AND avoids the privileged intent.

**How to apply:** For any check that only needs the invoking user's permissions or roles (not other guild members), always use `interaction.user` directly. Only reach for `guild.get_member()` when you specifically need another user's data.

## _close_deal_panels pattern

`_close_deal_panels(bot, deal, closing_embed)` in `cogs/deals.py` edits both stored DM panel messages (by `dm_message_id_initiator` / `dm_message_id_seller`) to a closed state, removing the View so buttons disappear. Every termination path calls it: cancel_btn, mark_complete_btn (both confirmed), `_expire_deal` in background_tasks.py. Local import (`from cogs.deals import _close_deal_panels`) is used in background_tasks.py to avoid a circular import.

**Why:** Without closing the panel, buttons remain clickable after a deal ends, leading to confusing or broken states (e.g. "mark complete" on an already-completed deal).

**How to apply:** Any new deal termination path must call `_close_deal_panels`. The function is guarded per-user with individual try/except and never raises, so it's safe to call unconditionally.

## build_deal_panel_embed personalized signature

`build_deal_panel_embed(deal, listing, *, viewer_role, counterpart_user, counterpart_profile, color)` — keyword-only. Built twice per deal: once for the buyer ("🛒 Buyer") and once for the seller ("💰 Seller"). Shows the OTHER party's IGN, star rating, and review count to each recipient.

**Why:** A personalized embed helps each party know WHO they're trading with and verify their reputation before sending items. The dual-build pattern mirrors how Discord sends separate embeds to each DM channel anyway.

**How to apply:** Always build two separate embed calls with swapped viewer_role/counterpart_user/counterpart_profile. Never reuse the same embed object for both parties.

## expiry_warning_sent column — startup migration pattern

New boolean column `expiry_warning_sent` on `Listing` model, with default `False`. Added via a startup migration in `database/engine.py` using `ALTER TABLE listings ADD COLUMN ...` wrapped in try/except for "duplicate column" (idempotent). This avoids needing a separate migration script for a single boolean column.

Reset to `False` in `renew_btn` so the warning fires again on the renewed expiry cycle. The `listing_expiry_sweep` "soon" query filters on `expiry_warning_sent == False` to prevent duplicate DMs.

**Why:** The `listing_expiry_sweep` runs every 6 hours — without the flag, every sweep that falls within the 24h warning window would re-send the DM. Resetting on renewal is essential or the user gets no warning after renewing.

**How to apply:** Any future simple boolean flag column should use this same startup migration pattern. For non-boolean columns or columns with complex constraints, continue using a separate migration script.

## Per-guild command sync on join only (not every boot)

`bot.tree.copy_global_to(guild=guild)` + `bot.tree.sync(guild=guild)` now runs ONLY in `on_guild_join`, not in `setup_hook`. Global `bot.tree.sync()` in `setup_hook` is sufficient for command propagation (up to 1h delay for new users, acceptable); per-guild sync on every boot is slow and risks rate-limits at scale.

**Why:** Discord rate-limits per-guild sync at 2 syncs per guild per day. A 200-guild bot booting several times a day (crash loops, updates) would exceed this limit within hours.

**How to apply:** Never add per-guild sync loops back to setup_hook. The only acceptable place is on_guild_join (first-time instant availability for new servers).
