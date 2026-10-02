"""Builds the file set of a generated C++ package, and writes it to disk (SPEC-03 section 4).

Everything here is a pure function of the IR, the RMW and the templates, so the same input
always gives the same bytes (SPEC-03 section 2, rule 1). ``generate_package`` returns the
files as ``{relative path: text}``; ``write_package`` puts them on disk atomically.
"""

import os
import shutil
from pathlib import Path

from ...ir.canonical import ir_hash
from . import names
from .dag import DagEmitter, PipelineInfo
from .engine import engine_members, engine_methods
from .errors import Unsupported
from .interfaces import InterfacesBuilder
from .literals import value_literal
from .logic import has_user_logic, logic_api_header, logic_defaults_source
from .naming import camel, cpp_type, cxx_namespace, member, message_header, split_type
from .parameters import (
    check_parameter_types,
    constraint_functions,
    declaration_lines,
    params_yaml,
    update_lines,
    validation_checks,
)
from .templates import environment, runtime_header

# The package providing each RMW implementation (SPEC-03 section 2.1).
RMW_PACKAGES = {"fastrtps": "rmw_fastrtps_cpp"}
SUPPORTED_EXECUTORS = ("SingleThreadedExecutor", "MultiThreadedExecutor")


def _check_supported(ir: dict) -> None:
    """Refuse what this revision of the generator does not implement yet."""
    for block, settings in ir["extensions"].items():
        if block != "concurrency" and settings.get("enabled", True):
            raise Unsupported(f"the {block} block is not implemented by the C++ generator yet")
    executor = ir["concurrency"]["executor"]
    if executor not in SUPPORTED_EXECUTORS:
        raise Unsupported(f"executor {executor} is not implemented by the C++ generator yet")
    endpoints = [*ir["interfaces"]["publishers"], *ir["interfaces"]["subscribers"]]
    if any(endpoint["zero_copy"] for endpoint in endpoints):
        raise Unsupported("zero_copy is not implemented yet")
    check_parameter_types(ir["parameters"])


def _initial_members(entries: list[dict], value_key: str) -> list[dict]:
    """Template context for the members of ``Params`` or ``State``: type, name and initialiser."""
    return [
        {
            "cpp_type": cpp_type(entry["canonical_type"]),
            "member": member(entry["name"]),
            "init": "{" + value_literal(entry["canonical_type"], entry[value_key]) + "}",
        }
        for entry in entries
    ]


def _pipelines_header(
    ir: dict, pipelines: list[PipelineInfo], result_types: dict, hash_: str
) -> str:
    """Text of ``pipelines.hpp``: parameters, state, validation and one function per pipeline."""
    meta = ir["node_meta"]
    return (
        environment()
        .get_template("pipelines.hpp.j2")
        .render(
            ir_hash=hash_,
            pkg=meta["name"],
            ns=cxx_namespace(meta["namespace"]),
            has_user_logic=has_user_logic(ir),
            params=_initial_members(ir["parameters"], "default_value"),
            states=_initial_members(ir["state_buffers"], "initial_value"),
            constraints=constraint_functions(ir),
            checks=[check for p in ir["parameters"] for check in validation_checks(p)],
            pipelines=[
                {
                    "name": p.name,
                    "result": result_types[p.name],
                    "has_input": p.input_type is not None,
                    "body": p.body,
                    "result_members": p.result_members,
                }
                for p in pipelines
            ],
        )
    )


def _message_packages(ir: dict) -> tuple[list[str], list[str]]:
    """(ROS packages to depend on, message headers to include) for the endpoints' types."""
    interfaces = ir["interfaces"]
    types = sorted(
        {
            e["type_symbol"]
            for kind in ("publishers", "subscribers", "services")
            for e in interfaces[kind]
        }
    )
    packages = {split_type(t)[0] for t in types}
    depends = sorted({"rclcpp", "diagnostic_msgs", "rcl_interfaces", *packages})
    return depends, sorted({message_header(t) for t in types})


