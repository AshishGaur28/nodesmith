"""C++ target: IR -> file set (SPEC-03 §4). Everything is a pure function of the IR, the RMW and the generator version."""
import os
import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ..canonical import ir_hash
from .naming import (NARROW_INTS, camel, cpp_type, cxx_namespace, member, message_cpp, message_header, path_ident, split_type)
from .pipeline import Emitter, Unsupported, cpp_string, literal

RMW_PACKAGES = {"fastrtps": "rmw_fastrtps_cpp"}                   # SPEC-03 §2.1
ROS_PARAM = {"bool": "bool", "int32": "std::int64_t", "int64": "std::int64_t", "float32": "double", "float64": "double",
             "string": "std::string", "bool[]": "std::vector<bool>", "int64[]": "std::vector<std::int64_t>",
             "float64[]": "std::vector<double>", "string[]": "std::vector<std::string>", "bytes": "std::vector<std::uint8_t>"}
ROS_PARAM_GET = {"bool": "as_bool", "int32": "as_int", "int64": "as_int", "float32": "as_double", "float64": "as_double", "string": "as_string",
                 "bool[]": "as_bool_array", "int64[]": "as_integer_array", "float64[]": "as_double_array", "string[]": "as_string_array",
                 "bytes": "as_byte_array"}
TEMPLATES = Path(os.environ.get("NODESMITH_TEMPLATES") or Path(__file__).resolve().parents[3] / "templates" / "cpp_rclcpp")
# ponytail: templates are read from the repo checkout; bundle them as package data when nodesmith is packaged.


def _env():
    return Environment(loader=FileSystemLoader(str(TEMPLATES)), undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True,
                       keep_trailing_newline=True)


def runtime_header() -> str:
    """runtime.hpp: the SPEC-12 primitives followed by the numeric helpers and the diagnostics ring."""
    return (TEMPLATES / "concurrency_primitives.hpp").read_text() + "\n" + (TEMPLATES / "runtime_helpers.hpp").read_text()


# ---------------------------------------------------------------- parameters and state (SPEC-07, SPEC-02 §7)
def value_literal(ty, v):
    if ty.endswith("[]") or ty == "bytes":
        elem = "uint8" if ty == "bytes" else ty[:-2]
        return f"{cpp_type(ty)}{{{', '.join(literal(x, elem) for x in v)}}}"
    return literal(v, ty)


def _bound(ty, v): return literal(v, "float64" if ty in ("float32", "float64") else "int64") if not isinstance(v, bool) else str(v).lower()


def param_checks(p):
    """C++ statements of validate() for one parameter; each returns the reason string (SPEC-07 §3.1 step 2)."""
    name, ty, val, m = p["name"], p["canonical_type"], p.get("validation", {}), f"p.{member(p['name'])}"
    quoted = repr(name)
    out = []

    def reject(text): return f"return std::string({cpp_string(text)});"

    if "min" in val or "max" in val:
        conds = ([f"{m} >= {_bound(ty, val['min'])}"] if "min" in val else []) + ([f"{m} <= {_bound(ty, val['max'])}"] if "max" in val else [])
        lo, hi = val.get("min", "-inf"), val.get("max", "inf")
        out.append(f"if (!({' && '.join(conds)})) " + reject(f"{quoted} out of range [{lo}, {hi}]"))
    if "step" in val:
        base = float(val.get("min", 0))
        out.append(f"if (std::fabs(std::remainder(static_cast<double>({m}) - {base!r}, {float(val['step'])!r})) > 1e-9) "
                   + reject(f"{quoted} is not a multiple of step {val['step']}"))
    if "one_of" in val:
        eq = " || ".join(f"{m} == {literal(x, 'string' if isinstance(x, str) else 'int64')}" for x in val["one_of"])
        out.append(f"if (!({eq})) " + reject(f"{quoted} must be one of " + ", ".join(map(str, val["one_of"]))))
    if "fixed_length" in val:
        out.append(f"if ({m}.size() != {val['fixed_length']}) " + reject(f"{quoted} must have exactly {val['fixed_length']} elements"))
    if "regex" in val:
        out.append("try { static const std::regex re(" + cpp_string(val["regex"]) + f"); if (!std::regex_match({m}, re)) "
                   + reject(f"{quoted} does not match its regex") + " } catch (const std::regex_error &) { " + reject(f"{quoted} has an invalid regex") + " }")
    return out


