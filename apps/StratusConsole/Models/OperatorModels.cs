using System.Text.Json.Serialization;

namespace StratusConsole.Models;

public sealed record RuntimeDto(
    [property: JsonPropertyName("mode")] string Mode,
    [property: JsonPropertyName("kill_switch")] bool KillSwitch,
    [property: JsonPropertyName("halted_reason")] string? HaltedReason);

public sealed record HealthAuditDto(
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("timestamp")] DateTimeOffset Timestamp,
    [property: JsonPropertyName("issues")] IReadOnlyList<Dictionary<string, object>> Issues);

public sealed record ValidationDto(
    [property: JsonPropertyName("checkpoint_day")] int CheckpointDay,
    [property: JsonPropertyName("evaluated_at")] DateTimeOffset EvaluatedAt,
    [property: JsonPropertyName("eligible")] bool Eligible,
    [property: JsonPropertyName("passed")] bool Passed);

public sealed record OverviewResponse(
    [property: JsonPropertyName("generated_at")] DateTimeOffset GeneratedAt,
    [property: JsonPropertyName("runtime")] RuntimeDto Runtime,
    [property: JsonPropertyName("active_markets")] int ActiveMarkets,
    [property: JsonPropertyName("blocked_markets")] int BlockedMarkets,
    [property: JsonPropertyName("open_positions")] int OpenPositions,
    [property: JsonPropertyName("pending_signals")] int PendingSignals,
    [property: JsonPropertyName("bankroll_usd")] double? BankrollUsd,
    [property: JsonPropertyName("open_exposure_usd")] double OpenExposureUsd,
    [property: JsonPropertyName("health_audit")] HealthAuditDto? HealthAudit,
    [property: JsonPropertyName("validation")] ValidationDto? Validation);

public sealed record CollectionResponse<T>(
    [property: JsonPropertyName("generated_at")] DateTimeOffset GeneratedAt,
    [property: JsonPropertyName("items")] IReadOnlyList<T> Items);

public sealed record MarketDto(
    [property: JsonPropertyName("id")] long Id,
    [property: JsonPropertyName("polymarket_id")] string PolymarketId,
    [property: JsonPropertyName("station_icao")] string? StationIcao,
    [property: JsonPropertyName("city")] string? City,
    [property: JsonPropertyName("market_date")] DateOnly MarketDate,
    [property: JsonPropertyName("kind")] string Kind,
    [property: JsonPropertyName("bucket_low_c")] double? BucketLowC,
    [property: JsonPropertyName("bucket_high_c")] double? BucketHighC,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("station_verified")] bool StationVerified,
    [property: JsonPropertyName("verification_reason")] string? VerificationReason,
    [property: JsonPropertyName("resolves_at")] DateTimeOffset ResolvesAt);

public sealed record SignalDto(
    [property: JsonPropertyName("id")] long Id,
    [property: JsonPropertyName("market_id")] long MarketId,
    [property: JsonPropertyName("timestamp")] DateTimeOffset Timestamp,
    [property: JsonPropertyName("polymarket_id")] string PolymarketId,
    [property: JsonPropertyName("station_icao")] string? StationIcao,
    [property: JsonPropertyName("side")] string Side,
    [property: JsonPropertyName("model_probability")] double ModelProbability,
    [property: JsonPropertyName("market_price")] double MarketPrice,
    [property: JsonPropertyName("edge")] double Edge,
    [property: JsonPropertyName("kelly_fraction")] double KellyFraction,
    [property: JsonPropertyName("size_usd")] double SizeUsd,
    [property: JsonPropertyName("confidence")] string Confidence,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("skip_reason")] string? SkipReason);

public sealed record PositionDto(
    [property: JsonPropertyName("id")] long Id,
    [property: JsonPropertyName("market_id")] long MarketId,
    [property: JsonPropertyName("polymarket_id")] string PolymarketId,
    [property: JsonPropertyName("station_icao")] string? StationIcao,
    [property: JsonPropertyName("market_date")] DateOnly MarketDate,
    [property: JsonPropertyName("kind")] string Kind,
    [property: JsonPropertyName("mode")] string Mode,
    [property: JsonPropertyName("side")] string Side,
    [property: JsonPropertyName("average_price")] double AveragePrice,
    [property: JsonPropertyName("size_usd")] double SizeUsd,
    [property: JsonPropertyName("fee_usd")] double? FeeUsd,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("pnl_usd")] double? PnlUsd,
    [property: JsonPropertyName("clv")] double? Clv,
    [property: JsonPropertyName("closed_at")] DateTimeOffset? ClosedAt,
    [property: JsonPropertyName("mark_price")] double? MarkPrice,
    [property: JsonPropertyName("unrealized_pnl_usd")] double? UnrealizedPnlUsd);

