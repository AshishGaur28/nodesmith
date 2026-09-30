# Conformance corpus

The executable definition of "a correct front end" for `manifest_version = "1"`. Every implementation of Stages 1 to 4 of `nodesmith` (M1, M2) must pass it; the specs and schemas stay normative, the corpus shows what they mean.

```bash
python conformance/verify.py            # needs python 3.11+, jsonschema, pyyaml
python conformance/verify.py --update   # rewrite expected/ from the reference front end; review the diff
```

| Path | Content |
|---|---|
| `../examples/*.toml` | The valid manifests. |
| `expected/<name>.ir.json`, `.sha256` | The IR each valid manifest must lower to (canonical form) and its hash. |
| `parity/<name>.yaml`, `.json` | The same manifests in the other formats. They must give the same hash. |
| `multi/*`, `multi/expected.json` | Manifests with several errors: every error the product reports, in order (`SPEC-04` §3). |
| `invalid/<cat><nnn>_<slug>.*` | One manifest that must be rejected with `ERR_<CAT>_<NNN>`. `warn_` in the slug means it is accepted with that warning instead; `release` means it is built with `--mode=release`. |
| `../docs/specs/*.adoc` | Every manifest embedded in a spec must be accepted. |
| `reference/front.py` | Reference Stage 3/4 lowering (core model). Evidence for `expected/`, not the product. |

`verify.py` also implements the cross-block rules (`SPEC-01` §5.1) and the format rules (`SPEC-01` §2) that the reference needs. The product must reimplement them, not import them.

## What is and is not covered

- Covered: every error code a manifest can trigger at build time, each by at least one case.
- Not covered (need a runtime or are retired): the `ERR_*` runtime codes, and the retired `ERR_SEM_111`, `ERR_CNC_202`, `ERR_CNC_203`.
- Each `invalid/` case triggers exactly one error (the product must report that code and nothing else, so cascades are caught). `multi/` holds manifests with several errors and `multi/expected.json` lists every code in order; the reference reports only the first, so `verify.py` checks that one.
- Known limit: the number-form check on TOML looks at the start of a value, so a bad number inside an inline array or table is not caught by the reference.
