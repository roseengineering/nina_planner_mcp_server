import inspect
import unittest

from nina_planner.models.switch import ReadonlySwitchInfo, Switch, WritableSwitchInfo


class WritableSwitchInfoIsOnTest(unittest.TestCase):
    def test_missing_value_means_unknown(self):
        assert WritableSwitchInfo().is_on is None
        assert WritableSwitchInfo(value=None).is_on is None

    def test_is_on_not_in_constructor_signature(self):
        assert "is_on" not in inspect.signature(WritableSwitchInfo).parameters

    def test_stale_is_on_kwarg_is_ignored(self):
        # API never sends IsOn; if stale data does, it must be discarded,
        # not silently override the derivation from `value`.
        info = WritableSwitchInfo(value=0.0, is_on=True)  # type: ignore[call-arg]
        assert info.value == 0.0
        assert info.is_on is False

    def test_is_on_appears_in_dump(self):
        dumped = WritableSwitchInfo(value=1).model_dump()
        assert dumped["is_on"] is True
        assert dumped["value"] == 1


class WritableSwitchInfoIsOnBinaryTest(unittest.TestCase):
    # Strict binary: only value == 1 is on. Range fields are ignored.
    def test_value_one_is_on(self):
        assert WritableSwitchInfo(value=1).is_on is True

    def test_float_one_is_on(self):
        assert WritableSwitchInfo(value=1.0).is_on is True

    def test_value_zero_is_off(self):
        assert WritableSwitchInfo(value=0).is_on is False
        assert WritableSwitchInfo(value=0.0).is_on is False

    def test_fractional_value_is_off(self):
        assert WritableSwitchInfo(value=0.5).is_on is False

    def test_value_above_one_is_off(self):
        # A 0-10 dimmer at position 10 is off — only position 1 counts.
        info = WritableSwitchInfo(value=10.0, minimum=0.0, maximum=10.0)
        assert info.is_on is False

    def test_negative_value_is_off(self):
        assert WritableSwitchInfo(value=-1.0).is_on is False

    def test_signed_range_ignores_range(self):
        # A ±1 tri-state at +1 is on; same value 1 reads the same regardless of range.
        info = WritableSwitchInfo(value=1.0, minimum=-1.0, maximum=1.0)
        assert info.is_on is True

    def test_no_range_value_one_is_on(self):
        assert WritableSwitchInfo(value=1).is_on is True

    def test_no_range_value_zero_is_off(self):
        assert WritableSwitchInfo(value=0).is_on is False


class WritableSwitchInfoSchemaFieldsTest(unittest.TestCase):
    def test_schema_fields_round_trip(self):
        info = WritableSwitchInfo(
            id=1,
            name="Power",
            description="Mains relay",
            value=1.0,
            target_value=0.0,
            minimum=0.0,
            maximum=1.0,
            step_size=1.0,
        )
        assert info.id == 1
        assert info.name == "Power"
        assert info.description == "Mains relay"
        assert info.target_value == 0.0
        assert info.minimum == 0.0
        assert info.maximum == 1.0
        assert info.step_size == 1.0
        assert info.is_on is True


class ReadonlySwitchInfoTest(unittest.TestCase):
    def test_schema_fields_round_trip(self):
        info = ReadonlySwitchInfo(id=2, name="Temp", description="Sensor", value=23.5)
        assert info.id == 2
        assert info.name == "Temp"
        assert info.description == "Sensor"
        assert info.value == 23.5


class SwitchTest(unittest.TestCase):
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
