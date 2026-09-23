# Commands

The `waku` command is installed with the package. In a checkout, the `make`
targets are aliases for the same things, plus the eval and tracing tools.

## waku

| Command | Does |
|---|---|
| `waku` | chat in the terminal |
| `waku serve` | the resident process: one Waku, shared by every gateway (`--zh`, `--dev` as below) |
| `waku dashboard` | the live cockpit at localhost:7777 — Overview, Memory, Tools, Models, Connections, Behaviour |
| `waku dashboard --dev` | the same, plus the developer pages (graph, loop, ops, database, the two races, model and connection setup) |
| `waku voice` | talk to it — the "waku waku" wake word, or push-to-talk (needs the `[voice]` extra) |
| `waku wechat` | the WeChat gateway's status (off by default — see below) |
| `waku wechat login` | scan a QR to bind one WeChat account (needs the `[wechat]` extra) |
| `waku wechat logout` | forget the credentials and the long-poll cursor |
| `waku brief` | a morning briefing from calendar + memory, run as a loop |
| `waku gather` | the same job as a graph: four sources fetched together, then one digest |
| `waku connections` | every integration and its health, including Waku Memory |
| `waku connect google` | sign in to Google Calendar (opens your browser) |
| `waku connect waku-memory` | connect Waku Memory, the memory shared with your other agents (opens your browser) |
| `waku mcp` | MCP servers, and which account each one knows you as |
| `waku mcp login <name>` | sign in again — as someone else, or after expiry |
| `waku mcp logout <name>` | forget a server's token |
| `waku skill install <url>` | install a community skill from a GitHub or Gist link |
| `waku skill export --to claude,codex` | copy Waku's skills to Claude Code and Codex (`--project` for `./.claude/skills`, `--force` to replace) |

In the dashboard chat, `/connect google` and `/connect waku-memory` do the same
as their `waku connect` commands, and `/help` lists the graph workflows.

`waku serve` and `waku dashboard` currently run the same process: the resident
host (see [resident-host-design.md](resident-host-design.md)) holds the one Waku
and the dashboard is its first gateway, so there is nothing for them to differ
on yet. `serve` is the name that grows gateways; `dashboard` stays because that
is what people already type.

## WeChat (optional, off by default)

One-to-one text with one bound WeChat account, over the iLink protocol the
[lab experiment](../lab/wechat-ilink/README.md) measured. It is a gateway: the
browser and WeChat share one Waku through the host, one turn at a time.

```bash
pip install -e '.[wechat]'   # only needed for the terminal QR
waku wechat login            # scan, then confirm on the phone
# then set WAKU_WECHAT=1 in .env and restart `waku serve`
waku wechat status           # enabled? logged in? cursor? interrupted messages?
waku wechat logout           # forget the credentials, keep the dedup record
```

What it does and does not do:

- Text only. An image, voice note, file or video gets a one-line reply saying so
  **without** running an agent turn.
- One account, direct messages. The bot cannot be added to an ordinary group.
- It cannot message you first: a reply needs the `context_token` from a recent
  inbound message, and the protocol has no "open a conversation" call.
- A turn's **final** reply is sent, once. Nothing streamed and nothing partial
  reaches WeChat.
- Off by default. With `WAKU_WECHAT` unset, `waku serve` starts the dashboard and
  nothing else; with it set but not logged in, the dashboard still starts and
  `status` says so. A WeChat problem never takes the browser down.
- Credentials and the cursor live in `.waku/wechat/`, which `.gitignore` covers.

If a turn is interrupted between being accepted and being answered, the message
is **not** retried (a retry could repeat a tool's side effects) and
`waku wechat status` lists it as interrupted rather than hiding it. The ordering
that makes this true is written out at the top of `waku/gateway/wechat.py`.

## make

| Command | Does |
|---|---|
| `make run` | chat with Waku in the terminal |
| `make dashboard` | the dashboard at localhost:7777 (restart it after pulling backend changes) |
| `make voice` | push-to-talk, or always-on with `WAKU_WAKE_WORD` |
| `make brief` · `make gather` | the morning briefing, as a loop or as a graph |

WeChat has no `make` target: it is one command with three verbs, and `make` is
for the things you run constantly.
| `make eval` | deterministic evals (0/1, no judge involved) |
| `make eval-judge` | LLM-as-judge evals (scored %, needs an API key) |
| `make gate` | the release gate: deterministic must pass, judge must clear its threshold |
| `make lint` | ruff over the code and the evals |
| `make trace` | trace waterfalls in Phoenix at localhost:6006 |
| `make shootout RUNS="…"` | the same tasks on different models, e.g. `RUNS="kimi:kimi-k3 anthropic:claude-opus-4-8"` |
| `make shootout-coding RUNS="…"` | a coding round through pi, scored by tests |

Tests live in `evals/`, not `tests/`. [evals.md](evals.md) explains the two
kinds.
