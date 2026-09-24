"""Judge backed by TypeSafe's Jev API.

Pure remote judgment module: evaluates message intent and risk, and ranks reply candidates
via TypeSafe Jev System One API over a persistent HTTP keep-alive connection pool.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
from pathlib import Path

import userconfig
from generate import jev_request_url, http_post_json

DEFAULT_BASE = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"
TIMEOUT = 30

INTENTS = {
    "派活": "对方要我做一件事或接一个任务",
    "催进度": "对方在催促我尽快完成某个已在办的事",
    "问进度": "对方在询问某件事的进展或状态",
    "批评": "对方对我的工作或结果表达不满、指出错误",
    "要解释": "对方要求我说明原因或给出解释",
    "闲聊": "对方只是在聊天、分享或表达感受，没有具体要求",
    "约会议": "对方想安排一次会议或通话",
    "夸奖": "对方在肯定、称赞我的成果",
}

RISK_LEVELS = [
    "完全没风险，怎么回都行",
    "基本没风险",
    "平淡，正常回就好",
    "需要稍微留神",
    "有点敏感，措辞注意",
    "需要谨慎，可能被挑刺",
    "比较危险，容易得罪人或踩坑",
    "很危险，说错要出问题",
    "非常危险，涉及责任或利益",
    "极度危险，先别回，想清楚再说",
]

ACTION_MAP = {
    "派活": ["接住", "问清交付标准和期限", "先给个时间点"],
    "催进度": ["先给当前状态", "给明确的完成时间", "别解释太多"],
    "问进度": ["直接说事实", "给下个节点", "有卡点就说卡点"],
    "批评": ["先认下来", "别急着辩解", "给补救方案"],
    "要解释": ["说清原因", "别找借口", "给改进措施"],
    "闲聊": ["轻松回应", "可以互动", "不用当真"],
    "约会议": ["确认时间", "说清议程", "准备好材料"],
    "夸奖": ["接住并感谢", "别过度谦虚", "可以顺带提下一步"],
}

MULTI_INTENTS = {
    "催进度": "催促完成、追问进展",
    "派任务": "交办工作、指派事情",
    "求协助": "寻求支持、请教问题",
    "要反馈": "征求意见、确认方案",
    "对信息": "核对事实、同步细节",
    "探口风": "打听私密消息、试探态度底线",
    "表不满": "指出问题、表达质疑批评",
    "聊闲天": "日常寒暄、随性互动",
    "道感谢": "表达谢意、肯定支持",
    "约时间": "约定会议、安排日程",
}

MULTI_EMOTIONS = {
    "焦急施压": "时间紧迫、急于得到结果",
    "不满质问": "心怀抱怨、严肃挑剔",
    "客气疏离": "礼貌客套、保持距离",
    "平静客观": "就事论事、理性冷静",
    "小心试探": "话留三分、试探摸底",
    "热情友好": "认可赞许、积极支持",
    "轻松随性": "随和闲适、毫无压力",
    "敷衍冷淡": "被动应付、兴致不高",
}

MULTI_STRATEGIES = {
    "给明确节点": "给出具体完成时间",
    "先稳住情绪": "先接住感受，避免辩解",
    "直给结论": "少说废话，直接说答案",
    "问清标准": "反问明确细节与边界",
    "客观报卡点": "说明困难并附备选方案",
    "借力挡回": "委婉推迟或按流程办",
    "顺势承接": "轻松随和回应即可",
    "礼貌回谢": "客气感谢对方支持",
}

# 微信日常研判 · 诉求 / 时效 / 建议 / 独立风险体系
APPEALS = {
    "派活待办": "交办任务或推动具体事项落实",
    "催追进度": "催促尽快完成或追问进展节点",
    "核对确认": "核对事实、细节或确认方案数据",
    "寻求协助": "遇到阻碍求助或请教支持",
    "要个说法": "指出问题、要求说明原因或道歉",
    "情绪回应": "倾诉压力或委屈，寻求共情与倾听",
    "关系维系": "客套问候、维系人脉或拉近距离",
    "随性闲聊": "日常闲聊、随性互动与吐槽",
}

URGENCIES = {
    "即刻紧急": "明确要求马上、即刻或急件处理",
    "今日节点": "要求今天内、今晚或下班前完成",
    "明确截止": "给出了明确的未来截止时间（如明早、本周五）",
    "常规无催": "未给出紧迫时限，按常规节奏推进",
}

STRATEGIES = {
    "确认范围并给节点": "先问清交付标准与边界，再给出明确完成时间",
    "直接给进展与结论": "就事论事直接汇报当前状态与明确节点，避免过多辩解",
    "先共情再探问": "先接住对方情绪给予倾听理解，再探问是否需要支持",
    "厘清事实与责任": "客观呈现数据与事实，厘清责任边界，避免情绪化推诿",
    "温和设限保留余地": "客观说明当前排期冲突或困难，协商可行替代方案",
    "核验授权按流程办": "涉及权限、数据或合规时，严格走合规流程不私下越权",
    "顺势承接礼貌回应": "自然友好回应，维持良好沟通氛围",
}

RISK_CATEGORIES = {
    "数据与隐私": "涉及客户名单、账号密码、隐私信息或商业机密",
    "流程与合规": "涉及绕过审批、借用权限、私下操作违规事项",
    "承诺与背书": "被要求口头做无法保障的兜底承诺或承担未定责任",
    "资金与权益": "涉及借款、转账、垫付资金、合同结算或利益分配",
    "权责不清": "责任界限模糊，可能被动兜底或承担非己职责",
    "无明显风险": "常规工作沟通，不涉及上述边界风险",
}


def detect_objective_risk(text: str) -> tuple[bool, str, str, int]:
    """严格基于客观证据识别风险，绝不从语气（催促、严肃）反推风险。

    返回 (has_risk, category, warning, score)
    """
    if any(k in text for k in ("私人邮箱", "客户名单", "客户资料", "私信发我", "导到我私", "发到私", "账号密码", "身份证", "验证码")):
        return True, "数据与隐私", "涉及客户数据与私发风险", 8
    if any(k in text for k in ("权限明天再补", "权限后补", "绕过审批", "先斩后奏", "走私单", "别走系统", "先发我后面补流程", "私下操作")):
        return True, "流程与合规", "涉及绕过流程与权限违规", 8
    if any(k in text for k in ("帮我垫付", "先转我", "私账", "转到我微信", "借点钱", "帮我充值", "无发票报销")):
        return True, "资金与权益", "涉及私下资金与财产操作", 8
    if any(k in text for k in ("你打包票", "必须绝对保证", "出事全归你", "出了问题你负责", "无论如何不能延期")):
        return True, "承诺与背书", "涉及口头兜底承诺与连带责任", 6
    if any(k in text for k in ("反正你看着办吧", "出了事找你", "这事你替我做")):
        return True, "权责不清", "责任范围模糊待澄清", 5
    return False, "无明显风险", "", 2


def extract_urgency_heuristics(text: str) -> str:
    if any(k in text for k in ("马上", "立刻", "加急", "十万火急", "急！", "急件")):
        return "即刻紧急"
    if any(k in text for k in ("今天", "今晚", "下班前", "今天内", "下午五点前", "下班后")):
        return "今日节点"
    if any(k in text for k in ("明天", "明早", "后天", "周五", "周一", "下周", "十点", "明晚")):
        return "明确截止"
    return "常规无催"


def extract_appeal_heuristics(text: str) -> str:
    if any(k in text for k in ("难受", "太矫情", "委屈", "好累", "想哭", "心塞", "emo")):
        return "情绪回应"
    if any(k in text for k in ("顺手把", "跟一下", "发过去吧", "补一下", "帮忙做", "去把", "负责")):
        return "派活待办"
    if any(k in text for k in ("做完了吗", "什么时候能好", "抓紧点", "催", "进度怎么样")):
        return "催追进度"
    if any(k in text for k in ("不对啊", "怎么又", "什么玩意", "为什么用", "怎么想的")):
        return "要个说法"
    if any(k in text for k in ("开个会", "聊十分钟", "语音")):
        return "约会议"
    if any(k in text for k in ("哈哈", "爬山", "牛啊")):
        return "随性闲聊"
    return "核对确认"


def format_judgment_summary(judg: dict | None) -> str:
    """Format structured judgment into a clean, actionable summary line.

    If risk is present:
        ⚠️ [风险类别/警示]  ·  建议: [应对建议]
    Else if appeal is emotional response:
        需要: [诉求]  ·  建议: [应对建议]
    Else:
        要你: [诉求]  ·  时效: [时效]  ·  建议: [应对建议]
    """
    if not judg or judg.get("status") == "pending":
        return "Jev 研判中…"
    if judg.get("error"):
        return f"研判异常: {judg.get('error')[:20]}"

    risk = judg.get("risk") or {}
    has_risk = risk.get("has_risk", False) or (
        judg.get("danger", {}).get("score", 0) >= 7 and "无明显风险" not in str(risk.get("category", ""))
    )

    strategy_choice = (
        (judg.get("strategy") or {}).get("choice")
        or (judg.get("action") or {}).get("choice")
        or "顺势承接礼貌回应"
    )

    if has_risk:
        warn = risk.get("warning") or risk.get("category") or "存在潜在风险"
        return f"⚠️ {warn}  ·  建议: {strategy_choice}"

    appeal_choice = (
        (judg.get("appeal") or {}).get("choice")
        or (judg.get("intent") or {}).get("choice")
        or "随性闲聊"
    )
    urgency_choice = (
        (judg.get("urgency") or {}).get("choice")
        or "常规无催"
    )

    # 兼容只有旧版 3 栏数据的情况 (诉求, 态度, 策略)
    if "urgency" not in judg and "emotion" in judg and "appeal" not in judg:
        emotion_choice = (judg.get("emotion") or {}).get("choice") or "平静客观"
        return f"诉求: {appeal_choice}  ·  态度: {emotion_choice}  ·  策略: {strategy_choice}"

    if appeal_choice in ("情绪回应", "求安慰"):
        return f"需要: {appeal_choice}  ·  建议: {strategy_choice}"

    return f"要你: {appeal_choice}  ·  时效: {urgency_choice}  ·  建议: {strategy_choice}"


format_jev_card_text = format_judgment_summary


class ModelNotDownloadedError(RuntimeError):
    pass


class LowMemoryError(RuntimeError):
    pass


def model_cache_dir() -> Path:
    hf_home = os.environ.get("HF_HOME")
    base = Path(hf_home) if hf_home else Path.home() / ".cache" / "huggingface"
    return base / "hub" / "models--Mapika--decider-2b"


def model_cached() -> bool:
    cache = model_cache_dir() / "snapshots"
    if not cache.is_dir():
        return False
    for snap in cache.iterdir():
        if snap.is_dir() and any(snap.glob("*.safetensors")):
            return True
    return False


def model_disk_usage() -> int:
    cache = model_cache_dir()
    if not cache.is_dir():
        return 0
    seen = set()
    total = 0
    for p in cache.rglob("*"):
        if p.is_file() and not p.is_symlink():
            try:
                st = p.stat()
                if st.st_ino not in seen:
                    seen.add(st.st_ino)
                    total += st.st_size
            except OSError:
                pass
    return total


def _download_progress(report, min_interval=0.5):
    class NoOpProgress:
        def __init__(self, *args, **kwargs):
            self.total = kwargs.get("total", 0)
            self.n = kwargs.get("initial", 0)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def update(self, *args, **kwargs):
            pass
        def refresh(self, *args, **kwargs):
            pass
        def close(self):
            pass
    return NoOpProgress


def low_memory_reason() -> str | None:
    return None


def download_block_reason(repo: str = "Mapika/decider-2b") -> str | None:
    backend = userconfig.get("JUDGE_BACKEND").strip().lower()
    if backend == "local":
        return None
    if backend == "cloud":
        return ("已选择在线判断（JUDGE_BACKEND=cloud）· 不下载本地模型。"
                "如需离线判断，可在模型设置的「判断 · Jev」页启用")
    if model_cached():
        return None
    return ("离线判断模型尚未下载（约 3.8 GB）· 可配置 TYPESAFE_API_KEY 走云端判断，"
            "或在模型设置的「判断 · Jev」页启用离线模型")


def jev_configured() -> bool:
    return bool(userconfig.get("TYPESAFE_API_KEY", "JEV_API_KEY"))


class Judge:
    """Remote Jev Judge providing intent, risk evaluation, and candidate ranking."""

    name = "jev-api"

    def __init__(self, base: str | None = None, key: str | None = None,
                 model: str | None = None, timeout: int = TIMEOUT):
        self.base = (base or userconfig.get("TYPESAFE_BASE_URL") or DEFAULT_BASE).rstrip("/")
        self.key = key or userconfig.get("TYPESAFE_API_KEY", "JEV_API_KEY")
        self.model = model or userconfig.get("TYPESAFE_MODEL") or DEFAULT_MODEL
        self.timeout = timeout
        self._last_url = ""
        self._load_status = None
        self._load_lock = threading.RLock()

    @property
    def load_status(self) -> str | None:
        return getattr(self, "_load_status", None)

    @load_status.setter
    def load_status(self, val: str | None) -> None:
        self._load_status = val

    @property
    def backend_label(self) -> str:
        return f"Jev ({self.model})"

    def _load(self):
        with self._load_lock:
            pass

    def warm(self) -> None:
        with self._load_lock:
            self._load()

    def judge(self, message: str, context: str | None = None) -> dict:
        state = f"{context}\n\n{message}" if context else message
        payload = {
            "model": self.model,
            "state": state,
            "questions": {
                "intent": {"type": "choice",
                           "instructions": "这句话的真实意图是什么？",
                           "criteria": INTENTS},
                "risk": {"type": "score",
                         "instructions": "如果直接回复这句话，风险有多大？",
                         "criteria": RISK_LEVELS},
            },
        }
        data = self._post(payload)
        answers = data.get("answers") or {}
        intent_ans = answers.get("intent") or {}
        risk_ans = answers.get("risk") or {}

        intent = intent_ans.get("choice") or "闲聊"
        if intent not in INTENTS:
            for name in INTENTS:
                if name in str(intent):
                    intent = name
                    break
            else:
                intent = "闲聊"
        confidence = float(intent_ans.get("confidence") or 0.0)
        risk = risk_ans.get("score")
        risk = float(risk) if isinstance(risk, (int, float)) else 0.0

        return {
            "intent": intent,
            "confidence": confidence,
            "intent_probs": intent_ans.get("probabilities") or {},
            "risk": round(risk, 1),
            "risk_probs": risk_ans.get("probabilities") or {},
            "actions": ACTION_MAP.get(intent, []),
            "message": message,
            "backend": f"jev/{self.model}",
        }

    def multi_judge(self, message: str, context: str | None = None) -> dict:
        """Evaluate a chat message across 4 core dimensions (Appeal, Urgency, Strategy, Risk).
        
        Strict rules:
        1. No mind-reading: objective task/relational cues, no psychologizing.
        2. Risk is independent: never infer danger from urgent or serious tone.
        3. Actionable guidance: appeal + urgency + strategy, risk highlighted only when triggered.
        """
        if not message.strip():
            return {}
        cache_key = f"{context}\n---\n{message}"
        if not hasattr(self, "_multi_cache"):
            self._multi_cache = {}
        if cache_key in self._multi_cache:
            return self._multi_cache[cache_key]

        rule_has_risk, rule_cat, rule_warn, rule_score = detect_objective_risk(message)
        h_urgency = extract_urgency_heuristics(message)
        h_appeal = extract_appeal_heuristics(message)

        if context:
            state = (
                f"【此前多轮对话记录（包含我方与对方的发言）】：\n"
                f"{context}\n\n"
                f"【对方当前最新消息】：\n"
                f"{message}\n\n"
                f"请务必结合上述完整多轮对话语境，客观研判对方此消息的诉求、时效压力、我方应对建议以及是否存在客观边界风险（隐私/合规/资金/承诺）。"
            )
        else:
            state = f"【对方当前最新消息】：\n{message}"

        payload = {
            "model": self.model,
            "state": state,
            "questions": {
                "appeal": {
                    "type": "choice",
                    "instructions": "对方发送此消息的核心真实诉求最符合哪项？",
                    "criteria": APPEALS,
                },
                "urgency": {
                    "type": "choice",
                    "instructions": "对方对此时效的要求与时间压力最符合哪项？",
                    "criteria": URGENCIES,
                },
                "strategy": {
                    "type": "choice",
                    "instructions": "结合当前语境，我方最得体有效的应对建议是？",
                    "criteria": STRATEGIES,
                },
                "risk": {
                    "type": "choice",
                    "instructions": "该消息是否存在涉及隐私数据、绕流程违规、资金或口头兜底承诺风险？（请严格基于客观事实，催促或严肃不构成合规/隐私风险）",
                    "criteria": RISK_CATEGORIES,
                },
            },
        }

        try:
            data = self._post(payload)
            ans = data.get("answers") or {}

            appeal_ans = ans.get("appeal") or {}
            urgency_ans = ans.get("urgency") or {}
            strategy_ans = ans.get("strategy") or {}
            risk_ans = ans.get("risk") or {}

            appeal_choice = appeal_ans.get("choice") or h_appeal or "核对确认"
            urgency_choice = urgency_ans.get("choice") or h_urgency or "常规无催"
            strategy_choice = strategy_ans.get("choice") or "顺势承接礼貌回应"
            risk_choice = risk_ans.get("choice") or "无明显风险"

            # 规则与模型双重保障：客观风险独立判定
            if rule_has_risk:
                has_risk = True
                risk_choice = rule_cat
                risk_warn = rule_warn
                risk_score = rule_score
                if strategy_choice in ("顺势承接礼貌回应", "先共情再探问"):
                    strategy_choice = "核验授权按流程办" if "流程" in rule_cat or "数据" in rule_cat else "温和设限保留余地"
            elif risk_choice != "无明显风险" and risk_choice in RISK_CATEGORIES:
                has_risk = True
                risk_score = 8 if risk_choice in ("数据与隐私", "流程与合规", "资金与权益") else 6
                risk_warn = f"涉及{risk_choice}风险"
                if strategy_choice == "顺势承接礼貌回应":
                    strategy_choice = "核验授权按流程办"
            else:
                has_risk = False
                risk_choice = "无明显风险"
                risk_warn = ""
                risk_score = 2

            # 针对情绪倾诉的针对性调整
            if appeal_choice == "情绪回应" and not has_risk:
                if strategy_choice not in ("先共情再探问", "顺势承接礼貌回应"):
                    strategy_choice = "先共情再探问"

            appeal_tip = APPEALS.get(appeal_choice, "")
            urgency_tip = URGENCIES.get(urgency_choice, "")
            strategy_tip = STRATEGIES.get(strategy_choice, "")
            risk_tip = RISK_CATEGORIES.get(risk_choice, "")

            result = {
                "appeal": {
                    "choice": appeal_choice,
                    "tip": appeal_tip,
                    "probabilities": appeal_ans.get("probabilities") or {},
                },
                "urgency": {
                    "choice": urgency_choice,
                    "tip": urgency_tip,
                    "probabilities": urgency_ans.get("probabilities") or {},
                },
                "strategy": {
                    "choice": strategy_choice,
                    "tip": strategy_tip,
                    "probabilities": strategy_ans.get("probabilities") or {},
                },
                "risk": {
                    "has_risk": has_risk,
                    "category": risk_choice,
                    "warning": risk_warn,
                    "tip": risk_tip,
                    "score": risk_score,
                },
                # Backward-compatibility aliases
                "intent": {
                    "choice": appeal_choice,
                    "tip": appeal_tip,
                    "probabilities": appeal_ans.get("probabilities") or {},
                },
                "emotion": {
                    "choice": "就事论事" if not has_risk else "需警惕防范",
                    "tip": "客观沟通" if not has_risk else "客观风险警示",
                    "probabilities": {},
                },
                "action": {
                    "choice": strategy_choice,
                    "tip": strategy_tip,
                    "probabilities": strategy_ans.get("probabilities") or {},
                },
                "danger": {
                    "score": risk_score,
                },
                "summary": format_judgment_summary({
                    "appeal": {"choice": appeal_choice},
                    "urgency": {"choice": urgency_choice},
                    "strategy": {"choice": strategy_choice},
                    "risk": {"has_risk": has_risk, "category": risk_choice, "warning": risk_warn, "score": risk_score},
                }),
                "message": message,
            }
            self._multi_cache[cache_key] = result
            return result
        except Exception as e:
            # 优雅降级：在离线或调用异常时提供启发式推断兜底
            has_risk = rule_has_risk
            risk_score = rule_score if has_risk else 2
            appeal_choice = h_appeal or ("派活待办" if "做" in message or "改" in message else "核对确认")
            urgency_choice = h_urgency or "常规无催"
            strategy_choice = ("核验授权按流程办" if has_risk else 
                               ("先共情再探问" if appeal_choice == "情绪回应" else 
                                ("确认范围并给节点" if appeal_choice == "派活待办" else "顺势承接礼貌回应")))
            fallback_res = {
                "appeal": {"choice": appeal_choice, "tip": APPEALS.get(appeal_choice, ""), "probabilities": {}},
                "urgency": {"choice": urgency_choice, "tip": URGENCIES.get(urgency_choice, ""), "probabilities": {}},
                "strategy": {"choice": strategy_choice, "tip": STRATEGIES.get(strategy_choice, ""), "probabilities": {}},
                "risk": {
                    "has_risk": has_risk,
                    "category": rule_cat if has_risk else "无明显风险",
                    "warning": rule_warn if has_risk else "",
                    "tip": RISK_CATEGORIES.get(rule_cat, "") if has_risk else "",
                    "score": risk_score,
                },
                "intent": {"choice": appeal_choice, "tip": APPEALS.get(appeal_choice, ""), "probabilities": {}},
                "emotion": {"choice": "就事论事", "tip": "客观沟通", "probabilities": {}},
                "action": {"choice": strategy_choice, "tip": STRATEGIES.get(strategy_choice, ""), "probabilities": {}},
                "danger": {"score": risk_score},
                "summary": format_judgment_summary({
                    "appeal": {"choice": appeal_choice},
                    "urgency": {"choice": urgency_choice},
                    "strategy": {"choice": strategy_choice},
                    "risk": {"has_risk": has_risk, "category": rule_cat, "warning": rule_warn, "score": risk_score},
                }),
                "message": message,
                "fallback": True,
                "error": str(e),
            }
            return fallback_res

    def rank_candidates(self, message: str, intent: str,
                        candidates: list[str]) -> list[dict]:
        if not candidates:
            return []
        payload = {
            "model": self.model,
            "state": f"收到的消息：{message}\n判断出的意图：{intent}",
            "questions": {"best": {"type": "choice",
                                   "instructions": "哪一条回复最合适？",
                                   "criteria": {c: None for c in candidates}}},
        }
        data = self._post(payload)
        ans = ((data.get("answers") or {}).get("best") or {})
        probs = ans.get("probabilities") or {}
        ranked = []
        for c in candidates:
            p = probs.get(c)
            if p is None:
                p = ans.get("confidence", 0.0) if ans.get("choice") == c else 0.0
            ranked.append({"text": c, "prob": float(p)})
        ranked.sort(key=lambda r: -r["prob"])
        return ranked

    def _post(self, payload: dict) -> dict:
        url = jev_request_url(self.base)
        self._last_url = url
        import judge_jev
        poster = getattr(judge_jev, "http_post_json", http_post_json)
        return poster(
            url,
            {"content-type": "application/json",
             "authorization": f"Bearer {self.key}"},
            payload, self.timeout)


class FallbackJudge(Judge):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.local = self

    @property
    def load_status(self) -> str | None:
        if hasattr(self, "local") and self.local is not None and self.local is not self:
            return self.local.load_status
        return getattr(self, "_load_status", None)

    @load_status.setter
    def load_status(self, val: str | None) -> None:
        self._load_status = val
        if hasattr(self, "local") and self.local is not None and self.local is not self:
            self.local.load_status = val


JevJudge = Judge


def make_judge() -> Judge:
    return Judge()


if __name__ == "__main__":
    j = Judge()
    msg = sys.argv[1] if len(sys.argv) > 1 else "这个需求你今天跟一下"
    t0 = time.perf_counter()
    try:
        res = j.judge(msg)
    except urllib.error.HTTPError as e:
        print(f"❌ HTTP {e.code} @ {j._last_url}\n   {e.read()[:300].decode(errors='replace')}")
        raise SystemExit(1)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    print(f"耗时 {time.perf_counter() - t0:.2f}s")
