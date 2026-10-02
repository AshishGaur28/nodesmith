"""The public interface of nodesmith: what the command line, a UI or a script calls.

Everything that processes a manifest lives in the packages below this one (``manifest``,
``semantics``, ``ir``, ``generators``); this module wires them together and is the only
thing a client needs to import:

* ``compile_manifest`` reads and checks a manifest and lowers it to the IR (SPEC-04 section
  3): ingest -> schema -> cross-block rules -> semantic checks and lowering. Each stage
  reports every error it can find; a stage that finds an error in the document itself
  stops the later ones. It returns a ``Compiled`` result.
* ``write_ir`` and ``generate`` take a ``Compiled`` result and write files.
  They raise ``GenerationError`` for what cannot be done, and print nothing.
"""

from dataclasses import dataclass, replace
from pathlib import Path

from .diagnostics import Diagnostic, Report
from .generators import cpp
from .ir.canonical import canonical_json, ir_hash
from .manifest import ingest, rules, schema
from .manifest.locate import Locator
from .semantics.lower import lower


class GenerationError(Exception):
    """Something that cannot be generated (a target that is not implemented, a feature the
    generator does not support yet, a directory that must not be overwritten)."""


@dataclass
class Compiled:
    """The result of ``compile_manifest``."""

    ir: dict | None  # None unless there were no errors
    report: Report
    source_lines: list[str] | None = None  # for printing the offending line
    # Build choices. They are not part of the IR (SPEC-00 section 4), so they travel beside it.
    target_language: str | None = None
    rmw: str | None = None
    manifest_path: str | None = None  # where the manifest was read from

    @property
    def ok(self) -> bool:
        """True when no error was found (warnings do not count)."""
        return not self.report.errors

    @property
    def ir_hash(self) -> str:
        """Hash of the IR (only meaningful when ``ok``)."""
        return ir_hash(self.ir)


def _attach_positions(report: Report, path: Path, text: str) -> None:
    """Give each diagnostic a file, line and column, found from its manifest path."""
    locator = Locator(path.suffix, text)
    positioned = []
    for diagnostic in report.diagnostics:
        if diagnostic.line is None and diagnostic.path:
            hit = locator.find(diagnostic.path)
            if hit:
                diagnostic = replace(diagnostic, line=hit[0], column=hit[1])
        if diagnostic.line is not None:
            diagnostic = replace(diagnostic, file=str(path))
        positioned.append(diagnostic)
    # Findings about the document itself are listed in document order.
    if positioned and positioned[0].code.startswith("SYN"):
        positioned.sort(key=lambda d: (d.line is None, d.line or 0, d.column or 0))
    report.diagnostics = positioned


def _apply_build_choices(manifest: dict, target_language: str | None, rmw: str | None) -> tuple:
    """Let the command line override ``node.target_language`` and ``node.rmw``; return the
    effective pair. The overrides apply before any rule runs, so they are checked like the
    values in the file."""
    node = manifest["node"]
    if target_language:
        node["target_language"] = target_language
    if rmw:
        node["rmw"] = rmw
    return node["target_language"], node.get("rmw", "fastrtps")


def _as_errors(report: Report) -> None:
    """``--strict``: report every warning as an error (SPEC-04 section 4.1, rule 5)."""
    report.diagnostics = [
        Diagnostic(d.code, d.message, path=d.path, line=d.line, column=d.column)
        for d in report.diagnostics
    ]


def compile_manifest(
    path: str | Path,
    mode: str = "debug",
    strict: bool = False,
    target_language: str | None = None,
    rmw: str | None = None,
) -> Compiled:
    """Check a manifest file and lower it to the IR.

    Findings in the document itself (bad syntax, schema violations) are all reported and
    stop the run. Otherwise every cross-block and semantic error is reported too, in a fixed
    order, and ``report.exit_code()`` is the status of the first. ``ir`` is ``None`` unless
    there were no errors. Diagnostics carry file, line and column where known.
    """
    path = Path(path)
    report = Report()
    manifest = ingest.load_manifest(path, report)
    # A syntax error means the document cannot be trusted: skip the schema check.
    if manifest is not None and not report.errors:
        schema.check(manifest, report)

    ir = None
    build_choices = (None, None)
    if not report.errors:
        manifest = schema.strip_x(manifest)
        build_choices = _apply_build_choices(manifest, target_language, rmw)
        extensions = schema.effective_extensions(manifest)
        rules.check(manifest, extensions, mode, report)
        ir = lower(manifest, extensions, report)
        if report.errors:
            ir = None

    if strict and report.warnings:
        _as_errors(report)
        ir = None

    text = path.read_text(encoding="utf-8")
    _attach_positions(report, path, text)
    return Compiled(ir, report, text.splitlines(), *build_choices, str(path))


def write_ir(compiled: Compiled, output: str | Path) -> None:
    """Write the IR as canonical JSON."""
    Path(output).write_text(canonical_json(compiled.ir), encoding="utf-8")


def _require_cpp(compiled: Compiled, what: str) -> None:
    if compiled.target_language != "cpp":
        raise GenerationError(
            f"{what} for target {compiled.target_language} is not implemented yet"
        )


def generate(
    compiled: Compiled, output_dir: str | Path, logic_dir: str | Path | None = None
) -> list[str]:
    """Write the source package of the manifest's target language into ``output_dir``.

    If the manifest declares functions, the user's files in ``logic_dir`` (default: ``logic/`` next
    to the manifest) are copied into the package. When that folder does not exist yet, a starter
    ``logic.cpp`` is written into it once; an existing folder is never modified. Returns the
    declared functions that do not seem to be implemented in it (a hint, see ``unimplemented``)."""
    _require_cpp(compiled, "generating")
    logic_files = None
    if cpp.has_user_logic(compiled.ir):
        folder = Path(logic_dir) if logic_dir else Path(compiled.manifest_path).parent / "logic"
        if not folder.exists():
            folder.mkdir(parents=True)
            for name, text in cpp.starter_files(compiled.ir).items():
                (folder / name).write_text(text, encoding="utf-8")
        logic_files = cpp.read_logic_dir(folder)
    try:
        cpp.write_package(
            cpp.generate_package(compiled.ir, compiled.rmw, logic_files=logic_files), output_dir
        )
    except cpp.Unsupported as unsupported:
        raise GenerationError(str(unsupported)) from unsupported
    return cpp.unimplemented(compiled.ir, logic_files) if logic_files is not None else []
