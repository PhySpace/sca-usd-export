"""Pass argv through SCA 2.2.4's Windows shell without quoting user paths."""

import base64
import binascii
import json

MAX_PAYLOAD_BYTES = 65536


def _validate_args(value):
    if not isinstance(value, list) or not value or any(
        not isinstance(item, str) for item in value
    ):
        raise ValueError("arguments must be a non-empty JSON array of strings")
    return value


def encode_args(arguments):
    payload = json.dumps(
        _validate_args(arguments), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError("argument payload exceeds 65536 bytes")
    return base64.b64encode(payload).decode("ascii")


def decode_args(token):
    if not isinstance(token, str) or not token or len(token) > (
        (MAX_PAYLOAD_BYTES + 2) // 3 * 4
    ):
        raise ValueError("invalid or oversized base64 argument payload")
    try:
        payload = base64.b64decode(token, validate=True)
        if len(payload) > MAX_PAYLOAD_BYTES:
            raise ValueError("argument payload exceeds 65536 bytes")
        value = json.loads(payload.decode("utf-8"))
    except (binascii.Error, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid base64 UTF-8 JSON argument payload") from exc
    return _validate_args(value)
