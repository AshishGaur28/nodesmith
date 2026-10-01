"""Stage 4: semantic checks and lowering to the language-neutral IR (SPEC-00, SPEC-01 §6-§7, SPEC-02, SPEC-12 §3)."""
from .diagnostics import BuildError, Cascade
from .constants import Fault, evaluate
from .expr import (FLOAT_ARGS, FUNCTIONS, NUMERIC, NUMERIC_ONLY, RESERVED, TARGET_KEYWORDS, common_type, parse_expression,
                   parse_statement, widens)

IR_VERSION = "1.0.0"
# ROS primitive (as declared under [interfaces]) -> canonical type (SPEC-02 §2.1)
CANONICAL = {"bool": "bool", "byte": "int32", "char": "int32", "int8": "int32", "uint8": "int32", "int16": "int32", "uint16": "int32",
             "int32": "int32", "uint32": "int64", "int64": "int64", "uint64": "int64", "float32": "float32", "float64": "float64",
             "string": "string", "time": "float64", "duration": "float64"}
TRIGGER_INPUT = {"msg": "subscriber", "req": "service"}
_FAILED = object()     # value of a local whose definition already failed and was reported


def err(code, msg, path=()): return BuildError(code, msg, path)


def canonical_type(ros):
    if ros in ("byte[]", "uint8[]"): return "bytes"
    return CANONICAL[ros[:-2]] + "[]" if ros.endswith("[]") else CANONICAL[ros]


def field_tree(fields):
    """Dotted leaf paths -> nested dict of canonical types. A path is a leaf or a prefix, never both (ERR_SEM_112)."""
    root = {}
    for path in sorted(fields):
        cur, parts = root, path.split(".")
        for p in parts[:-1]:
            if p in cur and not isinstance(cur[p], dict): raise err("ERR_SEM_112", f"{p!r} is both a field and a prefix")
            cur = cur.setdefault(p, {})
        if parts[-1] in cur: raise err("ERR_SEM_112", f"{path!r} is both a field and a prefix")
        cur[parts[-1]] = canonical_type(fields[path])
    return root


