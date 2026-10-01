"""The generated node, compiled against the rclcpp stand-in in tests/support/ros_stub, behaves as the specs say.

The stand-in proves the generated glue compiles against the ROS signatures we know and does the right thing; it does not prove that
real rclcpp accepts it. The Jazzy CI job (.github/workflows/ci.yml) is the real check.
"""
from pathlib import Path

import pytest

from nodesmith.compiler import compile_manifest
from tests.support import harness, node_harness

ROOT = Path(__file__).resolve().parents[2]
DRIVERS = sorted((ROOT / "tests" / "cpp" / "drivers").glob("[0-9]*.cpp"))
pytestmark = pytest.mark.skipif(harness.compiler() is None, reason="no C++ compiler")


@pytest.mark.parametrize("driver", DRIVERS, ids=lambda p: p.stem)
def test_generated_node_behaves(driver):
    ir = compile_manifest(ROOT / "examples" / f"{driver.stem}.toml").ir
    source = f'#include "{driver.parent / "check.hpp"}"\n' + driver.read_text().replace('#include "check.hpp"', "")
    code, output = node_harness.run_driver(ir, source)
    assert code == 0 and output.strip().endswith("ok"), output


def test_the_semantics_fixture_compiles_as_a_node():
    ir = compile_manifest(ROOT / "tests" / "fixtures" / "semantics.toml").ir
    code, output = node_harness.run_driver(ir, "#include \"semantics_node/semantics_node_node.hpp\"\nint main() { auto n = std::make_shared<test::SemanticsNode>(); return 0; }\n")
    assert code == 0, output
