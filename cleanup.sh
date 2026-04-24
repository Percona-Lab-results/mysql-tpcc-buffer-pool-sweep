#!/bin/bash
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[$(date '+%H:%M:%S')]${NC} $*"; }
warn() { echo -e "${YELLOW}[$(date '+%H:%M:%S')] WARN:${NC} $*"; }
die()  { echo -e "${RED}[$(date '+%H:%M:%S')] ERROR:${NC} $*" >&2; exit 1; }

usage() {
    cat <<EOF
Usage: $(basename "$0") [CONTAINER] [DATA_DIR]

Gracefully stops a MySQL/MariaDB container and removes binary logs.

  CONTAINER  Auto-detected from running docker containers if omitted.
  DATA_DIR   Auto-detected from the container's /var/lib/mysql mount.
EOF
    exit 0
}

[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && usage

# ─── Detect container ─────────────────────────────────────────────────────────
if [[ -n "${1:-}" ]]; then
    CONTAINER="$1"
else
    # Find containers (running or stopped) with mysql/mariadb image
    MATCHES=$(docker ps -a --format '{{.Names}} {{.Image}}' | grep -iE 'mysql|maria|percona|seekdb|oceanbase' || true)
    NUM=$(echo "$MATCHES" | grep -c . || true)

    if [[ "$NUM" -eq 0 ]]; then
        die "No MySQL/MariaDB containers found. Pass container name as argument."
    elif [[ "$NUM" -eq 1 ]]; then
        CONTAINER=$(echo "$MATCHES" | awk '{print $1}')
        log "Auto-detected container: ${CONTAINER}"
    else
        echo "Multiple database containers found:"
        echo "$MATCHES" | awk '{printf "  %-20s %s\n", $1, $2}'
        die "Specify which container to stop: $(basename "$0") <name>"
    fi
fi

# ─── Detect data directory ────────────────────────────────────────────────────
if [[ -n "${2:-}" ]]; then
    DATA_DIR="$2"
else
    DATA_DIR=$(docker inspect "${CONTAINER}" --format '{{range .Mounts}}{{if eq .Destination "/var/lib/mysql"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || true)
    [[ -n "$DATA_DIR" ]] || die "Could not detect data directory from container '${CONTAINER}'. Pass it as second argument."
    log "Detected data directory: ${DATA_DIR}"
fi

# ─── Graceful shutdown ────────────────────────────────────────────────────────
if docker ps --format '{{.Names}}' | grep -qx "${CONTAINER}"; then
    log "Stopping ${CONTAINER} gracefully..."
    docker stop -t 120 "${CONTAINER}"
    log "Removing container..."
    docker rm "${CONTAINER}"
else
    warn "Container '${CONTAINER}' is not running."
    docker rm "${CONTAINER}" 2>/dev/null && log "Removed stopped container." || true
fi

# ─── Remove binary logs ──────────────────────────────────────────────────────
[[ -d "$DATA_DIR" ]] || die "Data directory '${DATA_DIR}' does not exist."
log "Scanning ${DATA_DIR} for binary logs..."
count=0
bytes=0
for f in "${DATA_DIR}"/mysql-bin.*; do
    [ -e "$f" ] || continue
    size=$(stat -c%s "$f")
    bytes=$((bytes + size))
    count=$((count + 1))
    rm -f "$f"
done

if [ "$count" -gt 0 ]; then
    log "Removed ${count} binary log files ($(numfmt --to=iec "$bytes"))."
else
    log "No binary log files found."
fi
