"""All keyboard construction lives here so the handlers stay readable."""
from typing import Any, Mapping, Sequence

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

import config

# --- session reply keyboard labels (matched on exactly in handlers) ---
KEY_SWITCH_GROUP = "🔀 Switch group"
KEY_END_SESSION = "⏹ End session"
KEY_BUTTONS_PREFIX = "🔘 "
KEY_SCHEDULED = "📋 Scheduled"
KEY_END_PLANNING = "⏹ End planning"


def _field(row: Any, key: str) -> Any:
    """sqlite3.Row supports [] but not .get(); dicts support both."""
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def channel_markup(channel: Mapping[str, Any], buttons_mode: str
                   ) -> InlineKeyboardMarkup | None:
    """The inline keyboard attached to the post in ONE channel.

    Returns None when the channel has no configured URLs or mode is 'none',
    which makes copy_message send the post with no keyboard at all.
    """
    if buttons_mode == "none":
        return None

    row: list[InlineKeyboardButton] = []
    aff_url = _field(channel, "affiliate_url")
    dm_url = _field(channel, "dm_url")

    if buttons_mode in ("both", "affiliate") and aff_url:
        row.append(InlineKeyboardButton(
            _field(channel, "affiliate_text") or "Join Now", url=aff_url))
    if buttons_mode in ("both", "dm") and dm_url:
        row.append(InlineKeyboardButton(
            _field(channel, "dm_text") or "Chat with us", url=dm_url))

    if not row:
        return None
    # One row of up to two buttons keeps them side by side and thumb-friendly.
    return InlineKeyboardMarkup([row])


def mode_picker() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Single message", callback_data="mode:single")],
        [InlineKeyboardButton("🔴 Live session", callback_data="mode:session")],
        [InlineKeyboardButton("🗓 Planning session", callback_data="mode:plan")],
    ])


def group_picker(groups: Sequence[Mapping[str, Any]], purpose: str,
                 include_adhoc: bool = True) -> InlineKeyboardMarkup:
    """purpose is 'single' or 'session'; it rides along in the callback data."""
    rows = []
    for g in groups:
        n = _field(g, "n")
        rows.append([InlineKeyboardButton(
            f"{g['name']}  ({n})", callback_data=f"grp:{purpose}:{g['group_id']}")])
    if include_adhoc:
        rows.append([InlineKeyboardButton(
            "✏️ Pick channels…", callback_data=f"grp:{purpose}:adhoc")])
    rows.append([InlineKeyboardButton("✖️ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


def channel_picker(channels: Sequence[Mapping[str, Any]], selected: set[int],
                   purpose: str) -> InlineKeyboardMarkup:
    rows = []
    for ch in channels:
        mark = "✅ " if ch["chat_id"] in selected else "▫️ "
        rows.append([InlineKeyboardButton(
            f"{mark}{ch['title']}", callback_data=f"pick:{purpose}:{ch['chat_id']}")])
    rows.append([
        InlineKeyboardButton(f"Done ({len(selected)})", callback_data=f"picked:{purpose}"),
        InlineKeyboardButton("✖️ Cancel", callback_data="cancel"),
    ])
    return InlineKeyboardMarkup(rows)


def buttons_picker() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Both buttons", callback_data="btn:both")],
        [InlineKeyboardButton("Affiliate only", callback_data="btn:affiliate")],
        [InlineKeyboardButton("DM only", callback_data="btn:dm")],
        [InlineKeyboardButton("No buttons", callback_data="btn:none")],
        [InlineKeyboardButton("✖️ Cancel", callback_data="cancel")],
    ])


def confirm_send() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Send now", callback_data="send")],
        [InlineKeyboardButton("🕐 Schedule", callback_data="schedule")],
        [InlineKeyboardButton("✖️ Cancel", callback_data="cancel")],
    ])


def time_picker(options: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """options is [(label, raw)] from timeparse.quick_options()."""
    rows, pair = [], []
    for label, raw in options:
        pair.append(InlineKeyboardButton(label, callback_data=f"t:{raw}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([InlineKeyboardButton("✖️ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


def scheduled_list(posts) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"✖️ #{p['post_id']}",
                                  callback_data=f"cancelpost:{p['post_id']}")]
            for p in posts]
    return InlineKeyboardMarkup(rows) if rows else None


def plan_keyboard(target_label: str, buttons_mode: str,
                  pending: int) -> ReplyKeyboardMarkup:
    label = config.BUTTON_MODE_LABELS.get(buttons_mode, buttons_mode)
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(f"{KEY_BUTTONS_PREFIX}{label}")],
            [KeyboardButton(f"{KEY_SCHEDULED} ({pending})"),
             KeyboardButton(KEY_SWITCH_GROUP)],
            [KeyboardButton(KEY_END_PLANNING)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=f"🗓 PLANNING → {target_label}",
    )


def undo(broadcast_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("↩️ Undo", callback_data=f"undo:{broadcast_id}")]])


def session_keyboard(target_label: str, buttons_mode: str) -> ReplyKeyboardMarkup:
    """Pinned above the text input for the whole session, so the live target
    is never off-screen."""
    label = config.BUTTON_MODE_LABELS.get(buttons_mode, buttons_mode)
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(f"{KEY_BUTTONS_PREFIX}{label}")],
            [KeyboardButton(KEY_SWITCH_GROUP), KeyboardButton(KEY_END_SESSION)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=f"🔴 LIVE → {target_label}",
    )


def remove_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


def cycle_buttons_mode(current: str) -> str:
    modes = config.BUTTON_MODES
    return modes[(modes.index(current) + 1) % len(modes)] if current in modes else "both"
