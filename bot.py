"""Entrypoint. Long polling — no webhook, no public HTTPS needed."""
import html
import logging
import traceback

from telegram import Update
from telegram.error import BadRequest
from telegram.constants import ChatMemberStatus  # noqa: F401  (kept for clarity)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

import admin_handlers as ah
import auth
import broadcast_handlers as bh
import config
import db
import scheduler

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("broadcast-bot")


async def on_error(update: object, context) -> None:
    """Log the traceback and DM it to the admins, so failures are never silent."""
    err = context.error
    # A double-tapped button asks Telegram to re-apply an edit that's already
    # there. Harmless — the first tap did the work — so don't page anyone.
    if isinstance(err, BadRequest) and "message is not modified" in str(err).lower():
        log.debug("Ignored duplicate edit (double tap)")
        return
    log.error("Unhandled exception", exc_info=err)
    tb = "".join(traceback.format_exception(None, context.error,
                                            context.error.__traceback__))[-1500:]
    text = f"⚠️ Bot error\n\n<pre>{html.escape(tb)}</pre>"
    for u in db.list_users():
        if u["role"] == "admin":
            try:
                await context.bot.send_message(u["user_id"], text, parse_mode="HTML")
            except Exception:  # noqa: BLE001
                pass


def build() -> Application:
    config.validate()
    db.connect()
    db.seed_admins(config.ADMIN_IDS)

    app = (Application.builder()
           .token(config.BOT_TOKEN)
           .post_init(scheduler.start)
           .post_shutdown(scheduler.stop)
           .build())

    # --- operator ---
    app.add_handler(CommandHandler("start", bh.start))
    app.add_handler(CommandHandler("cancel", bh.cancel))
    app.add_handler(CommandHandler("status", bh.status))
    app.add_handler(CommandHandler("scheduled", bh.scheduled))
    app.add_handler(CommandHandler("help", bh.help_cmd))
    app.add_handler(CommandHandler("whoami", ah.whoami))
    app.add_handler(CommandHandler("groups", ah.groups))

    # --- admin ---
    app.add_handler(CommandHandler("addchannel", ah.addchannel))
    app.add_handler(CommandHandler("delchannel", ah.delchannel))
    app.add_handler(CommandHandler("channels", ah.channels))
    app.add_handler(CommandHandler("toggle", ah.toggle))
    app.add_handler(CommandHandler("setaff", ah.setaff))
    app.add_handler(CommandHandler("setdm", ah.setdm))
    app.add_handler(CommandHandler("addgroup", ah.addgroup))
    app.add_handler(CommandHandler("delgroup", ah.delgroup))
    app.add_handler(CommandHandler("assign", ah.assign))
    app.add_handler(CommandHandler("unassign", ah.unassign))
    app.add_handler(CommandHandler("adduser", ah.adduser))
    app.add_handler(CommandHandler("deluser", ah.deluser))
    app.add_handler(CommandHandler("users", ah.users))
    app.add_handler(CommandHandler("log", ah.log_cmd))
    app.add_handler(CommandHandler("import", ah.import_cmd))
    app.add_handler(CommandHandler("links", ah.links))

    app.add_handler(CallbackQueryHandler(bh.on_callback))

    # Auto-register channels when the bot is promoted to admin.
    app.add_handler(ChatMemberHandler(ah.on_my_chat_member,
                                      ChatMemberHandler.MY_CHAT_MEMBER))

    # Everything else in a private chat is broadcast content.
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.COMMAND & ~filters.StatusUpdate.ALL,
        bh.on_message))

    app.add_error_handler(on_error)
    return app


def main() -> None:
    app = build()
    log.info("Starting. Admins: %s", config.ADMIN_IDS)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
