#!/usr/bin/env python3
"""``build_delivery.py`` - regenerate the executable parts of ``DELIVERY.md`` from the real files.

``DELIVERY.md`` embeds the kit's own sources (single source of truth) plus the commands that were
actually run. Editing that by hand drifts: the embedded ``run_offerwall.py`` went stale the moment
``--sandbox`` and the enforced scope gate landed. This builder makes the document reproducible.

It is **idempotent**: running it twice produces byte-identical output. Sections are located by
heading markers, so a partially-edited document is repaired rather than duplicated.

What it refreshes
-----------------
* ARTEFAKT 4 — the embedded ``run_offerwall.py`` block (between the heading and the next fence).
* §5.6 — one-command local run (``sandbox_up.sh`` / ``run_offerwall.py --sandbox``).
* §6 — the postback durability artefakt (embedded ``postback_receiver.py`` + measured results).

Usage::

    python3 qa-kit/offerwall/build_delivery.py --check   # do not write, report drift
    python3 qa-kit/offerwall/build_delivery.py           # rewrite DELIVERY.md

Exit codes: 0 unchanged or rewritten · 1 drift found in ``--check`` mode · 2 file/section missing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Final, Optional, Sequence

KIT: Final[Path] = Path(__file__).resolve().parent
DOC: Final[Path] = KIT / "DELIVERY.md"

ARTEFAKT1_HEADING: Final[str] = "## ARTEFAKT 1 —"
ARTEFAKT4_HEADING: Final[str] = "## ARTEFAKT 4 —"
S56_HEADING: Final[str] = "### 5.6 Tek komut"
SECTION6_HEADING: Final[str] = "## 6. EK ARTEFAKT"
SECTION6_END: Final[str] = "\n---\n\n## Doğrulanmış koşu"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def replace_embedded_source(document: str, heading: str, source: str, *, label: str, skip: int = 0) -> str:
    """Replace the ```python fenced block that follows *heading* (``skip`` blocks are ignored)."""
    start = document.find(heading)
    if start == -1:
        raise SystemExit(f"✖ heading not found: {heading}")
    fence_open = document.find("\n```python\n", start)
    for _ in range(skip):
        if fence_open == -1:
            raise SystemExit(f"✖ fewer than {skip + 1} python fences after {heading}")
        fence_open = document.find("\n```python\n", fence_open + 10)
    if fence_open == -1:
        raise SystemExit(f"✖ no ```python fence after {heading}")
    fence_close = document.find("\n```\n", fence_open + 10)
    if fence_close == -1:
        raise SystemExit(f"✖ unterminated ```python fence after {heading}")
    body_start = fence_open + len("\n```python\n")
    new_block = f"\n```python\n{source.strip()}\n```\n"
    print(f"  • {label}: {fence_close - body_start} → {len(source.strip())} karakter")
    return document[: fence_open] + new_block + document[fence_close + len("\n```\n") :]


