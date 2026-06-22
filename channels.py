import logging

from apps.channels.models import Channel

logger = logging.getLogger("EventSlotarr")


def bool_setting(value, default=False):
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        return value.lower() in ("true", "yes", "1", "on")

    return bool(value)


def split_setting(value):
    if not value:
        return []

    value = str(value).replace("\n", ",")

    return [
        x.strip()
        for x in value.split(",")
        if x.strip()
    ]


def get_virtual_channel_names(params):
    names = split_setting(
        params.get("placeholder_channels", "")
    )

    if names:
        return names

    prefix = params.get("channel_prefix") or "EventSlotarr"

    count = int(
        params.get("channel_count", 4)
    )

    return [
        f"{prefix} {i + 1}"
        for i in range(count)
    ]


def ensure_virtual_channels(params):
    names = get_virtual_channel_names(params)

    start_number = int(
        params.get("starting_channel_number", 9801)
    )

    channels = []

    for i, name in enumerate(names):
        number = start_number + i

        channel, created = Channel.objects.get_or_create(
            name=name,
            defaults={
                "channel_number": str(number)
            }
        )

        if created:
            logger.info(
                f"Created virtual channel {number} {name}"
            )
        else:
            logger.info(
                f"Using existing virtual channel {channel.channel_number} {channel.name}"
            )

        channels.append(channel)

    return channels

