"""Auth helpers: admin token + agent token."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from . import models
from .database import BASE_DIR, get_db

ADMIN_TOKEN_FILE = BASE_DIR / "data" / "admin_token.txt"


def get_admin_token() -> str:
    token = os.environ.get("MONITOR_ADMIN_TOKEN")
    if token:
        return token
    if ADMIN_TOKEN_FILE.exists():
        return ADMIN_TOKEN_FILE.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(24)
    ADMIN_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    ADMIN_TOKEN_FILE.write_text(token, encoding="utf-8")
    return token


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    expected = get_admin_token()
    if not x_admin_token or not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin token")


def require_agent(
    x_agent_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> models.Agent:
    if not x_agent_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing agent token")
    agent = db.query(models.Agent).filter(models.Agent.token == x_agent_token).first()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent token")
    return agent


def new_agent_token() -> str:
    return secrets.token_urlsafe(32)
