# zeroize — the `doc wipe` sanitization engine

**Status:** design approved-pending, revised after adversarial review. No implementation yet.
**Date:** 2026-08-10
**Parent design:** [2026-08-10-corpsman-design.md](2026-08-10-corpsman-design.md)

This document specifies the `doc wipe` subcommand. The `identify`, `topology`, `probe`,
and `record` layers described below are shared with `inspect`, `test`, and `image`, and
are specified in the parent design.

## Goal

One CLI that runs on Windows, macOS, and Linux and either sanitizes the media in front
of it, or refuses to falsely claim it did. Target media: SD cards, USB flash, floppy,
CD-RW/DVD-RW, CD-R/DVD-R, IDE/PATA, SATA HDD, SATA SSD, NVMe, SCSI/SAS, tape.

Primary use: MSP client-device offboarding and bench disposal, producing a defensible
record that can be attached to a ticket.

## The two catastrophic failure modes

Every design decision below is justified against one of these:

- **(A) Wrong-device destruction.** Tool wipes a live production or system disk.
- **(B) False assurance.** Tool reports success while client data remains recoverable,
  and the operator hands that certificate to a law firm or CPA client.

The design is deliberately biased toward refusing to act and toward under-claiming
success. A tool that says "I could not sanitize this, destroy it physically" is
correct. A tool that says PURGED when it isn't is a liability event.

## Stack decision

Single-file Python 3.8+ script, stdlib only — including `ctypes`, which is what makes
the mandatory Windows volume-lock path reachable without third-party packages.

External binaries (hdparm, nvme-cli, sg3_utils, sedutil-cli, blkdiscard, cdrecord,
dvd+rw-format, mt) are optional *accelerators*: capability-probed at runtime, never
assumed. A missing accelerator downgrades the achievable verdict — it never silently
downgrades the safety path.

Rationale: one file copies to any bench machine or rescue USB and runs with no install
step, offline.

## Architecture — 6 layers

1. `identify`  — device discovery producing a composite identity token
2. `topology`  — resolves the full block-device dependency graph and active system state
3. `classify`  — device -> media class
4. `strategy`  — (media class + probed tools + hidden-area findings) -> ordered method plan
5. `execute`   — mandatory exclusive-access acquisition, then runs methods, streams progress
6. `record`    — append-only hash-chained record with an honest NIST-aligned verdict

---

## Layer 1 — `identify`: composite identity, not serial

**Rejected:** keying safety on serial number alone.

Serial fails in the exact conditions this tool is built for. USB-SATA bridges and card
readers report the *bridge's* serial rather than the drive's. Cheap flash reports blank,
duplicated, or vendor-boilerplate serials. Two identical sticks in one dock produce two
identical identity strings, and the operator's confirmation matches whichever the code
happened to resolve first.

**Design:** every device gets an `identity_token` — a SHA-256 truncation over the tuple:

    (os_instance_path, wwn_or_by_id_link, size_bytes_exact, model, serial_or_null, bus)

- Linux: `os_instance_path` = the `/sys/devices/...` canonical path, not `/dev/sdX`;
  `wwn` from `/dev/disk/by-id/wwn-*` where present.
- macOS: `IORegistry` entry path via `diskutil info -plist` (`DeviceIdentifier` alone is
  not stable enough).
- Windows: the device instance path from `Get-PhysicalDisk`/SetupAPI, not
  `\\.\PhysicalDriveN`, which renumbers across replug.

**Blank or duplicate serial is a hard condition, not a warning.** If two enumerated
devices share a serial, or a serial is empty, the tool refuses `--confirm-serial` for
those devices entirely and requires the operator to confirm the full `identity_token`
prefix instead, after physically confirming which port the device is in.

**Confirm/write race is closed.** The identity is re-resolved from scratch immediately
before the first destructive write, inside the exclusive-access window. Any mismatch
against the confirmed token aborts before a single byte is written. This is what makes
a hotplug between confirmation and execution non-exploitable.

## Layer 2 — `topology`: resolve the whole chain, refuse ancestors

**Rejected:** comparing the target against `/`, `/boot`, and `C:` directly.

That check misses every real-world layered setup. On a LUKS+LVM host, `/` is
`/dev/mapper/vg-root`, backed by `/dev/sda2`, backed by `/dev/sda` — and a naive check
happily lets the operator select `/dev/sda`.

