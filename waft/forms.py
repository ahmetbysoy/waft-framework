"""Form discovery + intelligent field matching + filling + submission.

The filler never relies on hard-coded selectors alone. For every data key coming from the
Excel/JSON source (``email``, ``password``, ``first_name``, ``tel`` …) it:

1.  scans the DOM for every ``input``/``textarea``/``select``/``[contenteditable]`` element and
    builds a fingerprint (name, id, placeholder, label, aria-label, autocomplete, type …),
2.  scores every field against the key using alias tables (English **and** Turkish),
    attribute equality, token overlap, substrings and fuzzy similarity,
3.  fills the best candidate when the score clears the threshold - explicit selectors from
    ``--selectors`` / the spreadsheet always win,
4.  iterates (forms reveal fields after the first interaction: sign-up wizards, multi-step
    checkouts, "continue" buttons),
5.  submits and waits for a success/error outcome.

Everything is reported through :class:`~waft.models.FillReport` so the run summary shows
exactly what was typed where (passwords are masked).
"""

from __future__ import annotations

import asyncio
import difflib
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from .config import Config
from .errors import (
    CaptchaDetectedError,
    FieldFillError,
    FieldResolutionError,
    SubmitError,
)
from .logging_setup import get_logger
from .models import FillReport, StepResult, TargetRow
from .utils import Redactor, Stopwatch, human_ms, normalize_ws, parse_bool, truncate

__all__ = [
    "FormFiller",
    "FormFieldSpec",
    "FieldMatch",
    "FieldPlan",
    "DEFAULT_FIELD_ALIASES",
    "CANONICAL_KINDS",
    "SELECTOR_TEMPLATES",
    "SUBMIT_TEXTS",
    "CAPTCHA_SELECTORS",
    "normalize_key",
    "canonical_kind",
    "similarity",
]

logger = get_logger("waft.forms")

#: Canonical field kinds → aliases (English + Turkish). Aliases are matched against
#: ``name|id|placeholder|label|aria-label`` after normalisation.
DEFAULT_FIELD_ALIASES: dict[str, list[str]] = {
    "email": [
        "email", "e-mail", "mail", "email_address", "emailaddress", "user_email", "eposta", "e_posta",
        "email_adresi", "poste", "correo", "courriel", "登録メール", "kullanici_email",
    ],
    "username": [
        "username", "user_name", "user", "userid", "user_id", "login", "login_name", "account",
        "account_name", "nickname", "nick", "kullanici_adi", "kullanici", "kullanıcı_adı", "giris_adi",
        "üye_adı", "uye_adi", "hesap", "kadi",
    ],
    "password": [
        "password", "passwd", "pass", "pwd", "passwd1", "user_password", "account_password",
        "sifre", "şifre", "parola", "sifreniz", "şifreniz", "giris_sifresi", "yeni_sifre", "sifre_tekrar",
        "password_confirm", "confirm_password", "sifre_tekrar", "repeat_password", "passwort", "mot_de_passe",
    ],
    "first_name": [
        "first_name", "firstname", "first", "given_name", "givenname", "fname", "name_first",
        "ad", "adi", "adınız", "isim", "isminiz", "ad1",
    ],
    "last_name": [
        "last_name", "lastname", "last", "family_name", "familyname", "lname", "surname", "name_last",
        "soyad", "soyadi", "soyadınız", "soyisim",
    ],
    "full_name": [
        "full_name", "fullname", "name", "your_name", "display_name", "displayname", "contact_name",
        "ad_soyad", "adsoyad", "ad_soyad", "isim_soyisim", "ad_ve_soyad", "isim_soyad", "adi_soyadi",
        "musteri_adi", "customer_name", "yourname", "full_name_of_user",
    ],
    "phone": [
        "phone", "telephone", "tel", "phone_number", "mobile", "mobile_number", "cell", "cellphone",
        "gsm", "gsm_no", "telefon", "telefon_no", "cep", "cep_telefonu", "iletisim_no", "telefon_numarasi",
        "whatsapp", "msisdn",
    ],
    "company": [
        "company", "company_name", "organization", "organisation", "org", "firm", "firma", "sirket",
        "şirket", "kurum", "kurulus", "isletme", "business", "employer",
    ],
    "job_title": ["job_title", "title", "position", "role", "pozisyon", "unvan", "gorev", "meslek", "occupation"],
    "address": [
        "address", "address1", "address_line_1", "street", "street_address", "adres", "acik_adres",
        "sokak", "cadde", "mahalle", "address_line", "addr",
    ],
    "address2": ["address2", "address_line_2", "adres2", "daire", "apartman", "suite"],
    "city": ["city", "town", "sehir", "şehir", "il", "ilce", "ilçe", "kasaba"],
    "state": ["state", "province", "region", "county", "eyalet", "bolge", "bölge"],
    "country": ["country", "country_code", "ulke", "ülke", "ulkesi", "nationality"],
    "zip": ["zip", "zipcode", "zip_code", "postal_code", "postcode", "post_code", "posta_kodu", "pk"],
    "birthdate": [
        "birthdate", "birth_date", "birthday", "dob", "date_of_birth", "bday", "dogum_tarihi",
        "doğum_tarihi", "dogum", "dt",
    ],
    "gender": ["gender", "sex", "cinsiyet"],
    "website": ["website", "url", "homepage", "site", "web_sitesi", "domain"],
    "subject": ["subject", "konu", "baslik", "başlık", "title_of_message"],
    "message": [
        "message", "comment", "comments", "notes", "note", "description", "details", "textarea", "body",
        "mesaj", "mesajiniz", "aciklama", "açıklama", "not", "detay", "sorunuz", "question", "inquiry",
        "feedback", "cover_letter", "görüş", "gorus",
    ],
    "otp": [
        "otp", "code", "verification_code", "sms_code", "totp", "mfa", "pin", "token", "security_code",
        "dogrulama_kodu", "doğrulama_kodu", "kod", "guvenlik_kodu",
    ],
    "captcha": ["captcha", "g-recaptcha-response", "h-captcha-response", "cf-turnstile-response", "recaptcha"],
    "terms": [
        "terms", "terms_of_service", "tos", "agree", "agreement", "accept", "privacy", "policy", "consent",
        "kvkk", "kullanim_kosullari", "kullanım_koşulları", "sozlesme", "sözleşme", "onay", "gizlilik",
        "aydinlatma", "aydınlatma", "riza", "rıza", "checkbox_terms",
    ],
    "newsletter": ["newsletter", "subscribe", "mailing", "opt_in", "optin", "bulten", "bülten", "kampanya", "abone"],
    "remember": ["remember", "remember_me", "keep_logged_in", "beni_hatirla", "beni_hatırla"],
    "card_number": ["card", "card_number", "cc_number", "credit_card", "kart", "kart_no", "kart_numarasi"],
    "card_expiry": ["expiry", "exp", "expiration", "cc_exp", "son_kullanma", "skt"],
    "card_cvc": ["cvc", "cvv", "cvv2", "security_code_card", "cvc2"],
    "search": ["search", "query", "q", "arama", "ara"],
}

