"""Stage 1 - read the manifest file (TOML, YAML 1.2 or JSON) into plain Python data.

Besides parsing, this stage enforces the format rules of SPEC-01 section 2: no duplicate
keys, no ``null``, no date-times, no YAML anchors/aliases/merge keys/custom tags, and only
JSON-style numbers (no ``0x10`` or ``1_000``). Violations are ``ERR_SYN_001``.

``load_manifest`` is the only public entry point. It reports every violation it can find
and still returns the document (so the schema check can add its own findings); it returns
``None`` only when the file cannot be parsed at all.
"""

import datetime
import json
import re
import tomllib
from collections.abc import Callable
from pathlib import Path

import yaml

from ..diagnostics import BuildError, Report

# Number spellings that JSON does not have: hex/octal/binary, a leading '+', inf/nan, '1_000'.
_NON_JSON_NUMBER = re.compile(r"^(0[xob][0-9a-fA-F_]+|\+\S|[+-]?(inf|nan)\b|\d[\d]*_\d)")
# The value part of a TOML ``key = value`` line.
_TOML_ASSIGNMENT = re.compile(r"^\s*[\w\"'.-]+\s*=\s*([^#]*)")


def _syntax_error(message: str, line: int | None = None, column: int | None = None) -> BuildError:
    return BuildError("ERR_SYN_001", message, line=line, column=column)


def _report_null_and_dates(value, path: tuple, report: Report) -> None:
    """Report every ``null`` and date-time in the document, with the path to each."""
    if value is None or isinstance(value, datetime.date | datetime.time):
        where = ".".join(map(str, path)) or "<root>"
        message = f"{where}: null and date-time values are not allowed"
        report.error("ERR_SYN_001", message, path)
    elif isinstance(value, dict):
        for key, child in value.items():
            _report_null_and_dates(child, (*path, key), report)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _report_null_and_dates(child, (*path, index), report)


# ---------------------------------------------------------------------------------- TOML
def _load_toml(text: str, report: Report) -> dict:
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        position = re.search(r"\(at line (\d+), column (\d+)\)", str(error))
        line_and_column = map(int, position.groups()) if position else ()
        raise _syntax_error(re.sub(r" \(at .*\)$", "", str(error)), *line_and_column) from error
    # tomllib accepts 0x10 and 1_000, which JSON does not: look at the raw text.
    for line_number, line in enumerate(text.splitlines(), start=1):
        assignment = _TOML_ASSIGNMENT.match(line)
        if assignment and _NON_JSON_NUMBER.match(assignment.group(1).strip()):
            report.error(
                "ERR_SYN_001",
                f"number form not allowed: {assignment.group(1).strip()}",
                line=line_number,
                column=assignment.start(1) + 1,
            )
    return document


# ---------------------------------------------------------------------------------- YAML
class _Yaml12Loader(yaml.SafeLoader):
    """YAML 1.2 core rules: only ``true``/``false`` are booleans (``no``/``off`` stay strings),
    and a duplicate key is an error."""

    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(key_node, deep=True) for key_node, _ in node.value]
        if len(keys) != len(set(keys)):
            raise _syntax_error("duplicate key")
        return super().construct_mapping(node, deep)


_YAML_BOOL_TAG = "tag:yaml.org,2002:bool"
_Yaml12Loader.yaml_implicit_resolvers = {
    first_char: [(tag, regexp) for tag, regexp in resolvers if tag != _YAML_BOOL_TAG]
    for first_char, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Yaml12Loader.add_implicit_resolver(
    _YAML_BOOL_TAG, re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF")
)


def _check_yaml_events(text: str, report: Report) -> None:
    """Report anchors, aliases, merge keys, custom tags and non-JSON numbers."""
    for event in yaml.parse(text, Loader=_Yaml12Loader):
        where = {"line": event.start_mark.line + 1, "column": event.start_mark.column + 1}
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
            report.error("ERR_SYN_001", "YAML anchors and aliases are not allowed", **where)
        if getattr(event, "tag", None) and event.tag.startswith("!"):
            report.error("ERR_SYN_001", "custom YAML tags are not allowed", **where)
        if isinstance(event, yaml.ScalarEvent) and event.implicit[0] and not event.tag:
            if event.value == "<<":
                report.error("ERR_SYN_001", "YAML merge keys are not allowed", **where)
            elif _NON_JSON_NUMBER.match(event.value):
                report.error("ERR_SYN_001", f"number form not allowed: {event.value}", **where)


def _load_yaml(text: str, report: Report) -> dict | None:
    try:
        _check_yaml_events(text, report)
        if report.errors:
            return None  # loading would only repeat what was just reported
        return yaml.load(text, Loader=_Yaml12Loader)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        line_and_column = (mark.line + 1, mark.column + 1) if mark else ()
        message = getattr(error, "problem", None) or str(error)
        raise _syntax_error(message, *line_and_column) from error


# ---------------------------------------------------------------------------------- JSON
def _load_json(text: str, report: Report) -> dict:
    def reject_duplicates(pairs):
        keys = [key for key, _ in pairs]
        if len(keys) != len(set(keys)):
            raise _syntax_error("duplicate key")
        return dict(pairs)

    def reject_constant(name):  # NaN, Infinity
        raise _syntax_error(f"number form not allowed: {name}")

    try:
        return json.loads(text, object_pairs_hook=reject_duplicates, parse_constant=reject_constant)
    except json.JSONDecodeError as error:
        raise _syntax_error(error.msg, error.lineno, error.colno) from error


_LOADERS: dict[str, Callable[[str, Report], dict | None]] = {
    ".toml": _load_toml,
    ".yaml": _load_yaml,
    ".yml": _load_yaml,
    ".json": _load_json,
}


def load_manifest(path: str | Path, report: Report) -> dict | None:
    """Read a manifest file. Returns the document, or ``None`` if it cannot be parsed at all
    (one ``ERR_SYN_001`` is reported). Number forms, anchors, nulls and dates are all
    reported and the document is still returned."""
    path = Path(path)
    loader = _LOADERS.get(path.suffix.lower())
    try:
        if loader is None:
            raise _syntax_error(
                f"unknown manifest format {path.suffix!r} (use .toml, .yaml or .json)"
            )
        document = loader(path.read_text(encoding="utf-8"), report)
        if document is not None or not report.errors:
            _report_null_and_dates(document, (), report)
    except BuildError as failure:
        report.add(failure.diagnostic)
        return None
    return document
