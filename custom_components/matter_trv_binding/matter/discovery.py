"""Device discovery: HA device registry -> Matter node/endpoint resolution.

The HA device registry is the mapping layer between a Matter server node id and a
real device: ``deviceid_<fabric>_<node>_<postfix>`` identifiers carry the node id,
and the node itself is scanned over the connection to find which endpoint hosts
the cluster the integration needs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntry

from ..const import CLUSTER_DESCRIPTOR
from .client import MatterConnection, MatterConnectionError

_MATTER_DOMAIN = "matter"
_NODE_ID_RE = re.compile(r"^deviceid_[0-9A-F]{16}-([0-9A-F]{16})-.*$")

# Descriptor cluster (0x001D) attributes: PartsList=0, ServerList=1, DeviceTypeList=0.
_PARTS_LIST_PATH = f"0/{CLUSTER_DESCRIPTOR}/0"


class DeviceMissingError(Exception):
    """Raised when the requested device is absent from the registry or not Matter."""


class NodeUnreachableError(Exception):
    """Raised when the Matter node cannot be read or hosts no usable endpoint."""


@dataclass(frozen=True)
class MatterDevice:
    """One resolved Matter device endpoint."""

    device_id: str
    name: str
    node_id: int
    endpoint: int
    device_types: tuple[int, ...]


def parse_matter_node_id(device: DeviceEntry) -> int | None:
    """Extract the Matter node id from a registry entry, or None if it is not Matter."""
    for domain, identifier in device.identifiers:
        if domain != _MATTER_DOMAIN:
            continue
        match = _NODE_ID_RE.match(identifier)
        if match:
            return int(match.group(1), 16)
    return None


async def async_scan_node(
    connection: MatterConnection, node_id: int
) -> dict[int, tuple[set[int], tuple[int, ...]]]:
    """Read the descriptor attributes of every endpoint; returns endpoint -> (clusters, device types)."""
    await connection.async_ensure_connected()
    parts = await connection.read_attribute(node_id, _PARTS_LIST_PATH)
    if parts is None:
        raise NodeUnreachableError(f"node {node_id} did not answer PartsList")

    scanned: dict[int, tuple[set[int], tuple[int, ...]]] = {}
    for endpoint in parts:
        clusters = await connection.read_attribute(
            node_id, f"{endpoint}/{CLUSTER_DESCRIPTOR}/1"
        )
        device_types = await connection.read_attribute(
            node_id, f"{endpoint}/{CLUSTER_DESCRIPTOR}/0"
        )
        scanned[endpoint] = (
            set(clusters or ()),
            tuple(_device_type(entry) for entry in device_types or ()),
        )
    return scanned


def _device_type(entry: Any) -> int:
    # Endpoint 0's DeviceTypeList path collides with the node PartsList read above,
    # which returns plain endpoint ids; accept both shapes.
    if isinstance(entry, dict):
        return entry.get("deviceType", entry.get("device_type", 0))
    return entry


async def async_resolve_device(
    connection: MatterConnection, hass: HomeAssistant, device_id: str, wanted_cluster: int
) -> MatterDevice:
    """Resolve ``device_id`` to the lowest endpoint hosting ``wanted_cluster``."""
    entry = dr.async_get(hass).async_get(device_id)
    if entry is None:
        raise DeviceMissingError(device_id)
    node_id = parse_matter_node_id(entry)
    if node_id is None:
        raise DeviceMissingError(device_id)

    try:
        endpoints = await async_scan_node(connection, node_id)
    except MatterConnectionError as err:
        raise NodeUnreachableError(str(err)) from err

    for endpoint in sorted(endpoints):
        clusters, device_types = endpoints[endpoint]
        if wanted_cluster in clusters:
            return MatterDevice(
                device_id=entry.id,
                name=entry.name_by_user or entry.name or "",
                node_id=node_id,
                endpoint=endpoint,
                device_types=device_types,
            )
    raise NodeUnreachableError(f"node {node_id} hosts no endpoint with cluster {wanted_cluster}")
