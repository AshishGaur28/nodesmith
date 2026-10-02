"""C++ target: turns the IR into an ``ament_cmake`` package for ROS 2 (``rclcpp``).

Public API: ``generate_package`` (IR -> files), ``write_package`` (files -> disk) and the
``Unsupported`` error. The modules, in the order the data flows through them:

``dag`` (one pipeline -> C++ function body), ``parameters`` (validation and declaration),
``node`` (ROS members, creation code, run methods), ``package`` (assembles and writes the
files from the templates in ``templates/cpp_rclcpp``).
"""

from .errors import Unsupported
from .logic import has_user_logic, read_logic_dir, starter_files, unimplemented
from .package import generate_package, write_package
from .templates import runtime_header

__all__ = [
    "Unsupported",
    "generate_package",
    "has_user_logic",
    "read_logic_dir",
    "starter_files",
    "unimplemented",
    "runtime_header",
    "write_package",
]
