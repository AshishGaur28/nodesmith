"""Layout of the generated C++: one limit for the line length and the helpers that wrap to it.

The generated code is read by people, so no emitter writes a line longer than ``LIMIT`` columns
(except where a formula inlines into one long expression, which is not broken up). Statements
that would be too long are split one argument per line, and every group of related statements is
separated from the next by a blank line.
"""

LIMIT = 100
CONTINUATION = "    "  # extra indentation of a wrapped line


def call(head: str, arguments: list[str], tail: str = ");", indent: str = "") -> str:
    """``head(arg, arg)tail`` on one line if it fits, else one argument per line.

    ``head`` ends with the opening parenthesis (or ``<T>(``); an argument may itself be several
    lines (a lambda), which are indented as given relative to the continuation."""
    one_line = indent + head + ", ".join(arguments) + tail
    if len(one_line) <= LIMIT and "\n" not in one_line:
        return one_line
    pad = indent + CONTINUATION
    packed = pad + ", ".join(arguments) + tail
    if len(packed) <= LIMIT and "\n" not in packed:
        return f"{indent}{head}\n{packed}"
    lines = [indent + head]
    for position, argument in enumerate(arguments):
        suffix = tail if position == len(arguments) - 1 else ","
        lines.append(_indent_block(argument, pad) + suffix)
    return "\n".join(lines)


def assign(target: str, expression: str, indent: str = "") -> str:
    """``target = expression;`` on one line if it fits, else broken after the ``=`` and, if the
    expression is still too long, at its ternary and logical operators."""
    one_line = f"{indent}{target} = {expression};"
    if len(one_line) <= LIMIT:
        return one_line
    pad = indent + CONTINUATION
    wrapped = wrap_expression(expression + ";", pad, len(pad))
    return f"{indent}{target} =\n{pad}{wrapped}"


_BREAKS = (" ? ", " : ", " && ", " || ")


def _cuts(expression: str) -> list[tuple[int, int]]:
    """(parenthesis depth, index) of each operator at which an expression may be broken."""
    depth, quoted, cuts, i = 0, False, [], 0
    while i < len(expression):
        char = expression[i]
        if quoted:
            if char == "\\":
                i += 1
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif any(expression.startswith(token, i) for token in _BREAKS):
            cuts.append((depth, i))
        i += 1
    return cuts


def wrap_expression(expression: str, indent: str, used: int = 0) -> str:
    """Break a long expression before its outermost ``?``, ``:``, ``&&`` and ``||``.

    ``indent`` is the indentation of the continuation lines and ``used`` the columns already
    taken on the first line. An expression with no such operator is returned unchanged."""
    if used + len(expression) <= LIMIT:
        return expression
    cuts = _cuts(expression)
    if not cuts:
        return expression
    level = min(depth for depth, _ in cuts)
    points = [index for depth, index in cuts if depth == level]
    bounds = [0, *points, len(expression)]
    pieces = [expression[start:end].strip() for start, end in zip(bounds, bounds[1:], strict=False)]
    lines = [wrap_expression(pieces[0], indent + CONTINUATION, used)]
    deeper = indent + CONTINUATION
    lines += [
        deeper + wrap_expression(piece, deeper + CONTINUATION, len(deeper)) for piece in pieces[1:]
    ]
    return "\n".join(lines)


def signature(head: str, parameters: list[str], tail: str = " {", indent: str = "") -> str:
    """A function signature: on one line if it fits, else one parameter per line."""
    return call(head, parameters, ")" + tail, indent)


def _indent_block(text: str, pad: str) -> str:
    first, *rest = text.split("\n")
    return pad + first + "".join("\n" + (pad + line if line else line) for line in rest)


def constructor(name: str, parameters: list[str], initialisers: list[str] | None = None) -> str:
    """The head of a constructor definition up to its ``{``: parameters on the first line if they
    fit, else on one continuation line, else one per line; initialisers on a line of their own."""
    qualified = f"{name}::{name}("
    one_line = qualified + ", ".join(parameters) + ")"
    packed = f"{qualified}\n{CONTINUATION}" + ", ".join(parameters) + ")"
    if len(one_line) <= LIMIT:
        text = one_line
    elif all(len(line) <= LIMIT for line in packed.split("\n")):
        text = packed
    else:
        text = call(qualified, parameters, ")")
    if not initialisers:
        return text + " {"
    one_line_inits = f"{CONTINUATION}: " + ", ".join(initialisers) + " {"
    if len(one_line_inits) <= LIMIT:
        return text + "\n" + one_line_inits
    return text + f"\n{CONTINUATION}: " + f",\n{CONTINUATION}  ".join(initialisers) + " {"
