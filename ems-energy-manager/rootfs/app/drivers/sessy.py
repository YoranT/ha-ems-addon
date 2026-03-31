"""
Sessy battery driver — local REST API
Sessy exposes a local HTTP REST API on http://sessy-XXXX.local (or IP).
Username + password are on the sticker on the dongle.
No MQTT needed — pure HTTP polling + control.

Key endpoints:
  GET  /api/v1/power/status         → SoC, power, system_state
  GET  /api/v1/power/active_strategy → current strategy
  POST /api/v1/power/active_strategy → set strategy
  POST /api/v1/power/setpoint        → set charge/discharge power (W)
                                       negative = charge, positive = discharge
                                       Only works in POWER_STRATEGY_API mode!
"""
import logging
import asyncio
import aiohttp
from typing import Any

log = logging.getLogger("ems.sessy")

SESSY_STRATEGIES = {
    "HOME_SMART":   "POWER_STRATEGY_NOM",     # Normal Operation Mode: Sessy controls itself
    "SELF_USE":     "POWER_STRATEGY_SELF_USE", # Self-consumption priority
    "API":          "POWER_STRATEGY_API",      # Manual API setpoint control
    "IDLE":         "POWER_STRATEGY_IDLE",     # Battery idle, no charge/discharge
}

SESSY_SYSTEM_STATES = {
    "SYSTEM_STATE_RUNNING_SAFE":                "online",
    "SYSTEM_STATE_STANDBY":                     "standby",
    "SYSTEM_STATE_INIT":                        "standby",
    "SYSTEM_STATE_WAIT_FOR_PERIPHERALS":        "standby",
    "SYSTEM_STATE_DISCONNECT":                  "offline",
    "SYSTEM_STATE_RECONNECT":                   "standby",
    "SYSTEM_STATE_WAITING_FOR_SAFE_SITUATION":  "standby",
    "SYSTEM_STATE_WAITING_IN_SAFE_SITUATION":   "standby",
    "SYSTEM_STATE_OVERRIDE_OVERFREQUENCY":      "online",
    "SYSTEM_STATE_OVERRIDE_UNDERFREQUENCY":     "online",
}


class SessyBatteryDriver:
    """
    Reads and controls a Sessy home battery over its local REST API.
    host:     IP address or hostname (sessy-XXXX.local)
    username: from sticker on dongle
    password: from sticker on dongle
    """
    name = "Sessy Batterij"

    def __init__(self, device: dict):
        self.host     = device.get("ip", "").rstrip("/")
        self.username = device.get("username", "")
        self.password = device.get("password", "")
        self._base    = f"http://{self.host}"
        self._auth    = aiohttp.BasicAuth(self.username, self.password)
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                auth=self._auth,
                timeout=aiohttp.ClientTimeout(total=8),
            )
        return self._session

    async def read(self) -> dict:
        """Poll Sessy power status and return normalized values."""
        try:
            session = await self._get_session()
            async with session.get(f"{self._base}/api/v1/power/status") as r:
                if r.status != 200:
                    return {"status": "offline"}
                data = await r.json()

            sessy = data.get("sessy", {})
            soc_raw = sessy.get("state_of_charge", 0)
            soc = round(float(soc_raw) * 100, 1)  # 0.0–1.0 → 0–100%

            power_w = round(float(sessy.get("power", 0)))
            # Sessy convention: positive = delivering to grid (discharge), negative = charging

            system_state = sessy.get("system_state", "SYSTEM_STATE_STANDBY")
            status = SESSY_SYSTEM_STATES.get(system_state, "standby")

            return {
                "power":        power_w,
                "soc":          soc,
                "status":       status,
                "system_state": system_state,
            }
        except aiohttp.ClientConnectorError:
            return {"status": "offline"}
        except Exception as e:
            log.debug(f"Sessy read error: {e}")
            return {"status": "offline"}

    async def set_strategy(self, strategy_key: str) -> bool:
        """
        Set the Sessy power strategy.
        strategy_key: one of HOME_SMART, SELF_USE, API, IDLE
        """
        strategy_val = SESSY_STRATEGIES.get(strategy_key, strategy_key)
        try:
            session = await self._get_session()
            async with session.post(
                f"{self._base}/api/v1/power/active_strategy",
                json={"strategy": strategy_val},
            ) as r:
                ok = r.status == 200
                if ok:
                    log.info(f"Sessy strategy set to {strategy_val}")
                return ok
        except Exception as e:
            log.debug(f"Sessy set_strategy error: {e}")
            return False

    async def set_power(self, watts: int) -> bool:
        """
        Set charge/discharge power. Requires strategy = POWER_STRATEGY_API.
        watts > 0: discharge (deliver to home/grid)
        watts < 0: charge (draw from grid/solar)
        watts = 0: idle
        """
        try:
            # First make sure we are in API mode
            await self.set_strategy("API")
            session = await self._get_session()
            async with session.post(
                f"{self._base}/api/v1/power/setpoint",
                json={"setpoint": int(watts)},
            ) as r:
                ok = r.status == 200
                if ok:
                    log.info(f"Sessy power setpoint: {watts}W")
                return ok
        except Exception as e:
            log.debug(f"Sessy set_power error: {e}")
            return False

    async def close(self):
        if self._session:
            await self._session.close()
