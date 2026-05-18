"""SQLAlchemy models."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from .database import Base


class Agent(Base):
    __tablename__ = "agents"

    id = Column(Integer, primary_key=True)
    token = Column(String(64), unique=True, nullable=False, index=True)
    hostname = Column(String(255), nullable=False)
    username = Column(String(255), nullable=False)
    os_info = Column(String(255), nullable=False, default="")
    consented = Column(Boolean, nullable=False, default=False)
    consented_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen = Column(DateTime, nullable=False, default=datetime.utcnow)

    screenshot_interval = Column(Integer, nullable=False, default=0)  # 0 = off
    tracking_enabled = Column(Boolean, nullable=False, default=True)
    block_enabled = Column(Boolean, nullable=False, default=True)
    notes = Column(Text, nullable=False, default="")

    events = relationship("Event", back_populates="agent", cascade="all, delete-orphan")
    screenshots = relationship("Screenshot", back_populates="agent", cascade="all, delete-orphan")
    commands = relationship("Command", back_populates="agent", cascade="all, delete-orphan")
    rules = relationship("BlockRule", back_populates="agent", cascade="all, delete-orphan")


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    ts = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    type = Column(String(32), nullable=False, index=True)  # active_window, idle, app_block, app_start, app_stop
    app = Column(String(255), nullable=False, default="")
    title = Column(String(512), nullable=False, default="")
    process = Column(String(255), nullable=False, default="")
    duration = Column(Integer, nullable=False, default=0)  # seconds

    agent = relationship("Agent", back_populates="events")


class Screenshot(Base):
    __tablename__ = "screenshots"

    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    ts = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    path = Column(String(512), nullable=False)
    width = Column(Integer, nullable=False, default=0)
    height = Column(Integer, nullable=False, default=0)
    size_bytes = Column(Integer, nullable=False, default=0)

    agent = relationship("Agent", back_populates="screenshots")


class Command(Base):
    __tablename__ = "commands"

    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    type = Column(String(32), nullable=False)  # lock, message, kill, logoff, shutdown, restart, set_interval, reload_rules
    payload = Column(Text, nullable=False, default="{}")  # JSON
    status = Column(String(16), nullable=False, default="pending", index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    delivered_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    result = Column(Text, nullable=False, default="")

    agent = relationship("Agent", back_populates="commands")


class BlockRule(Base):
    __tablename__ = "block_rules"

    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=True, index=True)  # null = global
    process = Column(String(255), nullable=False)  # exe name, case-insensitive
    reason = Column(String(255), nullable=False, default="")
    enabled = Column(Boolean, nullable=False, default=True)
    daily_limit_seconds = Column(Integer, nullable=False, default=0)  # 0 = always block
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    agent = relationship("Agent", back_populates="rules")


class Setting(Base):
    __tablename__ = "settings"

    key = Column(String(64), primary_key=True)
    value = Column(Text, nullable=False, default="")
