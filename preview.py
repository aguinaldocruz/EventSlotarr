from .state import get_state


def preview():
    state = get_state()

    lines = [
        f"Startup: {state.get('startup_time')}",
        f"Last run: {state.get('last_run')}",
        f"Runs: {state.get('run_count')}",
        f"Changes: {state.get('change_count')}",
        "",
        "Current assignments:"
    ]

    assignments = state.get("current_assignments", {})

    if not assignments:
        lines.append("(none)")
    else:
        for slot, source in assignments.items():
            lines.append(f"{slot} <- {source}")

    errors = state.get("errors") or []
    if errors:
        lines.append("")
        lines.append("Recent errors:")
        for error in errors[-5:]:
            lines.append(f"- {error}")

    return "\n".join(lines)
