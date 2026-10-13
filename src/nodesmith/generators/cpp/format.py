"""Formats generated C++ with clang-format and the ROS 2 style (``templates/cpp_rclcpp/clang-format``).

The formatter version is pinned in ``pyproject.toml``, so the same input gives the same bytes on
every machine (SPEC-03 section 2, rule 1). The same configuration is written into each package as
``.clang-format``, so editors and ``ament_clang_format`` agree with the generated files.
"""

import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from .errors import Unsupported
from .templates import TEMPLATES

CONFIG = TEMPLATES / "clang-format"


@lru_cache(maxsize=1)
def _executable() -> str:
    """The pinned ``clang-format`` that came with this installation (the PyPI wheel), else the
    one on ``PATH``."""
    beside_python = Path(sys.executable).parent / "clang-format"
    found = str(beside_python) if beside_python.exists() else shutil.which("clang-format")
    if not found:
        raise Unsupported("clang-format is not installed (pip install clang-format)")
    return found


def format_cpp(text: str, filename: str = "file.cpp") -> str:
    """``text`` as C++ source in the ROS 2 style. ``filename`` only selects header or source rules."""
    result = subprocess.run(
        [_executable(), f"--assume-filename={filename}", f"--style=file:{CONFIG}"],
        input=text,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise Unsupported(f"clang-format failed on {filename}: {result.stderr.strip()[:300]}")
    return result.stdout


def config_text() -> str:
    """Text of the ``.clang-format`` file that goes into every package."""
    return CONFIG.read_text()
