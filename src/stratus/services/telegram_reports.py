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
    StationStage,
)
from stratus.repositories.analytics import (
    probability_pipeline_version,
)
from stratus.repositories.evidence_continuity import (
    current_probability_pipeline_release_epoch,
)
from stratus.repositories.job_runs import collector_uptime
from stratus.repositories.model_probs import (
    latest_active_market_probability_components,
)
from stratus.repositories.runtime_control import get_runtime_control
from stratus.repositories.validation_ledger import load_prospective_validation_dataset
from stratus.services.forecast_provenance import model_input_provenance_is_complete
from stratus.services.live_analytics import (
    EquityPoint,
    PositionStat,
    brier_comparison,
    cumulative_equity,
    prospective_clv_outcomes,
    prospective_position_stats,
    prospective_trade_outcomes,
    summarize_live_performance,
    summarize_prospective_validation,
)


@dataclass(frozen=True, slots=True)
class StationScore:
    icao: str
    count: int
    brier: float


@dataclass(frozen=True, slots=True)
class GateCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class LiveGate:
    passed: bool
    station_scores: tuple[StationScore, ...]
    resolved_positions: int
    clv_samples: int
    average_clv: float | None
    roi: float
    independent_samples: int = 0
    checks: tuple[GateCheck, ...] = ()
    pipeline_version: str | None = None
    pipeline_resolved_trades: int = 0
    pipeline_brier_model: float | None = None
    pipeline_brier_market: float | None = None


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
    session: AsyncSession,
    now: datetime | None = None,
    station_stages: tuple[StationStage, ...] | None = None,
    pipeline_version: str | None = None,
    market_anchor_protocol: str | None = None,
    require_attested_outcome: bool = False,
) -> list[tuple[str, bool, float]]:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=30)
    statement = (
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
    if station_stages is not None:
        statement = statement.where(
            Station.active.is_(True),
            Station.stage.in_(station_stages),
        )
    if market_anchor_protocol is not None:
        statement = statement.where(
            ModelProb.market_anchor_protocol == market_anchor_protocol
        )
    if require_attested_outcome:
        statement = statement.where(Market.resolution_event_id.is_not(None))
    rows = (await session.execute(statement)).all()
    return [
        (station.icao, bool(market.resolved_outcome), float(probability.prob))
        for market, station, probability in rows
        if pipeline_version is None
        or probability_pipeline_version(probability.components) == pipeline_version
    ]


async def _current_pipeline_version(session: AsyncSession) -> str | None:
    """Cohort of the probability architecture approved for active evidence."""
    epoch = await current_probability_pipeline_release_epoch(session)
    return epoch.cohort if epoch is not None else None


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
    return cumulative_equity([(ts, float(pnl or 0)) for ts, pnl in rows if ts is not None])


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
    draw.rectangle((left, top, left + plot_width, top + plot_height), outline="#333333", width=2)
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


def _check(name: str, passed: bool, detail: str) -> GateCheck:
    return GateCheck(name=name, passed=passed, detail=detail)


def qualified_station_scores(
    scores: tuple[StationScore, ...],
    *,
    sample_minimum: int,
    brier_maximum: float,
) -> tuple[StationScore, ...]:
    """Return qualified stations without penalising a larger research pool."""
    return tuple(
        score for score in scores if score.count >= sample_minimum and score.brier < brier_maximum
    )


async def calculate_live_gate(
    session: AsyncSession,
    initial_bankroll_usd: float,
    *,
    min_stations: int = 3,
    station_samples: int = 100,
    station_brier: float = 0.18,
    independent_samples_min: int = 100,
) -> LiveGate:
    """Formal "edge is proven" gate (docs/EDGE_VALIDATION_PLAN.md P4).

    Every check must pass before live mode is reachable. The decision rides
    on skill_pnl and statistical significance, never on raw ROI, which one
    lucky tail trade can carry."""
    current_pipeline = await _current_pipeline_version(session)
    dataset = await load_prospective_validation_dataset(
        session,
        mode=ExecutionMode.PAPER,
        pipeline_cohort=current_pipeline or "__no_current_pipeline__",
    )
    formal_trades = dataset.trades
    positions = prospective_position_stats(formal_trades)
    trades = prospective_trade_outcomes(formal_trades)
    clv_outcomes = prospective_clv_outcomes(formal_trades)
    trajectory = summarize_prospective_validation(
        formal_trades,
        initial_bankroll_usd=initial_bankroll_usd,
    )
    by_station: dict[str, list[float]] = defaultdict(list)
    for trade in trades:
        if trade.station_icao is not None:
            by_station[trade.station_icao].append(
                (trade.model_prob - float(trade.won)) ** 2
            )
    station_scores = tuple(
        StationScore(icao, len(errors), sum(errors) / len(errors))
        for icao, errors in sorted(by_station.items())
    )
    independent_samples = trajectory.independent_blocks
    perf = summarize_live_performance(positions)
    briers = brier_comparison(trades)
    clvs = [outcome.clv for outcome in clv_outcomes]
    average_clv = sum(clvs) / len(clvs) if clvs else None
    pnl = sum(trade.pnl_usd for trade in formal_trades)
    roi = pnl / initial_bankroll_usd

    active_probability_components = (
        await latest_active_market_probability_components(session)
    )
    complete_forecast_provenance = sum(
        model_input_provenance_is_complete(components)
        and probability_pipeline_version(components) == current_pipeline
        for components in active_probability_components
    )

    qualified_stations = qualified_station_scores(
        station_scores,
        sample_minimum=station_samples,
        brier_maximum=station_brier,
    )
    stations_ok = len(qualified_stations) >= min_stations
    checks = (
        _check(
            "stations_calibrated",
            stations_ok,
            (
                f"{len(qualified_stations)}/{min_stations} qualified from "
                f"{len(station_scores)} paper station(s); "
                + "; ".join(
                    f"{s.icao} n={s.count}/{station_samples} "
                    f"brier={s.brier:.3f}/{station_brier:.3f}"
                    for s in station_scores
                )
                if station_scores
                else "no resolved probability samples on paper stations"
            ),
        ),
        _check(
            "resolved_positions_200",
            len(formal_trades) >= 200,
            f"{len(formal_trades)}/200 closed prospective paper order fills",
        ),
        _check(
            "independent_station_days_100",
            independent_samples >= independent_samples_min,
            (f"{independent_samples}/{independent_samples_min} distinct station-day-kind samples"),
        ),
        _check(
            "pipeline_version_samples_100",
            current_pipeline is not None and len(trades) >= 100,
            (
                f"{len(trades)}/100 lane-scoped resolved trades on {current_pipeline}"
                if current_pipeline is not None
                else "current probability pipeline version unavailable"
            ),
        ),
        _check("clv_samples_100", len(clvs) >= 100, f"{len(clvs)}/100 CLV samples"),
        _check(
            "avg_clv_positive",
            average_clv is not None and average_clv > 0.015,
            f"avg CLV {average_clv:+.4f}" if average_clv is not None else "no CLV",
        ),
        _check(
            "clv_beat_rate_52",
            perf.clv_beat_rate is not None and perf.clv_beat_rate >= 0.52,
            (f"beat rate {perf.clv_beat_rate:.1%}" if perf.clv_beat_rate is not None else "no CLV"),
        ),
        _check(
            "roi_ci_excludes_zero",
            perf.roi_ci_low is not None and perf.roi_ci_low > 0,
            (
                f"ROI 95% CI [{perf.roi_ci_low:+.4f}, {perf.roi_ci_high:+.4f}]"
                if perf.roi_ci_low is not None and perf.roi_ci_high is not None
                else "CI unavailable"
            ),
        ),
        _check(
            "skill_pnl_significant",
            perf.skill_pnl_ci_low is not None and perf.skill_pnl_ci_low > 0,
            (
                f"skill ${perf.skill_pnl_usd:+.2f}, CI low ${perf.skill_pnl_ci_low:+.2f}"
                if perf.skill_pnl_usd is not None and perf.skill_pnl_ci_low is not None
                else "decomposition unavailable"
            ),
        ),
        _check(
            "brier_model_beats_market",
            briers is not None and briers[0] < briers[1],
            (
                f"model {briers[0]:.4f} vs market {briers[1]:.4f} "
                f"(n={len(trades)}, {current_pipeline})"
                if briers is not None
                else (
                    f"no resolved trades on {current_pipeline}"
                    if current_pipeline is not None
                    else "current probability pipeline version unavailable"
                )
            ),
        ),
        _check(
            "forecast_input_provenance",
            bool(active_probability_components)
            and complete_forecast_provenance == len(active_probability_components),
            (
                f"{complete_forecast_provenance}/"
                f"{len(active_probability_components)} latest active-market "
                "probabilities bind immutable forecast source evidence"
            ),
        ),
    )
    return LiveGate(
        passed=all(check.passed for check in checks),
        station_scores=station_scores,
        resolved_positions=len(formal_trades),
        clv_samples=len(clvs),
        average_clv=average_clv,
        roi=roi,
        independent_samples=independent_samples,
        checks=checks,
        pipeline_version=current_pipeline,
        pipeline_resolved_trades=len(trades),
        pipeline_brier_model=briers[0] if briers is not None else None,
        pipeline_brier_market=briers[1] if briers is not None else None,
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
        (await session.execute(select(JobRun).where(JobRun.finished_at >= cutoff))).scalars().all()
    )
    if job_runs:
        successful = sum(run.status == "success" for run in job_runs)
        lines.append(
            f"Scheduler 24h: {successful / len(job_runs):.1%} ({successful}/{len(job_runs)})"
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
        lines.append(f"Data quality: {latest_audit.status} ({len(latest_audit.issues)} issue(s))")
    return "\n".join(lines)


async def calibration_report(session: AsyncSession, initial_bankroll_usd: float) -> str:
    gate = await calculate_live_gate(session, initial_bankroll_usd)
    dataset = await load_prospective_validation_dataset(
        session,
        mode=ExecutionMode.PAPER,
        pipeline_cohort=gate.pipeline_version or "__no_current_pipeline__",
    )
    resolved = [
        (trade.station_icao or "unknown", trade.won, trade.model_prob)
        for trade in prospective_trade_outcomes(dataset.trades)
    ]
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
        f"Prospective order fills: {gate.resolved_positions}; CLV samples: {gate.clv_samples}; "
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
                Position.size_shares,
                Position.avg_price,
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
                size_shares=float(shares) if shares is not None else None,
                avg_price=float(price) if price is not None else None,
            )
            for status, pnl, clv, cost, fee, shares, price in rows
        ]
    )
    if perf.positions == 0:
        return "Live trading: no live positions yet."
    win_rate = "n/a" if perf.win_rate is None else f"{perf.win_rate:.0%}"
    roi = "n/a" if perf.roi is None else f"{perf.roi:+.2%}"
    clv = "n/a" if perf.avg_clv is None else f"{perf.avg_clv:+.3f}"
    beat = "n/a" if perf.clv_beat_rate is None else f"{perf.clv_beat_rate:.0%}"
    lines = [
        "Live performance",
        f"Positions: {perf.positions} ({perf.open} open, {perf.closed} closed)",
        f"P&L: ${perf.total_pnl_usd:+.2f} · ROI {roi} · win rate {win_rate}",
        f"Avg CLV: {clv} · beat closing line: {beat}",
        f"Fees paid: ${perf.total_fees_usd:.2f}",
    ]
    if perf.skill_pnl_usd is not None and perf.luck_pnl_usd is not None:
        lines.append(
            f"Skill/luck split: ${perf.skill_pnl_usd:+.2f} skill · "
            f"${perf.luck_pnl_usd:+.2f} luck ({perf.decomposed} decomposed)"
        )
    return "\n".join(lines)


