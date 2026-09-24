import sys
from pathlib import Path

import pytest

# evals/ sits next to waku/, not inside it — make both importable when
# running `pytest evals` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _never_touch_the_developers_env(tmp_path, monkeypatch):
    """Point the .env writer at a throwaway file, for every test.

    `waku/integrations.py::_env_path` is `dotenv.find_dotenv(usecwd=True)`, which
    walks UP from the cwd until it finds a `.env`. pytest's `tmp_path` lives under
    the user's temp directory, so a test that chdir'd into something it believed
    was throwaway still resolved to `C:\\Users\\<name>\\.env` and wrote there. That
    is where the stray WAKU_APPLE_* keys in a real home directory came from, and a
    test for the WeChat card added two more before this existed.

    Six test files chdir for isolation, and not one of them can notice: the write
    succeeds, and it lands outside the repo where nobody looks. Fixing it here
    means a seventh cannot reintroduce it.

    Also clears integrations' two gateway hooks. They are module globals, so a test
    that registers a reloader would otherwise change what the next test's save path
    does — passing alone and failing in a suite, which is the worst kind.
    """
    from waku import integrations

    monkeypatch.setattr(integrations, "_env_path", lambda: tmp_path / ".env")
    monkeypatch.setattr(integrations, "_gateway_status_provider", None)
    monkeypatch.setattr(integrations, "_gateway_reloader", None)