class PipelineBuilder:
    """Builds the SSA node list of one pipeline (SPEC-00 §3.1): no deduplication, ids in creation order, widening calls before the operator."""

    def __init__(self, manifest, trigger_kind, input_fields):
        self.nodes, self.locals, self.input_fields, self.kind = [], {}, input_fields, trigger_kind
        self.params = {k: v["type"] for k, v in manifest.get("parameters", {}).items()}
        self.state = {k: v["type"] for k, v in manifest.get("state_variables", {}).items()}
        self.state_reads, self.state_writes, self.uses_dt, self.staged, self.defining = set(), set(), False, {}, None

    def node(self, op, ty, operands=(), attrs=None):
        n = {"id": f"n{len(self.nodes)}", "op": op, "type_symbol": ty, "operands": list(operands)}
        if attrs: n["attrs"] = attrs
        self.nodes.append(n)
        return n["id"], ty

    def coerce(self, value, target):
        nid, ty = value
        if ty == target: return value
        if not widens(ty, target): raise err("ERR_SEM_102", f"cannot implicitly convert {ty} to {target}")
        return self.node("call", target, [nid], {"function": "to_" + target})

    @staticmethod
    def fit_literal(e, want):
        if e[0] == "const" and e[1] == "int32" and want in ("int32", "int64"): return ("const", want, e[2])
        if e[0] == "const" and e[1] == "float64" and want in ("float32", "float64"): return ("const", want, e[2])
        return e

    def input_type(self, path):
        cur = self.input_fields
        for p in path:
            if not isinstance(cur, dict) or p not in cur: raise err("ERR_SEM_107", f"field {'.'.join(path)!r} is not declared")
            cur = cur[p]
        if isinstance(cur, dict): raise err("ERR_SEM_102", f"{'.'.join(path)!r} is a nested message, not a value")
        return cur

    def expr(self, e, want=None):
        kind = e[0]
        if kind == "const":
            e = self.fit_literal(e, want) if want else e
            return self.node("const", e[1], [], {"value": e[2]})
        if kind == "local":
            if e[1] == self.defining: raise err("ERR_SEM_103", f"local {e[1]!r} references itself")
            if e[1] not in self.locals: raise err("ERR_SEM_101", f"undefined local {e[1]!r}")
            if self.locals[e[1]] is _FAILED: raise Cascade()
            return self.locals[e[1]]
        if kind == "ref": return self._reference(e)
        if kind == "unary":
            nid, ty = self.expr(e[2])
            if e[1] == "!" and ty != "bool": raise err("ERR_SEM_102", "`!` needs a bool")
            if e[1] == "-" and ty not in NUMERIC: raise err("ERR_SEM_102", "unary `-` needs a number")
            return self.node("unary", ty, [nid], {"operator": e[1]})
        if kind == "binary": return self._binary(e)
        if kind == "select":
            cond = self.expr(e[1])
            if cond[1] != "bool": raise err("ERR_SEM_102", "`?:` condition must be bool")
            a, b = self.expr(e[2], want), self.expr(e[3], want)
            ty = a[1] if a[1] == b[1] else common_type(a[1], b[1])
            if ty is None: raise err("ERR_SEM_102", f"`?:` branches have types {a[1]} and {b[1]}")
            a, b = self.coerce(a, ty), self.coerce(b, ty)
            return self.node("select", ty, [cond[0], a[0], b[0]])
        if kind == "index":
            base, idx = self.expr(e[1]), self.expr(e[2])
            if not base[1].endswith("[]") or idx[1] not in ("int32", "int64"): raise err("ERR_SEM_102", "bad array index")
            return self.node("index", base[1][:-2], [base[0], idx[0]])
        return self._call(e)

    def _reference(self, e):
        root, path = e[1], e[2]
        if root == "param":
            if len(path) != 1 or path[0] not in self.params: raise err("ERR_SEM_101", f"unknown parameter {'.'.join(path)!r}")
            return self.node("param", self.params[path[0]], [], {"name": path[0]})
        if root == "state":
            if len(path) != 1 or path[0] not in self.state: raise err("ERR_SEM_101", f"unknown state variable {'.'.join(path)!r}")
            self.state_reads.add(path[0])
            return self.node("state", self.state[path[0]], [], {"name": path[0]})
        if self.kind != TRIGGER_INPUT[root]: raise err("ERR_SEM_108", f"`{root}.` is not available under a {self.kind} trigger")
        return self.node("input", self.input_type(path), [], {"root": root, "path": ".".join(path)})

    def _binary(self, e):
        op, a, b = e[1], self.expr(e[2]), self.expr(e[3])
        if op in ("&&", "||"):
            if a[1] != "bool" or b[1] != "bool": raise err("ERR_SEM_102", f"`{op}` needs bools")
            return self.node("binary", "bool", [a[0], b[0]], {"operator": op})
        ty = common_type(a[1], b[1])
        if ty is None: raise err("ERR_SEM_102", f"`{op}` on {a[1]} and {b[1]}")
        if op == "%" and ty not in ("int32", "int64"): raise err("ERR_SEM_102", "`%` needs integers")
        if op in ("/", "%") and e[2][0] == "const" and e[3][0] == "const" and e[3][2] == 0: raise err("ERR_SEM_109", "constant division by zero")
        a, b = self.coerce(a, ty), self.coerce(b, ty)
        result = "bool" if op in ("==", "!=", "<", "<=", ">", ">=") else ty
        return self.node("binary", result, [a[0], b[0]], {"operator": op})

    def _call(self, e):
        name, args = e[1], e[2]
        if name not in FUNCTIONS: raise err("ERR_SEM_101", f"unknown function {name!r}")
        arity, rule = FUNCTIONS[name]
        if len(args) != arity: raise err("ERR_SEM_102", f"{name} takes {arity} argument(s)")
        if name in ("dt_sec", "rate_limit"): self.uses_dt = True
        vals = [self.expr(a) for a in args]
        if name in NUMERIC_ONLY and any(v[1] not in NUMERIC for v in vals): raise err("ERR_SEM_102", f"{name} needs numbers")
        if rule == "same": ty = vals[0][1]
        elif rule == "common":
            ty = vals[0][1]
            for v in vals[1:]: ty = common_type(ty, v[1])
            vals = [self.coerce(v, ty) for v in vals]
        else:
            ty = rule
            if name in FLOAT_ARGS: vals = [self.coerce(v, "float64") for v in vals]
            if name == "len" and not (vals[0][1] in ("string", "bytes") or vals[0][1].endswith("[]")): raise err("ERR_SEM_102", "len needs a string, bytes or an array")
        return self.node("call", ty, [v[0] for v in vals], {"function": name})

    def statement(self, st):
        if st[0] == "let":
            if st[1] in self.locals or st[1] in RESERVED: raise err("ERR_SEM_104", f"duplicate or reserved local {st[1]!r}")
            self.defining = st[1]
            try: value = self.expr(st[2])
            except (BuildError, Cascade):
                self.defining, self.locals[st[1]] = None, _FAILED    # later uses stay silent instead of cascading
                raise
            self.defining = None
            self.locals[st[1]] = value
            for n in self.nodes:   # the `let` names the node its expression created
                if n["id"] == value[0] and "label" not in n: n["label"] = st[1]
            return
        name = st[1]
        if name not in self.state: raise err("ERR_SEM_101", f"unknown state variable {name!r}")
        if name in self.state_writes: raise err("ERR_SEM_104", f"state.{name} is assigned twice")
        self.state_writes.add(name)
        self.staged[name] = self.coerce(self.expr(st[2], self.state[name]), self.state[name])[0]


