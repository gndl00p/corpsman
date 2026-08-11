# corpsman

Drive doctor for the bench. Figures out what the drive is, whether it is worth keeping,
captures what is on it, gets data back off it, moves it to a replacement, destroys what is
on it, and leaves a record.

One file. Python 3.8+. Windows, macOS, Linux. No dependencies, no install step, runs
offline from a rescue USB. (The single file is a build artifact — source is one module per
layer, concatenated at build time, because a monolith mixing SMART parsing with the wipe
execution path is the wrong trade for a tool whose failure modes are destructive.)

> **Status: Phase 1 — Linux `inspect` only.** `doc inspect [device] [--json]` is the
> only command that exists. Everything else shown below — `test`, `image`, `clone`,
> `wipe`, `recover`, `ledger`, `serve-mcp`, the TUI — is design in
> `docs/superpowers/specs/`, not code. 117 tests pass (`python3 -m pytest tests/ -v`).
> `doc` itself is a build artifact (`python3 build.py`, gitignored) assembled from
> `src/corpsman/`.
>
> **Linux only.** macOS and Windows are refused rather than guessed at — there is no
> backend for either. Device identity, topology (including LUKS/LVM/mdraid resolution
> to the physical disk), and SMART-based health verdicts work on Linux and are covered
> by fixture-based tests. SMART is read via `smartctl --json`; a device whose SMART
> cannot be read reports `UNKNOWN`, never `REUSE`.
>
> **No destructive code path exists in this tree.** A guard test
> (`tests/test_guards.py::test_no_device_write_paths_in_phase_one`) scans for the
> obvious write-mode flags and destructive binaries, but it is a substring smoke alarm,
> not a proof of read-only-ness — see [`docs/PHASE1-SMOKE.md`](docs/PHASE1-SMOKE.md)
> for the manual verification procedure and its limits before pointing this at real
> hardware.

Invoked as `doc`.

```
doc                             # full-screen TUI - the primary interface

doc inspect /dev/sdb            # identity, SMART, health verdict
doc test    /dev/sdb            # self-tests, surface scan, throughput
doc image   /dev/sdb out.img    # error-tolerant capture off a dying drive
doc recover carve    out.img outdir/
doc recover undelete out.img outdir/
doc recover parts    out.img    # scan for lost partitions
doc ledger  --verify
doc serve-mcp                   # read-only MCP server

doc wipe    /dev/sdb                   # DEVIL DOC mode only
doc restore in.img /dev/sdb            # DEVIL DOC mode only
doc clone   /dev/sdb /dev/sdc          # DEVIL DOC mode only
doc recover parts --repair /dev/sdb    # DEVIL DOC mode only
```

## The TUI

```
+- CORPSMAN ------------ AID STATION -+
| > sda  1.0T Samsung 870    REUSE    |
|   sdb  4.0T WD40EFRX       SCRAP  ! |
|   sdc   32G SanDisk USB    REUSE    |
|   nvme0 512G SN770  [ROOT FS]  lock |
+-------------------------------------+
| sdb  WD-WCC4E5RJ0K2P  4.0 TB  SATA  |
| 197 Current_Pending  41  ^ +12 / 7d |
| 5   Reallocated     128             |
| 9   Power_On_Hours 41203            |
| ** CORPSMAN UP ** expectant         |
+-------------------------------------+
| i inspect  t test  r recover        |
| ^AD arm     q quit                  |
+-------------------------------------+
```

Typing a device path is itself a footgun. `/dev/sdb` isn't a stable name, it shifts when
something gets replugged, and typing it doesn't force you to look at what you're
addressing. Picking from a list showing model, serial, size, health, and a red
`[ROOT FS]` marker is strictly safer.

Hand-rolled ANSI/VT rather than `curses`, which is stdlib on Unix but absent on Windows —
`ctypes` flips on `ENABLE_VIRTUAL_TERMINAL_PROCESSING` there. Capability is **actively
probed** with a cursor-position query rather than inferred from `TERM`, because serial
gateways and IPMI redirects pass an `isatty` check and then desync the display without
visibly breaking it. No response, malformed response, or timeout falls back to a numbered
menu with no raw mode and no escape sequences.

