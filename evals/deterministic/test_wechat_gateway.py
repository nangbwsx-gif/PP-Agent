"""DETERMINISTIC EVAL — the WeChat gateway, driven by fake iLink responses.

No network, no account, no credentials: `FakeILink` stands in for the four calls
the gateway makes, so every path below — login state, retry, dedup, session id,
final-reply-only, send failure, restart recovery, a gateway that fails to start —
runs offline and deterministically.

The two that matter most are the ordering ones, because they are the difference
between "messages go missing" and "tools run twice":

  * the long-poll cursor is saved only AFTER the whole batch is handled
  * a message is CLAIMED before its turn runs

Both are asserted directly on what the state directory and the fake saw, not on
the shape of the code.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from evals.helpers import FakeAgent
from waku.config import Settings
from waku.gateway import wechat
from waku.runtime import conversation
from waku.runtime.host import Host

USER = "o9cq800kum_4g8Py8Qw5G0a@im.wechat"
CONTEXT = "ctx-token-1"


def _wait_for(predicate, seconds=5.0):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert predicate(), "timed out waiting for the gateway"


def user_text(message_id="m1", text="hello", user_id=USER, context=CONTEXT):
    return {
        "message_id": message_id,
        "from_user_id": user_id,
        "to_user_id": "bot@im.bot",
        "message_type": 1,
        "context_token": context,
        "item_list": [{"type": 1, "text_item": {"text": text}}],
    }


def user_image(message_id="img1"):
    return {
        "message_id": message_id,
        "from_user_id": USER,
        "message_type": 1,
        "context_token": CONTEXT,
        "item_list": [{"type": 2, "image_item": {"url": "http://example/x.png"}}],
    }


class FakeILink:
    """Scripted iLink responses.

    `batches` is popped one per `fetch_updates`. An entry that is an exception is
    raised instead of returned, which is how the retry and expiry tests drive it.
    When the script runs out the poll thread parks on an event rather than
    spinning, so a test never sees a busy loop.
    """

    def __init__(self, batches=None, *, send_failures=0, qr_statuses=None):
        self.batches = list(batches or [])
        self.send_failures = send_failures
        self.qr_statuses = list(qr_statuses or [])
        self.sent = []                 # every post_message payload that SUCCEEDED
        self.posts = []                # every attempt, failures included
        self.cursors_seen = []         # every cursor passed to fetch_updates
        self.exhausted = threading.Event()

    # -- the four calls
    def fetch_qr_code(self, base_url=wechat.BASE_URL):
        return {"qrcode": "qr-token", "qrcode_img_content": "https://example/qr", "ret": 0}

    def fetch_qr_status(self, qrcode, base_url=wechat.BASE_URL):
        return self.qr_statuses.pop(0) if self.qr_statuses else {"status": "wait"}

    def fetch_updates(self, base_url, token, cursor):
        self.cursors_seen.append(cursor)
        while self.batches:
            item = self.batches.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        # 脚本用完了：像一次“没有消息的长轮询”那样返回空批次，**不是报错**。
        # 报错会在“已恢复”之后立刻又报一次失败，把日志和评测都弄成随机的。
        # 等待要短：gateway 的 stop() 无法中断一次在飞的调用（真实长轮询也一样），
        # 所以这里停太久会让每个评测都付掉 join 的超时。
        self.exhausted.wait(0.05)
        return {"msgs": [], "get_updates_buf": cursor, "ret": 0}

    def post_message(self, base_url, token, message):
        self.posts.append(message)     # record failures too: retries are the point
        if self.send_failures > 0:
            self.send_failures -= 1
            raise wechat.ApiError("sendmessage failed", status=500, code=500)
        self.sent.append(message)
        return {"ret": 0}

    # -- helpers for assertions
    def sent_texts(self):
        return [item["item_list"][0]["text_item"]["text"] for item in self.sent]



@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Point WAKU_HOME somewhere disposable, for every test in this file.

    The shared thread id comes from `load_settings().home`, so without this the
    gateway resolves it against the developer's REAL conversation — which is
    exactly what happened the first time this ran: the assertion compared against
    a live `dashboard-*` thread from someone's actual chat log. A test must never
    read, and never depend on, the real runtime directory.
    """
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WAKU_HOME", str(home))
    monkeypatch.setattr(conversation, "_thread_id", None)


