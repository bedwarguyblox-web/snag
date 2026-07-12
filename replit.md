# Snag — Discord Cross-Server Minecraft Marketplace Bot

A Discord bot for trading items, services, and auctions across multiple servers, with a focus on Minecraft SMP communities (Donut SMP flagship). Listings can be server-scoped or broadcast globally; all deal negotiation happens in DMs.

## User preferences

- **Do not host/run this bot on Replit.** The owner already runs it on their own host. The "Snag Discord Bot" workflow exists because it was auto-generated on import — leave it stopped; don't start it or suggest deploying/publishing this bot from Replit. Use this Repl for code edits only.

## Run & Operate

- **Start the bot:** use the "Snag Discord Bot" workflow in Replit
- **Command:** `cd snag-bot && python3 main.py`

## Required Secrets (add in Replit Secrets panel)

| Key | Where to find it |
|-----|-----------------|
| `DISCORD_TOKEN` | Discord Developer Portal → your app → **Bot** tab → Token |
| `CLIENT_ID` | Discord Developer Portal → your app → **General Information** → Application ID |
| `DATABASE_URL` | Auto-provided by Replit — do not set manually |

Also enable the **Server Members Intent** in the Discord Developer Portal → your app → Bot → Privileged Gateway Intents.

## Stack

- Python 3.11, discord.py 2.4+
- PostgreSQL (Replit built-in) + SQLAlchemy 2.0 async + asyncpg
- All slash commands via discord.py app_commands

## Project Structure

```
snag-bot/
├── main.py                 # Entry point — bot init, cog loader, setup_hook
├── config.py               # All constants (limits, cooldowns, thresholds)
├── database/
│   ├── engine.py           # Async engine + sessionmaker
│   └── models.py           # All ORM models + indexes
├── cogs/
│   ├── admin_setup.py      # /setup preferences + /setup panel
│   ├── panel_views.py      # Persistent 3-button panel view
│   ├── listings.py         # Create-listing wizard, check listings, /listing edit/cancel
│   ├── deals.py            # Deal creation, DM panel, dual-confirm, report
│   ├── bidding.py          # Place Bid, anti-snipe, atomic bid acceptance
│   ├── reviews.py          # Review modal, same-pair cap
│   └── moderation.py       # /moderation (guild-scoped), /owner (global, owner-only), /stats
├── utils/
│   ├── embeds.py           # Shared embed builder + invite branding line
│   ├── checks.py           # is_globally_banned, is_guild_banned, admin_only, bot_owner_only
│   ├── cache.py            # In-memory guild_config cache (invalidated on /setup writes)
│   └── pagination.py       # Reusable paginator View
└── background_tasks.py     # Deal timeout, auction end, listing expiry, archive sweeps
```

## Architecture Decisions

- **Wizard state in memory only** — Create Listing wizard holds all answers on the View object; nothing written to `listings` until final Confirm click. Prevents orphaned partial rows.
- **In-memory cooldowns** — Listing creation and bid cooldowns use discord.py's in-process buckets (not DB timestamp checks) for zero extra round-trips on every interaction.
- **Atomic race condition prevention** — Listing claim and bid acceptance both use `UPDATE … WHERE status='active' RETURNING` rather than SELECT-then-UPDATE, so two concurrent users can't both win.
- **Two entirely separate ban tiers** — Guild admins can only touch `guild_bans` (their server only); `user_profiles.is_banned` (global) is settable only by the bot owner via `await bot.is_owner()`.
- **Persistent views registered in setup_hook** — All persistent Views (panel, deal panels, listing actions) are re-registered before `on_ready` fires so buttons survive restarts.

## Product

- `MainPanelView` — 3 buttons: Create Listing, Check Listings, My IGN
- **Create Listing** — 6-step wizard (scope → category → MC server → type → format → modal)
- **Check Listings** — filter by MC server, category, buy/sell, format + keyword search; paginated 5/page
- **Deals** — DM-based panel with Mark Complete (dual-confirm), Cancel Deal, Report Issue
- **Auctions** — Place Bid with anti-snipe extension, bid increment enforcement, bid history audit trail
- **Reviews** — Mandatory post-deal review; same-pair cap after 3 reviews prevents farming
- **Moderation** — `/moderation ban_user/unban_user/lookup_user/stats` (guild admins), `/owner global_ban/global_unban/lookup` (bot owner only)
- **Background tasks** — 48h deal timeout, auction auto-resolve, listing expiry + renewal DM, weekly 90-day archive sweep

## Gotchas

- Always run `/setup preferences` in a new server before sending the panel — the bot needs a `guild_configs` row.
- `DATABASE_URL` is runtime-managed by Replit — never set it in Secrets manually.
- The `members` intent is privileged — it **must** be enabled in the Discord Developer Portal or the bot won't boot.
- After adding `DISCORD_TOKEN` and `CLIENT_ID` to Secrets, start (or restart) the "Snag Discord Bot" workflow.
