from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MarketOrder(Base):
    __tablename__ = "market_orders"

    order_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # NULL = regional order snapshot, set = character's own order
    character_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    region_id: Mapped[int] = mapped_column(Integer, index=True)
    type_id: Mapped[int] = mapped_column(Integer, index=True)
    location_id: Mapped[int] = mapped_column(BigInteger)
    price: Mapped[float] = mapped_column(Float)
    volume_remain: Mapped[int] = mapped_column(Integer)
    volume_total: Mapped[int] = mapped_column(Integer)
    is_buy_order: Mapped[bool] = mapped_column(Boolean)
    duration: Mapped[int] = mapped_column(Integer)
    issued: Mapped[datetime] = mapped_column(DateTime)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MarketPrice(Base):
    """
    Prices per type_id.

    adjusted_price / average_price: from /markets/prices/ (CCP global averages, used in
      industry job cost formula).
    buy_price:  best buy-order price in Jita (what you can sell for immediately).
    sell_price: best sell-order price in Jita (what you'd pay to buy immediately).
    """

    __tablename__ = "market_prices"

    type_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    adjusted_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    buy_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    sell_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CharacterOrder(Base):
    """Character market orders from /characters/{id}/orders/."""

    __tablename__ = "character_orders"

    order_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    character_id: Mapped[int] = mapped_column(Integer, index=True)
    type_id: Mapped[int] = mapped_column(Integer, index=True)
    location_id: Mapped[int] = mapped_column(BigInteger)
    region_id: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float)
    volume_remain: Mapped[int] = mapped_column(Integer)
    volume_total: Mapped[int] = mapped_column(Integer)
    is_buy_order: Mapped[bool] = mapped_column(Boolean)
    duration: Mapped[int] = mapped_column(Integer)
    issued: Mapped[datetime] = mapped_column(DateTime)
    escrow: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_corporation: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[str] = mapped_column(String(20))
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
