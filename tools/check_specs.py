#!/usr/bin/env python3
"""Checks that the specifications, the schemas and the examples agree with one another.

Run from anywhere; exits non-zero if anything is inconsistent. It checks that:

* the specs are numbered contiguously and ``INDEX.adoc`` lists exactly them, with their
  status and version;
* every ``SPEC-nn`` mention and every ``SPEC-nn section x`` reference points at something
  that exists;
* every ``ERR_*`` code a spec uses is in the registry of SPEC-04, and every registered code is
  used by the spec the registry names;
* the schemas are valid, the examples and every manifest embedded in a spec validate;
* each extension block is owned by the spec that the schema, SPEC-01 and the index say.
"""

import contextlib
import glob
import json
import os
import re
import sys
import tomllib
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SPECS = ROOT / "docs" / "specs"
CODE = r"ERR_[A-Z]{2,3}_[0-9]{3}"
EXTENSION_OWNERS = {  # block: (schema definition, owning spec number)
    "lifecycle": ("LifecycleExtension", "05"),
    "shared_memory": ("SharedMemoryExtension", "06"),
    "security": ("SecurityExtension", "08"),
    "telemetry": ("TelemetryExtension", "09"),
    "realtime": ("RealtimeExtension", "10"),
    "simulation": ("SimulationExtension", "11"),
    "concurrency": ("ConcurrencyExtension", "12"),
}

problems: list[str] = []


def report(*message) -> None:
    """Record one inconsistency."""
    problems.append(" ".join(str(part) for part in message))


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def check_numbering(spec_files: list[Path]) -> list[str]:
    """The specs are SPEC-00 ... SPEC-nn with no gaps. Returns their numbers."""
    numbers = [re.match(r"SPEC-(\d\d)", f.name)[1] for f in spec_files]
    if numbers != [f"{i:02d}" for i in range(len(numbers))]:
        report("spec numbers are not contiguous:", numbers)
    return numbers


def mentioning_files() -> dict[Path, str]:
    """The text of every file that may mention a spec."""
    files = {}
    patterns = ["docs/specs/**/*", "schemas/*", "examples/*", "README.md"]
    for pattern in patterns:
        for path in ROOT.glob(pattern):
            if path.is_file() and path.suffix != ".pyc":
                with contextlib.suppress(UnicodeDecodeError):
                    files[path] = read(path)
    return files


def check_references(spec_files: list[Path], numbers: list[str]) -> None:
    """Every ``SPEC-nn`` exists, and every ``SPEC-nn section x`` names a real heading."""
    headings = {
        re.match(r"SPEC-(\d\d)", f.name)[1]: set(
            re.findall(r"^=+ (\d+(?:\.\d+)*)\.? ", read(f), re.MULTILINE)
        )
        for f in spec_files
    }
    for path, text in mentioning_files().items():
        for match in re.finditer(r"SPEC-(\d\d)", text):
            if match[1] not in numbers:
                report("unknown spec id", path.name, match[0])
        for match in re.finditer(r"`SPEC-(\d\d)`\s*(?:section |§)(\d+(?:\.\d+)*)", text):
            if match[2] not in headings.get(match[1], set()):
                report("reference to a missing section", path.name, match[0])
    for spec in spec_files:
        for include in re.findall(r"^include::(\S+?)\[", read(spec), re.MULTILINE):
            if not (spec.parent / include).exists():
                report("missing include", spec.name, include)


def check_index(spec_files: list[Path], numbers: list[str]) -> None:
    """INDEX.adoc has one row per spec, and each row's status and version match the spec."""
    index = read(SPECS / "INDEX.adoc")
    rows = list(
        re.finditer(r"\| `(SPEC-\d\d)`\n\| link:(\S+?)\[(.*?)\]\n\| (\w+) \| ([\d.]+)", index)
    )
    if [row[1][5:] for row in rows] != numbers:
        report("INDEX rows differ from the spec files")
    for row in rows:
        spec_id, filename, _, status, version = row.groups()
        path = SPECS / filename
        text = read(path) if path.exists() else ""
        matches = (
            text.startswith(f"= {spec_id}")
            and f"\n:status: {status}\n" in text
            and f"\n:version: {version}\n" in text
        )
        if not matches:
            report("INDEX row does not match its spec", spec_id)


