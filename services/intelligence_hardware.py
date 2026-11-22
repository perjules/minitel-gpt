"""Optional ADS1115 intelligence wheel; no GPIO output, UART or app imports.

The ADS1115 responding at 0x48 proves the ADC is present, not the potentiometer.
TODO: reliable wiper-disconnection detection requires an explicit bias/test circuit.
"""
import logging
import subprocess
import threading
import time

from . import config_store

log = logging.getLogger(__name__)
LEVELS = (1, 3, 5, 7, 9, 11)
# Physically validated v1: equal-voltage bands, 30 mV hysteresis, 400 ms dwell.
MAX_VOLTAGE = 3.3
ADDRESS = "0x48"
HYSTERESIS_V = 0.03
DWELL_SECONDS = 0.4


def decode(word):
    value = int(word, 16)
    raw = ((value & 255) << 8) | (value >> 8)
    return raw - 65536 if raw & 0x8000 else raw


def band(volts):
    index = min(5, max(0, int(volts / MAX_VOLTAGE * 6)))
    return LEVELS[index]


def stable_index(volts, current):
    """Match the physically tested level_test.py Schmitt-style thresholds."""
    target = min(5, max(0, int(volts / MAX_VOLTAGE * 6)))
    if current is None or target == current:
        return target
    width = MAX_VOLTAGE / 6
    if target > current:
        return target if volts >= (current + 1) * width + HYSTERESIS_V else current
    return target if volts <= current * width - HYSTERESIS_V else current


def _i2c(args):
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=2).stdout.strip()


def sample():
    # Single-shot A0/GND, +/-4.096V, 128 samples/s; comparator disabled.
    _i2c(["i2cset", "-y", "1", ADDRESS, "0x01", "0xC3", "0x83", "i"])
    time.sleep(0.02)
    raw = decode(_i2c(["i2cget", "-y", "1", ADDRESS, "0x00", "w"]))
    volts = raw * 4.096 / 32768
    if not -0.05 <= volts <= 3.4:
        raise ValueError("ADC sample outside expected wheel voltage")
    return max(0, volts)


class Wheel:
    def __init__(self, controller):
        self.controller = controller
        self._lock = threading.Lock()
        self._present = False
        self._enabled = True
        self._stop = threading.Event()
        self._thread = None

    def active(self):
        with self._lock:
            return self._enabled and self._present

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="intelligence-wheel")
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def _loop(self):
        last = None
        candidate = None
        since = 0.0
        failures = 0
        while not self._stop.is_set():
            try:
                enabled = self.controller.wheel_enabled()
                with self._lock:
                    self._enabled = enabled
                if not enabled:
                    with self._lock:
                        self._present = False
                    last = candidate = None
                    failures = 0
                    self._stop.wait(.5)
                    continue
                volts = sample()
                target = stable_index(volts, LEVELS.index(last) if last is not None else None)
                level = LEVELS[target]
                failures = 0
                now = time.monotonic()
                if level != candidate:
                    candidate, since = level, now
                elif level != last and now - since >= DWELL_SECONDS:
                    # Set selection before advertising hardware authority.
                    self.controller.set_level(level)
                    last = level
                with self._lock:
                    self._present = True
            except (OSError, ValueError, subprocess.SubprocessError, config_store.StorageFailure) as exc:
                failures += 1
                if failures == 3:
                    log.warning("intelligence wheel unavailable: %s", type(exc).__name__)
                    with self._lock:
                        self._present = False
                    last = candidate = None
            self._stop.wait(.15)


wheel = None


def start(controller):
    global wheel
    if wheel is None:
        wheel = Wheel(controller)
        wheel.start()
    return wheel


def stop():
    global wheel
    if wheel is not None:
        wheel.stop()
        wheel = None


def active():
    return wheel is not None and wheel.active()
