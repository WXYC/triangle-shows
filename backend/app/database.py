"""
SQLAlchemy async engine and session factory for the Triangle Shows database.

Role: Initialized at startup by main.py (init_db()), then provides get_session() as a
FastAPI dependency for all route handlers that need database access.
Requires: DATABASE_URL env var (async PostgreSQL URL, e.g. postgresql+asyncpg://...) via app.config.
"""

# --- Imports ---
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# --- Engine and Session Factory ---

# echo=False keeps SQL statements out of production logs; flip to True for debugging queries
engine = create_async_engine(settings.DATABASE_URL, echo=False)

# expire_on_commit=False prevents lazy-load errors after commit in async context
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# --- ORM Base ---

class Base(DeclarativeBase):
    """Shared declarative base for all ORM models (Venue, Event, ScrapeLog)."""
    pass


# --- FastAPI Dependency ---

async def get_session() -> AsyncSession:
    """Yield an async database session; used as a FastAPI dependency via Depends(get_session)."""
    async with async_session() as session:
        yield session


# --- Database Initialization ---

async def init_db():
    """Create all tables defined on Base.metadata if they don't already exist.

    Called once at application startup in main.py. Alembic handles schema migrations
    for production; this is a fallback for fresh environments and local dev.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
