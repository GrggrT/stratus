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
    [property: JsonPropertyName("skip_reason")] string? SkipReason,
    [property: JsonPropertyName("benchmark_protocol")] string? BenchmarkProtocol = null,
    [property: JsonPropertyName("sample_scope")] string? SampleScope = null);

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
    [property: JsonPropertyName("mark_at")] DateTimeOffset? MarkAt,
    [property: JsonPropertyName("mark_age_seconds")] int? MarkAgeSeconds,
    [property: JsonPropertyName("mark_stale")] bool MarkStale,
    [property: JsonPropertyName("unrealized_pnl_usd")] double? UnrealizedPnlUsd,
    [property: JsonPropertyName("liquidation_price")] double? LiquidationPrice,
    [property: JsonPropertyName("liquidation_fee_usd")] double? LiquidationFeeUsd,
    [property: JsonPropertyName("liquidation_pnl_usd")] double? LiquidationPnlUsd,
    [property: JsonPropertyName("pnl_change_1m_usd")] double? PnlChange1mUsd,
    [property: JsonPropertyName("pnl_change_5m_usd")] double? PnlChange5mUsd,
    [property: JsonPropertyName("pnl_change_60m_usd")] double? PnlChange60mUsd,
    [property: JsonPropertyName("benchmark_protocol")] string? BenchmarkProtocol = null,
    [property: JsonPropertyName("sample_scope")] string? SampleScope = null);

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

public sealed record StationLifecycleDto(
    [property: JsonPropertyName("icao")] string Icao,
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("city")] string City,
    [property: JsonPropertyName("stage")] string Stage,
    [property: JsonPropertyName("active")] bool Active,
    [property: JsonPropertyName("iem_network")] string? IemNetwork,
    [property: JsonPropertyName("calibration_rows")] int CalibrationRows,
    [property: JsonPropertyName("min_calibration_samples")] int? MinCalibrationSamples,
    [property: JsonPropertyName("active_markets")] int ActiveMarkets,
    [property: JsonPropertyName("closed_paper_positions")] int ClosedPaperPositions,
    [property: JsonPropertyName("latest_observation_at")] DateTimeOffset? LatestObservationAt,
    [property: JsonPropertyName("data_ready_for_paper")] bool DataReadyForPaper,
    [property: JsonPropertyName("reliability_status")] string ReliabilityStatus = "missing",
    [property: JsonPropertyName("reliability_score")] double? ReliabilityScore = null,
    [property: JsonPropertyName("reliability_lower_bound")] double? ReliabilityLowerBound = null,
    [property: JsonPropertyName("reliability_samples")] int ReliabilitySamples = 0,
    [property: JsonPropertyName("reliability_conflicts")] int ReliabilityConflicts = 0,
    [property: JsonPropertyName("reliability_boundary_samples")] int ReliabilityBoundarySamples = 0,
    [property: JsonPropertyName("reliability_unavailable_samples")] int ReliabilityUnavailableSamples = 0,
    [property: JsonPropertyName("reliability_evaluated_at")] DateTimeOffset? ReliabilityEvaluatedAt = null,
    [property: JsonPropertyName("reliability_reasons")] IReadOnlyList<string>? ReliabilityReasons = null,
    [property: JsonPropertyName("latest_conflict_at")] DateTimeOffset? LatestConflictAt = null,
    [property: JsonPropertyName("latest_conflict_date")] DateOnly? LatestConflictDate = null,
    [property: JsonPropertyName("latest_conflict_kind")] string? LatestConflictKind = null,
    [property: JsonPropertyName("reliability_policy_hash")] string? ReliabilityPolicyHash = null,
    [property: JsonPropertyName("reliability_source_hash")] string? ReliabilitySourceHash = null,
    [property: JsonPropertyName("reliability_record_hash")] string? ReliabilityRecordHash = null,
    [property: JsonPropertyName("selection_evaluated_for_date")] DateOnly? SelectionEvaluatedForDate = null,
    [property: JsonPropertyName("resolution_cooldown_until")] DateTimeOffset? ResolutionCooldownUntil = null,
    [property: JsonPropertyName("observation_cooldown_until")] DateTimeOffset? ObservationCooldownUntil = null,
    [property: JsonPropertyName("selection_blocked")] bool SelectionBlocked = true,
    [property: JsonPropertyName("selection_reasons")] IReadOnlyList<string>? SelectionReasons = null);

public sealed record StationLifecycleResponse(
    [property: JsonPropertyName("generated_at")] DateTimeOffset GeneratedAt,
    [property: JsonPropertyName("paper_stations")] int PaperStations,
    [property: JsonPropertyName("shadow_stations")] int ShadowStations,
    [property: JsonPropertyName("approved_stations")] int ApprovedStations,
    [property: JsonPropertyName("inactive_stations")] int InactiveStations,
    [property: JsonPropertyName("items")] IReadOnlyList<StationLifecycleDto> Items);

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
    [property: JsonPropertyName("points")] IReadOnlyList<MarketPricePointDto> Points,
    [property: JsonPropertyName("benchmark_protocol")] string? BenchmarkProtocol = null,
    [property: JsonPropertyName("sample_scope")] string? SampleScope = null);

public sealed record ProbabilityPointDto(
    [property: JsonPropertyName("t")] DateTimeOffset T,
    [property: JsonPropertyName("source")] string Source,
    [property: JsonPropertyName("model_probability")] double? ModelProbability,
    [property: JsonPropertyName("market_price")] double? MarketPrice,
    [property: JsonPropertyName("edge")] double? Edge,
    [property: JsonPropertyName("sigma")] double? Sigma,
    [property: JsonPropertyName("horizon_h")] double? HorizonH,
    [property: JsonPropertyName("confidence")] string? Confidence,
    [property: JsonPropertyName("status")] string? Status,
    [property: JsonPropertyName("benchmark_protocol")] string? BenchmarkProtocol = null,
    [property: JsonPropertyName("sample_scope")] string? SampleScope = null);

public sealed record ProbabilitySeriesResponse(
    [property: JsonPropertyName("generated_at")] DateTimeOffset GeneratedAt,
    [property: JsonPropertyName("market_id")] long MarketId,
    [property: JsonPropertyName("points")] IReadOnlyList<ProbabilityPointDto> Points,
    [property: JsonPropertyName("benchmark_protocol")] string? BenchmarkProtocol = null,
    [property: JsonPropertyName("sample_scope")] string? SampleScope = null);

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
    [property: JsonPropertyName("brier_market")] double? BrierMarket = null,
    [property: JsonPropertyName("performance_sample_scope")] string? PerformanceSampleScope = null,
    [property: JsonPropertyName("benchmark_protocol")] string? BenchmarkProtocol = null,
    [property: JsonPropertyName("benchmark_sample_scope")] string? BenchmarkSampleScope = null,
    [property: JsonPropertyName("benchmark_positions")] int BenchmarkPositions = 0,
    [property: JsonPropertyName("benchmark_roi")] double? BenchmarkRoi = null,
    [property: JsonPropertyName("benchmark_roi_ci_low")] double? BenchmarkRoiCiLow = null,
    [property: JsonPropertyName("benchmark_roi_ci_high")] double? BenchmarkRoiCiHigh = null,
    [property: JsonPropertyName("robust_pnl_usd")] double? RobustPnlUsd = null,
    [property: JsonPropertyName("robust_roi")] double? RobustRoi = null,
    [property: JsonPropertyName("decomposed")] int Decomposed = 0,
    [property: JsonPropertyName("roi_ci_low")] double? RoiCiLow = null,
    [property: JsonPropertyName("roi_ci_high")] double? RoiCiHigh = null,
    [property: JsonPropertyName("skill_pnl_ci_low")] double? SkillPnlCiLow = null,
    [property: JsonPropertyName("skill_pnl_ci_high")] double? SkillPnlCiHigh = null,
    [property: JsonPropertyName("n_resolved_trades")] int ResolvedTrades = 0,
    [property: JsonPropertyName("generated_at")] DateTimeOffset? GeneratedAt = null);

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
    [property: JsonPropertyName("pipeline_version")] string? PipelineVersion,
    [property: JsonPropertyName("pipeline_resolved_trades")] int PipelineResolvedTrades,
    [property: JsonPropertyName("pipeline_brier_model")] double? PipelineBrierModel,
    [property: JsonPropertyName("pipeline_brier_market")] double? PipelineBrierMarket,
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
    [property: JsonPropertyName("gate_checks")] IReadOnlyList<GateCheckDto> GateChecks,
    [property: JsonPropertyName("benchmark_protocol")] string? BenchmarkProtocol = null,
    [property: JsonPropertyName("sample_scope")] string? SampleScope = null,
    [property: JsonPropertyName("benchmark_positions")] int BenchmarkPositions = 0,
    [property: JsonPropertyName("clv_samples")] int ClvSamples = 0);

