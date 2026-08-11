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


SDA = ("/sys/devices/pci0000:00/0000:00:17.0/ata1/host0/target0:0:0/"
       "0:0:0:0/block/sda")
SDB = ("/sys/devices/pci0000:00/0000:00:17.0/ata2/host1/target1:0:0/"
       "1:0:0:0/block/sdb")
DM0 = "/sys/devices/virtual/block/dm-0"
DM1 = "/sys/devices/virtual/block/dm-1"


def rel_symlink(root, target_abs, link_abs):
    """Symlink link_abs -> target_abs using a correct relative path.

    Computing the relative path rather than hand-writing ../.. chains is
    how the fixture avoids the dangling links the first version shipped.
    """
    link_full = root + link_abs
    d = os.path.dirname(link_full)
    if not os.path.isdir(d):
        os.makedirs(d)
    rel = os.path.relpath(root + target_abs, d)
    if os.path.islink(link_full) or os.path.exists(link_full):
        os.remove(link_full)
    os.symlink(rel, link_full)


def luks_lvm(root):
    """/ lives on vg-root -> dm-1 (LVM) -> dm-0 (LUKS) -> sda2 -> sda.

    A naive check that only compares the target against the device backing
    '/' never looks at sda, so an operator selecting /dev/sda destroys a
    live host. This fixture exists to make that failure impossible to ship.
    """
    # Physical disk. Model is the 16-byte SCSI product ID, space padded.
    w(root + SDA + "/size", "1953525168")
    w(root + SDA + "/removable", "0")
    w(root + SDA + "/queue/rotational", "1")
    w(root + SDA + "/queue/logical_block_size", "512")
    w(root + SDA + "/queue/physical_block_size", "4096")
    w(root + SDA + "/device/model", "WDC WD10EZEX-0")
    w(root + SDA + "/device/vendor", "ATA     ")
    w(root + SDA + "/sda1/partition", "1")
    w(root + SDA + "/sda1/size", "1048576")
    w(root + SDA + "/sda2/partition", "2")
    w(root + SDA + "/sda2/size", "1952476592")
    # A second, unrelated disk that must NOT be flagged.
    w(root + SDB + "/size", "7814037168")
    w(root + SDB + "/removable", "0")
    w(root + SDB + "/queue/rotational", "1")
    w(root + SDB + "/queue/logical_block_size", "512")
    w(root + SDB + "/queue/physical_block_size", "4096")
    w(root + SDB + "/device/model", "WDC WD40EFRX-6")
    w(root + SDB + "/device/vendor", "ATA     ")
    # dm-0 = LUKS on sda2
    w(root + DM0 + "/size", "1952474544")
    w(root + DM0 + "/removable", "0")
    w(root + DM0 + "/queue/rotational", "1")
    w(root + DM0 + "/queue/logical_block_size", "512")
    w(root + DM0 + "/queue/physical_block_size", "512")
    w(root + DM0 + "/dm/name", "luks-9f3c")
    # dm-1 = LVM logical volume on dm-0
    w(root + DM1 + "/size", "1900000000")
    w(root + DM1 + "/removable", "0")
    w(root + DM1 + "/queue/rotational", "1")
    w(root + DM1 + "/queue/logical_block_size", "512")
    w(root + DM1 + "/queue/physical_block_size", "512")
    w(root + DM1 + "/dm/name", "vg-root")

    # /sys/block/<dev> symlinks into the devices tree, as the kernel does.
    rel_symlink(root, SDA, "/sys/block/sda")
    rel_symlink(root, SDB, "/sys/block/sdb")
    rel_symlink(root, DM0, "/sys/block/dm-0")
    rel_symlink(root, DM1, "/sys/block/dm-1")

    # The holder/slave edges. These are symlinks on a real kernel, and they
    # are the transitive chain the topology layer walks:
    #   dm-1 (vg-root) -> dm-0 (luks) -> sda2 -> sda
    rel_symlink(root, DM0, SDA + "/sda2/holders/dm-0")
    rel_symlink(root, SDA + "/sda2", DM0 + "/slaves/sda2")
    rel_symlink(root, DM1, DM0 + "/holders/dm-1")
    rel_symlink(root, DM0, DM1 + "/slaves/dm-0")

    # major:minor resolution, resolving to the same dirs as /sys/block.
    # Partitions get their own sys/dev/block entry too, same as whole disks
    # -- the kernel creates one for every block device, not just disks. The
    # first version of this fixture omitted sda1's (8:1), which meant /boot
    # silently failed to resolve to any physical device at all: the walk
    # never even started. Verified against this workstation's real
    # /sys/dev/block, which has an entry for every partition.
    rel_symlink(root, SDA, "/sys/dev/block/8:0")
    rel_symlink(root, SDA + "/sda1", "/sys/dev/block/8:1")
    rel_symlink(root, SDA + "/sda2", "/sys/dev/block/8:2")
    rel_symlink(root, SDB, "/sys/dev/block/8:16")
    rel_symlink(root, DM0, "/sys/dev/block/253:0")
    rel_symlink(root, DM1, "/sys/dev/block/253:1")

    # Device nodes so nothing dangles.
    for node in ("sda", "sda1", "sda2", "sdb", "dm-0", "dm-1"):
        w(root + "/dev/" + node, "")
    # / is dm-1 (253:1). /boot is sda1 (8:1). Swap is on dm-0.
    w(root + "/proc/self/mountinfo",
      "25 1 253:1 / / rw,relatime shared:1 - ext4 /dev/mapper/vg-root rw\n"
      "31 25 8:1 / /boot rw,relatime shared:2 - ext2 /dev/sda1 rw\n"
      "40 25 0:22 / /run rw,nosuid,nodev - tmpfs tmpfs rw\n")
    w(root + "/proc/swaps",
      "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"
      "/dev/dm-0                               partition\t8388604\t\t0\t\t-2\n")


