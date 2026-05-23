from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UserSetting(Base):
    """Key-value store for user-configurable application settings."""

    __tablename__ = "user_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(500), default="")


# Default values used when a key is not present in the DB
SETTING_DEFAULTS: dict[str, str] = {
    "market_region_id": "10000002",
    "inbound_shipping_isk_per_m3": "0",
    "outbound_shipping_isk_per_m3": "0",
    "sales_tax_pct": "2.0",
    "broker_fee_pct": "3.0",
    "manufacturing_system_id": "30000142",   # Jita
    "facility_tax_pct": "0.0",
}

# Market hubs: region_id → display name
MARKET_HUBS: dict[str, str] = {
    "10000002": "Jita — The Forge",
    "10000043": "Amarr — Domain",
    "10000032": "Dodixie — Sinq Laison",
    "10000030": "Rens — Heimatar",
    "10000042": "Hek — Metropolis",
}
