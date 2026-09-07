"""Home Assistant integration entry point for PC Remote."""

from __future__ import annotations

import os
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import DOMAIN, PLATFORMS
from .http import async_setup_http
from .manager import Manager
from .storage import ProfileStore


SERVICE_EXECUTE_SCHEMA = vol.Schema(
    {
        vol.Required("command"): vol.All(str, vol.Length(min=1, max=32)),
        vol.Optional("pc_id"): vol.All(str, vol.Length(min=1, max=128)),
    }
)
SERVICE_PROFILE_SCHEMA = vol.Schema(
    {vol.Optional("pc_id"): vol.All(str, vol.Length(min=1, max=128))}
)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up storage, API, panel and services once per HA runtime."""
    store = ProfileStore(hass)
    await store.async_load()
    manager = Manager(hass, store)
    hass.data[DOMAIN] = {"store": store, "manager": manager, "challenges": {}}

    await async_setup_http(hass)
    www = os.path.join(os.path.dirname(__file__), "www")
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                "/pc_remote/panel.js",
                os.path.join(www, "panel.js"),
                cache_headers=False,
            )
        ]
    )
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path="pc-remote",
        webcomponent_name="pc-remote-panel",
        # Change the query version whenever the frontend module changes.  Home
        # Assistant keeps ES modules cached in an already-open browser tab.
        module_url="/pc_remote/panel.js?v=1.1.1",
        sidebar_title="PC Remote",
        sidebar_icon="mdi:desktop-classic",
        require_admin=True,
        config={},
        config_panel_domain=DOMAIN,
    )

    async def execute(call: ServiceCall) -> None:
        await manager.execute(call.data.get("pc_id"), call.data["command"])

    async def wake(call: ServiceCall) -> None:
        await manager.wake(call.data.get("pc_id"))

    async def cancel_timer(call: ServiceCall) -> None:
        await manager.execute(call.data.get("pc_id"), "CANCEL")

    hass.services.async_register(
        DOMAIN,
        "execute",
        execute,
        schema=SERVICE_EXECUTE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "wake",
        wake,
        schema=SERVICE_PROFILE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "cancel_timer",
        cancel_timer,
        schema=SERVICE_PROFILE_SCHEMA,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
