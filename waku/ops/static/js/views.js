// waku dashboard — subtab/db helpers, SQL console, Memory/Tools sub-views, VIEWS.
// Split out of app.js: classic <script>, shared global scope (no build
// step, no modules). Load order + rules: static/README.md.

// --- sub-tabs: keep long pages short by splitting them into hash-routed tabs
// (#memory/semantic, #database/facts). Each tab is a plain link, so it's
// bookmarkable and the architecture cards can deep-link straight to one.
function subtabBar(view, tabs, active){
  return uiTabs(tabs.map(([key,label,n]) =>
    ({label: esc(label), href: `#${view}/${key}`, on: key===active, count: n})));
}

// A raw SQLite table, scrollable, with the column names AS the sticky headers
// so the schema lines up over its data instead of floating above it.
// 参数叫 table 不叫 t：函数体里要调全局的 t() 做翻译，参数一旦叫 t 就把它遮蔽掉。
// Tools 和 Database 两个页面曾经因为这件事整个崩掉（t 变成了对象，一调用就 TypeError）。
function dbTable(table){
  if (!table.sample.length) return uiCard(`<span class="empty">${t("db.emptyTable","empty — no rows yet")}</span>`);
  const cols = table.columns.map(c => `${esc(c)}${
    table.types&&table.types[c]?`<small>${esc(table.types[c].toLowerCase())}</small>`:""}`);
  const rows = table.sample.map(r => table.columns.map(c =>
    `<span class="dbcell">${esc(String(r[c]??"").slice(0,120))}</span>`));
  return `<div class="scrolly">${uiTable(cols, rows)}</div>
    <div class="meta" style="margin-top:calc(var(--spacing) * 1.5)">${t("db.showingRange","showing {n} of {total} rows (newest first)").replace("{n}",table.sample.length).replace("{total}",table.count)}</div>`;
}
const DB_DESC = {  // 每张表的说明，见 t() 的第二个参数
  calendar_events: t("db.desc.calendar","events the create_event tool wrote (the flagship task)"),
  facts: t("db.desc.facts","semantic memory — durable facts (Memory ▸ Semantic)"),
  episodes: t("db.desc.episodes","episodic memory — dated summaries (Memory ▸ Episodic)"),
  chat_log: t("db.desc.chatlog","every message, tagged by session_id — consolidation reads from here"),
};
const QUERY_EXAMPLES = [
  "SELECT role, content FROM chat_log ORDER BY id DESC LIMIT 10",
  "SELECT subject, content FROM facts",
  "SELECT session_id, COUNT(*) FROM chat_log GROUP BY session_id",
];
function dbQueryView(){
  return `<div class="meta" style="margin-bottom:var(--space-2)">${t("db.sqlIntro","A read-only SQL console over state.db. Only SELECT runs — the file is opened read-only, so nothing here can change your data.")}</div>
    <textarea class="sqlbox" id="sqlbox" spellcheck="false" onfocus="markEditing()" oninput="markEditing()">${esc(QUERY_EXAMPLES[0])}</textarea>
    <div style="margin:var(--space-2) 0">${uiButton(t("ui.run","Run"), {level: "primary", onclick: "runQuery()"})}
      <span class="meta" style="margin-left:var(--space-3)">try: ${QUERY_EXAMPLES.map(q=>`<span class="qexample" onclick="qFill(this.textContent)">${esc(q)}</span>`).join(" &nbsp; ")}</span></div>
    <div id="qout"></div>`;
}

// --- read-only SQL console (item: "a simple query editor like Supabase")
function qFill(sql){ const b=document.getElementById("sqlbox"); if(b){ b.value=sql; runQuery(); } }
async function runQuery(){
  editing = true;   // keep the 5s refresh from wiping the query + results
  const sql = (document.getElementById("sqlbox")||{}).value || "";
  const out = document.getElementById("qout");
  out.innerHTML = `<div class="meta">running…</div>`;
  const r = await postJSON("/api/query", {sql});
  if (r.error){ out.innerHTML = uiCard(`<span class="empty" style="color:var(--bad)">${esc(r.error)}</span>`); return; }
  if (!r.rows.length){ out.innerHTML = uiCard(`<span class="empty">0 rows</span>`); return; }
  out.innerHTML = `<div class="scrolly">${uiTable(r.columns.map(esc),
    r.rows.map(row => row.map(v => `<span class="dbcell">${esc(String(v).slice(0,120))}</span>`)))}</div>
    <div class="meta" style="margin-top:calc(var(--spacing) * 1.5)">${t("db.rowCount","{n} row(s)").replace("{n}",r.rows.length)}</div>`;
}

