"""Parameters, their validation and cross-parameter constraints (SPEC-01 4.3, SPEC-07).

Each parameter's default is checked against its own ``validation`` block, and each
``parameter_constraints`` expression is lowered and then evaluated on the defaults, so a
manifest whose defaults break its own rules is rejected at generation time (``ERR_SEM_106``).
"""

from ..diagnostics import BuildError, Report
from .builder import ExpressionBuilder
from .constants import Fault, evaluate
from .expr import parse_expression
from .functions import UserFunction

_FLOAT_TYPES = ("float32", "float64")
_NUMBER_TYPES = ("int32", "int64", "float32", "float64")
_STEP_TOLERANCE = 1e-9


def _is_number(value) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _is_integer(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _default_has_type(value, type_: str) -> bool:
    """Whether a default's value is of the declared parameter type."""
    checks = {
        "bool": isinstance(value, bool),
        "string": isinstance(value, str),
        "int32": _is_integer(value),
        "int64": _is_integer(value),
        "float32": _is_number(value),
        "float64": _is_number(value),
    }
    return checks.get(type_, isinstance(value, list))  # any other type is an array


def _check_validation(name: str, type_: str, default, rules: dict, where: tuple) -> None:
    """Check a default against the ``validation`` block of its parameter (SPEC-07 section 3.2)."""

    def violated(message: str):
        raise BuildError("ERR_SEM_106", message, where)

    if ("min" in rules and default < rules["min"]) or ("max" in rules and default > rules["max"]):
        violated(f"default of {name!r} is out of bounds")
    if "step" in rules and type_ in _NUMBER_TYPES:
        steps = (default - rules.get("min", 0)) / rules["step"]  # aligned to min (0 if absent)
        if abs(steps - round(steps)) > _STEP_TOLERANCE:
            violated(f"default of {name!r} is not a multiple of step {rules['step']}")
    if "one_of" in rules and default not in rules["one_of"]:
        violated(f"default of {name!r} is not one of {rules['one_of']}")
    if (
        "fixed_length" in rules
        and isinstance(default, list)
        and len(default) != rules["fixed_length"]
    ):
        violated(f"default of {name!r} has length {len(default)}, not {rules['fixed_length']}")


def lower_parameters(manifest: dict, report: Report) -> list[dict]:
    """The IR entries of the parameters, sorted by name. A parameter whose default is wrong
    is reported and left out."""
    lowered = []
    for name, parameter in sorted(manifest.get("parameters", {}).items()):
        where = ("parameters", name, "default")
        with report.guard():
            type_, default = parameter["type"], parameter["default"]
            if not _default_has_type(default, type_):
                raise BuildError(
                    "ERR_SEM_102", f"default of parameter {name!r} is not a {type_}", where
                )
            if type_ in _FLOAT_TYPES:
                default = float(default)
            rules = parameter.get("validation", {})
            _check_validation(name, type_, default, rules, where)
            entry = {
                "name": name,
                "canonical_type": type_,
                "default_value": default,
                "read_only": parameter.get("read_only", False),
            }
            if "description" in parameter:
                entry["description"] = parameter["description"]
            if rules:
                entry["validation"] = rules
            lowered.append(entry)
    return lowered


def lower_constraints(
    manifest: dict, report: Report, functions: dict[str, UserFunction] | None = None
) -> list[dict]:
    """The IR entries of the ``parameter_constraints``: each is a boolean expression over
    ``param.`` references only."""
    lowered = []
    for index, constraint in enumerate(manifest.get("parameter_constraints", [])):
        where = ("parameter_constraints", index, "expression")
        with report.guard(where):
            builder = ExpressionBuilder(manifest, "startup", {}, functions, allow_user_calls=False)
            root = builder.lower(parse_expression(constraint["expression"]))
            if root.type != "bool":
                raise BuildError(
                    "ERR_SEM_102", "a parameter constraint must be a bool expression", where
                )
            if any(node["op"] in ("state", "input") for node in builder.nodes):
                raise BuildError(
                    "ERR_SEM_108", "a parameter constraint may reference only param.", where
                )
            entry = {"root_node_id": root.node_id, "nodes": builder.nodes}
            if "message" in constraint:
                entry["message"] = constraint["message"]
            lowered.append(entry)
    return lowered


def check_constraints_on_defaults(
    manifest: dict, parameters: list[dict], constraints: list[dict], report: Report
) -> None:
    """Every constraint must hold on the parameters' defaults (SPEC-07 section 5, rule 3)."""
    sources = manifest.get("parameter_constraints", [])
    if len(parameters) != len(manifest.get("parameters", {})) or len(constraints) != len(sources):
        return  # an earlier error already explains the gap
    defaults = {p["name"]: p["default_value"] for p in parameters}
    for index, (constraint, source) in enumerate(zip(constraints, sources, strict=True)):
        text = source["expression"]
        with report.guard(("parameter_constraints", index, "expression")):
            try:
                holds = evaluate(constraint, defaults)
            except Fault as fault:
                raise BuildError(
                    "ERR_SEM_106", f"constraint {text!r} faults on the defaults: {fault}"
                ) from fault
            if not holds:
                raise BuildError(
                    "ERR_SEM_106",
                    source.get("message") or f"the defaults violate constraint {text!r}",
                )
