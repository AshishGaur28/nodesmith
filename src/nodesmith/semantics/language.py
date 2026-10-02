"""The expression language: its types, built-in functions and reserved words (SPEC-02).

This module only *describes* the language. ``expr`` parses text into a tree, and
``builder`` checks the tree against these definitions while lowering it to IR nodes.
"""

import keyword
from dataclasses import dataclass

NUMERIC_TYPES = ("int32", "int64", "float32", "float64")
INTEGER_TYPES = ("int32", "int64")
COMPARISON_OPERATORS = ("==", "!=", "<", "<=", ">", ">=")

# Implicit widening (SPEC-02 section 2.2): (from, to). Nothing else converts implicitly.
_WIDENING = {
    ("int32", "int64"),
    ("int32", "float64"),
    ("float32", "float64"),
    ("int64", "float64"),
}


def widens(source: str, target: str) -> bool:
    """True if a value of type ``source`` may be used where ``target`` is needed."""
    return source == target or (source, target) in _WIDENING


def common_type(left: str, right: str) -> str | None:
    """The type both operands of a binary operator are converted to, or ``None`` if they
    have none (SPEC-02 section 2.2: the least upper bound in the widening lattice)."""
    if left == right:
        return left
    if left not in NUMERIC_TYPES or right not in NUMERIC_TYPES:
        return None
    return next((t for t in NUMERIC_TYPES if widens(left, t) and widens(right, t)), "float64")


@dataclass(frozen=True)
class Function:
    """Signature of a built-in function (SPEC-02 section 5).

    ``result`` is a canonical type, or ``"same"`` (the type of the argument) or
    ``"common"`` (the common type of all arguments, which are widened to it)."""

    arity: int
    result: str
    numeric_args: bool = False  # every argument must be a number
    float_args: bool = False  # arguments are widened to float64 first


FUNCTIONS = {
    "abs": Function(1, "same", numeric_args=True),
    "min": Function(2, "common", numeric_args=True),
    "max": Function(2, "common", numeric_args=True),
    "clamp": Function(3, "common", numeric_args=True),
    "sqrt": Function(1, "float64", numeric_args=True, float_args=True),
    "sin": Function(1, "float64", numeric_args=True, float_args=True),
    "cos": Function(1, "float64", numeric_args=True, float_args=True),
    "pow": Function(2, "float64", numeric_args=True, float_args=True),
    "low_pass": Function(3, "float64", numeric_args=True, float_args=True),
    "deadband": Function(2, "float64", numeric_args=True, float_args=True),
    "rate_limit": Function(4, "float64", numeric_args=True, float_args=True),
    "len": Function(1, "int64"),  # a string, bytes or array: checked by the builder
    "to_int32": Function(1, "int32", numeric_args=True),
    "to_int64": Function(1, "int64", numeric_args=True),
    "to_float32": Function(1, "float32", numeric_args=True),
    "to_float64": Function(1, "float64", numeric_args=True),
    "now_sec": Function(0, "float64"),
    "dt_sec": Function(0, "float64"),
}

# Functions that depend on the time since the previous execution of the pipeline.
TIME_STEP_FUNCTIONS = {"dt_sec", "rate_limit"}

# Words an identifier in the manifest may not use (SPEC-02 section 4).
RESERVED_WORDS = {"let", "state", "param", "msg", "req", "res", "true", "false"} | set(FUNCTIONS)

_CPP_KEYWORD_TEXT = """
    alignas alignof and and_eq asm auto bitand bitor bool break case catch char class compl
    concept const consteval constexpr constinit const_cast continue co_await co_return co_yield
    decltype default delete do double dynamic_cast else enum explicit export extern false float
    for friend goto if inline int long mutable namespace new noexcept not not_eq nullptr operator
    or or_eq private protected public register reinterpret_cast requires return short signed
    sizeof static static_assert static_cast struct switch template this thread_local throw true
    try typedef typeid typename union unsigned using virtual void volatile wchar_t while xor
    xor_eq
"""
_CPP_KEYWORDS = frozenset(_CPP_KEYWORD_TEXT.split())

# Identifiers must not collide with a keyword of any supported target language (SPEC-01 4.2).
TARGET_KEYWORDS = _CPP_KEYWORDS | set(keyword.kwlist) | set(keyword.softkwlist)
