"""Strict, offline adapter for one evidenced Monzo current-account CSV profile."""

import csv
import hashlib
import io
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from safe_to_save.contracts import CanonicalTransaction, EconomicType

MONZO_PROFILE_VERSION = "monzo-csv-v1"
MONZO_CSV_V1_HEADERS = (
    "Transaction ID", "Date", "Time", "Type", "Name", "Emoji", "Category", "Amount",
    "Currency", "Local amount", "Local currency", "Notes and #tags", "Address", "Receipt",
    "Description", "Category split", "Money Out", "Money In",
)
_TIMESTAMP_FORMAT = "%d/%m/%Y %H:%M:%S"


def _error(reason: str, line: int) -> ValueError:
    return ValueError(f"{MONZO_PROFILE_VERSION} line={line} {reason}")


@dataclass(frozen=True)
class MonzoOverride:
    """Private classification keyed only by the physical CSV record start line."""

    source_line: int
    economic_type: EconomicType
    committed: bool
    category: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        line = self.source_line if type(self.source_line) is int else 0
        if (line < 2 or type(self.committed) is not bool
                or (self.category is not None and not isinstance(self.category, str))):
            raise _error("invalid_overrides", max(0, line))
        try:
            economic_type = EconomicType(self.economic_type)
        except (ValueError, TypeError):
            raise _error("invalid_overrides", line) from None
        object.__setattr__(self, "economic_type", economic_type)


@dataclass(frozen=True)
class MonzoRecordProvenance:
    """Link a canonical identity to exact CSV record evidence without raw text."""

    transaction_id: str
    source_line: int
    source_hash: str


@dataclass(frozen=True)
class ParsedMonzoExport:
    source_sha256: str
    line_hashes: tuple[str, ...]
    profile_name: str
    transactions: tuple[CanonicalTransaction, ...] = field(repr=False)
    record_provenance: tuple[MonzoRecordProvenance, ...]
    # The export has no account identifier: the operator must attest account scope.
    account_scope_verifiable: bool = False


def load_overrides(path: Path | None) -> dict[int, MonzoOverride]:
    """Load a JSON array of strict line-number classifications; never echo inputs."""
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, list):
            raise TypeError
        overrides = {}
        for record in payload:
            if not isinstance(record, dict):
                raise TypeError
            if not {"source_line", "economic_type", "committed"} <= record.keys():
                raise ValueError
            if record.keys() - {"source_line", "economic_type", "committed", "category"}:
                raise ValueError
            override = MonzoOverride(**record)
            if override.source_line in overrides:
                raise ValueError
            overrides[override.source_line] = override
        return overrides
    except (OSError, ValueError, TypeError):
        raise _error("invalid_overrides", 0) from None


def _timestamp(date: str, time: str, line: int) -> datetime:
    text = f"{date} {time}"
    if not re.fullmatch(r"[0-9]{2}/[0-9]{2}/[0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2}", text):
        raise _error("invalid_timestamp", line)
    try:
        # Naive local time is necessary to validate both London DST folds below.
        local = datetime.strptime(text, _TIMESTAMP_FORMAT)  # noqa: DTZ007
    except ValueError:
        raise _error("invalid_timestamp", line) from None
    london = ZoneInfo("Europe/London")
    candidates = set()
    for fold in (0, 1):
        utc = local.replace(tzinfo=london, fold=fold).astimezone(UTC)
        if utc.astimezone(london).replace(tzinfo=None) == local:
            candidates.add(utc)
    if not candidates:
        raise _error("nonexistent_timestamp", line)
    if len(candidates) != 1:
        raise _error("ambiguous_timestamp", line)
    return candidates.pop()


def _amount(text: str, line: int) -> int:
    if not re.fullmatch(r"-?[0-9]+(?:\.[0-9]{1,2})?", text):
        raise _error("invalid_amount", line)
    # Decimal's tuple avoids context-dependent rounding of very large values.
    value = Decimal(text)
    parts = value.as_tuple()
    coefficient = int("".join(str(digit) for digit in parts.digits))
    return (-1 if parts.sign else 1) * coefficient * 10 ** (parts.exponent + 2)


