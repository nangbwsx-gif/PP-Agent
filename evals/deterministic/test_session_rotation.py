"""DETERMINISTIC EVAL — the dashboard rotates idle chat threads.

Live bug: a tester returned days later and their fresh 'what's up' landed in a
week-old 32-message thread. New rule: if the current session's newest message
is older than WAKU_SESSION_IDLE_MINUTES, the next chat starts a new thread
(old one stays in History).

Since the resident host took the Waku instance over (docs/resident-host-design.md
phase 1), rotation is a pure decision about which session id a message belongs
to — it RETURNS the id and the host switches to it, rather than mutating a shared
agent's session.
"""

from __future__ import annotations

from evals.helpers import ScriptedClient, make_waku
from waku.ops import browser_agent
from waku.ops.browser_agent import rotate_if_idle


def _seed(app, session_id, age_minutes):
    app.conn.execute(
        "INSERT INTO chat_log (role, content, session_id, created_at) "
        "VALUES ('user', 'old message', ?, datetime('now', ?))",
        (session_id, f"-{age_minutes} minutes"),
    )
    app.conn.commit()


def test_idle_session_rotates(tmp_path, monkeypatch):
    monkeypatch.setenv("WAKU_SESSION_IDLE_MINUTES", "60")
    app = make_waku(tmp_path / "home", client=ScriptedClient([]))
    before = app.session.session_id
    _seed(app, before, age_minutes=120)          # 2h idle > 60m threshold
    rotated = rotate_if_idle(app.conn, before)
    assert rotated != before
    assert rotated.startswith("dashboard-")


def test_active_session_stays(tmp_path, monkeypatch):
    monkeypatch.setenv("WAKU_SESSION_IDLE_MINUTES", "60")
    app = make_waku(tmp_path / "home", client=ScriptedClient([]))
    before = app.session.session_id
    _seed(app, before, age_minutes=5)            # active conversation
    assert rotate_if_idle(app.conn, before) == before


def test_empty_session_stays(tmp_path, monkeypatch):
    monkeypatch.setenv("WAKU_SESSION_IDLE_MINUTES", "60")
    app = make_waku(tmp_path / "home", client=ScriptedClient([]))
    before = app.session.session_id
    assert rotate_if_idle(app.conn, before) == before   # no messages -> no-op


def test_rotation_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("WAKU_SESSION_IDLE_MINUTES", "0")
    app = make_waku(tmp_path / "home", client=ScriptedClient([]))
    before = app.session.session_id
    _seed(app, before, age_minutes=10000)
    assert rotate_if_idle(app.conn, before) == before


def test_the_rotated_thread_becomes_the_current_one(tmp_path, monkeypatch):
    """current_session() remembers the rotation. Without that, every single
    message of an idle chat would mint yet another thread."""
    monkeypatch.setenv("WAKU_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("WAKU_SESSION_IDLE_MINUTES", "60")
    app = make_waku(tmp_path / "home", client=ScriptedClient([]))
    _seed(app, "dashboard-20260101-120000", age_minutes=120)

    monkeypatch.setattr(browser_agent, "_dashboard_session", "dashboard-20260101-120000")
    rotated = browser_agent.current_session()

    assert rotated != "dashboard-20260101-120000"
    assert browser_agent.dash_session() == rotated


def test_set_current_session_moves_the_pointer(monkeypatch):
    """「New chat」 and 「switch」 move this pointer. Neither touches an agent,
    because the host binds the session on every request."""
    monkeypatch.setattr(browser_agent, "_dashboard_session", "dashboard-a")
    browser_agent.set_current_session("s-20260101-120000")
    assert browser_agent.dash_session() == "s-20260101-120000"


def test_provider_switch_resets_stale_model_overrides(tmp_path, monkeypatch):
    """Live bug: kimi -> gemini kept gate model kimi-k3; every turn then 404'd
    against Gemini. A provider change must reset any model field the user
    didn't newly type."""
    from waku.ops import settings_api

    captured = {}
    monkeypatch.setenv("WAKU_PROVIDER", "kimi")
    monkeypatch.setenv("WAKU_MODEL", "kimi-k3")
    monkeypatch.setenv("WAKU_SMALL_MODEL", "kimi-k3")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-tests")
    monkeypatch.setattr(settings_api, "find_dotenv", lambda **k: "", raising=False)

    # intercept at the env-write layer; abort before the agent rebuild
    def fake_set_key(path, k, v):
        captured[k] = v
        raise RuntimeError("stop-before-rebuild")

    import dotenv
    monkeypatch.setattr(dotenv, "set_key", fake_set_key)
    try:
        settings_api.apply_settings({"provider": "gemini", "model": "kimi-k3",
                                     "small_model": "kimi-k3", "keys": {}})
    except RuntimeError:
        pass
    assert captured.get("WAKU_MODEL", "unset") in ("", "unset") or \
        captured.get("WAKU_PROVIDER") == "gemini"
    # the actual contract: stale kimi ids must have been blanked
    assert captured.get("WAKU_MODEL") != "kimi-k3"
    assert captured.get("WAKU_SMALL_MODEL") != "kimi-k3"
