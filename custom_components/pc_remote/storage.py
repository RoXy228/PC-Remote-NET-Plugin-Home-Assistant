"""Persistent profile storage."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .const import SIGNAL_PROFILES_CHANGED, STORAGE_KEY, STORAGE_VERSION
from .models import Profile


class ProfileStore:
    """Keep all PC profiles in one versioned Home Assistant Store."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store = Store(
            hass,
            STORAGE_VERSION,
            STORAGE_KEY,
            private=True,
            atomic_writes=True,
        )
        self.profiles: dict[str, Profile] = {}
        self.default_id: str | None = None

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        raw_profiles = data.get("profiles", []) if isinstance(data, dict) else []
        if not isinstance(raw_profiles, list):
            raw_profiles = []
        loaded: dict[str, Profile] = {}
        for raw in raw_profiles:
            try:
                profile = Profile.from_dict(raw)
            except (TypeError, ValueError):
                continue
            loaded[profile.id] = profile
        self.profiles = loaded
        requested_default = data.get("default_profile_id") if isinstance(data, dict) else None
        self.default_id = (
            str(requested_default)
            if requested_default and str(requested_default) in self.profiles
            else next(iter(self.profiles), None)
        )
        self._sync_default_flags()

    def _sync_default_flags(self) -> None:
        for profile in self.profiles.values():
            profile.is_default = profile.id == self.default_id

    async def async_save(self) -> None:
        await self._store.async_save(
            {
                "profiles": [profile.as_dict(True) for profile in self.profiles.values()],
                "default_profile_id": self.default_id,
            }
        )

    async def upsert(self, profile: Profile) -> None:
        if not profile.id:
            raise ValueError("У профиля отсутствует идентификатор")
        is_new = profile.id not in self.profiles
        self.profiles[profile.id] = profile
        if not self.default_id:
            self.default_id = profile.id
        self._sync_default_flags()
        await self.async_save()
        async_dispatcher_send(
            self.hass,
            SIGNAL_PROFILES_CHANGED,
            "upsert",
            profile.id,
            is_new,
        )

    async def remove(self, profile_id: str) -> None:
        if profile_id not in self.profiles:
            raise KeyError(profile_id)
        self.profiles.pop(profile_id)
        if self.default_id == profile_id:
            self.default_id = next(iter(self.profiles), None)
        self._sync_default_flags()
        await self.async_save()
        async_dispatcher_send(self.hass, SIGNAL_PROFILES_CHANGED, "remove", profile_id, False)

    async def set_default(self, profile_id: str) -> None:
        if profile_id not in self.profiles:
            raise KeyError(profile_id)
        self.default_id = profile_id
        self._sync_default_flags()
        await self.async_save()
        async_dispatcher_send(self.hass, SIGNAL_PROFILES_CHANGED, "default", profile_id, False)

    def notify_updated(self, profile_id: str) -> None:
        """Refresh entities after a command changes transient profile state."""
        async_dispatcher_send(self.hass, SIGNAL_PROFILES_CHANGED, "update", profile_id, False)

    def get(self, profile_id: str | None) -> Profile:
        selected = profile_id or self.default_id
        if not selected or selected not in self.profiles:
            raise KeyError("Нет привязанного компьютера")
        return self.profiles[selected]
