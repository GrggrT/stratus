using System.Globalization;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using StratusConsole.Models;
using StratusConsole.Services;

namespace StratusConsole;

public sealed partial class MainPage : Page
{
    private readonly StratusApiClient _apiClient = new(new Uri("http://127.0.0.1:8000"));
    private readonly DispatcherTimer _refreshTimer = new() { Interval = TimeSpan.FromSeconds(10) };
    private readonly CancellationTokenSource _lifetime = new();
    private string _selectedDestination = "Dashboard";
    private bool _isRefreshing;
    private bool _refreshPending;

    public MainPage()
    {
        InitializeComponent();

        RootNavigation.SelectedItem = RootNavigation.MenuItems[0];
        _refreshTimer.Tick += RefreshTimer_Tick;
        Loaded += MainPage_Loaded;
        Unloaded += MainPage_Unloaded;
    }

    private async void MainPage_Loaded(object sender, RoutedEventArgs e)
    {
        _refreshTimer.Start();
        await RefreshAsync();
    }

    private void MainPage_Unloaded(object sender, RoutedEventArgs e)
    {
        _refreshTimer.Stop();
        _lifetime.Cancel();
        _apiClient.Dispose();
        _lifetime.Dispose();
    }

    private async void RefreshTimer_Tick(object? sender, object e)
    {
        if (AutoRefreshButton.IsChecked == true)
        {
            await RefreshAsync();
        }
    }

    private async void RefreshButton_Click(object sender, RoutedEventArgs e)
    {
        await RefreshAsync();
    }

    private async Task RefreshAsync()
    {
        if (_isRefreshing)
        {
            _refreshPending = true;
            return;
        }

        _isRefreshing = true;
        RefreshButton.IsEnabled = false;
        string destination = _selectedDestination;

        try
        {
            HealthSnapshot health = await _apiClient.GetHealthSnapshotAsync(_lifetime.Token);
            RenderHealth(health);

            if (destination == _selectedDestination)
            {
                await LoadDestinationAsync(destination);
            }
        }
        catch (OperationCanceledException) when (_lifetime.IsCancellationRequested)
        {
        }
        catch (Exception exception)
        {
            RenderUnreachable(exception.Message);
        }
        finally
        {
            RefreshButton.IsEnabled = true;
            _isRefreshing = false;
            if (_refreshPending && !_lifetime.IsCancellationRequested)
            {
                _refreshPending = false;
                _ = RefreshAsync();
            }
        }
    }

    private async Task LoadDestinationAsync(string destination)
    {
        try
        {
            switch (destination)
            {
                case "Dashboard":
                    RenderOverview(await _apiClient.GetOverviewAsync(_lifetime.Token));
                    break;
                case "Markets":
                    RenderMarkets((await _apiClient.GetMarketsAsync(_lifetime.Token)).Items);
                    break;
                case "Signals":
                    RenderSignals((await _apiClient.GetSignalsAsync(_lifetime.Token)).Items);
                    break;
                case "Positions":
                    RenderPositions((await _apiClient.GetPositionsAsync(_lifetime.Token)).Items);
                    break;
                case "Calibration":
                    RenderCalibrations((await _apiClient.GetCalibrationsAsync(_lifetime.Token)).Items);
                    break;
                case "Jobs":
                    RenderJobs((await _apiClient.GetJobsAsync(_lifetime.Token)).Items);
                    break;
                case "Operations":
                    RenderOperations(await _apiClient.GetOverviewAsync(_lifetime.Token));
                    break;
                case "Settings":
                    RenderSettings();
                    break;
            }
        }
        catch (OperationCanceledException) when (_lifetime.IsCancellationRequested)
        {
        }
        catch (Exception exception)
        {
            RenderMonitoringError(exception.Message);
        }
    }

