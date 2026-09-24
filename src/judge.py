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
    "立即报警并保留证据": "遭受人身安全威胁时，保留所有聊天记录并立即报警求助",
    "留存记录明确拒绝": "面对胁迫恐吓时，保留证据并拒绝不合理要求",
    "顺势承接礼貌回应": "自然友好回应，维持良好沟通氛围",
}

RISK_CATEGORIES = {
    "人身安全": "涉及人身安全受威胁、暴力伤害、死亡恐吓、极端非法行为",
    "胁迫与威胁": "涉及敲诈勒索、言语恐吓、强逼逼迫、职场霸凌",
    "数据与隐私": "涉及客户名单、账号密码、隐私信息或商业机密",
    "流程与合规": "涉及绕过审批、借用权限、私下操作违规事项",
    "承诺与背书": "被要求口头做无法保障的兜底承诺或承担未定责任",
    "资金与权益": "涉及借款、转账、垫付资金、合同结算或利益分配",
    "权责不清": "责任界限模糊，可能被动兜底或承担非己职责",
    "无明显风险": "常规工作沟通，不涉及上述边界风险",
}


def jev_configured() -> bool:
    """True when a TypeSafe key is present — callers prefer Jev over the local model."""
    return bool(userconfig.get("TYPESAFE_API_KEY", "JEV_API_KEY"))


RISK_STRATEGY_MAP = {
    "人身安全": "立即报警并保留证据",
    "胁迫与威胁": "留存记录明确拒绝",
    "数据与隐私": "核验授权按流程办",
    "流程与合规": "核验授权按流程办",
    "资金与权益": "核验授权按流程办",
    "承诺与背书": "温和设限保留余地",
    "权责不清": "厘清事实与责任",
}


def detect_objective_risk(text: str) -> tuple[bool, str, str, int]:
    """严格基于客观证据识别风险，绝不从语气（催促、严肃）反推风险。

    返回 (has_risk, category, warning, score)
    """
    # 0. 人身安全与暴力威胁（最高优先级）
    if any(k in text for k in (
        "弄死你", "弄死", "杀了你", "砍死", "打死你", "打死", "别想活", "要你的命", "同归于尽",
        "小心你的命", "走着瞧弄死", "找人搞你", "废了你", "打断你的腿", "弄残", "去死吧", "收拾你"
    )):
        return True, "人身安全", "人身安全受威胁与暴力言论", 9

    # 1. 胁迫与威胁
    if any(k in text for k in (
        "让你混不下去", "见不到钱就曝光", "不按我说的做就", "敢告我试试", "走着瞧", "有你好看的", "别怪我不客气", "逼你"
    )):
        return True, "胁迫与威胁", "遭遇言语胁迫与恐吓", 8

    # 2. 数据与隐私
    if any(k in text for k in ("私人邮箱", "客户名单", "客户资料", "私信发我", "导到我私", "发到私", "账号密码", "身份证", "验证码")):
        return True, "数据与隐私", "涉及客户数据与私发风险", 8

    # 3. 流程与合规
    if any(k in text for k in ("权限明天再补", "权限后补", "绕过审批", "先斩后奏", "走私单", "别走系统", "先发我后面补流程", "私下操作")):
        return True, "流程与合规", "涉及绕过流程与权限违规", 8

    # 4. 资金与权益
    if any(k in text for k in ("帮我垫付", "先转我", "私账", "转到我微信", "借点钱", "帮我充值", "无发票报销")):
        return True, "资金与权益", "涉及私下资金与财产操作", 8

    # 5. 承诺与背书
    if any(k in text for k in ("你打包票", "必须绝对保证", "出事全归你", "出了问题你负责", "无论如何不能延期")):
        return True, "承诺与背书", "涉及口头兜底承诺与连带责任", 6

    # 6. 权责不清
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


