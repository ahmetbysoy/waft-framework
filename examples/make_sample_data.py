#!/usr/bin/env python3
"""Generate the sample data files used by the README quick start.

Creates::

    examples/data/targets.xlsx     # 12 rows: URL + form data (the "Excel veri kaynağı")
    examples/data/targets.json     # 6 jobs: wizard steps, load test, API discovery, captcha …
    examples/data/selectors.json   # explicit selector overrides per field
    examples/data/steps.json       # a declarative workflow (JSON step list)
    examples/data/user_agents.txt  # custom user-agent pool
    examples/proxies.txt           # proxy list template (with comments + all supported formats)
    .env.example                   # environment template (IMAP, proxies, defaults)

Run it once::

    python examples/make_sample_data.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd  # only needed by this helper script

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
BASE = "http://127.0.0.1:8080"


def build_excel() -> Path:
    """Build ``targets.xlsx`` - the Excel data source consumed by ``--data``."""
    people = [
        ("Ayşe Yılmaz", "ayse.yilmaz@example.com", "+90 532 111 22 33", "Yılmaz Yazılım", "istanbul", "1990-04-12", "https://ayse.example.com", "Merhaba, ürünle ilgili bilgi almak istiyorum."),
        ("Mehmet Demir", "mehmet.demir@example.com", "+90 533 222 33 44", "Demir Ticaret", "ankara", "1985-11-02", "https://demir.example.com", "Kurumsal fiyat listesi rica ederim."),
        ("Zeynep Kaya", "zeynep.kaya@example.com", "+90 534 333 44 55", "Kaya Danışmanlık", "izmir", "1992-07-19", "https://kaya.example.com", "Demo talep ediyorum."),
        ("Ahmet Şahin", "ahmet.sahin@example.com", "+90 535 444 55 66", "Şahin Lojistik", "istanbul", "1979-01-30", "https://sahin.example.com", "Entegrasyon dokümanını paylaşır mısınız?"),
        ("Elif Aydın", "elif.aydin@example.com", "+90 536 555 66 77", "Aydın Medya", "ankara", "1995-09-08", "https://aydin.example.com", "Bültene abone olmak istiyorum."),
        ("Burak Özkan", "burak.ozkan@example.com", "+90 537 666 77 88", "Özkan Bilişim", "izmir", "1988-03-25", "https://ozkan.example.com", "Teknik destek talebi oluşturmak istiyorum."),
        ("Merve Çelik", "merve.celik@example.com", "+90 538 777 88 99", "Çelik Gıda", "istanbul", "1993-12-14", "https://celik.example.com", "Bayilik başvurusu hakkında bilgi alabilir miyim?"),
        ("Can Arslan", "can.arslan@example.com", "+90 539 888 99 00", "Arslan Otomotiv", "ankara", "1991-06-05", "https://arslan.example.com", "Randevu almak istiyorum."),
        ("Selin Doğan", "selin.dogan@example.com", "+90 530 999 00 11", "Doğan Hukuk", "istanbul", "1987-02-17", "https://dogan.example.com", "KVKK süreçleri için görüşmek istiyoruz."),
        ("Emre Koç", "emre.koc@example.com", "+90 531 000 11 22", "Koç Enerji", "berlin", "1984-08-21", "https://koc.example.com", "Uluslararası satış ekibiyle iletişime geçmek istiyorum."),
        ("Deniz Yıldız", "deniz.yildiz@example.com", "+90 532 123 45 67", "Yıldız Turizm", "london", "1996-05-29", "https://yildiz.example.com", "Kurumsal üyelik şartlarını öğrenmek istiyorum."),
        ("Kerem Aksoy", "kerem.aksoy@example.com", "+90 533 234 56 78", "Aksoy İnşaat", "istanbul", "1982-10-11", "https://aksoy.example.com", "Teklif almak istiyorum."),
    ]

    rows = []
    for index, (full_name, email, phone, company, city, birthdate, website, message) in enumerate(people, start=1):
        first, _, last = full_name.partition(" ")
        rows.append(
            {
                "name": f"signup-{index:02d}",
                "target_url": f"{BASE}/",
                "scenario": "form-submit",
                "email": email,
                "first_name": first,
                "last_name": last or "Test",
                "phone": phone,
                "company": company,
                "city": city,
                "birthdate": birthdate,
                "website": website,
                "message": message,
                "password": f"Wa!t{1000 + index}Str0ng",
                "password_confirm": f"Wa!t{1000 + index}Str0ng",
                "terms": "true",
                "newsletter": "true" if index % 2 == 0 else "false",
                "submit_button_text": "Kayıt Ol",
                "success_selector": '[data-testid="success"]',
                "error_selector": ".notice.error",
                "wait_after_submit_ms": 4000,
                "tags": "smoke,form",
            }
        )

    # Two extra rows exercising login + the negative (validation error) path.
    rows.append(
        {
            "name": "login-01",
            "target_url": f"{BASE}/login",
            "scenario": "form-submit",
            "email": "ayse.yilmaz@example.com",
            "password": "Wa!t1000Str0ng",
            "remember_me": "true",
            "submit_button_text": "Giriş",
            "success_selector": '[data-testid="success"]',
            "error_selector": ".notice.error",
            "wait_after_submit_ms": 4000,
            "tags": "login",
        }
    )
    rows.append(
        {
            "name": "negative-expect-rejection",
            "target_url": f"{BASE}/",
            "scenario": "form-submit",
            "email": "invalid-email@example.com",
            "first_name": "Test",
            "last_name": "Negatif",
            "password": "Wa!t9999Str0ng",
            "password_confirm": "Wa!t9999Str0ng",
            "terms": "true",
            "submit_button_text": "Kayıt Ol",
            "error_selector": ".notice.error",
            "wait_after_submit_ms": 4000,
            "expect_error": "true",
            "tags": "negative",
        }
    )

    frame = pd.DataFrame(rows)
    DATA.mkdir(parents=True, exist_ok=True)
    excel_path = DATA / "targets.xlsx"
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="targets", index=False)
        # A second sheet documents the column contract for whoever edits the file.
        documentation = pd.DataFrame(
            [
                {"column": "target_url", "required": "yes", "description": "Formun bulunduğu sayfa (http/https)"},
                {"column": "name", "required": "no", "description": "Test satırı adı (raporlarda görünür)"},
                {"column": "scenario", "required": "no", "description": "auto|form-submit|load-test|smoke|email-verify|api-discovery"},
                {"column": "email / first_name / last_name / phone / company / city / password …", "required": "no", "description": "Form alanları; başlık adı etiketle eşleşir"},
                {"column": "terms / newsletter", "required": "no", "description": "Checkbox; true/false/1/0/evet/hayir"},
                {"column": "submit_button_text", "required": "no", "description": "Gönder düğmesinin metni"},
                {"column": "success_selector / error_selector", "required": "no", "description": "Gönderim sonrası başarı/hata elemanları"},
                {"column": "success_url_regex", "required": "no", "description": "Başarı sayfası URL deseni (regex)"},
                {"column": "wait_after_submit_ms", "required": "no", "description": "Sonuç beklenirken eklenecek süre (ms)"},
                {"column": "requires_email_verification + verification_email", "required": "no", "description": "IMAP doğrulaması tetikleme"},
                {"column": "proxy", "required": "no", "description": "Bu satır için özel proxy (host:port veya socks5://…)"},
                {"column": "iterations / tags / active", "required": "no", "description": "Tekrar sayısı, etiketler, satırı devre dışı bırakma"},
                {"column": "expect_error (beklenen_hata)", "required": "no", "description": "true ise formun REDDEDİLMESİ beklenir (negatif test)"},
                {"column": "steps", "required": "no", "description": "JSON adım listesi: goto/fill/click/wait_for_selector/expect_*"},
            ]
        )
        documentation.to_excel(writer, sheet_name="columns", index=False)
    return excel_path


def build_json() -> Path:
    """Build ``targets.json`` - advanced jobs (wizard steps, load test, API discovery …)."""
    payload = {
        "rows": [
            {
                "name": "wizard-two-step",
                "target_url": f"{BASE}/wizard",
                "scenario": "form-submit",
                "email": "wizard.user@example.com",
                "password": "Wa!tW1zard",
                "phone": "+90 555 000 11 22",
                "submit": "false",
                "steps": [
                    {"action": "goto", "value": f"{BASE}/wizard"},
                    {"action": "fill", "target": "#wizard-email", "value": "{email}"},
                    {"action": "click", "target": "#wizard-continue"},
                    {"action": "wait_for_selector", "target": "#wizard-password"},
                    {"action": "fill", "target": "#wizard-password", "value": "{password}"},
                    {"action": "fill", "target": "#wizard-phone", "value": "{phone}"},
                    {"action": "click", "target": "button:has-text('Tamamla')"},
                    {"action": "wait_for_selector", "target": ".notice.success"},
                    {"action": "screenshot", "value": "wizard-done"},
                ],
            },
            {
                "name": "load-test-home",
                "target_url": f"{BASE}/",
                "scenario": "load-test",
                "submit": "false",
                "tags": "load",
            },
            {
                "name": "load-test-slow",
                "target_url": f"{BASE}/slow?ms=1200",
                "scenario": "load-test",
                "submit": "false",
                "tags": "load",
            },
            {
                "name": "api-discovery",
                "target_url": f"{BASE}/",
                "scenario": "api-discovery",
                "submit": "false",
                "tags": "api",
            },
            {
                "name": "smoke-dashboard",
                "target_url": f"{BASE}/dashboard",
                "scenario": "smoke",
                "submit": "false",
                "tags": "smoke",
            },
            {
                "name": "captcha-detection",
                "target_url": f"{BASE}/captcha",
                "scenario": "smoke",
                "submit": "false",
                "expect_error": True,
                "tags": "negative",
            },
        ]
    }
    path = DATA / "targets.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_selectors() -> Path:
    """Explicit selector overrides (data key → CSS selector)."""
    payload = {
        "selectors": {
            "email": "#email",
            "password": "#password",
            "password_confirm": "#password_confirm",
            "first_name": "#fname",
            "last_name": "#lname",
            "phone": "#phone",
            "company": "#company",
            "city": "#city",
            "birthdate": "#birthdate",
            "website": "#website",
            "message": "#message",
            "terms": "#terms",
            "newsletter": "#newsletter",
        },
        "field_aliases": {
            "email": ["eposta", "e_posta", "mail", "kullanici_email"],
            "phone": ["gsm", "cep", "telefon_no"],
            "message": ["mesaj", "not", "aciklama"],
        },
    }
    path = DATA / "selectors.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_steps() -> Path:
    """A standalone declarative workflow file (used with ``--steps``)."""
    payload = {
        "steps": [
            {"action": "goto", "value": f"{BASE}/"},
            {"action": "wait_for_selector", "target": "#signup-form"},
            {"action": "expect_visible", "target": "#email"},
            {"action": "scroll", "value": 400},
            {"action": "evaluate", "value": "() => document.querySelectorAll('input').length"},
            {"action": "screenshot", "value": "after-scan"},
        ]
    }
    path = DATA / "steps.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_user_agents() -> Path:
    agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    ]
    path = DATA / "user_agents.txt"
    path.write_text("\n".join(agents) + "\n", encoding="utf-8")
    return path


def build_proxies() -> Path:
    content = """# WAFT proxy listesi — her satır bir proxy, '#' ile başlayan satırlar yorumdur.
