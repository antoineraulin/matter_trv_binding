from matter_server.client.exceptions import ServerVersionTooOld

from custom_components.matter_trv_binding.matter.client import (
    MatterCommandError, MatterConnection, MatterConnectionError, MatterServerTooOldError,
)


def make_conn(fake_client):
    return MatterConnection("ws://x", client_factory=lambda url: fake_client)


async def test_connect_returns_server_info(fake_client):
    conn = make_conn(fake_client)
    info = await conn.async_connect()
    assert info["fabric_id"] == 1
    assert fake_client.connected


async def test_connect_failure_raises(fake_client):
    fake_client.connect_fails = 2
    conn = make_conn(fake_client)
    try:
        await conn.async_connect()
        raise AssertionError("should raise")
    except MatterConnectionError:
        pass


async def test_connect_server_version_too_old(fake_client):
    async def too_old():
        raise ServerVersionTooOld("schema 12 below client floor 13")

    fake_client.connect = too_old  # type: ignore[method-assign]
    conn = make_conn(fake_client)
    assert issubclass(MatterServerTooOldError, MatterConnectionError)
    try:
        await conn.async_connect()
        raise AssertionError("should raise")
    except MatterServerTooOldError:
        pass


async def test_ensure_connected_reconnects_after_drop(fake_client):
    conn = make_conn(fake_client)
    await conn.async_connect()
    fake_client.connected = False  # server died
    await conn.async_ensure_connected()
    assert fake_client.connected


async def test_read_attribute_returns_value(fake_client):
    fake_client.attributes[5] = {"1/513/26": 1}
    conn = make_conn(fake_client)
    await conn.async_connect()
    assert await conn.read_attribute(5, "1/513/26") == 1
    assert await conn.read_attribute(5, "9/9/9") is None


async def test_write_attribute_raises_on_bad_status(fake_client):
    conn = make_conn(fake_client)
    await conn.async_connect()
    async def bad(**kw):
        return [{"Path": kw["attribute_path"], "Status": 1}]
    fake_client.send_command = bad  # type: ignore[assignment]
    try:
        await conn.write_attribute(5, "1/513/26", 1)
        raise AssertionError("should raise")
    except MatterCommandError:
        pass


async def test_subscribe_roundtrip(fake_client):
    conn = make_conn(fake_client)
    await conn.async_connect()
    events = []
    unsub = conn.subscribe(lambda et, data: events.append((et, data)))
    fake_client.emit_event("attribute_updated", {"node_id": 5})
    unsub()
    fake_client.emit_event("attribute_updated", {"node_id": 5})
    assert len(events) == 1 and events[0][1]["node_id"] == 5


async def test_subscribe_survives_real_scalar_payload(fake_client):
    """The real client signals (EventType, bare_value); a scalar payload must pass
    through the wrapper's enum normalization untouched."""
    from matter_server.common.models import EventType

    conn = make_conn(fake_client)
    await conn.async_connect()
    events = []
    conn.subscribe(lambda et, data: events.append((et, data)))
    fake_client.emit_event(EventType.ATTRIBUTE_UPDATED, 21.5)   # real contract
    assert events == [("attribute_updated", 21.5)]


async def test_close_prevents_reconnect(fake_client):
    conn = make_conn(fake_client)
    await conn.async_connect()
    await conn.async_close()
    assert not fake_client.connected
    try:
        await conn.async_ensure_connected()
        raise AssertionError("should raise")
    except MatterConnectionError:
        pass
    assert not fake_client.connected   # never revived on its own


from tests.fake_matter import FakeMatterClient


async def test_subscription_survives_reconnect(fake_client):
    second = FakeMatterClient()
    generation = {"n": 0}

    def factory(url):
        generation["n"] += 1
        return fake_client if generation["n"] == 1 else second

    conn = MatterConnection("ws://x", client_factory=factory)
    await conn.async_connect()
    events = []
    conn.subscribe(lambda et, data: events.append(data))
    fake_client.connected = False  # server drop; reconnect builds the second client
    await conn.async_ensure_connected()
    second.emit_event("attribute_updated", {"node_id": 5})
    assert events == [{"node_id": 5}]
