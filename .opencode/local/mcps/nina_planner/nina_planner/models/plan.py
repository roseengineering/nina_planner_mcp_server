"""The observation-plan file schema: exposures, cadence, mosaic, identity.

Strict (``extra="forbid"``): a typo in a plan file fails validation instead
of silently loading as if it were honoured.
"""

import hashlib
import json
import re

from pydantic import Field, field_validator, model_validator

from .pointing import Pointing
from .strict import StrictModel

# A plan id is stamped into the target name, which N.I.N.A. rewrites on its
# way into the file path (it turned "[" and "]" into "_"), so an id only works
# if it survives that rewrite verbatim. Deliberately conservative charset,
# proven to survive: ASCII letters, digits, '.' and '-' only. "_" is refused
# because it is the attribution-token delimiter; everything else is refused
# because we cannot prove N.I.N.A. leaves it alone.
_PLAN_ID_ALLOWED = re.compile(r"[A-Za-z0-9.-]+")

# How a plan id reads inside a stamped target name: ``_<id>_``, optionally
# carrying the pointing suffix ``-<n>`` (mosaic panes). The id part is
# non-greedy so the suffix binds as ``-(\d+)`` rather than swallowing it.
# THIS is the one attribution pattern: sequence_state and every consumer
# derive their view from it, so a custom plan_id can never be recognised in
# one place and missed in another.
ATTRIBUTION_RE = re.compile(r"_([A-Za-z0-9.-]+?)(?:-(\d+))?_")


class LightExposurePlan(StrictModel):
    """One filter's light budget: exposure duration in seconds and frame count."""

    filter_name: str
    exposure_time_seconds: float = Field(gt=0)
    total_count: int = Field(ge=1)


class FlatExposurePlan(StrictModel):
    """One filter's flat budget; list order is shoot order (see flat_frames)."""

    # No exposure_time_seconds: N.I.N.A.'s SkyFlat picks the exposure itself
    # to hit a target signal level, so a plan-specified duration is never
    # sent. List order is shoot order — see the flat_frames description.
    filter_name: str
    total_count: int = Field(ge=1)


class DarkExposurePlan(StrictModel):
    """Dark frames of one exposure duration (seconds) and count."""

    exposure_time_seconds: float = Field(gt=0)
    total_count: int = Field(ge=1)


class BiasExposurePlan(StrictModel):
    """Bias frames: a count only — bias exposures have no duration."""

    total_count: int = Field(ge=1)


class AutofocusConfig(StrictModel):
    """Autofocus cadence and thresholds (HFR in pixels, temperature in Celsius)."""

    reference_filter_name: str = Field(default="L")
    hfr_increase_sample_size: int = Field(default=3, ge=1)
    hfr_increase_threshold_percent: float = Field(default=15.0, gt=0)
    every_n_exposures: int = Field(default=10, ge=1)
    threshold_celsius: float = Field(default=1.0, gt=0)


class GuidingConfig(StrictModel):
    """Dither and drift-check cadence; drift limit in arcminutes."""

    dither_every_n_exposures: int = Field(
        default=2,
        ge=0,
        description=(
            "Dither after this many exposures. 0 means never dither: the "
            "group omits its DitherAfterExposures trigger entirely."
        ),
    )
    check_drift_every_n_exposures: int = Field(default=6, ge=1)
    max_drift_arcmin: float = Field(default=1.5, gt=0)


class CoolerConfig(StrictModel):
    """Camera cooler target; setpoint in Celsius within ASCOM's −40…+30 range."""

    on: bool = True
    setpoint_celsius: float = Field(
        default=-10.0,
        ge=-40.0,
        le=30.0,
        description="Target setpoint temperature in degrees Celsius.",
    )


class ConstraintsConfig(StrictModel):
    """Altitude floor for the whole plan: degrees above horizon or skyline."""

    min_altitude: float = Field(
        default=30.0,
        ge=0.0,
        le=90.0,
        description="The baseline minimum altitude floor in degrees. ",
    )
    horizon_offset_degrees: float = Field(
        default=2.0,
        ge=0.0,
        le=10.0,
        description="Safety buffer added to custom horizon file profile (default 2°).",
    )


