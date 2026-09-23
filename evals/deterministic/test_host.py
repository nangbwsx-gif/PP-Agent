"""DETERMINISTIC EVAL — the resident host holds one Waku for every gateway.

Design: docs/resident-host-design.md. Two halves of that design are new
behaviour rather than a refactor, and both can look right in review while still
hanging a real user, so they are asserted here directly:

  the queue     one turn at a time, in enqueue order, with a bound
  shutdown      waiting requests get an explicit answer, never silence

The session tests are the other half. `Session.history` is one in-memory list
that `Waku.respond()` builds the prompt from, so two gateways sharing one Waku
would answer a WeChat message with the browser's turns in the prompt — while
the stored rows still looked correct. That is not a hypothetical: it is what
happens the moment a second gateway exists, which is why the host switches
session inside the serial boundary and why these tests read the prompt itself.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from evals.helpers import ScriptedClient, make_waku, response, text_block
from waku.runtime.host import Host, HostBusy, HostStopped

# ------------------------------------------------------------------ fakes


class FakeSession:
    """The one method the host is allowed to call on a session."""

    def __init__(self):
        self.current = None
        self.switched = []

    def switch(self, session_id):
        self.current = session_id
        self.switched.append(session_id)


class FakeAgent:
    """A Waku stand-in: records what it was asked, in order, and when.

    `gate` makes a turn block until the test releases it, which is how the queue
    tests get a deterministic window to enqueue into.
    """

    def __init__(self, name="agent", gate=None):
        self.name = name
        self.session = FakeSession()
        self.responded = []           # (session_id, text, source), in execution order
        self.closed = False
        self.gate = gate
        self.live = 0
        self.max_live = 0
        self._lock = threading.Lock()
        self.settings = SimpleNamespace(model="m", small_model="sm", provider="p")

    def respond(self, text, observer=None, source="cli", stream=False):
        with self._lock:
            self.live += 1
            self.max_live = max(self.max_live, self.live)
        try:
            self.responded.append((self.session.current, text, source))
            if self.gate is not None:
                self.gate.wait(5)
            else:
                # A window wide enough that an unserialised pair WOULD overlap,
                # so `max_live == 1` is a real assertion and not an accident.
                time.sleep(0.02)
            return SimpleNamespace(reply=f"{self.name}:{text}", tool_calls=[], iterations=1)
        finally:
            with self._lock:
                self.live -= 1

    def close(self):
        self.closed = True


def _gate_skip():
    return response([text_block('{"retrieve": false, "query": "", "reason": "t"}')])


def _loop_calls(sent, texts):
    """The calls that ran a LOOP turn: their last message is the user's text.

    The retrieval gate also calls the model, with the message embedded in a
    larger template, so identity only holds for the loop calls."""
    return [m for m in sent if m and isinstance(m[-1], dict) and m[-1].get("content") in texts]


# ------------------------------------------------------------- the contract


def test_ask_requires_an_explicit_source_and_session():
    """The host does not derive a session from its source. A source can own
    several threads — the dashboard already does — so guessing would put thread
    policy inside the host."""
    with Host(build=lambda: FakeAgent()) as host:
        for kwargs in ({"source": "", "session_id": "s"}, {"source": "dashboard", "session_id": ""}):
            with pytest.raises(ValueError):
                host.ask("hi", **kwargs)


# ----------------------------------------------------------- session isolation


def test_each_session_sees_only_its_own_history(tmp_path, monkeypatch):
    """Two sessions alternating: the model sees each one's own past, and not a
    word of the other's. This is the assertion the whole design exists for."""
    monkeypatch.setenv("WAKU_HISTORY_TURNS", "3")
    sent: list = []

    class Recorder(ScriptedClient):
        def _create(self, **kwargs):
            sent.append(list(kwargs.get("messages", [])))
            return self._script.pop(0)

    script = []
    for _ in range(4):
        script += [_gate_skip(), response([text_block("ok")])]

    # Build INSIDE the builder, so the Waku is constructed on the host's worker
    # thread — which is what the real builder does, and what a default
    # `check_same_thread=True` connection requires.
    built: list = []

    def build():
        agent = make_waku(tmp_path / "home", client=Recorder(script))
        built.append(agent)
        return agent

    with Host(build=build) as host:
        host.ask("alpha one", source="dashboard", session_id="sess-a")
        host.ask("bravo one", source="wechat", session_id="sess-b")
        host.ask("alpha two", source="dashboard", session_id="sess-a")
        host.ask("bravo two", source="wechat", session_id="sess-b")

    assert built

    mine = {"alpha one", "alpha two", "bravo one", "bravo two"}
    calls = _loop_calls(sent, mine)
    assert len(calls) == 4, [m[-1] for m in calls]

    blob = lambda ms: " ".join(str(m.get("content", "")) for m in ms)
    by_last = {m[-1]["content"]: blob(m) for m in calls}

    # The second turn of each session must contain its own first turn...
    assert "alpha one" in by_last["alpha two"]
    assert "bravo one" in by_last["bravo two"]
    # ...and none of the other session's.
    assert "bravo one" not in by_last["alpha two"]
    assert "alpha one" not in by_last["bravo two"]


