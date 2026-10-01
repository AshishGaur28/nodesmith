#!/usr/bin/env python3
"""Regenerates tests/golden/<example>/ (the C++ package for each example the generator supports). Review the diff before keeping it."""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from nodesmith.compiler import compile_manifest  # noqa: E402
from nodesmith.cpp.generate import package  # noqa: E402
from nodesmith.cpp.pipeline import Unsupported  # noqa: E402

GOLDEN = ROOT / "tests" / "golden"
for example in sorted((ROOT / "examples").glob("*.toml")):
    try: files = package(compile_manifest(example).ir)
    except Unsupported as e:
        print(f"skip {example.name}: {e}")
        continue
    target = GOLDEN / example.stem
    shutil.rmtree(target, ignore_errors=True)
    for rel, text in files.items():
        if rel.endswith("runtime.hpp"): continue        # identical in every package; checked on its own
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        (target / rel).write_text(text, encoding="utf-8")
    print(f"wrote {target.relative_to(ROOT)}")
