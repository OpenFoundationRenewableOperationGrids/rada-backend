from database import engine, Base
from models import Battery, StateOfCharge, GridSignal, DispatchCommand, BatteryTelemetry

# This will create only the NEW tables (won't touch existing ones)
Base.metadata.create_all(bind=engine)

print("✅ New tables created successfully!")