#: Reverse lookup: alias → canonical kind (built once).
CANONICAL_FROM_ALIAS: dict[str, str] = {}
for _kind, _aliases in DEFAULT_FIELD_ALIASES.items():
    for _alias in _aliases:
        CANONICAL_FROM_ALIAS[_alias] = _kind
CANONICAL_KINDS = tuple(DEFAULT_FIELD_ALIASES.keys())

#: Ready-made selectors per canonical kind (used when nothing else matches).
SELECTOR_TEMPLATES: dict[str, list[str]] = {
    "email": [
        'input[type="email"]',
        'input[name*="mail" i]',
        'input[id*="mail" i]',
        'input[autocomplete="email"]',
        'input[placeholder*="mail" i]',
    ],
    "username": [
        'input[name*="user" i]:not([type="password"])',
        'input[id*="user" i]:not([type="password"])',
        'input[autocomplete="username"]',
    ],
    "password": [
        'input[type="password"]',
        'input[autocomplete="new-password"]',
        'input[name*="pass" i]',
        'input[id*="sifre" i]',
    ],
    "phone": ['input[type="tel"]', 'input[name*="phone" i]', 'input[name*="tel" i]', 'input[name*="cep" i]'],
    "full_name": [
        'input[name*="name" i]:not([name*="user" i]):not([name*="nick" i])',
        'input[id*="adsoyad" i]',
        'input[placeholder*="ad" i]',
    ],
    "message": ["textarea", 'textarea[name*="mesaj" i]', "[contenteditable='true']"],
    "terms": ['input[type="checkbox"][name*="terms" i]', 'input[type="checkbox"][name*="kvkk" i]', 'input[type="checkbox"][required]'],
    "newsletter": ['input[type="checkbox"][name*="news" i]', 'input[type="checkbox"][name*="bulten" i]'],
    "submit": [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Gönder")',
        'button:has-text("Kaydet")',
        'button:has-text("Giriş")',
        'button:has-text("Submit")',
    ],
}

#: Button label hints used to find the submit control when no selector matches.
SUBMIT_TEXTS: tuple[str, ...] = (
    "submit", "send", "register", "sign up", "signup", "sign in", "signin", "log in", "login", "continue",
    "save", "confirm", "apply", "create account", "get started", "next", "join", "subscribe", "request",
    "gönder", "gonder", "kaydet", "kayıt ol", "kayit ol", "kaydol", "giriş", "giris", "giriş yap", "devam",
    "devam et", "onayla", "tamamla", "abone ol", "üye ol", "uye ol", "başvur", "basvur", "talep et", "ara",
)

#: CAPTCHA / bot-challenge markers. WAFT never solves CAPTCHAs - it detects and reports them.
CAPTCHA_SELECTORS: tuple[str, ...] = (
    'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    'iframe[src*="turnstile"]',
    'iframe[title*="recaptcha" i]',
    ".g-recaptcha",
    ".h-captcha",
    ".cf-turnstile",
    "[data-sitekey]",
    'textarea[name="g-recaptcha-response"]',
    "#cf-challenge-running",
    "#challenge-form",
    "#challenge-stage",
    ".cf-browser-verification",
    "#px-captcha",
    '[class*="captcha" i]',
    '[id*="captcha" i]',
)

CAPTCHA_ACTION_REQUIRED = ("error", "skip", "continue")


def normalize_key(value: Any) -> str:
    """Normalise a data key / attribute into ``snake_case`` ASCII."""
    text = str(value or "")
    text = (
        text.replace("ı", "i")
        .replace("İ", "i")
        .replace("ş", "s")
        .replace("Ş", "s")
        .replace("ğ", "g")
        .replace("Ğ", "g")
        .replace("ü", "u")
        .replace("Ü", "u")
        .replace("ö", "o")
        .replace("Ö", "o")
        .replace("ç", "c")
        .replace("Ç", "c")
    )
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)  # camelCase → camel_Case
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).lower()
    text = re.sub(r"_+", "_", text).strip("_")
    # Drop common noise words that appear in form labels.
    for noise in ("_required", "_field", "_input", "_entry", "_form", "_value", "_text", "_box", "_1"):
        if text.endswith(noise) and len(text) > len(noise) + 2:
            text = text[: -len(noise)]
    return text


def canonical_kind(key: str) -> Optional[str]:
    """Map a key/attribute to a canonical field kind (``e_posta`` → ``email``)."""
    normalised = normalize_key(key)
    if normalised in CANONICAL_FROM_ALIAS:
        return CANONICAL_FROM_ALIAS[normalised]
    if normalised in SELECTOR_TEMPLATES:
        return normalised
    # Substring pass: "user_email_address" → email
    for alias, kind in CANONICAL_FROM_ALIAS.items():
        if len(alias) >= 3 and (alias in normalised or normalised in alias):
            return kind
    return None


def similarity(left: str, right: str) -> float:
    """Fuzzy similarity of two normalised tokens in ``[0, 1]``."""
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return difflib.SequenceMatcher(None, left, right).ratio()


# --------------------------------------------------------------------------------------
# Field model
# --------------------------------------------------------------------------------------


