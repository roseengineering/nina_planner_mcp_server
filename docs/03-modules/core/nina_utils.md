# `nina_planner/nina_utils.py` — ASCOM Sentinel Coercion, Naming Normalization & Local-Time Display

[⬆️ Up to Atlas Index](../../README.md) · [⬆️ Core Package](./README.md) · **Callers:** [`imaging.py`](./imaging.md), [`progress.py`](./progress.md), [`tools/equipment.py`](../tools/equipment.md), [`models/pointing-and-frame.md`](../models/pointing-and-frame.md), [`models/core-rig-devices.md`](../models/core-rig-devices.md), [`models/auxiliary-and-safety-devices.md`](../models/auxiliary-and-safety-devices.md) · **Callees:** *(none — stdlib only)*

---

## 1. Module Overview

- **File Path:** `.opencode/local/mcps/nina_planner/nina_planner/nina_utils.py`
- **Line Count:** 123 lines
- **Module Docstring & Purpose (`nina_utils.py:1-7`):**
  > *"Coercion helpers for ASCOM/N.I.N.A. field values and name formatting. ASCOM drivers send 'no value' as a surprising variety of shapes — `None`, `'NaN'`, `'N/A'`, `.NET DateTime.MinValue` — so the `ascom_*` helpers normalize them to `None` instead of raising, keeping one odd driver value from failing a whole equipment-status call."*
- **Architectural Layer:** Core Foundation / Normalization Utilities (`nina_planner/`). Zero internal package dependencies; imported across CSV ingestion, progress tracking, MCP equipment/event tools, and 8 Pydantic model modules.
- **Test Coverage:** `.opencode/local/mcps/nina_planner/tests/test_nina_utils.py` (`AscomFloatTest`, `AscomIntTest`, `AscomDatetimeTest`, `AscomFloatFallbackTest`, `AscomDatetimeFallbackTest`, `ToLocalDisplayTest`).

### Outbound Imports

| Category | Import Statement | Lines | Usage |
|---|---|---|---|
| `__future__` | `from __future__ import annotations` | `9` | Deferred evaluation of PEP 604 union type annotations (`float \| None`, `int \| None`, `datetime \| None`). |
| Stdlib | `import math` | `11` | `math.isnan()` check on `float` inputs in `ascom_float` (`nina_utils.py:48`). |
| Stdlib | `import re` | `12` | `re.sub()` regex transformations in `to_snake` (`nina_utils.py:22-23`). |
| Stdlib | `from datetime import datetime` | `13` | `datetime.fromisoformat()`, `datetime.min`, and `.astimezone()` / `.isoformat()` in `ascom_datetime` and `to_local_display`. |
| Third-Party | *(none)* | — | — |
| Internal | *(none)* | — | — |

### Inbound Callers Across the Codebase

| Importing Module | Imported Symbols | Import Line | Call Sites & Role |
|---|---|---|---|
| `nina_planner/imaging.py` | `to_snake` | `19` | `_read_csv` (`imaging.py:108`) normalizes `ImageMetaData.csv` and `WeatherData.csv` header column names to `snake_case`. |
| `nina_planner/progress.py` | `to_local_display` | `17` | `plan_progress` (`progress.py:620`) formats calibration `window_end` (`window_end.replace(tzinfo=UTC)`) in host-local time with UTC offset. |
| `nina_planner/tools/equipment.py` | `ascom_float`, `ascom_int`, `to_local_display`, `to_snake` | `19` | `_convert_keys` (`equipment.py:24`) normalizes JSON dict keys; `_convert_to_met` (`equipment.py:77`) renders event/log timestamps; `get_site_profile` (`equipment.py:203-204, 230, 243`) coerces profile pixel size, gain, filter position, and focus offset. |
| `nina_planner/models/frame.py` | `ascom_datetime`, `ascom_float`, `ascom_int` | `9` | `FrameStats._normalize` (`frame.py:102, 104, 106`) coerces per-frame `GET /v2/api/image-history` fields (`_FLOAT_FIELDS`, `_INT_FIELDS`, `"date"`). |
| `nina_planner/models/camera.py` | `ascom_datetime`, `ascom_float`, `ascom_int` | `9` | `_binning_axis` (`camera.py:14`) and `CameraDevice._derive_fields` (`camera.py:156, 162-167, 172-173, 176, 179-180, 185-186, 200, 203-204`) coerce camera telemetry. |
| `nina_planner/models/mount.py` | `ascom_float` | `7` | `MountDevice._derive_fields` (`mount.py:53-54, 56-57, 63-64, 68`) coerces RA/Dec, azimuth/altitude, and `time_to_meridian_flip`. |
| `nina_planner/models/focuser.py` | `ascom_float`, `ascom_int` | `7` | `FocuserDevice._derive_fields` (`focuser.py:37-38`) coerces `temperature` and `step_size`. |
| `nina_planner/models/guider.py` | `ascom_float` | `7` | `GuiderDevice._derive_fields` (`guider.py:42, 59-60`) coerces `pixel_scale` and nested `RMSError` (`arcseconds`, `pixel`). |
| `nina_planner/models/rotator.py` | `ascom_float` | `7` | `RotatorDevice._derive_fields` (`rotator.py:29, 32`) coerces `position` and `mechanical_position`. |
| `nina_planner/models/dome.py` | `ascom_float` | `7` | `DomeDevice._derive_fields` (`dome.py:38`) coerces dome `azimuth`. |
| `nina_planner/models/weather.py` | `ascom_float` | `7` | `Weather._derive_fields` (`weather.py:37-47`) coerces all 11 weather station readings. |

