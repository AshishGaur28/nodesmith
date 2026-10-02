"""Stage 4 - semantic checks, then lowering to the language-neutral IR.

``lower`` checks identifiers, interface declarations, parameters, pipelines and callback
groups, reporting every error it finds (SPEC-04 section 3), and, if there were none,
assembles the IR described in SPEC-00 section 3. The IR is deliberately free of anything
specific to a target language or middleware.
"""

from ..diagnostics import BuildError, Report
from .callback_groups import StateAccess, assign_callback_groups
from .functions import UserFunction, function_entries, lower_functions
from .interfaces import InterfaceTable
from .language import RESERVED_WORDS, TARGET_KEYWORDS
from .parameters import check_constraints_on_defaults, lower_constraints, lower_parameters
from .pipelines import Endpoints, lower_pipeline

IR_VERSION = "1.1.0"
_ENDPOINT_KINDS = ("publishers", "subscribers", "services")
_FLOAT_TYPES = ("float32", "float64")


# ------------------------------------------------------------------------------ checks
def _check_identifiers(manifest: dict, report: Report) -> None:
    """No identifier is a reserved word or a keyword of a target language, and endpoint ids
    are unique (``ERR_SEM_104``)."""
    endpoints = [
        (e["id"], (kind, index, "id"))
        for kind in _ENDPOINT_KINDS
        for index, e in enumerate(manifest.get(kind, []))
    ]
    others = [
        (p["name"], ("pipelines", i, "name")) for i, p in enumerate(manifest.get("pipelines", []))
    ]
    others += [(n, ("parameters", n)) for n in manifest.get("parameters", {})]
    others += [(n, ("state_variables", n)) for n in manifest.get("state_variables", {})]
    for name, where in endpoints + others:
        with report.guard():
            if name in RESERVED_WORDS or name in TARGET_KEYWORDS:
                raise BuildError("ERR_SEM_104", f"{name!r} is a reserved word", where)
    seen: set[str] = set()
    for name, where in endpoints:
        with report.guard():
            if name in seen:
                raise BuildError(
                    "ERR_SEM_104", f"duplicate publisher/subscriber/service id {name!r}", where
                )
            seen.add(name)


def _warn_unused_declarations(manifest: dict, endpoints: Endpoints, report: Report) -> None:
    """Warn about ``[interfaces]`` entries no endpoint uses (``ERR_SEM_113``)."""
    used = {
        e["type"]
        for group in (endpoints.publishers, endpoints.subscribers, endpoints.services)
        for e in group.values()
    }
    for type_symbol in sorted(set(manifest.get("interfaces", {})) - used):
        report.warn(
            "ERR_SEM_113",
            f"interface declaration {type_symbol!r} is not used by any endpoint",
            ("interfaces", type_symbol),
        )


def _check_fault_injection(
    manifest: dict, extensions: dict, endpoints: Endpoints, report: Report
) -> None:
    """A fault-injection target must be a publisher, and ``bit_flip`` needs a fixed-size type."""
    declarations = manifest.get("interfaces", {})
    faults = extensions.get("simulation", {}).get("fault_injection", [])
    for index, fault in enumerate(faults):
        with report.guard(("simulation", "fault_injection", index, "target")):
            publisher = endpoints.publishers.get(fault["target"])
            if publisher is None:
                raise BuildError(
                    "ERR_SEM_105", f"fault_injection target {fault['target']!r} is not a publisher"
                )
            fixed_size = declarations.get(publisher["type"], {}).get("fixed_size", False)
            if fault["type"] == "bit_flip" and not fixed_size:
                raise BuildError(
                    "ERR_SIM_003", f"bit_flip needs {publisher['type']} declared fixed_size"
                )


def _lower_pipelines(
    manifest: dict,
    endpoints: Endpoints,
    interfaces: InterfaceTable,
    functions: dict[str, UserFunction],
    report: Report,
) -> tuple[list[dict], dict[str, StateAccess], bool]:
    """Lower every pipeline. Returns the DAGs, how each uses state, and whether any failed."""
    dags: list[dict] = []
    accesses: dict[str, StateAccess] = {}
    any_failed = False
    for index, pipeline in enumerate(manifest.get("pipelines", [])):
        errors_before = len(report.errors)
        with report.guard(("pipelines", index, "trigger", "source")):
            dag, access = lower_pipeline(
                manifest, pipeline, index, endpoints, interfaces, functions, report
            )
            dags.append(dag)
            accesses[pipeline["name"]] = access
        any_failed |= len(report.errors) > errors_before
    return dags, accesses, any_failed


def _check_trigger_coverage(manifest: dict, report: Report) -> None:
    """Each service is served by exactly one pipeline (``ERR_SEM_105``); a subscriber no pipeline
    uses is a warning (``ERR_SEM_110``)."""
    pipelines = manifest.get("pipelines", [])
    served = [p["trigger"]["source"] for p in pipelines if p["trigger"]["type"] == "service"]
    listened = {p["trigger"]["source"] for p in pipelines if p["trigger"]["type"] == "subscriber"}
    for index, service in enumerate(manifest.get("services", [])):
        with report.guard(("services", index)):
            if served.count(service["id"]) != 1:
                raise BuildError(
                    "ERR_SEM_105",
                    f"service {service['id']!r} must be served by exactly one pipeline",
                )
    for index, subscriber in enumerate(manifest.get("subscribers", [])):
        if subscriber["id"] not in listened:
            report.warn(
                "ERR_SEM_110",
                f"subscriber {subscriber['id']!r} triggers no pipeline",
                ("subscribers", index),
            )


