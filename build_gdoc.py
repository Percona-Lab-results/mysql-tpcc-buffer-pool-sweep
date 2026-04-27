"""
Build a self-contained HTML report (report_gdoc.html) for upload to Google Docs.

Reuses the data selectors from build_report.py (get_bp_record, get_vu_record, etc.)
so the numbers always match REPORT.md. Charts are rendered inline as base64 PNGs.
"""
import base64
import io
from datetime import datetime
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

from build_report import (
    ENGINES, EIDS, BP_SIZES, VU_STEPS,
    get_bp_record, get_vu_record,
    peak_bp_80vu, peak_vu_bp110, scaling_ratio,
    engine_cnf, SECTION_MAP, MARIA_ONLY, EXTRAS_KEEP,
    run_tpm, tpm_stats,
)

ROOT = Path(__file__).resolve().parent

ENGINE_META = {
    "maria122": {"color": "#d95f02", "marker": "o"},
    "maria123": {"color": "#e6ab02", "marker": "D"},
    "mysql84":  {"color": "#1b9e77", "marker": "s"},
    "mysql97":  {"color": "#7570b3", "marker": "^"},
}

C_BG, C_CARD, C_GRID, C_FG, C_DIM, C_AXIS = (
    "#ffffff", "#ffffff", "#e0e0e0", "#1a1a1a", "#555555", "#cccccc"
)
plt.rcParams.update({
    "figure.facecolor":  C_BG,
    "axes.facecolor":    C_CARD,
    "axes.edgecolor":    C_AXIS,
    "axes.labelcolor":   C_DIM,
    "text.color":        C_FG,
    "xtick.color":       C_DIM,
    "ytick.color":       C_DIM,
    "grid.color":        C_GRID,
    "grid.linewidth":    0.7,
    "legend.facecolor":  C_CARD,
    "legend.edgecolor":  C_AXIS,
    "legend.framealpha": 1.0,
    "legend.fontsize":   10,
    "font.family":       "DejaVu Sans",
    "font.size":         9,
    "axes.titlesize":    11,
    "axes.titlepad":     12,
    "axes.labelsize":    9,
})


def _clean_axes(ax, x_grid=False):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(C_AXIS)
    ax.spines["bottom"].set_color(C_AXIS)
    ax.yaxis.grid(True, color=C_GRID, lw=0.6, ls="-", alpha=0.7)
    if x_grid:
        ax.xaxis.grid(True, color=C_GRID, lw=0.6, ls=":", alpha=0.6)
    else:
        ax.xaxis.grid(False)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", length=4, pad=6, colors=C_DIM)


def fig_to_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=144, bbox_inches="tight",
                pad_inches=0.15, facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()


# ── Data series helpers ──────────────────────────────────────────────────────
def bp_series(eid):
    xs, ys = [], []
    for bp in BP_SIZES:
        v = run_tpm(get_bp_record(eid, bp))
        if v is not None:
            xs.append(bp)
            ys.append(v)
    return xs, ys


def vu_series(eid):
    xs, ys = [], []
    for vu in VU_STEPS:
        v = run_tpm(get_vu_record(eid, vu))
        if v is not None:
            xs.append(vu)
            ys.append(v)
    return xs, ys


def _series_tpm(r):
    """Per-second TPM series — native from hdbtcount, no scaling needed."""
    if not r or not r.get("tpm_series"):
        return np.array([])
    return np.array(r["tpm_series"], dtype=float)


# ── Figures ──────────────────────────────────────────────────────────────────
def fig_bp_line():
    fig, ax = plt.subplots(figsize=(11.5, 6.0))
    for eid in EIDS:
        m = ENGINE_META[eid]
        xs, ys = bp_series(eid)
        if not xs:
            continue
        ax.plot(xs, [y / 1000 for y in ys], color=m["color"], lw=2.5,
                marker=m["marker"], ms=7, markerfacecolor="white",
                markeredgecolor=m["color"], markeredgewidth=2,
                label=ENGINES[eid]["display"], zorder=5)
    ax.set_xlabel("InnoDB Buffer Pool Size (GiB)")
    ax.set_ylabel("Average TPM (thousands)")
    ax.set_title("TPROC-C Throughput vs Buffer Pool Size  [80 VU · 3600 s]")
    ax.set_xticks(BP_SIZES)
    ax.set_xticklabels([f"{s}G" for s in BP_SIZES])
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0f}k"))
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper left", fontsize=9)
    _clean_axes(ax)
    fig.tight_layout(pad=1.5)
    return fig_to_b64(fig)


