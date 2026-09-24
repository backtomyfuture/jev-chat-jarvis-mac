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
            "risk": {"has_risk": False, "category": "无明显风险", "score": 2}
        }
        task_text = format_jev_card_text(task_judg)
        self.assertIn("要你: 派活待办", task_text)
        self.assertIn("时效: 明确截止", task_text)
        self.assertIn("建议: 确认范围并给节点", task_text)

        # 新版：情绪回应
        emo_judg = {
            "appeal": {"choice": "情绪回应"},
            "strategy": {"choice": "先共情再探问"},
            "risk": {"has_risk": False, "score": 2}
        }
        emo_text = format_jev_card_text(emo_judg)
        self.assertIn("需要: 情绪回应", emo_text)
        self.assertIn("建议: 先共情再探问", emo_text)

        # 新版：风险触发
        risk_judg = {
            "appeal": {"choice": "派活待办"},
            "strategy": {"choice": "核验授权按流程办"},
            "risk": {"has_risk": True, "category": "数据与隐私", "warning": "涉及客户数据与私发风险", "score": 8}
        }
        risk_text = format_jev_card_text(risk_judg)
        self.assertIn("⚠️ 涉及客户数据与私发风险", risk_text)
        self.assertIn("建议: 核验授权按流程办", risk_text)

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
            "risk": {"has_risk": False}
        }
        task_attr = self.hud._build_card_attr(task_judg)
        self.assertIn("要你: 派活待办", task_attr.string())
        self.assertIn("时效: 今日节点", task_attr.string())
        self.assertIn("建议: 确认范围并给节点", task_attr.string())

        risk_judg = {
            "risk": {"has_risk": True, "warning": "涉及绕过流程与权限违规"},
            "strategy": {"choice": "核验授权按流程办"}
        }
        risk_attr = self.hud._build_card_attr(risk_judg)
        self.assertIn("⚠️ 涉及绕过流程与权限违规", risk_attr.string())
        self.assertIn("建议: 核验授权按流程办", risk_attr.string())

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


if __name__ == '__main__':
    unittest.main()
