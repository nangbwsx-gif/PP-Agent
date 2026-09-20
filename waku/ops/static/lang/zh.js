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
};
