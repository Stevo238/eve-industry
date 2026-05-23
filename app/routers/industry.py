from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.engine.bom import build_bom_tree, build_product_blueprint_map, collect_raw_materials, collect_stats
from app.engine.manufacturing import get_all_manufacturing_options, get_asset_inventory
from app.esi.sync import sync_blueprint_market_prices
from app.models.blueprints import Blueprint
from app.models.character import Character
from app.models.industry import IndustryCostIndex, IndustryJob
from app.models.market import MarketPrice
from app.models.settings import MARKET_HUBS
from app.models.sde import SdeBlueprintProduct, SdeType
from app.routers.settings import get_settings

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


@router.get("/manufacturing", response_class=HTMLResponse)
async def manufacturing_page(
    request: Request,
    runs: int = 1,
    buildable_only: bool = False,
    structure_me: float = 0.0,
    activity_filter: str = "all",   # "all" | "manufacturing" | "reactions"
    db: AsyncSession = Depends(get_db),
):
    chars_result = await db.execute(select(Character).order_by(Character.name))
    characters = chars_result.scalars().all()
    character_ids = [c.character_id for c in characters]

    # All config comes from Settings — no per-page overrides for shipping/hub
    cfg = await get_settings(db)
    inbound_isk_m3  = float(cfg.get("inbound_shipping_isk_per_m3", "0") or 0)
    outbound_isk_m3 = float(cfg.get("outbound_shipping_isk_per_m3", "0") or 0)
    sales_tax_pct   = float(cfg.get("sales_tax_pct", "2.0") or 2.0)
    broker_fee_pct  = float(cfg.get("broker_fee_pct", "3.0") or 3.0)
    region_id       = int(cfg.get("market_region_id", "10000002") or 10000002)
    hub_name        = MARKET_HUBS.get(str(region_id), f"Region {region_id}")

    mfg_system_id            = int(cfg.get("manufacturing_system_id", "30000142") or 30000142)
    facility_tax_pct_mfg     = float(cfg.get("facility_tax_pct", "0.0") or 0.0)
    structure_role_bonus_mfg = float(cfg.get("structure_role_bonus_pct", "0.0") or 0.0)

    mfg_ci = (await db.execute(
        select(IndustryCostIndex).where(
            IndustryCostIndex.solar_system_id == mfg_system_id,
            IndustryCostIndex.activity == "manufacturing",
        )
    )).scalar_one_or_none()
    mfg_cost_index = mfg_ci.cost_index if mfg_ci else 0.0

    price_row = (
        await db.execute(
            select(MarketPrice).where(MarketPrice.buy_price.isnot(None)).limit(1)
        )
    ).scalar_one_or_none()
    has_order_prices = price_row is not None

    options = []
    sde_available = True
    sde_error = ""
    if character_ids:
        try:
            options = await get_all_manufacturing_options(
                db,
                character_ids=character_ids,
                runs=runs,
                structure_me_bonus=structure_me / 100.0,
                buildable_only=buildable_only,
                activity_filter=activity_filter,
                inbound_isk_per_m3=inbound_isk_m3,
                outbound_isk_per_m3=outbound_isk_m3,
                sales_tax_pct=sales_tax_pct,
                broker_fee_pct=broker_fee_pct,
                system_cost_index=mfg_cost_index,
                structure_role_bonus_pct=structure_role_bonus_mfg,
                facility_tax_pct=facility_tax_pct_mfg,
            )
        except Exception:
            import traceback
            sde_available = False
            sde_error = traceback.format_exc()

    return templates.TemplateResponse(
        "manufacturing.html",
        {
            "request": request,
            "characters": characters,
            "options": options,
            "runs": runs,
            "buildable_only": buildable_only,
            "structure_me": structure_me,
            "activity_filter": activity_filter,
            "sde_available": sde_available,
            "sde_error": sde_error,
            "total": len(options),
            "buildable_count": sum(1 for o in options if o.can_build_now),
            "has_order_prices": has_order_prices,
            "price_sync_status": _price_sync_status,
            "hub_name": hub_name,
            "inbound_isk_m3": inbound_isk_m3,
            "outbound_isk_m3": outbound_isk_m3,
            "sales_tax_pct": sales_tax_pct,
        },
    )


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
    db: AsyncSession = Depends(get_db),
):
    bp_result = await db.execute(select(Blueprint).order_by(Blueprint.type_id))
    all_blueprints = bp_result.scalars().all()

    blueprint_map, display_list = await build_product_blueprint_map(db, all_blueprints)

    tree = None
    stats = None
    raw_materials: list = []
    selected_prod_name = ""
    error = ""
    mfg_cost_index           = 0.0
    reaction_cost_index      = 0.0
    facility_tax_pct_cfg     = 0.0
    structure_role_bonus_cfg = 0.0

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

            mfg_system_id            = int(cfg.get("manufacturing_system_id", "30000142") or 30000142)
            facility_tax_pct_cfg     = float(cfg.get("facility_tax_pct", "0.0") or 0.0)
            structure_role_bonus_cfg = float(cfg.get("structure_role_bonus_pct", "0.0") or 0.0)

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

            try:
                tree = await build_bom_tree(
                    db, product_type_id, total_qty,
                    blueprint_map, inventory, prices,
                    structure_me_bonus=structure_me / 100.0,
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
            "error": error,
        },
    )
