import argparse
import io
import json
import os
import shutil

from corpsman.cli import build_report
from corpsman.identity.linux import enumerate_devices
from corpsman.smart.parse import parse_smartctl_json, SmartData
from corpsman.topology.linux import system_devices

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
LUKS = os.path.join(FIX, "linux", "luks-lvm")
PLAIN_SATA = os.path.join(FIX, "linux", "plain-sata")


def smart(name):
    with open(os.path.join(FIX, "smartctl", name)) as f:
        return parse_smartctl_json(f.read())


def test_report_marks_system_device():
    devs = enumerate_devices(root=LUKS)
    sysmap = system_devices(root=LUKS)
    rep = build_report(devs, devs, sysmap, {d.name: smart("sata_healthy.json") for d in devs})
    sda = [d for d in rep["devices"] if d["name"] == "sda"][0]
    assert sda["system_state"] != []
    assert "/" in sda["system_state"]


def test_report_does_not_mark_unrelated_device():
    devs = enumerate_devices(root=LUKS)
    sysmap = system_devices(root=LUKS)
    rep = build_report(devs, devs, sysmap, {d.name: smart("sata_healthy.json") for d in devs})
    sdb = [d for d in rep["devices"] if d["name"] == "sdb"][0]
    assert sdb["system_state"] == []


def test_report_is_json_serialisable():
    devs = enumerate_devices(root=LUKS)
    rep = build_report(devs, devs, system_devices(root=LUKS),
                       {d.name: smart("sata_healthy.json") for d in devs})
    json.dumps(rep)


def test_report_uses_plain_enum_values_not_flavor():
    devs = enumerate_devices(root=LUKS)
    rep = build_report(devs, devs, system_devices(root=LUKS),
                       {d.name: smart("sata_pending_sectors.json") for d in devs})
    blob = json.dumps(rep)
    for flavor in ("expectant", "walking wounded", "CORPSMAN UP", "return to duty"):
        assert flavor not in blob


def test_unavailable_smart_reports_unknown_in_json():
    devs = enumerate_devices(root=LUKS)
    rep = build_report(devs, devs, system_devices(root=LUKS),
                       {d.name: SmartData(available=False, unreadable_reason="bridge")
                        for d in devs})
    assert all(d["health"] == "UNKNOWN" for d in rep["devices"])


def test_report_includes_identity_token_and_confirm_string():
    devs = enumerate_devices(root=LUKS)
    rep = build_report(devs, devs, system_devices(root=LUKS),
                       {d.name: smart("sata_healthy.json") for d in devs})
    for d in rep["devices"]:
        assert len(d["identity_token"]) == 12
        assert d["confirm"]


def test_schema_version_present():
    rep = build_report([], [], {}, {})
    assert rep["schema"] == 1


def test_filtering_by_device_does_not_hide_a_serial_collision():
    # Uniqueness is a property of the whole machine, not of the subset being
    # displayed. Judging it against a filtered list of one always finds no
    # duplicate -- confirm() must still be judged against every device on
    # the machine even when only one is shown.
    from corpsman.types import Device

    def dev(name, serial):
        return Device(
            path="/dev/" + name, name=name, instance_path="/dev/" + name,
            model="Model", serial=serial, wwn=None, size_bytes=1000,
            logical_sector=512, physical_sector=512, bus="sata",
            rotational=False, removable=False,
        )

    a = dev("sda", "DUPLICATE123")
    b = dev("sdb", "DUPLICATE123")
    all_devices = [a, b]
    shown = [a]

    rep = build_report(shown, all_devices, {}, {})
    reported_a = rep["devices"][0]

    assert reported_a["confirm"] == a.identity_token
    assert reported_a["confirm"] != "DUPLICATE123"


# --- Integration gaps beyond the brief -------------------------------------
#
# The brief's cmd_inspect calls topology_linux.system_devices() with no
# exception handling, and computes the exit code without specifying *how*
# it picks among multiple devices. Both are load-bearing for the fleet-check
# contract (see task-11-brief's "RULES") but neither has brief-level test
# coverage, so they are added here.

def _privileged_args(root, device=None, as_json=False):
    return argparse.Namespace(root=root, device=device, json=as_json)


def _run(cli, args):
    """Call cmd_inspect with an explicit stream and return (rc, output).

    cmd_inspect's default is `stream=sys.stdout`, a mutable default bound
    to the real stdout object at module-import time -- well before any
    per-test capsys redirection takes effect. Writes through that default
    go to the ORIGINAL stdout, not whatever object capsys swapped in, so
    `capsys.readouterr().out` silently reads back empty no matter what the
    function actually printed: every "X not in captured.out" assertion
    passes vacuously. Passing an explicit io.StringIO() sidesteps the
    default entirely and makes the assertion mean something.
    """
    stream = io.StringIO()
    rc = cli.cmd_inspect(args, stream=stream)
    return rc, stream.getvalue()


