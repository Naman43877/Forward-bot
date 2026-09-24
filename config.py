"""Configuration loaded from environment variables."""
import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Comma-separated Telegram user IDs that get admin rights (channel/group/link
# management). The first run seeds these into the users table.
_raw_admins = os.getenv("ADMIN_IDS", "").replace(" ", "")
ADMIN_IDS = [int(x) for x in _raw_admins.split(",") if x]

DB_PATH = os.getenv("DB_PATH", "data/broadcast.db")

# How long a live session survives with no messages, in minutes.
SESSION_IDLE_MINUTES = int(os.getenv("SESSION_IDLE_MINUTES", "15"))

# How long the Undo button stays valid, in minutes.
UNDO_WINDOW_MINUTES = int(os.getenv("UNDO_WINDOW_MINUTES", "10"))

# Delay between sends, in seconds. Telegram allows ~30 msg/sec overall;
# 0.06 keeps us comfortably under while staying fast for 15 channels.
SEND_DELAY_SECONDS = float(os.getenv("SEND_DELAY_SECONDS", "0.06"))

# Bots cannot read a user's Telegram timezone, so it is set here.
TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")
TIMEZONE_LABEL = os.getenv("TIMEZONE_LABEL", "IST")

# How often the scheduler checks for due posts, in seconds.
SCHEDULER_TICK_SECONDS = int(os.getenv("SCHEDULER_TICK_SECONDS", "30"))

# If the bot was down, send a late post only if it is less than this many
# minutes late. Beyond that it is marked missed and the author is told.
SCHEDULE_GRACE_MINUTES = int(os.getenv("SCHEDULE_GRACE_MINUTES", "30"))

BUTTON_MODES = ("both", "affiliate", "dm", "none")
BUTTON_MODE_LABELS = {
    "both": "Both buttons",
    "affiliate": "Affiliate only",
    "dm": "DM only",
    "none": "No buttons",
}


def validate() -> None:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")
    if not ADMIN_IDS:
        raise SystemExit("ADMIN_IDS is not set. Put your own Telegram user ID there.")
