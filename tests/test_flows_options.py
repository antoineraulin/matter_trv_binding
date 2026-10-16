from datetime import timedelta

from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.matter_trv_binding.const import DOMAIN
from tests.test_coordinator import seed_healthy


async def make_setup(hass, fake_client, matter_devices):
    seed_healthy(fake_client)
    sensor = matter_devices.create("Capetre Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    hub = MockConfigEntry(domain=DOMAIN, data={"name": "M", "url": "ws://x"}, unique_id="hub")
    hub.add_to_hass(hass)
    binding = MockConfigEntry(domain=DOMAIN, data={"label": "Bureau", "sensor_device_id": sensor.id, "trv_device_ids": [trv.id]}, unique_id=sensor.id)
    # The first domain setup loads the component and sets up every pending entry at
    # once, so the binding must be added after the hub is loaded.
    await hass.config_entries.async_setup(hub.entry_id)
    binding.add_to_hass(hass)
    await hass.config_entries.async_setup(binding.entry_id)
    await hass.async_block_till_done()
    return hub, binding, sensor, trv


async def test_hub_options_updates_interval_and_reloads_bindings(hass, fake_client, matter_devices):
    hub, binding, sensor, trv = await make_setup(hass, fake_client, matter_devices)
    result = await hass.config_entries.options.async_init(hub.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"update_interval_minutes": 15, "auto_repair": True, "repair_attempts": 3},
    )
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert hub.options["update_interval_minutes"] == 15
    # binding entry was reloaded so the coordinator picked up the new interval
    assert binding.state is ConfigEntryState.LOADED
    assert binding.runtime_data.coordinator.update_interval == timedelta(minutes=15)
    # the rebuilt coordinator captured the hub's *current* runtime, not the stale one
    assert binding.runtime_data.coordinator._connection is hub.runtime_data.connection


async def test_binding_reconfigure_changes_trv_list(hass, fake_client, matter_devices):
    hub, binding, sensor, trv = await make_setup(hass, fake_client, matter_devices)
    trv2 = matter_devices.create("Eve Thermo Salon", 6)
    fake_client.attributes[6] = {"0/29/3": [0, 1], "1/29/1": [513, 30], "1/29/0": [{"deviceType": 769, "revision": 3}]}
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": binding.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"label": "Bureau", "sensor_device": sensor.id, "trv_devices": [trv.id, trv2.id]}
    )
    await hass.async_block_till_done()
    assert result["type"] == "abort" and result["reason"] == "reconfigure_successful"
    assert set(binding.data["trv_device_ids"]) == {trv.id, trv2.id}
    # ACL on the sensor now grants both TRV node ids
    assert fake_client.attributes[20]["1/30/0"]
