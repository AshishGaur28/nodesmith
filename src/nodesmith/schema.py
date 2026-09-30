"""Stage 2: schema check (ERR_SYN_002), `x-` key removal, and default materialization (SPEC-00 §5.3)."""
import json
import os
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator


EXTENSIONS = ("lifecycle", "shared_memory", "security", "telemetry", "realtime", "simulation", "concurrency")


@lru_cache(maxsize=None)
def _schema(name):
    # ponytail: schemas are read from the repo checkout; bundle them as package data when nodesmith is packaged.
    root = Path(os.environ.get("ROS2_GEN_SCHEMAS") or Path(__file__).resolve().parents[2] / "schemas")
    return json.loads((root / f"{name}.schema.json").read_text(encoding="utf-8"))


def manifest_schema(): return _schema("node_manifest")
def ir_schema(): return _schema("node_ir")


@lru_cache(maxsize=None)
def _validator(): return Draft202012Validator(manifest_schema())


def check(manifest: dict, report) -> None:
    """Reports every schema violation, in document order, as ERR_SYN_002."""
    seen = set()
    for e in sorted(_validator().iter_errors(manifest), key=lambda e: [str(p) for p in e.path]):
        where = "/".join(str(p) for p in e.path) or "<root>"
        if e.validator == "additionalProperties":
            known = set(e.schema.get("properties", {}))
            extra = sorted(k for k in e.instance if k not in known and not k.startswith("x-"))
            msg = f"{where}: unknown key{'s' if len(extra) > 1 else ''} {', '.join(map(repr, extra))} (keys starting with x- are reserved for tooling)"
        else:
            msg = f"{where}: {e.message[:160]}"
        if msg not in seen:
            seen.add(msg)
            report.error("ERR_SYN_002", msg)


def strip_x(o):
    """Keys starting with `x-` are for tooling: accepted anywhere, never lowered (SPEC-01 §2)."""
    if isinstance(o, dict): return {k: strip_x(v) for k, v in o.items() if not k.startswith("x-")}
    if isinstance(o, list): return [strip_x(v) for v in o]
    return o


def _def(sch): return manifest_schema()["$defs"][sch["$ref"].split("/")[-1]] if "$ref" in sch else sch


def _has_default(sch):
    sch = _def(sch)
    return sch.get("type") == "object" and any("default" in _def(p) or _has_default(p) for p in sch.get("properties", {}).values())


def fill(inst, sch):
    """Spelled-out defaults and omitted defaults give the same value; an absent optional object with defaulted members is materialized."""
    sch = _def(sch)
    if isinstance(inst, dict) and sch.get("type") == "object":
        props, out = sch.get("properties", {}), {}
        for k, v in inst.items(): out[k] = fill(v, props[k]) if k in props else v
        for k, p in props.items():
            if k in out: continue
            if "default" in _def(p): out[k] = _def(p)["default"]
            elif _has_default(p): out[k] = fill({}, p)
        return out
    if isinstance(inst, list) and "items" in sch: return [fill(x, sch["items"]) for x in inst]
    return inst


def effective_extensions(manifest: dict) -> dict:
    """Extension blocks present in the manifest, with schema defaults filled in."""
    props = manifest_schema()["properties"]
    return {k: fill(manifest[k], props[k]) for k in EXTENSIONS if k in manifest}
