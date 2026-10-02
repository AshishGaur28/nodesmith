"""Lowers an expression tree to IR nodes, checking names and types on the way.

See SPEC-00 section 3.1 and SPEC-02.

``ExpressionBuilder`` serves one pipeline (or one parameter constraint). Nodes get ids in
creation order, are never shared or deduplicated, and a conversion that the language does
implicitly (widening) becomes an explicit ``to_*`` call node created before its operator.
"""

from typing import NamedTuple

from ..diagnostics import BuildError, Cascade
from . import expr
from .functions import UserFunction
from .language import (
    COMPARISON_OPERATORS,
    FUNCTIONS,
    INTEGER_TYPES,
    NUMERIC_TYPES,
    RESERVED_WORDS,
    TIME_STEP_FUNCTIONS,
    common_type,
    widens,
)

# Which kind of trigger provides which input root (``msg.`` needs a subscriber, ``req.`` a service).
_INPUT_ROOT_TRIGGER = {"msg": "subscriber", "req": "service"}
_LOCAL_FAILED = object()  # marks a local whose definition was already reported as an error


class Value(NamedTuple):
    """The result of lowering an expression: the id of its IR node and the node's type."""

    node_id: str
    type: str


def _error(code: str, message: str) -> BuildError:
    return BuildError(code, message)


