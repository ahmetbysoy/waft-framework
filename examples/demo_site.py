#!/usr/bin/env python3
"""Tiny local web application used to demo/verify the WAFT framework end-to-end.

It intentionally mimics the things that make real automation hard:

*   a **sign-up form** with first/last name, e-mail, phone, company, city select, password +
    password confirmation, birthdate, website, textarea and two checkboxes (KVKK terms,
    newsletter), where every field is described differently (``name``, ``id``, ``placeholder``,
    ``<label for>``, ``aria-label``) - exactly the mess an alias/score-based filler must solve;
*   a **two-step wizard** (``/wizard``) where the password field only appears after clicking
    "Devam";
*   a **login form** (``/login``);
*   an XHR call on page load (``/api/v1/app-config``) so the network monitor has something to
    discover, plus ``/api/v1/register`` used by the JS submit path;
*   a **verification page** (``/verify?token=…``) to emulate e-mail verification links;
*   a **CAPTCHA-like page** (``/captcha``) to exercise detection;
*   a slow endpoint (``/slow?ms=…``) for load-testing scenarios;
*   Turkish success/error texts so the outcome detector's locale heuristics are exercised.

Run it with::

    python examples/demo_site.py --port 8080

then point WAFT at ``http://127.0.0.1:8080/`` (see README for the full demo command).
"""

from __future__ import annotations

import argparse
import json
import random
import smtplib
import time
import urllib.parse
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

PAGE = """<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="/static/app.css">
</head>
<body>
<header class="topbar">
  <a class="brand" href="/">WAFT Demo</a>
  <nav>
    <a href="/">Kayıt</a> <a href="/login">Giriş</a> <a href="/wizard">Sihirbaz</a>
    <a href="/slow?ms=800">Yavaş sayfa</a> <a href="/captcha">Captcha</a>
  </nav>
</header>
<main>
{body}
</main>
<footer><small>WAFT demo site — sunucu tarafı Python stdlib, veri toplanmaz.</small></footer>
<script src="/static/app.js"></script>
</body>
</html>
"""

FIELD_CSS = """
body { font-family: system-ui, Arial, sans-serif; margin: 0; background: #f6f7fb; color: #1d2330; }
.topbar { display: flex; justify-content: space-between; align-items: center; padding: 12px 24px; background: #14213d; color: #fff; }
.topbar a { color: #cbd5f5; margin-right: 12px; text-decoration: none; }
.brand { font-weight: 700; color: #fff !important; }
main { max-width: 760px; margin: 24px auto; background: #fff; padding: 28px; border-radius: 14px; box-shadow: 0 10px 30px rgba(20,33,61,.08); }
h1 { margin-top: 0; font-size: 24px; }
label { display: block; margin: 14px 0 4px; font-weight: 600; font-size: 14px; }
input, select, textarea { width: 100%; padding: 10px 12px; border: 1px solid #d5d9e2; border-radius: 8px; font-size: 15px; box-sizing: border-box; }
input[type=checkbox] { width: auto; margin-right: 8px; }
.row { display: flex; gap: 14px; }
.row > div { flex: 1; }
button { margin-top: 18px; background: #2b6cb0; color: #fff; border: 0; padding: 12px 20px; border-radius: 8px; font-size: 16px; cursor: pointer; }
button.secondary { background: #64748b; }
.notice { padding: 12px 16px; border-radius: 8px; margin: 12px 0; }
.success { background: #e6f6ec; color: #1b6b3a; border: 1px solid #b7e2c6; }
.error { background: #fdeaea; color: #9b1c1c; border: 1px solid #f5c2c2; }
.muted { color: #6b7280; font-size: 13px; }
pre { background: #0f172a; color: #b7e1ff; padding: 14px; border-radius: 10px; overflow: auto; font-size: 12px; }
.hcaptcha-box { border: 2px dashed #b45309; background: #fff7ed; padding: 22px; border-radius: 10px; text-align: center; }
footer { text-align: center; margin: 30px 0; color: #8792a8; }
"""

