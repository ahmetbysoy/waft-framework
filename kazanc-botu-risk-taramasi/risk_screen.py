#!/usr/bin/env python3
"""``risk_screen.py`` - Telegram "para kazanma" botları için DOLANDIRICILIK RİSK TARAMA MOTORU.

Amaç
----
Bir Excel/CSV tablosundaki kayıtları **yalnızca risk** ekseninde inceler ve kullanıcıyı ön ödeme
tuzağı, ponzi şeması ve hareketli çekim eşiği gibi desenlerden korumak için gerekçeli bir rapor
üretir: hangi kayıt, hangi alanda, hangi ifade yüzünden riskli, neden riskli, ne yapılmalı.

Sınırlar (bilinçli)
-------------------
* **Kazanç potansiyeli / hızlı para sıralaması YAPMAZ.** Sıralama ölçütü yalnızca risk skorudur;
  düşük çekim eşiği veya kripto ödeme "iyi hedef" diye puanlanmaz — bunlar risk sinyali olarak
  okunur (geri döndürülemez ödeme kanalı).
* **Hiçbir ağ isteği atmaz.** Ne hedef siteye, ne Telegram'a, ne başka bir servise. Tamamen
  çevrimdışı metin analizi + raporlama.
* **Düşük puan güvenlik kanıtı değildir.** Skor yalnızca metinde *bulunan* desenleri sayar;
  yazılmamış bir tuzak da olabilir. Rapor bunu her bölümde hatırlatır.

Puanlama (0-100, yüksek = riskli)
---------------------------------
| Ağırlık | Desen |
|---|---|
| 40 | **Ön ödeme / aktivasyon / paket/VIP kapısı** (tek başına "DOKUNMA") |
| 25 | Referans piramidi (takım kur, davet, seviye) |
| 25 | Gerçek dışı veya garantili getiri vaadi |
| 15 | Çekim eşiği / davet şartı baskısı (hareketli çekim eşiği) |
| 15 | Kurumsal iz yokluğu (anonimlik, admin DM, şirket bilgisi yok) |
| 10 | Yalnızca kripto ile geri döndürülemez ödeme kanalı |
| 10 | Aciliyet / kıtlık baskısı |
| 10 | Şeffaflık eksikliği (mekanizma belirsiz) |

Etiketler: **DOKUNMA** (>=70 veya ön ödeme kapısı) · **TEMKİNLİ OL** (>=30) · **DOĞRULA** (>=10) ·
**DÜŞÜK RİSK** (<15 — "risk deseni bulunmadı" demektir, "güvenli" değil).

Kullanım
--------
    python3 risk_screen.py                                   # varsayılan dosya adını arar
    python3 risk_screen.py --input tablo.xlsx --sheet "🤖 Botlar"
    python3 risk_screen.py --input tablo.xlsx --out-md rapor.md --out-json rapor.json
    python3 risk_screen.py --demo-sheet demo.xlsx             # sentetik tablo üret (dosya yoksa)
    python3 risk_screen.py --self-test                        # sentetik tabloyla kendini sına

Çıkış kodu: 0 = kritik/yüksek bulgu yok · 1 = DOKUNMA/TEMKİNLİ OL etiketli kayıt var · 2 = kullanım hatası.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Optional, Sequence

# ======================================================================================
# etiketler ve bantlar
# ======================================================================================
DOKUNMA: Final[str] = "DOKUNMA"
TEMKINLI: Final[str] = "TEMKİNLİ OL"
DOGRULA: Final[str] = "DOĞRULA"
DUSUK: Final[str] = "DÜŞÜK RİSK"

#: (alt sınır, etiket) - yüksek skor = yüksek risk.
BANDS: Final[tuple[tuple[int, str], ...]] = ((70, DOKUNMA), (30, TEMKINLI), (10, DOGRULA), (0, DUSUK))

#: Varsayılan girdi dosyası (kullanıcının tablosu bu adla beklenir).
DEFAULT_INPUT: Final[str] = "telegram_bot_grup_kazanc_arastirmasi (2).xlsx"

#: Sütun adları → iç alan adları (Türkçe/İngilizce eşleme, büyük/küçük harf duyarsız).
FIELD_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "name": ("bot / mini-app adı", "bot adı", "bot", "ad", "i̇sim", "isim", "name", "platform"),
    "link": ("t.me / link", "link", "url", "adres", "kanal"),
    "mechanism": ("kazanç mekanizması", "mekanizma", "mechanism", "yöntem", "nasıl"),
    "payout": ("ödeme yöntemi", "ödeme", "payout", "payment"),
    "threshold": ("min. çekim / eşik", "min çekim", "minimum çekim", "eşik", "threshold"),
    "earnings": ("tahmini kazanç", "kazanç", "earnings", "getiri"),
    "risk": ("risk", "risk seviyesi", "risk durumu"),
    "note": ("güven notu / uyarı", "güven notu", "not", "uyarı", "note", "warning"),
}

#: Öncelikli taranan alanlar (spesifikasyondaki iki sütun) + ikincil alanlar.
PRIMARY_FIELDS: Final[tuple[str, ...]] = ("note", "mechanism")
SECONDARY_FIELDS: Final[tuple[str, ...]] = ("payout", "threshold", "earnings", "risk")


@dataclass(frozen=True, slots=True)
class Rule:
    """Bir dolandırıcılık deseni: ağırlık, tetikleyiciler, gerekçe ve öneri."""

    key: str
    label: str
    weight: int
    patterns: tuple[str, ...]
    reason: str
    advice: str
    fatal: bool = False  # tek başına "DOKUNMA" demek için yeterli mi?
    fields: tuple[str, ...] = ()  # boş = tüm alanlar; doluysa yalnızca bu alanlar taranır


#: Anahtar kelimeler spesifikasyondan birebir alınmıştır: para yatır, aktivasyon, paket, VIP,
#: kazanma paketi, önce yatırım (+ eş anlamlıları).
RULES: Final[tuple[Rule, ...]] = (
    Rule(
        key="advance_fee",
        label="Ön ödeme / aktivasyon / paket-VIP kapısı",
        weight=40,
        patterns=(
            r"para\s*yat[ıi]r", r"[öo]nce\s*yat[ıi]r[ıi]m", r"aktivasyon", r"kazma\s*paketi",
            r"kazanma\s*paketi", r"\bvip\b", r"paket\s*al", r"bakiye\s*y[üu]kle",
            r"\bdeposit\b", r"[öo]nce\s*[öo]deme", r"yat[ıi]r[ıi]m\s*yap",
        ),
        reason="Kazanca erişmek için önce sizden para isteniyor (aktivasyon ücreti, paket, VIP, "
               "bakiye yükleme). Gerçek görev/anket platformları sizden ücret almaz — ödemeyi onlar "
               "size yapar. Bu, 'ön ödeme tuzağı'nın (advance-fee fraud) anahtarıdır.",
        advice="DOKUNMA. Ödenen para geri alınamaz; sonrasında görünen 'kazanç' yalnızca ekran "
               "rakamıdır ve çekim aşamasında yeni şartlarla engellenir.",
        fatal=True,
    ),
    Rule(
        key="referral_pyramid",
        label="Referans piramidi / ponzi şeması",
        weight=25,
        patterns=(
            r"tak[ıi]m\s*kur", r"referans", r"davet\s*et", r"seviye\s*atla", r"alt\s*ekip",
            r"\breferral\b", r"network\s*marketing", r"piramit",
        ),
        reason="Gelir, ürün/hizmet satışından değil yeni katılımcı getirmekten geliyor. Ponzi "
               "şemalarının tanımı budur: ödemeler eski katılımcıya yeni katılımcının parasından "
               "yapılır ve sistem matematiksel olarak er ya da geç çöker.",
        advice="Uzak dur. Getirdiğiniz kişiler de aynı kaybı yaşar; sosyal çevreniz zarar görür ve "
               "bazı durumlarda siz de sorumlu tutulursunuz.",
    ),
    Rule(
        key="unrealistic_returns",
        label="Gerçek dışı / garantili getiri vaadi",
        weight=25,
        patterns=(
            r"g[üu]nde\s*%", r"g[üu]nl[üu]k\s*%", r"garanti\s*kazan", r"risksiz\s*kazan",
            r"\d+\s*kat[ıi]", r"s[ıi]n[ıi]rs[ıi]z\s*kazan", r"an[ıi]nda\s*zengin",
            r"%\s*\d+\s*(g[üu]nl[üu]k|ayl[ıi]k)",
        ),
        reason="Yüksek ve 'garantili' getiri piyasada yoktur; vaadin büyüklüğü tek başına "
               "dolandırıcılık göstergesidir. Gerçek platformlarda kazanç değişkendir ve emek/veri "
               "karşılığıdır.",
        advice="Bağımsız, doğrulanabilir ödeme kanıtı olmadan bu tür vaatleri ciddiye almayın.",
    ),
    Rule(
        key="moving_threshold",
        label="Hareketli çekim eşiği / davet şartı baskısı",
        weight=15,
        patterns=(
            r"\d{2,}\s*(usd|usdt|tl|try|eur|\$|₺)", r"çekim\s*için", r"\d+\s*davet",
            r"[şs]art", r"ko[şs]ul", r"seviye\s*\d+\s*ol",
        ),
        reason="Çekim, davet/aktivite/şart tamamlamaya bağlanmış. Bu kurgunun amacı eşiğe hiç "
               "ulaştırmamaktır: eşiğe yaklaştıkça yeni bir şart eklenir ('hareketli çekim eşiği').",
        advice="Şartları yazılı olarak isteyin ve sabit mi diye bakın. Değişiyorsa veya davet "
               "gerektiriyorsa çıkın.",
        fields=("note", "mechanism", "threshold"),
    ),
    Rule(
        key="irreversible_payout",
        label="Yalnızca kripto ile geri döndürülemez ödeme kanalı",
        weight=10,
        patterns=(r"\busdt\b", r"\bbtc\b", r"\bton\b", r"\btrx\b", r"kripto", r"crypto"),
        reason="Ödeme yalnızca kripto ise işlem geri alınamaz ve muhatap genellikle tüzel kişilik "
               "değildir; şikâyet edilecek bir merci bulunmaz.",
        advice="Kripto tek başına suç kanıtı değildir; ama şirket bilgisi yoksa riski büyütür.",
        fields=("payout", "note", "mechanism"),
    ),
    Rule(
        key="no_corporate_trace",
        label="Kurumsal iz yokluğu / anonimlik",
        weight=15,
        patterns=(
            r"admin\s*dm", r"anonim", r"kim\s*oldu[ğg]u\s*belirsiz", r"[şs]irket\s*bilgisi\s*yok",
            r"bilinmiyor", r"gizli\s*y[öo]netim",
        ),
        reason="Muhatabın tüzel kişiliği, adresi veya resmî iletişimi yok. Sorun çıktığında hesap "
               "veren bir taraf bulunmaz.",
        advice="Doğrulama listesi: şirket unvanı/sicil, resmî site ve alan adı yaşı, destek kanalı, "
               "kullanım şartları, KVKK/gizlilik metni. Yoksa listeye almayın.",
    ),
    Rule(
        key="urgency_pressure",
        label="Aciliyet / kıtlık baskısı",
        weight=10,
        patterns=(r"son\s*[şs]ans", r"s[üu]reli", r"acele\s*et", r"bug[üu]n\s*bitiyor", r"kontenjan"),
        reason="Karar süresini kısaltan mesajlar, inceleme yapmayı engellemek için kullanılır.",
        advice="Aciliyet baskısı gördüğünüz yerde durun; gerçek bir hizmet yarını da bekleyebilir.",
    ),
    Rule(
        key="no_transparency",
        label="Şeffaflık eksikliği (belirsiz mekanizma)",
        weight=10,
        patterns=(r"^\s*$", r"^çok\s*kolay", r"sadece\s*bekle", r"otomatik\s*kazan", r"hiçbir\s*[şs]ey\s*yapma"),
        reason="Kazanç mekanizması açıklanmıyor: ne yapıldığı belli değilse, gelirin kaynağı da "
               "yoktur. 'Kim, neden, ne için ödüyor?' sorusu cevapsız kalır.",
        advice="Mekanizma net değilse (hangi görev, kim ödüyor, neden ödüyor) katılmayın.",
    ),
)


@dataclass(slots=True)
class Finding:
    """Tek bir risk bulgusu: hangi desen, hangi alanda, hangi kanıtla."""

    rule: str
    label: str
    weight: int
    field: str
    evidence: str
    reason: str
    advice: str
    fatal: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "label": self.label,
            "weight": self.weight,
            "field": self.field,
            "evidence": self.evidence,
            "reason": self.reason,
            "advice": self.advice,
            "fatal": self.fatal,
        }


@dataclass(slots=True)
class Verdict:
    """Bir kaydın taranmış hâli: skor, etiket, gerekçeler."""

    index: int
    name: str
    link: str
    fields: dict[str, str]
    findings: list[Finding] = field(default_factory=list)
    score: int = 0
    label: str = DUSUK

    def to_dict(self) -> dict[str, Any]:
        return {
            "row": self.index,
            "name": self.name,
            "link": self.link,
            "risk_score": self.score,
            "label": self.label,
            "fields": self.fields,
            "findings": [item.to_dict() for item in self.findings],
        }


# ======================================================================================
# tarama
# ======================================================================================
def normalise_header(value: Any) -> str:
    """Sütun başlığını karşılaştırılabilir hâle getirir."""
    return re.sub(r"\s+", " ", str(value).strip().lower())


def map_columns(columns: Sequence[Any]) -> dict[str, str]:
    """Sütun başlıklarını iç alan adlarına eşler; eşleşmeyen alanlar atlanır."""
    normalised = {normalise_header(column): str(column) for column in columns}
    mapping: dict[str, str] = {}
    for field_name, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if alias in normalised:
                mapping[field_name] = normalised[alias]
                break
        else:
            for key, original in normalised.items():
                if any(alias in key for alias in aliases):
                    mapping[field_name] = original
                    break
    return mapping


def scan_text(rule: Rule, field_name: str, text: str) -> Optional[Finding]:
    """Bir alanda deseni arar; bulursa kanıtıyla birlikte :class:`Finding` döndürür."""
    value = text or ""
    for pattern in rule.patterns:
        match = re.search(pattern, value, flags=re.I)
        if match:
            return Finding(
                rule=rule.key,
                label=rule.label,
                weight=rule.weight,
                field=field_name,
                evidence=match.group(0),
                reason=rule.reason,
                advice=rule.advice,
                fatal=rule.fatal,
            )
    return None


def label_for(score: int, *, fatal: bool) -> str:
    """Skoru etikete çevirir; ön ödeme kapısı etiketi her zaman DOKUNMA yapar."""
    if fatal:
        return DOKUNMA
    for threshold, label in BANDS:
        if score >= threshold:
            return label
    return DUSUK


def analyse_row(index: int, record: dict[str, str]) -> Verdict:
    """Tek kaydı tarar: öncelikli alanlar + ikincil alanlar → skor → etiket."""
    fields = {name: str(record.get(name, "") or "") for name in FIELD_ALIASES}
    name = fields.get("name") or f"satır-{index}"
    link = fields.get("link", "")

    default_order: tuple[str, ...] = PRIMARY_FIELDS + SECONDARY_FIELDS
    findings: list[Finding] = []
    for rule in RULES:
        # Kural yalnızca belirli alanlarda anlamlıysa (ör. para birimi → çekim eşiği, kripto →
        # ödeme kanalı) diğer alanlarda yanlış pozitif üretmesin.
        for field_name in (rule.fields or default_order):
            finding = scan_text(rule, field_name, fields.get(field_name, ""))
            if finding is not None:
                findings.append(finding)
                break

    fatal = any(item.fatal for item in findings)
    score = min(100, sum(item.weight for item in findings))
    return Verdict(
        index=index,
        name=name,
        link=link,
        fields=fields,
        findings=findings,
        score=score,
        label=label_for(score, fatal=fatal),
    )


def analyse_table(rows: Sequence[dict[str, Any]], *, column_map: Optional[dict[str, str]] = None) -> list[Verdict]:
    """Kayıt listesini tarar (IO'dan bağımsız; bu yüzden test edilebilir)."""
    mapping = column_map if column_map is not None else map_columns(list(rows[0].keys()) if rows else [])
    return [
        analyse_row(position, {field_name: str(raw.get(column, "") or "") for field_name, column in mapping.items()})
        for position, raw in enumerate(rows, start=1)
    ]


# ======================================================================================
# raporlama
# ======================================================================================
DISCLAIMER: Final[str] = (
    "Bu rapor bir **kaçınma** raporudur: hangi kaydın hangi dolandırıcılık desenini taşıdığını "
    "gösterir. Bir hedef listesi değildir; sıralama yalnızca **risk** eksenindedir. "
    "Düşük puan 'güvenli' demek değildir — yalnızca metinde bilinen bir risk desenine "
    "rastlanmadığı anlamına gelir."
)


def render_markdown(verdicts: Sequence[Verdict], *, source: str) -> str:
    """Gerekçeli Markdown raporu üretir (en riskliden en aza sıralı)."""
    ordered = sorted(verdicts, key=lambda item: (-item.score, item.name.lower()))
    counts: dict[str, int] = {}
    for item in ordered:
        counts[item.label] = counts.get(item.label, 0) + 1

    lines: list[str] = [f"# Dolandırıcılık Risk Tarama Raporu — {source}", "", DISCLAIMER, ""]
    lines.append(
        f"Taranan kayıt: **{len(ordered)}** · "
        + " · ".join(f"{label}: {counts.get(label, 0)}" for _, label in BANDS)
    )
    lines.append("")
    lines.append("## Özet")
    lines.append("")
    lines.append("| # | Kayıt | Risk Skoru | Etiket | Başlıca bulgu |")
    lines.append("|---|-------|-----------:|--------|----------------|")
    for item in ordered:
        top = item.findings[0].label if item.findings else "—"
        lines.append(f"| {item.index} | {item.name} | {item.score}/100 | {item.label} | {top} |")
    lines.append("")
    lines.append("## Gerekçeli bulgular")
    for item in ordered:
        lines.append("")
        lines.append(f"### {item.index}. {item.name} — **{item.label}** ({item.score}/100)")
        if item.link:
            lines.append(f"Bağlantı: `{item.link}`")
        if not item.findings:
            lines.append(
                "- Bu kayıtta bilinen bir dolandırıcılık deseni bulunmadı. Bu **güvenlik kanıtı "
                "değildir**; aşağıdaki doğrulama listesini uygulayın."
            )
        for finding in item.findings:
            lines.append(
                f"- **{finding.label}** (+{finding.weight} puan) — `{finding.field}` alanında "
                f"\"{finding.evidence}\" ifadesi"
            )
            lines.append(f"  - **Neden riskli:** {finding.reason}")
            lines.append(f"  - **Ne yapmalı:** {finding.advice}")
    lines.append("")
    lines.append("## Her kazanç iddiası için doğrulama listesi")
    lines.append("")
    lines.append("1. **Ödeme yönü:** Para sizden onlara mı gidiyor? Üyelik/aktivasyon/paket/VIP isteyen yerden kazanç değil, ürün satın almış olursunuz.")
    lines.append("2. **Tüzel kişilik:** Şirket unvanı, sicil, adres, resmî destek kanalı, kullanım şartları, KVKK/gizlilik metni.")
    lines.append("3. **Gelirin kaynağı:** 'Kim, neden, ne için ödüyor?' sorusunun net cevabı (anket müşterisi, reklam veren vb.).")
    lines.append("4. **Ödeme kanıtı:** Platformun kendi ekran görüntüsü değil, bağımsız kaynaklardan doğrulanabilir ödeme kayıtları.")
    lines.append("5. **Çekim şartları:** Eşik ve koşullar sabit mi? Davet/aktivite şartı ekleniyor mu? Değişiyorsa çıkın.")
    lines.append("6. **Aciliyet:** 'Son şans / süreli / kontenjan' baskısı inceleme süresini kısaltmak içindir; durun.")
    lines.append("7. **Kişisel veri:** TC kimlik, banka şifresi, SMS kodu, uygulama şifresi isteniyorsa kesinlikle vermeyin.")
    lines.append("")
    lines.append("> Şüpheli durumda: platformu kullanmayı bırakın, ödeme yaptıysanız bankanıza iade/chargeback başvurusu yapın ve")
    lines.append("> `https://www.siberay.gov.tr` üzerinden bildirimde bulunun; Telegram'da botu @notoscam/şikâyet kanallarına raporlayın.")
    lines.append("")
    return "\n".join(lines)


def print_summary(verdicts: Sequence[Verdict], *, source: str) -> None:
    """Terminal özeti: en yüksek riskli 5 + en düşük riskli 5 kayıt (uyarılarıyla)."""
    ordered = sorted(verdicts, key=lambda item: (-item.score, item.name.lower()))
    print("=" * 94)
    print(f"DOLANDIRICILIK RİSK TARAMASI — {source}")
    print("=" * 94)
    print("Sıralama yalnızca RİSK eksenindedir; 'kazanç potansiyeli' ölçülmez. Düşük puan = bilinen desen yok.")
    print("-" * 94)
    # "En riskli" listesi yalnızca gerçekten desen taşıyan kayıtları gösterir; bulgusuz kayıtlar
    # burada listelenirse başlıkla çelişirdi (onlar zaten alttaki düşük-risk bölümünde çıkar).
    risky = [item for item in ordered if item.findings][:5]
    print(f"EN YÜKSEK RİSKLİ {len(risky)} KAYIT" if risky else "EN YÜKSEK RİSKLİ KAYIT: yok")
    if not risky:
        print("  (taranan kayıtlarda bilinen dolandırıcılık deseni bulunamadı)")
    for item in risky:
        print(f"  [{item.label:>11}] {item.score:>3}/100  {item.name}")
        for finding in item.findings[:3]:
            print(f"                ↳ {finding.label} — \"{finding.evidence}\" ({finding.field})")
    print("-" * 94)
    # Uzun olmayan listelerde üst bölümde gösterilen kayıtlar tekrar edilmez (aynı kaydı hem
    # "en riskli" hem "en düşük riskli" diye göstermek okuru yanıltırdı).
    shown = {item.index for item in risky}
    lowest = [item for item in reversed(ordered) if item.index not in shown][:5]
    print(f"EN DÜŞÜK RİSKLİ {len(lowest)} KAYIT (düşük puan = risk deseni BULUNMADI; güvenlik kanıtı değil)")
    for item in lowest:
        print(f"  [{item.label:>11}] {item.score:>3}/100  {item.name}")
    if not lowest:
        print("  (liste, tüm kayıtlar yukarıda gösterildiği için boş)")
    print("-" * 94)
    counts: dict[str, int] = {}
    for item in ordered:
        counts[item.label] = counts.get(item.label, 0) + 1
    print("Toplam: " + ", ".join(f"{label}={counts.get(label, 0)}" for _, label in BANDS))
    print("=" * 94)


# ======================================================================================
# girdi/çıktı
# ======================================================================================
def read_rows(path: Path, sheet: Optional[str]) -> tuple[list[dict[str, Any]], str]:
    """Excel (.xlsx/.xlsm/.xls) veya CSV okur; sayfa verilmezse ilk sayfa kullanılır."""
    if path.suffix.lower() in {".xlsx", ".xlsm", ".xls"}:
        try:
            import pandas as pd  # yalnızca Excel için gerekli (ağır bağımlılık)
        except ModuleNotFoundError as exc:  # pragma: no cover - ortam bağımlı
            raise SystemExit("✖ Excel için pandas gerekli: python3 -m pip install pandas openpyxl") from exc
        frame = pd.read_excel(path, sheet_name=sheet if sheet else 0)
        return frame.to_dict(orient="records"), (sheet or "ilk sayfa")
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle)), "csv"
    raise SystemExit(f"✖ Desteklenmeyen dosya türü: {path.suffix} (xlsx/xlsm/xls/csv)")


