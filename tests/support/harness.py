"""Differential test of the generated C++ pipelines against tests/support/interpreter.py.

For every pipeline of a lowered manifest: random inputs are written into a C++ program together with the generated
pipelines.hpp, the program is compiled (no ROS needed) and run, and each printed result is compared with the interpreter's.
"""
import math
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

from nodesmith.cpp.generate import package
from nodesmith.cpp.naming import camel, cxx_namespace

from . import interpreter as interp

ROS_CPP = {"bool": "bool", "byte": "std::uint8_t", "char": "std::uint8_t", "int8": "std::int8_t", "uint8": "std::uint8_t", "int16": "std::int16_t",
           "uint16": "std::uint16_t", "int32": "std::int32_t", "uint32": "std::uint32_t", "int64": "std::int64_t", "uint64": "std::uint64_t",
           "float32": "float", "float64": "double", "string": "std::string", "time": "Stamp", "duration": "Stamp"}
CANON_CPP = {"bool": "bool", "int32": "std::int32_t", "int64": "std::int64_t", "float32": "float", "float64": "double", "string": "std::string"}
INT_RANGE = {"int8": (-2**7, 2**7 - 1), "uint8": (0, 2**8 - 1), "byte": (0, 2**8 - 1), "char": (0, 2**8 - 1), "int16": (-2**15, 2**15 - 1),
             "uint16": (0, 2**16 - 1), "int32": (-2**31, 2**31 - 1), "uint32": (0, 2**32 - 1), "int64": (-2**63, 2**63 - 1), "uint64": (0, 2**64 - 1)}
FLOATS = [0.0, -0.0, 1.0, -1.0, 0.5, 2.5, -3.75, 1e-3, 1e3, 1e9, 1e300, -1e300, math.inf, -math.inf, math.nan]
STRINGS = ["", "a", "OK", "same", "héllo", "line\nbreak", "quote\"back\\slash"]


# ---------------------------------------------------------------- random values
NARROW_EDGES = [127, 128, 255, 256, 32767, 32768, 65535, 65536, -128, -129, -32768, -32769, 2**32 - 1, 2**32, 2**53 + 1]   # bounds of the narrow ROS sinks


def rand_int(rng, lo, hi):
    picks = [lo, hi, 0, 1, -1, 2, 3, 7, lo + 1, hi - 1, rng.randint(lo, hi), rng.randint(lo, hi), rng.randint(max(lo, -100), min(hi, 100)),
             rng.choice(NARROW_EDGES)]
    return rng.choice([p for p in picks if lo <= p <= hi])


def rand_float(rng):
    return rng.choice(FLOATS + [rng.uniform(-10, 10), rng.uniform(-10, 10), rng.uniform(0, 1), rng.uniform(-1e6, 1e6)])


def rand_value(rng, ty):
    """A raw ROS (or canonical) value for a type string."""
    if ty.endswith("[]"): return [rand_value(rng, ty[:-2]) for _ in range(rng.randint(0, 4))]
    if ty == "bool": return bool(rng.getrandbits(1))
    if ty in INT_RANGE: return rand_int(rng, *INT_RANGE[ty])
    if ty == "float32": return interp.f32(rand_float(rng))
    if ty == "float64": return rand_float(rng)
    if ty == "string": return rng.choice(STRINGS)
    if ty in ("time", "duration"):
        return (rng.choice([0, 1, -1, 5, -5, 2**31 - 1, -2**31, rng.randint(-1000, 1000)]), rng.choice([0, 1, 500000000, 999999999, rng.randint(0, 999999999)]))
    raise AssertionError(ty)


# ---------------------------------------------------------------- C++ literals
def cstr(s):
    return '"' + "".join("\\%03o" % b for b in s.encode("utf-8")) + '"'


def clit(ty, v):
    """C++ literal for a value of a ROS or canonical type."""
    if ty.endswith("[]") or ty == "bytes":
        elem = "uint8" if ty == "bytes" else ty[:-2]
        return f"std::vector<{ROS_CPP.get(elem) or CANON_CPP[elem]}>{{{', '.join(clit(elem, x) for x in v)}}}"
    if ty == "bool": return "true" if v else "false"
    if ty in ("time", "duration"): return f"Stamp{{{clit('int32', v[0])}, {clit('uint32', v[1])}}}"
    if ty == "string": return f"std::string({cstr(v)})"
    if ty in ("float32", "float64"):
        t = "float" if ty == "float32" else "double"
        if math.isnan(v): return f"std::numeric_limits<{t}>::quiet_NaN()"
        if math.isinf(v): return f"{'-' if v < 0 else ''}std::numeric_limits<{t}>::infinity()"
        return f"static_cast<{t}>({float(v).hex()})"
    cpp = ROS_CPP.get(ty) or CANON_CPP[ty]
    if v == -2**63: return f"std::numeric_limits<{cpp}>::min()"
    return f"static_cast<{cpp}>({v}{'ULL' if v > 2**63 - 1 else 'LL' if abs(v) > 2**31 else ''})"