def make_gateway(tmp_path, api, host=None):
    state = wechat.GatewayState(tmp_path / "wechat")
    state.save_credentials({"token": "bot-token", "baseUrl": wechat.BASE_URL,
                            "accountId": "bot@im.bot", "userId": USER})
    return wechat.WeChatGateway(state, api, allowed=[USER], poll_interval=0.01,
                                 send_retry_seconds=0.0)


def batch(*messages, cursor="cursor-1"):
    return {"msgs": list(messages), "get_updates_buf": cursor, "ret": 0}


# --------------------------------------------------------------- login state


def test_without_credentials_it_does_not_poll_and_does_not_error(tmp_path):
    """Not being logged in is a normal state, not a failure: no network call, and
    nothing for the host to report (which would otherwise look like a broken
    dashboard)."""
    api = FakeILink([batch(user_text())])
    gateway = wechat.WeChatGateway(wechat.GatewayState(tmp_path / "wechat"), api, allowed=[USER])

    with Host(build=lambda: FakeAgent(), gateways=[gateway]) as host:
        assert not host.gateway_error("wechat")
        assert gateway.status()["phase"] == "not-logged-in"
        assert not gateway.running
    assert api.cursors_seen == [], "it polled without credentials"


def test_with_credentials_it_polls(tmp_path):
    api = FakeILink([batch(user_text())])
    gateway = make_gateway(tmp_path, api)

    with Host(build=lambda: FakeAgent()) as host:
        gateway.start(host)
        _wait_for(lambda: len(api.sent) == 1)
        gateway.stop()
        assert gateway.status()["phase"] == "stopped"


def test_a_session_expired_token_stops_polling_and_says_so(tmp_path):
    """errcode -14 means the token is dead. Backing off and retrying would hammer
    a session that cannot recover without a new scan."""
    api = FakeILink([wechat.ApiError("session timeout", code=wechat.SESSION_EXPIRED_CODE)])
    gateway = make_gateway(tmp_path, api)

    with Host(build=lambda: FakeAgent()) as host:
        gateway.start(host)
        _wait_for(lambda: gateway.status()["phase"] == "expired")
        assert not gateway.running
        assert "waku wechat login" in gateway.status()["last_error"]
    assert len(api.cursors_seen) == 1, "it kept polling an expired session"


# ------------------------------------------------------------------- retries


def test_a_failed_long_poll_is_retried_and_the_message_still_lands(tmp_path):
    api = FakeILink([wechat.ApiError("network error: timed out"), batch(user_text())])
    gateway = make_gateway(tmp_path, api)

    with Host(build=lambda: FakeAgent()) as host:
        gateway.start(host)
        _wait_for(lambda: len(api.sent) == 1)
        gateway.stop()

    assert len(api.cursors_seen) >= 2, "the poll was not retried"
    assert api.sent_texts() == ["agent:hello"]


# ------------------------------------------------------------------ dedup


def test_a_redelivered_message_does_not_run_a_second_turn(tmp_path):
    """The cursor is saved last, so a crash re-delivers the batch. The claim is
    what stops that from running side-effecting tools twice."""
    same = user_text("m1", "make me a meeting")
    api = FakeILink([batch(same, cursor="c1"), batch(same, cursor="c2")])
    agent = FakeAgent()
    gateway = make_gateway(tmp_path, api)

    with Host(build=lambda: agent) as host:
        gateway.start(host)
        _wait_for(lambda: len(api.cursors_seen) >= 2)
        _wait_for(lambda: len(api.sent) >= 1)
        gateway.stop()

    assert [text for _sid, text, _src in agent.responded] == ["make me a meeting"], (
        "the re-delivered message ran the turn again"
    )
    assert api.sent_texts() == ["agent:make me a meeting"], "the reply was sent twice"


# ------------------------------------------------------------------ session


def test_it_asks_on_the_shared_thread_with_the_wechat_source(tmp_path):
    """Both gateways ask on the SAME thread, and only `source` tells them apart.

    That is the whole point of sharing one line: a conversation held over WeChat
    is the conversation the browser resumes, so "what did I say on WeChat" has an
    answer. The channel is still recorded per row, which is what lets History
    show where each message came in.
    """
    api = FakeILink([batch(user_text("m1", "first", user_id=USER),
                           user_text("m2", "second", user_id=USER))])
    agent = FakeAgent()
    gateway = make_gateway(tmp_path, api)

    with Host(build=lambda: agent) as host:
        gateway.start(host)
        _wait_for(lambda: len(api.sent) == 2)
        gateway.stop()

    threads = [sid for sid, _t, _s in agent.responded]
    assert len(set(threads)) == 1, f"two channels on two threads: {threads}"
    assert threads[0].startswith("chat-"), threads[0]
    assert [src for _sid, _t, src in agent.responded] == ["wechat", "wechat"]
    # And the thread the host bound is exactly the one the gateway asked for.
    assert agent.session.switched == threads


