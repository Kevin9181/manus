#!/usr/bin/env bash
# 健康检查：内存 / 磁盘 / Swap / 容器状态，异常时通过 Telegram Bot 告警
# 用法：每 5 分钟由 cron 调用；--test 发送一条测试消息
set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENV_FILE="${ENV_FILE:-$APP_DIR/.env}"
CONTAINER="${CONTAINER:-telegram-forwarder}"
STATE="${STATE:-/tmp/tf-alert-state}"

MIN_AVAIL_MB="${MIN_AVAIL_MB:-100}"   # 可用内存低于此值告警
MAX_DISK_PCT="${MAX_DISK_PCT:-85}"    # 根分区使用率高于此值告警
MAX_SWAP_MB="${MAX_SWAP_MB:-400}"     # Swap 使用高于此值告警

BOT_TOKEN="$(grep -m1 '^BOT_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d ' \r')"
CHAT_ID="$(grep -m1 '^USER_ID=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d ' \r')"

AVAIL="$(free -m | awk '/^Mem:/{print $7}')"
DISK="$(df -P / | awk 'NR==2{gsub("%","",$5); print $5}')"
SWAP="$(free -m | awk '/^Swap:/{print $3}')"
HEALTH="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER" 2>/dev/null || true)"

MSG=""
add() { MSG="${MSG}$1"$'\n'; }

[ "${AVAIL:-999}" -lt "$MIN_AVAIL_MB" ] && add "⚠️ 内存可用仅 ${AVAIL}MB"
[ "${DISK:-0}" -gt "$MAX_DISK_PCT" ] && add "⚠️ 磁盘使用 ${DISK}%"
[ "${SWAP:-0}" -gt "$MAX_SWAP_MB" ] && add "⚠️ Swap 已用 ${SWAP}MB"
case "$HEALTH" in
  healthy|starting) ;;
  *) add "⚠️ 容器 ${CONTAINER} 状态: ${HEALTH:-不存在}" ;;
esac

send() {
  [ -n "$BOT_TOKEN" ] && [ -n "$CHAT_ID" ] || return 0
  curl -s -m 15 "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${CHAT_ID}" \
    --data-urlencode "text=$1" >/dev/null 2>&1 || true
}

if [ "${1:-}" = "--test" ]; then
  send "🖥 $(hostname) 告警测试
内存可用 ${AVAIL}MB / 磁盘 ${DISK}% / Swap ${SWAP}MB / 容器 ${HEALTH}
收到这条说明通知链路正常。"
  exit 0
fi

LAST="$(cat "$STATE" 2>/dev/null || true)"

if [ -n "$MSG" ]; then
  # 同一告警只推送一次，避免每 5 分钟刷屏
  if [ "$MSG" != "$LAST" ]; then
    send "🖥 $(hostname) 异常告警
${MSG}"
    printf '%s' "$MSG" > "$STATE"
  fi
elif [ -n "$LAST" ]; then
  send "✅ $(hostname) 已恢复正常"
  rm -f "$STATE"
fi
