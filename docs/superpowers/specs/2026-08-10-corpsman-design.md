# corpsman — drive doctor

**Status:** design, revised after adversarial review of the wipe subsystem. No implementation yet.
**Date:** 2026-08-10
**Wipe subsystem detail:** [2026-08-10-zeroize-wipe-design.md](2026-08-10-zeroize-wipe-design.md)

## What it is

A zero-dependency, single-file terminal application that handles the whole life-end workflow
for storage media on a bench: figure out what the drive is, decide whether it is worth keeping, capture
what is on it, get data back off it, move it to a replacement, destroy what is on it, and
leave a record.

A full-screen TUI is the primary interface for interactive bench work. Every operation is
also a CLI subcommand, because the RMM check, the MCP server, and batch runs need one.

Invoked as `doc`.

    doc                              # full-screen TUI (this is the primary interface)

    doc inspect  /dev/sdb            # identity, SMART, health verdict
    doc test     /dev/sdb            # self-tests, surface scan, throughput
    doc image    /dev/sdb out.img    # error-tolerant capture off a dying drive
    doc recover  carve    out.img outdir/
    doc recover  undelete out.img outdir/
    doc recover  parts    out.img    # scan for lost partitions (read-only)
    doc ledger   --verify
    doc serve-mcp                    # read-only MCP server

    # DEVIL DOC mode only:
    doc wipe     /dev/sdb            # the zeroize engine
    doc restore  in.img /dev/sdb     # writes an image ONTO a device
    doc clone    /dev/sdb /dev/sdc   # device to device, no intermediate file
    doc recover  parts --repair /dev/sdb   # writes a rebuilt partition table

`recover` names the recovery family — getting data back. Capture is `image`, which is what
it always was; an intermediate draft called capture `recover` and that collided the moment
real recovery entered scope.

## The TUI is the primary interface

Typing a device path is itself a footgun. `/dev/sdb` is not a stable name, it shifts when
something is replugged, and nothing about typing it forces the operator to look at what
they are addressing. Selecting from a list that displays model, serial, size, bus, health
verdict, and a red `BACKS YOUR ROOT FILESYSTEM` marker is strictly safer than typing four
characters that may mean something different than they did a minute ago.

Running `doc` with no arguments enters the TUI. Running it with a subcommand behaves
exactly as specified elsewhere in this document — the CLI is not a second-class path, it is
what the RMM check, the MCP server, and any batch use drive, and neither of those can be
operated by a menu.

### Rendering

Hand-rolled ANSI/VT, not `curses`. `curses` is stdlib on Unix and absent on Windows, and
depending on `windows-curses` would break the zero-dependency guarantee. Windows 10+
consoles support VT sequences once `ENABLE_VIRTUAL_TERMINAL_PROCESSING` is set via
`SetConsoleMode`, reachable through `ctypes`.

Terminal capability is detected rather than assumed. Full-screen requires a TTY, a usable
`TERM`, and working VT support. Anything else — a serial console, an IPMI text redirect, a
recovery shell, a dumb terminal, a CI capture — **falls back to a numbered menu** that uses
no raw mode and no escape sequences. On a bench these are not hypothetical conditions;
they are Tuesday.

### Terminal state is restored unconditionally

A tool that leaves the terminal in raw mode after a crash is a tool people stop using. Raw
mode is entered and exited through a context manager, with restoration additionally wired
to `atexit` and to the `SIGINT`/`SIGTERM` handlers already specified in the operational
invariants. `SIGWINCH` triggers a re-layout rather than a corrupted frame.

### The TUI does not weaken any safety control

- Devices resolved as system-state ancestors render locked and are **not selectable** for
  destructive actions at all. The refusal is not a dialog that can be clicked past.
- Arming still requires the held chord. The TUI is already in raw mode, so the hold reads
  naturally and renders its own charge meter.
- The final gate before a destructive operation remains typing the device's serial suffix,
  shown on the identity card. Visual selection reduces the chance of addressing the wrong
  device; it does not replace the confirmation that the operator read the identity.
- The mode banner is persistent in the header. There is no screen on which a user can be
  armed and not see it.

## Why one tool instead of a wiper plus a diagnostics tool

