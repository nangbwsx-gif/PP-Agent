"""微信一对一文本 gateway —— 挂在常驻 Host 上的第二个 channel。

设计见 docs/resident-host-design.md §6。协议依据是 lab/wechat-ilink/（2026-09-23
真机跑通过的记录），不是猜的。

中文导读（先看这四条）：
  1. 这个文件只搬文本。它拿的是 Host，**永远不拿 Waku** —— 所以微信的协议、凭据
     和游标进不了 loop、memory、工具或提示词。
  2. 收到文本就一句 `host.ask(text, source="wechat", session_id=...)`，只在整轮
     结束后把 `result.reply` 发回去。中间的任何流式片段都不发。
  3. **游标最后推进，消息先认领**（见下面那段），这是"既不悄悄丢消息、也不重复
     执行有副作用的工具"的全部依据。
  4. 它默认关闭。挂了也不允许把 dashboard 一起带走 —— 唤醒它的每一处都兜住异常。

协议（实测于 2026-09-23，ilinkai.weixin.qq.com）：
    GET  /ilink/bot/get_bot_qrcode?bot_type=3      取登录二维码，无需鉴权
    GET  /ilink/bot/get_qrcode_status?qrcode=…     轮询扫码状态（约 30s 长轮询）
    POST /ilink/bot/getupdates                     长轮询收消息
    POST /ilink/bot/sendmessage                    发文本
    POST /ilink/bot/getconfig · /sendtyping        输入状态（本版不用）
  鉴权：AuthorizationType: ilink_bot_token + Authorization: Bearer <token>
       + X-WECHAT-UIN（每次请求换一个随机 uint32 的 base64）
  回复：必须带上来消息里的 `context_token`。它是**会话路由令牌**，不是用户身份 ——
       身份是 `from_user_id`，会话 id 由它派生。

=============================================================================
游标推进与消息处理的顺序（这条是硬约定，改之前先读完）
=============================================================================

    每轮 poll：
      1. getupdates(cursor) → (msgs, next_cursor)     ← 拿到的游标先**不存**
      2. 逐条处理 msgs：
           a. claim(message_id)                        ← 处理**之前**落盘
           b. host.ask(...) 跑完整回合
           c. 用这条消息的 context_token 发回 result.reply
           d. complete(message_id)
      3. 整个批次处理完，才 save_cursor(next_cursor)

为什么是这个顺序：

  * **游标放在最后** —— 它是流位置，不是逐条确认。提前存 = 崩在中间就再也不会
    重投这批消息，那是**静默丢消息**。放在最后，崩了就整批重投，一条都不会少。
  * **认领放在最前** —— 重投的消息靠 message_id 认领过就不再跑第二次。这一条是
    为了"不重复执行有副作用的工具"：一个被创建两次的日历事件，比一条没发出去的
    回复糟糕得多。
  * 代价写清楚：崩在"认领之后、回复之前"，那条消息**不会被重试**，用户收不到回复。
    这不藏着 —— `status` 会把这类中断列出来（`unfinished()`）。

  另外：`HostBusy` / `HostStopped` 意味着那一轮**根本没跑**，所以撤销认领并让本批
  不推进游标，下次重投时再试。这跟"跑了一半出错"是两回事，不能混。
"""

from __future__ import annotations

import base64
import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Collection
from datetime import UTC, datetime
from pathlib import Path

from waku.config import load_settings
from waku.runtime import conversation
from waku.runtime.host import Host, HostBusy, HostStopped

BASE_URL = "https://ilinkai.weixin.qq.com"
CHANNEL_VERSION = "1.0.0"

GET_UPDATES_TIMEOUT = 45
QR_STATUS_TIMEOUT = 40
SHORT_TIMEOUT = 20

MESSAGE_TYPE_USER = 1
MESSAGE_TYPE_BOT = 2
MESSAGE_STATE_FINISH = 2
ITEM_TYPE_TEXT = 1

ITEM_TYPE_NAMES = {2: "image", 3: "voice", 4: "file", 5: "video"}

# 一条会话消息的回复上限，超了按段落切开分多条发（协议侧的限制）。
MAX_TEXT_CHUNK = 2000

# 会话失效：这个 token 已经死了，必须重新扫码。
SESSION_EXPIRED_CODE = -14

RETRY_BASE_SECONDS = 1.0
RETRY_MAX_SECONDS = 30.0

# 服务器本该把 getupdates 挂住 ~18 秒才返回，可实测它有时立刻返回。下一次请求
# 之间必须有个下限，否则这个循环就空转到网络往返的速度：2026-09-23 lab 里跑了
# 16 小时、约 70 万次请求（12 req/s），那段时间微信那边看起来就是“bot 不回话”。
# 代价是服务器立刻返回时最多多等一秒多。
MIN_POLL_INTERVAL_SECONDS = 2.0

# 停 gateway 时最多等它的长轮询线程多久。线程是 daemon，等不到也不会拖住进程。
GATEWAY_STOP_JOIN_SECONDS = 5.0

# 发回复失败重试几次。**只重试发送，不重跑回合** —— 重跑会重复执行工具。
SEND_ATTEMPTS = 3
SEND_RETRY_SECONDS = 1.0

# 认领/完成的 message_id 各留多少条。留够覆盖一次重启的重投窗口即可。
SEEN_LIMIT = 200

NON_TEXT_REPLY = "目前只支持文本消息，图片/语音/文件/视频还看不了。"
EMPTY_TEXT_REPLY = "收到一条空消息。发文字我就懂了。"


