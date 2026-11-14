"""
database.py

Database engine and session setup — reads DATABASE_URL from .env,
configures the SQLAlchemy engine/session for a PostgreSQL + TimescaleDB
backend, and forces the connection timezone to UTC.
"""

import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker, declarative_base

load_dotenv()

# Always use the psycopg (version 3) driver. A plain postgresql:// URL means
# psycopg2 in SQLAlchemy 2.0 but psycopg in 2.1, so the driver is set here and
# the DATABASE_URL in every environment file can stay as it is.
SQLALCHEMY_DATABASE_URL = make_url(os.getenv("DATABASE_URL"))
if SQLALCHEMY_DATABASE_URL.drivername == "postgresql":
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.set(drivername="postgresql+psycopg")


engine = create_engine(SQLALCHEMY_DATABASE_URL)

@event.listens_for(engine, "connect")
def set_timezone(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("SET TIME ZONE 'UTC'")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
