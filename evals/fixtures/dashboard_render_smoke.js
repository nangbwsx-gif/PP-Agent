// Render every dashboard view headlessly, so a page that throws is caught here
// instead of blanking in someone's browser.
//
// Why this exists: node --check only proves syntax. The two bugs that took the
// Tools, Database and Connections pages down on 2026-09-19 were all valid
// JavaScript — `const t = d.tools` shadowing the t() translator, and
// `toolsMCP(t)` still pointing at the translator after a rename. Syntax checks
// and text checks (test_static_js_parses.py) each catch one of those classes;
// only actually calling the render function catches the rest.
//
// Run: node evals/fixtures/dashboard_render_smoke.js <data.json>
// The data comes from a fixture (see test_dashboard_renders.py), not a live
// server — this must stay offline and deterministic.

const fs = require("fs");
const path = require("path");

const STATIC = path.join(__dirname, "..", "..", "waku", "ops", "static");
const DATA = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));

// ── stubs: anything the views call that we don't want to exercise. They only
// have to return a string; the assertions are about not throwing.
const empty = () => "";
for (const name of [
  "uiCard", "uiTable", "uiBadge", "uiLink", "uiRow", "uiNotice", "uiTabs",
  "uiStatBand", "uiButton", "uiMenuLabel", "uiMenuItem", "uiMenuSep",
  "gateSplit", "turnCard", "table", "renderMarkdown", "archSVG", "graphSVG",
  "graphPanel", "graphRunPanel", "reveal", "modelsGrid", "syncModelChip",
  "applyTele", "syncLiveView", "loadCompareHistory", "stagesRow", "teleFooter",
  "chatTurnCard", "historicalCard", "stripTools", "histItem", "rowCells",
  "splitFigures", "nodesRow", "streamingCard", "gwTags", "sessionMeta",
  "toggleModelMenu", "toggleSessMenu", "toggleTele", "newChat", "openConversation",
  "modelPickerHTML", "uiMenu",
]) {
  globalThis[name] = empty;
}
globalThis.esc = (x) => String(x ?? "");
globalThis.money = () => "$0.00";
globalThis.secs = () => "0.0s";
globalThis.uiCard = (body) => body;
globalThis.postJSON = async () => ({});
globalThis.openDialog = () => ({ querySelector: () => null });
globalThis.closeDialog = () => {};
globalThis.refresh = () => {};
globalThis.stProvider = () => "deepseek";
globalThis.D = DATA;
globalThis.SESSION = DATA.current_session || "default";
globalThis.editing = false;
globalThis.activeView = null;
globalThis.animating = false;
globalThis.WAKU_LANG = "zh";

// i18n.js is written for a browser and reads both of these.
globalThis.window = globalThis;
globalThis.document = {
  readyState: "complete",
  addEventListener: () => {},
  querySelectorAll: () => [],
  documentElement: {},
};
// mode.js reads the stored choice and writes it back on toggle.
const _store = {};
globalThis.localStorage = {
  getItem: (k) => (k in _store ? _store[k] : null),
  setItem: (k, v) => { _store[k] = String(v); },
  removeItem: (k) => { delete _store[k]; },
};
// mode.js reads location.hash to send a deep link away from a hidden page.
globalThis.location = { hash: "#overview" };

// 让测试用不同的模式组合跑同一个 bundle：
//   SMOKE_DEV_FLAG=1      模拟 `waku dashboard --dev`
//   SMOKE_DEV_STORED=1|0  模拟用户在行为页切过开关
if (process.env.SMOKE_DEV_FLAG === "1") globalThis.WAKU_DEV = true;
if (process.env.SMOKE_DEV_STORED !== undefined) _store["waku-dev"] = process.env.SMOKE_DEV_STORED;

