"""基准测试：跑一组固定任务，量出延迟、首 token、token 与成本。

为什么要留这个脚本：简历上写"P50 1.3s"，面试官下一句一定是"怎么测的、多少
样本"。这个脚本就是答案——固定任务集、固定配置、可重复跑，环境隔离在
`.waku-bench/`（已 gitignore），不污染你的真实记忆。

用法：
    .venv/Scripts/python.exe scripts/benchmark.py            # 跑默认 12 条
    .venv/Scripts/python.exe scripts/benchmark.py --json out.json

隔离环境：默认把 WAKU_HOME 指到 .waku-bench，所以结果里没有你历史记忆的
影响，也不会有基准数据写进你的真实库。想用真实记忆跑就加 --real-home。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

# 必须在 import waku 之前设置：config.py 在导入时就读 WAKU_HOME
if "--real-home" not in sys.argv:
    os.environ["WAKU_HOME"] = ".waku-bench"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 覆盖四类真实用法：不调工具、查、写、联网。任务文本固定，便于重复对比。
TASKS = [
    ("闲聊", "用一句话解释什么是向量数据库"),
    ("闲聊", "今天心情不错，随便聊两句"),
    ("闲聊", "把这句话改得更简洁：我打算明天上午十点的时候去一趟公司"),
    ("查日程", "我这周有什么安排？"),
    ("查日程", "明天下午有空吗？"),
    ("建日程", "帮我约周五上午十点跟 Alex 开个会，讨论下季度计划"),
    ("建日程", "下周二下午三点提醒我给客户回电话"),
    ("记事", "记住：我的生日是 3 月 14 日"),
    ("记事", "记一下，Alex 是我在公司的同事，负责后端"),
    ("联网", "帮我搜一下 Python 3.14 有哪些新特性"),
    ("联网", "查一下今天有什么科技新闻"),
    ("综合", "帮我在网上找一下下周有哪些 AI 相关的会议，然后把最值得去的记到日历里"),
]


def percentile(values: list[float], pct: float) -> float:
    """线性插值分位数。样本少时也稳定，不依赖 numpy。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * pct
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def read_ledger(home: Path) -> list[dict]:
    path = home / "usage.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path, help="把结果写成 JSON")
    parser.add_argument("--real-home", action="store_true",
                        help="用真实记忆跑（默认隔离在 .waku-bench）")
    args = parser.parse_args()

    from waku.app import Waku
    from waku.ops.pricing import price_for

    home = Path(os.getenv("WAKU_HOME", ".waku"))
    print(f"环境: WAKU_HOME={home}  任务数: {len(TASKS)}\n")

    ledger_before = len(read_ledger(home))
    waku = Waku()
    waku.session.session_id = f"bench-{time.strftime('%Y%m%d-%H%M%S')}"
    # 模型写进结果里：少了它，那堆延迟数字就无法复现也无法解释
    model_used = waku.settings.model
    small_used = waku.settings.small_model
    print(f"模型: {model_used} (provider={waku.settings.provider})")
    print(f"打杂模型: {small_used}\n")

    rows = []
    try:
        for i, (category, task) in enumerate(TASKS, 1):
            first_token: list[float | None] = [None]
            started = time.perf_counter()

            def observe(kind, ev, _ft=first_token, _t0=started):
                if kind == "text" and _ft[0] is None and ev.get("delta"):
                    _ft[0] = time.perf_counter() - _t0

            # stream=True 是必须的：首 token 延迟只能在流式路径上量到，
            # 不传的话 text 事件永远不会触发（第一版基准就踩了这个坑）。
            result = waku.respond(task, observer=observe, source="benchmark", stream=True)
            wall = time.perf_counter() - started

            rows.append({
                "category": category,
                "task": task,
                "wall_s": round(wall, 3),
                "first_token_s": round(first_token[0], 3) if first_token[0] else None,
                "iterations": result.iterations,
                "tools": [c["tool"] for c in result.tool_calls],
                "ok": bool(result.reply),
                "streamed": first_token[0] is not None,
            })
            mark = "ok " if result.reply else "空!"
            ft = f"{first_token[0]:.2f}s" if first_token[0] else "  —  "
            print(f"  [{i:2d}/{len(TASKS)}] {mark} {wall:5.2f}s  首token {ft}  "
                  f"迭代{result.iterations}  {','.join(rows[-1]['tools']) or '无工具'}  | {task[:34]}")
    finally:
        waku.close()

    # 每轮 token 与成本从账本里取（只取本次跑出来的那一段）
    ledger = read_ledger(home)[ledger_before:]
    # 账本按调用顺序记录，但轮与轮之间没有分隔标记（一轮可能触发多次 LLM
    # 调用），所以单轮成本只能取平均，总量是精确的。诚实胜过精巧的猜测。
    loop_calls = [e for e in ledger if e.get("kind") == "loop"]
    gate_calls = [e for e in ledger if e.get("kind") == "gate"]

    def cost_of(entries: list[dict]) -> float:
        total = 0.0
        for e in entries:
            p_in, p_out = price_for(e.get("provider", ""), e.get("model", ""))
            total += (e.get("in", 0) / 1_000_000) * p_in
            total += (e.get("out", 0) / 1_000_000) * p_out
        return total

    latencies = [r["wall_s"] for r in rows]
    firsts = [r["first_token_s"] for r in rows if r["first_token_s"]]

    summary = {
        "setup": {
            "home": str(home),
            "provider": os.getenv("WAKU_PROVIDER", "anthropic"),
            "model": model_used,
            "small_model": small_used,
            "tasks": len(TASKS),
            "streaming": sum(1 for r in rows if r["streamed"]) == len(rows),
        },
        "latency_s": {
            "mean": round(statistics.mean(latencies), 3),
            "p50": round(percentile(latencies, 0.50), 3),
            "p95": round(percentile(latencies, 0.95), 3),
            "min": round(min(latencies), 3),
            "max": round(max(latencies), 3),
        },
        "first_token_s": {
            "mean": round(statistics.mean(firsts), 3) if firsts else None,
            "p50": round(percentile(firsts, 0.50), 3) if firsts else None,
            "p95": round(percentile(firsts, 0.95), 3) if firsts else None,
            "min": round(min(firsts), 3) if firsts else None,
            "max": round(max(firsts), 3) if firsts else None,
        },
        "tokens": {
            "llm_calls": len(loop_calls),
            "gate_calls": len(gate_calls),
            "in_total": sum(e.get("in", 0) for e in loop_calls),
            "out_total": sum(e.get("out", 0) for e in loop_calls),
            "in_per_turn": round(sum(e.get("in", 0) for e in loop_calls) / len(rows), 1),
            "out_per_turn": round(sum(e.get("out", 0) for e in loop_calls) / len(rows), 1),
            "calls_per_turn": round(len(loop_calls) / len(rows), 2),
        },
        "cost_usd": {
            "total": round(cost_of(ledger), 6),
            "per_turn": round(cost_of(ledger) / len(rows), 6),
        },
        "tools_per_turn": round(sum(len(r["tools"]) for r in rows) / len(rows), 2),
        "empty_replies": sum(1 for r in rows if not r["ok"]),
        "by_category": {
            cat: {
                "n": len(by_cat := [r for r in rows if r["category"] == cat]),
                "p50": round(percentile([r["wall_s"] for r in by_cat], 0.50), 3),
                "mean": round(statistics.mean([r["wall_s"] for r in by_cat]), 3),
            }
            for cat in dict.fromkeys(c for c, _ in TASKS)
        },
        "rows": rows,
    }

    print("\n" + "=" * 72)
    print(f"端到端延迟   P50 {summary['latency_s']['p50']}s   "
          f"P95 {summary['latency_s']['p95']}s   "
          f"均值 {summary['latency_s']['mean']}s   "
          f"(min {summary['latency_s']['min']}s / max {summary['latency_s']['max']}s)")
    tts = summary["first_token_s"]
    print(f"首 token     P50 {tts['p50']}s   P95 {tts['p95']}s   "
          f"均值 {tts['mean']}s   (min {tts['min']}s / max {tts['max']}s)")
    tk = summary["tokens"]
    print(f"每轮 token   输入 {tk['in_per_turn']}  输出 {tk['out_per_turn']}   "
          f"LLM 调用 {tk['calls_per_turn']} 次/轮")
    print(f"成本         ${summary['cost_usd']['per_turn']} / 轮   "
          f"${summary['cost_usd']['total']} / 全程")
    print(f"工具调用     {summary['tools_per_turn']} 次/轮   空回复 {summary['empty_replies']}")
    print("-" * 72)
    print("分类 P50：", "   ".join(
        f"{cat} {v['p50']}s(n={v['n']})" for cat, v in summary["by_category"].items()))
    print("=" * 72)

    if args.json:
        args.json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果已写入 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
