"""send_message — drafts a message into a local outbox.

Local-first: nothing is actually sent. Each message becomes a file in
.waku/outbox/ that you can read, edit, and send yourself. Wiring a real
channel (email, Slack, WeChat) is a great community contribution.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from waku.tools.registry import Tool


def make_tool(home: Path) -> Tool:
    def send_message(to: str, body: str) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        safe_to = "".join(c if c.isalnum() else "-" for c in to)[:40]
        path = home / "outbox" / f"{stamp}-{safe_to}.txt"
        path.write_text(f"To: {to}\n\n{body}\n", encoding="utf-8")
        return f"Message to {to} placed in outbox ({path.name}). Nothing was sent — review it there."

    return Tool(
        name="send_message",
        description=(
            "起草一条发给某人的消息，放进本地发件箱，等用户过目后自己发送。"
            "当用户让你给某人捎话、转告或提醒时用它。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient name or address"},
                "body": {"type": "string", "description": "The message text"},
            },
            "required": ["to", "body"],
        },
        fn=send_message,
    )
