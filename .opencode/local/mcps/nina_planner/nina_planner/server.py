"""MCP entry point: importing the tool modules registers their tools on ``mcp``."""

from .app import mcp
from .tools import equipment, lifecycle, plans, sequence, sky
from .tools.equipment import (
    get_events,
    get_logs,
    get_site_equipment_status,
    get_site_profile,
    list_site_profiles,
    screenshot_dashboard,
    switch_site_profile,
)
from .tools.lifecycle import get_nina_status, start_nina, stop_nina
from .tools.plans import (
    get_imaging_metadata,
    get_plan_progress,
    write_mosaic_plan,
    write_plan_file,
)
from .tools.sequence import (
    get_sequence_state,
    load_sequence,
    start_sequence,
    stop_sequence,
)
from .tools.sky import resolve_target, target_altitudes

__all__ = [
    "equipment",
    "get_events",
    "get_imaging_metadata",
    "get_logs",
    "get_nina_status",
    "get_plan_progress",
    "get_sequence_state",
    "get_site_equipment_status",
    "get_site_profile",
    "lifecycle",
    "list_site_profiles",
    "load_sequence",
    "mcp",
    "plans",
    "resolve_target",
    "screenshot_dashboard",
    "sequence",
    "sky",
    "start_nina",
    "start_sequence",
    "stop_nina",
    "stop_sequence",
    "switch_site_profile",
    "target_altitudes",
    "write_mosaic_plan",
    "write_plan_file",
]


if __name__ == "__main__":
    mcp.run()
