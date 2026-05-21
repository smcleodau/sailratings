"""Matplotlib PNG factory — one function per chart type.

Uses the Agg backend so it works under the API process without a
display. Each function returns raw PNG bytes; the orchestrator
base64-inlines them into the HTML template. Style is restrained,
brand-aligned navy/brass palette to match the PDF look.
"""
from __future__ import annotations

import io
import logging

import matplotlib
matplotlib.use("Agg")  # must be before pyplot import

import matplotlib.pyplot as plt  # noqa: E402

logger = logging.getLogger(__name__)

# ── Brand palette ───────────────────────────────────────────────────────
NAVY = "#0A2240"
BRASS = "#C29B61"
CREAM = "#F4F1E8"
SIGNAL_GREEN = "#4A8A6F"
SIGNAL_RED = "#B85450"
GRID = "#D9D5C7"
TEXT = "#1e293b"

# Chart sizing — A4 page is 595×842 pt; we target 480 pt wide ≈ 6.5 in.
DPI = 144
FIGSIZE_WIDE = (6.5, 3.0)
FIGSIZE_SQUARE = (4.5, 4.5)


def _to_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=DPI, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _style(ax) -> None:
    """Common axis cosmetics."""
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=TEXT, labelsize=8)
    ax.yaxis.label.set_color(TEXT)
    ax.xaxis.label.set_color(TEXT)
    ax.grid(True, axis="y", color=GRID, linewidth=0.5, alpha=0.7)


# ── Charts ──────────────────────────────────────────────────────────────


def render_anatomy_bar(facts) -> bytes:
    """Per-measurement TCC contribution, signed bar chart, sorted by
    absolute impact. Positive = boat rates higher than median; negative
    = lower."""
    items = sorted(facts.decomposition, key=lambda c: -abs(c.contrib_tcc))[:10]
    labels = [c.field for c in items][::-1]
    values = [c.contrib_tcc for c in items][::-1]
    colors = [BRASS if v >= 0 else SIGNAL_GREEN for v in values]

    fig, ax = plt.subplots(figsize=FIGSIZE_WIDE)
    ax.barh(labels, values, color=colors, edgecolor=NAVY, linewidth=0.5)
    ax.axvline(0, color=NAVY, linewidth=0.8)
    ax.set_xlabel("TCC contribution vs class mean (signed)", fontsize=9)
    ax.set_title(f"What drives {facts.boat_name}'s rating",
                 fontsize=11, color=NAVY, pad=12, fontweight="bold")
    _style(ax)
    return _to_png(fig)


def render_tcc_timeseries(facts) -> bytes:
    """TCC over time, with marker per certificate."""
    dates = [s.date for s in facts.snapshots]
    tccs = [float(s.tcc) for s in facts.snapshots]

    fig, ax = plt.subplots(figsize=FIGSIZE_WIDE)
    ax.plot(dates, tccs, color=NAVY, linewidth=2, marker="o",
            markerfacecolor=BRASS, markeredgecolor=NAVY, markersize=6)
    ax.set_ylabel("TCC", fontsize=9)
    ax.set_title(f"{facts.boat_name} — rating evolution",
                 fontsize=11, color=NAVY, pad=12, fontweight="bold")
    _style(ax)
    fig.autofmt_xdate()
    return _to_png(fig)


def render_class_distribution(facts, all_tccs: list[float]) -> bytes:
    """Histogram of class TCCs with this boat marked."""
    fig, ax = plt.subplots(figsize=FIGSIZE_WIDE)
    ax.hist(all_tccs, bins=20, color=NAVY, alpha=0.75, edgecolor="white")
    ax.axvline(facts.this_boat_tcc, color=BRASS, linewidth=2.5,
               label=f"This boat: {facts.this_boat_tcc:.4f}")
    ax.axvline(facts.class_tcc_median, color=SIGNAL_GREEN, linewidth=1.5,
               linestyle="--",
               label=f"Class median: {facts.class_tcc_median:.4f}")
    ax.set_xlabel("TCC", fontsize=9)
    ax.set_ylabel("Boats", fontsize=9)
    ax.set_title(f"{facts.design} TCC distribution (n={facts.class_n})",
                 fontsize=11, color=NAVY, pad=12, fontweight="bold")
    ax.legend(fontsize=8, loc="upper right", frameon=False)
    _style(ax)
    return _to_png(fig)


def render_sensitivity_bar(facts) -> bytes:
    """Standardised coefficient bar — which measurement levers move TCC
    most across the fleet, independent of this boat's position."""
    items = sorted(facts.coefficients, key=lambda c: -abs(c.beta))[:10]
    labels = [c.field for c in items][::-1]
    values = [c.beta for c in items][::-1]
    colors = [BRASS if v >= 0 else SIGNAL_GREEN for v in values]

    fig, ax = plt.subplots(figsize=FIGSIZE_WIDE)
    ax.barh(labels, values, color=colors, edgecolor=NAVY, linewidth=0.5)
    ax.axvline(0, color=NAVY, linewidth=0.8)
    ax.set_xlabel("Coefficient (β per unit, signed)", fontsize=9)
    ax.set_title(f"{facts.design} — which measurements move TCC most",
                 fontsize=11, color=NAVY, pad=12, fontweight="bold")
    _style(ax)
    return _to_png(fig)


def render_results_timeline(facts) -> bytes:
    """Scatter of recent results — place vs date, sized by fleet size."""
    pts = [(r.event_date, r.place, r.fleet_size or 10, r.status)
           for r in facts.recent_results
           if r.event_date and r.place]
    if not pts:
        # Render an explicit "no data" placeholder so the layout doesn't break.
        fig, ax = plt.subplots(figsize=FIGSIZE_WIDE)
        ax.text(0.5, 0.5, "No recent race data on file",
                ha="center", va="center", color=TEXT, fontsize=11)
        ax.set_axis_off()
        return _to_png(fig)

    dates, places, sizes, statuses = zip(*pts)
    fig, ax = plt.subplots(figsize=FIGSIZE_WIDE)
    ax.scatter(dates, places, s=[s * 8 for s in sizes],
               c=BRASS, edgecolor=NAVY, linewidth=0.7, alpha=0.85)
    ax.invert_yaxis()  # 1st place on top
    ax.set_ylabel("Finishing position", fontsize=9)
    ax.set_title(f"{facts.boat_name} — recent results",
                 fontsize=11, color=NAVY, pad=12, fontweight="bold")
    _style(ax)
    fig.autofmt_xdate()
    return _to_png(fig)