class QualityConfig(StrictModel):
    """Optional light-frame quality thresholds for the progress tools.

    The ONLY place quality thresholds live: ``load_sequence`` and
    ``get_plan_progress`` read them from the plan file, so every tool that
    counts this plan's frames applies the same rules — there is deliberately
    no per-call override. All four default to ``None`` (no filtering).
    Lights only; fail-closed when active: a light frame missing an enabled
    metric (or carrying NINA's ``0`` for an unmeasured value) does NOT
    count toward ``total_count``.
    """

    max_hfr: float | None = Field(
        default=None,
        description="Exclude light frames with HFR above this value.",
    )
    max_hfr_st_dev: float | None = Field(
        default=None,
        description=(
            "Exclude light frames whose per-star HFR spread (HFR sigma) is "
            "above this value — catches uneven star shapes from a guiding "
            "spike, tilt or flexure that an acceptable mean HFR hides."
        ),
    )
    min_detected_stars: int | None = Field(
        default=None,
        description="Exclude light frames detected with fewer stars than this.",
    )
    max_guiding_rms_arcsec: float | None = Field(
        default=None,
        description="Exclude light frames guided above this RMS (arcsec).",
    )


# --- Root Plan ---


class MosaicConfig(StrictModel):
    """How to derive a mosaic's pointings — intent in, grid recorded out.

    The caller states WHAT to cover (``center`` + ``coverage`` in arcmins,
    measured in the grid frame BEFORE ``pa_deg`` rotation) and how much
    panes may overlap; write_plan_file derives HOW (cols x rows, sized per
    axis with ``ceil`` so it never under-covers) from the rig's pane FOV at
    write time and stamps the decision back into ``cols``/``rows``/
    ``pane_fov_arcmins`` as provenance. Those three are ``None`` on input
    and never drive expansion — an expanded plan's ``pointings`` are the
    authority.
    """

    center_ra_hours: float = Field(
        ge=0.0,
        lt=24.0,
        description="J2000 right ascension of the mosaic center in hours [0, 24).",
    )
    center_dec_deg: float = Field(
        ge=-90.0,
        le=90.0,
        description="J2000 declination of the mosaic center in degrees.",
    )
    coverage_width_arcmins: float = Field(
        gt=0.0,
        description=(
            "Sky width the mosaic must cover, in arcminutes, across the grid "
            "(columns axis, before position-angle rotation). Round up to the "
            "target's full extent — the grid may over-cover but never "
            "under-covers it."
        ),
    )
    coverage_height_arcmins: float = Field(
        gt=0.0,
        description=(
            "Sky height the mosaic must cover, in arcminutes, down the grid "
            "(rows axis, before position-angle rotation)."
        ),
    )
    overlap_pct: float = Field(
        default=20.0,
        gt=0.0,
        lt=100.0,
        description="Overlap between adjacent panes as a percent of one pane.",
    )
    pa_deg: float = Field(
        default=0.0,
        ge=0.0,
        lt=360.0,
        description=(
            "Position angle of the grid in degrees east of north; rotates "
            "the footprint about the center. Refused downstream unless the "
            "profile has a rotator."
        ),
    )
    # --- stamped by write_plan_file after expansion (provenance only) ---
    cols: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Derived column count — stamped by write_plan_file when it "
            "expands the mosaic, never supplied by the caller."
        ),
    )
    rows: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Derived row count — stamped by write_plan_file when it "
            "expands the mosaic, never supplied by the caller."
        ),
    )
    pane_fov_arcmins: tuple[float, float] | None = Field(
        default=None,
        description=(
            "The rig's single-pane field of view (width, height) in "
            "arcminutes, snapshotted from the profile when the mosaic was "
            "expanded — stamped by write_plan_file, never supplied by the "
            "caller."
        ),
    )


