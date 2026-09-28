#!/usr/bin/env python3
"""``offerwall_sandbox.py`` - local offerwall / survey platform used to test the WAFT kit.

It deliberately mimics the *shape* of a commercial offerwall (registration -> e-mail
verification -> survey completion -> conversion postback) so that the same regression and
load-test package can be exercised end-to-end **without touching anyone else's platform**:

* ``/register``            sign-up form (e-mail, password + confirmation, first/last name,
                           country select, terms checkbox) - every field labelled differently
                           so alias matching is really tested;
* ``/verify?token=…``      the landing page of the verification link sent by e-mail;
* ``/surveys``             survey list; ``/survey/1`` and ``/survey/2`` are real forms
                           (radio / select / checkbox / textarea) that end in a completion page;
* ``/api/v1/app-config``   XHR fired on page load (network monitor / HAR content);
* ``/api/v1/surveys``      JSON survey catalogue;
* ``/api/v1/postback``     conversion callback endpoint (``uid``, ``status``, ``payout``);
* ``/captcha``             a reCAPTCHA-shaped page so ``--captcha-action`` can be exercised;
* ``/slow?ms=…``           latency injection for load testing;
* ``/robots.txt``.

Sign-up mails are sent over SMTP when ``--smtp-host`` is given (point it at Mailpit or
``qa-kit/devmail.py``); otherwise the shop stays silent and registration still succeeds.

Run::

    python examples/offerwall_sandbox.py --port 8090 --smtp-host 127.0.0.1 --smtp-port 1025

Exit codes: 0 clean shutdown, 2 bad arguments/ports busy.
"""

from __future__ import annotations

import argparse
import json
import random
import smtplib
import sys
import time
import urllib.parse
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------------------
# shared markup
# --------------------------------------------------------------------------------------
PAGE = """<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{ color-scheme: light; --brand:#5b21b6; --ok:#15803d; --bad:#b91c1c; }}
    body {{ font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif; margin:0; background:#f6f7fb; color:#111827; }}
    header {{ background:var(--brand); color:#fff; padding:14px 22px; font-weight:700; letter-spacing:.2px; }}
    main {{ max-width:720px; margin:28px auto; background:#fff; padding:26px 30px; border-radius:14px;
            box-shadow:0 6px 24px rgba(17,24,39,.08); }}
    h1 {{ font-size:22px; margin:0 0 6px; }}
    p.muted {{ color:#6b7280; font-size:14px; }}
    label {{ display:block; margin:14px 0 5px; font-size:14px; font-weight:600; }}
    input[type=text], input[type=email], input[type=password], input[type=tel], select, textarea {{
      width:100%; padding:10px 12px; border:1px solid #d1d5db; border-radius:9px; font-size:14px; box-sizing:border-box; }}
    button {{ margin-top:18px; background:var(--brand); color:#fff; border:0; border-radius:9px;
              padding:11px 20px; font-size:15px; font-weight:600; cursor:pointer; }}
    .notice {{ border-radius:10px; padding:12px 14px; margin:16px 0; font-size:14px; }}
    .notice.success {{ background:#ecfdf5; color:var(--ok); border:1px solid #a7f3d0; }}
    .notice.error {{ background:#fef2f2; color:var(--bad); border:1px solid #fecaca; }}
    .row {{ display:flex; gap:14px; }} .row > div {{ flex:1; }}
    ul.surveys li {{ margin:10px 0; }} .badge {{ background:#ede9fe; color:#5b21b6; border-radius:999px;
      padding:2px 10px; font-size:12px; font-weight:700; }}
  </style>
</head>
<body>
  <header>Offerwall Sandbox (yerel test sistemi)</header>
  <main>{body}</main>
</body>
</html>
"""

