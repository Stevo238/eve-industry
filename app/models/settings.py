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
    "inbound_shipping_isk_per_m3": "0",
    "outbound_shipping_isk_per_m3": "0",
    "sales_tax_pct": "2.0",
    "broker_fee_pct": "3.0",
}
