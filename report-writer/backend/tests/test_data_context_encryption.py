"""
Tests for data_context.py's at-rest encryption of connector credentials
(DATA_CONTEXT_ENCRYPTION_KEY). Covers: encrypted round-trip, plaintext
fallback when no key is set, wrong-key failure, and backward compatibility
with data contexts saved before this encryption support existed.
"""
import json

import pytest
from cryptography.fernet import Fernet

from app import data_context
from app.store_models import DataContextRecord

CONFIG = {"path": "/tmp/some.db", "password": "correct-horse-battery-staple"}


def _raw_connector(db_session, tenant_id, client_id) -> dict:
    """The `connector` column exactly as persisted -- the DB-backed
    equivalent of reading the old on-disk JSON file directly, for tests that
    need to prove what's actually AT REST, not what load_data_context()
    hands back after decrypting."""
    row = db_session.query(DataContextRecord).filter_by(tenant_id=tenant_id, client_id=client_id).one()
    return row.connector


def test_without_a_key_config_is_saved_as_plaintext_on_disk(db_session, monkeypatch):
    monkeypatch.delenv("DATA_CONTEXT_ENCRYPTION_KEY", raising=False)
    data_context.save_data_context("t1", "client-a", "sqlite", CONFIG, {})

    raw = _raw_connector(db_session, "t1", "client-a")
    assert raw["config"]["encrypted"] is False
    assert raw["config"]["config"] == CONFIG  # the secret is right there, in plain JSON


def test_without_a_key_load_still_returns_the_real_config(db_session, monkeypatch):
    monkeypatch.delenv("DATA_CONTEXT_ENCRYPTION_KEY", raising=False)
    data_context.save_data_context("t1", "client-a", "sqlite", CONFIG, {})
    loaded = data_context.load_data_context("t1", "client-a")
    assert loaded["connector"]["config"] == CONFIG


def test_with_a_key_the_password_never_appears_in_the_saved_file(db_session, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("DATA_CONTEXT_ENCRYPTION_KEY", key)
    data_context.save_data_context("t1", "client-a", "sqlite", CONFIG, {})

    raw = _raw_connector(db_session, "t1", "client-a")
    assert "correct-horse-battery-staple" not in json.dumps(raw)
    assert raw["config"]["encrypted"] is True
    assert isinstance(raw["config"]["config"], str)  # an opaque token, not a dict


def test_with_a_key_load_decrypts_back_to_the_exact_original_config(db_session, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("DATA_CONTEXT_ENCRYPTION_KEY", key)
    data_context.save_data_context("t1", "client-a", "sqlite", CONFIG, {})
    loaded = data_context.load_data_context("t1", "client-a")
    assert loaded["connector"]["config"] == CONFIG


def test_the_wrong_key_fails_loudly_not_silently():
    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()
    # encrypt with key_a's Fernet directly, then try to decrypt with key_b
    token = Fernet(key_a.encode()).encrypt(json.dumps(CONFIG).encode()).decode()
    stored = {"encrypted": True, "config": token}
    with pytest.raises(ValueError, match="could not be decrypted"):
        data_context._decrypt_config(stored, Fernet(key_b.encode()))


def test_encrypted_data_with_no_key_configured_fails_loudly_not_silently():
    key = Fernet.generate_key().decode()
    token = Fernet(key.encode()).encrypt(json.dumps(CONFIG).encode()).decode()
    stored = {"encrypted": True, "config": token}
    with pytest.raises(ValueError, match="cannot decrypt"):
        data_context._decrypt_config(stored, None)


def test_a_malformed_encryption_key_is_rejected_loudly(monkeypatch):
    monkeypatch.setenv("DATA_CONTEXT_ENCRYPTION_KEY", "not-a-valid-fernet-key")
    with pytest.raises(ValueError, match="not a valid Fernet key"):
        data_context._get_fernet()


def test_missing_key_is_none_not_an_error(monkeypatch):
    monkeypatch.delenv("DATA_CONTEXT_ENCRYPTION_KEY", raising=False)
    assert data_context._get_fernet() is None


# ---------------------------------------------------------------------------
# Backward compatibility: a data context saved before this feature existed
# ---------------------------------------------------------------------------

def test_pre_existing_plaintext_shaped_row_still_loads_correctly(db_session, monkeypatch):
    """A data context saved by the old code (connector.config was a bare
    dict, no {"encrypted": ...} wrapper at all) must keep working exactly
    as before, whether or not an encryption key happens to be set now."""
    row = DataContextRecord(
        tenant_id="t1", client_id="legacy-client", created_at="2026-01-01T00:00:00+00:00",
        connector={"kind": "sqlite", "config": CONFIG}, sources={},  # old shape: bare dict, no wrapper
    )
    db_session.add(row)
    db_session.commit()

    monkeypatch.setenv("DATA_CONTEXT_ENCRYPTION_KEY", Fernet.generate_key().decode())
    loaded = data_context.load_data_context("t1", "legacy-client")
    assert loaded["connector"]["config"] == CONFIG


def test_list_data_contexts_never_touches_connector_config(db_session, monkeypatch):
    """list_data_contexts() only reads client_id/created_at/kind/source
    keys -- must work identically regardless of encryption, since it never
    decrypts anything."""
    monkeypatch.delenv("DATA_CONTEXT_ENCRYPTION_KEY", raising=False)
    data_context.save_data_context("t1", "client-a", "sqlite", CONFIG, {"analytics": {}})
    listed = data_context.list_data_contexts("t1")
    assert listed == [{"client_id": "client-a", "created_at": listed[0]["created_at"],
                        "connector_kind": "sqlite", "sources": ["analytics"]}]
