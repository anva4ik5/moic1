"""Aggregated stats for dashboard."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import require_admin
from ..database import get_db

router = APIRouter()


@router.get(
    "/agents/{agent_id}",
    response_model=schemas.AgentStats,
    dependencies=[Depends(require_admin)],
)
def agent_stats(
    agent_id: int,
    days: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_db),
):
    agent = db.get(models.Agent, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(models.Event)
        .filter(
            models.Event.agent_id == agent_id,
            models.Event.type == "active_window",
            models.Event.ts >= cutoff,
        )
        .all()
    )
    by_app: dict[str, int] = defaultdict(int)
    by_day: dict[str, int] = defaultdict(int)
    for e in rows:
        by_app[e.app or e.process or "unknown"] += e.duration
        by_day[e.ts.date().isoformat()] += e.duration
    by_app_sorted = sorted(by_app.items(), key=lambda kv: kv[1], reverse=True)[:25]
    by_day_sorted = sorted(by_day.items())
    return schemas.AgentStats(
        agent_id=agent_id,
        range_days=days,
        total_seconds=sum(by_app.values()),
        by_app=[schemas.AppUsage(app=k, seconds=v) for k, v in by_app_sorted],
        by_day=[schemas.DailyUsage(date=k, seconds=v) for k, v in by_day_sorted],
    )


@router.get("/overview", dependencies=[Depends(require_admin)])
def overview(db: Session = Depends(get_db)):
    agents = db.query(models.Agent).all()
    cutoff = datetime.utcnow() - timedelta(hours=24)
    online_threshold = datetime.utcnow() - timedelta(seconds=90)
    online = sum(1 for a in agents if a.last_seen >= online_threshold)
    events_24h = (
        db.query(models.Event)
        .filter(models.Event.ts >= cutoff, models.Event.type == "active_window")
        .count()
    )
    shots_24h = (
        db.query(models.Screenshot)
        .filter(models.Screenshot.ts >= cutoff)
        .count()
    )
    return {
        "agents_total": len(agents),
        "agents_online": online,
        "events_24h": events_24h,
        "screenshots_24h": shots_24h,
    }
