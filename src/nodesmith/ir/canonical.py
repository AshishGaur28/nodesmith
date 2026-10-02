"""Canonical form and hash of the IR (SPEC-00 section 5).

Two IRs that mean the same thing serialise to the same bytes, so their hash is the same:
keys sorted, no insignificant whitespace, UTF-8. The hash identifies a lowered manifest and
appears in the header of every generated file.
"""

import hashlib
import json


def canonical_json(ir: dict) -> str:
    """The IR as canonical JSON text."""
    return json.dumps(ir, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def ir_hash(ir: dict) -> str:
    """SHA-256 (hex) of the IR's canonical JSON."""
    return hashlib.sha256(canonical_json(ir).encode("utf-8")).hexdigest()
