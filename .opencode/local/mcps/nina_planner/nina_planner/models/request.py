from typing import Literal

from pydantic import BaseModel, Field, model_validator

# Everything load_sequence understands: the four plan-backed frame sequences.
# Every action builds from a plan — there are no plan-less actions, and an
# empty request list is the stow (the composed sequence's End area parks and
# warms behind whatever steps ran, or on its own for an empty list).
Action = Literal[
    "light",
    "dark",
    "bias",
    "flat",
]

# HOW an action runs, kept out of the action enum so the two axes don't
# multiply into values (dawn_flat, dusk_flat, cover_dark, ...): an action
# says which of the plan's frames to shoot, its variant says the recipe —
# when for flats, how the light is cut off for darks/bias. The vocabulary is
# action-scoped: an empty set means the action has no variants at all.
VARIANTS: dict[str, frozenset[str]] = {
    "light": frozenset(),
    "dark": frozenset({"wheel", "cover", "shutter"}),
    "bias": frozenset({"wheel", "cover", "shutter"}),
    "flat": frozenset({"dawn", "dusk"}),
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
        description=("What to load from the plan: light, dark, bias, or flat."),
    )
    variant: str | None = Field(
        default=None,
        description=(
            "How to run `action` — action-scoped, validated against it: "
            "`flat` requires `dawn` (morning twilight) or `dusk` (evening "
            "twilight); `dark` and `bias` take `wheel` (default — park the "
            "wheel on the profile's `Dark` filter slot), `cover` (close "
            "the motorized cover, e.g. a flip-flat, around the exposures "
            "and reopen it after), or `shutter` (expose without moving the "
            "filter wheel, relying on the camera's shutter); `light` takes none."
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
            "Exclude light frames detected with fewer stars than this from "
            "the acquired count in 'remaining' mode."
        ),
    )
    max_guiding_rms_arcsec: float | None = Field(
        default=None,
        description=(
            "Exclude light frames guided above this RMS from the acquired "
            "count in 'remaining' mode."
        ),
    )

    @model_validator(mode="after")
    def _check_variant(self) -> "SequenceRequest":
        """Enforce the action-scoped variant vocabulary.

        `flat` has no sensible default (dawn or dusk is a real choice), so
        its variant is required; `dark`/`bias` fall back to the wheel method
        when omitted; and a variant that belongs to another action is an
        error rather than something silently ignored. load_sequence prefixes
        any failure with the entry's 0-based position.
        """
        allowed = VARIANTS[self.action]
        if self.variant is None:
            if self.action == "flat":
                raise ValueError(
                    "action='flat' requires a variant: 'dawn' (morning "
                    "twilight) or 'dusk' (evening twilight)."
                )
            return self
        if self.variant not in allowed:
            raise ValueError(
                f"variant={self.variant!r} does not apply to "
                f"action={self.action!r}; allowed: "
                f"{', '.join(sorted(allowed)) or 'none'}"
            )
        return self
