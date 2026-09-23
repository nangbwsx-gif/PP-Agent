"""Shared eval plumbing: a scripted fake LLM client for offline tests, and a
real-Waku factory for live ones."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from types import SimpleNamespace


def _has_key() -> bool:
    """True when the ACTIVE provider (WAKU_PROVIDER) has its key set, so live
    evals run on whatever the user actually configured (anthropic, openrouter,
    gemini, ...), not only on ANTHROPIC_API_KEY."""
    from waku.config import load_settings
    from waku.loop.models import PROVIDERS

    settings = load_settings()
    provider = PROVIDERS.get(settings.provider)
    return bool(settings.api_key or (provider and os.getenv(provider.key_env)))


HAS_KEY = _has_key()


def text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def tool_block(name: str, args: dict, call_id: str = "tu_1"):
    return SimpleNamespace(type="tool_use", id=call_id, name=name, input=args)


def response(blocks, stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=0, output_tokens=0),
        content=blocks,
    )


class ScriptedClient:
    """Plays back a fixed list of responses — the 'model' for offline tests."""

    def __init__(self, script: list):
        self._script = list(script)
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        return self._script.pop(0)


def make_waku(home: Path, client=None, **settings_overrides):
    """Build a Waku with an isolated home dir; optionally swap in a fake client."""
    from waku.app import Waku
    from waku.config import Settings

    # A test must describe its own world. `waku/config.py` calls load_dotenv()
    # at import, so every Settings() default is quietly seeded from whatever is
    # in the maintainer's .env — and a test that reads the developer's machine
    # is not deterministic. Each entry below is a switch that changes what a
    # turn DOES, pinned off unless a test asks for it:
    #   google_calendar        reaches the real calendar (network + a credential)
    #   graph_workflows        route every message through the triage graph,
    #                          which spends one extra model call — on 2026-07-31
    #                          a stale WAKU_GRAPH_WORKFLOWS=1 ate a scripted
    #                          response and failed 8 tests that pass in CI
    # NOT pinned here: `experimental`. test_delegate.py drives it with
    # monkeypatch.setenv to prove the env var really gates registration, and a
    # hardcoded False here would make that wiring untestable. Pin a switch only
    # when no test needs to observe the env reaching Settings.
    #
    # Only switches Settings actually HAS are pinned. Passing an unknown name
    # is a TypeError, so a hardcoded list would break the moment a flag is
    # renamed or lives only on a feature branch — which is exactly what
    # happened to `graph_workflows` here. Filtering keeps this list a superset
    # that costs nothing when an entry is absent.
    known = {f.name for f in dataclasses.fields(Settings)}
    for switch in ("google_calendar", "graph_workflows"):
        if switch in known:
            settings_overrides.setdefault(switch, False)
    settings = Settings(home=home, **settings_overrides)
    if client is not None and not settings.api_key:
        settings.api_key = "offline"  # never read the real key for scripted runs
    return Waku(settings=settings, client=client)


def stub_host(monkeypatch, *, agent=None, rebuild_error=None):
    """Point the resident host at a stub, and return (host, rebuilds).

    A settings save asks the host to rebuild (ReloadMode.AGENT). In a test that
    must not build a real Waku: `build_from_environment` reads the developer's
    .env and would reach the network. Tests used to patch
    `browser_agent.rebuild`; the instance moved to waku/runtime/host.py, so the
    seam moved with it and this keeps the patch shape in one place.

    `rebuilds` fills as rebuild() is called, which is what a test asserting
    "this save must not rebuild" needs to read.
    """
    from waku.runtime import host as host_module

    if agent is None:
        agent = SimpleNamespace(tracer=SimpleNamespace(event=lambda *a, **k: None))
    rebuilds: list = []

    class _StubHost:
        def current(self):
            return agent

        def rebuild(self):
            rebuilds.append(True)
            return rebuild_error

    stub = _StubHost()
    monkeypatch.setattr(host_module, "live_host", lambda: stub)
    monkeypatch.setattr(host_module, "shared_host", lambda: stub)
    return stub, rebuilds
