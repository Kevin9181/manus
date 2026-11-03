"""Compatibility commands for the legacy Telegram Monitor Bot Manager.

All changes are applied to the unified ForwardRule database; this module does
not start or maintain a second listener process.
"""
import asyncio
import html
import json
import logging
from pathlib import Path
from types import SimpleNamespace

from enums.enums import AddMode, ForwardMode
from models.models import Chat, ForwardRule, Keyword, get_session
from telethon.tl import types
from telethon.tl.types import PeerChannel
from telethon.utils import resolve_id
from utils.common import get_main_module

logger = logging.getLogger(__name__)


def _monitor_keyword_rules(session):
    return session.query(ForwardRule).filter(ForwardRule.is_monitor_compat.is_(True)).all()


def _monitor_channel_rules(session):
    return session.query(ForwardRule).filter(ForwardRule.is_monitor_full_channel.is_(True)).all()


def _chat_ids(rules, attribute):
    seen = []
    for rule in rules:
        chat = getattr(rule, attribute)
        if chat and chat.telegram_chat_id not in seen:
            seen.append(chat.telegram_chat_id)
    return seen


def _monitor_keywords(rules):
    values = []
    seen = set()
    for rule in rules:
        for keyword in rule.keywords:
            if not keyword.is_blacklist and keyword.keyword:
                key = (keyword.keyword, bool(keyword.is_regex))
                if key not in seen:
                    seen.add(key)
                    values.append(key)
    return values


def _ensure_chat(session, entity):
    telegram_chat_id = str(entity.id)
    chat = session.query(Chat).filter(Chat.telegram_chat_id == telegram_chat_id).first()
    if chat:
        if not chat.name:
            chat.name = _entity_name(entity)
        return chat
    chat = Chat(telegram_chat_id=telegram_chat_id, name=_entity_name(entity))
    session.add(chat)
    session.flush()
    return chat


def _entity_name(entity):
    return (
        getattr(entity, "title", None)
        or " ".join(filter(None, [getattr(entity, "first_name", None), getattr(entity, "last_name", None)]))
        or (f"@{entity.username}" if getattr(entity, "username", None) else None)
        or str(entity.id)
    )


async def _resolve_entity(value, allow_id_only=False):
    text = str(value or "").strip()
    if not text:
        raise ValueError("请提供聊天 ID、用户名或链接")

    main = await get_main_module()
    client = main.user_client
    candidates = [text]
    if text.lstrip("-").isdigit():
        candidates.insert(0, int(text))
    elif not text.startswith(("@", "https://", "http://", "t.me/")):
        candidates.insert(0, "@" + text)

    last_error = None
    for candidate in candidates:
        try:
            return await client.get_entity(candidate)
        except Exception as error:
            last_error = error

    if not text.lstrip("-").isdigit():
        try:
            exact_match = None
            partial_match = None
            async for dialog in client.iter_dialogs():
                name = (dialog.name or "").casefold()
                if name == text.casefold():
                    exact_match = dialog.entity
                    break
                if text.casefold() in name and partial_match is None:
                    partial_match = dialog.entity
            if exact_match or partial_match:
                return exact_match or partial_match
        except Exception as error:
            last_error = error

    if allow_id_only and text.lstrip("-").isdigit():
        try:
            peer_id, peer_type = resolve_id(int(text))
            if peer_type is PeerChannel:
                return SimpleNamespace(id=peer_id, title="Monitor channel (ID-only)", username=None)
            return SimpleNamespace(id=peer_id, title="Monitor chat (ID-only)", username=None)
        except Exception as error:
            last_error = error

    raise ValueError(f"无法解析聊天标识（账号须有权访问该聊天）: {last_error}")


def _create_keyword_rule(session, source_chat, target_chat, keywords):
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
    for value, is_regex in keywords:
        session.add(Keyword(
            rule_id=rule.id,
            keyword=value,
            is_regex=is_regex,
            is_blacklist=False,
        ))
    return rule


def _matching_pair(session, source_chat_id, target_chat_id):
    return session.query(ForwardRule).filter(
        ForwardRule.source_chat_id == source_chat_id,
        ForwardRule.target_chat_id == target_chat_id,
    ).first()


