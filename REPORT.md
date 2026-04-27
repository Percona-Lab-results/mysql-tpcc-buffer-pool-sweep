# Database Benchmark Comparison — TPROC-C Report

**HammerDB 5.0 | TPROC-C | 1000 warehouses | 3600 s runs | 600 s ramp-up**
**Hardware:** beast-node2.tp.int.percona.com · 80 logical CPUs · 187.54 GiB RAM · NVMe
**OS:** Ubuntu 24.04 · kernel 6.8.0-60-generic · governor=performance · THP=off · swappiness=1 · Generated: 2026-04-27
**Engines:** MariaDB 12.2.2, MariaDB 12.3.1-rc, MySQL 8.4.8, MySQL 9.7.0

---

## Executive Summary

| Config | MariaDB 12.2.2 | MariaDB 12.3.1 | MySQL 8.4.8 | MySQL 9.7.0 |
|--------|---|---|---|---|
| Peak TPM (BP iterations, 80 VU) | 1,147,448 | 1,102,582 | 1,026,225 | 1,172,021 |
| Peak TPM (VU iterations, BP 110G) | 1,147,448 | 1,212,899 | 1,097,938 | 1,252,012 |
| Scaling 10→320 VU (BP 110G) | — | 4.0× | 4.0× | 3.8× |

**Headline:** MySQL 9.7.0 posts the highest BP-iterations peak at 80 VU; MySQL 9.7.0 leads the VU-iterations peak at BP 110 GiB. TPM numbers come from HammerDB's own transaction counter (`hdbtcount_*.log`, one sample per second), and are cross-checked against the authoritative `TEST RESULT: System achieved … TPM` line (parsed from `hammerdbcli.out`) — the two agree within 1%. Per-second samples feed the jitter statistics (std, P5, P95, CV%) directly.

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

| BP Size | MariaDB 12.2.2 | MariaDB 12.3.1 | MySQL 8.4.8 | MySQL 9.7.0 |
|---------|---|---|---|---|
| 10G | 272,108 | 276,483 | **353,125** | 326,322 |
| 30G | 478,234 | 489,355 | 638,556 | **638,562** |
| 50G | 622,939 | 626,108 | 933,531 | **1,030,408** |
| 70G | 1,141,606 | 1,102,582 | 1,024,559 | **1,163,566** |
| 90G | 1,142,685 | 1,098,103 | 1,026,225 | **1,171,792** |
| 110G | 1,147,448 | 1,090,956 | 1,025,733 | **1,172,021** |

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

Concurrency was iterated through {10, 20, 40, 80, 160, 320} virtual users with
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

| VU | MariaDB 12.2.2 | MariaDB 12.3.1 | MySQL 8.4.8 | MySQL 9.7.0 |
|----|---|---|---|---|
| 10 | — | 292,497 | 276,116 | **333,019** |
| 20 | — | 518,776 | 506,079 | **603,331** |
| 40 | — | 810,835 | 800,144 | **914,072** |
| 80 | 1,147,448 | 1,107,155 | 1,023,746 | **1,163,664** |
| 160 | — | 1,212,899 | 1,092,135 | **1,226,158** |
| 320 | — | 1,166,107 | 1,097,938 | **1,252,012** |

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

