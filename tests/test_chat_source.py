"""Unit tests for the WeChat CLI Chat Intake module."""

import json
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from chat_source import WeChatCliAdapter, read_conversation_via_cli, ChatSession


class ChatSourceTests(unittest.TestCase):
    def setUp(self):
        self.adapter = WeChatCliAdapter(cli_path="/fake/wechat-cli")

    def test_message_parsing_formats(self):
        fake_json = {
            "chat": "张三",
            "is_group": False,
            "messages": [
                "[2026-09-23 15:30] 张三: 这个需求你跟一下",
                "[2026-09-23 15:31] me: 好的收到",
                "[2026-09-23 15:32] 张三: 明天给结果就行",
            ]
        }
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(fake_json))
            msgs, is_group = self.adapter.get_history("张三", limit=5)
            self.assertFalse(is_group)
            self.assertEqual(len(msgs), 3)

            # them
            self.assertEqual(msgs[0].side, "them")
            self.assertEqual(msgs[0].sender, "张三")
            self.assertEqual(msgs[0].text, "这个需求你跟一下")

            # me
            self.assertEqual(msgs[1].side, "me")
            self.assertIsNone(msgs[1].sender)
            self.assertEqual(msgs[1].text, "好的收到")

            # them
            self.assertEqual(msgs[2].side, "them")
            self.assertEqual(msgs[2].sender, "张三")
            self.assertEqual(msgs[2].text, "明天给结果就行")

    def test_get_sessions(self):
        fake_sessions = [
            {
                "chat": "张三",
                "username": "wxid_123",
                "is_group": False,
                "unread": 2,
                "last_message": "明天给结果就行",
                "msg_type": "文本",
                "time": "15:32",
            },
            {
                "chat": "技术交流群",
                "username": "12345@chatroom",
                "is_group": True,
                "unread": 0,
                "last_message": "欢迎新同学",
                "msg_type": "文本",
                "time": "15:20",
            }
        ]
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(fake_sessions))
            sessions = self.adapter.get_sessions(limit=5)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].chat, "张三")
            self.assertFalse(sessions[0].is_group)
            self.assertEqual(sessions[0].unread, 2)

    def test_get_sessions_filters_official_and_brand_accounts(self):
        fake_sessions = [
            {"chat": "优衣库", "username": "youyiku811016", "is_group": False, "is_official": True},
            {"chat": "中国南方航空", "username": "wxid_t5t60oy4syhv11", "is_group": False, "verify_flag": 24},
            {"chat": "招商银行信用卡", "username": "cmb4008205555", "is_group": False, "is_official": True},
            {"chat": "微信团队", "username": "weixin", "is_group": False},
            {"chat": "订阅号消息", "username": "gh_123456", "is_group": False},
            {"chat": "瑞幸幸运官", "username": "25984982063877680@openim", "is_group": False},
            {"chat": "企微通知", "username": "1727082517@qy_u", "is_group": False},
            {"chat": "老聊天人 李四", "username": "wxid_lisi", "is_group": False, "last_message": "好久不见"},
            {"chat": "老同学 王五", "username": "wxid_wangwu", "is_group": False, "last_message": "周末聚聚"},
        ]
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(fake_sessions))
            sessions = self.adapter.get_sessions(limit=10)
            self.assertEqual(len(sessions), 2)
            self.assertEqual(sessions[0].chat, "老聊天人 李四")
            self.assertEqual(sessions[1].chat, "老同学 王五")

    def test_get_sessions_fallback_on_cli_filter_unsupported(self):
        fake_sessions = [
            {"chat": "老朋友", "username": "wxid_friend", "is_group": False, "is_official": False},
            {"chat": "招行", "username": "cmb4008205555", "is_group": False},
        ]
        # First call with --filter-official fails (e.g. older CLI exit 1), second succeeds
        run_calls = [
            MagicMock(returncode=1, stdout="error: unknown option --filter-official"),
            MagicMock(returncode=0, stdout=json.dumps(fake_sessions)),
        ]
        with patch("subprocess.run", side_effect=run_calls) as mock_run:
            sessions = self.adapter.get_sessions(limit=10)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].chat, "老朋友")
            self.assertEqual(mock_run.call_count, 2)

    def test_read_conversation_via_cli_snapshot(self):
        fake_window = MagicMock(wid=101, pid=202, title="微信", x=100, y=100, w=800, h=600)
        with patch("chat_source.find_wechat_window", return_value=fake_window), \
             patch.object(self.adapter, "get_history") as mock_hist:
            from perception import Message
            mock_hist.return_value = ([
                Message(text="明天开会", side="them", y=0.1, conf=1.0, sender="李四")
            ], False)
            with patch("chat_source._GLOBAL_ADAPTER", self.adapter):
                res = read_conversation_via_cli(target_chat="李四")
                self.assertTrue(res["ok"])
                self.assertEqual(res["chat_title"], "李四")
                self.assertEqual(len(res["messages"]), 1)
                self.assertEqual(res["messages"][0].text, "明天开会")
                self.assertFalse(res["unchanged"])

                # Second call with same key -> unchanged
                res2 = read_conversation_via_cli(target_chat="李四", prev_key=res["snapshot_key"])
                self.assertTrue(res2["unchanged"])


    def test_get_latest_dict_messages(self):
        fake_latest = {
            "chat": "王五",
            "username": "wxid_wangwu",
            "is_group": False,
            "count": 3,
            "messages": [
                {
                    "local_id": 101,
                    "timestamp": 1789000000,
                    "time": "14:20:00",
                    "sender": "王五",
                    "text": "下午讨论一下方案",
                    "raw_type": 1,
                    "chat": "王五",
                    "username": "wxid_wangwu",
                    "is_group": False
                },
                {
                    "local_id": 102,
                    "timestamp": 1789000010,
                    "time": "14:20:10",
                    "sender": "七猫",
                    "text": "好的，我带电脑过去",
                    "raw_type": 1,
                    "chat": "王五",
                    "username": "wxid_wangwu",
                    "is_group": False
                }
            ]
        }
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(fake_latest))
            chat_title, msgs, is_group, snap_key = self.adapter.get_latest("王五", limit=5)
            self.assertEqual(chat_title, "王五")
            self.assertFalse(is_group)
            self.assertEqual(len(msgs), 2)

            # them (sender == chat)
            self.assertEqual(msgs[0].side, "them")
            self.assertEqual(msgs[0].sender, "王五")
            self.assertEqual(msgs[0].text, "下午讨论一下方案")

            # me (sender != chat in 1-on-1)
            self.assertEqual(msgs[1].side, "me")
            self.assertIsNone(msgs[1].sender)
            self.assertEqual(msgs[1].text, "好的，我带电脑过去")

            self.assertEqual(snap_key, ("王五", 102))


if __name__ == "__main__":
    unittest.main()
