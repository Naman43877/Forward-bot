"""Scheduling tests. Run after test_all.py."""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "stubs"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.update(BOT_TOKEN="test", ADMIN_IDS="111",
                  DB_PATH=os.path.join(tempfile.mkdtemp(), "s.db"),
                  SEND_DELAY_SECONDS="0", TIMEZONE="Asia/Kolkata",
                  TIMEZONE_LABEL="IST")

import broadcast_handlers as bh  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402
import keyboards  # noqa: E402
import scheduler  # noqa: E402
import timeparse  # noqa: E402
from test_all import FakeBot, FakeCtx, FakeMsg, FakeQuery, FakeUpdate  # noqa: E402

PASS, FAIL = [], []
IST = ZoneInfo("Asia/Kolkata")


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'  ok  ' if cond else ' FAIL '} {name}" + (f"   {detail}" if detail else ""))


def setup():
    db.connect(os.environ["DB_PATH"])
    db.seed_admins([111])
    db.add_user(222, "Aysha", "operator")
    db.upsert_channel(-1001, "AR Main", "ar")
    db.upsert_channel(-1002, "AR Sports", "ar")
    db.set_affiliate(-1001, "https://a.example/1", "انضم")
    db.set_dm(-1001, "https://t.me/c1", "تواصل")
    db.set_affiliate(-1002, "https://a.example/2", "انضم")
    db.set_dm(-1002, "https://t.me/c2", "تواصل")
    gid = db.add_group("Middle East", 1)
    db.assign(gid, [-1001, -1002])
    return gid


# ----------------------------------------------------------- time parsing

def test_timeparse():
    now = datetime(2026, 9, 16, 14, 0, tzinfo=IST)   # Wed 16 Sep, 2pm IST

    def p(s):
        dt, note = timeparse.parse(s, now=now)
        return dt.astimezone(IST), note

    check("24h time later today", p("21:00")[0] == datetime(2026, 9, 16, 21, 0, tzinfo=IST))
    check("9pm parses as 21:00", p("9pm")[0] == datetime(2026, 9, 16, 21, 0, tzinfo=IST))
    check("9:30pm keeps minutes",
          p("9:30pm")[0] == datetime(2026, 9, 16, 21, 30, tzinfo=IST))
    check("12am is midnight tonight",
          p("12am")[0] == datetime(2026, 9, 17, 0, 0, tzinfo=IST))
    check("12pm is noon", p("tomorrow 12pm")[0] ==
          datetime(2026, 9, 17, 12, 0, tzinfo=IST))

    dt, note = p("9am")
    check("a time already past today rolls to tomorrow",
          dt == datetime(2026, 9, 17, 9, 0, tzinfo=IST) and note is not None, str(note))

    check("tomorrow 9am", p("tomorrow 9am")[0] == datetime(2026, 9, 17, 9, 0, tzinfo=IST))
    check("tmr shorthand", p("tmr 9:30")[0] == datetime(2026, 9, 17, 9, 30, tzinfo=IST))
    check("dd/mm form", p("18/09 09:00")[0] == datetime(2026, 9, 18, 9, 0, tzinfo=IST))
    check("18 sep form", p("18 sep 9am")[0] == datetime(2026, 9, 18, 9, 0, tzinfo=IST))
    check("sep 18 form", p("sep 18 9am")[0] == datetime(2026, 9, 18, 9, 0, tzinfo=IST))
    check("relative +2h", p("+2h")[0] == datetime(2026, 9, 16, 16, 0, tzinfo=IST))
    check("relative +30m", p("+30m")[0] == datetime(2026, 9, 16, 14, 30, tzinfo=IST))
    try:
        timeparse.parse("01/02 09:00", now=now)
        check("a date already past is rejected, not rolled to next year", False)
    except timeparse.TimeParseError:
        check("a date already past is rejected, not rolled to next year", True)

    for bad in ("banana", "25:00", "13pm", "9am tomorrow please now", "32/09 9am"):
        try:
            timeparse.parse(bad, now=now)
            check(f"rejects '{bad}'", False)
        except timeparse.TimeParseError:
            check(f"rejects '{bad}'", True)

    check("stored value is UTC",
          timeparse.parse("21:00", now=now)[0].tzinfo == timezone.utc
          or str(timeparse.parse("21:00", now=now)[0].tzinfo) == "UTC")
    check("display renders back in IST",
          "IST" in timeparse.fmt(timeparse.parse("tomorrow 9am", now=now)[0]))


