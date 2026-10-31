#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# One-command deployment for the Telegram Unified Forwarder on Ubuntu/Debian.
# Run from a checked-out project directory with: sudo ./deploy.sh

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$APP_DIR/.env"
COMPOSE=()

if [[ "$(id -u)" -ne 0 ]]; then
  echo "请用 root 权限运行：sudo $0" >&2
  exit 1
fi

if [[ ! -f "$APP_DIR/docker-compose.yml" || ! -f "$APP_DIR/Dockerfile" ]]; then
  echo "错误：请在 Telegram Unified Forwarder 项目目录中运行此脚本。" >&2
  exit 1
fi

if [[ -L "$ENV_FILE" ]]; then
  echo "错误：拒绝覆盖符号链接形式的 .env。" >&2
  exit 1
fi

if [[ ! -f /etc/os-release ]]; then
  echo "错误：无法识别 Linux 发行版；本脚本支持 Ubuntu/Debian。" >&2
  exit 1
fi
# shellcheck disable=SC1091
source /etc/os-release
OS_ID="${ID:-}"
if [[ "$OS_ID" != "ubuntu" && "$OS_ID" != "debian" ]]; then
  echo "错误：当前系统为 ${OS_ID:-unknown}；本脚本仅自动部署 Ubuntu/Debian。" >&2
  exit 1
fi

install_docker_if_needed() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    return
  fi

  echo "未检测到 Docker Engine 与 Docker Compose 插件。"
  echo "需要从 Docker 官方站点下载并运行安装脚本；这会安装/配置系统级 Docker 服务。"
  if [[ ! -t 0 ]]; then
    echo "请在交互式终端重新运行，以便确认 Docker 安装。" >&2
    exit 1
  fi
  read -r -p "继续安装 Docker？[y/N] " answer
  case "$answer" in
    y|Y|yes|YES) ;;
    *) echo "已取消；安装 Docker Engine 和 Compose 插件后重新运行即可。"; exit 1 ;;
  esac

  apt-get update
  apt-get install -y ca-certificates curl
  installer="$(mktemp)"
  trap 'rm -f "$installer"' EXIT
  curl -fsSL https://get.docker.com -o "$installer"
  sh "$installer"
  rm -f "$installer"
  trap - EXIT
}

install_docker_if_needed

if ! docker compose version >/dev/null 2>&1; then
  if command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
  else
    echo "错误：Docker 已存在但 Compose 不可用。请安装 Docker Compose 插件后重试。" >&2
    exit 1
  fi
else
  COMPOSE=(docker compose)
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl enable --now docker >/dev/null 2>&1 || true
fi
if ! docker info >/dev/null 2>&1; then
  echo "错误：Docker daemon 未就绪。请检查 systemctl status docker 后重试。" >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$APP_DIR/.env.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "已从 .env.example 创建受限权限的 .env。"
fi
chmod 600 "$ENV_FILE"

get_env_value() {
  local key="$1"
  sed -n "s/^${key}=//p" "$ENV_FILE" | head -n 1
}

