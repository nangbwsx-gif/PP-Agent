"""Semantic memory — durable facts, keyword-searched with SQLite FTS5.

The Hermes insight from the whiteboard: "keyword top-k, no embedding". For a
single user's facts, ranked keyword search (BM25) is fast, fully local, and —
crucially for teaching — you can read the whole index with sqlite3.
Want a hosted store instead? Set WAKU_SEMANTIC_STORE=mem0 or zep.
"""

from __future__ import annotations

import re
import sqlite3

# The scripts unicode61 does not segment. It counts these as alphanumeric but
# puts no word boundary between them, so a whole run — 「阿历克斯喜欢游泳」 — is
# indexed as ONE term. An exact match on a name inside that run can therefore
# never hit, which is why these tokens, and only these, are searched as
# prefixes. Turning *every* token into `token*` would quietly make "car" match
# "carpet": a worse failure than this one, because it still looks like it works.
_UNSEGMENTED = re.compile(
    "["
    "\u3040-\u309f"  # Hiragana
    "\u30a0-\u30ff"  # Katakana
    "\u3400-\u4dbf"  # CJK Unified Ideographs Extension A
    "\u4e00-\u9fff"  # CJK Unified Ideographs
    "\uf900-\ufaff"  # CJK Compatibility Ideographs
    "]"
)
# 同一条字符集，这次用来把一串不切分的字符**整段抓出来**（不是单个字符）。
# 从上面派生，字面量只写一份 —— 两处万一不一致，兜底检索就会漏掉某个脚本。
_UNSEGMENTED_RUN = re.compile(_UNSEGMENTED.pattern + "+")


def _cjk_grams(text: str) -> set[str]:
    """把查询里那些脚本的连续段切成 2-gram。

    为什么需要：前缀通配符只救得了「词在整串开头」的情形。真实查询问的几乎都是
    中段词 —— 「用户是一名计算机技术专业的学生」里的「计算机」用 MATCH 永远命中
    不了。没有分词器也能解决：任意一个两字以上的词，至少有一个 2-gram 落在原文里。

    代价是「什么」这类高频双字容易过度召回，所以它只是 FTS 的第二把网，不是主路。
    """
    grams: set[str] = set()
    for run in _UNSEGMENTED_RUN.findall(text):
        if len(run) == 1:
            grams.add(run)
        else:
            grams.update(run[i : i + 2] for i in range(len(run) - 1))
    return grams


def _scan_substring(conn, sql, query, top_k, text_of):
    """FTS 之外的第二把网：子串匹配，按命中的 2-gram 数排序（一个迷你 BM25）。

    只在 MATCH 一个都没命中时才跑。个人规模（几百条事实）的全表扫描可以忽略，
    所以没有索引、也不需要索引 —— 这正是不用换 tokenizer 的原因：换 trigram 要
    重建索引，而仓库没有迁移机制，真实的 state.db 只能手写脚本改。

    ponytail: 全表扫描。上万条事实时应该换成带 ngram 的 FTS 表，而不是调优这里。
    """
    grams = _cjk_grams(query)
    if not grams:
        return []
    scored = []
    for row in conn.execute(sql):
        text = text_of(row)
        hits = sum(1 for gram in grams if gram in text)
        if hits:
            scored.append((hits, row))
    scored.sort(key=lambda pair: -pair[0])
    return [row for _, row in scored[:top_k]]


