"""
Track B3 — delivery: the finished report arrives in the client's inbox (or
a Slack channel), with the QA badge deciding whether it goes to the client
at all.

Channel implementations (EmailChannel/SlackChannel) are genuinely
unavailable until connected (SMTP_HOST / SLACK_WEBHOOK_URL env vars) --
same honesty as exports.py's Google Slides: never a fake "sent". The
QA-gate and logging logic that sits in front of a channel is real and
testable regardless of whether a real connector is configured, by
dependency-injecting the channel (channel_impl) — the same pattern
connectors/__init__.py already uses for warehouse connections.

Body content reuses exports.export_email_html(obj) verbatim as the message
body -- one email-shaped rendering of the object, not a second one built
independently here.

The delivery log is backed by the `delivery_log_entries` table (see
app/store_models.py) -- genuinely append-only (many attempts can share a
(tenant_id, report_id)), so unlike this app's other stores it uses a real
synthetic primary key instead of reusing the natural key. _log_attempt/
list_delivery_log each open and commit their own short-lived DB session
internally (see scheduler.py's module docstring for the fuller rationale).
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from . import db as db_mod
from .exports import export_email_html
from .report_builder import render_pdf_from_object
from .report_object import ReportObject
from .store_models import DeliveryLogEntry


@dataclass
class ChannelSendResult:
    status: str  # "sent" | "unavailable" | "failed"
    reason: str = ""


class EmailChannel:
    def send(self, to: list[str], subject: str, body_html: str,
              attachments: list[tuple[str, bytes, str]]) -> ChannelSendResult:
        host = os.environ.get("SMTP_HOST")
        if not host:
            return ChannelSendResult(status="unavailable", reason="SMTP_HOST not configured — email delivery is not connected yet.")
        try:
            import smtplib
            from email.mime.application import MIMEApplication
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText

            msg = MIMEMultipart()
            msg["Subject"] = subject
            msg["From"] = os.environ.get("SMTP_FROM", "reports@reportpilot.local")
            msg["To"] = ", ".join(to)
            msg.attach(MIMEText(body_html, "html"))
            for filename, data, _mimetype in attachments:
                part = MIMEApplication(data, Name=filename)
                part["Content-Disposition"] = f'attachment; filename="{filename}"'
                msg.attach(part)

            port = int(os.environ.get("SMTP_PORT", "587"))
            with smtplib.SMTP(host, port, timeout=10) as server:
                if os.environ.get("SMTP_USERNAME"):
                    server.starttls()
                    server.login(os.environ["SMTP_USERNAME"], os.environ["SMTP_PASSWORD"])
                server.sendmail(msg["From"], to, msg.as_string())
            return ChannelSendResult(status="sent")
        except Exception as exc:  # noqa: BLE001
            return ChannelSendResult(status="failed", reason=str(exc))


class SlackChannel:
    def send(self, to: list[str], subject: str, body_html: str,
              attachments: list[tuple[str, bytes, str]]) -> ChannelSendResult:
        webhook = os.environ.get("SLACK_WEBHOOK_URL")
        if not webhook:
            return ChannelSendResult(status="unavailable", reason="SLACK_WEBHOOK_URL not configured — Slack delivery is not connected yet.")
        try:
            import urllib.request

            text = f"*{subject}*\n(PDF attached via email — Slack delivery is a notification, not a file transfer.)"
            body = json.dumps({"text": text}).encode("utf-8")
            req = urllib.request.Request(webhook, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status >= 300:
                    return ChannelSendResult(status="failed", reason=f"Slack webhook returned HTTP {resp.status}")
            return ChannelSendResult(status="sent")
        except Exception as exc:  # noqa: BLE001
            return ChannelSendResult(status="failed", reason=str(exc))


CHANNELS = {"email": EmailChannel, "slack": SlackChannel}


@dataclass
class DeliveryAttempt:
    report_id: str
    channel: str
    recipients: list[str]
    #: "sent" | "sent_to_consultant" | "blocked" | "unavailable" | "failed"
    status: str
    reason: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "DeliveryAttempt":
        return DeliveryAttempt(**d)


def _log_attempt(tenant_id: str, attempt: DeliveryAttempt) -> None:
    with db_mod.SessionLocal() as session:
        row = DeliveryLogEntry(
            tenant_id=tenant_id, report_id=attempt.report_id,
            created_at=datetime.now(timezone.utc), attempt=attempt.to_dict(),
        )
        session.add(row)
        session.commit()


def list_delivery_log(tenant_id: str, report_id: str) -> list[DeliveryAttempt]:
    with db_mod.SessionLocal() as session:
        rows = (
            session.query(DeliveryLogEntry)
            .filter_by(tenant_id=tenant_id, report_id=report_id)
            .order_by(DeliveryLogEntry.created_at)
            .all()
        )
        return [DeliveryAttempt.from_dict(row.attempt) for row in rows]


def deliver_report(tenant_id: str, obj: ReportObject, recipients: list[str], channel: str = "email",
                    consultant_recipients: list[str] | None = None, channel_impl=None) -> DeliveryAttempt:
    """A FAILing QA badge blocks auto-send to the client: redirected to
    consultant_recipients (marked for review, not silently sent as if
    clean) when given, or blocked outright with no send attempted at all
    when there's nowhere to redirect to. Every outcome — sent, redirected,
    blocked, unavailable, failed — is logged; there is no silent path."""
    if channel not in CHANNELS:
        raise ValueError(f"Unknown channel {channel!r}. Choose one of: {sorted(CHANNELS)}")
    channel_impl = channel_impl or CHANNELS[channel]()
    badge = (obj.qa or {}).get("badge")

    if badge == "FAIL":
        if not consultant_recipients:
            attempt = DeliveryAttempt(
                report_id=obj.report_id, channel=channel, recipients=recipients, status="blocked",
                reason="QA badge is FAIL and no consultant_recipients were configured to redirect to — nothing sent.",
            )
            _log_attempt(tenant_id, attempt)
            return attempt
        target, audience, subject_prefix = consultant_recipients, "consultant", "[NEEDS REVIEW] "
    else:
        target, audience, subject_prefix = recipients, "client", ""

    body_html = export_email_html(obj).content
    _html, pdf_bytes = render_pdf_from_object(obj)
    subject = f"{subject_prefix}{obj.narrative.get('report_title') or 'Performance Report'}"
    attachments = [(f"report-{obj.report_id}.pdf", pdf_bytes, "application/pdf")]

    send_result = channel_impl.send(to=target, subject=subject, body_html=body_html, attachments=attachments)
    status = send_result.status
    if status == "sent" and audience == "consultant":
        status = "sent_to_consultant"

    attempt = DeliveryAttempt(report_id=obj.report_id, channel=channel, recipients=target,
                               status=status, reason=send_result.reason)
    _log_attempt(tenant_id, attempt)
    return attempt
