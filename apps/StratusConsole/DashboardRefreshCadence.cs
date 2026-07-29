namespace StratusConsole;

internal readonly record struct DashboardRefreshPlan(
    bool RefreshEvidenceAndExecution,
    bool RefreshAccuracyAndProvenance);

internal sealed class DashboardRefreshCadence
{
    internal static readonly TimeSpan EvidenceAndExecutionInterval = TimeSpan.FromMinutes(1);
    internal static readonly TimeSpan AccuracyAndProvenanceInterval = TimeSpan.FromMinutes(5);

    private DateTimeOffset? _lastEvidenceAndExecutionRefresh;
    private DateTimeOffset? _lastAccuracyAndProvenanceRefresh;

    internal DashboardRefreshPlan BeginRefresh(DateTimeOffset now, bool forceAll)
    {
        bool refreshEvidenceAndExecution = forceAll
            || IsDue(_lastEvidenceAndExecutionRefresh, now, EvidenceAndExecutionInterval);
        bool refreshAccuracyAndProvenance = forceAll
            || IsDue(_lastAccuracyAndProvenanceRefresh, now, AccuracyAndProvenanceInterval);

        // Record attempts at their start. A failing diagnostic is retried on its
        // normal tier, rather than creating a ten-second failure storm.
        if (refreshEvidenceAndExecution)
        {
            _lastEvidenceAndExecutionRefresh = now;
        }
        if (refreshAccuracyAndProvenance)
        {
            _lastAccuracyAndProvenanceRefresh = now;
        }

        return new DashboardRefreshPlan(
            refreshEvidenceAndExecution,
            refreshAccuracyAndProvenance);
    }

    private static bool IsDue(
        DateTimeOffset? lastRefresh,
        DateTimeOffset now,
        TimeSpan interval) =>
        lastRefresh is null || now - lastRefresh.Value >= interval;
}
