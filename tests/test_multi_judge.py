import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from judge import (
    Judge,
    APPEALS,
    URGENCIES,
    STRATEGIES,
    extract_urgency_heuristics,
    extract_appeal_heuristics,
    format_judgment_summary,
)


class MultiJudgeTests(unittest.TestCase):
    def setUp(self):
        self.judge = Judge()

    def test_heuristics_urgency_and_appeal(self):
        u1 = extract_urgency_heuristics("客户明早十点要看版本，你今晚方便的话再把首页过一遍？")
        self.assertIn(u1, ("今日节点", "明确截止"))

        u2 = extract_urgency_heuristics("这个需求马上加急处理一下")
        self.assertEqual(u2, "即刻紧急")

        a1 = extract_appeal_heuristics("最近真的很难受，我是不是太矫情了。")
        self.assertEqual(a1, "情绪回应")

        a2 = extract_appeal_heuristics("顺手把这个需求文档补一下")
        self.assertEqual(a2, "派活待办")

    @patch.object(Judge, '_post')
    def test_multi_judge_task_scenario(self, mock_post):
        mock_post.return_value = {
            "answers": {
                "appeal": {"choice": "派活待办", "confidence": 0.9},
                "urgency": {"choice": "今日节点", "confidence": 0.85},
                "strategy": {"choice": "确认范围并给节点", "confidence": 0.88},
            }
        }
        res = self.judge.multi_judge("客户明早十点要看版本，你今晚方便的话再把首页过一遍？")
        self.assertEqual(res["appeal"]["choice"], "派活待办")
        self.assertEqual(res["urgency"]["choice"], "今日节点")
        self.assertEqual(res["strategy"]["choice"], "确认范围并给节点")
        self.assertNotIn("risk", res)
        self.assertNotIn("danger", res)
        # 验证单行摘要
        summary = res["summary"]
        self.assertIn("要你: 派活待办", summary)
        self.assertIn("时效: 今日节点", summary)
        self.assertIn("建议: 确认范围并给节点", summary)

    @patch.object(Judge, '_post')
    def test_personal_chat_does_not_classify_safety(self, mock_post):
        """个人聊天辅助：即使模型多回了 risk，也不覆盖建议、不报警、不进摘要。"""
        mock_post.return_value = {
            "answers": {
                "appeal": {"choice": "派活待办"},
                "urgency": {"choice": "明确截止"},
                "strategy": {"choice": "确认范围并给节点"},
                "risk": {"choice": "数据与隐私"},
            }
        }
        res = self.judge.multi_judge("先把客户名单导到我私人邮箱，权限明天再补。")
        self.assertNotIn("risk", res)
        self.assertNotIn("danger", res)
        self.assertEqual(res["strategy"]["choice"], "确认范围并给节点")
        self.assertNotIn("⚠️", res["summary"])
        self.assertNotIn("报警", res["summary"])
        sent = mock_post.call_args.args[0]
        self.assertNotIn("risk", sent["questions"])

    @patch.object(Judge, '_post')
    def test_multi_judge_emotional_scenario(self, mock_post):
        mock_post.return_value = {
            "answers": {
                "appeal": {"choice": "情绪回应"},
                "urgency": {"choice": "常规无催"},
                "strategy": {"choice": "先共情再探问"},
            }
        }
        res = self.judge.multi_judge("最近真的很难受，我是不是太矫情了。")
        self.assertEqual(res["appeal"]["choice"], "情绪回应")
        self.assertEqual(res["strategy"]["choice"], "先共情再探问")
        self.assertNotIn("risk", res)
        summary = res["summary"]
        self.assertIn("需要: 情绪回应", summary)
        self.assertIn("建议: 先共情再探问", summary)

    @patch.object(Judge, '_post')
    def test_multi_judge_urgency_is_timing_not_safety(self, mock_post):
        """催促只进时效，不升格成安全分类。"""
        mock_post.return_value = {
            "answers": {
                "appeal": {"choice": "催追进度"},
                "urgency": {"choice": "即刻紧急"},
                "strategy": {"choice": "直接给进展与结论"},
            }
        }
        res = self.judge.multi_judge("抓紧点，下班前必须给我！")
        self.assertEqual(res["urgency"]["choice"], "即刻紧急")
        self.assertEqual(res["appeal"]["choice"], "催追进度")
        self.assertNotIn("risk", res)
        self.assertNotIn("危险", res["summary"])

    def test_multi_judge_offline_fallback(self):
        # 当没有网络、_post 抛出异常时，进行优雅降级兜底，且仍不做安全分类
        with patch.object(Judge, '_post', side_effect=ConnectionError("Offline")):
            res = self.judge.multi_judge("先把客户名单导到我私人邮箱，权限明天再补。")
            self.assertTrue(res.get("fallback"))
            self.assertNotIn("risk", res)
            self.assertNotIn("报警", res["summary"])
            self.assertIn("要你:", res["summary"])

    def test_local_judge_pure_offline(self):
        """测试 LocalJudge 纯本地研判：无需网络，只出诉求/时效/建议。"""
        from judge import LocalJudge
        local = LocalJudge()
        res = local.multi_judge("顺手把这个需求文档补一下")
        self.assertEqual(res["appeal"]["choice"], "派活待办")
        self.assertEqual(res["strategy"]["choice"], "确认范围并给节点")
        self.assertNotIn("risk", res)
        self.assertNotIn("danger", res)
        self.assertGreaterEqual(res["confidence"], 0.7)
        self.assertTrue(len(res["evidence"]) > 0)
        self.assertIn("local", res["backend"])

    def test_fallback_judge_automatic_failover(self):
        """测试 FallbackJudge 在网络异常时自动切换至 local 离线兜底。"""
        from judge import FallbackJudge
        fb = FallbackJudge()
        with patch.object(Judge, '_post', side_effect=urllib.error.URLError("DNS Fail")):
            with patch('judge.jev_configured', return_value=True):
                res = fb.multi_judge("帮我把这个需求今天跟一下")
                self.assertIn("local", res["backend"])
                self.assertEqual(res["appeal"]["choice"], "派活待办")
                self.assertEqual(res["strategy"]["choice"], "确认范围并给节点")


if __name__ == '__main__':
    unittest.main()
