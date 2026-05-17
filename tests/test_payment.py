"""Tests for x402 payment + HMAC key validation.

Mirrors milo-cost-auditor v0.1.3 security hardening tests (post Gemini audit):
  - Fail-secure: production refuses dev fallback when HMAC_KEY not set
  - DoS bound: tokens >1024 chars rejected before HMAC computation
  - Graceful non-ASCII handling
  - Per-process random dev key (no hardcoded constant)
"""

from __future__ import annotations

import pytest

from milo_usage_forecaster import payment


def test_issue_and_validate_roundtrip() -> None:
    token = payment.issue_pro_key("pro", "2099-01-01T00:00:00Z")
    v = payment.validate_pro_key(token)
    assert v.valid is True
    assert v.tier == "pro"
    assert v.expires_at == "2099-01-01T00:00:00Z"
    assert v.reason is None


def test_validate_missing_token() -> None:
    assert payment.validate_pro_key("").valid is False
    assert payment.validate_pro_key(None).valid is False  # type: ignore[arg-type]
    assert payment.validate_pro_key("garbage").valid is False


def test_validate_malformed_token() -> None:
    assert payment.validate_pro_key("nopayload.nosig").valid is False
    v = payment.validate_pro_key("only-one-part")
    assert v.reason == "malformed_token"


def test_validate_bad_signature() -> None:
    token = payment.issue_pro_key("pro", "2099-01-01T00:00:00Z")
    payload, sig = token.split(".")
    bad = f"{payload}.{'0' * len(sig)}"
    v = payment.validate_pro_key(bad)
    assert v.valid is False
    assert v.reason == "bad_signature"


def test_expired_key_rejected() -> None:
    token = payment.issue_pro_key("pro", "2020-01-01T00:00:00Z")
    v = payment.validate_pro_key(token)
    assert v.valid is False
    assert v.reason == "expired"
    assert v.tier == "pro"


def test_validate_unknown_tier(monkeypatch) -> None:
    """Forge a key with an unknown tier: should fail unknown_tier."""
    import base64, hashlib, hmac, json
    secret = payment._get_hmac_secret()
    payload = {"tier": "enterprise", "expires_at": "2099-01-01T00:00:00Z"}
    pb = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    pb64 = base64.urlsafe_b64encode(pb).rstrip(b"=").decode("ascii")
    sig = hmac.new(secret, pb64.encode("ascii"), hashlib.sha256).hexdigest()
    token = f"{pb64}.{sig}"
    v = payment.validate_pro_key(token)
    assert v.valid is False
    assert v.reason == "unknown_tier"


def test_payment_request_shape_pro() -> None:
    req = payment.build_payment_request("pro")
    assert req.http_status == 402
    assert req.currency == "USD"
    assert req.tier == "pro"
    assert req.amount_usd == 19
    assert req.payment_url.startswith("https://store-v2-khaki.vercel.app")
    assert "MILO_USAGE_FORECASTER_PRO_KEY" in req.instructions


def test_payment_request_shape_pro_year() -> None:
    req = payment.build_payment_request("pro-year")
    assert req.tier == "pro-year"
    assert req.amount_usd == 99  # annual headline price


def test_payment_request_falls_back_to_pro() -> None:
    req = payment.build_payment_request("unknown-tier-xyz")
    assert req.tier == "pro"


def test_dev_mode_detection(monkeypatch) -> None:
    monkeypatch.delenv("MILO_USAGE_FORECASTER_HMAC_KEY", raising=False)
    assert payment.is_dev_mode() is True
    monkeypatch.setenv("MILO_USAGE_FORECASTER_HMAC_KEY", "prod-key")
    assert payment.is_dev_mode() is False


def test_prod_key_doesnt_validate_against_other_secret(monkeypatch) -> None:
    """Sign with prod key A, validate with prod key B → must reject."""
    monkeypatch.setenv("MILO_USAGE_FORECASTER_HMAC_KEY", "prod-secret-aaa")
    token = payment.issue_pro_key("pro", "2099-01-01T00:00:00Z")
    monkeypatch.setenv("MILO_USAGE_FORECASTER_HMAC_KEY", "prod-secret-bbb")
    v = payment.validate_pro_key(token)
    assert v.valid is False
    assert v.reason == "bad_signature"


# ---- SECURITY HARDENING TESTS (inherited from cost-auditor v0.1.3) -----------


def test_production_refuses_dev_fallback_when_no_env(monkeypatch) -> None:
    """CRITICAL FIX: validate_pro_key must refuse if no HMAC_KEY + DEV_MODE not set.

    Previously, missing HMAC_KEY would silently use a publicly-visible dev key,
    letting anyone forge valid pro_keys with the known dev string.
    """
    monkeypatch.delenv("MILO_USAGE_FORECASTER_HMAC_KEY", raising=False)
    monkeypatch.delenv("MILO_USAGE_FORECASTER_DEV_MODE", raising=False)
    result = payment.validate_pro_key("anything.anything")
    assert result.valid is False
    assert result.reason == "server_missing_production_secret", (
        f"Expected server_missing_production_secret, got {result.reason!r}. "
        "If this fails, the CRITICAL silent-fallback vulnerability is back."
    )


def test_oversized_token_rejected(monkeypatch) -> None:
    """SECURITY: tokens >1024 chars are DoS-rejected before HMAC computation."""
    monkeypatch.setenv("MILO_USAGE_FORECASTER_HMAC_KEY", "test-secret")
    huge_token = "A" * 1100 + ".sig"  # >1024 chars
    result = payment.validate_pro_key(huge_token)
    assert result.valid is False
    assert result.reason == "token_too_large"


def test_non_ascii_token_rejected_gracefully(monkeypatch) -> None:
    """SECURITY: non-ASCII tokens don't crash the server."""
    monkeypatch.setenv("MILO_USAGE_FORECASTER_HMAC_KEY", "test-secret")
    naughty_token = "payload.☃signature"  # snowman in sig
    result = payment.validate_pro_key(naughty_token)
    assert result.valid is False
    assert result.reason == "malformed_token"


def test_dev_mode_uses_per_process_random_key(monkeypatch) -> None:
    """HIGH FIX: dev key changes between processes (no hardcoded constant)."""
    monkeypatch.delenv("MILO_USAGE_FORECASTER_HMAC_KEY", raising=False)
    monkeypatch.setenv("MILO_USAGE_FORECASTER_DEV_MODE", "1")
    secret = payment._get_hmac_secret()
    assert isinstance(secret, bytes)
    assert len(secret) >= 32, "dev key should be >=32 bytes (secrets.token_hex(32))"
    assert secret == payment._DEV_KEY
    # No hardcoded dev-string sentinels.
    assert b"DO-NOT-USE-IN-PROD" not in secret
    assert b"milo-usage-forecaster-dev-only" not in secret
