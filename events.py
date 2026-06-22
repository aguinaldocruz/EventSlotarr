from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

PLUGIN_TZ = ZoneInfo("America/Sao_Paulo")


def now_local():
    return datetime.now(PLUGIN_TZ).replace(tzinfo=None)


def parse_event_time(event_time):
    now = now_local()

    try:
        hour, minute = event_time.split(":")
        return now.replace(
            hour=int(hour),
            minute=int(minute),
            second=0,
            microsecond=0
        )
    except Exception:
        return now


def filter_active_events(params, events):
    duration_hours = int(params.get("event_duration_hours", 2))
    start_offset_minutes = int(params.get("start_offset_minutes", -15))
    stop_offset_minutes = int(params.get("stop_offset_minutes", 30))

    now = now_local()
    active = []

    for event in events:
        start = parse_event_time(event["time"])

        active_start = start + timedelta(minutes=start_offset_minutes)
        active_stop = start + timedelta(
            hours=duration_hours,
            minutes=stop_offset_minutes
        )

        if active_start <= now <= active_stop:
            active.append(event)

    active.sort(key=lambda x: x["time"])
    return active

