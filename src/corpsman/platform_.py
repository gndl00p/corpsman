"""Platform detection and privilege check.

Unsupported platforms are refused rather than guessed at: device path
conventions differ enough that a best-guess backend is a hazard.
"""
import os
import sys

SUPPORTED = ("linux",)


def detect():
    # type: () -> str
    p = sys.platform
    if p.startswith("linux"):
        return "linux"
    if p == "darwin":
        return "darwin"
    if p in ("win32", "cygwin"):
        return "windows"
    return "unsupported"


def has_backend():
    # type: () -> bool
    return detect() in SUPPORTED


def is_privileged():
    # type: () -> bool
    if detect() == "windows":
        return False
    return hasattr(os, "geteuid") and os.geteuid() == 0
