# tests/fixtures/make_fixtures.py
"""Recreate the Linux fixture trees.

Run: python3 tests/fixtures/make_fixtures.py
Idempotent. Committed files are regenerated identically.
"""
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))


def w(path, content):
    d = os.path.dirname(path)
    if not os.path.isdir(d):
        os.makedirs(d)
    with open(path, "w") as f:
        f.write(content if content.endswith("\n") else content + "\n")


def symlink(src, dst):
    d = os.path.dirname(dst)
    if not os.path.isdir(d):
        os.makedirs(d)
    if os.path.islink(dst) or os.path.exists(dst):
        os.remove(dst)
    os.symlink(src, dst)


def plain_sata(root):
    # 1 TB Samsung 870 EVO, non-rotational, not removable, one partition.
    # 1953525168 sectors * 512 = 1000204886016 bytes
    #
    # Real sysfs topology: /sys/block/sda is itself a symlink into
    # /sys/devices/..., and /sys/dev/block/8:0 resolves to that same real
    # directory. Attribute files live under the devices tree, not under
    # sys/block directly.
    dev = "/sys/devices/pci0000:00/0000:00:17.0/ata1/host0/target0:0:0/0:0:0:0/block/sda"
    w(root + dev + "/size", "1953525168")
    w(root + dev + "/removable", "0")
    w(root + dev + "/queue/rotational", "0")
    w(root + dev + "/queue/logical_block_size", "512")
    w(root + dev + "/queue/physical_block_size", "512")
    # SCSI translation (libata) truncates ATA's model string to the 16-byte
    # SCSI product-id field, space-padded -- not the full 40-byte ATA
    # IDENTIFY string. Keep the trailing space: it's what makes the
    # parser's .strip() load-bearing.
    w(root + dev + "/device/model", "Samsung SSD 870 ")
    w(root + dev + "/device/vendor", "ATA     ")
    w(root + dev + "/sda1/partition", "1")
    w(root + dev + "/sda1/size", "1953523120")
    symlink("../devices/pci0000:00/0000:00:17.0/ata1/host0/target0:0:0/0:0:0:0/block/sda",
            root + "/sys/block/sda")
    symlink("../../devices/pci0000:00/0000:00:17.0/ata1/host0/target0:0:0/0:0:0:0/block/sda",
            root + "/sys/dev/block/8:0")
    w(root + "/dev/sda", "")
    w(root + "/dev/sda1", "")
    # SATA disks frequently expose no device/serial; by-id is the reliable source.
    symlink("../../sda", root + "/dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_S5Y2NJ0T304891")
    symlink("../../sda", root + "/dev/disk/by-id/wwn-0x5002538f41a1b2c3")
    # udev appends _1, _2... to disambiguate colliding by-id names. Because
    # the suffixed name is a superstring, sorted() always places it AFTER the
    # clean one -- so last-wins extraction silently picks the mangled string.
    symlink("../../sda", root + "/dev/disk/by-id/wwn-0x5002538f41a1b2c3_1")
    symlink("../../sda1", root + "/dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_S5Y2NJ0T304891-part1")
    # Minimal mountinfo: root is on a different disk entirely in this fixture.
    w(root + "/proc/self/mountinfo",
      "25 1 259:2 / / rw,relatime shared:1 - ext4 /dev/nvme0n1p2 rw\n")
    w(root + "/proc/swaps", "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n")


def fourkn(root):
    """A 4Kn drive: logical and physical sector size both 4096.

    Exists so the 512-byte-unit rule is actually falsifiable. sysfs
    'size' stays in 512-byte units even here: 7814037168 * 512 =
    4000787030016. Code that multiplies by logical_block_size instead
    reports 32006296240128 -- eight times the true capacity.
    """
    dev = ("/sys/devices/pci0000:00/0000:00:17.0/ata2/host1/"
           "target1:0:0/1:0:0:0/block/sdc")
    w(root + dev + "/size", "7814037168")
    w(root + dev + "/removable", "0")
    w(root + dev + "/queue/rotational", "1")
    w(root + dev + "/queue/logical_block_size", "4096")
    w(root + dev + "/queue/physical_block_size", "4096")
    w(root + dev + "/device/model", "WDC WD4005FFBX-6")
    w(root + dev + "/device/vendor", "ATA     ")
    symlink("../devices/pci0000:00/0000:00:17.0/ata2/host1/target1:0:0/1:0:0:0/block/sdc",
            root + "/sys/block/sdc")
    symlink("../../devices/pci0000:00/0000:00:17.0/ata2/host1/target1:0:0/1:0:0:0/block/sdc",
            root + "/sys/dev/block/8:32")
    w(root + "/dev/sdc", "")
    symlink("../../sdc", root + "/dev/disk/by-id/ata-WDC_WD4005FFBX-6_VBGL1A2C")
    w(root + "/proc/self/mountinfo",
      "25 1 259:2 / / rw,relatime shared:1 - ext4 /dev/nvme0n1p2 rw\n")
    w(root + "/proc/swaps",
      "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n")


def usb_stick(root):
    """A cheap USB flash stick: removable, blank device/serial, by-id name
    carries an interface:LUN suffix that is not part of the serial.

    This is the case types.py's module docstring calls out: "cheap flash
    reports blank or duplicated serials."
    """
    dev = ("/sys/devices/pci0000:00/0000:00:14.0/usb1/1-1/1-1:1.0/host2/"
           "target2:0:0/2:0:0:0/block/sdd")
    w(root + dev + "/size", "60653568")
    w(root + dev + "/removable", "1")
    w(root + dev + "/queue/rotational", "0")
    w(root + dev + "/queue/logical_block_size", "512")
    w(root + dev + "/queue/physical_block_size", "512")
    # 16-byte SCSI product-id field, space-padded, same convention as the
    # SATA fixture's device/model.
    w(root + dev + "/device/model", "Flash Disk      ")
    w(root + dev + "/device/vendor", "Generic ")
    # Present but blank: a file containing only a newline. Cheap USB
    # bridges frequently expose this instead of omitting the file
    # entirely, and a blank string must not be treated as a real serial.
    w(root + dev + "/device/serial", "")
    symlink("../devices/pci0000:00/0000:00:14.0/usb1/1-1/1-1:1.0/host2/"
            "target2:0:0/2:0:0:0/block/sdd",
            root + "/sys/block/sdd")
    symlink("../../devices/pci0000:00/0000:00:14.0/usb1/1-1/1-1:1.0/host2/"
            "target2:0:0/2:0:0:0/block/sdd",
            root + "/sys/dev/block/8:48")
    w(root + "/dev/sdd", "")
    # udev USB by-id names are usb-<Vendor>_<Product>_<serial>-<iface>:<lun>.
    # The serial is 12345678, not 12345678-0:0.
    symlink("../../sdd", root + "/dev/disk/by-id/usb-Generic_Flash_Disk_12345678-0:0")
    w(root + "/proc/self/mountinfo",
      "25 1 259:2 / / rw,relatime shared:1 - ext4 /dev/nvme0n1p2 rw\n")
    w(root + "/proc/swaps", "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n")


def main():
    for name, builder in (
        ("plain-sata", plain_sata),
        ("fourkn", fourkn),
        ("usb-stick", usb_stick),
    ):
        root = os.path.join(HERE, "linux", name)
        if os.path.isdir(root):
            shutil.rmtree(root)
        builder(root)
        print("wrote " + root)


if __name__ == "__main__":
    main()
