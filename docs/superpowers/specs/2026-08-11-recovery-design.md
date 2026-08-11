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
2. **`SCRATCH_ONLY`** — one warning naming the attributes that produced the verdict, and a
   `y/N` confirmation. One keystroke.
3. **`SCRAP` or `UNKNOWN`** — the serious warning. Prints the specific failing attributes,
   states concretely that retry loops on failing media can convert a recoverable drive into
   one nobody can read, and that a recovery lab could likely still get this data today but
   may not after this run. Requires typing the device's serial suffix, not a `y`.
4. **Mechanical failure indicators** — SMART reporting spin-up failure, or IO errors already
   occurring during enumeration. This is the cleanroom case, where the correct advice is to
   power the drive down and send it out. The tool says so and requires
   `--accept-media-risk` explicitly. It still proceeds if the operator insists, because it
   is their drive and their client, and a tool that simply refuses gets replaced by `dd`.

`--accept-media-risk` pre-answers levels 2 through 4 for scripted use.

### Bailing out mid-run

If a live run starts throwing IO errors that were not present at enumeration, the drive is
degrading under the workload right now and continuing is the damage. The run halts, reports
what was recovered so far, and offers to switch to `doc image` — which is built for exactly
this and reads in an order designed to capture the most data before a dying drive gives up.
Accepting hands off to the imager with the already-read regions marked complete rather than
starting over.

## `doc image` on a failing drive

An earlier draft had `image` refuse on a `SCRAP` verdict without a flag. That was backwards.
Imaging a degraded drive **is** the recommended action — it is the thing that gets the data
somewhere safe, and the alternative is usually doing nothing or doing something worse.

`image` therefore warns and proceeds on confirmation for ordinary degraded drives. It
escalates to the level 4 treatment above only for mechanical-failure indicators, where
powering the drive at all is the risk and a lab is the honest recommendation.

## `doc recover parts` — partition recovery

The most common recoverable disaster and the fastest win in front of a client: the drive
enumerates, SMART is clean, and the machine reports it as unallocated. The data is all
still there; the table describing it is gone.

- Parse the existing MBR and GPT, including the GPT backup header at the end of the device,
  which frequently survives when the primary is destroyed. A great many "unallocated" drives
  are repaired by restoring the primary from the backup and nothing else.
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

- **NTFS:** parse `$MFT`, walk records with the in-use flag clear, resolve data runs and
  reassemble from them. Handles resident (small) files stored inline in the MFT record, and
  reads `$MFT` fragmentation via `$MFT`'s own attribute list. `$LogFile` and `$UsnJrnl` are
  read where present for recently-deleted entries the MFT has already reused.
- **ext2/3/4:** parse superblock and group descriptors, walk inode tables for inodes with
  a zeroed link count, and follow extent trees (ext4) or indirect block chains (ext2/3).
  ext4 zeroes extent info on delete more aggressively than ext3 did, so expected yield is
  lower and the tool reports that rather than implying parity.
- **FAT12/16/32 and exFAT:** directory entries marked deleted retain the full name minus its
  first character and the starting cluster; walk the FAT chain where it survives.

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