The device-targeting layers are the dangerous part of any of these commands. Targeting
bugs are what destroy a production disk, and they are equally capable of imaging the
wrong drive or reporting one drive's SMART data under another drive's serial.

Putting `inspect` and `test` on the *same* identity and topology code as `wipe` means
the high-risk code runs many times a day in a harmless mode. Duplicating that logic into
a separate diagnostics tool would give two copies that drift, and the copy that gets
exercised least would be the one wired to destruction.

## Two modes: AID STATION and DEVIL DOC

The tool boots into **AID STATION** — read-only. `inspect`, `test`, `image`, `recover`
(except `parts --repair`), and `ledger` work.

Destructive operations — `wipe`, `restore`, `clone`, `recover parts --repair`, and
`test --destructive` — are hidden from `--help`, absent from the MCP tool list and from the
TUI's action bar, and refuse at dispatch. Hiding is a usability affordance; the enforcement
is at dispatch and again immediately before the first destructive syscall.

Arming switches to **DEVIL DOC** — doc picked up a rifle.

### What arming is, and what it is not

**Arming is an anti-footgun control, not a security boundary.** Saying otherwise would be
theater, so the distinction is stated here and the marketing copy is held to it.

It does **not** stop an adversary who already has a root shell. Such an actor can allocate
a pty with `script`, `expect`, or `ssh -t`, drive keystrokes with `tmux send-keys`,
`xdotool`, `ydotool`, or Windows `SendInput`, or simply skip this tool and run `dd`. An
LLM with shell access and the ability to allocate a pty is in exactly the same position.

That is an acceptable limitation because **anyone who can do those things does not need
corpsman to destroy a disk.** The tool is not the weakest link in that threat model and
should not pretend to be the strongest.

What arming *does* reliably prevent is the failure mode that actually happens on a bench:
a destructive command arriving by accident. A pasted command block, a recalled shell
history entry, a script someone copied from a wiki, a CI job that inherited the wrong
arguments, or a tab-completion that landed one device off. Those are the realistic ways
a drive gets wiped by mistake, and a held physical keypress stops every one of them.

### Arming mechanism

1. **`stdin` must be a TTY.** If it is a pipe, a file, a heredoc, a CI runner, or an MCP
   transport, arming is refused with no override.
2. **Held chord, not tapped.** `ctrl+alt+D` held for 3 seconds, read in raw terminal mode
   via `termios` on POSIX and `msvcrt` on Windows — both stdlib. The hold renders a
   progress meter; releasing early aborts.
3. **Process-scoped only.** Armed state lives in process memory. Never written to disk,
   never exported to the environment, never inherited by a child process.
4. **Expires.** 10 minutes idle. **Expiry is re-checked immediately before every
   destructive operation, not once at startup** — see the dispatch rule below.
5. **Loud.** The banner stays on screen and every prompt is prefixed. There is no quiet
   destructive state.

### One parser, enforcement at dispatch

An earlier draft said destructive subcommands were "not registered" in AID STATION, with
the parser built conditionally from the mode at startup. That is a bug factory: the mode
is evaluated once at parse time, so either a session that arms interactively mid-process
can never reach the destructive commands, or a parse-time decision outlives the 10-minute
expiry it was supposed to respect.

**The parser is built once, statically, and always contains every subcommand.** Mode is
enforced at dispatch and re-checked inside the execution wrapper immediately before the
first destructive syscall. `--help` hides destructive commands in AID STATION as a
usability affordance only; hiding is never the enforcement mechanism.

### Scripted and batch use

An earlier draft gated batch use behind `CORPSMAN_DEVIL_DOC=1` "set in the operator's
shell rather than in the script." That is theater — nothing stops a pasted block from
containing the `export` line itself. It has been removed rather than left in to feel
reassuring.

The real control is the one already in the design: **`--confirm-token` is device-specific
and cannot be guessed.** It is derived from the composite identity of a particular
physical device, and the only way to obtain it is to run `doc inspect` against that device
first. A batch script therefore cannot name a device it has not already enumerated on that
machine, and a script copied from elsewhere carries tokens that resolve to nothing locally.

Batch mode additionally refuses to run if any supplied token does not resolve to a
currently-present device, and prints every target for review before the first write.

