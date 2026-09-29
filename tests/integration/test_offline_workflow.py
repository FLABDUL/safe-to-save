import hashlib
import json
from pathlib import Path

import pytest

from safe_to_save.cli import main
from safe_to_save.db import connect
from safe_to_save.history import load_history

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("seed_accepted", [False, True])
def test_cli_intra_import_conflict_retains_rejected_evidence_only(
    repository, tmp_path, capsys, seed_accepted,
):
    fixture = Path("tests/fixtures/synthetic_history")
    original = load_history(fixture)
    if seed_accepted:
        repository.import_bundle(original)
    for source in fixture.glob("*.json*"):
        (tmp_path / source.name).write_bytes(source.read_bytes())
    source = tmp_path / "transactions.jsonl"
    original_bytes = source.read_bytes()
    conflict = json.loads(original_bytes.splitlines()[0])
    conflict["amount_minor"] = -9999
    conflict_line = json.dumps(conflict).encode() + b"\r\n"
    # Evidence after the conflicting ID must survive too.
    trailing = {**conflict, "transaction_id": "synthetic-after-conflict"}
    source.write_bytes(original_bytes + conflict_line + json.dumps(trailing).encode() + b"\n")
    assert main(["history", "import", "--directory", str(tmp_path)]) == 1
    output = capsys.readouterr()
    assert "HistoryConflictError" in output.err
    assert "-9999" not in output.err and conflict["transaction_id"] not in output.err
    if seed_accepted:
        assert repository.load_bundle(original.manifest.dataset_id) == original
    with connect(repository.dsn) as connection:
        assert connection.execute("SELECT count(*) FROM transactions").fetchone()[0] == (
            len(original.transactions) if seed_accepted else 0
        )
        attempts = connection.execute(
            "SELECT import_id, records_seen FROM history_imports WHERE status = 'rejected'"
        ).fetchall()
        assert len(attempts) == 1
        import_id, seen = attempts[0]
        # Every original JSONL record, two appended records, and the exact manifest.
        assert seen == len(original.raw_records) + 3
        rows = connection.execute(
            """SELECT source_name, source_line, source_payload, source_hash
               FROM raw_history_records WHERE import_id = %s""", (import_id,),
        ).fetchall()
        for name, line, payload, digest in rows:
            chunks = ((tmp_path / name).read_bytes().splitlines(keepends=True)
                      if name != "manifest.json" else [(tmp_path / name).read_bytes()])
            assert payload == json.loads(chunks[line - 1])
            assert digest == hashlib.sha256(chunks[line - 1]).hexdigest()
    assert repository.count_raw_versions(conflict["transaction_id"]) == 2


def test_malformed_history_does_not_become_a_parsed_conflict_audit(repository, tmp_path):
    fixture = Path("tests/fixtures/synthetic_history")
    for source in fixture.glob("*.json*"):
        (tmp_path / source.name).write_bytes(source.read_bytes())
    source = tmp_path / "transactions.jsonl"
    rows = source.read_bytes()
    conflict = json.loads(rows.splitlines()[0])
    conflict["amount_minor"] = -9999
    source.write_bytes(rows + json.dumps(conflict).encode() + b"\nnot-json\n")
    assert main(["history", "import", "--directory", str(tmp_path)]) == 1
    with connect(repository.dsn) as connection:
        assert connection.execute("SELECT count(*) FROM history_imports").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM transactions").fetchone()[0] == 0


def run_args(output: Path, floor: str = "25000") -> list[str]:
    return ["backtest", "run", "--dataset-id", "synthetic-v2",
            "--review-weekday", "6", "--review-hour", "18",
            "--timezone", "Europe/London", "--floor-minor", floor,
            "--data-label", "Synthetic demonstration", "--output", str(output)]


def test_synthetic_workflow_is_persisted_reproducible_and_merchant_free(
    repository, tmp_path, capsys,
):
    assert main(["db", "migrate"]) == 0
    import_args = ["history", "import", "--directory", "tests/fixtures/synthetic_history"]
    assert main(import_args) == 0
    output = tmp_path / "report"
    assert main(run_args(output)) == 0
    original = {name: (output / name).read_bytes()
                for name in ("baseline-report.json", "baseline-report.md")}
    payload = json.loads(original["baseline-report.json"])
    assert set(payload["baselines"]) == {
        "trailing_median", "comparable_position", "conservative_rules",
    }
    assert payload["baselines"]["trailing_median"]["checkpoints_scored"] == 4
    for name in ("comparable_position", "conservative_rules"):
        assert payload["baselines"][name]["checkpoints_scored"] == 0
        assert payload["baselines"][name]["mean_absolute_error_minor"] is None
        assert payload["baselines"][name]["withheld_reasons"][
            "fewer_than_three_comparable_cycles"
        ] == 16
    assert payload["metadata"]["data_label"] == "Synthetic demonstration"
    assert len(payload["metadata"]["input_sha256"]) == 64
    assert original["baseline-report.md"].startswith(b"> **Synthetic demonstration:**")
    for content in original.values():
        for forbidden in (b"Synthetic Groceries", b"transaction_id", b"account_id",
                          b"commitment_id", b"DATABASE_URL"):
            assert forbidden not in content
    assert main(import_args) == 0
    assert main(run_args(output)) == 0
    assert {name: (output / name).read_bytes() for name in original} == original
    assert "noop" in capsys.readouterr().out
    with connect(repository.dsn) as connection:
        assert connection.execute("SELECT count(*) FROM backtest_runs").fetchone()[0] == 3
        assert connection.execute(
            "SELECT count(*) FROM history_imports WHERE status = 'noop'"
        ).fetchone()[0] == 1
    assert not list(output.glob("*.tmp"))


def test_changed_policy_uses_sibling_output_without_overwriting(repository, tmp_path):
    assert main(["history", "import", "--directory", "tests/fixtures/synthetic_history"]) == 0
    output = tmp_path / "report"
    assert main(run_args(output)) == 0
    original = (output / "baseline-report.json").read_bytes()
    assert main(run_args(output, "30000")) == 0
    assert (output / "baseline-report.json").read_bytes() == original
    siblings = list(tmp_path.glob("report-*"))
    assert len(siblings) == 1
    assert json.loads((siblings[0] / "baseline-report.json").read_text())["metadata"][
        "floor_minor"
    ] == 30000
