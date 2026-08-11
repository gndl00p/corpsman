"""Single chokepoint for every external process invocation."""
import os
import subprocess
from typing import List, NamedTuple


class RunResult(NamedTuple):
    rc: int
    out: str
    err: str
    found: bool
    timed_out: bool = False


def run(argv, timeout=60):
    # type: (List[str], int) -> RunResult
    """Run a command with a pinned C locale.

    Locale is forced because smartctl, hdparm and friends translate their
    output, which silently breaks parsing and feeds a wrong health verdict
    into everything downstream.
    """
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    try:
        p = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            universal_newlines=True,
        )
    except (OSError, FileNotFoundError):
        return RunResult(rc=127, out="", err="binary not found", found=False)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        out, err = p.communicate()
        return RunResult(rc=124, out=out or "", err="timeout", found=True,
                          timed_out=True)
    return RunResult(rc=p.returncode, out=out or "", err=err or "", found=True)
