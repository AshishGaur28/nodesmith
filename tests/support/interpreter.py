"""Reference interpreter for a lowered execution DAG (SPEC-02 §5-§7). Test-only: the generated C++ is checked against it.

It is written from the spec, not from the C++ helpers, and evaluates lazily from the roots the way the spec describes
(`let` statements eagerly and in order; `?:`, `&&`, `||` only along the branch taken).
"""
import math
import struct

INT_BITS = {"int32": 32, "int64": 64}
NARROW = {"int8": (-2**7, 2**7 - 1), "uint8": (0, 2**8 - 1), "byte": (0, 2**8 - 1), "char": (0, 2**8 - 1), "int16": (-2**15, 2**15 - 1),
          "uint16": (0, 2**16 - 1), "uint32": (0, 2**32 - 1), "uint64": (0, 2**64 - 1)}


def f32(x):
    if math.isnan(x) or math.isinf(x): return x
    try: return struct.unpack("f", struct.pack("f", x))[0]
    except OverflowError: return math.copysign(math.inf, x)


def wrap(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >= 1 << (bits - 1) else v


def tdiv(a, b):
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


class Fault(Exception):
    pass


def read_field(ros_type, raw):
    """ROS field value -> canonical value (SPEC-02 §2.1). Raises Fault for a uint64 above INT64_MAX."""
    if ros_type.endswith("[]"): return [read_field(ros_type[:-2], x) for x in raw]
    if ros_type == "uint64":
        if raw > 2**63 - 1: raise Fault()
        return raw
    if ros_type in ("time", "duration"): return float(raw[0]) + float(raw[1]) * 1e-9
    if ros_type == "string": return raw
    return raw


def run(ir, dag, inputs, params, state, now_sec, dt_sec):
    """Returns (fault, result) where result maps member names (next_<state>, out__<target>__<path>) to canonical values."""
    nodes = {n["id"]: n for n in dag["nodes"]}
    types = {t["type_symbol"]: t for t in ir["interface_types"]}
    endpoints = {e["identifier"]: e for k in ("publishers", "subscribers", "services") for e in ir["interfaces"][k]}
    trig = dag["trigger"]
    in_type = types.get(endpoints[trig["source_id"]]["type_symbol"]) if "source_id" in trig else None
    in_fields = (in_type["fields"] if in_type["kind"] == "msg" else in_type["request"]) if in_type else {}
    roots = [w["source_node_id"] for w in dag["state_writes"]] + [a["source_node_id"] for a in dag["assignments"]]
    eager = [n["id"] for n in dag["nodes"] if "label" in n or n["id"] in roots]
    cache, fault = {}, [False]

    def ev(nid):
        if nid in cache: return cache[nid]
        v = calc(nodes[nid])
        if nid in eager: cache[nid] = v
        return v

    def calc(n):
        op, ty, at, o = n["op"], n["type_symbol"], n.get("attrs", {}), n["operands"]
        if op == "const": return f32(at["value"]) if ty == "float32" else at["value"]
        if op == "param": return params[at["name"]]
        if op == "state": return state[at["name"]]
        if op == "input":
            try: return read_field(in_fields_ros[at["path"]], inputs[at["path"]])
            except Fault: fault[0] = True; return 0
        if op == "unary":
            a = ev(o[0])
            if at["operator"] == "!": return not a
            return wrap(-a, INT_BITS[ty]) if ty in INT_BITS else -a
        if op == "select": return ev(o[1]) if ev(o[0]) else ev(o[2])
        if op == "index":
            arr, i = ev(o[0]), ev(o[1])
            if not 0 <= i < len(arr): fault[0] = True; return zero(ty)
            return arr[i]
        if op == "binary": return binary(at["operator"], o, ty, nodes[o[0]]["type_symbol"])
        return call(at["function"], [ev(x) for x in o], ty, [nodes[x]["type_symbol"] for x in o])

    def zero(ty): return "" if ty == "string" else False if ty == "bool" else 0

    def binary(op, o, ty, operand_ty):
        if op == "&&": return ev(o[0]) and ev(o[1])
        if op == "||": return ev(o[0]) or ev(o[1])
        a, b = ev(o[0]), ev(o[1])
        if op in ("==", "!=", "<", "<=", ">", ">="): return {"==": a == b, "!=": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
        if operand_ty in INT_BITS:
            bits = INT_BITS[operand_ty]
            if op == "+": return wrap(a + b, bits)
            if op == "-": return wrap(a - b, bits)
            if op == "*": return wrap(a * b, bits)
            if b == 0 or (op == "/" and b == -1 and a == -2 ** (bits - 1)): fault[0] = True; return 0
            return wrap(tdiv(a, b), bits) if op == "/" else (0 if b == -1 else a - b * tdiv(a, b))
        r = {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b, "/": lambda: fdiv(a, b)}[op]()
        return f32(r) if operand_ty == "float32" else r

    def fdiv(a, b):
        if b == 0:
            if a == 0 or math.isnan(a): return math.nan
            return math.copysign(math.inf, a) * math.copysign(1.0, b)
        return a / b

    def call(f, a, ty, arg_types):
        if f == "now_sec": return now_sec
        if f == "dt_sec": return dt_sec
        if f == "abs": return (wrap(-a[0], INT_BITS[ty]) if a[0] < 0 else a[0]) if ty in INT_BITS else abs(a[0])
        if f == "min": return a[1] if a[1] < a[0] else a[0]
        if f == "max": return a[1] if a[0] < a[1] else a[0]
        if f == "clamp":
            if not a[1] <= a[2]: fault[0] = True; return a[0]
            hi = a[1] if a[1] > a[0] else a[0]          # max(v, lo)
            return a[2] if a[2] < hi else hi            # min(., hi)
        if f == "sqrt":
            if a[0] < 0: fault[0] = True; return 0.0
            return math.sqrt(a[0]) if not math.isnan(a[0]) else a[0]
        if f == "sin": return math.sin(a[0]) if math.isfinite(a[0]) else math.nan
        if f == "cos": return math.cos(a[0]) if math.isfinite(a[0]) else math.nan
        if f == "pow":
            try: return math.pow(a[0], a[1])
            except OverflowError: return math.inf
            except ValueError: return math.nan
        if f == "low_pass":
            if not (0.0 <= a[2] <= 1.0): fault[0] = True; return a[1]
            return a[1] + a[2] * (a[0] - a[1])
        if f == "deadband": return 0.0 if abs(a[0]) < a[1] else a[0]
        if f == "rate_limit":
            target, prev, rise, fall = a
            up, down, d = rise * dt_sec, fall * dt_sec, target - prev
            return prev + up if d > up else prev - down if d < -down else target
        if f == "len": return len(a[0].encode()) if isinstance(a[0], str) else len(a[0])
        if f in ("to_int32", "to_int64"):
            bits = 32 if f == "to_int32" else 64
            x = a[0]
            if isinstance(x, float):
                if not -2.0 ** (bits - 1) <= x < 2.0 ** (bits - 1): fault[0] = True; return 0      # also false for NaN
                return int(x)
            if not -2 ** (bits - 1) <= x < 2 ** (bits - 1): fault[0] = True; return 0
            return x
        if f == "to_float32": return f32(float(a[0]))
        if f == "to_float64": return float(a[0])
        raise AssertionError(f)

    def sink_ok(value, canonical, ros_type):
        if canonical in ("float32", "float64") or ros_type in ("time", "duration"): return math.isfinite(value)
        if canonical in ("float32[]", "float64[]"): return all(math.isfinite(x) for x in value)
        if ros_type in NARROW: lo, hi = NARROW[ros_type]; return lo <= value <= hi
        return True

    in_fields_ros = in_fields
    for nid in eager: ev(nid)
    states = {s["name"]: s["canonical_type"] for s in ir["state_buffers"]}
    out = {}
    for w in dag["state_writes"]:
        v = ev(w["source_node_id"])
        if not sink_ok(v, states[w["state_name"]], None): fault[0] = True
        out[f"next_{w['state_name']}"] = v
    for a in dag["assignments"]:
        target, _, path = a["target_field_path"].partition(".")
        if target == "res": ros = types[endpoints[trig["source_id"]]["type_symbol"]]["response"][path]
        else: ros = types[endpoints[target]["type_symbol"]]["fields"][path]
        v = ev(a["source_node_id"])
        if not sink_ok(v, nodes[a["source_node_id"]]["type_symbol"], ros): fault[0] = True
        out[f"out__{target}__{path.replace('.', '__')}"] = v
    return fault[0], out