**Design:** build a directed dependency graph from every active system-state consumer
down to physical devices, then refuse the target if it is an ancestor of any of them.

Active system state includes: all mountpoints in `/proc/mounts`, all active swap in
`/proc/swaps`, the Windows pagefile, hibernation file, and EFI System Partition.

Chain resolution covers:

- Linux: recursive walk of `/sys/block/<dev>/{holders,slaves}`, which transitively
  covers dm-crypt/LUKS, LVM, mdraid, bcache, loop, and dm-multipath. Plus `zpool status`
  vdev members and `btrfs filesystem show` device lists for filesystems that do not
  register holders the same way.
- macOS: APFS synthesized containers resolved to their `PhysicalStores`, plus CoreStorage
  and RAID sets, via `diskutil info -plist` and `diskutil apfs list -plist`.
- Windows: `Get-Partition`/`Get-Volume` mapping, plus dynamic disks and Storage Spaces
  pool membership via `Get-StoragePool`/`Get-PhysicalDisk`.

Network and virtual block devices (iSCSI, NBD, FC, loop backed by a file on a live
filesystem) are refused by default regardless of mount state — sanitizing them is
almost never the operator's intent and the blast radius is remote.

**No override flag exists for the system-state refusal.** `--allow-mounted` exists only
for non-system mounted volumes, and still requires explicit unmount to proceed.

## Layer 3 — `classify`

Media classes: `HDD_MAGNETIC`, `SSD_SATA`, `NVME`, `SED_OPAL`, `SD_USB_FLASH`,
`FLOPPY`, `OPTICAL_RW`, `OPTICAL_WORM`, `TAPE`, `UNKNOWN`.

`UNKNOWN` is treated as `SD_USB_FLASH` — the most pessimistic assumption — and its
verdict is capped accordingly.

### Hidden-area detection runs here, before any strategy is chosen

Previously omitted entirely. A full-device overwrite that only covers visible LBAs can
leave recoverable data behind, so the tool now looks for the places it hides:

- **HPA** via `hdparm -N`; **DCO** via `hdparm --dco-identify`. If the native max
  address is below the true device max, data exists outside the addressable range.
- **Reallocated/pending sectors** via `smartctl -A` (attrs 5, 197, 198). Remapped
  sectors physically retain data and cannot be overwritten through the LBA interface.
- **SSD/NVMe over-provisioning** is assumed present on all flash — it is not detectable
  and not addressable, which is precisely why flash overwrite can never reach PURGED.

Findings feed the strategy layer and cap the verdict. The tool does not silently remove
an HPA/DCO; it reports it and requires `--remove-hpa` to be passed explicitly, because
resizing is itself destructive and occasionally bricks firmware.

## Layer 4 — `strategy`

Methods are attempted in order. **First success wins only if that success can be
independently confirmed.** A method that cannot be verified does not count as success.

- `SED_OPAL` -> `sedutil-cli --PSIDrevert` (crypto-erase, requires the PSID printed on
  the drive label) -> ATA Secure Erase -> overwrite (capped)
- `HDD_MAGNETIC` -> ATA Secure Erase (`hdparm --security-erase`) -> single-pass
  cryptographic-random full-surface overwrite -> verify.
  Single pass only; multi-pass Gutmann is obsolete for post-1990s perpendicular
  recording and NIST SP 800-88 Rev.1 does not require it.
- `SSD_SATA` -> ATA Enhanced Secure Erase -> ATA Secure Erase -> `blkdiscard -z`
  (advisory only, see below) -> overwrite (capped, never PURGED)
- `NVME` -> `nvme sanitize` (crypto/block erase, with sanitize-status polling to
  completion) -> `nvme format --ses=1` -> `blkdiscard -z` (advisory) -> overwrite (capped)
- `SD_USB_FLASH` -> full-surface overwrite x1 + verify. Verdict capped at
  `CLEARED`; controller-managed remapping means PURGED is not reachable.
- `FLOPPY` -> full-surface overwrite x3 + verify. Capped at `CLEARED`, not "complete" —
  see the revised claim below.
