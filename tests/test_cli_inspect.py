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


# --- C1/C2/C3 (final-review) -----------------------------------------------
#
# The final whole-branch review found the human output path bypasses the
# collision guard (_human() read d["serial"] instead of d["confirm"]),
# that _human() itself had zero test coverage (three separate mutations --
# no-op, marker suppressed, CORPSMAN UP suppressed -- all left 117/117
# green), and that the one existing collision-related test pins
# build_report's signature contract rather than its actual call site in
# cmd_inspect. All three are covered below.

def _mkdev_report(**kw):
    # type: (...) -> dict
    """A single report['devices'] entry, with every field _human() reads
    given a sane default so each test only needs to override what it's
    exercising.
    """
    base = dict(
        name="sda", path="/dev/sda", identity_token="abc123def456",
        confirm="abc123def456", model="Test Model", serial=None, wwn=None,
        size_bytes=1000000000000, logical_sector=512, physical_sector=512,
        bus="sata", rotational=False, removable=False,
        system_state=[], health="REUSE", reasons=["clean"], cabling=None,
    )
    base.update(kw)
    return base


def _human_output(devices):
    from corpsman import cli
    stream = io.StringIO()
    cli._human({"schema": 1, "devices": devices}, stream)
    return stream.getvalue()


def test_human_output_uses_confirm_not_raw_serial_on_collision():
    # C1: two real devices sharing a serial, run through the actual
    # build_report -> _human pipeline. Before the fix, both printed
    # "#SAME123"; the collision guard in identity/collisions.py -- which
    # exists specifically to survive this case -- was dead code on the
    # human path.
    from corpsman import cli
    from corpsman.types import Device

    def dev(name, serial):
        return Device(
            path="/dev/" + name, name=name, instance_path="/dev/" + name,
            model="Model", serial=serial, wwn=None, size_bytes=1000,
            logical_sector=512, physical_sector=512, bus="sata",
            rotational=False, removable=False,
        )

    a = dev("sda", "SAME123")
    b = dev("sdb", "SAME123")
    all_devices = [a, b]

    rep = cli.build_report(all_devices, all_devices, {}, {})
    out = _human_output(rep["devices"])

    assert a.identity_token in out
    assert b.identity_token in out
    assert "#SAME123" not in out


def test_human_shows_system_marker_when_flagged():
    # C2: positive-content coverage. Deleting the [SYSTEM: ...] marker
    # from _human() left 117/117 passing before this test existed --
    # PHASE1-SMOKE.md makes this marker the phase's acceptance criterion.
    out = _human_output([_mkdev_report(system_state=["/"])])
    assert "[SYSTEM: /]" in out


def test_human_omits_system_marker_when_clean():
    out = _human_output([_mkdev_report(system_state=[])])
    assert "[SYSTEM" not in out


def test_human_shows_corpsman_up_for_scrap():
    # C2: `** CORPSMAN UP **` never printing also left 117/117 passing.
    out = _human_output([_mkdev_report(health="SCRAP", reasons=["bad"])])
    assert "** CORPSMAN UP **" in out


def test_human_omits_corpsman_up_for_healthy():
    out = _human_output([_mkdev_report(health="REUSE")])
    assert "CORPSMAN UP" not in out


def test_human_shows_device_path_and_confirm_string():
    out = _human_output([_mkdev_report(path="/dev/sda", confirm="XYZ789")])
    assert "/dev/sda" in out
    assert "XYZ789" in out


