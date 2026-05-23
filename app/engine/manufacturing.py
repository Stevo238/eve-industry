"""
Core manufacturing calculation engine.

Material quantity formula (per CCP):
  qty_per_run = max(1, ceil(base_qty * (1 - blueprint_ME/100) * structure_me_multiplier))
  total_qty = qty_per_run * runs

Profitability:
  - Owned materials are priced at best Jita BUY order (opportunity cost).
  - Missing materials are priced at best Jita SELL order (acquisition cost).
  - Comparison: net profit from manufacturing vs immediate sell of raw materials.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assets import Asset
from app.models.blueprints import Blueprint
from app.models.industry import CharacterSkill, IndustryCostIndex
from app.models.market import MarketPrice
from app.models.sde import (
    SdeBlueprintActivity,
    SdeBlueprintMaterial,
    SdeBlueprintProduct,
    SdeBlueprintSkill,
    SdeType,
)

ACTIVITY_MANUFACTURING = 1
ACTIVITY_REACTIONS = 11
SUPPORTED_ACTIVITIES = [ACTIVITY_MANUFACTURING, ACTIVITY_REACTIONS]
ACTIVITY_LABEL = {ACTIVITY_MANUFACTURING: "Manufacturing", ACTIVITY_REACTIONS: "Reaction"}


@dataclass
class MaterialRequirement:
    type_id: int
    name: str
    quantity_needed: int
    quantity_available: int  # from inventory
    shortage: int            # quantity_needed - quantity_available (0 if satisfied)
    volume_each: float       # m³ per unit
    buy_price: float         # Jita best buy order (opportunity cost)
    sell_price: float        # Jita best sell order (acquisition cost)

    @property
    def is_satisfied(self) -> bool:
        return self.shortage <= 0

    @property
    def cost_owned(self) -> float:
        """Opportunity cost of the owned portion (buy order price)."""
        owned = min(self.quantity_needed, self.quantity_available)
        return owned * self.buy_price

    @property
    def cost_to_buy(self) -> float:
        """Acquisition cost of the missing portion (sell order price)."""
        return self.shortage * self.sell_price

    @property
    def sell_value(self) -> float:
        """Value of selling ALL needed quantity to buy orders (sell-materials scenario)."""
        return self.quantity_needed * self.buy_price

    @property
    def total_volume(self) -> float:
        """Total volume of all units needed."""
        return self.volume_each * self.quantity_needed

    @property
    def inbound_volume(self) -> float:
        """Volume that must be shipped IN — only the units you don't already own."""
        return self.volume_each * self.shortage


@dataclass
class ManufacturingOption:
    blueprint_item_id: int
    blueprint_type_id: int
    blueprint_name: str
    product_type_id: int
    product_name: str
    character_id: int
    runs: int
    qty_produced: int          # units produced per run × runs
    blueprint_me: int
    blueprint_te: int
    is_bpo: bool
    activity_label: str = "Manufacturing"
    materials: list[MaterialRequirement] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    can_build_now: bool = False
    time_seconds: int = 0

    # ── Product pricing ──────────────────────────────────────────────────────
    product_sell_price: float = 0.0   # best Jita sell order (list-on-market value)
    product_buy_price: float = 0.0    # best Jita buy order  (immediate-sale value)
    product_volume: float = 0.0       # m³ per unit of product

    # ── Cost breakdown ───────────────────────────────────────────────────────
    # Material costs
    material_cost_owned: float = 0.0   # opportunity cost of owned materials
    material_cost_to_buy: float = 0.0  # acquisition cost of missing materials
    material_volume: float = 0.0         # total m³ of all materials needed
    inbound_volume: float = 0.0          # m³ that must be shipped in (shortage only)

    # Logistics
    inbound_shipping_cost: float = 0.0   # inbound_volume × inbound ISK/m³
    outbound_shipping_cost: float = 0.0  # product volume × outbound ISK/m³

    # EVE market fees (applied when selling product via market order)
    manufacturing_fee: float = 0.0
    sales_tax: float = 0.0
    broker_fee: float = 0.0

    # ── Summary totals ───────────────────────────────────────────────────────
    @property
    def total_material_cost(self) -> float:
        """Full material cost: opportunity cost of owned + acquisition of missing."""
        return self.material_cost_owned + self.material_cost_to_buy

    @property
    def total_cost(self) -> float:
        # broker_fee is always 0 for instant-sale-to-buy-order transactions
        return (
            self.total_material_cost
            + self.inbound_shipping_cost
            + self.outbound_shipping_cost
            + self.manufacturing_fee
            + self.sales_tax
        )

    @property
    def gross_revenue(self) -> float:
        """
        Revenue from selling finished product to buy orders immediately.
        Uses buy_price — the highest standing buy order in Jita.
        """
        return self.product_buy_price * self.qty_produced

    @property
    def net_profit_manufacture(self) -> float:
        """
        Net profit: sell product to buy orders, having bought materials from
        sell orders (or valued owned materials at buy-order opportunity cost).
        Broker fee is 0 — no sell order is placed.
        """
        return self.gross_revenue - self.total_cost

    @property
    def sell_materials_value(self) -> float:
        """ISK received by selling ALL input materials to buy orders right now."""
        return sum(m.sell_value for m in self.materials)

    @property
    def sell_materials_net(self) -> float:
        """sell_materials_value after sales tax (tax applies to all transactions)."""
        return self.sell_materials_value * (1.0 - self._sales_tax_rate)

    @property
    def _sales_tax_rate(self) -> float:
        """Derive rate from the stored sales_tax amount and gross revenue, fallback 0."""
        if self.gross_revenue > 0:
            return self.sales_tax / self.gross_revenue
        return 0.0

    @property
    def profit_delta(self) -> float:
        """
        How much MORE you make by manufacturing vs just selling the raw materials.
        Positive = manufacturing is better.
        """
        return self.net_profit_manufacture - self.sell_materials_net

    @property
    def recommendation(self) -> str:
        if not self.product_buy_price:
            return "no_price"
        if self.profit_delta > 0:
            return "manufacture"
        return "sell_materials"

    # Legacy alias used in old template code
    @property
    def estimated_material_cost(self) -> float:
        return self.total_material_cost

    @property
    def estimated_job_cost(self) -> float:
        return self.manufacturing_fee


