#!/usr/bin/env bash
# ============================================================================
# Robot Telemetry — Fresh Ubuntu VM Setup Script
# FRC Team 4607 "Slap Shot" (2026 Season)
# ============================================================================
#
# This script installs and configures everything needed to run the Robot
# Telemetry pipeline on a fresh Ubuntu 22.04/24.04 VM:
#
#   1. System packages (Python 3.12, build tools, PostgreSQL client libs)
#   2. PostgreSQL 16 server
#   3. InfluxDB 2.x
#   4. Grafana OSS (with plugins)
#   5. Python venv & pip dependencies
#   6. .env secrets file
#   7. owlet binary setup
#   8. Directory structure
#   9. Database creation & Alembic migrations
#  10. systemd service installation
#  11. Grafana datasources & dashboard upload
#
# Usage:
#   chmod +x setup-ubuntu.sh
#   sudo ./setup-ubuntu.sh
#
# After running, the service will be enabled and started automatically.
# Grafana will be available at http://<your-ip>:3000 (default admin/admin).
# ============================================================================

set -euo pipefail

# ── Configurable variables ─────────────────────────────────────────────────
REPO_DIR="/home/cis/robot-telemetry"
REPO_URL="https://github.com/FRC4607/Robot-Telemetry.git"
REPO_BRANCH="2026-wip"

PG_DB="stoplight"
PG_USER="postgres"

INFLUX_ORG="frc4607"
INFLUX_BUCKET="robot-telemetry"
INFLUX_ADMIN_USER="admin"

# ── Load secrets from .env if it exists ────────────────────────────────────
# Passwords are read from .env so they aren't hardcoded in this script.
# If .env doesn't exist yet, the script will create it later and prompt
# for any missing values.  Check cwd first (re-runs), then REPO_DIR.
if [[ -f ".env" ]]; then
    set -a
    source ".env"
    set +a
elif [[ -f "${REPO_DIR}/.env" ]]; then
    set -a
    source "${REPO_DIR}/.env"
    set +a
fi

# Read secrets from environment variables (set by .env or user input)
PG_PASS="${DB_PASSWORD:-}"
INFLUX_ADMIN_PASS="${INFLUX_ADMIN_PASS:-}"

# Prompt for any secrets not found in .env
if [[ -z "$PG_PASS" ]]; then
    read -rsp "Enter PostgreSQL password (DB_PASSWORD): " PG_PASS
    echo
fi
if [[ -z "$INFLUX_ADMIN_PASS" ]]; then
    read -rsp "Enter InfluxDB admin password (INFLUX_ADMIN_PASS, >= 8 chars, no special chars): " INFLUX_ADMIN_PASS
    echo
fi

# ── Colors ─────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }

# ── Pre-flight checks ─────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root (sudo)." >&2
    exit 1
fi

info "Starting Robot Telemetry setup on $(lsb_release -ds 2>/dev/null || cat /etc/os-release | head -1)..."

# ============================================================================
# 1. System packages
# ============================================================================
info "Installing system packages..."
apt-get update -qq
apt-get install -y -qq \
    software-properties-common \
    curl \
    wget \
    gnupg2 \
    git \
    build-essential \
    python3.12 \
    python3.12-venv \
    python3.12-dev \
    libpq-dev \
    pkg-config \
    apt-transport-https

# ============================================================================
# 2. PostgreSQL
# ============================================================================
info "Installing PostgreSQL..."
if ! command -v psql &>/dev/null; then
    # Add the official PostgreSQL APT repo for latest version
    sh -c 'echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /etc/apt/trusted.gpg.d/postgresql.gpg
    apt-get update -qq
    apt-get install -y -qq postgresql-16
else
    info "PostgreSQL already installed, skipping."
fi

# Start and enable PostgreSQL
systemctl enable --now postgresql

# Set the postgres user password and create the database
info "Configuring PostgreSQL database '${PG_DB}'..."
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname = '${PG_DB}'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE DATABASE ${PG_DB};"
sudo -u postgres psql -c "ALTER USER ${PG_USER} WITH PASSWORD '${PG_PASS}';"

