import logging
import re

from apps.channels.models import ChannelGroup

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


def discover_groups(params):
    patterns = split_setting(params.get("group_patterns", ""))

    if not patterns:
        return []

    regexes = []

    for pattern in patterns:
        try:
            regexes.append(re.compile(pattern, re.IGNORECASE))
        except Exception as ex:
            logger.warning(f"Invalid group regex '{pattern}': {ex}")

    groups = []

    for group in ChannelGroup.objects.all():
        for regex in regexes:
            if regex.search(group.name):
                groups.append(group.name)
                break

    groups = sorted(set(groups))

    logger.info(f"Auto-discovered {len(groups)} source groups")

    return groups

