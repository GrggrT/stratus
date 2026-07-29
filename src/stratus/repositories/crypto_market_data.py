from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.collectors.binance_archive import BinanceAggregateTradeData
from stratus.collectors.binance_spot import (
    BinanceBookTickerData,
    BinanceCandleData,
    BinanceSymbolInfo,
)
from stratus.db.models import (
    CryptoAggregateTrade,
    CryptoArchiveImport,
    CryptoCandle,
    CryptoDataGap,
    CryptoMarketSnapshot,
    CryptoSymbol,
    TradingVenue,
)
from stratus.db.session import dml_rowcount


async def crypto_symbols_need_refresh(
    session: AsyncSession,
    symbols: tuple[str, ...],
    *,
    now: datetime,
    max_age: timedelta,
) -> bool:
    rows = (
        await session.execute(
            select(CryptoSymbol.symbol, CryptoSymbol.fetched_at).where(
                CryptoSymbol.venue == TradingVenue.BINANCE_SPOT,
                CryptoSymbol.symbol.in_(symbols),
            )
        )
    ).all()
    if {row.symbol for row in rows} != set(symbols):
        return True
    cutoff = now - max_age
    return any(row.fetched_at <= cutoff for row in rows)


async def crypto_symbol_ids(session: AsyncSession, symbols: tuple[str, ...]) -> dict[str, int]:
    rows = (
        await session.execute(
            select(CryptoSymbol.symbol, CryptoSymbol.id).where(
                CryptoSymbol.venue == TradingVenue.BINANCE_SPOT,
                CryptoSymbol.symbol.in_(symbols),
            )
        )
    ).all()
    return {row.symbol: row.id for row in rows}


async def upsert_crypto_symbols(
    session: AsyncSession, values: list[BinanceSymbolInfo]
) -> dict[str, int]:
    if not values:
        return {}
    rows = [
        {
            "venue": TradingVenue.BINANCE_SPOT,
            "symbol": value.symbol,
            "base_asset": value.base_asset,
            "quote_asset": value.quote_asset,
            "status": value.status,
            "enabled": value.status == "TRADING" and value.spot_trading_allowed,
            "price_tick": value.price_tick,
            "quantity_step": value.quantity_step,
            "minimum_quantity": value.minimum_quantity,
            "minimum_notional": value.minimum_notional,
            "maximum_notional": value.maximum_notional,
            "supports_oco": value.supports_oco,
            "supports_oto": value.supports_oto,
            "filters": value.filters,
            "filter_hash": value.filter_hash,
            "fetched_at": value.fetched_at,
        }
        for value in values
    ]
    statement = insert(CryptoSymbol).values(rows)
    statement = statement.on_conflict_do_update(
        constraint="uq_crypto_symbols_venue_symbol",
        set_={
            "base_asset": statement.excluded.base_asset,
            "quote_asset": statement.excluded.quote_asset,
            "status": statement.excluded.status,
            # A refresh may force a symbol off, but never silently undo an
            # operator's prior disable when Binance later reports TRADING.
            "enabled": CryptoSymbol.enabled & statement.excluded.enabled,
            "price_tick": statement.excluded.price_tick,
            "quantity_step": statement.excluded.quantity_step,
            "minimum_quantity": statement.excluded.minimum_quantity,
            "minimum_notional": statement.excluded.minimum_notional,
            "maximum_notional": statement.excluded.maximum_notional,
            "supports_oco": statement.excluded.supports_oco,
            "supports_oto": statement.excluded.supports_oto,
            "filters": statement.excluded.filters,
            "filter_hash": statement.excluded.filter_hash,
            "fetched_at": statement.excluded.fetched_at,
        },
    )
    result = await session.execute(statement.returning(CryptoSymbol.symbol, CryptoSymbol.id))
    return {row.symbol: row.id for row in result.all()}


def _require_symbol_id(symbol_ids: dict[str, int], symbol: str) -> int:
    try:
        return symbol_ids[symbol]
    except KeyError as exc:
        raise ValueError(f"crypto symbol is not registered: {symbol}") from exc


