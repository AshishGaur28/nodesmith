"""C++ for parameters: their validation, declaration and update (SPEC-07).

Three pieces are generated from the IR's parameters:

* ``validation_checks`` - the body of ``validate()``, used for the initial values and for
  every dynamic update, so both obey the same rules;
* ``declaration_lines`` - ``declare_parameters()``: descriptors, defaults and overrides;
* ``update_lines`` - the loop in ``on_set_parameters()`` that copies the new values in.
"""

import json
import math

from . import names
from .dag import DagEmitter
from .errors import Unsupported
from .layout import assign, call
from .literals import cpp_string, literal, value_literal
from .naming import cpp_type, member

# Canonical parameter type -> the C++ type ROS stores it as, and the accessor that reads it.
ROS_PARAMETER_TYPE = {
    "bool": "bool",
    "int32": "std::int64_t",
    "int64": "std::int64_t",
    "float32": "double",
    "float64": "double",
    "string": "std::string",
    "bool[]": "std::vector<bool>",
    "int64[]": "std::vector<std::int64_t>",
    "float64[]": "std::vector<double>",
    "string[]": "std::vector<std::string>",
    "bytes": "std::vector<std::uint8_t>",
}
_ROS_PARAMETER_ACCESSOR = {
    "bool": "as_bool",
    "int32": "as_int",
    "int64": "as_int",
    "float32": "as_double",
    "float64": "as_double",
    "string": "as_string",
    "bool[]": "as_bool_array",
    "int64[]": "as_integer_array",
    "float64[]": "as_double_array",
    "string[]": "as_string_array",
    "bytes": "as_byte_array",
}
_FLOAT_TYPES = ("float32", "float64")
_INT64_LIMITS = (
    "std::numeric_limits<std::int64_t>::min()",
    "std::numeric_limits<std::int64_t>::max()",
)


def check_parameter_types(parameters: list[dict]) -> None:
    """Refuse a parameter type the generator cannot declare as a ROS parameter yet."""
    for parameter in parameters:
        if parameter["canonical_type"] not in ROS_PARAMETER_TYPE:
            raise Unsupported(f"parameter type {parameter['canonical_type']} is not supported yet")


def _is_array(type_: str) -> bool:
    return type_.endswith("[]") or type_ == "bytes"


# ------------------------------------------------------------------------------ validation
def _bound(type_: str, value) -> str:
    return literal(value, "float64" if type_ in _FLOAT_TYPES else "int64")


def _reject(text: str) -> str:
    return f"return std::string({cpp_string(text)});"


def _guard(condition: str, text: str) -> str:
    """``if (condition) { return reason; }`` over three lines."""
    return f"if ({condition}) {{\n  {_reject(text)}\n}}"


def validation_checks(parameter: dict) -> list[str]:
    """The C++ statements of ``validate()`` for one parameter; each returns the reason on failure
    (SPEC-07 section 3.1, step 2). Every statement is a few lines, with the first at indent 0."""
    name, type_ = parameter["name"], parameter["canonical_type"]
    rules = parameter.get("validation", {})
    value = f"p.{member(name)}"
    quoted = repr(name)
    checks = []
    if "min" in rules or "max" in rules:
        conditions = []
        if "min" in rules:
            conditions.append(f"{value} >= {_bound(type_, rules['min'])}")
        if "max" in rules:
            conditions.append(f"{value} <= {_bound(type_, rules['max'])}")
        low, high = rules.get("min", "-inf"), rules.get("max", "inf")
        message = f"{quoted} out of range [{low}, {high}]"
        checks.append(_guard(f"!({' && '.join(conditions)})", message))
    if "step" in rules:
        base, step = float(rules.get("min", 0)), float(rules["step"])
        misaligned = (
            f"std::fabs(std::remainder(static_cast<double>({value}) - {base!r}, {step!r})) > 1e-9"
        )
        message = f"{quoted} is not a multiple of step {rules['step']}"
        checks.append(_guard(misaligned, message))
    if "one_of" in rules:
        options = [
            f"{value} == {literal(x, 'string' if isinstance(x, str) else 'int64')}"
            for x in rules["one_of"]
        ]
        message = f"{quoted} must be one of " + ", ".join(map(str, rules["one_of"]))
        checks.append(_guard("!(" + " ||\n      ".join(options) + ")", message))
    if "fixed_length" in rules:
        message = f"{quoted} must have exactly {rules['fixed_length']} elements"
        checks.append(_guard(f"{value}.size() != {rules['fixed_length']}", message))
    if "regex" in rules:
        checks.append(_regex_check(value, rules["regex"], quoted))
    return checks


