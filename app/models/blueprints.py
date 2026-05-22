from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Blueprint(Base):
    __tablename__ = "blueprints"

    # ESI item_id for this blueprint instance
    item_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    character_id: Mapped[int] = mapped_column(Integer, index=True)
    type_id: Mapped[int] = mapped_column(Integer, index=True)
    location_id: Mapped[int] = mapped_column(BigInteger)
    location_flag: Mapped[str] = mapped_column(String(50))
    # 0-10 (ME level)
    material_efficiency: Mapped[int] = mapped_column(Integer, default=0)
    # 0-20 (TE level)
    time_efficiency: Mapped[int] = mapped_column(Integer, default=0)
    # -1 = original (BPO), >0 = runs remaining (BPC)
    runs: Mapped[int] = mapped_column(Integer, default=-1)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    @property
    def is_original(self) -> bool:
        return self.runs == -1
