// 用户模式 vs 开发者模式 —— 个人用户默认只看到核心页面，开发者模式显示全部。
//
// 界线画在哪：能自己装、自己配 API key、自己读 trace 的人才有用开发者页面的
// 需求。其他人第一次打开就看到"模型竞速""SQL 控制台""发布门禁"，只会觉得自己
// 装错了东西。
//
// 保留（用户模式）：总览、记忆、工具、行为 —— 加上右侧的聊天面板。
// 隐藏（开发者模式）：网关、循环、图谱、数据库、运维、两个竞速页、模型、连接。
//
// 为什么切换靠刷新而不是就地重渲染：切换会影响导航、当前页、以及正在跑的
// 动画和已打开的弹窗。刷新浏览器是最干净的实现，代价只是那一刻的白屏 ——
// 和语言开关是同一个取舍（见 i18n.js 顶部）。

// 上次的选择存在这里；没存过就看启动参数。
const DEV_STORED = (function () {
  try { return localStorage.getItem("waku-dev"); } catch (e) { return null; }
})();

// 优先级：localStorage（用户在界面上切过）> 启动参数 --dev > 默认用户模式。
// 网页上的显式选择必须压过命令行，否则用户在设置里关掉它、下次启动又被打开。
const DEV_ON = DEV_STORED !== null ? DEV_STORED === "1" : window.WAKU_DEV === true;

// 只有开发者才需要的页面，按导航项的 data-v 匹配。
const DEV_PAGES = ["gateway", "loop", "graph", "database", "ops",
                   "compare", "models", "connections"];

function isDevPage(view){
  return DEV_PAGES.indexOf(view) >= 0;
}

// 隐藏开发者导航项，以及整组都没剩下东西的分组名。
function applyUserMode(){
  if (DEV_ON) return;   // 开发者模式 = 什么都不动，导航保持原样

  document.querySelectorAll("nav a[data-v]").forEach(function (a) {
    if (isDevPage(a.dataset.v)) a.style.display = "none";
  });

  // "Arena" 那组的两个页面都是开发者页，整组消失后标签不该孤零零留着。
  document.querySelectorAll("nav .r-grp").forEach(function (group) {
    let el = group.nextElementSibling, hasVisibleLink = false;
    while (el && !el.classList.contains("r-grp")) {
      if (el.classList.contains("r-bottom")) break;
      if (el.tagName === "A" && el.style.display !== "none") { hasVisibleLink = true; break; }
      el = el.nextElementSibling;
    }
    group.style.display = hasVisibleLink ? "" : "none";
  });

  // 收藏夹或旧链接直接落到开发者页面时，送回总览，而不是给一个空页面。
  const view = (location.hash || "#overview").slice(1).split("/")[0];
  if (isDevPage(view)) location.hash = "#overview";
}

// 行为页那个开关调用它。刷新让新模式从导航到当前页一次性生效。
function applyDevMode(){
  const select = document.getElementById("set-dev-mode");
  if (!select) return;
  try { localStorage.setItem("waku-dev", select.value === "1" ? "1" : "0"); } catch (e) {}
  location.reload();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", applyUserMode);
} else {
  applyUserMode();
}
