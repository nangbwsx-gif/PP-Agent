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
waku wechat login            # scan, confirm on the phone, then paste the two
                             # .env lines it prints
# then, in .env:
#   WAKU_WECHAT=1
#   WAKU_WECHAT_ALLOW=<the userId login just printed>
waku wechat status           # allowlist? logged in? cursor? undelivered replies?
waku wechat logout           # forget the credentials, keep the dedup record
```

**The allowlist is mandatory and fails closed.** An inbound message is checked
against `WAKU_WECHAT_ALLOW` **before anything can reach the host**, so `login`
prints the line but deliberately does not write it: silently editing a security
setting is worse than pasting one line. With the list empty, `status` says so and
nobody can talk to the bot. Unauthorised senders are counted and listed in
`status` so you can see who knocked; they get no reply.

What it does and does not do:

- Text only. An image, voice note, file or video gets a one-line reply saying so
  **without** running an agent turn.
- **One conversation, both doors.** The browser and WeChat ask on the same thread
  (`waku/runtime/conversation.py`), so what you say in one is in the other's
  working window — and History shows which door each message came through,
  because `chat_log.source` still records it per row.
- One account, direct messages. The bot cannot be added to an ordinary group.
- It cannot message you first: a reply needs the `context_token` from a recent
  inbound message, and the protocol has no "open a conversation" call.
- A turn's **final** reply is sent, once. Nothing streamed and nothing partial
  reaches WeChat.
- A message with **no `message_id`** is refused outright rather than handled.
  Without an id there is nothing to deduplicate on, so every re-delivery would
  run the turn — and its tools — again.
- Off by default. With `WAKU_WECHAT` unset, `waku serve` starts the dashboard and
  nothing else; with it set but not logged in, the dashboard still starts and
  `status` says so. A WeChat problem never takes the browser down.
- Credentials, the cursor and the pending replies live in `.waku/wechat/`, which
  `.gitignore` covers.

### What the delivery guarantees actually are

Stated plainly, because two of these are deliberate choices and one is a
protocol limit:

- **A turn runs at most once per message.** The id is claimed before the turn,
  so a re-delivery is a no-op. A crash *between* the claim and the reply leaves
  that one message unanswered: it is listed as `interrupted`, and it is **not**
  retried, because a retry could repeat a side-effecting tool call.
- **A reply is delivered at least once.** Failed sends go to a persistent outbox
  in `.waku/wechat/outbox.json` and are retried on the next flush and on the next
  start — never by re-running the turn. A retry whose first attempt actually
  landed sends the sentence twice, and that is the trade: a duplicate sentence
  beats a missing one or a doubled calendar event.
- **Chunks are confirmed individually.** Each chunk of a long reply is recorded
  as sent on its own, and each reuses a stable `client_id` derived from
  `(message_id, chunk index)`, so a retry is the same message to the server
  rather than a new one.
- **"Sent" means the API accepted it.** WeChat gives no delivery receipt, so
  nothing here proves the person saw it.
- **Not verified against the real API:** whether the server actually deduplicates
  on `client_id`. The lab never tested that, and if it does not, a retry that had
  in fact been delivered shows up twice. The outbox is what keeps that window
  small.
- **A reply whose `context_token` has gone stale cannot be delivered at all.** It
  stays in the outbox — visible in `status`, with its attempt count — because the
  protocol offers no way to re-open a conversation from this side.

What the ordering guarantees are, and why, is written out at the top of
`waku/gateway/wechat.py`.

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