set_env_value() {
  local key="$1" value="$2"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

prompt_value() {
  local key="$1" label="$2" pattern="$3" secret="${4:-false}" current value
  current="$(get_env_value "$key")"
  if [[ "$current" =~ $pattern ]]; then
    echo "$key 已配置，保留现有值。"
    return
  fi
  while true; do
    if [[ "$secret" == "true" ]]; then
      read -r -s -p "$label: " value
      printf '\n'
    else
      read -r -p "$label: " value
    fi
    if [[ "$value" =~ $pattern ]]; then
      set_env_value "$key" "$value"
      break
    fi
    echo "格式不正确，请重新输入。" >&2
  done
}

if [[ ! -t 0 ]]; then
  echo "错误：首次配置凭据需要交互式终端。" >&2
  exit 1
fi

echo "配置 Telegram 凭据（值只写入本机 .env，不会打印或提交到 Git）。"
prompt_value API_ID "Telegram API ID（数字）" '^[0-9]+$'
prompt_value API_HASH "Telegram API Hash" '^[A-Fa-f0-9]{32}$' true
prompt_value PHONE_NUMBER "Telegram 登录手机号（国际格式，如 +8613800000000）" '^\+[0-9]{6,20}$' true
prompt_value BOT_TOKEN "Telegram Bot Token" '^[0-9]+:[A-Za-z0-9_-]+$' true
prompt_value USER_ID "主管理员 Telegram 用户 ID（数字）" '^[0-9]+$'
admins="$(get_env_value ADMINS)"
if [[ -z "$admins" ]]; then
  read -r -p "额外管理员 ID（逗号分隔；留空仅使用 USER_ID）: " admins
  if [[ -n "$admins" ]]; then
    if [[ ! "$admins" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
      echo "错误：管理员 ID 应为数字，以英文逗号分隔。" >&2
      exit 1
    fi
    set_env_value ADMINS "$admins"
  fi
fi

chmod 600 "$ENV_FILE"
mkdir -p "$APP_DIR/db" "$APP_DIR/logs" "$APP_DIR/sessions" "$APP_DIR/temp" \
  "$APP_DIR/ufb/config" "$APP_DIR/config" "$APP_DIR/migration" \
  "$APP_DIR/rss/data" "$APP_DIR/rss/media"
chmod 700 "$APP_DIR/db" "$APP_DIR/sessions" "$APP_DIR/config" "$APP_DIR/migration"

cd "$APP_DIR"
echo "构建统一版镜像……"
"${COMPOSE[@]}" build

if [[ ! -s "$APP_DIR/sessions/user.session" || ! -s "$APP_DIR/sessions/bot.session" ]]; then
  echo
  echo "首次运行需要在 Telegram 完成用户账号验证码/二步验证登录。"
  echo "登录成功并看到客户端已启动后，在此终端按 Ctrl+C 返回部署脚本。"
  set +e
  "${COMPOSE[@]}" run --rm -it telegram-forwarder
  run_status=$?
  set -e
  if [[ "$run_status" -ne 0 && "$run_status" -ne 130 && "$run_status" -ne 143 ]]; then
    echo "首次登录进程退出码为 $run_status；请检查登录信息和日志后重新运行。" >&2
    exit "$run_status"
  fi
  if [[ ! -s "$APP_DIR/sessions/user.session" || ! -s "$APP_DIR/sessions/bot.session" ]]; then
    echo "尚未检测到完整的用户账号与 Bot 登录会话；登录未完成，请重新运行本脚本。" >&2
    exit 1
  fi
fi
find "$APP_DIR/sessions" -maxdepth 1 -type f -exec chmod 600 {} +

legacy_config=""
if [[ -f "$APP_DIR/config/monitor-config.json" ]]; then
  legacy_config="/app/config/monitor-config.json"
elif [[ -f "$APP_DIR/config/config.json" ]]; then
  legacy_config="/app/config/config.json"
fi
if [[ -n "$legacy_config" ]]; then
  echo
  echo "检测到旧版 Telegram Monitor 配置：$legacy_config"
  read -r -p "现在迁移监听源、目标、关键词和白名单？[y/N] " migrate_answer
  case "$migrate_answer" in
    y|Y|yes|YES)
      "${COMPOSE[@]}" run --rm -it telegram-forwarder python import_monitor_config.py "$legacy_config" --dry-run
      read -r -p "预览无误后执行导入？[y/N] " confirm_import
      case "$confirm_import" in
        y|Y|yes|YES) "${COMPOSE[@]}" run --rm -it telegram-forwarder python import_monitor_config.py "$legacy_config" ;;
        *) echo "已跳过配置导入；可稍后按 README 手动运行迁移命令。" ;;
      esac
      ;;
    *) echo "跳过旧配置导入。" ;;
  esac
fi

echo "启动后台服务……"
"${COMPOSE[@]}" up -d

echo
echo "部署完成。容器状态："
"${COMPOSE[@]}" ps
echo
echo "查看日志：cd '$APP_DIR' && ${COMPOSE[*]} logs -f telegram-forwarder"
echo "重启服务：cd '$APP_DIR' && ${COMPOSE[*]} restart"
echo "停止服务：cd '$APP_DIR' && ${COMPOSE[*]} down（不会删除数据卷/本地数据）"