public sealed record ProspectiveValidationPointDto(
    [property: JsonPropertyName("sequence"), JsonRequired] int Sequence,
    [property: JsonPropertyName("pipeline_cohort"), JsonRequired] string PipelineCohort,
    [property: JsonPropertyName("station_icao"), JsonRequired] string StationIcao,
    [property: JsonPropertyName("market_date"), JsonRequired] DateOnly MarketDate,
    [property: JsonPropertyName("kind"), JsonRequired] string Kind,
    [property: JsonPropertyName("recognized_at"), JsonRequired] DateTimeOffset RecognizedAt,
    [property: JsonPropertyName("orders"), JsonRequired] int Orders,
    [property: JsonPropertyName("block_pnl_usd"), JsonRequired] double BlockPnlUsd,
    [property: JsonPropertyName("block_cost_usd"), JsonRequired] double BlockCostUsd,
    [property: JsonPropertyName("block_skill_pnl_usd"), JsonRequired] double? BlockSkillPnlUsd,
    [property: JsonPropertyName("block_luck_pnl_usd"), JsonRequired] double? BlockLuckPnlUsd,
    [property: JsonPropertyName("cumulative_pnl_usd"), JsonRequired] double CumulativePnlUsd,
    [property: JsonPropertyName("cumulative_skill_pnl_usd"), JsonRequired] double? CumulativeSkillPnlUsd,
    [property: JsonPropertyName("cumulative_luck_pnl_usd"), JsonRequired] double? CumulativeLuckPnlUsd,
    [property: JsonPropertyName("equity_usd"), JsonRequired] double EquityUsd,
    [property: JsonPropertyName("drawdown_usd"), JsonRequired] double DrawdownUsd,
    [property: JsonPropertyName("max_drawdown_usd"), JsonRequired] double MaxDrawdownUsd);

public sealed record ProspectiveValidationLedgerDto(
    [property: JsonPropertyName("generated_at"), JsonRequired] DateTimeOffset GeneratedAt,
    [property: JsonPropertyName("mode"), JsonRequired] string Mode,
    [property: JsonPropertyName("benchmark_protocol"), JsonRequired] string BenchmarkProtocol,
    [property: JsonPropertyName("sample_scope"), JsonRequired] string SampleScope,
    [property: JsonPropertyName("source_integrity"), JsonRequired] string SourceIntegrity,
    [property: JsonPropertyName("registration_opened_at"), JsonRequired] DateTimeOffset RegistrationOpenedAt,
    [property: JsonPropertyName("definition_version"), JsonRequired] string DefinitionVersion,
    [property: JsonPropertyName("definition_hash"), JsonRequired] string DefinitionHash,
    [property: JsonPropertyName("cohort_scope"), JsonRequired] string CohortScope,
    [property: JsonPropertyName("requested_pipeline_cohort"), JsonRequired] string? RequestedPipelineCohort,
    [property: JsonPropertyName("current_pipeline_cohort"), JsonRequired] string? CurrentPipelineCohort,
    [property: JsonPropertyName("selected_pipeline_cohort"), JsonRequired] string? SelectedPipelineCohort,
    [property: JsonPropertyName("selected_is_current"), JsonRequired] bool SelectedIsCurrent,
    [property: JsonPropertyName("enrolled_orders"), JsonRequired] int EnrolledOrders,
    [property: JsonPropertyName("eligible_enrollments"), JsonRequired] int EligibleEnrollments,
    [property: JsonPropertyName("excluded_enrollments"), JsonRequired] int ExcludedEnrollments,
    [property: JsonPropertyName("pending_outcomes"), JsonRequired] int PendingOutcomes,
    [property: JsonPropertyName("pending_blocks"), JsonRequired] int PendingBlocks,
    [property: JsonPropertyName("closed_orders"), JsonRequired] int ClosedOrders,
    [property: JsonPropertyName("independent_blocks"), JsonRequired] int IndependentBlocks,
    [property: JsonPropertyName("initial_bankroll_usd"), JsonRequired] double InitialBankrollUsd,
    [property: JsonPropertyName("total_pnl_usd"), JsonRequired] double TotalPnlUsd,
    [property: JsonPropertyName("total_cost_usd"), JsonRequired] double TotalCostUsd,
    [property: JsonPropertyName("roi"), JsonRequired] double? Roi,
    [property: JsonPropertyName("skill_pnl_usd"), JsonRequired] double? SkillPnlUsd,
    [property: JsonPropertyName("luck_pnl_usd"), JsonRequired] double? LuckPnlUsd,
    [property: JsonPropertyName("decomposed_orders"), JsonRequired] int DecomposedOrders,
    [property: JsonPropertyName("robust_pnl_usd"), JsonRequired] double? RobustPnlUsd,
    [property: JsonPropertyName("robust_roi"), JsonRequired] double? RobustRoi,
    [property: JsonPropertyName("max_drawdown_usd"), JsonRequired] double MaxDrawdownUsd,
    [property: JsonPropertyName("max_drawdown_fraction"), JsonRequired] double MaxDrawdownFraction,
    [property: JsonPropertyName("largest_order_id"), JsonRequired] long? LargestOrderId,
    [property: JsonPropertyName("largest_order_pnl_usd"), JsonRequired] double? LargestOrderPnlUsd,
    [property: JsonPropertyName("largest_order_abs_pnl_share"), JsonRequired] double? LargestOrderAbsPnlShare,
    [property: JsonPropertyName("pnl_excluding_largest_usd"), JsonRequired] double? PnlExcludingLargestUsd,
    [property: JsonPropertyName("roi_excluding_largest"), JsonRequired] double? RoiExcludingLargest,
    [property: JsonPropertyName("points"), JsonRequired] IReadOnlyList<ProspectiveValidationPointDto> Points);

public sealed record AlertItemDto(
    [property: JsonPropertyName("id"), JsonRequired] long Id,
    [property: JsonPropertyName("event_key"), JsonRequired] string EventKey,
    [property: JsonPropertyName("category"), JsonRequired] string Category,
    [property: JsonPropertyName("payload"), JsonRequired] string Payload,
    [property: JsonPropertyName("created_at"), JsonRequired] DateTimeOffset CreatedAt,
    [property: JsonPropertyName("acknowledged_at"), JsonRequired] DateTimeOffset? AcknowledgedAt,
    [property: JsonPropertyName("sent_at"), JsonRequired] DateTimeOffset? SentAt,
    [property: JsonPropertyName("attempts"), JsonRequired] int Attempts,
    [property: JsonPropertyName("last_attempt_at"), JsonRequired] DateTimeOffset? LastAttemptAt,
    [property: JsonPropertyName("next_attempt_at"), JsonRequired] DateTimeOffset? NextAttemptAt,
    [property: JsonPropertyName("lease_until"), JsonRequired] DateTimeOffset? LeaseUntil,
    [property: JsonPropertyName("dead_lettered_at"), JsonRequired] DateTimeOffset? DeadLetteredAt,
    [property: JsonPropertyName("error"), JsonRequired] string? Error,
    [property: JsonPropertyName("external_state"), JsonRequired] string ExternalState);

public sealed record AlertsResponseDto(
    [property: JsonPropertyName("generated_at"), JsonRequired] DateTimeOffset GeneratedAt,
    [property: JsonPropertyName("query_state"), JsonRequired] string QueryState,
    [property: JsonPropertyName("total_count"), JsonRequired] int TotalCount,
    [property: JsonPropertyName("unacknowledged_count"), JsonRequired] int UnacknowledgedCount,
    [property: JsonPropertyName("oldest_unacknowledged_at"), JsonRequired] DateTimeOffset? OldestUnacknowledgedAt,
    [property: JsonPropertyName("oldest_unacknowledged_age_seconds"), JsonRequired] int? OldestUnacknowledgedAgeSeconds,
    [property: JsonPropertyName("channel"), JsonRequired] string Channel,
    [property: JsonPropertyName("channel_state"), JsonRequired] string ChannelState,
    [property: JsonPropertyName("activated_at"), JsonRequired] DateTimeOffset? ActivatedAt,
    [property: JsonPropertyName("heartbeat_at"), JsonRequired] DateTimeOffset? HeartbeatAt,
    [property: JsonPropertyName("heartbeat_stale"), JsonRequired] bool HeartbeatStale,
    [property: JsonPropertyName("last_success_at"), JsonRequired] DateTimeOffset? LastSuccessAt,
    [property: JsonPropertyName("last_failure_at"), JsonRequired] DateTimeOffset? LastFailureAt,
    [property: JsonPropertyName("last_error"), JsonRequired] string? LastError,
    [property: JsonPropertyName("pending_after_activation"), JsonRequired] int PendingAfterActivation,
    [property: JsonPropertyName("oldest_pending_after_activation_at"), JsonRequired] DateTimeOffset? OldestPendingAfterActivationAt,
    [property: JsonPropertyName("backlog_overdue"), JsonRequired] bool BacklogOverdue,
    [property: JsonPropertyName("dead_letter_count"), JsonRequired] int DeadLetterCount,
    [property: JsonPropertyName("items"), JsonRequired] IReadOnlyList<AlertItemDto> Items);

public sealed record AlertAckThroughRequestDto(
    [property: JsonPropertyName("through_id")] long ThroughId);

