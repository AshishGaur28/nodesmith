# Nodesmith

Turns a declarative node manifest (TOML, YAML or JSON) into a ROS 2 node in C++ or Python source package (ROS 2 Jazzy). The specs stop at generation. `conformance/` holds the test corpus for the front end (`python conformance/verify.py`). Purely software and configuration driven: no hardware drivers, accelerators or hardware-in-the-loop.

**Status: front end built, generators not yet.** The suite in [`docs/specs/`](docs/specs/INDEX.adoc) is the source of truth. `nodesmith validate` and `nodesmith lower` work and pass the conformance corpus; `generate`, `bench` and `clean` are not implemented, and `templates/` is empty scaffolding.

```bash
pip install -e '.[dev]'
nodesmith validate --manifest examples/02_filter_pipeline.toml
nodesmith lower --manifest examples/02_filter_pipeline.toml --output build/imu.ir.json
pytest                                  # product front end vs. the corpus
python conformance/verify.py            # the corpus vs. the independent reference
python tools/check_specs.py             # specs and schemas agree
``` `frontend/` is empty scaffolding for a possible visual builder and is not covered by any spec.

- Start with the [spec index](docs/specs/INDEX.adoc) (reading order, what is normative, known gaps).
- Normative schemas: [`schemas/node_manifest.schema.json`](schemas/node_manifest.schema.json), [`schemas/node_ir.schema.json`](schemas/node_ir.schema.json).
- Example manifests: [`examples/`](examples/) (all validate against the schema).
- Tested reference material: [`docs/specs/reference/`](docs/specs/reference/) (concurrency primitives in C++).

## License

Apache-2.0, see [`LICENSE`](LICENSE).
