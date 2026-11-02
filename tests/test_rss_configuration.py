"""Regression tests for RSS environment gating and optional rule configuration."""
import logging
import unittest
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock, patch

from utils.constants import parse_bool_env
import filters.rss_filter as rss_filter


class RSSBooleanParsingTests(unittest.TestCase):
    def test_common_true_values(self):
        for value in ("true", "TRUE", "1", " yes ", "on"):
            with self.subTest(value=value):
                self.assertTrue(parse_bool_env(value))

    def test_common_false_values(self):
        for value in ("false", "FALSE", "0", " no ", "off", ""):
            with self.subTest(value=value):
                self.assertFalse(parse_bool_env(value))

    def test_missing_value_uses_default(self):
        self.assertFalse(parse_bool_env(None))
        self.assertTrue(parse_bool_env(None, default=True))


class RSSFilterOptionalConfigTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_rss_skips_database_lookup(self):
        context = SimpleNamespace(should_forward=True)
        with TemporaryDirectory() as media_dir:
            with (
                patch.object(rss_filter, "RSS_MEDIA_DIR", media_dir),
                patch.object(rss_filter, "RSS_ENABLED", False),
                patch.object(rss_filter, "get_db_ops", new_callable=AsyncMock) as get_db_ops,
                patch.object(rss_filter, "get_session") as get_session,
                self.assertLogs("filters.rss_filter", level="DEBUG") as captured,
            ):
                result = await rss_filter.RSSFilter()._process(context)

        self.assertTrue(result)
        get_db_ops.assert_not_awaited()
        get_session.assert_not_called()
        disabled_records = [
            record for record in captured.records
            if "RSS未启用" in record.getMessage()
        ]
        self.assertEqual(len(disabled_records), 1)
        self.assertEqual(disabled_records[0].levelno, logging.DEBUG)
        self.assertFalse(any(record.levelno >= logging.INFO for record in captured.records))

    async def test_missing_rule_config_is_debug_skip_not_error(self):
        fake_db_ops = SimpleNamespace(get_rss_config=AsyncMock(return_value=None))
        fake_session = Mock()
        context = SimpleNamespace(should_forward=True, rule=SimpleNamespace(id=123))

        with TemporaryDirectory() as media_dir:
            with (
                patch.object(rss_filter, "RSS_MEDIA_DIR", media_dir),
                patch.object(rss_filter, "RSS_ENABLED", True),
                patch.object(rss_filter, "get_db_ops", new=AsyncMock(return_value=fake_db_ops)),
                patch.object(rss_filter, "get_session", return_value=fake_session),
                self.assertLogs("filters.rss_filter", level="DEBUG") as captured,
            ):
                result = await rss_filter.RSSFilter()._process(context)

        self.assertTrue(result)
        fake_db_ops.get_rss_config.assert_awaited_once_with(fake_session, 123)
        fake_session.close.assert_called_once()
        missing_config_records = [
            record for record in captured.records
            if "未配置 RSS" in record.getMessage()
        ]
        self.assertEqual(len(missing_config_records), 1)
        self.assertEqual(missing_config_records[0].levelno, logging.DEBUG)
        self.assertFalse(any(record.levelno >= logging.ERROR for record in captured.records))


if __name__ == "__main__":
    unittest.main()
