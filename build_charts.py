"""
Render static PNG charts for REPORT.md into report_assets/.
Light theme, stdlib + matplotlib + numpy.
"""
import json
from collections import OrderedDict, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
RUNS = json.loads((ROOT / "data" / "runs.json").read_text())
ASSETS = ROOT / "report_assets"
ASSETS.mkdir(exist_ok=True)

BP_SIZES = [10, 30, 50, 70, 90, 110]
VU_STEPS = [10, 20, 40, 80, 160, 320]

ENGINES = OrderedDict([
    ("maria122", {"display": "MariaDB 12.2.2", "color": "#d97706", "marker": "o"}),
    ("maria123", {"display": "MariaDB 12.3.1", "color": "#ca8a04", "marker": "D"}),
    ("mysql84",  {"display": "MySQL 8.4.8",    "color": "#0891b2", "marker": "s"}),
    ("mysql97",  {"display": "MySQL 9.7.0",    "color": "#4f46e5", "marker": "^"}),
])
EIDS = list(ENGINES.keys())

plt.rcParams.update({
    "figure.facecolor": "white",
    "axes.facecolor":   "white",
    "axes.edgecolor":   "#cbd5e1",
    "axes.labelcolor":  "#334155",
    "axes.titleweight": "600",
    "axes.titlesize":   13,
    "axes.titlepad":    14,
    "axes.labelsize":   10,
    "text.color":       "#0f172a",
    "xtick.color":      "#475569",
    "ytick.color":      "#475569",
    "grid.color":       "#e2e8f0",
    "grid.linewidth":   0.8,
    "legend.facecolor": "white",
    "legend.edgecolor": "#cbd5e1",
    "legend.framealpha": 1.0,
    "legend.fontsize":  9,
    "font.family":      "DejaVu Sans",
    "font.size":        10,
})


def clean_axes(ax, x_grid=False):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.yaxis.grid(True, color="#e2e8f0", lw=0.8, ls="-", alpha=1.0)
    if x_grid:
        ax.xaxis.grid(True, color="#e2e8f0", lw=0.8, ls="-", alpha=1.0)
    else:
        ax.xaxis.grid(False)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", length=0, pad=6)


def fmt_k(v, _):
    return f"{v:.0f}k"


# ── Data slicing ──────────────────────────────────────────────────────────────
MYSQL_EIDS = {"mysql84", "mysql97"}


def _instances_ok(r: dict) -> bool:
    if r["engine_id"] in MYSQL_EIDS:
        return r.get("bp_instances") == 2
    return True


def bp_sweep_run(eid: str) -> str | None:
    by_run = defaultdict(set)
    for r in RUNS:
        if r["engine_id"] != eid or r["vu"] != 80 or not _instances_ok(r):
            continue
        by_run[r["run_dir"]].add(r["bp_gib"])
    full = [(rd, s) for rd, s in by_run.items() if set(BP_SIZES).issubset(s)]
    if not full:
        return None
    return sorted(full, key=lambda x: x[0])[-1][0]


def bp_record(eid, bp):
    rd = bp_sweep_run(eid)
    if not rd:
        return None
    for r in RUNS:
        if (r["run_dir"] == rd and r["bp_gib"] == bp and r["vu"] == 80
                and _instances_ok(r)):
            return r
    return None


def vu_record(eid, vu):
    cands = [r for r in RUNS
             if r["engine_id"] == eid and r["bp_gib"] == 110
             and r["vu"] == vu and r["tpm"] and _instances_ok(r)]
    if not cands:
        return None
    return sorted(cands, key=lambda r: r["timestamp_utc"] or "")[-1]


def run_tpm(r):
    """Authoritative TPM: HammerDB's TEST RESULT if present, else the per-second mean."""
    if not r or not r.get("tpm") or not r["tpm"].get("avg"):
        return None
    hdb = r.get("hammerdb_reported")
    if hdb and hdb.get("tpm"):
        return float(hdb["tpm"])
    return r["tpm"]["avg"]


def series_tpm(r) -> np.ndarray:
    """Per-second TPM series — native TPM from the hdbtcount log, no scaling needed."""
    if not r or not r.get("tpm_series"):
        return np.array([])
    return np.array(r["tpm_series"], dtype=float)


def bp_series(eid):
    xs, ys = [], []
    for bp in BP_SIZES:
        v = run_tpm(bp_record(eid, bp))
        if v is not None:
            xs.append(bp)
            ys.append(v)
    return xs, ys


def vu_series(eid):
    xs, ys = [], []
    for vu in VU_STEPS:
        v = run_tpm(vu_record(eid, vu))
        if v is not None:
            xs.append(vu)
            ys.append(v)
    return xs, ys


