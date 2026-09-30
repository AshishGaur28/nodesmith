#!/usr/bin/env python3
"""Conformance corpus runner (see conformance/README.md).

    python conformance/verify.py            check every case against the expectations
    python conformance/verify.py --update   rewrite conformance/expected/ from the reference front end

Needs: python 3.11+, jsonschema, pyyaml.
"""
import copy, datetime, glob, json, os, re, sys, tomllib
import yaml
from jsonschema import Draft202012Validator as V

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "reference"))
import front
from front import Diag, lower_manifest, ir_hash, canonical

MAN = json.load(open(f"{ROOT}/schemas/node_manifest.schema.json"))
IRS = json.load(open(f"{ROOT}/schemas/node_ir.schema.json"))
VMAN, VIR = V(MAN), V(IRS)
DEFS = MAN["$defs"]
EXT = ["lifecycle", "shared_memory", "security", "telemetry", "realtime", "simulation", "concurrency"]
PYTHON_EXT = [k for k in EXT if k != "concurrency"]
RMW_BACKENDS = {"fastrtps": {"rmw_default"}}
SIZES = {"bool": 1, "byte": 1, "char": 1, "int8": 1, "uint8": 1, "int16": 2, "uint16": 2, "int32": 4, "uint32": 4, "float32": 4,
         "int64": 8, "uint64": 8, "float64": 8, "time": 8, "duration": 8}
RESTRICTED = {"/rosout", "/parameter_events", "/clock", "/ros_discovery_info"}


# ---------------------------------------------------------------- Stage 1: ingestion (SPEC-01 §2)
class Bad(Exception):
    def __init__(self, code, msg): super().__init__(msg); self.code = code


NUM_BAD = re.compile(r"^(0[xob][0-9a-fA-F_]+|\+\S|[+-]?(inf|nan)\b|\d[\d]*_\d)")


def no_nulls_or_dates(o):
    if o is None or isinstance(o, (datetime.datetime, datetime.date, datetime.time)): raise Bad("ERR_SYN_001", "null or date-time")
    if isinstance(o, dict):
        for v in o.values(): no_nulls_or_dates(v)
    elif isinstance(o, list):
        for v in o: no_nulls_or_dates(v)
    return o


def load_toml(path):
    text = open(path, encoding="utf-8").read()
    for line in text.splitlines():
        m = re.match(r"^\s*[\w\"'.-]+\s*=\s*([^#]*)", line)
        if m and NUM_BAD.match(m.group(1).strip()): raise Bad("ERR_SYN_001", f"number form not allowed: {m.group(1).strip()}")
    try: return no_nulls_or_dates(tomllib.loads(text))
    except tomllib.TOMLDecodeError as e: raise Bad("ERR_SYN_001", str(e))


class Yaml12(yaml.SafeLoader):
    """YAML 1.2 core booleans only; anchors, aliases, merge keys and duplicate keys are rejected."""
Yaml12.yaml_implicit_resolvers = {k: [(t, r) for t, r in v if t != "tag:yaml.org,2002:bool"] for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()}
Yaml12.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))


def load_yaml(path):
    text = open(path, encoding="utf-8").read()
    for ev in yaml.parse(text, Loader=Yaml12):
        if isinstance(ev, yaml.AliasEvent) or getattr(ev, "anchor", None): raise Bad("ERR_SYN_001", "YAML anchor or alias")
        if isinstance(ev, yaml.ScalarEvent) and ev.implicit[0] and not ev.tag:
            if ev.value == "<<": raise Bad("ERR_SYN_001", "YAML merge key")
            if NUM_BAD.match(ev.value): raise Bad("ERR_SYN_001", f"number form not allowed: {ev.value}")
        if getattr(ev, "tag", None) and ev.tag.startswith("!"): raise Bad("ERR_SYN_001", "custom YAML tag")
    def construct(loader, node):
        keys = [loader.construct_object(k) for k, _ in node.value]
        if len(keys) != len(set(keys)): raise Bad("ERR_SYN_001", "duplicate key")
        return loader.construct_mapping(node)
    Yaml12.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct)
    try: return no_nulls_or_dates(yaml.load(text, Loader=Yaml12))
    except yaml.YAMLError as e: raise Bad("ERR_SYN_001", str(e))