def fig_vu_line():
    fig, ax = plt.subplots(figsize=(11.5, 6.0))
    for eid in EIDS:
        m = ENGINE_META[eid]
        xs, ys = vu_series(eid)
        if not xs:
            continue
        ax.plot(xs, [y / 1000 for y in ys], color=m["color"], lw=2.5,
                marker=m["marker"], ms=7, markerfacecolor="white",
                markeredgecolor=m["color"], markeredgewidth=2,
                label=ENGINES[eid]["display"], zorder=5)
    ax.set_xlabel("Virtual Users (log₂ scale)")
    ax.set_ylabel("Average TPM (thousands)")
    ax.set_title("TPROC-C Throughput vs Concurrency  [BP 110G · 3600 s]")
    ax.set_xscale("log", base=2)
    ax.set_xticks(VU_STEPS)
    ax.set_xticklabels([str(v) for v in VU_STEPS])
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0f}k"))
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper left", fontsize=9)
    _clean_axes(ax)
    fig.tight_layout(pad=1.5)
    return fig_to_b64(fig)


def fig_scaling():
    fig, ax = plt.subplots(figsize=(11.5, 6.0))
    ax.plot([VU_STEPS[0], VU_STEPS[-1]], [1, VU_STEPS[-1] / VU_STEPS[0]],
            color=C_AXIS, lw=1.5, ls="--", label="Linear (ideal)",
            alpha=0.5, zorder=1)
    for eid in EIDS:
        m = ENGINE_META[eid]
        xs, ys = vu_series(eid)
        if len(xs) < 2:
            continue
        base = ys[0]
        ax.plot(xs, [y / base for y in ys], color=m["color"], lw=2.5,
                marker=m["marker"], ms=7, markerfacecolor="white",
                markeredgecolor=m["color"], markeredgewidth=2,
                label=ENGINES[eid]["display"], zorder=5)
    # CPU topology reference lines
    ax.axvline(x=40, color="#d32f2f", lw=1.3, ls="-.", alpha=0.6, zorder=2)
    ax.text(40, 1.02, "  40 physical cores", transform=ax.get_xaxis_transform(),
            color="#d32f2f", fontsize=7.5, va="bottom", ha="left", alpha=0.8)
    ax.axvline(x=80, color="#1565c0", lw=1.3, ls="-.", alpha=0.6, zorder=2)
    ax.text(80, 1.02, "  80 HT threads", transform=ax.get_xaxis_transform(),
            color="#1565c0", fontsize=7.5, va="bottom", ha="left", alpha=0.8)
    ax.set_xlabel("Virtual Users (log₂ scale)")
    ax.set_ylabel("Speedup vs lowest measured VU")
    ax.set_title("Concurrency Scaling Efficiency  [BP 110G]")
    ax.set_xscale("log", base=2)
    ax.set_yscale("log", base=2)
    ax.set_xticks(VU_STEPS)
    ax.set_xticklabels([str(v) for v in VU_STEPS])
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:g}×"))
    ax.legend(fontsize=9)
    _clean_axes(ax, x_grid=True)
    fig.tight_layout(pad=1.5)
    return fig_to_b64(fig)


def fig_timeseries():
    fig, ax = plt.subplots(figsize=(11.5, 5.5))
    for eid in EIDS:
        m = ENGINE_META[eid]
        r = get_bp_record(eid, 50)
        notpm = _series_tpm(r)
        if notpm.size == 0:
            continue
        t_min = np.arange(len(notpm)) / 60.0
        ax.plot(t_min, notpm / 1000, color=m["color"], lw=0.4, alpha=0.2)
        # Edge-safe 60 s rolling mean (shrinks the window at the boundaries)
        window = 60
        csum = np.concatenate(([0.0], np.cumsum(notpm)))
        lo = np.maximum(np.arange(len(notpm)) - window // 2, 0)
        hi = np.minimum(np.arange(len(notpm)) + window // 2 + 1, len(notpm))
        smooth = (csum[hi] - csum[lo]) / (hi - lo)
        ax.plot(t_min, smooth / 1000, color=m["color"], lw=2.2,
                label=ENGINES[eid]["display"])
    ax.set_xlabel("Elapsed time (minutes, steady-state only)")
    ax.set_ylabel("TPM (thousands)")
    ax.set_title("TPM Over Time — BP 50G, 80 VU  [thin = per-second, thick = 60 s rolling]")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0f}k"))
    ax.set_ylim(bottom=0)
    ax.legend(loc="lower right", fontsize=9)
    _clean_axes(ax)
    fig.tight_layout(pad=1.5)
    return fig_to_b64(fig)


def _jitter_box(ax, keys, record_fn, normalize=False):
    n = len(EIDS)
    w_total = 0.7
    w = w_total / n
    for i, eid in enumerate(EIDS):
        col = ENGINE_META[eid]["color"]
        offset = (i - n / 2 + 0.5) * w
        for j, key in enumerate(keys):
            r = record_fn(key, eid)
            full = _series_tpm(r)
            arr = full[full > 0]
            if arr.size == 0:
                continue
            if normalize:
                m = arr.mean()
                if m == 0:
                    continue
                vals = arr / m * 100
            else:
                vals = arr / 1000
            ax.boxplot(
                vals, positions=[j + offset], widths=w * 0.85,
                patch_artist=True, notch=False, showfliers=False,
                whis=(5, 95),
                medianprops=dict(color="#333333", lw=2),
                boxprops=dict(facecolor=col + "30", alpha=1, linewidth=1.3, edgecolor=col),
                whiskerprops=dict(color=col, lw=1.2, alpha=0.8, linestyle=(0, (4, 3))),
                capprops=dict(color=col, lw=1.6),
                manage_ticks=False,
            )


def fig_jitter_bp():
    fig, ax = plt.subplots(figsize=(11.5, 6.0))
    _jitter_box(ax, BP_SIZES, lambda bp, eid: get_bp_record(eid, bp))
    ax.set_xticks(range(len(BP_SIZES)))
    ax.set_xticklabels([f"{s}G" for s in BP_SIZES])
    ax.set_xlabel("InnoDB Buffer Pool Size (GiB)")
    ax.set_ylabel("TPM (thousands) — steady-state")
    ax.set_title("TPM Jitter — Buffer Pool Iterations  [80 VU · boxes = P25–P75 · whiskers = P5–P95]")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0f}k"))
    handles = [mpatches.Patch(color=ENGINE_META[eid]["color"], label=ENGINES[eid]["display"])
               for eid in EIDS]
    ax.legend(handles=handles, loc="upper left", fontsize=9)
    ax.set_ylim(bottom=0)
    _clean_axes(ax)
    fig.tight_layout(pad=1.5)
    return fig_to_b64(fig)


