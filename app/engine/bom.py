"""
Bill of Materials (BOM) tree builder.

Recursively expands a blueprint into its full material tree, substituting
user-owned sub-blueprints for intermediate products all the way down to
raw materials.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.blueprints import Blueprint
from app.models.sde import (
    SdeBlueprintActivity,
    SdeBlueprintMaterial,
    SdeBlueprintProduct,
    SdeType,
)

ACTIVITY_MANUFACTURING = 1
ACTIVITY_REACTIONS = 11
SUPPORTED_ACTIVITIES = [ACTIVITY_MANUFACTURING, ACTIVITY_REACTIONS]
ACTIVITY_LABEL = {ACTIVITY_MANUFACTURING: "Manufacturing", ACTIVITY_REACTIONS: "Reaction"}
MAX_DEPTH = 12

# CCP-fixed SCC (Secure Commerce Commission) surcharge on every industry job.
# Applied to EIV at full rate — NOT reduced by structure role bonuses.
SCC_SURCHARGE = 0.04  # 4 %


@dataclass
class BOMNode:
    """One node in the bill of materials tree — either the root product or a material."""

    type_id: int
    name: str
    quantity_needed: int
    quantity_available: int   # units in inventory right now
    buy_price: float          # best Jita buy order  (you SELL to this price)
    sell_price: float         # best Jita sell order (you BUY from this price)
    volume_each: float

    # Populated when the user owns a blueprint that produces this item
    has_blueprint: bool = False
    blueprint_item_id: int | None = None
    blueprint_type_id: int | None = None
    blueprint_me: int = 0
    blueprint_te: int = 0
    is_bpo: bool = False
    activity_label: str = ""
    qty_per_run: int = 1        # units the blueprint produces per run
    runs_needed: int = 0        # runs required to cover quantity_needed
    job_time_seconds: int = 0

    children: list[BOMNode] = field(default_factory=list)

    # Shipping rates — set once at tree build time, same value on every node
    inbound_isk_per_m3: float = 0.0   # ISK/m³ to ship purchased materials in
    outbound_isk_per_m3: float = 0.0  # ISK/m³ to ship finished product out
    # Selling fee rates — set once at tree build time, same value on every node
    sales_tax_pct: float = 0.0        # % transaction tax when selling (e.g. 2.0)
    broker_fee_pct: float = 0.0       # % broker fee when listing on market (e.g. 3.0)

    # Industry job cost — set once at tree build time, same value on every node
    adjusted_price: float = 0.0              # CCP adjusted price for EIV calculation
    manufacturing_cost_index: float = 0.0   # system manufacturing cost index (e.g. 0.05 = 5%)
    reaction_cost_index: float = 0.0         # system reaction cost index
    structure_role_bonus_pct: float = 0.0   # structure discount on cost-index component only
                                             #   0% = NPC station, 15% = Raitaru/Azbel, 20% = Sotiyo
    facility_tax_pct: float = 0.0            # extra % set by structure owner (on top of everything)

    # ── Derived properties ───────────────────────────────────────────────────

    @property
    def shortage(self) -> int:
        return max(0, self.quantity_needed - self.quantity_available)

    @property
    def is_satisfied(self) -> bool:
        return self.shortage <= 0

    # ── Per-unit acquisition cost ────────────────────────────────────────────

    @property
    def cost_owned(self) -> float:
        """
        Opportunity cost of the units you already own.
        Priced at the buy order price — what you'd receive selling them instead
        of consuming them. Capped at quantity_needed.
        """
        owned = min(self.quantity_needed, self.quantity_available)
        return owned * self.buy_price

    @property
    def cost_to_buy(self) -> float:
        """
        Acquisition cost of the units you still need to buy.
        Priced at the sell order price — what you'd actually pay on the market.
        """
        return self.shortage * self.sell_price

    @property
    def true_material_cost(self) -> float:
        """
        Realistic cost for this material:
          owned qty  × buy_price  (opportunity cost — could sell them)
          missing qty × sell_price (acquisition cost — must buy them)
        """
        return self.cost_owned + self.cost_to_buy

    @property
    def total_buy_cost(self) -> float:
        """Worst-case cost: buy ALL quantity_needed at market sell price."""
        return self.quantity_needed * self.sell_price

    @property
    def total_sell_value(self) -> float:
        """Gross ISK received by selling quantity_needed to buy orders (before fees)."""
        return self.quantity_needed * self.buy_price

    @property
    def net_sell_value(self) -> float:
        """ISK received after transaction tax and broker fee (excludes outbound shipping)."""
        fee_factor = 1.0 - (self.sales_tax_pct + self.broker_fee_pct) / 100.0
        return self.quantity_needed * self.buy_price * fee_factor

    # ── Shipping ─────────────────────────────────────────────────────────────

    @property
    def inbound_shipping_cost(self) -> float:
        """
        Shipping cost for THIS node's shortage only.
        Rules:
          - Leaf nodes (raw materials you buy): ship the shortage volume in.
          - Intermediate nodes (manufactured locally): 0 — the sub-component is
            produced at your production facility, so it doesn't need to be
            shipped in. Its RAW MATERIAL children still carry their own shipping.
        """
        if self.children:
            return 0.0  # manufactured here — no inbound shipping on the node itself
        return self.shortage * self.volume_each * self.inbound_isk_per_m3

    @property
    def total_inbound_shipping(self) -> float:
        """Recursive sum of inbound shipping across all leaf descendants."""
        if not self.children:
            return self.inbound_shipping_cost
        return sum(c.total_inbound_shipping for c in self.children)

    @property
    def outbound_shipping_cost(self) -> float:
        """
        Shipping the finished product from the production site to market.
        Only meaningful on the root product — call it there explicitly.
        """
        return self.quantity_needed * self.volume_each * self.outbound_isk_per_m3

    # ── Cost rollups ─────────────────────────────────────────────────────────

    @property
    def make_cost(self) -> float:
        """
        Recursive realistic cost to manufacture this item, including inbound
        shipping on every raw material leaf that has a shortage.

        - Leaf nodes: true_material_cost + inbound_shipping_cost
        - Intermediate nodes: sum of children's make_cost (each child handles
          its own shipping at its leaf level; the intermediate itself ships nothing)
        """
        if not self.children:
            return self.true_material_cost + self.inbound_shipping_cost
        return sum(
            (c.make_cost if (c.has_blueprint and c.children) else
             c.true_material_cost + c.inbound_shipping_cost)
            for c in self.children
        )

    @property
    def make_vs_buy_saving(self) -> float:
        """Positive = cheaper to make (including shipping). Negative = cheaper to buy."""
        if not self.children:
            return 0.0
        return self.total_buy_cost - self.make_cost

    @property
    def is_exit_point(self) -> bool:
        """
        True when selling this manufactured intermediate — net of all selling
        fees (broker, sales tax) and outbound shipping — yields more ISK than
        its full make cost.  Uses the same fee model as the detail page so the
        tree and the drill-down are always consistent.
        """
        if not (self.has_blueprint and self.children and self.buy_price > 0):
            return False
        net_revenue = self.net_sell_value - self.outbound_shipping_cost
        return net_revenue > self.make_cost

    @property
    def exit_profit(self) -> float:
        """Net ISK gain from selling this item (after all fees + outbound shipping) vs make cost."""
        return (self.net_sell_value - self.outbound_shipping_cost) - self.make_cost

    @property
    def job_cost(self) -> float:
        """
        Industry job installation fee for THIS manufacturing/reaction job.

        EVE formula (matches the in-game installation breakdown):
          Job Gross Cost = EIV × cost_index × (1 − structure_role_bonus)
          SCC Surcharge  = EIV × 4%   (flat CCP tax, NOT reduced by role bonus)
          Facility Tax   = EIV × facility_tax%
          ─────────────────────────────────────────────────────
          Total Job Cost = Job Gross Cost + SCC Surcharge + Facility Tax

        Only applies to nodes with a blueprint and children (actual jobs).
        """
        if not (self.children and self.has_blueprint):
            return 0.0
        eiv = sum(c.adjusted_price * c.quantity_needed for c in self.children)
        if self.activity_label == "Reaction":
            ci = self.reaction_cost_index
        else:
            ci = self.manufacturing_cost_index
        # Cost-index portion reduced by structure role bonus (e.g. 15% for Raitaru)
        ci_after_bonus = ci * (1.0 - self.structure_role_bonus_pct / 100.0)
        # SCC surcharge is unavoidable — not reduced by role bonus
        rate = ci_after_bonus + SCC_SURCHARGE + self.facility_tax_pct / 100.0
        return eiv * rate

    @property
    def total_job_cost(self) -> float:
        """Recursive sum of job fees for this node and all manufactured descendants."""
        return self.job_cost + sum(c.total_job_cost for c in self.children)

    @property
    def total_volume_needed(self) -> float:
        return self.volume_each * self.quantity_needed

    @property
    def total_volume_short(self) -> float:
        return self.volume_each * self.shortage


@dataclass
class BOMStats:
    """Aggregate stats for the whole BOM tree — passed to the template."""
    total_make_cost: float = 0.0         # materials + inbound shipping
    total_material_cost: float = 0.0     # materials only (no shipping)
    total_inbound_shipping: float = 0.0  # sum of inbound shipping on all raw leaves
    outbound_shipping: float = 0.0       # outbound shipping on the finished product
    revenue: float = 0.0                 # sell root product to buy orders
    net_profit: float = 0.0             # revenue - make_cost - outbound_shipping
    intermediate_count: int = 0          # nodes that have a blueprint (excl. root)
    raw_material_count: int = 0          # true leaf nodes
    missing_material_count: int = 0      # nodes with shortage > 0
    exit_point_count: int = 0
    total_job_time: int = 0              # sum of all job times (seconds)
    total_job_cost: float = 0.0           # sum of all industry job installation fees


def collect_stats(root: BOMNode) -> BOMStats:
    """Walk the tree and gather aggregate statistics."""
    stats = BOMStats()
    stats.total_make_cost = root.make_cost
    stats.total_inbound_shipping = root.total_inbound_shipping
    stats.outbound_shipping = root.outbound_shipping_cost
    # material-only cost = make_cost minus all shipping
    stats.total_material_cost = root.make_cost - root.total_inbound_shipping
    stats.revenue = root.total_sell_value
    stats.net_profit = root.total_sell_value - root.make_cost - root.outbound_shipping_cost
    stats.total_job_cost = root.total_job_cost

    def walk(node: BOMNode, is_root: bool = False) -> None:
        if node.job_time_seconds:
            stats.total_job_time += node.job_time_seconds
        if not is_root:
            if node.has_blueprint and node.children:
                stats.intermediate_count += 1
                if node.is_exit_point:
                    stats.exit_point_count += 1
            else:
                stats.raw_material_count += 1
                if node.shortage > 0:
                    stats.missing_material_count += 1
        for child in node.children:
            walk(child)

    walk(root, is_root=True)
    return stats


def collect_raw_materials(root: BOMNode, inbound_isk_per_m3: float = 0.0) -> list[dict]:
    """
    Walk the whole tree and aggregate all leaf nodes (true raw materials —
    no blueprint expansion) into a flat shopping list keyed by type_id.

    The same raw material may appear in multiple branches (e.g. Tritanium
    used by two different sub-components); we sum quantities and use the
    single inventory figure (which is a global total, same in every branch).
    """
    aggregated: dict[int, dict] = {}

    def walk(node: BOMNode) -> None:
        if not node.children:
            # Leaf node — raw material
            if node.type_id in aggregated:
                aggregated[node.type_id]["quantity_needed"] += node.quantity_needed
            else:
                aggregated[node.type_id] = {
                    "type_id": node.type_id,
                    "name": node.name,
                    "quantity_needed": node.quantity_needed,
                    "inventory_qty": node.quantity_available,  # global total, same everywhere
                    "sell_price": node.sell_price,             # buy FROM market (cost)
                    "buy_price": node.buy_price,               # sell TO market (revenue)
                    "volume_each": node.volume_each,
                }
        else:
            for child in node.children:
                walk(child)

    # Walk children only — root is the product, not a raw material
    for child in root.children:
        walk(child)

    result = []
    for item in aggregated.values():
        qty = item["quantity_needed"]
        inv = item["inventory_qty"]
        shortage = max(0, qty - inv)
        owned = min(qty, inv)
        item["shortage"] = shortage
        item["is_satisfied"] = shortage <= 0
        # True cost: owned at opportunity cost (buy price), missing at acquisition cost (sell price)
        item["cost_owned"]   = owned * item["buy_price"]
        item["owned_qty"]    = owned
        item["owned_volume"] = owned * item["volume_each"]
        item["cost_to_buy"]  = shortage * item["sell_price"]
        item["total_cost"] = item["cost_owned"] + item["cost_to_buy"]
        item["shortage_cost"] = shortage * item["sell_price"]   # what you still need to spend
        item["total_volume"] = qty * item["volume_each"]
        item["shortage_volume"] = shortage * item["volume_each"]
        # Inbound shipping — only on the units you still need to buy and ship in
        item["inbound_shipping"] = shortage * item["volume_each"] * inbound_isk_per_m3
        item["total_with_shipping"] = item["total_cost"] + item["inbound_shipping"]
        result.append(item)

    # Sort: unsatisfied first, then by total cost descending
    result.sort(key=lambda x: (x["is_satisfied"], -x["total_cost"]))
    return result


async def build_product_blueprint_map(
    db: AsyncSession,
    all_blueprints: list[Blueprint],
) -> tuple[dict[int, Blueprint], list[dict]]:
    """
    Returns:
      blueprint_map  — {product_type_id: Blueprint} highest-ME per product
      display_list   — sorted list of dicts for the selector dropdown
    """
    bp_map: dict[int, Blueprint] = {}
    prod_to_activity: dict[int, int] = {}

    for bp in all_blueprints:
        for act_id in SUPPORTED_ACTIVITIES:
            result = await db.execute(
                select(SdeBlueprintProduct).where(
                    SdeBlueprintProduct.blueprint_type_id == bp.type_id,
                    SdeBlueprintProduct.activity_id == act_id,
                )
            )
            prod_row = result.scalar_one_or_none()
            if prod_row:
                pid = prod_row.product_type_id
                existing = bp_map.get(pid)
                if existing is None or bp.material_efficiency > existing.material_efficiency:
                    bp_map[pid] = bp
                    prod_to_activity[pid] = act_id
                break

    display_list: list[dict] = []
    seen: set[int] = set()

    for prod_type_id, bp in bp_map.items():
        if bp.item_id in seen:
            continue
        seen.add(bp.item_id)

        bp_sde = await db.get(SdeType, bp.type_id)
        prod_sde = await db.get(SdeType, prod_type_id)
        act_id = prod_to_activity.get(prod_type_id, 1)

        display_list.append({
            "item_id": bp.item_id,
            "bp_name": bp_sde.name if bp_sde else f"Type {bp.type_id}",
            "prod_name": prod_sde.name if prod_sde else f"Type {prod_type_id}",
            "prod_type_id": prod_type_id,
            "me": bp.material_efficiency,
            "is_bpo": bp.is_original,
            "activity_label": ACTIVITY_LABEL.get(act_id, "Manufacturing"),
        })

    display_list.sort(key=lambda x: x["prod_name"])
    return bp_map, display_list


async def build_bom_tree(
    db: AsyncSession,
    type_id: int,
    quantity_needed: int,
    blueprint_map: dict[int, Blueprint],
    inventory: dict[int, int],
    prices: dict[int, dict],
    visited: set[int] | None = None,
    structure_me_bonus: float = 0.0,
    inbound_isk_per_m3: float = 0.0,
    outbound_isk_per_m3: float = 0.0,
    sales_tax_pct: float = 0.0,
    broker_fee_pct: float = 0.0,
    manufacturing_cost_index: float = 0.0,
    reaction_cost_index: float = 0.0,
    structure_role_bonus_pct: float = 0.0,
    facility_tax_pct: float = 0.0,
    depth: int = 0,
) -> BOMNode:
    """Recursively build the BOM tree for the given item and quantity."""
    if visited is None:
        visited = set()

    from app.engine.manufacturing import calc_material_quantity, calc_time_seconds

    sde_type = await db.get(SdeType, type_id)
    name = sde_type.name if sde_type else f"Type {type_id}"
    volume = float(sde_type.volume or 0.0) if sde_type else 0.0

    p = prices.get(type_id, {})
    node = BOMNode(
        type_id=type_id,
        name=name,
        quantity_needed=quantity_needed,
        quantity_available=inventory.get(type_id, 0),
        buy_price=p.get("buy", 0.0),
        sell_price=p.get("sell", 0.0),
        volume_each=volume,
        inbound_isk_per_m3=inbound_isk_per_m3,
        outbound_isk_per_m3=outbound_isk_per_m3,
        sales_tax_pct=sales_tax_pct,
        broker_fee_pct=broker_fee_pct,
        adjusted_price=p.get("adjusted", 0.0),
        manufacturing_cost_index=manufacturing_cost_index,
        reaction_cost_index=reaction_cost_index,
        structure_role_bonus_pct=structure_role_bonus_pct,
        facility_tax_pct=facility_tax_pct,
    )

    if depth >= MAX_DEPTH or type_id in visited:
        return node

    bp = blueprint_map.get(type_id)
    if bp is None:
        return node

    # Find the activity this blueprint supports
    activity_id = None
    activity_row = None
    for act_id in SUPPORTED_ACTIVITIES:
        result = await db.execute(
            select(SdeBlueprintActivity).where(
                SdeBlueprintActivity.blueprint_type_id == bp.type_id,
                SdeBlueprintActivity.activity_id == act_id,
            )
        )
        activity_row = result.scalar_one_or_none()
        if activity_row:
            activity_id = act_id
            break

    if activity_row is None:
        return node

    prod_result = await db.execute(
        select(SdeBlueprintProduct).where(
            SdeBlueprintProduct.blueprint_type_id == bp.type_id,
            SdeBlueprintProduct.activity_id == activity_id,
        )
    )
    product_row = prod_result.scalar_one_or_none()
    if product_row is None:
        return node

    qty_per_run = product_row.quantity or 1
    runs_needed = math.ceil(quantity_needed / qty_per_run)

    node.has_blueprint = True
    node.blueprint_item_id = bp.item_id
    node.blueprint_type_id = bp.type_id
    node.blueprint_me = bp.material_efficiency
    node.blueprint_te = bp.time_efficiency
    node.is_bpo = bp.is_original
    node.activity_label = ACTIVITY_LABEL.get(activity_id, "Unknown")
    node.qty_per_run = qty_per_run
    node.runs_needed = runs_needed
    node.job_time_seconds = calc_time_seconds(
        activity_row.time, runs_needed, bp.time_efficiency
    )

    mat_result = await db.execute(
        select(SdeBlueprintMaterial).where(
            SdeBlueprintMaterial.blueprint_type_id == bp.type_id,
            SdeBlueprintMaterial.activity_id == activity_id,
        )
    )
    sde_materials = mat_result.scalars().all()

    new_visited = visited | {type_id}
    for mat in sde_materials:
        mat_qty = calc_material_quantity(
            mat.quantity, runs_needed, bp.material_efficiency, structure_me_bonus
        )
        child = await build_bom_tree(
            db, mat.material_type_id, mat_qty,
            blueprint_map, inventory, prices,
            visited=set(new_visited),
            structure_me_bonus=structure_me_bonus,
            inbound_isk_per_m3=inbound_isk_per_m3,
            outbound_isk_per_m3=outbound_isk_per_m3,
            sales_tax_pct=sales_tax_pct,
            broker_fee_pct=broker_fee_pct,
            manufacturing_cost_index=manufacturing_cost_index,
            reaction_cost_index=reaction_cost_index,
            structure_role_bonus_pct=structure_role_bonus_pct,
            facility_tax_pct=facility_tax_pct,
            depth=depth + 1,
        )
        node.children.append(child)

    # Sort children: intermediates first (most interesting), then by shortage, then name
    node.children.sort(key=lambda c: (
        not (c.has_blueprint and c.children),
        c.is_satisfied,
        c.name,
    ))

    return node
