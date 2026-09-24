"""The flows Aysha uses.

  Single    : message → group → buttons → Send now OR Schedule
  Live      : group → 🔴 every message fires immediately
  Planning  : group → 🗓 every message asks for a time, then queues

The ordering inside on_message is load-bearing: keyboard taps arrive as plain
text and must be intercepted before anything is treated as content.
"""
import logging
from typing import Any, Sequence

from telegram import Update
from telegram.ext import ContextTypes

import auth
import config
import db
import keyboards
import sender
import timeparse

log = logging.getLogger(__name__)

# Flow states waiting on a button tap, not on a message.
AWAITING_TAP = {
    "single_await_group", "single_await_buttons", "single_await_confirm",
    "session_await_group", "plan_await_group", "adhoc_pick",
}


# ------------------------------------------------------------- utilities

def _clear(context: ContextTypes.DEFAULT_TYPE) -> None:
    for k in ("flow", "draft", "group_id", "chats", "buttons_mode",
              "adhoc_selected", "adhoc_purpose", "sched_draft", "sched_ctx"):
        context.user_data.pop(k, None)


def _resolve(group_id: int | None, chats: Sequence[int] | None) -> list[Any]:
    if group_id is not None:
        return db.channels_in_group(group_id)
    return db.channels_by_ids(list(chats or []))


def _label(group_id: int | None, chats: Sequence[int] | None) -> str:
    if group_id is not None:
        g = db.get_group(group_id)
        return g["name"] if g else "unknown group"
    n = len(chats or [])
    return f"{n} channel{'s' if n != 1 else ''}"


def _preview(msg) -> str:
    text = getattr(msg, "text", None) or getattr(msg, "caption", None) or ""
    if text:
        flat = " ".join(text.split())
        return flat[:60] + ("…" if len(flat) > 60 else "")
    for attr, name in (("photo", "📷 photo"), ("video", "🎬 video"),
                       ("sticker", "🩷 sticker"), ("animation", "🎞 GIF"),
                       ("video_note", "⭕ video note"), ("voice", "🎤 voice"),
                       ("audio", "🎵 audio"), ("document", "📎 file")):
        if getattr(msg, attr, None):
            return name
    return "(no text)"


def _link_warnings(targets: Sequence[Any], buttons_mode: str) -> list[str]:
    if buttons_mode == "none":
        return []
    warnings = []
    if buttons_mode in ("both", "affiliate"):
        missing = [t["title"] for t in targets if not t["affiliate_url"]]
        if missing:
            warnings.append("no affiliate link: " + ", ".join(missing))
    if buttons_mode in ("both", "dm"):
        missing = [t["title"] for t in targets if not t["dm_url"]]
        if missing:
            warnings.append("no DM link: " + ", ".join(missing))
    return warnings


async def _report(update: Update, result: dict[str, Any], label: str,
                  terse: bool) -> None:
    ok, failed = result["ok"], result["failed"]
    text = (f"✓ {len(ok)} · {label}" if terse
            else f"✅ Sent to {len(ok)} channel(s) · {label}")
    if failed:
        text += "\n\n❌ Failed:\n" + "\n".join(f"• {t}: {e}" for t, e in failed)
    await update.effective_message.reply_text(
        text, reply_markup=keyboards.undo(result["broadcast_id"]) if ok else None)


# -------------------------------------------------------------- commands

@auth.operator_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear(context)
    uid = update.effective_user.id
    session = db.get_session(uid)

    if session and session.get("kind") == "plan":
        label = _label(session["group_id"], session["ad_hoc_chats"])
        await update.effective_message.reply_text(
            f"🗓 A planning session is already open → {label}",
            reply_markup=keyboards.plan_keyboard(
                label, session["buttons_mode"], len(db.pending_posts(uid))))
        return

    if session and not db.session_is_idle(session):
        label = _label(session["group_id"], session["ad_hoc_chats"])
        await update.effective_message.reply_text(
            f"🔴 A live session is already running → {label}",
            reply_markup=keyboards.session_keyboard(label, session["buttons_mode"]))
        return

    db.end_session(uid)
    await update.effective_message.reply_text(
        "What are we doing?\n\n"
        "📄 Single message — one post, confirm, send now or schedule it.\n"
        "🔴 Live session — pick the group once, every message goes out "
        "immediately. For tournaments.\n"
        "🗓 Planning session — pick the group once, every message asks for a "
        "time and gets queued. For planning tomorrow.",
        reply_markup=keyboards.mode_picker())


