"""Binding and RemoteSensing operations for the sensor->TRV temperature link.

A TRV only follows an external sensor when two things are true: the sensor node's
temperature cluster is bound to the TRV (Binding cluster), and the TRV's
RemoteSensing bitmap has its bit 0 set. Binding entries arrive keyed either by TLV
number (what the Matter server returns) or by name (what tests and callers hand in),
so :func:`normalize_binding` funnels both spellings into one tuple.
"""
from __future__ import annotations

from ..const import (
    CLUSTER_BINDING,
    CLUSTER_TEMPERATURE_MEASUREMENT,
    CLUSTER_THERMOSTAT,
    TrvStatus,
)
from .client import MatterConnection, MatterConnectionError
from .discovery import MatterDevice

# Binding cluster (0x001E) attribute 0 holds the list of bindings.
_BINDING_LIST_ATTRIBUTE = 0
# Thermostat cluster (0x0201) attribute 26 is the RemoteSensing bitmap; bit 0 = local sensor.
_REMOTE_SENSING_ATTRIBUTE = 26
_REMOTE_SENSING_LOCAL = 1


def _binding_path(trv: MatterDevice) -> str:
    return f"{trv.endpoint}/{CLUSTER_BINDING}/{_BINDING_LIST_ATTRIBUTE}"


def _remote_sensing_path(trv: MatterDevice) -> str:
    return f"{trv.endpoint}/{CLUSTER_THERMOSTAT}/{_REMOTE_SENSING_ATTRIBUTE}"


def normalize_binding(entry: dict) -> tuple[int | None, int | None, int | None]:
    """Return ``(node, endpoint, cluster)``, accepting TLV ("1"/"3"/"4") and named keys."""
    return (
        entry.get("node", entry.get("1")),
        entry.get("endpoint", entry.get("3")),
        entry.get("cluster", entry.get("4")),
    )


def desired_binding(sensor: MatterDevice) -> list[dict]:
    """The Binding entry that makes the TRV read the sensor's temperature cluster."""
    return [
        {
            "node": sensor.node_id,
            "endpoint": sensor.endpoint,
            "cluster": CLUSTER_TEMPERATURE_MEASUREMENT,
        }
    ]


async def async_check_trv(
    connection: MatterConnection, trv: MatterDevice, sensor: MatterDevice
) -> TrvStatus:
    """Report the binding health of ``trv``, never raising on a connection failure."""
    try:
        await connection.async_ensure_connected()
        bindings = await connection.read_attribute(trv.node_id, _binding_path(trv)) or []
        wanted = (sensor.node_id, sensor.endpoint, CLUSTER_TEMPERATURE_MEASUREMENT)
        if not any(normalize_binding(entry) == wanted for entry in bindings):
            return TrvStatus.BINDING_MISSING
        remote_sensing = await connection.read_attribute(
            trv.node_id, _remote_sensing_path(trv)
        )
        if remote_sensing is None or not remote_sensing & _REMOTE_SENSING_LOCAL:
            return TrvStatus.REMOTE_SENSING_OFF
        return TrvStatus.BOUND
    except MatterConnectionError:
        return TrvStatus.UNREACHABLE


async def async_apply_trv(
    connection: MatterConnection, trv: MatterDevice, sensor: MatterDevice
) -> None:
    """Write the sensor binding and set the RemoteSensing bit, preserving other bits."""
    await connection.async_ensure_connected()
    await connection.send_command(
        "set_node_binding",
        node_id=trv.node_id,
        endpoint=trv.endpoint,
        bindings=desired_binding(sensor),
    )
    path = _remote_sensing_path(trv)
    value = await connection.read_attribute(trv.node_id, path) or 0
    if value & _REMOTE_SENSING_LOCAL == 0:
        await connection.write_attribute(trv.node_id, path, value | _REMOTE_SENSING_LOCAL)