def _regex_check(value: str, pattern: str, quoted: str) -> str:
    return "\n".join(
        [
            "try {",
            f"  static const std::regex re({cpp_string(pattern)});",
            f"  if (!std::regex_match({value}, re)) {{",
            f"    {_reject(f'{quoted} does not match its regex')}",
            "  }",
            "} catch (const std::regex_error &) {",
            f"  {_reject(f'{quoted} has an invalid regex')}",
            "}",
        ]
    )


def constraint_functions(ir: dict) -> list[dict]:
    """Template context for each ``parameter_constraints`` entry: the body of its function and
    the messages ``validate()`` returns when it faults or fails."""
    functions = []
    for index, constraint in enumerate(ir["parameter_constraints"]):
        dag = {
            "id": f"constraint_{index}",
            "trigger": {"kind": "startup"},
            "nodes": constraint["nodes"],
            "state_writes": [],
            "assignments": [],
        }
        emitter = DagEmitter(ir, dag)
        root = constraint["root_node_id"]
        labelled = {node["id"] for node in dag["nodes"] if "label" in node}
        emitter.materialized = labelled | {root}
        lines = [
            assign(
                f"const {cpp_type(emitter.nodes[nid]['type_symbol'])} {emitter.local_name(nid)}",
                emitter.expression(nid),
                "  ",
            )
            for nid in (node["id"] for node in dag["nodes"])
            if nid in emitter.materialized
        ]
        lines += ["", f"  return {emitter.local_name(root)};"]
        default_message = f"parameter_constraints[{index}] is violated"
        functions.append(
            {
                "body": "\n".join(lines),
                "message": cpp_string(constraint.get("message") or default_message),
                "fault_message": cpp_string(
                    f"parameter_constraints[{index}] faulted on the new values"
                ),
            }
        )
    return functions


# ------------------------------------------------------------------------------ declaration
def _descriptor_lines(parameter: dict, descriptor: str) -> list[str]:
    """Statements filling a ``ParameterDescriptor``: description, read-only flag and range."""
    rules = parameter.get("validation", {})
    type_ = parameter["canonical_type"]
    lines = [f"rcl_interfaces::msg::ParameterDescriptor {descriptor};"]
    if "description" in parameter:
        lines.append(f"{descriptor}.description = {cpp_string(parameter['description'])};")
    if parameter.get("read_only"):
        lines.append(f"{descriptor}.read_only = true;")
    has_range = "min" in rules or "max" in rules
    if type_ in ("int32", "int64") and has_range:
        low = rules["min"] if "min" in rules else _INT64_LIMITS[0]
        high = rules["max"] if "max" in rules else _INT64_LIMITS[1]
        step = int(rules["step"]) if "step" in rules else 0
        lines += [
            f"{descriptor}.integer_range.resize(1);",
            f"{descriptor}.integer_range[0].from_value = {low};",
            f"{descriptor}.integer_range[0].to_value = {high};",
            f"{descriptor}.integer_range[0].step = {step};",
        ]
    if type_ in _FLOAT_TYPES and has_range:
        low = (
            repr(float(rules["min"])) if "min" in rules else "std::numeric_limits<double>::lowest()"
        )
        high = repr(float(rules["max"])) if "max" in rules else "std::numeric_limits<double>::max()"
        step = repr(float(rules["step"])) if "step" in rules else "0.0"
        lines += [
            f"{descriptor}.floating_point_range.resize(1);",
            f"{descriptor}.floating_point_range[0].from_value = {low};",
            f"{descriptor}.floating_point_range[0].to_value = {high};",
            f"{descriptor}.floating_point_range[0].step = {step};",
        ]
    return lines


def declaration_lines(parameters: list[dict]) -> list[str]:
    """The body of the parameters adapter's constructor up to the declarations: one descriptor and
    one declaration per parameter, collected into ``Params initial`` (``node`` is the ROS node).
    Each statement may span several lines; a blank line separates the parameters."""
    lines = [
        "// The effective values include launch-file and command-line overrides.",
        "Params initial;",
    ]
    for parameter in parameters:
        name, type_ = parameter["name"], parameter["canonical_type"]
        target, ros_type = member(name), ROS_PARAMETER_TYPE[type_]
        descriptor = f"d_{target}"
        default = value_literal(type_, parameter["default_value"])
        if _is_array(type_):
            initial = f"{ros_type}({default})"
        else:
            initial = f"static_cast<{ros_type}>({default})"
        lines += ["", *_descriptor_lines(parameter, descriptor)]
        lines += _store_declared(
            target, type_, ros_type, [names.parameter(name), initial, descriptor], name
        )
    return lines


