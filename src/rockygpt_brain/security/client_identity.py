"""Pseudonymous client identity, trusted only after signature verification.

Per spec/system-boundaries.md: the UI computes
  client_key = hex(HMAC-SHA256(ABUSE_HASH_KEY, normalized_source_address))
  signature  = hex(HMAC-SHA256(ABUSE_HASH_KEY, client_key))
and the brain "may trust x-rockygpt-client-key only after verifying
x-rockygpt-client-signature in constant time. It must never durably store
the raw source address or the abuse identity." Neither the key nor the
signature is ever persisted here; both are used for in-request rate-limit
bucketing only.

Header shapes are enforced against the OpenAPI bounds
(spec/brain-api.openapi.yaml components.parameters.ClientKey/ClientSignature)
before any HMAC work: an oversized or malformed header is rejected as
untrusted without ever being hashed or compared.
"""

from __future__ import annotations

import hmac
import re
import secrets
from dataclasses import dataclass
from hashlib import sha256

CLIENT_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
CLIENT_SIGNATURE_PATTERN = re.compile(r"^[A-Fa-f0-9]{64}$")


@dataclass(frozen=True, slots=True)
class ClientIdentity:
    key: str
    trusted: bool


def _sign(secret: str, value: str) -> str:
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), sha256).hexdigest()


def resolve_client_identity(
    *,
    client_key: str | None,
    client_signature: str | None,
    abuse_hash_key: str | None,
) -> ClientIdentity:
    if (
        abuse_hash_key
        and client_key
        and client_signature
        and CLIENT_KEY_PATTERN.fullmatch(client_key)
        and CLIENT_SIGNATURE_PATTERN.fullmatch(client_signature)
    ):
        expected = _sign(abuse_hash_key, client_key)
        if hmac.compare_digest(expected, client_signature.lower()):
            return ClientIdentity(key=client_key, trusted=True)
    return ClientIdentity(key=f"untrusted:{secrets.token_hex(16)}", trusted=False)