@dataclass
class FormFieldSpec:
    """A single fillable element discovered in the DOM."""

    index: int
    tag: str
    type: str = "text"
    name: str = ""
    element_id: str = ""
    placeholder: str = ""
    label: str = ""
    aria_label: str = ""
    autocomplete: str = ""
    title: str = ""
    classes: str = ""
    required: bool = False
    disabled: bool = False
    read_only: bool = False
    visible: bool = True
    checked: bool = False
    value: str = ""
    maxlength: Optional[int] = None
    pattern: str = ""
    options: list[dict[str, str]] = field(default_factory=list)
    in_password_manager: bool = False
    form_action: Optional[str] = None
    form_method: Optional[str] = None
    form_id: Optional[str] = None

    @property
    def selector(self) -> str:
        """Stable selector manufactured during the scan."""
        return f'[data-waft-idx="{self.index}"]'

    @property
    def kind(self) -> str:
        """Best-effort canonical kind for this element."""
        if self.tag == "select":
            return "select"
        if self.type in {"checkbox", "radio"}:
            return self.type
        for candidate in (self.name, self.element_id, self.placeholder, self.label, self.aria_label, self.autocomplete):
            if not candidate:
                continue
            kind = canonical_kind(candidate)
            if kind:
                return kind
        if self.type in {"email", "tel", "password", "number", "date", "file", "url", "search"}:
            return self.type
        return "text"

    @property
    def haystacks(self) -> dict[str, str]:
        """Normalised attribute bag used by the scorer."""
        return {
            "name": normalize_key(self.name),
            "id": normalize_key(self.element_id),
            "placeholder": normalize_key(self.placeholder),
            "label": normalize_key(self.label),
            "aria": normalize_key(self.aria_label),
            "title": normalize_key(self.title),
            "autocomplete": normalize_key(self.autocomplete),
        }

    def describe(self) -> str:
        bits = [f"<{self.tag}", f"type={self.type}"]
        for attribute in ("name", "id", "placeholder", "label"):
            value = getattr(self, attribute if attribute != "id" else "element_id")
            if value:
                bits.append(f"{attribute}={truncate(str(value), 40)}")
        if self.required:
            bits.append("required")
        return " ".join(bits) + ">"

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "tag": self.tag,
            "type": self.type,
            "name": self.name,
            "id": self.element_id,
            "label": self.label,
            "placeholder": self.placeholder,
            "aria_label": self.aria_label,
            "autocomplete": self.autocomplete,
            "required": self.required,
            "visible": self.visible,
            "disabled": self.disabled,
            "kind": self.kind,
            "options": [option.get("value") for option in self.options][:20],
        }


@dataclass
class FieldMatch:
    """The decision to fill ``field`` with ``value`` for the data ``key``."""

    key: str
    value: str
    field: FormFieldSpec
    score: float
    reason: str
    selector: str = ""
    explicit: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": truncate(self.value, 120),
            "field": self.field.describe(),
            "score": round(self.score, 1),
            "reason": self.reason,
            "explicit": self.explicit,
        }


@dataclass
class FieldPlan:
    """All matches for one pass over the page, plus untouched data keys."""

    matches: list[FieldMatch] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    fields_seen: int = 0
    skipped: dict[str, str] = field(default_factory=dict)

    def keys(self) -> set[str]:
        return {match.key for match in self.matches}


# --------------------------------------------------------------------------------------
# DOM scanning JS
# --------------------------------------------------------------------------------------

_SCAN_JS = r"""
() => {
  const results = [];
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    if (!style || style.visibility === "hidden" || style.display === "none" || style.opacity === "0") return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const labelFor = (el) => {
    try {
      if (el.labels && el.labels.length) {
        return Array.from(el.labels).map((l) => (l.innerText || l.textContent || "").trim()).join(" ").trim();
      }
      const closest = el.closest("label");
      if (closest) return (closest.innerText || closest.textContent || "").trim();
      const wrapper = el.closest(".form-group, .field, .input-group, .form-field, .control, li, td, div");
      if (wrapper) {
        const label = wrapper.querySelector("label, .label, .form-label, legend, span.label");
        if (label) return (label.innerText || label.textContent || "").trim();
      }
      const prev = el.previousElementSibling;
      if (prev && /^(label|span|div|p|caption)$/i.test(prev.tagName)) {
        return (prev.innerText || prev.textContent || "").trim();
      }
      const placeholder = el.getAttribute("placeholder");
      if (placeholder) return "";
      const table = el.closest("tr");
      if (table) {
        const cell = table.querySelector("th, td");
        if (cell) return (cell.innerText || cell.textContent || "").trim();
      }
    } catch (e) {}
    return "";
  };
  const ariaLabelledBy = (el) => {
    const ids = el.getAttribute("aria-labelledby");
    if (!ids) return "";
    return ids.split(/\s+/).map((id) => {
      const node = document.getElementById(id);
      return node ? (node.innerText || node.textContent || "").trim() : "";
    }).filter(Boolean).join(" ");
  };

  const nodes = Array.from(document.querySelectorAll('input, textarea, select, [contenteditable="true"], [contenteditable=""]'));
  let index = 0;
  for (const el of nodes) {
    const type = (el.getAttribute("type") || (el.tagName === "SELECT" ? "select" : el.tagName.toLowerCase())).toLowerCase();
    if (["hidden", "submit", "button", "reset", "image"].includes(type)) continue;
    if (el.disabled && !el.getAttribute("readonly")) { /* still record disabled for reporting */ }
    const form = el.form || el.closest("form");
    const options = el.tagName === "SELECT"
      ? Array.from(el.options || []).slice(0, 60).map((o) => ({ value: o.value, label: (o.textContent || "").trim() }))
      : [];
    el.setAttribute("data-waft-idx", String(index));
    results.push({
      index: index,
      tag: el.tagName.toLowerCase(),
      type: type,
      name: el.getAttribute("name") || "",
      element_id: el.id || "",
      placeholder: el.getAttribute("placeholder") || "",
      label: (labelFor(el) + " " + ariaLabelledBy(el)).trim(),
      aria_label: el.getAttribute("aria-label") || "",
      autocomplete: el.getAttribute("autocomplete") || "",
      title: el.getAttribute("title") || "",
      classes: el.className && el.className.toString ? el.className.toString().slice(0, 200) : "",
      required: el.required === true || el.getAttribute("aria-required") === "true",
      disabled: el.disabled === true,
      read_only: el.readOnly === true,
      visible: isVisible(el),
      checked: el.checked === true,
      value: (el.tagName === "SELECT" ? el.value : (el.value || "")).toString().slice(0, 200),
      maxlength: el.maxLength && el.maxLength > 0 ? el.maxLength : null,
      pattern: el.getAttribute("pattern") || "",
      options: options,
      form_action: form ? (form.getAttribute("action") || "") : null,
      form_method: form ? (form.getAttribute("method") || "get") : null,
      form_id: form ? (form.id || "") : null,
    });
    index += 1;
  }
  return {
    fields: results,
    counts: {
      forms: document.forms ? document.forms.length : 0,
      inputs: nodes.length,
      passwords: document.querySelectorAll('input[type="password"]').length,
      email: document.querySelectorAll('input[type="email"], input[name*="mail" i]').length,
    },
    title: document.title,
  };
}
"""

_CAPTCHA_JS = r"""
(selectors) => {
  const found = [];
  for (const selector of selectors) {
    try {
      const nodes = document.querySelectorAll(selector);
      if (nodes.length) {
        const first = nodes[0];
        found.push({
          selector: selector,
          count: nodes.length,
          visible: !!(first.offsetParent !== null || (first.getBoundingClientRect().width > 0)),
          src: first.getAttribute ? (first.getAttribute("src") || "") : "",
        });
      }
    } catch (e) {}
  }
  return found;
}
"""