async def upsert_crypto_candles(
    session: AsyncSession,
    values: list[BinanceCandleData],
    symbol_ids: dict[str, int],
) -> int:
    if not values:
        return 0
    rows = [
        {
            "symbol_id": _require_symbol_id(symbol_ids, value.symbol),
            "interval": value.interval,
            "open_time": value.open_time,
            "close_time": value.close_time,
            "open_price": value.open_price,
            "high_price": value.high_price,
            "low_price": value.low_price,
            "close_price": value.close_price,
            "base_volume": value.base_volume,
            "quote_volume": value.quote_volume,
            "trade_count": value.trade_count,
            "taker_buy_base_volume": value.taker_buy_base_volume,
            "taker_buy_quote_volume": value.taker_buy_quote_volume,
            "closed": value.closed,
            "source": value.source,
            "received_at": value.received_at,
        }
        for value in values
    ]
    statement = insert(CryptoCandle).values(rows)
    statement = statement.on_conflict_do_update(
        constraint="uq_crypto_candles_identity",
        set_={
            "close_time": statement.excluded.close_time,
            "open_price": statement.excluded.open_price,
            "high_price": statement.excluded.high_price,
            "low_price": statement.excluded.low_price,
            "close_price": statement.excluded.close_price,
            "base_volume": statement.excluded.base_volume,
            "quote_volume": statement.excluded.quote_volume,
            "trade_count": statement.excluded.trade_count,
            "taker_buy_base_volume": statement.excluded.taker_buy_base_volume,
            "taker_buy_quote_volume": statement.excluded.taker_buy_quote_volume,
            "closed": statement.excluded.closed,
            "source": statement.excluded.source,
            "received_at": statement.excluded.received_at,
        },
    )
    await session.execute(statement)
    return len(rows)


@dataclass(frozen=True, slots=True)
class CryptoAggregateTradeDayBounds:
    first_aggregate_trade_id: int
    last_aggregate_trade_id: int
    first_trade_id: int
    last_trade_id: int


async def upsert_crypto_aggregate_trades(
    session: AsyncSession,
    values: list[BinanceAggregateTradeData],
    symbol_ids: dict[str, int],
    *,
    batch_size: int = 2000,
) -> int:
    if not values:
        return 0
    if batch_size <= 0:
        raise ValueError("aggregate trade insert batch_size must be positive")
    if batch_size > 2500:
        raise ValueError("aggregate trade insert batch_size exceeds asyncpg bind limit")
    written = 0
    for offset in range(0, len(values), batch_size):
        rows = [
            {
                "symbol_id": _require_symbol_id(symbol_ids, value.symbol),
                "aggregate_trade_id": value.aggregate_trade_id,
                "price": value.price,
                "quantity": value.quantity,
                "first_trade_id": value.first_trade_id,
                "last_trade_id": value.last_trade_id,
                "trade_time": value.trade_time,
                "buyer_is_maker": value.buyer_is_maker,
                "best_price_match": value.best_price_match,
                "source": value.source,
                "received_at": value.received_at,
            }
            for value in values[offset : offset + batch_size]
        ]
        statement = insert(CryptoAggregateTrade).values(rows)
        await session.execute(
            statement.on_conflict_do_update(
                constraint="uq_crypto_aggregate_trades_identity",
                set_={
                    "price": statement.excluded.price,
                    "quantity": statement.excluded.quantity,
                    "first_trade_id": statement.excluded.first_trade_id,
                    "last_trade_id": statement.excluded.last_trade_id,
                    "trade_time": statement.excluded.trade_time,
                    "buyer_is_maker": statement.excluded.buyer_is_maker,
                    "best_price_match": statement.excluded.best_price_match,
                    "source": statement.excluded.source,
                    "received_at": statement.excluded.received_at,
                },
            )
        )
        written += len(rows)
    return written


async def crypto_aggregate_trade_day_bounds(
    session: AsyncSession,
    *,
    symbol_id: int,
    trading_date: date,
) -> CryptoAggregateTradeDayBounds | None:
    day_start = datetime.combine(trading_date, time.min, tzinfo=UTC)
    day_end = day_start + timedelta(days=1)
    columns = (
        CryptoAggregateTrade.aggregate_trade_id,
        CryptoAggregateTrade.first_trade_id,
        CryptoAggregateTrade.last_trade_id,
    )
    base = select(*columns).where(
        CryptoAggregateTrade.symbol_id == symbol_id,
        CryptoAggregateTrade.trade_time >= day_start,
        CryptoAggregateTrade.trade_time < day_end,
    )
    first = (
        await session.execute(base.order_by(CryptoAggregateTrade.aggregate_trade_id.asc()).limit(1))
    ).one_or_none()
    if first is None:
        return None
    last = (
        await session.execute(
            base.order_by(CryptoAggregateTrade.aggregate_trade_id.desc()).limit(1)
        )
    ).one()
    return CryptoAggregateTradeDayBounds(
        first_aggregate_trade_id=first.aggregate_trade_id,
        last_aggregate_trade_id=last.aggregate_trade_id,
        first_trade_id=first.first_trade_id,
        last_trade_id=last.last_trade_id,
    )


