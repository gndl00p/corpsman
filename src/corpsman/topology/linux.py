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


class TopologyError(Exception):
    """Raised when system state cannot be determined.

    A safety gate that cannot see the system must refuse to answer.
    Returning an empty map would read as "no disk holds system state",
    which makes every disk selectable -- failing open on the one check
    that has no override.
    """


def _mountinfo_entries(root):
    # type: (str) -> list
    """Yield (major_minor, mountpoint) from /proc/self/mountinfo.

    mountinfo is used rather than /proc/mounts because it carries the
    major:minor directly, so no stat() of a device node is needed and the
    whole layer stays testable against a fixture tree.

    A missing or unreadable mountinfo means this layer cannot see what is
    mounted at all, so it raises TopologyError rather than returning an
    empty list -- see TopologyError's docstring. A single malformed line
    within an otherwise-readable file is tolerated and skipped: one odd
    line should not blind the whole check.
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
    except (IOError, OSError) as exc:
        raise TopologyError("cannot read %s: %s" % (path, exc))
    return out


def _swap_entries(root):
    # type: (str) -> list
    """Yield /dev/... paths from /proc/swaps.

    Unlike mountinfo, an absent /proc/swaps is a normal, expected outcome:
    it means the system has no swap configured. That is distinct from the
    file being present but unreadable (permissions, a corrupt /proc), which
    signals something is wrong and must not be silently treated as "no
    swap" -- so only FileNotFoundError is tolerated; every other read
    failure raises TopologyError, same as mountinfo.
    """
    path = os.path.join(root, "proc", "swaps")
    out = []
    try:
        with open(path, "r") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return out
    except (IOError, OSError) as exc:
        raise TopologyError("cannot read %s: %s" % (path, exc))
    for line in lines[1:]:
        fields = line.split()
        if fields and fields[0].startswith("/dev/"):
            out.append(fields[0])
    return out


def _name_from_majmin(root, majmin):
    # type: (str, str) -> str
    """Resolve a major:minor pair to a sysfs device name.

    An absent sys/dev/block/<majmin> entry is an expected outcome for
    devices with no backing block device -- e.g. tmpfs under major 0, the
    kernel's anonymous-bdev range -- not an error, so this stays tolerant
    rather than raising.
    """
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
    """Directory for a device that may be a whole disk or a partition.

    An absent directory is tolerated: it is not an error, just a signal to
    the caller that this name does not resolve to anything walkable.
    """
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
    # so a walk that also followed `holders` would not terminate without
    # this. This walk only follows `slaves`, which forms a DAG by kernel
    # design (a device's slaves are strictly below it in the stack), so the
    # guard is not exercised as load-bearing by the current code path, and
    # test_resolution_terminates_despite_the_holders_slaves_cycle guards
    # termination in general rather than proving this specific guard is
    # load-bearing today. Keep it anyway: it is defence for a future walker
    # that also follows `holders`, and for any corrupted or adversarial
    # /sys content that could otherwise form a slaves-only cycle. Names are
    # unique per device within one /sys, which makes them a sufficient
    # visited key here; if this ever follows `holders` as well as `slaves`,
    # switch to realpath keys.
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

    # No slaves. A whole device is either physical or a virtual dead end.
    if os.path.isdir(os.path.join(root, "sys", "block", name)):
        if name.startswith(_VIRTUAL_PREFIXES):
            return set()
        return set([name])

    # A partition. Climb to the parent and resolve THAT -- a partition on a
    # virtual device (e.g. md0p1 on mdraid array md0) must reach md0's
    # slaves, not stop here just because its own name starts with a virtual
    # prefix. Stopping here would report zero physical disks for a mount on
    # a partitioned array, leaving every member disk destroyable.
    parent = os.path.basename(os.path.dirname(d))
    if parent:
        return _physical_ancestors(root, parent, seen)
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
