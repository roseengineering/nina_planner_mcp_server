import hashlib
import json
import re

from pydantic import Field, field_validator, model_validator

from .pointing import Pointing
from .strict import StrictModel

# A plan id is stamped into the target name, which N.I.N.A. rewrites on its way
# into the file path — it turned "[" and "]" into "_" even though NTFS allows
# them — so an id only works if it survives that rewrite verbatim. This is the
# deliberately conservative charset proven to survive: every recorded target
# directory carries its "plan-<hex>" id unchanged. "_" is refused outright
# because it is the attribution-token delimiter (an id containing it could
# substring-match inside a longer id's token); everything else outside this set
# is refused because we cannot prove N.I.N.A. leaves it alone.
_PLAN_ID_ALLOWED = re.compile(r"[A-Za-z0-9.-]+")


class LightExposurePlan(StrictModel):
    filter_name: str
    exposure_time_seconds: float = Field(gt=0)
    total_count: int = Field(ge=1)


class FlatExposurePlan(StrictModel):
    # No exposure_time_seconds: N.I.N.A.'s SkyFlat item picks the exposure
    # itself to hit a target signal level, so a plan-specified duration is
    # never sent. A plan that still carries one is refused at load rather
    # than silently reinterpreted — see StrictModel.
    #
    # List order is shoot order: dawn flats shoot flat_frames in list order
    # as written, while dusk flats shoot it reversed. The convention is to
    # list broadband filters first and narrowband last (e.g. L, R, G, B, Ha)
    # so narrowband always shoots while twilight is brighter.
    filter_name: str
    total_count: int = Field(ge=1)


class DarkExposurePlan(StrictModel):
    exposure_time_seconds: float = Field(gt=0)
    total_count: int = Field(ge=1)


class BiasExposurePlan(StrictModel):
    total_count: int = Field(ge=1)


class AutofocusConfig(StrictModel):
    reference_filter_name: str = Field(default="L")
    hfr_increase_sample_size: int = Field(default=3, ge=1)
    hfr_increase_threshold_percent: float = Field(default=15.0, gt=0)
    every_n_exposures: int = Field(default=10, ge=1)
    threshold_celsius: float = Field(default=1.0, gt=0)


class GuidingConfig(StrictModel):
    dither_every_n_exposures: int = Field(default=2, ge=1)
    check_drift_every_n_exposures: int = Field(default=6, ge=1)
    max_drift_arcmin: float = Field(default=1.5, gt=0)


class CoolerConfig(StrictModel):
    on: bool = True
    setpoint_celsius: float = Field(
        default=-10.0,
        ge=-40.0,
        le=30.0,
        description="Target setpoint temperature in degrees Celsius.",
    )