### MCP is never armed

`doc serve-mcp` runs permanently in AID STATION, and its transport is not a TTY. `wipe`
and `restore` are also absent from the advertised tool list entirely, and a test asserts
it. The absence is the control here — the TTY check is a backstop, not the guarantee.

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
  subcommand consults it; `wipe` refuses on it, `inspect` and `image` annotate with it
  so the operator can see "this device currently backs your root filesystem."
- `probe` — runtime capability detection for optional accelerators (smartctl, nvme-cli,
  hdparm, sg3_utils, sedutil-cli, ddrescue, cdrecord). Probed once, cached, reported.
  Every subcommand degrades explicitly and says what it could not do.
- `record` — hash-chained append-only ledger at `~/.corpsman/ledger.jsonl`. Inspections,
  tests, images, and wipes all append. The chain makes retroactive edits detectable.

## Zero-dependency posture, and why it is a build artifact rather than a source layout

Python 3.8+, stdlib only. `ctypes` covers the Windows volume-lock ioctls; `plistlib`
covers macOS `diskutil`; `/sys` and `/proc` cover Linux without udev.

External binaries are accelerators, never requirements. Their absence lowers what the tool
can *claim*, never what it can *safely do*.

**The single file is a distribution artifact, not the way the source is maintained.** An
earlier draft called for literally writing one file. At this scope — six subcommands,
three OS backends, SMART parsing across three transports, ddrescue-class imaging, and an
MCP server — a monolith means an edit to the SMART parser sits in the same file as the
wipe execution path, with no module boundary to contain a mistake. For a tool whose
failure modes are destructive, that is the wrong trade.

Source is organised one module per architectural layer: `identity`, `topology`, `probe`,
`smart`, `strategy`, `execute`, `image`, `record`, `mcp`, and a thin CLI entrypoint. A
build step concatenates them into a single `doc` script for distribution, so the property
that actually matters to the operator — one file, copy it to a rescue USB, run it offline
— is preserved without maintaining a monolith. The build output is checked for import
purity so a stdlib-only guarantee cannot regress silently.

## Operational invariants

These apply to every subcommand and are specified once here rather than repeated.

### Exclusive device locking

Nothing in an earlier draft prevented two instances from operating on one device — a
realistic bench scenario with two techs, or one tech and a forgotten SSH session. Both
would pass preflight independently and then interleave.

Before any destructive or imaging operation, the tool takes an **exclusive kernel-level
lock on the device**, not merely a lockfile: on Linux, opening the block device with
`O_EXCL` is refused by the kernel if the device is mounted or already held, which is a far
stronger guarantee than advisory locking. This is backed by a lockfile keyed to the
identity token, carrying PID and start time for stale-lock detection, so the operator gets
a comprehensible message rather than an opaque `EBUSY`. Windows uses the existing
`FSCTL_LOCK_VOLUME` acquisition; macOS uses `diskutil unmountDisk` plus an exclusive open.

The lock is held for the entire operation and released on exit, including on signal.

### Interruption and crash lifecycle

`SIGINT` and `SIGTERM` handlers atomically write an `INTERRUPTED` record with the byte
offset reached, release the device lock, and exit non-zero. Power loss leaves the lockfile
and an unclosed run record behind by design.

On the next invocation against the same identity, an unclosed prior run is detected and
the tool **refuses to certify** that device until the operator explicitly reconciles it.
A run that died halfway must never be indistinguishable from one that completed.

### Long-running operations survive terminal death

A multi-terabyte wipe or image outlives SSH sessions and laptop lids. Every long-running
command gets a persistent run-session ID and writes progress state, so it can be detached,
polled with `doc status <session>`, and resumed rather than restarted. Restarting a
14-hour wipe because a laptop slept is an unacceptable failure mode, and guessing whether
partial progress can be trusted is worse.

### Privilege is checked, never degraded into

Running unprivileged makes some ioctls and `/sys` reads fail silently, yielding partial
SMART data and truncated topology — and a verdict computed from incomplete data with no
indication it was incomplete. Any command touching a raw device performs an explicit
privilege preflight and **refuses to run** rather than proceeding degraded.

### Locale is pinned on every subprocess

