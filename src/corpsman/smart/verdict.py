"""Health verdict from SMART attributes.

Weighted toward the five attributes Backblaze fleet data found carry nearly
all the predictive signal: 5, 187, 188, 197, 198.

Thresholds and rate of change, not nonzero. A drive that remapped a handful
of sectors years ago and none since is a working drive, and a rule that
condemns any nonzero count would bin most of the used inventory that
crosses a bench.

The four scored attributes are not equally strong signals, and their
thresholds are ordered accordingly. 197 (Current_Pending_Sector) and 198
(Offline_Uncorrectable) mean the drive cannot read those sectors right now.
187 (Reported_Uncorrect) means the host already received data back that the
drive could not correct -- a realized failure. 5 (Reallocated_Sector_Ct)
means a sector was successfully remapped, possibly years ago, with no
recurrence since -- real but comparatively weak evidence. The sharper
signals (187/197/198) threshold lower than the weaker one (5); see
test_pending_sectors_threshold_is_tighter_than_reallocated.

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
    5: 48,     # Reallocated_Sector_Ct  -- completed, successful remaps
    187: 8,    # Reported_Uncorrect     -- host already got bad data back
    197: 8,    # Current_Pending_Sector -- unreadable RIGHT NOW
    198: 8,    # Offline_Uncorrectable  -- failed even offline
}

# A pending/reallocated count that grows between inspections is a stronger
# signal than a larger static one, but a delta of exactly one is well
# within normal aging noise -- a drive that picks up a single sector over a
# year of service is not in trouble. `prior` comes from the ledger and may
# be minutes or months old, and assess() has no timestamp, so the delta's
# magnitude is the only qualifier available. Revisit when the ledger
# supplies elapsed time.
_GROWTH_FLOOR = 2

# Above zero but below THRESHOLDS puts the drive in SCRATCH_ONLY.
#
# 188 is deliberately absent. Many vendors pack three 16-bit sub-counters
# into its 48-bit raw field, so raw.value is a composite number rather than
# a count of timeouts -- a drive with a single benign event can report a
# value in the millions. Thresholding it like 5/197/198 would condemn
# healthy drives, so it is reported informationally and never moves the
# verdict. See _INFORMATIONAL below.
_WATCH = (5, 187, 197, 198)

# Carried in the report for the operator to see, but never scored.
_INFORMATIONAL = (188,)

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

    for aid in _INFORMATIONAL:
        value = smart.attrs.get(aid, 0)
        if value:
            reasons.append(
                "%s (informational: vendor-packed composite, not a count)"
                % _label(aid, value)
            )

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

    # Unreadable is not the same as healthy, and neither is unmeasured. A
    # table that never reported any of the four scored attributes has told
    # us nothing about the drive's condition -- treating that silence as
    # "checked, clean" is the same mistake as treating available=False as
    # REUSE, just arriving through the parsed-table door instead of the
    # smartctl-invocation door.
    present = [aid for aid in _WATCH if aid in smart.attrs]
    if not present:
        return Verdict(
            HEALTH_UNKNOWN,
            ["SMART parsed but reported none of the predictive attributes "
             "(5, 187, 197, 198); nothing relevant was measured"],
            cabling,
        )

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
        # Rate of change matters more than magnitude, but a delta of one is
        # within normal aging noise -- require at least _GROWTH_FLOOR.
        if (prior is not None and aid in prior and
                (value - prior[aid]) >= _GROWTH_FLOOR):
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
    # NOTE: the brief's code replaced `reasons` here with a brand-new
    # single-element list, silently dropping any accumulated informational
    # note (e.g. attribute 188) and losing it from the report. Appending
    # instead of replacing preserves those notes while still explaining why
    # the verdict landed on REUSE. The wording also now names the
    # attributes explicitly and says they were present, so this case reads
    # differently from the "never reported at all" UNKNOWN case above.
    reasons.append(
        "predictive attributes (5, 187, 197, 198) present and read zero"
    )
    return Verdict(HEALTH_REUSE, reasons, cabling)