    private void RenderHealth(HealthSnapshot snapshot)
    {
        bool healthy = string.Equals(snapshot.HealthStatus, "ok", StringComparison.OrdinalIgnoreCase);
        bool ready = string.Equals(snapshot.ReadyStatus, "ready", StringComparison.OrdinalIgnoreCase);

        ApiStatusText.Text = healthy && ready ? "Online · Ready" : healthy ? "Online · Not ready" : "Unexpected";
        ApiDetailText.Text = ready
            ? "Process, database, runtime control, and health audit passed"
            : snapshot.Reasons.Count > 0
                ? string.Join(" · ", snapshot.Reasons)
                : $"/readyz returned '{snapshot.ReadyStatus}'";
        UpdatedStatusText.Text = $"Last successful refresh: {snapshot.CheckedAt.ToLocalTime():G}";

        SystemInfoBar.Severity = healthy && ready ? InfoBarSeverity.Success : InfoBarSeverity.Warning;
        SystemInfoBar.Title = healthy && ready ? "System ready" : "System not ready";
        SystemInfoBar.Message = healthy && ready
            ? "The local API and backend dependencies report ready."
            : ApiDetailText.Text;
    }

    private void RenderOverview(OverviewResponse overview)
    {
        RuntimeStatusText.Text = overview.Runtime.Mode.ToUpperInvariant();
        RuntimeDetailText.Text = overview.Runtime.KillSwitch
            ? $"Kill switch active{ReasonSuffix(overview.Runtime.HaltedReason)}"
            : "Kill switch is off; backend risk limits remain authoritative";

        PortfolioStatusText.Text = Usd(overview.BankrollUsd);
        PortfolioDetailText.Text =
            $"Open exposure {Usd(overview.OpenExposureUsd)} · {overview.OpenPositions} open positions";

        if (overview.Validation is null)
        {
            GateStatusText.Text = "Not evaluated";
            GateDetailText.Text = "No day-30 or day-60 validation checkpoint yet";
        }
        else
        {
            GateStatusText.Text = overview.Validation.Passed ? "Passed" : "Not passed";
            GateDetailText.Text =
                $"Day {overview.Validation.CheckpointDay} · "
                + (overview.Validation.Eligible ? "eligible" : "not yet eligible");
        }

        ActivityText.Text =
            $"{overview.ActiveMarkets} active markets · {overview.BlockedMarkets} blocked markets · "
            + $"{overview.PendingSignals} pending signals";
    }

    private void RenderMarkets(IReadOnlyList<MarketDto> markets)
    {
        SetCollection(markets.Select(market => new MonitoringListItem(
            $"{market.StationIcao ?? "Unmapped"} · {market.MarketDate:yyyy-MM-dd} · {Pretty(market.Kind)}",
            $"{market.City ?? "Unknown city"} · bucket {TemperatureRange(market.BucketLowC, market.BucketHighC)}",
            market.StationVerified
                ? "Station rules verified"
                : $"Verification blocked{ReasonSuffix(HumanizeReasons(market.VerificationReason))}",
            $"Resolves {LocalTime(market.ResolvesAt)} · {market.PolymarketId}",
            Pretty(market.Status))));
    }

    private void RenderSignals(IReadOnlyList<SignalDto> signals)
    {
        SetCollection(signals.Select(signal => new MonitoringListItem(
            $"{signal.StationIcao ?? "Unmapped"} · {Pretty(signal.Side)}",
            $"Model {Percent(signal.ModelProbability)} · market {Percent(signal.MarketPrice)} · edge {Percent(signal.Edge)}",
            $"Size {Usd(signal.SizeUsd)} · Kelly {Percent(signal.KellyFraction)} · confidence {signal.Confidence}",
            $"{LocalTime(signal.Timestamp)} · "
                + (signal.SkipReason is null ? signal.PolymarketId : HumanizeReasons(signal.SkipReason)),
            Pretty(signal.Status))));
    }

    private void RenderPositions(IReadOnlyList<PositionDto> positions)
    {
        SetCollection(positions.Select(position => new MonitoringListItem(
            $"{position.StationIcao ?? "Unmapped"} · {Pretty(position.Side)}",
            $"{Pretty(position.Mode)} · price {Percent(position.AveragePrice)} · size {Usd(position.SizeUsd)}",
            $"P&L {Usd(position.PnlUsd)} · CLV {Percent(position.Clv)} · fees {Usd(position.FeeUsd)}",
            position.ClosedAt is null
                ? position.PolymarketId
                : $"Closed {LocalTime(position.ClosedAt.Value)} · {position.PolymarketId}",
            Pretty(position.Status))));
    }

