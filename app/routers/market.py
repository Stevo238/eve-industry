from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.esi.client import ESIClient
from app.esi.sync import sync_market_prices
from app.models.character import Character
from app.models.market import CharacterOrder, MarketPrice
from app.models.sde import SdeType

router = APIRouter(prefix="/market", tags=["market"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def market_page(
    request: Request,
    search: str = "",
    db: AsyncSession = Depends(get_db),
):
    chars_result = await db.execute(select(Character).order_by(Character.name))
    characters = chars_result.scalars().all()
    char_map = {c.character_id: c.name for c in characters}

    # Character orders
    orders_result = await db.execute(
        select(CharacterOrder).where(CharacterOrder.state == "active").order_by(
            CharacterOrder.type_id
        )
    )
    char_orders = orders_result.scalars().all()

    # Price lookup results
    price_rows = []
    if search:
        type_result = await db.execute(
            select(SdeType).where(SdeType.name.ilike(f"%{search}%")).limit(20)
        )
        types = type_result.scalars().all()
        for t in types:
            price = await db.get(MarketPrice, t.type_id)
            price_rows.append(
                {
                    "type_id": t.type_id,
                    "name": t.name,
                    "adjusted_price": price.adjusted_price if price else None,
                    "average_price": price.average_price if price else None,
                }
            )

    return templates.TemplateResponse(
        "market.html",
        {
            "request": request,
            "characters": characters,
            "char_map": char_map,
            "char_orders": char_orders,
            "price_rows": price_rows,
            "search": search,
        },
    )


@router.post("/sync-prices", response_class=HTMLResponse)
async def sync_prices(request: Request, db: AsyncSession = Depends(get_db)):
    count = await sync_market_prices(db)
    return HTMLResponse(
        f'<div class="alert alert-success">Synced {count:,} market prices.</div>'
    )


@router.get("/orders/{type_id}", response_class=HTMLResponse)
async def get_jita_orders(
    type_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Fetch live Jita orders from ESI for a specific item."""
    esi = ESIClient(db)
    try:
        orders = await esi.get_paginated(
            f"/markets/{settings.market_region_id}/orders/",
            params={"type_id": type_id, "order_type": "all"},
        )
    finally:
        await esi.close()

    sell_orders = sorted(
        [o for o in orders if not o["is_buy_order"] and o["location_id"] == settings.jita_station_id],
        key=lambda x: x["price"],
    )
    buy_orders = sorted(
        [o for o in orders if o["is_buy_order"] and o["location_id"] == settings.jita_station_id],
        key=lambda x: x["price"],
        reverse=True,
    )

    type_obj = await db.get(SdeType, type_id)
    type_name = type_obj.name if type_obj else f"Type {type_id}"

    best_sell = sell_orders[0]["price"] if sell_orders else None
    best_buy = buy_orders[0]["price"] if buy_orders else None

    return templates.TemplateResponse(
        "partials/order_panel.html",
        {
            "request": request,
            "type_name": type_name,
            "type_id": type_id,
            "best_sell": best_sell,
            "best_buy": best_buy,
            "sell_orders": sell_orders[:10],
            "buy_orders": buy_orders[:10],
        },
    )
