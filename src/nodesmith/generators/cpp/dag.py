"""Emits the C++ for one execution DAG, i.e. one pipeline (SPEC-02 section 7, SPEC-00 3.1).

The DAG's nodes are in creation order. A node becomes a ``const`` local when it is a ``let``
(it has a label, so it is evaluated eagerly and in order) or the root of a state write or
output assignment. Every other node is inlined into the single expression that uses it, so
``?:``, ``&&`` and ``||`` evaluate only the branch they take, as SPEC-02 section 3 requires.

The result is the body of a pure function ``eval_<pipeline>`` (see ``pipelines.hpp.j2``): it
computes the staged state writes and the output values and reports a numeric fault by
returning ``false``. Committing and publishing happen in the node (``node.py``).
"""

from dataclasses import dataclass, field

from .errors import Unsupported
from .literals import literal
from .naming import NARROW_INTS, cpp_type, member, path_ident

_INTEGER_TYPES = ("int32", "int64")
_COMPARISONS = ("==", "!=", "<", "<=", ">", ">=")
_INTEGER_OPERATIONS = {
    "+": "wrap_add",
    "-": "wrap_sub",
    "*": "wrap_mul",
    "/": "idiv",
    "%": "imod",
}
_MATH_FUNCTIONS = {"sin": "std::sin", "cos": "std::cos", "pow": "std::pow"}

# Element type of a ROS array field, as the canonical element type the pipeline sees.
_ARRAY_ELEMENT_CPP = {
    "byte": "std::uint8_t",
    "uint8": "std::uint8_t",
    "char": "std::int32_t",
    "int8": "std::int32_t",
    "int16": "std::int32_t",
    "uint16": "std::int32_t",
    "int32": "std::int32_t",
    "uint32": "std::int64_t",
    "float32": "float",
    "float64": "double",
    "bool": "bool",
    "string": "std::string",
}


@dataclass
class Assignment:
    """One ``output_mapping`` entry: a value written into a field of a message or response."""

    target: str  # publisher id, or "res" for a service response
    path: str  # dotted field path inside that message
    ros_type: str  # the ROS type of that field
    member: str  # the member of the result struct holding the value
    canonical: str  # canonical type of the value


@dataclass
class PipelineInfo:
    """What the node needs to know about a pipeline's generated function."""

    name: str
    trigger: dict
    input_type: str | None = None  # message or service type of the trigger's input
    state_members: list = field(default_factory=list)  # (state name, C++ type, result member)
    assignments: list[Assignment] = field(default_factory=list)
    uses_dt: bool = False  # reads the time since the previous execution
    uses_now: bool = False
    body: str = ""  # the C++ body of the evaluation function
    result_members: list = field(default_factory=list)  # (C++ type, member) of the result struct


def read_ros_field(access: str, ros_type: str) -> str:
    """C++ expression converting a ROS message field to its canonical type (SPEC-02 section 2.1)."""
    if ros_type.endswith("[]"):
        element = ros_type[:-2]
        if element in ("uint64", "int64"):
            raise Unsupported(f"array fields of type {ros_type} are not supported yet")
        return f"r2d::to_vector<{_ARRAY_ELEMENT_CPP[element]}>({access})"
    if ros_type in ("bool", "float32", "float64", "int32"):
        return access
    if ros_type in ("byte", "char", "int8", "uint8", "int16", "uint16"):
        return f"static_cast<std::int32_t>({access})"
    if ros_type in ("uint32", "int64"):
        return f"static_cast<std::int64_t>({access})"
    if ros_type == "uint64":
        return f"r2d::u64_to_i64({access}, fault)"
    if ros_type == "string":
        return f"std::string({access})"
    if ros_type in ("time", "duration"):
        return f"r2d::time_to_sec({access})"
    raise Unsupported(f"field type {ros_type}")


def sink_check(value: str, canonical: str, ros_type: str | None) -> str | None:
    """A statement that sets ``fault`` if ``value`` must not reach a state write or message
    field (NaN/Inf, or out of range for a narrow ROS type; SPEC-02 section 6), or ``None``."""
    if canonical in ("float32", "float64") or ros_type in ("time", "duration"):
        return f"r2d::check_finite({value}, fault);"
    if canonical in ("float32[]", "float64[]"):
        return f"r2d::check_finite_all({value}, fault);"
    if ros_type in NARROW_INTS:
        return f"r2d::check_range<{NARROW_INTS[ros_type]}>({value}, fault);"
    if ros_type == "uint64":
        return f"r2d::check_range<std::uint64_t>({value}, fault);"
    return None