@auth.operator_only
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear(context)
    db.end_session(update.effective_user.id)
    await update.effective_message.reply_text(
        "Cancelled. Nothing is live.\n\n"
        "Scheduled posts are untouched — see /scheduled.",
        reply_markup=keyboards.remove_keyboard())


@auth.operator_only
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    session = db.get_session(uid)
    queued = len(db.pending_posts(uid))

    if not session or (session.get("kind") == "live" and db.session_is_idle(session)):
        await update.effective_message.reply_text(
            f"No live session.\n{queued} post(s) scheduled — /scheduled")
        return

    targets = _resolve(session["group_id"], session["ad_hoc_chats"])
    names = "\n".join(f"• {t['title']}" for t in targets)
    icon = "🗓 PLANNING" if session.get("kind") == "plan" else "🔴 LIVE"
    await update.effective_message.reply_text(
        f"{icon} → {_label(session['group_id'], session['ad_hoc_chats'])}\n"
        f"Buttons: {config.BUTTON_MODE_LABELS[session['buttons_mode']]}\n"
        f"{queued} post(s) scheduled\n\n{names}")


@auth.operator_only
async def scheduled(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    posts = db.pending_posts(None if auth.is_admin(uid) else uid)
    if not posts:
        await update.effective_message.reply_text("Nothing scheduled.")
        return

    lines = []
    for p in posts:
        import datetime as _dt
        when = timeparse.fmt(_dt.datetime.fromisoformat(p["send_at"]))
        lines.append(f"#{p['post_id']}  {when}\n   → {_label(p['group_id'], p['ad_hoc_chats'])}"
                     f" · {config.BUTTON_MODE_LABELS[p['buttons_mode']]}\n   “{p['preview']}”")
    await update.effective_message.reply_text(
        f"🗓 {len(posts)} scheduled\n\n" + "\n\n".join(lines) +
        "\n\nTap to cancel:", reply_markup=keyboards.scheduled_list(posts))


@auth.operator_only
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "/start — single message, live session, or planning session\n"
        "/scheduled — queued posts, tap to cancel\n"
        "/status — what's open right now\n"
        "/cancel — close any session\n"
        "/whoami — your Telegram user id\n\n"
        f"Times are {config.TIMEZONE_LABEL}. You can type:\n"
        "  9am · 9:30pm · 21:00 · tomorrow 9am · 18/09 09:00 · +2h\n"
    )
    if auth.is_admin(update.effective_user.id):
        text += ("\nAdmin:\n/channels /addchannel /delchannel /toggle\n"
                 "/setaff /setdm\n/groups /addgroup /delgroup /assign /unassign\n"
                 "/users /adduser /deluser\n/log\n")
    await update.effective_message.reply_text(text)


# --------------------------------------------------------- scheduling

async def _ask_time(reply_to, context, group_id, chats, buttons_mode,
                    draft, origin: str) -> None:
    context.user_data["sched_draft"] = draft
    context.user_data["sched_ctx"] = {
        "group_id": group_id, "chats": chats,
        "buttons_mode": buttons_mode, "origin": origin,
    }
    context.user_data["flow"] = "await_time"
    await reply_to(
        f"When should this go out?  ({config.TIMEZONE_LABEL})\n\n"
        f"Tap one, or type: 9am · tomorrow 9:30 · 18/09 09:00 · +2h",
        reply_markup=keyboards.time_picker(timeparse.quick_options()))


