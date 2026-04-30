#!/usr/bin/env python3
# Plot 1-second NOPM timelines for the SeekDB BP sweep, and overlay
# MySQL 9.7 for comparison where data exists.

import csv
import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # non-GUI backend
import matplotlib.pyplot as plt
import numpy as np

RESULTS = Path("/root/benchmarks/results")
OUT = Path("/root/benchmarks/plots")
OUT.mkdir(exist_ok=True)

SEEKDB_RUN = RESULTS / "20260426-143542-seekdb-hdb412"
MYSQL_RUN  = RESULTS / "20260421-184157-mysql9.7"
SIZES = [10, 30, 50, 70, 90, 110]

# 10-min rampup + 60-min measurement — clip to the window we care about
RAMPUP_SEC = 600
WINDOW_END = 4200


def load_seekdb_rate(run_dir, size_gib):
    """Load per-second NOPM rate from nopm_rate_1sec.csv."""
    csv_path = run_dir / f"bp-{size_gib}GiB" / "nopm_rate_1sec.csv"
    if not csv_path.exists():
        return None, None
    t, r = [], []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                sec = int(row["elapsed_sec"])
                rate = float(row["nopm_rate"])
            except (KeyError, ValueError):
                continue
            if sec <= 0:
                continue
            t.append(sec)
            r.append(rate)
    return np.array(t), np.array(r)


def load_mysql_tpm_to_nopm(run_dir, size_gib):
    """MySQL side writes 1-sec TPM; derive NOPM by multiplying with the
    run's NOPM/TPM ratio from the end-of-run TEST RESULT line."""
    cli = run_dir / f"bp-{size_gib}GiB" / "hammerdbcli.out"
    tpm_csv = run_dir / f"bp-{size_gib}GiB" / "tpm_1sec.csv"
    if not tpm_csv.exists() or not cli.exists():
        return None, None

    # ratio from TEST RESULT: "System achieved X NOPM from Y MySQL TPM"
    import re
    ratio = None
    with open(cli) as f:
        for line in f:
            m = re.search(r"System achieved (\d+) NOPM from (\d+) MySQL TPM", line)
            if m:
                nopm_total, tpm_total = int(m.group(1)), int(m.group(2))
                if tpm_total > 0:
                    ratio = nopm_total / tpm_total
                break
    if ratio is None:
        return None, None

    t, r = [], []
    with open(tpm_csv) as f:
        for row in csv.DictReader(f):
            try:
                sec = int(row["second"])
                tpm = float(row["tpm"])
            except (KeyError, ValueError):
                continue
            t.append(sec)
            r.append(tpm * ratio)  # scale to NOPM-equivalent rate
    return np.array(t), np.array(r)


def smooth(y, win=30):
    """Simple centered moving average."""
    if len(y) < win:
        return y
    k = np.ones(win) / win
    return np.convolve(y, k, mode="same")


# --- Figure 1: small multiples, SeekDB only ---
fig, axes = plt.subplots(2, 3, figsize=(15, 8), sharex=True, sharey=True)
for ax, size in zip(axes.flat, SIZES):
    t, r = load_seekdb_rate(SEEKDB_RUN, size)
    if t is None:
        ax.set_title(f"{size} GiB (no data)")
        continue
    mask = (t >= RAMPUP_SEC) & (t <= WINDOW_END)
    ax.plot(t[mask] / 60.0, r[mask], color="tab:blue", linewidth=0.4, alpha=0.3)
    ax.plot(t[mask] / 60.0, smooth(r[mask], 30), color="tab:blue", linewidth=1.5, label="30s avg")
    mean = r[mask].mean()
    ax.axhline(mean, color="red", linestyle="--", linewidth=0.8, alpha=0.7, label=f"mean {mean:,.0f}")
    ax.set_title(f"MEMORY_LIMIT = {size} GiB")
    ax.set_ylabel("NOPM (orders/min)")
    ax.set_xlabel("elapsed (min)")
    ax.legend(loc="lower right", fontsize=8)
    ax.grid(alpha=0.3)
    ax.set_ylim(0, 320_000)

fig.suptitle(
    "SeekDB v1.2.0.0 — per-second NOPM timeline by MEMORY_LIMIT\n"
    "HammerDB 4.12 TPC-C, 1000 warehouses, 80 VU, 60-min measurement",
    fontsize=11,
)
fig.tight_layout()
fig.savefig(OUT / "seekdb_nopm_by_size.png", dpi=110, bbox_inches="tight")
print(f"wrote {OUT / 'seekdb_nopm_by_size.png'}")

