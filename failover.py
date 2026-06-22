from .quality import quality_score
from .persistence import load_json, save_json

_stream_health = load_json("health.json", {})


def _persist():
    save_json("health.json", _stream_health)


def mark_failed(stream_id):
    _stream_health[str(stream_id)] = False
    _persist()


def mark_good(stream_id):
    _stream_health[str(stream_id)] = True
    _persist()


def stream_ok(stream_id):
    return _stream_health.get(str(stream_id), True)


def choose_best(entries):
    healthy = [
        entry
        for entry in entries
        if stream_ok(entry["stream"].id)
    ]

    if not healthy:
        healthy = entries

    healthy.sort(key=lambda x: quality_score(x["quality"]))

    return healthy[0]

