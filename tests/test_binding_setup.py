from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.matter_trv_binding.const import DOMAIN

from tests.test_coordinator import seed_healthy


async def test_binding_setup_creates_device_and_entities(hass, fake_client, matter_devices):
    seed_healthy(fake_client)
    sensor = matter_devices.create("Capetre Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    hub = MockConfigEntry(domain=DOMAIN, data={"name": "M", "url": "ws://x"}, unique_id="hub")
    hub.add_to_hass(hass)
    binding = MockConfigEntry(
        domain=DOMAIN,
        data={"label": "Bureau", "sensor_device_id": sensor.id, "trv_device_ids": [trv.id]},
        unique_id=sensor.id,
    )
    assert await hass.config_entries.async_setup(hub.entry_id)
    # The first setup loads the component, which sets up every pending entry of the
    # domain at once; the explicit binding setup below needs it added afterwards.
    binding.add_to_hass(hass)
    assert await hass.config_entries.async_setup(binding.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(f"binary_sensor.eve_thermo_bureau_binding").state == "on"
    assert hass.states.get(f"sensor.eve_thermo_bureau_status").state == "bound"
    assert hass.states.get("sensor.matter_binding_bureau_last_check") is not None
    assert hass.states.get("sensor.matter_binding_bureau_repairs").state == "0"
    assert hass.states.get("button.matter_binding_bureau_check_and_repair") is not None
    from homeassistant.helpers import device_registry as dr
    binding_device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, binding.entry_id)}
    )
    assert binding_device.via_device_id == sensor.id


async def test_binding_setup_without_hub_fails(hass, fake_client, matter_devices):
    sensor = matter_devices.create("Capetre Bureau", 9, postfix="361")
    binding = MockConfigEntry(domain=DOMAIN, data={"label": "B", "sensor_device_id": sensor.id, "trv_device_ids": []}, unique_id=sensor.id)
    binding.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(binding.entry_id)
    assert binding.state is ConfigEntryState.SETUP_RETRY
