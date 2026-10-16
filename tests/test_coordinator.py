from datetime import timedelta

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.matter_trv_binding.const import DOMAIN, TrvStatus
from custom_components.matter_trv_binding.coordinator import BindingCoordinator


def seed_healthy(fake_client):
    fake_client.attributes[9] = {
        "0/29/3": [0, 361], "361/29/1": [1026], "361/29/0": [{"deviceType": 7, "revision": 1}],
        "0/31/0": [{"1": 5, "2": 2, "3": [99], "4": []},
                   {"1": 1, "2": 2, "3": [20], "4": [{"0": 1026, "1": 361}]}],
    }
    fake_client.attributes[20] = {
        "0/29/3": [0, 1], "1/29/1": [513, 30], "1/29/0": [{"deviceType": 769, "revision": 3}],
        "1/30/0": [{"node": 9, "endpoint": 361, "cluster": 1026}], "1/513/26": 1,
    }


async def make_loaded_pair(hass, fake_client):
    from custom_components.matter_trv_binding.hub import HubRuntimeData
    from custom_components.matter_trv_binding.matter.client import MatterConnection
    conn = MatterConnection("ws://x", client_factory=lambda url: fake_client)
    await conn.async_connect()
    hub = MockConfigEntry(domain=DOMAIN, data={"name": "M", "url": "ws://x"}, unique_id="hub")
    hub.add_to_hass(hass)
    hub.runtime_data = HubRuntimeData(connection=conn, repair_counts={})
    binding = MockConfigEntry(domain=DOMAIN, data={"label": "Bureau", "sensor_device_id": "SENS", "trv_device_ids": ["TRV1"]}, unique_id="SENS")
    binding.add_to_hass(hass)
    # HA 2026.2 wraps entry data in a read-only MappingProxyType; expose a mutable copy
    # so the tests below can point the binding at the real device ids.
    object.__setattr__(binding, "data", dict(binding.data))
    return hub, binding


