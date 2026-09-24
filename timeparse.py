"""Parses the times Aysha will actually type, in the configured timezone.

Accepts:  9am · 9:30pm · 21:00 · tomorrow 9am · tmr 9:30 · 18/09 09:00
          18 sep 9am · sep 18 9am · +30m · +2h
Returns UTC, because that is what goes in the database.
"""
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config


class TimeParseError(Exception):
    pass


MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_REL = re.compile(r"^\+\s*(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours)?$")
_TIME = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$")
_DMY = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?$")
_D_MON = re.compile(r"^(\d{1,2})\s*([a-z]{3,4})$")
_MON_D = re.compile(r"^([a-z]{3,4})\s*(\d{1,2})$")


def tz() -> ZoneInfo:
    return ZoneInfo(config.TIMEZONE)


def local_now() -> datetime:
    return datetime.now(tz())


def fmt(dt_utc: datetime) -> str:
    """Render a stored UTC time back in local terms, for confirmations."""
    local = dt_utc.astimezone(tz())
    return local.strftime("%a %d %b, %H:%M ") + config.TIMEZONE_LABEL


def _parse_clock(token: str) -> tuple[int, int]:
    m = _TIME.match(token)
    if not m:
        raise TimeParseError(f"'{token}' isn't a time I understand.")
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    suffix = m.group(3)

    if suffix == "am":
        if hour == 12:
            hour = 0
        elif hour > 12:
            raise TimeParseError(f"'{token}' isn't a valid 12-hour time.")
    elif suffix == "pm":
        if hour != 12:
            hour += 12
        if hour > 23:
            raise TimeParseError(f"'{token}' isn't a valid 12-hour time.")
    # No suffix: a bare number is read as 24-hour.

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise TimeParseError(f"'{token}' isn't a valid time.")
    return hour, minute


def _parse_date(token: str, now: datetime) -> datetime | None:
    """Returns a local date (time zeroed) or None if the token isn't a date."""
    if token in ("today", "tdy"):
        return now
    if token in ("tomorrow", "tmr", "tom", "tmrw"):
        return now + timedelta(days=1)

    m = _DMY.match(token)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = m.group(3)
        if year:
            year = int(year)
            year += 2000 if year < 100 else 0
        else:
            year = now.year
        return _build_date(year, month, day, now)

    m = _D_MON.match(token)
    if m and m.group(2) in MONTHS:
        return _build_date(now.year, MONTHS[m.group(2)], int(m.group(1)), now)

    m = _MON_D.match(token)
    if m and m.group(1) in MONTHS:
        return _build_date(now.year, MONTHS[m.group(1)], int(m.group(2)), now)

    return None


def _build_date(year: int, month: int, day: int, now: datetime) -> datetime:
    try:
        d = now.replace(year=year, month=month, day=day,
                        hour=0, minute=0, second=0, microsecond=0)
    except ValueError:
        raise TimeParseError(f"{day}/{month}/{year} isn't a real date.")
    # A past date is treated as a mistake, not as next year. The useful
    # horizon here is days, so a typo is far likelier than a 2026 plan.
    return d


def parse(text: str, now: datetime | None = None) -> tuple[datetime, str | None]:
    """Returns (utc_datetime, note). `note` warns when a day was assumed."""
    now = now or local_now()
    s = " ".join((text or "").strip().lower().split())
    if not s:
        raise TimeParseError("Send me a time.")

    rel = _REL.match(s)
    if rel:
        n = int(rel.group(1))
        unit = rel.group(2) or "m"
        delta = timedelta(hours=n) if unit.startswith("h") else timedelta(minutes=n)
        if delta > timedelta(days=30):
            raise TimeParseError("That's more than 30 days out.")
        return (now + delta).astimezone(ZoneInfo("UTC")), None

    parts = s.split()
    note = None

    if len(parts) == 1:
        date_part = None
        clock = parts[0]
    elif len(parts) == 2:
        date_part = _parse_date(parts[0], now)
        if date_part is None:
            raise TimeParseError(f"I don't understand '{parts[0]}' as a date.")
        clock = parts[1]
    elif len(parts) == 3:
        # "18 sep 9am" / "sep 18 9am"
        date_part = _parse_date(f"{parts[0]} {parts[1]}", now)
        if date_part is None:
            raise TimeParseError(f"I don't understand '{parts[0]} {parts[1]}'.")
        clock = parts[2]
    else:
        raise TimeParseError("Too many words — try '9am' or 'tomorrow 9:30'.")

    hour, minute = _parse_clock(clock)

    if date_part is None:
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
            note = "that time has passed today, so this is tomorrow"
    else:
        target = date_part.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if target <= now:
        raise TimeParseError(f"{fmt(target.astimezone(ZoneInfo('UTC')))} is in the past.")
    if target - now > timedelta(days=60):
        raise TimeParseError("That's more than 60 days out.")

    return target.astimezone(ZoneInfo("UTC")), note


def quick_options(now: datetime | None = None) -> list[tuple[str, str]]:
    """(label, raw) pairs for the quick-pick buttons. Raw goes through parse()."""
    now = now or local_now()
    opts = [("In 15 min", "+15m"), ("In 1 hour", "+1h")]

    for hh, name in ((20, "8pm"), (22, "10pm")):
        if now.hour < hh:
            opts.append((f"Today {name}", f"{hh}:00"))

    opts += [("Tomorrow 9am", "tomorrow 9:00"),
             ("Tomorrow 12pm", "tomorrow 12:00"),
             ("Tomorrow 6pm", "tomorrow 18:00")]
    return opts
