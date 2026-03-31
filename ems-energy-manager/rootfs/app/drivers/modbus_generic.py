"""
Generic Modbus TCP driver
Handles known register maps for: Huawei, Fronius, Growatt, Victron, Solax, BYD, Eastron.
Falls back to SunSpec model 103 if brand is unknown.
"""
import logging
import asyncio
from typing import Any

log = logging.getLogger("ems.modbus")

# Register maps per brand
# Each entry: power_reg, power_scale (multiplier), slave_id, soc_reg (optional)
REGISTER_MAPS = {
    "Huawei": {
        # Huawei SUN2000 inverter + LUNA2000 battery
        "type_detect": "inverter",
        "power_reg": 32064, "power_scale": 1,    "slave": 0,
        "soc_reg":   37004, "soc_scale":   0.1,  # battery SoC
        "port":      6607,
        "signed":    True,
    },
    "Fronius": {
        "power_reg": 40083, "power_scale": 0.1,  "slave": 1, "port": 502,
        "soc_reg":   None,  "signed": True,
    },
    "Growatt": {
        "power_reg": 35,    "power_scale": 0.1,  "slave": 1, "port": 502,
        "soc_reg":   103,   "soc_scale":   1,    "signed": False,
    },
    "Victron": {
        # Victron Cerbo GX / Venus OS Modbus-TCP
        "power_reg": 820,   "power_scale": 1,    "slave": 100, "port": 502,
        "soc_reg":   843,   "soc_scale":   0.1,  "signed": True,
    },
    "Solax": {
        "power_reg": 70,    "power_scale": 1,    "slave": 1, "port": 502,
        "soc_reg":   103,   "soc_scale":   1,    "signed": True,
    },
    "BYD": {
        "power_reg": 30775, "power_scale": 1,    "slave": 1, "port": 502,
        "soc_reg":   30845, "soc_scale":   1,    "signed": True,
    },
    "Eastron": {
        # Eastron SDM630 three-phase power meter
        "power_reg": 52,    "power_scale": 1,    "slave": 1, "port": 502,
        "float32":   True,  "signed": True,
    },
    "Nibe": {
        "power_reg": 40079, "power_scale": 0.1,  "slave": 1, "port": 502,
        "signed": True,
    },
    # SunSpec fallback (most modern inverters)
    "SunSpec": {
        "power_reg": 40083, "power_scale": 1,    "slave": 1, "port": 502,
        "signed": True,
    },
}


class GenericModbusDriver:
    name = "Modbus Apparaat"

    def __init__(self, device: dict):
        self.device = device
        self.host   = device.get("ip", "")
        self.brand  = device.get("brand", "SunSpec")
        reg_map     = REGISTER_MAPS.get(self.brand, REGISTER_MAPS["SunSpec"])
        self.port   = int(device.get("port") or reg_map.get("port", 502))
        self.reg    = reg_map
        self._client = None

    async def read(self) -> dict:
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._read_sync
            )
        except Exception as e:
            log.debug(f"Modbus read {self.device.get('name')}: {e}")
            return {"status": "offline"}

    def _read_sync(self) -> dict:
        from pymodbus.client import ModbusTcpClient
        client = ModbusTcpClient(
            host=self.host,
            port=self.port,
            timeout=3,
        )
        try:
            if not client.connect():
                return {"status": "offline"}

            r = client.read_holding_registers(
                self.reg["power_reg"], count=2,
                slave=self.reg.get("slave", 1)
            )
            if r.isError():
                return {"status": "offline"}

            raw = r.registers[0]
            if self.reg.get("float32") and len(r.registers) >= 2:
                import struct
                raw_bytes = struct.pack(">HH", r.registers[0], r.registers[1])
                power_w = round(struct.unpack(">f", raw_bytes)[0])
            else:
                if self.reg.get("signed", True) and raw > 32767:
                    raw -= 65536
                power_w = round(raw * self.reg.get("power_scale", 1))

            result = {"power": power_w, "status": "online"}

            # Read SoC if defined
            soc_reg = self.reg.get("soc_reg")
            if soc_reg:
                r2 = client.read_holding_registers(
                    soc_reg, count=1, slave=self.reg.get("slave", 1)
                )
                if not r2.isError():
                    soc_raw = r2.registers[0]
                    result["soc"] = round(soc_raw * self.reg.get("soc_scale", 1), 1)

            return result

        except Exception as e:
            log.debug(f"Modbus sync error {self.device.get('name')}: {e}")
            return {"status": "offline"}
        finally:
            client.close()