async def _do_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       raw: str) -> bool:
    """Returns True when the post was queued, False when the time didn't parse."""
    draft = context.user_data.get("sched_draft")
    ctx = context.user_data.get("sched_ctx")
    if not draft or not ctx:
        await update.effective_message.reply_text(
            "That draft expired. Send the message again.")
        _clear(context)
        return True

    try:
        when_utc, note = timeparse.parse(raw)
    except timeparse.TimeParseError as e:
        await update.effective_message.reply_text(
            f"⚠️ {e}\n\nTry: 9am · tomorrow 9:30 · 18/09 09:00 · +2h",
            reply_markup=keyboards.time_picker(timeparse.quick_options()))
        return False

    uid = update.effective_user.id
    targets = _resolve(ctx["group_id"], ctx["chats"])
    if not targets:
        await update.effective_message.reply_text(
            "⚠️ That group has no active channels. Not scheduled.")
        _clear(context)
        return True

    post_id = db.schedule_post(
        uid, ctx["group_id"], ctx["chats"], ctx["buttons_mode"],
        draft[0], draft[1], draft[2], when_utc.isoformat())

    text = (f"🗓 Scheduled #{post_id}\n"
            f"{timeparse.fmt(when_utc)} → {_label(ctx['group_id'], ctx['chats'])} "
            f"({len(targets)} channels)\n"
            f"Buttons: {config.BUTTON_MODE_LABELS[ctx['buttons_mode']]}\n"
            f"“{draft[2]}”")
    if note:
        text += f"\n\nℹ️ {note}"
    for w in _link_warnings(targets, ctx["buttons_mode"]):
        text += f"\n⚠️ {w}"

    context.user_data.pop("sched_draft", None)
    context.user_data.pop("sched_ctx", None)
    context.user_data["flow"] = None

    await update.effective_message.reply_text(
        text, reply_markup=keyboards.scheduled_list([{"post_id": post_id}]))

    if ctx["origin"] == "plan":
        session = db.get_session(uid)
        if session:
            label = _label(session["group_id"], session["ad_hoc_chats"])
            await update.effective_message.reply_text(
                "Next post?",
                reply_markup=keyboards.plan_keyboard(
                    label, session["buttons_mode"], len(db.pending_posts(uid))))
    else:
        _clear(context)
    return True


# --------------------------------------------------------- message entry

