# mysql / mariadb buffer-pool sweep harness

HammerDB 5.0 TPC-C harness that sweeps `innodb_buffer_pool_size` (or virtual-user
count) across MySQL 8.4, MySQL 9.7, and MariaDB 11 / 12 / 12.3 and records
per-iteration system telemetry next to each HammerDB result.

## What's here

- `sweep_buffer_pool.sh` — main sweep. Iterates over buffer-pool sizes for one
  of the supported profiles. Before every iteration: restores the datadir from
  `/backup/...`, drops OS page cache, pins CPU governor + C-states, starts a
  fresh container, primes auth when needed, launches collectors, runs
  HammerDB, then snapshots status.
- `sweep_vu.sh` — wrapper that fixes BP size and iterates over virtual-user
  counts, optionally across multiple profiles back-to-back.
- `hammerdb_run.tcl` / `hammerdb_run_maria.tcl` — TPC-C run scripts
  (HammerDB 5.0, `timed` driver).
- `hammerdb_load.tcl` / `hammerdb_load_maria.tcl` — schema builds.
- `hammerdb_load.sh` / `hammerdb_load_maria.sh` — load wrappers.
- `cleanup.sh` — graceful container stop + binlog purge.
- `start_mysql.sh` / `start_mysql97.sh` — one-shot container launchers (used
  for ad-hoc work; sweeps do their own `docker run`).
- `mysql.cnf` / `mysql97.cnf` / `mariadb.cnf` — server configs.
- `mysql-9.7-image/` — Dockerfile + entrypoint that builds `mysql:9.7.0-lts`
  from Oracle upstream RPMs (Docker Hub's `mysql:lts` = 8.4; there is no
  official 9.7 image). RPMs themselves are `.gitignore`d — the Dockerfile
  downloads them.
- `CLAUDE.md` — harness design notes, gotchas, config decisions.

## Profiles

`MYSQL_PROFILE=` selects everything else (backup dir, datadir, image,
container name, cnf mount path, data-dir uid):

| Profile      | Engine  | Version     | Image               |
|--------------|---------|-------------|---------------------|
| `8.4`        | MySQL   | 8.4.8       | `mysql:8.4.8`       |
| `9.7`        | MySQL   | 9.7.0       | `mysql:9.7.0-lts`   |
| `maria-11`   | MariaDB | 11.8.6      | `mariadb:11.8.6`    |
| `maria-12`   | MariaDB | 12.2.2      | `mariadb:12.2.2`    |
| `maria-12.3` | MariaDB | 12.3.1-rc   | `mariadb:12.3.1-rc` |

## Quick start

```bash
# buffer-pool sweep, default sizes (10/30/50/70/90/110 GiB), 80 VU
MYSQL_PROFILE=8.4 ./sweep_buffer_pool.sh

# VU sweep at fixed BP=110 GiB across MySQL + MariaDB
PROFILES="8.4 9.7 maria-12.3" ./sweep_vu.sh

# Subset of sizes / VUs
SWEEP_SIZES_GIB="70 110" MYSQL_PROFILE=9.7 ./sweep_buffer_pool.sh
VU_LIST="80 160 320"     MYSQL_PROFILE=maria-12 ./sweep_vu.sh
```

## Per-iteration artefacts

Each `results/<ts>-<engine>-<ver>/bp-<N>GiB/` contains:

- `run.json` — manifest: version, image, VU, rampup/duration, warehouses,
  buffer-pool settings, host hardware, kernel/VM tunables.
- `mysql.cnf` — rendered server config for the iteration.
- `mysql_variables.txt` — full `SHOW GLOBAL VARIABLES`.
- `mysql_status_before.txt` / `mysql_status_after.txt` — full `SHOW GLOBAL
  STATUS` bracketing the run.
- `mysql_status_start.txt` / `mysql_status_end.txt` — narrow buffer-pool /
  commit counters pair.
- `innodb_status.txt` — `SHOW ENGINE INNODB STATUS` post-run.
- `system_info.txt` — uname / lscpu / memory / block device / kernel tunables.
- `hammerdb_run.out` — jobid + HammerDB result, timing, tcount summary.
- `tpm_1sec.csv` — 1-sec TPM samples parsed from `hammerdbcli.out`.
- `qps.csv` — 1-sec MySQL-side counters (QPS, TPS, commits, flushed pages,
  purge, history list length, threads running/connected).

Raw collector outputs (`turbostat.tsv`, `vmstat.log`, `iostat.log`,
`mpstat.log`, `hammerdb.log`, `hammerdbcli.out`, `hdbtcount_*.log`) are kept
locally but excluded from git via `.gitignore` — regenerate by rerunning.

## Host prerequisites

- Docker, `rsync`, `numactl`, `sysstat` (vmstat/iostat/mpstat), `turbostat`.
- Writable `/proc/sys/vm/drop_caches`, `/sys/.../scaling_governor`,
  `/sys/.../cpuidle/state*/disable`, `/sys/kernel/mm/transparent_hugepage/*`.
  Running as root is simplest; a `sudo` fallback is wired in.
- `/data/<engine>-<ver>/` and `/backup/<engine>-<ver>/` directories with a
  loaded TPC-C schema (run the `hammerdb_load*.sh` wrappers once per engine).
- HammerDB 5.0 at `/opt/HammerDB-5.0` (override with `HAMMERDB_DIR`).

See `CLAUDE.md` for design notes and the full list of quirks encountered
along the way.
