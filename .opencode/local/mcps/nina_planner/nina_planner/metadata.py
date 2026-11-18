"""Resolve the live imaging root and read its frame-attribution metadata."""

from pathlib import Path
from typing import Any

from .imaging import read_imaging_csv
from .tools import equipment as equipment_tools


async def resolve_imaging_root() -> Path:
    """The site profile's image save path, mapped for this host.

    Raises:
        Exception: N.I.N.A. could not be reached to read the profile.
    """
    profile = await equipment_tools.get_site_profile()
    return Path(profile.local_image_save_path)


async def read_metadata(image_type: str | None = None) -> list[dict[str, Any]]:
    """Attributed frame rows from every visible ``ImageMetaData.csv``.

    Args:
        image_type: Restrict to one frame type (``light``/``dark``/``bias``/``flat``);
            ``None`` matches all four in a single tree walk.

    Raises:
        FileNotFoundError: The imaging root does not exist on this host.
        Exception: N.I.N.A. could not be reached to read the profile.
    """
    root = await resolve_imaging_root()
    return read_imaging_csv(root=root, image_type=image_type)
