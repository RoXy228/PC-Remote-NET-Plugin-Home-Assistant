"""Command and Wake-on-LAN orchestration."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .client import PCRemoteClient
from .const import COMMANDS
from .models import Profile


COMMAND_RE = re.compile(r"^(SHUTDOWN|REBOOT):(15|30|60|90|120)$")


class Manager:
    def __init__(self, hass: HomeAssistant, store: Any) -> None:
        self.hass = hass
        self.store = store

    @staticmethod
    def validate_command(command: str) -> str:
        command = str(command or "").strip().upper()
        if command in COMMANDS or COMMAND_RE.fullmatch(command):
            return command
        raise ValueError("Неподдерживаемая команда PC Remote")

    async def execute(self, profile_id: str | None, command: str) -> dict[str, Any]:
        command = self.validate_command(command)
        try:
            profile: Profile = self.store.get(profile_id)
        except KeyError as exc:
            raise HomeAssistantError(str(exc)) from exc
        try:
            result = await PCRemoteClient(profile).command(command)
        except Exception as exc:
            profile.available = False
            profile.last_error = str(exc)[:500]
            await self.store.async_save()
            self.store.notify_updated(profile.id)
            raise
        profile.available = True
        profile.last_action = command
        profile.last_error = ""
        await self.store.async_save()
        self.store.notify_updated(profile.id)
        return result

    async def wake(self, profile_id: str | None) -> None:
        try:
            profile: Profile = self.store.get(profile_id)
        except KeyError as exc:
            raise HomeAssistantError(str(exc)) from exc
        if not profile.wol_enabled:
            raise HomeAssistantError("Wake-on-LAN отключён для этого компьютера")
        if not profile.mac:
            raise HomeAssistantError("У профиля не указан MAC-адрес")
        await self.hass.services.async_call(
            "wake_on_lan",
            "send_magic_packet",
            {
                "mac": profile.mac,
                "broadcast_address": profile.broadcast_address,
                "broadcast_port": profile.wol_port,
            },
            blocking=True,
        )
        profile.last_action = "WAKE"
        profile.last_error = ""
        await self.store.async_save()
        self.store.notify_updated(profile.id)