# Allow password auth for local connections (md5 instead of peer)
PG_HBA=$(sudo -u postgres psql -t -c "SHOW hba_file;" | xargs)
if grep -q "local.*all.*all.*peer" "$PG_HBA"; then
    sed -i 's/local\s\+all\s\+all\s\+peer/local   all             all                                     md5/' "$PG_HBA"
    systemctl restart postgresql
fi

info "PostgreSQL is ready."

# ============================================================================
# 3. InfluxDB 2.x
# ============================================================================
info "Installing InfluxDB 2.x..."
if ! command -v influx &>/dev/null; then
    # Ubuntu and Debian
    # Add the InfluxData GPG key and repository
    mkdir -p /etc/apt/keyrings
    curl --silent --location https://repos.influxdata.com/influxdata-archive.key \
        | gpg --dearmor \
        | tee /etc/apt/keyrings/influxdata-archive.gpg > /dev/null
    echo 'deb [signed-by=/etc/apt/keyrings/influxdata-archive.gpg] https://repos.influxdata.com/debian stable main' \
        | tee /etc/apt/sources.list.d/influxdata.list > /dev/null
    # Install influxdb
    apt-get update -qq && apt-get install -y -qq influxdb2 influxdb2-cli
else
    info "InfluxDB already installed, skipping."
fi

systemctl enable --now influxdb

# Wait for InfluxDB to be ready
for i in {1..15}; do
    curl -sf http://localhost:8086/health &>/dev/null && break
    sleep 2
done

# Initial setup (only if not already done)
if influx setup --host http://localhost:8086 \
    --org "${INFLUX_ORG}" \
    --bucket "${INFLUX_BUCKET}" \
    --username "${INFLUX_ADMIN_USER}" \
    --password "${INFLUX_ADMIN_PASS}" \
    --force 2>/dev/null; then
    info "InfluxDB initial setup complete."
else
    info "InfluxDB already set up, skipping initial config."
fi

