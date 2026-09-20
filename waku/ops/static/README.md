# Dashboard frontend — the map

Plain static files served as-is by `waku/ops/dashboard.py` (a stdlib HTTP
server). **No build step, no framework, no bundler, no dependencies.** Edit these
files to change the UI; edit `dashboard.py` to change the server/API.

- `index.html` — the shell (sidebar nav, `<main>`, chat dock) + the ordered
  `<script>` tags.
- `style.css` — one flat file of rules; every value comes from the tokens in
  `design/` (see "Design system" below).
- `design/`, `fonts/` — the Waku Memory design system and its three fonts.
- `js/` — the app, split by concern (below).

## The files (`js/`), in load order

They are **classic scripts sharing one global scope** — a `function`/`let`/`const`
in one file is visible to all the others. Order matters at both ends:
**`i18n.js` must load first** (it defines `t()`, which the rest of the app calls)
and **`main.js` loads last** (it runs the bootstrap).

| file | what lives here |
|------|-----------------|
| `i18n.js`    | `t(key, "English")` + the `data-i18n` sweep — the Chinese interface. In English mode it returns the string it was handed and touches no DOM |
| `util.js`    | `esc`, markdown renderer, core globals (`D`, `editing`), `postJSON`, `reveal`, `stampSlots` |
| `theme.js`   | the system / light / dark toggle (`cycleTheme`), stored as `waku-theme` like the Memory console |
| `memory.js`  | inline Memory / SOUL / skill editing actions |
| `models.js`  | `applyModel` (the one `/api/settings` writer), model picker / catalog / pins |
| `render.js`  | formatters + chat card renderers (`stagesRow`/`teleFooter`) + chatlog + streaming + `sendChat` |
| `diagram.js` | `archSVG` (the architecture chart) **and** its live animation (`STAGE`/`hot`/`pollEvents`) |
| `graph.js`   | graph workflows: data-driven topology chart (`graphSVG` from `d.graph.workflows`), the Overview panel (`graphPanel`), and `animateGraphStage` for `graph_*`/`route` events |
| `views.js`   | subtab/db helpers, SQL console, Memory/Tools sub-views, the `VIEWS` router object |
| `compare.js` | the Model arena (`Arena` tab; internals keep the `compare` name) — race one message through several models at once |
| `dock.js`    | chat sessions/history (`loadThreadInto`), model chip, stats toggle |
| `main.js`    | `render`/`refresh` loop, resizers, voice, and the bootstrap (**loads last**) |

Data flows one way: `refresh()` (main.js) fetches `/api/data` into the global
`D`, then `render()` writes `VIEWS[hash](D)` into `#view`. Every mutation
(`applyModel`, `pinModel`, `saveFact`, …) calls `refresh()` when it's done.

## Rules that bite (read before editing)

- **Inline handlers need global names.** Buttons use `onclick="fn()"` in the
  HTML strings the JS generates. `fn` must stay a top-level name in some `js/`
  file. Rename/move a handler and forget its call sites → the button silently
  breaks. `test_static_assets.py` guards this.
- **`archSVG` is byte-frozen — do not rewrite the architecture chart.** It emits
  `data-node="…"`/`data-edge="…"` ids that the `STAGE` map (same file) drives the
  live animation from. If you ever change a node/edge id, change it in both
  places. (Both are in `diagram.js` precisely so they stay together.)
- **The graph chart is data-driven — never hand-edit a topology.** `graphSVG`
  renders `Graph.describe()` served in `/api/data`, so the picture is provably
  what the engine runs (`test_graph_topology_payload.py` pins it). To change the
  chart's shape, change the workflow in `waku/graph/workflows/`. Graph ids are
  namespaced `g-<node>` / `g-<src>-<dst>` so they can never collide with archSVG's.
- **No build step / no framework / no new dependencies.** If you reach for one,
  stop — the whole point is that this reads and runs with nothing installed.
- **No emojis in UI** (project rule). Known pre-existing exception: the `★`/`☆`
  pin stars in `models.js` (typographic dingbats, not colour emoji) — left as-is.

## Chinese interface (`waku dashboard --zh`)

The UI ships in English; Chinese is an **overlay, not a fork**. Nothing here is
machine-translated, at build time or at runtime.

- `lang/zh.js` — the translations: one object, written by hand.
- `js/i18n.js` — `t(key, "English")`. The **English sentence stays in the code**
  as the fallback, so a missing key shows the English line — never a blank, never
  a bare key name. In English mode `t()` returns that fallback and the file does
  not touch the DOM, so the English path cannot break.
- `style-zh.css` — the two things Chinese needs and English does not: a CJK font
  stack (Instrument Sans has no Chinese glyphs, so Windows would fall back to a
  serif) and no `text-transform:uppercase` on the Chinese headings (it would turn
  the retained English nouns into "TOKEN").

`dashboard.py` injects those two tags only when `WAKU_LANG=zh`, which is what
`waku dashboard --zh` sets. The language is fixed at startup on purpose: a
runtime switcher would have to re-render every view, dialog and in-flight
animation, and that is a state machine nobody asked for.

Proper nouns stay English — Token, Prompt, Provider, MCP, Skill, Agent, Trace,
Loop, Gateway, JSONL, SQLite, Dashboard. That rule is at the top of `lang/zh.js`.

## Design system

How the dashboard looks, the token rules and the `js/ui.js` primitives are in
[docs/context/design-system.md](../../../docs/context/design-system.md). Read it
before changing how anything looks.

## Verifying a change (no JS test runner exists)

Three checks stand between a frontend edit and a blank page, in increasing order
of what they can see:

1. `node --check` — syntax. Run by `test_static_js_parses.py` over `js/` **and**
   `lang/`. Catches an unterminated string or a broken template literal.
2. `test_static_js_parses.py`'s shadowing guard — a local variable named `t`
   turns every `t("key","English")` call in that scope into a TypeError. Valid
   syntax, blank view.
3. `test_dashboard_renders.py` — **actually calls every view** with a fixture
   shaped like `/api/data`, via `evals/fixtures/dashboard_render_smoke.js`. This
   is the only one that catches a view that runs and throws: the server reports
   nothing, the console is the only witness, and the page is simply empty.

Beyond those, verify in the browser preview:
`waku dashboard` → hard-reload `localhost:7777` → click the sidebar tabs and the
chat dock → check the console shows **zero errors**.

The Python side (`dashboard.py` endpoints, `_thread_history`, pins, session
resume) *is* covered by `evals/deterministic/`.

**A running server does not pick up Python changes.** Static files here (`.js`,
`.css`, `index.html`) are read from disk on every request, so a hard-reload shows
them. But `dashboard.py` and everything it imports are held in memory — after
pulling or editing backend code, **restart `make dashboard`**, or the page renders
new markup against stale data (e.g. a new Settings panel that shows nothing because
the old route isn't sending its fields).
