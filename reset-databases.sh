#!/usr/bin/env bash
# ============================================================================
# Reset Databases — Robot Telemetry
# FRC Team 4607
# ============================================================================
#
# Clears all data from PostgreSQL and InfluxDB so you can start fresh.
# Optionally clears the archive directories as well.
#
# Usage:
#   sudo ./reset-databases.sh              # reset databases only
#   sudo ./reset-databases.sh --archive    # also clear archive directories
#
# ============================================================================

set -euo pipefail

REPO_DIR="/home/cis/robot-telemetry"
PG_DB="stoplight"
PG_USER="postgres"
INFLUX_ORG="frc4607"
INFLUX_BUCKET="robot-telemetry"

CLEAR_ARCHIVE=false

for arg in "$@"; do
    case "$arg" in
        --archive) CLEAR_ARCHIVE=true ;;
        -h|--help)
            echo "Usage: sudo $0 [--archive]"
            echo "  --archive   Also clear archive/logs, archive/metrics, archive/robot-logs"
            exit 0
            ;;
        *)
            echo "Unknown option: $arg" >&2
            exit 1
            ;;
    esac
done

# ── Colors ─────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }

# ── Pre-flight ─────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root (sudo)." >&2
    exit 1
fi

# Load .env for InfluxDB token
if [[ -f "${REPO_DIR}/.env" ]]; then
    set -a
    source "${REPO_DIR}/.env"
    set +a
fi

INFLUX_TOKEN="${INFLUX_TOKEN:-}"
if [[ -z "$INFLUX_TOKEN" ]]; then
    warn "INFLUX_TOKEN not found in .env — InfluxDB reset will be skipped."
fi

# ── Confirmation ───────────────────────────────────────────────────────────
echo ""
echo -e "${RED}WARNING: This will permanently delete all telemetry data.${NC}"
echo ""
echo "  PostgreSQL:  DROP and recreate database '${PG_DB}'"
echo "  InfluxDB:    Delete and recreate bucket '${INFLUX_BUCKET}'"
if [[ "$CLEAR_ARCHIVE" == true ]]; then
    echo "  Archive:     Clear archive/logs, archive/metrics, archive/robot-logs"
fi
echo ""
read -rp "Type 'yes' to confirm: " CONFIRM
if [[ "$CONFIRM" != "yes" ]]; then
    echo "Aborted."
    exit 0
fi

# ── Stop services ──────────────────────────────────────────────────────────
info "Stopping services..."
timeout 10 systemctl stop robot-telemetry.service 2>/dev/null || true
timeout 10 systemctl stop upload-server.service 2>/dev/null || true
# Kill any stragglers that didn't stop in time
systemctl kill robot-telemetry.service 2>/dev/null || true
systemctl kill upload-server.service 2>/dev/null || true
sleep 1

# ── Reset PostgreSQL ───────────────────────────────────────────────────────
info "Resetting PostgreSQL database '${PG_DB}'..."
# Terminate any remaining connections to the database
sudo -u postgres psql -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${PG_DB}' AND pid <> pg_backend_pid();" 2>/dev/null || true
sudo -u postgres psql -c "DROP DATABASE IF EXISTS ${PG_DB};"
sudo -u postgres psql -c "CREATE DATABASE ${PG_DB};"
info "PostgreSQL database recreated."

# ── Run Alembic migrations ─────────────────────────────────────────────────
info "Running Alembic migrations..."
cd "${REPO_DIR}"
"${REPO_DIR}/bin/alembic" upgrade head
info "Database schema is up to date."

# ── Reset InfluxDB ─────────────────────────────────────────────────────────
if [[ -n "$INFLUX_TOKEN" ]]; then
    info "Resetting InfluxDB bucket '${INFLUX_BUCKET}'..."

    # Get the bucket ID
    BUCKET_ID=$(influx bucket list \
        --host http://localhost:8086 \
        --token "${INFLUX_TOKEN}" \
        --org "${INFLUX_ORG}" \
        --name "${INFLUX_BUCKET}" \
        --json 2>/dev/null | python3.12 -c "
import sys, json
data = json.load(sys.stdin)
if data:
    print(data[0]['id'])
" 2>/dev/null || echo "")

    if [[ -n "$BUCKET_ID" ]]; then
        # Delete and recreate the bucket
        influx bucket delete \
            --host http://localhost:8086 \
            --token "${INFLUX_TOKEN}" \
            --org "${INFLUX_ORG}" \
            --id "${BUCKET_ID}" 2>/dev/null || true

        influx bucket create \
            --host http://localhost:8086 \
            --token "${INFLUX_TOKEN}" \
            --org "${INFLUX_ORG}" \
            --name "${INFLUX_BUCKET}" 2>/dev/null

        info "InfluxDB bucket recreated."
    else
        warn "Could not find InfluxDB bucket '${INFLUX_BUCKET}' — skipping."
    fi
else
    warn "Skipped InfluxDB reset (no token)."
fi

# ── Clear archive directories ─────────────────────────────────────────────
if [[ "$CLEAR_ARCHIVE" == true ]]; then
    info "Clearing archive directories..."
    rm -rf "${REPO_DIR}/archive/logs/"*
    rm -rf "${REPO_DIR}/archive/metrics/"*
    rm -rf "${REPO_DIR}/archive/robot-logs/"*
    rm -rf "${REPO_DIR}/input-logs/"*
    info "Archive directories cleared."
fi

# ── Restart services ───────────────────────────────────────────────────────
info "Restarting services..."
systemctl start robot-telemetry.service 2>/dev/null || true
systemctl start upload-server.service 2>/dev/null || true

echo ""
info "Reset complete. All databases are empty."
