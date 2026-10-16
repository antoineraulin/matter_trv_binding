"""Services of the Matter TRV Binding integration."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN

SERVICE_REPAIR = "repair"

_REPAIR_SCHEMA = vol.Schema({vol.Required(ATTR_DEVICE_ID): vol.Any(str, [str])})


async def async_register_services(hass: HomeAssistant) -> None:
    """Register the repair service."""
    hass.services.async_register(
        DOMAIN, SERVICE_REPAIR, _async_handle_repair, schema=_REPAIR_SCHEMA
    )


async def _async_handle_repair(call: ServiceCall) -> None:
    """Run one verify+repair cycle for every targeted binding device."""
    device_ids = call.data[ATTR_DEVICE_ID]
    if isinstance(device_ids, str):
        device_ids = [device_ids]

    registry = dr.async_get(call.hass)
    for device_id in device_ids:
        device = registry.async_get(device_id)
        if device is None:
            continue  # unknown device id: ignore
        for identifier in device.identifiers:
            if identifier[0] != DOMAIN:
                continue
            entry = call.hass.config_entries.async_get_entry(identifier[1])
            if (
                entry is None
                or entry.state is not ConfigEntryState.LOADED
                or getattr(entry, "runtime_data", None) is None
            ):
                continue
            await entry.runtime_data.coordinator.async_check_and_repair()
