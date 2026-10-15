"""Strict, bounded JSON argument transport without command interpretation."""

from __future__ import annotations

import base64
import json

import pytest

from sca_usd_export.transport import MAX_PAYLOAD_BYTES, decode_args, encode_args


def _token(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")


def test_round_trip_preserves_arguments_without_shell_interpretation():
    arguments = [
        "export", "", "--help", "--config", "配置 文件.json",
        "C:\\CAD Projects\\O'Brien & Sons\\input.scadpkg",
        "C:\\输出\\result with spaces.usda", 'a "quoted" value',
        "$(Write-Output should-remain-text)", "value; second-command",
        "& | > < ^ % ! $ ` ( )", "line one\nline two", "\x00", "😀",
    ]
    encoded = encode_args(arguments)
    assert isinstance(encoded, str)
    assert encoded.isascii()
    decoded_bytes = base64.b64decode(encoded, validate=True)
    assert json.loads(decoded_bytes.decode("utf-8")) == arguments
    assert decode_args(encoded) == arguments


@pytest.mark.parametrize("value", [None, "--help", ("--help",), [], [1], [True], [None], ["ok", {}]])
def test_encode_refuses_non_list_empty_list_and_non_string_elements(value):
    with pytest.raises(ValueError):
        encode_args(value)


@pytest.mark.parametrize("value", [None, b"WyJ4Il0=", "", "not base64!", "WyJ4Il0", "WyJ4Il0=\n", "WyJ4Il0=#"])
def test_decode_refuses_non_string_and_non_strict_base64(value):
    with pytest.raises(ValueError):
        decode_args(value)


@pytest.mark.parametrize("payload", [
    b"\xff", b"[unquoted]", b"null", b"{}", b'"--help"', b"[]",
    b"[1]", b"[true]", b"[null]", b'["ok", {}]', b"[NaN]",
])
def test_decode_refuses_invalid_utf8_json_or_argument_shape(payload):
    with pytest.raises(ValueError):
        decode_args(_token(payload))


def test_payload_limit_counts_utf8_bytes_and_rejects_oversized_decode():
    assert MAX_PAYLOAD_BYTES == 65536
    with pytest.raises(ValueError):
        encode_args(["x" * MAX_PAYLOAD_BYTES])
    # Multibyte characters must count as bytes, not Python string length.
    with pytest.raises(ValueError):
        encode_args(["界" * (MAX_PAYLOAD_BYTES // 2)])
    oversized_json = b'["--help"]' + b" " * MAX_PAYLOAD_BYTES
    with pytest.raises(ValueError):
        decode_args(_token(oversized_json))


def test_single_empty_string_is_a_valid_argument():
    assert decode_args(encode_args([""])) == [""]