| Config | Engine | Mean TPM | Std Dev | CV% | P5 | P95 | P5-P95 Range |
|--------|--------|-----------|---------|-----|-----|-----|-------------|
| 10G | MariaDB 12.2.2 | 272,235 | 27,743 | 10.2% | 230,220 | 314,520 | 84,300 |
| 10G | MariaDB 12.3.1 | 276,474 | 28,743 | 10.4% | 232,980 | 318,420 | 85,440 |
| 10G | MySQL 8.4.8 | 353,518 | 28,403 | 8.0% | 305,760 | 385,800 | 80,040 |
| 10G | MySQL 9.7.0 | 326,649 | 36,258 | 11.1% | 262,380 | 383,820 | 121,440 |
| 30G | MariaDB 12.2.2 | 478,762 | 88,404 | 18.5% | 327,600 | 616,020 | 288,420 |
| 30G | MariaDB 12.3.1 | 489,985 | 90,599 | 18.5% | 341,220 | 630,000 | 288,780 |
| 30G | MySQL 8.4.8 | 639,192 | 38,284 | 6.0% | 574,800 | 691,140 | 116,340 |
| 30G | MySQL 9.7.0 | 639,049 | 65,639 | 10.3% | 516,000 | 721,140 | 205,140 |
| 50G | MariaDB 12.2.2 | 630,021 | 170,573 | 27.1% | 375,300 | 889,020 | 513,720 |
| 50G | MariaDB 12.3.1 | 632,509 | 169,376 | 26.8% | 384,120 | 900,360 | 516,240 |
| 50G | MySQL 8.4.8 | 934,618 | 26,676 | 2.9% | 893,460 | 964,800 | 71,340 |
| 50G | MySQL 9.7.0 | 1,031,938 | 49,502 | 4.8% | 919,620 | 1,081,620 | 162,000 |
| 70G | MariaDB 12.2.2 | 1,143,318 | 39,809 | 3.5% | 1,064,760 | 1,189,560 | 124,800 |
| 70G | MariaDB 12.3.1 | 1,103,859 | 61,296 | 5.5% | 994,500 | 1,179,840 | 185,340 |
| 70G | MySQL 8.4.8 | 1,025,773 | 37,216 | 3.6% | 954,600 | 1,055,280 | 100,680 |
| 70G | MySQL 9.7.0 | 1,165,121 | 47,295 | 4.1% | 1,074,780 | 1,207,800 | 133,020 |
| 90G | MariaDB 12.2.2 | 1,144,128 | 45,806 | 4.0% | 1,059,900 | 1,198,320 | 138,420 |
| 90G | MariaDB 12.3.1 | 1,099,378 | 59,039 | 5.4% | 996,000 | 1,180,680 | 184,680 |
| 90G | MySQL 8.4.8 | 1,027,532 | 35,957 | 3.5% | 956,340 | 1,058,880 | 102,540 |
| 90G | MySQL 9.7.0 | 1,173,320 | 48,390 | 4.1% | 1,083,960 | 1,217,820 | 133,860 |
| 110G | MariaDB 12.2.2 | 1,148,907 | 42,925 | 3.7% | 1,073,040 | 1,199,520 | 126,480 |
| 110G | MariaDB 12.3.1 | 1,092,219 | 59,241 | 5.4% | 986,940 | 1,172,880 | 185,940 |
| 110G | MySQL 8.4.8 | 1,027,105 | 35,727 | 3.5% | 961,620 | 1,057,740 | 96,120 |
| 110G | MySQL 9.7.0 | 1,173,411 | 44,982 | 3.8% | 1,092,960 | 1,212,060 | 119,100 |

### Virtual Users Iterations

![TPM jitter — virtual users iterations](report_assets/fig7_jitter_vu.png)

With the buffer pool oversized (110 GiB, ~10% larger than the working set), all four engines
stay tightly clustered (CV ≤ 6%) across the entire concurrency range, and jitter actually
*decreases* as VU rises — averaging over more concurrent transactions smooths out
second-to-second variance. MariaDB 12.3.1 and MySQL 9.7 lead both peak TPM and low jitter
at 160–320 VU, while MySQL 8.4 trades some throughput for the flattest profile. For
latency-sensitive applications, the takeaway is that when memory is sized generously, all
four engines deliver predictable response times even under heavy parallelism.