def fig_jitter_vu():
    fig, ax = plt.subplots(figsize=(11.5, 6.0))
    _jitter_box(ax, VU_STEPS, lambda vu, eid: get_vu_record(eid, vu), normalize=True)
    ax.axhline(y=100, color=C_DIM, lw=1.2, ls="--", alpha=0.5, zorder=1)
    ax.set_xticks(range(len(VU_STEPS)))
    ax.set_xticklabels([str(v) for v in VU_STEPS])
    ax.set_xlabel("Virtual Users")
    ax.set_ylabel("TPM as % of mean — steady-state")
    ax.set_title("Normalized TPM Jitter — VU Iterations  [BP 110G]")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    handles = [mpatches.Patch(color=ENGINE_META[eid]["color"], label=ENGINES[eid]["display"])
               for eid in EIDS]
    ax.legend(handles=handles, loc="lower left", fontsize=9)
    _clean_axes(ax)
    fig.tight_layout(pad=1.5)
    return fig_to_b64(fig)


# ── Tables ───────────────────────────────────────────────────────────────────
def _fmt(n):
    return f"{int(round(n)):,}"


def engine_th():
    return "".join(
        f'<th style="color:{ENGINE_META[e]["color"]}">{ENGINES[e]["display"]}</th>'
        for e in EIDS
    )


def bp_table_html():
    rows = []
    for bp in BP_SIZES:
        vals = [run_tpm(get_bp_record(eid, bp)) for eid in EIDS]
        mx = max((v for v in vals if v is not None), default=None)
        cells = f'<td>{bp}G</td>'
        for v in vals:
            if v is None:
                cells += "<td>—</td>"
            else:
                cls = ' class="win"' if v == mx else ""
                cells += f'<td{cls}>{_fmt(v)}</td>'
        rows.append(f"<tr>{cells}</tr>")
    return "\n".join(rows)


def vu_table_html():
    rows = []
    for vu in VU_STEPS:
        vals = [run_tpm(get_vu_record(eid, vu)) for eid in EIDS]
        mx = max((v for v in vals if v is not None), default=None)
        cells = f'<td>{vu}</td>'
        for v in vals:
            if v is None:
                cells += "<td>—</td>"
            else:
                cls = ' class="win"' if v == mx else ""
                cells += f'<td{cls}>{_fmt(v)}</td>'
        rows.append(f"<tr>{cells}</tr>")
    return "\n".join(rows)


def jitter_table_html(group_keys, key_label_fn, record_fn):
    rows = [
        '<thead><tr>'
        '<th>Config</th><th>Engine</th><th>Mean TPM</th>'
        '<th>Std Dev</th><th title="Coefficient of Variation = std/mean × 100">CV%</th>'
        '<th>P5</th><th>P95</th><th>P5‑P95 Range</th>'
        '</tr></thead><tbody>',
    ]
    for key in group_keys:
        for eid in EIDS:
            s = tpm_stats(record_fn(key, eid))
            if not s:
                continue
            e_col = ENGINE_META[eid]["color"]
            rows.append(
                f'<tr><td>{key_label_fn(key)}</td>'
                f'<td style="color:{e_col}">{ENGINES[eid]["display"]}</td>'
                f'<td>{_fmt(s["mean"])}</td><td>{_fmt(s["std"])}</td>'
                f'<td>{s["cv_pct"]:.1f}%</td><td>{_fmt(s["p5"])}</td>'
                f'<td>{_fmt(s["p95"])}</td><td>{_fmt(s["p95"]-s["p5"])}</td></tr>'
            )
    rows.append('</tbody>')
    return "\n".join(rows)


