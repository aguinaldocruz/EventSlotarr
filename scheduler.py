import logging
from datetime import timedelta

from .assignment import assign_events_to_slots, seconds_until_next_slot_change
from .state import add_error
from .timezone_utils import now_local

logger = logging.getLogger("EventSlotarr")


def _int_setting(params, key, default):
    try:
        return int(params.get(key, default))
    except Exception:
        return default


def _parse_hhmm(value, default="00:00"):
    try:
        hour, minute = str(value or default).strip().split(":", 1)
        hour = max(0, min(23, int(hour)))
        minute = max(0, min(59, int(minute)))
        return hour, minute
    except Exception:
        return 0, 0


def _seconds_until_beginning_day(params):
    now = now_local(params)
    hour, minute = _parse_hhmm(params.get("beginning_day_time"), "00:00")
    start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now >= start:
        return 0
    return max(0, int((start - now).total_seconds()))


def scheduler_loop(params, stop_event):
    check_minutes = _int_setting(params, "source_change_check_minutes", 30)
    check_seconds = max(check_minutes * 60, 60)

    logger.info(
        "Scheduler started. source_check=%ss beginning_day=%r before=%r after=%r",
        check_seconds,
        params.get("beginning_day_time", "00:00"),
        params.get("minutes_before_event", 20),
        params.get("minutes_after_event", 20),
    )

    wait_start = _seconds_until_beginning_day(params)
    if wait_start > 0:
        logger.info("Waiting %ss until beginning day", wait_start)
        if stop_event.wait(wait_start):
            logger.info("Scheduler stopped before beginning day")
            return

    # First pass after beginning day/midnight. This only rebuilds XMLTV if the
    # source signature changed, but it also loads streams that are already due.
    next_source_check = now_local(params)

    while not stop_event.is_set():
        try:
            now = now_local(params)
            check_source = now >= next_source_check

            assign_events_to_slots(params, force_rebuild=False, check_source=check_source)

            if check_source:
                next_source_check = now + timedelta(seconds=check_seconds)

            seconds_to_change = seconds_until_next_slot_change(params)
            if seconds_to_change is None:
                sleep_seconds = check_seconds
            else:
                sleep_seconds = min(check_seconds, max(30, seconds_to_change))

        except Exception as ex:
            logger.exception(f"Scheduler error: {ex}")
            add_error(str(ex))
            sleep_seconds = min(check_seconds, 300)

        stop_event.wait(sleep_seconds)

    logger.info("Scheduler stopped")
