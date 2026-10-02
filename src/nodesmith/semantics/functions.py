"""User functions: logic the manifest declares and the user implements (SPEC-01 4.9, SPEC-02 5.1).

The manifest never contains the body. It only declares a name, typed arguments and a return
type, so that every call in an expression can be checked and the generator can produce the
interface the user implements.
"""

from dataclasses import dataclass

from ..diagnostics import BuildError, Report
from .language import RESERVED_WORDS, TARGET_KEYWORDS


@dataclass(frozen=True)
class UserFunction:
    """A declared function: its name, ordered ``(argument name, canonical type)`` pairs, and
    return type."""

    name: str
    arguments: tuple[tuple[str, str], ...]
    returns: str

    @property
    def argument_types(self) -> list[str]:
        """The canonical types of the arguments, in order."""
        return [type_ for _, type_ in self.arguments]


def _check_name(name: str, where: tuple, what: str) -> None:
    """A function or argument name must not be a reserved word or a target-language keyword."""
    if name in RESERVED_WORDS or name in TARGET_KEYWORDS:
        raise BuildError("ERR_SEM_104", f"{what} {name!r} is a reserved word", where)


def lower_functions(manifest: dict, report: Report) -> dict[str, UserFunction]:
    """The valid function declarations by name. A declaration with an error is reported and
    left out; calls to it then report an unknown function, which is the truth of the matter."""
    functions: dict[str, UserFunction] = {}
    for index, declaration in enumerate(manifest.get("functions", [])):
        name = declaration["name"]
        with report.guard(("functions", index, "name")):
            _check_name(name, ("functions", index, "name"), "function")
            if name in functions:
                raise BuildError("ERR_SEM_104", f"duplicate function {name!r}")
            arguments = _lower_arguments(declaration, index)
            functions[name] = UserFunction(name, arguments, declaration["returns"])
    return functions


def _lower_arguments(declaration: dict, index: int) -> tuple[tuple[str, str], ...]:
    """The ordered ``(name, type)`` pairs; argument names are valid and unique."""
    seen: set[str] = set()
    arguments = []
    for position, argument in enumerate(declaration.get("arguments", [])):
        where = ("functions", index, "arguments", position, "name")
        name = argument["name"]
        _check_name(name, where, "argument")
        if name in seen:
            raise BuildError("ERR_SEM_104", f"duplicate argument {name!r}", where)
        seen.add(name)
        arguments.append((name, argument["type"]))
    return tuple(arguments)


def function_entries(functions: dict[str, UserFunction], manifest: dict) -> list[dict]:
    """The IR's ``functions`` list: sorted by name, with the manifest's descriptions."""
    descriptions = {d["name"]: d.get("description") for d in manifest.get("functions", [])}
    entries = []
    for name in sorted(functions):
        function = functions[name]
        entry = {
            "name": name,
            "arguments": [{"name": n, "type_symbol": t} for n, t in function.arguments],
            "returns": function.returns,
        }
        if descriptions.get(name):
            entry["description"] = descriptions[name]
        entries.append(entry)
    return entries
