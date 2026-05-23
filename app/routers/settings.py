from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.industry import IndustryCostIndex
from app.models.sde import SdeSolarSystem
from app.models.settings import MARKET_HUBS, SETTING_DEFAULTS, UserSetting

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

    # Resolve stored ID → display name
    mfg_id = int(cfg.get("manufacturing_system_id", "30000142") or 30000142)
    current_sys = await db.get(SdeSolarSystem, mfg_id)
    current_system_name = current_sys.name if current_sys else ""

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "settings": cfg,
            "market_hubs": MARKET_HUBS,
            "solar_systems": solar_systems,
            "current_system_name": current_system_name,
        },
    )


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
