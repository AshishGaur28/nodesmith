"""Lowers one pipeline of the manifest to an execution DAG (SPEC-00 section 3)."""

from dataclasses import dataclass

from ..diagnostics import BuildError, Report
from .builder import ExpressionBuilder
from .callback_groups import StateAccess
from .expr import parse_expression, parse_statement
from .functions import UserFunction
from .interfaces import InterfaceTable


@dataclass
class Endpoints:
    """The manifest's publishers, subscribers and services, by id."""

    publishers: dict[str, dict]
    subscribers: dict[str, dict]
    services: dict[str, dict]

    @classmethod
    def of(cls, manifest: dict) -> "Endpoints":
        """The endpoints of a manifest."""

        def by_id(kind: str) -> dict[str, dict]:
            return {e["id"]: e for e in manifest.get(kind, [])}

        return cls(by_id("publishers"), by_id("subscribers"), by_id("services"))


def _input_fields(trigger: dict, endpoints: Endpoints, interfaces: InterfaceTable) -> dict:
    """Fields of the message or request that triggers the pipeline (none for a timer or startup)."""
    if trigger["type"] == "subscriber":
        subscriber = endpoints.subscribers.get(trigger["source"])
        if subscriber is None:
            raise BuildError(
                "ERR_SEM_105", f"trigger source {trigger['source']!r} is not a subscriber"
            )
        return interfaces.message_fields(subscriber["type"])
    if trigger["type"] == "service":
        service = endpoints.services.get(trigger["source"])
        if service is None:
            raise BuildError(
                "ERR_SEM_105", f"trigger source {trigger['source']!r} is not a service"
            )
        return interfaces.service_fields(service["type"], "request")
    return {}


def _output_field_type(
    target: str, pipeline: dict, endpoints: Endpoints, interfaces: InterfaceTable
) -> str:
    """Canonical type of the field an ``output_mapping`` key names (``publisher.path`` or
    ``res.path``)."""
    owner, _, path = target.partition(".")
    if owner == "res":
        if pipeline["trigger"]["type"] != "service":
            raise BuildError("ERR_SEM_108", "`res.` is only available in a service pipeline")
        service = endpoints.services[pipeline["trigger"]["source"]]
        fields = interfaces.service_fields(service["type"], "response")
    else:
        if owner not in endpoints.publishers:
            raise BuildError("ERR_SEM_105", f"output target {owner!r} is not a publisher")
        fields = interfaces.message_fields(endpoints.publishers[owner]["type"])
    node = fields
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise BuildError("ERR_SEM_107", f"output field {target!r} is not declared")
        node = node[part]
    if isinstance(node, dict):
        raise BuildError("ERR_SEM_102", f"output target {target!r} is a nested message")
    return node


def lower_pipeline(
    manifest: dict,
    pipeline: dict,
    index: int,
    endpoints: Endpoints,
    interfaces: InterfaceTable,
    functions: dict[str, UserFunction],
    report: Report,
) -> tuple[dict, StateAccess]:
    """Return the pipeline's DAG and how it uses state.

    An error in one statement or output target is reported and the rest is still checked;
    an error in the trigger abandons the pipeline."""
    trigger = pipeline["trigger"]
    builder = ExpressionBuilder(
        manifest, trigger["type"], _input_fields(trigger, endpoints, interfaces), functions
    )
    for position, text in enumerate(pipeline.get("expressions", [])):
        with report.guard(("pipelines", index, "expressions", position)):
            builder.run_statement(parse_statement(text))

    assignments = []
    for target in sorted(pipeline.get("output_mapping", {})):
        with report.guard(("pipelines", index, "output_mapping", target)):
            field_type = _output_field_type(target, pipeline, endpoints, interfaces)
            expression = parse_expression(pipeline["output_mapping"][target])
            value = builder.widen(builder.lower(expression, field_type), field_type)
            assignments.append({"target_field_path": target, "source_node_id": value.node_id})

    dag = {
        "id": pipeline["name"],
        "trigger": _trigger_description(trigger),
        "nodes": builder.nodes,
        "state_writes": [
            {"state_name": name, "source_node_id": node_id}
            for name, node_id in sorted(builder.staged.items())
        ],
        "assignments": assignments,
    }
    access = StateAccess(
        frozenset(builder.state_reads), frozenset(builder.state_writes), builder.uses_dt
    )
    return dag, access


def _trigger_description(trigger: dict) -> dict:
    """The trigger as the IR records it, with the timer clock defaulted."""
    description = {"kind": trigger["type"]}
    if trigger["type"] in ("subscriber", "service"):
        description["source_id"] = trigger["source"]
    if trigger["type"] == "timer":
        description.update(period_ms=trigger["period_ms"], clock=trigger.get("clock", "steady"))
    return description
