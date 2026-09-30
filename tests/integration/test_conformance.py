"""The product front end must agree with the conformance corpus (conformance/README.md)."""
import json
import re
from pathlib import Path

import pytest

from nodesmith.canonical import canonical_json, ir_hash
from nodesmith.compiler import compile_manifest
from nodesmith.schema import ir_schema

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "conformance"
VALID = sorted((ROOT / "examples").glob("*.toml"))
INVALID = sorted(p for p in (CORPUS / "invalid").iterdir() if p.is_file())


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.stem)
def test_valid_manifest_lowers_to_expected_ir(path):
    result = compile_manifest(path)
    assert result.ok, result.report.diagnostics
    expected = json.loads((CORPUS / "expected" / f"{path.stem}.ir.json").read_text())
    assert json.loads(canonical_json(result.ir)) == expected
    assert ir_hash(result.ir) == (CORPUS / "expected" / f"{path.stem}.sha256").read_text().strip()


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.stem)
def test_ir_validates_against_ir_schema(path):
    from jsonschema import Draft202012Validator
    assert not list(Draft202012Validator(ir_schema()).iter_errors(compile_manifest(path).ir))


@pytest.mark.parametrize("path", sorted((CORPUS / "parity").iterdir()), ids=lambda p: p.name)
def test_other_formats_give_the_same_hash(path):
    same = ir_hash(compile_manifest(ROOT / "examples" / f"{path.stem}.toml").ir)
    assert ir_hash(compile_manifest(path).ir) == same


def _expectation(path):
    m = re.match(r"([a-z]+)(\d{3})_(.*)\.\w+$", path.name)
    return f"ERR_{m.group(1).upper()}_{m.group(2)}", "warn_" in m.group(3), "release" in m.group(3)


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.name)
def test_invalid_manifest_is_rejected_with_its_code(path):
    code, is_warning, release = _expectation(path)
    mode = "release" if release else "debug"
    result = compile_manifest(path, mode)
    if is_warning:
        assert result.ok and code in [w.code for w in result.report.warnings]
        strict = compile_manifest(path, mode, strict=True)          # --strict promotes it to an error
        assert not strict.ok and strict.report.errors[0].code == code
    else:
        assert not result.ok and result.ir is None
        assert [d.code for d in result.report.errors] == [code], result.report.diagnostics   # one scenario, one error: no cascades


MULTI = json.loads((CORPUS / "multi" / "expected.json").read_text())


@pytest.mark.parametrize("name", sorted(MULTI), ids=str)
def test_every_error_is_reported_in_order(name):
    result = compile_manifest(CORPUS / "multi" / name)
    assert [d.code for d in result.report.errors] == MULTI[name]
