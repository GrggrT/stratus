namespace StratusConsole.Models;

public sealed record HealthSnapshot(
    string HealthStatus,
    string ReadyStatus,
    IReadOnlyList<string> Reasons,
    DateTimeOffset CheckedAt);
