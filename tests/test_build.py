# tests/test_build.py
"""Verify the single-file build artifact.

`doc` is a distribution artifact, not the source layout -- see build.py's
module docstring. These tests hold it to the same bar as the source tree
(test_guards.py's zero-third-party-import guarantee) plus the two things
unique to a build step: it must actually run, and it must actually behave
like the package it was built from, not just import cleanly.
"""
import argparse
import ast
import io
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ROOT)
sys.path.insert(0, ROOT)

import build  # noqa: E402

FIXTURE_ROOT = os.path.join(ROOT, "tests", "fixtures", "linux", "luks-lvm")

# Same allowlist as tests/test_guards.py, plus 'types': the build's own
# loader bootstrap (not part of src/corpsman) needs types.ModuleType.
STDLIB_OK = {
    "argparse", "hashlib", "json", "os", "subprocess", "sys", "typing", "types",
}


_RUN_VIA_DOC_SCRIPT = '''
import argparse
import importlib.machinery
import importlib.util
import io
import json
import sys

# spec_from_file_location can't infer a loader for a file with no .py
# extension, so the loader is constructed explicitly.
loader = importlib.machinery.SourceFileLoader("doc_under_test", sys.argv[1])
spec = importlib.util.spec_from_loader("doc_under_test", loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)

cli = sys.modules["corpsman.cli"]
cli.platform_.is_privileged = lambda: True
cli.platform_.has_backend = lambda: True

args = argparse.Namespace(root=sys.argv[2], device=None, json=True)
stream = io.StringIO()
cli.cmd_inspect(args, stream=stream)
sys.stdout.write(stream.getvalue())
'''


