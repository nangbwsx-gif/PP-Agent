"""DETERMINISTIC EVAL — one process resumes its last recent conversation thread.

Live bug: every time the server restarted (which happens a lot during dev), the
chat 'vanished' — the process minted a brand-new dated thread, so the dock loaded
empty and the real conversation was parked under the previous timestamped id.
Fix: on startup, resume the most recent thread if its last message is still
within the idle window; else start fresh.

The thread is now **shared by every gateway** (waku/runtime/conversation.py), so
"most recent" means most recent from any door, not just the browser. That is what
makes the browser able to answer "what did I tell you on WeChat" — and it is why
the `source` filter these tests used to assert is gone: filtering by source split
a single conversation into one thread per channel.
"""

from __future__ import annotations

from evals.helpers import ScriptedClient, make_waku
from waku.runtime.conversation import resume_or_new_session


def _seed(app, session_id, age_minutes, source="dashboard"):
    app.conn.execute(
        "INSERT INTO chat_log (role, content, session_id, created_at, source) "
        "VALUES ('user', 'hi', ?, datetime('now', ?), ?)",
        (session_id, f"-{age_minutes} minutes", source),
    )
    app.conn.commit()


def test_a_recent_thread_is_resumed(tmp_path, monkeypatch):
    monkeypatch.setenv("WAKU_SESSION_IDLE_MINUTES", "60")
    app = make_waku(tmp_path / "home", client=ScriptedClient([]))
    _seed(app, "chat-20260101-120000", age_minutes=5)          # fresh
    assert resume_or_new_session(app.conn) == "chat-20260101-120000"


def test_an_idle_thread_is_not_resumed(tmp_path, monkeypatch):
    monkeypatch.setenv("WAKU_SESSION_IDLE_MINUTES", "60")
    app = make_waku(tmp_path / "home", client=ScriptedClient([]))
    _seed(app, "chat-20260101-120000", age_minutes=120)        # 2h idle > 60m
    got = resume_or_new_session(app.conn)
    assert got != "chat-20260101-120000"
    assert got.startswith("chat-")


def test_the_most_recent_of_several_threads_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("WAKU_SESSION_IDLE_MINUTES", "60")
    app = make_waku(tmp_path / "home", client=ScriptedClient([]))
    _seed(app, "chat-20260101-090000", age_minutes=40)
    _seed(app, "chat-20260101-100000", age_minutes=10)         # newer
    assert resume_or_new_session(app.conn) == "chat-20260101-100000"


def test_a_thread_the_other_gateway_wrote_is_resumed(tmp_path, monkeypatch):
    """The point of sharing one line: a conversation that happened only over
    WeChat is the conversation the browser resumes.

    This test used to assert the opposite — that a phone/cli thread must not
    hijack the browser's resume. That was the isolation rule; it is deliberately
    gone, because it is what split one person's conversation into one thread per
    door and made "what did I say on WeChat" unanswerable.
    """
    monkeypatch.setenv("WAKU_SESSION_IDLE_MINUTES", "60")
    app = make_waku(tmp_path / "home", client=ScriptedClient([]))
    _seed(app, "chat-20260101-120000", age_minutes=1, source="wechat")
    assert resume_or_new_session(app.conn) == "chat-20260101-120000"


def test_a_new_chat_s_prefixed_thread_is_resumed(tmp_path, monkeypatch):
    """Regression: '+ New chat' creates 's-...' ids. Resuming by recency rather
    than by an id prefix means those threads survive a restart too — the bug
    where a new chat 'vanished' when the server bounced."""
    monkeypatch.setenv("WAKU_SESSION_IDLE_MINUTES", "60")
    app = make_waku(tmp_path / "home", client=ScriptedClient([]))
    _seed(app, "s-20260101-120000", age_minutes=3, source="dashboard")
    assert resume_or_new_session(app.conn) == "s-20260101-120000"


def test_an_old_dashboard_named_thread_is_still_resumed(tmp_path, monkeypatch):
    """Threads minted before the rename are still threads. Nothing migrates them
    and nothing needs to: recency is the only key."""
    monkeypatch.setenv("WAKU_SESSION_IDLE_MINUTES", "60")
    app = make_waku(tmp_path / "home", client=ScriptedClient([]))
    _seed(app, "dashboard-20260101-120000", age_minutes=5)
    assert resume_or_new_session(app.conn) == "dashboard-20260101-120000"
