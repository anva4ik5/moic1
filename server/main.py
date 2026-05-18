"""Monitor Suite server entry point.

Run:
    uvicorn server.main:app --host 0.0.0.0 --port 8765

The dashboard is served at /  (static files from ../dashboard).
The admin token is printed to console on first start; also stored in
server/data/admin_token.txt.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .auth import get_admin_token
from .database import BASE_DIR, init_db
from .routes import agents, commands, events, rules, screenshots, stats

DASHBOARD_DIR = BASE_DIR.parent / "dashboard"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("monitor.server")

app = FastAPI(title="Monitor Suite", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agents.router, prefix="/api/agents", tags=["agents"])
app.include_router(events.router, prefix="/api/events", tags=["events"])
app.include_router(screenshots.router, prefix="/api/screenshots", tags=["screenshots"])
app.include_router(commands.router, prefix="/api/commands", tags=["commands"])
app.include_router(rules.router, prefix="/api/rules", tags=["rules"])
app.include_router(stats.router, prefix="/api/stats", tags=["stats"])


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    token = get_admin_token()
    log.info("=" * 60)
    log.info("Monitor Suite ready")
    log.info("Admin token: %s", token)
    log.info("Dashboard:   http://localhost:8765/")
    log.info("Use this token to log in. Override via env var MONITOR_ADMIN_TOKEN.")
    log.info("=" * 60)


@app.get("/api/health")
def health():
    return {"status": "ok"}


if DASHBOARD_DIR.exists():
    app.mount("/", StaticFiles(directory=str(DASHBOARD_DIR), html=True), name="dashboard")
else:
    @app.get("/", response_class=HTMLResponse)
    def fallback():
        return "<h1>Dashboard not found</h1><p>Expected at " + str(DASHBOARD_DIR) + "</p>"
