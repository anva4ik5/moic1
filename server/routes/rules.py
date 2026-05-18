"""Block rules CRUD."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import require_admin
from ..database import get_db

router = APIRouter()


@router.get(
    "",
    response_model=list[schemas.BlockRuleOut],
    dependencies=[Depends(require_admin)],
)
def list_rules(db: Session = Depends(get_db)):
    return db.query(models.BlockRule).order_by(models.BlockRule.id.desc()).all()


@router.post(
    "",
    response_model=schemas.BlockRuleOut,
    dependencies=[Depends(require_admin)],
)
def create_rule(payload: schemas.BlockRuleIn, db: Session = Depends(get_db)):
    rule = models.BlockRule(
        agent_id=payload.agent_id,
        process=payload.process.lower().strip(),
        reason=payload.reason,
        enabled=payload.enabled,
        daily_limit_seconds=max(0, int(payload.daily_limit_seconds)),
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.patch(
    "/{rule_id}",
    response_model=schemas.BlockRuleOut,
    dependencies=[Depends(require_admin)],
)
def update_rule(rule_id: int, payload: schemas.BlockRuleIn, db: Session = Depends(get_db)):
    rule = db.get(models.BlockRule, rule_id)
    if not rule:
        raise HTTPException(404, "Not found")
    rule.agent_id = payload.agent_id
    rule.process = payload.process.lower().strip()
    rule.reason = payload.reason
    rule.enabled = payload.enabled
    rule.daily_limit_seconds = max(0, int(payload.daily_limit_seconds))
    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/{rule_id}", dependencies=[Depends(require_admin)])
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.get(models.BlockRule, rule_id)
    if not rule:
        raise HTTPException(404, "Not found")
    db.delete(rule)
    db.commit()
    return {"ok": True}