# -------------------------------------------------------------- migration

def test_migration():
    import sqlite3
    path = os.path.join(tempfile.mkdtemp(), "old.db")
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE sessions (user_id INTEGER PRIMARY KEY, group_id INTEGER,"
        " ad_hoc_chats TEXT, buttons_mode TEXT NOT NULL DEFAULT 'both',"
        " last_active_at TEXT NOT NULL);"
        "INSERT INTO sessions VALUES (5, 1, '[]', 'both', '2026-01-01T00:00:00+00:00');")
    old.commit()
    old.close()

    import importlib
    db._conn = None
    db.connect(path)
    cols = {r[1] for r in db.conn().execute("PRAGMA table_info(sessions)")}
    check("migration adds sessions.kind to an existing database", "kind" in cols)
    row = db.conn().execute("SELECT kind FROM sessions WHERE user_id=5").fetchone()
    check("existing session rows default to 'live'", row["kind"] == "live")
    check("existing data survives the migration",
          db.conn().execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1)

    db._conn = None
    db.connect(os.environ["DB_PATH"])


# ------------------------------------------------------ single + schedule

async def test_schedule_from_single(gid):
    bot = FakeBot()
    ctx = FakeCtx(bot)
    msg = FakeMsg(message_id=50, chat_id=222, text="Morning offer is live")

    await bh.on_message(FakeUpdate(222, message=msg), ctx)
    await bh.on_callback(FakeUpdate(222, query=FakeQuery(f"grp:single:{gid}", msg)), ctx)
    q = FakeQuery("btn:both", msg)
    await bh.on_callback(FakeUpdate(222, query=q), ctx)

    labels = [b[0].callback_data for b in q.edits[-1]["markup"].inline_keyboard]
    check("confirm screen offers both send and schedule",
          "send" in labels and "schedule" in labels, str(labels))

    await bh.on_callback(FakeUpdate(222, query=FakeQuery("schedule", msg)), ctx)
    check("schedule tap asks for a time",
          ctx.user_data.get("flow") == "await_time")
    check("quick picks are offered",
          any("t:" in b.callback_data
              for row in msg.replies[-1]["markup"].inline_keyboard for b in row))

    t = FakeMsg(message_id=51, chat_id=222, text="tomorrow 9am")
    await bh.on_message(FakeUpdate(222, message=t), ctx)
    check("nothing was broadcast", len(bot.sent) == 0)

    posts = db.pending_posts(222)
    check("post is queued", len(posts) == 1, str(len(posts)))
    check("queued post keeps the source pointer",
          posts[0]["src_chat_id"] == 222 and posts[0]["src_message_id"] == 50)
    check("preview stored for the list",
          posts[0]["preview"].startswith("Morning offer"))
    check("confirmation shows an absolute IST time",
          "IST" in t.replies[-1]["text"], t.replies[-1]["text"].replace("\n", " | "))
    check("flow cleared after scheduling", ctx.user_data.get("flow") is None)
    return posts[0]["post_id"]