public sealed record CalibrationDto(
    [property: JsonPropertyName("station_icao")] string StationIcao,
    [property: JsonPropertyName("model")] string Model,
    [property: JsonPropertyName("horizon_bucket")] string HorizonBucket,
    [property: JsonPropertyName("kind")] string Kind,
    [property: JsonPropertyName("bias_c")] double BiasC,
    [property: JsonPropertyName("sigma_c")] double SigmaC,
    [property: JsonPropertyName("sample_count")] int SampleCount,
    [property: JsonPropertyName("brier")] double? Brier,
    [property: JsonPropertyName("updated_at")] DateTimeOffset UpdatedAt);

public sealed record JobDto(
    [property: JsonPropertyName("id")] long Id,
    [property: JsonPropertyName("job_id")] string JobId,
    [property: JsonPropertyName("scheduled_at")] DateTimeOffset ScheduledAt,
    [property: JsonPropertyName("finished_at")] DateTimeOffset FinishedAt,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("error")] string? Error);

public sealed record EquityPointDto(
    [property: JsonPropertyName("t")] DateTimeOffset T,
    [property: JsonPropertyName("cumulative_pnl_usd")] double CumulativePnlUsd);

public sealed record EquityResponse(
    [property: JsonPropertyName("generated_at")] DateTimeOffset GeneratedAt,
    [property: JsonPropertyName("points")] IReadOnlyList<EquityPointDto> Points);

public sealed record MarketPricePointDto(
    [property: JsonPropertyName("t")] DateTimeOffset T,
    [property: JsonPropertyName("midpoint")] double Midpoint,
    [property: JsonPropertyName("best_bid")] double BestBid,
    [property: JsonPropertyName("best_ask")] double BestAsk,
    [property: JsonPropertyName("bid_depth_usd")] double BidDepthUsd,
    [property: JsonPropertyName("ask_depth_usd")] double AskDepthUsd);

public sealed record MarketPriceSeriesResponse(
    [property: JsonPropertyName("generated_at")] DateTimeOffset GeneratedAt,
    [property: JsonPropertyName("market_id")] long MarketId,
    [property: JsonPropertyName("points")] IReadOnlyList<MarketPricePointDto> Points);

public sealed record ProbabilityPointDto(
    [property: JsonPropertyName("t")] DateTimeOffset T,
    [property: JsonPropertyName("source")] string Source,
    [property: JsonPropertyName("model_probability")] double? ModelProbability,
    [property: JsonPropertyName("market_price")] double? MarketPrice,
    [property: JsonPropertyName("edge")] double? Edge,
    [property: JsonPropertyName("sigma")] double? Sigma,
    [property: JsonPropertyName("horizon_h")] double? HorizonH,
    [property: JsonPropertyName("confidence")] string? Confidence,
    [property: JsonPropertyName("status")] string? Status);

public sealed record ProbabilitySeriesResponse(
    [property: JsonPropertyName("generated_at")] DateTimeOffset GeneratedAt,
    [property: JsonPropertyName("market_id")] long MarketId,
    [property: JsonPropertyName("points")] IReadOnlyList<ProbabilityPointDto> Points);

public sealed record OrderBookLevelDto(
    [property: JsonPropertyName("price")] double Price,
    [property: JsonPropertyName("size")] double Size,
    [property: JsonPropertyName("notional_usd")] double NotionalUsd);

public sealed record OrderBookResponse(
    [property: JsonPropertyName("generated_at")] DateTimeOffset GeneratedAt,
    [property: JsonPropertyName("market_id")] long MarketId,
    [property: JsonPropertyName("side")] string Side,
    [property: JsonPropertyName("token_id")] string? TokenId,
    [property: JsonPropertyName("source_status")] string SourceStatus,
    [property: JsonPropertyName("error")] string? Error,
    [property: JsonPropertyName("midpoint")] double? Midpoint,
    [property: JsonPropertyName("best_bid")] double? BestBid,
    [property: JsonPropertyName("best_ask")] double? BestAsk,
    [property: JsonPropertyName("bids")] IReadOnlyList<OrderBookLevelDto> Bids,
    [property: JsonPropertyName("asks")] IReadOnlyList<OrderBookLevelDto> Asks);

