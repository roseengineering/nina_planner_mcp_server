# File and Symbol Manifest

[⬆️ Up to Atlas Index](../README.md) · [➡️ MCP Surface Catalog](./mcp-surface-catalog.md) · [➡️ Coverage Ledger](./coverage-ledger.md)

---

## 1. Repository Footprint Summary

| Category | File Count | Total Lines | Package / Directory Root |
|---|---:|---:|---|
| **Core & Root (`nina_planner/`)** | 8 | 691 | `.opencode/local/mcps/nina_planner/nina_planner/` |
| **Domain Services (`nina_planner/`)** | 8 | 1,961 | `.opencode/local/mcps/nina_planner/nina_planner/` |
| **CLI Modules (`nina_planner/`)** | 3 | 2,461 | `.opencode/local/mcps/nina_planner/nina_planner/` |
| **MCP Tool Modules (`nina_planner/tools/`)** | 6 | 3,096 | `.opencode/local/mcps/nina_planner/nina_planner/tools/` |
| **Pydantic Models (`nina_planner/models/`)** | 20 | 1,750 | `.opencode/local/mcps/nina_planner/nina_planner/models/` |
| **Sequence Compiler (`nina_planner/sequence/`)** | 6 | 2,149 | `.opencode/local/mcps/nina_planner/nina_planner/sequence/` |
| **Repository Scripts & Skills** | 4 | 1,403 | `scripts/`, `.agents/skills/site-conditions/` |
| **Production Python Subtotal** | **55** *(47 non-empty)* | **13,517** | |
| **Automated Test Suite (`tests/`)** | 35 | 18,950 | `.opencode/local/mcps/nina_planner/tests/` |
| **Grand Total (Python)** | **90** | **32,467** | |

---

## 2. Package & Console Script Entrypoints

Defined in `.opencode/local/mcps/nina_planner/pyproject.toml:58-63`:

| Console Script | Target Callable | Source Location | Purpose |
|---|---|---|---|
| `nina-planner` | `nina_planner.__main__:main` | `nina_planner/__main__.py:6-8` | Starts the `FastMCP` server (`nina-planner`) over stdio. |
| `nina-archive` | `nina_planner.archive:main` | `nina_planner/archive.py:1191-1200` | Atomically copies completed plan frames, joined weather CSVs, `plan.json`, and `manifest.json` into `--dest`. |
| `nina-pin-id` | `nina_planner.pin_id:main` | `nina_planner/pin_id.py:154-162` | Stamps a content-derived `_base_plan_id()` into `plan.plan_id` on disk so subsequent edits preserve frame attribution. |
| `nina-purge` | `nina_planner.purge:main` | `nina_planner/purge.py:1080-1087` | Verifies archive manifests, quarantines released frame folders into `.quarantine/` (`--apply`), and permanently deletes quarantined folders + original plan files (`--purge`). |

---

## 3. Complete Production Python File & Symbol Inventory

### 3.1 Core Bootstrap, Config, HTTP Client & OS/Time Helpers (`nina_planner/`)

| File Path | Lines | Classes / Exceptions | Functions & Methods (`start-end`) | Constants / Globals (`line`) | Outbound Internal Imports |
|---|---:|---|---|---|---|
| `nina_planner/__init__.py` | 1 | *(none)* | *(none)* | *(none)* | *(none)* |
| `nina_planner/__main__.py` | 12 | *(none)* | `main` (`6-8`) | *(none)* | `.server.mcp` (`3`) |
| `nina_planner/app.py` | 21 | *(none)* | *(none)* | `mcp` (`10-21`) | *(none — imports `FastMCP`)* |
| `nina_planner/server.py` | 61 | *(none)* | *(none — registers 22 tools via imports)* | `__all__` (`33-57`) | `.app.mcp` (`3`), `.tools.equipment` (`4-9`), `.tools.lifecycle` (`10`), `.tools.plans` (`11-18`), `.tools.sequence` (`19-24`), `.tools.sky` (`25-31`) |
| `nina_planner/config.py` | 19 | *(none)* | *(none)* | `NINA_ENDPOINT` (`10`), `NINA_API_URL` (`11`), `SEQUENCE_START_PATH` (`12`), `SEQUENCE_STATE_PATH` (`13`), `SEQUENCE_JSON_PATH` (`14`), `NINA_PLANNER_LOG` (`15`), `PROJECT_DIR` (`19`) | *(none)* |
| `nina_planner/client.py` | 281 | *(none)* | `_log_file` (`27-39`), `log_payload` (`42-59`), `_error_detail` (`62-78`), `unwrap_response` (`81-115`), `api_get` (`118-134`), `api_post` (`137-154`), `_sequence_running` (`157-159`), `_sequence_is_running` (`162-163`), `stop_running_sequence_and_wait` (`166-182`), `_node_statuses` (`191-206`), `wait_until_sequence_started` (`209-242`), `load_sequence_payload` (`245-252`), `api_probe` (`255-267`), `nina_api_responsive` (`270-272`), `run_blocking` (`275-281`) | `_WINDOWS_ABSOLUTE_PATH` (`24`), `SEQUENCE_START_TIMEOUT` (`188`) | `.config` (`22`) |
| `nina_planner/nina_utils.py` | 123 | *(none)* | `to_snake` (`16-23`), `_missing_string` (`29-30`), `ascom_float` (`33-55`), `ascom_int` (`58-71`), `ascom_datetime` (`74-104`), `to_local_display` (`107-123`) | `_MISSING_STRINGS` (`26`) | *(none)* |
| `nina_planner/system_time.py` | 174 | *(none)* | `nina_exe_path` (`27-32`), `kill_nina_command` (`35-37`), `_is_wsl` (`47-57`), `windows_interop_available` (`60-74`), `get_launcher_paths` (`77-112`), `_batch_quoted` (`115-130`), `write_nina_launcher` (`133-144`), `schtasks_create_command` (`147-164`), `schtasks_run_command` (`167-169`), `schtasks_delete_command` (`172-174`) | `NINA_EXE_DEFAULT` (`20-22`), `NINA_TASK_NAME` (`24`), `_WINDOWS_INTEROP_EXES` (`40-44`) | `.imaging.windows_to_local` (`18`) |

