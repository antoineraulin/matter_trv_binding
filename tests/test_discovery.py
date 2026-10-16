import pytest
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.matter_trv_binding.const import (
    CLUSTER_TEMPERATURE_MEASUREMENT, CLUSTER_THERMOSTAT,
)
from custom_components.matter_trv_binding.matter.client import MatterConnection
from custom_components.matter_trv_binding.matter.discovery import (
    DeviceMissingError, NodeUnreachableError, async_resolve_device, parse_matter_node_id,
)

CFID = 0x1234567890AB


def make_conn(fake_client):
    return MatterConnection("ws://x", client_factory=lambda url: fake_client)


async def test_parse_node_id_native_and_bridged(matter_devices):
    native = matter_devices.create("Eve Thermo Salon", 20)
    bridged = matter_devices.create("Capteur Bureau", 9, postfix="361")
    assert parse_matter_node_id(native) == 20
    assert parse_matter_node_id(bridged) == 9


async def test_parse_returns_none_for_foreign_device(hass):
    MockConfigEntry(domain="other_domain", entry_id="other_entry").add_to_hass(hass)
    registry = dr.async_get(hass)
    dev = registry.async_get_or_create(
        config_entry_id="other_entry",
        identifiers={("other_domain", "x")},
        name="Not Matter",
    )
    assert parse_matter_node_id(dev) is None


async def test_resolve_bridged_sensor(hass, fake_client, matter_devices):
    sensor = matter_devices.create("Capteur Bureau", 9, postfix="361")
    fake_client.attributes[9] = {
        "0/29/0": [0, 361],
        "361/29/1": [1026],
        "361/29/0": [{"deviceType": 7, "revision": 1}],
    }
    conn = make_conn(fake_client)
    dev = await async_resolve_device(conn, hass, sensor.id, CLUSTER_TEMPERATURE_MEASUREMENT)
    assert (dev.node_id, dev.endpoint) == (9, 361)
    assert dev.device_id == sensor.id and dev.device_types == (7,)


async def test_resolve_trv_finds_thermostat_endpoint(hass, fake_client, matter_devices):
    trv = matter_devices.create("Eve Thermo Bureau", 6)
    fake_client.attributes[6] = {
        "0/29/0": [0, 1],
        "1/29/1": [513, 30],
        "1/29/0": [{"deviceType": 769, "revision": 3}],
    }
    conn = make_conn(fake_client)
    dev = await async_resolve_device(conn, hass, trv.id, CLUSTER_THERMOSTAT)
    assert dev.endpoint == 1


async def test_resolve_missing_device_raises(hass, fake_client):
    conn = make_conn(fake_client)
    with pytest.raises(DeviceMissingError):
        await async_resolve_device(conn, hass, "no-such-device-id", CLUSTER_THERMOSTAT)


async def test_resolve_unreachable_node_raises(hass, fake_client, matter_devices):
    trv = matter_devices.create("Eve Thermo Bureau", 6)
    fake_client.fail_nodes.add(6)
    conn = make_conn(fake_client)
    with pytest.raises(NodeUnreachableError):
        await async_resolve_device(conn, hass, trv.id, CLUSTER_THERMOSTAT)
