#!/usr/bin/with-contenv bashio
# ==============================================================================
# EMS Energy Manager - Startup script
# ==============================================================================
set -e

bashio::log.info "======================================"
bashio::log.info "  EMS Energy Management System v1.0"
bashio::log.info "======================================"

# Read add-on config
MQTT_HOST=$(bashio::config 'mqtt_host')
MQTT_PORT=$(bashio::config 'mqtt_port')
MQTT_USER=$(bashio::config 'mqtt_username')
MQTT_PASS=$(bashio::config 'mqtt_password')
SCAN_INTERVAL=$(bashio::config 'scan_interval')
LOG_LEVEL=$(bashio::config 'log_level')
TARIFF_IMPORT=$(bashio::config 'energy_tariff_import')
TARIFF_EXPORT=$(bashio::config 'energy_tariff_export')
MAX_GRID=$(bashio::config 'max_grid_power')

# Export as environment variables for Python
export EMS_MQTT_HOST="${MQTT_HOST}"
export EMS_MQTT_PORT="${MQTT_PORT}"
export EMS_MQTT_USER="${MQTT_USER}"
export EMS_MQTT_PASS="${MQTT_PASS}"
export EMS_SCAN_INTERVAL="${SCAN_INTERVAL}"
export EMS_LOG_LEVEL="${LOG_LEVEL}"
export EMS_TARIFF_IMPORT="${TARIFF_IMPORT}"
export EMS_TARIFF_EXPORT="${TARIFF_EXPORT}"
export EMS_MAX_GRID="${MAX_GRID}"
export EMS_DATA_DIR="/config/ems"
export EMS_HA_TOKEN="${SUPERVISOR_TOKEN}"
export EMS_HA_URL="http://supervisor/core/api"

# Create persistent data directory
mkdir -p /config/ems
bashio::log.info "Data directory: /config/ems"

# Start Python backend on port 8765 (internal)
bashio::log.info "Starting EMS backend (port 8765)..."
python3 /app/backend.py &
BACKEND_PID=$!

# Wait until backend is ready
MAX_WAIT=15
WAITED=0
while ! curl -sf http://127.0.0.1:8765/api/live > /dev/null 2>&1; do
    sleep 1
    WAITED=$((WAITED + 1))
    if [ $WAITED -ge $MAX_WAIT ]; then
        bashio::log.warning "Backend did not start in time, continuing anyway..."
        break
    fi
done
bashio::log.info "EMS backend ready after ${WAITED}s"

# Start nginx (serves dashboard + proxies API)
bashio::log.info "Starting nginx (port 8099)..."
nginx -g "daemon off;" &
NGINX_PID=$!

bashio::log.info "EMS is running! Open the EMS panel in your HA sidebar."

# Keep container alive, restart if either process dies
while true; do
    if ! kill -0 $BACKEND_PID 2>/dev/null; then
        bashio::log.error "Backend crashed, restarting..."
        python3 /app/backend.py &
        BACKEND_PID=$!
    fi
    if ! kill -0 $NGINX_PID 2>/dev/null; then
        bashio::log.error "Nginx crashed, restarting..."
        nginx -g "daemon off;" &
        NGINX_PID=$!
    fi
    sleep 10
done
