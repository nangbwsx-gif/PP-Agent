// 中文文案表 —— 只有界面文字，没有逻辑。
//
// 规则（见 docs/ 里的说明，也是你和 Waku 的约定）：
//   * 专有名词保留英文：Token、Prompt、Model、Provider、MCP、API Key、Skill、
//     Agent、Trace、Loop、Gateway、JSONL、SQLite、Dashboard、eval、git
//   * 首次出现可中英混排：模型供应商（Provider）
//   * 动作词和界面词一律中文
//   * 每条都是人工写的，不是机器翻译
//
// key 的命名：区域.用途。英文原文不在这里 —— 它在代码里当 fallback 传进来，
// 所以漏翻一条只会显示英文原句，不会显示空白或 key。

const ZH = {
  // ── 侧边栏：分组
  "nav.group.system": "系统",
  "nav.group.arena": "竞技场",
  "nav.group.setup": "配置",

  // ── 侧边栏：系统
  "nav.overview": "总览",
  "nav.gateway": "网关",
  "nav.loop": "循环",
  "nav.graph": "图谱",
  "nav.memory": "记忆",
  "nav.tools": "工具",
  "nav.database": "数据库",
  "nav.ops": "运维",
  "nav.compare.models": "模型竞速",
  "nav.compare.memory": "记忆竞速",
  "nav.models": "模型",
  "nav.connections": "连接",
  "nav.settings": "行为",

  // ── 侧边栏：可访问性标签（屏幕阅读器 / 折叠后的提示）
  "nav.aria.overview": "总览",
  "nav.aria.gateway": "网关",
  "nav.aria.loop": "循环",
  "nav.aria.graph": "图谱",
  "nav.aria.memory": "记忆",
  "nav.aria.tools": "工具",
  "nav.aria.database": "数据库",
  "nav.aria.ops": "运维",
  "nav.aria.compareModels": "模型竞速",
  "nav.aria.compareMemory": "记忆竞速",
  "nav.aria.models": "模型",
  "nav.aria.connections": "连接",
  "nav.aria.settings": "行为",
  "nav.aria.collapse": "收起侧边栏",
  "nav.aria.expand": "展开侧边栏",

  // ── 页面标题
  "title.chat": "对话与观察",
  "title.ops": "LLM 运维",
  "title.graph": "图谱工作流 —— 给循环加上结构",
  "title.compare": "竞技场 —— 让模型和记忆跑同一条循环",
  "title.compare.models": "模型竞速 —— 十个大脑，同一套骨架",
  "title.compare.memory": "记忆竞速 —— 一个大脑，五种存事实的地方",
  "title.settings": "行为 —— 一轮对话怎么跑",
  "title.database": "数据库 —— Waku 存下的所有东西（state.db）",

  // ── 聊天面板
  "dock.title": "对话",
  "dock.newChat": "+ 新建对话",
  "dock.history": "历史 ▾",
  "dock.reopen": "‹ 对话",
  "dock.stats": "统计",
  "dock.statsTitle": "显示/隐藏每轮的统计：检索决策、耗时、循环圈数、工具调用",
  "dock.placeholder": "给 Waku 发消息…",
  "dock.send": "发送",
  "dock.open": "打开对话",
  "dock.collapse": "收起对话面板",
  "dock.mic": "点击说话 —— 本地 Whisper，不上云",
  "dock.modelChip": "切换本次对话使用的模型",
  "dock.dragResize": "拖动调整对话面板宽度",

  // ── 页头 / 实时状态
  "live.live": "实时",
  "live.updated": "更新于 {n} 秒前",

  // ── 通用按钮与占位
  "ui.copy": "复制",
  "ui.cancel": "取消",
  "ui.save": "保存",
  "ui.delete": "删除",
  "ui.close": "关闭",
  "ui.empty.nothingYet": "暂无",
  "ui.empty.nothingHere": "暂无内容",
  "ui.empty.noneConfigured": "还没有配置任何项",
  "ui.confirm": "确认",
  "ui.back": "返回",
  "ui.refresh": "刷新",
  "ui.loading": "加载中…",

  // ── 主题切换
  "theme.system": "跟随系统",
  "theme.light": "浅色",
  "theme.dark": "深色",

  // ══ 第二批：页面主体（views.js）══════════════════════

  // — 连接页
  "conn.group.channels": "渠道",
  "conn.group.productivity": "生产力",
  "conn.group.memory": "记忆",
  "conn.group.tools": "工具",
  "conn.state.connected": "已连接",
  "conn.state.error": "出错",
  "conn.state.configured": "已配置 · 未测试",
  "conn.state.needsSetup": "需要配置",
  "conn.state.notConfigured": "未配置",
  "conn.blankKeeps": "留空则保留已保存的值",
  "conn.notConfigured": "未配置",
  // 注意："Clear saved value" / "Setup guide" 的译文由跟进的第三个提交补齐

  // — 数据库页
  "db.tablesTitle": "数据表 —— 点上面的标签，或点这里的一行",
  "db.ftsTitle": "FTS5 —— 关键词索引",
  "db.rowCount": "{n} 行",
  "db.emptyTable": "空 —— 还没有数据",
  "db.notionNote": "情节目前存在 Notion 里 —— 见 记忆 ▸ 情节。下面的行是 state.db 里的旧本地副本。",
  "ui.empty.noTable": "没有这张表",
  "db.showingRange": "显示全部 {total} 行中的 {n} 行（新的在前）",
  "db.sqlIntro": "一个只读的 SQL 控制台，操作 state.db。只有 SELECT 会执行 —— 文件以只读方式打开，这里改不了你的数据。",
  "db.desc.calendar": "create_event 工具写入的日程（旗舰任务）",
  "db.desc.facts": "语义记忆 —— 持久事实（记忆 ▸ 语义）",
  "db.desc.episodes": "情节记忆 —— 带日期的摘要（记忆 ▸ 情节）",
  "db.desc.chatlog": "每一条消息，按 session_id 标记 —— 蒸馏从这里读",

  // — 记忆页
  "mem.pillars": "三根支柱",
  "mem.gateTitle": "检索 gate —— 这一轮到底需不需要记忆？",
  "mem.distilledTitle": "它蒸馏出的事实",
  "mem.introTitle": "记忆 vs 数据库 —— 同一个文件的两种视角。",
  "mem.introBody": "这个页面按三根支柱整理 Waku 记得的东西。数据库页面显示完全相同的原始 SQLite 表（外加 FTS5 关键词索引）。同一个 .waku/state.db —— 只是看的「高度」不同。",
  "mem.introMd": "有些助手（比如 Hermes）把记忆存成单个 MEMORY.md 文件。Waku 把可查询的源头放在 state.db（事实 + 情节，可用 FTS5 搜），同时在每轮之后写一份人能读的 MEMORY.md 镜像 —— 两者都有：一个你能打开的文件，背后是结实的数据库。",
  "mem.pillar.semantic": "关于你和你的联系人，蒸馏出的持久事实",
  "mem.pillar.episodic": "每次蒸馏产生一条带日期的摘要 —— 故意保持很小",
  "mem.pillar.procedural": "只在相关时才加载的 SKILL.md —— 教它怎么做",
  "mem.semanticIntro": "从你告诉 Waku 的话里蒸馏出的持久事实 —— 最小、复用最多的存储。可以随时修改或删除；下一轮就生效。",
  "mem.skillsIntro": "程序性记忆 —— 只有消息匹配时才加载的 Markdown 指令。三种方式添加自己的：在对话里教它（它会调 create_skill）、在下面直接编辑、或把 SKILL.md 放进技能目录。",
  "mem.soulIntro": "SOUL.md 是 Waku 的人格 —— 它每轮都加载的 system prompt。改它就改变了你的 Waku 是谁。下一轮生效。",

  // — 工具页
  "tools.availableIntro": "这一轮 Agent 能调用的能力。一个工具 = 模型读到的名字与描述 + 一份 JSON schema + 一个 Python 函数，就这些。想接更多走 MCP。",
  "tools.resultsIntro": "工具调用真正写下了什么。这些是结果，不是工具本身。",
  "tools.calendarTitle": "日程事件",
  "tools.fromCreateEvent": "来自 create_event",
  "tools.outboxTitle": "发件箱 —— 起草的消息",
  "tools.comingSoon": "即将支持 ",
  "tools.comingSoonSub": "架构图上画了但还没接线（用 WAKU_EXPERIMENTAL=1 打开）",
  "tool.src.flagship": "旗舰任务 —— 日程",
  "tool.src.web": "网页搜索",
  "tool.src.self": "自我管理 —— 它改自己的记忆",
  "tool.src.mcp": "MCP 服务器",
  "tool.src.other": "其他",

  // — MCP
  "mcp.connectTitle": "接一个（30 秒）",

  // — 图谱页
  "graph.turnsTitle": "图谱轮次",
  "graph.offTitle": "已关闭",
  "graph.offBody": "当前每一轮都走经典循环。",
  "ov.graphSub": " —— 当一轮需要固定形状时",

  // — 总览页
  "ov.gateTitle": "检索 gate —— 最关键的那个决策",
  "ov.latestTurn": "最近一轮",

  // — 行为（设置）页
  "set.experimental": "实验性工具",
  "set.experimentalIntro": "选择是否允许在对话里把编程活外包给本地子 Agent。",
  "set.subAgent": "子 Agent 外包",
  "set.graphTitle": "图谱工作流",
  "set.triageFirst": "先分诊再回答",
  "set.graphOff": "关闭 —— 每轮都走经典循环（默认）",
  "set.graphOn": "开启 —— 每条消息先由 triage 图谱路由",
  "set.rebuilds": "在进程内重建 Agent —— 不用重启",

  // — 运维页
  "ops.spendTitle": "花费",
  "ops.spendSub": "永久账本 —— 重置演示数据也不会丢",
  "ops.spendPerDay": "每日花费",
  "ops.gateTitle": "发布门禁",
  "ops.gateSub": "发布 / 不发布的检查",
  "ops.evalHistory": "评测历史",
  "ops.slowest": "最慢的几轮",
  "ops.traceTitle": "追踪",
  "ops.traceSub": "每轮都记成 JSONL，始终开启",
  "ops.wakeTitle": "语音 —— 差点被误唤醒",

  // — 网关（收件箱）页
  "gw.intro": "所有渠道的对话 —— 网页、语音、终端 —— 都由同一个大脑回答。点一条在右侧对话面板里打开。这里是收件箱，面板里是打开的那条线。",

  // — 指标卡
  "stat.spent": "已花费",
  "stat.avgTurn": "平均耗时",
  "stat.turns": "轮次",
  "stat.toolCalls": "工具调用",
  "stat.facts": "事实",
  "stat.events": "事件",
  "stat.tokensIn": "输入 token",
  "stat.tokensOut": "输出 token",
  "stat.llmCalls": "LLM 调用",
  "stat.toolErrors": "工具错误",
  "stat.queued": "排队消息",
  "stat.threshold": "触发阈值",
  "stat.distilled": "蒸馏出的事实",
  "stat.episodes": "情节总数",
  "stat.allTime": "累计",

  // — 子标签页
  "tab.overview": "总览",
  "tab.semantic": "语义",
  "tab.episodic": "情节",
  "tab.skills": "技能",
  "tab.soul": "SOUL",
  "tab.consolidation": "蒸馏",
  "tab.available": "可用",
  "tab.results": "结果",
  "tab.mcp": "MCP",
  "tab.sql": "SQL 控制台",

  // — 表头
  "tbl.subject": "主题",
  "tbl.fact": "事实",
  "tbl.source": "来源",
  "tbl.date": "日期",
  "tbl.episode": "情节",
  "tbl.event": "事件",
  "tbl.start": "开始",
  "tbl.end": "结束",
  "tbl.with": "参与人",
  "tbl.table": "数据表",
  "tbl.rows": "行数",
  "tbl.whatitholds": "存的是什么",
  "tbl.provider": "供应商",
  "tbl.LLMcalls": "LLM 调用",
  "tbl.tokensin": "输入 token",
  "tbl.tokensout": "输出 token",
  "tbl.cost(est)": "成本（估）",
  "tbl.day": "日期",
  "tbl.turn": "轮次",
  "tbl.decision": "决策",
  "tbl.why": "原因",
  "tbl.when": "时间",
  "tbl.deterministic": "确定性测试",
  "tbl.llmjudge": "LLM 评审",
  "tbl.counts": "统计",
  "tbl.latency": "耗时",
  "tbl.cost": "成本",
  "tbl.tools": "工具",
  "tbl.detail": "详情",
  "tbl.heard": "听到的",

  // — 按钮（第一批未覆盖的部分）
  "ui.run": "运行",
  "ui.edit": "编辑",
  "ui.testConnection": "测试连接",
  "ui.saveAnyway": "仍然保存",
  "ui.saveSkill": "保存 SKILL.md",
  "ui.saveSoul": "保存 SOUL.md",
  "ui.saveSwitch": "保存并切换",
  "ui.on": "开启",
  "ui.off": "关闭",
  "ui.failed": "失败",
  "ui.saving": "保存中…",
  "ui.testing": "测试中…",
  "ui.savingUnverified": "未经测试直接保存…",
  "ui.empty.noFacts": "还没有事实",
  "ui.empty.noEpisodes": "还没有情节",
  "ui.mcpServers": "可用的 ▸ MCP 服务器",
  "ui.memoryEpisodic": "记忆 ▸ 情节",
  "ui.memoryTab": "记忆页面",
  "ui.behaviourTab": "行为",
  "ui.graphTab": "图谱",
  "ui.traced": "追踪记录",

  // ══ 第二批补充：收尾替换引入的 key ══════════════════
  "ov.archTitle": "架构图 —— 点任意方块",

  "db.vsTitle": "数据库 vs 记忆。",
  "db.vsBody": "这是最底层的持久化层 —— 字面意义上的 SQLite 表。记忆页面是同一批行的友好视图（事实、情节、技能、人格）。一个文件，两种高度。Hermes 用 MEMORY.md 文件，Waku 用这些可查询的表 —— 同时镜像出一份能读的 MEMORY.md。",

  "mem.epiWhyTitle": "为什么这么小？",
  "mem.epiWhyBody": "情节记忆每次蒸馏只产生一条摘要，不是每条消息一条。原始的、逐句的对话躺在数据库页面的 chat_log 表里（那张大的）—— 情节是它的要点。",
  "mem.consTitle": "它是怎么工作的。",
  "mem.consBody": "每 {n} 轮，一个便宜模型读取尚未蒸馏的 chat_log，把它提炼成持久事实（语义记忆）加一条情节（情节记忆）。攒批处理省钱，也让摘要模型有足够上下文判断什么值得留。",

  "graph.offTurnOn": "打开",
  "graph.offIn": "里的",
  "graph.offOr": "，或者在",
  "graph.offInEnv": "里设置",
  "graph.offFailOpen": "任何失败都会退回普通循环 —— 这不会丢回复，只会省时间和 token。",
  "graph.note.triageTitle": "每条消息都会自动跑。",
  "graph.note.triageBody": "由 graph-workflows 开关控制。实线箭头 = 一定会走，虚线 = 路由器的选择。full_agent 就是普通的循环，只是作为一个节点在跑 —— 图谱不取代循环，它只是安排对循环的调用。",
  "graph.note.gatherTitle": "你启动它时才跑",
  "graph.note.gatherBody": "—— 用 make gather 或下面的按钮 —— 完全无视那个开关。四路扫描彼此没有依赖，所以引擎把它们放在同一波里一起跑，而不是排队。它只提议、从不自己动手；摘要落在发件箱里等你看。",

  "mcp.connected": " —— 已连接",
  "mcp.configured": " —— 已配置",
  "mcp.notSetUp": " —— 还没设置",
  "mcp.step1": "1 —— 装上 extra：",
  "mcp.step2": "2 —— 在",
  "mcp.theHome": ".waku 目录",
  "mcp.step3": "3 —— 重启 dashboard。这个服务器的工具会出现在上面的",

  "ops.gateWhich": "检索 gate —— 哪些轮次用了记忆",
  "ops.gateDecisions": "实际的决策（哪些跳过、哪些检索了），最新的在前：",
  "ops.spanWaterfalls": "跨度瀑布图：",

  "conn.empty": "还没有注册任何集成。",
  "conn.clearSaved": "清除已保存的值",
  "conn.setupGuide": "配置指南 ↗",
};
