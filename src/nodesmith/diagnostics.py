"""Build-time diagnostics and exit statuses (SPEC-04 §4)."""
import json
from contextlib import contextmanager
from dataclasses import dataclass, replace

EXIT_NAMES = {0: "EXIT_SUCCESS", 1: "EXIT_GENERIC_ERROR", 2: "EXIT_CLI_USAGE_ERROR", 3: "EXIT_MANIFEST_INVALID",
              4: "EXIT_SEMANTIC_INVALID", 5: "EXIT_CONCURRENCY_INVALID", 6: "EXIT_SECURITY_INVALID", 8: "EXIT_FEATURE_INVALID"}
CATEGORY_EXIT = {"SYN": 3, "SEM": 4, "CNC": 5, "SEC": 6, "SHM": 8, "RT": 8, "SIM": 8}
WARNINGS = {"ERR_SEM_110", "ERR_SEM_113"}   # promoted to errors by --strict (SPEC-04 §4.1 rule 5)


def exit_status(code: str) -> int:
    return CATEGORY_EXIT[code.split("_")[1]]


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    severity: str = "error"
    path: tuple = ()               # where in the manifest document, e.g. ("pipelines", 2, "expressions", 0)
    file: str | None = None
    line: int | None = None
    column: int | None = None

    def render(self, source_lines=None) -> str:
        """Console form of SPEC-04 §4.4: headline, `-->` position, and the source line with a caret when it is known."""
        out = f"{self.severity}[{self.code}]: {self.message}"
        if self.line is None: return out
        out += f"\n  --> {self.file or '<manifest>'}:{self.line}:{self.column or 1}"
        if source_lines and 1 <= self.line <= len(source_lines):
            text = source_lines[self.line - 1].rstrip()
            col = min(self.column or 1, len(text) + 1)
            width = len(str(self.line))
            out += f"\n{' ' * (width + 1)}|\n{self.line} | {text}\n{' ' * (width + 1)}| {' ' * (col - 1)}{'^' * max(1, min(len(text) - col + 1, 60))}"
        return out


class BuildError(Exception):
    """One error found by a check. Stages catch it (see Report.guard) and carry on with the next independent check."""
    def __init__(self, code: str, message: str, path: tuple = (), line=None, column=None):
        super().__init__(f"{code}: {message}")
        self.diagnostic = Diagnostic(code, message, path=tuple(path), line=line, column=column)


class Cascade(Exception):
    """Abandons a check whose input already failed and was reported; nothing is added to the report."""


class Report:
    """Every diagnostic found, in emission order (SPEC-04 §3)."""
    def __init__(self): self.diagnostics: list[Diagnostic] = []

    @property
    def errors(self): return [d for d in self.diagnostics if d.severity == "error"]
    @property
    def warnings(self): return [d for d in self.diagnostics if d.severity == "warning"]

    def add(self, diagnostic: Diagnostic): self.diagnostics.append(diagnostic)
    def error(self, code, message, path=(), line=None, column=None):
        self.add(Diagnostic(code, message, path=tuple(path), line=line, column=column))

    def warn(self, code, message, path=()):
        assert code in WARNINGS, code
        self.add(Diagnostic(code, message, "warning", tuple(path)))

    @contextmanager
    def guard(self, path=()):
        """Runs one independent check; its first error is recorded and the caller continues with the next check.
        `path` is where in the manifest the check looks; an error that names no place of its own is reported there."""
        try: yield
        except BuildError as e:
            d = e.diagnostic
            self.add(replace(d, path=tuple(path)) if not d.path and d.line is None else d)
        except Cascade: pass

    def exit_code(self) -> int:
        return exit_status(self.errors[0].code) if self.errors else 0


def summary(report: Report) -> str:
    n, w = len(report.errors), len(report.warnings)
    plural = lambda k, s: f"{k} {s}" + ("" if k == 1 else "s")
    if not n: return f"{plural(w, 'warning')}" if w else ""
    code = report.exit_code()
    return f"aborting due to {plural(n, 'error')}" + (f" and {plural(w, 'warning')}" if w else "") + f" (exit status {code}, {EXIT_NAMES[code]})"


def report_json(report: Report) -> str:
    code = report.exit_code()
    def item(d):
        out = {"code": d.code, "severity": d.severity, "message": d.message}
        if d.line is not None: out.update(file=d.file or "<manifest>", line=d.line, column=d.column or 1)
        return out
    return json.dumps({"exit_code": code, "success": code == 0, "error_count": len(report.errors), "warning_count": len(report.warnings),
                       "diagnostics": [item(d) for d in report.diagnostics]}, indent=2)