**Destructive confirmations bypass the TUI input path entirely.** The full-screen renderer is
three platform-specific implementations whose untested long tail — resize races, bracketed
paste, mouse reporting, IME — is exactly where a prompt could be satisfied by bytes nobody
typed. Confirmations drop to a minimal byte-level reader that rejects escape sequences and
control bytes outright, with mouse reporting and paste disabled for the duration. Auto-repeat
can't produce a serial suffix.

System-state ancestors render locked and aren't selectable for destructive actions at all —
the refusal isn't a dialog you can click past. Arming still needs the held chord, and the
mode banner is always in the header. Terminal restoration is wired to `atexit`, an
exception hook, and `SIGINT`/`SIGTERM`/`SIGHUP`/`SIGTSTP`/`SIGCONT` — but **not**
unconditional, since `SIGKILL` and a power cut can't be handled by the process being killed.
`doc reset-term` exists for that, and so does `stty sane`.

## Cloning

`doc clone <source> <target>` — failing drive to its replacement, HDD to SSD, machine to a
spare. Same error-tolerant read engine as `image`, writing straight through with no
intermediate file, so it needs no scratch space. The trade is stated in the help text: a
clone leaves no second copy, so if the source dies partway you have neither a working
original nor a usable image. With free space and a degraded source, image-then-restore is
the better play.

**Source/target inversion is the failure mode this design exists to stop.** Wiping the wrong
drive destroys one thing. Cloning backwards destroys the client's data *and* consumes the
copy that would have restored it, in a single operation, and you usually don't find out
until later.

The primary control isn't a heuristic — it's **describing what the target holds, in words**.
Heuristics only catch asymmetric cases, and two populated drives of similar size trip none of
them, which is exactly the ordinary migration where arguments get transposed:

```
TARGET — WILL BE DESTROYED
  Samsung 870 EVO  1.0 TB  #S5Y2NJ0T304891
  GPT, 3 partitions
  NTFS "Client-Data"   847 GB used of 931 GB
  last written 2026-08-09
  type S5Y2NJ0T304891 to destroy this:
```

Transpose your arguments and you're reading a description of the drive you meant to keep. No
content comparison can decide which of two data-bearing drives *should* be the source, so the
tool stops guessing intent and makes the consequence impossible to miss.

- Source and target are confirmed **separately**, each with its own card and typed serial.
- Asymmetric heuristics still run as a second net — target has a table the source lacks,
  target substantially fuller.
- A blank source is an acknowledgement, not a block. Blank-to-blank staging is real work, and
  a hard stop there teaches reflexive overriding, which kills the control where it matters.
- Direction is positional, never inferred from size or selection order.
- A clone marker on the target means a partial prior clone can't masquerade as a finished one.

**Why clones fail to boot**, all handled:

- **GPT identifier collision** — a byte-exact clone carries the source's disk GUID *and*
  every partition GUID, and with both drives attached the duplicate **disk** GUID makes
  Windows and several Linux boot paths unpredictable about which they mount. The obvious fix
  is worse than the problem: **partition** GUIDs are what `fstab` and `crypttab` `PARTUUID=`
  entries, systemd-boot, GRUB, BitLocker, and Windows BCD resolve against, so regenerating
  them yields a clone that looks fine and won't boot. Default is byte-exact, preserving
  everything. Regenerating the disk GUID alone is opt-in; regenerating partition GUIDs needs
  a separate flag that names what it breaks, and is never suggested.
- **Sector size and alignment** — 512e source onto a 4Kn target is misaligned and usually
  unbootable, and the symptom surfaces much later looking unrelated. Rather than a binary
  refuse-or-override, which just trains people to override, it evaluates logical size,
  physical size, alignment offset, and each partition start against the target and reports
  clean, viable-but-misaligned with the partitions named, or impossible. Only the last is
  refused.
- **Stranded backup GPT** — relocated to the end of the target, not left mid-disk on a larger
  drive where nothing will find it.
- Target smaller than source is refused; making it fit means shrinking filesystems, which is
  filesystem repair and out of scope. Larger is fine, trailing space reported not hidden.

`--used-only` reads allocation bitmaps and copies just the blocks in use — ten times faster
on a 2 TB drive holding 200 GB. Depends on the recovery subsystem's filesystem parsers, so
it lands after them; unrecognised filesystems are always copied whole rather than guessed at.

