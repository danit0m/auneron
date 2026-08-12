import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime

from app.core.memory_errors import InvalidCursorError


CURSOR_VERSION = 1


@dataclass(frozen=True)
class DecodedMemoryCursor:
    fingerprint: str
    sort: str
    valid_at: datetime
    position: tuple[str, ...]


def _encode_base64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_base64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


class MemoryCursorCodec:
    def __init__(self, secret: str | bytes) -> None:
        encoded = secret.encode("utf-8") if isinstance(secret, str) else secret

        if len(encoded) < 32:
            raise ValueError("Cursor secret deve possuir ao menos 32 bytes.")

        self._secret = encoded

    def encode(
        self,
        *,
        fingerprint: str,
        sort: str,
        valid_at: datetime,
        position: tuple[str, ...],
    ) -> str:
        payload = {
            "a": valid_at.isoformat(),
            "f": fingerprint,
            "p": list(position),
            "s": sort,
            "v": CURSOR_VERSION,
        }
        serialized = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        signature = hmac.new(
            self._secret,
            serialized,
            hashlib.sha256,
        ).digest()

        return f"{_encode_base64(serialized)}.{_encode_base64(signature)}"

    def decode(self, cursor: str) -> DecodedMemoryCursor:
        try:
            encoded_payload, encoded_signature = cursor.split(".", 1)
            serialized = _decode_base64(encoded_payload)
            signature = _decode_base64(encoded_signature)
            expected = hmac.new(
                self._secret,
                serialized,
                hashlib.sha256,
            ).digest()

            if not hmac.compare_digest(signature, expected):
                raise ValueError("invalid signature")

            payload = json.loads(serialized)

            if set(payload) != {"a", "f", "p", "s", "v"}:
                raise ValueError("invalid payload")

            if payload["v"] != CURSOR_VERSION:
                raise ValueError("invalid version")

            if not isinstance(payload["f"], str) or not payload["f"]:
                raise ValueError("invalid fingerprint")

            if not isinstance(payload["s"], str) or not payload["s"]:
                raise ValueError("invalid sort")

            if not isinstance(payload["p"], list) or not all(
                isinstance(value, str) for value in payload["p"]
            ):
                raise ValueError("invalid position")

            valid_at = datetime.fromisoformat(payload["a"])

            if valid_at.tzinfo is None or valid_at.utcoffset() is None:
                raise ValueError("invalid valid_at")

            return DecodedMemoryCursor(
                fingerprint=payload["f"],
                sort=payload["s"],
                valid_at=valid_at,
                position=tuple(payload["p"]),
            )
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
            raise InvalidCursorError("Cursor de memória inválido.") from error
