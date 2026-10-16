"""Config flow platform: hub connection flow and sensor->TRV binding flow."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries, data_entry_flow
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.selector import DeviceSelector

from .const import (
    CLUSTER_TEMPERATURE_MEASUREMENT,
    CLUSTER_THERMOSTAT,
    DEFAULT_URL,
    DOMAIN,
    HUB_UNIQUE_ID,
    TrvStatus,
)
from .hub import async_create_connection, get_hub_entry
from .matter.acl import async_apply_acl, async_verify_acl
from .matter.bindings import async_apply_trv, async_check_trv
from .matter.client import MatterCommandError, MatterConnectionError
from .matter.discovery import (
    DeviceMissingError,
    MatterDevice,
    NodeUnreachableError,
    async_resolve_device,
)

# matterjs-server schema the fake pins as supported; the client's own floor is 11.
_SUPPORTED_SCHEMA_VERSION = 19

# Keys of the validation result that are translation placeholders, not error ids.
_PLACEHOLDER_KEYS = ("devices", "detail")

_HUB_SCHEMA = vol.Schema(
    {
        vol.Required("name", default="Matter TRV Binding"): str,
        vol.Required("url", default=DEFAULT_URL): str,
    }
)

# R40: DeviceFilterIntegration does not exist in the installed HA (2026.2); the
# filter is a plain dict, passed as the selector's single config argument.
_MATTER_FILTER = {"filter": {"integration": "matter"}}

_BINDING_SCHEMA = vol.Schema(
    {
        vol.Optional("label"): str,
        vol.Required("sensor_device"): DeviceSelector(_MATTER_FILTER),
        vol.Required("trv_devices"): DeviceSelector({**_MATTER_FILTER, "multiple": True}),
    }
)


def _device_label(hass, device_id: str) -> str:
    """Human name of a registry device, falling back to its id."""
    entry = dr.async_get(hass).async_get(device_id)
    if entry is None:
        return device_id
    return entry.name_by_user or entry.name or device_id


class MatterTrvBindingConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Hub connection flow and the sensor->TRV binding flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Entry point: route to the binding step when a hub already exists."""
        if user_input is None:
            await self.async_set_unique_id(HUB_UNIQUE_ID)
            try:
                self._abort_if_unique_id_configured()
            except data_entry_flow.AbortFlow:
                # A hub is configured: the user is here to add a binding.
                return await self.async_step_binding()
            return self.async_show_form(step_id="hub", data_schema=_HUB_SCHEMA)
        return await self.async_step_hub(user_input)

    async def async_step_hub(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Create the hub entry, aborting into the binding flow if one exists."""
        if user_input is None:
            return self.async_show_form(step_id="hub", data_schema=_HUB_SCHEMA)

        # The abort check lives on the submit path as well: a hub may appear
        # between drawing the form and its submission.
        await self.async_set_unique_id(HUB_UNIQUE_ID)
        try:
            self._abort_if_unique_id_configured()
        except data_entry_flow.AbortFlow:
            return await self.async_step_binding()

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

    async def async_step_binding(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Pick a temperature sensor and its TRVs, then apply the binding."""
        if user_input is None:
            return self.async_show_form(step_id="binding", data_schema=_BINDING_SCHEMA)

        await self.async_set_unique_id(user_input["sensor_device"])
        self._abort_if_unique_id_configured(error="already_configured_sensor")

        errors = await self._async_validate_and_apply(user_input)
        if errors is None:
            label = user_input.get("label") or _device_label(
                self.hass, user_input["sensor_device"]
            )
            return self.async_create_entry(
                title=label,
                data={
                    "label": label,
                    "sensor_device_id": user_input["sensor_device"],
                    "trv_device_ids": list(user_input["trv_devices"]),
                },
            )

        placeholders = {key: errors.pop(key) for key in _PLACEHOLDER_KEYS if key in errors}
        return self.async_show_form(
            step_id="binding",
            data_schema=_BINDING_SCHEMA,
            errors=errors,
            description_placeholders=placeholders,
        )

    async def _async_validate_and_apply(
        self, user_input: dict[str, Any]
    ) -> dict[str, str] | None:
        """Resolve the picked devices, apply the binding, verify it.

        Returns None on success, else a dict holding the error id under "base"
        plus placeholder values under the keys in _PLACEHOLDER_KEYS.
        """
        hub = get_hub_entry(self.hass)
        if hub is None or getattr(hub, "runtime_data", None) is None:
            return {"base": "cannot_connect"}
        connection = hub.runtime_data.connection

        try:
            sensor = await async_resolve_device(
                connection,
                self.hass,
                user_input["sensor_device"],
                CLUSTER_TEMPERATURE_MEASUREMENT,
            )
        except (NodeUnreachableError, DeviceMissingError):
            return {"base": "invalid_sensor"}

        resolved_trvs: list[MatterDevice] = []
        invalid: list[str] = []
        unreachable: list[str] = []
        for device_id in user_input["trv_devices"]:
            try:
                resolved_trvs.append(
                    await async_resolve_device(
                        connection, self.hass, device_id, CLUSTER_THERMOSTAT
                    )
                )
            except DeviceMissingError:
                invalid.append(_device_label(self.hass, device_id))
            except NodeUnreachableError:
                unreachable.append(_device_label(self.hass, device_id))

        if invalid:
            return {"base": "invalid_trv", "devices": ", ".join(invalid)}
        if unreachable:
            return {"base": "unreachable", "devices": ", ".join(unreachable)}

        trv_node_ids = [trv.node_id for trv in resolved_trvs]
        device_type = sensor.device_types[0] if sensor.device_types else 0
        try:
            await async_apply_acl(
                connection, sensor.node_id, sensor.endpoint, trv_node_ids, device_type
            )
            for trv in resolved_trvs:
                await async_apply_trv(connection, trv, sensor)
            if not await async_verify_acl(
                connection, sensor.node_id, sensor.endpoint, trv_node_ids, device_type
            ):
                raise MatterCommandError("ACL not present after apply")
            for trv in resolved_trvs:
                if await async_check_trv(connection, trv, sensor) is not TrvStatus.BOUND:
                    raise MatterCommandError(f"binding not confirmed on node {trv.node_id}")
        except (MatterConnectionError, MatterCommandError) as err:
            return {"base": "apply_failed", "detail": str(err)}
        return None