def load_json(path):
    def pairs(ps):
        ks = [k for k, _ in ps]
        if len(ks) != len(set(ks)): raise Bad("ERR_SYN_001", "duplicate key")
        return dict(ps)
    def const(c): raise Bad("ERR_SYN_001", f"number form not allowed: {c}")
    try: return no_nulls_or_dates(json.load(open(path, encoding="utf-8"), object_pairs_hook=pairs, parse_constant=const))
    except json.JSONDecodeError as e: raise Bad("ERR_SYN_001", str(e))


LOADERS = {".toml": load_toml, ".yaml": load_yaml, ".yml": load_yaml, ".json": load_json}


def strip_x(o):
    """`x-` keys are accepted anywhere, ignored by every stage and never lowered (SPEC-01 §2)."""
    if isinstance(o, dict): return {k: strip_x(v) for k, v in o.items() if not k.startswith("x-")}
    if isinstance(o, list): return [strip_x(v) for v in o]
    return o


# ---------------------------------------------------------------- defaults (SPEC-00 §5.3)
def _d(sch): return DEFS[sch["$ref"].split("/")[-1]] if "$ref" in sch else sch
def _has_default(sch):
    sch = _d(sch)
    return sch.get("type") == "object" and any("default" in _d(p) or _has_default(p) for p in sch.get("properties", {}).values())
def fill(inst, sch):
    sch = _d(sch)
    if isinstance(inst, dict) and sch.get("type") == "object":
        props, out = sch.get("properties", {}), {}
        for k, v in inst.items(): out[k] = fill(v, props[k]) if k in props else v
        for k, p in props.items():
            if k in out: continue
            if "default" in _d(p): out[k] = _d(p)["default"]
            elif _has_default(p): out[k] = fill({}, p)
        return out
    if isinstance(inst, list) and "items" in sch: return [fill(x, sch["items"]) for x in inst]
    return inst


# ---------------------------------------------------------------- Stage 2/3: schema and cross-block rules (SPEC-01 §5.1)
def cross_block(m, mode):
    """Returns the first violated cross-block rule as a code, or None."""
    node, decl = m["node"], m.get("interfaces", {})
    ext = {k: fill(m[k], MAN["properties"][k]) for k in EXT if k in m}
    eps = [(e, k) for k in ("publishers", "subscribers") for e in m.get(k, [])]
    sm = ext.get("shared_memory")
    for e, _ in eps:                                                             # rule 1
        if not e.get("zero_copy"): continue
        q = e.get("qos", {})
        if (node["target_language"] != "cpp" or not (sm and sm["enabled"]) or q.get("history", "keep_last") != "keep_last"
                or q.get("durability", "volatile") != "volatile"
                or ("max_loaned_messages" in sm and e.get("loan_pool_capacity", 1) > sm["max_loaned_messages"])
                or sm["middleware_backend"] not in RMW_BACKENDS[node.get("rmw", "fastrtps")]): return "ERR_SHM_001"
    for e, _ in eps:                                                             # ERR_SHM_002 / 003
        if not e.get("zero_copy"): continue
        d = decl.get(e["type"], {})
        if not d.get("fixed_size", False): return "ERR_SHM_002"
        size = sum(SIZES[t] for t in d.get("fields", {}).values())
        if "segment_size_bytes" in sm and e.get("loan_pool_capacity", 1) * size > sm["segment_size_bytes"]: return "ERR_SHM_003"
    rt = ext.get("realtime")
    if any("callback_group" in p for p in m.get("pipelines", [])) and not (rt and rt["enabled"]): return "ERR_RT_002"   # rule 2
    sim = ext.get("simulation")
    if sim and sim["enabled"] and sim["use_sim_time"]:                           # rule 3
        if any(p["trigger"]["type"] == "timer" and p["trigger"].get("clock", "steady") != "ros" for p in m.get("pipelines", [])): return "ERR_SIM_002"
    if mode == "release" and sim and sim["enabled"]:                             # rule 4
        if not sim["enabled_in_production"] or any(not f["enabled_in_production"] for f in sim["fault_injection"]): return "ERR_SIM_001"
    sec = ext.get("security")
    if sec and sec["enabled"]:                                                   # rule 6 and SPEC-08 §5
        if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", seg) or seg in (".", "..") for seg in sec["enclave"][1:].split("/")): return "ERR_SEC_302"
        perms = sec.get("permissions", {})
        for d in ("publish", "subscribe"):
            seen = {}
            for p in perms.get(d, []):
                if seen.get(p["topic"], p["allow"]) != p["allow"]: return "ERR_SEC_304"
                seen[p["topic"]] = p["allow"]
        derived = {"publish": {e["topic"] for e in m.get("publishers", [])}, "subscribe": {e["topic"] for e in m.get("subscribers", [])}}
        for d in ("publish", "subscribe"):
            if any(not p["allow"] and p["topic"] not in derived[d] for p in perms.get(d, [])): return "ERR_SEC_304"
        granted = {p["topic"] for p in perms.get("publish", []) if p["allow"]}
        for e in m.get("publishers", []):
            if (e["topic"] in RESTRICTED or e["topic"].lstrip("/").startswith("_")) and e["topic"] not in granted: return "ERR_SEC_303"
    if node["target_language"] == "python" and any(k in m and ext[k]["enabled"] for k in PYTHON_EXT): return "ERR_SEM_114"   # rule 7
    return None