    private void RenderCalibrations(IReadOnlyList<CalibrationDto> calibrations)
    {
        SetCollection(calibrations.Select(calibration => new MonitoringListItem(
            $"{calibration.StationIcao} · {calibration.Model.ToUpperInvariant()} · {Pretty(calibration.Kind)}",
            $"Horizon {calibration.HorizonBucket} · n={calibration.SampleCount}",
            $"Bias {calibration.BiasC:+0.00;-0.00;0.00} °C · sigma {calibration.SigmaC:0.00} °C · Brier {Number(calibration.Brier)}",
            $"Updated {LocalTime(calibration.UpdatedAt)}",
            calibration.SampleCount >= 100 ? "Mature" : "Collecting")));
    }

    private void RenderJobs(IReadOnlyList<JobDto> jobs)
    {
        SetCollection(jobs.Select(job => new MonitoringListItem(
            job.JobId,
            $"Scheduled {LocalTime(job.ScheduledAt)}",
            $"Duration {(job.FinishedAt - job.ScheduledAt).TotalSeconds:0.0}s{ReasonSuffix(job.Error)}",
            $"Finished {LocalTime(job.FinishedAt)}",
            Pretty(job.Status))));
    }

    private void RenderOperations(OverviewResponse overview)
    {
        List<MonitoringListItem> items =
        [
            new(
                "Runtime control",
                $"Mode {overview.Runtime.Mode.ToUpperInvariant()}",
                overview.Runtime.KillSwitch ? "Kill switch active" : "Kill switch off",
                overview.Runtime.HaltedReason ?? "No project halt reason",
                overview.Runtime.KillSwitch ? "Stopped" : "Running"),
            new(
                "Latest health audit",
                overview.HealthAudit?.Status ?? "No audit available",
                overview.HealthAudit is null
                    ? "The backend has not written a health audit"
                    : $"{overview.HealthAudit.Issues.Count} recorded issues",
                overview.HealthAudit is null ? "—" : LocalTime(overview.HealthAudit.Timestamp),
                overview.HealthAudit?.Status is "healthy" ? "Healthy" : "Review"),
            new(
                "Validation gate",
                overview.Validation is null
                    ? "No checkpoint"
                    : $"Day {overview.Validation.CheckpointDay}",
                overview.Validation is null
                    ? "Paper evidence is still accumulating"
                    : overview.Validation.Eligible ? "Eligible for evaluation" : "Not yet eligible",
                "Live execution remains unavailable in this console",
                overview.Validation?.Passed is true ? "Passed" : "Locked"),
        ];
        SetCollection(items);
    }

    private void RenderSettings()
    {
        SetCollection(
        [
            new(
                "Local API endpoint",
                "http://127.0.0.1:8000",
                "Loopback-only connection; direct database access is disabled",
                "Changeable endpoint settings will be added with authenticated operations",
                "Read only"),
            new(
                "Refresh cadence",
                "10 seconds",
                "Automatic refresh can be disabled from the command bar",
                "Manual refresh remains available",
                "Active"),
        ]);
    }

    private void SetCollection(IEnumerable<MonitoringListItem> items)
    {
        List<MonitoringListItem> materialized = items.ToList();
        CollectionList.ItemsSource = materialized;
        CollectionEmptyText.Text = "No records yet";
        CollectionEmptyState.Visibility = materialized.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
    }

    private void RenderMonitoringError(string error)
    {
        if (_selectedDestination == "Dashboard")
        {
            ResetOverview();
        }
        else
        {
            CollectionList.ItemsSource = null;
            CollectionEmptyText.Text = "Monitoring data is unavailable";
            CollectionEmptyState.Visibility = Visibility.Visible;
        }

        SystemInfoBar.Severity = InfoBarSeverity.Error;
        SystemInfoBar.Title = "Monitoring API unavailable";
        SystemInfoBar.Message = error;
    }

