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

    At pure-Python runtime this behaves like PlainSlots (Final is not
    enforced). Under mypyc, Final instance attributes are treated as
    immutable, so this is the closest thing to a 'native immutable record'
    and is the fastest target mypyc can produce. It is the baseline the
    dataclass/attrs/NamedTuple variants should be measured against once
    compiled.
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
