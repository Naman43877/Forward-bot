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


def _clean_url(raw: str) -> str | None:
    """Strip wrapper characters that survive copy-paste, then validate."""
    u = (raw or "").strip().strip("<>[](){}\"'` ")
    if not u or " " in u or not u.lower().startswith(("https://", "http://", "tg://")):
        return None
    return u


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
    url = _clean_url(a[1])
    if not url:
        await update.effective_message.reply_text(
            f"That doesn't look like a valid URL:\n{a[1]}\n\n"
            "Paste it bare — no < >, no [ ], must start with https://")
        return
    text = a[2] if len(a) > 2 else None
    if db.set_affiliate(chat_id, url, text):
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
    url = _clean_url(a[1])
    if not url:
        await update.effective_message.reply_text(
            f"That doesn't look like a valid URL:\n{a[1]}\n\n"
            "Paste it bare — no < >, no [ ], must start with https://")
        return
    text = a[2] if len(a) > 2 else None
    if db.set_dm(chat_id, url, text):
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
async def links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Every configured URL, laid out by group, so an import or a campaign
    change can be eyeballed rather than trusted."""
    a = _args(update)
    groups = db.list_groups()
    if a:
        g = db.get_group_by_name(a[0])
        if not g:
            existing = [x["name"] for x in groups]
            await update.effective_message.reply_text(
                f"No group called '{a[0]}'.\n\n"
                + (f"Existing: {', '.join(existing)}" if existing else "No groups yet."))
            return
        groups = [g]

    if not groups:
        await update.effective_message.reply_text("No groups yet.")
        return

    blocks = []
    for g in groups:
        rows = []
        for c in db.channels_in_group(g["group_id"]):
            aff = (f"   ↳ [{c['affiliate_text']}] {c['affiliate_url']}"
                   if c["affiliate_url"] else "   ↳ ⚠️ no affiliate link")
            dm = (f"   ↳ [{c['dm_text']}] {c['dm_url']}"
                  if c["dm_url"] else "   ↳ ⚠️ no DM link")
            rows.append(f"• {c['title']}  ({c['chat_id']})\n{aff}\n{dm}")
        blocks.append(f"━━ {g['name']} ━━\n"
                      + ("\n".join(rows) if rows else "(no channels)"))

    text = "\n\n".join(blocks)
    # Telegram caps a message at 4096 characters; send in parts if needed.
    chunk = ""
    for block in text.split("\n\n"):
        if len(chunk) + len(block) + 2 > 3900:
            await update.effective_message.reply_text(chunk)
            chunk = ""
        chunk += ("\n\n" if chunk else "") + block
    if chunk:
        await update.effective_message.reply_text(chunk)


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
    existing = db.get_group_by_name(a[0])
    if existing:
        await update.effective_message.reply_text(
            f"'{existing['name']}' already exists "
            f"({len(db.channels_in_group(existing['group_id']))} channels). "
            f"Names are case-insensitive.\n\nSee them all: /groups")
        return

    near = db.similar_groups(a[0])
    forced = len(a) > 2 and a[2].lower() == "confirm"
    if near and not forced:
        await update.effective_message.reply_text(
            f"⚠️ '{a[0]}' looks close to: {', '.join(near)}\n\n"
            f"Did you mean one of those? Check with /groups.\n"
            f"To create it anyway:\n/addgroup {a[0]} | {order} | confirm")
        return

    gid = db.add_group(a[0], order)
    if gid is None:
        await update.effective_message.reply_text("Could not create that group.")
        return
    await update.effective_message.reply_text(
        f"Group '{a[0]}' created.\nAdd channels: /assign {a[0]} | -100..., -100...")


@auth.operator_only
async def groups(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Readable by anyone who can broadcast — you cannot pick a group you
    cannot see, and not seeing them is how duplicates get created."""
    rows = db.list_groups()
    if not rows:
        await update.effective_message.reply_text(
            "No groups yet."
            + (" Create one: /addgroup <name>"
               if auth.is_admin(update.effective_user.id) else ""))
        return

    lines = []
    for g in rows:
        members = db.channels_in_group(g["group_id"])
        names = "\n".join(f"   • {m['title']}" for m in members) or "   (empty)"
        lines.append(f"{g['name']}  ({len(members)} active)\n{names}")

    total = len(db.list_channels(only_active=True))
    ungrouped = [c["title"] for c in db.list_channels(only_active=True)
                 if not db.groups_for_channel(c["chat_id"])]
    text = f"🗂 {len(rows)} groups · {total} active channels\n\n" + "\n\n".join(lines)
    if ungrouped:
        text += ("\n\n⚠️ In no group (unreachable unless picked manually):\n"
                 + "\n".join(f"   • {t}" for t in ungrouped))
    await update.effective_message.reply_text(text)


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


