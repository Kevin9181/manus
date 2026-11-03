from telethon import events
import html
import json
from handlers.button.callback.callback_handlers import handle_callback
from handlers.command_handlers import *
from handlers.monitor_compat import handle_legacy_monitor_command
from handlers.link_handlers import handle_message_link
from telethon.tl.types import ChannelParticipantsAdmins
from dotenv import load_dotenv
from utils.common import *
from utils.media import *
from utils.startup_report import collect_stats, render_report
from datetime import datetime, timedelta
from version import VERSION

logger = logging.getLogger(__name__)

# 确保 temp 目录存在
os.makedirs(TEMP_DIR, exist_ok=True)

load_dotenv()


async def handle_monitor_admin_command(event, command, parts):
    """Manage the imported Telegram Monitor whitelist without editing .env."""
    project_root = os.path.dirname(os.path.dirname(__file__))
    whitelist_path = os.path.join(project_root, 'migration', 'monitor_compat_admins.json')
    try:
        with open(whitelist_path, 'r', encoding='utf-8') as file:
            admins = [int(value) for value in json.load(file)]
    except FileNotFoundError:
        admins = []
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        await event.reply(f'兼容白名单文件无效: {error}')
        return

    owner_id = admins[0] if admins else int(os.getenv('USER_ID', '0') or 0)
    if not owner_id or event.sender_id != owner_id:
        await event.reply('仅主管理员可以管理白名单')
        return
    if len(parts) < 2:
        await event.reply(f'用法: /{command} <用户ID>')
        return
    try:
        user_id = int(parts[1])
    except ValueError:
        await event.reply('用户ID必须为数字')
        return

    if command == 'allow':
        if user_id not in admins:
            admins.append(user_id)
            result = f'已允许用户 {user_id}'
        else:
            result = f'用户 {user_id} 已在白名单中'
    else:
        if user_id == owner_id:
            await event.reply('不能移除主管理员')
            return
        if user_id in admins:
            admins.remove(user_id)
            result = f'已移除用户 {user_id}'
        else:
            result = f'用户 {user_id} 不在导入白名单中'

    os.makedirs(os.path.dirname(whitelist_path), exist_ok=True)
    temp_path = whitelist_path + '.tmp'
    with open(temp_path, 'w', encoding='utf-8') as file:
        json.dump(admins, file, ensure_ascii=False, indent=2)
        file.write('\n')
    os.replace(temp_path, whitelist_path)
    try:
        os.chmod(whitelist_path, 0o600)
    except OSError:
        pass
    await event.reply(result)


