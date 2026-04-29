#!/bin/bash
# Sweep innodb_buffer_pool_size across MySQL and run a timed HammerDB TPC-C
# workload at each size. Before each iteration the data directory is restored
# from a clean snapshot so cache state is identical across runs.
#
# Profile selection via MYSQL_PROFILE=8.4 (default) or 9.7 — picks the right
# backup/datadir, container name, cnf file, image tag, cnf mount path, and
# data-dir uid. Individual variables can still be overridden.
#
# Examples:
#   ./sweep_buffer_pool.sh                      # MySQL 8.4 sweep
#   MYSQL_PROFILE=9.7 ./sweep_buffer_pool.sh    # MySQL 9.7 sweep
#
# Output:
#   /root/benchmarks/results/<timestamp>-mysql<profile>/bp-<size>GiB/
#       hammerdb_run.out         summary + HammerDB result
#       hammerdb.log             full HammerDB log for the run
#       mysql_status_start.txt   SHOW GLOBAL STATUS + VARIABLES before run
#       mysql_status_end.txt     ... after run
#       turbostat.tsv            per-second CPU telemetry
#       tpm_1sec.csv             per-second transaction counter

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HAMMERDB_DIR="${HAMMERDB_DIR:-/opt/HammerDB-5.0}"

# Profile selection. Valid: 8.4 | 9.7 | maria-11 | maria-12 | maria-12.3
# Sets defaults for every other variable but each one remains overrideable.
MYSQL_PROFILE="${MYSQL_PROFILE:-8.4}"
# DB_ENGINE is used downstream to pick the right HammerDB driver (mysql vs maria).
case "$MYSQL_PROFILE" in
    8.4)
        DB_ENGINE=mysql
        : "${BACKUP_DIR:=/backup/mysql-8.4}"
        : "${DATA_DIR:=/data/mysql-8.4}"
        : "${CONTAINER:=mysql}"
        : "${CNF:=$SCRIPT_DIR/mysql.cnf}"
        : "${MYSQL_IMAGE:=mysql:8.4.8}"
        # Cnf path used by the MySQL 8.x entrypoint's include directive.
        : "${CNF_MOUNT:=/etc/mysql/conf.d/mysql.cnf}"
        # Oracle 8.x image runs mysqld as uid 999.
        : "${DATA_UID:=999}"
        ;;
    9.7)
        DB_ENGINE=mysql
        : "${BACKUP_DIR:=/backup/mysql-9.7}"
        : "${DATA_DIR:=/data/mysql-9.7}"
        : "${CONTAINER:=mysql97}"
        : "${CNF:=$SCRIPT_DIR/mysql97.cnf}"
        : "${MYSQL_IMAGE:=mysql:9.7.0-lts}"
        # 9.x image reads /etc/my.cnf directly (no conf.d include).
        : "${CNF_MOUNT:=/etc/my.cnf}"
        # Oracle 9.x image runs mysqld as uid 27.
        : "${DATA_UID:=27}"
        # 9.x removed mysql_native_password, but HammerDB can still connect
        # over plain TCP with caching_sha2_password once the server-side
        # auth cache has been primed for that user (see ensure_native_auth).
        ;;
    maria-11)
        DB_ENGINE=maria
        : "${BACKUP_DIR:=/backup/mariadb-11}"
        : "${DATA_DIR:=/data/mariadb-11}"
        : "${CONTAINER:=mariadb}"
        : "${CNF:=$SCRIPT_DIR/mariadb.cnf}"
        : "${MYSQL_IMAGE:=mariadb:11.8.6}"
        : "${CNF_MOUNT:=/etc/mysql/conf.d/mariadb.cnf}"
        # mariadb images run mysqld as uid 999 by default (Debian packaging).
        : "${DATA_UID:=999}"
        ;;
    maria-12)
        DB_ENGINE=maria
        : "${BACKUP_DIR:=/backup/mariadb-12}"
        : "${DATA_DIR:=/data/mariadb-12}"
        : "${CONTAINER:=mariadb}"
        : "${CNF:=$SCRIPT_DIR/mariadb.cnf}"
        : "${MYSQL_IMAGE:=mariadb:12.2.2}"
        : "${CNF_MOUNT:=/etc/mysql/conf.d/mariadb.cnf}"
        : "${DATA_UID:=999}"
        ;;
    maria-12.3)
        DB_ENGINE=maria
        : "${BACKUP_DIR:=/backup/mariadb-12.3}"
        : "${DATA_DIR:=/data/mariadb-12.3}"
        : "${CONTAINER:=mariadb}"
        : "${CNF:=$SCRIPT_DIR/mariadb.cnf}"
        : "${MYSQL_IMAGE:=mariadb:12.3.1-rc}"
        : "${CNF_MOUNT:=/etc/mysql/conf.d/mariadb.cnf}"
        : "${DATA_UID:=999}"
        ;;
    *)
        echo "Unknown MYSQL_PROFILE='$MYSQL_PROFILE' (expected 8.4|9.7|maria-11|maria-12|maria-12.3)" >&2
        exit 1
        ;;
