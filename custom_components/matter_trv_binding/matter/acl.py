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
from . import discovery

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
    """Return an ACL entry with named keys, regardless of how it was spelled.

    ``subjects`` and ``targets`` stay ``None`` when the raw field is ``None`` or
    absent (real admin entries often omit both); an explicit empty list stays ``[]``.
    Consumers that need to iterate must coerce with ``or []``.
    """
    subjects = entry.get("subjects", entry.get("3"))
    targets = entry.get("targets", entry.get("4"))
    return {
        "privilege": entry.get("privilege", entry.get("1")),
        "auth_mode": entry.get("auth_mode", entry.get("authMode", entry.get("2"))),
        "subjects": None if subjects is None else list(subjects),
        "targets": None if targets is None else [_normalize_target(t) for t in targets],
        "fabric_index": entry.get("fabricIndex", entry.get("254")),
    }


def _to_write_entry(entry: dict, fabric_index: int) -> dict:
    """Render a normalized entry as the device's AccessControl write shape (camelCase).

    ``subjects``/``targets`` keep ``None`` so an admin entry written back is byte-for-byte
    the same as the device reported it (a device rejects or silently neuters a null-vs-[] swap).
    """
    subjects = entry["subjects"]
    targets = entry["targets"]
    return {
        "privilege": entry["privilege"],
        "authMode": entry["auth_mode"],
        "subjects": None if subjects is None else list(subjects),
        "targets": None
        if targets is None
        else [
            {
                "cluster": t.get("cluster"),
                "endpoint": t.get("endpoint"),
                "deviceType": t.get("device_type"),
            }
            for t in targets
        ],
        "fabricIndex": fabric_index,
    }


def acl_entry_grants(
    entry: dict, subjects: list[int], cluster: int, endpoint: int
) -> bool:
    """True iff the entry grants every requested subject access to (cluster, endpoint)."""
    normalized = normalize_acl_entry(entry)
    if not set(subjects).issubset(normalized["subjects"] or []):
        return False
    return any(
        target["cluster"] == cluster and target["endpoint"] == endpoint
        for target in normalized["targets"] or []
    )


def desired_acl_entry(
    subjects: list[int], cluster: int, endpoint: int, device_type: int
) -> dict:
    """Build the ACL entry granting ``subjects`` view access to one target.

    Both ``device_type`` and ``deviceType`` are emitted for cross-generation
    safety: v1.4.0's target map reads only ``device_type`` (the camelCase key is
    ignored), but an earlier matter.js generation relied on ``deviceType``. The
    requirement for this server generation is that ``device_type`` be present.
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
            for target in normalized["targets"] or []
        ):
            granted.update(normalized["subjects"] or [])
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
    """Ensure our fabric's ACL holds exactly our managed grant, keeping admin access.

    matterjs-server's ``set_acl_entry`` replaces the controller fabric's whole ACL
    (and reports write failures as data, never throwing), so the complete desired
    list for our fabric is written through the raw ``write_attribute`` path instead.
    """
    await connection.async_ensure_connected()
    # Snapshot as-read: the rollback below depends on it staying untouched.
    original = list(await connection.read_attribute(sensor_node_id, ACL_ATTRIBUTE_PATH) or [])

    our_fabric_id = (connection.server_info or {}).get("fabric_id")
    fabrics = await connection.get_matter_fabrics(sensor_node_id)
    our_index = next(
        (f.get("fabric_index") for f in fabrics if f.get("fabric_id") == our_fabric_id),
        None,
    )
    if our_index is None:
        raise MatterCommandError(
            f"could not identify this controller's fabric on node {sensor_node_id}; "
            "refusing to modify the ACL"
        )

    ours = [
        normalize_acl_entry(e)
        for e in original
        if normalize_acl_entry(e)["fabric_index"] == our_index
    ]
    # Live endpoints let us reclaim stale grants left by removed legacy scripts whose
    # target endpoint the bridge has since renumbered away (a target naming a dead
    # endpoint makes the device reject the whole write with CONSTRAINT_ERROR).
    # Best-effort: if the node cannot be scanned, skip the cleanup and still apply.
    live_endpoints: set[int] = set()
    try:
        live_endpoints = set(
            (await discovery.async_scan_node(connection, sensor_node_id)).keys()
        )
    except Exception:  # noqa: BLE001 - cleanup must never break the apply
        live_endpoints = set()

    # Replace semantics: drop any existing view grant to this sensor endpoint (e.g.
    # from an earlier TRV set) so repeat applies never accumulate stale grants. Also
    # reclaim grants to endpoints that no longer exist on the node.
    kept: list[dict] = []
    reclaimed = 0
    for e in ours:
        if e["privilege"] != PRIVILEGE_VIEW:
            kept.append(e)
            continue
        managed = [
            t
            for t in e["targets"] or []
            if t.get("cluster") == CLUSTER_TEMPERATURE_MEASUREMENT
        ]
        if any(t.get("endpoint") == sensor_endpoint for t in managed):
            continue  # replaced by the fresh grant appended below
        if live_endpoints and any(
            t.get("endpoint") not in live_endpoints for t in managed
        ):
            reclaimed += 1
            continue  # stale grant to a dead endpoint
        kept.append(e)
    if reclaimed:
        _LOGGER.info(
            "Reclaimed %d stale ACL grant(s) to dead endpoints on node %s",
            reclaimed,
            sensor_node_id,
        )
    if not any(e["privilege"] == PRIVILEGE_ADMIN for e in kept):
        raise MatterCommandError(
            "refusing ACL write: it would remove the controller's admin access"
        )

    desired = normalize_acl_entry(
        desired_acl_entry(
            trv_node_ids, CLUSTER_TEMPERATURE_MEASUREMENT, sensor_endpoint, device_type
        )
    )
    new_list = [_to_write_entry(e, our_index) for e in kept] + [
        _to_write_entry(desired, our_index)
    ]
    try:
        await connection.write_attribute(sensor_node_id, ACL_ATTRIBUTE_PATH, new_list)
    except MatterCommandError as err:
        raise MatterCommandError(
            f"{err} (ACL for this fabric on node {sensor_node_id} held {len(ours)} "
            "entries before the write)"
        ) from err

    current = await connection.read_attribute(sensor_node_id, ACL_ATTRIBUTE_PATH) or []
    if not _admin_entries_preserved(original, current):
        try:
            await connection.write_attribute(
                sensor_node_id,
                ACL_ATTRIBUTE_PATH,
                [_to_write_entry(e, our_index) for e in ours],
            )
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