def process(path, mode="debug"):
    """Runs Stages 1-4. Returns (ir, warnings) or raises Bad/Diag carrying the error code."""
    m = LOADERS[os.path.splitext(path)[1]](path)
    errs = sorted(VMAN.iter_errors(m), key=lambda e: list(map(str, e.path)))
    if errs: raise Bad("ERR_SYN_002", errs[0].message[:120])
    m = strip_x(m)
    code = cross_block(m, mode)
    if code: raise Bad(code, "cross-block rule")
    ir = lower_manifest(m)
    ir["extensions"] = {k: fill(m[k], MAN["properties"][k]) for k in sorted(EXT) if k in m}
    used = {p["trigger"].get("source") for p in m.get("pipelines", [])}
    warns = [w.split()[0] for w in front.WARN] + ["ERR_SEM_110" for s_ in m.get("subscribers", []) if s_["id"] not in used]
    return ir, warns


# ---------------------------------------------------------------- corpus
def header(path):
    """Expectation from the file name: <cat><nnn>_<slug> = ERR_<CAT>_<NNN>; `warn_` in the slug = accepted with that warning;
    `release` in the slug = built with --mode=release."""
    name = os.path.basename(path)
    m = re.match(r"([a-z]+)(\d{3})_(.*)\.\w+$", name)
    if not m: return {}
    code = f"ERR_{m.group(1).upper()}_{m.group(2)}"
    return {"expect": ("warn=" + code) if "warn_" in m.group(3) else code, "mode": "release" if "release" in m.group(3) else "debug"}