# Grab the token for .env
INFLUX_TOKEN=$(influx auth list --json 2>/dev/null | python3.12 -c "
import sys, json
data = json.load(sys.stdin)
for a in data:
    if 'operator' in a.get('description','').lower() or a.get('permissions'):
        print(a['token']); break
" 2>/dev/null || echo "")

if [[ -z "$INFLUX_TOKEN" ]]; then
    warn "Could not auto-detect InfluxDB token. You'll need to set it in .env manually."
    INFLUX_TOKEN="PASTE-YOUR-INFLUXDB-TOKEN-HERE"
fi

info "InfluxDB is ready."

# ============================================================================
# 4. Grafana OSS
# ============================================================================
info "Installing Grafana..."
if ! command -v grafana-server &>/dev/null; then
    curl -fsSL https://apt.grafana.com/gpg.key | gpg --dearmor -o /etc/apt/trusted.gpg.d/grafana.gpg
    echo "deb [signed-by=/etc/apt/trusted.gpg.d/grafana.gpg] https://apt.grafana.com stable main" > /etc/apt/sources.list.d/grafana.list
    apt-get update -qq
    apt-get install -y -qq grafana
else
    info "Grafana already installed, skipping."
fi

systemctl enable --now grafana-server

# Install required plugins
info "Installing Grafana plugins..."
grafana-cli plugins install snuids-trafficlights-panel 2>/dev/null || true
grafana-cli plugins install heywesty-trafficlight-panel 2>/dev/null || true
grafana-cli plugins install grafana-clock-panel 2>/dev/null || true

# Allow unsigned plugins (heywesty-trafficlight-panel is unsigned)
GRAFANA_INI="/etc/grafana/grafana.ini"
if ! grep -q "heywesty-trafficlight-panel" "$GRAFANA_INI" 2>/dev/null; then
    sed -i 's/^;allow_loading_unsigned_plugins =.*/allow_loading_unsigned_plugins = heywesty-trafficlight-panel/' "$GRAFANA_INI" 2>/dev/null || true
    # If the line didn't exist uncommented, append to [plugins] section
    if ! grep -q "^allow_loading_unsigned_plugins" "$GRAFANA_INI" 2>/dev/null; then
        sed -i '/^\[plugins\]/a allow_loading_unsigned_plugins = heywesty-trafficlight-panel' "$GRAFANA_INI" 2>/dev/null || true
    fi
fi

systemctl restart grafana-server

info "Grafana is ready at http://localhost:3000 (default login: admin / admin)."

# ============================================================================
# 5. Clone repo & Python venv
# ============================================================================
if [[ ! -d "${REPO_DIR}/.git" ]]; then
    info "Cloning repository..."
    git clone -b "${REPO_BRANCH}" "${REPO_URL}" "${REPO_DIR}"
else
    info "Repository already exists at ${REPO_DIR}, pulling latest..."
    cd "${REPO_DIR}" && git pull origin "${REPO_BRANCH}" || true
fi

cd "${REPO_DIR}"

# Create venv if it doesn't exist
if [[ ! -f "${REPO_DIR}/bin/python3" ]]; then
    info "Creating Python 3.12 virtual environment..."
    python3.12 -m venv "${REPO_DIR}"
fi

info "Installing Python dependencies..."
"${REPO_DIR}/bin/pip" install --upgrade pip -q
"${REPO_DIR}/bin/pip" install -r requirements.txt -q

# ============================================================================
# 6. .env secrets file
# ============================================================================
info "Writing .env..."
cat > "${REPO_DIR}/.env" <<ENVEOF
# ── Robot Telemetry Secrets (auto-generated by setup-ubuntu.sh) ────────────
INFLUX_TOKEN=${INFLUX_TOKEN}
DB_PASSWORD=${PG_PASS}
INFLUX_ADMIN_PASS=${INFLUX_ADMIN_PASS}
ENVEOF
chmod 600 "${REPO_DIR}/.env"

# ============================================================================
# 7. Make owlet executable
# ============================================================================
if [[ -f "${REPO_DIR}/owlet-26.1.0-linuxx86-64" ]]; then
    chmod +x "${REPO_DIR}/owlet-26.1.0-linuxx86-64"
    info "owlet binary is executable."
else
    warn "owlet binary not found — hoot→wpilog conversion won't work until you add it."
fi

# ============================================================================
# 8. Create required directories
# ============================================================================
mkdir -p "${REPO_DIR}/input-logs"
mkdir -p "${REPO_DIR}/archive/logs"
mkdir -p "${REPO_DIR}/archive/metrics"
mkdir -p "${REPO_DIR}/archive/robot-logs"
info "Directory structure is ready."

# ============================================================================
# 9. Database migrations (Alembic)
# ============================================================================
info "Running Alembic migrations..."
cd "${REPO_DIR}"
"${REPO_DIR}/bin/alembic" upgrade head

info "Database schema is up to date."

# ============================================================================
# 10. systemd service
# ============================================================================
info "Installing systemd services..."
sed "s|REPO_DIR_PLACEHOLDER|${REPO_DIR}|g" "${REPO_DIR}/robot-telemetry.service" > /etc/systemd/system/robot-telemetry.service
sed "s|REPO_DIR_PLACEHOLDER|${REPO_DIR}|g" "${REPO_DIR}/upload-server.service" > /etc/systemd/system/upload-server.service
systemctl daemon-reload
systemctl enable robot-telemetry.service
systemctl restart robot-telemetry.service
systemctl enable upload-server.service
systemctl restart upload-server.service

info "robot-telemetry.service is active."
info "upload-server.service is active (http://localhost:8080)."

# ============================================================================
# 11. Grafana datasources & dashboards
# ============================================================================
info "Configuring Grafana datasources..."

# Wait for Grafana API to be ready
for i in {1..15}; do
    curl -sf -u admin:admin http://localhost:3000/api/health &>/dev/null && break
    sleep 2
done

GRAFANA_API="http://localhost:3000/api"
GRAFANA_AUTH="admin:admin"

# The datasource UIDs must match what generate_dashboards.py expects
PG_DS_UID="ffd8jczdyabr4c"
INFLUX_DS_UID="fffnbhv0kdukgc"

# Create PostgreSQL datasource (skip if already exists)
if ! curl -sf -u "${GRAFANA_AUTH}" "${GRAFANA_API}/datasources/uid/${PG_DS_UID}" &>/dev/null; then
    curl -sf -u "${GRAFANA_AUTH}" -X POST "${GRAFANA_API}/datasources" \
        -H "Content-Type: application/json" \
        -d "{
            \"name\": \"PostgreSQL\",
            \"type\": \"grafana-postgresql-datasource\",
            \"uid\": \"${PG_DS_UID}\",
            \"url\": \"127.0.0.1:5432\",
            \"database\": \"${PG_DB}\",
            \"user\": \"${PG_USER}\",
            \"secureJsonData\": {\"password\": \"${PG_PASS}\"},
            \"jsonData\": {\"sslmode\": \"disable\", \"postgresVersion\": 1600},
            \"access\": \"proxy\",
            \"isDefault\": false
        }" >/dev/null && info "PostgreSQL datasource created." || warn "Failed to create PostgreSQL datasource."
