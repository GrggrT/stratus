from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import (
    BankrollLog,
    DailyActual,
    ExecutionMode,
    HealthAudit,
    JobRun,
    Market,
    ModelProb,
    Position,
    PositionStatus,
    Station,
)
from stratus.repositories.job_runs import collector_uptime
from stratus.repositories.runtime_control import get_runtime_control
from stratus.services.live_analytics import (
    EquityPoint,
    PositionStat,
    cumulative_equity,
    summarize_live_performance,
)


@dataclass(frozen=True, slots=True)
class StationScore:
    icao: str
    count: int
    brier: float


@dataclass(frozen=True, slots=True)
class LiveGate:
    passed: bool
    station_scores: tuple[StationScore, ...]
    resolved_positions: int
    clv_samples: int
    average_clv: float | None
    roi: float


@dataclass(frozen=True, slots=True)
class ReliabilityBin:
    lower: float
    upper: float
    forecast: float
    actual: float
    count: int


@dataclass(frozen=True, slots=True)
class ModelError:
    icao: str
    target: str
    model: str
    forecast_c: float
    actual_c: float

    @property
    def error_c(self) -> float:
        return self.forecast_c - self.actual_c


def largest_model_errors(
    rows: list[tuple[str, str, float, datetime, dict[str, object]]],
    limit: int = 5,
) -> list[ModelError]:
    if limit < 1:
        raise ValueError("limit must be positive")
    latest: dict[tuple[str, str, str], tuple[datetime, ModelError]] = {}
    for icao, target, actual_c, probability_ts, details in rows:
        for model, component in details.items():
            if model == "climatology" or not isinstance(component, dict):
                continue
            mean_c = component.get("mean_c")
            if not isinstance(mean_c, int | float):
                continue
            key = (icao, target, model)
            candidate = ModelError(icao, target, model, float(mean_c), actual_c)
            if key not in latest or probability_ts > latest[key][0]:
                latest[key] = (probability_ts, candidate)
    return sorted(
        (value[1] for value in latest.values()),
        key=lambda error: abs(error.error_c),
        reverse=True,
    )[:limit]


async def _daily_model_errors(
    session: AsyncSession, start: datetime, end: datetime
) -> list[ModelError]:
    rows = (
        await session.execute(
            select(
                Station.icao,
                Market.kind,
                DailyActual.temp_c,
                ModelProb.ts,
                ModelProb.components,
            )
            .select_from(Market)
            .join(Station, Market.station_id == Station.id)
            .join(ModelProb, ModelProb.market_id == Market.id)
            .join(
                DailyActual,
                and_(
                    DailyActual.station_id == Market.station_id,
                    DailyActual.date == Market.market_date,
                    DailyActual.kind == Market.kind,
                ),
            )
            .where(
                Market.resolves_at >= start,
                Market.resolves_at < end,
                ModelProb.ts <= Market.resolves_at,
            )
        )
    ).all()
    return largest_model_errors(
        [
            (icao, kind.value, float(actual_c), probability_ts, details)
            for icao, kind, actual_c, probability_ts, details in rows
            if isinstance(details, dict)
        ]
    )


async def _resolved_probability_rows(
    session: AsyncSession, now: datetime | None = None,
) -> list[tuple[str, bool, float]]:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=30)
    rows = (
        await session.execute(
            select(Market, Station, ModelProb)
            .join(Station, Market.station_id == Station.id)
            .join(ModelProb, ModelProb.market_id == Market.id)
            .where(
                Market.resolved_outcome.is_not(None),
                Market.resolves_at >= cutoff,
                ModelProb.ts <= Market.resolves_at,
            )
            .distinct(Market.id)
            .order_by(Market.id, ModelProb.ts.desc())
        )
    ).all()
    return [
        (station.icao, bool(market.resolved_outcome), float(probability.prob))
        for market, station, probability in rows
    ]