REGISTER_FORM = """
<h1>Kayıt ol</h1>
<p class="muted">Anketlere katılmak için hesap oluşturun. Bu sayfa yalnızca yerel test içindir.</p>
<form id="register-form" method="post" action="/register">
  <div class="row">
    <div><label for="first-name">Ad</label>
      <input type="text" id="first-name" name="first_name" placeholder="Adınız" required autocomplete="given-name"></div>
    <div><label for="last-name">Soyad</label>
      <input type="text" id="last-name" name="last_name" placeholder="Soyadınız" required autocomplete="family-name"></div>
  </div>
  <label for="register-email">E-posta adresi</label>
  <input type="email" id="register-email" name="email" placeholder="ornek@domain.com" required autocomplete="email">
  <label for="register-password">Parola</label>
  <input type="password" id="register-password" name="password" placeholder="En az 8 karakter" required autocomplete="new-password">
  <label for="register-password-confirm">Parola (tekrar)</label>
  <input type="password" id="register-password-confirm" name="password_confirm" placeholder="Parolayı tekrar girin" required autocomplete="new-password">
  <label for="country">Ülke</label>
  <select id="country" name="country">
    <option value="TR">Türkiye</option><option value="DE">Almanya</option>
    <option value="US">ABD</option><option value="GB">Birleşik Krallık</option>
  </select>
  <label style="display:flex;gap:8px;align-items:center;font-weight:500;margin-top:14px">
    <input type="checkbox" id="terms" name="terms" value="on" required> Kullanım koşullarını ve KVKK metnini okudum.
  </label>
  <label style="display:flex;gap:8px;align-items:center;font-weight:500">
    <input type="checkbox" id="newsletter" name="newsletter" value="1"> Kampanya e-postaları almak istiyorum.
  </label>
  <button type="submit" data-testid="register-submit" id="register-submit">Hesap oluştur</button>
</form>
<script>
  // same-origin XHR so the network monitor / HAR capture has API traffic to analyse
  fetch('/api/v1/app-config').then(r => r.json()).then(cfg => console.log('app-config', cfg));
</script>
"""

SURVEY_LIST = """
<h1>Anketler</h1>
<p class="muted">Tamamlanan her anket postback ile bildirilir.</p>
<ul class="surveys">
  <li><span class="badge">3 dk</span> <a href="/survey/1">Tüketici alışkanlıkları anketi</a></li>
  <li><span class="badge">2 dk</span> <a href="/survey/2">Uygulama memnuniyeti anketi</a></li>
</ul>
<script>fetch('/api/v1/surveys').then(r => r.json()).then(d => console.log('surveys', d));</script>
"""

SURVEY_1 = """
<h1>Tüketici alışkanlıkları anketi</h1>
<p class="muted">3 soru · yaklaşık 3 dakika</p>
<form id="survey-form" method="post" action="/survey/1/submit">
  <label>Ne sıklıkla çevrimiçi alışveriş yaparsınız?</label>
  <label style="font-weight:500"><input type="radio" name="frequency" value="weekly" required> Haftada birkaç kez</label>
  <label style="font-weight:500"><input type="radio" name="frequency" value="monthly"> Ayda birkaç kez</label>
  <label style="font-weight:500"><input type="radio" name="frequency" value="rarely"> Nadiren</label>
  <label for="favourite-category">En çok hangi kategoride alışveriş yaparsınız?</label>
  <select id="favourite-category" name="category">
    <option value="electronics">Elektronik</option><option value="fashion">Giyim</option>
    <option value="grocery">Market</option><option value="books">Kitap</option>
  </select>
  <label for="comments">Eklemek istediğiniz not</label>
  <textarea id="comments" name="comments" rows="3" placeholder="Kısa bir not (opsiyonel)"></textarea>
  <button type="submit" data-testid="survey-submit" id="survey-submit">Anketi gönder</button>
</form>
"""

SURVEY_2 = """
<h1>Uygulama memnuniyeti anketi</h1>
<p class="muted">2 soru · yaklaşık 2 dakika</p>
<form id="survey-form" method="post" action="/survey/2/submit">
  <label for="rating">Uygulamayı 1-5 arasında puanlar mısınız?</label>
  <select id="rating" name="rating">
    <option value="5">5 - Çok iyi</option><option value="4">4 - İyi</option>
    <option value="3">3 - Orta</option><option value="2">2 - Kötü</option><option value="1">1 - Çok kötü</option>
  </select>
  <label for="contact-email">Ödül bildirimi için e-posta</label>
  <input type="email" id="contact-email" name="contact_email" placeholder="ornek@domain.com" required>
  <label style="display:flex;gap:8px;align-items:center;font-weight:500;margin-top:14px">
    <input type="checkbox" id="contact-consent" name="contact_consent" value="1" required> İletişim kurulmasını kabul ediyorum.
  </label>
  <button type="submit" data-testid="survey-submit" id="survey-submit">Anketi tamamla</button>
</form>
"""

