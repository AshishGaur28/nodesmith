"""Message and service types as the manifest declares them (SPEC-01 sections 4.8 and 7).

The manifest lists, under ``[interfaces]``, the fields it reads and writes as a map from a
dotted leaf path (``header.frame_id``) to a ROS primitive type. This module turns such a map
into a tree of canonical types and keeps track of declarations that were already reported
as inconsistent.
"""

from ..diagnostics import BuildError, Cascade, Report

# ROS primitive -> canonical type (SPEC-02 section 2.1).
_CANONICAL = {
    "bool": "bool",
    "byte": "int32",
    "char": "int32",
    "int8": "int32",
    "uint8": "int32",
    "int16": "int32",
    "uint16": "int32",
    "int32": "int32",
    "uint32": "int64",
    "int64": "int64",
    "uint64": "int64",
    "float32": "float32",
    "float64": "float64",
    "string": "string",
    "time": "float64",
    "duration": "float64",
}


def canonical_type(ros_type: str) -> str:
    """Canonical type of a ROS primitive or ``T[]`` (``byte[]`` and ``uint8[]`` are ``bytes``)."""
    if ros_type in ("byte[]", "uint8[]"):
        return "bytes"
    if ros_type.endswith("[]"):
        return _CANONICAL[ros_type[:-2]] + "[]"
    return _CANONICAL[ros_type]


def field_tree(fields: dict[str, str]) -> dict:
    """Nested dict of canonical types for a map of dotted leaf paths to ROS types.

    A path is either a leaf or a prefix of another path, never both (``ERR_SEM_112``)."""
    tree: dict = {}
    for path in sorted(fields):
        *parents, leaf = path.split(".")
        node = tree
        for name in parents:
            if name in node and not isinstance(node[name], dict):
                raise BuildError("ERR_SEM_112", f"{name!r} is both a field and a prefix")
            node = node.setdefault(name, {})
        if leaf in node:
            raise BuildError("ERR_SEM_112", f"{path!r} is both a field and a prefix")
        node[leaf] = canonical_type(fields[path])
    return tree


class InterfaceTable:
    """The manifest's ``[interfaces]`` declarations, looked up by type symbol.

    A type with no declaration has no fields (a pass-through type needs none)."""

    def __init__(self, declarations: dict):
        self.declarations = declarations
        self._inconsistent: set[str] = set()

    def message_fields(self, type_symbol: str) -> dict:
        """Field tree of a message type."""
        return self._tree(type_symbol, "fields")

    def service_fields(self, type_symbol: str, side: str) -> dict:
        """Field tree of a service's ``request`` or ``response``."""
        return self._tree(type_symbol, side)

    def _tree(self, type_symbol: str, part: str) -> dict:
        if type_symbol in self._inconsistent:
            raise Cascade  # the declaration was already reported; do not report each use
        return field_tree(self.declarations.get(type_symbol, {}).get(part, {}))

    def validate(self, report: Report) -> None:
        """Check every declaration once, up front (``ERR_SEM_112``)."""
        for type_symbol in self.declarations:
            with report.guard(("interfaces", type_symbol)):
                try:
                    if "/msg/" in type_symbol:
                        self.message_fields(type_symbol)
                    else:
                        self.service_fields(type_symbol, "request")
                        self.service_fields(type_symbol, "response")
                except BuildError:
                    self._inconsistent.add(type_symbol)
                    raise
