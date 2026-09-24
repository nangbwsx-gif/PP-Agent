# WeChat iLink Bot

One WeChat account, one bot, text only: scan a QR, long-poll for messages, reply
in the same conversation, and pick the stream back up after a restart.

## The question

What does WeChat's own bot channel (iLink, the protocol behind the WeChat
ClawBot plugin) actually give you when you call it directly, and does a text
round trip survive a process restart?

## What we connect

Nothing from Waku. `wechat_ilink.py` speaks to `https://ilinkai.weixin.qq.com`
with the standard library plus `qrcode` for the terminal QR. No Waku agent,
memory, tools or skills are in the loop: this measures the channel on its own
terms before anything is built on top of it.

It is a channel, not a model. Incoming text is echoed back with a fixed prefix,
which is all a first experiment needs to prove the pipe works in both
directions.

Verified against: pi-wechat 0.1.0 (github.com/yangyang0507/pi-wechat, commit 8f351ce) and the live ilinkai API, 2026-09-23.

## Run it

```bash
pip install qrcode          # the only dependency; everything else is stdlib
python lab/wechat-ilink/wechat_ilink.py login     # scan, then confirm on the phone
python lab/wechat-ilink/wechat_ilink.py poll      # receive and reply, forever
```

`login` prints the QR in the terminal and also prints the URL it encodes, in
case the code is hard to scan off a screen. `poll` runs until interrupted;
`--rounds N` stops it after N polls, which is what the tests below used.
`status` and `logout` are the other two commands.

State lives outside the repo, in `~/.waku-wechat-lab/`: `credentials.json`
(mode 0600, though Windows reports 644 and relies on the profile ACL instead),
`cursor.json`, and `received.jsonl` with every inbound message as evidence.
Nothing here is committed and nothing here is read by `waku/`.

## What we found

### The protocol

Six endpoints, all JSON over HTTPS. Everything except the QR pair needs a bearer
token.

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `/ilink/bot/get_bot_qrcode?bot_type=3` | GET | none | start a login session |
| `/ilink/bot/get_qrcode_status?qrcode=` | GET | `iLink-App-ClientVersion: 1` | poll the scan (long-poll) |
| `/ilink/bot/getupdates` | POST | bearer | long-poll for messages |
| `/ilink/bot/sendmessage` | POST | bearer | send text |
| `/ilink/bot/getconfig` | POST | bearer | fetch the typing ticket |
| `/ilink/bot/sendtyping` | POST | bearer | show/hide the typing state |

Every authorised request carries `AuthorizationType: ilink_bot_token`,
`Authorization: Bearer <bot_token>`, and `X-WECHAT-UIN` (base64 of a random
uint32, regenerated per request). Every POST body carries
`base_info: { channel_version: "1.0.0" }`.

The reply model is the part that differs from every other chat platform: a
message is answered by echoing back its `context_token`, which is a
per-conversation capability token rather than an identity. Knowing only
`to_user_id` is not enough to reply.

### Things the code does not tell you

These came out of running it, not out of reading pi-wechat.

- **`bot_type=3` is the only accepted value.** 1, 2 and 4 all return
  `{"err_msg":"invalid bot_type","ret":2}`.
- **The error field is `err_msg`, and the code is `ret`.** pi-wechat's
  `parseJsonResponse` reads `errmsg` and `errcode`, so for this class of failure
  it reports an empty message. This is the one place our reading of pi-wechat
  and the live API disagree, and it is worth knowing before porting that code.
- **`get_qrcode_status` long-polls.** Measured 30.13s before it answered
  `{"status":"wait"}` with nobody scanning. pi-wechat calls it every 2 seconds,
  so each of those calls actually blocks for about half a minute.
- **The observed transition was `wait` then straight to `confirmed`.** The
  `scaned` state is in the documented state machine but never appeared in the
  run below.
- **`getupdates` also holds the connection.** With no traffic it returned after
  about 18s each time, well under the 35s the protocol reference mentions.
- **But it does not always hold.** On 2026-09-24 the same call started returning
  immediately with an empty batch and an unchanged cursor, and a poll loop that
  trusted the hold spun at 12 requests per second: one process left running
  overnight sent roughly 700,000 requests in 16 hours. The account looked dead
  from the phone — messages arrived but nothing picked them up — while the
  server was in fact still answering. `wechat_ilink.py` now enforces a minimum
  interval between polls instead of trusting the server to pace the loop.
