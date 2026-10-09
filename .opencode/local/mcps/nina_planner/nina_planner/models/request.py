from typing import Literal

from pydantic import BaseModel, Field

# Everything load_sequence understands: the five plan-backed frame sequences.
# Every action builds from a plan — there are no plan-less actions, and an
# empty request list is the stow (the composed sequence's End area parks and
# warms behind whatever steps ran, or on its own for an empty list).
Action = Literal[
    "light",
    "dark",
    "bias",
    "dawn_flat",
    "dusk_flat",
]

# Exposure group each action reads from the plan and reports progress under
# ("light" | "flat" | "dark" | "bias").
ACTION_GROUPS: dict[str, str] = {
    "light": "light",
    "dark": "dark",
    "bias": "bias",
    "dawn_flat": "flat",
    "dusk_flat": "flat",
}


class SequenceRequest(BaseModel):
    """One entry of the `request` list `load_sequence` takes: what to load and
    how."""

    plan: str | None = Field(
        default=None,
        description=(
            "Path to the observation-plan JSON file to load — required for "
            "every action."
        ),
    )
    action: Action = Field(
        default="light",
        description=(
            "What to load from the plan: light, dark, bias, dawn_flat, or dusk_flat."
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
