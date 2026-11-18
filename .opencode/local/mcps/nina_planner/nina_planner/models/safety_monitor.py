"""Safety-monitor device model: the enclosure's is_safe gate."""

from __future__ import annotations

from pydantic import BaseModel


class SafetyMonitorDevice(BaseModel):
    """The site's safety monitor as ``/equipment/info`` reports it.

    ``is_safe`` is the enclosure gate (roof/dome open, safe to unpark and
    expose); ``None`` means the device did not report it, which every caller
    must treat as unsafe.
    """

    connected: bool
    name: str
    description: str | None = None
    is_safe: bool | None = None
