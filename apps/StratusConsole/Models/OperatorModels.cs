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
    [property: JsonPropertyName("clv_beat_rate")] double? ClvBeatRate);

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
    string? Accent = null);