# ------------------------------------------------------- final reply only


def test_only_the_finished_turns_reply_is_sent(tmp_path):
    """One text in, exactly one text out, and it is the agent's final reply —
    never a partial or streamed fragment, never the echoed question."""
    api = FakeILink([batch(user_text("m1", "what time is it"))])
    gateway = make_gateway(tmp_path, api)

    with Host(build=lambda: FakeAgent()) as host:
        gateway.start(host)
        _wait_for(lambda: len(api.sent) == 1)
        gateway.stop()

    assert api.sent_texts() == ["agent:what time is it"]
    assert len(api.sent) == 1
    assert api.sent[0]["message_type"] == 2        # BOT
    assert api.sent[0]["message_state"] == 2       # FINISH
    assert api.sent[0]["context_token"] == CONTEXT, "the reply must carry the inbound token"
    assert api.sent[0]["to_user_id"] == USER


def test_a_long_reply_is_chunked_and_each_chunk_carries_the_context(tmp_path):
    class LongAgent(FakeAgent):
        def respond(self, text, observer=None, source="cli", stream=False):
            return type("R", (), {"reply": "x" * (wechat.MAX_TEXT_CHUNK + 50),
                                  "tool_calls": [], "iterations": 1})()

    api = FakeILink([batch(user_text("m1", "tell me everything"))])
    gateway = make_gateway(tmp_path, api)

    with Host(build=lambda: LongAgent()) as host:
        gateway.start(host)
        _wait_for(lambda: len(api.sent) >= 2)
        gateway.stop()

    assert len(api.sent) == 2
    assert all(item["context_token"] == CONTEXT for item in api.sent)
    assert "".join(api.sent_texts()) == "x" * (wechat.MAX_TEXT_CHUNK + 50)


# ------------------------------------------------------------- send failure


def test_a_transient_send_failure_is_retried_without_rerunning_the_turn(tmp_path):
    """Re-sending is safe (worst case a duplicate reply). Re-running the turn is
    not, because tools have side effects. So the send retries, the turn does not,
    and a later flush delivers what the first attempt could not."""
    api = FakeILink([batch(user_text("m1", "book it"))], send_failures=wechat.SEND_ATTEMPTS)
    agent = FakeAgent()
    gateway = make_gateway(tmp_path, api)

    with Host(build=lambda: agent) as host:
        gateway.start(host)
        _wait_for(lambda: len(api.sent) == 1)      # a later flush got it out
        gateway.stop()

    assert [text for _sid, text, _src in agent.responded] == ["book it"], "the turn ran twice"
    assert gateway.status()["pending"] == [], "a delivered reply was left in the outbox"


def test_an_undeliverable_reply_is_persisted_and_never_re_runs_the_turn(tmp_path):
    """The acceptance point: a send failure is reported as a failure, survives a
    restart, and does not cost a second turn."""
    api = FakeILink([batch(user_text("m1", "book it"))], send_failures=999)
    agent = FakeAgent()
    gateway = make_gateway(tmp_path, api)

    with Host(build=lambda: agent) as host:
        gateway.start(host)
        _wait_for(lambda: gateway.status()["pending"])
        gateway.stop()

    assert api.sent == [], "a failed send was reported as delivered"
    assert [t for _s, t, _src in agent.responded] == ["book it"], "the turn ran twice"
    pending = gateway.status()["pending"]
    assert len(pending) == 1
    assert pending[0]["messageId"] == "m1"
    assert pending[0]["sentChunks"] == 0
    assert pending[0]["attempts"] >= 1
    # And it is on disk, which is what makes the next start able to finish it.
    on_disk = json.loads((tmp_path / "wechat" / "outbox.json").read_text())
    assert on_disk["entries"][0]["messageId"] == "m1"


