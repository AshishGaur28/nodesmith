"""Where the C++ templates live, and the pieces of the output that are not templated."""

import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

# The templates are read from the repository checkout (or ``NODESMITH_TEMPLATES``).
# When nodesmith is packaged for installation they must be bundled as package data instead.
TEMPLATES = Path(
    os.environ.get("NODESMITH_TEMPLATES")
    or Path(__file__).resolve().parents[4] / "templates" / "cpp_rclcpp"
)


def environment() -> Environment:
    """Jinja environment for the templates. Undefined variables are errors, so a template can
    never silently render a missing value."""
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def binding_header() -> str:
    """Text of ``ros_binding.hpp``: the only file that calls the release-sensitive parts of
    rclcpp. It is identical in every generated package."""
    return (TEMPLATES / "ros_binding.hpp").read_text()


def runtime_header() -> str:
    """Text of ``runtime.hpp``: the SPEC-12 primitives, then the numeric helpers and the
    diagnostics ring. It is identical in every generated package."""
    primitives = (TEMPLATES / "concurrency_primitives.hpp").read_text()
    helpers = (TEMPLATES / "runtime_helpers.hpp").read_text()
    return primitives + "\n" + helpers