def test_build_produces_a_runnable_file():
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "doc")
        build.build(out)
        assert os.path.exists(out)
        r = subprocess.run([sys.executable, out, "--help"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert r.returncode == 0
        assert b"inspect" in r.stdout


def test_inspect_subcommand_help_exits_zero():
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "doc")
        build.build(out)
        r = subprocess.run([sys.executable, out, "inspect", "--help"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert r.returncode == 0, r.stderr.decode()


def test_built_file_has_no_corpsman_imports_left():
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "doc")
        build.build(out)
        with open(out) as f:
            text = f.read()
        assert "from corpsman" not in text
        assert "import corpsman" not in text


def test_built_file_has_no_executable_relative_imports():
    """Stronger than the substring check above: parse the built file and
    confirm there is no *statement* that is a relative import or that
    names the corpsman package, anywhere the outer parser can see it as
    code (as opposed to data inside an embedded module's source string).

    A leftover top-level `from . import x` in a file run directly as
    `__main__` raises ImportError immediately -- "no known parent
    package" -- which is exactly the failure mode this build exists to
    avoid.
    """
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "doc")
        build.build(out)
        with open(out) as f:
            text = f.read()
        tree = ast.parse(text, out)

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0, (
                "relative import statement left executable in the "
                "built file: level=%d module=%r" % (node.level, node.module)
            )
            assert node.module != "corpsman" and not (
                node.module or ""
            ).startswith("corpsman."), (
                "import of the corpsman package left in the built file: %r"
                % node.module
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "corpsman" and not alias.name.startswith(
                    "corpsman."
                ), "import of the corpsman package left in the built file: %r" % alias.name


def test_built_file_imports_only_stdlib():
    """AST-walk the built artifact for third-party imports, the same way
    test_guards.py does for the source tree -- but here it has to walk
    both the bootstrap loader code AND every embedded module's source,
    since the latter is only visible to the outer parser as string
    constants (assignments into _SOURCES), not as Import/ImportFrom nodes.
    Checking only the bootstrap would miss a third-party import buried
    inside an embedded module.
    """
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "doc")
        build.build(out)
        with open(out) as f:
            text = f.read()
        tree = ast.parse(text, out)

    def offenders_in(subtree):
        found = []
        for node in ast.walk(subtree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_name = alias.name.split(".")[0]
                    if root_name not in STDLIB_OK:
                        found.append(root_name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                root_name = (node.module or "").split(".")[0]
                if root_name and root_name not in STDLIB_OK:
                    found.append(root_name)
        return found

    offenders = offenders_in(tree)

    embedded_source_count = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if not (isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == "_SOURCES"):
            continue
        source_text, _is_pkg = ast.literal_eval(node.value)
        embedded_source_count += 1
        offenders.extend(offenders_in(ast.parse(source_text)))

    # Sanity check the check itself: if this is 0, the pattern match above
    # silently found nothing and the whole test would pass vacuously.
    assert embedded_source_count == len(build.MODULES)
    assert offenders == [], "third-party imports found in built file: %r" % offenders


def test_build_fails_loudly_if_a_source_module_is_missing_from_the_list():
    """build.py's MODULES list is the one thing that needs manual upkeep
    when a file is added to src/corpsman/ -- there is no shim or regex step
    left to silently paper over a miss. A module missing from MODULES is
    invisible to the built `doc`: it produces a file that imports cleanly
    and then fails on a real device list the first time something reaches
    for the missing name, which is exactly the failure mode this build
    exists to avoid.

    This proves that drift is caught at test time -- the moment someone
    adds a file and forgets to list it -- rather than whenever the missing
    module first gets exercised at runtime. It compares MODULES against an
    actual os.walk() of src/corpsman/, not against a second hardcoded list,
    so it can't drift out of sync with reality the same way MODULES itself
    could.
    """
    actual = set()
    for dirpath, _dirs, files in os.walk(build.SRC):
        for name in files:
            if name.endswith(".py"):
                actual.add(os.path.relpath(os.path.join(dirpath, name), build.SRC))

    listed = set(relpath for _name, relpath in build.MODULES)

    assert listed == actual, (
        "build.py's MODULES list has drifted from src/corpsman/: "
        "missing from MODULES=%r, listed but no longer on disk=%r"
        % (actual - listed, listed - actual)
    )


def test_built_file_matches_source_package_report():
    """The built file must actually WORK, not merely parse and import.

    cmd_inspect refuses to run unprivileged, which blocks a direct
    subprocess comparison in CI/sandboxes that aren't root. Instead this
    loads the built file as a module and calls cmd_inspect directly after
    monkeypatching the privilege gate -- the same technique
    tests/test_cli_inspect.py already uses against the source package.
    This does not weaken the privilege check itself; it only bypasses it
    for the comparison, the same way test_cli_inspect.py does.

    That load happens in a SUBPROCESS (via _RUN_VIA_DOC_SCRIPT), not
    in-process: loading `doc` registers fake modules under sys.modules
    ["corpsman"], ["corpsman.cli"], etc, and once other test files in the
    same pytest session have already imported the real corpsman package,
    replacing those sys.modules entries in-process leaves stale bound
    references (e.g. an already-imported `TopologyError` class) pointing
    at now-orphaned module objects -- an isinstance/except mismatch that
    broke test_topology_linux.py the first time this test did the
    swap-and-restore in-process. A subprocess keeps the pollution
    contained to a process that exits right after, so it can never leak
    into other tests.
    """
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "doc")
        build.build(out)

        script = os.path.join(tmp, "run_via_doc.py")
        with open(script, "w") as f:
            f.write(_RUN_VIA_DOC_SCRIPT)

        r = subprocess.run(
            [sys.executable, script, out, FIXTURE_ROOT],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert r.returncode == 0, r.stderr.decode()
        built_report = json.loads(r.stdout.decode())

    import corpsman.cli as source_cli

    source_cli.platform_.is_privileged = lambda: True
    source_cli.platform_.has_backend = lambda: True

    args = argparse.Namespace(root=FIXTURE_ROOT, device=None, json=True)
    source_stream = io.StringIO()
    source_cli.cmd_inspect(args, stream=source_stream)
    source_report = json.loads(source_stream.getvalue())

    assert built_report == source_report
    # Confirm the comparison is actually exercising real device data, not
    # two empty reports agreeing with each other vacuously.
    assert built_report["devices"]
