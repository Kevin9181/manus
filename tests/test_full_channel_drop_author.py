import unittest
from types import SimpleNamespace
from unittest.mock import patch

import handlers.user_handler as user_handler


class FakeMessage:
    def __init__(self, message_id, grouped_id=None, text="content"):
        self.id = message_id
        self.grouped_id = grouped_id
        self.text = text


class FakeEvent:
    def __init__(self, message):
        self.message = message
        self.chat_id = -1005001


class FakeClient:
    def __init__(self, messages=()):
        self.messages = list(messages)
        self.forward_calls = []

    async def forward_messages(self, *args, **kwargs):
        self.forward_calls.append((args, kwargs))

    async def iter_messages(self, chat_id, **kwargs):
        for message in self.messages:
            yield message


class UserHandlerDropAuthorTests(unittest.IsolatedAsyncioTestCase):
    def make_rule(self, is_full_channel):
        return SimpleNamespace(
            id=1,
            enable_rule=True,
            is_filter_user_info=False,
            forward_mode=SimpleNamespace(value="BLACKLIST"),
            target_chat=SimpleNamespace(telegram_chat_id="-1006001", name="target"),
            is_monitor_full_channel=is_full_channel,
        )

    async def run_rule(self, rule, event, client):
        async def allow_all(*args, **kwargs):
            return True

        with patch.object(user_handler, "check_keywords", new=allow_all):
            await user_handler.process_forward_rule(client, event, str(event.chat_id), rule)

    async def test_full_channel_single_message_drops_author(self):
        client = FakeClient()
        event = FakeEvent(FakeMessage(21))

        await self.run_rule(self.make_rule(True), event, client)

        self.assertEqual(len(client.forward_calls), 1)
        self.assertTrue(client.forward_calls[0][1]["drop_author"])

    async def test_full_channel_media_group_drops_author(self):
        first = FakeMessage(30, grouped_id="album-1", text="caption")
        second = FakeMessage(31, grouped_id="album-1")
        client = FakeClient([second, first])
        event = FakeEvent(second)

        async def no_wait(_seconds):
            return None

        with patch.object(user_handler.asyncio, "sleep", new=no_wait):
            await self.run_rule(self.make_rule(True), event, client)

        self.assertEqual(len(client.forward_calls), 1)
        args, kwargs = client.forward_calls[0]
        self.assertEqual(args, (-1006001, [30, 31], -1005001))
        self.assertTrue(kwargs["drop_author"])

    async def test_non_full_channel_forward_keeps_default_attribution(self):
        client = FakeClient()
        event = FakeEvent(FakeMessage(41))

        await self.run_rule(self.make_rule(False), event, client)

        self.assertEqual(len(client.forward_calls), 1)
        self.assertFalse(client.forward_calls[0][1]["drop_author"])


if __name__ == "__main__":
    unittest.main()