async def test_healthy_cycle(hass, fake_client, matter_devices):
    seed_healthy(fake_client)
    sensor = matter_devices.create("Capteur Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    hub, binding = await make_loaded_pair(hass, fake_client)
    binding.data["sensor_device_id"] = sensor.id
    binding.data["trv_device_ids"] = [trv.id]
    coord = BindingCoordinator(hass, binding, hub)
    data = await coord._async_update_data()
    assert data.sensor_ok is True
    assert data.trvs[trv.id].status is TrvStatus.BOUND and data.trvs[trv.id].healthy
    assert data.last_check is not None


async def test_drift_is_repaired(hass, fake_client, matter_devices):
    seed_healthy(fake_client)
    fake_client.attributes[20]["1/30/0"] = []                 # binding lost
    fake_client.attributes[20]["1/513/26"] = 0                # sensing lost
    del fake_client.attributes[9]["0/31/0"][1]                # ACL entry lost
    sensor = matter_devices.create("Capetre Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    hub, binding = await make_loaded_pair(hass, fake_client)
    binding.data["sensor_device_id"] = sensor.id
    binding.data["trv_device_ids"] = [trv.id]
    coord = BindingCoordinator(hass, binding, hub)
    data = await coord._async_update_data()
    assert data.trvs[trv.id].healthy                          # repaired in the same cycle
    assert fake_client.attributes[20]["1/30/0"]               # binding rewritten
    assert fake_client.attributes[20]["1/513/26"] == 1
    assert hub.runtime_data.repair_counts[binding.entry_id] >= 1


async def test_unreachable_trv_does_not_block_others(hass, fake_client, matter_devices):
    seed_healthy(fake_client)
    trv2 = matter_devices.create("Eve Thermo Salon", 6)
    fake_client.attributes[6] = {"0/29/3": [0, 1], "1/29/1": [513, 30], "1/29/0": [{"deviceType": 769, "revision": 3}]}
    fake_client.fail_nodes.add(6)                            # only node 6 fails
    sensor = matter_devices.create("Capetre Bureau", 9, postfix="361")
    trv1 = matter_devices.create("Eve Thermo Bureau", 20)
    hub, binding = await make_loaded_pair(hass, fake_client)
    binding.data["sensor_device_id"] = sensor.id
    binding.data["trv_device_ids"] = [trv1.id, trv2.id]
    coord = BindingCoordinator(hass, binding, hub)
    data = await coord._async_update_data()
    assert data.trvs[trv1.id].healthy is True                 # first TRV unaffected
    assert data.trvs[trv2.id].status is TrvStatus.UNREACHABLE


async def test_node_id_change_auto_heals_and_notifies(hass, fake_client, matter_devices):
    seed_healthy(fake_client)
    sensor = matter_devices.create("Capetre Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    hub, binding = await make_loaded_pair(hass, fake_client)
    binding.data["sensor_device_id"] = sensor.id
    binding.data["trv_device_ids"] = [trv.id]
    coord = BindingCoordinator(hass, binding, hub)
    await coord._async_update_data()
    # device re-commissioned: same HA device, new node id
    from homeassistant.helpers import device_registry as dr
    dr.async_get(hass).async_update_device(trv.id, new_identifiers={("matter", "deviceid_00001234567890AB-0000000000000064-MatterNodeDevice")})
    fake_client.attributes[100] = dict(fake_client.attributes[20])   # node 0x64 = 100
    data = await coord._async_update_data()
    assert data.trvs[trv.id].node_id == 100 and data.trvs[trv.id].healthy
    assert coord.node_changes == [(trv.id, 20, 100)]


async def test_fast_path_refreshes_on_trv_event(hass, fake_client, matter_devices):
    seed_healthy(fake_client)
    sensor = matter_devices.create("Capteur Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    hub, binding = await make_loaded_pair(hass, fake_client)
    binding.data["sensor_device_id"] = sensor.id
    binding.data["trv_device_ids"] = [trv.id]
    coord = BindingCoordinator(hass, binding, hub)
    await coord._async_update_data()
    # The real client sends the bare attribute value, not {"node_id": ...}.
    fake_client.emit_event("attribute_updated", 21.5)
    await hass.async_block_till_done()
    assert coord.data is not None
    assert coord.last_update_success
    assert coord.data.trvs[trv.id].healthy


async def test_device_missing_creates_issue(hass, fake_client, matter_devices):
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import issue_registry as ir
    seed_healthy(fake_client)
    sensor = matter_devices.create("Capteur Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    hub, binding = await make_loaded_pair(hass, fake_client)
    binding.data["sensor_device_id"] = sensor.id
    binding.data["trv_device_ids"] = [trv.id]
    coord = BindingCoordinator(hass, binding, hub)
    dr.async_get(hass).async_remove_device(trv.id)          # device gone from the registry
    data = await coord._async_update_data()
    assert data.trvs[trv.id].status is TrvStatus.DEVICE_MISSING
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"device_missing_{binding.entry_id}") is not None


async def test_repair_failed_escalation_and_clear(hass, fake_client, matter_devices):
    from homeassistant.helpers import issue_registry as ir
    seed_healthy(fake_client)
    del fake_client.attributes[9]["0/31/0"][1]      # sensor ACL grant lost
    fake_client.acl_replaces_all = True             # every ACL repair rolls back and raises
    sensor = matter_devices.create("Capteur Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    hub, binding = await make_loaded_pair(hass, fake_client)
    object.__setattr__(hub, "options", {"repair_attempts": 1})   # escalate on first failure
    binding.data["sensor_device_id"] = sensor.id
    binding.data["trv_device_ids"] = [trv.id]
    coord = BindingCoordinator(hass, binding, hub)
    issue_id = f"repair_failed_{binding.entry_id}"
    await coord._async_update_data()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    fake_client.acl_replaces_all = False            # next repair succeeds
    await coord._async_update_data()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_apply_write_failure_does_not_abort_cycle(hass, fake_client, matter_devices):
    seed_healthy(fake_client)
    fake_client.attributes[20]["1/30/0"] = []       # binding missing -> repair writes
    fake_client.attributes[20]["1/513/26"] = 0      # sensing off -> repair writes bit 0
    sensor = matter_devices.create("Capteur Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    hub, binding = await make_loaded_pair(hass, fake_client)
    binding.data["sensor_device_id"] = sensor.id
    binding.data["trv_device_ids"] = [trv.id]
    coord = BindingCoordinator(hass, binding, hub)

    original = fake_client.send_command

    async def bad(command, **kw):
        if command == "write_attribute" and str(kw.get("attribute_path", "")).endswith("/513/26"):
            return [{"Path": kw["attribute_path"], "Status": 1}]
        return await original(command, **kw)

    fake_client.send_command = bad  # type: ignore[assignment]
    await coord.async_refresh()
    assert coord.data is not None
    assert coord.last_update_success
    assert coord.data.trvs[trv.id].status is TrvStatus.BINDING_MISSING


async def test_device_missing_issue_persists_with_mixed_trvs(hass, fake_client, matter_devices):
    from homeassistant.helpers import issue_registry as ir
    seed_healthy(fake_client)
    sensor = matter_devices.create("Capteur Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    hub, binding = await make_loaded_pair(hass, fake_client)
    binding.data["sensor_device_id"] = sensor.id
    binding.data["trv_device_ids"] = ["missing-device-id", trv.id]   # missing first
    coord = BindingCoordinator(hass, binding, hub)
    data = await coord._async_update_data()
    assert data.trvs["missing-device-id"].status is TrvStatus.DEVICE_MISSING
    assert data.trvs[trv.id].status is TrvStatus.BOUND
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"device_missing_{binding.entry_id}") is not None


async def test_acl_lost_marks_trv_acl_missing(hass, fake_client, matter_devices):
    seed_healthy(fake_client)
    del fake_client.attributes[9]["0/31/0"][1]      # sensor ACL grant lost
    fake_client.acl_replaces_all = True             # repair rolls back and fails
    sensor = matter_devices.create("Capteur Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    hub, binding = await make_loaded_pair(hass, fake_client)
    binding.data["sensor_device_id"] = sensor.id
    binding.data["trv_device_ids"] = [trv.id]
    coord = BindingCoordinator(hass, binding, hub)
    data = await coord._async_update_data()
    assert data.trvs[trv.id].status is TrvStatus.ACL_MISSING
    assert data.trvs[trv.id].healthy is False


async def test_sensor_device_missing_creates_issue(hass, fake_client, matter_devices):
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import issue_registry as ir
    seed_healthy(fake_client)
    sensor = matter_devices.create("Capteur Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    hub, binding = await make_loaded_pair(hass, fake_client)
    binding.data["sensor_device_id"] = sensor.id
    binding.data["trv_device_ids"] = [trv.id]
    coord = BindingCoordinator(hass, binding, hub)
    dr.async_get(hass).async_remove_device(sensor.id)   # sensor registry entry gone
    data = await coord._async_update_data()
    assert data.sensor_ok is False
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"device_missing_{binding.entry_id}") is not None


async def test_unreachable_trv_is_still_granted_acl(hass, fake_client, matter_devices):
    from custom_components.matter_trv_binding.matter.acl import normalize_acl_entry
    seed_healthy(fake_client)
    trv2 = matter_devices.create("Eve Thermo Salon", 6)
    fake_client.attributes[6] = {"0/29/3": [0, 1], "1/29/1": [513, 30], "1/29/0": [{"deviceType": 769, "revision": 3}]}
    fake_client.fail_nodes.add(6)                      # node 6 unreachable this cycle
    sensor = matter_devices.create("Capteur Bureau", 9, postfix="361")
    trv1 = matter_devices.create("Eve Thermo Bureau", 20)
    hub, binding = await make_loaded_pair(hass, fake_client)
    binding.data["sensor_device_id"] = sensor.id
    binding.data["trv_device_ids"] = [trv1.id, trv2.id]
    coord = BindingCoordinator(hass, binding, hub)
    await coord._async_update_data()
    acl = fake_client.attributes[9]["0/31/0"]
    assert any(set(normalize_acl_entry(e)["subjects"]) == {20, 6} for e in acl)


async def test_sensor_node_change_notifies(hass, fake_client, matter_devices):
    from homeassistant.helpers import device_registry as dr
    seed_healthy(fake_client)
    sensor = matter_devices.create("Capteur Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    hub, binding = await make_loaded_pair(hass, fake_client)
    binding.data["sensor_device_id"] = sensor.id
    binding.data["trv_device_ids"] = [trv.id]
    coord = BindingCoordinator(hass, binding, hub)
    await coord._async_update_data()
    # sensor re-commissioned: same HA device, new node id
    dr.async_get(hass).async_update_device(
        sensor.id,
        new_identifiers={("matter", "deviceid_00001234567890AB-0000000000000064-MatterNodeDevice")},
    )
    fake_client.attributes[100] = dict(fake_client.attributes[9])   # node 0x64 = 100
    data = await coord._async_update_data()
    assert coord.node_changes == [(sensor.id, 9, 100)]
    assert data.trvs[trv.id].healthy