`smartctl`, `hdparm`, and friends change their output under a non-English locale, which
silently breaks parsing and feeds a wrong health verdict into everything downstream. Every
external invocation is made with `LC_ALL=C` and `LANG=C`, and JSON output is preferred
wherever the tool offers it. Ambiguous output fails closed.

### Unsupported platforms are refused, not guessed

Device path conventions differ enough between platforms that a best-guess backend on an
unrecognised OS is a destructive-path hazard. Platform detection is a hard gate at
startup: outside the explicitly supported set, destructive and imaging commands refuse to
run. `inspect` may still run read-only with a clear "unsupported platform" banner.

### Time on a certificate is operator-controllable, and says so

Wall-clock time comes from a machine the operator controls and can be set backwards. Runs
record both wall-clock timestamps and monotonic elapsed time, and where a reference is
reachable the tool checks for significant clock skew and flags it on the record. The
certificate states plainly that its timestamp is locally sourced.

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

- `SCRAP` — SMART overall self-assessment FAILED, a failed extended self-test, NVMe
  `available_spare` below its threshold, `media_errors` nonzero, **or** reallocated or
  pending sectors above threshold *or growing between inspections*.
- `SCRATCH_ONLY` — nonzero but stable-and-below-threshold 5/197/198, nonzero 187 or 188,
  NVMe `percentage_used` over 90, very high power-on hours, or sustained thermal
  excursions.
- `REUSE` — clean.
- `UNKNOWN` — SMART could not be read. Explicitly not the same as healthy, and the tool
  never collapses these two.

**Thresholds and trend, not nonzero.** An earlier draft condemned any drive with a single
nonzero 5/197/198. That is wrong in practice: a 10 TB drive that remapped three sectors
in year one and none since is a working drive, and a rule that bins it would condemn most
of the used enterprise inventory that crosses a bench. What predicts failure is
*magnitude* against the drive's own spare capacity and *rate of change* — a pending count
climbing across two inspections a week apart is far more alarming than a static count ten
times its size. The ledger already stores prior inspections of the same identity token,
so trend is available for free on any drive seen before. A drive seen for the first time
is judged on thresholds alone and says so.

Every verdict prints the specific attribute values, thresholds, and where available the
prior reading and delta, that produced it. No opaque scores.

**Raw versus normalized values are handled explicitly.** Vendors encode SMART raw fields
inconsistently — temperature packed alongside min/max in one 48-bit field, 64-bit counters
split into halves, some attributes meaningful only as normalized values. The tool reads
`smartctl --json` and treats the raw field as vendor-opaque unless the attribute is one of
the small set with reliable cross-vendor semantics. Anything it cannot interpret with
confidence is reported as raw and excluded from the verdict rather than guessed at, and
the JSON schema is version-checked with defensive parsing, since smartctl's output has
changed shape across releases.

### Health verdict does not determine sanitization verdict

The two specs previously contradicted each other here: this document said `SCRAP` capped
sanitization at `DESTROY_REQUIRED`, while the wipe spec said nonzero reallocated sectors
forced at best `INCOMPLETE`. Both were wrong, and the aggressive reading would have made
the tool useless for its primary purpose — nearly every used enterprise drive has some
remap history, so every certificate would have said "physically destroy this."

**They are separate questions and are now computed independently.**

- The **health verdict** answers "should this go back into service." It is about
  reliability.
- The **sanitization verdict** answers "is the data gone." It is about the method that
  ran and the evidence that it worked.

A drive with remapped sectors that completed a verified ATA Secure Erase is `PURGED`.
Hardware sanitize commands operate below the LBA layer and erase remapped and
over-provisioned blocks too — that is precisely why they outrank overwrite, and refusing
to credit them on a drive with remaps would invert the whole point.

A drive with remapped sectors that could only be *overwritten* is `CLEARED`, carrying an
explicit disclosure on the certificate that N remapped sectors were not addressable by
overwrite and may retain data. The operator, or their client's policy, decides whether
that is acceptable. The tool discloses; it does not silently decide for them.

`DESTROY_REQUIRED` is reserved for what it was always meant for: no adequate software
path exists, an unremovable hidden area was found, or the device threw IO errors that
left regions unwritten.

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

