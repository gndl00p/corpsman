"""Acquire SMART for a device via smartctl, degrading honestly."""
from .parse import SmartData, parse_smartctl_json
from ..run import run


def collect(device, probe, runner=run):
    # type: (object, object, object) -> SmartData
    if not probe.has("smartctl"):
        return SmartData(
            unreadable_reason="smartctl not installed (install smartmontools)"
        )
    args = ["smartctl", "--json", "-a", device.path]
    if device.bus == "usb":
        # Many USB bridges need an explicit translation layer, and many pass
        # nothing through at all. Failure here reports UNKNOWN, never healthy.
        args = ["smartctl", "--json", "-a", "-d", "sat", device.path]
    result = runner(args, timeout=30)
    if not result.found:
        return SmartData(unreadable_reason="smartctl disappeared between probe and run")
    data = parse_smartctl_json(result.out)
    if not data.available and device.bus == "usb":
        data.unreadable_reason = (
            "USB bridge did not pass SMART through: %s" % data.unreadable_reason
        )
    return data