| Config | Engine | Mean TPM | Std Dev | CV% | P5 | P95 | P5-P95 Range |
|--------|--------|-----------|---------|-----|-----|-----|-------------|
| 10 VU | MariaDB 12.3.1 | 292,824 | 16,691 | 5.7% | 267,300 | 311,700 | 44,400 |
| 10 VU | MySQL 8.4.8 | 276,348 | 8,857 | 3.2% | 264,360 | 287,520 | 23,160 |
| 10 VU | MySQL 9.7.0 | 333,440 | 12,863 | 3.9% | 310,560 | 350,580 | 40,020 |
| 20 VU | MariaDB 12.3.1 | 519,306 | 29,548 | 5.7% | 473,040 | 566,760 | 93,720 |
| 20 VU | MySQL 8.4.8 | 506,616 | 19,442 | 3.8% | 471,540 | 528,660 | 57,120 |
| 20 VU | MySQL 9.7.0 | 603,991 | 22,009 | 3.6% | 562,380 | 629,460 | 67,080 |
| 40 VU | MariaDB 12.3.1 | 811,552 | 48,059 | 5.9% | 737,340 | 896,700 | 159,360 |
| 40 VU | MySQL 8.4.8 | 800,838 | 26,530 | 3.3% | 756,000 | 827,580 | 71,580 |
| 40 VU | MySQL 9.7.0 | 915,110 | 37,568 | 4.1% | 848,640 | 962,040 | 113,400 |
| 80 VU | MariaDB 12.2.2 | 1,148,907 | 42,925 | 3.7% | 1,073,040 | 1,199,520 | 126,480 |
| 80 VU | MariaDB 12.3.1 | 1,108,372 | 45,859 | 4.1% | 1,034,400 | 1,172,880 | 138,480 |
| 80 VU | MySQL 8.4.8 | 1,024,983 | 37,813 | 3.7% | 953,580 | 1,055,160 | 101,580 |
| 80 VU | MySQL 9.7.0 | 1,165,157 | 46,019 | 4.0% | 1,082,400 | 1,208,280 | 125,880 |
| 160 VU | MariaDB 12.3.1 | 1,215,432 | 29,388 | 2.4% | 1,170,540 | 1,238,100 | 67,560 |
| 160 VU | MySQL 8.4.8 | 1,094,419 | 24,871 | 2.3% | 1,051,800 | 1,115,880 | 64,080 |
| 160 VU | MySQL 9.7.0 | 1,228,600 | 32,731 | 2.7% | 1,168,680 | 1,258,440 | 89,760 |
| 320 VU | MariaDB 12.3.1 | 1,172,040 | 22,682 | 1.9% | 1,144,500 | 1,196,400 | 51,900 |
| 320 VU | MySQL 8.4.8 | 1,100,789 | 24,254 | 2.2% | 1,085,280 | 1,116,540 | 31,260 |
| 320 VU | MySQL 9.7.0 | 1,255,419 | 18,369 | 1.5% | 1,231,380 | 1,276,380 | 45,000 |

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

