"""Names and type mappings shared by the C++ emitters (SPEC-03 §3, §4.1)."""
import re

from ..expr import TARGET_KEYWORDS

CPP_TYPE = {"bool": "bool", "int32": "std::int32_t", "int64": "std::int64_t", "float32": "float", "float64": "double",
            "string": "std::string", "bytes": "std::vector<std::uint8_t>"}
ROS_CPP_TYPE = {"bool": "bool", "byte": "std::uint8_t", "char": "std::uint8_t", "int8": "std::int8_t", "uint8": "std::uint8_t",
                "int16": "std::int16_t", "uint16": "std::uint16_t", "int32": "std::int32_t", "uint32": "std::uint32_t",
                "int64": "std::int64_t", "uint64": "std::uint64_t", "float32": "float", "float64": "double", "string": "std::string"}
NARROW_INTS = {"int8": "std::int8_t", "uint8": "std::uint8_t", "int16": "std::int16_t", "uint16": "std::uint16_t",
               "uint32": "std::uint32_t", "char": "std::uint8_t", "byte": "std::uint8_t"}   # sinks whose range is checked (SPEC-02 §2.1)


def cpp_type(canonical: str) -> str:
    """std::vector<T> for T[]; scalars as in CPP_TYPE."""
    return f"std::vector<{CPP_TYPE[canonical[:-2]]}>" if canonical.endswith("[]") else CPP_TYPE[canonical]


def snake(name: str) -> str:
    """CamelCase -> snake_case the way rosidl derives header names (Imu -> imu, LaserScan -> laser_scan, PointCloud2 -> point_cloud2)."""
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s).lower()


def camel(name: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in name.split("_") if p)


def split_type(symbol: str):
    pkg, kind, name = symbol.split("/")
    return pkg, kind, name


def message_cpp(symbol: str) -> str:
    pkg, kind, name = split_type(symbol)
    return f"{pkg}::{kind}::{name}"


def message_header(symbol: str) -> str:
    pkg, kind, name = split_type(symbol)
    return f"{pkg}/{kind}/{snake(name)}.hpp"


def cxx_namespace(namespace: str) -> str:
    """'/sensors/chassis' -> 'sensors::chassis'; the root namespace becomes 'generated'."""
    parts = [p + "_" if p in TARGET_KEYWORDS else p for p in namespace.strip("/").split("/") if p]
    return "::".join(parts) or "generated"


def member(name: str) -> str:
    """Name of a C++ data member or local derived from a manifest identifier (they are already [a-z][a-z0-9_]*)."""
    return name + "_" if name in TARGET_KEYWORDS else name


def path_ident(path: str) -> str:
    return path.replace(".", "__")