# ── Figures ───────────────────────────────────────────────────────────────────
def fig1_bp_line():
    fig, ax = plt.subplots(figsize=(9.5, 5.2))
    for eid in EIDS:
        e = ENGINES[eid]
        xs, ys = bp_series(eid)
        if not xs:
            continue
        yk = [v / 1000 for v in ys]
        ax.plot(xs, yk, color=e["color"], lw=2.2,
                marker=e["marker"], ms=7, markerfacecolor="white",
                markeredgecolor=e["color"], markeredgewidth=1.8,
                label=e["display"])
    ax.set_xlabel("InnoDB Buffer Pool Size (GiB)")
    ax.set_ylabel("Average TPM (thousands)")
    ax.set_title("TPROC-C Throughput vs Buffer Pool Size  (80 VU · 60-min runs)")
    ax.set_xticks(BP_SIZES)
    ax.set_xticklabels([f"{s}G" for s in BP_SIZES])
    ax.yaxis.set_major_formatter(plt.FuncFormatter(fmt_k))
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper left")
    clean_axes(ax)
    fig.tight_layout()
    fig.savefig(ASSETS / "fig1_bp_line.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def fig3_vu_line():
    fig, ax = plt.subplots(figsize=(9.5, 5.2))
    for eid in EIDS:
        e = ENGINES[eid]
        xs, ys = vu_series(eid)
        if not xs:
            continue
        yk = [v / 1000 for v in ys]
        ax.plot(xs, yk, color=e["color"], lw=2.2,
                marker=e["marker"], ms=7, markerfacecolor="white",
                markeredgecolor=e["color"], markeredgewidth=1.8,
                label=e["display"])
    ax.set_xlabel("Virtual Users (log₂ scale)")
    ax.set_ylabel("Average TPM (thousands)")
    ax.set_title("TPROC-C Throughput vs Concurrency  (BP 110G)")
    ax.set_xscale("log", base=2)
    ax.set_xticks(VU_STEPS)
    ax.set_xticklabels([str(v) for v in VU_STEPS])
    ax.yaxis.set_major_formatter(plt.FuncFormatter(fmt_k))
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper left")
    clean_axes(ax)
    fig.tight_layout()
    fig.savefig(ASSETS / "fig3_vu_line.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def fig4_scaling():
    fig, ax = plt.subplots(figsize=(9.5, 5.2))
    ax.plot([10, 320], [1, 32], color="#94a3b8", lw=1.5, ls="--",
            label="Linear (ideal)", alpha=0.75, zorder=1)
    for eid in EIDS:
        e = ENGINES[eid]
        xs, ys = vu_series(eid)
        if len(xs) < 2:
            continue
        base = ys[0]
        eff = [y / base for y in ys]
        ax.plot(xs, eff, color=e["color"], lw=2.2,
                marker=e["marker"], ms=7, markerfacecolor="white",
                markeredgecolor=e["color"], markeredgewidth=1.8,
                label=e["display"])
    ax.set_xlabel("Virtual Users (log₂ scale)")
    ax.set_ylabel(f"Speedup vs lowest measured VU")
    ax.set_title("Concurrency Scaling Efficiency  (BP 110G)")
    ax.set_xscale("log", base=2)
    ax.set_yscale("log", base=2)
    ax.set_xticks(VU_STEPS)
    ax.set_xticklabels([str(v) for v in VU_STEPS])
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:g}×"))
    ax.legend(loc="upper left")
    clean_axes(ax, x_grid=True)
    fig.tight_layout()
    fig.savefig(ASSETS / "fig4_scaling.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def fig5_timeseries():
    """TPM timeseries at BP 50G / 80 VU — one line per engine."""
    fig, ax = plt.subplots(figsize=(11.5, 4.8))
    for eid in EIDS:
        e = ENGINES[eid]
        r = bp_record(eid, 50)
        notpm = series_tpm(r)
        if notpm.size == 0:
            continue
        t_min = np.arange(len(notpm)) / 60.0
        ax.plot(t_min, notpm / 1000, color=e["color"], lw=0.4, alpha=0.25)
        # Edge-safe 60 s rolling mean — uses a shrinking window at the boundaries
        # so the smoothed line doesn't dive toward zero at the edges.
        window = 60
        csum = np.concatenate(([0.0], np.cumsum(notpm)))
        lo = np.maximum(np.arange(len(notpm)) - window // 2, 0)
        hi = np.minimum(np.arange(len(notpm)) + window // 2 + 1, len(notpm))
        smooth = (csum[hi] - csum[lo]) / (hi - lo)
        ax.plot(t_min, smooth / 1000, color=e["color"], lw=2.0, label=e["display"])
    ax.set_xlabel("Elapsed time (minutes, steady-state only)")
    ax.set_ylabel("TPM (thousands)")
    ax.set_title("TPM Over Time — BP 50G, 80 VU  (thin = per-second, thick = 60 s rolling)")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(fmt_k))
    ax.set_ylim(bottom=0)
    ax.legend(loc="lower right")
    clean_axes(ax)
    fig.tight_layout()
    fig.savefig(ASSETS / "fig5_timeseries.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


ENGINE_SHORT = {"maria122": "MDB 12.2", "maria123": "MDB 12.3",
                "mysql84":  "MySQL 8.4", "mysql97": "MySQL 9.7"}


def _grouped_boxplot(group_keys, group_label_fmt, record_fn, title, outname):
    """
    Grouped boxplot: outer groups (BP size or VU count), inner engines.
    Each engine's samples are normalized by that engine's own median in that
    group, so every box is centered on 1.0 and the spread shows purely
    intra-engine run-to-run variation. That way an engine that simply runs
    consistently slower than the pack doesn't look like it has skew.
    Engines that have no data for a given group are simply skipped.
    """
    slot       = 1.0                    # width per engine slot
    group_span = len(EIDS) * slot
    gap        = group_span * 0.45      # blank space between outer groups

    positions, data, colors, tick_labels = [], [], [], []
    group_centers = []
    cursor = 0.0
    for key in group_keys:
        any_in_group = False
        for i, eid in enumerate(EIDS):
            r = record_fn(key, eid)
            full = series_tpm(r)
            arr = full[full > 0]
            if arr.size == 0:
                continue
            med = float(np.median(arr))
            if med <= 0:
                continue
            positions.append(cursor + i * slot + slot / 2)
            data.append(arr / med)
            colors.append(ENGINES[eid]["color"])
            tick_labels.append(ENGINE_SHORT[eid].split()[-1])
            any_in_group = True
        if any_in_group:
            group_centers.append((key, cursor + group_span / 2))
        cursor += group_span + gap

    total_w = cursor - gap
    fig_w = max(11.0, 0.38 * total_w + 3)
    fig, ax = plt.subplots(figsize=(fig_w, 6.0))

    # alternating group shading (behind boxes)
    for i, (_, center) in enumerate(group_centers):
        if i % 2 == 1:
            ax.axvspan(center - group_span / 2 - gap / 2,
                       center + group_span / 2 + gap / 2,
                       color="#f1f5f9", zorder=0)

    bx = ax.boxplot(
        data, positions=positions, widths=slot * 0.78,
        showfliers=False, whis=(5, 95), patch_artist=True,
        medianprops=dict(color="#0f172a", lw=1.5),
        whiskerprops=dict(color="#475569", lw=1.1),
        capprops=dict(color="#475569", lw=1.1),
    )
    for patch, c in zip(bx["boxes"], colors):
        patch.set_facecolor(c)
        patch.set_alpha(0.88)
        patch.set_edgecolor(c)

    ax.set_xticks(positions)
    ax.set_xticklabels(tick_labels, rotation=0, fontsize=7.5, color="#64748b")
    ax.tick_params(axis="x", pad=2)

    # bold outer group labels ABOVE the boxes (on top of the alternating bands)
    for key, center in group_centers:
        ax.text(center, 0.98, group_label_fmt.format(key),
                transform=ax.get_xaxis_transform(),
                ha="center", va="top", fontsize=12, fontweight="700",
                color="#0f172a",
                bbox=dict(boxstyle="round,pad=0.25", fc="white",
                          ec="#cbd5e1", lw=0.8))

    ax.set_ylabel("TPM / engine median")
    ax.set_title(title)
    ax.axhline(1.0, color="#94a3b8", lw=0.8, ls="--", zorder=1)
    ax.set_ylim(bottom=0)
    ax.set_xlim(-gap / 2, cursor - gap / 2)

    legend_patches = [plt.Rectangle((0, 0), 1, 1, color=ENGINES[e]["color"], alpha=0.88,
                                    label=ENGINES[e]["display"]) for e in EIDS]
    ax.legend(handles=legend_patches, loc="lower right", ncol=2, framealpha=0.95)

    clean_axes(ax)
    fig.tight_layout()
    fig.savefig(ASSETS / outname, dpi=150)
    plt.close(fig)


def fig6_jitter_bp():
    _grouped_boxplot(
        group_keys=BP_SIZES,
        group_label_fmt="BP {}G",
        record_fn=lambda bp, eid: bp_record(eid, bp),
        title="TPM Jitter — Buffer Pool Iterations  (80 VU · each engine normalized to its own median · boxes = P25–P75 · whiskers = P5–P95)",
        outname="fig6_jitter_bp.png",
    )


def fig7_jitter_vu():
    _grouped_boxplot(
        group_keys=VU_STEPS,
        group_label_fmt="{} VU",
        record_fn=lambda vu, eid: vu_record(eid, vu),
        title="TPM Jitter — Virtual Users Iterations  (BP 110G · each engine normalized to its own median · boxes = P25–P75 · whiskers = P5–P95)",
        outname="fig7_jitter_vu.png",
    )


def main():
    fig1_bp_line()
    fig3_vu_line()
    fig4_scaling()
    fig5_timeseries()
    fig6_jitter_bp()
    fig7_jitter_vu()
    for p in sorted(ASSETS.glob("fig*.png")):
        print(f"  wrote {p.relative_to(ROOT)}  ({p.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