def map_appeal_to_intent(appeal: str, text: str) -> str:
    """保持对旧版 8 类意图 ('派活', '催进度', '问进度', '批评', '要解释', '闲聊', '约会议', '夸奖') 的兼容映射."""
    if any(k in text for k in ("做得不错", "牛逼", "太强了", "真棒", "思路好", "厉害", "赞")):
        return "夸奖"
    if appeal == "派活待办":
        return "派活"
    if appeal == "催追进度":
        if any(k in text for k in ("怎么样", "上线了吗", "如何", "进展如何", "现在进度")):
            return "问进度"
        return "催进度"
    if appeal == "核对确认":
        return "问进度"
    if appeal == "要个说法":
        if any(k in text for k in ("为什么", "怎么想的", "从哪来", "原因", "解释")):
            return "要解释"
        return "批评"
    if appeal == "约会议":
        return "约会议"
    return "闲聊"


class LocalJudge:
    """纯本地离线研判引擎：零网络请求，绝不外发消息内容，完全满足隐私保护与离线承诺。"""

    name = "local-rules"
    model = "local-offline"

    def __init__(self, *args, **kwargs):
        self.load_status = None

    @property
    def backend_label(self) -> str:
        return "本地离线判断（隐私保护）"

    def warm(self) -> None:
        pass

    def judge(self, message: str, context: str | None = None) -> dict:
        return self.multi_judge(message, context=context)

    def multi_judge(self, message: str, context: str | None = None) -> dict:
        if not message.strip():
            return {}
        has_risk, cat, warn, score = detect_objective_risk(message)
        rec_strat = RISK_STRATEGY_MAP.get(cat, "核验授权按流程办") if has_risk else ""
        h_urgency = extract_urgency_heuristics(message)
        h_appeal = extract_appeal_heuristics(message)

        appeal_choice = h_appeal or "核对确认"
        urgency_choice = h_urgency or "常规无催"

        if has_risk:
            risk_choice = cat
            risk_warn = warn
            risk_score = score
            strategy_choice = rec_strat
        else:
            risk_choice = "无明显风险"
            risk_warn = ""
            risk_score = 2
            if appeal_choice == "情绪回应":
                strategy_choice = "先共情再探问"
            elif appeal_choice == "派活待办":
                strategy_choice = "确认范围并给节点"
            elif appeal_choice == "催追进度":
                strategy_choice = "直接给进展与结论"
            elif appeal_choice == "要个说法":
                strategy_choice = "厘清事实与责任"
            else:
                strategy_choice = "顺势承接礼貌回应"

        evidence = []
        if has_risk:
            evidence.append(f"触发客观风险特征 -> {risk_choice}")
        if urgency_choice != "常规无催":
            evidence.append(f"提取到时效词 -> {urgency_choice}")
        if h_appeal:
            evidence.append(f"提取到诉求模式 -> {h_appeal}")

        conf = 0.88 if (h_appeal or has_risk or urgency_choice != "常规无催") else 0.52
        alternatives = []
        if conf < 0.60:
            alternatives.append("语义较为简短，需结合前文上下文确认")

        old_intent = map_appeal_to_intent(appeal_choice, message)

        summary_text = format_judgment_summary({
            "appeal": {"choice": appeal_choice},
            "urgency": {"choice": urgency_choice},
            "strategy": {"choice": strategy_choice},
            "risk": {"has_risk": has_risk, "category": risk_choice, "warning": risk_warn, "score": risk_score},
        })

        return {
            "appeal": {
                "choice": appeal_choice,
                "tip": APPEALS.get(appeal_choice, ""),
                "probabilities": {appeal_choice: conf},
            },
            "urgency": {
                "choice": urgency_choice,
                "tip": URGENCIES.get(urgency_choice, ""),
                "probabilities": {urgency_choice: 0.8},
            },
            "strategy": {
                "choice": strategy_choice,
                "tip": STRATEGIES.get(strategy_choice, ""),
                "probabilities": {strategy_choice: conf},
            },
            "risk": {
                "has_risk": has_risk,
                "category": risk_choice,
                "warning": risk_warn,
                "tip": RISK_CATEGORIES.get(risk_choice, ""),
                "score": risk_score,
            },
            "confidence": conf,
            "evidence": evidence,
            "alternatives": alternatives,
            "intent": old_intent,
            "intent_probs": {old_intent: conf},
            "risk_probs": {},
            "actions": ACTION_MAP.get(old_intent, [strategy_choice]),
            "danger": {"score": risk_score},
            "emotion": {
                "choice": "就事论事" if not has_risk else "高度警惕",
                "tip": "客观沟通" if not has_risk else "触发客观风险预警",
            },
            "action": {
                "choice": strategy_choice,
                "tip": STRATEGIES.get(strategy_choice, ""),
            },
            "summary": summary_text,
            "message": message,
            "backend": "local (离线规则模型)",
        }

    def rank_candidates(self, message: str, intent: str, candidates: list[str]) -> list[dict]:
        if not candidates:
            return []
        p = 1.0 / len(candidates)
        return [{"text": c, "prob": p} for c in candidates]


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
        """主链判断：统一调用多维研判并返回完整结构化与兼容字段。"""
        return self.multi_judge(message, context=context)

    def multi_judge(self, message: str, context: str | None = None) -> dict:
        """Evaluate a chat message across 4 core dimensions (Appeal, Urgency, Strategy, Risk).
        
        Strict rules:
        1. No mind-reading: objective task/relational cues, no psychologizing.
        2. Risk is independent: never infer danger from urgent or serious tone.
        3. Actionable guidance: appeal + urgency + strategy, risk highlighted only when triggered.
        4. Absolute priority on personal safety and threats.
        """
        if not message.strip():
            return {}
        cache_key = f"{context}\n---\n{message}"
        if not hasattr(self, "_multi_cache"):
            self._multi_cache = {}
        if cache_key in self._multi_cache:
            return self._multi_cache[cache_key]

        rule_has_risk, rule_cat, rule_warn, rule_score = detect_objective_risk(message)
        rule_strat = RISK_STRATEGY_MAP.get(rule_cat, "核验授权按流程办") if rule_has_risk else ""
        h_urgency = extract_urgency_heuristics(message)
        h_appeal = extract_appeal_heuristics(message)

        if context:
            state = (
                f"【此前多轮对话记录（包含我方与对方的发言）】：\n"
                f"{context}\n\n"
                f"【对方当前最新消息】：\n"
                f"{message}\n\n"
                f"请务必结合上述完整多轮对话语境，客观研判对方此消息的诉求、时效压力、我方应对建议以及是否存在客观边界风险（隐私/合规/资金/承诺/人身威胁）。"
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
                    "instructions": "该消息是否存在人身安全、胁迫恐吓、隐私数据、绕流程违规、资金或口头兜底承诺风险？（请严格基于客观事实，催促或严肃不构成合规/隐私风险）",
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
                strategy_choice = rule_strat or "核验授权按流程办"
            elif risk_choice != "无明显风险" and risk_choice in RISK_CATEGORIES:
                has_risk = True
                risk_score = 9 if risk_choice == "人身安全" else (8 if risk_choice in ("数据与隐私", "流程与合规", "资金与权益", "胁迫与威胁") else 6)
                risk_warn = f"涉及{risk_choice}风险"
                if risk_choice == "人身安全":
                    strategy_choice = "立即报警并保留证据"
                elif risk_choice == "胁迫与威胁":
                    strategy_choice = "留存记录明确拒绝"
                elif strategy_choice in ("顺势承接礼貌回应", "先共情再探问"):
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

            appeal_conf = float(appeal_ans.get("confidence") or 0.0)
            confidence = appeal_conf if appeal_conf > 0 else 0.86

            evidence = []
            alternatives = []
            if rule_has_risk:
                evidence.append(f"直接风险证据：命中「{rule_cat}」关键词特征")
            if h_urgency != "常规无催":
                evidence.append(f"时间线索：{h_urgency}")
            if confidence < 0.55:
                alternatives.append("置信度较低，可能是委婉表达或缺少前情，需结合前文确认")

            appeal_tip = APPEALS.get(appeal_choice, "")
            urgency_tip = URGENCIES.get(urgency_choice, "")
            strategy_tip = STRATEGIES.get(strategy_choice, "")
            risk_tip = RISK_CATEGORIES.get(risk_choice, "")

            old_intent = map_appeal_to_intent(appeal_choice, message)

            summary_text = format_judgment_summary({
                "appeal": {"choice": appeal_choice},
                "urgency": {"choice": urgency_choice},
                "strategy": {"choice": strategy_choice},
                "risk": {"has_risk": has_risk, "category": risk_choice, "warning": risk_warn, "score": risk_score},
            })

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
                "confidence": confidence,
                "evidence": evidence,
                "alternatives": alternatives,
                # Backward-compatibility aliases
                "intent": old_intent,
                "intent_probs": {old_intent: confidence},
                "risk_probs": {},
                "actions": ACTION_MAP.get(old_intent, [strategy_choice]),
                "emotion": {
                    "choice": "就事论事" if not has_risk else "高度警惕",
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
                "summary": summary_text,
                "message": message,
                "backend": f"jev/{self.model}",
            }
            self._multi_cache[cache_key] = result
            return result
        except Exception as e:
            # 优雅降级：在离线或调用异常时走 LocalJudge 的完整逻辑
            local_fallback = LocalJudge()
            fallback_res = local_fallback.multi_judge(message, context=context)
            fallback_res["fallback"] = True
            fallback_res["error"] = str(e)
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
    """优先使用云端 Jev 模型；若未配置 Key、网络异常或请求失败，安全平滑降级到 LocalJudge。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.local = LocalJudge()
        self.fell_back = False
        self.reason = ""

    @property
    def backend_label(self) -> str:
        if self.fell_back or not jev_configured():
            return f"local (离线兜底: {self.reason or '无网络/Key'})"
        return f"Jev ({self.model})"

    def judge(self, message: str, context: str | None = None) -> dict:
        return self.multi_judge(message, context=context)

    def multi_judge(self, message: str, context: str | None = None) -> dict:
        if not self.fell_back and jev_configured():
            try:
                res = super().multi_judge(message, context=context)
                if not res.get("fallback"):
                    return res
            except Exception as e:
                self.fell_back = True
                self.reason = f"{type(e).__name__}: {str(e)[:40]}"
        res = self.local.multi_judge(message, context=context)
        res["backend"] = f"local (Jev 不可用回退: {self.reason or '无网络/Key'})"
        return res

    def rank_candidates(self, message: str, intent: str, candidates: list[str]) -> list[dict]:
        if not self.fell_back and jev_configured():
            try:
                return super().rank_candidates(message, intent, candidates)
            except Exception as e:
                self.fell_back = True
                self.reason = f"{type(e).__name__}: {str(e)[:40]}"
        return self.local.rank_candidates(message, intent, candidates)


JevJudge = Judge


def make_judge() -> Judge | LocalJudge | FallbackJudge:
    backend = (userconfig.get("JUDGE_BACKEND") or "").strip().lower()
    if backend == "local":
        return LocalJudge()
    return FallbackJudge()


if __name__ == "__main__":
    j = make_judge()
    msg = sys.argv[1] if len(sys.argv) > 1 else "这个需求你今天跟一下"
    t0 = time.perf_counter()
    try:
        res = j.judge(msg)
    except urllib.error.HTTPError as e:
        print(f"❌ HTTP {e.code} @ {j._last_url}\n   {e.read()[:300].decode(errors='replace')}")
        raise SystemExit(1)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    print(f"耗时 {time.perf_counter() - t0:.2f}s")
