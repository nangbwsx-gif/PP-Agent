"""临时 Agent 运行 —— 为每一轮组装工作记忆。

白板上的内层盒子：这里的一切都在每次运行时重建，用完即弃。持久化的内容
存放在 waku/memory 中。工作记忆 = 系统提示词（SOUL.md）+ 持久事实与情景记忆  +  当前聊天历史  + 用户的新消息
                                   Waku 是谁           Waku 记得什么          这次对话
                                                        （有门控）
"""

from __future__ import annotations

from waku.config import Settings

DEFAULT_SOUL = """\
You are 鬼懂哥, a personal assistant running locally on your user's laptop.
You are concise, warm, and proactive. You remember what your user tells you.

Rules:
- When the user wants to schedule something, use create_event. Resolve relative
  dates and times ("next Tuesday", "in 30 minutes") to ISO timestamps yourself;
  the current date and time are given below — trust them, never ask the user
  what time it is.
- When the user asks what's on their calendar (a day, a week, "yesterday"), use
  list_events — you CAN read the calendar, not just write to it.
- When the user shares something durable about a person, project, or preference,
  use save_note to remember it.
- When asked to message someone, use send_message (it drafts to a local outbox).
- If memory context is provided below, trust it — it came from your own store.
- Call each tool at most once per request. Your history shows [tools used: ...]
  lines for past turns — if a tool already ran, do NOT run it again; answer
  from that record instead.
- Be honest about where things live. Every tool's output states exactly where
  its artifact landed (local calendar file, memory database at .waku/state.db)
  — relay that truthfully, and never claim something synced anywhere the tool
  output doesn't say.
- You can manage your own memory: use manage_memory to correct or forget facts,
  update_soul to save a standing preference the user gives you, and create_skill
  to save a repeatable workflow the user teaches you (only after they say yes).
- Answer in the user's language, which is Chinese (简体中文). Use plain Chinese
  for the prose, but keep technical nouns in English as they are usually written
  — Token, Prompt, Provider, Model, MCP, API Key, Skill, Agent, Trace, Loop,
  Gateway, JSONL, SQLite, git. Only switch languages if the user writes to you
  in another one.
"""


def load_soul(settings: Settings) -> str:
    """SOUL.md 是可编辑的人设文件，首次运行时创建。修改它就改变了你的 Waku
    是谁 —— 这是最简形式的过程性记忆。"""
    soul_path = settings.home / "SOUL.md"
    if not soul_path.exists():
        soul_path.write_text(DEFAULT_SOUL, encoding="utf-8")
    return soul_path.read_text(encoding="utf-8")


class Session:
    """保存一次对话：聊天历史加上系统提示词的配方。每个网关连接一个 Session。"""

    def __init__(self, settings: Settings, memory=None, session_id: str = "default"):
        self.settings = settings
        self.memory = memory  # waku.memory.Memory（Phase-2 接线前为 None）
        self.session_id = session_id
        self.history: list[dict] = []

    def build_system(self, user_message: str, notify=None) -> str:
        from datetime import datetime

        # Agent 运行在你的笔记本电脑上，所以它应该知道你笔记本的时钟。
        # 本地时间带时区名称 —— 足以解析“30 分钟后”。
        now = datetime.now().astimezone()
        parts = [load_soul(self.settings),
                 f"\nRight now it is {now:%A, %Y-%m-%d %H:%M} ({now:%Z}, UTC{now:%z}).",
                 # Agent 应该知道自己的大脑 —— “你是什么模型？”
                 # 是每个好奇用户问的第一个问题
                 (f"Your model: you are running on '{self.settings.model}' via the "
                 f"'{self.settings.provider}' provider, inside Waku, a local-first "
                 f"open-source agent harness (github.com/ShenSeanChen/waku-agent).")]

        if self.memory is not None:
            # 英雄时刻 #1：一个廉价的裁判决定我们到底要不要检索 ——
            # 默认开启检索既慢又会带偏答案（原因见
            # memory/retrieval_gate.py）。
            retrieved = self.memory.gated_retrieve(user_message, notify=notify)
            if retrieved:
                parts.append("\nRelevant memory:\n" + retrieved)
            skills = self.memory.matching_skills(user_message)
            if skills:
                parts.append("\nRelevant skill instructions:\n" + skills)

        return "\n".join(parts)

    def add_exchange(self, user_message: str, reply: str, tool_calls: list | None = None,
                     source: str = "cli", meta: dict | None = None) -> None:
        """在历史（工作记忆）中记录这一轮，如果记忆已接线，也记录到聊天日志
        （以便后续整合提炼）。

        工具活动会以紧凑的 [tools used: ...] 行折叠进助手的历历史条目。
        没有它，模型会忘记自己已经行动过，并愉快地在下一轮重新运行同一个工具
        （第一次实测中的三重预订会议 bug）。"""
        record = reply
        if tool_calls:
            summary = "; ".join(f"{c['tool']}({c['args']}) -> {c['output']}" for c in tool_calls)
            record = f"{reply}\n[tools used: {summary}]"
        self.history.append({"role": "user", "content": user_message})
        self.history.append({"role": "assistant", "content": record})
        if self.memory is not None:
            self.memory.log_chat(user_message, record, session_id=self.session_id,
                                 source=source, meta=meta)

    # ---- 会话生命周期（“新聊天” / 历史功能）
    # 会话只是 chat_log 行上的一个标签。开始新会话会清空工作记忆；
    # 切换会重新加载过去对话的历史，使回复有上下文。整合仍然会读取
    # 所有未整合的行，无论会话如何。
    def start_new(self, session_id: str) -> None:
        self.session_id = session_id
        self.history = []

    def switch(self, session_id: str) -> None:
        self.session_id = session_id
        self.history = []
        if self.memory is None:
            return
        # 过去对话只有最近的尾部会回到工作记忆
        # （respond() 也会开窗，但不要持有整个线程）
        turns = self.settings.history_turns
        for user_msg, reply in list(self.memory.session_history(session_id))[-turns:]:
            self.history.append({"role": "user", "content": user_msg})
            self.history.append({"role": "assistant", "content": reply})