def calc_material_quantity(
    base_quantity: int,
    runs: int,
    blueprint_me: int,
    structure_me_bonus: float = 0.0,
) -> int:
    me_reduction = blueprint_me / 100.0 + structure_me_bonus
    qty_per_run = max(1, math.ceil(base_quantity * (1.0 - me_reduction)))
    return qty_per_run * runs


def calc_time_seconds(
    base_time: int,
    runs: int,
    blueprint_te: int,
    structure_time_bonus: float = 0.0,
) -> int:
    te_reduction = blueprint_te / 100.0 + structure_time_bonus
    time_per_run = max(1, math.ceil(base_time * (1.0 - te_reduction)))
    return time_per_run * runs


async def get_asset_inventory(
    db: AsyncSession,
    character_ids: list[int] | None = None,
) -> dict[int, int]:
    """Returns {type_id: total_quantity} across selected characters."""
    stmt = select(Asset.type_id, Asset.quantity)
    if character_ids:
        stmt = stmt.where(Asset.character_id.in_(character_ids))
    result = await db.execute(stmt)
    inventory: dict[int, int] = {}
    for type_id, qty in result.fetchall():
        inventory[type_id] = inventory.get(type_id, 0) + qty
    return inventory


async def get_character_skills(
    db: AsyncSession, character_id: int
) -> dict[int, int]:
    result = await db.execute(
        select(CharacterSkill.skill_id, CharacterSkill.active_level).where(
            CharacterSkill.character_id == character_id
        )
    )
    return {row.skill_id: row.active_level for row in result.fetchall()}


