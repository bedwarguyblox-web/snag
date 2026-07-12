# Snag — Discord Trading Marketplace Bot

## Overview
Snag is a Discord bot for cross-server Minecraft trading marketplaces. Users can create listings (server-scoped or global), browse/filter listings, start deals, bid in auctions, leave reviews, and get moderated.

## Stack
- **Language:** Python 3.11+
- **Discord library:** discord.py 2.4+
- **Database:** SQLite via SQLAlchemy 2.0 (async, aiosqlite)
- **Database file:** `snag-bot/database/snag.db` (auto-created)

## How to run
The workflow `Snag Discord Bot` runs: `cd snag-bot && python main.py`

**Required secrets (set in Replit Secrets):**
- `DISCORD_TOKEN` — your bot token from the Discord Developer Portal
- `CLIENT_ID` — your application ID (optional; a default is already in `config.py`)

## Project structure
```
snag-bot/
  main.py              — Entry point: boot, cog loading, persistent view re-registration, slash sync
  config.py            — All tunable constants (rate limits, thresholds, etc.)
  background_tasks.py  — Background sweep loops (deal timeout, auction end, listing expiry, archive)
  cogs/
    admin_setup.py     — /setup commands (panel, preferences, categories, colors)
    panel_views.py     — Persistent MainPanelView (3 buttons: Create Listing, Check Listings, My IGN)
    listings.py        — Listing wizard, filter/search UI, /listing edit/cancel
    deals.py           — Deal creation, persistent DM deal panel, dual-confirm completion
    bidding.py         — Auction bid flow (atomic, anti-snipe, cooldowns)
    reviews.py         — Post-deal review modal
    moderation.py      — /moderation (guild-scoped) and /owner (global) ban commands
  database/
    engine.py          — Async SQLAlchemy engine (SQLite + StaticPool)
    models.py          — All ORM models
  utils/
    cache.py           — In-memory guild config cache
    checks.py          — Ban checks, admin guard, get_or_create_profile
    embeds.py          — Shared embed builders
    pagination.py      — PaginatorView for multi-page listing results
    parsing.py         — parse_amount() for shorthand numbers (10k, 2.5m, 1b)
```

## Key design notes
- **Wizard state is in-memory only** until the user confirms. Nothing hits the DB until "Confirm & Post".
- **Persistent views** (MainPanelView, DealPanelView, ListingActionView, RenewListingView) are re-registered on every startup so buttons survive bot restarts.
- **Place Bid** opens a modal directly via `send_modal()` — never deferred first (Discord API constraint).
- **SQLite primary keys** must use `Integer` (not `BigInteger`) for autoincrement to work correctly.

## User preferences
