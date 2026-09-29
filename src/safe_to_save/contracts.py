from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class _FrozenDict(dict[str, object]):
    """A JSON-compatible mapping that rejects mutation after construction."""

    @staticmethod
    def _immutable(*_args: object, **_kwargs: object) -> None:
        raise TypeError("raw history payloads are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


def _deep_freeze(value: object) -> object:
    if isinstance(value, dict):
        return _FrozenDict({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


class EconomicType(StrEnum):
    EXPENSE = "expense"
    INCOME = "income"
    INTERNAL_TRANSFER = "internal_transfer"
    SAVINGS_TRANSFER = "savings_transfer"
    ADJUSTMENT = "adjustment"


class CanonicalTransaction(BaseModel):
    model_config = ConfigDict(frozen=True)

    transaction_id: str = Field(min_length=1)
    occurred_at: AwareDatetime
    amount_minor: int = Field(strict=True)
    currency: Literal["GBP"]
    status: Literal["settled"]
    economic_type: EconomicType
    committed: bool = False
    category: str | None = None

    _normalise_time = field_validator("occurred_at")(_as_utc)


class BalanceAnchor(BaseModel):
    model_config = ConfigDict(frozen=True)

    captured_at: AwareDatetime
    balance_minor: int = Field(strict=True)
    currency: Literal["GBP"]

    _normalise_time = field_validator("captured_at")(_as_utc)


class Payday(BaseModel):
    model_config = ConfigDict(frozen=True)

    payday: date
    known_from: date


class Commitment(BaseModel):
    model_config = ConfigDict(frozen=True)

    commitment_id: str = Field(min_length=1)
    known_from: AwareDatetime
    due_at: AwareDatetime
    amount_minor: int = Field(le=0, strict=True)
    description: str = Field(min_length=1)

    _normalise_times = field_validator("known_from", "due_at")(_as_utc)

    @model_validator(mode="after")
    def due_after_known(self) -> "Commitment":
        if self.due_at <= self.known_from:
            raise ValueError("due_at must be after known_from")
        return self


class HistoryManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    dataset_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    currency: Literal["GBP"]
    coverage_start: AwareDatetime
    coverage_end: AwareDatetime

    _normalise_times = field_validator("coverage_start", "coverage_end")(_as_utc)

    @model_validator(mode="after")
    def coverage_is_forward(self) -> "HistoryManifest":
        if self.coverage_end <= self.coverage_start:
            raise ValueError("coverage_end must be after coverage_start")
        return self


class RawHistoryRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_name: str
    source_line: int
    payload: dict[str, object]
    source_hash: str

    @field_validator("payload")
    @classmethod
    def freeze_payload(cls, value: dict[str, object]) -> dict[str, object]:
        frozen = _deep_freeze(value)
        if not isinstance(frozen, dict):  # pragma: no cover - field validation guarantees this
            raise TypeError("payload must be a mapping")
        return frozen


class HistoryEvidence(BaseModel):
    """Validated manifest and source evidence, without any canonical acceptance."""

    model_config = ConfigDict(frozen=True)

    manifest: HistoryManifest
    raw_records: tuple[RawHistoryRecord, ...]


class HistoryBundle(HistoryEvidence):
    transactions: tuple[CanonicalTransaction, ...]
    anchors: tuple[BalanceAnchor, ...]
    paydays: tuple[Payday, ...]
    commitments: tuple[Commitment, ...]