else
    info "PostgreSQL datasource already exists, skipping."
fi

# Create InfluxDB datasource (skip if already exists)
if ! curl -sf -u "${GRAFANA_AUTH}" "${GRAFANA_API}/datasources/uid/${INFLUX_DS_UID}" &>/dev/null; then
    curl -sf -u "${GRAFANA_AUTH}" -X POST "${GRAFANA_API}/datasources" \
        -H "Content-Type: application/json" \
        -d "{
            \"name\": \"InfluxDB\",
            \"type\": \"influxdb\",
            \"uid\": \"${INFLUX_DS_UID}\",
            \"url\": \"http://127.0.0.1:8086\",
            \"access\": \"proxy\",
            \"jsonData\": {
                \"version\": \"Flux\",
                \"organization\": \"${INFLUX_ORG}\",
                \"defaultBucket\": \"${INFLUX_BUCKET}\"
            },
            \"secureJsonData\": {\"token\": \"${INFLUX_TOKEN}\"},
            \"isDefault\": false
        }" >/dev/null && info "InfluxDB datasource created." || warn "Failed to create InfluxDB datasource."
else
    info "InfluxDB datasource already exists, skipping."
fi

# Generate and upload dashboards
info "Generating and uploading Grafana dashboards..."
"${REPO_DIR}/bin/python3" "${REPO_DIR}/generate_dashboards.py" --upload \
    --grafana-url http://localhost:3000 --grafana-user admin --grafana-pass admin

# ============================================================================
# 12. Summary
# ============================================================================
echo ""
echo "============================================================================"
echo " Setup Complete!"
echo "============================================================================"
echo ""
echo " PostgreSQL:    localhost:5432  db=${PG_DB}  user=${PG_USER}"
echo " InfluxDB:      http://localhost:8086  org=${INFLUX_ORG}  bucket=${INFLUX_BUCKET}"
echo " Grafana:       http://localhost:3000  (login: admin / admin)"
echo " Upload Server: http://localhost:8080  (drag & drop log files)"
echo " Service:       systemctl status robot-telemetry"
echo "                systemctl status upload-server"
echo ""
echo " Grafana datasources and dashboards have been configured automatically."
echo " Dashboards available at:"
echo "   - Match Stoplight:    http://localhost:3000/d/match-stoplight"
echo "   - Signal Explorer:    http://localhost:3000/d/signal-explorer"
echo "   - Subsystem Overview: http://localhost:3000/d/subsystem-overview"
echo ""
echo " Drop .hoot log directories into ${REPO_DIR}/input-logs/"
echo " and the service will process them automatically."
echo ""
echo " To check the service log:  journalctl -u robot-telemetry -f"
echo "============================================================================"