def test_a_later_run_delivers_the_backlog_without_running_any_turn(tmp_path):
    """Restart recovery for delivery: the reply a previous run could not send goes
    out, and the agent is not asked to produce it again."""
    api_one = FakeILink([batch(user_text("m1", "book it"))], send_failures=999)
    agent_one = FakeAgent()
    gateway_one = make_gateway(tmp_path, api_one)
    with Host(build=lambda: agent_one) as host:
        gateway_one.start(host)
        _wait_for(lambda: gateway_one.status()["pending"])
        gateway_one.stop()
    assert api_one.sent == []

    # A fresh process over the same state directory, this time with a working send.
    api_two = FakeILink([])
    agent_two = FakeAgent()
    gateway_two = make_gateway(tmp_path, api_two)
    with Host(build=lambda: agent_two) as host:
        gateway_two.start(host)
        _wait_for(lambda: len(api_two.sent) == 1)
        gateway_two.stop()

    assert [t for _s, t, _src in agent_two.responded] == [], (
        "the backlog was delivered by re-running the turn"
    )
    assert api_two.sent_texts() == ["agent:book it"]
    assert gateway_two.status()["pending"] == []


# ----------------------------------------------------------- restart recovery


def test_a_restart_resumes_the_cursor_and_does_not_replay(tmp_path):
    """Restart recovery, the two halves: the cursor comes back from disk, and a
    message claimed before the crash is not handled again."""
    first = user_text("m1", "remember this")
    api_one = FakeILink([batch(first, cursor="cursor-after-1")])

    agent_one = FakeAgent()
    gateway_one = make_gateway(tmp_path, api_one)
    with Host(build=lambda: agent_one) as host:
        gateway_one.start(host)
        _wait_for(lambda: len(api_one.sent) == 1)
        gateway_one.stop()

    # A fresh gateway over the same state directory = the process restarted.
    api_two = FakeILink([batch(first, cursor="cursor-after-1"),
                         batch(user_text("m2", "and this"), cursor="cursor-after-2")])
    agent_two = FakeAgent()
    gateway_two = make_gateway(tmp_path, api_two)
    with Host(build=lambda: agent_two) as host:
        gateway_two.start(host)
        _wait_for(lambda: len(api_two.sent) == 1)
        gateway_two.stop()

    assert api_two.cursors_seen[0] == "cursor-after-1", "the cursor was not restored"
    assert [text for _sid, text, _src in agent_two.responded] == ["and this"], (
        "the already-handled message was handled again after the restart"
    )


def test_a_turn_interrupted_by_a_crash_is_reported_not_retried(tmp_path):
    """A claim with no completion is the one case where a message is genuinely
    left unanswered. It must be visible, not silent — and not retried, because
    the tools may already have run."""
    state = wechat.GatewayState(tmp_path / "wechat")
    state.save_credentials({"token": "t", "baseUrl": wechat.BASE_URL})
    state.seen.claim("half-done")

    api = FakeILink([batch(user_text("m2", "next"))])
    gateway = wechat.WeChatGateway(state, api, allowed=[USER], poll_interval=0.01,
                                 send_retry_seconds=0.0)

    with Host(build=lambda: FakeAgent()) as host:
        gateway.start(host)
        _wait_for(lambda: len(api.sent) == 1)
        gateway.stop()

    # Reported during this run, and cleared from disk so it is not reported again.
    assert gateway.status()["unfinished"] == ["half-done"]
    on_disk = json.loads((tmp_path / "wechat" / "seen.json").read_text())
    assert on_disk["claimed"] == [], "the interrupted claim was left in flight"
    assert "half-done" in on_disk["done"]
    assert "m2" in on_disk["done"], "the message after it was not handled"


# --------------------------------------------------------- non-text and empty


def test_a_non_text_message_is_answered_without_a_turn(tmp_path):
    api = FakeILink([batch(user_image("img1"))])
    agent = FakeAgent()
    gateway = make_gateway(tmp_path, api)

    with Host(build=lambda: agent) as host:
        gateway.start(host)
        _wait_for(lambda: len(api.sent) == 1)
        gateway.stop()

    assert agent.responded == [], "an image message ran an agent turn"
    assert "只支持文本" in api.sent_texts()[0]


def test_an_empty_message_is_answered_without_a_turn(tmp_path):
    empty = {"message_id": "e1", "from_user_id": USER, "message_type": 1,
             "context_token": CONTEXT, "item_list": []}
    api = FakeILink([batch(empty)])
    agent = FakeAgent()
    gateway = make_gateway(tmp_path, api)

    with Host(build=lambda: agent) as host:
        gateway.start(host)
        _wait_for(lambda: len(api.sent) == 1)
        gateway.stop()

    assert agent.responded == []
    assert "空消息" in api.sent_texts()[0]


