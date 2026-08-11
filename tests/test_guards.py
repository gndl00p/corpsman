# tests/test_guards.py
"""Guards on the two global constraints that must never regress."""
import ast
import os

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "src", "corpsman")

# This is an allowlist, not a convenience list: add a name only when a module
# under src/corpsman genuinely imports it. Every unused entry is a hole
# nobody is watching.
STDLIB_OK = {
    "argparse", "hashlib", "json", "os", "subprocess", "sys", "typing",
}


def _python_files():
    for dirpath, _dirs, files in os.walk(SRC):
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def test_no_third_party_runtime_imports():
    """The zero-dependency guarantee is what lets this run from a rescue USB.

    Does not catch: __import__("requests"), importlib.import_module("numpy"),
    or any other runtime string-based import. Those are invisible to a static
    ast walk over Import/ImportFrom nodes, and a string-argument analysis
    isn't worth the complexity here — this is a guard against ordinary
    top-of-file imports, not against deliberate obfuscation.
    """
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
    """Phase 1 is read-only. Nothing here may open a device for writing, or
    shell out to a binary that writes to one.

    This is a substring smoke alarm, not a lock. It catches the obvious,
    idiomatic mistake (naming os.O_WRONLY, or shelling out to `dd`) and
    nothing more subtle. It does NOT catch: a numeric os.open() mode (e.g.
    os.open(path, 1)), open(path, mode) where mode is a variable, ctypes
    calls into libc, mmap with PROT_WRITE, shutil.copy/move onto a device
    path, or os.truncate/os.ftruncate. Treat it as a tripwire for one
    specific class of mistake, not a guarantee the tree has no destructive
    code path.

    A line may opt out with a trailing `# corpsman: allow-write-flag`
    marker. That makes every exception greppable and deliberate rather
    than forcing someone to weaken the guard when Phase 2 (wipe/clone/
    restore) lands and legitimately needs to name these constants — even
    if only to refuse them or document why they are absent.
    """
    banned_flags = ("O_WRONLY", "O_RDWR", "O_TRUNC", "O_CREAT")
    banned_binaries = ("dd", "wipefs", "mkfs", "sgdisk", "blkdiscard", "shred")
    opt_out = "corpsman: allow-write-flag"
    offenders = []
    for path in _python_files():
        with open(path) as f:
            lines = f.readlines()
        for lineno, line in enumerate(lines, 1):
            if opt_out in line:
                continue
            for token in banned_flags:
                if token in line:
                    offenders.append((path, lineno, token))
            for binary in banned_binaries:
                # Whole argv-style tokens only (quoted "dd" or 'dd'), not a
                # bare substring — a bare "dd" would match "address" and
                # half the comments in the tree.
                if ('"%s"' % binary) in line or ("'%s'" % binary) in line:
                    offenders.append((path, lineno, binary))
    assert offenders == [], (
        "write flags or destructive binaries found in read-only phase: %r"
        % offenders
    )


def test_flavor_strings_are_not_in_non_cli_modules():
    """Corpsman voice belongs in terminal output only, never in data paths."""
    flavor = ("CORPSMAN UP", "expectant", "walking wounded", "DEVIL DOC")
    offenders = []
    for path in _python_files():
        # Match the relative path at the package root, not the bare
        # basename — a bare basename check would silently exempt any future
        # file anywhere in the tree that happens to be named cli.py too.
        if os.path.relpath(path, SRC) == "cli.py":
            continue
        with open(path) as f:
            text = f.read()
        for word in flavor:
            if word in text:
                offenders.append((path, word))
    assert offenders == [], "flavor text outside cli.py: %r" % offenders
