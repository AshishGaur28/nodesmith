"""Emits the C++ for one execution DAG (SPEC-02 §7, SPEC-00 §3.1).

The DAG's nodes are in creation order. A node becomes a `const` local when it is a `let` (it has a label: it is evaluated eagerly,
in order) or the root of a state write or output assignment. Every other node is inlined into its single consumer, so `?:`, `&&` and
`||` evaluate only the branch they take, as SPEC-02 §3 requires.
"""
from dataclasses import dataclass, field

from .naming import NARROW_INTS, cpp_type, member, path_ident

UNSUPPORTED_ROS = {"uint64[]", "int64[]"}     # placeholder, see ros_read


class Unsupported(Exception):
    """A manifest feature this revision of the C++ generator does not implement."""


@dataclass
class Assignment:
    target: str                 # publisher id or "res"
    path: str                   # dotted field path
    ros_type: str
    member: str                 # member of the result struct holding the value
    canonical: str


@dataclass
class PipelineInfo:
    name: str
    trigger: dict
    input_type: str | None = None        # message or service type symbol of the trigger's input, if any
    input_root: str | None = None        # "msg" or "req"
    state_members: list = field(default_factory=list)       # (state name, C++ type, member)
    assignments: list = field(default_factory=list)
    uses_dt: bool = False
    uses_now: bool = False
    body: str = ""
    result_members: list = field(default_factory=list)      # (C++ type, member)


def cpp_string(s: str) -> str:
    out = []
    for b in s.encode("utf-8"):
        c = chr(b)
        if c == "\\": out.append("\\\\")
        elif c == '"': out.append('\\"')
        elif c == "\n": out.append("\\n")
        elif 32 <= b < 127: out.append(c)
        else: out.append("\\%03o" % b)
    return '"' + "".join(out) + '"'


def literal(value, ty):
    if ty == "bool": return "true" if value else "false"
    if ty == "string": return f"std::string({cpp_string(value)})"
    if ty == "int32": return f"std::int32_t{{{value}}}"
    if ty == "int64": return f"std::int64_t{{INT64_C({value})}}"
    text = repr(float(value))
    if all(c not in text for c in ".eEn"): text += ".0"
    if ty == "float32": return f"static_cast<float>({text})"
    return text


def ros_read(expr, ros_type):
    """C++ expression converting a ROS message field to its canonical type (SPEC-02 §2.1)."""
    if ros_type.endswith("[]"):
        base = ros_type[:-2]
        if base in ("uint64", "int64"): raise Unsupported(f"array fields of type {ros_type} are not supported yet")
        canon = {"byte": "std::uint8_t", "uint8": "std::uint8_t", "char": "std::int32_t", "int8": "std::int32_t", "int16": "std::int32_t",
                 "uint16": "std::int32_t", "int32": "std::int32_t", "uint32": "std::int64_t", "float32": "float", "float64": "double",
                 "bool": "bool", "string": "std::string"}[base]
        return f"r2d::to_vector<{canon}>({expr})"
    if ros_type in ("bool", "float32", "float64", "int32"): return expr
    if ros_type in ("byte", "char", "int8", "uint8", "int16", "uint16"): return f"static_cast<std::int32_t>({expr})"
    if ros_type in ("uint32", "int64"): return f"static_cast<std::int64_t>({expr})"
    if ros_type == "uint64": return f"r2d::u64_to_i64({expr}, fault)"
    if ros_type == "string": return f"std::string({expr})"
    if ros_type in ("time", "duration"): return f"r2d::time_to_sec({expr})"
    raise Unsupported(f"field type {ros_type}")


FUNCS = {"sin": "std::sin", "cos": "std::cos", "pow": "std::pow"}


