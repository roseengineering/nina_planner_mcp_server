"""Compute mosaic pointings from a coverage request.

A mosaic is specified by the footprint it must cover on the sky — arcminutes,
measured in the grid frame *before* any position-angle rotation — plus the
overlap between adjacent panes. The pane size comes from the rig (the active
N.I.N.A. profile's optics), so the grid (cols x rows) is derived rather than
chosen: the caller never does the tiling arithmetic, and the grid can never
under-cover the request (``grid_size`` rounds UP, so a target needing 2.1
panes gets 3 — one pane of overshoot beats a gap at the target's edge).

``plan_mosaic`` is a pure port of the standalone ``mosaic.sh`` calculator
this module replaces: a symmetric grid about the requested center, stepped by
pane FOV x (1 - overlap), rotated by the position angle, with each pane's
exact sky coordinate found by porting astropy's ``spherical_offsets_by``
(the ``SkyOffsetFrame`` rotation ``mosaic.sh`` relied on) into stdlib
``math`` — no astropy dependency at runtime. A pane center that crosses RA
0h/24h wraps correctly instead of being clamped.
"""

from __future__ import annotations

import math

from .models.pointing import Pointing
from .models.profile import OpticalTrainInfo

__all__ = ["grid_size", "pane_fov_arcmins", "plan_mosaic"]


def pane_fov_arcmins(optics: OpticalTrainInfo) -> tuple[float, float]:
    """The rig's single-pane field of view ``(width, height)`` in arcminutes.

    Derived exactly as ``mosaic.sh`` and ``OpticalTrainInfo._compute_fov``
    do: sensor size in mm from pixels x pixel size, then
    ``2 * atan(sensor / (2 * focal_length))``.

    Raises ``ValueError`` naming every missing value when the profile lacks
    the optics the computation needs — a mosaic sized against a guessed FOV
    would be sized wrong, so there is no default.
    """
    focal_length = optics.focal_length_mm
    pixel_size = optics.pixel_size_microns
    xsize = optics.camera_xsize_px
    ysize = optics.camera_ysize_px
    if not focal_length or not pixel_size or not xsize or not ysize:
        values = {
            "focal_length_mm": focal_length,
            "pixel_size_microns": pixel_size,
            "camera_xsize_px": xsize,
            "camera_ysize_px": ysize,
        }
        missing = [name for name, value in values.items() if not value]
        raise ValueError(
            "cannot compute the mosaic pane FOV: the active profile's optics "
            f"are missing {', '.join(missing)}. Set them in N.I.N.A. "
            "(telescope focal length, camera pixel size and dimensions) "
            "before writing a mosaic plan — a mosaic sized against a guessed "
            "field of view would be sized wrong."
        )

    sensor_width_mm = xsize * pixel_size / 1000.0
    sensor_height_mm = ysize * pixel_size / 1000.0
    fov_w_deg = math.degrees(2.0 * math.atan(sensor_width_mm / (2.0 * focal_length)))
    fov_h_deg = math.degrees(2.0 * math.atan(sensor_height_mm / (2.0 * focal_length)))
    return (fov_w_deg * 60.0, fov_h_deg * 60.0)


def _axis_count(
    coverage_arcmins: float, pane_fov_arcmins_: float, overlap_pct: float
) -> int:
    """Panes needed on one axis to cover ``coverage_arcmins``.

    ``n`` panes span ``pane_fov + (n - 1) * step`` (they overlap, so panes
    beyond the first add a step, not a full pane), so
    ``n = ceil((coverage - pane_fov) / step) + 1``. The ``ceil`` is the
    safety direction: the grid may over-cover by up to one step, but never
    leaves a gap. Coverage that fits within a single pane is 1.
    """
    if coverage_arcmins <= pane_fov_arcmins_:
        return 1
    step = pane_fov_arcmins_ * (1.0 - overlap_pct / 100.0)
    return int(math.ceil((coverage_arcmins - pane_fov_arcmins_) / step)) + 1


def grid_size(
    coverage_width_arcmins: float,
    coverage_height_arcmins: float,
    pane_fov_width_arcmins: float,
    pane_fov_height_arcmins: float,
    overlap_pct: float,
) -> tuple[int, int]:
    """``(cols, rows)`` covering the requested footprint on each axis.

    Each axis is sized independently: width across (columns), height down
    (rows). ``overlap_pct`` is a whole-pane overlap fraction in ``(0, 100)``.
    """
    if not 0.0 < overlap_pct < 100.0:
        raise ValueError(f"overlap_pct must be in (0, 100), got {overlap_pct}")
    if pane_fov_width_arcmins <= 0 or pane_fov_height_arcmins <= 0:
        raise ValueError(
            "pane FOV must be positive, got "
            f"{pane_fov_width_arcmins} x {pane_fov_height_arcmins} arcmins"
        )
    if coverage_width_arcmins <= 0 or coverage_height_arcmins <= 0:
        raise ValueError(
            "coverage must be positive, got "
            f"{coverage_width_arcmins} x {coverage_height_arcmins} arcmins"
        )
    cols = _axis_count(coverage_width_arcmins, pane_fov_width_arcmins, overlap_pct)
    rows = _axis_count(coverage_height_arcmins, pane_fov_height_arcmins, overlap_pct)
    return cols, rows


