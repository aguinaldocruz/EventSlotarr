import hashlib
import json
import logging
import os
import xml.etree.ElementTree as ET
from datetime import timedelta

from apps.channels.models import Channel, ChannelStream
from django.db import transaction

from .channels import bool_setting, ensure_virtual_channels
from .discovery import discover_groups
from .failover import choose_best
from .parser import load_events
from .persistence import load_json, save_json
from .plex import refresh_plex_tv_guide
from .state import get_state, increment_changes, set_assignments, update_run
from .sticky import assign_slot, clear_slot, get_slot
from .timezone_utils import day_bounds, now_local, parse_today_time
from .xmltv import save_xmltv

logger = logging.getLogger("plugins.eventslotarr")

SCHEDULE_STATE_FILE = "schedule_state.json"

def diagnose_system(params):
    """Return a compact end-to-end report suitable for Dispatcharr logs/UI."""
    groups = get_configured_source_groups(params)
    group_reports = []
    all_events = []
    for group in groups:
        events = load_events(group)
        all_events.extend(events)
        group_reports.append({
            "configured": group,
            "events": len(events),
            "event_names": [f"{e['time']} - {e['event']}" for e in events[:20]],
        })

    slots = get_slot_channels(params)
    slot_reports = []
    for slot in slots:
        streams = list(ChannelStream.objects.filter(channel=slot).order_by("order"))
        slot_reports.append({
            "name": slot.name,
            "number": getattr(slot, "channel_number", None),
            "epg_id": get_channel_epg_id(slot),
            "stream_ids": [getattr(x.stream, "id", None) for x in streams],
            "stream_names": [getattr(x.stream, "name", None) for x in streams],
        })

    timeline, ignored = allocate_events_to_slots(params, slots, all_events)
    now = now_local(params)
    due = []
    for items in timeline.values():
        item = find_due_item_for_slot(params, items)
        if item:
            due.append({"slot": item["slot"].name, "event": item["event"]["event"], "time": item["event"]["time"]})

    xml_path = params.get("xmltv_output") or "/data/eventslotarr.xml"
    xml_report = {"path": xml_path, "exists": os.path.exists(xml_path)}
    if xml_report["exists"]:
        xml_report["bytes"] = os.path.getsize(xml_path)
        try:
            root = ET.parse(xml_path).getroot()
            xml_report["channels"] = len(root.findall("./channel"))
            xml_report["programmes"] = len(root.findall("./programme"))
        except Exception as ex:
            xml_report["parse_error"] = str(ex)

    return {
        "now": now.isoformat(),
        "groups": group_reports,
        "events_total": len(all_events),
        "slots": slot_reports,
        "due": due,
        "ignored": [f"{e.get('time')} - {e.get('event')}" for e in ignored],
        "xmltv": xml_report,
        "schedule_state": load_schedule_state(),
    }


def get_next_scheduled_event(params):
    all_day_events = load_all_events_for_day(params)
    slot_channels = get_slot_channels(params)

    if not all_day_events:
        return "No source events found for today."

    if not slot_channels:
        return "No EventSlotarr slot channels found."

    timeline, _ignored = allocate_events_to_slots(
        params,
        slot_channels,
        all_day_events,
    )

    now = now_local(params)
    future_items = []

    for items in timeline.values():
        for item in items:
            if item["operational_start"] > now:
                future_items.append(item)

    if not future_items:
        return "No more scheduled EventSlotarr events today."

    next_item = min(
        future_items,
        key=lambda item: item["operational_start"],
    )

    event = next_item["event"]
    slot = next_item["slot"]

    return "\n".join(
        [
            f"Next scheduled event: {event.get('event')}",
            f"Event time: {next_item['start'].strftime('%H:%M')}",
            f"Slot channel: {slot.name}",
            f"Stream load time: {next_item['operational_start'].strftime('%H:%M')}",
            f"Keep until: {next_item['operational_stop'].strftime('%H:%M')}",
        ]
    )

def int_setting(params, key, default):
    try:
        return int(params.get(key, default))
    except Exception:
        return default


def get_configured_source_groups(params):
    if bool_setting(params.get("auto_discover_groups"), default=False):
        return discover_groups(params)

    return [
        x.strip()
        for x in str(params.get("source_groups", "")).replace("\n", ",").split(",")
        if x.strip()
    ]


def get_slot_channels(params):
    if bool_setting(params.get("auto_create_channels"), default=False):
        return ensure_virtual_channels(params)

    slot_channels = []

    names = str(params.get("placeholder_channels", "")).replace("\n", ",")

    for name in names.split(","):
        name = name.strip()

        if not name:
            continue

        channel = Channel.objects.filter(name=name).first()

        if channel:
            slot_channels.append(channel)
        else:
            logger.warning("[EventSlotarr] Placeholder channel not found: %s", name)

    return slot_channels