APP_JS = """
// The demo app fetches its runtime configuration over XHR on every page load:
// this is what the WAFT network monitor discovers as an API endpoint.
document.addEventListener('DOMContentLoaded', function () {
  try {
    fetch('/api/v1/app-config', { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) { return r.json(); })
      .then(function (data) { window.__DEMO_CONFIG__ = data; console.log('app-config loaded', data.version); })
      .catch(function (e) { console.warn('app-config failed', e); });
  } catch (e) {}

  var steps = document.querySelectorAll('[data-wizard-step]');
  var continueBtn = document.getElementById('wizard-continue');
  if (continueBtn) {
    continueBtn.addEventListener('click', function () {
      var step1 = document.getElementById('wizard-step-1');
      var step2 = document.getElementById('wizard-step-2');
      if (step1) step1.style.display = 'none';
      if (step2) step2.style.display = 'block';
      window.__WIZARD_STEP__ = 2;
      fetch('/api/v1/wizard/step/2', { method: 'POST' });
    });
  }
  if (steps.length) { window.__WIZARD_STEP__ = 1; }
});
"""

SIGNUP_FORM = """
<h1>Hesap Oluştur</h1>
<p class="muted">Tüm alanları doldurun, KVKK onayını işaretleyin ve "Kayıt Ol" düğmesine basın.</p>
<form id="signup-form" method="post" action="/submit" autocomplete="on">
  <div class="row">
    <div>
      <label for="fname">Ad</label>
      <input type="text" id="fname" name="first_name" placeholder="Adınız" required autocomplete="given-name">
    </div>
    <div>
      <label for="lname">Soyad</label>
      <input type="text" id="lname" name="last_name" placeholder="Soyadınız" required autocomplete="family-name">
    </div>
  </div>

  <label for="email">E-posta</label>
  <input type="email" id="email" name="email" placeholder="ornek@sirket.com" required autocomplete="email">

  <div class="row">
    <div>
      <label for="phone">Telefon</label>
      <input type="tel" id="phone" name="phone" placeholder="+90 5xx xxx xx xx" autocomplete="tel">
    </div>
    <div>
      <label for="birthdate">Doğum tarihi</label>
      <input type="date" id="birthdate" name="birthdate">
    </div>
  </div>

  <div class="row">
    <div>
      <label for="company">Şirket</label>
      <input type="text" id="company" name="company" placeholder="Şirket adı" autocomplete="organization">
    </div>
    <div>
      <label for="city">Şehir</label>
      <select id="city" name="city">
        <option value="">Seçiniz…</option>
        <option value="istanbul">İstanbul</option>
        <option value="ankara">Ankara</option>
        <option value="izmir">İzmir</option>
        <option value="berlin">Berlin</option>
        <option value="london">London</option>
      </select>
    </div>
  </div>

  <label for="website">Web sitesi</label>
  <input type="url" id="website" name="website" placeholder="https://ornek.com">

  <label for="password">Şifre</label>
  <input type="password" id="password" name="password" placeholder="En az 8 karakter" required autocomplete="new-password">

  <label for="password_confirm">Şifre (tekrar)</label>
  <input type="password" id="password_confirm" name="password_confirm" placeholder="Şifreyi tekrar giriniz" required autocomplete="new-password">

  <label for="message">Notunuz</label>
  <textarea id="message" name="message" rows="3" placeholder="Bize iletmek istediğiniz mesaj"></textarea>

  <p>
    <input type="checkbox" id="terms" name="terms" value="on" required>
    <label for="terms" style="display:inline">KVKK aydınlatma metnini okudum ve kabul ediyorum.</label>
  </p>
  <p>
    <input type="checkbox" id="newsletter" name="newsletter" value="on">
    <label for="newsletter" style="display:inline">Kampanya bültenine abone olmak istiyorum.</label>
  </p>

  <input type="text" name="_hidden_token" id="hidden-token" value="static-demo-token" style="display:none">

  <button type="submit" class="primary">Kayıt Ol</button>
  <button type="button" class="secondary" onclick="location.href='/login'">Giriş yap</button>
</form>
<p class="muted">Bu form bir demo uygulamasıdır; gönderilen veriler yalnızca sunucu konsoluna yazılır.</p>
"""

