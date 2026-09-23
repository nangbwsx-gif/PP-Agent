"""DETERMINISTIC EVAL — the dashboard's public surface, pinned.

This is a characterization net, not a feature test. It exists so that moving or
deleting code inside dashboard.py cannot silently remove a URL the browser calls
or a key the page reads. Written BEFORE the dead-code deletion and the 1,695 ->
~880 line split, and it stayed green through both without a single edit to the
route lists — which is the whole point: the browser's contract never moved, only
the code behind it.

The handler list below is checked with getattr(dashboard, ...), so it also pins
the re-exports. After the split, most handlers LIVE in arena / catalog /
settings_api / browser_agent and are imported here. If one stops being reachable
from `dashboard`, the router breaks and this fails.

If you add a route or a payload key on purpose, update the list below in the
same commit — that edit is the review signal that the public surface changed.
"""

from __future__ import annotations

import inspect

import pytest

from evals.helpers import FakeAgent
from waku.ops import dashboard

# Every path the POST router accepts. `/api/compare` (non-streaming) was removed
# on 2026-07-26: nothing called it, and its implementation had drifted behind the
# streaming one badly enough to return wrong scores.
POST_ROUTES = {
    "/api/chat",
    "/api/memory",
    "/api/settings",
    "/api/query",
    "/api/session",
    "/api/pin",
    "/api/compare/clear",
    "/api/compare/regrade",
    "/api/compare/delete_run",
}

# Paths served on GET, either exactly or as a prefix.
GET_PATHS = {
    "/api/data",
    "/api/models",
    "/api/events",
    "/api/reveal",
    "/api/compare/history",
    "/static/",
}

# Streaming endpoints. These are what the dashboard actually uses for chat and
# racing; the browser reads them as SSE, so they are handled before the router.
STREAM_ROUTES = {"/api/chat/stream", "/api/compare/stream", "/api/voice",
                 "/api/graph/stream"}


def _source() -> str:
    return inspect.getsource(dashboard)


def test_every_post_route_is_still_registered():
    src = _source()
    for path in POST_ROUTES:
        assert f'"{path}"' in src, f"POST route disappeared: {path}"


def test_every_get_path_is_still_served():
    src = _source()
    for path in GET_PATHS:
        assert f'"{path}"' in src, f"GET path disappeared: {path}"


def test_streaming_routes_survive():
    """The dashboard's chat dock and the Arena both depend on these. Losing one
    breaks the UI silently — the fetch just 404s into a dead column."""
    src = _source()
    for path in STREAM_ROUTES:
        assert f'"{path}"' in src, f"streaming route disappeared: {path}"


def test_the_handlers_behind_the_routes_exist_and_are_callable():
    for name in ("collect", "chat", "chat_stream", "compare_stream", "graph_stream", "memory_action",
                 "apply_settings", "run_query", "session_action", "pin_action",
                 "list_models", "events_since", "reveal_path", "settings_info",
                 "tools_info", "compare_clear", "compare_regrade", "compare_delete_run"):
        fn = getattr(dashboard, name, None)
        assert callable(fn), f"handler missing or not callable: {name}"