#
# Desteklenen formatlar:
#   host:port
#   host:port:username:password
#   username:password@host:port
#   http://username:password@host:8080
#   socks5://username:password@host:1080
#   socks5h://host:1080            (DNS çözümlemesi proxy tarafında)
#   1.2.3.4:8080 user pass         (boşluklu format)
#
# Gerçek bir çalıştırmada aşağıdaki satırları kendi proxy'lerinizle değiştirin.
# Örnek (çalışma testi için kullanılmaz — health-check başarısız olur):
# 127.0.0.1:8888
# user:pass@proxy1.example.com:8080
# socks5://user:pass@proxy2.example.com:1080
#
# Lokal demo çalıştırmasında proxy kullanmak istemiyorsanız:
#   python -m waft --data examples/data/targets.xlsx --proxy-mode off
"""
    path = ROOT.parent / "proxies.txt"
    path.write_text(content, encoding="utf-8")
    return path


def build_env_example() -> Path:
    content = """# ---------------------------------------------------------------------------
# WAFT örnek ortam dosyası — kopyalayın:  cp .env.example .env
# Her değişken WAFT_ ön ekiyle de okunur (ör. WAFT_CONTEXTS=10).
# CLI parametreleri her zaman .env değerlerini geçersiz kılar.
# ---------------------------------------------------------------------------

