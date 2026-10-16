"""Config flow platform.

Task 8 implements the real hub and binding flows in ``MatterTrvBindingConfigFlow``.
This module exists now because HA refuses to set up a config entry of an integration
whose manifest declares ``config_flow: true`` unless the platform imports *and*
registers a flow handler for the domain (see ``ConfigEntry.async_migrate``).
"""
from homeassistant import config_entries

from .const import DOMAIN


class MatterTrvBindingConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Hub/binding config flow; steps are added in Task 8."""
