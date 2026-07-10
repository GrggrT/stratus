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
using Windows.UI.Text;

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
    private IReadOnlyList<PositionDto> _cockpitPositions = [];
    private IReadOnlyList<EquityPointDto> _cockpitEquity = [];
    private string? _cockpitMarketTitle;
    private IReadOnlyList<MarketPricePointDto> _cockpitMarketPrices = [];
    private IReadOnlyList<ProbabilityPointDto> _cockpitProbabilities = [];
    private IReadOnlyList<MarketPricePointDto> _drilldownPrices = [];
    private IReadOnlyList<ProbabilityPointDto> _drilldownProbabilities = [];
    private OrderBookResponse? _drilldownOrderBook;
    private MonitoringListItem? _selectedDrilldownItem;
    private IReadOnlyList<PositionDto> _portfolioPositions = [];
    private IReadOnlyList<EquityPointDto> _portfolioEquity = [];
    private IReadOnlyList<SignalDto> _signalPanelSignals = [];
    private IReadOnlyList<MarketDto> _marketBoardMarkets = [];
    private IReadOnlyList<CalibrationDto> _calibrationLabItems = [];

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
        ResizeCollectionView(ContentRoot.ActualWidth);
        ResizeDashboardContent(DashboardView.ActualWidth);
        _refreshTimer.Start();
        await RefreshAsync();
    }

    private void ContentRoot_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        ResizeCollectionView(e.NewSize.Width);
    }

    private void ResizeCollectionView(double viewportWidth)
    {
        if (double.IsNaN(viewportWidth) || viewportWidth <= 0)
        {
            return;
        }

        CollectionView.Width = Math.Min(1500, viewportWidth);
    }

    private void DashboardView_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        ResizeDashboardContent(e.NewSize.Width);
    }

    private void ResizeDashboardContent(double viewportWidth)
    {
        if (double.IsNaN(viewportWidth) || viewportWidth <= 0)
        {
            return;
        }

        DashboardContent.Width = Math.Min(1500, viewportWidth);
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
                    OverviewResponse cockpitOverview =
                        await _apiClient.GetOverviewAsync(_lifetime.Token);
                    CollectionResponse<SignalDto> cockpitSignals =
                        await _apiClient.GetSignalsAsync(_lifetime.Token);
                    CollectionResponse<PositionDto> cockpitPositions =
                        await _apiClient.GetPositionsAsync(_lifetime.Token);
                    EquityResponse cockpitEquity =
                        await _apiClient.GetEquityAsync("paper", _lifetime.Token);
                    RenderCockpit(
                        cockpitOverview,
                        cockpitSignals.Items,
                        cockpitPositions.Items,
                        cockpitEquity.Points);
                    break;
                case "Markets":
                    RenderMarkets((await _apiClient.GetMarketsAsync(_lifetime.Token)).Items);
                    break;
                case "Signals":
                    RenderSignals((await _apiClient.GetSignalsAsync(_lifetime.Token)).Items);
                    break;
                case "Positions":
                    CollectionResponse<PositionDto> allPositions =
                        await _apiClient.GetPositionsAsync(_lifetime.Token);
                    EquityResponse paperEquity =
                        await _apiClient.GetEquityAsync("paper", _lifetime.Token);
                    RenderPositions(allPositions.Items, paperEquity.Points);
                    break;
                case "Calibration":
                    RenderCalibrations((await _apiClient.GetCalibrationsAsync(_lifetime.Token)).Items);
                    break;
                case "Jobs":
                    RenderJobs((await _apiClient.GetJobsAsync(_lifetime.Token)).Items);
                    break;
                case "Live":
                    OverviewResponse liveOverview =
                        await _apiClient.GetOverviewAsync(_lifetime.Token);
                    CollectionResponse<CommandItem> liveCommandLog =
                        await _apiClient.GetCommandsAsync(_lifetime.Token);
                    LiveSummaryDto liveSummary = await _apiClient.GetLiveSummaryAsync(_lifetime.Token);
                    EquityResponse liveEquity = await _apiClient.GetLiveEquityAsync(_lifetime.Token);
                    CollectionResponse<OrderDto> liveOrders =
                        await _apiClient.GetOrdersAsync(_lifetime.Token);
                    CollectionResponse<PositionDto> livePositions =
                        await _apiClient.GetPositionsAsync(_lifetime.Token);
                    RenderLive(
                        liveOverview,
                        liveCommandLog.Items,
                        liveSummary,
                        liveEquity.Points,
                        liveOrders.Items,
                        livePositions.Items);
                    break;
                case "Operations":
                    OverviewResponse operationsOverview =
                        await _apiClient.GetOverviewAsync(_lifetime.Token);
                    CollectionResponse<CommandItem> commandLog =
                        await _apiClient.GetCommandsAsync(_lifetime.Token);
                    LiveSummaryDto operationsLiveSummary =
                        await _apiClient.GetLiveSummaryAsync(_lifetime.Token);
                    CollectionResponse<OrderDto> operationsOrders =
                        await _apiClient.GetOrdersAsync(_lifetime.Token);
                    RenderOperations(
                        operationsOverview,
                        commandLog.Items,
                        operationsLiveSummary,
                        operationsOrders.Items);
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
        // The green "ready" banner is reassurance for the Dashboard; on data
        // pages it just steals vertical space. Keep problems (warning/error)
        // visible everywhere.
        bool onDashboard = _selectedDestination == "Dashboard";
        SystemInfoBar.Visibility = (healthy && ready && !onDashboard)
            ? Visibility.Collapsed
            : Visibility.Visible;
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

    private void RenderCockpit(
        OverviewResponse overview,
        IReadOnlyList<SignalDto> signals,
        IReadOnlyList<PositionDto> positions,
        IReadOnlyList<EquityPointDto> paperEquity)
    {
        RenderOverview(overview);
        _cockpitPositions = positions;
        _cockpitEquity = paperEquity;

        List<PositionDto> open = positions
            .Where(p => string.Equals(p.Status, "open", StringComparison.OrdinalIgnoreCase))
            .ToList();
        List<PositionDto> closed = positions
            .Where(p => string.Equals(p.Status, "closed", StringComparison.OrdinalIgnoreCase))
            .ToList();
        double? unrealized = open.Any(p => p.UnrealizedPnlUsd is not null)
            ? open.Sum(p => p.UnrealizedPnlUsd ?? 0)
            : null;
        double realized = closed.Sum(p => p.PnlUsd ?? 0);
        _portfolioPositions = positions;
        _portfolioEquity = paperEquity;

        CockpitUnrealizedText.Text = Signed(unrealized);
        CockpitUnrealizedText.Foreground = AccentBrush(PnlAccent(unrealized));
        CockpitUnrealizedDetailText.Text =
            $"{open.Count} open positions · realised {Signed(realized)}";
        CockpitExposureText.Text = Usd(overview.OpenExposureUsd);
        CockpitExposureDetailText.Text =
            $"{overview.OpenPositions} open positions · {overview.PendingSignals} pending signals";
        CockpitRiskRuntime.Text = overview.Runtime.KillSwitch
            ? $"Kill switch active{ReasonSuffix(overview.Runtime.HaltedReason)}"
            : $"Mode {overview.Runtime.Mode.ToUpperInvariant()} · kill switch off";
        CockpitRiskHealth.Text = overview.HealthAudit is null
            ? "No health audit available"
            : $"{Pretty(overview.HealthAudit.Status)} · {overview.HealthAudit.Issues.Count} issue(s)";
        CockpitRiskActivity.Text =
            $"{overview.ActiveMarkets} active markets · {overview.BlockedMarkets} blocked";

        List<MonitoringListItem> scannerItems = BuildCockpitScanner(signals, open);
        CockpitScannerList.ItemsSource = scannerItems;
        CockpitScannerSubtitle.Text =
            $"{signals.Count} recent signal decisions · {open.Count} open positions";
        if (scannerItems.Count == 0)
        {
            RenderCockpitDetail(new MonitoringListItem(
                "No active opportunity",
                "Scanner is waiting for signals or open positions",
                "Collectors and models are still feeding the system.",
                "No row selected",
                "Idle"));
        }
        else
        {
            CockpitScannerList.SelectedIndex = 0;
            RenderCockpitDetail(scannerItems[0]);
        }

        DrawCockpitChart();
    }

    private static List<MonitoringListItem> BuildCockpitScanner(
        IReadOnlyList<SignalDto> signals,
        IReadOnlyList<PositionDto> openPositions)
    {
        IEnumerable<MonitoringListItem> signalItems = signals
            .OrderByDescending(signal => Math.Abs(signal.Edge))
            .Take(10)
            .Select(signal => new MonitoringListItem(
                $"{signal.StationIcao ?? "Unmapped"} · Signal · {Pretty(signal.Side)}",
                $"edge {Percent(signal.Edge)} · model {Percent(signal.ModelProbability)} · market {Percent(signal.MarketPrice)}",
                $"size {Usd(signal.SizeUsd)} · Kelly {Percent(signal.KellyFraction)} · confidence {signal.Confidence}",
                signal.SkipReason is null
                    ? $"{LocalTime(signal.Timestamp)} · {signal.PolymarketId}"
                    : $"{LocalTime(signal.Timestamp)} · {HumanizeReasons(signal.SkipReason)}",
                signal.Confidence,
                signal.Edge > 0 ? "positive" : "negative",
                signal.MarketId));
        IEnumerable<MonitoringListItem> positionItems = openPositions
            .OrderByDescending(position => Math.Abs(position.UnrealizedPnlUsd ?? 0))
            .Take(10)
            .Select(position => new MonitoringListItem(
                $"{position.StationIcao ?? "Unmapped"} · Position · {Pretty(position.Side)}",
                $"{position.MarketDate:MM-dd} {Pretty(position.Kind)} · entry {Percent(position.AveragePrice)}"
                    + (position.MarkPrice is null ? string.Empty : $" → mark {Percent(position.MarkPrice)}"),
                $"unrealized {Signed(position.UnrealizedPnlUsd)} · size {Usd(position.SizeUsd)} · fees {Usd(position.FeeUsd)}",
                position.PolymarketId,
                "Open",
                PnlAccent(position.UnrealizedPnlUsd),
                position.MarketId));
        return signalItems.Concat(positionItems).ToList();
    }

    private async void CockpitScannerList_ItemClick(object sender, ItemClickEventArgs e)
    {
        if (e.ClickedItem is MonitoringListItem item)
        {
            RenderCockpitDetail(item);
            if (item.MarketId is not null)
            {
                await LoadCockpitMarketChartAsync(item);
            }
        }
    }

    private void RenderCockpitDetail(MonitoringListItem item)
    {
        CockpitDetailStatusTag.Text = item.Status;
        CockpitDetailStatusBadge.Background = AccentBrush(item.Accent);
        CockpitDetailTitle.Text = item.Title;
        CockpitDetailPrimary.Text = item.Subtitle;
        CockpitDetailSecondary.Text = item.Detail;
        CockpitDetailMeta.Text = item.Meta;
        CockpitDetailHint.Text = item.Title.Contains("Signal", StringComparison.OrdinalIgnoreCase)
            ? "Signal context: model-vs-market edge, sizing, confidence, and skip rationale."
            : item.Title.Contains("Position", StringComparison.OrdinalIgnoreCase)
                ? "Position context: entry, current mark, unrealized P&L, fees, and market id."
                : "Click a scanner row to inspect the signal or position.";
    }

    private async Task LoadCockpitMarketChartAsync(MonitoringListItem item)
    {
        if (item.MarketId is null)
        {
            return;
        }

        try
        {
            MarketPriceSeriesResponse prices =
                await _apiClient.GetMarketPricesAsync(item.MarketId.Value, _lifetime.Token);
            ProbabilitySeriesResponse probabilities =
                await _apiClient.GetProbabilitySeriesAsync(item.MarketId.Value, _lifetime.Token);
            _cockpitMarketTitle = item.Title;
            _cockpitMarketPrices = prices.Points;
            _cockpitProbabilities = probabilities.Points;
            DrawCockpitChart();
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception ex)
        {
            CockpitDetailMeta.Text = $"Drill-down unavailable: {ex.Message}";
        }
    }

    private async void CollectionList_ItemClick(object sender, ItemClickEventArgs e)
    {
        if (e.ClickedItem is not MonitoringListItem item || item.MarketId is null)
        {
            HideMarketDrilldown();
            return;
        }

        await LoadMarketDrilldownAsync(item);
    }

    private void HideMarketDrilldown()
    {
        MarketDrilldownPanel.Visibility = Visibility.Collapsed;
        CollectionDetailColumn.Width = new GridLength(0);
        _drilldownPrices = [];
        _drilldownProbabilities = [];
        _drilldownOrderBook = null;
        _selectedDrilldownItem = null;
    }

    private async Task LoadMarketDrilldownAsync(MonitoringListItem item)
    {
        if (item.MarketId is null)
        {
            HideMarketDrilldown();
            return;
        }

        _selectedDrilldownItem = item;
        CollectionDetailColumn.Width = new GridLength(460);
        MarketDrilldownPanel.Visibility = Visibility.Visible;
        DrilldownTitle.Text = item.Title;
        DrilldownSummaryText.Text = "Loading market series and orderbook...";
        DrilldownMetaText.Text = item.Meta;
        OrderBookBidsText.Text = "Loading";
        OrderBookAsksText.Text = "Loading";
        DrilldownChartCanvas.Children.Clear();

        try
        {
            Task<MarketPriceSeriesResponse> pricesTask =
                _apiClient.GetMarketPricesAsync(item.MarketId.Value, _lifetime.Token);
            Task<ProbabilitySeriesResponse> probabilitiesTask =
                _apiClient.GetProbabilitySeriesAsync(item.MarketId.Value, _lifetime.Token);
            Task<OrderBookResponse> orderBookTask =
                _apiClient.GetOrderBookAsync(item.MarketId.Value, cancellationToken: _lifetime.Token);

            await Task.WhenAll(pricesTask, probabilitiesTask, orderBookTask);
            _drilldownPrices = pricesTask.Result.Points;
            _drilldownProbabilities = probabilitiesTask.Result.Points;
            _drilldownOrderBook = orderBookTask.Result;

            MarketPricePointDto? latestPrice = _drilldownPrices.LastOrDefault();
            ProbabilityPointDto? latestSignal = _drilldownProbabilities
                .LastOrDefault(point => point.Source == "signal");
            DrilldownSummaryText.Text =
                $"mid {Percent(latestPrice?.Midpoint)} · model {Percent(latestSignal?.ModelProbability)}"
                + $" · edge {Percent(latestSignal?.Edge)}";
            DrilldownMetaText.Text =
                $"{_drilldownPrices.Count} price point(s) · {_drilldownProbabilities.Count} probability/signal point(s)"
                + $" · book {_drilldownOrderBook.SourceStatus}";
            if (!string.IsNullOrWhiteSpace(_drilldownOrderBook.Error))
            {
                DrilldownMetaText.Text += $" · {_drilldownOrderBook.Error}";
            }
            OrderBookBidsText.Text = FormatBookLevels(_drilldownOrderBook.Bids);
            OrderBookAsksText.Text = FormatBookLevels(_drilldownOrderBook.Asks);
            DrawDrilldownChart();
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception ex)
        {
            DrilldownSummaryText.Text = "Drill-down unavailable";
            DrilldownMetaText.Text = ex.Message;
            OrderBookBidsText.Text = "—";
            OrderBookAsksText.Text = "—";
        }
    }

    private static string FormatBookLevels(IReadOnlyList<OrderBookLevelDto> levels) =>
        levels.Count == 0
            ? "—"
            : string.Join(
                Environment.NewLine,
                levels.Take(3).Select(level =>
                    $"{level.Price:0.000} × {level.Size:0.##}  ${level.NotionalUsd:0.00}"));

    private void RenderMarkets(IReadOnlyList<MarketDto> markets)
    {
        List<MarketDto> ordered = markets
            .OrderBy(market => market.ResolvesAt)
            .ToList();
        List<MarketDto> filtered = FilterMarkets(ordered).ToList();
        _marketBoardMarkets = ordered;

        SetCollection(filtered.Select(market => new MonitoringListItem(
            $"{market.StationIcao ?? "Unmapped"} · {market.MarketDate:yyyy-MM-dd} · {Pretty(market.Kind)}",
            $"{market.City ?? "Unknown city"} · bucket {TemperatureRange(market.BucketLowC, market.BucketHighC)}",
            market.StationVerified
                ? "Station rules verified"
                : $"Verification blocked{ReasonSuffix(HumanizeReasons(market.VerificationReason))}",
            $"Resolves {LocalTime(market.ResolvesAt)} · market #{market.Id}",
            Pretty(market.Status),
            MarketAccent(market),
            market.Id)));
        RenderMarketBoard(ordered, filtered);
    }

    private string _marketsFilter = "Active";

    private async void MarketsFilterBox_SelectionChanged(
        object sender,
        SelectionChangedEventArgs e)
    {
        if (!IsLoaded)
        {
            return;
        }

        _marketsFilter =
            (MarketsFilterBox.SelectedItem as ComboBoxItem)?.Content as string ?? "Active";
        await RefreshAsync();
    }

    private IEnumerable<MarketDto> FilterMarkets(IReadOnlyList<MarketDto> markets)
    {
        DateTimeOffset soon = DateTimeOffset.Now.AddHours(36);
        return _marketsFilter switch
        {
            "Resolving soon" => markets.Where(market =>
                IsMarketStatus(market, "active") && market.ResolvesAt <= soon),
            "Needs verify" => markets.Where(market => !market.StationVerified),
            "Blocked" => markets.Where(market => IsMarketStatus(market, "blocked")),
            "All" => markets,
            _ => markets.Where(market => IsMarketStatus(market, "active")),
        };
    }

    private static bool IsMarketStatus(MarketDto market, string status) =>
        market.Status.Equals(status, StringComparison.OrdinalIgnoreCase);

    private static string? MarketAccent(MarketDto market) =>
        !market.StationVerified || IsMarketStatus(market, "blocked")
            ? "negative"
            : IsMarketStatus(market, "active")
                ? "positive"
                : null;

    private void RenderMarketBoard(
        IReadOnlyList<MarketDto> markets,
        IReadOnlyList<MarketDto> filtered)
    {
        MarketBoardPanel.Visibility = Visibility.Visible;
        int active = markets.Count(market => IsMarketStatus(market, "active"));
        int blocked = markets.Count(market => IsMarketStatus(market, "blocked"));
        int resolved = markets.Count(market => IsMarketStatus(market, "resolved"));
        int verified = markets.Count(market => market.StationVerified);
        MarketBoardSummaryText.Text =
            $"{filtered.Count}/{markets.Count} shown · {active} active · {blocked} blocked · {verified} verified";

        MarketCoverageSummaryText.Text = markets.Count == 0
            ? "No market coverage loaded"
            : $"{markets.Select(market => market.StationIcao ?? "Unmapped").Distinct().Count()} station(s)"
                + $" · {markets.Select(market => market.MarketDate).Distinct().Count()} date(s)"
                + $" · filter {_marketsFilter}";

        MarketStatusSummaryText.Text =
            $"active {active} · blocked {blocked} · resolved {resolved} · other {Math.Max(0, markets.Count - active - blocked - resolved)}";

        List<MarketDto> needsVerify = markets
            .Where(market => !market.StationVerified)
            .Take(4)
            .ToList();
        MarketVerificationQueueText.Text = needsVerify.Count == 0
            ? "Verification queue clear."
            : string.Join(
                Environment.NewLine,
                needsVerify.Select(market =>
                    $"{market.StationIcao ?? "Unmapped"} · market #{market.Id} · {HumanizeReasons(market.VerificationReason)}"));

        MarketDto? next = markets
            .Where(market => IsMarketStatus(market, "active"))
            .OrderBy(market => market.ResolvesAt)
            .FirstOrDefault();
        MarketNextResolveText.Text = next is null
            ? "No active market resolution is queued."
            : $"Next resolve · {next.StationIcao ?? "Unmapped"} · {next.MarketDate:yyyy-MM-dd}"
                + $" · {Pretty(next.Kind)} · {LocalTime(next.ResolvesAt)}";

        DrawMarketCoverage();
        DrawMarketStatusMix();
    }

    private void MarketCoverageCanvas_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        if (MarketBoardPanel.Visibility == Visibility.Visible)
        {
            DrawMarketCoverage();
        }
    }

    private void MarketStatusCanvas_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        if (MarketBoardPanel.Visibility == Visibility.Visible)
        {
            DrawMarketStatusMix();
        }
    }

    private void DrawMarketCoverage()
    {
        MarketCoverageCanvas.Children.Clear();
        double width = MarketCoverageCanvas.ActualWidth;
        double height = MarketCoverageCanvas.ActualHeight;
        if (width <= 1 || height <= 1)
        {
            return;
        }

        List<MarketDto> markets = _marketBoardMarkets.ToList();
        if (markets.Count == 0)
        {
            AddCanvasText(
                MarketCoverageCanvas,
                "No markets yet",
                18,
                20,
                new SolidColorBrush(Color.FromArgb(0xCC, 0xFF, 0xFF, 0xFF)),
                16,
                new FontWeight { Weight = 600 });
            AddCanvasText(
                MarketCoverageCanvas,
                "Run discovery to populate station/date market coverage.",
                18,
                52,
                new SolidColorBrush(Color.FromArgb(0xAA, 0xFF, 0xFF, 0xFF)),
                13);
            return;
        }

        var cells = markets
            .GroupBy(market => new
            {
                Station = market.StationIcao ?? "Unmapped",
                market.MarketDate,
            })
            .Select(group => new
            {
                group.Key.Station,
                group.Key.MarketDate,
                Count = group.Count(),
                Verified = group.Count(market => market.StationVerified),
                Blocked = group.Count(market => IsMarketStatus(market, "blocked")),
                Active = group.Count(market => IsMarketStatus(market, "active")),
                Sample = group.OrderBy(market => market.ResolvesAt).First(),
            })
            .OrderBy(cell => cell.Station)
            .ThenBy(cell => cell.MarketDate)
            .ToList();
        string[] stations = cells
            .Select(cell => cell.Station)
            .Distinct()
            .OrderBy(station => station)
            .Take(6)
            .ToArray();
        DateOnly[] dates = cells
            .Select(cell => cell.MarketDate)
            .Distinct()
            .OrderBy(date => date)
            .Take(7)
            .ToArray();
        if (stations.Length == 0 || dates.Length == 0)
        {
            return;
        }

        double left = 64;
        double top = 28;
        double gap = 5;
        double cellWidth = Math.Max(32, (width - left - 6 - gap * (dates.Length - 1)) / dates.Length);
        double cellHeight = Math.Max(26, (height - top - 6 - gap * (stations.Length - 1)) / stations.Length);
        int maxCount = Math.Max(1, cells.Max(cell => cell.Count));

        for (int dateIndex = 0; dateIndex < dates.Length; dateIndex++)
        {
            AddCanvasText(
                MarketCoverageCanvas,
                dates[dateIndex].ToString("MM-dd", CultureInfo.InvariantCulture),
                left + dateIndex * (cellWidth + gap),
                4,
                new SolidColorBrush(Color.FromArgb(0xAA, 0xFF, 0xFF, 0xFF)),
                11);
        }

        for (int stationIndex = 0; stationIndex < stations.Length; stationIndex++)
        {
            string station = stations[stationIndex];
            double y = top + stationIndex * (cellHeight + gap);
            AddCanvasText(
                MarketCoverageCanvas,
                station,
                4,
                y + 6,
                new SolidColorBrush(Color.FromArgb(0xCC, 0xFF, 0xFF, 0xFF)),
                11);

            for (int dateIndex = 0; dateIndex < dates.Length; dateIndex++)
            {
                DateOnly date = dates[dateIndex];
                var cell = cells.FirstOrDefault(item =>
                    item.Station == station && item.MarketDate == date);
                double x = left + dateIndex * (cellWidth + gap);
                if (cell is null)
                {
                    var emptyRect = new Rectangle
                    {
                        Width = cellWidth,
                        Height = cellHeight,
                        RadiusX = 5,
                        RadiusY = 5,
                        Stroke = new SolidColorBrush(Color.FromArgb(0x22, 0xFF, 0xFF, 0xFF)),
                        StrokeThickness = 1,
                        Fill = new SolidColorBrush(Color.FromArgb(0x18, 0xFF, 0xFF, 0xFF)),
                    };
                    Canvas.SetLeft(emptyRect, x);
                    Canvas.SetTop(emptyRect, y);
                    MarketCoverageCanvas.Children.Add(emptyRect);
                    continue;
                }

                byte alpha = (byte)Math.Clamp(80 + (double)cell.Count / maxCount * 150, 80, 230);
                Brush fill = cell.Blocked > 0 || cell.Verified < cell.Count
                    ? new SolidColorBrush(Color.FromArgb(alpha, 0xD1, 0x49, 0x5B))
                    : cell.Active > 0
                        ? new SolidColorBrush(Color.FromArgb(alpha, 0x31, 0xC4, 0x8D))
                        : new SolidColorBrush(Color.FromArgb(alpha, 0x8A, 0x8A, 0x8A));
                var rect = new Rectangle
                {
                    Width = cellWidth,
                    Height = cellHeight,
                    RadiusX = 5,
                    RadiusY = 5,
                    Fill = fill,
                };
                Canvas.SetLeft(rect, x);
                Canvas.SetTop(rect, y);
                MarketCoverageCanvas.Children.Add(rect);
                AddCanvasText(
                    MarketCoverageCanvas,
                    cell.Count.ToString(CultureInfo.InvariantCulture),
                    x + 7,
                    y + 5,
                    new SolidColorBrush(Colors.White),
                    11,
                    new FontWeight { Weight = 600 });
            }
        }
    }

    private void DrawMarketStatusMix()
    {
        MarketStatusCanvas.Children.Clear();
        double width = MarketStatusCanvas.ActualWidth;
        double height = MarketStatusCanvas.ActualHeight;
        if (width <= 1 || height <= 1)
        {
            return;
        }

        int active = _marketBoardMarkets.Count(market => IsMarketStatus(market, "active"));
        int blocked = _marketBoardMarkets.Count(market => IsMarketStatus(market, "blocked"));
        int resolved = _marketBoardMarkets.Count(market => IsMarketStatus(market, "resolved"));
        int unverified = _marketBoardMarkets.Count(market => !market.StationVerified);
        int other = Math.Max(0, _marketBoardMarkets.Count - active - blocked - resolved);
        int total = active + blocked + resolved + other;
        if (total == 0)
        {
            AddCanvasText(
                MarketStatusCanvas,
                "No status mix yet",
                12,
                16,
                new SolidColorBrush(Color.FromArgb(0xCC, 0xFF, 0xFF, 0xFF)),
                13,
                new FontWeight { Weight = 600 });
            return;
        }

        double x = 0;
        double y = 28;
        double barHeight = 28;
        (string Label, int Count, Brush Brush)[] segments =
        [
            ("Active", active, AccentBrush("positive")),
            ("Blocked", blocked, AccentBrush("negative")),
            ("Resolved", resolved, new SolidColorBrush(Color.FromArgb(0xFF, 0x8A, 0x8A, 0x8A))),
            ("Other", other, new SolidColorBrush(Color.FromArgb(0xFF, 0x6D, 0x8D, 0xC7))),
        ];
        foreach ((string label, int count, Brush brush) in segments.Where(segment => segment.Count > 0))
        {
            double segmentWidth = Math.Max(3, width * count / total);
            var rect = new Rectangle
            {
                Width = segmentWidth,
                Height = barHeight,
                RadiusX = 4,
                RadiusY = 4,
                Fill = brush,
            };
            Canvas.SetLeft(rect, x);
            Canvas.SetTop(rect, y);
            MarketStatusCanvas.Children.Add(rect);
            if (segmentWidth > 70)
            {
                AddCanvasText(
                    MarketStatusCanvas,
                    $"{label} {count}",
                    x + 8,
                    y + 5,
                    new SolidColorBrush(Colors.White),
                    11,
                    new FontWeight { Weight = 600 });
            }

            x += segmentWidth;
        }

        if (unverified > 0)
        {
            AddCanvasText(
                MarketStatusCanvas,
                $"{unverified} unverified",
                0,
                68,
                AccentBrush("negative"),
                12,
                new FontWeight { Weight = 600 });
        }
    }

    private string _signalsFilter = "All";

    private async void SignalsFilterBox_SelectionChanged(
        object sender,
        SelectionChangedEventArgs e)
    {
        if (!IsLoaded)
        {
            return;
        }

        _signalsFilter =
            (SignalsFilterBox.SelectedItem as ComboBoxItem)?.Content as string ?? "All";
        await RefreshAsync();
    }

    private void RenderSignals(IReadOnlyList<SignalDto> signals)
    {
        List<SignalDto> recent = signals
            .OrderByDescending(signal => signal.Timestamp)
            .ToList();
        List<SignalDto> filtered = FilterSignals(recent).ToList();
        _signalPanelSignals = recent;

        SetCollection(filtered.Select(signal => new MonitoringListItem(
            $"{signal.StationIcao ?? "Unmapped"} · {Pretty(signal.Side)}",
            $"Model {Percent(signal.ModelProbability)} · market {Percent(signal.MarketPrice)} · edge {Percent(signal.Edge)}",
            $"Size {Usd(signal.SizeUsd)} · Kelly {Percent(signal.KellyFraction)} · confidence {signal.Confidence}",
            $"{LocalTime(signal.Timestamp)} · "
                + (signal.SkipReason is null ? signal.PolymarketId : HumanizeReasons(signal.SkipReason)),
            Pretty(signal.Status),
            IsActionableSignal(signal) ? PnlAccent(signal.Edge) : null,
            signal.MarketId)));
        RenderSignalIntelligence(recent, filtered);
    }

    private IEnumerable<SignalDto> FilterSignals(IReadOnlyList<SignalDto> signals) =>
        _signalsFilter switch
        {
            "Skipped" => signals.Where(signal => !IsActionableSignal(signal)),
            "A+B" => signals.Where(signal => signal.Confidence is "A" or "B"),
            "All" => signals,
            _ => signals.Where(IsActionableSignal),
        };

    private static bool IsActionableSignal(SignalDto signal) =>
        signal.SkipReason is null
        && !signal.Status.Equals("skipped", StringComparison.OrdinalIgnoreCase)
        && !signal.Status.Equals("expired", StringComparison.OrdinalIgnoreCase);

    private void RenderSignalIntelligence(
        IReadOnlyList<SignalDto> signals,
        IReadOnlyList<SignalDto> filtered)
    {
        SignalIntelligencePanel.Visibility = Visibility.Visible;
        int actionable = signals.Count(IsActionableSignal);
        int skipped = signals.Count - actionable;
        double averageAbsEdge = signals.Count == 0 ? 0 : signals.Average(signal => Math.Abs(signal.Edge));
        double actionableSize = signals.Where(IsActionableSignal).Sum(signal => signal.SizeUsd);
        SignalIntelligenceSummaryText.Text =
            $"{filtered.Count}/{signals.Count} shown · {actionable} actionable · {skipped} blocked";

        SignalEdgeSummaryText.Text = signals.Count == 0
            ? "No signal tape loaded"
            : $"avg |edge| {Percent(averageAbsEdge)} · actionable size {Usd(actionableSize)} · filter {_signalsFilter}";

        int pending = signals.Count(signal => signal.Status.Equals("pending", StringComparison.OrdinalIgnoreCase));
        int executed = signals.Count(signal => signal.Status.Equals("executed", StringComparison.OrdinalIgnoreCase));
        int expired = signals.Count(signal => signal.Status.Equals("expired", StringComparison.OrdinalIgnoreCase));
        SignalMixSummaryText.Text =
            $"pending {pending} · executed {executed} · skipped {skipped} · expired {expired}";

        SignalDto? top = signals
            .Where(IsActionableSignal)
            .OrderByDescending(signal => Math.Abs(signal.Edge))
            .FirstOrDefault();
        SignalTopOpportunityText.Text = top is null
            ? "No actionable signal in the current window."
            : $"{top.StationIcao ?? "Unmapped"} · {Pretty(top.Side)} · edge {Percent(top.Edge)}"
                + Environment.NewLine
                + $"model {Percent(top.ModelProbability)} vs market {Percent(top.MarketPrice)}"
                + Environment.NewLine
                + $"size {Usd(top.SizeUsd)} · Kelly {Percent(top.KellyFraction)} · confidence {top.Confidence}";

        List<string> skipReasons = signals
            .Where(signal => signal.SkipReason is not null)
            .GroupBy(signal => HumanizeReasons(signal.SkipReason))
            .OrderByDescending(group => group.Count())
            .ThenBy(group => group.Key)
            .Take(3)
            .Select(group => $"{group.Key}: {group.Count()}")
            .ToList();
        SignalSkipSummaryText.Text = skipReasons.Count == 0
            ? "No structured skip reasons in the current signal window."
            : "Top blocks · " + string.Join(" · ", skipReasons);

        DrawSignalEdgeTape();
        DrawSignalDecisionMix();
    }

    private void SignalEdgeCanvas_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        if (SignalIntelligencePanel.Visibility == Visibility.Visible)
        {
            DrawSignalEdgeTape();
        }
    }

    private void SignalMixCanvas_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        if (SignalIntelligencePanel.Visibility == Visibility.Visible)
        {
            DrawSignalDecisionMix();
        }
    }

    private void DrawSignalEdgeTape()
    {
        SignalEdgeCanvas.Children.Clear();
        double width = SignalEdgeCanvas.ActualWidth;
        double height = SignalEdgeCanvas.ActualHeight;
        if (width <= 1 || height <= 1)
        {
            return;
        }

        List<double> edges = _signalPanelSignals
            .OrderBy(signal => signal.Timestamp)
            .TakeLast(40)
            .Select(signal => signal.Edge)
            .ToList();
        if (edges.Count == 0)
        {
            AddCanvasText(
                SignalEdgeCanvas,
                "No signals yet",
                18,
                20,
                new SolidColorBrush(Color.FromArgb(0xCC, 0xFF, 0xFF, 0xFF)),
                16,
                new FontWeight { Weight = 600 });
            AddCanvasText(
                SignalEdgeCanvas,
                "The tape will fill after signal generation runs.",
                18,
                52,
                new SolidColorBrush(Color.FromArgb(0xAA, 0xFF, 0xFF, 0xFF)),
                13);
            return;
        }

        DrawBarSeries(SignalEdgeCanvas, edges, width, height);
        SignalDto latest = _signalPanelSignals.OrderByDescending(signal => signal.Timestamp).First();
        AddCanvasText(
            SignalEdgeCanvas,
            $"latest {Percent(latest.Edge)} · {latest.StationIcao ?? "Unmapped"} · {Pretty(latest.Side)}",
            16,
            12,
            new SolidColorBrush(Color.FromArgb(0xDD, 0xFF, 0xFF, 0xFF)),
            13,
            new FontWeight { Weight = 600 });
    }

    private void DrawSignalDecisionMix()
    {
        SignalMixCanvas.Children.Clear();
        double width = SignalMixCanvas.ActualWidth;
        double height = SignalMixCanvas.ActualHeight;
        if (width <= 1 || height <= 1)
        {
            return;
        }

        int actionable = _signalPanelSignals.Count(IsActionableSignal);
        int skipped = _signalPanelSignals.Count(signal =>
            signal.Status.Equals("skipped", StringComparison.OrdinalIgnoreCase));
        int expired = _signalPanelSignals.Count(signal =>
            signal.Status.Equals("expired", StringComparison.OrdinalIgnoreCase));
        int other = Math.Max(0, _signalPanelSignals.Count - actionable - skipped - expired);
        int total = actionable + skipped + expired + other;
        if (total == 0)
        {
            AddCanvasText(
                SignalMixCanvas,
                "No decision mix yet",
                12,
                16,
                new SolidColorBrush(Color.FromArgb(0xCC, 0xFF, 0xFF, 0xFF)),
                13,
                new FontWeight { Weight = 600 });
            return;
        }

        double x = 0;
        double y = 28;
        double barHeight = 28;
        (string Label, int Count, Brush Brush)[] segments =
        [
            ("Actionable", actionable, AccentBrush("positive")),
            ("Skipped", skipped, new SolidColorBrush(Color.FromArgb(0xFF, 0x8A, 0x8A, 0x8A))),
            ("Expired", expired, AccentBrush("negative")),
            ("Other", other, new SolidColorBrush(Color.FromArgb(0xFF, 0x6D, 0x8D, 0xC7))),
        ];
        foreach ((string label, int count, Brush brush) in segments.Where(segment => segment.Count > 0))
        {
            double segmentWidth = Math.Max(3, width * count / total);
            var rect = new Rectangle
            {
                Width = segmentWidth,
                Height = barHeight,
                RadiusX = 4,
                RadiusY = 4,
                Fill = brush,
            };
            Canvas.SetLeft(rect, x);
            Canvas.SetTop(rect, y);
            SignalMixCanvas.Children.Add(rect);
            if (segmentWidth > 70)
            {
                AddCanvasText(
                    SignalMixCanvas,
                    $"{label} {count}",
                    x + 8,
                    y + 5,
                    new SolidColorBrush(Colors.White),
                    11,
                    new FontWeight { Weight = 600 });
            }

            x += segmentWidth;
        }
    }

    private string _positionsFilter = "Open";

    private async void PositionsFilterBox_SelectionChanged(
        object sender, SelectionChangedEventArgs e)
    {
        if (!IsLoaded)
        {
            return;
        }

        _positionsFilter =
            (PositionsFilterBox.SelectedItem as ComboBoxItem)?.Content as string ?? "Open";
        await RefreshAsync();
    }

    private static string? PnlAccent(double? value) =>
        value is null or 0 ? null : value > 0 ? "positive" : "negative";

    private void RenderPositions(
        IReadOnlyList<PositionDto> positions,
        IReadOnlyList<EquityPointDto> paperEquity)
    {
        List<PositionDto> open = positions
            .Where(p => string.Equals(p.Status, "open", StringComparison.OrdinalIgnoreCase))
            .ToList();
        List<PositionDto> closed = positions
            .Where(p => string.Equals(p.Status, "closed", StringComparison.OrdinalIgnoreCase))
            .ToList();
        List<PositionDto> shown = _positionsFilter switch
        {
            "Open" => open,
            "Closed" => closed,
            _ => positions.ToList(),
        };

        double openCost = open.Sum(p => p.SizeUsd);
        double? unrealized = open.Any(p => p.UnrealizedPnlUsd is not null)
            ? open.Sum(p => p.UnrealizedPnlUsd ?? 0)
            : null;
        double realized = closed.Sum(p => p.PnlUsd ?? 0);

        List<MonitoringListItem> items =
        [
            new(
                "Portfolio",
                $"{open.Count} open (cost {Usd(openCost)}) · {closed.Count} closed",
                $"Unrealized {Signed(unrealized)} · Realized {Signed(realized)}",
                $"Showing: {_positionsFilter.ToLowerInvariant()} positions",
                Signed(unrealized is null ? realized : unrealized + realized),
                PnlAccent((unrealized ?? 0) + realized)),
        ];
        items.AddRange(shown.Select(position =>
        {
            bool isOpen = position.ClosedAt is null;
            double? pnl = isOpen ? position.UnrealizedPnlUsd : position.PnlUsd;
            return new MonitoringListItem(
                $"{position.StationIcao ?? "Unmapped"} · {position.MarketDate:MM-dd} "
                    + $"{Pretty(position.Kind)} · {Pretty(position.Side)}",
                $"{Pretty(position.Mode)} · entry {Percent(position.AveragePrice)}"
                    + (position.MarkPrice is null ? "" : $" → mark {Percent(position.MarkPrice)}")
                    + $" · size {Usd(position.SizeUsd)}",
                (isOpen
                    ? $"Unrealized {Signed(position.UnrealizedPnlUsd)}"
                    : $"P&L {Signed(position.PnlUsd)} · CLV {Percent(position.Clv)}")
                    + $" · fees {Usd(position.FeeUsd)}",
                position.ClosedAt is null
                    ? position.PolymarketId
                    : $"Closed {LocalTime(position.ClosedAt.Value)} · {position.PolymarketId}",
                Pretty(position.Status),
                PnlAccent(pnl),
                position.MarketId);
        }));
        SetCollection(items);
        PortfolioAnalyticsPanel.Visibility = Visibility.Visible;
        PortfolioAnalyticsSubtitle.Text =
            $"{open.Count} open · {closed.Count} closed · open cost {Usd(openCost)}"
            + $" · unrealised {Signed(unrealized)} · realised {Signed(realized)}";
        PortfolioEquitySummaryText.Text =
            $"Realised {Signed(realized)} · unrealised {Signed(unrealized)}"
            + $" · total mark {Signed((unrealized ?? 0) + realized)}";
        PortfolioExposureSummaryText.Text =
            $"{open.Count} open position(s) across "
            + $"{open.Select(p => p.StationIcao ?? "Unmapped").Distinct().Count()} station(s)";
        DrawPortfolioAnalytics();
    }

    private string _calibrationFilter = "All";

    private async void CalibrationFilterBox_SelectionChanged(
        object sender,
        SelectionChangedEventArgs e)
    {
        if (!IsLoaded)
        {
            return;
        }

        _calibrationFilter =
            (CalibrationFilterBox.SelectedItem as ComboBoxItem)?.Content as string ?? "All";
        await RefreshAsync();
    }

    private void RenderCalibrations(IReadOnlyList<CalibrationDto> calibrations)
    {
        List<CalibrationDto> ordered = calibrations
            .OrderBy(calibration => calibration.StationIcao)
            .ThenBy(calibration => calibration.HorizonBucket)
            .ThenBy(calibration => calibration.Kind)
            .ToList();
        List<CalibrationDto> filtered = FilterCalibrations(ordered).ToList();
        _calibrationLabItems = ordered;

        SetCollection(filtered.Select(calibration => new MonitoringListItem(
            $"{calibration.StationIcao} · {calibration.Model.ToUpperInvariant()} · {Pretty(calibration.Kind)}",
            $"Horizon {calibration.HorizonBucket} · n={calibration.SampleCount}",
            $"Bias {calibration.BiasC:+0.00;-0.00;0.00} °C · sigma {calibration.SigmaC:0.00} °C · Brier {Number(calibration.Brier)}",
            $"Updated {LocalTime(calibration.UpdatedAt)}",
            CalibrationStatus(calibration),
            CalibrationAccent(calibration))));
        CollectionEmptyText.Text = _calibrationFilter == "All"
            ? "No calibration records yet"
            : "No calibration records match this filter";
        RenderCalibrationLab(ordered, filtered);
    }

    private IEnumerable<CalibrationDto> FilterCalibrations(IReadOnlyList<CalibrationDto> calibrations) =>
        _calibrationFilter switch
        {
            "Mature" => calibrations.Where(calibration => calibration.SampleCount >= 100),
            "Needs data" => calibrations.Where(calibration => calibration.SampleCount < 100),
            "Review risk" => calibrations.Where(IsCalibrationRisky),
            _ => calibrations,
        };

    private static bool IsCalibrationRisky(CalibrationDto calibration) =>
        calibration.Brier is null
        || calibration.Brier > 0.18
        || calibration.SampleCount < 30
        || Math.Abs(calibration.BiasC) > 1.0;

    private static string CalibrationStatus(CalibrationDto calibration)
    {
        if (calibration.SampleCount < 30)
        {
            return "Sparse";
        }

        if (calibration.Brier is null)
        {
            return "No Brier";
        }

        if (calibration.SampleCount >= 100 && calibration.Brier <= 0.18 && Math.Abs(calibration.BiasC) <= 1.0)
        {
            return "Gate ready";
        }

        return calibration.Brier <= 0.18 ? "Collecting" : "Review";
    }

    private static string? CalibrationAccent(CalibrationDto calibration) =>
        CalibrationStatus(calibration) switch
        {
            "Gate ready" => "positive",
            "Review" or "Sparse" or "No Brier" => "negative",
            _ => null,
        };

    private void RenderCalibrationLab(
        IReadOnlyList<CalibrationDto> calibrations,
        IReadOnlyList<CalibrationDto> filtered)
    {
        CalibrationLabPanel.Visibility = Visibility.Visible;
        int mature = calibrations.Count(calibration => calibration.SampleCount >= 100);
        int gateReady = calibrations.Count(calibration => CalibrationStatus(calibration) == "Gate ready");
        int risk = calibrations.Count(IsCalibrationRisky);
        int totalSamples = calibrations.Sum(calibration => calibration.SampleCount);
        double? averageBrier = calibrations
            .Where(calibration => calibration.Brier is not null)
            .Select(calibration => calibration.Brier!.Value)
            .DefaultIfEmpty(double.NaN)
            .Average();
        if (double.IsNaN(averageBrier.Value))
        {
            averageBrier = null;
        }

        CalibrationLabSummaryText.Text =
            $"{filtered.Count}/{calibrations.Count} shown · {gateReady} gate-ready · {risk} review";
        CalibrationMatrixSummaryText.Text = calibrations.Count == 0
            ? "No station/horizon calibration rows yet"
            : $"{calibrations.Select(calibration => calibration.StationIcao).Distinct().Count()} station(s)"
                + $" · {calibrations.Select(calibration => calibration.HorizonBucket).Distinct().Count()} horizon bucket(s)"
                + $" · {totalSamples} samples · filter {_calibrationFilter}";
        CalibrationQualitySummaryText.Text =
            $"mature {mature} · gate-ready {gateReady} · review {risk} · avg Brier {Number(averageBrier)}";
        CalibrationReadinessText.Text = calibrations.Count == 0
            ? "No calibration rows available yet. Paper mode needs resolved markets before this lab can grade model reliability."
            : $"Gate-ready rows {gateReady}/{calibrations.Count}"
                + Environment.NewLine
                + $"Mature rows {mature}/{calibrations.Count} · total samples {totalSamples}";

        CalibrationDto? worstBrier = calibrations
            .Where(calibration => calibration.Brier is not null)
            .OrderByDescending(calibration => calibration.Brier)
            .FirstOrDefault();
        CalibrationDto? biggestBias = calibrations
            .OrderByDescending(calibration => Math.Abs(calibration.BiasC))
            .FirstOrDefault();
        CalibrationOutlierText.Text = calibrations.Count == 0
            ? "Expected here: worst Brier, largest absolute bias, and sparse station/horizon buckets."
            : string.Join(
                Environment.NewLine,
                worstBrier is null
                    ? "Worst Brier: not available"
                    : $"Worst Brier · {worstBrier.StationIcao} · {worstBrier.HorizonBucket} · {Number(worstBrier.Brier)}",
                biggestBias is null
                    ? "Largest bias: not available"
                    : $"Largest bias · {biggestBias.StationIcao} · {biggestBias.HorizonBucket} · {biggestBias.BiasC:+0.00;-0.00;0.00} °C");

        DrawCalibrationMatrix();
        DrawCalibrationQualityMix();
    }

    private void CalibrationMatrixCanvas_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        if (CalibrationLabPanel.Visibility == Visibility.Visible)
        {
            DrawCalibrationMatrix();
        }
    }

    private void CalibrationQualityCanvas_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        if (CalibrationLabPanel.Visibility == Visibility.Visible)
        {
            DrawCalibrationQualityMix();
        }
    }

    private void DrawCalibrationMatrix()
    {
        CalibrationMatrixCanvas.Children.Clear();
        double width = CalibrationMatrixCanvas.ActualWidth;
        double height = CalibrationMatrixCanvas.ActualHeight;
        if (width <= 1 || height <= 1)
        {
            return;
        }

        if (_calibrationLabItems.Count == 0)
        {
            AddCanvasText(
                CalibrationMatrixCanvas,
                "No calibration evidence yet",
                18,
                20,
                new SolidColorBrush(Color.FromArgb(0xDD, 0xFF, 0xFF, 0xFF)),
                18,
                new FontWeight { Weight = 600 });
            AddCanvasText(
                CalibrationMatrixCanvas,
                "Resolved markets will populate station × horizon reliability here. Until then live mode should remain locked by the gate.",
                18,
                56,
                new SolidColorBrush(Color.FromArgb(0xAA, 0xFF, 0xFF, 0xFF)),
                13);
            return;
        }

        var cells = _calibrationLabItems
            .GroupBy(calibration => new
            {
                calibration.StationIcao,
                calibration.HorizonBucket,
            })
            .Select(group => new
            {
                group.Key.StationIcao,
                group.Key.HorizonBucket,
                Samples = group.Sum(calibration => calibration.SampleCount),
                AvgBrier = group
                    .Where(calibration => calibration.Brier is not null)
                    .Select(calibration => calibration.Brier!.Value)
                    .DefaultIfEmpty(double.NaN)
                    .Average(),
                AvgAbsBias = group.Average(calibration => Math.Abs(calibration.BiasC)),
                Count = group.Count(),
            })
            .OrderBy(cell => cell.StationIcao)
            .ThenBy(cell => cell.HorizonBucket)
            .ToList();
        string[] stations = cells
            .Select(cell => cell.StationIcao)
            .Distinct()
            .OrderBy(station => station)
            .Take(6)
            .ToArray();
        string[] horizons = cells
            .Select(cell => cell.HorizonBucket)
            .Distinct()
            .OrderBy(horizon => horizon)
            .Take(7)
            .ToArray();
        if (stations.Length == 0 || horizons.Length == 0)
        {
            return;
        }

        double left = 76;
        double top = 28;
        double gap = 5;
        double cellWidth = Math.Max(34, (width - left - 6 - gap * (horizons.Length - 1)) / horizons.Length);
        double cellHeight = Math.Max(26, (height - top - 6 - gap * (stations.Length - 1)) / stations.Length);
        int maxSamples = Math.Max(1, cells.Max(cell => cell.Samples));

        for (int horizonIndex = 0; horizonIndex < horizons.Length; horizonIndex++)
        {
            AddCanvasText(
                CalibrationMatrixCanvas,
                horizons[horizonIndex],
                left + horizonIndex * (cellWidth + gap),
                4,
                new SolidColorBrush(Color.FromArgb(0xAA, 0xFF, 0xFF, 0xFF)),
                11);
        }

        for (int stationIndex = 0; stationIndex < stations.Length; stationIndex++)
        {
            string station = stations[stationIndex];
            double y = top + stationIndex * (cellHeight + gap);
            AddCanvasText(
                CalibrationMatrixCanvas,
                station,
                4,
                y + 6,
                new SolidColorBrush(Color.FromArgb(0xCC, 0xFF, 0xFF, 0xFF)),
                11);

            for (int horizonIndex = 0; horizonIndex < horizons.Length; horizonIndex++)
            {
                string horizon = horizons[horizonIndex];
                var cell = cells.FirstOrDefault(item =>
                    item.StationIcao == station && item.HorizonBucket == horizon);
                double x = left + horizonIndex * (cellWidth + gap);
                if (cell is null)
                {
                    var emptyRect = new Rectangle
                    {
                        Width = cellWidth,
                        Height = cellHeight,
                        RadiusX = 5,
                        RadiusY = 5,
                        Stroke = new SolidColorBrush(Color.FromArgb(0x22, 0xFF, 0xFF, 0xFF)),
                        StrokeThickness = 1,
                        Fill = new SolidColorBrush(Color.FromArgb(0x18, 0xFF, 0xFF, 0xFF)),
                    };
                    Canvas.SetLeft(emptyRect, x);
                    Canvas.SetTop(emptyRect, y);
                    CalibrationMatrixCanvas.Children.Add(emptyRect);
                    continue;
                }

                bool hasBrier = !double.IsNaN(cell.AvgBrier);
                bool gateReady = hasBrier && cell.Samples >= 100 && cell.AvgBrier <= 0.18 && cell.AvgAbsBias <= 1.0;
                bool risky = !hasBrier || cell.Samples < 30 || cell.AvgBrier > 0.18 || cell.AvgAbsBias > 1.0;
                byte alpha = (byte)Math.Clamp(80 + (double)cell.Samples / maxSamples * 150, 80, 230);
                Brush fill = gateReady
                    ? new SolidColorBrush(Color.FromArgb(alpha, 0x31, 0xC4, 0x8D))
                    : risky
                        ? new SolidColorBrush(Color.FromArgb(alpha, 0xD1, 0x49, 0x5B))
                        : new SolidColorBrush(Color.FromArgb(alpha, 0x6D, 0x8D, 0xC7));
                var rect = new Rectangle
                {
                    Width = cellWidth,
                    Height = cellHeight,
                    RadiusX = 5,
                    RadiusY = 5,
                    Fill = fill,
                };
                Canvas.SetLeft(rect, x);
                Canvas.SetTop(rect, y);
                CalibrationMatrixCanvas.Children.Add(rect);
                AddCanvasText(
                    CalibrationMatrixCanvas,
                    cell.Samples.ToString(CultureInfo.InvariantCulture),
                    x + 7,
                    y + 5,
                    new SolidColorBrush(Colors.White),
                    11,
                    new FontWeight { Weight = 600 });
            }
        }
    }

    private void DrawCalibrationQualityMix()
    {
        CalibrationQualityCanvas.Children.Clear();
        double width = CalibrationQualityCanvas.ActualWidth;
        double height = CalibrationQualityCanvas.ActualHeight;
        if (width <= 1 || height <= 1)
        {
            return;
        }

        int gateReady = _calibrationLabItems.Count(calibration => CalibrationStatus(calibration) == "Gate ready");
        int collecting = _calibrationLabItems.Count(calibration => CalibrationStatus(calibration) == "Collecting");
        int mature = _calibrationLabItems.Count(calibration =>
            calibration.SampleCount >= 100 && CalibrationStatus(calibration) != "Gate ready");
        int review = _calibrationLabItems.Count(calibration =>
            CalibrationStatus(calibration) is "Review" or "Sparse" or "No Brier");
        int total = gateReady + collecting + mature + review;
        if (total == 0)
        {
            AddCanvasText(
                CalibrationQualityCanvas,
                "No quality mix yet",
                12,
                16,
                new SolidColorBrush(Color.FromArgb(0xCC, 0xFF, 0xFF, 0xFF)),
                13,
                new FontWeight { Weight = 600 });
            return;
        }

        double x = 0;
        double y = 28;
        double barHeight = 28;
        (string Label, int Count, Brush Brush)[] segments =
        [
            ("Gate", gateReady, AccentBrush("positive")),
            ("Collecting", collecting, new SolidColorBrush(Color.FromArgb(0xFF, 0x6D, 0x8D, 0xC7))),
            ("Mature review", mature, new SolidColorBrush(Color.FromArgb(0xFF, 0xC7, 0x9A, 0x3D))),
            ("Risk", review, AccentBrush("negative")),
        ];
        foreach ((string label, int count, Brush brush) in segments.Where(segment => segment.Count > 0))
        {
            double segmentWidth = Math.Max(3, width * count / total);
            var rect = new Rectangle
            {
                Width = segmentWidth,
                Height = barHeight,
                RadiusX = 4,
                RadiusY = 4,
                Fill = brush,
            };
            Canvas.SetLeft(rect, x);
            Canvas.SetTop(rect, y);
            CalibrationQualityCanvas.Children.Add(rect);
            if (segmentWidth > 72)
            {
                AddCanvasText(
                    CalibrationQualityCanvas,
                    $"{label} {count}",
                    x + 8,
                    y + 5,
                    new SolidColorBrush(Colors.White),
                    11,
                    new FontWeight { Weight = 600 });
            }

            x += segmentWidth;
        }
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
        IReadOnlyList<CommandItem> commands,
        LiveSummaryDto liveSummary,
        IReadOnlyList<OrderDto> orders)
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
        RenderControlRoom(
            "Risk & Live control room",
            "Runtime, live readiness, command audit, and execution monitor",
            overview,
            commands,
            liveSummary,
            orders,
            []);
    }

    private void RenderControlRoom(
        string title,
        string subtitle,
        OverviewResponse overview,
        IReadOnlyList<CommandItem> commands,
        LiveSummaryDto liveSummary,
        IReadOnlyList<OrderDto> orders,
        IReadOnlyList<PositionDto> positions)
    {
        ControlRoomPanel.Visibility = Visibility.Visible;
        ControlRoomTitle.Text = title;
        ControlRoomSubtitle.Text = subtitle;

        bool killClear = !overview.Runtime.KillSwitch;
        bool healthOk = string.Equals(
            overview.HealthAudit?.Status,
            "healthy",
            StringComparison.OrdinalIgnoreCase);
        bool gatePassed = overview.Validation?.Passed is true;
        bool credentialConfigured = _credentialSource != "Not configured";
        bool liveMode = string.Equals(overview.Runtime.Mode, "live", StringComparison.OrdinalIgnoreCase);

        ControlRoomRuntimeText.Text = overview.Runtime.KillSwitch
            ? "STOPPED"
            : overview.Runtime.Mode.ToUpperInvariant();
        ControlRoomRuntimeText.Foreground = AccentBrush(overview.Runtime.KillSwitch ? "negative" : "positive");
        ControlRoomRuntimeDetailText.Text = overview.Runtime.KillSwitch
            ? $"Kill switch active{ReasonSuffix(overview.Runtime.HaltedReason)}"
            : $"Kill switch off · {overview.OpenPositions} open · {overview.PendingSignals} pending signal(s)";

        ControlRoomReadinessText.Text = string.Join(
            Environment.NewLine,
            ReadinessLine(killClear, "No active kill switch"),
            ReadinessLine(healthOk, $"Health audit {overview.HealthAudit?.Status ?? "missing"}"),
            ReadinessLine(gatePassed, overview.Validation is null
                ? "Paper gate missing"
                : $"Paper gate day {overview.Validation.CheckpointDay}"),
            ReadinessLine(
                credentialConfigured,
                credentialConfigured
                    ? "API credential configured"
                    : "API credential missing"),
            ReadinessLine(
                liveMode,
                liveMode ? "Runtime LIVE" : "Runtime PAPER; live locked"),
            "[…] Legal/tax/wallet: backend gated");

        ControlRoomAuditText.Text = commands.Count == 0
            ? "No operator commands recorded yet"
            : string.Join(
                Environment.NewLine,
                commands.Take(5).Select(command =>
                    $"{Pretty(command.Status)} · {Pretty(command.Command)} · {LocalTime(command.CreatedAt)}"
                    + (command.Detail is null ? string.Empty : $" · {command.Detail}")));

        List<OrderDto> liveOrders = orders
            .Where(order => string.Equals(order.Mode, "live", StringComparison.OrdinalIgnoreCase))
            .ToList();
        int openLivePositions = positions.Count(position =>
            string.Equals(position.Mode, "live", StringComparison.OrdinalIgnoreCase)
            && string.Equals(position.Status, "open", StringComparison.OrdinalIgnoreCase));
        int redeemPending = positions.Count(position =>
            string.Equals(position.Mode, "live", StringComparison.OrdinalIgnoreCase)
            && string.Equals(position.Status, "closed", StringComparison.OrdinalIgnoreCase)
            && position.PnlUsd is > 0);
        ControlRoomLiveText.Text = string.Join(
            Environment.NewLine,
            $"Positions {liveSummary.Positions} · open {liveSummary.Open} · closed {liveSummary.Closed}",
            $"P&L {Usd(liveSummary.TotalPnlUsd)} · ROI {Percent(liveSummary.Roi)} · CLV {Number(liveSummary.AvgClv)}",
            $"Orders {liveOrders.Count} · open positions {openLivePositions}",
            redeemPending == 0
                ? "Manual redeem queue clear"
                : $"{redeemPending} winning position(s) need manual redeem");
    }

    private static string ReadinessLine(bool passed, string label) =>
        $"{(passed ? "[OK]" : "[LOCK]")} {label}";

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

    private async void PaperModeButton_Click(object sender, RoutedEventArgs e)
    {
        string? reason = await ConfirmWithReasonAsync(
            "Switch to paper trading",
            "Signals will execute against the simulated book with virtual money. "
                + "Real Polymarket data keeps flowing; no real orders are placed.",
            "Switch to paper");
        if (reason is not null)
        {
            await SubmitCommandAsync(
                "set_mode", reason, new Dictionary<string, object> { ["mode"] = "paper" });
        }
    }

    private async void LiveModeButton_Click(object sender, RoutedEventArgs e)
    {
        string? reason = await ConfirmWithReasonAsync(
            "Request LIVE trading",
            "Live mode places REAL orders with REAL money. The backend enforces "
                + "every gate: operator legal/tax flags, a configured wallet, the "
                + "day-30/60 validation gate, and a two-step confirmation — this "
                + "command must be sent TWICE within 5 minutes. The audit log "
                + "below shows each gate's verdict.",
            "Request live mode");
        if (reason is not null)
        {
            await SubmitCommandAsync(
                "set_mode", reason, new Dictionary<string, object> { ["mode"] = "live" });
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
        OverviewResponse overview,
        IReadOnlyList<CommandItem> commands,
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
        RenderControlRoom(
            "Live execution monitor",
            "Live performance, executor state, pending orders, and manual redeem queue",
            overview,
            commands,
            summary,
            live,
            livePositions);

        _equityPoints = equity;
        _equityChartLabel = "Live realised equity";
        EquityChartCard.Visibility = Visibility.Visible;
        DrawEquity();
    }

    private IReadOnlyList<EquityPointDto> _equityPoints = [];

    private void CockpitChartCanvas_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        if (_selectedDestination == "Dashboard")
        {
            DrawCockpitChart();
        }
    }

    private void DrawCockpitChart()
    {
        CockpitChartCanvas.Children.Clear();
        double width = CockpitChartCanvas.ActualWidth;
        double height = CockpitChartCanvas.ActualHeight;
        if (width <= 1 || height <= 1)
        {
            return;
        }

        DrawChartGrid(CockpitChartCanvas, width, height);
        if (_cockpitMarketTitle is not null
            && (_cockpitMarketPrices.Count >= 2
                || _cockpitProbabilities.Count(point => point.ModelProbability is not null) >= 2
                || _cockpitProbabilities.Count(point => point.MarketPrice is not null) >= 2))
        {
            DrawMarketOverlay(
                CockpitChartCanvas,
                _cockpitMarketPrices,
                _cockpitProbabilities,
                width,
                height);
            MarketPricePointDto? latestPrice = _cockpitMarketPrices.LastOrDefault();
            ProbabilityPointDto? latestSignal = _cockpitProbabilities
                .LastOrDefault(point => point.Source == "signal");
            CockpitChartTitle.Text = $"Market drill-down · {_cockpitMarketTitle}";
            CockpitChartSubtitle.Text =
                $"mid {Percent(latestPrice?.Midpoint)} · model {Percent(latestSignal?.ModelProbability)}"
                + $" · edge {Percent(latestSignal?.Edge)}";
            CockpitLegendText.Text = "Cyan market midpoint · violet model probability";
            return;
        }

        if (_cockpitEquity.Count >= 2)
        {
            double[] values = _cockpitEquity.Select(p => p.CumulativePnlUsd).ToArray();
            DrawLineSeries(
                CockpitChartCanvas,
                values,
                width,
                height,
                values[^1] >= 0 ? "positive" : "negative");
            CockpitChartTitle.Text = "Paper realised equity";
            CockpitChartSubtitle.Text =
                $"{Usd(values[^1])} over {_cockpitEquity.Count} closed trade points";
            CockpitLegendText.Text = "Realised P&L curve";
            return;
        }

        List<PositionDto> marked = _cockpitPositions
            .Where(p => string.Equals(p.Status, "open", StringComparison.OrdinalIgnoreCase)
                && p.UnrealizedPnlUsd is not null)
            .OrderBy(p => p.UnrealizedPnlUsd)
            .Take(18)
            .ToList();
        if (marked.Count == 0)
        {
            AddCanvasText(
                CockpitChartCanvas,
                "No chart data yet",
                20,
                22,
                new SolidColorBrush(Colors.White),
                18,
                new FontWeight { Weight = 600 });
            AddCanvasText(
                CockpitChartCanvas,
                "The cockpit will draw equity once trades close, or open P&L bars once marks arrive.",
                20,
                54,
                new SolidColorBrush(Color.FromArgb(0xAA, 0xFF, 0xFF, 0xFF)),
                13);
            CockpitChartTitle.Text = "Paper equity and open P&L";
            CockpitChartSubtitle.Text = "Waiting for mark-to-market or realised trade history";
            CockpitLegendText.Text = "No series";
            return;
        }

        double[] pnl = marked.Select(p => p.UnrealizedPnlUsd ?? 0).ToArray();
        DrawBarSeries(CockpitChartCanvas, pnl, width, height);
        double total = pnl.Sum();
        CockpitChartTitle.Text = "Open mark-to-market distribution";
        CockpitChartSubtitle.Text =
            $"{Signed(total)} across {marked.Count} marked open position(s)";
        CockpitLegendText.Text = "Green positive · red negative";
    }

    private static void DrawChartGrid(Canvas canvas, double width, double height)
    {
        var gridBrush = new SolidColorBrush(Color.FromArgb(0x24, 0xFF, 0xFF, 0xFF));
        for (int i = 1; i <= 4; i++)
        {
            double y = i * height / 5;
            canvas.Children.Add(new Line
            {
                X1 = 0,
                X2 = width,
                Y1 = y,
                Y2 = y,
                Stroke = gridBrush,
                StrokeThickness = 1,
            });
        }
    }

    private static void DrawMarketOverlay(
        Canvas canvas,
        IReadOnlyList<MarketPricePointDto> prices,
        IReadOnlyList<ProbabilityPointDto> probabilities,
        double width,
        double height)
    {
        double[] midpointValues = prices.Select(point => point.Midpoint).ToArray();
        double[] signalMarketValues = probabilities
            .Where(point => point.MarketPrice is not null)
            .Select(point => point.MarketPrice!.Value)
            .ToArray();
        double[] modelValues = probabilities
            .Where(point => point.ModelProbability is not null)
            .Select(point => point.ModelProbability!.Value)
            .ToArray();

        if (midpointValues.Length >= 2)
        {
            DrawUnitLineSeries(
                canvas,
                midpointValues,
                width,
                height,
                new SolidColorBrush(Color.FromArgb(0xFF, 0x43, 0xD8, 0xFF)),
                3);
        }
        else if (signalMarketValues.Length >= 2)
        {
            DrawUnitLineSeries(
                canvas,
                signalMarketValues,
                width,
                height,
                new SolidColorBrush(Color.FromArgb(0xFF, 0x43, 0xD8, 0xFF)),
                3);
        }

        if (modelValues.Length >= 2)
        {
            DrawUnitLineSeries(
                canvas,
                modelValues,
                width,
                height,
                new SolidColorBrush(Color.FromArgb(0xFF, 0xB2, 0x7C, 0xFF)),
                3);
        }
    }

    private static void DrawUnitLineSeries(
        Canvas canvas,
        IReadOnlyList<double> values,
        double width,
        double height,
        Brush brush,
        double strokeThickness)
    {
        if (values.Count < 2)
        {
            return;
        }

        double X(int index) => index * (width / (values.Count - 1));
        double Y(double value) => height - Math.Clamp(value, 0, 1) * height;
        var line = new Polyline { Stroke = brush, StrokeThickness = strokeThickness };
        for (int i = 0; i < values.Count; i++)
        {
            line.Points.Add(new Point(X(i), Y(values[i])));
        }
        canvas.Children.Add(line);
    }

    private void DrilldownSeriesBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (IsLoaded)
        {
            DrawDrilldownChart();
        }
    }

    private void DrilldownChartCanvas_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        DrawDrilldownChart();
    }

    private void DrawDrilldownChart()
    {
        DrilldownChartCanvas.Children.Clear();
        double width = DrilldownChartCanvas.ActualWidth;
        double height = DrilldownChartCanvas.ActualHeight;
        if (width <= 1 || height <= 1)
        {
            return;
        }

        DrawChartGrid(DrilldownChartCanvas, width, height);
        int selectedIndex = DrilldownSeriesBox.SelectedIndex;
        if (selectedIndex == 0)
        {
            double[] values = _drilldownPrices.Select(point => point.Midpoint).ToArray();
            if (values.Length >= 2)
            {
                DrawUnitLineSeries(
                    DrilldownChartCanvas,
                    values,
                    width,
                    height,
                    new SolidColorBrush(Color.FromArgb(0xFF, 0x43, 0xD8, 0xFF)),
                    3);
                return;
            }
        }
        else if (selectedIndex == 1)
        {
            DrawMarketOverlay(
                DrilldownChartCanvas,
                _drilldownPrices,
                _drilldownProbabilities,
                width,
                height);
            if (_drilldownPrices.Count >= 2
                || _drilldownProbabilities.Count(point => point.ModelProbability is not null) >= 2)
            {
                return;
            }
        }
        else
        {
            double[] edgeValues = _drilldownProbabilities
                .Where(point => point.Edge is not null)
                .Select(point => point.Edge!.Value)
                .ToArray();
            if (edgeValues.Length >= 2)
            {
                DrawLineSeries(
                    DrilldownChartCanvas,
                    edgeValues,
                    width,
                    height,
                    edgeValues[^1] >= 0 ? "positive" : "negative");
                return;
            }
        }

        AddCanvasText(
            DrilldownChartCanvas,
            "No series data yet",
            16,
            18,
            new SolidColorBrush(Color.FromArgb(0xCC, 0xFF, 0xFF, 0xFF)),
            14,
            new FontWeight { Weight = 600 });
    }

    private static void DrawLineSeries(
        Canvas canvas,
        IReadOnlyList<double> values,
        double width,
        double height,
        string? accent)
    {
        double lo = Math.Min(0, values.Min());
        double hi = Math.Max(0, values.Max());
        double span = hi - lo <= 0 ? 1 : hi - lo;
        double X(int i) => i * (width / (values.Count - 1));
        double Y(double v) => height - ((v - lo) / span * height);

        var zero = new Line
        {
            X1 = 0,
            X2 = width,
            Y1 = Y(0),
            Y2 = Y(0),
            Stroke = new SolidColorBrush(Color.FromArgb(0x66, 0x99, 0x99, 0x99)),
            StrokeThickness = 1,
        };
        canvas.Children.Add(zero);

        Brush brush = AccentBrush(accent);
        var line = new Polyline { Stroke = brush, StrokeThickness = 3 };
        for (int i = 0; i < values.Count; i++)
        {
            line.Points.Add(new Point(X(i), Y(values[i])));
        }
        canvas.Children.Add(line);

        var dot = new Ellipse { Width = 10, Height = 10, Fill = brush };
        Canvas.SetLeft(dot, X(values.Count - 1) - 5);
        Canvas.SetTop(dot, Y(values[^1]) - 5);
        canvas.Children.Add(dot);
    }

    private static void DrawBarSeries(
        Canvas canvas,
        IReadOnlyList<double> values,
        double width,
        double height)
    {
        double absMax = Math.Max(1, values.Select(Math.Abs).Max());
        double baseline = height / 2;
        double gap = 4;
        double barWidth = Math.Max(8, (width - gap * (values.Count - 1)) / values.Count);
        var zero = new Line
        {
            X1 = 0,
            X2 = width,
            Y1 = baseline,
            Y2 = baseline,
            Stroke = new SolidColorBrush(Color.FromArgb(0x88, 0xAA, 0xAA, 0xAA)),
            StrokeThickness = 1,
        };
        canvas.Children.Add(zero);

        for (int i = 0; i < values.Count; i++)
        {
            double value = values[i];
            double barHeight = Math.Abs(value) / absMax * (height * 0.42);
            double x = i * (barWidth + gap);
            double y = value >= 0 ? baseline - barHeight : baseline;
            var bar = new Rectangle
            {
                Width = barWidth,
                Height = Math.Max(2, barHeight),
                RadiusX = 3,
                RadiusY = 3,
                Fill = AccentBrush(PnlAccent(value)),
            };
            Canvas.SetLeft(bar, x);
            Canvas.SetTop(bar, y);
            canvas.Children.Add(bar);
        }
    }

    private static void AddCanvasText(
        Canvas canvas,
        string text,
        double left,
        double top,
        Brush brush,
        double fontSize,
        FontWeight? weight = null)
    {
        TextBlock block = new()
        {
            Text = text,
            Foreground = brush,
            FontSize = fontSize,
            FontWeight = weight ?? new FontWeight { Weight = 400 },
            TextWrapping = TextWrapping.Wrap,
            MaxWidth = Math.Max(120, canvas.ActualWidth - left - 20),
        };
        Canvas.SetLeft(block, left);
        Canvas.SetTop(block, top);
        canvas.Children.Add(block);
    }

    private void PortfolioEquityCanvas_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        if (PortfolioAnalyticsPanel.Visibility == Visibility.Visible)
        {
            DrawPortfolioEquity();
        }
    }

    private void PortfolioExposureCanvas_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        if (PortfolioAnalyticsPanel.Visibility == Visibility.Visible)
        {
            DrawPortfolioExposureHeatmap();
        }
    }

    private void DrawPortfolioAnalytics()
    {
        DrawPortfolioEquity();
        DrawPortfolioExposureHeatmap();
    }

    private void DrawPortfolioEquity()
    {
        PortfolioEquityCanvas.Children.Clear();
        double width = PortfolioEquityCanvas.ActualWidth;
        double height = PortfolioEquityCanvas.ActualHeight;
        if (width <= 1 || height <= 1)
        {
            return;
        }

        DrawChartGrid(PortfolioEquityCanvas, width, height);
        List<double> realised = _portfolioEquity
            .Select(point => point.CumulativePnlUsd)
            .ToList();
        double unrealised = _portfolioPositions
            .Where(position => string.Equals(position.Status, "open", StringComparison.OrdinalIgnoreCase))
            .Sum(position => position.UnrealizedPnlUsd ?? 0);
        double lastRealised = realised.Count == 0 ? 0 : realised[^1];
        double markToMarket = lastRealised + unrealised;
        List<double> values = realised.Count >= 2 ? realised : [0, lastRealised];
        values.Add(markToMarket);

        DrawLineSeries(
            PortfolioEquityCanvas,
            values,
            width,
            height,
            PnlAccent(markToMarket));
        AddCanvasText(
            PortfolioEquityCanvas,
            $"mark {Signed(markToMarket)}",
            16,
            14,
            AccentBrush(PnlAccent(markToMarket)),
            14,
            new FontWeight { Weight = 600 });
        AddCanvasText(
            PortfolioEquityCanvas,
            $"realised {Signed(lastRealised)} · unrealised {Signed(unrealised)}",
            16,
            36,
            new SolidColorBrush(Color.FromArgb(0xCC, 0xFF, 0xFF, 0xFF)),
            12);
    }

    private void DrawPortfolioExposureHeatmap()
    {
        PortfolioExposureCanvas.Children.Clear();
        double width = PortfolioExposureCanvas.ActualWidth;
        double height = PortfolioExposureCanvas.ActualHeight;
        if (width <= 1 || height <= 1)
        {
            return;
        }

        List<PositionDto> open = _portfolioPositions
            .Where(position => string.Equals(position.Status, "open", StringComparison.OrdinalIgnoreCase))
            .ToList();
        if (open.Count == 0)
        {
            AddCanvasText(
                PortfolioExposureCanvas,
                "No open exposure",
                16,
                18,
                new SolidColorBrush(Color.FromArgb(0xCC, 0xFF, 0xFF, 0xFF)),
                14,
                new FontWeight { Weight = 600 });
            return;
        }

        var cells = open
            .GroupBy(position => new
            {
                Station = position.StationIcao ?? "Unmapped",
                position.MarketDate,
            })
            .Select(group => new
            {
                group.Key.Station,
                group.Key.MarketDate,
                Cost = group.Sum(position => position.SizeUsd),
                Pnl = group.Sum(position => position.UnrealizedPnlUsd ?? 0),
                Count = group.Count(),
            })
            .OrderBy(cell => cell.Station)
            .ThenBy(cell => cell.MarketDate)
            .ToList();
        string[] stations = cells.Select(cell => cell.Station).Distinct().Take(5).ToArray();
        DateOnly[] dates = cells.Select(cell => cell.MarketDate).Distinct().Order().Take(6).ToArray();
        if (stations.Length == 0 || dates.Length == 0)
        {
            return;
        }

        double left = 58;
        double top = 28;
        double cellGap = 5;
        double cellWidth = Math.Max(28, (width - left - 6 - cellGap * (dates.Length - 1)) / dates.Length);
        double cellHeight = Math.Max(28, (height - top - 6 - cellGap * (stations.Length - 1)) / stations.Length);
        double maxCost = Math.Max(1, cells.Max(cell => cell.Cost));

        for (int dateIndex = 0; dateIndex < dates.Length; dateIndex++)
        {
            AddCanvasText(
                PortfolioExposureCanvas,
                dates[dateIndex].ToString("MM-dd", CultureInfo.InvariantCulture),
                left + dateIndex * (cellWidth + cellGap),
                4,
                new SolidColorBrush(Color.FromArgb(0xAA, 0xFF, 0xFF, 0xFF)),
                11);
        }

        for (int stationIndex = 0; stationIndex < stations.Length; stationIndex++)
        {
            string station = stations[stationIndex];
            double y = top + stationIndex * (cellHeight + cellGap);
            AddCanvasText(
                PortfolioExposureCanvas,
                station,
                4,
                y + 6,
                new SolidColorBrush(Color.FromArgb(0xCC, 0xFF, 0xFF, 0xFF)),
                11);
            for (int dateIndex = 0; dateIndex < dates.Length; dateIndex++)
            {
                DateOnly date = dates[dateIndex];
                var cell = cells.FirstOrDefault(item =>
                    item.Station == station && item.MarketDate == date);
                double x = left + dateIndex * (cellWidth + cellGap);
                if (cell is null)
                {
                    var emptyRect = new Rectangle
                    {
                        Width = cellWidth,
                        Height = cellHeight,
                        RadiusX = 5,
                        RadiusY = 5,
                        Stroke = new SolidColorBrush(Color.FromArgb(0x22, 0xFF, 0xFF, 0xFF)),
                        StrokeThickness = 1,
                        Fill = new SolidColorBrush(Color.FromArgb(0x18, 0xFF, 0xFF, 0xFF)),
                    };
                    Canvas.SetLeft(emptyRect, x);
                    Canvas.SetTop(emptyRect, y);
                    PortfolioExposureCanvas.Children.Add(emptyRect);
                    continue;
                }

                byte alpha = (byte)Math.Clamp(70 + cell.Cost / maxCost * 160, 70, 230);
                Color baseColor = cell.Pnl switch
                {
                    > 0 => Color.FromArgb(alpha, 0x31, 0xC4, 0x8D),
                    < 0 => Color.FromArgb(alpha, 0xF8, 0x51, 0x49),
                    _ => Color.FromArgb(alpha, 0x4C, 0xB3, 0xFF),
                };
                var rect = new Rectangle
                {
                    Width = cellWidth,
                    Height = cellHeight,
                    RadiusX = 5,
                    RadiusY = 5,
                    Fill = new SolidColorBrush(baseColor),
                };
                Canvas.SetLeft(rect, x);
                Canvas.SetTop(rect, y);
                PortfolioExposureCanvas.Children.Add(rect);

                AddCanvasText(
                    PortfolioExposureCanvas,
                    $"{Usd(cell.Cost)}\n{Signed(cell.Pnl)}",
                    x + 5,
                    y + 4,
                    new SolidColorBrush(Colors.White),
                    10);
            }
        }
    }

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
        if (width <= 1 || height <= 1)
        {
            return;
        }

        if (_equityPoints.Count < 2)
        {
            EquityChartTitle.Text = $"{_equityChartLabel} — waiting for closed live trades";
            AddCanvasText(
                EquityCanvas,
                "No live equity curve yet",
                20,
                24,
                new SolidColorBrush(Color.FromArgb(0xDD, 0xFF, 0xFF, 0xFF)),
                18,
                new FontWeight { Weight = 600 });
            AddCanvasText(
                EquityCanvas,
                "The chart will draw cumulative realised P&L after live positions settle.",
                20,
                58,
                new SolidColorBrush(Color.FromArgb(0xAA, 0xFF, 0xFF, 0xFF)),
                13);
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
            $"{_equityChartLabel} — {Usd(values[^1])} over {values.Length} trades";
    }

    private string _equityChartLabel = "Realised equity";

    private static string Signed(double? value) =>
        value.HasValue
            ? value.Value.ToString("+$0.00;-$0.00;$0.00", CultureInfo.InvariantCulture)
            : "—";

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
        // Page-specific top panels opt in after SetCollection has reset shared surfaces.
        EquityChartCard.Visibility = Visibility.Collapsed;
        PortfolioAnalyticsPanel.Visibility = Visibility.Collapsed;
        ControlRoomPanel.Visibility = Visibility.Collapsed;
        SignalIntelligencePanel.Visibility = Visibility.Collapsed;
        MarketBoardPanel.Visibility = Visibility.Collapsed;
        CalibrationLabPanel.Visibility = Visibility.Collapsed;
        if (SupportsMarketDrilldown(_selectedDestination) && _selectedDrilldownItem?.MarketId is not null)
        {
            CollectionDetailColumn.Width = new GridLength(460);
            MarketDrilldownPanel.Visibility = Visibility.Visible;
        }
        else
        {
            HideMarketDrilldown();
        }
        List<MonitoringListItem> materialized = items.ToList();
        CollectionList.ItemsSource = materialized;
        CollectionEmptyText.Text = "No records yet";
        CollectionEmptyState.Visibility = materialized.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
    }

    private static bool SupportsMarketDrilldown(string destination) =>
        destination is "Markets" or "Signals" or "Positions";

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
        SystemInfoBar.Visibility = Visibility.Visible;
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
        SystemInfoBar.Visibility = Visibility.Visible;
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
        string previousDestination = _selectedDestination;
        _selectedDestination = args.IsSettingsSelected
            ? "Settings"
            : (args.SelectedItemContainer?.Tag as string ?? "Dashboard");
        if (previousDestination != _selectedDestination)
        {
            HideMarketDrilldown();
        }

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
        PaperModeButton.Visibility =
            _selectedDestination == "Operations" ? Visibility.Visible : Visibility.Collapsed;
        LiveModeButton.Visibility =
            _selectedDestination == "Operations" ? Visibility.Visible : Visibility.Collapsed;
        PositionsFilterContainer.Visibility =
            _selectedDestination == "Positions" ? Visibility.Visible : Visibility.Collapsed;
        SignalsFilterContainer.Visibility =
            _selectedDestination == "Signals" ? Visibility.Visible : Visibility.Collapsed;
        MarketsFilterContainer.Visibility =
            _selectedDestination == "Markets" ? Visibility.Visible : Visibility.Collapsed;
        CalibrationFilterContainer.Visibility =
            _selectedDestination == "Calibration" ? Visibility.Visible : Visibility.Collapsed;
        ApiKeyButton.Visibility =
            _selectedDestination == "Settings" ? Visibility.Visible : Visibility.Collapsed;

        if (IsLoaded)
        {
            await RefreshAsync();
        }
    }

    private void SetPageHeading(string destination)
    {
        PageTitle.Text = destination == "Dashboard" ? "STRATUS Cockpit" : destination;
        PageSubtitle.Text = destination switch
        {
            "Dashboard" => "Trading cockpit: equity, scanner, risk, and selected market context",
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

    private static Brush AccentBrush(string? accent) =>
        accent switch
        {
            "positive" or "Passed" or "High" => new SolidColorBrush(Color.FromArgb(0xFF, 0x31, 0xC4, 0x8D)),
            "negative" or "Blocked" or "Low" => new SolidColorBrush(Color.FromArgb(0xFF, 0xF8, 0x51, 0x49)),
            "warning" or "Pending" => new SolidColorBrush(Color.FromArgb(0xFF, 0xFF, 0xB0, 0x2E)),
            "A" => new SolidColorBrush(Color.FromArgb(0xFF, 0x31, 0xC4, 0x8D)),
            "B" => new SolidColorBrush(Color.FromArgb(0xFF, 0x4C, 0xB3, 0xFF)),
            "C" => new SolidColorBrush(Color.FromArgb(0xFF, 0xB2, 0x7C, 0xFF)),
            _ => new SolidColorBrush(Color.FromArgb(0xFF, 0x4C, 0xB3, 0xFF)),
        };

    private static string HumanizeReasons(string? reasons) =>
        string.IsNullOrWhiteSpace(reasons)
            ? string.Empty
            : string.Join(
                " · ",
                reasons.Split(';', StringSplitOptions.RemoveEmptyEntries).Select(Pretty));
}
