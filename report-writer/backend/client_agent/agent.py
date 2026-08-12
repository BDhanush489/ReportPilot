"""
ReportPilot client agent -- Ingestion Mode 1 ("client push").

A small, standalone Windows CLI, packaged as a single .exe via PyInstaller,
that runs as a scheduled Task Scheduler job on a CLIENT's own machine: it
fetches new CSV/Excel attachments from their Gmail/Outlook inbox or a Slack
channel, using credentials the client controls (never handed to us to
host), and submits them to a hosted ReportPilot backend's
/api/generate-report endpoint. Use this mode for a client whose IT will run
something locally on a schedule but won't grant a direct warehouse
connection or hand mailbox/Slack credentials to us to host -- see
app/data_context.py + app/scheduler.py for the alternative "we host the
poll" mode (Mode 2), used when a client IS willing to let us hold those
credentials.

Deliberately standalone: this file does not import anything from the `app`
package. app/email_source.py already has the real MIME-parsing/slot-
guessing logic, but importing it pulls in app/connectors/base.py's
`import pandas as pd`, which would drag pandas (and its own weight) into a
tiny local .exe for no benefit. The small, stable pure functions below
(_attachments_from_message, guess_upload_slot, _decode_mime_words, the
provider-host and filename-hint tables) are an intentional duplicate of
app/email_source.py's, kept in sync by hand since they change rarely --
not a shared import, for exactly the reason above.

Credentials at rest: encrypted with Windows DPAPI (CryptProtectData), which
ties the ciphertext to this specific Windows user account on this specific
machine. There is no separate key file to manage, rotate, or leak, and nothing
here can decrypt the saved config from a different machine or as a different
Windows user -- the OS itself is the key store. This is Windows-only by
design (ctypes calls into crypt32.dll), matching the literal deployment
target (Task Scheduler); there is no cross-platform fallback, and none is
needed for what this agent is.
"""
from __future__ import annotations

import argparse
import ctypes
import email
import imaplib
import json
import os
import sys
from ctypes import wintypes
from datetime import datetime, timezone
from email.header import decode_header
from pathlib import Path

import requests

AGENT_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ReportPilotAgent"
CONFIG_PATH = AGENT_DIR / "config.bin"
LOG_PATH = AGENT_DIR / "agent.log"

PROVIDER_IMAP_HOSTS = {"gmail": "imap.gmail.com", "outlook": "outlook.office365.com"}
ATTACHMENT_EXTENSIONS = (".csv", ".xlsx", ".xls")
SLACK_API_BASE = "https://slack.com/api"

#: Filename hints -> which /api/generate-report upload slot an attachment
#: belongs in -- mirrors app/email_source.py's _SLOT_FILENAME_HINTS exactly.
_SLOT_FILENAME_HINTS = {
    "analytics": ("analytics", "web_analytics", "ga4", "sessions"),
    "seo": ("seo", "audit", "crawl"),
    "sales": ("sales", "pipeline", "deals", "crm"),
}
_UPLOAD_FIELD_NAMES = {"analytics": "analytics_file", "seo": "seo_file", "sales": "sales_file"}


class AgentError(Exception):
    """Raised for any expected failure (bad credentials, unreachable API,
    misconfiguration) -- caught at the top level and turned into a clean
    error message + non-zero exit code, never a raw traceback dumped into
    Task Scheduler's history."""


# ---------------------------------------------------------------------------
# Windows DPAPI -- local credential encryption, no key file to manage.
# ---------------------------------------------------------------------------

_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _load_dpapi():
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    return crypt32, kernel32