@auth.admin_only
async def import_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bulk setup from a pasted block. Idempotent — safe to re-send."""
    text = update.effective_message.text or ""
    body = text.split("\n", 1)[1] if "\n" in text else ""
    if not body.strip():
        await update.effective_message.reply_text(
            "Paste an import block after /import, one item per line:\n\n"
            "CHANNEL | chat_id | title | lang | aff_url | aff_text | dm_url | dm_text\n"
            "GROUP | name | sort_order\n"
            "MEMBER | group name | chat_id\n\n"
            "Generate it from your old database with:\n"
            "python export_config.py\n\n"
            "Re-sending the same block is safe — it updates rather than duplicates.")
        return

    counts = {"channel": 0, "group": 0, "member": 0, "user": 0}
    errors: list[str] = []

    for n, raw in enumerate(body.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        kind = parts[0].upper()

        try:
            if kind == "CHANNEL":
                chat_id = int(parts[1])
                db.upsert_channel(chat_id, parts[2] or str(chat_id),
                                  parts[3] or None)
                aff = _clean_url(parts[4]) if len(parts) > 4 and parts[4] else None
                if len(parts) > 4 and parts[4] and not aff:
                    errors.append(f"line {n}: bad affiliate URL, skipped")
                elif aff:
                    db.set_affiliate(chat_id, aff,
                                     parts[5] if len(parts) > 5 and parts[5] else None)
                dm = _clean_url(parts[6]) if len(parts) > 6 and parts[6] else None
                if len(parts) > 6 and parts[6] and not dm:
                    errors.append(f"line {n}: bad DM URL, skipped")
                elif dm:
                    db.set_dm(chat_id, dm,
                              parts[7] if len(parts) > 7 and parts[7] else None)
                counts["channel"] += 1

            elif kind == "GROUP":
                order = int(parts[2]) if len(parts) > 2 and parts[2] else 0
                if db.get_group_by_name(parts[1]) is None:
                    db.add_group(parts[1], order)
                counts["group"] += 1

            elif kind == "MEMBER":
                g = db.get_group_by_name(parts[1])
                if not g:
                    errors.append(f"line {n}: no group '{parts[1]}'")
                    continue
                chat_id = int(parts[2])
                if not db.get_channel(chat_id):
                    errors.append(f"line {n}: channel {chat_id} not added yet")
                    continue
                db.assign(g["group_id"], [chat_id])
                counts["member"] += 1

            elif kind == "USER":
                role = parts[3] if len(parts) > 3 and parts[3] in ("admin", "operator") \
                    else "operator"
                db.add_user(int(parts[1]), parts[2], role)
                counts["user"] += 1

            else:
                errors.append(f"line {n}: unknown type '{parts[0]}'")

        except (IndexError, ValueError) as e:
            errors.append(f"line {n}: {e}")

    msg = (f"✅ Import done\n"
           f"{counts['channel']} channels · {counts['group']} groups · "
           f"{counts['member']} assignments"
           + (f" · {counts['user']} users" if counts["user"] else ""))
    if errors:
        msg += "\n\n⚠️ " + "\n⚠️ ".join(errors[:15])
        if len(errors) > 15:
            msg += f"\n… and {len(errors) - 15} more"
    msg += "\n\nCheck it with /channels and /groups."
    await update.effective_message.reply_text(msg)


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