### 3.2 Domain Services & CLI Engines (`nina_planner/`)

| File Path | Lines | Classes / Exceptions | Functions & Methods (`start-end`) | Constants / Globals (`line`) | Outbound Internal Imports |
|---|---:|---|---|---|---|
| `nina_planner/plan_utils.py` | 309 | *(none)* | `write_text_atomic` (`30-53`), `sanitize_filename` (`56-62`), `validate_filters` (`65-90`), `validate_position_angle` (`93-136`), `check_pointing_index` (`139-149`), `require_parkable` (`152-175`), `require_light_capabilities` (`178-220`), `resolve_plan_dir` (`223-232`), `plan_files` (`235-255`), `load_plan` (`258-287`), `quality_kwargs` (`290-309`) | `_NEW_PLAN_MODE` (`25`), `_INVALID_FILENAME_CHARS` (`27`) | `.config` (`19`), `.imaging._walk_visible` (`20`), `.models.observatory.ObservatoryEquipment` (`21`), `.models.plan.ObservationPlan` (`22`), `.models.profile.ObservatoryProfile` (`23`) |
| `nina_planner/progress.py` | 728 | *(none)* | `_path_components` (`34-36`), `path_has_id` (`39-57`), `_exposure_matches` (`60-69`), `is_junk_row` (`72-118`), `_light_target_matches` (`121-169`), `_quality_accepted` (`172-217`), `_exposure_instant` (`220-239`), `_session_start` (`242-265`), `_within_age` (`268-297`), `_row_matches` (`300-329`), `frame_group_constraints` (`332-364`), `_select_matching` (`367-406`), `filter_metadata_rows` (`409-475`), `plan_progress` (`478-631`), `group_verdict` (`634-660`), `progress_verdict` (`663-687`), `plan_with_remaining` (`690-728`) | `__all__` (`19-27`), `Verdict` (`31`) | `.models.plan.ObservationPlan` (`16`), `.nina_utils.to_local_display` (`17`) |
| `nina_planner/metadata.py` | 66 | *(none)* | `resolve_imaging_root` (`12-19`), `resolve_cli_root` (`22-47`), `read_metadata` (`50-66`) | *(none)* | `.imaging.read_imaging_csv` (`8`), `.tools.equipment` (`9`) |
| `nina_planner/imaging.py` | 222 | *(none)* | `_prunable_dir` (`34-36`), `_walk_visible` (`39-52`), `visible_rglob` (`55-62`), `windows_to_local` (`65-81`), `read_weather_rows` (`84-103`), `_read_csv` (`106-108`), `_resolve_image_path` (`111-124`), `_resolve_existing_file` (`127-136`), `_require_root` (`139-152`), `read_imaging_csv` (`155-222`, `@overload` at `155-158`, `161-164`) | `_PRUNED_DIR_NAMES` (`26`), `FRAME_TYPES` (`30`), `_FRAME_TYPES_UPPER` (`31`) | `.nina_utils.to_snake` (`19`) |
| `nina_planner/mosaic.py` | 200 | *(none)* | `pane_fov_arcmins` (`29-55`), `_axis_count` (`58-72`), `grid_size` (`75-101`), `_spherical_offset` (`104-144`), `plan_mosaic` (`147-200`) | `__all__` (`26`) | `.models.pointing.Pointing` (`23`), `.models.profile.OpticalTrainInfo` (`24`) |
| `nina_planner/horizon.py` | 136 | `Horizon(BaseModel)` (`32-96`) | `Horizon._sort_and_wrap` (`38-60`), `Horizon.is_custom` (`62-70`), `Horizon.altitude_at` (`72-96`), `_clamp` (`99-100`), `parse_horizon` (`103-120`), `fetch_horizon` (`123-136`) | `_MAX_HORIZON_ALT` (`29`) | `.client` (`25`) |
| `nina_planner/dso_catalog.py` | 59 | `Dso` (`22-38`, `@dataclass(frozen=True)`) | `_load_catalog` (`41-56`) | `CATALOG` (`59`) | *(none)* |
| `nina_planner/sequence_state.py` | 241 | *(none)* | `_walk` (`14-21`), `_find` (`24-25`), `_target_area` (`28-29`), `_issues` (`32-47`), `_children` (`50-70`), `_counts_exposures` (`73-96`), `_exposure_groups` (`99-114`), `_exposure` (`117-150`), `_horizon` (`153-161`), `project_sequence_state` (`164-241`) | `_TARGET_AREA` (`11`) | `.models.plan.ATTRIBUTION_RE` (`9`) |
| `nina_planner/archive.py` | 1204 | `ArchiveError(Exception)` (`118-119`) | `_human_bytes` (`125-131`), `_sha256` (`134-139`), `_inside` (`142-147`), `_looks_like_plan_file` (`150-163`), `_folder_name` (`166-194`), `_dest_child` (`197-215`), `_recover_replaced` (`218-238`), `_merge_weather` (`241-272`), `_write_image_metadata_csv` (`275-300`), `_progress_facts` (`306-344`), `_select_frames` (`347-384`), `_without_degenerate` (`387-403`), `_divert_frames` (`406-441`), `disk_report` (`444-525`), `_copy_file` (`531-539`), `_archive_one` (`542-836`), `_build_parser` (`842-917`), `_resolve_root` (`920-921`), `run` (`924-1188`), `main` (`1191-1200`) | `_ARCHIVE_TYPES` (`101`), `_FOLDER_NAMES` (`106-112`), `_INDEX_NAME` (`113`), `_MANIFEST_NAME` (`114`), `_PLAN_KEYS` (`115`) | `.config`, `.metadata`, `.plan_utils` (`90`), `.imaging` (`91`), `.models.plan.ObservationPlan` (`92`), `.progress` (`93-99`) |
| `nina_planner/purge.py` | 1091 | `PurgeError(Exception)` (`121-122`) | `_now` (`128-129`), `_norm` (`132-134`), `_append_index` (`137-139`), `_tree_size` (`142-150`), `_unrecorded_count` (`153-164`), `_unique_join` (`167-171`), `_plan_file_gate` (`174-219`), `_resolve_root` (`222-223`), `_idle_state` (`226-244`), `_read_manifests` (`250-296`), `_classify` (`302-409`), `_quarantine` (`415-472`), `_purge_quarantine` (`475-531`), `_quarantine_gates` (`534-609`), `_sweep_plan_files` (`612-698`), `_build_parser` (`704-758`), `run` (`761-1077`), `main` (`1080-1087`) | `_QUARANTINE_DIR` (`110`), `_INDEX_NAME` (`111`), `_FRAME_TYPES` (`112`), `_SIDECAR_NAMES` (`116-118`) | `.config`, `.metadata`, `.plan_utils` (`95`), `.archive` (`96-105`), `.client._sequence_is_running` (`106`), `.imaging` (`107`), `.models.plan.ObservationPlan` (`108`) |
| `nina_planner/pin_id.py` | 166 | `PinError(Exception)` (`39-40`) | `_resolve` (`46-51`), `pin_plan_file` (`57-86`), `_build_parser` (`92-123`), `run` (`126-151`), `main` (`154-162`) | *(none)* | `.config`, `.plan_utils` (`36`) |

