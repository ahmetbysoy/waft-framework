#!/usr/bin/env python3
"""``devmail.py`` - dependency-free SMTP + IMAP4rev1 development server (test double for Mailpit).

Why: the sign-up flow under test sends a verification e-mail, and the WAFT IMAP watcher has to
pick the link out of it.  In CI you want that hop to be real (no mocks), but you also do not
want a Docker daemon or a real mailbox.  ``devmail.py`` speaks just enough SMTP and IMAP for
that loop:

    demo app / staging  --SMTP-->  devmail (in-memory store)  --IMAP-->  WAFT

Supported IMAP commands (IMAP4rev1 subset): ``CAPABILITY``, ``NOOP``, ``LOGIN``, ``LOGOUT``,
``LIST``, ``CREATE``, ``SELECT``, ``EXAMINE``, ``CLOSE``, ``CHECK``, ``STATUS``, ``SEARCH``,
``FETCH``, ``STORE``, ``COPY``, ``UID SEARCH``, ``UID FETCH``, ``UID STORE``, ``UID COPY``.
Mail is stored in memory only; restart the process and the mailbox is empty again.

Run it::

    python qa-kit/devmail.py --smtp-port 1025 --imap-port 1430 --verbose

Then point the application under test at ``smtp://127.0.0.1:1025`` and WAFT at
``--imap-host 127.0.0.1 --imap-port 1430 --imap-user devmail --imap-password devmail --imap-no-ssl``.

Exit codes: ``0`` clean shutdown, ``2`` bad arguments / ports busy, ``130`` Ctrl+C.
"""

from __future__ import annotations

import argparse
import email
import socketserver
import sys
import threading
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Final, Iterable, Optional, Sequence

CRLF: Final[bytes] = b"\r\n"
MONTHS: Final[tuple[str, ...]] = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# --------------------------------------------------------------------------------------
# message store
# --------------------------------------------------------------------------------------
@dataclass(slots=True)
class StoredMessage:
    """One message plus its IMAP metadata."""

    uid: int
    raw: bytes
    flags: set[str] = field(default_factory=set)
    received_at: float = field(default_factory=time.time)

    @property
    def subject(self) -> str:
        try:
            return email.message_from_bytes(self.raw).get("Subject", "") or ""
        except Exception:  # noqa: BLE001
            return ""

    @property
    def sender(self) -> str:
        try:
            return email.message_from_bytes(self.raw).get("From", "") or ""
        except Exception:  # noqa: BLE001
            return ""

    @property
    def recipient(self) -> str:
        try:
            return email.message_from_bytes(self.raw).get("To", "") or ""
        except Exception:  # noqa: BLE001
            return ""


