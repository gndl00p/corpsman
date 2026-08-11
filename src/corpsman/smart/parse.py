"""Parse smartctl --json output.

JSON is used rather than text because smartctl's human output has changed
shape repeatedly across releases and is locale-sensitive. Raw attribute
values are vendor-opaque in general; only the small set with reliable
cross-vendor semantics is consumed by the verdict, and anything else is
carried through for display without interpretation.
"""
import json


class SmartData(object):
    __slots__ = ("available", "overall_passed", "attrs", "power_on_hours",
                 "source", "unreadable_reason", "model", "serial")

    def __init__(self, available=False, overall_passed=None, attrs=None,
                 power_on_hours=None, source="smartctl",
                 unreadable_reason=None, model=None, serial=None):
        self.available = available
        self.overall_passed = overall_passed
        self.attrs = attrs if attrs is not None else {}
        self.power_on_hours = power_on_hours
        self.source = source
        self.unreadable_reason = unreadable_reason
        self.model = model
        self.serial = serial


def parse_smartctl_json(text):
    # type: (str) -> SmartData
    if not text or not text.strip():
        return SmartData(unreadable_reason="no output from smartctl")
    try:
        doc = json.loads(text)
    except ValueError as exc:
        return SmartData(unreadable_reason="unparseable smartctl output: %s" % exc)
    if not isinstance(doc, dict):
        return SmartData(unreadable_reason="unexpected smartctl document shape")

    table = (doc.get("ata_smart_attributes") or {}).get("table")
    if not isinstance(table, list) or not table:
        return SmartData(unreadable_reason="no ATA SMART attribute table present")

    attrs = {}
    for row in table:
        if not isinstance(row, dict):
            continue
        aid = row.get("id")
        raw = (row.get("raw") or {}).get("value")
        if isinstance(aid, int) and isinstance(raw, int):
            attrs[aid] = raw

    status = doc.get("smart_status")
    passed = status.get("passed") if isinstance(status, dict) else None
    hours = (doc.get("power_on_time") or {}).get("hours")

    return SmartData(
        available=True,
        overall_passed=passed if isinstance(passed, bool) else None,
        attrs=attrs,
        power_on_hours=hours if isinstance(hours, int) else None,
        model=doc.get("model_name"),
        serial=doc.get("serial_number"),
    )