class Emitter:
    def __init__(self, ir, dag):
        self.ir, self.dag = ir, dag
        self.nodes = {n["id"]: n for n in dag["nodes"]}
        self.types = {t["type_symbol"]: t for t in ir["interface_types"]}
        self.endpoints = {}
        for kind in ("publishers", "subscribers", "services"):
            for e in ir["interfaces"][kind]: self.endpoints[e["identifier"]] = e
        self.info = PipelineInfo(dag["id"], dag["trigger"])
        trig = dag["trigger"]
        if trig["kind"] in ("subscriber", "service"):
            self.info.input_type = self.endpoints[trig["source_id"]]["type_symbol"]
            self.info.input_root = "msg" if trig["kind"] == "subscriber" else "req"
        roots = [w["source_node_id"] for w in dag["state_writes"]] + [a["source_node_id"] for a in dag["assignments"]]
        self.materialized = {n["id"] for n in dag["nodes"] if "label" in n} | set(roots)
        self.names = {}

    # -- expressions
    def local(self, nid):
        if nid not in self.names:
            n = self.nodes[nid]
            self.names[nid] = f"v_{member(n['label'])}" if "label" in n else f"{nid}"
        return self.names[nid]

    def ex(self, nid):
        if nid in self.materialized: return self.local(nid)
        return self.expr(nid)

    def input_ros_type(self, path):
        t = self.types[self.info.input_type]
        fields = t["fields"] if t["kind"] == "msg" else t["request"]
        return fields[path]

    def expr(self, nid):
        n = self.nodes[nid]; op, ty, at = n["op"], n["type_symbol"], n.get("attrs", {})
        a = [self.ex(o) for o in n["operands"]]
        if op == "const": return literal(at["value"], ty)
        if op == "param": return f"params.{member(at['name'])}"
        if op == "state": return f"state.{member(at['name'])}"
        if op == "input": return ros_read(f"in.{at['path']}", self.input_ros_type(at["path"]))
        if op == "unary":
            if at["operator"] == "!": return f"(!{a[0]})"
            return f"r2d::wrap_neg<{cpp_type(ty)}>({a[0]})" if ty in ("int32", "int64") else f"(-{a[0]})"
        if op == "select": return f"({a[0]} ? {a[1]} : {a[2]})"
        if op == "index": return f"r2d::at({a[0]}, {a[1]}, fault)"
        if op == "binary": return self.binary(at["operator"], a, self.nodes[n["operands"][0]]["type_symbol"])
        return self.call(at["function"], a, ty, [self.nodes[o]["type_symbol"] for o in n["operands"]])

    def binary(self, o, a, operand_ty):
        if o in ("&&", "||", "==", "!=", "<", "<=", ">", ">="): return f"({a[0]} {o} {a[1]})"
        if operand_ty in ("int32", "int64"):
            t = cpp_type(operand_ty)
            fn = {"+": "wrap_add", "-": "wrap_sub", "*": "wrap_mul", "/": "idiv", "%": "imod"}[o]
            return f"r2d::{fn}<{t}>({a[0]}, {a[1]}{', fault' if o in '/%' else ''})"
        return f"({a[0]} {o} {a[1]})"

    def call(self, f, a, ty, arg_types):
        if f == "now_sec": self.info.uses_now = True; return "now_sec"
        if f == "dt_sec": self.info.uses_dt = self.info.uses_now = True; return "dt_sec"
        if f == "rate_limit": self.info.uses_dt = self.info.uses_now = True; return f"r2d::rate_limit({', '.join(a)}, dt_sec)"
        if f == "abs": return f"r2d::iabs<{cpp_type(ty)}>({a[0]})" if ty in ("int32", "int64") else f"std::fabs({a[0]})"
        if f in ("min", "max"): return f"r2d::{f}_({a[0]}, {a[1]})"
        if f == "clamp": return f"r2d::clamp_({', '.join(a)}, fault)"
        if f == "sqrt": return f"r2d::fsqrt({a[0]}, fault)"
        if f in FUNCS: return f"{FUNCS[f]}({', '.join(a)})"
        if f == "low_pass": return f"r2d::low_pass({', '.join(a)}, fault)"
        if f == "deadband": return f"r2d::deadband({a[0]}, {a[1]})"
        if f == "len": return f"r2d::len({a[0]})"
        if f in ("to_int32", "to_int64"):
            target = cpp_type(ty)
            return a[0] if arg_types[0] == ty else (f"static_cast<{target}>({a[0]})" if arg_types[0] == "int32" else f"r2d::to_int<{target}>({a[0]}, fault)")
        if f in ("to_float32", "to_float64"): return f"static_cast<{cpp_type(ty)}>({a[0]})"
        raise AssertionError(f)

    # -- the function body
    def sink_check(self, value, canonical, ros_type):
        """Statement checking a value before it reaches a state write or message field (SPEC-02 §6), or None."""
        if canonical in ("float32", "float64") or ros_type in ("time", "duration"): return f"r2d::check_finite({value}, fault);"
        if canonical == "float32[]" or canonical == "float64[]": return f"r2d::check_finite_all({value}, fault);"
        if ros_type in NARROW_INTS: return f"r2d::check_range<{NARROW_INTS[ros_type]}>({value}, fault);"
        if ros_type == "uint64": return f"r2d::check_range<std::uint64_t>({value}, fault);"
        return None

    def build(self):
        info, lines = self.info, []
        order = [n["id"] for n in self.dag["nodes"] if n["id"] in self.materialized]
        for nid in order:
            n = self.nodes[nid]
            lines.append(f"const {cpp_type(n['type_symbol'])} {self.local(nid)} = {self.expr(nid)};")
        states = {s["name"]: s["canonical_type"] for s in self.ir["state_buffers"]}
        checks, assigns = [], []
        for w in self.dag["state_writes"]:
            member_ = f"next_{member(w['state_name'])}"
            info.state_members.append((w["state_name"], cpp_type(states[w["state_name"]]), member_))
            info.result_members.append((cpp_type(states[w["state_name"]]), member_))
            c = self.sink_check(self.ex(w["source_node_id"]), states[w["state_name"]], None)
            if c: checks.append(c)
            assigns.append(f"out.{member_} = {self.ex(w['source_node_id'])};")
        for a in self.dag["assignments"]:
            target, _, path = a["target_field_path"].partition(".")
            ros_type = self.ros_sink_type(target, path)
            canonical = self.nodes[a["source_node_id"]]["type_symbol"]
            member_ = f"out__{target}__{path_ident(path)}"
            info.assignments.append(Assignment(target, path, ros_type, member_, canonical))
            info.result_members.append((cpp_type(canonical), member_))
            c = self.sink_check(self.ex(a["source_node_id"]), canonical, ros_type)
            if c: checks.append(c)
            assigns.append(f"out.{member_} = {self.ex(a['source_node_id'])};")
        checks = list(dict.fromkeys(checks))      # the same value reaching two sinks is checked once
        info.body = "\n  ".join(lines + checks + ["if (fault) return false;"] + assigns + ["return true;"])
        return info

    def ros_sink_type(self, target, path):
        if target == "res":
            svc = self.endpoints[self.dag["trigger"]["source_id"]]
            return self.types[svc["type_symbol"]]["response"][path]
        return self.types[self.endpoints[target]["type_symbol"]]["fields"][path]
