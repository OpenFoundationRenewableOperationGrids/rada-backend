"""
test_seed_reset.py

seed_batteries.py --reset empties RESET_TABLES with one TRUNCATE, without
CASCADE: PostgreSQL refuses it while a table outside the list points at one
inside. This test catches that without running the seed (about 20 s and
218,000 rows): a migration adding such a table must add it to RESET_TABLES.
"""

from sqlalchemy import text

import database
from seed_batteries import RESET_TABLES


def test_reset_tables_cover_every_table_pointing_at_demo_data():
    with database.engine.connect() as conn:
        # Foreign keys between our tables; TimescaleDB chunks (_timescaledb_internal) are left out
        references = conn.execute(text("""
            SELECT src.relname, dst.relname
            FROM pg_constraint c
            JOIN pg_class src ON src.oid = c.conrelid
            JOIN pg_class dst ON dst.oid = c.confrelid
            JOIN pg_namespace n ON n.oid = src.relnamespace
            WHERE c.contype = 'f' AND n.nspname = 'public'
        """)).all()

    missing = sorted({(table, target) for table, target in references
                      if target in RESET_TABLES and table not in RESET_TABLES})
    assert missing == [], f"Add these tables to RESET_TABLES in seed_batteries.py: {missing}"


def test_reset_keeps_the_catalogue_and_source_connection():
    assert not {"language", "metric", "metric_translation", "source_connection"} & set(RESET_TABLES)
