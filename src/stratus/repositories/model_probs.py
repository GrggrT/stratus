from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import ModelProb
from stratus.services.clob_evidence import LEGACY_MARKET_PROTOCOL

MODEL_PROBABILITY_WRITE_LOCK_ID = 7_418_303_001


@dataclass(frozen=True, slots=True)
class ModelProbabilityValue:
    market_id: int
    ts: datetime
    prob: Decimal
    sigma: Decimal
    horizon_h: Decimal
    components: dict[str, Any]
    market_snapshot_id: int | None = None
    market_anchor_protocol: str = LEGACY_MARKET_PROTOCOL


async def upsert_model_probs(
    session: AsyncSession,
    values: list[ModelProbabilityValue],
    *,
    batch_size: int = 500,
) -> int:
    if not values:
        return 0
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": MODEL_PROBABILITY_WRITE_LOCK_ID},
    )
    ordered = sorted(values, key=lambda value: (value.market_id, value.ts))
    for start in range(0, len(ordered), batch_size):
        rows = [asdict(value) for value in ordered[start : start + batch_size]]
        statement = insert(ModelProb).values(rows)
        statement = statement.on_conflict_do_update(
            constraint="uq_model_probs_market_time",
            set_={
                "prob": statement.excluded.prob,
                "sigma": statement.excluded.sigma,
                "horizon_h": statement.excluded.horizon_h,
                "components": statement.excluded.components,
                "market_snapshot_id": statement.excluded.market_snapshot_id,
                "market_anchor_protocol": statement.excluded.market_anchor_protocol,
            },
        )
        await session.execute(statement)
    return len(values)
