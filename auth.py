"""Access control. Anyone not in the users table is ignored entirely — the bot
holds posting rights to every channel you own, so it must never respond to a
stranger who finds it."""
import functools
import logging
from typing import Callable

from telegram import Update
from telegram.ext import ContextTypes

import db

log = logging.getLogger(__name__)


def _user_id(update: Update) -> int | None:
    if update.effective_user:
        return update.effective_user.id
    return None


async def _deny(update: Update) -> None:
    uid = _user_id(update)
    log.warning("Unauthorised access attempt from user_id=%s", uid)
    if update.callback_query:
        await update.callback_query.answer("Not authorised.", show_alert=True)
    elif update.effective_message:
        await update.effective_message.reply_text("Not authorised.")


def operator_only(func: Callable) -> Callable:
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *a, **kw):
        uid = _user_id(update)
        if uid is None or db.get_user(uid) is None:
            return await _deny(update)
        return await func(update, context, *a, **kw)
    return wrapper


def admin_only(func: Callable) -> Callable:
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *a, **kw):
        uid = _user_id(update)
        user = db.get_user(uid) if uid is not None else None
        if user is None or user["role"] != "admin":
            return await _deny(update)
        return await func(update, context, *a, **kw)
    return wrapper


def is_admin(user_id: int) -> bool:
    user = db.get_user(user_id)
    return user is not None and user["role"] == "admin"