def check_error_codes(spec_files: list[Path]) -> None:
    """Codes used in the specs are registered in SPEC-04, and each registered code is used."""
    registry_file = SPECS / "SPEC-04-cli-toolchain.adoc"
    registry_text = read(registry_file)
    registered = set(re.findall(rf"^\| `({CODE})`", registry_text, re.MULTILINE))
    used = {code for f in spec_files for code in re.findall(CODE, read(f))}
    if used - registered:
        report("codes used but not registered:", sorted(used - registered))
    others = [f for f in spec_files if f != registry_file]
    for code in registered:
        if not any(code in read(f) for f in others):
            report("registered code never cited by another spec:", code)
    # The registry's last column names the specs that cite each code.
    for line in registry_text.splitlines():
        match = re.match(rf"\| `({CODE})` \|.* \| ((?:\d\d)(?:, \d\d)*)$", line)
        for number in match[2].split(", ") if match else []:
            matches = list(SPECS.glob(f"SPEC-{number}-*.adoc"))
            if not matches:
                report("registry names a missing spec", match[1], number)
            elif match[1] not in read(matches[0]):
                report("registry names a spec that never cites the code", number, match[1])


def check_manifests(spec_files: list[Path]) -> int:
    """Schemas are valid; examples and manifests embedded in specs validate. Returns how many
    embedded manifests were checked."""
    manifest_schema = json.loads(read(ROOT / "schemas" / "node_manifest.schema.json"))
    ir_schema = json.loads(read(ROOT / "schemas" / "node_ir.schema.json"))
    Draft202012Validator.check_schema(manifest_schema)
    Draft202012Validator.check_schema(ir_schema)
    validator = Draft202012Validator(manifest_schema)
    for example in sorted((ROOT / "examples").glob("*.toml")):
        for error in validator.iter_errors(tomllib.loads(read(example))):
            report("example", example.name, error.message[:70])
    embedded = 0
    for spec in spec_files:
        text = read(spec)
        for block in re.finditer(r"\[source,toml\]\n----\n(.*?)\n----", text, re.DOTALL):
            if block.group(1).startswith("include::"):
                continue
            document = tomllib.loads(block.group(1))
            if "node" in document:
                embedded += 1
                for error in validator.iter_errors(document):
                    report("embedded manifest", spec.name, error.message[:70])
        for block in re.finditer(r"\[source,(yaml|json)\]\n----\n(.*?)\n----", text, re.DOTALL):
            if block.group(2).startswith("include::"):
                continue
            parse = yaml.safe_load if block.group(1) == "yaml" else json.loads
            try:
                parse(block.group(2))
            except Exception as error:  # noqa: BLE001 - any parse failure is a finding
                report("embedded", block.group(1), "does not parse in", spec.name, str(error)[:50])
    return embedded


def check_extension_owners() -> None:
    """Each extension block is owned by the same spec in the schema, SPEC-01 and the index, and
    that spec mentions every key of the block."""
    schema = json.loads(read(ROOT / "schemas" / "node_manifest.schema.json"))
    spec_01 = read(next(SPECS.glob("SPEC-01-*.adoc")))
    index = read(SPECS / "INDEX.adoc")
    for block, (definition, number) in EXTENSION_OWNERS.items():
        text = read(next(SPECS.glob(f"SPEC-{number}-*.adoc")))
        for key in schema["$defs"][definition]["properties"]:
            if not re.search(rf"\b{key}\b", text):
                report("schema key not described in its spec", definition, key)
        for name, document in (("SPEC-01", spec_01), ("INDEX", index)):
            if not re.search(rf"\| `{block}` \| `SPEC-{number}`", document):
                report("owner mismatch", name, block, number)
        if f"`$defs/{definition}`" not in text:
            report("spec does not cite its schema definition", number, definition)


def main() -> int:
    """Run every check; print a summary; return the exit status."""
    os.chdir(SPECS)
    spec_files = sorted(SPECS.glob("SPEC-*.adoc"))
    numbers = check_numbering(spec_files)
    check_references(spec_files, numbers)
    check_index(spec_files, numbers)
    check_error_codes(spec_files)
    embedded = check_manifests(spec_files)
    check_extension_owners()
    for problem in problems:
        print("PROBLEM:", problem)
    registry = read(SPECS / "SPEC-04-cli-toolchain.adoc")
    code_count = len(set(re.findall(rf"^\| `({CODE})`", registry, re.MULTILINE)))
    example_count = len(glob.glob(str(ROOT / "examples" / "*.toml")))
    print(
        f"specs 00..{numbers[-1]} | embedded manifests: {embedded} | "
        f"codes: {code_count} | examples: {example_count}"
    )
    print("ISSUES FOUND" if problems else "ALL CONSISTENT")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