### 3.3 MCP Tool Modules (`nina_planner/tools/`)

| File Path | Lines | `@mcp.tool()` Endpoints (`start-end`) | Private Helpers (`start-end`) | Constants / Globals (`line`) | Outbound Internal Imports |
|---|---:|---|---|---|---|
| `nina_planner/tools/__init__.py` | 1 | *(none)* | *(none)* | *(none)* | *(none)* |
| `nina_planner/tools/lifecycle.py` | 231 | `stop_nina` (`93-155`), `start_nina` (`158-188`), `get_nina_status` (`191-231`) | `_run_launch_command` (`22-33`), `_launch_nina_interactive` (`36-60`), `_raise_for_command_failure` (`63-78`), `_poll_until_async` (`81-90`) | *(none)* | `..client` (`9`), `..app.mcp` (`10`), `..system_time` (`11-19`) |
| `nina_planner/tools/equipment.py` | 310 | `get_site_equipment_status` (`170-176`), `get_site_profile` (`179-289`), `get_events` (`292-298`), `get_logs` (`301-310`) | `_convert_keys` (`22-27`), `_convert_to_met` (`30-79`), `_get_site_equipment_status` (`82-167`) | *(none)* | `..client` (`8`), `..app.mcp` (`9`), `..horizon.fetch_horizon` (`10`), `..models.*` (`11-18`, `85-95`), `..nina_utils` (`19`) |
| `nina_planner/tools/sky.py` | 862 | `resolve_target` (`107-118`), `target_altitudes` (`172-200`), `best_targets` (`777-823`), `sun_times` (`837-846`), `moon_times` (`849-862`) | `_fetch_simbad` (`54-63`), `_resolve_via_simbad_web` (`66-86`), `_resolve_via_sesame` (`89-91`), `_resolve_name` (`94-104`), `_altitudes` (`121-169`), `_surface_brightness` (`224-237`), `_is_compact` (`240-241`), `_difficulty` (`244-251`), `_easiness` (`254-257`), `_body_altitudes` (`281-291`), `_sun_altitudes` (`294-296`), `_site_dict` (`299-305`), `_interp_time` (`308-311`), `_sun_events` (`314-421`), `_moon_altitudes` (`478-480`), `_elongation_series` (`483-502`), `_next_angle` (`505-511`), `_prev_angle` (`514-516`), `_crossing_time` (`519-532`), `_phase_from_elongation` (`535-565`), `_moon_events` (`568-632`), `_rank_catalog` (`635-774`), `_site_location_and_now` (`826-834`) | `_SIMBAD_MIRRORS` (`34-37`), `_SIMBAD_TIMEOUT_S` (`38`), `_COORD_RE` (`45-51`), `_LEVEL_THRESHOLDS` (`210-213`), `_SCORE_RANGE` (`216-219`), `_COMPACT_ARCMIN` (`221`), `_DARKNESS_SUN_ALT` (`265`), `_SUN_CROSSINGS` (`266-271`), `_SUN_BACKFILL_H` (`276`), `_SUN_FORWARD_H` (`277`), `_SUN_STEP_MIN` (`278`), `_MOON_HORIZON_ALT` (`438`), `_MOON_FORWARD_H` (`444`), `_MOON_STEP_MIN` (`445`), `_PHASE_BACK_D` (`453`), `_PHASE_FORWARD_D` (`454`), `_PHASE_STEP_H` (`455`), `_PHASE_EVENT_ANGLES` (`460-465`), `_PHASE_NEW_MAX` (`474`), `_PHASE_FULL_MIN` (`475`), `_PHASE_QUARTER_BAND` (`476`) | `..dso_catalog` (`22`), `..app.mcp` (`23`), `..horizon.Horizon` (`24`), `.equipment` (`25`) |
| `nina_planner/tools/sequence.py` | 454 | `load_sequence` (`24-63`), `stop_sequence` (`419-423`), `start_sequence` (`426-434`), `get_sequence_state` (`437-454`) | `_full_mode_note` (`66-113`), `_require_sequencer_plus` (`126-173`), `_load_many` (`176-416`) | `SEQUENCER_PLUS_PLUGIN_NAMES` (`123`) | `..client`, `..config`, `..metadata`, `..plan_utils` (`6`), `..app.mcp` (`7`), `..models.*` (`8-11`), `..progress` (`12`), `..sequence` (`13-19`), `..sequence_state` (`20`), `.equipment` (`21`) |
| `nina_planner/tools/plans.py` | 1238 | `get_frame_stats` (`34-97`), `write_plan_file` (`164-199`), `get_plan_progress` (`202-311`), `audit_plans` (`759-1165`), `list_plans` (`1168-1199`), `get_nina_settings` (`1215-1238`) | `_expand_mosaic` (`103-161`), `_forbid_unknown_arguments` (`314-338`), `_probe_plan_id` (`362-376`), `_plan_ids_in_path` (`379-390`), `_load_project_plans` (`393-421`), `_plan_type_totals` (`424-441`), `_count_fits` (`444-472`), `_own_token_rows` (`475-511`), `_junk_count` (`514-542`), `_status_for` (`545-599`), `_audit_row` (`602-661`), `_action_for` (`664-670`), `_written_iso` (`673-682`), `_light_expected_per_pointing` (`685-692`), `_never_shot_report` (`695-756`) | `_AUDIT_TYPES` (`347`), `_FITS_SUFFIXES` (`348`), `_AUDIT_BROKEN` (`353`), `_PLAN_ID_CANDIDATE_RE` (`359`), `_SETTING_NAMES` (`1202-1208`), `_ARCHIVE_GATE_NAMES` (`1212`) | `..client`, `..config`, `..metadata`, `..plan_utils` (`13`), `..app.mcp` (`14`), `..imaging` (`15-18`), `..models.*` (`19-21`), `..mosaic` (`22`), `..progress` (`23-30`), `.equipment` (`31`) |

