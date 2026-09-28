#!/usr/bin/env bash
# ======================================================================================
# start_local_stack.sh — CI için yerel test yığınını kaldırır (ve --stop ile kapatır)
#
# Kaldırdığı servisler (hepsi yalnızca 127.0.0.1 üzerinde dinler):
#   • devmail      : SMTP 1025 / IMAP 1430   (qa-kit/devmail.py)
#   • demo site    : HTTP 8080                (examples/demo_site.py; SMTP'ye bağlı)
#
# Neden ayrı dosya: workflow içine gömülü bash blokları ne yerelde test edilebilir ne de kabuk
# denetiminden geçer. Bu script CI'da çağrılanın birebir aynısıdır ve actionlint'in kabuk
# denetimiyle doğrulanır. `A && B || C` kalıbından kaçınılır (SC2015: C dalı, A doğruyken de
# çalışabilir) — bu yüzden her kontrol açık `if` bloklarıyla yazılmıştır. Not: açıklama
# satırlarında denetleyicinin adını yazmak yanlışlıkla direktif sanılmasına yol açıyor.
#
# Kullanım:
#   bash .github/scripts/start_local_stack.sh          # başlat + hazır olana kadar bekle
#   bash .github/scripts/start_local_stack.sh --stop   # başlattıklarını kapat
#
# Çıkış kodları: 0 hazır · 1 port açılmadı (günlük kuyruğu basılır) · 2 kullanım hatası
# ======================================================================================
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="${PYTHON:-python3}"
LOGDIR="${ROOT}/artifacts/_nightly_logs"
PIDFILE="${LOGDIR}/pids"

SMTP_PORT="${SMTP_PORT:-1025}"
IMAP_PORT="${IMAP_PORT:-1430}"
WEB_PORT="${WEB_PORT:-8080}"

log()  { printf '[stack] %s\n' "$*"; }
fail() { printf '[stack] HATA: %s\n' "$*" >&2; exit 1; }

port_open() {
  # Saf bash TCP yoklaması (netcat gerekmez).
  (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

wait_port() {
  local port="$1" name="$2" log_file="$3"
  for _ in $(seq 1 60); do
    if port_open "${port}"; then
      log "${name} hazır (127.0.0.1:${port})"
      return 0
    fi
    sleep 0.25
  done
  log "✖ ${name} açılmadı (port ${port}); son günlük satırları:"
  tail -n 40 "${log_file}" >&2 2>/dev/null || true
  return 1
}

stop_stack() {
  if [[ ! -f "${PIDFILE}" ]]; then
    log "kapatılacak kayıt yok (${PIDFILE})"
    return 0
  fi
  while read -r pid; do
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
      log "kapatıldı (pid ${pid})"
    fi
  done < "${PIDFILE}"
  rm -f "${PIDFILE}"
}

if [[ "${1:-}" == "--stop" ]]; then
  stop_stack
  exit 0
fi

[[ -f "${ROOT}/qa-kit/devmail.py" ]] || fail "qa-kit/devmail.py bulunamadı (${ROOT})"
[[ -f "${ROOT}/examples/demo_site.py" ]] || fail "examples/demo_site.py bulunamadı (${ROOT})"

mkdir -p "${LOGDIR}"
: > "${PIDFILE}"

# --- 1) yerel posta sunucusu -----------------------------------------------------------
if port_open "${IMAP_PORT}"; then
  log "IMAP ${IMAP_PORT} zaten dinliyor — devmail yeniden başlatılmadı"
else
  log "devmail başlatılıyor (SMTP ${SMTP_PORT} / IMAP ${IMAP_PORT})"
  nohup "${PY}" "${ROOT}/qa-kit/devmail.py" --smtp-port "${SMTP_PORT}" --imap-port "${IMAP_PORT}" \
    >"${LOGDIR}/devmail.log" 2>&1 &
  echo "$!" >> "${PIDFILE}"
  wait_port "${IMAP_PORT}" "devmail IMAP" "${LOGDIR}/devmail.log"
fi

# --- 2) test edilecek uygulama ---------------------------------------------------------
if port_open "${WEB_PORT}"; then
  log "HTTP ${WEB_PORT} zaten dinliyor — demo site yeniden başlatılmadı"
else
  log "demo site başlatılıyor (http://127.0.0.1:${WEB_PORT})"
  nohup "${PY}" "${ROOT}/examples/demo_site.py" --port "${WEB_PORT}" --quiet \
    --smtp-host 127.0.0.1 --smtp-port "${SMTP_PORT}" \
    --public-url "http://127.0.0.1:${WEB_PORT}" \
    >"${LOGDIR}/demo_site.log" 2>&1 &
  echo "$!" >> "${PIDFILE}"
  wait_port "${WEB_PORT}" "demo site" "${LOGDIR}/demo_site.log"
fi

log "yığın hazır: IMAP ${IMAP_PORT} · HTTP ${WEB_PORT} · pmt kaydı ${PIDFILE}"
exit 0
