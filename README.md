# Nodesmith

> [!WARNING]
> **Nothing is shipped.** Nodesmith is an early work in progress and has no release.
>
> - **The generated C++ has never been built against ROS 2.** `nodesmith generate` writes a C++ package for simple manifests (no extension blocks), but nothing has been compiled or run with a real ROS 2 Jazzy installation. It has only been compiled and exercised against a local stand-in for rclcpp, so expect it to fail against the real thing until the Jazzy CI job has passed.
> - **Python generation is not implemented.**
> - **Not published.** There is no PyPI package, no release and no version you can depend on. Do not use it in a project.
> - **Everything can still change.** The specs are drafts (only the manifest format is frozen, and only for this unreleased project).

What works today: `nodesmith validate` and `nodesmith lower` check a manifest and turn it into a language-neutral intermediate representation (IR), and `nodesmith generate` writes a C++ package from it for the core blocks. The numeric behaviour of the generated pipelines is tested against a reference interpreter on random inputs; everything that touches ROS is not verified yet.

## What it is meant to become

Nodesmith turns a declarative node manifest (TOML, YAML or JSON) into a ROS 2 (Jazzy) node in C++ or Python source package. The specs stop at generation. Purely software and configuration driven: no hardware drivers, accelerators or hardware-in-the-loop.

## Status

| Part | State |
|---|---|
| Specifications ([`docs/specs/`](docs/specs/INDEX.adoc)) | Drafts. `SPEC-00`, `01` and `02` (manifest, IR, expression language) are frozen for this unreleased project; the rest can change. |
| Front end (`nodesmith validate`, `nodesmith lower`) | Works. Passes the conformance corpus. |
| C++ generator (`nodesmith generate`, core blocks) | Written. Pipelines checked against a reference interpreter; ROS glue checked only against a local stand-in; never built with real ROS 2. Extension blocks are refused. |
| Python generator | Not started. |
| `nodesmith bench`, `nodesmith clean` | Not implemented. |
| Python support for extension blocks | Planned right after v1. v1 supports the core blocks only. |
| Anything ROS-specific (build, run, real-time, shared memory, security) | Specified, never verified against ROS 2 Jazzy. |
| `frontend/` | Empty scaffolding for a possible visual builder. No spec covers it. |

## Conformance corpus

[`conformance/`](conformance/README.md) is the test suite for the front end: known manifests with the exact result each must produce. It holds the expected IR and hash for every example (plus YAML and JSON copies), one rejected manifest per error code (each code has at least two), and manifests with several errors that must all be reported. Two independently written implementations, the product and a reference, are checked against it, and CI runs both on every push. Details are in the [conformance README](conformance/README.md).

## Try the front end

```bash
pip install -e '.[dev]'
nodesmith validate --manifest examples/02_filter_pipeline.toml
nodesmith lower --manifest examples/02_filter_pipeline.toml --output build/imu.ir.json
nodesmith generate --manifest examples/02_filter_pipeline.toml --output-dir build/imu_filter_node   # C++ package, core blocks only
pytest                                  # front end vs. the corpus; generated C++ vs. a reference interpreter (needs a C++ compiler)
python conformance/verify.py            # the corpus vs. the independent reference
python tools/check_specs.py             # specs and schemas agree
```

## Where to look

- Start with the [spec index](docs/specs/INDEX.adoc) (reading order, what is normative, known gaps).
- Normative schemas: [`schemas/node_manifest.schema.json`](schemas/node_manifest.schema.json), [`schemas/node_ir.schema.json`](schemas/node_ir.schema.json).
- Example manifests: [`examples/`](examples/) (all validate against the schema).
- Conformance corpus: [`conformance/`](conformance/README.md).
- Plan and milestones: [`docs/PLAN.adoc`](docs/PLAN.adoc).
- Reference material: [`docs/specs/reference/`](docs/specs/reference/) (concurrency primitives in C++, tested under ThreadSanitizer).

## License

Apache-2.0, see [`LICENSE`](LICENSE).