esac

# Default sweep sizes. Override via SWEEP_SIZES_GIB="110" (space-separated)
# to run a single-size iteration — used by the VU sweep wrapper.
if [[ -n "${SWEEP_SIZES_GIB:-}" ]]; then
    # shellcheck disable=SC2206
    SIZES_GIB=($SWEEP_SIZES_GIB)
else
    SIZES_GIB=(10 30 50 70 90 110)
fi

RAMPUP_MIN="${RAMPUP_MIN:-10}"
DURATION_MIN="${DURATION_MIN:-60}"
TC_REFRESH_SEC="${TC_REFRESH_SEC:-1}"
NUM_VU="${NUM_VU:-80}"

TS=$(date +%Y%m%d-%H%M%S)
# Results path gets a db-engine prefix (mysql or mariadb) so the profile
# label (e.g. maria-11) reads as "mariadb-11" on disk.
case "$MYSQL_PROFILE" in
    maria-*) _results_prefix="mariadb-${MYSQL_PROFILE#maria-}" ;;
    *)       _results_prefix="mysql${MYSQL_PROFILE}" ;;
esac
RESULTS_ROOT="$SCRIPT_DIR/results/$TS-$_results_prefix"
mkdir -p "$RESULTS_ROOT"

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
die()  { echo "[$(date '+%H:%M:%S')] ERROR: $*" >&2; exit 1; }

[[ -x "$HAMMERDB_DIR/hammerdbcli" ]] || die "HammerDB not found at $HAMMERDB_DIR"
[[ -d "$BACKUP_DIR" ]] || die "Backup directory $BACKUP_DIR missing"
[[ -f "$CNF" ]] || die "MySQL config $CNF missing"

# Verify backup looks like a MySQL datadir to fail fast before the first wipe.
[[ -f "$BACKUP_DIR/ibdata1" || -d "$BACKUP_DIR/mysql" ]] \
    || die "$BACKUP_DIR does not look like a MySQL datadir"

stop_mysql() {
    if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
        log "Stopping container $CONTAINER"
        docker stop -t 120 "$CONTAINER" >/dev/null
    fi
    docker rm "$CONTAINER" >/dev/null 2>&1 || true
}

restore_datadir() {
    log "Restoring $DATA_DIR from $BACKUP_DIR"
    rm -rf "$DATA_DIR"
    mkdir -p "$DATA_DIR"
    # -a preserves ownership; --delete keeps target clean if it already exists.
    rsync -a --delete "$BACKUP_DIR/" "$DATA_DIR/"
    # Belt-and-braces: even with `innodb_buffer_pool_load_at_startup=OFF` in
    # the cnf, the backup may contain a stale ib_buffer_pool. Drop it so the
    # buffer pool always starts empty.
    rm -f "$DATA_DIR/ib_buffer_pool"
    # The 9.x image expects uid/gid 27; 8.x uses 999. Chown the tree so
    # mysqld in the container can read its own files regardless of what the
    # backup was captured under.
    chown -R "${DATA_UID}:${DATA_UID}" "$DATA_DIR"
    # For profiles that need TLS (currently 9.7), make sure the host-side
    # capath has the freshly restored ca.pem — it's stable across iterations
    # but we refresh it each time so the path always reflects reality.
    if [[ "${HDB_SSL:-false}" == "true" && -n "${HDB_SSL_CAPATH:-}" && -f "$DATA_DIR/ca.pem" ]]; then
        mkdir -p "$HDB_SSL_CAPATH"
        cp -f "$DATA_DIR/ca.pem" "$HDB_SSL_CAPATH/ca.pem"
        chmod 644 "$HDB_SSL_CAPATH/ca.pem"
    fi
    drop_os_cache
}

drop_os_cache() {
    # Each iteration must start with a cold OS page cache — otherwise the
    # previous run's pages (or rsync read-ahead from the backup) bias the
    # next measurement. sync flushes dirty pages first; 3 = pagecache +
    # dentries + inodes.
    log "Dropping OS page cache"
    sync
    if [[ -w /proc/sys/vm/drop_caches ]]; then
        echo 3 > /proc/sys/vm/drop_caches
    elif command -v sudo >/dev/null 2>&1; then
        sudo sh -c 'sync && echo 3 > /proc/sys/vm/drop_caches'
    else
        die "Cannot write /proc/sys/vm/drop_caches — run as root or with sudo"
    fi
}

