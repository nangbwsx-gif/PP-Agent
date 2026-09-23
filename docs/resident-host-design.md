# Resident host — one process, one Waku, many gateways

Status: proposal. This is a **Proposal** tier change under
[conventions §2](context/conventions.md#2-how-much-process-a-change-needs) — it
decides who owns the Waku instance and changes the gateway-facing contract — so
it needs a maintainer's yes before any code. [agent-graphs-design.md](agent-graphs-design.md)
is the precedent for the bar.

Scope: let one long-lived process serve the dashboard and several gateways at
once, without giving any of them their own Waku.

## 1. What is wrong today

Every gateway builds its own Waku. `waku/gateway/cli.py` does `Waku()`,
`waku/gateway/voice.py` does `Waku()`, and the dashboard goes through
`waku/ops/browser_agent.py`, which holds a lazily built singleton behind
`agent_lock`. Three processes, three `Waku` objects, three SQLite connections to
the same `state.db`.

The dashboard's singleton already solved most of the hard parts for itself:
one instance, one turn at a time, a rebuild that survives a settings change. The
problem is that those solutions live behind `waku/ops/browser_agent.py` and only
the dashboard can use them. A second gateway has nowhere to plug in.

Adding WeChat makes that concrete. Its credentials live in the Connections page,
which already classifies them as `ReloadMode.AGENT` — a change that is supposed
to rebuild the agent. Today that path calls `browser_agent.rebuild()`, which
reaches one in-process singleton. A WeChat gateway running anywhere else would
never notice.

## 2. Decision: the host owns the one Waku, and nothing else does

A new module, `waku/runtime/host.py`, owns:

- **the Waku instance** — created once, rebuilt in place, closed once
- **the turn queue** — one worker, FIFO, across every gateway
- **the sessions** — one `Session` binding per source, so they cannot cross
- **the gateway registry** — start, stop, and what each gateway is allowed to do

A gateway holds a reference to the host. It never holds the Waku, and it never
imports `waku.app`, `waku.memory`, `waku.tools` or `waku.loop`. That is the
whole of the gateway interface: text in, text out, and its own protocol on its
own side of the line.

```python
class Gateway(Protocol):
    name: str                      # "wechat"; also the session-id prefix
    def start(self, host: "Host") -> None: ...
    def stop(self) -> None: ...


class Host:
    def ask(self, text: str, *, source: str, observer=None, stream=False) -> LoopResult: ...
    def current(self) -> Waku | None: ...      # read-only peek for status pages
    def rebuild(self) -> str | None: ...       # rebuild for every gateway at once
    def start(self) -> None: ...
    def stop(self) -> None: ...
```

`waku dashboards`'s `main()` becomes: build the host, register the dashboard and
the configured gateways, `host.start()`, serve, and `host.stop()` in a `finally`.

## 3. Who executes when the browser and WeChat fire at once

**One turn at a time, in arrival order.** The host runs a single worker thread
pulling from one bounded `queue.Queue`. A gateway calls `host.ask()` and blocks
on that one turn's result, exactly as the dashboard blocks on `agent_lock`
today.

A bare lock would also give mutual exclusion, and it is less code. The queue is
proposed instead for three things a lock cannot express:

- **Order.** With a lock, "who went first" is thread scheduling, so a burst of
  WeChat messages and a browser turn interleave unpredictably.
- **A bound.** `maxsize` turns unbounded queueing into a clear answer. On a full
  queue `ask()` returns a "busy" result instead of growing without limit, and
  the gateway decides what to tell its user.
- **Depth.** The dashboard can show how many turns are waiting, which matters
  the moment a second gateway exists.

This is the one place the proposal asks for more machinery than the current code
has. If the maintainer prefers the smaller version, a lock is a drop-in: the
queue is internal to the host and no gateway can tell the difference.

## 4. Why sessions cannot cross

This is the failure the design has to prevent, and it is not hypothetical.

`Session` holds `self.history`, an **in-memory list**, and says so in its own
docstring: one session per gateway. `Waku.respond()` builds the prompt from it:

```python
window = self.settings.history_turns * 2
messages = self.session.history[-window:] + [{"role": "user", "content": user_message}]
```

`Waku` holds exactly one `Session`. So two gateways sharing one Waku would share
one working window: a WeChat message would be answered with the browser's last
few turns in the prompt. The database rows would still be tagged correctly,
which makes it worse — the stored transcript would look right while the model
saw someone else's conversation.

The fix is that the host keeps one session binding per source:

```python
def ask(self, text, *, source, observer=None, stream=False):
    with self._turn_lock:
        agent = self._agent or self._build()
        agent.session.switch(self._session_id_for(source))
        return agent.respond(text, observer=observer, source=source, stream=stream)
```

`Session.switch()` already exists and already does the right thing: it sets
`session_id` and reloads the last `history_turns` from the database. So the
binding is one existing call, made inside the lock, and `waku/app.py` does not
change at all.

It also means a rebuild does not have to hand-carry the conversation. Today
`browser_agent.rebuild()` copies `session.session_id` from the old instance to
the new one by hand, because a fresh Waku starts on `default`. With `switch()`
called on every turn, the next `ask()` after a rebuild reloads the window from
`state.db` on its own. The hand-off disappears.

| Source | Session id | Produced by |
|---|---|---|
| dashboard | `dashboard-YYYYmmdd-HHMMSS` | `resume_or_new_session` (unchanged) |
| wechat | `wechat-<bot_id>` | the WeChat gateway, per bound bot |
| cli | `terminal` | unchanged |
| voice | `voice` | unchanged |

`chat_log.source` already records the origin per row, and the dashboard's
"unified inbox" already reads it. Session ids stay readable and keep their
existing prefixes; the source column stays the reliable signal, exactly as
`resume_or_new_session` argues today.

## 5. Settings changes: one rebuild, every gateway

`ReloadMode` already classifies each integration, and `waku/integrations.py`
already acts on it — but only against the dashboard's singleton. The change is
where the rebuild lands, not when it runs.

| Mode | Today | Under the host |
|---|---|---|
| `LIVE` (Tavily) | read from the environment per call | unchanged |
| `AGENT` (providers, memory backends, calendar, OTEL) | `browser_agent.rebuild()` | `host.rebuild()` — every gateway's next turn uses the new instance |
| `GATEWAY` (a gateway's own credentials) | a registered reloader hook | the host stops and starts that one gateway |

**The rebuild is a task in the same queue**, so it runs between turns rather
than through one:

- A turn already running finishes on the old instance and its reply goes out
  normally. The observer belongs to the turn, not to the instance, so nothing is
  lost mid-stream.
- Turns already queued run on the old instance; turns queued after the rebuild
  task run on the new one. The order is the arrival order, so the behaviour is
  predictable.
- **WeChat stays connected throughout.** Its socket, its long-poll cursor and
  its queue are the gateway's, not the instance's. Nothing about a provider
  switch touches them.
- If the rebuild raises, the old instance stays live and the `.env` write is
  rolled back — both already implemented in `integrations.py::apply`. WeChat
  keeps working on the previous provider; the Connections page reports the
  error.
- A gateway whose *own* credentials changed is a `GATEWAY` reload: that gateway
  is stopped and started, and the host is left alone.

## 6. Gateway lifecycle

`host.start()` calls `gateway.start(host)` in registration order.
`host.stop()` calls `gateway.stop()` in reverse. Each gateway owns its own
transport and its own state:

- **The dashboard** binds its port and serves. Its handler calls
  `host.ask(..., source="dashboard")`.
- **A WeChat gateway** does the QR login, long-polls in its own thread, and
  calls `host.ask(..., source="wechat")`, then sends the reply back over the
  channel. The iLink protocol, its credentials and its cursor stay inside that
  gateway file. Nothing about it reaches the loop, the memory, the tools or the
  prompt.

A gateway is responsible for being stop-able: `stop()` must abort whatever
blocking I/O it owns, because the process will not wait forever for it.

## 7. How the process exits

`serve_forever()` returns only on `shutdown()`, and nothing today calls it: the
dashboard has no signal handler, no `atexit`, no `server_close()` and no
`agent.close()`. Ctrl-C leaves the MCP bridge subprocess and the SQLite handle
to the operating system. The host fixes that with one ordered path:

```
SIGINT / SIGTERM (and Ctrl-Break on Windows)
      |
host.stop()
      |  1. set stopping -> ask() refuses new work with a clear error
      |  2. stop each gateway in reverse order (abort its I/O, send what it can)
      |  3. drain the queue: run what is already queued, or fail it fast with
      |     "shutting down" so a waiting gateway can answer its user
      |  4. close the MCP bridge and the SQLite connection  (Waku.close)
      |  5. server.shutdown() then server.server_close()
```

`main()` wraps the serve call in `try/finally` so the same path runs on Ctrl-C
and on a normal return. A step that raises does not skip the ones after it —
shutdown must always reach the connection close.

## 8. What this does to the existing files

**`waku/ops/browser_agent.py` — its job moves out.** `_agent`, `agent_lock`,
`get_agent`, `current` and `rebuild` all become the host's. The module keeps
what is genuinely dashboard policy: `resume_or_new_session`,
`maybe_rotate_session`, and the reasoning behind them, exposed as the dashboard
gateway's session policy. The comment about rebinding a module global from the
module that owns it stays true and moves with the code to `host.py`.

**`waku/ops/dashboard.py` — stops owning the agent and the process.** The
`with agent_lock: agent = get_agent()` block in `chat_stream` becomes one
`host.ask()` call. `collect()` and `tools_info()` read `host.current()` instead
of `browser_agent.current()`. `main()` stops being a three-line bind-and-serve
loop and becomes host construction, gateway registration, signal handlers and a
`finally`.

**`waku/app.py` — no signature change.** `Waku()` still assembles in the same
order, and `respond()` keeps its exact signature: `source` is already a
parameter and already reaches `chat_log.source`. The only change proposed here
is in `close()`, which today closes the MCP bridge but not the connection; under
a host that outlives every request, the connection needs closing too. That is a
one-line fix to shutdown correctness, not a redesign.

**`waku/integrations.py`** — two call sites move from `browser_agent.rebuild()`
to `host.rebuild()`, and `register_gateway_reloader` gets a real implementation
now that there is a host to call it. The rollback and health-recording logic
around them does not change.

## 9. What this deliberately does not do

- **It does not run two Waku instances against one `state.db`.** The opposite:
  the dashboard and every gateway in the process share one instance and one
  connection. Today's dashboard-plus-gateway deployments would go from N
  connections to one.
- **It does not change separate foreground invocations.** `waku` in a terminal
  and `waku voice` are still their own processes with their own Waku and their
  own SQLite connection, as they are today. Making the CLI attach to a running
  host needs IPC and is its own proposal.
- **It does not add a dependency.** `queue`, `threading`, `signal` and
  `http.server` are all stdlib.
- **It does not put any channel protocol in the core.** A gateway's protocol,
  credentials and cursor stay in its own file. The host knows gateways by
  `name`, `start` and `stop`, and nothing else.

## 10. Phasing

1. **Host, dashboard only.** Extract `browser_agent`'s singleton into
   `waku/runtime/host.py`, register the dashboard as the first gateway, add the
   queue, the session binding and the shutdown path. Behaviour must be identical
   to today: same single turn at a time, same session rotation, same rebuild
   semantics. The existing dashboard evals are the guard.
2. **WeChat gateway.** One file, `waku/gateway/wechat.py`, at rung 5 of the
   footprint ladder, wired to the host. The lab experiment in
   `lab/wechat-ilink/` is the protocol reference and stays the on-its-own-terms
   baseline.
3. **Later, if wanted.** CLI and voice attach to a running host over IPC.

## 11. Decisions taken (flag disagreement before phase 1)

1. **The host owns the Waku; no gateway may hold one.** Everything else follows
   from this.
2. **One worker and a bounded queue**, not a bare lock, so a burst has an order
   and a limit.
3. **A rebuild is a queue task**, so it lands between turns and never through
   one.
4. **Sessions are bound per source with the existing `Session.switch()`**, which
   keeps `app.py` untouched and removes the hand-carried session id in the
   rebuild path.
5. **This is Proposal tier.** It changes who owns the instance and the
   gateway-facing contract, so it needs a maintainer's yes before phase 1
   starts.