// One eval, so the three files share a lexical scope: lang/zh.js declares
// `const ZH` and i18n.js reads it. Separate evals would not see each other —
// which is fine in a browser (scripts share the global scope) and not fine here.
const bundle = [
  fs.readFileSync(path.join(STATIC, "lang", "zh.js"), "utf8"),
  fs.readFileSync(path.join(STATIC, "js", "i18n.js"), "utf8"),
  fs.readFileSync(path.join(STATIC, "js", "mode.js"), "utf8"),
  fs.readFileSync(path.join(STATIC, "js", "views.js"), "utf8"),
  "globalThis.__VIEWS = VIEWS; globalThis.__T = t; globalThis.__ZH = ZH; globalThis.__DEV_ON = DEV_ON;",
].join("\n;\n");
eval(bundle);

const VIEWS = globalThis.__VIEWS;
console.log(`dev mode: ${globalThis.__DEV_ON}`);

// Every view, and every sub-tab of the ones that have them. A sub-tab is a
// separate render path, and two of the three bugs above lived in one.
const CALLS = [
  ["overview", () => VIEWS.overview(DATA)],
  ["gateway", () => VIEWS.gateway(DATA)],
  ["loop", () => VIEWS.loop(DATA)],
  ["graph", () => VIEWS.graph(DATA)],
  ["memory/overview", () => VIEWS.memory(DATA, "overview")],
  ["memory/semantic", () => VIEWS.memory(DATA, "semantic")],
  ["memory/episodic", () => VIEWS.memory(DATA, "episodic")],
  ["memory/skills", () => VIEWS.memory(DATA, "skills")],
  ["memory/soul", () => VIEWS.memory(DATA, "soul")],
  ["memory/consolidation", () => VIEWS.memory(DATA, "consolidation")],
  ["tools/available", () => VIEWS.tools(DATA, "available")],
  ["tools/results", () => VIEWS.tools(DATA, "results")],
  ["tools/mcp", () => VIEWS.tools(DATA, "mcp")],
  ["database/overview", () => VIEWS.database(DATA, "overview")],
  ["database/query", () => VIEWS.database(DATA, "query")],
  ["database/facts", () => VIEWS.database(DATA, "facts")],
  ["settings", () => VIEWS.settings(DATA)],
  ["ops", () => VIEWS.ops(DATA)],
  ["connections", () => VIEWS.connections(DATA)],
];

let failed = 0;
const lines = [];
for (const [name, render] of CALLS) {
  try {
    const html = render();
    if (html === undefined || html === null) throw new Error("returned undefined");
    const text = String(html);
    // An empty string is legitimate: a view with no data renders nothing when
    // uiCard is stubbed. What we are hunting is a throw.
    const junk = text.match(/\b(undefined|NaN|\[object Object\])\b/);
    if (junk) throw new Error(`output contains ${junk[0]}`);
    lines.push(`  ok   ${name}`);
  } catch (e) {
    failed++;
    const at = (e.stack || "").split("\n")[1] || "";
    lines.push(`  FAIL ${name}  ${e.constructor.name}: ${e.message}\n       ${at.trim()}`);
  }
}

console.log(lines.join("\n"));

// The Chinese interface is the reason this file exists — prove the language
// layer really engaged rather than silently falling back to English.
const sample = VIEWS.tools(DATA, "available");
if (!/[\u4e00-\u9fff]/.test(sample)) {
  console.log("\nFAIL  tools/available rendered no Chinese — is the dictionary loaded?");
  failed++;
}

// 状态标签必须用**原始状态值**（"connected"），不能是翻译后的文字。
// 真实事故：批量翻译把 "connected" 无差别包成 t(...)，于是
// state === t(...) 永远不成立（拿中文“已连接”去比英文状态值），所有卡片
// 掉到兜底分支显示“未配置”，连状态圆点的 CSS 类都变成了中文。
// 而服务端数据一直是对的，所以只能靠渲染结果才能发现。
const connHtml = VIEWS.connections(DATA);
if (!/class="connstatus connected"/.test(connHtml)) {
  console.log("\nFAIL  a connected integration did not render the raw `connected` class "
            + "— is a translated string being used as a state value or a CSS class?");
  failed++;
}

if (failed) {
  console.log(`\n${failed} view(s) failed`);
  process.exit(1);
}
console.log(`\nall ${CALLS.length} views render`);
