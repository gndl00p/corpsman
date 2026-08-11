"""Command line entry point.

Phase 1 is read-only. There is no destructive subcommand in this tree.
"""
import argparse
import json
import sys

from . import platform_
from .identity import linux as identity_linux
from .identity.collisions import confirm_string
from .probe import Probe
from .smart.collect import collect
from .smart.verdict import assess
from .topology import linux as topology_linux
from .types import (
    HEALTH_REUSE, HEALTH_SCRATCH_ONLY, HEALTH_SCRAP, HEALTH_UNKNOWN,
)

SCHEMA = 1

# HEALTH_UNKNOWN is deliberately absent from this map, so `.get(health, 3)`
# makes it outrank every scored health, including SCRAP. An unreadable
# drive is an audit gap, not a graded result: it may be masking a failing
# controller or cable that is about to take out every disk on that bus. If
# SCRAP outranked UNKNOWN, an operator could replace the one obviously-bad
# drive and walk away from a device that was never actually checked. Do not
# "tidy" HEALTH_UNKNOWN into this mapping at 2 -- see
# test_unknown_outranks_scrap_in_the_exit_code.
_EXIT = {
    HEALTH_REUSE: 0,
    HEALTH_SCRATCH_ONLY: 1,
    HEALTH_SCRAP: 2,
}

# Flavor is terminal-only. It never reaches --json, the ledger, or a
# customer-facing record.
_TRIAGE = {
    HEALTH_REUSE: "return to duty",
    HEALTH_SCRATCH_ONLY: "walking wounded",
    HEALTH_SCRAP: "expectant",
    HEALTH_UNKNOWN: "unable to assess",
}


def build_report(devices, all_devices, sysmap, smart_by_name):
    # type: (list, list, dict, dict) -> dict
    """Build the report for `devices`, but judge collisions against
    `all_devices`.

    Uniqueness of a serial is a property of the whole machine, not of
    whatever subset is being displayed (e.g. a `--device` filter). Judging
    it against a filtered list of one always finds no duplicate, silently
    handing back a serial that collides elsewhere on the same box -- see
    test_filtering_by_device_does_not_hide_a_serial_collision.
    """
    out = []
    for d in devices:
        s = smart_by_name.get(d.name)
        v = assess(s) if s is not None else None
        out.append({
            "name": d.name,
            "path": d.path,
            "identity_token": d.identity_token,
            "confirm": confirm_string(all_devices, d),
            "model": d.model,
            "serial": d.serial,
            "wwn": d.wwn,
            "size_bytes": d.size_bytes,
            "logical_sector": d.logical_sector,
            "physical_sector": d.physical_sector,
            "bus": d.bus,
            "rotational": d.rotational,
            "removable": d.removable,
            "system_state": sysmap.get(d.name, []),
            "health": v.health if v else HEALTH_UNKNOWN,
            "reasons": v.reasons if v else ["not assessed"],
            "cabling": v.cabling if v else None,
        })
    return {"schema": SCHEMA, "devices": out}


def _human(report, stream):
    for d in report["devices"]:
        gb = d["size_bytes"] / 1000.0 ** 3
        flag = ""
        if d["system_state"]:
            flag = "  [SYSTEM: %s]" % ", ".join(d["system_state"])
        stream.write("%s  %.1f GB  %s  #%s%s\n"
                     % (d["path"], gb, d["model"] or "unknown",
                        d["serial"] or d["identity_token"], flag))
        if d["health"] == HEALTH_SCRAP:
            stream.write("  ** CORPSMAN UP **\n")
        stream.write("  %s (%s)\n" % (d["health"], _TRIAGE.get(d["health"], "")))
        for r in d["reasons"]:
            stream.write("    %s\n" % r)
        if d["cabling"]:
            stream.write("    CABLING: %s\n" % d["cabling"])
        stream.write("\n")


def cmd_inspect(args, stream=sys.stdout):
    if not platform_.has_backend():
        stream.write("corpsman has no backend for this platform (%s); "
                     "refusing to guess device conventions\n" % platform_.detect())
        return 3
    if not platform_.is_privileged():
        stream.write("corpsman needs root to read device metadata. Running "
                     "unprivileged returns partial data, and a verdict built "
                     "on partial data is worse than no verdict.\n")
        return 3

    all_devices = identity_linux.enumerate_devices(root=args.root)
    shown = all_devices
    if args.device:
        shown = [d for d in all_devices
                 if d.path == args.device or d.name == args.device]
    try:
        sysmap = topology_linux.system_devices(root=args.root)
    except topology_linux.TopologyError as exc:
        # A gate that cannot see system state must refuse, not emit a
        # report with every disk unflagged -- that reads as "confirmed
        # safe" rather than "unknown". No device block is printed.
        stream.write("corpsman could not determine system state (%s); "
                     "refusing to report device safety blind\n" % exc)
        return 3
    probe = Probe()
    smart_by_name = dict((d.name, collect(d, probe)) for d in shown)
    report = build_report(shown, all_devices, sysmap, smart_by_name)

    if not report["devices"]:
        # "Nothing was inspected" must never read as "everything is
        # healthy" -- the same failure mode fixed in system_devices().
        if args.device:
            stream.write("no device matched %r; nothing inspected\n" % args.device)
        else:
            stream.write("no storage devices found; nothing inspected\n")
        return 3

    if args.json:
        stream.write(json.dumps(report, indent=2) + "\n")
    else:
        _human(report, stream)

    worst = 0
    for d in report["devices"]:
        worst = max(worst, _EXIT.get(d["health"], 3))
    return worst


def main(argv=None):
    parser = argparse.ArgumentParser(prog="doc", description="drive doctor")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("inspect", help="identity, SMART, health verdict")
    p.add_argument("device", nargs="?", help="device path, omit for all")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--root", default="/", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.command == "inspect":
        return cmd_inspect(args)
    parser.print_help()
    return 0
