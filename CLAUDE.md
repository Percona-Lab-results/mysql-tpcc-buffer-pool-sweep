# benchmarks/ — MySQL + HammerDB harness

## Layout

- `start_mysql.sh` / `start_mysql97.sh` — launch MySQL 8.4 / 9.7 in Docker with `--network host`, mount the datadir from `/data/mysql-8.4` (or `/data/mysql-9.7`), and convert users to `mysql_native_password` so HammerDB can connect over plain TCP.
- `cleanup.sh` — graceful container stop + binary-log purge. Auto-detects the container and data dir.
- `mysql.cnf` / `mysql97.cnf` — server configs. `mysql.cnf` sets `mysql_native_password = ON` and `authentication_policy = mysql_native_password` so new users skip `caching_sha2_password` (which requires TLS).
- `hammerdb_load.tcl` — HammerDB 5.0 TPC-C schema build (root@127.0.0.1:3306, DB `tpcc`, `mysql_socket null` to force TCP, `mysql_ssl false`).
- `hammerdb_load.sh` — wrapper that runs the load via `hammerdbcli auto`.
- `hammerdb_run.tcl` — timed TPC-C run. Reads `HDB_NUM_VU`, `HDB_RAMPUP`, `HDB_DURATION`, `HDB_TC_RATE`, `HDB_OUTFILE` from env. Uses `tcset refreshrate` for 1-sec transaction-counter sampling.
- `sweep_buffer_pool.sh` — main experiment harness. Iterates `innodb_buffer_pool_size`, restoring `/data/mysql-8.4` from `/backup/mysql-8.4` before each run.

## HammerDB connection quirks (keep in mind)

- HammerDB 5.0 forces a Unix-socket connection whenever `mysql_host` is `127.0.0.1` or `localhost` *unless* `mysql_socket` is the literal string `null`. Always `diset connection mysql_socket null` for containerised MySQL.
- MySQL 8.4's default plugin `caching_sha2_password` requires TLS. Options: (a) enable SSL in HammerDB with one-way cert verification, or (b) switch users to `mysql_native_password` after container start. We use (b).
- `mysql_ssl` persists in HammerDB's on-disk config between runs. If SSL was ever enabled, explicitly `diset connection mysql_ssl false` on subsequent runs or the driver will still try an SSL handshake.

## MySQL 8.4 config quirks

- **`innodb_numa_interleave` is NOT available in the official `mysql:8.4.8` Oracle image** — the binary is built without NUMA support, and setting the variable aborts startup with `unknown variable 'innodb_numa_interleave=ON'`. For NUMA interleaving, apply the policy at the container/kernel level (e.g. add `numactl` to the image and override the entrypoint to `numactl --interleave=all mysqld …`).
- The sweep script starts the container with `--restart no` and polls `docker inspect .State.Status`. If MySQL exits during startup (bad cnf, corrupt datadir, port clash) the script dumps the last 30 log lines and `die()`s immediately instead of spending 4 minutes waiting.

## Sweep harness (`sweep_buffer_pool.sh`)

Before the loop starts:

- Every online CPU is pinned to the `performance` governor (`/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor`).
- CPU idle is capped at C1: `POLL` and `C1` stay enabled; every deeper state (`C1E`, `C6`, …) is disabled via `/sys/devices/system/cpu/cpu*/cpuidle/state*/disable`.
- `vm.swappiness = 1` (keep swap as last resort only).
- Transparent Huge Pages disabled: both `/sys/kernel/mm/transparent_hugepage/enabled` and `.../defrag` are set to `never`, and the script `die()`s if the resulting `[never]` marker isn't present.

None of these settings are reset between iterations. Each `run.json` records the effective values under `host.cpu_governor`, `host.cpu_idle_states_enabled`, `host.vm_swappiness`, `host.transparent_hugepages_enabled`, `host.transparent_hugepages_defrag`. **These changes persist on the host until reboot or a manual revert** — to undo: `cpupower idle-set -E`, restore governor via `echo powersave > .../scaling_governor`, `sysctl vm.swappiness=60`, `echo madvise > /sys/kernel/mm/transparent_hugepage/enabled`.