def _check_identifiers(m, report):
    names = [(e["id"], (kind, i, "id")) for kind in ("publishers", "subscribers", "services") for i, e in enumerate(m.get(kind, []))]
    names += [(p["name"], ("pipelines", i, "name")) for i, p in enumerate(m.get("pipelines", []))]
    names += [(n, ("parameters", n)) for n in m.get("parameters", {})] + [(n, ("state_variables", n)) for n in m.get("state_variables", {})]
    for n, path in names:
        with report.guard():
            if n in RESERVED or n in TARGET_KEYWORDS: raise err("ERR_SEM_104", f"{n!r} is a reserved word", path)
    seen = set()
    for n, path in names[:len([1 for k in ("publishers", "subscribers", "services") for _ in m.get(k, [])])]:
        with report.guard():
            if n in seen: raise err("ERR_SEM_104", f"duplicate publisher/subscriber/service id {n!r}", path)
            seen.add(n)


def _parameters(m, report):
    out = []
    for name, p in sorted(m.get("parameters", {}).items()):
        with report.guard():
            ty, d = p["type"], p["default"]
            is_num = isinstance(d, (int, float)) and not isinstance(d, bool)
            ok = {"bool": isinstance(d, bool), "string": isinstance(d, str), "int32": isinstance(d, int) and not isinstance(d, bool),
                  "int64": isinstance(d, int) and not isinstance(d, bool), "float32": is_num, "float64": is_num}.get(ty, isinstance(d, list))
            if not ok: raise err("ERR_SEM_102", f"default of parameter {name!r} is not a {ty}", ("parameters", name, "default"))
            if ty in ("float32", "float64"): d = float(d)
            val = p.get("validation", {})
            where = ("parameters", name, "default")
            if ("min" in val and d < val["min"]) or ("max" in val and d > val["max"]): raise err("ERR_SEM_106", f"default of {name!r} is out of bounds", where)
            if "step" in val and ty in ("int32", "int64", "float32", "float64"):      # aligned to min (0 if absent), SPEC-07 §3.2
                steps = (d - val.get("min", 0)) / val["step"]
                if abs(steps - round(steps)) > 1e-9: raise err("ERR_SEM_106", f"default of {name!r} is not a multiple of step {val['step']}", where)
            if "one_of" in val and d not in val["one_of"]: raise err("ERR_SEM_106", f"default of {name!r} is not one of {val['one_of']}", where)
            if "fixed_length" in val and isinstance(d, list) and len(d) != val["fixed_length"]:
                raise err("ERR_SEM_106", f"default of {name!r} has length {len(d)}, not {val['fixed_length']}", where)
            item = {"name": name, "canonical_type": ty, "default_value": d, "read_only": p.get("read_only", False)}
            if "description" in p: item["description"] = p["description"]
            if val: item["validation"] = val
            out.append(item)
    return out


