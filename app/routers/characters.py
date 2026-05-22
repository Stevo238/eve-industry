from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.esi.sync import sync_character_all, sync_market_prices, sync_industry_cost_indexes
from app.models.character import Character
from app.models.industry import CharacterSkill, IndustryJob

router = APIRouter(prefix="/characters", tags=["characters"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def characters_page(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Character).order_by(Character.name))
    characters = result.scalars().all()
    return templates.TemplateResponse(
        "characters.html", {"request": request, "characters": characters}
    )


@router.post("/sync/{character_id}", response_class=HTMLResponse)
async def sync_character(
    character_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    result = await sync_character_all(db, character_id)
    char = await db.get(Character, character_id)

    errors_text = ""
    if result.get("errors"):
        errors_text = "; ".join(f"{k}: {v}" for k, v in result["errors"].items())

    return templates.TemplateResponse(
        "partials/character_card.html",
        {
            "request": request,
            "char": char,
            "sync_result": result,
            "errors_text": errors_text,
        },
    )


@router.post("/sync-all", response_class=HTMLResponse)
async def sync_all_characters(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Character))
    characters = result.scalars().all()

    all_results = {}
    for char in characters:
        all_results[char.character_id] = await sync_character_all(db, char.character_id)

    # Also sync global market data
    try:
        await sync_market_prices(db)
        await sync_industry_cost_indexes(db)
    except Exception:
        pass

    return templates.TemplateResponse(
        "characters.html",
        {"request": request, "characters": characters, "sync_results": all_results},
    )


@router.get("/skills/{character_id}", response_class=HTMLResponse)
async def character_skills(
    character_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    char = await db.get(Character, character_id)
    result = await db.execute(
        select(CharacterSkill).where(CharacterSkill.character_id == character_id)
    )
    skills = result.scalars().all()
    return templates.TemplateResponse(
        "skills.html", {"request": request, "char": char, "skills": skills}
    )
