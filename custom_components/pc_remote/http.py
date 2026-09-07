"""Authenticated panel API and one-time pairing callback."""

from __future__ import annotations

import secrets
import time
import uuid
from urllib.parse import urlencode

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

from .const import DOMAIN, PAIRING_CHALLENGE_TTL
from .manager import Manager
from .models import Profile, SECRET_FIELDS, validate_callback_profile


def _error(message: str, status: int = 400) -> web.Response:
    return web.json_response({"error": message, "message": message}, status=status)


async def _json_body(request: web.Request) -> dict:
    try:
        body = await request.json()
    except (TypeError, ValueError, web.HTTPException) as exc:
        raise ValueError("Ожидается JSON-объект") from exc
    if not isinstance(body, dict):
        raise ValueError("Ожидается JSON-объект")
    return body


class PCRemoteView(HomeAssistantView):
    """API consumed by the local Home Assistant panel."""

    requires_auth = True
    url = "/api/pc_remote/{path:.*}"
    name = "api:pc_remote"

    @staticmethod
    def _data(request: web.Request) -> dict:
        return request.app["hass"].data[DOMAIN]

    async def get(self, request: web.Request, path: str = "", **kwargs) -> web.Response:
        data = self._data(request)
        store = data["store"]
        path = path.strip("/")
        if path == "profiles":
            return self.json(
                {
                    "profiles": [profile.as_dict(False) for profile in store.profiles.values()],
                    "default_profile_id": store.default_id,
                }
            )
        if path == "export":
            # Secret-bearing export is deliberately POST-only and requires a
            # confirmation flag.  A normal GET can safely be used for backup
            # previews and never includes credentials.
            return self.json(
                {
                    "profiles": [profile.as_dict(False) for profile in store.profiles.values()],
                    "default_profile_id": store.default_id,
                    "includes_secrets": False,
                }
            )
        if path == "alice_yaml":
            lines = [
                "# Фразы для установленного Yandex.Station Intents",
                "# Опасные действия подключите через сценарий с подтверждением.",
            ]
            for profile in store.profiles.values():
                name = profile.display_name.strip() or "Компьютер"
                lowered = name.lower()
                lines.extend(
                    (
                        f"# {name}",
                        f"- выключи {lowered}",
                        f"- перезагрузи {lowered}",
                        f"- заблокируй {lowered}",
                        f"- выключи экран {lowered}",
                        f"- включи {lowered}",
                    )
                )
            return self.json({"yaml": "\n".join(lines) + "\n"})
        return _error("Маршрут не найден", 404)

    async def post(self, request: web.Request, path: str = "", **kwargs) -> web.Response:
        hass = request.app["hass"]
        data = hass.data[DOMAIN]
        store = data["store"]
        manager: Manager = data["manager"]
        path = path.strip("/")
        try:
            body = await _json_body(request)
        except ValueError as exc:
            return _error(str(exc))

        if path == "challenge":
            now = time.time()
            challenges = data.setdefault("challenges", {})
            for old, expiry in list(challenges.items()):
                expiry_time = expiry["expires_at"] if isinstance(expiry, dict) else expiry
                if expiry_time <= now:
                    challenges.pop(old, None)
            challenge = secrets.token_urlsafe(24)
            requested_profile_id = body.get("pc_id")
            if requested_profile_id is not None and str(requested_profile_id) not in store.profiles:
                return _error("Профиль для повторной привязки не найден", 404)
            challenges[challenge] = {
                "expires_at": now + PAIRING_CHALLENGE_TTL,
                "profile_id": str(requested_profile_id) if requested_profile_id else None,
            }
            # Build the callback from the URL the authenticated browser used.
            # This works on installations where HA's external_url is unset or
            # deliberately different from the local LAN address.
            callback = (
                f"{request.scheme}://{request.host}"
                f"/api/pc_remote/callback/{challenge}"
            )
            pairing_uri = "pcremote://pair?" + urlencode(
                {"callback": callback, "challenge": challenge}
            )
            return self.json(
                {
                    "challenge": challenge,
                    "expires_in": PAIRING_CHALLENGE_TTL,
                    "pairing_uri": pairing_uri,
                }
            )

        if path == "export":
            include_secrets = body.get("include_secrets") is True
            if include_secrets and body.get("confirm") is not True:
                return _error(
                    "Для экспорта ключей требуется явное подтверждение (confirm=true)"
                )
            return self.json(
                {
                    "profiles": [
                        profile.as_dict(include_secrets)
                        for profile in store.profiles.values()
                    ],
                    "default_profile_id": store.default_id,
                    "includes_secrets": include_secrets,
                }
            )

        if path == "import":
            raw_profiles = body.get("profiles")
            if not isinstance(raw_profiles, list):
                return _error("Поле profiles должно быть массивом")
            prepared: list[tuple[Profile, bool]] = []
            try:
                for raw in raw_profiles:
                    if not isinstance(raw, dict):
                        raise ValueError("Некорректный профиль")
                    present_secrets = [
                        bool(str(raw.get(field) or "").strip()) for field in SECRET_FIELDS
                    ]
                    if any(present_secrets) and not all(present_secrets):
                        raise ValueError("Экспорт содержит неполный набор ключей")
                    if all(present_secrets):
                        validate_callback_profile(raw)
                        prepared.append((Profile.from_dict(raw), True))
                        continue
                    # A regular settings export is intentionally importable,
                    # but it must never overwrite credentials.  Existing
                    # paired profiles retain their keys; a new settings-only
                    # profile is created as an unpaired template.
                    profile_id = str(raw.get("id") or "").strip()
                    existing = store.profiles.get(profile_id)
                    if existing is not None:
                        # Work on a detached copy during validation.  If a
                        # later profile is invalid, no live profile is
                        # changed and the import remains all-or-nothing.
                        copied = Profile.from_dict(existing.as_dict(True))
                        copied.update_editable(raw)
                        prepared.append((copied, False))
                    else:
                        if not profile_id or len(profile_id) > 128:
                            raise ValueError("У профиля отсутствует корректный id")
                        prepared.append((Profile.from_dict(raw), False))
            except (TypeError, ValueError) as exc:
                return _error(f"Импорт отменён: {exc}")
            for profile, _has_secrets in prepared:
                await store.upsert(profile)
            requested_default = body.get("default_profile_id")
            if requested_default and str(requested_default) in store.profiles:
                await store.set_default(str(requested_default))
            return self.json({"ok": True, "imported": len(prepared)})

        if path == "execute":
            command = body.get("command")
            try:
                result = await manager.execute(body.get("pc_id"), command)
            except (ValueError, KeyError) as exc:
                return _error(str(exc))
            except Exception as exc:  # network/protocol error; don't expose a traceback
                return _error(str(exc) or "Не удалось выполнить команду", 502)
            return self.json({"ok": True, "response": result})

        if path == "wake":
            try:
                await manager.wake(body.get("pc_id"))
            except (ValueError, KeyError) as exc:
                return _error(str(exc))
            except Exception as exc:
                return _error(str(exc) or "Не удалось отправить Wake-on-LAN", 502)
            return self.json({"ok": True})

        if path.startswith("default/"):
            profile_id = path.split("/", 1)[1]
            try:
                await store.set_default(profile_id)
            except KeyError:
                return _error("Профиль не найден", 404)
            return self.json({"ok": True, "default_profile_id": profile_id})

        if path.startswith("profile/"):
            profile_id = path.split("/", 1)[1]
            profile = store.profiles.get(profile_id)
            if profile is None:
                return _error("Профиль не найден", 404)
            try:
                profile.update_editable(body)
            except (TypeError, ValueError) as exc:
                return _error(f"Некорректные настройки профиля: {exc}")
            await store.upsert(profile)
            return self.json(profile.as_dict(False))

        return _error("Маршрут не найден", 404)

    async def delete(self, request: web.Request, path: str = "", **kwargs) -> web.Response:
        data = self._data(request)
        path = path.strip("/")
        if not path.startswith("profile/"):
            return _error("Маршрут не найден", 404)
        profile_id = path.split("/", 1)[1]
        try:
            await data["store"].remove(profile_id)
        except KeyError:
            return _error("Профиль не найден", 404)
        return self.json({"ok": True})