def _executor(manifest: dict, extensions: dict, report: Report) -> tuple[str, int]:
    """The effective executor and thread count (``ERR_RT_002`` if they disagree)."""
    threads = manifest.get("concurrency", {}).get("threads", 1)
    declared = extensions.get("realtime", {}).get("executor", {}).get("type")
    executor = declared or ("MultiThreadedExecutor" if threads > 1 else "SingleThreadedExecutor")
    with report.guard(("concurrency", "threads")):
        if threads > 1 and executor != "MultiThreadedExecutor":
            raise BuildError(
                "ERR_RT_002", f"concurrency.threads > 1 needs MultiThreadedExecutor, not {executor}"
            )
    return executor, threads


# ------------------------------------------------------------------------------ the IR
def _channel(endpoint: dict) -> dict:
    """IR entry of a publisher or subscriber, with QoS defaults filled in."""
    qos = endpoint.get("qos", {})
    return {
        "identifier": endpoint["id"],
        "topic": endpoint["topic"],
        "type_symbol": endpoint["type"],
        "zero_copy": endpoint.get("zero_copy", False),
        "qos": {
            "history": qos.get("history", "keep_last"),
            "depth": qos.get("depth", 10),
            "reliability": qos.get("reliability", "reliable"),
            "durability": qos.get("durability", "volatile"),
        },
    }


def _interface_type(type_symbol: str, declaration: dict) -> dict:
    """IR entry of a declared message or service type."""
    if "/msg/" in type_symbol:
        return {
            "type_symbol": type_symbol,
            "kind": "msg",
            "fixed_size": declaration.get("fixed_size", False),
            "fields": dict(sorted(declaration.get("fields", {}).items())),
        }
    return {
        "type_symbol": type_symbol,
        "kind": "srv",
        "request": dict(sorted(declaration.get("request", {}).items())),
        "response": dict(sorted(declaration.get("response", {}).items())),
    }


def _state_buffers(manifest: dict) -> list[dict]:
    buffers = []
    for name, state in sorted(manifest.get("state_variables", {}).items()):
        initial = state["initial_value"]
        buffers.append(
            {
                "name": name,
                "canonical_type": state["type"],
                "initial_value": float(initial) if state["type"] in _FLOAT_TYPES else initial,
            }
        )
    return buffers


def _assemble_ir(
    manifest: dict,
    extensions: dict,
    endpoints: Endpoints,
    parts: dict,
) -> dict:
    """Put the lowered pieces together in the layout of SPEC-00 section 3."""
    node = manifest["node"]
    meta = {
        "name": node["name"],
        "namespace": node["namespace"],
        "version": node.get("version", "0.1.0"),
    }
    if "description" in node:
        meta["description"] = node["description"]
    ir = {
        "ir_version": IR_VERSION,
        "node_meta": meta,
        "interfaces": {
            "publishers": [_channel(e) for _, e in sorted(endpoints.publishers.items())],
            "subscribers": [_channel(e) for _, e in sorted(endpoints.subscribers.items())],
            "services": [
                {"identifier": e["id"], "service_name": e["name"], "type_symbol": e["type"]}
                for _, e in sorted(endpoints.services.items())
            ],
        },
        "interface_types": [
            _interface_type(symbol, declaration)
            for symbol, declaration in sorted(manifest.get("interfaces", {}).items())
        ],
        "parameters": parts["parameters"],
        "parameter_constraints": parts["constraints"],
        "state_buffers": _state_buffers(manifest),
        "execution_dags": sorted(parts["dags"], key=lambda d: d["id"]),
        "concurrency": {
            "executor": parts["executor"],
            "threads": parts["threads"],
            "callback_groups": [
                {"name": name, "type": type_, "explicit": explicit}
                for name, (type_, explicit) in sorted(parts["groups"].items())
            ],
        },
        "extensions": {name: extensions[name] for name in sorted(extensions)},
    }
    if parts["functions"]:
        ir["functions"] = parts["functions"]
    return ir


def lower(manifest: dict, extensions: dict, report: Report) -> dict | None:
    """Check the manifest semantically and lower it to the IR.

    Every error found is reported. Returns the IR, or ``None`` if there was any error.
    ``manifest`` has had its ``x-`` keys removed; ``extensions`` is
    ``schema.effective_extensions(manifest)``."""
    _check_identifiers(manifest, report)
    endpoints = Endpoints.of(manifest)
    interfaces = InterfaceTable(manifest.get("interfaces", {}))
    interfaces.validate(report)
    _warn_unused_declarations(manifest, endpoints, report)
    _check_fault_injection(manifest, extensions, endpoints, report)

    functions = lower_functions(manifest, report)
    parameters = lower_parameters(manifest, report)
    constraints = lower_constraints(manifest, report, functions)
    check_constraints_on_defaults(manifest, parameters, constraints, report)

    dags, accesses, any_failed = _lower_pipelines(
        manifest, endpoints, interfaces, functions, report
    )
    _check_trigger_coverage(manifest, report)

    # The callback-group check needs every pipeline's state access, so it only runs when all
    # pipelines were lowered cleanly (SPEC-04 section 3).
    assignment, groups = (
        ({}, {}) if any_failed else assign_callback_groups(manifest, accesses, report)
    )
    for dag in dags:
        dag["callback_group"] = assignment.get(dag["id"])
    executor, threads = _executor(manifest, extensions, report)

    if report.errors:
        return None
    parts = {
        "parameters": parameters,
        "constraints": constraints,
        "dags": dags,
        "groups": groups,
        "executor": executor,
        "threads": threads,
        "functions": function_entries(functions, manifest),
    }
    return _assemble_ir(manifest, extensions, endpoints, parts)
