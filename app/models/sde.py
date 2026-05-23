from sqlalchemy import Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SdeType(Base):
    """Items/types from typeIDs.yaml."""

    __tablename__ = "sde_types"

    type_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    group_id: Mapped[int] = mapped_column(Integer, index=True)
    category_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    market_group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mass: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    packaged_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    portion_size: Mapped[int] = mapped_column(Integer, default=1)
    published: Mapped[int] = mapped_column(Integer, default=1)


class SdeGroup(Base):
    """Item groups from groupIDs.yaml."""

    __tablename__ = "sde_groups"

    group_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    category_id: Mapped[int] = mapped_column(Integer, index=True)
    published: Mapped[int] = mapped_column(Integer, default=1)


class SdeCategory(Base):
    """Item categories from categoryIDs.yaml."""

    __tablename__ = "sde_categories"

    category_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    published: Mapped[int] = mapped_column(Integer, default=1)


class SdeMarketGroup(Base):
    """Market groups from marketGroups.yaml."""

    __tablename__ = "sde_market_groups"

    market_group_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    parent_group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class SdeBlueprintActivity(Base):
    """Blueprint activities with time from blueprints.yaml."""

    __tablename__ = "sde_blueprint_activities"

    blueprint_type_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 1=manufacturing, 3=TE research, 4=ME research, 5=copying, 8=invention, 11=reactions
    activity_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time: Mapped[int] = mapped_column(Integer)
    max_production_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SdeBlueprintMaterial(Base):
    """Materials required by blueprint activities from blueprints.yaml."""

    __tablename__ = "sde_blueprint_materials"

    blueprint_type_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    activity_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    material_type_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    quantity: Mapped[int] = mapped_column(Integer)


class SdeBlueprintProduct(Base):
    """Products produced by blueprint activities from blueprints.yaml."""

    __tablename__ = "sde_blueprint_products"

    blueprint_type_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    activity_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_type_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    probability: Mapped[float | None] = mapped_column(Float, nullable=True)


class SdeSolarSystem(Base):
    """Solar system names — populated when syncing industry cost indexes."""

    __tablename__ = "sde_solar_systems"

    solar_system_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), index=True)


class SdeBlueprintSkill(Base):
    """Skills required by blueprint activities from blueprints.yaml."""

    __tablename__ = "sde_blueprint_skills"

    blueprint_type_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    activity_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    skill_type_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    level: Mapped[int] = mapped_column(Integer)
