from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.engine.manufacturing import get_all_manufacturing_options
from app.models.character import Character
from app.models.industry import IndustryJob
from app.models.sde import SdeType

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

    # Resolve type names
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
        job_rows.append(
            {
                "job": job,
                "bp_name": bp_type.name if bp_type else f"Type {job.blueprint_type_id}",
                "prod_name": prod_type.name if prod_type else "",
                "activity": ACTIVITY_NAMES.get(job.activity_id, f"Activity {job.activity_id}"),
                "char_name": char_map.get(job.character_id, str(job.character_id)),
                "time_remaining": time_remaining,
            }
        )

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
    db: AsyncSession = Depends(get_db),
):
    chars_result = await db.execute(select(Character).order_by(Character.name))
    characters = chars_result.scalars().all()
    character_ids = [c.character_id for c in characters]

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
            )
        except Exception as exc:
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
            "sde_available": sde_available,
            "sde_error": sde_error,
            "total": len(options),
            "buildable_count": sum(1 for o in options if o.can_build_now),
        },
    )