public sealed record LiveSummaryDto(
    [property: JsonPropertyName("positions")] int Positions,
    [property: JsonPropertyName("closed")] int Closed,
    [property: JsonPropertyName("open")] int Open,
    [property: JsonPropertyName("wins")] int Wins,
    [property: JsonPropertyName("win_rate")] double? WinRate,
    [property: JsonPropertyName("total_pnl_usd")] double TotalPnlUsd,
    [property: JsonPropertyName("total_cost_usd")] double TotalCostUsd,
    [property: JsonPropertyName("total_fees_usd")] double TotalFeesUsd,
    [property: JsonPropertyName("roi")] double? Roi,
    [property: JsonPropertyName("avg_clv")] double? AvgClv,
    [property: JsonPropertyName("clv_beat_rate")] double? ClvBeatRate,
    [property: JsonPropertyName("skill_pnl_usd")] double? SkillPnlUsd = null,
    [property: JsonPropertyName("luck_pnl_usd")] double? LuckPnlUsd = null,
    [property: JsonPropertyName("statistically_positive")] bool? StatisticallyPositive = null,
    [property: JsonPropertyName("brier_model")] double? BrierModel = null,
    [property: JsonPropertyName("brier_market")] double? BrierMarket = null);

public sealed record EdgeBucketDto(
    [property: JsonPropertyName("low")] double Low,
    [property: JsonPropertyName("high")] double? High,
    [property: JsonPropertyName("count")] int Count,
    [property: JsonPropertyName("avg_predicted_edge")] double? AvgPredictedEdge,
    [property: JsonPropertyName("avg_realized_return")] double? AvgRealizedReturn);

public sealed record GateCheckDto(
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("passed")] bool Passed,
    [property: JsonPropertyName("detail")] string Detail);

public sealed record EdgeReportDto(
    [property: JsonPropertyName("generated_at")] DateTimeOffset GeneratedAt,
    [property: JsonPropertyName("mode")] string Mode,
    [property: JsonPropertyName("n_resolved_trades")] int ResolvedTrades,
    [property: JsonPropertyName("brier_model")] double? BrierModel,
    [property: JsonPropertyName("brier_market")] double? BrierMarket,
    [property: JsonPropertyName("skill_pnl_usd")] double? SkillPnlUsd,
    [property: JsonPropertyName("luck_pnl_usd")] double? LuckPnlUsd,
    [property: JsonPropertyName("roi_ci_low")] double? RoiCiLow,
    [property: JsonPropertyName("roi_ci_high")] double? RoiCiHigh,
    [property: JsonPropertyName("skill_pnl_ci_low")] double? SkillPnlCiLow,
    [property: JsonPropertyName("skill_pnl_ci_high")] double? SkillPnlCiHigh,
    [property: JsonPropertyName("statistically_positive")] bool? StatisticallyPositive,
    [property: JsonPropertyName("edge_slope")] double? EdgeSlope,
    [property: JsonPropertyName("buckets")] IReadOnlyList<EdgeBucketDto> Buckets,
    [property: JsonPropertyName("gate_passed")] bool GatePassed,
    [property: JsonPropertyName("gate_checks")] IReadOnlyList<GateCheckDto> GateChecks);

public sealed record BrierSliceDto(
    [property: JsonPropertyName("dimension")] string Dimension,
    [property: JsonPropertyName("value")] string Value,
    [property: JsonPropertyName("count")] int Count,
    [property: JsonPropertyName("brier_model")] double BrierModel,
    [property: JsonPropertyName("brier_market")] double BrierMarket,
    [property: JsonPropertyName("model_minus_market")] double ModelMinusMarket,
    [property: JsonPropertyName("brier_raw_model")] double? BrierRawModel,
    [property: JsonPropertyName("raw_model_minus_market")] double? RawModelMinusMarket,
    [property: JsonPropertyName("raw_count")] int RawCount,
    [property: JsonPropertyName("avg_predicted_edge")] double AvgPredictedEdge,
    [property: JsonPropertyName("avg_realized_return")] double AvgRealizedReturn,
    [property: JsonPropertyName("win_rate")] double WinRate);

public sealed record BrierReportDto(
    [property: JsonPropertyName("generated_at")] DateTimeOffset GeneratedAt,
    [property: JsonPropertyName("mode")] string Mode,
    [property: JsonPropertyName("n_resolved_trades")] int ResolvedTrades,
    [property: JsonPropertyName("brier_model")] double? BrierModel,
    [property: JsonPropertyName("brier_market")] double? BrierMarket,
    [property: JsonPropertyName("brier_raw_model")] double? BrierRawModel,
    [property: JsonPropertyName("raw_count")] int RawCount,
    [property: JsonPropertyName("model_minus_market")] double? ModelMinusMarket,
    [property: JsonPropertyName("raw_model_minus_market")] double? RawModelMinusMarket,
    [property: JsonPropertyName("slices")] IReadOnlyList<BrierSliceDto> Slices);

public sealed record ClvSliceDto(
    [property: JsonPropertyName("dimension")] string Dimension,
    [property: JsonPropertyName("value")] string Value,
    [property: JsonPropertyName("count")] int Count,
    [property: JsonPropertyName("avg_clv")] double AvgClv,
    [property: JsonPropertyName("clv_beat_rate")] double ClvBeatRate,
    [property: JsonPropertyName("avg_entry_price")] double AvgEntryPrice,
    [property: JsonPropertyName("avg_fee_usd")] double AvgFeeUsd,
    [property: JsonPropertyName("avg_realized_return")] double AvgRealizedReturn,
    [property: JsonPropertyName("win_rate")] double WinRate);