def test_a_rebuild_does_not_carry_the_old_window_over(tmp_path, monkeypatch):
    """After a rebuild the next turn reloads its window from state.db, so the
    conversation survives the swap without anyone hand-carrying a session id."""
    monkeypatch.setenv("WAKU_HISTORY_TURNS", "3")
    sent: list = []

    class Recorder(ScriptedClient):
        def _create(self, **kwargs):
            sent.append(list(kwargs.get("messages", [])))
            return self._script.pop(0)

    script = []
    for _ in range(3):
        script += [_gate_skip(), response([text_block("ok")])]
    built: list = []

    def build():
        agent = make_waku(tmp_path / "home", client=Recorder(script))
        built.append(agent)
        return agent

    with Host(build=build) as host:
        host.ask("before rebuild", source="dashboard", session_id="sess-a")
        assert host.rebuild() is None
        host.ask("after rebuild", source="dashboard", session_id="sess-a")

    assert len(built) == 2, "the rebuild did not build a second instance"

    last = _loop_calls(sent, {"after rebuild"})[-1]
    assert "before rebuild" in " ".join(str(m.get("content", "")) for m in last)


# ------------------------------------------------------------------- ordering


def test_turns_run_one_at_a_time_in_enqueue_order():
    """While a turn is running, later requests queue; they then run in the order
    they arrived. Deterministic because the worker is held at the front and the
    rest are enqueued one at a time, each after the previous one is queued."""
    release = threading.Event()
    agent = FakeAgent(gate=release)

    def wait_for(predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            time.sleep(0.005)
        assert predicate()

    with Host(build=lambda: agent) as host:
        threads = [threading.Thread(
            target=lambda t=text: host.ask(t, source="dashboard", session_id="s")
        ) for text in ("first", "second", "third")]

        threads[0].start()
        wait_for(lambda: bool(agent.responded))   # the worker is inside turn one

        # Enqueue the rest one at a time, so arrival order is not a race.
        for index, thread in enumerate(threads[1:], start=1):
            thread.start()
            wait_for(lambda target=index: host.pending() == target)

        release.set()
        for thread in threads:
            thread.join(5)

    assert [text for _sid, text, _src in agent.responded] == ["first", "second", "third"]
    assert agent.max_live == 1


def test_simultaneous_submissions_never_overlap_and_never_cross():
    """Two gateways firing at once: each caller gets its OWN reply, and the two
    turns never run at the same time."""
    agent = FakeAgent()
    replies: dict = {}
    barrier = threading.Barrier(2)

    with Host(build=lambda: agent) as host:
        def submit(text, source):
            barrier.wait(5)
            replies[text] = host.ask(text, source=source, session_id=f"s-{source}").reply

        threads = [
            threading.Thread(target=submit, args=("from-dashboard", "dashboard")),
            threading.Thread(target=submit, args=("from-wechat", "wechat")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)

    assert replies == {"from-dashboard": "agent:from-dashboard",
                       "from-wechat": "agent:from-wechat"}
    assert agent.max_live == 1, "two turns ran at once"


# ----------------------------------------------------------------- queue full


def test_a_full_queue_is_refused_not_awaited():
    """A bounded queue turns unbounded waiting into a clear answer."""
    release = threading.Event()
    agent = FakeAgent(gate=release)

    with Host(build=lambda: agent, queue_size=1) as host:
        running = threading.Thread(
            target=lambda: host.ask("running", source="s", session_id="1"))
        running.start()
        deadline = time.monotonic() + 5
        while not agent.responded and time.monotonic() < deadline:
            time.sleep(0.005)

        queued = threading.Thread(
            target=lambda: host.ask("queued", source="s", session_id="1"))
        queued.start()
        deadline = time.monotonic() + 5
        while host.pending() < 1 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert host.pending() == 1

        # One running + one queued fills a queue of size 1.
        with pytest.raises(HostBusy):
            host.ask("overflow", source="s", session_id="1")

        # Let both accepted turns finish BEFORE leaving the `with`: stop() would
        # otherwise reject the queued one, which is a different test.
        release.set()
        running.join(5)
        queued.join(5)

    assert [text for _sid, text, _src in agent.responded] == ["running", "queued"]


# ------------------------------------------------------------------- rebuild


def test_rebuild_swaps_the_instance_and_closes_the_old_one():
    old, new = FakeAgent("old"), FakeAgent("new")
    builds = [old, new]

    with Host(build=lambda: builds.pop(0)) as host:
        assert host.ask("first", source="s", session_id="1").reply == "old:first"
        assert host.rebuild() is None
        assert host.ask("second", source="s", session_id="1").reply == "new:second"

    assert old.closed, "the replaced instance was not closed"


def test_a_failed_rebuild_keeps_the_working_instance():
    """A broken key must not also take away the agent you already had."""
    good = FakeAgent("good")
    calls = {"n": 0}

    def build():
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("bad key")
        return good

    with Host(build=build) as host:
        assert host.ask("before", source="s", session_id="1").reply == "good:before"
        error = host.rebuild()
        assert error and "bad key" in error
        assert host.current() is good, "the working instance was replaced anyway"
        assert host.ask("after", source="s", session_id="1").reply == "good:after"


def test_a_rebuild_lands_between_turns_not_through_one():
    """Queued behind a running turn, the rebuild happens after it — so the turn
    in flight finishes on the instance it started on."""
    release = threading.Event()
    old, new = FakeAgent("old", gate=release), FakeAgent("new")
    builds = [old, new]
    outcome: dict = {}

    with Host(build=lambda: builds.pop(0)) as host:
        threading.Thread(target=lambda: host.ask("running", source="s", session_id="1")).start()
        deadline = time.monotonic() + 5
        while not old.responded and time.monotonic() < deadline:
            time.sleep(0.005)

        rebuild_thread = threading.Thread(target=lambda: outcome.update(error=host.rebuild()))
        rebuild_thread.start()
        time.sleep(0.05)          # let the rebuild reach the queue
        assert outcome == {}, "the rebuild ran while a turn was still in flight"

        release.set()
        rebuild_thread.join(5)
        assert outcome["error"] is None
        assert host.ask("after", source="s", session_id="1").reply == "new:after"

    assert old.closed


# ------------------------------------------------------------------ shutdown


def test_stop_refuses_new_requests_and_answers_waiting_ones():
    """Nothing waits forever when the process goes down. The queued request gets
    an explicit answer instead of silence."""
    release = threading.Event()
    agent = FakeAgent(gate=release)
    outcome: dict = {}

    host = Host(build=lambda: agent)
    host.start()
    threading.Thread(target=lambda: host.ask("running", source="s", session_id="1")).start()
    deadline = time.monotonic() + 5
    while not agent.responded and time.monotonic() < deadline:
        time.sleep(0.005)

    def queued():
        try:
            host.ask("waiting", source="s", session_id="1")
            outcome["result"] = "ran"
        except HostStopped as exc:
            outcome["result"] = str(exc)

    waiting = threading.Thread(target=queued)
    waiting.start()
    deadline = time.monotonic() + 5
    while host.pending() < 1 and time.monotonic() < deadline:
        time.sleep(0.005)

    stopper = threading.Thread(target=host.stop)
    stopper.start()
    waiting.join(5)
    assert not waiting.is_alive(), "a waiting request hung through shutdown"
    assert outcome["result"] == "the host is shutting down; this request was not run"

    with pytest.raises(HostStopped):
        host.ask("too late", source="s", session_id="1")

    release.set()
    stopper.join(10)
    assert not stopper.is_alive()


def test_stop_closes_the_agent_and_its_resources():
    agent = FakeAgent()

    host = Host(build=lambda: agent)
    host.start()
    host.ask("hello", source="s", session_id="1")
    host.stop()

    assert agent.closed, "stop() left the instance open"
    assert host.current() is None


def test_stop_is_idempotent():
    agent = FakeAgent()
    host = Host(build=lambda: agent)
    host.start()
    host.stop()
    host.stop()          # a second Ctrl-C must not raise


def test_a_closed_host_does_not_build_an_agent():
    """stop() then ask() must not quietly construct a fresh Waku."""
    built = []

    host = Host(build=lambda: built.append(1) or FakeAgent())
    host.start()
    host.stop()
    with pytest.raises(HostStopped):
        host.ask("hi", source="s", session_id="1")
    assert not built