def declare_body(params):
    lines = ["Params initial;  // effective values include launch-file and command-line overrides"]
    for p in params:
        name, ty, val, m = p["name"], p["canonical_type"], p.get("validation", {}), member(p["name"])
        ros = ROS_PARAM[ty]
        d = f"d_{m}"
        lines.append(f"rcl_interfaces::msg::ParameterDescriptor {d};")
        if "description" in p: lines.append(f"{d}.description = {cpp_string(p['description'])};")
        if p.get("read_only"): lines.append(f"{d}.read_only = true;")
        if ty in ("int32", "int64") and ("min" in val or "max" in val):
            lines += [f"{d}.integer_range.resize(1);", f"{d}.integer_range[0].from_value = {val['min'] if 'min' in val else 'std::numeric_limits<std::int64_t>::min()'};",
                      f"{d}.integer_range[0].to_value = {val['max'] if 'max' in val else 'std::numeric_limits<std::int64_t>::max()'};",
                      f"{d}.integer_range[0].step = {int(val['step']) if 'step' in val else 0};"]
        if ty in ("float32", "float64") and ("min" in val or "max" in val):
            lines += [f"{d}.floating_point_range.resize(1);", f"{d}.floating_point_range[0].from_value = {float(val['min'])!r};" if "min" in val else f"{d}.floating_point_range[0].from_value = std::numeric_limits<double>::lowest();",
                      f"{d}.floating_point_range[0].to_value = {float(val['max'])!r};" if "max" in val else f"{d}.floating_point_range[0].to_value = std::numeric_limits<double>::max();",
                      f"{d}.floating_point_range[0].step = {float(val['step'])!r};" if "step" in val else f"{d}.floating_point_range[0].step = 0.0;"]
        default = value_literal(ty, p["default_value"])
        decl = f"declare_parameter<{ros}>({cpp_string(name)}, static_cast<{ros}>({default}), {d})" if not (ty.endswith('[]') or ty == 'bytes') else f"declare_parameter<{ros}>({cpp_string(name)}, {ros}({default}), {d})"
        if ty == "int32":
            why = cpp_string(repr(name) + " is outside the int32 range")
            lines += ["{", "  bool f = false;", f"  initial.{m} = r2d::to_int<std::int32_t>({decl}, f);",
                      f"  if (f) throw rclcpp::exceptions::InvalidParameterValueException({why});", "}"]
        elif ty in ("float32",): lines.append(f"initial.{m} = static_cast<float>({decl});")
        elif ty.endswith("[]") or ty == "bytes": lines.append(f"initial.{m} = {decl};")
        else: lines.append(f"initial.{m} = {decl};")
    return lines


def update_body(params):
    lines = ["for (const auto & p : updates) {", "  [[maybe_unused]] const std::string & name = p.get_name();"]
    first = True
    for p in params:
        name, ty, m = p["name"], p["canonical_type"], member(p["name"])
        lines.append(f"  {'if' if first else '} else if'} (name == {cpp_string(name)}) {{"); first = False
        if p.get("read_only"):
            lines.append("    return reject(" + cpp_string(repr(name) + " is read-only and cannot be updated at runtime") + ");")
        elif ty == "int32":
            lines += ["    bool f = false;", f"    next.{m} = r2d::to_int<std::int32_t>(p.as_int(), f);",
                      "    if (f) return reject(" + cpp_string(repr(name) + " is outside the int32 range") + ");"]
        elif ty == "float32": lines.append(f"    next.{m} = static_cast<float>(p.as_double());")
        else: lines.append(f"    next.{m} = p.{ROS_PARAM_GET[ty]}();")
    if not first: lines.append("  }")
    lines.append("}")
    return lines


