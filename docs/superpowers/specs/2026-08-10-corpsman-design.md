# corpsman — drive doctor

**Status:** design, revised after adversarial review of the wipe subsystem. No implementation yet.
**Date:** 2026-08-10
**Wipe subsystem detail:** [2026-08-10-zeroize-wipe-design.md](2026-08-10-zeroize-wipe-design.md)

## What it is

A single-file, zero-dependency CLI that handles the whole life-end workflow for storage
media on a bench: figure out what the drive is, decide whether it is worth keeping,
capture what is on it, destroy what is on it, and leave a record.

Invoked as `doc`.

    doc inspect  /dev/sdb            # identity, SMART, health verdict
    doc test     /dev/sdb            # self-tests, surface scan, throughput
    doc recover  /dev/sdb out.img    # error-tolerant capture off a dying drive
    doc ledger   --verify
    doc serve-mcp                    # read-only MCP server

    # DEVIL DOC mode only:
    doc wipe     /dev/sdb            # the zeroize engine
    doc restore  in.img /dev/sdb     # writes an image ONTO a device

## Why one tool instead of a wiper plus a diagnostics tool

The device-targeting layers are the dangerous part of any of these commands. Targeting
bugs are what destroy a production disk, and they are equally capable of imaging the
wrong drive or reporting one drive's SMART data under another drive's serial.

Putting `inspect` and `test` on the *same* identity and topology code as `wipe` means
the high-risk code runs many times a day in a harmless mode. Duplicating that logic into
a separate diagnostics tool would give two copies that drift, and the copy that gets
exercised least would be the one wired to destruction.

## Two modes: AID STATION and DEVIL DOC

The tool boots into **AID STATION** — read-only. `inspect`, `test`, `recover`, and
`ledger` work. Destructive subcommands (`wipe`, `restore`, `test --destructive`) are not
merely refused: they are **not registered**. They do not appear in `--help`, they do not
parse, and the MCP server does not advertise them.

Arming switches to **DEVIL DOC** — doc picked up a rifle.

### Why a mode instead of a flag

A flag can be pasted from a wiki, recalled from shell history, inherited from a script
someone copied, or produced by an LLM completing a command. A mode entered by a held
physical keypress at a real terminal cannot arrive by any of those routes.

### Arming mechanism

1. **`stdin` must be a TTY.** If it is a pipe, a file, a heredoc, a CI runner, or an MCP
   transport, arming is impossible and there is no override. This single rule closes
   paste-injection, accidental automation, and every prompt-injection path through the
   MCP server at once.
2. **Held chord, not tapped.** `ctrl+alt+D` held for 3 seconds, read in raw terminal mode
   via `termios` on POSIX and `msvcrt` on Windows — both stdlib. A hold cannot be
   expressed in a pasted string. The hold renders a progress meter; releasing early
   aborts.
3. **Process-scoped only.** The armed state lives in process memory. It is never written
   to disk, never exported to the environment, and never inherited by a child process.
4. **Expires.** 10 minutes idle, or on any subcommand completing, whichever is first.
   The remaining time is displayed in the persistent banner.
5. **Loud.** While armed, every prompt is prefixed and the banner stays on screen. There
   is no quiet destructive state.

### Scripted and batch use

Arming is interactive-only by design, so bench automation uses a separate path that
cannot be reached by pasting a command: the existing
`--device / --confirm-token / --yes-i-am-sure` triple, **plus** the environment variable
`CORPSMAN_DEVIL_DOC=1`, which must be set in the operator's shell rather than inside the
script being run. A copy-pasted command block therefore cannot self-arm, and a script
committed to a repo cannot arm itself on someone else's machine.

### MCP is never armed

`doc serve-mcp` runs permanently in AID STATION. The transport is not a TTY, so arming is
structurally impossible rather than merely disallowed — the check is the same one that
protects every other non-interactive path. `wipe` and `restore` are absent from the
advertised tool list entirely, and a test asserts it.

## Voice and terminology

Human-readable terminal output uses corpsman language. `inspect` announces
`** CORPSMAN UP! **` when it finds a failing drive. Triage categories map onto the health
verdicts, since they are the same decision:

| Technical verdict | Triage |
|---|---|
| `REUSE` | return to duty |
| `SCRATCH_ONLY` | walking wounded |
| `SCRAP` | expectant |
| `UNKNOWN` | unable to assess |

