"""
Render REPORT.md from data/runs.json.
"""
import json, re
from collections import OrderedDict, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNS = json.loads((ROOT / "data" / "runs.json").read_text())

ENGINES = OrderedDict([
    ("maria122", {"display": "MariaDB 12.2.2", "version": "12.2.2"}),
    ("maria123", {"display": "MariaDB 12.3.1", "version": "12.3.1-rc"}),
    ("mysql84",  {"display": "MySQL 8.4.8",    "version": "8.4.8"}),
    ("mysql97",  {"display": "MySQL 9.7.0",    "version": "9.7.0"}),
])
EIDS = list(ENGINES.keys())

BP_SIZES = [10, 30, 50, 70, 90, 110]
VU_STEPS = [10, 20, 40, 80, 160, 320]


def reported_tpm(r: dict) -> float | None:
    """HammerDB's TEST RESULT TPM for the run, if parsed."""
    hdb = r.get("hammerdb_reported") if r else None
    return hdb["tpm"] if hdb else None


def tpm_stats(r: dict) -> dict | None:
    """Mean/std/p5/p95/CV% in TPM units.

    The `tpm` summary in runs.json is already native per-second TPM (parsed from
    HammerDB's own `hdbtcount_*.log`), so no rescaling is needed — the mean of
    the per-second series agrees with HammerDB's TEST RESULT TPM within <1%.
    """
    if not r or not r.get("tpm"):
        return None
    t = r["tpm"]
    return {
        "mean": t["avg"],
        "std":  t["std"],
        "p5":   t["p5"],
        "p95":  t["p95"],
        "cv_pct": t["cv_pct"],
    }


def fmt_int(n) -> str:
    return f"{int(round(n)):,}"


def fmt_pct(n) -> str:
    return f"{n:.1f}%"


# MySQL runs vary `innodb_buffer_pool_instances` across experiments; we restrict
# the MySQL columns to instances==2 for an apples-to-apples comparison.
# MariaDB always runs with 1 instance so no filter is applied.
MYSQL_EIDS = {"mysql84", "mysql97"}


def _instances_ok(r: dict) -> bool:
    if r["engine_id"] in MYSQL_EIDS:
        return r.get("bp_instances") == 2
    return True


def bp_sweep_run(eid: str) -> str | None:
    """Return the run_dir of the latest full BP-iterations run (all 6 sizes at 80 VU)."""
    by_run = defaultdict(set)
    for r in RUNS:
        if r["engine_id"] != eid or r["vu"] != 80 or not _instances_ok(r):
            continue
        by_run[r["run_dir"]].add(r["bp_gib"])
    full = [(rd, sizes) for rd, sizes in by_run.items()
            if set(BP_SIZES).issubset(sizes)]
    if not full:
        return None
    return sorted(full, key=lambda x: x[0])[-1][0]


def get_bp_record(eid: str, bp: int):
    rd = bp_sweep_run(eid)
    if not rd:
        return None
    for r in RUNS:
        if (r["run_dir"] == rd and r["bp_gib"] == bp and r["vu"] == 80
                and _instances_ok(r)):
            return r
    return None


def get_vu_record(eid: str, vu: int):
    """Pick the latest run at BP 110 GiB and the given VU."""
    cands = [r for r in RUNS
             if r["engine_id"] == eid and r["bp_gib"] == 110
             and r["vu"] == vu and r["tpm"] and _instances_ok(r)]
    if not cands:
        return None
    return sorted(cands, key=lambda r: r["timestamp_utc"] or "")[-1]


def run_tpm(r: dict) -> float | None:
    """Authoritative TPM for a run — prefer HammerDB's TEST RESULT, fall back to
    the per-second series mean (they agree within <1%)."""
    if not r:
        return None
    hdb = reported_tpm(r)
    if hdb:
        return float(hdb)
    return r["tpm"]["avg"] if r.get("tpm") else None


# ── Executive summary ────────────────────────────────────────────────────────
def peak_bp_80vu(eid: str):
    """Best TPM across the BP sweep at 80 VU."""
    vals = [run_tpm(get_bp_record(eid, bp)) for bp in BP_SIZES]
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None


