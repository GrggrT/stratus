using System.Globalization;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Shapes;
using StratusConsole.Models;
using StratusConsole.Services;
using Windows.Foundation;
using Windows.UI;

namespace StratusConsole;

public sealed partial class MainPage : Page
{
    private static readonly Uri ApiBaseAddress = new("http://127.0.0.1:8000");
    private readonly OperatorCredentialStore _credentialStore = new();
    private StratusApiClient _apiClient;
    private readonly DispatcherTimer _refreshTimer = new() { Interval = TimeSpan.FromSeconds(10) };
    private readonly CancellationTokenSource _lifetime = new();
    private string _selectedDestination = "Dashboard";
    private bool _isRefreshing;
    private bool _refreshPending;
    private bool _isSubmittingCommand;
    private RuntimeDto? _lastRuntime;
    private string _credentialSource;

    public MainPage()
    {
        string? storedApiKey = _credentialStore.LoadApiKey();
        string? environmentApiKey =
            Environment.GetEnvironmentVariable("STRATUS_OPERATOR_API_KEY");
        string? apiKey = storedApiKey ?? environmentApiKey;
        _credentialSource = storedApiKey is not null
            ? "Windows Credential Locker"
            : environmentApiKey is not null
                ? "STRATUS_OPERATOR_API_KEY environment variable"
                : "Not configured";
        _apiClient = new StratusApiClient(ApiBaseAddress, apiKey);
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
                case "Live":
                    LiveSummaryDto liveSummary = await _apiClient.GetLiveSummaryAsync(_lifetime.Token);
                    EquityResponse liveEquity = await _apiClient.GetLiveEquityAsync(_lifetime.Token);
                    CollectionResponse<OrderDto> liveOrders =
                        await _apiClient.GetOrdersAsync(_lifetime.Token);
                    CollectionResponse<PositionDto> livePositions =
                        await _apiClient.GetPositionsAsync(_lifetime.Token);
                    RenderLive(liveSummary, liveEquity.Points, liveOrders.Items, livePositions.Items);
                    break;
                case "Operations":
                    OverviewResponse operationsOverview =
                        await _apiClient.GetOverviewAsync(_lifetime.Token);
                    CollectionResponse<CommandItem> commandLog =
                        await _apiClient.GetCommandsAsync(_lifetime.Token);
                    RenderOperations(operationsOverview, commandLog.Items);
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
        _lastRuntime = overview.Runtime;
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
            $"Resolves {LocalTime(market.ResolvesAt)} · #{market.Id} · {market.PolymarketId}",
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

    private void RenderOperations(
        OverviewResponse overview,
        IReadOnlyList<CommandItem> commands)
    {
        _lastRuntime = overview.Runtime;
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
        items.AddRange(commands.Select(command => new MonitoringListItem(
            $"{Pretty(command.Command)} · {command.RequestId}",
            command.Reason,
            command.Detail ?? "Queued; the worker polls every 20 seconds",
            $"{LocalTime(command.CreatedAt)} · {command.Actor}"
                + (command.ExecutedAt is null
                    ? string.Empty
                    : $" · executed {LocalTime(command.ExecutedAt.Value)}"),
            Pretty(command.Status))));
        SetCollection(items);
    }

    private async void StopButton_Click(object sender, RoutedEventArgs e)
    {
        string? reason = await ConfirmWithReasonAsync(
            "Stop trading",
            "Enables the kill switch and expires all pending signals. "
                + "Collectors and monitoring keep running.",
            "Stop trading");
        if (reason is not null)
        {
            await SubmitCommandAsync("stop", reason);
        }
    }

    private async void ResumeButton_Click(object sender, RoutedEventArgs e)
    {
        string killState = _lastRuntime is null
            ? "The current runtime state is unknown."
            : _lastRuntime.KillSwitch
                ? $"Kill switch is active{ReasonSuffix(_lastRuntime.HaltedReason)}."
                : "Kill switch is currently off.";
        string? reason = await ConfirmWithReasonAsync(
            "Resume trading",
            $"{killState} Resuming re-enables signal execution; "
                + "a project halt cannot be resumed from this console.",
            "Resume trading");
        if (reason is not null)
        {
            await SubmitCommandAsync("resume", reason);
        }
    }

    private async void VerifyMarketButton_Click(object sender, RoutedEventArgs e)
    {
        TextBox idBox = new() { PlaceholderText = "Internal market id (the # number in the list)" };
        TextBox reasonBox = new() { PlaceholderText = "Reason (at least 4 characters)" };
        StackPanel panel = new() { Spacing = 12 };
        panel.Children.Add(new TextBlock
        {
            Text = "Approve the parsed station for a blocked market after "
                + "re-checking its rules text against the resolution source.",
            TextWrapping = TextWrapping.Wrap,
        });
        panel.Children.Add(idBox);
        panel.Children.Add(reasonBox);
        ContentDialog dialog = new()
        {
            Title = "Verify market station",
            Content = panel,
            PrimaryButtonText = "Verify",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Close,
            XamlRoot = XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }

        if (!int.TryParse(idBox.Text.Trim(), out int marketId) || marketId < 1)
        {
            ShowCommandWarning("A positive internal market id is required.");
            return;
        }

        string reason = reasonBox.Text.Trim();
        if (reason.Length < 4)
        {
            ShowCommandWarning("A reason of at least 4 characters is required.");
            return;
        }

        await SubmitCommandAsync(
            "verify_market",
            reason,
            new Dictionary<string, object> { ["market_id"] = marketId });
    }

    private async void RunJobButton_Click(object sender, RoutedEventArgs e)
    {
        ComboBox jobBox = new()
        {
            ItemsSource = new[] { "discover-markets", "backfill-fees", "prune-signals" },
            SelectedIndex = 0,
            HorizontalAlignment = HorizontalAlignment.Stretch,
        };
        TextBox reasonBox = new() { PlaceholderText = "Reason (at least 4 characters)" };
        StackPanel panel = new() { Spacing = 12 };
        panel.Children.Add(new TextBlock
        {
            Text = "Queue one allowlisted maintenance job. The worker runs it "
                + "with the backend's own limits; this console never starts processes.",
            TextWrapping = TextWrapping.Wrap,
        });
        panel.Children.Add(jobBox);
        panel.Children.Add(reasonBox);
        ContentDialog dialog = new()
        {
            Title = "Run maintenance job",
            Content = panel,
            PrimaryButtonText = "Queue job",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Close,
            XamlRoot = XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }

        string reason = reasonBox.Text.Trim();
        if (reason.Length < 4)
        {
            ShowCommandWarning("A reason of at least 4 characters is required.");
            return;
        }

        await SubmitCommandAsync(
            "run_job",
            reason,
            new Dictionary<string, object> { ["job"] = (string)jobBox.SelectedItem! });
    }

    private async Task<string?> ConfirmWithReasonAsync(
        string title,
        string body,
        string primaryText)
    {
        TextBox reasonBox = new() { PlaceholderText = "Reason (at least 4 characters)" };
        StackPanel panel = new() { Spacing = 12 };
        panel.Children.Add(new TextBlock { Text = body, TextWrapping = TextWrapping.Wrap });
        panel.Children.Add(reasonBox);
        ContentDialog dialog = new()
        {
            Title = title,
            Content = panel,
            PrimaryButtonText = primaryText,
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Close,
            XamlRoot = XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return null;
        }

        string reason = reasonBox.Text.Trim();
        if (reason.Length < 4)
        {
            ShowCommandWarning("A reason of at least 4 characters is required.");
            return null;
        }

        return reason;
    }

    private async Task SubmitCommandAsync(
        string command,
        string reason,
        IReadOnlyDictionary<string, object>? payload = null)
    {
        if (_isSubmittingCommand)
        {
            return;
        }

        _isSubmittingCommand = true;
        try
        {
            string actor = string.IsNullOrEmpty(Environment.UserName)
                ? "console"
                : $"console:{Environment.UserName}";
            CommandRequest request = new(
                $"con-{Guid.NewGuid():N}",
                command,
                reason,
                actor.Length > 64 ? actor[..64] : actor,
                payload);
            CommandAccepted accepted = await _apiClient.PostCommandAsync(request, _lifetime.Token);
            SystemInfoBar.Severity = InfoBarSeverity.Success;
            SystemInfoBar.Title = accepted.Created ? "Command queued" : "Command was already queued";
            SystemInfoBar.Message =
                $"{Pretty(command)} · status {accepted.Item.Status} · request {accepted.Item.RequestId}";
        }
        catch (OperationCanceledException) when (_lifetime.IsCancellationRequested)
        {
            return;
        }
        catch (Exception exception)
        {
            SystemInfoBar.Severity = InfoBarSeverity.Error;
            SystemInfoBar.Title = "Command rejected";
            SystemInfoBar.Message =
                exception.Message.Contains("403") || exception.Message.Contains("401")
                    ? "Authentication failed: set OPERATOR_API_KEY on the API and "
                        + "STRATUS_OPERATOR_API_KEY for this console."
                    : exception.Message;
            return;
        }
        finally
        {
            _isSubmittingCommand = false;
        }

        await RefreshAsync();
    }

    private async void ApiKeyButton_Click(object sender, RoutedEventArgs e)
    {
        PasswordBox keyBox = new()
        {
            PlaceholderText = "Paste the OPERATOR_API_KEY value",
            PasswordRevealMode = PasswordRevealMode.Peek,
        };
        StackPanel panel = new() { Spacing = 12 };
        panel.Children.Add(new TextBlock
        {
            Text = "The key is stored for this Windows user in Credential Locker. "
                + "Configure the same OPERATOR_API_KEY in the backend .env file.",
            TextWrapping = TextWrapping.Wrap,
        });
        panel.Children.Add(keyBox);
        panel.Children.Add(new TextBlock
        {
            Text = $"Current source: {_credentialSource}",
            TextWrapping = TextWrapping.Wrap,
        });
        ContentDialog dialog = new()
        {
            Title = "Operator API credential",
            Content = panel,
            PrimaryButtonText = "Save",
            SecondaryButtonText = "Remove saved key",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Close,
            XamlRoot = XamlRoot,
        };
        ContentDialogResult result = await dialog.ShowAsync();
        if (result == ContentDialogResult.None)
        {
            return;
        }

        string? apiKey = null;
        if (result == ContentDialogResult.Primary)
        {
            apiKey = keyBox.Password.Trim();
            if (apiKey.Length < 16)
            {
                ShowCommandWarning("The API key must contain at least 16 characters.");
                return;
            }
        }

        if (_isSubmittingCommand)
        {
            ShowCommandWarning("Wait for the current command submission to finish.");
            return;
        }
        for (int attempt = 0; _isRefreshing && attempt < 60; attempt++)
        {
            await Task.Delay(50);
            if (_lifetime.IsCancellationRequested)
            {
                return;
            }
        }
        if (_isRefreshing)
        {
            ShowCommandWarning("Wait for the current refresh to finish.");
            return;
        }

        _credentialStore.SaveApiKey(apiKey);
        StratusApiClient previous = _apiClient;
        _apiClient = new StratusApiClient(ApiBaseAddress, apiKey);
        previous.Dispose();
        _credentialSource = apiKey is null ? "Not configured" : "Windows Credential Locker";
        SystemInfoBar.Severity = InfoBarSeverity.Success;
        SystemInfoBar.Title = apiKey is null ? "Saved key removed" : "API key saved";
        SystemInfoBar.Message = apiKey is null
            ? "Monitoring remains available only while the backend does not require a key."
            : "The key is stored in Windows Credential Locker and is never displayed.";
        RenderSettings();
        await RefreshAsync();
    }

    private void ShowCommandWarning(string message)
    {
        SystemInfoBar.Severity = InfoBarSeverity.Warning;
        SystemInfoBar.Title = "Command not sent";
        SystemInfoBar.Message = message;
    }

    private void RenderLive(
        LiveSummaryDto summary,
        IReadOnlyList<EquityPointDto> equity,
        IReadOnlyList<OrderDto> orders,
        IReadOnlyList<PositionDto> positions)
    {
        List<OrderDto> live = orders
            .Where(order => string.Equals(order.Mode, "live", StringComparison.OrdinalIgnoreCase))
            .ToList();
        List<PositionDto> livePositions = positions
            .Where(p => string.Equals(p.Mode, "live", StringComparison.OrdinalIgnoreCase))
            .ToList();
        // Winning closed live positions still need a manual on-chain redeem.
        List<PositionDto> redeemPending = livePositions
            .Where(p => string.Equals(p.Status, "closed", StringComparison.OrdinalIgnoreCase)
                && p.PnlUsd is > 0)
            .ToList();

        List<MonitoringListItem> items =
        [
            new(
                "Live performance",
                summary.Positions == 0
                    ? "No live positions yet"
                    : $"{summary.Positions} positions · {summary.Open} open · {summary.Closed} closed",
                $"P&L {Usd(summary.TotalPnlUsd)} · ROI {Percent(summary.Roi)} · win {Percent(summary.WinRate)}",
                $"Avg CLV {Number(summary.AvgClv)} · beat line {Percent(summary.ClvBeatRate)} · "
                    + $"fees {Usd(summary.TotalFeesUsd)}",
                summary.ClvBeatRate is > 0.5 ? "Edge" : "Watch"),
            new(
                "Live executor",
                live.Count == 0 ? "No live orders" : $"{live.Count} live order(s)",
                redeemPending.Count == 0
                    ? "No manual redemptions pending"
                    : $"{redeemPending.Count} winning position(s) awaiting manual redeem",
                "Redemption of winnings is always a manual step in the Polymarket UI",
                redeemPending.Count == 0 ? "OK" : "Redeem"),
        ];
        items.AddRange(live.Select(order => new MonitoringListItem(
            $"{order.StationIcao ?? "Unmapped"} · {Pretty(order.Side)} · #{order.Id}",
            $"{Percent(order.Price)} · size {Usd(order.SizeUsd)} · filled {Usd(order.FilledUsd)}",
            $"CLOB {order.ClobOrderId ?? "—"} · fee {Usd(order.FeeUsd)}",
            order.FilledAt is null
                ? $"Created {LocalTime(order.CreatedAt)} · {order.PolymarketId}"
                : $"Filled {LocalTime(order.FilledAt.Value)} · {order.PolymarketId}",
            Pretty(order.Status))));
        items.AddRange(livePositions.Select(position => new MonitoringListItem(
            $"Position · {position.StationIcao ?? "Unmapped"} · {Pretty(position.Side)}",
            $"price {Percent(position.AveragePrice)} · size {Usd(position.SizeUsd)}",
            $"P&L {Usd(position.PnlUsd)} · CLV {Percent(position.Clv)} · fees {Usd(position.FeeUsd)}",
            position.ClosedAt is null ? position.PolymarketId : $"Closed · {position.PolymarketId}",
            Pretty(position.Status))));
        SetCollection(items);

        _equityPoints = equity;
        if (equity.Count >= 2)
        {
            EquityChartCard.Visibility = Visibility.Visible;
            DrawEquity();
        }
    }

    private IReadOnlyList<EquityPointDto> _equityPoints = [];

    private void EquityCanvas_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        if (EquityChartCard.Visibility == Visibility.Visible)
        {
            DrawEquity();
        }
    }

