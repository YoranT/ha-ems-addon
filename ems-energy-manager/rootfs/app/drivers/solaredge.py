"""
SolarEdge driver — inverter, inline meter, battery
Uses the solaredge_modbus library (pip install solaredge-modbus)
Connects over Modbus TCP to the inverter. The meter and battery
are discovered automatically via the inverter's Modbus registers.
"""
import logging
import asyncio
from typing import Any

log = logging.getLogger("ems.solaredge")


def _run_sync(coro):
    """Run a coroutine or call sync depending on what solaredge_modbus gives us."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We are inside an async loop — run in a thread executor
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result(timeout=10)
        else:
            return loop.run_until_complete(coro)
    except Exception:
        import asyncio as _a
        return _a.run(coro)


class SolarEdgeInverterDriver:
    """
    Reads SolarEdge inverter AC power, DC power, status.
    Port is typically 1502 (newer firmware) or 502 (older).
    Modbus must be enabled on the inverter: SetApp → Communication → LAN → Modbus TCP.
    """
    name = "SolarEdge Omvormer"

    def __init__(self, device: dict):
        self.host = device.get("ip", "")
        self.port = int(device.get("port", 1502))
        self._inverter = None

    def _get_inverter(self):
        try:
            import solaredge_modbus
            if self._inverter is None:
                self._inverter = solaredge_modbus.Inverter(
                    host=self.host,
                    port=self.port,
                    timeout=5,
                    retries=2,
                )
            return self._inverter
        except ImportError:
            log.error("solaredge-modbus not installed")
            return None

    async def read(self) -> dict:
        """Returns dict with power (W), status."""
        try:
            inv = await asyncio.get_event_loop().run_in_executor(
                None, self._read_sync
            )
            return inv
        except Exception as e:
            log.debug(f"SolarEdge inverter read error: {e}")
            return {"status": "offline"}

    def _read_sync(self) -> dict:
        inv = self._get_inverter()
        if not inv:
            return {"status": "offline"}
        try:
            data = inv.read_all()
            if not data:
                return {"status": "offline"}

            # AC power with scale factor
            ac_power = data.get("power_ac", 0)
            ac_sf = data.get("power_ac_scale", 0)
            power_w = round(float(ac_power) * (10 ** int(ac_sf)))

            # Status mapping
            status_map = {
                1: "off", 2: "sleeping", 3: "starting", 4: "mppt",
                5: "throttled", 6: "shutting_down", 7: "fault", 8: "standby",
            }
            status_val = data.get("status", 0)
            inverter_status = status_map.get(status_val, "unknown")
            online = status_val in (3, 4, 5)

            # Daily energy production
            energy_wh = data.get("energy_total", 0)
            energy_sf = data.get("energy_total_scale", 0)
            energy_kwh = round(float(energy_wh) * (10 ** int(energy_sf)) / 1000, 2)

            return {
                "power":       power_w,
                "status":      "online" if online else "standby",
                "inverter_status": inverter_status,
                "energy_today_kwh": energy_kwh,
                "temperature": round(float(data.get("temperature", 0)) * (10 ** int(data.get("temperature_scale", -2))), 1),
                "voltage_ac":  round(float(data.get("voltage_ab", 0)) * (10 ** int(data.get("voltage_scale", -1))), 1),
            }
        except Exception as e:
            log.debug(f"SolarEdge inverter parse error: {e}")
            self._inverter = None  # force reconnect
            return {"status": "offline"}


class SolarEdgeMeterDriver:
    """
    Reads SolarEdge inline meter (import/export power).
    The meter is connected to the inverter via RS485 and accessible
    through the inverter's Modbus registers (offset 0 = meter 1).
    """
    name = "SolarEdge Inline Meter"

    def __init__(self, device: dict):
        self.host = device.get("ip", "")
        self.port = int(device.get("port", 1502))
        self.offset = int(device.get("modbus_offset", 0))  # 0=meter1, 1=meter2
        self._meter = None

    def _get_meter(self):
        try:
            import solaredge_modbus
            if self._meter is None:
                self._meter = solaredge_modbus.Meter(
                    host=self.host,
                    port=self.port,
                    timeout=5,
                    retries=2,
                    offset=self.offset,
                )
            return self._meter
        except ImportError:
            log.error("solaredge-modbus not installed")
            return None

    async def read(self) -> dict:
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._read_sync
            )
        except Exception as e:
            log.debug(f"SolarEdge meter read error: {e}")
            return {"status": "offline"}

    def _read_sync(self) -> dict:
        meter = self._get_meter()
        if not meter:
            return {"status": "offline"}
        try:
            data = meter.read_all()
            if not data:
                return {"status": "offline"}

            # AC power (positive = import, negative = export)
            power = data.get("power", 0)
            power_sf = data.get("power_scale", 0)
            power_w = round(float(power) * (10 ** int(power_sf)))

            # Energy counters
            export_wh = data.get("export_energy_active", 0)
            import_wh = data.get("import_energy_active", 0)
            e_sf = data.get("energy_active_scale", 0)

            return {
                "power":      power_w,
                "status":     "online",
                "exported_kwh": round(float(export_wh) * (10 ** int(e_sf)) / 1000, 2),
                "imported_kwh": round(float(import_wh) * (10 ** int(e_sf)) / 1000, 2),
                "voltage":    round(float(data.get("voltage_ln", 0)) * (10 ** int(data.get("voltage_scale", -1))), 1),
                "current":    round(float(data.get("current", 0)) * (10 ** int(data.get("current_scale", -2))), 2),
            }
        except Exception as e:
            log.debug(f"SolarEdge meter parse error: {e}")
            self._meter = None
            return {"status": "offline"}


class SolarEdgeBatteryDriver:
    """
    Reads SolarEdge-connected battery (e.g. LG Chem, BYD via StorEdge).
    The battery is discovered through the inverter's battery registers.
    """
    name = "SolarEdge Batterij"

    def __init__(self, device: dict):
        self.host = device.get("ip", "")
        self.port = int(device.get("port", 1502))
        self.offset = int(device.get("modbus_offset", 0))
        self._battery = None

    def _get_battery(self):
        try:
            import solaredge_modbus
            if self._battery is None:
                self._battery = solaredge_modbus.Battery(
                    host=self.host,
                    port=self.port,
                    timeout=5,
                    retries=2,
                    offset=self.offset,
                )
            return self._battery
        except ImportError:
            log.error("solaredge-modbus not installed")
            return None

    async def read(self) -> dict:
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._read_sync
            )
        except Exception as e:
            log.debug(f"SolarEdge battery read error: {e}")
            return {"status": "offline"}

    def _read_sync(self) -> dict:
        bat = self._get_battery()
        if not bat:
            return {"status": "offline"}
        try:
            data = bat.read_all()
            if not data:
                return {"status": "offline"}

            soc = round(float(data.get("state_of_charge", 0)), 1)

            # Power: positive = discharging, negative = charging (SE convention)
            power_w = round(float(data.get("average_power", 0)))

            status_map = {
                0: "offline", 1: "standby", 2: "init",
                3: "charge", 4: "discharge", 5: "fault", 6: "idle",
            }
            bat_status = data.get("status", 0)
            state_label = status_map.get(bat_status, "unknown")

            return {
                "power":     power_w,
                "soc":       soc,
                "status":    "online" if bat_status in (3, 4, 6) else "standby",
                "bat_state": state_label,
                "max_energy_kwh": round(float(data.get("max_energy", 0)) / 1000, 2),
                "temperature": round(float(data.get("average_temperature", 0)), 1),
            }
        except Exception as e:
            log.debug(f"SolarEdge battery parse error: {e}")
            self._battery = None
            return {"status": "offline"}