def _store_declared(
    target: str, type_: str, ros_type: str, arguments: list[str], name: str
) -> list[str]:
    """Statements declaring one parameter and storing its value in ``initial``, converting ROS's
    64-bit integers and doubles to the canonical type."""
    head = f"node.declare_parameter<{ros_type}>("
    if type_ == "int32":  # ROS parameters are 64-bit: refuse a value outside int32
        why = cpp_string(f"{name!r} is outside the int32 range")
        return [
            "{",
            call(f"const {ros_type} declared = {head}", arguments, ");", "  "),
            "  bool out_of_range = false;",
            f"  initial.{target} = r2d::to_int<std::int32_t>(declared, out_of_range);",
            "  if (out_of_range) {",
            f"    throw rclcpp::exceptions::InvalidParameterValueException({why});",
            "  }",
            "}",
        ]
    if type_ == "float32":
        return [
            "{",
            call(f"const {ros_type} declared = {head}", arguments, ");", "  "),
            f"  initial.{target} = static_cast<float>(declared);",
            "}",
        ]
    return [call(f"initial.{target} = {head}", arguments, ");")]


# ------------------------------------------------------------------------------ update
def update_lines(parameters: list[dict]) -> list[str]:
    """The loop of the parameters adapter's ``on_set`` that copies each updated value into ``next``
    (``reject`` is a lambda defined in the template)."""
    lines = [
        "for (const auto & p : updates) {",
        "  [[maybe_unused]] const std::string & name = p.get_name();",
    ]
    for position, parameter in enumerate(parameters):
        name, type_, target = (
            parameter["name"],
            parameter["canonical_type"],
            member(parameter["name"]),
        )
        keyword = "if" if position == 0 else "} else if"
        lines.append(f"  {keyword} (name == {names.parameter(name)}) {{")
        if parameter.get("read_only"):
            why = cpp_string(f"{name!r} is read-only and cannot be updated at runtime")
            lines.append(f"    return reject({why});")
        elif type_ == "int32":
            why = cpp_string(f"{name!r} is outside the int32 range")
            lines += [
                "    bool f = false;",
                f"    next.{target} = r2d::to_int<std::int32_t>(p.as_int(), f);",
                f"    if (f) return reject({why});",
            ]
        elif type_ == "float32":
            lines.append(f"    next.{target} = static_cast<float>(p.as_double());")
        else:
            lines.append(f"    next.{target} = p.{_ROS_PARAMETER_ACCESSOR[type_]}();")
    if parameters:
        lines.append("  }")
    lines.append("}")
    return lines


# ------------------------------------------------------------------------------ parameter file
def _yaml_scalar(type_: str, value) -> str:
    """One parameter value as YAML that ROS reads back as the declared type. Strings use JSON
    quoting (valid YAML); floats always keep a decimal point or exponent form YAML 1.1 accepts."""
    if type_ == "bool":
        return "true" if value else "false"
    if type_ in _FLOAT_TYPES:
        number = float(value)
        if math.isnan(number):
            return ".nan"
        if math.isinf(number):
            return ".inf" if number > 0 else "-.inf"
        text = repr(number)
        return text.replace("e", ".0e") if "e" in text and "." not in text else text
    if type_ == "string":
        return json.dumps(value)
    return str(int(value))


def params_yaml(ir: dict) -> str:
    """The text of ``config/<node>.params.yaml``: every parameter with its manifest default, in
    the format of ``--ros-args --params-file``. Edit a copy to change values without rebuilding.
    ``/**`` matches the node under any name or namespace. Empty arrays are left as comments,
    because YAML cannot say which element type an empty list has."""
    lines = [
        "# Generated by nodesmith: the node's parameters with their manifest defaults, in the format",
        "# of `--ros-args --params-file`. Copy this file, change values and pass the copy to the",
        "# node (or to the launch file); no rebuild is needed.",
        "/**:",
        "  ros__parameters:",
    ]
    for parameter in ir["parameters"]:
        name, type_, value = (
            parameter["name"],
            parameter["canonical_type"],
            parameter["default_value"],
        )
        if _is_array(type_):
            if not value:
                lines.append(f'    # {name}: []  (empty array: set it with -p {name}:="[...]")')
                continue
            element = type_[:-2] if type_ != "bytes" else "int64"
            items = ", ".join(_yaml_scalar(element, v) for v in value)
            lines.append(f"    {name}: [{items}]")
        else:
            lines.append(f"    {name}: {_yaml_scalar(type_, value)}")
    return "\n".join(lines) + "\n"