public sealed record AlertAcknowledgementResponseDto(
    [property: JsonPropertyName("acknowledged"), JsonRequired] int Acknowledged,
    [property: JsonPropertyName("through_id")] long? ThroughId);

public sealed record AlertInboxListItem(
    long Id,
    string Title,
    string EventKey,
    string Payload,
    string Created,
    string LocalState,
    string ExternalState,
    string DeliveryDetail,
    string ErrorDetail,
    string Status,
    string? Accent,
    string AcknowledgeLabel,
    bool CanAcknowledge);

public sealed record SelectionPolicyItemDto(
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("description")] string Description,
    [property: JsonPropertyName("is_reference")] bool IsReference,
    [property: JsonPropertyName("max_predicted_edge")] double? MaxPredictedEdge,
    [property: JsonPropertyName("opportunities")] int Opportunities,
    [property: JsonPropertyName("accepted")] int Accepted,
    [property: JsonPropertyName("retention")] double Retention,
    [property: JsonPropertyName("independent_blocks")] int IndependentBlocks,
    [property: JsonPropertyName("brier_model")] double? BrierModel,
    [property: JsonPropertyName("brier_market")] double? BrierMarket,
    [property: JsonPropertyName("model_minus_market")] double? ModelMinusMarket,
    [property: JsonPropertyName("total_pnl_usd")] double TotalPnlUsd,
    [property: JsonPropertyName("avg_realized_return")] double? AvgRealizedReturn,
    [property: JsonPropertyName("information_value")] double InformationValue,
    [property: JsonPropertyName("delta_information_value")] double DeltaInformationValue,
    [property: JsonPropertyName("delta_ci_low")] double? DeltaCiLow,
    [property: JsonPropertyName("delta_ci_high")] double? DeltaCiHigh,
    [property: JsonPropertyName("eligible")] bool Eligible);

public sealed record SelectionPolicyReportDto(
    [property: JsonPropertyName("generated_at")] DateTimeOffset GeneratedAt,
    [property: JsonPropertyName("mode")] string Mode,
    [property: JsonPropertyName("pipeline_version")] string? PipelineVersion,
    [property: JsonPropertyName("historical_opportunities")] int HistoricalOpportunities,
    [property: JsonPropertyName("historical_edge_slope")] double? HistoricalEdgeSlope,
    [property: JsonPropertyName("historical_model_minus_market")] double? HistoricalModelMinusMarket,
    [property: JsonPropertyName("recorded_opportunities")] int RecordedOpportunities,
    [property: JsonPropertyName("current_opportunities")] int CurrentOpportunities,
    [property: JsonPropertyName("candidates")] IReadOnlyList<SelectionPolicyItemDto> Candidates,
    [property: JsonPropertyName("benchmark_protocol")] string? BenchmarkProtocol = null,
    [property: JsonPropertyName("sample_scope")] string? SampleScope = null,
    [property: JsonPropertyName("historical_benchmark_protocol")] string? HistoricalBenchmarkProtocol = null,
    [property: JsonPropertyName("historical_sample_scope")] string? HistoricalSampleScope = null);

public sealed record AccuracyModelDto(
    [property: JsonPropertyName("provider")] string Provider,
    [property: JsonPropertyName("model")] string Model,
    [property: JsonPropertyName("model_version")] string ModelVersion,
    [property: JsonPropertyName("region")] string Region,
    [property: JsonPropertyName("enabled")] bool Enabled,
    [property: JsonPropertyName("shadow_only")] bool ShadowOnly,
    [property: JsonPropertyName("expected_latency_minutes")] int? ExpectedLatencyMinutes,
    [property: JsonPropertyName("source_url")] string SourceUrl,
    [property: JsonPropertyName("license_status")] string LicenseStatus);

public sealed record AccuracyDriftDto(
    [property: JsonPropertyName("station_id")] long? StationId,
    [property: JsonPropertyName("detected_at")] DateTimeOffset DetectedAt,
    [property: JsonPropertyName("window_days")] int WindowDays,
    [property: JsonPropertyName("metric")] string Metric,
    [property: JsonPropertyName("baseline_value")] double BaselineValue,
    [property: JsonPropertyName("recent_value")] double RecentValue,
    [property: JsonPropertyName("delta")] double Delta,
    [property: JsonPropertyName("severity")] string Severity);

public sealed record SettlementCalibratorDto(
    [property: JsonPropertyName("scope_key")] string ScopeKey,
    [property: JsonPropertyName("alpha")] double Alpha,
    [property: JsonPropertyName("beta")] double Beta,
    [property: JsonPropertyName("training_blocks")] int TrainingBlocks,
    [property: JsonPropertyName("training_buckets")] int TrainingBuckets,
    [property: JsonPropertyName("minimum_blocks")] int MinimumBlocks,
    [property: JsonPropertyName("baseline_brier")] double? BaselineBrier,
    [property: JsonPropertyName("calibrated_brier")] double? CalibratedBrier,
    [property: JsonPropertyName("baseline_ece")] double? BaselineEce,
    [property: JsonPropertyName("calibrated_ece")] double? CalibratedEce,
    [property: JsonPropertyName("delta_ci_low")] double? DeltaCiLow,
    [property: JsonPropertyName("delta_ci_high")] double? DeltaCiHigh,
    [property: JsonPropertyName("eligible")] bool Eligible,
    [property: JsonPropertyName("applied")] bool Applied,
    [property: JsonPropertyName("trained_through")] DateTimeOffset TrainedThrough);

public sealed record TrajectoryChallengerDto(
    [property: JsonPropertyName("stored_trajectories")] int StoredTrajectories,
    [property: JsonPropertyName("latest_available_at")] DateTimeOffset? LatestAvailableAt,
    [property: JsonPropertyName("minimum_blocks")] int MinimumBlocks,
    [property: JsonPropertyName("maximum_blocks")] int MaximumBlocks,
    [property: JsonPropertyName("evaluated_slices")] int EvaluatedSlices,
    [property: JsonPropertyName("eligible_slices")] int EligibleSlices,
    [property: JsonPropertyName("brier")] double? Brier,
    [property: JsonPropertyName("delta_brier")] double? DeltaBrier,
    [property: JsonPropertyName("ci_low")] double? CiLow,
    [property: JsonPropertyName("ci_high")] double? CiHigh,
    [property: JsonPropertyName("applied")] bool Applied);

public sealed record CensoredChallengerDto(
    [property: JsonPropertyName("current_markets")] int CurrentMarkets,
    [property: JsonPropertyName("training_blocks")] int TrainingBlocks,
    [property: JsonPropertyName("bias_c")] double? BiasC,
    [property: JsonPropertyName("sigma_scale")] double? SigmaScale,
    [property: JsonPropertyName("historical_baseline_brier")] double? HistoricalBaselineBrier,
    [property: JsonPropertyName("historical_calibrated_brier")] double? HistoricalCalibratedBrier,
    [property: JsonPropertyName("historical_ci_low")] double? HistoricalCiLow,
    [property: JsonPropertyName("historical_ci_high")] double? HistoricalCiHigh,
    [property: JsonPropertyName("historical_oof_eligible")] bool HistoricalOofEligible,
    [property: JsonPropertyName("minimum_blocks")] int MinimumBlocks,
    [property: JsonPropertyName("maximum_blocks")] int MaximumBlocks,
    [property: JsonPropertyName("evaluated_slices")] int EvaluatedSlices,
    [property: JsonPropertyName("eligible_slices")] int EligibleSlices,
    [property: JsonPropertyName("delta_brier")] double? DeltaBrier,
    [property: JsonPropertyName("ci_low")] double? CiLow,
    [property: JsonPropertyName("ci_high")] double? CiHigh,
    [property: JsonPropertyName("applied")] bool Applied);

