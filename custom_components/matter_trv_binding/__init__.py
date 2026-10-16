"""Matter TRV Binding integration entry points."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry, ConfigEntryError
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, HUB_UNIQUE_ID, PLATFORMS
from .coordinator import BindingCoordinator
from .hub import HubRuntimeData, async_create_connection, get_hub_entry
from .matter.client import MatterServerTooOldError
from .services import SERVICE_REPAIR, async_register_services


@dataclass
class BindingRuntimeData:
    """Runtime state of one sensor->TRV binding entry."""

    coordinator: BindingCoordinator


type MatterTrvBindingConfigEntry = ConfigEntry[HubRuntimeData | BindingRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the hub or a binding entry."""
    if entry.unique_id == HUB_UNIQUE_ID:
        try:
            connection = await async_create_connection(hass, entry.data["url"])
        except MatterServerTooOldError as err:
            # Permanent: retrying cannot age the server forward.
            raise ConfigEntryError(
                "The Matter server is too old for the installed client; "
                "update to the Matter Server add-on 9.x or newer."
            ) from err
        entry.runtime_data = HubRuntimeData(connection=connection, repair_counts={})
        await async_register_services(hass)
        entry.async_on_unload(
            lambda: hass.services.async_remove(DOMAIN, SERVICE_REPAIR)
        )
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
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Matter Binding — {entry.data['label']}",
    )
    # Link by device-entry id, not the deprecated (domain, identifier) tuple.
    # HA 2026.2's async_get_or_create has no via_device_id kwarg yet, so set it
    # through async_update_device (the same route core's bluetooth uses).
    sensor_entry = registry.async_get(entry.data["sensor_device_id"])
    if sensor_entry is not None:
        registry.async_update_device(device.id, via_device_id=sensor_entry.id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Tear down an entry."""
    if entry.unique_id == HUB_UNIQUE_ID:
        await entry.runtime_data.connection.async_close()
        return True
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