def reliability_bins(
    rows: list[tuple[str, bool, float]],
) -> dict[str, tuple[ReliabilityBin, ...]]:
    grouped: dict[str, dict[int, list[tuple[float, float]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for icao, outcome, probability in rows:
        bin_id = min(9, max(0, int(probability * 10)))
        grouped[icao][bin_id].append((probability, float(outcome)))

    result: dict[str, tuple[ReliabilityBin, ...]] = {}
    for icao, station_bins in sorted(grouped.items()):
        values = []
        for bin_id, samples in sorted(station_bins.items()):
            values.append(
                ReliabilityBin(
                    lower=bin_id / 10,
                    upper=(bin_id + 1) / 10,
                    forecast=sum(sample[0] for sample in samples) / len(samples),
                    actual=sum(sample[1] for sample in samples) / len(samples),
                    count=len(samples),
                )
            )
        result[icao] = tuple(values)
    return result


def render_reliability_chart(rows: list[tuple[str, bool, float]]) -> bytes | None:
    bins_by_station = reliability_bins(rows)
    if not bins_by_station:
        return None

    width, height = 900, 650
    left, top, right, bottom = 95, 70, 55, 85
    plot_width = width - left - right
    plot_height = height - top - bottom
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=18)
    small_font = ImageFont.load_default(size=15)

    def point(x: float, y: float) -> tuple[int, int]:
        return (
            round(left + x * plot_width),
            round(top + (1 - y) * plot_height),
        )

    for index in range(11):
        value = index / 10
        x, y = point(value, value)
        draw.line((x, top, x, top + plot_height), fill="#e4e7eb", width=1)
        draw.line((left, y, left + plot_width, y), fill="#e4e7eb", width=1)
        draw.text((x - 10, top + plot_height + 14), f"{value:.1f}", fill="#333333", font=small_font)
        draw.text((left - 48, y - 8), f"{value:.1f}", fill="#333333", font=small_font)

    draw.line((*point(0, 0), *point(1, 1)), fill="#7d8790", width=3)
    colors = ("#1261a0", "#d1495b", "#238636")
    for color, (_icao, bins) in zip(colors, bins_by_station.items(), strict=False):
        points = [point(item.forecast, item.actual) for item in bins]
        if len(points) > 1:
            draw.line(points, fill=color, width=4)
        for chart_point, item in zip(points, bins, strict=True):
            x, y = chart_point
            draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=color, outline="white", width=2)
            draw.text((x + 9, y - 17), f"n={item.count}", fill=color, font=small_font)

    draw.rectangle((left, top, left + plot_width, top + plot_height), outline="#333333", width=2)
    draw.text((left, 24), "Reliability — rolling 30 days", fill="#111111", font=font)
    draw.text((width // 2 - 85, height - 38), "Forecast probability", fill="#111111", font=font)
    draw.text((15, top + plot_height // 2 - 10), "Observed", fill="#111111", font=font)
    legend_x = left + plot_width - 225
    for index, (color, icao) in enumerate(zip(colors, bins_by_station, strict=False)):
        y = 27 + index * 24
        draw.line((legend_x, y + 8, legend_x + 28, y + 8), fill=color, width=4)
        draw.text((legend_x + 36, y), icao, fill=color, font=small_font)

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


async def reliability_chart(session: AsyncSession) -> bytes | None:
    return render_reliability_chart(await _resolved_probability_rows(session))


async def _live_equity_points(session: AsyncSession) -> list[EquityPoint]:
    rows = (
        await session.execute(
            select(Position.closed_at, Position.pnl_usd).where(
                Position.mode == ExecutionMode.LIVE,
                Position.status == PositionStatus.CLOSED,
                Position.closed_at.is_not(None),
            )
        )
    ).all()
    return cumulative_equity(
        [(ts, float(pnl or 0)) for ts, pnl in rows if ts is not None]
    )


def render_equity_chart(points: list[EquityPoint]) -> bytes | None:
    if len(points) < 2:
        return None

    width, height = 900, 520
    left, top, right, bottom = 95, 60, 45, 80
    plot_width = width - left - right
    plot_height = height - top - bottom
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=18)
    small_font = ImageFont.load_default(size=15)

    values = [p.cumulative_pnl_usd for p in points]
    lo = min(0.0, min(values))
    hi = max(0.0, max(values))
    span = hi - lo or 1.0
    n = len(points)

    def coord(index: int, value: float) -> tuple[int, int]:
        x = left + (index / (n - 1)) * plot_width
        y = top + (1 - (value - lo) / span) * plot_height
        return round(x), round(y)

    # Zero line.
    zero_y = coord(0, 0.0)[1]
    draw.line((left, zero_y, left + plot_width, zero_y), fill="#c8ccd0", width=2)
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        value = lo + frac * span
        y = round(top + (1 - frac) * plot_height)
        draw.text((left - 78, y - 8), f"${value:+.0f}", fill="#333333", font=small_font)

    line = [coord(i, v) for i, v in enumerate(values)]
    final_positive = values[-1] >= 0
    draw.line(line, fill="#238636" if final_positive else "#d1495b", width=3)
    draw.ellipse(
        (line[-1][0] - 6, line[-1][1] - 6, line[-1][0] + 6, line[-1][1] + 6),
        fill="#238636" if final_positive else "#d1495b",
        outline="white",
        width=2,
    )
    draw.rectangle(
        (left, top, left + plot_width, top + plot_height), outline="#333333", width=2
    )
    draw.text((left, 22), "Live realised equity", fill="#111111", font=font)
    draw.text(
        (left + plot_width - 150, 24),
        f"${values[-1]:+.2f} ({n} trades)",
        fill="#238636" if final_positive else "#d1495b",
        font=small_font,
    )

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


async def live_equity_chart(session: AsyncSession) -> bytes | None:
    return render_equity_chart(await _live_equity_points(session))


async def calculate_live_gate(
    session: AsyncSession, initial_bankroll_usd: float
) -> LiveGate:
    resolved = await _resolved_probability_rows(session)
    by_station: dict[str, list[float]] = defaultdict(list)
    for icao, outcome, probability in resolved:
        by_station[icao].append((probability - float(outcome)) ** 2)
    station_scores = tuple(
        StationScore(icao, len(errors), sum(errors) / len(errors))
        for icao, errors in sorted(by_station.items())
    )
    positions = (
        await session.execute(
            select(Position).where(
                Position.mode == ExecutionMode.PAPER,
                Position.status == PositionStatus.CLOSED,
            )
        )
    ).scalars().all()
    clvs = [float(position.clv) for position in positions if position.clv is not None]
    average_clv = sum(clvs) / len(clvs) if clvs else None
    pnl = sum(float(position.pnl_usd or 0) for position in positions)
    roi = pnl / initial_bankroll_usd
    passed = (
        len(station_scores) == 3
        and all(score.count >= 100 and score.brier < 0.18 for score in station_scores)
        and len(positions) >= 100
        and len(clvs) >= 100
        and average_clv is not None
        and average_clv > 0.015
        and roi > 0
    )
    return LiveGate(
        passed,
        station_scores,
        len(positions),
        len(clvs),
        average_clv,
        roi,
    )


async def status_report(session: AsyncSession, initial_bankroll_usd: float) -> str:
    now = datetime.now(UTC)
    control = await get_runtime_control(session)
    balance = await session.scalar(
        select(BankrollLog.balance_usd)
        .where(BankrollLog.mode == control.mode)
        .order_by(BankrollLog.ts.desc())
        .limit(1)
    )
    rows = (
        await session.execute(
            select(Position, Market, Station)
            .join(Market, Position.market_id == Market.id)
            .join(Station, Market.station_id == Station.id)
            .where(
                Position.mode == control.mode,
                Position.status == PositionStatus.OPEN,
            )
        )
    ).all()
    exposure: dict[tuple[str, object], Decimal] = defaultdict(Decimal)
    for position, market, station in rows:
        exposure[(station.icao, market.market_date)] += position.size_usd
    lines = [
        "STRATUS status",
        f"Mode: {control.mode.value}",
        f"Kill switch: {'ON' if control.kill_switch else 'off'}",
        f"Bankroll: ${float(balance or initial_bankroll_usd):.2f}",
        f"Open positions: {len(rows)}",
    ]
    if control.halted_reason:
        lines.insert(3, f"PROJECT HALTED: {control.halted_reason}")
    for (icao, market_date), value in sorted(exposure.items()):
        lines.append(f"{icao} {market_date}: ${value:.2f} exposure")
    cutoff = now - timedelta(hours=24)
    job_runs = (
        await session.execute(select(JobRun).where(JobRun.finished_at >= cutoff))
    ).scalars().all()
    if job_runs:
        successful = sum(run.status == "success" for run in job_runs)
        lines.append(
            f"Scheduler 24h: {successful / len(job_runs):.1%} "
            f"({successful}/{len(job_runs)})"
        )
        failures = [run for run in job_runs if run.status != "success"]
        if failures:
            latest = max(failures, key=lambda run: run.finished_at)
            lines.append(f"Last job failure: {latest.job_id} ({latest.status})")
    uptime = await collector_uptime(session, now=now)
    coverage_hours = uptime.coverage.total_seconds() / 3600
    if uptime.mature:
        state = "PASS" if uptime.passed else "FAIL"
        worst = min((metric.uptime for metric in uptime.metrics), default=0.0)
        lines.append(f"Collector 48h: {state}; worst uptime {worst:.2%}")
        failing = [metric for metric in uptime.metrics if not metric.passed]
        if failing:
            detail = min(failing, key=lambda metric: metric.uptime)
            lines.append(
                f"Collector issue: {detail.job_id} {detail.successful}/{detail.expected}, "
                f"max gap {detail.max_gap.total_seconds() / 60:.0f}m"
            )
    else:
        lines.append(f"Collector 48h: collecting ({coverage_hours:.1f}/48.0h)")
    latest_audit = await session.scalar(
        select(HealthAudit).order_by(HealthAudit.ts.desc()).limit(1)
    )
    if latest_audit is not None:
        lines.append(
            f"Data quality: {latest_audit.status} ({len(latest_audit.issues)} issue(s))"
        )
    return "\n".join(lines)


async def calibration_report(session: AsyncSession, initial_bankroll_usd: float) -> str:
    gate = await calculate_live_gate(session, initial_bankroll_usd)
    resolved = await _resolved_probability_rows(session)
    bins: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for _, outcome, probability in resolved:
        bins[min(9, int(probability * 10))].append((probability, float(outcome)))
    lines = ["Calibration (rolling 30 days)", f"Live gate: {'PASS' if gate.passed else 'BLOCKED'}"]
    for score in gate.station_scores:
        lines.append(f"{score.icao}: Brier {score.brier:.3f}, n={score.count}/100")
    lines.append("Reliability bins (forecast → actual, n):")
    for bin_id, values in sorted(bins.items()):
        forecast = sum(value[0] for value in values) / len(values)
        actual = sum(value[1] for value in values) / len(values)
        lines.append(
            f"{bin_id / 10:.1f}–{(bin_id + 1) / 10:.1f}: "
            f"{forecast:.2f} → {actual:.2f}, {len(values)}"
        )
    clv = "n/a" if gate.average_clv is None else f"{gate.average_clv:+.3f}"
    lines.append(
        f"Paper positions: {gate.resolved_positions}; CLV samples: {gate.clv_samples}; "
        f"CLV {clv}; ROI {gate.roi:+.2%}"
    )
    return "\n".join(lines)


async def live_report(session: AsyncSession) -> str:
    rows = (
        await session.execute(
            select(
                Position.status,
                Position.pnl_usd,
                Position.clv,
                Position.size_usd,
                Position.fee_usd,
            ).where(Position.mode == ExecutionMode.LIVE)
        )
    ).all()
    perf = summarize_live_performance(
        [
            PositionStat(
                closed=status is PositionStatus.CLOSED,
                pnl_usd=float(pnl) if pnl is not None else None,
                clv=float(clv) if clv is not None else None,
                cost_usd=float(cost),
                fee_usd=float(fee) if fee is not None else None,
            )
            for status, pnl, clv, cost, fee in rows
        ]
    )
    if perf.positions == 0:
        return "Live trading: no live positions yet."
    win_rate = "n/a" if perf.win_rate is None else f"{perf.win_rate:.0%}"
    roi = "n/a" if perf.roi is None else f"{perf.roi:+.2%}"
    clv = "n/a" if perf.avg_clv is None else f"{perf.avg_clv:+.3f}"
    beat = "n/a" if perf.clv_beat_rate is None else f"{perf.clv_beat_rate:.0%}"
    return "\n".join(
        [
            "Live performance",
            f"Positions: {perf.positions} ({perf.open} open, {perf.closed} closed)",
            f"P&L: ${perf.total_pnl_usd:+.2f} · ROI {roi} · win rate {win_rate}",
            f"Avg CLV: {clv} · beat closing line: {beat}",
            f"Fees paid: ${perf.total_fees_usd:.2f}",
        ]
    )


async def daily_report(session: AsyncSession, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    local_tz = ZoneInfo("Europe/Berlin")
    local_today = now.astimezone(local_tz).date()
    start_local = datetime.combine(
        local_today - timedelta(days=1), datetime.min.time(), tzinfo=local_tz
    )
    end_local = start_local + timedelta(days=1)
    positions = (
        await session.execute(
            select(Position).where(
                Position.mode == ExecutionMode.PAPER,
                Position.status == PositionStatus.CLOSED,
                Position.closed_at >= start_local.astimezone(UTC),
                Position.closed_at < end_local.astimezone(UTC),
            )
        )
    ).scalars().all()
    pnl = sum(float(position.pnl_usd or 0) for position in positions)
    clvs = [float(position.clv) for position in positions if position.clv is not None]
    average_clv = sum(clvs) / len(clvs) if clvs else None
    clv_text = "n/a" if average_clv is None else f"{average_clv:+.3f}"
    errors = await _daily_model_errors(
        session, start_local.astimezone(UTC), end_local.astimezone(UTC)
    )
    lines = [
        f"Daily {start_local.date()}",
        f"Resolved positions: {len(positions)}",
        f"P&L: ${pnl:+.2f}",
        f"Average CLV: {clv_text}",
        "Largest model errors:",
    ]
    if not errors:
        lines.append("n/a")
    for error in errors:
        lines.append(
            f"{error.icao} {error.target} {error.model}: {error.error_c:+.1f}°C "
            f"({error.forecast_c:.1f} vs actual {error.actual_c:.1f})"
        )
    return "\n".join(lines)
