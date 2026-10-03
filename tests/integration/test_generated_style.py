"""The generated files are laid out to be read: a column limit, no stray whitespace, no wall of
text. Every example the generator supports is checked, plus the starter file for user functions."""

import re
from pathlib import Path

import pytest

from nodesmith.api import compile_manifest
from nodesmith.generators.cpp import Unsupported, generate_package, starter_files
from nodesmith.generators.cpp.layout import LIMIT

ROOT = Path(__file__).resolve().parents[2]


def _files(example: Path) -> dict[str, str]:
    ir = compile_manifest(example).ir
    logic = starter_files(ir) if ir.get("functions") else None
    try:
        files = generate_package(ir, logic_files=logic)
    except Unsupported:
        pytest.skip("not supported by the generator yet")
    if logic:
        files.update({f"starter/{name}": text for name, text in logic.items()})
    return files


@pytest.mark.parametrize(
    "example", sorted((ROOT / "examples").glob("*.toml")), ids=lambda p: p.stem
)
def test_generated_files_follow_the_layout_rules(example):
    problems = []
    for name, text in _files(example).items():
        assert text.endswith("\n") and not text.endswith("\n\n"), f"{name}: ends with blank lines"
        lines = text.split("\n")[:-1]
        for number, line in enumerate(lines, 1):
            if len(line) > LIMIT:
                problems.append(f"{name}:{number}: {len(line)} columns")
            if line != line.rstrip() or "\t" in line:
                problems.append(f"{name}:{number}: trailing whitespace or tab")
            two_blank = number > 1 and not line and not lines[number - 2]
            if two_blank and not name.endswith(".py"):  # PEP 8 wants two before a def
                problems.append(f"{name}:{number}: two blank lines in a row")
    assert not problems, "\n".join(problems[:20])


_RELEASE_SENSITIVE = re.compile(
    r"(?<!binding::)(create_service<|create_subscription<|create_timer\(|"
    r"add_on_set_parameters_callback)|rclcpp::init|rclcpp::shutdown|rclcpp::executors::|"
    r"ServicesQoS|SubscriptionOptions"
)


@pytest.mark.parametrize(
    "example", sorted((ROOT / "examples").glob("*.toml")), ids=lambda p: p.stem
)
def test_release_sensitive_rclcpp_calls_live_only_in_the_binding(example):
    """Everything that can differ between ROS 2 releases is called through ros_binding.hpp, so
    supporting another release means replacing that one file."""
    leaks = []
    for name, text in _files(example).items():
        if name.endswith("ros_binding.hpp") or not name.endswith((".cpp", ".hpp")):
            continue
        leaks += [f"{name}: {found.group(0)}" for found in _RELEASE_SENSITIVE.finditer(text)]
    assert not leaks, "\n".join(leaks)
