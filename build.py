#!/usr/bin/env python3
"""Bundle src/corpsman into a single runnable script, `doc`.

The one-file property is what lets an operator copy 'doc' to a rescue USB
and run it offline on a machine with nothing installed. It is a
distribution artifact, not the source layout: source stays one module per
layer so an edit to SMART parsing does not share an edit surface with
device targeting.

APPROACH: rather than concatenating source into one flat namespace (which
requires stripping every internal import and hand-maintaining shims for
every qualified reference cli.py makes, e.g. `platform_.detect()` or
`identity_linux.enumerate_devices()`), this embeds each module's source
verbatim and loads it through the real Python import system: a
types.ModuleType per module, registered in sys.modules under its real
dotted name, exec'd with the same `from . import x` / `from ..types import
Device` statements the source already has. Nothing is rewritten or
stripped, so there is no separate "build dialect" of the source that can
silently drift out of sync with the real package -- add a module to
MODULES below and its own imports just work, unmodified.

The one piece of import machinery this reimplements by hand: when Python's
real import system loads a submodule for the first time, it also sets that
submodule as an attribute of its parent package (e.g. `corpsman.platform_`
becomes accessible as `corpsman.platform_`, an attribute lookup, not just a
sys.modules key). That happens automatically on the filesystem-backed path
but NOT on a manual cache-hit path like this one, and `from . import
platform_` needs the attribute, not just the sys.modules entry -- see
_load() below.
"""
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src", "corpsman")

# Every module in the package, dotted name -> path relative to SRC, in an
# order where each package is loaded before its own submodules. A module
# absent from this list is invisible to the built script -- if you add a
# new module under src/corpsman, add it here too.
MODULES = [
    ("corpsman", "__init__.py"),
    ("corpsman.run", "run.py"),
    ("corpsman.platform_", "platform_.py"),
    ("corpsman.types", "types.py"),
    ("corpsman.probe", "probe.py"),
    ("corpsman.identity", os.path.join("identity", "__init__.py")),
    ("corpsman.identity.linux", os.path.join("identity", "linux.py")),
    ("corpsman.identity.collisions", os.path.join("identity", "collisions.py")),
    ("corpsman.topology", os.path.join("topology", "__init__.py")),
    ("corpsman.topology.linux", os.path.join("topology", "linux.py")),
    ("corpsman.smart", os.path.join("smart", "__init__.py")),
    ("corpsman.smart.parse", os.path.join("smart", "parse.py")),
    ("corpsman.smart.verdict", os.path.join("smart", "verdict.py")),
    ("corpsman.smart.collect", os.path.join("smart", "collect.py")),
    ("corpsman.cli", "cli.py"),
]

HEADER = '''#!/usr/bin/env python3
"""corpsman - drive doctor.

Generated single-file build; edit src/corpsman and rerun build.py instead
of editing this file directly.

This file embeds every corpsman module as source text below and loads each
one through the real Python import machinery (types.ModuleType +
sys.modules + exec), so each module's own relative imports -- `from .
import x`, `from ..types import Device` -- run completely unmodified.
"""
import sys
import types


def _load(name, source, is_package):
    mod = types.ModuleType(name)
    mod.__file__ = "<corpsman-build>/" + name.replace(".", "/") + ".py"
    parent_name, _, leaf = name.rpartition(".")
    if is_package:
        mod.__path__ = []
        mod.__package__ = name
    else:
        mod.__package__ = parent_name
    sys.modules[name] = mod
    if parent_name:
        # Python's real import loader sets a loaded submodule as an
        # attribute of its parent package; `from . import x` needs that
        # attribute, not just the sys.modules entry, and a manual
        # cache-hit load (this one) never gets it for free.
        parent = sys.modules.get(parent_name)
        if parent is not None:
            setattr(parent, leaf, mod)
    exec(compile(source, mod.__file__, "exec"), mod.__dict__)
    return mod

'''


def build(out_path):
    # type: (str) -> str
    parts = [HEADER]

    parts.append("_ORDER = [")
    for name, _relpath in MODULES:
        parts.append("    %r," % (name,))
    parts.append("]")
    parts.append("")

    parts.append("_SOURCES = {}")
    for name, relpath in MODULES:
        path = os.path.join(SRC, relpath)
        with open(path, "r") as f:
            source = f.read()
        is_pkg = os.path.basename(relpath) == "__init__.py"
        # repr() of the source text, not a triple-quoted literal: it
        # handles any quote/backslash content in the original source
        # without a hand-rolled escaping pass, and always round-trips.
        parts.append("_SOURCES[%r] = (%r, %r)" % (name, source, is_pkg))
    parts.append("")

    parts.append("for _name in _ORDER:")
    parts.append("    _source, _is_pkg = _SOURCES[_name]")
    parts.append("    _load(_name, _source, _is_pkg)")
    parts.append("")

    parts.append('main = sys.modules["corpsman.cli"].main')
    parts.append("")
    parts.append('if __name__ == "__main__":')
    parts.append("    sys.exit(main())")

    text = "\n".join(parts) + "\n"
    with open(out_path, "w") as f:
        f.write(text)
    os.chmod(out_path, 0o755)
    return out_path


if __name__ == "__main__":
    print(build(os.path.join(ROOT, "doc")))
