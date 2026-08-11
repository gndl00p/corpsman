"""Resolve active system state down to the physical devices carrying it.

Comparing a target against the device backing '/' is not a safety check. On
a LUKS+LVM host, '/' is /dev/mapper/vg-root, backed by dm-1, backed by dm-0,
backed by sda2, backed by sda. Only a transitive walk finds sda, and sda is
what an operator would select.

/sys/block/<dev>/slaves is the transitive edge that covers dm-crypt, LVM,
mdraid, bcache and multipath uniformly, because the kernel maintains it for
all of them.
"""
import os

_VIRTUAL_PREFIXES = ("dm-", "md", "loop", "bcache")


def _mountinfo_entries(root):
    # type: (str) -> list
    """Yield (major_minor, mountpoint) from /proc/self/mountinfo.

    mountinfo is used rather than /proc/mounts because it carries the
    major:minor directly, so no stat() of a device node is needed and the
    whole layer stays testable against a fixture tree.
    """
    path = os.path.join(root, "proc", "self", "mountinfo")
    out = []
    try:
        with open(path, "r") as f:
            for line in f:
                fields = line.split()
                if len(fields) < 5:
                    continue
                out.append((fields[2], fields[4]))
    except (IOError, OSError):
        pass
    return out


def _swap_entries(root):
    # type: (str) -> list
    path = os.path.join(root, "proc", "swaps")
    out = []
    try:
        with open(path, "r") as f:
            lines = f.read().splitlines()
    except (IOError, OSError):
        return out
    for line in lines[1:]:
        fields = line.split()
        if fields and fields[0].startswith("/dev/"):
            out.append(fields[0])
    return out


def _name_from_majmin(root, majmin):
    # type: (str, str) -> str
    link = os.path.join(root, "sys", "dev", "block", majmin)
    try:
        return os.path.basename(os.readlink(link))
    except OSError:
        return ""


def _name_from_devpath(root, devpath):
    # type: (str, str) -> str
    """Resolve /dev/mapper/x or /dev/dm-0 or /dev/sda2 to a sysfs name."""
    base = os.path.basename(devpath)
    if os.path.isdir(os.path.join(root, "sys", "block", base)):
        return base
    # /dev/mapper/<name> -> find the dm-N whose dm/name matches
    blockdir = os.path.join(root, "sys", "block")
    if os.path.isdir(blockdir):
        for entry in sorted(os.listdir(blockdir)):
            dm_name = os.path.join(blockdir, entry, "dm", "name")
            try:
                with open(dm_name, "r") as f:
                    if f.read().strip() == base:
                        return entry
            except (IOError, OSError):
                continue
    return base


def _parent_dir(root, name):
    # type: (str, str) -> str
    """Directory for a device that may be a whole disk or a partition."""
    direct = os.path.join(root, "sys", "block", name)
    if os.path.isdir(direct):
        return direct
    blockdir = os.path.join(root, "sys", "block")
    if os.path.isdir(blockdir):
        for disk in sorted(os.listdir(blockdir)):
            cand = os.path.join(blockdir, disk, name)
            if os.path.isdir(cand):
                return cand
    return ""


def _physical_ancestors(root, name, seen=None):
    # type: (str, str, set) -> set
    """Walk slaves transitively until reaching non-virtual whole disks."""
    if seen is None:
        seen = set()
    # Cycle guard. holders/slaves are reciprocal on every stacked dm device,
    # so an unguarded walk does not terminate. Names are unique per device
    # within one /sys, which makes them a sufficient visited key here; if this
    # ever follows `holders` as well as `slaves`, switch to realpath keys.
    if name in seen:
        return set()
    seen.add(name)

    d = _parent_dir(root, name)
    if not d:
        return set()

    slaves_dir = os.path.join(d, "slaves")
    if os.path.isdir(slaves_dir):
        found = set()
        for slave in sorted(os.listdir(slaves_dir)):
            found |= _physical_ancestors(root, slave, seen)
        if found:
            return found

    # No slaves. Either a whole disk, or a partition whose parent is one.
    if name.startswith(_VIRTUAL_PREFIXES):
        return set()
    if os.path.isdir(os.path.join(root, "sys", "block", name)):
        return set([name])
    parent = os.path.basename(os.path.dirname(d))
    if parent:
        return set([parent])
    return set()


def system_devices(root="/"):
    # type: (str) -> dict
    result = {}

    def record(dev, reason):
        result.setdefault(dev, [])
        if reason not in result[dev]:
            result[dev].append(reason)

    for majmin, mountpoint in _mountinfo_entries(root):
        name = _name_from_majmin(root, majmin)
        if not name:
            continue
        for phys in _physical_ancestors(root, name):
            record(phys, mountpoint)

    for devpath in _swap_entries(root):
        name = _name_from_devpath(root, devpath)
        for phys in _physical_ancestors(root, name):
            record(phys, "swap (%s)" % devpath)

    return result
