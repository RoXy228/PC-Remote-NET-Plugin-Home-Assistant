"""Connection state entities for dynamically paired PCs."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, SIGNAL_PROFILES_CHANGED


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    store = hass.data[DOMAIN]["store"]
    known: set[str] = set()

    def make_entity(profile_id: str):
        if profile_id not in store.profiles or profile_id in known:
            return None
        known.add(profile_id)
        return PCAvailable(hass, profile_id)

    initial = [
        entity
        for profile_id in store.profiles
        if (entity := make_entity(profile_id)) is not None
    ]
    async_add_entities(initial)

    @callback
    def profiles_changed(action: str, profile_id: str, is_new: bool) -> None:
        if action == "upsert":
            entity = make_entity(profile_id)
            if entity is not None:
                async_add_entities([entity])

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_PROFILES_CHANGED, profiles_changed)
    )


class PCAvailable(BinarySensorEntity):
    """Show whether the latest PC Remote command reached the PC."""

    _attr_has_entity_name = True
    _attr_name = "Связь"

    def __init__(self, hass, profile_id: str) -> None:
        self.hass = hass
        self.profile_id = profile_id
        self._attr_unique_id = f"{profile_id}_available"

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
        return self.hass.data[DOMAIN]["store"].profiles.get(self.profile_id)

    @property
    def available(self) -> bool:
        return self._profile is not None

    @property
    def is_on(self) -> bool:
        profile = self._profile
        return bool(profile and profile.available)

    @property
    def device_info(self) -> DeviceInfo:
        profile = self._profile
        return DeviceInfo(
            identifiers={(DOMAIN, self.profile_id)},
            name=profile.display_name if profile else "PC Remote",
            manufacturer="PC Remote",
        )
