"""Switch and WritableSwitchInfo model behaviour."""

import inspect
import unittest

from nina_planner.models.switch import Switch, WritableSwitchInfo


class WritableSwitchInfoIsOnTest(unittest.TestCase):
    """How is_on is derived and exposed on WritableSwitchInfo."""

    def test_missing_value_means_unknown(self):
        """A missing or null value leaves is_on unknown."""
        assert WritableSwitchInfo().is_on is None
        assert WritableSwitchInfo(value=None).is_on is None

    def test_is_on_not_in_constructor_signature(self):
        """is_on is derived, not a constructor parameter."""
        assert "is_on" not in inspect.signature(WritableSwitchInfo).parameters

    def test_stale_is_on_kwarg_is_ignored(self):
        """A stale is_on kwarg is discarded, not overriding value."""
        # API never sends IsOn; if stale data does, it must be discarded,
        # not silently override the derivation from `value`.
        info = WritableSwitchInfo(value=0.0, is_on=True)  # type: ignore[call-arg]
        assert info.value == 0.0
        assert info.is_on is False

    def test_is_on_appears_in_dump(self):
        """model_dump exposes the derived is_on alongside value."""
        dumped = WritableSwitchInfo(value=1).model_dump()
        assert dumped["is_on"] is True
        assert dumped["value"] == 1


class WritableSwitchInfoIsOnBinaryTest(unittest.TestCase):
    """is_on across integer, fractional, and ranged switch values."""

    # Strict binary: only value == 1 is on. Range fields are ignored.
    def test_value_one_is_on(self):
        """A value of 1 reads as on."""
        assert WritableSwitchInfo(value=1).is_on is True

    def test_float_one_is_on(self):
        """A value of 1.0 reads as on."""
        assert WritableSwitchInfo(value=1.0).is_on is True

    def test_value_zero_is_off(self):
        """A value of 0, as int or float, reads as off."""
        assert WritableSwitchInfo(value=0).is_on is False
        assert WritableSwitchInfo(value=0.0).is_on is False

    def test_fractional_value_is_off(self):
        """A fractional value such as 0.5 reads as off."""
        assert WritableSwitchInfo(value=0.5).is_on is False

    def test_value_above_one_is_off(self):
        """A 0-10 dimmer at its maximum reads as off, not on."""
        # A 0-10 dimmer at position 10 is off — only position 1 counts.
        info = WritableSwitchInfo(value=10.0, minimum=0.0, maximum=10.0)
        assert info.is_on is False

    def test_negative_value_is_off(self):
        """A negative value reads as off."""
        assert WritableSwitchInfo(value=-1.0).is_on is False

    def test_signed_range_ignores_range(self):
        """Value 1 reads on regardless of the surrounding range."""
        # A ±1 tri-state at +1 is on; same value 1 reads the same regardless of range.
        info = WritableSwitchInfo(value=1.0, minimum=-1.0, maximum=1.0)
        assert info.is_on is True


class SwitchTest(unittest.TestCase):
    """Switch schema fields surviving a round trip through the model."""

    def _build(self, **overrides) -> Switch:
        defaults = dict(
            connected=True,
            name="PWR",
            writable_switches=[],
            readonly_switches=[],
        )
        defaults.update(overrides)
        return Switch(**defaults)

    def test_schema_fields_round_trip(self):
        """Display, driver, device id and action fields round-trip."""
        switch = self._build(
            description="Power controller",
            display_name="Power",
            driver_info="ASCOM Switch",
            driver_version="1.0",
            device_id="ASCOM.Simulator.Switch",
            supported_actions=["reset"],
        )
        assert switch.display_name == "Power"
        assert switch.driver_info == "ASCOM Switch"
        assert switch.driver_version == "1.0"
        assert switch.device_id == "ASCOM.Simulator.Switch"
        assert switch.supported_actions == ["reset"]


class SwitchMissingListsTest(unittest.TestCase):
    """A payload without switch lists must not fail the equipment-status call."""

    def test_missing_lists_default_to_empty(self):
        """A payload without switch lists yields empty lists, not errors."""
        switch = Switch(connected=True, name="PWR")
        assert switch.writable_switches == []
        assert switch.readonly_switches == []

    def test_switch_lists_are_not_shared_between_instances(self):
        """Each Switch instance gets its own list objects."""
        first = Switch(connected=True, name="A")
        second = Switch(connected=True, name="B")
        assert first.writable_switches is not second.writable_switches
