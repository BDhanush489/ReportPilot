"""
Email-as-a-data-source: fetch CSV/Excel attachments from an inbox and hand
them to report_builder.build_report() in the exact `uploads` shape it
already expects — {"analytics": (filename, filelike), ...}. No changes to
report_builder.py needed; this module's only job is to produce that dict
from a mailbox instead of an HTTP upload. Nothing here computes a metric —
same rule as connectors/base.py: this only ever hands back raw file bytes,
parsers.py/metrics.py do every bit of actual computation, unchanged.

Auth: IMAP + an app password is the one path actually implemented and
testable here — works for a Gmail account (imap.gmail.com) and for a
personal/Outlook.com Microsoft account with basic auth still enabled
(outlook.office365.com). A Microsoft 365 tenant with modern auth defaults
(most business tenants today) has IMAP basic auth disabled entirely and
needs Microsoft Graph API + an Azure AD app registration instead — that
path is deliberately stubbed as unavailable below rather than half-built,
same honesty as delivery.py's channels and exports.py's Google Slides:
never a fake success.
"""
from __future__ import annotations

import email
import imaplib
import os
from dataclasses import dataclass
from email.header import decode_header
from io import BytesIO

from .connectors.base import ConnectorError

PROVIDER_IMAP_HOSTS = {"gmail": "imap.gmail.com", "outlook": "outlook.office365.com"}
ATTACHMENT_EXTENSIONS = (".csv", ".xlsx", ".xls")

#: Filename hints -> which report_builder.build_report() upload slot an
#: attachment belongs in. Deterministic substring matching, same spirit as
#: data_context.py's fuzzy column-name matching -- narrow and explicit
#: rather than guessed from file contents.
_SLOT_FILENAME_HINTS = {
    "analytics": ("analytics", "web_analytics", "ga4", "sessions"),
    "seo": ("seo", "audit", "crawl"),
    "sales": ("sales", "pipeline", "deals", "crm"),
}


def guess_upload_slot(filename: str) -> str | None:
    lower = filename.lower()
    for slot, hints in _SLOT_FILENAME_HINTS.items():
        if any(h in lower for h in hints):
            return slot
    return None


@dataclass
class FetchedAttachment:
    filename: str
    content: bytes
    message_subject: str
    message_from: str
    message_date: str


def _decode_mime_words(s: str) -> str:
    if not s:
        return ""
    decoded_parts = decode_header(s)
    return "".join(
        part.decode(enc or "utf-8", errors="replace") if isinstance(part, bytes) else part
        for part, enc in decoded_parts
    )


def attachments_from_message(msg: email.message.Message,
                              extensions: tuple[str, ...] = ATTACHMENT_EXTENSIONS) -> list[FetchedAttachment]:
    """Pure MIME-parsing: given an already-fetched email.message.Message,
    extract every attachment whose filename matches `extensions`. Split out
    from the live IMAP fetch so this — the actual parsing logic — is
    testable with hand-built messages, no live mailbox required."""
    subject = _decode_mime_words(msg.get("Subject", ""))
    sender = msg.get("From", "")
    date = msg.get("Date", "")

    results: list[FetchedAttachment] = []
    for part in msg.walk():
        filename = part.get_filename()
        if not filename:
            continue
        filename = _decode_mime_words(filename)
        if not filename.lower().endswith(extensions):
            continue
        content = part.get_payload(decode=True)
        if content is None:
            continue
        results.append(FetchedAttachment(
            filename=filename, content=content,
            message_subject=subject, message_from=sender, message_date=date,
        ))
    return results


