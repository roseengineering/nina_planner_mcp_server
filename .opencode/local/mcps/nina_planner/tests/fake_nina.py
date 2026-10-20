"""In-process fake of N.I.N.A.'s REST sequence API, served over httpx.MockTransport.

Unlike patching ``api_get``/``api_post``, the real client code runs: URLs,
JSON bodies and the ``Success``/``Error`` envelope all cross a genuine HTTP
request/response boundary.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import httpx

_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _envelope(response: Any = None, *, error: str = "", status: int = 200):
    body = {
        "Response": response if response is not None else "",
        "Error": error,
        "StatusCode": status,
        "Success": not error,
        "Type": "API",
    }
    return httpx.Response(status, json=body)


class FakeNina:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.loaded: dict[str, Any] | None = None
        self.running = False

    def _state(self) -> dict[str, Any]:
        return {"Status": "RUNNING" if self.running else "CREATED"}

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v2/api")
        self.requests.append((request.method, path))

        if path == "/sequence/load" and request.method == "POST":
            if self.running:
                return _envelope(error="Sequence is running", status=409)
            self.loaded = json.loads(request.content)
            return _envelope("Sequence loaded")
        if path == "/sequence/start":
            if self.loaded is None:
                return _envelope(error="No sequence loaded", status=409)
            self.running = True
            return _envelope("Sequence started")
        if path == "/sequence/stop":
            self.running = False
            return _envelope("Sequence stopped")
        if path in ("/sequence/json", "/sequence/state"):
            return _envelope(self._state())
        return _envelope(error=f"Unknown endpoint {path}", status=404)

    @property
    def posts(self) -> list[str]:
        return [path for method, path in self.requests if method == "POST"]

    @contextmanager
    def installed(self):
        """Route every ``httpx.AsyncClient`` the server creates to this fake."""
        transport = httpx.MockTransport(self.handle)

        def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            return _REAL_ASYNC_CLIENT(*args, transport=transport, **kwargs)

        with patch("nina_planner.client.httpx.AsyncClient", factory):
            yield self
