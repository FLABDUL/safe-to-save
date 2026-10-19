"""All rows and identifying values in this module are wholly synthetic."""

import csv
import hashlib
import io
import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path

import pytest

from safe_to_save.contracts import EconomicType
from safe_to_save.monzo_export import (
    MONZO_CSV_V1_HEADERS,
    MonzoOverride,
    load_overrides,
    parse_monzo_export,
)

FIXTURES = Path(__file__).parents[1] / "fixtures/monzo_export"


def synthetic_row(**changes):
    row = dict.fromkeys(MONZO_CSV_V1_HEADERS, "")
    row.update({"Transaction ID": "synthetic-id", "Date": "15/07/2026",
                "Time": "12:30:00", "Type": "Card payment", "Name": "Synthetic Shop",
                "Category": "synthetic", "Amount": "-12.34", "Currency": "GBP",
                "Notes and #tags": "SYNTHETIC ONLY"})
    row.update(changes)
    return row


def write_csv(tmp_path, rows=None, headers=MONZO_CSV_V1_HEADERS, *, ending="\n",
              bom=False, quoting=csv.QUOTE_MINIMAL):
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=headers, lineterminator=ending, quoting=quoting)
    writer.writeheader()
    writer.writerows([synthetic_row()] if rows is None else rows)
    path = tmp_path / "synthetic.csv"
    path.write_bytes(("\ufeff" if bom else "").encode() + text.getvalue().encode())
    return path


def assert_safe_error(path, reason, line=2, overrides=None):
    with pytest.raises(ValueError) as caught:
        parse_monzo_export(path, overrides or {})
    message = str(caught.value)
    assert reason in message
    assert f"line={line}" in message
    for private in ("Synthetic Shop", "-12.34", "synthetic-id", str(path)):
        assert private not in message
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("change", ["reorder", "missing", "extra", "duplicate", "case"])
def test_exact_header_is_required(tmp_path, change):
    headers = list(MONZO_CSV_V1_HEADERS)
    if change == "reorder":
        headers[0], headers[1] = headers[1], headers[0]
    elif change == "missing":
        headers.pop()
    elif change == "extra":
        headers.append("Account ID")
    elif change == "duplicate":
        headers[-1] = headers[0]
    else:
        headers[0] = "transaction id"
    assert_safe_error(write_csv(tmp_path, [], headers), "unsupported_header", 1)


@pytest.mark.parametrize(("value", "expected"), [
    ("-12.34", -1234), ("12.34", 1234), ("0.00", 0), ("1.2", 120),
    ("12345678901234567890.12", 1234567890123456789012),
])
def test_amount_uses_decimal_to_create_signed_pence(tmp_path, value, expected):
    parsed = parse_monzo_export(write_csv(tmp_path, [synthetic_row(Amount=value)]), {})
    assert parsed.transactions[0].amount_minor == expected
    assert type(parsed.transactions[0].amount_minor) is int


@pytest.mark.parametrize("value", ["NaN", "Infinity", "1.001", "1e2", "", " 1.00"])
def test_invalid_money_is_rejected_without_rounding(tmp_path, value):
    assert_safe_error(write_csv(tmp_path, [synthetic_row(Amount=value)]), "invalid_amount")


def test_stable_ids_and_line_hashes_are_deterministic(tmp_path):
    path = write_csv(tmp_path)
    first = parse_monzo_export(path, {})
    assert parse_monzo_export(path, {}) == first
    raw = path.read_bytes()
    assert first.source_sha256 == hashlib.sha256(raw).hexdigest()
    assert first.line_hashes == tuple(hashlib.sha256(line).hexdigest()
                                      for line in raw.splitlines(keepends=True))
    assert first.profile_name == "monzo-csv-v1"
    assert "synthetic-id" not in first.transactions[0].transaction_id
    assert first.transactions[0].transaction_id == (
        "monzo-" + hashlib.sha256(b"synthetic-id").hexdigest())
    path.write_bytes(raw.replace(b"Synthetic Shop", b"Synthetic Cafe"))
    second = parse_monzo_export(path, {})
    assert second.source_sha256 != first.source_sha256
    assert second.transactions[0].transaction_id == first.transactions[0].transaction_id


def test_non_gbp_rows_are_rejected(tmp_path):
    assert_safe_error(write_csv(tmp_path, [synthetic_row(Currency="USD")]),
                      "unsupported_currency")


def test_account_scope_is_explicitly_unverifiable(tmp_path):
    assert parse_monzo_export(write_csv(tmp_path), {}).account_scope_verifiable is False


