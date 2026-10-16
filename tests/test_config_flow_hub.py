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
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={})
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


async def test_hub_flow_cannot_connect_shows_error(hass, fake_client):
    fake_client.connect_fails = 99
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Matter", "url": "ws://localhost:5580/ws"}
    )
    assert result["type"] == "form" and result["errors"] == {"base": "cannot_connect"}


async def test_hub_flow_legacy_server_shows_error(hass, fake_client):
    async def legacy_info():
        return {"fabric_id": 1, "schema_version": 15, "sdk_version": "legacy"}
    fake_client.get_server_info = legacy_info  # type: ignore[method-assign]
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Matter", "url": "ws://localhost:5580/ws"}
    )
    assert result["type"] == "form" and result["errors"] == {"base": "legacy_server"}