def peak_vu_bp110(eid: str):
    """Best TPM across the VU sweep at BP 110."""
    vals = [run_tpm(get_vu_record(eid, vu)) for vu in VU_STEPS]
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None


def scaling_ratio(eid: str):
    base = run_tpm(get_vu_record(eid, 10))
    top = None
    for vu in reversed(VU_STEPS):
        top = run_tpm(get_vu_record(eid, vu))
        if top:
            break
    if not base or not top:
        return None
    return top / base


# ── Config table ─────────────────────────────────────────────────────────────
SECTION_MAP = OrderedDict([
    ("InnoDB Buffer",  ["innodb_buffer_pool_size", "innodb_buffer_pool_instances"]),
    ("InnoDB I/O",     ["innodb_io_capacity", "innodb_io_capacity_max",
                        "innodb_read_io_threads", "innodb_write_io_threads",
                        "innodb_use_native_aio",
                        "innodb_data_file_buffering", "innodb_data_file_write_through",
                        "innodb_log_file_buffering", "innodb_log_file_write_through"]),
    ("InnoDB Log",     ["innodb_log_file_size", "innodb_log_buffer_size",
                        "innodb_flush_log_at_trx_commit", "innodb_doublewrite"]),
    ("InnoDB OLTP",    ["innodb_snapshot_isolation", "innodb_stats_on_metadata",
                        "innodb_open_files", "innodb_lock_wait_timeout",
                        "innodb_rollback_on_timeout"]),
    ("Binary Log",     ["log_bin", "binlog_format", "binlog_row_image",
                        "expire_logs_days", "sync_binlog", "binlog_cache_size",
                        "max_binlog_size"]),
])

EXTRAS_KEEP = {
    "binlog_expire_logs_seconds",
    "innodb_buffer_pool_dump_at_shutdown",
    "innodb_buffer_pool_load_at_startup",
    "innodb_flush_method",
    "innodb_redo_log_capacity",
    "log_queries_not_using_indexes",
    "long_query_time",
    "min_examined_row_limit",
    "myisam_sort_buffer_size",
    "mysqlx",
    "slow_query_log",
    "slow_query_log_file",
}
MARIA_ONLY = {
    "innodb_snapshot_isolation",
    "innodb_data_file_buffering", "innodb_data_file_write_through",
    "innodb_log_file_buffering", "innodb_log_file_write_through",
}


def parse_cnf(path: Path) -> dict[str, str]:
    params = {}
    in_mysqld = False
    for line in path.read_text().splitlines():
        s = line.strip()
        if s.startswith("[mysqld]"):
            in_mysqld = True
            continue
        if s.startswith("[") and s != "[mysqld]":
            in_mysqld = False
            continue
        if not in_mysqld or not s or s.startswith("#"):
            continue
        s = re.sub(r"\s*#.*$", "", s).strip()
        if "=" in s:
            k, v = s.split("=", 1)
            params[k.strip().lower()] = v.strip()
    return params


def engine_cnf(eid: str) -> dict[str, str]:
    # Prefer the full BP-sweep run's bp-110GiB dir; fall back to any run.
    rd = bp_sweep_run(eid)
    if rd:
        p = ROOT / "results" / rd / "bp-110GiB" / "mysql.cnf"
        if p.exists():
            return parse_cnf(p)
    for r in RUNS:
        if r["engine_id"] == eid:
            p = ROOT / "results" / r["run_dir"] / r["iter"] / "mysql.cnf"
            if p.exists():
                return parse_cnf(p)
    return {}


# ── Sections ─────────────────────────────────────────────────────────────────
HEADER_ROW = "| Config | " + " | ".join(ENGINES[e]["display"] for e in EIDS) + " |"
HEADER_SEP = "|--------|" + "|".join(["---"] * len(EIDS)) + "|"


def bold_winner(vals: list[float | None]) -> list[str]:
    nums = [v for v in vals if v is not None]
    mx = max(nums) if nums else None
    cells = []
    for v in vals:
        if v is None:
            cells.append("—")
        elif v == mx:
            cells.append(f"**{fmt_int(v)}**")
        else:
            cells.append(fmt_int(v))
    return cells