# ---------------------------------------------------------------- fake message types from the manifest's own declarations
def struct_tree(fields):
    tree = {}
    for path, ty in fields.items():
        cur = tree
        parts = path.split(".")
        for p in parts[:-1]: cur = cur.setdefault(p, {})
        cur[parts[-1]] = ty
    return tree


def emit_struct(name, tree, out):
    lines = []
    for k, v in tree.items():
        if isinstance(v, dict):
            emit_struct(f"{name}_{k}", v, out)
            lines.append(f"  {name}_{k} {k}{{}};")
        else:
            cpp = f"std::vector<{ROS_CPP[v[:-2]]}>" if v.endswith("[]") else ROS_CPP[v]
            lines.append(f"  {cpp} {k}{{}};")
    out.append(f"struct {name} {{\n" + "\n".join(lines) + "\n};")


def flat(tree, prefix=""):
    for k, v in tree.items():
        if isinstance(v, dict): yield from flat(v, f"{prefix}{k}.")
        else: yield f"{prefix}{k}", v


PRINTERS = r'''
template <class T> std::enable_if_t<std::is_integral_v<T> && !std::is_same_v<T, bool>> pv(T v) {
  if constexpr (std::is_signed_v<T>) std::printf("%lld", static_cast<long long>(v)); else std::printf("%llu", static_cast<unsigned long long>(v));
}
void pv(bool v) { std::printf("%d", v ? 1 : 0); }
void pv(double v) { if (std::isnan(v)) std::printf("nan"); else if (std::isinf(v)) std::printf(v < 0 ? "-inf" : "inf"); else std::printf("%a", v); }
void pv(float v) { pv(static_cast<double>(v)); }
void pv(const std::string & s) { for (unsigned char c : s) std::printf("%02x", c); }
template <class T> void pv(const std::vector<T> & v) { std::printf("["); bool first = true; for (const auto & e : v) { if (!first) std::printf(","); first = false; pv(e); } std::printf("]"); }
'''


def build_program(ir, pkg, ns, case_lists):
    """C++ source: fake messages, the cases, and a main that evaluates and prints every case."""
    decls, body = [], []
    for dag, cases in zip(ir["execution_dags"], case_lists):
        trig, p = dag["trigger"], dag["id"]
        struct, fields = None, input_fields(ir, dag)
        if "source_id" in trig:
            struct = f"In_{p}"
            emit_struct(struct, struct_tree(fields), decls)
        for i, c in enumerate(cases):
            lines = ["{", "  Params params; State state;"]
            for q in ir["parameters"]: lines.append(f"  params.{q['name']} = {clit(q['canonical_type'], c['params'][q['name']])};")
            for s in ir["state_buffers"]: lines.append(f"  state.{s['name']} = {clit(s['canonical_type'], c['state'][s['name']])};")
            args = ""
            if struct:
                lines.append(f"  {struct} in;")
                lines += [f"  in.{path} = {clit(ty, c['inputs'][path])};" for path, ty in flat(struct_tree(fields))]
                args = "in, "
            lines += [f"  {camel(p)}Result r;",
                      f"  const bool ok = eval_{p}({args}params, state, {clit('float64', c['now'])}, {clit('float64', c['dt'])}, r);",
                      f'  std::printf("R {p} {i} %d", ok ? 0 : 1);']
            lines += [f'  if (ok) {{ std::printf(" {m}="); pv(r.{m}); }}' for m in c["members"]]
            lines += ['  std::printf("\\n");', "}"]
            body.append("\n".join(lines))
    return ('#include <cmath>\n#include <cstdint>\n#include <cstdio>\n#include <limits>\n#include <string>\n#include <type_traits>\n#include <vector>\n'
            f'#include "{pkg}/pipelines.hpp"\nusing namespace {ns};\nstruct Stamp {{ std::int32_t sec; std::uint32_t nanosec; }};\n'
            + "\n".join(decls) + PRINTERS + "int main() {\n" + "\n".join(body) + "\n  return 0;\n}\n")