class IMAPInboxConnector:
    def __init__(self, host: str, username: str, password: str, port: int = 993):
        self.host, self.username, self.port = host, username, port
        try:
            self._conn = imaplib.IMAP4_SSL(host, port)
            self._conn.login(username, password)
        except (imaplib.IMAP4.error, OSError) as exc:
            raise ConnectorError(f"IMAP login failed for {username}@{host}:{port}: {exc}") from exc

    def fetch_attachments(self, mailbox: str = "INBOX", search: str = "UNSEEN", limit: int = 20,
                           extensions: tuple[str, ...] = ATTACHMENT_EXTENSIONS,
                           mark_as_read: bool = False) -> list[FetchedAttachment]:
        status, _ = self._conn.select(mailbox, readonly=not mark_as_read)
        if status != "OK":
            raise ConnectorError(f"Could not select mailbox {mailbox!r}")

        status, data = self._conn.search(None, search)
        if status != "OK":
            raise ConnectorError(f"IMAP search {search!r} failed")
        message_ids = data[0].split()[-limit:] if data and data[0] else []

        results: list[FetchedAttachment] = []
        for msg_id in message_ids:
            status, msg_data = self._conn.fetch(msg_id, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            results.extend(attachments_from_message(msg, extensions=extensions))
        return results

    def close(self) -> None:
        try:
            self._conn.close()
        except imaplib.IMAP4.error:
            pass
        try:
            self._conn.logout()
        except imaplib.IMAP4.error:
            pass


def create_inbox_connector(provider: str, username: str, password: str) -> IMAPInboxConnector:
    host = PROVIDER_IMAP_HOSTS.get(provider)
    if host is None:
        raise ConnectorError(f"Unknown provider {provider!r}. Supported: {sorted(PROVIDER_IMAP_HOSTS)}")
    return IMAPInboxConnector(host=host, username=username, password=password)


def inbox_connector_from_env(provider: str) -> IMAPInboxConnector | None:
    """None (not a raised error) when IMAP_USERNAME/IMAP_PASSWORD aren't
    set — "not configured yet" is a distinct, honestly-reported state from
    "configured but the login failed" (which still raises ConnectorError)."""
    username = os.environ.get("IMAP_USERNAME")
    password = os.environ.get("IMAP_PASSWORD")
    if not username or not password:
        return None
    return create_inbox_connector(provider, username, password)


def build_uploads_from_inbox(connector: IMAPInboxConnector, mailbox: str = "INBOX", search: str = "UNSEEN",
                              limit: int = 20, mark_as_read: bool = False,
                              ) -> tuple[dict, list[FetchedAttachment]]:
    """Returns (uploads, unmatched). `uploads` is ready to pass straight
    into report_builder.build_report(uploads, branding) exactly as an HTTP
    upload would be. `unmatched` holds every fetched attachment whose
    filename didn't map to a known slot (or whose slot was already filled
    by an earlier attachment) — surfaced to the caller to log or review,
    never silently dropped."""
    attachments = connector.fetch_attachments(mailbox=mailbox, search=search, limit=limit, mark_as_read=mark_as_read)
    uploads: dict = {}
    unmatched: list[FetchedAttachment] = []
    for att in attachments:
        slot = guess_upload_slot(att.filename)
        if slot is None or slot in uploads:
            unmatched.append(att)
            continue
        buf = BytesIO(att.content)
        buf.name = att.filename
        uploads[slot] = (att.filename, buf)
    return uploads, unmatched


def create_graph_api_connector(*_args, **_kwargs):
    """Microsoft 365 modern-auth Outlook (Graph API + Azure AD app
    registration) — genuinely not built. Raising here rather than a stub
    that pretends to work is the same honesty as delivery.py/exports.py's
    unavailable channels: this needs a real Azure AD app (client secret,
    Mail.Read permission, tenant admin consent) that doesn't exist to test
    against in this environment. Use IMAPInboxConnector for a tenant/
    account that still allows basic auth."""
    raise ConnectorError(
        "Graph API / Azure AD OAuth for Outlook is not implemented yet — most Microsoft 365 tenants have "
        "disabled IMAP basic auth, so this is the real path for them, but it needs an Azure AD app "
        "registration (client secret + Mail.Read permission + tenant admin consent) to build against. "
        "For a personal Outlook.com account or a tenant that still allows basic auth, use "
        "create_inbox_connector('outlook', ...) instead."
    )
