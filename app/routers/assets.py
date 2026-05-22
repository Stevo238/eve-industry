from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.assets import Asset
from app.models.character import Character
from app.models.location import Location
from app.models.sde import SdeType

router = APIRouter(prefix="/assets", tags=["assets"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def assets_page(
    request: Request,
    character_id: str | None = None,
    search: str = "",
    db: AsyncSession = Depends(get_db),
):
    # Treat empty string (form submits "" for "All Characters") as no filter
    char_id_int: int | None = int(character_id) if character_id else None

    chars_result = await db.execute(select(Character).order_by(Character.name))
    characters = chars_result.scalars().all()
    char_map = {c.character_id: c.name for c in characters}

    stmt = (
        select(
            Asset.item_id,
            Asset.type_id,
            Asset.character_id,
            Asset.location_id,
            Asset.location_flag,
            Asset.quantity,
            Asset.is_blueprint_copy,
            SdeType.name.label("type_name"),
            SdeType.volume,
            Location.name.label("location_name"),
            Location.location_type.label("loc_type"),
        )
        .outerjoin(SdeType, Asset.type_id == SdeType.type_id)
        .outerjoin(Location, Asset.location_id == Location.location_id)
        .order_by(SdeType.name)
    )

    if char_id_int:
        stmt = stmt.where(Asset.character_id == char_id_int)

    if search:
        stmt = stmt.where(SdeType.name.ilike(f"%{search}%"))

    result = await db.execute(stmt)
    rows = result.fetchall()

    # Count totals
    total_stmt = select(func.count(Asset.item_id), func.sum(Asset.quantity))
    if char_id_int:
        total_stmt = total_stmt.where(Asset.character_id == char_id_int)
    totals = (await db.execute(total_stmt)).fetchone()

    return templates.TemplateResponse(
        "assets.html",
        {
            "request": request,
            "characters": characters,
            "char_map": char_map,
            "assets": rows,
            "selected_char": char_id_int,
            "search": search,
            "total_items": totals[0] or 0,
            "total_quantity": totals[1] or 0,
        },
    )
