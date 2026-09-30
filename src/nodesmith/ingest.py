"""Stage 1: read TOML, YAML 1.2 or JSON under the format rules of SPEC-01 §2 (ERR_SYN_001)."""
import datetime
import json
import re
import tomllib
from pathlib import Path

import yaml

from .diagnostics import BuildError, Report

_BAD_NUMBER = re.compile(r"^(0[xob][0-9a-fA-F_]+|\+\S|[+-]?(inf|nan)\b|\d[\d]*_\d)")
_TOML_VALUE = re.compile(r"^\s*[\w\"'.-]+\s*=\s*([^#]*)")


def _syn(msg, line=None, column=None): return BuildError("ERR_SYN_001", msg, line=line, column=column)


def _report_null_and_dates(o, path, report):   # `path` is a tuple
    if o is None or isinstance(o, (datetime.datetime, datetime.date, datetime.time)):
        report.error("ERR_SYN_001", f"{'.'.join(map(str, path)) or '<root>'}: null and date-time values are not allowed", path)
    elif isinstance(o, dict):
        for k, v in o.items(): _report_null_and_dates(v, path + (k,), report)
    elif isinstance(o, list):
        for i, v in enumerate(o): _report_null_and_dates(v, path + (i,), report)


def _load_toml(text, report):
    try: doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        at = re.search(r"\(at line (\d+), column (\d+)\)", str(e))
        raise _syn(re.sub(r" \(at .*\)$", "", str(e)), *(map(int, at.groups()) if at else ()))
    for n, line in enumerate(text.splitlines(), 1):
        m = _TOML_VALUE.match(line)
        if m and _BAD_NUMBER.match(m.group(1).strip()): report.error("ERR_SYN_001", f"number form not allowed: {m.group(1).strip()}", line=n, column=m.start(1) + 1)
    return doc


class _Yaml12(yaml.SafeLoader):
    """YAML 1.2 core booleans only (`no`/`off` stay strings); duplicate keys are rejected."""

    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(k, deep=True) for k, _ in node.value]
        if len(keys) != len(set(keys)): raise _syn("duplicate key")
        return super().construct_mapping(node, deep)


_Yaml12.yaml_implicit_resolvers = {k: [(t, r) for t, r in v if t != "tag:yaml.org,2002:bool"]
                                   for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()}
_Yaml12.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))


def _load_yaml(text, report):
    try:
        for ev in yaml.parse(text, Loader=_Yaml12):
            at = dict(line=ev.start_mark.line + 1, column=ev.start_mark.column + 1)
            if isinstance(ev, yaml.AliasEvent) or getattr(ev, "anchor", None): report.error("ERR_SYN_001", "YAML anchors and aliases are not allowed", **at)
            if getattr(ev, "tag", None) and ev.tag.startswith("!"): report.error("ERR_SYN_001", "custom YAML tags are not allowed", **at)
            if isinstance(ev, yaml.ScalarEvent) and ev.implicit[0] and not ev.tag:
                if ev.value == "<<": report.error("ERR_SYN_001", "YAML merge keys are not allowed", **at)
                elif _BAD_NUMBER.match(ev.value): report.error("ERR_SYN_001", f"number form not allowed: {ev.value}", **at)
        return None if report.errors else yaml.load(text, Loader=_Yaml12)    # the parser would only repeat what was reported
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        raise _syn(getattr(e, "problem", None) or str(e), *((mark.line + 1, mark.column + 1) if mark else ()))


def _load_json(text, report):
    def pairs(items):
        keys = [k for k, _ in items]
        if len(keys) != len(set(keys)): raise _syn("duplicate key")
        return dict(items)
    def constant(c): raise _syn(f"number form not allowed: {c}")
    try: return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except json.JSONDecodeError as e: raise _syn(e.msg, e.lineno, e.colno)


_LOADERS = {".toml": _load_toml, ".yaml": _load_yaml, ".yml": _load_yaml, ".json": _load_json}


def load_manifest(path, report: Report):
    """Returns the document, or None when it cannot be parsed at all (one ERR_SYN_001). Number forms, anchors, nulls and dates are all
    reported and the document is still returned, so the schema check can report its own errors too."""
    path = Path(path)
    loader = _LOADERS.get(path.suffix.lower())
    try:
        if loader is None: raise _syn(f"unknown manifest format {path.suffix!r} (use .toml, .yaml or .json)")
        doc = loader(path.read_text(encoding="utf-8"), report)
        if doc is not None or not report.errors: _report_null_and_dates(doc, (), report)
    except BuildError as e:
        report.add(e.diagnostic)
        return None
    return doc
