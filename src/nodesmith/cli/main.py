"""The ``nodesmith`` command line (SPEC-04 section 2): arguments in, ``nodesmith.api`` called,
results printed. It holds no processing of its own.

Commands: ``validate`` (check a manifest), ``lower`` (also write the IR) and ``generate`` (also
write a source package, with the user's functions from their ``logic/`` folder).
``bench`` and ``clean`` are accepted but not implemented yet. Every command that reads a manifest
reports every error it finds, and the exit status is that of the first (SPEC-04 section 4.2).
"""

import argparse
import sys

from .. import __version__, api
from ..diagnostics import report_json, summary

EXIT_GENERIC_ERROR = 1  # I/O failure, or something that is not implemented yet
COMMANDS_WITH_A_MANIFEST = ("validate", "lower", "generate")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nodesmith", description="Turns a node manifest into a ROS 2 package."
    )
    parser.add_argument("-V", "--version", action="version", version=f"nodesmith {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    commands = parser.add_subparsers(dest="command", required=True)

    def manifest_command(name: str, help_text: str, *extra: str) -> argparse.ArgumentParser:
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--manifest", required=True)
        command.add_argument("--strict", action="store_true", help="treat warnings as errors")
        for option in extra:
            command.add_argument(option, required=True)
        return command

    manifest_command("validate", "check a manifest")
    manifest_command("lower", "check a manifest and write its IR", "--output")
    generate = manifest_command(
        "generate", "check a manifest and write a source package", "--output-dir"
    )
    generate.add_argument("--target-language", choices=("cpp", "python"))
    generate.add_argument("--rmw", choices=("fastrtps",))
    generate.add_argument(
        "--logic-dir", help="the folder with your functions (default: logic/ next to the manifest)"
    )

    for name in ("bench", "clean"):
        commands.add_parser(name, help="not implemented yet").add_argument("--manifest")
    return parser


def _print_report(arguments: argparse.Namespace, result: api.Compiled) -> None:
    """Print every diagnostic, as JSON on stdout or as text on stderr."""
    if arguments.format == "json":
        print(report_json(result.report))
        return
    for diagnostic in result.report.diagnostics:
        print(diagnostic.render(result.source_lines), file=sys.stderr)
    last_line = summary(result.report)
    if last_line:
        print(last_line, file=sys.stderr)


def _run(arguments: argparse.Namespace, result: api.Compiled) -> None:
    """Do what the command asks with a manifest that checked clean."""
    if arguments.command == "lower":
        api.write_ir(result, arguments.output)
    elif arguments.command == "generate":
        for name in api.generate(result, arguments.output_dir, arguments.logic_dir):
            print(
                f"note: no implementation of {name}() found in your logic folder", file=sys.stderr
            )


def main(argv: list[str] | None = None) -> int:
    """Run the command line and return the process exit status."""
    arguments = _build_parser().parse_args(argv)
    if arguments.command not in COMMANDS_WITH_A_MANIFEST:
        print(f"nodesmith {arguments.command}: not implemented in this revision", file=sys.stderr)
        return EXIT_GENERIC_ERROR
    try:
        result = api.compile_manifest(
            arguments.manifest,
            arguments.mode,
            arguments.strict,
            getattr(arguments, "target_language", None),
            getattr(arguments, "rmw", None),
        )
        _print_report(arguments, result)
        if not result.ok:
            return result.report.exit_code()
        _run(arguments, result)
    except api.GenerationError as error:
        print(f"nodesmith {arguments.command}: {error}", file=sys.stderr)
        return EXIT_GENERIC_ERROR
    except OSError as error:  # a missing manifest, an unwritable output
        print(f"nodesmith: {error}", file=sys.stderr)
        return EXIT_GENERIC_ERROR
    if not arguments.quiet and arguments.format == "text":
        print(f"ok: {arguments.manifest} (IR hash {result.ir_hash})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