Per iteration:

1. `docker stop/rm mysql`
2. `rsync -a --delete /backup/mysql-8.4/ /data/mysql-8.4/` — clean snapshot restore.
3. `sync && echo 3 > /proc/sys/vm/drop_caches` — drops OS pagecache/dentries/inodes so the rsync read-ahead and the previous run's hot pages don't warm the new iteration. Requires root (or sudo).
4. Render `iter_dir/mysql.cnf` from `mysql.cnf` with `innodb_buffer_pool_size` rewritten and `innodb_buffer_pool_instances` set to `size_gib / 5` (5 GiB per instance, floor 1).
5. `docker run` the MySQL image against that cnf + datadir.
6. `ALTER USER ... mysql_native_password` on `root@%` and `root@localhost`.
7. **Write `run.json`** (manifest — see below).
8. Snapshot `SHOW GLOBAL VARIABLES/STATUS` to `mysql_status_start.txt`.
9. Invoke `hammerdb_run.tcl` with the configured rampup/duration/VUs.
10. Copy `/tmp/hammerdb.log` and dump post-run status.

Environment overrides: `HAMMERDB_DIR`, `BACKUP_DIR`, `DATA_DIR`, `CONTAINER`, `CNF`, `MYSQL_IMAGE`, `NUM_VU`.

## Result directory manifest (`run.json`)

Every `results/<ts>/bp-<size>GiB/` directory gets a `run.json` emitted by `write_manifest` in `sweep_buffer_pool.sh`. The schema is stable — extend it by adding fields, do not rename existing ones.

```json
{
  "timestamp_utc": "2026-04-19T12:34:56Z",
  "benchmark": {
    "tool": "HammerDB",
    "tool_version": "4.12",
    "workload": "TPC-C",
    "driver": "timed",
    "num_virtual_users": 64,
    "warehouses": 1000,
    "rampup_minutes": 10,
    "duration_minutes": 60,
    "tc_refresh_seconds": 1,
    "allwarehouse": true,
    "timeprofile": false
  },
  "database": {
    "engine": "mysql",
    "image": "mysql:8.4.8",
    "version": "8.4.8",
    "storage_engine": "innodb",
    "partitioned": true,
    "authentication": "mysql_native_password",
    "ssl": false
  },
  "innodb": {
    "buffer_pool_size_gib": 30,
    "buffer_pool_instances": 6,
    "buffer_pool_gib_per_instance": 5
  },
  "paths": {
    "backup_dir": "/backup/mysql-8.4",
    "data_dir": "/data/mysql-8.4",
    "mysql_cnf": ".../mysql.cnf",
    "hammerdb_dir": "/opt/HammerDB-5.0",
    "hammerdb_load_script": ".../hammerdb_load.tcl",
    "hammerdb_run_script": ".../hammerdb_run.tcl"
  },
  "host": {
    "hostname": "...",
    "kernel": "...",
    "cpu_count": 32,
    "ram_gib": 128.00
  }
}
```

Fields chosen so a future tool can:

- group / facet result plots by InnoDB config (size, instances),
- detect apples-to-apples runs (same workload knobs + same DB version/image),
- reproduce a run from the manifest alone (paths to the exact cnf + scripts used),
- correlate with host hardware (RAM, CPUs, kernel).

If you add a new tunable to the sweep (e.g. redo-log capacity, IO threads), add a matching field under an appropriate section of `run.json` rather than creating a new ad-hoc file.

## Alongside `run.json`

- `mysql.cnf` — the exact rendered config for the run.
- `mysql_status_start.txt` / `mysql_status_end.txt` — buffer-pool + commit counters.
- `hammerdb_run.out` — jobid + HammerDB `timing` / `tcount` / `result` sections.
- `hammerdb.log` — full HammerDB log including per-VU progress and the 1-sec TX counter series.
- `hammerdbcli.out` — stdout from `hammerdbcli auto`.
