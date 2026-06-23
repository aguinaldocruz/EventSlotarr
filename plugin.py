import logging
import threading

LOGGER = logging.getLogger("plugins.eventslotarr")

_scheduler_thread = None
_stop_scheduler = threading.Event()
_last_settings = {}

from .assignment import (
    assign_events_to_slots,
    clear_slots,
    get_configured_source_groups
)
from .parser import load_events
from .preview import preview
from .scheduler import scheduler_loop


class Plugin:
    name = "EventSlotarr"
    version = "0.1.0"
    description = "Assign temporary live event streams to placeholder channels."

    def __init__(self):
        LOGGER.info("[EventSlotarr] Plugin initialized")

    @property
    def fields(self):
        return [
            {
                "id": "auto_discover_groups",
                "label": "Auto-discover source groups",
                "type": "boolean",
                "default": False
            },
            {
                "id": "group_patterns",
                "label": "Auto-discovery group regex patterns",
                "type": "string",
                "default": ".*JOGOS.*,.*EVENT.*,.*PPV.*,.*UFC.*,.*BOX.*",
                "help_text": "Regex patterns separated by comma."
            },
            {
                "id": "source_groups",
                "label": "Source Groups",
                "type": "string",
                "default": "Canais | Jogos do Dia",
                "help_text": "Source groups separated by comma."
            },
            {
                "id": "auto_create_channels",
                "label": "Automatically create EventSlotarr channels",
                "type": "boolean",
                "default": False
            },
            {
                "id": "placeholder_channels",
                "label": "Placeholder Channels",
                "type": "string",
                "default": "EventSlotarr 1,EventSlotarr 2,EventSlotarr 3,EventSlotarr 4",
                "help_text": "Placeholder channels separated by comma."
            },
            {
                "id": "channel_prefix",
                "label": "Auto-created Channel Prefix",
                "type": "string",
                "default": "EventSlotarr"
            },
            {
                "id": "channel_count",
                "label": "Auto-created Channel Count",
                "type": "number",
                "default": 4
            },
            {
                "id": "starting_channel_number",
                "label": "Starting Channel Number",
                "type": "number",
                "default": 9801
            },
            {
                "id": "event_timezone",
                "label": "Event / EPG Timezone",
                "type": "string",
                "default": "America/Sao_Paulo",
                "help_text": "Use an IANA timezone like America/Sao_Paulo, or 'local' to read the container TZ environment variable."
            },
            {
                "id": "event_duration_hours",
                "label": "Event Duration Hours",
                "type": "number",
                "default": 2
            },
            {
                "id": "start_offset_minutes",
                "label": "Start Offset Minutes",
                "type": "number",
                "default": -15
            },
            {
                "id": "stop_offset_minutes",
                "label": "Stop Offset Minutes",
                "type": "number",
                "default": 30
            },
            {
                "id": "refresh_minutes",
                "label": "Refresh Interval Minutes",
                "type": "number",
                "default": 1
            },
            {
                "id": "enable_xmltv",
                "label": "Enable Dynamic XMLTV",
                "type": "boolean",
                "default": True
            },
            {
                "id": "xmltv_output",
                "label": "XMLTV Output Path",
                "type": "string",
                "default": "/data/eventslotarr.xml"
            }
        ]

    def _resolve_settings(self, settings, context=None):
        global _last_settings

        if settings:
            _last_settings = dict(settings)
            return _last_settings

        if isinstance(context, dict):
            ctx_settings = context.get("settings")
            if ctx_settings:
                _last_settings = dict(ctx_settings)
                return _last_settings

        return dict(_last_settings)

    def run(self, action, settings=None, context=None):
        settings = self._resolve_settings(settings or {}, context)

        LOGGER.info(f"[EventSlotarr] Action: {action}")
        LOGGER.info(f"[EventSlotarr] Settings keys: {list(settings.keys())}")
        LOGGER.info(f"[EventSlotarr] source_groups={settings.get('source_groups')!r}")
        LOGGER.info(f"[EventSlotarr] auto_discover_groups={settings.get('auto_discover_groups')!r}")
        LOGGER.info(f"[EventSlotarr] event_timezone={settings.get('event_timezone')!r}")

        try:
            if action == "validate_settings":
                return self.validate_settings(settings)

            if action == "load_events":
                return self.load_events_action(settings)

            if action == "assign_events":
                return self.assign_events_action(settings)

            if action == "preview":
                return self.preview_action()

            if action == "clear_slots":
                return self.clear_slots_action(settings)

            if action == "update_schedule":
                return self.update_schedule_action(settings)

            return {
                "status": "error",
                "message": f"Unknown action '{action}'"
            }

        except Exception as ex:
            LOGGER.exception(f"[EventSlotarr] Error running action {action}: {ex}")
            return {
                "status": "error",
                "message": str(ex)
            }

    def validate_settings(self, settings):
        groups = get_configured_source_groups(settings)

        message = [
            f"Settings keys: {', '.join(settings.keys()) if settings else '(none)'}",
            f"source_groups: {settings.get('source_groups')!r}",
            f"auto_discover_groups: {settings.get('auto_discover_groups')!r}",
            f"event_timezone: {settings.get('event_timezone', 'America/Sao_Paulo')!r}",
            f"refresh_minutes: {settings.get('refresh_minutes', 1)!r}",
            f"{len(groups)} source groups resolved:"
        ]

        for group in groups:
            message.append(f"- {group}")

        return {
            "status": "success",
            "message": "\n".join(message)
        }

    def load_events_action(self, settings):
        total = 0
        lines = []

        for group_name in get_configured_source_groups(settings):
            events = load_events(group_name)
            total += len(events)
            lines.append(f"{group_name}: {len(events)} event(s)")

        return {
            "status": "success",
            "message": f"{total} events loaded\n" + "\n".join(lines)
        }

    def assign_events_action(self, settings):
        assignments = assign_events_to_slots(settings)

        return {
            "status": "success",
            "message": f"{len(assignments)} channels assigned"
        }

    def preview_action(self):
        return {
            "status": "success",
            "message": preview()
        }

    def clear_slots_action(self, settings):
        clear_slots(settings)

        return {
            "status": "success",
            "message": "Slots cleared"
        }

    def update_schedule_action(self, settings):
        self.restart_scheduler(settings)

        return {
            "status": "success",
            "message": "Scheduler restarted"
        }

    def restart_scheduler(self, settings):
        global _scheduler_thread
        global _last_settings

        if settings:
            _last_settings = dict(settings)

        if _scheduler_thread and _scheduler_thread.is_alive():
            _stop_scheduler.set()

            try:
                _scheduler_thread.join(timeout=5)
            except Exception:
                pass

        _stop_scheduler.clear()

        _scheduler_thread = threading.Thread(
            target=scheduler_loop,
            args=(lambda: dict(_last_settings), _stop_scheduler),
            daemon=True
        )

        _scheduler_thread.start()

