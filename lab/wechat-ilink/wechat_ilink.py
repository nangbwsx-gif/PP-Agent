#!/usr/bin/env python3
"""WeChat iLink Bot, on its own terms — login, receive, reply, reconnect.

This is a standalone experiment. Nothing here imports Waku and nothing imports
this: `lab/` never ships and `waku/` never reaches into it (conventions §6).

PROTOCOL
    WeChat iLink Bot (微信 iLink Bot) — HTTP+JSON against
    https://ilinkai.weixin.qq.com. The endpoints are `/ilink/bot/get_bot_qrcode`,
    `/ilink/bot/get_qrcode_status`, `/ilink/bot/getupdates`,
    `/ilink/bot/sendmessage`, `/ilink/bot/getconfig`, `/ilink/bot/sendtyping`.
    Every request carries `AuthorizationType: ilink_bot_token` plus
    `Authorization: Bearer <token>`; every POST body carries
    `base_info.channel_version`. A reply is only accepted with the
    `context_token` that arrived on the message it answers.

    Read from pi-wechat 0.1.0 (github.com/yangyang0507/pi-wechat @ 8f351ce),
    which documents the same six endpoints in api.ts.

VERIFIED AGAINST
    pi-wechat 0.1.0 (2026-03-23) · ilinkai API, 2026-09-23.
    Python: stdlib plus `pip install qrcode` for the terminal QR rendering.

CREDENTIALS
    Live in ~/.waku-wechat-lab/, outside the repo: credentials.json (mode 0600),
    cursor.json (the long-poll stream position, so a restart resumes), and
    received.jsonl (every inbound message, as evidence). Nothing is committed.

USAGE
    python lab/wechat-ilink/wechat_ilink.py login          # scan the QR
    python lab/wechat-ilink/wechat_ilink.py poll           # receive and reply
    python lab/wechat-ilink/wechat_ilink.py status
    python lab/wechat-ilink/wechat_ilink.py logout
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path

BASE_URL = "https://ilinkai.weixin.qq.com"
CHANNEL_VERSION = "1.0.0"

STATE_DIR = Path(os.path.expanduser("~")) / ".waku-wechat-lab"
CREDENTIALS_PATH = STATE_DIR / "credentials.json"
CURSOR_PATH = STATE_DIR / "cursor.json"
RECEIVED_PATH = STATE_DIR / "received.jsonl"

# From pi-wechat api.ts: getupdates is allowed 40s; our own calls are bounded.
GET_UPDATES_TIMEOUT = 45
QR_STATUS_TIMEOUT = 40
SHORT_TIMEOUT = 20

# get_qrcode_status holds the connection until something happens (measured ~30s
# and then answering {"status":"wait"}), so "polling" means calling it in a loop
# rather than sleeping between calls.
QR_STATUS_LONG_POLL_SECONDS = 30

# A reply must fit; pi-wechat splits at 2000 characters.
MAX_TEXT_CHUNK = 2000

MESSAGE_TYPE_USER = 1
MESSAGE_TYPE_BOT = 2
MESSAGE_STATE_FINISH = 2
ITEM_TYPE_TEXT = 1

# The one error code that means "this token is dead, log in again".
SESSION_EXPIRED_CODE = -14

RETRY_BASE_SECONDS = 1.0
RETRY_MAX_SECONDS = 10.0


class ApiError(Exception):
    """A failed call, carrying enough of the response to act on it."""

    def __init__(self, message, *, status=0, code=None, payload=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.payload = payload


# ---------------------------------------------------------------- HTTP


def _wechat_uin() -> str:
    """pi-wechat sends a random uint32, base64-encoded, as X-WECHAT-UIN."""
    return base64.b64encode(str(random.getrandbits(32)).encode()).decode()


def _auth_headers(token: str) -> dict:
    return {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Authorization": f"Bearer {token}",
        "X-WECHAT-UIN": _wechat_uin(),
    }


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
    except Exception as exc:  # URLError, TimeoutError, ssl errors, ...
        raise ApiError(f"network error: {exc}") from exc

    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        raise ApiError(
            f"expected JSON, got HTTP {status}: {raw[:160]!r}", status=status
        ) from None

    if not 200 <= status < 300:
        raise ApiError(
            payload.get("errmsg") or f"HTTP {status}", status=status,
            code=payload.get("errcode"), payload=payload,
        )
    # The API reports business failures as ret != 0 with HTTP 200.
    if isinstance(payload.get("ret"), int) and payload["ret"] != 0:
        raise ApiError(
            payload.get("errmsg") or f"ret={payload['ret']}", status=status,
            code=payload.get("errcode", payload["ret"]), payload=payload,
        )
    return payload


def _base_info() -> dict:
    return {"channel_version": CHANNEL_VERSION}


def fetch_qr_code(base_url: str = BASE_URL) -> dict:
    """No credentials needed — this is the first call in the whole flow."""
    return _call(f"{base_url}/ilink/bot/get_bot_qrcode?bot_type=3", timeout=SHORT_TIMEOUT)


def fetch_qr_status(qrcode: str, base_url: str = BASE_URL) -> dict:
    return _call(
        f"{base_url}/ilink/bot/get_qrcode_status?qrcode={urllib.parse.quote(qrcode)}",
        headers={"iLink-App-ClientVersion": "1"},
        timeout=QR_STATUS_TIMEOUT,
    )


def fetch_updates(base_url: str, token: str, cursor: str) -> dict:
    return _call(
        f"{base_url}/ilink/bot/getupdates",
        data={"get_updates_buf": cursor, "base_info": _base_info()},
        headers=_auth_headers(token),
        timeout=GET_UPDATES_TIMEOUT,
    )


def post_message(base_url: str, token: str, message: dict) -> dict:
    return _call(
        f"{base_url}/ilink/bot/sendmessage",
        data={"msg": message, "base_info": _base_info()},
        headers=_auth_headers(token),
        timeout=SHORT_TIMEOUT,
    )


def fetch_config(base_url: str, token: str, user_id: str, context_token: str) -> dict:
    return _call(
        f"{base_url}/ilink/bot/getconfig",
        data={
            "ilink_user_id": user_id,
            "context_token": context_token,
            "base_info": _base_info(),
        },
        headers=_auth_headers(token),
        timeout=SHORT_TIMEOUT,
    )


def post_typing(base_url: str, token: str, user_id: str, ticket: str, status: int) -> dict:
    return _call(
        f"{base_url}/ilink/bot/sendtyping",
        data={
            "ilink_user_id": user_id,
            "typing_ticket": ticket,
            "status": status,
            "base_info": _base_info(),
        },
        headers=_auth_headers(token),
        timeout=SHORT_TIMEOUT,
    )


# ---------------------------------------------------------------- messages


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
    """Text items only. Images, voice, files and video are placeholders here."""
    parts = [
        str(item.get("text_item", {}).get("text", "")).strip()
        for item in item_list or []
        if item.get("type") == ITEM_TYPE_TEXT
    ]
    return "\n".join(part for part in parts if part)


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


# ---------------------------------------------------------------- state


def _write_json(path: Path, payload: dict, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if mode is not None:
        try:
            path.chmod(mode)
        except OSError:
            pass  # Windows: mode bits are advisory here, and the repo is not the store.


def load_credentials() -> dict | None:
    try:
        return json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_cursor() -> str:
    try:
        return json.loads(CURSOR_PATH.read_text(encoding="utf-8")).get("cursor", "")
    except (OSError, json.JSONDecodeError):
        return ""


def save_cursor(cursor: str) -> None:
    """Persist the stream position, which is what makes a restart resume rather
    than replay. Written before the messages are handled, so a crash mid-handle
    resumes after them instead of replying twice."""
    _write_json(CURSOR_PATH, {"cursor": cursor})


def append_received(record: dict) -> None:
    RECEIVED_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RECEIVED_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _log(message: str) -> None:
    """Timestamps matter here: the whole point is observing the sequence."""
    stamp = datetime.now(UTC).astimezone().strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


# ---------------------------------------------------------------- commands


def render_qr(url: str) -> None:
    try:
        import qrcode
    except ImportError:
        print(
            "qrcode not installed — run: pip install qrcode\n"
            f"Meanwhile, encode this URL as a QR yourself:\n{url}",
            flush=True,
        )
        return
    qr = qrcode.QRCode(border=1, error_correction=qrcode.constants.ERROR_CORRECT_L)
    qr.add_data(url)
    qr.make(fit=True)
    # Half-block rendering keeps a ~90-character URL readable in a terminal.
    qr.print_ascii(invert=True)


def cmd_login(args) -> int:
    if CREDENTIALS_PATH.exists() and not args.force:
        _log(f"credentials already saved: {CREDENTIALS_PATH}")
        _log("use --force to log in again")
        return 0

    qr = fetch_qr_code()
    token = qr.get("qrcode", "")
    url = qr.get("qrcode_img_content", "")
    if not token:
        _log(f"the QR endpoint returned no token: {qr}")
        return 1

    _log("scan this with WeChat, then confirm on the phone:")
    render_qr(url)
    _log(f"or encode this URL yourself: {url}")
    _log(f"waiting for the scan (each status call holds ~{QR_STATUS_LONG_POLL_SECONDS}s)...")

    last = None
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        try:
            status = fetch_qr_status(token)
        except ApiError as exc:
            _log(f"status check failed: {exc}; retrying")
            time.sleep(RETRY_BASE_SECONDS)
            continue

        state = status.get("status")
        if state != last:
            _log(f"status: {state}")
            last = state

        if state == "scaned":
            continue
        if state == "expired":
            _log("the QR expired — run login again")
            return 1
        if state == "confirmed":
            if not status.get("bot_token"):
                _log(f"confirmed but no bot_token in the response: {status}")
                return 1
            credentials = {
                "token": status["bot_token"],
                "baseUrl": status.get("baseurl") or BASE_URL,
                "accountId": status.get("ilink_bot_id", ""),
                "userId": status.get("ilink_user_id", ""),
                "savedAt": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            _write_json(CREDENTIALS_PATH, credentials, mode=0o600)
            _log(f"logged in — credentials saved to {CREDENTIALS_PATH}")
            _log(f"accountId={credentials['accountId']} userId={credentials['userId']}")
            return 0
        time.sleep(0.5)

    _log("gave up waiting for the scan")
    return 1


def _reply_text(template: str, text: str) -> str:
    return template.replace("{text}", text)


def cmd_poll(args) -> int:
    credentials = load_credentials()
    if not credentials:
        _log(f"no credentials at {CREDENTIALS_PATH} — run: login")
        return 1

    base_url = credentials.get("baseUrl") or BASE_URL
    token = credentials.get("token", "")
    cursor = load_cursor()
    _log(f"polling {base_url} (accountId={credentials.get('accountId', '')})")
    _log(f"resuming from saved cursor: {cursor!r}" if cursor else "starting from an empty cursor")
    _log("send a WeChat message to the bot to test the bridge")

    backoff = RETRY_BASE_SECONDS
    rounds = 0

    while args.rounds == 0 or rounds < args.rounds:
        rounds += 1
        try:
            response = fetch_updates(base_url, token, cursor)
        except ApiError as exc:
            if exc.code == SESSION_EXPIRED_CODE:
                _log(f"session expired (errcode {SESSION_EXPIRED_CODE}) — run: login --force")
                return 2
            _log(f"getupdates failed: {exc} — retrying in {backoff:.0f}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, RETRY_MAX_SECONDS)
            continue

        backoff = RETRY_BASE_SECONDS
        new_cursor = response.get("get_updates_buf") or cursor
        if new_cursor != cursor:
            cursor = new_cursor
            save_cursor(cursor)

        for raw in response.get("msgs") or []:
            if raw.get("message_type") != MESSAGE_TYPE_USER:
                continue

            user_id = raw.get("from_user_id", "")
            context_token = raw.get("context_token", "")
            text = extract_text(raw.get("item_list"))
            record = {
                "receivedAt": datetime.now(UTC).isoformat(timespec="seconds"),
                "messageId": str(raw.get("message_id", "")),
                "userId": user_id,
                "text": text,
                "createTimeMs": raw.get("create_time_ms"),
            }
            append_received(record)
            _log(f"received from {user_id}: {text!r}")

            if not (user_id and context_token):
                _log("  no user id or context token on the message — cannot reply")
                continue

            reply = _reply_text(args.reply, text)
            try:
                for chunk in chunk_text(reply):
                    post_message(base_url, token, build_text_message(user_id, context_token, chunk))
                _log(f"  replied: {reply!r}")
            except ApiError as exc:
                _log(f"  sendmessage failed: {exc} (code={exc.code})")

            if args.typing:
                try:
                    config = fetch_config(base_url, token, user_id, context_token)
                    if config.get("typing_ticket"):
                        post_typing(base_url, token, user_id, config["typing_ticket"], 1)
                        _log("  typing indicator accepted")
                except ApiError as exc:
                    _log(f"  typing indicator failed: {exc}")

        if not response.get("msgs"):
            _log(f"no messages (cursor now {cursor[:24]!r})")

    return 0


def cmd_status(_args) -> int:
    credentials = load_credentials()
    print(f"state dir   : {STATE_DIR}")
    print(f"credentials : {'present' if credentials else 'missing'} ({CREDENTIALS_PATH})")
    if credentials:
        print(f"  accountId : {credentials.get('accountId', '')}")
        print(f"  userId    : {credentials.get('userId', '')}")
        print(f"  baseUrl   : {credentials.get('baseUrl', '')}")
        print(f"  savedAt   : {credentials.get('savedAt', '')}")
    print(f"cursor      : {load_cursor()!r}")
    if RECEIVED_PATH.exists():
        lines = RECEIVED_PATH.read_text(encoding="utf-8").strip().splitlines()
        print(f"received    : {len(lines)} message(s) logged to {RECEIVED_PATH}")
    return 0


def cmd_logout(_args) -> int:
    for path in (CREDENTIALS_PATH, CURSOR_PATH):
        try:
            path.unlink()
            print(f"removed {path}")
        except FileNotFoundError:
            pass
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="QR login; credentials land outside the repo")
    login.add_argument("--force", action="store_true", help="log in even if credentials exist")
    login.add_argument("--timeout", type=float, default=180.0, help="seconds to wait for the scan")
    login.set_defaults(func=cmd_login)

    poll = sub.add_parser("poll", help="long-poll, print incoming text, reply to each")
    poll.add_argument("--reply", default="[wechat-ilink lab] received: {text}",
                      help="reply template; {text} is replaced by what arrived")
    poll.add_argument("--rounds", type=int, default=0, help="stop after N polls (0 = forever)")
    poll.add_argument("--typing", action="store_true", help="also exercise getconfig/sendtyping")
    poll.set_defaults(func=cmd_poll)

    sub.add_parser("status", help="show credentials and the saved cursor").set_defaults(func=cmd_status)
    sub.add_parser("logout", help="delete local credentials and cursor").set_defaults(func=cmd_logout)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        _log("interrupted")
        return 130
    except ApiError as exc:
        _log(f"API error: {exc} (status={exc.status} code={exc.code})")
        if exc.payload:
            _log(f"payload: {json.dumps(exc.payload, ensure_ascii=False)[:400]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