### 3.4 Pydantic Data Models (`nina_planner/models/`)

| File Path | Lines | Pydantic Models (`start-end`) | Validators, Properties & Functions (`start-end`) | Constants / Globals (`line`) | Outbound Internal Imports |
|---|---:|---|---|---|---|
| `nina_planner/models/__init__.py` | 66 | *(re-exports 31 symbols)* | *(none)* | `__all__` (`34-66`) | `.camera`, `.dome`, `.filter_wheel`, `.flat_device`, `.focuser`, `.frame`, `.guider`, `.meridian`, `.mount`, `.observatory`, `.pointing`, `.profile`, `.rotator`, `.safety_monitor`, `.switch`, `.weather` (`3-32`) |
| `nina_planner/models/strict.py` | 16 | `StrictModel(BaseModel)` (`6-16`) | *(none — `ConfigDict(extra="forbid")`)* | *(none)* | *(none)* |
| `nina_planner/models/plan.py` | 455 | `LightExposurePlan` (`33-38`), `FlatExposurePlan` (`41-48`), `DarkExposurePlan` (`51-55`), `BiasExposurePlan` (`58-61`), `AutofocusConfig` (`64-71`), `GuidingConfig` (`74-86`), `CoolerConfig` (`89-98`), `ConstraintsConfig` (`101-115`), `QualityConfig` (`118-149`), `MosaicConfig` (`155-235`), `ObservationPlan` (`238-455`) | `ObservationPlan._pointings_or_mosaic` (`343-357`), `ObservationPlan._plan_id_charset` (`359-375`), `ObservationPlan._base_plan_id` (`377-382`), `ObservationPlan.per_pointing_plan_id` (`384-391`), `ObservationPlan.effective_plan_id` (`393-404`), `ObservationPlan.attribution_token` (`406-417`), `ObservationPlan.plan_token` (`419-425`), `ObservationPlan.target_prefix` (`427-440`), `ObservationPlan.calibration_prefix` (`442-455`) | `_PLAN_ID_ALLOWED` (`22`), `ATTRIBUTION_RE` (`30`) | `.pointing.Pointing` (`13`), `.strict.StrictModel` (`14`) |
| `nina_planner/models/pointing.py` | 90 | `PointingInfo(BaseModel)` (`10-27`), `Pointing(StrictModel)` (`30-90`) | `Pointing.requests_rotation` (`78-90`) | *(none)* | `.strict.StrictModel` (`7`) |
| `nina_planner/models/frame.py` | 109 | `FrameStats(BaseModel)` (`53-109`) | `FrameStats._normalize` (`92-109`) | `_FIELD_MAP` (`15-38`), `_FLOAT_FIELDS` (`40-49`), `_INT_FIELDS` (`50`) | `..nina_utils` (`9`) |
| `nina_planner/models/request.py` | 124 | `SequenceRequest(StrictModel)` (`38-124`) | `SequenceRequest._check_variant` (`100-124`) | `Action` (`13-18`), `DARK_VARIANTS` (`23`), `VARIANTS` (`30-35`) | `.strict.StrictModel` (`7`) |
| `nina_planner/models/profile.py` | 143 | `FilterInfo` (`14-19`), `SiteLocationInfo` (`22-27`), `EquipmentConfig` (`30-43`), `OpticalTrainInfo` (`46-91`), `ObservatoryProfile` (`94-143`) | `OpticalTrainInfo.fov_deg` (`58-84`), `OpticalTrainInfo._compute_fov` (`86-91`), `ObservatoryProfile.local_image_save_path` (`119-128`), `ObservatoryProfile.filter_position` (`130-143`) | *(none)* | `..horizon.Horizon` (`10`), `..imaging.windows_to_local` (`11`) |
| `nina_planner/models/observatory.py` | 38 | `ObservatoryEquipment(BaseModel)` (`20-38`) | *(none)* | *(none)* | `.camera`, `.dome`, `.filter_wheel`, `.flat_device`, `.focuser`, `.guider`, `.mount`, `.rotator`, `.safety_monitor`, `.switch`, `.weather` (`7-17`) |
| `nina_planner/models/camera.py` | 220 | `SensorInfo` (`36-43`), `GainInfo` (`46-53`), `ExposureInfo` (`56-62`), `OffsetInfo` (`65-71`), `BinningInfo` (`74-78`), `ThermalInfo` (`81-90`), `ReadoutInfo` (`93-97`), `CameraDevice` (`100-220`) | `_binning_axis` (`12-16`), `_parse_binning_mode` (`19-33`), `CameraDevice._derive_fields` (`125-220`) | *(none)* | `..nina_utils` (`9`) |
| `nina_planner/models/mount.py` | 77 | `MountDevice(BaseModel)` (`12-77`) | `MountDevice._derive_fields` (`33-77`) | *(none)* | `..nina_utils.ascom_float` (`7`), `.meridian.MeridianInfo` (`8`), `.pointing.PointingInfo` (`9`) |
| `nina_planner/models/focuser.py` | 40 | `FocuserDevice(BaseModel)` (`10-40`) | `FocuserDevice._derive_fields` (`28-40`) | *(none)* | `..nina_utils` (`7`) |
| `nina_planner/models/filter_wheel.py` | 29 | `FilterWheelDevice(BaseModel)` (`8-29`) | `FilterWheelDevice._derive_fields` (`22-29`) | *(none)* | *(none)* |
| `nina_planner/models/guider.py` | 69 | `GuiderMetric(BaseModel)` (`10-14`), `GuiderDevice(BaseModel)` (`17-69`) | `GuiderDevice._derive_fields` (`36-69`, inner `_build_metric` at `53-61`) | *(none)* | `..nina_utils.ascom_float` (`7`) |
| `nina_planner/models/rotator.py` | 51 | `RotatorDevice(BaseModel)` (`10-51`) | `RotatorDevice._derive_fields` (`25-51`) | *(none)* | `..nina_utils.ascom_float` (`7`) |
| `nina_planner/models/dome.py` | 67 | `DomeDevice(BaseModel)` (`10-49`) | `DomeDevice._derive_fields` (`28-49`), `compute_dome_status` (`52-67`) | *(none)* | `..nina_utils.ascom_float` (`7`) |
| `nina_planner/models/switch.py` | 55 | `ReadonlySwitchInfo` (`8-14`), `WritableSwitchInfo` (`17-40`), `Switch` (`43-55`) | `WritableSwitchInfo.is_on` (`29-40`) | *(none)* | *(none)* |
| `nina_planner/models/flat_device.py` | 27 | `FlatDevice(BaseModel)` (`8-27`) | *(none)* | *(none)* | *(none)* |
| `nina_planner/models/weather.py` | 48 | `Weather(BaseModel)` (`10-48`) | `Weather._derive_fields` (`33-48`) | *(none)* | `..nina_utils.ascom_float` (`7`) |
| `nina_planner/models/safety_monitor.py` | 19 | `SafetyMonitorDevice(BaseModel)` (`8-19`) | *(none)* | *(none)* | *(none)* |
| `nina_planner/models/meridian.py` | 12 | `MeridianInfo(BaseModel)` (`8-12`) | *(none)* | *(none)* | *(none)* |