SDC = ("/sys/devices/pci0000:00/0000:00:17.0/ata1/host0/target0:0:0/"
       "0:0:0:0/block/sdc")
SDD = ("/sys/devices/pci0000:00/0000:00:17.0/ata2/host1/target1:0:0/"
       "1:0:0:0/block/sdd")
MD0 = "/sys/devices/virtual/block/md0"


def mdraid(root):
    """/ lives on md0p1, a partition on mdraid array md0, itself made of
    member disks sdc and sdd.

    A resolver that stops the walk at the first virtual device it meets
    (rather than climbing from a virtual partition to its whole-device
    parent) reports zero physical disks here, leaving both array members
    destroyable. This fixture exists to make that failure impossible to
    ship.
    """
    # Two physical member disks, each with one partition (the raid member).
    w(root + SDC + "/size", "1953525168")
    w(root + SDC + "/removable", "0")
    w(root + SDC + "/queue/rotational", "1")
    w(root + SDC + "/queue/logical_block_size", "512")
    w(root + SDC + "/queue/physical_block_size", "512")
    w(root + SDC + "/device/model", "WDC WD10EZEX-0")
    w(root + SDC + "/device/vendor", "ATA     ")
    w(root + SDC + "/sdc1/partition", "1")
    w(root + SDC + "/sdc1/size", "1953523120")

    w(root + SDD + "/size", "1953525168")
    w(root + SDD + "/removable", "0")
    w(root + SDD + "/queue/rotational", "1")
    w(root + SDD + "/queue/logical_block_size", "512")
    w(root + SDD + "/queue/physical_block_size", "512")
    w(root + SDD + "/device/model", "WDC WD10EZEX-0")
    w(root + SDD + "/device/vendor", "ATA     ")
    w(root + SDD + "/sdd1/partition", "1")
    w(root + SDD + "/sdd1/size", "1953523120")

    # md0 = mdraid array over sdc1 + sdd1, itself partitioned (md0p1).
    w(root + MD0 + "/size", "1953523120")
    w(root + MD0 + "/removable", "0")
    w(root + MD0 + "/queue/rotational", "1")
    w(root + MD0 + "/queue/logical_block_size", "512")
    w(root + MD0 + "/queue/physical_block_size", "512")
    w(root + MD0 + "/md0p1/partition", "1")
    w(root + MD0 + "/md0p1/size", "1953520000")

    # /sys/block/<dev> symlinks into the devices tree, as the kernel does.
    rel_symlink(root, SDC, "/sys/block/sdc")
    rel_symlink(root, SDD, "/sys/block/sdd")
    rel_symlink(root, MD0, "/sys/block/md0")

    # The holder/slave edges: md0 -> [sdc1, sdd1]. Reciprocal holders links
    # on the member partitions, as the kernel creates them.
    rel_symlink(root, SDC + "/sdc1", MD0 + "/slaves/sdc1")
    rel_symlink(root, SDD + "/sdd1", MD0 + "/slaves/sdd1")
    rel_symlink(root, MD0, SDC + "/sdc1/holders/md0")
    rel_symlink(root, MD0, SDD + "/sdd1/holders/md0")

    # major:minor resolution, resolving to the same dirs as /sys/block.
    # md0 is 9:0, its partition md0p1 is 9:1 -- the pair the mount resolves
    # through. sdc/sdd and their member partitions get realistic majmins too
    # so nothing dangles.
    rel_symlink(root, SDC, "/sys/dev/block/8:32")
    rel_symlink(root, SDC + "/sdc1", "/sys/dev/block/8:33")
    rel_symlink(root, SDD, "/sys/dev/block/8:48")
    rel_symlink(root, SDD + "/sdd1", "/sys/dev/block/8:49")
    rel_symlink(root, MD0, "/sys/dev/block/9:0")
    rel_symlink(root, MD0 + "/md0p1", "/sys/dev/block/9:1")

    # Device nodes so nothing dangles.
    for node in ("sdc", "sdc1", "sdd", "sdd1", "md0", "md0p1"):
        w(root + "/dev/" + node, "")

    # / is md0p1 (9:1).
    w(root + "/proc/self/mountinfo",
      "25 1 9:1 / / rw,relatime shared:1 - ext4 /dev/md0p1 rw\n")
    w(root + "/proc/swaps",
      "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n")


def main():
    for name, builder in (
        ("plain-sata", plain_sata),
        ("fourkn", fourkn),
        ("usb-stick", usb_stick),
        ("luks-lvm", luks_lvm),
        ("mdraid", mdraid),
    ):
        root = os.path.join(HERE, "linux", name)
        if os.path.isdir(root):
            shutil.rmtree(root)
        builder(root)
        print("wrote " + root)


if __name__ == "__main__":
    main()
