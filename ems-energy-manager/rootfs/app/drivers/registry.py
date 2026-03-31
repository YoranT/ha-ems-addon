"""
EMS Driver Registry
Maps device type + brand to the appropriate driver class.
Each driver exposes:  async read() -> dict
Control drivers also: async set_power(W), start/stop, etc.
"""
import logging
from typing import Optional, Any

log = logging.getLogger("ems.registry")


def get_driver(device: dict, ha_url: str = "", ha_token: str = "") -> Optional[Any]:
    """
    Instantiate and return the right driver for a device dict.
    Returns None if no driver is available (UI-only device).
    """
    dtype  = device.get("type", "")
    brand  = device.get("brand", "")
    proto  = device.get("protocol", "")

    # ── SolarEdge ─────────────────────────────────────────────────────────────
    if brand == "SolarEdge":
        if dtype == "Zonnepanelen" or dtype == "Omvormer":
            from drivers.solaredge import SolarEdgeInverterDriver
            return SolarEdgeInverterDriver(device)

        if dtype == "Net / Slimme meter":
            from drivers.solaredge import SolarEdgeMeterDriver
            return SolarEdgeMeterDriver(device)

        if dtype == "Thuisbatterij":
            from drivers.solaredge import SolarEdgeBatteryDriver
            return SolarEdgeBatteryDriver(device)

    # ── Sessy battery ─────────────────────────────────────────────────────────
    if brand == "Sessy" and dtype == "Thuisbatterij":
        from drivers.sessy import SessyBatteryDriver
        return SessyBatteryDriver(device)

    # ── Sessy P1 meter ────────────────────────────────────────────────────────
    if brand == "Sessy" and dtype == "Net / Slimme meter":
        from drivers.meters import SessyP1Driver
        return SessyP1Driver(device)

    # ── HomeWizard P1 ─────────────────────────────────────────────────────────
    if brand == "HomeWizard" and dtype == "Net / Slimme meter":
        from drivers.meters import HomeWizardP1Driver
        return HomeWizardP1Driver(device)

    # ── Easee EV charger ──────────────────────────────────────────────────────
    if brand == "Easee" and dtype == "Laadpaal (EV)":
        from drivers.easee import EaseeChargerDriver
        return EaseeChargerDriver(device)

    # ── Home Assistant entity (fallback for anything with HA entities) ─────────
    if proto == "Home Assistant Entiteit" or (
        device.get("ha_entity_power") or device.get("ha_entity_soc")
    ):
        from drivers.meters import HomeAssistantEntityDriver
        return HomeAssistantEntityDriver(device, ha_url, ha_token)

    # ── Generic Modbus TCP (Growatt, Fronius, Victron, Huawei, etc.) ──────────
    if proto == "Modbus TCP" and device.get("ip"):
        from drivers.modbus_generic import GenericModbusDriver
        return GenericModbusDriver(device)

    log.debug(f"No driver for device '{device.get('name')}' (type={dtype}, brand={brand}, proto={proto})")
    return None


async def get_control_driver(device: dict) -> Optional[Any]:
    """Return a driver that supports control commands (set_power, start/stop)."""
    brand = device.get("brand", "")
    dtype = device.get("type", "")

    if brand == "Sessy" and dtype == "Thuisbatterij":
        from drivers.sessy import SessyBatteryDriver
        return SessyBatteryDriver(device)

    if brand == "Easee" and dtype == "Laadpaal (EV)":
        from drivers.easee import EaseeChargerDriver
        return EaseeChargerDriver(device)

    return None