### 3.5 N.I.N.A. Sequence AST Compiler (`nina_planner/sequence/`)

| File Path | Lines | Functions (`start-end`) | Constants / Globals (`line`) | Outbound Internal Imports |
|---|---:|---|---|---|
| `nina_planner/sequence/__init__.py` | 198 | *(re-exports 88 symbols)* | `__all__` (`109-198`) | `.builders`, `.instructions`, `.nodes`, `.targets`, `.variants` (`10-107`) |
| `nina_planner/sequence/nodes.py` | 346 | `_fix_provider` (`17-36`), `_add_refs` (`39-84`), `_child` (`87-88`), `_values` (`91-94`), `_to_minutes` (`97-98`), `_to_seconds` (`101-102`), `_radec_coordinates` (`105-118`), `_azalt_coordinates` (`121-131`), `_round_robin` (`134-160`), `_container_base` (`166-197`), `container_root` (`200-214`), `container_start` (`217-223`), `container_target` (`226-232`), `container_end` (`235-241`), `container_sequential` (`244-257`), `container_deepsky` (`260-291`), `trigger_meridian_flip` (`297-301`), `trigger_autofocus_after_filter_change` (`304-308`), `trigger_autofocus_after_temperature_change` (`311-315`), `trigger_autofocus_after_exposures` (`318-322`), `trigger_autofocus_after_hfr_increase` (`325-329`), `trigger_center_after_drift` (`332-341`), `trigger_restore_guiding` (`344-346`) | `NINA_FLATS_ALTITUDE` (`12`), `NINA_FLATS_AZIMUTH_DAWN` (`13`), `NINA_FLATS_AZIMUTH_DUSK` (`14`) | `..models.plan.LightExposurePlan` (`10`) |
| `nina_planner/sequence/instructions.py` | 582 | `connect_all_equipment` (`15-19`), `unpark_scope` (`22-24`), `park_scope` (`27-29`), `home_scope` (`32-36`), `warm_camera` (`39-43`), `cool_camera` (`46-51`), `start_guiding` (`54-58`), `stop_guiding` (`61-63`), `set_tracking` (`66-79`), `slew_and_center` (`82-86`), `slew_center_and_rotate` (`89-95`), `slew_to_azalt` (`98-102`), `run_autofocus` (`105-107`), `annotation` (`110-114`), `switch_filter` (`117-140`), `close_cover` (`143-146`), `open_cover` (`149-151`), `sky_flats` (`154-182`), `wait_until_safe` (`188-192`), `wait_until_above_horizon` (`195-205`), `wait_until_above_altitude` (`208-219`), `wait_until_time` (`222-227`), `wait_until_dusk` (`230-234`), `wait_until_dawn` (`237-241`), `wait_until_sunset` (`244-248`), `wait_if_sun_altitude` (`251-263`), `wait_if_sun_altitude_above` (`266-268`), `wait_if_sun_altitude_below` (`271-273`), `wait_for_timespan` (`276-282`), `loop_until_sun_altitude` (`288-298`), `loop_until_sun_altitude_above` (`301-303`), `loop_until_sun_altitude_below` (`306-308`), `loop_until_time` (`311-316`), `loop_until_sunset` (`319-324`), `loop_until_sunrise` (`327-332`), `loop_until_dawn` (`335-339`), `loop_until_dusk` (`342-346`), `loop_until_meridian` (`349-354`), `loop_while_safe` (`357-359`), `loop_while_above_horizon` (`362-370`), `loop_while_above_altitude` (`373-381`), `loop_for_iterations` (`384-389`), `dither_after_exposures` (`392-396`), `take_exposure` (`399-411`), `smart_exposure` (`414-450`), `send_event` (`456-460`), `_step_word` (`463-464`), `run_starting_event` (`467-473`), `run_finished_event` (`476-490`), `end_instruction` (`496-500`), `declare_variable` (`503-518`), `loop_while_expression` (`521-533`), `initialize_array` (`536-548`), `get_array` (`551-566`), `put_array` (`569-582`) | *(none)* | `.nodes` (`10`) |
| `nina_planner/sequence/builders.py` | 494 | `acquisition_container` (`57-98`), `_validated_pointing` (`101-118`), `sequence_deepsky` (`121-145`), `sequence_calibration` (`148-187`), `sequence_warm_camera` (`190-196`), `sequence_cool_camera` (`199-207`), `sequence_park_scope` (`244-247`), `sequence_home_scope` (`250-258`), `sequence_unpark_scope` (`261-272`), `sequence_count_exposure` (`290-338`), `sequence_wait_until_sunset` (`341-358`), `sequence_wait_until_dawn` (`361-376`), `sequence_wait_until_dusk` (`379-386`), `sequence_safetynet` (`389-423`), `sequence_end` (`426-471`), `_sequence_with_steps` (`474-494`) | `COUNT_ARRAY` (`280`), `COUNT_VARIABLE` (`281`), `EXPOSURE_GROUP_NAME` (`287`) | `..models.*` (`11-13`), `.instructions` (`14-44`), `.nodes` (`45-52`) |
| `nina_planner/sequence/targets.py` | 415 | `_contains_type` (`69-82`), `build_sequence_many` (`85-102`), `target_payload_darks` (`108-190`), `target_payload_flats` (`196-258`), `target_payload_lights` (`264-415`) | *(none)* | `..models.*` (`5-8`), `.builders` (`9-26`), `.instructions` (`27-52`), `.nodes` (`53-66`) |
| `nina_planner/sequence/variants.py` | 114 | `dark_variant_reasons` (`23-86`), `validate_dark_variant` (`89-114`) | `_UNUSABLE_COVER_STATES` (`20`) | `..models.observatory` (`14`), `..models.profile` (`15`), `..models.request.DARK_VARIANTS` (`16`) |