// --- Memory sub-tabs. Memory is the friendly, per-pillar view of what persists;
// the Data tab shows the SAME rows as raw SQLite tables (see the explainer).
function memOverview(d){
  const s = d.stats;
  const pillars = [
    ["Semantic","semantic",d.facts.length+" facts",t("mem.pillar.semantic","durable, distilled facts about you and your people")],
    ["Episodic","episodic",d.episodes.length+" episodes",t("mem.pillar.episodic","one dated summary per consolidation — stays small on purpose")],
    ["Procedural","skills",d.skills.length+" skills",t("mem.pillar.procedural","SKILL.md files loaded only when relevant — how to act")],
  ].map(([t,sub,n,desc]) => uiCard(`<span>${desc}</span>`,
      {title: t, action: uiLink(n, `#memory/${sub}`), cls: "box"})).join("");
  return uiNotice("note", `<b>${t("mem.introTitle","Memory vs Database — two views of one file.")}</b>
      <div class="r">${t("mem.introBody","This tab is the curated, per-pillar view of what Waku remembers. The Database tab shows the exact same thing as raw SQLite tables (plus the FTS5 keyword index). Same .waku/state.db — different altitude.")}
      <br><br>${t("mem.introMd","Some assistants (Hermes) keep memory as a single MEMORY.md file. Waku keeps the queryable source in state.db (facts + episodes, FTS5-searchable) and writes a human-readable MEMORY.md mirror after every turn — so you get both: a real file you can open, backed by a sturdy database.")}</div>`) + `
    <h2>${t("mem.pillars","The three pillars")}</h2>
    <div class="tiles" style="grid-template-columns:repeat(auto-fill,minmax(220px,1fr))">${pillars}</div>
    <h2>${t("mem.gateTitle","Retrieval gate — does this turn even need memory?")}</h2>${gateSplit(s)}
    <div class="meta" style="margin-top:var(--space-2)">A cheap model decides <b>if</b> a turn needs memory at all, before any lookup —
      this is memory <i>retrieval</i>, the hero decision. (The Ops tab charts the same skip/retrieve
      numbers as an operational metric; the decision itself is memory's.)</div>
    <div class="meta" style="margin-top:var(--space-3)">Files: ${reveal("state.db","state.db")} · ${reveal("MEMORY.md","MEMORY.md")} · ${reveal("SOUL.md","SOUL.md")} · ${reveal("skills","skills/")}</div>`;
}
function memSemantic(d){
  let h = `<div class="meta" style="margin-bottom:var(--space-3)">${t("mem.semanticIntro","Durable facts distilled from what you tell Waku — the smallest, most-reused store. Edit or forget any of them; changes are live next turn.")}</div>`;
  // editFact (memory.js) finds the row #fact-N, swaps its .fc for a textarea
  // and its last cell for save/cancel, so the row carries the id.
  h += uiTable([t("tbl.subject","subject"), t("tbl.fact","fact"), t("tbl.source","source")], d.facts.map(f => ({id: `fact-${f.id}`, cells: [
    `<code>${esc(f.subject)}</code>`,
    `<span class="fc">${esc(f.content)}</span>`,
    `<span class="meta">${esc(f.source)}</span>`,
    `<span style="white-space:nowrap">${uiButton(t("ui.edit","edit"), {level: "tertiary", size: "sm", onclick: `editFact(${f.id})`})} · ${uiButton(t("ui.delete","delete"), {level: "tertiary", size: "sm", danger: true, onclick: `delMem('delete_fact',${f.id})`})}</span>`,
  ]})), {empty: t("ui.empty.noFacts","no facts yet")});
  return h;
}
function memEpisodic(d){
  const src = d.episodes_source || "sqlite";
  let h = `<div class="meta" style="margin-bottom:var(--space-2)">backend: ${uiBadge(esc(src), "value")}</div>`;
  if (d.episodes_error) h += uiCard(`<span class="empty">Could not read episodes from Notion: ${esc(d.episodes_error)}</span>`);
  h += uiNotice("note", `<b>${t("mem.epiWhyTitle","Why is this small?")}</b> <span class="r">${t("mem.epiWhyBody","Episodic memory holds one distilled summary per consolidation, not every message. The raw, blow-by-blow conversation lives in the chat_log table (the big one) on the Database tab — episodes are its highlights.")}</span>`);
  h += uiTable([t("tbl.date","date"), t("tbl.episode","episode"), ""], d.episodes.map(e => [
    `<span class="meta">${esc(e.happened_at)}</span>`, esc(e.summary),
    uiButton(t("ui.delete","delete"), {level: "tertiary", size: "sm", danger: true, onclick: `delMem('delete_episode','${e.id}')`}),
  ]), {empty: t("ui.empty.noEpisodes","no episodes yet")});
  return h;
}
function memSkills(d){
  let h = `<div class="meta" style="margin-bottom:var(--space-3)">${t("mem.skillsIntro","Procedural memory — markdown instructions loaded only when a message matches. Add your own three ways: teach Waku in chat (it calls create_skill), edit a skill below, or drop a SKILL.md into the skills folder.")}</div>`;
  h += d.skills.map((sk,i) => {
    const full = `---
name: ${sk.name}
description: ${sk.description}
---

${sk.body}`;
    return uiCard(`
      <div class="u"><code>${esc(sk.name)}</code> <span class="meta" style="font-weight:400">· ${esc(sk.description)}</span>
        <span style="margin-left:calc(var(--spacing) * 1.5)">${uiBadge(sk.editable?"home":"built-in")}</span></div>
      <textarea class="editor" id="sk-${i}" style="min-height:150px;margin-top:var(--space-2)" data-path="${esc(sk.path)}"
        oninput="dirty('sksave-${i}')" onfocus="markEditing()">${esc(full)}</textarea>
      <div style="margin-top:var(--space-2)">${uiButton(t("ui.saveSkill","Save SKILL.md"), {level: "primary", onclick: `saveSkill(${i})`, attrs: `id="sksave-${i}" disabled`})}
        <span class="meta" id="skmsg-${i}" style="margin-left:var(--space-2)">${esc(sk.rel)}</span></div>`);
  }).join("") || uiCard(`<span class="empty">no skills loaded</span>`);
  return h;
}
function memSoul(d){
  return `<div class="meta" style="margin-bottom:var(--space-3)">${t("mem.soulIntro","SOUL.md is Waku's persona — the system prompt it loads every turn. Editing it changes who your Waku is. Changes are live next turn.")}</div>
    ${uiCard(`<textarea id="soul" class="editor" style="min-height:260px"
      oninput="dirty('soul-save')" onfocus="markEditing()">${esc(d.soul||"")}</textarea>
    <div style="margin-top:var(--space-2)">${uiButton(t("ui.saveSoul","Save SOUL.md"), {level: "primary", onclick: "saveSoul()", attrs: 'id="soul-save" disabled'})}
      <span class="meta" id="soul-msg" style="margin-left:var(--space-2)"></span></div>`)}
    <div class="meta" style="margin-top:var(--space-2)">${reveal("SOUL.md","open SOUL.md in your editor")}</div>`;
}
function memConsolidation(d){
  const distilled = d.facts.filter(f => f.source==="consolidation");
  let h = uiCard(`<b>${t("mem.consTitle","How it works.")}</b> <span class="r prose">${
    t("mem.consBody","Every {n} exchanges, a cheap model reads the unconsolidated chat_log and distills it into durable facts (semantic) plus one episode (episodic). Batching keeps it cheap and gives the summarizer enough context to pick what's worth keeping.")
      .replace("{n}", d.consolidate_every)}</span>`);
  h += `<div style="margin-top:var(--space-3)">${uiStatBand([
    {label:t("stat.queued","messages queued"), value:d.chat_pending},
    {label:t("stat.threshold","trigger threshold"), value:d.consolidate_every*2},
    {label:t("stat.distilled","facts from consolidation"), value:distilled.length},
    {label:t("stat.episodes","episodes total"), value:d.episodes.length},
  ])}</div>`;
  h += `<h2>${t("mem.distilledTitle","Facts it distilled")}</h2>`;
  h += table([t("tbl.subject","subject"), t("tbl.fact","fact"), t("tbl.when","when")], distilled.map(f =>
    `<tr><td><code>${esc(f.subject)}</code></td><td>${esc(f.content)}</td><td class="meta">${esc((f.created_at||"").slice(0,10))}</td></tr>`));
  h += `<div class="meta" style="margin-top:var(--space-2)">This is a memory operation, shown here. Each run is also
    ${uiLink(t("ui.traced","traced"), "#ops")} (Ops) and can be scored by the judge evals.</div>`;
  return h;
}

