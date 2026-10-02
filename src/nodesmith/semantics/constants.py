"""Evaluates a lowered parameter constraint on the parameters' default values.

This is how ``ERR_SEM_106`` (SPEC-07 section 5, rule 3) is decided: a constraint such as
``param.step <= param.limit`` must hold on the defaults. Only what a constraint can contain is
supported: constants, ``param.`` references, operators, ``?:``, indexing and the pure
built-ins. Integers wrap at their width (SPEC-02 section 6); float32 values are computed in
double precision, so a constraint that holds only through float32 rounding is not detected.
"""

import math
import operator

_INT_BITS = {"int32": 32, "int64": 64}
_COMPARISONS = {
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}
_EXACT_DIVISION_LIMIT = 2**52  # below this, float division of integers is exact enough to truncate


class Fault(Exception):
    """A numeric fault (division by zero, domain error, bad index) on the defaults."""


def _wrap(value: int, type_: str) -> int:
    """Wrap an integer into the two's-complement range of ``type_`` (a no-op for floats)."""
    bits = _INT_BITS.get(type_)
    if bits is None:
        return value
    value &= (1 << bits) - 1
    return value - (1 << bits) if value >= 1 << (bits - 1) else value


def _truncating_divide(dividend: int, divisor: int) -> int:
    """Integer division rounding toward zero (SPEC-02 section 6)."""
    if abs(dividend) < _EXACT_DIVISION_LIMIT and abs(divisor) < _EXACT_DIVISION_LIMIT:
        return int(dividend / divisor)
    quotient = abs(dividend) // abs(divisor)
    return quotient if (dividend < 0) == (divisor < 0) else -quotient


def _float_divide(dividend: float, divisor: float) -> float:
    """Float division: by zero gives inf or nan, never a fault (SPEC-02 section 6)."""
    if divisor == 0:
        if dividend == 0 or math.isnan(dividend):
            return math.nan
        return math.copysign(math.inf, dividend) * math.copysign(1.0, divisor)
    return dividend / divisor


def _call_builtin(name: str, args: list):
    """Value of a built-in call; raises ``Fault`` where SPEC-02 section 5 says it faults."""
    if name == "abs":
        return abs(args[0])
    if name == "min":
        return min(args)
    if name == "max":
        return max(args)
    if name == "clamp":
        value, low, high = args
        if low > high:
            raise Fault("clamp with lo > hi")
        return min(max(value, low), high)
    if name == "sqrt":
        if args[0] < 0:
            raise Fault("sqrt of a negative number")
        return math.sqrt(args[0])
    if name in ("sin", "cos"):
        return getattr(math, name)(args[0])
    if name == "pow":
        try:
            return float(args[0]) ** float(args[1])
        except (ZeroDivisionError, OverflowError, ValueError) as error:
            raise Fault("pow out of domain") from error
    if name == "low_pass":
        current, previous, alpha = args
        if not 0 <= alpha <= 1:
            raise Fault("low_pass alpha outside [0, 1]")
        return previous + alpha * (current - previous)
    if name == "deadband":
        return 0.0 if abs(args[0]) < args[1] else args[0]
    if name == "len":
        return len(args[0])
    if name in ("to_int32", "to_int64"):
        return _to_integer(args[0], _INT_BITS[name[3:]])
    if name in ("to_float32", "to_float64"):
        return float(args[0])
    raise Fault(f"{name} cannot be evaluated at build time")  # now_sec, dt_sec, rate_limit


def _to_integer(value, bits: int) -> int:
    if isinstance(value, float) and not math.isfinite(value):
        raise Fault("conversion of a non-finite value")
    result = int(value)
    if not -(1 << (bits - 1)) <= result < 1 << (bits - 1):
        raise Fault("integer conversion out of range")
    return result


def _binary(op: str, left, right, type_: str):
    """Value of a binary operator on two already-evaluated operands (not ``&&``/``||``)."""
    if op in _COMPARISONS:
        return _COMPARISONS[op](left, right)
    is_integer = type_ in _INT_BITS
    if op in ("+", "-", "*"):
        result = {"+": operator.add, "-": operator.sub, "*": operator.mul}[op](left, right)
        return _wrap(result, type_) if is_integer else result
    if is_integer:  # "/" or "%"
        if right == 0:
            raise Fault("integer division by zero")
        if right == -1 and left == -(1 << (_INT_BITS[type_] - 1)):
            raise Fault("INT_MIN / -1")
        quotient = _truncating_divide(left, right)
        return quotient if op == "/" else left - right * quotient
    if op == "/":
        return _float_divide(left, right)
    raise Fault(f"cannot evaluate {op}")


def evaluate(constraint: dict, defaults: dict):
    """Value of the constraint's root node, with ``param.<name>`` taken from ``defaults``.

    Raises ``Fault`` on a numeric fault."""
    nodes = {node["id"]: node for node in constraint["nodes"]}

    def value_of(node_id: str):
        node = nodes[node_id]
        op, type_ = node["op"], node["type_symbol"]
        operands, attributes = node["operands"], node.get("attrs", {})
        if op == "const":
            return attributes["value"]
        if op == "param":
            return defaults[attributes["name"]]
        if op == "unary":
            operand = value_of(operands[0])
            return (not operand) if attributes["operator"] == "!" else _wrap(-operand, type_)
        if op == "select":
            return value_of(operands[1]) if value_of(operands[0]) else value_of(operands[2])
        if op == "index":
            array, index = value_of(operands[0]), value_of(operands[1])
            if not 0 <= index < len(array):
                raise Fault("index out of range")
            return array[index]
        if op == "call":
            return _call_builtin(attributes["function"], [value_of(o) for o in operands])
        if op == "binary":
            operator_ = attributes["operator"]
            if operator_ == "&&":  # short-circuit, like the generated code
                return value_of(operands[0]) and value_of(operands[1])
            if operator_ == "||":
                return value_of(operands[0]) or value_of(operands[1])
            return _binary(operator_, value_of(operands[0]), value_of(operands[1]), type_)
        raise Fault(f"cannot evaluate {op}")

    return value_of(constraint["root_node_id"])
