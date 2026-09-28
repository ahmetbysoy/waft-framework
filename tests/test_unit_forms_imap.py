"""Unit tests for the form matcher and the IMAP mail parser (no browser required)."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waft.forms import (
    FormFieldSpec,
    FormFiller,
    canonical_kind,
    normalize_key,
    similarity,
)
from waft.imap_client import (
    ImapClient,
    ImapSettings,
    extract_links,
    extract_otp_codes,
    html_to_text,
    message_body,
)


# --------------------------------------------------------------------------------------
# key normalisation / canonical kinds
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("E-Posta", "e_posta"),
        ("firstName", "first_name"),
        ("user_name ", "user_name"),
        ("Ad Soyad", "ad_soyad"),
        ("password_confirm", "password_confirm"),
    ],
)
def test_normalize_key(raw, expected):
    assert normalize_key(raw) == expected


@pytest.mark.parametrize(
    "raw, kind",
    [
        ("email", "email"),
        ("e_posta", "email"),
        ("eposta_adresi", "email"),
        ("user_email", "email"),
        ("şifre", "password"),
        ("sifre_tekrar", "password"),
        ("password_confirm", "password"),
        ("ad", "first_name"),
        ("soyad", "last_name"),
        ("ad_soyad", "full_name"),
        ("cep_telefonu", "phone"),
        ("gsm", "phone"),
        ("firma", "company"),
        ("kvkk", "terms"),
        ("mesajiniz", "message"),
        ("dogrulama_kodu", "otp"),
        ("posta_kodu", "zip"),
    ],
)
def test_canonical_kind(raw, kind):
    assert canonical_kind(raw) == kind


def test_similarity():
    assert similarity("email", "email") == 1.0
    assert similarity("email", "e_mail") > 0.8
    assert similarity("email", "company") < 0.5


# --------------------------------------------------------------------------------------
# field matching (offline: FormFieldSpec objects instead of a live DOM)
# --------------------------------------------------------------------------------------


def make_field(index: int, **kwargs) -> FormFieldSpec:
    defaults = dict(
        index=index,
        tag="input",
        type="text",
        name="",
        element_id="",
        placeholder="",
        label="",
        aria_label="",
        autocomplete="",
        visible=True,
    )
    defaults.update(kwargs)
    return FormFieldSpec(**defaults)


def test_plan_matches_by_name_id_and_label(base_config):
    filler = FormFiller(base_config)
    fields = [
        make_field(0, name="first_name", element_id="fname", placeholder="Adınız", autocomplete="given-name"),
        make_field(1, name="last_name", element_id="lname", placeholder="Soyadınız"),
        make_field(2, name="email", element_id="email", type="email", autocomplete="email"),
        make_field(3, name="password", element_id="password", type="password"),
        make_field(4, name="password_confirm", element_id="password_confirm", type="password"),
        make_field(5, name="phone", element_id="phone", type="tel"),
        make_field(6, name="company", element_id="company"),
        make_field(7, name="city", tag="select", type="select", element_id="city"),
        make_field(8, name="terms", element_id="terms", type="checkbox"),
        make_field(9, name="message", tag="textarea", element_id="message"),
    ]
    data = {
        "email": "a@b.com",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "password": "S3cret!pass",
        "password_confirm": "S3cret!pass",
        "phone": "+90 555 111 22 33",
        "company": "Analytical Engines",
        "city": "istanbul",
        "terms": "true",
        "message": "Merhaba dünya",
    }
    plan = filler.plan(fields, data)
    matched = {match.key: match.field.name for match in plan.matches}
    assert matched["email"] == "email"
    assert matched["password"] == "password"
    assert matched["password_confirm"] == "password_confirm"  # must not steal the password field
    assert matched["first_name"] == "first_name"
    assert matched["last_name"] == "last_name"
    assert matched["city"] == "city"
    assert matched["terms"] == "terms"
    assert matched["message"] == "message"
    assert plan.unmatched == []


def test_plan_uses_turkish_placeholders(base_config):
    filler = FormFiller(base_config)
    fields = [
        make_field(0, placeholder="E-posta adresiniz", type="email"),
        make_field(1, placeholder="Şifreniz", type="password"),
        make_field(2, placeholder="Telefon numaranız", type="tel"),
    ]
    plan = filler.plan(fields, {"e_posta": "x@y.z", "sifre": "abcd1234", "telefon": "555"})
    assert len(plan.matches) == 3
    assert {match.key for match in plan.matches} == {"e_posta", "sifre", "telefon"}


def test_plan_respects_hidden_and_readonly_fields(base_config):
    filler = FormFiller(base_config)
    fields = [
        make_field(0, name="email", element_id="email_view", read_only=True),
        make_field(1, name="user_email_address", element_id="email_real"),
    ]
    plan = filler.plan(fields, {"email": "a@b.com"})
    assert plan.matches[0].field.element_id == "email_real"


def test_plan_skips_non_text_inputs_for_text_keys(base_config):
    filler = FormFiller(base_config)
    fields = [
        make_field(0, name="file_upload", type="file"),
        make_field(1, name="email", element_id="email", type="email"),
    ]
    plan = filler.plan(fields, {"email": "a@b.com"})
    assert plan.matches[0].field.type == "email"


def test_plan_explicit_selector_override_wins(base_config):
    filler = FormFiller(base_config)
    fields = [
        make_field(0, name="contact_email", element_id="legacy-email"),
        make_field(1, name="email", element_id="email"),
    ]
    plan = filler.plan(fields, {"email": "a@b.com"}, overrides={"email": "#email"})
    assert plan.matches[0].explicit is True
    assert plan.matches[0].field.element_id == "email"


def test_plan_reports_unmatched_keys(base_config):
    filler = FormFiller(base_config)
    fields = [make_field(0, name="email", element_id="email", type="email")]
    plan = filler.plan(fields, {"iban_number_xyz": "TR12"})
    assert plan.unmatched == ["iban_number_xyz"]


def test_prepare_text_value_coercions(base_config):
    filler = FormFiller(base_config)
    number_field = make_field(0, name="amount", type="number")
    assert filler._prepare_text_value(number_field, "1.234,56 TL") == "1234.56"
    date_field = make_field(1, name="birthdate", type="date")
    assert filler._prepare_text_value(date_field, "28.09.2026") == "2026-09-28"
    assert filler._prepare_text_value(date_field, "09/28/2026") == "2026-09-28"
    assert filler._prepare_text_value(date_field, "2026-09-28") == "2026-09-28"
    tel_field = make_field(2, name="phone", type="tel")
    assert filler._prepare_text_value(tel_field, "+90 (555) 111-22-33") == "+90 555 1112233"
    limited = make_field(3, name="code", maxlength=4)
    assert filler._prepare_text_value(limited, "1234567") == "1234"


def test_password_values_are_masked_in_logs(base_config):
    filler = FormFiller(base_config)
    assert "hunter2" not in filler._display_value("password", "hunter2")
    assert filler._display_value("company", "Acme") == "Acme"


# --------------------------------------------------------------------------------------
# IMAP parsing
# --------------------------------------------------------------------------------------


def build_message(
    subject: str,
    text: str,
    html: str = "",
    sender: str = "no-reply@demo.test",
    date: datetime | None = None,
) -> bytes:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = "tester@demo.test"
    message["Date"] = (date or datetime.now(timezone.utc)).strftime("%a, %d %b %Y %H:%M:%S %z")
    message["Message-ID"] = "<abc123@demo.test>"
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
    return message.as_bytes()


def test_extract_links_filters_noise():
    text = (
        "Hesabınızı doğrulamak için tıklayın: https://app.demo.test/verify?token=abc123 "
        "Abonelikten çıkmak için: https://demo.test/unsubscribe?u=1 "
        "Takip: https://twitter.com/demo"
    )
    links = extract_links(text)
    assert links
    assert links[0].startswith("https://app.demo.test/verify")


def test_extract_otp_codes_prefers_keyword_context():
    codes = extract_otp_codes("Doğrulama kodunuz: 482913. Bu kod 10 dakika geçerlidir. Ref: 20260928")
    assert codes[0] == "482913"


def test_html_to_text_and_message_body():
    html = "<html><body><h1>Merhaba</h1><script>var x=1;</script><p>Doğrula: <a href='https://x.test/confirm?t=1'>link</a></p></body></html>"
    text = html_to_text(html)
    assert "Merhaba" in text and "var x=1" not in text
    raw = build_message("Doğrulama", "düz metin", html, sender="no-reply@demo.test")
    import email as email_lib
    import email.policy

    parsed = email_lib.message_from_bytes(raw, policy=email_lib.policy.default)
    plain, html_body = message_body(parsed)
    assert "düz metin" in plain
    assert "confirm" in html_body


def test_imap_settings_from_config_and_validation(base_config):
    base_config.imap_host = "imap.demo.test"
    base_config.imap_username = "tester@demo.test"
    base_config.imap_password = "secret"
    settings = ImapSettings.from_config(base_config)
    assert settings.host == "imap.demo.test" and settings.port == 993
    settings.validate()

    broken = ImapSettings(host="", username="", password="")
    with pytest.raises(Exception):
        broken.validate()


def test_imap_message_parsing_and_scoring(base_config):
    base_config.imap_host = "imap.demo.test"
    base_config.imap_username = "tester@demo.test"
    base_config.imap_password = "secret"
    client = ImapClient(ImapSettings.from_config(base_config))

    raw = build_message(
        "Hesabınızı doğrulayın",  # fixture keeps the mail "fresh" so scoring stays positive
        "Merhaba, hesabınızı doğrulamak için: https://app.demo.test/activate?token=zzz999",
        html="<p><a href='https://app.demo.test/activate?token=zzz999'>Aktive et</a></p>",
    )
    message = client._parse_message("42", raw, flags=b")")
    assert message.uid == "42"
    assert "doğrula" in message.subject.lower()
    assert message.links and "activate" in message.links[0]
    assert message.date is not None

    score, reason = client.score_message(message, since=datetime.now(timezone.utc) - timedelta(minutes=5))
    assert score > 0 and reason

    extracted = client._extract(message, prefer_same_domain="https://app.demo.test")
    assert extracted.found and extracted.method == "link"
    assert "token=zzz999" in (extracted.link or "")


def test_imap_extract_otp_only_message(base_config):
    base_config.imap_host = "imap.demo.test"
    base_config.imap_username = "t@demo.test"
    base_config.imap_password = "s"
    client = ImapClient(ImapSettings.from_config(base_config))
    raw = build_message("Giriş kodu", "Giriş doğrulama kodunuz: 135790\nBu kodu 5 dakika içinde kullanın.")
    message = client._parse_message("77", raw)
    extracted = client._extract(message)
    assert extracted.found and extracted.method == "otp"
    assert extracted.otp == "135790"
    payload = extracted.to_dict()
    assert payload["otp"] == "***"  # never leak the code in reports