def build() -> str:
    document = read(DOC)
    runner = read(KIT / "run_offerwall.py").strip()
    postback = read(KIT / "postback_receiver.py").strip()
    sh = read(KIT / "sandbox_up.sh").strip()

    account_pool = read(KIT / "account_pool.py").strip()
    # ARTEFAKT 1 embeds a short ``import json`` snippet first, then the whole account_pool.py.
    document = replace_embedded_source(
        document, ARTEFAKT1_HEADING, account_pool, label="ARTEFAKT 1 (account_pool.py)", skip=1
    )
    document = replace_embedded_source(document, ARTEFAKT4_HEADING, runner, label="ARTEFAKT 4 (run_offerwall.py)")

    s56 = f'''{S56_HEADING} — tek komutla yerel koşu (hesap dosyası gerekmez)

Senin "ATEŞLEME KOMUTU" adımının bu repodaki karşılığı. İki eşdeğer yol var:

```bash
# A) sunucuları da kendisi kaldırsın (SMTP 1025 / IMAP 1430 / web 8090)
bash qa-kit/offerwall/sandbox_up.sh

# B) sunucular zaten ayaktaysa doğrudan koşu
python qa-kit/offerwall/run_offerwall.py --sandbox
```

`--sandbox` şu varsayılanları uygular: `credentials.sandbox.json`, hedefler `http://127.0.0.1:8090`,
IMAP `127.0.0.1:1430` (SSL off), proxy `off`, IMAP timeout 30 s. **Açıkça verdiğin bayrak her zaman
kazanır** (ör. `--contexts 4 --concurrency 2`).

Gerçek koşu çıktısı (bu repoda):

```text
🔒 sandbox modu — yerel mock hedef: http://127.0.0.1:8090 | posta kutusu: 127.0.0.1:1430 (SSL off) | proxy: off
→ scope: 4 pattern(s) from qa-kit/authorized_hosts.txt | blocked_third_party=none | out_of_scope_override=none
  hedefler         : 60 koşu, 60 ok, 0 fail (%100.0)
  e-posta doğrulama: 20 ok / 0 fail
  blocked_captcha  : 0 hedef
→ exit code: 0 (PASSED)
```

Kurulum ve sorun giderme için tam adımlı sürüm: **`qa-kit/offerwall/RUNBOOK.md`**.

Tam `sandbox_up.sh` kaynağı:

```bash
{sh}
```
'''
    if S56_HEADING in document:
        start = document.find(S56_HEADING)
        end = document.find(SECTION6_HEADING, start)
        if end == -1:
            raise SystemExit("✖ §5.6 present but §6 heading missing")
        # keep the separator that precedes §6 intact
        sep = document.rfind("\n---\n\n", start, end)
        end = sep if sep != -1 else end
        document = document[:start] + s56 + document[end:]
        print("  • §5.6 güncellendi")
    else:
        anchor = "\n---\n\n" + SECTION6_HEADING
        if anchor not in document:
            raise SystemExit("✖ anchor for §5.6 insertion not found")
        document = document.replace(anchor, "\n---\n\n" + s56 + anchor, 1)
        print("  • §5.6 eklendi")

    section6 = f'''{SECTION6_HEADING} — `postback_receiver.py` (API endpoint dayanıklılığı)

İstediğin "API endpoint'lerinin dayanıklılığını test etme" maddesinin meşru karşılığı: **senin
kendi S2S postback alıcın**. Partner entegrasyonlarında dayanıklılık tam olarak burada sınanır —
imzalı dönüşüm çağrıları yük altında gelir; tekrar denemeler, replay ve sahte imza da beraberinde.
Kaynak: `qa-kit/offerwall/postback_receiver.py` (aşağıda tam gömülü).

Uyguladığı kurallar: HMAC-SHA256 imza (`uid|offer_id|status|payout|ts`, sabit zamanlı karşılaştırma)
→ **401**; ±300 s replay penceresi → **410**; `uid` başına tek kabul (idempotency) → **409**;
bozuk payload → **400**; JSONL denetim günlüğü; `/health` ve `/metrics` (sayaçlar + p50/p95/p99).

```python
{postback}
```

Gerçek koşu (bu repoda, `--workers 10`):

```bash
python qa-kit/offerwall/postback_receiver.py --selftest --workers 10 --requests 500
```

```text
  senaryo sonuçları
    OK  valid          n= 500  beklenen=202  gözlenen=[202]
    OK  duplicate      n=  20  beklenen=409  gözlenen=[409]
    OK  bad signature  n=  10  beklenen=401  gözlenen=[401]
    OK  stale ts       n=  10  beklenen=410  gözlenen=[410]
    OK  missing uid    n=   1  beklenen=400  gözlenen=[400]

✔ postback receiver durability gate PASSED — 500 kabul, 20 duplicate, 10 sahte imza,
  10 süresi geçmiş, 1 bozuk payload; 1107.0 rps, p50 8.58 ms, p95 11.36 ms, p99 12.66 ms
```

CI kapısı olarak kullanılabilir: konfigürasyon bozulduğunda (ör. `--replay-window 0.001`)
assertion'lar düşer ve **exit kodu 1** olur (test edildi); doğru konfigürasyonda **exit 0**.

Bu artefakt yazılırken bulunan ve düzeltilen iki gerçek kusur:

1. **Test tasarımı yarışı:** ilk sürümde aynı `uid` hem "geçerli" hem "duplicate" olarak aynı anda
   ateşleniyordu; hangisi önce varırsa kazanıyordu (sayaçlar doğruydu ama etiketler karışıyordu).
   Artık iki fazlı: önce tüm benzersiz dönüşümler, sonra saldırı/yeniden gönderim trafiği; sayaç
   doğrulaması warm-up sonrası alınan baseline'ın delta'sı üzerinden yapılıyor.
2. **Listen backlog:** `ThreadingHTTPServer`'ın varsayılan `request_queue_size=5` değeri 10 paralel
   göndericide kuyruğu taşırıyor, Linux SYN'i ~1 s sonra tekrar deniyordu → istemci tarafında
   p99 **1015 ms** ve 255 rps. `request_queue_size = 128` ile: **p99 12.7 ms**, **1107 rps**.

Gerçek bir partnerin/ağın postback göndermesini bekliyorsan:

```bash
POSTBACK_SECRET='paylasilan-anahtar' python qa-kit/offerwall/postback_receiver.py \\
  --serve --host 0.0.0.0 --port 8095 --audit artifacts/postbacks.jsonl
curl -s localhost:8095/health   | python -m json.tool
curl -s localhost:8095/metrics  | python -m json.tool
```

'''
    start = document.find(SECTION6_HEADING)
    end = document.find(SECTION6_END, start)
    if start == -1 or end == -1:
        raise SystemExit("✖ §6 heading or its end marker not found")
    document = document[:start] + section6 + document[end + 1 :]
    print("  • §6 güncellendi")

    return document


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="build_delivery.py", description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Report drift without writing.")
    args = parser.parse_args(argv)

    if not DOC.exists():
        print(f"✖ {DOC} not found", file=sys.stderr)
        return 2

    before = read(DOC)
    print(f"→ {DOC} ({len(before):,} byte) yeniden üretiliyor")
    after = build()

    if args.check:
        if before == after:
            print("✔ drift yok — doküman kaynaklarla uyumlu")
            return 0
        print("✖ drift var: `python3 qa-kit/offerwall/build_delivery.py` çalıştırın", file=sys.stderr)
        return 1

    if before == after:
        print(f"✔ değişiklik yok ({len(after):,} byte) — doküman zaten güncel")
        return 0
    DOC.write_text(after, encoding="utf-8")
    print(f"✔ yazıldı: {len(before):,} → {len(after):,} byte · {len(after.splitlines()):,} satır")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