LOGIN_FORM = """
<h1>Giriş Yap</h1>
<form id="login-form" method="post" action="/submit">
  <label for="login-email">E-posta adresi</label>
  <input type="email" id="login-email" name="email" placeholder="E-posta" required autocomplete="username">
  <label for="login-password">Şifre</label>
  <input type="password" id="login-password" name="password" placeholder="Şifre" required autocomplete="current-password">
  <p><input type="checkbox" id="remember_me" name="remember_me" value="1"> <label for="remember_me" style="display:inline">Beni hatırla</label></p>
  <button type="submit">Giriş</button>
</form>
"""

WIZARD = """
<h1>İki Adımlı Kayıt Sihirbazı</h1>
<p class="muted">1. adımda e-posta, 2. adımda şifre istenir (çok adımlı formların otomasyonu için).</p>
<div id="wizard-step-1" data-wizard-step="1">
  <label for="wizard-email">E-posta</label>
  <input type="email" id="wizard-email" name="email" placeholder="E-posta adresiniz" required>
  <button type="button" id="wizard-continue">Devam</button>
</div>
<div id="wizard-step-2" data-wizard-step="2" style="display:none">
  <form method="post" action="/submit">
    <label for="wizard-password">Şifre belirle</label>
    <input type="password" id="wizard-password" name="password" placeholder="Yeni şifre" required>
    <label for="wizard-phone">Telefon</label>
    <input type="tel" id="wizard-phone" name="phone" placeholder="Telefon numarası">
    <button type="submit">Tamamla</button>
  </form>
</div>
"""

CAPTCHA_PAGE = """
<h1>Güvenlik Doğrulaması</h1>
<p>Bu sayfa bir CAPTCHA/edge challenge sayfasını taklit eder; WAFT bunu <em>tespit eder</em>, çözmez.</p>
<div class="hcaptcha-box" data-sitekey="demo-site-key">
  <div class="h-captcha" data-sitekey="demo-site-key"></div>
  <p>lütfen robot olmadığınızı doğrulayın</p>
</div>
"""


def send_verification_mail(server: ThreadingHTTPServer, to_addr: str, token: str) -> Optional[str]:
    """Best effort SMTP delivery of the account-verification mail (``--smtp-host``).

    The mail mirrors what a real sign-up flow sends: a subject containing the word
    "doğrula" plus a one-click verification link.  It is used by ``qa-kit`` to exercise the
    IMAP watcher end-to-end against a local mail server (Mailpit / MailHog) instead of a
    real inbox.  Returns the message-id on success, ``None`` when SMTP is not configured or
    the hand-off failed (the web flow never fails because of mail problems).
    """
    host = getattr(server, "smtp_host", None)
    if not host:
        return None
    port = int(getattr(server, "smtp_port", 1025))
    mail_from = str(getattr(server, "mail_from", "demo@waft.local"))
    public_url = str(getattr(server, "public_url", "http://127.0.0.1:8080")).rstrip("/")
    link = f"{public_url}/verify?token={token}"

    message = EmailMessage()
    message["From"] = f"WAFT Demo <{mail_from}>"
    message["To"] = to_addr
    message["Subject"] = "Hesabınızı doğrulayın (verify your account)"
    message["X-Demo-Token"] = token
    message.set_content(
        "Merhaba,\n\n"
        "Kaydınızı tamamlamak için aşağıdaki bağlantıya tıklayarak hesabınızı doğrulayın:\n\n"
        f"{link}\n\n"
        "Bu e-postayı siz istemediyseniz yok sayabilirsiniz.\n\n"
        "— WAFT demo uygulaması"
    )
    message.add_alternative(
        "<p>Merhaba,</p><p>Hesabınızı doğrulamak için "
        f'<a href="{link}">bu bağlantıya tıklayın</a>.</p><p>— WAFT demo uygulaması</p>',
        subtype="html",
    )
    try:
        with smtplib.SMTP(host, port, timeout=5) as smtp:
            smtp.send_message(message)
    except Exception as exc:  # noqa: BLE001 - mail problems must never break the demo
        print(f"[demo] verification mail could not be sent via {host}:{port} ({exc})")
        return None
    return message["Message-ID"] or token


