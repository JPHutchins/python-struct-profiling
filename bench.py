"""Harness for comparing Python record constructs across four axes:

  1. memory footprint        (getsizeof + tracemalloc bulk allocation)
  2. alloc/dealloc bytecode   (dis instruction counts on __new__/__init__)
  3. definition-time cost     (timeit on the type-creation call)
  4. instantiation & access   (the 'already known' timing baselines)

Run interpreted:
    rm -f containers*.so       # a stale .so shadows the .py, so remove it first
    python bench.py

Run with containers.py compiled (the mypyc axis):
    mypyc containers.py        # produces containers.*.so
    python bench.py            # now imports the compiled module
    # NOTE: the .so wins over the .py on every later run until you delete it,
    # so re-`rm containers*.so` before measuring the interpreted axis again.

When containers is compiled, the dis column for the stdlib constructs flips to
"C" (no interpreted bytecode left) — that flip is itself the headline result.
attrs/msgspec stay interpreted here; to put them on the compiled axis, move
their defs into a module you also pass to mypyc (mind attrs' mypyc notes).
"""

from __future__ import annotations

import dis
import gc
import sys
import timeit
from types import FunctionType
from typing import Any, Callable

import containers as C

# Detect whether the imported containers module is compiled by mypyc.
COMPILED = getattr(C, "__file__", "").endswith((".so", ".pyd"))

N_MEM = 200_000      # instances allocated for the tracemalloc measurement
N_TIME = 1_000_000   # iterations per repeat for instantiation timing
N_ACCESS = 5_000_000 # iterations per repeat for attribute-access timing
N_DEF = 50_000       # iterations for type-creation timing
N_REPEAT = 7         # timeit repeats; report the min (low-noise estimator)


# ---------------------------------------------------------------------------
# Construct registry: name -> (constructor, args, accessor)
# ---------------------------------------------------------------------------

ARGS = (1, 2, 3)


def _reg() -> list[tuple[str, Callable[..., Any], tuple[Any, ...], Callable[[Any], Any]]]:
    reg: list[tuple[str, Callable[..., Any], tuple[Any, ...], Callable[[Any], Any]]] = [
        ("dict",            lambda a, b, c: {"a": a, "b": b, "c": c}, ARGS, lambda o: o["a"]),
        ("PlainNoSlots",    C.PlainNoSlots,   ARGS, lambda o: o.a),
        ("PlainSlots",      C.PlainSlots,     ARGS, lambda o: o.a),
        ("NativeFinal",     C.NativeFinal,    ARGS, lambda o: o.a),
        ("collections.NT",  C.CNamedTuple,    ARGS, lambda o: o.a),
        ("typing.NT",       C.TNamedTuple,    ARGS, lambda o: o.a),
        ("dataclass",       C.DCPlain,        ARGS, lambda o: o.a),
        ("dataclass frozen", C.DCFrozen,      ARGS, lambda o: o.a),
        ("dataclass slots", C.DCSlots,        ARGS, lambda o: o.a),
        ("dataclass fz+slots", C.DCFrozenSlots, ARGS, lambda o: o.a),
    ]

    # Optional third-party constructs (interpreted in this process).
    try:
        import attrs

        @attrs.define
        class AttrsSlots:
            a: int
            b: int
            c: int

        @attrs.frozen
        class AttrsFrozenSlots:
            a: int
            b: int
            c: int

        @attrs.define(slots=False)
        class AttrsNoSlots:
            a: int
            b: int
            c: int

        reg += [
            ("attrs slots",     AttrsSlots,        ARGS, lambda o: o.a),
            ("attrs fz+slots",  AttrsFrozenSlots,  ARGS, lambda o: o.a),
            ("attrs no-slots",  AttrsNoSlots,      ARGS, lambda o: o.a),
        ]
    except ImportError:
        pass

    try:
        import msgspec

        class MsgspecStruct(msgspec.Struct):
            a: int
            b: int
            c: int

        class MsgspecFrozen(msgspec.Struct, frozen=True):
            a: int
            b: int
            c: int

        reg += [
            ("msgspec.Struct",  MsgspecStruct,  ARGS, lambda o: o.a),
            ("msgspec frozen",  MsgspecFrozen,  ARGS, lambda o: o.a),
        ]
    except ImportError:
        pass

    return reg


# ---------------------------------------------------------------------------
# 1. memory footprint
# ---------------------------------------------------------------------------

def getsize_one(ctor: Callable[..., Any], args: tuple[Any, ...]) -> int:
    return sys.getsizeof(ctor(*args))


def mem_per_instance(ctor: Callable[..., Any], args: tuple[Any, ...], n: int = N_MEM) -> float:
    """Real allocator cost per instance, GC header included.

    Subtracts a same-length [None]*n list so the list's own storage cancels
    out and we are left with n instances' worth of allocation.
    """
    import tracemalloc

    gc.collect()
    tracemalloc.start()
    base = [None] * n  # noqa: F841  (held to keep it alive)
    base_cur, _ = tracemalloc.get_traced_memory()
    objs = [ctor(*args) for _ in range(n)]  # noqa: F841
    cur, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del objs, base
    return (cur - base_cur) / n


