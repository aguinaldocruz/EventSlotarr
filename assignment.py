import logging
from datetime import datetime, timedelta

from apps.channels.models import Channel, ChannelStream

from .channels import bool_setting, ensure_virtual_channels
from .discovery import discover_groups
from .events import filter_active_events
from .failover import choose_best
from .parser import load_events
from .state import increment_changes, set_assignments, update_run
from .sticky import assign_slot, clear_slot, get_slot
from .xmltv import save_xmltv

logger = logging.getLogger("EventSlotarr")


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
            logger.warning(f"[EventSlotarr] Placeholder channel not found: {name}")

    return slot_channels


def load_all_events_for_day(params):
    events = []

    for group_name in get_configured_source_groups(params):
        events.extend(load_events(group_name))

    events.sort(key=lambda x: x["time"])

    return events


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
        logger.info(f"[EventSlotarr] {slot_channel.name}: already correct")
        return False

    ChannelStream.objects.filter(channel=slot_channel).delete()

    ChannelStream.objects.create(
        channel=slot_channel,
        stream=source_stream,
        order=0
    )

    logger.info(
        f"[EventSlotarr] {slot_channel.name}: assigned stream {source_stream.name}"
    )

    return True


def clear_channel(slot_channel):
    qs = ChannelStream.objects.filter(channel=slot_channel)

    if not qs.exists():
        return False

    qs.delete()

    logger.info(f"[EventSlotarr] {slot_channel.name}: cleared")

    return True


def clear_slots(params):
    for slot_channel in get_slot_channels(params):
        clear_slot(slot_channel.name)

        if clear_channel(slot_channel):
            increment_changes()


def parse_event_datetime(event_time):
    now = datetime.now()

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


def event_duration(params):
    return timedelta(
        hours=int(params.get("event_duration_hours", 2))
    )


def get_event_window(params, event):
    start = parse_event_datetime(event["time"])
    stop = start + event_duration(params)

    return start, stop


def build_xmltv_assignment(slot_channel, event, title=None, start=None, stop=None):
    if start is None or stop is None:
        start, stop = get_event_window({}, event)

    epg_id = (
        getattr(slot_channel, "tvg_id", None)
        or getattr(slot_channel, "xmltv_id", None)
        or getattr(slot_channel, "channel_id", None)
        or getattr(slot_channel, "channel_number", None)
        or slot_channel.name
    )

    return {
        "channel_id": str(epg_id),
        "channel_number": getattr(slot_channel, "channel_number", None),
        "display_name": slot_channel.name,
        "event": title or event["event"],
        "start": start,
        "stop": stop
    }


def windows_overlap(start_a, stop_a, start_b, stop_b):
    return start_a < stop_b and start_b < stop_a


def event_overlaps_slot(slot_items, start, stop):
    for item in slot_items:
        if windows_overlap(start, stop, item["start"], item["stop"]):
            return True

    return False


def allocate_events_to_slots(params, slot_channels, all_day_events):
    """
    Allocate all events to the first available channel slot in timeline order.

    Example:
    Slot 1: 16:00 game, 19:00 game, 22:00 game
    Slot 2: only used when another event overlaps Slot 1
    Slot 3: only used when Slot 1 and Slot 2 are both busy
    """

    timeline = {
        slot.name: []
        for slot in slot_channels
    }

    ignored = []

    sorted_events = sorted(
        all_day_events,
        key=lambda e: get_event_window(params, e)[0]
    )

    for event in sorted_events:
        start, stop = get_event_window(params, event)
        placed = False

        for slot in slot_channels:
            slot_items = timeline[slot.name]

            if not event_overlaps_slot(slot_items, start, stop):
                slot_items.append({
                    "event": event,
                    "start": start,
                    "stop": stop,
                    "slot": slot
                })

                logger.info(
                    f"[EventSlotarr] Timeline allocation: "
                    f"{event['time']} - {event['event']} -> {slot.name}"
                )

                placed = True
                break

        if not placed:
            ignored.append(event)

            logger.warning(
                f"[EventSlotarr] Event ignored because all slots overlap: "
                f"{event['time']} - {event['event']}"
            )

    return timeline, ignored


def find_timeline_item_for_event(timeline, event):
    for slot_name, items in timeline.items():
        for item in items:
            if item["event"]["event"] == event["event"]:
                return item

    return None


def get_current_timeline_items(params, timeline):
    now = datetime.now()
    current = []

    for slot_name, items in timeline.items():
        for item in items:
            if item["start"] <= now <= item["stop"]:
                current.append(item)

    return current


def build_next_event_title(next_item):
    event = next_item["event"]
    return f"Next event AT {event['time']} - {event['event']}"


