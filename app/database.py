"""Database engine and session management.

This module configures SQLAlchemy using values from the application settings.  A
global engine and session factory are created on import.  Models defined in
`models.py` should import and use the `Base` defined here.
"""

from sqlalchemy import create_engine
from app.settings import get_settings
from sqlalchemy.orm import sessionmaker, Session, declarative_base

from .settings import get_settings


# Obtain settings on import.  Using a function avoids reading environment
# variables until this module is first imported.
settings = get_settings()


# Create the SQLAlchemy engine.  `pool_pre_ping` helps avoid stale connections.
engine = create_engine(settings.database_url, pool_pre_ping=True)

# Session factory bound to the engine.  We disable autocommit and autoflush so
# that changes are only persisted when explicitly committed.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for declarative models.
Base = declarative_base()


def get_db() -> Session:
    """Yield a SQLAlchemy session and ensure it is closed when done."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_all_tables() -> None:
    """Create all database tables.

    This function should be called at application start.  It uses SQLAlchemy’s
    metadata to create missing tables without dropping existing ones.
    """
    Base.metadata.create_all(bind=engine)