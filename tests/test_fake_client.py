from tests.fake_matter import FakeMatterClient


async def test_read_write_roundtrip():
    client = FakeMatterClient()
    await client.send_command("write_attribute", node_id=5, attribute_path="1/513/26", value=3)
    result = await client.send_command("read_attribute", node_id=5, attribute_path="1/513/26")
    assert result["1/513/26"] == 3


async def test_fail_nodes_raise():
    client = FakeMatterClient()
    client.fail_nodes.add(5)
    try:
        await client.send_command("read_attribute", node_id=5, attribute_path="0/29/0")
        raise AssertionError("should have raised")
    except ConnectionError:
        pass
