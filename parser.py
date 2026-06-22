import logging
import re

from apps.channels.models import Stream

from .quality import quality_score

logger = logging.getLogger("EventSlotarr")

EVENT_RE = re.compile(
    r"(?P<time>\d{2}:\d{2})\s*-\s*(?P<event>.*?)\s*\[(?P<quality>.*?)\]",
    re.IGNORECASE
)


def normalize_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def normalize_event_name(name):
    name = name.upper()
    name = name.replace(" VS ", " X ")
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def parse_stream_name(name):
    match = EVENT_RE.match(name)

    if not match:
        return None

    return {
        "time": match.group("time"),
        "event": match.group("event").strip(),
        "quality": match.group("quality").strip().upper()
    }


def stream_is_usable(stream):
    stats = getattr(stream, "stream_stats", None)

    logger.info(
        f"Stream check: id={stream.id} name={stream.name!r} "
        f"is_stale={getattr(stream, 'is_stale', None)!r} "
        f"stream_stats={stats!r}"
    )

    if getattr(stream, "is_stale", False):
        logger.info(f"Skipping stale stream: {stream.name}")
        return False

    # None means not checked / no stats, not necessarily broken.
    if stats is None:
        return True

    if isinstance(stats, dict):
        width = stats.get("width")
        height = stats.get("height")
        status = str(stats.get("status", "")).lower()
        error = stats.get("error")

        if width == 0 or height == 0:
            logger.info(f"Skipping broken stream by stats resolution: {stream.name}")
            return False

        if status in (
            "dead",
            "offline",
            "failed",
            "error",
            "invalid",
            "unusable",
            "broken"
        ):
            logger.info(f"Skipping broken stream by stats status={status}: {stream.name}")
            return False

        if error:
            logger.info(f"Skipping broken stream by stats error={error}: {stream.name}")
            return False

    return True

def find_stream_group_name(source_group_name):
    wanted = normalize_text(source_group_name)

    all_groups = list(
        Stream.objects
        .exclude(channel_group__isnull=True)
        .values_list("channel_group__name", flat=True)
        .distinct()
    )

#    logger.info("EventSlotarr available stream groups:")

#    for group in all_groups:
#        logger.info(f" - {group}")

    for group in all_groups:
        if normalize_text(group) == wanted:
            return group

    for group in all_groups:
        if wanted in normalize_text(group) or normalize_text(group) in wanted:
            logger.info(f"Using fuzzy group match: '{source_group_name}' -> '{group}'")
            return group

    logger.warning(f"Source group not found: '{source_group_name}'")
    return None


def load_events(source_group_name):
    real_group_name = find_stream_group_name(source_group_name)

    if not real_group_name:
        return []

    logger.info(f"Loading event streams from group '{real_group_name}'")

    streams = (
        Stream.objects
        .filter(channel_group__name=real_group_name)
        .distinct()
    )

    logger.info(f"Found {streams.count()} stream(s) in group '{real_group_name}'")

    grouped = {}

    skipped_unusable = 0
    skipped_non_event = 0

    for stream in streams:
        if not stream_is_usable(stream):
            skipped_unusable += 1
            continue

        parsed = parse_stream_name(stream.name)

        if not parsed:
            skipped_non_event += 1
            logger.debug(f"Skipped non-event stream: {stream.name}")
            continue

        key = normalize_event_name(parsed["event"])

        entry = {
            "stream": stream,
            "event": parsed["event"],
            "time": parsed["time"],
            "quality": parsed["quality"],
            "source_name": stream.name
        }

        grouped.setdefault(key, []).append(entry)

    events = []

    for candidates in grouped.values():
        candidates.sort(key=lambda x: quality_score(x["quality"]))

        best = candidates[0]

        events.append({
            "event": best["event"],
            "time": best["time"],
            "alternatives": candidates
        })

    events.sort(key=lambda x: x["time"])

    logger.info(
        f"{len(events)} event(s) parsed from '{real_group_name}'. "
        f"Skipped unusable={skipped_unusable}, non_event={skipped_non_event}"
    )

    return events

