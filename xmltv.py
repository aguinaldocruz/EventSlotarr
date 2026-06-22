import datetime
import logging
import xml.etree.ElementTree as ET

logger = logging.getLogger("EventSlotarr")


def xmltv_time(dt):
    return dt.strftime("%Y%m%d%H%M%S +0000")


def generate_xmltv(assignments):
    root = ET.Element("tv")

    for slot_name, info in assignments.items():
        channel_id = str(info.get("channel_number") or slot_name)

        channel = ET.SubElement(root, "channel", id=channel_id)

        display = ET.SubElement(channel, "display-name")
        display.text = info.get("display_name", slot_name)

        event_title = info.get("event")
        if not event_title:
            continue

        start = info.get("start") or datetime.datetime.utcnow()
        stop = info.get("stop") or start + datetime.timedelta(hours=2)

        programme = ET.SubElement(
            root,
            "programme",
            channel=channel_id,
            start=xmltv_time(start),
            stop=xmltv_time(stop)
        )

        title = ET.SubElement(programme, "title")
        title.text = event_title

        desc = ET.SubElement(programme, "desc")
        desc.text = "Live event assigned by EventSlotarr"

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def save_xmltv(path, assignments):
    try:
        xml_data = generate_xmltv(assignments)

        with open(path, "wb") as f:
            f.write(xml_data)

        logger.info(f"XMLTV saved to {path}")
        return True

    except Exception as ex:
        logger.exception(f"Failed saving XMLTV: {ex}")
        return False
