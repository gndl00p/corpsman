# tests/test_identity_token.py
from corpsman.types import Device


def mkdev(**kw):
    base = dict(
        path="/dev/sda", name="sda",
        instance_path="/sys/devices/pci0000:00/0000:00:17.0/ata1/host0/target0:0:0/0:0:0:0/block/sda",
        model="Samsung SSD 870", serial="S5Y2NJ0T304891", wwn="0x5002538f41a1b2c3",
        size_bytes=1000204886016, logical_sector=512, physical_sector=512,
        bus="sata", rotational=False, removable=False,
    )
    base.update(kw)
    return Device(**base)


def test_token_is_stable_for_identical_input():
    assert mkdev().identity_token == mkdev().identity_token


def test_token_is_12_hex_chars():
    t = mkdev().identity_token
    assert len(t) == 12
    assert all(c in "0123456789abcdef" for c in t)


def test_size_change_changes_token():
    assert mkdev().identity_token != mkdev(size_bytes=512110190592).identity_token


def test_blank_serials_do_not_collide_when_instance_path_differs():
    # Two identical cheap USB sticks in one dock: same model, blank serial.
    # Serial alone would collide. The instance path must keep them distinct.
    a = mkdev(serial=None, wwn=None, model="USB DISK", instance_path="/sys/devices/pci0000:00/usb1/1-1/block/sdb")
    b = mkdev(serial=None, wwn=None, model="USB DISK", instance_path="/sys/devices/pci0000:00/usb1/1-2/block/sdc")
    assert a.identity_token != b.identity_token


def test_device_path_alone_does_not_change_token():
    # /dev/sdb -> /dev/sdc across a replug is not a different device.
    assert mkdev(path="/dev/sdb", name="sdb").identity_token == mkdev(path="/dev/sdc", name="sdc").identity_token


def test_serial_change_changes_token():
    assert mkdev().identity_token != mkdev(serial="DIFFERENT").identity_token


def test_model_change_changes_token():
    assert mkdev().identity_token != mkdev(model="Other Model").identity_token


def test_wwn_change_changes_token():
    assert mkdev().identity_token != mkdev(wwn="0xdeadbeef").identity_token


def test_bus_change_changes_token():
    assert mkdev().identity_token != mkdev(bus="usb").identity_token


def test_separator_injection_cannot_forge_another_devices_token():
    # A field is firmware-controlled and may contain any bytes. Two
    # distinct field tuples must never produce the same token.
    a = mkdev(model="A", serial="B")
    b = mkdev(model="A\x1fB", serial="")
    assert a.identity_token != b.identity_token
