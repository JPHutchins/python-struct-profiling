"""Reproducible harness for the import / type-construction axis.

The per-instance harness (`bench.py`) cannot measure what it costs to *define* a
type at import: `timeit` on `make_dataclass()` measures a different (dynamic)
form and is blind to both mypyc and the one-time library import. So every
import number in the post comes from this script instead, which drives a
**fresh interpreter under `python -X importtime`** and reads the *self* time
attributed to a generated module of K identical-shape classes.

    uv run python importtime_sweep.py            # full sweep, markdown table
    uv run python importtime_sweep.py --k 200 --runs 5

Definitions:
  * per-type (us/class) = module self-time / K, median of `runs` fresh interpreters.
  * warm = `__pycache__/*.pyc` present (bytecode cached); cold = removed first,
    so source is recompiled to bytecode in-process.
  * mypyc = the generated module compiled to a `.so` and imported under the same
    harness (a compiled extension has no source to recompile -> no cold/warm gap).
  * dependency import (ms, cumulative) = `python -X importtime -c "import LIB"`;
    cold points PYTHONPYCACHEPREFIX at an empty dir so the whole source tree
    recompiles.

All constructs carry the same three int fields (a, b, c), matching bench.py /
containers.py, so the numbers are apples-to-apples across both harnesses.
"""

from __future__ import annotations

import argparse
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, NamedTuple


# ---------------------------------------------------------------------------
# construct code templates (real class-statement / decorator forms)
# ---------------------------------------------------------------------------

class Construct(NamedTuple):
    key: str
    label: str
    header: str           # module-level imports the body needs
    body: Callable[[int], str]  # i -> one class definition
    compilable: bool      # stdlib-only -> can go through mypyc cleanly


def _native_frozen(i: int) -> str:
    return (
        f"class C{i}:\n"
        f'    __slots__ = ("a", "b", "c")\n'
        f"    def __init__(self, a: int, b: int, c: int) -> None:\n"
        f"        self.a: Final = a\n"
        f"        self.b: Final = b\n"
        f"        self.c: Final = c\n"
    )


def _native_mutable(i: int) -> str:
    return (
        f"class C{i}:\n"
        f'    __slots__ = ("a", "b", "c")\n'
        f"    def __init__(self, a: int, b: int, c: int) -> None:\n"
        f"        self.a = a\n"
        f"        self.b = b\n"
        f"        self.c = c\n"
    )


def _namedtuple(i: int) -> str:
    return f"class C{i}(NamedTuple):\n    a: int\n    b: int\n    c: int\n"


def _dc(decorator: str) -> Callable[[int], str]:
    return lambda i: f"@{decorator}\nclass C{i}:\n    a: int\n    b: int\n    c: int\n"


def _msgspec(frozen: bool) -> Callable[[int], str]:
    base = "msgspec.Struct, frozen=True" if frozen else "msgspec.Struct"
    return lambda i: f"class C{i}({base}):\n    a: int\n    b: int\n    c: int\n"


def _attrs(decorator: str) -> Callable[[int], str]:
    return lambda i: f"@{decorator}\nclass C{i}:\n    a: int\n    b: int\n    c: int\n"


CONSTRUCTS: list[Construct] = [
    Construct("native_mut", "native mutable (slots)", "from typing import Final",
              _native_mutable, True),
    Construct("native_frz", "native (Final + slots)", "from typing import Final",
              _native_frozen, True),
    Construct("namedtuple", "typing.NamedTuple", "from typing import NamedTuple",
              _namedtuple, True),
    Construct("dc_mut", "dataclass (slots)", "from dataclasses import dataclass",
              _dc("dataclass(slots=True)"), True),
    Construct("dc_frz", "dataclass (frozen + slots)", "from dataclasses import dataclass",
              _dc("dataclass(frozen=True, slots=True)"), True),
    Construct("attrs_mut", "attrs (slots)", "import attrs", _attrs("attrs.define"), False),
    Construct("attrs_frz", "attrs (frozen + slots)", "import attrs", _attrs("attrs.frozen"), False),
    Construct("msgspec_mut", "msgspec", "import msgspec", _msgspec(False), False),
    Construct("msgspec_frz", "msgspec (frozen)", "import msgspec", _msgspec(True), False),
]

DEPS = [("typing", "typing"), ("dataclasses", "dataclasses"),
        ("attrs", "attrs"), ("msgspec", "msgspec")]


# ---------------------------------------------------------------------------
# -X importtime driver
# ---------------------------------------------------------------------------

_LINE = re.compile(r"import time:\s+(\d+)\s+\|\s+(\d+)\s+\|\s+(.*)")


def _parse_importtime(stderr: str, target: str) -> tuple[int, int] | None:
    """Return (self_us, cumulative_us) for the import line whose package name
    (last path component, stripped of tree-drawing indentation) equals target."""
    for line in stderr.splitlines():
        m = _LINE.match(line)
        if m and m.group(3).strip() == target:
            return int(m.group(1)), int(m.group(2))
    return None


