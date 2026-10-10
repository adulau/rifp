"""Optional RIFP object authentication using HMAC-SHA-256.

See standard/draft-dulaunoy-rifp-auth-00.md for the wire format and scope.
The MAC authenticates the descriptor's image digest, not optional JSON metadata.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import struct
from dataclasses import dataclass
from pathlib import Path

from rifp_protocol import HeaderExtension, ObjectDescriptor, ProtocolError, TLV_CRITICAL, TLV_OBJECT_AUTH

AUTH_VERSION = 1
AUTH_HMAC_SHA256 = 1
AUTH_DOMAIN = b"RIFP-OBJECT-AUTH-v1\x00"
AUTH_CONTEXT = struct.Struct(">BBQI")
KEY_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,32}\Z")


def validate_key_id(key_id: str) -> bytes:
    if not KEY_ID_PATTERN.fullmatch(key_id):
        raise ProtocolError("authentication key ID must be 1-32 ASCII letters, digits, '.', '_' or '-'")
    return key_id.encode("ascii")


def validate_key(key: bytes) -> None:
    if len(key) != 32:
        raise ProtocolError("authentication key must contain exactly 32 raw bytes")


def read_key_file(path: Path) -> bytes:
    # Bound the read and never include file contents in diagnostics.
    with path.open("rb") as stream:
        key = stream.read(33)
    validate_key(key)
    return key


@dataclass(frozen=True)
class ObjectAuthentication:
    key_id: str
    tag: bytes

    def prefix(self) -> bytes:
        key_id = validate_key_id(self.key_id)
        return bytes((AUTH_VERSION, AUTH_HMAC_SHA256, len(key_id))) + key_id

    def extension(self) -> HeaderExtension:
        if len(self.tag) != 32:
            raise ProtocolError("authentication tag must contain exactly 32 bytes")
        return HeaderExtension(TLV_CRITICAL | TLV_OBJECT_AUTH, self.prefix() + self.tag)

    @classmethod
    def decode(cls, extension: HeaderExtension) -> ObjectAuthentication:
        if extension.type_id != (TLV_CRITICAL | TLV_OBJECT_AUTH):
            raise ProtocolError("object authentication TLV must be critical")
        value = extension.value
        if len(value) < 3 or value[:2] != bytes((AUTH_VERSION, AUTH_HMAC_SHA256)):
            raise ProtocolError("unsupported authentication version or algorithm")
        length = value[2]
        if not 1 <= length <= 32 or len(value) != 3 + length + 32:
            raise ProtocolError("invalid authentication TLV length")
        try:
            key_id = value[3:3 + length].decode("ascii")
        except UnicodeDecodeError as exc:
            raise ProtocolError("invalid authentication key ID") from exc
        validate_key_id(key_id)
        return cls(key_id, value[3 + length:])

    def message(self, session_id: int, total: int, descriptor: bytes) -> bytes:
        ObjectDescriptor.decode(descriptor)
        if not 0 <= session_id <= 0xFFFFFFFFFFFFFFFF or not 1 <= total <= 0xFFFFFFFF:
            raise ProtocolError("invalid authentication session ID or chunk count")
        # RIFP 1.0 semantics, independent of advisory minor-version additions.
        return AUTH_DOMAIN + self.prefix() + AUTH_CONTEXT.pack(1, 0, session_id, total) + descriptor

    def verify(self, key: bytes, session_id: int, total: int, descriptor: bytes) -> bool:
        validate_key(key)
        expected = hmac.digest(key, self.message(session_id, total, descriptor), hashlib.sha256)
        return hmac.compare_digest(self.tag, expected)


def authenticate_descriptor(key: bytes, key_id: str, session_id: int, total: int,
                            descriptor: bytes) -> HeaderExtension:
    validate_key(key)
    auth = ObjectAuthentication(key_id, b"")
    tag = hmac.digest(key, auth.message(session_id, total, descriptor), hashlib.sha256)
    return ObjectAuthentication(key_id, tag).extension()
