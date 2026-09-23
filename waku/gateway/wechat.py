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
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from waku.config import load_settings
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
    if isinstance(payload.get("ret"), int) and payload["ret"] != 0:
        raise ApiError(
            payload.get("err_msg") or payload.get("errmsg") or f"ret={payload['ret']}",
            status=status,
            code=payload.get("errcode", payload["ret"]),
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


def build_text_message(user_id: str, context_token: str, text: str) -> dict:
    return {
        "from_user_id": "",
        "to_user_id": user_id,
        "client_id": str(uuid.uuid4()),
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
                 poll_interval: float = RETRY_BASE_SECONDS,
                 send_retry_seconds: float = SEND_RETRY_SECONDS,
                 announce: Callable[[str], None] | None = None):
        self._state = state
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
        # 上一次跑崩在回合中间的那些 id。start() 时取一次，之后由 status 报告。
        self._interrupted: list[str] = []
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
            }

    # ------------------------------------------------------------ 长轮询

    def _poll_forever(self, host: Host, credentials: dict) -> None:
        base_url = credentials.get("baseUrl") or BASE_URL
        token = credentials.get("token", "")
        cursor = self._state.cursor()
        backoff = self._poll_interval

        while not self._stop.is_set():
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
            backoff = self._poll_interval
            cursor = self._handle_batch(host, base_url, token, response)
            if cursor is None:
                # 本批没能处理完（那一轮没跑成）。游标原地不动，下一轮整批重投，
                # 靠 message_id 认领去重。
                self._stop.wait(self._poll_interval)
                cursor = self._state.cursor()

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
        return next_cursor or self._state.cursor()

    def _handle_one(self, host: Host, base_url: str, token: str, raw: dict) -> None:
        message_id = str(raw.get("message_id", ""))
        user_id = str(raw.get("from_user_id", ""))
        context_token = str(raw.get("context_token", ""))
        kind = detect_kind(raw.get("item_list"))
        text = extract_text(raw.get("item_list"))

        if message_id and self._state.seen.is_known(message_id):
            self._note(f"duplicate {message_id} ignored")
            return

        if not (user_id and context_token):
            # 没有 context_token 就发不出去，也没有会话可归属。不认领它 ——
            # 认领等于假装处理过，而重投时它还能再试一次。
            self._note(f"message {message_id} has no user id or context token; skipped")
            return

        if message_id:
            self._state.seen.claim(message_id)          # ← 认领在跑回合之前

        try:
            if kind != "text":
                # 非文本/空消息由 channel 自己回一句，不进 Agent 回合。
                reply = NON_TEXT_REPLY if kind != "empty" else EMPTY_TEXT_REPLY
                self._note(f"{kind} message {message_id} — answered without a turn")
            else:
                session_id = f"{self.name}-{user_id}"   # 稳定的微信会话 id
                result = host.ask(text, source=self.name, session_id=session_id)
                # 只有整轮结束后的最终回复才发出去。中间任何流式片段都不发。
                reply = result.reply or ""
                self._handled += 1

            self._deliver(base_url, token, user_id, context_token, reply, message_id)
        except (HostBusy, HostStopped):
            if message_id:
                self._state.seen.unclaim(message_id)
            raise
        except Exception as exc:
            # 回合可能跑了一半。认领保留 —— 重试会重复执行有副作用的工具。
            self._failed += 1
            self._last_error = f"message {message_id}: {type(exc).__name__}: {exc}"
            return

        if message_id:
            self._state.seen.complete(message_id)

    def _deliver(self, base_url: str, token: str, user_id: str,
                 context_token: str, text: str, message_id: str) -> None:
        """发回复。**只有发送会重试**，回合不会 —— 这是两件不同的事。

        重发最坏是多一条重复回复；重跑回合会重复执行有副作用的工具。
        """
        if not text:
            self._note(f"turn for {message_id} produced no text; nothing sent")
            return
        last = ""
        for attempt in range(SEND_ATTEMPTS):
            try:
                for chunk in chunk_text(text):
                    self._api.post_message(
                        base_url, token, build_text_message(user_id, context_token, chunk)
                    )
                return
            except ApiError as exc:
                last = str(exc)
                if attempt + 1 < SEND_ATTEMPTS:
                    time.sleep(self._send_retry_seconds)
        self._failed += 1
        self._last_error = f"sendmessage failed for {message_id}: {last}"

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