def constraint_context(ir, c, i):
    dag = {"id": f"constraint_{i}", "trigger": {"kind": "startup"}, "nodes": c["nodes"], "state_writes": [], "assignments": []}
    e = Emitter(ir, dag)
    e.materialized = {n["id"] for n in dag["nodes"] if "label" in n} | {c["root_node_id"]}
    lines = [f"const {cpp_type(e.nodes[nid]['type_symbol'])} {e.local(nid)} = {e.expr(nid)};" for nid in (n["id"] for n in dag["nodes"]) if nid in e.materialized]
    lines.append(f"return {e.local(c['root_node_id'])};")
    text = "\n  ".join(lines)
    return {"body": text, "message": cpp_string(c.get("message") or f"parameter_constraints[{i}] is violated"),
            "fault_message": cpp_string(f"parameter_constraints[{i}] faulted on the new values")}


# ---------------------------------------------------------------- the node (SPEC-03 §4.3, SPEC-12 §3-§6)
def qos_expr(q):
    base = "rclcpp::QoS(rclcpp::KeepAll())" if q["history"] == "keep_all" else f"rclcpp::QoS({q['depth']})"
    return (base + (".reliable()" if q["reliability"] == "reliable" else ".best_effort()")
            + (".transient_local()" if q["durability"] == "transient_local" else ".durability_volatile()"))


def _assign_field(a, dst, src):
    if a.ros_type in ("time", "duration"): return f"r2d::set_time({dst}, {src});"
    if a.ros_type.endswith("[]"): return f"r2d::assign_array({dst}, {src});"
    if a.ros_type == "string" or a.ros_type == "bool" or a.ros_type in ("float32", "float64"): return f"{dst} = {src};"
    from .naming import ROS_CPP_TYPE
    return f"{dst} = static_cast<{ROS_CPP_TYPE[a.ros_type]}>({src});"


def _clock_now(info):
    t = info.trigger
    if t["kind"] == "timer" and t["clock"] == "system": return "system_now()"
    if t["kind"] == "timer" and t["clock"] == "ros": return "get_clock()->now().seconds()"
    return "steady_now()"


def _run_method(cls, info, ctx):
    p, kind = info.name, info.trigger["kind"]
    if kind == "subscriber": sig, args = f"const {message_cpp(info.input_type)} & in", "in, "
    elif kind == "service": sig, args = f"const {message_cpp(info.input_type)}::Request & in, {message_cpp(info.input_type)}::Response & res", "in, "
    else: sig, args = "", ""
    L = [f"void {cls}::run_{p}({sig}) {{"]
    if kind == "timer":
        L += [f"  if (busy_{p}_.exchange(true)) {{ diag_.report(r2d::Code::ERR_RUN_102, {cpp_string(p)}); return; }}",
              f"  struct BusyGuard {{ std::atomic<bool> & b; ~BusyGuard() {{ b.store(false); }} }} busy_guard{{busy_{p}_}};"]
    L += ["  r2d::RunGate::Scope run(gate_);", f"  if (!run) {{{' res = ' + message_cpp(info.input_type) + '::Response();' if kind == 'service' else ''} return; }}",
          "  const auto params = params_.acquire();  // one snapshot for the whole execution (SPEC-12 §5)"]
    if info.uses_now: L.append(f"  const double now_sec = {_clock_now(info)};")
    if info.uses_dt:
        L += [f"  const double dt_sec = have_last_{p}_ ? now_sec - last_{p}_ : 0.0;", f"  have_last_{p}_ = true;", f"  last_{p}_ = now_sec;"]
    L += [f"  {ctx['result'][p]} r;",
          f"  if (!eval_{p}({args}*params, state_, {'now_sec' if info.uses_now else '0.0'}, {'dt_sec' if info.uses_dt else '0.0'}, r)) {{",
          f"    diag_.report(r2d::Code::ERR_RUN_101, {cpp_string(p)});  // preallocated ring: no allocation, no logging here"]
    if kind == "service": L.append(f"    res = {message_cpp(info.input_type)}::Response();  // a fault returns the default response (SPEC-02 §7 step 6)")
    L += ["    return;", "  }"]
    for name, _, m in info.state_members: L.append(f"  state_.{member(name)} = r.{m};  // step 5: commit")
    by_target = {}
    for a in info.assignments: by_target.setdefault(a.target, []).append(a)
    for target in sorted(by_target):                 # ascending id order (SPEC-02 §7 step 5)
        if target == "res":
            L.append(f"  res = {message_cpp(info.input_type)}::Response();")
            L += [f"  {_assign_field(a, 'res.' + a.path, 'r.' + a.member)}" for a in by_target[target]]
        else:
            L += ["  {", f"    {message_cpp(ctx['pub_types'][target])} out{{}};  // value-initialised"]
            L += [f"    {_assign_field(a, 'out.' + a.path, 'r.' + a.member)}" for a in by_target[target]]
            L += [f"    pub_{target}_->publish(out);", "  }"]
    L.append("}")
    return "\n".join(L)


