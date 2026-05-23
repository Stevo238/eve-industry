from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.engine.bom import build_bom_tree, build_product_blueprint_map, collect_raw_materials, collect_stats
from app.engine.manufacturing import StructureConfig, get_all_manufacturing_options, get_asset_inventory
from app.esi.sync import sync_blueprint_market_prices
from app.models.blueprints import Blueprint
from app.models.character import Character
from app.models.industry import IndustryCostIndex, IndustryJob
from app.models.location import Location
from app.models.market import MarketPrice
from app.models.settings import MARKET_HUBS
from app.models.sde import SdeBlueprintProduct, SdeSolarSystem, SdeType
from app.models.structures import ManufacturingStructure, role_bonus_from_type_id
from app.routers.settings import get_settings


async def _build_structures_map(db: AsyncSession) -> dict[int, StructureConfig]:
    """Build {structure_location_id: StructureConfig} for all configured manufacturing structures."""
    rows = (await db.execute(select(ManufacturingStructure))).scalars().all()
    result: dict[int, StructureConfig] = {}
    for s in rows:
        if not s.structure_id:
            continue
        loc = await db.get(Location, s.structure_id)
        if not loc:
            continue
        solar_system_id = loc.solar_system_id
        type_id = loc.type_id
        role_bonus = role_bonus_from_type_id(type_id)

        cost_index = 0.0
        reaction_cost_idx = 0.0
        sys_name = ""
        if solar_system_id:
            mfg_ci = (await db.execute(
                select(IndustryCostIndex).where(
                    IndustryCostIndex.solar_system_id == solar_system_id,
                    IndustryCostIndex.activity == "manufacturing",
                )
            )).scalar_one_or_none()
            rxn_ci = (await db.execute(
                select(IndustryCostIndex).where(
                    IndustryCostIndex.solar_system_id == solar_system_id,
                    IndustryCostIndex.activity == "reaction",
                )
            )).scalar_one_or_none()
            cost_index = mfg_ci.cost_index if mfg_ci else 0.0
            reaction_cost_idx = rxn_ci.cost_index if rxn_ci else 0.0
            sys_row = await db.get(SdeSolarSystem, solar_system_id)
            sys_name = sys_row.name if sys_row else str(solar_system_id)

        result[s.structure_id] = StructureConfig(
            label=s.label,
            system_cost_index=cost_index,
            reaction_cost_index=reaction_cost_idx,
            role_bonus_pct=role_bonus,
            me_rig_bonus=s.me_rig_bonus_pct / 100.0,
            system_name=sys_name,
        )
    return result


ACTIVITY_NAMES = {
    1: "Manufacturing",
    3: "TE Research",
    4: "ME Research",
    5: "Copying",
    8: "Invention",
    11: "Reactions",
}

router = APIRouter(prefix="/industry", tags=["industry"])
templates = Jinja2Templates(directory="app/templates")

_price_sync_status: dict = {"running": False, "done": 0, "error": "", "hub": ""}


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_page(
    request: Request,
    character_id: int | None = None,
    status: str = "active",
    db: AsyncSession = Depends(get_db),
):
    chars_result = await db.execute(select(Character).order_by(Character.name))
    characters = chars_result.scalars().all()
    char_map = {c.character_id: c.name for c in characters}

    stmt = select(IndustryJob).order_by(IndustryJob.end_date.desc())
    if character_id:
        stmt = stmt.where(IndustryJob.character_id == character_id)
    if status == "active":
        stmt = stmt.where(IndustryJob.status.in_(["active", "paused", "ready"]))
    elif status == "completed":
        stmt = stmt.where(IndustryJob.status == "delivered")

    result = await db.execute(stmt)
    jobs = result.scalars().all()

    now = datetime.utcnow()
    job_rows = []
    for job in jobs:
        bp_type = await db.get(SdeType, job.blueprint_type_id)
        prod_type = await db.get(SdeType, job.product_type_id) if job.product_type_id else None
        time_remaining = None
        if job.status == "active" and job.end_date > now:
            delta = job.end_date - now
            h, rem = divmod(int(delta.total_seconds()), 3600)
            m = rem // 60
            time_remaining = f"{h}h {m}m"
        job_rows.append({
            "job": job,
            "bp_name": bp_type.name if bp_type else f"Type {job.blueprint_type_id}",
            "prod_name": prod_type.name if prod_type else "",
            "activity": ACTIVITY_NAMES.get(job.activity_id, f"Activity {job.activity_id}"),
            "char_name": char_map.get(job.character_id, str(job.character_id)),
            "time_remaining": time_remaining,
        })

    return templates.TemplateResponse(
        "industry.html",
        {
            "request": request,
            "characters": characters,
            "job_rows": job_rows,
            "selected_char": character_id,
            "status_filter": status,
        },
    )