def member_types(ir, dag):
    """Result members of a pipeline with their canonical types, named as the generator names them."""
    nodes = {n["id"]: n for n in dag["nodes"]}
    states = {s["name"]: s["canonical_type"] for s in ir["state_buffers"]}
    out = {f"next_{w['state_name']}": states[w["state_name"]] for w in dag["state_writes"]}
    for a in dag["assignments"]:
        target, _, path = a["target_field_path"].partition(".")
        out[f"out__{target}__{path.replace('.', '__')}"] = nodes[a["source_node_id"]]["type_symbol"]
    return out


def input_fields(ir, dag):
    """Declared fields of the trigger's message or request; a pass-through type with no declaration has none."""
    trig = dag["trigger"]
    if "source_id" not in trig: return {}
    types = {t["type_symbol"]: t for t in ir["interface_types"]}
    endpoints = {e["identifier"]: e for k in ("publishers", "subscribers", "services") for e in ir["interfaces"][k]}
    t = types.get(endpoints[trig["source_id"]]["type_symbol"])
    if t is None: return {}
    return t["fields"] if t["kind"] == "msg" else t["request"]


def make_cases(ir, dag, rng, n):
    fields = input_fields(ir, dag)
    members = member_types(ir, dag)
    return [{"inputs": {p: rand_value(rng, ty) for p, ty in fields.items()},
             "params": {q["name"]: rand_value(rng, q["canonical_type"]) for q in ir["parameters"]},
             "state": {s["name"]: rand_value(rng, s["canonical_type"]) for s in ir["state_buffers"]},
             "now": rng.choice([0.0, 1.5, rng.uniform(0, 1e6)]), "dt": rng.choice([0.0, 0.01, 0.5, 2.0]), "members": members}
            for _ in range(n)]


# ---------------------------------------------------------------- run and compare
def parse_value(ty, text):
    if ty.endswith("[]"):
        inner = text[1:-1]
        return [parse_value(ty[:-2], x) for x in inner.split(",")] if inner else []
    if ty == "bool": return text == "1"
    if ty in ("int32", "int64"): return int(text)
    if ty in ("float32", "float64"): return math.nan if text == "nan" else math.inf if text == "inf" else -math.inf if text == "-inf" else float.fromhex(text)
    if ty == "string": return bytes.fromhex(text).decode("utf-8")
    raise AssertionError(ty)


def same(ty, a, b):
    if ty.endswith("[]"): return len(a) == len(b) and all(same(ty[:-2], x, y) for x, y in zip(a, b))
    if ty in ("float32", "float64"): return (math.isnan(a) and math.isnan(b)) or (a == b and math.copysign(1, a) == math.copysign(1, b))
    return a == b


def compiler():
    return shutil.which("clang++") or shutil.which("g++")


def differential(ir, n=60, seed=1):
    """Returns a list of mismatch descriptions (empty when the C++ agrees with the interpreter on every case)."""
    files = package(ir, core_only=True)
    pkg, ns = ir["node_meta"]["name"], cxx_namespace(ir["node_meta"]["namespace"])
    rng = random.Random(seed)
    case_lists = [make_cases(ir, dag, rng, n) for dag in ir["execution_dags"]]
    src = build_program(ir, pkg, ns, case_lists)
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
        (root / "main.cpp").write_text(src)
        exe = root / "harness"
        build = subprocess.run([compiler(), "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", "-ffp-contract=off", f"-I{root}/include", str(root / "main.cpp"), "-o", str(exe)],
                               capture_output=True, text=True)
        if build.returncode: return ["compile failed:\n" + build.stderr[:3000]]
        run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
    if run.returncode: return [f"harness exited {run.returncode}"]
    got = {}
    for line in run.stdout.splitlines():
        tok = line.split(" ")
        got[(tok[1], int(tok[2]))] = (tok[3] == "1", dict(t.split("=", 1) for t in tok[4:]))
    problems = []
    for dag, cases in zip(ir["execution_dags"], case_lists):
        for i, c in enumerate(cases):
            fault_cpp, vals = got[(dag["id"], i)]
            fault_ref, expect = interp.run(ir, dag, c["inputs"], c["params"], c["state"], c["now"], c["dt"])
            where = f"{dag['id']}[{i}] inputs {c['inputs']} params {c['params']} state {c['state']} now {c['now']} dt {c['dt']}"
            if fault_cpp != fault_ref:
                problems.append(f"{where}: fault is {fault_cpp} in C++, {fault_ref} in the interpreter")
                continue
            if fault_ref: continue
            for m, ty in c["members"].items():
                a, b = parse_value(ty, vals[m]), expect[m]
                if not same(ty, a, b): problems.append(f"{where}: {m} is {a!r} in C++, {b!r} in the interpreter")
    return problems