write_cnf_with_bp() {
    local size_gib="$1"
    local out="$2"
    # Default: fixed 2 buffer-pool instances across all iterations — matches
    # the best-performing configuration from the prior sweep comparison.
    # Explicit overrides:
    #   BP_INSTANCES=<n>           fixed instance count across all iterations
    #   BP_GIB_PER_INSTANCE=<n>    dynamic, ceiling of size/ratio (pre-empts
    #                              the default only when BP_INSTANCES is unset)
    local instances
    if [[ -n "${BP_INSTANCES:-}" ]]; then
        instances="$BP_INSTANCES"
    elif [[ -n "${BP_GIB_PER_INSTANCE:-}" ]]; then
        local per="$BP_GIB_PER_INSTANCE"
        # ceiling division so a 10 GiB pool with per=5 gets 2 instances, not 1
        instances=$(( (size_gib + per - 1) / per ))
    else
        instances=2
    fi
    (( instances < 1 )) && instances=1
    awk -v bp="${size_gib}G" -v inst="$instances" '
        /^innodb_buffer_pool_size/ {
            printf "innodb_buffer_pool_size         = %s\n", bp
            next
        }
        /^innodb_buffer_pool_instances/ {
            printf "innodb_buffer_pool_instances    = %s\n", inst
            next
        }
        { print }
    ' "$CNF" > "$out"
}

db_client() {
    # Run the right client binary inside $CONTAINER.
    # MariaDB 12+ removed `mysql`; `mariadb` works on 11 and 12.
    # MySQL images don't have `mariadb`; stick to `mysql` there.
    local bin
    if [[ "$DB_ENGINE" == "maria" ]]; then bin=mariadb; else bin=mysql; fi
    docker exec "$CONTAINER" "$bin" -uroot -prootpassword "$@"
}

db_client_in() {
    # Same, for commands that need stdin (heredocs).
    local bin
    if [[ "$DB_ENGINE" == "maria" ]]; then bin=mariadb; else bin=mysql; fi
    docker exec -i "$CONTAINER" "$bin" -uroot -prootpassword "$@"
}

start_mysql() {
    local cnf_path="$1"
    log "Starting MySQL container with $(grep -E '^innodb_buffer_pool_(size|instances)' "$cnf_path" | tr -s ' ' | paste -sd '; ')"
    docker run -d \
        --name "$CONTAINER" \
        --restart no \
        -e MYSQL_ROOT_PASSWORD=rootpassword \
        -v "$DATA_DIR":/var/lib/mysql \
        -v "$cnf_path":"$CNF_MOUNT":ro \
        --network host \
        "$MYSQL_IMAGE" >/dev/null

    log "Waiting for MySQL to accept connections"
    local status
    for _ in {1..120}; do
        status=$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo missing)
        # Fail fast if the container exited — usually a bad cnf option. Keep
        # restart disabled so we see the real exit status and logs.
        if [[ "$status" == "exited" || "$status" == "dead" ]]; then
            log "Container exited unexpectedly. Last 30 log lines:"
            docker logs --tail 30 "$CONTAINER" 2>&1 | sed 's/^/  /' >&2
            die "MySQL container exited during startup (status=$status)"
        fi
        # Ping probe: MariaDB 12+ removed `mysqladmin` and only ships
        # `mariadb-admin`; older images (mysql 8.4/9.7, mariadb 11) only ship
        # `mysqladmin`. Try whichever fits the engine, fully silencing the
        # OCI "executable not found" errors that leak to stderr.
        local ping_bin="mysqladmin"
        [[ "$DB_ENGINE" == "maria" ]] && ping_bin="mariadb-admin"
        if docker exec "$CONTAINER" "$ping_bin" ping \
                -uroot -prootpassword --silent >/dev/null 2>&1; then
            log "MySQL ready"
            return 0
        fi
        sleep 2
    done
    die "MySQL did not become ready in time"
}

ensure_native_auth() {
    # MariaDB uses mysql_native_password by default; nothing to do.
    if [[ "$DB_ENGINE" == "maria" ]]; then
        return 0
    fi
    # 9.x: the caching_sha2_password auth cache is empty after every server
    # restart, so the first TCP connection for a given user requires a
    # secure channel (TLS) to send the password. Prime the cache by doing
    # one TCP+TLS login against the server's auto-generated ca.pem. After
    # that, subsequent plain-TCP connections from HammerDB take the
    # fast-path nonce exchange (no TLS, no password on the wire).
    # NOTE: a socket-based login inside the container (docker exec mysql ...)
    # does NOT prime the cache — socket auth bypasses caching_sha2_password's
    # scramble step entirely. Must be TCP+TLS from the host.
    # NOTE: do not FLUSH PRIVILEGES here — it would clear the cache we just
    # populated.
    if [[ "$MYSQL_PROFILE" == "9.7" ]]; then
        local ca="$DATA_DIR/ca.pem"
        [[ -f "$ca" ]] || die "ca.pem not found at $ca — cannot prime caching_sha2 cache"
        mysql -h 127.0.0.1 -P 3306 -uroot -prootpassword \
            --ssl-mode=VERIFY_CA --ssl-ca="$ca" \
            -e "SELECT 1;" >/dev/null \
            || die "failed to prime caching_sha2_password cache over TLS"
        return 0
    fi
    # 8.x: backup may have been taken when users were caching_sha2_password;
    # convert them so HammerDB (no TLS) can connect.
    db_client_in <<'SQL' >/dev/null
ALTER USER 'root'@'%'         IDENTIFIED WITH mysql_native_password BY 'rootpassword';
ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY 'rootpassword';
FLUSH PRIVILEGES;
SQL
}

