"""Health verdict from SMART attributes.

Weighted toward the five attributes Backblaze fleet data found carry nearly
all the predictive signal: 5, 187, 188, 197, 198.

Thresholds and rate of change, not nonzero. A drive that remapped a handful
of sectors years ago and none since is a working drive, and a rule that
condemns any nonzero count would bin most of the used inventory that
crosses a bench.

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
    5: 64,     # Reallocated_Sector_Ct
    197: 16,   # Current_Pending_Sector
    198: 16,   # Offline_Uncorrectable
}

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
        # Rate of change matters more than magnitude. A pending count that
        # climbed since the last inspection is worse than a larger static one.
        if prior is not None and aid in prior and value > prior[aid]:
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
    # the verdict landed on REUSE.
    reasons.append("no predictive attributes above zero")
    return Verdict(HEALTH_REUSE, reasons, cabling)