class PairCallbackView(HomeAssistantView):
    """Unauthenticated, single-use callback used only during pairing."""

    requires_auth = False
    url = "/api/pc_remote/callback/{challenge}"
    name = "api:pc_remote_callback"

    async def post(
        self, request: web.Request, challenge: str = "", **kwargs
    ) -> web.Response:
        data = request.app["hass"].data[DOMAIN]
        # Pop before parsing/validating: a challenge is consumed by the first
        # callback attempt and cannot be brute-forced or replayed.
        record = data.setdefault("challenges", {}).pop(challenge, None)
        expires_at = record.get("expires_at") if isinstance(record, dict) else record
        if not expires_at or expires_at < time.time():
            return _error("Ссылка привязки истекла", 410)
        try:
            body = await _json_body(request)
            if body.get("challenge") != challenge:
                raise ValueError("challenge в callback не совпадает")
            profile_data = body.get("profile")
            validate_callback_profile(profile_data)
            # The browser/URI does not get to choose the storage identifier.
            profile_data = dict(profile_data)
            target_profile_id = record.get("profile_id") if isinstance(record, dict) else None
            if target_profile_id:
                existing = data["store"].profiles.get(target_profile_id)
                if existing is None:
                    return _error("Профиль для повторной привязки не найден", 404)
                # The bridge intentionally only sends connection credentials
                # and discovered LAN values.  Preserve the user's display
                # name and editable network/WOL choices on re-pairing.
                profile = Profile.from_dict(existing.as_dict(True))
                for field in (
                    "tls_fingerprint",
                    "device_id",
                    "device_name",
                    "device_private_key",
                    "device_public_key",
                    "transport_key",
                ):
                    setattr(profile, field, profile_data[field])
                if profile_data.get("local_ip"):
                    profile.local_ip = str(profile_data["local_ip"]).strip()
                if profile_data.get("mac"):
                    profile.mac = str(profile_data["mac"]).strip()
                if profile_data.get("port"):
                    profile.port = int(profile_data["port"])
            else:
                profile_data.setdefault("display_name", "Компьютер")
                profile_data.setdefault("external_ip", "")
                profile = Profile.from_dict(profile_data)
                profile.id = str(uuid.uuid4())
            await data["store"].upsert(profile)
            # Existing entity platforms can subscribe to this signal; panel
            # clients see the updated store immediately without a restart.
            return self.json({"ok": True, "id": profile.id})
        except (TypeError, ValueError) as exc:
            return _error(str(exc))


async def async_setup_http(hass) -> None:
    hass.http.register_view(PCRemoteView)
    hass.http.register_view(PairCallbackView)
