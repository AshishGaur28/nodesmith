"""Stage 3 - cross-block rules that a JSON Schema cannot express (SPEC-01 section 5.1).

Each rule looks at several parts of the manifest together (for example "an endpoint with
``zero_copy`` needs an enabled ``shared_memory`` block"). ``check`` runs every rule and
reports every violation, in rule order; the order matters because the first error decides
the process exit status (SPEC-04 section 4.2).
"""

import re
from dataclasses import dataclass

from ..diagnostics import BuildError, Cascade, Report

# Shared-memory backends each RMW can provide (SPEC-03 section 2.1).
RMW_BACKENDS = {"fastrtps": {"rmw_default"}}

# Extension blocks the Python target does not support in v1 (SPEC-01 section 5.1, rule 7).
PYTHON_UNSUPPORTED_BLOCKS = (
    "lifecycle",
    "shared_memory",
    "security",
    "telemetry",
    "realtime",
    "simulation",
)

# Size in bytes of each ROS primitive, for the loan pool check (SPEC-06 section 3.2).
FIELD_BYTES = {
    "bool": 1,
    "byte": 1,
    "char": 1,
    "int8": 1,
    "uint8": 1,
    "int16": 2,
    "uint16": 2,
    "int32": 4,
    "uint32": 4,
    "float32": 4,
    "int64": 8,
    "uint64": 8,
    "float64": 8,
    "time": 8,
    "duration": 8,
}

# System topics a node may only publish on with an explicit permission (SPEC-08 section 5).
RESTRICTED_TOPICS = {"/rosout", "/parameter_events", "/clock", "/ros_discovery_info"}

_ENCLAVE_SEGMENT = re.compile(r"[A-Za-z0-9_.-]+")


def _fail(code: str, message: str):
    raise BuildError(code, message)


@dataclass
class _Context:
    """What every rule needs: the manifest, its extension blocks with defaults filled in,
    the build mode, and where to report."""

    manifest: dict
    extensions: dict
    mode: str
    report: Report

    @property
    def node(self) -> dict:
        return self.manifest["node"]

    @property
    def pipelines(self) -> list:
        return self.manifest.get("pipelines", [])


def check(manifest: dict, extensions: dict, mode: str, report: Report) -> None:
    """Report every violated cross-block rule.

    ``manifest`` has had its ``x-`` keys removed; ``extensions`` is
    ``schema.effective_extensions(manifest)``; ``mode`` is ``debug`` or ``release``."""
    context = _Context(manifest, extensions, mode, report)
    _check_zero_copy_settings(context)  # rule 1
    _check_zero_copy_types(context)  # rule 1 (SHM_002, SHM_003)
    _check_callback_groups_need_realtime(context)  # rule 2
    _check_simulated_clock(context)  # rule 3
    _check_release_build(context)  # rule 4
    _check_security(context)  # rules 6 and SPEC-08 section 5
    _check_python_target(context)  # rule 7


# ------------------------------------------------------------------------------ rule 1
def _zero_copy_endpoints(context: _Context):
    """(endpoint, manifest path) for every publisher and subscriber with ``zero_copy``."""
    for kind in ("publishers", "subscribers"):
        for index, endpoint in enumerate(context.manifest.get(kind, [])):
            if endpoint.get("zero_copy"):
                yield endpoint, (kind, index)


def _check_zero_copy_settings(context: _Context) -> None:
    """Each ``zero_copy`` endpoint needs C++, an enabled ``shared_memory`` block, plain QoS,
    a pool within the limit, and a backend the chosen RMW supports (ERR_SHM_001)."""
    shared_memory = context.extensions.get("shared_memory")
    rmw = context.node.get("rmw", "fastrtps")
    for endpoint, where in _zero_copy_endpoints(context):
        name = endpoint["id"]
        with context.report.guard((*where, "zero_copy")):
            qos = endpoint.get("qos", {})
            if context.node["target_language"] != "cpp":
                _fail("ERR_SHM_001", f"{name}: zero_copy needs target_language = cpp")
            if not (shared_memory and shared_memory["enabled"]):
                _fail("ERR_SHM_001", f"{name}: zero_copy needs an enabled shared_memory block")
            keep_last = qos.get("history", "keep_last") == "keep_last"
            volatile = qos.get("durability", "volatile") == "volatile"
            if not (keep_last and volatile):
                _fail("ERR_SHM_001", f"{name}: zero_copy needs history keep_last and volatile")
            limit = shared_memory.get("max_loaned_messages")
            if limit is not None and endpoint.get("loan_pool_capacity", 1) > limit:
                _fail("ERR_SHM_001", f"{name}: loan_pool_capacity exceeds max_loaned_messages")
            backend = shared_memory["middleware_backend"]
            if backend not in RMW_BACKENDS[rmw]:
                _fail(
                    "ERR_SHM_001", f"middleware_backend {backend!r} is not supported by rmw {rmw!r}"
                )


def _check_zero_copy_types(context: _Context) -> None:
    """A loaned type must be declared ``fixed_size`` (ERR_SHM_002) and its pool must fit in
    the segment (ERR_SHM_003)."""
    shared_memory = context.extensions.get("shared_memory")
    declarations = context.manifest.get("interfaces", {})
    for endpoint, where in _zero_copy_endpoints(context):
        with context.report.guard((*where, "type")):
            if not (shared_memory and shared_memory["enabled"]):
                raise Cascade  # already reported by the previous check
            declaration = declarations.get(endpoint["type"], {})
            if not declaration.get("fixed_size", False):
                _fail(
                    "ERR_SHM_002",
                    f"{endpoint['id']}: {endpoint['type']} is not declared fixed_size",
                )
            message_bytes = sum(FIELD_BYTES[t] for t in declaration.get("fields", {}).values())
            capacity = endpoint.get("loan_pool_capacity", 1)
            segment = shared_memory.get("segment_size_bytes")
            if segment is not None and capacity * message_bytes > segment:
                pool = f"{capacity} x {message_bytes} bytes"
                _fail(
                    "ERR_SHM_003",
                    f"{endpoint['id']}: loan pool ({pool}) exceeds segment_size_bytes",
                )