def package(ir, rmw="fastrtps", core_only=False):
    """Returns {relative path: text} for the ament_cmake package of this IR.

    `core_only` returns just the ROS-free part (pipelines.hpp and runtime.hpp) and skips the feature checks that only concern
    the ROS node; the tests compile it on machines without ROS.
    """
    for k, v in ir["extensions"].items():
        if k != "concurrency" and v.get("enabled", True) and not core_only:
            raise Unsupported(f"the {k} block is not implemented by the C++ generator yet")
    if ir["concurrency"]["executor"] not in ("SingleThreadedExecutor", "MultiThreadedExecutor") and not core_only:
        raise Unsupported(f"executor {ir['concurrency']['executor']} is not implemented by the C++ generator yet")
    if any(e["zero_copy"] for k in ("publishers", "subscribers") for e in ir["interfaces"][k]) and not core_only: raise Unsupported("zero_copy is not implemented yet")
    for p in ir["parameters"]:
        if p["canonical_type"] not in ROS_PARAM: raise Unsupported(f"parameter type {p['canonical_type']} is not supported yet")
    env, meta = _env(), ir["node_meta"]
    pkg, cls, ns = meta["name"], camel(meta["name"]), cxx_namespace(meta["namespace"])
    threads, digest = ir["concurrency"]["threads"], ir_hash(ir)

    infos = [Emitter(ir, dag).build() for dag in ir["execution_dags"]]
    result = {i.name: camel(i.name) + "Result" for i in infos}
    pubs = {e["identifier"]: e for e in ir["interfaces"]["publishers"]}
    subs = {e["identifier"]: e for e in ir["interfaces"]["subscribers"]}
    srvs = {e["identifier"]: e for e in ir["interfaces"]["services"]}
    ctx = {"result": result, "pub_types": {k: v["type_symbol"] for k, v in pubs.items()}}
    group_of = {d["id"]: d["callback_group"] for d in ir["execution_dags"]}

    types = sorted({e["type_symbol"] for kind in (pubs, subs, srvs) for e in kind.values()})
    msg_pkgs = sorted({split_type(t)[0] for t in types})
    depends = sorted({"rclcpp", "diagnostic_msgs", "rcl_interfaces", *msg_pkgs})
    headers = sorted({message_header(t) for t in types})

    # pipelines.hpp
    pl_ctx = []
    for i in infos:
        pl_ctx.append({"name": i.name, "result": result[i.name], "has_input": i.input_type is not None, "body": i.body, "result_members": i.result_members})
    constraints = [constraint_context(ir, c, n) for n, c in enumerate(ir["parameter_constraints"])]
    params_ctx = [{"cpp_type": cpp_type(p["canonical_type"]), "member": member(p["name"]), "init": "{" + value_literal(p["canonical_type"], p["default_value"]) + "}"}
                  for p in ir["parameters"]]
    states_ctx = [{"cpp_type": cpp_type(s["canonical_type"]), "member": member(s["name"]), "init": "{" + value_literal(s["canonical_type"], s["initial_value"]) + "}"}
                  for s in ir["state_buffers"]]
    checks = [line for p in ir["parameters"] for line in param_checks(p)]
    files = {}
    files[f"include/{pkg}/pipelines.hpp"] = env.get_template("pipelines.hpp.j2").render(
        ir_hash=digest, pkg=pkg, ns=ns, params=params_ctx, states=states_ctx, constraints=constraints, checks=checks, pipelines=pl_ctx)

    if core_only:
        files[f"include/{pkg}/runtime.hpp"] = runtime_header()
        return dict(sorted(files.items()))

    # node.hpp / node.cpp
    members, creates, methods = [], [], []
    group_names = [g["name"] for g in ir["concurrency"]["callback_groups"]]
    for g in ir["concurrency"]["callback_groups"]:
        members.append(f"rclcpp::CallbackGroup::SharedPtr group_{g['name']}_;")
        creates.append(f"group_{g['name']}_ = create_callback_group(rclcpp::CallbackGroupType::{g['type']});")
    members.append("rclcpp::CallbackGroup::SharedPtr group_diagnostics_;")
    creates.append("group_diagnostics_ = create_callback_group(rclcpp::CallbackGroupType::MutuallyExclusive);")
    for pid, e in pubs.items():
        t = message_cpp(e["type_symbol"])
        members.append(f"rclcpp::Publisher<{t}>::SharedPtr pub_{pid}_;")
        creates.append(f"pub_{pid}_ = create_publisher<{t}>({cpp_string(e['topic'])}, {qos_expr(e['qos'])});")
    uses_clock = {"system": False, "ros": False}
    for i in infos:
        p, trig, g = i.name, i.trigger, group_of[i.name]
        if i.uses_dt: members += [f"double last_{p}_{{0.0}};", f"bool have_last_{p}_{{false}};"]
        if trig["kind"] == "subscriber":
            e, t = subs[trig["source_id"]], message_cpp(subs[trig["source_id"]]["type_symbol"])
            members.append(f"rclcpp::Subscription<{t}>::SharedPtr sub_{trig['source_id']}_{p}_;")
            creates += ["{", "  rclcpp::SubscriptionOptions opts;", f"  opts.callback_group = group_{g}_;",
                        f"  sub_{trig['source_id']}_{p}_ = create_subscription<{t}>({cpp_string(e['topic'])}, {qos_expr(e['qos'])},",
                        f"    [this]({t}::ConstSharedPtr m) {{ run_{p}(*m); }}, opts);", "}"]
            methods.append(f"void run_{p}(const {t} & in);")
        elif trig["kind"] == "service":
            e = srvs[trig["source_id"]]; t = message_cpp(e["type_symbol"])
            members.append(f"rclcpp::Service<{t}>::SharedPtr srv_{trig['source_id']}_;")
            creates.append(f"srv_{trig['source_id']}_ = create_service<{t}>({cpp_string(e['service_name'])}, "
                           f"[this](const std::shared_ptr<{t}::Request> req, std::shared_ptr<{t}::Response> res) {{ run_{p}(*req, *res); }}, "
                           f"rclcpp::ServicesQoS(), group_{g}_);")
            methods.append(f"void run_{p}(const {t}::Request & in, {t}::Response & res);")
        elif trig["kind"] == "timer":
            clock = trig["clock"]
            clock_expr = {"steady": "clock_steady_", "system": "clock_system_", "ros": "get_clock()"}[clock]
            members += [f"rclcpp::TimerBase::SharedPtr timer_{p}_;", f"std::atomic<bool> busy_{p}_{{false}};"]
            if clock != "ros": uses_clock[clock] = True
            creates.append(f"timer_{p}_ = rclcpp::create_timer(this, {clock_expr}, rclcpp::Duration(std::chrono::milliseconds({trig['period_ms']})), [this]() {{ run_{p}(); }}, group_{g}_);")
            methods.append(f"void run_{p}();")
        else:
            methods.append(f"void run_{p}();")
    if uses_clock["system"]: members.append("rclcpp::Clock::SharedPtr clock_system_{std::make_shared<rclcpp::Clock>(RCL_SYSTEM_TIME)};")
    if any(i.trigger["kind"] == "timer" and i.trigger["clock"] == "steady" for i in infos): members.append("rclcpp::Clock::SharedPtr clock_steady_{std::make_shared<rclcpp::Clock>(RCL_STEADY_TIME)};")
    members += ["rclcpp::Publisher<diagnostic_msgs::msg::DiagnosticArray>::SharedPtr diag_pub_;", "rclcpp::TimerBase::SharedPtr diag_timer_;"]
    creates += ["diag_pub_ = create_publisher<diagnostic_msgs::msg::DiagnosticArray>(\"/diagnostics\", rclcpp::QoS(10));",
                "diag_timer_ = rclcpp::create_timer(this, clock_diag_, rclcpp::Duration(std::chrono::seconds(1)), [this]() { drain_diagnostics(); }, group_diagnostics_);"]
    members.append("rclcpp::Clock::SharedPtr clock_diag_{std::make_shared<rclcpp::Clock>(RCL_STEADY_TIME)};")
    startups = [i.name for i in infos if i.trigger["kind"] == "startup"]
    run_methods = "\n\n".join(_run_method(cls, i, ctx) for i in infos)
    base = {"ir_hash": digest, "pkg": pkg, "ns": ns, "cls": cls}
    files[f"include/{pkg}/{pkg}_node.hpp"] = env.get_template("node.hpp.j2").render(
        **base, headers=headers, n_ring=threads + 2, threads=threads, members=members, methods=methods)
    files[f"src/{pkg}_node.cpp"] = env.get_template("node.cpp.j2").render(
        **base, node_name=pkg, ros_namespace=meta["namespace"], n_ring=threads + 2, creates=creates, startups=startups,
        declare=declare_body(ir["parameters"]), update=update_body(ir["parameters"]), run_methods=run_methods,
        has_params=bool(ir["parameters"]))
    files["src/main.cpp"] = env.get_template("main.cpp.j2").render(**base, executor=ir["concurrency"]["executor"], threads=threads)
    files["CMakeLists.txt"] = env.get_template("CMakeLists.txt.j2").render(pkg=pkg, depends=depends)
    files["package.xml"] = env.get_template("package.xml.j2").render(
        pkg=pkg, version=meta["version"], description=meta.get("description", f"Node {pkg} generated by nodesmith."),
        depends=depends, rmw_package=RMW_PACKAGES[rmw])
    files[f"include/{pkg}/runtime.hpp"] = runtime_header()
    return dict(sorted(files.items()))


def write_package(files, out_dir):
    """Writes the file set next to `out_dir` and swaps it in, so a failed run never leaves a half-written package."""
    out = Path(out_dir)
    staging = out.with_name(out.name + f".tmp-{os.getpid()}")
    if staging.exists(): shutil.rmtree(staging)
    for rel, text in files.items():
        path = staging / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    old = None
    if out.exists():
        old = out.with_name(out.name + f".old-{os.getpid()}")
        out.rename(old)
    staging.rename(out)
    if old: shutil.rmtree(old)
