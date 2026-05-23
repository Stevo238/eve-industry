from sqlalchemy import BigInteger, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


# Engineering Complex type_id → manufacturing cost-index role bonus %
# Refineries (Athanor/Tatara) have 0% manufacturing role bonus — they are
# included in the name map for display but not in the bonus map.
STRUCTURE_ROLE_BONUSES: dict[int, float] = {
    35825: 15.0,  # Raitaru
    35826: 15.0,  # Azbel
    35827: 20.0,  # Sotiyo
}

STRUCTURE_TYPE_NAMES: dict[int, str] = {
    35825: "Raitaru",
    35826: "Azbel",
    35827: "Sotiyo",
    35835: "Athanor",
    35836: "Tatara",
}


def role_bonus_from_type_id(type_id: int | None) -> float:
    """Return the manufacturing cost-index role bonus for a structure type_id, or 0.0."""
    if type_id is None:
        return 0.0
    return STRUCTURE_ROLE_BONUSES.get(type_id, 0.0)


class ManufacturingStructure(Base):
    __tablename__ = "manufacturing_structures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(200))
    structure_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    me_rig_bonus_pct: Mapped[float] = mapped_column(Float, default=0.0)
