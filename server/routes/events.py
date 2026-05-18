"""Activity events: agents push events, admin reads them."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import require_admin, require_agent
from ..database import get_db

router = APIRouter()


@router.post("/batch")
def submit_batch(
    payload: schemas.EventBatch,
    agent: models.Agent = Depends(require_agent),
    db: Session = Depends(get_db),
):
    if not agent.consented or not agent.tracking_enabled:
        return {"accepted": 0, "reason": "tracking disabled"}
    now = datetime.utcnow()
    rows = []
    for ev in payload.events:
        rows.append(
            models.Event(
                agent_id=agent.id,
                ts=ev.ts or now,
                type=ev.type[:32],
                app=(ev.app or "")[:255],
                title=(ev.title or "")[:512],
                process=(ev.process or "")[:255],
                duration=max(0, int(ev.duration or 0)),
            )
        )
    if rows:
        db.add_all(rows)
        agent.last_seen = now
        db.commit()
    return {"accepted": len(rows)}


@router.get(
    "",
    response_model=list[schemas.EventOut],
    dependencies=[Depends(require_admin)],
)
def list_events(
    agent_id: Optional[int] = None,
    type: Optional[str] = None,
    since_minutes: int = Query(default=24 * 60, ge=1, le=60 * 24 * 30),
    limit: int = Query(default=500, ge=1, le=5000),
    db: Session = Depends(get_db),
):
    cutoff = datetime.utcnow() - timedelta(minutes=since_minutes)
    q = db.query(models.Event).filter(models.Event.ts >= cutoff)
    if agent_id is not None:
        q = q.filter(models.Event.agent_id == agent_id)
    if type:
        q = q.filter(models.Event.type == type)
    return q.order_by(models.Event.ts.desc()).limit(limit).all()
