"""Audit hash chain — HMAC-SHA256 tamper-evident history of DNA versions.

Each version of a DNA carries an audit_hash computed over its content AND
the previous version's hash. This forms a chain: any retroactive edit to an
older version changes its hash, which propagates and breaks verification of
every later version. The history is tamper-evident without being encrypted.

The chain mirrors how DNA versions relate:
  - Pre-DNA:        audit_hash = H(content)
  - Complete DNA:   audit_hash = H(content + pre_dna.audit_hash)
  - Edited section: audit_hash = H(content + old_dna.audit_hash)
  - PROMOTE (future): audit_hash = H(content + dna_generale_vN.audit_hash)

The HMAC secret comes from ZEUS_AUDIT_SECRET. A default is used for dev/test
only and a warning is logged when it is in effect; production must set the
secret explicitly.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os

logger = logging.getLogger(__name__)

# Dev/test fallback. Production MUST override via ZEUS_AUDIT_SECRET.
_DEFAULT_SECRET = "zeus_foundation_default_key"


def _get_secret() -> bytes:
    """Return the HMAC secret, warning if the dev default is in use."""
    secret = os.environ.get("ZEUS_AUDIT_SECRET", _DEFAULT_SECRET)
    if secret == _DEFAULT_SECRET:
        logger.warning(
            "ZEUS_AUDIT_SECRET non impostata: uso la chiave default di dev/test. "
            "Impostare ZEUS_AUDIT_SECRET in produzione."
        )
    return secret.encode("utf-8")


def _canonicalize(payload) -> str:
    """Serialize a payload deterministically: sorted keys, UTF-8 preserved."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def compute_audit_hash(payload, previous_hash: str = "") -> str:
    """Compute the HMAC-SHA256 audit hash for a DNA payload.

    The hash binds the payload to the previous version's hash, so the chain
    is tamper-evident: changing an old version invalidates every newer hash.

    Args:
        payload: the DNA content (dict or JSON-serializable object).
        previous_hash: the audit_hash of the preceding version ("" for the
            first version in a chain, e.g. a pre-DNA).

    Returns:
        A 64-char lowercase hex string.
    """
    data = _canonicalize(payload) + previous_hash
    return hmac.new(_get_secret(), data.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_audit_hash(hash_value: str, payload, previous_hash: str = "") -> bool:
    """Verify a payload against a recorded audit hash (constant-time compare).

    Returns True only if the payload and previous_hash reproduce the recorded
    hash_value exactly — i.e. the payload has not been tampered with.
    """
    expected = compute_audit_hash(payload, previous_hash=previous_hash)
    return hmac.compare_digest(hash_value, expected)
