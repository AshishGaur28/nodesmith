"""The code must run on Python 3.11 (pyproject: requires-python), but is usually developed on a newer one. Python 3.12 (PEP 701) allows
f-strings that 3.11 rejects: a quote of the enclosing kind or a backslash inside a replacement field. This finds them."""
import io
import sys
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FILES = sorted([*(ROOT / "src").rglob("*.py"), *(ROOT / "tests").rglob("*.py"), *(ROOT / "tools").rglob("*.py"), *(ROOT / "conformance").rglob("*.py")])


def pep701_uses(path):
    found, stack = [], []          # stack of (quote kind, depth of replacement-field braces) for the f-strings being read
    with open(path, "rb") as f:
        for tok in tokenize.tokenize(f.readline):
            name = tokenize.tok_name[tok.type]
            if name == "FSTRING_START":
                if stack and tok.string[-1] == stack[-1][0] and stack[-1][1] > 0: found.append((tok.start[0], "same quote nested in an f-string"))
                stack.append([tok.string[-1], 0])
            elif name == "FSTRING_END": stack.pop()
            elif stack:
                if name == "OP" and tok.string == "{": stack[-1][1] += 1
                elif name == "OP" and tok.string == "}": stack[-1][1] -= 1
                elif name == "STRING" and stack[-1][1] > 0 and tok.string[-1] == stack[-1][0] and not tok.string.startswith(("'''", '"""')):
                    found.append((tok.start[0], "same quote nested in an f-string"))
                if stack and stack[-1][1] > 0 and "\\" in tok.string and name in ("STRING", "OP"): found.append((tok.start[0], "backslash in a replacement field"))
    return found


@pytest.mark.skipif(sys.version_info < (3, 12), reason="only newer tokenizers expose f-string structure")
@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_syntax_that_python_3_11_rejects(path):
    assert pep701_uses(path) == []