def test_a_message_without_a_context_token_is_skipped_not_claimed(tmp_path):
    """No context token means it cannot be answered. Leaving it unclaimed means a
    later delivery has another chance; claiming it would pretend it was handled."""
    broken = {"message_id": "n1", "from_user_id": USER, "message_type": 1,
              "context_token": "", "item_list": [{"type": 1, "text_item": {"text": "hi"}}]}
    api = FakeILink([batch(broken, cursor="c1"), batch(broken, cursor="c2")])
    agent = FakeAgent()
    gateway = make_gateway(tmp_path, api)

    with Host(build=lambda: agent) as host:
        gateway.start(host)
        _wait_for(lambda: len(api.cursors_seen) >= 2)
        gateway.stop()

    assert agent.responded == []
    assert gateway.status()["unfinished"] == []
    assert gateway.status()["logged_in"] is True


# ---------------------------------------------------- cursor ordering


def test_the_cursor_is_not_advanced_when_the_turn_could_not_run(tmp_path):
    """A full queue means the turn never ran. Advancing the cursor there would
    drop the message permanently; leaving it lets the next poll re-deliver, and
    the released claim lets it be handled then."""
    api = FakeILink([batch(user_text("m1", "important"), cursor="cursor-moved")])
    state = wechat.GatewayState(tmp_path / "wechat")
    state.save_credentials({"token": "t", "baseUrl": wechat.BASE_URL})
    gateway = wechat.WeChatGateway(state, api, allowed=[USER], poll_interval=0.01,
                                 send_retry_seconds=0.0)

    class RefusingHost:
        """Stands in for a host whose queue is full."""

        def ask(self, *_a, **_k):
            from waku.runtime.host import HostBusy

            raise HostBusy("the queue is full")

    response = api.fetch_updates(wechat.BASE_URL, "t", state.cursor())
    result = gateway._handle_batch(RefusingHost(), wechat.BASE_URL, "t", response)

    assert result is None, "the batch reported success even though the turn never ran"
    assert state.cursor() == "", "the cursor advanced past a message that never ran"
    assert state.seen.is_known("m1") is False, "the claim was not released for a retry"


def test_the_cursor_advances_after_a_batch_that_did_run(tmp_path):
    api = FakeILink([batch(user_text("m1"), cursor="cursor-moved")])
    state = wechat.GatewayState(tmp_path / "wechat")
    state.save_credentials({"token": "t", "baseUrl": wechat.BASE_URL})
    gateway = wechat.WeChatGateway(state, api, allowed=[USER], poll_interval=0.01,
                                 send_retry_seconds=0.0)
    agent = FakeAgent()
    host = Host(build=lambda: agent)
    host.start()
    try:
        response = api.fetch_updates(wechat.BASE_URL, "t", state.cursor())
        gateway._handle_batch(host, wechat.BASE_URL, "t", response)
    finally:
        host.stop()

    assert state.cursor() == "cursor-moved"
    assert state.seen.is_known("m1")


# ------------------------------------ gateway failure never takes the host down


def test_a_gateway_that_fails_to_start_leaves_the_host_working(tmp_path):
    """The whole point of the try/except in register/start: a broken channel must
    not take the browser with it."""
    class ExplodingGateway:
        name = "wechat"

        def start(self, host):
            raise RuntimeError("bad credentials file")

        def stop(self):
            pass

    agent = FakeAgent()
    with Host(build=lambda: agent, gateways=[ExplodingGateway()]) as host:
        assert "bad credentials file" in host.gateway_error("wechat")
        # The dashboard still works.
        assert host.ask("hi", source="dashboard", session_id="dash-1").reply == "agent:hi"


def test_a_gateway_that_fails_to_stop_does_not_stop_the_host(tmp_path):
    class ExplodingStop:
        name = "wechat"

        def start(self, host):
            pass

        def stop(self):
            raise RuntimeError("cannot close")

    agent = FakeAgent()
    host = Host(build=lambda: agent, gateways=[ExplodingStop()])
    host.start()
    host.ask("hi", source="dashboard", session_id="dash-1")
    assert host.stop() is True, "a failing gateway.stop() broke host shutdown"
    assert "cannot close" in host.gateway_error("wechat")
    assert agent.closed


