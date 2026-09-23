"""`/triage <message>` — run the router on demand, and watch it choose.

The triage graph is normally INVISIBLE: when graph workflows are on it runs
itself on every message, and the whole point is that you never think about it.
That is the right default and it stays the default.

But a router you cannot watch is a router you have to take on faith. This
binder gives it a front door, so you can send one message through the graph
deliberately — with the flag off, without changing any setting, and see which
branch it picks and why.

    /triage thanks!                     -> quick door, small model, no gate
    /triage what's on my calendar?      -> full door, gate fires, tools run

That side-by-side is the clearest demonstration of a router there is: same
harness, same message box, two paths. The automatic door is unaffected — this
forces the graph for ONE message and changes nothing else.
"""

from __future__ import annotations

from waku.ops import browser_agent
from waku.runtime.host import shared_host

USAGE = (
    "`/triage` needs a message to route.\n\n"
    "Try `/triage thanks!` (takes the quick door) and then "
    "`/triage what's on my calendar today?` (takes the full door), and compare."
)


def run_triage(observer=None, message: str = "") -> dict:
    """Run ONE message through the triage graph, whatever the flag says.

    Returns a state dict shaped like the other workflows so the chat renders it
    the same way: `digest` is the reply, plus the route and the classifier's
    reason, which are the parts worth seeing.
    """
    text = (message or "").strip()
    if not text:
        return {"digest": USAGE}

    seen: dict = {}

    def notify(kind: str, ev: dict) -> None:
        if kind == "route":
            seen["route"] = ev.get("target")
        if kind == "triage":
            seen["reason"] = ev.get("reason")
        if observer:
            observer(kind, ev)

    # 仍然走串行边界，但这不是一个普通回合，所以用 host.run 而不是 host.ask。
    # session 一并带上 —— /triage 是 dashboard 的动作，不该跑到别的 gateway 的
    # 会话上去。
    #
    # _respond_via_graph is the same method the automatic door uses, so what you
    # watch here IS what runs on every message when the flag is on — not a demo
    # path that could drift from it.
    result = shared_host().run(
        lambda agent: agent._respond_via_graph(text, notify, stream=False),
        session_id=browser_agent.current_session(),
    )

    if result is None:
        return {"digest": ("The graph produced no answer, so a real turn would have "
                           "fallen open to the plain loop — that is the fail-open rule "
                           "working."), **seen}
    door = "quick" if seen.get("route") == "quick_reply" else "full"
    header = (f"**{door} door** — {seen.get('reason', '')}\n\n"
              if seen.get("route") else "")
    return {"digest": header + result.reply,
            "route": seen.get("route"), "reason": seen.get("reason")}