async def _manufacturing_shell_context(request: Request, db: AsyncSession) -> dict:
    """Shared fast context used by both the shell page and the results partial."""
    chars_result = await db.execute(select(Character).order_by(Character.name))
    characters = chars_result.scalars().all()

    cfg = await get_settings(db)
    region_id = int(cfg.get("market_region_id", "10000002") or 10000002)
    hub_name  = MARKET_HUBS.get(str(region_id), f"Region {region_id}")
    inbound_isk_m3  = float(cfg.get("inbound_shipping_isk_per_m3", "0") or 0)
    outbound_isk_m3 = float(cfg.get("outbound_shipping_isk_per_m3", "0") or 0)
    sales_tax_pct   = float(cfg.get("sales_tax_pct", "2.0") or 2.0)
    broker_fee_pct  = float(cfg.get("broker_fee_pct", "3.0") or 3.0)

    mfg_system_id            = int(cfg.get("manufacturing_system_id", "30000142") or 30000142)
    facility_tax_pct_mfg     = float(cfg.get("facility_tax_pct", "0.0") or 0.0)
    structure_role_bonus_mfg = float(cfg.get("structure_role_bonus_pct", "0.0") or 0.0)

    mfg_ci = (await db.execute(
        select(IndustryCostIndex).where(
            IndustryCostIndex.solar_system_id == mfg_system_id,
            IndustryCostIndex.activity == "manufacturing",
        )
    )).scalar_one_or_none()
    rxn_ci = (await db.execute(
        select(IndustryCostIndex).where(
            IndustryCostIndex.solar_system_id == mfg_system_id,
            IndustryCostIndex.activity == "reaction",
        )
    )).scalar_one_or_none()
    mfg_cost_index = mfg_ci.cost_index if mfg_ci else 0.0
    reaction_cost_index = rxn_ci.cost_index if rxn_ci else 0.0

    price_row = (
        await db.execute(
            select(MarketPrice).where(MarketPrice.buy_price.isnot(None)).limit(1)
        )
    ).scalar_one_or_none()

    return {
        "request": request,
        "characters": characters,
        "character_ids": [c.character_id for c in characters],
        "cfg": cfg,
        "hub_name": hub_name,
        "inbound_isk_m3": inbound_isk_m3,
        "outbound_isk_m3": outbound_isk_m3,
        "sales_tax_pct": sales_tax_pct,
        "broker_fee_pct": broker_fee_pct,
        "mfg_cost_index": mfg_cost_index,
        "reaction_cost_index": reaction_cost_index,
        "facility_tax_pct_mfg": facility_tax_pct_mfg,
        "structure_role_bonus_mfg": structure_role_bonus_mfg,
        "has_order_prices": price_row is not None,
    }


@router.get("/manufacturing", response_class=HTMLResponse)
async def manufacturing_page(
    request: Request,
    runs: int = 1,
    buildable_only: bool = False,
    activity_filter: str = "all",
    db: AsyncSession = Depends(get_db),
):
    """Fast shell — renders the page and filter form with no analysis."""
    ctx = await _manufacturing_shell_context(request, db)
    return templates.TemplateResponse("manufacturing.html", {
        **ctx,
        "runs": runs,
        "buildable_only": buildable_only,
        "activity_filter": activity_filter,
    })


