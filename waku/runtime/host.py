"""常驻 Host —— 一个进程、一个 Waku、多个 gateway。

设计见 docs/resident-host-design.md。为什么需要它：

今天每个 gateway 各自 `Waku()`：CLI 一个、voice 一个、dashboard 一个
（藏在 ops/browser_agent.py 的单例里），于是三个进程、三条 SQLite 连接指向
同一个 state.db。dashboard 那个单例其实已经把自己那半边问题解决了 ——
一个实例、一次一轮、设置变更后能重建 —— 但那套能力锁在 dashboard 里，
第二个 gateway 无处接入。

中文导读（先看这五条，再往下读代码）：
  1. Host 持有唯一的 Waku 实例，gateway 只持有 Host。
  2. 每轮请求自带 `source` 和 `session_id`。Host **不**从 source 推导会话：
     一个 source 可以合法地拥有多个会话（dashboard 的「新聊天」就是这样），
     推导等于把会话策略塞进 Host，让它去猜每个 gateway 的语义。
  3. 串行边界 = 一个 worker 线程 + 一条有界 FIFO 队列。
  4. 重建是队列里的一个任务，所以永远发生在两轮之间，不会穿过某一轮。
  5. stop() 明确拒绝排队中尚未开始的请求，绝不让调用方一直挂着。

为什么会话隔离是这个文件的重点：`Session.history` 是一个内存里的列表，
`Waku.respond()` 直接拿它拼提示词。两个 gateway 共用一个 Waku 就等于共用
一个工作窗口 —— 微信的消息会带着浏览器最近几轮的上下文被回答。而数据库
里的行看着还是对的（session_id / source 都记对了），所以这种串会话很难
从数据上发现。修法就是在串行边界内 `session.switch(session_id)`。
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol, Self

DEFAULT_QUEUE_SIZE = 32

# 关停时最多等当前这一轮多久。超时后**不关资源**，直接返回 False：关掉一条活
# 线程正在写的连接会把这一轮拆成两半（chat_log 里只剩用户消息、没有回复），而
# 进程反正要退出，操作系统会回收描述符和 MCP 子进程。一个卡住的模型调用不该让
# Ctrl-C 变成"去任务管理器杀进程"。
SHUTDOWN_JOIN_SECONDS = 30.0

_NOT_RUNNING = "the host is not running; no request was accepted"
_STOPPING = "the host is shutting down; this request was not run"


class HostBusy(RuntimeError):
    """队列满了。请求没有被接受，也没有跑 —— 调用方应当回一句"忙"。"""


class HostStopped(RuntimeError):
    """Host 正在关停或已经关停，请求没有被接受。"""


class Gateway(Protocol):
    """一个 gateway 只拥有一件东西：它自己的传输方式。

    它拿到 Host，**永远不拿 Waku 实例** —— 这样 channel 的协议、凭据和游标
    就自然地留在它自己那个文件里，进不了 loop、memory、工具或提示词。
    """

    name: str

    def start(self, host: Host) -> None: ...

    def stop(self) -> None: ...


@dataclass
class _Task:
    seq: int
    kind: str  # "turn" | "rebuild" | "custom" | "stop"
    text: str = ""
    source: str = ""
    session_id: str = ""
    observer: object = None
    stream: bool = False
    work: object = None          # 仅 "custom"：要在串行边界内跑的可调用对象
    done: threading.Event = field(default_factory=threading.Event)
    result: object = None
    error: BaseException | None = None


# 哨兵：worker 收到它就退出。stop() 会先把队列倒空，所以一定有位置放它。
_STOP = _Task(seq=-1, kind="stop")


def _close_quietly(obj) -> None:
    """关东西的时候别让异常掀桌子：关停必须走到底。"""
    try:
        obj.close()
    except Exception:
        pass


def build_from_environment():
    """真正建一个 Waku。和 dashboard 以前的做法一致：settings 从当前环境读，
    连接允许跨线程（HTTP 的 worker 线程会用到它）。"""
    from waku.app import Waku
    from waku.config import load_settings
    from waku.db import connect

    settings = load_settings()
    settings.ensure_home()
    conn = connect(settings.home, check_same_thread=False)
    return Waku(settings=settings, conn=conn)


class Host:
    """持有唯一的 Waku，并把所有 gateway 的回合串成一条有序队列。

    `build` 可注入，方便测试塞一个脚本化的假模型；默认就是
    `build_from_environment`。
    """

    def __init__(self, build: Callable[[], object] | None = None, *,
                 queue_size: int = DEFAULT_QUEUE_SIZE,
                 stop_timeout: float = SHUTDOWN_JOIN_SECONDS,
                 gateways: Sequence[Gateway] | None = None):
        self._build_agent = build or build_from_environment
        self._queue: queue.Queue[_Task] = queue.Queue(maxsize=queue_size)
        self._stop_timeout = stop_timeout
        # 一把锁保护 _seq / _accepting / _pending / _agent。它不保护"正在跑的
        # 那一轮" —— 那是 worker 一个人的事，一次只有一个。
        self._lock = threading.Lock()
        self._agent = None
        self._seq = 0
        self._pending = 0
        self._accepting = False
        self._started = False
        self._closed = False
        self._worker: threading.Thread | None = None
        self._gateways: list[Gateway] = list(gateways or ())
        # 某个 gateway 启动失败的原因，按名字记下来。它绝不能把 Host（也就是
        # dashboard）一起带走 —— 一个渠道配错了不该让网页打不开。
        self._gateway_errors: dict[str, str] = {}

    # ------------------------------------------------------------ gateway

    def register(self, gateway: Gateway) -> None:
        """挂一个 gateway。Host 已经在跑就立刻启动它。"""
        with self._lock:
            self._gateways.append(gateway)
            running = self._started and self._accepting
        if running:
            self._start_gateway(gateway)

    def gateway_names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(g.name for g in self._gateways)

    def gateway_error(self, name: str) -> str:
        """这个 gateway 启动时挂掉的原因，空字符串表示没问题（或没挂过）。"""
        with self._lock:
            return self._gateway_errors.get(name, "")

    def _start_gateway(self, gateway: Gateway) -> None:
        try:
            gateway.start(self)
            with self._lock:
                self._gateway_errors.pop(gateway.name, None)
        except Exception as exc:  # 任何异常都不允许往上冒
            with self._lock:
                self._gateway_errors[gateway.name] = f"{type(exc).__name__}: {exc}"

    # ------------------------------------------------------------ 生命周期

    def start(self) -> None:
        """起 worker。此时还不建 Waku —— 没人问过话就先别花这个钱。

        一个 Host 只用一次：stop() 之后再 start() 是空操作。否则一个还在旧
        worker 里跑着的回合会和新 worker 的回合叠在一起 —— 而"一次一轮"正是
        整个设计要保的东西。
        """
        with self._lock:
            if self._started:
                return
            self._started = True
            self._accepting = True
        self._worker = threading.Thread(target=self._work, name="waku-host", daemon=True)
        self._worker.start()
        with self._lock:
            gateways = list(self._gateways)
        for gateway in gateways:
            self._start_gateway(gateway)

    def stop(self) -> bool:
        """按顺序关停：停收 → 明确拒绝排队中的请求 → 等当前一轮 → 关资源。

        已经排上队但还没开始的请求**不会**被悄悄丢掉，也不会让它一直挂着：
        `ask()` 会收到 HostStopped 并带着一句人话抛出来。正在跑的那一轮不打断
        （模型调用无法从外部取消），最多等 `stop_timeout` 秒。

        **返回值就是超时策略本身**：

          True    worker 真的停了，外部资源（MCP 子进程 + SQLite 连接）已关。
          False  超过 `stop_timeout` 还没停 —— 这一轮仍在使用那条连接，所以
                 **什么都不关**。关掉一条活线程正在写的连接，会把这一轮的
                 写拆成两半（chat_log 里只落用户消息、没有回复），而进程反正
                 马上要退出，操作系统会回收描述符和子进程。

        重复调用返回同一个答案，不会第二次突然变成 True。
        """
        with self._lock:
            if not self._accepting:
                return self._closed
            self._accepting = False
            gateways = list(reversed(self._gateways))

        # 先停 gateway：它们可能正阻塞在自己的长轮询上，而那是它们自己的 I/O，
        # 只有它们能中止。跑在它们手里的 `ask()` 要么等到这一轮结束，要么在下面
        # 被明确拒绝，不会一直挂着。
        for gateway in gateways:
            try:
                gateway.stop()
            except Exception as exc:
                with self._lock:
                    self._gateway_errors[gateway.name] = f"{type(exc).__name__}: {exc}"

        with self._lock:
            rejected = self._drain_locked()
            try:
                self._queue.put_nowait(_STOP)
            except queue.Full:  # 刚倒空过，不该发生
                pass

        # 唤醒被拒绝的调用方（在锁外做，别拿着锁等别人）
        for task in rejected:
            task.error = HostStopped(_STOPPING)
            task.done.set()

        worker, self._worker = self._worker, None
        if worker is not None:
            worker.join(timeout=self._stop_timeout)
            if worker.is_alive():
                # 那一轮还在跑。绝不在此时 close() —— 见上面 docstring。
                return False

        with self._lock:
            agent, self._agent = self._agent, None
            self._closed = True
        if agent is not None:
            _close_quietly(agent)
        return True

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *_exc) -> bool:
        self.stop()
        return False
    # ---------------------------------------------------------------- 请求

    def ask(self, text: str, *, source: str, session_id: str,
            observer=None, stream: bool = False):
        """跑一轮，返回 LoopResult。阻塞到自己这一轮结束。

        `source` 和 `session_id` 都必须显式给出：前者是写入 chat_log 的来源
        标记，后者决定这一轮读哪段工作记忆。Host 不从 source 推导 session_id。
        """
        if not source or not session_id:
            raise ValueError("ask() needs an explicit source and session_id")
        task = self._submit("turn", text=text, source=source,
                            session_id=session_id, observer=observer, stream=stream)
        task.done.wait()
        if task.error is not None:
            raise task.error
        return task.result

    def run(self, work: Callable[[object], object], *, session_id: str | None = None):
        """在串行边界内、对当前实例跑一段自定义调用。

        给"不是普通回合"的调用方用（dashboard 的 `/triage` 直接调图那条路）。
        它拿的仍然是那把串行权，所以不会和任何一回合叠在一起；给了
        `session_id` 就先 switch 过去，免得一个 dashboard 的调试动作跑到
        gateway 的会话上去。
        """
        task = self._submit("custom", work=work, session_id=session_id or "")
        task.done.wait()
        if task.error is not None:
            raise task.error
        return task.result

    def rebuild(self) -> str | None:
        """在两轮之间按当前环境重建实例。

        成功返回 None；失败返回错误字符串，**并且旧实例继续服务** —— 一个坏
        key 不该顺手把你原本能用的 agent 一起拿走。契约与
        `browser_agent.rebuild()` 保持一致，所以 integrations.py 的调用点不用改。
        """
        try:
            task = self._submit("rebuild")
        except (HostBusy, HostStopped) as exc:
            return str(exc)
        task.done.wait()
        if task.error is not None:
            return str(task.error)
        return task.result or None

    def current(self):
        """只读窥视：还没聊过就是 None。状态页读它，不为了看状态去建实例。"""
        return self._agent

    def pending(self) -> int:
        """等待中的请求数，**不含**正在跑的那一条 —— "还有几条在排队"。"""
        with self._lock:
            return self._pending

    # ---------------------------------------------------------------- 内部

    def _submit(self, kind: str, **kw) -> _Task:
        with self._lock:
            if not self._accepting:
                raise HostStopped(_NOT_RUNNING)
            # 取号和白入队必须在**同一个临界区**里完成。否则两个并发提交者可能
            # 拿到 1、2 却以 2、1 的顺序入队，顺序保证当场作废。
            self._seq += 1
            task = _Task(seq=self._seq, kind=kind, **kw)
            try:
                self._queue.put_nowait(task)
            except queue.Full:
                self._seq -= 1  # 没排上就别占号
                raise HostBusy(
                    f"the queue is full ({self._queue.maxsize} turns waiting); "
                    "this request was not accepted"
                ) from None
            self._pending += 1
            return task

    def _drain_locked(self) -> list[_Task]:
        """把队列倒空。调用方必须已经持有 self._lock。"""
        rejected: list[_Task] = []
        while True:
            try:
                task = self._queue.get_nowait()
            except queue.Empty:
                break
            if task.kind == "stop":
                continue
            self._pending -= 1
            rejected.append(task)
        return rejected

    def _work(self) -> None:
        while True:
            task = self._queue.get()
            try:
                if task.kind == "stop":
                    return
                with self._lock:
                    # 被拿起来了就不再是"在等"。pending() 报的是**等待中**的条数，
                    # 不含正在跑的那一条 —— 那才是"还有几条在排队"的意思。
                    self._pending -= 1
                try:
                    task.result = self._run(task)
                except BaseException as exc:  # 原样交给调用方，别吞
                    task.error = exc
            finally:
                self._queue.task_done()
                if task.kind != "stop":
                    task.done.set()

    def _run(self, task: _Task):
        if task.kind == "rebuild":
            return self._rebuild_now()
        agent = self._ensure_agent()
        # 串行边界之内绑定会话 —— "两个来源不会串会话"就落在这一行上。
        # switch() 顺带把最近 N 轮从 state.db 重新读进工作记忆，所以重建之后
        # 不需要任何人手工搬运 session_id。
        if task.session_id:
            agent.session.switch(task.session_id)
        if task.kind == "custom":
            return task.work(agent)
        return agent.respond(task.text, observer=task.observer,
                             source=task.source, stream=task.stream)

    def _ensure_agent(self):
        agent = self._agent
        if agent is None:
            agent = self._build_agent()
            self._agent = agent
        return agent

    def _rebuild_now(self) -> str | None:
        old = self._agent
        try:
            fresh = self._build_agent()
        except (Exception, SystemExit) as exc:  # get_client 会 raise SystemExit
            return str(exc)
        self._agent = fresh
        if old is not None:
            _close_quietly(old)
        return None


# ------------------------------------------------------- 本进程的 Host（单例）
#
# 谁拿它：这个模块。别人只能通过下面三个函数碰它 —— 在别人的文件里写
# `global` 正是两个写者互相打架的方式（这条规矩是从被替掉的
# browser_agent 单例继承来的）。
_HOST: Host | None = None
_HOST_LOCK = threading.Lock()


def shared_host() -> Host:
    """本进程的 Host。第一次用到时建并起 worker；之后一直是同一个。"""
    global _HOST
    with _HOST_LOCK:
        if _HOST is None:
            _HOST = Host()
            _HOST.start()
        return _HOST


def live_host() -> Host | None:
    """已经建起来的 Host，没有就是 None。给"顺手指一下"的调用方用
    （integrations 换 provider 之后要重建，但 CLI 里可能压根没有 Host），
    所以它不会顺手建一个。"""
    return _HOST


def set_shared_host(replacement: Host | None) -> None:
    """换掉本进程的 Host（测试用）。调用方负责停掉旧的那个。"""
    global _HOST
    with _HOST_LOCK:
        _HOST = replacement
