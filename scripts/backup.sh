#!/usr/bin/env bash
# 备份运行数据：SQLite 数据库 + 登录会话 + .env
# 用法：APP_DIR=/opt/telegram-unified DEST=/root/backups ./scripts/backup.sh
set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
DEST="${DEST:-/root/backups}"
KEEP="${KEEP:-7}"

mkdir -p "$DEST"
chmod 700 "$DEST"
TS="$(date +%Y%m%d-%H%M%S)"

# 用 SQLite 在线备份 API，避免直接拷贝得到写了一半的数据库
if [ -f "$APP_DIR/db/forward.db" ]; then
  python3 -c 'import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close()' \
    "$APP_DIR/db/forward.db" "$DEST/forward-$TS.db"
fi

# 归档数据库、会话与凭据（.env 若存在一并归档）
if [ -f "$APP_DIR/.env" ]; then
  tar czf "$DEST/tf-$TS.tar.gz" -C "$APP_DIR" db sessions .env
else
  tar czf "$DEST/tf-$TS.tar.gz" -C "$APP_DIR" db sessions
fi

chmod 600 "$DEST"/tf-*.tar.gz "$DEST"/forward-*.db 2>/dev/null || true

# 只保留最近 KEEP 份
ls -1t "$DEST"/tf-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f

echo "backup ok: $DEST/tf-$TS.tar.gz"
