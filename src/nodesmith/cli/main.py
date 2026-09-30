"""nodesmith command line (SPEC-04 §2). This revision implements `validate` and `lower`."""
import argparse
import sys
from pathlib import Path

from .. import __version__
from ..canonical import canonical_json, ir_hash
from ..compiler import compile_manifest
from ..diagnostics import report_json, summary


def _parser():
    p = argparse.ArgumentParser(prog="nodesmith", description="Turns a node manifest into a ROS 2 package.")
    p.add_argument("-V", "--version", action="version", version=f"nodesmith {__version__}")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.add_argument("--mode", choices=("debug", "release"), default="debug")
    sub = p.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate", help="Gates 1-3: schema, semantics, concurrency")
    v.add_argument("--manifest", required=True); v.add_argument("--strict", action="store_true")
    l = sub.add_parser("lower", help="Gates 1-3, then write the IR")
    l.add_argument("--manifest", required=True); l.add_argument("--output", required=True); l.add_argument("--strict", action="store_true")
    for name in ("generate", "bench", "clean"): sub.add_parser(name, help="not implemented yet").add_argument("--manifest")
    return p


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.command not in ("validate", "lower"):
        print(f"nodesmith {args.command}: not implemented in this revision", file=sys.stderr)
        return 1
    try: result = compile_manifest(args.manifest, args.mode, args.strict)
    except OSError as e:
        print(f"nodesmith: {e}", file=sys.stderr)
        return 1
    report, code = result.report, result.report.exit_code()
    if args.format == "json": print(report_json(report))
    else:
        for d in report.diagnostics: print(d.render(), file=sys.stderr)
        if summary(report): print(summary(report), file=sys.stderr)
    if not result.ok: return code
    if args.command == "lower":
        try: Path(args.output).write_text(canonical_json(result.ir), encoding="utf-8")
        except OSError as e:
            print(f"nodesmith: {e}", file=sys.stderr)
            return 1
    if not args.quiet and args.format == "text":
        print(f"ok: {args.manifest} (IR hash {ir_hash(result.ir)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
