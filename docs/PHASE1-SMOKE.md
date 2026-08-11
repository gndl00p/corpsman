# Phase 1 smoke check

Phase 1 is read-only. No code path under `src/corpsman` opens a device for
writing — `tests/test_guards.py::test_no_device_write_paths_in_phase_one`
checks for it on every run. **This whole procedure is safe to run on a live
machine, including the one you're using right now.**

What that guard actually is, so you don't over-trust it: it's a substring
scan for write-mode flags (`O_WRONLY`, `O_RDWR`, ...) and destructive
binaries (`dd`, `wipefs`, `mkfs`, ...) across `src/corpsman/*.py`. It catches
the obvious mistake — someone typing one of those tokens into this tree — and
nothing more subtle. It is not a sandbox, not a kernel-level guarantee, and
not a proof that this code cannot write to a device; it is a tripwire against
one class of mistake in a tree that, as of Phase 1, has no destructive
subcommand to trip it in the first place.

What this procedure does NOT prove:

- Nothing about `wipe`, `clone`, `restore`, or `recover parts --repair` —
  none of them exist in this tree yet. There's nothing to smoke-test.
- Nothing about macOS or Windows. Both platforms are refused outright
  (`corpsman has no backend for this platform`); there's no backend to
  exercise.
- Steps run with the hidden `--root` flag against a fixture prove that
  parsing and topology resolution work against a known filesystem layout.
  They prove **nothing** about real device enumeration on your actual
  hardware — for that you need the unprefixed, unprivileged-then-privileged
  runs in steps 2 and 4.

## 0. What exists right now

The only command is `doc inspect [device] [--json]`. There is no `test`,
`image`, `clone`, `wipe`, `recover`, `ledger`, or `serve-mcp` — those are
designed in `docs/superpowers/specs/` and unbuilt. `device` is a positional
argument (`doc inspect sda`, not `doc inspect --device sda`); omit it to
inspect every disk found.

## 1. Build

    python3 build.py

This produces `./doc`, a single-file script assembled from `src/corpsman/`.
It's a build artifact, gitignored — never commit it, and rebuild it after
any source change before smoke-testing again.

## 2. Run unprivileged first — confirm the refusal

    ./doc inspect; echo "exit: $?"

Expect a refusal (`corpsman needs root to read device metadata...`) and
**exit 3**, with no device data printed. Reading device metadata without
root returns partial data on Linux, and this tool would rather refuse than
hand back a verdict built on a partial picture.

## 3. Build-vs-source parity (the build step is new; nothing else has proven it)

The build concatenates `src/corpsman/*.py` into `doc` through a hand-rolled
module loader (see `build.py`'s docstring). The only thing that proves the
artifact still behaves like the source it was built from is comparing their
output on the same input:

    .venv/bin/python -m pytest tests/test_build.py -v

`test_built_file_matches_source_package_report` is the one that matters
here: it loads the freshly built `doc`, calls its `cmd_inspect` against
`tests/fixtures/linux/luks-lvm` via the hidden `--root` flag, and diffs the
resulting JSON against the same call made directly against `src/corpsman`.

You can't reproduce this by hand from the command line, because `--root`
only swaps which path stands in for `/` when reading `/sys` and `/proc` — it
does **not** bypass the privilege gate in step 2. Without real root, `./doc
inspect --root tests/fixtures/linux/luks-lvm` still refuses with exit 3.
That's expected; it's why the test above loads the module directly and
monkeypatches the privilege check instead of shelling out, same as
`tests/test_cli_inspect.py` does for the source package.

## 4. `sudo ./doc inspect` — the real run

    sudo ./doc inspect

Expect one block per whole disk. **Partitions must not appear as devices.**

## 5. The system-disk check is the one that matters

**First, check what filesystem `/` actually is:**

    findmnt -no FSTYPE /

If this reports `btrfs` or `zfs`, **the system-state flag cannot currently be
trusted on this host.** Phase 1's topology resolver walks
`/sys/block/*/slaves`, which covers LVM, LUKS, mdraid, and other
device-mapper stacks — but btrfs and ZFS allocate anonymous superblock
devices (major 0) rather than a real block device, and multi-device
btrfs/ZFS expose no `slaves` links either. On a btrfs- or ZFS-root host —
the Fedora and openSUSE default — the boot disk can plausibly show no
`[SYSTEM]` flag at all. Do not treat an unflagged boot disk as proof of
safety on such a host; this gap must close before any destructive phase.

On an ext4/xfs root on LVM/LUKS/mdraid, confirm your boot disk is marked
`[SYSTEM: /]` — and on a LUKS or LVM machine, confirm the mark lands on the
*physical* disk (`sda`, `nvme0n1`), not only on the mapper device
(`/dev/mapper/vg-root`, `/dev/dm-0`).

If your boot disk is NOT flagged and `findmnt` reported ext4/xfs/etc. (not
btrfs/zfs), stop. That is the bug this phase exists to prevent, and no
destructive code may be written until it is fixed.

## 6. Sizes

Cross-check against `lsblk -b -d -o NAME,SIZE`. A 4Kn drive reported at
eight times its true size means the 512-byte sector-unit rule was broken.

## 7. SMART degradation is honest

    sudo ./doc inspect --json | python3 -m json.tool | grep -A2 '"health"'

Unplug smartctl (`sudo mv /usr/sbin/smartctl /usr/sbin/smartctl.bak`) and
re-run. Every device must report `UNKNOWN` with a reason naming the missing
tool — never `REUSE`. Restore it afterwards.

(If smartctl was never installed to begin with, you'll see this for free —
every device reports `UNKNOWN`, reason `smartctl not installed`. That is
correct behavior, not a broken test environment.)

## 8. USB bridge behavior

Attach a USB drive. If the bridge does not pass SMART through, it must
report `UNKNOWN`, never `REUSE`.

## 9. No-devices-found refuses, it does not report "healthy"

    sudo ./doc inspect this-device-does-not-exist; echo "exit: $?"

Expect `no device matched 'this-device-does-not-exist'; nothing inspected`
and **exit 3** — never exit 0. "Nothing was inspected" must never read as
"everything is healthy."

## 10. Exit codes

    sudo ./doc inspect >/dev/null; echo $?

`0` all healthy, `1` a warning drive present (`SCRATCH_ONLY`), `2` a scrap
drive present (`SCRAP`), `3` unknown, unsupported, or refused. `UNKNOWN`
deliberately outranks `SCRAP` in this ordering — an unreadable drive may be
masking a failing controller or cable, and letting a legible `SCRAP` verdict
elsewhere on the bus win the exit code would let that go unnoticed. Run
unprivileged (step 2) and confirm exit 3 with a refusal message, not partial
data, is what you see instead of a graded result.
