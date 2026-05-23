from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    # Import all models so they register with Base.metadata
    from app.models import assets, blueprints, character, industry, location, market, sde, settings, structures  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Add columns introduced after initial schema — safe to run every startup
        for sql in [
            "ALTER TABLE market_prices ADD COLUMN buy_price REAL",
            "ALTER TABLE market_prices ADD COLUMN sell_price REAL",
            "ALTER TABLE locations ADD COLUMN solar_system_id INTEGER",
            "ALTER TABLE locations ADD COLUMN type_id INTEGER",
        ]:
            try:
                await conn.execute(__import__("sqlalchemy").text(sql))
            except Exception:
                pass  # column already exists
