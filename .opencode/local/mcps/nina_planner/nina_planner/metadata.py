from pathlib import Path
from typing import Any

from .imaging import read_imaging_csv
from .tools import equipment as equipment_tools


async def resolve_imaging_root() -> Path:
    profile = await equipment_tools.get_site_profile()
    return Path(profile.local_image_save_path)


async def read_metadata(image_type: str | None = None) -> list[dict[str, Any]]:
    root = await resolve_imaging_root()
    if image_type is not None:
        return read_imaging_csv(root=root, image_type=image_type)
    rows: list[dict[str, Any]] = []
    for t in ("light", "dark", "bias", "flat"):
        rows += read_imaging_csv(root=root, image_type=t)
    return rows


### mcp tools
