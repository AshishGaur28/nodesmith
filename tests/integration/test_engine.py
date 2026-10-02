"""The engine (``engine.hpp``) is the ROS-free core of a node: it is compiled and driven here with no ROS and no stand-in."""

import subprocess
from pathlib import Path

import pytest

from nodesmith.api import compile_manifest
from nodesmith.generators.cpp import generate_package
from tests.support import harness

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(harness.compiler() is None, reason="no C++ compiler")

DRIVER = r"""
#include <cstdio>
#include <cstdlib>
#include <string>
#include "counter_node/engine.hpp"

#define CHECK(c) do { if (!(c)) { std::printf("FAILED line %d: %s\n", __LINE__, #c); std::exit(1); } } while (0)

struct Empty {};

int main() {
  demo::CounterNodeEngine engine;
  CHECK(!engine.start(demo::Params{}));               // the defaults are valid
  {
    auto run = engine.enter();
    CHECK(!run);                                      // the gate is closed until open()
  }
  engine.open();

  {
    auto run = engine.enter();
    CHECK(run);
    auto first = engine.run_tick(run);
    CHECK(first && first->out__count_pub__data == 1);  // state 0 + step 1
    auto second = engine.run_tick(run);
    CHECK(second && second->out__count_pub__data == 2);  // the first run committed its state
    CHECK(engine.run_reset(run, Empty{}));
    CHECK(engine.run_tick(run)->out__count_pub__data == 1);  // reset set the count back to 0
  }

  demo::Params bad = engine.parameters();
  bad.step = 0;                                       // validation: step >= 1
  auto why = engine.update(bad);
  CHECK(why && why->find("step") != std::string::npos);
  CHECK(engine.parameters().step == 1);               // a rejected update changes nothing

  demo::Params good = engine.parameters();
  good.step = 4;
  CHECK(!engine.update(good));
  {
    auto run = engine.enter();
    CHECK(engine.run_tick(run)->out__count_pub__data == 5);  // 1 + new step 4
  }

  engine.report(r2d::Code::ERR_RUN_103, demo::names::kParametersWho);
  int drained = 0;
  engine.drain_diagnostics([&](r2d::Code code, const char * who) {
    CHECK(code == r2d::Code::ERR_RUN_103 && std::string(who) == "parameters");
    ++drained;
  });
  CHECK(drained == 1);
  std::puts("ok");
  return 0;
}
"""


def test_the_engine_runs_pipelines_and_guards_parameters_without_ros(tmp_path):
    ir = compile_manifest(ROOT / "examples" / "03_state_machine.toml").ir
    for relative, text in generate_package(ir, core_only=True).items():
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text(text)
    (tmp_path / "driver.cpp").write_text(DRIVER)
    build = subprocess.run(
        [
            harness.compiler(),
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-pthread",
            f"-I{tmp_path}/include",
            str(tmp_path / "driver.cpp"),
            "-o",
            str(tmp_path / "driver"),
        ],
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr[:3000]
    run = subprocess.run([str(tmp_path / "driver")], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0 and run.stdout.strip() == "ok", run.stdout + run.stderr
