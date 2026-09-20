"""waku-agent — 一个最小、透明、本地优先的 Waku。

中文导读：整个仓库就是下面这四根支柱，每根对应一组模块。
读代码顺着这张表往下走，就不会迷路。

Four pillars, one module each:
  harness  → waku/runtime + waku/gateway  (scaffolding around the raw LLM)
  loop     → waku/loop                      (observe → reason → act → repeat)
             waku/graph                     (opt-in structure around the loop — extends this pillar)
  memory   → waku/memory                    (procedural / semantic / episodic)
  ops      → waku/ops + evals/              (trace → eval → gate → release)

四根支柱具体是：
  harness（外壳） waku/runtime 组装"这一轮该看到什么"（SOUL.md 人格 + 相关记忆 +
                  最近对话），waku/gateway 只负责文本进出。包在裸 LLM 外面的脚手架。
  loop（循环）    waku/loop/agent.py 就是那个 while：想 → 调工具 → 看结果 → 再想。
                  waku/graph 是可选的结构化外壳（形状能提前画出来时用它，拓展同一支柱）。
  memory（记忆）  waku/memory：procedural 程序性（SKILL.md 怎么做）、semantic 语义
                  （事实）、episodic 情节（带日期的事件）。全部落在 .waku/state.db。
  ops（运维）     waku/ops + evals：trace（每轮都记）→ eval（对错打分）→ gate（够不够格
                  发布）→ release。回答"这次改动到底让 agent 变好了没有"。

一句话：harness 决定它看到什么，loop 决定它怎么做，memory 决定它记得什么，
ops 决定它有没有变好。
"""

# 全仓唯一的版本号来源：pyproject.toml 的 [tool.hatch.version] 就是读这里。
# 别在任何其他地方再写一个版本号 —— evals/deterministic/test_version.py 会拦住。
__version__ = "0.1.8"
