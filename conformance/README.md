# Conformance corpus

"Conformance" means: does an implementation behave the way the specs say? This folder holds known inputs together with the exact result each must produce. It is the test suite for the part of Nodesmith that reads and checks a manifest (ingestion, schema check, cross-block rules, semantic checks, lowering to the IR). It is the executable definition of "a correct front end" for `manifest_version = "1"`.

The specs and schemas stay normative; the corpus shows what they mean. Any implementation of those stages, including a future rewrite in another language, must pass it.

## Why it exists

1. **It pins down the specs.** Writing the cases exposed places where the specs were vague or contradicted themselves; each was fixed in the spec (`docs/PLAN.adoc`, "M0 record" and "M2").
2. **It protects the front end.** `pytest` runs the real `nodesmith` against every case, and CI does the same on each push. A change that alters an IR hash, loses an error or adds a bogus one fails at once.
3. **It is the acceptance test for a reimplementation.** It says what "the same result" means, down to the IR bytes and the hash.

It does not cover the C++ and Python generators. Those get their own tests in milestone M3.

## Running it

```bash
pytest                                  # the product front end (src/nodesmith) against this corpus
python conformance/verify.py            # the corpus against the independent reference; needs python 3.11+, jsonschema, pyyaml
python conformance/verify.py --update   # rewrite expected/ from the reference; review the diff before keeping it
```

Two implementations check each other: the product (`src/nodesmith`) and the reference (`reference/front.py` with `verify.py`). The reference was written separately from the product, from the specs, so they do not share mistakes. `expected/` is produced by the reference and then checked against the product.

## Layout

| Path | Content |
|---|---|
| `../examples/*.toml` | The 8 valid manifests. |
| `expected/<name>.ir.json`, `.sha256` | The IR each valid manifest must lower to (canonical form) and its SHA-256 hash (`SPEC-00` §5). |
| `parity/<name>.yaml`, `.json` | The same manifests in YAML and JSON. They must give the same hash as the TOML. |
| `invalid/<cat><nnn>_<slug>.*` | One manifest that must be rejected with exactly `ERR_<CAT>_<NNN>` (85 cases). |
| `multi/*.toml`, `multi/*.yaml`, `multi/expected.json` | Manifests with several errors. `expected.json` lists every code that must be reported, in order (`SPEC-04` §3). |
| `../docs/specs/*.adoc` | Every manifest embedded in a spec must be accepted. |
| `reference/front.py` | The reference Stage 3/4 lowering (core model). Evidence for `expected/`, not the product. |
| `verify.py` | The runner, plus the reference's format rules (`SPEC-01` §2), cross-block rules (`SPEC-01` §5.1) and constraint evaluation. |

## Naming an invalid case

The file name carries the expectation, so a case needs no extra metadata:

- `sem109_constant_division_by_zero.toml` must fail with `ERR_SEM_109`.
- `warn_` in the slug (`sem110_warn_unused_subscriber.toml`) means it is accepted with that warning, and rejected when `--strict` promotes the warning to an error.
- `release` in the slug (`sim001_release_block_not_allowed.toml`) means it is built with `--mode=release`.
- Invalid cases may be `.toml`, `.yaml` or `.json`, as the format rule under test needs.

## What the checks assert

For every valid manifest:
- it is accepted and its IR validates against `schemas/node_ir.schema.json`;
- its IR equals `expected/<name>.ir.json` and its hash equals `expected/<name>.sha256`;
- its YAML and JSON copies give the same hash;
- the canonical form is a fixed point.

For every `invalid/` case:
- it is rejected with exactly its one code and nothing else, so cascades of follow-on errors are caught;
- every diagnostic has a position (file, line, column) inside the file.

For every `multi/` case:
- the product reports exactly the listed codes, in the listed order;
- the reference reports only the first error, so `verify.py` checks that the first one matches.

Also checked: callback-group assignment against answers worked out by hand for all eight examples, and as properties on random inputs; and `--format=json` output against the schema printed in `SPEC-04` §4.4.

## What is and is not covered

- **Covered:** every error code a manifest can trigger at build time, each by at least two `invalid/` cases.
- **Not covered:** the runtime `ERR_*` codes (they need a running node) and the retired codes `ERR_SEM_111`, `ERR_CNC_202`, `ERR_CNC_203`.
- **Known limits of the reference:**
  - The number-form check on TOML looks at the start of a value, so a bad number inside an inline array or table is not caught.
  - Its constraint check models neither integer wrap-around nor float32 rounding. (The product models integer wrap-around, not float32 rounding.)
  - It reports only the first error; multi-error reporting is checked in the product only.
  - `validation.regex` has no defined dialect and is not checked (a known gap, `docs/specs/INDEX.adoc` §8).

## Changing the corpus

A change to the schemas, the expression language or the error registry follows the freeze policy in `docs/specs/INDEX.adoc` §7 and must pass both commands above in the same change. A new error code needs at least two cases here.