## `doc image` and `doc restore`

Two directions, and only one of them is safe. `image` reads a device into an image file and
runs in AID STATION. `restore` writes an image onto a device, which destroys whatever is
there, so it is DEVIL DOC only and sits behind the same topology refusals, identity
confirmation, and device locking as `wipe`.

`clone` is the two fused into one pass, device to device, with no intermediate file — see
below.

Getting data back out of an image is a separate subsystem — see the
[recovery design](2026-08-11-recovery-design.md).

`image` is error-tolerant, ddrescue-shaped, because the drives most worth capturing are
the ones actively dying.

### Reading a failing drive is not harmless

An earlier draft allowed capture freely in AID STATION on the reasoning that reads are
safe. They are safe for the *data* but not for the *media*. Sustained retry on a failing
drive keeps the heads loaded and the platters spinning, generates heat, and is a
well-documented way to convert a drive a professional recovery house could have read into
one nobody can. The most valuable case — a client's only copy on a dying disk — is exactly
the case where an amateur retry loop does the most damage.

So health gates read intensity — but the gate scales with risk instead of being uniform,
because imaging a degraded drive **is** the recommended action and blocking it would be
backwards. The full ladder is specified in the
[recovery design](2026-08-11-recovery-design.md); in short, a healthy drive runs with an
informational note, a degraded one warns and takes a keystroke, a `SCRAP`/`UNKNOWN` one
requires typing the serial suffix, and only mechanical-failure indicators — SMART spin-up
failure, or IO errors present at enumeration — escalate to naming a recovery lab as the
honest recommendation.

Every level still proceeds if the operator insists. It is their drive and their client, and
a tool that flatly refuses gets replaced by `dd`, which warns about nothing at all.

### The mapfile is GNU ddrescue's format exactly, or it is not claimed

An earlier draft said the progress map was "compatible in spirit" with GNU ddrescue's.
That phrase is worthless: an operator who resumes a partially-compatible map with real
ddrescue gets silently wrong region coverage and false confidence about what was read.

The mapfile implements the documented GNU ddrescue format exactly, with interop tests
asserting round-trip fidelity in both directions against real ddrescue. If that proves
impractical, the fallback is a clearly-named proprietary format that ddrescue will refuse
to open. There is no middle option.

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

## `doc clone` — device to device

    doc clone <source> <target>     [DEVIL DOC]

Failing drive to its replacement, HDD to SSD, client machine to a spare. Same error-tolerant
read engine as `image`, writing straight through to the target instead of to a file.

Worth being its own verb rather than telling people to pipe `image` into `restore`: it needs
no scratch space, which matters because cloning a 2 TB drive otherwise requires 2 TB free
that a bench often does not have. The trade is real and stated in the help text — a clone
produces no second copy, so if the source dies partway through, there is neither a working
original nor a usable image. When free space exists and the source is degraded,
image-then-restore is the better play and the tool says so.

### Source/target inversion is the failure mode this design exists to stop

Wiping the wrong drive at least destroys one thing. Cloning backwards destroys the client's
data *and* consumes the copy that would have restored it, in one operation, and the operator
usually does not find out until later.

- **Source and target are confirmed separately**, each with its own identity card and its own
  typed serial suffix. There is no single confirmation covering both.
- **The target's card is rendered in the danger style and labelled `WILL BE DESTROYED`.** No
  screen ever shows both devices without saying which one dies.
- **Inversion heuristics run before anything is written.** If the target holds a valid
  partition table with recognisable filesystems and the source does not, or the target is
  substantially fuller than the source, or the source is blank, the tool stops and says
  plainly: *this looks like source and target are reversed.* Overriding requires re-typing
  both serials in the correct roles. Most real inversions are caught by exactly this check.
- **Order is fixed and explicit.** `clone <source> <target>` — never inferred from device
  order, size, or which one was selected first.
- Both devices take the exclusive lock, not just the target.
- Refuses when source and target are the same device, or when one is an ancestor or
  descendant of the other in the topology graph.

### Geometry, and the reasons clones fail to boot

- **Target smaller than source:** refused. Making it fit requires shrinking filesystems,
  which is filesystem repair and deliberately out of scope. The tool says to shrink first
  with a filesystem tool, or to use `image` plus selective `restore`.
