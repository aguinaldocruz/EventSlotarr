import threading

from .persistence import load_json, save_json

_lock = threading.Lock()

_event_to_slot = load_json("sticky.json", {})
_slot_to_event = {slot: event for event, slot in _event_to_slot.items()}


def _persist():
    save_json("sticky.json", _event_to_slot)


def get_slot(event_name):
    with _lock:
        return _event_to_slot.get(event_name)


def assign_slot(event_name, slot_name):
    with _lock:
        _event_to_slot[event_name] = slot_name
        _slot_to_event[slot_name] = event_name
        _persist()


def clear_slot(slot_name):
    with _lock:
        event_name = _slot_to_event.pop(slot_name, None)

        if event_name:
            _event_to_slot.pop(event_name, None)

        _persist()


def current_mapping():
    with _lock:
        return {
            "event_to_slot": dict(_event_to_slot),
            "slot_to_event": dict(_slot_to_event)
        }
