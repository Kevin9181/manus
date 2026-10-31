# Telegram Unified Forwarder

一个整合 **Telegram Monitor** 与 **TelegramForwarder** 能力的 Telegram 消息监控、过滤和分发程序。运行时只使用一套 Telethon 客户端、一个数据库和一条过滤/转发流水线，避免两个原项目同时监听导致重复转发。

## 合并后的能力

| 能力 | 整合结果 |
|---|---|
| 多个来源监听、关键词命中后转发 | 每个“来源 × 目标”建立独立规则；可同时配置多个来源和多个目标 |
| 关键词过滤、白名单/黑名单 | 使用同一套 Forwarder 过滤引擎；普通关键词、正则关键词、组合过滤模式均保留 |
| Monitor 消息摘要 | 导入的规则自动附加来源、发送者、时间、命中关键词和原消息直达链接 |
| 多目标与规则管理 | 合并到同一个规则数据库和 Bot 管理界面，可单独启停、调整每条规则 |
| 权限白名单 | 导入旧 Monitor `whitelist`；主管理员可用 `/allow`、`/unallow` 修改 |
| 消息处理 | 保留内容替换、媒体类型/大小/扩展名过滤、编辑/转发模式、延迟、评论区按钮等 |
| AI 与定时总结 | 保留内置 AI 服务商配置、消息处理和定时总结 |
| 多平台推送与 RSS | 保留 Apprise 推送、RSS 订阅和 RSS 管理页面 |
| 其他 | 保留链接转发、规则复制/同步、导入导出、UFB 集成等原 Forwarder 能力 |

两个项目重复的多源监听、关键词筛选、转发目标管理与 Bot 管理由同一套规则系统实现，不再并行保留两份监听服务。Monitor 的旧配置通过导入器迁移；其 Ubuntu/systemd 与 SSH 安装脚本不是运行时功能，本合并版统一使用 Docker Compose 或直接运行 Python 服务。

## 快速启动

需要 Python 3.11/Docker、Telegram API 凭据、一个 Bot Token，以及已加入源群组/频道的 Telegram 用户账号。仅导入和监听你有权访问的聊天。

1. 在 Telegram 官方 API 页面创建应用，获取 `API_ID` 与 `API_HASH`；使用 BotFather 创建机器人；通过 `@userinfobot` 获取自己的用户 ID。
2. 在项目目录复制并编辑环境文件：

   ```bash
   cp .env.example .env
   ```

   至少填写 `API_ID`、`API_HASH`、`PHONE_NUMBER`、`BOT_TOKEN`、`USER_ID`。`ADMINS` 可填写逗号分隔的管理员 ID；未设置时使用 `USER_ID`。
3. 构建并首次启动，按 Telethon 提示完成用户账号验证码/二步验证登录：

   ```bash
   docker compose build
   docker compose run --rm -it telegram-forwarder
   ```

   登录完成后按 `Ctrl+C` 退出。`sessions/` 会持久保存登录会话。
4. 启动后台服务：

   ```bash
   docker compose up -d
   docker compose logs -f telegram-forwarder
   ```

之后在 Bot 管理聊天中使用 `/bind <源聊天链接或名称> [目标聊天链接或名称]` 建立规则，再用 `/settings`、`/add`、`/add_regex` 配置规则。完整的 Forwarder 操作手册见 [`README-forwarder-reference.md`](README-forwarder-reference.md)。

## 从 Telegram Monitor 迁移配置

迁移前先准备旧项目的 `config.json`，确保 `.env` 中的用户账号已加入所有监听源和目标聊天。将旧配置放进合并项目 `config/` 目录（此目录默认被 Git 忽略，适合放运行配置）：

```bash
cp /path/to/telegram-monitor/config/config.json ./config/monitor-config.json
```

先试解析，再正式导入：

```bash
docker compose build
docker compose run --rm -it telegram-forwarder \
  python import_monitor_config.py /app/config/monitor-config.json --dry-run

docker compose run --rm -it telegram-forwarder \
  python import_monitor_config.py /app/config/monitor-config.json
```

导入器会解析旧配置的 `watch_ids`、`target_ids`、`keywords`、`whitelist`，为每个来源与目标组合创建规则，把关键词转为白名单匹配，并启用用户账号转发及 Monitor 来源摘要。旧配置的空关键词列表会按 Monitor 原行为导入为不命中任何消息的规则。导入是可重复运行的；已有的非兼容规则不会被覆盖。

如果使用 Python 直接运行而不是 Docker：先在项目根目录配置 `.env`、安装依赖、启动一次 `python main.py` 完成登录，然后运行：

```bash
python import_monitor_config.py /path/to/telegram-monitor/config/config.json --dry-run
python import_monitor_config.py /path/to/telegram-monitor/config/config.json
```

导入的白名单保存在 `migration/monitor_compat_admins.json`，已加入 `.gitignore`；该名单与 `.env` 的管理员列表取并集。迁移白名单中的第一位用户是 `/allow`、`/unallow` 的主管理员，不能被移除。Docker Compose 将 `migration/` 挂载到容器中以持久保存此名单。

## 旧命令与合并版命令

- `/addkw <词语>`、`/delkw <词语>` 分别作为 `/add`、`/remove_keyword` 的别名，作用于当前选中的规则。
- `/show`、`/list_rule` 查看转发规则；`/allow <用户ID>`、`/unallow <用户ID>` 管理导入的 Monitor 白名单。
- 旧版 `/addwatch`、`/delwatch`、`/addgroup`、`/delgroup` 没有明确的来源—目标配对上下文；迁移时自动转换为独立规则。迁移后通过 `/settings` 修改某个来源到某个目标的规则，新增配对用 `/bind <源> <目标>`。
- 原 Monitor 的简易关键字路由与 Forwarder 的规则过滤功能并存于同一条规则链，不会再有两套 Bot/监听进程互相覆盖配置。

## 直接运行

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 填好 .env 后
python main.py
```

## 文件与配置安全

不要提交 `.env`、Telegram session、运行数据库或包含账号 ID 的迁移配置。`config/config.json` 与 `migration/monitor_compat_admins.json` 已加入忽略规则。请定期备份 `db/`、`sessions/` 与必要的运行配置；备份 session 等同于保护账号凭据。

## 许可与来源

合并版以 GPL-3.0 发行，因为其核心包含 TelegramForwarder 的 GPL-3.0 代码；Telegram Monitor 原项目采用 MIT 许可，其许可文本随仓库保留为 [`LICENSE-MIT-telegram-monitor`](LICENSE-MIT-telegram-monitor)。仓库来源和归属说明见 [`NOTICE.md`](NOTICE.md)。