def _read_monitor_admins():
    root = Path(__file__).resolve().parents[1]
    path = root / "migration" / "monitor_compat_admins.json"
    try:
        return [int(value) for value in json.loads(path.read_text(encoding="utf-8"))]
    except FileNotFoundError:
        return []
    except (ValueError, TypeError, json.JSONDecodeError):
        logger.exception("Monitor 兼容白名单文件无效")
        return []


async def _resolve_chat_label(client, session, telegram_id):
    """把 Telegram ID 解析成 (名称, 链接, 是否解析到实体)。"""
    value = str(telegram_id).strip()
    name = None
    username = None
    entity = None
    is_user = None

    if client is not None:
        try:
            entity = await client.get_entity(int(value))
            username = getattr(entity, "username", None)
            is_user = isinstance(entity, types.User)
            name = (
                getattr(entity, "title", None)
                or " ".join(filter(None, [getattr(entity, "first_name", None), getattr(entity, "last_name", None)]))
                or username
            )
        except Exception:
            entity = None

    chat_row = session.query(Chat).filter(Chat.telegram_chat_id == value).first()
    if not name:
        name = chat_row.name if chat_row else None
    name = name or value

    # 解析不到实体时，chats 表里有的按群/频道处理，没有的按用户处理
    if is_user is None:
        is_user = chat_row is None

    internal = value[4:] if value.startswith("-100") else value.lstrip("-")
    if is_user:
        url = f"https://t.me/{username}" if username else f"tg://user?id={value}"
    else:
        url = f"https://t.me/{username}" if username else f"https://t.me/c/{internal}"

    return name, url, entity is not None


async def _format_chat_list(client, session, ids):
    """把一组 ID 渲染成带序号和超链的多行文本。"""
    if not ids:
        return "（无）"

    # 并发解析，20 个来源串行会有好几秒延迟
    results = await asyncio.gather(
        *[_resolve_chat_label(client, session, telegram_id) for telegram_id in ids],
        return_exceptions=True,
    )

    lines = []
    for index, (telegram_id, item) in enumerate(zip(ids, results), 1):
        if isinstance(item, Exception):
            name, url, found = str(telegram_id), "", False
        else:
            name, url, found = item
        label = html.escape(name, quote=False)
        if not found:
            # 解析不到实体的，附上原始 ID，避免同名条目分不清
            label = f"{label} · {html.escape(str(telegram_id), quote=False)}"
        if url:
            lines.append(f'{index}. <a href="{url}">{label}</a>')
        else:
            lines.append(f"{index}. {label}")
    return "\n".join(lines)


async def _show_monitor_config(event, keyword_rules, channel_rules, session):
    main = await get_main_module()
    client = getattr(main, "user_client", None) or event.client

    keywords = [value for value, is_regex in _monitor_keywords(keyword_rules) if not is_regex]
    keyword_text = "、".join(keywords) if keywords else "（无）"

    targets = await _format_chat_list(client, session, _chat_ids(keyword_rules, "target_chat"))
    sources = await _format_chat_list(client, session, _chat_ids(keyword_rules, "source_chat"))
    channel_sources = await _format_chat_list(client, session, _chat_ids(channel_rules, "source_chat"))
    channel_targets = await _format_chat_list(client, session, _chat_ids(channel_rules, "target_chat"))
    admins = await _format_chat_list(client, session, [str(value) for value in _read_monitor_admins()])

    text = (
        "📋 <b>当前配置</b>\n\n"
        f"🔑 <b>关键词</b>（{len(keywords)}）\n{keyword_text}\n\n"
        f"🎯 <b>转发目标</b>\n{targets}\n\n"
        f"👀 <b>关键词监听源</b>（{len(_chat_ids(keyword_rules, 'source_chat'))}）\n{sources}\n\n"
        f"📡 <b>全量频道来源</b>\n{channel_sources}\n\n"
        f"📨 <b>全量频道目标</b>\n{channel_targets}\n\n"
        f"👤 <b>白名单用户</b>\n{admins}"
    )
    await event.reply(text, parse_mode="html")


