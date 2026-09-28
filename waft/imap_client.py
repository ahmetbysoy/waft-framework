"""IMAP verification-email watcher.

After a form is submitted, many flows require the user to click a link (or enter an OTP)
delivered by e-mail. :class:`ImapClient` polls an IMAP mailbox - in a worker thread, so the
asyncio event loop never blocks - until a matching message arrives, extracts the
verification link / OTP code, and hands it back to the Playwright engine.

Highlights
----------
*   Robust MIME handling: nested multipart, quoted-printable, base64, declared charsets,
    HTML-only mails, ``text/plain`` alternatives.
*   Deterministic search: ``SINCE``/``FROM``/``SUBJECT``/``UNSEEN`` criteria first, then a
    local scoring pass (subject regex, sender filter, target address present, recency).
*   De-duplication: every consumed UID is remembered so a second target never re-uses the
    same verification mail.
*   Safety rails: TLS by default, retrying reconnect with exponential backoff, hard timeout,
    optional "mark as seen" / "move to processed mailbox" behaviour.
*   The flow never silently succeeds: timeouts raise :class:`~waft.errors.ImapTimeoutError`
    which the engine turns into a failed (but non-retryable) step.
"""

from __future__ import annotations

import asyncio
import email
import email.policy
import email.utils
import html
import imaplib
import re
import ssl
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from typing import Any, Optional, Sequence
from urllib.parse import urlparse

from .config import Config
from .errors import ImapAuthError, ImapError, ImapTimeoutError
from .logging_setup import get_logger
from .utils import Stopwatch, human_ms, truncate

__all__ = [
    "ImapClient",
    "MailMessage",
    "ImapSettings",
    "ExtractedVerification",
    "extract_links",
    "extract_otp_codes",
    "decode_mime_header",
    "message_body",
]

logger = get_logger("waft.imap")

URL_RE = re.compile(r"https?://[^\s\"'<>\]\)]+", re.I)
HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)
VERIFY_HINTS = re.compile(
    r"(verify|verification|confirm|confirmation|activate|activation|aktive|aktivasyon|"
    r"dogrula|doğrula|onayla|dogrulama|doğrulama|hesap|account|token|signup|sign-up|welcome|"
    r"reset|password|sifre|şifre|unlock|validate)",
    re.I,
)
OTP_CONTEXT_RE = re.compile(
    r"(kod|code|otp|pin|şifre|sifre|password|doğrulama|dogrulama|verification)[^\d]{0,24}(\d{3,8})",
    re.I,
)
STOP_LINK_PATTERNS = (
    "unsubscribe",
    "privacy-policy",
    "terms-of-service",
    "terms",
    "help",
    "support",
    "facebook.com",
    "twitter.com",
    "x.com/",
    "instagram.com",
    "linkedin.com",
    "youtube.com",
    "play.google.com",
    "apps.apple.com",
    "apple.com",
    "google.com/maps",
    "/preferences",
    "/settings/notifications",
)


# --------------------------------------------------------------------------------------
# Settings / models
# --------------------------------------------------------------------------------------


@dataclass
class ImapSettings:
    """Everything the client needs to reach a mailbox."""

    host: str
    port: int = 993
    username: str = ""
    password: str = ""
    use_ssl: bool = True
    starttls: bool = False
    mailbox: str = "INBOX"
    timeout_s: float = 120.0
    poll_interval_s: float = 5.0
    search_days: int = 2
    unseen_only: bool = True
    mark_seen: bool = False
    mark_processed: bool = False
    processed_mailbox: str = "WAFT-Processed"
    link_regex: str = ""
    otp_regex: str = r"(?<!\d)(\d{4,8})(?!\d)"
    subject_regex: Optional[str] = None
    sender_filter: Optional[str] = None
    max_links: int = 25
    connect_retries: int = 3
    verify_tls: bool = True

    @classmethod
    def from_config(cls, config: Config) -> "ImapSettings":
        return cls(
            host=config.imap_host or "",
            port=config.imap_port,
            username=config.imap_username or "",
            password=config.imap_password or "",
            use_ssl=config.imap_ssl,
            starttls=config.imap_starttls,
            mailbox=config.imap_mailbox,
            timeout_s=config.imap_timeout_s,
            poll_interval_s=config.imap_poll_interval_s,
            search_days=config.imap_search_days,
            unseen_only=config.imap_allow_unseen_only,
            mark_seen=config.imap_mark_seen,
            mark_processed=config.imap_mark_processed,
            link_regex=config.imap_link_regex,
            otp_regex=config.imap_otp_regex,
            subject_regex=config.imap_subject_regex,
            sender_filter=config.imap_sender_filter,
            max_links=config.imap_max_links,
        )

    def validate(self) -> None:
        if not self.host or not self.username or not self.password:
            raise ImapError("IMAP settings are incomplete (host/username/password required)")
        if self.use_ssl and self.starttls:
            raise ImapError("use_ssl and starttls are mutually exclusive")


