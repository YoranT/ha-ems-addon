#!/usr/bin/env python3
"""
EMS Energy Management System - Backend v2
Real device drivers: SolarEdge Modbus, Sessy REST, Easee API,
HomeWizard P1, Sessy P1, HA entities, generic Modbus.
No MQTT dependency.
"""
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import aiohttp
import aiofiles
from aiohttp import web, WSMsgType

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("EMS_LOG_LEVEL", "info").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [EMS] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("ems")

# ── Config ────────────────────────────────────────────────────────────────────
DATA_DIR      = Path(os.getenv("EMS_DATA_DIR", "/config/ems"))
HA_URL        = os.getenv("EMS_HA_URL", "http://supervisor/core/api")
HA_TOKEN      = os.getenv("EMS_HA_TOKEN", "")
SCAN_INTERVAL = int(os.getenv("EMS_SCAN_INTERVAL", "10"))
MAX_GRID      = int(os.getenv("EMS_MAX_GRID", "10000"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
DEVICES_FILE  = DATA_DIR / "devices.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
HISTORY_FILE  = DATA_DIR / "history.json"

# ── Add app dir to path so drivers are importable ────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

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
    "notify_grid_fail":          True,
    "notify_battery":            True,
    "notify_ev_done":            False,
    "energy_tariff_import":      float(os.getenv("EMS_TARIFF_IMPORT", "0.28")),
    "energy_tariff_export":      float(os.getenv("EMS_TARIFF_EXPORT", "0.08")),
    "scan_interval":             SCAN_INTERVAL,
    "max_grid_power":            MAX_GRID,
    "currency":                  "EUR",
}

# ── Global state ──────────────────────────────────────────────────────────────
state = {
    "devices":    [],
    "settings":   {},
    "live": {
        "solar_power":    0,
        "battery_soc":    0.0,
        "battery_power":  0,
        "grid_power":     0,
        "home_power":     0,
        "ev_power":       0,
        "heatpump_power": 0,
        "boiler_power":   0,
        "timestamp":      "",
    },
    "history":    [],
    "logs":       [],
    "ws_clients": set(),
    "_drivers":   {},   # device_id → driver instance
}

# ── Persistence ───────────────────────────────────────────────────────────────
async def load_data():
    if DEVICES_FILE.exists():
        async with aiofiles.open(DEVICES_FILE) as f:
            state["devices"] = json.loads(await f.read())
        log.info(f"Loaded {len(state['devices'])} devices")

    if SETTINGS_FILE.exists():
        async with aiofiles.open(SETTINGS_FILE) as f:
            saved = json.loads(await f.read())
        state["settings"] = {**DEFAULT_SETTINGS, **saved}
    else:
        state["settings"] = DEFAULT_SETTINGS.copy()

    if HISTORY_FILE.exists():
        async with aiofiles.open(HISTORY_FILE) as f:
            state["history"] = json.loads(await f.read())

    _rebuild_drivers()
    log.info("Data loaded")

async def save_devices():
    async with aiofiles.open(DEVICES_FILE, "w") as f:
        await f.write(json.dumps(state["devices"], indent=2))

async def save_settings():
    async with aiofiles.open(SETTINGS_FILE, "w") as f:
        await f.write(json.dumps(state["settings"], indent=2))

async def save_history():
    async with aiofiles.open(HISTORY_FILE, "w") as f:
        await f.write(json.dumps(state["history"][-288:], indent=2))

# ── Driver management ─────────────────────────────────────────────────────────
def _rebuild_drivers():
    """Instantiate drivers for all devices that don't have one yet."""
    from drivers.registry import get_driver
    for device in state["devices"]:
        dev_id = device.get("id")
        if dev_id not in state["_drivers"]:
            driver = get_driver(device, ha_url=HA_URL, ha_token=HA_TOKEN)
            state["_drivers"][dev_id] = driver
            if driver:
                log.info(f"Driver: {device.get('name')} → {type(driver).__name__}")
            else:
                log.debug(f"No driver for: {device.get('name')}")

# ── Device polling ────────────────────────────────────────────────────────────
async def poll_all_devices():
    totals = dict(solar=0, bat_pwr=0, bat_soc=0, bat_cnt=0,
                  grid=0, ev=0, hp=0, boiler=0)

    tasks = []
    for device in state["devices"]:
        if device.get("status") == "disabled":
            continue
        dev_id = device.get("id")
        driver = state["_drivers"].get(dev_id)
        if driver:
            tasks.append(_poll_one(device, driver))

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            if isinstance(res, Exception):
                log.debug(f"Poll exception: {res}")

    # Aggregate
    for device in state["devices"]:
        if device.get("status") == "disabled":
            continue
        pwr   = float(device.get("power", 0))
        dtype = device.get("type", "")
        if dtype == "Zonnepanelen" or (dtype == "Omvormer" and pwr > 0):
            totals["solar"] += pwr
        elif dtype == "Thuisbatterij":
            totals["bat_pwr"] += pwr
            totals["bat_soc"] += float(device.get("soc", 0))
            totals["bat_cnt"] += 1
        elif dtype == "Net / Slimme meter":
            totals["grid"] += pwr
        elif dtype == "Laadpaal (EV)":
            totals["ev"] += pwr
        elif dtype == "Warmtepomp":
            totals["hp"] += pwr
        elif dtype == "Boiler":
            totals["boiler"] += pwr

    avg_soc   = round(totals["bat_soc"] / totals["bat_cnt"], 1) if totals["bat_cnt"] else 0
    home_pwr  = max(0, round(totals["solar"]
                              - abs(totals["bat_pwr"])
                              - totals["ev"]
                              - totals["hp"]
                              - totals["boiler"]
                              - max(0, -totals["grid"])))

    state["live"].update({
        "solar_power":    round(totals["solar"]),
        "battery_power":  round(totals["bat_pwr"]),
        "battery_soc":    avg_soc,
        "grid_power":     round(totals["grid"]),
        "home_power":     home_pwr,
        "ev_power":       round(totals["ev"]),
        "heatpump_power": round(totals["hp"]),
        "boiler_power":   round(totals["boiler"]),
        "timestamp":      datetime.now().isoformat(),
    })


async def _poll_one(device: dict, driver) -> None:
    """Poll a single device and merge updates back into state."""
    try:
        updates = await asyncio.wait_for(driver.read(), timeout=8)
        if updates:
            dev_id = device.get("id")
            for i, d in enumerate(state["devices"]):
                if d.get("id") == dev_id:
                    state["devices"][i].update(updates)
                    device.update(updates)
                    break
    except asyncio.TimeoutError:
        log.debug(f"Timeout polling {device.get('name')}")
        device["status"] = "offline"
    except Exception as e:
        log.debug(f"Poll error {device.get('name')}: {e}")
        device["status"] = "offline"


# ── EMS Strategy engine ───────────────────────────────────────────────────────
async def run_strategy():
    s   = state["settings"]
    lv  = state["live"]
    sol = lv["solar_power"]
    soc = lv["battery_soc"]
    grd = lv["grid_power"]
    ev  = lv["ev_power"]
    hp  = lv["heatpump_power"]
    min_soc = s.get("battery_min_soc", 20)

    # ── Self-consumption / battery protection ──────────────────────────────
    if s.get("strategy_self_consumption"):
        if soc < min_soc and grd > 500:
            add_log("warn", f"Batterij onder minimum ({soc:.0f}% < {min_soc}%) — terugval bescherming actief")

    # ── Smart EV charging ─────────────────────────────────────────────────
    if s.get("strategy_smart_charging"):
        surplus = sol - lv["home_power"] - abs(lv.get("battery_power", 0))

        # Find Easee chargers
        easee_chargers = [d for d in state["devices"]
                          if d.get("brand") == "Easee"
                          and d.get("type") == "Laadpaal (EV)"
                          and d.get("status") == "online"]

        for charger in easee_chargers:
            from drivers.easee import EaseeChargerDriver
            drv = state["_drivers"].get(charger["id"])
            if not isinstance(drv, EaseeChargerDriver):
                continue
            if surplus >= 1380 and not charger.get("is_charging"):
                await drv.start_charging()
                add_log("ok", f"Laadpaal gestart: {int(surplus)}W zonne-overschot")
            elif surplus < 500 and charger.get("is_charging"):
                amps = max(6, int(surplus / 230))
                await drv.set_dynamic_current(amps)
                add_log("info", f"Laadstroom verlaagd naar {amps}A (surplus {int(surplus)}W)")

    # ── Smart Sessy battery control ────────────────────────────────────────
    if s.get("strategy_self_consumption"):
        sessy_bats = [d for d in state["devices"]
                      if d.get("brand") == "Sessy"
                      and d.get("type") == "Thuisbatterij"
                      and d.get("status") == "online"]

        for bat in sessy_bats:
            from drivers.sessy import SessyBatteryDriver
            drv = state["_drivers"].get(bat["id"])
            if not isinstance(drv, SessyBatteryDriver):
                continue

            surplus = sol - lv["home_power"] - ev
            if surplus > 300 and soc < 98:
                # Charge from solar surplus
                charge_w = min(int(surplus), 3700)  # Sessy max ~3.7kW
                await drv.set_power(-charge_w)  # negative = charge
                add_log("info", f"Sessy laden: {charge_w}W van zonnestroom")
            elif grd > 200 and soc > min_soc + 5:
                # Discharge to avoid grid import
                discharge_w = min(int(grd), 3700)
                await drv.set_power(discharge_w)  # positive = discharge
            else:
                await drv.set_strategy("HOME_SMART")  # let Sessy decide

    # ── Peak shaving ───────────────────────────────────────────────────────
    if s.get("strategy_peak_shaving"):
        max_g = s.get("max_grid_power", 10000)
        if grd > max_g * 0.9:
            add_log("warn", f"Piekbeveiliging: netafname {grd}W nadert limiet {max_g}W")


# ── WebSocket ─────────────────────────────────────────────────────────────────
def add_log(level: str, message: str):
    entry = {"time": datetime.now().strftime("%H:%M:%S"), "type": level, "msg": message}
    state["logs"].append(entry)
    if len(state["logs"]) > 300:
        state["logs"] = state["logs"][-300:]
    asyncio.ensure_future(broadcast({"type": "log", "data": entry}))
    log.info(f"[{level}] {message}")


async def broadcast(message: dict):
    dead, msg_str = set(), json.dumps(message)
    for ws in state["ws_clients"].copy():
        try:
            await ws.send_str(msg_str)
        except Exception:
            dead.add(ws)
    state["ws_clients"] -= dead


# ── Main loop ─────────────────────────────────────────────────────────────────
async def polling_loop():
    interval = state["settings"].get("scan_interval", SCAN_INTERVAL)
    last_history = 0
    while True:
        try:
            await poll_all_devices()
            await run_strategy()
            await broadcast({"type": "live",    "data": state["live"]})
            await broadcast({"type": "devices", "data": _safe_devices()})

            now = time.time()
            if now - last_history >= 300:
                point = {**state["live"], "ts": datetime.now().isoformat()}
                state["history"].append(point)
                await save_history()
                last_history = now
        except Exception as e:
            log.error(f"Polling loop error: {e}")
        await asyncio.sleep(interval)


def _safe_devices():
    """Return devices without sensitive credentials."""
    safe = []
    for d in state["devices"]:
        s = {k: v for k, v in d.items() if k not in ("password", "username")}
        safe.append(s)
    return safe


# ── HTTP handlers ─────────────────────────────────────────────────────────────
async def ws_handler(req):
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(req)
    state["ws_clients"].add(ws)
    await ws.send_str(json.dumps({"type": "init", "data": {
        "live":     state["live"],
        "devices":  _safe_devices(),
        "settings": state["settings"],
        "logs":     state["logs"][-50:],
    }}))
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    cmd = json.loads(msg.data)
                    if cmd.get("action") == "ping":
                        await ws.send_str(json.dumps({"type": "pong"}))
                    elif cmd.get("action") == "control":
                        await handle_control_cmd(cmd)
                except Exception:
                    pass
            elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                break
    finally:
        state["ws_clients"].discard(ws)
    return ws


async def handle_control_cmd(cmd: dict):
    """Handle control commands sent over WebSocket."""
    device_id = cmd.get("device_id")
    action    = cmd.get("cmd")
    value     = cmd.get("value")
    device    = next((d for d in state["devices"] if d.get("id") == device_id), None)
    if not device:
        return
    from drivers.registry import get_control_driver
    drv = await get_control_driver(device)
    if not drv:
        return
    if action == "start":
        await drv.start_charging()
    elif action == "stop":
        await drv.stop_charging()
    elif action == "set_power" and value is not None:
        await drv.set_power(int(value))
    elif action == "set_current" and value is not None:
        await drv.set_dynamic_current(float(value))


async def handle_live(req):
    return web.json_response(state["live"])

async def handle_devices_get(req):
    return web.json_response(_safe_devices())

async def handle_devices_post(req):
    try:
        device = await req.json()
        device["id"] = int(time.time() * 1000)
        device.setdefault("status", "online")
        device.setdefault("power", 0)
        device.setdefault("soc", None)
        state["devices"].append(device)
        _rebuild_drivers()
        await save_devices()
        add_log("ok", f"Apparaat toegevoegd: {device.get('name')}")
        await broadcast({"type": "devices", "data": _safe_devices()})
        return web.json_response({k: v for k, v in device.items()
                                   if k not in ("password",)}, status=201)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)