verify_server_running() {
    # Cheap liveness check: the container is still running *and* MySQL
    # responds to SELECT 1. Replaces the earlier innodb_numa_interleave
    # check — the official 8.4 build is compiled without NUMA support and
    # refuses to start if that variable is set.
    local status
    status=$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo missing)
    [[ "$status" == "running" ]] || die "Container $CONTAINER is '$status', expected running"
    db_client -N -B -e 'SELECT 1;' >/dev/null 2>&1 \
        || die "MySQL in $CONTAINER is not answering queries"
    log "MySQL is running and responsive"
}

dump_status() {
    # Keeps the narrow start/end snapshot callers already use. Separate from
    # the full variables dump in dump_variables() below.
    local path="$1"
    db_client -e "
        SHOW GLOBAL VARIABLES LIKE 'innodb_buffer_pool_%';
        SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_%';
        SHOW GLOBAL STATUS LIKE 'Com_commit';
        SHOW GLOBAL STATUS LIKE 'Com_rollback';
        SHOW GLOBAL STATUS LIKE 'Questions';
        SHOW GLOBAL STATUS LIKE 'Uptime';
    " 2>/dev/null > "$path" || true
}

dump_all_status() {
    # Full SHOW GLOBAL STATUS for before/after delta analysis.
    local path="$1"
    db_client -e "SHOW GLOBAL STATUS;" 2>/dev/null > "$path" || true
}

dump_all_variables() {
    # Full SHOW GLOBAL VARIABLES — captures every effective server setting
    # including ones not explicitly set in the cnf.
    local path="$1"
    db_client -e "SHOW GLOBAL VARIABLES;" 2>/dev/null > "$path" || true
}

dump_innodb_status() {
    local path="$1"
    db_client -e "SHOW ENGINE INNODB STATUS\G" 2>/dev/null > "$path" || true
}

dump_system_info() {
    local path="$1"
    # Every collector below is wrapped in `|| true` / `: $?` because under
    # set -euo pipefail any failing probe (e.g. findmnt on a non-mountpoint,
    # missing sysctl key) would abort the whole sweep.
    {
        echo "=== uname ==="
        uname -a || true
        echo ""
        echo "=== CPU ==="
        lscpu 2>/dev/null || true
        echo ""
        echo "=== Memory ==="
        free -h || true
        echo ""
        echo "=== Disk (data + backup) ==="
        df -h "$DATA_DIR" "$BACKUP_DIR" 2>/dev/null || true
        echo ""
        echo "=== Block device ==="
        local dev=""
        # findmnt needs a real mountpoint; fall back through ancestors.
        local candidate="$DATA_DIR"
        while [[ "$candidate" != "/" && -z "$dev" ]]; do
            dev=$(findmnt -n -o SOURCE "$candidate" 2>/dev/null || true)
            candidate=$(dirname "$candidate")
        done
        if [[ -n "$dev" ]]; then
            echo "datadir backed by: $dev"
            lsblk -o NAME,SIZE,TYPE,ROTA,MODEL "$dev" 2>/dev/null || true
        fi
        echo ""
        echo "=== Kernel / VM tunables ==="
        sysctl vm.swappiness vm.dirty_ratio vm.dirty_background_ratio \
               vm.dirty_bytes vm.dirty_background_bytes \
               kernel.numa_balancing 2>/dev/null || true
        echo ""
        echo "=== THP ==="
        cat /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || true
        cat /sys/kernel/mm/transparent_hugepage/defrag 2>/dev/null || true
        echo ""
        echo "=== NUMA ==="
        numactl --hardware 2>/dev/null | head -20 || true
    } > "$path" || true
}

