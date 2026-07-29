from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass(frozen=True, slots=True)
class TimeWindow:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.start.utcoffset() is None:
            raise ValueError("research window start must be timezone-aware")
        if self.end.tzinfo is None or self.end.utcoffset() is None:
            raise ValueError("research window end must be timezone-aware")
        if self.start >= self.end:
            raise ValueError("research window start must be before end")


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    number: int
    train: TimeWindow
    validation: TimeWindow
    test: TimeWindow


@dataclass(frozen=True, slots=True)
class WalkForwardPlan:
    folds: tuple[WalkForwardFold, ...]
    final_holdout: TimeWindow
    plan_hash: str


def walk_forward_plan_payload(plan: WalkForwardPlan) -> dict[str, Any]:
    return {
        "folds": [
            {
                "number": fold.number,
                "train": [_timestamp(fold.train.start), _timestamp(fold.train.end)],
                "validation": [
                    _timestamp(fold.validation.start),
                    _timestamp(fold.validation.end),
                ],
                "test": [_timestamp(fold.test.start), _timestamp(fold.test.end)],
            }
            for fold in plan.folds
        ],
        "final_holdout": [
            _timestamp(plan.final_holdout.start),
            _timestamp(plan.final_holdout.end),
        ],
    }


def walk_forward_plan_from_payload(payload: Mapping[str, object]) -> WalkForwardPlan:
    raw_folds = payload.get("folds")
    raw_holdout = payload.get("final_holdout")
    if not isinstance(raw_folds, list) or not raw_folds:
        raise ValueError("research plan folds are invalid")
    folds: list[WalkForwardFold] = []
    for raw_fold in raw_folds:
        if not isinstance(raw_fold, Mapping):
            raise ValueError("research plan fold is invalid")
        number = raw_fold.get("number")
        if not isinstance(number, int) or number != len(folds) + 1:
            raise ValueError("research plan fold numbering is invalid")
        folds.append(
            WalkForwardFold(
                number=number,
                train=_window_from_payload(raw_fold.get("train")),
                validation=_window_from_payload(raw_fold.get("validation")),
                test=_window_from_payload(raw_fold.get("test")),
            )
        )
    holdout = _window_from_payload(raw_holdout)
    provisional = WalkForwardPlan(folds=tuple(folds), final_holdout=holdout, plan_hash="")
    canonical = json.dumps(
        walk_forward_plan_payload(provisional),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return WalkForwardPlan(
        folds=tuple(folds),
        final_holdout=holdout,
        plan_hash=hashlib.sha256(canonical).hexdigest(),
    )


def _window_from_payload(value: object) -> TimeWindow:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not all(isinstance(item, str) for item in value)
    ):
        raise ValueError("research plan window is invalid")
    start, end = value
    assert isinstance(start, str)
    assert isinstance(end, str)
    try:
        parsed_start = datetime.fromisoformat(start)
        parsed_end = datetime.fromisoformat(end)
    except ValueError as exc:
        raise ValueError("research plan window timestamp is invalid") from exc
    return TimeWindow(start=parsed_start, end=parsed_end)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def build_expanding_walk_forward_plan(
    *,
    dataset_start: datetime,
    dataset_end: datetime,
    minimum_train: timedelta,
    validation: timedelta,
    test: timedelta,
    step: timedelta,
    final_holdout: timedelta,
) -> WalkForwardPlan:
    TimeWindow(dataset_start, dataset_end)
    durations = {
        "minimum_train": minimum_train,
        "validation": validation,
        "test": test,
        "step": step,
        "final_holdout": final_holdout,
    }
    invalid = [name for name, value in durations.items() if value <= timedelta(0)]
    if invalid:
        raise ValueError("walk-forward durations must be positive: " + ", ".join(invalid))
    if step < test:
        raise ValueError("walk-forward step must not create overlapping test windows")

    holdout_start = dataset_end - final_holdout
    holdout_window = TimeWindow(holdout_start, dataset_end)
    train_end = dataset_start + minimum_train
    folds: list[WalkForwardFold] = []
    while True:
        validation_end = train_end + validation
        test_end = validation_end + test
        if test_end > holdout_start:
            break
        folds.append(
            WalkForwardFold(
                number=len(folds) + 1,
                train=TimeWindow(dataset_start, train_end),
                validation=TimeWindow(train_end, validation_end),
                test=TimeWindow(validation_end, test_end),
            )
        )
        train_end += step
    if not folds:
        raise ValueError("dataset is too short for one walk-forward fold plus holdout")

    provisional = WalkForwardPlan(folds=tuple(folds), final_holdout=holdout_window, plan_hash="")
    payload = walk_forward_plan_payload(provisional)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    return WalkForwardPlan(
        folds=tuple(folds),
        final_holdout=holdout_window,
        plan_hash=hashlib.sha256(canonical).hexdigest(),
    )
