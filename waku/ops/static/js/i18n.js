// 语言层 —— 只做文案查表，不含任何业务逻辑（LOADS FIRST，必须先于其他 js）。
//
// 设计取舍，三条：
//
//   1. 语言在启动时定死（`waku dashboard --zh`），不做运行时切换。主题可以
//      随手切，因为那只是观感；换语言要重画整页文案，还要照顾已经生成的
//      DOM、弹窗、正在跑的动画 —— 状态一多就是 bug。少一个状态，少一类 bug。
//
//   2. 英文模式 = 本文件完全不介入。t() 原样返回代码里写的英文原文，DOM 也
//      一个都不碰。所以英文版的执行路径和加这层语言之前一模一样，
//      "改坏了原版"这件事在英文下不可能发生。
//
//   3. 查不到就显示英文原文。不会出现空白，也不会把 nav.foo 这种 key
//      露给用户 —— 漏翻一条的后果只是那一句还是英文。
//
// 英文原文不写在这张表里：它是代码里传给 t() 的第二个参数。所以没有"两份
// 英文要对齐"的负担，中文表也永远不用回答"这句英文有没有被改过"。

(function () {
  const LANG = (window.WAKU_LANG === "zh") ? "zh" : "en";
  // ZH 由 /static/lang/zh.js 定义；服务端只在中文模式注入它，英文模式下它不存在。
  const DICT = (LANG === "zh" && typeof ZH !== "undefined") ? ZH : {};
  const MISSING = [];

  // t(key, "English原文") → 中文模式下返回中文，其余情况返回英文原文。
  function t(key, fallback) {
    if (LANG !== "zh") return fallback === undefined ? key : fallback;
    const v = DICT[key];
    if (v === undefined || v === "") {
      if (MISSING.indexOf(key) < 0) MISSING.push(key);   // 记下来，控制台能看到
      return fallback === undefined ? key : fallback;
    }
    return v;
  }

  // 侧边栏：直接读 href 区块已有的 data-v / data-sub 推出 key，所以不用给
  // 13 个 <a> 各加一个 data-i18n 属性 —— 加了反而多一处要同步的地方。
  function translateNav() {
    document.querySelectorAll("nav a[data-v]").forEach(function (a) {
      const key = a.dataset.sub ? "nav." + a.dataset.v + "." + a.dataset.sub
                                : "nav." + a.dataset.v;
      const lbl = a.querySelector(".lbl");
      const text = DICT[key];
      if (lbl && text) lbl.textContent = text;

      // aria-label 是给屏幕阅读器和折叠后的 tooltip 用的，也得跟着走
      const ariaKey = "nav.aria." + (a.dataset.sub
        ? a.dataset.v + a.dataset.sub.charAt(0).toUpperCase() + a.dataset.sub.slice(1)
        : a.dataset.v);
      const ariaText = DICT[ariaKey] || text;
      if (ariaText) {
        a.setAttribute("aria-label", ariaText);
        if (a.title) a.title = ariaText;   // 折叠状态下 tooltip 已经显示在外面
      }
    });
  }

  // 显式标注的元素。三个属性各管一处，因为 title / placeholder / 文本内容
  // 是三种不同的 DOM 属性，合成一个反而要在运行时猜。
  function applyI18n(root) {
    if (LANG !== "zh") return;
    const scope = root || document;
    scope.querySelectorAll("[data-i18n]").forEach(function (el) {
      const text = DICT[el.dataset.i18n];
      if (text) el.textContent = text;
    });
    scope.querySelectorAll("[data-i18n-title]").forEach(function (el) {
      const text = DICT[el.dataset.i18nTitle];
      if (text) el.title = text;
    });
    scope.querySelectorAll("[data-i18n-placeholder]").forEach(function (el) {
      const text = DICT[el.dataset.i18nPlaceholder];
      if (text) el.placeholder = text;
    });
    scope.querySelectorAll("[data-i18n-aria]").forEach(function (el) {
      const text = DICT[el.dataset.i18nAria];
      if (text) el.setAttribute("aria-label", text);
    });
  }

  function translateAll(root) {
    if (LANG !== "zh") return;
    translateNav();
    applyI18n(root);
    if (MISSING.length) {
      // 只提醒开发者，不打扰用户。中文模式下漏翻的 key 会在这里列出来。
      console.warn("[i18n] 这些 key 在 zh.js 里没有译文，已回退英文：", MISSING);
    }
  }

  window.t = t;
  window.WAKU_I18N = {lang: LANG, t: t, applyI18n: applyI18n, missing: MISSING};

  if (LANG === "zh") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", function () { translateAll(); });
    } else {
      translateAll();
    }
  }
})();