def test_the_gateway_never_touches_the_waku_instance(tmp_path):
    """The gateway holds the host, never the Waku — that is what keeps the iLink
    protocol, the credentials and the cursor out of the loop and the memory."""
    api = FakeILink([batch(user_text("m1"))])
    gateway = make_gateway(tmp_path, api)
    seen = {}

    class RecordingHost:
        def ask(self, text, *, source, session_id):
            seen["called"] = (text, source, session_id)
            return type("R", (), {"reply": "ok", "tool_calls": [], "iterations": 1})()

    host = RecordingHost()
    response = api.fetch_updates(wechat.BASE_URL, "t", "")
    gateway._handle_batch(host, wechat.BASE_URL, "t", response)

    assert set(seen) == {"called"}, "the gateway reached for something other than ask()"
    assert not hasattr(gateway, "_agent") and not hasattr(gateway, "agent")


# ------------------------------------------------------------ credentials


def test_credentials_never_appear_in_status_or_logs(tmp_path):
    api = FakeILink([batch(user_text("m1"))])
    gateway = make_gateway(tmp_path, api)
    gateway._last_error = "polling https://example (accountId=bot@im.bot)"

    blob = json.dumps(gateway.status(), ensure_ascii=False)
    assert "bot-token" not in blob
    assert "token" not in gateway.status()
    assert "token" not in json.dumps(gateway.status()["last_error"])


def test_state_lives_under_the_gateway_directory_and_nowhere_else(tmp_path):
    """Everything the gateway persists is under <home>/wechat/, and .waku/ as a
    whole is gitignored."""
    home = tmp_path / "home"
    directory = wechat.state_directory(home)
    assert directory == home / "wechat"

    state = wechat.GatewayState(directory)
    state.save_credentials({"token": "secret-token"})
    state.save_cursor("c1")
    state.seen.claim("m1")

    files = sorted(p.name for p in directory.iterdir())
    assert files == ["credentials.json", "cursor.json", "seen.json"]
    assert state.credentials()["token"] == "secret-token"


# ---------------------------------------------------------------- notices


def make_gateway_with_notices(tmp_path, api):
    """Same gateway, but its user-facing notices go to a list.

    Not capsys: several gateways' poll threads can outlive their own test for a
    few milliseconds, and they all write to the same captured stdout."""
    notices: list = []
    state = wechat.GatewayState(tmp_path / "wechat")
    state.save_credentials({"token": "bot-token", "baseUrl": wechat.BASE_URL,
                            "accountId": "bot@im.bot", "userId": USER})
    gateway = wechat.WeChatGateway(state, api, allowed=[USER], poll_interval=0.01,
                                   send_retry_seconds=0.0, announce=notices.append)
    return gateway, notices


def test_an_expired_session_is_announced_and_points_at_the_fix(tmp_path):
    """Expiry needs the user to do something, so it cannot live only in a status
    field that a different process would have to read."""
    api = FakeILink([wechat.ApiError("session timeout", code=wechat.SESSION_EXPIRED_CODE)])
    gateway, notices = make_gateway_with_notices(tmp_path, api)

    with Host(build=lambda: FakeAgent()) as host:
        gateway.start(host)
        _wait_for(lambda: gateway.status()["phase"] == "expired")

    assert any("waku wechat login" in n for n in notices), notices
    assert any("dashboard is unaffected" in n for n in notices), notices


def test_repeated_poll_failures_are_announced_once_then_recovery(tmp_path):
    """A retry loop must not spam the terminal, but the user does need to know it
    dropped — and that it came back."""
    api = FakeILink([
        wechat.ApiError("network error: down"),
        wechat.ApiError("network error: down"),
        wechat.ApiError("network error: down"),
        batch(user_text()),
    ])
    gateway, notices = make_gateway_with_notices(tmp_path, api)

    with Host(build=lambda: FakeAgent()) as host:
        gateway.start(host)
        _wait_for(lambda: len(api.sent) == 1)
        gateway.stop()

    failures = [n for n in notices if "polling failed" in n]
    assert len(failures) == 1, notices
    assert any("recovered" in n for n in notices), notices
    assert not any("bot-token" in n for n in notices), "a credential reached a notice"


# ---------------------------------------------------------- authorisation


