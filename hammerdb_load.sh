#!/bin/bash
# Load TPC-C data into MySQL using HammerDB 5.0
set -euo pipefail

HAMMERDB_VERSION="5.0"
HAMMERDB_DIR="${HAMMERDB_DIR:-/opt/HammerDB-${HAMMERDB_VERSION}}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ ! -x "${HAMMERDB_DIR}/hammerdbcli" ]]; then
    echo "HammerDB ${HAMMERDB_VERSION} not found at ${HAMMERDB_DIR}"
    echo "Download: https://github.com/TPC-Council/HammerDB/releases/tag/v${HAMMERDB_VERSION}"
    echo "  curl -L -o /tmp/hammerdb.tar.gz https://github.com/TPC-Council/HammerDB/releases/download/v${HAMMERDB_VERSION}/HammerDB-${HAMMERDB_VERSION}-Linux.tar.gz"
    echo "  tar -xzf /tmp/hammerdb.tar.gz -C /opt/"
    exit 1
fi

cd "${HAMMERDB_DIR}"
./hammerdbcli auto "${SCRIPT_DIR}/hammerdb_load.tcl"
