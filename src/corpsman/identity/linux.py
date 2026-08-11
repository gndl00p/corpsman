"""Linux device enumeration from sysfs.

Deliberately does not shell out to lsblk or udevadm: this must work on a
rescue USB where neither is installed.
"""
import os

from ..types import Device

# Virtual and pseudo devices that are never wipe or inspect targets.
_SKIP_PREFIXES = ("loop", "ram", "zram", "dm-", "md", "sr", "fd")


def _read(path, default=None):
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except (IOError, OSError):
        return default


def _read_int(path, default=None):
    v = _read(path)
    if v is None:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def _bus_of(instance_path):
    # type: (str) -> str
    p = instance_path or ""
    if "/usb" in p:
        return "usb"
    if "nvme" in p:
        return "nvme"
    if "/ata" in p:
        return "sata"
    if "/host" in p:
        return "scsi"
    return "unknown"


def _by_id_map(root):
    # type: (str) -> dict
    """Map block device name -> list of by-id link names.

    by-id is the reliable source of serial for SATA disks, which frequently
    expose no device/serial in sysfs at all.
    """
    out = {}
    d = os.path.join(root, "dev", "disk", "by-id")
    if not os.path.isdir(d):
        return out
    for entry in sorted(os.listdir(d)):
        full = os.path.join(d, entry)
        try:
            target = os.readlink(full)
        except OSError:
            continue
        name = os.path.basename(target)
        out.setdefault(name, []).append(entry)
    return out


def _serial_and_wwn(name, links, root):
    # type: (str, list, str) -> tuple
    serial = _read(os.path.join(root, "sys", "block", name, "device", "serial"))
    if serial is not None and not serial.strip():
        # Present-but-blank device/serial (cheap USB bridges expose this
        # instead of omitting the file). Normalise to None BEFORE the loop
        # so the by-id fallback below actually runs.
        serial = None
    wwn = None
    for link in links:
        if link.startswith("wwn-") and wwn is None:
            # First (clean) wwn-* link wins. udev appends _1, _2... to
            # disambiguate colliding by-id names; sorted() always places
            # the suffixed superstring after the clean one, so guarding on
            # wwn is None keeps last-wins from picking the mangled name.
            wwn = link[len("wwn-"):]
        elif serial is None and "_" in link:
            # ata-Samsung_SSD_870_EVO_1TB_S5Y2NJ0T304891 -> trailing field.
            # _by_id_map keys links by the basename of their symlink target,
            # so a -part1 link is filed under the partition's own name
            # (e.g. sda1) and can never appear in a whole-disk's link list;
            # no partition guard is needed here.
            candidate = link.rsplit("_", 1)[-1]
            # udev USB names end -<iface>:<lun>, e.g. ...-0:0. That is bus
            # addressing, not part of the serial.
            if ":" in candidate and "-" in candidate:
                candidate = candidate.rsplit("-", 1)[0]
            if candidate:
                serial = candidate
    return serial, wwn


def enumerate_devices(root="/"):
    # type: (str) -> list
    block = os.path.join(root, "sys", "block")
    if not os.path.isdir(block):
        return []
    by_id = _by_id_map(root)
    devices = []
    for name in sorted(os.listdir(block)):
        if name.startswith(_SKIP_PREFIXES):
            continue
        base = os.path.join(block, name)
        size_sectors = _read_int(os.path.join(base, "size"))
        if not size_sectors:
            continue
        instance_path = os.path.realpath(os.path.join(base, "device"))
        links = by_id.get(name, [])
        serial, wwn = _serial_and_wwn(name, links, root)
        model = _read(os.path.join(base, "device", "model"), "") or ""
        rot = _read_int(os.path.join(base, "queue", "rotational"))
        devices.append(Device(
            path="/dev/" + name,
            name=name,
            instance_path=instance_path,
            model=model.strip(),
            serial=serial,
            wwn=wwn,
            # sysfs 'size' is ALWAYS in 512-byte units, whatever the drive's
            # real sector size. Multiplying by logical_block_size reports a
            # 4Kn drive as eight times its true capacity.
            size_bytes=size_sectors * 512,
            logical_sector=_read_int(os.path.join(base, "queue", "logical_block_size"), 512),
            physical_sector=_read_int(os.path.join(base, "queue", "physical_block_size"), 512),
            bus=_bus_of(instance_path),
            rotational=(None if rot is None else bool(rot)),
            removable=bool(_read_int(os.path.join(base, "removable"), 0)),
        ))
    return devices