### Module-Level Constants & Globals

| Symbol | Type | Line | Value | Purpose |
|---|---|---|---|---|
| `_MISSING_STRINGS` | `set[str]` | `26` | `{"nan", "n/a", "na", ""}` | Lowercase sentinel strings representing missing or unmeasured ASCOM/CSV values. Checked by `_missing_string` (`nina_utils.py:29-30`). |

### Environment Variables

- *(None — `nina_utils.py` does not read `os.environ` directly; `to_local_display` relies on the OS process timezone via `datetime.astimezone()`).*

---

## 2. Classes, Data Models & Exceptions

*(None — `nina_utils.py` is a pure functional utility module and defines no classes, Pydantic models, or custom exceptions.)*

---

## 3. Function & Method Reference

### 3.1 `to_snake` (`nina_planner/nina_utils.py:16-23`)

1. **Purpose & Architectural Role**
   Converts `camelCase` and `PascalCase` identifiers emitted by N.I.N.A.'s Advanced API JSON payloads (`tools/equipment.py:_convert_keys`) and `ImageMetaData.csv` / `WeatherData.csv` headers (`imaging.py:_read_csv`) into normalized lowercase `snake_case` field keys.

2. **Signature, Decorators & Inputs**
   ```python
   def to_snake(name: str) -> str:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `name: str` — Input identifier string (typically PascalCase or camelCase, e.g., `"RMSArcSec"`, `"CameraState"`, `"HFR"`).

3. **Outputs & Return Semantics**
   - Returns `str`: Lowercase `snake_case` string with underscores inserted at word and acronym boundaries.

4. **Step-by-Step Internal Logic**
   1. **Acronym-boundary pass (`nina_utils.py:22`):** Executes `s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)`. Any character followed by an uppercase letter and one or more lowercase letters has an underscore inserted between them (e.g., `"RMSArcSec"` $\rightarrow$ `"RMS_ArcSec"`).
   2. **Embedded-uppercase & lowercase pass (`nina_utils.py:23`):** Executes `return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s).lower()`. Inserts an underscore between any lowercase letter or digit and a following uppercase letter, then converts the entire string to lowercase (e.g., `"RMS_ArcSec"` $\rightarrow$ `"rms_arc_sec"`).

5. **Side Effects & I/O**
   - Pure string transformation; no I/O or state mutation.

6. **Error Handling & Edge Cases**
   - Empty strings `""` return `""`.
   - Already-`snake_case` strings pass through unchanged.
   - Note documented in `models/frame.py:12-14`: compound suffixes where a trailing capital stands alone or blends with an acronym (such as `"HFRStDev"` $\rightarrow$ `"hfrst_dev"` because `"R"` precedes `"St"`, not split after `"HFR"`) require an explicit `_FIELD_MAP` when `"hfr_st_dev"` is desired.

7. **Complexity & Risk Notes**
   - **Time / Space Complexity:** $\mathcal{O}(k)$ where $k = \text{len}(name)$.
   - Called on every key of every dictionary returned by `/equipment/*`, `/event-history`, `/application/logs`, and every CSV cell key in `_read_csv`.

---

### 3.2 `_missing_string` (`nina_planner/nina_utils.py:29-30`)

1. **Purpose & Architectural Role**
   Private predicate helper for `ascom_float` (`nina_utils.py:44`) that tests whether a string represents an ASCOM/CSV "no value" sentinel.

2. **Signature, Decorators & Inputs**
   ```python
   def _missing_string(value: str) -> bool:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `value: str` — String candidate to test.

3. **Outputs & Return Semantics**
   - Returns `bool`: `True` if `value` (stripped of whitespace and lowercased) belongs to `_MISSING_STRINGS` (`{"nan", "n/a", "na", ""}`); `False` otherwise.

4. **Step-by-Step Internal Logic**
   1. **Normalize & membership test (`nina_utils.py:30`):** Evaluates `value.strip().lower() in _MISSING_STRINGS`.

5. **Side Effects & I/O**
   - Pure function; no side effects.

6. **Error Handling & Edge Cases**
   - Whitespace-only strings (e.g., `"   "`) strip to `""`, which matches `_MISSING_STRINGS` and returns `True`.
   - Mixed-case variants (`"NaN"`, `" N/A "`, `"NA"`) all normalize and return `True`.

7. **Complexity & Risk Notes**
   - **Time Complexity:** $\mathcal{O}(k)$ string strip/lower + $\mathcal{O}(1)$ set lookup.

---

### 3.3 `ascom_float` (`nina_planner/nina_utils.py:33-55`)

1. **Purpose & Architectural Role**
   Coerces arbitrary ASCOM / N.I.N.A. JSON or CSV values to `float`, mapping all sentinel "no value" representations (`None`, `NaN` floats, `"NaN"`, `"N/A"`, `"na"`, blank/whitespace strings) to `None`. Serves as the foundational numeric sanitizer across all equipment models and indirectly powers `ascom_int`.

2. **Signature, Decorators & Inputs**
   ```python
   def ascom_float(value: object) -> float | None:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `value: object` — Raw value from a parsed JSON response, CSV dictionary row, or model validator input.

3. **Outputs & Return Semantics**
   - Returns `float | None`:
     - `None` if `value` is `None`, a float `NaN`, or a string sentinel matching `_missing_string`.
     - A finite Python `float` (`±inf` is not filtered by `math.isnan`, only `NaN` is mapped to `None`) for valid numeric strings, `float`, `int`, `bool` (`True` $\rightarrow$ `1.0`, `False` $\rightarrow$ `0.0`), or objects whose `str(value)` is float-parseable (e.g., `decimal.Decimal`).

4. **Step-by-Step Internal Logic**
   1. **`None` guard (`nina_utils.py:40-41`):** If `value is None`, returns `None`.
   2. **`str` branch (`nina_utils.py:42-46`):** If `isinstance(value, str)`:
      - Strips whitespace (`stripped = value.strip()`, line `43`).
      - Checks `if _missing_string(stripped): return None` (lines `44-45`).
      - Parses and returns `float(stripped)` (line `46`). Note: in Python, `float("nan")` would produce a `NaN` float, which `_missing_string` catches beforehand.
   3. **`float` branch (`nina_utils.py:47-50`):** If `isinstance(value, float)`:
      - Checks `if math.isnan(value): return None` (lines `48-49`).
      - Otherwise returns `value` unchanged (line `50`).
   4. **Non-boolean `int` branch (`nina_utils.py:51-52`):** If `isinstance(value, int) and not isinstance(value, bool)`, returns `float(value)`.
   5. **`bool` branch (`nina_utils.py:53-54`):** If `isinstance(value, bool)`, returns `float(value)` (`1.0` or `0.0`).
   6. **Fallback coercion (`nina_utils.py:55`):** For any other object type (e.g., `Decimal("3.14")`), evaluates `return float(str(value))`.

5. **Side Effects & I/O**
   - Pure function; no side effects.

6. **Error Handling & Edge Cases**
   - **Raises `ValueError` / `TypeError`:** As documented in the docstring (`nina_utils.py:37-38`), genuinely non-numeric inputs (e.g., `"abc"`, `[1, 2]`) passed to `float(stripped)` or `float(str(value))` are **not** suppressed; they raise `ValueError` so callers that want strictness get it, or callers like `tools/equipment.py:229-232` and `models/camera.py:12-16` explicitly catch `(TypeError, ValueError)`.
   - Edge case: If a custom object's `__str__()` returns `"NaN"`, `float(str(value))` on line `55` would return `float('nan')` without going through `math.isnan`. In practice all JSON/CSV inputs are `None`, `str`, `float`, `int`, `bool`, or `Decimal`.

7. **Complexity & Risk Notes**
   - **Time Complexity:** $\mathcal{O}(1)$ for primitives; $\mathcal{O}(k)$ for strings of length $k$.

---

### 3.4 `ascom_int` (`nina_planner/nina_utils.py:58-71`)

1. **Purpose & Architectural Role**
   Coerces an ASCOM / N.I.N.A. value to `int`, mapping all sentinel "no value" shapes to `None` while preserving arbitrary-precision Python `int` values without `float` rounding loss and truncating fractional values toward zero.

2. **Signature, Decorators & Inputs**
   ```python
   def ascom_int(value: object) -> int | None:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `value: object` — Raw value from a parsed JSON response or CSV row.

3. **Outputs & Return Semantics**
   - Returns `int | None`:
     - Exact `int` if `value` is a non-bool `int`.
     - `None` if `ascom_float(value)` returns `None`.
     - `int(parsed)` (truncated toward zero, e.g., `3.7` $\rightarrow$ `3`, `"42.7"` $\rightarrow$ `42`) for all other numeric shapes.

4. **Step-by-Step Internal Logic**
   1. **Exact `int` fast-path (`nina_utils.py:68-69`):** Checks `if isinstance(value, int) and not isinstance(value, bool): return value`. Because `bool` is a subclass of `int` in Python, `not isinstance(value, bool)` prevents `True`/`False` from returning as `bool` instances and ensures large integers (> $2^{53}$) do not lose precision through IEEE-754 `float` conversion.
   2. **Delegate sentinel & float parsing (`nina_utils.py:70`):** Calls `parsed = ascom_float(value)`.
   3. **Truncate or propagate `None` (`nina_utils.py:71`):** Returns `None if parsed is None else int(parsed)`.

5. **Side Effects & I/O**
   - Pure function; no side effects.

6. **Error Handling & Edge Cases**
   - `bool` values (`True`, `False`) bypass line `68`, turn into `1.0` / `0.0` in `ascom_float`, and become `int` `1` / `0` on line `71`.
   - Non-numeric strings (e.g., `"bogus"`) raise `ValueError` from `ascom_float`.

7. **Complexity & Risk Notes**
   - **Time Complexity:** $\mathcal{O}(1)$ for `int`/`float`/`bool`; $\mathcal{O}(k)$ for strings.

---

### 3.5 `ascom_datetime` (`nina_planner/nina_utils.py:74-104`)

1. **Purpose & Architectural Role**
   Parses ISO-8601 timestamp strings emitted by ASCOM drivers and N.I.N.A. endpoints (e.g., `CameraInfo.ExposureEndTime`, `image-history` `Date`) into Python `datetime` objects while normalizing `.NET DateTime.MinValue` (`0001-01-01T00:00:00` in all timezone/tick forms), `"NaN"`, blank strings, unparseable strings, and non-string values to `None`.

2. **Signature, Decorators & Inputs**
   ```python
   def ascom_datetime(value: object) -> datetime | None:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `value: object` — Raw timestamp candidate (expected `str` or `None`, though any `object` is safely accepted).

3. **Outputs & Return Semantics**
   - Returns `datetime | None`:
     - A `datetime` object preserving the input string's original `tzinfo` (naive if input was naive, UTC/offset-aware if input had `Z` or `±HH:MM`).
     - `None` if `value` is `None`, not a `str`, blank, `"nan"` (case-insensitive), unparseable by `datetime.fromisoformat()`, or equal to `.NET DateTime.MinValue` (`0001-01-01T00:00:00` with zero microseconds).

4. **Step-by-Step Internal Logic**
   1. **`None` check (`nina_utils.py:84-85`):** If `value is None`, returns `None`.
   2. **Type guard (`nina_utils.py:86, 104`):** Checks `if isinstance(value, str):`; if `value` is any non-string object (e.g., `int`, `float`, `list`, `dict`), falls through to line `104` and returns `None`.
   3. **Strip & string sentinel check (`nina_utils.py:87-89`):** Computes `stripped = value.strip()`. If `not stripped or stripped.lower() == "nan"`, returns `None`.
   4. **ISO-8601 parse (`nina_utils.py:90-93`):** Calls `parsed = datetime.fromisoformat(stripped)` inside a `try ... except (ValueError, TypeError):` block, returning `None` on parse failure.
   5. **`.NET DateTime.MinValue` sentinel check (`nina_utils.py:101-102`):** Checks `if parsed.replace(tzinfo=None) == datetime.min: return None`. Stripping `tzinfo` collapses all four ISO forms of `.NET DateTime.MinValue` — naive (`"0001-01-01T00:00:00"`), 7-digit ticks (`"0001-01-01T00:00:00.0000000"`), UTC `Z` (`"0001-01-01T00:00:00Z"`), and explicit offset (`"0001-01-01T00:00:00+00:00"`) — onto `datetime.min` (`datetime(1, 1, 1, 0, 0)`). Any year-1 timestamp with non-zero time or microsecond components (e.g., `"0001-01-01T00:00:00.123"`) has `microsecond != 0`, does not equal `datetime.min`, and is preserved.
   6. **Return valid `datetime` (`nina_utils.py:103`):** Returns `parsed` with its original `tzinfo` intact.

   ```mermaid
   flowchart TD
       A["ascom_datetime(value)"] --> B{"value is None?"}
       B -- Yes --> R1["return None (line 85)"]
       B -- No --> C{"isinstance(value, str)?"}
       C -- No --> R2["return None (line 104)"]
       C -- Yes --> D["stripped = value.strip() (line 87)"]
       D --> E{"not stripped or<br/>stripped.lower() == 'nan'?"}
       E -- Yes --> R3["return None (line 89)"]
       E -- No --> F["parsed = datetime.fromisoformat(stripped)<br/>(line 91)"]
       F -- "ValueError / TypeError" --> R4["return None (line 93)"]
       F -- Success --> G{"parsed.replace(tzinfo=None)<br/>== datetime.min?<br/>(line 101)"}
       G -- Yes --> R5["return None (line 102)<br/>(.NET DateTime.MinValue)"]
       G -- No --> R6["return parsed (line 103)"]
   ```

5. **Side Effects & I/O**
   - Pure function; no side effects.

6. **Error Handling & Edge Cases**
   - **Never raises:** Unlike `ascom_float` and `ascom_int`, `ascom_datetime` catches `(ValueError, TypeError)` and rejects non-`str` inputs, returning `None` on every invalid input (`test_nina_utils.py:147-165`).
   - Note that an existing `datetime` object passed as `value` will return `None` because line `86` checks `isinstance(value, str)`.

7. **Complexity & Risk Notes**
   - **Time Complexity:** $\mathcal{O}(k)$ where $k = \text{len}(value)$ for `datetime.fromisoformat`.

---

### 3.6 `to_local_display` (`nina_planner/nina_utils.py:107-123`)

1. **Purpose & Architectural Role**
   Single canonical formatting boundary that renders a Python `datetime` into an ISO-8601 string in the host machine's local timezone with second resolution (`timespec="seconds"`), preserving the UTC offset on aware datetimes so displayed instants remain unambiguous across machines and DST transitions.

2. **Signature, Decorators & Inputs**
   ```python
   def to_local_display(dt: datetime) -> str:
   ```
   - **Decorators:** None.
   - **Parameters:**
     - `dt: datetime` — A naive or timezone-aware `datetime` instance.

3. **Outputs & Return Semantics**
   - Returns `str`:
     - If `dt` is naive (`dt.tzinfo is None`): `YYYY-MM-DDTHH:MM:SS` (taken as already-local wall clock; no timezone conversion or offset suffix).
     - If `dt` is timezone-aware (`dt.tzinfo is not None`): `YYYY-MM-DDTHH:MM:SS±HH:MM` converted to the host's local timezone via `dt.astimezone()`.

4. **Step-by-Step Internal Logic**
   1. **Check timezone awareness (`nina_utils.py:121`):** Tests `if dt.tzinfo is None:`.
   2. **Naive branch (`nina_utils.py:122`):** Returns `dt.isoformat(timespec="seconds")` without altering the clock fields.
   3. **Aware branch (`nina_utils.py:123`):** Converts `dt` to the system local timezone with `dt.astimezone()` and formats with `.isoformat(timespec="seconds")`.

5. **Side Effects & I/O**
   - Queries the OS local timezone definition via `dt.astimezone()` (no file or network I/O beyond standard libc timezone lookup).

6. **Error Handling & Edge Cases**
   - Sub-second microseconds are truncated by `timespec="seconds"`.
   - Callers holding a naive-UTC timestamp (such as `window_end` in `progress.py:620`) must explicitly attach `tzinfo=UTC` (`window_end.replace(tzinfo=UTC)`) before calling `to_local_display`, otherwise line `121` treats a naive `datetime` as already-local.

7. **Complexity & Risk Notes**
   - **Time Complexity:** $\mathcal{O}(1)$.