def test_an_unauthorised_sender_never_reaches_the_agent(tmp_path):
    """The allowlist check is before `host.ask`, not after it. Anything that can
    reach the bot would otherwise be able to run a tool-calling agent."""
    stranger = "someone-else@im.wechat"
    api = FakeILink([batch(user_text("m1", "delete everything", user_id=stranger))])
    agent = FakeAgent()
    state = wechat.GatewayState(tmp_path / "wechat")
    state.save_credentials({"token": "t", "baseUrl": wechat.BASE_URL})
    gateway = wechat.WeChatGateway(state, api, allowed=[USER], poll_interval=0.01,
                                   send_retry_seconds=0.0)

    with Host(build=lambda: agent) as host:
        gateway.start(host)
        _wait_for(lambda: gateway.status()["refused"] == 1)
        gateway.stop()

    assert agent.responded == [], "an unauthorised message reached the agent"
    assert api.posts == [], "it answered a stranger"
    assert gateway.status()["refused_senders"] == [stranger]
    assert state.seen.is_known("m1") is False, "a refused message was recorded as handled"


def test_an_empty_allowlist_refuses_everyone(tmp_path):
    """Fail closed. A missing config line must not read as "everyone is welcome"."""
    api = FakeILink([batch(user_text("m1"))])
    agent = FakeAgent()
    state = wechat.GatewayState(tmp_path / "wechat")
    state.save_credentials({"token": "t", "baseUrl": wechat.BASE_URL})
    gateway = wechat.WeChatGateway(state, api, poll_interval=0.01, send_retry_seconds=0.0)

    with Host(build=lambda: agent) as host:
        gateway.start(host)
        _wait_for(lambda: gateway.status()["refused"] == 1)
        gateway.stop()

    assert agent.responded == []
    assert api.posts == []
    assert gateway.status()["allowed"] == []


def test_the_allowlist_parses_the_env_value_and_ignores_blanks(monkeypatch):
    monkeypatch.setenv("WAKU_WECHAT_ALLOW", f" {USER} , second@im.wechat ,,")
    assert wechat.allowed_senders(Settings()) == {USER, "second@im.wechat"}
    monkeypatch.setenv("WAKU_WECHAT_ALLOW", "   ")
    assert wechat.allowed_senders(Settings()) == set()


# --------------------------------------------------- messages with no id


def test_a_message_without_an_id_never_runs_a_turn(tmp_path):
    """No id means nothing to deduplicate on, so every re-delivery would run the
    turn — and its tools — again. Refuse visibly instead of guessing."""
    no_id = {"from_user_id": USER, "message_type": 1, "context_token": CONTEXT,
             "item_list": [{"type": 1, "text_item": {"text": "make me a meeting"}}]}
    api = FakeILink([batch(no_id, cursor="c1"), batch(no_id, cursor="c2")])
    agent = FakeAgent()
    gateway = make_gateway(tmp_path, api)

    with Host(build=lambda: agent) as host:
        gateway.start(host)
        _wait_for(lambda: len(api.cursors_seen) >= 2)
        gateway.stop()

    assert agent.responded == [], "an undeduplicable message ran a tool-calling turn"
    assert api.posts == []
    assert gateway.status()["no_id"] >= 1


# ------------------------------------------------- chunked send retries


class LongAgent(FakeAgent):
    def respond(self, text, observer=None, source="cli", stream=False):
        return type("R", (), {"reply": "x" * (wechat.MAX_TEXT_CHUNK + 50),
                              "tool_calls": [], "iterations": 1})()


def test_a_confirmed_chunk_is_not_resent_when_a_later_one_fails(tmp_path):
    """The acceptance point, exactly: chunk 1 confirmed, chunk 2 fails, and the
    retry must not send chunk 1 again."""
    class SecondChunkFails(FakeILink):
        def __init__(self):
            super().__init__([batch(user_text("m1", "long"))])
            self.posts = []
            self.ok = []

        def post_message(self, base_url, token, message):
            text = message["item_list"][0]["text_item"]["text"]
            self.posts.append(text)
            if not self.ok:
                self.ok.append(text)
                self.sent.append(message)
                return {"ret": 0}
            raise wechat.ApiError("second chunk failed", status=500, code=500)

    api = SecondChunkFails()
    agent = LongAgent()
    gateway = make_gateway(tmp_path, api)

    with Host(build=lambda: agent) as host:
        gateway.start(host)
        _wait_for(lambda: gateway.status()["pending"])
        gateway.stop()

    first_chunk = api.ok[0]
    rest = [text for text in api.posts if text != first_chunk]
    assert len(api.posts) > 1, "the second chunk was never retried"
    assert api.posts.count(first_chunk) == 1, (
        f"the confirmed chunk was sent {api.posts.count(first_chunk)} times"
    )
    assert rest and len(set(rest)) == 1, (
        "a retry sent something other than the pending chunk"
    )
    pending = gateway.status()["pending"]
    assert pending[0]["sentChunks"] == 1 and pending[0]["totalChunks"] == 2