def cfg_table_html():
    cnfs = {e: engine_cnf(e) for e in EIDS}
    out = []
    seen = set()
    n_cols = len(EIDS) + 1
    for section, keys in SECTION_MAP.items():
        section_rows = []
        for k in keys:
            vals = {e: cnfs[e].get(k, "") for e in EIDS}
            if not any(vals.values()):
                continue
            seen.add(k)
            maria = k in MARIA_ONLY
            uniq = set(v for v in vals.values() if v)
            differs = len(uniq) > 1
            cls = ' class="cfg-maria"' if maria else (' class="cfg-diff"' if differs else "")
            badge = ' <span class="badge-maria">MariaDB only</span>' if maria else ""
            cells = f'<td class="cfg-param">{k}{badge}</td>'
            for eid in EIDS:
                v = vals[eid]
                cells += f'<td>{v}</td>' if v else '<td><span class="cfg-na">n/a</span></td>'
            section_rows.append(f'<tr{cls}>{cells}</tr>')
        if section_rows:
            out.append(f'<tr class="cfg-section"><td colspan="{n_cols}">{section}</td></tr>')
            out.extend(section_rows)
    # Extras
    all_keys = set()
    for e in EIDS:
        all_keys |= set(cnfs[e].keys())
    extras_keys = sorted((all_keys & EXTRAS_KEEP) - seen)
    if extras_keys:
        out.append(f'<tr class="cfg-section"><td colspan="{n_cols}">Other</td></tr>')
        for k in extras_keys:
            vals = {e: cnfs[e].get(k, "") for e in EIDS}
            maria = k in MARIA_ONLY
            uniq = set(v for v in vals.values() if v)
            differs = len(uniq) > 1
            cls = ' class="cfg-maria"' if maria else (' class="cfg-diff"' if differs else "")
            badge = ' <span class="badge-maria">MariaDB only</span>' if maria else ""
            cells = f'<td class="cfg-param">{k}{badge}</td>'
            for eid in EIDS:
                v = vals[eid]
                cells += f'<td>{v}</td>' if v else '<td><span class="cfg-na">n/a</span></td>'
            out.append(f'<tr{cls}>{cells}</tr>')
    return "\n".join(out)


# ── Exec summary / pills ─────────────────────────────────────────────────────
def pills_html():
    parts = []
    for eid in EIDS:
        c = ENGINE_META[eid]["color"]
        parts.append(f'<span class="pill" style="border-color:{c};color:{c}">{ENGINES[eid]["display"]}</span>')
    parts += [
        '<span class="pill">HammerDB 5.0</span>',
        '<span class="pill">Ubuntu 24.04</span>',
        '<span class="pill">3600 s runs</span>',
        '<span class="pill">600 s ramp-up</span>',
        f'<span class="pill">Generated {datetime.now().strftime("%Y-%m-%d")}</span>',
    ]
    return "\n    ".join(parts)


def kpi_html():
    peak_bp = {e: peak_bp_80vu(e) for e in EIDS}
    peak_vu = {e: peak_vu_bp110(e) for e in EIDS}
    scal    = {e: scaling_ratio(e) for e in EIDS}
    parts = []
    for eid in EIDS:
        c = ENGINE_META[eid]["color"]
        val = f"{int(peak_bp[eid]):,}" if peak_bp[eid] else "—"
        parts.append(f'''<div class="kpi">
      <div class="kpi-label">{ENGINES[eid]["display"]} peak TPM</div>
      <div class="kpi-val" style="color:{c}">{val}</div>
      <div class="kpi-sub">BP iterations · 80 VU</div>
    </div>''')
    for eid in EIDS:
        c = ENGINE_META[eid]["color"]
        val = f"{scal[eid]:.1f}×" if scal[eid] else "—"
        parts.append(f'''<div class="kpi">
      <div class="kpi-label">{ENGINES[eid]["display"]} scaling (10→320 VU)</div>
      <div class="kpi-val" style="color:{c}">{val}</div>
      <div class="kpi-sub">BP 110G</div>
    </div>''')
    return "\n".join(parts)


# ── Render ───────────────────────────────────────────────────────────────────
print("Generating charts...")
img_bp_line   = fig_bp_line()
img_vu_line   = fig_vu_line()
img_scaling   = fig_scaling()
img_ts        = fig_timeseries()
img_jitter_bp = fig_jitter_bp()
img_jitter_vu = fig_jitter_vu()
print("Charts done.")

HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Database Benchmark Comparison — TPROC-C Report</title>
<style>
  body {{
    font-family: Arial, Helvetica, sans-serif;
    background: #ffffff;
    color: #222222;
    line-height: 1.6;
    max-width: 900px;
    margin: 0 auto;
    padding: 20px;
  }}
  a {{ color: #1a73e8; }}
  h1 {{ font-size: 22pt; color: #1a1a1a; margin-bottom: 8px; }}
  h2 {{ font-size: 14pt; color: #333; margin-top: 32px; border-bottom: 2px solid #ddd; padding-bottom: 6px; }}
  h3 {{ font-size: 12pt; color: #555; margin-top: 24px; }}
  p {{ font-size: 11pt; color: #333; margin-bottom: 10px; line-height: 1.7; }}
  .subtitle {{ color: #666; font-size: 10pt; margin-bottom: 16px; }}
  .pills {{ margin: 12px 0 24px; }}
  .pill {{
    display: inline-block;
    font-size: 9pt;
    padding: 2px 10px;
    border-radius: 12px;
    border: 1px solid #ccc;
    color: #555;
    margin: 2px 4px 2px 0;
  }}
  img {{ max-width: 100%; height: auto; margin: 16px 0; }}
  .chart-caption {{ font-size: 9pt; color: #888; font-style: italic; margin-bottom: 20px; }}

  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 10pt;
    margin: 16px 0;
  }}
  th {{
    text-align: left;
    background: #f5f5f5;
    padding: 8px 10px;
    border: 1px solid #ddd;
    font-weight: 700;
    font-size: 9pt;
    color: #444;
  }}
  td {{
    padding: 6px 10px;
    border: 1px solid #ddd;
  }}
  td.win {{ font-weight: 700; }}
  tr:nth-child(even) td {{ background: #fafafa; }}

  .callout {{
    background: #f0f4f8;
    border-left: 4px solid #4a90d9;
    padding: 12px 16px;
    font-size: 10.5pt;
    color: #333;
    margin: 16px 0;
  }}
  .kpi-grid {{ margin: 16px 0; }}
  .kpi {{
    display: inline-block;
    border: 1px solid #ddd;
    border-radius: 8px;
    padding: 12px 16px;
    margin: 4px 8px 4px 0;
    min-width: 180px;
    vertical-align: top;
  }}
  .kpi .kpi-label {{ font-size: 8pt; color: #777; text-transform: uppercase; }}
  .kpi .kpi-val {{ font-size: 16pt; font-weight: 700; margin: 4px 0; }}
  .kpi .kpi-sub {{ font-size: 8pt; color: #999; }}

  .cfg-section td {{ background: #f0f0f0; font-weight: 700; font-size: 9pt; color: #555; }}
  .cfg-param {{ font-family: 'Consolas','Courier New',monospace; }}
  .cfg-maria td {{ background: #fff8f0; }}
  .cfg-maria .cfg-param {{ color: #c45000; }}
  .cfg-diff td {{ background: #f5f0ff; }}
  .cfg-diff .cfg-param {{ color: #6b21a8; }}
  .cfg-na {{ color: #bbb; font-style: italic; }}
  .badge-maria {{
    font-size: 7pt;
    background: #fff3e0;
    border: 1px solid #e6a04080;
    color: #c45000;
    padding: 1px 4px;
    border-radius: 3px;
    margin-left: 4px;
  }}
  footer {{ border-top: 1px solid #ddd; padding-top: 16px; font-size: 9pt; color: #999; text-align: center; margin-top: 40px; }}
</style>
</head>
<body>

<h1>Database Benchmark Comparison — TPROC-C Report</h1>
<div class="subtitle">HammerDB 5.0 · TPROC-C · 1000 warehouses · beast-node2.tp.int.percona.com · 80 logical CPUs · 187.54 GiB RAM · NVMe</div>
<div class="pills">
  {pills_html()}
</div>

<h2>About HammerDB</h2>
<p>
  <strong>HammerDB</strong> is an open-source database benchmarking tool that simulates real-world
  transactional workloads against relational databases. It implements industry-standard benchmarks
  including <strong>TPROC-C</strong> (derived from TPC-C), which models an order-processing warehouse
  system — one of the most widely used OLTP benchmarks for evaluating database throughput,
  concurrency scaling, and latency under load.
</p>
<p>
  In a TPROC-C run, HammerDB spawns multiple <strong>virtual users (VUs)</strong>, each acting as an
  independent client that continuously executes a mix of five transaction types: new-order (45%),
  payment (43%), order-status (4%), delivery (4%), and stock-level (4%). The primary metric is
  <strong>TPM</strong> — total TPC-C transactions per minute across the whole mix (HammerDB
  also reports <strong>NOPM</strong>, the new-order subset). This report plots per-second TPM
  samples from HammerDB's own transaction counter (<code>hdbtcount_*.log</code>); the mean of
  the steady-state window agrees with HammerDB's end-of-run
  <code>TEST RESULT: System achieved &lt;N&gt; NOPM from &lt;M&gt; TPM</code> line within 1%.
</p>

<h2>Executive Summary</h2>
<div class="kpi-grid">
  {kpi_html()}
</div>
<div class="callout">
  TPM comes from HammerDB's own per-second transaction counter
  (<code>hdbtcount_*.log</code>); the steady-state mean matches the end-of-run
  <code>TEST RESULT</code> line in <code>hammerdbcli.out</code> within 1%. For MySQL, only
  runs with <code>innodb_buffer_pool_instances = 2</code> are included for an apples-to-apples
  comparison.
</div>

<h2>Buffer Pool Iterations <span style="font-weight:400;color:#3d5070;font-size:0.8rem">80 VU · 10G – 110G</span></h2>
<p>
  The <strong>InnoDB Buffer Pool</strong> is the main memory area where InnoDB caches table data
  and index pages. Every read that hits the buffer pool avoids a disk I/O; every miss forces a
  physical read from storage. For write-heavy OLTP workloads like TPROC-C, the buffer pool also
  holds dirty pages waiting to be flushed — a larger pool means fewer flush cycles and less
  I/O contention between foreground transactions and background flushing.
</p>
<p>
  A <strong>buffer pool iteration</strong> varies this single parameter (from 10 GiB to 110 GiB in
  20 GiB steps) while holding everything else constant — 80 virtual users, 1000 warehouses
  (~100 GB working set), same hardware, same configuration. This isolates the effect of memory
  pressure on throughput. At small pool sizes (10–30G) only a fraction of the hot data fits
  in RAM, so performance is dominated by disk I/O speed and the engine’s read-ahead and
  flushing strategies. As the pool grows toward and past the working set size, more reads hit
  cache and fewer dirty-page evictions are needed, revealing the engine’s in-memory
  efficiency.
</p>
<p>
  The 80 VU count was chosen to represent a moderate-to-high concurrency level typical of
  production OLTP servers (80 logical CPUs on this host), ensuring that throughput differences
  reflect buffer pool efficiency rather than single-thread performance.
</p>
<img width="899" src="data:image/png;base64,{img_bp_line}" alt="BP iterations line chart">
<div class="chart-caption">Figure 1 — Average TPM vs buffer pool size. Each point is the steady-state average (post-ramp-up).</div>
<table>
  <thead><tr><th>BP Size</th>{engine_th()}</tr></thead>
  <tbody>{bp_table_html()}</tbody>
</table>

<h2>Virtual Users Iterations <span style="font-weight:400;color:#3d5070;font-size:0.8rem">BP 110G · 10 – 320 VU</span></h2>
<p>
  A <strong>Virtual User (VU)</strong> is a HammerDB worker thread that simulates an independent
  database client. Each VU opens its own connection, picks a random warehouse, and continuously
  executes the TPROC-C transaction mix (new-order, payment, delivery, order-status, stock-level)
  in a tight loop for the duration of the run. Increasing the VU count is equivalent to increasing
  the number of concurrent application threads hitting the database simultaneously.
</p>
<p>
  VU count directly stresses the database engine’s concurrency internals: InnoDB row-level
  locking, the lock manager, undo/purge scheduling, buffer pool latch contention, and redo log
  synchronisation. At low VU counts the engine is mostly CPU- and I/O-bound; as VU rises,
  internal latch contention and lock waits become the dominant bottleneck. The point where
  throughput plateaus reveals how efficiently the engine scales under parallel workloads —
  a critical metric for multi-tenant and connection-pool-heavy OLTP deployments.
</p>
<p>
  Concurrency was iterated through {{{", ".join(str(v) for v in VU_STEPS)}}} virtual users with
  a fixed 110 GiB buffer pool. Each VU count ran for 3600 s with a 600 s ramp-up. MariaDB 12.2.2
  was only benchmarked at 80 VU in this dataset; its other points are omitted.
</p>
<img width="899" src="data:image/png;base64,{img_vu_line}" alt="VU iterations line chart">
<div class="chart-caption">Figure 3 — TPM vs virtual users (log₂ X-axis).</div>
<img width="899" src="data:image/png;base64,{img_scaling}" alt="Scaling efficiency">
<div class="chart-caption">Figure 4 — Speedup vs lowest measured VU on log/log axes. Dashed = ideal linear scaling. Vertical lines mark physical core count (40) and HT thread count (80).</div>
<p>
  <strong>Note on scaling vs peak throughput:</strong> Absolute TPM and relative scaling tell
  different stories. An engine with a higher single-thread baseline saturates the available CPU
  resources sooner in relative terms, so its 10→320 VU multiplier is mechanically lower than
  a slower-starting engine that still has headroom to grow. Beyond the physical core count
  (40 cores / 80 HT threads on this system), even a perfectly scalable engine cannot maintain
  linear speedup — threads begin competing for the same execution units, and InnoDB internal
  serialisation points (lock manager, redo log, buffer pool latches) become the bottleneck. A
  higher baseline simply means the engine hits that ceiling at a lower multiplier, not that it
  scales worse in absolute terms.
</p>
<table>
  <thead><tr><th>VU</th>{engine_th()}</tr></thead>
  <tbody>{vu_table_html()}</tbody>
</table>

<h2>TPM Stability <span style="font-weight:400;color:#3d5070;font-size:0.8rem">BP 50G · 80 VU</span></h2>
<p>
  <strong>TPM Stability</strong> measures how consistently a database sustains its throughput
  over the entire duration of a benchmark run. A high average TPM is meaningless if the engine
  periodically stalls — background checkpoint flushes, purge operations, or adaptive flushing
  can cause sharp dips that ripple through the application as latency spikes.
</p>
<p>
  The chart below plots per-second TPM for the full 3600-second steady-state window (ramp-up
  excluded) at BP 50G with 80 virtual users. 50 GiB is roughly half the working set, which is
  where memory pressure is most visible — the buffer pool is neither trivially small nor
  comfortably oversized, so differences in checkpoint/flush behaviour between engines show up
  most clearly. Thin lines are raw 1-second samples; thick lines are 60-second rolling
  averages. A flat rolling average indicates stable throughput; wide oscillations suggest
  periodic internal bottlenecks (e.g. InnoDB log checkpointing, buffer pool flushing, or
  purge lag).
</p>
<img width="899" src="data:image/png;base64,{img_ts}" alt="TPM timeseries">
<div class="chart-caption">Figure 5 — TPM over elapsed time. BP 50G · 80 VU.</div>

<h2>TPM Jitter <span style="font-weight:400;color:#3d5070;font-size:0.8rem">steady-state windows · box = P25‑P75 · whiskers = P5‑P95</span></h2>
<p>
  <strong>TPM Jitter</strong> quantifies the <em>spread</em> of second-to-second throughput
  variation during the steady-state portion of each run (ramp-up excluded). While the Stability
  chart above shows the full time-series shape, jitter distills it into a single statistical
  picture: how tightly packed are the per-second TPM readings around the mean?
</p>
<p>
  A database with low jitter delivers predictable response times, simplifies capacity planning,
  and avoids tail-latency violations under peak load. High jitter forces the application tier to
  absorb throughput dips through connection pooling, retry logic, or queuing — adding
  complexity and latency even when the average throughput looks good.
</p>
<p>
  Each box shows the P25–P75 range (interquartile), the centre line is the median, and
  whiskers extend to P5–P95. The tables include <strong>CV%</strong> (Coefficient of
  Variation = std / mean × 100): a scale-free measure where lower is more stable. Unlike
  raw standard deviation, CV% is directly comparable across runs with different mean throughputs.
</p>

<h3>Buffer Pool Iterations</h3>
<img width="899" src="data:image/png;base64,{img_jitter_bp}" alt="BP jitter">
<div class="chart-caption">Figure 6 — TPM distribution per buffer pool size (steady-state).</div>
<p>
  At small buffer pool sizes (10–50G), both MariaDB versions exhibit noticeably wider TPM
  spread (CV 10–26%) compared to MySQL 8.4 and 9.7 (CV 3–11%). This suggests more
  aggressive checkpoint flushing and dirty-page eviction under memory pressure in MariaDB, which
  creates periodic throughput dips. Once the buffer pool comfortably exceeds the working set
  (70–110G), all four engines converge to similar jitter levels (CV 3–7%), confirming
  that the instability is I/O-driven rather than an inherent engine limitation. MySQL 8.4 stands
  out as the most consistently stable across all buffer pool sizes.
</p>
<table>{jitter_table_html(BP_SIZES, lambda bp: f"{bp}G", lambda bp, eid: get_bp_record(eid, bp))}</table>

<h3 style="margin-top:28px;">Virtual Users Iterations</h3>
<img width="899" src="data:image/png;base64,{img_jitter_vu}" alt="VU jitter">
<div class="chart-caption">Figure 7 — Normalized TPM jitter per VU count. 100% = each engine’s own mean at that VU level.</div>
<p>
  This chart uses <strong>normalized jitter</strong>: each engine’s per-second TPM values
  are divided by that engine’s mean at the given VU count and expressed as a percentage, so
  100% represents the average. This removes absolute throughput differences between engines and
  isolates how <em>consistently</em> each one sustains its own average. A narrow box around 100%
  means stable throughput; a wide box means the engine oscillates significantly above and below
  its mean.
</p>
<p>
  With the buffer pool oversized (110 GiB, ~10% larger than the working set), all four engines
  stay tightly clustered (CV ≤ 6%) across the entire concurrency range, and jitter actually
  <em>decreases</em> as VU rises — averaging over more concurrent transactions smooths out
  second-to-second variance. MariaDB 12.3.1 and MySQL 9.7 lead both peak TPM and low jitter at
  160–320 VU, while MySQL 8.4 trades some throughput for the flattest profile. For
  latency-sensitive applications, the takeaway is that when memory is sized generously, all four
  engines deliver predictable response times even under heavy parallelism.
</p>
<table>{jitter_table_html(VU_STEPS, lambda vu: f"{vu} VU", lambda vu, eid: get_vu_record(eid, vu))}</table>

<h2>Database Configuration</h2>
<p>
  All engines used the same base <code>my.cnf</code> layout. The only parameter that varies across
  runs is <code>innodb_buffer_pool_size</code>.
</p>
<p>
  <strong>Buffer pool instances.</strong> <code>innodb_buffer_pool_instances</code> is set to 2 for
  MySQL runs so that the 110 GiB pool is served by a fixed, predictable number of instances
  (also used as the apples-to-apples filter when selecting MySQL runs for this report).
  MariaDB uses a single buffer pool instance regardless of pool size.
</p>
<p>
  <strong>I/O and storage.</strong> <code>innodb_io_capacity</code> is set to 10,000 to fully
  utilise NVMe storage and avoid I/O throttling during background flushing. Direct I/O bypasses
  the operating system page cache, allowing each engine to manage its own memory through the
  buffer pool without double-caching.
</p>
<p>
  <strong>Redo log.</strong> The InnoDB redo log is set to 32 GiB — deliberately oversized
  so that log capacity is never a bottleneck and no engine is limited by checkpoint pressure.
</p>
<p>
  <strong>Durability.</strong> Binary logging is configured for full safety:
  <code>sync_binlog = 1</code> fsyncs every transaction to the binlog before commit, and
  <code>innodb_flush_log_at_trx_commit = 1</code> flushes the redo log on each commit —
  the most durable setting at the cost of some throughput.
</p>
<p>
  <span style="color:#f97316;font-weight:600;">MariaDB-only</span> parameters are highlighted.
  Parameters that differ between engines are marked
  <span style="color:#a78bfa;font-weight:600;">purple</span>.
</p>
<table>
  <thead><tr><th>Parameter</th>{engine_th()}</tr></thead>
  <tbody>{cfg_table_html()}</tbody>
</table>

<h2>Methodology</h2>
<p><strong>Benchmark:</strong> TPROC-C via HammerDB 5.0 (<code>hammerdb_run.tcl</code>).</p>
<p><strong>Workload:</strong> 1000 warehouses (~100 GB data), 600 s ramp-up, 3600 s measurement window, partitioned InnoDB.</p>
<p><strong>Driver:</strong> timed, <code>tc_refresh_seconds=1</code>.</p>
<p><strong>Hardware:</strong> beast-node2.tp.int.percona.com — 80 logical CPUs, 187.54 GiB RAM, NVMe.</p>
<p><strong>OS:</strong> Ubuntu 24.04, kernel 6.8.0-60-generic; governor=performance, THP=off, swappiness=1, CPU idle POLL+C1 only.</p>
<p><strong>Engines:</strong> MariaDB 12.2.2, MariaDB 12.3.1-rc, MySQL 8.4.8, MySQL 9.7.0.</p>
<p><strong>Metric:</strong> TPM — per-second samples from HammerDB's <code>hdbtcount_*.log</code>; steady-state mean confirmed against the <code>TEST RESULT</code> line in <code>hammerdbcli.out</code>.</p>
<p><strong>BP iterations:</strong> 80 VU, buffer pool ∈ {{10, 30, 50, 70, 90, 110}} GiB.</p>
<p><strong>VU iterations:</strong> 110 GiB buffer pool, VU ∈ {{10, 20, 40, 80, 160, 320}}.</p>
<p><strong>Tail trim:</strong> trailing ramp-down samples below 10% of the steady-state median are dropped so HammerDB's graceful shutdown doesn't skew the mean or the timeseries chart.</p>
<p><strong>MySQL filter:</strong> only runs with <code>innodb_buffer_pool_instances = 2</code> are included.</p>

<footer>
  Data source:
  <a href="https://github.com/Percona-Lab-results/mysql-tpcc-buffer-pool-sweep">Percona-Lab-results/mysql-tpcc-buffer-pool-sweep</a>
  · Report generated {datetime.now().strftime("%Y-%m-%d %H:%M")}
</footer>

</body>
</html>
"""

out = ROOT / "report_gdoc.html"
out.write_text(HTML, encoding="utf-8")
print(f"Wrote {out}  ({len(HTML):,} chars)")