@dataclass
class MailMessage:
    """A parsed mail message plus the artifacts we care about."""

    uid: str
    subject: str = ""
    sender: str = ""
    to: str = ""
    date: Optional[datetime] = None
    message_id: str = ""
    body_text: str = ""
    body_html: str = ""
    links: list[str] = field(default_factory=list)
    otp_codes: list[str] = field(default_factory=list)
    is_seen: bool = False
    folder: str = "INBOX"
    headers: dict[str, str] = field(default_factory=dict)
    size: Optional[int] = None
    score: float = 0.0
    matched_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "subject": self.subject,
            "sender": self.sender,
            "to": self.to,
            "date": self.date.isoformat() if self.date else None,
            "message_id": self.message_id,
            "links": self.links[:5],
            "otp_codes": self.otp_codes[:3],
            "is_seen": self.is_seen,
            "folder": self.folder,
            "score": round(self.score, 1),
            "matched_reason": self.matched_reason,
            "body_preview": truncate(self.body_text or self.body_html, 300),
        }

    @property
    def age_seconds(self) -> Optional[float]:
        if not self.date:
            return None
        reference = self.date if self.date.tzinfo else self.date.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - reference).total_seconds()


@dataclass
class ExtractedVerification:
    """Result of scanning one message for a link/OTP."""

    message: MailMessage
    link: Optional[str] = None
    otp: Optional[str] = None
    candidates: list[str] = field(default_factory=list)
    method: str = "none"  # link | otp | none
    reason: str = ""

    @property
    def found(self) -> bool:
        return bool(self.link or self.otp)

    def to_dict(self) -> dict[str, Any]:
        return {
            "found": self.found,
            "method": self.method,
            "link": self.link,
            "otp": "***" if self.otp else None,
            "otp_length": len(self.otp) if self.otp else 0,
            "candidates": self.candidates[:10],
            "reason": self.reason,
            "message": self.message.to_dict(),
        }


# --------------------------------------------------------------------------------------
# MIME helpers
# --------------------------------------------------------------------------------------


