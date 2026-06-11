# Database migrations

`schema.sql` remains the bootstrap schema for current fresh installs and should
not be edited for routine schema evolution. Runtime migrations live in
`migrations/` and are tracked in SQLite by the `schema_migrations` table.

## Rules

1. Add one numbered SQL file per schema change:

   ```text
   migrations/0002_watchlist_events.sql
   migrations/0003_ticker_sector_updated_at.sql
   ```

2. Never edit a committed migration. Add a new migration instead.
3. Do not add explicit `BEGIN`/`COMMIT` statements inside migration files; the
   migration runner wraps each file in one transaction with its version insert.
4. Keep migrations idempotent when practical, especially baseline migrations
   that may run against already-initialized databases.
5. `db.connect()` applies any unapplied migrations in filename order.
6. Add a temp-DB test for every schema change:
   - create or open an older database shape
   - run `gexwheel.db.connect()` or `_apply_migrations()`
   - assert existing data survives
   - assert the new table/column/index exists

## Creating a migration

1. Pick the next number after the latest file in `migrations/`.
2. Write plain SQLite SQL.
3. Add tests in `tests/test_db_migrations.py`.
4. Run:

   ```bash
   PYTHONPATH=src python3 -m pytest tests/test_db_migrations.py -q
   PYTHONPATH=src python3 -m pytest
   ```
