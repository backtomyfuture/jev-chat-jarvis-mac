import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import AppKit
from perception import Message
from hud import HudController, format_jev_card_text


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.hud = HudController.alloc().init()

    def test_format_jev_card_text_pending_and_complete(self):
        pending_text = format_jev_card_text(None)
        self.assertIn("研判中", pending_text)

        # 兼容旧版 3 栏数据
        sample_judg = {
            "intent": {"choice": "催进度"},
            "emotion": {"choice": "焦急施压"},
            "strategy": {"choice": "给明确节点"}
        }
        card_text = format_jev_card_text(sample_judg)
        self.assertIn("诉求: 催进度", card_text)
        self.assertIn("态度: 焦急施压", card_text)
        self.assertIn("策略: 给明确节点", card_text)
        self.assertNotIn("\n", card_text)

        # 新版：要你 / 时效 / 建议
        task_judg = {
            "appeal": {"choice": "派活待办"},
            "urgency": {"choice": "明确截止"},
            "strategy": {"choice": "确认范围并给节点"},
        }
        task_text = format_jev_card_text(task_judg)
        self.assertIn("要你: 派活待办", task_text)
        self.assertIn("时效: 明确截止", task_text)
        self.assertIn("建议: 确认范围并给节点", task_text)

        # 新版：情绪回应
        emo_judg = {
            "appeal": {"choice": "情绪回应"},
            "strategy": {"choice": "先共情再探问"},
        }
        emo_text = format_jev_card_text(emo_judg)
        self.assertIn("需要: 情绪回应", emo_text)
        self.assertIn("建议: 先共情再探问", emo_text)

        # 旧结果里残留的风险字段不得盖住诉求/时效/建议
        leftover = {
            "appeal": {"choice": "派活待办"},
            "urgency": {"choice": "明确截止"},
            "strategy": {"choice": "确认范围并给节点"},
            "risk": {"has_risk": True, "category": "数据与隐私", "warning": "涉及客户数据与私发风险", "score": 8}
        }
        leftover_text = format_jev_card_text(leftover)
        self.assertIn("要你: 派活待办", leftover_text)
        self.assertNotIn("⚠️", leftover_text)
        self.assertNotIn("报警", leftover_text)

    def test_build_card_attr_styles(self):
        pending_attr = self.hud._build_card_attr(None)
        self.assertIn("研判中", pending_attr.string())

        err_attr = self.hud._build_card_attr({"error": "network timeout"})
        self.assertIn("研判异常", err_attr.string())

        sample_judg = {
            "intent": {"choice": "聊闲天"},
            "emotion": {"choice": "热情友好"},
            "strategy": {"choice": "顺势承接"}
        }
        attr = self.hud._build_card_attr(sample_judg)
        self.assertIn("诉求: 聊闲天", attr.string())
        self.assertIn("态度: 热情友好", attr.string())
        self.assertIn("策略: 顺势承接", attr.string())
        self.assertNotIn("\n", attr.string())

        # 新版属性测试
        task_judg = {
            "appeal": {"choice": "派活待办"},
            "urgency": {"choice": "今日节点"},
            "strategy": {"choice": "确认范围并给节点"},
        }
        task_attr = self.hud._build_card_attr(task_judg)
        self.assertIn("要你: 派活待办", task_attr.string())
        self.assertIn("时效: 今日节点", task_attr.string())
        self.assertIn("建议: 确认范围并给节点", task_attr.string())

        leftover = {
            "appeal": {"choice": "派活待办"},
            "urgency": {"choice": "今日节点"},
            "strategy": {"choice": "确认范围并给节点"},
            "risk": {"has_risk": True, "warning": "涉及绕过流程与权限违规"},
        }
        leftover_attr = self.hud._build_card_attr(leftover)
        self.assertIn("要你: 派活待办", leftover_attr.string())
        self.assertNotIn("⚠️", leftover_attr.string())

    def test_apply_timeline_messages_renders_bubbles(self):
        msgs = [
            Message(text="明天项目汇报准备好了吗？", side="them", sender="李主管", x=0.1, y=0.1, w=0.3, h=0.03, conf=0.98),
            Message(text="已经准备好了PPT，下班前发您邮箱。", side="me", sender="", x=0.6, y=0.2, w=0.3, h=0.03, conf=0.99),
        ]
        self.hud.applyTimelineMessages_(msgs)
        self.assertEqual(len(self.hud._chat_messages), 2)
        # Should render subviews in _chat_doc
        subviews = list(self.hud._chat_doc.subviews())
        self.assertGreater(len(subviews), 0)

    def test_apply_judgment_result_updates_card(self):
        msg_text = "明天项目汇报准备好了吗？"
        msgs = [Message(text=msg_text, side="them", sender="李主管", x=0.1, y=0.1, w=0.3, h=0.03, conf=0.98)]
        self.hud.applyTimelineMessages_(msgs)
        
        judg_res = {
            "subtext": {"choice": "有言外之意/试探", "probabilities": {"有言外之意/试探": 0.8}},
            "intent": {"choice": "催推进度", "probabilities": {"催推进度": 0.75}},
            "danger": {"score": 6},
            "action": {"choice": "给明确时间点/行动", "probabilities": {"给明确时间点/行动": 0.8}}
        }
        self.hud.applyJudgmentResult_((msg_text, judg_res))
        self.assertIn(msg_text, self.hud._chat_judgments)
        self.assertEqual(self.hud._chat_judgments[msg_text]["danger"]["score"], 6)

    def test_timeline_epoch_guard_discards_stale_result(self):
        """测试时间线异步结果必须有 epoch 守卫：旧会话的过期结果绝对不能写进新会话。"""
        self.hud._reply_epoch = 5
        old_epoch = 4
        old_text = "这是上一位联系人的私密消息"
        stale_res = {"appeal": {"choice": "派活待办"}, "strategy": {"choice": "确认范围"}}
        
        # 带有旧 epoch 的结果被送入
        self.hud.applyJudgmentResult_((old_epoch, old_text, stale_res))
        
        # 必须被安全拦截丢弃，不得写入 _chat_judgments
        self.assertNotIn(old_text, self.hud._chat_judgments)

        # 带有当前匹配 epoch 的结果可以正常写入
        curr_text = "这是当前会话的消息"
        curr_res = {"appeal": {"choice": "核对确认"}, "strategy": {"choice": "顺势承接"}}
        self.hud.applyJudgmentResult_((5, curr_text, curr_res))
        self.assertIn(curr_text, self.hud._chat_judgments)

    def test_foreground_hidden_clears_timeline_for_privacy(self):
        """测试微信离开前台时，清空时间线敏感气泡以保护聊天隐私。"""
        msgs = [Message(text="保密合同明天签署", side="them", sender="客户", x=0.1, y=0.1, w=0.3, h=0.03, conf=0.98)]
        self.hud.applyTimelineMessages_(msgs)
        self.assertEqual(len(self.hud._chat_messages), 1)

        # 微信离开前台
        self.hud.applyForegroundHidden_("微信不在前台")
        self.assertEqual(len(self.hud._chat_messages), 0)

    def test_multi_message_judgments_dispatched(self):
        """测试对进入时间线的每一条对方消息都发起研判，而不是只判最后一条。"""
        msgs = [
            Message(text="第一条：需求文档发我一下", side="them", sender="张总", x=0.1, y=0.1, w=0.3, h=0.03, conf=0.98),
            Message(text="我这就发", side="me", sender="", x=0.6, y=0.2, w=0.3, h=0.03, conf=0.99),
            Message(text="第二条：顺便把合同也过一遍", side="them", sender="张总", x=0.1, y=0.3, w=0.3, h=0.03, conf=0.98),
            Message(text="第三条：下午三点前必须给我", side="them", sender="张总", x=0.1, y=0.4, w=0.3, h=0.03, conf=0.98),
        ]
        self.hud.applyTimelineMessages_(msgs)
        self.assertEqual(len(self.hud._chat_messages), 4)

        # 验证所有 3 条对方消息都进入了研判系统（在 _chat_judgments 中存在键）
        self.assertIn("第一条：需求文档发我一下", self.hud._chat_judgments)
        self.assertIn("第二条：顺便把合同也过一遍", self.hud._chat_judgments)
        self.assertIn("第三条：下午三点前必须给我", self.hud._chat_judgments)

    def test_emoticon_and_image_quick_judgment(self):
        """测试表情包和图片消息能够瞬间产生标准研判建议，无需网络往返。"""
        msgs = [
            Message(text="[表情]", side="them", sender="朋友", x=0.1, y=0.1, w=0.3, h=0.03, conf=0.98),
            Message(text="[图片] (local_id=123)", side="them", sender="同事", x=0.1, y=0.2, w=0.3, h=0.03, conf=0.98),
        ]
        self.hud.applyTimelineMessages_(msgs)
        # 应该直接出完整结果，而不是 pending
        self.assertNotEqual(self.hud._chat_judgments["[表情]"].get("status"), "pending")
        self.assertIn("随性闲聊", self.hud._chat_judgments["[表情]"]["appeal"]["choice"])
        self.assertNotEqual(self.hud._chat_judgments["[图片] (local_id=123)"].get("status"), "pending")
        self.assertIn("核对确认", self.hud._chat_judgments["[图片] (local_id=123)"]["appeal"]["choice"])

    def test_history_paging_and_incremental_merge(self):
        """测试历史消息向上翻页加载（prepend）以及后续轮询增量合并（不冲刷历史）。"""
        self.hud._current_chat = "测试群"
        initial_msgs = [
            Message(text="消息10", side="them", sender="A", x=0.1, y=0.1, w=0.3, h=0.03, conf=1.0),
            Message(text="消息11", side="me", sender="", x=0.6, y=0.2, w=0.3, h=0.03, conf=1.0),
        ]
        self.hud.applyTimelineMessages_(initial_msgs)
        self.assertEqual(len(self.hud._chat_messages), 2)

        # 模拟向上翻页加载更早的历史消息（消息8, 消息9）
        older_history = [
            Message(text="消息8", side="them", sender="A", x=0.1, y=0.1, w=0.3, h=0.03, conf=1.0),
            Message(text="消息9", side="them", sender="B", x=0.1, y=0.2, w=0.3, h=0.03, conf=1.0),
        ]
        self.hud.applyOlderHistory_((self.hud._reply_epoch, "测试群", older_history, 4))
        # 历史记录应该拼在最前面
        self.assertEqual(len(self.hud._chat_messages), 4)
        self.assertEqual(self.hud._chat_messages[0].text, "消息8")
        self.assertEqual(self.hud._chat_messages[1].text, "消息9")
        self.assertEqual(self.hud._chat_messages[2].text, "消息10")
        self.assertEqual(self.hud._chat_messages[3].text, "消息11")

        # 模拟下一跳轮询（只抓到了最新的 消息11 和新到达的 消息12）
        poll_msgs = [
            Message(text="消息11", side="me", sender="", x=0.6, y=0.2, w=0.3, h=0.03, conf=1.0),
            Message(text="消息12", side="them", sender="A", x=0.1, y=0.3, w=0.3, h=0.03, conf=1.0),
        ]
        self.hud.applyTimelineMessages_(poll_msgs)
        # 翻出来的 消息8 和 消息9 绝不能被轮询冲掉，消息12 应该被追加到末尾
        self.assertEqual(len(self.hud._chat_messages), 5)
        self.assertEqual([m.text for m in self.hud._chat_messages],
                         ["消息8", "消息9", "消息10", "消息11", "消息12"])


if __name__ == '__main__':
    unittest.main()
