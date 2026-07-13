"""
Snag Bot — global constants and configuration.
All abuse/performance limits live here so they can be tuned without hunting through cogs.
"""

import os

# ─── Bot identity ────────────────────────────────────────────────────────────
BOT_NAME = "Snag"
BOT_OWNER_ID: int = 1454352231494189121   # Only this user can run /owner commands
BOT_VERSION = "1.0.0"
DEFAULT_EMBED_COLOR = 0x5865F2   # Discord blurple

# Build invite URL from CLIENT_ID env var (never hardcode elsewhere)
_CLIENT_ID = os.getenv("CLIENT_ID", "1525818030066761868")
# Perms: Send Messages | Embed Links | Read Message History |
#         Use Slash Commands | Manage Messages
# NOTE: Attach Files is intentionally excluded — listings never need the bot
# to upload files, and granting it just adds an unused attack surface.
_PERMISSIONS_INT = 275414993920
INVITE_URL = (
    f"https://discord.com/api/oauth2/authorize"
    f"?client_id={_CLIENT_ID}&permissions={_PERMISSIONS_INT}&scope=bot%20applications.commands"
)

# ─── Preset global listing categories ────────────────────────────────────────
GLOBAL_CATEGORIES = [
    "Bases",
    "Items",
    "Service",
    "Ranks/Currency",
    "Builds",
    "Farms",
    "Other",
]

# ─── Preset Minecraft server filter options ───────────────────────────────────
PRESET_MC_SERVERS = [
    "Donut SMP",
    "Hypixel",
    "2b2t",
    "Lifesteal SMP",
    "Dream SMP",
    "Hermitcraft",
    "MCC Island",
    "Any",
]

# ─── Abuse / rate-limit constants ─────────────────────────────────────────────
# Listing creation: one per cooldown window per user (in-memory bucket)
LISTING_CREATE_COOLDOWN_SECONDS: int = 300        # 5 minutes

# How many active listings a single user may have at once
MAX_ACTIVE_LISTINGS_PER_USER: int = 10

# Listing content limits (also enforced at Modal level)
LISTING_DESCRIPTION_MAX_LEN: int = 1000
REVIEW_COMMENT_MAX_LEN: int = 500

# Auction bidding
MIN_BID_INCREMENT_PERCENT: float = 5.0            # bid must exceed current by ≥5 %
MIN_BID_INCREMENT_ABSOLUTE: float = 1.0           # …but at least 1 unit
BID_COOLDOWN_SECONDS: int = 10                    # per-user, per-listing, in-memory
MAX_BIDS_PER_LISTING: int = 200                   # cap bid rows per auction

# Anti-snipe
ANTI_SNIPE_WINDOW_SECONDS: int = 120              # bid lands within 2 min of end → extend
ANTI_SNIPE_EXTENSION_SECONDS: int = 120           # extend by 2 min
MAX_AUCTION_EXTENSIONS: int = 5                   # hard cap on extensions

# Deal system
DEAL_TIMEOUT_HOURS: int = 48                      # auto-expire inactive deals after 48h
DEAL_TIMEOUT_SWEEP_MINUTES: int = 15              # how often the sweep job runs

# Listing expiry
LISTING_EXPIRY_DAYS: int = 14                     # default listing lifetime
LISTING_EXPIRY_RENEW_NOTIFY_HOURS_BEFORE: int = 24

# Archive sweep
ARCHIVE_AFTER_DAYS: int = 90                      # move terminal listings to archive after 90 d
ARCHIVE_SWEEP_HOURS: int = 168                    # weekly (7 * 24)

# Archive retention — irreversible hard-delete of old archive rows
ARCHIVE_PURGE_AFTER_DAYS: int = 730              # permanently delete archive rows after 2 years
VACUUM_SWEEP_HOURS: int = 720                     # reclaim disk space monthly (30 * 24)

# Listings query hard cap before pagination
LISTINGS_QUERY_HARD_LIMIT: int = 200
LISTINGS_PER_PAGE: int = 5

# Global listing broadcast delay between guilds (seconds) to avoid rate-limit bursts
GLOBAL_BROADCAST_DELAY: float = 0.5

# Same-pair review cap: reviews after this many between the same two users don't count
SAME_PAIR_REVIEW_CAP: int = 3

# Trusted-seller badge thresholds (computed at render time, not stored)
TRUSTED_BADGE_MIN_COMPLETED_DEALS: int = 10
TRUSTED_BADGE_MIN_AVG_RATING: float = 4.5

# Duplicate listing detection window (seconds)
DUPLICATE_LISTING_WINDOW_SECONDS: int = 600      # 10 minutes
