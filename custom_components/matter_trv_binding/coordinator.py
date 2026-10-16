"""Verify/repair coordinator for one sensor->TRV binding entry.

Each update cycle re-resolves the sensor and every TRV, verifies the sensor's ACL
and each TRV's binding, repairs whatever drifted (when enabled), and reports the
result as :class:`BindingData`. A hub event for a known TRV node triggers a
debounced refresh so an out-of-band change is healed without waiting a full poll.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CLUSTER_THERMOSTAT,
    CLUSTER_TEMPERATURE_MEASUREMENT,
    DEFAULT_AUTO_REPAIR,
    DEFAULT_REPAIR_ATTEMPTS,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
    OPT_AUTO_REPAIR,
    OPT_REPAIR_ATTEMPTS,
    OPT_UPDATE_INTERVAL_MINUTES,
    TrvStatus,
)
from .hub import HubRuntimeData
from .matter.acl import async_apply_acl, async_verify_acl
from .matter.bindings import async_apply_trv, async_check_trv
from .matter.client import MatterConnectionError
from .matter.discovery import (
    DeviceMissingError,
    MatterDevice,
    NodeUnreachableError,
    async_resolve_device,
)

_LOGGER = logging.getLogger(__name__)

_NOTIFICATION_TITLE = "Matter TRV Binding"


@dataclass
class TrvState:
    """Health of one TRV's binding to the sensor."""

    device_id: str
    name: str
    node_id: int | None
    status: TrvStatus
    healthy: bool


@dataclass
class BindingData:
    """One verify/repair cycle's result for a binding entry."""

    sensor_ok: bool
    trvs: dict[str, TrvState]
    last_check: datetime | None