def from_environment() -> WeChatGateway | None:
    """`WAKU_WECHAT=1` 才返回一个 gateway；默认返回 None。

    默认关闭是有意的：一个没配好的 channel 不该在每次 `waku serve` 时去连一次
    外网。开了但没登录也不报错 —— 那是一个正常状态，由 status 说清楚。
    """
    if not load_settings().wechat:
        return None
    return WeChatGateway(GatewayState(state_directory()))


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


def cmd_login(force: bool = False, timeout: float = 180.0) -> int:
    state = GatewayState(state_directory())
    if state.credentials() and not force:
        print(f"already logged in ({state.credentials_path})")
        print("use `waku wechat login --force` to scan again")
        return 0

    api = ILinkApi()
    try:
        qr = api.fetch_qr_code()
    except ApiError as exc:
        print(f"could not fetch a QR code: {exc}")
        return 1

    token, url = qr.get("qrcode", ""), qr.get("qrcode_img_content", "")
    if not token:
        print(f"the QR endpoint returned no token: {qr}")
        return 1

    print("scan this with WeChat, then confirm on the phone:")
    render_qr(url)
    print(f"or encode this URL yourself: {url}")
    print("waiting for the scan (each status call holds ~30s)…")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status = api.fetch_qr_status(token)
        except ApiError as exc:
            print(f"status check failed: {exc}; retrying")
            time.sleep(RETRY_BASE_SECONDS)
            continue
        state_name = status.get("status")
        if state_name == "expired":
            print("the QR expired — run `waku wechat login` again")
            return 1
        if state_name == "confirmed":
            if not status.get("bot_token"):
                print(f"confirmed but no bot_token in the response: {status}")
                return 1
            state.save_credentials({
                "token": status["bot_token"],
                "baseUrl": status.get("baseurl") or BASE_URL,
                "accountId": status.get("ilink_bot_id", ""),
                "userId": status.get("ilink_user_id", ""),
                "savedAt": datetime.now(UTC).isoformat(timespec="seconds"),
            })
            state.clear_cursor()      # 换了身份，旧的流位置没有意义
            print(f"logged in — credentials saved to {state.credentials_path}")
            print(f"accountId={status.get('ilink_bot_id', '')}")
            print("set WAKU_WECHAT=1 in .env to have `waku serve` start the gateway")
            return 0
        time.sleep(0.5)

    print("gave up waiting for the scan")
    return 1


def cmd_status() -> int:
    settings = load_settings()
    state = GatewayState(state_directory())
    credentials = state.credentials()
    print(f"enabled     : {'yes' if settings.wechat else 'no'}  (set WAKU_WECHAT=1 in .env)")
    print(f"state dir   : {state.directory}")
    print(f"logged in   : {'yes' if credentials else 'no'}")
    if credentials:
        print(f"  accountId : {credentials.get('accountId', '')}")
        print(f"  userId    : {credentials.get('userId', '')}")
        print(f"  savedAt   : {credentials.get('savedAt', '')}")
    cursor = state.cursor()
    print(f"cursor      : {cursor[:32] + '…' if len(cursor) > 32 else cursor or '(empty)'}")
    unfinished = list(_read_json(state.seen.path, {}).get("claimed") or [])
    if unfinished:
        print(f"interrupted : {len(unfinished)} message(s) were claimed but never finished;")
        print("              they will NOT be retried (a retry could repeat tool side effects)")
        for message_id in unfinished[:5]:
            print(f"              {message_id}")
    return 0


def cmd_logout() -> int:
    state = GatewayState(state_directory())
    state.clear_credentials()
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
