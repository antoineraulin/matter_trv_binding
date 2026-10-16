import pytest
from homeassistant.data_entry_flow import InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.matter_trv_binding.const import DOMAIN
from tests.test_coordinator import seed_healthy


async def setup_hub(hass):
    hub = MockConfigEntry(domain=DOMAIN, data={"name": "M", "url": "ws://x"}, unique_id="hub")
    hub.add_to_hass(hass)
    assert await hass.config_entries.async_setup(hub.entry_id)
    return hub


async def test_binding_flow_creates_entry(hass, fake_client, matter_devices):
    await setup_hub(hass)
    seed_healthy(fake_client)
    sensor = matter_devices.create("Capetre Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    fake_client.attributes[9]["0/31/0"] = [{"1": 5, "2": 2, "3": [99], "4": []}]  # only admin; ACL to be created by the flow
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["step_id"] == "binding"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"label": "Bureau", "sensor_device": sensor.id, "trv_devices": [trv.id]},
    )
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert result["data"]["sensor_device_id"] == sensor.id
    assert result["data"]["trv_device_ids"] == [trv.id]
    # the flow applied the binding before creating the entry
    assert fake_client.attributes[20]["1/30/0"] == [{"node": 9, "endpoint": 361, "cluster": 1026}]
    assert fake_client.attributes[20]["1/513/26"] == 1


async def test_binding_flow_is_idempotent_when_already_applied(hass, fake_client, matter_devices):
    await setup_hub(hass)
    seed_healthy(fake_client)                     # ACL grant, binding and sensing already correct
    sensor = matter_devices.create("Capteur Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"label": "Bureau", "sensor_device": sensor.id, "trv_devices": [trv.id]},
    )
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    # nothing was re-applied: the sensor ACL still holds exactly admin + our grant
    assert len(fake_client.attributes[9]["0/31/0"]) == 2


async def test_binding_flow_rejects_non_sensor(hass, fake_client, matter_devices):
    await setup_hub(hass)
    seed_healthy(fake_client)
    not_a_sensor = matter_devices.create("Capetre Bureau", 9, postfix="361")  # has 1026
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    # pick a device with no 1026 as the "sensor": use the TRV itself
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"label": "X", "sensor_device": trv.id, "trv_devices": [trv.id]}
    )
    assert result["type"] == "form" and result["errors"] == {"base": "invalid_sensor"}


async def test_binding_flow_rejects_unreachable(hass, fake_client, matter_devices):
    await setup_hub(hass)
    seed_healthy(fake_client)
    fake_client.fail_nodes.add(20)
    sensor = matter_devices.create("Capetre Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"label": "X", "sensor_device": sensor.id, "trv_devices": [trv.id]}
    )
    assert result["type"] == "form" and result["errors"] == {"base": "unreachable"}


async def test_binding_flow_rejects_empty_trv_list(hass, fake_client, matter_devices):
    await setup_hub(hass)
    seed_healthy(fake_client)
    sensor = matter_devices.create("Capteur Bureau", 9, postfix="361")
    fake_client.attributes[9]["0/31/0"] = [{"1": 5, "2": 2, "3": [99], "4": []}]
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {"label": "X", "sensor_device": sensor.id, "trv_devices": []}
        )
    # nothing was applied and no binding entry was created
    assert len(fake_client.attributes[9]["0/31/0"]) == 1
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
