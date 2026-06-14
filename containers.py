"""Container construct definitions, stdlib-only and mypyc-clean.

This is the module you compile: `mypyc containers.py`. Keep it free of
dynamic/guarded definitions so the compiled and interpreted layouts stay
comparable. Third-party constructs (attrs, msgspec) live in bench.py so this
module compiles without their mypyc caveats.

All constructs carry the same 3 int fields (a, b, c) so memory, bytecode, and
timing numbers are apples-to-apples.
"""

from collections import namedtuple
from dataclasses import dataclass
from typing import Final, NamedTuple


# --- baselines / "already known" cases -------------------------------------

class PlainNoSlots:
    """Hand-written class with __dict__ (the slow, fat baseline)."""
    def __init__(self, a: int, b: int, c: int) -> None:
        self.a = a
        self.b = b
        self.c = c


class PlainSlots:
    """Hand-written __slots__ class — the usual 'fast mutable' reference."""
    __slots__ = ("a", "b", "c")

    def __init__(self, a: int, b: int, c: int) -> None:
        self.a = a
        self.b = b
        self.c = c


class NativeFinal:
    """The mypyc control: a plain slotted class with Final attributes.

    `Final` is never enforced at runtime — interpreted OR mypyc-compiled, the
    slot stays writable (verified: `o.a = 99` succeeds on the compiled .so).
    `Final` is a static-checker hint only. So this is the closest thing to a
    'native record' mypyc can produce — compact, slotted, C-level __init__ —
    but it is NOT runtime-immutable; genuine immutability needs a frozen
    dataclass/attrs, msgspec, or a NamedTuple. It is the layout/speed baseline
    the other variants are measured against once compiled.
    """
    __slots__ = ("a", "b", "c")

    def __init__(self, a: int, b: int, c: int) -> None:
        self.a: Final = a
        self.b: Final = b
        self.c: Final = c


# --- namedtuples ------------------------------------------------------------

CNamedTuple = namedtuple("CNamedTuple", ["a", "b", "c"])


class TNamedTuple(NamedTuple):
    a: int
    b: int
    c: int


# --- dataclasses, the 2x2 of {mutable, frozen} x {dict, slots} --------------

@dataclass
class DCPlain:
    a: int
    b: int
    c: int


@dataclass(frozen=True)
class DCFrozen:
    a: int
    b: int
    c: int


@dataclass(slots=True)
class DCSlots:
    a: int
    b: int
    c: int


@dataclass(frozen=True, slots=True)
class DCFrozenSlots:
    a: int
    b: int
    c: int