def test_api_models_returns_picker_contract(monkeypatch):
    """The model picker depends on /api/models returning models and listed."""
    import io
    import json
    import urllib.request

    from waku.ops import catalog

    monkeypatch.setenv("WAKU_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    monkeypatch.delenv("WAKU_MODEL", raising=False)
    monkeypatch.delenv("WAKU_SMALL_MODEL", raising=False)

    def fake_urlopen(req, timeout=10):
        return io.BytesIO(json.dumps(
            {"data": [{"id": "vendor/model:free"}]}
        ).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    catalog._models_cache.clear()

    result = catalog.list_models("openrouter")
    assert result["listed"] is True
    assert isinstance(result["models"], list)
    assert result["models"][0]["id"] == "vendor/model:free"

    catalog._models_cache.clear()


def test_collect_returns_the_keys_the_page_reads():
    """`/api/data` is read by every view in static/js/. These are the keys the
    frontend indexes into; dropping one blanks a tab with no error."""
    expected = {
        "settings", "tools", "facts", "episodes", "soul", "chat_log", "sessions",
        "turns", "stats", "db", "skills", "trace_file", "chat_pending", "graph",
    }
    src = inspect.getsource(dashboard.collect)
    for key in expected:
        assert f'"{key}"' in src, f"collect() no longer returns: {key}"


def test_the_removed_arena_duplicate_stays_removed():
    """_compare_one and compare_models were a stale copy of the arena, missing
    completion scoring, quality grading, sub-agent relay, history recording and
    the scoring module. Anyone reaching them got a race that looked right and
    was quietly wrong. Re-adding a second racing path should be deliberate."""
    src = _source()
    assert "def _compare_one" not in src
    assert "def compare_models" not in src


def test_a_second_dashboard_cannot_bind_an_occupied_port():
    """端口被占必须真的绑不上 —— main() 的"换下一个端口"循环全指望这件事。

    HTTP 的 HTTPServer 默认 allow_reuse_address = 1，而 Windows 的
    SO_REUSEADDR 语义和 POSIX 不同：它允许第二个 socket 绑到已经在用的地址
    上，不报错。结果是循环不触发、两个 dashboard 同时听着 7777，谁先接住
    请求谁响应 —— 用户看到的是"我改了配置或换了语言，刷新却没变化"。
    这个仓库里被它骗过两次：一次这页永远显示旧语言，一次是中文实例把英文
    实例的请求接走了。

    这个测试在两种平台上都有意义：POSIX 的 SO_REUSEADDR 本来就不允许双绑
    （那需要 SO_REUSEPORT），所以断言在两边都成立。
    """
    from http.server import BaseHTTPRequestHandler

    from waku.ops.dashboard import DashboardServer

    class Noop(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

    first = DashboardServer(("127.0.0.1", 0), Noop)
    try:
        port = first.server_address[1]
        with pytest.raises(OSError):
            DashboardServer(("127.0.0.1", port), Noop)
    finally:
        first.server_close()


def test_the_server_never_joins_a_handler_on_close():
    """Why `server_close()` cannot hang, pinned so nobody "fixes" it away.

    `ThreadingMixIn.server_close()` joins the handler threads it registered, but
    its `_Threads.append()` returns early for daemon threads — CPython's own
    comment calls it "Joinable list of all non-daemon threads". So the inherited
    `daemon_threads = True` is what makes a mid-turn chat handler unable to hold
    the exit. Flip either one and `server_close()` starts waiting on a handler
    that is waiting for its turn, which is where the ordering in
    `serve_until_signalled` becomes load-bearing.
    """
    from waku.ops.dashboard import DashboardServer

    assert DashboardServer.daemon_threads is True


# ------------------------------------------------------------------ shutdown


def _wait_for(predicate, seconds=10.0):
    import time

    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert predicate()


def test_an_http_request_queued_behind_a_turn_still_gets_an_answer(tmp_path, monkeypatch):
    """Taking the service down must not leave a browser request hanging.

    Both acceptance points at once: the request queued behind the running turn is
    answered instead of waiting its turn, and that running turn is left alone to
    finish on a connection that is still open.
    """
    import json
    import socket
    import threading
    import urllib.request

    from waku.runtime import host as host_module
    from waku.runtime.host import Host

    monkeypatch.setenv("WAKU_HOME", str(tmp_path / "home"))
    release = threading.Event()
    agent = FakeAgent(gate=release)
    host = Host(build=lambda: agent, stop_timeout=0.3)
    monkeypatch.setattr(host_module, "shared_host", lambda: host)
    monkeypatch.setattr(host_module, "live_host", lambda: host)
    host.start()      # shared_host() would normally do this; here the host is injected

    server = dashboard.DashboardServer(("127.0.0.1", 0), dashboard.Handler)
    port = server.server_address[1]
    serving = threading.Thread(target=lambda: dashboard.serve_until_signalled(server), daemon=True)
    serving.start()

    def port_is_open():
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            return False

    _wait_for(port_is_open)

    answers: dict = {}

    def post(text):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/chat/stream",
            data=json.dumps({"message": text}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                answers[text] = response.read().decode("utf-8", "replace")
        except Exception as exc:      # recorded, not raised: the point is the answer
            answers[text] = f"<<{type(exc).__name__}: {exc}>>"

    running = threading.Thread(target=post, args=("running turn",))
    running.start()
    _wait_for(lambda: bool(agent.responded))          # the worker is inside turn one

    queued = threading.Thread(target=post, args=("queued turn",))
    queued.start()
    _wait_for(lambda: host.pending() == 1)            # the second one is waiting

    # Ctrl-C, from a thread — exactly what the signal handler does.
    threading.Thread(target=server.shutdown, daemon=True).start()
    serving.join(15)
    assert not serving.is_alive(), "the dashboard hung on shutdown"

    queued.join(15)
    assert not queued.is_alive(), "the queued request hung through shutdown"
    assert "shutting down" in answers["queued turn"], answers["queued turn"]

    # The turn that was running must still be able to finish, on a live connection.
    release.set()
    running.join(15)
    assert not running.is_alive(), "the running request hung"
    assert "agent:running turn" in answers["running turn"], answers["running turn"]
    assert agent.closed is False, "the connection was closed under the live turn"


def test_serve_until_signalled_closes_the_socket_and_stops_the_host(monkeypatch):
    """`serve_forever()` only returns when somebody calls `shutdown()`, and the
    signal handler in `serve_until_signalled` is that somebody.

    The `finally` around it is the whole shutdown path: close the listening
    socket, then stop the resident host (refuse new requests, answer the waiting
    ones, close the MCP bridge and the SQLite connection). Before this landed
    there was no handler, no `server_close` and no `agent.close` at all, so
    Ctrl-C left the child process and the database handle to the operating
    system.

    Driving `shutdown()` from another thread is exactly what the handler does,
    so this exercises the real path rather than a copy of it.
    """
    import socket
    import threading
    import time

    from waku.runtime import host as host_module

    stopped = []

    class _StubHost:
        def stop(self):
            stopped.append("host.stop")
            return True

    monkeypatch.setattr(host_module, "shared_host", lambda: _StubHost())

    server = dashboard.DashboardServer(("127.0.0.1", 0), dashboard.Handler)
    port = server.server_address[1]
    real_server_close = server.server_close
    server.server_close = lambda: (stopped.append("server_close"), real_server_close())[1]
    threading.Thread(target=lambda: (time.sleep(0.05), server.shutdown()), daemon=True).start()

    dashboard.serve_until_signalled(server)

    assert stopped == ["host.stop", "server_close"], (
        "the host must be stopped BEFORE the server is closed: server_close() joins "
        "every handler thread, and a handler waiting for its turn is only released "
        "by host.stop() rejecting it"
    )
    # The port must be free again: binding it succeeds only if the server let go.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))


def test_the_bind_switch_follows_the_platform():
    """Windows 上必须关掉 reuse-address（那里它允许双绑）；POSIX 上必须留着
    （那里它只影响 TIME_WAIT，关掉会让刚重启的服务偶尔绑不上）。"""
    import sys

    from waku.ops.dashboard import DashboardServer

    assert DashboardServer.allow_reuse_address is (sys.platform != "win32")
