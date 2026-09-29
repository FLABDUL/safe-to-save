from pathlib import Path


def test_ci_workflow_preserves_project_verification_contract() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "permissions:\n  contents: read" in workflow
    assert "pull_request_target" not in workflow
    assert "cancel-in-progress: true" in workflow
    assert "timeout-minutes: 15" in workflow
    assert "runs-on: ubuntu-24.04" in workflow
    assert "uses: actions/checkout@v7" in workflow
    assert "uses: actions/setup-python@v7" in workflow
    assert "python-version: \"3.11\"" in workflow
    assert "image: postgres:14" in workflow
    assert "55433:5432" in workflow
    assert (
        "DATABASE_URL: postgresql://sts_ci:sts_ci_password@127.0.0.1:55433/safe_to_save"
        in workflow
    )
    assert "python -m ruff check src tests" in workflow
    assert 'python -m pytest -m "not integration" -v' in workflow
    assert "apply_migrations" in workflow
    assert "python -m pytest -m integration -v" in workflow