def main(update):
    bad, passed = 0, 0
    def check(ok, msg):
        nonlocal bad, passed
        print(("PASS " if ok else "FAIL ") + msg); bad += not ok; passed += ok
    # 1. valid manifests: IR validates, hash matches the recorded one, other formats give the same hash
    for f in sorted(glob.glob(f"{ROOT}/examples/*.toml")):
        name = os.path.basename(f)[:-5]
        try: ir, warns = process(f)
        except (Bad, Diag) as e: check(False, f"{name}: rejected ({e.code})"); continue
        errs = [e.message[:80] for e in VIR.iter_errors(ir)]
        check(not errs, f"{name}: IR validates against node_ir.schema.json {errs or ''}")
        h = ir_hash(ir)
        if update:
            json.dump(ir, open(f"{HERE}/expected/{name}.ir.json", "w"), indent=2, sort_keys=True); open(f"{HERE}/expected/{name}.ir.json", "a").write("\n")
            open(f"{HERE}/expected/{name}.sha256", "w").write(h + "\n")
        else:
            check(json.load(open(f"{HERE}/expected/{name}.ir.json")) == json.loads(canonical(ir)), f"{name}: IR equals expected/{name}.ir.json")
            check(open(f"{HERE}/expected/{name}.sha256").read().strip() == h, f"{name}: IR hash equals expected/{name}.sha256 ({h[:12]})")
        check(canonical(ir) == canonical(json.loads(canonical(ir))), f"{name}: canonical form is a fixed point")
        for alt in sorted(glob.glob(f"{HERE}/parity/{name}.*")):
            try: check(ir_hash(process(alt)[0]) == h, f"{name}: {os.path.basename(alt)} gives the same IR hash")
            except (Bad, Diag) as e: check(False, f"{name}: {os.path.basename(alt)} rejected ({e.code})")
    # 2. invalid manifests: each is rejected with exactly the code in its header
    codes = set()
    for f in sorted(glob.glob(f"{HERE}/invalid/*")):
        h, name = header(f), os.path.basename(f)
        exp = h.get("expect")
        if exp is None: check(False, f"{name}: file name must start with <cat><nnn>_"); continue
        try: ir, warns = process(f, h.get("mode", "debug")); got = None
        except (Bad, Diag) as e: got, warns = e.code, []
        if exp.startswith("warn="):
            check(got is None and exp[5:] in warns, f"{name}: accepted with warning {exp[5:]}" + ("" if got is None else f" (got {got})"))
        else:
            check(got == exp, f"{name}: rejected with {exp}" + ("" if got == exp else f" (got {got})"))
        codes.add(exp.replace("warn=", ""))
    # 2b. multi-error manifests: the reference reports only the first error; it must be the first of the expected list
    for name, exp_codes in sorted(json.load(open(f"{HERE}/multi/expected.json")).items()):
        try: process(f"{HERE}/multi/{name}"); got = None
        except (Bad, Diag) as e: got = e.code
        check(got == exp_codes[0], f"multi/{name}: reference first error is {exp_codes[0]}" + ("" if got == exp_codes[0] else f" (got {got})"))
    # 3. manifests embedded in the specs are accepted (keeps the prose and the rules from drifting apart)
    import tempfile
    for f in sorted(glob.glob(f"{ROOT}/docs/specs/SPEC-*.adoc")):
        for i, b in enumerate(re.findall(r"\[source,toml\]\n----\n(.*?)\n----", open(f, encoding="utf-8").read(), re.S)):
            if "[node]" not in b: continue
            with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as t: t.write(b)
            try: process(t.name); ok = True
            except (Bad, Diag) as e: ok = False
            finally: os.remove(t.name)
            check(ok, f"{os.path.basename(f)[:7]} embedded manifest #{i} is accepted")
    # 4. every build-time code that can be triggered by a manifest has a case
    want = {"ERR_SYN_001", "ERR_SYN_002", "ERR_SEM_101", "ERR_SEM_102", "ERR_SEM_103", "ERR_SEM_104", "ERR_SEM_105", "ERR_SEM_106", "ERR_SEM_107",
            "ERR_SEM_108", "ERR_SEM_109", "ERR_SEM_110", "ERR_SEM_112", "ERR_SEM_113", "ERR_SEM_114", "ERR_CNC_201", "ERR_SEC_302", "ERR_SEC_303",
            "ERR_SEC_304", "ERR_SHM_001", "ERR_SHM_002", "ERR_SHM_003", "ERR_RT_002", "ERR_SIM_001", "ERR_SIM_002", "ERR_SIM_003"}
    check(not (want - codes), f"every manifest-triggerable build code has a case (missing: {sorted(want - codes) or 'none'})")
    print(f"\n{passed} passed, {bad} failed")
    return bad


if __name__ == "__main__":
    sys.exit(1 if main("--update" in sys.argv) else 0)