// Tools ▸ Results: the artifacts tool calls produced (kept distinct from the
// tools themselves — the old tab conflated capability with output).
function toolsResults(d){
  let h = `<div class="meta" style="margin-bottom:var(--space-2)">${t("tools.resultsIntro","What tool calls actually wrote. These are results, not the tools.")}</div>`;
  h += `<h2>${t("tools.calendarTitle","Calendar events")} <span class="meta" style="font-weight:400">· ${t("tools.fromCreateEvent","from create_event")}</span></h2>`;
  h += table([t("tbl.event","event"), t("tbl.start","start"), t("tbl.end","end"), t("tbl.with","with")], d.calendar.map(e =>
    `<tr><td>${esc(e.title)}</td><td class="meta">${esc(e.start)}</td><td class="meta">${esc(e.end)}</td><td>${esc(e.attendees)}</td></tr>`));
  h += `<div class="meta" style="margin-bottom:var(--space-4)">also written to <code>calendar.ics</code> — ${reveal("calendar.ics","reveal calendar.ics in Finder")} (double-click to import into Calendar.app)</div>`;
  h += `<h2>${t("tools.outboxTitle","Outbox — drafted messages")} <span style="font-weight:400;text-transform:none;letter-spacing:0">· ${reveal("outbox","open the outbox folder")}</span></h2>`;
  h += d.outbox.length ? d.outbox.map(o => uiCard(`<span class="u">${esc(o.name)}</span><div class="r">${esc(o.text)}</div>`)).join("")
                       : uiCard(`<span class="empty">no drafted messages</span>`);
  return h;
}
// Tools ▸ MCP: external connectors. Shows live status + a copy-paste config so
// anyone can plug in their own server (scalable, not a one-off).
// 参数叫 tools 不叫 t：见 dbTable 上方的说明。
function toolsMCP(tools){
  const m = tools.mcp;
  let h = uiNotice(m.live ? "ok" : "note", `<b>Model Context Protocol${m.live?t("mcp.connected"," — connected"):m.configured?t("mcp.configured"," — configured"):t("mcp.notSetUp"," — not set up")}.</b>
    <div class="r">MCP lets Waku borrow tools from any external server (files, GitHub, a database, …),
    namespaced <code>&lt;server&gt;_&lt;tool&gt;</code>. ${m.configured
      ? `Configured servers: ${m.servers.map(s=>`<code>${esc(s)}</code>`).join(" ")}${m.live?"":" — start a chat to connect them."}`
      : t("ui.empty.noneConfigured","None configured yet.")}</div>`);
  h += `<h2>${t("mcp.connectTitle","Connect one (30 seconds)")}</h2>` + uiCard(`
    <div class="meta">${t("mcp.step1","1 — install the extra:")} <code>pip install -e '.[mcp]'</code></div>
    <div class="meta" style="margin-top:calc(var(--spacing) * 1.5)">${t("mcp.step2","2 — create")} ${reveal("",t("mcp.theHome","the .waku folder"))}<code>/mcp.json</code>:</div>
    <pre style="font-family:var(--face-mono);font-size:var(--text-xs);color:var(--text-muted);white-space:pre-wrap;margin-top:var(--space-2)">{"servers": [
  {"name": "fs", "command": "npx",
   "args": ["-y", "@modelcontextprotocol/server-filesystem", "${esc(D&&D.home||"")}"]}
]}</pre>
    <div class="meta" style="margin-top:var(--space-2)">${t("mcp.step3","3 — restart the dashboard. The server's tools appear above under")}
      ${uiLink(t("ui.mcpServers","Available ▸ MCP servers"), "#tools/available")}, callable in chat.</div>`);
  h += `<div class="meta" style="margin-top:var(--space-3)">The same pattern scales: any MCP server (yours or a vendor's)
    plugs in the same way — no code changes to Waku. Skills work the same way — drop a <code>SKILL.md</code>
    in ${reveal("skills","skills/")}.</div>`;
  return h;
}

function connectionField(key, field, prefix="connection"){
  const id = `${prefix}-${key}-${field.name}`;
  const label = `${esc(field.label)}${field.required?" *":""}`;
  const help = field.help ? `<div class="conn-field-help">${esc(field.help)}</div>` : "";
  if (field.kind === "bool") return `<div class="conn-field">
    <label class="conn-check" for="${id}"><input id="${id}" data-field="${esc(field.name)}" type="checkbox" ${field.value?"checked":""}>
      <span>${label}</span></label>${help}</div>`;
  if (field.kind === "choice") return `<div class="conn-field"><label class="fld" for="${id}"><span>${label}</span>
    <select id="${id}" data-field="${esc(field.name)}">${field.options.map(o=>`<option value="${esc(o)}" ${o===field.value?"selected":""}>${esc(o)}</option>`).join("")}</select>
    </label>${help}</div>`;
  const configured = field.secret && field.configured
    ? ` ${uiBadge("set ····"+esc(field.last4), "ok")}` : "";
  const clear = field.secret && field.configured
    ? `<label class="conn-clear"><input type="checkbox" data-clear="${esc(field.name)}"> ${t("conn.clearSaved","Clear saved value")}</label>` : "";
  return `<div class="conn-field"><label class="fld" for="${id}"><span>${label}${configured}</span>
    <input id="${id}" data-field="${esc(field.name)}" type="${field.secret?"password":"text"}"
      value="${field.secret?"":esc(field.value)}" placeholder="${field.secret?(field.configured?t("conn.blankKeeps","Blank keeps the saved value"):t("conn.notConfigured","Not configured")):""}">
    </label>${clear}${help}</div>`;
}
async function saveConnection(key, force){
  const modal = document.querySelector(`dialog [data-connection="${key}"]`), values = {}, clear = [];
  if (!modal) return;
  modal.querySelectorAll("[data-field]").forEach(el => values[el.dataset.field] = el.type === "checkbox" ? (el.checked ? "1" : "") : el.value);
  modal.querySelectorAll("[data-clear]").forEach(el => { if (el.checked) clear.push(el.dataset.clear); });
  const msg = document.getElementById(`connection-msg-${key}`);
  msg.textContent = force ? t("ui.savingUnverified","saving without a successful test…") : t("ui.saving","saving…");
  const r = await postJSON("/api/connections", {key, values, clear, force:!!force});
  if (!r.ok && r.can_force) {
    msg.innerHTML = `${esc(r.error)} ${uiButton(t("ui.saveAnyway","Save anyway"), {level: "secondary", cls: "conn-force", onclick: `saveConnection('${esc(key)}',true)`})}`;
  } else if (!r.ok) {
    msg.textContent = r.error || t("ui.failed","failed");
  } else {
    closeConnectionModal();
    await refresh();
  }
}
async function testConnection(key){
  const msg = document.getElementById(`connection-msg-${key}`);
  if (msg) msg.textContent = t("ui.testing","testing…");
  const r = await postJSON("/api/connections/test", {key});
  if (!r.status) {
    if (msg) msg.textContent = r.error || t("ui.failed","failed");
    return;
  }
  const display = connectionStatusDisplay(r.status);
  const status = document.getElementById("connection-modal-status");
  if (status) {
    status.className = `connstatus ${display.className}`;
    status.innerHTML = `<span class="conndot"></span>${esc(display.label)}`;
  }
  const detail = document.getElementById("connection-modal-status-detail");
  if (detail) detail.textContent = r.status.message || "";
  const checked = document.getElementById("connection-modal-checked");
  if (checked) checked.textContent = r.status.checked_at ? `Last checked ${r.status.checked_at}` : "";
  if (msg) msg.textContent = r.status.message || display.label;
  await refresh();
}
async function saveProvider(provider){
  const info = (D.providers || []).find(x => x.key === provider);
  const field = info && info.fields[0] && document.getElementById(`provider-${provider}-${info.fields[0].name}`);
  const payload = {provider};
  if (field && field.value) payload.key = field.value;
  // Models are global fields for the *current* provider. Switching cards must
  // omit them so apply_provider selects the new provider's own default.
  if (provider === stProvider()) {
    const model = document.getElementById("provider-model"), small = document.getElementById("provider-small-model"), base = document.getElementById("provider-base-url"), custom = document.getElementById("provider-custom-key");
    if (model) payload.model = model.value;
    if (small) payload.small_model = small.value;
    if (base) payload.base_url = base.value;
    if (custom && custom.value) payload.custom_key = custom.value;
    if (document.getElementById("provider-clear-custom-key")?.checked) payload.custom_key = "";
  }
  const r = await postJSON("/api/providers", payload);
  if (!r.ok) alert(r.error || "Provider update failed"); else refresh();
}
function stProvider(){ return (D.settings || {}).provider || "anthropic"; }

