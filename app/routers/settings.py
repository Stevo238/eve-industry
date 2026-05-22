from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.settings import SETTING_DEFAULTS, UserSetting

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
    settings = await get_settings(db)
    return templates.TemplateResponse(
        "settings.html", {"request": request, "settings": settings}
    )


@router.post("", response_class=RedirectResponse)
async def save_settings(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    for key in SETTING_DEFAULTS:
        value = str(form.get(key, "0"))
        existing = await db.get(UserSetting, key)
        if existing:
            existing.value = value
        else:
            db.add(UserSetting(key=key, value=value))
    await db.commit()
    return RedirectResponse("/settings?saved=1", status_code=303)
