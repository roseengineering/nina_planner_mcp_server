import hashlib
import json
import re

from pydantic import Field, field_validator

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


# --- Root Plan ---


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
        min_length=1,
        description=(
            "One or more target pointings (RA/Dec/PA, optional label) to image. "
            "Each pointing of a plan is loaded separately by pointing_index."
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
            "variant='dawn' (morning) or variant='dusk' (evening)."
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