public sealed record WeightedEnsembleDto(
    [property: JsonPropertyName("current_markets")] int CurrentMarkets,
    [property: JsonPropertyName("fitted_scopes")] int FittedScopes,
    [property: JsonPropertyName("minimum_training_sample_n")] int MinimumTrainingSampleN,
    [property: JsonPropertyName("maximum_training_sample_n")] int MaximumTrainingSampleN,
    [property: JsonPropertyName("historical_samples")] int HistoricalSamples,
    [property: JsonPropertyName("historical_blocks")] int HistoricalBlocks,
    [property: JsonPropertyName("historical_baseline_rmse_c")] double? HistoricalBaselineRmseC,
    [property: JsonPropertyName("historical_candidate_rmse_c")] double? HistoricalCandidateRmseC,
    [property: JsonPropertyName("historical_delta_mse")] double? HistoricalDeltaMse,
    [property: JsonPropertyName("historical_ci_low")] double? HistoricalCiLow,
    [property: JsonPropertyName("historical_ci_high")] double? HistoricalCiHigh,
    [property: JsonPropertyName("historical_oof_eligible")] bool HistoricalOofEligible,
    [property: JsonPropertyName("settlement_available_events")] int SettlementAvailableEvents,
    [property: JsonPropertyName("settlement_fitted_events")] int SettlementFittedEvents,
    [property: JsonPropertyName("settlement_backtest_blocks")] int SettlementBacktestBlocks,
    [property: JsonPropertyName("settlement_backtest_market_rows")] int SettlementBacktestMarketRows,
    [property: JsonPropertyName("settlement_baseline_brier")] double? SettlementBaselineBrier,
    [property: JsonPropertyName("settlement_candidate_brier")] double? SettlementCandidateBrier,
    [property: JsonPropertyName("settlement_delta_brier")] double? SettlementDeltaBrier,
    [property: JsonPropertyName("settlement_ci_low")] double? SettlementCiLow,
    [property: JsonPropertyName("settlement_ci_high")] double? SettlementCiHigh,
    [property: JsonPropertyName("settlement_aligned_eligible")] bool SettlementAlignedEligible,
    [property: JsonPropertyName("settlement_empirical_candidate_brier")] double? SettlementEmpiricalCandidateBrier,
    [property: JsonPropertyName("settlement_empirical_delta_brier")] double? SettlementEmpiricalDeltaBrier,
    [property: JsonPropertyName("settlement_empirical_ci_low")] double? SettlementEmpiricalCiLow,
    [property: JsonPropertyName("settlement_empirical_ci_high")] double? SettlementEmpiricalCiHigh,
    [property: JsonPropertyName("settlement_shrunk_empirical_weight")] double SettlementShrunkEmpiricalWeight,
    [property: JsonPropertyName("settlement_shrunk_empirical_brier")] double? SettlementShrunkEmpiricalBrier,
    [property: JsonPropertyName("settlement_shrunk_empirical_delta_brier")] double? SettlementShrunkEmpiricalDeltaBrier,
    [property: JsonPropertyName("settlement_shrunk_empirical_ci_low")] double? SettlementShrunkEmpiricalCiLow,
    [property: JsonPropertyName("settlement_shrunk_empirical_ci_high")] double? SettlementShrunkEmpiricalCiHigh,
    [property: JsonPropertyName("settlement_shrunk_empirical_eligible")] bool SettlementShrunkEmpiricalEligible,
    [property: JsonPropertyName("minimum_settlement_backtest_blocks")] int MinimumSettlementBacktestBlocks,
    [property: JsonPropertyName("shrunk_current_markets")] int ShrunkCurrentMarkets,
    [property: JsonPropertyName("shrunk_legacy_markets")] int ShrunkLegacyMarkets,
    [property: JsonPropertyName("shrunk_expert_version")] string ShrunkExpertVersion,
    [property: JsonPropertyName("shrunk_evidence_protocol")] string ShrunkEvidenceProtocol,
    [property: JsonPropertyName("shrunk_latest_generated_at")] DateTimeOffset? ShrunkLatestGeneratedAt,
    [property: JsonPropertyName("shrunk_minimum_blocks")] int ShrunkMinimumBlocks,
    [property: JsonPropertyName("shrunk_maximum_blocks")] int ShrunkMaximumBlocks,
    [property: JsonPropertyName("shrunk_evaluated_slices")] int ShrunkEvaluatedSlices,
    [property: JsonPropertyName("shrunk_eligible_slices")] int ShrunkEligibleSlices,
    [property: JsonPropertyName("shrunk_delta_brier")] double? ShrunkDeltaBrier,
    [property: JsonPropertyName("shrunk_ci_low")] double? ShrunkCiLow,
    [property: JsonPropertyName("shrunk_ci_high")] double? ShrunkCiHigh,
    [property: JsonPropertyName("weights")] IReadOnlyDictionary<string, double> Weights,
    [property: JsonPropertyName("minimum_blocks")] int MinimumBlocks,
    [property: JsonPropertyName("maximum_blocks")] int MaximumBlocks,
    [property: JsonPropertyName("evaluated_slices")] int EvaluatedSlices,
    [property: JsonPropertyName("eligible_slices")] int EligibleSlices,
    [property: JsonPropertyName("delta_brier")] double? DeltaBrier,
    [property: JsonPropertyName("ci_low")] double? CiLow,
    [property: JsonPropertyName("ci_high")] double? CiHigh,
    [property: JsonPropertyName("applied")] bool Applied);

public sealed record ConformalGuardrailDto(
    [property: JsonPropertyName("residual_samples")] int ResidualSamples,
    [property: JsonPropertyName("scale")] double? Scale,
    [property: JsonPropertyName("target_coverage")] double TargetCoverage,
    [property: JsonPropertyName("empirical_coverage_before")] double? EmpiricalCoverageBefore,
    [property: JsonPropertyName("empirical_coverage_after")] double? EmpiricalCoverageAfter,
    [property: JsonPropertyName("minimum_blocks")] int MinimumBlocks,
    [property: JsonPropertyName("maximum_blocks")] int MaximumBlocks,
    [property: JsonPropertyName("evaluated_slices")] int EvaluatedSlices,
    [property: JsonPropertyName("eligible_slices")] int EligibleSlices,
    [property: JsonPropertyName("delta_brier")] double? DeltaBrier,
    [property: JsonPropertyName("ci_low")] double? CiLow,
    [property: JsonPropertyName("ci_high")] double? CiHigh,
    [property: JsonPropertyName("applied")] bool Applied);

public sealed record NowcastChallengerDto(
    [property: JsonPropertyName("current_markets")] int CurrentMarkets,
    [property: JsonPropertyName("recent_bias_c")] double? RecentBiasC,
    [property: JsonPropertyName("extreme_shift_c")] double? ExtremeShiftC,
    [property: JsonPropertyName("matched_hours")] int MatchedHours,
    [property: JsonPropertyName("latest_observed_hour")] string? LatestObservedHour,
    [property: JsonPropertyName("minimum_blocks")] int MinimumBlocks,
    [property: JsonPropertyName("maximum_blocks")] int MaximumBlocks,
    [property: JsonPropertyName("evaluated_slices")] int EvaluatedSlices,
    [property: JsonPropertyName("eligible_slices")] int EligibleSlices,
    [property: JsonPropertyName("delta_brier")] double? DeltaBrier,
    [property: JsonPropertyName("ci_low")] double? CiLow,
    [property: JsonPropertyName("ci_high")] double? CiHigh,
    [property: JsonPropertyName("applied")] bool Applied);

public sealed record HierarchicalChallengerDto(
    [property: JsonPropertyName("current_markets")] int CurrentMarkets,
    [property: JsonPropertyName("local_weight")] double? LocalWeight,
    [property: JsonPropertyName("local_sample_n")] int LocalSampleN,
    [property: JsonPropertyName("peer_sample_n")] int PeerSampleN,
    [property: JsonPropertyName("peer_stations")] int PeerStations,
    [property: JsonPropertyName("minimum_blocks")] int MinimumBlocks,
    [property: JsonPropertyName("maximum_blocks")] int MaximumBlocks,
    [property: JsonPropertyName("evaluated_slices")] int EvaluatedSlices,
    [property: JsonPropertyName("eligible_slices")] int EligibleSlices,
    [property: JsonPropertyName("delta_brier")] double? DeltaBrier,
    [property: JsonPropertyName("ci_low")] double? CiLow,
    [property: JsonPropertyName("ci_high")] double? CiHigh,
    [property: JsonPropertyName("applied")] bool Applied);

public sealed record RegimeChallengerDto(
    [property: JsonPropertyName("active_shifts")] int ActiveShifts,
    [property: JsonPropertyName("critical_shifts")] int CriticalShifts,
    [property: JsonPropertyName("current_markets")] int CurrentMarkets,
    [property: JsonPropertyName("strongest_adjustment_c")] double? StrongestAdjustmentC,
    [property: JsonPropertyName("strongest_model")] string? StrongestModel,
    [property: JsonPropertyName("model_version")] string? ModelVersion,
    [property: JsonPropertyName("boundary_kind")] string? BoundaryKind,
    [property: JsonPropertyName("boundary_date")] string? BoundaryDate,
    [property: JsonPropertyName("latest_detected_at")] DateTimeOffset? LatestDetectedAt,
    [property: JsonPropertyName("minimum_blocks")] int MinimumBlocks,
    [property: JsonPropertyName("maximum_blocks")] int MaximumBlocks,
    [property: JsonPropertyName("evaluated_slices")] int EvaluatedSlices,
    [property: JsonPropertyName("eligible_slices")] int EligibleSlices,
    [property: JsonPropertyName("delta_brier")] double? DeltaBrier,
    [property: JsonPropertyName("ci_low")] double? CiLow,
    [property: JsonPropertyName("ci_high")] double? CiHigh,
    [property: JsonPropertyName("applied")] bool Applied);

public sealed record NonlinearExpertDto(
    [property: JsonPropertyName("expert")] string Expert,
    [property: JsonPropertyName("current_markets")] int CurrentMarkets,
    [property: JsonPropertyName("training_sample_n")] int TrainingSampleN,
    [property: JsonPropertyName("maximum_blocks")] int MaximumBlocks,
    [property: JsonPropertyName("evaluated_slices")] int EvaluatedSlices,
    [property: JsonPropertyName("eligible_slices")] int EligibleSlices,
    [property: JsonPropertyName("delta_brier")] double? DeltaBrier,
    [property: JsonPropertyName("ci_low")] double? CiLow,
    [property: JsonPropertyName("ci_high")] double? CiHigh,
    [property: JsonPropertyName("applied")] bool Applied);

