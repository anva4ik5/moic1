"""Thin HTTP client for the Monitor Suite server."""
from __future__ import annotations

import logging
from typing import Any, Optional

import requests

log = logging.getLogger("monitor.agent.api")


class APIError(Exception):
    pass


class ServerClient:
    def __init__(self, server_url: str, token: Optional[str] = None, timeout: float = 10.0):
        self.server_url = server_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._session = requests.Session()

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["X-Agent-Token"] = self.token
        return h

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.server_url}{path}"
        kwargs.setdefault("timeout", self.timeout)
        # Use our headers but allow override (e.g. multipart upload removes Content-Type)
        headers = {**self._headers(), **kwargs.pop("headers", {})}
        resp = self._session.request(method, url, headers=headers, **kwargs)
        if resp.status_code >= 400:
            raise APIError(f"{method} {path} -> {resp.status_code}: {resp.text[:200]}")
        if resp.status_code == 204 or not resp.content:
            return None
        ctype = resp.headers.get("Content-Type", "")
        if "application/json" in ctype:
            return resp.json()
        return resp.content

    # ----- agent endpoints -----

    def register(self, hostname: str, username: str, os_info: str) -> dict:
        return self._request(
            "POST",
            "/api/agents/register",
            json={"hostname": hostname, "username": username, "os_info": os_info},
        )

    def consent(self, value: bool) -> dict:
        return self._request("POST", "/api/agents/consent", json={"consented": value})

    def heartbeat(self) -> dict:
        return self._request("POST", "/api/agents/heartbeat")

    def submit_events(self, events: list[dict]) -> dict:
        return self._request("POST", "/api/events/batch", json={"events": events})

    def upload_screenshot(self, jpeg_bytes: bytes) -> dict:
        url = f"{self.server_url}/api/screenshots/upload"
        headers = {"X-Agent-Token": self.token or ""}
        files = {"file": ("shot.jpg", jpeg_bytes, "image/jpeg")}
        resp = self._session.post(url, headers=headers, files=files, timeout=self.timeout)
        if resp.status_code >= 400:
            raise APIError(f"upload -> {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def poll_commands(self) -> list[dict]:
        return self._request("GET", "/api/commands/poll") or []

    def ack_command(self, cmd_id: int, status: str, result: str = "") -> None:
        self._request(
            "POST", f"/api/commands/{cmd_id}/ack", json={"status": status, "result": result}
        )
