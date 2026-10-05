"""Things worth telling the user after a package was generated, that are not errors.

``advisories`` returns plain sentences; the command line prints them as ``note: ...``. Nothing here
changes the generated code.
"""

from .package import PACKAGE_PLACEHOLDERS

_CLOCK_READERS = ("now_sec", "dt_sec", "rate_limit")


def advisories(ir: dict, package_info: dict[str, str] | None = None) -> list[str]:
    """Notes about the manifest and the package: placeholder metadata and clock mixing."""
    notes = []
    given = package_info or {}
    missing = [key for key in PACKAGE_PLACEHOLDERS if not given.get(key)]
    if missing:
        flags = ", ".join("--" + key.replace("_", "-") for key in missing)
        notes.append(
            f"package.xml still has placeholder values; set {flags} (needed to release it)"
        )
    notes += _clock_mixing(ir)
    return notes


def _clock_mixing(ir: dict) -> list[str]:
    """A state variable read or written by pipelines that read ``now_sec()`` on different clocks.

    ``now_sec()`` is the time on the trigger's clock, and ``steady`` for every trigger that is not
    a timer (SPEC-02 section 5), so values taken from different clocks cannot be compared."""
    clocks_by_state: dict[str, dict[str, str]] = {}
    for dag in ir["execution_dags"]:
        reads_time = any(
            node["op"] == "call" and node.get("attrs", {}).get("function") in _CLOCK_READERS
            for node in dag["nodes"]
        )
        if not reads_time:
            continue
        trigger = dag["trigger"]
        clock = trigger.get("clock", "steady") if trigger["kind"] == "timer" else "steady"
        touched = {write["state_name"] for write in dag["state_writes"]}
        touched |= {node["attrs"]["name"] for node in dag["nodes"] if node["op"] == "state"}
        for state in touched:
            clocks_by_state.setdefault(state, {})[dag["id"]] = clock
    return [
        f"state '{state}' is used by pipelines that read now_sec() on different clocks "
        f"({', '.join(f'{name}: {clock}' for name, clock in sorted(pipelines.items()))}); "
        "times from different clocks cannot be compared"
        for state, pipelines in sorted(clocks_by_state.items())
        if len(set(pipelines.values())) > 1
    ]
