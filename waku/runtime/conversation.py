"""一个进程的对话线程 —— 谁跟它说话都落在同一条线上。

为什么它在 runtime/ 下、也不叫 browser_agent：这条策略**不是浏览器的**。两个
gateway 都用它：

    浏览器   host.ask(source="dashboard", session_id=session_id_for_turn())
    微信     host.ask(source="wechat",    session_id=session_id_for_turn())

于是"一个助手、多个门"是字面意义上的同一条对话：你在微信说的话，回到浏览器问
"我刚才说过什么"，它在 12 轮滑窗之内就能读到（更早的靠情节记忆和整合）。

`source` 仍然逐行写进 `chat_log`，所以 History 里**看得出来**每句话是从哪个门进来的
—— 共用一条线不等于丢掉来源，这也是它能同时显示两个 channel 的原因。

（这里曾经是按 source 分会话的：微信和浏览器各一条线，互不可见。那套隔离是为
多用户防串线设计的，而这个部署是一个人一个账号，跨门连续才是他要的。改的是策略，
机制没动 —— host 依然按请求带的 session_id 绑定，一个请求也依然只属于一条线。）

指针为什么留在模块级全局：三个函数都要改它，而模块全局只有拥有它的模块能重新绑定。
在别人的文件里写 `global` 正是两个写者互相打架的方式。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from waku.config import load_settings
from waku.db import connect

# 本进程当前的对话线。带日期，跨刷页稳定；"新聊天"和"切换"改的就是它。
_thread_id: str | None = None


def thread_id() -> str:
    """本进程当前那条线，不施加轮换。给"显示现在是哪一条"用。

    每进程只解析一次：RESUME 最近一条线（这样重启后屏幕上还是那段对话），没有
    就开一条新的带日期的。**绝不退回那条永生的 'default'** —— 一个回来的用户该看到
    自己的对话，不是他一辈子的无限滚动。
    """
    global _thread_id
    if _thread_id is None:
        try:
            conn = connect(load_settings().home)
            _thread_id = resume_or_new_session(conn)
            conn.close()
        except Exception:
            _thread_id = datetime.now().strftime("chat-%Y%m%d-%H%M%S")
    return _thread_id


def set_thread_id(session_id: str) -> None:
    """换掉本进程的对话线。「新聊天」和「切换」走这里 —— 不用碰 agent，因为 host
    每次请求都会 switch 到这条线上。"""
    global _thread_id
    _thread_id = session_id


def resume_or_new_session(conn) -> str:
    """挑本进程这条线：**最近一条线**如果还新鲜（在空闲窗口内）就 RESUME，否则开一条
    新的带日期的。没有这一步，每次重启服务都会开一条空的新线，屏幕上的对话就
    "不见了"（其实只是停在旧 id 下）。

    按时间挑，**不按 source 过滤**：微信和浏览器共用一条线，所以一条只有微信消息
    的线也应当被恢复 —— 早先按 source='dashboard' 过滤，那会在"只用微信聊过"之后
    每次重启都新开一条，把对话切碎。
    """
    idle_min = int(os.getenv("WAKU_SESSION_IDLE_MINUTES", "60"))
    row = conn.execute(
        "SELECT session_id, MAX(created_at) AS last_at FROM chat_log "
        "GROUP BY session_id ORDER BY last_at DESC LIMIT 1"
    ).fetchone()
    if row and row["last_at"]:
        try:
            last = datetime.strptime(row["last_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
            if idle_min <= 0 or (datetime.now(UTC) - last).total_seconds() <= idle_min * 60:
                return row["session_id"]
        except ValueError:
            pass
    return datetime.now().strftime("chat-%Y%m%d-%H%M%S")


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
        return datetime.now().strftime("chat-%Y%m%d-%H%M%S")
    return session_id


def session_id_for_turn() -> str:
    """下一轮该落在哪条线上：本进程当前那条，或它已经静默太久之后的新一条。

    两个 gateway 都调它，所以"下次说什么"对两个门是同一个答案。

    这里自己开一条短连接做判断，因为"该不该轮换"是个纯粹的读操作，不该逼一个
    还没建起来的 agent 先建起来。
    """
    session_id = thread_id()
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
        set_thread_id(rotated)
    return rotated
