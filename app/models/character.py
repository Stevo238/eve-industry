from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Character(Base):
    __tablename__ = "characters"

    character_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    corporation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    corporation_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    alliance_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    alliance_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    owner_hash: Mapped[str] = mapped_column(String(200))
    wallet_balance: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_synced: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OAuthToken(Base):
    __tablename__ = "oauth_tokens"

    character_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    access_token: Mapped[str] = mapped_column(String(2000))
    refresh_token: Mapped[str] = mapped_column(String(2000))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    scopes: Mapped[str] = mapped_column(String(1000))