async def test_bad_time_keeps_prompt(gid):
    bot = FakeBot()
    ctx = FakeCtx(bot)
    msg = FakeMsg(message_id=60, chat_id=222, text="Evening post")
    await bh.on_message(FakeUpdate(222, message=msg), ctx)
    await bh.on_callback(FakeUpdate(222, query=FakeQuery(f"grp:single:{gid}", msg)), ctx)
    await bh.on_callback(FakeUpdate(222, query=FakeQuery("btn:both", msg)), ctx)
    await bh.on_callback(FakeUpdate(222, query=FakeQuery("schedule", msg)), ctx)

    before = len(db.pending_posts(222))
    bad = FakeMsg(message_id=61, chat_id=222, text="sometime later")
    await bh.on_message(FakeUpdate(222, message=bad), ctx)
    check("unparseable time is rejected, nothing queued",
          len(db.pending_posts(222)) == before)
    check("still waiting for a time", ctx.user_data.get("flow") == "await_time")
    check("draft survives a bad time", ctx.user_data.get("sched_draft") is not None)

    good = FakeMsg(message_id=62, chat_id=222, text="+3h")
    await bh.on_message(FakeUpdate(222, message=good), ctx)
    check("retry with a valid time queues it",
          len(db.pending_posts(222)) == before + 1)


# ------------------------------------------------------------- plan mode

async def test_plan_mode(gid):
    bot = FakeBot()
    ctx = FakeCtx(bot)
    msg = FakeMsg(message_id=70, chat_id=222)

    await bh.on_callback(FakeUpdate(222, query=FakeQuery("mode:plan", msg)), ctx)
    await bh.on_callback(FakeUpdate(222, query=FakeQuery(f"grp:plan:{gid}", msg)), ctx)
    s = db.get_session(222)
    check("planning session persisted with kind=plan",
          s is not None and s["kind"] == "plan")
    check("planning keyboard shown, not live keyboard",
          "PLANNING" in msg.replies[-1]["markup"].input_field_placeholder)

    before = len(db.pending_posts(222))
    for i, (body, when) in enumerate([("Post A", "tomorrow 9am"),
                                      ("Post B", "tomorrow 12:00"),
                                      ("Post C", "tomorrow 18:00")]):
        m = FakeMsg(message_id=80 + i, chat_id=222, text=body)
        await bh.on_message(FakeUpdate(222, message=m), ctx)
        t = FakeMsg(message_id=90 + i, chat_id=222, text=when)
        await bh.on_message(FakeUpdate(222, message=t), ctx)

    check("three posts queued in one planning run",
          len(db.pending_posts(222)) == before + 3)
    check("planning never broadcasts", len(bot.sent) == 0)
    check("keyboard returns after each schedule",
          "PLANNING" in t.replies[-1]["markup"].input_field_placeholder)

    # the critical routing case
    end = FakeMsg(message_id=99, chat_id=222, text=keyboards.KEY_END_PLANNING)
    await bh.on_message(FakeUpdate(222, message=end), ctx)
    check("End planning is intercepted, not scheduled",
          db.get_session(222) is None
          and len(db.pending_posts(222)) == before + 3)
    check("user is reminded posts are still queued",
          "still scheduled" in end.replies[-1]["text"])


async def test_keyboard_tap_while_awaiting_time(gid):
    bot = FakeBot()
    ctx = FakeCtx(bot)
    msg = FakeMsg(message_id=120, chat_id=222)
    await bh.on_callback(FakeUpdate(222, query=FakeQuery("mode:plan", msg)), ctx)
    await bh.on_callback(FakeUpdate(222, query=FakeQuery(f"grp:plan:{gid}", msg)), ctx)

    m = FakeMsg(message_id=121, chat_id=222, text="Draft post")
    await bh.on_message(FakeUpdate(222, message=m), ctx)
    check("awaiting a time", ctx.user_data.get("flow") == "await_time")

    before = len(db.pending_posts(222))
    tap = FakeMsg(message_id=122, chat_id=222, text=keyboards.KEY_END_PLANNING)
    await bh.on_message(FakeUpdate(222, message=tap), ctx)
    check("a keyboard tap while awaiting a time is not read as a time",
          len(db.pending_posts(222)) == before and db.get_session(222) is None)


