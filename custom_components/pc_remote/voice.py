"""Voice control, confirmation and YAML package management for PC Remote.

The integration deliberately does not alter a user's existing intents.  It
owns exactly one optional package file, ``packages/pc_remote_voice.yaml``.
That file contains only PC Remote actions and can be regenerated safely from
the panel at any time.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml
from homeassistant.components import media_player
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    TIMER_MINUTES,
    VOICE_CONFIRMATION_TTL,
    VOICE_MODE_MANAGED,
    VOICE_MODE_MANUAL,
    VOICE_PACKAGE_DIRECTORY,
    VOICE_PACKAGE_FILENAME,
    VOICE_PACKAGE_RELATIVE_PATH,
)
from .manager import Manager
from .models import Profile
from .storage import ProfileStore


_PHRASE_RE: Final = re.compile(r"^[а-яёa-z0-9 ]+$", re.IGNORECASE)
_SPACE_RE: Final = re.compile(r"\s+")
# Capturing every top-level spelling is important: an inline
# ``homeassistant: !include ...`` cannot safely be edited in place, so setup
# must refuse it rather than appending a duplicate YAML key.
_HOMEASSISTANT_LINE_RE: Final = re.compile(
    # YAML permits quoted mapping keys and spaces before the colon.  Treat
    # those forms as ``homeassistant`` too; otherwise setup could append an
    # equivalent second root key and alter a user's configuration.
    r"(?m)^(?:homeassistant|['\"]homeassistant['\"])[ \t]*:[ \t]*(?P<value>[^#\r\n]*)"
)
_TOP_LEVEL_RE: Final = re.compile(r"(?m)^[^\s#][^:]*:\s*(?:#.*)?$")
_PACKAGES_RE: Final = re.compile(r"(?m)^\s+packages\s*:\s*(.*?)\s*(?:#.*)?$")
_SOURCE_TEMPLATE: Final = "{{ entity_id | default('') }}"
_ACCOUNT_TEMPLATE: Final = "{{ account | default('') }}"


class VoiceConfigurationError(HomeAssistantError):
    """The panel supplied a voice configuration that cannot be made safe."""


class VoiceSetupRequired(VoiceConfigurationError):
    """The HA packages include is not ready yet."""


@dataclass(slots=True)
class PendingConfirmation:
    """An in-memory, source-bound destructive command."""

    profile_id: str
    command: str
    expires_at: float


# Keys are part of the panel API and therefore intentionally English/stable.
VOICE_ACTIONS: Final[dict[str, dict[str, Any]]] = {
    "wake": {
        "label": "Включить (Wake-on-LAN)",
        "command": "WAKE",
        "dangerous": False,
        "requires_confirmation": False,
    },
    "lock": {
        "label": "Заблокировать",
        "command": "LOCK",
        "dangerous": False,
        "requires_confirmation": False,
    },
    "screen_off": {
        "label": "Выключить экран",
        "command": "SCREEN_OFF",
        "dangerous": False,
        "requires_confirmation": False,
    },
    "status": {
        "label": "Проверить связь",
        "command": "STATUS",
        "dangerous": False,
        "requires_confirmation": False,
    },
    "cancel": {
        "label": "Отменить таймер ПК",
        "command": "CANCEL",
        "dangerous": False,
        "requires_confirmation": False,
    },
    "shutdown": {
        "label": "Выключить",
        "command": "SHUTDOWN",
        "dangerous": True,
        "requires_confirmation": True,
    },
    "reboot": {
        "label": "Перезагрузить",
        "command": "REBOOT",
        "dangerous": True,
        "requires_confirmation": True,
    },
}

_SAFE_COMMANDS: Final = frozenset({"WAKE", "LOCK", "SCREEN_OFF", "STATUS", "CANCEL"})
_DANGEROUS_COMMAND_RE: Final = re.compile(r"^(SHUTDOWN|REBOOT)(?::(15|30|60|90|120))?$")
_ALLOWED_MANUAL_SERVICES: Final = frozenset(
    {
        "pc_remote.voice_command",
        "pc_remote.voice_confirm",
        "pc_remote.voice_cancel",
    }
)


def _bool(value: Any, default: bool = False) -> bool:
    """Read a boolean without accepting surprising truthy YAML strings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "да"}
    if value is None:
        return default
    return bool(value)


def _normalise_phrase(value: Any, *, field: str = "Фраза") -> str:
    """Match the deliberately narrow grammar of Yandex.Station Intents."""
    phrase = _SPACE_RE.sub(" ", str(value or "").strip().lower())
    if not phrase:
        raise VoiceConfigurationError(f"{field} не может быть пустой")
    if len(phrase) > 100:
        raise VoiceConfigurationError(f"{field} слишком длинная (не более 100 символов)")
    if not _PHRASE_RE.fullmatch(phrase):
        raise VoiceConfigurationError(
            f"{field} «{phrase}» содержит недопустимые символы. "
            "Разрешены только кириллица, латиница, цифры и пробелы."
        )
    return phrase


def _default_alias(profile: Profile) -> str:
    """Turn a display name into an intent-safe initial voice alias."""
    candidate = re.sub(r"[^а-яёa-z0-9 ]+", " ", profile.display_name, flags=re.IGNORECASE)
    candidate = _SPACE_RE.sub(" ", candidate).strip().lower()
    return candidate if candidate else "компьютер"


