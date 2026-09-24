"""Polls SQLite for due posts. Polling rather than an in-memory scheduler on
purpose: a restart needs no job restoration, the queue is simply still there."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import config
import db
import sender
import timeparse

log = logging.getLogger(__name__)


def _resolve(post: dict):
    if post["group_id"] is not None:
        return db.channels_in_group(post["group_id"])
    return db.channels_by_ids(post["ad_hoc_chats"])


def _label(post: dict) -> str:
    if post["group_id"] is not None:
        g = db.get_group(post["group_id"])
        return g["name"] if g else "deleted group"
    n = len(post["ad_hoc_chats"])
    return f"{n} channel{'s' if n != 1 else ''}"


async def _notify(bot, user_id: int, text: str, markup=None) -> None:
    try:
        await bot.send_message(user_id, text, reply_markup=markup)
    except Exception as e:  # noqa: BLE001 — a failed notice must not stop the queue
        log.error("Could not notify %s: %s", user_id, e)


async def run_due(bot) -> None:
    import keyboards  # local import keeps the module import graph acyclic

    for post in db.claim_due_posts():
        if post is None:
            continue

        due = datetime.fromisoformat(post["send_at"])
        late = datetime.now(timezone.utc) - due

        # Bot was down past the grace window: better a gap than a 3pm
        # "good morning" post.
        if late > timedelta(minutes=config.SCHEDULE_GRACE_MINUTES):
            db.finish_post(post["post_id"], "missed",
                           error=f"{int(late.total_seconds() // 60)} min late")
            await _notify(
                bot, post["user_id"],
                f"⚠️ Missed scheduled post #{post['post_id']}\n"
                f"Was due {timeparse.fmt(due)} → {_label(post)}\n"
                f"“{post['preview']}”\n\n"
                f"It was {int(late.total_seconds() // 60)} minutes late, so it "
                f"was NOT sent. Post it manually if it's still relevant.")
            continue

        targets = _resolve(post)
        if not targets:
            db.finish_post(post["post_id"], "failed", error="no active channels")
            await _notify(
                bot, post["user_id"],
                f"⚠️ Scheduled post #{post['post_id']} was not sent — "
                f"{_label(post)} has no active channels.\n“{post['preview']}”")
            continue

        try:
            result = await sender.broadcast(
                bot, post["user_id"], "scheduled", post["buttons_mode"],
                post["group_id"], targets, post["src_chat_id"],
                post["src_message_id"])
        except Exception as e:  # noqa: BLE001
            log.exception("Scheduled send blew up")
            db.finish_post(post["post_id"], "failed", error=str(e)[:200])
            await _notify(bot, post["user_id"],
                          f"⚠️ Scheduled post #{post['post_id']} failed: {e}")
            continue

        if not result["ok"]:
            # Nothing landed anywhere — almost always a network outage, not a
            # per-channel problem. Put it back in the queue: the next tick
            # retries, and the grace window above caps how long that goes on.
            # Only when zero succeeded; a partial send re-queued would
            # duplicate the post in the channels that did receive it.
            reasons = "; ".join(sorted({e for _, e in result["failed"]}))[:200]
            db.requeue_post(post["post_id"], reasons)
            log.warning("Post #%s reached no channels (%s) — re-queued",
                        post["post_id"], reasons)
            continue

        db.finish_post(post["post_id"], "sent", broadcast_id=result["broadcast_id"])

        text = f"🗓 Sent as scheduled → {_label(post)} ({len(result['ok'])} channels)"
        if result["failed"]:
            text += "\n\n❌ " + "\n".join(f"{t}: {e}" for t, e in result["failed"])
        await _notify(bot, post["user_id"], text,
                      keyboards.undo(result["broadcast_id"]) if result["ok"] else None)


async def loop(app) -> None:
    stuck = db.reap_stuck_sending()
    if stuck:
        log.warning("Reset %d post(s) stuck in 'sending' from a previous run", stuck)
    log.info("Scheduler running, tick every %ss", config.SCHEDULER_TICK_SECONDS)

    while True:
        try:
            await run_due(app.bot)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the loop must never die
            log.exception("Scheduler tick failed")
        await asyncio.sleep(config.SCHEDULER_TICK_SECONDS)


async def start(app) -> None:
    app.bot_data["scheduler_task"] = asyncio.create_task(loop(app))


async def stop(app) -> None:
    task = app.bot_data.get("scheduler_task")
    if task:
        task.cancel()
