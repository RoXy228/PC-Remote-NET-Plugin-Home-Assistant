"""Button entities for dynamically paired PCs."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, SIGNAL_PROFILES_CHANGED


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    store = hass.data[DOMAIN]["store"]
    manager = hass.data[DOMAIN]["manager"]
    known: set[str] = set()

    def make_entities(profile_id: str):
        profile = store.profiles.get(profile_id)
        if profile is None or profile_id in known:
            return []
        known.add(profile_id)
        actions = (
            ("wake", "Включить", None),
            ("shutdown", "Выключить", "SHUTDOWN"),
            ("reboot", "Перезагрузить", "REBOOT"),
            ("lock", "Заблокировать", "LOCK"),
            ("screen_off", "Выключить экран", "SCREEN_OFF"),
            ("cancel", "Отменить таймер", "CANCEL"),
        )
        return [PCButton(profile_id, manager, key, label, action) for key, label, action in actions]

    async_add_entities(
        [
            entity
            for profile_id in store.profiles
            for entity in make_entities(profile_id)
        ]
    )

    @callback
    def profiles_changed(action: str, profile_id: str, is_new: bool) -> None:
        if action == "upsert":
            entities = make_entities(profile_id)
            if entities:
                async_add_entities(entities)

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_PROFILES_CHANGED, profiles_changed)
    )


class PCButton(ButtonEntity):
    """A command button that always reads the current profile from the store."""

    _attr_has_entity_name = True

    def __init__(self, profile_id, manager, key, label, action) -> None:
        self.profile_id = profile_id
        self.manager = manager
        self.action = action
        self._attr_unique_id = f"{profile_id}_{key}"
        self._attr_name = label

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_PROFILES_CHANGED, self._profile_changed
            )
        )

    @callback
    def _profile_changed(self, action: str, profile_id: str, is_new: bool) -> None:
        if profile_id == self.profile_id:
            self.async_write_ha_state()

    @property
    def _profile(self):
        return self.manager.store.profiles.get(self.profile_id)

    @property
    def available(self) -> bool:
        return self._profile is not None

    @property
    def device_info(self) -> DeviceInfo:
        profile = self._profile
        return DeviceInfo(
            identifiers={(DOMAIN, self.profile_id)},
            name=profile.display_name if profile else "PC Remote",
            manufacturer="PC Remote",
        )

    async def async_press(self) -> None:
        if self.action is None:
            await self.manager.wake(self.profile_id)
        else:
            await self.manager.execute(self.profile_id, self.action)
