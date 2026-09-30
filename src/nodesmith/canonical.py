"""Canonical IR serialization and hash (SPEC-00 §5)."""
import hashlib
import json


def canonical_json(ir: dict) -> str:
    return json.dumps(ir, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def ir_hash(ir: dict) -> str:
    return hashlib.sha256(canonical_json(ir).encode("utf-8")).hexdigest()
