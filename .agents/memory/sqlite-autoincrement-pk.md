---
name: SQLite autoincrement primary key type
description: Why an autoincrementing SQLAlchemy primary key silently fails to get a value on SQLite, and the fix.
---

When a SQLAlchemy model's primary key column is declared as `BigInteger` (or any
type SQLite doesn't compile to the literal string `INTEGER`), SQLite does not
treat it as a rowid alias — so it never auto-assigns a value on INSERT. The
insert sends `NULL` for that column and fails with a `NOT NULL constraint
failed` IntegrityError, even though `autoincrement=True` was set.

**Why:** SQLite's autoincrement/rowid-alias behavior only kicks in for a
column whose declared type is exactly `INTEGER PRIMARY KEY`. `BigInteger`
compiles to `BIGINT` on SQLite, which doesn't qualify — this is silent at the
ORM layer (no warning at model-definition time), so it only surfaces the
first time a row is actually inserted, which can look like "the feature does
nothing" (e.g. a bot's confirm button silently fails because a deferred
interaction never gets a followup after the insert throws).

**How to apply:** For any project using SQLite via SQLAlchemy, autoincrementing
primary keys must use plain `Integer`, not `BigInteger`. Foreign keys and
non-PK columns that store large values (e.g. Discord snowflake IDs) can still
use `BigInteger` — only the autoincrement PK itself needs to be `Integer`.
Postgres/MySQL don't have this quirk (BigInteger autoincrement PKs work fine
there), so this only matters for SQLite-backed projects.
