from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.blueprints import Blueprint
from app.models.industry import IndustryCostIndex
from app.models.location import Location
from app.models.sde import SdeSolarSystem
from app.models.settings import MARKET_HUBS, SETTING_DEFAULTS, UserSetting
from app.models.structures import STRUCTURE_TYPE_NAMES, ManufacturingStructure, role_bonus_from_type_id

router = APIRouter(prefix="/settings", tags=["settings"])
templates = Jinja2Templates(directory="app/templates")


async def get_settings(db: AsyncSession) -> dict[str, str]:
    """Load all settings from DB, falling back to defaults for missing keys."""
    rows = (await db.execute(select(UserSetting))).scalars().all()
    result = dict(SETTING_DEFAULTS)
    for row in rows:
        result[row.key] = row.value
    return result


async def get_setting_float(db: AsyncSession, key: str) -> float:
    settings = await get_settings(db)
    try:
        return float(settings.get(key, SETTING_DEFAULTS.get(key, "0")))
    except ValueError:
        return 0.0


@router.get("", response_class=HTMLResponse)
async def settings_page(request: Request, db: AsyncSession = Depends(get_db)):
    cfg = await get_settings(db)

    # Load solar systems that have manufacturing cost indexes, sorted by name
    systems_result = await db.execute(
        select(SdeSolarSystem)
        .join(IndustryCostIndex,
              SdeSolarSystem.solar_system_id == IndustryCostIndex.solar_system_id)
        .where(IndustryCostIndex.activity == "manufacturing")
        .order_by(SdeSolarSystem.name)
        .distinct()
    )
    solar_systems = systems_result.scalars().all()

    # Resolve stored system ID → display name
    mfg_id = int(cfg.get("manufacturing_system_id", "30000142") or 30000142)
    current_sys = await db.get(SdeSolarSystem, mfg_id)
    current_system_name = current_sys.name if current_sys else ""

    # Load configured structures with resolved display data
    struct_rows = (await db.execute(select(ManufacturingStructure).order_by(ManufacturingStructure.label))).scalars().all()
    structures_display = []
    for s in struct_rows:
        struct_name = ""
        struct_type = ""
        role_bonus: float | None = None
        system_name = ""
        if s.structure_id:
            loc = await db.get(Location, s.structure_id)
            if loc and loc.name and "Unknown Structure" not in loc.name:
                struct_name = loc.name
            if loc and loc.type_id:
                struct_type = STRUCTURE_TYPE_NAMES.get(loc.type_id, f"type {loc.type_id}")
                role_bonus = role_bonus_from_type_id(loc.type_id)
            if loc and loc.solar_system_id:
                sys_row = await db.get(SdeSolarSystem, loc.solar_system_id)
                system_name = sys_row.name if sys_row else ""
        structures_display.append({
            "id": s.id,
            "label": s.label,
            "structure_id": s.structure_id or "",
            "me_rig_bonus_pct": s.me_rig_bonus_pct,
            "struct_name": struct_name,
            "struct_type": struct_type,
            "role_bonus": role_bonus,
            "system_name": system_name,
            "is_npc": bool(s.structure_id and s.structure_id < 1_000_000_000_000),
        })

    # Build list of locations where blueprints actually live, for the structure picker
    bp_loc_rows = (await db.execute(
        select(Location.location_id, Location.name)
        .join(Blueprint, Blueprint.location_id == Location.location_id)
        .where(Location.name.isnot(None))
        .order_by(Location.name)
        .distinct()
    )).fetchall()
    # {name: location_id} — only include rows with a real name
    bp_locations = [
        {"name": r[1], "location_id": r[0], "is_structure": r[0] >= 1_000_000_000_000}
        for r in bp_loc_rows
        if r[1] and "Unknown Structure" not in r[1]
    ]

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "settings": cfg,
            "market_hubs": MARKET_HUBS,
            "solar_systems": solar_systems,
            "current_system_name": current_system_name,
            "structures": structures_display,
            "bp_locations": bp_locations,
        },
    )


@router.post("/structures", response_class=RedirectResponse)
async def create_structure(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    label = str(form.get("label", "")).strip() or "Unnamed Structure"
    raw_id = str(form.get("structure_id", "")).strip()
    me_rig = float(str(form.get("me_rig_bonus_pct", "0")).strip() or 0)
    db.add(ManufacturingStructure(
        label=label,
        structure_id=int(raw_id) if raw_id else None,
        me_rig_bonus_pct=me_rig,
    ))
    await db.commit()
    return RedirectResponse("/settings?saved=1#structures", status_code=303)


@router.post("/structures/{struct_id}", response_class=RedirectResponse)
async def update_structure(struct_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    s = await db.get(ManufacturingStructure, struct_id)
    if s:
        raw_id = str(form.get("structure_id", "")).strip()
        s.label = str(form.get("label", "")).strip() or s.label
        s.structure_id = int(raw_id) if raw_id else None
        s.me_rig_bonus_pct = float(str(form.get("me_rig_bonus_pct", "0")).strip() or 0)
        await db.commit()
    return RedirectResponse("/settings?saved=1#structures", status_code=303)


@router.post("/structures/{struct_id}/delete", response_class=RedirectResponse)
async def delete_structure(struct_id: int, db: AsyncSession = Depends(get_db)):
    s = await db.get(ManufacturingStructure, struct_id)
    if s:
        await db.delete(s)
        await db.commit()
    return RedirectResponse("/settings?saved=1#structures", status_code=303)


@router.post("/resolve-structures", response_class=RedirectResponse)
async def resolve_structures(db: AsyncSession = Depends(get_db)):
    from app.esi.sync import resolve_locations
    resolved, _errs = await resolve_locations(db)

    # Auto-link structure rows that have no structure_id but whose label
    # exactly matches a now-resolved Location name (player structure IDs only).
    unlinked = (await db.execute(
        select(ManufacturingStructure).where(ManufacturingStructure.structure_id.is_(None))
    )).scalars().all()
    linked = 0
    for s in unlinked:
        loc = (await db.execute(
            select(Location).where(Location.name == s.label)
        )).scalar_one_or_none()
        if loc:
            s.structure_id = loc.location_id
            linked += 1
    if linked:
        await db.commit()

    return RedirectResponse(f"/settings?resolved={resolved}&linked={linked}#structures", status_code=303)


@router.post("", response_class=RedirectResponse)
async def save_settings(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()

    # If the user selected by name, resolve it to a system ID
    mfg_id_override: str | None = None
    system_name = str(form.get("manufacturing_system_name", "")).strip()
    if system_name:
        sys_row = (await db.execute(
            select(SdeSolarSystem).where(SdeSolarSystem.name == system_name)
        )).scalar_one_or_none()
        if sys_row:
            mfg_id_override = str(sys_row.solar_system_id)

    for key in SETTING_DEFAULTS:
        if key == "manufacturing_system_id" and mfg_id_override is not None:
            value = mfg_id_override
        else:
            value = str(form.get(key, SETTING_DEFAULTS[key]))
        existing = await db.get(UserSetting, key)
        if existing:
            existing.value = value
        else:
            db.add(UserSetting(key=key, value=value))
    await db.commit()
    return RedirectResponse("/settings?saved=1", status_code=303)
