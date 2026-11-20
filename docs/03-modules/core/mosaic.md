# `nina_planner/mosaic.py` — Mosaic Pane FOV, Ceiling Grid Sizing, Spherical Offset Rotation & Pointing Generation

[⬆️ Up to Atlas Index](../../README.md) · [⬆️ Core Package](./README.md) · **Callers:** [`tools/plans.py`](../tools/plans.md) · **Callees:** [`models/pointing.py` (`Pointing`)](../models/pointing-and-frame.md), [`models/profile.py` (`OpticalTrainInfo`)](../models/profile.md)

---

## 1. Module Overview

- **File Path:** `.opencode/local/mcps/nina_planner/nina_planner/mosaic.py`
- **Line Count:** 200 lines
- **Module Docstring & Purpose (`mosaic.py:1-17`):**
  > *"Compute mosaic pointings from a coverage request. A mosaic is specified by the footprint it must cover on the sky — arcminutes, measured in the grid frame before any position-angle rotation — plus the overlap between adjacent panes. The pane size comes from the rig (the active N.I.N.A. profile's optics), so the grid (cols x rows) is derived rather than chosen: the caller never does the tiling arithmetic, and the grid can never under-cover the request (`grid_size` rounds UP, so a target needing 2.1 panes gets 3 — one pane of overshoot beats a gap at the target's edge). `plan_mosaic` lays out a symmetric grid about the requested center, stepped by pane FOV x (1 - overlap) and rotated by the position angle, with each pane's exact sky coordinate found via the same rotation astropy's `spherical_offsets_by` performs (`SkyOffsetFrame`) — ported into stdlib `math` here, so there is no astropy dependency at runtime. A pane center that crosses RA 0h/24h wraps correctly instead of being clamped."*
- **Architectural Layer:** Core Domain Service / Spherical Geometry Engine (`nina_planner/`). Pure-Python (`math`) implementation of rig FOV extraction (`pane_fov_arcmins`), ceiling-based tiling arithmetic (`_axis_count`, `grid_size`), 3D rotation-matrix spherical coordinate offsets (`_spherical_offset`), and row-major `Pointing` generation (`plan_mosaic`).
- **Test Coverage:** `.opencode/local/mcps/nina_planner/tests/test_mosaic.py` (`PaneFovTest`, `GridSizeTest`, `SphericalOffsetTest`, `PlanMosaicTest`).

### Outbound Imports

