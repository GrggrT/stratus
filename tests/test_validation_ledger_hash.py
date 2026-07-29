from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum

import pytest

from stratus.repositories.validation_ledger import _canonical_json, _record_hash


class _ExampleEnum(Enum):
    VALUE = "enum-value"


def test_canonical_json_expands_nested_floats_and_preserves_string_types() -> None:
    value: dict[str, object] = {
        "z": [
            3.62e-05,
            {"negative_zero": -0.0, "large": 1.25e20},
        ],
        "a": {
            "timestamp": datetime(2026, 7, 15, 4, 5, 6, tzinfo=UTC),
            "enum": _ExampleEnum.VALUE,
            "decimal": Decimal("1.2300"),
            "date": date(2026, 7, 15),
        },
    }
    expected = (
        '{"a":{"date":"2026-07-15","decimal":"1.23","enum":"enum-value",'
        '"timestamp":"2026-07-15T04:05:06+00:00"},'
        '"z":[0.0000362,{"large":125000000000000000000,"negative_zero":0.0}]}'
    )

    assert _canonical_json(value) == expected
    assert _record_hash(value) == hashlib.sha256(expected.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_record_hash_rejects_nested_nonfinite_float(value: float) -> None:
    with pytest.raises(ValueError, match="non-finite float"):
        _record_hash({"outer": [{"value": value}]})