class ConstraintsConfig(StrictModel):
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

    These are the ONLY place quality thresholds live: ``load_sequence`` and
    ``get_plan_progress`` read them from the plan file, so every tool that
    counts this plan's frames applies the same rules without a caller having
    to repeat them. There is deliberately no per-call override — one value,
    one source of truth, and the two tools can never disagree about what
    counts.

    All four default to ``None`` (no filtering): a plan without a ``quality``
    section behaves exactly as one written before this field existed, and a
    threshold is only ever active because someone set it deliberately.

    Lights only. Calibration frames (flat/dark/bias) have no star quality and
    are matched by filter, exposure and the calibration window alone, so
    nothing here applies to them.

    Fail-closed when active: a light frame missing the relevant field (or
    carrying NINA's ``0`` for an unmeasured value) does NOT count toward the
    plan's ``total_count`` — otherwise a plan could be marked complete on
    frames nobody measured.
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

    The caller states WHAT to cover (``center`` + ``coverage`` in arcmins)
    and how much panes may overlap; the tool derives HOW (cols x rows) from
    the rig's pane field of view at write time and stamps the decision back
    into ``cols``/``rows``/``pane_fov_arcmins`` as a record of what was
    actually planned. Those three are therefore ``None`` on input and never
    drive expansion — an expanded plan's ``pointings`` are the authority.

    ``coverage`` is measured in the grid frame BEFORE ``pa_deg`` rotation:
    it is the footprint the pane rectangle must span on each axis, and the
    grid is sized per axis with ``ceil`` so it always covers at least this
    much (over-covering by up to one pane rather than leaving a gap).
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
    target: str = Field(default="", description="catalog designation")
    intent: str = Field(
        default="", description="2 to 3 words describing the primary observation goal"
    )
    plan_id: str = Field(
        default="",
        description="stable identifier for this acquisition goal; stamped on write, "
        "derived from plan content if absent. Empty, or ASCII letters, digits, "
        "'.' and '-' only: '_' is the frame-attribution token delimiter, and "
        "N.I.N.A. rewrites other characters when it puts $$TARGETNAME$$ into "
        "the file path, so an id outside that charset could never be matched "
        "back to the frames it stamped.",
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
    # Light-frame quality thresholds, read by load_sequence and
    # get_plan_progress only — see QualityConfig. Default is "no filtering".
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
        """A plan carries pointings, or a mosaic to derive them from — not neither.

        Empty ``pointings`` with no ``mosaic`` would be a plan that points
        nowhere, so it is refused here rather than surfacing later as a
        sequence with nothing to slew to. The reverse (both present) is
        legal and means the pointings are already expanded: they are the
        authority, the mosaic block is provenance, and nothing re-derives.
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

        Emitted as ``_{plan_id}_`` in the target name and read back out of the
        recorded path, so the id must survive N.I.N.A.'s path sanitisation
        unchanged *and* must not contain the delimiter itself. Empty stays
        legal: it means "derive one on write" (the derived id is always
        ``plan-<hex>``, which passes).
        """
        if v and not _PLAN_ID_ALLOWED.fullmatch(v):
            raise ValueError(
                f"plan_id {v!r} must be empty (a content-derived id is stamped "
                "instead) or consist only of ASCII letters, digits, '.' and "
                "'-'. '_' is the plan-id token delimiter, so an id containing "
                "it could substring-match inside a longer id's token. Every "
                "other character is refused because N.I.N.A. rewrites the "
                "target name on its way into the file path — it turned '[' "
                "and ']' into '_' although NTFS allows them — and an id that "
                "does not survive that rewrite verbatim can never be matched "
                "back to its frames."
            )
        return v

    def _base_plan_id(self) -> str:
        if self.plan_id:
            return self.plan_id
        data = self.model_dump(exclude={"plan_id"})
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return "plan-" + hashlib.sha1(canonical.encode()).hexdigest()[:12]

    def per_pointing_plan_id(self, pointing_index: int) -> str:
        """The per-pointing id form ``{base}-{index}``, historically used for
        every pointing, including single-pointing plans."""
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

        Underscore-delimited: N.I.N.A. rewrites ``[`` and ``]`` to ``_``
        when it substitutes ``$$TARGETNAME$$`` into the file path, so a
        bracketed token never survives into the recorded path and the
        frames stop attributing. Emitting ``_plan-x_`` writes exactly what
        N.I.N.A. would have produced anyway.

        The closing underscore keeps tokens from substring-matching each
        other: ``_plan-x_`` never appears inside ``_plan-x-1_``, nor
        ``_plan-x-1_`` inside ``_plan-x-10_`` — as long as the id itself
        contains no underscore (ids never do when derived; see
        ``_base_plan_id``). Attribution also accepts the legacy bracketed
        and parenthesised forms (frames shot under the earlier conventions,
        or recorded before N.I.N.A. rewrote the brackets) — see
        ``progress._light_target_matches``.
        """
        return f"_{self.effective_plan_id(pointing_index)}_"

    def plan_token(self) -> str:
        """Plan-level target-name token, used for calibration (darks/bias/
        flats): calibration belongs to the whole plan, not to any pane."""
        return f"_{self._base_plan_id()}_"

    def target_prefix(self, label: str | None = "") -> str:
        """The readable head of a target name: ``<target> <intent> [- label]``.

        ``$$TARGETNAME$$`` turns this (plus the plan-id token) into the
        imaging directory, so this is what a folder reads as. The intent
        rides along because the target alone rarely says *why* — "M31" is a
        catalog designation, not an observation. An empty target or intent
        is skipped rather than leaving a stray separator, and an empty
        ``label`` appends nothing, so the pre-intent shapes ("M31",
        "M31 - core") are reproduced verbatim for plans that leave intent
        unset.

        Both lights (``sequence_deepsky``) and calibration
        (``calibration_prefix``) build their prefix here on purpose: two
        spellings of the same prefix would split one plan's frames across
        two ``$$TARGETNAME$$`` folders.
        """
        base = " ".join(part for part in (self.target, self.intent) if part)
        if not label:
            return base
        return f"{base} - {label}" if base else label

    def calibration_prefix(self) -> str:
        """The ``<target> <intent>`` part of a calibration step's target name.

        Mirrors the prefix ``sequence_deepsky`` builds for lights, but only
        where the two can actually agree: a single-pointing plan shares its
        bare plan token with its calibration (see ``effective_plan_id``), so
        carrying that pointing's label here too keeps lights and calibration
        in ONE ``$$TARGETNAME$$`` folder instead of splitting them into
        ``<target> <intent> - <label> _plan-x_`` (lights) and
        ``<target> <intent> _plan-x_`` (calibration).

        A mosaic's panes use ``-N`` tokens that no plan-level calibration can
        share, so its label would only make the folder read as one pane's —
        the label is dropped there (the intent is not: it is plan-level, so
        the mosaic's calibration folder still reads as this plan's). An empty
        ``pointings`` list (defensive; the model forbids it) likewise keeps
        just the target and intent.
        """
        if len(self.pointings) == 1:
            return self.target_prefix(self.pointings[0].label)
        return self.target_prefix()
