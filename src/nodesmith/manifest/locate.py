"""Finds where in the source file a manifest path lives.

A diagnostic knows *where in the document* it was found (``("pipelines", 2, "expressions",
0)``); ``Locator`` turns that into a line and column. A path that is not found resolves to
its closest existing prefix, so a missing key points at its table.

YAML and JSON use the YAML parser's node marks (JSON is read as YAML for this). The TOML
reader in the standard library gives no positions, so ``_scan_toml`` records where tables,
keys and array elements start.
"""

import re

import yaml

_STRING = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'[^']*'")
_KEY_PART = re.compile(r"\s*(?:\"((?:[^\"\\]|\\.)*)\"|'([^']*)'|([A-Za-z0-9_-]+))\s*")
# Inside an array value: a string, a bracket, or a comment.
_ARRAY_TOKEN = re.compile(_STRING.pattern + r"|[\[\]]|#.*")

Position = tuple[int, int]  # (line, column), both starting at 1


def _split_key(text: str):
    """Split a possibly dotted, possibly quoted TOML key: ``a."b/c".d`` -> ``(["a", "b/c", "d"],
    text after the key)``, or ``(None, text)`` if it does not start with a key."""
    parts, position = [], 0
    while True:
        match = _KEY_PART.match(text, position)
        if not match:
            return None, text
        parts.append(next(group for group in match.groups() if group is not None))
        position = match.end()
        if position < len(text) and text[position] == ".":
            position += 1
            continue
        return parts, text[position:]


class _TomlScanner:
    """Walks the lines of a TOML file and records the position of every table, key and array
    element, keyed by its path in the document."""

    def __init__(self, text: str):
        self.lines = text.splitlines()
        self.positions: dict[tuple, Position] = {}
        self._array_counts: dict[tuple, int] = {}  # how many ``[[x]]`` entries so far
        self._table: tuple = ()  # path of the current table

    def scan(self) -> dict[tuple, Position]:
        index = 0
        while index < len(self.lines):
            line = self.lines[index].strip()
            if line.startswith("[["):
                self._array_table_header(index, line[2:])
            elif line.startswith("["):
                self._table_header(index, line[1:])
            elif line and not line.startswith("#"):
                index = self._assignment(index)
            index += 1
        self._add_implicit_parents()
        return self.positions

    def _resolve(self, parts: list[str]) -> tuple:
        """Path of a header, with the index of the current entry inserted below each array."""
        path: tuple = ()
        for part in parts:
            path += (part,)
            if path in self._array_counts:
                path += (self._array_counts[path] - 1,)
        return path

    def _array_table_header(self, index: int, text: str) -> None:
        parts, _ = _split_key(text.rstrip("] "))
        if parts:
            base = (*self._resolve(parts[:-1]), parts[-1])
            self._array_counts[base] = self._array_counts.get(base, 0) + 1
            self._table = (*base, self._array_counts[base] - 1)
            self.positions[self._table] = (index + 1, self.lines[index].index("[") + 1)

    def _table_header(self, index: int, text: str) -> None:
        parts, _ = _split_key(text.rstrip("] "))
        if parts:
            self._table = self._resolve(parts)
            self.positions[self._table] = (index + 1, self.lines[index].index("[") + 1)

    def _assignment(self, index: int) -> int:
        """Record ``key = value``; return the index of the last line the value occupies."""
        raw = self.lines[index]
        parts, rest = _split_key(raw.strip())
        if not (parts and rest.lstrip().startswith("=")):
            return index
        key = (*self._table, *parts)
        indent = len(raw) - len(raw.lstrip())
        self.positions[key] = (index + 1, indent + 1)
        if rest.lstrip()[1:].lstrip().startswith("["):
            return self._array_elements(index, key)
        return index

    def _array_elements(self, first_line: int, key: tuple) -> int:
        """Record each string element of an array value, which may span several lines."""
        depth, element, line_index = 0, 0, first_line
        while line_index < len(self.lines):
            offset = self.lines[first_line].index("=") + 1 if line_index == first_line else 0
            for token in _ARRAY_TOKEN.finditer(self.lines[line_index][offset:]):
                text = token.group(0)
                if text == "[":
                    depth += 1
                elif text == "]":
                    depth -= 1
                elif text[0] in "\"'" and depth == 1:
                    self.positions[(*key, element)] = (line_index + 1, offset + token.start() + 1)
                    element += 1
            if depth <= 0:
                break
            line_index += 1
        return line_index

    def _add_implicit_parents(self) -> None:
        """A table with no header of its own (``[a.b.c]`` implies ``a`` and ``a.b``) starts
        where its first child does."""
        for path, position in list(self.positions.items()):
            for length in range(1, len(path)):
                self.positions.setdefault(path[:length], position)


class Locator:
    """Looks up the source position of manifest paths for one file."""

    def __init__(self, suffix: str, text: str):
        self._is_toml = suffix.lower() == ".toml"
        self._toml_positions = _TomlScanner(text).scan() if self._is_toml else {}
        self._yaml_root = None
        if not self._is_toml:
            try:
                self._yaml_root = yaml.compose(text)
            except yaml.YAMLError:
                self._yaml_root = None

    def find(self, path) -> Position | None:
        """Position of ``path``, or of its closest existing prefix; ``None`` if nothing matches."""
        path = tuple(path)
        while True:
            hit = self._lookup(path)
            if hit or not path:
                return hit
            path = path[:-1]

    def _lookup(self, path: tuple) -> Position | None:
        if self._is_toml:
            return self._toml_positions.get(path)
        node = self._yaml_root
        if node is None:
            return None
        mark = node.start_mark
        for step in path:
            if isinstance(node, yaml.MappingNode):
                entry = next(
                    ((k, v) for k, v in node.value if getattr(k, "value", None) == step), None
                )
                if entry is None:
                    return None
                mark, node = entry[0].start_mark, entry[1]
            elif (
                isinstance(node, yaml.SequenceNode)
                and isinstance(step, int)
                and step < len(node.value)
            ):
                node = node.value[step]
                mark = node.start_mark
            else:
                return None
        return (mark.line + 1, mark.column + 1)
