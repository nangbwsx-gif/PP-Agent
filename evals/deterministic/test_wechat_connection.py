"""DETERMINISTIC EVAL — WeChat on the Connections page.

The card, the enable/save path and the browser login flow, all offline: `FakeILink`
stands in for the two iLink calls the login makes, and nothing here reaches the
network or reads the real runtime directory.

Two things this file exists to protect, both of them acceptance criteria:

  * saving the WeChat card must restart **that gateway only**. A provider switch
    rebuilds the one Waku instance; a channel has no business interrupting a turn
    that is in flight, so the save path must be ReloadMode.GATEWAY and the rebuild
    count must stay zero.
  * no response the dashboard serves may carry the bot token or a context_token.
    The token lives in .waku/wechat/credentials.json and is never rendered, logged
    or returned — including by the QR endpoints.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from evals.helpers import stub_host
from waku import integrations
from waku.gateway import wechat
from waku.integrations import IntegrationState
from waku.ops import dashboard
from waku.runtime import conversation

USER = "o9cq800kum_4g8Py8Qw5G0a@im.wechat"
TOKEN = "ilinkbot_supersecret_token_value"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """A throwaway home and a throwaway .env, plus clean module globals.

    The .env writer is redirected by evals/conftest.py for every test, so this
    fixture only has to keep the credentials out of the real .waku/wechat/ and
    clear the module globals these tests poke.
    """
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WAKU_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(dashboard, "_wechat", None)
    monkeypatch.setattr(dashboard, "_pending_login", None)
    monkeypatch.setattr(conversation, "_thread_id", None)
    integrations._HEALTH = None
    integrations._reset_import_cache()
    # Stub the host for the whole file. Without this a test that reaches
    # `apply_wechat_gateway()` gets the process-wide Host and starts a REAL
    # gateway on it — which polls iLink with whatever credentials are on disk.
    # One test did exactly that and "passed" while doing it.
    stub_host(monkeypatch)
    return home


class FakeILink:
    """The two iLink calls the login flow makes, scripted."""

    def __init__(self, *, statuses=None, token=TOKEN, qr_url="https://example/qr"):
        self.statuses = list(statuses or [])
        self.token = token
        self.qr_url = qr_url
        self.qr_calls = 0
        self.status_qrcodes = []

    def fetch_qr_code(self, base_url=wechat.BASE_URL):
        self.qr_calls += 1
        return {"qrcode": "qr-token-1", "qrcode_img_content": self.qr_url, "ret": 0}

    def fetch_qr_status(self, qrcode, base_url=wechat.BASE_URL):
        self.status_qrcodes.append(qrcode)
        if not self.statuses:
            return {"status": "wait"}
        return self.statuses.pop(0)


def _use_fake(monkeypatch, fake):
    monkeypatch.setattr(wechat, "ILinkApi", lambda: fake)
    return fake


def _confirmed_status():
    return {"status": "confirmed", "bot_token": TOKEN, "ilink_bot_id": "bot@im.bot",
            "ilink_user_id": USER, "baseurl": wechat.BASE_URL}


def _stub_qr(monkeypatch, svg="<svg><title>qr</title></svg>"):
    """The QR renderer needs qrcode, which CI does not install. The token-safety
    assertions do not care how the SVG was drawn, so they stub it."""
    monkeypatch.setattr(wechat, "qr_svg", lambda url: svg)


# ------------------------------------------------------------- the registry


def test_the_card_is_a_channels_entry_that_restarts_only_itself():
    row = next(item for item in integrations.registry() if item.key == "wechat")
    assert row.group == "Channels", "the Channels group finally has a member"
    assert row.reload is integrations.ReloadMode.GATEWAY, (
        "an AGENT reload would rebuild the Waku instance to toggle a channel"
    )
    assert row.extra == "wechat"


def test_enable_parses_the_way_settings_does():
    """`bool(env.get(...))` would read WAKU_WECHAT=0 as on, and the card's
    checkbox writes exactly "0"."""
    row = next(item for item in integrations.registry() if item.key == "wechat")
    assert row.enabled({"WAKU_WECHAT": "1"}) is True
    assert row.enabled({"WAKU_WECHAT": "true"}) is True
    assert row.enabled({"WAKU_WECHAT": "0"}) is False
    assert row.enabled({"WAKU_WECHAT": ""}) is False
    assert row.enabled({}) is False


def test_an_empty_allow_list_shows_up_as_needing_setup(monkeypatch):
    """Fail-closed is a state the card has to be able to explain, not a silence."""
    monkeypatch.setenv("WAKU_WECHAT", "1")
    monkeypatch.delenv("WAKU_WECHAT_ALLOW", raising=False)
    view = next(v for v in integrations.list_integrations() if v.key == "wechat")
    assert view.status.state is IntegrationState.INSTALLED_BUT_UNCONFIGURED
    assert "WAKU_WECHAT_ALLOW" in view.status.message


# -------------------------------------------------------- the live status


@pytest.mark.parametrize(("status", "expected"), [
    ({"phase": "polling", "account_id": "bot@im.bot"}, IntegrationState.CONNECTED),
    ({"phase": "not-logged-in"}, IntegrationState.INSTALLED_BUT_UNCONFIGURED),
    ({"phase": "expired", "last_error": "session expired"}, IntegrationState.ERROR),
    ({"phase": "stopped"}, IntegrationState.CONFIGURED),
    # A live poll thread whose last request failed is retrying, not connected.
    # `_last_error` is cleared on the next success, so this is not a stale state.
    ({"phase": "polling", "last_error": "getupdates failed"}, IntegrationState.ERROR),
])
def test_each_gateway_phase_has_an_honest_status(status, expected):
    assert dashboard.wechat_phase_status(status).state is expected


def test_without_a_gateway_the_provider_defers(_isolated):
    """No gateway in this process means no opinion — `_status` falls through to
    its own answer rather than the page inventing a connection."""
    assert dashboard.wechat_status_provider("wechat") is None
    assert dashboard.wechat_status_provider("tavily") is None


def test_the_card_reads_the_attached_gateway_not_the_env(monkeypatch, _isolated):
    """The env can look perfectly configured while nothing is receiving. The card
    has to say which of those it is.

    This host stub records gateways without starting them (the real Host does
    start them, and `test_host.py` pins that), so the honest answer here is
    "enabled, not running" — not "connected", and not "needs setup".
    """
    monkeypatch.setenv("WAKU_WECHAT", "1")
    monkeypatch.setenv("WAKU_WECHAT_ALLOW", USER)
    stub_host(monkeypatch)

    # The real startup path — it both attaches the gateway and registers the
    # status hook `list_integrations()` asks.
    dashboard.start_configured_gateways()
    assert dashboard._wechat is not None

    view = next(v for v in integrations.list_integrations() if v.key == "wechat")

    assert view.status.state is IntegrationState.CONFIGURED
    assert "not running" in view.status.message


# ----------------------------------------- enabling restarts one gateway only


def test_enabling_attaches_the_gateway_without_rebuilding_the_waku(monkeypatch, _isolated):
    monkeypatch.setenv("WAKU_WECHAT", "1")
    monkeypatch.setenv("WAKU_WECHAT_ALLOW", USER)
    _stub, rebuilds = stub_host(monkeypatch)

    gateway = dashboard.apply_wechat_gateway()

    assert gateway is not None
    assert dashboard._wechat is gateway
    assert rebuilds == [], "toggling a channel rebuilt the Waku instance"
    dashboard._wechat.stop()


def test_disabling_removes_the_gateway_and_never_rebuilds(monkeypatch, _isolated):
    monkeypatch.setenv("WAKU_WECHAT", "1")
    monkeypatch.setenv("WAKU_WECHAT_ALLOW", USER)
    host, rebuilds = stub_host(monkeypatch)
    dashboard.apply_wechat_gateway()
    assert "wechat" in host.gateways

    monkeypatch.delenv("WAKU_WECHAT", raising=False)
    assert dashboard.apply_wechat_gateway() is None

    assert "wechat" not in host.gateways, "the gateway was left attached after disabling"
    assert dashboard._wechat is None
    assert rebuilds == []


def test_saving_the_card_takes_the_gateway_path_not_the_agent_path(monkeypatch, _isolated):
    """The acceptance criterion, end to end through the real save path: apply a
    WeChat setting and watch what the host was asked to do."""
    host, rebuilds = stub_host(monkeypatch)
    integrations.register_gateway_reloader(dashboard.wechat_reloader)

    result = integrations.apply_integration(
        "wechat", {"WAKU_WECHAT": "1", "WAKU_WECHAT_ALLOW": USER}, ())

    assert result.ok, result.error
    assert rebuilds == [], "a channel save rebuilt the one Waku instance"
    assert "wechat" in host.gateways
    env_file = pathlib.Path(".env")
    if not env_file.exists():
        raise AssertionError("no .env was written; cwd holds "
                             f"{sorted(p.name for p in pathlib.Path('.').iterdir())}")
    assert "WAKU_WECHAT" in env_file.read_text(encoding="utf-8")
    dashboard._wechat.stop()


def test_the_reloader_ignores_other_integrations(monkeypatch, _isolated):
    _stub, rebuilds = stub_host(monkeypatch)
    assert dashboard.wechat_reloader({"tavily"}) == {}
    assert rebuilds == []


# ------------------------------------------------------------- the QR login


def test_the_login_endpoint_returns_an_svg_and_no_token(monkeypatch, _isolated):
    _use_fake(monkeypatch, FakeILink())
    _stub_qr(monkeypatch)

    out = dashboard.wechat_login({})

    assert out["ok"] is True
    assert out["svg"].startswith("<svg")
    assert TOKEN not in json.dumps(out), "the bot token reached the browser"
    assert "token" not in json.dumps(out).lower()


def test_the_status_endpoint_carries_no_credentials(monkeypatch, _isolated):
    _use_fake(monkeypatch, FakeILink(statuses=[{"status": "scaned"}]))
    _stub_qr(monkeypatch)
    dashboard.wechat_login({})

    out = dashboard.wechat_login_status({})

    assert out["status"] == "scaned"
    blob = json.dumps(out)
    assert TOKEN not in blob
    assert "bot_token" not in blob
    assert "context_token" not in blob


def test_confirming_saves_the_credentials_and_starts_the_gateway(monkeypatch, _isolated):
    monkeypatch.setenv("WAKU_WECHAT", "1")
    monkeypatch.setenv("WAKU_WECHAT_ALLOW", USER)
    _stub, _rebuilds = stub_host(monkeypatch)
    _use_fake(monkeypatch, FakeILink(statuses=[_confirmed_status()]))
    _stub_qr(monkeypatch)

    dashboard.wechat_login({})
    out = dashboard.wechat_login_status({})

    assert out["status"] == "confirmed"
    assert out["accountId"] == "bot@im.bot"
    assert out["userId"] == USER
    saved = wechat.GatewayState(wechat.state_directory()).credentials()
    assert saved and saved["token"] == TOKEN, "the token was not persisted where it belongs"
    assert dashboard._pending_login is None, "the QR session was left open"
    assert dashboard._wechat is not None, "the gateway did not pick up the new credentials"
    dashboard._wechat.stop()


def test_a_confirmed_login_never_reaches_the_response(monkeypatch, _isolated):
    """The confirm reply carries the token in the iLink payload. It must not
    survive the trip to the browser."""
    _use_fake(monkeypatch, FakeILink(statuses=[_confirmed_status()]))
    _stub_qr(monkeypatch)
    dashboard.wechat_login({})

    blob = json.dumps(dashboard.wechat_login_status({}))

    assert TOKEN not in blob
    assert "ilinkbot" not in blob


def test_an_expired_qr_closes_the_session(monkeypatch, _isolated):
    _use_fake(monkeypatch, FakeILink(statuses=[{"status": "expired"}]))
    _stub_qr(monkeypatch)
    dashboard.wechat_login({})

    assert dashboard.wechat_login_status({})["status"] == "expired"
    assert dashboard._pending_login is None
    assert "no login is in progress" in dashboard.wechat_login_status({})["error"]


def test_status_without_a_login_says_so_instead_of_calling_out(monkeypatch, _isolated):
    fake = _use_fake(monkeypatch, FakeILink())
    out = dashboard.wechat_login_status({})
    assert "no login is in progress" in out["error"]
    assert fake.status_qrcodes == []


def test_a_missing_qrcode_package_is_an_install_hint_not_a_crash(monkeypatch, _isolated):
    """qrcode is the [wechat] extra. Losing it must read as advice, not a 500."""
    _use_fake(monkeypatch, FakeILink())

    def explode(url):
        raise RuntimeError("qrcode is not installed — pip install 'waku-agent[wechat]'")

    monkeypatch.setattr(wechat, "qr_svg", explode)
    out = dashboard.wechat_login({})

    assert "pip install" in out["error"]


def test_the_qr_is_drawn_with_a_white_background():
    """A QR is black-on-transparent by default, which is invisible on the dark
    theme — and the design system will not let CSS fix that, so the code owns it."""
    pytest.importorskip("qrcode")
    svg = wechat.qr_svg("https://example.test/qr")
    assert 'fill="#ffffff"' in svg, "no white field: this code will not scan on dark"
    assert "<svg " in svg


# --------------------------------------------------------------- log out


def test_logging_out_clears_the_credentials_and_stops_the_gateway(monkeypatch, _isolated):
    monkeypatch.setenv("WAKU_WECHAT", "1")
    monkeypatch.setenv("WAKU_WECHAT_ALLOW", USER)
    _stub, _rebuilds = stub_host(monkeypatch)
    state = wechat.GatewayState(wechat.state_directory())
    state.save_credentials({"token": TOKEN, "accountId": "bot@im.bot", "userId": USER})
    dashboard.apply_wechat_gateway()

    out = dashboard.wechat_logout({})

    assert out["ok"] is True
    assert out["phase"] == "stopped"      # attached, idle, and logged out
    assert state.credentials() is None, "logout left the token on disk"
    assert dashboard._wechat is not None, "the gateway should still be attached, just logged out"
    dashboard._wechat.stop()


# ------------------------------------------------- what the page may expose


def test_the_page_payload_never_carries_the_token(monkeypatch, _isolated):
    monkeypatch.setenv("WAKU_WECHAT", "1")
    monkeypatch.setenv("WAKU_WECHAT_ALLOW", USER)
    stub_host(monkeypatch)
    state = wechat.GatewayState(wechat.state_directory())
    state.save_credentials({"token": TOKEN, "accountId": "bot@im.bot", "userId": USER,
                            "baseUrl": wechat.BASE_URL})
    dashboard._wechat = wechat.WeChatGateway(state, wechat.ILinkApi())

    payload = dashboard._wechat_payload()

    assert "bot-token" not in payload["accountId"]        # sanity: it is the id, not the key
    blob = json.dumps(payload)
    assert TOKEN not in blob
    assert "token" not in blob.lower(), "a field name alone would invite a leak"
    assert set(payload) >= {"phase", "allowed", "pending", "lastError", "unfinished"}


def test_the_payload_is_absent_when_no_gateway_is_running(_isolated):
    assert dashboard._wechat_payload() is None


# ------------------------------------------------------------ local-only


def test_the_wechat_routes_are_guarded_to_local_clients():
    """The server binds 127.0.0.1, so this is the second lock on the same door:
    a QR session and a login state are not for another host to reach."""
    import inspect

    source = inspect.getsource(dashboard.Handler.do_POST)
    assert 'startswith("/api/wechat/")' in source
    assert "_is_local" in source

    handler = dashboard.Handler.__new__(dashboard.Handler)
    handler.client_address = ("127.0.0.1", 12345)
    assert handler._is_local() is True
    handler.client_address = ("::1", 12345)
    assert handler._is_local() is True
    handler.client_address = ("192.168.1.50", 12345)
    assert handler._is_local() is False