public sealed record NonlinearChallengerDto(
    [property: JsonPropertyName("minimum_training_samples")] int MinimumTrainingSamples,
    [property: JsonPropertyName("minimum_blocks")] int MinimumBlocks,
    [property: JsonPropertyName("experts")] IReadOnlyList<NonlinearExpertDto> Experts);

public sealed record StackChallengerDto(
    [property: JsonPropertyName("minimum_training_samples")] int MinimumTrainingSamples,
    [property: JsonPropertyName("minimum_blocks")] int MinimumBlocks,
    [property: JsonPropertyName("current_markets")] int CurrentMarkets,
    [property: JsonPropertyName("training_sample_n")] int TrainingSampleN,
    [property: JsonPropertyName("fit_source")] string? FitSource,
    [property: JsonPropertyName("local_sample_n")] int LocalSampleN,
    [property: JsonPropertyName("peer_sample_n")] int PeerSampleN,
    [property: JsonPropertyName("peer_stations")] int PeerStations,
    [property: JsonPropertyName("maximum_blocks")] int MaximumBlocks,
    [property: JsonPropertyName("evaluated_slices")] int EvaluatedSlices,
    [property: JsonPropertyName("eligible_slices")] int EligibleSlices,
    [property: JsonPropertyName("delta_brier")] double? DeltaBrier,
    [property: JsonPropertyName("ci_low")] double? CiLow,
    [property: JsonPropertyName("ci_high")] double? CiHigh,
    [property: JsonPropertyName("applied")] bool Applied);

public sealed record AccuracyPromotionTrialDto(
    [property: JsonPropertyName("expert")] string Expert,
    [property: JsonPropertyName("station_id")] long StationId,
    [property: JsonPropertyName("station_icao")] string StationIcao,
    [property: JsonPropertyName("horizon_bucket")] string HorizonBucket,
    [property: JsonPropertyName("kind")] string Kind,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("evidence_blocks")] int EvidenceBlocks,
    [property: JsonPropertyName("target_blocks")] int TargetBlocks,
    [property: JsonPropertyName("mean_delta_brier")] double? MeanDeltaBrier,
    [property: JsonPropertyName("wins")] int? Wins,
    [property: JsonPropertyName("losses")] int? Losses,
    [property: JsonPropertyName("ties")] int? Ties,
    [property: JsonPropertyName("confidence_radius")] double? ConfidenceRadius,
    [property: JsonPropertyName("mean_improvement_lcb")] double? MeanImprovementLcb,
    [property: JsonPropertyName("one_sided_p_bound")] double? OneSidedPBound,
    [property: JsonPropertyName("allocated_alpha")] double AllocatedAlpha,
    [property: JsonPropertyName("activated")] bool Activated);

public sealed record AccuracyPromotionOverviewDto(
    [property: JsonPropertyName("protocol_version")] string ProtocolVersion,
    [property: JsonPropertyName("registration_epoch")] string RegistrationEpoch,
    [property: JsonPropertyName("test_method")] string TestMethod,
    [property: JsonPropertyName("family_alpha")] double FamilyAlpha,
    [property: JsonPropertyName("family_size")] int FamilySize,
    [property: JsonPropertyName("registered_trials")] int RegisteredTrials,
    [property: JsonPropertyName("collecting_trials")] int CollectingTrials,
    [property: JsonPropertyName("passed_trials")] int PassedTrials,
    [property: JsonPropertyName("failed_trials")] int FailedTrials,
    [property: JsonPropertyName("activated_trials")] int ActivatedTrials,
    [property: JsonPropertyName("maximum_evidence_blocks")] int MaximumEvidenceBlocks,
    [property: JsonPropertyName("target_blocks")] int TargetBlocks,
    [property: JsonPropertyName("activation_manifest_hash")] string ActivationManifestHash,
    [property: JsonPropertyName("trials")] IReadOnlyList<AccuracyPromotionTrialDto> Trials);

public sealed record AccuracyOverviewDto(
    [property: JsonPropertyName("generated_at")] DateTimeOffset GeneratedAt,
    [property: JsonPropertyName("resolution_contracts")] int ResolutionContracts,
    [property: JsonPropertyName("licensed_contracts")] int LicensedContracts,
    [property: JsonPropertyName("exact_settlement_actuals")] int ExactSettlementActuals,
    [property: JsonPropertyName("forecast_evaluations")] int ForecastEvaluations,
    [property: JsonPropertyName("independent_samples")] int IndependentSamples,
    [property: JsonPropertyName("mean_model_brier")] double? MeanModelBrier,
    [property: JsonPropertyName("mean_market_brier")] double? MeanMarketBrier,
    [property: JsonPropertyName("qualified_skill_reports")] int QualifiedSkillReports,
    [property: JsonPropertyName("open_drift_events")] int OpenDriftEvents,
    [property: JsonPropertyName("active_stack_experts")] int ActiveStackExperts,
    [property: JsonPropertyName("promotion")] AccuracyPromotionOverviewDto? Promotion,
    [property: JsonPropertyName("settlement_calibrator")] SettlementCalibratorDto? SettlementCalibrator,
    [property: JsonPropertyName("censored_challenger")] CensoredChallengerDto CensoredChallenger,
    [property: JsonPropertyName("weighted_ensemble")] WeightedEnsembleDto WeightedEnsemble,
    [property: JsonPropertyName("trajectory_challenger")] TrajectoryChallengerDto TrajectoryChallenger,
    [property: JsonPropertyName("conformal_guardrail")] ConformalGuardrailDto ConformalGuardrail,
    [property: JsonPropertyName("nowcast_challenger")] NowcastChallengerDto NowcastChallenger,
    [property: JsonPropertyName("hierarchical_challenger")] HierarchicalChallengerDto HierarchicalChallenger,
    [property: JsonPropertyName("regime_challenger")] RegimeChallengerDto RegimeChallenger,
    [property: JsonPropertyName("stack_challenger")] StackChallengerDto StackChallenger,
    [property: JsonPropertyName("nonlinear_challengers")] NonlinearChallengerDto NonlinearChallengers,
    [property: JsonPropertyName("models")] IReadOnlyList<AccuracyModelDto> Models,
    [property: JsonPropertyName("drift")] IReadOnlyList<AccuracyDriftDto> Drift,
    [property: JsonPropertyName("benchmark_protocol")] string? BenchmarkProtocol = null,
    [property: JsonPropertyName("sample_scope")] string? SampleScope = null);

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
    [property: JsonPropertyName("slices")] IReadOnlyList<BrierSliceDto> Slices,
    [property: JsonPropertyName("benchmark_protocol")] string? BenchmarkProtocol = null,
    [property: JsonPropertyName("sample_scope")] string? SampleScope = null);

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
    [property: JsonPropertyName("slices")] IReadOnlyList<ClvSliceDto> Slices,
    [property: JsonPropertyName("benchmark_protocol")] string? BenchmarkProtocol = null,
    [property: JsonPropertyName("sample_scope")] string? SampleScope = null);

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
    [property: JsonPropertyName("resolved_outcome")] bool? ResolvedOutcome,
    [property: JsonPropertyName("benchmark_protocol")] string? BenchmarkProtocol = null);

public sealed record RootCauseDrilldownDto(
    [property: JsonPropertyName("generated_at")] DateTimeOffset GeneratedAt,
    [property: JsonPropertyName("mode")] string Mode,
    [property: JsonPropertyName("metric")] string Metric,
    [property: JsonPropertyName("dimension")] string Dimension,
    [property: JsonPropertyName("value")] string Value,
    [property: JsonPropertyName("count")] int Count,
    [property: JsonPropertyName("items")] IReadOnlyList<RootCauseTradeDto> Items,
    [property: JsonPropertyName("benchmark_protocol")] string? BenchmarkProtocol = null,
    [property: JsonPropertyName("sample_scope")] string? SampleScope = null);

public sealed record RootCauseActionDto(
    [property: JsonPropertyName("metric")] string Metric,
    [property: JsonPropertyName("dimension")] string Dimension,
    [property: JsonPropertyName("value")] string Value,
    [property: JsonPropertyName("count")] int Count,
    [property: JsonPropertyName("action")] string Action,
    [property: JsonPropertyName("severity")] string Severity,
    [property: JsonPropertyName("detail")] string Detail);

public sealed record RootCauseActionsDto(
    [property: JsonPropertyName("generated_at")] DateTimeOffset GeneratedAt,
    [property: JsonPropertyName("mode")] string Mode,
    [property: JsonPropertyName("items")] IReadOnlyList<RootCauseActionDto> Items,
    [property: JsonPropertyName("benchmark_protocol")] string? BenchmarkProtocol = null,
    [property: JsonPropertyName("sample_scope")] string? SampleScope = null);

