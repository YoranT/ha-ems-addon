"""
Easee EV charger driver — Easee Cloud REST API
Uses https://api.easee.com (no local API available for Easee).
Authentication: OAuth2 with 1-hour access token + refresh token.

Required device config:
  username:   Easee account email or phone (+31xxxxxxxxx)
  password:   Easee account password
  charger_id: Easee charger serial number (e.g. EH123456) — optional,
              will auto-discover if not set

API endpoints used:
  POST /api/accounts/token           → get access/refresh tokens
  POST /api/accounts/refresh_token   → refresh access token
  GET  /api/chargers                 → list chargers
  GET  /api/chargers/{id}/state      → charger state (power, status, SoC)
  POST /api/chargers/{id}/commands/start_charging
  POST /api/chargers/{id}/commands/stop_charging
  POST /api/chargers/{id}/settings   → set dynamic current limit (smart charging)
"""
import logging
import asyncio
import time
import aiohttp
from typing import Any

log = logging.getLogger("ems.easee")

EASEE_BASE = "https://api.easee.com"

# chargerOpMode values
OP_MODE = {
    0: "offline",
    1: "disconnected",
    2: "awaiting_start",
    3: "charging",
    4: "completed",
    5: "error",
    6: "ready_to_charge",
}


class EaseeChargerDriver:
    """
    Controls an Easee EV charger via the Easee Cloud REST API.
    """
    name = "Easee Laadpaal"

    def __init__(self, device: dict):
        self.username   = device.get("username", "")
        self.password   = device.get("password", "")
        self.charger_id = device.get("charger_id", "")  # auto-discovered if empty
        self._access_token  = None
        self._refresh_token = None
        self._token_expires = 0
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self._session

    async def _ensure_token(self) -> bool:
        """Get or refresh OAuth2 token."""
        now = time.time()
        if self._access_token and now < self._token_expires - 60:
            return True  # still valid

        session = await self._get_session()

        # Try refresh first
        if self._refresh_token:
            try:
                async with session.post(
                    f"{EASEE_BASE}/api/accounts/refresh_token",
                    json={"accessToken": self._access_token,
                          "refreshToken": self._refresh_token},
                    headers={"Content-Type": "application/json"},
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        self._access_token  = data["accessToken"]
                        self._refresh_token = data["refreshToken"]
                        self._token_expires = now + data.get("expiresIn", 3600)
                        log.debug("Easee token refreshed")
                        return True
            except Exception as e:
                log.debug(f"Easee refresh failed: {e}")

        # Full login
        try:
            async with session.post(
                f"{EASEE_BASE}/api/accounts/token",
                json={"userName": self.username, "password": self.password},
                headers={"Content-Type": "application/json"},
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    self._access_token  = data["accessToken"]
                    self._refresh_token = data["refreshToken"]
                    self._token_expires = now + data.get("expiresIn", 3600)
                    log.info("Easee login successful")
                    return True
                else:
                    body = await r.text()
                    log.error(f"Easee login failed {r.status}: {body}")
                    return False
        except Exception as e:
            log.error(f"Easee login error: {e}")
            return False

    async def _headers(self) -> dict:
        await self._ensure_token()
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        }

    async def _get_charger_id(self) -> str:
        """Auto-discover first charger ID if not configured."""
        if self.charger_id:
            return self.charger_id
        try:
            session = await self._get_session()
            async with session.get(
                f"{EASEE_BASE}/api/chargers",
                headers=await self._headers()
            ) as r:
                if r.status == 200:
                    chargers = await r.json()
                    if chargers:
                        self.charger_id = chargers[0].get("id", "")
                        log.info(f"Easee auto-discovered charger: {self.charger_id}")
        except Exception as e:
            log.debug(f"Easee get_chargers error: {e}")
        return self.charger_id

    async def read(self) -> dict:
        """Poll charger state."""
        try:
            if not await self._ensure_token():
                return {"status": "offline", "error": "auth_failed"}

            charger_id = await self._get_charger_id()
            if not charger_id:
                return {"status": "offline", "error": "no_charger_found"}

            session = await self._get_session()
            async with session.get(
                f"{EASEE_BASE}/api/chargers/{charger_id}/state",
                headers=await self._headers()
            ) as r:
                if r.status != 200:
                    return {"status": "offline"}
                data = await r.json()

            op_mode = data.get("chargerOpMode", 0)
            mode_str = OP_MODE.get(op_mode, "unknown")

            # Power in kW → W
            power_kw = data.get("totalPower", 0.0) or 0.0
            power_w  = round(float(power_kw) * 1000)

            # Session energy in kWh
            session_kwh = data.get("sessionEnergy", 0.0) or 0.0

            # Car connected?
            car_connected = op_mode in (2, 3, 4, 6)

            # SoC if available (not all cars report this)
            soc = data.get("inCarChargeState")

            return {
                "power":        power_w,
                "soc":          round(float(soc), 1) if soc else None,
                "status":       "online" if op_mode != 0 else "offline",
                "mode":         mode_str,
                "car_connected": car_connected,
                "is_charging":  op_mode == 3,
                "session_kwh":  round(float(session_kwh), 2),
                "charger_id":   charger_id,
            }
        except Exception as e:
            log.debug(f"Easee read error: {e}")
            return {"status": "offline"}

    async def start_charging(self) -> bool:
        """Start charging session."""
        charger_id = await self._get_charger_id()
        if not charger_id:
            return False
        try:
            session = await self._get_session()
            async with session.post(
                f"{EASEE_BASE}/api/chargers/{charger_id}/commands/start_charging",
                headers=await self._headers()
            ) as r:
                ok = r.status in (200, 202)
                if ok:
                    log.info(f"Easee {charger_id} charging started")
                return ok
        except Exception as e:
            log.debug(f"Easee start_charging error: {e}")
            return False

    async def stop_charging(self) -> bool:
        """Stop charging session."""
        charger_id = await self._get_charger_id()
        if not charger_id:
            return False
        try:
            session = await self._get_session()
            async with session.post(
                f"{EASEE_BASE}/api/chargers/{charger_id}/commands/stop_charging",
                headers=await self._headers()
            ) as r:
                ok = r.status in (200, 202)
                if ok:
                    log.info(f"Easee {charger_id} charging stopped")
                return ok
        except Exception as e:
            log.debug(f"Easee stop_charging error: {e}")
            return False

    async def set_dynamic_current(self, amps: float) -> bool:
        """
        Set the dynamic charging current limit (smart charging).
        amps: 0–32A (minimum 6A to charge, 0 pauses charging).
        """
        charger_id = await self._get_charger_id()
        if not charger_id:
            return False

        if amps < 6:
            return await self.stop_charging()

        try:
            session = await self._get_session()
            async with session.post(
                f"{EASEE_BASE}/api/chargers/{charger_id}/settings",
                json={"dynamicChargerCurrent": amps},
                headers=await self._headers(),
            ) as r:
                ok = r.status in (200, 202)
                if ok:
                    log.info(f"Easee {charger_id} current set to {amps}A")
                return ok
        except Exception as e:
            log.debug(f"Easee set_current error: {e}")
            return False

    async def close(self):
        if self._session:
            await self._session.close()
