#!/usr/bin/env python3
"""``run_offerwall_min.py`` - minimal, tek dosyalık sürücü: WAFT'ın programatik API'si.

Bu dosya, "kendi yazdığım wrapper" taslağının **WAFT 1.0.0'ın gerçek API'siyle** düzeltilmiş
hâlidir; taslaktaki 8 gerçek hata giderilmiştir (hepsi `qa-kit/offerwall/RUNBOOK.md` §11'de
listeli). Üretimde tam sürüm olan `run_offerwall.py` kullanılır; bu dosya API'nin nasıl
kullanıldığını gösteren okunabilir bir sürücüdür ve **her parçası test edilmiş** kod yolunu
yeniden kullanır (hesap pinleme, kapsam kapısı, özet) - kopyalamaz.

Taslakta düzeltilen hatalar (özet):
  1. ``waft.data_source.load_data`` yok        → ``DataLoader(config).load()`` (DataFrame değil,
                                                 ``list[TargetRow]`` döner)
  2. ``waft.reporting.generate_summary`` yok   → ``Reporter`` / ``summarise_endpoints``
  3. ``Config(imap=…, imap_timeout=…, trace=…, artifacts=…)`` yanlış alan adları
                                               → ``imap_enabled``, ``imap_timeout_s``,
                                                 ``trace_mode``, ``artifacts_dir``
  4. ``Orchestrator(config, targets)``         → ``Orchestrator(config)``; satırlar
                                                 ``config.data_file``'dan yüklenir
  5. **Parolayı maskelemek parolayı yok ediyordu** (``acc["password"] = "***MASKED***"``
     sonra form doldurmada o değer kullanılıyordu) → maskeleme yalnızca **çıktı sınırında**
  6. ``form_data`` 6 alana kırpılıyordu (``country``/``terms`` düşüyordu) → satırın tüm
     form verisi korunur
  7. ``verification_email`` her satıra konuyordu → yalnızca ``email-verify`` satırlarında
  8. ``artifacts/endpoints.json`` okunuyordu   → gerçek yol ``artifacts/<run_id>/endpoints.json``
     ve yapı ``{"endpoints": [...]}`` (düz liste değil, alan adı ``statuses``)

Kapsam: bu sürücü de kapsam kapısını **zorlar** (``qa-kit/authorized_hosts.txt`` + sert 3. parti
block-list'i). Üçüncü taraf offerwall/mikro görev platformları reddedilir.

Kullanım::

    # yerel sandbox (varsayılan)
    python qa-kit/offerwall/run_offerwall_min.py

    # kendi staging'iniz (host'u qa-kit/authorized_hosts.txt içine ekleyin)
    python qa-kit/offerwall/run_offerwall_min.py \\
        --credentials qa-kit/offerwall/credentials.json \\
        --targets qa-kit/offerwall/targets_offerwall.xlsx \\
        --base-url https://staging.sirketiniz.com \\
        --imap-host imap.gmail.com --imap-port 993 --imap-ssl on \\
        --proxies proxies.txt --proxy-mode auto

Çıkış kodları: 0 pass | 1 hedef hatası | 2 kullanım/kapsam/kimlik hatası | 3 proxy | 130 Ctrl-C.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Final, Optional, Sequence

# --- repo + kardeş modülleri import edilebilir yap (kurulum gerekmez) -------------------
_HERE: Final[Path] = Path(__file__).resolve().parent
_REPO: Final[Path] = _HERE.parent.parent
for _path in (str(_REPO), str(_HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from account_pool import AccountPool, AccountPoolError  # noqa: E402
from run_offerwall import (  # noqa: E402 - test edilmiş parçaları yeniden kullan
    AccountPinnedOrchestrator,
    ScopeViolation,
    bind_rows_to_account,
    enforce_scope,
    load_scope,
)
from waft import Config, TargetRow  # noqa: E402  (üst düzey re-export)
from waft.data_source import DataLoader  # noqa: E402
from waft.utils import mask_secret  # noqa: E402

LOG = logging.getLogger("offerwall.min")

DEFAULT_SUBJECT_REGEX: Final[str] = "(doğrula|dogrula|verify|confirm|aktivasyon|activate)"


# ======================================================================================
# 1) hesap havuzu
# ======================================================================================
def describe_pool(pool: AccountPool) -> str:
    """Havuzu **maskeli** biçimde yazdır (gerçek parolalar asla log'a girmez)."""
    lines = [f"hesap havuzu: {len(pool)} hesap"]
    for index, account in enumerate(pool.accounts):
        lines.append(
            f"  [{index:02d}] {account.email:<38s} imap={account.imap_host}:{account.imap_port} "
            f"pw={mask_secret(account.password)}"
        )
    return "\n".join(lines)


# ======================================================================================
# 2) config + satırlar
# ======================================================================================
def build_config(args: argparse.Namespace, pool: AccountPool) -> Config:
    """Gerçek alan adlarıyla ``Config`` kur (WAFT 1.0.0 ``waft/config.py``)."""
    account = pool.for_context(0)
    config = Config(
        data_file=Path(args.targets),
        contexts=args.contexts,
        concurrency=args.concurrency,
        scenario="auto",
        # --- stealth -----------------------------------------------------------------
        stealth=True,
        verify_stealth=True,
        humanize=not args.no_humanize,
        # --- proxy -------------------------------------------------------------------
        proxy_file=Path(args.proxies) if args.proxies and args.proxy_mode != "off" else None,
        proxy_mode=args.proxy_mode,
        # --- ağ keşfi ----------------------------------------------------------------
        capture_har=True,
        log_network=True,
        # --- form / captcha ----------------------------------------------------------
        captcha_action="skip",
        selectors_file=Path(args.selectors) if Path(args.selectors).exists() else None,
        # --- IMAP doğrulama ----------------------------------------------------------
        imap_enabled=bool(args.imap_host or args.imap_ssl == "off"),
        imap_host=args.imap_host or account.imap_host,
        imap_port=int(args.imap_port or account.imap_port),
        imap_ssl=(args.imap_ssl != "off"),
        imap_username=args.imap_user or account.email,
        imap_password=args.imap_password or account.password,
        imap_subject_regex=args.imap_subject_regex,
        imap_timeout_s=float(args.imap_timeout),
        # --- çıktı / dayanıklılık ----------------------------------------------------
        artifacts_dir=Path(args.artifacts),
        trace_mode="on-failure",
        retries=args.retries,
        retry_only_retryable=False,
        rate_limit=args.rate_limit,
        save_storage_state=True,
        log_level=args.log_level,
    )
    config.validate()
    return config


def load_template_rows(config: Config) -> list[TargetRow]:
    """Şablon satırları Excel'den yükle (yer tutucular olduğu gibi kalır)."""
    rows = DataLoader(config).load()
    if not rows:
        raise SystemExit(f"✖ {config.data_file} hiç satır üretmedi")
    return rows


# ======================================================================================
# 3) koşu
# ======================================================================================
async def run(args: argparse.Namespace) -> int:
    try:
        pool = AccountPool.from_file(Path(args.credentials))
        pool.validate(require_enabled=True)
    except AccountPoolError as exc:
        print(f"✖ credential problem: {exc}", file=sys.stderr)
        return 2

    print(describe_pool(pool))
    for warning in pool.validate(require_enabled=True):
        print(f"⚠ {warning}")
    if not pool.enabled():
        print("✖ havuzda etkin hesap yok", file=sys.stderr)
        return 2

    try:
        config = build_config(args, pool)
        template_rows = load_template_rows(config)
    except Exception as exc:  # noqa: BLE001 - yapılandırma/veri hatası = kullanım hatası
        print(f"✖ configuration error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    # --- kapsam kapısı -----------------------------------------------------------------
    try:
        scope = load_scope(Path(args.scope), args.allow_host)
        blocked, unauthorized = enforce_scope(
            template_rows, scope, i_am_authorized=bool(args.i_am_authorized)
        )
    except ScopeViolation as exc:
        print(f"✖ scope: {exc}", file=sys.stderr)
        return 2
    print(
        f"→ scope: {len(scope.patterns)} pattern | blocked_third_party={blocked or 'none'} | "
        f"out_of_scope_override={unauthorized or 'none'}"
    )

    # --- her context'e KENDİ hesabı ----------------------------------------------------
    rows_by_context: list[list[TargetRow]] = []
    offset = 0
    for index in range(config.contexts):
        bound = bind_rows_to_account(template_rows, pool.for_context(index), iteration_offset=offset)
        offset += len(template_rows)
        rows_by_context.append(bound)

    total = sum(len(rows) for rows in rows_by_context)
    print(
        f"→ {config.contexts} context / {config.concurrency} paralel | "
        f"{len(template_rows)} şablon satırı × {len(pool)} hesap = {total} hedef koşu"
    )

    orchestrator = AccountPinnedOrchestrator(config, pool=pool, rows_by_context=rows_by_context)
    print(f"→ run id: {orchestrator.run_id} | artifacts: {config.artifacts_dir}")

    try:
        exit_code = int(await orchestrator.run())
    except KeyboardInterrupt:
        print("\n✖ kesildi - kısmi artefaktlar korundu", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        LOG.exception("orchestrator crashed: %s", exc)
        return 1

    print_endpoints(Path(args.artifacts), orchestrator.run_id)
    return exit_code


# ======================================================================================
# 4) keşfedilen API uçları (gerçek yol + gerçek yapı)
# ======================================================================================
def print_endpoints(artifacts_dir: Path, run_id: str) -> None:
    """``artifacts/<run_id>/endpoints.json`` oku ve terminale özetle.

    Dosya **düz liste değil**, ``{"endpoints": [...]}`` sözlüğüdür ve her uçta status listesi
    ``statuses`` alanındadır. Yanlış okuma "hiç endpoint yok" gibi görünür.
    """
    path = artifacts_dir / run_id / "endpoints.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"API endpoint özeti yok: {path} okunamadı ({exc})")
        return

    endpoints: list[dict[str, Any]] = list(payload.get("endpoints") or [])
    print("=" * 96)
    print(f"API endpoint özeti — {run_id} ({len(endpoints)} uç)")
    for item in endpoints:
        statuses = ",".join(str(code) for code in (item.get("statuses") or []))
        print(
            f"  {str(item.get('method')):6s} {item.get('host')}{item.get('path')} "
            f"[status {statuses or '?'}, {item.get('count')} kez]"
        )
    print(f"  dosya: {path}")
    print("=" * 96)


# ======================================================================================
# 5) CLI
# ======================================================================================
def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_offerwall_min.py",
        description="Minimal offerwall sürücüsü (WAFT programatik API) — yalnızca yetkili hedefler.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--credentials", type=Path, default=_HERE / "credentials.sandbox.json")
    parser.add_argument("--targets", type=Path, default=_HERE / "targets_offerwall.xlsx")
    parser.add_argument("--selectors", type=Path, default=_HERE / "selectors.resolved.json")
    parser.add_argument("--base-url", default=None, help="Tüm hedef URL'lerini bu host'a çevir.")
    parser.add_argument("--contexts", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--rate-limit", type=float, default=2.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--proxies", type=Path, default=_REPO / "proxies.txt")
    parser.add_argument("--proxy-mode", choices=["auto", "require", "off"], default="off")
    parser.add_argument("--scope", type=Path, default=_REPO / "qa-kit" / "authorized_hosts.txt")
    parser.add_argument("--allow-host", action="append", default=[])
    parser.add_argument("--i-am-authorized", action="store_true")
    parser.add_argument("--imap-host", default="127.0.0.1")
    parser.add_argument("--imap-port", type=int, default=1430)
    parser.add_argument("--imap-user", default=None)
    parser.add_argument("--imap-password", default=None)
    parser.add_argument("--imap-ssl", choices=["on", "off"], default="off")
    parser.add_argument("--imap-subject-regex", default=DEFAULT_SUBJECT_REGEX)
    parser.add_argument("--imap-timeout", type=float, default=30.0)
    parser.add_argument("--artifacts", type=Path, default=_REPO / "artifacts")
    parser.add_argument("--log-level", default="WARNING")
    parser.add_argument("--no-humanize", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.WARNING),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    print("┌────────────────────────────────────────────────────────────────────────────┐")
    print("│  OFFERWALL AKIŞI — hesap pinli yük & regresyon koşusu (minimal sürücü)      │")
    print("├────────────────────────────────────────────────────────────────────────────┤")
    print("│  [+] Headless + stealth + e-posta doğrulama + HAR/API keşfi                │")
    print("│  [+] Kapsam: yalnızca sizin/yetkili olduğunuz hedefler (kapı zorunlu)      │")
    print("└────────────────────────────────────────────────────────────────────────────┘")

    for required in (args.credentials, args.targets):
        if not Path(required).exists():
            print(f"✖ bulunamadı: {required}", file=sys.stderr)
            return 2

    rewritten = rewrite_base_url(args)
    if rewritten is not None:
        args.targets = rewritten

    return asyncio.run(run(args))


def rewrite_base_url(args: argparse.Namespace) -> Optional[Path]:
    """``--base-url`` verilmişse hedef URL'lerini o host'a çeviren bir kopya üret."""
    if not args.base_url:
        return None
    import pandas as pd

    base = str(args.base_url).rstrip("/")
    out = Path(args.artifacts) / "targets_offerwall.base_url.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)
    from urllib.parse import urlsplit

    sheets = pd.read_excel(args.targets, sheet_name=None)
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        for sheet_name, sheet in sheets.items():
            if "target_url" in sheet.columns:
                sheet = sheet.copy()
                sheet["target_url"] = [
                    base + (urlsplit(str(value)).path or "/")
                    if str(value).startswith("http")
                    else f"{base}/{value}"
                    for value in sheet["target_url"]
                ]
            sheet.to_excel(writer, sheet_name=sheet_name[:31], index=False)
    print(f"→ hedef URL'leri {base} adresine çevrildi → {out}")
    return out


if __name__ == "__main__":
    raise SystemExit(main())
