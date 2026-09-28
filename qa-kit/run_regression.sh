#!/usr/bin/env bash
# =============================================================================
#  qa-kit/run_regression.sh — terminal çalıştırma komutu (referans + çalışan script)
#
#  Kayıt + e-posta doğrulama akışının yük/regresyon koşusu:
#    10 izole context, 10 paralel, proxy listesi, IMAP doğrulama,
#    stealth + verify-stealth, HAR + ağ trafiği kaydı,
#    CAPTCHA görülürse SADECE o hedef "blocked/skipped" olur, koşu devam eder.
#
#  Kullanım:
#     chmod +x qa-kit/run_regression.sh
#     ./qa-kit/run_regression.sh                       # .env / IMAP_* ortam değişkenlerini kullanır
#     ./qa-kit/run_regression.sh --dry-run             # tarayıcı açmadan plan + pre-flight
#     ./qa-kit/run_regression.sh --no-probe-selectors  # selector probe'unu atla (hız)
#     BASE_URL=http://staging.sirketiniz.com ./qa-kit/run_regression.sh
#
#  Bu script kendi sisteminiz / yazılı izin aldığınız staging ortamı içindir.
#  Kapsam (scope) dosyası: qa-kit/authorized_hosts.txt
# =============================================================================
set -Eeuo pipefail

cd "$(dirname "$0")/.."                 # repo kökü
REPO_ROOT="$(pwd)"
KIT="qa-kit"

# ---------------------------------------------------------------------------
# 0) Ortam
# ---------------------------------------------------------------------------
export PYTHONUNBUFFERED=1
export WAFT_NO_COLOR="${WAFT_NO_COLOR:-1}"

# IMAP ayarları: .env varsa oradan, yoksa ortam değişkenlerinden okunur.
# Local Mailpit (docker compose -f qa-kit/docker-compose.mail.yml up -d):
#   IMAP_HOST=127.0.0.1 IMAP_PORT=1430 IMAP_USER=mailpit IMAP_PASSWORD=mailpit IMAP_SSL=false
IMAP_HOST="${IMAP_HOST:-}"
IMAP_PORT="${IMAP_PORT:-1430}"
IMAP_USER="${IMAP_USER:-}"
IMAP_PASSWORD="${IMAP_PASSWORD:-}"
MAILBOX="${MAILBOX:-INBOX}"
SUBJECT_REGEX="${SUBJECT_REGEX:-(doğrula|dogrula|verify|aktivasyon|activat)}"

CONTEXTS="${CONTEXTS:-10}"             # izole bağlam sayısı
CONCURRENCY="${CONCURRENCY:-10}"       # aynı anda koşan bağlam sayısı
DATA="${DATA:-$KIT/targets.xlsx}"      # veri kaynağı (Excel ya da JSON)
PROXIES="${PROXIES:-$REPO_ROOT/proxies.txt}"
BASE_URL="${BASE_URL:-}"               # verilirse tüm hedef URL'ler bu host'a çevrilir

echo "───────────────────────────────────────────────────────────────────────────"
echo " WAFT · kayıt + e-posta doğrulama regresyon koşusu"
echo " hedef veri : $DATA"
echo " bağlam     : $CONTEXTS (paralel: $CONCURRENCY)"
echo " proxy      : $PROXIES"
echo " IMAP       : ${IMAP_HOST:-<kapalı>}:$IMAP_PORT  user=${IMAP_USER:-<yok>}"
echo "───────────────────────────────────────────────────────────────────────────"

# ---------------------------------------------------------------------------
# 1) Pre-flight: bağımlılıklar + veri dosyası
# ---------------------------------------------------------------------------
python3 - <<'PY' || { echo "✖ pre-flight başarısız"; exit 2; }
import sys
try:
    import playwright, pandas  # noqa: F401
except ImportError as exc:
    sys.exit(f"eksik paket: {exc} — kurulum: pip install -r requirements.txt")
PY

if [[ ! -f "$DATA" ]]; then
  echo "✖ veri dosyası yok: $DATA"
  echo "  üretmek için: python qa-kit/make_targets.py --base-url http://127.0.0.1:8080 --rows 12"
  exit 2
fi

