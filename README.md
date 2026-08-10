# zeroize

Cross-platform CLI media sanitization for people who have to prove the data is gone.

One file. Python 3.8+. Runs on Windows, macOS, and Linux with no install step, offline,
from a rescue USB.

> **Status: design phase.** The specification is complete and has been through
> adversarial review. No implementation has landed yet. Do not point this at hardware.

## What it is for

Bench disposal and client-device offboarding, where two things have to be true at once:
the data is actually unrecoverable, and you can hand someone a record saying so.

Handles SD cards, USB flash, floppies, CD-RW/DVD-RW, CD-R/DVD-R, IDE/PATA, SATA,
SAS/SCSI, SSD, NVMe, self-encrypting drives, and tape.

## Design principles

**It would rather refuse than lie.** The two ways a tool like this ruins someone's day
are wiping the wrong device and claiming success it did not achieve. Every decision in
the [design spec](docs/superpowers/specs/2026-08-10-zeroize-design.md) is biased toward
refusing to act and toward under-claiming the result.

Concretely:

- **Serial numbers are not identity.** USB bridges report the bridge's serial, cheap
  flash reports blank or duplicate serials. Devices are keyed on a composite token, and
  the identity is re-resolved inside the write window so a hotplug between confirmation
  and execution cannot redirect the wipe.
- **The system-disk check walks the whole chain.** `/` on a LUKS+LVM host is
  `/dev/mapper/vg-root` → `/dev/sda2` → `/dev/sda`. Refusing only the literal root device
  is not a safety check. LVM, LUKS, mdraid, ZFS, btrfs, APFS containers, and Storage
  Spaces are all resolved to their physical members. There is no override flag.
- **Exclusive access is mandatory.** An unlocked raw write on Windows gets cached or
  dropped, and the tool would report a wipe that never happened. If the volume lock
  fails, the run aborts. There is no best-effort path.
- **Hardware sanitize commands are verified, not trusted.** ATA Secure Erase silently
  no-ops on a frozen drive and on many USB bridges. The freeze state is preflighted and
  the result is independently confirmed by reading the device back.
- **`blkdiscard`/TRIM is advisory.** The controller may ignore it and the FTL may return
  zeros over cells that still hold data. It never justifies a passing verdict alone.
- **Hidden areas are looked for.** HPA, DCO, reallocated sectors, and flash
  over-provisioning all survive a "complete" full-device overwrite. Finding one caps the
  verdict rather than being silently ignored.
- **Errors are fatal to the verdict.** Any IO error, unwritable region, or media defect
  drops the result to `INCOMPLETE` or `DESTROY_REQUIRED`. A dying drive never earns a
  clean certificate.

## Verdicts

Aligned to NIST SP 800-88 Rev. 1, because the words end up on a customer-facing document.

| Verdict | Meaning |
|---|---|
| `PURGED` | A hardware sanitize command completed **and** was independently verified. Not reachable by overwrite alone on flash. |
| `CLEARED` | Full-surface overwrite, fully verified. Resists non-invasive recovery. |
| `CLEARED_SAMPLED` | As above, but only sampled verification ran. Explicitly weaker. |
| `INCOMPLETE` | A method failed, was advisory-only, or coverage was partial. |
| `DESTROY_REQUIRED` | No adequate software path. Physical destruction guidance is printed. |

## Records

Runs append to a hash-chained `~/.zeroize/ledger.jsonl`, so retroactive edits are
detectable. The chain head prints at the end of each run for recording out-of-band.
`zeroize --verify-ledger` revalidates the chain. A human-readable per-run report is
written alongside it for attaching to a ticket.

## Out of scope

Firmware-resident malware survives every method here — if you suspect it, destroy the
device instead. Lab-grade forensic recovery is also out of scope, as is any expectation
that the media still works afterward.

## License

MIT. See [LICENSE](LICENSE).