async def analyse_blueprint(
    db: AsyncSession,
    blueprint: Blueprint,
    runs: int = 1,
    character_ids: list[int] | None = None,
    structure_me_bonus: float = 0.0,
    structure_time_bonus: float = 0.0,
    inbound_isk_per_m3: float = 0.0,
    outbound_isk_per_m3: float = 0.0,
    sales_tax_pct: float = 2.0,
    broker_fee_pct: float = 3.0,
    system_cost_index: float = 0.0,
    structure_role_bonus_pct: float = 0.0,
    facility_tax_pct: float = 0.0,
) -> "ManufacturingOption | None":
    # Clamp runs to BPC limit
    if not blueprint.is_original and runs > blueprint.runs:
        runs = blueprint.runs

    # Find the first supported activity this blueprint has (manufacturing, then reactions)
    activity = None
    activity_id = ACTIVITY_MANUFACTURING
    for act_id in SUPPORTED_ACTIVITIES:
        act_result = await db.execute(
            select(SdeBlueprintActivity).where(
                SdeBlueprintActivity.blueprint_type_id == blueprint.type_id,
                SdeBlueprintActivity.activity_id == act_id,
            )
        )
        activity = act_result.scalar_one_or_none()
        if activity:
            activity_id = act_id
            break
    if activity is None:
        return None

    prod_result = await db.execute(
        select(SdeBlueprintProduct).where(
            SdeBlueprintProduct.blueprint_type_id == blueprint.type_id,
            SdeBlueprintProduct.activity_id == activity_id,
        )
    )
    product_row = prod_result.scalar_one_or_none()
    if product_row is None:
        return None

    # Resolve names & volumes
    bp_type = await db.get(SdeType, blueprint.type_id)
    prod_type = await db.get(SdeType, product_row.product_type_id)
    bp_name = bp_type.name if bp_type else f"Type {blueprint.type_id}"
    prod_name = prod_type.name if prod_type else f"Type {product_row.product_type_id}"
    prod_volume = (prod_type.volume or 0.0) if prod_type else 0.0
    qty_produced = (product_row.quantity or 1) * runs

    # Market prices (pre-loaded map passed in is faster, but per-call is fine here)
    price_result = await db.execute(
        select(
            MarketPrice.type_id,
            MarketPrice.adjusted_price,
            MarketPrice.buy_price,
            MarketPrice.sell_price,
        )
    )
    prices: dict[int, dict] = {}
    for row in price_result.fetchall():
        prices[row.type_id] = {
            "adjusted": row.adjusted_price or 0.0,
            "buy": row.buy_price or 0.0,
            "sell": row.sell_price or 0.0,
        }

    # Product market price
    prod_prices = prices.get(product_row.product_type_id, {})
    product_sell_price = prod_prices.get("sell", 0.0)
    product_buy_price = prod_prices.get("buy", 0.0)

    # Fetch SDE materials for this activity
    mat_result = await db.execute(
        select(SdeBlueprintMaterial).where(
            SdeBlueprintMaterial.blueprint_type_id == blueprint.type_id,
            SdeBlueprintMaterial.activity_id == activity_id,
        )
    )
    sde_materials = mat_result.scalars().all()

    inventory = await get_asset_inventory(db, character_ids)

    # Build material requirements
    materials: list[MaterialRequirement] = []
    material_cost_owned = 0.0
    material_cost_to_buy = 0.0
    material_volume_total = 0.0
    manufacturing_fee = 0.0

    for mat in sde_materials:
        needed = calc_material_quantity(
            mat.quantity, runs, blueprint.material_efficiency, structure_me_bonus
        )
        available = inventory.get(mat.material_type_id, 0)
        shortage = max(0, needed - available)

        mat_type = await db.get(SdeType, mat.material_type_id)
        mat_name = mat_type.name if mat_type else f"Type {mat.material_type_id}"
        mat_vol = (mat_type.volume or 0.0) if mat_type else 0.0

        mat_prices = prices.get(mat.material_type_id, {})
        buy_p = mat_prices.get("buy", 0.0)
        sell_p = mat_prices.get("sell", 0.0)
        adj_p = mat_prices.get("adjusted", 0.0)

        req = MaterialRequirement(
            type_id=mat.material_type_id,
            name=mat_name,
            quantity_needed=needed,
            quantity_available=available,
            shortage=shortage,
            volume_each=mat_vol,
            buy_price=buy_p,
            sell_price=sell_p,
        )
        materials.append(req)
        material_cost_owned += req.cost_owned
        material_cost_to_buy += req.cost_to_buy
        material_volume_total += req.total_volume

        # Industry job cost uses adjusted_price (CCP formula)
        manufacturing_fee += adj_p * needed

    # Skills
    skill_result = await db.execute(
        select(SdeBlueprintSkill).where(
            SdeBlueprintSkill.blueprint_type_id == blueprint.type_id,
            SdeBlueprintSkill.activity_id == activity_id,
        )
    )
    required_skills = skill_result.scalars().all()
    char_skills = await get_character_skills(db, blueprint.character_id)
    missing_skills: list[str] = []
    for req in required_skills:
        actual = char_skills.get(req.skill_type_id, 0)
        if actual < req.level:
            skill_type = await db.get(SdeType, req.skill_type_id)
            skill_name = skill_type.name if skill_type else f"Skill {req.skill_type_id}"
            missing_skills.append(f"{skill_name} {req.level} (have {actual})")

    can_build_now = all(m.is_satisfied for m in materials) and not missing_skills
    time_total = calc_time_seconds(
        activity.time, runs, blueprint.time_efficiency, structure_time_bonus
    )

    # Logistics costs
    # Inbound: only materials you need to BUY and ship in (shortage volume).
    # Materials you already own are at your production location — no shipping needed.
    inbound_volume = sum(m.inbound_volume for m in materials)
    inbound_shipping = inbound_volume * inbound_isk_per_m3

    # Outbound: finished goods shipped to market.
    outbound_volume = prod_volume * qty_produced
    outbound_shipping = outbound_volume * outbound_isk_per_m3

    # Sales tax applies when selling to buy orders (instant sale, no listing).
    # Broker fee is 0 — we are NOT placing a sell order.
    gross = product_buy_price * qty_produced
    sales_tax = gross * (sales_tax_pct / 100.0)
    broker_fee = 0.0  # no order listing

    # Industry job cost (full EVE formula):
    #   Job Gross Cost = EIV × cost_index × (1 − structure_role_bonus)
    #   SCC Surcharge  = EIV × 4%   (flat CCP tax, NOT reduced by role bonus)
    #   Facility Tax   = EIV × facility_tax%
    #   Total Job Cost = Job Gross Cost + SCC Surcharge + Facility Tax
    SCC_SURCHARGE = 0.04
    ci_after_bonus = system_cost_index * (1.0 - structure_role_bonus_pct / 100.0)
    rate = ci_after_bonus + SCC_SURCHARGE + facility_tax_pct / 100.0
    manufacturing_fee = manufacturing_fee * rate

    return ManufacturingOption(
        blueprint_item_id=blueprint.item_id,
        blueprint_type_id=blueprint.type_id,
        blueprint_name=bp_name,
        product_type_id=product_row.product_type_id,
        product_name=prod_name,
        character_id=blueprint.character_id,
        runs=runs,
        qty_produced=qty_produced,
        blueprint_me=blueprint.material_efficiency,
        blueprint_te=blueprint.time_efficiency,
        is_bpo=blueprint.is_original,
        activity_label=ACTIVITY_LABEL.get(activity_id, "Unknown"),
        materials=materials,
        missing_skills=missing_skills,
        can_build_now=can_build_now,
        time_seconds=time_total,
        product_sell_price=product_sell_price,
        product_buy_price=product_buy_price,
        product_volume=prod_volume,
        material_cost_owned=material_cost_owned,
        material_cost_to_buy=material_cost_to_buy,
        material_volume=material_volume_total,
        inbound_volume=inbound_volume,
        inbound_shipping_cost=inbound_shipping,
        outbound_shipping_cost=outbound_shipping,
        manufacturing_fee=manufacturing_fee,
        sales_tax=sales_tax,
        broker_fee=broker_fee,
    )