def _dpapi_call(fn, data: bytes, kernel32) -> bytes:
    buf_in = ctypes.create_string_buffer(data, len(data))
    blob_in = _DATA_BLOB(len(data), ctypes.cast(buf_in, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()
    ok = fn(ctypes.byref(blob_in), None, None, None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    if not ok:
        raise AgentError(f"Windows DPAPI call failed: {ctypes.WinError(ctypes.get_last_error())}")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def dpapi_protect(data: bytes) -> bytes:
    crypt32, kernel32 = _load_dpapi()
    return _dpapi_call(crypt32.CryptProtectData, data, kernel32)


def dpapi_unprotect(data: bytes) -> bytes:
    crypt32, kernel32 = _load_dpapi()
    return _dpapi_call(crypt32.CryptUnprotectData, data, kernel32)


# ---------------------------------------------------------------------------
# Local config: one encrypted JSON blob, one Windows user, one machine.
# ---------------------------------------------------------------------------

def save_config(config: dict) -> Path:
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    ciphertext = dpapi_protect(json.dumps(config).encode("utf-8"))
    CONFIG_PATH.write_bytes(ciphertext)
    return CONFIG_PATH


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise AgentError(f"No config found at {CONFIG_PATH} — run with `setup` first.")
    try:
        plaintext = dpapi_unprotect(CONFIG_PATH.read_bytes())
    except AgentError as exc:
        raise AgentError(
            f"Could not decrypt {CONFIG_PATH} — DPAPI-encrypted config only decrypts under the same "
            "Windows user account on the same machine that ran `setup`. Re-run `setup` if this is a new "
            f"machine or account. ({exc})"
        ) from exc
    return json.loads(plaintext.decode("utf-8"))


def _log(line: str) -> None:
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{stamp} | {line}\n")


# ---------------------------------------------------------------------------
# Fetch: IMAP (Gmail/Outlook) -- pure functions duplicated from
# app/email_source.py (see module docstring for why).
# ---------------------------------------------------------------------------

def guess_upload_slot(filename: str) -> str | None:
    lower = filename.lower()
    for slot, hints in _SLOT_FILENAME_HINTS.items():
        if any(h in lower for h in hints):
            return slot
    return None


def _decode_mime_words(s: str) -> str:
    if not s:
        return ""
    return "".join(
        part.decode(enc or "utf-8", errors="replace") if isinstance(part, bytes) else part
        for part, enc in decode_header(s)
    )


def _attachments_from_message(msg: email.message.Message) -> list[tuple[str, bytes]]:
    results = []
    for part in msg.walk():
        filename = part.get_filename()
        if not filename:
            continue
        filename = _decode_mime_words(filename)
        if not filename.lower().endswith(ATTACHMENT_EXTENSIONS):
            continue
        content = part.get_payload(decode=True)
        if content is None:
            continue
        results.append((filename, content))
    return results


def fetch_from_imap(config: dict) -> list[tuple[str, bytes]]:
    provider = config["source_kind"]
    host = PROVIDER_IMAP_HOSTS.get(provider)
    if host is None:
        raise AgentError(f"Unknown IMAP provider {provider!r}. Supported: {sorted(PROVIDER_IMAP_HOSTS)}")
    try:
        conn = imaplib.IMAP4_SSL(host)
        conn.login(config["username"], config["password"])
    except (imaplib.IMAP4.error, OSError) as exc:
        raise AgentError(f"IMAP login failed for {config['username']}@{host}: {exc}") from exc

    try:
        mailbox, search = config.get("mailbox", "INBOX"), config.get("search", "UNSEEN")
        mark_as_read = config.get("mark_as_read", True)
        status, _ = conn.select(mailbox, readonly=not mark_as_read)
        if status != "OK":
            raise AgentError(f"Could not select mailbox {mailbox!r}")
        status, data = conn.search(None, search)
        if status != "OK":
            raise AgentError(f"IMAP search {search!r} failed")
        message_ids = data[0].split()[-config.get("limit", 20):] if data and data[0] else []

        found: list[tuple[str, bytes]] = []
        for msg_id in message_ids:
            status, msg_data = conn.fetch(msg_id, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            found.extend(_attachments_from_message(msg))
        return found
    finally:
        try:
            conn.close()
        except imaplib.IMAP4.error:
            pass
        conn.logout()


# ---------------------------------------------------------------------------
# Fetch: Slack -- same shape as app/slack_source.py's SlackInboxConnector,
# reimplemented with `requests` since this file already depends on it.
# ---------------------------------------------------------------------------

def fetch_from_slack(config: dict) -> list[tuple[str, bytes]]:
    token, channel_id = config["bot_token"], config["channel_id"]
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(f"{SLACK_API_BASE}/files.list",
                             params={"channel": channel_id, "count": config.get("limit", 20)},
                             headers=headers, timeout=15)
        payload = resp.json()
    except requests.RequestException as exc:
        raise AgentError(f"Slack API request failed: {exc}") from exc
    if not payload.get("ok"):
        raise AgentError(f"Slack API files.list returned an error: {payload.get('error', 'unknown_error')}")

    found: list[tuple[str, bytes]] = []
    for f in payload.get("files", []):
        filename = f.get("name") or ""
        if not filename.lower().endswith(ATTACHMENT_EXTENSIONS):
            continue
        download_url = f.get("url_private_download")
        if not download_url:
            continue
        try:
            dl = requests.get(download_url, headers=headers, timeout=30)
            dl.raise_for_status()
        except requests.RequestException as exc:
            raise AgentError(f"Failed to download Slack file {filename!r}: {exc}") from exc
        found.append((filename, dl.content))
    return found


def fetch_new_files(config: dict) -> list[tuple[str, bytes]]:
    source_kind = config["source_kind"]
    if source_kind in PROVIDER_IMAP_HOSTS:
        return fetch_from_imap(config)
    if source_kind == "slack":
        return fetch_from_slack(config)
    raise AgentError(f"Unknown source_kind {source_kind!r}. Supported: gmail, outlook, slack.")


# ---------------------------------------------------------------------------
# Submit: hand fetched files to the hosted backend, exactly like a browser
# upload would (POST /api/generate-report), then follow real progress via
# its SSE stream until the job finishes.
# ---------------------------------------------------------------------------

def slot_files(attachments: list[tuple[str, bytes]]) -> tuple[dict[str, tuple[str, bytes]], list[str]]:
    """First match per slot wins -- same policy as email_source.py's
    build_uploads_from_inbox. Returns (slotted, unmatched_filenames)."""
    slotted: dict[str, tuple[str, bytes]] = {}
    unmatched: list[str] = []
    for filename, content in attachments:
        slot = guess_upload_slot(filename)
        if slot is None or slot in slotted:
            unmatched.append(filename)
            continue
        slotted[slot] = (filename, content)
    return slotted, unmatched


def submit_report(config: dict, slotted: dict[str, tuple[str, bytes]]) -> str:
    base_url = config["api_base_url"].rstrip("/")
    headers = {"X-API-Key": config["api_key"]} if config.get("api_key") else {}
    data = {
        "agency_name": config.get("agency_name", "Your Agency"),
        "client_name": config.get("client_name", "Client"),
        "primary_color": config.get("primary_color", "#2a78d6"),
        "accent_color": config.get("accent_color", "#eda100"),
    }
    files = {
        _UPLOAD_FIELD_NAMES[slot]: (filename, content)
        for slot, (filename, content) in slotted.items()
    }
    try:
        resp = requests.post(f"{base_url}/api/generate-report", data=data, files=files,
                              headers=headers, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise AgentError(f"Submitting the report to {base_url} failed: {exc}") from exc
    job_id = resp.json().get("job_id")
    if not job_id:
        raise AgentError(f"Backend did not return a job_id: {resp.text}")
    return job_id


def wait_for_job(config: dict, job_id: str, timeout_seconds: int = 900) -> dict:
    """Streams /api/jobs/{job_id}/events (Server-Sent Events) and returns the
    final {"status", "stage", "error"} payload -- real pipeline stages, not
    a fixed sleep, same signal the web UI's progress bar uses."""
    base_url = config["api_base_url"].rstrip("/")
    headers = {"X-API-Key": config["api_key"]} if config.get("api_key") else {}
    url = f"{base_url}/api/jobs/{job_id}/events"
    last: dict = {"status": "running", "stage": None, "error": None}
    try:
        with requests.get(url, headers=headers, stream=True, timeout=timeout_seconds) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                last = json.loads(line[len("data: "):])
                if last.get("status") in ("done", "error"):
                    return last
    except requests.RequestException as exc:
        raise AgentError(f"Lost connection while waiting for job {job_id}: {exc}") from exc
    raise AgentError(f"Job {job_id} did not finish within {timeout_seconds}s (last known: {last}).")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_setup(args: argparse.Namespace) -> int:
    config = {
        "source_kind": args.source,
        "api_base_url": args.api_base_url,
        "api_key": args.api_key or "",
        "agency_name": args.agency_name,
        "client_name": args.client_name,
        "primary_color": args.primary_color,
        "accent_color": args.accent_color,
        "mailbox": args.mailbox,
        "search": args.search,
        "limit": args.limit,
        "mark_as_read": not args.no_mark_as_read,
    }
    if args.source in PROVIDER_IMAP_HOSTS:
        if not args.username or not args.password:
            raise AgentError("--username and --password are required for --source gmail/outlook.")
        config["username"], config["password"] = args.username, args.password
    elif args.source == "slack":
        if not args.bot_token or not args.channel_id:
            raise AgentError("--bot-token and --channel-id are required for --source slack.")
        config["bot_token"], config["channel_id"] = args.bot_token, args.channel_id

    path = save_config(config)
    print(f"Saved encrypted config to {path} (readable only by this Windows user, on this machine).")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config()
    attachments = fetch_new_files(config)
    if not attachments:
        _log("run: nothing new")
        print("No new matching attachments found.")
        return 0

    slotted, unmatched = slot_files(attachments)
    if unmatched:
        print(f"Skipped (unrecognized filename, no free slot): {', '.join(unmatched)}")
    if not slotted:
        _log("run: attachments found but none matched a known slot")
        print("No attachments matched a recognized report slot (analytics/seo/sales).")
        return 0

    if args.dry_run:
        print(f"Dry run: would submit {list(slotted.keys())} to {config['api_base_url']}. Nothing sent.")
        _log(f"dry_run: would submit {list(slotted.keys())}")
        return 0

    print(f"Submitting {list(slotted.keys())} to {config['api_base_url']} ...")
    job_id = submit_report(config, slotted)
    result = wait_for_job(config, job_id)

    if result["status"] == "error":
        _log(f"run: job {job_id} failed: {result.get('error')}")
        print(f"Report generation failed: {result.get('error')}", file=sys.stderr)
        return 1

    _log(f"run: job {job_id} done, sources={list(slotted.keys())}")
    print(f"Report generated: {config['api_base_url'].rstrip('/')}/api/report/{job_id}/pdf")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reportpilot-agent",
        description="Fetches new CSV/Excel files from a Gmail/Outlook inbox or a Slack channel and "
                     "submits them to a hosted ReportPilot backend to generate a report. Run `setup` "
                     "once, then schedule `run` as a recurring Windows Task Scheduler task.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="Save an encrypted local config for this machine/user.")
    setup.add_argument("--source", required=True, choices=["gmail", "outlook", "slack"])
    setup.add_argument("--username", help="Mailbox address (gmail/outlook)")
    setup.add_argument("--password", help="App password, not the account password (gmail/outlook)")
    setup.add_argument("--bot-token", help="Slack bot token, xoxb-... (slack)")
    setup.add_argument("--channel-id", help="Slack channel ID to watch (slack)")
    setup.add_argument("--mailbox", default="INBOX")
    setup.add_argument("--search", default="UNSEEN")
    setup.add_argument("--limit", type=int, default=20)
    setup.add_argument("--no-mark-as-read", action="store_true",
                        help="Don't mark fetched emails as read (default: mark as read, so the next "
                             "run doesn't resubmit the same file).")
    setup.add_argument("--api-base-url", required=True, help="Hosted ReportPilot backend, e.g. https://reports.example.com")
    setup.add_argument("--api-key", help="An API token for your workspace, minted at POST /api/auth/tokens "
                                          "while logged into the ReportPilot web app -- sent as X-API-Key.")
    setup.add_argument("--agency-name", default="Your Agency")
    setup.add_argument("--client-name", default="Client")
    setup.add_argument("--primary-color", default="#2a78d6")
    setup.add_argument("--accent-color", default="#eda100")
    setup.set_defaults(func=cmd_setup)

    run = sub.add_parser("run", help="Fetch new files and submit a report. This is what Task Scheduler calls.")
    run.add_argument("--dry-run", action="store_true", help="Fetch and report what would be submitted; submit nothing.")
    run.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except AgentError as exc:
        _log(f"error: {exc}")
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