// 内部 key 保持英文：connectionDisplayGroup 返回的就是这些词，分组靠它们对上。
// 中文只出现在渲染时的标题里（conn.group.* 那几条译文）。
// 曾经把这里的元素也换成了 t()，结果 grouped["Channels"] 变成 undefined，
// Connections 页面整页崩 —— t() 是给人看的，不是给代码对 key 用的。
const CONNECTION_GROUPS = ["Channels", "Productivity", "Memory", "Tools"];
// "Memory", not "Storage". The registry already calls this group "Memory &
// Storage"; the display map was dropping the half that says what these
// actually are. Notion is the episodic store, Supabase the semantic one, and
// every hosted memory service that joins them is semantic too — none of it is
// generic storage, and Memory is one of the four pillars the rest of the
// dashboard is organised around.
const CONNECTION_GROUP_MAP = {
  "Channels": "Channels",
  "Calendar & Productivity": "Productivity",
  "Memory & Storage": "Memory",
  "Search & Observability": "Tools",
};

function connectionDisplayGroup(item){
  return CONNECTION_GROUP_MAP[item.group] || "Tools";
}

function connectionStatusDisplay(status){
  // 比较用的是原始状态值，className 是 CSS 类名 —— 两者都不能过 t()。
  // 这个坑踩过一次：批量翻译时把 "connected" 无差别包成了 t(...)，结果
  // state 永远不等于翻译后的“已连接”，所有卡片都掉到兜底分支显示“未配置”，
  // 而且 CSS 类变成中文、状态圆点的颜色也一起没了。
  // 只有给人看的 label 该翻译。
  const state = (status && status.state) || "not_configured";
  if (state === "connected") return {label: t("conn.state.connected", "connected"), className: "connected"};
  if (state === "error") return {label: t("conn.state.error", "error"), className: "error"};
  // "configured" means every required field is filled and the extra is
  // installed — it just hasn't been probed. That is not a warning, so it must
  // not wear the amber "needs setup" pill: this state covers most of a working
  // setup on first visit, and colouring it like a problem told every new user
  // their Notion and Tavily needed fixing when they were fine.
  if (state === "configured") return {label:t("conn.state.configured","configured · not tested"), className:"configured"};
  if (state === "installed_but_unconfigured") return {label:t("conn.state.needsSetup","needs setup"), className:"needs-setup"};
  return {label:t("conn.state.notConfigured","not configured"), className:"not-configured"};
}

function connectionCard(item){
  const display = connectionStatusDisplay(item.status);
  const action = item.status && item.status.state !== "not_configured" ? "Edit" : "Configure";
  // Say WHY on the card. "needs setup" covers two unrelated fixes — a missing
  // value ("missing NOTION_TOKEN") and a missing package ("missing notion
  // extra", which wants a pip install, not a key) — and the reason used to be
  // hidden until you opened the modal. The message repeats the label for
  // connected/configured, so only show it where it adds something.
  const why = (item.status && item.status.message
    && (item.status.state === "installed_but_unconfigured" || item.status.state === "error"))
    ? `<div class="connwhy">${esc(item.status.message)}</div>` : "";
  return uiCard(`
    <img class="provlogo connlogo" src="/static/logos/connections/${esc(item.key)}.svg" alt="">
    <div class="connstatus ${display.className}"><span class="conndot"></span>${esc(display.label)}</div>
    ${why}
    <div class="conndesc">${esc(item.what)}</div>
    <div class="provactions connactions">
      ${uiButton(action, {level: "secondary", onclick: `openConnectionModal('${esc(item.key)}')`})}
    </div>`, {title: esc(item.name), cls: "provcard conncard"});
}

function connectionsGrid(items){
  const grouped = Object.fromEntries(CONNECTION_GROUPS.map(group => [group, []]));
  items.forEach(item => grouped[connectionDisplayGroup(item)].push(item));
  return CONNECTION_GROUPS.map(group => `<section class="connsection">
    <h2>${t("conn.group." + group.toLowerCase(), group)}</h2>
    <div class="provgrid conngrid">${grouped[group].map(connectionCard).join("")}</div>
  </section>`).join("");
}

