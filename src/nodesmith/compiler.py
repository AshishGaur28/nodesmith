"""Runs the front-end stages in order and reports every error it can find (SPEC-04 §3)."""
from dataclasses import dataclass, replace
from pathlib import Path

from . import ingest, rules, schema
from .diagnostics import Diagnostic, Report
from .locate import Locator
from .lower import lower


@dataclass
class Compiled:
    ir: dict | None
    report: Report
    source_lines: list | None = None
    target_language: str | None = None     # build choices (not part of the IR, SPEC-00 §4)
    rmw: str | None = None

    @property
    def ok(self): return not self.report.errors


def _position(report, path, text):
    """Fills in file, line and column from each diagnostic's manifest path."""
    locator = Locator(path.suffix, text)
    out = []
    for d in report.diagnostics:
        if d.line is None and d.path:
            hit = locator.find(d.path)
            if hit: d = replace(d, line=hit[0], column=hit[1])
        out.append(replace(d, file=str(path)) if d.line is not None else d)
    if out and out[0].code.startswith("SYN"):      # Gate 1 findings come in document order
        out.sort(key=lambda d: (d.line is None, d.line or 0, d.column or 0))
    report.diagnostics = out


def compile_manifest(path, mode="debug", strict=False, target_language=None, rmw=None) -> Compiled:
    """Gates 1-3 of SPEC-04 §3 for a manifest file.

    Gate 1 stops the run (all its errors are reported, but later gates would work on untrusted input; a syntax error in
    the document also skips the schema check). Gates 2 and 3 report every error in emission order; `report.exit_code()` is
    the status of the first. With `strict`, every warning becomes an error (SPEC-04 §4.1 rule 5). `ir` is None unless there
    were no errors. Diagnostics carry file, line and column where the manifest position is known.
    """
    path = Path(path)
    report = Report()
    manifest = ingest.load_manifest(path, report)
    if manifest is not None and not report.errors:     # an ERR_SYN_001 means the document cannot be trusted, so no schema check
        schema.check(manifest, report)
    ir = None
    settings = (None, None)
    if not report.errors:
        manifest = schema.strip_x(manifest)
        if target_language: manifest["node"]["target_language"] = target_language     # --target-language and --rmw override the manifest
        if rmw: manifest["node"]["rmw"] = rmw
        settings = (manifest["node"]["target_language"], manifest["node"].get("rmw", "fastrtps"))
        ext = schema.effective_extensions(manifest)
        rules.check(manifest, ext, mode, report)
        ir = lower(manifest, ext, report)
        if report.errors: ir = None
    if strict and report.warnings:
        report.diagnostics = [Diagnostic(d.code, d.message, path=d.path, line=d.line, column=d.column) for d in report.diagnostics]
        ir = None
    text = path.read_text(encoding="utf-8")
    _position(report, path, text)
    return Compiled(ir, report, text.splitlines(), *settings)
