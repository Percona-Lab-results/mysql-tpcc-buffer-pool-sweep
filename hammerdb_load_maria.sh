#!/bin/bash
# Load TPC-C schema into MariaDB via HammerDB 5.0 and refresh its backup.
#
# Workflow per version:
#   1. start a MariaDB container for the requested version
#   2. drop any existing `tpcc` database
#   3. run hammerdb_load_maria.tcl (1000 warehouses, 64 loader VUs)
#   4. verify all 5 TPC-C stored procedures exist
#   5. stop the container cleanly
#   6. rsync the freshly-loaded datadir into /backup/mariadb-<version>/
#
# Usage:
#   ./hammerdb_load_maria.sh 11        # MariaDB 11.8.6
#   ./hammerdb_load_maria.sh 12        # MariaDB 12.2.2
#   ./hammerdb_load_maria.sh 12.3      # MariaDB 12.3.1-rc
#   ./hammerdb_load_maria.sh all       # all three in sequence
#
# Env overrides:
#   HAMMERDB_DIR  (default /opt/HammerDB-5.0)
#   SKIP_REBUILD=1  — skip load if all 5 procs already exist (idempotent)
#   NO_BACKUP=1     — skip the rsync to /backup at the end

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HAMMERDB_DIR="${HAMMERDB_DIR:-/opt/HammerDB-5.0}"
CONTAINER=mariadb
LOAD_TCL="$SCRIPT_DIR/hammerdb_load_maria.tcl"
CNF="$SCRIPT_DIR/mariadb.cnf"

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
die()  { echo "[$(date '+%H:%M:%S')] ERROR: $*" >&2; exit 1; }

[[ -x "$HAMMERDB_DIR/hammerdbcli" ]] || die "HammerDB not found at $HAMMERDB_DIR"
[[ -f "$LOAD_TCL" ]]                 || die "Missing $LOAD_TCL"
[[ -f "$CNF" ]]                      || die "Missing $CNF"

# Map version token → image tag + host data/backup paths.
profile_for() {
    case "$1" in
        11)   echo "mariadb:11.8.6    /data/mariadb-11   /backup/mariadb-11" ;;
        12)   echo "mariadb:12.2.2    /data/mariadb-12   /backup/mariadb-12" ;;
        12.3) echo "mariadb:12.3.1-rc /data/mariadb-12.3 /backup/mariadb-12.3" ;;
        *)    die "Unknown MariaDB version '$1' (expected 11|12|12.3|all)" ;;
    esac
}

stop_container() {
    if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
        log "Stopping $CONTAINER"
        docker stop -t 120 "$CONTAINER" >/dev/null
    fi
    docker rm "$CONTAINER" >/dev/null 2>&1 || true
}

start_container() {
    local image="$1" datadir="$2"
    mkdir -p "$datadir"
    # MariaDB image runs mysqld as uid 999 (Debian default).
    chown -R 999:999 "$datadir" 2>/dev/null || true
    log "Starting $CONTAINER ($image) with datadir=$datadir"
    docker run -d \
        --name "$CONTAINER" \
        --restart no \
        -e MYSQL_ROOT_PASSWORD=rootpassword \
        -e MYSQL_DATABASE=mydb \
        -e MYSQL_USER=myuser \
        -e MYSQL_PASSWORD=mypassword \
        -v "$datadir":/var/lib/mysql \
        -v "$CNF":/etc/mysql/conf.d/mariadb.cnf:ro \
        --network host \
        "$image" >/dev/null
    log "Waiting for MariaDB to accept connections"
    local status
    for _ in {1..120}; do
        status=$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo missing)
        if [[ "$status" == "exited" || "$status" == "dead" ]]; then
            log "Container exited during startup. Last 30 log lines:"
            docker logs --tail 30 "$CONTAINER" 2>&1 | sed 's/^/  /' >&2
            die "MariaDB container exited (status=$status)"
        fi
        # mariadb-admin is present on every MariaDB 11+ image; mysqladmin was
        # removed in 12.x. Redirect both stderr streams — docker's OCI runtime
        # errors print on client-side stderr too, so we need full silence.
        if docker exec "$CONTAINER" mariadb-admin ping \
                -uroot -prootpassword --silent >/dev/null 2>&1; then
            log "MariaDB ready"
            return 0
        fi
        sleep 2
    done
    die "MariaDB did not become ready in time"
}

mariadb_cli() {
    docker exec -i "$CONTAINER" mariadb -uroot -prootpassword "$@"
}

count_tpcc_procs() {
    mariadb_cli -N -B -e "SELECT COUNT(*) FROM information_schema.routines WHERE routine_schema='tpcc' AND routine_type='PROCEDURE';" 2>/dev/null \
        | tail -1
}

load_version() {
    local version="$1"
    local image datadir backupdir
    read -r image datadir backupdir < <(profile_for "$version")

    log "====== MariaDB $version ($image) ======"
    stop_container
    start_container "$image" "$datadir"

    local procs
    procs=$(count_tpcc_procs || echo 0)
    if [[ "${SKIP_REBUILD:-0}" == "1" && "$procs" -ge 5 ]]; then
        log "SKIP_REBUILD=1 and $procs TPC-C procedures already present — skipping load"
    else
        log "Dropping any existing tpcc database"
        mariadb_cli -e "DROP DATABASE IF EXISTS tpcc;" >/dev/null

        log "Running HammerDB schema build (1000 warehouses, 64 loader VUs)"
        (
            cd "$HAMMERDB_DIR"
            ./hammerdbcli auto "$LOAD_TCL"
        )

        procs=$(count_tpcc_procs || echo 0)
        [[ "$procs" -ge 5 ]] || die "Schema build finished but only $procs TPC-C procs present (expected 5)"
        log "Schema build complete — $procs procedures created"
    fi

    stop_container

    if [[ "${NO_BACKUP:-0}" == "1" ]]; then
        log "NO_BACKUP=1 — skipping rsync to $backupdir"
    else
        log "Refreshing $backupdir from $datadir"
        mkdir -p "$backupdir"
        rsync -a --delete "$datadir/" "$backupdir/"
        log "Backup refreshed (size: $(du -sh "$backupdir" | awk '{print $1}'))"
    fi
    log "MariaDB $version done"
}

[[ $# -ge 1 ]] || { echo "Usage: $(basename "$0") 11|12|12.3|all"; exit 1; }

case "$1" in
    all) for v in 11 12 12.3; do load_version "$v"; done ;;
    *)   load_version "$1" ;;
esac

log "All requested versions complete"
