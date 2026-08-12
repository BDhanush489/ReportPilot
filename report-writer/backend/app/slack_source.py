"""
Slack-as-a-data-source: fetch CSV/Excel files shared in a channel and hand
them to report_builder.build_report() in the exact `uploads` shape it
already expects — {"analytics": (filename, filelike), ...}. Same rule as
email_source.py/connectors/base.py: this only ever hands back raw file
bytes, parsers.py/metrics.py do every bit of actual computation, unchanged.

Auth: a Slack bot token (xoxb-...) with `files:read` and `channels:history`
(or `groups:history` for a private channel) scopes — created via a Slack
App at api.slack.com/apps, installed to the workspace once. No interactive
OAuth browser flow needed for a bot token used server-side/by an unattended
agent — same "app credential, not a user login" posture as Gmail's app
password in email_source.py.

SlackInboxConnector.fetch_attachments() deliberately matches
IMAPInboxConnector.fetch_attachments()'s exact signature (mailbox/search/
mark_as_read accepted but unused here) so email_source.build_uploads_from_inbox()
works unchanged against either connector — "where the files came from" stays
a single decision at the call site, not two parallel code paths.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .connectors.base import ConnectorError
from .email_source import ATTACHMENT_EXTENSIONS, FetchedAttachment

SLACK_API_BASE = "https://slack.com/api"


class SlackInboxConnector:
    def __init__(self, bot_token: str, channel_id: str):
        self.bot_token = bot_token
        self.channel_id = channel_id
        self._call("auth.test")  # fail fast on a bad/expired token, same posture as IMAP login-on-init

    def _call(self, method: str, params: dict | None = None) -> dict:
        url = f"{SLACK_API_BASE}/{method}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.bot_token}"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ConnectorError(f"Slack API request to {method!r} failed: {exc}") from exc
        if not payload.get("ok"):
            raise ConnectorError(f"Slack API {method!r} returned an error: {payload.get('error', 'unknown_error')}")
        return payload

    def _download(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.bot_token}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ConnectorError(f"Failed to download Slack file from {url}: {exc}") from exc

    def fetch_attachments(self, mailbox: str = "unused", search: str = "unused", limit: int = 20,
                           extensions: tuple[str, ...] = ATTACHMENT_EXTENSIONS,
                           mark_as_read: bool = False) -> list[FetchedAttachment]:
        """`mailbox`/`search`/`mark_as_read` are accepted but unused — Slack
        has no mailbox/unread-flag concept; they exist purely so this method
        satisfies the same call shape build_uploads_from_inbox() already
        uses for IMAPInboxConnector. Files are matched by filename extension
        client-side (same as email_source.py), not Slack's own coarse
        `types` filter (which has no "csv"/"xlsx" category, only broad
        buckets like "spaces"/"pdfs")."""
        payload = self._call("files.list", {"channel": self.channel_id, "count": limit})
        results: list[FetchedAttachment] = []
        for f in payload.get("files", []):
            filename = f.get("name") or ""
            if not filename.lower().endswith(extensions):
                continue
            download_url = f.get("url_private_download")
            if not download_url:
                continue
            content = self._download(download_url)
            results.append(FetchedAttachment(
                filename=filename, content=content,
                message_subject=f.get("title", ""), message_from=f.get("user", ""),
                message_date=str(f.get("timestamp", "")),
            ))
        return results

    def close(self) -> None:
        pass  # stateless HTTP calls per-request -- nothing persistent to tear down, unlike IMAP


def create_slack_connector(bot_token: str, channel_id: str) -> SlackInboxConnector:
    return SlackInboxConnector(bot_token=bot_token, channel_id=channel_id)


def slack_connector_from_env() -> SlackInboxConnector | None:
    """None (not a raised error) when SLACK_BOT_TOKEN/SLACK_CHANNEL_ID aren't
    set — "not configured yet" is a distinct, honestly-reported state from
    "configured but the token/channel is bad" (which still raises
    ConnectorError), same distinction email_source.inbox_connector_from_env
    makes for IMAP_USERNAME/IMAP_PASSWORD."""
    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    channel_id = os.environ.get("SLACK_CHANNEL_ID")
    if not bot_token or not channel_id:
        return None
    return create_slack_connector(bot_token, channel_id)