_collect_mysql_qps() {
    # 1-second delta sampler for QPS/TPS and InnoDB background counters.
    # Matches the schema used by /root/bench so downstream tools can consume
    # either interchangeably.
    local outfile="$1"
    local prev_q="" prev_cc="" prev_cr="" prev_flushed="" prev_purge=""
    echo "timestamp,questions,qps,com_commit,com_rollback,tps,threads_running,threads_connected,pages_flushed,pages_flushed_ps,purge_trx_id,purge_tps,history_list_length" > "$outfile"
    while true; do
        local stats
        stats=$(db_client -N \
            -e "SHOW GLOBAL STATUS WHERE Variable_name IN ('Questions','Com_commit','Com_rollback','Threads_running','Threads_connected','Innodb_buffer_pool_pages_flushed','Innodb_purge_trx_id','Innodb_history_list_length');" 2>/dev/null) || { sleep 1; continue; }
        local q cc cr tr tc flushed purge hll
        q=$(echo "$stats"       | awk '/^Questions/ {print $2}')
        cc=$(echo "$stats"      | awk '/^Com_commit/ {print $2}')
        cr=$(echo "$stats"      | awk '/^Com_rollback/ {print $2}')
        tr=$(echo "$stats"      | awk '/^Threads_running/ {print $2}')
        tc=$(echo "$stats"      | awk '/^Threads_connected/ {print $2}')
        flushed=$(echo "$stats" | awk '/^Innodb_buffer_pool_pages_flushed/ {print $2}')
        purge=$(echo "$stats"   | awk '/^Innodb_purge_trx_id/ {print $2}')
        hll=$(echo "$stats"     | awk '/^Innodb_history_list_length/ {print $2}')
        flushed=${flushed:-0}; purge=${purge:-0}; hll=${hll:-0}
        local ts qps tps flushed_ps purge_ps
        ts=$(date '+%Y-%m-%d %H:%M:%S')
        qps=0; tps=0; flushed_ps=0; purge_ps=0
        if [[ -n "$prev_q" ]]; then
            qps=$(( q - prev_q ))
            tps=$(( (cc - prev_cc) + (cr - prev_cr) ))
            flushed_ps=$(( flushed - prev_flushed ))
            [[ "$prev_purge" -gt 0 && "$purge" -gt 0 ]] && purge_ps=$(( purge - prev_purge ))
        fi
        echo "${ts},${q},${qps},${cc},${cr},${tps},${tr},${tc},${flushed},${flushed_ps},${purge},${purge_ps},${hll}" >> "$outfile"
        prev_q=$q; prev_cc=$cc; prev_cr=$cr; prev_flushed=$flushed; prev_purge=$purge
        sleep 1
    done
}

start_os_collectors() {
    # $1 = iter_dir; sets iter-scoped COLLECTOR_PIDS array the caller drains.
    local iter_dir="$1"
    # Generous cap: rampup + duration + 10 min slack, in seconds.
    local total_secs=$(( (RAMPUP_MIN + DURATION_MIN) * 60 + 600 ))
    COLLECTOR_PIDS=()
    if command -v vmstat >/dev/null; then
        vmstat 1 "$total_secs" > "$iter_dir/vmstat.log" 2>&1 &
        COLLECTOR_PIDS+=($!)
    fi
    if command -v iostat >/dev/null; then
        iostat -xdm 1 "$total_secs" > "$iter_dir/iostat.log" 2>&1 &
        COLLECTOR_PIDS+=($!)
    fi
    if command -v mpstat >/dev/null; then
        mpstat -P ALL 1 "$total_secs" > "$iter_dir/mpstat.log" 2>&1 &
        COLLECTOR_PIDS+=($!)
    fi
    _collect_mysql_qps "$iter_dir/qps.csv" &
    COLLECTOR_PIDS+=($!)
    log "Started OS/MySQL collectors (${#COLLECTOR_PIDS[@]} pids, max ${total_secs}s)"
}

stop_os_collectors() {
    local pid
    for pid in "${COLLECTOR_PIDS[@]:-}"; do
        [[ -n "$pid" ]] || continue
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    done
    COLLECTOR_PIDS=()
}

run_hammerdb() {
    local outfile="$1"
    local tcl
    case "$DB_ENGINE" in
        mysql) tcl="$SCRIPT_DIR/hammerdb_run.tcl" ;;
        maria) tcl="$SCRIPT_DIR/hammerdb_run_maria.tcl" ;;
        *) die "unknown DB_ENGINE='$DB_ENGINE'" ;;
    esac
    log "Running HammerDB (engine=$DB_ENGINE rampup=${RAMPUP_MIN}m duration=${DURATION_MIN}m num_vu=${NUM_VU})"
    cd "$HAMMERDB_DIR"
    HDB_NUM_VU="$NUM_VU" \
    HDB_RAMPUP="$RAMPUP_MIN" \
    HDB_DURATION="$DURATION_MIN" \
    HDB_TC_RATE="$TC_REFRESH_SEC" \
    HDB_OUTFILE="$outfile" \
    HDB_NO_STORED_PROCS="${HDB_NO_STORED_PROCS:-false}" \
        ./hammerdbcli auto "$tcl" 2>&1
    cd - >/dev/null
}

hammerdb_version() {
    # Parse the CLI banner ("HammerDB CLI v4.12"). hammerdbcli exits non-zero
    # on EOF, so swallow the status.
    ( "$HAMMERDB_DIR/hammerdbcli" </dev/null 2>&1 || true ) \
        | awk '/HammerDB CLI/ {sub(/^v/, "", $3); print $3; exit}'
}

mysql_version_full() {
    db_client -N -B -e "SELECT VERSION();" 2>/dev/null || true
}