// The connection dialog. openDialog (ui.js) owns the <dialog>: Escape and a
// click on the scrim close it natively, and onClose tidies up however it closed.
function openConnectionModal(key){
  const item = ((D && D.connections) || []).find(connection => connection.key === key);
  if (!item) return;
  markEditing();
  const display = connectionStatusDisplay(item.status);
  const status = item.status || {};
  const fields = item.fields.map(field => connectionField(item.key, field)).join("");
  const setup = (item.install_command || item.setup_url) ? uiNotice("note", `
        ${item.install_command?`<code>${esc(item.install_command)}</code>`:""}
        ${item.setup_url?`<a href="${esc(item.setup_url)}" target="_blank" rel="noopener noreferrer">${t("conn.setupGuide","Setup guide ↗")}</a>`:""}`) : "";
  const d = openDialog(`<div data-connection="${esc(item.key)}">
      <header class="connmodal-head">
        <img class="provlogo connlogo" src="/static/logos/connections/${esc(item.key)}.svg" alt="">
        <div class="connmodal-title">
          <h3 id="connection-modal-title">${esc(item.name)}</h3>
          <div class="connstatus ${display.className}" id="connection-modal-status"><span class="conndot"></span>${esc(display.label)}</div>
        </div>
        ${uiButton(t("ui.close","Close"), {level: "tertiary", size: "sm", cls: "connmodal-close", onclick: "closeConnectionModal()", attrs: 'aria-label="Close"'})}
      </header>
      <p class="conndesc connmodal-desc">${esc(item.what)}</p>
      <div class="connmodal-meta">
        <span id="connection-modal-status-detail">${esc(status.message || "")}</span>
        <span id="connection-modal-checked">${status.checked_at?`Last checked ${esc(status.checked_at)}`:""}</span>
      </div>
      ${setup}
      <div class="connection-fields">${fields}</div>
      <div class="dialog-foot">
        <span class="connmodal-message" id="connection-msg-${esc(item.key)}" aria-live="polite"></span>
        ${uiButton(t("ui.save","Save"), {level: "primary", onclick: `saveConnection('${esc(item.key)}')`})}
        ${uiButton(t("ui.testConnection","Test connection"), {level: "secondary", onclick: `testConnection('${esc(item.key)}')`})}
      </div>
    </div>`, {label: item.name, onClose: () => {
      editing = false;
      if (activeView === "connections") render();
    }});
  // openDialog focuses the first control, which is Close; start on a field instead.
  d.querySelector(".connection-fields input, .connection-fields select")?.focus();
}

function closeConnectionModal(){
  closeDialog();
}

// Escape is native to <dialog> now; kept for anything that still calls it.
function connectionModalKeydown(event){
  if (event.key === "Escape") closeConnectionModal();
}

