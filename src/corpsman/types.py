# src/corpsman/types.py
"""Core value types.

Identity deliberately does not key on serial number alone. USB bridges
report the bridge's serial rather than the drive's, and cheap flash reports
blank or duplicated serials, so two devices in one dock can produce the same
string. The token combines the stable OS instance path with geometry and
identifiers so that neither a blank serial nor a /dev renumbering can make
two devices look like one.
"""
import hashlib

HEALTH_REUSE = "REUSE"
HEALTH_SCRATCH_ONLY = "SCRATCH_ONLY"
HEALTH_SCRAP = "SCRAP"
HEALTH_UNKNOWN = "UNKNOWN"

_TOKEN_LEN = 12


class Device(object):
    __slots__ = (
        "path", "name", "instance_path", "model", "serial", "wwn",
        "size_bytes", "logical_sector", "physical_sector", "bus",
        "rotational", "removable",
    )

    def __init__(self, path, name, instance_path, model, serial, wwn,
                 size_bytes, logical_sector, physical_sector, bus,
                 rotational, removable):
        self.path = path
        self.name = name
        self.instance_path = instance_path
        self.model = model
        self.serial = serial
        self.wwn = wwn
        self.size_bytes = size_bytes
        self.logical_sector = logical_sector
        self.physical_sector = physical_sector
        self.bus = bus
        self.rotational = rotational
        self.removable = removable

    @property
    def identity_token(self):
        # type: () -> str
        # /dev name is deliberately excluded: it is not stable across replug.
        parts = [
            self.instance_path or "",
            self.wwn or "",
            str(self.size_bytes),
            self.model or "",
            self.serial or "",
            self.bus or "",
        ]
        blob = "\x1f".join(parts).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:_TOKEN_LEN]

    def __repr__(self):
        return "<Device %s %s %s>" % (self.name, self.model, self.identity_token)
