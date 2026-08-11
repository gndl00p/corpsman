# corpsman Phase 1 — Linux targeting and `inspect` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a working, read-only `doc inspect` on Linux that identifies storage devices by composite token, resolves the full block-device dependency graph to find system-state ancestors, reads SMART, and prints a health verdict — with zero destructive code anywhere in the tree.

**Architecture:** Pure functions over an injectable filesystem root, so every targeting decision is tested against committed fixture trees rather than hardware. `SysfsReader(root)` reads a directory tree that is `/` in production and `tests/fixtures/linux/<case>/` under test. External binaries are probed at runtime and never assumed. Nothing in this phase opens a block device for writing; there is no code path that could.

**Tech Stack:** Python 3.8+, standard library only at runtime. `pytest` as a development-only dependency — the zero-dependency guarantee is about what an operator must install to run `doc`, not about the test harness.

## Global Constraints

- Runtime imports: Python standard library only. No third-party runtime imports, ever. Enforced by a test in Task 12.
- Python floor: 3.8. No walrus-in-comprehension gymnastics, no `match`, no `X | Y` type syntax, no `dict` ordering assumptions beyond insertion order.
- Every external process invocation goes through `corpsman.run.run()`, which forces `LC_ALL=C` and `LANG=C`. Never call `subprocess` directly.
- Every filesystem read that could be a fixture takes a `root` parameter. Never hardcode `/sys` or `/proc`.
- No code in this phase may open a block device with any write flag. No `os.O_WRONLY`, `os.O_RDWR`, `"wb"`, or `"r+b"` against a device path.
- `UNKNOWN` health is never collapsed into `REUSE`. Unreadable is not healthy.
- SMART attribute 199 (`UDMA_CRC_Error_Count`) is reported as a **cabling** fault and excluded from the drive health verdict.
- Health verdicts use thresholds and rate-of-change, never "nonzero means SCRAP".
- Flavor text (`CORPSMAN UP`, triage words) appears only in human terminal output. `--json` emits plain technical enum values.
- Platform gate: on any platform without an implemented backend, `inspect` refuses with a clear message and exit code 3.

---

### Task 1: Repository skeleton, run helper, platform gate

**Files:**
- Create: `pyproject.toml`
- Create: `src/corpsman/__init__.py`
- Create: `src/corpsman/run.py`
- Create: `src/corpsman/platform_.py`
- Create: `tests/test_run.py`
- Create: `tests/test_platform.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `corpsman.run.run(argv: list, timeout: int = 60) -> RunResult` where `RunResult` is a `NamedTuple` with fields `rc: int`, `out: str`, `err: str`, `found: bool`. Never raises on non-zero exit. `found=False` when the binary is absent.
  - `corpsman.platform_.detect() -> str` returning `"linux"`, `"darwin"`, `"windows"`, or `"unsupported"`.
  - `corpsman.platform_.is_privileged() -> bool`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_run.py
from corpsman.run import run

def test_run_captures_stdout():
    r = run(["python3", "-c", "print('hi')"])
    assert r.found is True
    assert r.rc == 0
    assert r.out.strip() == "hi"

def test_run_forces_c_locale():
    r = run(["python3", "-c", "import os; print(os.environ['LC_ALL'], os.environ['LANG'])"])
    assert r.out.strip() == "C C"

def test_run_missing_binary_sets_found_false():
    r = run(["corpsman-no-such-binary-xyz"])
    assert r.found is False
    assert r.rc != 0

def test_run_nonzero_exit_does_not_raise():
    r = run(["python3", "-c", "import sys; sys.exit(3)"])
    assert r.rc == 3
```

```python
# tests/test_platform.py
from corpsman.platform_ import detect, is_privileged

def test_detect_returns_known_value():
    assert detect() in ("linux", "darwin", "windows", "unsupported")

def test_is_privileged_returns_bool():
    assert isinstance(is_privileged(), bool)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/ -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'corpsman'`

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[project]
name = "corpsman"
version = "0.1.0"
description = "Drive doctor for the bench"
requires-python = ">=3.8"
dependencies = []

[project.scripts]
doc = "corpsman.cli:main"

[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 4: Write the implementation**

```python
# src/corpsman/__init__.py
__version__ = "0.1.0"
```

```python
# src/corpsman/run.py
"""Single chokepoint for every external process invocation."""
import os
import subprocess
from typing import List, NamedTuple


class RunResult(NamedTuple):
    rc: int
    out: str
    err: str
    found: bool


def run(argv, timeout=60):
    # type: (List[str], int) -> RunResult
    """Run a command with a pinned C locale.

    Locale is forced because smartctl, hdparm and friends translate their
    output, which silently breaks parsing and feeds a wrong health verdict
    into everything downstream.
    """
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    try:
        p = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            universal_newlines=True,
        )
    except (OSError, FileNotFoundError):
        return RunResult(rc=127, out="", err="binary not found", found=False)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        out, err = p.communicate()
        return RunResult(rc=124, out=out or "", err="timeout", found=True)
    return RunResult(rc=p.returncode, out=out or "", err=err or "", found=True)
```

```python
# src/corpsman/platform_.py
"""Platform detection and privilege check.

Unsupported platforms are refused rather than guessed at: device path
conventions differ enough that a best-guess backend is a hazard.
"""
import os
import sys

SUPPORTED = ("linux",)


def detect():
    # type: () -> str
    p = sys.platform
    if p.startswith("linux"):
        return "linux"
    if p == "darwin":
        return "darwin"
    if p in ("win32", "cygwin"):
        return "windows"
    return "unsupported"


def has_backend():
    # type: () -> bool
    return detect() in SUPPORTED


def is_privileged():
    # type: () -> bool
    if detect() == "windows":
        return False
    return hasattr(os, "geteuid") and os.geteuid() == 0
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/ -v`
Expected: PASS, 6 tests

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/corpsman/__init__.py src/corpsman/run.py src/corpsman/platform_.py tests/test_run.py tests/test_platform.py
git commit -m "feat: repo skeleton, locale-pinned run helper, platform gate"
```

---

### Task 2: Device type and composite identity token

**Files:**
- Create: `src/corpsman/types.py`
- Create: `tests/test_identity_token.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `corpsman.types.Device` — a class with attributes `path: str`, `name: str`, `instance_path: str`, `model: str`, `serial: Optional[str]`, `wwn: Optional[str]`, `size_bytes: int`, `logical_sector: int`, `physical_sector: int`, `bus: str`, `rotational: Optional[bool]`, `removable: bool`.
  - `Device.identity_token` — a read-only property returning a 12-character lowercase hex string.
  - `corpsman.types.HEALTH_REUSE`, `HEALTH_SCRATCH_ONLY`, `HEALTH_SCRAP`, `HEALTH_UNKNOWN` — the string constants `"REUSE"`, `"SCRATCH_ONLY"`, `"SCRAP"`, `"UNKNOWN"`.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_identity_token.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'corpsman.types'`

- [ ] **Step 3: Write the implementation**

```python
# src/corpsman/types.py
"""Core value types.

Identity deliberately does not key on serial number alone. USB bridges
report the bridge's serial rather than the drive's, and cheap flash reports
blank or duplicated serials, so two devices in one dock can produce the same
string. The token combines the stable OS instance path with geometry and
identifiers so that neither a blank serial nor a /dev renumbering can make
two devices look like one.
"""
import hashlib

HEALTH_REUSE = "REUSE"
HEALTH_SCRATCH_ONLY = "SCRATCH_ONLY"
HEALTH_SCRAP = "SCRAP"
HEALTH_UNKNOWN = "UNKNOWN"

_TOKEN_LEN = 12


class Device(object):
    __slots__ = (
        "path", "name", "instance_path", "model", "serial", "wwn",
        "size_bytes", "logical_sector", "physical_sector", "bus",
        "rotational", "removable",
    )

    def __init__(self, path, name, instance_path, model, serial, wwn,
                 size_bytes, logical_sector, physical_sector, bus,
                 rotational, removable):
        self.path = path
        self.name = name
        self.instance_path = instance_path
        self.model = model
        self.serial = serial
        self.wwn = wwn
        self.size_bytes = size_bytes
        self.logical_sector = logical_sector
        self.physical_sector = physical_sector
        self.bus = bus
        self.rotational = rotational
        self.removable = removable

    @property
    def identity_token(self):
        # type: () -> str
        # /dev name is deliberately excluded: it is not stable across replug.
        parts = [
            self.instance_path or "",
            self.wwn or "",
            str(self.size_bytes),
            self.model or "",
            self.serial or "",
            self.bus or "",
        ]
        blob = "\x1f".join(parts).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:_TOKEN_LEN]

    def __repr__(self):
        return "<Device %s %s %s>" % (self.name, self.model, self.identity_token)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_identity_token.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Commit**

