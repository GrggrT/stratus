using System.Collections.Concurrent;
using System.Net.Http.Json;
using System.Text.Json.Serialization;
using StratusConsole.Models;

namespace StratusConsole.Services;

public sealed class StratusApiClient : IDisposable
{
    internal const int MaxConcurrentRequestsPerServer = 4;
    internal const int MaxConcurrentReadRequestsPerServer = 3;
    private static readonly ConcurrentDictionary<string, SemaphoreSlim> ServerReadRequestGates =
        new(StringComparer.OrdinalIgnoreCase);

    private readonly HttpClient _httpClient;
    private readonly SemaphoreSlim _serverReadRequestGate;

    public StratusApiClient(Uri baseAddress, string? apiKey = null)
    {
        string serverKey = baseAddress.GetLeftPart(UriPartial.Authority);
        _serverReadRequestGate = ServerReadRequestGates.GetOrAdd(
            serverKey,
            _ => new SemaphoreSlim(
                MaxConcurrentReadRequestsPerServer,
                MaxConcurrentReadRequestsPerServer));
        _httpClient = new HttpClient(new SocketsHttpHandler
        {
            MaxConnectionsPerServer = MaxConcurrentRequestsPerServer,
        })
        {
            BaseAddress = baseAddress,
            Timeout = TimeSpan.FromSeconds(3),
        };
        if (!string.IsNullOrWhiteSpace(apiKey))
        {
            _httpClient.DefaultRequestHeaders.Add("X-API-Key", apiKey);
        }
    }

    public async Task<HealthSnapshot> GetHealthSnapshotAsync(CancellationToken cancellationToken = default)
    {
        StatusResponse health = await GetStatusAsync("healthz", allowServiceUnavailable: false, cancellationToken);
        StatusResponse ready = await GetStatusAsync("readyz", allowServiceUnavailable: true, cancellationToken);

        return new HealthSnapshot(
            health.Status,
            ready.Status,
            ready.Reasons ?? [],
            DateTimeOffset.UtcNow);
    }

    public Task<OverviewResponse> GetOverviewAsync(CancellationToken cancellationToken = default) =>
        GetRequiredAsync<OverviewResponse>("api/v1/overview", cancellationToken);