@auth.operator_only
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    msg = update.effective_message
    text = msg.text or ""
    session = db.get_session(uid)

    # 1. Keyboard taps arrive as ordinary text. Always intercept first.
    if text == keyboards.KEY_END_SESSION or text == keyboards.KEY_END_PLANNING:
        db.end_session(uid)
        _clear(context)
        queued = len(db.pending_posts(uid))
        note = f"\n{queued} post(s) still scheduled — /scheduled" if queued else ""
        await msg.reply_text(f"⏹ Session ended.{note}",
                             reply_markup=keyboards.remove_keyboard())
        return

    if text.startswith(keyboards.KEY_SCHEDULED):
        await scheduled(update, context)
        return

    if text == keyboards.KEY_SWITCH_GROUP:
        groups = db.list_groups()
        if not groups:
            await msg.reply_text("No groups configured.")
            return
        kind = session.get("kind", "live") if session else "live"
        context.user_data["flow"] = ("plan_await_group" if kind == "plan"
                                     else "session_await_group")
        await msg.reply_text("Switch to:", reply_markup=keyboards.group_picker(
            groups, "plan" if kind == "plan" else "session"))
        return

    if text.startswith(keyboards.KEY_BUTTONS_PREFIX):
        if not session:
            await msg.reply_text("No session open.",
                                 reply_markup=keyboards.remove_keyboard())
            return
        new_mode = keyboards.cycle_buttons_mode(session["buttons_mode"])
        db.set_session_buttons(uid, new_mode)
        label = _label(session["group_id"], session["ad_hoc_chats"])
        kb = (keyboards.plan_keyboard(label, new_mode, len(db.pending_posts(uid)))
              if session.get("kind") == "plan"
              else keyboards.session_keyboard(label, new_mode))
        await msg.reply_text(
            f"Buttons → {config.BUTTON_MODE_LABELS[new_mode]}", reply_markup=kb)
        return

    # 2. Waiting on a time: this text is the time, not content.
    if context.user_data.get("flow") == "await_time":
        await _do_schedule(update, context, text)
        return

    # 3. Mid-flow, waiting on a tap.
    if context.user_data.get("flow") in AWAITING_TAP:
        await msg.reply_text("Finish the step above first, or /cancel.")
        return

    # 4. Planning session: queue instead of sending.
    if session and session.get("kind") == "plan":
        try:
            sender.check_supported(msg)
        except sender.UnsupportedContent as e:
            await msg.reply_text(f"⚠️ Not scheduled.\n\n{e}")
            return
        db.touch_session(uid)
        await _ask_time(msg.reply_text, context, session["group_id"],
                        session["ad_hoc_chats"], session["buttons_mode"],
                        (msg.chat_id, msg.message_id, _preview(msg)), "plan")
        return

    # 5. Live session: send straight away.
    if session:
        if db.session_is_idle(session):
            db.end_session(uid)
            _clear(context)
            await msg.reply_text(
                f"⚠️ Your live session expired after "
                f"{config.SESSION_IDLE_MINUTES} minutes idle, so this message "
                f"was NOT sent.\n\n/start to begin again.",
                reply_markup=keyboards.remove_keyboard())
            return

        try:
            sender.check_supported(msg)
        except sender.UnsupportedContent as e:
            await msg.reply_text(f"⚠️ Not sent.\n\n{e}")
            return

        targets = _resolve(session["group_id"], session["ad_hoc_chats"])
        if not targets:
            await msg.reply_text("⚠️ That group has no active channels. Nothing sent.")
            return

        db.touch_session(uid)
        result = await sender.broadcast(
            context.bot, uid, "session", session["buttons_mode"],
            session["group_id"], targets, msg.chat_id, msg.message_id)
        await _report(update, result,
                      _label(session["group_id"], session["ad_hoc_chats"]), terse=True)
        return

    # 6. No session: single-message draft.
    try:
        sender.check_supported(msg)
    except sender.UnsupportedContent as e:
        await msg.reply_text(f"⚠️ {e}")
        return

    groups = db.list_groups()
    if not groups and not db.list_channels(only_active=True):
        await msg.reply_text("No channels configured yet.")
        return

    context.user_data["draft"] = (msg.chat_id, msg.message_id, _preview(msg))
    context.user_data["flow"] = "single_await_group"
    await msg.reply_text("Send this to:",
                         reply_markup=keyboards.group_picker(groups, "single"))


# ------------------------------------------------------------- callbacks

@auth.operator_only
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    data = q.data or ""
    uid = update.effective_user.id
    await q.answer()

    if data == "cancel":
        _clear(context)
        await q.edit_message_text("Cancelled.")
        return

    if data.startswith("mode:"):
        await _on_mode(q, context, data.split(":", 1)[1])
    elif data.startswith("grp:"):
        _, purpose, value = data.split(":", 2)
        await _on_group(update, q, context, purpose, value)
    elif data.startswith("pick:"):
        _, purpose, chat_id = data.split(":", 2)
        await _on_pick(q, context, purpose, int(chat_id))
    elif data.startswith("picked:"):
        await _on_picked(update, q, context, data.split(":", 1)[1])
    elif data.startswith("btn:"):
        await _on_buttons(q, context, data.split(":", 1)[1])
    elif data == "send":
        await _on_send(update, q, context)
    elif data == "schedule":
        await _on_schedule_tap(q, context)
    elif data.startswith("t:"):
        await _on_time_tap(update, q, context, data.split(":", 1)[1])
    elif data.startswith("cancelpost:"):
        await _on_cancel_post(q, int(data.split(":", 1)[1]), uid)
    elif data.startswith("undo:"):
        await _on_undo(q, context, int(data.split(":", 1)[1]), uid)


