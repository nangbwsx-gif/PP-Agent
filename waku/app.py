"""装配层 —— 把一个 Waku 从它的零件拼起来。所有 gateway 都调 `respond()`。

中文导读（先看这三条，再往下读代码）：
  1. `Waku()` 构造时按固定顺序装配零件，顺序就是依赖顺序：
       config → 建 .waku/ → db → client(模型客户端) → memory → tools → session → tracer
     注意 memory 排在 tools 前面：三个"管记忆"的工具要拿它当构造参数才造得出来。
  2. `respond()` 是唯一对外的入口，一轮对话 = 组装工作记忆 → 跑 loop → 落库。
     CLI / dashboard / voice 三个 gateway 都只做两件事：把文本递进来、把文本送出去。
  3. 这个文件不实现记忆、也不实现 loop，只负责"接线"和"一轮的时序"。
     想读懂整个仓库，从这里开始是对的路。
"""

from __future__ import annotations

from waku.config import Settings, load_settings
from waku.db import connect
from waku.loop.agent import LoopResult, Observer, run_loop
from waku.loop.models import get_client
from waku.ops.tracing import Tracer, compose
from waku.runtime.session import Session
from waku.tools import build_registry


class Waku:
    def __init__(self, settings: Settings | None = None, client=None, conn=None):
        # ── 装配：下面这几行就是全部依赖关系，从上到下 ──────────────────────
        # client 和 conn 可注入：eval 塞一个脚本化的假模型，dashboard 注入跨线程
        # 的连接。同一个接缝，两种用途。
        self.settings = settings or load_settings()        # 读 .env + 默认值（见 config.py）
        self.settings.ensure_home()                        # 建 .waku/ traces/ outbox/
        self.conn = conn or connect(self.settings.home)    # 一个 SQLite 文件 = 全部记忆
        self.client = client or get_client(self.settings)  # 模型客户端（按 provider 适配）

        # 记忆必须最先建：下面三个"自我管理"工具要拿它当构造参数。
        from waku.memory import Memory

        self.memory = Memory(self.conn, self.settings, self.client)
        self.tools = build_registry(self.conn, self.settings, self.memory)  # 工具注册表
        self.mcp_bridge = getattr(self.tools, "mcp_bridge", None)          # 外部 MCP 子进程句柄
        self.session = Session(self.settings, memory=self.memory)          # 工作记忆（每轮重建）
        self.tracer = Tracer(self.settings)                                # 每个事件写一行 JSONL

    def close(self) -> None:
        """只关外部资源：MCP 子进程和 SQLite 连接。可重复调用。

        host 在两处会调它：设置变更后换掉旧实例，以及进程退出时。以前只关 MCP
        子进程、把连接留给操作系统 —— 一个打算常驻的进程不该这么干。
        """
        if self.mcp_bridge is not None:
            self.mcp_bridge.close()
        if self.conn is not None:
            self.conn.close()   # sqlite3 重复 close 是空操作

    def respond(self, user_message: str, observer: Observer | None = None,
                source: str = "cli", stream: bool = False) -> LoopResult:
        """一轮完整对话：组装工作记忆 → 跑 loop → 落库。

        参数：
          source   —— 这条消息从哪个 gateway 来的（cli / voice / dashboard），
                      统一对话界面上靠它显示来源。
          stream   —— 回复逐字吐给 observer（dashboard 拿它做打字机效果）。
          observer —— 界面回调。每个事件既给它看，也照样写进 trace。

        时序 —— 本函数的骨架，读代码时对着这五步看：
          1. 起 tracer.turn()：之后每个事件既给 observer（界面），又写 trace
          2. 可选前门：过 triage 图（闲聊用便宜小模型快答），任何异常都掉回第 3 步
          3. 否则 _run_full_turn()：装 system prompt + 滑窗历史 → run_loop（真正的循环）
          4. 组装 meta：gate 决策、图路由、耗时、工具、这一轮用的模型
          5. 存消息，然后跑记忆巩固、刷新 MEMORY.md
        """
        # 顺手把"检索 gate 决策"和"图路由"抄进 captured，好跟着这一轮一起入库
        # （dashboard 重开旧对话时显示的，就是这个遥测）
        import time
        captured: dict = {}

        def _capture(kind, ev):
            # 只留这四类事件：检索 gate、图路由、triage 理由、图路径
            if kind == "gate":
                captured["gate"] = {"decision": ev.get("decision"), "reason": ev.get("reason")}
            if kind == "route":
                captured["graph_route"] = {"target": ev.get("target"), "reason": ev.get("reason")}
            if kind == "triage":
                captured["triage_reason"] = ev.get("reason")
            if kind == "graph_end":
                captured["graph_path"] = ev.get("path")
        # notify = 同一条事件流有三个去处：界面(observer) / trace / 上面的 captured
        notify = compose(observer, self.tracer.event, _capture)
        t0 = time.perf_counter()   # 计时起点，最后写进 meta["latency_ms"]

        with self.tracer.turn(user_message):   # 出这个 with 就封口这一轮的 trace
            # 图前门是可选的，而且"只会更好、不会更坏"：开关关着 = 与以前完全相同的
            # 代码路径；开着 = 由 triage 图判断快答还是全流程，中途任何失败都掉回下面的
            # 普通 loop（和检索 gate 同一条 fail-open 原则：出错就退回老实路径）。
            result = None
            if self.settings.graph_workflows:
                try:
                    result = self._respond_via_graph(user_message, notify, stream)
                except Exception as exc:
                    notify("graph_end", {"workflow": "triage", "ms": 0, "steps": 0,
                                         "path": [], "error": repr(exc)})
                    result = None
            if result is None:
                result = self._run_full_turn(user_message, notify, stream)

            quick = captured.get("graph_route", {}).get("target") == "quick_reply"

            def _status(out: str) -> str:
                """工具输出没有结构化状态码，只能按关键字猜成功还是失败。"""
                low = (out or "").lower()
                return "error" if ("failed" in low or "timed out" in low
                                   or low.startswith("error")) else "ok"
            # meta = 这一轮的"体检报告"，落库后 dashboard 每张对话卡片靠它显示细节
            meta = {
                "gate": captured.get("gate"),            # 检索 gate：跳还是取，以及理由
                "graph": ({"workflow": "triage",         # 图路由：quick 还是 full、走了哪些节点
                           "route": "quick" if quick else "full",
                           "reason": captured.get("triage_reason", ""),
                           "path": captured.get("graph_path")}
                          if "graph_route" in captured else None),
                "iterations": result.iterations,          # loop 转了几圈
                "latency_ms": int((time.perf_counter() - t0) * 1000),   # 端到端耗时
                "tools": [{"tool": c["tool"], "status": _status(c["output"])}
                          for c in result.tool_calls],    # 这轮调了哪些工具、成功与否
                # 这一轮到底是哪个模型答的 —— 重开旧对话（或中途换过模型）时按卡片显示。
                # 图的快答分支由小模型回答，这里就如实写小模型。
                "model": self.settings.small_model if quick else self.settings.model,
                "provider": self.settings.provider,
            }
            # 落这一轮：用户消息 + 回复 + 工具调用 + 上面的 meta，并按 source 标注来源
            self.session.add_exchange(user_message, result.reply, tool_calls=result.tool_calls,
                                      source=source, meta=meta)
            if self.memory is not None:
                # 攒够 N 轮才蒸馏成持久事实（默认每 6 轮一次，省钱）
                self.memory.maybe_consolidate(notify=notify)
                self.memory.export_markdown()   # 刷新可读版 MEMORY.md（真源仍是 state.db）

        self.tracer.end_turn(result.reply, result.iterations)   # 收尾：补一行 turn 总结
        return result

    def _run_full_turn(self, user_message: str, notify, stream: bool) -> LoopResult:
        """经典的完整回合：装工作记忆，跑 THE loop（loop/agent.py 那个 while）。

        为什么单独拆成一个函数：图里的 full 分支必须调用"和默认路径完全相同"的
        代码，否则两条路会各自演化、慢慢跑偏。
        """
        # system prompt = SOUL.md（人格）+ 本轮该带的记忆 + 当前时间 + 我是谁
        system = self.session.build_system(user_message, notify=notify)
        # 工作记忆是有界的滑窗：只把最近 N 轮（每轮 2 行：你说的话 + 它回的话）放进
        # prompt，所以聊多久上下文/成本/延迟都不涨。更早的内容没丢 —— 它们在
        # state.db 里，需要时由检索 gate + 情节记忆捞回来。
        window = self.settings.history_turns * 2
        messages = self.session.history[-window:] + [{"role": "user", "content": user_message}]

        # 真正进循环：想 → 动手 → 看结果 → 再想，直到模型不再要工具或到轮数上限
        return run_loop(
            client=self.client,
            model=self.settings.model,
            system=system,
            messages=messages,
            tools=self.tools,
            max_iterations=self.settings.max_iterations,
            max_tokens=self.settings.max_tokens,
            observer=notify,
            stream=stream,
        )

    def _respond_via_graph(self, user_message: str, notify, stream: bool) -> LoopResult | None:
        """走 triage 图工作流的路径。图没给出答案就返回 None —— 上层 respond()
        随即掉回普通 loop，所以这条路只可能"更快"，不会丢回复。
        """
        from waku.graph import run_graph
        from waku.graph.workflows.triage import (
            QUICK_REPLY_PROMPT,
            build_triage_graph,
            classify_message,
            todays_events,
        )

        def quick_reply(state: dict) -> str:
            # "快答"分支：不开工具、只带今天的日历，用便宜的小模型一次问完。
            # 闲聊、"今天几号"这类问题走这里，省掉一整圈 loop 的钱和时间。
            prompt = QUICK_REPLY_PROMPT.format(calendar=state.get("calendar", ""),
                                               message=state["message"])
            response = self.client.messages.create(
                model=self.settings.small_model, max_tokens=600,
                messages=[{"role": "user", "content": prompt}])
            return "".join(b.text for b in response.content if b.type == "text")

        # 四个节点回调：classify 判快/慢，calendar 拿今天日程，quick 直接答，full 进真正的 loop
        graph = build_triage_graph(
            classify_fn=lambda m: classify_message(self.client, self.settings.small_model, m),
            calendar_fn=lambda: todays_events(self.settings.home),
            quick_fn=quick_reply,
            # full 分支调的就是"开关关闭时那个默认方法"，保证两条路不跑偏；
            # 引擎的 notifier 会给里面产生的事件打上 node= 标签（图表里能看到）
            full_fn=lambda state: self._run_full_turn(
                state["message"], state.get("_notify", notify), stream),
        )
        state = run_graph(graph, {"message": user_message}, observer=notify)
        # 图跑完了，把结果翻译成 LoopResult；三种情况都要覆盖，见下
        if isinstance(state.get("result"), LoopResult):
            return state["result"]        # full 分支跑过真正的 loop，原样返回
        if state.get("reply"):
            return LoopResult(reply=state["reply"], tool_calls=[], iterations=1)  # quick 分支
        return None  # 图什么都没产出 → 交给调用方掉回普通 loop（fail open）
