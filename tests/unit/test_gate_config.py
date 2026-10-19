import json
import os
import subprocess

import pytest
from pydantic import ValidationError

from safe_to_save.gate_config import FloorScenarios, GateAConfig, load_gate_config, require_within


def config_payload(**changes):
    payload = {
        "schema_version": "gate-a-v1",
        "as_of_date": "2026-10-09",
        "period_start": "2026-04-01",
        "period_end_exclusive": "2026-10-01",
        "dataset_id": "synthetic-dataset",
        "account_id": "synthetic-account",
        "source_csv": "data/private/source.csv",
        "overrides_path": "data/private/overrides.json",
        "anchors_path": "data/private/anchors.json",
        "paydays_path": "data/private/paydays.json",
        "commitments_path": "data/private/commitments.json",
        "canonical_directory": "outputs/private/canonical",
        "output_directory": "outputs/private/reports",
        "floors": {"lower": 10000, "primary": 25000, "higher": 40000},
    }
    return payload | changes


def write_config(root, payload):
    path = root / "data/private/gate-a.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_three_floors_must_be_non_negative_and_strictly_ordered():
    assert FloorScenarios(lower=10000, primary=25000, higher=40000).primary == 25000
    assert FloorScenarios(lower=0, primary=1, higher=2).lower == 0
    for values in [(10000, 10000, 40000), (10000, 40000, 40000), (-1, 25000, 40000),
                   (40000, 25000, 10000), (1.5, 25000, 40000), (True, 25000, 40000)]:
        with pytest.raises(ValidationError):
            FloorScenarios(lower=values[0], primary=values[1], higher=values[2])


@pytest.mark.parametrize("start, end, valid", [
    ("2026-04-01", "2026-10-01", True),
    ("2025-10-01", "2026-10-01", True),
    ("2026-05-01", "2026-10-01", False),
    ("2025-09-01", "2026-10-01", False),
    ("2026-04-02", "2026-10-01", False),
    ("2026-04-01", "2026-10-09", False),
    ("2026-04-01", "2026-11-01", False),
    ("2026-03-01", "2026-09-01", False),
])
def test_period_is_six_to_twelve_complete_months_before_as_of(start, end, valid):
    payload = config_payload(period_start=start, period_end_exclusive=end)
    if valid:
        assert GateAConfig.model_validate(payload).period_start.isoformat() == start
    else:
        with pytest.raises(ValidationError):
            GateAConfig.model_validate(payload)


def test_private_inputs_and_outputs_are_resolved_under_separate_roots(tmp_path):
    config = load_gate_config(write_config(tmp_path, config_payload()), tmp_path)
    assert config.source_csv == tmp_path / "data/private/source.csv"
    assert config.overrides_path == tmp_path / "data/private/overrides.json"
    assert config.anchors_path == tmp_path / "data/private/anchors.json"
    assert config.paydays_path == tmp_path / "data/private/paydays.json"
    assert config.commitments_path == tmp_path / "data/private/commitments.json"
    assert config.canonical_directory == tmp_path / "outputs/private/canonical"
    assert config.output_directory == tmp_path / "outputs/private/reports"
    with pytest.raises(ValidationError):
        config.dataset_id = "replacement"
    with pytest.raises(ValidationError):
        config.floors.primary = 30000
    for field, value in [("source_csv", "outputs/private/source.csv"),
                         ("output_directory", "data/private/reports"),
                         ("overrides_path", "../outside.json")]:
        with pytest.raises(ValueError):
            load_gate_config(write_config(tmp_path, config_payload(**{field: value})), tmp_path)


def test_resolved_private_path_cannot_escape_root(tmp_path):
    root = tmp_path / "private"
    root.mkdir()
    assert require_within(root / "nested/file.csv", root, "input") == root / "nested/file.csv"
    for escaped in [root / "../outside.csv", tmp_path / "private-sibling/file.csv"]:
        with pytest.raises(ValueError):
            require_within(escaped, root, "input")


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_link_escape_is_rejected_after_resolution(tmp_path, kind):
    root = tmp_path / "private"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "link"
    if kind == "junction":
        if os.name != "nt":
            pytest.skip("Windows junction test")
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                                capture_output=True, check=False)
        assert result.returncode == 0
    else:
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("Symlink creation is unavailable")
    with pytest.raises(ValueError):
        require_within(link / "source.csv", root, "input")


def test_config_cannot_escape_private_root(tmp_path):
    path = tmp_path / "outside.json"
    path.write_text(json.dumps(config_payload()), encoding="utf-8")
    with pytest.raises(ValueError):
        load_gate_config(path, tmp_path)


@pytest.mark.parametrize("private_root", ["data/private", "outputs/private"])
def test_private_roots_cannot_escape_project_through_junction(tmp_path, private_root):
    if os.name != "nt":
        pytest.skip("Windows junction test")
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    link = project / private_root
    link.parent.mkdir()
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                   capture_output=True, check=True)
    with pytest.raises(ValueError):
        load_gate_config(write_config(project, config_payload()), project)


def test_optional_overrides_can_be_omitted(tmp_path):
    payload = config_payload()
    del payload["overrides_path"]
    assert load_gate_config(write_config(tmp_path, payload), tmp_path).overrides_path is None


def test_public_example_comment_is_allowed_without_allowing_unknown_keys(tmp_path):
    payload = config_payload(**{"$comment": "All money values are illustrative."})
    assert load_gate_config(write_config(tmp_path, payload), tmp_path).floors.primary == 25000


@pytest.mark.parametrize("changes", [
    {"schema_version": "wrong"}, {"dataset_id": ""},
    {"floors": {"lower": -123456789, "primary": 25000, "higher": 40000}},
    {"source_csv": "../private-marker-secret.csv"},
    {"unexpected": "private-marker-secret"},
])
def test_invalid_config_errors_do_not_echo_private_values(tmp_path, changes):
    with pytest.raises(ValueError) as caught:
        load_gate_config(write_config(tmp_path, config_payload(**changes)), tmp_path)
    assert "private-marker-secret" not in str(caught.value)
    assert "123456789" not in str(caught.value)
    assert str(tmp_path) not in str(caught.value)
    assert caught.value.__cause__ is None


def test_malformed_json_error_is_redacted(tmp_path):
    path = write_config(tmp_path, config_payload())
    path.write_text('{"private-marker-secret": invalid}', encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        load_gate_config(path, tmp_path)
    assert "private-marker-secret" not in str(caught.value)
    assert str(tmp_path) not in str(caught.value)
