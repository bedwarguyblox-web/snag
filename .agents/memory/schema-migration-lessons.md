---
name: Schema migration lessons
description: Recurring failure modes — create_all() silent no-ops, untraced write paths, code/DB drift, and the startup-migration pattern for simple columns.
---

# Schema migration lessons

## create_all() does NOT migrate existing tables

`Base.metadata.create_all()` only creates tables that don't exist yet. It will silently skip a table that exists even if columns were added, types changed, or FKs corrected in models.py. This is what shipped the BigInteger PK bug and would have swallowed the `completed_deals` column addition if the DB had existed.

**Why:** SQLAlchemy's `create_all` is designed to be idempotent against existing schemas, not to evolve them.

**How to apply:** Any schema change (new column, type fix, FK fix) on a live database needs an explicit migration step — either drop+recreate (dev only) or ALTER TABLE. For production: use the startup migration pattern below, or Alembic.

## Startup migration pattern for simple columns

For single boolean/integer/text columns with simple defaults, use idempotent `ALTER TABLE` in `create_all_tables()` in `database/engine.py`:

```python
_migrations = [
    ("col_name", "ALTER TABLE table ADD COLUMN col_name TYPE NOT NULL DEFAULT x"),
]
for col_name, ddl in _migrations:
    try:
        async with engine.begin() as mig_conn:
            await mig_conn.exec_driver_sql(ddl)
    except OperationalError as e:
        if "duplicate column" in str(e).lower():
            pass  # Already present — idempotent
        else:
            logger.warning(...)
```

This runs on every boot but is safe (duplicate column is caught). No separate migration script needed.

**Why:** A separate migration script (`database/migrate_live.py`) requires the operator to remember to run it before restarting. The startup pattern eliminates that human step for simple additions.

**How to apply:** Use startup migrations for new boolean/integer/text columns with simple defaults. Use a proper migration script for: type changes, dropping columns, renaming columns, adding FK constraints, or anything requiring a data backfill.

## Every model field needs a confirmed write path before it's trusted

`_completed_deals` (read in embeds.py, never written) and `global_feed_channel_id` (read in listings.py fallback, never set by any command) are the same bug shape: code reads a value, looks implemented, but nothing ever writes it. The Trusted Seller badge was silently broken — always 0 — because of this.

**Why:** The read site is locally correct code. You only discover the bug by tracing backward to every write site. `getattr(obj, "_field", 0)` is especially dangerous — it hides the missing write by returning 0 silently.

**How to apply:** When finishing a feature that reads a model field or config value, grep for every place that *sets* it. If the answer is "nowhere," either wire it up now or leave a `# TODO: no write path yet` comment. Prefer direct attribute access (`obj.field`) over `getattr(..., default)` so a missing column fails loudly.

## Schema change shipped in code without migrating the live DB (recurring)

This caused the `sqlite3.OperationalError: no such column: user_profiles.completed_deals` live crash. In both cases, models.py was updated correctly, but the live snag.db was not migrated — so the code expected columns that did not exist.

**Why:** No Alembic, no migration runner, no startup schema check by default. `create_all()` silently no-ops on existing tables.

**How to apply:** Any time models.py is changed, this checklist must run before deploying:
1. If simple column: add to the startup migration list in `database/engine.py`.
2. If complex change: write a migration script, back up the live DB first, run and verify.
3. Only then restart the bot.

Always ask: *what ALTER TABLE statement does this require on the live DB, and has it been run?*
