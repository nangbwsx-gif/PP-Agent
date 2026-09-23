"""Entrypoints — installed as the `waku` command (and `python -m waku`):

  waku                       chat in the terminal (default)
  waku serve                 the resident process: one Waku, the dashboard as its
                             first gateway  (--zh / --dev as below)
  waku dashboard             the browser cockpit → localhost:7777
  waku dashboard --zh        同上，但界面是中文（--zh: Chinese interface）
  waku dashboard --dev       同上，但显示开发者页面（图谱、运维、数据库…）
  waku connections           list configured integrations and their health
  waku connect google        sign in to Google Calendar (opens your browser)
  waku connect waku-memory   one memory shared with your other agents (opens your browser)
  waku mcp                   MCP servers, and which account each knows you as
  waku mcp login <name>      sign in again — as someone else, or after expiry
  waku voice                 talk to it (needs the [voice] extra)
  waku brief                 morning briefing (calendar + memory) — as a LOOP
  waku gather                same job as a GRAPH: github, web, calendar and
                             memory fetched together, then one digest
  waku skill install <url>   install a community skill
  waku skill export          copy Waku's skills to Claude Code / Codex (--to claude,codex)
"""

from __future__ import annotations

import os
import sys


def _tolerant_stdio() -> None:
    """Windows consoles default to a legacy codepage (cp1252) that cannot
    encode the arrows and middots in our output — printing the dashboard
    banner would crash with UnicodeEncodeError before the server even
    started. Keep the console's encoding but replace what it can't show."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass  # not a real console stream (tests, pipes) — leave it alone


def main() -> None:
    _tolerant_stdio()
    args = sys.argv[1:]
    if not args:
        from waku.gateway.cli import main as cli_main

        cli_main()
    elif args[0] == "serve":
        # 常驻进程（docs/resident-host-design.md 的 phase 1）：Host 持有唯一的
        # Waku，dashboard 是它的第一个 gateway。现阶段还没有第二个 gateway，
        # 所以它跑的就是那个 dashboard —— `serve` 是以后挂更多 gateway 的地方。
        if "--zh" in args[1:]:
            os.environ["WAKU_LANG"] = "zh"
        if "--dev" in args[1:]:
            os.environ["WAKU_DEV"] = "1"
        from waku.ops.dashboard import main as serve_main

        serve_main()
    elif args[0] == "dashboard":
        # `waku dashboard --zh` → 中文界面；`--dev` → 显示开发者页面。
        # 两个开关都在这里定死，之后一路不再变。
        if "--zh" in args[1:]:
            os.environ["WAKU_LANG"] = "zh"
        if "--dev" in args[1:]:
            os.environ["WAKU_DEV"] = "1"
        from waku.ops.dashboard import main as dash_main

        dash_main()
    elif args[0] == "connections":
        from waku.integrations import cli_main

        sys.exit(cli_main())
    elif args[0] == "connect":
        from waku.connect import cli_main as connect_main

        sys.exit(connect_main(args[1:]))
    elif args[0] == "voice":
        from waku.gateway.voice import main as voice_main

        voice_main()
    elif args[0] == "brief":
        from waku.ops.brief import main as brief_main

        brief_main()
    elif args[0] == "gather":
        from waku.ops.gather import main as gather_main

        gather_main()
    elif args[0] == "mcp":
        from waku.tools.mcp_cli import cli_main as mcp_main

        sys.exit(mcp_main())
    elif args[0] == "skill" and len(args) >= 2 and args[1] == "export":
        from waku.memory.procedural.exporter import cli_main as export_main

        sys.exit(export_main(args[2:]))
    elif args[0] == "skill" and len(args) >= 3 and args[1] == "install":
        from waku.memory.procedural.installer import install

        install(args[2])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
