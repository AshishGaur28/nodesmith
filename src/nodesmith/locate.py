"""Maps a path in the manifest document (("pipelines", 2, "expressions", 0)) to a line and column in the source file.

YAML and JSON use the YAML parser's node marks (JSON is read as YAML for this). TOML has no position information in `tomllib`,
so a small scanner records where tables, keys and array elements start. A path that is not found resolves to its closest
existing prefix, so a missing key points at its table.
"""
import re

import yaml

_STRING = re.compile(r'"(?:[^"\\]|\\.)*"|\'[^\']*\'')
_KEYPART = re.compile(r'\s*(?:"((?:[^"\\]|\\.)*)"|\'([^\']*)\'|([A-Za-z0-9_-]+))\s*')


def _split_key(text):
    """'a."b/c".d' -> (['a', 'b/c', 'd'], rest_of_text_after_the_key)."""
    parts, i = [], 0
    while True:
        m = _KEYPART.match(text, i)
        if not m: return None, text
        parts.append(next(g for g in m.groups() if g is not None))
        i = m.end()
        if i < len(text) and text[i] == ".": i += 1; continue
        return parts, text[i:]


_TOKEN = re.compile(_STRING.pattern + r'|[\[\]]|#.*')


def _toml_positions(text):
    pos, arrays, table = {}, {}, ()

    def resolve(parts):
        out = ()
        for p in parts:
            out += (p,)
            if out in arrays: out += (arrays[out] - 1,)
        return out

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]; line = raw.strip(); ln = i + 1
        if line.startswith("[["):
            parts, _ = _split_key(line[2:].rstrip("] "))
            if parts:
                base = resolve(parts[:-1]) + (parts[-1],)
                arrays[base] = arrays.get(base, 0) + 1
                table = base + (arrays[base] - 1,); pos[table] = (ln, raw.index("[") + 1)
        elif line.startswith("["):
            parts, _ = _split_key(line[1:].rstrip("] "))
            if parts: table = resolve(parts); pos[table] = (ln, raw.index("[") + 1)
        elif line and not line.startswith("#"):
            parts, rest = _split_key(line)
            if parts and rest.lstrip().startswith("="):
                key = table + tuple(parts); pos[key] = (ln, len(raw) - len(raw.lstrip()) + 1)
                value = rest.lstrip()[1:]
                if value.lstrip().startswith("["):         # array: record each string element (possibly over several lines)
                    depth, idx, j = 0, 0, i
                    while j < len(lines):
                        offset = raw.index("=") + 1 if j == i else 0
                        seg = lines[j][offset:]
                        for m in _TOKEN.finditer(seg):
                            t = m.group(0)
                            if t == "[": depth += 1
                            elif t == "]": depth -= 1
                            elif t[0] in "\"'" and depth == 1: pos[key + (idx,)] = (j + 1, offset + m.start() + 1); idx += 1
                        if depth <= 0: break
                        j += 1
                    i = j
        i += 1
    for path, where in list(pos.items()):           # a table that has no header of its own (`[a.b.c]` implies a and a.b) starts where its first child does
        for n in range(1, len(path)): pos.setdefault(path[:n], where)
    return pos


class Locator:
    def __init__(self, suffix: str, text: str):
        self._toml = suffix.lower() == ".toml"
        self._pos = _toml_positions(text) if self._toml else None
        self._root = None
        if not self._toml:
            try: self._root = yaml.compose(text)
            except yaml.YAMLError: self._root = None

    def find(self, path):
        path = tuple(path)
        while True:
            hit = self._lookup(path)
            if hit or not path: return hit
            path = path[:-1]

    def _lookup(self, path):
        if self._toml: return self._pos.get(path)
        node = self._root
        if node is None: return None
        mark = node.start_mark
        for p in path:
            if isinstance(node, yaml.MappingNode):
                hit = next(((k, v) for k, v in node.value if getattr(k, "value", None) == p), None)
                if not hit: return None
                mark, node = hit[0].start_mark, hit[1]
            elif isinstance(node, yaml.SequenceNode) and isinstance(p, int) and p < len(node.value):
                node = node.value[p]; mark = node.start_mark
            else: return None
        return (mark.line + 1, mark.column + 1)