def _constraints(m, report):
    out = []
    for i, c in enumerate(m.get("parameter_constraints", [])):
        with report.guard():
            path = ("parameter_constraints", i, "expression")
            b = PipelineBuilder(m, "startup", {})
            root = b.expr(parse_expression(c["expression"]))
            if root[1] != "bool": raise err("ERR_SEM_102", "a parameter constraint must be a bool expression", path)
            if any(n["op"] in ("state", "input") for n in b.nodes): raise err("ERR_SEM_108", "a parameter constraint may reference only param.", path)
            out.append({"root_node_id": root[0], "nodes": b.nodes, **({"message": c["message"]} if "message" in c else {})})
    return out


def _check_constraints(m, parameters, constraints, report):
    """SPEC-07 §5 rule 3: every parameter_constraints expression must hold on the defaults."""
    if len(parameters) != len(m.get("parameters", {})) or len(constraints) != len(m.get("parameter_constraints", [])): return   # an earlier error
    defaults = {p["name"]: p["default_value"] for p in parameters}
    for i, (c, src) in enumerate(zip(constraints, m.get("parameter_constraints", []))):
        with report.guard(("parameter_constraints", i, "expression")):
            try: holds = evaluate(c, defaults)
            except Fault as f: raise err("ERR_SEM_106", f"constraint {src['expression']!r} faults on the defaults: {f}")
            if not holds: raise err("ERR_SEM_106", src.get("message") or f"the defaults violate constraint {src['expression']!r}")


def _callback_groups(m, dags, access, report):
    """SPEC-12 §3: pipelines that share state form one component; a component shares one mutually exclusive group."""
    names = sorted(access)
    parent = {n: n for n in names}
    def find(x):
        while parent[x] != x: parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for a in names:
        for b in names:
            (ra, wa), (rb, wb) = access[a][:2], access[b][:2]
            if a < b and (wa & (rb | wb) or wb & (ra | wa)): parent[find(a)] = find(b)
    comps = {}
    for n in names: comps.setdefault(find(n), []).append(n)
    declared = {g["name"]: g["type"] for g in m.get("realtime", {}).get("callback_groups", [])}
    explicit = {p["name"]: p["callback_group"] for p in m.get("pipelines", []) if "callback_group" in p}
    assign, groups, k = {}, {}, 0
    index = {p["name"]: i for i, p in enumerate(m.get("pipelines", []))}
    for comp in sorted(comps.values(), key=lambda c: c[0]):
        named_first = next((x for x in comp if x in explicit), comp[0])
        with report.guard(("pipelines", index[named_first], "callback_group" if named_first in explicit else "name")):
            named = {explicit[x] for x in comp if x in explicit}
            stateful = any(access[x][0] or access[x][1] or access[x][2] for x in comp)
            if len(named) > 1: raise err("ERR_CNC_201", f"pipelines {comp} share state but name several callback groups")
            if named:
                g = next(iter(named))
                if g not in declared:
                    if not declared: raise Cascade()         # no realtime block: ERR_RT_002 already says so
                    raise err("ERR_SEM_105", f"unknown callback_group {g!r}")
                if stateful and declared[g] != "MutuallyExclusive": raise err("ERR_CNC_201", f"stateful pipeline in Reentrant group {g!r}")
                assign.update({x: g for x in comp}); groups[g] = (declared[g], True)
            elif stateful:
                k += 1
                assign.update({x: f"state_domain_{k}" for x in comp}); groups[f"state_domain_{k}"] = ("MutuallyExclusive", False)
            else:
                for x in comp: assign[x] = f"pipeline_{x}"; groups[f"pipeline_{x}"] = ("MutuallyExclusive", False)
    return assign, groups