```bash
git add src/corpsman/types.py tests/test_identity_token.py
git commit -m "feat: Device type with composite identity token"
```

---

### Task 3: Linux fixture tree for a plain SATA disk

**Files:**
- Create: `tests/fixtures/linux/plain-sata/sys/block/sda/size`
- Create: `tests/fixtures/linux/plain-sata/sys/block/sda/removable`
- Create: `tests/fixtures/linux/plain-sata/sys/block/sda/queue/rotational`
- Create: `tests/fixtures/linux/plain-sata/sys/block/sda/queue/logical_block_size`
- Create: `tests/fixtures/linux/plain-sata/sys/block/sda/queue/physical_block_size`
- Create: `tests/fixtures/linux/plain-sata/sys/block/sda/device/model`
- Create: `tests/fixtures/linux/plain-sata/sys/block/sda/device/vendor`
- Create: `tests/fixtures/linux/plain-sata/sys/block/sda/sda1/partition`
- Create: `tests/fixtures/make_fixtures.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a fixture tree at `tests/fixtures/linux/plain-sata/` and a generator script that recreates it. Later tasks read this tree via `root=`.

Symlinks under `dev/disk/by-id/` and `sys/dev/block/` are created by the generator and also committed — git stores them as mode `120000` blobs, which is correct on Linux and macOS. A Windows clone without symlink support would materialise them as regular text files, which would break serial extraction; that is acceptable while Phase 1 is Linux-only, and re-running the generator repairs any tree.

- [ ] **Step 1: Write the fixture generator**

```python
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
```

- [ ] **Step 2: Run the generator**

Run: `python3 tests/fixtures/make_fixtures.py`
Expected: prints `wrote .../tests/fixtures/linux/plain-sata`

- [ ] **Step 3: Verify the tree looks right**

Run: `find tests/fixtures/linux/plain-sata -type f | sort`
Expected: lists `size`, `removable`, `rotational`, `logical_block_size`, `physical_block_size`, `model`, `vendor`, `sda1/partition`, `sda1/size`, `mountinfo`, `swaps`

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/
git commit -m "test: Linux sysfs fixture tree for a plain SATA disk"
```

---

### Task 4: Linux device enumeration from sysfs

**Files:**
- Create: `src/corpsman/identity/__init__.py`
- Create: `src/corpsman/identity/linux.py`
- Create: `tests/test_identity_linux.py`

**Interfaces:**
- Consumes: `corpsman.types.Device`.
- Produces:
  - `corpsman.identity.linux.enumerate_devices(root="/") -> List[Device]` — whole devices only, no partitions, no loop/ram/dm virtuals.
  - `corpsman.identity.enumerate(root="/") -> List[Device]` — platform dispatch, raising `RuntimeError` when no backend exists.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_identity_linux.py
import os
from corpsman.identity.linux import enumerate_devices

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "linux", "plain-sata")
FOURKN = os.path.join(os.path.dirname(__file__), "fixtures", "linux", "fourkn")


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_identity_linux.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'corpsman.identity'`

- [ ] **Step 3: Write the implementation**

```python
# src/corpsman/identity/linux.py
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
    wwn = None
    for link in links:
        if link.startswith("wwn-"):
            wwn = link[len("wwn-"):]
        elif serial is None and "_" in link:
            # ata-Samsung_SSD_870_EVO_1TB_S5Y2NJ0T304891 -> trailing field
            candidate = link.rsplit("_", 1)[-1]
            if candidate and not candidate.startswith("part"):
                serial = candidate
    if serial == "":
        serial = None
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
        try:
            instance_path = os.path.realpath(os.path.join(base, "device"))
        except OSError:
            instance_path = base
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
```

```python
# src/corpsman/identity/__init__.py
"""Device identity, dispatched by platform."""
from .. import platform_