CAPTCHA_PAGE = """
<h1>Doğrulama gerekli</h1>
<div class="notice error" data-testid="captcha">Bu isteğin bot olmadığını doğrulayın.</div>
<div id="captcha" class="g-recaptcha" data-sitekey="sandbox-site-key"></div>
<iframe src="/static/recaptcha-frame.html" title="captcha" width="300" height="74"></iframe>
"""

RECHAPTCHA_FRAME = "<!doctype html><html><body style='font-family:sans-serif'>Ben robot değilim (sandbox).</body></html>"


# --------------------------------------------------------------------------------------
# mail
# --------------------------------------------------------------------------------------
def send_verification_mail(server: ThreadingHTTPServer, to_addr: str, token: str) -> Optional[str]:
    """Send the verification mail (best effort; never breaks registration)."""
    host = getattr(server, "smtp_host", None)
    if not host:
        return None
    port = int(getattr(server, "smtp_port", 1025))
    mail_from = str(getattr(server, "mail_from", "no-reply@offerwall.sandbox"))
    public_url = str(getattr(server, "public_url", "http://127.0.0.1:8090")).rstrip("/")
    link = f"{public_url}/verify?token={token}&email={urllib.parse.quote(to_addr)}"

    message = EmailMessage()
    message["From"] = f"Offerwall Sandbox <{mail_from}>"
    message["To"] = to_addr
    message["Subject"] = "Hesabınızı doğrulayın / please verify your account"
    message["X-Sandbox-Token"] = token
    message.set_content(
        "Merhaba,\n\nHesabınızı doğrulamak için bağlantıya tıklayın / verify your account:\n\n"
        f"{link}\n\n— Offerwall Sandbox"
    )
    message.add_alternative(
        f'<p>Merhaba,</p><p><a href="{link}">Hesabınızı doğrulayın / verify your account</a></p>'
        "<p>— Offerwall Sandbox</p>",
        subtype="html",
    )
    try:
        with smtplib.SMTP(host, port, timeout=5) as smtp:
            smtp.send_message(message)
    except Exception as exc:  # noqa: BLE001 - mail must never break the flow
        print(f"[sandbox] verification mail to {to_addr} failed via {host}:{port} ({exc})")
        return None
    return message["Message-ID"] or token