async def handle_device_patch(req):
    dev_id = int(req.match_info["id"])
    updates = await req.json()
    for i, d in enumerate(state["devices"]):
        if d.get("id") == dev_id:
            state["devices"][i].update(updates)
            # Re-init driver if connection params changed
            if any(k in updates for k in ("ip", "port", "brand", "protocol", "username", "password")):
                state["_drivers"].pop(dev_id, None)
                _rebuild_drivers()
            await save_devices()
            await broadcast({"type": "devices", "data": _safe_devices()})
            return web.json_response(state["devices"][i])
    return web.json_response({"error": "Not found"}, status=404)

async def handle_device_delete(req):
    dev_id = int(req.match_info["id"])
    before = len(state["devices"])
    # Close driver
    drv = state["_drivers"].pop(dev_id, None)
    if drv and hasattr(drv, "close"):
        try: await drv.close()
        except Exception: pass
    state["devices"] = [d for d in state["devices"] if d.get("id") != dev_id]
    if len(state["devices"]) < before:
        await save_devices()
        await broadcast({"type": "devices", "data": _safe_devices()})
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
    """Return relevant HA entities for the entity picker."""
    try:
        hdrs = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}
        async with aiohttp.ClientSession(headers=hdrs) as s:
            async with s.get(f"{HA_URL}/states", timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    return web.json_response([])
                entities = await r.json()
        relevant_prefixes = ("sensor.", "number.", "switch.", "climate.", "input_number.")
        result = [
            {
                "entity_id":     e["entity_id"],
                "state":         e.get("state", ""),
                "friendly_name": e.get("attributes", {}).get("friendly_name", e["entity_id"]),
                "unit":          e.get("attributes", {}).get("unit_of_measurement", ""),
            }
            for e in entities
            if any(e["entity_id"].startswith(p) for p in relevant_prefixes)
        ]
        return web.json_response(result)
    except Exception as e:
        log.debug(f"ha_entities: {e}")
        return web.json_response([])


# ── App ───────────────────────────────────────────────────────────────────────
async def on_startup(app):
    await load_data()
    add_log("ok", "EMS Energy Management System v2 gestart")
    asyncio.ensure_future(polling_loop())

async def on_shutdown(app):
    for drv in state["_drivers"].values():
        if drv and hasattr(drv, "close"):
            try: await drv.close()
            except Exception: pass
    await save_devices()
    await save_settings()

def build_app() -> web.Application:
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.router.add_get( "/ws",                   ws_handler)
    app.router.add_get( "/api/live",              handle_live)
    app.router.add_get( "/api/devices",           handle_devices_get)
    app.router.add_post("/api/devices",           handle_devices_post)
    app.router.add_patch("/api/devices/{id}",     handle_device_patch)
    app.router.add_delete("/api/devices/{id}",    handle_device_delete)
    app.router.add_get( "/api/settings",          handle_settings_get)
    app.router.add_post("/api/settings",          handle_settings_post)
    app.router.add_get( "/api/history",           handle_history)
    app.router.add_get( "/api/logs",              handle_logs)
    app.router.add_get( "/api/ha/entities",       handle_ha_entities)
    return app

if __name__ == "__main__":
    log.info("EMS backend starting on 127.0.0.1:8765")
    web.run_app(build_app(), host="127.0.0.1", port=8765, access_log=None)