def _lower_pipeline(m, p, views, report, idx):
    """Returns (dag, access) for one pipeline. Errors in a statement or output target are reported and the rest still checked;
    an error in the trigger abandons the pipeline."""
    subs, srvs, pubs, msg_fields, srv_fields = views
    trig = p["trigger"]; kind = trig["type"]; input_fields = {}
    if kind == "subscriber":
        if trig["source"] not in subs: raise err("ERR_SEM_105", f"trigger source {trig['source']!r} is not a subscriber")
        input_fields = msg_fields(subs[trig["source"]]["type"])
    elif kind == "service":
        if trig["source"] not in srvs: raise err("ERR_SEM_105", f"trigger source {trig['source']!r} is not a service")
        input_fields = srv_fields(srvs[trig["source"]]["type"], "request")
    b = PipelineBuilder(m, kind, input_fields)
    for i, text in enumerate(p.get("expressions", [])):
        with report.guard(("pipelines", idx, "expressions", i)): b.statement(parse_statement(text))
    assignments = []
    for target in sorted(p.get("output_mapping", {})):
        with report.guard(("pipelines", idx, "output_mapping", target)):
            root, _, rest = target.partition(".")
            if root == "res":
                if kind != "service": raise err("ERR_SEM_108", "`res.` is only available in a service pipeline")
                fields = srv_fields(srvs[trig["source"]]["type"], "response")
            else:
                if root not in pubs: raise err("ERR_SEM_105", f"output target {root!r} is not a publisher")
                fields = msg_fields(pubs[root]["type"])
            cur = fields
            for part in rest.split("."):
                if not isinstance(cur, dict) or part not in cur: raise err("ERR_SEM_107", f"output field {target!r} is not declared")
                cur = cur[part]
            if isinstance(cur, dict): raise err("ERR_SEM_102", f"output target {target!r} is a nested message")
            value = b.coerce(b.expr(parse_expression(p["output_mapping"][target]), cur), cur)
            assignments.append({"target_field_path": target, "source_node_id": value[0]})
    dag = {"id": p["name"], "trigger": {"kind": kind}, "nodes": b.nodes,
           "state_writes": [{"state_name": k, "source_node_id": v} for k, v in sorted(b.staged.items())], "assignments": assignments}
    if kind in ("subscriber", "service"): dag["trigger"]["source_id"] = trig["source"]
    if kind == "timer": dag["trigger"].update(period_ms=trig["period_ms"], clock=trig.get("clock", "steady"))
    return dag, (b.state_reads, b.state_writes, b.uses_dt)


