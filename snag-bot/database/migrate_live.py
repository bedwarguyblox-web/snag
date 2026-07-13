"""
snag-bot/database/migrate_live.py
──────────────────────────────────
One-shot migration: brings a pre-fix live snag.db in sync with the current
models.py.  Run this ONCE against the actual live DB before (re)starting the
bot after the Round 1+2 code changes.

Drift that this script fixes
────────────────────────────
  1. user_profiles.completed_deals — column was entirely absent; adds it with
     DEFAULT 0 (existing rows get 0, which is correct — they pre-date tracking).
  2. bids.listing_id — declared BIGINT in original schema, must be INTEGER to
     match listings.listing_id FK target.  Requires full table rebuild (SQLite
     cannot ALTER a column type in place).
  3. deals.listing_id — same as above.

All other tables (guild_configs, guild_bans, listings, reviews, reports,
listings_archive, bids_archive) were confirmed IN SYNC with models.py and
are left untouched.

Usage
─────
  cd snag-bot
  python database/migrate_live.py [path/to/snag.db]

If no path is given the script resolves the DB from config.py (DATABASE_URL).
A .bak copy is made before any changes.
"""

import shutil
import sqlite3
import sys
from pathlib import Path

# ── Resolve DB path ───────────────────────────────────────────────────────────
if len(sys.argv) >= 2:
    db_path = Path(sys.argv[1])
else:
    # Fall back to DATABASE_URL from config if available
    try:
        from config import DATABASE_URL  # e.g. sqlite+aiosqlite:///./database/snag.db
        db_path = Path(DATABASE_URL.replace("sqlite+aiosqlite:///", ""))
    except Exception:
        db_path = Path(__file__).parent / "snag.db"

if not db_path.exists():
    print(f"ERROR: DB not found at {db_path}")
    sys.exit(1)

bak_path = db_path.with_suffix(".db.bak")
shutil.copy2(db_path, bak_path)
print(f"Backup written: {bak_path}")

con = sqlite3.connect(db_path)
con.execute("PRAGMA foreign_keys = OFF")

# ── Helper: get column info for a table ───────────────────────────────────────
def col_types(table: str) -> dict[str, str]:
    cur = con.execute(f"PRAGMA table_info({table})")
    return {r[1]: r[2].upper() for r in cur.fetchall()}


# ── 1. user_profiles.completed_deals ─────────────────────────────────────────
cols = col_types("user_profiles")
if "completed_deals" not in cols:
    con.execute(
        "ALTER TABLE user_profiles "
        "ADD COLUMN completed_deals INTEGER NOT NULL DEFAULT 0"
    )
    con.commit()
    print("APPLIED  user_profiles.completed_deals — added (INTEGER DEFAULT 0)")
else:
    print("SKIP     user_profiles.completed_deals — already present")


# ── 2. bids.listing_id: BIGINT → INTEGER ─────────────────────────────────────
cols = col_types("bids")
if cols.get("listing_id", "") != "INTEGER":
    con.executescript("""
        CREATE TABLE bids_migration_new (
            bid_id     INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            listing_id INTEGER REFERENCES listings(listing_id),
            bidder_id  BIGINT,
            amount     NUMERIC(12,2),
            created_at DATETIME
        );
        INSERT INTO bids_migration_new
            SELECT bid_id, listing_id, bidder_id, amount, created_at FROM bids;
        DROP TABLE bids;
        ALTER TABLE bids_migration_new RENAME TO bids;
        CREATE INDEX ix_bids_listing_id ON bids(listing_id);
    """)
    con.commit()
    print("APPLIED  bids.listing_id — rebuilt table, BIGINT → INTEGER")
else:
    print("SKIP     bids.listing_id — already INTEGER")


# ── 3. deals.listing_id: BIGINT → INTEGER ────────────────────────────────────
cols = col_types("deals")
if cols.get("listing_id", "") != "INTEGER":
    con.executescript("""
        CREATE TABLE deals_migration_new (
            deal_id                 INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            listing_id              INTEGER REFERENCES listings(listing_id),
            initiator_id            BIGINT,
            seller_id               BIGINT,
            status                  VARCHAR(15),
            initiator_confirmed     BOOLEAN,
            seller_confirmed        BOOLEAN,
            dm_message_id_initiator BIGINT,
            dm_message_id_seller    BIGINT,
            last_activity_at        DATETIME,
            created_at              DATETIME,
            ended_at                DATETIME,
            end_reason              VARCHAR(25),
            CONSTRAINT ck_deal_status
                CHECK (status IN ('active','completed','cancelled','expired'))
        );
        INSERT INTO deals_migration_new
            SELECT deal_id, listing_id, initiator_id, seller_id, status,
                   initiator_confirmed, seller_confirmed,
                   dm_message_id_initiator, dm_message_id_seller,
                   last_activity_at, created_at, ended_at, end_reason
            FROM deals;
        DROP TABLE deals;
        ALTER TABLE deals_migration_new RENAME TO deals;
        CREATE INDEX ix_deals_status_last_activity ON deals(status, last_activity_at);
    """)
    con.commit()
    print("APPLIED  deals.listing_id — rebuilt table, BIGINT → INTEGER")
else:
    print("SKIP     deals.listing_id — already INTEGER")


# ── 4. Verify ─────────────────────────────────────────────────────────────────
print("\n── Post-migration verification ──────────────────────────────────────")
checks = [
    ("user_profiles", "completed_deals", "INTEGER"),
    ("bids",          "listing_id",      "INTEGER"),
    ("deals",         "listing_id",      "INTEGER"),
]
all_ok = True
for table, col, want in checks:
    got = col_types(table).get(col, "MISSING")
    ok = got == want
    all_ok = all_ok and ok
    print(f"  {'PASS' if ok else 'FAIL'}  {table}.{col}: {got}")

con.execute("PRAGMA foreign_keys = ON")
con.close()

if all_ok:
    print("\nMigration complete — DB is now in sync with models.py.")
    print(f"Backup retained at: {bak_path}")
else:
    print("\nERROR: one or more columns did not migrate correctly.")
    print(f"Original DB preserved at: {bak_path}")
    sys.exit(1)