async def handle_legacy_monitor_command(event, command, parts):
    """Handle a legacy command. Return False when caller should use Forwarder semantics."""
    session = get_session()
    try:
        keyword_rules = _monitor_keyword_rules(session)
        channel_rules = _monitor_channel_rules(session)
        has_monitor_routes = bool(keyword_rules or channel_rules)

        if command == "show":
            if not has_monitor_routes:
                return False
            await _show_monitor_config(event, keyword_rules, channel_rules, session)
            return True

        if command in ("addkw", "delkw"):
            if not has_monitor_routes:
                return False
            if not keyword_rules:
                await event.reply("当前没有 Monitor 关键词路由；全量频道规则不使用关键词。")
                return True
            if len(parts) < 2:
                await event.reply(f"用法: /{command} <关键词>")
                return True
            value = parts[1]
            changed = 0
            for rule in keyword_rules:
                matches = [item for item in list(rule.keywords)
                           if not item.is_blacklist and not item.is_regex
                           and (item.keyword or "").casefold() == value.casefold()]
                if command == "addkw" and not matches:
                    session.add(Keyword(
                        rule_id=rule.id,
                        keyword=value,
                        is_regex=False,
                        is_blacklist=False,
                    ))
                    changed += 1
                elif command == "delkw" and matches:
                    for item in matches:
                        session.delete(item)
                    changed += 1
            session.commit()
            await event.reply(f"已处理关键词 {value}；影响 {changed} 条 Monitor 关键词路由。")
            return True

        if command not in ("addgroup", "delgroup", "addwatch", "delwatch"):
            return False

        if len(parts) < 2:
            await event.reply(f"用法: /{command} <聊天ID或用户名>")
            return True
        if not keyword_rules:
            await event.reply("当前没有 Monitor 关键词路由可管理；先导入 Monitor 配置，或使用 /bind 建立 Forwarder 规则。")
            return True

        value = parts[1]
        allow_id_only = command in ("addwatch", "delwatch", "delgroup")
        entity = await _resolve_entity(value, allow_id_only=allow_id_only)
        if command in ("addgroup", "addwatch"):
            chat = _ensure_chat(session, entity)
        else:
            chat = session.query(Chat).filter(Chat.telegram_chat_id == str(entity.id)).first()
            if not chat:
                await event.reply("未找到对应的 Monitor 路由；没有修改任何规则。")
                return True
        keywords = _monitor_keywords(keyword_rules)

        if command == "addgroup":
            source_ids = list(dict.fromkeys(rule.source_chat_id for rule in keyword_rules))
            created = 0
            skipped = 0
            for source_id in source_ids:
                existing = _matching_pair(session, source_id, chat.id)
                if existing:
                    skipped += 1
                    continue
                source_chat = session.query(Chat).get(source_id)
                _create_keyword_rule(session, source_chat, chat, keywords)
                created += 1
            session.commit()
            await event.reply(f"已添加目标；新增 {created} 条关键词路由，跳过已有配对 {skipped} 条。")
            return True

        if command == "delgroup":
            rules = [rule for rule in keyword_rules if rule.target_chat_id == chat.id]
            for rule in rules:
                for item in list(rule.keywords):
                    session.delete(item)
                session.delete(rule)
            session.commit()
            await event.reply(f"已删除该 Monitor 目标对应的 {len(rules)} 条关键词路由。")
            return True

        if command == "addwatch":
            target_ids = list(dict.fromkeys(rule.target_chat_id for rule in keyword_rules))
            created = 0
            skipped = 0
            for target_id in target_ids:
                existing = _matching_pair(session, chat.id, target_id)
                if existing:
                    skipped += 1
                    continue
                target_chat = session.query(Chat).get(target_id)
                _create_keyword_rule(session, chat, target_chat, keywords)
                created += 1
            session.commit()
            await event.reply(f"已添加监听源；新增 {created} 条关键词路由，跳过已有配对 {skipped} 条。")
            return True

        # delwatch: remove only keyword-based Monitor routes; full-channel rules remain.
        rules = [rule for rule in keyword_rules if rule.source_chat_id == chat.id]
        for rule in rules:
            for item in list(rule.keywords):
                session.delete(item)
            session.delete(rule)
        session.commit()
        await event.reply(f"已删除该 Monitor 监听源对应的 {len(rules)} 条关键词路由。")
        return True
    except Exception as error:
        session.rollback()
        logger.exception("执行 Monitor 兼容命令失败: %s", error)
        await event.reply("处理 Monitor 兼容命令失败，请检查账号聊天权限和服务日志。")
        return True
    finally:
        session.close()
