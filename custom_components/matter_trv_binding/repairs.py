"""Repairs fix flows for the Matter TRV Binding integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult


class BindingRepairFlow(RepairsFlow):
    """Offer to remove a binding entry whose Matter device disappeared."""

    def __init__(self, data: dict[str, Any]) -> None:
        """Initialize."""
        self.data = data

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm removal of the binding entry."""
        entry_id = self.data.get("entry_id")
        # HA passes the issue data ({"issue_id": ...}) to the first step as
        # user_input, so only an explicit "confirm" counts as the user's answer.
        if user_input is not None and user_input.get("confirm"):
            if entry_id:
                await self.hass.config_entries.async_remove(entry_id)
            return self.async_create_entry(data={})

        entry = self.hass.config_entries.async_get_entry(entry_id) if entry_id else None
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({vol.Required("confirm"): bool}),
            description_placeholders={
                "binding_name": (entry.title if entry else None) or entry_id or "—"
            },
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow | None:
    """Create a fix flow for a device_missing issue, None for any other."""
    if issue_id.startswith("device_missing_"):
        return BindingRepairFlow(data or {})
    return None
