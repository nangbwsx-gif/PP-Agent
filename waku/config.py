"""
配置——每一个可调项都是环境变量，并在 .env.example 中有文档说明。

没有设置框架：只是一个在启动时读取一次的 dataclass。如果你能读懂这个文件，你就知道 Waku 可以被配置成做什么。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import find_dotenv, load_dotenv


def _load_env() -> str:
    """
    以用户期望的方式查找用户的 .env 文件：从用户“所在”的位置开始。
    裸调用 load_dotenv() 会从调用它的那个文件所在位置向上搜索，而不是从当前工作目录搜索。
    在 git 仓库里这问题看不出来——config.py 就在项目里，从它向上走会落到项目的 .env 上，一切正常。
    但从 PyPI 安装后，它会从 site-packages 向上走，一直走到文件系统根目录，什么也找不到：
    用户明明站在一个放着完好 .env 的文件夹里，Waku 却报“没有 API key”。这个问题在 2026-07-31 从一次干净安装中被报告，而且就是从仓库文件夹内部报告的。
    usecwd=True 就是全部的修复。向上搜索是故意保留的，这样从项目的子目录运行 waku 时，仍然能找到项目根目录的 .env——和 git、npm、pytest 已经教会大家的规则一样。
    返回被加载的路径（如果没有则返回空字符串），这样 waku doctor 和首次运行的错误信息就能说出到底读了哪个文件，而不是让人在三个 .env 文件之间猜来猜去。
    """
    path = find_dotenv(usecwd=True)
    if path:
        load_dotenv(path)
    return path


DOTENV_PATH = _load_env()


@dataclass
class Settings:
    
    # --- LLM：选择一个提供商，设置其密钥。参见 waku/loop/models.py 中的 PROVIDERS。
    provider: str = field(default_factory=lambda: os.getenv("WAKU_PROVIDER", "anthropic"))
    # 显式覆盖（可选）：密钥、端点和模型 ID。留空时，将使用提供商自己的密钥环境变量和默认模型。
    api_key: str = field(default_factory=lambda: os.getenv("WAKU_API_KEY", ""))
    base_url: str | None = field(default_factory=lambda: os.getenv("WAKU_BASE_URL") or None)
    model: str = field(default_factory=lambda: os.getenv("WAKU_MODEL", ""))
    # 用于检索门和整合摘要器的廉价模型。
    small_model: str = field(default_factory=lambda: os.getenv("WAKU_SMALL_MODEL", ""))
    # 用户在仪表板中关闭的提供商（逗号分隔的 ID）。
    # 已禁用的提供商会从选择器/切换器中隐藏；当前活动的提供商
    # 不能被禁用（在 integrations.apply_provider_disabled 中有保护）。
    disabled_providers: frozenset[str] = field(default_factory=lambda: frozenset(
        p.strip() for p in os.getenv("WAKU_DISABLED_PROVIDERS", "").split(",") if p.strip()))

    # --- Home：Waku 保存其状态的位置（记忆数据库、日历、发件箱、追踪）。
    # 默认在运行目录旁边的 ./.waku，这样你可以打开它写入的每个文件。
    # 本地优先意味着你总能查看。
    home: Path = field(default_factory=lambda: Path(os.getenv("WAKU_HOME", ".waku")))

    # --- 循环保护措施
    max_iterations: int = field(default_factory=lambda: int(os.getenv("WAKU_MAX_ITERATIONS", "10")))
    # 对于推理模型（kimi-k3、gpt-5.x、gemini-*-pro）来说，余量很重要：
    # 它们会在回答前花费输出 token 进行思考，因此上限过低会使
    # 它们在思考中途达到 stop_reason=max_tokens 并返回空回复
    # （观察到 kimi-k3 在 2048 时正是如此）。8192 为思考和回答
    # 都留出了空间；它是上限而非目标，所以高效模型的成本仍然相同。
    max_tokens: int = field(default_factory=lambda: int(os.getenv("WAKU_MAX_TOKENS", "8192")))
    # 工作记忆是一个滑动窗口（类似上下文 RAM）：只有最近 N 轮
    # 进入提示。更早的轮次不会丢失——它们保存在 state.db 中，
    # 由整合过程提炼为事实，并在相关时由检索门拉回。
    # 没有这个上限，长线程（尤其是常开的网关会话）会每轮重发整个历史，直到爆炸。
    history_turns: int = field(default_factory=lambda: int(os.getenv("WAKU_HISTORY_TURNS", "12")))

    # --- 记忆
    # 仅在 N 次新交换后整合（将聊天提炼为持久事实）。
    consolidate_every: int = field(default_factory=lambda: int(os.getenv("WAKU_CONSOLIDATE_EVERY", "6")))
    retrieval_top_k: int = field(default_factory=lambda: int(os.getenv("WAKU_RETRIEVAL_TOP_K", "4")))
    # 'sqlite'（默认，零配置）或 'mem0' / 'zep'（托管后端——见 docs/memory-backends-playbook.md）。
    semantic_store: str = field(default_factory=lambda: os.getenv("WAKU_SEMANTIC_STORE", "sqlite"))
    # 'sqlite'（默认，零配置）或 'notion'（片段存放在 Notion 数据库中）。
    episodic_store: str = field(default_factory=lambda: os.getenv("WAKU_EPISODIC_STORE", "sqlite"))

    # --- 工具
    # 将本地创建的事件镜像到 Google 日历。SQLite + ICS 仍是
    # 事实来源；这只是一个选择加入的写入目标。
    google_calendar: bool = field(
        default_factory=lambda: os.getenv("WAKU_GOOGLE_CALENDAR", "") in ("1", "true", "yes")
    )
    google_calendar_id: str = field(
        default_factory=lambda: os.getenv("WAKU_GOOGLE_CALENDAR_ID", "") or "primary"
    )
    # 通过 `gh` CLI 自身的认证进行只读 GitHub 访问（此处无需令牌）。
    # 默认关闭且有意如此：每个注册的工具都会出现在每个
    # 提示中，而读取 PR 是维护者能力，不是助手能力。
    # gather 工作流以库的形式调用 waku/tools/github.py，不需要
    # 开启此开关——该开关只决定模型能否访问它。
    gh_tool: bool = field(
        default_factory=lambda: os.getenv("WAKU_GH_TOOL", "") in ("1", "true", "yes")
    )
    # 当调用省略 owner/name 时假定的值——用于 Waku 在
    # 检出目录之外运行，此时 `gh` 没有可推断的远程仓库。
    gh_repo: str = field(default_factory=lambda: os.getenv("WAKU_GH_REPO", ""))
    # 注册实验性工具（delegate_task -> pi 子代理，...）。环境变量是
    # 全局开关；竞技场按比赛设置此项，这样编码比赛可以将工作
    # 交给 pi，而无需为整个进程打开它。
    experimental: bool = field(
        default_factory=lambda: os.getenv("WAKU_EXPERIMENTAL", "") in ("1", "true", "yes")
    )
    # 先将每条消息路由通过分诊图工作流（一个小模型对其进行分类；
    # 琐碎消息获得快速的小模型回复，真实任务作为图节点运行正常循环）。
    # 任何地方的任何失败都会失败开放到普通循环，所以这永远不会让 Waku 变差——只会更快/更便宜。
    graph_workflows: bool = field(
        default_factory=lambda: os.getenv("WAKU_GRAPH_WORKFLOWS", "") in ("1", "true", "yes")
    )

    # --- 追踪（始终 JSONL；如果设置了端点则导出 OTel）
    otel_endpoint: str = field(
        default_factory=lambda: os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    )
    
    def ensure_home(self) -> Path:
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "traces").mkdir(exist_ok=True)
        (self.home / "outbox").mkdir(exist_ok=True)
        return self.home


def load_settings() -> Settings:
    return Settings()
