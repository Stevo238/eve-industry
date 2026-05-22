from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Location(Base):
    """Cached location names for stations, structures, and solar systems."""

    __tablename__ = "locations"

    location_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(500))
    # "station" | "structure" | "solar_system" | "unknown"
    location_type: Mapped[str] = mapped_column(String(20))
    solar_system_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
