#!/usr/bin/env bash
# ======================================================================================
# sandbox_up.sh — tek komutla yerel offerwall sandbox'ını ayağa kaldır ve kiti koştur
#
# Ne yapar:
#   1. yerel posta sunucusunu başlatır (devmail: SMTP 1025 / IMAP 1430)   [gerekirse]
#   2. offerwall sandbox'ını başlatır (kayıt + anket + doğrulama + XHR API) [gerekirse]
#   3. portlar açılana kadar bekler (zaman aşımı kontrollü)
#   4. `run_offerwall.py --sandbox` ile 10 context / 5 paralel koşuyu çalıştırır
#   5. çıkışta yalnızca KENDİ başlattığı süreçleri kapatır (var olanlara dokunmaz)
#
# Kullanım:
#   bash qa-kit/offerwall/sandbox_up.sh              # başlat → koştur → temizle
#   bash qa-kit/offerwall/sandbox_up.sh --keep        # sunucuları açık bırak (artefakt incelemesi)
#   bash qa-kit/offerwall/sandbox_up.sh --no-color    # diğer bayraklar run_offerwall.py'ye geçer
#   CONTEXTS=4 CONCURRENCY=2 bash qa-kit/offerwall/sandbox_up.sh
#
# Çıkış kodları: run_offerwall.py'nin kodu aynen döner (0 PASSED | 1 fail | 2 usage |
#                3 proxy | 130 interrupt); 10 = ön koşul hatası (port/venv/betik yok).
# ======================================================================================
set -Eeuo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/../.." && pwd)"
PY="${PYTHON:-python3}"

WEB_PORT="${WEB_PORT:-8090}"
SMTP_PORT="${SMTP_PORT:-1025}"
IMAP_PORT="${IMAP_PORT:-1430}"
CONTEXTS="${CONTEXTS:-10}"
CONCURRENCY="${CONCURRENCY:-5}"

KEEP=0
PASSTHROUGH=()
# `--keep` bizim bayrağımız; geri kalan HER argüman run_offerwall.py'ye aynen geçirilir.
# (Önceki sürüm yalnızca "${@:2}" iletiyordu: `--no-color` gibi bir bayrak ilk sırada verilirse
#  sessizce düşüyordu — CI'da renk kodlarını kapatmak için bu tam olarak yapılıyor.)
for arg in "$@"; do
  if [[ "${arg}" == "--keep" ]]; then
    KEEP=1
  else
    PASSTHROUGH+=("${arg}")
  fi
done

LOGDIR="${ROOT}/artifacts/_sandbox_logs"
mkdir -p "${LOGDIR}"
STARTED_PIDS=()

log()  { printf '\033[1m[sandbox_up]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[sandbox_up] HATA:\033[0m %s\n' "$*" >&2; exit 10; }

port_open() {
  # Saf bash ile TCP yoklaması (netcat gerekmez).
  (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && exec 3>&- && return 0
  return 1
}

wait_port() {
  local port="$1" name="$2" tries=100
  for ((i = 0; i < tries; i++)); do
    if port_open "${port}"; then
      log "${name} hazır (127.0.0.1:${port})"
      return 0
    fi
    sleep 0.2
  done
  return 1
}

cleanup() {
  if [[ "${KEEP}" -eq 1 ]]; then
    log "sunucular açık bırakıldı (--keep). Kapatmak için: kill ${STARTED_PIDS[*]:-}"
    return 0
  fi
  for pid in "${STARTED_PIDS[@]:-}"; do
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
      log "kapatıldı (pid ${pid})"
    fi
  done
}
trap cleanup EXIT

[[ -f "${HERE}/run_offerwall.py" ]] || fail "run_offerwall.py bulunamadı (${HERE})"
[[ -f "${ROOT}/examples/offerwall_sandbox.py" ]] || fail "examples/offerwall_sandbox.py bulunamadı (${ROOT})"
[[ -f "${ROOT}/qa-kit/devmail.py" ]] || fail "qa-kit/devmail.py bulunamadı (${ROOT})"

# --- 1) posta sunucusu ----------------------------------------------------------------
if port_open "${IMAP_PORT}" && port_open "${SMTP_PORT}"; then
  log "posta sunucusu zaten dinliyor (SMTP ${SMTP_PORT} / IMAP ${IMAP_PORT}) — yeniden başlatılmadı"
else
  log "devmail başlatılıyor (SMTP ${SMTP_PORT} / IMAP ${IMAP_PORT}) → ${LOGDIR}/devmail.log"
  (cd "${ROOT}" && exec "${PY}" qa-kit/devmail.py --smtp-port "${SMTP_PORT}" --imap-port "${IMAP_PORT}") \
    >"${LOGDIR}/devmail.log" 2>&1 &
  STARTED_PIDS+=("$!")
  wait_port "${SMTP_PORT}" "devmail SMTP" || fail "devmail SMTP ${SMTP_PORT} açılmadı; ${LOGDIR}/devmail.log"
  wait_port "${IMAP_PORT}" "devmail IMAP" || fail "devmail IMAP ${IMAP_PORT} açılmadı; ${LOGDIR}/devmail.log"
fi

# --- 2) offerwall sandbox -------------------------------------------------------------
if port_open "${WEB_PORT}"; then
  log "offerwall sandbox zaten dinliyor (127.0.0.1:${WEB_PORT}) — yeniden başlatılmadı"
else
  log "offerwall sandbox başlatılıyor (http://127.0.0.1:${WEB_PORT}) → ${LOGDIR}/sandbox.log"
  (cd "${ROOT}" && exec "${PY}" examples/offerwall_sandbox.py \
      --port "${WEB_PORT}" --quiet --smtp-host 127.0.0.1 --smtp-port "${SMTP_PORT}" \
      --public-url "http://127.0.0.1:${WEB_PORT}") >"${LOGDIR}/sandbox.log" 2>&1 &
  STARTED_PIDS+=("$!")
  wait_port "${WEB_PORT}" "offerwall sandbox" || fail "sandbox ${WEB_PORT} açılmadı; ${LOGDIR}/sandbox.log"
fi

# --- 3) koşu --------------------------------------------------------------------------
log "koşu başlıyor: ${CONTEXTS} context / ${CONCURRENCY} paralel (run_offerwall.py --sandbox)"
set +e
"${PY}" "${HERE}/run_offerwall.py" --sandbox \
  --contexts "${CONTEXTS}" --concurrency "${CONCURRENCY}" "${PASSTHROUGH[@]:-}"
code=$?
set -e

log "run_offerwall.py çıkış kodu: ${code}"
exit "${code}"
