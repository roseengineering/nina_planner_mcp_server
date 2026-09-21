import hashlib
import json

from pydantic import BaseModel, Field


class LightExposurePlan(BaseModel):
    filter_name: str
    exposure_time_seconds: float = Field(gt=0)
    total_count: int = Field(ge=1)


class FlatExposurePlan(BaseModel):
    filter_name: str
    exposure_time_seconds: float = Field(gt=0)
    total_count: int = Field(ge=1)


class DarkExposurePlan(BaseModel):
    exposure_time_seconds: float = Field(gt=0)
    total_count: int = Field(ge=1)


class BiasExposurePlan(BaseModel):
    total_count: int = Field(ge=1)


class AutofocusConfig(BaseModel):
    reference_filter_name: str = Field(default="L")
    hfr_increase_sample_size: int = Field(default=3, ge=1)
    hfr_increase_threshold_percent: float = Field(default=15.0, gt=0)
    every_n_exposures: int = Field(default=10, ge=1)
    threshold_celsius: float = Field(default=1.0, gt=0)


class GuidingConfig(BaseModel):
    dither_every_n_exposures: int = Field(default=2, ge=1)
    check_drift_every_n_exposures: int = Field(default=6, ge=1)
    max_drift_arcmin: float = Field(default=1.5, gt=0)


class CoolerConfig(BaseModel):
    on: bool = True
    setpoint_celsius: float = Field(
        default=-10.0,
        ge=-40.0,
        le=30.0,
        description="Target setpoint temperature in degrees Celsius.",
    )


class ConstraintsConfig(BaseModel):
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


class ObservationPlan(BaseModel):
    target: str = Field(default="", description="catalog designation")
    intent: str = Field(
        default="", description="2 to 3 words describing the primary observation goal"
    )
    plan_id: str = Field(
        default="",
        description="stable identifier for this acquisition goal; stamped on write, "
        "derived from plan content if absent",
    )
    description: str = Field(
        default="",
        description="explanation of the observation plan, including rationale, exposure goals, equipment, or sky constraints",
    )
    ra_hours: float = Field(
        ge=0.0, lt=24.0, description="Right Ascension J2000 in hours [0.0, 24.0)"
    )
    dec_deg: float = Field(
        ge=-90.0, le=90.0, description="Declination J2000 in degrees [-90.0, +90.0]"
    )
    batch_size: int = Field(
        default=5, ge=0, description="Exposures per batch. 0 means unbatch."
    )

    # hardware
    cooler: CoolerConfig = Field(default_factory=CoolerConfig)
    constraints: ConstraintsConfig = Field(default_factory=ConstraintsConfig)
    autofocus: AutofocusConfig = Field(default_factory=AutofocusConfig)
    guiding: GuidingConfig = Field(default_factory=GuidingConfig)

    # exposures
    light: list[LightExposurePlan] = Field(min_length=1)
    flat: list[FlatExposurePlan] = Field(min_length=1)
    dark: list[DarkExposurePlan] = Field(min_length=1)
    bias: list[BiasExposurePlan] = Field(min_length=1)

    def effective_plan_id(self) -> str:
        if self.plan_id:
            return self.plan_id
        data = self.model_dump(exclude={"plan_id"})
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return "plan-" + hashlib.sha1(canonical.encode()).hexdigest()[:12]
