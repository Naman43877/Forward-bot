"""Admin-only management commands. These exist so channel and link changes can
be made from a phone instead of by SSHing in and editing SQLite by hand."""
import logging

from telegram import Update
from telegram.ext import ContextTypes

import auth
import db

log = logging.getLogger(__name__)


def _args(update: Update) -> list[str]:
    """Everything after the command, split on '|' and stripped."""
    text = update.effective_message.text or ""
    _, _, rest = text.partition(" ")
    if not rest.strip():
        return []
    return [p.strip() for p in rest.split("|")]


def _ids(blob: str) -> list[int]:
    out = []
    for part in blob.replace(",", " ").split():
        try:
            out.append(int(part))
        except ValueError:
            pass
    return out


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    row = db.get_user(u.id)
    role = row["role"] if row else "not registered"
    await update.effective_message.reply_text(
        f"user_id: {u.id}\nname: {u.full_name}\nrole: {role}")


# ------------------------------------------------------------- channels

@auth.admin_only
async def addchannel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    a = _args(update)
    if len(a) < 2:
        await update.effective_message.reply_text(
            "Usage:\n/addchannel <chat_id> | <title> | <lang>\n\n"
            "Example:\n/addchannel -1001234567890 | Arabic Main | ar\n\n"
            "Tip: add the bot as admin to the channel and it registers itself.")
        return
    try:
        chat_id = int(a[0])
    except ValueError:
        await update.effective_message.reply_text("chat_id must be a number.")
        return
    lang = a[2] if len(a) > 2 else None
    db.upsert_channel(chat_id, a[1], lang)
    await update.effective_message.reply_text(
        f"Saved: {a[1]} ({chat_id})\nNow set links with /setaff and /setdm.")


