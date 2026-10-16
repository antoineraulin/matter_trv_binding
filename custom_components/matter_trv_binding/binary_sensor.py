"""Binary sensor: one per TRV, on when its binding to the sensor is healthy."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, TrvStatus
from .coordinator import BindingCoordinator, TrvState

PARALLEL_UPDATES = 1

# A node that cannot be reached or no longer exists has no known binding state.
_OFFLINE = (TrvStatus.UNREACHABLE, TrvStatus.DEVICE_MISSING)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add one binding-health sensor per TRV."""
    coordinator: BindingCoordinator = entry.runtime_data.coordinator
    async_add_entities(
        TrvBindingBinarySensor(coordinator, device_id)
        for device_id in entry.data["trv_device_ids"]
    )


class TrvBindingBinarySensor(CoordinatorEntity[BindingCoordinator], BinarySensorEntity):
    """Health of one TRV's binding."""

    _attr_has_entity_name = False

    def __init__(self, coordinator: BindingCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{device_id}_binding"
        self._attr_name = f"{coordinator.data.trvs[device_id].name} binding"

    @property
    def _trv(self) -> TrvState:
        return self.coordinator.data.trvs[self._device_id]

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self.coordinator.entry.entry_id)})

    @property
    def available(self) -> bool:
        return super().available and self._trv.status not in _OFFLINE

    @property
    def is_on(self) -> bool:
        return self._trv.healthy