def load_all_events_for_day(params):
    events = []

    for group_name in get_configured_source_groups(params):
        events.extend(load_events(group_name))

    events.sort(key=lambda x: x["time"])

    return events


def event_source_signature(events, params=None, slot_channels=None):
    payload = {
        "events": [],
        "settings": {
            key: (params or {}).get(key)
            for key in (
                "event_timezone", "event_duration_hours",
                "minutes_before_event", "minutes_after_event",
            )
        },
        "slots": [
            {"name": slot.name, "number": getattr(slot, "channel_number", None)}
            for slot in (slot_channels or [])
        ],
    }

    for event in sorted(events, key=lambda e: (e.get("time"), e.get("event"))):
        alternatives = []

        for alt in event.get("alternatives", []):
            stream = alt.get("stream")

            alternatives.append(
                {
                    "source_name": alt.get("source_name"),
                    "quality": alt.get("quality"),
                    "stream_id": getattr(stream, "id", None),
                    "stream_name": getattr(stream, "name", None),
                }
            )

        payload["events"].append(
            {
                "time": event.get("time"),
                "event": event.get("event"),
                "alternatives": sorted(alternatives, key=lambda x: str(x)),
            }
        )

    data = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )

    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def load_schedule_state():
    return load_json(
        SCHEDULE_STATE_FILE,
        {
            "source_signature": None,
            "timeline": {},
            "ignored": [],
            "last_rebuild": None,
        },
    )


def save_schedule_state(state):
    save_json(SCHEDULE_STATE_FILE, state)


def channel_stream_ids(channel):
    return list(
        ChannelStream.objects
        .filter(channel=channel)
        .order_by("order")
        .values_list("stream_id", flat=True)
    )


def streams_match(slot_channel, source_stream):
    return channel_stream_ids(slot_channel) == [source_stream.id]


def replace_stream(slot_channel, source_stream):
    if streams_match(slot_channel, source_stream):
        logger.info("[EventSlotarr] %s: already correct", slot_channel.name)
        return False

    with transaction.atomic():
        # Serialize scheduler/manual actions across Dispatcharr workers.
        locked_channel = Channel.objects.select_for_update().get(pk=slot_channel.pk)
        ChannelStream.objects.filter(channel=locked_channel).delete()
        ChannelStream.objects.create(
            channel=locked_channel,
            stream=source_stream,
            order=0,
        )

    logger.info(
        "[EventSlotarr] %s: assigned stream %s",
        slot_channel.name,
        source_stream.name,
    )

    return True


def clear_channel(slot_channel):
    with transaction.atomic():
        locked_channel = Channel.objects.select_for_update().get(pk=slot_channel.pk)
        qs = ChannelStream.objects.filter(channel=locked_channel)

        if not qs.exists():
            return False

        qs.delete()

    logger.info("[EventSlotarr] %s: cleared", slot_channel.name)

    return True


def clear_slots(params):
    for slot_channel in get_slot_channels(params):
        clear_slot(slot_channel.name)

        if clear_channel(slot_channel):
            increment_changes()


def parse_event_datetime(params, event_time):
    try:
        return parse_today_time(params, event_time)
    except Exception:
        return now_local(params)


def event_duration(params):
    return timedelta(hours=int_setting(params, "event_duration_hours", 2))


def before_event_delta(params):
    return timedelta(minutes=int_setting(params, "minutes_before_event", 20))


def after_event_delta(params):
    return timedelta(minutes=int_setting(params, "minutes_after_event", 20))


def get_event_window(params, event):
    start = parse_event_datetime(params, event["time"])
    stop = start + event_duration(params)

    return start, stop


def get_operational_window(params, event):
    start, stop = get_event_window(params, event)

    return start - before_event_delta(params), stop + after_event_delta(params)


def get_channel_epg_id(slot_channel):
    return (
        getattr(slot_channel, "tvg_id", None)
        or getattr(slot_channel, "xmltv_id", None)
        or getattr(slot_channel, "channel_id", None)
        or getattr(slot_channel, "channel_number", None)
        or slot_channel.name
    )


def build_xmltv_assignment(
    params,
    slot_channel,
    event,
    title=None,
    start=None,
    stop=None,
):
    if start is None or stop is None:
        start, stop = get_event_window(params, event)

    epg_id = get_channel_epg_id(slot_channel)

    return {
        "channel_id": str(epg_id),
        "channel_number": getattr(slot_channel, "channel_number", None),
        "display_name": slot_channel.name,
        "event": title or event["event"],
        "start": start,
        "stop": stop,
    }


