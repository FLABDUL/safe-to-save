"""Private, deterministic configuration for the offline Gate A evaluation."""

import json
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FloorScenarios(BaseModel):
    """Illustrative or privately supplied floors in integer minor units."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    lower: int = Field(ge=0, strict=True)
    primary: int = Field(ge=0, strict=True)
    higher: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def ordered(self) -> "FloorScenarios":
        if not self.lower < self.primary < self.higher:
            raise ValueError("floors must be strictly increasing")
        return self


class GateAConfig(BaseModel):
    """Frozen configuration; use load_gate_config to enforce private boundaries."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    schema_version: Literal["gate-a-v1"]
    as_of_date: date
    period_start: date
    period_end_exclusive: date
    dataset_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    source_csv: Path
    overrides_path: Path | None = None
    anchors_path: Path
    paydays_path: Path
    commitments_path: Path
    canonical_directory: Path
    output_directory: Path
    floors: FloorScenarios

    @model_validator(mode="after")
    def complete_months(self) -> "GateAConfig":
        start, end = self.period_start, self.period_end_exclusive
        months = (end.year - start.year) * 12 + end.month - start.month
        if (start.day != 1 or end.day != 1
                or end != self.as_of_date.replace(day=1) or not 6 <= months <= 12):
            raise ValueError("period must cover 6 to 12 complete months before the as-of month")
        return self


def require_within(path: Path, root: Path, label: str) -> Path:
    """Return a resolved path only if it stays inside the resolved root.

    Path.resolve follows Windows junctions and symlinks, including existing
    parents of not-yet-created output paths. Messages deliberately omit paths
    and the caller-supplied label, which may itself contain private information.
    """
    try:
        resolved = path.resolve()
        resolved_root = root.resolve()
        if not resolved.is_relative_to(resolved_root):
            raise ValueError("path is outside its permitted private root")
    except (OSError, RuntimeError, ValueError):
        raise ValueError("path is outside its permitted private root") from None
    return resolved


def load_gate_config(path: Path, project_root: Path) -> GateAConfig:
    """Load private JSON and resolve every path relative to the explicit project.

    Loading never creates directories or reads source data. Both private roots
    must themselves remain within the project, even when linked on Windows.
    """
    try:
        project = project_root.resolve()
        inputs = require_within(project / "data/private", project, "private inputs")
        outputs = require_within(project / "outputs/private", project, "private outputs")
        if inputs.is_relative_to(outputs) or outputs.is_relative_to(inputs):
            raise ValueError("private input and output roots must be separate")
        config_path = require_within(project / path, inputs, "configuration")
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload.pop("$comment", None)
        config = GateAConfig.model_validate(payload)
        resolved_paths = {}
        for field in ("source_csv", "overrides_path", "anchors_path", "paydays_path",
                      "commitments_path", "canonical_directory", "output_directory"):
            value = getattr(config, field)
            if value is not None:
                root = outputs if field in ("canonical_directory", "output_directory") else inputs
                resolved_paths[field] = require_within(project / value, root, field)
        return config.model_copy(update=resolved_paths)
    except (OSError, RuntimeError, ValueError):
        raise ValueError("invalid private Gate A configuration") from None
