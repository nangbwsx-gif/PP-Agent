"""Episodic memory — dated events: what happened, and when.

Semantic memory answers "what do I know?"; episodic answers "what happened
last Tuesday?". Same SQLite file, but every row carries a date and retrieval
blends relevance (FTS rank) with recency — the whiteboard's "RAG for
relevance + SQL for recency".
"""

from __future__ import annotations

import sqlite3

from waku.memory.semantic.store import _cjk_grams, _fts_query, _scan_substring


class SqliteEpisodeStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add(self, summary: str, happened_at: str) -> None:
        self.conn.execute(
            "INSERT INTO episodes (happened_at, summary) VALUES (?,?)",
            (happened_at, summary),
        )
        self.conn.commit()

    def search(self, query: str, top_k: int = 3) -> list[str]:
        """Relevance first (FTS), most recent first among matches."""
        fts = _fts_query(query)
        # 真正搜不了的查询（只有标点）仍然读作“给我最近的”。
        if not fts and not _cjk_grams(query):
            return self.recent(top_k)
        rows = []
        if fts:
            rows = self.conn.execute(
                "SELECT e.id, e.happened_at, e.summary FROM episodes_fts JOIN episodes e "
                "ON e.id = episodes_fts.rowid WHERE episodes_fts MATCH ? "
                "ORDER BY rank, e.happened_at DESC LIMIT ?",
                (fts, top_k),
            ).fetchall()
        if _cjk_grams(query):
            # 中文在索引里是一整串一个 token，MATCH 只能命中开头（见 _cjk_grams），
            # 所以这里总是补扫一层子串。补不到就是空 —— **绝不能**退回 recent()，
            # 那等于把别人的记忆当成相关记忆递给模型。
            seen = {row["id"] for row in rows}
            rows += [
                row
                for row in _scan_substring(
                    self.conn,
                    "SELECT id, happened_at, summary FROM episodes",
                    query,
                    top_k,
                    lambda row: row["summary"],
                )
                if row["id"] not in seen
            ]
        return [f"({r['happened_at']}) {r['summary']}" for r in rows[:top_k]]

    def recent(self, top_k: int = 3) -> list[str]:
        rows = self.conn.execute(
            "SELECT happened_at, summary FROM episodes ORDER BY happened_at DESC LIMIT ?",
            (top_k,),
        ).fetchall()
        return [f"({r['happened_at']}) {r['summary']}" for r in rows]

    def list(self, limit: int = 200) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, happened_at, summary, created_at FROM episodes ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete(self, episode_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM episodes WHERE id=?", (episode_id,))
        self.conn.commit()
        return cur.rowcount > 0
