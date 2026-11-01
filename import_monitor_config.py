#!/usr/bin/env python3
"""Import Telegram Monitor config.json into the unified Forwarder database.

Run from the project root after configuring .env. The importer resolves chat names
with the already-authorized Telethon user account and is safe to re-run.
"""
import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import PeerChannel
from telethon.utils import resolve_id

from enums.enums import AddMode, ForwardMode
from models.models import Chat, ForwardRule, Keyword, get_session, init_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("monitor_import")


def parse_args():
    parser = argparse.ArgumentParser(description="Import Telegram Monitor config.json into this unified app")
    parser.add_argument("config", help="Path to Telegram Monitor config.json")
    parser.add_argument("--dry-run", action="store_true", help="Resolve and report mappings without writing database/admin changes")
    parser.add_argument("--allow-unresolved-source-ids", action="store_true",
                        help="Import numeric channel sources by ID when the user session cannot resolve their entity; does not grant Telegram access")
    return parser.parse_args()


def _entity_label(entity):
    return (
        getattr(entity, "title", None)
        or " ".join(filter(None, [getattr(entity, "first_name", None), getattr(entity, "last_name", None)]))
        or (f"@{entity.username}" if getattr(entity, "username", None) else None)
        or str(entity.id)
    )


async def _resolve(client, value, allow_unresolved_source_ids=False):
    text = str(value).strip()
    if not text:
        raise ValueError("聊天标识不能为空")
    candidates = [text]
    if text.startswith("-") and text.lstrip("-").isdigit():
        candidates.insert(0, int(text))
    elif text.isdigit():
        candidates.insert(0, int(text))
    elif not text.startswith(("@", "https://", "http://", "t.me/")):
        candidates.insert(0, "@" + text)
    last_error = None
    for candidate in candidates:
        try:
            return await client.get_entity(candidate)
        except Exception as error:
            last_error = error
    if allow_unresolved_source_ids and text.lstrip("-").isdigit():
        try:
            peer_id, peer_type = resolve_id(int(text))
            if peer_type is PeerChannel:
                logger.warning("一个源频道仅按数字 ID 建立占位规则；运行账号仍须拥有该频道访问权限")
                return SimpleNamespace(
                    id=peer_id,
                    title="Monitor channel (ID-only)",
                    username=None,
                    is_id_only=True,
                )
        except Exception:
            pass
    raise ValueError(f"无法解析聊天 {value!r}: {last_error}")


def _get_or_create_chat(session, entity, telegram_chat_id=None):
    # Source chats use positive Telethon entity.id values for listener matching.
    # Full-channel targets may preserve a marked peer ID (for example, -100...) so
    # Telethon can resolve the correct channel when forwarding natively.
    chat_id = str(entity.id if telegram_chat_id is None else telegram_chat_id)
    chat = session.query(Chat).filter(Chat.telegram_chat_id == chat_id).first()
    if not chat:
        chat = Chat(telegram_chat_id=chat_id, name=_entity_label(entity))
        session.add(chat)
        session.flush()
    elif not chat.name:
        chat.name = _entity_label(entity)
    return chat


def _normalize_admins(values):
    admins = []
    for value in values or []:
        try:
            admin_id = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"whitelist 中有无效用户 ID: {value!r}")
        if admin_id not in admins:
            admins.append(admin_id)
    return admins


