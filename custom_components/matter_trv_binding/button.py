"""Button: run an immediate verify/repair cycle for a binding."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BindingCoordinator

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the check-and-repair button."""
    coordinator: BindingCoordinator = entry.runtime_data.coordinator
    async_add_entities([CheckAndRepairButton(coordinator)])


class CheckAndRepairButton(CoordinatorEntity[BindingCoordinator], ButtonEntity):
    """Triggers one verify/repair cycle."""

    _attr_has_entity_name = True
    _attr_translation_key = "check_and_repair"

    def __init__(self, coordinator: BindingCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_check_and_repair"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self.coordinator.entry.entry_id)})

    async def async_press(self) -> None:
        await self.coordinator.async_check_and_repair()