public sealed record ResolutionReconciliationItemDto(
    [property: JsonPropertyName("market_id")] long MarketId,
    [property: JsonPropertyName("station_icao")] string? StationIcao,
    [property: JsonPropertyName("market_date")] DateOnly MarketDate,
    [property: JsonPropertyName("kind")] string Kind,
    [property: JsonPropertyName("classification")] string Classification,
    [property: JsonPropertyName("boundary_margin_c")] double? BoundaryMarginC,
    [property: JsonPropertyName("exact_truth_gap_c")] double? ExactTruthGapC,
    [property: JsonPropertyName("checked_at")] DateTimeOffset CheckedAt);

public sealed record ResolutionReconciliationResponseDto(
    [property: JsonPropertyName("generated_at")] DateTimeOffset GeneratedAt,
    [property: JsonPropertyName("total")] int Total,
    [property: JsonPropertyName("classification_counts")] IReadOnlyDictionary<string, int> ClassificationCounts,
    [property: JsonPropertyName("items")] IReadOnlyList<ResolutionReconciliationItemDto> Items,
    [property: JsonPropertyName("metar_available"), JsonRequired] int MetarAvailable,
    [property: JsonPropertyName("metar_consistent"), JsonRequired] int MetarConsistent,
    [property: JsonPropertyName("exact_available"), JsonRequired] int ExactAvailable,
    [property: JsonPropertyName("exact_source_hash_available"), JsonRequired] int ExactSourceHashAvailable,
    [property: JsonPropertyName("exact_coverage"), JsonRequired] double ExactCoverage,
    [property: JsonPropertyName("latest_checked_at"), JsonRequired] DateTimeOffset? LatestCheckedAt,
    [property: JsonPropertyName("oldest_checked_at"), JsonRequired] DateTimeOffset? OldestCheckedAt,
    [property: JsonPropertyName("stale_records"), JsonRequired] int StaleRecords,
    [property: JsonPropertyName("fresh"), JsonRequired] bool Fresh,
    [property: JsonPropertyName("stale_after_seconds"), JsonRequired] int StaleAfterSeconds);

public sealed record SettlementCaseItemDto(
    [property: JsonPropertyName("event_id")] string EventId,
    [property: JsonPropertyName("classification")] string Classification,
    [property: JsonPropertyName("reason_code")] string ReasonCode,
    [property: JsonPropertyName("age_seconds")] int AgeSeconds,
    [property: JsonPropertyName("attempt_count")] int AttemptCount,
    [property: JsonPropertyName("last_error")] string? LastError,
    [property: JsonPropertyName("last_checked_at")] DateTimeOffset LastCheckedAt);

public sealed record SettlementCasesResponseDto(
    [property: JsonPropertyName("generated_at")] DateTimeOffset GeneratedAt,
    [property: JsonPropertyName("total")] int Total,
    [property: JsonPropertyName("classification_counts")] IReadOnlyDictionary<string, int> ClassificationCounts,
    [property: JsonPropertyName("items")] IReadOnlyList<SettlementCaseItemDto> Items,
    [property: JsonPropertyName("open_total"), JsonRequired] int OpenTotal,
    [property: JsonPropertyName("oldest_open_at"), JsonRequired] DateTimeOffset? OldestOpenAt,
    [property: JsonPropertyName("latest_checked_at"), JsonRequired] DateTimeOffset? LatestCheckedAt,
    [property: JsonPropertyName("stale_open_cases"), JsonRequired] int StaleOpenCases,
    [property: JsonPropertyName("fresh"), JsonRequired] bool Fresh,
    [property: JsonPropertyName("stale_after_seconds"), JsonRequired] int StaleAfterSeconds);

public sealed record StorageCapacityDto(
    [property: JsonPropertyName("generated_at")] DateTimeOffset GeneratedAt,
    [property: JsonPropertyName("sample_at")] DateTimeOffset? SampleAt,
    [property: JsonPropertyName("sample_age_seconds")] double? SampleAgeSeconds,
    [property: JsonPropertyName("sample_fresh")] bool SampleFresh,
    [property: JsonPropertyName("severity")] string Severity,
    [property: JsonPropertyName("reason")] string Reason,
    [property: JsonPropertyName("measurement_complete")] bool MeasurementComplete,
    [property: JsonPropertyName("observed_hours")] double? ObservedHours,
    [property: JsonPropertyName("database_bytes")] long? DatabaseBytes,
    [property: JsonPropertyName("ledger_bytes")] long? LedgerBytes,
    [property: JsonPropertyName("clob_batch_count")] long? ClobBatchCount,
    [property: JsonPropertyName("clob_snapshot_count")] long? ClobSnapshotCount,
    [property: JsonPropertyName("free_disk_bytes")] long? FreeDiskBytes,
    [property: JsonPropertyName("minimum_free_disk_bytes")] long MinimumFreeDiskBytes,
    [property: JsonPropertyName("growth_bytes_per_day")] double? GrowthBytesPerDay,
    [property: JsonPropertyName("projected_ledger_bytes")] double? ProjectedLedgerBytes,
    [property: JsonPropertyName("days_to_budget")] double? DaysToBudget,
    [property: JsonPropertyName("budget_bytes")] long BudgetBytes,
    [property: JsonPropertyName("policy_version")] string PolicyVersion,
    [property: JsonPropertyName("automatic_deletion")] bool AutomaticDeletion,
    [property: JsonPropertyName("retention_rule")] string RetentionRule);

public sealed record ForecastProvenanceCollectorDto(
    [property: JsonPropertyName("collector")] string Collector,
    [property: JsonPropertyName("request_model")] string RequestModel,
    [property: JsonPropertyName("ingests")] int Ingests,
    [property: JsonPropertyName("stations")] int Stations,
    [property: JsonPropertyName("responses")] int Responses,
    [property: JsonPropertyName("raw_bytes")] long RawBytes,
    [property: JsonPropertyName("verified_upstream_inits")] int VerifiedUpstreamInits,
    [property: JsonPropertyName("latest_first_seen_at")] DateTimeOffset? LatestFirstSeenAt);

public sealed record ForecastProvenanceDto(
    [property: JsonPropertyName("generated_at")] DateTimeOffset GeneratedAt,
    [property: JsonPropertyName("state")] string State,
    [property: JsonPropertyName("source_integrity")] string SourceIntegrity,
    [property: JsonPropertyName("opened_at")] DateTimeOffset OpenedAt,
    [property: JsonPropertyName("definition_version")] string DefinitionVersion,
    [property: JsonPropertyName("definition_hash")] string DefinitionHash,
    [property: JsonPropertyName("ingests")] int Ingests,
    [property: JsonPropertyName("stations")] int Stations,
    [property: JsonPropertyName("responses")] int Responses,
    [property: JsonPropertyName("raw_bytes")] long RawBytes,
    [property: JsonPropertyName("verified_upstream_inits")] int VerifiedUpstreamInits,
    [property: JsonPropertyName("single_run_ingests")] int SingleRunIngests,
    [property: JsonPropertyName("pipeline_fingerprints")] int PipelineFingerprints,
    [property: JsonPropertyName("materialized_forecasts")] int MaterializedForecasts,
    [property: JsonPropertyName("materialized_trajectories")] int MaterializedTrajectories,
    [property: JsonPropertyName("latest_first_seen_at")] DateTimeOffset? LatestFirstSeenAt,
    [property: JsonPropertyName("latest_age_seconds")] double? LatestAgeSeconds,
    [property: JsonPropertyName("latest_active_probabilities")] int LatestActiveProbabilities,
    [property: JsonPropertyName("complete_active_probabilities")] int CompleteActiveProbabilities,
    [property: JsonPropertyName("average_http_latency_ms")] double? AverageHttpLatencyMs,
    [property: JsonPropertyName("p95_http_latency_ms")] double? P95HttpLatencyMs,
    [property: JsonPropertyName("average_ingestion_lag_ms")] double? AverageIngestionLagMs,
    [property: JsonPropertyName("p95_ingestion_lag_ms")] double? P95IngestionLagMs,
    [property: JsonPropertyName("average_verified_upstream_latency_minutes")] double? AverageVerifiedUpstreamLatencyMinutes,
    [property: JsonPropertyName("p95_verified_upstream_latency_minutes")] double? P95VerifiedUpstreamLatencyMinutes,
    [property: JsonPropertyName("collectors")] IReadOnlyList<ForecastProvenanceCollectorDto> Collectors);

