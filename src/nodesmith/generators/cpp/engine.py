"""The engine: the ROS-free part of a node (``engine.hpp``).

It owns what a node must keep consistent between executions (parameter snapshots, state, the run
gate, the diagnostics ring and the time of the previous run of each pipeline) and has one method
per pipeline that evaluates it and commits its state (SPEC-02 section 7 steps 2 to 6; SPEC-12
sections 3 to 6). It returns the outputs to publish and never touches ROS: the adapters in
``interfaces.py`` feed it and publish what it returns.
"""

from . import names
from .dag import PipelineInfo
from .naming import member

_HAS_INPUT = ("subscriber", "service")


def engine_members(pipelines: list[PipelineInfo]) -> list[str]:
    """Data members for the time since the previous execution of the pipelines that read it."""
    members = []
    for pipeline in pipelines:
        if pipeline.uses_dt:
            members += [
                f"double last_{pipeline.name}_{{0.0}};",
                f"bool have_last_{pipeline.name}_{{false}};",
            ]
    return members


def engine_methods(pipelines: list[PipelineInfo], result_types: dict[str, str]) -> str:
    """The ``run_<pipeline>`` methods of the engine class, separated by blank lines."""
    return "\n\n".join(_method(p, result_types[p.name]) for p in pipelines)


def _method(pipeline: PipelineInfo, result_type: str) -> str:
    name = pipeline.name
    has_input = pipeline.trigger["kind"] in _HAS_INPUT
    parameters = ["const r2d::RunGate::Scope &"]  # proof that the caller entered the gate
    if has_input:
        parameters.append("const In & in")
    if pipeline.uses_now:
        parameters.append("double now_sec")
    arguments = ("in, " if has_input else "") + "*params, state_, "
    arguments += "now_sec, " if pipeline.uses_now else "0.0, "
    arguments += "dt_sec, " if pipeline.uses_dt else "0.0, "
    lines = []
    if has_input:
        lines.append("  template <class In>")
    lines += [
        f"  std::optional<{result_type}> run_{name}({', '.join(parameters)}) {{",
        "    const auto params = params_.acquire();  // one snapshot for the whole execution (SPEC-12 §5)",
    ]
    if pipeline.uses_dt:
        lines += [
            f"    const double dt_sec = have_last_{name}_ ? now_sec - last_{name}_ : 0.0;",
            f"    have_last_{name}_ = true;",
            f"    last_{name}_ = now_sec;",
        ]
    lines += [
        f"    {result_type} r;",
        f"    if (!eval_{name}({arguments}r)) {{",
        f"      diag_.report(r2d::Code::ERR_RUN_101, {names.pipeline(name)});  // preallocated ring: no allocation, no logging here",
        "      return std::nullopt;",
        "    }",
    ]
    lines += [
        f"    state_.{member(state)} = r.{result_member};  // step 5: commit"
        for state, _, result_member in pipeline.state_members
    ]
    lines += ["    return r;", "  }"]
    return "\n".join(lines)