class DemoHandler(BaseHTTPRequestHandler):
    """Request handler implementing the demo application."""

    server_version = "WAFTDemo/1.0"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------------ helpers
    def _send(self, body: str, status: int = 200, content_type: str = "text/html; charset=utf-8") -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        # A fake API-ish header so the network monitor sees something interesting.
        self.send_header("X-Demo-Server", "waft-demo")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, payload: Any, status: int = 200) -> None:
        self._send(json.dumps(payload, ensure_ascii=False), status=status, content_type="application/json; charset=utf-8")

    def _page(self, title: str, body: str, status: int = 200) -> None:
        self._send(PAGE.format(title=title, body=body), status=status)

    def _read_form(self) -> Dict[str, List[str]]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        return urllib.parse.parse_qs(raw, keep_blank_values=True)

    def log_message(self, fmt: str, *args: Any) -> None:  # keep the demo output readable
        if self.server.quiet:  # type: ignore[attr-defined]
            return
        print(f"[demo] {self.address_string()} {fmt % args}")

    # ------------------------------------------------------------------ routes
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path in {"/", "/index.html"}:
            self._page("WAFT Demo — Kayıt", SIGNUP_FORM)
        elif path == "/login":
            self._page("WAFT Demo — Giriş", LOGIN_FORM)
        elif path == "/wizard":
            self._page("WAFT Demo — Sihirbaz", WIZARD)
        elif path == "/captcha":
            self._page("WAFT Demo — Doğrulama", CAPTCHA_PAGE)
        elif path == "/dashboard":
            self._page(
                "Panel",
                '<h1>Hoş geldiniz</h1><div class="notice success" data-testid="dashboard">Panele giriş yapıldı.</div>',
            )
        elif path == "/slow":
            delay_ms = int(query.get("ms", ["1000"])[0])
            time.sleep(min(delay_ms, 8000) / 1000.0)
            self._page("Yavaş sayfa", f"<h1>Yavaş sayfa</h1><p>Yanıt {delay_ms} ms gecikti.</p>")
        elif path == "/verify":
            token = query.get("token", ["demo-token"])[0]
            self._page(
                "Doğrulama",
                f'<h1>Hesabınız doğrulandı</h1><div class="notice success" data-testid="verified">'
                f"Token: {token}</div>",
            )
        elif path == "/api/v1/app-config":
            self._json(
                {
                    "version": "1.4.2",
                    "features": ["signup", "login", "wizard"],
                    "endpoints": {"register": "/api/v1/register", "verify": "/verify"},
                    "request_id": f"req-{random.randint(1000, 9999)}",
                }
            )
        elif path == "/api/v1/users":
            self._json({"users": [{"id": index, "email": f"user{index}@example.com"} for index in range(1, 6)]})
        elif path == "/static/app.css":
            self._send(FIELD_CSS, content_type="text/css; charset=utf-8")
        elif path == "/static/app.js":
            self._send(APP_JS, content_type="application/javascript; charset=utf-8")
        elif path == "/robots.txt":
            self._send("User-agent: *\nDisallow: /captcha\n", content_type="text/plain")
        else:
            self._page("Bulunamadı", '<div class="notice error">404 — sayfa bulunamadı.</div>', status=404)

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/v1/register":
            self._json({"status": "ok", "id": random.randint(10000, 99999)}, status=201)
            return
        if parsed.path == "/api/v1/wizard/step/2":
            self._json({"status": "ok", "step": 2})
            return
        if parsed.path == "/submit":
            data = self._read_form()
            email = (data.get("email") or [""])[0]
            if "invalid" in email.lower():
                self._page(
                    "Hata",
                    '<h1>Kayıt tamamlanamadı</h1><div class="notice error" data-testid="error">'
                    "Geçersiz e-posta adresi girdiniz.</div>",
                    status=422,
                )
                return
            summary = {key: values[0] for key, values in sorted(data.items()) if key != "password" and key != "password_confirm"}
            token = f"tok-{random.randint(100000, 999999)}"
            self.server.verification_tokens.append((email, token))  # type: ignore[attr-defined]
            mail_id = send_verification_mail(self.server, email, token)
            mail_note = (
                f"<p class='muted'>Doğrulama e-postası gönderildi (message-id: {mail_id}).</p>"
                if mail_id
                else "<p class='muted'>Doğrulama e-postası gönderildi (demo, SMTP kapalı).</p>"
            )
            self._page(
                "Kayıt başarılı",
                '<h1>Kayıt başarılı 🎉</h1>'
                '<div class="notice success" data-testid="success">Teşekkürler, kaydınız alındı.</div>'
                f'{mail_note}'
                f"<pre>{json.dumps(summary, ensure_ascii=False, indent=2)}</pre>"
                '<p><a href="/dashboard">Panele git</a></p>',
            )
            return
        self._json({"error": "not_found"}, status=404)


