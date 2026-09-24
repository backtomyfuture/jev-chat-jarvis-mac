"""Chinese intent-judgment test: evaluate TypeSafe Jev model on 22 real messages.

Zero-shot remote regression test: verifies intent classification accuracy
and confidence against gold labels via TypeSafe Jev System One API.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from judge import INTENTS, Judge

# (message text, gold intent) — includes real WeChat messages and realistic variants
CASES: list[tuple[str, str]] = [
    ("这个需求你今天跟一下", "派活"),
    ("顺手把这个需求文档补一下", "派活"),
    ("明天把这个方案给客户发过去吧", "派活"),
    ("那个东西做完了吗", "催进度"),
    ("那个东西什么时候能好？", "催进度"),
    ("这块还没动呢？抓紧点", "催进度"),
    ("现在进度怎么样了", "问进度"),
    ("上线了吗", "问进度"),
    ("客户那边反馈如何", "问进度"),
    ("这个逻辑不对啊，你再看下", "批评"),
    ("怎么又出问题了", "批评"),
    ("这做的什么玩意", "批评"),
    ("为什么用这个方案？", "要解释"),
    ("你当时怎么想的", "要解释"),
    ("这个数据从哪来的", "要解释"),
    ("哈哈哈太搞笑了", "闲聊"),
    ("我周末去爬山了", "闲聊"),
    ("牛啊这也能写出来", "闲聊"),
    ("下午三点开个会同步一下", "约会议"),
    ("方便的话我们语音聊十分钟", "约会议"),
    ("这个做得不错，继续", "夸奖"),
    ("牛逼，这个思路好", "夸奖"),
]


def run_jev() -> dict:
    """TypeSafe Jev remote model via src/judge.py."""
    j = Judge()
    t0 = time.perf_counter()
    results = []
    for text, gold in CASES:
        try:
            res = j.judge(text)
            pred = res.get("intent")
            conf = float(res.get("confidence") or 0.0)
        except Exception as e:
            pred, conf = f"ERR:{type(e).__name__}", 0.0
        results.append({"text": text, "gold": gold, "pred": pred, "conf": conf})
    return {"model": f"jev/{j.model}", "elapsed_s": time.perf_counter() - t0, "results": results}


def summarize(run: dict) -> dict:
    res = run["results"]
    n = len(res)
    correct = sum(1 for r in res if r["pred"] == r["gold"])
    by_gold: dict[str, list[bool]] = {}
    for r in res:
        by_gold.setdefault(r["gold"], []).append(r["pred"] == r["gold"])
    return {
        "model": run["model"],
        "acc": correct / n,
        "n": n,
        "elapsed_s": round(run["elapsed_s"], 1),
        "per_intent": {k: round(sum(v) / len(v), 2) for k, v in by_gold.items()},
        "majority_baseline": round(max(
            sum(1 for r in res if r["gold"] == g) for g in set(r["gold"] for r in res)) / n, 3),
    }


def main() -> None:
    out_path = Path("results/judge_zh.json")
    out_path.parent.mkdir(exist_ok=True)
    report = {}

    print(f"\n===== jev (TypeSafe API) =====", flush=True)
    try:
        run = run_jev()
        s = summarize(run)
        report["jev"] = {"summary": s, "results": run["results"]}
        print(f"acc={s['acc']:.3f}  n={s['n']}  elapsed={s['elapsed_s']}s  "
              f"majority_baseline={s['majority_baseline']}")
        print("per-intent:", s["per_intent"])
        for r in run["results"]:
            flag = "OK " if r["pred"] == r["gold"] else "XX "
            print(f"  {flag}{r['text'][:22]:24s} gold={r['gold']:5s} "
                  f"pred={str(r['pred']):6s} conf={r['conf']:.2f}")
    except Exception as e:
        import traceback
        report["jev"] = {"error": f"{type(e).__name__}: {e}"}
        print("FAILED:", e)
        traceback.print_exc()

    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"\nsaved {out_path}")


if __name__ == "__main__":
    main()