async def import_config(config_path, dry_run=False, allow_unresolved_source_ids=False):
    with open(config_path, "r", encoding="utf-8") as file:
        config = json.load(file)

    watch_ids = config.get("watch_ids") or []
    target_ids = config.get("target_ids") or []
    channel_watch_ids = config.get("channel_watch_ids") or []
    channel_target_ids = config.get("channel_target_ids") or []
    keywords = list(dict.fromkeys(str(item).strip() for item in config.get("keywords", []) if str(item).strip()))
    admins = _normalize_admins(config.get("whitelist", []))
    if watch_ids and not target_ids:
        raise ValueError("配置有 watch_ids，但没有 target_ids")
    if channel_watch_ids and not channel_target_ids:
        raise ValueError("配置有 channel_watch_ids，但没有 channel_target_ids")
    if not ((watch_ids and target_ids) or (channel_watch_ids and channel_target_ids)):
        raise ValueError("配置没有完整的来源/目标路由")

    load_dotenv()
    api_id = os.getenv("API_ID")
    api_hash = os.getenv("API_HASH")
    phone = os.getenv("PHONE_NUMBER")
    if not api_id or not api_hash:
        raise ValueError("请先在项目根目录 .env 中设置 API_ID 和 API_HASH")

    os.makedirs("./sessions", exist_ok=True)
    client = TelegramClient("./sessions/user", int(api_id), api_hash)
    await client.start(phone=phone or None)
    try:
        sources = [
            (value, await _resolve(client, value, allow_unresolved_source_ids))
            for value in watch_ids
        ]
        targets = [(value, await _resolve(client, value)) for value in target_ids]
        channel_sources = [
            (value, await _resolve(client, value, allow_unresolved_source_ids))
            for value in channel_watch_ids
        ]
        channel_targets = [(value, await _resolve(client, value)) for value in channel_target_ids]
        logger.info("关键词路由: %d 个来源 × %d 个目标 = %d 条规则；关键词 %d 个；白名单 %d 人",
                    len(sources), len(targets), len(sources) * len(targets), len(keywords), len(admins))
        logger.info("全量频道原生转发: %d 个来源 × %d 个目标 = %d 条规则",
                    len(channel_sources), len(channel_targets), len(channel_sources) * len(channel_targets))
        id_only_sources = sum(1 for _, source in sources + channel_sources if getattr(source, "is_id_only", False))
        if id_only_sources:
            logger.warning("其中 %d 个来源按数字 ID 建立占位记录；Telegram 不会因此授予账号访问权限", id_only_sources)
        if dry_run:
            return

        init_db()
        session = get_session()
        try:
            for _, source_entity in sources:
                source_chat = _get_or_create_chat(session, source_entity)
                for _, target_entity in targets:
                    target_chat = _get_or_create_chat(session, target_entity)
                    rule = session.query(ForwardRule).filter(
                        ForwardRule.source_chat_id == source_chat.id,
                        ForwardRule.target_chat_id == target_chat.id,
                    ).first()
                    if rule and not getattr(rule, "is_monitor_compat", False):
                        logger.warning("跳过已存在的非兼容规则 %s -> %s；为避免覆盖已有设置，请手动检查",
                                       source_chat.name, target_chat.name)
                        continue
                    if not rule:
                        rule = ForwardRule(
                            source_chat_id=source_chat.id,
                            target_chat_id=target_chat.id,
                            forward_mode=ForwardMode.WHITELIST,
                            add_mode=AddMode.WHITELIST,
                            use_bot=False,
                            enable_rule=True,
                            is_original_link=True,
                            is_original_sender=True,
                            is_original_time=True,
                            is_monitor_compat=True,
                        )
                        session.add(rule)
                        session.flush()
                    else:
                        rule.forward_mode = ForwardMode.WHITELIST
                        rule.add_mode = AddMode.WHITELIST
                        rule.use_bot = False
                        rule.enable_rule = True
                        rule.is_original_link = True
                        rule.is_original_sender = True
                        rule.is_original_time = True
                    existing = {item.keyword for item in rule.keywords if not item.is_blacklist}
                    for value in keywords:
                        if value not in existing:
                            session.add(Keyword(rule_id=rule.id, keyword=value,
                                                is_regex=False, is_blacklist=False))
                    if not keywords:
                        logger.warning("配置未设置关键词；与 Telegram Monitor 原行为一致，该规则不会命中转发消息: %s -> %s",
                                       source_chat.name, target_chat.name)

            for _, source_entity in channel_sources:
                source_chat = _get_or_create_chat(session, source_entity)
                for target_value, target_entity in channel_targets:
                    target_chat_id = str(target_value).strip()
                    if not (target_chat_id.startswith("-") and target_chat_id.lstrip("-").isdigit()):
                        target_chat_id = None
                    target_chat = _get_or_create_chat(
                        session, target_entity, telegram_chat_id=target_chat_id
                    )
                    rule = session.query(ForwardRule).filter(
                        ForwardRule.source_chat_id == source_chat.id,
                        ForwardRule.target_chat_id == target_chat.id,
                    ).first()
                    if rule:
                        logger.warning("跳过已存在的来源—目标规则；为避免覆盖现有设置，请手动检查")
                        continue
                    session.add(ForwardRule(
                        source_chat_id=source_chat.id,
                        target_chat_id=target_chat.id,
                        forward_mode=ForwardMode.BLACKLIST,
                        add_mode=AddMode.BLACKLIST,
                        use_bot=False,
                        enable_rule=True,
                        is_monitor_compat=False,
                        is_monitor_full_channel=True,
                    ))
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        if admins:
            admin_path = Path("migration/monitor_compat_admins.json")
            admin_path.parent.mkdir(parents=True, exist_ok=True)
            existing_admins = []
            if admin_path.exists():
                try:
                    existing_admins = _normalize_admins(json.loads(admin_path.read_text(encoding="utf-8")))
                except Exception as error:
                    raise ValueError(f"兼容管理员名单文件无效: {error}") from error
            merged = list(dict.fromkeys(existing_admins + admins))
            temp_path = admin_path.with_suffix(".json.tmp")
            temp_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temp_path.replace(admin_path)
            try:
                os.chmod(admin_path, 0o600)
            except OSError:
                pass
            logger.info("已导入 %d 个授权用户到 %s", len(admins), admin_path)
        logger.info("导入完成。请保持目标账号有权访问源聊天和目标聊天。")
    finally:
        await client.disconnect()


def main():
    args = parse_args()
    try:
        asyncio.run(import_config(args.config, args.dry_run, args.allow_unresolved_source_ids))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        logger.error("导入失败: %s", error)
        sys.exit(1)


if __name__ == "__main__":
    main()
