from homeassistant.components.diagnostics import REDACTED

from custom_components.matter_trv_binding.const import DOMAIN
from custom_components.matter_trv_binding.diagnostics import TO_REDACT, async_get_config_entry_diagnostics
from tests.test_coordinator import seed_healthy


async def test_diagnostics_redacts_node_ids(hass, fake_client, matter_devices):
    seed_healthy(fake_client)
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    sensor = matter_devices.create("Capetre Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    hub = MockConfigEntry(domain=DOMAIN, data={"name": "M", "url": "ws://x"}, unique_id="hub")
    hub.add_to_hass(hass)
    binding = MockConfigEntry(domain=DOMAIN, data={"label": "Bureau", "sensor_device_id": sensor.id, "trv_device_ids": [trv.id]}, unique_id=sensor.id)
    await hass.config_entries.async_setup(hub.entry_id)
    binding.add_to_hass(hass)
    await hass.config_entries.async_setup(binding.entry_id)
    await hass.async_block_till_done()
    diag = await async_get_config_entry_diagnostics(hass, binding)
    assert diag["trvs"][trv.id]["node_id"] == REDACTED
    assert "fabric" not in str(diag).lower() or REDACTED
    assert diag["server_info"]["fabric_id"] == REDACTED


async def test_device_diagnostics_delegates(hass, fake_client, matter_devices):
    seed_healthy(fake_client)
    from homeassistant.helpers import device_registry as dr
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    sensor = matter_devices.create("Capetre Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    hub = MockConfigEntry(domain=DOMAIN, data={"name": "M", "url": "ws://x"}, unique_id="hub")
    hub.add_to_hass(hass)
    binding = MockConfigEntry(domain=DOMAIN, data={"label": "Bureau", "sensor_device_id": sensor.id, "trv_device_ids": [trv.id]}, unique_id=sensor.id)
    await hass.config_entries.async_setup(hub.entry_id)
    binding.add_to_hass(hass)
    await hass.config_entries.async_setup(binding.entry_id)
    await hass.async_block_till_done()

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, binding.entry_id)})
    from custom_components.matter_trv_binding.diagnostics import async_get_device_diagnostics
    device_diag = await async_get_device_diagnostics(hass, binding, device)
    assert device_diag == await async_get_config_entry_diagnostics(hass, binding)
    assert device_diag["trvs"][trv.id]["node_id"] == REDACTED
