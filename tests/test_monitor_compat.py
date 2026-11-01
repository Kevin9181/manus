"""Run with `python tests/test_monitor_compat.py` after installing requirements."""
import asyncio
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from enums.enums import AddMode, ForwardMode
from models.models import Base, Chat, ForwardRule, Keyword
import handlers.monitor_compat as compat


engine = create_engine("sqlite://")
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
compat.get_session = lambda: Session()


class FakeUserClient:
    async def get_entity(self, value):
        if isinstance(value, int):
            return SimpleNamespace(id=abs(value), title=f"chat-{abs(value)}", username=None)
        raise ValueError("not found")

    async def iter_dialogs(self):
        if False:
            yield None


async def fake_get_main_module():
    return SimpleNamespace(user_client=FakeUserClient())


compat.get_main_module = fake_get_main_module


class FakeEvent:
    def __init__(self):
        self.replies = []

    async def reply(self, text):
        self.replies.append(text)


def seed():
    session = Session()
    try:
        sources = [
            Chat(telegram_chat_id="1101", name="source-1"),
            Chat(telegram_chat_id="1102", name="source-2"),
        ]
        target = Chat(telegram_chat_id="2001", name="target-1")
        full_source = Chat(telegram_chat_id="5001", name="channel-source")
        full_target = Chat(telegram_chat_id="-1006001", name="channel-target")
        session.add_all(sources + [target, full_source, full_target])
        session.flush()
        for source in sources:
            rule = ForwardRule(
                source_chat_id=source.id,
                target_chat_id=target.id,
                forward_mode=ForwardMode.WHITELIST,
                add_mode=AddMode.WHITELIST,
                use_bot=False,
                is_monitor_compat=True,
            )
            session.add(rule)
            session.flush()
            session.add(Keyword(rule_id=rule.id, keyword="oldkeyword", is_regex=False, is_blacklist=False))
        session.add(ForwardRule(
            source_chat_id=full_source.id,
            target_chat_id=full_target.id,
            forward_mode=ForwardMode.BLACKLIST,
            add_mode=AddMode.BLACKLIST,
            use_bot=False,
            is_monitor_full_channel=True,
        ))
        session.commit()
    finally:
        session.close()


async def main():
    seed()
    event = FakeEvent()
    assert await compat.handle_legacy_monitor_command(event, "show", ["/show"])
    assert "oldkeyword" in event.replies[-1]
    assert "5001" in event.replies[-1] and "-1006001" in event.replies[-1]

    await compat.handle_legacy_monitor_command(event, "addkw", ["/addkw", "newkeyword"])
    session = Session()
    try:
        assert session.query(Keyword).filter(Keyword.keyword == "newkeyword").count() == 2
    finally:
        session.close()

    await compat.handle_legacy_monitor_command(event, "delkw", ["/delkw", "oldkeyword"])
    session = Session()
    try:
        assert session.query(Keyword).filter(Keyword.keyword == "oldkeyword").count() == 0
    finally:
        session.close()

    await compat.handle_legacy_monitor_command(event, "addgroup", ["/addgroup", "3001"])
    session = Session()
    try:
        new_target = session.query(Chat).filter(Chat.telegram_chat_id == "3001").one()
        assert session.query(ForwardRule).filter(
            ForwardRule.target_chat_id == new_target.id,
            ForwardRule.is_monitor_compat.is_(True),
        ).count() == 2
    finally:
        session.close()

    await compat.handle_legacy_monitor_command(event, "addwatch", ["/addwatch", "4001"])
    session = Session()
    try:
        new_source = session.query(Chat).filter(Chat.telegram_chat_id == "4001").one()
        assert session.query(ForwardRule).filter(
            ForwardRule.source_chat_id == new_source.id,
            ForwardRule.is_monitor_compat.is_(True),
        ).count() == 2
    finally:
        session.close()

    await compat.handle_legacy_monitor_command(event, "delgroup", ["/delgroup", "3001"])
    session = Session()
    try:
        assert session.query(ForwardRule).join(Chat, ForwardRule.target_chat_id == Chat.id).filter(
            Chat.telegram_chat_id == "3001",
            ForwardRule.is_monitor_compat.is_(True),
        ).count() == 0
    finally:
        session.close()

    await compat.handle_legacy_monitor_command(event, "delwatch", ["/delwatch", "4001"])
    session = Session()
    try:
        assert session.query(ForwardRule).join(Chat, ForwardRule.source_chat_id == Chat.id).filter(
            Chat.telegram_chat_id == "4001",
            ForwardRule.is_monitor_compat.is_(True),
        ).count() == 0
    finally:
        session.close()

    print("Monitor compatibility regression: PASS")


if __name__ == "__main__":
    asyncio.run(main())
