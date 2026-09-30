"""Reference front end for the conformance corpus: Stage 3/4 of SPEC-00 for the core model, written only from the specs
(SPEC-00/01/02/12) and schemas. It is the executable evidence behind conformance/expected/. It is not the product:
the real nodesmith front end (milestone M1) must reproduce its output, not reuse its code."""
import hashlib, json, re, copy

class Diag(Exception):
    def __init__(self, code, msg): super().__init__(f"{code}: {msg}"); self.code = code

# ---------------- interface declarations (SPEC-01 section 4.8 / 7): the only source of field names and types
CAN = {"bool": "bool", "byte": "int32", "char": "int32", "int8": "int32", "uint8": "int32", "int16": "int32", "uint16": "int32", "int32": "int32",
       "uint32": "int64", "int64": "int64", "uint64": "int64", "float32": "float32", "float64": "float64", "string": "string", "time": "float64", "duration": "float64"}
def canon(ros):
    if ros in ("byte[]", "uint8[]"): return "bytes"
    return CAN[ros[:-2]] + "[]" if ros.endswith("[]") else CAN[ros]
def tree(fieldmap):
    """dotted leaf paths -> nested dict of canonical types; a path may not be both leaf and prefix (ERR_SEM_112)."""
    root = {}
    for path in sorted(fieldmap):
        cur, parts = root, path.split(".")
        for p in parts[:-1]:
            if p in cur and not isinstance(cur[p], dict): raise Diag("ERR_SEM_112", f"{p} is both a field and a prefix")
            cur = cur.setdefault(p, {})
        if parts[-1] in cur: raise Diag("ERR_SEM_112", f"{path} is both a field and a prefix")
        cur[parts[-1]] = canon(fieldmap[path])
    return root
WARN = []
RESERVED = {"let", "state", "param", "msg", "req", "res", "true", "false"}
FUNCS = {  # name -> (arity, result rule)
 "abs": (1, "same"), "min": (2, "common"), "max": (2, "common"), "clamp": (3, "common"), "sqrt": (1, "float64"),
 "sin": (1, "float64"), "cos": (1, "float64"), "pow": (2, "float64"), "low_pass": (3, "float64"), "deadband": (2, "float64"),
 "rate_limit": (4, "float64"), "len": (1, "int64"), "to_int32": (1, "int32"), "to_int64": (1, "int64"),
 "to_float32": (1, "float32"), "to_float64": (1, "float64"), "now_sec": (0, "float64"), "dt_sec": (0, "float64")}
RESERVED |= set(FUNCS)
import keyword
KW = set(keyword.kwlist) | {"asm", "auto", "class", "delete", "double", "float", "int", "long", "namespace", "new", "operator", "private", "public", "short", "struct", "template", "this", "typename", "union", "virtual", "void", "volatile"}  # SPEC-01 4.2
NUM = ["int32", "int64", "float32", "float64"]

# ---------------- tokenizer / parser (SPEC-02 section 3)
TOK = re.compile(r'\s*(?:(?P<float>\d+\.\d+(?:[eE][+-]?\d+)?)|(?P<int>\d+)|(?P<str>"(?:[^"\\]|\\["\\n])*")|(?P<id>[A-Za-z_]\w*)|(?P<op>\|\||&&|==|!=|<=|>=|[<>+\-*/%!?:()\[\],.=]))')
def lex(s):
    out, i = [], 0
    while i < len(s):
        if s[i:].strip() == "": break
        m = TOK.match(s, i)
        if not m or m.end() == i: raise Diag("ERR_SYN_002", f"bad token at {s[i:i+10]!r}")
        i = m.end(); k = m.lastgroup; out.append((k, m.group(k)))
    return out + [("eof", "")]