### 3.6 Standalone Repository Scripts & Skill Utilities (`scripts/`, `.agents/skills/`)

| File Path | Lines | Classes / Exceptions | Functions (`start-end`) | Constants / Globals (`line`) | Outbound Imports |
|---|---:|---|---|---|---|
| `scripts/gen_sequence.py` | 409 | `ScriptError(Exception)` (`73-74`) | `_fetch_profile` (`80-88`), `_fetch_equipment` (`91-96`), `_generate` (`102-249`), `_build_parser` (`255-333`), `_validate` (`336-356`), `_run` (`358-383`), `main` (`386-405`) | *(none)* | `nina_planner.*` (`44-63`) |
| `scripts/update_readme.py` | 105 | *(none)* | `insert_file_contents_and_links` (`66-89`), `main` (`92-101`) | `PROJECT_ROOT` (`51`), `README_PATH` (`52`), `_PATTERN` (`58-63`) | stdlib only (`os`, `re`, `pathlib`) |
| `scripts/refresh_fixtures.py` | 72 | *(none)* | `fetch` (`26-43`), `main` (`46-68`) | `DEFAULT_OUT` (`19-22`), `ENDPOINTS` (`23`) | stdlib only (`argparse`, `json`, `sys`, `urllib.request`, `pathlib`) |
| `.agents/skills/site-conditions/fetch_conditions.py` | 817 | `FetchError(Exception)` (`103-106`), `ParseError(Exception)` (`109-112`) | `_fmt_coord` (`115-131`), `fetch` (`134-167`), `_mins` (`170-178`), `_is_dark` (`181-212`), `_fmt_run` (`215-217`), `_compress` (`220-242`), `_row_cells` (`245-264`), `_fit` (`267-286`), `_row_ints` (`289-296`), `_row_frost` (`299-311`), `_row_wind` (`314-335`), `parse` (`338-507`), `slice_hours` (`510-538`), `summarize` (`541-595`), `_tag` (`598-599`), `summarize_conditions` (`602-698`), `main` (`701-813`) | `BASE_URL` (`30`), `USER_AGENT` (`31`), `RE_*` / `ROW_*` (`34-100`) | stdlib only (`argparse`, `json`, `re`, `sys`, `urllib.*`, `datetime`) |