Verified on completion by re-reading both devices and comparing hashes. Bad sectors are
pattern-filled and logged by LBA, never silently zeroed — a zero-filled hole in a clone is
corruption that looks like data.

## Recovery

Heavy recovery defaults to working from an **image** rather than the live device — a
full-surface scan is thousands of seeks across a drive that's already failing, which is a
documented way to finish it off, and working from an image also makes writing output onto
the source structurally impossible.

**But that rule gets out of the way when it's ceremony.** A 4 TB disk with clean SMART and a
lost partition table doesn't need an eight-hour image before reading a handful of sectors,
and demanding one assumes scratch space the bench often doesn't have. `parts` in its
table-reading form defaults to live. A safety control people route around protects nobody.

Ceremony scales with the product of health and read intensity:

| Health | What running live costs you |
|---|---|
| `REUSE` | Runs. One informational line. No prompt, no flag. |
| `SCRATCH_ONLY` | Warning naming the attributes, then type the serial suffix. |
| `SCRAP` / `UNKNOWN` | Serious warning, type the serial suffix — not a `y`. |
| Mechanical failure | **Hard-blocked by default.** Names a recovery lab. Overridable only with `--override-mechanical-failure`. |

**Every level still proceeds if you insist**, including the last — it's your drive and your
client, and a tool that flatly refuses gets replaced by `dd`, which warns about nothing at
all. But mechanical failure is different in kind from the rest of the ladder: everything else
risks wearing out media that's already worn, while a failing head risks the client's only
copy in the next few minutes. So its override is a separate, deliberately verbose flag rather
than the one you'd already be passing, and `--accept-media-risk` deliberately doesn't reach
it — a batch script written for ordinary degraded drives can't silently escalate into
powering a dying one.

If a live run starts throwing IO errors that weren't there at enumeration, the drive is
degrading under the workload right now — it halts, reports what it got, and offers to hand
off to `doc image`, which reads in an order built to capture the most data before a dying
drive quits. Already-read regions carry over rather than starting again.

| Verb | What it does |
|---|---|
| `parts` | Restore a GPT from its backup header, or rebuild a table by scanning for filesystem superblocks and boot signatures. Fixes the very common "drive shows unallocated but everything's still there." |
| `carve` | Signature-based extraction — JPEG, PNG, PDF, OOXML, MP4, SQLite, PST. No filesystem needed. Fragmented files are the known limit of all carving and the docs say so. |
| `undelete` | Filesystem-aware, so files come back with original **names, paths, and timestamps**. NTFS `$MFT`, ext2/3/4 inodes and extent trees, FAT/exFAT directory entries. |

`parts --repair` writes to the device, so it's DEVIL DOC only, and it backs up the existing
sector 0 and GPT areas first. A partition repair you can't undo isn't a repair.

Filesystem *repair* stays out of scope — `fsck` and `chkdsk` mutate the patient and can
destroy recoverable data. corpsman extracts to a new location instead.

## Two modes

It boots into **AID STATION** — read-only. Destructive subcommands are hidden from
`--help` and absent from the MCP tool list, and they refuse at dispatch.

Enforcement happens at dispatch and again immediately before the first destructive
syscall — never once at startup. Hiding is a usability affordance, not the control, and a
mode checked at parse time would either lock out a session that armed mid-process or
outlive the expiry it was meant to respect.

Arming switches to **DEVIL DOC**. Doc picked up a rifle.

```
$ doc wipe /dev/sdb
  aid station can't do that.
  hold ctrl+alt+D 3s.

[hold]
  >> DEVIL DOC <<
  doc picked up a rifle.
  expires 10:00
```

**This is an anti-footgun control, not a security boundary**, and the docs say so rather
than implying otherwise. It does not stop anyone with a root shell — they can allocate a
pty with `ssh -t`, drive keys with `tmux send-keys` or `xdotool`, or skip the tool and run
`dd`. That's fine: anyone who can do that doesn't need corpsman to destroy a disk.

What it reliably stops is how drives actually get wiped by mistake — a pasted command
block, a recalled history entry, a copied wiki snippet, a CI job with the wrong argument,
a tab-completion that landed one device off.

