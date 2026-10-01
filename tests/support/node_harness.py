"""Compiles and runs the GENERATED node against the rclcpp stand-in in tests/support/ros_stub (no ROS needed)."""
import subprocess
import tempfile
from pathlib import Path

from nodesmith.cpp.generate import package
from nodesmith.cpp.naming import message_header, split_type

from .harness import compiler, flat, struct_tree

STUB = Path(__file__).resolve().parent / "ros_stub"
ROS_CPP = {"bool": "bool", "byte": "std::uint8_t", "char": "std::uint8_t", "int8": "std::int8_t", "uint8": "std::uint8_t", "int16": "std::int16_t",
           "uint16": "std::uint16_t", "int32": "std::int32_t", "uint32": "std::uint32_t", "int64": "std::int64_t", "uint64": "std::uint64_t",
           "float32": "float", "float64": "double", "string": "std::string", "time": "builtin_interfaces::msg::Time",
           "duration": "builtin_interfaces::msg::Duration"}


def _members(fields, prefix):
    """(nested struct definitions, member lines) for a field map: a nested struct per dotted-path prefix, vectors for arrays."""
    defs = []

    def emit(name, tree):
        lines = []
        for k, v in tree.items():
            if isinstance(v, dict):
                emit(f"{name}_{k}", v)
                lines.append(f"  {name}_{k} {k}{{}};")
            else:
                lines.append(f"  {f'std::vector<{ROS_CPP[v[:-2]]}>' if v.endswith('[]') else ROS_CPP[v]} {k}{{}};")
        defs.append(f"struct {name} {{\n" + "\n".join(lines) + "\n};\n")
        return lines

    top = emit(prefix, struct_tree(fields))
    return "".join(defs[:-1]), "\n".join(top)


def message_headers(ir):
    """Fake ROS message headers for every type the manifest uses, built from its own [interfaces] declarations."""
    declared = {t["type_symbol"]: t for t in ir["interface_types"]}
    symbols = {e["type_symbol"] for k in ("publishers", "subscribers", "services") for e in ir["interfaces"][k]}
    files = {}
    for sym in sorted(symbols):
        pkg, kind, name = split_type(sym)
        t = declared.get(sym)
        if kind == "msg":
            nested, top = _members(t["fields"] if t else {}, name)
            body = f"{nested}struct {name} {{\n{top}\n  using SharedPtr = std::shared_ptr<{name}>;\n  using ConstSharedPtr = std::shared_ptr<const {name}>;\n}};\n"
        else:
            n1, req = _members(t["request"] if t else {}, f"{name}_Req")
            n2, res = _members(t["response"] if t else {}, f"{name}_Res")
            body = f"{n1}{n2}struct {name} {{\n  struct Request {{\n{req}\n  }};\n  struct Response {{\n{res}\n  }};\n}};\n"
        files[message_header(sym)] = ('#pragma once\n#include <cstdint>\n#include <memory>\n#include <string>\n#include <vector>\n#include "diagnostic_msgs/msg/diagnostic_array.hpp"\n'
                                     f"namespace {pkg}::{kind} {{\n{body}}}\n")
    return files


def run_driver(ir, driver_source, extra_flags=()):
    """Generates the package for `ir`, compiles node + stubs + driver, runs it. Returns (returncode, output)."""
    files = package(ir)
    files.update({f"include/{p}": t for p, t in message_headers(ir).items()})
    pkg = ir["node_meta"]["name"]
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
        (root / "driver.cpp").write_text(driver_source)
        exe = root / "driver"
        cmd = [compiler(), "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-ffp-contract=off", f"-I{root}/include", f"-I{STUB}",
               str(root / f"src/{pkg}_node.cpp"), str(root / "driver.cpp"), "-o", str(exe), "-pthread", *extra_flags]
        build = subprocess.run(cmd, capture_output=True, text=True)
        if build.returncode: return build.returncode, "compile failed:\n" + build.stderr[:4000]
        run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
        # main.cpp must compile too
        main = subprocess.run([compiler(), "-std=c++17", "-fsyntax-only", "-Wall", "-Wextra", "-Werror", f"-I{root}/include", f"-I{STUB}", str(root / "src/main.cpp")],
                              capture_output=True, text=True)
        if main.returncode: return main.returncode, "main.cpp failed:\n" + main.stderr[:2000]
    return run.returncode, run.stdout + run.stderr
