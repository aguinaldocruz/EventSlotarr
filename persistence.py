import json
import logging
import os

logger = logging.getLogger("EventSlotarr")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)


def _path(filename):
    return os.path.join(DATA_DIR, filename)


def load_json(filename, default):
    path = _path(filename)

    try:
        if not os.path.exists(path):
            return default

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception as ex:
        logger.exception(f"Failed loading {filename}: {ex}")
        return default


def save_json(filename, data):
    path = _path(filename)

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    except Exception as ex:
        logger.exception(f"Failed saving {filename}: {ex}")
