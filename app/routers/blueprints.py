from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.blueprints import Blueprint
from app.models.character import Character
from app.models.sde import SdeType

router = APIRouter(prefix="/blueprints", tags=["blueprints"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def blueprints_page(
    request: Request,
    character_id: int | None = None,
    search: str = "",
    show: str = "all",  # "all", "bpo", "bpc"
    db: AsyncSession = Depends(get_db),
):
    chars_result = await db.execute(select(Character).order_by(Character.name))
    characters = chars_result.scalars().all()

    stmt = (
        select(
            Blueprint.item_id,
            Blueprint.character_id,
            Blueprint.type_id,
            Blueprint.location_id,
            Blueprint.location_flag,
            Blueprint.material_efficiency,
            Blueprint.time_efficiency,
            Blueprint.runs,
            SdeType.name.label("type_name"),
        )
        .outerjoin(SdeType, Blueprint.type_id == SdeType.type_id)
        .order_by(SdeType.name)
    )

    if character_id:
        stmt = stmt.where(Blueprint.character_id == character_id)

    if search:
        stmt = stmt.where(SdeType.name.ilike(f"%{search}%"))

    if show == "bpo":
        stmt = stmt.where(Blueprint.runs == -1)
    elif show == "bpc":
        stmt = stmt.where(Blueprint.runs != -1)

    result = await db.execute(stmt)
    blueprints = result.fetchall()

    # Character name lookup
    char_map = {c.character_id: c.name for c in characters}

    return templates.TemplateResponse(
        "blueprints.html",
        {
            "request": request,
            "characters": characters,
            "blueprints": blueprints,
            "char_map": char_map,
            "selected_char": character_id,
            "search": search,
            "show": show,
            "total": len(blueprints),
        },
    )