def _spherical_offset(
    ra0_rad: float, dec0_rad: float, east_deg: float, north_deg: float
) -> tuple[float, float]:
    """``(ra, dec)`` of the point ``east_deg``/``north_deg`` from ``(ra0, dec0)``.

    A faithful port of ``SkyCoord.spherical_offsets_by`` — what ``mosaic.sh``
    called, via its ``SkyOffsetFrame`` implementation — in pure math: build
    the offset as a unit vector of the rotated frame whose origin is the
    center (``sph(east, north)``), then rotate it back to RA/Dec with
    ``Rz(ra0) . Ry(-dec0)``. The two are the same sphere, so north shifts
    declination along the meridian and east along the parallel, composed
    exactly on the sphere rather than by naive addition (which drifts away
    from the equator) or by a single great-circle walk in the combined
    direction (which is what a naive tangent-plane offset does, and it
    disagrees with astropy for combined offsets).

    RA comes back in ``[0, 2*pi)`` by construction (``atan2``), so a pane
    crossing 0h/24h wraps instead of being clamped, and ``asin`` keeps dec
    in range even when an offset carries a pane over the pole.
    """
    lon = math.radians(east_deg)
    lat = math.radians(north_deg)
    sin_ra, cos_ra = math.sin(ra0_rad), math.cos(ra0_rad)
    sin_dec, cos_dec = math.sin(dec0_rad), math.cos(dec0_rad)

    # Unit vector of the offset in the rotated (SkyOffsetFrame) frame.
    cos_lat = math.cos(lat)
    x0 = cos_lat * math.cos(lon)
    y0 = cos_lat * math.sin(lon)
    z0 = math.sin(lat)

    # Ry(-dec0): swing the frame's equator onto the center's declination.
    x1 = x0 * cos_dec - z0 * sin_dec
    y1 = y0
    z1 = x0 * sin_dec + z0 * cos_dec

    # Rz(ra0): swing it onto the center's right ascension.
    x2 = x1 * cos_ra - y1 * sin_ra
    y2 = x1 * sin_ra + y1 * cos_ra
    z2 = z1

    ra = math.atan2(y2, x2) % (2.0 * math.pi)
    dec = math.asin(max(-1.0, min(1.0, z2)))
    return ra, dec


def plan_mosaic(
    center_ra_hours: float,
    center_dec_deg: float,
    cols: int,
    rows: int,
    step_width_deg: float,
    step_height_deg: float,
    pa_deg: float = 0.0,
) -> list[Pointing]:
    """The ``cols x rows`` pane pointings of a mosaic centered on the target.

    Grid layout (port of ``mosaic.sh``): pane centers step by
    ``step_width/height`` degrees, symmetric about the center — an odd count
    puts a pane on the center, an even count straddles it — with row 1 at the
    top (north). The whole grid is then rotated by ``pa_deg`` in the tangent
    plane, so a nonzero PA rotates the footprint on the sky without moving
    its center.

    Pointings come back labeled ``r{row}_c{col}`` (1-based, top-to-bottom,
    left-to-right) with ``row``/``column`` carried as metadata N.I.N.A. does
    not consume — they exist for labeling, ordering, and later mosaic
    assembly — and ``position_angle_deg`` set on every pane (0.0 by default;
    a nonzero value is refused downstream when the profile has no rotator).
    """
    if cols < 1 or rows < 1:
        raise ValueError(f"cols and rows must both be >= 1, got {cols}x{rows}")

    ra0 = math.radians(center_ra_hours * 15.0)
    dec0 = math.radians(center_dec_deg)
    pa = math.radians(pa_deg)

    pointings: list[Pointing] = []
    for r in range(rows):
        for c in range(cols):
            # Offset from the mosaic center in grid coordinates:
            # columns left-to-right, rows top-to-bottom (north first).
            dx = (c - (cols - 1) / 2.0) * step_width_deg
            dy = ((rows - 1) / 2.0 - r) * step_height_deg

            # Rotate the grid by the position angle.
            east = dx * math.cos(pa) - dy * math.sin(pa)
            north = dx * math.sin(pa) + dy * math.cos(pa)

            ra, dec = _spherical_offset(ra0, dec0, east, north)
            pointings.append(
                Pointing(
                    label=f"r{r + 1}_c{c + 1}",
                    ra_hours=round(math.degrees(ra) / 15.0, 6) % 24.0,
                    dec_deg=round(math.degrees(dec), 6),
                    position_angle_deg=round(pa_deg % 360.0, 3),
                    row=r + 1,
                    column=c + 1,
                )
            )
    return pointings
