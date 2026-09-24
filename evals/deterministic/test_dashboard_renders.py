"""DETERMINISTIC EVAL — every dashboard view actually renders.

`node --check` proves syntax and `test_static_js_parses.py` proves no local
variable shadows the t() translator. Neither can see the third class of bug,
which is the one that actually happened twice on 2026-09-19: the view function
runs, throws, and the page comes back blank while the server is perfectly
healthy and the console is the only place that says so.

    Tools      `const t = d.tools` shadowed the translator  -> TypeError
    Database   `const t = tables.find(...)` ditto            -> TypeError
    Connections CONNECTION_GROUPS got translated, so the group names no longer
                matched the keys connectionDisplayGroup returns -> undefined.push
    Tools/MCP  a rename left `toolsMCP(t)` pointing at the translator

All four are valid JavaScript. The only way to catch them is to call the render
function with data shaped like the real /api/data payload, which is what the
node script in evals/fixtures/ does. It runs offline against a fixture, so it
stays deterministic and needs no server.

Skips without node, like the parse check next door.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "evals" / "fixtures" / "dashboard_render_smoke.js"
NODE = shutil.which("node")

# Shaped like the real /api/data response, and deliberately non-empty in every
# field a view branches on: a fixture with empty lists would skip the code that
# broke. Values are meaningless; only their presence matters.
FIXTURE = {
    "home": "/tmp/.waku",
    "current_session": "s-1",
    "stats": {"turns": 3, "tool_calls": 2, "tool_errors": 0, "latency_avg": 1200,
              "gate_skip": 1, "gate_retrieve": 2, "trace_files": 2},
    "usage": {"calls": 5, "total_in": 1000, "total_out": 500, "total_cost": 0.0123,
              "by_day": [{"date": "2026-09-19", "calls": 5, "in": 1000, "out": 500, "cost": 0.0123}],
              "by_provider": [{"provider": "deepseek", "calls": 5, "in": 1000, "out": 500, "cost": 0.0123}]},
    "facts": [{"id": 1, "subject": "用户", "content": "住在杭州", "source": "user",
               "created_at": "2026-09-19 10:00:00"}],
    "episodes": [{"id": "e1", "happened_at": "2026-09-19", "summary": "聊了住处"}],
    "skills": [{"name": "weekly-brief", "description": "d", "body": "b",
                "path": "/p/SKILL.md", "rel": "skills/weekly-brief", "editable": True}],
    "soul": "You are 鬼懂哥.",
    "calendar": [{"title": "咖啡", "start": "2026-09-20 09:00", "end": "2026-09-20 09:30",
                  "attendees": ""}],
    "outbox": [{"name": "msg-1.txt", "text": "hi"}],
    "chat_log": [{"role": "user", "content": "hi"}],
    "turns": [{"user_message": "你好", "reply": "你好", "iterations": 1, "latency_ms": 900,
               "cost": 0.001, "gate": {"decision": "retrieve", "reason": "问了住处"},
               "graph": {"route": "full", "reason": "真任务"},
               "tools": [{"tool": "save_note", "status": "ok"}]}],
    "sessions": [{"id": "s-1", "title": "第一个对话", "last": "刚刚", "source": "cli"}],
    "tools": {"catalog": [
                  {"name": "create_event", "description": "d", "source": "flagship"},
                  {"name": "search_web", "description": "d", "source": "web"},
                  {"name": "manage_memory", "description": "d", "source": "self-management"},
                  {"name": "fs_read", "description": "d", "source": "mcp"},
                  {"name": "odd", "description": "d", "source": "other"}],
              "mcp": {"configured": True, "servers": ["fs"], "live": False},
              "planned": [{"name": "browse", "box": "tools", "description": "d"}]},
    "db": {"tables": [{"name": "chat_log", "count": 2, "columns": ["id", "content"],
                       "types": {"id": "INTEGER"}, "sample": [{"id": 1, "content": "hi"}]},
                      {"name": "facts", "count": 1, "columns": ["id"], "types": {},
                       "sample": [{"id": 1}]}],
           "all_tables": ["chat_log", "facts"], "fts": ["facts_fts"],
           "size": 53248, "path": "/tmp/.waku/state.db"},
    "connections": [{"key": "notion", "group": "Memory & Storage", "name": "Notion",
                     "what": "w", "status": {"state": "not_configured", "message": ""},
                     "fields": [], "install_command": "", "setup_url": ""},
                    {"key": "tavily", "group": "Search & Observability", "name": "Tavily",
                     "what": "w", "status": {"state": "connected", "message": "ok"},
                     "fields": [], "install_command": "", "setup_url": ""},
                    # The Channels member. Its card renders two extra buttons and a
                    # detail line, so it needs to be in here or that code is never
                    # executed by the only test that actually calls the view.
                    {"key": "wechat", "group": "Channels", "name": "WeChat",
                     "what": "w", "status": {"state": "installed_but_unconfigured",
                                             "message": "not logged in — press Scan to log in"},
                     "fields": [], "install_command": "pip install -e '.[wechat]'",
                     "setup_url": ""}],
    # Every branch of the card's detail line, plus a pending delivery, so
    # wechatCardDetail() is exercised rather than skipped for lack of data.
    "wechat": {"phase": "polling", "accountId": "bot@im.bot", "userId": "me@im.wechat",
               "allowed": ["me@im.wechat"], "refused": 1, "refusedSenders": ["stranger"],
               "noId": 2, "handled": 3, "failed": 0, "lastError": "",
               "unfinished": ["m-interrupted"],
               "pending": [{"messageId": "m1", "attempts": 1, "sentChunks": 0,
                            "totalChunks": 1, "lastError": "boom"}]},
    "providers": [{"key": "deepseek", "fields": []}],
    "settings": {"provider": "deepseek", "model": "deepseek-v4-pro",
                 "small_model": "deepseek-flash", "experimental": False,
                 "graph_workflows": True},
    "graph": {"enabled": True, "stats": {"quick": 2, "full": 1},
              "workflows": [{"name": "triage"}, {"name": "gather"}]},
    "chat_pending": 3,
    "consolidate_every": 6,
    "episodes_source": "sqlite",
    "trace_tail": [{"type": "llm", "detail": "d", "ts": "2026-09-19T10:00:00"}],
    "trace_errors": [], "trace_file": "2026-09-19.jsonl", "wake_scans": [],
    "eval_report": {"deterministic": "pass", "judge": "pass", "ran_at": "2026-09-19"},
    "eval_history": [{"ran_at": "2026-09-19T10:00:00", "deterministic": "pass",
                      "judge": "pass", "suites": {"deterministic": {"passed": 1, "failed": 0}}}],
}


@pytest.mark.skipif(NODE is None, reason="node not installed")
@pytest.mark.parametrize("lang", ["en", "zh"])
def test_every_dashboard_view_renders(tmp_path, lang):
    """Both languages, because they are not one code path.

    Chinese goes through the overlay in i18n.js; English is the original path
    with the layer switched off entirely. A view that only breaks in one of them
    is a blank page in someone's browser, so rendering a single language proves
    half of what this file exists to prove.
    """
    data = tmp_path / "api_data.json"
    data.write_text(json.dumps(FIXTURE, ensure_ascii=False), encoding="utf-8")
    result = subprocess.run(  # noqa: S603 — fixed argv, paths from this file
        [NODE, str(SCRIPT), str(data), lang],
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert result.returncode == 0, (
        f"a dashboard view throws while rendering in '{lang}' — that page would "
        "be blank in the browser, with the server reporting nothing wrong.\n"
        f"{result.stdout}\n{result.stderr}"
    )
