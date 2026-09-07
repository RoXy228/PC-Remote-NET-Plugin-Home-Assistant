"""Constants for the PC Remote integration."""

from typing import Final

DOMAIN: Final = "pc_remote"
PLATFORMS: Final = ["button", "sensor", "binary_sensor"]
STORAGE_VERSION: Final = 1
STORAGE_KEY: Final = "pc_remote.storage"
SIGNAL_PROFILES_CHANGED: Final = f"{DOMAIN}_profiles_changed"

COMMANDS: Final = frozenset(
    {"STATUS", "SHUTDOWN", "REBOOT", "CANCEL", "LOCK", "SCREEN_OFF"}
)
TIMER_MINUTES: Final = frozenset({15, 30, 60, 90, 120})
PAIRING_CHALLENGE_TTL: Final = 120
DEFAULT_PORT: Final = 5055
DEFAULT_WOL_PORT: Final = 9

# Voice control is intentionally kept inside the integration.  The generated
# Yandex.Station Intents package is a normal HA YAML file, but its settings and
# short-lived confirmations must never be exposed through that YAML file.
VOICE_CONFIRMATION_TTL: Final = 15
VOICE_SETTINGS_KEY: Final = "voice_settings"
VOICE_MODE_MANAGED: Final = "managed"
VOICE_MODE_MANUAL: Final = "manual"
VOICE_PACKAGE_DIRECTORY: Final = "packages"
VOICE_PACKAGE_FILENAME: Final = "pc_remote_voice.yaml"
VOICE_PACKAGE_RELATIVE_PATH: Final = (
    f"{VOICE_PACKAGE_DIRECTORY}/{VOICE_PACKAGE_FILENAME}"
)
