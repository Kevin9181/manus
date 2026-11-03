"""启动自检报告：采集主机/数据库概况并渲染成 Telegram HTML 消息。"""
import os
import shutil
import sqlite3


def collect_stats(db_path):
    """采集启动时的基本信息，任何一项失败都只跳过该字段。"""
    stats = {}

    try:
        memory = {}
        with open('/proc/meminfo', encoding='utf-8') as handle:
            for line in handle:
                key, _, value = line.partition(':')
                memory[key.strip()] = int(value.split()[0])
        total = memory.get('MemTotal', 0) // 1024
        available = memory.get('MemAvailable', memory.get('MemFree', 0)) // 1024
        if total:
            stats['mem_total'] = total
            stats['mem_used'] = total - available
    except Exception:
        pass

    try:
        usage = shutil.disk_usage('/')
        stats['disk_used_pct'] = usage.used / usage.total * 100
        stats['disk_free_gb'] = usage.free / 1024 ** 3
    except Exception:
        pass

    try:
        stats['load'] = os.getloadavg()
    except Exception:
        pass

    try:
        with sqlite3.connect(db_path, timeout=3) as conn:
            cursor = conn.cursor()
            stats['rules_total'] = cursor.execute('select count(*) from forward_rules').fetchone()[0]
            stats['rules_keyword'] = cursor.execute(
                'select count(*) from forward_rules where is_monitor_compat=1'
            ).fetchone()[0]
            stats['keywords'] = cursor.execute('select count(*) from keywords').fetchone()[0]
            stats['chats'] = cursor.execute('select count(*) from chats').fetchone()[0]
    except Exception:
        pass

    return stats


def render_report(now, version, bot_label, user_label, stats):
    """把采集到的数据渲染成一条 HTML 消息。"""
    lines = ['<b>🚀 服务已启动</b>', '']

    env_lines = [f"🕐 {now.strftime('%Y-%m-%d %H:%M:%S')}", f'📦 v{version}']
    if user_label:
        env_lines.append(f'👤 {user_label}')
    env_lines.append(f'🤖 {bot_label}')
    lines.append('<blockquote>' + '\n'.join(env_lines) + '</blockquote>')

    if 'rules_total' in stats:
        keyword_rules = stats['rules_keyword']
        full_rules = stats['rules_total'] - keyword_rules
        lines.append(
            '<blockquote>'
            f"📋 转发规则 {stats['rules_total']} 条（关键词 {keyword_rules} · 全量 {full_rules}）\n"
            f"🔑 关键词 {stats['keywords']} 个 · 💬 会话缓存 {stats['chats']} 个"
            '</blockquote>'
        )

    resource_lines = []
    if 'mem_total' in stats:
        resource_lines.append(f"💾 内存 {stats['mem_used']} / {stats['mem_total']} MB")
    if 'disk_used_pct' in stats:
        resource_lines.append(f"🗄 磁盘 {stats['disk_used_pct']:.0f}%（剩 {stats['disk_free_gb']:.1f} GB）")
    if 'load' in stats:
        load1, load5, load15 = stats['load']
        resource_lines.append(f'📈 负载 {load1:.2f} / {load5:.2f} / {load15:.2f}')
    if resource_lines:
        lines.append('<blockquote>' + '\n'.join(resource_lines) + '</blockquote>')

    tips = []
    if stats.get('disk_used_pct', 0) >= 85:
        tips.append('⚠️ 磁盘剩余空间不足，建议尽快清理')
    if stats.get('mem_total') and stats['mem_used'] / stats['mem_total'] >= 0.9:
        tips.append('⚠️ 内存吃紧，留意是否频繁换页')
    if stats.get('rules_total') == 0:
        tips.append('💡 还没有规则，用 /bind <来源> [目标] 建立第一条')
    if not tips:
        tips.append('✅ 启动自检通过，正在监听消息')
    lines.append('<blockquote>' + '\n'.join(tips) + '</blockquote>')

    lines.append('<b>常用命令</b>')
    lines.append('/settings 管理规则 · /list_rule 查看全部 · /show 查看配置 · /changelog 更新日志')
    lines.append('')
    lines.append('<i>基于 TelegramForwarder（GPL-3.0）与 Telegram Monitor 合并</i>')

    return '\n'.join(lines)