def _classification(type_label: str, amount: int, line: int) -> tuple[EconomicType, bool]:
    if type_label == "Card payment":
        return EconomicType.EXPENSE, False
    if type_label in ("Direct Debit", "Flex"):
        return EconomicType.EXPENSE, True
    if type_label == "Pot transfer":
        return EconomicType.SAVINGS_TRANSFER, False
    if type_label == "Bacs (Direct Credit)" and amount > 0:
        return EconomicType.INCOME, False
    raise _error("override_required", line)


def parse_monzo_export(
    path: Path, overrides: Mapping[int, MonzoOverride],
) -> ParsedMonzoExport:
    """Parse without touching the source; errors contain only profile/line/reason.

    Source hashes include exact bytes, BOM and original line endings. Line hashes
    include every physical line, including the header and multiline record parts.
    Record provenance links each transaction to its start line and complete bytes.
    Transaction IDs hash the provider identity, remaining stable on re-export.
    """
    try:
        source = path.read_bytes()
    except OSError:
        raise _error("source_unreadable", 0) from None
    try:
        text = source.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _error("invalid_encoding", 0) from None
    for line, override in overrides.items():
        if (type(line) is not int or line < 2 or not isinstance(override, MonzoOverride)
                or override.source_line != line):
            raise _error("invalid_overrides", line if type(line) is int and line >= 0 else 0)
    reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
    physical_lines = source.splitlines(keepends=True)
    identities: set[str] = set()
    used_overrides: set[int] = set()
    transactions = []
    record_provenance = []
    source_line = 1
    try:
        if tuple(reader.fieldnames or ()) != MONZO_CSV_V1_HEADERS:
            raise _error("unsupported_header", 1)
        while True:
            source_line = reader.reader.line_num + 1
            try:
                row = next(reader)
            except StopIteration:
                if reader.reader.line_num >= source_line:
                    raise _error("invalid_row", source_line) from None
                break
            if not physical_lines[source_line - 1].rstrip(b"\r\n"):
                raise _error("invalid_row", source_line)
            if None in row or any(value is None for value in row.values()):
                raise _error("invalid_row", source_line)
            identity = row["Transaction ID"]
            if not identity.strip():
                raise _error("blank_identity", source_line)
            if identity in identities:
                raise _error("duplicate_identity", source_line)
            identities.add(identity)
            if row["Currency"] != "GBP":
                raise _error("unsupported_currency", source_line)
            amount = _amount(row["Amount"], source_line)
            occurred_at = _timestamp(row["Date"], row["Time"], source_line)
            override = overrides.get(source_line)
            if override is None:
                economic_type, committed = _classification(row["Type"], amount, source_line)
                category = row["Category"] or None
            else:
                used_overrides.add(source_line)
                economic_type, committed = override.economic_type, override.committed
                category = override.category
            transaction_id = "monzo-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
            transactions.append(CanonicalTransaction(
                transaction_id=transaction_id,
                occurred_at=occurred_at, amount_minor=amount, currency="GBP", status="settled",
                economic_type=economic_type, committed=committed, category=category,
            ))
            record_bytes = b"".join(physical_lines[source_line - 1:reader.reader.line_num])
            record_provenance.append(MonzoRecordProvenance(
                transaction_id=transaction_id, source_line=source_line,
                source_hash=hashlib.sha256(record_bytes).hexdigest(),
            ))
    except csv.Error:
        raise _error("invalid_csv", source_line) from None
    unused = overrides.keys() - used_overrides
    if unused:
        raise _error("invalid_overrides", min(unused))
    return ParsedMonzoExport(
        source_sha256=hashlib.sha256(source).hexdigest(),
        line_hashes=tuple(hashlib.sha256(line).hexdigest()
                          for line in physical_lines),
        profile_name=MONZO_PROFILE_VERSION, transactions=tuple(transactions),
        record_provenance=tuple(record_provenance),
    )