write_manifest() {
    # $1 = iter_dir, $2 = size_gib, $3 = instances
    local iter_dir="$1" size_gib="$2" instances="$3"
    local manifest="$iter_dir/run.json"
    # Command substitutions below must not kill the script under pipefail/set -e.
    # Each is guarded with `|| true`; empty results fall back to "unknown" at
    # JSON render time.
    local mysql_ver hdb_ver kernel_ver host_ram_kb host_ram_gib cpu_governor
    mysql_ver=$(mysql_version_full || true)
    hdb_ver=$(hammerdb_version || true)
    kernel_ver=$(uname -r)
    host_ram_kb=$(awk '/MemTotal:/ {print $2}' /proc/meminfo || true)
    host_ram_gib=$(awk -v kb="$host_ram_kb" 'BEGIN{printf "%.2f", kb/1024/1024}' || true)
    cpu_governor=$( { cut -d' ' -f1 /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor 2>/dev/null \
        | sort -u | paste -sd ','; } || true)
    [[ -z "$cpu_governor" ]] && cpu_governor="unknown"

    local cpu_idle_enabled="unknown"
    if [[ -d /sys/devices/system/cpu/cpu0/cpuidle ]]; then
        cpu_idle_enabled=$(
            for s in /sys/devices/system/cpu/cpu0/cpuidle/state*; do
                if [[ "$(cat "$s/disable" 2>/dev/null)" == "0" ]]; then
                    cat "$s/name"
                fi
            done | paste -sd,
        ) || true
        [[ -z "$cpu_idle_enabled" ]] && cpu_idle_enabled="none"
    fi

    local swappiness thp_enabled thp_defrag
    swappiness=$(cat /proc/sys/vm/swappiness 2>/dev/null || echo unknown)
    thp_enabled=$(awk -F'[][]' '{print $2}' /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null)
    thp_defrag=$(awk -F'[][]' '{print $2}' /sys/kernel/mm/transparent_hugepage/defrag 2>/dev/null)
    [[ -z "$thp_enabled" ]] && thp_enabled="unknown"
    [[ -z "$thp_defrag" ]] && thp_defrag="unknown"

    # Warehouse count comes from the load script; parse it so the manifest
    # reflects the actual scale used.
    local warehouses
    warehouses=$(awk '/mysql_count_ware/ {print $NF; exit}' "$SCRIPT_DIR/hammerdb_load.tcl")

    cat > "$manifest" <<JSON
{
  "timestamp_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "benchmark": {
    "tool": "HammerDB",
    "tool_version": "${hdb_ver:-5.0}",
    "workload": "TPC-C",
    "driver": "timed",
    "num_virtual_users": $NUM_VU,
    "warehouses": ${warehouses:-null},
    "rampup_minutes": $RAMPUP_MIN,
    "duration_minutes": $DURATION_MIN,
    "tc_refresh_seconds": $TC_REFRESH_SEC,
    "allwarehouse": true,
    "timeprofile": false,
    "no_stored_procs": $([[ "${HDB_NO_STORED_PROCS:-false}" == "true" ]] && echo true || echo false)
  },
  "database": {
    "engine": "$([[ "$DB_ENGINE" == "maria" ]] && echo mariadb || echo mysql)",
    "profile": "$MYSQL_PROFILE",
    "image": "$MYSQL_IMAGE",
    "version": "${mysql_ver:-unknown}",
    "storage_engine": "innodb",
    "partitioned": true,
    "authentication": "$([[ "$MYSQL_PROFILE" == "9.7" ]] && echo caching_sha2_password || echo mysql_native_password)",
    "ssl": $([[ "${HDB_SSL:-false}" == "true" ]] && echo true || echo false)
  },
  "innodb": {
    "buffer_pool_size_gib": $size_gib,
    "buffer_pool_instances": $instances,
    "buffer_pool_gib_per_instance": $(awk -v s="$size_gib" -v i="$instances" 'BEGIN{printf "%.2f", s/i}')
  },
  "paths": {
    "backup_dir": "$BACKUP_DIR",
    "data_dir": "$DATA_DIR",
    "mysql_cnf": "$iter_dir/mysql.cnf",
    "hammerdb_dir": "$HAMMERDB_DIR",
    "hammerdb_load_script": "$SCRIPT_DIR/hammerdb_load.tcl",
    "hammerdb_run_script": "$SCRIPT_DIR/hammerdb_run.tcl"
  },
  "host": {
    "hostname": "$(hostname)",
    "kernel": "$kernel_ver",
    "cpu_count": $(nproc),
    "ram_gib": $host_ram_gib,
    "cpu_governor": "$cpu_governor",
    "cpu_idle_states_enabled": "$cpu_idle_enabled",
    "vm_swappiness": $swappiness,
    "transparent_hugepages_enabled": "$thp_enabled",
    "transparent_hugepages_defrag": "$thp_defrag"
  }
}
JSON
}

