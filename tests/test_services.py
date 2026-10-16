from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.matter_trv_binding.const import DOMAIN
from tests.test_coordinator import seed_healthy


async def test_repair_service_triggers_refresh(hass, fake_client, matter_devices):
    seed_healthy(fake_client)
    sensor = matter_devices.create("Capetre Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    hub = MockConfigEntry(domain=DOMAIN, data={"name": "M", "url": "ws://x"}, unique_id="hub")
    hub.add_to_hass(hass)
    binding = MockConfigEntry(domain=DOMAIN, data={"label": "Bureau", "sensor_device_id": sensor.id, "trv_device_ids": [trv.id]}, unique_id=sensor.id)
    await hass.config_entries.async_setup(hub.entry_id)
    # The first setup loads the component and sets up every pending entry of the
    # domain at once, so the binding must be added after it (see test_binding_setup).
    binding.add_to_hass(hass)
    await hass.config_entries.async_setup(binding.entry_id)
    await hass.async_block_till_done()

    from homeassistant.helpers import device_registry as dr
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, binding.entry_id)})
    fake_client.attributes[20]["1/30/0"] = []  # drift after setup
    await hass.services.async_call(
        DOMAIN, "repair", {"device_id": device.id}, blocking=True
    )
    await hass.async_block_till_done()
    assert fake_client.attributes[20]["1/30/0"]  # repaired