def _node_files(ir: dict, pipelines: list[PipelineInfo], hash_: str) -> dict:
    """The files of the ROS side: the node, the interfaces, parameters and diagnostics adapters,
    the application with ``main.cpp``, and the build, parameter and launch files."""
    env = environment()
    meta = ir["node_meta"]
    package_name, class_name = meta["name"], camel(meta["name"])
    depends, headers = _message_packages(ir)
    interfaces = InterfacesBuilder(ir, pipelines).build()
    has_params = bool(ir["parameters"])
    shared = {
        "ir_hash": hash_,
        "pkg": package_name,
        "ns": cxx_namespace(meta["namespace"]),
        "cls": class_name,
    }

    def render(template: str, **context) -> str:
        return env.get_template(template).render(**shared, **context)

    files = {
        f"include/{package_name}/{package_name}_node.hpp": render(
            "node.hpp.j2", has_user_logic=has_user_logic(ir)
        ),
        f"src/{package_name}_node.cpp": render("node.cpp.j2", has_user_logic=has_user_logic(ir)),
        f"include/{package_name}/interfaces.hpp": render(
            "interfaces.hpp.j2",
            headers=headers,
            methods=interfaces.methods,
            members=interfaces.members,
        ),
        "src/interfaces.cpp": render(
            "interfaces.cpp.j2",
            creates=interfaces.creates,
            startups=interfaces.startups,
            run_methods=interfaces.run_methods(class_name + "Interfaces"),
        ),
        f"include/{package_name}/parameters.hpp": render("parameters.hpp.j2"),
        "src/parameters.cpp": render(
            "parameters.cpp.j2",
            declare=declaration_lines(ir["parameters"]),
            update=update_lines(ir["parameters"]),
        ),
        f"include/{package_name}/diagnostics.hpp": render("diagnostics.hpp.j2"),
        "src/diagnostics.cpp": render("diagnostics.cpp.j2"),
        f"include/{package_name}/app.hpp": render("app.hpp.j2"),
        "src/app.cpp": render("app.cpp.j2", executor=ir["concurrency"]["executor"]),
        "src/main.cpp": render("main.cpp.j2"),
        "CMakeLists.txt": render(
            "CMakeLists.txt.j2",
            depends=depends,
            has_user_logic=has_user_logic(ir),
            has_params=has_params,
        ),
        f"launch/{package_name}.launch.py": render("launch.py.j2", has_params=has_params),
    }
    if has_params:
        files[f"config/{package_name}.params.yaml"] = params_yaml(ir)
    return files


def _core_files(
    ir: dict, pipelines: list[PipelineInfo], result_types: dict, hash_: str
) -> dict[str, str]:
    """The files that need no ROS: the names, the pipelines, the engine and the runtime library."""
    meta = ir["node_meta"]
    package_name = meta["name"]
    threads = ir["concurrency"]["threads"]
    context = {
        "ir_hash": hash_,
        "pkg": package_name,
        "ns": cxx_namespace(meta["namespace"]),
        "cls": camel(package_name),
    }
    env = environment()
    return {
        f"include/{package_name}/interface_names.hpp": env.get_template(
            "interface_names.hpp.j2"
        ).render(**context, constants=names.constants(ir)),
        f"include/{package_name}/pipelines.hpp": _pipelines_header(
            ir, pipelines, result_types, hash_
        ),
        f"include/{package_name}/engine.hpp": env.get_template("engine.hpp.j2").render(
            **context,
            methods=engine_methods(pipelines, result_types),
            members=engine_members(pipelines),
            n_ring=threads
            + 2,  # SPEC-12 section 5: one slot per thread, the current one, a free one
            threads=threads,
        ),
        f"include/{package_name}/runtime.hpp": runtime_header(),
    }


def generate_package(
    ir: dict,
    rmw: str = "fastrtps",
    core_only: bool = False,
    logic_files: dict[str, str] | None = None,
) -> dict[str, str]:
    """The files of the ``ament_cmake`` package for ``ir``, as ``{relative path: text}``.

    ``logic_files`` are the user's source files (``{path relative to logic/: text}``); they are
    copied into the package under ``logic/`` and built with it (see ``logic.py``).

    With ``core_only`` only the ROS-free part is produced (``interface_names.hpp``,
    ``pipelines.hpp``, ``engine.hpp``, ``runtime.hpp`` and ``logic_api.hpp``) and the checks that concern the ROS node are skipped; the tests compile it without ROS."""
    if not core_only:
        _check_supported(ir)
    meta = ir["node_meta"]
    package_name, hash_ = meta["name"], ir_hash(ir)
    pipelines = [DagEmitter(ir, dag).build() for dag in ir["execution_dags"]]
    result_types = {p.name: camel(p.name) + "Result" for p in pipelines}

    files = _core_files(ir, pipelines, result_types, hash_)
    if has_user_logic(ir):
        files[f"include/{package_name}/logic_api.hpp"] = logic_api_header(ir)
        files.update({f"logic/{path}": text for path, text in (logic_files or {}).items()})
    if not core_only:
        if has_user_logic(ir):
            files["src/logic_defaults.cpp"] = logic_defaults_source(ir)
        files.update(_node_files(ir, pipelines, hash_))
        files["package.xml"] = (
            environment()
            .get_template("package.xml.j2")
            .render(
                pkg=package_name,
                version=meta["version"],
                description=meta.get("description", f"Node {package_name} generated by nodesmith."),
                depends=_message_packages(ir)[0],
                rmw_package=RMW_PACKAGES[rmw],
            )
        )
    return dict(sorted(files.items()))


def write_package(files: dict[str, str], out_dir: str | Path) -> None:
    """Write the files to ``out_dir`` so that a failed run never leaves a half-written package.

    The files go to a temporary directory next to ``out_dir`` first; only then does it replace
    ``out_dir``. Everything in a generated package is regenerated: nothing in it is edited by
    hand (the user's own code is edited in its ``logic/`` folder and copied in, see ``logic.py``)."""
    target = Path(out_dir)
    staging = target.with_name(f"{target.name}.tmp-{os.getpid()}")
    if staging.exists():
        shutil.rmtree(staging)
    for relative, text in files.items():
        path = staging / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    previous = None
    if target.exists():
        previous = target.with_name(f"{target.name}.old-{os.getpid()}")
        target.rename(previous)
    staging.rename(target)
    if previous:
        shutil.rmtree(previous)
