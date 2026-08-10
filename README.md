# corpsman

Drive doctor for the bench. Figures out what the drive is, whether it is worth keeping,
captures what is on it, destroys what is on it, and leaves a record.

One file. Python 3.8+. Windows, macOS, Linux. No dependencies, no install step, runs
offline from a rescue USB. (The single file is a build artifact — source is one module per
layer, concatenated at build time, because a monolith mixing SMART parsing with the wipe
execution path is the wrong trade for a tool whose failure modes are destructive.)

Invoked as `doc`.

```
doc inspect /dev/sdb            # identity, SMART, health verdict
doc test    /dev/sdb            # self-tests, surface scan, throughput
doc recover /dev/sdb out.img    # error-tolerant capture off a dying drive
doc ledger  --verify
doc serve-mcp                   # read-only MCP server

doc wipe    /dev/sdb            # DEVIL DOC mode only
doc restore in.img /dev/sdb     # DEVIL DOC mode only
```

> **Status: design phase.** The spec is complete and has been through adversarial
> review. No implementation has landed. Do not point this at hardware.

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
- **Reading a dying drive isn't harmless either.** `recover` refuses on a `SCRAP` verdict
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

**`wipe` and `restore` are not exposed.** Not behind a flag, not behind a confirmation,
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
Lab-grade forensic recovery. Filesystem repair and file-level recovery, which are a
different job with better existing tools. Any guarantee the media still works afterward.

## License

MIT. See [LICENSE](LICENSE).
