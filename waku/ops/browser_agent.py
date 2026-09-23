"""dashboard 这个 gateway 的会话策略 —— Host 把 Waku 搬走之后剩下的那一半。

常驻 Host 现在持有唯一的 Waku（见 waku/runtime/host.py），并把每一轮请求
`switch` 到调用方指定的 session。所以这个文件不再需要持有 agent：单例、
`agent_lock`、`get_agent`、`rebuild`、`current` 都搬到 Host 去了。

留下来的全是 dashboard **自己**的会话策略，而策略本来也不该由 Host 管 ——
Host 不知道也不该猜"dashboard 这条线"是什么意思：

  dash_session          本进程当前那条 dashboard 线
  resume_or_new_session 重启后接着上一条 dashboard 线，而不是永远开新的
                        （否则重启一次、刷新一次，"聊天记录就没了"）
  rotate_if_idle        离开一段时间回来该开一条新线，旧的那条留在 History
  current_session       下一条消息该落在哪条线上（上面两者的合成）
  set_current_session   「新聊天」和「切换」改的就是这个指针

为什么指针还留在模块级全局：三个函数都要改它，而模块全局只有拥有它的模块
能重新绑定 —— 在别人的文件里写 `global` 正是一种让两个写者互相打架的方式。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from waku.config import load_settings
from waku.db import connect

_dashboard_session = None  # 本进程当前的 dashboard 线（带日期；跨刷页稳定）


def dash_session() -> str:
    """本进程新消息所属的那条线。每进程只解析一次：RESUME 最近的 dashboard 线
    （这样重启后屏幕上还是那段对话），没有就开一条新的带日期的。绝不退回那条
    永生的 'default'。"""
    global _dashboard_session
    if _dashboard_session is None:
        try:
            conn = connect(load_settings().home)
            _dashboard_session = resume_or_new_session(conn)
            conn.close()
        except Exception:
            _dashboard_session = datetime.now().strftime("dashboard-%Y%m%d-%H%M%S")
    return _dashboard_session


def set_current_session(session_id: str) -> None:
    """换掉本进程的 dashboard 线。「新聊天」和「切换」走这里 —— 不用碰 agent，
    因为 Host 每次请求都会 switch 到这条线上。"""
    global _dashboard_session
    _dashboard_session = session_id


def resume_or_new_session(conn) -> str:
    """挑本进程这条线：最近那条 dashboard 线如果还新鲜（在空闲窗口内）就 RESUME，
    否则开一条新的带日期的。没有这一步，每次重启服务都会开一条空的新线，屏幕上的
    对话就"不见了"（其实只是停在旧 id 下）。空闲久了仍会轮换 —— 那是
    `rotate_if_idle` 运行中的职责。"""
    idle_min = int(os.getenv("WAKU_SESSION_IDLE_MINUTES", "60"))
    # 按 source 匹配，而不是按 id 前缀："+ New chat" 造出来的 id 是 's-...'，
    # 所以用 'dashboard-%' 过滤会在重启时把这些线程变成孤儿。dashboard 的每条
    # 消息都带 source='dashboard' —— 那才是可靠的信号。
    row = conn.execute(
        "SELECT session_id, MAX(created_at) AS last_at FROM chat_log "
        "WHERE source='dashboard' GROUP BY session_id "
        "ORDER BY last_at DESC LIMIT 1"
    ).fetchone()
    if row and row["last_at"]:
        try:
            last = datetime.strptime(row["last_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
            if idle_min <= 0 or (datetime.now(UTC) - last).total_seconds() <= idle_min * 60:
                return row["session_id"]
        except ValueError:
            pass
    return datetime.now().strftime("dashboard-%Y%m%d-%H%M%S")


def rotate_if_idle(conn, session_id: str) -> str:
    """这条线上最新一条消息比 WAKU_SESSION_IDLE_MINUTES 还旧，就换一条新的带日期的线。
    旧的那条留在 History 里一点就能回去。实际 bug：一个测试者隔了几天回来，新消息
    落进了一条一周前的 32 条会话里。"""
    idle_min = int(os.getenv("WAKU_SESSION_IDLE_MINUTES", "60"))
    if idle_min <= 0:
        return session_id
    row = conn.execute("SELECT MAX(created_at) FROM chat_log WHERE session_id=?",
                       (session_id,)).fetchone()
    if not row or not row[0]:
        return session_id
    try:  # sqlite 的 datetime('now') 是 UTC 的 "YYYY-MM-DD HH:MM:SS"
        last = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return session_id
    if (datetime.now(UTC) - last).total_seconds() > idle_min * 60:
        return datetime.now().strftime("dashboard-%Y%m%d-%H%M%S")
    return session_id


def current_session() -> str:
    """下一条消息该落在哪条线上：本进程当前那条，或它已经静默太久之后的新一条。

    这里自己开一条短连接做判断，因为"该不该轮换"是个纯粹的读操作，不该逼一个
    还没建起来的 agent 先建起来。"""
    session_id = dash_session()
    conn = None
    try:
        conn = connect(load_settings().home)
        rotated = rotate_if_idle(conn, session_id)
    except Exception:
        return session_id
    finally:
        if conn is not None:
            conn.close()
    if rotated != session_id:
        set_current_session(rotated)
    return rotated
