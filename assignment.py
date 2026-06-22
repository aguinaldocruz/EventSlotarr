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


def split_setting(value):
    if not value:
        return []

    value = str(value).replace("\n", ",")

    return [
        x.strip()
        for x in value.split(",")
        if x.strip()
    ]


def get_configured_source_groups(params):
    if bool_setting(params.get("auto_discover_groups"), default=False):
        return discover_groups(params)

    return split_setting(params.get("source_groups", ""))


def get_slot_channels(params):
    if bool_setting(params.get("auto_create_channels"), default=False):
        return ensure_virtual_channels(params)

    slot_channels = []

    for name in split_setting(params.get("placeholder_channels", "")):
        channel = Channel.objects.filter(name=name).first()

        if channel:
            slot_channels.append(channel)
        else:
            logger.warning(f"Placeholder channel not found: {name}")

    return slot_channels


def load_all_events(params):
    events = []

    for group_name in get_configured_source_groups(params):
        events.extend(load_events(group_name))

    events.sort(key=lambda x: x["time"])

    return filter_active_events(params, events)


def channel_stream_ids(channel):
    return list(
        ChannelStream.objects
        .filter(channel=channel)
        .order_by("order")
        .values_list("stream_id", flat=True)
    )


def streams_match(slot_channel, source_channel):
    return channel_stream_ids(slot_channel) == channel_stream_ids(source_channel)


def replace_stream(slot_channel, source_stream):
    current_stream_ids = channel_stream_ids(slot_channel)

    if current_stream_ids == [source_stream.id]:
        return False

    ChannelStream.objects.filter(channel=slot_channel).delete()

    ChannelStream.objects.create(
        channel=slot_channel,
        stream=source_stream,
        order=0
    )

    logger.info(f"{slot_channel.name}: assigned stream {source_stream.name}")

    return True


def clear_channel(slot_channel):
    qs = ChannelStream.objects.filter(channel=slot_channel)

    if not qs.exists():
        return False

    qs.delete()
    return True


def clear_slots(params):
    for slot_channel in get_slot_channels(params):
        clear_slot(slot_channel.name)

        if clear_channel(slot_channel):
            increment_changes()


def parse_event_datetime(event_time):
    now = datetime.utcnow()

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


def build_xmltv_assignment(slot_channel, event):
    start = parse_event_datetime(event["time"])
    stop = start + timedelta(hours=2)

    return {
        "channel_number": getattr(slot_channel, "channel_number", None),
        "display_name": slot_channel.name,
        "event": event["event"],
        "start": start,
        "stop": stop
    }


def assign_existing_sticky_events(events, free_slots, assignments, xmltv_assignments):
    used_events = set()

    for event in events:
        previous_slot_name = get_slot(event["event"])

        if not previous_slot_name or previous_slot_name not in free_slots:
            continue

        slot_channel = free_slots.pop(previous_slot_name)
        source = choose_best(event["alternatives"])

        if replace_stream(slot_channel, source["stream"]):
            increment_changes()

        assignments.append((slot_channel.name, source["stream"].name))
        used_events.add(event["event"])

        xmltv_assignments[slot_channel.name] = build_xmltv_assignment(
            slot_channel,
            event
        )

    return used_events


def assign_new_events(events, free_slots, used_events, assignments, xmltv_assignments):
    for event in events:
        if event["event"] in used_events:
            continue

        if not free_slots:
            break

        slot_name = next(iter(free_slots))
        slot_channel = free_slots.pop(slot_name)

        assign_slot(event["event"], slot_channel.name)

        source = choose_best(event["alternatives"])

        if replace_stream(slot_channel, source["stream"]):
            increment_changes()

        assignments.append((slot_channel.name, source["stream"].name))
        used_events.add(event["event"])

        xmltv_assignments[slot_channel.name] = build_xmltv_assignment(
            slot_channel,
            event
        )


def clear_unused_slots(free_slots):
    for slot_channel in free_slots.values():
        clear_slot(slot_channel.name)

        if clear_channel(slot_channel):
            increment_changes()


def write_xmltv_if_enabled(params, xmltv_assignments):
    from .channels import bool_setting

    if not bool_setting(params.get("enable_xmltv"), default=True):
        return

    output_path = params.get("xmltv_output") or "/data/eventslotarr.xml"

    save_xmltv(output_path, xmltv_assignments)


def assign_events_to_slots(params):
    update_run()

    events = load_all_events(params)
    slot_channels = get_slot_channels(params)

    free_slots = {
        channel.name: channel
        for channel in slot_channels
    }

    assignments = []
    xmltv_assignments = {}

    used_events = assign_existing_sticky_events(
        events,
        free_slots,
        assignments,
        xmltv_assignments
    )

    assign_new_events(
        events,
        free_slots,
        used_events,
        assignments,
        xmltv_assignments
    )

    clear_unused_slots(free_slots)

    set_assignments(assignments)
    write_xmltv_if_enabled(params, xmltv_assignments)

    return assignments