def _run_import(stmt: str, target: str, cwd: Path,
                env: dict[str, str] | None = None) -> tuple[int, int]:
    # `python -c` puts cwd ('') first on sys.path, so a module written into
    # `cwd` is importable by the fresh subprocess without any PYTHONPATH games.
    proc = subprocess.run(
        [sys.executable, "-X", "importtime", "-c", stmt],
        capture_output=True, text=True, env=env, cwd=str(cwd),
    )
    parsed = _parse_importtime(proc.stderr, target)
    if parsed is None:
        raise RuntimeError(f"no importtime line for {target!r}\n{proc.stderr[-2000:]}")
    return parsed


def _median_self(stmt: str, target: str, runs: int, cwd: Path,
                 prep: Callable[[], None]) -> float:
    samples = []
    for _ in range(runs):
        prep()
        samples.append(_run_import(stmt, target, cwd)[0])
    return statistics.median(samples)


# ---------------------------------------------------------------------------
# per-type sweeps
# ---------------------------------------------------------------------------

def _write_module(path: Path, c: Construct, k: int) -> None:
    path.write_text(c.header + "\n\n\n" + "\n\n".join(c.body(i) for i in range(k)) + "\n")


def _rm_pycache(d: Path) -> None:
    shutil.rmtree(d / "__pycache__", ignore_errors=True)


def per_type_us(c: Construct, k: int, runs: int, work: Path) -> dict[str, float | None]:
    # Each construct gets its own dir so its __pycache__ is isolated and the
    # subprocess (run with cwd=cdir) imports exactly this module.
    cdir = work / c.key
    cdir.mkdir(exist_ok=True)
    _write_module(cdir / f"{c.key}.py", c, k)
    stmt = f"import {c.key}"
    # warm: prime the .pyc once, then every measured run finds it cached.
    _rm_pycache(cdir)
    _run_import(stmt, c.key, cdir)
    warm = _median_self(stmt, c.key, runs, cdir, lambda: None) / k
    # cold: drop the .pyc before each run so source is recompiled in-process.
    cold = _median_self(stmt, c.key, runs, cdir, lambda: _rm_pycache(cdir)) / k
    out: dict[str, float | None] = {"warm": warm, "cold": cold, "mypyc": None}

    if c.compilable:
        out["mypyc"] = _mypyc_per_type(c, k, runs, work)
    return out


def _mypyc_per_type(c: Construct, k: int, runs: int, work: Path) -> float | None:
    """Compile the generated module with mypyc and measure the .so's self-time."""
    cdir = work / f"c_{c.key}"
    cdir.mkdir(exist_ok=True)
    (cdir / f"{c.key}.py").write_text(
        c.header + "\n\n\n" + "\n\n".join(c.body(i) for i in range(k)) + "\n")
    proc = subprocess.run(["mypyc", f"{c.key}.py"], cwd=cdir,
                          capture_output=True, text=True)
    so = list(cdir.glob(f"{c.key}.*.so"))
    if proc.returncode != 0 or not so:
        print(f"  [mypyc skip {c.key}] {proc.stderr.strip()[-200:]}", file=sys.stderr)
        return None
    (cdir / f"{c.key}.py").unlink()  # force the .so to be imported
    return _median_self(f"import {c.key}", c.key, runs, cdir, lambda: None) / k


def dep_import_ms(runs: int, work: Path) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    import os

    for lib, target in DEPS:
        warm = statistics.median(
            _run_import(f"import {lib}", target, work)[1] for _ in range(runs)) / 1000
        cold_env = {**os.environ, "PYTHONPYCACHEPREFIX": str(work / "cold_cache")}
        cold_samples = []
        for _ in range(runs):
            shutil.rmtree(work / "cold_cache", ignore_errors=True)
            cold_samples.append(_run_import(f"import {lib}", target, work, env=cold_env)[1])
        out[lib] = {"warm": warm, "cold": statistics.median(cold_samples) / 1000}
    return out


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=200)
    ap.add_argument("--runs", type=int, default=5)
    args = ap.parse_args()

    print(f"python {sys.version.split()[0]} | K={args.k} | median of {args.runs} "
          f"fresh interpreters\n")

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        print(f"{'construct':<28}{'warm us/type':>14}{'cold us/type':>14}{'mypyc us/type':>15}")
        print("-" * 71)
        for c in CONSTRUCTS:
            r = per_type_us(c, args.k, args.runs, work)
            mp = f"{r['mypyc']:.1f}" if r["mypyc"] is not None else "n/a"
            print(f"{c.label:<28}{r['warm']:>14.1f}{r['cold']:>14.1f}{mp:>15}")

        print(f"\n{'dependency import':<20}{'warm ms':>12}{'cold ms':>12}")
        print("-" * 44)
        for lib, v in dep_import_ms(args.runs, work).items():
            print(f"{lib:<20}{v['warm']:>12.1f}{v['cold']:>12.1f}")


if __name__ == "__main__":
    main()