public sealed record ClvReportDto(
    [property: JsonPropertyName("generated_at")] DateTimeOffset GeneratedAt,
    [property: JsonPropertyName("mode")] string Mode,
    [property: JsonPropertyName("n_closed_positions")] int ClosedPositions,
    [property: JsonPropertyName("avg_clv")] double? AvgClv,
    [property: JsonPropertyName("clv_beat_rate")] double? ClvBeatRate,
    [property: JsonPropertyName("slices")] IReadOnlyList<ClvSliceDto> Slices);

public sealed record RootCauseTradeDto(
    [property: JsonPropertyName("id")] long Id,
    [property: JsonPropertyName("record_type")] string RecordType,
    [property: JsonPropertyName("order_id")] long? OrderId,
    [property: JsonPropertyName("signal_id")] long? SignalId,
    [property: JsonPropertyName("position_id")] long? PositionId,
    [property: JsonPropertyName("market_id")] long MarketId,
    [property: JsonPropertyName("polymarket_id")] string PolymarketId,
    [property: JsonPropertyName("station_icao")] string? StationIcao,
    [property: JsonPropertyName("market_date")] DateOnly MarketDate,
    [property: JsonPropertyName("kind")] string Kind,
    [property: JsonPropertyName("side")] string Side,
    [property: JsonPropertyName("confidence")] string? Confidence,
    [property: JsonPropertyName("horizon_bucket")] string HorizonBucket,
    [property: JsonPropertyName("timestamp")] DateTimeOffset? Timestamp,
    [property: JsonPropertyName("closed_at")] DateTimeOffset? ClosedAt,
    [property: JsonPropertyName("entry_price")] double EntryPrice,
    [property: JsonPropertyName("closing_line_price")] double? ClosingLinePrice,
    [property: JsonPropertyName("model_probability")] double? ModelProbability,
    [property: JsonPropertyName("market_probability")] double? MarketProbability,
    [property: JsonPropertyName("raw_model_probability")] double? RawModelProbability,
    [property: JsonPropertyName("predicted_edge")] double? PredictedEdge,
    [property: JsonPropertyName("brier_model_error")] double? BrierModelError,
    [property: JsonPropertyName("brier_market_error")] double? BrierMarketError,
    [property: JsonPropertyName("brier_delta")] double? BrierDelta,
    [property: JsonPropertyName("clv")] double? Clv,
    [property: JsonPropertyName("pnl_usd")] double PnlUsd,
    [property: JsonPropertyName("cost_usd")] double CostUsd,
    [property: JsonPropertyName("fee_usd")] double? FeeUsd,
    [property: JsonPropertyName("won")] bool Won,
    [property: JsonPropertyName("resolved_outcome")] bool? ResolvedOutcome);

public sealed record RootCauseDrilldownDto(
    [property: JsonPropertyName("generated_at")] DateTimeOffset GeneratedAt,
    [property: JsonPropertyName("mode")] string Mode,
    [property: JsonPropertyName("metric")] string Metric,
    [property: JsonPropertyName("dimension")] string Dimension,
    [property: JsonPropertyName("value")] string Value,
    [property: JsonPropertyName("count")] int Count,
    [property: JsonPropertyName("items")] IReadOnlyList<RootCauseTradeDto> Items);

public sealed record OrderDto(
    [property: JsonPropertyName("id")] long Id,
    [property: JsonPropertyName("polymarket_id")] string PolymarketId,
    [property: JsonPropertyName("station_icao")] string? StationIcao,
    [property: JsonPropertyName("mode")] string Mode,
    [property: JsonPropertyName("clob_order_id")] string? ClobOrderId,
    [property: JsonPropertyName("side")] string Side,
    [property: JsonPropertyName("price")] double Price,
    [property: JsonPropertyName("size_usd")] double SizeUsd,
    [property: JsonPropertyName("filled_usd")] double FilledUsd,
    [property: JsonPropertyName("fee_usd")] double? FeeUsd,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("created_at")] DateTimeOffset CreatedAt,
    [property: JsonPropertyName("filled_at")] DateTimeOffset? FilledAt);

public sealed record MonitoringListItem(
    string Title,
    string Subtitle,
    string Detail,
    string Meta,
    string Status,
    string? Accent = null,
    long? MarketId = null);

public sealed record DiagnosticBarItem(
    string Title,
    string Subtitle,
    string Detail,
    string Status,
    string? Accent,
    double MagnitudePercent,
    string Metric,
    string Dimension,
    string Value);
