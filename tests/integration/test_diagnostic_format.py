"""`--format=json` output conforms to the schema printed in SPEC-04 §4.4 (read from the spec, so the two cannot drift)."""

import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from nodesmith.cli.main import main

ROOT = Path(__file__).resolve().parents[2]
SPEC = (ROOT / "docs/specs/SPEC-04-cli-toolchain.adoc").read_text()
BLOCK = re.search(r"\[source,json\]\n----\n(\{\n  \"\$schema\".*?)\n----", SPEC, re.S).group(1)
VALIDATOR = Draft202012Validator(json.loads(BLOCK))
CASES = [
    (ROOT / "examples/02_filter_pipeline.toml", []),
    (ROOT / "conformance/invalid/sem109_constant_division_by_zero.toml", []),
    (ROOT / "conformance/invalid/sem110_warn_unused_subscriber.toml", []),
    (ROOT / "conformance/invalid/sem110_warn_unused_subscriber.toml", ["--strict"]),
    (ROOT / "conformance/multi/sem_errors_in_three_pipelines.toml", []),
    (ROOT / "conformance/multi/schema_errors_all_reported.toml", []),
    (ROOT / "conformance/invalid/syn001_duplicate_key.toml", []),
]


@pytest.mark.parametrize(
    "manifest,flags", CASES, ids=lambda x: x.name if isinstance(x, Path) else "-".join(x) or "plain"
)
def test_json_report_matches_the_spec_schema(capsys, manifest, flags):
    code = main(["--format", "json", "validate", "--manifest", str(manifest), *flags])
    report = json.loads(capsys.readouterr().out)
    assert not list(VALIDATOR.iter_errors(report)), report
    assert report["exit_code"] == code and report["success"] == (code == 0)
    assert report["error_count"] == sum(d["severity"] == "error" for d in report["diagnostics"])
    assert report["warning_count"] == sum(d["severity"] == "warning" for d in report["diagnostics"])
