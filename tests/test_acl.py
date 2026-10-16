import logging

from custom_components.matter_trv_binding.matter.acl import (
    ACL_ATTRIBUTE_PATH, acl_entry_grants, async_apply_acl, async_verify_acl,
    desired_acl_entry, normalize_acl_entry,
)
from custom_components.matter_trv_binding.matter.client import MatterCommandError, MatterConnection
from custom_components.matter_trv_binding.const import (
    CLUSTER_TEMPERATURE_MEASUREMENT,
    PRIVILEGE_ADMIN,
    PRIVILEGE_VIEW,
)

def make_conn(fake_client):
    return MatterConnection("ws://x", client_factory=lambda url: fake_client)

ADMIN = {"1": 5, "2": 2, "3": [99], "4": [], "fabricIndex": 1}   # TLV-keyed admin entry (controller)
OUR_TL = {"1": 1, "2": 2, "3": [20, 6], "4": [{"0": 1026, "1": 361}], "fabricIndex": 1}
# Real controller admin entries commonly omit both: absent (TLV) and explicit None.
NULL_ADMIN_TLV = {"1": 5, "2": 2, "fabricIndex": 1}
NULL_ADMIN_NAMED = {"privilege": 5, "authMode": 2, "subjects": None, "targets": None, "fabricIndex": 1}
# A view grant that is NOT our managed sensor grant -> must be kept untouched.
OTHER_VIEW = {"1": 1, "2": 2, "3": [42], "4": [{"0": 513, "1": 1}], "fabricIndex": 1}


def test_normalize_tlv_keys():
    n = normalize_acl_entry(ADMIN)
    assert n["privilege"] == 5 and n["auth_mode"] == 2 and n["subjects"] == [99]
    assert n["fabric_index"] == 1


def test_normalize_named_keys():
    n = normalize_acl_entry({"privilege": 1, "authMode": 2, "subjects": [20], "targets": [{"cluster": 1026, "endpoint": 361}]})
    assert n["privilege"] == 1 and n["targets"][0]["endpoint"] == 361
    assert n["fabric_index"] is None


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
            {"1": 1, "2": 2, "3": [20], "4": [{"0": 1026, "1": 361}], "fabricIndex": 1},
            {"1": 1, "2": 2, "3": [6], "4": [{"0": 1026, "1": 361}], "fabricIndex": 1},
        ]
    }
    conn = make_conn(fake_client)
    assert await async_verify_acl(conn, 9, 361, [20, 6], 7) is True


async def test_apply_keeps_admin_and_adds_grant(fake_client):
    """The full fabric list is written: admin kept, our managed grant appended."""
    fake_client.attributes[9] = {"0/31/0": [ADMIN]}
    conn = make_conn(fake_client)
    await async_apply_acl(conn, 9, 361, [20, 6], 7)
    acl = fake_client.attributes[9]["0/31/0"]
    assert len(acl) == 2                                   # admin preserved, ours added
    assert acl[0]["privilege"] == PRIVILEGE_ADMIN
    assert acl[1]["subjects"] == [20, 6] and acl[1]["fabricIndex"] == 1
    assert await async_verify_acl(conn, 9, 361, [20, 6], 7)


async def test_apply_replaces_our_managed_grant(fake_client):
    """Applying twice with different TRV sets leaves exactly one managed grant."""
    fake_client.attributes[9] = {"0/31/0": [ADMIN]}
    conn = make_conn(fake_client)
    await async_apply_acl(conn, 9, 361, [20, 6], 7)
    await async_apply_acl(conn, 9, 361, [20, 6, 7], 7)
    acl = fake_client.attributes[9]["0/31/0"]
    managed = [e for e in acl if e["privilege"] == PRIVILEGE_VIEW]
    assert len(managed) == 1
    assert managed[0]["subjects"] == [20, 6, 7]


async def test_apply_preserves_null_subjects_and_targets(fake_client):
    """Absent/None subjects+targets survive the round-trip; other kept entries untouched."""
    fake_client.attributes[9] = {
        "0/31/0": [NULL_ADMIN_TLV, NULL_ADMIN_NAMED, OTHER_VIEW]
    }
    conn = make_conn(fake_client)
    await async_apply_acl(conn, 9, 361, [20, 6], 7)
    acl = fake_client.attributes[9]["0/31/0"]
    assert acl[0]["subjects"] is None and acl[0]["targets"] is None   # TLV absent -> None
    assert acl[1]["subjects"] is None and acl[1]["targets"] is None   # explicit None
    assert acl[2]["subjects"] == [42]                                 # kept view grant untouched
    assert acl[2]["targets"][0]["cluster"] == 513


async def test_verify_handles_null_subjects(fake_client):
    """A null-subjects entry grants nothing; a proper grant still verifies."""
    fake_client.attributes[9] = {
        "0/31/0": [{"1": 1, "2": 2, "3": None, "4": None, "fabricIndex": 1}]
    }
    conn = make_conn(fake_client)
    assert await async_verify_acl(conn, 9, 361, [20, 6], 7) is False
    fake_client.attributes[9]["0/31/0"].append(OUR_TL)
    assert await async_verify_acl(conn, 9, 361, [20, 6], 7) is True


async def test_apply_refuses_without_admin(fake_client):
    """No admin entry kept -> refuse, and write nothing."""
    fake_client.attributes[9] = {"0/31/0": [OUR_TL]}       # no admin entry at all
    conn = make_conn(fake_client)
    before = list(fake_client.attributes[9]["0/31/0"])
    try:
        await async_apply_acl(conn, 9, 361, [20, 6], 7)
        raise AssertionError("should raise")
    except MatterCommandError as err:
        assert "admin" in str(err)
    assert fake_client.attributes[9]["0/31/0"] == before


async def test_apply_raises_and_names_path_on_rejected_write(fake_client):
    """A device rejection comes back as a non-zero write status; the wrapper must raise."""
    fake_client.attributes[9] = {"0/31/0": [ADMIN]}
    conn = make_conn(fake_client)
    original = fake_client.send_command

    async def bad(command, **kw):
        if command == "write_attribute" and kw.get("attribute_path") == ACL_ATTRIBUTE_PATH:
            return [{"Path": ACL_ATTRIBUTE_PATH, "Status": 1}]
        return await original(command, **kw)

    fake_client.send_command = bad  # type: ignore[assignment]
    try:
        await async_apply_acl(conn, 9, 361, [20, 6], 7)
        raise AssertionError("should raise")
    except MatterCommandError as err:
        assert ACL_ATTRIBUTE_PATH in str(err)


async def test_apply_warns_when_acl_exceeds_device_limit(fake_client, caplog):
    """Spec: an ACL longer than the ~4-entry device limit must be logged."""
    fake_client.attributes[9] = {
        "0/31/0": [ADMIN] + [{"1": 1, "2": 2, "3": [i], "4": [], "fabricIndex": 1} for i in range(1, 5)]
    }
    conn = make_conn(fake_client)
    with caplog.at_level(logging.WARNING):
        await async_apply_acl(conn, 9, 361, [20, 6], 7)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings and "9" in warnings[0].getMessage()