class P:
    def __init__(s, text): s.t = lex(text); s.i = 0
    def peek(s): return s.t[s.i]
    def eat(s, v=None):
        k, x = s.t[s.i]
        if v is not None and x != v: raise Diag("ERR_SYN_002", f"expected {v!r} got {x!r}")
        s.i += 1; return x
    def is_(s, v): return s.t[s.i][1] == v and s.t[s.i][0] in ("op", "id")
    def statement(s):
        if s.is_("let"):
            s.eat(); n = s.eat(); s.eat("="); return ("let", n, s.expr())
        if s.is_("state"):
            s.eat(); s.eat("."); n = s.eat(); s.eat("="); return ("assign", n, s.expr())
        raise Diag("ERR_SYN_002", "statement must start with let or state.")
    def expr(s):
        c = s.logor()
        if s.is_("?"):
            s.eat(); a = s.expr(); s.eat(":"); b = s.expr(); return ("select", c, a, b)
        return c
    def _bin(s, sub, ops):
        l = sub()
        while s.peek()[0] == "op" and s.peek()[1] in ops:
            o = s.eat(); l = ("binary", o, l, sub())
        return l
    def logor(s): return s._bin(s.logand, ("||",))
    def logand(s): return s._bin(s.eq, ("&&",))
    def eq(s): return s._bin(s.rel, ("==", "!="))
    def rel(s): return s._bin(s.add, ("<", "<=", ">", ">="))
    def add(s): return s._bin(s.mul, ("+", "-"))
    def mul(s): return s._bin(s.un, ("*", "/", "%"))
    def un(s):
        if s.peek()[0] == "op" and s.peek()[1] in ("!", "-"): o = s.eat(); return ("unary", o, s.un())
        return s.post()
    def post(s):
        e = s.prim()
        while s.is_("["): s.eat(); i = s.expr(); s.eat("]"); e = ("index", e, i)
        return e
    def prim(s):
        k, v = s.peek()
        if k == "float": s.eat(); return ("const", "float64", float(v))
        if k == "int": s.eat(); n = int(v); return ("const", "int32" if n < 2**31 else "int64", n)
        if k == "str": s.eat(); return ("const", "string", v[1:-1].replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\"))
        if v in ("true", "false") and k == "id": s.eat(); return ("const", "bool", v == "true")
        if v == "(": s.eat(); e = s.expr(); s.eat(")"); return e
        if k == "id":
            s.eat()
            if s.is_("("):
                s.eat(); a = []
                if not s.is_(")"):
                    a.append(s.expr())
                    while s.is_(","): s.eat(); a.append(s.expr())
                s.eat(")"); return ("call", v, a)
            if v in ("param", "msg", "req", "state"):
                path = []
                while s.is_("."): s.eat(); path.append(s.eat())
                return ("ref", v, path)
            return ("local", v)
        raise Diag("ERR_SYN_002", f"unexpected {v!r}")
def parse_stmt(t):
    p = P(t); r = p.statement()
    if p.peek()[0] != "eof": raise Diag("ERR_SYN_002", f"trailing tokens in {t!r}")
    return r
def parse_expr(t):
    p = P(t); r = p.expr()
    if p.peek()[0] != "eof": raise Diag("ERR_SYN_002", f"trailing tokens in {t!r}")
    return r

# ---------------- types (SPEC-02 section 2)
WIDEN = {("int32", "int64"), ("int32", "float64"), ("float32", "float64"), ("int64", "float64")}
def widens(a, b): return a == b or (a, b) in WIDEN
def common(a, b):
    if a == b: return a
    if a not in NUM or b not in NUM: return None
    for c in ("int32", "int64", "float32", "float64"):
        if widens(a, c) and widens(b, c): return c
    return "float64"

class Lower:
    """One pipeline execution: builds SSA nodes (SPEC-00 3.1)."""
    def __init__(s, m, pipe, trig_iface):
        s.m, s.pipe, s.nodes, s.locals, s.iface = m, pipe, [], {}, trig_iface
        s.params = {k: v["type"] for k, v in m.get("parameters", {}).items()}
        s.state = {k: v["type"] for k, v in m.get("state_variables", {}).items()}
        s.reads, s.writes, s.uses_dt = set(), set(), False
        s.kind = pipe["trigger"]["type"]
    def node(s, op, ty, operands=(), attrs=None, label=None):
        # SPEC-00 3.1: no deduplication; ids are creation order (post-order, operands left to right, widening calls before the operator)
        n = {"id": f"n{len(s.nodes)}", "op": op, "type_symbol": ty, "operands": list(operands), "_key": None}
        if attrs: n["attrs"] = attrs
        if label: n["label"] = label
        s.nodes.append(n); return n["id"], ty
    def coerce(s, x, target):  # implicit widening becomes an explicit to_* call (SPEC-00 3.1)
        nid, ty = x
        if ty == target: return x
        if not widens(ty, target): raise Diag("ERR_SEM_102", f"cannot implicitly convert {ty} to {target}")
        return s.node("call", target, [nid], {"function": "to_" + target})
    def lit_fit(s, e, want):  # literal adapts to a same-kind narrower sink (SPEC-02 2.2)
        if e[0] == "const" and e[1] == "int32" and want in ("int32", "int64"): return ("const", want, e[2])
        if e[0] == "const" and e[1] == "float64" and want in ("float32", "float64"): return ("const", want, e[2])
        return e
    def resolve_field(s, root, path):
        cur = s.iface
        for p in path:
            if not isinstance(cur, dict) or p not in cur: raise Diag("ERR_SEM_107", f"no field {'.'.join(path)}")
            cur = cur[p]
        if isinstance(cur, dict): raise Diag("ERR_SEM_102", "nested message is not a value")
        return cur
    def expr(s, e, want=None):
        k = e[0]
        if k == "const":
            e = s.lit_fit(e, want) if want else e
            return s.node("const", e[1], [], {"value": e[2]})
        if k == "local":
            if e[1] == getattr(s, "defining", None): raise Diag("ERR_SEM_103", f"{e[1]} references itself")
            if e[1] not in s.locals: raise Diag("ERR_SEM_101", f"undefined local {e[1]}")
            return s.locals[e[1]]
        if k == "ref":
            root, path = e[1], e[2]
            if root == "param":
                if len(path) != 1 or path[0] not in s.params: raise Diag("ERR_SEM_101", f"unknown parameter {path}")
                return s.node("param", s.params[path[0]], [], {"name": path[0]})
            if root == "state":
                if len(path) != 1 or path[0] not in s.state: raise Diag("ERR_SEM_101", f"unknown state {path}")
                s.reads.add(path[0]); return s.node("state", s.state[path[0]], [], {"name": path[0]})
            need = {"msg": "subscriber", "req": "service"}[root]
            if s.kind != need: raise Diag("ERR_SEM_108", f"{root}. used under a {s.kind} trigger")
            return s.node("input", s.resolve_field(root, path), [], {"root": root, "path": ".".join(path)})
        if k == "unary":
            n, ty = s.expr(e[2])
            if e[1] == "!":
                if ty != "bool": raise Diag("ERR_SEM_102", "! needs bool")
            elif ty not in NUM: raise Diag("ERR_SEM_102", "unary - needs a number")
            return s.node("unary", ty, [n], {"operator": e[1]})
        if k == "binary":
            o = e[1]; a, b = s.expr(e[2]), s.expr(e[3])
            if o in ("&&", "||"):
                if a[1] != "bool" or b[1] != "bool": raise Diag("ERR_SEM_102", f"{o} needs bool")
                return s.node("binary", "bool", [a[0], b[0]], {"operator": o})
            c = common(a[1], b[1])
            if c is None: raise Diag("ERR_SEM_102", f"{o} on {a[1]} and {b[1]}")
            if o == "%" and c not in ("int32", "int64"): raise Diag("ERR_SEM_102", "% needs integers")
            if o in ("/", "%") and e[3][0] == "const" and e[2][0] == "const" and e[3][2] == 0: raise Diag("ERR_SEM_109", "constant division by zero")
            a, b = s.coerce(a, c), s.coerce(b, c)
            rt = "bool" if o in ("==", "!=", "<", "<=", ">", ">=") else c
            return s.node("binary", rt, [a[0], b[0]], {"operator": o})
        if k == "select":
            c = s.expr(e[1])
            if c[1] != "bool": raise Diag("ERR_SEM_102", "?: condition must be bool")
            a, b = s.expr(e[2], want), s.expr(e[3], want)
            t = common(a[1], b[1]) if a[1] != b[1] else a[1]
            if t is None: raise Diag("ERR_SEM_102", "?: branches differ")
            a, b = s.coerce(a, t), s.coerce(b, t)
            return s.node("select", t, [c[0], a[0], b[0]])
        if k == "index":
            b, i = s.expr(e[1]), s.expr(e[2])
            if not b[1].endswith("[]") or i[1] not in ("int32", "int64"): raise Diag("ERR_SEM_102", "bad index")
            return s.node("index", b[1][:-2], [b[0], i[0]])
        if k == "call":
            n, args = e[1], e[2]
            if n not in FUNCS: raise Diag("ERR_SEM_101", f"unknown function {n}")
            ar, rule = FUNCS[n]
            if len(args) != ar: raise Diag("ERR_SEM_102", f"{n} takes {ar} args")
            if n in ("dt_sec", "rate_limit"): s.uses_dt = True
            av = [s.expr(a) for a in args]
            if rule == "same": rt = av[0][1]
            elif rule == "common":
                rt = av[0][1]
                for x in av[1:]: rt = common(rt, x[1])
            else: rt = rule
            if n in ("abs", "min", "max", "clamp", "sqrt", "sin", "cos", "pow", "low_pass", "deadband", "rate_limit") and any(x[1] not in NUM for x in av): raise Diag("ERR_SEM_102", f"{n} needs numbers")
            if rule == "common": av = [s.coerce(x, rt) for x in av]
            elif rule == "float64" and n not in ("now_sec", "dt_sec"): av = [s.coerce(x, "float64") for x in av]
            return s.node("call", rt, [x[0] for x in av], {"function": n})
        raise AssertionError(k)
    def stmt(s, st):
        if st[0] == "let":
            if st[1] in s.locals or st[1] in RESERVED: raise Diag("ERR_SEM_104", f"duplicate/reserved local {st[1]}")
            s.defining = st[1]; r = s.expr(st[2]); s.defining = None; s.locals[st[1]] = r
            for n in s.nodes:
                if n["id"] == r[0] and "label" not in n: n["label"] = st[1]
        else:
            if st[1] not in s.state: raise Diag("ERR_SEM_101", f"unknown state {st[1]}")
            if st[1] in s.writes: raise Diag("ERR_SEM_104", f"state.{st[1]} assigned twice")
            s.writes.add(st[1]); s.staged = getattr(s, "staged", {})
            r = s.expr(st[2], s.state[st[1]]); r = s.coerce(r, s.state[st[1]]); s.staged[st[1]] = r[0]

def lower_manifest(m):
    """Stage 3/4 of SPEC-00 for the core model. Returns the IR dict (before schema validation)."""
    n = m["node"]
    for coll in ("publishers", "subscribers", "services"):
        pass
    ids = [e["id"] for c in ("publishers", "subscribers", "services") for e in m.get(c, [])]
    for i in ids + [p["name"] for p in m.get("pipelines", [])] + list(m.get("parameters", {})) + list(m.get("state_variables", {})):
        if i in RESERVED or i in KW: raise Diag("ERR_SEM_104", f"reserved identifier {i}")
    if len(ids) != len(set(ids)): raise Diag("ERR_SEM_104", "duplicate endpoint id")
    subs = {e["id"]: e for e in m.get("subscribers", [])}; srvs = {e["id"]: e for e in m.get("services", [])}
    pubs = {e["id"]: e for e in m.get("publishers", [])}
    WARN.clear(); decl = m.get("interfaces", {})
    def msg_tree(ty): return tree(decl.get(ty, {}).get("fields", {}))
    def srv_tree(ty, side): return tree(decl.get(ty, {}).get(side, {}))
    for ty in decl:   # validate every declaration up front (ERR_SEM_112) and flag unused ones (warning ERR_SEM_113)
        (tree(decl[ty].get("fields", {})) if "/msg/" in ty else (srv_tree(ty, "request"), srv_tree(ty, "response")))
    used_types = {e["type"] for e in list(pubs.values()) + list(subs.values()) + list(srvs.values())}
    WARN.extend(f"ERR_SEM_113 {ty}" for ty in sorted(set(decl) - used_types))
    # feature rules that depend on declarations (SPEC-01 5.1 rules 1 and 3)
    for e in list(pubs.values()) + list(subs.values()):
        if e.get("zero_copy") and not decl.get(e["type"], {}).get("fixed_size", False): raise Diag("ERR_SHM_002", f"{e['id']}: {e['type']} not declared fixed_size")
    for fi in m.get("simulation", {}).get("fault_injection", []):
        if fi["target"] not in pubs: raise Diag("ERR_SEM_105", "fault target")
        if fi["type"] == "bit_flip" and not decl.get(pubs[fi["target"]]["type"], {}).get("fixed_size", False): raise Diag("ERR_SIM_003", "bit_flip needs fixed_size")
    # defaults (SPEC-01 4.3): value must match type; validation bounds
    params = []
    for k, v in sorted(m.get("parameters", {}).items()):
        ty, d = v["type"], v["default"]
        ok = {"bool": isinstance(d, bool), "string": isinstance(d, str), "int32": isinstance(d, int) and not isinstance(d, bool), "int64": isinstance(d, int) and not isinstance(d, bool),
              "float32": isinstance(d, (int, float)) and not isinstance(d, bool), "float64": isinstance(d, (int, float)) and not isinstance(d, bool)}.get(ty, isinstance(d, list))
        if not ok or (ty == "float64" and False): raise Diag("ERR_SEM_102", f"default of {k} is not {ty}")
        if ty in ("float32", "float64"): d = float(d)
        val = v.get("validation", {})
        if ("min" in val and d < val["min"]) or ("max" in val and d > val["max"]): raise Diag("ERR_SEM_106", f"default of {k} out of bounds")
        if "step" in val and ty in NUM and abs((d - val.get("min", 0)) / val["step"] - round((d - val.get("min", 0)) / val["step"])) > 1e-9: raise Diag("ERR_SEM_106", f"default of {k} not aligned to step")
        if "one_of" in val and d not in val["one_of"]: raise Diag("ERR_SEM_106", f"default of {k} not in one_of")
        if "fixed_length" in val and isinstance(d, list) and len(d) != val["fixed_length"]: raise Diag("ERR_SEM_106", f"default of {k} has the wrong length")
        p = {"name": k, "canonical_type": ty, "default_value": d, "read_only": v.get("read_only", False)}
        if "description" in v: p["description"] = v["description"]
        if val: p["validation"] = val
        params.append(p)
    states = [{"name": k, "canonical_type": v["type"], "initial_value": float(v["initial_value"]) if v["type"] in ("float32", "float64") else v["initial_value"]} for k, v in sorted(m.get("state_variables", {}).items())]
    def qos(e):
        q = e.get("qos", {}); return {"history": q.get("history", "keep_last"), "depth": q.get("depth", 10), "reliability": q.get("reliability", "reliable"), "durability": q.get("durability", "volatile")}
    def chan(e): return {"identifier": e["id"], "topic": e["topic"], "type_symbol": e["type"], "zero_copy": e.get("zero_copy", False), "qos": qos(e)}
    constraints = []
    for c in m.get("parameter_constraints", []):
        L = Lower(m, {"trigger": {"type": "startup"}}, {}); r = L.expr(parse_expr(c["expression"]))
        if r[1] != "bool": raise Diag("ERR_SEM_102", "constraint must be bool")
        if any(x["op"] in ("state", "input") for x in L.nodes): raise Diag("ERR_SEM_108", "constraint may reference only param.")
        constraints.append({"root_node_id": r[0], "nodes": [{k: v for k, v in x.items() if k != "_key"} for x in L.nodes], **({"message": c["message"]} if "message" in c else {})})
    dags, access, used_subs, used_srv = [], {}, set(), {}
    outs_seen = {}
    for p in m.get("pipelines", []):
        t = p["trigger"]; kind = t["type"]; iface = {}
        if kind == "subscriber":
            if t["source"] not in subs: raise Diag("ERR_SEM_105", f"trigger source {t['source']}")
            iface = msg_tree(subs[t["source"]]["type"]); used_subs.add(t["source"])
        elif kind == "service":
            if t["source"] not in srvs: raise Diag("ERR_SEM_105", f"trigger source {t['source']}")
            iface = srv_tree(srvs[t["source"]]["type"], "request"); used_srv[t["source"]] = used_srv.get(t["source"], 0) + 1
        L = Lower(m, p, iface)
        for x in p.get("expressions", []): L.stmt(parse_stmt(x))
        assigns, seen = [], set()
        for tgt in sorted(p.get("output_mapping", {})):
            if tgt in seen: raise Diag("ERR_SEM_104", "duplicate target")
            seen.add(tgt); root, _, rest = tgt.partition(".")
            if root == "res":
                if kind != "service": raise Diag("ERR_SEM_108", "res. outside service pipeline")
                fields = srv_tree(srvs[t["source"]]["type"], "response")
            else:
                if root not in pubs: raise Diag("ERR_SEM_105", f"unknown publisher {root}")
                fields = msg_tree(pubs[root]["type"])
            cur = fields
            for part in rest.split("."):
                if not isinstance(cur, dict) or part not in cur: raise Diag("ERR_SEM_107", f"no field {tgt}")
                cur = cur[part]
            if isinstance(cur, dict): raise Diag("ERR_SEM_102", "target is a nested message")
            r = L.expr(parse_expr(p["output_mapping"][tgt]), cur); r = L.coerce(r, cur)
            assigns.append({"target_field_path": tgt, "source_node_id": r[0]})
        dag = {"id": p["name"], "trigger": {"kind": kind}, "nodes": [{k: v for k, v in x.items() if k != "_key"} for x in L.nodes],
               "state_writes": [{"state_name": k, "source_node_id": v} for k, v in sorted(getattr(L, "staged", {}).items())], "assignments": assigns}
        if kind in ("subscriber", "service"): dag["trigger"]["source_id"] = t["source"]
        if kind == "timer": dag["trigger"].update(period_ms=t["period_ms"], clock=t.get("clock", "steady"))
        if "callback_group" in p: dag["_explicit"] = p["callback_group"]
        dags.append(dag); access[p["name"]] = (L.reads, L.writes, L.uses_dt)
    for sid in subs:
        pass
    for sid in srvs:
        if used_srv.get(sid, 0) != 1: raise Diag("ERR_SEM_105", f"service {sid} needs exactly one pipeline")
    # ---- callback groups (SPEC-12 section 3)
    names = sorted(access); par = {x: x for x in names}
    def f(x):
        while par[x] != x: par[x] = par[par[x]]; x = par[x]
        return x
    for a in names:
        for b in names:
            Ra, Wa, _ = access[a]; Rb, Wb, _ = access[b]
            if a < b and (Wa & (Rb | Wb) or Wb & (Ra | Wa)): par[f(a)] = f(b)
    comps = {}
    for x in names: comps.setdefault(f(x), []).append(x)
    rtg = {g["name"]: g["type"] for g in m.get("realtime", {}).get("callback_groups", [])}
    assign, groups, k = {}, {}, 0
    for c in sorted(comps.values(), key=lambda c: c[0]):
        named = {d["_explicit"] for d in dags if d["id"] in c and "_explicit" in d}
        stateless = [x for x in c if not (access[x][0] or access[x][1] or access[x][2])]
        stateful = len(stateless) != len(c)
        if len(named) > 1: raise Diag("ERR_CNC_201", f"component {c} names several groups")
        if named:
            g = next(iter(named))
            if g not in rtg: raise Diag("ERR_SEM_105", f"unknown callback_group {g}")
            if stateful and rtg[g] != "MutuallyExclusive": raise Diag("ERR_CNC_201", "stateful pipeline in Reentrant group")
            for x in c: assign[x] = g
            groups[g] = (rtg[g], True)
        elif stateful:
            k += 1
            for x in c: assign[x] = f"state_domain_{k}"
            groups[f"state_domain_{k}"] = ("MutuallyExclusive", False)
        else:
            for x in c: assign[x] = f"pipeline_{x}"; groups[f"pipeline_{x}"] = ("MutuallyExclusive", False)
    for d in dags: d["callback_group"] = assign[d["id"]]; d.pop("_explicit", None)
    threads = m.get("concurrency", {}).get("threads", 1)
    ex = m.get("realtime", {}).get("executor", {}).get("type") or ("MultiThreadedExecutor" if threads > 1 else "SingleThreadedExecutor")
    if threads > 1 and ex != "MultiThreadedExecutor": raise Diag("ERR_RT_002", "threads>1 needs MultiThreadedExecutor")
    ir = {"ir_version": "1.0.0", "node_meta": {"name": n["name"], "namespace": n["namespace"], "version": n.get("version", "0.1.0"), **({"description": n["description"]} if "description" in n else {})},
          "interfaces": {"publishers": [chan(e) for e in sorted(pubs.values(), key=lambda e: e["id"])], "subscribers": [chan(e) for e in sorted(subs.values(), key=lambda e: e["id"])],
                         "services": [{"identifier": e["id"], "service_name": e["name"], "type_symbol": e["type"]} for e in sorted(srvs.values(), key=lambda e: e["id"])]},
          "interface_types": [ ({"type_symbol": ty, "kind": "msg", "fixed_size": d.get("fixed_size", False), "fields": dict(sorted(d.get("fields", {}).items()))} if "/msg/" in ty
                                else {"type_symbol": ty, "kind": "srv", "request": dict(sorted(d.get("request", {}).items())), "response": dict(sorted(d.get("response", {}).items()))}) for ty, d in sorted(decl.items())],
          "parameters": params, "parameter_constraints": constraints, "state_buffers": states,
          "execution_dags": sorted(dags, key=lambda d: d["id"]),
          "concurrency": {"executor": ex, "threads": threads, "callback_groups": [{"name": g, "type": t, "explicit": e} for g, (t, e) in sorted(groups.items())]}}
    return ir

def canonical(ir): return json.dumps(ir, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
def ir_hash(ir): return hashlib.sha256(canonical(ir).encode()).hexdigest()
