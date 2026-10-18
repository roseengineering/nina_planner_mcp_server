"""Whole-sequence builders for tests.

Production assembles every sequence through ``build_sequence_many``; these wrap
a single step's payload the same way so tests exercise that real path.
"""

from typing import Any

from nina_planner.models.observatory import ObservatoryEquipment
from nina_planner.models.plan import ObservationPlan
from nina_planner.models.profile import ObservatoryProfile
from nina_planner.sequence import (
    build_sequence_many,
    target_payload_darks,
    target_payload_flats,
    target_payload_lights,
)


def build_sequence_stow(equipment: ObservatoryEquipment) -> dict[str, Any]:
    return build_sequence_many(equipment, [])


def build_sequence_lights(
    plan: ObservationPlan,
    equipment: ObservatoryEquipment,
    profile: ObservatoryProfile,
    *,
    pointing_index: int = 1,
) -> dict[str, Any]:
    payload = target_payload_lights(
        plan, equipment, profile, pointing_index=pointing_index
    )
    return build_sequence_many(equipment, [payload])


def build_sequence_darks(
    plan: ObservationPlan,
    equipment: ObservatoryEquipment,
    profile: ObservatoryProfile,
    bias: bool = False,
    variant: str = "wheel",
) -> dict[str, Any]:
    payload = target_payload_darks(plan, equipment, profile, bias, variant)
    return build_sequence_many(equipment, [payload])


def build_sequence_flats(
    plan: ObservationPlan,
    equipment: ObservatoryEquipment,
    profile: ObservatoryProfile,
    dusk: bool = False,
) -> dict[str, Any]:
    payload = target_payload_flats(plan, equipment, profile, dusk=dusk)
    return build_sequence_many(equipment, [payload])