class BindingCoordinator(DataUpdateCoordinator[BindingData]):
    """Runs the verify/repair cycle and heals node-id changes."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, hub_entry: ConfigEntry
    ) -> None:
        options = hub_entry.options
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(
                minutes=options.get(
                    OPT_UPDATE_INTERVAL_MINUTES, DEFAULT_UPDATE_INTERVAL_MINUTES
                )
            ),
        )
        self.entry = entry
        self._auto_repair = options.get(OPT_AUTO_REPAIR, DEFAULT_AUTO_REPAIR)
        self._repair_attempts = options.get(
            OPT_REPAIR_ATTEMPTS, DEFAULT_REPAIR_ATTEMPTS
        )
        self._runtime: HubRuntimeData = hub_entry.runtime_data
        self._connection = self._runtime.connection
        self.node_changes: list[tuple[str, int, int]] = []
        self._last_node_ids: dict[str, int] = {}
        self._known_trv_nodes: set[int] = set()
        self._trv_failures: dict[str, int] = {}      # device_id -> consecutive failures
        self._binding_failures = 0                   # binding-level consecutive failures
        self._unsub = self._connection.subscribe(self._on_matter_event)
        entry.async_on_unload(self._unsub)

    async def _async_update_data(self) -> BindingData:
        """Run one verify+repair cycle over the whole binding."""
        try:
            await self._connection.async_ensure_connected()
        except MatterConnectionError as err:
            raise UpdateFailed(str(err)) from err

        entry = self.entry
        label = entry.data.get("label") or entry.title or entry.entry_id
        trv_device_ids = list(entry.data["trv_device_ids"])

        sensor: MatterDevice | None
        try:
            sensor = await async_resolve_device(
                self._connection,
                self.hass,
                entry.data["sensor_device_id"],
                CLUSTER_TEMPERATURE_MEASUREMENT,
            )
        except (DeviceMissingError, NodeUnreachableError):
            sensor = None

        resolved: dict[str, MatterDevice] = {}
        failed: dict[str, TrvStatus] = {}
        for device_id in trv_device_ids:
            try:
                resolved[device_id] = await async_resolve_device(
                    self._connection, self.hass, device_id, CLUSTER_THERMOSTAT
                )
            except DeviceMissingError:
                failed[device_id] = TrvStatus.DEVICE_MISSING
            except NodeUnreachableError:
                failed[device_id] = TrvStatus.UNREACHABLE

        # shortcut: endpoints are re-resolved every cycle (no cache) — costs a few
        # descriptor reads per poll; upgrade: cache per node_id if polling ever moves
        # below one minute.
        self._known_trv_nodes = {device.node_id for device in resolved.values()}

        if sensor is not None and resolved:
            await self._verify_acl(sensor, [d.node_id for d in resolved.values()])

        sensor_ok = sensor is not None
        trvs: dict[str, TrvState] = {}
        for device_id in trv_device_ids:
            device = resolved.get(device_id)
            if device is None:
                trvs[device_id] = await self._unresolved_state(
                    device_id, failed[device_id], label
                )
                continue
            # Device resolves again: a stale missing-device issue is no longer valid.
            ir.async_delete_issue(self.hass, DOMAIN, f"device_missing_{entry.entry_id}")
            trvs[device_id] = await self._verify_trv(device, sensor, sensor_ok)

        self._sync_repair_failed_issue(label, trvs)

        return BindingData(
            sensor_ok=sensor_ok,
            trvs=trvs,
            last_check=dt_util.utcnow(),
        )

    async def async_check_and_repair(self, force: bool = True) -> None:
        """Force one verify+repair cycle (button/service entry point)."""
        await self.async_request_refresh()

    async def _verify_acl(
        self, sensor: MatterDevice, trv_node_ids: list[int]
    ) -> None:
        device_type = sensor.device_types[0] if sensor.device_types else 0
        try:
            acl_ok = await async_verify_acl(
                self._connection,
                sensor.node_id,
                sensor.endpoint,
                trv_node_ids,
                device_type,
            )
            if not acl_ok and self._auto_repair:
                await async_apply_acl(
                    self._connection,
                    sensor.node_id,
                    sensor.endpoint,
                    trv_node_ids,
                    device_type,
                )
                acl_ok = await async_verify_acl(
                    self._connection,
                    sensor.node_id,
                    sensor.endpoint,
                    trv_node_ids,
                    device_type,
                )
        except MatterConnectionError:
            acl_ok = False
        self._binding_failures = 0 if acl_ok else self._binding_failures + 1

    async def _verify_trv(
        self, device: MatterDevice, sensor: MatterDevice | None, sensor_ok: bool
    ) -> TrvState:
        if sensor is None:
            # Without a resolved sensor the binding target is unknown; nothing can be
            # verified or repaired. sensor_ok=False already flags the root cause.
            status = TrvStatus.BINDING_MISSING
            self._trv_failures[device.device_id] = (
                self._trv_failures.get(device.device_id, 0) + 1
            )
            return TrvState(
                device.device_id,
                self._device_name(device.device_id, device),
                device.node_id,
                status,
                False,
            )

        status = await async_check_trv(self._connection, device, sensor)
        repaired = False
        if status is not TrvStatus.BOUND and self._auto_repair:
            try:
                await async_apply_trv(self._connection, device, sensor)
            except MatterConnectionError:
                status = TrvStatus.UNREACHABLE
            else:
                repaired = True
                status = await async_check_trv(self._connection, device, sensor)

        if status is TrvStatus.BOUND:
            self._trv_failures[device.device_id] = 0
            if repaired:
                self._runtime.repair_counts[self.entry.entry_id] = (
                    self._runtime.repair_counts.get(self.entry.entry_id, 0) + 1
                )
            self._track_node_change(device)
        else:
            self._trv_failures[device.device_id] = (
                self._trv_failures.get(device.device_id, 0) + 1
            )

        return TrvState(
            device.device_id,
            self._device_name(device.device_id, device),
            device.node_id,
            status,
            status is TrvStatus.BOUND and sensor_ok,
        )

    async def _unresolved_state(
        self, device_id: str, status: TrvStatus, label: str
    ) -> TrvState:
        if status is TrvStatus.DEVICE_MISSING:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                f"device_missing_{self.entry.entry_id}",
                is_fixable=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key="device_missing",
                translation_placeholders={
                    "device_name": self._device_name(device_id, None),
                    "binding_name": label,
                },
                data={"entry_id": self.entry.entry_id},
            )
        return TrvState(
            device_id, self._device_name(device_id, None), None, status, False
        )

    def _track_node_change(self, device: MatterDevice) -> None:
        notification_id = (
            f"{DOMAIN}_node_change_{self.entry.entry_id}_{device.device_id}"
        )
        # Clear any previous notice; a fresh one is created below only on a change.
        persistent_notification.async_dismiss(self.hass, notification_id)
        old = self._last_node_ids.get(device.device_id)
        if old is not None and old != device.node_id:
            self.node_changes.append((device.device_id, old, device.node_id))
            persistent_notification.async_create(
                self.hass,
                message=(
                    f"Matter node of {self._device_name(device.device_id, device)} "
                    f"changed from {old} to {device.node_id}; the binding was re-applied."
                ),
                title=_NOTIFICATION_TITLE,
                notification_id=notification_id,
            )
        self._last_node_ids[device.device_id] = device.node_id

    def _sync_repair_failed_issue(
        self, label: str, trvs: dict[str, TrvState]
    ) -> None:
        issue_id = f"repair_failed_{self.entry.entry_id}"
        failing = next(
            (s for s in trvs.values() if not s.healthy), None
        )
        reached = self._binding_failures >= self._repair_attempts or any(
            count >= self._repair_attempts for count in self._trv_failures.values()
        )
        if not reached:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        device_name = failing.name if failing is not None else label
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="repair_failed",
            translation_placeholders={
                "device_name": device_name,
                "attempts": self._repair_attempts,
            },
        )

    def _device_name(self, device_id: str, resolved: MatterDevice | None) -> str:
        if resolved is not None and resolved.name:
            return resolved.name
        entry = dr.async_get(self.hass).async_get(device_id)
        if entry is not None:
            return entry.name_by_user or entry.name or device_id
        return device_id

    @callback
    def _on_matter_event(self, event_type: str, data: dict) -> None:
        if (
            event_type == "attribute_updated"
            and data.get("node_id") in self._known_trv_nodes
        ):
            self.hass.async_create_task(self.async_request_refresh())
