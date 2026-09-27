"""
create_tables.py

Standalone script to create any tables missing from the database
(does not alter existing tables — see Base.metadata.create_all below).
"""

from database import engine, Base
from models import Asset, StateOfCharge, GridSignal, DispatchCommand, AssetType, GridConnectionStatus, AssetStatus

# This will create only the NEW tables (won't touch existing ones)
Base.metadata.create_all(bind=engine)

print("✅ New tables created successfully!")