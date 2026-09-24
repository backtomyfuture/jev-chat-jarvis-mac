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
    RISK_CATEGORIES,
    detect_objective_risk,
    extract_urgency_heuristics,
    extract_appeal_heuristics,
    format_judgment_summary,
)


class MultiJudgeTests(unittest.TestCase):
    def setUp(self):
        self.judge = Judge()

    def test_heuristics_risk_detection(self):
        # 1. 涉及私人邮箱与客户名单 -> 必须触发数据隐私/合规风险
        has_risk, cat, warn, score = detect_objective_risk("先把客户名单导到我私人邮箱，权限明天再补。")
        self.assertTrue(has_risk)
        self.assertIn(cat, ("数据与隐私", "流程与合规"))
        self.assertGreaterEqual(score, 7)

        # 2. 正常催促与严肃语气 -> 绝不误报风险 (遵守“风险不从语气推导”规则)
        has_risk, cat, warn, score = detect_objective_risk("抓紧点，下班前必须给我，别耽搁了！")
        self.assertFalse(has_risk)
        self.assertEqual(cat, "无明显风险")
        self.assertLessEqual(score, 3)

        # 3. 情绪倾诉 -> 绝不误报风险
        has_risk, cat, warn, score = detect_objective_risk("最近真的很难受，我是不是太矫情了。")
        self.assertFalse(has_risk)
        self.assertEqual(cat, "无明显风险")

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
                "risk": {"choice": "无明显风险", "confidence": 0.95},
            }
        }
        res = self.judge.multi_judge("客户明早十点要看版本，你今晚方便的话再把首页过一遍？")
        self.assertEqual(res["appeal"]["choice"], "派活待办")
        self.assertEqual(res["urgency"]["choice"], "今日节点")
        self.assertEqual(res["strategy"]["choice"], "确认范围并给节点")
        self.assertFalse(res["risk"]["has_risk"])
        self.assertLessEqual(res["risk"]["score"], 3)
        self.assertLessEqual(res["danger"]["score"], 3)
        # 验证单行摘要
        summary = res["summary"]
        self.assertIn("要你: 派活待办", summary)
        self.assertIn("时效: 今日节点", summary)
        self.assertIn("建议: 确认范围并给节点", summary)

    @patch.object(Judge, '_post')
    def test_multi_judge_risk_scenario(self, mock_post):
        mock_post.return_value = {
            "answers": {
                "appeal": {"choice": "派活待办"},
                "urgency": {"choice": "明确截止"},
                "strategy": {"choice": "核验授权按流程办"},
                "risk": {"choice": "数据与隐私"},
            }
        }
        res = self.judge.multi_judge("先把客户名单导到我私人邮箱，权限明天再补。")
        self.assertTrue(res["risk"]["has_risk"])
        self.assertGreaterEqual(res["risk"]["score"], 7)
        self.assertGreaterEqual(res["danger"]["score"], 7)
        summary = res["summary"]
        self.assertIn("⚠️", summary)
        self.assertIn("建议: 核验授权按流程办", summary)

    @patch.object(Judge, '_post')
    def test_multi_judge_emotional_scenario(self, mock_post):
        mock_post.return_value = {
            "answers": {
                "appeal": {"choice": "情绪回应"},
                "urgency": {"choice": "常规无催"},
                "strategy": {"choice": "先共情再探问"},
                "risk": {"choice": "无明显风险"},
            }
        }
        res = self.judge.multi_judge("最近真的很难受，我是不是太矫情了。")
        self.assertEqual(res["appeal"]["choice"], "情绪回应")
        self.assertEqual(res["strategy"]["choice"], "先共情再探问")
        self.assertFalse(res["risk"]["has_risk"])
        summary = res["summary"]
        self.assertIn("需要: 情绪回应", summary)
        self.assertIn("建议: 先共情再探问", summary)

    @patch.object(Judge, '_post')
    def test_multi_judge_urgency_not_danger(self, mock_post):
        """测试核心原则：催促/严肃语气绝不直接等同于危险。"""
        mock_post.return_value = {
            "answers": {
                "appeal": {"choice": "催追进度"},
                "urgency": {"choice": "即刻紧急"},
                "strategy": {"choice": "直接给进展与结论"},
                "risk": {"choice": "无明显风险"},
            }
        }
        res = self.judge.multi_judge("抓紧点，下班前必须给我！")
        self.assertEqual(res["urgency"]["choice"], "即刻紧急")
        self.assertFalse(res["risk"]["has_risk"])
        self.assertLessEqual(res["danger"]["score"], 3)

    def test_multi_judge_offline_fallback(self):
        # 当没有网络、_post 抛出异常时，进行优雅降级兜底
        with patch.object(Judge, '_post', side_effect=ConnectionError("Offline")):
            res = self.judge.multi_judge("先把客户名单导到我私人邮箱，权限明天再补。")
            self.assertTrue(res.get("fallback"))
            self.assertTrue(res["risk"]["has_risk"])
            self.assertGreaterEqual(res["risk"]["score"], 7)

    def test_severe_threat_safety_risk_priority(self):
        """测试对人身安全与暴力恐吓威胁的最高优先级拦截与报警策略。"""
        # 1. 人身安全威胁
        has_risk, cat, warn, score = detect_objective_risk("不答应就弄死你，小心你的命！")
        self.assertTrue(has_risk)
        self.assertEqual(cat, "人身安全")
        self.assertEqual(score, 9)

        # 2. 言语胁迫
        has_risk, cat, warn, score = detect_objective_risk("不按我说的做就让你在行业里混不下去！")
        self.assertTrue(has_risk)
        self.assertEqual(cat, "胁迫与威胁")
        self.assertGreaterEqual(score, 8)

    def test_threat_offline_fallback_never_downgrades(self):
        """防范重大安全漏洞：断网降级时，严重威胁绝不可降级为“无明显风险+顺势承接”。"""
        with patch.object(Judge, '_post', side_effect=ConnectionError("Offline")):
            res = self.judge.multi_judge("不答应就弄死你")
            self.assertTrue(res.get("fallback"))
            self.assertTrue(res["risk"]["has_risk"])
            self.assertEqual(res["risk"]["category"], "人身安全")
            self.assertEqual(res["risk"]["score"], 9)
            self.assertEqual(res["strategy"]["choice"], "立即报警并保留证据")
            self.assertIn("立即报警", res["summary"])

    def test_local_judge_pure_offline(self):
        """测试 LocalJudge 纯本地研判：无需网络、完整输出 4 维度与证据片段。"""
        from judge import LocalJudge
        local = LocalJudge()
        res = local.multi_judge("先把客户名单导到我私人邮箱，权限明天再补。")
        self.assertTrue(res["risk"]["has_risk"])
        self.assertEqual(res["risk"]["category"], "数据与隐私")
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