def decode_mime_header(value: Optional[str]) -> str:
    """Decode ``=?utf-8?B?...?=`` style headers into readable Unicode."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001 - malformed headers happen in the wild
        return str(value)


def _decode_payload(part: Message) -> str:
    """Decode a MIME part into text without ever raising."""
    try:
        payload = part.get_payload(decode=True)
    except Exception:  # noqa: BLE001
        payload = None
    if payload is None:
        raw = part.get_payload()
        return str(raw) if isinstance(raw, str) else ""

    charset = part.get_content_charset() or part.get_charset() or "utf-8"
    for candidate in (charset, "utf-8", "latin-1"):
        try:
            return payload.decode(candidate, errors="replace")
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("utf-8", errors="replace")


def message_body(message: Message) -> tuple[str, str]:
    """Return ``(plain_text, html)`` for a (possibly multipart) message."""
    plain_chunks: list[str] = []
    html_chunks: list[str] = []

    def walk(part: Message) -> None:
        content_type = (part.get_content_type() or "").lower()
        disposition = str(part.get("Content-Disposition") or "").lower()
        if "attachment" in disposition:
            return
        if part.is_multipart():
            for child in part.get_payload():
                if isinstance(child, Message):
                    walk(child)
            return
        if content_type == "text/plain":
            plain_chunks.append(_decode_payload(part))
        elif content_type in {"text/html", "application/xhtml+xml"}:
            html_chunks.append(_decode_payload(part))
        elif part.get_payload() and not part.is_multipart():
            text = _decode_payload(part)
            if "<html" in text.lower():
                html_chunks.append(text)
            else:
                plain_chunks.append(text)

    walk(message)
    return "\n".join(chunk for chunk in plain_chunks if chunk), "\n".join(chunk for chunk in html_chunks if chunk)


def html_to_text(html_body: str) -> str:
    """Very light HTML → text conversion (enough for link/OTP extraction)."""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html_body)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6])>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t\u00a0]+", " ", text)


def extract_links(text: str, html_body: str = "", *, pattern: str = "", max_links: int = 25) -> list[str]:
    """Extract unique, plausible action links from text and HTML bodies."""
    candidates: list[str] = []

    for match in HREF_RE.finditer(html_body or ""):
        candidates.append(html.unescape(match.group(1)).strip())
    for match in URL_RE.finditer(text or ""):
        candidates.append(match.group(0).strip())
    for match in URL_RE.finditer(html_body or ""):
        candidates.append(html.unescape(match.group(0)).strip())

    compiled = re.compile(pattern, re.I) if pattern else None
    seen: set[str] = set()
    cleaned: list[str] = []
    for candidate in candidates:
        url = candidate.rstrip(".,;:!?)»\"'")
        if not url.lower().startswith(("http://", "https://")):
            continue
        if any(stop in url.lower() for stop in STOP_LINK_PATTERNS):
            continue
        if url in seen:
            continue
        seen.add(url)
        cleaned.append(url)

    if compiled is not None:
        cleaned.sort(key=lambda url: (0 if compiled.search(url) else 1, -len(VERIFY_HINTS.findall(url))))
    else:
        cleaned.sort(key=lambda url: (0 if VERIFY_HINTS.search(url) else 1,))
    return cleaned[:max_links]


def extract_otp_codes(text: str, *, pattern: str = r"(?<!\d)(\d{4,8})(?!\d)") -> list[str]:
    """Extract OTP-like numeric codes, preferring ones next to a "kod/code" keyword."""
    if not text:
        return []
    ordered: list[str] = []
    for match in OTP_CONTEXT_RE.finditer(text):
        code = match.group(2)
        if code not in ordered:
            ordered.append(code)
    try:
        for match in re.finditer(pattern, text):
            code = match.group(1) if match.groups() else match.group(0)
            if code.isdigit() and len(code) >= 4 and code not in ordered:
                ordered.append(code)
    except re.error as exc:  # pragma: no cover - config error path
        logger.debug("Invalid OTP regex %r: %s", pattern, exc)
    return ordered[:5]


# --------------------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------------------


class ImapClient:
    """Polling IMAP client with a synchronous worker thread + asyncio facade."""

    def __init__(self, settings: ImapSettings | Config) -> None:
        if isinstance(settings, Config):
            settings = ImapSettings.from_config(settings)
        settings.validate()
        self.settings: ImapSettings = settings
        self._connection: Optional[imaplib.IMAP4] = None
        self._lock = threading.Lock()
        self._consumed_uids: set[str] = set()
        self._reconnect_attempts = 0
        self.stats = {"connections": 0, "messages_scanned": 0, "matches": 0, "timeouts": 0, "errors": 0}

    # ------------------------------------------------------------------ connection
    def _connect_sync(self) -> imaplib.IMAP4:
        """Open (or reuse) the IMAP connection - runs inside the worker thread."""
        if self._connection is not None:
            try:
                self._connection.noop()
                return self._connection
            except Exception:  # noqa: BLE001 - dead connection, reconnect
                self._connection = None

        context: Optional[ssl.SSLContext] = None
        if self.settings.verify_tls:
            context = ssl.create_default_context()
        else:  # pragma: no cover - diagnostics only
            context = ssl._create_unverified_context()  # type: ignore[attr-defined]

        last_error: Optional[Exception] = None
        for attempt in range(1, self.settings.connect_retries + 1):
            try:
                if self.settings.starttls:
                    connection: imaplib.IMAP4 = imaplib.IMAP4(self.settings.host, self.settings.port)
                    connection.starttls(context)
                elif self.settings.use_ssl:
                    connection = imaplib.IMAP4_SSL(self.settings.host, self.settings.port, ssl_context=context)
                else:
                    connection = imaplib.IMAP4(self.settings.host, self.settings.port)
                connection.login(self.settings.username, self.settings.password)
                try:
                    typ, _ = connection.select(self.settings.mailbox, readonly=not self.settings.mark_seen)
                    if typ != "OK":
                        raise ImapError(f"Could not select mailbox '{self.settings.mailbox}'")
                except imaplib.IMAP4.error as exc:
                    raise ImapError(f"Could not select mailbox '{self.settings.mailbox}': {exc}") from exc
                self._connection = connection
                self._reconnect_attempts = 0
                self.stats["connections"] += 1
                logger.debug(
                    "IMAP connected to %s:%s (mailbox=%s, ssl=%s)",
                    self.settings.host,
                    self.settings.port,
                    self.settings.mailbox,
                    self.settings.use_ssl,
                )
                return connection
            except imaplib.IMAP4.error as exc:
                message = str(exc)
                raise ImapAuthError(
                    f"IMAP login/select failed for {self.settings.username}@{self.settings.host}: {message}"
                ) from exc
            except (OSError, ssl.SSLError) as exc:
                last_error = exc
                delay = min(30.0, 1.5 * (2 ** (attempt - 1)))
                logger.warning(
                    "IMAP connect attempt %d/%d failed (%s); retrying in %.1fs",
                    attempt,
                    self.settings.connect_retries,
                    exc,
                    delay,
                )
                time.sleep(delay)
        raise ImapError(f"Could not connect to IMAP server {self.settings.host}:{self.settings.port}: {last_error}")

    def close(self) -> None:
        """Log out and drop the connection (idempotent)."""
        with self._lock:
            if self._connection is None:
                return
            try:
                self._connection.close()
            except Exception:  # noqa: BLE001 - mailbox may not be selected
                pass
            try:
                self._connection.logout()
            except Exception:  # noqa: BLE001
                pass
            self._connection = None

    # ------------------------------------------------------------------ sync search
    def _fetch_sync(self, *, since: Optional[datetime] = None, target_email: Optional[str] = None) -> list[MailMessage]:
        """Synchronously search UIDs and parse the matching messages."""
        with self._lock:
            connection = self._connect_sync()
            criteria: list[str] = []

            if self.settings.unseen_only and not self.settings.mark_seen:
                criteria.append("UNSEEN")
            since_date = (since or (datetime.now() - timedelta(days=max(1, self.settings.search_days))))
            criteria += ["SINCE", since_date.strftime("%d-%b-%Y")]
            if self.settings.sender_filter and "@" in self.settings.sender_filter:
                criteria += ["FROM", self.settings.sender_filter]
            if target_email:
                criteria += ["TO", target_email]

            query = " ".join(f'"{item}"' if " " in item else item for item in criteria)
            try:
                typ, data = connection.uid("SEARCH", None, *criteria)
            except imaplib.IMAP4.error as exc:
                logger.debug("IMAP SEARCH %r failed (%s); retrying without filters", query, exc)
                typ, data = connection.uid("SEARCH", None, "ALL")

            if typ != "OK":
                raise ImapError(f"IMAP search failed: {typ} {data}")

            uids: list[str] = []
            for chunk in data or []:
                if chunk:
                    uids.extend(chunk.decode().split())
            uids = [uid for uid in uids if uid not in self._consumed_uids]
            uids = uids[-40:]  # newest 40 messages are plenty
            if not uids:
                return []

            logger.debug("IMAP search returned %d new candidate message(s)", len(uids))
            messages: list[MailMessage] = []
            for uid in uids:
                try:
                    typ, payload = connection.uid("FETCH", uid, "(RFC822 FLAGS)")
                    if typ != "OK" or not payload:
                        continue
                    raw: Optional[bytes] = None
                    flags = b""
                    for item in payload:
                        if isinstance(item, tuple) and len(item) >= 2:
                            raw = item[1] if isinstance(item[1], bytes) else None
                            if isinstance(item[0], bytes):
                                flags = item[0]
                    if raw is None:
                        continue
                    messages.append(self._parse_message(uid, raw, flags))
                    self.stats["messages_scanned"] += 1
                except Exception as exc:  # noqa: BLE001 - one bad message must not kill the poll
                    logger.debug("Could not fetch/parse UID %s: %s", uid, exc)
            return messages

    def _parse_message(self, uid: str, raw: bytes, flags: bytes = b"") -> MailMessage:
        """Parse a raw RFC822 message into :class:`MailMessage`."""
        message = email.message_from_bytes(raw, policy=email.policy.default)
        text_body, html_body = message_body(message)
        if not text_body and html_body:
            text_body = html_to_text(html_body)

        subject = decode_mime_header(message.get("Subject"))
        sender = decode_mime_header(message.get("From"))
        to_header = decode_mime_header(message.get("To"))
        message_id = decode_mime_header(message.get("Message-ID"))
        date: Optional[datetime] = None
        try:
            parsed_date = email.utils.parsedate_to_datetime(message.get("Date"))
            date = parsed_date
        except Exception:  # noqa: BLE001 - unparsable Date header
            date = None

        links = extract_links(text_body, html_body, pattern=self.settings.link_regex, max_links=self.settings.max_links)
        otp_codes = extract_otp_codes(f"{subject}\n{text_body}", pattern=self.settings.otp_regex)

        return MailMessage(
            uid=uid,
            subject=subject,
            sender=sender,
            to=to_header,
            date=date,
            message_id=message_id,
            body_text=text_body,
            body_html=html_body,
            links=links,
            otp_codes=otp_codes,
            is_seen=b"\\Seen" in (flags or b""),
            folder=self.settings.mailbox,
            headers={
                key: decode_mime_header(value)
                for key, value in list(message.items())[:20]
            },
            score=0.0,
        )

    # ------------------------------------------------------------------ scoring
    def score_message(
        self,
        message: MailMessage,
        *,
        since: Optional[datetime] = None,
        forbidden_uids: Optional[set[str]] = None,
        target_email: Optional[str] = None,
    ) -> tuple[float, str]:
        """Score how likely a message is the verification mail we are waiting for."""
        if forbidden_uids and message.uid in forbidden_uids:
            return 0.0, "already consumed"
        score = 0.0
        reasons: list[str] = []

        if self.settings.subject_regex:
            if re.search(self.settings.subject_regex, message.subject, re.I):
                score += 45.0
                reasons.append(f"subject~/{self.settings.subject_regex}/")
            else:
                score -= 25.0

        if self.settings.sender_filter:
            if re.search(re.escape(self.settings.sender_filter), message.sender, re.I) or re.search(
                self.settings.sender_filter, message.sender, re.I
            ):
                score += 25.0
                reasons.append("sender-match")
            else:
                score -= 20.0

        if target_email and target_email.lower() in (message.to or "").lower():
            score += 25.0
            reasons.append("to-address-match")

        haystack = f"{message.subject}\n{message.body_text}"
        if VERIFY_HINTS.search(haystack):
            score += 20.0
            reasons.append("verification-keyword")
        if message.links:
            score += 12.0
            reasons.append(f"{len(message.links)} link(s)")
        if message.otp_codes:
            score += 8.0
            reasons.append(f"{len(message.otp_codes)} code(s)")

        age = message.age_seconds
        if age is not None:
            if age < 120:
                score += 15.0
                reasons.append("fresh<2min")
            elif age < 600:
                score += 8.0
                reasons.append("fresh<10min")
            elif age > 3600:
                score -= 10.0
                reasons.append("stale>1h")

        if since is not None and message.date is not None:
            reference = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
            message_date = message.date if message.date.tzinfo else message.date.replace(tzinfo=timezone.utc)
            if message_date < reference - timedelta(seconds=30):
                score -= 30.0
                reasons.append("older-than-window")

        return score, "; ".join(reasons) or "no-signal"

    # ------------------------------------------------------------------ public API
    async def fetch_messages(
        self,
        *,
        since: Optional[datetime] = None,
        target_email: Optional[str] = None,
        limit: int = 20,
    ) -> list[MailMessage]:
        """Fetch candidate messages (in a thread) sorted by likelihood, newest first."""
        messages = await asyncio.to_thread(self._fetch_sync, since=since, target_email=target_email)
        for message in messages:
            message.score, message.matched_reason = self.score_message(
                message, since=since, forbidden_uids=self._consumed_uids, target_email=target_email
            )
        messages.sort(key=lambda item: (-item.score, -(item.date.timestamp() if item.date else 0)))
        return messages[:limit]

    async def wait_for_verification(
        self,
        *,
        since: Optional[datetime] = None,
        timeout_s: Optional[float] = None,
        target_email: Optional[str] = None,
        subject_regex: Optional[str] = None,
        sender_filter: Optional[str] = None,
        link_regex: Optional[str] = None,
        otp_regex: Optional[str] = None,
        prefer_same_domain: Optional[str] = None,
        poll_interval_s: Optional[float] = None,
    ) -> ExtractedVerification:
        """Poll until a verification mail arrives, then extract its link/OTP."""
        if subject_regex:
            self.settings.subject_regex = subject_regex
        if sender_filter:
            self.settings.sender_filter = sender_filter
        if link_regex:
            self.settings.link_regex = link_regex
        if otp_regex:
            self.settings.otp_regex = otp_regex

        timeout = float(timeout_s or self.settings.timeout_s)
        interval = float(poll_interval_s or self.settings.poll_interval_s)
        since = since or datetime.now(timezone.utc)
        deadline = time.monotonic() + timeout
        stopwatch = Stopwatch()
        stopwatch.__enter__()
        attempts = 0
        best: Optional[ExtractedVerification] = None

        logger.info(
            "Waiting up to %s for a verification e-mail in %s (subject=%s, from=%s)…",
            f"{timeout:.0f}s",
            self.settings.mailbox,
            self.settings.subject_regex or "*",
            self.settings.sender_filter or "*",
        )

        while time.monotonic() < deadline:
            attempts += 1
            try:
                messages = await self.fetch_messages(since=since, target_email=target_email, limit=10)
            except ImapAuthError:
                raise
            except Exception as exc:  # noqa: BLE001 - transient IMAP failures are retried
                self.stats["errors"] += 1
                logger.warning("IMAP poll failed (%s); retrying", truncate(str(exc), 160))
                await asyncio.sleep(interval)
                continue

            if messages:
                for message in messages:
                    if message.score <= 0:
                        continue
                    extracted = self._extract(message, prefer_same_domain=prefer_same_domain)
                    logger.info(
                        "Mail candidate: '%s' from %s (score %.0f, %s) → %s",
                        truncate(message.subject, 70),
                        truncate(message.sender, 45),
                        message.score,
                        message.matched_reason,
                        "link" if extracted.link else ("otp" if extracted.otp else "no link/otp"),
                    )
                    if extracted.found:
                        self._consumed_uids.add(message.uid)
                        self.stats["matches"] += 1
                        await self._post_process(message)
                        extracted.message = message
                        return extracted
                    if best is None:
                        best = extracted

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(interval, max(0.5, remaining)))

        stopwatch.stop()
        self.stats["timeouts"] += 1
        detail = f" after {attempts} poll(s), {human_ms(stopwatch.elapsed_ms)}"
        if best is not None and best.message:
            detail += f"; best candidate was '{truncate(best.message.subject, 60)}'"
        logger.error("No verification e-mail found%s", detail)
        raise ImapTimeoutError(f"No verification e-mail found{detail}")

    async def close_async(self) -> None:
        """Close the connection from async code."""
        await asyncio.to_thread(self.close)

    # ------------------------------------------------------------------ internals
    def _extract(self, message: MailMessage, *, prefer_same_domain: Optional[str] = None) -> ExtractedVerification:
        """Pick the best verification link / OTP from a message."""
        candidates = list(message.links)
        compiled = re.compile(self.settings.link_regex, re.I) if self.settings.link_regex else None
        if compiled is not None:
            strict = [url for url in candidates if compiled.search(url)]
            if strict:
                candidates = strict
        elif VERIFY_HINTS:
            hinted = [url for url in candidates if VERIFY_HINTS.search(url)]
            if hinted:
                candidates = hinted

        if prefer_same_domain and candidates:
            try:
                wanted = urlparse(prefer_same_domain).netloc.split(":")[0].lower()
                if wanted:
                    same_domain = [url for url in candidates if wanted in urlparse(url).netloc.lower()]
                    if same_domain:
                        candidates = same_domain + [url for url in candidates if url not in same_domain]
            except Exception:  # noqa: BLE001 - malformed target URL
                pass

        link = candidates[0] if candidates else None
        if link:
            return ExtractedVerification(
                message=message,
                link=link,
                otp=message.otp_codes[0] if message.otp_codes else None,
                candidates=candidates,
                method="link",
                reason=f"{len(candidates)} candidate link(s); subject='{truncate(message.subject, 60)}'",
            )
        if message.otp_codes:
            return ExtractedVerification(
                message=message,
                link=None,
                otp=message.otp_codes[0],
                candidates=[],
                method="otp",
                reason="no link found; using numeric verification code",
            )
        return ExtractedVerification(
            message=message,
            method="none",
            reason="message contained neither a usable link nor a numeric code",
        )

    async def _post_process(self, message: MailMessage) -> None:
        """Optionally mark the message seen / move it to the processed mailbox."""
        if not (self.settings.mark_seen or self.settings.mark_processed):
            return
        try:
            await asyncio.to_thread(self._post_process_sync, message)
        except Exception as exc:  # noqa: BLE001 - never fail the run for mailbox bookkeeping
            logger.debug("IMAP post-processing failed: %s", exc)

    def _post_process_sync(self, message: MailMessage) -> None:
        with self._lock:
            connection = self._connect_sync()
            if self.settings.mark_seen:
                connection.uid("STORE", message.uid, "+FLAGS", r"(\Seen)")
            if self.settings.mark_processed:
                processed = self.settings.processed_mailbox
                try:
                    connection.create(processed)
                except Exception:  # noqa: BLE001 - already exists
                    pass
                try:
                    connection.uid("COPY", message.uid, processed)
                    connection.uid("STORE", message.uid, "+FLAGS", r"(\Deleted)")
                    connection.expunge()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Could not move UID %s to %s: %s", message.uid, processed, exc)

    # ------------------------------------------------------------------ diagnostics
    async def check(self) -> dict[str, Any]:
        """Connect + select the mailbox and report basic statistics (used by --dry-run)."""
        def _sync() -> dict[str, Any]:
            with self._lock:
                connection = self._connect_sync()
                typ, data = connection.select(self.settings.mailbox, readonly=True)
                count = data[0].decode() if data and data[0] else "0"
                typ_unseen, unseen = connection.uid("SEARCH", None, "UNSEEN")
                unseen_count = len(unseen[0].split()) if unseen and unseen[0] else 0
                return {
                    "host": self.settings.host,
                    "port": self.settings.port,
                    "mailbox": self.settings.mailbox,
                    "messages": int(count) if str(count).isdigit() else count,
                    "unseen": unseen_count,
                    "select_status": typ,
                }

        return await asyncio.to_thread(_sync)


def pick_best_message(messages: Sequence[MailMessage]) -> Optional[MailMessage]:
    """Return the highest scoring message (helper for tests/tools)."""
    if not messages:
        return None
    return max(messages, key=lambda item: item.score)


def normalize_date(value: Any) -> datetime:
    """Coerce assorted date inputs into a timezone-aware datetime."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = email.utils.parsedate_to_datetime(value)
            if parsed:
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            pass
    return datetime.now(timezone.utc)


__all__ += ["pick_best_message", "normalize_date", "html_to_text"]
