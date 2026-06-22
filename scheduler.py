import logging

from .assignment import assign_events_to_slots
from .state import add_error

logger = logging.getLogger("EventSlotarr")


def scheduler_loop(params, stop_event):
    try:
        refresh_minutes = int(params.get("refresh_minutes", 1))
    except Exception:
        refresh_minutes = 1

    refresh_seconds = max(refresh_minutes * 60, 30)

    logger.info(f"Scheduler started with {refresh_seconds}s interval")

    while not stop_event.is_set():
        try:
            assign_events_to_slots(params)
        except Exception as ex:
            logger.exception(f"Scheduler error: {ex}")
            add_error(str(ex))

        stop_event.wait(refresh_seconds)

    logger.info("Scheduler stopped")
