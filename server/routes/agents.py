"""Agent registration, heartbeat, listing, config."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import new_agent_token, require_admin, require_agent
from ..database import get_db

router = APIRouter()

ONLINE_WINDOW_SECONDS = 90


def _is_online(agent: models.Agent) -> bool:
    return (datetime.utcnow() - agent.last_seen).total_seconds() < ONLINE_WINDOW_SECONDS


def _to_out(agent: models.Agent) -> schemas.AgentOut:
    return schemas.AgentOut(
        id=agent.id,
        hostname=agent.hostname,
        username=agent.username,
        os_info=agent.os_info,
        consented=agent.consented,
        created_at=agent.created_at,
        last_seen=agent.last_seen,
        screenshot_interval=agent.screenshot_interval,
        tracking_enabled=agent.tracking_enabled,
        block_enabled=agent.block_enabled,
        notes=agent.notes,
        online=_is_online(agent),
    )


@router.post("/register", response_model=schemas.AgentRegistered)
def register(payload: schemas.AgentRegister, db: Session = Depends(get_db)):
    """Open endpoint - any new agent can register itself.

    The admin then has to review and either keep or delete the agent.
    Until consent is recorded, the agent will refuse to track.
    """
    agent = models.Agent(
        token=new_agent_token(),
        hostname=payload.hostname[:255],
        username=payload.username[:255],
        os_info=payload.os_info[:255],
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return schemas.AgentRegistered(id=agent.id, token=agent.token)


@router.post("/consent")
def consent(payload: schemas.AgentConsent, agent: models.Agent = Depends(require_agent), db: Session = Depends(get_db)):
    agent.consented = bool(payload.consented)
    agent.consented_at = datetime.utcnow() if payload.consented else None
    db.commit()
    return {"ok": True, "consented": agent.consented}


@router.post("/heartbeat", response_model=schemas.AgentConfig)
def heartbeat(agent: models.Agent = Depends(require_agent), db: Session = Depends(get_db)):
    agent.last_seen = datetime.utcnow()
    rules = (
        db.query(models.BlockRule)
        .filter(
            models.BlockRule.enabled.is_(True),
            (models.BlockRule.agent_id == agent.id) | (models.BlockRule.agent_id.is_(None)),
            models.BlockRule.daily_limit_seconds == 0,  # only hard blocks here
        )
        .all()
    )
    blocked = sorted({r.process.lower() for r in rules})
    db.commit()
    return schemas.AgentConfig(
        screenshot_interval=agent.screenshot_interval if agent.consented else 0,
        tracking_enabled=agent.tracking_enabled and agent.consented,
        block_enabled=agent.block_enabled,
        blocked_processes=blocked,
    )


@router.get("", response_model=list[schemas.AgentOut], dependencies=[Depends(require_admin)])
def list_agents(db: Session = Depends(get_db)):
    agents = db.query(models.Agent).order_by(models.Agent.last_seen.desc()).all()
    return [_to_out(a) for a in agents]


@router.get("/{agent_id}", response_model=schemas.AgentOut, dependencies=[Depends(require_admin)])
def get_agent(agent_id: int, db: Session = Depends(get_db)):
    agent = db.get(models.Agent, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    return _to_out(agent)


@router.patch("/{agent_id}", response_model=schemas.AgentOut, dependencies=[Depends(require_admin)])
def update_agent(agent_id: int, payload: schemas.AgentUpdate, db: Session = Depends(get_db)):
    agent = db.get(models.Agent, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    if payload.screenshot_interval is not None:
        agent.screenshot_interval = max(0, int(payload.screenshot_interval))
    if payload.tracking_enabled is not None:
        agent.tracking_enabled = bool(payload.tracking_enabled)
    if payload.block_enabled is not None:
        agent.block_enabled = bool(payload.block_enabled)
    if payload.notes is not None:
        agent.notes = payload.notes
    db.commit()
    db.refresh(agent)
    return _to_out(agent)


@router.delete("/{agent_id}", dependencies=[Depends(require_admin)])
def delete_agent(agent_id: int, db: Session = Depends(get_db)):
    agent = db.get(models.Agent, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    db.delete(agent)
    db.commit()
    return {"ok": True}
