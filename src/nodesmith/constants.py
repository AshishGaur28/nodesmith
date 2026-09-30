"""Evaluates a lowered parameter constraint on the parameters' default values (SPEC-07 §5 rule 3, ERR_SEM_106).

Only what a constraint can contain: constants, `param.` references, operators, `?:`, indexing and the pure built-ins. Integers wrap at
their width (SPEC-02 §6); float32 values are computed in double precision, so a constraint that only holds through float32 rounding
is not detected.
"""
import math


class Fault(Exception):
    """A numeric fault (division by zero, domain error, bad index) while evaluating on the defaults."""


_WIDTH = {"int32": 32, "int64": 64}


def _wrap(v, ty):
    bits = _WIDTH.get(ty)
    if bits is None: return v
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >= 1 << (bits - 1) else v


def _trunc_div(a, b): return int(a / b) if abs(a) < 2 ** 52 and abs(b) < 2 ** 52 else (abs(a) // abs(b)) * (1 if (a < 0) == (b < 0) else -1)


def _call(name, a):
    if name in ("abs",): return abs(a[0])
    if name == "min": return min(a)
    if name == "max": return max(a)
    if name == "clamp":
        if a[1] > a[2]: raise Fault("clamp with lo > hi")
        return min(max(a[0], a[1]), a[2])
    if name == "sqrt":
        if a[0] < 0: raise Fault("sqrt of a negative number")
        return math.sqrt(a[0])
    if name == "sin": return math.sin(a[0])
    if name == "cos": return math.cos(a[0])
    if name == "pow":
        try: return float(a[0]) ** float(a[1])
        except (ZeroDivisionError, OverflowError, ValueError): raise Fault("pow out of domain")
    if name == "low_pass":
        if not 0 <= a[2] <= 1: raise Fault("low_pass alpha outside [0, 1]")
        return a[1] + a[2] * (a[0] - a[1])
    if name == "deadband": return 0.0 if abs(a[0]) < a[1] else a[0]
    if name == "len": return len(a[0])
    if name in ("to_int32", "to_int64"):
        if isinstance(a[0], float) and not math.isfinite(a[0]): raise Fault("conversion of a non-finite value")
        v = int(a[0])
        if not -(1 << (_WIDTH[name[3:]] - 1)) <= v < 1 << (_WIDTH[name[3:]] - 1): raise Fault("integer conversion out of range")
        return v
    if name in ("to_float32", "to_float64"): return float(a[0])
    raise Fault(f"{name} cannot be evaluated at build time")      # now_sec, dt_sec, rate_limit


def evaluate(constraint: dict, defaults: dict):
    """Value of the constraint's root node with `param.<name>` taken from `defaults`. Raises Fault on a numeric fault."""
    nodes = {n["id"]: n for n in constraint["nodes"]}

    def ev(nid):
        n = nodes[nid]; op, ty, ops, at = n["op"], n["type_symbol"], n["operands"], n.get("attrs", {})
        if op == "const": return at["value"]
        if op == "param": return defaults[at["name"]]
        if op == "unary":
            v = ev(ops[0])
            return (not v) if at["operator"] == "!" else _wrap(-v, ty)
        if op == "select": return ev(ops[1]) if ev(ops[0]) else ev(ops[2])
        if op == "index":
            arr, i = ev(ops[0]), ev(ops[1])
            if not 0 <= i < len(arr): raise Fault("index out of range")
            return arr[i]
        if op == "call": return _call(at["function"], [ev(o) for o in ops])
        if op == "binary":
            o = at["operator"]
            if o == "&&": return ev(ops[0]) and ev(ops[1])
            if o == "||": return ev(ops[0]) or ev(ops[1])
            a, b = ev(ops[0]), ev(ops[1])
            if o in ("==", "!=", "<", "<=", ">", ">="): return {"==": a == b, "!=": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[o]
            integer = ty in _WIDTH
            if o == "+": return _wrap(a + b, ty) if integer else a + b
            if o == "-": return _wrap(a - b, ty) if integer else a - b
            if o == "*": return _wrap(a * b, ty) if integer else a * b
            if integer and o in ("/", "%"):
                if b == 0: raise Fault("integer division by zero")
                if b == -1 and a == -(1 << (_WIDTH[ty] - 1)): raise Fault("INT_MIN / -1")
                return _trunc_div(a, b) if o == "/" else a - b * _trunc_div(a, b)
            if o == "/":                                   # float division by zero is inf or nan, not a fault (SPEC-02 §6)
                if b == 0: return math.nan if a == 0 or a != a else math.copysign(math.inf, a) * math.copysign(1.0, b)
                return a / b
        raise Fault(f"cannot evaluate {op}")

    return ev(constraint["root_node_id"])
