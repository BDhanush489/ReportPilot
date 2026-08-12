"""
Per-client "data context": onboard once, reuse forever.

The expensive part of connecting a new client isn't the SQL — it's figuring out
that *their* `chan` column means what our `channel_group` means, that revenue
lives in `deals.amount_usd` not `deals.deal_value`, etc. This module discovers
a client's real schema, proposes a mapping from their column names to
ReportPilot's canonical fields (using the same Claude -> Ollama provider chain
as agent.py, with a deterministic fuzzy-match fallback if neither is
available), and persists the result so every future run just reads the saved
mapping instead of re-discovering it.

INVARIANT NOTE: the LLM here only ever sees column *names and types* — never a
single business data value. It is not being asked to interpret or summarize
data, only to match schema vocabulary. This is a strictly narrower, safer use
of the model than report narrative generation, and it still never computes or
touches a metric.

CREDENTIALS AT REST: connector.config routinely holds real secrets -- a
Postgres DSN with a password baked in, a Snowflake user/password pair, a
Databricks access token. When DATA_CONTEXT_ENCRYPTION_KEY is set, that
config is encrypted (Fernet, symmetric+authenticated) before it's ever
persisted. When it isn't set, this still works -- saved in plaintext, so a
local single-user demo doesn't hard-fail on a missing key -- but every
plaintext save logs a loud warning, so running insecurely is never silent.
Decryption happens once, inside load_data_context(); every existing caller
(report_builder.py, main.py) already gets a plain dict back and needs no
changes.

Persisted in the `data_contexts` table (see app/store_models.py) rather
than local disk -- the Fernet encryption above is completely unaffected by
that; it still runs before anything is written, only what it's written TO
changed (a column instead of a file). Every save/load function here opens
its own short-lived DB session internally (see scheduler.py's module
docstring for the fuller rationale: some callers, like a background report
job, have no FastAPI request to draw a `Depends(get_db)` session from).
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

from . import db as db_mod
from .connectors.base import Connector
from .store_models import DataContextRecord

_logger = logging.getLogger("reportpilot.data_context")


def _get_fernet():
    """None when DATA_CONTEXT_ENCRYPTION_KEY isn't set -- a caller-visible
    "not configured" state, distinct from raising, which is reserved for a
    key that IS set but malformed (a typo should be loud, not silently
    treated as "not configured")."""
    key = os.environ.get("DATA_CONTEXT_ENCRYPTION_KEY")
    if not key:
        return None
    from cryptography.fernet import Fernet
    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise ValueError(
            "DATA_CONTEXT_ENCRYPTION_KEY is set but is not a valid Fernet key. Generate one with: "
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        ) from exc


def _encrypt_config(config: dict, fernet) -> dict:
    if fernet is None:
        _logger.warning(
            "DATA_CONTEXT_ENCRYPTION_KEY is not set — this data source's connector credentials "
            "are being saved in PLAINTEXT to local disk. Set DATA_CONTEXT_ENCRYPTION_KEY to encrypt them."
        )
        return {"encrypted": False, "config": config}
    token = fernet.encrypt(json.dumps(config).encode("utf-8"))
    return {"encrypted": True, "config": token.decode("ascii")}


def _decrypt_config(stored, fernet) -> dict:
    """stored: either the new {"encrypted": bool, "config": ...} wrapper,
    or a bare dict -- the shape every data context saved before this
    encryption support existed. Old files are read as-is, unchanged;
    nothing re-encrypts them automatically (that would require decrypting
    and rewriting every file the first time this code runs, a migration
    step deliberately left explicit rather than implicit)."""
    if not (isinstance(stored, dict) and "encrypted" in stored):
        return stored  # pre-existing plaintext shape, unchanged
    if not stored["encrypted"]:
        return stored["config"]
    if fernet is None:
        raise ValueError(
            "This data source's connector config is encrypted, but DATA_CONTEXT_ENCRYPTION_KEY is not "
            "set (or no longer matches the key it was encrypted with) — cannot decrypt it."
        )
    from cryptography.fernet import InvalidToken
    try:
        decrypted = fernet.decrypt(stored["config"].encode("ascii"))
    except InvalidToken as exc:
        raise ValueError(
            "This data source's connector config could not be decrypted — DATA_CONTEXT_ENCRYPTION_KEY "
            "does not match the key it was encrypted with."
        ) from exc
    return json.loads(decrypted.decode("utf-8"))

# The fields each source type needs — mirrors parsers.py's alias-map targets
# exactly, so a SQL-sourced DataFrame and a CSV-sourced DataFrame are
# interchangeable inputs to metrics.py.
CANONICAL_FIELDS = {
    "analytics": ["date", "channel_group", "device_category", "sessions", "new_users",
                  "engaged_sessions", "conversions", "revenue_usd", "bounce_rate",
                  "avg_session_duration_sec"],
    "seo": ["url", "status_code", "is_indexable", "load_time_ms", "title_length",
            "meta_description_length", "h1_count", "word_count", "has_canonical",
            "mobile_friendly", "broken_internal_links", "images_missing_alt",
            "impressions_28d", "clicks_28d", "ctr", "avg_position",
            "organic_sessions_28d", "issue_severity", "issues"],
    "sales": ["deal_id", "close_date", "sales_rep", "product", "region", "lead_source",
              "deal_stage", "amount_usd", "potential_amount_usd", "days_to_close"],
}


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _fuzzy_match_mapping(canonical_fields: list[str], columns: list[dict]) -> dict[str, str | None]:
    """Deterministic fallback used when no AI provider is reachable — exact or
    substring match on normalized names. Always produces a mapping, just a
    cruder one than the LLM would; onboarding never hard-blocks on AI being down."""
    mapping: dict[str, str | None] = {}
    norm_cols = {_normalize(c["name"]): c["name"] for c in columns}
    for field in canonical_fields:
        norm_field = _normalize(field)
        if norm_field in norm_cols:
            mapping[field] = norm_cols[norm_field]
            continue
        match = next((real for norm, real in norm_cols.items()
                      if norm_field in norm or norm in norm_field), None)
        mapping[field] = match
    return mapping


_MAPPING_SCHEMA = {
    "type": "object",
    "properties": {
        "mapping": {
            "type": "object",
            "description": "canonical field name -> real column name, or null if no good match exists",
        }
    },
    "required": ["mapping"],
    "additionalProperties": False,
}


def _ai_propose_mapping(source_type: str, canonical_fields: list[str], columns: list[dict]) -> dict | None:
    """Returns a mapping dict, or None if no AI provider is reachable/succeeds
    (caller falls back to _fuzzy_match_mapping)."""
    prompt = json.dumps({
        "source_type": source_type,
        "canonical_fields_to_map": canonical_fields,
        "real_columns_available": columns,
        "instructions": (
            "For each canonical field, name the real column that means the same thing "
            "(same business concept, tolerate different naming conventions/abbreviations). "
            "Use null if there truly is no matching column. Return ONLY the mapping, "
            "no commentary — this is a schema-matching task, not data analysis."
        ),
    })

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        try:
            import anthropic
            client = anthropic.Anthropic()
            response = client.messages.create(
                model="claude-opus-4-8",
                max_tokens=2000,
                thinking={"type": "adaptive"},
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": _MAPPING_SCHEMA}},
                system="You map database column names to a canonical schema. You never see or reason about actual data values, only column names and types.",
                messages=[{"role": "user", "content": prompt}],
            )
            text_block = next(b.text for b in response.content if b.type == "text")
            return json.loads(text_block)["mapping"]
        except Exception:  # noqa: BLE001 - fall through to Ollama / fuzzy match
            pass

    from . import agent as agent_mod
    if agent_mod._ollama_available():
        try:
            import urllib.request
            body = json.dumps({
                "model": agent_mod.OLLAMA_MODEL,
                "stream": False,
                "options": {"temperature": 0.1},
                "messages": [
                    {"role": "system", "content": "You map database column names to a canonical schema. You never see actual data values, only column names and types."},
                    {"role": "user", "content": prompt},
                ],
                "format": _MAPPING_SCHEMA,
            }).encode("utf-8")
            req = urllib.request.Request(
                f"{agent_mod.OLLAMA_BASE_URL}/api/chat", data=body,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return json.loads(payload["message"]["content"])["mapping"]
        except Exception:  # noqa: BLE001 - fall through to fuzzy match
            pass

    return None


def propose_mapping(source_type: str, columns: list[dict]) -> dict:
    """Returns {"mapping": {...}, "method": "ai" | "fuzzy_match"}."""
    canonical_fields = CANONICAL_FIELDS[source_type]
    ai_mapping = _ai_propose_mapping(source_type, canonical_fields, columns)
    if ai_mapping is not None:
        # still run every proposed column through a sanity check: the model
        # must not invent a column name that doesn't actually exist.
        real_names = {c["name"] for c in columns}
        cleaned = {f: (v if v in real_names else None) for f, v in ai_mapping.items()}
        return {"mapping": cleaned, "method": "ai"}
    return {"mapping": _fuzzy_match_mapping(canonical_fields, columns), "method": "fuzzy_match"}


def discover_and_propose(connector: Connector, table_map: dict[str, str]) -> dict:
    """table_map: {"analytics": "web_events", "seo": "seo_crawl", "sales": "crm_deals"}
    (only include the source types this client actually has tables for)."""
    sources = {}
    for source_type, table in table_map.items():
        columns = connector.list_columns(table)
        proposed = propose_mapping(source_type, columns)
        sources[source_type] = {
            "table": table,
            "column_map": proposed["mapping"],
            "mapping_method": proposed["method"],
            "discovered_columns": columns,
        }
    return sources


def save_data_context(tenant_id: str, client_id: str, connector_kind: str, connector_config: dict, sources: dict) -> None:
    connector = {"kind": connector_kind, "config": _encrypt_config(connector_config, _get_fernet())}
    # A sa.JSON column's default encoder has no fallback for a stray
    # non-JSON-native value (e.g. a Decimal) landing in connector_config --
    # round-tripping through json.dumps(..., default=str) first preserves
    # the same safety net the old json.dumps(..., default=str) file write had.
    connector = json.loads(json.dumps(connector, default=str))
    sources = json.loads(json.dumps(sources, default=str))
    created_at = datetime.now(timezone.utc).isoformat()

    with db_mod.SessionLocal() as session:
        row = DataContextRecord(
            tenant_id=tenant_id, client_id=client_id, created_at=created_at,
            connector=connector, sources=sources,
        )
        session.merge(row)
        session.commit()


def load_data_context(tenant_id: str, client_id: str) -> dict | None:
    with db_mod.SessionLocal() as session:
        row = session.query(DataContextRecord).filter_by(tenant_id=tenant_id, client_id=client_id).one_or_none()
        if row is None:
            return None
        context = {
            "tenant_id": row.tenant_id, "client_id": row.client_id,
            "created_at": row.created_at, "connector": row.connector, "sources": row.sources,
        }
    context["connector"]["config"] = _decrypt_config(context["connector"]["config"], _get_fernet())
    return context


def delete_data_context(tenant_id: str, client_id: str) -> bool:
    """False (not a raised error) when there was nothing to delete for this
    tenant_id/client_id -- the caller (main.py) turns that into a 404, same
    "honest missing state, not an exception" posture as load_data_context()
    returning None. Does NOT touch any Schedule that references this
    client_id as its data_source_ref -- that schedule's next run will fail
    loudly with report_builder's own "No data context saved" ValueError
    rather than this function silently cascading the delete somewhere the
    caller didn't ask for."""
    with db_mod.SessionLocal() as session:
        row = session.query(DataContextRecord).filter_by(tenant_id=tenant_id, client_id=client_id).one_or_none()
        if row is None:
            return False
        session.delete(row)
        session.commit()
        return True


def list_data_contexts(tenant_id: str) -> list[dict]:
    with db_mod.SessionLocal() as session:
        rows = session.query(DataContextRecord).filter_by(tenant_id=tenant_id).all()
        return [
            {
                "client_id": row.client_id, "created_at": row.created_at,
                "connector_kind": row.connector["kind"], "sources": list(row.sources.keys()),
            }
            for row in rows
        ]
