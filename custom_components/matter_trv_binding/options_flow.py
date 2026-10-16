"""Options flow for the hub entry: verification interval and repair behaviour."""
from __future__ import annotations

from typing import Any, Mapping

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    DEFAULT_AUTO_REPAIR,
    DEFAULT_REPAIR_ATTEMPTS,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
    HUB_UNIQUE_ID,
    OPT_AUTO_REPAIR,
    OPT_REPAIR_ATTEMPTS,
    OPT_UPDATE_INTERVAL_MINUTES,
)
from .hub import get_hub_entry

_OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(OPT_UPDATE_INTERVAL_MINUTES): NumberSelector(
            NumberSelectorConfig(min=5, max=1440, step=1, mode=NumberSelectorMode.BOX)
        ),
        vol.Required(OPT_AUTO_REPAIR): bool,
        vol.Required(OPT_REPAIR_ATTEMPTS): NumberSelector(
            NumberSelectorConfig(min=1, max=10, step=1, mode=NumberSelectorMode.BOX)
        ),
    }
)


class MatterTrvBindingOptionsFlowHandler(OptionsFlowWithReload):
    """Hub options; hub and bindings are reloaded in a fixed order (see below)."""

    # The flow manager still applies the new options (async_finish_flow writes them
    # with async_update_entry before checking this flag); disabling only suppresses
    # its eager async_schedule_reload, so the hub is reloaded by our ordered task
    # instead of racing the binding reloads.
    automatic_reload = False

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            # NumberSelector returns floats; store ints so placeholders and
            # diagnostics don't render "15.0".
            data = dict(user_input)
            data[OPT_UPDATE_INTERVAL_MINUTES] = int(data[OPT_UPDATE_INTERVAL_MINUTES])
            data[OPT_REPAIR_ATTEMPTS] = int(data[OPT_REPAIR_ATTEMPTS])
            return self.async_create_entry(data=data)

        options = self._entry.options
        suggested = {
            OPT_UPDATE_INTERVAL_MINUTES: options.get(
                OPT_UPDATE_INTERVAL_MINUTES, DEFAULT_UPDATE_INTERVAL_MINUTES
            ),
            OPT_AUTO_REPAIR: options.get(OPT_AUTO_REPAIR, DEFAULT_AUTO_REPAIR),
            OPT_REPAIR_ATTEMPTS: options.get(
                OPT_REPAIR_ATTEMPTS, DEFAULT_REPAIR_ATTEMPTS
            ),
        }
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(_OPTIONS_SCHEMA, suggested),
        )

    @callback
    def async_create_entry(
        self,
        *,
        title: str | None = None,
        data: Mapping[str, Any],
        description: str | None = None,
        description_placeholders: Mapping[str, str] | None = None,
    ) -> ConfigFlowResult:
        # Binding coordinators read the interval/repair options only at creation, so
        # they must reload to pick up the new values. Lazy task: the flow manager
        # applies the new options only after this returns, so an eager task would
        # reload against the old values. The task reloads the hub first, awaited, so
        # every rebuilt binding coordinator captures the hub's new runtime data.
        self.hass.async_create_task(
            self._async_reload_binding_entries(), eager_start=False
        )
        return super().async_create_entry(
            title=title,
            data=data,
            description=description,
            description_placeholders=description_placeholders,
        )

    async def _async_reload_binding_entries(self) -> None:
        # The hub goes first, awaited: a binding rebuild reads the hub's runtime data
        # at creation, so reloading it before the hub would either capture the old
        # connection or re-setup inside the hub's unloaded window (ConfigEntryNotReady).
        hub = get_hub_entry(self.hass)
        if hub is not None:
            await self.hass.config_entries.async_reload(hub.entry_id)
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.unique_id != HUB_UNIQUE_ID:
                await self.hass.config_entries.async_reload(entry.entry_id)