async def completed_crypto_archive_imports(
    session: AsyncSession,
    symbol_ids: dict[str, int],
    *,
    interval: str,
    start_date: date,
    end_date: date,
) -> set[tuple[str, date]]:
    if not symbol_ids:
        return set()
    rows = (
        await session.execute(
            select(
                CryptoArchiveImport.symbol_id,
                CryptoArchiveImport.trading_date,
            ).where(
                CryptoArchiveImport.symbol_id.in_(symbol_ids.values()),
                CryptoArchiveImport.interval == interval,
                CryptoArchiveImport.trading_date >= start_date,
                CryptoArchiveImport.trading_date <= end_date,
                CryptoArchiveImport.status == "imported",
            )
        )
    ).all()
    by_id = {symbol_id: symbol for symbol, symbol_id in symbol_ids.items()}
    return {(by_id[row.symbol_id], row.trading_date) for row in rows}


async def record_crypto_archive_import(
    session: AsyncSession,
    *,
    symbol_id: int,
    interval: str,
    trading_date: date,
    source_url: str,
    checksum_sha256: str | None,
    archive_bytes: int,
    row_count: int,
    status: str,
    error: str | None,
    imported_at: datetime,
) -> None:
    statement = insert(CryptoArchiveImport).values(
        symbol_id=symbol_id,
        interval=interval,
        trading_date=trading_date,
        source_url=source_url,
        checksum_sha256=checksum_sha256,
        archive_bytes=archive_bytes,
        row_count=row_count,
        status=status,
        error=error,
        imported_at=imported_at,
    )
    await session.execute(
        statement.on_conflict_do_update(
            constraint="uq_crypto_archive_imports_identity",
            set_={
                "source_url": statement.excluded.source_url,
                "checksum_sha256": statement.excluded.checksum_sha256,
                "archive_bytes": statement.excluded.archive_bytes,
                "row_count": statement.excluded.row_count,
                "status": statement.excluded.status,
                "error": statement.excluded.error,
                "imported_at": statement.excluded.imported_at,
            },
        )
    )


async def record_crypto_archive_stream_verification(
    session: AsyncSession,
    *,
    symbol_id: int,
    interval: str,
    trading_date: date,
    source_url: str,
    checksum_sha256: str,
    archive_bytes: int,
    row_count: int,
    verified_at: datetime,
) -> None:
    existing = await session.scalar(
        select(CryptoArchiveImport)
        .where(
            CryptoArchiveImport.symbol_id == symbol_id,
            CryptoArchiveImport.interval == interval,
            CryptoArchiveImport.trading_date == trading_date,
        )
        .with_for_update()
    )
    if existing is not None:
        identity = (
            existing.checksum_sha256,
            existing.archive_bytes,
            existing.row_count,
        )
        expected = (checksum_sha256, archive_bytes, row_count)
        if identity != expected:
            raise ValueError(
                "verified Binance archive conflicts with persisted provenance: "
                f"{interval}:{trading_date}"
            )
        if existing.status == "imported":
            return
    await record_crypto_archive_import(
        session,
        symbol_id=symbol_id,
        interval=interval,
        trading_date=trading_date,
        source_url=source_url,
        checksum_sha256=checksum_sha256,
        archive_bytes=archive_bytes,
        row_count=row_count,
        status="stream_verified",
        error=None,
        imported_at=verified_at,
    )


async def insert_crypto_market_snapshots(
    session: AsyncSession,
    values: list[BinanceBookTickerData],
    symbol_ids: dict[str, int],
) -> int:
    if not values:
        return 0
    rows = [
        {
            "symbol_id": _require_symbol_id(symbol_ids, value.symbol),
            "snapshot_type": (
                "book_ticker_ws"
                if value.source == "binance_ws"
                else (
                    "depth20_rest" if value.source == "binance_depth_rest" else "book_ticker_rest"
                )
            ),
            "update_id": value.update_id,
            "event_time": value.event_time,
            "received_at": value.received_at,
            "best_bid": value.best_bid,
            "best_bid_quantity": value.best_bid_quantity,
            "best_ask": value.best_ask,
            "best_ask_quantity": value.best_ask_quantity,
            "spread_bps": value.spread_bps,
            "bid_depth_quote": value.bid_depth_quote,
            "ask_depth_quote": value.ask_depth_quote,
            "imbalance": value.imbalance,
            "depth_valid": value.depth_valid,
        }
        for value in values
    ]
    statement = (
        insert(CryptoMarketSnapshot)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_crypto_market_snapshots_identity")
        .returning(CryptoMarketSnapshot.id)
    )
    inserted = (await session.execute(statement)).scalars().all()
    return len(inserted)


