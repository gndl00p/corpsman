import argparse
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
    rep = build_report(devs, sysmap, {d.name: smart("sata_healthy.json") for d in devs})
    sda = [d for d in rep["devices"] if d["name"] == "sda"][0]
    assert sda["system_state"] != []
    assert "/" in sda["system_state"]


def test_report_does_not_mark_unrelated_device():
    devs = enumerate_devices(root=LUKS)
    sysmap = system_devices(root=LUKS)
    rep = build_report(devs, sysmap, {d.name: smart("sata_healthy.json") for d in devs})
    sdb = [d for d in rep["devices"] if d["name"] == "sdb"][0]
    assert sdb["system_state"] == []


def test_report_is_json_serialisable():
    devs = enumerate_devices(root=LUKS)
    rep = build_report(devs, system_devices(root=LUKS),
                       {d.name: smart("sata_healthy.json") for d in devs})
    json.dumps(rep)


def test_report_uses_plain_enum_values_not_flavor():
    devs = enumerate_devices(root=LUKS)
    rep = build_report(devs, system_devices(root=LUKS),
                       {d.name: smart("sata_pending_sectors.json") for d in devs})
    blob = json.dumps(rep)
    for flavor in ("expectant", "walking wounded", "CORPSMAN UP", "return to duty"):
        assert flavor not in blob


def test_unavailable_smart_reports_unknown_in_json():
    devs = enumerate_devices(root=LUKS)
    rep = build_report(devs, system_devices(root=LUKS),
                       {d.name: SmartData(available=False, unreadable_reason="bridge")
                        for d in devs})
    assert all(d["health"] == "UNKNOWN" for d in rep["devices"])


def test_report_includes_identity_token_and_confirm_string():
    devs = enumerate_devices(root=LUKS)
    rep = build_report(devs, system_devices(root=LUKS),
                       {d.name: smart("sata_healthy.json") for d in devs})
    for d in rep["devices"]:
        assert len(d["identity_token"]) == 12
        assert d["confirm"]


def test_schema_version_present():
    rep = build_report([], {}, {})
    assert rep["schema"] == 1


# --- Integration gaps beyond the brief -------------------------------------
#
# The brief's cmd_inspect calls topology_linux.system_devices() with no
# exception handling, and computes the exit code without specifying *how*
# it picks among multiple devices. Both are load-bearing for the fleet-check
# contract (see task-11-brief's "RULES") but neither has brief-level test
# coverage, so they are added here.

def _privileged_args(root, device=None, as_json=False):
    return argparse.Namespace(root=root, device=device, json=as_json)


def test_topology_error_exits_three_and_does_not_print_a_report(tmp_path, monkeypatch, capsys):
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

    rc = cli.cmd_inspect(_privileged_args(root))

    assert rc == 3
    captured = capsys.readouterr()
    # No device block -- not "sda" printed with every flag empty, not a
    # traceback either.
    assert "sda" not in captured.out
    assert "Traceback" not in captured.out


def test_unprivileged_refuses_rather_than_reporting_partial_data(monkeypatch, capsys):
    # Mutation (d) in the task-11 report found this path had zero coverage:
    # cmd_inspect must exit 3 and print nothing resembling a device report
    # when unprivileged, rather than quietly emitting partial data.
    from corpsman import cli

    monkeypatch.setattr(cli.platform_, "has_backend", lambda: True)
    monkeypatch.setattr(cli.platform_, "is_privileged", lambda: False)

    rc = cli.cmd_inspect(_privileged_args(LUKS))

    assert rc == 3
    captured = capsys.readouterr()
    assert "sda" not in captured.out


def test_exit_code_is_the_worst_device_on_the_bus(monkeypatch, capsys):
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

    rc = cli.cmd_inspect(_privileged_args(LUKS))

    assert rc == 2
