import logging
import threading
from datetime import datetime

LOGGER = logging.getLogger("plugins.eventslotarr")

_scheduler_thread = None
_scheduler_settings = None
_scheduler_started_at = None
_stop_scheduler = threading.Event()
_scheduler_lock = threading.RLock()

from .assignment import (
    assign_events_to_slots,
    clear_slots,
    get_configured_source_groups,
    get_next_scheduled_event,
)
from .parser import load_events
from .scheduler import scheduler_loop


class Plugin:
    name = "EventSlotarr"
    version = "0.2.3"
    description = "Assign temporary live event streams to placeholder channels."

    actions = [
        {"id": "assign_events", "label": "Assign Events Now"},
        {"id": "next_scheduled_event", "label": "Next Scheduled Event"},
        {"id": "clear_slots", "label": "Clear Slots"},
        {"id": "schedule_status", "label": "Scheduler Status"},
    ]

    def __init__(self):
        LOGGER.info("[EventSlotarr] Plugin initialized")

        try:
            settings = self._load_persisted_settings()

            if settings:
                self.ensure_scheduler_running(settings)
            else:
                LOGGER.warning(
                    "[EventSlotarr] Plugin initialized but no persisted settings were found; "
                    "scheduler will start on first plugin action"
                )

        except Exception:
            LOGGER.exception("[EventSlotarr] Failed during scheduler autostart")

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
                "help_text": "Use 'local' or an IANA timezone like America/Sao_Paulo.",
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
            },
            {
                "id": "beginning_day_time",
                "label": "Beginning day time",
                "type": "string",
                "default": "00:00",
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
            {
                "id": "refresh_plex_on_epg_change",
                "label": "Refresh Plex TV Guide when EPG changes",
                "type": "boolean",
                "default": False,
            },
            {
                "id": "plex_url",
                "label": "Plex Server URL",
                "type": "string",
                "default": "",
                "help_text": "Example: http://192.168.15.15:32400",
            },
            {
                "id": "plex_token",
                "label": "Plex Token",
                "type": "string",
                "default": "",
            },
        ]

    def _default_settings(self):
        return {
            field["id"]: field["default"]
            for field in self.fields
            if "id" in field and "default" in field
        }

    def _merge_defaults(self, settings):
        merged = self._default_settings()
        merged.update(settings or {})
        return merged

    def _load_persisted_settings(self):
        try:
            from apps.plugins.models import PluginConfig
        except Exception:
            LOGGER.exception("[EventSlotarr] Could not import PluginConfig")
            return {}

        candidates = [
            "eventslotarr",
            "event_slotarr",
            "event-slotarr",
            "EventSlotarr",
            self.name,
        ]

        config = None

        for key in candidates:
            try:
                config = PluginConfig.objects.filter(key=key, enabled=True).first()

                if config:
                    break
            except Exception:
                pass

        if not config:
            try:
                config = (
                    PluginConfig.objects
                    .filter(enabled=True, name__iexact=self.name)
                    .first()
                )
            except Exception:
                config = None

        if not config:
            LOGGER.warning("[EventSlotarr] No enabled PluginConfig found")
            return {}

        return self._merge_defaults(config.settings or {})

    def _resolve_settings(self, settings=None, context=None):
        if settings:
            return self._merge_defaults(settings)

        if isinstance(context, dict):
            ctx_settings = context.get("settings")

            if ctx_settings:
                return self._merge_defaults(ctx_settings)

        return self._load_persisted_settings()

    def start(self, settings=None, context=None):
        settings = self._resolve_settings(settings, context)
        self.ensure_scheduler_running(settings)

        return {"status": "success", "message": "EventSlotarr startup checked"}

    def on_start(self, settings=None, context=None):
        return self.start(settings=settings, context=context)

    def stop(self, context=None):
        self.stop_scheduler()

        return {"status": "success", "message": "EventSlotarr scheduler stopped"}

    def on_stop(self, context=None):
        return self.stop(context=context)

    def run(self, action, params=None, context=None):
        settings = self._resolve_settings(params or {}, context)

        LOGGER.info("[EventSlotarr] Action: %s", action)

        if action not in ("schedule_status", "next_scheduled_event"):
            self.ensure_scheduler_running(settings)

        try:
            if action == "assign_events":
                return self.assign_events_action(settings)

            if action == "next_scheduled_event":
                return self.next_scheduled_event_action(settings)

            if action == "clear_slots":
                return self.clear_slots_action(settings)

            if action == "schedule_status":
                return self.schedule_status_action(settings)

            return {"status": "error", "message": f"Unknown action '{action}'"}

        except Exception as ex:
            LOGGER.exception("[EventSlotarr] Error running action %s: %s", action, ex)
            return {"status": "error", "message": str(ex)}

    def assign_events_action(self, settings):
        assignments = assign_events_to_slots(
            settings,
            force_rebuild=True,
            check_source=True,
        )

        return {
            "status": "success",
            "message": f"{len(assignments)} channels assigned",
        }

    def next_scheduled_event_action(self, settings):
        message = get_next_scheduled_event(settings)

        return {
            "status": "success",
            "message": message,
        }

    def clear_slots_action(self, settings):
        clear_slots(settings)

        return {"status": "success", "message": "Slots cleared"}

    def schedule_status_action(self, settings):
        global _scheduler_thread, _scheduler_settings, _scheduler_started_at

        running = bool(_scheduler_thread and _scheduler_thread.is_alive())

        lines = [
            f"running: {running}",
            f"thread: {getattr(_scheduler_thread, 'name', None)}",
            f"started_at: {_scheduler_started_at}",
            f"source_change_check_minutes: {(settings or {}).get('source_change_check_minutes', 30)}",
            f"beginning_day_time: {(settings or {}).get('beginning_day_time', '00:00')}",
            f"minutes_before_event: {(settings or {}).get('minutes_before_event', 20)}",
            f"minutes_after_event: {(settings or {}).get('minutes_after_event', 20)}",
        ]

        if _scheduler_settings:
            lines.append(
                f"active_settings_keys: {', '.join(sorted(_scheduler_settings.keys()))}"
            )

        return {"status": "success", "message": "\n".join(lines)}

    def ensure_scheduler_running(self, settings):
        global _scheduler_thread

        settings = self._merge_defaults(settings or {})

        if not settings:
            LOGGER.warning("[EventSlotarr] Cannot auto-start scheduler: no settings available")
            return False

        with _scheduler_lock:
            if _scheduler_thread and _scheduler_thread.is_alive():
                return True

            self.restart_scheduler(settings)

        return True

    def stop_scheduler(self):
        global _scheduler_thread, _scheduler_settings, _scheduler_started_at

        with _scheduler_lock:
            if _scheduler_thread and _scheduler_thread.is_alive():
                _stop_scheduler.set()

                try:
                    _scheduler_thread.join(timeout=5)
                except Exception:
                    LOGGER.exception("[EventSlotarr] Error while stopping scheduler")

            _scheduler_thread = None
            _scheduler_settings = None
            _scheduler_started_at = None
            _stop_scheduler.clear()

    def restart_scheduler(self, settings):
        global _scheduler_thread, _scheduler_settings, _scheduler_started_at

        settings = self._merge_defaults(settings or {})

        with _scheduler_lock:
            if _scheduler_thread and _scheduler_thread.is_alive():
                _stop_scheduler.set()

                try:
                    _scheduler_thread.join(timeout=5)
                except Exception:
                    LOGGER.exception("[EventSlotarr] Error while stopping old scheduler")

            _stop_scheduler.clear()

            _scheduler_settings = dict(settings)
            _scheduler_started_at = datetime.now().isoformat(timespec="seconds")

            _scheduler_thread = threading.Thread(
                target=scheduler_loop,
                args=(_scheduler_settings, _stop_scheduler),
                daemon=True,
                name="EventSlotarrScheduler",
            )

            _scheduler_thread.start()

