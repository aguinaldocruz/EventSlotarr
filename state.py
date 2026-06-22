import threading
from datetime import datetime

from .persistence import load_json, save_json

_lock = threading.Lock()

_default_state = {
    "startup_time": datetime.now().isoformat(),
    "last_run": None,
    "run_count": 0,
    "change_count": 0,
    "current_assignments": {},
    "errors": []
}

_state = load_json("state.json", _default_state)


def _persist():
    save_json("state.json", _state)


def update_run():
    with _lock:
        _state["last_run"] = datetime.now().isoformat()
        _state["run_count"] = int(_state.get("run_count", 0)) + 1
        _persist()


def increment_changes():
    with _lock:
        _state["change_count"] = int(_state.get("change_count", 0)) + 1
        _persist()


def set_assignments(assignments):
    with _lock:
        _state["current_assignments"] = dict(assignments)
        _persist()


def add_error(message):
    with _lock:
        errors = _state.setdefault("errors", [])
        errors.append(str(message))
        _state["errors"] = errors[-20:]
        _persist()


def get_state():
    with _lock:
        return dict(_state)
