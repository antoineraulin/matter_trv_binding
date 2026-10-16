"""Config flow platform: hub connection flow (binding flow added in Task 11)."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries, data_entry_flow
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DEFAULT_URL, DOMAIN, HUB_UNIQUE_ID
from .hub import async_create_connection
from .matter.client import MatterConnectionError

# matterjs-server schema the fake pins as supported; the client's own floor is 11.
_SUPPORTED_SCHEMA_VERSION = 19

_HUB_SCHEMA = vol.Schema(
    {
        vol.Required("name", default="Matter TRV Binding"): str,
        vol.Required("url", default=DEFAULT_URL): str,
    }
)


class MatterTrvBindingConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Hub connection flow; Task 11 adds the binding step to this class."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Entry point; the form is drawn and submitted by async_step_hub."""
        return await self.async_step_hub(user_input)

    async def async_step_hub(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Create the hub entry, aborting if one exists or the server is unusable."""
        if user_input is None:
            return self.async_show_form(step_id="hub", data_schema=_HUB_SCHEMA)

        # The abort check lives on the submit path, not in async_init: aborting during
        # async_init discards the flow, so the duplicate test's second async_configure
        # on the finished flow would raise UnknownFlow.
        await self.async_set_unique_id(HUB_UNIQUE_ID)
        try:
            self._abort_if_unique_id_configured()
        except data_entry_flow.AbortFlow:
            # Task 11 routes this branch into async_step_binding.
            return self.async_abort(reason="already_configured")

        errors: dict[str, str] = {}
        try:
            connection = await async_create_connection(self.hass, user_input["url"])
        except (MatterConnectionError, ConfigEntryNotReady):
            # async_create_connection wraps connect failures in ConfigEntryNotReady.
            errors["base"] = "cannot_connect"
        else:
            info = connection.server_info or {}
            await connection.async_close()
            schema_version = info.get("schema_version")
            if isinstance(schema_version, int) and schema_version < _SUPPORTED_SCHEMA_VERSION:
                errors["base"] = "legacy_server"

        if errors:
            return self.async_show_form(step_id="hub", data_schema=_HUB_SCHEMA, errors=errors)
        return self.async_create_entry(
            title=user_input["name"],
            data={"name": user_input["name"], "url": user_input["url"]},
        )