    public Task<CollectionResponse<MarketDto>> GetMarketsAsync(
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<CollectionResponse<MarketDto>>("api/v1/markets?limit=100", cancellationToken);

    public Task<CollectionResponse<SignalDto>> GetSignalsAsync(
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<CollectionResponse<SignalDto>>("api/v1/signals?limit=100", cancellationToken);

    public Task<CollectionResponse<PositionDto>> GetPositionsAsync(
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<CollectionResponse<PositionDto>>("api/v1/positions?limit=100", cancellationToken);

    public Task<CollectionResponse<CalibrationDto>> GetCalibrationsAsync(
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<CollectionResponse<CalibrationDto>>("api/v1/calibration?limit=500", cancellationToken);

    public Task<StationLifecycleResponse> GetStationsAsync(
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<StationLifecycleResponse>("api/v1/stations", cancellationToken);

    public Task<CollectionResponse<JobDto>> GetJobsAsync(
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<CollectionResponse<JobDto>>("api/v1/jobs?limit=100", cancellationToken);

    public Task<CollectionResponse<OrderDto>> GetOrdersAsync(
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<CollectionResponse<OrderDto>>("api/v1/orders?limit=100", cancellationToken);

    public Task<LiveSummaryDto> GetLiveSummaryAsync(
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<LiveSummaryDto>("api/v1/live-summary", cancellationToken);

    public Task<LiveSummaryDto> GetPerformanceSummaryAsync(
        string mode,
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<LiveSummaryDto>(
            $"api/v1/performance-summary?mode={mode}", cancellationToken);

    public Task<EdgeReportDto> GetEdgeReportAsync(
        string mode,
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<EdgeReportDto>(
            $"api/v1/edge-report?mode={mode}", cancellationToken);

    public Task<ProspectiveValidationLedgerDto> GetProspectiveValidationLedgerAsync(
        string mode,
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<ProspectiveValidationLedgerDto>(
            $"api/v1/validation-ledger?mode={mode}", cancellationToken);

    public async Task<AlertsResponseDto> GetAlertsAsync(
        string state,
        int limit = 100,
        CancellationToken cancellationToken = default)
    {
        string queryState = state.Equals("all", StringComparison.OrdinalIgnoreCase)
            ? "all"
            : "unacknowledged";
        int boundedLimit = Math.Clamp(limit, 1, 500);
        AlertsResponseDto response = await GetRequiredAsync<AlertsResponseDto>(
            $"api/v1/alerts?state={queryState}&limit={boundedLimit}",
            cancellationToken);
        string[] channelStates = ["not_activated", "healthy", "stale", "degraded"];
        string[] externalStates =
            ["dead_letter", "sent", "local_only", "in_flight", "retry_scheduled", "pending"];
        if (response.Items is null
            || string.IsNullOrWhiteSpace(response.Channel)
            || !response.QueryState.Equals(queryState, StringComparison.OrdinalIgnoreCase)
            || !channelStates.Contains(response.ChannelState, StringComparer.OrdinalIgnoreCase)
            || response.TotalCount < 0
            || response.UnacknowledgedCount < 0
            || response.UnacknowledgedCount > response.TotalCount
            || response.PendingAfterActivation < 0
            || response.DeadLetterCount < 0
            || response.OldestUnacknowledgedAgeSeconds is < 0
            || response.Items.Count > response.TotalCount
            || response.Items.Select(item => item.Id).Distinct().Count() != response.Items.Count
            || response.Items.Any(item => item.Id < 1
                || item.Attempts < 0
                || string.IsNullOrWhiteSpace(item.EventKey)
                || string.IsNullOrWhiteSpace(item.Category)
                || item.Payload is null)
            || (queryState == "unacknowledged"
                && response.Items.Any(item => item.AcknowledgedAt is not null))
            || response.Items.Any(item => !externalStates.Contains(
                item.ExternalState,
                StringComparer.OrdinalIgnoreCase)))
        {
            throw new InvalidDataException(
                "/api/v1/alerts returned an incompatible response.");
        }

        return response;
    }

    public async Task<AlertAcknowledgementResponseDto> AcknowledgeAlertAsync(
        long alertId,
        CancellationToken cancellationToken = default)
    {
        using HttpResponseMessage response = await _httpClient.PostAsync(
            $"api/v1/alerts/{alertId}/ack",
            content: null,
            cancellationToken);
        response.EnsureSuccessStatusCode();
        AlertAcknowledgementResponseDto? payload =
            await response.Content.ReadFromJsonAsync<AlertAcknowledgementResponseDto>(
                cancellationToken);
        if (payload is null || payload.Acknowledged != 1)
        {
            throw new InvalidDataException(
                $"/api/v1/alerts/{alertId}/ack returned an invalid response.");
        }

        return payload;
    }

    public async Task<AlertAcknowledgementResponseDto> AcknowledgeAlertsThroughAsync(
        long throughId,
        CancellationToken cancellationToken = default)
    {
        using HttpResponseMessage response = await _httpClient.PostAsJsonAsync(
            "api/v1/alerts/ack-through",
            new AlertAckThroughRequestDto(throughId),
            cancellationToken);
        response.EnsureSuccessStatusCode();
        AlertAcknowledgementResponseDto? payload =
            await response.Content.ReadFromJsonAsync<AlertAcknowledgementResponseDto>(
                cancellationToken);
        if (payload is null
            || payload.Acknowledged < 0
            || payload.ThroughId != throughId)
        {
            throw new InvalidDataException(
                "/api/v1/alerts/ack-through returned an invalid response.");
        }

        return payload;
    }

    public Task<AccuracyOverviewDto> GetAccuracyOverviewAsync(
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<AccuracyOverviewDto>("api/v1/accuracy-overview", cancellationToken);

    public Task<ResolutionReconciliationResponseDto> GetResolutionReconciliationAsync(
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<ResolutionReconciliationResponseDto>(
            "api/v1/resolution-reconciliation?limit=40", cancellationToken);

    public Task<SettlementCasesResponseDto> GetSettlementCasesAsync(
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<SettlementCasesResponseDto>(
            "api/v1/settlement-cases?limit=40", cancellationToken);

    public Task<StorageCapacityDto> GetStorageCapacityAsync(
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<StorageCapacityDto>("api/v1/storage-capacity", cancellationToken);

    public Task<ForecastProvenanceDto> GetForecastProvenanceAsync(
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<ForecastProvenanceDto>(
            "api/v1/forecast-provenance", cancellationToken);

    public Task<OfficialResolutionSourcesDto> GetOfficialResolutionSourcesAsync(
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<OfficialResolutionSourcesDto>(
            "api/v1/official-resolution-sources", cancellationToken);

    public async Task<EvidenceContinuityDto> GetEvidenceContinuityAsync(
        CancellationToken cancellationToken = default)
    {
        EvidenceContinuityDto payload = await GetRequiredAsync<EvidenceContinuityDto>(
            "api/v1/evidence-continuity",
            cancellationToken);
        bool knownState = payload.State is "activation_required"
            or "collecting"
            or "cohort_change_blocked";
        bool stateIsCoherent = payload.State switch
        {
            "activation_required" => payload.ActiveEpoch is null,
            "collecting" => payload.ActiveEpoch is not null
                && !string.IsNullOrWhiteSpace(payload.ActiveCohort)
                && payload.CohortMatch,
            "cohort_change_blocked" => payload.ActiveEpoch is not null
                && !string.IsNullOrWhiteSpace(payload.ActiveCohort)
                && !payload.CohortMatch,
            _ => false,
        };
        if (!knownState
            || !stateIsCoherent
            || payload.GeneratedAt == default
            || string.IsNullOrWhiteSpace(payload.CandidateCohort)
            || string.IsNullOrWhiteSpace(payload.CurrentBottleneck)
            || string.IsNullOrWhiteSpace(payload.CurrentBottleneckDetail)
            || payload.TopSkipReasons is null
            || payload.Lanes is null)
        {
            throw new InvalidDataException(
                "/api/v1/evidence-continuity returned an invalid continuity contract.");
        }

        return payload;
    }

    public Task<ExecutionCostReportDto> GetExecutionCostReportAsync(
        string mode,
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<ExecutionCostReportDto>(
            $"api/v1/execution-cost-report?mode={Uri.EscapeDataString(mode)}",
            cancellationToken);

    public Task<ExecutionCostOrdersDto> GetExecutionCostOrdersAsync(
        string mode,
        int limit = 50,
        int offset = 0,
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<ExecutionCostOrdersDto>(
            "api/v1/execution-cost-report/orders"
            + $"?mode={Uri.EscapeDataString(mode)}"
            + $"&limit={limit}&offset={offset}",
            cancellationToken);

    public Task<SelectionPolicyReportDto> GetSelectionPolicyReportAsync(
        string mode,
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<SelectionPolicyReportDto>(
            $"api/v1/selection-policy-report?mode={mode}", cancellationToken);

    public Task<BrierReportDto> GetBrierReportAsync(
        string mode,
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<BrierReportDto>(
            $"api/v1/brier-report?mode={mode}", cancellationToken);

    public Task<ClvReportDto> GetClvReportAsync(
        string mode,
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<ClvReportDto>(
            $"api/v1/clv-report?mode={mode}", cancellationToken);

    public Task<RootCauseDrilldownDto> GetRootCauseDrilldownAsync(
        string mode,
        string metric,
        string dimension,
        string value,
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<RootCauseDrilldownDto>(
            "api/v1/root-cause-drilldown"
            + $"?mode={Uri.EscapeDataString(mode)}"
            + $"&metric={Uri.EscapeDataString(metric)}"
            + $"&dimension={Uri.EscapeDataString(dimension)}"
            + $"&value={Uri.EscapeDataString(value)}",
            cancellationToken);

    public Task<RootCauseActionsDto> GetRootCauseActionsAsync(
        string mode,
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<RootCauseActionsDto>(
            $"api/v1/root-cause-actions?mode={Uri.EscapeDataString(mode)}",
            cancellationToken);

    public Task<EquityResponse> GetLiveEquityAsync(
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<EquityResponse>("api/v1/live-equity", cancellationToken);

    public Task<EquityResponse> GetEquityAsync(
        string mode,
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<EquityResponse>($"api/v1/equity?mode={mode}", cancellationToken);

    public Task<MarketPriceSeriesResponse> GetMarketPricesAsync(
        long marketId,
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<MarketPriceSeriesResponse>(
            $"api/v1/market-timeseries?market_id={marketId}&limit=240",
            cancellationToken);

    public Task<ProbabilitySeriesResponse> GetProbabilitySeriesAsync(
        long marketId,
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<ProbabilitySeriesResponse>(
            $"api/v1/probability-timeseries?market_id={marketId}&benchmark_protocol=clob-dual-v1&limit=240",
            cancellationToken);

    public Task<OrderBookResponse> GetOrderBookAsync(
        long marketId,
        string side = "yes",
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<OrderBookResponse>(
            $"api/v1/orderbook?market_id={marketId}&side={side}",
            cancellationToken);

    public Task<CollectionResponse<CommandItem>> GetCommandsAsync(
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<CollectionResponse<CommandItem>>("api/v1/commands?limit=100", cancellationToken);

    public async Task<CommandAccepted> PostCommandAsync(
        CommandRequest command,
        CancellationToken cancellationToken = default)
    {
        using HttpResponseMessage response =
            await _httpClient.PostAsJsonAsync("api/v1/commands", command, cancellationToken);
        response.EnsureSuccessStatusCode();
        CommandAccepted? payload =
            await response.Content.ReadFromJsonAsync<CommandAccepted>(cancellationToken);
        return payload
            ?? throw new InvalidDataException("/api/v1/commands returned an invalid response.");
    }

    private async Task<T> GetRequiredAsync<T>(string path, CancellationToken cancellationToken)
    {
        using HttpResponseMessage response = await SendReadAsync(
            token => _httpClient.GetAsync(path, token),
            cancellationToken);
        response.EnsureSuccessStatusCode();
        T? payload = await response.Content.ReadFromJsonAsync<T>(cancellationToken);
        return payload ?? throw new InvalidDataException($"/{path} returned an invalid response.");
    }

    private async Task<StatusResponse> GetStatusAsync(
        string path,
        bool allowServiceUnavailable,
        CancellationToken cancellationToken)
    {
        using HttpResponseMessage response = await SendReadAsync(
            token => _httpClient.GetAsync(path, token),
            cancellationToken);
        bool acceptedStatus = response.IsSuccessStatusCode
            || (allowServiceUnavailable && response.StatusCode == System.Net.HttpStatusCode.ServiceUnavailable);
        if (!acceptedStatus)
        {
            response.EnsureSuccessStatusCode();
        }

        StatusResponse? payload = await response.Content.ReadFromJsonAsync<StatusResponse>(cancellationToken);
        if (payload is null || string.IsNullOrWhiteSpace(payload.Status))
        {
            throw new InvalidDataException($"/{path} returned an invalid response.");
        }

        return payload;
    }

    private async Task<HttpResponseMessage> SendReadAsync(
        Func<CancellationToken, Task<HttpResponseMessage>> send,
        CancellationToken cancellationToken)
    {
        // Keep one of the four transport slots available for authenticated
        // mutations such as emergency Stop. Queued reads wait outside HttpClient,
        // so they do not consume the three-second network timeout.
        await _serverReadRequestGate.WaitAsync(cancellationToken);
        try
        {
            return await send(cancellationToken);
        }
        finally
        {
            _serverReadRequestGate.Release();
        }
    }

    public void Dispose() => _httpClient.Dispose();

    private sealed record StatusResponse(
        [property: JsonPropertyName("status")] string Status,
        [property: JsonPropertyName("reasons")] IReadOnlyList<string>? Reasons);
}

public sealed record CommandRequest(
    [property: JsonPropertyName("request_id")] string RequestId,
    [property: JsonPropertyName("command")] string Command,
    [property: JsonPropertyName("reason")] string Reason,
    [property: JsonPropertyName("actor")] string Actor = "console",
    [property: JsonPropertyName("payload")]
    [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    IReadOnlyDictionary<string, object>? Payload = null);

public sealed record CommandItem(
    [property: JsonPropertyName("id")] long Id,
    [property: JsonPropertyName("request_id")] string RequestId,
    [property: JsonPropertyName("command")] string Command,
    [property: JsonPropertyName("actor")] string Actor,
    [property: JsonPropertyName("reason")] string Reason,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("detail")] string? Detail,
    [property: JsonPropertyName("created_at")] DateTimeOffset CreatedAt,
    [property: JsonPropertyName("executed_at")] DateTimeOffset? ExecutedAt);

public sealed record CommandAccepted(
    [property: JsonPropertyName("created")] bool Created,
    [property: JsonPropertyName("item")] CommandItem Item);
