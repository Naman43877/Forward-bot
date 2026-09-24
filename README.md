# Channel Broadcast Bot

One message in, many channels out — each with its own affiliate and creator-DM
button, in that channel's language.

## Three modes

**Single** — send the post → pick group → pick buttons → confirm → sent, with Undo.

**Live session** — pick the group once, then every message goes out immediately.
A keyboard stays pinned above the text input showing the live target, so it's
never off-screen. Auto-ends after 15 minutes idle, and the message that trips
the expiry is **not** sent. For tournaments.

**Planning session** — pick the group once, then each message asks for a time
and joins the queue. For batch-planning tomorrow in one sitting.

Times are typed, not picked: `9am` · `9:30pm` · `21:00` · `tomorrow 9am` ·
`18/09 09:00` · `+2h`. Quick-pick buttons cover the common ones. Everything is
`Asia/Kolkata` unless you change `TIMEZONE`; confirmations always echo the
resolved absolute time. A time already past today rolls to tomorrow and says so;
a past *date* is rejected as a typo.

`/scheduled` lists the queue with a cancel button per post.

## Setup

1. `cp .env.example .env`, put in your bot token from @BotFather.
2. Message the bot `/whoami` to get your Telegram user ID, put it in `ADMIN_IDS`.
3. `pip install -r requirements.txt && python bot.py`
4. In @BotFather: **Group Privacy → disabled** is not needed (DM only), but
   **do** turn off inline mode. Nothing else required.
5. Add the bot as an admin (Post Messages + Delete Messages) to each channel.
   It registers itself and DMs you the `chat_id`.
6. In each channel: **Settings → Sign messages → OFF**. Otherwise posts get
   the bot's name attached instead of looking like a normal channel post.
7. Configure, then add Aysha:

```
/setaff -1001234567890 | https://aff.example/ar | انضم الآن
/setdm  -1001234567890 | https://t.me/creator_ar | تواصل معنا
/addgroup Middle East
/assign Middle East | -1001234567890, -1009876543210
/adduser 123456789 | Aysha | operator
```

Delete Messages rights are what make Undo work. Without them the bot can post
but not retract.

## Commands

Operator: `/start` `/status` `/cancel` `/help` `/whoami`

Admin: `/channels` `/addchannel` `/delchannel` `/toggle` `/setaff` `/setdm`
`/groups` `/addgroup` `/delgroup` `/assign` `/unassign`
`/users` `/adduser` `/deluser` `/log`

Arguments are separated by `|`. A channel can belong to several groups.

## What it deliberately refuses

- **Albums.** Telegram won't attach an inline keyboard to a media group, so the
  affiliate button would silently disappear. One image per post.
- **Polls.** `copy_message` can't reproduce them; they'd need a separate path.

Both are rejected with an explanation rather than sent wrong.

## Scheduling reliability

The scheduler polls SQLite every 30s rather than holding jobs in memory, so a
restart needs no job restoration — the queue is simply still there.

**Uptime becomes load-bearing once you schedule.** With manual posting, a bot
that is down is noticed immediately. With scheduling, nobody finds out until
someone asks why the announcement never went out. If the bot was down when a
post came due, it is sent only if less than `SCHEDULE_GRACE_MINUTES` (default
30) late; beyond that it is marked missed and the author is DMed. Better a gap
than a "good morning" post landing at 3pm.

Due posts are claimed with an atomic status flip, so a slow send can't be
picked up twice by overlapping ticks. A crash mid-send leaves rows in
`sending`; those are reaped to `failed` at startup rather than sitting invisible.

Buttons are built at send time from current channel config, so changing an
affiliate link tonight updates tomorrow's queued posts automatically.

## Design notes

- `copy_message`, not `forward_message` — no "Forwarded from" header.
- `reply_markup` is built per channel, so the same post carries a different
  affiliate URL and localised button text in each one.
- Sends are serial with a 60ms gap. Parallel fan-out is the quickest way to
  trip Telegram's flood limits. 15 channels takes about a second.
- 429s are retried honouring `retry_after`; a channel where the bot lost admin
  rights is reported as a failure rather than failing the whole broadcast, and
  gets auto-paused.
- Sessions live in SQLite, not memory, so a Railway redeploy doesn't silently
  drop a live session.
- Content is never stored. `broadcasts` keeps a pointer to the original message
  in the operator's own DM and copies from it.
- Undo walks `broadcast_targets` calling `deleteMessage`. Bots can delete their
  own channel messages for 48h; the window is capped at 10 minutes by config.

## Tests

`python tests/test_all.py` — 52 checks: schema, per-channel keyboards, retry
and failure paths, undo rules, single and live flows, idle expiry, access control.

`python tests/test_schedule.py` — 65 checks: time parsing, the sessions.kind
migration, scheduling from single mode, planning mode, message routing while
awaiting a time, the grace window, cancellation, and claim concurrency.

Both run against a stubbed Telegram — no token or network needed. Run them
after any edit and before restarting the bot.
