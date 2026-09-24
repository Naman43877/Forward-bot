"""SQLite layer. Synchronous on purpose: at 15 channels the queries are
microseconds and a blocking call is simpler than an async driver."""
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import config

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    name    TEXT,
    role    TEXT NOT NULL DEFAULT 'operator'
);

CREATE TABLE IF NOT EXISTS channels (
    chat_id        INTEGER PRIMARY KEY,
    title          TEXT NOT NULL,
    lang           TEXT,
    affiliate_url  TEXT,
    affiliate_text TEXT NOT NULL DEFAULT 'Join Now',
    dm_url         TEXT,
    dm_text        TEXT NOT NULL DEFAULT 'Chat with us',
    active         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS groups (
    group_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS channel_groups (
    group_id INTEGER NOT NULL REFERENCES groups(group_id)  ON DELETE CASCADE,
    chat_id  INTEGER NOT NULL REFERENCES channels(chat_id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, chat_id)
);

CREATE TABLE IF NOT EXISTS broadcasts (
    broadcast_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    mode           TEXT NOT NULL,
    buttons_mode   TEXT NOT NULL,
    group_id       INTEGER,
    src_chat_id    INTEGER NOT NULL,
    src_message_id INTEGER NOT NULL,
    created_at     TEXT NOT NULL,
    undone_at      TEXT
);

CREATE TABLE IF NOT EXISTS broadcast_targets (
    broadcast_id INTEGER NOT NULL REFERENCES broadcasts(broadcast_id) ON DELETE CASCADE,
    chat_id      INTEGER NOT NULL,
    message_id   INTEGER,
    status       TEXT NOT NULL,
    error        TEXT,
    PRIMARY KEY (broadcast_id, chat_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    user_id        INTEGER PRIMARY KEY,
    group_id       INTEGER,
    ad_hoc_chats   TEXT,
    buttons_mode   TEXT NOT NULL DEFAULT 'both',
    last_active_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scheduled_posts (
    post_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    group_id       INTEGER,
    ad_hoc_chats   TEXT,
    buttons_mode   TEXT NOT NULL,
    src_chat_id    INTEGER NOT NULL,
    src_message_id INTEGER NOT NULL,
    preview        TEXT,
    send_at        TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    created_at     TEXT NOT NULL,
    broadcast_id   INTEGER,
    error          TEXT
);

CREATE INDEX IF NOT EXISTS idx_sched_due ON scheduled_posts(status, send_at);
CREATE INDEX IF NOT EXISTS idx_targets_broadcast ON broadcast_targets(broadcast_id);
CREATE INDEX IF NOT EXISTS idx_broadcasts_user   ON broadcasts(user_id, created_at);
"""

_conn: sqlite3.Connection | None = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def connect(path: str | None = None) -> sqlite3.Connection:
    global _conn
    path = path or config.DB_PATH
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    _conn = sqlite3.connect(path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.executescript(SCHEMA)
    _conn.commit()
    _migrate(_conn)
    return _conn


def _migrate(c: sqlite3.Connection) -> None:
    """CREATE TABLE IF NOT EXISTS never alters an existing table, so new
    columns on old tables have to be added explicitly. Safe to re-run."""
    wanted = [
        ("sessions", "kind", "TEXT NOT NULL DEFAULT 'live'"),
    ]
    for table, column, decl in wanted:
        cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    c.commit()


def conn() -> sqlite3.Connection:
    if _conn is None:
        return connect()
    return _conn


# ---------------------------------------------------------------- users

def seed_admins(admin_ids: list[int]) -> None:
    c = conn()
    for uid in admin_ids:
        c.execute(
            "INSERT INTO users (user_id, name, role) VALUES (?, 'admin', 'admin') "
            "ON CONFLICT(user_id) DO UPDATE SET role='admin'",
            (uid,),
        )
    c.commit()


def get_user(user_id: int) -> sqlite3.Row | None:
    return conn().execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def add_user(user_id: int, name: str, role: str = "operator") -> None:
    c = conn()
    c.execute(
        "INSERT INTO users (user_id, name, role) VALUES (?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET name=excluded.name, role=excluded.role",
        (user_id, name, role),
    )
    c.commit()


def remove_user(user_id: int) -> None:
    c = conn()
    c.execute("DELETE FROM users WHERE user_id=?", (user_id,))
    c.commit()


def list_users() -> list[sqlite3.Row]:
    return conn().execute("SELECT * FROM users ORDER BY role, user_id").fetchall()


# ------------------------------------------------------------- channels

def upsert_channel(chat_id: int, title: str, lang: str | None = None,
                   active: int = 1) -> None:
    c = conn()
    c.execute(
        "INSERT INTO channels (chat_id, title, lang, active) VALUES (?,?,?,?) "
        "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, "
        "lang=COALESCE(excluded.lang, channels.lang)",
        (chat_id, title, lang, active),
    )
    c.commit()


def set_affiliate(chat_id: int, url: str, text: str | None = None) -> bool:
    c = conn()
    if text:
        cur = c.execute(
            "UPDATE channels SET affiliate_url=?, affiliate_text=? WHERE chat_id=?",
            (url, text, chat_id))
    else:
        cur = c.execute(
            "UPDATE channels SET affiliate_url=? WHERE chat_id=?", (url, chat_id))
    c.commit()
    return cur.rowcount > 0


def set_dm(chat_id: int, url: str, text: str | None = None) -> bool:
    c = conn()
    if text:
        cur = c.execute(
            "UPDATE channels SET dm_url=?, dm_text=? WHERE chat_id=?",
            (url, text, chat_id))
    else:
        cur = c.execute("UPDATE channels SET dm_url=? WHERE chat_id=?", (url, chat_id))
    c.commit()
    return cur.rowcount > 0


def set_channel_active(chat_id: int, active: bool) -> bool:
    c = conn()
    cur = c.execute("UPDATE channels SET active=? WHERE chat_id=?",
                    (1 if active else 0, chat_id))
    c.commit()
    return cur.rowcount > 0


def get_channel(chat_id: int) -> sqlite3.Row | None:
    return conn().execute("SELECT * FROM channels WHERE chat_id=?", (chat_id,)).fetchone()


def list_channels(only_active: bool = False) -> list[sqlite3.Row]:
    q = "SELECT * FROM channels"
    if only_active:
        q += " WHERE active=1"
    q += " ORDER BY title COLLATE NOCASE"
    return conn().execute(q).fetchall()


def delete_channel(chat_id: int) -> bool:
    c = conn()
    cur = c.execute("DELETE FROM channels WHERE chat_id=?", (chat_id,))
    c.commit()
    return cur.rowcount > 0


# --------------------------------------------------------------- groups

def add_group(name: str, sort_order: int = 0) -> int | None:
    """Returns None if a group by that name already exists. The check is
    case-insensitive on purpose: the UNIQUE constraint is not, and lookups
    are, so without this you can end up with two groups only one of which
    is ever reachable."""
    if get_group_by_name(name) is not None:
        return None
    c = conn()
    try:
        cur = c.execute("INSERT INTO groups (name, sort_order) VALUES (?,?)",
                        (name, sort_order))
        c.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None


def similar_groups(name: str, threshold: float = 0.7) -> list[str]:
    """Existing names close enough to `name` to be worth a second look."""
    import difflib
    target = name.strip().lower()
    out = []
    for g in list_groups():
        other = g["name"].lower()
        if other == target:
            continue
        ratio = difflib.SequenceMatcher(None, target, other).ratio()
        if ratio >= threshold or target in other or other in target:
            out.append(g["name"])
    return out


def get_group_by_name(name: str) -> sqlite3.Row | None:
    return conn().execute(
        "SELECT * FROM groups WHERE name=? COLLATE NOCASE", (name,)).fetchone()


def get_group(group_id: int) -> sqlite3.Row | None:
    return conn().execute("SELECT * FROM groups WHERE group_id=?", (group_id,)).fetchone()


def list_groups() -> list[sqlite3.Row]:
    return conn().execute(
        "SELECT g.*, COUNT(c.chat_id) AS n FROM groups g "
        "LEFT JOIN channel_groups cg ON cg.group_id=g.group_id "
        "LEFT JOIN channels c ON c.chat_id=cg.chat_id AND c.active=1 "
        "GROUP BY g.group_id ORDER BY g.sort_order, g.name COLLATE NOCASE"
    ).fetchall()


def delete_group(group_id: int) -> bool:
    c = conn()
    cur = c.execute("DELETE FROM groups WHERE group_id=?", (group_id,))
    c.commit()
    return cur.rowcount > 0


def assign(group_id: int, chat_ids: list[int]) -> int:
    c = conn()
    n = 0
    for cid in chat_ids:
        try:
            c.execute("INSERT OR IGNORE INTO channel_groups (group_id, chat_id) "
                      "VALUES (?,?)", (group_id, cid))
            n += 1
        except sqlite3.IntegrityError:
            pass
    c.commit()
    return n


def unassign(group_id: int, chat_ids: list[int]) -> int:
    c = conn()
    n = 0
    for cid in chat_ids:
        cur = c.execute("DELETE FROM channel_groups WHERE group_id=? AND chat_id=?",
                        (group_id, cid))
        n += cur.rowcount
    c.commit()
    return n


def channels_in_group(group_id: int, only_active: bool = True) -> list[sqlite3.Row]:
    q = ("SELECT c.* FROM channels c JOIN channel_groups cg ON cg.chat_id=c.chat_id "
         "WHERE cg.group_id=?")
    if only_active:
        q += " AND c.active=1"
    q += " ORDER BY c.title COLLATE NOCASE"
    return conn().execute(q, (group_id,)).fetchall()


def groups_for_channel(chat_id: int) -> list[sqlite3.Row]:
    return conn().execute(
        "SELECT g.* FROM groups g JOIN channel_groups cg ON cg.group_id=g.group_id "
        "WHERE cg.chat_id=? ORDER BY g.sort_order", (chat_id,)).fetchall()


def channels_by_ids(chat_ids: list[int], only_active: bool = True) -> list[sqlite3.Row]:
    if not chat_ids:
        return []
    marks = ",".join("?" * len(chat_ids))
    q = f"SELECT * FROM channels WHERE chat_id IN ({marks})"
    if only_active:
        q += " AND active=1"
    q += " ORDER BY title COLLATE NOCASE"
    return conn().execute(q, chat_ids).fetchall()


# ----------------------------------------------------------- broadcasts

def create_broadcast(user_id: int, mode: str, buttons_mode: str,
                     group_id: int | None, src_chat_id: int,
                     src_message_id: int) -> int:
    c = conn()
    cur = c.execute(
        "INSERT INTO broadcasts (user_id, mode, buttons_mode, group_id, "
        "src_chat_id, src_message_id, created_at) VALUES (?,?,?,?,?,?,?)",
        (user_id, mode, buttons_mode, group_id, src_chat_id, src_message_id, now()),
    )
    c.commit()
    return cur.lastrowid


def record_target(broadcast_id: int, chat_id: int, message_id: int | None,
                  status: str, error: str | None = None) -> None:
    c = conn()
    c.execute(
        "INSERT INTO broadcast_targets (broadcast_id, chat_id, message_id, status, error) "
        "VALUES (?,?,?,?,?) ON CONFLICT(broadcast_id, chat_id) DO UPDATE SET "
        "message_id=excluded.message_id, status=excluded.status, error=excluded.error",
        (broadcast_id, chat_id, message_id, status, error),
    )
    c.commit()


def get_broadcast(broadcast_id: int) -> sqlite3.Row | None:
    return conn().execute("SELECT * FROM broadcasts WHERE broadcast_id=?",
                          (broadcast_id,)).fetchone()


def sent_targets(broadcast_id: int) -> list[sqlite3.Row]:
    return conn().execute(
        "SELECT * FROM broadcast_targets WHERE broadcast_id=? AND status='sent'",
        (broadcast_id,)).fetchall()


def mark_target_deleted(broadcast_id: int, chat_id: int) -> None:
    c = conn()
    c.execute("UPDATE broadcast_targets SET status='deleted' "
              "WHERE broadcast_id=? AND chat_id=?", (broadcast_id, chat_id))
    c.commit()


def mark_undone(broadcast_id: int) -> None:
    c = conn()
    c.execute("UPDATE broadcasts SET undone_at=? WHERE broadcast_id=?",
              (now(), broadcast_id))
    c.commit()


def undo_expired(broadcast_id: int) -> bool:
    b = get_broadcast(broadcast_id)
    if not b:
        return True
    age = datetime.now(timezone.utc) - _parse(b["created_at"])
    return age > timedelta(minutes=config.UNDO_WINDOW_MINUTES)


def recent_broadcasts(limit: int = 10) -> list[sqlite3.Row]:
    return conn().execute(
        "SELECT b.*, g.name AS group_name, "
        "(SELECT COUNT(*) FROM broadcast_targets t WHERE t.broadcast_id=b.broadcast_id "
        " AND t.status='sent') AS n_sent "
        "FROM broadcasts b LEFT JOIN groups g ON g.group_id=b.group_id "
        "ORDER BY b.broadcast_id DESC LIMIT ?", (limit,)).fetchall()


# ------------------------------------------------------------- sessions

def start_session(user_id: int, group_id: int | None,
                  ad_hoc_chats: list[int] | None = None,
                  buttons_mode: str = "both", kind: str = "live") -> None:
    c = conn()
    c.execute(
        "INSERT INTO sessions (user_id, group_id, ad_hoc_chats, buttons_mode, "
        "last_active_at, kind) VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
        "group_id=excluded.group_id, ad_hoc_chats=excluded.ad_hoc_chats, "
        "buttons_mode=excluded.buttons_mode, last_active_at=excluded.last_active_at, "
        "kind=excluded.kind",
        (user_id, group_id, json.dumps(ad_hoc_chats or []), buttons_mode,
         now(), kind),
    )
    c.commit()


def get_session(user_id: int) -> dict[str, Any] | None:
    row = conn().execute("SELECT * FROM sessions WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["ad_hoc_chats"] = json.loads(d["ad_hoc_chats"] or "[]")
    return d


def touch_session(user_id: int) -> None:
    c = conn()
    c.execute("UPDATE sessions SET last_active_at=? WHERE user_id=?", (now(), user_id))
    c.commit()


def set_session_buttons(user_id: int, buttons_mode: str) -> None:
    c = conn()
    c.execute("UPDATE sessions SET buttons_mode=?, last_active_at=? WHERE user_id=?",
              (buttons_mode, now(), user_id))
    c.commit()


def end_session(user_id: int) -> None:
    c = conn()
    c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    c.commit()


def session_is_idle(session: dict[str, Any]) -> bool:
    age = datetime.now(timezone.utc) - _parse(session["last_active_at"])
    return age > timedelta(minutes=config.SESSION_IDLE_MINUTES)


# ------------------------------------------------------ scheduled posts

def schedule_post(user_id: int, group_id: int | None, ad_hoc_chats: list[int] | None,
                  buttons_mode: str, src_chat_id: int, src_message_id: int,
                  preview: str, send_at_utc: str) -> int:
    c = conn()
    cur = c.execute(
        "INSERT INTO scheduled_posts (user_id, group_id, ad_hoc_chats, buttons_mode, "
        "src_chat_id, src_message_id, preview, send_at, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (user_id, group_id, json.dumps(ad_hoc_chats or []), buttons_mode,
         src_chat_id, src_message_id, preview, send_at_utc, now()),
    )
    c.commit()
    return cur.lastrowid


def get_post(post_id: int) -> dict[str, Any] | None:
    row = conn().execute(
        "SELECT * FROM scheduled_posts WHERE post_id=?", (post_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["ad_hoc_chats"] = json.loads(d["ad_hoc_chats"] or "[]")
    return d


def pending_posts(user_id: int | None = None) -> list[dict[str, Any]]:
    q = "SELECT * FROM scheduled_posts WHERE status='pending'"
    args: list[Any] = []
    if user_id is not None:
        q += " AND user_id=?"
        args.append(user_id)
    q += " ORDER BY send_at"
    out = []
    for row in conn().execute(q, args):
        d = dict(row)
        d["ad_hoc_chats"] = json.loads(d["ad_hoc_chats"] or "[]")
        out.append(d)
    return out


def claim_due_posts() -> list[dict[str, Any]]:
    """Atomically take ownership of everything due, so a slow send can never
    be picked up twice by overlapping ticks."""
    c = conn()
    rows = c.execute(
        "SELECT post_id FROM scheduled_posts WHERE status='pending' AND send_at<=?",
        (now(),)).fetchall()
    claimed = []
    for r in rows:
        cur = c.execute(
            "UPDATE scheduled_posts SET status='sending' "
            "WHERE post_id=? AND status='pending'", (r["post_id"],))
        if cur.rowcount:
            claimed.append(r["post_id"])
    c.commit()
    return [get_post(pid) for pid in claimed]


def finish_post(post_id: int, status: str, broadcast_id: int | None = None,
                error: str | None = None) -> None:
    c = conn()
    c.execute("UPDATE scheduled_posts SET status=?, broadcast_id=?, error=? "
              "WHERE post_id=?", (status, broadcast_id, error, post_id))
    c.commit()


def requeue_post(post_id: int, error: str | None = None) -> None:
    """Back to pending after a send that reached no channel at all."""
    c = conn()
    c.execute("UPDATE scheduled_posts SET status='pending', error=? "
              "WHERE post_id=? AND status='sending'", (error, post_id))
    c.commit()


def cancel_post(post_id: int, user_id: int, is_admin: bool = False) -> str:
    """Returns 'ok', 'not_found', 'not_yours', or the current status."""
    p = get_post(post_id)
    if not p:
        return "not_found"
    if p["user_id"] != user_id and not is_admin:
        return "not_yours"
    if p["status"] != "pending":
        return p["status"]
    c = conn()
    c.execute("UPDATE scheduled_posts SET status='cancelled' WHERE post_id=?",
              (post_id,))
    c.commit()
    return "ok"


def reap_stuck_sending() -> int:
    """A crash mid-send leaves rows in 'sending'. Called once at startup so
    they surface as failures rather than sitting invisible forever."""
    c = conn()
    cur = c.execute(
        "UPDATE scheduled_posts SET status='failed', "
        "error='bot restarted while sending' WHERE status='sending'")
    c.commit()
    return cur.rowcount
