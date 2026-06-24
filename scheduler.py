import logging
from datetime import timedelta

from .assignment import assign_events_to_slots, seconds_until_next_slot_change
from .state import add_error
from .timezone_utils import now_local

logger = logging.getLogger("plugins.eventslotarr")


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

    # If the configured beginning day already passed, start immediately.
    # Example: beginning_day_time=00:00 and container starts 15:23.
    if now >= start:
        return 0

    return max(0, int((start - now).total_seconds()))


def scheduler_loop(params, stop_event):
    check_minutes = _int_setting(params, "source_change_check_minutes", 30)
    check_seconds = max(check_minutes * 60, 60)

    logger.info(
        "[EventSlotarr] Scheduler started. source_check=%ss beginning_day=%r before=%r after=%r",
        check_seconds,
        params.get("beginning_day_time", "00:00"),
        params.get("minutes_before_event", 20),
        params.get("minutes_after_event", 20),
    )

    wait_start = _seconds_until_beginning_day(params)
    if wait_start > 0:
        logger.info("[EventSlotarr] Waiting %ss until beginning day", wait_start)
        if stop_event.wait(wait_start):
            logger.info("[EventSlotarr] Scheduler stopped before beginning day")
            return

    # Run immediately on startup. This is critical because if Dispatcharr starts
    # at 15:23 and an event starts 16:00 with minutes_before_event=20, the slot
    # must be replaced at 15:40 without waiting for a manual plugin action.
    next_source_check = now_local(params)

    while not stop_event.is_set():
        try:
            now = now_local(params)
            check_source = now >= next_source_check

            logger.info(
                "[EventSlotarr] Scheduler tick. now=%s check_source=%s next_source_check=%s",
                now,
                check_source,
                next_source_check,
            )

            assignments = assign_events_to_slots(
                params,
                force_rebuild=False,
                check_source=check_source,
            )

            if check_source:
                next_source_check = now + timedelta(seconds=check_seconds)
                logger.info("[EventSlotarr] Next source check at %s", next_source_check)

            seconds_to_change = seconds_until_next_slot_change(params)

            if seconds_to_change is None:
                sleep_seconds = check_seconds
                logger.info(
                    "[EventSlotarr] No future slot switch found; sleeping until next source check in %ss",
                    sleep_seconds,
                )
            else:
                sleep_seconds = min(check_seconds, max(10, seconds_to_change))
                logger.info(
                    "[EventSlotarr] Due assignments=%s; next slot/source wake in %ss",
                    len(assignments),
                    sleep_seconds,
                )

        except Exception as ex:
            logger.exception("[EventSlotarr] Scheduler error: %s", ex)
            add_error(str(ex))
            sleep_seconds = min(check_seconds, 300)

        stop_event.wait(sleep_seconds)

    logger.info("[EventSlotarr] Scheduler stopped")
