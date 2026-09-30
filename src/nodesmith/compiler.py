"""Runs the front-end stages in order and reports every error it can find (SPEC-04 §3)."""
from dataclasses import dataclass

from . import ingest, rules, schema
from .diagnostics import Diagnostic, Report
from .lower import lower


@dataclass
class Compiled:
    ir: dict | None
    report: Report

    @property
    def ok(self): return not self.report.errors


def compile_manifest(path, mode="debug", strict=False) -> Compiled:
    """Gates 1-3 of SPEC-04 §3 for a manifest file.

    Gate 1 stops the run (all its errors are reported, but later gates would work on untrusted input; a syntax error in
    the document also skips the schema check). Gates 2 and 3 report every
    error in emission order; `report.exit_code()` is the status of the first. With `strict`, every warning becomes an error
    (SPEC-04 §4.1 rule 5). `ir` is None unless there were no errors.
    """
    report = Report()
    manifest = ingest.load_manifest(path, report)
    if manifest is not None and not report.errors:     # an ERR_SYN_001 means the document cannot be trusted, so no schema check
        schema.check(manifest, report)
    ir = None
    if not report.errors:
        manifest = schema.strip_x(manifest)
        ext = schema.effective_extensions(manifest)
        rules.check(manifest, ext, mode, report)
        ir = lower(manifest, ext, report)
        if report.errors: ir = None
    if strict and report.warnings:
        report.diagnostics = [Diagnostic(d.code, d.message) for d in report.diagnostics]
        ir = None
    return Compiled(ir, report)
