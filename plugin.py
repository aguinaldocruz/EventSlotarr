import logging
import threading

LOGGER = logging.getLogger("plugins.eventslotarr")

_scheduler_thread = None
_stop_scheduler = threading.Event()

from .assignment import (
    assign_events_to_slots,
    clear_slots,
    get_configured_source_groups,
)
from .parser import load_events
from .preview import preview
from .scheduler import scheduler_loop


class Plugin:
    name = "EventSlotarr"
    version = "0.2.0"
    description = "Assign temporary live event streams to placeholder channels."

    def __init__(self):
        LOGGER.info("[EventSlotarr] Plugin initialized")

    def start(self, settings=None, context=None):
        """Dispatcharr lifecycle hook, if supported by the installed version.

        Some Dispatcharr versions only instantiate the plugin on startup and do
        not call a custom action. If they call start/on_start, this makes the
        scheduler begin automatically after the container starts.
        """
        settings = self._resolve_settings(settings or {}, context)
        if settings:
            self.ensure_scheduler_running(settings)
        else:
            LOGGER.warning(
                "[EventSlotarr] start() called without settings; scheduler will start on first plugin action"
            )
        return {"status": "success", "message": "EventSlotarr startup checked"}

    def on_start(self, settings=None, context=None):
        return self.start(settings=settings, context=context)

    def stop(self):
        self.stop_scheduler()
        return {"status": "success", "message": "EventSlotarr scheduler stopped"}

    def on_stop(self):
        return self.stop()

    @property
    def fields(self):
        return [
            {
                "id": "auto_discover_groups",
                "label": "Auto-discover source groups",
                "type": "boolean",
                "default": False,
            },
            {
                "id": "group_patterns",
                "label": "Auto-discovery group regex patterns",
                "type": "string",
                "default": ".*JOGOS.*,.*EVENT.*,.*PPV.*,.*UFC.*,.*BOX.*",
                "help_text": "Regex patterns separated by comma.",
            },
            {
                "id": "source_groups",
                "label": "Source Groups",
                "type": "string",
                "default": "Canais | Jogos do Dia",
                "help_text": "Source groups separated by comma.",
            },
            {
                "id": "auto_create_channels",
                "label": "Automatically create EventSlotarr channels",
                "type": "boolean",
                "default": False,
            },
            {
                "id": "placeholder_channels",
                "label": "Placeholder Channels",
                "type": "string",
                "default": "EventSlotarr 1,EventSlotarr 2,EventSlotarr 3,EventSlotarr 4",
                "help_text": "Placeholder channels separated by comma.",
            },
            {
                "id": "channel_prefix",
                "label": "Auto-created Channel Prefix",
                "type": "string",
                "default": "EventSlotarr",
            },
            {
                "id": "channel_count",
                "label": "Auto-created Channel Count",
                "type": "number",
                "default": 4,
            },
            {
                "id": "starting_channel_number",
                "label": "Starting Channel Number",
                "type": "number",
                "default": 9801,
            },
            {
                "id": "event_timezone",
                "label": "Event / EPG Timezone",
                "type": "string",
                "default": "local",
                "help_text": "Use 'local' to read the container/system TZ environment variable, or set an IANA timezone like America/Sao_Paulo.",
            },
            {
                "id": "event_duration_hours",
                "label": "Event Duration Hours",
                "type": "number",
                "default": 2,
            },
            {
                "id": "source_change_check_minutes",
                "label": "Look for source event changes every N minutes",
                "type": "number",
                "default": 30,
                "help_text": "Scheduler checks source groups for changes on this interval.",
            },
            {
                "id": "beginning_day_time",
                "label": "Beginning day time",
                "type": "string",
                "default": "00:00",
                "help_text": "Daily source-change checking starts at this local time. Use HH:MM, default midnight.",
            },
            {
                "id": "minutes_before_event",
                "label": "Minutes before event to load next stream",
                "type": "number",
                "default": 20,
            },
            {
                "id": "minutes_after_event",
                "label": "Minutes after event to keep previous stream valid",
                "type": "number",
                "default": 20,
            },
            {
                "id": "enable_xmltv",
                "label": "Enable Dynamic XMLTV",
                "type": "boolean",
                "default": True,
            },
            {
                "id": "xmltv_output",
                "label": "XMLTV Output Path",
                "type": "string",
                "default": "/data/eventslotarr.xml",
            },
        ]

    def _resolve_settings(self, settings, context=None):
        if settings:
            return settings
        if isinstance(context, dict):
            ctx_settings = context.get("settings")
            if ctx_settings:
                return ctx_settings
        return {}

    def run(self, action, settings=None, context=None):
        settings = self._resolve_settings(settings or {}, context)

        LOGGER.info(f"[EventSlotarr] Action: {action}")
        LOGGER.info(f"[EventSlotarr] Settings keys: {list(settings.keys())}")
        LOGGER.info(f"[EventSlotarr] source_groups={settings.get('source_groups')!r}")
        LOGGER.info(f"[EventSlotarr] auto_discover_groups={settings.get('auto_discover_groups')!r}")
        LOGGER.info(f"[EventSlotarr] event_timezone={settings.get('event_timezone')!r}")

        # Important: after Dispatcharr/container restart the old in-memory
        # scheduler thread is gone. Do not wait for the manual update_schedule
        # button; start it as soon as Dispatcharr calls any plugin action with
        # saved settings.
        if action not in ("preview",):
            self.ensure_scheduler_running(settings)

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

            return {"status": "error", "message": f"Unknown action '{action}'"}

        except Exception as ex:
            LOGGER.exception(f"[EventSlotarr] Error running action {action}: {ex}")
            return {"status": "error", "message": str(ex)}

    def validate_settings(self, settings):
        groups = get_configured_source_groups(settings)
        message = [
            f"Settings keys: {', '.join(settings.keys()) if settings else '(none)'}",
            f"source_groups: {settings.get('source_groups')!r}",
            f"auto_discover_groups: {settings.get('auto_discover_groups')!r}",
            f"event_timezone: {settings.get('event_timezone', 'local')!r}",
            f"source_change_check_minutes: {settings.get('source_change_check_minutes', 30)!r}",
            f"beginning_day_time: {settings.get('beginning_day_time', '00:00')!r}",
            f"minutes_before_event: {settings.get('minutes_before_event', 20)!r}",
            f"minutes_after_event: {settings.get('minutes_after_event', 20)!r}",
            f"{len(groups)} source groups resolved:",
        ]
        for group in groups:
            message.append(f"- {group}")
        return {"status": "success", "message": "\n".join(message)}

    def load_events_action(self, settings):
        total = 0
        lines = []
        for group_name in get_configured_source_groups(settings):
            events = load_events(group_name)
            total += len(events)
            lines.append(f"{group_name}: {len(events)} event(s)")
        return {"status": "success", "message": f"{total} events loaded\n" + "\n".join(lines)}

    def assign_events_action(self, settings):
        assignments = assign_events_to_slots(settings, force_rebuild=True)
        return {"status": "success", "message": f"{len(assignments)} channels assigned"}

    def preview_action(self):
        return {"status": "success", "message": preview()}

    def clear_slots_action(self, settings):
        clear_slots(settings)
        return {"status": "success", "message": "Slots cleared"}

    def update_schedule_action(self, settings):
        self.restart_scheduler(settings)
        return {"status": "success", "message": "Scheduler restarted"}

    def ensure_scheduler_running(self, settings):
        global _scheduler_thread

        if not settings:
            LOGGER.warning("[EventSlotarr] Cannot auto-start scheduler: no settings available")
            return False

        if _scheduler_thread and _scheduler_thread.is_alive():
            return True

        LOGGER.info("[EventSlotarr] Scheduler is not running; starting automatically")
        self.restart_scheduler(settings)
        return True

    def stop_scheduler(self):
        global _scheduler_thread

        if _scheduler_thread and _scheduler_thread.is_alive():
            LOGGER.info("[EventSlotarr] Stopping scheduler")
            _stop_scheduler.set()
            try:
                _scheduler_thread.join(timeout=5)
            except Exception:
                LOGGER.exception("[EventSlotarr] Error while stopping scheduler")

        _scheduler_thread = None
        _stop_scheduler.clear()

    def restart_scheduler(self, settings):
        global _scheduler_thread

        self.stop_scheduler()

        _stop_scheduler.clear()
        _scheduler_thread = threading.Thread(
            target=scheduler_loop,
            args=(dict(settings or {}), _stop_scheduler),
            daemon=True,
            name="EventSlotarrScheduler",
        )
        _scheduler_thread.start()
        LOGGER.info("[EventSlotarr] Scheduler thread started")