class MailStore:
    """Thread-safe in-memory mailbox."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._messages: list[StoredMessage] = []
        self._next_uid = 1

    def append(self, raw: bytes) -> StoredMessage:
        with self._lock:
            message = StoredMessage(uid=self._next_uid, raw=raw)
            self._next_uid += 1
            self._messages.append(message)
            return message

    def all(self) -> list[StoredMessage]:
        with self._lock:
            return list(self._messages)

    def by_uid(self, uid: int) -> Optional[StoredMessage]:
        with self._lock:
            for message in self._messages:
                if message.uid == uid:
                    return message
        return None

    def sequence_of(self, uid: int) -> Optional[int]:
        with self._lock:
            for position, message in enumerate(self._messages, start=1):
                if message.uid == uid:
                    return position
        return None

    def uids_matching(self, criteria: Sequence[str]) -> list[int]:
        """Very small SEARCH evaluator: UNSEEN/SEEN/ALL/SINCE/FROM/TO/SUBJECT/BODY."""
        with self._lock:
            messages = list(self._messages)
        tokens = [token.upper() for token in criteria]
        unseen_only = "UNSEEN" in tokens
        seen_only = "SEEN" in tokens
        since_ts: Optional[float] = None
        sender_filter: Optional[str] = None
        recipient_filter: Optional[str] = None
        subject_filter: Optional[str] = None
        text_filter: Optional[str] = None

        index = 0
        while index < len(criteria):
            token = criteria[index].upper()
            if token in {"SINCE", "ON", "FROM", "TO", "SUBJECT", "BODY", "TEXT", "HEADER"} and index + 1 < len(criteria):
                raw_value = criteria[index + 1]
                if token in {"SINCE", "ON"}:
                    parsed = _parse_imap_date(raw_value)
                    since_ts = parsed
                elif token == "FROM":
                    sender_filter = raw_value.lower().strip('"')
                elif token == "TO":
                    recipient_filter = raw_value.lower().strip('"')
                elif token == "SUBJECT":
                    subject_filter = raw_value.lower().strip('"')
                elif token in {"TEXT", "BODY", "HEADER"}:
                    text_filter = raw_value.lower().strip('"')
                index += 2
                continue
            index += 1

        matched: list[int] = []
        for message in messages:
            if unseen_only and "\\Seen" in message.flags:
                continue
            if seen_only and "\\Seen" not in message.flags:
                continue
            if since_ts is not None and message.received_at < since_ts:
                continue
            if sender_filter and sender_filter not in message.sender.lower():
                continue
            if recipient_filter and recipient_filter not in message.recipient.lower():
                continue
            if subject_filter and subject_filter not in message.subject.lower():
                continue
            if text_filter and text_filter not in (message.subject + " " + _body_text(message.raw)).lower():
                continue
            matched.append(message.uid)
        return matched


def _body_text(raw: bytes, limit: int = 20000) -> str:
    try:
        message = email.message_from_bytes(raw)
        if message.is_multipart():
            parts = [part.get_payload(decode=True) or b"" for part in message.walk() if not part.is_multipart()]
            return b" ".join(parts)[:limit].decode("utf-8", errors="replace")
        return (message.get_payload(decode=True) or b"")[:limit].decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return raw[:limit].decode("utf-8", errors="replace")


def _parse_imap_date(value: str) -> Optional[float]:
    """Parse IMAP ``SINCE`` dates (``26-Sep-2026`` / ``26-Sep-2026 12:00:00 +0000``)."""
    cleaned = value.strip().strip('"')
    try:
        parsed = parsedate_to_datetime(cleaned)
        return parsed.timestamp()
    except Exception:  # noqa: BLE001 - fall through to the manual parser
        pass
    parts = cleaned.split()
    if len(parts) >= 3:
        try:
            day = int(parts[0])
            month = MONTHS.index(parts[1][:3].title()) + 1
            year = int(parts[2])
            return time.mktime((year, month, day, 0, 0, 0, 0, 0, -1))
        except Exception:  # noqa: BLE001
            return None
    return None


# --------------------------------------------------------------------------------------
# SMTP
# --------------------------------------------------------------------------------------
class SmtpHandler(socketserver.StreamRequestHandler):
    """Minimal SMTP: enough for ``smtplib.SMTP(...).send_message(...)``."""

    server: "SmtpServer"

    def handle(self) -> None:  # noqa: C901 - flat protocol loop, kept readable
        self._write(b"220 waft-devmail ESMTP ready")
        in_data = False
        buffer: list[bytes] = []
        while True:
            line = self.rfile.readline()
            if not line:
                return
            if in_data:
                if line.strip() == b".":
                    in_data = False
                    raw = b"".join(buffer)
                    message = self.server.store.append(raw)
                    self.server.log(
                        f"[smtp] stored UID {message.uid}: to={message.recipient!r} subject={message.subject!r}"
                    )
                    buffer = []
                    self._write(b"250 2.0.0 Ok: queued")
                else:
                    buffer.append(line if line.endswith(CRLF) else line + CRLF)
                continue

            command, _, argument = line.decode("utf-8", errors="replace").strip().partition(" ")
            command = command.upper()
            if command in {"EHLO", "HELO"}:
                self._write(b"250-waft-devmail\r\n250-SIZE 10485760\r\n250-8BITMIME\r\n250-AUTH LOGIN PLAIN\r\n250 OK")
            elif command == "AUTH":
                self._write(b"235 2.7.0 Authentication successful")
            elif command in {"MAIL", "RCPT"}:
                self._write(b"250 2.1.0 Ok")
            elif command == "DATA":
                in_data = True
                buffer = []
                self._write(b"354 End data with <CR><LF>.<CR><LF>")
            elif command == "RSET":
                buffer = []
                self._write(b"250 2.0.0 Ok")
            elif command == "NOOP":
                self._write(b"250 2.0.0 Ok")
            elif command == "QUIT":
                self._write(b"221 2.0.0 Bye")
                return
            else:
                self.server.log(f"[smtp] unsupported command: {command} {argument}")
                self._write(b"250 2.0.0 Ok (ignored)")

    def _write(self, payload: bytes) -> None:
        self.wfile.write(payload + CRLF)
        self.wfile.flush()


class SmtpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], store: MailStore, verbose: bool) -> None:
        super().__init__(address, SmtpHandler)
        self.store = store
        self.verbose = verbose

    def log(self, message: str) -> None:
        print(message, flush=True)


# --------------------------------------------------------------------------------------
# IMAP
# --------------------------------------------------------------------------------------
class ImapHandler(socketserver.StreamRequestHandler):
    """IMAP4rev1 subset: the exact commands imaplib/WAFT use for verification polling."""

    server: "ImapServer"
    selected = False
    authenticated = False
    read_only = False

    def handle(self) -> None:  # noqa: C901 - protocol dispatch
        self.selected = False
        self.authenticated = False
        self._write(b"* OK [CAPABILITY IMAP4rev1] waft-devmail ready")
        while True:
            line = self.rfile.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not text.strip():
                continue
            self.server.log(f"[imap] {text}")
            tag, _, remainder = text.partition(" ")
            if not tag:
                continue
            parts = _tokenize(remainder)
            if not parts:
                self._write(f"{tag} BAD empty command".encode())
                continue
            command = parts[0].upper()
            args = parts[1:]

            if command == "UID" and args:
                sub = args[0].upper()
                rest = args[1:]
                if sub == "SEARCH":
                    self._uid_search(tag, rest)
                elif sub == "FETCH":
                    self._uid_fetch(tag, rest)
                elif sub == "STORE":
                    self._store(tag, rest, uid_mode=True)
                elif sub == "COPY":
                    self._copy(tag, rest)
                else:
                    self._write(f"{tag} OK UID {sub} completed".encode())
                continue

            if command == "CAPABILITY":
                self._write(b"* CAPABILITY IMAP4rev1")
                self._write(f"{tag} OK CAPABILITY completed".encode())
            elif command == "NOOP":
                self._write(f"{tag} OK NOOP completed".encode())
            elif command == "LOGIN":
                self.authenticated = True
                self._write(f"{tag} OK LOGIN completed".encode())
            elif command == "AUTHENTICATE":
                self._write(b"+ ")
                self.rfile.readline()
                self.authenticated = True
                self._write(f"{tag} OK AUTHENTICATE completed".encode())
            elif command == "LIST":
                self._write(b'* LIST (\\HasNoChildren) "." "INBOX"')
                self._write(f'{tag} OK LIST completed'.encode())
            elif command == "CREATE":
                self._write(f"{tag} OK CREATE completed".encode())
            elif command in {"SELECT", "EXAMINE"}:
                self.selected = True
                self.read_only = command == "EXAMINE"
                messages = self.server.store.all()
                self._write(b"* FLAGS (\\Seen \\Answered \\Flagged \\Deleted \\Draft)")
                self._write(f"* {len(messages)} EXISTS".encode())
                self._write(b"* 0 RECENT")
                self._write(b"* OK [UIDVALIDITY 1] UIDs valid")
                next_uid = (messages[-1].uid + 1) if messages else 1
                self._write(f"* OK [UIDNEXT {next_uid}] Predicted next UID".encode())
                mode = b"READ-ONLY" if self.read_only else b"READ-WRITE"
                self._write(f"{tag} OK [{mode.decode()}] {command} completed".encode())
            elif command in {"CLOSE", "UNSELECT"}:
                self.selected = False
                self._write(f"{tag} OK {command} completed".encode())
            elif command == "CHECK":
                self._write(f"{tag} OK CHECK completed".encode())
            elif command == "STATUS":
                count = len(self.server.store.all())
                self._write(b'* STATUS "INBOX" (MESSAGES %d UNSEEN %d)' % (count, count))
                self._write(f"{tag} OK STATUS completed".encode())
            elif command == "SEARCH":
                self._search(tag, args, uid_mode=False)
            elif command == "FETCH":
                self._fetch(tag, args, uid_mode=False)
            elif command == "STORE":
                self._store(tag, args, uid_mode=False)
            elif command == "COPY":
                self._copy(tag, args)
            elif command == "LOGOUT":
                self._write(b"* BYE waft-devmail closing connection")
                self._write(f"{tag} OK LOGOUT completed".encode())
                return
            else:
                self.server.log(f"[imap] unsupported command: {command}")
                self._write(f"{tag} OK {command} completed".encode())

    # ------------------------------------------------------------------------ helpers
    def _write(self, payload: bytes) -> None:
        self.wfile.write(payload + CRLF)
        self.wfile.flush()

    def _write_literal(self, prefix: str, payload: bytes, suffix: str = ")") -> None:
        self.wfile.write(prefix.encode() + b"{" + str(len(payload)).encode() + b"}" + CRLF)
        self.wfile.write(payload + suffix.encode() + CRLF)
        self.wfile.flush()

    def _search(self, tag: str, args: Sequence[str], *, uid_mode: bool) -> None:
        criteria = [item for item in args if not item.startswith("CHARSET")]
        uids = self.server.store.uids_matching(criteria)
        numbers = [str(uid) if uid_mode else str(self.server.store.sequence_of(uid) or 0) for uid in uids]
        self._write(f"* SEARCH {' '.join(numbers)}".rstrip().encode())
        self._write(f"{tag} OK SEARCH completed".encode())

    def _uid_search(self, tag: str, args: Sequence[str]) -> None:
        self._search(tag, args, uid_mode=True)

    def _resolve_targets(self, spec: str, *, uid_mode: bool) -> list[StoredMessage]:
        messages = self.server.store.all()
        if spec in {"1:*", "*", "1:*"}:
            return messages
        specs: list[str] = []
        for chunk in spec.split(","):
            specs.extend(chunk.split(":") if ":" in chunk else [chunk])
        requested: list[StoredMessage] = []
        for token in specs:
            token = token.strip()
            if token == "*":
                requested.extend(messages)
                continue
            if not token.isdigit():
                continue
            value = int(token)
            if uid_mode:
                message = self.server.store.by_uid(value)
                if message:
                    requested.append(message)
            else:
                position = self.server.store.sequence_of(value)
                if position is not None and position == value and 1 <= value <= len(messages):
                    requested.append(messages[value - 1])
        # de-duplicate while keeping order
        seen: set[int] = set()
        result: list[StoredMessage] = []
        for message in requested:
            if message.uid not in seen:
                seen.add(message.uid)
                result.append(message)
        return result

    def _fetch(self, tag: str, args: Sequence[str], *, uid_mode: bool) -> None:
        if not args:
            self._write(f"{tag} BAD FETCH needs a sequence set".encode())
            return
        target_spec = args[0]
        items = " ".join(args[1:]).upper() if len(args) > 1 else "RFC822"
        wants_flags = "FLAGS" in items or "RFC822" in items
        for message in self._resolve_targets(target_spec, uid_mode=uid_mode):
            sequence = self.server.store.sequence_of(message.uid) or 0
            flag_text = " ".join(sorted(message.flags))
            if wants_flags and not self.read_only and self.server.store_flag_changes:
                message.flags.add("\\Seen")
                flag_text = " ".join(sorted(message.flags))
            if "BODY.PEEK" in items and "RFC822" not in items:
                self._write_literal(
                    f"* {sequence} FETCH (UID {message.uid} FLAGS ({flag_text}) BODY[] ",
                    message.raw,
                )
            elif "FKAGS" in items or "FLAGS" in items and "BODY" not in items and "RFC822" not in items:
                self._write(f"* {sequence} FETCH (UID {message.uid} FLAGS ({flag_text}))".encode())
            else:
                self._write_literal(
                    f"* {sequence} FETCH (UID {message.uid} FLAGS ({flag_text}) RFC822 ",
                    message.raw,
                )
        self._write(f"{tag} OK {'UID ' if uid_mode else ''}FETCH completed".encode())

    def _uid_fetch(self, tag: str, args: Sequence[str]) -> None:
        self._fetch(tag, args, uid_mode=True)

    def _store(self, tag: str, args: Sequence[str], *, uid_mode: bool) -> None:
        if len(args) < 3:
            self._write(f"{tag} OK STORE completed".encode())
            return
        target_spec, operation, flags_arg = args[0], args[1].upper(), " ".join(args[2:])
        flags = {item.strip() for item in flags_arg.strip("()").split() if item.strip()}
        for message in self._resolve_targets(target_spec, uid_mode=uid_mode):
            sequence = self.server.store.sequence_of(message.uid) or 0
            if operation.startswith("+"):
                message.flags.update(flags)
            elif operation.startswith("-"):
                message.flags.difference_update(flags)
            else:
                message.flags = set(flags)
            flag_text = " ".join(sorted(message.flags))
            self._write(f"* {sequence} FETCH (UID {message.uid} FLAGS ({flag_text}))".encode())
        self._write(f"{tag} OK {'UID ' if uid_mode else ''}STORE completed".encode())

    def _copy(self, tag: str, args: Sequence[str]) -> None:
        self._write(f"{tag} OK COPY completed".encode())


class ImapServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], store: MailStore, verbose: bool, store_flag_changes: bool = False) -> None:
        super().__init__(address, ImapHandler)
        self.store = store
        self.verbose = verbose
        self.store_flag_changes = store_flag_changes

    def log(self, message: str) -> None:
        if self.verbose:
            print(message, flush=True)


# --------------------------------------------------------------------------------------
# helpers / CLI
# --------------------------------------------------------------------------------------
def _tokenize(text: str) -> list[str]:
    """Split an IMAP argument string honouring double quotes and parenthesised lists."""
    tokens: list[str] = []
    current = ""
    quoted = False
    for char in text.strip():
        if char == '"':
            quoted = not quoted
            continue
        if char.isspace() and not quoted:
            if current:
                tokens.append(current)
                current = ""
            continue
        current += char
    if current:
        tokens.append(current)
    return tokens


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="devmail.py",
        description="Dependency-free SMTP + IMAP test server for the WAFT verification flow.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (localhost only by default).")
    parser.add_argument("--smtp-port", type=int, default=1025, help="SMTP listen port.")
    parser.add_argument("--imap-port", type=int, default=1430, help="IMAP listen port.")
    parser.add_argument("--verbose", action="store_true", help="Log every SMTP/IMAP command.")
    parser.add_argument("--keep-flags", action="store_true", help="Honour \\Seen changes (default: never mark seen).")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    store = MailStore()
    try:
        smtp = SmtpServer((args.host, args.smtp_port), store, args.verbose)
        imap = ImapServer((args.host, args.imap_port), store, args.verbose, store_flag_changes=args.keep_flags)
    except OSError as exc:
        print(f"✖ could not bind {args.host}:{args.smtp_port}/{args.imap_port}: {exc}", file=sys.stderr)
        return 2

    print(f"devmail ready — SMTP {args.host}:{args.smtp_port} · IMAP {args.host}:{args.imap_port}")
    print("  WAFT:  --imap --imap-host {h} --imap-port {p} --imap-user devmail --imap-password devmail --imap-no-ssl".format(
        h=args.host, p=args.imap_port
    ))
    threads = [
        threading.Thread(target=smtp.serve_forever, name="smtp", daemon=True),
        threading.Thread(target=imap.serve_forever, name="imap", daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        while all(thread.is_alive() for thread in threads):
            time.sleep(0.3)
    except KeyboardInterrupt:
        print("\ndevmail shutting down…")
    finally:
        smtp.shutdown()
        imap.shutdown()
        smtp.server_close()
        imap.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
