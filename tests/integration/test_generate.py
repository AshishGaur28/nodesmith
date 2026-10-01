"""`nodesmith generate`: determinism, golden packages, atomic emission, and what is refused."""
import filecmp
import subprocess
import sys
from pathlib import Path

import pytest

from nodesmith.cli.main import main
from nodesmith.compiler import compile_manifest
from nodesmith.cpp.generate import package, runtime_header

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "golden"
EXAMPLES = sorted(p.name for p in GOLDEN.iterdir())


@pytest.mark.parametrize("name", EXAMPLES)
def test_generated_package_equals_the_golden_one(name):
    files = package(compile_manifest(ROOT / "examples" / f"{name}.toml").ir)
    golden = {str(p.relative_to(GOLDEN / name)): p.read_text() for p in (GOLDEN / name).rglob("*") if p.is_file()}
    assert {k: v for k, v in files.items() if not k.endswith("runtime.hpp")} == golden


def test_every_generated_package_carries_the_same_runtime_header():
    assert runtime_header() == (ROOT / "templates/cpp_rclcpp/concurrency_primitives.hpp").read_text() + "\n" + (ROOT / "templates/cpp_rclcpp/runtime_helpers.hpp").read_text()


def test_the_bundled_primitives_are_the_tested_reference_ones():
    assert (ROOT / "templates/cpp_rclcpp/concurrency_primitives.hpp").read_text() == (ROOT / "docs/specs/reference/cpp/concurrency_primitives.hpp").read_text()


def test_output_is_byte_identical_across_runs(tmp_path):
    for n in ("a", "b"):
        assert main(["generate", "--manifest", str(ROOT / "examples/07_gain_service.toml"), "--output-dir", str(tmp_path / n)]) == 0
    cmp = filecmp.dircmp(tmp_path / "a", tmp_path / "b")
    assert not cmp.diff_files and not cmp.left_only and not cmp.right_only
    assert (tmp_path / "a" / "include" / "gain_node" / "runtime.hpp").read_text() == runtime_header()


def test_a_second_run_replaces_the_package_and_leaves_no_temporary_directory(tmp_path):
    out = tmp_path / "pkg"
    assert main(["generate", "--manifest", str(ROOT / "examples/01_simple_pubsub.toml"), "--output-dir", str(out)]) == 0
    (out / "stale.txt").write_text("left over")
    assert main(["generate", "--manifest", str(ROOT / "examples/01_simple_pubsub.toml"), "--output-dir", str(out)]) == 0
    assert not (out / "stale.txt").exists()
    assert [p.name for p in tmp_path.iterdir()] == ["pkg"]


def test_nothing_is_written_when_the_manifest_is_invalid(tmp_path):
    out = tmp_path / "pkg"
    assert main(["generate", "--manifest", str(ROOT / "conformance/invalid/sem109_constant_division_by_zero.toml"), "--output-dir", str(out)]) == 4
    assert not out.exists()


@pytest.mark.parametrize("example", ["04_all_extensions", "05_realtime_controller", "06_secured_filter"])
def test_extension_blocks_are_refused_until_they_are_implemented(tmp_path, capsys, example):
    assert main(["generate", "--manifest", str(ROOT / "examples" / f"{example}.toml"), "--output-dir", str(tmp_path / "pkg")]) == 1
    assert "not implemented" in capsys.readouterr().err and not (tmp_path / "pkg").exists()


def test_the_python_target_is_not_implemented_yet(tmp_path, capsys):
    assert main(["generate", "--manifest", str(ROOT / "examples/08_link_watchdog.toml"), "--target-language", "python", "--output-dir", str(tmp_path / "p")]) == 1
    assert "python" in capsys.readouterr().err


def test_target_override_is_checked_like_the_manifest_value(tmp_path):
    # a python target rejects extension blocks (ERR_SEM_114) even when chosen on the command line
    assert main(["generate", "--manifest", str(ROOT / "examples/05_realtime_controller.toml"), "--target-language", "python", "--output-dir", str(tmp_path / "p")]) == 4


def test_package_xml_depends_on_only_the_chosen_rmw(tmp_path):
    assert main(["generate", "--manifest", str(ROOT / "examples/02_filter_pipeline.toml"), "--output-dir", str(tmp_path / "p")]) == 0
    xml = (tmp_path / "p" / "package.xml").read_text()
    assert "<exec_depend>rmw_fastrtps_cpp</exec_depend>" in xml and "cyclone" not in xml and "zenoh" not in xml