- `OPTICAL_RW` -> `cdrecord blank=all` (full blank, **not** `blank=fast`, which only
  clears the TOC/PMA and leaves the data pits intact and trivially recoverable).
  Capped at `CLEARED`.
- `OPTICAL_WORM` -> no software path exists. Emits physical-destruction guidance and
  the `DESTROY_REQUIRED` verdict.
- `TAPE` -> `mt erase` (long erase) if available, else `DESTROY_REQUIRED`.

### ATA Secure Erase is preflighted and post-verified

Secure erase is the single most commonly-believed-but-silently-broken method in this
whole table. Two failure modes are checked explicitly:

1. **Frozen state.** Most systems issue SECURITY FREEZE LOCK at boot, and a frozen
   drive silently rejects the erase. The tool parses `hdparm -I` for `frozen`, and if
   frozen, refuses to claim the method and tells the operator to hot-replug the data
   cable or suspend/resume to clear it. It does not attempt the command and report
   success.
2. **Silent no-op.** Some drives, particularly USB-bridged ones, accept the command and
   do nothing. After the command returns, the tool re-reads `hdparm -I` security status
   and independently samples the device for the pre-recorded pattern. If prior data is
   still readable, the method is recorded as FAILED regardless of the tool's exit code.

### `blkdiscard -z` is advisory and can never justify a verdict alone

TRIM/discard is a hint. The controller may ignore it, defer it, or apply it lazily, and
a readback can return zeros from the FTL while the underlying cells retain data. It is
recorded as attempted, and it never on its own raises the verdict above `INCOMPLETE`.

## Layer 5 — `execute`: exclusive access is mandatory, never optional

**Rejected:** best-effort writes that fall back to a plain file handle.

An unlocked raw device write on Windows is cached, redirected, or silently dropped by
the volume manager, and the tool would have reported a wipe that never happened.

Before any destructive write the tool MUST acquire exclusive access. Failure to do so
aborts the run with a non-zero exit. There is no degrade path.

- Windows: open `\\.\PhysicalDriveN`, then `FSCTL_LOCK_VOLUME` and
  `FSCTL_DISMOUNT_VOLUME` on every child volume via `ctypes.windll.kernel32.DeviceIoControl`.
  Abort if any lock fails.
- macOS: `diskutil unmountDisk force <dev>`; abort if it fails. SIP prevents raw writes
  to the boot device, which is a backstop, not the primary control.
- Linux: open with `O_DIRECT|O_SYNC` where alignment permits (falling back to `O_SYNC`
  plus explicit `os.fsync` and `BLKFLSBUF` ioctl), and confirm no holders appeared since
  the topology check.

Durability is explicit: buffers are aligned for `O_DIRECT`, and every pass ends with
`fsync` plus a cache-flush ioctl before verification reads. Verification reads bypass the
page cache — otherwise verify reads back its own cached writes and proves nothing.

## Verification and the honest verdict

Sampled verification cannot prove full-surface sanitization, and presenting it as if it
could is exactly failure mode (B). So the verdict now states which one ran.

- `--verify full` (default for any run intended to produce a certificate): full-surface
  readback, cache-bypassed.
- `--verify sample`: random offsets plus first/last 64 MiB. Fast triage only. Its result
  is labelled `SAMPLED` in the record and **cannot** produce an unqualified verdict.

### Verdicts, aligned to NIST SP 800-88 Rev.1

Terminology matters here because it goes on a customer-facing document.

- `PURGED` — a hardware sanitize command (ATA Secure Erase, NVMe Sanitize/Format,
  PSID revert) completed **and** was independently post-verified. Overwrite alone never
  produces PURGED on flash. Requires full verification.
- `CLEARED` — full-surface overwrite completed and fully verified. Protects against
  non-invasive recovery only.
- `CLEARED_SAMPLED` — as above but only sampled verification ran. Explicitly weaker.
- `INCOMPLETE` — any method failed, was advisory-only, or coverage was partial.
- `DESTROY_REQUIRED` — no adequate software path exists, or hidden areas / IO errors
  were found. Physical destruction guidance is printed.

**Any IO error during the run, any region left unwritten or unverified, and any detected
HPA/DCO that was not removed forces the verdict to `DESTROY_REQUIRED`.** A drive that
threw errors mid-wipe never earns a clean certificate. This replaces the earlier "errors
are counted but non-fatal" behavior, which would have produced exactly the false
certificate this tool exists to prevent.