async def get_all_manufacturing_options(
    db: AsyncSession,
    character_ids: list[int],
    runs: int = 1,
    structure_me_bonus: float = 0.0,
    buildable_only: bool = False,
    activity_filter: str = "all",   # "all" | "manufacturing" | "reactions"
    inbound_isk_per_m3: float = 0.0,
    outbound_isk_per_m3: float = 0.0,
    sales_tax_pct: float = 2.0,
    broker_fee_pct: float = 3.0,
    system_cost_index: float = 0.0,
    structure_role_bonus_pct: float = 0.0,
    facility_tax_pct: float = 0.0,
) -> list[ManufacturingOption]:
    stmt = select(Blueprint)
    if character_ids:
        stmt = stmt.where(Blueprint.character_id.in_(character_ids))
    result = await db.execute(stmt)
    blueprints = result.scalars().all()

    options: list[ManufacturingOption] = []
    for bp in blueprints:
        opt = await analyse_blueprint(
            db, bp,
            runs=runs,
            character_ids=character_ids,
            structure_me_bonus=structure_me_bonus,
            inbound_isk_per_m3=inbound_isk_per_m3,
            outbound_isk_per_m3=outbound_isk_per_m3,
            sales_tax_pct=sales_tax_pct,
            broker_fee_pct=broker_fee_pct,
            system_cost_index=system_cost_index,
            structure_role_bonus_pct=structure_role_bonus_pct,
            facility_tax_pct=facility_tax_pct,
        )
        if opt is None:
            continue
        if buildable_only and not opt.can_build_now:
            continue
        if activity_filter == "manufacturing" and opt.activity_label != "Manufacturing":
            continue
        if activity_filter == "reactions" and opt.activity_label != "Reaction":
            continue
        options.append(opt)

    # Sort: profitable-to-manufacture first, then buildable, then by profit delta
    options.sort(key=lambda o: (
        o.recommendation != "manufacture",
        not o.can_build_now,
        -(o.profit_delta if o.product_sell_price else 0),
    ))
    return options
