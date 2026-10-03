"""The generated C++ pipelines compute what the spec says, on random inputs (differential test against tests/support/interpreter.py)."""

import re
import shutil
from pathlib import Path

import pytest

from nodesmith.api import compile_manifest
from tests.support import harness

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "semantics.toml"
EXAMPLES = sorted((ROOT / "examples").glob("*.toml"))
pytestmark = pytest.mark.skipif(harness.compiler() is None, reason="no C++ compiler")


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_example_pipelines_match_the_interpreter(path):
    result = compile_manifest(path)
    assert result.ok, result.report.diagnostics
    assert harness.differential(result.ir, n=30) == []


@pytest.mark.parametrize("seed", [1, 2])
def test_every_operator_function_and_sink_matches_the_interpreter(seed):
    result = compile_manifest(FIXTURE)
    assert result.ok, result.report.diagnostics
    assert harness.differential(result.ir, n=40, seed=seed) == []


def test_the_harness_notices_a_wrong_helper(tmp_path, monkeypatch):
    """A saturating `+` in place of the wrapping one must be caught, or the test above proves nothing."""
    templates = tmp_path / "templates"
    shutil.copytree(ROOT / "templates" / "cpp_rclcpp", templates)
    header = templates / "runtime_helpers.hpp"
    text = header.read_text()
    broken = re.sub(
        r"inline T wrap_add\(T a, T b\) \{.*?\n\}\n",
        "inline T wrap_add(T a, T b) {\n"
        "  T r;\n"
        "  if (__builtin_add_overflow(a, b, &r)) return std::numeric_limits<T>::max();\n"
        "  return r;\n"
        "}\n",
        text,
        count=1,
        flags=re.S,
    )
    assert broken != text
    header.write_text(broken)
    from nodesmith.generators.cpp import templates as cpp_templates

    monkeypatch.setattr(cpp_templates, "TEMPLATES", templates)
    assert harness.differential(compile_manifest(FIXTURE).ir, n=40, seed=3) != []
