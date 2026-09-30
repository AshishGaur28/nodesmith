"""Stage 3: cross-block rules that JSON Schema cannot express (SPEC-01 §5.1)."""
import re

from .diagnostics import BuildError, Cascade

RMW_BACKENDS = {"fastrtps": {"rmw_default"}}          # SPEC-03 §2.1
PYTHON_UNSUPPORTED = ("lifecycle", "shared_memory", "security", "telemetry", "realtime", "simulation")   # v1
FIELD_BYTES = {"bool": 1, "byte": 1, "char": 1, "int8": 1, "uint8": 1, "int16": 2, "uint16": 2, "int32": 4, "uint32": 4,
               "float32": 4, "int64": 8, "uint64": 8, "float64": 8, "time": 8, "duration": 8}
RESTRICTED_TOPICS = {"/rosout", "/parameter_events", "/clock", "/ros_discovery_info"}     # SPEC-08 §5


def _fail(code, msg): raise BuildError(code, msg)


def check(m: dict, ext: dict, mode: str, report) -> None:
    """Every violated rule is reported, in SPEC-01 §5.1 rule order. `m` has had `x-` keys removed; `ext` is `schema.effective_extensions(m)`."""
    node, decl = m["node"], m.get("interfaces", {})
    endpoints = [e for k in ("publishers", "subscribers") for e in m.get(k, [])]
    zero_copy = [e for e in endpoints if e.get("zero_copy")]
    shm = ext.get("shared_memory")
    rmw = node.get("rmw", "fastrtps")
    for e in zero_copy:                                                               # rule 1
        with report.guard():
            q = e.get("qos", {})
            if node["target_language"] != "cpp": _fail("ERR_SHM_001", f"{e['id']}: zero_copy needs target_language = cpp")
            if not (shm and shm["enabled"]): _fail("ERR_SHM_001", f"{e['id']}: zero_copy needs an enabled shared_memory block")
            if q.get("history", "keep_last") != "keep_last" or q.get("durability", "volatile") != "volatile":
                _fail("ERR_SHM_001", f"{e['id']}: zero_copy needs history = keep_last and durability = volatile")
            if "max_loaned_messages" in shm and e.get("loan_pool_capacity", 1) > shm["max_loaned_messages"]:
                _fail("ERR_SHM_001", f"{e['id']}: loan_pool_capacity exceeds shared_memory.max_loaned_messages")
            if shm["middleware_backend"] not in RMW_BACKENDS[rmw]:
                _fail("ERR_SHM_001", f"middleware_backend {shm['middleware_backend']!r} is not supported by rmw {rmw!r}")
    for e in zero_copy:
        with report.guard():
            if not (shm and shm["enabled"]): raise Cascade()          # already reported above
            d = decl.get(e["type"], {})
            if not d.get("fixed_size", False): _fail("ERR_SHM_002", f"{e['id']}: {e['type']} is not declared fixed_size")
            size = sum(FIELD_BYTES[t] for t in d.get("fields", {}).values())
            if "segment_size_bytes" in shm and e.get("loan_pool_capacity", 1) * size > shm["segment_size_bytes"]:
                _fail("ERR_SHM_003", f"{e['id']}: loan pool ({e.get('loan_pool_capacity', 1)} x {size} bytes) exceeds segment_size_bytes")
    rt = ext.get("realtime")
    with report.guard():                                                               # rule 2
        if any("callback_group" in p for p in m.get("pipelines", [])) and not (rt and rt["enabled"]):
            _fail("ERR_RT_002", "a pipeline names a callback_group but the realtime block is absent or disabled")
    sim = ext.get("simulation")
    with report.guard():                                                               # rule 3
        timers = [p for p in m.get("pipelines", []) if p["trigger"]["type"] == "timer"]
        if sim and sim["enabled"] and sim["use_sim_time"] and any(p["trigger"].get("clock", "steady") != "ros" for p in timers):
            _fail("ERR_SIM_002", "use_sim_time requires every timer trigger to use clock = \"ros\"")
    if mode == "release" and sim and sim["enabled"]:                                   # rule 4
        with report.guard():
            if not sim["enabled_in_production"]: _fail("ERR_SIM_001", "simulation.enabled in a release build needs enabled_in_production = true")
        for i, f in enumerate(sim.get("fault_injection", [])):
            with report.guard():
                if not f["enabled_in_production"]:
                    _fail("ERR_SIM_001", f"fault_injection[{i}] in a release build needs enabled_in_production = true")
    sec = ext.get("security")
    if sec and sec["enabled"]:                                                         # rule 6, SPEC-08 §5
        with report.guard():
            for seg in sec["enclave"][1:].split("/"):
                if seg in (".", "..") or not re.fullmatch(r"[A-Za-z0-9_.-]+", seg): _fail("ERR_SEC_302", f"invalid enclave path segment {seg!r}")
        perms = sec.get("permissions", {})
        derived = {"publish": {e["topic"] for e in m.get("publishers", [])}, "subscribe": {e["topic"] for e in m.get("subscribers", [])}}
        for direction, entries in perms.items():
            seen = {}
            for p in entries:
                with report.guard():
                    if seen.get(p["topic"], p["allow"]) != p["allow"]: _fail("ERR_SEC_304", f"{p['topic']} is both allowed and denied for {direction}")
                    seen[p["topic"]] = p["allow"]
                    if not p["allow"] and p["topic"] not in derived[direction]: _fail("ERR_SEC_304", f"deny of {p['topic']} removes no grant")
        granted = {p["topic"] for p in perms.get("publish", []) if p["allow"]}
        for e in m.get("publishers", []):
            with report.guard():
                if (e["topic"] in RESTRICTED_TOPICS or e["topic"].lstrip("/").startswith("_")) and e["topic"] not in granted:
                    _fail("ERR_SEC_303", f"{e['id']}: publishing to {e['topic']} needs an explicit allow permission")
    if node["target_language"] == "python":                                            # rule 7
        for k in PYTHON_UNSUPPORTED:
            with report.guard():
                if k in ext and ext[k]["enabled"]: _fail("ERR_SEM_114", f"the {k} block is not supported for target_language = python in v1")
