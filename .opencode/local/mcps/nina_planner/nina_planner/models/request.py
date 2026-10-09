from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

# Everything load_sequence understands: the five plan-backed frame sequences
# plus the four teardown-style, plan-less ones.
Action = Literal[
    "light",
    "dark",
    "bias",
    "dawn_flat",
    "dusk_flat",
    "stow",
    "park",
    "warm",
    "unpark",
]

# The fields that only mean something for a plan-backed action.
PLAN_FIELDS = frozenset(
    {
        "plan",
        "pointing_index",
        "mode",
        "max_hfr",
        "min_detected_stars",
        "max_guiding_rms_arcsec",
    }
)


@dataclass(frozen=True)
class ActionSpec:
    """What one `action` value means to `load_sequence`."""

    requires_plan: bool
    uses: frozenset[str]
    # Exposure group the action reads from the plan and reports progress under
    # ("light" | "flat" | "dark" | "bias"); None for plan-less actions.
    group: str | None = None


ACTIONS: dict[str, ActionSpec] = {
    "light": ActionSpec(requires_plan=True, uses=PLAN_FIELDS, group="light"),
    "dark": ActionSpec(requires_plan=True, uses=PLAN_FIELDS, group="dark"),
    "bias": ActionSpec(requires_plan=True, uses=PLAN_FIELDS, group="bias"),
    "dawn_flat": ActionSpec(requires_plan=True, uses=PLAN_FIELDS, group="flat"),
    "dusk_flat": ActionSpec(requires_plan=True, uses=PLAN_FIELDS, group="flat"),
    # Teardown-style: driven by live equipment, no plan and no pointing/mode/
    # quality thresholds. Anything else sent with them is a caller mistake.
    "stow": ActionSpec(requires_plan=False, uses=frozenset()),
    "park": ActionSpec(requires_plan=False, uses=frozenset()),
    "warm": ActionSpec(requires_plan=False, uses=frozenset()),
    # unpark is a *list* step only: on its own it would finish with the mount
    # unparked and nothing running (see load_sequence).
    "unpark": ActionSpec(requires_plan=False, uses=frozenset()),
}


class SequenceRequest(BaseModel):
    """The single object `load_sequence` takes: what to load and how."""

    plan: str | None = Field(
        default=None,
        description=(
            "Path to the observation-plan JSON file to load. Required for the "
            "frame actions (light, dark, bias, dawn_flat, dusk_flat). Leave it "
            "out for stow, park, warm, and unpark, which are driven by live "
            "equipment state instead — sending it with those is rejected as "
            "ignored."
        ),
    )
    action: Action = Field(
        default="light",
        description=(
            "What to load: light, dark, bias, dawn_flat, or dusk_flat build "
            "that sequence from `plan`; stow parks (or homes) the mount and "
            "warms the camera, park parks (or homes) the mount only, warm "
            "warms the camera only, unpark releases a parked mount. "
            "stow/park/warm/unpark need no plan and ignore pointing_index, "
            "mode, and the quality thresholds. Inside a `request` list these "
            "four become ordered steps instead of whole sequences: park, warm, "
            "and unpark may appear anywhere, while stow is rejected (the "
            "composed sequence's End area already parks and warms) and unpark "
            "is rejected as a lone single request (it would end with the "
            "mount unparked and nothing running)."
        ),
    )
    pointing_index: int = Field(
        default=1,
        ge=1,
        description=(
            "1-based pointing of the plan to load (default 1). Lights embed "
            "`({base_plan_id}-{pointing_index})` in the target name so each "
            "pointing's frames are attributed independently."
        ),
    )
    mode: str = Field(
        default="remaining",
        description=(
            "'remaining' (default) acquires only frames not yet attributed to "
            "this plan/pointing; 'full' acquires the whole plan again."
        ),
    )
    max_hfr: float | None = Field(
        default=None,
        description=(
            "Exclude light frames with HFR above this value from the acquired "
            "count in 'remaining' mode."
        ),
    )
    min_detected_stars: int | None = Field(
        default=None,
        description=(
            "Exclude light frames detected with fewer stars than this from the "
            "acquired count in 'remaining' mode."
        ),
    )
    max_guiding_rms_arcsec: float | None = Field(
        default=None,
        description=(
            "Exclude light frames guided above this RMS (arcsec) from the "
            "acquired count in 'remaining' mode."
        ),
    )


def ignored_fields(request: SequenceRequest, spec: ActionSpec) -> list[str]:
    """Request fields carrying a non-default value that `spec` does not use.

    Defaults are never reported: a client that echoes the schema's own
    defaults back (`mode="remaining"`, `pointing_index=1`) is not making a
    mistake, only a genuinely meaningful value is.
    """
    defaults = SequenceRequest()
    return sorted(
        name
        for name, value in request.model_dump().items()
        if name != "action"
        and name not in spec.uses
        and value != getattr(defaults, name)
    )
