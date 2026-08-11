"""Runtime capability detection for optional external tools.

External binaries are accelerators, never requirements. Their absence
lowers what the tool can claim, never what it can safely do, so every one
is probed and the result reported rather than assumed.
"""
from .run import run

TOOLS = (
    "smartctl",
    "nvme",
    "hdparm",
    "sg_sanitize",
    "sedutil-cli",
    "blkdiscard",
    "ddrescue",
)


class Probe(object):
    def __init__(self, runner=run):
        self._runner = runner
        self._cache = {}

    def has(self, name):
        # type: (str) -> bool
        if name not in self._cache:
            # 'command -v' is not available as an executable; probe the
            # binary itself with a harmless argument instead.
            result = self._runner([name, "--version"], timeout=10)
            # `has()` means usable, not merely present. A tool that cannot
            # answer --version within the timeout will not answer during a
            # real operation either -- it would just hang somewhere less
            # recoverable. This is a deliberate stance, not an oversight.
            self._cache[name] = result.found and not result.timed_out
        return self._cache[name]

    def missing(self):
        # type: () -> list
        return [t for t in TOOLS if not self.has(t)]
