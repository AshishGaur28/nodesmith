"""The generated C++ is clean under ``ament_uncrustify``, the ROS 2 lint check (uncrustify 0.78, the
version in the Jazzy distribution, with ``tests/support/ament_code_style_0_78.cfg``).

Skipped where ``uncrustify`` is not installed (set ``UNCRUSTIFY`` to its path, or put it on
``PATH``). The ``jazzy`` job runs the real ``ament_uncrustify`` on the built packages as well.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from nodesmith.api import compile_manifest
from nodesmith.generators.cpp import Unsupported, generate_package, starter_files

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "tests" / "support" / "ament_code_style_0_78.cfg"
UNCRUSTIFY = os.environ.get("UNCRUSTIFY") or shutil.which("uncrustify")
MANIFESTS = [*sorted((ROOT / "examples").glob("*.toml")), ROOT / "tests/fixtures/semantics.toml"]

pytestmark = pytest.mark.skipif(not UNCRUSTIFY, reason="uncrustify is not installed")


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda p: p.stem)
def test_uncrustify_would_change_nothing(manifest):
    ir = compile_manifest(manifest).ir
    logic = starter_files(ir) if ir.get("functions") else None
    try:
        files = generate_package(ir, logic_files=logic)
    except Unsupported:
        pytest.skip("not supported by the generator yet")
    changed = []
    for path, text in files.items():
        if not path.endswith((".cpp", ".hpp")) or path.startswith("logic/"):
            continue
        out = subprocess.run(
            [UNCRUSTIFY, "-q", "-c", str(CONFIG), "-l", "CPP"],
            input=text,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        if out != text:
            changed.append(path)
    assert not changed, f"uncrustify would reformat: {changed}"