def lower(m: dict, ext: dict, report):
    """Reports every semantic and concurrency error it finds (SPEC-04 §3) and returns the IR, or None if there were any.
    `m` has had `x-` keys removed; `ext` is `schema.effective_extensions(m)`."""
    node = m["node"]
    _check_identifiers(m, report)
    pubs = {e["id"]: e for e in m.get("publishers", [])}
    subs = {e["id"]: e for e in m.get("subscribers", [])}
    srvs = {e["id"]: e for e in m.get("services", [])}
    decl = m.get("interfaces", {})
    bad_types = set()                          # declarations already reported as inconsistent: their later uses stay silent

    def msg_fields(ty):
        if ty in bad_types: raise Cascade()
        return field_tree(decl.get(ty, {}).get("fields", {}))

    def srv_fields(ty, side):
        if ty in bad_types: raise Cascade()
        return field_tree(decl.get(ty, {}).get(side, {}))

    for ty in decl:                                                   # validate declarations up front (ERR_SEM_112)
        with report.guard(("interfaces", ty)):
            try:
                if "/msg/" in ty: msg_fields(ty)
                else: srv_fields(ty, "request"), srv_fields(ty, "response")
            except BuildError:
                bad_types.add(ty)
                raise
    used = {e["type"] for e in [*pubs.values(), *subs.values(), *srvs.values()]}
    for ty in sorted(set(decl) - used): report.warn("ERR_SEM_113", f"interface declaration {ty!r} is not used by any endpoint", ("interfaces", ty))
    for i, f in enumerate(ext.get("simulation", {}).get("fault_injection", [])):    # SPEC-01 §5.1 rule 3 (targets and bit_flip)
        with report.guard(("simulation", "fault_injection", i, "target")):
            if f["target"] not in pubs: raise err("ERR_SEM_105", f"fault_injection target {f['target']!r} is not a publisher")
            if f["type"] == "bit_flip" and not decl.get(pubs[f["target"]]["type"], {}).get("fixed_size", False):
                raise err("ERR_SIM_003", f"bit_flip needs {pubs[f['target']]['type']} declared fixed_size")
    parameters, constraints = _parameters(m, report), _constraints(m, report)
    _check_constraints(m, parameters, constraints, report)
    states = [{"name": k, "canonical_type": v["type"],
               "initial_value": float(v["initial_value"]) if v["type"] in ("float32", "float64") else v["initial_value"]}
              for k, v in sorted(m.get("state_variables", {}).items())]

    views = (subs, srvs, pubs, msg_fields, srv_fields)
    dags, access, used_subs, service_uses, pipeline_failed = [], {}, set(), {}, False
    for idx, p in enumerate(m.get("pipelines", [])):
        trig = p["trigger"]
        if trig["type"] == "subscriber": used_subs.add(trig["source"])
        if trig["type"] == "service": service_uses[trig["source"]] = service_uses.get(trig["source"], 0) + 1
        before = len(report.errors)
        with report.guard(("pipelines", idx, "trigger", "source")):
            dag, acc = _lower_pipeline(m, p, views, report, idx)
            dags.append(dag); access[p["name"]] = acc
        pipeline_failed |= len(report.errors) > before
    for i, sid in enumerate(e["id"] for e in m.get("services", [])):
        with report.guard(("services", i)):
            if service_uses.get(sid, 0) != 1: raise err("ERR_SEM_105", f"service {sid!r} must be served by exactly one pipeline")
    for i, sid in enumerate(e["id"] for e in m.get("subscribers", [])):
        if sid not in used_subs: report.warn("ERR_SEM_110", f"subscriber {sid!r} triggers no pipeline", ("subscribers", i))

    # Gate 3 needs every pipeline's state access, so it only runs when all pipelines checked clean (SPEC-04 §3).
    assign, groups = ({}, {}) if pipeline_failed else _callback_groups(m, dags, access, report)
    for d in dags: d["callback_group"] = assign.get(d["id"])
    threads = m.get("concurrency", {}).get("threads", 1)
    executor = ext.get("realtime", {}).get("executor", {}).get("type") or ("MultiThreadedExecutor" if threads > 1 else "SingleThreadedExecutor")
    with report.guard(("concurrency", "threads")):
        if threads > 1 and executor != "MultiThreadedExecutor": raise err("ERR_RT_002", f"concurrency.threads > 1 needs MultiThreadedExecutor, not {executor}")
    if report.errors: return None

    def qos(e):
        q = e.get("qos", {})
        return {"history": q.get("history", "keep_last"), "depth": q.get("depth", 10),
                "reliability": q.get("reliability", "reliable"), "durability": q.get("durability", "volatile")}
    channel = lambda e: {"identifier": e["id"], "topic": e["topic"], "type_symbol": e["type"], "zero_copy": e.get("zero_copy", False), "qos": qos(e)}
    interface_types = [{"type_symbol": ty, "kind": "msg", "fixed_size": d.get("fixed_size", False), "fields": dict(sorted(d.get("fields", {}).items()))}
                       if "/msg/" in ty else
                       {"type_symbol": ty, "kind": "srv", "request": dict(sorted(d.get("request", {}).items())),
                        "response": dict(sorted(d.get("response", {}).items()))}
                       for ty, d in sorted(decl.items())]
    return {"ir_version": IR_VERSION,
            "node_meta": {"name": node["name"], "namespace": node["namespace"], "version": node.get("version", "0.1.0"),
                          **({"description": node["description"]} if "description" in node else {})},
            "interfaces": {"publishers": [channel(e) for _, e in sorted(pubs.items())], "subscribers": [channel(e) for _, e in sorted(subs.items())],
                           "services": [{"identifier": e["id"], "service_name": e["name"], "type_symbol": e["type"]} for _, e in sorted(srvs.items())]},
            "interface_types": interface_types, "parameters": parameters, "parameter_constraints": constraints, "state_buffers": states,
            "execution_dags": sorted(dags, key=lambda d: d["id"]),
            "concurrency": {"executor": executor, "threads": threads,
                            "callback_groups": [{"name": g, "type": t, "explicit": x} for g, (t, x) in sorted(groups.items())]},
            "extensions": {k: ext[k] for k in sorted(ext)}}