def test_a_retried_chunk_reuses_the_same_client_id(tmp_path):
    """A fresh uuid per attempt would make the server treat the retry as a second
    message, which is how a user gets the same sentence twice."""
    api = FakeILink([batch(user_text("m1", "hi"))], send_failures=wechat.SEND_ATTEMPTS)
    gateway = make_gateway(tmp_path, api)

    with Host(build=lambda: FakeAgent()) as host:
        gateway.start(host)
        _wait_for(lambda: len(api.posts) >= 2)
        gateway.stop()

    assert len({message["client_id"] for message in api.posts}) == 1


def test_each_chunk_gets_its_own_stable_client_id():
    assert wechat.stable_client_id("m1", 0) == wechat.stable_client_id("m1", 0)
    assert wechat.stable_client_id("m1", 0) != wechat.stable_client_id("m1", 1)
    assert wechat.stable_client_id("m1", 0) != wechat.stable_client_id("m2", 0)


# ------------------------------------------- what "HTTP 200" really means


class _FakeResponse:
    """Just enough of urlopen's return value for `_call`."""

    def __init__(self, payload, status=200):
        self.status = status
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _urlopen_returning(monkeypatch, payload, status=200):
    monkeypatch.setattr(wechat.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse(payload, status))


def test_an_errcode_without_a_ret_field_is_still_a_failure(monkeypatch):
    """Measured against the live API on 2026-09-23: sending with a dead session
    answers **HTTP 200** with `{"errcode": -14, "errmsg": "session timeout"}` and
    no `ret` field at all.

    A checker that only reads `ret` sees nothing wrong and reports the send as
    delivered — which is the one thing a send must never get wrong. This is why
    `_call` looks at both fields.
    """
    _urlopen_returning(monkeypatch, {"errcode": -14, "errmsg": "session timeout"})

    with pytest.raises(wechat.ApiError) as raised:
        wechat._call("https://example/ilink/bot/sendmessage", data={"msg": {}}, timeout=5)

    assert raised.value.code == wechat.SESSION_EXPIRED_CODE
    assert "session timeout" in str(raised.value)


def test_a_ret_only_error_is_still_caught(monkeypatch):
    """The API's other error shape, also measured: err_msg + ret."""
    _urlopen_returning(monkeypatch, {"err_msg": "invalid bot_type", "ret": 2})

    with pytest.raises(wechat.ApiError) as raised:
        wechat._call("https://example/ilink/bot/get_bot_qrcode", timeout=5)

    assert raised.value.code == 2


def test_a_clean_response_is_not_an_error(monkeypatch):
    _urlopen_returning(monkeypatch, {"ret": 0, "msgs": []})
    assert wechat._call("https://example/x", timeout=5)["ret"] == 0


def test_a_send_that_loses_its_session_is_kept_and_reported(tmp_path):
    """End to end: the expired send must leave the reply queued, not delivered,
    and say why."""
    class ExpiredOnSend(FakeILink):
        def post_message(self, base_url, token, message):
            self.posts.append(message)
            raise wechat.ApiError("session timeout", code=wechat.SESSION_EXPIRED_CODE)

    api = ExpiredOnSend([batch(user_text("m1", "hi"))])
    notices: list = []
    agent = FakeAgent()
    state = wechat.GatewayState(tmp_path / "wechat")
    state.save_credentials({"token": "t", "baseUrl": wechat.BASE_URL})
    gateway = wechat.WeChatGateway(state, api, allowed=[USER], poll_interval=0.01,
                                   send_retry_seconds=0.0, announce=notices.append)

    with Host(build=lambda: agent) as host:
        gateway.start(host)
        # 发送撞上 -14 → phase 变 expired；回复留在 outbox 里。
        _wait_for(lambda: gateway.status()["phase"] == "expired")
        _wait_for(lambda: gateway.status()["pending"])
        expired_phase = gateway.status()["phase"]
        gateway.stop()

    assert len(api.posts) == 1, "it retried a token that is already dead"
    assert expired_phase == "expired"
    assert any("waku wechat login" in n for n in notices), notices
    assert gateway.status()["pending"][0]["sentChunks"] == 0