pin_cpu_cstate_to_c1() {
    # Disable every cpuidle state deeper than C1 so cores don't drop into
    # C1E / C6 / deeper between transactions — those exits add µs-to-ms of
    # wakeup latency and show up as tail-latency noise.
    #
    # On this host cpuidle exposes: state0=POLL, state1=C1, state2=C1E,
    # state3=C6. We leave POLL + C1 enabled, disable state2+ on every CPU.
    if [[ ! -d /sys/devices/system/cpu/cpu0/cpuidle ]]; then
        log "cpuidle not exposed — skipping C-state pinning"
        return 0
    fi
    log "Pinning CPU idle to C1 max (disabling C1E / C6 / deeper)"
    local f name
    for f in /sys/devices/system/cpu/cpu*/cpuidle/state*/disable; do
        name=$(cat "${f%/disable}/name" 2>/dev/null || echo "?")
        case "$name" in
            POLL|C1)
                echo 0 > "$f" 2>/dev/null \
                    || (command -v sudo >/dev/null 2>&1 && echo 0 | sudo tee "$f" >/dev/null) \
                    || die "Cannot write $f"
                ;;
            *)
                echo 1 > "$f" 2>/dev/null \
                    || (command -v sudo >/dev/null 2>&1 && echo 1 | sudo tee "$f" >/dev/null) \
                    || die "Cannot write $f"
                ;;
        esac
    done
    local enabled_states
    enabled_states=$(
        for s in /sys/devices/system/cpu/cpu0/cpuidle/state*; do
            if [[ "$(cat "$s/disable")" == "0" ]]; then
                cat "$s/name"
            fi
        done | paste -sd,
    ) || true
    log "Enabled idle states (cpu0): $enabled_states"
}

set_cpu_performance() {
    # Pin every online CPU to the `performance` governor so frequency scaling
    # doesn't add noise between iterations. No-op if cpufreq isn't exposed
    # (e.g. inside a VM without scaling_governor).
    local govs=(/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor)
    if [[ ! -e "${govs[0]}" ]]; then
        log "cpufreq not available — skipping governor change"
        return 0
    fi
    log "Setting all CPUs to performance governor"
    local g
    for g in "${govs[@]}"; do
        if [[ -w "$g" ]]; then
            echo performance > "$g"
        elif command -v sudo >/dev/null 2>&1; then
            echo performance | sudo tee "$g" >/dev/null
        else
            die "Cannot write $g — run as root or with sudo"
        fi
    done
    # Record what actually took effect (some drivers silently pin to a different
    # governor, e.g. intel_pstate with active mode).
    local active
    active=$(cut -d' ' -f1 "${govs[@]}" 2>/dev/null | sort -u | paste -sd ',')
    log "Active governors: ${active}"
}

write_sysfs() {
    # Write $2 to $1, falling back to sudo. die()s on failure.
    local path="$1" val="$2"
    if [[ -w "$path" ]]; then
        echo "$val" > "$path"
    elif command -v sudo >/dev/null 2>&1; then
        echo "$val" | sudo tee "$path" >/dev/null
    else
        die "Cannot write $path — run as root or with sudo"
    fi
}

set_swappiness() {
    # Large InnoDB buffer pool + O_DIRECT means the kernel should almost never
    # page anonymous memory out. 1 keeps swap as a last-resort safety net; 0
    # risks OOM-killing MySQL under brief pressure.
    local target=1
    log "Setting vm.swappiness = $target (was $(cat /proc/sys/vm/swappiness))"
    write_sysfs /proc/sys/vm/swappiness "$target"
}

disable_thp() {
    # InnoDB allocates the buffer pool in large contiguous regions up front;
    # THP khugepaged compaction against that region causes tail-latency spikes
    # with no throughput upside. Force both `enabled` and `defrag` to `never`.
    local enabled=/sys/kernel/mm/transparent_hugepage/enabled
    local defrag=/sys/kernel/mm/transparent_hugepage/defrag
    if [[ ! -f "$enabled" ]]; then
        log "THP sysfs not present — skipping"
        return 0
    fi
    log "Disabling Transparent Huge Pages (was enabled='$(cat "$enabled")', defrag='$(cat "$defrag")')"
    write_sysfs "$enabled" never
    write_sysfs "$defrag" never
    local state_enabled state_defrag
    state_enabled=$(cat "$enabled")
    state_defrag=$(cat "$defrag")
    log "THP now: enabled='$state_enabled' defrag='$state_defrag'"
    # Sanity check: the bracketed entry should be [never].
    [[ "$state_enabled" == *"[never]"* ]] \
        || die "Failed to set THP enabled=never (got '$state_enabled')"
    [[ "$state_defrag" == *"[never]"* ]] \
        || die "Failed to set THP defrag=never (got '$state_defrag')"
}

trap 'log "Interrupted"; stop_os_collectors 2>/dev/null; stop_mysql; exit 130' INT TERM

