"""Diagnostics: what the tool reports, how it is collected, and how it is printed.

A *diagnostic* is one finding (an error or a warning) with a stable code such as
``ERR_SEM_104`` (the registry is in SPEC-04 section 4). The checks do not stop at the
first problem: each independent check runs inside ``Report.guard`` so that every error
in a manifest is found in one pass (SPEC-04 section 3).
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace

EXIT_NAMES = {
    0: "EXIT_SUCCESS",
    1: "EXIT_GENERIC_ERROR",
    2: "EXIT_CLI_USAGE_ERROR",
    3: "EXIT_MANIFEST_INVALID",
    4: "EXIT_SEMANTIC_INVALID",
    5: "EXIT_CONCURRENCY_INVALID",
    6: "EXIT_SECURITY_INVALID",
    8: "EXIT_FEATURE_INVALID",
}

# Process exit status by the category letters of a code (ERR_<CATEGORY>_<NUMBER>).
CATEGORY_EXIT = {"SYN": 3, "SEM": 4, "CNC": 5, "SEC": 6, "SHM": 8, "RT": 8, "SIM": 8}

# Codes that are only warnings; ``--strict`` turns them into errors (SPEC-04 section 4.1).
WARNING_CODES = {"ERR_SEM_110", "ERR_SEM_113"}

SOURCE_MARKER_WIDTH = 60  # longest run of ``^`` drawn under a source line


def exit_status(code: str) -> int:
    """Process exit status for a diagnostic code, taken from its category letters."""
    category = code.split("_")[1]
    return CATEGORY_EXIT[category]


@dataclass(frozen=True)
class Diagnostic:
    """One finding. ``path`` says where in the manifest it was found, for example
    ``("pipelines", 2, "expressions", 0)``; ``line`` and ``column`` are filled in later
    from that path (or directly, when a syntax error already knows its position)."""

    code: str
    message: str
    severity: str = "error"
    path: tuple = ()
    file: str | None = None
    line: int | None = None
    column: int | None = None

    def render(self, source_lines: list[str] | None = None) -> str:
        """Text form of SPEC-04 section 4.4: a headline, the position, and the source line
        with a caret under it when the position is known."""
        text = f"{self.severity}[{self.code}]: {self.message}"
        if self.line is None:
            return text
        column = self.column or 1
        text += f"\n  --> {self.file or '<manifest>'}:{self.line}:{column}"
        if source_lines and 1 <= self.line <= len(source_lines):
            text += "\n" + self._source_excerpt(source_lines[self.line - 1].rstrip(), column)
        return text

    def _source_excerpt(self, source: str, column: int) -> str:
        """The offending line, with a gutter, and a caret run beneath it."""
        column = min(column, len(source) + 1)
        gutter = " " * (len(str(self.line)) + 1)
        marker = "^" * max(1, min(len(source) - column + 1, SOURCE_MARKER_WIDTH))
        return f"{gutter}|\n{self.line} | {source}\n{gutter}| {' ' * (column - 1)}{marker}"


class BuildError(Exception):
    """One error found by a check. It carries its diagnostic; ``Report.guard`` records it
    and lets the caller carry on with the next independent check."""

    def __init__(
        self,
        code: str,
        message: str,
        path: tuple = (),
        line: int | None = None,
        column: int | None = None,
    ):
        super().__init__(f"{code}: {message}")
        self.diagnostic = Diagnostic(code, message, path=tuple(path), line=line, column=column)


class Cascade(Exception):
    """Abandons a check whose input already failed and was already reported.

    Raised instead of a second, misleading error (for example when an expression uses a
    local variable whose definition had an error). ``Report.guard`` drops it silently."""


class Report:
    """Every diagnostic found in one run, in the order they were found (SPEC-04 section 3)."""

    def __init__(self) -> None:
        self.diagnostics: list[Diagnostic] = []

    @property
    def errors(self) -> list[Diagnostic]:
        """The diagnostics with severity ``error``."""
        return [d for d in self.diagnostics if d.severity == "error"]

    @property
    def warnings(self) -> list[Diagnostic]:
        """The diagnostics with severity ``warning``."""
        return [d for d in self.diagnostics if d.severity == "warning"]

    def add(self, diagnostic: Diagnostic) -> None:
        """Record a ready-made diagnostic."""
        self.diagnostics.append(diagnostic)

    def error(self, code, message, path=(), line=None, column=None) -> None:
        """Record an error."""
        self.add(Diagnostic(code, message, path=tuple(path), line=line, column=column))

    def warn(self, code, message, path=()) -> None:
        """Record a warning; ``code`` must be one of ``WARNING_CODES``."""
        assert code in WARNING_CODES, code
        self.add(Diagnostic(code, message, "warning", tuple(path)))

    @contextmanager
    def guard(self, path: tuple = ()) -> Iterator[None]:
        """Run one independent check. If it fails, record its error and carry on.

        ``path`` is where in the manifest the check looks; an error that does not name a
        place of its own is reported there."""
        try:
            yield
        except BuildError as failure:
            found = failure.diagnostic
            has_place = bool(found.path) or found.line is not None
            self.add(found if has_place else replace(found, path=tuple(path)))
        except Cascade:
            pass

    def exit_code(self) -> int:
        """Exit status of the run: that of the first error, or 0 when there is none."""
        return exit_status(self.errors[0].code) if self.errors else 0


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def summary(report: Report) -> str:
    """The last line of the text output, or an empty string when there is nothing to say."""
    errors, warnings = len(report.errors), len(report.warnings)
    if not errors:
        return _plural(warnings, "warning") if warnings else ""
    status = report.exit_code()
    line = f"aborting due to {_plural(errors, 'error')}"
    if warnings:
        line += f" and {_plural(warnings, 'warning')}"
    return f"{line} (exit status {status}, {EXIT_NAMES[status]})"


def report_json(report: Report) -> str:
    """The report in the JSON format of SPEC-04 section 4.4."""

    def as_dict(diagnostic: Diagnostic) -> dict:
        item = {
            "code": diagnostic.code,
            "severity": diagnostic.severity,
            "message": diagnostic.message,
        }
        if diagnostic.line is not None:
            item.update(
                file=diagnostic.file or "<manifest>",
                line=diagnostic.line,
                column=diagnostic.column or 1,
            )
        return item

    status = report.exit_code()
    document = {
        "exit_code": status,
        "success": status == 0,
        "error_count": len(report.errors),
        "warning_count": len(report.warnings),
        "diagnostics": [as_dict(d) for d in report.diagnostics],
    }
    return json.dumps(document, indent=2)
