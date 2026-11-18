"""Aggregate model of every device N.I.N.A. reports for the rig."""

from __future__ import annotations

from pydantic import BaseModel

from .camera import CameraDevice
from .dome import DomeDevice
from .filter_wheel import FilterWheelDevice
from .flat_device import FlatDevice
from .focuser import FocuserDevice
from .guider import GuiderDevice
from .mount import MountDevice
from .rotator import RotatorDevice
from .safety_monitor import SafetyMonitorDevice
from .switch import Switch
from .weather import Weather


class ObservatoryEquipment(BaseModel):
    """The rig's devices as returned by ``GET /equipment/info``.

    Every device is ``None`` when the profile does not include it (or it is
    not connected), so capability guards read ``equipment.mount is None``
    rather than checking connection state twice.
    """

    mount: MountDevice | None = None
    camera: CameraDevice | None = None
    focuser: FocuserDevice | None = None
    guider: GuiderDevice | None = None
    filter_wheel: FilterWheelDevice | None = None
    flat_device: FlatDevice | None = None
    rotator: RotatorDevice | None = None
    dome: DomeDevice | None = None
    weather: Weather | None = None
    safety_monitor: SafetyMonitorDevice | None = None
    switch: Switch | None = None