async def edge_report_text(session: AsyncSession, initial_bankroll_usd: float) -> str:
    """Weekly anti-luck digest: the live-gate checklist with its numbers.
    Shows exactly what is already proven and what is still missing."""
    gate = await calculate_live_gate(session, initial_bankroll_usd)
    lines = [
        "STRATUS edge report (paper)",
        f"Live gate: {'PASS' if gate.passed else 'BLOCKED'}",
    ]
    for check in gate.checks:
        marker = "✅" if check.passed else "❌"
        lines.append(f"{marker} {check.name}: {check.detail}")
    return "\n".join(lines)


async def daily_report(session: AsyncSession, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    local_tz = ZoneInfo("Europe/Berlin")
    local_today = now.astimezone(local_tz).date()
    start_local = datetime.combine(
        local_today - timedelta(days=1), datetime.min.time(), tzinfo=local_tz
    )
    end_local = start_local + timedelta(days=1)
    positions = (
        (
            await session.execute(
                select(Position).where(
                    Position.mode == ExecutionMode.PAPER,
                    Position.status == PositionStatus.CLOSED,
                    Position.closed_at >= start_local.astimezone(UTC),
                    Position.closed_at < end_local.astimezone(UTC),
                )
            )
        )
        .scalars()
        .all()
    )
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
