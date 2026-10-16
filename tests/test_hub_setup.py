from homeassistant.config_entries import ConfigEntryState
from matter_server.client.exceptions import ServerVersionTooOld
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.matter_trv_binding.const import DOMAIN, DEFAULT_URL
from custom_components.matter_trv_binding.hub import get_hub_entry


async def test_hub_setup_and_unload(hass, fake_client):
    entry = MockConfigEntry(domain=DOMAIN, data={"name": "Matter", "url": DEFAULT_URL}, unique_id="hub")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hub = get_hub_entry(hass)
    assert hub.runtime_data.connection  # type: ignore[attr-defined]
    assert fake_client.connected
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert fake_client.connected is False


async def test_hub_setup_not_ready_when_server_down(hass, fake_client):
    fake_client.connect_fails = 99
    entry = MockConfigEntry(domain=DOMAIN, data={"name": "Matter", "url": DEFAULT_URL}, unique_id="hub")
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_hub_setup_fails_permanently_when_server_too_old(hass, fake_client):
    async def too_old():
        raise ServerVersionTooOld("schema 12 below client floor 13")
    fake_client.connect = too_old  # type: ignore[method-assign]
    entry = MockConfigEntry(domain=DOMAIN, data={"name": "Matter", "url": DEFAULT_URL}, unique_id="hub")
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR  # permanent, not a retry
