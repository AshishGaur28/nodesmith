"""Expression syntax: turns the text of an expression or statement into a tree (SPEC-02 section 3).

Only syntax is handled here. Whether a name exists or the types fit is decided later, by
``builder``. A syntax error is ``ERR_SYN_002``.
"""

import re
from dataclasses import dataclass

from ..diagnostics import BuildError


# ---------------------------------------------------------------------------- the tree
@dataclass(frozen=True)
class Const:
    """A literal. ``type`` is its canonical type (an integer that fits is ``int32``)."""

    type: str
    value: object


@dataclass(frozen=True)
class Local:
    """A name bound by an earlier ``let``."""

    name: str


@dataclass(frozen=True)
class Ref:
    """``param.x``, ``state.x``, ``msg.a.b`` or ``req.a``: a root and a path below it."""

    root: str
    path: tuple


@dataclass(frozen=True)
class Unary:
    """``-x`` or ``!x``."""

    operator: str
    operand: object


@dataclass(frozen=True)
class Binary:
    """``left operator right`` for an arithmetic, comparison or logical operator."""

    operator: str
    left: object
    right: object


@dataclass(frozen=True)
class Select:
    """``condition ? if_true : if_false``"""

    condition: object
    if_true: object
    if_false: object


@dataclass(frozen=True)
class Index:
    """``base[index]``"""

    base: object
    index: object


@dataclass(frozen=True)
class Call:
    """A call of a built-in function."""

    name: str
    arguments: tuple


@dataclass(frozen=True)
class Let:
    """Statement ``let name = value``."""

    name: str
    value: object


@dataclass(frozen=True)
class Assign:
    """Statement ``state.name = value``."""

    name: str
    value: object


# ---------------------------------------------------------------------------- tokens
_TOKEN = re.compile(
    r"""\s*(?:
        (?P<float>\d+\.\d+(?:[eE][+-]?\d+)?)
      | (?P<int>\d+)
      | (?P<string>"(?:[^"\\]|\\["\\n])*")
      | (?P<identifier>[A-Za-z_]\w*)
      | (?P<operator>\|\||&&|==|!=|<=|>=|[<>+\-*/%!?:()\[\],.=])
    )""",
    re.VERBOSE,
)
_STRING_ESCAPES = (('\\"', '"'), ("\\n", "\n"), ("\\\\", "\\"))
_INT32_LIMIT = 2**31


def _syntax_error(message: str) -> BuildError:
    return BuildError("ERR_SYN_002", message)


def _tokenize(text: str) -> list[tuple[str, str]]:
    """(kind, text) pairs, ending with an ``eof`` token."""
    tokens, position = [], 0
    while position < len(text) and text[position:].strip():
        match = _TOKEN.match(text, position)
        if not match or match.end() == position:
            raise _syntax_error(f"bad token near {text[position : position + 10]!r}")
        position = match.end()
        tokens.append((match.lastgroup, match.group(match.lastgroup)))
    return [*tokens, ("eof", "")]


# ---------------------------------------------------------------------------- parser
class _Parser:
    """Recursive-descent parser; one method per level of the grammar in SPEC-02 section 3."""

    def __init__(self, text: str):
        self.tokens = _tokenize(text)
        self.position = 0

    def peek(self) -> tuple[str, str]:
        return self.tokens[self.position]

    def at(self, text: str) -> bool:
        """True if the next token is the operator or keyword ``text``."""
        kind, value = self.peek()
        return value == text and kind in ("operator", "identifier")

    def take(self, expected: str | None = None) -> str:
        """Consume the next token and return its text; fail if it is not ``expected``."""
        _, value = self.peek()
        if expected is not None and value != expected:
            raise _syntax_error(f"expected {expected!r}, found {value!r}")
        self.position += 1
        return value

    def statement(self):
        if self.at("let"):
            self.take()
            name = self.take()
            self.take("=")
            return Let(name, self.expression())
        if self.at("state"):
            self.take()
            self.take(".")
            name = self.take()
            self.take("=")
            return Assign(name, self.expression())
        raise _syntax_error("a statement starts with `let` or `state.`")

    def expression(self):
        condition = self._logical_or()
        if self.at("?"):
            self.take()
            if_true = self.expression()
            self.take(":")
            return Select(condition, if_true, self.expression())
        return condition

    def _left_associative(self, operand, operators):
        """``operand (op operand)*`` for the given binary operators."""
        left = operand()
        while self.peek()[0] == "operator" and self.peek()[1] in operators:
            operator = self.take()
            left = Binary(operator, left, operand())
        return left

    def _logical_or(self):
        return self._left_associative(self._logical_and, ("||",))

    def _logical_and(self):
        return self._left_associative(self._equality, ("&&",))

    def _equality(self):
        return self._left_associative(self._relational, ("==", "!="))

    def _relational(self):
        return self._left_associative(self._additive, ("<", "<=", ">", ">="))

    def _additive(self):
        return self._left_associative(self._multiplicative, ("+", "-"))

    def _multiplicative(self):
        return self._left_associative(self._unary, ("*", "/", "%"))

    def _unary(self):
        if self.peek()[0] == "operator" and self.peek()[1] in ("!", "-"):
            operator = self.take()
            return Unary(operator, self._unary())
        value = self._primary()
        while self.at("["):
            self.take()
            index = self.expression()
            self.take("]")
            value = Index(value, index)
        return value

    def _primary(self):
        kind, text = self.peek()
        if kind == "float":
            self.take()
            return Const("float64", float(text))
        if kind == "int":
            self.take()
            number = int(text)
            return Const("int32" if number < _INT32_LIMIT else "int64", number)
        if kind == "string":
            self.take()
            return Const("string", _unescape(text[1:-1]))
        if kind == "identifier" and text in ("true", "false"):
            self.take()
            return Const("bool", text == "true")
        if kind == "operator" and text == "(":
            self.take()
            inner = self.expression()
            self.take(")")
            return inner
        if kind == "identifier":
            self.take()
            if self.at("("):
                return self._call(text)
            if text in ("param", "msg", "req", "state"):
                return self._reference(text)
            return Local(text)
        raise _syntax_error(f"unexpected {text!r}")

    def _call(self, name: str) -> Call:
        self.take("(")
        arguments = []
        if not self.at(")"):
            arguments.append(self.expression())
            while self.at(","):
                self.take()
                arguments.append(self.expression())
        self.take(")")
        return Call(name, tuple(arguments))

    def _reference(self, root: str) -> Ref:
        path = []
        while self.at("."):
            self.take()
            path.append(self.take())
        return Ref(root, tuple(path))


def _unescape(text: str) -> str:
    for escaped, plain in _STRING_ESCAPES:
        text = text.replace(escaped, plain)
    return text


def _parse(text: str, rule: str):
    parser = _Parser(text)
    tree = getattr(parser, rule)()
    if parser.peek()[0] != "eof":
        raise _syntax_error(f"unexpected tokens after the end of {text!r}")
    return tree


def parse_statement(text: str):
    """Parse one entry of ``expressions``: a ``Let`` or an ``Assign``."""
    return _parse(text, "statement")


def parse_expression(text: str):
    """Parse a single expression (an ``output_mapping`` value or a parameter constraint)."""
    return _parse(text, "expression")
