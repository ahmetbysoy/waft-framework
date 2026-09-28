#!/usr/bin/env python3
"""``ci_summary.py`` — GitHub Actions için koşu özeti (job summary) üretici.

CI'da işin sonunda çalışır: ``artifacts/`` altındaki **en yeni koşuyu** bulur, sayaçları ve
keşfedilen API uçlarını okur, markdown tablo üretir ve ``$GITHUB_STEP_SUMMARY`` dosyasına
yazar (yoksa stdout'a basar). Tarayıcı/ağ gerektirmez; hiçbir şeyi değiştirmez, yalnızca okur.

Neden ayrı dosya: workflow içine gömülü bash/python blokları CI'da test edilemez, bu ise
yerelde gerçek artefaktlarla birebir çalıştırılabilir (bu repoda çalıştırıldı).

Çıkış kodları: 0 özet üretildi · 1 artefakt bulunamadı (CI'da işi düşürmez; uyarı basar).
"""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path
from typing import Any, Final, Optional

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
ARTIFACTS: Final[Path] = ROOT / "artifacts"
SUMMARY_PATH_ENV: Final[str] = "GITHUB_STEP_SUMMARY"


def latest_run_dir(artifacts: Path) -> Optional[Path]:
    """Return the most recently modified ``artifacts/run-*`` directory."""
    candidates = [path for path in artifacts.glob("run-*") if path.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def read_results(run_dir: Path) -> list[dict[str, str]]:
    path = run_dir / "results.csv"
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except OSError:
        return []


def count_by(rows: list[dict[str, str]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = (row.get(key) or "-").strip() or "-"
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: -item[1]))


def build_summary() -> tuple[str, bool]:
    """Return ``(markdown, found_run)``."""
    run_dir = latest_run_dir(ARTIFACTS)
    if run_dir is None:
        return ("## WAFT CI özeti\n\n⚠ `artifacts/run-*` bulunamadı — koşu artefaktı üretilmemiş.\n", False)

    run = read_json(run_dir / "run.json")
    totals = run.get("totals") or {}
    endpoints = read_json(run_dir / "endpoints.json")
    rows = read_results(run_dir)

    ok = int(totals.get("targets_ok", 0) or 0)
    failed = int(totals.get("targets_failed", 0) or 0)
    planned = int(totals.get("targets_run", ok + failed) or (ok + failed))
    rate = f"{ok / planned * 100:.1f}%" if planned else "-"
    duration_s = f"{int(run.get('duration_ms', 0) or 0) / 1000:.1f} s"
    verifications = sum(1 for row in rows if str(row.get("verification_ok", "")).lower() == "true")
    blocked_captcha = count_by(rows, "error_type").get("CaptchaDetectedError", 0)

    lines: list[str] = [
        "## WAFT CI özeti",
        "",
        f"**Koşu:** `{run_dir.name}` · **durum:** {run.get('status', '?')} · **süre:** {duration_s}",
        "",
        "| Metrik | Değer |",
        "|---|---|",
        f"| Bağlam (context) | {totals.get('contexts_ok', 0)} ok / {totals.get('contexts_failed', 0)} fail |",
        f"| Hedef | {ok} ok / {failed} fail ({rate}) |",
        f"| Adım | {totals.get('steps_ok', 0)} ok / {totals.get('steps_failed', 0)} fail |",
        f"| E-posta doğrulama | {verifications} ok |",
        f"| CAPTCHA (politika=skip) | {blocked_captcha} hedef |",
        f"| Keşfedilen API ucu | {endpoints.get('count', len(endpoints.get('endpoints') or []))} |",
    ]

    endpoint_rows = endpoints.get("endpoints") or []
    if endpoint_rows:
        lines += ["", "| Metot | Host + yol | Status | Çağrı |", "|---|---|---|---|"]
        for item in endpoint_rows[:15]:
            statuses = ",".join(str(code) for code in (item.get("statuses") or [])) or "?"
            lines.append(
                f"| {item.get('method')} | `{item.get('host')}{item.get('path')}` | {statuses} | {item.get('count')} |"
            )

    errors = count_by([row for row in rows if row.get("status") != "ok"], "error_type")
    if errors:
        lines += ["", "**Başarısız hedeflerde hata tipleri:** " + ", ".join(f"`{name}`×{count}" for name, count in errors.items())]

    suites = sorted(path.name for path in ARTIFACTS.glob("run-*") if path.is_dir())
    if len(suites) > 1:
        lines += ["", f"_Bu iş klasöründe {len(suites)} koşu var; özet en yenisi ({run_dir.name}) için._"]

    return ("\n".join(lines) + "\n", True)


def main() -> int:
    markdown, found = build_summary()
    target = os.environ.get(SUMMARY_PATH_ENV)
    if target:
        try:
            with open(target, "a", encoding="utf-8") as handle:
                handle.write(markdown)
        except OSError as exc:  # pragma: no cover - CI filesystem edge case
            print(f"özet yazılamadı ({target}): {exc}", file=sys.stderr)
            print(markdown)
            return 0
        print(f"→ özet {target} dosyasına yazıldı")
    else:
        print(markdown)
    return 0 if found else 1


if __name__ == "__main__":
    raise SystemExit(main())
