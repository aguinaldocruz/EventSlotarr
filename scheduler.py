import logging

from .assignment import assign_events_to_slots
from .state import add_error

logger = logging.getLogger("EventSlotarr")


def scheduler_loop(get_params, stop_event):
    logger.info("[EventSlotarr] Scheduler started")

    while not stop_event.is_set():
        refresh_seconds = 60

        try:
            params = get_params() or {}

            try:
                refresh_minutes = int(params.get("refresh_minutes", 1))
            except Exception:
                refresh_minutes = 1

            refresh_seconds = max(refresh_minutes * 60, 30)

            logger.info(
                f"[EventSlotarr] Scheduler run. "
                f"refresh_minutes={refresh_minutes}, "
                f"event_timezone={params.get('event_timezone')!r}"
            )

            assign_events_to_slots(params)

        except Exception as ex:
            logger.exception(f"[EventSlotarr] Scheduler error: {ex}")
            add_error(str(ex))

        stop_event.wait(refresh_seconds)

    logger.info("[EventSlotarr] Scheduler stopped")

