"""Dry run without Telegram. Exercises schema, per-channel keyboards, the
send loop's retry/failure paths, undo, and both end-to-end flows."""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "stubs"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["BOT_TOKEN"] = "test"
os.environ["ADMIN_IDS"] = "111"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["SEND_DELAY_SECONDS"] = "0"

from telegram import Message  # noqa: E402
from telegram.error import BadRequest, Forbidden, RetryAfter  # noqa: E402

import broadcast_handlers as bh  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402
import keyboards  # noqa: E402
import sender  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'  ok  ' if cond else ' FAIL '} {name}" + (f"   {detail}" if detail else ""))


# ------------------------------------------------------------- fake bot

class FakeBot:
    def __init__(self):
        self.sent = []
        self.deleted = []
        self.messages = []
        self.fail_once = set()
        self.forbidden = set()
        self.badrequest = set()
        self._n = 1000

    async def copy_message(self, chat_id, from_chat_id, message_id, reply_markup=None):
        if chat_id in self.forbidden:
            raise Forbidden("bot was blocked")
        if chat_id in self.badrequest:
            raise BadRequest("message can't be copied")
        if chat_id in self.fail_once:
            self.fail_once.discard(chat_id)
            raise RetryAfter(0)
        self._n += 1
        self.sent.append({"chat_id": chat_id, "src": (from_chat_id, message_id),
                          "markup": reply_markup, "message_id": self._n})
        return type("R", (), {"message_id": self._n})()

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        self.messages.append({"chat_id": chat_id, "text": text})


class FakeMsg(Message):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.replies = []

    async def reply_text(self, text, reply_markup=None):
        self.replies.append({"text": text, "markup": reply_markup})
        return self


class FakeQuery:
    def __init__(self, data, message):
        self.data = data
        self.message = message
        self.edits = []
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append(text)

    async def edit_message_text(self, text, reply_markup=None):
        self.edits.append({"text": text, "markup": reply_markup})

    async def edit_message_reply_markup(self, reply_markup=None):
        self.edits.append({"text": None, "markup": reply_markup})


class FakeUpdate:
    def __init__(self, user_id, message=None, query=None):
        self.effective_user = type("U", (), {"id": user_id, "full_name": "Aysha"})()
        self.effective_message = message or (query.message if query else None)
        self.callback_query = query


class FakeCtx:
    def __init__(self, bot):
        self.bot = bot
        self.user_data = {}


# ------------------------------------------------------------- fixtures

def setup():
    db.connect(os.environ["DB_PATH"])
    db.seed_admins([111])
    db.add_user(222, "Aysha", "operator")

    db.upsert_channel(-1001, "AR Main", "ar")
    db.upsert_channel(-1002, "AR Sports", "ar")
    db.upsert_channel(-1003, "BN Main", "bn")
    db.upsert_channel(-1004, "HI Main", "hi")
    db.upsert_channel(-1005, "EN Paused", "en")
    db.set_channel_active(-1005, False)

    db.set_affiliate(-1001, "https://aff.example/ar1", "انضم الآن")
    db.set_dm(-1001, "https://t.me/creator_ar", "تواصل معنا")
    db.set_affiliate(-1002, "https://aff.example/ar2", "انضم الآن")
    db.set_affiliate(-1003, "https://aff.example/bn", "এখনই যোগ দিন")
    db.set_dm(-1003, "https://t.me/creator_bn", "চ্যাট করুন")
    # -1004 deliberately has no links, to test the warning path.

    me = db.add_group("Middle East", 1)
    ar = db.add_group("Arabic", 2)
    allg = db.add_group("All", 9)
    db.assign(me, [-1001, -1002])
    db.assign(ar, [-1001, -1002])          # overlap is intentional
    db.assign(allg, [-1001, -1002, -1003, -1004, -1005])
    return {"me": me, "ar": ar, "all": allg}


# ---------------------------------------------------------------- tests

