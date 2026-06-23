from datetime import timedelta

from .timezone_utils import now_local, parse_today_time


def parse_event_time(params, event_time):
    try:
        return parse_today_time(params, event_time)
    except Exception:
        return now_local(params)


def filter_active_events(params, events):
    duration_hours = int(params.get("event_duration_hours", 2))
    start_offset_minutes = int(params.get("start_offset_minutes", -15))
    stop_offset_minutes = int(params.get("stop_offset_minutes", 30))

    now = now_local(params)

    active = []

    for event in events:
        start = parse_event_time(params, event["time"])

        active_start = start + timedelta(minutes=start_offset_minutes)

        active_stop = start + timedelta(
            hours=duration_hours,
            minutes=stop_offset_minutes
        )

        if active_start <= now <= active_stop:
            active.append(event)

    active.sort(key=lambda x: x["time"])

    return active