async def handle_command(client, event):
    """处理机器人命令"""

    # 检查是否是管理员
    if not await is_admin(event):
        return
    
    # 处理命令逻辑
    message = event.message
    if not message.text:
        return
    
    chat = await event.get_chat()
    user_id = await get_user_id()
    chat_id = abs(chat.id)
    user_id = int(user_id)
    

    # 链接转发功能
    if not message.text.startswith('/') and chat_id == user_id:
        # 检查是否是 Telegram 消息链接且是用户自己的消息
        logger.info(f'进入链接转发功能：{message.text}')
        if 't.me/' in message.text:
            await handle_message_link(client, event)
        return
    if not message.text.startswith('/'):
        return
    
    logger.info(f'收到管理员命令: {event.message.text}')
    # 分割命令，处理可能带有机器人用户名的情况
    parts = message.text.split()
    command = parts[0].split('@')[0][1:]  # 移除开头的 '/' 并处理可能的 @username

    if command in {'show', 'addkw', 'delkw', 'addgroup', 'delgroup', 'addwatch', 'delwatch'}:
        handled = await handle_legacy_monitor_command(event, command, parts)
        if handled:
            return
        if command in {'addgroup', 'delgroup', 'addwatch', 'delwatch'}:
            await event.reply('这些是 Monitor 兼容命令。请先导入 Monitor 配置，或使用 /bind 管理普通 Forwarder 规则。')
            return

    # 命令处理器字典
    command_handlers = {
        'bind': lambda: handle_bind_command(event, client, parts),
        'b': lambda: handle_bind_command(event, client, parts),
        'settings': lambda: handle_settings_command(event, command, parts),
        's': lambda: handle_settings_command(event, command, parts),
        'switch': lambda: handle_switch_command(event),
        'sw': lambda: handle_switch_command(event),
        'add': lambda: handle_add_command(event, command, parts),
        'a': lambda: handle_add_command(event, command, parts),
        'addkw': lambda: handle_add_command(event, 'add', parts),
        'add_regex': lambda: handle_add_command(event, command, parts),
        'ar': lambda: handle_add_command(event, 'add_regex', parts),
        'replace': lambda: handle_replace_command(event, parts),
        'r': lambda: handle_replace_command(event, parts),
        'list_keyword': lambda: handle_list_keyword_command(event),
        'lk': lambda: handle_list_keyword_command(event),
        'list_replace': lambda: handle_list_replace_command(event),
        'lrp': lambda: handle_list_replace_command(event),
        'remove_keyword': lambda: handle_remove_command(event, command, parts),
        'rk': lambda: handle_remove_command(event, 'remove_keyword', parts),
        'delkw': lambda: handle_remove_command(event, 'remove_keyword', parts),
        'remove_keyword_by_id': lambda: handle_remove_command(event, command, parts),
        'rkbi': lambda: handle_remove_command(event, 'remove_keyword_by_id', parts),
        'remove_replace': lambda: handle_remove_command(event, command, parts),
        'rr': lambda: handle_remove_command(event, 'remove_replace', parts),
        'remove_all_keyword': lambda: handle_remove_all_keyword_command(event, command, parts),
        'rak': lambda: handle_remove_all_keyword_command(event, 'remove_all_keyword', parts),
        'clear_all': lambda: handle_clear_all_command(event),
        'ca': lambda: handle_clear_all_command(event),
        'start': lambda: handle_start_command(event),
        'help': lambda: handle_help_command(event,'help'),
        'h': lambda: handle_help_command(event,'help'),
        'export_keyword': lambda: handle_export_keyword_command(event, command),
        'ek': lambda: handle_export_keyword_command(event, command),
        'export_replace': lambda: handle_export_replace_command(event, client),
        'er': lambda: handle_export_replace_command(event, client),
        'add_all': lambda: handle_add_all_command(event, command, parts),
        'aa': lambda: handle_add_all_command(event, 'add_all', parts),
        'add_regex_all': lambda: handle_add_all_command(event, command, parts),
        'ara': lambda: handle_add_all_command(event, 'add_regex_all', parts),
        'replace_all': lambda: handle_replace_all_command(event, parts),
        'ra': lambda: handle_replace_all_command(event, parts),
        'import_keyword': lambda: handle_import_command(event, command),
        'ik': lambda: handle_import_command(event, 'import_keyword'),
        'import_regex_keyword': lambda: handle_import_command(event, command),
        'irk': lambda: handle_import_command(event, 'import_regex_keyword'),
        'import_replace': lambda: handle_import_command(event, command),
        'ir': lambda: handle_import_command(event, 'import_replace'),
        'ufb_bind': lambda: handle_ufb_bind_command(event, command),
        'ub': lambda: handle_ufb_bind_command(event, 'ufb_bind'),
        'ufb_unbind': lambda: handle_ufb_unbind_command(event, command),
        'uu': lambda: handle_ufb_unbind_command(event, 'ufb_unbind'),
        'ufb_item_change': lambda: handle_ufb_item_change_command(event, command),
        'uic': lambda: handle_ufb_item_change_command(event, 'ufb_item_change'),
        'clear_all_keywords': lambda: handle_clear_all_keywords_command(event, command),
        'cak': lambda: handle_clear_all_keywords_command(event, 'clear_all_keywords'),
        'clear_all_keywords_regex': lambda: handle_clear_all_keywords_regex_command(event, command),
        'cakr': lambda: handle_clear_all_keywords_regex_command(event, 'clear_all_keywords_regex'),
        'clear_all_replace': lambda: handle_clear_all_replace_command(event, command),
        'car': lambda: handle_clear_all_replace_command(event, 'clear_all_replace'),
        'copy_keywords': lambda: handle_copy_keywords_command(event, command),
        'ck': lambda: handle_copy_keywords_command(event, 'copy_keywords'),
        'copy_keywords_regex': lambda: handle_copy_keywords_regex_command(event, command),
        'ckr': lambda: handle_copy_keywords_regex_command(event, 'copy_keywords_regex'),
        'copy_replace': lambda: handle_copy_replace_command(event, command),
        'crp': lambda: handle_copy_replace_command(event, 'copy_replace'),
        'copy_rule': lambda: handle_copy_rule_command(event, command),
        'cr': lambda: handle_copy_rule_command(event, 'copy_rule'),
        'changelog': lambda: handle_changelog_command(event),
        'cl': lambda: handle_changelog_command(event),
        'list_rule': lambda: handle_list_rule_command(event, command, parts),
        'lr': lambda: handle_list_rule_command(event, command, parts),
        'show': lambda: handle_list_rule_command(event, 'list_rule', parts),
        'delete_rule': lambda: handle_delete_rule_command(event, command, parts),
        'dr': lambda: handle_delete_rule_command(event, command, parts),
        'delete_rss_user': lambda: handle_delete_rss_user_command(event, command, parts),
        'dru': lambda: handle_delete_rss_user_command(event, command, parts),
        'allow': lambda: handle_monitor_admin_command(event, 'allow', parts),
        'unallow': lambda: handle_monitor_admin_command(event, 'unallow', parts),
    }

    # 执行对应的命令处理器
    handler = command_handlers.get(command)
    if handler:
        await handler()



# 注册回调处理器
@events.register(events.CallbackQuery)
async def callback_handler(event):
    """回调处理器入口"""
    # 检查是否是管理员的回调
    if not await is_admin(event):
        return
    await handle_callback(event)


async def send_welcome_message(client, user_client=None):
    """发送启动自检通知：运行环境、规则概况、资源占用和常用命令。"""
    try:
        await _send_startup_report(client, user_client)
    except Exception as error:
        logger.error("发送启动通知失败: %s", error)


async def _send_startup_report(client, user_client=None):
    user_id = await get_user_id()
    db_path = os.getenv('DATABASE_URL', 'sqlite:///./db/forward.db').replace('sqlite:///', '', 1)
    stats = collect_stats(db_path)

    bot_label = '未知机器人'
    try:
        bot_me = await client.get_me()
        bot_label = bot_me.first_name or '机器人'
        if bot_me.username:
            bot_label += f' (@{bot_me.username})'
    except Exception:
        pass

    user_label = ''
    if user_client is not None:
        try:
            user_me = await user_client.get_me()
            user_label = user_me.first_name or ''
            if user_me.username:
                user_label += f' (@{user_me.username})'
        except Exception:
            pass

    text = render_report(
        datetime.now(),
        VERSION,
        html.escape(bot_label),
        html.escape(user_label),
        stats,
    )
    await client.send_message(user_id, text, parse_mode='html', link_preview=False)
    logger.info("已发送启动通知")