- **Target larger:** fine. Trailing space is left unallocated, and the tool reports how much
  rather than silently leaving it. Expanding the last partition is filesystem work and is
  not done here.
- **Sector size mismatch:** a 512-byte-sector source cloned to a 4Kn target produces a disk
  that is misaligned and frequently unbootable. Detected before starting and refused unless
  explicitly overridden, because the failure appears much later and looks like something else
  entirely.
- **GPT disk GUID collision:** a byte-exact clone carries the source's GPT disk GUID and
  partition GUIDs. With both drives attached to one machine — the normal state during a
  migration — Windows and several Linux boot paths behave unpredictably about which one they
  mount. After a successful clone the tool offers to regenerate the target's disk GUID and
  partition GUIDs, and explains why. Most cloning tools get this wrong and the resulting
  bug is miserable to diagnose.
- The GPT backup header is relocated to the end of the *target*, since a larger target
  otherwise carries a backup header stranded in the middle of the disk where nothing will
  find it.

### Reading only what is used

A full-surface clone of a 2 TB drive holding 200 GB moves ten times more data than it needs
to. Where the source's partition scheme and filesystems are ones the recovery subsystem can
already parse, `--used-only` reads allocation bitmaps and copies just the blocks in use,
zeroing the rest on the target.

This depends on the filesystem parsers from the recovery work, so it lands after them. Until
then, and always for unrecognised filesystems, the clone is full-surface. The tool never
guesses at allocation — an unparsed filesystem is copied whole.

### Verification

A clone nobody verified is a guess. After completion the tool re-reads both devices and
compares hashes over the copied extents, reporting any divergence. Bad sectors on the source
are filled with a known pattern, logged by LBA, and named in the summary — never silently
zeroed, because a zero-filled hole in a clone is corruption that looks like data.

The run appends to the ledger with both device identities, extents copied, bad-sector count,
verification result, and whether GUIDs were regenerated.

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

### Protocol negotiation fails closed

Hand-rolling JSON-RPC is fine; hand-rolling it *loosely* is not. The server implements
explicit `initialize`/`initialized` handshaking with protocol-version and capability
checks, and **refuses the session** on an unsupported version rather than best-effort
accepting fields it does not understand and presenting a stale tool surface to a client
that assumes compliance. The supported protocol version is pinned and asserted in tests,
so a spec change surfaces as a clear failure rather than silent drift.

### `read_ledger` is scoped and redacted by default

The ledger accumulates device serials, models, and job history for **every customer the
MSP has ever worked on**. An unscoped `read_ledger` hands that entire cross-customer
history to any connected model session, which is a data-exfiltration surface regardless of
how trustworthy the client is.

`read_ledger` therefore requires a device-identity or session selector — there is no
"return everything" call. Customer-identifying fields are redacted unless explicitly
requested for a single specified record, and any unredacted access is itself written to
the ledger.

**Never exposed:** `wipe`, `restore`, and `clone`. Not behind a flag, not behind a confirmation,
not present in the tool list at all. The server runs permanently in AID STATION and its
transport is not a TTY, so it cannot arm even in principle — but the tools are also
simply absent, because defense in depth is cheap here. Drive destruction requires a human
at a keyboard who held a physical chord and typed an identity token, and no chain of
prompt text should be able to reach it.

`image` is not exposed by default either — it is read-only with respect to the source
device, but it writes large files and is enabled only with `--allow-image`.

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
- `execute` and `image` run against loopback and sparse-file devices, and a scratch USB
  stick. Never against real disks in CI.
- A refusal suite asserts non-zero exit for every topology refusal case.
- An MCP suite asserts `wipe` is absent from the advertised tool list.

## Out of scope

- Firmware-resident malware survives all of this. Suspect it, destroy the device.
- Lab-grade forensic recovery — electron microscopy, platter transplant.
- Filesystem *repair* — `fsck`, `chkdsk`, rebuilding a mountable filesystem in place.
  Repair mutates the patient and can destroy recoverable data. File *recovery* is in scope
  and specified separately in the [recovery design](2026-08-11-recovery-design.md).
- Any guarantee the media still functions afterward.