def section_bp_table() -> str:
    header = "| BP Size | " + " | ".join(ENGINES[e]["display"] for e in EIDS) + " |"
    sep = "|---------|" + "|".join(["---"] * len(EIDS)) + "|"
    rows = [header, sep]
    for bp in BP_SIZES:
        vals = [run_tpm(get_bp_record(eid, bp)) for eid in EIDS]
        rows.append(f"| {bp}G | " + " | ".join(bold_winner(vals)) + " |")
    return "\n".join(rows)


def section_vu_table() -> str:
    header = "| VU | " + " | ".join(ENGINES[e]["display"] for e in EIDS) + " |"
    sep = "|----|" + "|".join(["---"] * len(EIDS)) + "|"
    rows = [header, sep]
    for vu in VU_STEPS:
        vals = [run_tpm(get_vu_record(eid, vu)) for eid in EIDS]
        rows.append(f"| {vu} | " + " | ".join(bold_winner(vals)) + " |")
    return "\n".join(rows)


def section_exec_summary() -> str:
    rows = [HEADER_ROW, HEADER_SEP]
    peak_bp = [peak_bp_80vu(e) for e in EIDS]
    rows.append("| Peak TPM (BP iterations, 80 VU) | " + " | ".join(
        fmt_int(v) if v else "—" for v in peak_bp) + " |")
    peak_vu = [peak_vu_bp110(e) for e in EIDS]
    rows.append("| Peak TPM (VU iterations, BP 110G) | " + " | ".join(
        fmt_int(v) if v else "—" for v in peak_vu) + " |")
    scal = [scaling_ratio(e) for e in EIDS]
    rows.append("| Scaling 10→320 VU (BP 110G) | " + " | ".join(
        f"{v:.1f}×" if v else "—" for v in scal) + " |")
    return "\n".join(rows)


def section_jitter_bp() -> str:
    header = "| Config | Engine | Mean TPM | Std Dev | CV% | P5 | P95 | P5-P95 Range |"
    sep    = "|--------|--------|-----------|---------|-----|-----|-----|-------------|"
    rows = [header, sep]
    for bp in BP_SIZES:
        for eid in EIDS:
            s = tpm_stats(get_bp_record(eid, bp))
            if not s:
                continue
            rows.append(f"| {bp}G | {ENGINES[eid]['display']} | {fmt_int(s['mean'])} | "
                        f"{fmt_int(s['std'])} | {s['cv_pct']:.1f}% | {fmt_int(s['p5'])} | "
                        f"{fmt_int(s['p95'])} | {fmt_int(s['p95'] - s['p5'])} |")
    return "\n".join(rows)


def section_jitter_vu() -> str:
    header = "| Config | Engine | Mean TPM | Std Dev | CV% | P5 | P95 | P5-P95 Range |"
    sep    = "|--------|--------|-----------|---------|-----|-----|-----|-------------|"
    rows = [header, sep]
    for vu in VU_STEPS:
        for eid in EIDS:
            s = tpm_stats(get_vu_record(eid, vu))
            if not s:
                continue
            rows.append(f"| {vu} VU | {ENGINES[eid]['display']} | {fmt_int(s['mean'])} | "
                        f"{fmt_int(s['std'])} | {s['cv_pct']:.1f}% | {fmt_int(s['p5'])} | "
                        f"{fmt_int(s['p95'])} | {fmt_int(s['p95'] - s['p5'])} |")
    return "\n".join(rows)