@router.get("/manufacturing/results", response_class=HTMLResponse)
async def manufacturing_results(
    request: Request,
    runs: int = 1,
    buildable_only: bool = False,
    activity_filter: str = "all",
    db: AsyncSession = Depends(get_db),
):
    """HTMX partial — runs the slow analysis and returns just the results table."""
    ctx = await _manufacturing_shell_context(request, db)
    character_ids = ctx["character_ids"]

    options = []
    sde_available = True
    sde_error = ""
    if character_ids:
        try:
            structures_by_loc = await _build_structures_map(db)
            options = await get_all_manufacturing_options(
                db,
                character_ids=character_ids,
                runs=runs,
                buildable_only=buildable_only,
                activity_filter=activity_filter,
                inbound_isk_per_m3=ctx["inbound_isk_m3"],
                outbound_isk_per_m3=ctx["outbound_isk_m3"],
                sales_tax_pct=ctx["sales_tax_pct"],
                system_cost_index=ctx["mfg_cost_index"],
                reaction_cost_index=ctx["reaction_cost_index"],
                structure_role_bonus_pct=ctx["structure_role_bonus_mfg"],
                facility_tax_pct=ctx["facility_tax_pct_mfg"],
                structures_by_loc=structures_by_loc,
            )
        except Exception:
            import traceback
            sde_available = False
            sde_error = traceback.format_exc()

    return templates.TemplateResponse("manufacturing_results.html", {
        **ctx,
        "options": options,
        "runs": runs,
        "buildable_only": buildable_only,
        "activity_filter": activity_filter,
        "sde_available": sde_available,
        "sde_error": sde_error,
        "total": len(options),
        "buildable_count": sum(1 for o in options if o.can_build_now),
    })


