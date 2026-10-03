"""The logic the user supplies, and what the generator writes around it.

A manifest may declare functions (``[[functions]]``, SPEC-01 section 4.9) whose bodies the
user writes as plain C++ functions in a ``logic/`` folder, with no ``main()``, CMake or ROS code.
The generated package is self-contained: the user's files are copied into it and built with it.

The generator writes:

* ``include/<pkg>/logic_api.hpp``: declarations of the functions the manifest asks for, in the
  global namespace ``logic``, plus the optional hooks ``setup()`` and ``teardown()``;
* ``src/logic_defaults.cpp``: weak placeholders for all of them, so a missing function builds
  and faults loudly at run time instead of computing with a made-up value;
* ``logic/*``: a copy of the user's files;
* once, when the user has no ``logic/`` folder yet, a starter ``logic.cpp`` (``starter_files``).

See SPEC-03 section 4.4.
"""

import re
from pathlib import Path

from ...ir.canonical import ir_hash
from .layout import signature
from .naming import cpp_type, member
from .templates import environment

_BY_VALUE = ("bool", "int32", "int64", "float32", "float64")
_SOURCE_SUFFIXES = (".cpp", ".cc", ".cxx", ".hpp", ".h")


def has_user_logic(ir: dict) -> bool:
    """True if the manifest declares functions, so the package needs the user's logic."""
    return bool(ir.get("functions"))


def function_context(ir: dict) -> list[dict]:
    """Template context for the declared functions: C++ return type, and each argument's
    C++ type and name. Scalars are passed by value; strings and arrays by const reference."""
    functions = []
    for function in ir.get("functions", []):
        arguments = []
        for argument in function["arguments"]:
            cpp = cpp_type(argument["type_symbol"])
            by_value = argument["type_symbol"] in _BY_VALUE
            arguments.append(
                {
                    "name": member(argument["name"]),
                    "cpp_type": cpp if by_value else f"const {cpp} &",
                }
            )
        name, returns = function["name"], cpp_type(function["returns"])
        named = [f"{argument['cpp_type']} {argument['name']}" for argument in arguments]
        unnamed = [argument["cpp_type"] for argument in arguments]
        functions.append(
            {
                "name": name,
                "description": function.get("description", ""),
                "returns": returns,
                "arguments": arguments,
                "signature": signature(f"{returns} {name}(", [*named, "bool & fault"], ";"),
                "definition": _definition(returns, name, named),
                "weak_definition": _weak_definition(returns, name, unnamed),
            }
        )
    return functions


def _definition(returns: str, name: str, named: list[str]) -> str:
    """A function of the starter file: it faults until the user implements it."""
    unused = [f"[[maybe_unused]] {parameter}" for parameter in named]
    lines = [signature(f"{returns} {name}(", [*unused, "bool & fault"])]
    lines += [
        f"  fault = true;  // TODO: implement {name}, then delete this line.",
        "  return {};",
        "}",
    ]
    return "\n".join(lines)


def _weak_definition(returns: str, name: str, unnamed: list[str]) -> str:
    """The placeholder that replaces a function the user has not written."""
    head = f"__attribute__((weak)) {returns} {name}("
    return "\n".join(
        [
            signature(head, [*unnamed, "bool & fault"]),
            f'  [[maybe_unused]] static const bool reported = (not_implemented("{name}"), true);',
            "  fault = true;",
            "  return {};",
            "}",
        ]
    )


def _render(template: str, ir: dict) -> str:
    return (
        environment()
        .get_template(template)
        .render(
            ir_hash=ir_hash(ir),
            pkg=ir["node_meta"]["name"],
            functions=function_context(ir),
        )
    )


def logic_api_header(ir: dict) -> str:
    """Text of ``logic_api.hpp``."""
    return _render("logic_api.hpp.j2", ir)


def logic_defaults_source(ir: dict) -> str:
    """Text of ``logic_defaults.cpp``: the weak placeholders."""
    return _render("logic_defaults.cpp.j2", ir)


def starter_files(ir: dict) -> dict[str, str]:
    """The files written once into a new ``logic/`` folder: ``{relative path: text}``."""
    return {"logic.cpp": _render("logic.cpp.j2", ir)}


def read_logic_dir(logic_dir: str | Path) -> dict[str, str]:
    """The user's source files under ``logic_dir`` as ``{relative path: text}``, in path order."""
    root = Path(logic_dir)
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix in _SOURCE_SUFFIXES
    }


def unimplemented(ir: dict, logic_files: dict[str, str]) -> list[str]:
    """Declared functions whose name does not appear as a call-like ``name(`` in the user's
    files. A heuristic for a hint to the user, not a check: the weak placeholder is the safety."""
    text = "\n".join(logic_files.values())
    return [
        f["name"]
        for f in ir.get("functions", [])
        if not re.search(rf"\b{re.escape(f['name'])}\s*\(", text)
    ]
