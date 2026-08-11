# tests/test_guards.py
"""Guards on the two global constraints that must never regress."""
import ast
import os

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "src", "corpsman")

STDLIB_OK = {
    "argparse", "ast", "hashlib", "json", "os", "re", "subprocess", "sys",
    "time", "typing", "collections", "errno", "stat", "struct", "datetime",
}


def _python_files():
    for dirpath, _dirs, files in os.walk(SRC):
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def test_no_third_party_runtime_imports():
    """The zero-dependency guarantee is what lets this run from a rescue USB."""
    offenders = []
    for path in _python_files():
        with open(path) as f:
            tree = ast.parse(f.read(), path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root not in STDLIB_OK:
                        offenders.append((path, root))
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative import, ours
                    continue
                root = (node.module or "").split(".")[0]
                if root and root not in STDLIB_OK:
                    offenders.append((path, root))
    assert offenders == [], "third-party imports found: %r" % offenders


def test_no_device_write_paths_in_phase_one():
    """Phase 1 is read-only. Nothing here may open a device for writing."""
    banned = ("O_WRONLY", "O_RDWR", "O_TRUNC", "O_CREAT")
    offenders = []
    for path in _python_files():
        with open(path) as f:
            text = f.read()
        for token in banned:
            if token in text:
                offenders.append((path, token))
    assert offenders == [], "write flags found in read-only phase: %r" % offenders


def test_flavor_strings_are_not_in_non_cli_modules():
    """Corpsman voice belongs in terminal output only, never in data paths."""
    flavor = ("CORPSMAN UP", "expectant", "walking wounded", "DEVIL DOC")
    offenders = []
    for path in _python_files():
        if os.path.basename(path) == "cli.py":
            continue
        with open(path) as f:
            text = f.read()
        for word in flavor:
            if word in text:
                offenders.append((path, word))
    assert offenders == [], "flavor text outside cli.py: %r" % offenders