def write_demo_sheet(path: Path) -> Path:
    """``--demo-sheet`` için sentetik tablo üretir (gerçek platform adı içermez)."""
    try:
        from openpyxl import Workbook
    except ModuleNotFoundError as exc:  # pragma: no cover - ortam bağımlı
        raise SystemExit("✖ openpyxl gerekli: python3 -m pip install openpyxl") from exc

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "🤖 Botlar"
    sheet.append(
        [
            "Bot / Mini-App Adı",
            "t.me / Link",
            "Kazanç Mekanizması",
            "Ödeme Yöntemi",
            "Min. Çekim / Eşik",
            "Tahmini Kazanç",
            "Risk",
            "Güven Notu / Uyarı",
        ]
    )
    rows: list[list[str]] = [
        ["PaketKazan", "t.me/ornek-paket", "Kazanma paketi al, günde %30 getiri", "USDT", "50 USDT",
         "%30 günlük", "Çok Yüksek", "Önce yatırım yapman gerekiyor; VIP paket şartı var"],
        ["VipKulup", "t.me/ornek-vip", "VIP üyelik ile sınırsız kazanç", "USDT", "100 USDT",
         "garanti kazanç", "Yüksek", "Aktivasyon için bakiye yükle, sonra çekim açılır"],
        ["TakimKur", "t.me/ornek-takim", "Takım kur, seviye atla, referans topla", "TRX", "100 TRX",
         "değişken", "Yüksek", "Kazanç yalnızca davet edilen kişilerden geliyor"],
        ["AnketOdak", "t.me/ornek-anket", "Anket doldurma; reklam veren müşteriler ödüyor", "PayPal",
         "5 USD", "aylık 20-40 USD", "Orta", "Ödeme kanıtı kullanıcı raporlarında paylaşılmış"],
        ["PTCİzle", "t.me/ornek-ptc", "Reklam izleme (PTC), günlük küçük ödemeler", "PayPal",
         "2 USD", "günde 0.5-1 USD", "Orta", "Kurumsal bilgi var, ödeme geçmişi uzun"],
        ["SessizBot", "t.me/ornek-sessiz", "", "kripto", "", "", "Belirsiz",
         "hiçbir şey yapmadan otomatik kazanç; admin dm ile iletişim"],
    ]
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    return path


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="risk_screen.py",
        description="Telegram kazanç botlarında dolandırıcılık deseni tarar; gerekçeli risk raporu üretir.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input", type=Path, default=Path(DEFAULT_INPUT),
                        help="Excel/CSV dosyası")
    parser.add_argument("--sheet", default=None, help="Excel sayfa adı (varsayılan: ilk sayfa)")
    parser.add_argument("--out-md", type=Path, default=Path("rapor.md"), help="Markdown rapor yolu")
    parser.add_argument("--out-json", type=Path, default=Path("rapor.json"), help="JSON çıktı yolu")
    parser.add_argument("--demo-sheet", type=Path, default=None, help="sentetik tablo yaz ve çık")
    parser.add_argument("--self-test", action="store_true", help="sentetik tabloyla kendini sına")
    args = parser.parse_args(argv)

    if args.demo_sheet:
        print(f"→ sentetik tablo yazıldı: {write_demo_sheet(Path(args.demo_sheet))}")
        return 0

    if args.self_test:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = write_demo_sheet(Path(tmp) / "demo.xlsx")
            rows, sheet_name = read_rows(path, "🤖 Botlar")
        verdicts = analyse_table(rows)
        print_summary(verdicts, source="sentetik tablo")
        dangerous = [item for item in verdicts if item.label in {DOKUNMA, TEMKINLI}]
        print(f"→ self-test: {len(verdicts)} kayıt tarandı, {len(dangerous)} yüksek riskli bulgu")
        return 1 if dangerous else 0

    if not args.input.exists():
        print(
            f"✖ dosya bulunamadı: {args.input}\n"
            f"  → tabloyu bu script ile aynı klasöre koyun ya da yolu verin: --input /yol/dosya.xlsx\n"
            f"  → elinizde dosya yoksa aracı denemek için: python3 risk_screen.py --demo-sheet demo.xlsx "
            f"&& python3 risk_screen.py --input demo.xlsx --sheet \"🤖 Botlar\"",
            file=sys.stderr,
        )
        return 2

    rows, sheet_name = read_rows(args.input, args.sheet)
    if not rows:
        print("✖ tablo boş", file=sys.stderr)
        return 2

    verdicts = analyse_table(rows)
    column_map = map_columns(list(rows[0].keys()))
    missing = [field_name for field_name in ("note", "mechanism") if field_name not in column_map]
    if missing:
        print(f"⚠ eşlenemeyen sütun(lar): {', '.join(missing)} — başlıkları kontrol edin", file=sys.stderr)

    print_summary(verdicts, source=f"{args.input.name} [{sheet_name}]")

    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.write_text(render_markdown(verdicts, source=args.input.name), encoding="utf-8")
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(
        json.dumps(
            {
                "source": str(args.input),
                "sheet": sheet_name,
                "columns": column_map,
                "scanned": len(verdicts),
                "results": [item.to_dict() for item in verdicts],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"→ rapor: {args.out_md} · {args.out_json}")

    dangerous = [item for item in verdicts if item.label in {DOKUNMA, TEMKINLI}]
    return 1 if dangerous else 0


if __name__ == "__main__":
    raise SystemExit(main())
