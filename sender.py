"""Broadcast execution: copy one source message into many channels, each with
its own inline keyboard, then support deleting the lot again."""
import asyncio
import logging
from typing import Any, Mapping, Sequence

from telegram import Bot, Message
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError

import config
import db
import keyboards

log = logging.getLogger(__name__)

MAX_RETRIES = 3


class UnsupportedContent(Exception):
    """Raised for content the bot deliberately refuses to broadcast."""


def check_supported(message: Message) -> None:
    """Reject the two content types that cannot work correctly in v1.

    Albums: Telegram does not allow an inline keyboard on a media group, so the
    affiliate button would silently vanish.
    Polls: copy_message cannot reproduce a poll; it needs a separate send path.
    """
    if getattr(message, "media_group_id", None):
        raise UnsupportedContent(
            "Albums (multiple photos sent together) can't carry the affiliate "
            "button — Telegram doesn't allow it.\n\nSend the images one at a time."
        )
    if getattr(message, "poll", None):
        raise UnsupportedContent(
            "Polls can't be broadcast by this bot. Post polls manually for now."
        )


async def _copy_one(bot: Bot, target: Mapping[str, Any], src_chat_id: int,
                    src_message_id: int, buttons_mode: str) -> tuple[int | None, str | None]:
    """Returns (message_id, error). Retries on Telegram rate limiting."""
    markup = keyboards.channel_markup(target, buttons_mode)

    for attempt in range(MAX_RETRIES):
        try:
            result = await bot.copy_message(
                chat_id=target["chat_id"],
                from_chat_id=src_chat_id,
                message_id=src_message_id,
                reply_markup=markup,
            )
            return result.message_id, None

        except RetryAfter as e:
            wait = float(getattr(e, "retry_after", 2)) + 0.5
            log.warning("Rate limited on %s, sleeping %.1fs", target["title"], wait)
            await asyncio.sleep(wait)

        except Forbidden:
            return None, "bot is not an admin (or was removed)"

        except BadRequest as e:
            return None, f"rejected: {e}"

        except TelegramError as e:
            if attempt == MAX_RETRIES - 1:
                return None, str(e)
            await asyncio.sleep(1.5 * (attempt + 1))

    return None, "gave up after rate limiting"


async def broadcast(bot: Bot, user_id: int, mode: str, buttons_mode: str,
                    group_id: int | None, targets: Sequence[Mapping[str, Any]],
                    src_chat_id: int, src_message_id: int) -> dict[str, Any]:
    """Send to every target serially. Serial is deliberate: parallel fan-out is
    the fastest way to trip Telegram's flood limits, and 15 channels takes
    under a second anyway."""
    broadcast_id = db.create_broadcast(
        user_id, mode, buttons_mode, group_id, src_chat_id, src_message_id)

    ok: list[str] = []
    failed: list[tuple[str, str]] = []

    for i, target in enumerate(targets):
        message_id, error = await _copy_one(
            bot, target, src_chat_id, src_message_id, buttons_mode)

        if message_id is not None:
            db.record_target(broadcast_id, target["chat_id"], message_id, "sent")
            ok.append(target["title"])
        else:
            db.record_target(broadcast_id, target["chat_id"], None, "failed", error)
            failed.append((target["title"], error or "unknown"))
            log.error("Send failed for %s: %s", target["title"], error)

        if i < len(targets) - 1:
            await asyncio.sleep(config.SEND_DELAY_SECONDS)

    return {"broadcast_id": broadcast_id, "ok": ok, "failed": failed}


async def undo(bot: Bot, broadcast_id: int) -> dict[str, int]:
    """Delete every message this broadcast produced. Bots can delete their own
    messages in a channel for 48h, so a 10-minute undo window is safe."""
    deleted = 0
    errors = 0

    for target in db.sent_targets(broadcast_id):
        try:
            await bot.delete_message(chat_id=target["chat_id"],
                                     message_id=target["message_id"])
            db.mark_target_deleted(broadcast_id, target["chat_id"])
            deleted += 1
        except TelegramError as e:
            log.error("Undo failed for chat %s: %s", target["chat_id"], e)
            errors += 1
        await asyncio.sleep(config.SEND_DELAY_SECONDS)

    db.mark_undone(broadcast_id)
    return {"deleted": deleted, "errors": errors}
