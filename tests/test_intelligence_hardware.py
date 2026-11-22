"""Offline wheel authority and ADS1115 conversion tests (no I2C device required)."""
import pytest

from services import intelligence, intelligence_hardware


@pytest.fixture
def dial(tmp_path, monkeypatch):
    controller = intelligence.Controller(tmp_path)
    monkeypatch.setattr(intelligence, "controller", controller)
    monkeypatch.setattr(intelligence_hardware, "wheel", None)
    return controller


def test_wheel_opt_out_persists_and_defaults_enabled(dial):
    assert dial.wheel_enabled() is True
    dial.set_wheel_enabled(False)
    assert intelligence.Controller(dial.profiles_path.parent).wheel_enabled() is False
    dial.set_wheel_enabled(True)
    assert dial.wheel_enabled() is True


def test_command_authority_and_status(dial, monkeypatch):
    assert "09" in intelligence.command("I 9")
    monkeypatch.setattr(intelligence_hardware, "active", lambda: True)
    assert "molette" in intelligence.command("I 3")
    assert dial.level == 9
    assert "09" in intelligence.command("I")
    monkeypatch.setattr(intelligence_hardware, "active", lambda: False)
    assert "03" in intelligence.command("I 3")


@pytest.mark.parametrize("word,raw", [("0x0000", 0), ("0x1067", 26384), ("0xffff", -1)])
def test_byte_order(word, raw):
    assert intelligence_hardware.decode(word) == raw


@pytest.mark.parametrize("volts,level", [
    (0.0, 1), (0.54, 1), (0.56, 3), (1.10, 5),
    (1.66, 7), (2.21, 9), (2.80, 11), (3.3, 11),
])
def test_provisional_voltage_bands(volts, level):
    assert intelligence_hardware.band(volts) == level


def test_invalid_admin_setting(dial):
    with pytest.raises(intelligence.config_store.InvalidConfig):
        dial.set_wheel_enabled("yes")
    path = dial.profiles_path.with_name("intelligence-wheel.json")
    path.write_text('{"version":1,"enabled":"yes"}')
    with pytest.raises(intelligence.config_store.InvalidConfig):
        dial.wheel_enabled()


def test_adc_sample_commands(monkeypatch):
    calls = []
    def fake_run(args, **kwargs):
        calls.append(args)
        class Result:
            stdout = "0x1067"
        return Result()
    monkeypatch.setattr(intelligence_hardware.subprocess, "run", fake_run)
    monkeypatch.setattr(intelligence_hardware.time, "sleep", lambda _: None)
    assert intelligence_hardware.sample() == pytest.approx(26384 * 4.096 / 32768)
    assert calls[0][:3] == ["i2cset", "-y", "1"]
    assert calls[1][:3] == ["i2cget", "-y", "1"]


def test_hysteresis_holds_near_each_boundary():
    for index in range(5):
        boundary = (index + 1) * intelligence_hardware.MAX_VOLTAGE / 6
        assert intelligence_hardware.stable_index(boundary + .02, index) == index
        assert intelligence_hardware.stable_index(boundary + .04, index) == index + 1
        assert intelligence_hardware.stable_index(boundary - .02, index + 1) == index + 1
        assert intelligence_hardware.stable_index(boundary - .04, index + 1) == index


def test_hysteresis_allows_skipping_levels():
    assert intelligence_hardware.stable_index(3.1, 1) == 5
    assert intelligence_hardware.stable_index(.2, 5) == 0
