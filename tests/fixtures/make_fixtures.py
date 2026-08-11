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
    w(root + "/sys/block/sda/size", "1953525168")
    w(root + "/sys/block/sda/removable", "0")
    w(root + "/sys/block/sda/queue/rotational", "0")
    w(root + "/sys/block/sda/queue/logical_block_size", "512")
    w(root + "/sys/block/sda/queue/physical_block_size", "512")
    w(root + "/sys/block/sda/device/model", "Samsung SSD 870 EVO 1TB")
    w(root + "/sys/block/sda/device/vendor", "ATA     ")
    w(root + "/sys/block/sda/sda1/partition", "1")
    w(root + "/sys/block/sda/sda1/size", "1953523120")
    # SATA disks frequently expose no device/serial; by-id is the reliable source.
    symlink("../../sda", root + "/dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_S5Y2NJ0T304891")
    symlink("../../sda", root + "/dev/disk/by-id/wwn-0x5002538f41a1b2c3")
    symlink("../../sda1", root + "/dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_S5Y2NJ0T304891-part1")
    # Minimal mountinfo: root is on a different disk entirely in this fixture.
    w(root + "/proc/self/mountinfo",
      "25 1 259:2 / / rw,relatime shared:1 - ext4 /dev/nvme0n1p2 rw\n")
    w(root + "/proc/swaps", "Filename\t\t\t\tType\t\tSize\tUsed\tPriority\n")
    symlink("../../devices/pci0000:00/0000:00:17.0/ata1/host0/target0:0:0/0:0:0:0/block/sda",
            root + "/sys/dev/block/8:0")


def main():
    root = os.path.join(HERE, "linux", "plain-sata")
    if os.path.isdir(root):
        shutil.rmtree(root)
    plain_sata(root)
    print("wrote " + root)


if __name__ == "__main__":
    main()
