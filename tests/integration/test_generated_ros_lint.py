"""The generated launch file and CMake file are clean under the ROS 2 lint tools they will meet in
``colcon test``: ``ament_flake8`` (flake8 with ROS's plugins and settings) and ``ament_lint_cmake``
(cmakelint, vendored in ``tests/support/ament_lint_cmake``). The ``jazzy`` job runs the real tools.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from nodesmith.api import compile_manifest
from nodesmith.generators.cpp import Unsupported, generate_package, starter_files

ROOT = Path(__file__).resolve().parents[2]
SUPPORT = ROOT / "tests" / "support"
MANIFESTS = sorted((ROOT / "examples").glob("*.toml"))


def _package(manifest):
    ir = compile_manifest(manifest).ir
    logic = starter_files(ir) if ir.get("functions") else None
    try:
        return generate_package(ir, logic_files=logic)
    except Unsupported:
        pytest.skip("not supported by the generator yet")


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda p: p.stem)
def test_the_launch_file_passes_ament_flake8(manifest, tmp_path):
    pytest.importorskip("flake8")
    pytest.importorskip("flake8_quotes")
    for path, text in _package(manifest).items():
        if path.endswith(".py"):
            file = tmp_path / Path(path).name
            file.write_text(text)
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "flake8",
                    "--config",
                    str(SUPPORT / "ament_flake8.ini"),
                    str(file),
                ],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stdout[:2000]


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda p: p.stem)
def test_the_cmake_file_passes_ament_lint_cmake(manifest, tmp_path):
    (tmp_path / "CMakeLists.txt").write_text(_package(manifest)["CMakeLists.txt"])
    env = {**os.environ, "PYTHONPATH": str(SUPPORT)}
    result = subprocess.run(
        [sys.executable, "-m", "ament_lint_cmake.main", str(tmp_path / "CMakeLists.txt")],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stdout[:2000] + result.stderr[:500]