def _dup_serial_root(tmp_path):
    """Two whole disks with an identical device/serial, enumerable via
    real sysfs paths -- the minimal fixture needed to run the collision
    scenario through cmd_inspect end to end rather than by calling
    build_report directly.
    """
    root = str(tmp_path / "root")
    for name in ("sda", "sdb"):
        base = os.path.join(root, "sys", "block", name)
        os.makedirs(os.path.join(base, "queue"))
        os.makedirs(os.path.join(base, "device"))
        with open(os.path.join(base, "size"), "w") as f:
            f.write("1953525168\n")
        with open(os.path.join(base, "removable"), "w") as f:
            f.write("0\n")
        with open(os.path.join(base, "queue", "rotational"), "w") as f:
            f.write("0\n")
        with open(os.path.join(base, "queue", "logical_block_size"), "w") as f:
            f.write("512\n")
        with open(os.path.join(base, "queue", "physical_block_size"), "w") as f:
            f.write("512\n")
        with open(os.path.join(base, "device", "model"), "w") as f:
            f.write("Test Disk       \n")
        with open(os.path.join(base, "device", "serial"), "w") as f:
            f.write("DUPTWIN12345\n")
    os.makedirs(os.path.join(root, "proc", "self"))
    with open(os.path.join(root, "proc", "self", "mountinfo"), "w") as f:
        f.write("")
    with open(os.path.join(root, "proc", "swaps"), "w") as f:
        f.write("Filename\n")
    return root


def test_device_filter_confirm_survives_collision_through_cmd_inspect(monkeypatch, tmp_path):
    # C3: the prior collision regression test (test_filtering_by_device_
    # does_not_hide_a_serial_collision, above) calls build_report directly
    # with hand-supplied arguments, so it pins the signature contract, not
    # the actual call in cmd_inspect. Reverting cli.py's
    # `build_report(shown, all_devices, ...)` to
    # `build_report(shown, shown, ...)` passes 117/117 because nothing
    # exercises the --device-filtered path through the real CLI dispatch.
    # This does.
    from corpsman import cli
    from corpsman.identity.linux import enumerate_devices

    monkeypatch.setattr(cli.platform_, "has_backend", lambda: True)
    monkeypatch.setattr(cli.platform_, "is_privileged", lambda: True)
    monkeypatch.setattr(
        cli, "collect",
        lambda device, probe, runner=None: smart("sata_healthy.json"),
    )

    root = _dup_serial_root(tmp_path)
    rc, out = _run(cli, _privileged_args(root, device="sda", as_json=True))

    report = json.loads(out)
    assert len(report["devices"]) == 1
    reported = report["devices"][0]

    all_devs = enumerate_devices(root=root)
    sda_token = [d for d in all_devs if d.name == "sda"][0].identity_token

    assert reported["confirm"] == sda_token
    assert reported["confirm"] != "DUPTWIN12345"


# --- I2/I3 (final-review) ---------------------------------------------------

def test_unexpected_exception_exits_three_not_one(monkeypatch, capsys):
    # I2: chmod 000 on <root>/sys/block makes PermissionError escape
    # enumerate_devices, and main() had no top-level handler -- Python
    # exits 1 on an uncaught exception, which README/PHASE1-SMOKE both
    # sell as "a warning drive is present" in the RMM contract. A crash
    # must never be graded as a health result.
    from corpsman import cli

    monkeypatch.setattr(cli.platform_, "has_backend", lambda: True)
    monkeypatch.setattr(cli.platform_, "is_privileged", lambda: True)

    def boom(root=None):
        raise PermissionError("[Errno 13] Permission denied: 'sys/block'")

    monkeypatch.setattr(cli.identity_linux, "enumerate_devices", boom)

    rc = cli.main(["inspect", "--root", LUKS])
    captured = capsys.readouterr()

    assert rc == 3
    assert "PermissionError" in captured.err


def test_unsupported_platform_exits_three_and_names_platform(monkeypatch):
    # I3: deleting `if not platform_.has_backend(): ...` from cmd_inspect
    # passes 117/117 because every other CLI test monkeypatches
    # has_backend to True. This is a named Global Constraint with nothing
    # behind it until now.
    from corpsman import cli

    monkeypatch.setattr(cli.platform_, "has_backend", lambda: False)
    monkeypatch.setattr(cli.platform_, "detect", lambda: "windows")

    rc, out = _run(cli, _privileged_args(LUKS))

    assert rc == 3
    assert "windows" in out