public sealed record OfficialResolutionSourcesDto(
    [property: JsonPropertyName("generated_at")] DateTimeOffset GeneratedAt,
    [property: JsonPropertyName("state")] string State,
    [property: JsonPropertyName("source_integrity")] string SourceIntegrity,
    [property: JsonPropertyName("artifact_import_enabled")] bool ArtifactImportEnabled,
    [property: JsonPropertyName("opened_at")] DateTimeOffset OpenedAt,
    [property: JsonPropertyName("definition_version")] string DefinitionVersion,
    [property: JsonPropertyName("definition_hash")] string DefinitionHash,
    [property: JsonPropertyName("license_protocol")] string LicenseProtocol,
    [property: JsonPropertyName("response_protocol")] string ResponseProtocol,
    [property: JsonPropertyName("extraction_protocol")] string ExtractionProtocol,
    [property: JsonPropertyName("active_contracts")] int ActiveContracts,
    [property: JsonPropertyName("licensed_contracts")] int LicensedContracts,
    [property: JsonPropertyName("expired_contracts")] int ExpiredContracts,
    [property: JsonPropertyName("blocked_contracts")] int BlockedContracts,
    [property: JsonPropertyName("license_attestations")] int LicenseAttestations,
    [property: JsonPropertyName("source_responses")] int SourceResponses,
    [property: JsonPropertyName("exact_actuals")] int ExactActuals,
    [property: JsonPropertyName("projected_settlement_actuals")] int ProjectedSettlementActuals,
    [property: JsonPropertyName("exact_coverage")] double? ExactCoverage,
    [property: JsonPropertyName("raw_response_bytes")] long RawResponseBytes,
    [property: JsonPropertyName("latest_response_at")] DateTimeOffset? LatestResponseAt,
    [property: JsonPropertyName("latest_exact_actual_at")] DateTimeOffset? LatestExactActualAt);

public sealed record EvidenceContinuityEpochDto(
    [property: JsonPropertyName("id")] long Id,
    [property: JsonPropertyName("opened_at")] DateTimeOffset OpenedAt,
    [property: JsonPropertyName("pipeline_version")] string PipelineVersion,
    [property: JsonPropertyName("material_fingerprint")] string MaterialFingerprint,
    [property: JsonPropertyName("activation_manifest_hash")] string ActivationManifestHash,
    [property: JsonPropertyName("cohort")] string Cohort,
    [property: JsonPropertyName("image_digest")] string ImageDigest,
    [property: JsonPropertyName("plan_id")] long PlanId,
    [property: JsonPropertyName("previous_epoch_id")] long? PreviousEpochId,
    [property: JsonPropertyName("approval_reason")] string ApprovalReason,
    [property: JsonPropertyName("plan_hash")] string PlanHash,
    [property: JsonPropertyName("record_hash")] string RecordHash);

public sealed record EvidenceContinuitySkipReasonDto(
    [property: JsonPropertyName("reason")] string Reason,
    [property: JsonPropertyName("count")] int Count);

public sealed record EvidenceContinuityLaneDto(
    [property: JsonPropertyName("cohort")] string Cohort,
    [property: JsonPropertyName("epoch_id")] long? EpochId,
    [property: JsonPropertyName("scope")] string Scope,
    [property: JsonPropertyName("opened_at")] DateTimeOffset? OpenedAt,
    [property: JsonPropertyName("probability_rows")] int ProbabilityRows,
    [property: JsonPropertyName("materialized_events")] int MaterializedEvents,
    [property: JsonPropertyName("coherent_events")] int CoherentEvents,
    [property: JsonPropertyName("execution_intents")] int ExecutionIntents,
    [property: JsonPropertyName("filled_orders")] int FilledOrders,
    [property: JsonPropertyName("no_fill_orders")] int NoFillOrders,
    [property: JsonPropertyName("open_orders")] int OpenOrders,
    [property: JsonPropertyName("cost_seals")] int CostSeals,
    [property: JsonPropertyName("eligible_cost_seals")] int EligibleCostSeals,
    [property: JsonPropertyName("required_marks")] int RequiredMarks,
    [property: JsonPropertyName("observed_marks")] int ObservedMarks,
    [property: JsonPropertyName("enrollments")] int Enrollments,
    [property: JsonPropertyName("eligible_enrollments")] int EligibleEnrollments,
    [property: JsonPropertyName("outcomes")] int Outcomes,
    [property: JsonPropertyName("independent_blocks")] int IndependentBlocks,
    [property: JsonPropertyName("first_evidence_at")] DateTimeOffset? FirstEvidenceAt,
    [property: JsonPropertyName("last_evidence_at")] DateTimeOffset? LastEvidenceAt);

public sealed record EvidenceContinuityControlLaneDto(
    [property: JsonPropertyName("id")] long Id,
    [property: JsonPropertyName("epoch_id")] long EpochId,
    [property: JsonPropertyName("lane_name")] string LaneName,
    [property: JsonPropertyName("action")] string Action,
    [property: JsonPropertyName("identity_hash")] string IdentityHash,
    [property: JsonPropertyName("parent_lane_id")] long? ParentLaneId,
    [property: JsonPropertyName("boundary_at")] DateTimeOffset BoundaryAt,
    [property: JsonPropertyName("manifest")] Dictionary<string, object> Manifest,
    [property: JsonPropertyName("rationale")] string Rationale,
    [property: JsonPropertyName("record_hash")] string RecordHash);

public sealed record EvidenceDueCountsDto(
    [property: JsonPropertyName("waiting_for_due")] int WaitingForDue,
    [property: JsonPropertyName("due_pending")] int DuePending,
    [property: JsonPropertyName("overdue")] int Overdue,
    [property: JsonPropertyName("classified")] int Classified,
    [property: JsonPropertyName("total")] int Total);

public sealed record EvidenceCollectionWindowDto(
    [property: JsonPropertyName("started_at")] DateTimeOffset? StartedAt,
    [property: JsonPropertyName("ended_at")] DateTimeOffset? EndedAt,
    [property: JsonPropertyName("latest_complete_day_ended_at")] DateTimeOffset? LatestCompleteDayEndedAt,
    [property: JsonPropertyName("candidate_full_days")] int CandidateFullDays,
    [property: JsonPropertyName("maintenance_excluded_days")] int MaintenanceExcludedDays,
    [property: JsonPropertyName("complete_days")] int CompleteDays,
    [property: JsonPropertyName("waiting_for_due_days")] int WaitingForDueDays,
    [property: JsonPropertyName("due_pending_days")] int DuePendingDays,
    [property: JsonPropertyName("collecting_on_schedule_days")] int CollectingOnScheduleDays,
    [property: JsonPropertyName("overdue_days")] int OverdueDays,
    [property: JsonPropertyName("stalled_days")] int StalledDays,
    [property: JsonPropertyName("missing_scheduler_jobs")] IReadOnlyList<string> MissingSchedulerJobs,
    [property: JsonPropertyName("due_counts")] EvidenceDueCountsDto DueCounts);

public sealed record EvidenceContinuityDto(
    [property: JsonPropertyName("generated_at")] DateTimeOffset GeneratedAt,
    [property: JsonPropertyName("state")] string State,
    [property: JsonPropertyName("candidate_cohort")] string CandidateCohort,
    [property: JsonPropertyName("candidate_image_digest")] string CandidateImageDigest,
    [property: JsonPropertyName("candidate_release_manifest_hash")] string? CandidateReleaseManifestHash,
    [property: JsonPropertyName("active_cohort")] string? ActiveCohort,
    [property: JsonPropertyName("active_release_manifest_hash")] string? ActiveReleaseManifestHash,
    [property: JsonPropertyName("cohort_match")] bool CohortMatch,
    [property: JsonPropertyName("image_match")] bool ImageMatch,
    [property: JsonPropertyName("execution_gate_allowed")] bool ExecutionGateAllowed,
    [property: JsonPropertyName("execution_gate_reason")] string ExecutionGateReason,
    [property: JsonPropertyName("lane_identity_mismatches")] IReadOnlyList<string> LaneIdentityMismatches,
    [property: JsonPropertyName("frozen_lanes")] IReadOnlyList<string> FrozenLanes,
    [property: JsonPropertyName("active_epoch")] EvidenceContinuityEpochDto? ActiveEpoch,
    [property: JsonPropertyName("epoch_count")] int EpochCount,
    [property: JsonPropertyName("cohort_changes_30d")] int CohortChanges30d,
    [property: JsonPropertyName("current_bottleneck")] string CurrentBottleneck,
    [property: JsonPropertyName("current_bottleneck_detail")] string CurrentBottleneckDetail,
    [property: JsonPropertyName("liveness")] string Liveness,
    [property: JsonPropertyName("eta_reason")] string? EtaReason,
    [property: JsonPropertyName("collection_window")] EvidenceCollectionWindowDto CollectionWindow,
    [property: JsonPropertyName("complete_days")] int CompleteDays,
    [property: JsonPropertyName("outcome_velocity_per_day")] double? OutcomeVelocityPerDay,
    [property: JsonPropertyName("eta_100_at")] DateTimeOffset? Eta100At,
    [property: JsonPropertyName("eta_200_at")] DateTimeOffset? Eta200At,
    [property: JsonPropertyName("paper_stations")] int PaperStations,
    [property: JsonPropertyName("shadow_stations")] int ShadowStations,
    [property: JsonPropertyName("open_positions")] int OpenPositions,
    [property: JsonPropertyName("open_exposure_usd")] double OpenExposureUsd,
    [property: JsonPropertyName("bankroll_usd")] double BankrollUsd,
    [property: JsonPropertyName("signal_rows_since_epoch")] int SignalRowsSinceEpoch,
    [property: JsonPropertyName("pending_signals")] int PendingSignals,
    [property: JsonPropertyName("top_skip_reasons")] IReadOnlyList<EvidenceContinuitySkipReasonDto> TopSkipReasons,
    [property: JsonPropertyName("continuity_lanes")] IReadOnlyList<EvidenceContinuityControlLaneDto> ContinuityLanes,
    [property: JsonPropertyName("lanes")] IReadOnlyList<EvidenceContinuityLaneDto> Lanes);