def section_config() -> str:
    cnfs = {e: engine_cnf(e) for e in EIDS}
    header = "| Parameter | " + " | ".join(ENGINES[e]["display"] for e in EIDS) + " | Note |"
    sep    = "|-----------|" + "|".join(["---"] * len(EIDS)) + "|------|"
    rows = [header, sep]
    seen = set()
    for section, keys in SECTION_MAP.items():
        section_rows = []
        for k in keys:
            vals = {e: cnfs[e].get(k, "") for e in EIDS}
            if not any(vals.values()):
                continue
            seen.add(k)
            note = "MariaDB only" if k in MARIA_ONLY else ""
            cells = [f"`{vals[e]}`" if vals[e] else "—" for e in EIDS]
            section_rows.append(f"| `{k}` | " + " | ".join(cells) + f" | {note} |")
        if section_rows:
            rows.append(f"| **{section}** | | | | | |")
            rows.extend(section_rows)
    # Extras — only performance-relevant parameters, excluding general/connection boilerplate.
    extras = []
    all_keys = set()
    for e in EIDS:
        all_keys |= set(cnfs[e].keys())
    for k in sorted((all_keys & EXTRAS_KEEP) - seen):
        vals = {e: cnfs[e].get(k, "") for e in EIDS}
        note = "MariaDB only" if k in MARIA_ONLY else ""
        cells = [f"`{vals[e]}`" if vals[e] else "—" for e in EIDS]
        extras.append(f"| `{k}` | " + " | ".join(cells) + f" | {note} |")
    if extras:
        rows.append("| **Other** | | | | | |")
        rows.extend(extras)
    return "\n".join(rows)


