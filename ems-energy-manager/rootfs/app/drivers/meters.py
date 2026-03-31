"""
HomeWizard P1 meter driver — local HTTP API (no cloud, no MQTT)
HomeWizard Wi-Fi P1 meter has a built-in local API on port 80.
Enable it in the HomeWizard Energy app: Settings → Meters → Wi-Fi P1 → Local API.

GET http://<ip>/api/v1/data  → all power readings
GET http://<ip>/api          → device info

Also included:
  - HomWizardEnergySocket (for smart plugs)
  - HomeAssistantEntityDriver (reads any HA sensor/number)
  - SessyP1Driver (Sessy P1 dongle which also has local REST API)
"""
import logging
import asyncio
import aiohttp
from typing import Any

log = logging.getLogger("ems.meters")


class HomeWizardP1Driver:
    """
    HomeWizard Wi-Fi P1 meter — local HTTP API.
    Enable local API in HomeWizard Energy app first.
    Returns net power (positive = import, negative = export).
    """
    name = "HomeWizard P1 Meter"

    def __init__(self, device: dict):
        self.host = device.get("ip", "").rstrip("/")
        self._base = f"http://{self.host}"
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=8)
            )
        return self._session

    async def read(self) -> dict:
        """Returns net power and energy counters."""
        try:
            session = await self._get_session()
            async with session.get(f"{self._base}/api/v1/data") as r:
                if r.status != 200:
                    return {"status": "offline"}
                data = await r.json()

            # Active power: positive = import from grid, negative = export to grid
            # HomeWizard uses active_power_w (total net power)
            power_w = round(float(data.get("active_power_w", 0)))

            # Per-phase breakdown
            p1 = data.get("active_power_l1_w", 0)
            p2 = data.get("active_power_l2_w", 0)
            p3 = data.get("active_power_l3_w", 0)

            # Energy counters
            import_kwh  = round(float(data.get("total_power_import_kwh", 0)), 3)
            export_kwh  = round(float(data.get("total_power_export_kwh", 0)), 3)
            import_t1   = round(float(data.get("total_power_import_t1_kwh", 0)), 3)
            import_t2   = round(float(data.get("total_power_import_t2_kwh", 0)), 3)
            export_t1   = round(float(data.get("total_power_export_t1_kwh", 0)), 3)
            export_t2   = round(float(data.get("total_power_export_t2_kwh", 0)), 3)

            # Gas if available
            gas_m3 = data.get("total_gas_m3")

            return {
                "power":       power_w,
                "status":      "online",
                "power_l1_w":  round(float(p1)),
                "power_l2_w":  round(float(p2)),
                "power_l3_w":  round(float(p3)),
                "import_kwh":  import_kwh,
                "export_kwh":  export_kwh,
                "import_t1_kwh": import_t1,
                "import_t2_kwh": import_t2,
                "export_t1_kwh": export_t1,
                "export_t2_kwh": export_t2,
                "gas_m3":      round(float(gas_m3), 3) if gas_m3 else None,
            }
        except aiohttp.ClientConnectorError:
            return {"status": "offline"}
        except Exception as e:
            log.debug(f"HomeWizard P1 read error: {e}")
            return {"status": "offline"}

    async def close(self):
        if self._session:
            await self._session.close()


class SessyP1Driver:
    """
    Sessy P1 dongle — local REST API.
    Same credentials as Sessy battery (sticker on dongle).
    GET http://sessy-p1-XXXX.local/api/v1/p1/status
    """
    name = "Sessy P1 Dongle"

    def __init__(self, device: dict):
        self.host     = device.get("ip", "").rstrip("/")
        self.username = device.get("username", "")
        self.password = device.get("password", "")
        self._base    = f"http://{self.host}"
        self._auth    = aiohttp.BasicAuth(self.username, self.password)
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                auth=self._auth,
                timeout=aiohttp.ClientTimeout(total=8)
            )
        return self._session

    async def read(self) -> dict:
        try:
            session = await self._get_session()
            async with session.get(f"{self._base}/api/v1/p1/status") as r:
                if r.status != 200:
                    return {"status": "offline"}
                data = await r.json()

            # Sessy P1 returns net_power (positive = import, negative = export)
            power_w = round(float(data.get("net_power", 0)))

            return {
                "power":      power_w,
                "status":     "online",
                "import_kwh": round(float(data.get("import_energy", 0)) / 1000, 3),
                "export_kwh": round(float(data.get("export_energy", 0)) / 1000, 3),
                "p1_state":   data.get("p1_state", ""),
            }
        except aiohttp.ClientConnectorError:
            return {"status": "offline"}
        except Exception as e:
            log.debug(f"Sessy P1 read error: {e}")
            return {"status": "offline"}

    async def close(self):
        if self._session:
            await self._session.close()


class HomeAssistantEntityDriver:
    """
    Reads power and SoC from Home Assistant sensor entities via the HA REST API.
    This is the fallback for devices that already have a HA integration
    (e.g. Victron via VRM, Fronius via HA integration, DSMR via P1 Reader, etc).

    The HA Supervisor token is passed automatically from the add-on environment.
    """
    name = "Home Assistant Entiteit"

    def __init__(self, device: dict, ha_url: str, ha_token: str):
        self.ha_url        = ha_url.rstrip("/")
        self.ha_token      = ha_token
        self.power_entity  = device.get("ha_entity_power", "")
        self.soc_entity    = device.get("ha_entity_soc", "")
        self._session: aiohttp.ClientSession | None = None

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.ha_token}",
            "Content-Type":  "application/json",
        }

    async def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=8)
            )
        return self._session

    async def _get_state(self, entity_id: str) -> Any:
        if not entity_id:
            return None
        try:
            session = await self._get_session()
            async with session.get(
                f"{self.ha_url}/states/{entity_id}"
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    state = data.get("state", "unavailable")
                    if state in ("unavailable", "unknown", ""):
                        return None
                    return float(state)
        except Exception as e:
            log.debug(f"HA entity read {entity_id}: {e}")
        return None

    async def read(self) -> dict:
        power = await self._get_state(self.power_entity)
        soc   = await self._get_state(self.soc_entity)

        if power is None and soc is None:
            return {"status": "offline"}

        result = {"status": "online"}
        if power is not None:
            result["power"] = round(power)
        if soc is not None:
            result["soc"] = round(soc, 1)

        return result

    async def close(self):
        if self._session:
            await self._session.close()
