"""Tests for corpsman.smart.collect.collect().

No existing test exercised the real collect() at all: the probe-absent
branch, the "disappeared between probe and run" branch, the USB `-d sat`
argv shape, and the USB-bridge-unreadable reason rewrite were all
unverified before this file.
"""
from corpsman.run import RunResult
from corpsman.smart.collect import collect
from corpsman.smart.parse import REASON_PARSE_ERROR
from corpsman.types import Device


def _device(bus="sata", path="/dev/sda"):
    return Device(
        path=path, name="sda", instance_path=path, model="Model",
        serial="SERIAL1", wwn=None, size_bytes=1000, logical_sector=512,
        physical_sector=512, bus=bus, rotational=False, removable=False,
    )


class _Probe(object):
    def __init__(self, present):
        self._present = present
        self.checked = []

    def has(self, name):
        self.checked.append(name)
        return name in self._present


def test_smartctl_absent_reports_unavailable_and_never_runs():
    calls = []

    def runner(argv, timeout=60):
        calls.append(argv)
        raise AssertionError("runner must not be called when smartctl is absent")

    data = collect(_device(), _Probe(present=set()), runner=runner)

    assert data.available is False
    assert "smartmontools" in data.unreadable_reason
    assert calls == []


def test_runner_not_found_reports_disappeared_between_probe_and_run():
    def runner(argv, timeout=60):
        return RunResult(rc=127, out="", err="not found", found=False)

    data = collect(_device(), _Probe(present={"smartctl"}), runner=runner)

    assert data.available is False
    assert "disappeared between probe and run" in data.unreadable_reason


def test_usb_device_argv_requests_sat_translation():
    seen = {}

    def runner(argv, timeout=60):
        seen["argv"] = argv
        return RunResult(rc=0, out="", err="", found=True)

    collect(_device(bus="usb"), _Probe(present={"smartctl"}), runner=runner)

    assert "-d" in seen["argv"]
    idx = seen["argv"].index("-d")
    assert seen["argv"][idx + 1] == "sat"


def test_non_usb_device_argv_has_no_sat_translation():
    seen = {}

    def runner(argv, timeout=60):
        seen["argv"] = argv
        return RunResult(rc=0, out="", err="", found=True)

    collect(_device(bus="sata"), _Probe(present={"smartctl"}), runner=runner)

    assert "-d" not in seen["argv"]
    assert "sat" not in seen["argv"]


def test_usb_bridge_unparseable_output_names_the_bridge_and_keeps_reason_kind():
    def runner(argv, timeout=60):
        return RunResult(rc=0, out="not valid json {{{", err="", found=True)

    data = collect(_device(bus="usb"), _Probe(present={"smartctl"}), runner=runner)

    assert data.available is False
    assert "bridge" in data.unreadable_reason.lower()
    # reason_kind comes straight from the parser and must not be
    # overwritten by the bus-specific message rewrite -- only
    # unreadable_reason is rewritten.
    assert data.reason_kind == REASON_PARSE_ERROR
