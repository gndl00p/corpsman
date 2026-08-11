# corpsman recovery — `doc recover`

**Status:** design. No implementation yet.
**Date:** 2026-08-11
**Parent design:** [2026-08-10-corpsman-design.md](2026-08-10-corpsman-design.md)

Getting client data back, as opposed to `doc image` which captures a device and
`doc restore` which writes one back.

    doc recover carve    <image> <outdir>   # signature-based extraction
    doc recover undelete <image> <outdir>   # filesystem-aware, with names and paths
    doc recover parts    <image|device>     # scan for lost partitions, read-only
    doc recover parts --repair <device>     # write a rebuilt table  [DEVIL DOC]

## Working from an image, and when that rule should get out of the way

Imaging first is the right default for heavy recovery on a compromised drive, for three
real reasons:

- A full-surface scan is thousands of seeks. On a drive that is already failing — which is
  the drive people want recovered — that workload is a well-documented way to finish it off.
  The first thing a recovery house does is image the patient and then never touch it again.
- Working from an image makes writing output onto the source structurally impossible,
  rather than a check that can be forgotten.
- A failing drive gives different answers on successive reads, so a live run cannot be
  repeated or reviewed. An image can.

**None of that applies to a healthy drive with a logical problem**, which is the majority of
real cases. A 4 TB disk with a clean SMART report and a lost partition table does not need
an eight-hour image before reading a handful of sectors, and demanding one assumes free
scratch space the bench frequently does not have. A rule that is ceremony in the common case
gets worked around, and a safety control people route around protects nobody.

So live access is a first-class path, and the ceremony scales with actual risk rather than
being uniform.

### Risk is the product of health and read intensity

Verbs differ enormously in how hard they work the media:

| Verb | Read pattern | Intensity |
|---|---|---|
| `parts` (table read) | A few dozen sectors at known offsets, plus the GPT backup at the end | Trivial |
| `parts` (signature scan) | Full surface, sequential | Heavy |
| `carve` | Full surface, sequential | Heavy |
| `undelete` | Metadata regions, then targeted extent reads — fewer bytes than carving, but seek-heavy | Moderate |

`parts` in its table-reading form **defaults to live**, because imaging 4 TB to read sector 0
and a backup GPT header is absurd. `carve` and `undelete` default to image-based and take
`--live` to override.

### The ladder

What the operator has to do to run live, by health verdict:

1. **`REUSE`** — runs. A single informational line notes that imaging first is safer if the
   data is irreplaceable. No prompt, no flag. This is the common case and it should feel
   like the tool is helping.
2. **`SCRATCH_ONLY`** — warning naming the attributes that produced the verdict, then the
   device's serial suffix typed to proceed.

   An earlier draft made this a `y/N` keystroke. That was too thin for a drive the tool has
   *already assessed as at-risk*: a `y` is reflexive, it is the answer to every prompt, and
   it carries no evidence the operator read which attributes fired. Typing the suffix costs
   about two seconds and is the difference between acknowledging and dismissing. Level 1
   remains frictionless, which is where the ceremony budget belongs.
3. **`SCRAP` or `UNKNOWN`** — the serious warning. Prints the specific failing attributes,
   states concretely that retry loops on failing media can convert a recoverable drive into
   one nobody can read, and that a recovery lab could likely still get this data today but
   may not after this run. Serial suffix typed to proceed.
4. **Mechanical failure indicators** — SMART reporting spin-up failure, a failing head, or IO
   errors already occurring during enumeration. **Hard-blocked by default.**

   This is the cleanroom case and it is genuinely different in kind from the levels above.
   Everything else on this ladder risks wearing out media that is already worn. This risks
   the client's only copy, in the next few minutes, to a head that is failing right now — and
   it is the one case where the drive going back in a box unpowered is worth real money to
   them.

   It remains overridable, because it is the operator's drive and their client, and a tool
   that flatly refuses gets replaced by `dd`, which warns about nothing at all. But the
   override is **`--override-mechanical-failure`**, which is deliberately verbose, cannot be
   typed by accident, and is not what anyone reaches for reflexively. Interactive use
   additionally requires typing the serial suffix after reading what the tool expects to
   happen.

`--accept-media-risk` pre-answers levels 2 and 3 for scripted use. **It does not reach level
4** — that needs its own flag, so a batch script written for ordinary degraded drives cannot
silently escalate into powering a mechanically failing one.

