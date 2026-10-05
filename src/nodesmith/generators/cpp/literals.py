"""C++ source text for constants: string literals, numbers, and whole initialiser values."""

from .naming import cpp_type


def cpp_string(text: str) -> str:
    """A C++ string literal for ``text``; non-ASCII bytes become octal escapes."""
    pieces = []
    for byte in text.encode("utf-8"):
        char = chr(byte)
        if char == "\\":
            pieces.append("\\\\")
        elif char == '"':
            pieces.append('\\"')
        elif char == "\n":
            pieces.append("\\n")
        elif 32 <= byte < 127:
            pieces.append(char)
        else:
            pieces.append(f"\\{byte:03o}")
    return '"' + "".join(pieces) + '"'


def literal(value, canonical_type: str) -> str:
    """A C++ constant of a canonical scalar type."""
    if canonical_type == "bool":
        return "true" if value else "false"
    if canonical_type == "string":
        return f"std::string({cpp_string(value)})"
    if canonical_type == "int32":
        return f"std::int32_t{{{value}}}"
    if canonical_type == "int64":
        if -(2**31) <= value < 2**31:
            return f"std::int64_t{{{value}}}"
        return f"std::int64_t{{INT64_C({value})}}"  # a literal that does not fit in int
    text = repr(float(value))
    if not any(marker in text for marker in ".eEn"):
        text += ".0"
    if canonical_type == "float32":
        return f"static_cast<float>({text})"
    return text


def value_literal(canonical_type: str, value) -> str:
    """A C++ constant of any canonical type, including arrays (``std::vector<T>{...}``)."""
    if canonical_type.endswith("[]") or canonical_type == "bytes":
        element = "uint8" if canonical_type == "bytes" else canonical_type[:-2]
        items = ", ".join(literal(item, element) for item in value)
        return f"{cpp_type(canonical_type)}{{{items}}}"
    return literal(value, canonical_type)