# ---- çalıştırma şekli
CONTEXTS=10
CONCURRENCY=5
ITERATIONS=1
HEADLESS=true
LOG_LEVEL=INFO

# ---- veri & proxy
WAFT_DATA=examples/data/targets.xlsx
WAFT_PROXY_FILE=proxies.txt
# WAFT_PROXIES=socks5://user:pass@host:1080;http://user:pass@host:8080   # virgül/noktalı virgüllü liste
PROXY_MODE=auto
PROXY_HEALTH_CHECK=true
PROXY_HEALTH_URL=https://api.ipify.org?format=json

# ---- kimlik / parmak izi
TIMEZONES_SPEC=Europe/Istanbul
LOCALES_SPEC=tr-TR,en-US
DEVICES_SPEC=random
GEOLOCATION_SPEC=41.0082,28.9784

# ---- stealth
STEALTH=true
CANVAS_NOISE=true
WEBGL_SPOOF=true
AUDIO_NOISE=true
WEBRTC_BLOCK=true
VERIFY_STEALTH=true

# ---- ağ trafiği
LOG_NETWORK=true
LOG_NETWORK_HEADERS=false
LOG_RESPONSE_BODY=false

# ---- IMAP (hesap doğrulama)
# Örn. Gmail için uygulama şifresi gerekir.
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_SSL=true
IMAP_USER=test-hesabi@gmail.com
IMAP_PASSWORD=uygulama-sifresi
IMAP_MAILBOX=INBOX
IMAP_TIMEOUT_S=120
IMAP_POLL_INTERVAL_S=5
IMAP_SUBJECT_REGEX=(doğrula|verify|aktivasyon|confirm)
IMAP_SENDER_FILTER=no-reply@
IMAP_LINK_REGEX=https?://[^\\s"']*(verify|confirm|activate|token)[^\\s"']*
IMAP_MARK_SEEN=false
IMAP_MARK_PROCESSED=false

# ---- artefaktlar
ARTIFACTS_DIR=artifacts
TRACE_MODE=on-failure
SCREENSHOTS=true
CAPTURE_HAR=false
"""
    path = ROOT.parent / ".env.example"
    path.write_text(content, encoding="utf-8")
    return path


def main() -> int:
    excel = build_excel()
    js = build_json()
    selectors = build_selectors()
    steps = build_steps()
    agents = build_user_agents()
    proxies = build_proxies()
    env = build_env_example()
    print("Sample data written:")
    for path in (excel, js, selectors, steps, agents, proxies, env):
        print(f"  • {path.relative_to(ROOT.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
