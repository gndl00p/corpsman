# src/corpsman/identity/collisions.py
"""Detect serials that cannot safely identify a device.

A blank or duplicated serial is a hard condition, not a warning. If two
devices in a dock report the same string, a confirmation keyed on that
string matches whichever the code resolved first, which is exactly the
wrong-device outcome the confirmation exists to prevent.
"""


def serial_is_usable(devices, device):
    # type: (list, object) -> bool
    serial = (device.serial or "").strip()
    if not serial:
        return False
    matches = 0
    for d in devices:
        if (d.serial or "").strip() == serial:
            matches += 1
    return matches == 1


def confirm_string(devices, device):
    # type: (list, object) -> str
    if serial_is_usable(devices, device):
        return device.serial.strip()
    return device.identity_token