@auth.admin_only
async def setaff(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    a = _args(update)
    if len(a) < 2:
        await update.effective_message.reply_text(
            "Usage:\n/setaff <chat_id> | <url> | <button text>\n\n"
            "The button text is what viewers see, so write it in that "
            "channel's language.")
        return
    try:
        chat_id = int(a[0])
    except ValueError:
        await update.effective_message.reply_text("chat_id must be a number.")
        return
    text = a[2] if len(a) > 2 else None
    if db.set_affiliate(chat_id, a[1], text):
        ch = db.get_channel(chat_id)
        await update.effective_message.reply_text(
            f"{ch['title']}\nAffiliate button: [{ch['affiliate_text']}] → {ch['affiliate_url']}")
    else:
        await update.effective_message.reply_text("No such channel. Add it first.")


@auth.admin_only
async def setdm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    a = _args(update)
    if len(a) < 2:
        await update.effective_message.reply_text(
            "Usage:\n/setdm <chat_id> | <url> | <button text>")
        return
    try:
        chat_id = int(a[0])
    except ValueError:
        await update.effective_message.reply_text("chat_id must be a number.")
        return
    text = a[2] if len(a) > 2 else None
    if db.set_dm(chat_id, a[1], text):
        ch = db.get_channel(chat_id)
        await update.effective_message.reply_text(
            f"{ch['title']}\nDM button: [{ch['dm_text']}] → {ch['dm_url']}")
    else:
        await update.effective_message.reply_text("No such channel. Add it first.")


@auth.admin_only
async def channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = db.list_channels()
    if not rows:
        await update.effective_message.reply_text("No channels yet.")
        return
    lines = []
    for c in rows:
        flags = []
        if not c["active"]:
            flags.append("PAUSED")
        if not c["affiliate_url"]:
            flags.append("no affiliate link")
        if not c["dm_url"]:
            flags.append("no DM link")
        groups = ", ".join(g["name"] for g in db.groups_for_channel(c["chat_id"]))
        lines.append(
            f"{c['title']}  [{c['lang'] or '—'}]\n"
            f"  {c['chat_id']}\n"
            f"  groups: {groups or 'none'}"
            + (f"\n  ⚠️ {'; '.join(flags)}" if flags else ""))
    await update.effective_message.reply_text(
        f"{len(rows)} channels\n\n" + "\n\n".join(lines))


@auth.admin_only
async def toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    a = _args(update)
    if not a:
        await update.effective_message.reply_text("Usage: /toggle <chat_id>")
        return
    try:
        chat_id = int(a[0])
    except ValueError:
        await update.effective_message.reply_text("chat_id must be a number.")
        return
    ch = db.get_channel(chat_id)
    if not ch:
        await update.effective_message.reply_text("No such channel.")
        return
    new = not bool(ch["active"])
    db.set_channel_active(chat_id, new)
    await update.effective_message.reply_text(
        f"{ch['title']} is now {'ACTIVE' if new else 'PAUSED'}.")


@auth.admin_only
async def delchannel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    a = _args(update)
    if not a:
        await update.effective_message.reply_text("Usage: /delchannel <chat_id>")
        return
    try:
        chat_id = int(a[0])
    except ValueError:
        await update.effective_message.reply_text("chat_id must be a number.")
        return
    ok = db.delete_channel(chat_id)
    await update.effective_message.reply_text("Deleted." if ok else "No such channel.")


# --------------------------------------------------------------- groups

@auth.admin_only
async def addgroup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    a = _args(update)
    if not a:
        await update.effective_message.reply_text(
            "Usage: /addgroup <name> | <sort order>")
        return
    order = 0
    if len(a) > 1:
        try:
            order = int(a[1])
        except ValueError:
            pass
    gid = db.add_group(a[0], order)
    if gid is None:
        await update.effective_message.reply_text("A group with that name exists.")
    else:
        await update.effective_message.reply_text(
            f"Group '{a[0]}' created.\nAdd channels: /assign {a[0]} | -100..., -100...")


@auth.admin_only
async def groups(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = db.list_groups()
    if not rows:
        await update.effective_message.reply_text("No groups yet. Use /addgroup.")
        return
    lines = []
    for g in rows:
        members = db.channels_in_group(g["group_id"])
        names = "\n".join(f"   • {m['title']}" for m in members) or "   (empty)"
        lines.append(f"{g['name']}  ({len(members)} active)\n{names}")
    await update.effective_message.reply_text("\n\n".join(lines))


@auth.admin_only
async def delgroup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    a = _args(update)
    if not a:
        await update.effective_message.reply_text("Usage: /delgroup <name>")
        return
    g = db.get_group_by_name(a[0])
    if not g:
        await update.effective_message.reply_text("No such group.")
        return
    db.delete_group(g["group_id"])
    await update.effective_message.reply_text(
        f"Group '{g['name']}' deleted. The channels themselves are untouched.")


@auth.admin_only
async def assign(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    a = _args(update)
    if len(a) < 2:
        await update.effective_message.reply_text(
            "Usage:\n/assign <group name> | <chat_id>, <chat_id>, ...\n\n"
            "A channel can sit in several groups.")
        return
    g = db.get_group_by_name(a[0])
    if not g:
        await update.effective_message.reply_text("No such group.")
        return
    ids = _ids(a[1])
    known = {c["chat_id"] for c in db.channels_by_ids(ids, only_active=False)}
    unknown = [i for i in ids if i not in known]
    db.assign(g["group_id"], list(known))
    msg = f"{g['name']} now has {len(db.channels_in_group(g['group_id']))} active channels."
    if unknown:
        msg += f"\n⚠️ Not registered, skipped: {unknown}"
    await update.effective_message.reply_text(msg)


@auth.admin_only
async def unassign(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    a = _args(update)
    if len(a) < 2:
        await update.effective_message.reply_text(
            "Usage: /unassign <group name> | <chat_id>, ...")
        return
    g = db.get_group_by_name(a[0])
    if not g:
        await update.effective_message.reply_text("No such group.")
        return
    n = db.unassign(g["group_id"], _ids(a[1]))
    await update.effective_message.reply_text(f"Removed {n} channel(s) from {g['name']}.")


# ---------------------------------------------------------------- users

@auth.admin_only
async def adduser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    a = _args(update)
    if len(a) < 2:
        await update.effective_message.reply_text(
            "Usage: /adduser <user_id> | <name> | <role>\n"
            "role is 'operator' (default) or 'admin'.\n"
            "They can get their user_id by sending /whoami to this bot.")
        return
    try:
        uid = int(a[0])
    except ValueError:
        await update.effective_message.reply_text("user_id must be a number.")
        return
    role = a[2] if len(a) > 2 and a[2] in ("admin", "operator") else "operator"
    db.add_user(uid, a[1], role)
    await update.effective_message.reply_text(f"Added {a[1]} ({uid}) as {role}.")


@auth.admin_only
async def users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = db.list_users()
    lines = [f"{r['name'] or '—'} · {r['user_id']} · {r['role']}" for r in rows]
    await update.effective_message.reply_text("\n".join(lines) or "No users.")


@auth.admin_only
async def deluser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    a = _args(update)
    if not a:
        await update.effective_message.reply_text("Usage: /deluser <user_id>")
        return
    try:
        uid = int(a[0])
    except ValueError:
        await update.effective_message.reply_text("user_id must be a number.")
        return
    if uid == update.effective_user.id:
        await update.effective_message.reply_text("You can't remove yourself.")
        return
    db.remove_user(uid)
    db.end_session(uid)
    await update.effective_message.reply_text("Removed.")


@auth.admin_only
async def log_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = db.recent_broadcasts(15)
    if not rows:
        await update.effective_message.reply_text("Nothing sent yet.")
        return
    lines = []
    for b in rows:
        who = db.get_user(b["user_id"])
        stamp = b["created_at"][:16].replace("T", " ")
        target = b["group_name"] or "ad-hoc"
        undone = " ↩️ undone" if b["undone_at"] else ""
        lines.append(f"#{b['broadcast_id']} {stamp}  {who['name'] if who else b['user_id']}"
                     f"  → {target}  ({b['n_sent']} sent){undone}")
    await update.effective_message.reply_text("\n".join(lines))


# ------------------------------------------- auto-registration of channels

async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """When the bot is added as an admin to a channel, register it and tell the
    admins. Saves hunting for chat_ids by hand."""
    cm = update.my_chat_member
    if not cm or cm.chat.type != "channel":
        return

    status = cm.new_chat_member.status
    title = cm.chat.title or str(cm.chat.id)

    if status == "administrator":
        existing = db.get_channel(cm.chat.id)
        db.upsert_channel(cm.chat.id, title, active=1)
        if existing:
            return
        note = (f"📡 New channel registered\n\n{title}\n{cm.chat.id}\n\n"
                f"Set it up:\n/setaff {cm.chat.id} | <url> | <button text>\n"
                f"/setdm {cm.chat.id} | <url> | <button text>\n"
                f"/assign <group> | {cm.chat.id}\n\n"
                f"⚠️ Also turn OFF 'Sign messages' in the channel settings.")
    elif status in ("left", "kicked", "member"):
        if not db.get_channel(cm.chat.id):
            return
        db.set_channel_active(cm.chat.id, False)
        note = (f"⚠️ Lost admin rights in {title} ({cm.chat.id}).\n"
                f"It has been paused and will be skipped in broadcasts.")
    else:
        return

    for u in db.list_users():
        if u["role"] == "admin":
            try:
                await context.bot.send_message(u["user_id"], note)
            except Exception as e:  # noqa: BLE001 - never let a notify failure crash
                log.error("Could not notify admin %s: %s", u["user_id"], e)