# ---------------------------------------------------------------------------
# 2. alloc / dealloc bytecode
# ---------------------------------------------------------------------------

def _py_func(cls: type, name: str) -> FunctionType | None:
    """Return the attribute if it is a real Python function (has bytecode),
    else None (C slot wrapper, builtin, or compiled native method)."""
    fn = cls.__dict__.get(name)
    return fn if isinstance(fn, FunctionType) else None


def bytecode_counts(cls: type) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for meth in ("__new__", "__init__"):
        fn = _py_func(cls, meth)
        if fn is None:
            out[meth] = "C/none"
        else:
            out[meth] = sum(1 for _ in dis.get_instructions(fn))
    # Deallocation: pure C tp_dealloc unless a Python __del__ exists.
    out["__del__"] = "py" if _py_func(cls, "__del__") else "C"
    return out


def dump_init_disassembly(name: str, cls: type) -> None:
    """Full dis of the construction path — eyeball the frozen object.__setattr__
    calls vs a plain STORE_ATTR here."""
    print(f"\n===== {name}: __init__ / __new__ disassembly =====")
    for meth in ("__new__", "__init__"):
        fn = _py_func(cls, meth)
        print(f"--- {meth} ---")
        if fn is None:
            print("  (C-level / no Python bytecode)")
        else:
            dis.dis(fn)


# ---------------------------------------------------------------------------
# 3. definition-time (type creation) cost
# ---------------------------------------------------------------------------

def definition_time_factories() -> list[tuple[str, Callable[[], Any]]]:
    from collections import namedtuple
    from dataclasses import make_dataclass
    from typing import NamedTuple

    facs: list[tuple[str, Callable[[], Any]]] = [
        ("collections.NT", lambda: namedtuple("X", ["a", "b", "c"])),
        ("typing.NT",      lambda: NamedTuple("X", [("a", int), ("b", int), ("c", int)])),
        ("dataclass",      lambda: make_dataclass("X", [("a", int), ("b", int), ("c", int)])),
        ("dataclass frozen", lambda: make_dataclass(
            "X", [("a", int), ("b", int), ("c", int)], frozen=True)),
        ("dataclass slots", lambda: make_dataclass(
            "X", [("a", int), ("b", int), ("c", int)], slots=True)),
        ("dataclass fz+slots", lambda: make_dataclass(
            "X", [("a", int), ("b", int), ("c", int)], frozen=True, slots=True)),
    ]
    try:
        import attrs
        facs.append(("attrs make_class", lambda: attrs.make_class(
            "X", {"a": attrs.field(), "b": attrs.field(), "c": attrs.field()})))
        facs.append(("attrs frozen", lambda: attrs.make_class(
            "X", {"a": attrs.field(), "b": attrs.field(), "c": attrs.field()},
            frozen=True)))
    except ImportError:
        pass
    return facs


# ---------------------------------------------------------------------------
# timing helpers
# ---------------------------------------------------------------------------

def time_construct(ctor: Callable[..., Any], args: tuple[Any, ...], n: int = N_TIME) -> float:
    best = min(timeit.repeat(lambda: ctor(*args), repeat=N_REPEAT, number=n))
    return best / n * 1e9  # ns/op, min of N_REPEAT


def time_access(ctor: Callable[..., Any], args: tuple[Any, ...],
                accessor: Callable[[Any], Any], n: int = N_ACCESS) -> float:
    obj = ctor(*args)
    best = min(timeit.repeat(lambda: accessor(obj), repeat=N_REPEAT, number=n))
    return best / n * 1e9  # ns/op, min of N_REPEAT


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def main() -> None:
    reg = _reg()
    tag = "COMPILED (mypyc)" if COMPILED else "interpreted (CPython)"
    print(f"containers module: {tag}  |  python {sys.version.split()[0]}\n")

    hdr = (f"{'construct':<20}{'getsizeof':>11}{'mem/inst':>11}"
           f"{'new_bc':>9}{'init_bc':>9}{'del':>5}"
           f"{'make ns':>10}{'access ns':>11}")
    print(hdr)
    print("-" * len(hdr))

    for name, ctor, args, accessor in reg:
        gso = getsize_one(ctor, args)
        mpi = mem_per_instance(ctor, args)
        cls = ctor if isinstance(ctor, type) else type(ctor(*args))
        bc = bytecode_counts(cls)
        mk = time_construct(ctor, args)
        ac = time_access(ctor, args, accessor)
        print(f"{name:<20}{gso:>11}{mpi:>11.1f}"
              f"{str(bc['__new__']):>9}{str(bc['__init__']):>9}{bc['__del__']:>5}"
              f"{mk:>10.1f}{ac:>11.2f}")

    print("\n--- type-creation (definition) cost, ns per class built ---")
    for name, fac in definition_time_factories():
        t = timeit.timeit(fac, number=N_DEF) / N_DEF * 1e9
        print(f"{name:<22}{t:>12.1f} ns")

    # Eyeball the construction bytecode for the immutability-cost story.
    if not COMPILED:
        dump_init_disassembly("dataclass slots", C.DCSlots)
        dump_init_disassembly("dataclass fz+slots", C.DCFrozenSlots)


if __name__ == "__main__":
    main()