def _default_phrases(alias: str) -> dict[str, list[str]]:
    return {
        # Do not steal the very common existing intent "Включи компьютер" on
        # a first installation.  The user can deliberately choose it later
        # after removing/replacing their old direct-WOL scenario.
        "wake": [f"разбуди {alias}"],
        "lock": [f"заблокируй {alias}"],
        "screen_off": [f"выключи экран {alias}"],
        "status": [f"проверь {alias}"],
        "cancel": [f"отмени таймер {alias}"],
        "shutdown": [f"выключи {alias}"],
        "reboot": [f"перезагрузи {alias}"],
    }


def _confirmation_phrase(alias: str) -> str:
    return f"подтверждаю действие с {alias}"


def _decline_phrase(alias: str) -> str:
    return f"отменяю действие с {alias}"


class VoiceController:
    """Own voice preferences, generated YAML and station-bound confirmations."""

    def __init__(self, hass: HomeAssistant, store: ProfileStore, manager: Manager) -> None:
        self.hass = hass
        self.store = store
        self.manager = manager
        self._pending: dict[str, PendingConfirmation] = {}
        self._file_lock = asyncio.Lock()

    @property
    def _config_path(self) -> Path:
        return Path(self.hass.config.config_dir) / "configuration.yaml"

    @property
    def _package_path(self) -> Path:
        return (
            Path(self.hass.config.config_dir)
            / VOICE_PACKAGE_DIRECTORY
            / VOICE_PACKAGE_FILENAME
        )

    def _available_accounts(self) -> list[dict[str, str]]:
        """Return enabled Yandex.Station Intents accounts without any tokens."""
        available: list[dict[str, str]] = []
        seen: set[str] = set()
        for entry in self.hass.config_entries.async_entries("yandex_station_intents"):
            if getattr(entry, "disabled_by", None) is not None:
                continue
            account_id = str(getattr(entry, "unique_id", "") or "").strip()
            if not account_id or account_id in seen:
                continue
            seen.add(account_id)
            title = str(getattr(entry, "title", "") or account_id).strip() or account_id
            available.append({"id": account_id, "title": title})
        return available

    def _normalise_accounts(
        self, raw_accounts: Any, *, strict: bool = False
    ) -> list[str]:
        """Validate selected Yandex accounts against live config entries.

        A legacy installation with exactly one Yandex.Station Intents account
        gets that account selected automatically.  With zero or several
        accounts, no output is generated until the administrator chooses.
        """
        available = self._available_accounts()
        available_ids = {item["id"] for item in available}
        if raw_accounts is None:
            return [available[0]["id"]] if len(available) == 1 else []
        if not isinstance(raw_accounts, list):
            raise VoiceConfigurationError("accounts должен быть списком аккаунтов Яндекса")
        if len(raw_accounts) > len(available_ids):
            raise VoiceConfigurationError("Выбрано слишком много аккаунтов Яндекса")
        selected: list[str] = []
        for raw_account in raw_accounts:
            account_id = str(raw_account or "").strip()
            if not account_id:
                raise VoiceConfigurationError("Идентификатор аккаунта Яндекса не может быть пустым")
            if account_id in selected:
                raise VoiceConfigurationError("Один аккаунт Яндекса выбран дважды")
            if account_id not in available_ids:
                if strict:
                    raise VoiceConfigurationError(
                        "Выбранный аккаунт Яндекса не найден среди включённых "
                        "Yandex.Station Intents"
                    )
                # A removed integration entry must not leave a stale account
                # silently attached to newly generated voice scenarios.
                continue
            selected.append(account_id)
        return selected

    def _require_accounts(self, settings: dict[str, Any]) -> list[str]:
        accounts = list(settings.get("accounts") or [])
        if accounts:
            return accounts
        if not self._available_accounts():
            raise VoiceConfigurationError(
                "Не найден настроенный аккаунт Yandex.Station Intents. "
                "Сначала добавьте и включите эту интеграцию."
            )
        raise VoiceConfigurationError(
            "Выберите хотя бы один аккаунт Яндекса для голосовых сценариев."
        )

    def _settings_from_raw(
        self, raw: dict[str, Any] | None = None, *, strict_accounts: bool = False
    ) -> dict[str, Any]:
        """Validate stored or submitted settings and fill missing profile defaults."""
        raw = deepcopy(raw if raw is not None else self.store.get_voice_settings())
        if not isinstance(raw, dict):
            raw = {}

        mode = str(raw.get("mode") or VOICE_MODE_MANAGED).strip().lower()
        if mode not in {VOICE_MODE_MANAGED, VOICE_MODE_MANUAL}:
            raise VoiceConfigurationError("Режим голосового управления должен быть managed или manual")

        accounts = self._normalise_accounts(
            raw.get("accounts") if "accounts" in raw else None,
            strict=strict_accounts,
        )

        raw_profiles = raw.get("profiles", [])
        if isinstance(raw_profiles, dict):
            raw_profiles = [
                {"id": profile_id, **value}
                for profile_id, value in raw_profiles.items()
                if isinstance(value, dict)
            ]
        if not isinstance(raw_profiles, list):
            raise VoiceConfigurationError("profiles должен быть списком")

        supplied: dict[str, dict[str, Any]] = {}
        for item in raw_profiles:
            if not isinstance(item, dict):
                raise VoiceConfigurationError("Каждая голосовая настройка ПК должна быть объектом")
            profile_id = str(item.get("id") or "").strip()
            if not profile_id:
                raise VoiceConfigurationError("В голосовой настройке отсутствует id компьютера")
            if profile_id in supplied:
                raise VoiceConfigurationError("Один компьютер указан в голосовых настройках дважды")
            supplied[profile_id] = item

        profiles: list[dict[str, Any]] = []
        for profile in self.store.profiles.values():
            saved = supplied.get(profile.id, {})
            alias = _normalise_phrase(
                saved.get("alias", _default_alias(profile)),
                field="Голосовой псевдоним",
            )
            enabled = _bool(saved.get("enabled"), True)
            defaults = _default_phrases(alias)
            saved_actions = saved.get("actions", {})
            if not isinstance(saved_actions, dict):
                raise VoiceConfigurationError("actions должен быть объектом")
            actions: dict[str, dict[str, Any]] = {}
            for action_key in VOICE_ACTIONS:
                saved_action = saved_actions.get(action_key, {})
                if not isinstance(saved_action, dict):
                    raise VoiceConfigurationError(f"Настройка действия {action_key} должна быть объектом")
                wake_available = bool(profile.wol_enabled and profile.mac)
                default_action_enabled = wake_available if action_key == "wake" else True
                action_enabled = _bool(
                    saved_action.get("enabled"), default_action_enabled
                )
                # Do not publish a voice intent that is guaranteed to fail.
                # When WOL is configured later it becomes selectable again;
                # the user's saved choice remains in storage.
                if action_key == "wake" and not wake_available:
                    action_enabled = False
                raw_phrases = saved_action.get("phrases", defaults[action_key])
                if isinstance(raw_phrases, str):
                    raw_phrases = [raw_phrases]
                if not isinstance(raw_phrases, list):
                    raise VoiceConfigurationError(f"Фразы действия {action_key} должны быть списком")
                if len(raw_phrases) > 8:
                    raise VoiceConfigurationError(
                        f"У действия {action_key} может быть не более 8 фраз"
                    )
                phrases: list[str] = []
                for phrase in raw_phrases:
                    normalised = _normalise_phrase(
                        phrase,
                        field=f"Фраза действия {action_key}",
                    )
                    if normalised not in phrases:
                        phrases.append(normalised)
                if enabled and action_enabled and not phrases:
                    raise VoiceConfigurationError(
                        f"Для включённого действия {action_key} нужна хотя бы одна фраза"
                    )
                action = {"enabled": action_enabled, "phrases": phrases}
                if action_key == "wake" and not wake_available:
                    action["unavailable_reason"] = (
                        "Wake-on-LAN отключён или в профиле не указан MAC-адрес"
                    )
                actions[action_key] = action
            profiles.append(
                {
                    "id": profile.id,
                    "display_name": profile.display_name,
                    "alias": alias,
                    "enabled": enabled,
                    "actions": actions,
                }
            )

        manual_yaml = raw.get("manual_yaml", "")
        if manual_yaml is None:
            manual_yaml = ""
        if not isinstance(manual_yaml, str):
            raise VoiceConfigurationError("manual_yaml должен быть строкой")
        if len(manual_yaml.encode("utf-8")) > 100_000:
            raise VoiceConfigurationError("Ручной YAML слишком большой (не более 100 КБ)")
        return {
            "mode": mode,
            "profiles": profiles,
            "manual_yaml": manual_yaml,
            "accounts": accounts,
        }

    def _settings_with_patch(self, patch: dict[str, Any]) -> dict[str, Any]:
        current = self.store.get_voice_settings()
        if not isinstance(current, dict):
            current = {}
        if "accounts" in patch:
            # Reject a forged/stale id submitted through the browser API,
            # rather than putting a scenario on an unintended account.
            self._normalise_accounts(patch["accounts"], strict=True)
        for key in ("mode", "profiles", "manual_yaml", "accounts"):
            if key in patch:
                current[key] = deepcopy(patch[key])
        return self._settings_from_raw(current)

    async def async_get_public_config(self) -> dict[str, Any]:
        settings = self._settings_from_raw()
        return {
            "profiles": deepcopy(settings["profiles"]),
            "mode": settings["mode"],
            "manual_yaml": settings["manual_yaml"],
            "accounts": list(settings["accounts"]),
            "available_accounts": self._available_accounts(),
            "setup": await self.async_get_setup_state(),
            "action_definitions": deepcopy(VOICE_ACTIONS),
        }

    async def async_update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise VoiceConfigurationError("Ожидается JSON-объект")
        settings = self._settings_with_patch(patch)
        await self.store.set_voice_settings(settings)
        return await self.async_get_public_config()

    async def async_set_manual_yaml(self, yaml_text: Any) -> dict[str, Any]:
        if not isinstance(yaml_text, str):
            raise VoiceConfigurationError("yaml должен быть строкой")
        self._validate_manual_yaml(yaml_text)
        settings = self._settings_with_patch(
            {"mode": VOICE_MODE_MANUAL, "manual_yaml": yaml_text}
        )
        await self.store.set_voice_settings(settings)
        return await self.async_get_public_config()

    async def async_preview(self, patch: dict[str, Any] | None = None) -> dict[str, Any]:
        """Render a candidate package without persisting or reloading HA."""
        patch = patch or {}
        if not isinstance(patch, dict):
            raise VoiceConfigurationError("Ожидается JSON-объект")
        settings = self._settings_with_patch(patch)
        selected_accounts = self._require_accounts(settings)
        if settings["mode"] == VOICE_MODE_MANUAL:
            document = self._manual_document_with_accounts(
                settings["manual_yaml"], selected_accounts
            )
            yaml_text = self._dump_package(document)
        else:
            yaml_text = self._managed_yaml(settings)
        return {
            "yaml": yaml_text,
            "mode": settings["mode"],
            "valid": True,
            "setup": await self.async_get_setup_state(),
        }

    def _managed_yaml(self, settings: dict[str, Any]) -> str:
        selected_accounts = self._require_accounts(settings)
        intents: dict[str, Any] = {}
        used_phrases: set[str] = set()

        def add_intent(
            phrase: str,
            service: str,
            data: dict[str, Any],
            extra_phrases: list[str] | None = None,
        ) -> None:
            phrase = _normalise_phrase(phrase)
            extras = [_normalise_phrase(item) for item in (extra_phrases or [])]
            all_phrases = [phrase, *extras]
            duplicate = next((item for item in all_phrases if item in used_phrases), None)
            if duplicate:
                raise VoiceConfigurationError(
                    f"Фраза «{duplicate}» повторяется в голосовых действиях"
                )
            used_phrases.update(all_phrases)
            action: dict[str, Any] = {"action": service, "data": data}
            intent: dict[str, Any] = {
                "accounts": list(selected_accounts),
                "action": action,
            }
            if extras:
                intent["extra_phrases"] = extras
            intents[phrase] = intent

        for profile_settings in settings["profiles"]:
            if not profile_settings["enabled"]:
                continue
            profile_id = profile_settings["id"]
            alias = profile_settings["alias"]
            has_dangerous_action = False
            for action_key, definition in VOICE_ACTIONS.items():
                action_settings = profile_settings["actions"][action_key]
                if not action_settings["enabled"]:
                    continue
                phrases = action_settings["phrases"]
                if not phrases:
                    continue
                command = definition["command"]
                add_intent(
                    phrases[0],
                    "pc_remote.voice_command",
                    self._voice_data(profile_id, command),
                    phrases[1:],
                )
                if definition["dangerous"]:
                    has_dangerous_action = True
                    # Timers stay explicit.  An alternative trigger for the
                    # base command remains an extra phrase; timers use the
                    # primary phrase so every generated command is obvious.
                    for minutes in sorted(TIMER_MINUTES):
                        add_intent(
                            f"{phrases[0]} через {minutes} минут",
                            "pc_remote.voice_command",
                            self._voice_data(profile_id, f"{command}:{minutes}"),
                        )
            if has_dangerous_action:
                add_intent(
                    _confirmation_phrase(alias),
                    "pc_remote.voice_confirm",
                    self._voice_data(profile_id),
                )
                add_intent(
                    _decline_phrase(alias),
                    "pc_remote.voice_cancel",
                    self._voice_data(profile_id),
                )

        if not intents:
            raise VoiceConfigurationError("Нет включённых голосовых действий для генерации")
        return self._dump_package({"yandex_station_intents": {"intents": intents}})

    @staticmethod
    def _voice_data(profile_id: str, command: str | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {
            "pc_id": profile_id,
            "source_entity_id": _SOURCE_TEMPLATE,
            "source_account": _ACCOUNT_TEMPLATE,
        }
        if command:
            data["command"] = command
        return data

    @staticmethod
    def _dump_package(document: dict[str, Any]) -> str:
        body = yaml.safe_dump(
            document,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
            width=120,
        )
        return (
            "# Этот файл создан и управляется интеграцией PC Remote.\n"
            "# Изменения вручную в этом файле будут перезаписаны при применении из панели.\n"
            f"# Путь: {VOICE_PACKAGE_RELATIVE_PATH}\n\n{body}"
        )

    def _validate_manual_yaml(self, yaml_text: str) -> dict[str, Any]:
        if not yaml_text.strip():
            raise VoiceConfigurationError("В ручном режиме YAML не может быть пустым")
        try:
            document = yaml.safe_load(yaml_text)
        except yaml.YAMLError as exc:
            raise VoiceConfigurationError(f"Некорректный YAML: {exc}") from exc
        if not isinstance(document, dict) or set(document) != {"yandex_station_intents"}:
            raise VoiceConfigurationError(
                "Ручной YAML должен содержать ровно один верхний раздел yandex_station_intents"
            )
        component = document["yandex_station_intents"]
        if not isinstance(component, dict) or set(component) != {"intents"}:
            raise VoiceConfigurationError(
                "В ручном YAML разрешён только раздел yandex_station_intents.intents"
            )
        intents = component["intents"]
        if not isinstance(intents, dict) or not intents:
            raise VoiceConfigurationError("yandex_station_intents.intents должен быть непустым объектом")
        if len(intents) > 150:
            raise VoiceConfigurationError("В ручном YAML допустимо не более 150 фраз PC Remote")
        seen: set[str] = set()
        for phrase, intent in intents.items():
            normalised = _normalise_phrase(phrase)
            if normalised in seen:
                raise VoiceConfigurationError(f"Фраза «{normalised}» повторяется")
            seen.add(normalised)
            if not isinstance(intent, dict) or set(intent) - {
                "action",
                "extra_phrases",
                "accounts",
            }:
                raise VoiceConfigurationError(
                    f"Интент «{normalised}» может содержать только action, extra_phrases и accounts"
                )
            action = intent.get("action")
            if not isinstance(action, dict) or set(action) - {"action", "data"}:
                raise VoiceConfigurationError(f"Интент «{normalised}» содержит некорректное action")
            if action.get("action") not in _ALLOWED_MANUAL_SERVICES:
                raise VoiceConfigurationError(
                    f"Интент «{normalised}» может вызывать только сервисы PC Remote voice_*"
                )
            action_data = action.get("data", {})
            if not isinstance(action_data, dict):
                raise VoiceConfigurationError(f"data интента «{normalised}» должен быть объектом")
            profile_id = str(action_data.get("pc_id") or "").strip()
            if not profile_id or profile_id not in self.store.profiles:
                raise VoiceConfigurationError(
                    f"Интент «{normalised}» ссылается на несуществующий компьютер"
                )
            source_entity = action_data.get("source_entity_id")
            source_account = action_data.get("source_account")
            if source_entity != _SOURCE_TEMPLATE or source_account != _ACCOUNT_TEMPLATE:
                raise VoiceConfigurationError(
                    f"Интент «{normalised}» обязан передавать source_entity_id и "
                    "source_account через шаблоны, созданные интеграцией"
                )
            service = action["action"]
            command = action_data.get("command")
            if service == "pc_remote.voice_command":
                if command is None:
                    raise VoiceConfigurationError(
                        f"Интент «{normalised}» должен содержать command"
                    )
                self._voice_command(command)
            elif command is not None:
                raise VoiceConfigurationError(
                    f"Интент «{normalised}» не должен передавать command"
                )
            extra_phrases = intent.get("extra_phrases", [])
            if isinstance(extra_phrases, str):
                extra_phrases = [extra_phrases]
            if not isinstance(extra_phrases, list) or len(extra_phrases) > 8:
                raise VoiceConfigurationError(
                    f"extra_phrases интента «{normalised}» должен быть списком до 8 фраз"
                )
            for extra in extra_phrases:
                extra_normalised = _normalise_phrase(extra)
                if extra_normalised in seen:
                    raise VoiceConfigurationError(f"Фраза «{extra_normalised}» повторяется")
                seen.add(extra_normalised)
            if "accounts" in intent:
                intent_accounts = intent["accounts"]
                if not isinstance(intent_accounts, list) or not intent_accounts:
                    raise VoiceConfigurationError(
                        f"accounts интента «{normalised}» должен быть непустым списком"
                    )
                seen_accounts: set[str] = set()
                for account in intent_accounts:
                    account_id = str(account or "").strip()
                    if not account_id or account_id in seen_accounts:
                        raise VoiceConfigurationError(
                            f"accounts интента «{normalised}» содержит некорректный аккаунт"
                        )
                    seen_accounts.add(account_id)
        return document

    def _manual_document_with_accounts(
        self, yaml_text: str, selected_accounts: list[str]
    ) -> dict[str, Any]:
        """Force global account selection into every manual intent.

        Preview output can safely be copied into the editor: a pre-existing
        ``accounts`` field is accepted, but it never overrides the panel's
        explicit account selection when the package is rendered.
        """
        document = deepcopy(self._validate_manual_yaml(yaml_text))
        intents = document["yandex_station_intents"]["intents"]
        for intent in intents.values():
            intent["accounts"] = list(selected_accounts)
        return document

    async def async_get_setup_state(self) -> dict[str, Any]:
        return await self.hass.async_add_executor_job(self._get_setup_state_sync)

    def _get_setup_state_sync(self) -> dict[str, Any]:
        path = self._config_path
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            return {
                "ready": False,
                "message": f"Не удалось прочитать configuration.yaml: {exc}",
                "yaml_path": VOICE_PACKAGE_RELATIVE_PATH,
            }
        ready, message = self._packages_ready_from_text(text)
        return {"ready": ready, "message": message, "yaml_path": VOICE_PACKAGE_RELATIVE_PATH}

    @staticmethod
    def _packages_ready_from_text(text: str) -> tuple[bool, str]:
        headings = list(_HOMEASSISTANT_LINE_RE.finditer(text))
        if len(headings) != 1:
            if not headings:
                return (
                    False,
                    "В configuration.yaml ещё не включены Home Assistant packages.",
                )
            return False, "В configuration.yaml найдено несколько разделов homeassistant."
        heading = headings[0]
        if heading.group("value").strip():
            return (
                False,
                "Раздел homeassistant задан через inline-значение или include; "
                "автонастройка не будет его изменять.",
            )
        following = _TOP_LEVEL_RE.search(text, heading.end())
        section_end = following.start() if following else len(text)
        section = text[heading.end() : section_end]
        packages = _PACKAGES_RE.search(section)
        if packages and re.search(r"!include_dir_named\s+packages\b", packages.group(1)):
            return True, "Пакеты Home Assistant включены."
        if packages:
            return (
                False,
                "Раздел homeassistant.packages уже задан другим способом; "
                "автонастройка не будет его изменять.",
            )
        return False, "В разделе homeassistant отсутствует packages: !include_dir_named packages."

    async def async_setup_packages(self, confirm: bool) -> dict[str, Any]:
        """Safely add a package include only after an explicit UI confirmation."""
        if confirm is not True:
            raise VoiceConfigurationError(
                "Для подготовки конфигурации требуется confirm=true"
            )
        async with self._file_lock:
            try:
                changed = await self.hass.async_add_executor_job(self._setup_packages_sync)
            except VoiceConfigurationError:
                raise
            except OSError as exc:
                raise VoiceConfigurationError(
                    f"Не удалось подготовить configuration.yaml: {exc}"
                ) from exc
        state = await self.async_get_setup_state()
        state["changed"] = changed
        if state["ready"]:
            state["message"] = (
                "Пакеты Home Assistant подготовлены. Сохраните голосовые настройки "
                "и нажмите «Применить»."
            )
        return state

    def _setup_packages_sync(self) -> bool:
        config_path = self._config_path
        text = config_path.read_text(encoding="utf-8")
        ready, message = self._packages_ready_from_text(text)
        if ready:
            self._package_path.parent.mkdir(parents=True, exist_ok=True)
            return False

        headings = list(_HOMEASSISTANT_LINE_RE.finditer(text))
        newline = "\r\n" if "\r\n" in text else "\n"
        if not headings:
            suffix = "" if not text or text.endswith(("\n", "\r")) else newline
            updated = (
                f"{text}{suffix}{newline}# PC Remote: отдельные голосовые сценарии.{newline}"
                f"homeassistant:{newline}"
                f"  packages: !include_dir_named packages{newline}"
            )
        elif len(headings) == 1:
            heading = headings[0]
            if heading.group("value").strip():
                raise VoiceConfigurationError(message)
            following = _TOP_LEVEL_RE.search(text, heading.end())
            section_end = following.start() if following else len(text)
            section = text[heading.end() : section_end]
            if _PACKAGES_RE.search(section):
                raise VoiceConfigurationError(message)
            insertion = f"{newline}  packages: !include_dir_named packages"
            updated = text[: heading.end()] + insertion + text[heading.end() :]
        else:
            raise VoiceConfigurationError(message)

        self._atomic_write(config_path, updated)
        self._package_path.parent.mkdir(parents=True, exist_ok=True)
        return True

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        """Write a user-visible YAML file without ever leaving a partial file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        backup = path.with_name(f"{path.name}.pc_remote_voice.bak")
        original_mode: int | None = None
        if path.exists():
            original_mode = path.stat().st_mode & 0o777
        if path.exists() and not backup.exists():
            backup.write_bytes(path.read_bytes())
            if original_mode is not None:
                os.chmod(backup, original_mode)
        temporary = path.with_name(f".{path.name}.pc_remote_voice.tmp")
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if original_mode is not None:
            os.chmod(temporary, original_mode)
        os.replace(temporary, path)

    async def async_apply(self) -> dict[str, Any]:
        """Write the owned package and reload only Yandex.Station Intents."""
        state = await self.async_get_setup_state()
        if not state["ready"]:
            raise VoiceSetupRequired(state["message"])
        settings = self._settings_from_raw()
        selected_accounts = self._require_accounts(settings)
        if settings["mode"] == VOICE_MODE_MANUAL:
            document = self._manual_document_with_accounts(
                settings["manual_yaml"], selected_accounts
            )
            yaml_text = self._dump_package(document)
        else:
            yaml_text = self._managed_yaml(settings)

        generated_titles, generated_phrases = self._intent_sets_from_yaml(yaml_text)
        # Yandex.Station Intents itself has a hard limit of 200 scenario
        # titles.  Keep the PC Remote-owned share below 150 so a normal
        # existing configuration still has room; then check the exact merged
        # count before touching the package file.
        if len(generated_titles) > 150:
            raise VoiceConfigurationError(
                "PC Remote сгенерировал более 150 сценариев. Отключите лишние "
                "действия или сократите число включённых компьютеров."
            )
        async with self._file_lock:
            existing_titles, existing_phrases = await self.hass.async_add_executor_job(
                self._existing_non_owned_intent_sets_sync
            )
            # Yandex.Station Intents rejects 200 too (its own guard is
            # ``>= 200``), so fail before replacing the owned package rather
            # than writing a configuration that cannot be loaded.
            if len(existing_titles) + len(generated_titles) >= 200:
                raise VoiceConfigurationError(
                    "После добавления PC Remote получится 200 или более сценариев "
                    "Yandex.Station Intents. Отключите часть фраз или сценариев."
                )
            collisions = sorted(generated_phrases & existing_phrases)
            if collisions:
                joined = ", ".join(f"«{phrase}»" for phrase in collisions[:5])
                raise VoiceConfigurationError(
                    "Эти фразы уже заняты существующими сценариями Yandex.Station Intents: "
                    f"{joined}. Измените фразы PC Remote или удалите старые сценарии."
                )
            try:
                await self.hass.async_add_executor_job(self._atomic_write, self._package_path, yaml_text)
            except OSError as exc:
                raise VoiceConfigurationError(
                    f"Не удалось записать {VOICE_PACKAGE_RELATIVE_PATH}: {exc}"
                ) from exc

        reloaded = False
        sync_started = False
        reload_error = ""
        try:
            if self.hass.services.has_service("yandex_station_intents", "reload"):
                await self.hass.services.async_call(
                    "yandex_station_intents", "reload", blocking=True
                )
                reloaded = True
                if self.hass.services.has_service("yandex_station_intents", "sync"):
                    await self.hass.services.async_call(
                        "yandex_station_intents", "sync", {"full": False}, blocking=True
                    )
                    sync_started = True
            else:
                reload_error = (
                    "Файл сохранён, но сервис yandex_station_intents.reload не найден. "
                    "Установите/включите Yandex.Station Intents и перезапустите Home Assistant."
                )
        except Exception as exc:  # YAML may be rejected by another integration config
            reload_error = (
                "Файл сохранён, но Yandex.Station Intents не смог перечитать конфигурацию: "
                f"{str(exc)[:300]}"
            )
        message = (
            "Голосовые сценарии применены и отправлены на синхронизацию."
            if reloaded
            else reload_error
        )
        return {
            "ok": not bool(reload_error),
            "written": True,
            "reloaded": reloaded,
            "sync_started": sync_started,
            "yaml_path": VOICE_PACKAGE_RELATIVE_PATH,
            "message": message,
        }

    def _existing_non_owned_intent_sets_sync(self) -> tuple[set[str], set[str]]:
        """Get titles/triggers, excluding the current owned package file."""
        titles: set[str] = set()
        phrases: set[str] = set()
        component = self.hass.data.get("yandex_station_intents")
        yaml_config = getattr(component, "yaml_config", {})
        if isinstance(yaml_config, dict):
            intents = yaml_config.get("intents", {})
            if isinstance(intents, dict):
                for title, config in intents.items():
                    try:
                        normalised_title = _normalise_phrase(title)
                        titles.add(normalised_title)
                        phrases.add(normalised_title)
                        if isinstance(config, dict):
                            extras = config.get("extra_phrases", [])
                            if isinstance(extras, str):
                                extras = [extras]
                            if isinstance(extras, list):
                                phrases.update(_normalise_phrase(extra) for extra in extras)
                    except VoiceConfigurationError:
                        continue
        if self._package_path.exists():
            try:
                owned_titles, owned_phrases = self._intent_sets_from_yaml(
                    self._package_path.read_text(encoding="utf-8")
                )
                titles.difference_update(owned_titles)
                phrases.difference_update(owned_phrases)
            except (OSError, VoiceConfigurationError):
                # A malformed previous owned file must not make unrelated
                # existing intent names disappear from collision detection.
                pass
        return titles, phrases

    def _intent_sets_from_yaml(self, yaml_text: str) -> tuple[set[str], set[str]]:
        document = self._validate_manual_yaml(yaml_text)
        intents = document["yandex_station_intents"]["intents"]
        titles: set[str] = set()
        phrases: set[str] = set()
        for name, config in intents.items():
            normalised_name = _normalise_phrase(name)
            titles.add(normalised_name)
            phrases.add(normalised_name)
            extras = config.get("extra_phrases", [])
            if isinstance(extras, str):
                extras = [extras]
            for phrase in extras:
                phrases.add(_normalise_phrase(phrase))
        return titles, phrases

    @staticmethod
    def _source_key(source_entity_id: Any, source_account: Any) -> str:
        entity = str(source_entity_id or "").strip().lower()
        account = str(source_account or "").strip().lower()
        # Both identifiers are included.  This prevents a confirmation from a
        # different station (or a different account) being accepted.
        return f"{account}\x1f{entity}"

    def _pending_key(self, source_entity_id: Any, source_account: Any, profile_id: str) -> str:
        return f"{self._source_key(source_entity_id, source_account)}\x1f{profile_id}"

    def _prune_pending(self) -> None:
        now = time.monotonic()
        for key, pending in list(self._pending.items()):
            if pending.expires_at <= now:
                self._pending.pop(key, None)

    def _profile(self, profile_id: str | None) -> Profile:
        try:
            return self.store.get(profile_id)
        except KeyError as exc:
            raise VoiceConfigurationError("Компьютер для голосовой команды не найден") from exc

    def _alias_for_profile(self, profile: Profile) -> str:
        """Use the saved voice alias in spoken confirmation prompts too."""
        try:
            for item in self._settings_from_raw()["profiles"]:
                if item["id"] == profile.id:
                    return item["alias"]
        except VoiceConfigurationError:
            # A transiently corrupt preference must not make an already
            # pending safety action impossible to decline.
            pass
        return _default_alias(profile)

    @staticmethod
    def _voice_command(command: Any) -> str:
        value = str(command or "").strip().upper()
        if value in _SAFE_COMMANDS or _DANGEROUS_COMMAND_RE.fullmatch(value):
            return value
        raise VoiceConfigurationError("Неподдерживаемая голосовая команда PC Remote")

    @staticmethod
    def _require_confirmation_source(
        source_entity_id: Any, source_account: Any
    ) -> tuple[str, str]:
        """Require a real Station/account pair for destructive voice flows."""
        entity_id = str(source_entity_id or "").strip()
        account = str(source_account or "").strip()
        if not entity_id.startswith("media_player.") or not account:
            raise VoiceConfigurationError(
                "Опасное голосовое действие требует источник Яндекс.Станции и аккаунт. "
                "Используйте сгенерированный сценарий PC Remote, а не ручной вызов сервиса."
            )
        return entity_id, account

    async def async_command(
        self,
        profile_id: str | None,
        command: Any,
        source_entity_id: Any = "",
        source_account: Any = "",
    ) -> dict[str, Any]:
        """Run a safe command or create a 15 second destructive-command hold."""
        self._prune_pending()
        profile = self._profile(profile_id)
        command = self._voice_command(command)
        if _DANGEROUS_COMMAND_RE.fullmatch(command):
            source_entity_id, source_account = self._require_confirmation_source(
                source_entity_id, source_account
            )
            key = self._pending_key(source_entity_id, source_account, profile.id)
            self._pending[key] = PendingConfirmation(
                profile_id=profile.id,
                command=command,
                expires_at=time.monotonic() + VOICE_CONFIRMATION_TTL,
            )
            text = (
                f"Подтвердите действие для {profile.display_name}: скажите «"
                f"{_confirmation_phrase(self._alias_for_profile(profile))}» в течение "
                f"{VOICE_CONFIRMATION_TTL} секунд."
            )
            await self._async_say(source_entity_id, text)
            return {
                "pending": True,
                "pc_id": profile.id,
                "command": command,
                "expires_in": VOICE_CONFIRMATION_TTL,
            }

        try:
            if command == "WAKE":
                await self.manager.wake(profile.id)
                text = f"Включаю {profile.display_name}."
            else:
                await self.manager.execute(profile.id, command)
                text = self._success_text(command, profile.display_name)
        except Exception as exc:
            await self._async_say(
                source_entity_id, f"Не удалось выполнить команду для {profile.display_name}."
            )
            raise VoiceConfigurationError(
                f"Не удалось выполнить команду для {profile.display_name}: {str(exc)[:300]}"
            ) from exc
        await self._async_say(source_entity_id, text)
        return {"pending": False, "pc_id": profile.id, "command": command}

    async def async_confirm(
        self,
        profile_id: str | None,
        source_entity_id: Any = "",
        source_account: Any = "",
        command: Any | None = None,
    ) -> dict[str, Any]:
        """Confirm only a pending action from this exact station/account."""
        self._prune_pending()
        profile = self._profile(profile_id)
        source_entity_id, source_account = self._require_confirmation_source(
            source_entity_id, source_account
        )
        key = self._pending_key(source_entity_id, source_account, profile.id)
        pending = self._pending.get(key)
        if pending is None:
            await self._async_say(
                source_entity_id,
                f"Для {profile.display_name} нет ожидающего подтверждения.",
            )
            raise VoiceConfigurationError("Нет ожидающего подтверждения для этого источника и компьютера")
        if command is not None and self._voice_command(command) != pending.command:
            raise VoiceConfigurationError("Команда подтверждения не совпадает с ожидающим действием")
        # Consume first: duplicate station events must never execute a second
        # shutdown/reboot if the reply reaches HA twice.
        self._pending.pop(key, None)
        try:
            await self.manager.execute(pending.profile_id, pending.command)
        except Exception as exc:
            await self._async_say(
                source_entity_id, f"Не удалось выполнить подтверждённое действие для {profile.display_name}."
            )
            raise VoiceConfigurationError(
                f"Не удалось выполнить подтверждённое действие: {str(exc)[:300]}"
            ) from exc
        await self._async_say(
            source_entity_id,
            self._success_text(pending.command, profile.display_name, confirmed=True),
        )
        return {"confirmed": True, "pc_id": profile.id, "command": pending.command}

    async def async_cancel(
        self,
        profile_id: str | None,
        source_entity_id: Any = "",
        source_account: Any = "",
    ) -> dict[str, Any]:
        """Cancel a voice confirmation; this is not the PC's timer CANCEL command."""
        self._prune_pending()
        profile = self._profile(profile_id)
        source_entity_id, source_account = self._require_confirmation_source(
            source_entity_id, source_account
        )
        key = self._pending_key(source_entity_id, source_account, profile.id)
        pending = self._pending.pop(key, None)
        if pending is None:
            await self._async_say(
                source_entity_id,
                f"Для {profile.display_name} нет ожидающего подтверждения.",
            )
            return {"cancelled": False, "pc_id": profile.id}
        await self._async_say(source_entity_id, f"Действие для {profile.display_name} отменено.")
        return {"cancelled": True, "pc_id": profile.id, "command": pending.command}

    async def _async_say(self, source_entity_id: Any, text: str) -> None:
        """Reply on the initiating Station when the integration exposes one."""
        entity_id = str(source_entity_id or "").strip()
        if not entity_id.startswith("media_player.") or self.hass.states.get(entity_id) is None:
            return
        try:
            await self.hass.services.async_call(
                media_player.DOMAIN,
                media_player.SERVICE_PLAY_MEDIA,
                {
                    ATTR_ENTITY_ID: entity_id,
                    media_player.ATTR_MEDIA_CONTENT_TYPE: "text",
                    media_player.ATTR_MEDIA_CONTENT_ID: text,
                },
                blocking=False,
            )
        except Exception:
            # Voice control must still run if one Station is temporarily
            # unavailable; the initiating action is recorded in HA's log.
            return

    @staticmethod
    def _success_text(command: str, name: str, *, confirmed: bool = False) -> str:
        suffix = "Подтверждено. " if confirmed else ""
        if command.startswith("SHUTDOWN"):
            if ":" in command:
                return f"{suffix}Выключение {name} запланировано."
            return f"{suffix}Выключаю {name}."
        if command.startswith("REBOOT"):
            if ":" in command:
                return f"{suffix}Перезагрузка {name} запланирована."
            return f"{suffix}Перезагружаю {name}."
        return {
            "LOCK": f"Блокирую {name}.",
            "SCREEN_OFF": f"Выключаю экран {name}.",
            "STATUS": f"Проверяю связь с {name}.",
            "CANCEL": f"Отменяю таймер {name}.",
        }.get(command, f"Команда для {name} выполнена.")