# ------------------------------------------------------------- scheduler

async def test_scheduler_sends(gid):
    bot = FakeBot()
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    pid = db.schedule_post(222, gid, [], "both", 222, 500, "Due now", past)

    await scheduler.run_due(bot)
    check("due post is sent", len(bot.sent) == 2, str(len(bot.sent)))
    check("post marked sent", db.get_post(pid)["status"] == "sent")
    check("linked to a broadcast for undo",
          db.get_post(pid)["broadcast_id"] is not None)
    check("author notified",
          any("Sent as scheduled" in m["text"] for m in bot.messages))

    n = len(bot.sent)
    await scheduler.run_due(bot)
    check("a sent post is never re-sent", len(bot.sent) == n)


async def test_grace_window(gid):
    bot = FakeBot()
    late = (datetime.now(timezone.utc)
            - timedelta(minutes=config.SCHEDULE_GRACE_MINUTES + 10)).isoformat()
    pid = db.schedule_post(222, gid, [], "both", 222, 501, "Good morning", late)

    await scheduler.run_due(bot)
    check("a badly late post is NOT sent", len(bot.sent) == 0)
    check("marked missed", db.get_post(pid)["status"] == "missed")
    check("author told it was missed",
          any("Missed scheduled post" in m["text"] for m in bot.messages))


async def test_within_grace(gid):
    bot = FakeBot()
    slightly = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    pid = db.schedule_post(222, gid, [], "both", 222, 502, "Slightly late", slightly)
    await scheduler.run_due(bot)
    check("a slightly late post still goes out",
          db.get_post(pid)["status"] == "sent" and len(bot.sent) == 2)


async def test_future_not_sent(gid):
    bot = FakeBot()
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    pid = db.schedule_post(222, gid, [], "both", 222, 503, "Later", future)
    await scheduler.run_due(bot)
    check("a future post is left alone",
          len(bot.sent) == 0 and db.get_post(pid)["status"] == "pending")
    return pid


