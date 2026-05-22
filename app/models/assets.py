from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Asset(Base):
    __tablename__ = "assets"

    # ESI item_id (unique per item instance in EVE)
    item_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    character_id: Mapped[int] = mapped_column(Integer, index=True)
    type_id: Mapped[int] = mapped_column(Integer, index=True)
    location_id: Mapped[int] = mapped_column(BigInteger, index=True)
    # "station", "solar_system", "item", "other"
    location_type: Mapped[str] = mapped_column(String(20))
    # "Hangar", "Cargo", "CorpSAG1", etc.
    location_flag: Mapped[str] = mapped_column(String(50))
    quantity: Mapped[int] = mapped_column(Integer)
    is_singleton: Mapped[bool] = mapped_column(Boolean, default=False)
    # True = BPC, False = BPO, None = not a blueprint
    is_blueprint_copy: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
