"""
Shared Google service-account auth (RFC 7523 JWT-bearer grant) for every
Google-API-backed connector (gsc_connector.py, ga4_connector.py). Signs the
assertion with `cryptography` (already a hard dependency — used elsewhere for
data_context.py's Fernet encryption) and exchanges it for a bearer token over
plain urllib — the same "stdlib HTTP over a vendor SDK" posture already used
by slack_source.py/email_source.py, and the reason PageSpeed doesn't need
this file at all: it needs no auth.

This has nothing to do with app/auth.py's Authlib "Sign in with Google" flow
— that authenticates a human via a browser redirect. This authenticates a
machine credential the tenant generates themselves in Google Cloud Console
(IAM & Admin -> Service Accounts -> Keys -> Add key -> JSON) and grants read
access to their own GSC property / GA4 property, no OAuth consent screen or
app review involved.
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .base import ConnectorError

TOKEN_URI = "https://oauth2.googleapis.com/token"
_REQUIRED_KEYS = ("client_email", "private_key")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def get_access_token(service_account_info: dict, scope: str) -> str:
    """service_account_info: the parsed JSON key downloaded from Google Cloud
    Console. Returns a short-lived (1hr) bearer token — callers fetch once
    per connector instance, not per-request (GSCConnector/GA4Connector are
    both single-use-per-report objects, not long-lived daemons, so there's
    no caching/refresh concern here)."""
    missing = [k for k in _REQUIRED_KEYS if not service_account_info.get(k)]
    if missing:
        raise ConnectorError(f"Service account config is missing required field(s): {missing}")

    now = int(time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8"))
    claims = _b64url(json.dumps({
        "iss": service_account_info["client_email"],
        "scope": scope,
        "aud": service_account_info.get("token_uri", TOKEN_URI),
        "iat": now,
        "exp": now + 3600,
    }, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header}.{claims}".encode("ascii")

    try:
        private_key = serialization.load_pem_private_key(
            service_account_info["private_key"].encode("utf-8"), password=None,
        )
        signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    except (ValueError, TypeError) as exc:
        raise ConnectorError(f"Could not sign JWT with this service account's private key: {exc}") from exc

    assertion = f"{header}.{claims}.{_b64url(signature)}"
    body = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion,
    }).encode("ascii")
    req = urllib.request.Request(
        service_account_info.get("token_uri", TOKEN_URI), data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ConnectorError(f"Google token exchange failed ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ConnectorError(f"Google token exchange failed: {exc}") from exc

    token = payload.get("access_token")
    if not token:
        raise ConnectorError(f"Google token exchange returned no access_token: {payload}")
    return token
