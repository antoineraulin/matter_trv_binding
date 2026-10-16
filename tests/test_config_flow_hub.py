from matter_server.client.exceptions import ServerVersionTooOld
from pytest_homeassistant_custom_component.common import MockConfigEntry, patch

from custom_components.matter_trv_binding.const import DOMAIN


async def test_hub_flow_creates_entry(hass, fake_client):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["step_id"] == "hub"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Matter", "url": "ws://localhost:5580/ws"}
    )
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert result["title"] == "Matter"
    assert result["data"]["url"] == "ws://localhost:5580/ws"


async def test_hub_flow_duplicate_aborts(hass, fake_client):
    entry = MockConfigEntry(domain=DOMAIN, data={"name": "Matter", "url": "ws://x"}, unique_id="hub")
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    # With a hub already configured the flow now routes to the binding step, alive.
    assert result["step_id"] == "binding"


async def test_hub_flow_cannot_connect_shows_error(hass, fake_client):
    fake_client.connect_fails = 99
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Matter", "url": "ws://localhost:5580/ws"}
    )
    assert result["type"] == "form" and result["errors"] == {"base": "cannot_connect"}
    assert result["description_placeholders"]["url"] == "ws://localhost:5580/ws"


async def test_hub_flow_legacy_server_shows_error(hass, fake_client):
    async def too_old():
        raise ServerVersionTooOld("schema 12 below client floor 13")
    fake_client.connect = too_old  # type: ignore[method-assign]
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Matter", "url": "ws://localhost:5580/ws"}
    )
    assert result["type"] == "form" and result["errors"] == {"base": "legacy_server"}


async def test_hub_flow_low_schema_still_creates_entry(hass, fake_client):
    """Regression: a real matterjs-server 9.2.0 reports a low schema_version but
    the client accepts it, so the flow must not reject it."""
    async def low_schema_info():
        return {"fabric_id": 1, "schema_version": 12, "sdk_version": "9.2.0"}
    fake_client.get_server_info = low_schema_info  # type: ignore[method-assign]
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Matter", "url": "ws://localhost:5580/ws"}
    )
    assert result["type"] == "create_entry"