### Bailing out mid-run

If a live run starts throwing IO errors that were not present at enumeration, the drive is
degrading under the workload right now and continuing is the damage. The run halts, reports
what was recovered so far, and offers to switch to `doc image` — which is built for exactly
this and reads in an order designed to capture the most data before a dying drive gives up.
Handing off carries forward **only regions whose contents were checksummed and verified at
read time**. Everything else is re-read. Marking unverified reads from a drive that was
visibly degrading as complete would bake the least trustworthy data into the image and call
it done — reads taken from failing media in the minutes before it faulted are exactly the
ones most likely to be wrong.

## `doc image` on a failing drive

An earlier draft had `image` refuse on a `SCRAP` verdict without a flag. That was backwards.
Imaging a degraded drive **is** the recommended action — it is the thing that gets the data
somewhere safe, and the alternative is usually doing nothing or doing something worse.

`image` therefore warns and proceeds on confirmation for ordinary degraded drives. It
escalates to the level 4 treatment above only for mechanical-failure indicators, where
powering the drive at all is the risk and a lab is the honest recommendation — and there,
as everywhere else, `--override-mechanical-failure` still lets the operator proceed.

## `doc recover parts` — partition recovery

The most common recoverable disaster and the fastest win in front of a client: the drive
enumerates, SMART is clean, and the machine reports it as unallocated. The data is all
still there; the table describing it is gone.

- Parse the existing MBR and GPT, including the GPT backup header at the end of the device,
  which frequently survives when the primary is destroyed. A great many "unallocated" drives
  are repaired by restoring the primary from the backup and nothing else.
- **The backup is never trusted just because it parses.** A backup header can be stale,
  describing a layout from before the disk was repartitioned, and restoring it yields a table
  that validates cleanly and points at the wrong places — worse than an obviously broken one,
  because every tool downstream will believe it. Before proposing a restore the tool verifies
  header and entry-array CRCs, checks usable-range and partition-bound self-consistency, and
  **corroborates each partition start against a filesystem signature actually present at that
  offset on the media**. A backup that describes partitions where no filesystem begins is
  reported as stale and is not offered as a repair.
- When both are gone, scan the full surface for filesystem superblocks and boot signatures —
  NTFS `$Boot`, ext2/3/4 superblocks at the standard offsets and their backups, FAT boot
  sectors, APFS and HFS+ headers, exFAT — and reconstruct a plausible table from what is
  found and where.
- Report candidates with confidence and let the operator choose. Never auto-apply.

**`--repair` writes to the device and is therefore DEVIL DOC only**, gated by the same
topology refusals, identity confirmation, and device locking as `wipe`. Before writing, the
current sector 0 and the GPT areas are backed up into the ledger directory so the operation
is reversible. A partition-table repair that cannot be undone is not a repair, it is a
second disaster.

## `doc recover carve` — signature-based extraction

Filesystem-agnostic. Scan the image for known file headers, determine extent by footer or
by structure, and write out what is found. Recovers data from a filesystem too damaged to
parse, and from unallocated space where the directory entry is long gone.

- Ships with signatures for the formats that matter in an MSP context: JPEG, PNG, GIF, TIFF,
  HEIC, PDF, ZIP and the OOXML family that rides on it (`.docx`, `.xlsx`, `.pptx`), legacy
  OLE2 Office, MP4/MOV, SQLite, PST/OST, and common archive formats.
- **A signature match alone never produces an artifact.** Header bytes collide constantly
  inside compressed, encrypted, and already-carved data, and a carver that trusts headers
  emits a directory full of unopenable files that a client will treat as recovered data.
  Every candidate must additionally pass structural validation appropriate to its format —
  a matching footer, an internal length field that lands where it should, a CRC that
  verifies, a parseable container header — before it is written. Candidates that match a
  header and fail validation are counted in the manifest but not emitted.
- Fragmented files are the known limit of all carving and the docs say so plainly. A carver
  recovers contiguous runs; a file scattered across the disk comes back truncated or not at
  all. Overstating this is how a client is told their data is safe when it is not.
- Output is named by offset and type, deduplicated by hash, and accompanied by a manifest
  recording where each artifact came from. Carved files have no original names — that is
  what `undelete` is for.

## `doc recover undelete` — filesystem-aware

