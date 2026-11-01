import html
import logging
import os
import re
from filters.base_filter import BaseFilter

logger = logging.getLogger(__name__)


def _escape_for_parse_mode(value, message_mode):
    """Escape metadata values so chat titles/usernames cannot break message formatting."""
    value = str(value or "")
    mode = getattr(message_mode, "value", message_mode)
    if str(mode).lower() == "html":
        return html.escape(value, quote=False)
    return re.sub(r"([_*\[\]()`])", r"\\\1", value)


def _message_link(event, chat):
    """Build a direct Telegram link for public and private chats."""
    username = getattr(chat, "username", None)
    if username:
        return f"https://t.me/{username}/{event.message.id}"

    chat_id = str(event.chat_id)
    if chat_id.startswith("-100"):
        internal_id = chat_id[4:]
    else:
        internal_id = chat_id.lstrip("-")
    return f"https://t.me/c/{internal_id}/{event.message.id}"


class InfoFilter(BaseFilter):
    """Add source links and sender/time metadata to the outgoing message."""

    async def _process(self, context):
        rule = context.rule
        event = context.event
        try:
            chat = getattr(event, "chat", None)
            if chat is None:
                try:
                    chat = await event.get_chat()
                except Exception:
                    chat = None

            if getattr(rule, "is_monitor_compat", False):
                mode = rule.message_mode
                source_name = (
                    getattr(chat, "title", None)
                    or getattr(chat, "first_name", None)
                    or getattr(getattr(rule, "source_chat", None), "name", None)
                    or str(event.chat_id)
                )
                if getattr(chat, "username", None):
                    source_name = f"@{chat.username} ({source_name})"
                source_name = _escape_for_parse_mode(source_name, mode)
                header_lines = [f"📢 消息来源: {source_name}"]

                sender_name = ""
                sender = getattr(event.message, "sender_chat", None) or getattr(event, "sender", None)
                if sender:
                    sender_name = (
                        getattr(sender, "title", None)
                        or " ".join(filter(None, [getattr(sender, "first_name", None), getattr(sender, "last_name", None)]))
                        or getattr(sender, "username", None)
                        or str(getattr(sender, "id", ""))
                    )
                    sender_name = _escape_for_parse_mode(sender_name, mode)
                    header_lines.append(f"👤 发送者: {sender_name}")
                context.sender_info = "\n".join(header_lines) + "\n──────────────────────────────\n"

                message_date = getattr(event.message, "date", None)
                if message_date:
                    try:
                        import pytz
                        timezone = pytz.timezone(os.getenv("DEFAULT_TIMEZONE", "Asia/Shanghai"))
                        formatted_time = message_date.astimezone(timezone).strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        formatted_time = message_date.strftime("%Y-%m-%d %H:%M:%S")
                    context.time_info = f"\n🕐 时间: {formatted_time}"

                message_text = getattr(event.message, "message", None) or ""
                matched = []
                for keyword in getattr(rule, "keywords", []) or []:
                    if getattr(keyword, "is_blacklist", True) or not getattr(keyword, "keyword", None):
                        continue
                    try:
                        is_match = bool(re.search(keyword.keyword, message_text, re.IGNORECASE)) if keyword.is_regex else keyword.keyword.casefold() in message_text.casefold()
                    except re.error:
                        is_match = False
                    if is_match and keyword.keyword not in matched:
                        matched.append(keyword.keyword)
                if matched:
                    rendered = _escape_for_parse_mode(", ".join(matched), mode)
                    context.time_info += f"\n🔑 匹配关键词: {rendered}"

                if getattr(rule, "is_original_link", True):
                    try:
                        link = _message_link(event, chat)
                        context.original_link = f"\n🔗 原始消息: {link}"
                    except Exception as error:
                        logger.warning("生成原始消息链接失败: %s", error)
                return True

            # Original TelegramForwarder behavior, with corrected public-channel URLs.
            if rule.is_original_link:
                try:
                    original_link = _message_link(event, chat)
                    if getattr(rule, "original_link_template", None):
                        link_info = rule.original_link_template.replace("{original_link}", original_link)
                        context.original_link = f"\n\n{link_info}"
                    else:
                        context.original_link = f"\n\n原始消息: {original_link}"
                except Exception as error:
                    logger.error("生成原始消息链接失败: %s", error)

            if rule.is_original_sender:
                try:
                    sender = getattr(event.message, "sender_chat", None) or getattr(event, "sender", None)
                    sender_name = "Unknown Sender"
                    sender_id = "Unknown"
                    if sender:
                        sender_name = (
                            getattr(sender, "title", None)
                            or " ".join(filter(None, [getattr(sender, "first_name", None), getattr(sender, "last_name", None)]))
                            or "Unknown Sender"
                        )
                        sender_id = getattr(sender, "id", "Unknown")
                    else:
                        peer = getattr(event.message, "peer_id", None)
                        if peer is not None and getattr(peer, "channel_id", None):
                            sender_id = peer.channel_id
                            try:
                                sender = await event.client.get_entity(peer)
                                sender_name = getattr(sender, "title", None) or "Unknown Channel"
                                sender_id = getattr(sender, "id", sender_id)
                            except Exception as peer_error:
                                logger.error("获取 peer_id 对应频道信息失败: %s", peer_error)
                                sender_name = "Unknown Channel"

                    if getattr(rule, "userinfo_template", None):
                        user_info = rule.userinfo_template.replace("{name}", str(sender_name)).replace("{id}", str(sender_id))
                        context.sender_info = f"{user_info}\n\n"
                    else:
                        context.sender_info = f"{sender_name}\n\n"
                except Exception as error:
                    logger.error("获取发送者信息出错: %s", error)

            if rule.is_original_time:
                try:
                    import pytz
                    timezone = pytz.timezone(os.getenv("DEFAULT_TIMEZONE", "Asia/Shanghai"))
                    local_time = event.message.date.astimezone(timezone)
                    formatted_time = local_time.strftime("%Y-%m-%d %H:%M:%S")
                    if getattr(rule, "time_template", None):
                        context.time_info = f"\n\n{rule.time_template.replace('{time}', formatted_time)}"
                    else:
                        context.time_info = f"\n\n{formatted_time}"
                except Exception as error:
                    logger.error("处理消息时间信息出错: %s", error)
            return True
        except Exception as error:
            logger.error("处理消息来源信息时出错: %s", error)
            return True