---

## 4. Test Suite Inventory (`.opencode/local/mcps/nina_planner/tests/`)

| Test File | Lines | Test Classes / Helpers | Target Production Module(s) |
|---|---:|---|---|
| `tests/conftest.py` | 43 | `make_metadata_row` (`15-36`) | Shared CSV metadata fixture builder |
| `tests/fake_nina.py` | 84 | `_envelope` (`20-28`), `FakeNina` (`31-84`) | Stateful fake N.I.N.A. sequencer simulator |
| `tests/sequence_helpers.py` | 59 | `build_sequence_park` (`20-22`), `build_sequence_lights` (`25-36`), `build_sequence_darks` (`39-48`), `build_sequence_flats` (`51-59`) | Single-step wrappers around `build_sequence_many` |
| `tests/test_archive.py` | 1,932 | 11 test classes (`ArchiveBase`, `ArchiveHappyPathTest`, `ArchiveGateTest`, `ArchiveIdempotencyTest`, `ArchiveAttributionTest`, etc.) | `nina_planner/archive.py` |
| `tests/test_camera.py` | 170 | 4 test classes (`CameraDeviceValidationTest`, `CameraDeviceDeriveFieldsTest`, `CameraDeviceBinningModesTest`, `CameraDeviceDewHeaterTest`) | `nina_planner/models/camera.py` |
| `tests/test_dome.py` | 130 | `DomeDeviceDeriveFieldsTest` (`8-126`) | `nina_planner/models/dome.py` |
| `tests/test_fake_nina.py` | 141 | `FakeNinaTest` (`30-137`) | `tests/fake_nina.py`, `nina_planner/sequence_state.py` |
| `tests/test_filter_wheel.py` | 57 | `FilterWheelDeviceDeriveFieldsTest` (`8-53`) | `nina_planner/models/filter_wheel.py` |
| `tests/test_focuser.py` | 80 | `FocuserDeviceDeriveFieldsTest` (`8-76`) | `nina_planner/models/focuser.py` |
| `tests/test_frame.py` | 63 | `FrameStatsNormalizationTest` (`11-59`) | `nina_planner/models/frame.py` |
| `tests/test_gen_sequence.py` | 671 | `GenSequenceScriptTest` (`99-440`), `_StubNina` (`506-552`), `GenSequenceLoadOverHttpTest` (`556-667`) | `scripts/gen_sequence.py` |
| `tests/test_guider.py` | 110 | `GuiderDeviceDeriveFieldsTest` (`18-61`), `GuiderDeviceNestedRmsErrorTest` (`64-106`) | `nina_planner/models/guider.py` |
| `tests/test_horizon.py` | 191 | `HorizonModelTest` (`9-88`), `FetchHorizonTest` (`91-125`), `ProfileHorizonIntegrationTest` (`128-187`) | `nina_planner/horizon.py` |
| `tests/test_imaging.py` | 436 | `ReadImagingCsvTest` (`25-224`), `RealNinaSampleTest` (`227-299`), `WindowsToLocalTest` (`302-353`), `ImagingDefensivePathsTest` (`356-432`) | `nina_planner/imaging.py` |
| `tests/test_live.py` | 279 | `LiveNinaTest` (`71-279`, `@pytest.mark.live`) | End-to-end live N.I.N.A. hardware/VM integration |
| `tests/test_mcp_entrypoint.py` | 103 | `EntryPointHandshakeTest` (`48-72`), `EntryPointWiringTest` (`75-99`) | `nina_planner/__main__.py`, `nina_planner/server.py`, `nina_planner/app.py` |
| `tests/test_mosaic.py` | 290 | `PaneFovTest` (`24-54`), `GridSizeTest` (`57-127`), `SphericalOffsetTest` (`130-179`), `PlanMosaicTest` (`182-286`) | `nina_planner/mosaic.py` |
| `tests/test_mount.py` | 277 | 6 test classes (`MountDeviceHoursParsingTest`, `MountDeviceRaHoursValidationTest`, etc.) | `nina_planner/models/mount.py` |
| `tests/test_nina_settings.py` | 106 | `NinaSettingsTest` (`27-102`) | `nina_planner/tools/plans.py:get_nina_settings` |
| `tests/test_nina_utils.py` | 190 | 6 test classes (`AscomFloatTest`, `AscomIntTest`, `AscomDatetimeTest`, etc.) | `nina_planner/nina_utils.py` |
| `tests/test_pin_id.py` | 368 | `PinBase` (`35-95`), `PinCliTest` (`98-325`), `PinAcceptanceTest` (`328-364`) | `nina_planner/pin_id.py` |
| `tests/test_plan.py` | 476 | 8 test classes (`PlanIdCharsetTest`, `TargetPrefixTest`, `UnknownFieldTest`, etc.) | `nina_planner/models/plan.py`, `nina_planner/plan_utils.py` |
| `tests/test_plans_audit.py` | 1,078 | `AuditBase` (`62-187`), `AuditTest` (`190-991`), `ListPlansTest` (`994-1074`) | `nina_planner/tools/plans.py` (`audit_plans`, `list_plans`) |
| `tests/test_pointing.py` | 86 | `PointingPositionAngleDegValidationTest` (`20-50`), `PointingMosaicMetadataTest` (`53-82`) | `nina_planner/models/pointing.py` |
| `tests/test_profile.py` | 191 | `OpticalTrainInfoFovTest` (`18-64`), `ObservatoryProfileFilterPositionTest` (`67-112`), `ObservatoryProfileLocalSavePathTest` (`115-191`) | `nina_planner/models/profile.py` |
| `tests/test_progress.py` | 1,571 | 10 test classes (`PlanProgressTest`, `QualityFilterTest`, `PlanWithRemainingTest`, `CalibrationAgeTest`, etc.) | `nina_planner/progress.py` |
| `tests/test_purge.py` | 1,220 | 8 test classes (`PurgeBase`, `PurgeDryRunTest`, `PurgeApplyTest`, `PurgeReleaseTest`, `PurgeJunkTest`, etc.) | `nina_planner/purge.py` |
| `tests/test_rotator.py` | 114 | `RotatorDeviceDeriveFieldsTest` (`8-70`), `RotatorDeviceNinaSyncedKeyTest` (`73-110`) | `nina_planner/models/rotator.py` |
| `tests/test_sequence.py` | 2,548 | 12 test classes (`RoundRobinBatchingTest`, `ContainerDefaultsTest`, `ReferenceNormalizationTest`, `PointingIndexValidationTest`, etc.) | `nina_planner/sequence/*.py` |
| `tests/test_sequence_state.py` | 660 | `ProjectSequenceStateTests` (`75-358`), `FinishedRunTests` (`361-414`), `LightsSequenceTests` (`417-599`), `ChildrenShapeTests` (`602-650`) | `nina_planner/sequence_state.py` |
| `tests/test_sequence_state_contract.py` | 75 | `GeneratedSequenceContract` (`40-75`) | Contract between `sequence/*` and `sequence_state.py` |
| `tests/test_server.py` | 3,857 | `ConvertToMetTest` (`67-188`), `HelperFunctionsTest` (`191-616`), `McpToolDirectTest` (`619-3853`) | `nina_planner/tools/*.py`, `nina_planner/client.py`, `nina_planner/server.py` |
| `tests/test_sky.py` | 1,139 | 12 test classes (`AltitudesTest`, `NameResolutionTest`, `SurfaceBrightnessTest`, `DifficultyTest`, `SunEventsTest`, etc.) | `nina_planner/tools/sky.py`, `nina_planner/dso_catalog.py` |
| `tests/test_switch.py` | 117 | 4 test classes (`WritableSwitchInfoIsOnTest`, `WritableSwitchInfoIsOnBinaryTest`, `SwitchTest`, `SwitchMissingListsTest`) | `nina_planner/models/switch.py` |
| `tests/test_system_time.py` | 865 | 8 test classes (`CommandBuildersTest`, `NinaExePathTest`, `WindowsInteropAvailableTest`, `StopNinaTest`, `StartNinaTest`, etc.) | `nina_planner/system_time.py`, `nina_planner/tools/lifecycle.py` |
| `tests/test_variants.py` | 223 | `DarkVariantReasonsTest` (`72-167`), `ValidateDarkVariantTest` (`170-219`) | `nina_planner/sequence/variants.py`, `nina_planner/models/request.py` |
| `tests/test_weather.py` | 99 | `WeatherDeriveFieldsTest` (`8-95`) | `nina_planner/models/weather.py` |
