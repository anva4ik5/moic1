"""Pydantic schemas for API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ----- Agent -----

class AgentRegister(BaseModel):
    hostname: str
    username: str
    os_info: str = ""


class AgentRegistered(BaseModel):
    id: int
    token: str


class AgentConsent(BaseModel):
    consented: bool


class AgentOut(BaseModel):
    id: int
    hostname: str
    username: str
    os_info: str
    consented: bool
    created_at: datetime
    last_seen: datetime
    screenshot_interval: int
    tracking_enabled: bool
    block_enabled: bool
    notes: str
    online: bool

    class Config:
        from_attributes = True


class AgentUpdate(BaseModel):
    screenshot_interval: Optional[int] = None
    tracking_enabled: Optional[bool] = None
    block_enabled: Optional[bool] = None
    notes: Optional[str] = None


class AgentConfig(BaseModel):
    """Config returned to agent on heartbeat."""
    screenshot_interval: int
    tracking_enabled: bool
    block_enabled: bool
    blocked_processes: list[str]


# ----- Events -----

class EventIn(BaseModel):
    ts: Optional[datetime] = None
    type: str
    app: str = ""
    title: str = ""
    process: str = ""
    duration: int = 0


class EventBatch(BaseModel):
    events: list[EventIn]


class EventOut(BaseModel):
    id: int
    agent_id: int
    ts: datetime
    type: str
    app: str
    title: str
    process: str
    duration: int

    class Config:
        from_attributes = True


# ----- Screenshots -----

class ScreenshotOut(BaseModel):
    id: int
    agent_id: int
    ts: datetime
    width: int
    height: int
    size_bytes: int
    url: str

    class Config:
        from_attributes = True


# ----- Commands -----

class CommandCreate(BaseModel):
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class CommandOut(BaseModel):
    id: int
    agent_id: int
    type: str
    payload: dict[str, Any]
    status: str
    created_at: datetime
    delivered_at: Optional[datetime]
    completed_at: Optional[datetime]
    result: str

    class Config:
        from_attributes = True


class CommandAck(BaseModel):
    status: str  # done | failed
    result: str = ""


class CommandPending(BaseModel):
    id: int
    type: str
    payload: dict[str, Any]


# ----- Block rules -----

class BlockRuleIn(BaseModel):
    process: str
    reason: str = ""
    enabled: bool = True
    daily_limit_seconds: int = 0
    agent_id: Optional[int] = None


class BlockRuleOut(BaseModel):
    id: int
    agent_id: Optional[int]
    process: str
    reason: str
    enabled: bool
    daily_limit_seconds: int
    created_at: datetime

    class Config:
        from_attributes = True


# ----- Stats -----

class AppUsage(BaseModel):
    app: str
    seconds: int


class DailyUsage(BaseModel):
    date: str
    seconds: int


class AgentStats(BaseModel):
    agent_id: int
    range_days: int
    total_seconds: int
    by_app: list[AppUsage]
    by_day: list[DailyUsage]