async def test_cancel(gid, pid):
    check("cancel by the author works", db.cancel_post(pid, 222) == "ok")
    check("cancelled post leaves the queue",
          pid not in [p["post_id"] for p in db.pending_posts(222)])

    bot = FakeBot()
    await scheduler.run_due(bot)
    check("a cancelled post never sends", len(bot.sent) == 0)

    other = db.schedule_post(222, gid, [], "both", 222, 504, "Mine",
                             (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat())
    db.add_user(333, "Other", "operator")
    check("another operator cannot cancel it",
          db.cancel_post(other, 333) == "not_yours")
    check("an admin can", db.cancel_post(other, 111, is_admin=True) == "ok")


async def test_claim_is_atomic(gid):
    db.schedule_post(222, gid, [], "both", 222, 505, "Race",
                     (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
    first = db.claim_due_posts()
    second = db.claim_due_posts()
    check("overlapping ticks cannot claim the same post",
          len(first) == 1 and len(second) == 0)
    check("stuck 'sending' rows are reaped at startup",
          db.reap_stuck_sending() == 1)


async def test_group_deleted_under_a_post():
    bot = FakeBot()
    tmp = db.add_group("Temporary")
    db.assign(tmp, [-1001])
    pid = db.schedule_post(222, tmp, [], "both", 222, 506, "Orphan",
                           (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
    db.delete_group(tmp)
    await scheduler.run_due(bot)
    check("a post whose group was deleted fails loudly instead of sending",
          len(bot.sent) == 0 and db.get_post(pid)["status"] == "failed")
    check("author told why",
          any("no active channels" in m["text"] for m in bot.messages))


class DownBot(FakeBot):
    """Every call fails the way a DNS outage does."""
    async def copy_message(self, *a, **k):
        from telegram.error import TelegramError
        raise TelegramError("httpx.ConnectError: [Errno 11001] getaddrinfo failed")

    async def send_message(self, *a, **k):
        from telegram.error import TelegramError
        raise TelegramError("network down")


async def test_network_outage(gid):
    import sender
    real_sleep = sender.asyncio.sleep

    async def fast(*_a, **_k):
        return None
    sender.asyncio.sleep = fast           # skip retry backoff in tests
    try:
        due = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        pid = db.schedule_post(222, gid, [], "both", 222, 700, "Outage test", due)

        await scheduler.run_due(DownBot())
        p = db.get_post(pid)
        check("post reaching no channel is NOT marked sent", p["status"] != "sent",
              p["status"])
        check("it goes back in the queue", p["status"] == "pending")
        check("the reason is recorded", "getaddrinfo" in (p["error"] or ""),
              p["error"])

        bot = FakeBot()                    # network is back
        await scheduler.run_due(bot)
        check("next tick delivers it once the network returns",
              db.get_post(pid)["status"] == "sent" and len(bot.sent) == 2)
        check("author gets the success DM", any(
            "Sent as scheduled" in m["text"] for m in bot.messages))

        # outage that outlasts the grace window
        late = (datetime.now(timezone.utc)
                - timedelta(minutes=config.SCHEDULE_GRACE_MINUTES - 1)).isoformat()
        pid2 = db.schedule_post(222, gid, [], "both", 222, 701, "Long outage", late)
        await scheduler.run_due(DownBot())
        check("still retrying while inside grace",
              db.get_post(pid2)["status"] == "pending")
        db.conn().execute(
            "UPDATE scheduled_posts SET send_at=? WHERE post_id=?",
            ((datetime.now(timezone.utc)
              - timedelta(minutes=config.SCHEDULE_GRACE_MINUTES + 5)).isoformat(), pid2))
        db.conn().commit()
        bot2 = FakeBot()
        await scheduler.run_due(bot2)
        check("once past grace it's marked missed, not sent late",
              db.get_post(pid2)["status"] == "missed" and len(bot2.sent) == 0)
        check("and the author is told", any(
            "Missed scheduled post" in m["text"] for m in bot2.messages))

        # partial failure must NOT re-queue (would duplicate in the good channel)
        bot3 = FakeBot()
        bot3.forbidden.add(-1002)
        pid3 = db.schedule_post(222, gid, [], "both", 222, 702, "Partial",
                                (datetime.now(timezone.utc)
                                 - timedelta(seconds=5)).isoformat())
        await scheduler.run_due(bot3)
        await scheduler.run_due(bot3)
        check("partial failure is final, never re-sent to channels that got it",
              db.get_post(pid3)["status"] == "sent"
              and [s["chat_id"] for s in bot3.sent].count(-1001) == 1)
    finally:
        sender.asyncio.sleep = real_sleep


async def main():
    gid = setup()
    print("\n— time parsing —");      test_timeparse()
    print("\n— migration —");         test_migration()
    print("\n— schedule from single —"); await test_schedule_from_single(gid)
    print("\n— bad times —");         await test_bad_time_keeps_prompt(gid)
    print("\n— planning mode —");     await test_plan_mode(gid)
    print("\n— routing —");           await test_keyboard_tap_while_awaiting_time(gid)

    db.conn().execute("UPDATE scheduled_posts SET status='cancelled' "
                      "WHERE status='pending'")
    db.conn().commit()

    print("\n— scheduler —");         await test_scheduler_sends(gid)
    print("\n— grace window —");      await test_grace_window(gid)
    print("\n— within grace —");      await test_within_grace(gid)
    print("\n— future posts —");      pid = await test_future_not_sent(gid)
    print("\n— cancelling —");        await test_cancel(gid, pid)
    print("\n— concurrency —");       await test_claim_is_atomic(gid)
    print("\n— broken targets —");    await test_group_deleted_under_a_post()
    print("\n— network outage —");    await test_network_outage(gid)

    print(f"\n{'='*52}\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("Failures: " + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