- **The cursor is the whole restart story.** `get_updates_buf` is opaque, and it
  carries the bot identity inside it (base64-decoded, it contains
  `<bot_id>:0600...`). Persisting it is what makes a restart resume rather than
  replay.

### The round trip, and the restart (2026-09-23)

```
16:42:47  QR fetched and rendered
16:43:17  status: wait
16:43:48  status: confirmed, credentials saved (accountId 3cb4f0...@im.bot)
16:44:10  poll started from an empty cursor
16:44:50  message 1 received, replied, typing indicator accepted
16:46:14  process killed and restarted, resumed from the saved cursor
16:50:17  message 2 received, replied
```

Both acceptance points were met: WeChat text reached a local process that
answered in the same conversation, and the connection came back after a restart
without replaying the message it had already handled. `received.jsonl` holds
both messages with distinct `message_id` values.

### Can one WeChat account run several iLink bots?

No. One WeChat account binds one bot at a time, and scanning again replaces the
binding rather than adding to it. Three independent sources agree:

- The protocol reference's re-login procedure says to delete or overwrite the
  old credential file, and to clear the cursor; the credential set that changes
  on re-login includes `ilink_bot_id`.
- Its credential-refresh section states plainly that "refresh" means obtaining
  new credentials and overwriting the old ones, not renewing a token in place.
- A field report from another project describes re-scanning as overwriting the
  previous binding, and states that one bot token equals one WeChat account.

"Multiple accounts" in the protocol reference means one *program* managing
several WeChat accounts that each have their own bot, storing credentials and
cursors per `ilink_bot_id`. It does not mean several bots behind one account.
So N bots requires N WeChat accounts.

### What this channel does not give you

Failure modes worth knowing before building on it.

- **No proactive sending, and none at all after a restart.** A reply needs the
  `context_token` from a recent inbound message, and there is no "open a
  conversation" endpoint. A restarted process can answer the next message but
  cannot push one, so "your agent messages you first" is not available here.
- **Session expiry is a re-login.** `errcode`/`ret` `-14` means the token is
  dead: clear the credentials and the cursor and scan again. The official client
  chooses to back off for an hour rather than hammer an expired session.
- **The QR expires on its own**, with no field announcing when; the documented
  behaviour is to re-fetch on `expired`.
- **Groups do not work.** The bot is not a normal contact that can be invited
  into an ordinary group, and group events generally are not delivered. Direct
  messages are the reliable path.
- **Text only, here.** Images, voice, files and video arrive as items this
  experiment ignores. Sending media means `getuploadurl`, an AES-128-ECB
  encrypted body, and a separate CDN host.
- **The bot identity is created by the scan**, so the token is bound to one
  person's WeChat. This is not a multi-user channel.

## Video angle

- **Hook:** WeChat shipped an official bot API, and the whole login is one scan
  plus a long-poll.
- **The surprise:** the reply token is not an identity but a per-conversation
  capability, which is why a restarted bridge can answer you but can never
  message you first.
- **Board:** none yet. If this becomes a video, a single sequence diagram of the
  QR handshake and one message round trip says more than a screenshot.

## Graduation

Nothing here should move into `waku/` as it stands, and this experiment stays
the on-its-own-terms baseline if something does.

The destination, when it is wanted, is rung 5 of the footprint ladder: a
gateway, one file in `waku/gateway/`, moving text in through `waku.respond()`
and out again with no memory, tools or loop logic of its own. Three things would
have to arrive with it:

1. **Per-account state directories.** Credentials and cursor are already
   separate files, but they assume one account. The protocol reference is
   explicit that they must be stored per `ilink_bot_id`.
2. **A persisted `context_token` cache.** Without it a restart loses the ability
   to send anything unprompted, which for a gateway means losing scheduled
   notifications. It needs a staleness rule, because the token is not permanent.
3. **A decision about the single-account limit.** A gateway can serve exactly
   one WeChat account per bot, so multi-user is not a configuration this channel
   can reach.