**The flavor is confined to interactive terminal output.** `--json`, the RMM check
schema, the ledger, and the customer-facing sanitization record all use the plain
technical identifiers only. A certificate handed to a CPA or law-firm client says
`DESTROY_REQUIRED`, never "expectant," and a TacticalRMM check parses stable enum values
that never change because someone improved a joke.

## Shared foundation

Four layers are common to every subcommand:

- `identify` — composite identity token per device. Serial is not trusted as identity;
  see the wipe spec for why (USB bridges, blank and duplicate serials).
- `topology` — full block-device dependency graph and active system state. Every
  subcommand consults it; `wipe` refuses on it, `inspect` and `recover` annotate with it
  so the operator can see "this device currently backs your root filesystem."
- `probe` — runtime capability detection for optional accelerators (smartctl, nvme-cli,
  hdparm, sg3_utils, sedutil-cli, ddrescue, cdrecord). Probed once, cached, reported.
  Every subcommand degrades explicitly and says what it could not do.
- `record` — hash-chained append-only ledger at `~/.corpsman/ledger.jsonl`. Inspections,
  tests, images, and wipes all append. The chain makes retroactive edits detectable.

## Zero-dependency posture

Python 3.8+, stdlib only, one file. `ctypes` covers the Windows volume-lock ioctls;
`plistlib` covers macOS `diskutil`; `/sys` and `/proc` cover Linux without udev.

External binaries are accelerators, never requirements. Their absence lowers what the
tool can *claim*, never what it can *safely do*.

This includes the MCP server: MCP over stdio is JSON-RPC 2.0 on stdin/stdout, which is
implementable in stdlib. No SDK, so `doc serve-mcp` runs from the same single file on a
machine with nothing installed.

---

## `doc inspect`

Identity, geometry, partition layout, and health.

**SMART sources**, in preference order, per transport:

- ATA/SATA: `smartctl --json` (7.0+ emits JSON natively, which avoids parsing its
  notoriously unstable text output). Fallback: `smartctl` text parse. Fallback:
  `/sys/block` metadata only, with SMART reported as UNAVAILABLE rather than assumed OK.
- NVMe: `nvme smart-log --output-format=json` and `nvme id-ctrl`, else `smartctl --json`.
- SAS/SCSI: `smartctl --json` log pages — grown defect list, and read/write/verify error
  counters.
- USB-bridged: attempted with `-d sat`, `-d usbjmicron`, and similar. Many bridges do not
  pass SMART through at all; this is reported as UNAVAILABLE, never as healthy.

**Attributes that drive the verdict.** Backblaze's fleet data across hundreds of
thousands of drives found five attributes carry nearly all the predictive signal, and
those are weighted accordingly:

| ID | Attribute | Meaning |
|---|---|---|
| 5 | Reallocated_Sector_Ct | Sectors already failed and remapped |
| 187 | Reported_Uncorrect | Errors the drive could not correct |
| 188 | Command_Timeout | Drive stopped responding to commands |
| 197 | Current_Pending_Sector | Unstable sectors awaiting remap — the strongest near-term signal |
| 198 | Offline_Uncorrectable | Sectors that failed and could not be remapped |

Also collected: 9 power-on hours, 194 temperature and its history, 241 total LBAs
written, 12 power cycle count, and full self-test history.

NVMe equivalents: `critical_warning` bitfield, `percentage_used`, `available_spare`
against `available_spare_threshold`, `media_errors`, `unsafe_shutdowns`, and thermal
throttling events.

**Attribute 199 (UDMA_CRC_Error_Count) is deliberately excluded from the drive's health
verdict and reported separately as a cabling fault.** CRC errors are interface errors —
a bad SATA cable, a marginal backplane, a flaky USB bridge. Counting them against the
drive is how a perfectly good disk gets thrown away and the actual faulty cable stays in
the machine and kills the next one. When 199 is nonzero, the tool says so in those words.

**Health verdict** — the actual bench decision, stated plainly:

- `SCRAP` — SMART overall self-assessment FAILED, or any of 5/197/198 nonzero, or NVMe
  `available_spare` below threshold, or `media_errors` nonzero, or a failed long
  self-test. Drive does not go back into service, and per the wipe spec its sanitization
  verdict is capped at `DESTROY_REQUIRED` because remapped sectors retain data.
- `SCRATCH_ONLY` — 187 or 188 nonzero, NVMe `percentage_used` over 90, very high
  power-on hours, or sustained thermal excursions. Works, but nothing irreplaceable goes
  on it.