# --------------------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------------------
class SandboxHandler(BaseHTTPRequestHandler):
    server_version = "OfferwallSandbox/1.0"

    # ------------------------------------------------------------------ plumbing
    def _send(self, body: str, status: int = 200, content_type: str = "text/html; charset=utf-8") -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Sandbox-Server", "offerwall")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, payload: Any, status: int = 200) -> None:
        self._send(json.dumps(payload, ensure_ascii=False), status, "application/json; charset=utf-8")

    def _page(self, title: str, body: str, status: int = 200) -> None:
        self._send(PAGE.format(title=title, body=body), status)

    def _read_form(self) -> Dict[str, List[str]]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        return urllib.parse.parse_qs(raw, keep_blank_values=True)

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: N802 - stdlib naming
        if getattr(self.server, "quiet", False):
            return
        print(f"[sandbox] {self.address_string()} {fmt % args}")

    # ------------------------------------------------------------------ GET
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)

        if path in {"/", "/index.html"}:
            self._page("Offerwall Sandbox", """
                <h1>Offerwall Sandbox</h1>
                <p class="muted">Kayıt + e-posta doğrulama + anket akışının yerel test ikizi.</p>
                <ul class="surveys">
                  <li><a href="/register">Hesap oluştur</a></li>
                  <li><a href="/surveys">Anketleri gör</a></li>
                  <li><a href="/captcha">CAPTCHA sayfası</a></li>
                </ul>""")
        elif path == "/register":
            self._page("Kayıt ol", REGISTER_FORM)
        elif path == "/surveys":
            self._page("Anketler", SURVEY_LIST)
        elif path == "/survey/1":
            self._page("Anket 1", SURVEY_1)
        elif path == "/survey/2":
            self._page("Anket 2", SURVEY_2)
        elif path == "/verify":
            token = query.get("token", ["sandbox-token"])[0]
            email = query.get("email", ["?"])[0]
            self.server.verifications.append((email, token))  # type: ignore[attr-defined]
            self._page(
                "Doğrulama",
                "<h1>Hesabınız doğrulandı</h1>"
                '<div class="notice success" data-testid="verified">'
                f"E-posta: {email} · token: {token}</div>",
            )
        elif path == "/captcha":
            self._page("Doğrulama gerekli", CAPTCHA_PAGE)
        elif path == "/static/recaptcha-frame.html":
            self._send(RECHAPTCHA_FRAME)
        elif path == "/slow":
            delay_ms = int((query.get("ms", ["1000"])[0] or "1000"))
            time.sleep(min(delay_ms, 8000) / 1000.0)
            self._page("Yavaş yanıt", f"<h1>Yavaş yanıt</h1><p>{delay_ms} ms gecikme uygulandı.</p>")
        elif path == "/api/v1/app-config":
            self._json({
                "version": "2.3.0",
                "features": ["register", "survey", "postback"],
                "endpoints": {"surveys": "/api/v1/surveys", "postback": "/api/v1/postback"},
                "request_id": f"req-{random.randint(1000, 9999)}",
            })
        elif path == "/api/v1/surveys":
            self._json({
                "surveys": [
                    {"id": 1, "title": "Tüketici alışkanlıkları", "minutes": 3, "payout": 0.45},
                    {"id": 2, "title": "Uygulama memnuniyeti", "minutes": 2, "payout": 0.30},
                ],
                "count": 2,
            })
        elif path == "/api/v1/accounts":
            self._json({"accounts": len(self.server.accounts), "unique_emails": len(set(self.server.accounts))})  # type: ignore[attr-defined]
        elif path == "/robots.txt":
            self._send("User-agent: *\nDisallow: /captcha\n", content_type="text/plain; charset=utf-8")
        else:
            self._page("Bulunamadı", '<div class="notice error">404 — sayfa bulunamadı.</div>', status=404)

    # ------------------------------------------------------------------ POST
    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)

        if path == "/register":
            data = self._read_form()
            email = (data.get("email") or [""])[0].strip().lower()
            password = (data.get("password") or [""])[0]
            confirm = (data.get("password_confirm") or [""])[0]
            first_name = (data.get("first_name") or [""])[0]

            if not email or "@" not in email:
                self._page("Hata", '<div class="notice error" data-testid="error">Geçerli bir e-posta adresi girin.</div>', status=422)
                return
            if "invalid" in email or first_name.lower() in {"hata", "invalid"}:
                self._page("Hata", '<div class="notice error" data-testid="error">Bu e-posta adresi reddedildi (sandbox negatif test kuralı).</div>', status=422)
                return
            if password and confirm and password != confirm:
                self._page("Hata", '<div class="notice error" data-testid="error">Parolalar eşleşmiyor.</div>', status=422)
                return

            token = f"tok-{random.randint(100000, 999999)}"
            self.server.accounts.append(email)  # type: ignore[attr-defined]
            mail_id = send_verification_mail(self.server, email, token)
            note = (
                f"<p class='muted'>Doğrulama e-postası gönderildi (message-id: {mail_id}).</p>"
                if mail_id
                else "<p class='muted'>Doğrulama e-postası gönderilemedi (SMTP kapalı).</p>"
            )
            self._page(
                "Kayıt başarılı",
                "<h1>Kayıt başarılı 🎉</h1>"
                '<div class="notice success" data-testid="success">Hesabınız oluşturuldu, anketlere katılabilirsiniz.</div>'
                f"{note}<p><a href='/surveys'>Anketlere git</a></p>",
            )
            return

        if path in {"/survey/1/submit", "/survey/2/submit"}:
            data = self._read_form()
            survey_id = 1 if path.startswith("/survey/1") else 2
            answers = {key: values[0] for key, values in sorted(data.items())}
            if survey_id == 2 and "rating" in answers and answers["rating"] == "1":
                # Deterministic "bad answer" branch so error_selector can be regression-tested.
                self._page(
                    "Anket reddedildi",
                    '<div class="notice error" data-testid="error">Bu yanıt kalite kontrolünden geçemedi.</div>',
                    status=422,
                )
                return
            uid = f"uid-{random.randint(10000, 99999)}"
            self.server.postbacks.append({"uid": uid, "survey": survey_id, "status": "completed"})  # type: ignore[attr-defined]
            self._page(
                "Anket tamamlandı",
                f'<h1>Anket tamamlandı ✅</h1><div class="notice success" data-testid="survey-complete">'
                f"Ödül hesabınıza eklendi (uid: {uid}).</div>"
                f"<pre>{json.dumps(answers, ensure_ascii=False, indent=2)}</pre>",
            )
            return

        if path == "/api/v1/postback":
            payload = self._read_form()
            record = {key: values[0] for key, values in payload.items()}
            record.setdefault("status", query.get("status", ["completed"])[0])
            self.server.postbacks.append(record)  # type: ignore[attr-defined]
            self._json({"accepted": True, "postback": record, "total": len(self.server.postbacks)}, status=202)  # type: ignore[attr-defined]
            return

        if path == "/api/v1/register":
            self._json({"status": "ok", "id": random.randint(10000, 99999)}, status=201)
            return

        self._json({"error": "not_found"}, status=404)


