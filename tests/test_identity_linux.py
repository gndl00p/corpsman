import os
from corpsman.identity.linux import enumerate_devices

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "linux", "plain-sata")
FOURKN = os.path.join(os.path.dirname(__file__), "fixtures", "linux", "fourkn")
USB = os.path.join(os.path.dirname(__file__), "fixtures", "linux", "usb-stick")


def test_finds_the_disk():
    devs = enumerate_devices(root=FIX)
    assert [d.name for d in devs] == ["sda"]


def test_reads_geometry():
    d = enumerate_devices(root=FIX)[0]
    assert d.size_bytes == 1953525168 * 512
    assert d.logical_sector == 512
    assert d.physical_sector == 512


def test_reads_model_and_flags():
    d = enumerate_devices(root=FIX)[0]
    # Real SATA disks expose device/model from the 16-byte SCSI product ID,
    # so the fixture stores the truncated, space-padded form and the parser
    # must strip it.
    assert d.model == "Samsung SSD 870"
    assert d.rotational is False
    assert d.removable is False


def test_extracts_serial_and_wwn_from_by_id():
    d = enumerate_devices(root=FIX)[0]
    assert d.serial == "S5Y2NJ0T304891"
    assert d.wwn == "0x5002538f41a1b2c3"


def test_partitions_are_not_returned_as_devices():
    assert all(d.name != "sda1" for d in enumerate_devices(root=FIX))


def test_size_is_always_512_byte_units():
    # sysfs 'size' is in 512-byte sectors regardless of the drive's real
    # sector size. Multiplying by logical_block_size is a classic bug that
    # reports a 4Kn drive as 8x its true capacity.
    #
    # This MUST be asserted against the 4Kn fixture, not the 512e one: on a
    # 512-byte drive the correct and the buggy arithmetic agree, so the
    # assertion could not fail and would be decorative.
    d = enumerate_devices(root=FOURKN)[0]
    assert d.logical_sector == 4096
    assert d.size_bytes == 4000787030016
    # The bug this guards against would yield 8x:
    assert d.size_bytes != 7814037168 * 4096


def test_instance_path_points_into_the_devices_tree():
    # Real sysfs makes /sys/block/<dev> a symlink into /sys/devices/...;
    # instance_path must be the resolved devices path, since that is the
    # stable identity across a /dev renumbering.
    d = enumerate_devices(root=FIX)[0]
    assert "/devices/" in d.instance_path


def test_blank_device_serial_falls_back_to_by_id():
    # A present-but-empty device/serial must not defeat the by-id
    # fallback. Cheap flash reports blank serials, and by-id is then the
    # only source.
    d = enumerate_devices(root=USB)[0]
    assert d.serial == "12345678"


def test_usb_by_id_serial_strips_interface_and_lun_suffix():
    # udev USB names are usb-<Vendor>_<Product>_<serial>-<iface>:<lun>.
    # The serial is 12345678, not 12345678-0:0.
    d = enumerate_devices(root=USB)[0]
    assert "-0:0" not in (d.serial or "")


def test_udev_dedup_suffixed_wwn_does_not_win():
    # wwn-<x> and wwn-<x>_1 both exist; sorted() puts _1 last, so
    # last-wins extraction picks the mangled one.
    d = enumerate_devices(root=FIX)[0]
    assert d.wwn == "0x5002538f41a1b2c3"
