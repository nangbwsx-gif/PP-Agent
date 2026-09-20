"""
一个 SQLite 文件（state.db）保存了 Waku 记住和做的一切。

这反映了白板上 Hermes 的做法：SQLite + FTS5，无需服务器。
你可以随时自己打开它：sqlite3 .waku/state.db '.tables'
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
-- 旗舰任务产物：日历工具创建的事件。确定性评估直接断言此表中的行
-- （“会议是否触发了？”）。

CREATE TABLE IF NOT EXISTS calendar_events (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    start TEXT NOT NULL,           -- ISO 8601
    "end" TEXT,
    attendees TEXT DEFAULT '',     -- 逗号分隔
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);

-- 语义记忆：关于你、你的人和你的项目的持久事实。
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    subject TEXT NOT NULL,         -- 事实关于谁/什么，例如 'alex'
    content TEXT NOT NULL,         -- 事实本身
    source TEXT DEFAULT 'user',    -- 'user'（直接告知）或 'consolidation'（整合）
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    subject, content, content=facts, content_rowid=id
);
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, subject, content) VALUES (new.id, new.subject, new.content);
END;
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, subject, content) VALUES ('delete', old.id, old.subject, old.content);
END;
CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, subject, content) VALUES ('delete', old.id, old.subject, old.content);
    INSERT INTO facts_fts(rowid, subject, content) VALUES (new.id, new.subject, new.content);
END;

-- 情景记忆：带日期的已发生事件（过去的聊天，经过提炼）。
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY,
    happened_at TEXT NOT NULL,     -- 情景发生的 ISO 8601 日期
    summary TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
    summary, content=episodes, content_rowid=id
);
CREATE TRIGGER IF NOT EXISTS episodes_ai AFTER INSERT ON episodes BEGIN
    INSERT INTO episodes_fts(rowid, summary) VALUES (new.id, new.summary);
END;
CREATE TRIGGER IF NOT EXISTS episodes_ad AFTER DELETE ON episodes BEGIN
    INSERT INTO episodes_fts(episodes_fts, rowid, summary) VALUES ('delete', old.id, old.summary);
END;

-- 原始聊天记录（“保存消息”框）。整合过程从这里读取。
-- session_id 为每一行标记它属于哪个对话，这样仪表板可以
-- 提供“新聊天”并在过去的会话之间切换（像聊天应用一样）。
-- 所有内容共享这一个表——会话只是一个标签。
CREATE TABLE IF NOT EXISTS chat_log (
    id INTEGER PRIMARY KEY,
    role TEXT NOT NULL,            -- 'user' | 'assistant'
    content TEXT NOT NULL,
    consolidated INTEGER DEFAULT 0,
    session_id TEXT DEFAULT 'default',
    created_at TEXT DEFAULT (datetime('now'))
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive, idempotent column upgrades for databases created before a
    column existed. SQLite has no 'ADD COLUMN IF NOT EXISTS', so we check."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(chat_log)").fetchall()}
    if "session_id" not in cols:
        conn.execute("ALTER TABLE chat_log ADD COLUMN session_id TEXT DEFAULT 'default'")
        conn.commit()
    if "source" not in cols:
        # which gateway a message came in through (cli / voice / dashboard)
        conn.execute("ALTER TABLE chat_log ADD COLUMN source TEXT DEFAULT 'cli'")
        conn.commit()
    if "meta" not in cols:
        # per-turn telemetry as JSON on the assistant row (gate decision,
        # latency, iterations, tools) — so reopening a thread still shows how
        # each answer was produced, not just the plain text.
        conn.execute("ALTER TABLE chat_log ADD COLUMN meta TEXT")
        conn.commit()


def connect(home: Path, check_same_thread: bool = True) -> sqlite3.Connection:
    # 连接并初始化”项目的 SQLite 数据库
    conn = sqlite3.connect(home / "state.db", check_same_thread=check_same_thread)  # 打开/创建数据库文件
    conn.row_factory = sqlite3.Row                                                  # 设置连接行为
    conn.execute("PRAGMA busy_timeout=3000")                                        # 设置连接行为
    conn.executescript(SCHEMA)                                                      # 建表
    _migrate(conn)                                                                  # 迁移旧数据库
    return conn                                                                     # 返回连接对象
