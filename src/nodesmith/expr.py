"""SPEC-02: expression tokenizer, parser and the type rules the lowering needs.

Expressions are tuples: ("const", type, value) ("local", name) ("ref", root, [path]) ("unary", op, e) ("binary", op, l, r)
("select", c, a, b) ("index", e, i) ("call", name, [args]).  Statements: ("let", name, e) and ("assign", state_name, e).
"""
import keyword
import re

from .diagnostics import BuildError

NUMERIC = ("int32", "int64", "float32", "float64")
WIDENING = {("int32", "int64"), ("int32", "float64"), ("float32", "float64"), ("int64", "float64")}
# name -> (arity, result rule): "same" = type of the argument, "common" = common type of all, else a fixed type
FUNCTIONS = {"abs": (1, "same"), "min": (2, "common"), "max": (2, "common"), "clamp": (3, "common"), "sqrt": (1, "float64"),
             "sin": (1, "float64"), "cos": (1, "float64"), "pow": (2, "float64"), "low_pass": (3, "float64"),
             "deadband": (2, "float64"), "rate_limit": (4, "float64"), "len": (1, "int64"), "to_int32": (1, "int32"),
             "to_int64": (1, "int64"), "to_float32": (1, "float32"), "to_float64": (1, "float64"), "now_sec": (0, "float64"),
             "dt_sec": (0, "float64")}
NUMERIC_ONLY = {"abs", "min", "max", "clamp", "sqrt", "sin", "cos", "pow", "low_pass", "deadband", "rate_limit", "to_int32", "to_int64", "to_float32", "to_float64"}
FLOAT_ARGS = {"sqrt", "sin", "cos", "pow", "low_pass", "deadband", "rate_limit"}     # their arguments are widened to float64
RESERVED = {"let", "state", "param", "msg", "req", "res", "true", "false"} | set(FUNCTIONS)
_CPP_KEYWORDS = set("""alignas alignof and and_eq asm auto bitand bitor bool break case catch char class compl concept const consteval
constexpr constinit const_cast continue co_await co_return co_yield decltype default delete do double dynamic_cast else enum explicit
export extern false float for friend goto if inline int long mutable namespace new noexcept not not_eq nullptr operator or or_eq
private protected public register reinterpret_cast requires return short signed sizeof static static_assert static_cast struct
switch template this thread_local throw true try typedef typeid typename union unsigned using virtual void volatile wchar_t while
xor xor_eq""".split())
TARGET_KEYWORDS = _CPP_KEYWORDS | set(keyword.kwlist) | set(keyword.softkwlist)


def syntax(msg): return BuildError("ERR_SYN_002", msg)


# ---------------------------------------------------------------- tokenizer and parser (SPEC-02 §3)
_TOKEN = re.compile(r'\s*(?:(?P<float>\d+\.\d+(?:[eE][+-]?\d+)?)|(?P<int>\d+)|(?P<str>"(?:[^"\\]|\\["\\n])*")'
                    r'|(?P<id>[A-Za-z_]\w*)|(?P<op>\|\||&&|==|!=|<=|>=|[<>+\-*/%!?:()\[\],.=]))')


def _tokenize(s):
    out, i = [], 0
    while i < len(s):
        if not s[i:].strip(): break
        m = _TOKEN.match(s, i)
        if not m or m.end() == i: raise syntax(f"bad token near {s[i:i + 10]!r}")
        i, kind = m.end(), m.lastgroup
        out.append((kind, m.group(kind)))
    return out + [("eof", "")]


class _Parser:
    def __init__(self, text): self.toks, self.i = _tokenize(text), 0

    def peek(self): return self.toks[self.i]
    def at(self, v): return self.toks[self.i][1] == v and self.toks[self.i][0] in ("op", "id")

    def eat(self, v=None):
        _, x = self.toks[self.i]
        if v is not None and x != v: raise syntax(f"expected {v!r}, found {x!r}")
        self.i += 1
        return x

    def statement(self):
        if self.at("let"):
            self.eat(); name = self.eat(); self.eat("="); return ("let", name, self.expr())
        if self.at("state"):
            self.eat(); self.eat("."); name = self.eat(); self.eat("="); return ("assign", name, self.expr())
        raise syntax("a statement starts with `let` or `state.`")

    def expr(self):
        cond = self._binary(self._and, ("||",))
        if self.at("?"):
            self.eat(); a = self.expr(); self.eat(":"); b = self.expr()
            return ("select", cond, a, b)
        return cond

    def _binary(self, operand, ops):
        left = operand()
        while self.peek()[0] == "op" and self.peek()[1] in ops:
            op = self.eat(); left = ("binary", op, left, operand())
        return left

    def _and(self): return self._binary(self._eq, ("&&",))
    def _eq(self): return self._binary(self._rel, ("==", "!="))
    def _rel(self): return self._binary(self._add, ("<", "<=", ">", ">="))
    def _add(self): return self._binary(self._mul, ("+", "-"))
    def _mul(self): return self._binary(self._unary, ("*", "/", "%"))

    def _unary(self):
        if self.peek()[0] == "op" and self.peek()[1] in ("!", "-"):
            op = self.eat(); return ("unary", op, self._unary())
        e = self._primary()
        while self.at("["):
            self.eat(); idx = self.expr(); self.eat("]"); e = ("index", e, idx)
        return e

    def _primary(self):
        kind, v = self.peek()
        if kind == "float": self.eat(); return ("const", "float64", float(v))
        if kind == "int": self.eat(); n = int(v); return ("const", "int32" if n < 2 ** 31 else "int64", n)
        if kind == "str": self.eat(); return ("const", "string", v[1:-1].replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\"))
        if kind == "id" and v in ("true", "false"): self.eat(); return ("const", "bool", v == "true")
        if v == "(" and kind == "op": self.eat(); e = self.expr(); self.eat(")"); return e
        if kind == "id":
            self.eat()
            if self.at("("):
                self.eat(); args = []
                if not self.at(")"):
                    args.append(self.expr())
                    while self.at(","): self.eat(); args.append(self.expr())
                self.eat(")")
                return ("call", v, args)
            if v in ("param", "msg", "req", "state"):
                path = []
                while self.at("."): self.eat(); path.append(self.eat())
                return ("ref", v, path)
            return ("local", v)
        raise syntax(f"unexpected {v!r}")


def _parse(text, rule):
    p = _Parser(text)
    tree = getattr(p, rule)()
    if p.peek()[0] != "eof": raise syntax(f"unexpected tokens after the end of {text!r}")
    return tree


def parse_statement(text): return _parse(text, "statement")
def parse_expression(text): return _parse(text, "expr")


# ---------------------------------------------------------------- types (SPEC-02 §2)
def widens(a, b): return a == b or (a, b) in WIDENING


def common_type(a, b):
    if a == b: return a
    if a not in NUMERIC or b not in NUMERIC: return None
    return next((c for c in NUMERIC if widens(a, c) and widens(b, c)), "float64")
