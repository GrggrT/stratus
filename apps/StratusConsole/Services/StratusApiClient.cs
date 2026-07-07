using System.Net.Http.Json;
using System.Text.Json.Serialization;
using StratusConsole.Models;

namespace StratusConsole.Services;

public sealed class StratusApiClient : IDisposable
{
    private readonly HttpClient _httpClient;

    public StratusApiClient(Uri baseAddress, string? apiKey = null)
    {
        _httpClient = new HttpClient
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
        GetRequiredAsync<CollectionResponse<CalibrationDto>>("api/v1/calibration?limit=200", cancellationToken);

    public Task<CollectionResponse<JobDto>> GetJobsAsync(
        CancellationToken cancellationToken = default) =>
        GetRequiredAsync<CollectionResponse<JobDto>>("api/v1/jobs?limit=100", cancellationToken);

    private async Task<T> GetRequiredAsync<T>(string path, CancellationToken cancellationToken)
    {
        using HttpResponseMessage response = await _httpClient.GetAsync(path, cancellationToken);
        response.EnsureSuccessStatusCode();
        T? payload = await response.Content.ReadFromJsonAsync<T>(cancellationToken);
        return payload ?? throw new InvalidDataException($"/{path} returned an invalid response.");
    }

    private async Task<StatusResponse> GetStatusAsync(
        string path,
        bool allowServiceUnavailable,
        CancellationToken cancellationToken)
    {
        using HttpResponseMessage response = await _httpClient.GetAsync(path, cancellationToken);
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

    public void Dispose() => _httpClient.Dispose();

    private sealed record StatusResponse(
        [property: JsonPropertyName("status")] string Status,
        [property: JsonPropertyName("reasons")] IReadOnlyList<string>? Reasons);
}