- **`stdin` must be a TTY**, no override.
- **Held, not tapped** — 3 seconds in raw terminal mode.
- **Process-scoped**, never persisted, never inherited, expires after 10 minutes — and
  expiry is re-checked immediately before every destructive operation, not once at
  startup.
- Batch use is gated on `--confirm-token`, which is derived from a specific device's
  composite identity and can only be obtained by running `doc inspect` against that
  physical device first. A script copied from elsewhere carries tokens that resolve to
  nothing locally.

Handles SD cards, USB flash, floppies, CD-RW/DVD-RW, CD-R/DVD-R, IDE/PATA, SATA,
SAS/SCSI, SSD, NVMe, self-encrypting drives, and tape.

## Design principles

**It would rather refuse than lie.** The two ways a tool like this ruins someone's day
are touching the wrong device and claiming a result it did not achieve. Every decision
in the [design spec](docs/superpowers/specs/2026-08-10-corpsman-design.md) is biased
toward refusing to act and toward under-claiming.

- **Serial numbers are not identity.** USB bridges report the bridge's serial; cheap
  flash reports blank or duplicate serials. Devices are keyed on a composite token, and
  identity is re-resolved inside the write window so a hotplug between confirmation and
  execution cannot redirect the operation.
- **The system-disk check walks the whole chain.** `/` on a LUKS+LVM host is
  `/dev/mapper/vg-root` → `/dev/sda2` → `/dev/sda`. Refusing only the literal root device
  is not a safety check. LVM, LUKS, mdraid, ZFS, btrfs, APFS containers, and Storage
  Spaces all resolve to their physical members. No override flag.
- **The safe commands share the dangerous command's targeting code.** `inspect` and
  `test` run on the same `identify` and `topology` layers as `wipe`, so the high-risk
  code gets exercised harmlessly many times a day rather than only when it is armed.
- **Unreadable is not healthy.** A USB bridge that won't pass SMART through reports
  `UNKNOWN`, never `REUSE`.
- **Bad cables don't get blamed on drives.** SMART attribute 199 (UDMA CRC errors) is an
  *interface* fault — a marginal SATA cable or backplane. It is reported separately and
  excluded from the drive's health verdict, because counting it against the drive is how
  a good disk gets binned while the actual faulty cable stays in the machine and kills
  the next one.
- **Errors are fatal to the verdict.** Any IO error during a run, or a region left
  unwritten or unverified, drops the result to `DESTROY_REQUIRED`. A drive that threw
  errors mid-wipe never earns a clean certificate.
- **Health and sanitization are separate questions.** "Should this go back in service" and
  "is the data gone" are computed independently. A drive with remapped sectors that
  completed a verified hardware sanitize is `PURGED` — sanitize commands reach below the
  LBA layer, which is the whole reason they outrank overwrite. One that could only be
  overwritten is `CLEARED` with an explicit disclosure of the unreachable sectors. Binning
  every drive with remap history would condemn most used enterprise inventory.
- **Reading a dying drive isn't harmless either.** `image` refuses on a `SCRAP` verdict
  without `--accept-media-risk`, because retry loops on failing media are a known way to
  destroy data a recovery lab could have gotten. For an only-copy, the right advice is to
  stop and send it out, and the tool says so.
- **Never degrade silently.** Unprivileged? Refuse, don't return partial SMART. Unknown
  platform? Refuse, don't guess device conventions. Ambiguous tool output? Fail closed.
  Every external command runs under `LC_ALL=C` so a non-English locale can't quietly
  reshape a parse into a wrong verdict.

## Health verdicts

Weighted toward the five SMART attributes Backblaze's fleet data found carry nearly all
the predictive signal: 5, 187, 188, 197, 198.

**Thresholds and trend, not nonzero.** A 10 TB drive that remapped three sectors in year
one and none since is a working drive; a rule that condemns any nonzero count would bin
most of the used enterprise inventory that crosses a bench. What predicts failure is
magnitude against the drive's own spare capacity and *rate of change* — a pending count
climbing across two inspections a week apart is far worse than a static count ten times
its size. The ledger stores prior inspections keyed to the same identity, so trend is free
on any drive seen before; a first-time drive is judged on thresholds alone and says so.

