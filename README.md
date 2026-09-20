# PP-Agent

部署在你自己电脑上的本地 AI 助手。手机或电脑随时对话，日程、记事和长期记忆全部留在本机，不经过任何云服务。

- **本地优先** — 所有状态都在一个 SQLite 文件里（`.waku/state.db`），可以自己打开、备份、删除
- **长期记忆** — 不是每次对话都从零开始。它记住你说过的事，需要时自己翻出来
- **模型不锁定** — 支持 11 家模型供应商，用哪个自己定，改一行配置就能换
- **中文界面** — 网页控制台提供中英双语，工具说明也是中文

---

## 环境要求

- Python **3.11 或更高**
- 一个模型供应商的 API Key（推荐 DeepSeek，便宜且中文好）
- Windows / macOS / Linux 都可以

---

## 安装

### 1. 拿到代码并建虚拟环境

```bash
git clone https://github.com/nangbwsx-gif/PP-Agent.git
cd PP-Agent

# 方式一：用 uv（推荐，快很多）
uv venv
uv pip install -e .

# 方式二：用内置 venv + pip
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux
pip install -e .
```

### 2. 配置模型

在**项目根目录**新建 `.env` 文件（这个文件不会进版本库）：

```ini
WAKU_PROVIDER=deepseek
DEEPSEEK_API_KEY=你的key

# 可选：指定模型。不写就用供应商默认值
WAKU_MODEL=deepseek-v4-pro        # 主模型：真正回答你的那个
WAKU_SMALL_MODEL=deepseek-flash   # 打杂模型：判断要不要翻记忆、提炼事实
```

**支持的供应商**（把 `WAKU_PROVIDER` 换成对应名字，并填上它的 Key）：

| 供应商 | `WAKU_PROVIDER` | Key 变量名 |
|---|---|---|
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` |
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY` |
| OpenAI | `openai` | `OPENAI_API_KEY` |
| Google Gemini | `gemini` | `GEMINI_API_KEY` |
| Kimi（月之暗面） | `kimi` | `MOONSHOT_API_KEY` |
| 智谱 GLM | `glm` | `ZHIPU_API_KEY` |
| MiniMax | `minimax` | `MINIMAX_API_KEY` |
| xAI Grok | `xai` | `XAI_API_KEY` |
| OpenRouter | `openrouter` | `OPENROUTER_API_KEY` |
| OpenCode Zen / Go | `opencode_zen` / `opencode_go` | `OPENCODE_ZEN_API_KEY` / `OPENCODE_GO_API_KEY` |

配置文件里所有可调项都写在 `.env.example` 里，每一条都有注释。

### 3. 跑起来

```bash
# 终端对话
.venv\Scripts\python.exe -m waku

# 网页控制台（中文）
.venv\Scripts\python.exe -m waku dashboard --zh
```

浏览器打开 **http://localhost:7777**。

> Windows 上没有 `make`，所以上面都直接用 `python -m`。macOS / Linux 可以用仓库里 Makefile 的简写，比如 `make run`、`make dashboard`。

---

## 使用

| 命令 | 做什么 |
|---|---|
| `python -m waku` | 终端里对话 |
| `python -m waku dashboard --zh` | 中文网页控制台 → localhost:7777 |
| `python -m waku dashboard --zh --dev` | 同上，额外显示开发者页面（图谱、运维、数据库等） |
| `python -m waku voice` | 语音对话（需要先装 `[voice]` 依赖） |
| `python -m waku brief` | 生成一份晨间摘要（日历 + 记忆），可挂到系统计划任务 |
| `python -m waku gather` | 并行扫描 GitHub／网页／日历／记忆，汇总成一份摘要 |
| `python -m waku connections` | 查看所有集成的配置状态和健康情况 |

### 界面说明

- 默认（用户模式）只显示 4 个页面：**总览、记忆、工具、行为**
- `--dev` 展开全部 13 个页面，包含图谱、循环、运维、数据库、模型配置等
- 也可以在**行为**页里用开关切换开发者模式（界面上切换的优先级高于启动参数）
- 右侧聊天面板在任何页面都能用

### 它会做什么

装好就有 8 个工具可用：

- **日程** — 建日程、查日程（本地日历，接上 Google Calendar 后可同步）
- **记事** — 把值得记住的事写进长期记忆
- **消息** — 起草消息到本地发件箱，你自己过目后再发
- **联网** — 搜网页（默认走 DuckDuckGo，配了 Tavily key 效果更好）
- **自我管理** — 修正/遗忘记忆、保存行为规则、自己写新技能

---

## 数据存在哪

所有运行时数据都在项目下的 `.waku/` 目录，**已加入 `.gitignore`，不会被提交**：

```
.waku/
├── state.db      记忆、对话记录、日程（一个 SQLite 文件）
├── SOUL.md       人格文件，改它就改变了它的性格
├── MEMORY.md     人能直接读的记忆摘要（自动生成）
├── skills/       你教给它的技能
├── traces/       每轮对话的结构化日志
├── usage.jsonl   每次调用的 token 与费用账本
└── outbox/       起草的消息和摘要
```

想换位置就设 `WAKU_HOME` 环境变量。

---

## 测试

```bash
.venv\Scripts\python.exe -m pytest -q evals/deterministic    # 705 个离线用例，不需要 Key
.venv\Scripts\python.exe -m ruff check waku evals scripts    # 代码检查
```

> Windows 上有 3 个用例会失败（系统临时文件与子进程相关的上游问题），另有 2 个文件需要 `-X utf8` 才能读 GBK 环境下的文本。详见 `docs/status.md`。

---

## 项目结构

```
waku/
├── gateway/     文本的进出（终端、语音、网页控制台）
├── loop/        Agent 主循环：想 → 调工具 → 看结果 → 再想
├── graph/       可选的结构化工作流（形状固定的任务）
├── memory/      三层记忆：语义 / 情节 / 程序性 + 检索决策 + 自动提炼
├── runtime/     每一轮的工作记忆组装
├── tools/       它能调用的工具
├── ops/         可观测性、评测、网页控制台
└── config.py    所有可调项（读 .env）
```

更深入的说明看 `docs/`：`architecture.md`（架构）、`getting-started.md`（上手指南）、`evals.md`（评测与追踪）。

---

## 许可

代码采用 MIT 许可，见 [LICENSE](LICENSE)。仓库内自带的字体为 SIL OFL 1.1 许可。
