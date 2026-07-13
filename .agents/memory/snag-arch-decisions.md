---
name: Snag architecture decisions
description: Key design decisions made during feature additions — channel posting removed, paginator action_buttons pattern, retention policy.
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
