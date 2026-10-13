"""The generated C++ is in the ROS 2 style, and formatting it again changes nothing."""

from pathlib import Path

import pytest

from nodesmith.api import compile_manifest
from nodesmith.generators.cpp import Unsupported, generate_package, starter_files
from nodesmith.generators.cpp.format import config_text, format_cpp

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = sorted((ROOT / "examples").glob("*.toml"))


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.stem)
def test_formatting_generated_code_again_changes_nothing(example):
    ir = compile_manifest(example).ir
    logic = starter_files(ir) if ir.get("functions") else None
    try:
        files = generate_package(ir, logic_files=logic)
    except Unsupported:
        pytest.skip("not supported by the generator yet")
    for path, text in files.items():
        if path.endswith((".hpp", ".cpp")) and not path.startswith("logic/"):
            assert format_cpp(text, path) == text, path


def test_the_package_carries_the_style_it_was_formatted_with():
    files = generate_package(compile_manifest(ROOT / "examples/01_simple_pubsub.toml").ir)
    assert files[".clang-format"] == config_text()
    assert "ColumnLimit: 100" in files[".clang-format"]
    assert "AfterFunction: true" in files[".clang-format"]