class ExpressionBuilder:
    """State for lowering the expressions of one pipeline.

    After the statements have run, ``nodes`` holds the IR nodes, ``staged`` maps each assigned
    state variable to its value node, and ``state_reads``, ``state_writes`` and ``uses_dt``
    say how the pipeline touches state (needed to assign callback groups, SPEC-12)."""

    def __init__(
        self,
        manifest: dict,
        trigger_kind: str,
        input_fields: dict,
        functions: dict[str, UserFunction] | None = None,
        allow_user_calls: bool = True,
    ):
        self.nodes: list[dict] = []
        self.staged: dict[str, str] = {}
        self.state_reads: set[str] = set()
        self.state_writes: set[str] = set()
        self.uses_dt = False
        self._trigger_kind = trigger_kind
        self._input_fields = input_fields
        self._parameter_types = {k: v["type"] for k, v in manifest.get("parameters", {}).items()}
        self._state_types = {k: v["type"] for k, v in manifest.get("state_variables", {}).items()}
        self._locals: dict[str, Value | object] = {}
        self._defining: str | None = None  # the local whose definition is being lowered
        self._user_functions = functions or {}
        self._allow_user_calls = allow_user_calls

    # ------------------------------------------------------------------ nodes and conversions
    def add_node(self, op: str, type_: str, operands=(), attributes=None) -> Value:
        """Append an IR node and return its value."""
        node = {
            "id": f"n{len(self.nodes)}",
            "op": op,
            "type_symbol": type_,
            "operands": list(operands),
        }
        if attributes:
            node["attrs"] = attributes
        self.nodes.append(node)
        return Value(node["id"], type_)

    def widen(self, value: Value, target: str) -> Value:
        """Convert ``value`` to ``target`` along the implicit widening lattice, as an explicit
        ``to_*`` call; fail if the language does not convert that implicitly."""
        if value.type == target:
            return value
        if not widens(value.type, target):
            raise _error("ERR_SEM_102", f"cannot implicitly convert {value.type} to {target}")
        return self.add_node("call", target, [value.node_id], {"function": "to_" + target})

    @staticmethod
    def _adapt_literal(literal: expr.Const, expected: str | None) -> expr.Const:
        """A literal takes the type of a narrower sink of the same kind (SPEC-02 section 2.2)."""
        if literal.type == "int32" and expected in INTEGER_TYPES:
            return expr.Const(expected, literal.value)
        if literal.type == "float64" and expected in ("float32", "float64"):
            return expr.Const(expected, literal.value)
        return literal

    # ------------------------------------------------------------------ expressions
    def lower(self, tree, expected: str | None = None) -> Value:
        """Lower an expression. ``expected`` is the type of the sink it feeds, if any; it lets
        a literal adapt to that type."""
        match tree:
            case expr.Const():
                literal = self._adapt_literal(tree, expected)
                return self.add_node("const", literal.type, [], {"value": literal.value})
            case expr.Local():
                return self._local(tree)
            case expr.Ref():
                return self._reference(tree)
            case expr.Unary():
                return self._unary(tree)
            case expr.Binary():
                return self._binary(tree)
            case expr.Select():
                return self._select(tree, expected)
            case expr.Index():
                return self._index(tree)
            case expr.Call():
                return self._call(tree)
        raise AssertionError(f"unknown expression {tree!r}")

    def _local(self, tree: expr.Local) -> Value:
        if tree.name == self._defining:
            raise _error("ERR_SEM_103", f"local {tree.name!r} references itself")
        if tree.name not in self._locals:
            raise _error("ERR_SEM_101", f"undefined local {tree.name!r}")
        value = self._locals[tree.name]
        if value is _LOCAL_FAILED:
            raise Cascade  # its definition was reported; stay silent about the use
        return value

    def _reference(self, tree: expr.Ref) -> Value:
        dotted = ".".join(tree.path)
        if tree.root == "param":
            if len(tree.path) != 1 or tree.path[0] not in self._parameter_types:
                raise _error("ERR_SEM_101", f"unknown parameter {dotted!r}")
            name = tree.path[0]
            return self.add_node("param", self._parameter_types[name], [], {"name": name})
        if tree.root == "state":
            if len(tree.path) != 1 or tree.path[0] not in self._state_types:
                raise _error("ERR_SEM_101", f"unknown state variable {dotted!r}")
            name = tree.path[0]
            self.state_reads.add(name)
            return self.add_node("state", self._state_types[name], [], {"name": name})
        if self._trigger_kind != _INPUT_ROOT_TRIGGER[tree.root]:
            raise _error(
                "ERR_SEM_108",
                f"`{tree.root}.` is not available under a {self._trigger_kind} trigger",
            )
        field_type = self._input_field_type(tree.path)
        return self.add_node("input", field_type, [], {"root": tree.root, "path": dotted})

    def _input_field_type(self, path: tuple) -> str:
        """Canonical type of a field of the trigger's message or request."""
        node = self._input_fields
        for name in path:
            if not isinstance(node, dict) or name not in node:
                raise _error("ERR_SEM_107", f"field {'.'.join(path)!r} is not declared")
            node = node[name]
        if isinstance(node, dict):
            raise _error("ERR_SEM_102", f"{'.'.join(path)!r} is a nested message, not a value")
        return node

    def _unary(self, tree: expr.Unary) -> Value:
        operand = self.lower(tree.operand)
        if tree.operator == "!" and operand.type != "bool":
            raise _error("ERR_SEM_102", "`!` needs a bool")
        if tree.operator == "-" and operand.type not in NUMERIC_TYPES:
            raise _error("ERR_SEM_102", "unary `-` needs a number")
        return self.add_node("unary", operand.type, [operand.node_id], {"operator": tree.operator})

    def _binary(self, tree: expr.Binary) -> Value:
        operator = tree.operator
        left, right = self.lower(tree.left), self.lower(tree.right)
        if operator in ("&&", "||"):
            if left.type != "bool" or right.type != "bool":
                raise _error("ERR_SEM_102", f"`{operator}` needs bools")
            return self.add_node(
                "binary", "bool", [left.node_id, right.node_id], {"operator": operator}
            )
        operand_type = common_type(left.type, right.type)
        if operand_type is None:
            raise _error("ERR_SEM_102", f"`{operator}` on {left.type} and {right.type}")
        if operator == "%" and operand_type not in INTEGER_TYPES:
            raise _error("ERR_SEM_102", "`%` needs integers")
        if (
            operator in ("/", "%")
            and _is_zero_literal(tree.right)
            and isinstance(tree.left, expr.Const)
        ):
            raise _error("ERR_SEM_109", "constant division by zero")
        left, right = self.widen(left, operand_type), self.widen(right, operand_type)
        result_type = "bool" if operator in COMPARISON_OPERATORS else operand_type
        operands = [left.node_id, right.node_id]
        return self.add_node("binary", result_type, operands, {"operator": operator})

    def _select(self, tree: expr.Select, expected: str | None) -> Value:
        condition = self.lower(tree.condition)
        if condition.type != "bool":
            raise _error("ERR_SEM_102", "`?:` condition must be bool")
        if_true, if_false = self.lower(tree.if_true, expected), self.lower(tree.if_false, expected)
        result_type = (
            if_true.type
            if if_true.type == if_false.type
            else common_type(if_true.type, if_false.type)
        )
        if result_type is None:
            raise _error(
                "ERR_SEM_102", f"`?:` branches have types {if_true.type} and {if_false.type}"
            )
        if_true, if_false = self.widen(if_true, result_type), self.widen(if_false, result_type)
        operands = [condition.node_id, if_true.node_id, if_false.node_id]
        return self.add_node("select", result_type, operands)

    def _index(self, tree: expr.Index) -> Value:
        base, index = self.lower(tree.base), self.lower(tree.index)
        if not base.type.endswith("[]") or index.type not in INTEGER_TYPES:
            raise _error("ERR_SEM_102", "bad array index")
        return self.add_node("index", base.type[:-2], [base.node_id, index.node_id])

    def _call(self, tree: expr.Call) -> Value:
        function = FUNCTIONS.get(tree.name)
        if function is None:
            return self._user_call(tree)
        if len(tree.arguments) != function.arity:
            raise _error("ERR_SEM_102", f"{tree.name} takes {function.arity} argument(s)")
        if tree.name in TIME_STEP_FUNCTIONS:
            self.uses_dt = True
        arguments = [self.lower(argument) for argument in tree.arguments]
        if function.numeric_args and any(a.type not in NUMERIC_TYPES for a in arguments):
            raise _error("ERR_SEM_102", f"{tree.name} needs numbers")
        if tree.name == "len" and not _is_sized(arguments[0].type):
            raise _error("ERR_SEM_102", "len needs a string, bytes or an array")
        if function.result == "same":
            result_type = arguments[0].type
        elif function.result == "common":
            result_type = arguments[0].type
            for argument in arguments[1:]:
                result_type = common_type(result_type, argument.type)
            arguments = [self.widen(a, result_type) for a in arguments]
        else:
            result_type = function.result
            if function.float_args:
                arguments = [self.widen(a, "float64") for a in arguments]
        operand_ids = [a.node_id for a in arguments]
        return self.add_node("call", result_type, operand_ids, {"function": tree.name})

    def _user_call(self, tree: expr.Call) -> Value:
        """A call of a function the manifest declares (SPEC-02 section 5.1)."""
        declared = self._user_functions.get(tree.name)
        if declared is None:
            raise _error("ERR_SEM_101", f"unknown function {tree.name!r}")
        if not self._allow_user_calls:
            raise _error(
                "ERR_SEM_101",
                f"user function {tree.name!r} cannot be used in a parameter constraint",
            )
        if len(tree.arguments) != len(declared.arguments):
            raise _error("ERR_SEM_102", f"{tree.name} takes {len(declared.arguments)} argument(s)")
        values = [self.lower(argument) for argument in tree.arguments]
        widened = [self.widen(v, t) for v, t in zip(values, declared.argument_types, strict=True)]
        operand_ids = [value.node_id for value in widened]
        return self.add_node("user_call", declared.returns, operand_ids, {"function": tree.name})

    # ------------------------------------------------------------------ statements
    def run_statement(self, statement) -> None:
        """Lower one ``let`` or ``state.x = ...`` statement."""
        if isinstance(statement, expr.Let):
            self._let(statement)
        else:
            self._assign(statement)

    def _let(self, statement: expr.Let) -> None:
        name = statement.name
        if name in self._locals or name in RESERVED_WORDS:
            raise _error("ERR_SEM_104", f"duplicate or reserved local {name!r}")
        self._defining = name
        try:
            value = self.lower(statement.value)
        except (BuildError, Cascade):
            self._locals[name] = _LOCAL_FAILED  # later uses stay silent instead of cascading
            raise
        finally:
            self._defining = None
        self._locals[name] = value
        for node in self.nodes:  # the `let` labels the node its expression created
            if node["id"] == value.node_id and "label" not in node:
                node["label"] = name

    def _assign(self, statement: expr.Assign) -> None:
        name = statement.name
        if name not in self._state_types:
            raise _error("ERR_SEM_101", f"unknown state variable {name!r}")
        if name in self.state_writes:
            raise _error("ERR_SEM_104", f"state.{name} is assigned twice")
        self.state_writes.add(name)
        state_type = self._state_types[name]
        value = self.widen(self.lower(statement.value, state_type), state_type)
        self.staged[name] = value.node_id


def _is_zero_literal(tree) -> bool:
    return isinstance(tree, expr.Const) and tree.value == 0


def _is_sized(type_: str) -> bool:
    """True for the types ``len`` accepts: a string, bytes or an array."""
    return type_ in ("string", "bytes") or type_.endswith("[]")