async def _on_mode(q, context, mode: str) -> None:
    _clear(context)
    if mode == "single":
        context.user_data["flow"] = "single_await_message"
        await q.edit_message_text("📄 Single message.\n\nSend me the post now.")
        return

    groups = db.list_groups()
    if not groups:
        await q.edit_message_text("No groups configured. Ask the admin to add some.")
        return

    if mode == "plan":
        context.user_data["flow"] = "plan_await_group"
        await q.edit_message_text(
            "🗓 Planning session.\n\nWhich group are you planning for?",
            reply_markup=keyboards.group_picker(groups, "plan"))
    else:
        context.user_data["flow"] = "session_await_group"
        await q.edit_message_text(
            "🔴 Live session.\n\nWhich group goes live?",
            reply_markup=keyboards.group_picker(groups, "session"))


async def _on_group(update, q, context, purpose: str, value: str) -> None:
    if value == "adhoc":
        channels = db.list_channels(only_active=True)
        if not channels:
            await q.edit_message_text("No active channels.")
            return
        context.user_data["flow"] = "adhoc_pick"
        context.user_data["adhoc_purpose"] = purpose
        context.user_data["adhoc_selected"] = set()
        await q.edit_message_text(
            "Tap the channels, then Done:",
            reply_markup=keyboards.channel_picker(channels, set(), purpose))
        return

    group_id = int(value)
    if purpose == "single":
        await _ask_buttons(q, context, group_id, None)
    else:
        await _go_live(update, q, context, group_id, None, purpose)


async def _on_pick(q, context, purpose: str, chat_id: int) -> None:
    selected: set[int] = context.user_data.setdefault("adhoc_selected", set())
    selected.symmetric_difference_update({chat_id})
    await q.edit_message_reply_markup(reply_markup=keyboards.channel_picker(
        db.list_channels(only_active=True), selected, purpose))


async def _on_picked(update, q, context, purpose: str) -> None:
    selected = list(context.user_data.get("adhoc_selected") or [])
    if not selected:
        await q.answer("Pick at least one channel.", show_alert=True)
        return
    if purpose == "single":
        await _ask_buttons(q, context, None, selected)
    else:
        await _go_live(update, q, context, None, selected, purpose)


async def _ask_buttons(q, context, group_id: int | None,
                       chats: list[int] | None) -> None:
    if not context.user_data.get("draft"):
        await q.edit_message_text("That draft expired. Send the message again.")
        _clear(context)
        return
    context.user_data["group_id"] = group_id
    context.user_data["chats"] = chats
    context.user_data["flow"] = "single_await_buttons"
    await q.edit_message_text("Buttons on this post?",
                              reply_markup=keyboards.buttons_picker())


async def _on_buttons(q, context, mode: str) -> None:
    if mode not in config.BUTTON_MODES:
        return
    context.user_data["buttons_mode"] = mode
    group_id = context.user_data.get("group_id")
    chats = context.user_data.get("chats")
    targets = _resolve(group_id, chats)

    if not targets:
        await q.edit_message_text("No active channels in that selection.")
        _clear(context)
        return

    names = "\n".join(f"• {t['title']}" for t in targets)
    text = (f"Send to {_label(group_id, chats)} — {len(targets)} channel(s):\n\n"
            f"{names}\n\nButtons: {config.BUTTON_MODE_LABELS[mode]}")
    warnings = _link_warnings(targets, mode)
    if warnings:
        text += "\n\n⚠️ " + "\n⚠️ ".join(warnings)

    context.user_data["flow"] = "single_await_confirm"
    await q.edit_message_text(text, reply_markup=keyboards.confirm_send())


async def _on_send(update, q, context) -> None:
    draft = context.user_data.get("draft")
    if not draft:
        await q.edit_message_text("That draft expired. Send the message again.")
        _clear(context)
        return

    group_id = context.user_data.get("group_id")
    chats = context.user_data.get("chats")
    buttons_mode = context.user_data.get("buttons_mode", "both")
    targets = _resolve(group_id, chats)

    await q.edit_message_text(f"Sending to {len(targets)} channel(s)…")
    result = await sender.broadcast(
        context.bot, update.effective_user.id, "single", buttons_mode,
        group_id, targets, draft[0], draft[1])
    _clear(context)
    await _report(update, result, _label(group_id, chats), terse=False)


