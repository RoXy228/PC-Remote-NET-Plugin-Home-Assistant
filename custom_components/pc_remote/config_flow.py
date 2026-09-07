"""Config flow for the PC Remote integration.

The integration keeps its computer profiles in one Home Assistant storage
record, therefore it must have exactly one config entry.  Older releases did
not set a unique id and allowed the user to create a second (or third) entry;
the explicit domain check below also covers those legacy entries.
"""

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult

from .const import DOMAIN


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle setup of PC Remote."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the single integration entry."""
        # ``unique_id`` was missing in the first published version, so checking
        # the domain as well is necessary to stop duplicate legacy entries.
        if any(entry.domain == DOMAIN for entry in self._async_current_entries()):
            return self.async_abort(reason="already_configured")

        if user_input is not None:
            await self.async_set_unique_id(DOMAIN, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="PC Remote", data={})

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({}),
        )
