"""
create_tables.py

Standalone script to create any tables missing from the database
(does not alter existing tables — see Base.metadata.create_all below).
"""

from database import engine, Base
import models  # noqa: F401 — registers all tables on Base.metadata

# This will create only the NEW tables (won't touch existing ones)
Base.metadata.create_all(bind=engine)

print("✅ New tables created successfully!")