"""``risk_screen.py`` birim testleri (ağ erişimi yok, ağ isteği yok).

Amaç: tarayıcının *politika* kararlarını sabitlemek —
hangi ifade hangi deseni tetikler, skor nasıl banda çevrilir, ön ödeme kapısı neden her zaman
"DOKUNMA" yapar ve modülün içinde hiçbir HTTP istemcisi bulunmadığını garanti etmek.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("risk_screen_under_test", HERE / "risk_screen.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["risk_screen_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def rs() -> Any:
    return _load()


def _row(**overrides: str) -> dict[str, str]:
    base = {
        "Bot / Mini-App Adı": "ÖrnekBot",
        "t.me / Link": "t.me/ornek",
        "Kazanç Mekanizması": "anket doldurma",
        "Ödeme Yöntemi": "PayPal",
        "Min. Çekim / Eşik": "5 USD",
        "Tahmini Kazanç": "20 USD",
        "Risk": "Orta",
        "Güven Notu / Uyarı": "kullanıcı raporları var",
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------------------------- modül sınırı
def test_module_has_no_network_client() -> None:
    """Araç çevrimdışı olmalı: kaynakta ağ kütüphanesi importu BULUNMAMALI."""
    source = (HERE / "risk_screen.py").read_text(encoding="utf-8")
    for forbidden in ("import aiohttp", "import requests", "urllib.request", "httpx", "socket"):
        assert forbidden not in source, f"ağ bağımlılığı bulundu: {forbidden}"


# ------------------------------------------------------------------------------- sütun eşleme
def test_map_columns_matches_turkish_headers(rs: Any) -> None:
    mapping = rs.map_columns(
        [
            "Bot / Mini-App Adı",
            "t.me / Link",
            "Kazanç Mekanizması",
            "Ödeme Yöntemi",
            "Min. Çekim / Eşik",
            "Güven Notu / Uyarı",
        ]
    )
    assert mapping["name"] == "Bot / Mini-App Adı"
    assert mapping["mechanism"] == "Kazanç Mekanizması"
    assert mapping["note"] == "Güven Notu / Uyarı"
    assert mapping["threshold"] == "Min. Çekim / Eşik"


# ---------------------------------------------------------------------- etiketler / eşikler
def test_label_thresholds(rs: Any) -> None:
    assert rs.label_for(90, fatal=False) == rs.DOKUNMA
    assert rs.label_for(70, fatal=False) == rs.DOKUNMA
    assert rs.label_for(40, fatal=False) == rs.TEMKINLI
    assert rs.label_for(30, fatal=False) == rs.TEMKINLI
    assert rs.label_for(15, fatal=False) == rs.DOGRULA
    assert rs.label_for(0, fatal=False) == rs.DUSUK


def test_advance_fee_forces_dokunma_even_with_low_sum(rs: Any) -> None:
    """Ön ödeme kapısı tek başına 'DOKUNMA' demelidir (tek sinyal, 40 puan)."""
    verdict = rs.analyse_row(1, rs.map_columns(list(_row().keys())) and {
        name: value
        for name, value in {
            "name": "PaketBot",
            "link": "t.me/x",
            "mechanism": "VIP paket ile kazanç",
            "payout": "USDT",
            "threshold": "50 USDT",
            "earnings": "",
            "risk": "Yüksek",
            "note": "önce yatırım yapmalısın",
        }.items()
    })
    assert verdict.label == rs.DOKUNMA
    assert verdict.score >= 40
    assert any(item.rule == "advance_fee" and item.fatal for item in verdict.findings)


# ------------------------------------------------------------------------- anahtar kelimeler
@pytest.mark.parametrize(
    ("text", "expected_rule"),
    [
        ("para yatırman gerekiyor", "advance_fee"),
        ("aktivasyon ücreti 100 TL", "advance_fee"),
        ("VIP üyelik al", "advance_fee"),
        ("kazanma paketi satın al", "advance_fee"),
        ("önce yatırım yap", "advance_fee"),
        ("takım kur, referans topla", "referral_pyramid"),
        ("günde %30 garanti kazanç", "unrealistic_returns"),
        ("admin dm ile iletişim", "no_corporate_trace"),
        ("hiçbir şey yapmadan otomatik kazan", "no_transparency"),
    ],
)
def test_keywords_trigger_expected_rule(rs: Any, text: str, expected_rule: str) -> None:
    field = "note" if expected_rule != "referral_pyramid" else "mechanism"
    hits = [
        rule.key
        for rule in rs.RULES
        if rs.scan_text(rule, field, text) is not None
    ]
    assert expected_rule in hits, f"{text!r} → beklenen {expected_rule}, bulunan {hits}"


# --------------------------------------------------------------------------------- tablo
def test_analyse_table_scores_and_labels(rs: Any) -> None:
    rows = [
        _row(**{"Güven Notu / Uyarı": "önce yatırım yapman gerekiyor; VIP paket şartı"}),
        _row(**{"Kazanç Mekanizması": "reklam izleme", "Güven Notu / Uyarı": "kurumsal bilgi mevcut"}),
    ]
    verdicts = rs.analyse_table(rows)
    assert verdicts[0].label == rs.DOKUNMA and verdicts[0].score >= 40
    assert verdicts[1].label == rs.DUSUK and verdicts[1].score == 0


def test_every_finding_carries_reason_and_advice(rs: Any) -> None:
    verdict = rs.analyse_table([_row(**{"Güven Notu / Uyarı": "para yatır sonra çek"})])[0]
    assert verdict.findings
    for finding in verdict.findings:
        assert len(finding.reason) > 30, "gerekçe açıklayıcı olmalı"
        assert finding.advice, "öneri boş olmamalı"
        assert finding.evidence, "kanıt (eşleşen ifade) kaydedilmeli"


def test_markdown_report_marks_low_score_as_not_proof(rs: Any) -> None:
    verdicts = rs.analyse_table([_row()])
    report = rs.render_markdown(verdicts, source="test.xlsx")
    assert "güvenlik kanıtı değildir" in report.lower()
    assert "DOKUNMA" in report  # bant tablosu raporda görünür


def test_json_payload_is_serialisable(rs: Any) -> None:
    import json

    verdicts = rs.analyse_table([_row(**{"Güven Notu / Uyarı": "paket al"})])
    payload = json.dumps([item.to_dict() for item in verdicts], ensure_ascii=False)
    assert "risk_score" in payload and "findings" in payload