def build_server(
    port: int,
    *,
    quiet: bool = False,
    host: str = "0.0.0.0",
    smtp_host: Optional[str] = None,
    smtp_port: int = 1025,
    mail_from: str = "demo@waft.local",
    public_url: Optional[str] = None,
) -> ThreadingHTTPServer:
    """Create the demo HTTP server (not yet started).

    ``smtp_host`` (optional) enables real verification e-mails - point it at Mailpit /
    MailHog while testing the IMAP watcher (see ``qa-kit/README.md``).
    """
    server = ThreadingHTTPServer((host, port), DemoHandler)
    server.daemon_threads = True
    server.quiet = quiet  # type: ignore[attr-defined]
    server.smtp_host = smtp_host  # type: ignore[attr-defined]
    server.smtp_port = smtp_port  # type: ignore[attr-defined]
    server.mail_from = mail_from  # type: ignore[attr-defined]
    server.public_url = public_url or f"http://127.0.0.1:{port}"  # type: ignore[attr-defined]
    server.verification_tokens = []  # type: ignore[attr-defined]
    return server


def main() -> int:
    parser = argparse.ArgumentParser(description="WAFT demo web site")
    parser.add_argument("--port", type=int, default=8080, help="TCP port to listen on (default: 8080)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--quiet", action="store_true", help="Do not log every request")
    parser.add_argument("--smtp-host", type=str, default=None, help="SMTP host for verification mails (e.g. 127.0.0.1)")
    parser.add_argument("--smtp-port", type=int, default=1025, help="SMTP port (Mailpit default: 1025)")
    parser.add_argument("--mail-from", type=str, default="demo@waft.local", help="Envelope/From address")
    parser.add_argument("--public-url", type=str, default=None, help="Base URL used inside e-mail links")
    args = parser.parse_args()

    server = build_server(
        args.port,
        quiet=args.quiet,
        host=args.host,
        smtp_host=args.smtp_host,
        smtp_port=args.smtp_port,
        mail_from=args.mail_from,
        public_url=args.public_url,
    )
    print(f"WAFT demo site listening on http://{args.host}:{args.port}/  (Ctrl+C to stop)")
    if args.smtp_host:
        print(f"Verification mails -> smtp://{args.smtp_host}:{args.smtp_port} (from {args.mail_from})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down…")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
