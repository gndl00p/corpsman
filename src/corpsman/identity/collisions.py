# src/corpsman/identity/collisions.py
"""Detect serials that cannot safely identify a device.

A blank or duplicated serial is a hard condition, not a warning. If two
devices in a dock report the same string, a confirmation keyed on that
string matches whichever the code resolved first, which is exactly the
wrong-device outcome the confirmation exists to prevent.
"""


def _normalised(serial):
    """Return the comparable form of a serial, or None if unusable.

    A serial is only usable if a human can read it off a screen and type
    it back. Firmware-supplied strings may contain zero-width spaces,
    control characters, or unicode confusables -- "AA11" and "AA​11"
    are byte-distinct but render identically, so both would pass a
    uniqueness check while matching the same typed input. Rejecting them
    falls back to the identity token, which is always unambiguous.
    """
    if serial is None:
        return None
    s = serial.strip()
    if not s:
        return None
    for ch in s:
        if ch < " " or ch > "~":
            return None
    return s


def _require_member(devices, device):
    for d in devices:
        if d.identity_token == device.identity_token:
            return
    raise ValueError(
        "device %s is not in the supplied device list; refusing to judge "
        "serial uniqueness against a set it is not part of"
        % device.identity_token
    )


def serial_is_usable(devices, device):
    # type: (list, object) -> bool
    _require_member(devices, device)
    serial = _normalised(device.serial)
    if serial is None:
        return False
    matches = 0
    for d in devices:
        if _normalised(d.serial) == serial:
            matches += 1
    return matches == 1


def confirm_string(devices, device):
    # type: (list, object) -> str
    _require_member(devices, device)
    if serial_is_usable(devices, device):
        return _normalised(device.serial)
    return device.identity_token
