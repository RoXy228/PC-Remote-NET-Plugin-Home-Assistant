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