public sealed record EvidenceFunnelItem(
    string Stage,
    string Value,
    string Detail,
    string Accent);

public sealed record ExecutionCostCoverageDto(
    [property: JsonPropertyName("known")] int Known,
    [property: JsonPropertyName("total")] int Total,
    [property: JsonPropertyName("ratio")] double? Ratio);

public sealed record ExecutionCostMetricsDto(
    [property: JsonPropertyName("eligible_orders")] int EligibleOrders,
    [property: JsonPropertyName("eligible_shares")] double EligibleShares,
    [property: JsonPropertyName("gross_model_edge")] double? GrossModelEdge,
    [property: JsonPropertyName("latency_movement")] double? LatencyMovement,
    [property: JsonPropertyName("taker_spread")] double? TakerSpread,
    [property: JsonPropertyName("taker_slippage")] double? TakerSlippage,
    [property: JsonPropertyName("maker_improvement")] double? MakerImprovement,
    [property: JsonPropertyName("fee_drag")] double? FeeDrag,
    [property: JsonPropertyName("realized_net_edge")] double? RealizedNetEdge,
    [property: JsonPropertyName("adverse_selection_5m")] double? AdverseSelection5m,
    [property: JsonPropertyName("adverse_selection_30m")] double? AdverseSelection30m);

public sealed record ExecutionCostReasonDto(
    [property: JsonPropertyName("reason")] string Reason,
    [property: JsonPropertyName("orders")] int Orders);

public sealed record ExecutionCostSliceDto(
    [property: JsonPropertyName("dimension")] string Dimension,
    [property: JsonPropertyName("value")] string Value,
    [property: JsonPropertyName("attempts")] int Attempts,
    [property: JsonPropertyName("filled")] int Filled,
    [property: JsonPropertyName("sealed")] int Sealed,
    [property: JsonPropertyName("eligible")] int Eligible,
    [property: JsonPropertyName("metrics")] ExecutionCostMetricsDto Metrics);

public sealed record ExecutionCostReportDto(
    [property: JsonPropertyName("generated_at")] DateTimeOffset GeneratedAt,
    [property: JsonPropertyName("mode")] string Mode,
    [property: JsonPropertyName("state")] string State,
    [property: JsonPropertyName("source_integrity")] string SourceIntegrity,
    [property: JsonPropertyName("opened_at")] DateTimeOffset OpenedAt,
    [property: JsonPropertyName("definition_version")] string DefinitionVersion,
    [property: JsonPropertyName("definition_hash")] string DefinitionHash,
    [property: JsonPropertyName("attempts")] int Attempts,
    [property: JsonPropertyName("filled_attempts")] int FilledAttempts,
    [property: JsonPropertyName("no_fill_attempts")] int NoFillAttempts,
    [property: JsonPropertyName("pending_attempts")] int PendingAttempts,
    [property: JsonPropertyName("sealed_attempts")] int SealedAttempts,
    [property: JsonPropertyName("eligible_seals")] int EligibleSeals,
    [property: JsonPropertyName("ineligible_seals")] int IneligibleSeals,
    [property: JsonPropertyName("maker_attempts")] int MakerAttempts,
    [property: JsonPropertyName("maker_fills")] int MakerFills,
    [property: JsonPropertyName("maker_fill_rate")] double? MakerFillRate,
    [property: JsonPropertyName("maker_seals")] int MakerSeals,
    [property: JsonPropertyName("taker_seals")] int TakerSeals,
    [property: JsonPropertyName("mixed_seals")] int MixedSeals,
    [property: JsonPropertyName("unknown_role_seals")] int UnknownRoleSeals,
    [property: JsonPropertyName("expected_slippage_coverage")] ExecutionCostCoverageDto ExpectedSlippageCoverage,
    [property: JsonPropertyName("exact_fee_coverage")] ExecutionCostCoverageDto ExactFeeCoverage,
    [property: JsonPropertyName("markout_5m_coverage")] ExecutionCostCoverageDto Markout5mCoverage,
    [property: JsonPropertyName("markout_30m_coverage")] ExecutionCostCoverageDto Markout30mCoverage,
    [property: JsonPropertyName("metrics")] ExecutionCostMetricsDto Metrics,
    [property: JsonPropertyName("reasons")] IReadOnlyList<ExecutionCostReasonDto> Reasons,
    [property: JsonPropertyName("slices")] IReadOnlyList<ExecutionCostSliceDto> Slices);

public sealed record ExecutionCostOrderDto(
    [property: JsonPropertyName("order_id")] long OrderId,
    [property: JsonPropertyName("intent_id")] long IntentId,
    [property: JsonPropertyName("signal_id")] long SignalId,
    [property: JsonPropertyName("market_id")] long MarketId,
    [property: JsonPropertyName("station_icao")] string? StationIcao,
    [property: JsonPropertyName("market_date")] DateOnly MarketDate,
    [property: JsonPropertyName("kind")] string Kind,
    [property: JsonPropertyName("confidence")] string Confidence,
    [property: JsonPropertyName("horizon_h")] double? HorizonHours,
    [property: JsonPropertyName("mode")] string Mode,
    [property: JsonPropertyName("side")] string Side,
    [property: JsonPropertyName("order_style")] string OrderStyle,
    [property: JsonPropertyName("requested_price")] double RequestedPrice,
    [property: JsonPropertyName("requested_notional_usd")] double RequestedNotionalUsd,
    [property: JsonPropertyName("intent_at")] DateTimeOffset IntentAt,
    [property: JsonPropertyName("terminal_status")] string? TerminalStatus,
    [property: JsonPropertyName("obligation_state")] string? ObligationState,
    [property: JsonPropertyName("eligible")] bool? Eligible,
    [property: JsonPropertyName("eligibility_reason")] string? EligibilityReason,
    [property: JsonPropertyName("aggregate_role")] string? AggregateRole,
    [property: JsonPropertyName("leg_count")] int LegCount,
    [property: JsonPropertyName("total_shares")] double? TotalShares,
    [property: JsonPropertyName("vwap_fill_price")] double? VwapFillPrice,
    [property: JsonPropertyName("total_fee_usd")] double? TotalFeeUsd,
    [property: JsonPropertyName("gross_model_edge")] double? GrossModelEdge,
    [property: JsonPropertyName("latency_movement")] double? LatencyMovement,
    [property: JsonPropertyName("taker_spread")] double? TakerSpread,
    [property: JsonPropertyName("taker_slippage")] double? TakerSlippage,
    [property: JsonPropertyName("maker_improvement")] double? MakerImprovement,
    [property: JsonPropertyName("fee_drag")] double? FeeDrag,
    [property: JsonPropertyName("realized_net_edge")] double? RealizedNetEdge,
    [property: JsonPropertyName("adverse_selection_5m")] double? AdverseSelection5m,
    [property: JsonPropertyName("adverse_selection_30m")] double? AdverseSelection30m,
    [property: JsonPropertyName("sealed_at")] DateTimeOffset? SealedAt);

public sealed record ExecutionCostOrdersDto(
    [property: JsonPropertyName("generated_at")] DateTimeOffset GeneratedAt,
    [property: JsonPropertyName("mode")] string Mode,
    [property: JsonPropertyName("total")] int Total,
    [property: JsonPropertyName("limit")] int Limit,
    [property: JsonPropertyName("offset")] int Offset,
    [property: JsonPropertyName("items")] IReadOnlyList<ExecutionCostOrderDto> Items);

public sealed record OrderDto(
    [property: JsonPropertyName("id")] long Id,
    [property: JsonPropertyName("polymarket_id")] string PolymarketId,
    [property: JsonPropertyName("station_icao")] string? StationIcao,
    [property: JsonPropertyName("mode")] string Mode,
    [property: JsonPropertyName("clob_order_id")] string? ClobOrderId,
    [property: JsonPropertyName("side")] string Side,
    [property: JsonPropertyName("price")] double Price,
    [property: JsonPropertyName("requested_limit_price")] double? RequestedLimitPrice,
    [property: JsonPropertyName("actual_fill_vwap")] double? ActualFillVwap,
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

public sealed record StationTrustListItem(
    string Station,
    string Lifecycle,
    string Trust,
    string Evidence,
    string Selection,
    string Freshness,
    string Provenance,
    string Status,
    string? Accent = null);

public sealed record DiagnosticBarItem(
    string Title,
    string Subtitle,
    string Detail,
    string Status,
    string? Accent,
    double MagnitudePercent,
    string Metric,
    string Dimension,
    string Value,
    string ActionDetail = "");

public sealed record RootCauseTradeListItem(
    string Market,
    string Setup,
    string Model,
    string Execution,
    string Outcome,
    string Status,
    string? Accent,
    RootCauseTradeDto Trade);