def enumerate(root="/"):
    # type: (str) -> list
    plat = platform_.detect()
    if plat == "linux":
        from . import linux
        return linux.enumerate_devices(root=root)
    raise RuntimeError(
        "no device backend for platform '%s'; refusing to guess device "
        "conventions" % plat
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_identity_linux.py -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add src/corpsman/identity/ tests/test_identity_linux.py
git commit -m "feat: Linux device enumeration from sysfs"
```

---

### Task 5: LUKS+LVM fixture — the case that destroys a production disk

**Files:**
- Modify: `tests/fixtures/make_fixtures.py` (add `luks_lvm` builder and call it from `main`)

**Interfaces:**
- Consumes: the `w`/`symlink`/`main` helpers from Task 3.
- Produces: fixture tree at `tests/fixtures/linux/luks-lvm/` where `/` is `/dev/mapper/vg-root`, backed by `/dev/dm-0` (LUKS), backed by `/dev/sda2`, backed by `/dev/sda`.

- [ ] **Step 1: Add the fixture builder**

```python
# append to tests/fixtures/make_fixtures.py, above main()
#
# Follow the SAME topology convention Task 3 established after review:
# attribute files live under a real sys/devices/... path, and sys/block/<dev>
# plus sys/dev/block/<maj>:<min> are symlinks into it. That is what a real
# kernel exposes, and a fixture that flattens it teaches the parser a wrong
# lesson about where instance_path comes from.

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
    rel_symlink(root, SDA, "/sys/dev/block/8:0")
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
```

- [ ] **Step 2: Wire it into `main`**

```python
def main():
    for name, builder in (("plain-sata", plain_sata), ("fourkn", fourkn),
                          ("luks-lvm", luks_lvm)):
        root = os.path.join(HERE, "linux", name)
        if os.path.isdir(root):
            shutil.rmtree(root)
        builder(root)
        print("wrote " + root)
```

- [ ] **Step 3: Regenerate and verify**

Run: `python3 tests/fixtures/make_fixtures.py && find tests/fixtures/linux/luks-lvm -name mountinfo -exec cat {} \;`
Expected: prints three mountinfo lines, the first with `253:1`

- [ ] **Step 4: Confirm existing tests still pass**

Run: `python3 -m pytest tests/ -v`
Expected: PASS, all tests from Tasks 1–4

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/
git commit -m "test: LUKS+LVM fixture where / is four layers above the physical disk"
```

---

### Task 6: Topology — resolve system state to physical devices

**Files:**
- Create: `src/corpsman/topology/__init__.py`
- Create: `src/corpsman/topology/linux.py`
- Create: `tests/test_topology_linux.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (operates on names, not `Device`).
- Produces:
  - `corpsman.topology.linux.system_devices(root="/") -> Dict[str, List[str]]` — maps physical device name (e.g. `"sda"`) to a list of human-readable reasons (e.g. `["/", "/boot", "swap"]`).
  - `corpsman.topology.system_devices(root="/") -> Dict[str, List[str]]` — platform dispatch.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_topology_linux.py
import os
from corpsman.topology.linux import system_devices

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "linux")
LUKS = os.path.join(FIX, "luks-lvm")
PLAIN = os.path.join(FIX, "plain-sata")


def test_resolves_root_through_lvm_and_luks_to_the_physical_disk():
    # This is the whole point of the layer. / is /dev/mapper/vg-root,
    # backed by dm-1 -> dm-0 -> sda2 -> sda.
    sysd = system_devices(root=LUKS)
    assert "sda" in sysd


def test_reason_names_the_mountpoint():
    sysd = system_devices(root=LUKS)
    assert "/" in sysd["sda"]


def test_boot_on_a_partition_is_also_caught():
    assert "/boot" in system_devices(root=LUKS)["sda"]


def test_swap_on_a_mapper_device_is_caught():
    assert any("swap" in r for r in system_devices(root=LUKS)["sda"])


def test_unrelated_disk_is_not_flagged():
    # sdb holds nothing. Flagging it would train operators to override.
    assert "sdb" not in system_devices(root=LUKS)


def test_tmpfs_does_not_produce_a_device():
    for reasons in system_devices(root=LUKS).values():
        assert "/run" not in reasons


def test_plain_fixture_root_is_on_a_different_disk():
    # / is on nvme0n1p2, which is not enumerated in this fixture at all.
    assert "sda" not in system_devices(root=PLAIN)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_topology_linux.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'corpsman.topology'`

- [ ] **Step 3: Write the implementation**

```python
# src/corpsman/topology/linux.py
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
```

```python
# src/corpsman/topology/__init__.py
"""Active system state resolution, dispatched by platform."""
from .. import platform_


def system_devices(root="/"):
    # type: (str) -> dict
    plat = platform_.detect()
    if plat == "linux":
        from . import linux
        return linux.system_devices(root=root)
    raise RuntimeError(
        "no topology backend for platform '%s'; refusing to guess" % plat
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_topology_linux.py -v`
Expected: PASS, 7 tests

- [ ] **Step 5: Commit**

```bash
git add src/corpsman/topology/ tests/test_topology_linux.py
git commit -m "feat: resolve system state through LUKS/LVM/mdraid to physical devices"
```

---

### Task 7: Duplicate and blank serial detection

**Files:**
- Create: `src/corpsman/identity/collisions.py`
- Create: `tests/test_serial_collisions.py`

**Interfaces:**
- Consumes: `corpsman.types.Device`.
- Produces:
  - `corpsman.identity.collisions.serial_is_usable(devices: List[Device], device: Device) -> bool` — `False` when the serial is blank or shared with another enumerated device.
  - `corpsman.identity.collisions.confirm_string(devices, device) -> str` — the string an operator must type: the serial when usable, otherwise the identity token.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_serial_collisions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'corpsman.identity.collisions'`

- [ ] **Step 3: Write the implementation**

```python
# src/corpsman/identity/collisions.py
"""Detect serials that cannot safely identify a device.

A blank or duplicated serial is a hard condition, not a warning. If two
devices in a dock report the same string, a confirmation keyed on that
string matches whichever the code resolved first, which is exactly the
wrong-device outcome the confirmation exists to prevent.
"""


def serial_is_usable(devices, device):
    # type: (list, object) -> bool
    serial = (device.serial or "").strip()
    if not serial:
        return False
    matches = 0
    for d in devices:
        if (d.serial or "").strip() == serial:
            matches += 1
    return matches == 1


def confirm_string(devices, device):
    # type: (list, object) -> str
    if serial_is_usable(devices, device):
        return device.serial.strip()
    return device.identity_token
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_serial_collisions.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Commit**

```bash
git add src/corpsman/identity/collisions.py tests/test_serial_collisions.py
git commit -m "feat: refuse serial-based confirmation for blank or duplicate serials"
```

---

### Task 8: Capability probe for external tools

**Files:**
- Create: `src/corpsman/probe.py`
- Create: `tests/test_probe.py`

**Interfaces:**
- Consumes: `corpsman.run.run`.
- Produces:
  - `corpsman.probe.Probe` — class with `has(name: str) -> bool` and `missing() -> List[str]`. Constructor takes `runner=run` for injection.
  - `corpsman.probe.TOOLS` — tuple of tool names: `("smartctl", "nvme", "hdparm", "sg_sanitize", "sedutil-cli", "blkdiscard", "ddrescue")`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_probe.py
from corpsman.run import RunResult
from corpsman.probe import Probe, TOOLS


def fake_runner(present):
    def _run(argv, timeout=60):
        name = argv[0]
        if name in present:
            return RunResult(rc=0, out="/usr/sbin/" + name, err="", found=True)
        return RunResult(rc=127, out="", err="binary not found", found=False)
    return _run


def test_reports_present_tool():
    p = Probe(runner=fake_runner({"smartctl"}))
    assert p.has("smartctl") is True


def test_reports_absent_tool():
    p = Probe(runner=fake_runner({"smartctl"}))
    assert p.has("hdparm") is False


def test_missing_lists_absent_tools():
    p = Probe(runner=fake_runner({"smartctl"}))
    missing = p.missing()
    assert "hdparm" in missing
    assert "smartctl" not in missing


def test_probe_runs_each_tool_once():
    calls = []

    def counting(argv, timeout=60):
        calls.append(argv[0])
        return RunResult(rc=127, out="", err="", found=False)

    p = Probe(runner=counting)
    p.has("smartctl")
    p.has("smartctl")
    p.has("smartctl")
    assert calls.count("smartctl") == 1


def test_unknown_tool_is_false_not_an_error():
    p = Probe(runner=fake_runner(set()))
    assert p.has("definitely-not-a-tool") is False


def test_tools_tuple_is_stable():
    assert "smartctl" in TOOLS
    assert "nvme" in TOOLS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_probe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'corpsman.probe'`

- [ ] **Step 3: Write the implementation**

```python
# src/corpsman/probe.py
"""Runtime capability detection for optional external tools.

External binaries are accelerators, never requirements. Their absence
lowers what the tool can claim, never what it can safely do, so every one
is probed and the result reported rather than assumed.
"""
from .run import run

TOOLS = (
    "smartctl",
    "nvme",
    "hdparm",
    "sg_sanitize",
    "sedutil-cli",
    "blkdiscard",
    "ddrescue",
)


class Probe(object):
    def __init__(self, runner=run):
        self._runner = runner
        self._cache = {}

    def has(self, name):
        # type: (str) -> bool
        if name not in self._cache:
            # 'command -v' is not available as an executable; probe the
            # binary itself with a harmless argument instead.
            result = self._runner([name, "--version"], timeout=10)
            self._cache[name] = result.found
        return self._cache[name]

    def missing(self):
        # type: () -> list
        return [t for t in TOOLS if not self.has(t)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_probe.py -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add src/corpsman/probe.py tests/test_probe.py
git commit -m "feat: runtime capability probe for optional external tools"
```

---

### Task 9: SMART fixtures and smartctl JSON parsing

**Files:**
- Create: `tests/fixtures/smartctl/sata_healthy.json`
- Create: `tests/fixtures/smartctl/sata_pending_sectors.json`
- Create: `tests/fixtures/smartctl/sata_crc_only.json`
- Create: `src/corpsman/smart/__init__.py`
- Create: `src/corpsman/smart/parse.py`
- Create: `tests/test_smart_parse.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `corpsman.smart.parse.SmartData` — class with `available: bool`, `overall_passed: Optional[bool]`, `attrs: Dict[int, int]` (id to raw value), `power_on_hours: Optional[int]`, `source: str`, `unreadable_reason: Optional[str]`.
  - `corpsman.smart.parse.parse_smartctl_json(text: str) -> SmartData`.

- [ ] **Step 1: Write the fixtures**

```json
{
  "json_format_version": [1, 0],
  "smartctl": {"version": [7, 4], "exit_status": 0},
  "device": {"name": "/dev/sda", "type": "sat"},
  "model_name": "Samsung SSD 870 EVO 1TB",
  "serial_number": "S5Y2NJ0T304891",
  "smart_status": {"passed": true},
  "power_on_time": {"hours": 8112},
  "ata_smart_attributes": {
    "table": [
      {"id": 5,   "name": "Reallocated_Sector_Ct", "raw": {"value": 0}},
      {"id": 9,   "name": "Power_On_Hours",        "raw": {"value": 8112}},
      {"id": 187, "name": "Reported_Uncorrect",    "raw": {"value": 0}},
      {"id": 188, "name": "Command_Timeout",       "raw": {"value": 0}},
      {"id": 194, "name": "Temperature_Celsius",   "raw": {"value": 34}},
      {"id": 197, "name": "Current_Pending_Sector","raw": {"value": 0}},
      {"id": 198, "name": "Offline_Uncorrectable", "raw": {"value": 0}},
      {"id": 199, "name": "UDMA_CRC_Error_Count",  "raw": {"value": 0}}
    ]
  }
}
```
Save as `tests/fixtures/smartctl/sata_healthy.json`.

```json
{
  "json_format_version": [1, 0],
  "smartctl": {"version": [7, 4], "exit_status": 0},
  "device": {"name": "/dev/sdb", "type": "sat"},
  "model_name": "WDC WD40EFRX-68N32N0",
  "serial_number": "WD-WCC4E5RJ0K2P",
  "smart_status": {"passed": true},
  "power_on_time": {"hours": 41203},
  "ata_smart_attributes": {
    "table": [
      {"id": 5,   "name": "Reallocated_Sector_Ct", "raw": {"value": 128}},
      {"id": 9,   "name": "Power_On_Hours",        "raw": {"value": 41203}},
      {"id": 187, "name": "Reported_Uncorrect",    "raw": {"value": 12}},
      {"id": 188, "name": "Command_Timeout",       "raw": {"value": 0}},
      {"id": 194, "name": "Temperature_Celsius",   "raw": {"value": 41}},
      {"id": 197, "name": "Current_Pending_Sector","raw": {"value": 41}},
      {"id": 198, "name": "Offline_Uncorrectable", "raw": {"value": 8}},
      {"id": 199, "name": "UDMA_CRC_Error_Count",  "raw": {"value": 0}}
    ]
  }
}
```
Save as `tests/fixtures/smartctl/sata_pending_sectors.json`.

```json
{
  "json_format_version": [1, 0],
  "smartctl": {"version": [7, 4], "exit_status": 0},
  "device": {"name": "/dev/sdc", "type": "sat"},
  "model_name": "ST2000DM008-2FR102",
  "serial_number": "ZFL2AB3C",
  "smart_status": {"passed": true},
  "power_on_time": {"hours": 900},
  "ata_smart_attributes": {
    "table": [
      {"id": 5,   "name": "Reallocated_Sector_Ct", "raw": {"value": 0}},
      {"id": 9,   "name": "Power_On_Hours",        "raw": {"value": 900}},
      {"id": 187, "name": "Reported_Uncorrect",    "raw": {"value": 0}},
      {"id": 188, "name": "Command_Timeout",       "raw": {"value": 0}},
      {"id": 194, "name": "Temperature_Celsius",   "raw": {"value": 30}},
      {"id": 197, "name": "Current_Pending_Sector","raw": {"value": 0}},
      {"id": 198, "name": "Offline_Uncorrectable", "raw": {"value": 0}},
      {"id": 199, "name": "UDMA_CRC_Error_Count",  "raw": {"value": 4831}}
    ]
  }
}
```
Save as `tests/fixtures/smartctl/sata_crc_only.json`.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_smart_parse.py
import os
from corpsman.smart.parse import parse_smartctl_json

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "smartctl")


def load(name):
    with open(os.path.join(FIX, name)) as f:
        return f.read()


def test_healthy_disk_parses():
    s = parse_smartctl_json(load("sata_healthy.json"))
    assert s.available is True
    assert s.overall_passed is True
    assert s.attrs[5] == 0
    assert s.attrs[197] == 0
    assert s.power_on_hours == 8112


def test_failing_disk_attributes_parse():
    s = parse_smartctl_json(load("sata_pending_sectors.json"))
    assert s.attrs[5] == 128
    assert s.attrs[197] == 41
    assert s.attrs[198] == 8
    assert s.attrs[187] == 12


def test_crc_attribute_is_parsed_but_kept_separate():
    s = parse_smartctl_json(load("sata_crc_only.json"))
    assert s.attrs[199] == 4831


def test_garbage_input_is_unavailable_not_an_exception():
    s = parse_smartctl_json("this is not json")
    assert s.available is False
    assert s.unreadable_reason is not None


def test_empty_input_is_unavailable():
    s = parse_smartctl_json("")
    assert s.available is False


def test_json_without_attribute_table_is_unavailable():
    s = parse_smartctl_json('{"smartctl": {"version": [7, 4]}}')
    assert s.available is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m pytest tests/test_smart_parse.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'corpsman.smart'`

- [ ] **Step 4: Write the implementation**

```python
# src/corpsman/smart/__init__.py
"""SMART acquisition and health verdict."""
```

```python
# src/corpsman/smart/parse.py
"""Parse smartctl --json output.

JSON is used rather than text because smartctl's human output has changed
shape repeatedly across releases and is locale-sensitive. Raw attribute
values are vendor-opaque in general; only the small set with reliable
cross-vendor semantics is consumed by the verdict, and anything else is
carried through for display without interpretation.
"""
import json


class SmartData(object):
    __slots__ = ("available", "overall_passed", "attrs", "power_on_hours",
                 "source", "unreadable_reason", "model", "serial")

    def __init__(self, available=False, overall_passed=None, attrs=None,
                 power_on_hours=None, source="smartctl",
                 unreadable_reason=None, model=None, serial=None):
        self.available = available
        self.overall_passed = overall_passed
        self.attrs = attrs if attrs is not None else {}
        self.power_on_hours = power_on_hours
        self.source = source
        self.unreadable_reason = unreadable_reason
        self.model = model
        self.serial = serial


def parse_smartctl_json(text):
    # type: (str) -> SmartData
    if not text or not text.strip():
        return SmartData(unreadable_reason="no output from smartctl")
    try:
        doc = json.loads(text)
    except ValueError as exc:
        return SmartData(unreadable_reason="unparseable smartctl output: %s" % exc)
    if not isinstance(doc, dict):
        return SmartData(unreadable_reason="unexpected smartctl document shape")

    table = (doc.get("ata_smart_attributes") or {}).get("table")
    if not isinstance(table, list) or not table:
        return SmartData(unreadable_reason="no ATA SMART attribute table present")

    attrs = {}
    for row in table:
        if not isinstance(row, dict):
            continue
        aid = row.get("id")
        raw = (row.get("raw") or {}).get("value")
        if isinstance(aid, int) and isinstance(raw, int):
            attrs[aid] = raw

    status = doc.get("smart_status")
    passed = status.get("passed") if isinstance(status, dict) else None
    hours = (doc.get("power_on_time") or {}).get("hours")

    return SmartData(
        available=True,
        overall_passed=passed if isinstance(passed, bool) else None,
        attrs=attrs,
        power_on_hours=hours if isinstance(hours, int) else None,
        model=doc.get("model_name"),
        serial=doc.get("serial_number"),
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_smart_parse.py -v`
Expected: PASS, 6 tests

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/smartctl/ src/corpsman/smart/ tests/test_smart_parse.py
git commit -m "feat: parse smartctl --json into SmartData"
```

---

### Task 10: Health verdict with thresholds and the cabling split

**Files:**
- Create: `src/corpsman/smart/verdict.py`
- Create: `tests/test_smart_verdict.py`

**Interfaces:**
- Consumes: `corpsman.smart.parse.SmartData`, health constants from `corpsman.types`.
- Produces:
  - `corpsman.smart.verdict.Verdict` — class with `health: str`, `reasons: List[str]`, `cabling: Optional[str]`.
  - `corpsman.smart.verdict.assess(smart: SmartData, prior: Optional[dict] = None) -> Verdict`. `prior` is a mapping of attribute id to the previously recorded raw value.
  - `corpsman.smart.verdict.THRESHOLDS` — dict mapping attribute id to the count at or above which the drive is `SCRAP`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_smart_verdict.py
from corpsman.smart.parse import SmartData
from corpsman.smart.verdict import assess
from corpsman.types import (
    HEALTH_REUSE, HEALTH_SCRATCH_ONLY, HEALTH_SCRAP, HEALTH_UNKNOWN,
)


def sd(**attrs):
    base = {5: 0, 187: 0, 188: 0, 197: 0, 198: 0, 199: 0}
    base.update(attrs)
    return SmartData(available=True, overall_passed=True, attrs=base,
                     power_on_hours=1000)


def test_clean_drive_is_reuse():
    assert assess(sd()).health == HEALTH_REUSE


def test_unavailable_smart_is_unknown_never_reuse():
    v = assess(SmartData(available=False, unreadable_reason="USB bridge"))
    assert v.health == HEALTH_UNKNOWN
    assert v.health != HEALTH_REUSE


def test_failed_overall_status_is_scrap():
    s = sd()
    s.overall_passed = False
    assert assess(s).health == HEALTH_SCRAP


def test_a_few_reallocated_sectors_is_not_scrap():
    # A 10 TB drive that remapped three sectors in year one and none since
    # is a working drive. Condemning it would bin most used inventory.
    assert assess(sd(**{5: 3})).health == HEALTH_SCRATCH_ONLY


def test_many_reallocated_sectors_is_scrap():
    assert assess(sd(**{5: 128})).health == HEALTH_SCRAP


def test_any_pending_sectors_growing_is_scrap():
    prior = {5: 0, 197: 4}
    assert assess(sd(**{197: 41}), prior=prior).health == HEALTH_SCRAP


def test_static_low_pending_is_scratch_only():
    prior = {197: 2}
    assert assess(sd(**{197: 2}), prior=prior).health == HEALTH_SCRATCH_ONLY


def test_crc_errors_alone_do_not_condemn_the_drive():
    # Attribute 199 is an interface fault. Counting it against the drive is
    # how a good disk gets binned while the bad cable stays in the machine.
    v = assess(sd(**{199: 4831}))
    assert v.health == HEALTH_REUSE
    assert v.cabling is not None
    assert "199" in v.cabling or "CRC" in v.cabling


def test_reported_uncorrect_is_scratch_only():
    assert assess(sd(**{187: 12})).health == HEALTH_SCRATCH_ONLY


def test_reasons_name_the_attributes_that_fired():
    v = assess(sd(**{5: 128}))
    assert any("5" in r for r in v.reasons)


def test_offline_uncorrectable_above_threshold_is_scrap():
    assert assess(sd(**{198: 20})).health == HEALTH_SCRAP
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_smart_verdict.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'corpsman.smart.verdict'`

- [ ] **Step 3: Write the implementation**

```python
# src/corpsman/smart/verdict.py
"""Health verdict from SMART attributes.

Weighted toward the five attributes Backblaze fleet data found carry nearly
all the predictive signal: 5, 187, 188, 197, 198.

Thresholds and rate of change, not nonzero. A drive that remapped a handful
of sectors years ago and none since is a working drive, and a rule that
condemns any nonzero count would bin most of the used inventory that
crosses a bench.

Attribute 199 is excluded from the drive verdict entirely and reported as a
cabling fault, because UDMA CRC errors are interface errors -- a marginal
SATA cable, backplane or USB bridge. Blaming the drive is how a good disk
gets binned while the faulty cable stays in the machine and kills the next
one.
"""
from ..types import (
    HEALTH_REUSE, HEALTH_SCRATCH_ONLY, HEALTH_SCRAP, HEALTH_UNKNOWN,
)

# At or above these raw counts the drive is SCRAP.
THRESHOLDS = {
    5: 64,     # Reallocated_Sector_Ct
    197: 16,   # Current_Pending_Sector
    198: 16,   # Offline_Uncorrectable
}

# Above zero but below THRESHOLDS puts the drive in SCRATCH_ONLY.
_WATCH = (5, 187, 188, 197, 198)

_NAMES = {
    5: "Reallocated_Sector_Ct",
    187: "Reported_Uncorrect",
    188: "Command_Timeout",
    197: "Current_Pending_Sector",
    198: "Offline_Uncorrectable",
    199: "UDMA_CRC_Error_Count",
}


class Verdict(object):
    __slots__ = ("health", "reasons", "cabling")

    def __init__(self, health, reasons=None, cabling=None):
        self.health = health
        self.reasons = reasons if reasons is not None else []
        self.cabling = cabling


def _label(aid, value):
    return "%d %s = %d" % (aid, _NAMES.get(aid, "attr"), value)


def assess(smart, prior=None):
    # type: (object, dict) -> Verdict
    if not smart.available:
        reason = smart.unreadable_reason or "SMART could not be read"
        # Unreadable is never the same as healthy.
        return Verdict(HEALTH_UNKNOWN, ["SMART unavailable: " + reason])

    reasons = []
    cabling = None

    crc = smart.attrs.get(199, 0)
    if crc:
        cabling = (
            "%s -- interface fault, not the drive. Check the SATA cable, "
            "backplane or USB bridge before condemning this disk."
            % _label(199, crc)
        )

    if smart.overall_passed is False:
        reasons.append("SMART overall self-assessment FAILED")
        return Verdict(HEALTH_SCRAP, reasons, cabling)

    scrap = False
    watch = False

    for aid in _WATCH:
        value = smart.attrs.get(aid, 0)
        if not value:
            continue
        threshold = THRESHOLDS.get(aid)
        if threshold is not None and value >= threshold:
            reasons.append("%s (threshold %d)" % (_label(aid, value), threshold))
            scrap = True
            continue
        # Rate of change matters more than magnitude. A pending count that
        # climbed since the last inspection is worse than a larger static one.
        if prior is not None and aid in prior and value > prior[aid]:
            reasons.append(
                "%s, up from %d since last inspection" % (_label(aid, value), prior[aid])
            )
            scrap = True
            continue
        reasons.append(_label(aid, value))
        watch = True

    if scrap:
        return Verdict(HEALTH_SCRAP, reasons, cabling)
    if watch:
        return Verdict(HEALTH_SCRATCH_ONLY, reasons, cabling)
    return Verdict(HEALTH_REUSE, ["no predictive attributes above zero"], cabling)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_smart_verdict.py -v`
Expected: PASS, 11 tests

- [ ] **Step 5: Commit**

```bash
git add src/corpsman/smart/verdict.py tests/test_smart_verdict.py
git commit -m "feat: health verdict on thresholds and trend, with 199 as a cabling fault"
```

---

### Task 11: `doc inspect` command with human and JSON output

**Files:**
- Create: `src/corpsman/smart/collect.py`
- Create: `src/corpsman/cli.py`
- Create: `tests/test_cli_inspect.py`

**Interfaces:**
- Consumes: everything from Tasks 1–10.
- Produces:
  - `corpsman.smart.collect.collect(device, probe, runner=run) -> SmartData`.
  - `corpsman.cli.main(argv=None) -> int` — exit codes `0` healthy, `1` warning, `2` critical, `3` unknown or unsupported.
  - `corpsman.cli.build_report(devices, sysmap, smart_by_name) -> dict` — the `--json` document.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_inspect.py
import json
import os
from corpsman.cli import build_report
from corpsman.identity.linux import enumerate_devices
from corpsman.smart.parse import parse_smartctl_json, SmartData
from corpsman.topology.linux import system_devices

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
LUKS = os.path.join(FIX, "linux", "luks-lvm")


def smart(name):
    with open(os.path.join(FIX, "smartctl", name)) as f:
        return parse_smartctl_json(f.read())


def test_report_marks_system_device():
    devs = enumerate_devices(root=LUKS)
    sysmap = system_devices(root=LUKS)
    rep = build_report(devs, sysmap, {d.name: smart("sata_healthy.json") for d in devs})
    sda = [d for d in rep["devices"] if d["name"] == "sda"][0]
    assert sda["system_state"] != []
    assert "/" in sda["system_state"]


def test_report_does_not_mark_unrelated_device():
    devs = enumerate_devices(root=LUKS)
    sysmap = system_devices(root=LUKS)
    rep = build_report(devs, sysmap, {d.name: smart("sata_healthy.json") for d in devs})
    sdb = [d for d in rep["devices"] if d["name"] == "sdb"][0]
    assert sdb["system_state"] == []


def test_report_is_json_serialisable():
    devs = enumerate_devices(root=LUKS)
    rep = build_report(devs, system_devices(root=LUKS),
                       {d.name: smart("sata_healthy.json") for d in devs})
    json.dumps(rep)


def test_report_uses_plain_enum_values_not_flavor():
    devs = enumerate_devices(root=LUKS)
    rep = build_report(devs, system_devices(root=LUKS),
                       {d.name: smart("sata_pending_sectors.json") for d in devs})
    blob = json.dumps(rep)
    for flavor in ("expectant", "walking wounded", "CORPSMAN UP", "return to duty"):
        assert flavor not in blob


def test_unavailable_smart_reports_unknown_in_json():
    devs = enumerate_devices(root=LUKS)
    rep = build_report(devs, system_devices(root=LUKS),
                       {d.name: SmartData(available=False, unreadable_reason="bridge")
                        for d in devs})
    assert all(d["health"] == "UNKNOWN" for d in rep["devices"])


def test_report_includes_identity_token_and_confirm_string():
    devs = enumerate_devices(root=LUKS)
    rep = build_report(devs, system_devices(root=LUKS),
                       {d.name: smart("sata_healthy.json") for d in devs})
    for d in rep["devices"]:
        assert len(d["identity_token"]) == 12
        assert d["confirm"]


def test_schema_version_present():
    rep = build_report([], {}, {})
    assert rep["schema"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_cli_inspect.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'corpsman.cli'`

- [ ] **Step 3: Write the SMART collector**

```python
# src/corpsman/smart/collect.py
"""Acquire SMART for a device via smartctl, degrading honestly."""
from .parse import SmartData, parse_smartctl_json
from ..run import run


def collect(device, probe, runner=run):
    # type: (object, object, object) -> SmartData
    if not probe.has("smartctl"):
        return SmartData(
            unreadable_reason="smartctl not installed (install smartmontools)"
        )
    args = ["smartctl", "--json", "-a", device.path]
    if device.bus == "usb":
        # Many USB bridges need an explicit translation layer, and many pass
        # nothing through at all. Failure here reports UNKNOWN, never healthy.
        args = ["smartctl", "--json", "-a", "-d", "sat", device.path]
    result = runner(args, timeout=30)
    if not result.found:
        return SmartData(unreadable_reason="smartctl disappeared between probe and run")
    data = parse_smartctl_json(result.out)
    if not data.available and device.bus == "usb":
        data.unreadable_reason = (
            "USB bridge did not pass SMART through: %s" % data.unreadable_reason
        )
    return data
```

- [ ] **Step 4: Write the CLI**

```python
# src/corpsman/cli.py
"""Command line entry point.

Phase 1 is read-only. There is no destructive subcommand in this tree.
"""
import argparse
import json
import sys

from . import platform_
from .identity import linux as identity_linux
from .identity.collisions import confirm_string
from .probe import Probe
from .smart.collect import collect
from .smart.verdict import assess
from .topology import linux as topology_linux
from .types import HEALTH_REUSE, HEALTH_SCRATCH_ONLY, HEALTH_SCRAP

SCHEMA = 1

_EXIT = {
    HEALTH_REUSE: 0,
    HEALTH_SCRATCH_ONLY: 1,
    HEALTH_SCRAP: 2,
}

# Flavor is terminal-only. It never reaches --json, the ledger, or a
# customer-facing record.
_TRIAGE = {
    HEALTH_REUSE: "return to duty",
    HEALTH_SCRATCH_ONLY: "walking wounded",
    HEALTH_SCRAP: "expectant",
    "UNKNOWN": "unable to assess",
}


def build_report(devices, sysmap, smart_by_name):
    # type: (list, dict, dict) -> dict
    out = []
    for d in devices:
        s = smart_by_name.get(d.name)
        v = assess(s) if s is not None else None
        out.append({
            "name": d.name,
            "path": d.path,
            "identity_token": d.identity_token,
            "confirm": confirm_string(devices, d),
            "model": d.model,
            "serial": d.serial,
            "wwn": d.wwn,
            "size_bytes": d.size_bytes,
            "logical_sector": d.logical_sector,
            "physical_sector": d.physical_sector,
            "bus": d.bus,
            "rotational": d.rotational,
            "removable": d.removable,
            "system_state": sysmap.get(d.name, []),
            "health": v.health if v else "UNKNOWN",
            "reasons": v.reasons if v else ["not assessed"],
            "cabling": v.cabling if v else None,
        })
    return {"schema": SCHEMA, "devices": out}


def _human(report, stream):
    for d in report["devices"]:
        gb = d["size_bytes"] / 1000.0 ** 3
        flag = ""
        if d["system_state"]:
            flag = "  [SYSTEM: %s]" % ", ".join(d["system_state"])
        stream.write("%s  %.1f GB  %s  #%s%s\n"
                     % (d["path"], gb, d["model"] or "unknown",
                        d["serial"] or d["identity_token"], flag))
        if d["health"] == HEALTH_SCRAP:
            stream.write("  ** CORPSMAN UP **\n")
        stream.write("  %s (%s)\n" % (d["health"], _TRIAGE.get(d["health"], "")))
        for r in d["reasons"]:
            stream.write("    %s\n" % r)
        if d["cabling"]:
            stream.write("    CABLING: %s\n" % d["cabling"])
        stream.write("\n")


def cmd_inspect(args, stream=sys.stdout):
    if not platform_.has_backend():
        stream.write("corpsman has no backend for this platform (%s); "
                     "refusing to guess device conventions\n" % platform_.detect())
        return 3
    if not platform_.is_privileged():
        stream.write("corpsman needs root to read device metadata. Running "
                     "unprivileged returns partial data, and a verdict built "
                     "on partial data is worse than no verdict.\n")
        return 3

    devices = identity_linux.enumerate_devices(root=args.root)
    if args.device:
        devices = [d for d in devices if d.path == args.device or d.name == args.device]
    sysmap = topology_linux.system_devices(root=args.root)
    probe = Probe()
    smart_by_name = dict((d.name, collect(d, probe)) for d in devices)
    report = build_report(devices, sysmap, smart_by_name)

    if args.json:
        stream.write(json.dumps(report, indent=2) + "\n")
    else:
        _human(report, stream)

    worst = 0
    for d in report["devices"]:
        worst = max(worst, _EXIT.get(d["health"], 3))
    return worst


def main(argv=None):
    parser = argparse.ArgumentParser(prog="doc", description="drive doctor")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("inspect", help="identity, SMART, health verdict")
    p.add_argument("device", nargs="?", help="device path, omit for all")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--root", default="/", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.command == "inspect":
        return cmd_inspect(args)
    parser.print_help()
    return 0
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_cli_inspect.py -v`
Expected: PASS, 7 tests

- [ ] **Step 6: Run the whole suite**

Run: `python3 -m pytest tests/ -v`
Expected: PASS, all tests from Tasks 1–11

- [ ] **Step 7: Commit**

```bash
git add src/corpsman/smart/collect.py src/corpsman/cli.py tests/test_cli_inspect.py
git commit -m "feat: doc inspect with human and JSON output"
```

---

### Task 12: Guard tests — no runtime dependencies, no write paths

**Files:**
- Create: `tests/test_guards.py`

**Interfaces:**
- Consumes: the whole `src/corpsman` tree as text.
- Produces: nothing. These tests exist to make two global constraints unbreakable by a future change.

- [ ] **Step 1: Write the tests**

```python
# tests/test_guards.py
"""Guards on the two global constraints that must never regress."""
import ast
import os

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "src", "corpsman")

STDLIB_OK = {
    "argparse", "ast", "hashlib", "json", "os", "re", "subprocess", "sys",
    "time", "typing", "collections", "errno", "stat", "struct", "datetime",
}


def _python_files():
    for dirpath, _dirs, files in os.walk(SRC):
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def test_no_third_party_runtime_imports():
    """The zero-dependency guarantee is what lets this run from a rescue USB."""
    offenders = []
    for path in _python_files():
        with open(path) as f:
            tree = ast.parse(f.read(), path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root not in STDLIB_OK:
                        offenders.append((path, root))
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative import, ours
                    continue
                root = (node.module or "").split(".")[0]
                if root and root not in STDLIB_OK:
                    offenders.append((path, root))
    assert offenders == [], "third-party imports found: %r" % offenders


def test_no_device_write_paths_in_phase_one():
    """Phase 1 is read-only. Nothing here may open a device for writing."""
    banned = ("O_WRONLY", "O_RDWR", "O_TRUNC", "O_CREAT")
    offenders = []
    for path in _python_files():
        with open(path) as f:
            text = f.read()
        for token in banned:
            if token in text:
                offenders.append((path, token))
    assert offenders == [], "write flags found in read-only phase: %r" % offenders


def test_flavor_strings_are_not_in_non_cli_modules():
    """Corpsman voice belongs in terminal output only, never in data paths."""
    flavor = ("CORPSMAN UP", "expectant", "walking wounded", "DEVIL DOC")
    offenders = []
    for path in _python_files():
        if os.path.basename(path) == "cli.py":
            continue
        with open(path) as f:
            text = f.read()
        for word in flavor:
            if word in text:
                offenders.append((path, word))
    assert offenders == [], "flavor text outside cli.py: %r" % offenders
```

- [ ] **Step 2: Run the tests**

Run: `python3 -m pytest tests/test_guards.py -v`
Expected: PASS, 3 tests

- [ ] **Step 3: Verify the guard actually catches a violation**

Temporarily add `import requests` to the top of `src/corpsman/probe.py`, then run:

Run: `python3 -m pytest tests/test_guards.py::test_no_third_party_runtime_imports -v`
Expected: FAIL, naming `probe.py` and `requests`

Remove the line and re-run to confirm PASS. A guard that cannot fail is not a guard.

- [ ] **Step 4: Commit**

```bash
git add tests/test_guards.py
git commit -m "test: guard zero-dependency and read-only invariants"
```

---

### Task 13: Single-file build artifact

**Files:**
- Create: `build.py`
- Create: `tests/test_build.py`

**Interfaces:**
- Consumes: the `src/corpsman` package.
- Produces: `build.build(out_path: str) -> str` writing a self-contained `doc` script, and `python3 build.py` as a CLI.

The single file is a distribution artifact, not the source layout. Source stays one module per layer so an edit to SMART parsing cannot sit in the same file as device targeting.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_build.py
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import build  # noqa: E402


def test_build_produces_a_runnable_file():
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "doc")
        build.build(out)
        assert os.path.exists(out)
        r = subprocess.run([sys.executable, out, "--help"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert r.returncode == 0
        assert b"inspect" in r.stdout


def test_built_file_has_no_corpsman_imports_left():
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "doc")
        build.build(out)
        with open(out) as f:
            text = f.read()
        assert "from corpsman" not in text
        assert "import corpsman" not in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_build.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'build'`

- [ ] **Step 3: Write the implementation**

```python
#!/usr/bin/env python3
"""Concatenate the corpsman package into a single runnable script.

The one-file property is what lets an operator copy 'doc' to a rescue USB
and run it offline. It is not a reason to maintain the source as a monolith:
at this scope a single file would put SMART parsing in the same edit surface
as device targeting, which is the wrong trade for a tool whose failure modes
are destructive.
"""
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src", "corpsman")

# Dependency order. A module may only use names defined above it.
ORDER = [
    "run.py",
    "platform_.py",
    "types.py",
    "probe.py",
    "identity/linux.py",
    "identity/collisions.py",
    "topology/linux.py",
    "smart/parse.py",
    "smart/verdict.py",
    "smart/collect.py",
    "cli.py",
]

HEADER = '''#!/usr/bin/env python3
"""corpsman - drive doctor. Generated single-file build; edit src/ instead."""
'''

_IMPORT_RE = re.compile(r"^\s*from\s+\.+[\w.]*\s+import\s+.*$|^\s*from\s+\.+\s+import\s+.*$")


def _strip_internal_imports(text):
    out = []
    for line in text.splitlines():
        if _IMPORT_RE.match(line):
            continue
        out.append(line)
    return "\n".join(out)


def build(out_path):
    # type: (str) -> str
    chunks = [HEADER]
    for rel in ORDER:
        path = os.path.join(SRC, rel)
        with open(path) as f:
            body = f.read()
        chunks.append("\n# --- %s ---\n" % rel)
        chunks.append(_strip_internal_imports(body))
    chunks.append('\n\nif __name__ == "__main__":\n    import sys\n    sys.exit(main())\n')
    text = "\n".join(chunks)
    with open(out_path, "w") as f:
        f.write(text)
    os.chmod(out_path, 0o755)
    return out_path


if __name__ == "__main__":
    print(build(os.path.join(ROOT, "doc")))
```

Note: `cli.py` references modules by name (`identity_linux.enumerate_devices`). For the concatenated build those qualified names must resolve. Add these aliases at the end of `build.py`'s `ORDER` processing by appending a shim chunk before the `__main__` block:

```python
SHIM = '''

class _Mod(object):
    pass

identity_linux = _Mod()
identity_linux.enumerate_devices = enumerate_devices
topology_linux = _Mod()
topology_linux.system_devices = system_devices
platform_ = _Mod()
platform_.detect = detect
platform_.has_backend = has_backend
platform_.is_privileged = is_privileged
'''
```

Insert `chunks.append(SHIM)` immediately before the `__main__` chunk in `build()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_build.py -v`
Expected: PASS, 2 tests

- [ ] **Step 5: Build and smoke-test by hand**

Run: `python3 build.py && ./doc --help && ./doc inspect --help`
Expected: help output listing `inspect`, exit 0

- [ ] **Step 6: Commit**

```bash
git add build.py tests/test_build.py
git commit -m "feat: single-file build artifact from per-layer source modules"
```

---

### Task 14: README status update and real-hardware smoke check

**Files:**
- Modify: `README.md` (status block near the top)
- Create: `docs/PHASE1-SMOKE.md`

**Interfaces:**
- Consumes: the built `doc` script.
- Produces: a documented manual verification procedure. Phase 1 is read-only, so this is safe to run on any machine.

- [ ] **Step 1: Write the smoke procedure**

```markdown
# Phase 1 smoke check

Phase 1 is read-only. Nothing here writes to a device. Safe on a live machine.

## 1. Build and run

    python3 build.py
    sudo ./doc inspect

Expect one block per whole disk. Partitions must not appear as devices.

## 2. The system-disk check is the one that matters

Confirm your boot disk is marked `[SYSTEM: /]` — and on a LUKS or LVM
machine, confirm the mark lands on the *physical* disk (`sda`, `nvme0n1`),
not only on the mapper device.

If your boot disk is NOT flagged, stop. That is the bug this phase exists to
prevent, and no destructive code may be written until it is fixed.

## 3. Sizes

Cross-check against `lsblk -b -d -o NAME,SIZE`. A 4Kn drive reported at
eight times its true size means the 512-byte sector-unit rule was broken.

## 4. SMART degradation is honest

    sudo ./doc inspect --json | python3 -m json.tool | grep -A2 '"health"'

Unplug smartctl (`sudo mv /usr/sbin/smartctl /usr/sbin/smartctl.bak`) and
re-run. Every device must report `UNKNOWN` with a reason naming the missing
tool — never `REUSE`. Restore it afterwards.

## 5. USB bridge behaviour

Attach a USB drive. If the bridge does not pass SMART through, it must
report `UNKNOWN`, never `REUSE`.

## 6. Exit codes

    sudo ./doc inspect >/dev/null; echo $?

`0` all healthy, `1` a warning drive present, `2` a scrap drive present,
`3` unknown or unprivileged. Run unprivileged and confirm `3` plus a refusal
message rather than partial data.
```

- [ ] **Step 2: Update the README status block**

Replace the existing status block in `README.md` with:

```markdown
> **Status: Phase 1 — Linux `inspect` only.** Device identity, topology, SMART,
> and health verdicts work on Linux and are covered by fixture-based tests.
> macOS and Windows backends are not implemented and are refused rather than
> guessed at. No destructive subcommand exists in the tree yet — `wipe`,
> `clone`, `restore`, and `recover` are designed but unbuilt.
```

- [ ] **Step 3: Run the whole suite one final time**

Run: `python3 -m pytest tests/ -v`
Expected: PASS, all tests

- [ ] **Step 4: Run the smoke check on the workstation**

Run: `python3 build.py && sudo ./doc inspect`
Expected: real devices listed, boot disk flagged `[SYSTEM: /]`

- [ ] **Step 5: Commit**

```bash
git add README.md docs/PHASE1-SMOKE.md
git commit -m "docs: phase 1 status and read-only smoke procedure"
```

---

## What this phase deliberately does not build

- **macOS and Windows backends.** Each is a task set of its own. The platform gate refuses them cleanly, which is the specified behaviour, not a stub.
- **Any destructive operation.** `wipe`, `clone`, `restore`, `recover parts --repair`. These need arming, device locking, signal handling, and the ledger, and they get their own plan once this phase's targeting is proven on real hardware.
- **The TUI.** It is built on top of commands that already work. Phase 1 is CLI only.
- **The ledger.** Its first real consumer is `wipe`. Building it here would mean designing its schema against a command that does not need it.
- **`test`, `image`, MCP, RMM mode.** All downstream of this foundation.
- **Floppy, optical, and tape enumeration.** Task 4's `_SKIP_PREFIXES` excludes `sr` and
  `fd`. The parent design promises these media, and they will be enumerated when the
  media-class layer lands — they carry no SMART, so including them in a SMART-driven
  `inspect` would produce a list of devices that can only ever report `UNKNOWN`. This is a
  deliberate deferral, not an oversight; the exclusion list is the single line to change.

## Self-review notes

- Spec coverage for this phase: composite identity token (Task 2), blank and duplicate serial refusal (Task 7), full dependency-chain resolution (Task 6), capability probing (Task 8), locale pinning (Task 1), platform gate (Task 1), privilege refusal rather than degradation (Task 11), SMART via JSON with honest unavailability (Tasks 9, 11), thresholds and trend (Task 10), attribute 199 as cabling (Task 10), flavor confined to terminal output (Tasks 11, 12), RMM exit codes and versioned schema (Task 11), single-file build from modular source (Task 13).
- Trend comparison in Task 10 accepts a `prior` mapping but nothing supplies one yet, because prior readings come from the ledger, which is Phase 2. `assess()` is written to take it now so the signature does not change later; `inspect` passes `None` and the verdict falls back to thresholds alone, which Task 10's tests cover.
- Names used consistently across tasks: `enumerate_devices`, `system_devices`, `identity_token`, `confirm_string`, `assess`, `collect`, `build_report`.
