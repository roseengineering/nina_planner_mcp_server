from __future__ import annotations

from pydantic import BaseModel


class FlatDevice(BaseModel):
    """The flat panel / motorized cover reported by ``/equipment/info``.

    What matters for the ``cover`` dark/bias variant is
    ``supports_open_close`` — a panel that only lights up
    (``supports_on_off``) cannot cut the light. ``cover_state`` is NINA's
    enum: ``Unknown``, ``NeitherOpenNorClosed``, ``Closed``, ``Open``,
    ``Error``, ``NotPresent``. Every field defaults so a partial or absent
    payload degrades to "no usable cover" (which the variant check rejects)
    instead of failing the equipment-status call.
    """

    connected: bool = False
    name: str = ""
    description: str | None = None
    cover_state: str | None = None
    supports_open_close: bool | None = None
    supports_on_off: bool | None = None
    light_on: bool | None = None
    brightness: int | None = None
