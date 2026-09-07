"""Data model and validation helpers for stored computer profiles."""

from __future__ import annotations

import base64
import binascii
import re
import uuid
from dataclasses import asdict, dataclass, fields
from typing import Any

from cryptography.hazmat.primitives import serialization

from .const import DEFAULT_PORT, DEFAULT_WOL_PORT


SECRET_FIELDS = frozenset(
    {"device_private_key", "device_public_key", "transport_key"}
)
EDITABLE_FIELDS = frozenset(
    {
        "display_name",
        "local_ip",
        "external_ip",
        "port",
        "mac",
        "broadcast_address",
        "wol_port",
        "wol_enabled",
    }
)
MAC_RE = re.compile(r"^(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}$", re.IGNORECASE)


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "да"}
    if value is None:
        return default
    return bool(value)


def _as_port(value: Any, default: int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return default
    return port if 1 <= port <= 65535 else default


@dataclass(slots=True)
class Profile:
    """A PC paired with this Home Assistant instance."""

    id: str
    display_name: str = "Компьютер"
    local_ip: str = ""
    external_ip: str = ""
    port: int = DEFAULT_PORT
    mac: str = ""
    broadcast_address: str = "255.255.255.255"
    wol_port: int = DEFAULT_WOL_PORT
    wol_enabled: bool = True
    tls_fingerprint: str = ""
    device_id: str = ""
    device_name: str = "Home Assistant"
    device_private_key: str = ""
    device_public_key: str = ""
    transport_key: str = ""
    is_default: bool = False
    available: bool = False
    last_action: str = ""
    last_error: str = ""

    def as_dict(self, include_secrets: bool = True) -> dict[str, Any]:
        """Serialise a profile, optionally omitting credentials."""
        result = asdict(self)
        if not include_secrets:
            for field in SECRET_FIELDS:
                result.pop(field, None)
        return result

    def update_editable(self, data: dict[str, Any]) -> None:
        """Apply fields exposed by the UI without changing credentials."""
        if "display_name" in data:
            self.display_name = str(data["display_name"] or "").strip() or "Компьютер"
        if "local_ip" in data:
            self.local_ip = str(data["local_ip"] or "").strip()
        if "external_ip" in data:
            self.external_ip = str(data["external_ip"] or "").strip()
        if "port" in data:
            self.port = _as_port(data["port"], self.port)
        if "mac" in data:
            self.mac = str(data["mac"] or "").strip()
        if "broadcast_address" in data:
            self.broadcast_address = (
                str(data["broadcast_address"] or "255.255.255.255").strip()
                or "255.255.255.255"
            )
        if "wol_port" in data:
            self.wol_port = _as_port(data["wol_port"], self.wol_port)
        if "wol_enabled" in data:
            self.wol_enabled = _as_bool(data["wol_enabled"], self.wol_enabled)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Profile":
        """Load persisted data while tolerating fields from older releases."""
        if not isinstance(data, dict):
            raise ValueError("Профиль должен быть объектом")
        field_names = {field.name for field in fields(cls)}
        values = {key: value for key, value in data.items() if key in field_names}
        values["id"] = str(values.get("id") or uuid.uuid4())
        # Normalise scalar values before constructing the dataclass.  A corrupt
        # storage file should not prevent Home Assistant from starting.
        values["display_name"] = str(values.get("display_name") or "Компьютер").strip() or "Компьютер"
        for key in ("local_ip", "external_ip", "mac", "broadcast_address", "tls_fingerprint", "device_id", "device_name", "device_private_key", "device_public_key", "transport_key", "last_action", "last_error"):
            if key in values and values[key] is not None:
                values[key] = str(values[key]).strip()
        values["port"] = _as_port(values.get("port"), DEFAULT_PORT)
        values["wol_port"] = _as_port(values.get("wol_port"), DEFAULT_WOL_PORT)
        values["wol_enabled"] = _as_bool(values.get("wol_enabled"), True)
        for key in ("is_default", "available"):
            values[key] = _as_bool(values.get(key), False)
        return cls(**values)


def validate_callback_profile(data: dict[str, Any]) -> None:
    """Validate the credential-bearing object sent by the Windows bridge."""
    if not isinstance(data, dict):
        raise ValueError("В callback отсутствует профиль")
    required = (
        "device_id",
        "device_private_key",
        "device_public_key",
        "transport_key",
        "tls_fingerprint",
    )
    missing = [key for key in required if not str(data.get(key) or "").strip()]
    if missing:
        raise ValueError(f"В callback отсутствуют поля: {', '.join(missing)}")

    fingerprint = "".join(ch for ch in str(data["tls_fingerprint"]) if ch.isalnum())
    if len(fingerprint) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in fingerprint):
        raise ValueError("TLS-отпечаток должен быть SHA-256")

    try:
        transport = base64.b64decode(str(data["transport_key"]), validate=True)
        private_bytes = base64.b64decode(str(data["device_private_key"]), validate=True)
        public_bytes = base64.b64decode(str(data["device_public_key"]), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Ключи callback должны быть Base64") from exc
    if len(transport) != 32:
        raise ValueError("transport_key должен содержать 32 байта")
    try:
        private = serialization.load_der_private_key(private_bytes, password=None)
        public = serialization.load_der_public_key(public_bytes)
    except (ValueError, TypeError) as exc:
        raise ValueError("Некорректный ключ устройства") from exc
    if not hasattr(private, "public_key") or private.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ) != public.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ):
        raise ValueError("Приватный и публичный ключи не совпадают")
    if not str(data.get("device_id") or "").strip():
        raise ValueError("Некорректный device_id")
    if "mac" in data and data["mac"] and not MAC_RE.match(str(data["mac"]).strip()):
        raise ValueError("Некорректный MAC-адрес")
    if "port" in data:
        try:
            port = int(data.get("port"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Некорректный порт PC Remote") from exc
        if not 1 <= port <= 65535:
            raise ValueError("Некорректный порт PC Remote")
    if "wol_port" in data:
        try:
            wol_port = int(data.get("wol_port"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Некорректный WOL-порт") from exc
        if not 1 <= wol_port <= 65535:
            raise ValueError("Некорректный WOL-порт")
