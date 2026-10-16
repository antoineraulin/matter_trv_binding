"""Hub config entry runtime data and connection helpers."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN, HUB_UNIQUE_ID
from .matter import client as matter_client
from .matter.client import MatterConnection, MatterConnectionError


@dataclass
class HubRuntimeData:
    """Runtime state shared by every entity of the hub entry."""

    connection: MatterConnection
    repair_counts: dict[str, int]  # binding entry_id -> cumulative repair counter


def get_hub_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """Return the loaded (or retrying) hub entry, if any."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.unique_id == HUB_UNIQUE_ID and entry.state in (
            ConfigEntryState.LOADED,
            ConfigEntryState.SETUP_RETRY,
        ):
            return entry
    return None


async def async_create_connection(hass: HomeAssistant, url: str) -> MatterConnection:
    """Build and connect a MatterConnection, raising ConfigEntryNotReady on failure."""
    # Resolve _create_client through the module so tests can patch it after import.
    connection = MatterConnection(
        url, client_factory=lambda url: matter_client._create_client(hass, url)
    )
    try:
        await connection.async_connect()
    except MatterConnectionError as err:
        raise ConfigEntryNotReady(f"cannot connect to {url}") from err
    return connection
