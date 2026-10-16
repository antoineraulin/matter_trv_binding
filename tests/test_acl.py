import logging

from custom_components.matter_trv_binding.matter.acl import (
    acl_entry_grants, async_apply_acl, async_verify_acl, desired_acl_entry, normalize_acl_entry,
)
from custom_components.matter_trv_binding.matter.client import MatterConnection
from custom_components.matter_trv_binding.const import (
    CLUSTER_TEMPERATURE_MEASUREMENT,
    PRIVILEGE_VIEW,
)

def make_conn(fake_client):
    return MatterConnection("ws://x", client_factory=lambda url: fake_client)

ADMIN = {"1": 5, "2": 2, "3": [99], "4": []}          # TLV-keyed admin entry (controller)
OUR_TL = {"1": 1, "2": 2, "3": [20, 6], "4": [{"0": 1026, "1": 361}]}


def test_normalize_tlv_keys():
    n = normalize_acl_entry(ADMIN)
    assert n["privilege"] == 5 and n["auth_mode"] == 2 and n["subjects"] == [99]


def test_normalize_named_keys():
    n = normalize_acl_entry({"privilege": 1, "authMode": 2, "subjects": [20], "targets": [{"cluster": 1026, "endpoint": 361}]})
    assert n["privilege"] == 1 and n["targets"][0]["endpoint"] == 361


def test_grants_requires_all_subjects_and_target():
    assert acl_entry_grants(OUR_TL, [20, 6], CLUSTER_TEMPERATURE_MEASUREMENT, 361)
    assert not acl_entry_grants(OUR_TL, [20, 7], CLUSTER_TEMPERATURE_MEASUREMENT, 361)
    assert not acl_entry_grants(OUR_TL, [20, 6], CLUSTER_TEMPERATURE_MEASUREMENT, 999)


def test_desired_entry_both_spellings():
    entry = desired_acl_entry([20, 6], CLUSTER_TEMPERATURE_MEASUREMENT, 361, 7)
    target = entry["targets"][0]
    assert target["device_type"] == 7 and target["deviceType"] == 7
    assert entry["privilege"] == 1 and entry["auth_mode"] == 2


async def test_verify_true_when_granted(fake_client):
    fake_client.attributes[9] = {"0/31/0": [ADMIN, OUR_TL]}
    conn = make_conn(fake_client)
    assert await async_verify_acl(conn, 9, 361, [20, 6], 7) is True
    assert await async_verify_acl(conn, 9, 361, [20, 7], 7) is False


async def test_verify_true_when_subjects_spread_across_entries(fake_client):
    """Matter only needs each TRV granted by SOME entry, so the union counts."""
    fake_client.attributes[9] = {
        "0/31/0": [
            ADMIN,
            {"1": 1, "2": 2, "3": [20], "4": [{"0": 1026, "1": 361}]},
            {"1": 1, "2": 2, "3": [6], "4": [{"0": 1026, "1": 361}]},
        ]
    }
    conn = make_conn(fake_client)
    assert await async_verify_acl(conn, 9, 361, [20, 6], 7) is True


async def test_apply_adds_entry_and_passes_safety(fake_client):
    fake_client.attributes[9] = {"0/31/0": [ADMIN]}
    conn = make_conn(fake_client)
    await async_apply_acl(conn, 9, 361, [20, 6], 7)
    acl = fake_client.attributes[9]["0/31/0"]
    assert len(acl) == 2          # admin preserved, ours added
    assert await async_verify_acl(conn, 9, 361, [20, 6], 7)


async def test_apply_never_drops_admin_even_on_replacing_server(fake_client):
    """Safety net: if the server's set_acl_entry replaces the whole list,
    apply must detect the lost admin entry and restore it."""
    fake_client.acl_replaces_all = True
    fake_client.attributes[9] = {"0/31/0": [ADMIN]}
    conn = make_conn(fake_client)
    try:
        await async_apply_acl(conn, 9, 361, [20, 6], 7)
        raise AssertionError("should raise")
    except Exception:
        pass
    acl = fake_client.attributes[9]["0/31/0"]
    assert any(normalize_acl_entry(e)["privilege"] == 5 for e in acl), "admin entry must be restored"


async def test_apply_sends_entry_as_list(fake_client):
    """Server contract: `entry` must be an array, else entries.map throws."""
    fake_client.attributes[9] = {"0/31/0": [ADMIN]}
    conn = make_conn(fake_client)
    await async_apply_acl(conn, 9, 361, [20, 6], 7)
    _, kwargs = next(
        (c, k) for c, k in fake_client.commands if c == "set_acl_entry"
    )
    assert isinstance(kwargs["entry"], list)
    assert kwargs["entry"][0]["privilege"] == PRIVILEGE_VIEW


async def test_apply_warns_when_acl_exceeds_device_limit(fake_client, caplog):
    """Spec: an ACL longer than the ~4-entry device limit must be logged."""
    fake_client.attributes[9] = {
        "0/31/0": [ADMIN] + [{"1": 1, "2": 2, "3": [i], "4": []} for i in range(1, 5)]
    }
    conn = make_conn(fake_client)
    with caplog.at_level(logging.WARNING):
        await async_apply_acl(conn, 9, 361, [20, 6], 7)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings and "9" in warnings[0].getMessage()
