from __future__ import annotations

from pydantic import BaseModel


class SafetyMonitorDevice(BaseModel):
    connected: bool
    name: str
    description: str | None = None
    is_safe: bool | None = None
