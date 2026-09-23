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

from evals.helpers import FakeAgent, ScriptedClient, make_waku, response, text_block
from waku.runtime.host import Host, HostBusy, HostStopped

# ------------------------------------------------------------------ fakes


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


# ------------------------------------------------------------- shutdown bounds


def _wait_for(predicate, seconds=5.0):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert predicate()


def test_stop_reports_clean_and_closes_when_the_turn_finishes_in_time():
    agent = FakeAgent()
    host = Host(build=lambda: agent, stop_timeout=5)
    host.start()
    host.ask("hi", source="s", session_id="1")

    assert host.stop() is True
    assert agent.closed is True


def test_stop_gives_up_rather_than_closing_under_a_running_turn():
    """The turn overruns the deadline, so stop() must NOT close the instance: a
    live thread is still using its connection."""
    release = threading.Event()
    agent = FakeAgent(gate=release)
    host = Host(build=lambda: agent, stop_timeout=0.2)
    host.start()
    threading.Thread(target=lambda: host.ask("running", source="s", session_id="1")).start()
    _wait_for(lambda: bool(agent.responded))

    assert host.stop() is False, "an overrunning turn must not be reported as a clean stop"
    assert agent.closed is False, "stop() closed the instance a live turn was using"

    release.set()


def test_a_second_stop_keeps_the_first_answer():
    """The result is a decision, not a race: asking again cannot turn an unclean
    stop into a clean one."""
    release = threading.Event()
    agent = FakeAgent(gate=release)
    host = Host(build=lambda: agent, stop_timeout=0.2)
    host.start()
    threading.Thread(target=lambda: host.ask("running", source="s", session_id="1")).start()
    _wait_for(lambda: bool(agent.responded))

    assert host.stop() is False
    assert host.stop() is False
    assert agent.closed is False

    release.set()


def test_a_turn_that_overran_can_still_use_its_connection(tmp_path):
    """The acceptance criterion, made literal.

    After a timed-out stop, the turn that was still running must be able to
    finish its database write. If stop() had closed the connection underneath
    it, this raises `Cannot operate on a closed database` — and on a two-INSERT
    turn like log_chat() it would leave a user row with no reply beside it.
    """
    from waku.db import connect

    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    release = threading.Event()
    seen: dict = {}

    class _DbAgent(FakeAgent):
        def __init__(self, conn):
            super().__init__(gate=release)
            self.conn = conn

        def respond(self, text, observer=None, source="cli", stream=False):
            self.responded.append((self.session.current, text, source))
            self.gate.wait(5)          # overruns the stop deadline below
            self.conn.execute(
                "INSERT INTO chat_log (role, content, session_id) VALUES ('user', ?, 's')",
                (text,),
            )
            self.conn.commit()
            seen["rows"] = self.conn.execute("SELECT COUNT(*) FROM chat_log").fetchone()[0]
            return SimpleNamespace(reply="ok", tool_calls=[], iterations=1)

    built: dict = {}

    def build():
        # Built here, on the worker thread — the same way the real builder does.
        agent = _DbAgent(connect(tmp_path / "home", check_same_thread=False))
        built["agent"] = agent
        return agent

    host = Host(build=build, stop_timeout=0.3)
    host.start()
    asker = threading.Thread(target=lambda: host.ask("hello", source="s", session_id="s1"))
    asker.start()
    _wait_for(lambda: "agent" in built and bool(built["agent"].responded))

    assert host.stop() is False
    assert built["agent"].closed is False

    release.set()
    asker.join(5)
    assert not asker.is_alive()
    assert seen.get("rows") == 1, "the overrunning turn could not finish its write"
    # And the connection is still usable afterwards, not a closed handle.
    assert built["agent"].conn.execute("SELECT COUNT(*) FROM chat_log").fetchone()[0] == 1


def test_start_after_stop_does_not_add_a_second_worker():
    """Two workers would run two turns at once, which is the one thing the host
    exists to prevent."""
    host = Host(build=lambda: FakeAgent(), stop_timeout=5)
    host.start()
    host.stop()
    host.start()          # must be a no-op, not a fresh worker

    with pytest.raises(HostStopped):
        host.ask("hi", source="s", session_id="1")
