#!/bin/bash
# Sweep HammerDB virtual-user count at a fixed innodb_buffer_pool_size.
# Can walk any combination of MySQL and MariaDB profiles back-to-back.
#
# Configuration (override via env vars):
#   VU_LIST="10 20 40 80 160 320"   VUs to iterate (default: 10 20 40 80 160 320)
#   BP_SIZE_GIB=110                  fixed buffer-pool size (default: 110)
#   BP_INSTANCES=2                   fixed buffer-pool instances (default: 2,
#                                    ignored by MariaDB 11+ — single-instance
#                                    only)
#   RAMPUP_MIN=10                    HammerDB rampup minutes (default: 10)
#   DURATION_MIN=60                  HammerDB measurement minutes (default: 60)
#   PROFILES="..."                   whitespace list of profile tokens to run
#                                    Valid: 8.4 | 9.7 | maria-11 | maria-12 |
#                                    maria-12.3. Default: "8.4 9.7".
#
# Each (profile, VU) combination delegates one iteration to
# sweep_buffer_pool.sh with SIZES_GIB=(BP_SIZE_GIB) and NUM_VU=<vu>. All the
# usual per-iteration artefacts (run.json, tpm_1sec.csv, turbostat.tsv,
# vmstat/iostat/mpstat, qps.csv, etc.) land in the resulting directory.
#
# Examples:
#   ./sweep_vu.sh                                       # 8.4 + 9.7
#   PROFILES="maria-11 maria-12.3" ./sweep_vu.sh        # two MariaDB versions
#   PROFILES="8.4 9.7 maria-12.3" VU_LIST="80 160" ./sweep_vu.sh
#   PROFILES=maria-12.3 BP_SIZE_GIB=70 ./sweep_vu.sh    # single DB, smaller BP

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SWEEP="$SCRIPT_DIR/sweep_buffer_pool.sh"

VU_LIST="${VU_LIST:-10 20 40 80 160 320}"
BP_SIZE_GIB="${BP_SIZE_GIB:-110}"
BP_INSTANCES="${BP_INSTANCES:-2}"
RAMPUP_MIN="${RAMPUP_MIN:-10}"
DURATION_MIN="${DURATION_MIN:-60}"
# Accepted profile tokens: 8.4 9.7 maria-11 maria-12 maria-12.3
# PROFILES is the canonical knob (space-separated list). As a convenience,
# MYSQL_PROFILE=<single> is accepted too so the same env var works for both
# sweep_buffer_pool.sh and sweep_vu.sh.
if [[ -z "${PROFILES:-}" && -n "${MYSQL_PROFILE:-}" ]]; then
    PROFILES="$MYSQL_PROFILE"
fi
PROFILES="${PROFILES:-8.4 9.7}"

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
die()  { echo "[$(date '+%H:%M:%S')] ERROR: $*" >&2; exit 1; }

[[ -x "$SWEEP" ]] || die "sweep_buffer_pool.sh not found at $SWEEP"

# Validate every profile token before we spend any real time on the sweep.
for p in $PROFILES; do
    case "$p" in
        8.4|9.7|9.7-nopgo|maria-11|maria-12|maria-12.3) ;;
        *) die "Unknown profile '$p' — expected 8.4|9.7|9.7-nopgo|maria-11|maria-12|maria-12.3" ;;
    esac
done

# The outer sweep builds its own timestamped results dir per (profile,VU)
# invocation. Group them under a single grand-parent so the whole VU-sweep
# is trivially identifiable on disk.
TS=$(date +%Y%m%d-%H%M%S)
GRAND_DIR="$SCRIPT_DIR/results/vu-sweep-$TS"
mkdir -p "$GRAND_DIR"
log "VU sweep start — top-level dir: $GRAND_DIR"
log "  profiles:     $PROFILES"
log "  VU list:      $VU_LIST"
log "  BP:           ${BP_SIZE_GIB}GiB, instances=$BP_INSTANCES"
log "  rampup/dur:   ${RAMPUP_MIN}m / ${DURATION_MIN}m"

{
    echo "# VU sweep manifest"
    echo "timestamp_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "profiles=$PROFILES"
    echo "vu_list=$VU_LIST"
    echo "bp_size_gib=$BP_SIZE_GIB"
    echo "bp_instances=$BP_INSTANCES"
    echo "rampup_min=$RAMPUP_MIN"
    echo "duration_min=$DURATION_MIN"
} > "$GRAND_DIR/sweep_params.txt"

# Map to collect the per-iteration result directories the inner sweep creates,
# so we can symlink them into $GRAND_DIR for easy navigation.
run_one() {
    local profile="$1" vu="$2"
    # Pick a sensible prefix per profile family.
    local prefix
    case "$profile" in
        maria-*) prefix="mariadb${profile#maria-}" ;;
        *)       prefix="mysql${profile}" ;;
    esac
    local label="${prefix}-vu${vu}"
    log "===== $label ====="

    # Inject a single-size list and the VU count via env. The inner script
    # already supports NUM_VU and BP_INSTANCES; SIZES_GIB is a bash array
    # declared unconditionally in the script, so we override via a drop-in:
    # export the single-size via SWEEP_SIZES and patch with an env-driven
    # wrapper below.
    NUM_VU="$vu" \
    BP_INSTANCES="$BP_INSTANCES" \
    RAMPUP_MIN="$RAMPUP_MIN" \
    DURATION_MIN="$DURATION_MIN" \
    MYSQL_PROFILE="$profile" \
    SWEEP_SIZES_GIB="$BP_SIZE_GIB" \
        "$SWEEP" 2>&1 | tee "$GRAND_DIR/$label.log"

    # Locate the inner timestamped results dir (most recent matching prefix)
    # and symlink it for easy browsing.
    local suffix
    case "$profile" in
        maria-*) suffix="mariadb-${profile#maria-}" ;;
        *)       suffix="mysql${profile}" ;;
    esac
    local inner
    inner=$(ls -1dt "$SCRIPT_DIR/results/"*"-${suffix}" 2>/dev/null | head -1 || true)
    if [[ -n "$inner" && -d "$inner" ]]; then
        ln -sfn "$inner" "$GRAND_DIR/$label"
        log "  → $label -> $inner"
    fi
}

for profile in $PROFILES; do
    for vu in $VU_LIST; do
        run_one "$profile" "$vu"
    done
done

log "VU sweep done — results grouped under $GRAND_DIR"