def test_db(g):
    check("overlapping group membership",
          {c["chat_id"] for c in db.channels_in_group(g["ar"])} == {-1001, -1002})
    check("paused channels excluded from group",
          -1005 not in {c["chat_id"] for c in db.channels_in_group(g["all"])},
          f"{len(db.channels_in_group(g['all']))} active of 5")
    check("channel knows its groups",
          {x["name"] for x in db.groups_for_channel(-1001)} ==
          {"Middle East", "Arabic", "All"})
    counts = {r["name"]: r["n"] for r in db.list_groups()}
    check("group picker counts are active-only", counts["All"] == 4, str(counts))


def test_keyboards():
    ar = db.get_channel(-1001)
    bn = db.get_channel(-1003)
    ar2 = db.get_channel(-1002)
    hi = db.get_channel(-1004)

    k1 = keyboards.channel_markup(ar, "both")
    k2 = keyboards.channel_markup(bn, "both")
    check("per-channel affiliate URL differs",
          k1.inline_keyboard[0][0].url != k2.inline_keyboard[0][0].url)
    check("button label is in the channel's language",
          k1.inline_keyboard[0][0].text == "انضم الآن"
          and k2.inline_keyboard[0][0].text == "এখনই যোগ দিন")
    check("both mode gives two buttons", len(k1.inline_keyboard[0]) == 2)
    check("affiliate-only mode gives one",
          len(keyboards.channel_markup(ar, "affiliate").inline_keyboard[0]) == 1)
    check("none mode gives no keyboard",
          keyboards.channel_markup(ar, "none") is None)
    check("channel without DM link degrades to one button",
          len(keyboards.channel_markup(ar2, "both").inline_keyboard[0]) == 1)
    check("channel with no links gets no keyboard",
          keyboards.channel_markup(hi, "both") is None)
    check("buttons mode cycles both→affiliate→dm→none→both",
          keyboards.cycle_buttons_mode("both") == "affiliate"
          and keyboards.cycle_buttons_mode("affiliate") == "dm"
          and keyboards.cycle_buttons_mode("dm") == "none"
          and keyboards.cycle_buttons_mode("none") == "both")
    kdm = keyboards.channel_markup(ar, "dm")
    check("dm-only mode gives just the DM button",
          len(kdm.inline_keyboard[0]) == 1
          and kdm.inline_keyboard[0][0].url == ar["dm_url"])
    check("dm-only on a channel with no DM link gives no keyboard",
          keyboards.channel_markup(ar2, "dm") is None)


def test_unsupported():
    try:
        sender.check_supported(FakeMsg(media_group_id="abc"))
        check("album rejected", False)
    except sender.UnsupportedContent:
        check("album rejected", True)
    try:
        sender.check_supported(FakeMsg(poll=object()))
        check("poll rejected", False)
    except sender.UnsupportedContent:
        check("poll rejected", True)
    try:
        sender.check_supported(FakeMsg(text="hello"))
        check("plain text accepted", True)
    except sender.UnsupportedContent:
        check("plain text accepted", False)


async def test_sender(g):
    bot = FakeBot()
    bot.fail_once.add(-1002)      # one 429, must retry and succeed
    bot.forbidden.add(-1004)      # bot not admin
    targets = db.channels_in_group(g["all"])

    r = await sender.broadcast(bot, 222, "single", "both", g["all"],
                               targets, 999, 55)
    check("retry after 429 succeeds", "AR Sports" in r["ok"])
    check("forbidden channel recorded as failure",
          [t for t, _ in r["failed"]] == ["HI Main"], str(r["failed"]))
    check("three channels sent", len(r["ok"]) == 3, str(r["ok"]))

    by_chat = {s["chat_id"]: s for s in bot.sent}
    check("each channel got its own keyboard object",
          by_chat[-1001]["markup"].inline_keyboard[0][0].url !=
          by_chat[-1003]["markup"].inline_keyboard[0][0].url)
    check("same source message copied to all",
          {s["src"] for s in bot.sent} == {(999, 55)})

    u = await sender.undo(bot, r["broadcast_id"])
    check("undo deletes exactly what was sent",
          u["deleted"] == 3 and len(bot.deleted) == 3, str(u))
    check("broadcast marked undone",
          db.get_broadcast(r["broadcast_id"])["undone_at"] is not None)
    check("no sent targets remain after undo",
          len(db.sent_targets(r["broadcast_id"])) == 0)


