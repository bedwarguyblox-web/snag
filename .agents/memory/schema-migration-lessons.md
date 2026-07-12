---
name: Schema migration lessons
description: Two durable patterns that caused bugs in this project — create_all() silent no-ops and untraced write paths for model fields.
---

# Schema migration lessons

## create_all() does NOT migrate existing tables

`Base.metadata.create_all()` only creates tables that don't exist yet. It will silently skip a table that exists even if columns were added, types changed, or FKs corrected in models.py. This is what shipped the BigInteger PK bug and would have swallowed the `completed_deals` column addition if the DB had existed.

**Why:** SQLAlchemy's `create_all` is designed to be idempotent against existing schemas, not to evolve them. "Safe on every startup" is accurate but misleading — it does nothing when a change actually needs to land.

**How to apply:** Any schema change (new column, type fix, FK fix) on a live database needs an explicit migration step — either drop+recreate (dev only, after confirming no real data) or a proper ALTER TABLE / migration script. For production: adopt Alembic. At minimum, add a schema version check at startup using `PRAGMA table_info`.

## Every model field needs a confirmed write path before it's trusted

`_completed_deals` (read in embeds.py, never written) and `global_feed_channel_id` (read in listings.py fallback, never set by any command) are the same bug shape: code reads a value, looks implemented, but nothing ever writes it. The Trusted Seller badge was silently broken — always 0 — because of this.

**Why:** The read site is locally correct code. You only discover the bug by tracing backward to every write site. `getattr(obj, "_field", 0)` is especially dangerous — it hides the missing write by returning 0 silently.

**How to apply:** When finishing a feature that reads a model field or config value, grep for every place that *sets* it. If the answer is "nowhere," either wire it up now or leave a `# TODO: no write path yet` comment. Prefer direct attribute access (`obj.field`) over `getattr(..., default)` so a missing column fails loudly.
