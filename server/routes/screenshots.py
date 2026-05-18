"""Screenshot upload/list/serve."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import require_admin, require_agent
from ..database import BASE_DIR, get_db

router = APIRouter()

SCREENSHOT_DIR = BASE_DIR / "data" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


@router.post("/upload")
async def upload_screenshot(
    file: UploadFile = File(...),
    agent: models.Agent = Depends(require_agent),
    db: Session = Depends(get_db),
):
    if not agent.consented or agent.screenshot_interval <= 0:
        raise HTTPException(403, "Screenshots disabled for this agent")

    now = datetime.utcnow()
    agent_dir = SCREENSHOT_DIR / str(agent.id)
    agent_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{now.strftime('%Y%m%d_%H%M%S')}_{int(now.timestamp() * 1000) % 1000:03d}.jpg"
    fpath = agent_dir / fname
    data = await file.read()
    fpath.write_bytes(data)

    shot = models.Screenshot(
        agent_id=agent.id,
        ts=now,
        path=str(fpath.relative_to(BASE_DIR).as_posix()),
        size_bytes=len(data),
    )
    db.add(shot)
    agent.last_seen = now
    db.commit()
    db.refresh(shot)
    return {"id": shot.id}


@router.get(
    "",
    response_model=list[schemas.ScreenshotOut],
    dependencies=[Depends(require_admin)],
)
def list_screenshots(
    agent_id: Optional[int] = None,
    since_minutes: int = Query(default=24 * 60, ge=1, le=60 * 24 * 30),
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    cutoff = datetime.utcnow() - timedelta(minutes=since_minutes)
    q = db.query(models.Screenshot).filter(models.Screenshot.ts >= cutoff)
    if agent_id is not None:
        q = q.filter(models.Screenshot.agent_id == agent_id)
    rows = q.order_by(models.Screenshot.ts.desc()).limit(limit).all()
    return [
        schemas.ScreenshotOut(
            id=r.id,
            agent_id=r.agent_id,
            ts=r.ts,
            width=r.width,
            height=r.height,
            size_bytes=r.size_bytes,
            url=f"/api/screenshots/{r.id}/file",
        )
        for r in rows
    ]


@router.get("/{shot_id}/file", dependencies=[Depends(require_admin)])
def get_file(shot_id: int, db: Session = Depends(get_db)):
    shot = db.get(models.Screenshot, shot_id)
    if not shot:
        raise HTTPException(404, "Not found")
    full = BASE_DIR / shot.path
    if not full.exists():
        raise HTTPException(404, "File missing")
    return FileResponse(full, media_type="image/jpeg")


@router.delete("/{shot_id}", dependencies=[Depends(require_admin)])
def delete_shot(shot_id: int, db: Session = Depends(get_db)):
    shot = db.get(models.Screenshot, shot_id)
    if not shot:
        raise HTTPException(404, "Not found")
    full = BASE_DIR / shot.path
    if full.exists():
        try:
            full.unlink()
        except OSError:
            pass
    db.delete(shot)
    db.commit()
    return {"ok": True}