- `REUSE` — clean.
- `UNKNOWN` — SMART could not be read. Explicitly not the same as healthy, and the tool
  never collapses these two.

Every verdict prints the specific attribute values that produced it. No opaque scores.

## `doc test`

- SMART short and long (extended) self-tests, launched and polled to completion with
  progress, results appended to the ledger.
- Non-destructive read-only surface scan: full sequential read, logging every unreadable
  LBA and every sector that required retries. Slow sectors are recorded too — a sector
  that reads successfully after two seconds is a sector about to fail.
- Optional destructive read-write surface pass (`--destructive`), which is gated behind
  the same topology refusals as `wipe`.
- Sequential throughput sample, to catch the drive that passes every health check and
  runs at 4 MB/s because it is quietly retrying everything.

## `doc recover` and `doc restore`

Two directions, and only one of them is safe. `recover` reads a device into an image file
and runs in AID STATION. `restore` writes an image onto a device, which destroys whatever
is there, so it is DEVIL DOC only and sits behind the same topology refusals and identity
confirmation as `wipe`.

`recover` is error-tolerant, ddrescue-shaped, because the drives most worth capturing are
the ones actively dying.

- Multi-pass: fast sequential sweep first to capture everything readable, then reverse
  and retry passes narrowing on the bad regions. Reading a dying drive linearly and
  hammering the first bad sector is how the rest of the recoverable data gets lost to
  a head crash or thermal death mid-run.
- Resumable mapfile compatible in spirit with GNU ddrescue's, so a run can be stopped
  and resumed, or handed to ddrescue if someone prefers it.
- Rolling SHA-256 of the image, plus a per-region hash map, written into the ledger.
- Sparse output and optional gzip streaming; imaging a mostly-empty 4 TB drive should not
  cost 4 TB.
- Bad regions are recorded explicitly and filled with a known pattern, never silently
  zero-filled — an image with an unmarked zeroed hole is a corrupt image that looks fine.
- Refuses to write the image onto the source device, or onto a filesystem without room,
  checked before starting rather than 3 TB in.

## `doc --json` / RMM mode

Every subcommand emits a stable JSON document with `--json`, and sets exit codes for
monitoring: `0` healthy, `1` warning (`SCRATCH_ONLY`), `2` critical (`SCRAP`), `3`
unknown/unreadable.

Intended to run as a TacticalRMM check across the managed fleet, so drive failures
surface on RiverRMM before the client calls. The schema is versioned so a check written
today does not break when fields are added.

## `doc serve-mcp`

Read-only MCP server over stdio, so a Claude session can ask about drive health directly.

**Exposed tools:** `list_devices`, `inspect_device`, `get_smart`, `test_status`,
`read_ledger`.

**Never exposed:** `wipe` and `restore`. Not behind a flag, not behind a confirmation,
not present in the tool list at all. The server runs permanently in AID STATION and its
transport is not a TTY, so it cannot arm even in principle — but the tools are also
simply absent, because defense in depth is cheap here. Drive destruction requires a human
at a keyboard who held a physical chord and typed an identity token, and no chain of
prompt text should be able to reach it.

`recover` is not exposed by default either — it is read-only with respect to the source
device, but it writes large files and is enabled only with `--allow-recover`.

The server is also read-only about the ledger: it can validate and read the chain, never
append to it.

## Testing

- `identify` and `topology` are pure functions over captured fixtures — recorded `/sys`
  trees, `diskutil -plist` output, PowerShell JSON — from real machines including a
  LUKS+LVM host, an APFS Fusion drive, and a Storage Spaces box. These layers are tested
  without touching hardware because a bug in them destroys a production disk.
- SMART parsing is tested against a corpus of real `smartctl --json` and text output,
  including drives that report garbage, USB bridges that report nothing, and at least one
  drive with a nonzero 199 to assert the cabling verdict is separated correctly.
- `strategy` and the health verdict are pure table tests.
- `execute` and `recover` run against loopback and sparse-file devices, and a scratch USB
  stick. Never against real disks in CI.
- A refusal suite asserts non-zero exit for every topology refusal case.
- An MCP suite asserts `wipe` is absent from the advertised tool list.

## Out of scope

- Firmware-resident malware survives all of this. Suspect it, destroy the device.
- Lab-grade forensic recovery — electron microscopy, platter transplant.
- Filesystem-level repair and file recovery. This tool works at the block layer;
  `fsck`/`chkdsk`/PhotoRec are different jobs and better tools already exist.
- Any guarantee the media still functions afterward.