async def latest_candle_open_times(
    session: AsyncSession,
    symbol_ids: dict[str, int],
    *,
    interval: str,
) -> dict[str, datetime]:
    if not symbol_ids:
        return {}
    rows = (
        await session.execute(
            select(CryptoCandle.symbol_id, CryptoCandle.open_time)
            .where(
                CryptoCandle.symbol_id.in_(symbol_ids.values()),
                CryptoCandle.interval == interval,
                CryptoCandle.closed.is_(True),
            )
            .distinct(CryptoCandle.symbol_id)
            .order_by(CryptoCandle.symbol_id, CryptoCandle.open_time.desc())
        )
    ).all()
    by_id = {symbol_id: symbol for symbol, symbol_id in symbol_ids.items()}
    return {by_id[row.symbol_id]: row.open_time for row in rows}


async def latest_book_ticker_update_ids(
    session: AsyncSession, symbol_ids: dict[str, int]
) -> dict[str, int]:
    if not symbol_ids:
        return {}
    rows = (
        await session.execute(
            select(CryptoMarketSnapshot.symbol_id, CryptoMarketSnapshot.update_id)
            .where(
                CryptoMarketSnapshot.symbol_id.in_(symbol_ids.values()),
                CryptoMarketSnapshot.snapshot_type == "book_ticker_ws",
            )
            .distinct(CryptoMarketSnapshot.symbol_id)
            .order_by(
                CryptoMarketSnapshot.symbol_id,
                CryptoMarketSnapshot.event_time.desc(),
            )
        )
    ).all()
    by_id = {symbol_id: symbol for symbol, symbol_id in symbol_ids.items()}
    return {by_id[row.symbol_id]: row.update_id for row in rows}


async def open_crypto_data_gap(
    session: AsyncSession,
    *,
    stream: str,
    detected_at: datetime,
    symbol_id: int | None = None,
    expected_cursor: str | None = None,
    actual_cursor: str | None = None,
    details: dict[str, Any] | None = None,
) -> int:
    statement = select(CryptoDataGap).where(
        CryptoDataGap.stream == stream,
        CryptoDataGap.status == "open",
    )
    if symbol_id is None:
        statement = statement.where(CryptoDataGap.symbol_id.is_(None))
    else:
        statement = statement.where(CryptoDataGap.symbol_id == symbol_id)
    existing = await session.scalar(
        statement.order_by(CryptoDataGap.detected_at.desc()).limit(1).with_for_update()
    )
    if existing is not None:
        existing.expected_cursor = expected_cursor
        existing.actual_cursor = actual_cursor
        existing.details = details or {}
        return existing.id
    gap = CryptoDataGap(
        symbol_id=symbol_id,
        stream=stream,
        detected_at=detected_at,
        expected_cursor=expected_cursor,
        actual_cursor=actual_cursor,
        status="open",
        details=details or {},
    )
    session.add(gap)
    await session.flush()
    return gap.id


async def resolve_crypto_data_gaps(
    session: AsyncSession,
    *,
    streams: tuple[str, ...],
    resolved_at: datetime,
) -> int:
    if not streams:
        return 0
    result = await session.execute(
        update(CryptoDataGap)
        .where(
            CryptoDataGap.stream.in_(streams),
            CryptoDataGap.status == "open",
        )
        .values(status="resolved", resolved_at=resolved_at)
    )
    return dml_rowcount(result)


async def resolve_crypto_data_gap(
    session: AsyncSession,
    *,
    stream: str,
    symbol_id: int | None,
    expected_cursor: str | None,
    resolved_at: datetime,
) -> int:
    statement = update(CryptoDataGap).where(
        CryptoDataGap.stream == stream,
        CryptoDataGap.status == "open",
        CryptoDataGap.expected_cursor == expected_cursor,
    )
    if symbol_id is None:
        statement = statement.where(CryptoDataGap.symbol_id.is_(None))
    else:
        statement = statement.where(CryptoDataGap.symbol_id == symbol_id)
    result = await session.execute(statement.values(status="resolved", resolved_at=resolved_at))
    return dml_rowcount(result)
