"""Access-control provisioning for the sensor->TRV temperature binding.

The sensor node must be granted read access to each TRV's Temperature Measurement
cluster. ACL entries arrive keyed either by TLV number (what the Matter server
returns) or by name (what tests and callers hand in), so everything is funneled
through :func:`normalize_acl_entry` before it is inspected.
"""
from __future__ import annotations

import logging

from ..const import (
    AUTH_MODE_CASE,
    CLUSTER_ACCESS_CONTROL,
    CLUSTER_TEMPERATURE_MEASUREMENT,
    PRIVILEGE_ADMIN,
    PRIVILEGE_VIEW,
)
from .client import MatterCommandError, MatterConnection

_LOGGER = logging.getLogger(__name__)

ACL_ATTRIBUTE_PATH = f"0/{CLUSTER_ACCESS_CONTROL}/0"
DEVICE_LIMIT = 4  # the Matter SDK's usable ACL entry count on most devices


def _normalize_target(target: dict) -> dict:
    """Normalize one ACL target, accepting TLV ("0"/"1"/"2") and named keys."""
    resource: dict = {
        "cluster": target.get("cluster", target.get("0")),
        "endpoint": target.get("endpoint", target.get("1")),
    }
    device_type = target.get("device_type", target.get("deviceType", target.get("2")))
    if device_type is not None:
        resource["device_type"] = device_type
    return resource


def normalize_acl_entry(entry: dict) -> dict:
    """Return an ACL entry with named keys, regardless of how it was spelled."""
    return {
        "privilege": entry.get("privilege", entry.get("1")),
        "auth_mode": entry.get("auth_mode", entry.get("authMode", entry.get("2"))),
        "subjects": list(entry.get("subjects", entry.get("3", [])) or []),
        "targets": [_normalize_target(t) for t in (entry.get("targets", entry.get("4", [])) or [])],
    }


def acl_entry_grants(
    entry: dict, subjects: list[int], cluster: int, endpoint: int
) -> bool:
    """True iff the entry grants every requested subject access to (cluster, endpoint)."""
    normalized = normalize_acl_entry(entry)
    if not set(subjects).issubset(normalized["subjects"]):
        return False
    return any(
        target["cluster"] == cluster and target["endpoint"] == endpoint
        for target in normalized["targets"]
    )


def desired_acl_entry(
    subjects: list[int], cluster: int, endpoint: int, device_type: int
) -> dict:
    """Build the ACL entry granting ``subjects`` view access to one target.

    Both ``device_type`` and ``deviceType`` are emitted: matter.js derives a bogus
    DeviceTypeId when ``device_type`` is absent, so neither spelling is optional.
    """
    return {
        "privilege": PRIVILEGE_VIEW,
        "auth_mode": AUTH_MODE_CASE,
        "subjects": list(subjects),
        "targets": [
            {
                "cluster": cluster,
                "endpoint": endpoint,
                "device_type": device_type,
                "deviceType": device_type,
            }
        ],
    }


async def async_verify_acl(
    connection: MatterConnection,
    sensor_node_id: int,
    sensor_endpoint: int,
    trv_node_ids: list[int],
    device_type: int,
) -> bool:
    """True iff every TRV is granted the temperature cluster by some entry.

    Matter only requires each subject be granted by *an* entry, so the union across
    entries (each matching the target cluster/endpoint) is what counts.
    """
    await connection.async_ensure_connected()
    acl = await connection.read_attribute(sensor_node_id, ACL_ATTRIBUTE_PATH) or []
    granted: set[int] = set()
    for entry in acl:
        normalized = normalize_acl_entry(entry)
        if any(
            target["cluster"] == CLUSTER_TEMPERATURE_MEASUREMENT
            and target["endpoint"] == sensor_endpoint
            for target in normalized["targets"]
        ):
            granted.update(normalized["subjects"])
    return set(trv_node_ids).issubset(granted)


def _admin_entries_preserved(original: list[dict], current: list[dict]) -> bool:
    """True unless an entry that was admin in ``original`` is gone from ``current``."""
    current_normalized = [normalize_acl_entry(e) for e in current]
    return all(
        normalize_acl_entry(entry) in current_normalized
        for entry in original
        if normalize_acl_entry(entry)["privilege"] == PRIVILEGE_ADMIN
    )


# shortcut: one ACL entry per sensor endpoint per TRV-set; consolidating multiple
# endpoints into one entry (the legacy script's trick) is not implemented — few users
# hit the ~4-entry device limit with one endpoint per binding. Upgrade: group entries
# by TRV set before writing.
async def async_apply_acl(
    connection: MatterConnection,
    sensor_node_id: int,
    sensor_endpoint: int,
    trv_node_ids: list[int],
    device_type: int,
) -> None:
    """Add the binding's ACL entry, never leaving the node without admin access."""
    await connection.async_ensure_connected()
    # Copy: a server that replaces the whole list mutates in place, which would
    # otherwise clobber this snapshot before the rollback can use it.
    original = list(await connection.read_attribute(sensor_node_id, ACL_ATTRIBUTE_PATH) or [])

    await connection.send_command(
        "set_acl_entry",
        node_id=sensor_node_id,
        # Server contract: `entry` is an array of ACL entries (a bare object
        # crashes the server's entries.map), so always send a one-element list.
        entry=[
            desired_acl_entry(
                trv_node_ids, CLUSTER_TEMPERATURE_MEASUREMENT, sensor_endpoint, device_type
            )
        ],
    )
    current = await connection.read_attribute(sensor_node_id, ACL_ATTRIBUTE_PATH) or []

    if not _admin_entries_preserved(original, current):
        try:
            await connection.write_attribute(sensor_node_id, ACL_ATTRIBUTE_PATH, original)
        except Exception:  # noqa: BLE001 - rollback is best-effort; report the lockout cause
            _LOGGER.exception(
                "Failed to roll back ACL on node %s after a lockout-risk write",
                sensor_node_id,
            )
        raise MatterCommandError("ACL write would remove admin access; rolled back")

    if not await async_verify_acl(
        connection, sensor_node_id, sensor_endpoint, trv_node_ids, device_type
    ):
        raise MatterCommandError(
            f"ACL entry not present on node {sensor_node_id} after write"
        )

    if len(current) > DEVICE_LIMIT:
        _LOGGER.warning(
            "ACL on node %s holds %d entries, above the %d-entry device limit",
            sensor_node_id,
            len(current),
            DEVICE_LIMIT,
        )
