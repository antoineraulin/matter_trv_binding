"""Matter TRV Binding integration entry points."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, HUB_UNIQUE_ID, PLATFORMS
from .coordinator import BindingCoordinator
from .hub import HubRuntimeData, async_create_connection, get_hub_entry


@dataclass
class BindingRuntimeData:
    """Runtime state of one sensor->TRV binding entry."""

    coordinator: BindingCoordinator


type MatterTrvBindingConfigEntry = ConfigEntry[HubRuntimeData | BindingRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the hub or a binding entry."""
    if entry.unique_id == HUB_UNIQUE_ID:
        connection = await async_create_connection(hass, entry.data["url"])
        entry.runtime_data = HubRuntimeData(connection=connection, repair_counts={})
        return True

    hub = get_hub_entry(hass)
    if hub is None or getattr(hub, "runtime_data", None) is None:
        raise ConfigEntryNotReady("Matter TRV Binding hub is not set up")

    coordinator = BindingCoordinator(hass, entry, hub)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = BindingRuntimeData(coordinator=coordinator)
    _async_create_binding_device(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _async_create_binding_device(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Create/refresh the binding device, linked to the sensor's Matter device."""
    registry = dr.async_get(hass)
    sensor_entry = registry.async_get(entry.data["sensor_device_id"])
    via_device: tuple[str, str] | None = None
    if sensor_entry is not None:
        via_device = next(
            (ident for ident in sensor_entry.identifiers if ident[0] == "matter"), None
        )
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Matter Binding — {entry.data['label']}",
        via_device=via_device,
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Tear down an entry."""
    if entry.unique_id == HUB_UNIQUE_ID:
        await entry.runtime_data.connection.async_close()
        return True
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
