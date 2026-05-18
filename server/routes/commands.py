"""Remote command queue: admin queues, agent polls/acks."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import require_admin, require_agent
from ..database import get_db

router = APIRouter()

ALLOWED_TYPES = {
    "lock",          # lock workstation
    "logoff",        # log off current user
    "shutdown",      # shutdown
    "restart",       # restart
    "message",       # show MessageBox - payload: {"text": "...", "title": "..."}
    "kill",          # kill process - payload: {"process": "name.exe"}
    "set_interval",  # change screenshot interval - payload: {"seconds": N}
    "reload_rules",  # force agent to refresh blocklist
    "screenshot_now",  # take screenshot immediately (only if consented + interval>0)
}


@router.post(
    "/agents/{agent_id}",
    response_model=schemas.CommandOut,
    dependencies=[Depends(require_admin)],
)
def queue_command(
    agent_id: int,
    payload: schemas.CommandCreate,
    db: Session = Depends(get_db),
):
    if payload.type not in ALLOWED_TYPES:
        raise HTTPException(400, f"Unknown command type: {payload.type}")
    agent = db.get(models.Agent, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    cmd = models.Command(
        agent_id=agent_id,
        type=payload.type,
        payload=json.dumps(payload.payload or {}),
    )
    db.add(cmd)
    db.commit()
    db.refresh(cmd)
    return _to_out(cmd)


@router.get(
    "/agents/{agent_id}",
    response_model=list[schemas.CommandOut],
    dependencies=[Depends(require_admin)],
)
def list_commands(agent_id: int, limit: int = 100, db: Session = Depends(get_db)):
    rows = (
        db.query(models.Command)
        .filter(models.Command.agent_id == agent_id)
        .order_by(models.Command.created_at.desc())
        .limit(limit)
        .all()
    )
    return [_to_out(c) for c in rows]


@router.get("/poll", response_model=list[schemas.CommandPending])
def poll(agent: models.Agent = Depends(require_agent), db: Session = Depends(get_db)):
    rows = (
        db.query(models.Command)
        .filter(models.Command.agent_id == agent.id, models.Command.status == "pending")
        .order_by(models.Command.created_at.asc())
        .limit(20)
        .all()
    )
    out: list[schemas.CommandPending] = []
    now = datetime.utcnow()
    for c in rows:
        c.status = "delivered"
        c.delivered_at = now
        try:
            payload = json.loads(c.payload or "{}")
        except json.JSONDecodeError:
            payload = {}
        out.append(schemas.CommandPending(id=c.id, type=c.type, payload=payload))
    agent.last_seen = now
    db.commit()
    return out


@router.post("/{cmd_id}/ack")
def ack(
    cmd_id: int,
    payload: schemas.CommandAck,
    agent: models.Agent = Depends(require_agent),
    db: Session = Depends(get_db),
):
    cmd = db.get(models.Command, cmd_id)
    if not cmd or cmd.agent_id != agent.id:
        raise HTTPException(404, "Command not found")
    cmd.status = payload.status if payload.status in ("done", "failed") else "failed"
    cmd.completed_at = datetime.utcnow()
    cmd.result = (payload.result or "")[:1000]
    db.commit()
    return {"ok": True}


def _to_out(cmd: models.Command) -> schemas.CommandOut:
    try:
        payload = json.loads(cmd.payload or "{}")
    except json.JSONDecodeError:
        payload = {}
    return schemas.CommandOut(
        id=cmd.id,
        agent_id=cmd.agent_id,
        type=cmd.type,
        payload=payload,
        status=cmd.status,
        created_at=cmd.created_at,
        delivered_at=cmd.delivered_at,
        completed_at=cmd.completed_at,
        result=cmd.result,
    )