    private void DrawEquity()
    {
        EquityCanvas.Children.Clear();
        double width = EquityCanvas.ActualWidth;
        double height = EquityCanvas.ActualHeight;
        if (_equityPoints.Count < 2 || width <= 1 || height <= 1)
        {
            return;
        }

        double[] values = _equityPoints.Select(p => p.CumulativePnlUsd).ToArray();
        double lo = Math.Min(0, values.Min());
        double hi = Math.Max(0, values.Max());
        double span = hi - lo <= 0 ? 1 : hi - lo;
        double X(int i) => i * (width / (values.Length - 1));
        double Y(double v) => height - ((v - lo) / span * height);

        // Zero baseline.
        var zero = new Line
        {
            X1 = 0,
            X2 = width,
            Y1 = Y(0),
            Y2 = Y(0),
            Stroke = new SolidColorBrush(Color.FromArgb(0x66, 0x99, 0x99, 0x99)),
            StrokeThickness = 1,
        };
        EquityCanvas.Children.Add(zero);

        bool positive = values[^1] >= 0;
        var brush = new SolidColorBrush(positive
            ? Color.FromArgb(0xFF, 0x23, 0x86, 0x36)
            : Color.FromArgb(0xFF, 0xD1, 0x49, 0x5B));
        var line = new Polyline { Stroke = brush, StrokeThickness = 2 };
        for (int i = 0; i < values.Length; i++)
        {
            line.Points.Add(new Point(X(i), Y(values[i])));
        }
        EquityCanvas.Children.Add(line);

        var dot = new Ellipse { Width = 8, Height = 8, Fill = brush };
        Canvas.SetLeft(dot, X(values.Length - 1) - 4);
        Canvas.SetTop(dot, Y(values[^1]) - 4);
        EquityCanvas.Children.Add(dot);

        EquityChartTitle.Text =
            $"Live realised equity — {Usd(values[^1])} over {values.Length} trades";
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
                "Operator API credential",
                _credentialSource,
                "Stored secrets are never written to app settings or displayed after saving",
                "Use the API key command to save or remove the credential",
                _credentialSource == "Not configured" ? "Missing" : "Configured"),
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
        // Only the Live page shows the equity chart; every other list hides it.
        EquityChartCard.Visibility = Visibility.Collapsed;
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
        VerifyMarketButton.Visibility =
            _selectedDestination == "Markets" ? Visibility.Visible : Visibility.Collapsed;
        RunJobButton.Visibility =
            _selectedDestination == "Jobs" ? Visibility.Visible : Visibility.Collapsed;
        ResumeButton.Visibility =
            _selectedDestination == "Operations" ? Visibility.Visible : Visibility.Collapsed;
        ApiKeyButton.Visibility =
            _selectedDestination == "Settings" ? Visibility.Visible : Visibility.Collapsed;

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
            "Live" => "Live orders, positions, and pending manual redemptions",
            "Operations" => "Runtime control, operator command audit, and validation state",
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