@router.post("/fetch-prices", response_class=HTMLResponse)
async def fetch_market_prices(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    if _price_sync_status["running"]:
        return HTMLResponse(
            '<div class="alert alert-info" hx-get="/industry/prices-status" '
            'hx-trigger="every 3s">Already fetching prices…</div>'
        )
    # Read region from settings before kicking off background task
    cfg = await get_settings(db)
    region_id = int(cfg.get("market_region_id", "10000002") or 10000002)
    hub_name  = MARKET_HUBS.get(str(region_id), f"Region {region_id}")
    _price_sync_status["hub"] = hub_name
    background_tasks.add_task(_run_price_sync, region_id)
    return HTMLResponse(
        f'<div class="alert alert-info" hx-get="/industry/prices-status" '
        f'hx-trigger="every 3s">Fetching prices from {hub_name}…</div>'
    )


@router.get("/prices-status", response_class=HTMLResponse)
async def prices_status(request: Request):
    s = _price_sync_status
    hub = s.get("hub", "market")
    if s["error"]:
        return HTMLResponse(f'<div class="alert alert-error">Price fetch error: {s["error"]}</div>')
    if s["running"]:
        return HTMLResponse(
            f'<div class="alert alert-info" hx-get="/industry/prices-status" '
            f'hx-trigger="every 3s">Fetching prices from {hub}… ({s["done"]} types done)</div>'
        )
    if s["done"]:
        return HTMLResponse(
            f'<div class="alert alert-success">✓ Fetched {s["done"]} prices from {hub}. '
            f'<a href="/industry/manufacturing">Refresh analysis</a></div>'
        )
    return HTMLResponse("")


async def _run_price_sync(region_id: int = 10000002):
    from app.database import AsyncSessionLocal
    _price_sync_status.update({"running": True, "done": 0, "error": ""})
    try:
        async with AsyncSessionLocal() as db:
            count = await sync_blueprint_market_prices(db, region_id=region_id)
            _price_sync_status["done"] = count
    except Exception as exc:
        _price_sync_status["error"] = str(exc)
    finally:
        _price_sync_status["running"] = False


@router.get("/detail", response_class=HTMLResponse)
async def detail_page(
    request: Request,
    blueprint_item_id: int | None = None,
    runs: int = 1,
    structure_me: float = 0.0,
    structure_override_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    bp_result = await db.execute(select(Blueprint).order_by(Blueprint.type_id))
    all_blueprints = bp_result.scalars().all()

    blueprint_map, display_list = await build_product_blueprint_map(db, all_blueprints)

    override_id: int | None = int(structure_override_id) if structure_override_id else None

    # Load all configured structures for the override dropdown
    configured_structures = (await db.execute(select(ManufacturingStructure).order_by(ManufacturingStructure.label))).scalars().all()

    tree = None
    stats = None
    raw_materials: list = []
    selected_prod_name = ""
    error = ""
    mfg_cost_index           = 0.0
    reaction_cost_index      = 0.0
    facility_tax_pct_cfg     = 0.0
    structure_role_bonus_cfg = 0.0
    structure_me_rig         = 0.0
    detected_system_name     = ""
    auto_detected            = False
    active_structure_label   = ""
    active_structure_is_override = False

    if blueprint_item_id:
        selected_bp = next((bp for bp in all_blueprints if bp.item_id == blueprint_item_id), None)
        entry = next((e for e in display_list if e["item_id"] == blueprint_item_id), None)

        if selected_bp and entry:
            product_type_id = entry["prod_type_id"]
            selected_prod_name = entry["prod_name"]

            # Resolve qty per run so root quantity = qty_per_run × runs
            total_qty = runs
            for act_id in (1, 11):
                prod_res = await db.execute(
                    select(SdeBlueprintProduct).where(
                        SdeBlueprintProduct.blueprint_type_id == selected_bp.type_id,
                        SdeBlueprintProduct.activity_id == act_id,
                    )
                )
                prod_row = prod_res.scalar_one_or_none()
                if prod_row:
                    total_qty = (prod_row.quantity or 1) * runs
                    break

            inventory = await get_asset_inventory(db)

            price_rows = (await db.execute(
                select(MarketPrice.type_id, MarketPrice.buy_price, MarketPrice.sell_price,
                       MarketPrice.adjusted_price)
            )).fetchall()
            prices: dict[int, dict] = {
                row.type_id: {
                    "buy":      float(row.buy_price or 0.0),
                    "sell":     float(row.sell_price or 0.0),
                    "adjusted": float(row.adjusted_price or 0.0),
                }
                for row in price_rows
            }

            cfg = await get_settings(db)
            inbound_isk_m3  = float(cfg.get("inbound_shipping_isk_per_m3", "0") or 0)
            outbound_isk_m3 = float(cfg.get("outbound_shipping_isk_per_m3", "0") or 0)
            sales_tax_pct   = float(cfg.get("sales_tax_pct", "2.0") or 2.0)
            broker_fee_pct  = float(cfg.get("broker_fee_pct", "3.0") or 3.0)

            facility_tax_pct_cfg = float(cfg.get("facility_tax_pct", "0.0") or 0.0)

            # Structure resolution priority:
            # 1. User override (structure_override_id query param)
            # 2. Auto-detect: blueprint location_id matches a configured ManufacturingStructure
            # 3. Auto-detect: blueprint location's solar_system_id for cost index
            # 4. Global fallback settings

            override_struct = await db.get(ManufacturingStructure, override_id) if override_id else None
            auto_struct = None
            if not override_struct:
                for s in configured_structures:
                    if s.structure_id and s.structure_id == selected_bp.location_id:
                        auto_struct = s
                        break

            active_struct = override_struct or auto_struct
            active_structure_is_override = override_struct is not None

            if active_struct and active_struct.structure_id:
                struct_loc = await db.get(Location, active_struct.structure_id)
                mfg_system_id = struct_loc.solar_system_id if struct_loc else int(cfg.get("manufacturing_system_id", "30000142") or 30000142)
                structure_role_bonus_cfg = role_bonus_from_type_id(struct_loc.type_id if struct_loc else None)
                structure_me_rig = active_struct.me_rig_bonus_pct
                active_structure_label = active_struct.label
                auto_detected = True
            else:
                # Fall back: use blueprint's solar system for cost index
                bp_location = await db.get(Location, selected_bp.location_id)
                auto_system_id: int | None = bp_location.solar_system_id if bp_location else None
                mfg_system_id = auto_system_id or int(cfg.get("manufacturing_system_id", "30000142") or 30000142)
                structure_role_bonus_cfg = float(cfg.get("structure_role_bonus_pct", "0.0") or 0.0)
                structure_me_rig = 0.0
                active_structure_label = ""
                auto_detected = auto_system_id is not None

            # Resolve system name for display
            sys_row = await db.get(SdeSolarSystem, mfg_system_id)
            detected_system_name = sys_row.name if sys_row else str(mfg_system_id)

            # Look up system cost indexes (both manufacturing and reactions)
            mfg_ci = (await db.execute(
                select(IndustryCostIndex).where(
                    IndustryCostIndex.solar_system_id == mfg_system_id,
                    IndustryCostIndex.activity == "manufacturing",
                )
            )).scalar_one_or_none()
            reaction_ci = (await db.execute(
                select(IndustryCostIndex).where(
                    IndustryCostIndex.solar_system_id == mfg_system_id,
                    IndustryCostIndex.activity == "reaction",
                )
            )).scalar_one_or_none()
            mfg_cost_index      = mfg_ci.cost_index if mfg_ci else 0.0
            reaction_cost_index = reaction_ci.cost_index if reaction_ci else 0.0

            # Effective ME bonus: structure rig + manual slider (for ad-hoc what-if on top)
            effective_me_bonus = (structure_me_rig + structure_me) / 100.0

            try:
                tree = await build_bom_tree(
                    db, product_type_id, total_qty,
                    blueprint_map, inventory, prices,
                    structure_me_bonus=effective_me_bonus,
                    inbound_isk_per_m3=inbound_isk_m3,
                    outbound_isk_per_m3=outbound_isk_m3,
                    sales_tax_pct=sales_tax_pct,
                    broker_fee_pct=broker_fee_pct,
                    manufacturing_cost_index=mfg_cost_index,
                    reaction_cost_index=reaction_cost_index,
                    structure_role_bonus_pct=structure_role_bonus_cfg,
                    facility_tax_pct=facility_tax_pct_cfg,
                )
                stats = collect_stats(tree)
                raw_materials = collect_raw_materials(tree, inbound_isk_per_m3=inbound_isk_m3)
            except Exception:
                import traceback
                error = traceback.format_exc()

    return templates.TemplateResponse(
        "detail.html",
        {
            "request": request,
            "blueprints": display_list,
            "selected_item_id": blueprint_item_id,
            "selected_prod_name": selected_prod_name,
            "tree": tree,
            "stats": stats,
            "raw_materials": raw_materials,
            "runs": runs,
            "structure_me": structure_me,
            "inbound_isk_m3": inbound_isk_m3 if blueprint_item_id else 0.0,
            "outbound_isk_m3": outbound_isk_m3 if blueprint_item_id else 0.0,
            "sales_tax_pct": sales_tax_pct if blueprint_item_id else 2.0,
            "broker_fee_pct": broker_fee_pct if blueprint_item_id else 3.0,
            "mfg_cost_index":           mfg_cost_index if blueprint_item_id else 0.0,
            "reaction_cost_index":      reaction_cost_index if blueprint_item_id else 0.0,
            "structure_role_bonus_pct": structure_role_bonus_cfg if blueprint_item_id else 0.0,
            "facility_tax_pct":         facility_tax_pct_cfg if blueprint_item_id else 0.0,
            "structure_me_rig":         structure_me_rig,
            "detected_system_name":     detected_system_name,
            "auto_detected":            auto_detected,
            "active_structure_label":   active_structure_label,
            "active_structure_is_override": active_structure_is_override,
            "configured_structures":    configured_structures,
            "structure_override_id":    override_id,
            "error": error,
        },
    )
