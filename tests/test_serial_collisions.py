# tests/test_serial_collisions.py
from corpsman.types import Device
from corpsman.identity.collisions import serial_is_usable, confirm_string


def mk(name, serial, instance):
    return Device(
        path="/dev/" + name, name=name, instance_path=instance,
        model="USB DISK", serial=serial, wwn=None, size_bytes=32000000000,
        logical_sector=512, physical_sector=512, bus="usb",
        rotational=None, removable=True,
    )


def test_unique_serial_is_usable():
    a = mk("sdb", "AA11", "/sys/.../1-1")
    b = mk("sdc", "BB22", "/sys/.../1-2")
    assert serial_is_usable([a, b], a) is True


def test_duplicate_serial_is_not_usable():
    # Two identical cheap sticks in one dock reporting the same serial.
    a = mk("sdb", "SAME", "/sys/.../1-1")
    b = mk("sdc", "SAME", "/sys/.../1-2")
    assert serial_is_usable([a, b], a) is False
    assert serial_is_usable([a, b], b) is False


def test_blank_serial_is_not_usable():
    a = mk("sdb", None, "/sys/.../1-1")
    assert serial_is_usable([a], a) is False
    b = mk("sdc", "", "/sys/.../1-2")
    assert serial_is_usable([b], b) is False


def test_confirm_string_is_serial_when_usable():
    a = mk("sdb", "AA11", "/sys/.../1-1")
    assert confirm_string([a], a) == "AA11"


def test_confirm_string_falls_back_to_token_when_ambiguous():
    a = mk("sdb", "SAME", "/sys/.../1-1")
    b = mk("sdc", "SAME", "/sys/.../1-2")
    assert confirm_string([a, b], a) == a.identity_token
    assert confirm_string([a, b], a) != confirm_string([a, b], b)