def _fts_query(text: str) -> str:
    r"""User text isn't a valid FTS5 query (quotes/punctuation break MATCH).
    Reduce it to `word OR word OR ...` over alphanumeric tokens.

    "Alphanumeric" has to mean what the *index* means by it. facts_fts and
    episodes_fts declare no tokenizer, so they get FTS5's default — unicode61,
    which keeps every Unicode alphanumeric and folds diacritics ("München" is
    stored as `munchen`). An ASCII-only `[a-zA-Z0-9]` disagreed with that
    index, and the index was not the side that was wrong:

      * accented Latin was truncated to a fragment — "Müller" became `ller`,
        which matches nothing, so German, French, Spanish, Portuguese, Turkish
        and Vietnamese users got silently wrong answers;
      * every non-Latin script reduced to "" — and an empty query is not a
        no-op. SqliteEpisodeStore.search() reads it as "just give me the recent
        ones", so a user asking about Сергей got an unrelated English episode
        handed to the model under the heading "Relevant memory". Waku wasn't
        skipping memory for those users, it was confidently supplying someone
        else's.

    `[^\W_]` is the same set unicode61 keeps. Underscore is excluded because
    unicode61 treats it as a separator, so `waku_agent` is two terms in the
    index and has to be two terms here too, or it matches nothing.

    Everything stays lowercased, and that is load-bearing beyond tidiness:
    FTS5's AND/OR/NOT/NEAR are operators only in UPPERCASE, so lowercasing is
    what stops a user's "Alex and Bob" from parsing as a boolean expression.
    """
    words = re.findall(r"[^\W_]{2,}", text.lower())
    if not words:
        return ""
    return " OR ".join(
        f"{w}*" if _UNSEGMENTED.search(w) else w for w in dict.fromkeys(words)
    )


class SqliteFactStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add(self, subject: str, content: str, source: str = "user") -> None:
        self.conn.execute(
            "INSERT INTO facts (subject, content, source) VALUES (?,?,?)",
            (subject.lower().strip(), content, source),
        )
        self.conn.commit()

    def _match(self, query: str, top_k: int) -> list[sqlite3.Row]:
        """FTS 排在前；查询含不切分脚本时，再补一层子串命中。

        为什么不只在 FTS 空的时候才兜底：对中文这类没有词边界的脚本，MATCH
        **结构上就不可靠**——它只能命中整串的开头，所以"命中了"不代表答完了。
        （实测：「专业」能搜到，但若另有一条以「专业」开头的事实，它就会把中段
        那条挡在外面。）

        补进来的排在 FTS 命中之后，于是纯 ASCII / 拉丁查询的结果与以前逐字节相同。
        """
        rows: list[sqlite3.Row] = []
        fts = _fts_query(query)
        if fts:
            rows = self.conn.execute(
                "SELECT f.id, f.subject, f.content FROM facts_fts JOIN facts f "
                "ON f.id = facts_fts.rowid WHERE facts_fts MATCH ? ORDER BY rank LIMIT ?",
                (fts, top_k),
            ).fetchall()
        if _cjk_grams(query):
            seen = {row["id"] for row in rows}
            rows += [
                row
                for row in _scan_substring(
                    self.conn,
                    "SELECT id, subject, content FROM facts",
                    query,
                    top_k,
                    lambda row: f"{row['subject']} {row['content']}",
                )
                if row["id"] not in seen
            ]
        return rows[:top_k]

    def search(self, query: str, top_k: int = 4) -> list[str]:
        # 搜不到的词就是搜不到，不能变成“什么都没有”。
        if not _fts_query(query) and not _cjk_grams(query):
            return []
        return [f"[{r['subject']}] {r['content']}" for r in self._match(query, top_k)]

    # --- CRUD: humans (dashboard) and the agent (manage_memory tool) edit memory.
    # The facts_au / facts_ad triggers keep the FTS index in sync automatically.
    def list(self, limit: int = 200) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, subject, content, source, created_at FROM facts ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def search_with_ids(self, query: str, top_k: int = 8) -> list[dict]:
        # 空查询在这里仍读作“把最近的列出来”（dashboard 的记忆页和 manage_memory
        # 工具靠它）—— 那是与 search() 不同的、有意保留的行为。
        if not _fts_query(query) and not _cjk_grams(query):
            return self.list(top_k)
        return [dict(r) for r in self._match(query, top_k)]

    def update(self, fact_id: int, content: str, subject: str | None = None) -> bool:
        if subject is None:
            cur = self.conn.execute("UPDATE facts SET content=? WHERE id=?", (content, fact_id))
        else:
            cur = self.conn.execute(
                "UPDATE facts SET content=?, subject=? WHERE id=?",
                (content, subject.lower().strip(), fact_id),
            )
        self.conn.commit()
        return cur.rowcount > 0

    def delete(self, fact_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM facts WHERE id=?", (fact_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def settle(self, timeout: float = 120.0) -> bool:
        """Already settled. The row and its FTS5 index land in one transaction,
        so a fact is searchable the instant add() returns. The hosted backends
        have to work for this."""
        return True
