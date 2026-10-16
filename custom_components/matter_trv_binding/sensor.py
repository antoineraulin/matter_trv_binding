"""Sensors: per-TRV status, last check time, and cumulative repair count."""
from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, TrvStatus
from .coordinator import BindingCoordinator, TrvState
from .hub import get_hub_entry

PARALLEL_UPDATES = 1

_OFFLINE = (TrvStatus.UNREACHABLE, TrvStatus.DEVICE_MISSING)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the per-TRV status sensors and the binding-level sensors."""
    coordinator: BindingCoordinator = entry.runtime_data.coordinator
    entities: list[SensorEntity] = [
        TrvStatusSensor(coordinator, device_id)
        for device_id in entry.data["trv_device_ids"]
    ]
    entities.append(LastCheckSensor(coordinator))
    entities.append(RepairsSensor(coordinator))
    async_add_entities(entities)


class _BindingSensor(CoordinatorEntity[BindingCoordinator], SensorEntity):
    """Common device attachment for every binding sensor."""

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self.coordinator.entry.entry_id)})


class TrvStatusSensor(_BindingSensor):
    """Health status of one TRV's binding."""

    _attr_has_entity_name = False
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [status.value for status in TrvStatus]

    def __init__(self, coordinator: BindingCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{device_id}_status"
        self._attr_name = f"{coordinator.data.trvs[device_id].name} status"

    @property
    def _trv(self) -> TrvState:
        return self.coordinator.data.trvs[self._device_id]

    @property
    def available(self) -> bool:
        return super().available and self._trv.status not in _OFFLINE

    @property
    def native_value(self) -> str:
        return self._trv.status.value


class LastCheckSensor(_BindingSensor):
    """Timestamp of the last verify/repair cycle."""

    _attr_has_entity_name = True
    _attr_translation_key = "last_check"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: BindingCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_last_check"

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.data.last_check


class RepairsSensor(_BindingSensor):
    """Total repairs applied to this binding (cumulative)."""

    _attr_has_entity_name = True
    _attr_translation_key = "repairs"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator: BindingCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_repairs"

    @property
    def native_value(self) -> int:
        hub = get_hub_entry(self.hass)
        if hub is None or getattr(hub, "runtime_data", None) is None:
            return 0
        return hub.runtime_data.repair_counts.get(self.coordinator.entry.entry_id, 0)
