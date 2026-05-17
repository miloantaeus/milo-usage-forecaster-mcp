"""
x402-pattern payment + HMAC pro_key validation.

Inherits ALL the v0.1.3 security hardening from milo-cost-auditor:
  - Fail-secure: no silent dev-key fallback in production
  - Per-process random dev key (changes on each server restart)
  - 1024-char token length cap (DoS bound before HMAC computation)
  - Graceful non-ASCII handling (no server crashes on naughty input)

This module does NOT process payments. It:
  1. Issues a structured 402 Payment Required response with PayPal storefront URL.
  2. Validates a signed pro_key (HMAC-SHA256 over {tier, expires_at}).

Actual money handling happens on store-v2-khaki.vercel.app via Milo's existing
PayPal direct-buy buttons. After purchase, the customer's email receives a
pro_key blob they paste into Claude Code / Cursor as the
MILO_USAGE_FORECASTER_PRO_KEY env var.

Key format:
    <base64(json({tier, expires_at}))>.<hex(HMAC-SHA256(payload, secret))>

Time is treated as ISO-8601 UTC (Zulu) strings.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets as _secrets
import time
from typing import Dict, Optional

from pydantic import BaseModel


# ---- pricing tiers (hardcoded) --------------------------------------------

TIERS: Dict[str, Dict[str, object]] = {
    "pro": {
        "price_usd_monthly": 19,
        "label": "Pro",
        "paypal_button": "https://store-v2-khaki.vercel.app/products/usage-forecaster-pro",
        "includes": (
            "optimize_recommendations unlimited + (v0.2) Slack/email weekly "
            "spend-and-spike digest"
        ),
    },
    "pro-year": {
        "price_usd_monthly": 99 / 12.0,  # ~$8.25/mo effective
        "label": "Pro-Year",
        "paypal_button": "https://store-v2-khaki.vercel.app/products/usage-forecaster-pro-year",
        "includes": "Same as Pro, billed yearly (~57% discount vs monthly)",
        "annual_price_usd": 99,
    },
}


# ---- public schemas --------------------------------------------------------


class PaymentRequest(BaseModel):
    """x402-style payment request returned when pro_key is missing or invalid."""

    http_status: int = 402
    error: str = "Payment Required"
    message: str
    amount_usd: float
    currency: str = "USD"
    tier: str
    payment_url: str
    pro_key_format: str = (
        "<base64(json({tier, expires_at}))>.<hex(HMAC-SHA256(payload, secret))>"
    )
    instructions: str


class KeyValidation(BaseModel):
    """Result of validating a pro_key."""

    valid: bool
    tier: Optional[str] = None
    expires_at: Optional[str] = None
    reason: Optional[str] = None


# ---- env / secret handling ------------------------------------------------


# SECURITY HARDENING inherited from milo-cost-auditor v0.1.3 (per Gemini audit):
# 1. CRITICAL: Silent dev-key fallback in production = anyone can forge
#    pro_keys with the publicly-visible dev key. Fixed by requiring
#    explicit MILO_USAGE_FORECASTER_DEV_MODE=1 to allow dev key.
# 2. HIGH: Static dev key replaced with per-process random — even in
#    dev mode, the key changes between server restarts.

# Per-process random dev key; not a constant. Tests can override via secret param.
# Stored as bytes since hmac.new() requires bytes for the key parameter.
_DEV_KEY = _secrets.token_hex(32).encode("utf-8")


class MissingProductionSecret(RuntimeError):
    """Raised when a production HMAC secret is required but not set."""


def _get_hmac_secret() -> bytes:
    """Return the HMAC secret.

    Production: requires MILO_USAGE_FORECASTER_HMAC_KEY env var (32+ random hex).
    Dev mode: requires MILO_USAGE_FORECASTER_DEV_MODE=1 to explicitly opt in;
              uses per-process random _DEV_KEY (changes on each server restart).
    """
    secret = os.environ.get("MILO_USAGE_FORECASTER_HMAC_KEY")
    if secret:
        return secret.encode("utf-8")
    # Fail-secure: refuse dev key unless explicitly enabled.
    if os.environ.get("MILO_USAGE_FORECASTER_DEV_MODE") == "1":
        return _DEV_KEY
    raise MissingProductionSecret(
        "MILO_USAGE_FORECASTER_HMAC_KEY not set. Production refuses dev fallback. "
        "For local development, set MILO_USAGE_FORECASTER_DEV_MODE=1 (per-process "
        "random dev key will be used)."
    )


def is_dev_mode() -> bool:
    """True when no MILO_USAGE_FORECASTER_HMAC_KEY is set AND dev mode is enabled."""
    return (
        not os.environ.get("MILO_USAGE_FORECASTER_HMAC_KEY")
        and os.environ.get("MILO_USAGE_FORECASTER_DEV_MODE") == "1"
    )


# ---- key issue + verify ---------------------------------------------------


def issue_pro_key(tier: str, expires_at_iso: str, secret: Optional[bytes] = None) -> str:
    """Mint a pro_key (mostly used by tests + storefront fulfillment script)."""
    if tier not in TIERS:
        raise ValueError(f"unknown tier: {tier!r}; valid: {list(TIERS)}")
    payload = {"tier": tier, "expires_at": expires_at_iso}
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode("ascii")
    sec = secret if secret is not None else _get_hmac_secret()
    sig = hmac.new(sec, payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def validate_pro_key(token: Optional[str], *, now_ts: Optional[float] = None) -> KeyValidation:
    """Validate a pro_key. Returns KeyValidation."""
    if not token or not isinstance(token, str):
        return KeyValidation(valid=False, reason="missing_token")
    # SECURITY: bound the token length to prevent DoS via massive HMAC inputs.
    # Legitimate keys are <512 chars.
    if len(token) > 1024:
        return KeyValidation(valid=False, reason="token_too_large")
    parts = token.split(".")
    if len(parts) != 2:
        return KeyValidation(valid=False, reason="malformed_token")
    payload_b64, sig = parts
    # SECURITY: catch non-ASCII gracefully instead of crashing server.
    try:
        payload_b64.encode("ascii")
        sig.encode("ascii")
    except UnicodeEncodeError:
        return KeyValidation(valid=False, reason="malformed_token")
    try:
        secret = _get_hmac_secret()
    except MissingProductionSecret:
        # Fail-secure: refuse all validations when production secret unconfigured.
        return KeyValidation(valid=False, reason="server_missing_production_secret")
    expected_sig = hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_sig, sig):
        return KeyValidation(valid=False, reason="bad_signature")
    # decode payload (pad b64)
    try:
        pad = "=" * (-len(payload_b64) % 4)
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + pad)
        payload = json.loads(payload_bytes)
    except Exception:
        return KeyValidation(valid=False, reason="payload_decode_failed")
    tier = payload.get("tier")
    expires_at_iso = payload.get("expires_at")
    if tier not in TIERS:
        return KeyValidation(valid=False, reason="unknown_tier")
    if not expires_at_iso:
        return KeyValidation(valid=False, reason="missing_expiry")
    # parse expiry
    try:
        exp_struct = _parse_iso8601_utc(expires_at_iso)
    except ValueError:
        return KeyValidation(valid=False, reason="bad_expiry_format")
    current = now_ts if now_ts is not None else time.time()
    if exp_struct < current:
        return KeyValidation(
            valid=False,
            tier=tier,
            expires_at=expires_at_iso,
            reason="expired",
        )
    return KeyValidation(
        valid=True,
        tier=tier,
        expires_at=expires_at_iso,
        reason=None,
    )


def _parse_iso8601_utc(iso: str) -> float:
    """Parse a Zulu ISO-8601 string into a unix timestamp."""
    s = iso.strip()
    # Accept both "2026-12-31T23:59:59Z" and "2026-12-31T23:59:59+00:00"
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    from datetime import datetime
    return datetime.fromisoformat(s).timestamp()


# ---- payment request builder ---------------------------------------------


def build_payment_request(tier: str = "pro") -> PaymentRequest:
    """Build a 402 Payment Required payload for the requested tier."""
    if tier not in TIERS:
        tier = "pro"
    t = TIERS[tier]
    # Display the actual headline price for pro vs the effective monthly for pro-year.
    if tier == "pro-year":
        display_amount = float(t.get("annual_price_usd", 99))  # type: ignore[arg-type]
        price_phrase = f"${display_amount:.0f}/yr"
    else:
        display_amount = float(t["price_usd_monthly"])  # type: ignore[arg-type]
        price_phrase = f"${display_amount:.0f}/mo"
    return PaymentRequest(
        message=(
            f"This is a pro tool. Pick the {t['label']} tier "
            f"({price_phrase}) on the storefront, "
            "then set MILO_USAGE_FORECASTER_PRO_KEY in your shell."
        ),
        amount_usd=display_amount,
        tier=tier,
        payment_url=str(t["paypal_button"]),
        instructions=(
            "1. Open the payment_url and complete checkout. "
            "2. You'll receive an emailed pro_key — copy the full token. "
            "3. export MILO_USAGE_FORECASTER_PRO_KEY='<token>' in the shell that runs "
            "your MCP client. 4. Re-run optimize_recommendations; the key is verified "
            "locally — no callback to my server is required."
        ),
    )