def windows_overlap(start_a, stop_a, start_b, stop_b):
    return start_a < stop_b and start_b < stop_a


def event_overlaps_slot(slot_items, start, stop):
    for item in slot_items:
        if windows_overlap(
            start,
            stop,
            item["operational_start"],
            item["operational_stop"],
        ):
            return True

    return False


def event_identity(event):
    return f"{event.get('time', '')}|{event.get('event', '')}"


def allocate_events_to_slots(params, slot_channels, all_day_events):
    """Allocate events while preserving valid previous event-to-slot mappings."""
    timeline = {slot.name: [] for slot in slot_channels}
    ignored = []
    slots_by_name = {slot.name: slot for slot in slot_channels}

    sorted_events = sorted(
        all_day_events,
        key=lambda e: get_event_window(params, e)[0],
    )

    def place(event, slot):
        start, stop = get_event_window(params, event)
        operational_start, operational_stop = get_operational_window(params, event)
        items = timeline[slot.name]
        if event_overlaps_slot(items, operational_start, operational_stop):
            return False
        items.append({
            "event": event,
            "start": start,
            "stop": stop,
            "operational_start": operational_start,
            "operational_stop": operational_stop,
            "slot": slot,
        })
        return True

    # First retain existing mappings when they are still valid.
    unplaced = []
    for event in sorted_events:
        mapped_name = get_slot(event_identity(event))
        slot = slots_by_name.get(mapped_name)
        if slot and place(event, slot):
            logger.info("[EventSlotarr] Retained sticky allocation: %s -> %s", event["event"], slot.name)
        else:
            unplaced.append(event)

    # Fill remaining slots deterministically for new events.
    for event in unplaced:
        for slot in slot_channels:
            if place(event, slot):
                logger.info("[EventSlotarr] Timeline allocation: %s - %s -> %s", event["time"], event["event"], slot.name)
                break
        else:
            ignored.append(event)
            logger.warning("[EventSlotarr] Event ignored because all slots overlap: %s - %s", event["time"], event["event"])

    return timeline, ignored

def build_next_event_title(next_item):
    event = next_item["event"]

    return f"Next event AT {event['time']} - {event['event']}"


def build_filler_assignment(slot, title, start, stop):
    return {
        "channel_id": str(get_channel_epg_id(slot)),
        "channel_number": getattr(slot, "channel_number", None),
        "display_name": slot.name,
        "event": title,
        "start": start,
        "stop": stop,
    }


def build_filler_programmes(params, slot_channels, timeline):
    day_start, day_end = day_bounds(params)
    fillers = {}

    for slot in slot_channels:
        items = sorted(
            timeline.get(slot.name, []),
            key=lambda x: x["start"],
        )

        if not items:
            fillers[f"{slot.name}-no-events"] = build_filler_assignment(
                slot,
                "No Live Events Today",
                day_start,
                day_end,
            )
            continue

        previous_stop = day_start

        for idx, item in enumerate(items):
            if previous_stop < item["start"]:
                fillers[f"{slot.name}-filler-before-{idx}"] = build_filler_assignment(
                    slot,
                    build_next_event_title(item),
                    previous_stop,
                    item["start"],
                )

            previous_stop = max(previous_stop, item["stop"])

        if previous_stop < day_end:
            fillers[f"{slot.name}-filler-after-last"] = build_filler_assignment(
                slot,
                "No More Live Events Today",
                previous_stop,
                day_end,
            )

    return fillers


def build_all_day_xmltv(params, slot_channels, timeline):
    xmltv_assignments = {}

    for slot in slot_channels:
        items = timeline.get(slot.name, [])

        for item in items:
            event = item["event"]
            key = f"{slot.name}-{event['time']}-{event['event']}"

            xmltv_assignments[key] = build_xmltv_assignment(
                params,
                slot,
                event,
                title=event["event"],
                start=item["start"],
                stop=item["stop"],
            )

    xmltv_assignments.update(
        build_filler_programmes(
            params,
            slot_channels,
            timeline,
        )
    )

    logger.info(
        "[EventSlotarr] Built XMLTV with %s programme(s)",
        len(xmltv_assignments),
    )

    return xmltv_assignments


def find_due_item_for_slot(params, items):
    now = now_local(params)
    due = None

    for item in sorted(items, key=lambda x: x["operational_start"]):
        logger.info(
            "[EventSlotarr] Due check: now=%s event=%s start=%s stop=%s load_from=%s keep_until=%s",
            now,
            item["event"].get("event"),
            item["start"],
            item["stop"],
            item["operational_start"],
            item["operational_stop"],
        )

        if item["operational_start"] <= now <= item["operational_stop"]:
            due = item

    return due