async def _on_schedule_tap(q, context) -> None:
    draft = context.user_data.get("draft")
    if not draft:
        await q.edit_message_text("That draft expired. Send the message again.")
        _clear(context)
        return
    await q.edit_message_text("🕐 Scheduling…")
    await _ask_time(q.message.reply_text, context,
                    context.user_data.get("group_id"),
                    context.user_data.get("chats"),
                    context.user_data.get("buttons_mode", "both"),
                    draft, "single")


async def _on_time_tap(update, q, context, raw: str) -> None:
    if context.user_data.get("flow") != "await_time":
        await q.answer("That prompt expired.", show_alert=True)
        return
    await q.edit_message_reply_markup(reply_markup=None)
    await _do_schedule(update, context, raw)


async def _on_cancel_post(q, post_id: int, uid: int) -> None:
    outcome = db.cancel_post(post_id, uid, auth.is_admin(uid))
    messages = {
        "ok": f"🗓 Post #{post_id} cancelled.",
        "not_found": f"No post #{post_id}.",
        "not_yours": "That isn't yours to cancel.",
        "sent": f"Post #{post_id} has already gone out — undo it instead.",
        "cancelled": f"Post #{post_id} was already cancelled.",
    }
    await q.message.reply_text(
        messages.get(outcome, f"Post #{post_id} is '{outcome}'."))


async def _on_undo(q, context, broadcast_id: int, uid: int) -> None:
    b = db.get_broadcast(broadcast_id)
    if not b:
        await q.answer("Unknown broadcast.", show_alert=True)
        return
    if b["undone_at"]:
        await q.answer("Already undone.", show_alert=True)
        return
    if b["user_id"] != uid and not auth.is_admin(uid):
        await q.answer("That isn't yours to undo.", show_alert=True)
        return
    if db.undo_expired(broadcast_id):
        await q.answer(f"The {config.UNDO_WINDOW_MINUTES}-minute undo window has "
                       f"closed. Delete manually.", show_alert=True)
        await q.edit_message_reply_markup(reply_markup=None)
        return

    await q.edit_message_reply_markup(reply_markup=None)
    result = await sender.undo(context.bot, broadcast_id)
    text = f"↩️ Deleted from {result['deleted']} channel(s)."
    if result["errors"]:
        text += f"\n⚠️ {result['errors']} could not be deleted — check manually."
    await q.message.reply_text(text)


async def _go_live(update, q, context, group_id: int | None,
                   chats: list[int] | None, purpose: str) -> None:
    uid = update.effective_user.id
    targets = _resolve(group_id, chats)
    if not targets:
        await q.edit_message_text("No active channels in that selection.")
        _clear(context)
        return

    kind = "plan" if purpose == "plan" else "live"
    db.start_session(uid, group_id, chats, buttons_mode="both", kind=kind)
    _clear(context)

    label = _label(group_id, chats)
    names = "\n".join(f"• {t['title']}" for t in targets)

    if kind == "plan":
        await q.edit_message_text(f"🗓 Planning → {label}\n\n{names}")
        await q.message.reply_text(
            f"Send a post, then give it a time. Nothing goes out until its "
            f"scheduled moment.\n\nTimes are {config.TIMEZONE_LABEL}.",
            reply_markup=keyboards.plan_keyboard(
                label, "both", len(db.pending_posts(uid))))
    else:
        await q.edit_message_text(f"🔴 Live → {label}\n\n{names}")
        await q.message.reply_text(
            f"Every message you send now goes to these {len(targets)} channels "
            f"immediately — no confirmation.\n\n"
            f"Auto-stops after {config.SESSION_IDLE_MINUTES} minutes idle.",
            reply_markup=keyboards.session_keyboard(label, "both"))
