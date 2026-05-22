from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class IndustryJob(Base):
    __tablename__ = "industry_jobs"

    job_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    character_id: Mapped[int] = mapped_column(Integer, index=True)
    installer_id: Mapped[int] = mapped_column(Integer)
    facility_id: Mapped[int] = mapped_column(BigInteger)
    station_id: Mapped[int] = mapped_column(BigInteger)
    # 1=manufacturing, 3=TE research, 4=ME research, 5=copying, 8=invention, 11=reactions
    activity_id: Mapped[int] = mapped_column(Integer)
    blueprint_id: Mapped[int] = mapped_column(BigInteger)
    blueprint_type_id: Mapped[int] = mapped_column(Integer)
    blueprint_location_id: Mapped[int] = mapped_column(BigInteger)
    output_location_id: Mapped[int] = mapped_column(BigInteger)
    runs: Mapped[int] = mapped_column(Integer)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    licensed_runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    product_type_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # "active", "cancelled", "delivered", "paused", "ready", "reverted"
    status: Mapped[str] = mapped_column(String(20))
    duration: Mapped[int] = mapped_column(Integer)
    start_date: Mapped[datetime] = mapped_column(DateTime)
    end_date: Mapped[datetime] = mapped_column(DateTime)
    pause_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_character_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    successful_runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CharacterSkill(Base):
    __tablename__ = "character_skills"

    character_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    skill_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trained_level: Mapped[int] = mapped_column(Integer)
    active_level: Mapped[int] = mapped_column(Integer)
    skillpoints_in_skill: Mapped[int] = mapped_column(Integer)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class IndustryCostIndex(Base):
    """System cost indexes per activity from /industry/systems/."""

    __tablename__ = "industry_cost_indexes"

    solar_system_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # "manufacturing", "researching_time_efficiency", "researching_material_efficiency",
    # "copying", "invention", "reaction"
    activity: Mapped[str] = mapped_column(String(50), primary_key=True)
    cost_index: Mapped[float] = mapped_column(Float)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