    private void RenderUnreachable(string error)
    {
        ApiStatusText.Text = "Unreachable";
        ApiDetailText.Text = "No current response from /healthz or /readyz";
        UpdatedStatusText.Text = "Displayed monitoring data is stale";
        ResetOverview();
        if (_selectedDestination != "Dashboard")
        {
            CollectionList.ItemsSource = null;
            CollectionEmptyText.Text = "The local STRATUS API is unreachable";
            CollectionEmptyState.Visibility = Visibility.Visible;
        }

        SystemInfoBar.Severity = InfoBarSeverity.Error;
        SystemInfoBar.Title = "API unreachable";
        SystemInfoBar.Message = error;
    }

    private void ResetOverview()
    {
        RuntimeStatusText.Text = "Unknown";
        RuntimeDetailText.Text = "Monitoring data unavailable";
        PortfolioStatusText.Text = "—";
        PortfolioDetailText.Text = "Balance and exposure unavailable";
        GateStatusText.Text = "Unknown";
        GateDetailText.Text = "Validation data unavailable";
        ActivityText.Text = "Monitoring data unavailable";
    }

    private async void RootNavigation_SelectionChanged(
        NavigationView sender,
        NavigationViewSelectionChangedEventArgs args)
    {
        _selectedDestination = args.IsSettingsSelected
            ? "Settings"
            : (args.SelectedItemContainer?.Tag as string ?? "Dashboard");

        bool dashboard = _selectedDestination == "Dashboard";
        DashboardView.Visibility = dashboard ? Visibility.Visible : Visibility.Collapsed;
        CollectionView.Visibility = dashboard ? Visibility.Collapsed : Visibility.Visible;
        SetPageHeading(_selectedDestination);

        if (IsLoaded)
        {
            await RefreshAsync();
        }
    }

    private void SetPageHeading(string destination)
    {
        PageTitle.Text = destination == "Dashboard" ? "System overview" : destination;
        PageSubtitle.Text = destination switch
        {
            "Dashboard" => "Live read-only monitoring from the local STRATUS API",
            "Markets" => "Active, resolved, and blocked weather markets",
            "Signals" => "Recent signal decisions and structured skip reasons",
            "Positions" => "Paper positions, P&L, fees, and closing-line value",
            "Calibration" => "Station and horizon calibration evidence",
            "Jobs" => "Recent scheduler outcomes and execution durations",
            "Operations" => "Read-only runtime, audit, and validation state",
            "Settings" => "Local client configuration",
            _ => "STRATUS monitoring",
        };
    }

    private static string Pretty(string value) =>
        CultureInfo.InvariantCulture.TextInfo.ToTitleCase(value.Replace('_', ' '));

    private static string Usd(double? value) =>
        value.HasValue ? value.Value.ToString("$0.00", CultureInfo.InvariantCulture) : "—";

    private static string Percent(double? value) =>
        value.HasValue ? value.Value.ToString("P1", CultureInfo.InvariantCulture) : "—";

    private static string Number(double? value) =>
        value.HasValue ? value.Value.ToString("0.000", CultureInfo.InvariantCulture) : "—";

    private static string LocalTime(DateTimeOffset value) => value.ToLocalTime().ToString("g");

    private static string TemperatureRange(double? low, double? high) => (low, high) switch
    {
        (not null, not null) => $"{low:0.0}–{high:0.0} °C",
        (not null, null) => $"≥ {low:0.0} °C",
        (null, not null) => $"≤ {high:0.0} °C",
        _ => "not specified",
    };

    private static string ReasonSuffix(string? reason) =>
        string.IsNullOrWhiteSpace(reason) ? string.Empty : $" · {reason}";

    private static string HumanizeReasons(string? reasons) =>
        string.IsNullOrWhiteSpace(reasons)
            ? string.Empty
            : string.Join(
                " · ",
                reasons.Split(';', StringSplitOptions.RemoveEmptyEntries).Select(Pretty));
}