async def test_single_flow(g):
    bot = FakeBot()
    ctx = FakeCtx(bot)
    msg = FakeMsg(message_id=77, chat_id=222, text="Tournament starts 8pm")

    await bh.on_message(FakeUpdate(222, message=msg), ctx)
    check("message drafts and asks for group",
          ctx.user_data.get("flow") == "single_await_group"
          and msg.replies[-1]["markup"] is not None)

    labels = [b[0].text for b in msg.replies[-1]["markup"].inline_keyboard]
    check("group picker lists groups and ad-hoc option",
          any("Middle East" in x for x in labels) and any("Pick" in x for x in labels),
          str(labels))

    q = FakeQuery(f"grp:single:{g['me']}", msg)
    await bh.on_callback(FakeUpdate(222, query=q), ctx)
    check("group tap moves to buttons step",
          ctx.user_data.get("flow") == "single_await_buttons")

    q2 = FakeQuery("btn:both", msg)
    await bh.on_callback(FakeUpdate(222, query=q2), ctx)
    confirm = q2.edits[-1]["text"]
    check("confirmation names every channel",
          "AR Main" in confirm and "AR Sports" in confirm, confirm.replace("\n", " | "))
    check("confirmation shows the count", "2 channel" in confirm)
    check("nothing sent before confirming", len(bot.sent) == 0)

    q3 = FakeQuery("send", msg)
    await bh.on_callback(FakeUpdate(222, query=q3), ctx)
    check("send reaches both channels", len(bot.sent) == 2)
    check("flow cleared after send", ctx.user_data.get("flow") is None)
    check("undo button offered",
          msg.replies[-1]["markup"].inline_keyboard[0][0].callback_data.startswith("undo:"))


async def test_missing_link_warning(g):
    bot = FakeBot()
    ctx = FakeCtx(bot)
    msg = FakeMsg(message_id=78, chat_id=222, text="hi")
    await bh.on_message(FakeUpdate(222, message=msg), ctx)
    await bh.on_callback(FakeUpdate(222, query=FakeQuery(f"grp:single:{g['all']}", msg)), ctx)
    q = FakeQuery("btn:both", msg)
    await bh.on_callback(FakeUpdate(222, query=q), ctx)
    text = q.edits[-1]["text"]
    check("warns about channel with no affiliate link",
          "no affiliate link" in text and "HI Main" in text)
    check("warns about channels missing the DM link",
          "no DM link" in text and "AR Sports" in text)


async def test_session_flow(g):
    bot = FakeBot()
    ctx = FakeCtx(bot)
    msg = FakeMsg(message_id=90, chat_id=222)

    await bh.on_callback(FakeUpdate(222, query=FakeQuery("mode:session", msg)), ctx)
    await bh.on_callback(FakeUpdate(222, query=FakeQuery(f"grp:session:{g['me']}", msg)), ctx)
    s = db.get_session(222)
    check("session persisted to DB", s is not None and s["group_id"] == g["me"])
    check("reply keyboard shows the live target",
          "Middle East" in msg.replies[-1]["markup"].input_field_placeholder)

    for i, body in enumerate(["Match 1 live", "Match 2 live", "Final in 10"]):
        m = FakeMsg(message_id=100 + i, chat_id=222, text=body)
        await bh.on_message(FakeUpdate(222, message=m), ctx)
    check("three rapid messages each hit both channels", len(bot.sent) == 6)
    check("each send offers its own undo",
          m.replies[-1]["markup"].inline_keyboard[0][0].callback_data.startswith("undo:"))
    check("session sends are terse", m.replies[-1]["text"].startswith("✓ 2"),
          m.replies[-1]["text"])

    # cycle buttons from the pinned keyboard
    cyc = FakeMsg(message_id=110, chat_id=222,
                  text=keyboards.KEY_BUTTONS_PREFIX + "Both buttons")
    await bh.on_message(FakeUpdate(222, message=cyc), ctx)
    check("keyboard tap cycles mode without sending",
          db.get_session(222)["buttons_mode"] == "affiliate" and len(bot.sent) == 6)

    m2 = FakeMsg(message_id=111, chat_id=222, text="Now affiliate only")
    await bh.on_message(FakeUpdate(222, message=m2), ctx)
    check("subsequent post uses the new mode",
          len(bot.sent[-1]["markup"].inline_keyboard[0]) == 1)

    end = FakeMsg(message_id=112, chat_id=222, text=keyboards.KEY_END_SESSION)
    await bh.on_message(FakeUpdate(222, message=end), ctx)
    check("end session clears DB state", db.get_session(222) is None)
    check("end session removes the keyboard",
          type(end.replies[-1]["markup"]).__name__ == "ReplyKeyboardRemove")