**Pre-existing reallocated sectors are a disclosure, not an automatic failure.** An
earlier draft forced any drive with a nonzero reallocated count to at best `INCOMPLETE`,
which contradicted the parent design and would have made the tool useless — nearly every
used enterprise drive carries some remap history, so every certificate would have read
"physically destroy this."

The correct handling depends on which method ran, because the methods differ in whether
they can reach a remapped block at all:

- A **hardware sanitize** command (ATA Secure Erase, NVMe Sanitize/Format, PSID revert)
  operates below the LBA layer and erases remapped and over-provisioned blocks along with
  everything else. That is exactly why it outranks overwrite. A verified hardware sanitize
  on a drive with remaps is `PURGED`, and refusing to credit it would invert the point.
- An **overwrite** cannot reach a remapped block through the LBA interface. A verified
  full-surface overwrite on such a drive is `CLEARED`, and the record carries an explicit
  disclosure naming the count of remapped sectors that were not addressable and may retain
  data.

The tool discloses and lets the operator or their client's policy decide. It does not
silently downgrade a usable result, and it does not silently hide the caveat either.

The health verdict from `doc inspect` never overrides the sanitization verdict. They
answer different questions — reliability versus data removal — and are computed
independently.

## Layer 6 — `record`: append-only and hash-chained

A plain mutable JSON file is worth little as evidence, since the operator who produced
it can edit it.

Records append to `~/.corpsman/ledger.jsonl`. Each entry carries a SHA-256 over its own
canonicalized content plus the previous entry's hash. `doc ledger --verify` re-walks and
validates the chain.

**What the chain is worth, stated honestly.** It detects accidental corruption, partial
writes, an interrupted append, and edits made by anyone who is not the operator. Against
the operator themselves it is worth nothing — they hold the file and can regenerate the
entire chain, internally consistent, in seconds. A hash chain cannot make its own author
accountable.

That matters because the earlier draft printed the chain head for "recording out-of-band
in the ticket," which invites a customer to read it as proof. It is not proof.

**Therefore: the chain hash does not appear on a customer-facing certificate unless it has
been externally anchored.** Anchoring means either a detached signature made with a key
the operator does not store alongside the ledger, or a third-party timestamp. Neither is
in scope for the first version, so the first version's certificate carries no chain hash
and makes no integrity claim — it is a record of what the tool did, attested by the
operator who ran it, and nothing more.

The chain stays in the local ledger, where its real job is catching corruption and giving
`doc ledger --verify` something to check.

Per-run human-readable output is also written to `corpsman-<identity_prefix>-<ts>.txt`
for stapling to a ticket, containing: device identity, media class, hidden-area findings,
every method attempted and its independently-confirmed outcome, pass count, bytes
written vs. device size, verification mode and result, duration, operator, hostname,
verdict, and the ledger chain hash.

## Revised claims about legacy media

The prior draft asserted floppy overwrite was "genuinely complete." That overstates it.
Residual magnetism at track edges and variable-density/non-standard formats mean a
determined forensic lab with specialized equipment is not fully excluded. Floppy and
optical are therefore capped at `CLEARED` with an explicit caveat that a physical
adversary with lab capability is out of scope for any software method on this media.

## Out of scope, stated explicitly

- Firmware-resident malware survives every method here. If a device is suspected of
  firmware compromise, sanitization is the wrong tool; destroy it.
- Forensic recovery by a lab with electron microscopy or platter-transplant capability.
- Anything that requires the device to still be functional after the run. Several
  strategy paths may leave media unusable, which is acceptable and intended.

## Testing

- `identify`/`topology` are pure functions over captured fixtures: recorded `/sys` trees,
  `diskutil -plist` output, and PowerShell JSON from real machines including a
  LUKS+LVM host, an APFS Fusion drive, and a Storage Spaces box. These are the layers
  where a bug destroys a production disk, so they are tested without touching hardware.
- `strategy` is a pure table test over (media class, available tools, hidden findings).
- `execute` is tested against loopback/sparse-file devices and a scratch USB stick, never
  in CI against real disks.
- A dedicated refusal suite asserts the tool exits non-zero for every case in Layer 2.