| Category | Import Statement | Lines | Usage |
|---|---|---|---|
| `__future__` | `from __future__ import annotations` | `19` | Deferred evaluation of type annotations. |
| Stdlib | `import math` | `21` | `math.ceil`, `math.radians`, `math.degrees`, `math.sin`, `math.cos`, `math.atan2`, `math.asin`, `math.pi`. |
| Third-Party | *(none — `astropy`'s `SkyOffsetFrame` math is ported to stdlib `math`)* | — | — |
| Internal | `from .models.pointing import Pointing` | `23` | Constructs validated `Pointing` instances in `plan_mosaic` (`mosaic.py:191-198`). |
| Internal | `from .models.profile import OpticalTrainInfo` | `24` | Input type and `.fov_deg()` provider in `pane_fov_arcmins` (`mosaic.py:29, 39`). |

### Inbound Callers Across the Codebase

| Importing Module | Imported Symbols | Import Line | Call Sites & Role |
|---|---|---|---|
| `nina_planner/tools/plans.py` | `grid_size`, `pane_fov_arcmins`, `plan_mosaic` | `22` | `_expand_mosaic` (`plans.py:117-138`), invoked by `write_plan_file` (`plans.py:173`), derives pane FOV, `cols x rows`, and the expanded `pointings` list from `ObservationPlan.mosaic`. |

### Module-Level Constants & Globals

| Symbol | Type | Line | Value | Purpose |
|---|---|---|---|---|
| `__all__` | `list[str]` | `26` | `["grid_size", "pane_fov_arcmins", "plan_mosaic"]` | Public API surface exported by `nina_planner.mosaic`. |

### Environment Variables

- *(None).*

---

## 2. Classes, Data Models & Exceptions

*(None — `mosaic.py` defines no classes of its own; it consumes `OpticalTrainInfo` from `models/profile.py` and returns `Pointing` instances from `models/pointing.py`.)*

---

## 3. Function & Method Reference

### 3.1 `pane_fov_arcmins` (`nina_planner/mosaic.py:29-55`)

1. **Purpose & Architectural Role**
   Computes the active optical train's single-pane field of view `(width_arcmins, height_arcmins)` from `OpticalTrainInfo.fov_deg()`, failing loudly with a descriptive `ValueError` naming every missing optical parameter if the profile lacks focal length, pixel size, or sensor pixel dimensions.

2. **Signature, Decorators & Inputs**
   ```python
   def pane_fov_arcmins(optics: OpticalTrainInfo) -> tuple[float, float]:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `optics: OpticalTrainInfo` — Optical train configuration from `ObservatoryProfile.optics`.

3. **Outputs & Return Semantics**
   - Returns `tuple[float, float]`: `(width_arcmins, height_arcmins)` in arcminutes (`fov_deg[0] * 60.0, fov_deg[1] * 60.0`).

4. **Step-by-Step Internal Logic**
   1. **Delegate degree FOV computation (`mosaic.py:39`):** Calls `fov = optics.fov_deg()` (defined in `models/profile.py:58-84`, which computes $2 \arctan(\text{sensor\_mm} / (2 f))$ in degrees along X and Y, or returns `None` if any required parameter is `None` or $\le 0$).
   2. **Missing-optics diagnostic check (`mosaic.py:40-54`):** If `fov is None`:
      - Builds dictionary `values` (`lines 41-46`) mapping `"focal_length_mm"`, `"pixel_size_microns"`, `"camera_xsize_px"`, and `"camera_ysize_px"` to their values on `optics`.
      - Collects `missing = [name for name, value in values.items() if not value]` (`line 47` — catches both `None` and `0` / `0.0`).
      - Raises `ValueError` (`lines 48-54`) naming `', '.join(missing)` and instructing the operator to configure them in N.I.N.A. before writing a mosaic plan.
   3. **Convert degrees to arcminutes (`mosaic.py:55`):** Returns `(fov[0] * 60.0, fov[1] * 60.0)`.

5. **Side Effects & I/O**
   - Pure function; no I/O.

6. **Error Handling & Edge Cases**
   - Raises `ValueError` when any of the 4 optical parameters is `None` or `0` (`test_mosaic.py:35-55`). Note: if a negative value were somehow present on `optics`, `optics.fov_deg()` returns `None` while `if not value` in `missing` only checks truthiness (`None` or `0`), though N.I.N.A. profiles only emit `None` or `0` for unconfigured fields.

7. **Complexity & Risk Notes**
   - **Time Complexity:** $\mathcal{O}(1)$.

---

### 3.2 `_axis_count` (`nina_planner/mosaic.py:58-72`)

1. **Purpose & Architectural Role**
   Private 1D tiling helper that computes the minimum integer number of panes $n \ge 1$ along a single axis needed to span `coverage_arcmins` given single-pane size `pane_fov_arcmins_` and inter-pane overlap percentage `overlap_pct`. Always rounds up (`math.ceil`) so the mosaic never under-covers the requested footprint.

2. **Signature, Decorators & Inputs**
   ```python
   def _axis_count(
       coverage_arcmins: float, pane_fov_arcmins_: float, overlap_pct: float
   ) -> int:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `coverage_arcmins: float` — Requested sky span along this axis in arcminutes ($> 0$).
     - `pane_fov_arcmins_: float` — Single-pane FOV along this axis in arcminutes ($> 0$).
     - `overlap_pct: float` — Overlap percentage between adjacent panes in $(0.0, 100.0)$.

3. **Outputs & Return Semantics**
   - Returns `int`: Number of panes $n \ge 1$ such that the achieved span $\text{pane\_fov} + (n - 1) \cdot \text{step} \ge \text{coverage\_arcmins}$.

4. **Step-by-Step Internal Logic**
   1. **Single-pane check (`mosaic.py:69-70`):** If `coverage_arcmins <= pane_fov_arcmins_`, returns `1`.
   2. **Compute effective step size (`mosaic.py:71`):** Computes `step = pane_fov_arcmins_ * (1.0 - overlap_pct / 100.0)`.
   3. **Compute ceiling pane count (`mosaic.py:72`):** Evaluates and returns:
      $$n = \left\lceil \frac{\text{coverage\_arcmins} - \text{pane\_fov\_arcmins\_}}{\text{step}} \right\rceil + 1$$
      via `return int(math.ceil((coverage_arcmins - pane_fov_arcmins_) / step)) + 1`.

5. **Side Effects & I/O**
   - Pure mathematical function.

6. **Error Handling & Edge Cases**
   - Assumes inputs have already been validated by `grid_size` (`overlap_pct` in $(0, 100)$ so `step > 0`).
   - Exact fits (e.g., `coverage == pane_fov` or `coverage == pane_fov + step`) yield exact integer counts without adding an extra pane (`test_mosaic.py:67-80`).

7. **Complexity & Risk Notes**
   - **Time Complexity:** $\mathcal{O}(1)$.

---

### 3.3 `grid_size` (`nina_planner/mosaic.py:75-101`)

1. **Purpose & Architectural Role**
   Validates mosaic coverage, pane FOV, and overlap parameters, then computes `(cols, rows)` independently for width (columns) and height (rows) via `_axis_count`.

2. **Signature, Decorators & Inputs**
   ```python
   def grid_size(
       coverage_width_arcmins: float,
       coverage_height_arcmins: float,
       pane_fov_width_arcmins: float,
       pane_fov_height_arcmins: float,
       overlap_pct: float,
   ) -> tuple[int, int]:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `coverage_width_arcmins: float` — Requested horizontal coverage in arcminutes ($> 0$).
     - `coverage_height_arcmins: float` — Requested vertical coverage in arcminutes ($> 0$).
     - `pane_fov_width_arcmins: float` — Rig pane width in arcminutes ($> 0$).
     - `pane_fov_height_arcmins: float` — Rig pane height in arcminutes ($> 0$).
     - `overlap_pct: float` — Adjacent pane overlap percentage in $(0.0, 100.0)$.

3. **Outputs & Return Semantics**
   - Returns `tuple[int, int]`: `(cols, rows)` where `cols >= 1` and `rows >= 1`.

4. **Step-by-Step Internal Logic**
   1. **Validate `overlap_pct` (`mosaic.py:87-88`):** If `not 0.0 < overlap_pct < 100.0`, raises `ValueError(f"overlap_pct must be in (0, 100), got {overlap_pct}")`.
   2. **Validate positive pane FOV (`mosaic.py:89-93`):** If `pane_fov_width_arcmins <= 0 or pane_fov_height_arcmins <= 0`, raises `ValueError(f"pane FOV must be positive, got {pane_fov_width_arcmins} x {pane_fov_height_arcmins} arcmins")`.
   3. **Validate positive coverage (`mosaic.py:94-98`):** If `coverage_width_arcmins <= 0 or coverage_height_arcmins <= 0`, raises `ValueError(f"coverage must be positive, got {coverage_width_arcmins} x {coverage_height_arcmins} arcmins")`.
   4. **Compute columns & rows (`mosaic.py:99-101`):**
      - `cols = _axis_count(coverage_width_arcmins, pane_fov_width_arcmins, overlap_pct)` (`line 99`)
      - `rows = _axis_count(coverage_height_arcmins, pane_fov_height_arcmins, overlap_pct)` (`line 100`)
      - Returns `(cols, rows)` (`line 101`).

5. **Side Effects & I/O**
   - Pure function; no I/O.

6. **Error Handling & Edge Cases**
   - Rejects `overlap_pct` of `0.0`, `100.0`, negative values, or `NaN` (since `0.0 < float("nan") < 100.0` is `False`) with `ValueError`.
   - Rejects `<= 0` pane FOV or coverage dimensions with `ValueError`.

7. **Complexity & Risk Notes**
   - **Time Complexity:** $\mathcal{O}(1)$.

---

### 3.4 `_spherical_offset` (`nina_planner/mosaic.py:104-144`)

1. **Purpose & Architectural Role**
   Private spherical-trigonometry kernel that computes the exact celestial coordinates `(ra_rad, dec_rad)` of a point offset by `(east_deg, north_deg)` from a reference center `(ra0_rad, dec0_rad)`. Implements a pure-`math` port of `astropy.coordinates.SkyCoord.spherical_offsets_by` (`SkyOffsetFrame` rotation): constructs the offset as a 3D unit vector in the local offset frame centered at $(0, 0)$ and applies the rotation matrix $R_z(\alpha_0) \cdot R_y(-\delta_0)$ to bring it to the celestial $(\alpha_0, \delta_0)$ frame.

2. **Signature, Decorators & Inputs**
   ```python
   def _spherical_offset(
       ra0_rad: float, dec0_rad: float, east_deg: float, north_deg: float
   ) -> tuple[float, float]:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `ra0_rad: float` — Center Right Ascension $\alpha_0$ in radians.
     - `dec0_rad: float` — Center Declination $\delta_0$ in radians.
     - `east_deg: float` — Spherical longitude offset $\Delta\lambda$ (eastward) in degrees.
     - `north_deg: float` — Spherical latitude offset $\Delta\phi$ (northward) in degrees.

3. **Outputs & Return Semantics**
   - Returns `tuple[float, float]`: `(ra, dec)` in radians, where:
     - `ra` $\in [0.0, 2\pi)$ radians (wrapped across the $0\text{h}/24\text{h}$ meridian via `% (2.0 * math.pi)`).
     - `dec` $\in [-\pi/2, +\pi/2]$ radians (clamped inside `math.asin`).

4. **Step-by-Step Internal Logic**
   1. **Convert offset angles & precompute center trig (`mosaic.py:121-124`):**
      - `lon = math.radians(east_deg)` (`line 121`)
      - `lat = math.radians(north_deg)` (`line 122`)
      - `sin_ra, cos_ra = math.sin(ra0_rad), math.cos(ra0_rad)` (`line 123`)
      - `sin_dec, cos_dec = math.sin(dec0_rad), math.cos(dec0_rad)` (`line 124`)
   2. **Unit vector in the rotated `SkyOffsetFrame` (`mosaic.py:127-130`):**
      Computes Cartesian coordinates $(x_0, y_0, z_0)$ of spherical coordinates $(\text{lon}, \text{lat})$ relative to frame origin $(1, 0, 0)$:
      $$\begin{pmatrix} x_0 \\ y_0 \\ z_0 \end{pmatrix} = \begin{pmatrix} \cos(\text{lat})\cos(\text{lon}) \\ \cos(\text{lat})\sin(\text{lon}) \\ \sin(\text{lat}) \end{pmatrix}$$
   3. **Apply declination rotation $R_y(-\delta_0)$ (`mosaic.py:133-135`):**
      Swings the offset frame's equator onto the center's declination $\delta_0$:
      $$\begin{pmatrix} x_1 \\ y_1 \\ z_1 \end{pmatrix} = \begin{pmatrix} x_0 \cos\delta_0 - z_0 \sin\delta_0 \\ y_0 \\ x_0 \sin\delta_0 + z_0 \cos\delta_0 \end{pmatrix}$$
   4. **Apply right-ascension rotation $R_z(\alpha_0)$ (`mosaic.py:138-140`):**
      Swings the meridian onto the center's right ascension $\alpha_0$:
      $$\begin{pmatrix} x_2 \\ y_2 \\ z_2 \end{pmatrix} = \begin{pmatrix} x_1 \cos\alpha_0 - y_1 \sin\alpha_0 \\ x_1 \sin\alpha_0 + y_1 \cos\alpha_0 \\ z_1 \end{pmatrix}$$
   5. **Convert Cartesian unit vector back to spherical `(ra, dec)` (`mosaic.py:142-144`):**
      - `ra = math.atan2(y2, x2) % (2.0 * math.pi)` (`line 142`), mapping $[-\pi, \pi]$ into $[0, 2\pi)$.
      - `dec = math.asin(max(-1.0, min(1.0, z2)))` (`line 143`), clamping `z2` to $[-1.0, 1.0]$ to guard against IEEE-754 rounding drift slightly outside $\pm 1$ near the celestial poles.
      - Returns `(ra, dec)` (`line 144`).

   ```mermaid
   flowchart TD
       A["_spherical_offset(ra0_rad, dec0_rad, east_deg, north_deg)"] --> B["lon = radians(east_deg), lat = radians(north_deg)<br/>sin_ra, cos_ra = sin(ra0_rad), cos(ra0_rad)<br/>sin_dec, cos_dec = sin(dec0_rad), cos(dec0_rad) (lines 121-124)"]
       B --> C["SkyOffsetFrame unit vector (lines 127-130):<br/>x0 = cos(lat)*cos(lon)<br/>y0 = cos(lat)*sin(lon)<br/>z0 = sin(lat)"]
       C --> D["Ry(-dec0) pitch rotation (lines 133-135):<br/>x1 = x0*cos_dec - z0*sin_dec<br/>y1 = y0<br/>z1 = x0*sin_dec + z0*cos_dec"]
       D --> E["Rz(ra0) yaw rotation (lines 138-140):<br/>x2 = x1*cos_ra - y1*sin_ra<br/>y2 = x1*sin_ra + y1*cos_ra<br/>z2 = z1"]
       E --> F["Spherical extraction & wrap (lines 142-144):<br/>ra = atan2(y2, x2) % (2*pi)<br/>dec = asin(clamp(z2, -1.0, 1.0))<br/>return (ra, dec)"]
   ```

5. **Side Effects & I/O**
   - Pure mathematical function; no I/O.

6. **Error Handling & Edge Cases**
   - **Pole crossing:** Offsetting northward past $\delta = +90^\circ$ naturally flips RA by $180^\circ$ ($12\text{h}$) via `atan2(y2, x2)` and brings declination down the far side via `asin(z2)` (`test_mosaic.py:163-179`).
   - **RA $0\text{h}/24\text{h}$ seam:** `math.atan2(y2, x2) % (2.0 * math.pi)` wraps smoothly across $0\text{h}$ without clamping (`test_mosaic.py:154-161`).
   - **Floating-point domain safety:** `max(-1.0, min(1.0, z2))` prevents `ValueError: math domain error` in `math.asin` when `abs(z2)` is $1 + 10^{-16}$.

7. **Complexity & Risk Notes**
   - **Time Complexity:** $\mathcal{O}(1)$ (8 trig calls + basic arithmetic).

---

### 3.5 `plan_mosaic` (`nina_planner/mosaic.py:147-200`)

1. **Purpose & Architectural Role**
   Generates the complete row-major list of `cols * rows` `Pointing` objects for a mosaic centered on `(center_ra_hours, center_dec_deg)` stepped by `(step_width_deg, step_height_deg)` and rotated in the tangent plane by position angle `pa_deg`. Labels each pane `"r{row}_c{col}"` (1-based, row 1 at the north/top before rotation, column 1 at the west/left before rotation).

2. **Signature, Decorators & Inputs**
   ```python
   def plan_mosaic(
       center_ra_hours: float,
       center_dec_deg: float,
       cols: int,
       rows: int,
       step_width_deg: float,
       step_height_deg: float,
       pa_deg: float = 0.0,
   ) -> list[Pointing]:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `center_ra_hours: float` — Mosaic center Right Ascension in hours $[0, 24)$.
     - `center_dec_deg: float` — Mosaic center Declination in degrees $[-90, 90]$.
     - `cols: int` — Number of columns ($\ge 1$).
     - `rows: int` — Number of rows ($\ge 1$).
     - `step_width_deg: float` — Horizontal center-to-center step between adjacent columns in degrees ($\text{pane\_w\_deg} \times (1 - \text{overlap})$).
     - `step_height_deg: float` — Vertical center-to-center step between adjacent rows in degrees ($\text{pane\_h\_deg} \times (1 - \text{overlap})$).
     - `pa_deg: float = 0.0` — Position angle rotation in degrees (default `0.0`).

3. **Outputs & Return Semantics**
   - Returns `list[Pointing]`: Row-major list of `cols * rows` `Pointing` objects, each populated with:
     - `label`: `f"r{r + 1}_c{c + 1}"`
     - `ra_hours`: `round(math.degrees(ra) / 15.0, 6) % 24.0` (in $[0.0, 24.0)$)
     - `dec_deg`: `round(math.degrees(dec), 6)` (in $[-90.0, 90.0]$)
     - `position_angle_deg`: `round(pa_deg % 360.0, 3)` (in $[0.0, 360.0)$)
     - `row`: `r + 1` (1-based)
     - `column`: `c + 1` (1-based)

4. **Step-by-Step Internal Logic**
   1. **Validate grid dimensions (`mosaic.py:170-171`):** If `cols < 1 or rows < 1`, raises `ValueError(f"cols and rows must both be >= 1, got {cols}x{rows}")`.
   2. **Convert center and PA to radians (`mosaic.py:173-175`):**
      - `ra0 = math.radians(center_ra_hours * 15.0)` (`line 173`)
      - `dec0 = math.radians(center_dec_deg)` (`line 174`)
      - `pa = math.radians(pa_deg)` (`line 175`)
   3. **Iterate row-major (`mosaic.py:177-179`):** Initializes `pointings: list[Pointing] = []` and loops `for r in range(rows):` (outer) and `for c in range(cols):` (inner).
   4. **Compute unrotated symmetric grid offsets `(dx, dy)` (`mosaic.py:182-183`):**
      - `dx = (c - (cols - 1) / 2.0) * step_width_deg` (columns step left-to-right / negative-to-positive east).
      - `dy = ((rows - 1) / 2.0 - r) * step_height_deg` (row `r = 0` starts at the top / positive north, stepping southward as `r` increases).
   5. **Rotate grid offset by position angle `pa` (`mosaic.py:186-187`):**
      $$\begin{pmatrix} \text{east} \\ \text{north} \end{pmatrix} = \begin{pmatrix} \cos(\text{pa}) & -\sin(\text{pa}) \\ \sin(\text{pa}) & \cos(\text{pa}) \end{pmatrix} \begin{pmatrix} dx \\ dy \end{pmatrix}$$
   6. **Project onto celestial sphere (`mosaic.py:189`):** Calls `ra, dec = _spherical_offset(ra0, dec0, east, north)`.
   7. **Construct and append `Pointing` (`mosaic.py:190-199`):** Appends `Pointing(...)`:
      - `ra_hours=round(math.degrees(ra) / 15.0, 6) % 24.0` — Note the trailing `% 24.0` after `round(..., 6)`, which wraps `24.0` back to `0.0` if an RA very close to $24\text{h}$ (e.g., `23.9999999`) rounds up to `24.000000`, satisfying `Pointing.ra_hours < 24` (`models/pointing.py:42`).
      - `position_angle_deg=round(pa_deg % 360.0, 3)` — Note: if `pa_deg % 360.0` were within $0.0005^\circ$ of $360^\circ$ (e.g., `359.9999`), `round(..., 3)` could yield `360.0`, which `Pointing.position_angle_deg < 360` (`models/pointing.py:49`) would reject with a Pydantic `ValidationError`.
   8. **Return pointings (`mosaic.py:200`):** Returns `pointings`.

   ```mermaid
   flowchart TD
       A["plan_mosaic(center_ra_hours, center_dec_deg, cols, rows, step_width_deg, step_height_deg, pa_deg=0.0)"] --> B{"cols < 1 or rows < 1?<br/>(line 170)"}
       B -- Yes --> R1["raise ValueError('cols and rows must both be >= 1...')<br/>(line 171)"]
       B -- No --> C["ra0 = radians(center_ra_hours * 15.0)<br/>dec0 = radians(center_dec_deg)<br/>pa = radians(pa_deg)<br/>pointings = [] (lines 173-177)"]
       C --> D["Loop r in range(rows), c in range(cols) (lines 178-179)"]
       D --> E["Symmetric grid offset (lines 182-183):<br/>dx = (c - (cols - 1) / 2.0) * step_width_deg<br/>dy = ((rows - 1) / 2.0 - r) * step_height_deg"]
       E --> F["Tangent-plane PA rotation (lines 186-187):<br/>east = dx * cos(pa) - dy * sin(pa)<br/>north = dx * sin(pa) + dy * cos(pa)"]
       F --> G["ra, dec = _spherical_offset(ra0, dec0, east, north)<br/>(line 189)"]
       G --> H["pointings.append(Pointing(<br/>  label=f'r{r+1}_c{c+1}',<br/>  ra_hours=round(degrees(ra)/15.0, 6) % 24.0,<br/>  dec_deg=round(degrees(dec), 6),<br/>  position_angle_deg=round(pa_deg % 360.0, 3),<br/>  row=r+1, column=c+1<br/>)) (lines 190-199)"]
       H --> D
       D -- All panes built --> I["return pointings (line 200)"]
   ```

5. **Side Effects & I/O**
   - Pure function; instantiates `cols * rows` Pydantic `Pointing` objects in memory.

6. **Error Handling & Edge Cases**
   - Raises `ValueError` if `cols < 1` or `rows < 1` (`test_mosaic.py:270-275`).
   - For a `1x1` grid (`cols=1, rows=1`), `dx = 0.0` and `dy = 0.0`, so `east = 0.0, north = 0.0` and the single pane `"r1_c1"` lands directly on `(center_ra_hours, center_dec_deg)` (`test_mosaic.py:185-194`).

7. **Complexity & Risk Notes**
   - **Time / Space Complexity:** $\mathcal{O}(\text{cols} \times \text{rows})$ time and space.
