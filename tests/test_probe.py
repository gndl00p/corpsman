from corpsman.run import RunResult
from corpsman.probe import Probe, TOOLS


def fake_runner(present):
    def _run(argv, timeout=60):
        name = argv[0]
        if name in present:
            return RunResult(rc=0, out="/usr/sbin/" + name, err="", found=True)
        return RunResult(rc=127, out="", err="binary not found", found=False)
    return _run


def test_reports_present_tool():
    p = Probe(runner=fake_runner({"smartctl"}))
    assert p.has("smartctl") is True


def test_reports_absent_tool():
    p = Probe(runner=fake_runner({"smartctl"}))
    assert p.has("hdparm") is False


def test_missing_lists_absent_tools():
    p = Probe(runner=fake_runner({"smartctl"}))
    missing = p.missing()
    assert "hdparm" in missing
    assert "smartctl" not in missing


def test_probe_runs_each_tool_once():
    calls = []

    def counting(argv, timeout=60):
        calls.append(argv[0])
        return RunResult(rc=127, out="", err="", found=False)

    p = Probe(runner=counting)
    p.has("smartctl")
    p.has("smartctl")
    p.has("smartctl")
    assert calls.count("smartctl") == 1


def test_unknown_tool_is_false_not_an_error():
    p = Probe(runner=fake_runner(set()))
    assert p.has("definitely-not-a-tool") is False


def test_tools_tuple_is_stable():
    assert "smartctl" in TOOLS
    assert "nvme" in TOOLS


def test_present_tool_with_nonzero_exit_is_still_present():
    # Real hdparm answers --version with rc=22 "unknown flag" while very
    # much existing. has() must key off `found` (did the exec succeed),
    # never `rc` (what the tool thought of our arguments).
    def weird(argv, timeout=60):
        return RunResult(rc=22, out="", err="version: unknown flag",
                         found=True)
    assert Probe(runner=weird).has("hdparm") is True


def test_absent_tool_with_zero_exit_is_still_absent():
    # The mirror case: found=False must win even if rc looks successful.
    def impossible(argv, timeout=60):
        return RunResult(rc=0, out="", err="", found=False)
    assert Probe(runner=impossible).has("smartctl") is False


def test_timed_out_probe_counts_as_unusable():
    def hangs(argv, timeout=60):
        return RunResult(rc=124, out="", err="timeout", found=True,
                         timed_out=True)
    assert Probe(runner=hangs).has("smartctl") is False