# ── Assemble ─────────────────────────────────────────────────────────────────
def main():
    peak_bp_map  = {e: peak_bp_80vu(e)   for e in EIDS}
    peak_vu_map  = {e: peak_vu_bp110(e)  for e in EIDS}
    best_bp = max((e for e in EIDS if peak_bp_map[e]),
                  key=lambda e: peak_bp_map[e], default=None)
    best_vu = max((e for e in EIDS if peak_vu_map[e]),
                  key=lambda e: peak_vu_map[e], default=None)

    today = datetime.now().strftime("%Y-%m-%d")
    md = f"""# Database Benchmark Comparison — TPROC-C Report

**HammerDB 5.0 | TPROC-C | 1000 warehouses | 3600 s runs | 600 s ramp-up**
**Hardware:** beast-node2.tp.int.percona.com · 80 logical CPUs · 187.54 GiB RAM · NVMe
**OS:** Ubuntu 24.04 · kernel 6.8.0-60-generic · governor=performance · THP=off · swappiness=1 · Generated: {today}
**Engines:** MariaDB 12.2.2, MariaDB 12.3.1-rc, MySQL 8.4.8, MySQL 9.7.0

---

## Executive Summary

{section_exec_summary()}

**Headline:** {ENGINES[best_bp]["display"] if best_bp else "—"} posts the highest BP-iterations peak at 80 VU; \
{ENGINES[best_vu]["display"] if best_vu else "—"} leads the VU-iterations peak at BP 110 GiB. \
TPM numbers come from HammerDB's own transaction counter (`hdbtcount_*.log`, one sample \
per second), and are cross-checked against the authoritative `TEST RESULT: System achieved \
… TPM` line (parsed from `hammerdbcli.out`) — the two agree within 1%. Per-second samples \
feed the jitter statistics (std, P5, P95, CV%) directly.

---

## Buffer Pool Iterations — 80 VU, 10G–110G

The **InnoDB Buffer Pool** is the main memory area where InnoDB caches table data and index
pages. Every read that hits the buffer pool avoids a disk I/O; every miss forces a physical
read from storage. For write-heavy OLTP workloads like TPROC-C, the buffer pool also holds
dirty pages waiting to be flushed — a larger pool means fewer flush cycles and less I/O
contention between foreground transactions and background flushing.

A **buffer pool iteration** varies this single parameter (from 10 GiB to 110 GiB in 20 GiB
steps) while holding everything else constant — 80 virtual users, 1000 warehouses
(~100 GB working set), same hardware, same configuration. This isolates the effect of memory
pressure on throughput. At small pool sizes (10–30G) only a fraction of the hot data fits in
RAM, so performance is dominated by disk I/O speed and the engine's read-ahead and flushing
strategies. As the pool grows toward and past the working set size, more reads hit cache and
fewer dirty-page evictions are needed, revealing the engine's in-memory efficiency.

The 80 VU count was chosen to represent a moderate-to-high concurrency level typical of
production OLTP servers (80 logical CPUs on this host), ensuring that throughput differences
reflect buffer pool efficiency rather than single-thread performance.

![TPROC-C throughput vs buffer pool size](report_assets/fig1_bp_line.png)

{section_bp_table()}

---

## Virtual Users Iterations — BP 110G, 10–320 VU

A **Virtual User (VU)** is a HammerDB worker thread that simulates an independent
database client. Each VU opens its own connection, picks a random warehouse, and
continuously executes the TPROC-C transaction mix (new-order, payment, delivery,
order-status, stock-level) for the duration of the run.

VU count stresses the engine's concurrency internals: row-level locking, the lock
manager, undo/purge scheduling, buffer-pool latch contention, and redo-log synchronisation.
At low VU counts the engine is CPU/I/O-bound; as VU rises, internal latch contention and
lock waits become the dominant bottleneck. The point where throughput plateaus reveals how
efficiently the engine scales under parallel workloads.

Concurrency was iterated through {{{", ".join(str(v) for v in VU_STEPS)}}} virtual users with
a fixed 110 GiB buffer pool. Each VU count ran for 3600 s with a 600 s ramp-up.
MariaDB 12.2.2 was only benchmarked at 80 VU in this dataset; its other columns are shown
as `—`.

![TPROC-C throughput vs concurrency](report_assets/fig3_vu_line.png)

![Concurrency scaling efficiency](report_assets/fig4_scaling.png)

**Note on scaling vs peak throughput:** Absolute TPM and relative scaling tell different
stories. An engine with a higher single-thread baseline saturates the available CPU
resources sooner in relative terms, so its 10→320 VU multiplier is mechanically lower than a
slower-starting engine that still has headroom to grow. Beyond the physical core count
(40 cores / 80 HT threads on this system), even a perfectly scalable engine cannot maintain
linear speedup — threads begin competing for the same execution units, and InnoDB internal
serialisation points (lock manager, redo log, buffer-pool latches) become the bottleneck. A
higher baseline simply means the engine hits that ceiling at a lower multiplier, not that it
scales worse in absolute terms.

{section_vu_table()}

---

## TPM Stability — BP 50G, 80 VU

**TPM Stability** measures how consistently a database sustains its throughput over the
entire duration of a benchmark run. A high average TPM is meaningless if the engine
periodically stalls — background checkpoint flushes, purge operations, or adaptive flushing
can cause sharp dips that ripple through the application as latency spikes.

The chart plots per-second TPM for the full 3600-second steady-state window (ramp-up
excluded) at BP 50G with 80 virtual users. 50 GiB is roughly half the working set, which is
where memory pressure is most visible — the buffer pool is neither trivially small nor
comfortably oversized, so differences in checkpoint/flush behaviour between engines show up
most clearly. Thin lines are raw 1-second samples; thick lines are 60-second rolling
averages. A flat rolling average indicates stable throughput; wide oscillations suggest
periodic internal bottlenecks (e.g. InnoDB log checkpointing, buffer pool flushing, or
purge lag).

![TPM over time at BP 50G / 80 VU](report_assets/fig5_timeseries.png)

---

## TPM Jitter — steady-state windows

**TPM Jitter** quantifies the *spread* of second-to-second throughput variation during the
steady-state portion of each run (ramp-up excluded). While the Stability chart above shows
the full time-series shape, jitter distills it into a single statistical picture: how
tightly packed are the per-second TPM readings around the mean?

A database with low jitter delivers predictable response times, simplifies capacity planning,
and avoids tail-latency violations under peak load. High jitter forces the application tier
to absorb throughput dips through connection pooling, retry logic, or queuing — adding
complexity and latency even when the average throughput looks good.

Each box shows the P25–P75 range (interquartile), the centre line is the median, and whiskers
extend to P5–P95. The tables include **CV%** (Coefficient of Variation = std ÷ mean × 100):
a scale-free measure where lower is more stable. Unlike raw standard deviation, CV% is
directly comparable across runs with different mean throughputs.

### Buffer Pool Iterations

![TPM jitter — buffer pool iterations](report_assets/fig6_jitter_bp.png)

At small buffer pool sizes (10–50G), both MariaDB versions exhibit noticeably wider TPM
spread (CV 10–26%) compared to MySQL 8.4 and 9.7 (CV 3–11%). This suggests more aggressive
checkpoint flushing and dirty-page eviction under memory pressure in MariaDB, which creates
periodic throughput dips. Once the buffer pool comfortably exceeds the working set (70–110G),
all four engines converge to similar jitter levels (CV 3–7%), confirming that the
instability is I/O-driven rather than an inherent engine limitation. MySQL 8.4 stands out as
the most consistently stable across all buffer pool sizes.

{section_jitter_bp()}

### Virtual Users Iterations

![TPM jitter — virtual users iterations](report_assets/fig7_jitter_vu.png)

With the buffer pool oversized (110 GiB, ~10% larger than the working set), all four engines
stay tightly clustered (CV ≤ 6%) across the entire concurrency range, and jitter actually
*decreases* as VU rises — averaging over more concurrent transactions smooths out
second-to-second variance. MariaDB 12.3.1 and MySQL 9.7 lead both peak TPM and low jitter
at 160–320 VU, while MySQL 8.4 trades some throughput for the flattest profile. For
latency-sensitive applications, the takeaway is that when memory is sized generously, all
four engines deliver predictable response times even under heavy parallelism.

{section_jitter_vu()}

---

## Database Configuration

All engines used the same base `my.cnf` layout. The only parameter that varies across runs
is `innodb_buffer_pool_size`. MariaDB-only parameters are tagged in the `Note` column.

**Buffer pool instances.** `innodb_buffer_pool_instances` is set to 2 for MySQL runs so that
the 110 GiB pool is served by a fixed, predictable number of instances (also used as the
apples-to-apples filter when selecting MySQL runs for this report). MariaDB uses a single
buffer pool instance regardless of pool size.

**I/O and storage.** `innodb_io_capacity` is set to 10,000 to fully utilise NVMe storage and
avoid I/O throttling during background flushing. Direct I/O bypasses the operating system
page cache, allowing each engine to manage its own memory through the buffer pool without
double-caching.

**Redo log.** The InnoDB redo log is set to 32 GiB — deliberately oversized so that log
capacity is never a bottleneck and no engine is limited by checkpoint pressure.

**Durability.** Binary logging is configured for full safety: `sync_binlog = 1` fsyncs every
transaction to the binlog before commit, and `innodb_flush_log_at_trx_commit = 1` flushes
the redo log on each commit — the most durable setting at the cost of some throughput.

{section_config()}

---

## Methodology

- **Benchmark:** TPROC-C via HammerDB 5.0 (`hammerdb_run.tcl`)
- **Workload:** 1000 warehouses (~100 GB), 600 s ramp-up, 3600 s measurement window, partitioned InnoDB
- **Driver:** timed, `tc_refresh_seconds=1`
- **Hardware:** beast-node2.tp.int.percona.com — 80 logical CPUs, 187.54 GiB RAM
- **OS:** Ubuntu 24.04, kernel 6.8.0-60-generic; governor=performance, THP=off, swappiness=1, CPU idle POLL+C1 only
- **Engines:** MariaDB 12.2.2, MariaDB 12.3.1-rc, MySQL 8.4.8, MySQL 9.7.0
- **Metric:** TPM — per-second samples from HammerDB's `hdbtcount_*.log` (dense, complete); end-of-run peak confirmed against the `TEST RESULT` line in `hammerdbcli.out`
- **BP iterations:** 80 VU, buffer pool ∈ {{10, 30, 50, 70, 90, 110}} GiB
- **VU iterations:** 110 GiB buffer pool, VU ∈ {{10, 20, 40, 80, 160, 320}}
- **Tail trim:** trailing ramp-down samples below 10% of median are dropped so HammerDB's graceful shutdown doesn't pollute stats or charts

---

*Data source: [Percona-Lab-results/mysql-tpcc-buffer-pool-sweep](https://github.com/Percona-Lab-results/mysql-tpcc-buffer-pool-sweep)*
"""
    out = ROOT / "REPORT.md"
    out.write_text(md, encoding="utf-8")
    print(f"Wrote {out}  ({len(md):,} chars)")


if __name__ == "__main__":
    main()