class ApiError(Exception):
    """一次调用失败，带上足够判断该怎么处理的字段。"""

    def __init__(self, message, *, status=0, code=None, payload=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.payload = payload


# ------------------------------------------------------------------ 协议层


def _wechat_uin() -> str:
    """X-WECHAT-UIN：一个随机 uint32 的 base64。每次请求都换。"""
    return base64.b64encode(str(random.getrandbits(32)).encode()).decode()


def _auth_headers(token: str) -> dict:
    return {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Authorization": f"Bearer {token}",
        "X-WECHAT-UIN": _wechat_uin(),
    }


def _base_info() -> dict:
    return {"channel_version": CHANNEL_VERSION}


def _call(url, *, data=None, headers=None, timeout):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    request = urllib.request.Request(
        url, data=body, headers=headers or {}, method="POST" if body else "GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, raw = response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # URLError / TimeoutError / ssl —— 都算网络问题
        raise ApiError(f"network error: {exc}") from exc

    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        raise ApiError(f"expected JSON, got HTTP {status}: {raw[:160]!r}", status=status) from None

    if not 200 <= status < 300:
        # 实测：这个接口报错用的是 err_msg / ret，不是 errmsg / errcode。
        raise ApiError(
            payload.get("err_msg") or payload.get("errmsg") or f"HTTP {status}",
            status=status,
            code=payload.get("errcode", payload.get("ret")),
            payload=payload,
        )

    # HTTP 200 不等于成功。实测（2026-09-23，对一个死掉的 session 发消息）：
    #
    #     {"errcode": -14, "errmsg": "session timeout"}
    #
    # **根本没有 `ret` 字段。** 一个只看 ret 的检查会把这次发送读成“已送达” ——
    # 而发送是绝对不能读错的一件事。所以 ret 和 errcode **两个都要看**，
    # 任一个存在且非 0 就是失败。
    ret, code = payload.get("ret"), payload.get("errcode")
    failed = (isinstance(ret, int) and ret != 0) or (isinstance(code, int) and code != 0)
    if failed:
        raise ApiError(
            payload.get("err_msg") or payload.get("errmsg") or f"ret={ret} errcode={code}",
            status=status,
            code=code if isinstance(code, int) and code != 0 else ret,
            payload=payload,
        )
    return payload


class ILinkApi:
    """gateway 用到的四个调用。

    做成对象而不是模块函数，是为了让离线评测能整体替换掉它 —— 假 iLink 响应跑完
    登录、长轮询、去重、发送失败、重启恢复的每一条路径，一次网络都不用碰。
    """

    def fetch_qr_code(self, base_url: str = BASE_URL) -> dict:
        return _call(f"{base_url}/ilink/bot/get_bot_qrcode?bot_type=3", timeout=SHORT_TIMEOUT)

    def fetch_qr_status(self, qrcode: str, base_url: str = BASE_URL) -> dict:
        return _call(
            f"{base_url}/ilink/bot/get_qrcode_status?qrcode={urllib.parse.quote(qrcode)}",
            headers={"iLink-App-ClientVersion": "1"},
            timeout=QR_STATUS_TIMEOUT,
        )

    def fetch_updates(self, base_url: str, token: str, cursor: str) -> dict:
        return _call(
            f"{base_url}/ilink/bot/getupdates",
            data={"get_updates_buf": cursor, "base_info": _base_info()},
            headers=_auth_headers(token),
            timeout=GET_UPDATES_TIMEOUT,
        )

    def post_message(self, base_url: str, token: str, message: dict) -> dict:
        return _call(
            f"{base_url}/ilink/bot/sendmessage",
            data={"msg": message, "base_info": _base_info()},
            headers=_auth_headers(token),
            timeout=SHORT_TIMEOUT,
        )


# 固定的命名空间，用来给每个分段算一个稳定 client_id。不要改它：改了等于
# 把所有在途重试变成新消息。
CLIENT_NAMESPACE = uuid.UUID("2f8b7c14-9d3a-5e6b-8c41-7a0d5b2e9f13")


def stable_client_id(message_id: str, index: int) -> str:
    """同一条入站消息的第 index 段，永远是同一个 client_id。

    每次重试都 `uuid4()` 的话，服务端会把重试当成**一条新消息** —— 用户收到两份。
    用 uuid5 从 (message_id, index) 派生：同一段永远同一个 id，不同段不同。
    """
    return str(uuid.uuid5(CLIENT_NAMESPACE, f"{message_id}:{index}"))


def build_text_message(user_id: str, context_token: str, text: str, client_id: str) -> dict:
    """`client_id` 是必填参数，故意的：它必须是稳定的，不能在上游随手 uuid4()。"""
    return {
        "from_user_id": "",
        "to_user_id": user_id,
        "client_id": client_id,
        "message_type": MESSAGE_TYPE_BOT,
        "message_state": MESSAGE_STATE_FINISH,
        "context_token": context_token,
        "item_list": [{"type": ITEM_TYPE_TEXT, "text_item": {"text": text}}],
    }


def extract_text(item_list) -> str:
    parts = [
        str(item.get("text_item", {}).get("text", "")).strip()
        for item in item_list or []
        if item.get("type") == ITEM_TYPE_TEXT
    ]
    return "\n".join(part for part in parts if part)


def detect_kind(item_list) -> str:
    """'text'，或者第一个非文本项的名字。空消息返回 'empty'。"""
    items = item_list or []
    if not items:
        return "empty"
    for item in items:
        kind = item.get("type")
        if kind == ITEM_TYPE_TEXT:
            continue
        return ITEM_TYPE_NAMES.get(kind, "unsupported")
    return "text" if extract_text(items) else "empty"


def chunk_text(text: str, limit: int = MAX_TEXT_CHUNK) -> list:
    chunks, remaining = [], text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


# -------------------------------------------------------------------- 状态


def _write_json(path: Path, payload, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)      # 原子替换：写一半崩了不会留下半个 JSON
    if mode is not None:
        try:
            path.chmod(mode)
        except OSError:
            pass           # Windows 上 mode 只是建议值，真正的保护是用户目录 ACL


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


class SeenMessages:
    """哪些入站 message_id 被认领了、哪些跑完了。

    **认领在跑回合之前**：这条就是"重投的消息不会跑第二次"的全部依据。代价是崩在
    认领之后、回复之前的那条不会被重试 —— `take_unfinished()` 把它报出来，而不是
    藏起来。
    """

    def __init__(self, path: Path):
        self.path = path
        data = _read_json(path, {"claimed": [], "done": []})
        self._claimed = list(data.get("claimed") or [])
        self._done = list(data.get("done") or [])

    def _save(self) -> None:
        _write_json(self.path, {"claimed": self._claimed[-SEEN_LIMIT:],
                                "done": self._done[-SEEN_LIMIT:]})

    def is_known(self, message_id: str) -> bool:
        return message_id in self._claimed or message_id in self._done

    def claim(self, message_id: str) -> None:
        if message_id not in self._claimed:
            self._claimed.append(message_id)
            self._save()

    def unclaim(self, message_id: str) -> None:
        """那一轮根本没跑（队列满 / 正在关停），所以撤回认领，下次重投再试。"""
        if message_id in self._claimed:
            self._claimed.remove(message_id)
            self._save()

    def complete(self, message_id: str) -> None:
        if message_id in self._claimed:
            self._claimed.remove(message_id)
        if message_id not in self._done:
            self._done.append(message_id)
        self._save()

    def take_unfinished(self) -> list:
        """取走"认领了但没跑完"的 id，并把它们记成已完成（不重试）。

        进程启动时调一次：这些是上次崩在回合中间的。跑没跑一半无从判断，而重跑会
        重复执行有副作用的工具 —— 所以不重试，只报告。
        """
        unfinished = list(self._claimed)
        for message_id in unfinished:
            self.complete(message_id)
        return unfinished


class Outbox:
    """回合已经跑完、但还没送达的回复。

    为什么需要它：**发送失败绝不能等于"已送达"。** 回合已经跑过了（工具可能已经
    产生了副作用），所以永远只能重发、不能重跑。回复先写进这里，
    `seen.complete()` 才把那条消息记为已处理 —— 那个标记的意思是
    "回合跑完了、回复不会丢"，**不是**"回复发出去了"。

    逐段记录确认状态，所以某一段失败重试时不会重发前面已经确认的段落。
    """

    def __init__(self, path: Path):
        self.path = path
        self._entries: list[dict] = list(_read_json(path, {}).get("entries") or [])

    def _save(self) -> None:
        _write_json(self.path, {"entries": self._entries})

    def _find(self, message_id: str) -> dict | None:
        return next((e for e in self._entries if e.get("messageId") == message_id), None)

    def entries(self) -> list[dict]:
        return [dict(entry) for entry in self._entries]

    def count(self) -> int:
        return len(self._entries)

    def enqueue(self, message_id: str, user_id: str, context_token: str, text: str) -> None:
        """把一条回复排进待发。分段和 client_id 在这里就定死 —— 重试要用同一个。"""
        if not text or self._find(message_id) is not None:
            return
        self._entries.append({
            "messageId": message_id,
            "userId": user_id,
            "contextToken": context_token,
            "createdAt": datetime.now(UTC).isoformat(timespec="seconds"),
            "attempts": 0,
            "lastError": "",
            "chunks": [
                {"text": chunk, "clientId": stable_client_id(message_id, index), "sent": False}
                for index, chunk in enumerate(chunk_text(text))
            ],
        })
        self._save()

    def mark_sent(self, message_id: str, index: int) -> None:
        entry = self._find(message_id)
        if entry is None:
            return
        entry["chunks"][index]["sent"] = True
        self._save()

    def note_attempt(self, message_id: str, error: str) -> None:
        entry = self._find(message_id)
        if entry is None:
            return
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        entry["lastError"] = error
        self._save()

    def remove(self, message_id: str) -> None:
        before = len(self._entries)
        self._entries = [e for e in self._entries if e.get("messageId") != message_id]
        if len(self._entries) != before:
            self._save()

    def summary(self) -> list[dict]:
        """给 status 看的一小段 —— 不含回复正文。回复内容属于对话，不属于日志。"""
        return [
            {
                "messageId": e.get("messageId", ""),
                "attempts": e.get("attempts", 0),
                "sentChunks": sum(1 for c in e.get("chunks", []) if c.get("sent")),
                "totalChunks": len(e.get("chunks", [])),
                "lastError": e.get("lastError", ""),
            }
            for e in self._entries
        ]


class DecisionLog:
    """最近几条入站消息被怎么处理的，以及为什么。

    存在的理由很具体：gateway 的计数字段在内存里，而 `waku wechat status` 是
    **另一个进程** —— 没有这个东西，“它为什么不回我” 就只能靠猜。一次拒绝只能看到
    “消息被取走了、没有回复”，而原因（不在白名单 / 没有 ID / 没有 context_token）
    完全不可见。

    只记 message_id 和原因，**不记正文**：正文属于对话，不属于日志。
    """

    LIMIT = 20

    def __init__(self, path: Path):
        self.path = path
        self._entries: list[dict] = list(_read_json(path, {}).get("entries") or [])

    def record(self, outcome: str, message_id: str = "", reason: str = "") -> None:
        self._entries.append({
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "messageId": message_id or "(none)",
            "outcome": outcome,
            "reason": reason,
        })
        del self._entries[:-self.LIMIT]
        _write_json(self.path, {"entries": self._entries})

    def recent(self) -> list[dict]:
        return list(self._entries)


class GatewayState:
    """gateway 落在磁盘上的一切，全在 `<home>/wechat/` 下。

    `.waku/` 整个被 .gitignore 挡住，`credentials.json` 和 `*token*.json` 还被
    额外挡了一层 —— 凭据只存在这里，不进日志、不进测试快照、不进提交。
    """

    def __init__(self, directory: Path):
        self.directory = directory
        self.credentials_path = directory / "credentials.json"
        self.cursor_path = directory / "cursor.json"
        self.seen = SeenMessages(directory / "seen.json")
        self.outbox = Outbox(directory / "outbox.json")
        self.decisions = DecisionLog(directory / "decisions.json")

    def credentials(self) -> dict | None:
        return _read_json(self.credentials_path, None)

    def save_credentials(self, payload: dict) -> None:
        _write_json(self.credentials_path, payload, mode=0o600)

    def clear_credentials(self) -> None:
        for path in (self.credentials_path, self.cursor_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def cursor(self) -> str:
        return str(_read_json(self.cursor_path, {}).get("cursor", ""))

    def save_cursor(self, cursor: str) -> None:
        _write_json(self.cursor_path, {"cursor": cursor})

    def clear_cursor(self) -> None:
        try:
            self.cursor_path.unlink()
        except FileNotFoundError:
            pass


# ----------------------------------------------------------------- gateway


class WeChatGateway:
    """一个已绑定微信账号的一对一文本 channel。

    第一版范围就是这些：文本进、文本出、一个账号。非文本和空消息直接由 channel
    自己回一句，**不进 Agent 回合** —— 所以一条图片消息触发零次回合。
    """

    name = "wechat"

    def __init__(self, state: GatewayState, api: ILinkApi | None = None, *,
                 allowed: Collection[str] = (),
                 poll_interval: float = RETRY_BASE_SECONDS,
                 send_retry_seconds: float = SEND_RETRY_SECONDS,
                 announce: Callable[[str], None] | None = None):
        self._state = state
        self._allowed = {sender for sender in allowed if sender}
        self._api = api or ILinkApi()
        self._poll_interval = poll_interval
        self._send_retry_seconds = send_retry_seconds
        # 说给用户听的一句话。可注入 —— 评测里塞一个 list.append，就不靠全局
        # stdout 了（多个评测的 gateway 线程会往同一个捕获流里写）。
        self._announce_fn = announce or (lambda message: print(f"[wechat] {message}", flush=True))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_error = ""      # 真的出错了：轮询失败、发送失败、回合失败
        self._last_note = ""       # 只是告知：重复消息、非文本、没 context_token
        self._phase = "stopped"       # stopped | not-logged-in | polling | expired
        self._handled = 0
        self._failed = 0
        self._refused = 0                        # 不在白名单里的发送者
        self._refused_senders: list[str] = []    # 看到就记下来，好用得着去加白名单
        self._no_id = 0                          # 没有 message_id、无法去重的消息
        # 上一次跑崩在回合中间的那些 id。start() 时取一次，之后由 status 报告。
        self._interrupted: list[str] = []
        self._announced_decisions: set[str] = set()
        # 当前这轮连续失败是否已经报过。断线重试是静默的（否则日志被刷爆），
        # 但第一次失败和恢复各报一次 —— 用户需要知道它掉线了。
        self._announced_failure = False

    # ------------------------------------------------------------ 生命周期

    def start(self, host: Host) -> None:
        """起长轮询线程。

        **没登录不是错误**，是一个正常状态：把 phase 记成 not-logged-in 就返回，
        不去连外网，也不抛异常。抛出去等于一个没配好的 channel 把 dashboard 一起
        带走 —— 那正是这份实现要避免的。
        """
        credentials = self._state.credentials()
        if not credentials or not credentials.get("token"):
            self._set_phase("not-logged-in")
            return
        # 上次崩在回合中间、认领了却没跑完的：取一次报告，并记为已完成（**不重试** ——
        # 工具可能已经跑过了）。这不藏着，就挂在 status()["unfinished"] 里。
        # 故意不写进 last_error：那是"本次运行出了什么错"，而这是一件历史事实 ——
        # 不该被下一次例行的轮询失败顶掉。
        self._interrupted = self._state.seen.take_unfinished()
        self._set_phase("polling")
        self._thread = threading.Thread(
            target=self._poll_forever, args=(host, credentials), name="waku-wechat", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """让长轮询线程退出。线程是 daemon，等不到也不会拖住进程。"""
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=GATEWAY_STOP_JOIN_SECONDS)
        self._set_phase("stopped")

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---------------------------------------------------------------- 状态

    def _set_phase(self, phase: str) -> None:
        with self._lock:
            self._phase = phase

    def status(self) -> dict:
        """给 `waku wechat status` 用。**绝不回显 token**，连后四位都不回。"""
        credentials = self._state.credentials() or {}
        with self._lock:
            return {
                "phase": self._phase,
                "account_id": credentials.get("accountId", ""),
                "user_id": credentials.get("userId", ""),
                "logged_in": bool(credentials.get("token")),
                "cursor": self._state.cursor(),
                "handled": self._handled,
                "failed": self._failed,
                "last_error": self._last_error,
                "last_note": self._last_note,
                "unfinished": list(self._interrupted),
                # 白名单：空 = 谁都不许。status 看这个就知道要不要加一行。
                "allowed": sorted(self._allowed),
                "refused": self._refused,
                "refused_senders": list(self._refused_senders),
                "no_id": self._no_id,
                # 已产生但还没确认送达的回复。这里非空才是真信号：
                # 它意味着“回合跑过了，但用户可能没看到” —— 重启后会再试发。
                "pending": self._state.outbox.summary(),
                # 最近几条入站消息被怎么处理的 —— 另一个进程看不到内存里的计数器，
                # 所以“它为什么不回我”必须有落盘的地方。
                "decisions": self._state.decisions.recent(),
            }

    # ------------------------------------------------------------ 长轮询

    def _poll_forever(self, host: Host, credentials: dict) -> None:
        base_url = credentials.get("baseUrl") or BASE_URL
        token = credentials.get("token", "")
        # 上一次没送出去的回复，先送掉 —— **只发，绝不重跑回合**。
        # “发送失败能在重启后被处理”就落在这一行上。
        self.flush_outbox(base_url, token)
        cursor = self._state.cursor()
        backoff = self._poll_interval

        while not self._stop.is_set():
            started = time.monotonic()
            try:
                response = self._api.fetch_updates(base_url, token, cursor)
            except ApiError as exc:
                if exc.code == SESSION_EXPIRED_CODE:
                    # token 死了。别再打流量了 —— 等用户重新扫码。
                    self._last_error = (
                        f"session expired (errcode {SESSION_EXPIRED_CODE}); "
                        "run `waku wechat login` again"
                    )
                    self._announce(
                        "WeChat session expired — the bot needs a new scan. "
                        "Run `waku wechat login`. The dashboard is unaffected."
                    )
                    self._set_phase("expired")
                    return
                self._last_error = f"getupdates failed: {exc}"
                self._announce_once(f"WeChat polling failed ({exc}); retrying in background")
                self._stop.wait(backoff)
                backoff = min(backoff * 2, RETRY_MAX_SECONDS)
                continue
            except Exception as exc:       # 解析、编码、任何没预料到的
                self._last_error = f"getupdates failed: {type(exc).__name__}: {exc}"
                self._announce_once(f"WeChat polling failed ({type(exc).__name__}); retrying")
                self._stop.wait(backoff)
                backoff = min(backoff * 2, RETRY_MAX_SECONDS)
                continue

            if self._announced_failure:
                self._announced_failure = False
                self._announce("WeChat polling recovered")
            # 成功了就把上次的错误清掉。否则一个瞬断会永远挂在连接页上：
            # “上次出过错”不等于“现在还在错”。
            self._last_error = ""
            backoff = self._poll_interval
            cursor = self._handle_batch(host, base_url, token, response)
            if cursor is None:
                # 本批没能处理完（那一轮没跑成）。游标原地不动，下一轮整批重投，
                # 靠 message_id 认领去重。
                self._stop.wait(self._poll_interval)
                cursor = self._state.cursor()
            # 一次成功轮询到下一次之间的下限。放在这里，所有成功路径都得过。
            # 出错的那几条 continue 走不到这儿，但它们已经等了 backoff。
            self._stop.wait(
                max(0.0, MIN_POLL_INTERVAL_SECONDS - (time.monotonic() - started))
            )

    def _handle_batch(self, host: Host, base_url: str, token: str, response: dict) -> str | None:
        """处理一批消息，**最后**才推进游标。返回新游标，或 None 表示这批没处理完。"""
        for raw in response.get("msgs") or []:
            if raw.get("message_type") != MESSAGE_TYPE_USER:
                continue        # 自己发出去的回声，跳过
            try:
                self._handle_one(host, base_url, token, raw)
            except (HostBusy, HostStopped):
                # 这一轮根本没跑（队列满 / 正在关停）—— 撤回认领，这批不推进游标。
                # 这跟"跑了一半出错"是两回事：那个不能重试，这个可以。
                return None

        next_cursor = response.get("get_updates_buf") or ""
        if next_cursor and next_cursor != self._state.cursor():
            self._state.save_cursor(next_cursor)
        # 游标推进之后才发。回复已经安全落在 outbox 里，所以即使这里发失败，
        # 推进游标也是对的 —— 回合确实完成了，投递由 outbox 单独负责。
        self.flush_outbox(base_url, token)
        return next_cursor or self._state.cursor()

    def _handle_one(self, host: Host, base_url: str, token: str, raw: dict) -> None:
        message_id = str(raw.get("message_id", ""))
        user_id = str(raw.get("from_user_id", ""))
        context_token = str(raw.get("context_token", ""))

        # ---- 1. 先过白名单。这一步在**任何可能碰到 Waku 的动作之前** ——
        # 一个能被任何人触发、还能调工具的 agent 不是一个小问题。拒绝时
        # **不回复**：跟未授权的人对话本身就是错。
        if not self._is_allowed(user_id):
            self._refused += 1
            self._remember_refused(user_id)
            self._decide(message_id, "refused", "sender is not in WAKU_WECHAT_ALLOW")
            return

        # ---- 2. 没有可去重的 ID 就不跑回合。
        # 一个无法去重的消息每重投一次就跑一次，而工具是有副作用的 —— 宁可
        # 明确不处理（status 报出来），也不要“大概只跑了一次”。
        if not message_id:
            self._no_id += 1
            self._decide(message_id, "refused",
                         "no message_id: it cannot be deduplicated, so a turn could run twice")
            return

        if self._state.seen.is_known(message_id):
            self._decide(message_id, "duplicate", "this id was already handled")
            return

        kind = detect_kind(raw.get("item_list"))
        text = extract_text(raw.get("item_list"))

        if not context_token:
            # 没有 context_token 就发不出去，也没有会话可归属。不认领它 ——
            # 认领等于假装处理过，而重投时它还能再试一次。
            self._decide(message_id, "refused", "the message carries no context_token, "
                                               "so there is nowhere to reply")
            return

        self._state.seen.claim(message_id)              # ← 认领在跑回合之前

        try:
            if kind != "text":
                # 非文本/空消息由 channel 自己回一句，不进 Agent 回合。
                reply = NON_TEXT_REPLY if kind != "empty" else EMPTY_TEXT_REPLY
                self._decide(message_id, "handled", f"{kind} message, answered without a turn")
            else:
                # 和浏览器**同一条线**：一个助手、多个门。你在微信说的，回到
                # 浏览器问"我说过什么"就能读到。来源仍然逐行记进 chat_log.source。
                session_id = conversation.session_id_for_turn()
                result = host.ask(text, source=self.name, session_id=session_id)
                # 只有整轮结束后的最终回复才发出去。中间任何流式片段都不发。
                reply = result.reply or ""
                self._handled += 1
                self._decide(message_id, "handled", f"ran a turn ({len(reply)} chars back)")
        except (HostBusy, HostStopped):
            self._state.seen.unclaim(message_id)
            raise
        except Exception as exc:
            # 回合可能跑了一半。认领保留 —— 重试会重复执行有副作用的工具。
            self._failed += 1
            self._decide(message_id, "failed", f"the turn raised: {type(exc).__name__}: {exc}")
            return

        # 回合跑完了。回复先进 outbox，**然后**才把这条消息记为已处理：
        # 那个标记说的是“回复不会丢”，不是“回复发出去了”。
        if reply:
            self._state.outbox.enqueue(message_id, user_id, context_token, reply)
        else:
            self._decide(message_id, "handled", "the turn produced no text; nothing queued")
        self._state.seen.complete(message_id)

    def flush_outbox(self, base_url: str, token: str) -> None:
        """把待发的回复送出去。**只发，不跑回合。**
        逐段确认：一次 flush 里失败的那一段会当场重试几次，而**已经确认的段落
        永远不会被重发**。始终送不出去的留在 outbox 里，由 status 报出来，
        重启后会再试 —— 但回合永远不会为它再跑一次。

        返回后 outbox 里剩下的东西，就是"已产生但尚未确认送达"的全部。
        """
        if self._phase == "expired":
            # session 已经死了：拿同一个 token 重发只会再撞一次墙。回复留在
            # outbox 里等重新登录（那需要重启 serve，因为轮询线程也已经退了）。
            return
        for entry in self._state.outbox.entries():
            aborted = False
            for index, chunk in enumerate(entry.get("chunks") or []):
                if chunk.get("sent"):
                    continue                      # 已确认的段落不重发
                error = self._send_chunk(base_url, token, entry, chunk)
                if error:
                    self._state.outbox.note_attempt(entry["messageId"], error)
                    self._failed += 1
                    self._last_error = (
                        f"sendmessage failed for {entry['messageId']} "
                        f"chunk {index + 1}/{len(entry['chunks'])}: {error}"
                    )
                    aborted = True
                    break
                self._state.outbox.mark_sent(entry["messageId"], index)
            if not aborted:
                self._state.outbox.remove(entry["messageId"])

    def _send_chunk(self, base_url: str, token: str, entry: dict, chunk: dict) -> str:
        """发一段，失败时只重试**这一段**。成功返回空串，失败返回错误文本。

        client_id 是稳定的：即使服务端其实收到了第一次、只是回包丢了，重试也不会
        在它那里变成第二条消息。

        一次 401/-14 意味着整个 session 已经死了，重试同一个 token 没有任何意义 ——
        直接把 phase 标成 expired，让用户去重新扫码。
        """
        last = ""
        for attempt in range(SEND_ATTEMPTS):
            try:
                self._api.post_message(base_url, token, build_text_message(
                    entry["userId"], entry["contextToken"], chunk["text"], chunk["clientId"]))
                return ""
            except ApiError as exc:
                last = str(exc)
                if exc.code == SESSION_EXPIRED_CODE:
                    self._set_phase("expired")
                    self._announce(
                        "WeChat session expired while sending — run `waku wechat login`. "
                        "The reply stays queued and will go out after that."
                    )
                    return last
                if attempt + 1 < SEND_ATTEMPTS:
                    time.sleep(self._send_retry_seconds)
        return last

    def _is_allowed(self, user_id: str) -> bool:
        """白名单空 = 谁都不许。故意 fail closed 而不是 fail open。"""
        return bool(user_id) and user_id in self._allowed

    def _remember_refused(self, user_id: str) -> None:
        if user_id and user_id not in self._refused_senders:
            self._refused_senders.append(user_id)
            del self._refused_senders[:-5]        # 只留最近 5 个，够加白名单用了

    def _decide(self, message_id: str, outcome: str, reason: str) -> None:
        """记下（并第一眼就说一声）这条消息被怎么处理了。

        **静默拒绝是最难查的一类 bug**：消息被取走了、没有回复、什么也不说。
        落盘是为了让另一个进程的 `waku wechat status` 能回答“它为什么不回我”，
        第一眼就 announce 是为了当场就能看到。同一种结果只报一次，不刷屏。
        """
        self._state.decisions.record(outcome, message_id, reason)
        key = f"{outcome}:{reason}"
        if key not in self._announced_decisions:
            self._announced_decisions.add(key)
            self._announce(f"{outcome} — {reason}")

    def _announce(self, message: str) -> None:
        """说一句给用户听。**不带凭据、不带正文。** 用户需要知道它掉线了，
        而后台的断线重试本身不该把日志刷爆。"""
        self._announce_fn(message)

    def _announce_once(self, message: str) -> None:
        """同一个失败连续发生只报一次，直到恢复。"""
        if not self._announced_failure:
            self._announced_failure = True
            self._announce(message)

    def _note(self, message: str) -> None:
        """告知型的一行（重复消息、非文本…）。**不带凭据、不带正文** ——
        这句可能被贴到 issue 里。"""
        self._last_note = message


# ------------------------------------------------------------------- 组装


def state_directory(home: Path | None = None) -> Path:
    resolved = home or load_settings().home
    return Path(resolved) / "wechat"


def allowed_senders(settings) -> set[str]:
    """`WAKU_WECHAT_ALLOW` 里的微信用户 id。**空 = 谁都不许（fail closed）。**"""
    return {part.strip() for part in (settings.wechat_allow or "").split(",") if part.strip()}


def from_environment() -> WeChatGateway | None:
    """`WAKU_WECHAT=1` 才返回一个 gateway；默认返回 None。

    默认关闭是有意的：一个没配好的 channel 不该在每次 `waku serve` 时去连一次
    外网。开了但没登录也不报错 —— 那是一个正常状态，由 status 说清楚。
    """
    if not load_settings().wechat:
        return None
    settings = load_settings()
    return WeChatGateway(GatewayState(state_directory()), allowed=allowed_senders(settings))


# ---------------------------------------------------------------------- CLI


def render_qr(url: str) -> None:
    """终端里画二维码。缺 qrcode 就退回打印 URL，不崩。"""
    try:
        import qrcode
    except ImportError:
        print("qrcode is not installed — `pip install 'waku-agent[wechat]'`,")
        print("or encode this URL as a QR yourself:")
        print(url)
        return
    code = qrcode.QRCode(border=1, error_correction=qrcode.constants.ERROR_CORRECT_L)
    code.add_data(url)
    code.make(fit=True)
    code.print_ascii(invert=True)


def qr_svg(url: str) -> str:
    """把二维码画成 SVG 给浏览器看。

    缺 qrcode 就抛 RuntimeError（带安装提示）而不是崩 —— 它是 `[wechat]` extra，
    不是默认依赖。SVG 而不是 PNG：qrcode 自带的 svg 工厂是纯 Python，PNG 要 pillow。
    """
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError as exc:
        raise RuntimeError(
            "qrcode is not installed — pip install 'waku-agent[wechat]'"
        ) from exc
    code = qrcode.QRCode(border=1, error_correction=qrcode.constants.ERROR_CORRECT_L,
                         image_factory=qrcode.image.svg.SvgPathImage)
    code.add_data(url)
    code.make(fit=True)
    body = code.make_image().to_string().decode("utf-8")
    # qrcode 画的是黑模块 + 透明底，在深色主题下就是一坨看不见的黑。
    # 白底必须跟着二维码走，不能靠 CSS —— 设计系统不允许 CSS 写颜色，而且
    # 对比度该属于这个 asset，不属于页面主题（换主题不该让码扫不动）。
    head, _, rest = body.partition(">")
    return f'{head}><rect width="100%" height="100%" fill="#ffffff"/>{rest}'


# ------------------------------------------------------------- 登录流程
#
# 拆成小块，因为有两个驱动者：CLI 自己循环，dashboard 的 HTTP 请求每次只推进一步。
# **协议只实现一遍** —— 浏览器那条路不允许有第二份取码 / 轮询 / 落盘的逻辑。


def begin_login(api: ILinkApi | None = None) -> dict:
    """取一张登录二维码。返回 `{"qrcode": token, "url": ...}`；失败抛 ApiError。"""
    qr = (api or ILinkApi()).fetch_qr_code()
    token = str(qr.get("qrcode", ""))
    if not token:
        raise ApiError(f"the QR endpoint returned no token: {qr}")
    return {"qrcode": token, "url": str(qr.get("qrcode_img_content", ""))}


def poll_login(qrcode: str, state: GatewayState | None = None,
               api: ILinkApi | None = None) -> dict:
    """问一次扫码状态；确认了就落盘凭据。

    返回的 dict **绝不含 bot_token**，因为它会被原样送进 HTTP 响应。
    """
    state = state or GatewayState(state_directory())
    status = (api or ILinkApi()).fetch_qr_status(qrcode)
    name = str(status.get("status", "wait"))
    if name == "confirmed":
        token = str(status.get("bot_token", ""))
        if not token:
            raise ApiError("confirmed but the response carried no bot_token")
        state.save_credentials({
            "token": token,
            "baseUrl": status.get("baseurl") or BASE_URL,
            "accountId": status.get("ilink_bot_id", ""),
            "userId": status.get("ilink_user_id", ""),
            "savedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        })
        state.clear_cursor()      # 换了身份，旧的流位置没有意义
    return {
        "status": name,
        "accountId": str(status.get("ilink_bot_id", "")),
        "userId": str(status.get("ilink_user_id", "")),
    }


def clear_login(state: GatewayState | None = None) -> None:
    """忘掉凭据和游标。去重记录留着，这样重投不会重放旧消息。"""
    (state or GatewayState(state_directory())).clear_credentials()


def cmd_login(force: bool = False, timeout: float = 180.0) -> int:
    state = GatewayState(state_directory())
    credentials = state.credentials()
    if credentials and not force:
        print(f"already logged in as {credentials.get('accountId', '')} "
              f"({state.credentials_path})")
        print("use `waku wechat login --force` to scan again")
        return 0

    try:
        pending = begin_login()
    except ApiError as exc:
        print(f"could not fetch a QR code: {exc}")
        return 1

    print("scan this with WeChat, then confirm on the phone:")
    render_qr(pending["url"])
    print(f"or encode this URL yourself: {pending['url']}")
    print("waiting for the scan (each status call holds ~30s)…")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = poll_login(pending["qrcode"], state)
        except ApiError as exc:
            print(f"status check failed: {exc}; retrying")
            time.sleep(RETRY_BASE_SECONDS)
            continue
        if result["status"] == "expired":
            print("the QR expired — run `waku wechat login` again")
            return 1
        if result["status"] == "confirmed":
            print(f"logged in — credentials saved to {state.credentials_path}")
            print(f"accountId={result['accountId']}")
            bound = result["userId"]
            if bound:
                # 白名单**不替你写** —— 静默改安全配置比多敲一行糟得多。
                print()
                print("now allow your own WeChat account to talk to it — add this to .env:")
                print("  WAKU_WECHAT=1")
                print(f"  WAKU_WECHAT_ALLOW={bound}")
                print("(nobody can talk to it until that line is there — it fails closed)")
            return 0
        time.sleep(0.5)

    print("gave up waiting for the scan")
    return 1


def cmd_status() -> int:
    settings = load_settings()
    state = GatewayState(state_directory())
    credentials = state.credentials()
    allowed = allowed_senders(settings)
    print(f"enabled     : {'yes' if settings.wechat else 'no'}  (set WAKU_WECHAT=1 in .env)")
    print(f"state dir   : {state.directory}")
    print(f"logged in   : {'yes' if credentials else 'no'}")
    if credentials:
        print(f"  accountId : {credentials.get('accountId', '')}")
        print(f"  userId    : {credentials.get('userId', '')}")
        print(f"  savedAt   : {credentials.get('savedAt', '')}")
    # 白名单是这一版最重要的一个开关，所以放得很显眼。
    if allowed:
        print(f"allowed     : {len(allowed)} sender(s)")
        for sender in sorted(allowed):
            print(f"              {sender}")
    else:
        print("allowed     : NONE — nobody can talk to this bot (it fails closed)")
        bound = (credentials or {}).get("userId", "")
        if bound:
            print(f"              add to .env:  WAKU_WECHAT_ALLOW={bound}")
    cursor = state.cursor()
    print(f"cursor      : {cursor[:32] + '…' if len(cursor) > 32 else cursor or '(empty)'}")
    unfinished = list(_read_json(state.seen.path, {}).get("claimed") or [])
    if unfinished:
        print(f"interrupted : {len(unfinished)} message(s) were claimed but never finished;")
        print("              they will NOT be retried (a retry could repeat tool side effects)")
        for message_id in unfinished[:5]:
            print(f"              {message_id}")
    pending = state.outbox.summary()
    if pending:
        print(f"undelivered : {len(pending)} reply(ies) the agent produced but WeChat has")
        print("              not confirmed. They are re-sent on the next `waku serve`,")
        print("              and the turn is NOT re-run for them:")
        for entry in pending[:5]:
            print(f"              {entry['messageId']}  "
                  f"{entry['sentChunks']}/{entry['totalChunks']} chunks sent, "
                  f"{entry['attempts']} attempt(s)")
            if entry["lastError"]:
                print(f"                last error: {entry['lastError']}")
    # “它为什么不回我” 得有个能查的地方。计数器在另一个进程的内存里，所以这里读的是
    # 网关落盘的处理决定。
    decisions = state.decisions.recent()
    if decisions:
        print("recent      : what happened to the last inbound messages")
        for entry in decisions[-5:]:
            print(f"              {entry['at'][11:19]}  {entry['messageId']:>20}  "
                  f"{entry['outcome']:9} {entry['reason']}")
    return 0


def cmd_logout() -> int:
    state = GatewayState(state_directory())
    clear_login(state)
    print(f"credentials and cursor removed from {state.directory}")
    print("(the dedup record is kept, so a re-delivery cannot replay an old message)")
    return 0


def cli_main(argv: list | None = None) -> int:
    args = list(argv or [])
    command = args[0] if args else "status"
    if command == "login":
        return cmd_login(force="--force" in args[1:])
    if command == "logout":
        return cmd_logout()
    if command == "status":
        return cmd_status()
    print("usage: waku wechat [login [--force] | status | logout]")
    return 1


# host 用不到这个，但 `python -m waku.gateway.wechat` 方便手动跑一次状态。
if __name__ == "__main__":
    import sys

    sys.exit(cli_main(sys.argv[1:]))
