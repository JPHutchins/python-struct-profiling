"""Reproducible probe for the type-DEFINITION axis: what does each construct
actually generate-and-compile when its `class` statement or decorator executes?

`importtime_sweep.py` measures *how long* defining a type costs; this script
shows *why* the cost falls into tiers. It patches `exec` / `compile` / `eval` to
capture every unit of Python **source generated at class-creation time** for one
class of each construct, reusing the exact templates from `importtime_sweep.py`
so the two harnesses stay in lock-step.

    uv run python codegen_probe.py                 # summary table
    uv run python codegen_probe.py --show-source   # + the generated source

On CPython 3.14 (the build used for the post):
  * native slots / manual record -> 0 units: the methods are hand-written and
    compiled once into the `.pyc`; nothing is generated when the class is built.
  * msgspec / record-type (C)     -> 0 units: a C metaclass builds the type, so
    there is no Python source to generate.
  * NamedTuple / record-type      -> 1 generated method (an `eval`'d `__new__`
    lambda / an `exec`'d `__init__`); everything else is inherited.
  * dataclass / attrs             -> several generated methods, plus field
    introspection and, for `dataclass(slots=True)`, a second class creation.

The count of methods generated here is the mechanism behind the three warm
type-cost tiers reported by `importtime_sweep.py`.
"""

from __future__ import annotations

import argparse
import builtins
from typing import Any, Callable, NamedTuple

from importtime_sweep import CONSTRUCTS, Construct


class Generated(NamedTuple):
    kind: str  # "exec" | "compile" | "eval"
    source: str

    def methods(self) -> int:
        """Callables defined in this unit (excluding the dataclass factory shell)."""
        return (
            self.source.count("def ")
            - self.source.count("def __create_fn__")
            + self.source.count("lambda ")
        )


def generated_by(construct: Construct) -> list[Generated]:
    """Source units generated while defining ONE class of `construct`.

    The class body is run through the *real* `exec`, so only the construct's own
    internal codegen (the `exec`/`eval`/`compile` its machinery calls) is caught.
    """
    real_exec, real_compile, real_eval = builtins.exec, builtins.compile, builtins.eval
    namespace: dict[str, Any] = {}
    if construct.header:
        real_exec(construct.header, namespace)
    # Compile the body as a standalone module would, with `dont_inherit=True`:
    # this file's `from __future__ import annotations` must NOT stringize the
    # construct's annotations (records.py and the metaclasses read them at
    # runtime), which an inherited PEP 563 flag would do.
    body = real_compile(construct.body(0), "<codegen-probe>", "exec", dont_inherit=True)

    captured: list[Generated] = []

    def tap(kind: str, real: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if args and isinstance(args[0], str):
                captured.append(Generated(kind, args[0]))
            return real(*args, **kwargs)

        return wrapper

    builtins.exec = tap("exec", real_exec)
    builtins.compile = tap("compile", real_compile)
    builtins.eval = tap("eval", real_eval)
    try:
        real_exec(body, namespace)
    finally:
        builtins.exec, builtins.compile, builtins.eval = real_exec, real_compile, real_eval
    return captured


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show-source", action="store_true",
                        help="print the exact generated source for each construct")
    args = parser.parse_args()

    rows = [(c.label, generated_by(c)) for c in CONSTRUCTS]

    print(f"{'construct':28} {'units':>5} {'methods':>8} {'bytes':>7}")
    print("-" * 52)
    for label, units in rows:
        methods = sum(g.methods() for g in units)
        nbytes = sum(len(g.source) for g in units)
        print(f"{label:28} {len(units):5d} {methods:8d} {nbytes:7d}")

    if args.show_source:
        for label, units in rows:
            print(f"\n{'=' * 72}\n{label}: {len(units)} generated unit(s)\n{'=' * 72}")
            for g in units:
                print(f"\n--- [{g.kind}] {len(g.source)} chars, {g.methods()} method(s) ---")
                print(g.source.rstrip())


if __name__ == "__main__":
    main()