const VIEWS = {
  models(d){
    // Provider card grid (logo / status dot / edit / enable-disable). Editing
    // happens in a modal opened from a card; both live in js/models.js.
    return modelsGrid(d);
  },
  connections(d){
    const items = d.connections || [];
    return items.length ? connectionsGrid(items) : uiCard(`<span class="empty">${t("conn.empty","No integrations registered.")}</span>`);
  },
  // Gateway: ONE unified conversation across every channel (dashboard, voice,
  // cli) — the same loop + memory answer all of them. Each message is
  // tagged with where it came in, Hermes-style. You type in the dock on the right.
  // Gateway = an INBOX of conversations (like Slack/Intercom): one row per
  // conversation, tagged with its channel(s). Click one to open it in the chat
  // dock (the active thread). No longer a flat stream that duplicates the dock.
  gateway(d){
    const sessions = d.sessions || [];
    let h = `<div class="meta" style="margin-bottom:var(--space-3)">${t("gw.intro","Every conversation across every channel — web, voice, terminal — answered by the same brain. Click one to open it in the chat dock. This is the inbox; the dock is the open thread.")}</div>`;
    if (!sessions.length)
      return h + uiCard(`<span class="empty">no conversations yet — say something in the chat dock &rarr;</span>`);
    h += sessions.map(s => uiRow(gwTags(s),
      `${esc(s.title||s.id)} <span class="meta" style="font-weight:400;white-space:nowrap">· ${sessionMeta(s)}</span>`,
      esc(s.last||""),
      {onclick: `openConversation('${esc(s.id)}')`, cls: s.id === SESSION ? "on" : ""})).join("");
    return h;
  },
  overview(d){
    const s = d.stats;
    const u = d.usage || {total_cost:0};
    return `${uiStatBand([
        {label:t("stat.spent","spent"), value:money(u.total_cost), sub:t("stat.allTime","all-time"), tone:"ok"},
        {label:t("stat.avgTurn","avg turn"), value:secs(s.latency_avg)},
        {label:t("stat.turns","turns"), value:s.turns}, {label:t("stat.toolCalls","tool calls"), value:s.tool_calls},
        {label:t("stat.facts","facts"), value:d.facts.length}, {label:t("stat.events","events"), value:d.calendar.length},
      ])}
    <h2>${t("ov.gateTitle","Retrieval gate — the hero decision")}</h2>${gateSplit(s)}
    <h2 style="margin-top:var(--space-6)">${t("ov.archTitle","Architecture — click any box")} <span class="arch-status"></span></h2>
    ${archSVG(d)}
    <h2>${t("set.graphTitle","Graph workflows")}${t("ov.graphSub"," — when a turn needs shape")}</h2>
    ${graphPanel(d)}
    <h2>${t("ov.latestTurn","Latest turn")}</h2>${d.turns.length?turnCard(d.turns[0]):uiCard('<span class="empty">no turns yet — talk to Waku first</span>')}`;
  },
  loop(d){
    return d.turns.length ? d.turns.map(turnCard).join("") : uiCard(`<span class="empty">no turns yet</span>`);
  },
  // Graph workflows: the loop's sibling. The chart is rendered from the
  // engine's own describe() (served in d.graph.workflows) so it can never
  // show a shape the engine doesn't run. Nothing here is a mode switch —
  // the harness routes every message itself; this tab just tells the story.
  graph(d){
    const g = d.graph || {enabled:false, workflows:[], stats:{quick:0, full:0}};
    let h = `<div class="meta" style="margin-bottom:var(--space-3)">${t("graph.intro","The loop is one agent turn: the model picks tools until it stops. Some work has <b>shape</b> — steps that can run at the same time, and explicit 'if this, go there' routing. A <b>graph workflow</b> makes that shape first-class: nodes (each does one job) connected by edges (what happens next). The loop did not change one line — the <code>full_agent</code> node below IS the same loop, running as one step. The harness routes every message itself — and workflows you can also call BY NAME from the chat box: type <code>/graphs</code> to see them.")}</div>`;
    if (!g.enabled)
      h += uiCard(`<b>${t("graph.offTitle","Off")}</b> — ${t("graph.offBody","every turn currently runs the classic loop.")}
        <div class="meta" style="margin-top:calc(var(--spacing) * 1.5)">Switch on <b>graph workflows</b> in
        ${uiLink(t("ui.behaviourTab","Behaviour"), "#settings")}, or set
        <code>WAKU_GRAPH_WORKFLOWS=1</code> in <code>.env</code>. Any failure anywhere fails open to the
        plain loop — this can never lose a reply, only save time and tokens.</div>`);
    // The two workflows are two different JOBS with different triggers, which is
    // the thing the page has to make obvious — otherwise two stacked charts read
    // like two options you pick between.
    const NOTE = {
      triage: `<b>${t("graph.note.triageTitle","Runs itself, on every message.")}</b> ${t("graph.note.triageBody","Gated by the graph-workflows flag. Solid arrows = always, dashed = the router's choice. full_agent is the ordinary loop running as one node — a graph does not replace the loop, it arranges calls to it.")}`,
      gather: `<b>${t("graph.note.gatherTitle","Runs when you start it")}</b> ${t("graph.note.gatherBody","— make gather or the button below — and ignores the flag entirely. The four scans have no dependencies on each other, so the engine runs them in ONE WAVE: together, not in turn. It proposes and never acts; the digest lands in the outbox for you to read.")}`,
    };
    (g.workflows || []).forEach(w => {
      if (!w) return;
      h += `<h2>${esc(w.name)} — live topology <span class="arch-status"></span></h2>`;
      const tot = g.stats.quick + g.stats.full;
      const extra = w.name === "triage" && tot
        ? ` · ${g.stats.quick} quick / ${g.stats.full} full so far` : "";
      h += uiCard(`${graphSVG(w)}
        <div class="meta" style="margin-top:var(--space-2)">${NOTE[w.name] || ""}${extra} ·
        drawn from the engine's own <code>describe()</code>, so this picture cannot drift from the code</div>`);
      if (w.name === "gather") h += graphRunPanel();
    });
    const gturns = (d.turns||[]).filter(t => t.graph && t.graph.route);
    h += `<h2>${t("graph.turnsTitle","Graph turns")}</h2>`;
    h += gturns.length
      ? gturns.slice(0,20).map(t => uiCard(`
          <div class="u">${esc(t.user_message)}</div>
          <div class="meta" style="margin-top:var(--spacing)">${uiBadge(`graph · ${esc(t.graph.route)}`, t.graph.route==="quick" ? "neutral" : "value")}
            <span class="meta" style="margin:0">${esc(t.graph.reason||"")}</span></div>
          <div class="r">${renderMarkdown(t.reply||"")}</div>`)).join("")
      : uiCard(`<span class="empty">no graph turns yet — ${g.enabled
          ? 'say "thanks!" in the chat and watch it take the quick door'
          : "switch the flag on first"}</span>`);
    return h;
  },
  memory(d, sub){
    sub = sub || "overview";
    const tabs = [["overview",t("tab.overview","Overview")],["semantic",t("tab.semantic","Semantic"),d.facts.length],
      ["episodic",t("tab.episodic","Episodic"),d.episodes.length],["skills",t("tab.skills","Skills"),d.skills.length],
      ["soul",t("tab.soul","SOUL")],["consolidation",t("tab.consolidation","Consolidation"),d.chat_pending]];
    let h = subtabBar("memory", tabs, sub);
    if (sub==="semantic") return h + memSemantic(d);
    if (sub==="episodic") return h + memEpisodic(d);
    if (sub==="skills") return h + memSkills(d);
    if (sub==="soul") return h + memSoul(d);
    if (sub==="consolidation") return h + memConsolidation(d);
    return h + memOverview(d);
  },
  settings(d){
    const st = d.settings || {providers:[]};
    return `<h2>${t("set.experimental","Experimental tools")}</h2>${uiCard(`
      <div class="meta" style="margin-bottom:var(--space-2)">${t("set.experimentalIntro","Opt in to local coding delegation for chat.")}</div>
      <label class="fld">${t("set.subAgent","Sub-agent delegation")}<select id="set-experimental" onfocus="markEditing()">
        <option value="" ${!st.experimental?"selected":""}>${t("ui.off","off")}</option>
        <option value="1" ${st.experimental?"selected":""}>${t("ui.on","on")}</option>
      </select></label>
      ${uiButton(t("ui.save","Save"), {level: "primary", onclick: "saveSettings()"})}<span class="meta" id="set-msg"></span>`)}
    <h2>${t("set.graphTitle","Graph workflows")}</h2>${uiCard(`
      <div class="meta" style="margin-bottom:var(--space-2)">Off by default. When on, <b>every</b> message is triaged
        through a graph first: a small model classifies it while today's calendar loads in parallel — trivial
        messages get a fast small-model reply, real tasks run the exact same loop as a node. This flag governs
        the AUTOMATIC door only — workflows you call by name (<code>/gather</code>) run either way. Any
        failure fails open to the plain loop. Watch it live on the
        ${uiLink(t("ui.graphTab","Graph"), "#graph")} tab.</div>
      <label class="fld">${t("set.triageFirst","Triage-first turns")}
        <select id="set-graph-workflows" onfocus="markEditing()">
          <option value="" ${!st.graph_workflows?"selected":""}>${t("set.graphOff","off — every turn runs the classic loop (default)")}</option>
          <option value="1" ${st.graph_workflows?"selected":""}>${t("set.graphOn","on — triage graph routes each message")}</option>
        </select></label>
      <div style="margin-top:var(--space-3)">${uiButton(t("ui.saveSwitch","Save &amp; switch"), {level: "primary", onclick: "saveSettings()"})}
        <span class="meta" style="margin-left:var(--space-2)">${t("set.rebuilds","rebuilds the agent in-process — no restart")}</span></div>
    `)}
    <h2>${t("set.devTitle","Developer mode")}</h2>${uiCard(`
      <div class="meta" style="margin-bottom:var(--space-2)">${t("set.devIntro","Show the pages that are only useful if you run this yourself: the graph, the loop, ops and tracing, the database with its SQL console, and the two races. Off keeps the sidebar to Overview, Memory, Tools, Models, Connections and Behaviour. The page reloads — the sidebar and the current page both change. Your own choice here wins over the --dev flag, so turning it off sticks.")}</div>
      <label class="fld">${t("set.devLabel","Developer mode")}
        <select id="set-dev-mode">
          <option value="" ${!DEV_ON?"selected":""}>${t("ui.off","off")}</option>
          <option value="1" ${DEV_ON?"selected":""}>${t("ui.on","on")}</option>
        </select></label>
      <div style="margin-top:var(--space-3)">${uiButton(t("ui.save","Save"), {level: "primary", onclick: "applyDevMode()"})}
        <span class="meta" style="margin-left:var(--space-2)">${t("set.devReload","saves and reloads the page")}</span></div>
    `)}`;
  },
  tools(d, sub){
    const tools = d.tools || {catalog:[], mcp:{configured:false,servers:[],live:false}};
    sub = sub || "available";
    const tabs = [["available",t("tab.available","Available"),tools.catalog.length],["results",t("tab.results","Results")],
      ["mcp",t("tab.mcp","MCP"),tools.mcp.servers.length||null]];
    let h = subtabBar("tools", tabs, sub);
    if (sub === "results") return h + toolsResults(d);
    if (sub === "mcp") return h + toolsMCP(tools);
    // Available: what the agent CAN do (grouped by origin), not just what it did.
    h += `<div class="meta" style="margin-bottom:var(--space-3)">${t("tools.availableIntro","The capabilities the agent can call this turn. A tool is a name + description the model reads, a JSON schema, and a Python function — that's it. Connect more via MCP.")}</div>`;
    const SRC = [["flagship",t("tool.src.flagship","Flagship task — scheduling")],["web",t("tool.src.web","Web search")],
      ["self-management",t("tool.src.self","Self-management — it edits its own memory")],
      ["mcp",t("tool.src.mcp","MCP servers")],["other",t("tool.src.other","Other")]];
    SRC.forEach(([key,label]) => {
      const items = tools.catalog.filter(c => c.source === key);
      if (!items.length) return;
      h += `<h2>${label}</h2>`;
      h += items.map(c => uiCard(`
        <div class="tn"><code>${esc(c.name)}</code> ${uiBadge(esc(key), key==="mcp" ? "ok" : "neutral")}</div>
        <div class="td">${esc(c.description)}</div>`, {cls: "toolcard", size: "sm"})).join("");
    });
    // Roadmap: whiteboard boxes not wired in yet — set expectations, don't over-promise.
    if ((tools.planned||[]).length){
      h += `<h2>${t("tools.comingSoon","Coming soon ")}<span class="meta" style="font-weight:400">· ${t("tools.comingSoonSub","on the architecture chart, not wired in yet (opt in with WAKU_EXPERIMENTAL=1)")}</span></h2>`;
      h += tools.planned.map(p => uiCard(`
        <div class="tn"><code>${esc(p.name)}</code> ${uiBadge(`soon · ${esc(p.box)}`)}</div>
        <div class="td">${esc(p.description)}</div>`, {cls: "toolcard toolcard-soon", size: "sm"})).join("");
    }
    return h;
  },
  database(d, sub){
    // The persistence layer itself — one SQLite file, real tables, FTS5 index.
    // "Data" in the nav (plainer than "state.db"), but we keep saying state.db
    // because that's literally the filename you can open.
    const db = d.db || {tables:[], all_tables:[], fts:[], size:0, path:""};
    const tables = db.tables || [];
    sub = sub || "overview";
    const tabs = [["overview",t("tab.overview","Overview")],
      ...tables.map(t => [t.name, t.name, t.count]),
      ["query",t("tab.sql","SQL console")]];
    let h = subtabBar("database", tabs, sub);
    if (sub === "query") return h + dbQueryView();
    if (sub !== "overview"){
      const table = tables.find(x => x.name === sub);
      if (!table) return h + uiCard(`<span class="empty">${t("ui.empty.noTable","no such table")}</span>`);
      const notionNote = (table.name === "episodes" && d.episodes_source === "notion")
        ? `<div class="meta" style="margin-bottom:var(--space-2)">${t("db.notionNote","Episodes currently live in Notion — see Memory ▸ Episodic. The rows below are the old local copy in state.db.")}</div>` : "";
      return h + notionNote + `<div class="meta" style="margin-bottom:var(--space-2)">${DB_DESC[table.name]||""}</div>` + dbTable(table);
    }
    const kb = (db.size/1024).toFixed(1);
    h += uiNotice("note", `<b>Database vs Memory.</b> <span class="r">This is the raw persistence layer — the literal SQLite
      tables. The ${uiLink(t("ui.memoryTab","Memory tab"), "#memory")} is the friendly
      view of the same rows (facts, episodes, skills, persona). One file, two altitudes. Where Hermes
      uses a <code>MEMORY.md</code> file, Waku uses these queryable tables — and mirrors them to a
      readable <code>MEMORY.md</code> too.</span>`);
    h += uiCard(`
      <div class="u" style="font-family:var(--face-mono);font-size:var(--text-xs);word-break:break-all">${esc(db.path)}</div>
      <div class="meta">${kb} KB on disk · SQLite + FTS5 · open it yourself: <code>sqlite3 .waku/state.db</code></div>
      <div class="meta" style="margin-top:var(--space-2)">${reveal("state.db","reveal state.db in Finder")} &nbsp;·&nbsp; ${reveal("","open the .waku folder")}</div>`);
    h += `<h2>${t("db.tablesTitle","Tables — click a tab above, or a row here")}</h2>`;
    h += table([t("tbl.table","table"), t("tbl.rows","rows"), t("tbl.whatitholds","what it holds")], tables.map(t =>
      `<tr><td>${uiLink(`<code>${esc(t.name)}</code>`, `#database/${esc(t.name)}`)}</td>
        <td class="meta">${t.count}</td><td class="meta">${DB_DESC[t.name]||""}</td></tr>`));
    h += `<h2>${t("db.ftsTitle","FTS5 — the keyword index")}</h2>` + uiCard(`The <code>*_fts</code> virtual tables (and their
      <code>*_fts_data</code>/<code>*_fts_idx</code> shadows) make memory searchable by keyword — no embeddings,
      no vector DB. This is the "keyword top-k" the retrieval gate queries.
      <div class="meta" style="margin-top:var(--space-2)">all ${db.all_tables.length} tables: ${db.all_tables.map(t=>`<code>${esc(t)}</code>`).join(" ")}</div>`);
    return h;
  },
  ops(d){
    const s = d.stats;
    const u = d.usage || {calls:0,total_in:0,total_out:0,total_cost:0,by_day:[],by_provider:[]};
    let h = uiStatBand([
        {label:t("stat.spent","spent"), value:money(u.total_cost), sub:t("stat.allTime","all-time"), tone:"ok"},
        {label:t("stat.tokensIn","tokens in"), value:u.total_in.toLocaleString(), sub:"all-time"},
        {label:t("stat.tokensOut","tokens out"), value:u.total_out.toLocaleString(), sub:t("stat.allTime","all-time")},
        {label:t("stat.llmCalls","LLM calls"), value:u.calls.toLocaleString()},
        {label:t("stat.avgTurn","avg turn"), value:secs(s.latency_avg)}, {label:t("stat.toolErrors","tool errors"), value:`${s.tool_errors}`},
      ]);
    // Eval verdicts are "pass" / "fail" / anything else (skipped, not run).
    const verdict = v => v === "pass" ? "ok" : v === "fail" ? "bad" : "neutral";

    h += `<h2>${t("ops.spendTitle","Spend")} <span class="meta" style="font-weight:400">· ${t("ops.spendSub","permanent ledger — survives a demo reset")}</span></h2>`;
    h += uiCard(`<span class="r prose">Every LLM call's tokens are logged to
      <code>.waku/usage.jsonl</code> (append-only, never wiped). Dollar cost is estimated from tokens
      × current pricing — the tokens are the ground truth.</span>`,
      {footer: reveal("usage.jsonl","open usage.jsonl")});
    if ((u.by_provider||[]).length){
      h += table([t("tbl.provider","provider"), t("tbl.LLMcalls","LLM calls"), t("tbl.tokensin","tokens in"), t("tbl.tokensout","tokens out"), t("tbl.cost(est)","cost (est)")], u.by_provider.map(p =>
        `<tr><td><code>${esc(p.provider)}</code></td><td class="meta">${p.calls}</td>
          <td class="meta">${p.in.toLocaleString()}</td><td class="meta">${p.out.toLocaleString()}</td>
          <td class="meta">${money(p.cost)}</td></tr>`));
    }
    if ((u.by_day||[]).length){
      h += `<h2>${t("ops.spendPerDay","Spend per day")}</h2>`;
      h += table([t("tbl.day","day"), t("tbl.LLMcalls","LLM calls"), t("tbl.tokensin","tokens in"), t("tbl.tokensout","tokens out"), t("tbl.cost(est)","cost (est)")], u.by_day.map(r =>
        `<tr><td class="meta">${esc(r.date)}</td><td class="meta">${r.calls}</td>
          <td class="meta">${r.in.toLocaleString()}</td><td class="meta">${r.out.toLocaleString()}</td>
          <td class="meta">${money(r.cost)}</td></tr>`));
    }

    h += `<h2>${t("ops.gateWhich","Retrieval gate — which turns used memory")}</h2>${gateSplit(s)}`;
    const decided = d.turns.filter(t => t.gate);
    if (decided.length){
      h += `<div class="meta" style="margin:var(--space-2) 0">${t("ops.gateDecisions","The actual decisions (what was skipped vs retrieved), most recent first:")}</div>`;
      h += table([t("tbl.turn","turn"), t("tbl.decision","decision"), t("tbl.why","why")], decided.slice(0,10).map(t =>
        `<tr><td>${esc((t.user_message||"").slice(0,44))}</td>
          <td>${uiBadge(esc(t.gate.decision), t.gate.decision==="skip" ? "neutral" : "ok")}</td>
          <td class="meta">${esc(t.gate.reason||"")}</td></tr>`));
    }

    h += `<h2>${t("ops.gateTitle","Release gate")} <span class="meta" style="font-weight:400">· ${t("ops.gateSub","the ship/no-ship check")}</span></h2>`;
    h += uiCard(`<span class="r prose">Before you ship a change (new prompt, swapped model, tuned
      retrieval), <code>make gate</code> runs both eval suites: deterministic must pass 100%, the judge must
      clear its threshold. It's manual — you run it — so there's one record per run. The history below grows
      each time you run it.</span>`);
    h += d.eval_report ? uiCard(`
        ${uiBadge(`deterministic · ${esc(d.eval_report.deterministic)}`, verdict(d.eval_report.deterministic))}
        <span style="margin-left:var(--space-2)">${uiBadge(`llm-judge · ${esc(d.eval_report.judge)}`, verdict(d.eval_report.judge))}</span>
        <div class="meta">last run ${esc(d.eval_report.ran_at)} — re-run with <code>make gate</code></div>`)
      : uiCard(`<span class="empty">never run yet — run <code>make gate</code> to populate this</span>`);

    if ((d.eval_history||[]).length){
      const cnt = s => s ? `${s.passed||0} pass · ${s.failed||0} fail` : "—";
      h += `<h2>${t("ops.evalHistory","Eval history")}</h2>`;
      h += table([t("tbl.when","when"), t("tbl.deterministic","deterministic"), t("tbl.llmjudge","llm-judge"), t("tbl.counts","counts")], d.eval_history.map(r =>
        `<tr><td class="meta">${esc((r.ran_at||"").replace("T"," ").slice(0,19))}</td>
         <td>${uiBadge(esc(r.deterministic), verdict(r.deterministic))}</td>
         <td>${uiBadge(esc(r.judge), verdict(r.judge))}</td>
         <td class="meta">det ${cnt(r.suites&&r.suites.deterministic)} · judge ${cnt(r.suites&&r.suites.judge)}</td></tr>`));
    }

    h += `<h2>${t("ops.slowest","Slowest turns")}</h2>`;
    const slow = [...d.turns].filter(t=>t.latency_ms!=null).sort((a,b)=>b.latency_ms-a.latency_ms).slice(0,6);
    h += table([t("tbl.turn","turn"), t("tbl.latency","latency"), t("tbl.cost","cost"), t("tbl.tools","tools")], slow.map(t =>
      `<tr><td>${esc((t.user_message||"").slice(0,48))}</td><td class="meta">${secs(t.latency_ms)}</td><td class="meta">${money(t.cost||0)}</td><td class="meta">${(t.tools||[]).map(x=>x.tool).join(", ")||"—"}</td></tr>`));

    h += `<h2>${t("ops.traceTitle","Tracing")} <span class="meta" style="font-weight:400">· ${t("ops.traceSub","every turn as JSONL, always on")}</span></h2>`;
    if ((d.trace_errors||[]).length){
      h += d.trace_errors.map(e => uiCard(`${uiBadge("trace encoding error", "bad")}
        <div class="meta" style="margin-top:var(--space-2)"><code>${esc(e.file)}</code> — ${esc(e.error)}</div>`)).join("");
    }
    h += uiCard(`<span class="r prose">${s.trace_files} trace file(s) in <code>traces/</code>${
      d.trace_file?` (newest: <code>${esc(d.trace_file)}</code>)`:""}.
      A trace is just "what happened, in order" — here are the most recent lines:</span>`,
      {footer: reveal("traces","open the traces folder")});
    h += (d.trace_tail||[]).length ? table([t("tbl.event","event"), t("tbl.detail","detail"), t("tbl.when","when")], d.trace_tail.map(e =>
        `<tr><td><code>${esc(e.type)}</code></td><td class="meta">${esc(String(e.detail).slice(0,60))}</td>
          <td class="meta">${esc((e.ts||"").replace("T"," ").slice(0,19))}</td></tr>`))
      : uiCard(`<span class="empty">no trace lines yet — talk to Waku</span>`);
    h += `<div class="meta" style="margin-top:var(--space-2)">${t("ops.spanWaterfalls","Span waterfalls:")} <code>make trace</code> + <code>OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317</code>.</div>`;

    if (d.wake_scans.length){
      h += `<h2>${t("ops.wakeTitle","Voice — wake near-misses")}</h2>`;
      h += table([t("tbl.heard","heard"), t("tbl.when","when")], d.wake_scans.map(w =>
        `<tr><td>${esc(w.heard)}</td><td class="meta">${esc((w.ts||"").replace("T"," ").slice(0,19))}</td></tr>`));
    }
    return h;
  },
};