# --------------------------------------------------------------------------------------
# server bootstrap
# --------------------------------------------------------------------------------------
def build_server(
    port: int,
    *,
    quiet: bool = False,
    host: str = "0.0.0.0",
    smtp_host: Optional[str] = None,
    smtp_port: int = 1025,
    mail_from: str = "no-reply@offerwall.sandbox",
    public_url: Optional[str] = None,
) -> ThreadingHTTPServer:
    """Create the sandbox HTTP server (not yet started)."""
    server = ThreadingHTTPServer((host, port), SandboxHandler)
    server.daemon_threads = True
    server.quiet = quiet  # type: ignore[attr-defined]
    server.smtp_host = smtp_host  # type: ignore[attr-defined]
    server.smtp_port = smtp_port  # type: ignore[attr-defined]
    server.mail_from = mail_from  # type: ignore[attr-defined]
    server.public_url = public_url or f"http://127.0.0.1:{port}"  # type: ignore[attr-defined]
    server.accounts = []  # type: ignore[attr-defined]
    server.verifications = []  # type: ignore[attr-defined]
    server.postbacks = []  # type: ignore[attr-defined]
    return server


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Local offerwall/survey sandbox for WAFT testing")
    parser.add_argument("--port", type=int, default=8090, help="TCP port (default: 8090)")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--quiet", action="store_true", help="Do not log every request")
    parser.add_argument("--smtp-host", default=None, help="SMTP host for verification mails")
    parser.add_argument("--smtp-port", type=int, default=1025, help="SMTP port (Mailpit/devmail: 1025)")
    parser.add_argument("--mail-from", default="no-reply@offerwall.sandbox", help="From address")
    parser.add_argument("--public-url", default=None, help="Base URL used inside e-mail links")
    args = parser.parse_args(argv)

    try:
        server = build_server(
            args.port,
            quiet=args.quiet,
            host=args.host,
            smtp_host=args.smtp_host,
            smtp_port=args.smtp_port,
            mail_from=args.mail_from,
            public_url=args.public_url,
        )
    except OSError as exc:
        print(f"✖ could not bind {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 2

    print(f"Offerwall sandbox listening on http://{args.host}:{args.port}/  (Ctrl+C to stop)")
    if args.smtp_host:
        print(f"Verification mails -> smtp://{args.smtp_host}:{args.smtp_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down…")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