# --- Figure 2: overlay (all sizes on one chart, 30s smoothed) ---
fig, ax = plt.subplots(figsize=(12, 6))
colors = plt.cm.viridis(np.linspace(0, 0.9, len(SIZES)))
for size, color in zip(SIZES, colors):
    t, r = load_seekdb_rate(SEEKDB_RUN, size)
    if t is None:
        continue
    mask = (t >= RAMPUP_SEC) & (t <= WINDOW_END)
    ax.plot(t[mask] / 60.0, smooth(r[mask], 30), color=color, linewidth=1.3, label=f"{size} GiB")
ax.set_xlabel("elapsed (min)")
ax.set_ylabel("NOPM (orders/min, 30-s smoothed)")
ax.set_title(
    "SeekDB v1.2.0.0 — per-second NOPM overlay\n"
    "HammerDB 4.12 TPC-C, 1000 warehouses, 80 VU"
)
ax.legend(title="MEMORY_LIMIT", loc="lower right", fontsize=9)
ax.grid(alpha=0.3)
ax.set_ylim(0, 320_000)
fig.tight_layout()
fig.savefig(OUT / "seekdb_nopm_overlay.png", dpi=120, bbox_inches="tight")
print(f"wrote {OUT / 'seekdb_nopm_overlay.png'}")

# --- Figure 2b: raw 1-sec NOPM, one per size (no smoothing) ---
fig, axes = plt.subplots(6, 1, figsize=(14, 14), sharex=True)
for ax, size in zip(axes, SIZES):
    t, r = load_seekdb_rate(SEEKDB_RUN, size)
    if t is None:
        ax.set_title(f"{size} GiB (no data)")
        continue
    mask = (t >= RAMPUP_SEC) & (t <= WINDOW_END)
    ax.plot(t[mask] / 60.0, r[mask], color="tab:blue",
            linewidth=0.5, alpha=0.9)
    mean = r[mask].mean()
    med = np.median(r[mask])
    ax.axhline(mean, color="red", linestyle="--", linewidth=0.8, alpha=0.8,
               label=f"mean {mean:,.0f}")
    ax.axhline(med, color="darkgreen", linestyle=":", linewidth=0.8, alpha=0.8,
               label=f"median {med:,.0f}")
    ax.set_title(f"MEMORY_LIMIT = {size} GiB", fontsize=10, loc="left")
    ax.set_ylabel("NOPM")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper right", fontsize=8)
    ax.set_ylim(0, 340_000)

axes[-1].set_xlabel("elapsed (min)")
fig.suptitle(
    "SeekDB v1.2.0.0 — RAW 1-second NOPM (no smoothing)\n"
    "HammerDB 4.12 TPC-C, 1000 warehouses, 80 VU, 60-min measurement window",
    fontsize=11,
)
fig.tight_layout()
fig.savefig(OUT / "seekdb_nopm_1sec_raw.png", dpi=110, bbox_inches="tight")
print(f"wrote {OUT / 'seekdb_nopm_1sec_raw.png'}")

# --- Figure 3: SeekDB vs MySQL 9.7 at three representative sizes ---
fig, axes = plt.subplots(1, 3, figsize=(16, 5), sharey=True)
for ax, size in zip(axes, [10, 50, 110]):
    s_t, s_r = load_seekdb_rate(SEEKDB_RUN, size)
    m_t, m_r = load_mysql_tpm_to_nopm(MYSQL_RUN, size)
    if s_t is not None:
        mask = (s_t >= RAMPUP_SEC) & (s_t <= WINDOW_END)
        ax.plot(s_t[mask] / 60.0, smooth(s_r[mask], 30),
                color="tab:blue", linewidth=1.5, label="SeekDB v1.2 (30s avg)")
    if m_t is not None:
        mask = (m_t >= RAMPUP_SEC) & (m_t <= WINDOW_END)
        ax.plot(m_t[mask] / 60.0, smooth(m_r[mask], 30),
                color="tab:orange", linewidth=1.5, label="MySQL 9.7 (30s avg)")
    ax.set_title(f"BP / MEMORY_LIMIT = {size} GiB")
    ax.set_xlabel("elapsed (min)")
    ax.set_ylabel("NOPM (orders/min)" if size == 10 else "")
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(alpha=0.3)
    ax.set_ylim(0, 550_000)
fig.suptitle(
    "SeekDB v1.2.0.0 vs MySQL 9.7.0 — per-second NOPM timelines\n"
    "Same workload, same host; MySQL NOPM derived from TPM × run ratio",
    fontsize=11,
)
fig.tight_layout()
fig.savefig(OUT / "seekdb_vs_mysql_nopm.png", dpi=110, bbox_inches="tight")
print(f"wrote {OUT / 'seekdb_vs_mysql_nopm.png'}")
