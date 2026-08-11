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


def test_zero_width_space_makes_a_serial_unusable():
    # Renders identically to "AA11" on screen. Two devices whose serials
    # differ only by an invisible character would both pass a byte-exact
    # uniqueness check and both match the same typed input.
    a = mk("sdb", "AA11", "/sys/.../1-1")
    b = mk("sdc", "AA​11", "/sys/.../1-2")
    assert serial_is_usable([a, b], b) is False
    assert confirm_string([a, b], b) == b.identity_token


def test_control_characters_make_a_serial_unusable():
    a = mk("sdb", "AA\x0111", "/sys/.../1-1")
    assert serial_is_usable([a], a) is False


def test_non_ascii_serial_is_unusable():
    a = mk("sdb", "AA 11", "/sys/.../1-1")
    assert serial_is_usable([a], a) is False


def test_ordinary_printable_serial_still_usable():
    # The rejection must not be so broad it pushes normal drives to tokens.
    a = mk("sdb", "WD-WCC4E5RJ0K2P", "/sys/.../1-1")
    assert serial_is_usable([a], a) is True


def test_device_absent_from_list_raises_rather_than_guessing():
    import pytest
    a = mk("sdb", "SAME", "/sys/.../1-1")
    stale = mk("sdd", "SAME", "/sys/.../1-9")
    with pytest.raises(ValueError):
        serial_is_usable([a], stale)