@pytest.mark.parametrize(("ending", "bom", "quoting"), [
    ("\n", False, csv.QUOTE_MINIMAL), ("\r\n", True, csv.QUOTE_ALL),
])
def test_csv_dialect_variants_parse(tmp_path, ending, bom, quoting):
    row = synthetic_row(Name='Synthetic, "Shop"', **{"Notes and #tags": "Synthetic\nnote"})
    parsed = parse_monzo_export(write_csv(tmp_path, [row], ending=ending, bom=bom,
                                         quoting=quoting), {})
    assert parsed.transactions[0].amount_minor == -1234
    assert parsed.transactions[0].occurred_at == datetime(2026, 7, 15, 11, 30, tzinfo=UTC)
    assert len(parsed.line_hashes) == 3


def test_refund_remains_positive_expense():
    parsed = parse_monzo_export(FIXTURES / "minimal-synthetic.csv", {})
    refund = parsed.transactions[1]
    assert refund.amount_minor == 500
    assert refund.economic_type == EconomicType.EXPENSE


def test_pot_movement_is_savings_transfer():
    parsed = parse_monzo_export(FIXTURES / "minimal-synthetic.csv", {})
    assert parsed.transactions[2].economic_type == EconomicType.SAVINGS_TRANSFER


@pytest.mark.parametrize("type_label", ["Direct Debit", "Flex"])
@pytest.mark.parametrize("amount", ["-12.34", "12.34"])
def test_direct_debit_and_flex_are_committed_expenses(tmp_path, type_label, amount):
    parsed = parse_monzo_export(write_csv(tmp_path, [synthetic_row(Type=type_label,
                                                                Amount=amount)]), {})
    assert parsed.transactions[0].economic_type == EconomicType.EXPENSE
    assert parsed.transactions[0].committed is True


def test_positive_bacs_direct_credit_is_income(tmp_path):
    parsed = parse_monzo_export(write_csv(tmp_path, [synthetic_row(
        Type="Bacs (Direct Credit)", Amount="12.34")]), {})
    assert parsed.transactions[0].economic_type == EconomicType.INCOME
    assert parsed.transactions[0].committed is False


@pytest.mark.parametrize("type_label", [
    "monzo_paid", "Faster payment", "Bacs (Direct Credit)", "Bank transfer",
    "Monzo-to-Monzo", "international-payments", "overdraft", "synthetic-unknown",
])
def test_unknown_type_requires_line_number_override(tmp_path, type_label):
    path = write_csv(tmp_path, [synthetic_row(Type=type_label)])
    assert_safe_error(path, "override_required")
    parsed = parse_monzo_export(path, {2: MonzoOverride(
        source_line=2, economic_type="internal_transfer", committed=False,
        category="synthetic override")})
    assert parsed.transactions[0].economic_type == EconomicType.INTERNAL_TRANSFER
    assert parsed.transactions[0].category == "synthetic override"


@pytest.mark.parametrize(("date", "time", "reason"), [
    ("25/10/2026", "01:30:00", "ambiguous_timestamp"),
    ("29/03/2026", "01:30:00", "nonexistent_timestamp"),
    ("31/02/2026", "12:00:00", "invalid_timestamp"),
    ("15/07/2026", "12:30:00+01:00", "invalid_timestamp"),
])
def test_ambiguous_or_nonexistent_timestamp_is_rejected(tmp_path, date, time, reason):
    assert_safe_error(write_csv(tmp_path, [synthetic_row(Date=date, Time=time)]), reason)


def test_duplicate_source_identity_is_rejected(tmp_path):
    path = write_csv(tmp_path, [synthetic_row(), synthetic_row()])
    assert_safe_error(path, "duplicate_identity", 3)


def test_blank_source_identity_is_rejected(tmp_path):
    assert_safe_error(write_csv(tmp_path, [synthetic_row(**{"Transaction ID": " "})]),
                      "blank_identity")


def test_header_only_profile_is_valid():
    assert parse_monzo_export(FIXTURES / "header-only.csv", {}).transactions == ()


def test_local_foreign_currency_does_not_change_gbp_posting(tmp_path):
    row = synthetic_row(**{"Local amount": "-16.00", "Local currency": "USD"})
    assert parse_monzo_export(write_csv(tmp_path, [row]), {}).transactions[0].amount_minor == -1234


def test_override_loader_accepts_only_line_number_records(tmp_path):
    assert load_overrides(None) == {}
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps([{"source_line": 2, "economic_type": "income", "committed": False}]))
    assert load_overrides(path) == {2: MonzoOverride(2, "income", False)}


@pytest.mark.parametrize("records", [
    {}, [{"source_line": 2, "economic_type": "income"}],
    [{"source_line": True, "economic_type": "income", "committed": False}],
    [{"source_line": 2, "economic_type": "income", "committed": "false"}],
    [{"source_line": 2, "economic_type": "income", "committed": False,
      "Transaction ID": "synthetic-private"}],
    [{"source_line": 2, "economic_type": "income", "committed": False}] * 2,
])
def test_invalid_override_files_are_sanitised(tmp_path, records):
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps(records))
    with pytest.raises(ValueError, match="invalid_overrides") as caught:
        load_overrides(path)
    assert "synthetic-private" not in str(caught.value)
    assert str(path) not in str(caught.value)


