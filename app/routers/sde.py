from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.sde import SdeBlueprintMaterial, SdeType

router = APIRouter(prefix="/sde", tags=["sde"])
templates = Jinja2Templates(directory="app/templates")

# Simple progress tracking for the background import
_import_status: dict = {"running": False, "progress": "", "error": ""}


@router.get("", response_class=HTMLResponse)
async def sde_page(request: Request, db: AsyncSession = Depends(get_db)):
    type_count_result = await db.execute(select(func.count(SdeType.type_id)))
    type_count = type_count_result.scalar() or 0

    mat_count_result = await db.execute(
        select(func.count()).select_from(SdeBlueprintMaterial)
    )
    mat_count = mat_count_result.scalar() or 0

    return templates.TemplateResponse(
        "sde.html",
        {
            "request": request,
            "type_count": type_count,
            "mat_count": mat_count,
            "import_status": _import_status,
        },
    )


@router.post("/import", response_class=HTMLResponse)
async def start_import(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    if _import_status["running"]:
        return HTMLResponse(
            '<div class="alert alert-warning">Import already running. Check status below.</div>'
        )

    background_tasks.add_task(_run_import)
    return HTMLResponse(
        '<div class="alert alert-info">SDE import started in background. '
        'Refresh this page to check progress.</div>'
    )


@router.get("/status", response_class=HTMLResponse)
async def import_status(request: Request):
    s = _import_status
    if s["error"]:
        return HTMLResponse(f'<div class="alert alert-error">Error: {s["error"]}</div>')
    if s["running"]:
        return HTMLResponse(
            f'<div class="alert alert-info" hx-get="/sde/status" hx-trigger="every 3s">'
            f"Importing: {s['progress']}</div>"
        )
    if s["progress"]:
        return HTMLResponse(
            f'<div class="alert alert-success">Complete: {s["progress"]}</div>'
        )
    return HTMLResponse("")


async def _run_import():
    from sde.importer import run_import

    _import_status["running"] = True
    _import_status["error"] = ""
    _import_status["progress"] = "Starting..."
    try:
        await run_import(_import_status)
    except Exception as exc:
        _import_status["error"] = str(exc)
    finally:
        _import_status["running"] = False
