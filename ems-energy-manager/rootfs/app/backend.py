#!/usr/bin/env python3
"""
EMS Energy Management System - Backend
Handles: Home Assistant API, MQTT, Modbus TCP, WebSocket live data, device storage
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import aiohttp
import aiofiles
from aiohttp import web, WSMsgType

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("EMS_LOG_LEVEL", "info").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [EMS] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ems")

# ── Config from environment (set by run.sh from addon config) ─────────────────
DATA_DIR        = Path(os.getenv("EMS_DATA_DIR", "/config/ems"))
HA_URL          = os.getenv("EMS_HA_URL", "http://supervisor/core/api")
HA_TOKEN        = os.getenv("EMS_HA_TOKEN", "")
MQTT_HOST       = os.getenv("EMS_MQTT_HOST", "homeassistant")
MQTT_PORT       = int(os.getenv("EMS_MQTT_PORT", "1883"))
MQTT_USER       = os.getenv("EMS_MQTT_USER", "")
MQTT_PASS       = os.getenv("EMS_MQTT_PASS", "")
SCAN_INTERVAL   = int(os.getenv("EMS_SCAN_INTERVAL", "10"))
TARIFF_IMPORT   = float(os.getenv("EMS_TARIFF_IMPORT", "0.28"))
TARIFF_EXPORT   = float(os.getenv("EMS_TARIFF_EXPORT", "0.08"))
MAX_GRID        = int(os.getenv("EMS_MAX_GRID", "10000"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
DEVICES_FILE  = DATA_DIR / "devices.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
HISTORY_FILE  = DATA_DIR / "history.json"

# ── Default settings ──────────────────────────────────────────────────────────
DEFAULT_SETTINGS = {
    "strategy_self_consumption": True,
    "strategy_smart_charging":   True,
    "strategy_dynamic_tariff":   False,
    "strategy_peak_shaving":     True,
    "strategy_heatpump":         False,
    "battery_min_soc":           20,
    "battery_night_charging":    False,
    "battery_v2g":               False,
    "mqtt_enabled":              True,
    "modbus_enabled":            True,
    "ocpp_enabled":              True,
    "notify_grid_fail":          True,
    "notify_battery":            True,
    "notify_ev_done":            False,
    "energy_tariff_import":      TARIFF_IMPORT,
    "energy_tariff_export":      TARIFF_EXPORT,
    "scan_interval":             SCAN_INTERVAL,
    "max_grid_power":            MAX_GRID,
    "currency":                  "EUR",
}

# ── Global state ──────────────────────────────────────────────────────────────
state = {
    "devices":    [],
    "settings":   {},
    "live": {
        "solar_power":     0,
        "battery_soc":     0,
        "battery_power":   0,
        "grid_power":      0,
        "home_power":      0,
        "ev_power":        0,
        "heatpump_power":  0,
        "boiler_power":    0,
        "timestamp":       "",
    },
    "history":    [],
    "logs":       [],
    "ws_clients": set(),
}

# ── Persistence ───────────────────────────────────────────────────────────────
async def load_data():
    if DEVICES_FILE.exists():
        async with aiofiles.open(DEVICES_FILE) as f:
            state["devices"] = json.loads(await f.read())
        log.info(f"Loaded {len(state['devices'])} devices from disk")

    if SETTINGS_FILE.exists():
        async with aiofiles.open(SETTINGS_FILE) as f:
            saved = json.loads(await f.read())
        state["settings"] = {**DEFAULT_SETTINGS, **saved}
    else:
        state["settings"] = DEFAULT_SETTINGS.copy()

    if HISTORY_FILE.exists():
        async with aiofiles.open(HISTORY_FILE) as f:
            state["history"] = json.loads(await f.read())

    log.info("Data loaded")


async def save_devices():
    async with aiofiles.open(DEVICES_FILE, "w") as f:
        await f.write(json.dumps(state["devices"], indent=2))

async def save_settings():
    async with aiofiles.open(SETTINGS_FILE, "w") as f:
        await f.write(json.dumps(state["settings"], indent=2))

async def save_history():
    # Keep last 288 points = 24h at 5-min intervals
    async with aiofiles.open(HISTORY_FILE, "w") as f:
        await f.write(json.dumps(state["history"][-288:], indent=2))

# ── Home Assistant API client ─────────────────────────────────────────────────
class HAClient:
    def __init__(self):
        self._session: aiohttp.ClientSession | None = None

    @property
    def headers(self):
        return {
            "Authorization": f"Bearer {HA_TOKEN}",
            "Content-Type":  "application/json",
        }

    async def session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(headers=self.headers)
        return self._session

    async def get_state(self, entity_id: str) -> dict | None:
        try:
            s = await self.session()
            async with s.get(f"{HA_URL}/states/{entity_id}") as r:
                if r.status == 200:
                    return await r.json()
        except Exception as e:
            log.debug(f"HA get_state({entity_id}): {e}")
        return None

    async def call_service(self, domain: str, service: str, data: dict = None) -> bool:
        try:
            s = await self.session()
            async with s.post(f"{HA_URL}/services/{domain}/{service}", json=data or {}) as r:
                return r.status == 200
        except Exception as e:
            log.debug(f"HA call_service({domain}.{service}): {e}")
        return False

    async def get_all_states(self) -> list:
        try:
            s = await self.session()
            async with s.get(f"{HA_URL}/states") as r:
                if r.status == 200:
                    return await r.json()
        except Exception as e:
            log.debug(f"HA get_all_states: {e}")
        return []

    async def close(self):
        if self._session:
            await self._session.close()

ha = HAClient()

# ── Modbus register maps per brand ────────────────────────────────────────────
MODBUS_MAPS = {
    "Huawei": {
        "power_reg": 32064, "power_scale": 1,   "slave": 0,
        "soc_reg":   37004, "soc_scale":   0.1,
    },
    "SolarEdge": {
        "power_reg": 40083, "power_scale": 1,   "slave": 1,
        "soc_reg":   62,    "soc_scale":   1,
    },
    "Fronius": {
        "power_reg": 40083, "power_scale": 0.1, "slave": 1,
    },
    "Growatt": {
        "power_reg": 35,    "power_scale": 0.1, "slave": 1,
    },
    "Victron": {
        "power_reg": 820,   "power_scale": 1,   "slave": 100,
    },
    "Solax": {
        "power_reg": 70,    "power_scale": 1,   "slave": 1,
        "soc_reg":   103,   "soc_scale":   1,
    },
    "Eastron": {
        "power_reg": 12,    "power_scale": 0.01,"slave": 1,
    },
    "BYD": {
        "power_reg": 30775, "power_scale": 1,   "slave": 1,
        "soc_reg":   30845, "soc_scale":   1,
    },
}


async def poll_modbus(device: dict) -> dict:
    """Poll a device via Modbus TCP and return updated fields."""
    updates = {}
    try:
        from pymodbus.client import AsyncModbusTcpClient
        reg_map = MODBUS_MAPS.get(device.get("brand", ""), {
            "power_reg": 40083, "power_scale": 1, "slave": 1
        })
        client = AsyncModbusTcpClient(
            host=device["ip"],
            port=int(device.get("port", 502)),
            timeout=3,
        )
        if await client.connect():
            # Read power register
            r = await client.read_holding_registers(
                reg_map["power_reg"], count=2,
                slave=reg_map.get("slave", 1)
            )
            if not r.isError():
                raw = r.registers[0]
                # Handle signed 16-bit
                if raw > 32767:
                    raw -= 65536
                updates["power"] = round(raw * reg_map.get("power_scale", 1))

            # Read SoC register if available
            if "soc_reg" in reg_map:
                r2 = await client.read_holding_registers(
                    reg_map["soc_reg"], count=1,
                    slave=reg_map.get("slave", 1)
                )
                if not r2.isError():
                    updates["soc"] = round(r2.registers[0] * reg_map.get("soc_scale", 1), 1)

            updates["status"] = "online"
            await client.close()
        else:
            updates["status"] = "offline"
    except Exception as e:
        log.debug(f"Modbus poll {device.get('name')}: {e}")
        updates["status"] = "offline"
    return updates


async def poll_ha_entity(device: dict) -> dict:
    """Read power/SoC from linked HA entities."""
    updates = {}
    if device.get("ha_entity_power"):
        s = await ha.get_state(device["ha_entity_power"])
        if s:
            try:
                updates["power"] = round(float(s["state"]))
                updates["status"] = "online"
            except (ValueError, TypeError):
                pass

    if device.get("ha_entity_soc"):
        s = await ha.get_state(device["ha_entity_soc"])
        if s:
            try:
                updates["soc"] = round(float(s["state"]), 1)
            except (ValueError, TypeError):
                pass
    return updates


async def poll_all_devices():
    """Poll all active devices and aggregate live totals."""
    totals = {k: 0.0 for k in ["solar", "battery_power", "battery_soc",
                                 "grid", "ev", "hp", "boiler"]}
    bat_count = 0

    for i, device in enumerate(state["devices"]):
        if device.get("status") == "disabled":
            continue

        updates = {}
        proto = device.get("protocol", "")

        if proto == "Modbus TCP" and device.get("ip"):
            updates = await poll_modbus(device)
        elif proto == "Home Assistant Entiteit":
            updates = await poll_ha_entity(device)
        # MQTT devices are updated via subscription (not polled)

        if updates:
            state["devices"][i].update(updates)
            device.update(updates)

        # Aggregate
        pwr = float(device.get("power", 0))
        dtype = device.get("type", "")
        if dtype == "Zonnepanelen":
            totals["solar"] += pwr
        elif dtype == "Thuisbatterij":
            totals["battery_power"] += pwr
            totals["battery_soc"] += float(device.get("soc", 0))
            bat_count += 1
        elif dtype in ("Net / Slimme meter",):
            totals["grid"] += pwr
        elif dtype == "Laadpaal (EV)":
            totals["ev"] += pwr
        elif dtype == "Warmtepomp":
            totals["hp"] += pwr
        elif dtype == "Boiler":
            totals["boiler"] += pwr

    avg_soc = round(totals["battery_soc"] / bat_count, 1) if bat_count else 0
    home = max(0, totals["solar"] - abs(totals["battery_power"]) - totals["ev"] - totals["hp"] - totals["boiler"])

    state["live"].update({
        "solar_power":    round(totals["solar"]),
        "battery_power":  round(totals["battery_power"]),
        "battery_soc":    avg_soc,
        "grid_power":     round(totals["grid"]),
        "home_power":     round(home),
        "ev_power":       round(totals["ev"]),
        "heatpump_power": round(totals["hp"]),
        "boiler_power":   round(totals["boiler"]),
        "timestamp":      datetime.now().isoformat(),
    })


# ── EMS Strategy engine ───────────────────────────────────────────────────────
async def run_strategy():
    s   = state["settings"]
    lv  = state["live"]
    soc = lv["battery_soc"]
    sol = lv["solar_power"]
    ev  = lv["ev_power"]
    hp  = lv["heatpump_power"]
    grd = lv["grid_power"]
    min_soc = s.get("battery_min_soc", 20)

    # Self-consumption: avoid grid export when battery not full
    if s.get("strategy_self_consumption"):
        surplus = sol - lv["home_power"] - ev - hp
        if surplus > 300 and soc < 95:
            await ha.call_service("number", "set_value", {
                "entity_id": "number.ems_battery_charge_power",
                "value": str(min(int(surplus), 5000))
            })
        elif soc <= min_soc and grd > 500:
            add_log("warn", f"Batterij onder reserve ({soc}%) — laden beperkt")

    # Smart EV charging: charge EV only from solar surplus
    if s.get("strategy_smart_charging"):
        solar_surplus = sol - lv["home_power"] - abs(lv["battery_power"])
        if solar_surplus >= 1380 and ev == 0:
            await ha.call_service("switch", "turn_on",
                                  {"entity_id": "switch.ems_ev_charger"})
            add_log("ok", f"Slimme laadpaal gestart: {int(solar_surplus)}W overschot")
        elif solar_surplus < 500 and ev > 0:
            await ha.call_service("switch", "turn_off",
                                  {"entity_id": "switch.ems_ev_charger"})
            add_log("info", "Laadpaal gepauzeerd: onvoldoende zonne-overschot")

    # Peak shaving: limit grid import below max
    if s.get("strategy_peak_shaving"):
        max_g = s.get("max_grid_power", 10000)
        if grd > max_g * 0.9:
            add_log("warn", f"Piekbeveiliging: {grd}W > {max_g}W drempel")
            await ha.call_service("number", "set_value", {
                "entity_id": "number.ems_ev_charge_current",
                "value": "6"
            })

    # Heat pump scheduling: run during solar hours
    if s.get("strategy_heatpump"):
        hour = datetime.now().hour
        if sol > 2000 and 9 <= hour <= 16 and hp == 0:
            await ha.call_service("climate", "set_hvac_mode", {
                "entity_id": "climate.ems_heatpump",
                "hvac_mode": "heat"
            })
            add_log("ok", "Warmtepomp geactiveerd op zonne-energie")


# ── WebSocket broadcast ───────────────────────────────────────────────────────
def add_log(level: str, message: str):
    entry = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "type": level,
        "msg":  message,
    }
    state["logs"].append(entry)
    if len(state["logs"]) > 300:
        state["logs"] = state["logs"][-300:]
    asyncio.ensure_future(broadcast({"type": "log", "data": entry}))
    log.info(f"[{level.upper()}] {message}")


async def broadcast(message: dict):
    dead = set()
    msg_str = json.dumps(message)
    for ws in state["ws_clients"].copy():
        try:
            await ws.send_str(msg_str)
        except Exception:
            dead.add(ws)
    state["ws_clients"] -= dead


# ── Main polling loop ─────────────────────────────────────────────────────────
async def polling_loop():
    interval = state["settings"].get("scan_interval", SCAN_INTERVAL)
    last_history_save = 0

    while True:
        try:
            await poll_all_devices()
            await run_strategy()
            await broadcast({"type": "live",    "data": state["live"]})
            await broadcast({"type": "devices", "data": state["devices"]})

            # Append history point every 5 minutes
            now = time.time()
            if now - last_history_save >= 300:
                point = {**state["live"], "ts": datetime.now().isoformat()}
                state["history"].append(point)
                await save_history()
                last_history_save = now

        except Exception as e:
            log.error(f"Polling loop error: {e}")

        await asyncio.sleep(interval)


# ── HTTP / WebSocket handlers ─────────────────────────────────────────────────
async def ws_handler(request):
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    state["ws_clients"].add(ws)
    log.info(f"WS client connected ({len(state['ws_clients'])} total)")

    # Send full initial state
    await ws.send_str(json.dumps({
        "type": "init",
        "data": {
            "live":     state["live"],
            "devices":  state["devices"],
            "settings": state["settings"],
            "logs":     state["logs"][-50:],
        }
    }))

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get("action") == "ping":
                    await ws.send_str(json.dumps({"type": "pong"}))
            elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                break
    finally:
        state["ws_clients"].discard(ws)
    return ws


async def handle_live(req):
    return web.json_response(state["live"])


async def handle_devices_get(req):
    return web.json_response(state["devices"])


async def handle_devices_post(req):
    try:
        device = await req.json()
        device["id"] = int(time.time() * 1000)
        device.setdefault("status", "online")
        device.setdefault("power", 0)
        device.setdefault("soc", None)
        state["devices"].append(device)
        await save_devices()
        add_log("ok", f"Apparaat toegevoegd: {device.get('name')}")
        await broadcast({"type": "devices", "data": state["devices"]})
        return web.json_response(device, status=201)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)


async def handle_device_patch(req):
    dev_id = int(req.match_info["id"])
    updates = await req.json()
    for i, d in enumerate(state["devices"]):
        if d.get("id") == dev_id:
            state["devices"][i].update(updates)
            await save_devices()
            await broadcast({"type": "devices", "data": state["devices"]})
            return web.json_response(state["devices"][i])
    return web.json_response({"error": "Not found"}, status=404)


async def handle_device_delete(req):
    dev_id = int(req.match_info["id"])
    before = len(state["devices"])
    state["devices"] = [d for d in state["devices"] if d.get("id") != dev_id]
    if len(state["devices"]) < before:
        await save_devices()
        await broadcast({"type": "devices", "data": state["devices"]})
        add_log("info", f"Apparaat verwijderd (id={dev_id})")
        return web.json_response({"ok": True})
    return web.json_response({"error": "Not found"}, status=404)


async def handle_settings_get(req):
    return web.json_response(state["settings"])


async def handle_settings_post(req):
    try:
        updates = await req.json()
        state["settings"].update(updates)
        await save_settings()
        add_log("info", "Instellingen opgeslagen")
        return web.json_response(state["settings"])
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)


async def handle_history(req):
    limit = int(req.query.get("limit", 288))
    return web.json_response(state["history"][-limit:])


async def handle_logs(req):
    limit = int(req.query.get("limit", 100))
    return web.json_response(state["logs"][-limit:])


async def handle_ha_entities(req):
    """Return HA entities filtered to energy-relevant types."""
    entities = await ha.get_all_states()
    relevant_prefixes = ("sensor.", "number.", "switch.", "climate.", "input_number.")
    result = []
    for e in entities:
        if any(e["entity_id"].startswith(p) for p in relevant_prefixes):
            result.append({
                "entity_id":     e["entity_id"],
                "state":         e.get("state", ""),
                "friendly_name": e.get("attributes", {}).get("friendly_name", e["entity_id"]),
                "unit":          e.get("attributes", {}).get("unit_of_measurement", ""),
            })
    return web.json_response(result)


# ── App setup ─────────────────────────────────────────────────────────────────
async def on_startup(app):
    await load_data()
    add_log("ok", "EMS Energy Management System gestart")
    asyncio.ensure_future(polling_loop())


async def on_shutdown(app):
    await ha.close()
    await save_devices()
    await save_settings()


def build_app() -> web.Application:
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    app.router.add_get( "/ws",                ws_handler)
    app.router.add_get( "/api/live",           handle_live)
    app.router.add_get( "/api/devices",        handle_devices_get)
    app.router.add_post("/api/devices",        handle_devices_post)
    app.router.add_patch("/api/devices/{id}",  handle_device_patch)
    app.router.add_delete("/api/devices/{id}", handle_device_delete)
    app.router.add_get( "/api/settings",       handle_settings_get)
    app.router.add_post("/api/settings",       handle_settings_post)
    app.router.add_get( "/api/history",        handle_history)
    app.router.add_get( "/api/logs",           handle_logs)
    app.router.add_get( "/api/ha/entities",    handle_ha_entities)

    return app


if __name__ == "__main__":
    log.info("Starting EMS backend on 127.0.0.1:8765")
    web.run_app(build_app(), host="127.0.0.1", port=8765, access_log=None)