| Parameter | MariaDB 12.2.2 | MariaDB 12.3.1 | MySQL 8.4.8 | MySQL 9.7.0 | Note |
|-----------|---|---|---|---|------|
| **InnoDB Buffer** | | | | | |
| `innodb_buffer_pool_size` | `110G` | `110G` | `110G` | `110G` |  |
| `innodb_buffer_pool_instances` | — | — | `2` | `2` |  |
| **InnoDB I/O** | | | | | |
| `innodb_io_capacity` | `10000` | `10000` | `10000` | `10000` |  |
| `innodb_io_capacity_max` | `20000` | `20000` | `20000` | `20000` |  |
| `innodb_read_io_threads` | `16` | `16` | `16` | `16` |  |
| `innodb_write_io_threads` | `16` | `16` | `16` | `16` |  |
| `innodb_use_native_aio` | `ON` | `ON` | `ON` | `ON` |  |
| `innodb_data_file_buffering` | `OFF` | `OFF` | — | — | MariaDB only |
| `innodb_data_file_write_through` | `OFF` | `OFF` | — | — | MariaDB only |
| `innodb_log_file_buffering` | `ON` | `ON` | — | — | MariaDB only |
| `innodb_log_file_write_through` | `OFF` | `OFF` | — | — | MariaDB only |
| **InnoDB Log** | | | | | |
| `innodb_log_file_size` | `32G` | `32G` | — | — |  |
| `innodb_log_buffer_size` | `256M` | `256M` | `256M` | `256M` |  |
| `innodb_flush_log_at_trx_commit` | `1` | `1` | `1` | `1` |  |
| `innodb_doublewrite` | `ON` | `ON` | `ON` | `ON` |  |
| **InnoDB OLTP** | | | | | |
| `innodb_snapshot_isolation` | `OFF` | `OFF` | — | — | MariaDB only |
| `innodb_stats_on_metadata` | `OFF` | `OFF` | `OFF` | `OFF` |  |
| `innodb_open_files` | `65536` | `65536` | `65536` | `65536` |  |
| `innodb_lock_wait_timeout` | `50` | `50` | `50` | `50` |  |
| `innodb_rollback_on_timeout` | `ON` | `ON` | `ON` | `ON` |  |
| **Binary Log** | | | | | |
| `log_bin` | `/var/lib/mysql/mysql-bin` | `/var/lib/mysql/mysql-bin` | `/var/lib/mysql/mysql-bin` | `/var/lib/mysql/mysql-bin` |  |
| `binlog_format` | `ROW` | `ROW` | `ROW` | `ROW` |  |
| `binlog_row_image` | `MINIMAL` | `MINIMAL` | `MINIMAL` | `MINIMAL` |  |
| `expire_logs_days` | `7` | `7` | — | — |  |
| `sync_binlog` | `1` | `1` | `1` | `1` |  |
| `binlog_cache_size` | `4M` | `4M` | `4M` | `4M` |  |
| `max_binlog_size` | `512M` | `512M` | `512M` | `512M` |  |
| **Other** | | | | | |
| `binlog_expire_logs_seconds` | — | — | `604800` | `604800` |  |
| `innodb_buffer_pool_dump_at_shutdown` | `OFF` | `OFF` | `OFF` | `OFF` |  |
| `innodb_buffer_pool_load_at_startup` | `OFF` | `OFF` | `OFF` | `OFF` |  |
| `innodb_flush_method` | — | — | `O_DIRECT_NO_FSYNC` | — |  |
| `innodb_redo_log_capacity` | — | — | `32G` | `32G` |  |
| `log_queries_not_using_indexes` | `OFF` | `OFF` | — | — |  |
| `long_query_time` | `1` | `1` | — | — |  |
| `min_examined_row_limit` | `1000` | `1000` | — | — |  |
| `myisam_sort_buffer_size` | `128M` | `128M` | — | — |  |
| `mysqlx` | — | — | `OFF` | `OFF` |  |
| `slow_query_log` | `ON` | `ON` | — | — |  |
| `slow_query_log_file` | `/var/lib/mysql/slow.log` | `/var/lib/mysql/slow.log` | — | — |  |

---

## Methodology

- **Benchmark:** TPROC-C via HammerDB 5.0 (`hammerdb_run.tcl`)
- **Workload:** 1000 warehouses (~100 GB), 600 s ramp-up, 3600 s measurement window, partitioned InnoDB
- **Driver:** timed, `tc_refresh_seconds=1`
- **Hardware:** beast-node2.tp.int.percona.com — 80 logical CPUs, 187.54 GiB RAM
- **OS:** Ubuntu 24.04, kernel 6.8.0-60-generic; governor=performance, THP=off, swappiness=1, CPU idle POLL+C1 only
- **Engines:** MariaDB 12.2.2, MariaDB 12.3.1-rc, MySQL 8.4.8, MySQL 9.7.0
- **Metric:** TPM — per-second samples from HammerDB's `hdbtcount_*.log` (dense, complete); end-of-run peak confirmed against the `TEST RESULT` line in `hammerdbcli.out`
- **BP iterations:** 80 VU, buffer pool ∈ {10, 30, 50, 70, 90, 110} GiB
- **VU iterations:** 110 GiB buffer pool, VU ∈ {10, 20, 40, 80, 160, 320}
- **Tail trim:** trailing ramp-down samples below 10% of median are dropped so HammerDB's graceful shutdown doesn't pollute stats or charts

---

*Data source: [Percona-Lab-results/mysql-tpcc-buffer-pool-sweep](https://github.com/Percona-Lab-results/mysql-tpcc-buffer-pool-sweep)*
