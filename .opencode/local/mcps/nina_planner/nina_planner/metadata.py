"""Resolve the live imaging root and read its frame-attribution metadata."""

from pathlib import Path
from typing import Any

import anyio

from .imaging import read_imaging_csv
from .tools import equipment as equipment_tools


async def resolve_imaging_root() -> Path:
    """The site profile's image save path, mapped for this host.

    Raises:
        Exception: N.I.N.A. could not be reached to read the profile.
    """
    profile = await equipment_tools.get_site_profile()
    return Path(profile.local_image_save_path)


async def resolve_cli_root(
    imaging_root: str | None, error: type[Exception], command: str
) -> Path:
    """The imaging root for a CLI run: ``--imaging-root`` if given, else NINA's.

    Args:
        imaging_root: The ``--imaging-root`` value, or ``None``.
        error: Exception class raised for every resolution failure.
        command: CLI name used in the hint (``archive``, ``purge``).

    Raises:
        error: The override is not a directory, or NINA's profile is unreachable.
    """
    if imaging_root:
        root = Path(imaging_root).expanduser()
        if not root.is_dir():
            raise error(f"--imaging-root does not exist or is not a directory: {root}")
        return root
    try:
        return await resolve_imaging_root()
    except Exception as exc:  # noqa: BLE001 - surface any NINA/profile failure
        raise error(
            "cannot resolve the imaging root from the N.I.N.A. site profile "
            f"({exc}). Is NINA running? Pass --imaging-root to {command} "
            "without NINA."
        ) from exc


async def read_metadata(image_type: str | None = None) -> list[dict[str, Any]]:
    """Attributed frame rows from every visible ``ImageMetaData.csv``.

    The CSV walk is blocking I/O, so it runs in a worker thread.

    Args:
        image_type: Restrict to one frame type (``light``/``dark``/``bias``/``flat``);
            ``None`` matches all four in a single tree walk.

    Raises:
        FileNotFoundError: The imaging root does not exist on this host.
        Exception: N.I.N.A. could not be reached to read the profile.
    """
    root = await resolve_imaging_root()
    return await anyio.to_thread.run_sync(
        lambda: read_imaging_csv(root=root, image_type=image_type)
    )
