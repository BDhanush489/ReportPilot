"""
Tests for app/connectors/google_auth.py -- the shared JWT-bearer token
exchange every Google-API connector (GSC, GA4) uses. A throwaway RSA keypair
is generated per test (fast at 2048 bits) so the JWT signing step runs for
real; only the network exchange with Google's token endpoint is mocked.
"""
import json
import urllib.error
from io import BytesIO

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.connectors.base import ConnectorError
from app.connectors.google_auth import get_access_token


@pytest.fixture
def service_account_info():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    return {
        "client_email": "svc@test-project.iam.gserviceaccount.com",
        "private_key": pem,
        "token_uri": "https://oauth2.googleapis.com/token",
    }


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_get_access_token_signs_and_exchanges(monkeypatch, service_account_info):
    seen = {}

    def handler(req, timeout=None):  # noqa: ARG001
        seen["url"] = req.full_url
        seen["body"] = req.data.decode("ascii")
        return _FakeResponse(json.dumps({"access_token": "fake-token-123", "expires_in": 3599}).encode())

    monkeypatch.setattr("app.connectors.google_auth.urllib.request.urlopen", handler)
    token = get_access_token(service_account_info, "https://www.googleapis.com/auth/webmasters.readonly")

    assert token == "fake-token-123"
    assert seen["url"] == "https://oauth2.googleapis.com/token"
    assert "grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer" in seen["body"]
    assert "assertion=" in seen["body"]


def test_missing_required_fields_raises_connector_error():
    with pytest.raises(ConnectorError, match="private_key"):
        get_access_token({"client_email": "svc@test.iam.gserviceaccount.com"}, "scope")


def test_malformed_private_key_raises_connector_error(service_account_info):
    service_account_info["private_key"] = "not-a-real-pem-key"
    with pytest.raises(ConnectorError, match="sign JWT"):
        get_access_token(service_account_info, "scope")


def test_http_error_from_token_endpoint_raises_connector_error(monkeypatch, service_account_info):
    def handler(req, timeout=None):  # noqa: ARG001
        raise urllib.error.HTTPError(req.full_url, 401, "invalid_grant", None, BytesIO(b'{"error":"invalid_grant"}'))

    monkeypatch.setattr("app.connectors.google_auth.urllib.request.urlopen", handler)
    with pytest.raises(ConnectorError, match="invalid_grant"):
        get_access_token(service_account_info, "scope")


def test_missing_access_token_in_response_raises_connector_error(monkeypatch, service_account_info):
    def handler(req, timeout=None):  # noqa: ARG001
        return _FakeResponse(json.dumps({"error": "unauthorized_client"}).encode())

    monkeypatch.setattr("app.connectors.google_auth.urllib.request.urlopen", handler)
    with pytest.raises(ConnectorError, match="no access_token"):
        get_access_token(service_account_info, "scope")
