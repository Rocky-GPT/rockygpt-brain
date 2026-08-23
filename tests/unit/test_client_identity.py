import hmac
from hashlib import sha256

from rockygpt_brain.security.client_identity import resolve_client_identity

KEY = "a" * 32


def _sign(value: str, key: str = KEY) -> str:
    return hmac.new(key.encode(), value.encode(), sha256).hexdigest()


def test_valid_signature_is_trusted() -> None:
    client_key = "abc123"
    result = resolve_client_identity(
        client_key=client_key, client_signature=_sign(client_key), abuse_hash_key=KEY
    )
    assert result.trusted
    assert result.key == client_key


def test_invalid_signature_is_untrusted() -> None:
    result = resolve_client_identity(
        client_key="abc123", client_signature="0" * 64, abuse_hash_key=KEY
    )
    assert not result.trusted


def test_missing_headers_are_untrusted_and_ephemeral() -> None:
    result = resolve_client_identity(client_key=None, client_signature=None, abuse_hash_key=KEY)
    assert not result.trusted
    assert result.key.startswith("untrusted:")


def test_two_untrusted_calls_get_different_keys() -> None:
    a = resolve_client_identity(client_key=None, client_signature=None, abuse_hash_key=KEY)
    b = resolve_client_identity(client_key=None, client_signature=None, abuse_hash_key=KEY)
    assert a.key != b.key


def test_oversized_client_key_rejected_before_hmac() -> None:
    huge_key = "a" * 200
    result = resolve_client_identity(
        client_key=huge_key, client_signature=_sign(huge_key), abuse_hash_key=KEY
    )
    assert not result.trusted


def test_malformed_client_key_charset_rejected() -> None:
    result = resolve_client_identity(
        client_key="bad key!", client_signature=_sign("bad key!"), abuse_hash_key=KEY
    )
    assert not result.trusted


def test_malformed_signature_shape_rejected() -> None:
    result = resolve_client_identity(
        client_key="abc123", client_signature="not-hex", abuse_hash_key=KEY
    )
    assert not result.trusted


def test_no_abuse_hash_key_configured_is_untrusted() -> None:
    client_key = "abc123"
    result = resolve_client_identity(
        client_key=client_key, client_signature=_sign(client_key), abuse_hash_key=None
    )
    assert not result.trusted
