"""Tests for the repairs fix flow."""
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.matter_trv_binding.const import DOMAIN
from custom_components.matter_trv_binding.repairs import async_create_fix_flow


async def test_binding_repair_flow_confirm_removes_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"label": "B", "sensor_device_id": "s", "trv_device_ids": []},
        unique_id="s",
    )
    entry.add_to_hass(hass)
    flow = await async_create_fix_flow(
        hass, f"device_missing_{entry.entry_id}", {"entry_id": entry.entry_id}
    )
    assert flow is not None
    flow.hass = hass  # direct-step tests must set hass (the manager normally does)
    result = await flow.async_step_init()
    assert result["type"] == "form"
    assert any(str(key) == "confirm" for key in result["data_schema"].schema)
    assert result["description_placeholders"]["binding_name"] == entry.title
    await flow.async_step_init({"confirm": True})
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(entry.entry_id) is None


async def test_binding_repair_flow_only_removes_on_confirm(hass):
    """HA feeds the issue data to the first step as user_input (see
    RepairsFlowManager.async_create_flow); it must not delete the binding."""
    entry = MockConfigEntry(domain=DOMAIN, data={"label": "B"}, unique_id="s")
    entry.add_to_hass(hass)
    issue_id = f"device_missing_{entry.entry_id}"
    flow = await async_create_fix_flow(hass, issue_id, {"entry_id": entry.entry_id})
    flow.hass = hass
    result = await flow.async_step_init({"issue_id": issue_id})
    assert result["type"] == "form"
    assert hass.config_entries.async_get_entry(entry.entry_id) is not None


async def test_create_fix_flow_ignores_other_issues(hass):
    result = await async_create_fix_flow(hass, "repair_failed_x", {"entry_id": "x"})
    assert result is None
