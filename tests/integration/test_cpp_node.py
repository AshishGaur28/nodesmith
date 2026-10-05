"""The generated node, compiled against the rclcpp stand-in in tests/support/ros_stub, behaves as the specs say.

The stand-in proves the generated glue compiles against the ROS signatures we know and does the right thing; it does not prove that
real rclcpp accepts it. The Jazzy CI job (.github/workflows/ci.yml) is the real check.
"""

from pathlib import Path

import pytest

from nodesmith.api import compile_manifest
from tests.support import harness, node_harness, user_functions

ROOT = Path(__file__).resolve().parents[2]
DRIVERS = sorted((ROOT / "tests" / "cpp" / "drivers").glob("[0-9]*.cpp"))
pytestmark = pytest.mark.skipif(harness.compiler() is None, reason="no C++ compiler")


def _logic_source(ir):
    """The test implementation of the manifest's functions (standing in for the user's `logic/`)."""
    if not ir.get("functions"):
        return ""
    return user_functions.IMPLEMENTATIONS[ir["node_meta"]["name"]].cpp


@pytest.mark.parametrize("driver", DRIVERS, ids=lambda p: p.stem)
def test_generated_node_behaves(driver):
    ir = compile_manifest(ROOT / "examples" / f"{driver.stem}.toml").ir
    source = f'#include "{driver.parent / "check.hpp"}"\n' + driver.read_text().replace(
        '#include "check.hpp"', ""
    )
    code, output = node_harness.run_driver(ir, source, logic_source=_logic_source(ir))
    assert code == 0 and output.strip().endswith("ok"), output


def test_the_semantics_fixture_compiles_as_a_node():
    ir = compile_manifest(ROOT / "tests" / "fixtures" / "semantics.toml").ir
    source = (
        '#include "semantics_node/semantics_node_node.hpp"\n'
        "int main() {\n"
        "  auto n = std::make_shared<test::SemanticsNode>();\n"
        "  return 0;\n}\n"
    )
    code, output = node_harness.run_driver(ir, source, logic_source=_logic_source(ir))
    assert code == 0, output


def test_a_function_missing_from_the_logic_folder_faults_instead_of_guessing():
    ir = compile_manifest(ROOT / "examples" / "09_user_functions.toml").ir
    source = (
        '#include "check.hpp"\n'
        '#include "speed_planner_node/speed_planner_node_node.hpp"\n'
        "int main() {\n"
        "  auto node = std::make_shared<planning::SpeedPlannerNode>();\n"
        "  std_msgs::msg::Float64 in;\n"
        "  in.data = 4.0;\n"
        '  node->deliver("/planning/range", in);\n'
        '  CHECK(node->sent<std_msgs::msg::Float64>("/planning/speed").empty());\n'
        '  std::puts("ok");\n  return 0;\n}\n'
    )
    source = source.replace('#include "check.hpp"', f'#include "{DRIVERS[0].parent / "check.hpp"}"')
    code, output = node_harness.run_driver(ir, source, logic_source="")
    assert code == 0 and "logic::plan_speed is not implemented" in output, output


def test_a_start_up_failure_ends_with_a_message_and_a_failure_status_not_an_abort():
    ir = compile_manifest(ROOT / "examples" / "02_filter_pipeline.toml").ir
    assert node_harness.run_main(ir) == (0, "")
    code, stderr = node_harness.run_main(ir, {"STUB_PARAM_FAILS": "1"})
    assert code == 1, f"exit status {code} (an abort would be 134)"
    assert "imu_filter_node: stub: invalid value for alpha" in stderr