class DagEmitter:
    """Turns one execution DAG of the IR into C++ (see the module docstring)."""

    def __init__(self, ir: dict, dag: dict):
        self.ir = ir
        self.dag = dag
        self.nodes = {node["id"]: node for node in dag["nodes"]}
        self.types = {t["type_symbol"]: t for t in ir["interface_types"]}
        self.endpoints = {
            endpoint["identifier"]: endpoint
            for kind in ("publishers", "subscribers", "services")
            for endpoint in ir["interfaces"][kind]
        }
        self.info = PipelineInfo(dag["id"], dag["trigger"])
        trigger = dag["trigger"]
        if trigger["kind"] in ("subscriber", "service"):
            self.info.input_type = self.endpoints[trigger["source_id"]]["type_symbol"]
        roots = [w["source_node_id"] for w in dag["state_writes"]]
        roots += [a["source_node_id"] for a in dag["assignments"]]
        labelled = {node["id"] for node in dag["nodes"] if "label" in node}
        self.materialized = labelled | set(roots)  # nodes that become a ``const`` local
        self._local_names: dict[str, str] = {}

    # ------------------------------------------------------------------ names and expressions
    def local_name(self, node_id: str) -> str:
        """The C++ name of a materialized node: ``v_<label>`` for a ``let``, else its id."""
        if node_id not in self._local_names:
            node = self.nodes[node_id]
            self._local_names[node_id] = (
                f"v_{member(node['label'])}" if "label" in node else node_id
            )
        return self._local_names[node_id]

    def operand(self, node_id: str) -> str:
        """The C++ for a node used as an operand: its local name, or the inlined expression."""
        return (
            self.local_name(node_id) if node_id in self.materialized else self.expression(node_id)
        )

    def expression(self, node_id: str) -> str:
        """The C++ expression computing a node."""
        node = self.nodes[node_id]
        op, type_ = node["op"], node["type_symbol"]
        attributes = node.get("attrs", {})
        operands = [self.operand(o) for o in node["operands"]]
        operand_types = [self.nodes[o]["type_symbol"] for o in node["operands"]]
        if op == "const":
            return literal(attributes["value"], type_)
        if op == "param":
            return f"params.{member(attributes['name'])}"
        if op == "state":
            return f"state.{member(attributes['name'])}"
        if op == "input":
            ros_type = self._input_ros_type(attributes["path"])
            return read_ros_field(f"in.{attributes['path']}", ros_type)
        if op == "unary":
            return self._unary(attributes["operator"], operands[0], type_)
        if op == "select":
            return f"({operands[0]} ? {operands[1]} : {operands[2]})"
        if op == "index":
            return f"r2d::at({operands[0]}, {operands[1]}, fault)"
        if op == "binary":
            return self._binary(attributes["operator"], operands, operand_types[0])
        if op == "user_call":
            return self._user_call(attributes["function"], operands)
        return self._call(attributes["function"], operands, type_, operand_types)

    @staticmethod
    def _user_call(name: str, operands: list[str]) -> str:
        """A call of a function the user implements (``logic_api.hpp``)."""
        return f"::logic::{name}({', '.join([*operands, 'fault'])})"

    def _input_ros_type(self, path: str) -> str:
        declaration = self.types[self.info.input_type]
        fields = declaration["fields"] if declaration["kind"] == "msg" else declaration["request"]
        return fields[path]

    @staticmethod
    def _unary(operator: str, operand: str, type_: str) -> str:
        if operator == "!":
            return f"(!{operand})"
        if type_ in _INTEGER_TYPES:
            return f"r2d::wrap_neg<{cpp_type(type_)}>({operand})"
        return f"(-{operand})"

    @staticmethod
    def _binary(operator: str, operands: list[str], operand_type: str) -> str:
        left, right = operands
        if operator in ("&&", "||", *_COMPARISONS) or operand_type not in _INTEGER_TYPES:
            return f"({left} {operator} {right})"
        helper = _INTEGER_OPERATIONS[operator]
        fault = ", fault" if operator in ("/", "%") else ""
        return f"r2d::{helper}<{cpp_type(operand_type)}>({left}, {right}{fault})"

    def _call(self, name: str, args: list[str], type_: str, arg_types: list[str]) -> str:
        arguments = ", ".join(args)
        if name == "now_sec":
            self.info.uses_now = True
            return "now_sec"
        if name == "dt_sec":
            self.info.uses_dt = self.info.uses_now = True
            return "dt_sec"
        if name == "rate_limit":
            self.info.uses_dt = self.info.uses_now = True
            return f"r2d::rate_limit({arguments}, dt_sec)"
        if name == "abs":
            if type_ in _INTEGER_TYPES:
                return f"r2d::iabs<{cpp_type(type_)}>({args[0]})"
            return f"std::fabs({args[0]})"
        if name in ("min", "max"):
            return f"r2d::{name}_({arguments})"
        if name == "clamp":
            return f"r2d::clamp_({arguments}, fault)"
        if name == "sqrt":
            return f"r2d::fsqrt({args[0]}, fault)"
        if name in _MATH_FUNCTIONS:
            return f"{_MATH_FUNCTIONS[name]}({arguments})"
        if name == "low_pass":
            return f"r2d::low_pass({arguments}, fault)"
        if name == "deadband":
            return f"r2d::deadband({arguments})"
        if name == "len":
            return f"r2d::len({args[0]})"
        if name in ("to_int32", "to_int64"):
            return self._integer_conversion(args[0], type_, arg_types[0])
        if name in ("to_float32", "to_float64"):
            return f"static_cast<{cpp_type(type_)}>({args[0]})"
        raise AssertionError(name)

    @staticmethod
    def _integer_conversion(argument: str, target_type: str, source_type: str) -> str:
        if source_type == target_type:
            return argument
        target = cpp_type(target_type)
        if source_type == "int32":  # widening: cannot fail
            return f"static_cast<{target}>({argument})"
        return f"r2d::to_int<{target}>({argument}, fault)"

    # ------------------------------------------------------------------ the function body
    def build(self) -> PipelineInfo:
        """Fill in ``info.body`` and the member lists; return ``info``."""
        info = self.info
        declarations = [
            f"const {cpp_type(self.nodes[nid]['type_symbol'])} {self.local_name(nid)} = "
            f"{self.expression(nid)};"
            for nid in (n["id"] for n in self.dag["nodes"])
            if nid in self.materialized
        ]
        checks, assignments = [], []
        self._state_writes(checks, assignments)
        self._output_assignments(checks, assignments)
        checks = list(dict.fromkeys(checks))  # a value reaching two sinks is checked once
        lines = [*declarations, *checks, "if (fault) return false;", *assignments, "return true;"]
        info.body = "\n  ".join(lines)
        return info

    def _state_writes(self, checks: list[str], assignments: list[str]) -> None:
        state_types = {s["name"]: s["canonical_type"] for s in self.ir["state_buffers"]}
        for write in self.dag["state_writes"]:
            name, value = write["state_name"], self.operand(write["source_node_id"])
            result_member = f"next_{member(name)}"
            cpp = cpp_type(state_types[name])
            self.info.state_members.append((name, cpp, result_member))
            self.info.result_members.append((cpp, result_member))
            check = sink_check(value, state_types[name], None)
            if check:
                checks.append(check)
            assignments.append(f"out.{result_member} = {value};")

    def _output_assignments(self, checks: list[str], assignments: list[str]) -> None:
        for mapping in self.dag["assignments"]:
            target, _, path = mapping["target_field_path"].partition(".")
            value = self.operand(mapping["source_node_id"])
            ros_type = self._sink_ros_type(target, path)
            canonical = self.nodes[mapping["source_node_id"]]["type_symbol"]
            result_member = f"out__{target}__{path_ident(path)}"
            self.info.assignments.append(
                Assignment(target, path, ros_type, result_member, canonical)
            )
            self.info.result_members.append((cpp_type(canonical), result_member))
            check = sink_check(value, canonical, ros_type)
            if check:
                checks.append(check)
            assignments.append(f"out.{result_member} = {value};")

    def _sink_ros_type(self, target: str, path: str) -> str:
        """The ROS type of the field an output is written to."""
        if target == "res":
            service = self.endpoints[self.dag["trigger"]["source_id"]]
            return self.types[service["type_symbol"]]["response"][path]
        return self.types[self.endpoints[target]["type_symbol"]]["fields"][path]
