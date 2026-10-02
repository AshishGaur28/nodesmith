"""Names and type mappings shared by the C++ emitters (SPEC-03 sections 3 and 4.1)."""

import re

from ...semantics.language import TARGET_KEYWORDS

# Canonical type -> C++ type (SPEC-03 section 3).
CPP_TYPE = {
    "bool": "bool",
    "int32": "std::int32_t",
    "int64": "std::int64_t",
    "float32": "float",
    "float64": "double",
    "string": "std::string",
    "bytes": "std::vector<std::uint8_t>",
}

# ROS primitive -> the C++ type ROS gives that message field.
ROS_CPP_TYPE = {
    "bool": "bool",
    "byte": "std::uint8_t",
    "char": "std::uint8_t",
    "int8": "std::int8_t",
    "uint8": "std::uint8_t",
    "int16": "std::int16_t",
    "uint16": "std::uint16_t",
    "int32": "std::int32_t",
    "uint32": "std::uint32_t",
    "int64": "std::int64_t",
    "uint64": "std::uint64_t",
    "float32": "float",
    "float64": "double",
    "string": "std::string",
}

# ROS sinks narrower than their canonical type: a value written there is range-checked (SPEC-02 2.1).
NARROW_INTS = {
    "int8": "std::int8_t",
    "uint8": "std::uint8_t",
    "int16": "std::int16_t",
    "uint16": "std::uint16_t",
    "uint32": "std::uint32_t",
    "char": "std::uint8_t",
    "byte": "std::uint8_t",
}


def cpp_type(canonical: str) -> str:
    """C++ type of a canonical type (``std::vector<T>`` for ``T[]``)."""
    if canonical.endswith("[]"):
        return f"std::vector<{CPP_TYPE[canonical[:-2]]}>"
    return CPP_TYPE[canonical]


def snake(name: str) -> str:
    """CamelCase to snake_case, the way rosidl derives header names
    (``Imu`` -> ``imu``, ``LaserScan`` -> ``laser_scan``, ``PointCloud2`` -> ``point_cloud2``)."""
    spaced = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", spaced).lower()


def camel(name: str) -> str:
    """snake_case to CamelCase (``imu_filter_node`` -> ``ImuFilterNode``)."""
    return "".join(part[:1].upper() + part[1:] for part in name.split("_") if part)


def split_type(symbol: str) -> tuple[str, str, str]:
    """``pkg/msg/Name`` -> ``("pkg", "msg", "Name")``."""
    package, kind, name = symbol.split("/")
    return package, kind, name


def message_cpp(symbol: str) -> str:
    """C++ type name of a message or service type (``sensor_msgs::msg::Imu``)."""
    package, kind, name = split_type(symbol)
    return f"{package}::{kind}::{name}"


def message_header(symbol: str) -> str:
    """Include path of a message or service type (``sensor_msgs/msg/imu.hpp``)."""
    package, kind, name = split_type(symbol)
    return f"{package}/{kind}/{snake(name)}.hpp"


def cxx_namespace(namespace: str) -> str:
    """C++ namespace for a ROS namespace: ``/sensors/chassis`` -> ``sensors::chassis``;
    the root namespace becomes ``generated``."""
    parts = [p + "_" if p in TARGET_KEYWORDS else p for p in namespace.strip("/").split("/") if p]
    return "::".join(parts) or "generated"


def member(name: str) -> str:
    """C++ member or local name for a manifest identifier (a keyword gets a trailing ``_``)."""
    return name + "_" if name in TARGET_KEYWORDS else name


def path_ident(path: str) -> str:
    """A field path as part of an identifier (``a.b`` -> ``a__b``)."""
    return path.replace(".", "__")
