"""Matter TRV Binding integration entry points."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import HUB_UNIQUE_ID
from .hub import HubRuntimeData, async_create_connection

# Task 10 extends this union with BindingRuntimeData for binding entries.
type MatterTrvBindingConfigEntry = ConfigEntry[HubRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the hub or a binding entry."""
    if entry.unique_id == HUB_UNIQUE_ID:
        connection = await async_create_connection(hass, entry.data["url"])
        entry.runtime_data = HubRuntimeData(connection=connection, repair_counts={})
        return True
    # Task 10 implements the binding branch.
    return False


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Tear down an entry."""
    if entry.unique_id == HUB_UNIQUE_ID:
        await entry.runtime_data.connection.async_close()
    return True
