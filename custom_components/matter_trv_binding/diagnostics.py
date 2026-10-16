"""Diagnostics support for the Matter TRV Binding integration.

HA discovers this module automatically; it only needs the two pinned
``async_get_*_diagnostics`` coroutines. Every payload is passed through
``async_redact_data`` once, so any ``TO_REDACT`` key (at any nesting depth)
comes out redacted.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .const import HUB_UNIQUE_ID
from .hub import get_hub_entry

TO_REDACT = {"node_id", "fabric_id", "compressed_fabric_id", "url"}


def _hub_server_info(hass: HomeAssistant, entry: ConfigEntry) -> dict:
    """Server info of the hub connection (empty when no hub is loaded)."""
    hub = entry if entry.unique_id == HUB_UNIQUE_ID else get_hub_entry(hass)
    runtime = getattr(hub, "runtime_data", None) if hub is not None else None
    connection = getattr(runtime, "connection", None)
    return dict(getattr(connection, "server_info", None) or {})


def _payload(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Build the raw (unredacted) diagnostics payload for either entry kind."""
    runtime = getattr(entry, "runtime_data", None)
    if entry.unique_id == HUB_UNIQUE_ID or runtime is None:
        return {
            "entry_data": dict(entry.data),
            "options": dict(entry.options),
            "server_info": _hub_server_info(hass, entry),
        }
    data = runtime.coordinator.data
    return {
        "entry_data": dict(entry.data),
        "options": dict(entry.options),
        "sensor_ok": data.sensor_ok if data is not None else None,
        "trvs": {
            trv.device_id: {
                "name": trv.name,
                "node_id": trv.node_id,
                "status": trv.status.value,
                "healthy": trv.healthy,
            }
            for trv in (data.trvs.values() if data is not None else ())
        },
        "last_check": data.last_check.isoformat() if data and data.last_check else None,
        "server_info": _hub_server_info(hass, entry),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a hub or binding entry."""
    return async_redact_data(_payload(hass, entry), TO_REDACT)


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """Return the same redacted payload as the owning config entry."""
    return await async_get_config_entry_diagnostics(hass, entry)