# Proxy dosyası yoksa uyar (koşu proxy'siz devam eder).
if [[ ! -f "$PROXIES" ]]; then
  echo "⚠ proxy listesi bulunamadı ($PROXIES) — koşu doğrudan bağlantı ile sürer"
  PROXY_ARGS=(--proxy-mode off)
else
  PROXY_ARGS=(--proxies "$PROXIES" --proxy-mode auto)
fi

# ---------------------------------------------------------------------------
# 2) Çalıştırma — wrapper (kapsam kontrolü + selector probe + manifest)
#
#    Doğrudan WAFT CLI'ı çalıştırmak isterseniz, aşağıdaki wrapper satırını
#    COMMENT'e alıp en alttaki `python -m waft …` komutunu kullanın.
# ---------------------------------------------------------------------------
EXTRA_ARGS=()
[[ -n "$BASE_URL" ]]            && EXTRA_ARGS+=(--base-url "$BASE_URL")
[[ "${DRY_RUN:-0}" == "1" ]]    && EXTRA_ARGS+=(--dry-run)
[[ "${NO_PROBE:-0}" == "1" ]]   && EXTRA_ARGS+=(--no-probe-selectors)

IMAP_ARGS=(--no-imap)
if [[ -n "$IMAP_HOST" ]]; then
  IMAP_ARGS=(
    --imap
    --imap-host "$IMAP_HOST"
    --imap-port "$IMAP_PORT"
    --imap-user "$IMAP_USER"
    --imap-password "$IMAP_PASSWORD"
    --imap-mailbox "$MAILBOX"
    --imap-subject-regex "$SUBJECT_REGEX"
  )
  # Yerel test posta sunucuları TLS konuşmaz:
  [[ "${IMAP_SSL:-false}" == "false" ]] && IMAP_ARGS+=(--imap-no-ssl)
fi

echo "→ wrapper başlıyor (exit kodu WAFT ile aynı: 0 başarılı, 1 hata, 2 kapsam/veri, 3 proxy)"
echo

python3 "$KIT/run_regression.py" \
  --data "$DATA" \
  --contexts "$CONTEXTS" \
  --concurrency "$CONCURRENCY" \
  "${PROXY_ARGS[@]}" \
  "${IMAP_ARGS[@]}" \
  --captcha-action skip \
  --stealth \
  --verify-stealth \
  --capture-har \
  --log-network \
  --trace on-failure \
  --devices random \
  --locale "tr-TR,en-US" \
  --timezone "Europe/Istanbul" \
  --rate-limit "${RATE_LIMIT:-5}" \
  --retries "${RETRIES:-1}" \
  --artifacts "$REPO_ROOT/artifacts" \
  --log-level "${LOG_LEVEL:-INFO}" \
  --no-color \
  "${EXTRA_ARGS[@]}"

STATUS=$?
echo
echo "───────────────────────────────────────────────────────────────────────────"
echo " bitti — çıkış kodu: $STATUS"
echo " raporlar : artifacts/<run-id>/summary.md · run.json · endpoints.json · junit.xml"
echo " kanıtlar : artifacts/<run-id>/contexts/<ctx>/{screenshots,traces,network.jsonl,*.har}"
echo " manifest : artifacts/run_manifest.json"
echo "───────────────────────────────────────────────────────────────────────────"
exit $STATUS

# =============================================================================
#  EŞDEĞER SAF CLI KOMUTU (wrapper olmadan, kopyala-çalıştır)
#
#  python -m waft \
#    --data qa-kit/targets.xlsx \
#    --contexts 10 --concurrency 10 \
#    --proxy-file proxies.txt --proxy-mode auto \
#    --selectors qa-kit/selectors.resolved.json \
#    --stealth --verify-stealth \
#    --capture-har --log-network \
#    --imap --imap-host 127.0.0.1 --imap-port 1430 --imap-user mailpit --imap-password mailpit \
#    --imap-subject-regex "(doğrula|dogrula|verify|aktivasyon|activat)" \
#    --captcha-action skip \
#    --trace on-failure \
#    --rate-limit 5 --retries 1 \
#    --artifacts artifacts --log-level INFO
#
#  Not: --selectors dosyası yoksa şu komutla üretin:
#    python qa-kit/selector_resolver.py --data qa-kit/targets.xlsx
# =============================================================================
