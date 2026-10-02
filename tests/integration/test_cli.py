import json
from pathlib import Path

import pytest

from nodesmith.cli.main import main

EX = Path(__file__).resolve().parents[2] / "examples"
BAD = Path(__file__).resolve().parents[2] / "conformance" / "invalid"
MULTI = Path(__file__).resolve().parents[2] / "conformance" / "multi"


def test_validate_ok(capsys):
    assert main(["validate", "--manifest", str(EX / "01_simple_pubsub.toml")]) == 0


def test_lower_writes_canonical_ir(tmp_path):
    out = tmp_path / "x.ir.json"
    assert (
        main(["lower", "--manifest", str(EX / "02_filter_pipeline.toml"), "--output", str(out)])
        == 0
    )
    assert json.loads(out.read_text())["node_meta"]["name"] == "imu_filter_node"


@pytest.mark.parametrize(
    "name,status",
    [
        ("syn002_unknown_key.toml", 3),
        ("sem101_unknown_parameter.toml", 4),
        ("cnc201_state_split_across_groups.toml", 5),
        ("sec303_publishes_to_rosout.toml", 6),
        ("shm002_type_not_fixed_size.toml", 8),
    ],
)
def test_exit_status_follows_the_category(name, status):
    assert main(["validate", "--manifest", str(BAD / name)]) == status


def test_json_report(capsys):
    assert (
        main(
            [
                "--format",
                "json",
                "validate",
                "--manifest",
                str(BAD / "sem109_constant_division_by_zero.toml"),
            ]
        )
        == 4
    )
    report = json.loads(capsys.readouterr().out)
    assert (
        report["exit_code"] == 4
        and report["diagnostics"][0]["code"] == "ERR_SEM_109"
        and not report["success"]
    )


def test_warning_exits_zero_but_strict_fails():
    m = str(BAD / "sem110_warn_unused_subscriber.toml")
    assert main(["validate", "--manifest", m]) == 0
    assert main(["validate", "--manifest", m, "--strict"]) == 4


def test_release_mode_enables_the_production_check():
    m = str(BAD / "sim001_release_block_not_allowed.toml")
    assert main(["validate", "--manifest", m]) == 0
    assert main(["--mode", "release", "validate", "--manifest", m]) == 8


def test_missing_file_is_a_generic_error():
    assert main(["validate", "--manifest", "/nonexistent.toml"]) == 1


@pytest.mark.parametrize("command", ["bench", "clean"])
def test_unimplemented_command(command):
    assert main([command]) == 1


def test_text_output_lists_every_error_and_one_summary(capsys):
    assert main(["validate", "--manifest", str(MULTI / "sem_errors_in_three_pipelines.toml")]) == 4
    err = capsys.readouterr().err
    assert (
        err.count("error[") == 3
        and err.count("exit status") == 1
        and "aborting due to 3 errors" in err
    )