# ------------------------------------------------------------------------------ rule 2
def _check_callback_groups_need_realtime(context: _Context) -> None:
    """A pipeline may name a ``callback_group`` only when ``realtime`` is enabled (ERR_RT_002)."""
    named = [i for i, p in enumerate(context.pipelines) if "callback_group" in p]
    realtime = context.extensions.get("realtime")
    with context.report.guard(("pipelines", named[0] if named else 0, "callback_group")):
        if named and not (realtime and realtime["enabled"]):
            _fail(
                "ERR_RT_002",
                "a pipeline names a callback_group but the realtime block is absent or disabled",
            )


# ------------------------------------------------------------------------------ rule 3
def _check_simulated_clock(context: _Context) -> None:
    """With ``use_sim_time``, every timer must use the ROS clock (ERR_SIM_002)."""
    simulation = context.extensions.get("simulation")
    wrong_clock = [
        i
        for i, p in enumerate(context.pipelines)
        if p["trigger"]["type"] == "timer" and p["trigger"].get("clock", "steady") != "ros"
    ]
    with context.report.guard(("pipelines", wrong_clock[0] if wrong_clock else 0, "trigger")):
        uses_sim_time = simulation and simulation["enabled"] and simulation["use_sim_time"]
        if uses_sim_time and wrong_clock:
            _fail("ERR_SIM_002", 'use_sim_time requires every timer trigger to use clock = "ros"')


# ------------------------------------------------------------------------------ rule 4
def _check_release_build(context: _Context) -> None:
    """A release build rejects simulation unless it is explicitly allowed (ERR_SIM_001)."""
    simulation = context.extensions.get("simulation")
    if context.mode != "release" or not (simulation and simulation["enabled"]):
        return
    with context.report.guard(("simulation",)):
        if not simulation["enabled_in_production"]:
            _fail(
                "ERR_SIM_001", "simulation.enabled in a release build needs enabled_in_production"
            )
    for index, fault in enumerate(simulation.get("fault_injection", [])):
        with context.report.guard(("simulation", "fault_injection", index)):
            if not fault["enabled_in_production"]:
                _fail(
                    "ERR_SIM_001",
                    f"fault_injection[{index}] in a release build needs enabled_in_production",
                )


# ------------------------------------------------------------------------------ rule 6
def _check_security(context: _Context) -> None:
    """Enclave path (ERR_SEC_302), permission conflicts (ERR_SEC_304) and publishing on
    restricted topics (ERR_SEC_303); SPEC-08 section 5."""
    security = context.extensions.get("security")
    if not (security and security["enabled"]):
        return
    with context.report.guard(("security", "enclave")):
        for segment in security["enclave"][1:].split("/"):
            if segment in (".", "..") or not _ENCLAVE_SEGMENT.fullmatch(segment):
                _fail("ERR_SEC_302", f"invalid enclave path segment {segment!r}")
    permissions = security.get("permissions", {})
    _check_permission_conflicts(context, permissions)
    _check_restricted_topics(context, permissions)


def _check_permission_conflicts(context: _Context, permissions: dict) -> None:
    """The same topic must not be both allowed and denied, and a deny must remove a grant
    that exists (ERR_SEC_304)."""
    derived = {
        "publish": {e["topic"] for e in context.manifest.get("publishers", [])},
        "subscribe": {e["topic"] for e in context.manifest.get("subscribers", [])},
    }
    for direction, entries in permissions.items():
        decided: dict[str, bool] = {}
        for index, entry in enumerate(entries):
            topic, allow = entry["topic"], entry["allow"]
            with context.report.guard(("security", "permissions", direction, index)):
                if decided.get(topic, allow) != allow:
                    _fail("ERR_SEC_304", f"{topic} is both allowed and denied for {direction}")
                decided[topic] = allow
                if not allow and topic not in derived[direction]:
                    _fail("ERR_SEC_304", f"deny of {topic} removes no grant")


def _check_restricted_topics(context: _Context, permissions: dict) -> None:
    """Publishing on a system topic needs an explicit ``allow`` permission (ERR_SEC_303)."""
    granted = {p["topic"] for p in permissions.get("publish", []) if p["allow"]}
    for index, endpoint in enumerate(context.manifest.get("publishers", [])):
        topic = endpoint["topic"]
        restricted = topic in RESTRICTED_TOPICS or topic.lstrip("/").startswith("_")
        with context.report.guard(("publishers", index, "topic")):
            if restricted and topic not in granted:
                _fail(
                    "ERR_SEC_303",
                    f"{endpoint['id']}: publishing to {topic} needs an explicit allow permission",
                )


# ------------------------------------------------------------------------------ rule 7
def _check_python_target(context: _Context) -> None:
    """The Python target supports only the core blocks in v1 (ERR_SEM_114)."""
    if context.node["target_language"] != "python":
        return
    for block in PYTHON_UNSUPPORTED_BLOCKS:
        where = (block,) if block in context.manifest else ("node", "target_language")
        with context.report.guard(where):
            if block in context.extensions and context.extensions[block]["enabled"]:
                _fail(
                    "ERR_SEM_114",
                    f"the {block} block is not supported for target_language = python in v1",
                )
