"""Capability checks for the dark/bias light-cutting variants.

A ``variant`` says *how* a dark/bias step cuts the light off, and N.I.N.A.
only learns about a bad choice when the sequence is already running — a
``cover`` step on a rig with no flip-flat fails at 2 a.m., and a ``shutter``
step on a camera without one silently produces light-contaminated darks.
``validate_dark_variant`` moves that failure to load time, where it names the
variant, the reason, and the variants this rig can actually run.

Readability rules (the rig is allowed to be simple, not to be guessed):

* ``wheel`` is profile data — a configured filter wheel plus a ``Dark`` slot
  in the profile's filters — so it is always readable and is the default
  when a request omits ``variant``.
* ``cover`` needs the profile's flat device *and* an ``/equipment/info``
  payload that says the panel can open and close. Missing payload while the
  profile says one is configured is unread capability, not a maybe.
* ``shutter`` needs ``CameraInfo.HasShutter`` to be reported ``true``;
  ``false`` and "not reported" are both unsupported.

Every check fails closed: an unreadable capability rejects the load with a
message rather than quietly choosing a different way to take the dark.
"""

from ..models.observatory import ObservatoryEquipment
from ..models.profile import ObservatoryProfile

# The dark/bias vocabulary, in preference order: the wheel method first
# because it is the default, then the two ways to cut light without one.
DARK_VARIANTS = ("wheel", "cover", "shutter")

# CoverState values that mean "there is no usable cover here" — NotPresent
# is NINA's explicit "no such device", Error is a cover that will not move.
_UNUSABLE_COVER_STATES = ("NotPresent", "Error")


def dark_variant_reasons(
    equipment: ObservatoryEquipment, profile: ObservatoryProfile
) -> dict[str, str]:
    """Why each dark/bias variant cannot run on this rig.

    Returns ``{variant: reason}`` holding only the variants that would fail;
    an empty dict means all three are usable. Callers turn that into the
    "supported here: ..." list in the load-time error, so keeping this a
    plain reason map keeps the message and the check from drifting apart.
    """
    reasons: dict[str, str] = {}

    # wheel — the step parks the wheel on the profile's `Dark` slot, so the
    # wheel and that slot both have to exist. Both are profile data, read on
    # every load, so there is no unread case to guard against here.
    if not profile.equipment.has_filter_wheel:
        reasons["wheel"] = "the profile has no filter wheel configured"
    elif not any(f.name == "Dark" for f in profile.filters):
        available = ", ".join(f.name for f in profile.filters) or "none"
        reasons["wheel"] = (
            f"profile {profile.profile_name!r} has no 'Dark' filter slot "
            f"(filters: {available})"
        )

    # cover — a motorized cover (flip-flat) closed around the exposures. The
    # profile says whether one is configured; only /equipment/info says
    # whether it can open and close, so both must hold.
    if not profile.equipment.has_flat_device:
        reasons["cover"] = "the profile has no flat device configured"
    else:
        flat = equipment.flat_device
        if flat is None:
            reasons["cover"] = (
                "the profile configures a flat device but NINA reports none"
            )
        elif not flat.connected:
            reasons["cover"] = "the flat device is not connected"
        elif flat.supports_open_close is not True:
            reasons["cover"] = (
                "the flat device reports SupportsOpenClose=false — it cannot "
                "open and close to cut the light"
            )
        elif flat.cover_state in _UNUSABLE_COVER_STATES:
            reasons["cover"] = (
                f"the flat device reports CoverState={flat.cover_state!r}"
            )

    # shutter — the camera's own shutter, with the wheel left alone. A camera
    # NINA does not report, or one whose HasShutter it does not report, is
    # not a camera we may trust to cut the light.
    camera = equipment.camera
    if camera is None:
        reasons["shutter"] = "NINA reports no camera"
    elif camera.has_shutter is None:
        reasons["shutter"] = (
            f"NINA does not report HasShutter for camera {camera.name!r}"
        )
    elif not camera.has_shutter:
        reasons["shutter"] = (
            f"camera {camera.name!r} reports HasShutter=false — it cannot "
            "cut the light itself"
        )

    return reasons


def validate_dark_variant(
    variant: str, equipment: ObservatoryEquipment, profile: ObservatoryProfile
) -> None:
    """Raise ``ValueError`` unless ``variant`` can cut the light on this rig.

    The default (``wheel``) passes through the same check as an explicit
    one — a default that silently does something other than park the wheel
    would be worse than no default. On failure the message names the
    variant, the reason, and every variant this rig supports, so the caller
    can retry with one of those instead of guessing.
    """
    if variant not in DARK_VARIANTS:
        names = [repr(v) for v in DARK_VARIANTS]
        raise ValueError(
            f"Unsupported dark variant: {variant!r} "
            f"(use {', '.join(names[:-1])}, or {names[-1]})"
        )
    reasons = dark_variant_reasons(equipment, profile)
    if variant not in reasons:
        return
    supported = [v for v in DARK_VARIANTS if v not in reasons]
    usable = ", ".join(supported) if supported else "none"
    raise ValueError(
        f"dark/bias variant {variant!r} is not available on this rig: "
        f"{reasons[variant]}. Supported here: {usable}."
    )