def build_filler_programmes(params, slot_channels, timeline):
    """
    Fill EPG gaps with:
    - Next event AT xx:xx - Title
    - No Live Events Today
    """

    now = datetime.now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = now.replace(hour=23, minute=59, second=59, microsecond=0)

    fillers = {}

    for slot in slot_channels:
        items = sorted(
            timeline.get(slot.name, []),
            key=lambda x: x["start"]
        )

        if not items:
            fillers[f"{slot.name}-no-events"] = {
                "channel_id": str(
                    getattr(slot, "tvg_id", None)
                    or getattr(slot, "xmltv_id", None)
                    or getattr(slot, "channel_id", None)
                    or getattr(slot, "channel_number", None)
                    or slot.name
                ),
                "channel_number": getattr(slot, "channel_number", None),
                "display_name": slot.name,
                "event": "No Live Events Today",
                "start": day_start,
                "stop": day_end
            }
            continue

        previous_stop = day_start

        for idx, item in enumerate(items):
            if previous_stop < item["start"]:
                fillers[f"{slot.name}-filler-before-{idx}"] = {
                    "channel_id": str(
                        getattr(slot, "tvg_id", None)
                        or getattr(slot, "xmltv_id", None)
                        or getattr(slot, "channel_id", None)
                        or getattr(slot, "channel_number", None)
                        or slot.name
                    ),
                    "channel_number": getattr(slot, "channel_number", None),
                    "display_name": slot.name,
                    "event": build_next_event_title(item),
                    "start": previous_stop,
                    "stop": item["start"]
                }

            previous_stop = item["stop"]

        if previous_stop < day_end:
            fillers[f"{slot.name}-filler-after-last"] = {
                "channel_id": str(
                    getattr(slot, "tvg_id", None)
                    or getattr(slot, "xmltv_id", None)
                    or getattr(slot, "channel_id", None)
                    or getattr(slot, "channel_number", None)
                    or slot.name
                ),
                "channel_number": getattr(slot, "channel_number", None),
                "display_name": slot.name,
                "event": "No More Live Events Today",
                "start": previous_stop,
                "stop": day_end
            }

    return fillers


def build_all_day_xmltv(params, slot_channels, timeline):
    xmltv_assignments = {}

    for slot in slot_channels:
        items = timeline.get(slot.name, [])

        for item in items:
            event = item["event"]

            key = f"{slot.name}-{event['time']}-{event['event']}"

            xmltv_assignments[key] = build_xmltv_assignment(
                slot,
                event,
                title=event["event"],
                start=item["start"],
                stop=item["stop"]
            )

    fillers = build_filler_programmes(
        params,
        slot_channels,
        timeline
    )

    xmltv_assignments.update(fillers)

    logger.info(
        f"[EventSlotarr] Built XMLTV with "
        f"{len(xmltv_assignments)} programme(s)"
    )

    return xmltv_assignments


def assign_current_events_from_timeline(params, timeline, slot_channels):
    assignments = []
    now = datetime.now()

    occupied_slot_names = set()

    for slot in slot_channels:
        items = timeline.get(slot.name, [])

        current_item = None

        for item in items:
            if item["start"] <= now <= item["stop"]:
                current_item = item
                break

        if not current_item:
            continue

        event = current_item["event"]
        source = choose_best(event["alternatives"])

        assign_slot(event["event"], slot.name)

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
        return

    if not xmltv_assignments:
        logger.warning("[EventSlotarr] XMLTV not written because there are no events")
        return

    output_path = params.get("xmltv_output") or "/data/eventslotarr.xml"

    logger.info(
        f"[EventSlotarr] Writing XMLTV with {len(xmltv_assignments)} programme(s)"
    )

    save_xmltv(output_path, xmltv_assignments)


def assign_events_to_slots(params):
    update_run()

    all_day_events = load_all_events_for_day(params)

    logger.info(f"[EventSlotarr] Events for day: {len(all_day_events)}")

    slot_channels = get_slot_channels(params)

    logger.info(f"[EventSlotarr] Slot channels found: {len(slot_channels)}")

    timeline, ignored = allocate_events_to_slots(
        params,
        slot_channels,
        all_day_events
    )

    assignments = assign_current_events_from_timeline(
        params,
        timeline,
        slot_channels
    )

    set_assignments(assignments)

    all_day_xmltv = build_all_day_xmltv(
        params,
        slot_channels,
        timeline
    )

    write_xmltv_if_enabled(
        params,
        all_day_xmltv
    )

    logger.info(
        f"[EventSlotarr] Current assignments: {len(assignments)}, "
        f"ignored events: {len(ignored)}"
    )

    return assignments

