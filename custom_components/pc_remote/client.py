"""Client for the PC Remote.NET TCP protocol.

The Windows service speaks a small framed protocol.  Keep the wire format in
sync with ``PCRemote.Core``: a PCR2 JSON envelope, an AES-256-GCM payload
prefixed by its 12-byte nonce, and a little-endian frame length.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import time
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


ENVELOPE_PREFIX = b"PCR2"
MAX_FRAME_SIZE = 1024 * 1024
CONNECT_TIMEOUT = 10
IO_TIMEOUT = 15


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _decode_key(value: str, *, name: str, length: int | None = None) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Некорректный ключ {name}") from exc
    if length is not None and len(decoded) != length:
        raise ValueError(f"Ключ {name} имеет неверную длину")
    return decoded


def _normalise_fingerprint(value: str) -> str:
    return "".join(ch for ch in value if ch.isalnum()).upper()


def _signature_payload(
    version: int,
    timestamp: int,
    nonce: str,
    command: str,
    data: str,
    device_id: str,
    device_name: str,
    device_public_key: str,
) -> bytes:
    # Exact order used by DeviceRequestVerifier.BuildSignaturePayload.
    return "\n".join(
        (
            str(version),
            str(timestamp),
            nonce,
            command,
            data,
            device_id,
            device_name,
            device_public_key,
        )
    ).encode("utf-8")


def _encrypt(key: bytes, plain: bytes) -> bytes:
    nonce = os.urandom(12)
    # .NET CryptoService serialises nonce || ciphertext || tag.  cryptography's
    # AESGCM helper returns ciphertext || tag, so prepend the nonce explicitly.
    return nonce + AESGCM(key).encrypt(nonce, plain, None)


def _decrypt(key: bytes, encrypted: bytes) -> bytes:
    if len(encrypted) < 12 + 16:
        raise ValueError("Ответ PC Remote имеет неверный размер")
    nonce = encrypted[:12]
    return AESGCM(key).decrypt(nonce, encrypted[12:], None)


class PCRemoteClient:
    """Send authenticated commands to one paired Windows service."""

    def __init__(self, profile: Any) -> None:
        self.profile = profile

    async def command(self, command: str) -> dict[str, Any]:
        profile = self.profile
        hosts = [
            candidate.strip()
            for candidate in (profile.local_ip, profile.external_ip)
            if candidate and candidate.strip()
        ]
        if not hosts:
            raise ValueError("У профиля не указан IP-адрес компьютера")
        if not profile.device_private_key or not profile.transport_key:
            raise ValueError("Компьютер ещё не привязан к Home Assistant")
        if not profile.tls_fingerprint:
            raise ValueError("У профиля отсутствует TLS-отпечаток")

        private_bytes = _decode_key(
            profile.device_private_key, name="device_private_key"
        )
        transport_key = _decode_key(
            profile.transport_key, name="transport_key", length=32
        )
        try:
            private = serialization.load_der_private_key(private_bytes, password=None)
        except (ValueError, TypeError) as exc:
            raise ValueError("Некорректный приватный ключ профиля") from exc
        if not isinstance(private, ec.EllipticCurvePrivateKey):
            raise ValueError("Ключ профиля не является ECDSA")

        derived_public = _b64(
            private.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
        stored_public = profile.device_public_key or derived_public
        if stored_public != derived_public:
            raise ValueError("Приватный и публичный ключи профиля не совпадают")

        version = 2
        timestamp = int(time.time())
        nonce = _b64(os.urandom(18))
        data = ""
        signature = private.sign(
            _signature_payload(
                version,
                timestamp,
                nonce,
                command,
                data,
                profile.device_id,
                profile.device_name,
                stored_public,
            ),
            ec.ECDSA(hashes.SHA256()),
        )
        request = {
            "Version": version,
            "Timestamp": timestamp,
            "Nonce": nonce,
            "Command": command,
            "Data": data,
            "DeviceId": profile.device_id,
            "DeviceName": profile.device_name,
            "DevicePublicKey": stored_public,
            "Signature": _b64(signature),
        }
        envelope = {
            "Version": version,
            "DeviceId": profile.device_id,
            "PayloadBase64": _b64(
                _encrypt(
                    transport_key,
                    json.dumps(request, separators=(",", ":")).encode("utf-8"),
                )
            ),
        }
        packet = ENVELOPE_PREFIX + json.dumps(
            envelope, separators=(",", ":")
        ).encode("utf-8")
        if len(packet) > MAX_FRAME_SIZE:
            raise ValueError("Запрос PC Remote слишком большой")

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        # PC Remote creates a local self-signed certificate.  Verification is
        # performed below by the explicitly paired SHA-256 fingerprint.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        errors: list[str] = []
        for host in dict.fromkeys(hosts):
            writer: asyncio.StreamWriter | None = None
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(
                        host,
                        int(profile.port),
                        family=socket.AF_UNSPEC,
                        ssl=context,
                        server_hostname=host,
                    ),
                    timeout=CONNECT_TIMEOUT,
                )
                ssl_object = writer.get_extra_info("ssl_object")
                certificate = (
                    ssl_object.getpeercert(binary_form=True) if ssl_object else None
                )
                actual = hashlib.sha256(certificate or b"").hexdigest().upper()
                expected = _normalise_fingerprint(profile.tls_fingerprint)
                if not certificate or len(expected) != 64 or actual != expected:
                    raise ssl.SSLError("TLS-отпечаток компьютера не совпадает")

                writer.write(struct.pack("<I", len(packet)) + packet)
                await asyncio.wait_for(writer.drain(), timeout=IO_TIMEOUT)
                raw_length = await asyncio.wait_for(
                    reader.readexactly(4), timeout=IO_TIMEOUT
                )
                length = struct.unpack("<I", raw_length)[0]
                if length <= 0 or length > MAX_FRAME_SIZE:
                    raise ValueError("PC Remote вернул неверный размер ответа")
                encrypted = await asyncio.wait_for(
                    reader.readexactly(length), timeout=IO_TIMEOUT
                )
                response = json.loads(
                    _decrypt(transport_key, encrypted).decode("utf-8")
                )
                if not isinstance(response, dict):
                    raise ValueError("PC Remote вернул неверный ответ")
                if not response.get("Ok", False):
                    message = (
                        response.get("Message")
                        or response.get("ErrorCode")
                        or "команда отклонена"
                    )
                    raise RuntimeError(str(message))
                return response
            except asyncio.IncompleteReadError as exc:
                errors.append(f"{host}: PC Remote закрыл соединение до ответа")
            except Exception as exc:
                errors.append(f"{host}: {exc}")
            finally:
                if writer is not None:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except (ConnectionError, OSError):
                        pass
        raise ConnectionError("Не удалось связаться с PC Remote: " + "; ".join(errors))