def test_topology_error_exits_three_and_does_not_print_a_report(tmp_path, monkeypatch):
    # A gate that cannot see the system must refuse, not emit a report with
    # every disk unflagged. Build a root with a real, populated sys/block
    # (so there IS a device to (wrongly) report on) but no proc/self/mountinfo
    # at all, so system_devices() cannot determine system state and raises.
    from corpsman import cli

    monkeypatch.setattr(cli.platform_, "has_backend", lambda: True)
    monkeypatch.setattr(cli.platform_, "is_privileged", lambda: True)

    root = str(tmp_path / "root")
    shutil.copytree(os.path.join(PLAIN_SATA, "sys"), os.path.join(root, "sys"))
    shutil.copytree(os.path.join(PLAIN_SATA, "dev"), os.path.join(root, "dev"))
    # Deliberately no proc/ tree at all.

    rc, out = _run(cli, _privileged_args(root))

    assert rc == 3
    # No device block -- not "sda" printed with every flag empty, not a
    # traceback either.
    assert "sda" not in out
    assert "Traceback" not in out


def test_unprivileged_refuses_rather_than_reporting_partial_data(monkeypatch):
    # Mutation (d) in the task-11 report found this path had zero coverage:
    # cmd_inspect must exit 3 and print nothing resembling a device report
    # when unprivileged, rather than quietly emitting partial data.
    from corpsman import cli

    monkeypatch.setattr(cli.platform_, "has_backend", lambda: True)
    monkeypatch.setattr(cli.platform_, "is_privileged", lambda: False)
    # If the privilege gate is ever bypassed, force a HEALTHY verdict so a
    # false pass can't happen for the unrelated reason that smartctl isn't
    # installed in the test environment -- an unbypassed-but-uninstalled
    # smartctl would ALSO exit 3 (via HEALTH_UNKNOWN), which would mask a
    # missing gate behind the right-looking exit code for the wrong reason.
    monkeypatch.setattr(
        cli, "collect",
        lambda device, probe, runner=None: smart("sata_healthy.json"),
    )

    rc, out = _run(cli, _privileged_args(LUKS))

    assert rc == 3
    assert "sda" not in out


def test_exit_code_is_the_worst_device_on_the_bus(monkeypatch):
    # sda is healthy (would exit 0 alone) and sda sorts first; sdb has
    # pending sectors (SCRAP, exit 2). The bus as a whole must still exit 2
    # -- an operator who only checks $? must not be told the bay is clean
    # because the first drive scanned happened to be fine.
    from corpsman import cli

    monkeypatch.setattr(cli.platform_, "has_backend", lambda: True)
    monkeypatch.setattr(cli.platform_, "is_privileged", lambda: True)

    canned = {
        "sda": smart("sata_healthy.json"),
        "sdb": smart("sata_pending_sectors.json"),
    }

    def fake_collect(device, probe, runner=None):
        return canned[device.name]

    monkeypatch.setattr(cli, "collect", fake_collect)

    rc, _ = _run(cli, _privileged_args(LUKS))

    assert rc == 2


def test_unknown_outranks_scrap_in_the_exit_code(monkeypatch):
    # An unreadable drive is an audit gap that may mask a failing
    # controller. It must not hide behind a worse-but-legible neighbour --
    # exit 3 (unknown), not 2 (scrap), when both are present on the bus.
    from corpsman import cli

    monkeypatch.setattr(cli.platform_, "has_backend", lambda: True)
    monkeypatch.setattr(cli.platform_, "is_privileged", lambda: True)

    canned = {
        "sda": smart("sata_pending_sectors.json"),
        "sdb": SmartData(available=False, unreadable_reason="bridge"),
    }

    def fake_collect(device, probe, runner=None):
        return canned[device.name]

    monkeypatch.setattr(cli, "collect", fake_collect)

    rc, _ = _run(cli, _privileged_args(LUKS))

    assert rc == 3


def test_no_devices_found_exits_three_not_zero(monkeypatch, tmp_path):
    # "Nothing was inspected" must never read as "everything is healthy".
    from corpsman import cli

    monkeypatch.setattr(cli.platform_, "has_backend", lambda: True)
    monkeypatch.setattr(cli.platform_, "is_privileged", lambda: True)

    # A root with no sys/block at all -> enumerate_devices() returns [].
    empty_root = str(tmp_path / "empty")
    os.makedirs(os.path.join(empty_root, "proc", "self"))
    with open(os.path.join(empty_root, "proc", "self", "mountinfo"), "w") as f:
        f.write("")

    rc, _ = _run(cli, _privileged_args(empty_root))

    assert rc == 3
    assert rc != 0


def test_unmatched_device_filter_exits_three(monkeypatch):
    from corpsman import cli

    monkeypatch.setattr(cli.platform_, "has_backend", lambda: True)
    monkeypatch.setattr(cli.platform_, "is_privileged", lambda: True)

    rc, _ = _run(cli, _privileged_args(LUKS, device="/dev/nonexistent"))

    assert rc == 3
    assert rc != 0