async def test_idle_expiry(g):
    bot = FakeBot()
    ctx = FakeCtx(bot)
    db.start_session(222, g["me"], [], "both")
    db.conn().execute(
        "UPDATE sessions SET last_active_at='2020-01-01T00:00:00+00:00' "
        "WHERE user_id=222")
    db.conn().commit()

    m = FakeMsg(message_id=200, chat_id=222, text="oops wrong window")
    await bh.on_message(FakeUpdate(222, message=m), ctx)
    check("idle session does NOT send the message", len(bot.sent) == 0)
    check("idle session is cleared", db.get_session(222) is None)
    check("user is told it wasn't sent", "NOT sent" in m.replies[-1]["text"])


async def test_auth(g):
    bot = FakeBot()
    ctx = FakeCtx(bot)
    m = FakeMsg(message_id=300, chat_id=555, text="let me in")
    await bh.on_message(FakeUpdate(555, message=m), ctx)
    check("stranger cannot broadcast",
          len(bot.sent) == 0 and "Not authorised" in m.replies[-1]["text"])


async def test_undo_window(g):
    bot = FakeBot()
    ctx = FakeCtx(bot)
    bid = db.create_broadcast(222, "single", "both", g["me"], 222, 1)
    db.record_target(bid, -1001, 5001, "sent")
    db.conn().execute(
        "UPDATE broadcasts SET created_at='2020-01-01T00:00:00+00:00' "
        "WHERE broadcast_id=?", (bid,))
    db.conn().commit()

    q = FakeQuery(f"undo:{bid}", FakeMsg(chat_id=222))
    await bh.on_callback(FakeUpdate(222, query=q), ctx)
    check("expired undo refuses and deletes nothing",
          len(bot.deleted) == 0 and any(a and "window has closed" in a
                                        for a in q.answers), str(q.answers))

    bid2 = db.create_broadcast(222, "single", "both", g["me"], 222, 1)
    db.record_target(bid2, -1001, 5002, "sent")
    db.add_user(333, "Other operator", "operator")   # registered, but not the sender
    q2 = FakeQuery(f"undo:{bid2}", FakeMsg(chat_id=222))
    await bh.on_callback(FakeUpdate(333, query=q2), ctx)
    check("another operator cannot undo someone else's broadcast",
          len(bot.deleted) == 0 and any(a and "isn't yours" in a for a in q2.answers),
          str(q2.answers))

    db.add_user(444, "Admin two", "admin")
    q3 = FakeQuery(f"undo:{bid2}", FakeMsg(chat_id=222))
    await bh.on_callback(FakeUpdate(444, query=q3), ctx)
    check("an admin can undo anyone's broadcast", len(bot.deleted) == 1)


async def main():
    g = setup()
    print("\n— schema —");        test_db(g)
    print("\n— keyboards —");     test_keyboards()
    print("\n— content rules —"); test_unsupported()
    print("\n— sender —");        await test_sender(g)
    print("\n— single flow —");   await test_single_flow(g)
    print("\n— warnings —");      await test_missing_link_warning(g)
    print("\n— session flow —");  await test_session_flow(g)
    print("\n— idle expiry —");   await test_idle_expiry(g)
    print("\n— access —");        await test_auth(g)
    print("\n— undo rules —");    await test_undo_window(g)

    print(f"\n{'='*52}\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("Failures: " + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
