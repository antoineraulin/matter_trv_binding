from custom_components.matter_trv_binding.const import TrvStatus
from custom_components.matter_trv_binding.matter.bindings import (
    async_apply_trv, async_check_trv, desired_binding, normalize_binding,
)
from custom_components.matter_trv_binding.matter.client import MatterConnection
from custom_components.matter_trv_binding.matter.discovery import MatterDevice

def make_conn(fake_client):
    return MatterConnection("ws://x", client_factory=lambda url: fake_client)

SENSOR = MatterDevice(device_id="d1", name="Capteur", node_id=9, endpoint=361, device_types=(7,))
TRV = MatterDevice(device_id="d2", name="Eve Thermo", node_id=20, endpoint=1, device_types=(769,))


def test_normalize_both_spellings():
    assert normalize_binding({"1": 9, "3": 361, "4": 1026}) == (9, 361, 1026)
    assert normalize_binding({"node": 9, "endpoint": 361, "cluster": 1026}) == (9, 361, 1026)


def test_desired_binding_payload():
    assert desired_binding(SENSOR) == [
        {"node": 9, "group": None, "endpoint": 361, "cluster": 1026}
    ]


async def test_check_healthy(fake_client):
    fake_client.attributes[20] = {"1/30/0": [{"1": 9, "3": 361, "4": 1026}], "1/513/26": 1}
    assert await async_check_trv(make_conn(fake_client), TRV, SENSOR) is TrvStatus.BOUND


async def test_check_binding_missing(fake_client):
    fake_client.attributes[20] = {"1/30/0": [], "1/513/26": 1}
    assert await async_check_trv(make_conn(fake_client), TRV, SENSOR) is TrvStatus.BINDING_MISSING


async def test_check_remote_sensing_off(fake_client):
    fake_client.attributes[20] = {"1/30/0": [{"node": 9, "endpoint": 361, "cluster": 1026}], "1/513/26": 0}
    assert await async_check_trv(make_conn(fake_client), TRV, SENSOR) is TrvStatus.REMOTE_SENSING_OFF


async def test_check_unreachable(fake_client):
    fake_client.fail_nodes.add(20)
    assert await async_check_trv(make_conn(fake_client), TRV, SENSOR) is TrvStatus.UNREACHABLE


async def test_apply_trv_writes_binding_and_sensing(fake_client):
    fake_client.attributes[20] = {"1/513/26": 0}
    await async_apply_trv(make_conn(fake_client), TRV, SENSOR)
    assert fake_client.attributes[20]["1/30/0"] == [{"node": 9, "group": None, "endpoint": 361, "cluster": 1026}]
    assert fake_client.attributes[20]["1/513/26"] == 1   # OR'd, not overwritten


async def test_apply_trv_preserves_other_sensing_bits(fake_client):
    fake_client.attributes[20] = {"1/513/26": 2}
    await async_apply_trv(make_conn(fake_client), TRV, SENSOR)
    assert fake_client.attributes[20]["1/513/26"] == 3