class ObservationPlan(StrictModel):
    """A complete observation plan: identity, pointings, exposures, cadence.

    The on-disk unit of work — always written with its ``pointings``
    expanded (see ``plan_utils.load_plan``), and identified for frame
    attribution by ``plan_id``.
    """

    target: str = Field(default="", description="catalog designation")
    intent: str = Field(
        default="", description="2 to 3 words describing the primary observation goal"
    )
    plan_id: str = Field(
        default="",
        description="stable identifier for this acquisition goal; stamped on write, "
        "derived from plan content if absent. Empty, or ASCII letters, digits, "
        "'.' and '-' only ('_' is the frame-attribution token delimiter, and "
        "N.I.N.A. rewrites other characters in the file path).",
    )
    description: str = Field(
        default="",
        description=(
            "explanation of the observation plan, including rationale, "
            "exposure goals, equipment, or sky constraints"
        ),
    )
    pointings: list[Pointing] = Field(
        default_factory=list,
        description=(
            "One or more target pointings (RA/Dec/PA, optional label) to image. "
            "Each pointing of a plan is loaded separately by pointing_index. "
            "May be empty — or omitted entirely — only when a `mosaic` block "
            "is present, in which case write_plan_file expands it into "
            "pointings on write; once expanded (any written file) the "
            "pointings are the authority and the mosaic block is provenance."
        ),
    )
    mosaic: MosaicConfig | None = Field(
        default=None,
        description=(
            "Optional mosaic specification: instead of listing pointings, "
            "give the center and the sky footprint to cover "
            "(coverage_*_arcmins) plus overlap/PA, and write_plan_file "
            "derives the cols x rows grid from the profile's optics, "
            "expands it into `pointings`, and stamps the derived cols/rows/"
            "pane FOV back here. Omit for an ordinary single- or "
            "multi-pointing plan given explicitly."
        ),
    )
    batch_size: int = Field(
        default=5, ge=0, description="Exposures per batch. 0 means unbatch."
    )
    calibration_window_days: int = Field(
        default=7,
        ge=1,
        description="Calibration frames (darks, bias, flats) are attributed "
        "only when shot within this many days of the plan's earliest "
        "attributed light, on either side of it. While the current time is "
        "inside that window the plan reports the calibration it still "
        "needs; once the current time falls outside it, no frame shot now "
        "could be credited, so remaining calibration is reported as 0 "
        "(window_open=false). Raise this value — or write a new plan — to "
        "reopen the window.",
    )

    # hardware
    cooler: CoolerConfig = Field(default_factory=CoolerConfig)
    constraints: ConstraintsConfig = Field(default_factory=ConstraintsConfig)
    autofocus: AutofocusConfig = Field(default_factory=AutofocusConfig)
    guiding: GuidingConfig = Field(default_factory=GuidingConfig)
    # Light-frame quality thresholds — see QualityConfig. Default: no filtering.
    quality: QualityConfig = Field(default_factory=QualityConfig)

    # exposures
    light_frames: list[LightExposurePlan] = Field(
        min_length=1,
        description="Light exposure specs; loaded with action='light'.",
    )
    flat_frames: list[FlatExposurePlan] = Field(
        min_length=1,
        description=(
            "Flat specs (filter + count only — N.I.N.A.'s SkyFlat picks the "
            "exposure itself); loaded with action='flat' and "
            "variant='dawn' (morning) or variant='dusk' (evening). "
            "List order is shoot order: dawn shoots as written, dusk shoots "
            "reversed; list broadband first, narrowband last so narrowband "
            "shoots while the sky is brighter in both variants."
        ),
    )
    dark_frames: list[DarkExposurePlan] = Field(
        min_length=1,
        description=(
            "Dark exposure specs; loaded with action='dark' "
            "(variant='wheel' by default, variant='cover' around a "
            "motorized cover, or variant='shutter' via camera shutter)."
        ),
    )
    bias_frames: list[BiasExposurePlan] = Field(
        min_length=1,
        description=(
            "Bias exposure specs; loaded with action='bias' (same variants "
            "as action='dark')."
        ),
    )

    @model_validator(mode="after")
    def _pointings_or_mosaic(self) -> "ObservationPlan":
        """A plan carries pointings, or a mosaic to derive them from — not.

        Neither (empty pointings with no mosaic points nowhere). Both
        present is legal: the pointings are already expanded and are the
        authority.
        """
        if not self.pointings and self.mosaic is None:
            raise ValueError(
                "a plan needs at least one pointing, or a `mosaic` block "
                "(center + coverage) for write_plan_file to expand into "
                "pointings"
            )
        return self

    @field_validator("plan_id")
    @classmethod
    def _plan_id_charset(cls, v: str) -> str:
        """Refuse ids that could not be matched back to their own frames.

        See ``_PLAN_ID_ALLOWED``. Empty stays legal: it means "derive one
        on write" (the derived id is always ``plan-<hex>``, which passes).
        """
        if v and not _PLAN_ID_ALLOWED.fullmatch(v):
            raise ValueError(
                f"plan_id {v!r} must be empty (a content-derived id is stamped "
                "instead) or consist only of ASCII letters, digits, '.' and "
                "'-' — '_' is the token delimiter, and every other character "
                "is refused because N.I.N.A. rewrites it in the file path, so "
                "the frames could never be matched back to this id."
            )
        return v

    def _base_plan_id(self) -> str:
        if self.plan_id:
            return self.plan_id
        data = self.model_dump(exclude={"plan_id"})
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return "plan-" + hashlib.sha1(canonical.encode()).hexdigest()[:12]

    def per_pointing_plan_id(self, pointing_index: int) -> str:
        """The per-pointing id form ``{base}-{index}``.

        Historically used for every pointing, including single-pointing
        plans; prefer :meth:`effective_plan_id`, which omits the suffix for
        a lone pointing.
        """
        return f"{self._base_plan_id()}-{pointing_index}"

    def effective_plan_id(self, pointing_index: int) -> str:
        """Plan id as stamped on this pointing's frames.

        A single-pointing plan carries the bare base id — it has no panes, so
        there is nothing to disambiguate, and its lights share a
        ``$$TARGETNAME$$`` folder with the plan-level calibration. A
        multi-pointing (mosaic) plan appends the 1-based pointing index so
        each pane's frames attribute independently.
        """
        if len(self.pointings) == 1:
            return self._base_plan_id()
        return self.per_pointing_plan_id(pointing_index)

    def attribution_token(self, pointing_index: int) -> str:
        """Target-name token stamped on frames so they attribute to a pointing.

        Underscore-delimited (``_plan-x_``): N.I.N.A. rewrites ``[`` and
        ``]`` to ``_`` when it substitutes ``$$TARGETNAME$$`` into the file
        path, so a bracketed token never survives and the frames stop
        attributing. The closing underscore keeps tokens from
        substring-matching each other (``_plan-x_`` never appears inside
        ``_plan-x-1_``). Legacy bracketed/parenthesised forms are still
        accepted on read — see ``progress._light_target_matches``.
        """
        return f"_{self.effective_plan_id(pointing_index)}_"

    def plan_token(self) -> str:
        """Plan-level target-name token, used for calibration.

        Calibration (darks/bias/flats) belongs to the whole plan, not to any
        pane, so it carries the bare base id with no pointing suffix.
        """
        return f"_{self._base_plan_id()}_"

    def target_prefix(self, label: str | None = "") -> str:
        """The readable head of a target name: ``<target> <intent> [- label]``.

        ``$$TARGETNAME$$`` turns this (plus the plan-id token) into the
        imaging directory. Empty target/intent segments are skipped (no
        stray separator), and an empty ``label`` appends nothing. Lights
        (``sequence_deepsky``) and calibration (``calibration_prefix``) both
        build their prefix here on purpose: two spellings would split one
        plan's frames across two folders.
        """
        base = " ".join(part for part in (self.target, self.intent) if part)
        if not label:
            return base
        return f"{base} - {label}" if base else label

    def calibration_prefix(self) -> str:
        """The ``<target> <intent>`` head of a calibration step's target name.

        Mirrors ``sequence_deepsky``'s light prefix. A single-pointing plan
        shares its bare plan token with its calibration, so carrying that
        pointing's label here too keeps lights and calibration in ONE
        ``$$TARGETNAME$$`` folder. A mosaic's ``-N`` pane tokens cannot be
        shared with plan-level calibration, so its label is dropped there
        (the intent is not). An empty ``pointings`` list (defensive) keeps
        just target and intent.
        """
        if len(self.pointings) == 1:
            return self.target_prefix(self.pointings[0].label)
        return self.target_prefix()
