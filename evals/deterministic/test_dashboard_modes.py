"""DETERMINISTIC EVAL — the personal-user mode vs the developer mode.

Two failures here are invisible until someone complains:

1. A page is added to the sidebar and never classified. It either leaks into the
   personal-user sidebar, or it becomes unreachable in developer mode. Neither
   shows up as a broken page, so nothing else in this suite would notice.
2. `--dev` stops injecting its one script tag, and the flag quietly does
   nothing while the mode switch in the UI still works — so the person who
   starts the server never sees their pages.

The precedence rule is checked too (the switch in the interface beats the
command-line flag). Getting it backwards means a user turns developer mode off
and it comes back on at the next restart.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "waku" / "ops" / "static"
INDEX = STATIC / "index.html"
MODE_JS = STATIC / "js" / "mode.js"
SMOKE = ROOT / "evals" / "fixtures" / "dashboard_render_smoke.js"
NODE = shutil.which("node")

# 个人用户看到的页面。隐藏的其余页面定义在 mode.js 的 DEV_PAGES 里。
#
# 这是一份刻意写死的清单：改这里等于改产品的对外形态，应该是一次有意识的
# 决定，而不是顺手加个页面时的副作用。
PERSONAL_PAGES = {"overview", "memory", "tools", "settings"}


def _nav_pages() -> set[str]:
    return set(re.findall(r'<a href="#[^"]*" data-v="([^"]+)"', INDEX.read_text(encoding="utf-8")))


def _dev_pages() -> set[str]:
    block = re.search(r"const DEV_PAGES = \[(.*?)\];", MODE_JS.read_text(encoding="utf-8"),
                      re.DOTALL)
    assert block, "mode.js no longer declares DEV_PAGES"
    return set(re.findall(r'"([a-z]+)"', block.group(1)))


def test_every_sidebar_page_is_classified():
    """每一个导航项要么在个人用户清单里，要么在开发者清单里，不能两边都不在。"""
    nav = _nav_pages()
    assert nav, "读不到任何导航项 —— index.html 的侧边栏结构变了？"
    assert nav == PERSONAL_PAGES | _dev_pages(), (
        "有导航项没被归类。漏掉的："
        f"{sorted(nav - PERSONAL_PAGES - _dev_pages())}；"
        f"清单里有但侧边栏没有的：{sorted(PERSONAL_PAGES | _dev_pages() - nav)}"
    )


def test_the_two_lists_do_not_overlap():
    assert not (PERSONAL_PAGES & _dev_pages()), (
        "同一个页面同时个人可见又开发者专用 —— 它到底属于哪边？"
    )


def test_dev_injection_is_opt_in_and_adds_only_one_tag(monkeypatch):
    """--dev 只多一个脚本标签；不带它时 HTML 里不该出现任何 WAKU_DEV。"""
    from waku.ops import dashboard

    monkeypatch.delenv("WAKU_DEV", raising=False)
    monkeypatch.setenv("WAKU_LANG", "en")
    plain = dashboard.index_html().decode("utf-8")
    assert "WAKU_DEV" not in plain

    monkeypatch.setenv("WAKU_DEV", "1")
    dev = dashboard.index_html().decode("utf-8")
    assert 'window.WAKU_DEV=true' in dev
    assert dev.replace(dashboard.DEV_HEAD, "") == plain, (
        "开发者模式改了注入的那一行以外的东西 —— 它应该只多一个 script 标签"
    )


@pytest.mark.skipif(NODE is None, reason="node not installed")
@pytest.mark.parametrize("flag,stored,expected", [
    (None, None, "false"),   # 默认：个人用户模式
    ("1", None, "true"),     # --dev
    (None, "1", "true"),     # 界面上打开过
    (None, "0", "false"),    # 界面上关掉过
    ("1", "0", "false"),     # 界面上关掉 > 启动参数（关掉之后重启不该又开回来）
])
def test_mode_precedence(tmp_path, flag, stored, expected):
    """localStorage 的选择压过 --dev，--dev 压过默认。"""
    data = tmp_path / "api_data.json"
    data.write_text(json.dumps({"tools": {}, "turns": [], "facts": [], "episodes": [],
                                "skills": [], "db": {}, "stats": {}, "usage": {}}),
                    encoding="utf-8")
    env = dict(os.environ)
    env.pop("SMOKE_DEV_FLAG", None)
    env.pop("SMOKE_DEV_STORED", None)
    if flag is not None:
        env["SMOKE_DEV_FLAG"] = flag
    if stored is not None:
        env["SMOKE_DEV_STORED"] = stored

    result = subprocess.run(  # noqa: S603 — fixed argv, paths from this file
        [NODE, str(SMOKE), str(data)],
        capture_output=True, text=True, timeout=120, check=False, env=env,
    )
    match = re.search(r"^dev mode: (true|false)$", result.stdout, re.MULTILINE)
    assert match, f"冒烟脚本没报出模式：\n{result.stdout}\n{result.stderr}"
    assert match.group(1) == expected
