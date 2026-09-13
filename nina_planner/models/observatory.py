from __future__ import annotations

from pydantic import BaseModel

from .camera import CameraDevice
from .dome import DomeDevice
from .filter_wheel import FilterWheelDevice
from .focuser import FocuserDevice
from .guider import GuiderDevice
from .mount import MountDevice
from .rotator import RotatorDevice
from .safety_monitor import SafetyMonitorDevice
from .weather import Weather


class ObservatoryEquipment(BaseModel):
    mount: MountDevice | None = None
    camera: CameraDevice | None = None
    focuser: FocuserDevice | None = None
    guider: GuiderDevice | None = None
    filter_wheel: FilterWheelDevice | None = None
    rotator: RotatorDevice | None = None
    dome: DomeDevice | None = None
    weather: Weather | None = None
    safety_monitor: SafetyMonitorDevice | None = None
