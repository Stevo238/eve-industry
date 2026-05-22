from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.database import init_db
from app.routers import assets, auth, blueprints, characters, industry, market, sde, settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="EVE Industry Manager", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")

app.include_router(auth.router)
app.include_router(characters.router)
app.include_router(assets.router)
app.include_router(blueprints.router)
app.include_router(industry.router)
app.include_router(market.router)
app.include_router(sde.router)
app.include_router(settings.router)


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return RedirectResponse("/dashboard")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    from sqlalchemy import func, select

    from app.database import AsyncSessionLocal
    from app.models.blueprints import Blueprint
    from app.models.character import Character
    from app.models.industry import IndustryJob
    from app.models.assets import Asset

    async with AsyncSessionLocal() as db:
        chars_result = await db.execute(select(Character).order_by(Character.name))
        characters = chars_result.scalars().all()

        active_jobs_result = await db.execute(
            select(func.count(IndustryJob.job_id)).where(
                IndustryJob.status.in_(["active", "ready", "paused"])
            )
        )
        active_jobs = active_jobs_result.scalar() or 0

        bp_result = await db.execute(select(func.count(Blueprint.item_id)))
        total_bps = bp_result.scalar() or 0

        asset_result = await db.execute(select(func.count(Asset.item_id)))
        total_assets = asset_result.scalar() or 0

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "characters": characters,
            "active_jobs": active_jobs,
            "total_blueprints": total_bps,
            "total_assets": total_assets,
        },
    )
