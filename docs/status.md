# Status

**What is true right now.** Rewritten whole when it changes, never appended to
— a status file that grows is a changelog, and git already is one.

Read this before opening a PR or filing an issue: most of what is already
known-broken is below, and half of it already has a fix in flight.

**Last updated:** 2026-09-23

---

## What works

The four pillars run: the loop, memory (semantic + episodic + procedural with
a retrieval gate), tools, and both eval tiers. `waku`, `waku serve`,
`waku dashboard`, `waku voice`, `waku brief`, `waku wechat` and
`waku connect google` all start.

**`waku serve` is the resident process.** One Waku, held by
`waku/runtime/host.py`, with the dashboard as its first gateway and — when
`WAKU_WECHAT=1` — the WeChat gateway as its second. Every request names its own
`source` and `session_id`, the host binds the session inside a serial boundary,
and turns run one at a time in arrival order. `waku dashboard` still works and
runs the same process. Settings changes rebuild the one instance between turns;
a failed rebuild keeps the working instance. Shutdown refuses new requests,
answers the ones already queued, and closes the MCP bridge, the SQLite
connection and the listening socket.

**The WeChat gateway is off by default** and needs `waku wechat login` (a QR
scan) before it does anything. Enabled-but-not-logged-in is a normal state. A
WeChat problem — no network, an expired session, a failed send — is reported and
retried on the WeChat side only; the browser keeps working. It is text-only,
one bound account, and it cannot message you first. See
[commands.md](commands.md#wechat-optional-off-by-default).

**The WeChat side has not been through a real device since it moved out of the
lab.** The protocol, the login and the round trip were verified live on
2026-09-23 in `lab/wechat-ilink/`, and every path in `waku/gateway/wechat.py` is
covered offline by `evals/deterministic/test_wechat_gateway.py`, but nobody has
scanned a QR with the gateway itself running. Treat it as working-but-unproven.

**753 deterministic evals pass offline**, with no API key; 38 more skip without
one. On Windows 3 of them fail (temp-file and process assumptions, not this
checkout) and 2 more need `python -X utf8` to read files with the locale codec
— see Known broken. CI runs the offline tier on every PR along with
ruff, the skills validator, and a check that `.env.example` still matches the
integrations registry.

**0.1.8 is on PyPI and on GitHub Releases.** Pushing a `v*` tag publishes to
both, so the repo's "Latest" release always matches `pip install waku-agent`.

**The dashboard uses the Waku Memory design system**, and
`test_design_system.py` keeps it from drifting. See
[context/design-system.md](context/design-system.md).

## Known broken

Nothing here is a surprise. If you hit one of these, the issue exists.

| What | Where | Fix in flight |
|---|---|---|
| The model picker offers OpenAI models that 404 on use | #137 | #178 |
| GPT-5.6 tool calls fail on Chat Completions | — | #146 |
| OpenCode Zen fails with a rate-limit error | #112 | #113 |
| Google Calendar sign-in has no bundled OAuth client, so `waku connect google` needs your own `.waku/credentials.json` | — | — |
| On Windows `pytest evals/deterministic` needs `-X utf8`: `test_static_assets.py` and `test_version.py` read files with the locale (GBK) codec. `test_coding_eval.py` (2) and `test_provider_disabled.py` (1) fail on temp-file and subprocess assumptions | `evals/deterministic/` | — |

**Providers are the recurring theme.** Three of the items above are one
provider or another, and there is no single place that says which providers
are known-good today. Until there is, treat the model picker as a list of
things that *might* work.

## What is deliberately not built

Not a framework, not multi-agent, not production — see
[architecture.md](architecture.md).

Additionally, and worth stating because people ask:

- **No Windows CI.** The Windows bugs so far (#140, #141, both fixed) were
  found by contributors, not by us. Every Windows claim in this repo is
  untested.
- **The judge evals are not in CI.** `make gate` runs deterministic evals at
  100% plus a judge threshold, and CI runs only the first half. The judge tier
  needs an API key, which CI does not have.
- **No provider smoke check.** Nothing verifies that a model in the picker
  resolves, which is why #137 reached a user.

## Open questions

1. **Where the memory pillar ends and Waku Memory begins.** This repo's memory
   is local, single-machine, and yours. Waku Memory is the same memory across
   several agents, and it is a paid hosted service. Both are true and the
   README does not yet say either plainly, so a reader has to work out the
   difference alone.

## Not in the repo

Deliberately absent, so nobody goes looking:

- Filming and demo notes — production material, not product documentation
- Session handoffs — this file replaces them
- Plans and specs — they belong with the work, not in `docs/`
