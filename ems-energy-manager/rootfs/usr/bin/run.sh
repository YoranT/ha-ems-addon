#!/usr/bin/with-contenv bashio
# ==============================================================================
# EMS Energy Manager v2 - Startup script
# ==============================================================================
set -e

bashio::log.info "======================================"
bashio::log.info "  EMS Energy Management System v2.0"
bashio::log.info "======================================"

SCAN_INTERVAL=$(bashio::config 'scan_interval')
LOG_LEVEL=$(bashio::config 'log_level')
TARIFF_IMPORT=$(bashio::config 'energy_tariff_import')
TARIFF_EXPORT=$(bashio::config 'energy_tariff_export')
MAX_GRID=$(bashio::config 'max_grid_power')

export EMS_SCAN_INTERVAL="${SCAN_INTERVAL}"
export EMS_LOG_LEVEL="${LOG_LEVEL}"
export EMS_TARIFF_IMPORT="${TARIFF_IMPORT}"
export EMS_TARIFF_EXPORT="${TARIFF_EXPORT}"
export EMS_MAX_GRID="${MAX_GRID}"
export EMS_DATA_DIR="/config/ems"
export EMS_HA_TOKEN="${SUPERVISOR_TOKEN}"
export EMS_HA_URL="http://supervisor/core/api"

bashio::log.info "Config: interval=${SCAN_INTERVAL}s, import=${TARIFF_IMPORT}eu, export=${TARIFF_EXPORT}eu, max_grid=${MAX_GRID}W"

mkdir -p /config/ems

bashio::log.info "Starting EMS Python backend on port 8765..."
python3 /app/backend.py &
BACKEND_PID=$!

bashio::log.info "Waiting for backend..."
MAX_WAIT=20
WAITED=0
until curl -sf http://127.0.0.1:8765/api/live > /dev/null 2>&1; do
    sleep 1
    WAITED=$((WAITED + 1))
    if [ "${WAITED}" -ge "${MAX_WAIT}" ]; then
        bashio::log.warning "Backend not ready after ${MAX_WAIT}s, continuing anyway..."
        break
    fi
done
bashio::log.info "Backend ready after ${WAITED}s"

bashio::log.info "Starting nginx on port 8099..."
nginx -g "daemon off;" &
NGINX_PID=$!

bashio::log.info "EMS is running! Open EMS in the Home Assistant sidebar."

while true; do
    if ! kill -0 "${BACKEND_PID}" 2>/dev/null; then
        bashio::log.error "Backend crashed, restarting..."
        python3 /app/backend.py &
        BACKEND_PID=$!
    fi
    if ! kill -0 "${NGINX_PID}" 2>/dev/null; then
        bashio::log.error "Nginx crashed, restarting..."
        nginx -g "daemon off;" &
        NGINX_PID=$!
    fi
    sleep 15
done