def test_unused_and_mismatched_overrides_are_rejected(tmp_path):
    path = write_csv(tmp_path)
    assert_safe_error(path, "invalid_overrides", 3,
                      {3: MonzoOverride(3, "income", False)})
    assert_safe_error(path, "invalid_overrides", 2,
                      {2: MonzoOverride(3, "income", False)})


@pytest.mark.parametrize("raw", [b"\xff", b"", b"header\n", b'"unterminated'])
def test_malformed_input_is_sanitised(tmp_path, raw):
    path = tmp_path / "synthetic.csv"
    path.write_bytes(raw)
    with pytest.raises(ValueError) as caught:
        parse_monzo_export(path, {})
    assert str(path) not in str(caught.value)


def test_missing_source_is_sanitised(tmp_path):
    with pytest.raises(ValueError, match="source_unreadable") as caught:
        parse_monzo_export(tmp_path / "private-name.csv", {})
    assert "private-name" not in str(caught.value)


@pytest.mark.parametrize("mutation", ["short", "long", "blank", "trailing_blank", "quote"])
def test_malformed_record_errors_use_physical_start_line(tmp_path, mutation):
    path = write_csv(tmp_path)
    header, row = path.read_bytes().splitlines(keepends=True)
    if mutation == "short":
        raw = header + b"synthetic-id,15/07/2026\n"
    elif mutation == "long":
        raw = header + row.rstrip(b"\n") + b",extra\n"
    elif mutation == "blank":
        raw = header + b"\n" + row
    elif mutation == "trailing_blank":
        raw = header + row + b"\n"
    else:
        raw = header + b'"unterminated'
    path.write_bytes(raw)
    assert_safe_error(path, "invalid_csv" if mutation == "quote" else "invalid_row",
                      3 if mutation == "trailing_blank" else 2)


def test_override_after_multiline_record_uses_physical_start_line(tmp_path):
    first = synthetic_row(**{"Notes and #tags": "Synthetic\nnote"})
    second = synthetic_row(**{"Transaction ID": "synthetic-second", "Type": "synthetic-unknown"})
    path = write_csv(tmp_path, [first, second])
    override = MonzoOverride(4, "income", False)
    parsed = parse_monzo_export(path, {4: override})
    assert parsed.transactions[1].economic_type == EconomicType.INCOME


def test_conflicting_duplicate_identity_is_rejected(tmp_path):
    path = write_csv(tmp_path, [synthetic_row(), synthetic_row(Amount="1.00")])
    assert_safe_error(path, "duplicate_identity", 3)


@pytest.mark.parametrize(("ending", "bom", "final_ending"), [
    ("\n", False, True), ("\r\n", True, True), ("\r\n", False, False),
])
def test_record_provenance_preserves_multiline_exact_bytes_after_sorting(
    tmp_path, ending, bom, final_ending,
):
    rows = [
        synthetic_row(Date="16/07/2026", **{"Notes and #tags": "Synthetic\nnote"}),
        synthetic_row(Date="15/07/2026", **{"Transaction ID": "synthetic-second"}),
    ]
    path = write_csv(tmp_path, rows, ending=ending, bom=bom)
    if not final_ending:
        path.write_bytes(path.read_bytes().removesuffix(ending.encode()))
    parsed = parse_monzo_export(path, {})
    raw_lines = path.read_bytes().splitlines(keepends=True)
    by_id = {record.transaction_id: record for record in parsed.record_provenance}
    sorted_transactions = sorted(parsed.transactions, key=lambda row: row.occurred_at)
    sorted_records = [by_id[row.transaction_id] for row in sorted_transactions]
    assert [record.source_line for record in sorted_records] == [4, 2]
    assert [record.source_hash for record in sorted_records] == [
        hashlib.sha256(raw_lines[3]).hexdigest(),
        hashlib.sha256(b"".join(raw_lines[1:3])).hexdigest(),
    ]
    assert tuple(record.transaction_id for record in parsed.record_provenance) == tuple(
        row.transaction_id for row in parsed.transactions)
    assert parsed == parse_monzo_export(path, {})
    assert parsed.line_hashes == tuple(hashlib.sha256(line).hexdigest() for line in raw_lines)
    for private in ("synthetic-id", "Synthetic Shop", "Synthetic\nnote"):
        assert private not in repr(parsed.record_provenance)
    with pytest.raises(FrozenInstanceError):
        sorted_records[0].source_line = 99


def test_header_only_export_has_no_record_provenance():
    assert parse_monzo_export(FIXTURES / "header-only.csv", {}).record_provenance == ()