def assign_due_events_from_timeline(params, timeline, slot_channels):
    assignments = []
    occupied_slot_names = set()

    for slot in slot_channels:
        items = timeline.get(slot.name, [])
        due_item = find_due_item_for_slot(params, items)

        if not due_item:
            continue

        event = due_item["event"]
        source = choose_best(event["alternatives"])

        assign_slot(event_identity(event), slot.name)

        if replace_stream(slot, source["stream"]):
            increment_changes()

        assignments.append((slot.name, source["stream"].name))
        occupied_slot_names.add(slot.name)

    for slot in slot_channels:
        if slot.name not in occupied_slot_names:
            clear_slot(slot.name)

            if clear_channel(slot):
                increment_changes()

    return assignments


def write_xmltv_if_enabled(params, xmltv_assignments):
    if not bool_setting(params.get("enable_xmltv"), default=True):
        logger.info("[EventSlotarr] XMLTV disabled")
        return False

    if not xmltv_assignments:
        logger.warning("[EventSlotarr] XMLTV not written because there are no events")
        return False

    output_path = params.get("xmltv_output") or "/data/eventslotarr.xml"

    logger.info(
        "[EventSlotarr] Writing XMLTV with %s programme(s)",
        len(xmltv_assignments),
    )

    return bool(save_xmltv(output_path, xmltv_assignments, params))


def rebuild_timeline_and_xmltv(params, all_day_events, slot_channels, signature):
    timeline, ignored = allocate_events_to_slots(
        params,
        slot_channels,
        all_day_events,
    )

    all_day_xmltv = build_all_day_xmltv(
        params,
        slot_channels,
        timeline,
    )

    epg_changed = write_xmltv_if_enabled(
        params,
        all_day_xmltv,
    )

    if epg_changed:
        refresh_plex_tv_guide(params)
    else:
        logger.info(
            "[EventSlotarr] Plex refresh skipped because XMLTV was not updated"
        )

    save_schedule_state(
        {
            "source_signature": signature,
            "last_rebuild": now_local(params).isoformat(),
            "empty_scan_count": 0,
            "timeline": {},
            "ignored": [
                f"{e.get('time')} - {e.get('event')}"
                for e in ignored
            ],
        }
    )

    return timeline, ignored


def assign_events_to_slots(params, force_rebuild=False, check_source=True):
    update_run()

    all_day_events = load_all_events_for_day(params)

    logger.info("[EventSlotarr] Events for day: %s", len(all_day_events))

    slot_channels = get_slot_channels(params)

    logger.info("[EventSlotarr] Slot channels found: %s", len(slot_channels))

    state = load_schedule_state()
    if not all_day_events and state.get("source_signature") and not force_rebuild:
        empty_scans = int(state.get("empty_scan_count", 0)) + 1
        state["empty_scan_count"] = empty_scans
        save_schedule_state(state)
        if empty_scans < 2:
            logger.warning("[EventSlotarr] Empty source scan %s; retaining the last assignment", empty_scans)
            return list((get_state().get("current_assignments") or {}).items())

    signature = event_source_signature(all_day_events, params, slot_channels)
    source_changed = signature != state.get("source_signature")

    if force_rebuild or check_source or source_changed:
        if force_rebuild or source_changed:
            logger.info("[EventSlotarr] Source events changed; rebuilding timeline and XMLTV")

            timeline, ignored = rebuild_timeline_and_xmltv(
                params,
                all_day_events,
                slot_channels,
                signature,
            )

        else:
            logger.info("[EventSlotarr] Source events unchanged")

            timeline, ignored = allocate_events_to_slots(
                params,
                slot_channels,
                all_day_events,
            )

    else:
        timeline, ignored = allocate_events_to_slots(
            params,
            slot_channels,
            all_day_events,
        )

    assignments = assign_due_events_from_timeline(
        params,
        timeline,
        slot_channels,
    )

    set_assignments(assignments)

    logger.info(
        "[EventSlotarr] Due assignments: %s, ignored events: %s, source_changed=%s",
        len(assignments),
        len(ignored),
        source_changed,
    )

    return assignments


def seconds_until_next_slot_change(params):
    all_day_events = load_all_events_for_day(params)
    slot_channels = get_slot_channels(params)

    timeline, _ignored = allocate_events_to_slots(
        params,
        slot_channels,
        all_day_events,
    )

    now = now_local(params)
    next_times = []

    for items in timeline.values():
        for item in items:
            if item["operational_start"] > now:
                next_times.append(item["operational_start"])

            if item["operational_stop"] > now:
                next_times.append(item["operational_stop"])

    if not next_times:
        return None

    next_time = min(next_times)

    return max(0, int((next_time - now).total_seconds()))

