"""Render the struct-profiling results, focused on statically-typed constructs.

Data captured on this box: CPython 3.14.0, mypy/mypyc 2.1.0, attrs 26.1.0,
msgspec 0.21.1. Per-instance/timing from bench.py (interpreted vs containers.so
compiled). Import-time numbers from the /tmp/imptime scaling, cold/warm, and
dependency-import experiments (median of 5 fresh interpreters each).

Only constructs with strong static-typing capability are plotted; functional
namedtuple and bare dict are dropped. NativeFinal (Final-annotated slotted
class) is the mypyc native-record control.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# --- focus set ---------------------------------------------------------------
# label, has_compiled_axis (stdlib -> mypyc-clean)
FOCUS = [
    ("NativeFinal\n(control)", True),
    ("typing.NT\n(class form)", True),
    ("dataclass\nfz+slots", True),
    ("attrs\nfz+slots", False),
    ("msgspec\nfrozen", False),
]
LABELS = [f[0] for f in FOCUS]

# --- per-instance + per-op (bench.py) ---------------------------------------
# bytes/instance (tracemalloc); ns instantiate; from matched interp/compiled runs
MEM_INTERP = [64.1, 88.1, 64.1, 80.1, 64.1]
MEM_COMP = [72.1, 88.1, 72.1, 80.1, 64.1]   # attrs/msgspec stay interpreted
MAKE_INTERP = [98.2, 152.8, 256.6, 231.5, 71.1]
MAKE_COMP = [93.9, 151.7, 250.9, 231.5, 71.1]

# --- import-time experiments -------------------------------------------------
# marginal per-class definition cost at import, warm (us/class), real forms
MARGINAL_US = [None, 87.0, 404.0, 339.0, 10.0]   # control has no codegen step
# mypyc effect on import: per-type cost (K=200 self-time / 200, warm), µs/type.
# None where no codegen step (control) or not compiled (attrs/msgspec).
IMPORT_INTERP_US = [None, 85.0, 404.0, 332.0, 11.0]
IMPORT_COMP_US = [None, 69.6, 358.7, None, None]    # only stdlib set compiled
# cold vs warm per-type (K=200 self-time / 200, µs/type) for the codegen story
COLD_US = [None, 123.5, 447.5, 364.5, 45.0]
WARM_US = [None, 85.0, 411.5, 332.0, 11.0]
# one-time dependency import (ms cumulative), cold (no .pyc) vs warm (cached)
DEP_NAMES = ["typing\n(NT)", "dataclasses", "attrs", "msgspec"]
DEP_COLD = [39.0, 92.5, 139.3, 141.2]
DEP_WARM = [4.6, 12.2, 24.9, 20.4]

C_INT, C_CMP = "#4C72B0", "#DD8452"
C_MUT, C_FRZ = "#55A868", "#C44E52"

# --- mutable vs frozen, per family (interpreted; mypyc doesn't change the
# frozen penalty — dataclass fz stays ~251ns compiled) ----------------------
# NamedTuple is immutable-only; control pair = PlainSlots / NativeFinal(Final).
FAM = ["plain/Final\n(control)", "dataclass\n+slots", "attrs\n+slots",
       "msgspec", "typing.NT\n(class)"]
MEM_MUT = [64.1, 64.1, 80.1, 64.1, None]
MEM_FRZ = [64.1, 64.1, 80.1, 64.1, 88.1]
MAKE_MUT = [95.8, 120.3, 105.7, 71.4, None]
MAKE_FRZ = [98.2, 256.6, 231.5, 71.1, 152.8]
IMP_MUT = [None, 270.0, 300.0, 10.0, None]   # control has no codegen step
IMP_FRZ = [None, 404.0, 339.0, 12.0, 87.0]


def _bars(ax, vals_a, vals_b, labels, la, lb, ylabel, title):
    import numpy as np

    x = np.arange(len(labels))
    w = 0.38
    a = [v if v is not None else 0 for v in vals_a]
    ax.bar(x - w / 2, a, w, label=la, color=C_INT)
    if vals_b is not None:
        b = [v if v is not None else 0 for v in vals_b]
        ax.bar(x + w / 2, b, w, label=lb, color=C_CMP)
        for i, v in enumerate(vals_b):
            if v is None:
                ax.text(i + w / 2, 1, "n/a", ha="center", va="bottom",
                        fontsize=7, rotation=90, color="gray")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel(ylabel)
    ax.set_title(title, fontsize=10, weight="bold")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)


def figure_import() -> None:
    fig, axes = plt.subplots(2, 2, figsize=(13, 9))
    fig.suptitle("Import / type-construction cost — statically-typed structs (CPython 3.14)",
                 fontsize=13, weight="bold")

    # panel 1: marginal per-class definition cost (the real "import work")
    ax = axes[0, 0]
    vals = [v if v is not None else 0 for v in MARGINAL_US]
    bars = ax.bar(LABELS, vals, color=["gray", C_INT, C_INT, C_INT, C_INT])
    ax.set_ylabel("µs per class (warm)")
    ax.set_title("Marginal type-construction cost per class\n(real decorator / class-statement form)",
                 fontsize=10, weight="bold")
    ax.grid(axis="y", alpha=0.3)
    for b, v in zip(bars, MARGINAL_US):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height(),
                "no codegen" if v is None else f"{v:.0f}",
                ha="center", va="bottom", fontsize=8)

    # panel 2: mypyc effect on import, per-type
    _bars(axes[0, 1], IMPORT_INTERP_US, IMPORT_COMP_US, LABELS,
          "interpreted", "mypyc-compiled", "µs per type (import, warm)",
          "mypyc effect on import time, cost per type\n(decorator still runs → only ~10-20% off)")

    # panel 3: cold vs warm (bytecode compile), per-type
    _bars(axes[1, 0], COLD_US, WARM_US, LABELS,
          "cold (recompile source)", "warm (.pyc cached)", "µs per type (import)",
          "Cold vs warm start, cost per type\n(~35µs/type source→bytecode, mypyc removes it)")

    # panel 4: one-time dependency import, cold vs warm
    _bars(axes[1, 1], DEP_COLD, DEP_WARM, DEP_NAMES,
          "cold (no .pyc)", "warm (cached)", "ms (cumulative, fresh interp)",
          "One-time dependency import — cold vs warm\n(paid once; cold recompiles whole source tree)")

    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig("fig_import_time.png", dpi=130)
    print("wrote fig_import_time.png")


def figure_runtime() -> None:
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.6))
    fig.suptitle("Per-instance runtime cost — statically-typed structs (CPython 3.14)",
                 fontsize=13, weight="bold")
    _bars(axes[0], MEM_INTERP, MEM_COMP, LABELS,
          "interpreted", "mypyc-compiled", "bytes / instance",
          "Memory footprint\n(mypyc native +8B vtable word)")
    _bars(axes[1], MAKE_INTERP, MAKE_COMP, LABELS,
          "interpreted", "mypyc-compiled", "ns / instantiation",
          "Instantiation time\n(frozen pays object.__setattr__ per field)")
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig("fig_runtime.png", dpi=130)
    print("wrote fig_runtime.png")


COLORS = {"typing.NT": "#4C72B0", "dataclass fz+slots": "#C44E52",
          "attrs fz+slots": "#8172B3", "msgspec frozen": "#55A868"}


def _log2_realticks(ax) -> None:
    """log2 axes labelled with real numbers (ints, or decimals below 1)."""
    from matplotlib.ticker import FuncFormatter, NullFormatter

    def fmt(v, _):
        if v <= 0:
            return ""
        return f"{v:.0f}" if v >= 1 else f"{v:g}"

    for axis in (ax.xaxis, ax.yaxis):
        axis.set_major_formatter(FuncFormatter(fmt))
        axis.set_minor_formatter(NullFormatter())


def _crossover_fig(cold, warm, slope_div, unit, xlabel, x_max_exp,
                   suptitle, fname) -> None:
    """Fixed-cost + N*marginal startup, drawn as a cold↔warm band ("fat curve")
    per construct on one log2/log2 axes. Each model maps construct ->
    (fixed_ms, marginal_in_`unit`); marginal/`slope_div` -> ms. The NT↔msgspec
    crossover is itself a band: warm-crossover (best case) to cold-crossover."""
    import numpy as np

    def total(mc, n):
        fixed, marg = mc
        return fixed + n * marg / slope_div

    def crossover(model, a, b):
        fa, ma = model[a]
        fb, mb = model[b]
        return (fb - fa) / ((ma - mb) / slope_div)

    n = np.power(2.0, np.linspace(0, x_max_exp, 600))
    fig, ax = plt.subplots(figsize=(11, 6.4))
    fig.suptitle(suptitle, fontsize=13, weight="bold")

    for name in cold:
        lo = np.array([total(warm[name], x) for x in n])
        hi = np.array([total(cold[name], x) for x in n])
        ax.fill_between(n, lo, hi, color=COLORS[name], alpha=0.28)
        ax.plot(n, lo, color=COLORS[name], lw=1.4)
        ax.plot(n, hi, color=COLORS[name], lw=1.4, ls="--")
        ax.plot([], [], color=COLORS[name], lw=6, alpha=0.5,
                label=f"{name}  (warm—cold band)")

    xw = crossover(warm, "typing.NT", "msgspec frozen")
    xcld = crossover(cold, "typing.NT", "msgspec frozen")
    ax.axvspan(xw, xcld, color="gray", alpha=0.18)
    for xc, lab in ((xw, f"warm: {xw:,.0f}"), (xcld, f"cold: {xcld:,.0f}")):
        ax.axvline(xc, color="gray", ls=":", lw=1)
    ymid = total(warm["msgspec frozen"], xw) * 3
    ax.annotate(f"NT ↔ msgspec crossover\n{xw:,.0f} {unit} (warm) → {xcld:,.0f} (cold)",
                ((xw * xcld) ** 0.5, ymid), ha="center", fontsize=9,
                bbox=dict(boxstyle="round", fc="white", ec="gray", alpha=0.85))

    ax.set_xscale("log", base=2)
    ax.set_yscale("log", base=2)
    _log2_realticks(ax)
    ax.set_xlabel(f"{xlabel} (log2)")
    ax.set_ylabel("total time, ms (log2)")
    ax.grid(which="both", alpha=0.22)
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(fname, dpi=130)
    print(f"wrote {fname}  (crossover {xw:,.0f}→{xcld:,.0f} {unit})")


def figure_startup() -> None:
    """Startup = dependency import (fixed) + N * per-type construction cost."""
    cold = {"typing.NT": (39.0, 123.5), "dataclass fz+slots": (92.5, 447.5),
            "attrs fz+slots": (139.3, 364.5), "msgspec frozen": (141.2, 45.0)}
    warm = {"typing.NT": (4.6, 85.0), "dataclass fz+slots": (12.2, 411.5),
            "attrs fz+slots": (24.9, 332.0), "msgspec frozen": (20.4, 11.0)}
    _crossover_fig(cold, warm, 1000.0, "types", "number of types defined", 12.5,
                   "Program startup vs number of types — when does msgspec overtake NamedTuple?",
                   "fig_startup_crossover.png")


def figure_immutability() -> None:
    import numpy as np

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.8))
    fig.suptitle("Cost of immutability — mutable vs frozen per family (CPython 3.14, interpreted)",
                 fontsize=13, weight="bold")
    x = np.arange(len(FAM))
    w = 0.38

    def pair(ax, mut, frz, ylabel, title):
        m = [v if v is not None else 0 for v in mut]
        f = [v if v is not None else 0 for v in frz]
        ax.bar(x - w / 2, m, w, label="mutable", color=C_MUT)
        ax.bar(x + w / 2, f, w, label="frozen / immutable", color=C_FRZ)
        for i, v in enumerate(mut):
            if v is None:
                ax.text(i - w / 2, 1, "n/a", ha="center", va="bottom",
                        fontsize=7, rotation=90, color="gray")
        ax.set_xticks(x)
        ax.set_xticklabels(FAM, fontsize=8)
        ax.set_ylabel(ylabel)
        ax.set_title(title, fontsize=10, weight="bold")
        ax.legend(fontsize=8)
        ax.grid(axis="y", alpha=0.3)

    pair(axes[0], MEM_MUT, MEM_FRZ, "bytes / instance",
         "Memory — immutability is FREE")
    pair(axes[1], MAKE_MUT, MAKE_FRZ, "ns / instantiation",
         "Instantiation — frozen 2.2x for dc/attrs,\nfree for msgspec")
    pair(axes[2], IMP_MUT, IMP_FRZ, "µs / class (import, warm)",
         "Import/type-construction —\nfrozen +50% dc, +13% attrs, free msgspec")
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig("fig_immutability.png", dpi=130)
    print("wrote fig_immutability.png")


if __name__ == "__main__":
    figure_import()
    figure_runtime()
    figure_immutability()
    figure_startup()