log "Sweep start — results under $RESULTS_ROOT"
log "Sizes (GiB): ${SIZES_GIB[*]}"

set_cpu_performance
pin_cpu_cstate_to_c1
set_swappiness
disable_thp

for size in "${SIZES_GIB[@]}"; do
    iter_dir="$RESULTS_ROOT/bp-${size}GiB"
    mkdir -p "$iter_dir"
    log "===== Iteration: buffer_pool=${size}GiB ($iter_dir) ====="

    stop_mysql
    restore_datadir

    cnf_path="$iter_dir/mysql.cnf"
    write_cnf_with_bp "$size" "$cnf_path"
    # Parse the instance count back out of the rendered cnf so the manifest
    # always reflects what MySQL was actually started with.
    instances=$(awk '/^innodb_buffer_pool_instances/ {print $NF; exit}' "$cnf_path")
    [[ -z "$instances" ]] && instances=1

    start_mysql "$cnf_path"
    ensure_native_auth
    verify_server_running
    write_manifest "$iter_dir" "$size" "$instances"

    # Pre-run snapshots — narrow status + full variables/status + host env
    dump_status        "$iter_dir/mysql_status_start.txt"
    dump_all_variables "$iter_dir/mysql_variables.txt"
    dump_all_status    "$iter_dir/mysql_status_before.txt"
    dump_system_info   "$iter_dir/system_info.txt"

    : > /tmp/hammerdb.log
    # Move away any pre-existing tcount logs so we can unambiguously pick up
    # only this iteration's file(s) afterwards.
    mkdir -p /tmp/hdbtcount_archive
    mv /tmp/hdbtcount_*.log /tmp/hdbtcount_archive/ 2>/dev/null || true

    # Sample package-level turbostat counters once per second for the full
    # run (rampup + measurement). --quiet drops the version header; the
    # per-package summary rows are the ones with `-	-	-` in the first
    # three columns. We keep the raw tab-separated output so a downstream
    # script can pull any column (CoreTmp, PkgTmp, Bzy_MHz, PkgWatt, …).
    tb_iters=$(( (RAMPUP_MIN + DURATION_MIN) * 60 + 120 ))
    if command -v turbostat >/dev/null 2>&1; then
        turbostat --interval 1 --num_iterations "$tb_iters" --quiet \
            --show Package,Core,CPU,Avg_MHz,Busy%,Bzy_MHz,IPC,CoreTmp,PkgTmp,PkgWatt,RAMWatt,CPU%c1,CPU%c6 \
            > "$iter_dir/turbostat.tsv" 2>"$iter_dir/turbostat.err" &
        tb_pid=$!
        log "turbostat sampling pid=$tb_pid (max ${tb_iters}s)"
    else
        log "turbostat not available — skipping CPU telemetry"
        tb_pid=""
    fi

    # OS-level (vmstat/iostat/mpstat) + MySQL QPS collectors at 1 s.
    start_os_collectors "$iter_dir"

    run_hammerdb "$iter_dir/hammerdb_run.out" | tee "$iter_dir/hammerdbcli.out"
    cp -f /tmp/hammerdb.log "$iter_dir/hammerdb.log" 2>/dev/null || true

    stop_os_collectors

    # Stop turbostat — it usually exits on its own when num_iterations is
    # reached, but the fallback kill covers an abnormal HammerDB exit.
    if [[ -n "$tb_pid" ]] && kill -0 "$tb_pid" 2>/dev/null; then
        kill -TERM "$tb_pid" 2>/dev/null || true
        wait "$tb_pid" 2>/dev/null || true
    fi

    # Preserve the per-iteration 1-second transaction-counter log. HammerDB
    # names it /tmp/hdbtcount_<jobid>.log (one per vurun since we enabled
    # `tcset unique 1`). Copy every tcount log created during this run.
    for tc in /tmp/hdbtcount_*.log; do
        [[ -f "$tc" ]] || continue
        cp -f "$tc" "$iter_dir/$(basename "$tc")"
    done

    # Extract a clean per-second TPM series from the cli output. hammerdbcli
    # prints one "<tpm> MySQL tpm" line per second (tc_refresh_rate=1). No
    # wall-clock timestamp, so we use a second-offset counter starting at 0.
    if [[ -f "$iter_dir/hammerdbcli.out" ]]; then
        awk '
            /MySQL tpm/ {
                if (n == 0) print "second,tpm"
                printf "%d,%d\n", n, $1
                n++
            }
        ' "$iter_dir/hammerdbcli.out" > "$iter_dir/tpm_1sec.csv"
    fi

    # Post-run snapshots — same set as pre-run, plus a full InnoDB status
    dump_status        "$iter_dir/mysql_status_end.txt"
    dump_all_status    "$iter_dir/mysql_status_after.txt"
    dump_innodb_status "$iter_dir/innodb_status.txt"
    log "Iteration ${size}GiB complete"
done

stop_mysql
log "Sweep done — results under $RESULTS_ROOT"
