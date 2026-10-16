import pytest
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.matter_trv_binding.const import (
    CLUSTER_TEMPERATURE_MEASUREMENT, CLUSTER_THERMOSTAT,
)
from custom_components.matter_trv_binding.matter.client import MatterConnection
from custom_components.matter_trv_binding.matter.discovery import (
    DeviceMissingError, NodeUnreachableError, async_resolve_device, parse_matter_endpoint,
    parse_matter_node_id,
)

CFID = 0x1234567890AB


def make_conn(fake_client):
    return MatterConnection("ws://x", client_factory=lambda url: fake_client)


async def test_parse_node_id_native_and_bridged(matter_devices):
    native = matter_devices.create("Eve Thermo Salon", 20)
    bridged = matter_devices.create("Capteur Bureau", 9, postfix="361")
    assert parse_matter_node_id(native) == 20
    assert parse_matter_node_id(bridged) == 9


async def test_parse_endpoint_native_vs_bridged(matter_devices):
    native = matter_devices.create("Eve Thermo Salon", 20)
    bridged = matter_devices.create("Capteur Bureau", 9, postfix="361")
    assert parse_matter_endpoint(native) is None
    assert parse_matter_endpoint(bridged) == 361


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
        "0/29/3": [0, 361],
        "361/29/1": [1026],
        "361/29/0": [{"deviceType": 7, "revision": 1}],
    }
    conn = make_conn(fake_client)
    dev = await async_resolve_device(conn, hass, sensor.id, CLUSTER_TEMPERATURE_MEASUREMENT)
    assert (dev.node_id, dev.endpoint) == (9, 361)
    assert dev.device_id == sensor.id and dev.device_types == (7,)


async def test_resolve_bridged_device_uses_its_own_endpoint(hass, fake_client, matter_devices):
    """Two bridged sensors on one node: the registry's endpoint beats the lowest match."""
    sensor = matter_devices.create("Capteur Chambre", 9, postfix="366")
    fake_client.attributes[9] = {
        "0/29/3": [0, 364, 366],
        "364/29/1": [1026],
        "364/29/0": [{"deviceType": 7, "revision": 1}],
        "366/29/1": [1026],
        "366/29/0": [{"deviceType": 7, "revision": 1}],
    }
    conn = make_conn(fake_client)
    dev = await async_resolve_device(conn, hass, sensor.id, CLUSTER_TEMPERATURE_MEASUREMENT)
    assert dev.endpoint == 366


async def test_resolve_node_level_device_falls_back_to_lowest(
    hass, fake_client, matter_devices
):
    """A node-level device names no endpoint, so the lowest matching one is used."""
    sensor = matter_devices.create("Sonde Salon", 9)
    fake_client.attributes[9] = {
        "0/29/3": [0, 364, 366],
        "364/29/1": [1026],
        "364/29/0": [{"deviceType": 7, "revision": 1}],
        "366/29/1": [1026],
        "366/29/0": [{"deviceType": 7, "revision": 1}],
    }
    conn = make_conn(fake_client)
    dev = await async_resolve_device(conn, hass, sensor.id, CLUSTER_TEMPERATURE_MEASUREMENT)
    assert dev.endpoint == 364


async def test_resolve_bridged_device_falls_back_when_own_endpoint_lacks_cluster(
    hass, fake_client, matter_devices
):
    """A bridged identifier endpoint without the wanted cluster falls back to the scan."""
    sensor = matter_devices.create("Capteur Chambre", 9, postfix="366")
    fake_client.attributes[9] = {
        "0/29/3": [0, 364, 366],
        "364/29/1": [1026],
        "364/29/0": [{"deviceType": 7, "revision": 1}],
        "366/29/1": [513],   # own endpoint hosts the thermostat cluster, not temperature
        "366/29/0": [{"deviceType": 769, "revision": 1}],
    }
    conn = make_conn(fake_client)
    dev = await async_resolve_device(conn, hass, sensor.id, CLUSTER_TEMPERATURE_MEASUREMENT)
    assert dev.endpoint == 364


async def test_resolve_trv_finds_thermostat_endpoint(hass, fake_client, matter_devices):
    trv = matter_devices.create("Eve Thermo Bureau", 6)
    fake_client.attributes[6] = {
        "0/29/3": [0, 1],
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
