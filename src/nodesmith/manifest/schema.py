"""Stage 2 - check the manifest against the JSON Schema, and fill in defaults.

The schema (``schemas/node_manifest.schema.json``) is the normative definition of the
manifest's structure. This module reports every violation (``ERR_SYN_002``), removes the
``x-`` keys that are reserved for tooling (SPEC-01 section 2), and materializes defaults so
that a manifest which spells a default out gives the same IR as one that omits it
(SPEC-00 section 5.3).
"""

import json
import os
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator

from ..diagnostics import Report

# The optional top-level blocks that belong to a feature rather than to the core model.
EXTENSIONS = (
    "lifecycle",
    "shared_memory",
    "security",
    "telemetry",
    "realtime",
    "simulation",
    "concurrency",
)


@cache
def _schema(name: str) -> dict:
    # Read from the repository checkout (or ``ROS2_GEN_SCHEMAS``). When nodesmith is packaged
    # for installation, the schemas must be bundled as package data instead.
    default_directory = Path(__file__).resolve().parents[3] / "schemas"
    directory = Path(os.environ.get("ROS2_GEN_SCHEMAS") or default_directory)
    return json.loads((directory / f"{name}.schema.json").read_text(encoding="utf-8"))


def manifest_schema() -> dict:
    """The manifest JSON Schema, loaded once."""
    return _schema("node_manifest")


def ir_schema() -> dict:
    """The IR JSON Schema, loaded once."""
    return _schema("node_ir")


@cache
def _validator() -> Draft202012Validator:
    return Draft202012Validator(manifest_schema())


def _unknown_key_message(where: str, error) -> tuple[str, list[str]]:
    """Message and offending keys for an ``additionalProperties`` violation."""
    known = set(error.schema.get("properties", {}))
    unknown = sorted(k for k in error.instance if k not in known and not k.startswith("x-"))
    plural = "s" if len(unknown) > 1 else ""
    names = ", ".join(map(repr, unknown))
    hint = "keys starting with x- are reserved for tooling"
    return f"{where}: unknown key{plural} {names} ({hint})", unknown


def check(manifest: dict, report: Report) -> None:
    """Report every schema violation, in document order, as ``ERR_SYN_002``."""
    seen: set[str] = set()
    violations = sorted(
        _validator().iter_errors(manifest), key=lambda error: [str(step) for step in error.path]
    )
    for error in violations:
        path = tuple(error.path)
        where = "/".join(str(step) for step in path) or "<root>"
        if error.validator == "additionalProperties":
            message, unknown = _unknown_key_message(where, error)
            path += tuple(unknown[:1])  # point at the first unknown key
        else:
            message = f"{where}: {error.message[:160]}"
        if message not in seen:
            seen.add(message)
            report.error("ERR_SYN_002", message, path)


def strip_x(value):
    """Remove keys starting with ``x-`` everywhere: they are for tooling, accepted anywhere
    and never lowered (SPEC-01 section 2)."""
    if isinstance(value, dict):
        return {k: strip_x(v) for k, v in value.items() if not k.startswith("x-")}
    if isinstance(value, list):
        return [strip_x(item) for item in value]
    return value


def _resolve(schema: dict) -> dict:
    """Follow a ``$ref`` to its definition."""
    if "$ref" in schema:
        return manifest_schema()["$defs"][schema["$ref"].split("/")[-1]]
    return schema


def _contains_default(schema: dict) -> bool:
    """True if the object schema, or one nested inside it, declares a default."""
    schema = _resolve(schema)
    if schema.get("type") != "object":
        return False
    return any(
        "default" in _resolve(sub) or _contains_default(sub)
        for sub in schema.get("properties", {}).values()
    )


def fill(instance, schema: dict):
    """Fill in the schema's defaults.

    A manifest that spells a default out and one that omits it give the same result, and an
    absent optional object that has defaulted members is materialized."""
    schema = _resolve(schema)
    if isinstance(instance, dict) and schema.get("type") == "object":
        properties = schema.get("properties", {})
        filled = {
            key: fill(value, properties[key]) if key in properties else value
            for key, value in instance.items()
        }
        for key, sub_schema in properties.items():
            if key in filled:
                continue
            if "default" in _resolve(sub_schema):
                filled[key] = _resolve(sub_schema)["default"]
            elif _contains_default(sub_schema):
                filled[key] = fill({}, sub_schema)
        return filled
    if isinstance(instance, list) and "items" in schema:
        return [fill(item, schema["items"]) for item in instance]
    return instance


def effective_extensions(manifest: dict) -> dict:
    """The extension blocks present in the manifest, with schema defaults filled in."""
    properties = manifest_schema()["properties"]
    return {name: fill(manifest[name], properties[name]) for name in EXTENSIONS if name in manifest}