Raw SMART fields are vendor-opaque and inconsistently encoded (temperature packed with
min/max, 64-bit counters split in halves). Anything the tool can't interpret confidently
is reported as raw and excluded from the verdict rather than guessed at.

Every verdict prints the attribute values, thresholds, and prior reading with delta that
produced it — no opaque scores.

| Verdict | Triage | Meaning |
|---|---|---|
| `REUSE` | return to duty | Clean. Back into service. |
| `SCRATCH_ONLY` | walking wounded | Works, but nothing irreplaceable goes on it. |
| `SCRAP` | expectant | Failing or failed. Does not go back into service. |
| `UNKNOWN` | unable to assess | SMART could not be read. Explicitly not the same as healthy. |

`inspect` calls `** CORPSMAN UP! **` when it finds a drive dying.

**The flavor stays in the terminal.** `--json`, the RMM check schema, the ledger, and the
customer-facing sanitization record use the plain technical identifiers only. A
certificate handed to a CPA or law-firm client says `DESTROY_REQUIRED`, never
"expectant," and an RMM check parses stable enum values that don't change because someone
improved a joke.

## Sanitization verdicts

Aligned to NIST SP 800-88 Rev. 1, because these words end up on a customer-facing
document. Full detail in the [wipe spec](docs/superpowers/specs/2026-08-10-zeroize-wipe-design.md).

| Verdict | Meaning |
|---|---|
| `PURGED` | A hardware sanitize command completed **and** was independently verified. Not reachable by overwrite alone on flash. |
| `CLEARED` | Full-surface overwrite, fully verified. Resists non-invasive recovery. |
| `CLEARED_SAMPLED` | As above, but only sampled verification ran. Explicitly weaker. |
| `INCOMPLETE` | A method failed, was advisory-only, or coverage was partial. |
| `DESTROY_REQUIRED` | No adequate software path. Physical destruction guidance is printed. |

## MCP server

`doc serve-mcp` runs permanently in AID STATION and exposes read-only tools — `list_devices`, `inspect_device`, `get_smart`,
`test_status`, `read_ledger` — over stdio, implemented in stdlib JSON-RPC so it needs no
SDK.

**`wipe`, `restore`, and `clone` are not exposed.** Not behind a flag, not behind a confirmation,
not present in the advertised tool list. The transport isn't a TTY, so the server can't
arm even in principle — but the tools are also simply absent, because defense in depth is
cheap here. Drive destruction requires a human who held a physical chord and typed an
identity token, and no chain of prompt text should be able to reach it. There is a test
asserting this.

`read_ledger` is **scoped and redacted by default** — it requires a device or session
selector, because the ledger holds device serials and job history for every customer the
shop has ever touched, and handing that wholesale to a model session is an exfiltration
surface no matter how much you trust the client. The server also does strict
`initialize` version negotiation and refuses unsupported protocol versions rather than
best-effort accepting fields it doesn't understand.

## Fleet monitoring

`--json` on any subcommand emits a versioned document, with exit codes suited to a
TacticalRMM check: `0` healthy, `1` warning, `2` critical, `3` unknown. Drive failures
surface on the RMM before the client calls.

## Records

Runs append to a hash-chained `~/.corpsman/ledger.jsonl`. `doc ledger --verify` revalidates
the chain.

**What the chain is actually worth:** it catches accidental corruption, partial writes, an
interrupted append, and edits by anyone who isn't the operator. Against the operator it's
worth nothing — they hold the file and can regenerate the whole chain, internally
consistent, in seconds. A hash chain can't make its own author accountable.

So the chain hash **does not go on a customer-facing certificate** unless it's been
externally anchored with a detached signature or third-party timestamp, neither of which
is in v1. The v1 certificate makes no integrity claim: it records what the tool did,
attested by the operator who ran it, and nothing more.

## Out of scope

Firmware-resident malware survives everything here — suspect it, destroy the device.
Lab-grade forensic recovery needing a cleanroom, head transplant, or PCB swap. Filesystem
*repair* in place. RAID reassembly. Encrypted volumes without the key — BitLocker,
FileVault, and LUKS are detected and reported, never guessed at. Any guarantee the media
still works afterward.

## License

MIT. See [LICENSE](LICENSE).