class FormFiller:
    """Scans, matches, fills and submits forms."""

    def __init__(self, config: Config, redactor: Optional[Redactor] = None) -> None:
        self.config = config
        self.redactor = redactor or Redactor(enabled=config.redact)
        self.threshold = float(getattr(config, "field_match_threshold", 45.0) or 45.0)
        self.last_scan: dict[str, Any] = {}
        self.last_fields: list[FormFieldSpec] = []

    # ------------------------------------------------------------------ scanning
    async def scan(self, page: Any) -> list[FormFieldSpec]:
        """Scan the page and return every fillable field."""
        payload = await page.evaluate(_SCAN_JS)
        self.last_scan = payload or {}
        fields = [FormFieldSpec(**item) for item in (payload or {}).get("fields", [])]
        self.last_fields = fields
        logger.debug(
            "Scan found %d field(s) (%d forms, %d password, %d e-mail)",
            len(fields),
            (payload or {}).get("counts", {}).get("forms", 0),
            (payload or {}).get("counts", {}).get("passwords", 0),
            (payload or {}).get("counts", {}).get("email", 0),
        )
        return fields

    # ------------------------------------------------------------------ matching
    def plan(
        self,
        fields: Sequence[FormFieldSpec],
        data: dict[str, Any],
        *,
        overrides: Optional[dict[str, str]] = None,
        already_used: Optional[set[str]] = None,
    ) -> FieldPlan:
        """Match data keys to DOM fields.

        ``overrides`` maps data keys (or raw selectors) to explicit selectors; those entries
        always win and are reported as ``explicit``.
        """
        overrides = dict(overrides or {})
        used_keys = set(already_used or set())
        plan = FieldPlan(fields_seen=len(fields))
        taken_fields: set[int] = set()

        # 0) Turn selector-style override keys (e.g. "#email") into data-key entries when the
        #    key itself is a selector - this supports `--selectors {"#newsletter": "true"}`.
        selector_map: dict[str, str] = {}
        for key, selector in list(overrides.items()):
            if key.startswith(("#", ".", "[", "//")) and key not in data:
                selector_map[selector] = key
            else:
                selector_map.setdefault(key, selector)

        # 1) explicit overrides first
        for key, selector in selector_map.items():
            if key in used_keys:
                continue
            value = data.get(key)
            if value in (None, ""):
                continue
            field = self._find_by_selector(fields, selector)
            if field is None:
                plan.skipped[key] = f"explicit selector '{selector}' did not match any input"
                logger.debug("Override selector %s for key '%s' matched nothing", selector, key)
                continue
            plan.matches.append(
                FieldMatch(
                    key=key,
                    value=str(value),
                    field=field,
                    score=1000.0,
                    reason=f"explicit selector override ({selector})",
                    selector=field.selector,
                    explicit=True,
                )
            )
            taken_fields.add(field.index)
            # The key is now satisfied: without this the heuristic pass below would re-match it
            # against the next spare field (e.g. "password" leaking into "password_confirm")
            # and would then wrongly report the key as unmatched.
            used_keys.add(key)

        # 2) heuristic scoring for everything else. Disabled/read-only inputs can never be
        #    filled, so they are removed from the candidate set entirely.
        candidate_fields = [f for f in fields if f.index not in taken_fields and not f.disabled and not f.read_only]
        for key, value in data.items():
            if key in used_keys:
                continue
            if value in (None, "") or (isinstance(value, str) and not value.strip()):
                continue
            best: Optional[FieldMatch] = None
            for candidate in candidate_fields:
                if candidate.index in taken_fields:
                    continue
                score, reason = self._score(candidate, key)
                if score <= 0:
                    continue
                if best is None or score > best.score:
                    best = FieldMatch(key=key, value=str(value), field=candidate, score=score, reason=reason, selector=candidate.selector)
            if best is None or best.score < self.threshold:
                plan.unmatched.append(key)
                continue
            plan.matches.append(best)
            taken_fields.add(best.field.index)
            used_keys.add(key)

        # Report what the caller asked but we could not place.
        plan.unmatched = [k for k in plan.unmatched if k not in plan.skipped]
        return plan

    def _find_by_selector(self, fields: Sequence[FormFieldSpec], selector: str) -> Optional[FormFieldSpec]:
        """Resolve a raw CSS selector against the scanned fields (best effort)."""
        selector = (selector or "").strip()
        if not selector:
            return None
        # 1) exact attribute match on the scanned fingerprint
        patterns = [
            (r"#([\w\-:.]+)", "element_id"),
            (r"\[name=[\"']?([^\"'\]]+)[\"']?\]", "name"),
            (r"\[id=[\"']?([^\"'\]]+)[\"']?\]", "element_id"),
            (r"\[placeholder=[\"']?([^\"'\]]+)[\"']?\]", "placeholder"),
            (r"\[type=[\"']?([^\"'\]]+)[\"']?\]", "type"),
        ]
        requirements: list[tuple[str, str]] = []
        for pattern, attribute in patterns:
            match = re.search(pattern, selector)
            if match:
                requirements.append((attribute, match.group(1)))
        if requirements:
            for field in fields:
                if all(str(getattr(field, attribute, "")).lower() == value.lower() for attribute, value in requirements):
                    return field
        # 2) plain text selector: "text=Forgot" style still usable for clicks, not for filling
        return None

    def _score(self, field: FormFieldSpec, key: str) -> tuple[float, str]:
        """Score how well *field* matches data *key* (``0`` = no match)."""
        key_norm = normalize_key(key)
        key_kind = canonical_kind(key) or key_norm
        field_kind = field.kind
        haystacks = field.haystacks
        score = 0.0
        reasons: list[str] = []

        # --- attribute equality (strongest signal)
        for attribute, value in haystacks.items():
            if not value:
                continue
            if value == key_norm:
                bonus = 95.0 if attribute in {"name", "id"} else 75.0
                score += bonus
                reasons.append(f"{attribute}=={key_norm}")
                break

        # --- canonical kind agreement
        if field_kind == key_kind:
            score += 55.0
            reasons.append(f"kind:{key_kind}")
        elif field_kind == "text" and key_kind in {"text", "full_name", "address"}:
            score += 12.0

        # --- substring / token overlap
        for attribute, value in haystacks.items():
            if not value or value == key_norm:
                continue
            if len(key_norm) >= 3 and key_norm in value:
                score += 30.0
                reasons.append(f"{attribute}~{key_norm}")
                break
            key_tokens = {token for token in key_norm.split("_") if len(token) > 2}
            value_tokens = {token for token in value.split("_") if len(token) > 2}
            shared = key_tokens & value_tokens
            if shared:
                score += 22.0 * min(1.0, len(shared) / max(1, len(key_tokens)))
                reasons.append(f"tokens:{','.join(sorted(shared))}")
                break

        # --- fuzzy similarity against the best attribute
        best_similarity = max((similarity(key_norm, value) for value in haystacks.values() if value), default=0.0)
        if best_similarity >= 0.72:
            score += 40.0 * best_similarity
            reasons.append(f"fuzzy:{best_similarity:.2f}")

        # --- element type compatibility
        type_bonus = {
            "email": {"email": 30.0},
            "password": {"password": 40.0},
            "phone": {"tel": 25.0, "number": 8.0},
            "birthdate": {"date": 30.0},
            "website": {"url": 25.0},
            "search": {"search": 30.0},
        }
        score += type_bonus.get(key_kind, {}).get(field.type, 0.0)
        if key_kind in {"full_name", "first_name", "last_name", "company", "city", "country", "job_title"} and field.type in {"email", "password", "tel", "number", "date", "file"}:
            score -= 60.0
            reasons.append("type-mismatch")

        # --- autocomplete attribute is a very strong hint
        autocomplete_bonus = {
            "email": {"email": 30.0},
            "username": {"username": 30.0},
            "password": {"current-password": 30.0, "new-password": 30.0},
            "first_name": {"given-name": 30.0, "additional-name": 10.0},
            "last_name": {"family-name": 30.0},
            "full_name": {"name": 30.0},
            "phone": {"tel": 30.0, "tel-national": 25.0, "tel-country-code": 15.0},
            "address": {"street-address": 30.0, "address-line1": 25.0},
            "city": {"address-level2": 25.0},
            "state": {"address-level1": 25.0},
            "country": {"country": 25.0, "country-name": 25.0},
            "zip": {"postal-code": 30.0},
            "birthdate": {"bday": 30.0},
            "company": {"organization": 30.0},
        }
        for token in haystacks.get("autocomplete", "").split("_"):
            if token and token in autocomplete_bonus.get(key_kind, {}):
                score += autocomplete_bonus[key_kind][token]
                reasons.append(f"autocomplete:{token}")
                break

        # --- usability penalties
        if not field.visible:
            score -= 55.0
            reasons.append("invisible")
        if field.disabled:
            score -= 80.0
            reasons.append("disabled")
        if field.read_only:
            score -= 90.0
            reasons.append("readonly")
        if field.type == "file" and key_kind != "file":
            score -= 120.0
            reasons.append("file-input")
        if field_kind in {"checkbox", "radio"} and key_kind not in {"terms", "newsletter", "remember", "gender", "captcha"}:
            score -= 40.0
            reasons.append("non-text-input")
        if field.type == "password" and key_kind == "password":
            score += 10.0
        if field.type == "password" and key_kind != "password" and key_kind != "otp":
            score -= 70.0
            reasons.append("password-mismatch")
        if field.type in {"submit", "button", "hidden", "image", "reset"}:
            score -= 150.0

        return score, "; ".join(reasons) or "no-signal"

    # ------------------------------------------------------------------ filling
    async def fill(
        self,
        page: Any,
        plan: FieldPlan,
        *,
        row: Optional[TargetRow] = None,
        report: Optional[FillReport] = None,
        strict: bool = False,
    ) -> FillReport:
        """Execute a :class:`FieldPlan` against the live page."""
        report = report or FillReport()
        stopwatch = Stopwatch()
        stopwatch.__enter__()
        report.fields_seen = plan.fields_seen
        report.skipped.update(plan.skipped)

        for match in plan.matches:
            try:
                filled_as = await self._fill_one(page, match)
                if filled_as is None:
                    report.skipped[match.key] = f"no action for kind '{match.field.kind}'"
                    continue
                bucket, summary = filled_as
                getattr(report, bucket)[match.key] = summary
                logger.info(
                    "  ✎ %-14s → %-28s (%s, score %.0f)",
                    match.key,
                    self._display_value(match.key, match.value),
                    match.field.describe()[:46],
                    match.score,
                )
            except Exception as exc:  # noqa: BLE001 - collected per field
                message = f"{type(exc).__name__}: {exc}"
                report.skipped[match.key] = message
                logger.warning("  ✗ could not fill '%s' (%s): %s", match.key, match.field.describe()[:60], truncate(message, 160))
                if strict:
                    raise FieldFillError(f"Could not fill '{match.key}': {message}") from exc

        report.unmatched = list(plan.unmatched)
        if report.unmatched:
            logger.warning(
                "  ! %d data key(s) could not be matched to a form field: %s",
                len(report.unmatched),
                ", ".join(report.unmatched),
            )
        stopwatch.stop()
        report.duration_ms = round(stopwatch.elapsed_ms, 1)
        logger.info(
            "Form fill finished: %d filled, %d checked, %d selected, %d uploaded, %d unmatched (%.0f ms)",
            len(report.filled),
            len(report.checked),
            len(report.selected),
            len(report.uploaded),
            len(report.unmatched),
            report.duration_ms,
        )
        return report

    async def _fill_one(self, page: Any, match: FieldMatch) -> Optional[tuple[str, str]]:
        """Fill a single field; returns ``(bucket, summary)`` or ``None`` when not applicable."""
        field = match.field
        value = match.value
        locator = page.locator(field.selector).first
        await locator.wait_for(state="attached", timeout=min(self.config.default_timeout_ms, 8000))

        if field.type in {"checkbox", "radio"}:
            desired = parse_bool(value, default=True)
            if field.type == "radio":
                if desired:
                    await locator.check(timeout=self.config.default_timeout_ms, force=not field.visible)
                    return "checked", f"radio '{value}' checked"
                return "skipped", "radio not selected (falsy value)"
            if desired:
                if not field.checked:
                    await locator.check(timeout=self.config.default_timeout_ms, force=not field.visible)
                return "checked", "checked"
            if field.checked:
                await locator.uncheck(timeout=self.config.default_timeout_ms, force=not field.visible)
                return "checked", "unchecked"
            return "skipped", "already unchecked"

        if field.tag == "select" or field.type == "select":
            chosen = await self._select_option(locator, field, value)
            return "selected", chosen

        if field.type == "file":
            return await self._upload(locator, value)

        if field.tag in {"textarea", "input"} or "contenteditable" in field.classes:
            payload = self._prepare_text_value(field, value)
            if self.config.clear_before_fill:
                try:
                    await locator.fill("", timeout=self.config.default_timeout_ms)
                except Exception:  # noqa: BLE001 - not all inputs support clearing
                    pass
            if self.config.humanize and payload and len(payload) <= max(1, self.config.humanize_max_chars):
                await locator.click(timeout=self.config.default_timeout_ms, force=not field.visible)
                await self._type_human(locator, payload)
            else:
                await locator.fill(payload, timeout=self.config.default_timeout_ms)
                if self.config.humanize and payload:
                    # Long values (messages, addresses) are filled instantly but still get a
                    # short "human" pause so the interaction pattern is not machine-perfect.
                    await asyncio.sleep(random.uniform(0.05, 0.2))
            await self._verify_value(locator, payload)
            return "filled", payload

        # contenteditable / exotic widgets
        try:
            await locator.click(timeout=self.config.default_timeout_ms, force=not field.visible)
            await page.keyboard.type(str(value), delay=self._typing_delay(str(value)))
            return "filled", str(value)
        except Exception:  # noqa: BLE001 - fall through to None
            return None

    async def _select_option(self, locator: Any, field: FormFieldSpec, value: str) -> str:
        """Select an option by value, then by visible label, then by index."""
        candidates = [value.strip()]
        if field.options:
            lowered = value.strip().lower()
            for option in field.options:
                label = str(option.get("label") or "")
                if label and (lowered == label.lower() or lowered in label.lower() or label.lower() in lowered):
                    candidates.append(option.get("value") or label)
        for candidate in candidates:
            try:
                await locator.select_option(candidate, timeout=self.config.default_timeout_ms)
                return candidate
            except Exception:  # noqa: BLE001 - try the next strategy
                continue
        # numeric index fallback
        if value.strip().isdigit() and field.options:
            index = int(value.strip())
            try:
                await locator.select_option(index=index, timeout=self.config.default_timeout_ms)
                return f"index:{index}"
            except Exception as exc:  # noqa: BLE001
                raise FieldFillError(f"select index {index} failed: {exc}") from exc
        raise FieldFillError(f"no matching <option> for value '{truncate(value, 60)}'")

    async def _upload(self, locator: Any, value: str) -> tuple[str, str]:
        """Attach files to an ``input[type=file]`` (``;`` or ``|`` separated paths)."""
        raw_paths = [token.strip() for token in re.split(r"[;|]", value) if token.strip()]
        existing: list[str] = []
        missing: list[str] = []
        for raw in raw_paths:
            candidate = Path(raw).expanduser()
            if candidate.exists():
                existing.append(str(candidate.resolve()))
            else:
                missing.append(raw)
        if not existing:
            raise FieldFillError(f"file input given but no existing file found: {', '.join(raw_paths) or value}")
        if missing:
            logger.warning("Skipping %d missing upload path(s): %s", len(missing), ", ".join(missing))
        await locator.set_input_files(existing, timeout=self.config.default_timeout_ms)
        return "uploaded", ", ".join(Path(p).name for p in existing)

    def _prepare_text_value(self, field: FormFieldSpec, value: str) -> str:
        """Coerce a value to the field's input type (dates, numbers, maxlength, money)."""
        text = str(value).strip()
        if field.type == "number" or (field.pattern and re.fullmatch(r"\d+", field.pattern or "")):
            cleaned = re.sub(r"[^\d.,\-]", "", text).replace(",", ".")
            if cleaned.count(".") > 1:
                cleaned = cleaned.replace(".", "", cleaned.count(".") - 1)
            text = cleaned
        elif field.type == "date":
            text = self._to_iso_date(text)
        elif field.type == "time":
            text = text if re.match(r"^\d{1,2}:\d{2}", text) else text
        elif field.type == "tel":
            text = re.sub(r"[^\d+ ]", "", text)
        if field.maxlength and len(text) > field.maxlength:
            logger.debug("Truncating value for %s to maxlength=%d", field.describe()[:40], field.maxlength)
            text = text[: field.maxlength]
        return text

    @staticmethod
    def _to_iso_date(text: str) -> str:
        """Convert ``28.09.2026`` / ``09/28/2026`` / ``2026-09-28`` into ISO for date inputs."""
        text = text.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return text
        match = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", text)
        if match:
            day, month, year = match.groups()
            if len(year) == 2:
                year = ("20" if int(year) < 70 else "19") + year
            if int(day) > 12 and int(month) <= 12:  # DD.MM layout
                return f"{year}-{int(month):02d}-{int(day):02d}"
            if int(month) > 12:  # MM.DD layout
                return f"{year}-{int(day):02d}-{int(month):02d}"
            return f"{year}-{int(month):02d}-{int(day):02d}"
        return text

    async def _type_human(self, locator: Any, text: str) -> None:
        """Type character by character with a jittered delay (bypasses naive bot checks).

        The delay is derived from ``humanize_min_ms``/``humanize_max_ms`` but bounded by
        ``humanize_typing_budget_ms`` so a 20-character field never costs 3+ seconds.
        """
        delay = self._typing_delay(text)
        try:
            await locator.press_sequentially(text, delay=delay, timeout=max(self.config.default_timeout_ms, 5000))
            return
        except Exception as exc:  # noqa: BLE001 - older Playwright or exotic widgets
            logger.debug("press_sequentially unavailable (%s); falling back to keyboard typing", truncate(str(exc), 120))
        if not self.config.humanize:
            # Playwright's own fast path when humanisation is not required.
            await locator.evaluate(
                "(el, value) => { el.value = value; el.dispatchEvent(new Event('input', { bubbles: true })); }",
                text,
            )
            return
        await locate_focus(locator)
        page = locator.page
        await page.keyboard.type(text, delay=delay)

    def _typing_delay(self, text: str = "") -> int:
        """Per-character delay in milliseconds (0 when humanisation is disabled)."""
        if not self.config.humanize:
            return 0
        delay = random.randint(self.config.humanize_min_ms, self.config.humanize_max_ms)
        length = max(1, len(text or ""))
        budget = max(0, int(getattr(self.config, "humanize_typing_budget_ms", 1800)))
        if budget:
            delay = min(delay, max(1, budget // length))
        return max(1, delay) if self.config.humanize else 0

    async def _verify_value(self, locator: Any, expected: str) -> None:
        """Warn (never fail) when the DOM value differs from what we typed (masks/JS)."""
        try:
            current = await locator.input_value(timeout=2000)
        except Exception:  # noqa: BLE001 - not an input anymore
            return
        if current != expected:
            logger.debug("Value drift: expected %r, DOM holds %r", truncate(expected, 40), truncate(current, 40))

    def _display_value(self, key: str, value: str) -> str:
        """Mask credentials before they reach the console/report."""
        if self.redactor.is_sensitive_field(key):
            self.redactor.text(value)
            return f"<{len(str(value))} chars masked>"
        return truncate(str(value), 40)

    # ------------------------------------------------------------------ captcha
    async def detect_captcha(self, page: Any) -> list[dict[str, Any]]:
        """Return visible CAPTCHA/challenge markers (empty list = none)."""
        if not self.config.detect_captcha:
            return []
        try:
            found = await page.evaluate(_CAPTCHA_JS, list(CAPTCHA_SELECTORS))
        except Exception as exc:  # noqa: BLE001 - page may be mid-navigation
            logger.debug("CAPTCHA probe failed: %s", exc)
            return []
        interesting = [item for item in (found or []) if item.get("selector") in {"#challenge-form", "#cf-challenge-running", ".cf-turnstile", ".g-recaptcha", ".h-captcha"} or item.get("visible")]
        if interesting:
            logger.warning("CAPTCHA/challenge detected: %s", ", ".join(item["selector"] for item in interesting))
        return interesting

    # ------------------------------------------------------------------ submit
    async def find_submit(self, page: Any, row: Optional[TargetRow] = None) -> Optional[Any]:
        """Locate the submit control using row overrides → config → label heuristics."""
        candidates: list[str] = []
        if row is not None:
            if row.submit_selector:
                candidates.append(row.submit_selector)
            if row.submit_button_text:
                candidates.append(f'button:has-text("{row.submit_button_text}")')
        if self.config.submit_selector:
            candidates.append(self.config.submit_selector)
        if self.config.submit_button_text:
            candidates.append(f'button:has-text("{self.config.submit_button_text}")')
        candidates.extend(SELECTOR_TEMPLATES["submit"])
        for text in SUBMIT_TEXTS:
            candidates.append(f'button:has-text("{text}")')
            candidates.append(f'input[type="submit"][value*="{text}" i]')
        for selector in candidates:
            try:
                locator = page.locator(selector).first
                if await locator.count() and await locator.is_visible():
                    interceptor = await self._is_submit_intercepted(page, locator)
                    if interceptor:
                        continue
                    return locator
            except Exception:  # noqa: BLE001 - keep searching
                continue
        return None

    async def _is_submit_intercepted(self, page: Any, locator: Any) -> bool:
        """Detect buttons that are decorative overlays rather than real submits."""
        try:
            tag = await locator.evaluate("el => el.tagName.toLowerCase()")
            if tag == "button":
                button_type = await locator.get_attribute("type") or "submit"
                return button_type not in {"submit", ""}
            return False
        except Exception:  # noqa: BLE001
            return False

    async def submit(
        self,
        page: Any,
        row: Optional[TargetRow] = None,
        *,
        button: Optional[Any] = None,
    ) -> StepResult:
        """Click the submit control (or press Enter) and report the action taken."""
        stopwatch = Stopwatch()
        stopwatch.__enter__()
        if row is not None and row.metadata.get("submit") is False:
            stopwatch.stop()
            return StepResult(name="submit", status="skipped", details={"reason": "row metadata submit=false"}, duration_ms=stopwatch.elapsed_ms)

        if button is None:
            button = await self.find_submit(page, row)
        if button is None:
            stopwatch.stop()
            try:
                await page.keyboard.press("Enter")
                logger.info("  ⏎ No submit button found - pressed Enter instead")
                return StepResult(name="submit", status="ok", details={"method": "keyboard-enter"}, duration_ms=stopwatch.elapsed_ms)
            except Exception as exc:  # noqa: BLE001
                raise SubmitError(f"No submit control found and pressing Enter failed: {exc}") from exc

        try:
            label = ""
            try:
                label = truncate((await button.inner_text()) or "", 40)
            except Exception:  # noqa: BLE001
                label = ""
            await self._human_pause(0.05, 0.25)
            await button.scroll_into_view_if_needed(timeout=5000)
            await button.click(timeout=self.config.default_timeout_ms)
            stopwatch.stop()
            logger.info("  ⏎ Submit clicked%s (%s)", f": '{label}'" if label else "", human_ms(stopwatch.elapsed_ms))
            return StepResult(
                name="submit",
                status="ok",
                duration_ms=round(stopwatch.elapsed_ms, 1),
                details={"method": "click", "label": label},
            )
        except Exception as exc:  # noqa: BLE001 - retry through JS click
            logger.debug("Native click failed (%s); retrying with dispatchEvent", exc)
            try:
                await button.dispatch_event("click")
                await button.evaluate("el => el.click && el.click()")
                stopwatch.stop()
                return StepResult(
                    name="submit",
                    status="ok",
                    duration_ms=round(stopwatch.elapsed_ms, 1),
                    details={"method": "js-click"},
                )
            except Exception as inner:  # noqa: BLE001
                stopwatch.stop()
                raise SubmitError(f"Could not submit the form: {inner}") from inner

    async def _human_pause(self, low: float = 0.05, high: float = 0.25) -> None:
        if not self.config.humanize:
            await asyncio.sleep(self.config.action_pause_ms / 1000.0)
            return
        await asyncio.sleep(random.uniform(low, high))

    # ------------------------------------------------------------------ outcome
    async def wait_for_outcome(self, page: Any, row: Optional[TargetRow] = None) -> dict[str, Any]:
        """Wait for a success/error signal after submitting.

        Returns ``{"status": "success"|"error"|"unknown", "reason": str, ...}``; never raises -
        the caller decides how strict to be (see ``OutcomeTimeoutError`` usage in the engine).
        """
        timeout_ms = row.wait_after_submit_ms if row and row.wait_after_submit_ms else self.config.outcome_timeout_ms
        started_url = page.url
        success_url = (row.success_url_regex if row and row.success_url_regex else None) or self.config.success_url_regex
        success_selector = (row.success_selector if row and row.success_selector else None) or self.config.success_selector
        error_selector = (row.error_selector if row and row.error_selector else None) or self.config.error_selector
        success_texts = list(self.config.success_text) + (["thank you", "thanks", "başarılı", "tesekkur", "teşekkür", "kayıt tamamlandı", "welcome", "hoş geldiniz"] if not self.config.success_text else [])
        failure_texts = list(self.config.failure_text) + (["error", "hata", "invalid", "geçersiz", "required", "zorunlu", "failed", "başarısız"] if not self.config.failure_text else [])

        stopwatch = Stopwatch()
        stopwatch.__enter__()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_ms / 1000.0
        outcome: dict[str, Any] = {"status": "unknown", "reason": "no signal detected", "url": started_url}

        while loop.time() < deadline:
            try:
                await asyncio.sleep(0.35)
                await self._wait_settle(page, 5000)
            except Exception:  # noqa: BLE001 - navigation in progress
                pass

            try:
                current_url = page.url
            except Exception:  # noqa: BLE001
                break

            if success_url and re.search(success_url, current_url, re.I):
                stopwatch.stop()
                return {
                    "status": "success",
                    "reason": f"URL matched success pattern '{success_url}'",
                    "url": current_url,
                    "duration_ms": round(stopwatch.elapsed_ms, 1),
                }
            if error_selector:
                try:
                    if await page.locator(error_selector).first.is_visible():
                        stopwatch.stop()
                        detail = truncate(await self._text_of(page, error_selector), 200)
                        return {
                            "status": "error",
                            "reason": f"error selector '{error_selector}' visible",
                            "message": detail,
                            "url": current_url,
                            "duration_ms": round(stopwatch.elapsed_ms, 1),
                        }
                except Exception:  # noqa: BLE001
                    pass
            if success_selector:
                try:
                    if await page.locator(success_selector).first.is_visible():
                        stopwatch.stop()
                        return {
                            "status": "success",
                            "reason": f"success selector '{success_selector}' visible",
                            "url": current_url,
                            "duration_ms": round(stopwatch.elapsed_ms, 1),
                        }
                except Exception:  # noqa: BLE001
                    pass

            try:
                body_text = (await page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 20000)")).lower()
            except Exception:  # noqa: BLE001
                body_text = ""
            for marker in failure_texts:
                if marker and marker in body_text and ("hata" in marker or "error" in marker or "invalid" in marker or "zorunlu" in marker):
                    # only treat as error when the marker appears near a form context
                    if re.search(r"(error|hata|invalid|geçersiz|gecersiz|zorunlu|required)[^.\n]{0,80}", body_text):
                        stopwatch.stop()
                        return {
                            "status": "error",
                            "reason": f"error text detected ('{marker}')",
                            "url": current_url,
                            "duration_ms": round(stopwatch.elapsed_ms, 1),
                        }
            for marker in success_texts:
                if marker and marker in body_text:
                    stopwatch.stop()
                    return {
                        "status": "success",
                        "reason": f"success text detected ('{marker}')",
                        "url": current_url,
                        "duration_ms": round(stopwatch.elapsed_ms, 1),
                    }

            captcha = await self.detect_captcha(page)
            if captcha:
                stopwatch.stop()
                return {
                    "status": "captcha",
                    "reason": "CAPTCHA/challenge appeared after submit",
                    "markers": [item["selector"] for item in captcha],
                    "url": current_url,
                    "duration_ms": round(stopwatch.elapsed_ms, 1),
                }

        stopwatch.stop()
        outcome["duration_ms"] = round(stopwatch.elapsed_ms, 1)
        try:
            outcome["url"] = page.url
        except Exception:  # noqa: BLE001
            pass
        if outcome["status"] == "unknown":
            logger.warning(
                "No success/error signal within %d ms (url=%s). Configure --success-url-regex or a "
                "success_selector column for a deterministic result.",
                timeout_ms,
                outcome.get("url"),
            )
        return outcome

    async def _wait_settle(self, page: Any, timeout: float) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=timeout)
        except Exception:  # noqa: BLE001 - timeouts are expected while waiting for signals
            pass

    @staticmethod
    async def _text_of(page: Any, selector: str) -> str:
        try:
            return truncate(normalize_ws(await page.locator(selector).first.inner_text()), 300)
        except Exception:  # noqa: BLE001
            return ""

    # ------------------------------------------------------------------ orchestration
    async def fill_form_for_row(
        self,
        page: Any,
        row: TargetRow,
        *,
        report: Optional[FillReport] = None,
        iterate: bool = True,
        max_passes: int = 3,
    ) -> FillReport:
        """Full fill cycle for one row: scan → plan → fill, iterating as the form grows."""
        report = report or FillReport()
        remaining = dict(row.form_data)
        overrides = dict(row.selectors or {})
        pass_index = 0
        while remaining and pass_index < max_passes:
            pass_index += 1
            fields = await self.scan(page)
            if not fields:
                if pass_index == 1:
                    raise FieldResolutionError(
                        f"No form fields found on {page.url} - is the page fully loaded? "
                        "Use --steps to navigate to the form first."
                    )
                break
            plan = self.plan(fields, remaining, overrides=overrides)
            if not plan.matches:
                if plan.unmatched:
                    logger.warning(
                        "No checkbox/select/field matched for keys: %s",
                        ", ".join(plan.unmatched),
                    )
                break
            await self.fill(page, plan, row=row, report=report)
            filled_keys = plan.keys()
            before = len(remaining)
            remaining = {k: v for k, v in remaining.items() if k not in filled_keys}
            if iterate and pass_index == 1 and remaining and before != len(remaining):
                await self._human_pause(0.15, 0.4)
                # Some wizards reveal the next step after a "continue" click; the engine
                # decides when to click, so we simply re-scan once more.
                continue
            break

        if remaining:
            report.unmatched = sorted(set(report.unmatched) | set(remaining.keys()))
            logger.warning(
                "%d data key(s) were not placed: %s",
                len(remaining),
                ", ".join(sorted(remaining.keys())),
            )
        return report


async def locate_focus(locator: Any) -> None:
    """Focus a field for keyboard typing with graceful degradation."""
    try:
        await locator.focus(timeout=5000)
    except Exception:  # noqa: BLE001 - invisible/hidden inputs cannot be focused
        try:
            await locator.click(force=True, timeout=5000)
        except Exception:  # noqa: BLE001 - last resort: JS focus
            await locator.evaluate("el => el.focus && el.focus()")


def looks_like_form_page(scan_payload: dict[str, Any]) -> bool:
    """Heuristic used by the API-discovery scenario to decide whether to fill."""
    counts = (scan_payload or {}).get("counts", {})
    return bool(counts.get("inputs"))


def captcha_action(config: Config, detected: Sequence[dict[str, Any]]) -> str:
    """Return the configured CAPTCHA policy after detection (``error``/``skip``/``continue``)."""
    if not detected:
        return "continue"
    action = config.captcha_action if config.captcha_action in CAPTCHA_ACTION_REQUIRED else "error"
    if action == "error":
        raise CaptchaDetectedError(
            "CAPTCHA detected and --captcha-action=error; aborting this target",
            details={"markers": [item["selector"] for item in detected]},
        )
    return action


def dumps_plan(plan: FieldPlan) -> str:
    """Human readable plan preview (used in dry runs/debug logs)."""
    payload = {
        "fields_seen": plan.fields_seen,
        "matches": [match.to_dict() for match in plan.matches],
        "unmatched": plan.unmatched,
        "skipped": plan.skipped,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
