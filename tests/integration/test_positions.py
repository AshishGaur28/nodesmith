"""Diagnostics point at the place in the manifest that caused them."""

from pathlib import Path

import pytest

from nodesmith.api import compile_manifest

CORPUS = Path(__file__).resolve().parents[2] / "conformance"
INVALID = sorted(p for p in (CORPUS / "invalid").iterdir() if p.is_file())


def first(path, mode="debug"):
    r = compile_manifest(path, mode)
    d = (r.report.errors or r.report.warnings)[0]
    return d, r.source_lines


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.name)
def test_every_corpus_diagnostic_has_a_position_inside_the_file(path):
    mode = "release" if "release" in path.name else "debug"
    r = compile_manifest(path, mode)
    for d in r.report.diagnostics:
        assert d.line is not None and 1 <= d.line <= len(r.source_lines), d
        assert d.file == str(path)


@pytest.mark.parametrize(
    "name,snippet",
    [
        ("invalid/sem101_unknown_parameter.toml", "param.nope"),
        ("invalid/sem104_state_assigned_twice.toml", "state.x = 2.0"),
        ("invalid/sem106_default_above_max.toml", "default = 5.0"),
        ("invalid/syn001_hex_number.toml", "0x10"),
        ("invalid/syn002_unknown_key.toml", "colour"),
        ("invalid/sim002_sim_time_with_steady_timer.toml", "[pipelines.trigger]"),
        ("multi/failed_let_does_not_cascade.toml", "param.nope"),
    ],
)
def test_the_reported_line_shows_the_culprit(name, snippet):
    d, lines = first(CORPUS / name)
    assert snippet in lines[d.line - 1], (d, lines[d.line - 1])


def test_yaml_and_json_positions(tmp_path):
    y = tmp_path / "m.yaml"
    y.write_text(
        'manifest_version: "1"\nnode:\n  name: t\n  namespace: /\n  target_language: cpp\n  colour: red\n'
    )
    d, lines = first(y)
    assert d.line == 6 and "colour" in lines[5]
    j = tmp_path / "m.json"
    j.write_text(
        '{\n "manifest_version": "1",\n "node": {"name": "t", "namespace": "/", "target_language": "rust"}\n}\n'
    )
    d, lines = first(j)
    assert d.code == "ERR_SYN_002" and d.line == 3


def test_syntax_error_position(tmp_path):
    t = tmp_path / "m.toml"
    t.write_text('[node]\nname = "t"\nnamespace = \n')
    d, _ = first(t)
    assert d.code == "ERR_SYN_001" and d.line == 3


def test_rendering_shows_source_line_and_caret():
    d, lines = first(CORPUS / "invalid/sem109_constant_division_by_zero.toml")
    text = d.render(lines)
    assert "--> " in text and "1 / 0" in text and "^" in text
