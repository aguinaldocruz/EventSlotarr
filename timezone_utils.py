import os
from datetime import datetime
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "America/Sao_Paulo"


def get_timezone(params=None):
    params = params or {}

    timezone_name = str(
        params.get("event_timezone")
        or DEFAULT_TIMEZONE
    ).strip()

    if not timezone_name or timezone_name.lower() in ("local", "system", "env"):
        timezone_name = os.environ.get("TZ") or DEFAULT_TIMEZONE

    if timezone_name.upper() == "UTC":
        timezone_name = DEFAULT_TIMEZONE

    try:
        return ZoneInfo(timezone_name)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


def now_local(params=None):
    return datetime.now(get_timezone(params))


def parse_today_time(params, event_time):
    now = now_local(params)
    hour, minute = str(event_time).strip().split(":")

    return now.replace(
        hour=int(hour),
        minute=int(minute),
        second=0,
        microsecond=0
    )


def ensure_local(dt, params=None):
    tz = get_timezone(params)

    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)

    return dt.astimezone(tz)


def day_bounds(params=None):
    now = now_local(params)

    return (
        now.replace(hour=0, minute=0, second=0, microsecond=0),
        now.replace(hour=23, minute=59, second=59, microsecond=0)
    )