Higher-value output than carving, because files come back with their **original names,
paths, timestamps, and sizes**, and because the filesystem's own records identify extents
so fragmented files can be reassembled correctly.

### Supported cases are declared, and unsupported ones are refused rather than approximated

The failure mode that matters here is the same one the rest of this project keeps correcting:
emitting output that looks successful and is wrong. A file recovered with the wrong bytes,
handed to a client as their recovered document, is worse than reporting that it could not be
recovered — they will find out later, having already deleted the source.

So each parser declares a **capability tier**, and anything outside it is reported as
`UNSUPPORTED` with the reason, never reconstructed on a best guess.

- **NTFS.** Tier one is non-resident, uncompressed, unencrypted files whose data runs
  resolve completely within the base MFT record — plus resident files stored inline, which
  are trivially correct. Everything else is named and refused rather than approximated:
  attribute lists spanning multiple records, sparse and compressed attributes with their own
  run encoding, encrypted files, alternate data streams, and transactionally-deleted files
  whose state lives in `$LogFile`. `$MFT`'s own fragmentation is resolved via its attribute
  list because without it the parser silently reads the wrong records. `$LogFile` and
  `$UsnJrnl` are read where present to *name* recently-deleted entries the MFT has already
  reused, which is useful even when the content is gone.
- **ext2/3/4.** Tier one is inodes with a zeroed link count whose extent tree or indirect
  chain resolves completely and whose metadata checksums validate. ext4 zeroes extent info
  on delete far more aggressively than ext3 did, so the honest expectation is not merely
  *lower yield* but that a partially-zeroed extent tree can resolve to blocks that now belong
  to something else — plausible-looking output that is wrong. Checksum validation failure,
  inline-to-extent transitions, delayed allocation, and encrypted inodes all return
  `UNSUPPORTED` rather than content.
- **FAT12/16/32 and exFAT.** Deleted directory entries retain the name minus its first
  character and the starting cluster. The FAT chain for a deleted file is typically already
  freed, so only contiguous runs are recoverable with confidence; non-contiguous files are
  reported as name-known, content-unrecoverable.

Every emitted artifact carries the tier and the validation that passed, so a manifest
distinguishes "byte-exact and verified" from "recovered, unverified" without the operator
having to know NTFS internals.

Each filesystem parser is a self-contained module reading from a byte-range interface, so it
is tested against small crafted images committed to the repo rather than against hardware.

## External tools as accelerators

Consistent with how `hdparm`, `nvme-cli`, and `smartctl` are already treated: if `testdisk`
or `photorec` is present, corpsman can drive it and normalise its output into the same
manifest and ledger format. Absent, the built-in implementations do the work.

This is not a fallback hierarchy where the external tool is the real implementation — the
built-ins are first-class and independently tested. PhotoRec in particular has a signature
corpus no reimplementation will match, so offering it where installed is honest rather than
proud.

## Output safety

- Refuses to write output onto the source device, or onto the filesystem holding the source
  image, unless explicitly overridden.
- Checks free space against a size estimate **before** starting rather than failing partway
  through a multi-hour extraction.
- Every run writes a manifest — source image hash, verb, parameters, artifact count and
  bytes, per-artifact origin offset and hash — and appends a summary to the ledger.

## Explicitly still out of scope

- **Filesystem repair.** `fsck`, `chkdsk`, and rebuilding a mountable filesystem in place.
  Repair mutates the patient and can destroy recoverable data; corpsman extracts to a new
  location instead. This is a deliberate line, not an omission.
- RAID reassembly and parity reconstruction across member disks.
- Encrypted volumes without the key. BitLocker, FileVault, and LUKS containers are detected
  and reported as encrypted, never guessed at.
- Physically damaged media requiring a cleanroom, head transplant, or PCB swap. The tool
  should say so and name that as the correct next step.

## Build order

The four capabilities differ enormously in effort, and shipping them in value order means
something useful exists early:

1. **`parts`** — smallest and highest immediate value. Restoring a GPT from its backup
   header fixes a large share of real cases in seconds.
2. **External tool wrapping** — near-free capability once the manifest format exists.
3. **`carve`** — self-contained, no filesystem knowledge, testable against crafted images.
4. **`undelete`** — by far the largest. A correct NTFS `$MFT` parser is a serious piece of
   work with a long tail of edge cases, and ext4 extent trees are not far behind. Sequence
   it as NTFS first, since it is the overwhelming majority of client data in this shop.
