using System.Globalization;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
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
    private HealthSnapshot? _lastHealth;
    private string _credentialSource;
    private IReadOnlyList<PositionDto> _cockpitPositions = [];
    private IReadOnlyList<EquityPointDto> _cockpitEquity = [];
    private string? _cockpitMarketTitle;
    private IReadOnlyList<MarketPricePointDto> _cockpitMarketPrices = [];
    private IReadOnlyList<ProbabilityPointDto> _cockpitProbabilities = [];
    private Point? _cockpitHoverPoint;
    private string _cockpitBaseChartSubtitle = "Realised curve when available; otherwise open mark-to-market distribution";
    private IReadOnlyList<ProspectiveValidationPointDto> _validationLedgerPoints = [];
    private Point? _validationLedgerHoverPoint;
    private string _validationLedgerBaseChartSubtitle =
        "Waiting for the first post-boundary fill and resolution";
    private IReadOnlyList<MarketPricePointDto> _drilldownPrices = [];
    private IReadOnlyList<ProbabilityPointDto> _drilldownProbabilities = [];
    private OrderBookResponse? _drilldownOrderBook;
    private MonitoringListItem? _selectedDrilldownItem;
    private Point? _drilldownHoverPoint;
    private string _drilldownBaseMetaText = "Chart and orderbook will load after selection";
    private IReadOnlyList<PositionDto> _portfolioPositions = [];
    private IReadOnlyList<EquityPointDto> _portfolioEquity = [];
    private IReadOnlyList<SignalDto> _signalPanelSignals = [];
    private IReadOnlyList<MarketDto> _marketBoardMarkets = [];
    private IReadOnlyList<CalibrationDto> _calibrationLabItems = [];
    private IReadOnlyList<JobDto> _jobsBoardItems = [];
    private IReadOnlyList<AlertItemDto> _visibleAlertItems = [];
    private string _alertsQueryState = "unacknowledged";
    private bool _isAcknowledgingAlert;
    private IReadOnlyList<MonitoringListItem> _collectionAllItems = [];
    private IReadOnlyList<MonitoringListItem> _cockpitActivityItems = [];
    private string _collectionSearchQuery = string.Empty;
    private string _collectionDefaultEmptyText = "No records yet";
    private string _cockpitActivitySearchQuery = string.Empty;
    private bool _isCommandPaletteOpen;

    private sealed record CommandPaletteAction(
        string Title,
        string Subtitle,
        string Detail,
        string SearchText,
        string Status,
        string? Accent,
        Func<Task> Execute);

    private sealed record DrilldownHoverReadout(
        double X,
        double Y,
        string Text,
        string Meta,
        Brush Brush);

    private sealed record DashboardFetch<T>(T? Value, string? Error)
        where T : class;

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
        RootNavigation.SizeChanged += (_, e) => ResizeNavigationContent(e.NewSize.Width);
        RootNavigation.DisplayModeChanged += (_, _) => ResizeNavigationContent(RootNavigation.ActualWidth);
        RootNavigation.PaneOpened += (_, _) => ResizeNavigationContent(RootNavigation.ActualWidth);
        RootNavigation.PaneClosed += (_, _) => ResizeNavigationContent(RootNavigation.ActualWidth);
        _refreshTimer.Tick += RefreshTimer_Tick;
        Loaded += MainPage_Loaded;
        Unloaded += MainPage_Unloaded;
    }

    private async void MainPage_Loaded(object sender, RoutedEventArgs e)
    {
        ResizeNavigationContent(RootNavigation.ActualWidth);
        _refreshTimer.Start();
        await RefreshAsync();
    }

    private void ResizeNavigationContent(double shellWidth)
    {
        if (double.IsNaN(shellWidth) || shellWidth <= 0)
        {
            return;
        }

        double reservedPaneWidth = RootNavigation.DisplayMode switch
        {
            NavigationViewDisplayMode.Expanded => RootNavigation.OpenPaneLength,
            NavigationViewDisplayMode.Compact when RootNavigation.IsPaneOpen
                => RootNavigation.OpenPaneLength,
            NavigationViewDisplayMode.Compact => RootNavigation.CompactPaneLength,
            _ => 0,
        };
        double contentWidth = Math.Max(0, shellWidth - reservedPaneWidth);
        ContentRoot.Width = contentWidth;
        ResizeCollectionView(contentWidth);
        ResizeDashboardContent(contentWidth);
    }

    private void ContentRoot_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        ResizeCollectionView(e.NewSize.Width);
        ResizeDashboardContent(e.NewSize.Width);
    }

    private void ResizeCollectionView(double viewportWidth)
    {
        if (double.IsNaN(viewportWidth) || viewportWidth <= 0)
        {
            return;
        }

        double contentWidth = Math.Min(1500, viewportWidth);
        CollectionView.Width = contentWidth;
        AlertsView.Width = contentWidth;
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

    private async void CommandPaletteButton_Click(object sender, RoutedEventArgs e)
    {
        await ShowCommandPaletteAsync();
    }

    private async void CommandPaletteKeyboardAccelerator_Invoked(
        KeyboardAccelerator sender,
        KeyboardAcceleratorInvokedEventArgs args)
    {
        args.Handled = true;
        await ShowCommandPaletteAsync();
    }

    private async Task ShowCommandPaletteAsync()
    {
        if (_isCommandPaletteOpen)
        {
            return;
        }

        _isCommandPaletteOpen = true;
        try
        {
            List<CommandPaletteAction> actions = BuildCommandPaletteActions();
            TextBox searchBox = new()
            {
                PlaceholderText = "Type a page, action, job, market, signal, or risk command",
                MinWidth = 560,
            };
            ListView actionList = new()
            {
                Height = 430,
                IsItemClickEnabled = true,
                ItemTemplate = (DataTemplate)Resources["CommandPaletteActionTemplate"],
                SelectionMode = ListViewSelectionMode.Single,
            };
            actionList.ItemContainerStyle = new Style(typeof(ListViewItem))
            {
                Setters =
                {
                    new Setter(ListViewItem.HorizontalContentAlignmentProperty, HorizontalAlignment.Stretch),
                    new Setter(ListViewItem.PaddingProperty, new Thickness(0)),
                    new Setter(ListViewItem.MarginProperty, new Thickness(0, 0, 0, 6)),
                    new Setter(ListViewItem.MinHeightProperty, 0d),
                },
            };

            void ApplyPaletteFilter()
            {
                string query = searchBox.Text.Trim();
                List<CommandPaletteAction> filtered = string.IsNullOrWhiteSpace(query)
                    ? actions
                    : actions.Where(action => MatchesPaletteQuery(action, query)).ToList();
                actionList.ItemsSource = filtered;
                actionList.SelectedIndex = filtered.Count > 0 ? 0 : -1;
            }

            searchBox.TextChanged += (_, _) => ApplyPaletteFilter();

            StackPanel panel = new() { Spacing = 12 };
            panel.Children.Add(new TextBlock
            {
                Text = "Fast navigation and operator actions. Risk-changing commands still open their normal confirmation dialogs.",
                TextWrapping = TextWrapping.Wrap,
                Foreground = (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"],
            });
            panel.Children.Add(searchBox);
            panel.Children.Add(actionList);

            ContentDialog dialog = new()
            {
                Title = "Command palette",
                Content = panel,
                PrimaryButtonText = "Run",
                CloseButtonText = "Cancel",
                DefaultButton = ContentDialogButton.Primary,
                XamlRoot = XamlRoot,
            };
            dialog.Opened += (_, _) => searchBox.Focus(FocusState.Programmatic);
            actionList.ItemClick += (_, args) =>
            {
                actionList.SelectedItem = args.ClickedItem;
                dialog.Hide();
            };

            ApplyPaletteFilter();
            ContentDialogResult result = await dialog.ShowAsync();
            if (result != ContentDialogResult.Primary && actionList.SelectedItem is null)
            {
                return;
            }

            if (actionList.SelectedItem is CommandPaletteAction selected)
            {
                await selected.Execute();
            }
        }
        finally
        {
            _isCommandPaletteOpen = false;
        }
    }

    private List<CommandPaletteAction> BuildCommandPaletteActions()
    {
        List<CommandPaletteAction> actions =
        [
            NavigationAction("Cockpit", "Dashboard", "Trading overview, scanner, equity, and activity feed", "1"),
            NavigationAction("Markets", "Markets", "Weather markets, station verification, resolving soon, blocked", "2"),
            NavigationAction("Signals", "Signals", "Recent model decisions, edge, confidence, skip reasons", "3"),
            NavigationAction("Positions", "Positions", "Paper positions, mark-to-market, P&L, CLV", "4"),
            NavigationAction("Calibration", "Calibration", "Station evidence, bias, sigma, Brier", "5"),
            NavigationAction("Jobs", "Jobs", "Scheduler outcomes, failures, slow jobs, runtime tape", "6"),
            NavigationAction("Alerts", "Alerts", "Durable local inbox and optional Telegram delivery status", "7"),
            NavigationAction("Live", "Live", "Live positions, live orders, redemption queue", "8"),
            NavigationAction("Operations", "Operations", "Runtime control, command audit, validation gate", "9"),
            new(
                "Open settings",
                "Configure local API credentials",
                $"Current key source: {_credentialSource}",
                "settings api key credential local config",
                "Settings",
                null,
                () =>
                {
                    NavigateToDestination("Settings");
                    return Task.CompletedTask;
                }),
            new(
                "Refresh current page",
                "Fetch latest monitoring state now",
                AutoRefreshButton.IsChecked == true ? "Auto-refresh is enabled" : "Auto-refresh is paused",
                "refresh reload update sync",
                "Refresh",
                null,
                RefreshAsync),
            new(
                "Focus list search",
                "Jump to the current page search box",
                _selectedDestination == "Dashboard"
                    ? "Search is available on data pages; choose a page first"
                    : $"Search {_selectedDestination}",
                "search filter find current list",
                "Search",
                null,
                FocusCollectionSearchAsync),
            new(
                "Stop trading",
                "Enable kill switch and expire pending signals",
                "Opens the existing confirmation dialog with required reason",
                "stop halt kill switch risk emergency",
                "Risk",
                "negative",
                () =>
                {
                    StopButton_Click(this, new RoutedEventArgs());
                    return Task.CompletedTask;
                }),
            new(
                "Resume trading",
                "Request normal signal execution after a stop",
                "Opens the existing confirmation dialog with required reason",
                "resume unhalt restart trading",
                "Risk",
                "warning",
                () =>
                {
                    ResumeButton_Click(this, new RoutedEventArgs());
                    return Task.CompletedTask;
                }),
            new(
                "Switch to paper mode",
                "Queue runtime mode change to paper trading",
                "Uses the audited backend command queue",
                "paper mode simulated trading",
                "Mode",
                null,
                () =>
                {
                    PaperModeButton_Click(this, new RoutedEventArgs());
                    return Task.CompletedTask;
                }),
            new(
                "Request live mode",
                "Queue LIVE request; backend gates remain authoritative",
                "Requires the same two-step live confirmation and legal/wallet gates",
                "live mode real money gate kyc wallet",
                "Live",
                "negative",
                () =>
                {
                    LiveModeButton_Click(this, new RoutedEventArgs());
                    return Task.CompletedTask;
                }),
            new(
                "Verify market station",
                "Approve a blocked market's parsed station by internal id",
                "Opens the existing verify dialog; does not bypass station checks",
                "verify market station blocked rules",
                "Verify",
                "warning",
                () =>
                {
                    VerifyMarketButton_Click(this, new RoutedEventArgs());
                    return Task.CompletedTask;
                }),
            new(
                "Run maintenance job",
                "Queue discover-markets, backfill-fees, or prune-signals",
                "Uses the audited backend command queue",
                "run job maintenance discover markets backfill fees prune signals",
                "Job",
                null,
                () =>
                {
                    RunJobButton_Click(this, new RoutedEventArgs());
                    return Task.CompletedTask;
                }),
            new(
                "Edit API key",
                "Save or remove the local operator API credential",
                $"Current source: {_credentialSource}",
                "api key credential auth token settings",
                "Auth",
                null,
                () =>
                {
                    ApiKeyButton_Click(this, new RoutedEventArgs());
                    return Task.CompletedTask;
                }),
        ];

        return actions;
    }

    private CommandPaletteAction NavigationAction(
        string label,
        string destination,
        string detail,
        string shortcut) =>
        new(
            $"Go to {label}",
            destination == _selectedDestination ? "Current page" : $"Navigate to {label}",
            detail,
            $"{label} {destination} page nav navigation {shortcut}",
            $"Ctrl+K",
            destination == _selectedDestination ? "positive" : null,
            () =>
            {
                NavigateToDestination(destination);
                return Task.CompletedTask;
            });

    private void NavigateToDestination(string destination)
    {
        if (destination == "Settings")
        {
            RootNavigation.SelectedItem = RootNavigation.SettingsItem;
            return;
        }

        foreach (object item in RootNavigation.MenuItems)
        {
            if (item is NavigationViewItem navigationItem
                && string.Equals(navigationItem.Tag as string, destination, StringComparison.Ordinal))
            {
                RootNavigation.SelectedItem = navigationItem;
                return;
            }
        }
    }

    private Task FocusCollectionSearchAsync()
    {
        if (_selectedDestination == "Alerts")
        {
            AlertsFilterBox.Focus(FocusState.Programmatic);
            return Task.CompletedTask;
        }

        if (_selectedDestination == "Dashboard")
        {
            ShowCommandWarning("List search is available after opening Markets, Signals, Positions, Calibration, Jobs, Live, Operations, or Settings.");
            return Task.CompletedTask;
        }

        CollectionSearchBox.Focus(FocusState.Programmatic);
        return Task.CompletedTask;
    }

    private static bool MatchesPaletteQuery(CommandPaletteAction action, string query) =>
        ContainsSearch(action.Title, query)
        || ContainsSearch(action.Subtitle, query)
        || ContainsSearch(action.Detail, query)
        || ContainsSearch(action.Status, query)
        || ContainsSearch(action.SearchText, query);

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
            Task<DashboardFetch<AlertsResponseDto>>? alertsBadgeFetch =
                destination == "Alerts"
                    ? null
                    : CaptureDashboardFetchAsync(
                        _apiClient.GetAlertsAsync(
                            "unacknowledged",
                            limit: 1,
                            cancellationToken: _lifetime.Token),
                        "alerts badge");
            HealthSnapshot health = await _apiClient.GetHealthSnapshotAsync(_lifetime.Token);
            RenderHealth(health);

            if (alertsBadgeFetch is not null)
            {
                DashboardFetch<AlertsResponseDto> alertsBadge = await alertsBadgeFetch;
                UpdateAlertsNavigationBadge(alertsBadge.Value, alertsBadge.Error);
            }

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
                    Task<OverviewResponse> cockpitOverview =
                        _apiClient.GetOverviewAsync(_lifetime.Token);
                    Task<CollectionResponse<SignalDto>> cockpitSignals =
                        _apiClient.GetSignalsAsync(_lifetime.Token);
                    Task<CollectionResponse<PositionDto>> cockpitPositions =
                        _apiClient.GetPositionsAsync(_lifetime.Token);
                    Task<EquityResponse> cockpitEquity =
                        _apiClient.GetEquityAsync("paper", _lifetime.Token);
                    Task<CollectionResponse<CommandItem>> cockpitCommands =
                        _apiClient.GetCommandsAsync(_lifetime.Token);
                    Task<CollectionResponse<JobDto>> cockpitJobs =
                        _apiClient.GetJobsAsync(_lifetime.Token);
                    Task<DashboardFetch<EdgeReportDto>> cockpitEdge = CaptureDashboardFetchAsync(
                        _apiClient.GetEdgeReportAsync("paper", _lifetime.Token), "edge report");
                    Task<DashboardFetch<ProspectiveValidationLedgerDto>> cockpitValidationLedger =
                        CaptureDashboardFetchAsync(
                            _apiClient.GetProspectiveValidationLedgerAsync(
                                "paper", _lifetime.Token),
                            "prospective validation ledger");
                    Task<DashboardFetch<BrierReportDto>> cockpitBrier = CaptureDashboardFetchAsync(
                        _apiClient.GetBrierReportAsync("paper", _lifetime.Token), "Brier report");
                    Task<DashboardFetch<ClvReportDto>> cockpitClv = CaptureDashboardFetchAsync(
                        _apiClient.GetClvReportAsync("paper", _lifetime.Token), "CLV report");
                    Task<DashboardFetch<RootCauseActionsDto>> cockpitActions =
                        CaptureDashboardFetchAsync(
                            _apiClient.GetRootCauseActionsAsync("paper", _lifetime.Token),
                            "root-cause actions");
                    Task<DashboardFetch<AccuracyOverviewDto>> cockpitAccuracy =
                        CaptureDashboardFetchAsync(
                            _apiClient.GetAccuracyOverviewAsync(_lifetime.Token),
                            "accuracy overview");
                    Task<DashboardFetch<SelectionPolicyReportDto>> cockpitSelection =
                        CaptureDashboardFetchAsync(
                            _apiClient.GetSelectionPolicyReportAsync("paper", _lifetime.Token),
                            "selection policy");
                    Task<DashboardFetch<ResolutionReconciliationResponseDto>> cockpitReconciliation =
                        CaptureDashboardFetchAsync(
                            _apiClient.GetResolutionReconciliationAsync(_lifetime.Token),
                            "resolution reconciliation");
                    Task<DashboardFetch<SettlementCasesResponseDto>> cockpitSettlementCases =
                        CaptureDashboardFetchAsync(
                            _apiClient.GetSettlementCasesAsync(_lifetime.Token),
                            "settlement lifecycle");
                    Task<DashboardFetch<StorageCapacityDto>> cockpitStorage =
                        CaptureDashboardFetchAsync(
                            _apiClient.GetStorageCapacityAsync(_lifetime.Token),
                            "storage capacity");
                    Task<DashboardFetch<ForecastProvenanceDto>> cockpitForecastProvenance =
                        CaptureDashboardFetchAsync(
                            _apiClient.GetForecastProvenanceAsync(_lifetime.Token),
                            "forecast provenance");
                    Task<DashboardFetch<ExecutionCostReportDto>> cockpitExecutionCost =
                        CaptureDashboardFetchAsync(
                            _apiClient.GetExecutionCostReportAsync("paper", _lifetime.Token),
                            "execution quality");
                    Task<DashboardFetch<OfficialResolutionSourcesDto>> cockpitOfficialResolution =
                        CaptureDashboardFetchAsync(
                            _apiClient.GetOfficialResolutionSourcesAsync(_lifetime.Token),
                            "official resolution truth");
                    Task<DashboardFetch<EvidenceContinuityDto>> cockpitEvidenceContinuity =
                        CaptureDashboardFetchAsync(
                            _apiClient.GetEvidenceContinuityAsync(_lifetime.Token),
                            "prospective evidence clock");
                    await Task.WhenAll(
                        cockpitOverview,
                        cockpitSignals,
                        cockpitPositions,
                        cockpitEquity,
                        cockpitCommands,
                        cockpitJobs);
                    RenderCockpit(
                        cockpitOverview.Result,
                        cockpitSignals.Result.Items,
                        cockpitPositions.Result.Items,
                        cockpitEquity.Result.Points,
                        cockpitCommands.Result.Items,
                        cockpitJobs.Result.Items);

                    await Task.WhenAll(
                        cockpitEdge,
                        cockpitValidationLedger,
                        cockpitBrier,
                        cockpitClv,
                        cockpitActions,
                        cockpitAccuracy,
                        cockpitSelection,
                        cockpitReconciliation,
                        cockpitSettlementCases,
                        cockpitStorage,
                        cockpitForecastProvenance,
                        cockpitExecutionCost,
                        cockpitOfficialResolution,
                        cockpitEvidenceContinuity);
                    DashboardFetch<EdgeReportDto> edge = cockpitEdge.Result;
                    DashboardFetch<ProspectiveValidationLedgerDto> validationLedger =
                        cockpitValidationLedger.Result;
                    DashboardFetch<BrierReportDto> brier = cockpitBrier.Result;
                    DashboardFetch<ClvReportDto> clv = cockpitClv.Result;
                    DashboardFetch<RootCauseActionsDto> actions = cockpitActions.Result;
                    DashboardFetch<AccuracyOverviewDto> accuracy = cockpitAccuracy.Result;
                    DashboardFetch<SelectionPolicyReportDto> selection = cockpitSelection.Result;
                    DashboardFetch<ResolutionReconciliationResponseDto> reconciliation =
                        cockpitReconciliation.Result;
                    DashboardFetch<SettlementCasesResponseDto> settlement =
                        cockpitSettlementCases.Result;
                    DashboardFetch<StorageCapacityDto> storage = cockpitStorage.Result;
                    DashboardFetch<ForecastProvenanceDto> forecastProvenance =
                        cockpitForecastProvenance.Result;
                    DashboardFetch<ExecutionCostReportDto> executionCost =
                        cockpitExecutionCost.Result;
                    DashboardFetch<OfficialResolutionSourcesDto> officialResolution =
                        cockpitOfficialResolution.Result;
                    DashboardFetch<EvidenceContinuityDto> evidenceContinuity =
                        cockpitEvidenceContinuity.Result;

                    if (edge.Value is not null)
                    {
                        RenderEdgeValidation(edge.Value);
                    }
                    if (validationLedger.Value is not null)
                    {
                        RenderProspectiveValidationLedger(validationLedger.Value);
                    }
                    else
                    {
                        RenderProspectiveValidationLedgerUnavailable(validationLedger.Error);
                    }
                    if (evidenceContinuity.Value is not null)
                    {
                        RenderEvidenceContinuity(evidenceContinuity.Value);
                    }
                    else
                    {
                        RenderEvidenceContinuityUnavailable(evidenceContinuity.Error);
                    }
                    if (accuracy.Value is not null)
                    {
                        RenderAccuracyOverview(accuracy.Value);
                    }
                    if (selection.Value is not null)
                    {
                        RenderSelectionPolicy(selection.Value);
                    }
                    if (reconciliation.Value is not null
                        && settlement.Value is not null
                        && storage.Value is not null)
                    {
                        RenderEvidenceOperations(
                            reconciliation.Value,
                            settlement.Value,
                            storage.Value);
                    }
                    else
                    {
                        RenderEvidenceOperationsDegraded(
                            reconciliation.Error,
                            settlement.Error,
                            storage.Error);
                    }
                    if (forecastProvenance.Value is not null)
                    {
                        RenderForecastProvenance(forecastProvenance.Value);
                    }
                    else
                    {
                        RenderForecastProvenanceUnavailable(forecastProvenance.Error);
                    }
                    if (executionCost.Value is not null)
                    {
                        RenderExecutionQuality(executionCost.Value);
                    }
                    else
                    {
                        RenderExecutionQualityUnavailable(executionCost.Error);
                    }
                    if (officialResolution.Value is not null)
                    {
                        RenderOfficialResolutionSources(officialResolution.Value);
                    }
                    else
                    {
                        RenderOfficialResolutionSourcesUnavailable(officialResolution.Error);
                    }
                    if (brier.Value is not null
                        && clv.Value is not null
                        && actions.Value is not null)
                    {
                        RenderRootCauseDiagnostics(brier.Value, clv.Value, actions.Value);
                    }

                    RenderDashboardDiagnosticErrors(
                        edge.Error,
                        validationLedger.Error,
                        brier.Error,
                        clv.Error,
                        actions.Error,
                        accuracy.Error,
                        selection.Error,
                        reconciliation.Error,
                        settlement.Error,
                        storage.Error,
                        forecastProvenance.Error,
                        executionCost.Error,
                        officialResolution.Error,
                        evidenceContinuity.Error);
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
                    Task<CollectionResponse<CalibrationDto>> calibrationRows =
                        _apiClient.GetCalibrationsAsync(_lifetime.Token);
                    Task<DashboardFetch<StationLifecycleResponse>> stationPool =
                        CaptureDashboardFetchAsync(
                            _apiClient.GetStationsAsync(_lifetime.Token),
                            "station trust");
                    await Task.WhenAll(calibrationRows, stationPool);
                    RenderCalibrations(
                        calibrationRows.Result.Items,
                        stationPool.Result.Value,
                        stationPool.Result.Error);
                    break;
                case "Jobs":
                    RenderJobs((await _apiClient.GetJobsAsync(_lifetime.Token)).Items);
                    break;
                case "Alerts":
                    DashboardFetch<AlertsResponseDto> alerts = await CaptureDashboardFetchAsync(
                        _apiClient.GetAlertsAsync(
                            _alertsQueryState,
                            limit: 100,
                            cancellationToken: _lifetime.Token),
                        "alerts inbox");
                    if (alerts.Value is not null)
                    {
                        RenderAlerts(alerts.Value);
                        UpdateAlertsNavigationBadge(alerts.Value, error: null);
                    }
                    else
                    {
                        RenderAlertsUnavailable(alerts.Error);
                        UpdateAlertsNavigationBadge(response: null, error: alerts.Error);
                    }
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
                    OverviewResponse settingsOverview =
                        await _apiClient.GetOverviewAsync(_lifetime.Token);
                    RenderSettings(settingsOverview, _lastHealth);
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

    private async Task<DashboardFetch<T>> CaptureDashboardFetchAsync<T>(
        Task<T> request,
        string component)
        where T : class
    {
        try
        {
            return new DashboardFetch<T>(await request, null);
        }
        catch (OperationCanceledException) when (_lifetime.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception exception)
        {
            return new DashboardFetch<T>(null, $"{component}: {exception.Message}");
        }
    }

    private void RenderDashboardDiagnosticErrors(params string?[] errors)
    {
        string[] failures = errors
            .Where(error => !string.IsNullOrWhiteSpace(error))
            .Select(error => error!)
            .ToArray();
        if (failures.Length == 0)
        {
            return;
        }

        string detail = string.Join(" · ", failures);
        bool backendReady = string.Equals(
            _lastHealth?.ReadyStatus,
            "ready",
            StringComparison.OrdinalIgnoreCase);
        if (backendReady)
        {
            SystemInfoBar.Severity = InfoBarSeverity.Warning;
            SystemInfoBar.Title = "Dashboard partially degraded";
            SystemInfoBar.Message =
                $"Core trading data is current; last-known diagnostic values were retained. {detail}";
        }
        else
        {
            SystemInfoBar.Message = $"{SystemInfoBar.Message} · Diagnostics: {detail}";
        }
        SystemInfoBar.Visibility = Visibility.Visible;
    }

    private void RenderHealth(HealthSnapshot snapshot)
    {
        _lastHealth = snapshot;
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
        IReadOnlyList<EquityPointDto> paperEquity,
        IReadOnlyList<CommandItem> commands,
        IReadOnlyList<JobDto> jobs)
    {
        RenderOverview(overview);
        _cockpitPositions = positions;
        _cockpitEquity = paperEquity;
        _cockpitHoverPoint = null;

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
        double? liquidation = open.Any(p => p.LiquidationPnlUsd is not null)
            ? open.Sum(p => p.LiquidationPnlUsd ?? 0)
            : null;
        CockpitLiquidationText.Text = $"Executable liquidation {Signed(liquidation)}";
        CockpitLiquidationText.Foreground = AccentBrush(PnlAccent(liquidation));
        CockpitMarkMovesText.Text =
            $"Δ1m {Signed(SumAvailable(open, p => p.PnlChange1mUsd))} · "
            + $"Δ5m {Signed(SumAvailable(open, p => p.PnlChange5mUsd))} · "
            + $"Δ60m {Signed(SumAvailable(open, p => p.PnlChange60mUsd))}";
        int staleMarks = open.Count(p => p.MarkStale);
        DateTimeOffset? latestMark = open
            .Where(p => p.MarkAt is not null)
            .Select(p => p.MarkAt)
            .Max();
        CockpitMarkFreshnessText.Text = latestMark is null
            ? "No market marks"
            : $"Last mark {RelativeAge(latestMark.Value)} ago"
                + (staleMarks > 0 ? $" · {staleMarks} STALE" : " · all fresh");
        CockpitMarkFreshnessText.Foreground = AccentBrush(staleMarks > 0 ? "warning" : null);
        CockpitExposureText.Text = Usd(overview.OpenExposureUsd);
        CockpitExposureDetailText.Text =
            $"{overview.OpenPositions} open positions · {overview.PendingSignals} pending signals";
        CockpitRiskRuntime.Text = overview.Runtime.KillSwitch
            ? $"Kill switch active{ReasonSuffix(overview.Runtime.HaltedReason)}"
            : $"Mode {overview.Runtime.Mode.ToUpperInvariant()} · kill switch off";
        CockpitRiskHealth.Text = overview.HealthAudit is null
            ? "No health audit available"
            : $"{Pretty(overview.HealthAudit.Status)} · {overview.HealthAudit.Issues.Count} issue(s)";
        _cockpitActivityItems = BuildCockpitActivityFeed(overview, signals, positions, commands, jobs);
        ApplyCockpitActivityFilters();
        CockpitRiskActivity.Text =
            $"{overview.ActiveMarkets} active markets · {overview.BlockedMarkets} blocked · "
            + $"{commands.Count} commands · {jobs.Count(job => !IsJobSuccess(job))} job issue(s)";

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

    private static readonly IReadOnlyDictionary<string, string> EdgeCheckLabels =
        new Dictionary<string, string>
        {
            ["stations_calibrated"] = "Stations calibrated",
            ["independent_station_days_100"] = "Independent station-days",
            ["resolved_positions_200"] = "≥200 closed positions",
            ["pipeline_version_samples_100"] = "≥100 trades on current model",
            ["clv_samples_100"] = "≥100 CLV samples",
            ["avg_clv_positive"] = "Average CLV positive",
            ["clv_beat_rate_52"] = "CLV beat rate ≥52%",
            ["roi_ci_excludes_zero"] = "ROI CI excludes zero",
            ["skill_pnl_significant"] = "Skill P&L significant",
            ["brier_model_beats_market"] = "Model beats market (Brier)",
            ["forecast_input_provenance"] = "Forecast source evidence complete",
        };

    private void RenderEdgeValidation(EdgeReportDto edge)
    {
        int passed = edge.GateChecks.Count(check => check.Passed);
        int total = edge.GateChecks.Count;
        string evidenceScope = EvidenceScope(edge.BenchmarkProtocol, edge.SampleScope);
        bool prospectiveDual = IsProspectiveDual(edge.BenchmarkProtocol, edge.SampleScope);

        EdgeGateBadgeText.Text = edge.GatePassed
            ? "LIVE-READY"
            : total == 0 ? "No data" : $"BLOCKED · {passed}/{total}";
        EdgeGateBadge.Background = AccentBrush(edge.GatePassed ? "positive" : "negative");
        EdgeProtocolText.Text = prospectiveDual
            ? "Prospective dual-token · new evidence cohort · legacy rows excluded"
            : $"{evidenceScope} · protocol scope reported by backend";
        EdgeProtocolText.Foreground = AccentBrush(prospectiveDual ? "positive" : "warning");
        EdgeGateSubtitle.Text = prospectiveDual
            ? "Live mode stays blocked until the new immutable YES/NO cohort passes every check. Earlier trades remain portfolio history only."
            : "Live mode stays blocked until every check passes. This backend did not identify the expected prospective dual-token cohort.";

        // The continuous edge gate supersedes the day-30/60 checkpoint tile:
        // it answers "can we go live now" on every refresh.
        GateStatusText.Text = edge.GatePassed ? "Live-ready" : "Blocked";
        GateStatusText.Foreground = AccentBrush(edge.GatePassed ? "positive" : "negative");
        GateDetailText.Text = total == 0
            ? $"{evidenceScope} · no validation checks reported yet"
            : $"{passed}/{total} checks · {edge.ResolvedTrades} prospective resolved · "
                + $"{edge.ClvSamples} CLV · {edge.PipelineResolvedTrades} current-model";

        EdgeSkillText.Text = edge.BenchmarkPositions == 0 ? "Collecting" : Signed(edge.SkillPnlUsd);
        EdgeSkillText.Foreground = AccentBrush(PnlAccent(edge.SkillPnlUsd));
        EdgeLuckText.Text = edge.BenchmarkPositions == 0 ? "Collecting" : Signed(edge.LuckPnlUsd);
        EdgeLuckText.Foreground = AccentBrush(PnlAccent(edge.LuckPnlUsd));

        if (edge.PipelineBrierModel is { } model && edge.PipelineBrierMarket is { } market)
        {
            EdgeBrierText.Text = $"{model:0.000} vs {market:0.000}";
            EdgeBrierText.Foreground = AccentBrush(model < market ? "positive" : "negative");
        }
        else
        {
            EdgeBrierText.Text = edge.PipelineVersion is { Length: > 0 }
                ? $"collecting · n={edge.PipelineResolvedTrades}"
                : "version unavailable";
            EdgeBrierText.Foreground = AccentBrush(null);
        }

        EdgeCiText.Text = edge.RoiCiLow is { } low && edge.RoiCiHigh is { } high
            ? $"[{Percent(low)}, {Percent(high)}]"
            : "—";
        EdgeCiText.Foreground = AccentBrush(
            edge.StatisticallyPositive is true ? "positive"
            : edge.StatisticallyPositive is false ? "warning" : null);

        EdgeChecklistItems.ItemsSource = edge.GateChecks
            .Select(check => new MonitoringListItem(
                EdgeCheckLabels.TryGetValue(check.Name, out string? label) ? label : Pretty(check.Name),
                string.Empty,
                check.Detail,
                string.Empty,
                check.Passed ? "Passed" : "Blocked",
                check.Passed ? "positive" : "negative"))
            .ToList();
    }

    private void RenderProspectiveValidationLedger(ProspectiveValidationLedgerDto ledger)
    {
        _validationLedgerPoints = ledger.Points
            .OrderBy(point => point.Sequence)
            .ToList();
        _validationLedgerHoverPoint = null;

        string? cohort = ledger.SelectedPipelineCohort;
        string shortDefinitionHash = ledger.DefinitionHash.Length > 12
            ? $"{ledger.DefinitionHash[..12]}..."
            : ledger.DefinitionHash;
        ValidationLedgerBadgeText.Text = cohort is null
            ? "NO CURRENT COHORT"
            : ledger.SelectedIsCurrent ? "CURRENT COHORT" : "EXPLICIT COHORT";
        ValidationLedgerBadge.Background = AccentBrush(
            cohort is null || !ledger.SelectedIsCurrent ? "warning" : null);
        ValidationLedgerSubtitle.Text =
            "Immutable zero-backfill evidence collected after the registration boundary. "
            + "Diagnostic only; it does not unlock live mode.";
        ValidationLedgerCohortText.Text = cohort is null
            ? "No current pipeline cohort is available for enrollment"
            : $"{cohort} | {Pretty(ledger.CohortScope)} scope";
        ValidationLedgerCohortText.Foreground = AccentBrush(cohort is null ? "warning" : null);
        ValidationLedgerBoundaryText.Text =
            $"Boundary {LocalTime(ledger.RegistrationOpenedAt)} | {ledger.DefinitionVersion} "
            + $"| definition {shortDefinitionHash} | {Pretty(ledger.SourceIntegrity)} "
            + $"| generated {LocalTime(ledger.GeneratedAt)}";

        ValidationEnrollmentText.Text =
            $"{ledger.EligibleEnrollments}/{ledger.EnrolledOrders} eligible";
        ValidationEnrollmentDetailText.Text =
            $"pending outcomes {ledger.PendingOutcomes} | pending blocks {ledger.PendingBlocks} "
            + $"| excluded {ledger.ExcludedEnrollments}";

        ValidationClosedText.Text =
            $"{ledger.ClosedOrders} orders | {ledger.IndependentBlocks} blocks";
        ValidationClosedDetailText.Text = ledger.ClosedOrders == 0
            ? "Waiting for the first eligible post-boundary order to resolve"
            : $"{ledger.DecomposedOrders}/{ledger.ClosedOrders} orders have skill/luck decomposition";

        ValidationPnlText.Text = $"{Signed(ledger.TotalPnlUsd)} | {Percent(ledger.Roi)}";
        ValidationPnlText.Foreground = AccentBrush(PnlAccent(ledger.TotalPnlUsd));
        ValidationPnlDetailText.Text =
            $"cost {Usd(ledger.TotalCostUsd)} | skill {Signed(ledger.SkillPnlUsd)} "
            + $"| luck {Signed(ledger.LuckPnlUsd)}";

        ValidationDrawdownText.Text =
            $"{Usd(ledger.MaxDrawdownUsd)} | {Percent(ledger.MaxDrawdownFraction)}";
        ValidationDrawdownText.Foreground = AccentBrush(
            ledger.MaxDrawdownUsd > 0 ? "warning" : null);
        ValidationDrawdownDetailText.Text =
            $"peak-to-trough on an initial {Usd(ledger.InitialBankrollUsd)} paper bankroll";

        if (ledger.RobustPnlUsd is { } robustPnl)
        {
            ValidationRobustText.Text = $"{Signed(robustPnl)} | {Percent(ledger.RobustRoi)}";
            ValidationRobustText.Foreground = AccentBrush(PnlAccent(robustPnl));
            ValidationRobustDetailText.Text = ledger.LargestOrderId is { } largestOrderId
                ? $"without order #{largestOrderId} ({Signed(ledger.LargestOrderPnlUsd)})"
                : "largest-order exclusion reported without an order identifier";
        }
        else
        {
            ValidationRobustText.Text = "-";
            ValidationRobustText.Foreground = AccentBrush(null);
            ValidationRobustDetailText.Text = "Needs at least two closed prospective orders";
        }

        ValidationConcentrationText.Text = Percent(ledger.LargestOrderAbsPnlShare);
        ValidationConcentrationText.Foreground = AccentBrush(
            ledger.LargestOrderAbsPnlShare is >= 0.5 ? "warning" : null);
        ValidationConcentrationDetailText.Text = ledger.LargestOrderId is { } orderId
            ? $"order #{orderId} | P&L {Signed(ledger.LargestOrderPnlUsd)}"
            : "No resolved prospective order yet";

        _validationLedgerBaseChartSubtitle = ledger.IndependentBlocks == 0
            ? "Waiting for the first post-boundary fill and resolution"
            : $"{ledger.IndependentBlocks} independent block(s) | P&L "
                + $"{Signed(ledger.TotalPnlUsd)} | max drawdown {Usd(ledger.MaxDrawdownUsd)}";
        ValidationLedgerChartTitle.Text = "Immutable cohort trajectory";
        ValidationLedgerChartSubtitle.Text = _validationLedgerBaseChartSubtitle;
        ValidationLedgerLegendText.Text = "Cumulative P&L | hover block details";
        ValidationLedgerEmptyText.Text =
            "Waiting for the first post-boundary fill and resolution. "
            + "Pre-boundary history is intentionally excluded.";
        DrawValidationLedgerChart();
    }

    private void RenderProspectiveValidationLedgerUnavailable(string? error)
    {
        _validationLedgerPoints = [];
        _validationLedgerHoverPoint = null;
        _validationLedgerBaseChartSubtitle = "Prospective ledger endpoint unavailable";
        ValidationLedgerBadgeText.Text = "UNAVAILABLE";
        ValidationLedgerBadge.Background = AccentBrush("warning");
        ValidationLedgerSubtitle.Text =
            "This isolated diagnostic could not be refreshed. The live validation gate is unchanged.";
        ValidationLedgerCohortText.Text = "Immutable cohort unavailable";
        ValidationLedgerCohortText.Foreground = AccentBrush("warning");
        ValidationLedgerBoundaryText.Text = error ?? "No response from /api/v1/validation-ledger";
        ValidationEnrollmentText.Text = "-";
        ValidationEnrollmentDetailText.Text = "Enrollment counts unavailable";
        ValidationClosedText.Text = "-";
        ValidationClosedDetailText.Text = "Resolved evidence unavailable";
        ValidationPnlText.Text = "-";
        ValidationPnlText.Foreground = AccentBrush(null);
        ValidationPnlDetailText.Text = "Prospective P&L unavailable";
        ValidationDrawdownText.Text = "-";
        ValidationDrawdownText.Foreground = AccentBrush(null);
        ValidationDrawdownDetailText.Text = "Drawdown unavailable";
        ValidationRobustText.Text = "-";
        ValidationRobustText.Foreground = AccentBrush(null);
        ValidationRobustDetailText.Text = "Largest-order exclusion unavailable";
        ValidationConcentrationText.Text = "-";
        ValidationConcentrationText.Foreground = AccentBrush(null);
        ValidationConcentrationDetailText.Text = "Outcome concentration unavailable";
        ValidationLedgerChartTitle.Text = "Immutable cohort trajectory";
        ValidationLedgerChartSubtitle.Text = _validationLedgerBaseChartSubtitle;
        ValidationLedgerLegendText.Text = "Diagnostic unavailable";
        ValidationLedgerEmptyText.Text =
            "Prospective validation data is temporarily unavailable. No historical rows are substituted.";
        DrawValidationLedgerChart();
    }

    private void RenderEvidenceContinuity(EvidenceContinuityDto continuity)
    {
        IReadOnlyList<EvidenceContinuityLaneDto> lanes = continuity.Lanes ?? [];
        IReadOnlyList<EvidenceContinuityControlLaneDto> controlLanes =
            continuity.ContinuityLanes ?? [];
        IReadOnlyList<EvidenceContinuitySkipReasonDto> skipReasons =
            continuity.TopSkipReasons ?? [];
        string gateReason = continuity.ExecutionGateReason ?? string.Empty;
        bool activationRequired = continuity.ActiveEpoch is null
            || string.Equals(
                gateReason,
                "evidence_continuity_unactivated",
                StringComparison.OrdinalIgnoreCase);
        bool releaseIdentityBlocked = !activationRequired
            && string.Equals(
                gateReason,
                "evidence_release_identity_mismatch",
                StringComparison.OrdinalIgnoreCase);
        bool laneIdentityBlocked = !activationRequired
            && string.Equals(
                gateReason,
                "evidence_lane_identity_mismatch",
                StringComparison.OrdinalIgnoreCase);
        bool laneFrozen = !activationRequired
            && string.Equals(
                gateReason,
                "evidence_lane_frozen",
                StringComparison.OrdinalIgnoreCase);
        bool integrityBlocked = !activationRequired
            && string.Equals(
                gateReason,
                "evidence_continuity_integrity_error",
                StringComparison.OrdinalIgnoreCase);
        (string badge, string accent) = activationRequired
            ? ("ACTIVATION REQUIRED", "warning")
            : releaseIdentityBlocked
                ? ("RELEASE IDENTITY MISMATCH", "negative")
                : laneIdentityBlocked
                    ? ("LANE IDENTITY MISMATCH", "negative")
                    : integrityBlocked
                        ? ("INTEGRITY ERROR", "negative")
                        : laneFrozen
                            ? ("LANE FROZEN", "warning")
                            : continuity.ExecutionGateAllowed
                                ? ("ACTIVE + COLLECTING", "positive")
                                : ("EXECUTION BLOCKED", "warning");

        EvidenceClockBadgeText.Text = badge;
        EvidenceClockBadge.Background = AccentBrush(accent);
        EvidenceClockSubtitle.Text = activationRequired
            ? "No reviewed release is active. Existing operations remain visible, but every new intent is fail-closed."
            : releaseIdentityBlocked
                ? "The running build, schema, configuration, or global manifest differs from the reviewed release."
                : laneIdentityBlocked
                    ? "At least one material evidence definition differs from its activated lane identity."
                    : integrityBlocked
                        ? "The control-plane chain is incomplete or cannot be verified; new execution is fail-closed."
                        : laneFrozen
                            ? "A required lane lacks a complete identity. Collection may continue, but execution cannot."
                            : "The running release and all six material lanes exactly match the reviewed activation.";

        EvidenceClockActiveCohortText.Text = activationRequired
            ? "No active frozen cohort"
            : continuity.ActiveCohort!;
        EvidenceClockActiveCohortText.Foreground = AccentBrush(
            activationRequired ? "warning"
            : releaseIdentityBlocked || laneIdentityBlocked || integrityBlocked
                ? "negative"
                : laneFrozen ? "warning" : "positive");
        EvidenceClockCandidateCohortText.Text = string.IsNullOrWhiteSpace(
            continuity.CandidateCohort)
            ? "Candidate cohort unavailable"
            : continuity.CandidateCohort;
        EvidenceClockCandidateCohortText.Foreground = AccentBrush(
            releaseIdentityBlocked || laneIdentityBlocked ? "negative" : null);

        if (continuity.ActiveEpoch is { } epoch)
        {
            string fingerprint = ShortEvidenceIdentity(epoch.MaterialFingerprint);
            string image = ShortEvidenceIdentity(epoch.ImageDigest);
            string previous = epoch.PreviousEpochId is { } previousId
                ? $"previous #{previousId:N0}"
                : "first reviewed epoch";
            EvidenceClockEpochText.Text =
                $"Epoch #{epoch.Id:N0} opened {LocalTime(epoch.OpenedAt)} | "
                + $"pipeline {epoch.PipelineVersion} | material {fingerprint} | image {image} | "
                + $"{previous} | approval: {epoch.ApprovalReason}";
        }
        else
        {
            EvidenceClockEpochText.Text =
                $"No activation epoch | {continuity.EpochCount:N0} historical epoch(s) | "
                + $"{continuity.CohortChanges30d:N0} cohort change(s) in 30 days";
        }

        string bottleneck = EvidenceBottleneckTitle(
            continuity.ExecutionGateAllowed
                ? continuity.CurrentBottleneck
                : gateReason);
        EvidenceClockBottleneckTitleText.Text = bottleneck;
        EvidenceClockBottleneckTitleText.Foreground = AccentBrush(
            releaseIdentityBlocked || laneIdentityBlocked || integrityBlocked
                ? "negative"
            : laneFrozen ? "warning"
            : activationRequired ? "warning" : "info");
        string bottleneckDetail = string.IsNullOrWhiteSpace(
            continuity.CurrentBottleneckDetail)
            ? "The backend did not report additional bottleneck detail."
            : continuity.CurrentBottleneckDetail;
        if (!continuity.ExecutionGateAllowed)
        {
            bottleneckDetail = $"Execution gate: {gateReason}. " + bottleneckDetail;
        }
        EvidenceClockBottleneckDetailText.Text = bottleneckDetail
            + $" Due state: {Pretty(continuity.Liveness)} | "
            + $"waiting {continuity.CollectionWindow.DueCounts.WaitingForDue:N0}, "
            + $"pending {continuity.CollectionWindow.DueCounts.DuePending:N0}, "
            + $"overdue {continuity.CollectionWindow.DueCounts.Overdue:N0}.";

        EvidenceClockDaysText.Text = continuity.CompleteDays.ToString("N0");
        EvidenceClockDaysText.Foreground = AccentBrush(
            continuity.CompleteDays >= 7 ? "positive" : "warning");
        EvidenceClockDaysDetailText.Text = continuity.CompleteDays >= 7
            ? $"Velocity baseline eligible | {Pretty(continuity.Liveness)}"
            : $"{Math.Max(0, 7 - continuity.CompleteDays):N0} more complete day(s) before ETA"
                + $" | {Pretty(continuity.Liveness)}";

        if (continuity.OutcomeVelocityPerDay is { } velocity)
        {
            EvidenceClockVelocityText.Text = $"{velocity:0.##} outcomes / day";
            EvidenceClockVelocityText.Foreground = AccentBrush(
                velocity > 0 ? "positive" : "warning");
            string eta100 = continuity.Eta100At is { } eta100At
                ? LocalTime(eta100At)
                : "not projected";
            string eta200 = continuity.Eta200At is { } eta200At
                ? LocalTime(eta200At)
                : "not projected";
            EvidenceClockVelocityDetailText.Text =
                $"ETA 100: {eta100} | ETA 200: {eta200}";
        }
        else
        {
            EvidenceClockVelocityText.Text = "Collecting baseline";
            EvidenceClockVelocityText.Foreground = AccentBrush(null);
            EvidenceClockVelocityDetailText.Text =
                $"ETA unavailable | {Pretty(continuity.EtaReason ?? "reason unavailable")}";
        }

        EvidenceClockStationText.Text =
            $"{continuity.PaperStations:N0} paper | {continuity.ShadowStations:N0} shadow";
        EvidenceClockStationDetailText.Text =
            $"{continuity.SignalRowsSinceEpoch:N0} signal rows since the frozen boundary";
        EvidenceClockExposureText.Text = Usd(continuity.OpenExposureUsd);
        EvidenceClockExposureText.Foreground = AccentBrush(
            continuity.BankrollUsd > 0
                && continuity.OpenExposureUsd > continuity.BankrollUsd
                ? "warning"
                : null);
        EvidenceClockExposureDetailText.Text =
            $"{continuity.OpenPositions:N0} positions | bankroll {Usd(continuity.BankrollUsd)}";
        EvidenceClockSignalText.Text =
            $"{continuity.SignalRowsSinceEpoch:N0} signals | {continuity.PendingSignals:N0} pending";

        EvidenceContinuityLaneDto? activeLane = continuity.ActiveEpoch is { } activeEpoch
            ? lanes.FirstOrDefault(lane => lane.EpochId == activeEpoch.Id)
            : null;
        activeLane ??= !string.IsNullOrWhiteSpace(continuity.ActiveCohort)
            ? lanes.FirstOrDefault(lane => string.Equals(
                lane.Cohort,
                continuity.ActiveCohort,
                StringComparison.Ordinal))
            : null;
        EvidenceClockFunnelItems.ItemsSource = BuildEvidenceFunnel(activeLane);

        EvidenceClockSkipReasonsText.Text = skipReasons.Count == 0
            ? "Top filters: no skip reasons recorded since this epoch."
            : "Top filters: " + string.Join(
                " | ",
                skipReasons
                    .OrderByDescending(item => item.Count)
                    .Take(5)
                    .Select(item =>
                        $"{Pretty(string.IsNullOrWhiteSpace(item.Reason) ? "unknown" : item.Reason)} "
                        + $"{item.Count:N0}"));

        List<MonitoringListItem> laneItems = controlLanes
            .OrderBy(lane => lane.LaneName, StringComparer.Ordinal)
            .Select(BuildContinuityControlLaneItem)
            .ToList();
        if (laneItems.Count == 0)
        {
            laneItems.Add(new MonitoringListItem(
                "No continuity decisions yet",
                "Waiting for a reviewed activation",
                "All six material lane identities must be activated atomically.",
                "The endpoint returned an empty continuity_lanes collection.",
                "EMPTY",
                "warning"));
        }
        EvidenceClockLaneItems.ItemsSource = laneItems;
        int continued = controlLanes.Count(lane => string.Equals(
            lane.Action,
            "continue",
            StringComparison.OrdinalIgnoreCase));
        int forked = controlLanes.Count(lane => string.Equals(
            lane.Action,
            "fork",
            StringComparison.OrdinalIgnoreCase));
        int frozen = controlLanes.Count - continued - forked;
        EvidenceClockLaneSummaryText.Text =
            $"{continued:N0} continue | {forked:N0} fork | {frozen:N0} blocked"
            + $" | {lanes.Count:N0} cohort view(s)";

        string epochIntegrity = continuity.ActiveEpoch is { } integrityEpoch
            ? $"plan {ShortEvidenceIdentity(integrityEpoch.PlanHash)} | "
                + $"record {ShortEvidenceIdentity(integrityEpoch.RecordHash)} | "
                + $"manifest {ShortEvidenceIdentity(integrityEpoch.ActivationManifestHash)}"
            : "activation hashes unavailable until a reviewed epoch exists";
        EvidenceClockIntegrityText.Text =
            $"gate={gateReason} | allowed={continuity.ExecutionGateAllowed.ToString().ToLowerInvariant()} | "
            + $"state={continuity.State} | cohort_match={continuity.CohortMatch.ToString().ToLowerInvariant()} | "
            + $"image_match={continuity.ImageMatch.ToString().ToLowerInvariant()} | "
            + $"release={ShortEvidenceIdentity(continuity.ActiveReleaseManifestHash ?? string.Empty)} | "
            + $"candidate={ShortEvidenceIdentity(continuity.CandidateReleaseManifestHash ?? string.Empty)} | "
            + $"due={continuity.CollectionWindow.DueCounts.DuePending:N0} | "
            + $"overdue={continuity.CollectionWindow.DueCounts.Overdue:N0} | "
            + $"epochs={continuity.EpochCount:N0} | changes_30d={continuity.CohortChanges30d:N0} | "
            + $"generated={LocalTime(continuity.GeneratedAt)} | {epochIntegrity}";
        EvidenceClockIntegrityText.Foreground = AccentBrush(accent);
    }

    private void RenderEvidenceContinuityUnavailable(string? error)
    {
        EvidenceClockBadgeText.Text = "UNAVAILABLE";
        EvidenceClockBadge.Background = AccentBrush("warning");
        EvidenceClockSubtitle.Text =
            "The evidence continuity endpoint could not be refreshed. No prior lane is promoted as current.";
        EvidenceClockActiveCohortText.Text = "Frozen cohort unavailable";
        EvidenceClockActiveCohortText.Foreground = AccentBrush("warning");
        EvidenceClockCandidateCohortText.Text = "Candidate cohort unavailable";
        EvidenceClockCandidateCohortText.Foreground = AccentBrush(null);
        EvidenceClockEpochText.Text = error ?? "No response from /api/v1/evidence-continuity";
        EvidenceClockBottleneckTitleText.Text = "Evidence continuity cannot be verified";
        EvidenceClockBottleneckTitleText.Foreground = AccentBrush("warning");
        EvidenceClockBottleneckDetailText.Text =
            "Activation and cohort matching remain fail-closed while this read model is unavailable.";
        EvidenceClockDaysText.Text = "\u2014";
        EvidenceClockDaysText.Foreground = AccentBrush(null);
        EvidenceClockDaysDetailText.Text = "Complete-day count unavailable";
        EvidenceClockVelocityText.Text = "ETA unavailable";
        EvidenceClockVelocityText.Foreground = AccentBrush(null);
        EvidenceClockVelocityDetailText.Text =
            "The evidence endpoint is unavailable; no velocity was inferred.";
        EvidenceClockStationText.Text = "\u2014";
        EvidenceClockStationDetailText.Text = "Station scope unavailable";
        EvidenceClockExposureText.Text = "\u2014";
        EvidenceClockExposureText.Foreground = AccentBrush(null);
        EvidenceClockExposureDetailText.Text = "Exposure context unavailable";
        EvidenceClockSignalText.Text = "Signals unavailable";
        EvidenceClockFunnelItems.ItemsSource = BuildUnavailableEvidenceFunnel();
        EvidenceClockSkipReasonsText.Text = "Top filters unavailable.";
        EvidenceClockLaneSummaryText.Text = "No verified lanes";
        EvidenceClockLaneItems.ItemsSource = new[]
        {
            new MonitoringListItem(
                "Evidence lanes unavailable",
                "No cached lane is substituted",
                "Refresh after the backend endpoint recovers.",
                "Endpoint unavailable; no numeric values were substituted.",
                "UNAVAILABLE",
                "warning"),
        };
        EvidenceClockIntegrityText.Text = "Evidence continuity endpoint is unavailable";
        EvidenceClockIntegrityText.Foreground = AccentBrush("warning");
    }

    private static IReadOnlyList<EvidenceFunnelItem> BuildEvidenceFunnel(
        EvidenceContinuityLaneDto? lane)
    {
        if (lane is null)
        {
            return new[]
            {
                new EvidenceFunnelItem("Probabilities", "0", "no active frozen lane", "warning"),
                new EvidenceFunnelItem("Execution intents", "0", "activation required", "warning"),
                new EvidenceFunnelItem("Filled orders", "0", "activation required", "warning"),
                new EvidenceFunnelItem("Eligible enrollments", "0", "activation required", "warning"),
                new EvidenceFunnelItem("Resolved outcomes", "0", "activation required", "warning"),
            };
        }

        return new[]
        {
            new EvidenceFunnelItem(
                "Probabilities",
                lane.ProbabilityRows.ToString("N0"),
                $"{lane.MaterializedEvents:N0} materialized | {lane.CoherentEvents:N0} coherent events",
                lane.ProbabilityRows > 0 ? "positive" : "warning"),
            new EvidenceFunnelItem(
                "Execution intents",
                lane.ExecutionIntents.ToString("N0"),
                $"{lane.OpenOrders:N0} open orders",
                lane.ExecutionIntents > 0 ? "positive" : "warning"),
            new EvidenceFunnelItem(
                "Filled orders",
                lane.FilledOrders.ToString("N0"),
                $"{lane.NoFillOrders:N0} no fill | {lane.CostSeals:N0} cost seals",
                lane.FilledOrders > 0 ? "positive" : "warning"),
            new EvidenceFunnelItem(
                "Eligible enrollments",
                lane.EligibleEnrollments.ToString("N0"),
                $"{lane.Enrollments:N0} total enrollments | {lane.EligibleCostSeals:N0} eligible seals",
                lane.EligibleEnrollments > 0 ? "positive" : "warning"),
            new EvidenceFunnelItem(
                "Resolved outcomes",
                lane.Outcomes.ToString("N0"),
                $"{lane.IndependentBlocks:N0} independent blocks",
                lane.Outcomes > 0 ? "positive" : "warning"),
        };
    }

    private static IReadOnlyList<EvidenceFunnelItem> BuildUnavailableEvidenceFunnel()
    {
        return new[]
        {
            new EvidenceFunnelItem("Probabilities", "\u2014", "endpoint unavailable", "warning"),
            new EvidenceFunnelItem("Execution intents", "\u2014", "endpoint unavailable", "warning"),
            new EvidenceFunnelItem("Filled orders", "\u2014", "endpoint unavailable", "warning"),
            new EvidenceFunnelItem("Eligible enrollments", "\u2014", "endpoint unavailable", "warning"),
            new EvidenceFunnelItem("Resolved outcomes", "\u2014", "endpoint unavailable", "warning"),
        };
    }

    private static MonitoringListItem BuildContinuityControlLaneItem(
        EvidenceContinuityControlLaneDto lane)
    {
        string action = string.IsNullOrWhiteSpace(lane.Action)
            ? "unknown"
            : lane.Action.ToLowerInvariant();
        string accent = action switch
        {
            "continue" => "positive",
            "fork" => "info",
            "freeze" => "warning",
            _ => "warning",
        };
        string parent = lane.ParentLaneId is { } parentId
            ? $"parent lane #{parentId:N0}"
            : "no parent lane";
        return new MonitoringListItem(
            $"{Pretty(lane.LaneName)} | {ShortEvidenceIdentity(lane.IdentityHash)}",
            $"Epoch #{lane.EpochId:N0} | boundary {LocalTime(lane.BoundaryAt)} | {parent}",
            string.IsNullOrWhiteSpace(lane.Rationale)
                ? "No reviewer rationale was supplied."
                : lane.Rationale,
            $"lane #{lane.Id:N0} | record {ShortEvidenceIdentity(lane.RecordHash)}",
            action.ToUpperInvariant(),
            accent);
    }

    private static MonitoringListItem BuildEvidenceLaneItem(
        EvidenceContinuityLaneDto lane,
        EvidenceContinuityDto continuity)
    {
        bool active = IsActiveEvidenceLane(lane, continuity);
        string opened = lane.OpenedAt is { } openedAt
            ? $"opened {LocalTime(openedAt)}"
            : "no activation boundary";
        string evidenceRange = lane.FirstEvidenceAt is { } first
            ? $"first {LocalTime(first)} | last "
                + (lane.LastEvidenceAt is { } last ? LocalTime(last) : "pending")
            : "no evidence rows observed";
        return new MonitoringListItem(
            string.IsNullOrWhiteSpace(lane.Cohort) ? "Unidentified cohort" : lane.Cohort,
            $"{Pretty(string.IsNullOrWhiteSpace(lane.Scope) ? "unscoped" : lane.Scope)} | "
                + $"epoch {(lane.EpochId is { } id ? $"#{id}" : "none")} | {opened}",
            $"P {lane.ProbabilityRows:N0} -> intents {lane.ExecutionIntents:N0} -> fills {lane.FilledOrders:N0} "
                + $"-> eligible {lane.EligibleEnrollments:N0} -> outcomes {lane.Outcomes:N0}",
            $"coherent {lane.CoherentEvents:N0}/{lane.MaterializedEvents:N0} events | "
                + $"seals {lane.EligibleCostSeals:N0}/{lane.CostSeals:N0} | "
                + $"marks {lane.ObservedMarks:N0}/{lane.RequiredMarks:N0} | {evidenceRange}",
            active ? "ACTIVE FROZEN" : "AUDIT ONLY",
            active ? "positive" : null);
    }

    private static bool IsActiveEvidenceLane(
        EvidenceContinuityLaneDto lane,
        EvidenceContinuityDto continuity)
    {
        if (continuity.ActiveEpoch is { } epoch)
        {
            return lane.EpochId == epoch.Id;
        }

        return !string.IsNullOrWhiteSpace(continuity.ActiveCohort)
            && string.Equals(lane.Cohort, continuity.ActiveCohort, StringComparison.Ordinal);
    }

    private static string EvidenceBottleneckTitle(string bottleneck) => bottleneck switch
    {
        "release_activation_required" or "activation_required" or "no_active_epoch" =>
            "A reviewed activation epoch is required",
        "evidence_continuity_unactivated" =>
            "New execution is blocked until an exact release is activated",
        "evidence_release_identity_mismatch" =>
            "The running release identity differs from its reviewed activation",
        "evidence_lane_identity_mismatch" =>
            "A material continuity lane identity changed",
        "evidence_lane_frozen" =>
            "A required continuity lane is frozen",
        "evidence_continuity_integrity_error" =>
            "The continuity control plane cannot be verified",
        "cohort_change_blocked" or "cohort_mismatch" =>
            "The candidate changed; frozen evidence remains protected",
        "probability_publication_empty" or "probability_rows" or "probabilities"
            or "no_probabilities" =>
            "The active cohort is not producing probability rows",
        "materialized_events" or "coherent_events" or "event_coherence" =>
            "Event probabilities are not reaching coherent materialization",
        "risk_capacity_occupied" =>
            "Existing exposure is using the available paper risk capacity",
        "awaiting_executable_signal" or "execution_intents" or "no_execution_intents"
            or "intents" =>
            "Qualified signals are not reaching execution intents",
        "maker_orders_resting" =>
            "Maker orders are resting inside their execution windows",
        "maker_no_fills" or "filled_orders" or "no_fills" or "fills" =>
            "Maker attempts are not producing genuine fills",
        "execution_seal_pending" =>
            "A terminal execution attempt is waiting for its cost seal",
        "execution_evidence_ineligible" =>
            "Observed fills do not yet satisfy the frozen evidence definition",
        "validation_enrollment_gap" or "eligible_enrollments" or "enrollments" =>
            "Fills are not qualifying for prospective enrollment",
        "awaiting_resolution" or "outcomes" or "pending_outcomes" or "no_outcomes" =>
            "Eligible fills are waiting for resolved outcomes",
        "correlated_orders_consolidated" or "independent_blocks" =>
            "Resolved outcomes have not formed enough independent blocks",
        "collecting_independent_blocks" =>
            "The frozen cohort is collecting independent resolved evidence",
        _ => string.IsNullOrWhiteSpace(bottleneck)
            ? "The backend has not identified a bottleneck"
            : Pretty(bottleneck),
    };

    private static string ShortEvidenceIdentity(string value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return "unavailable";
        }
        const int visible = 12;
        return value.Length <= visible ? value : $"{value[..visible]}...";
    }

    private void RenderEvidenceOperations(
        ResolutionReconciliationResponseDto reconciliation,
        SettlementCasesResponseDto settlement,
        StorageCapacityDto storage)
    {
        static int Count(IReadOnlyDictionary<string, int> counts, string key) =>
            counts.TryGetValue(key, out int value) ? value : 0;

        int reconciliationTotal = Math.Max(0, reconciliation.Total);
        int metarAvailable = Math.Clamp(
            reconciliation.MetarAvailable,
            0,
            reconciliationTotal);
        int metarConsistent = Math.Clamp(
            reconciliation.MetarConsistent,
            0,
            metarAvailable);
        int exactAvailable = Math.Clamp(
            reconciliation.ExactAvailable,
            0,
            reconciliationTotal);
        int exactSourceHashes = Math.Clamp(
            reconciliation.ExactSourceHashAvailable,
            0,
            exactAvailable);
        double exactCoverage = Math.Clamp(reconciliation.ExactCoverage, 0, 1);
        bool exactComplete = reconciliationTotal > 0
            && exactSourceHashes == reconciliationTotal
            && exactCoverage >= 0.9995;
        bool reconciliationFresh = reconciliationTotal > 0
            && reconciliation.Fresh
            && reconciliation.StaleRecords == 0;

        int matches = Count(reconciliation.ClassificationCounts, "match");
        int exactMismatch = Count(
            reconciliation.ClassificationCounts, "exact_source_mismatch");
        int clearMismatch = Count(reconciliation.ClassificationCounts, "clear_mismatch");
        int truthGaps = Count(reconciliation.ClassificationCounts, "exact_truth_gap");
        int boundary = Count(reconciliation.ClassificationCounts, "boundary_sensitive");
        int truthUnavailable = Count(
            reconciliation.ClassificationCounts, "truth_unavailable");
        EvidenceResolutionText.Text = reconciliationTotal == 0
            ? "Collecting reconciliation"
            : $"{metarConsistent}/{reconciliationTotal} METAR-consistent";
        EvidenceResolutionText.Foreground = AccentBrush(
            exactMismatch > 0 ? "negative"
            : clearMismatch > 0 || metarConsistent < reconciliationTotal ? "warning"
            : reconciliationTotal > 0 ? "positive" : null);
        EvidenceResolutionExactText.Text = reconciliationTotal == 0
            ? "Official exact truth 0/0 · collecting"
            : $"Official exact truth {exactSourceHashes}/{reconciliationTotal} · "
                + $"{Percent(exactCoverage)} attested coverage · raw exact values "
                + $"{exactAvailable}/{reconciliationTotal}";
        EvidenceResolutionExactText.Foreground = AccentBrush(
            exactMismatch > 0 ? "negative" : exactComplete ? "positive" : "warning");
        EvidenceResolutionDetailText.Text =
            $"METAR available {metarAvailable}/{reconciliationTotal} · match class {matches} · "
            + $"exact conflict {exactMismatch} · clear {clearMismatch} · truth gap {truthGaps} · "
            + $"boundary {boundary} · unavailable {truthUnavailable}";
        string reconciliationLatest = reconciliation.LatestCheckedAt is { } latestChecked
            ? $"latest {RelativeAge(latestChecked)} ago"
            : "latest check unavailable";
        string reconciliationOldest = reconciliation.OldestCheckedAt is { } oldestChecked
            ? $"oldest {RelativeAge(oldestChecked)} ago"
            : "oldest check unavailable";
        EvidenceResolutionFreshnessText.Text =
            $"{(reconciliationFresh ? "FRESH" : reconciliationTotal == 0 ? "COLLECTING" : "STALE")}"
            + $" · {reconciliationLatest} · {reconciliationOldest}"
            + $" · stale {Math.Max(0, reconciliation.StaleRecords)}"
            + $" · SLA {CompactDurationSeconds(reconciliation.StaleAfterSeconds)}";
        EvidenceResolutionFreshnessText.Foreground = AccentBrush(
            reconciliationFresh ? "positive" : "warning");

        int resolved = Count(settlement.ClassificationCounts, "resolved");
        int venuePending = Count(settlement.ClassificationCounts, "venue_pending");
        int sourceMissing = Count(settlement.ClassificationCounts, "source_missing");
        int ambiguous = Count(settlement.ClassificationCounts, "ambiguous");
        int manualReview = Count(settlement.ClassificationCounts, "manual_review");
        int openCases = Math.Max(
            Math.Max(0, settlement.OpenTotal),
            Math.Max(0, settlement.Total - resolved));
        bool settlementFresh = settlement.Fresh && settlement.StaleOpenCases == 0;
        EvidenceSettlementText.Text = openCases == 0
            ? settlement.Total == 0 ? "No cases" : "All resolved"
            : $"{openCases} open";
        EvidenceSettlementText.Foreground = AccentBrush(
            ambiguous + manualReview > 0 ? "negative"
            : sourceMissing > 0 ? "warning"
            : openCases == 0 && settlement.Total > 0 ? "positive" : null);
        EvidenceSettlementDetailText.Text =
            $"pending {venuePending} · source missing {sourceMissing} · "
            + $"ambiguous {ambiguous} · manual review {manualReview} · resolved {resolved}";
        string settlementLatest = settlement.LatestCheckedAt is { } latestSettlementCheck
            ? $"latest poll {RelativeAge(latestSettlementCheck)} ago"
            : "latest poll unavailable";
        string oldestOpen = settlement.OldestOpenAt is { } oldestOpenAt
            ? $"oldest open {RelativeAge(oldestOpenAt)} ago"
            : "no open-case timestamp";
        EvidenceSettlementFreshnessText.Text =
            $"{(settlementFresh ? "FRESH" : "STALE")} · {settlementLatest} · {oldestOpen}"
            + $" · stale {Math.Max(0, settlement.StaleOpenCases)}"
            + $" · SLA {CompactDurationSeconds(settlement.StaleAfterSeconds)}";
        EvidenceSettlementFreshnessText.Foreground = AccentBrush(
            settlementFresh ? "positive" : "warning");

        double capacityRatio = storage.LedgerBytes is { } ledger && storage.BudgetBytes > 0
            ? Math.Clamp((double)ledger / storage.BudgetBytes, 0, 1)
            : 0;
        EvidenceStorageProgress.Value = capacityRatio;
        EvidenceStorageText.Text = storage.LedgerBytes is { } ledgerBytes
            ? $"{FormatBytes(ledgerBytes)} / {FormatBytes(storage.BudgetBytes)}"
            : "Collecting";
        EvidenceStorageText.Foreground = AccentBrush(
            string.Equals(storage.Severity, "blocking", StringComparison.OrdinalIgnoreCase)
                ? "negative"
                : string.Equals(storage.Severity, "warning", StringComparison.OrdinalIgnoreCase)
                    ? "warning" : "positive");
        string growth = storage.GrowthBytesPerDay is { } growthPerDay
            ? $"{FormatBytes(growthPerDay)}/day"
            : $"window {storage.ObservedHours ?? 0:0.0}h / 20h";
        string runway = storage.DaysToBudget is { } days
            ? $"{days:0.0} days to budget"
            : "runway pending";
        string freeSpace = storage.FreeDiskBytes is { } freeDisk
            ? $"free {FormatBytes(freeDisk)} / reserve {FormatBytes(storage.MinimumFreeDiskBytes)}"
            : "free-space probe unavailable";
        EvidenceStorageDetailText.Text =
            $"{Pretty(storage.Reason)} · {growth} · {runway} · {freeSpace} · "
            + $"{storage.ClobSnapshotCount ?? 0:N0} paired snapshots";

        bool blocking = exactMismatch > 0
            || string.Equals(storage.Severity, "blocking", StringComparison.OrdinalIgnoreCase);
        bool critical = clearMismatch > 0 || ambiguous > 0 || manualReview > 0;
        bool warning = !exactComplete
            || !reconciliationFresh
            || !settlementFresh
            || truthGaps > 0
            || boundary > 0
            || truthUnavailable > 0
            || sourceMissing > 0
            || string.Equals(storage.Severity, "warning", StringComparison.OrdinalIgnoreCase);
        string state = blocking ? "BLOCKING" : critical ? "REVIEW" : warning ? "WATCH" : "HEALTHY";
        string accent = blocking ? "negative" : critical || warning ? "warning" : "positive";
        EvidenceOpsBadgeText.Text = state;
        EvidenceOpsBadge.Background = AccentBrush(accent);
        EvidenceOpsHintText.Text =
            $"{storage.PolicyVersion} · automatic deletion "
            + (storage.AutomaticDeletion ? "enabled" : "disabled")
            + $" · sample {(storage.SampleAt is { } at ? RelativeAge(at) + " ago" : "pending")}"
            + (storage.SampleFresh ? " · fresh" : " · stale/blocking");
    }

    private void RenderEvidenceOperationsDegraded(params string?[] errors)
    {
        string[] unavailable = errors
            .Where(error => !string.IsNullOrWhiteSpace(error))
            .Select(error => error!.Split(':', 2)[0])
            .ToArray();
        EvidenceOpsBadgeText.Text = "DEGRADED";
        EvidenceOpsBadge.Background = AccentBrush("warning");
        EvidenceOpsHintText.Text = unavailable.Length == 0
            ? "Evidence diagnostics unavailable; last-known values retained"
            : $"Last-known values retained · unavailable: {string.Join(", ", unavailable)}";
    }

    private void RenderForecastProvenance(ForecastProvenanceDto provenance)
    {
        string accent = provenance.State switch
        {
            "healthy" => "positive",
            "stale" => "negative",
            _ => "warning",
        };
        ForecastProvenanceBadgeText.Text = provenance.State switch
        {
            "healthy" => "SEALED",
            "stale" => "STALE",
            _ => "COLLECTING",
        };
        ForecastProvenanceBadge.Background = AccentBrush(accent);

        ForecastProvenanceIngestText.Text = $"{provenance.Ingests:N0}";
        ForecastProvenanceIngestText.Foreground = AccentBrush(
            provenance.Ingests > 0 ? "positive" : "warning");
        ForecastProvenanceIngestDetailText.Text =
            $"{provenance.Responses:N0} exact HTTP responses · "
            + $"{provenance.Stations:N0} stations · {FormatBytes(provenance.RawBytes)} raw";

        int materialized = provenance.MaterializedForecasts
            + provenance.MaterializedTrajectories;
        ForecastProvenanceRowsText.Text = $"{materialized:N0}";
        ForecastProvenanceRowsDetailText.Text =
            $"{provenance.MaterializedForecasts:N0} daily extremes · "
            + $"{provenance.MaterializedTrajectories:N0} trajectories";

        int active = Math.Max(0, provenance.LatestActiveProbabilities);
        int complete = Math.Clamp(provenance.CompleteActiveProbabilities, 0, active);
        double coverage = active > 0 ? (double)complete / active : 0;
        ForecastProvenanceCoverageProgress.Value = coverage;
        ForecastProvenanceProbabilityText.Text = active > 0
            ? $"{complete:N0}/{active:N0}"
            : "Collecting";
        ForecastProvenanceProbabilityText.Foreground = AccentBrush(
            active > 0 && complete == active ? "positive" : "warning");
        ForecastProvenanceProbabilityDetailText.Text = active > 0
            ? $"{Percent(coverage)} complete · live gate requires 100%"
            : "No current active-market probabilities to verify";

        ForecastProvenanceLatencyText.Text = provenance.P95HttpLatencyMs is { } httpP95
            ? $"{httpP95:0} ms"
            : "Collecting";
        string ingestLag = provenance.P95IngestionLagMs is { } lagP95
            ? $"DB seal p95 {lagP95:0} ms"
            : "DB seal latency pending";
        ForecastProvenanceLatencyDetailText.Text =
            $"{ingestLag} · latest "
            + (provenance.LatestFirstSeenAt is { } latest
                ? $"{RelativeAge(latest)} ago"
                : "not yet observed");

        string collectorList = provenance.Collectors.Count > 0
            ? string.Join(", ", provenance.Collectors
                .Select(item => $"{item.RequestModel}:{item.Ingests:N0}")
                .Take(6))
            : "collectors pending";
        ForecastProvenanceIntegrityText.Text =
            $"{provenance.DefinitionVersion} · immutable raw bytes + parsed manifest · "
            + $"zero backfill · upstream init verified "
            + $"{provenance.VerifiedUpstreamInits:N0}/{provenance.Ingests:N0} · "
            + $"pipeline fingerprints {provenance.PipelineFingerprints:N0} · {collectorList}";
        ForecastProvenanceIntegrityText.Foreground = AccentBrush(accent);
    }

    private void RenderForecastProvenanceUnavailable(string? error)
    {
        ForecastProvenanceBadgeText.Text = "UNAVAILABLE";
        ForecastProvenanceBadge.Background = AccentBrush("warning");
        ForecastProvenanceIngestText.Text = "—";
        ForecastProvenanceIngestText.Foreground = AccentBrush(null);
        ForecastProvenanceIngestDetailText.Text = "Source-response ledger unavailable";
        ForecastProvenanceRowsText.Text = "—";
        ForecastProvenanceRowsDetailText.Text = "Materialized input lineage unavailable";
        ForecastProvenanceProbabilityText.Text = "—";
        ForecastProvenanceProbabilityText.Foreground = AccentBrush("warning");
        ForecastProvenanceCoverageProgress.Value = 0;
        ForecastProvenanceProbabilityDetailText.Text =
            "Live readiness remains fail-closed while provenance is unavailable";
        ForecastProvenanceLatencyText.Text = "—";
        ForecastProvenanceLatencyDetailText.Text = "Collection timing unavailable";
        ForecastProvenanceIntegrityText.Text = string.IsNullOrWhiteSpace(error)
            ? "Forecast provenance endpoint is unavailable"
            : error;
        ForecastProvenanceIntegrityText.Foreground = AccentBrush("warning");
    }

    private void RenderOfficialResolutionSources(OfficialResolutionSourcesDto sources)
    {
        bool integrityValid = sources.SourceIntegrity.Equals(
            "valid",
            StringComparison.OrdinalIgnoreCase);
        (string badge, string accent) = !integrityValid
            ? ("INTEGRITY ERROR", "negative")
            : sources.State switch
            {
                "integrity_error" => ("INTEGRITY ERROR", "negative"),
                "disabled_external_authorization" => ("EXTERNAL AUTH BLOCKED", "warning"),
                "awaiting_official_evidence" => ("COLLECTING", "info"),
                "collecting" => ("COLLECTING", "info"),
                _ => (sources.State.Replace('_', ' ').ToUpperInvariant(), "warning"),
            };
        OfficialResolutionBadgeText.Text = badge;
        OfficialResolutionBadge.Background = AccentBrush(accent);

        int activeContracts = Math.Max(0, sources.ActiveContracts);
        int licensedContracts = Math.Clamp(
            sources.LicensedContracts,
            0,
            activeContracts);
        OfficialResolutionLicensedText.Text = activeContracts > 0
            ? $"{licensedContracts:N0}/{activeContracts:N0}"
            : "No active contracts";
        OfficialResolutionLicensedText.Foreground = AccentBrush(
            !integrityValid ? "negative"
            : activeContracts == 0 ? null
            : licensedContracts == activeContracts ? "positive"
            : licensedContracts == 0 ? "warning"
            : "info");
        OfficialResolutionLicensedDetailText.Text =
            $"{sources.BlockedContracts:N0} blocked · {sources.ExpiredContracts:N0} expired · "
            + $"{sources.LicenseAttestations:N0} attestations";

        OfficialResolutionArtifactsText.Text = sources.SourceResponses.ToString("N0");
        OfficialResolutionArtifactsText.Foreground = AccentBrush(
            !integrityValid ? "negative"
            : sources.SourceResponses > 0 ? "positive"
            : null);
        OfficialResolutionArtifactsDetailText.Text =
            $"{FormatBytes(sources.RawResponseBytes)} immutable raw responses · "
            + (sources.ArtifactImportEnabled ? "import path configured" : "import path disabled");

        double exactCoverage = Math.Clamp(sources.ExactCoverage ?? 0, 0, 1);
        OfficialResolutionCoverageProgress.Value = exactCoverage;
        OfficialResolutionExactText.Text = sources.ExactCoverage is null
            ? "Not applicable"
            : $"{sources.ExactActuals:N0}/{activeContracts:N0}";
        OfficialResolutionExactText.Foreground = AccentBrush(
            !integrityValid ? "negative"
            : sources.ExactCoverage is null ? null
            : exactCoverage >= 1 ? "positive"
            : "warning");
        OfficialResolutionExactDetailText.Text = sources.ExactCoverage is null
            ? "No active contracts require exact official truth"
            : $"{Percent(exactCoverage)} covered · "
                + $"{sources.ProjectedSettlementActuals:N0} projected settlement actuals";

        OfficialResolutionLatestText.Text = sources.LatestExactActualAt is { } latestExact
            ? $"{RelativeAge(latestExact)} ago"
            : "Not collected";
        OfficialResolutionLatestText.Foreground = AccentBrush(
            !integrityValid ? "negative"
            : sources.LatestExactActualAt is null ? "warning"
            : "positive");
        string latestResponse = sources.LatestResponseAt is { } latestRaw
            ? $"latest raw response {RelativeAge(latestRaw)} ago"
            : "no raw response observed";
        OfficialResolutionLatestDetailText.Text =
            $"{latestResponse} · endpoint refreshed {RelativeAge(sources.GeneratedAt)} ago";

        OfficialResolutionIntegrityText.Text =
            $"integrity={sources.SourceIntegrity} · state={sources.State} · "
            + $"opened={sources.OpenedAt:O} · definition={sources.DefinitionVersion} "
            + $"sha256={sources.DefinitionHash} · license={sources.LicenseProtocol} · "
            + $"response={sources.ResponseProtocol} · extraction={sources.ExtractionProtocol} · "
            + $"artifact_import={(sources.ArtifactImportEnabled ? "enabled" : "disabled")}";
        OfficialResolutionIntegrityText.Foreground = AccentBrush(accent);
    }

    private void RenderOfficialResolutionSourcesUnavailable(string? error)
    {
        OfficialResolutionBadgeText.Text = "UNAVAILABLE";
        OfficialResolutionBadge.Background = AccentBrush("warning");
        OfficialResolutionLicensedText.Text = "—";
        OfficialResolutionLicensedText.Foreground = AccentBrush(null);
        OfficialResolutionLicensedDetailText.Text = "Authorization inventory unavailable";
        OfficialResolutionArtifactsText.Text = "—";
        OfficialResolutionArtifactsText.Foreground = AccentBrush(null);
        OfficialResolutionArtifactsDetailText.Text = "Raw source-response ledger unavailable";
        OfficialResolutionExactText.Text = "—";
        OfficialResolutionExactText.Foreground = AccentBrush("warning");
        OfficialResolutionCoverageProgress.Value = 0;
        OfficialResolutionExactDetailText.Text =
            "Exact values and settlement projections unavailable";
        OfficialResolutionLatestText.Text = "—";
        OfficialResolutionLatestText.Foreground = AccentBrush("warning");
        OfficialResolutionLatestDetailText.Text = "Evidence freshness unavailable";
        OfficialResolutionIntegrityText.Text = string.IsNullOrWhiteSpace(error)
            ? "Official resolution truth endpoint is unavailable"
            : error;
        OfficialResolutionIntegrityText.Foreground = AccentBrush("warning");
    }

    private void RenderExecutionQuality(ExecutionCostReportDto report)
    {
        bool onlySealedNoFills = report.Attempts > 0
            && report.FilledAttempts == 0
            && report.PendingAttempts == 0
            && report.NoFillAttempts == report.Attempts
            && report.SealedAttempts == report.Attempts;
        (string badge, string accent) = onlySealedNoFills
            ? ("NO FILLS YET", "info")
            : report.State switch
            {
                "healthy" => ("EVIDENCE SEALED", "positive"),
                "pending" => ("MARKS PENDING", "warning"),
                "partial" => ("PARTIAL EVIDENCE", "warning"),
                "evidence_incomplete" => ("EVIDENCE INCOMPLETE", "negative"),
                _ => ("COLLECTING", "warning"),
            };
        ExecutionQualityBadgeText.Text = badge;
        ExecutionQualityBadge.Background = AccentBrush(accent);

        ExecutionQualityAttemptsText.Text = report.Attempts.ToString("N0");
        ExecutionQualityAttemptsDetailText.Text = report.Attempts == 0
            ? "Waiting for the first post-boundary intent"
            : $"{report.FilledAttempts:N0} filled · {report.NoFillAttempts:N0} no fill · "
                + $"{report.PendingAttempts:N0} pending";

        ExecutionQualityMakerText.Text = report.MakerFillRate is { } makerRate
            ? Percent(makerRate)
            : "Not measured";
        ExecutionQualityMakerText.Foreground = AccentBrush(
            report.MakerFillRate is null ? null
            : report.MakerFillRate >= 0.5 ? "positive" : "warning");
        ExecutionQualityMakerProgress.Value = report.MakerFillRate ?? 0;
        ExecutionQualityMakerDetailText.Text = report.MakerAttempts == 0
            ? "No maker attempts yet"
            : $"{report.MakerFills:N0}/{report.MakerAttempts:N0} maker attempts filled";

        ExecutionQualityEligibleText.Text = report.SealedAttempts == 0
            ? "Not measured"
            : $"{report.EligibleSeals:N0}/{report.SealedAttempts:N0}";
        ExecutionQualityEligibleText.Foreground = AccentBrush(
            report.SealedAttempts == 0 ? null
            : report.EligibleSeals == report.SealedAttempts ? "positive" : "warning");
        ExecutionQualityEligibleDetailText.Text = report.SealedAttempts == 0
            ? "No sealed execution evidence yet"
            : $"{report.IneligibleSeals:N0} ineligible · role mix "
                + $"M {report.MakerSeals:N0} / T {report.TakerSeals:N0} / "
                + $"mixed {report.MixedSeals:N0} / unknown {report.UnknownRoleSeals:N0}";

        SetExecutionEdgeText(
            ExecutionQualityNetText,
            report.Metrics.RealizedNetEdge,
            positiveIsGood: true);
        ExecutionQualityNetDetailText.Text = report.Metrics.RealizedNetEdge is null
            ? "Requires an eligible sealed fill with exact fee evidence"
            : $"Share-weighted across {report.Metrics.EligibleOrders:N0} eligible orders · "
                + $"{report.Metrics.EligibleShares:N2} shares";

        SetExecutionEdgeText(
            ExecutionQualityGrossText,
            report.Metrics.GrossModelEdge,
            positiveIsGood: true);
        SetExecutionEdgeText(
            ExecutionQualityLatencyText,
            report.Metrics.LatencyMovement,
            positiveIsGood: false);
        SetExecutionEdgeText(
            ExecutionQualitySpreadText,
            report.Metrics.TakerSpread,
            positiveIsGood: false);
        SetExecutionEdgeText(
            ExecutionQualitySlippageText,
            report.Metrics.TakerSlippage,
            positiveIsGood: false);
        SetExecutionEdgeText(
            ExecutionQualityMakerImprovementText,
            report.Metrics.MakerImprovement,
            positiveIsGood: true);
        SetExecutionEdgeText(
            ExecutionQualityFeeText,
            report.Metrics.FeeDrag,
            positiveIsGood: false);
        SetExecutionEdgeText(
            ExecutionQualityWaterfallNetText,
            report.Metrics.RealizedNetEdge,
            positiveIsGood: true);

        SetExecutionCoverage(
            ExecutionQualitySlippageCoverageProgress,
            ExecutionQualitySlippageCoverageText,
            report.ExpectedSlippageCoverage);
        SetExecutionCoverage(
            ExecutionQualityFeeCoverageProgress,
            ExecutionQualityFeeCoverageText,
            report.ExactFeeCoverage);
        SetExecutionCoverage(
            ExecutionQuality5mCoverageProgress,
            ExecutionQuality5mCoverageText,
            report.Markout5mCoverage);
        SetExecutionCoverage(
            ExecutionQuality30mCoverageProgress,
            ExecutionQuality30mCoverageText,
            report.Markout30mCoverage);

        string adverse5m = ExecutionEdge(report.Metrics.AdverseSelection5m);
        string adverse30m = ExecutionEdge(report.Metrics.AdverseSelection30m);
        ExecutionQualityMarkoutText.Text =
            $"Adverse selection after fill: 5 min {adverse5m} · 30 min {adverse30m}. "
            + "Positive values mean the market moved against the fill.";
        bool adverse = report.Metrics.AdverseSelection5m > 0
            || report.Metrics.AdverseSelection30m > 0;
        ExecutionQualityMarkoutText.Foreground = AccentBrush(adverse ? "warning" : null);

        string reasons = report.Reasons.Count == 0
            ? "no ineligibility reasons"
            : string.Join(", ", report.Reasons
                .Take(5)
                .Select(item => $"{Pretty(item.Reason)} {item.Orders:N0}"));
        string shortHash = report.DefinitionHash[..Math.Min(12, report.DefinitionHash.Length)];
        ExecutionQualityIntegrityText.Text =
            $"{report.DefinitionVersion} · opened {LocalTime(report.OpenedAt)} · "
            + $"definition {shortHash} · {report.SourceIntegrity} · {reasons}";
        ExecutionQualityIntegrityText.Foreground = AccentBrush(accent);
    }

    private void RenderExecutionQualityUnavailable(string? error)
    {
        ExecutionQualityBadgeText.Text = "UNAVAILABLE";
        ExecutionQualityBadge.Background = AccentBrush("warning");
        ExecutionQualityAttemptsText.Text = "—";
        ExecutionQualityAttemptsDetailText.Text = "Execution intent ledger unavailable";
        ExecutionQualityMakerText.Text = "Not measured";
        ExecutionQualityMakerText.Foreground = AccentBrush(null);
        ExecutionQualityMakerProgress.Value = 0;
        ExecutionQualityMakerDetailText.Text = "Maker fill evidence unavailable";
        ExecutionQualityEligibleText.Text = "Not measured";
        ExecutionQualityEligibleText.Foreground = AccentBrush(null);
        ExecutionQualityEligibleDetailText.Text = "Seal eligibility unavailable";
        ExecutionQualityNetText.Text = "Not measured";
        ExecutionQualityNetText.Foreground = AccentBrush(null);
        ExecutionQualityNetDetailText.Text = "Execution cost decomposition unavailable";
        foreach (TextBlock value in new[]
        {
            ExecutionQualityGrossText,
            ExecutionQualityLatencyText,
            ExecutionQualitySpreadText,
            ExecutionQualitySlippageText,
            ExecutionQualityMakerImprovementText,
            ExecutionQualityFeeText,
            ExecutionQualityWaterfallNetText,
        })
        {
            value.Text = "Not measured";
            value.Foreground = AccentBrush(null);
        }
        SetExecutionCoverageUnavailable(
            ExecutionQualitySlippageCoverageProgress,
            ExecutionQualitySlippageCoverageText);
        SetExecutionCoverageUnavailable(
            ExecutionQualityFeeCoverageProgress,
            ExecutionQualityFeeCoverageText);
        SetExecutionCoverageUnavailable(
            ExecutionQuality5mCoverageProgress,
            ExecutionQuality5mCoverageText);
        SetExecutionCoverageUnavailable(
            ExecutionQuality30mCoverageProgress,
            ExecutionQuality30mCoverageText);
        ExecutionQualityMarkoutText.Text =
            "Adverse selection: 5 min not measured · 30 min not measured";
        ExecutionQualityMarkoutText.Foreground = AccentBrush(null);
        ExecutionQualityIntegrityText.Text = string.IsNullOrWhiteSpace(error)
            ? "Execution-quality endpoint is unavailable"
            : error;
        ExecutionQualityIntegrityText.Foreground = AccentBrush("warning");
    }

    private static void SetExecutionCoverage(
        ProgressBar progress,
        TextBlock text,
        ExecutionCostCoverageDto coverage)
    {
        progress.Value = coverage.Ratio ?? 0;
        text.Text = coverage.Total == 0
            ? "Not measured"
            : $"{coverage.Known:N0}/{coverage.Total:N0} · {Percent(coverage.Ratio)}";
    }

    private static void SetExecutionCoverageUnavailable(ProgressBar progress, TextBlock text)
    {
        progress.Value = 0;
        text.Text = "Not measured";
    }

    private void SetExecutionEdgeText(
        TextBlock text,
        double? value,
        bool positiveIsGood)
    {
        text.Text = ExecutionEdge(value);
        string? accent = value switch
        {
            null => null,
            > 0 when positiveIsGood => "positive",
            < 0 when !positiveIsGood => "positive",
            0 => null,
            _ => "negative",
        };
        text.Foreground = AccentBrush(accent);
    }

    private static string ExecutionEdge(double? value) => value is { } edge
        ? (edge * 100).ToString("+0.00¢;-0.00¢;0.00¢", CultureInfo.InvariantCulture)
        : "Not measured";

    private void RenderPromotionProtocol(AccuracyPromotionOverviewDto? promotion)
    {
        if (promotion is null)
        {
            PromotionBadgeText.Text = "UNAVAILABLE";
            PromotionBadge.Background = AccentBrush("warning");
            PromotionProtocolText.Text = "Promotion registry is not available from this backend";
            PromotionManifestText.Text = "manifest —";
            PromotionProgressBar.Maximum = 1;
            PromotionProgressBar.Value = 0;
            PromotionProgressText.Text = "0/60 blocks";
            PromotionRegisteredText.Text = "0 / 0";
            PromotionCollectingText.Text = "0";
            PromotionDecisionText.Text = "0 / 0";
            PromotionActivatedText.Text = "0";
            PromotionActivatedText.Foreground = AccentBrush(null);
            PromotionTrialsHeadingText.Text = "Priority trials";
            PromotionTrialItems.ItemsSource = new List<MonitoringListItem>
            {
                new(
                    "No registered confirmatory trials",
                    "The accuracy laboratory remains diagnostic-only",
                    "Refresh after the promotion registry has been initialized.",
                    string.Empty,
                    "WAITING",
                    "warning"),
            };
            return;
        }

        bool staleActivation = promotion.Trials.Any(trial =>
            trial.Status.Equals("stale_activation", StringComparison.OrdinalIgnoreCase));
        (string badge, string accent) = staleActivation
            ? ("INTEGRITY ALERT", "negative")
            : promotion.ActivatedTrials > 0
                ? ($"APPLIED · {promotion.ActivatedTrials}", "positive")
                : promotion.PassedTrials > 0
                    ? ("PASSED · AWAITING FAMILY", "warning")
                    : promotion.CollectingTrials > 0
                        ? ("COLLECTING", "warning")
                        : promotion.FailedTrials > 0
                            ? ("FAMILY CLOSED", "negative")
                            : ("NOT REGISTERED", "neutral");
        PromotionBadgeText.Text = badge;
        PromotionBadge.Background = AccentBrush(accent);
        PromotionProtocolText.Text =
            $"Epoch {promotion.RegistrationEpoch} · {promotion.TestMethod} · "
            + $"family α={promotion.FamilyAlpha:0.###} · {promotion.ProtocolVersion}";
        string manifest = promotion.ActivationManifestHash;
        PromotionManifestText.Text = string.IsNullOrWhiteSpace(manifest)
            ? "manifest —"
            : $"manifest {manifest[..Math.Min(12, manifest.Length)]}";

        int targetBlocks = Math.Max(1, promotion.TargetBlocks);
        int maximumBlocks = Math.Clamp(promotion.MaximumEvidenceBlocks, 0, targetBlocks);
        PromotionProgressBar.Maximum = targetBlocks;
        PromotionProgressBar.Value = maximumBlocks;
        PromotionProgressText.Text =
            $"most advanced {promotion.MaximumEvidenceBlocks}/{promotion.TargetBlocks} blocks";
        PromotionRegisteredText.Text =
            $"{promotion.RegisteredTrials} / {promotion.FamilySize}";
        PromotionCollectingText.Text = promotion.CollectingTrials.ToString();
        PromotionDecisionText.Text =
            $"{promotion.PassedTrials} / {promotion.FailedTrials}";
        PromotionActivatedText.Text = promotion.ActivatedTrials.ToString();
        PromotionCollectingText.Foreground = AccentBrush(
            promotion.CollectingTrials > 0 ? "warning" : null);
        PromotionDecisionText.Foreground = AccentBrush(
            promotion.FailedTrials > 0 ? "negative"
            : promotion.PassedTrials > 0 ? "warning"
            : null);
        PromotionActivatedText.Foreground = AccentBrush(
            promotion.ActivatedTrials > 0 ? "positive" : null);

        IReadOnlyList<AccuracyPromotionTrialDto> trials = promotion.Trials ?? [];
        List<MonitoringListItem> priorityTrials = trials
            .OrderBy(trial => PromotionTrialPriority(trial))
            .ThenByDescending(trial => trial.EvidenceBlocks)
            .ThenBy(trial => trial.StationIcao)
            .ThenBy(trial => trial.Expert)
            .Take(12)
            .Select(trial =>
            {
                string result = trial.MeanDeltaBrier is { } delta
                    ? $"ΔBrier {delta:+0.0000;-0.0000;0.0000}"
                    : "ΔBrier collecting";
                string outcomes = trial.Wins is { } wins
                    && trial.Losses is { } losses
                    && trial.Ties is { } ties
                        ? $"W/L/T {wins}/{losses}/{ties}"
                        : "W/L/T collecting";
                string bound = trial.MeanImprovementLcb is { } lowerBound
                    ? $"improvement LCB {lowerBound:+0.0000;-0.0000;0.0000}"
                    : "improvement LCB collecting";
                string test = trial.OneSidedPBound is { } pValue
                    ? $"p-bound={pValue:0.###E+0} ≤ α={trial.AllocatedAlpha:0.###E+0}"
                    : $"α={trial.AllocatedAlpha:0.###E+0}";
                return new MonitoringListItem(
                    Pretty(trial.Expert),
                    $"{trial.StationIcao} · {Pretty(trial.Kind)} · {Pretty(trial.HorizonBucket)}",
                    $"{trial.EvidenceBlocks}/{trial.TargetBlocks} blocks · {result} · "
                        + $"{outcomes} · {bound} · {test}",
                    string.Empty,
                    PromotionTrialStatus(trial),
                    PromotionTrialAccent(trial));
            })
            .ToList();
        PromotionTrialsHeadingText.Text = trials.Count > priorityTrials.Count
            ? $"Priority trials · showing {priorityTrials.Count} of {trials.Count}"
            : $"Priority trials · {trials.Count}";
        PromotionTrialItems.ItemsSource = priorityTrials.Count > 0
            ? priorityTrials
            : new List<MonitoringListItem>
            {
                new(
                    "No trials registered for this epoch",
                    "The family is frozen before confirmatory evidence can be collected",
                    $"Target {promotion.TargetBlocks} independent blocks per trial.",
                    string.Empty,
                    "WAITING",
                    "warning"),
            };
    }

    private static int PromotionTrialPriority(AccuracyPromotionTrialDto trial) =>
        trial.Status.ToLowerInvariant() switch
        {
            "stale_activation" => 0,
            "activated" => 1,
            "passed" => 2,
            "failed" => 3,
            _ => 4,
        };

    private static string PromotionTrialStatus(AccuracyPromotionTrialDto trial) =>
        trial.Activated ? "APPLIED"
        : trial.Status.ToLowerInvariant() switch
        {
            "stale_activation" => "STALE ACTIVATION",
            "passed" => "PASSED",
            "failed" => "FAILED",
            _ => "COLLECTING",
        };

    private static string PromotionTrialAccent(AccuracyPromotionTrialDto trial) =>
        trial.Activated ? "positive"
        : trial.Status.ToLowerInvariant() switch
        {
            "stale_activation" or "failed" => "negative",
            "passed" or "collecting" => "warning",
            _ => "neutral",
        };

    private void RenderAccuracyOverview(AccuracyOverviewDto accuracy)
    {
        bool truthReady = accuracy.ResolutionContracts > 0
            && accuracy.ExactSettlementActuals > 0;
        AccuracyBadgeText.Text = accuracy.OpenDriftEvents > 0
            ? $"DRIFT · {accuracy.OpenDriftEvents}"
            : truthReady ? "PIPELINE ACTIVE" : "BUILDING EVIDENCE";
        AccuracyBadge.Background = AccentBrush(
            accuracy.OpenDriftEvents > 0 ? "negative" : truthReady ? "positive" : "warning");
        AccuracyTruthText.Text = $"{accuracy.ExactSettlementActuals} exact";
        AccuracyTruthDetail.Text =
            $"{accuracy.ResolutionContracts} contracts · {accuracy.LicensedContracts} storage-approved";
        AccuracyEvaluationText.Text = accuracy.IndependentSamples.ToString();
        AccuracyEvaluationDetail.Text =
            $"{accuracy.ForecastEvaluations} horizon evaluations · "
            + EvidenceScope(accuracy.BenchmarkProtocol, accuracy.SampleScope);
        AccuracyBrierText.Text = accuracy.MeanModelBrier is { } model
            ? accuracy.MeanMarketBrier is { } market
                ? $"{model:0.000} vs {market:0.000}"
                : $"{model:0.000}"
            : "—";
        AccuracyBrierText.Foreground = AccentBrush(
            accuracy.MeanModelBrier is { } modelBrier
            && accuracy.MeanMarketBrier is { } marketBrier
                ? modelBrier < marketBrier ? "positive" : "negative"
                : null);
        AccuracySkillText.Text = accuracy.QualifiedSkillReports.ToString();
        AccuracySkillDetail.Text =
            $"rolling diagnostic reports · {accuracy.ActiveStackExperts} immutable active stack scopes";
        RenderPromotionProtocol(accuracy.Promotion);

        SettlementCalibratorDto? settlement = accuracy.SettlementCalibrator;
        if (settlement is null)
        {
            AccuracySettlementCalibrationText.Text =
                "Settlement calibration · waiting for the first fit";
            AccuracySettlementCalibrationText.Foreground = AccentBrush("warning");
        }
        else
        {
            string status = settlement.Applied ? "APPLIED"
                : settlement.Eligible ? "QUALIFIED SHADOW"
                : "SHADOW";
            string brier = settlement.BaselineBrier is { } baselineBrier
                && settlement.CalibratedBrier is { } calibratedBrier
                    ? $"Brier {baselineBrier:0.0000}→{calibratedBrier:0.0000}"
                    : "Brier collecting";
            string ece = settlement.BaselineEce is { } baselineEce
                && settlement.CalibratedEce is { } calibratedEce
                    ? $"ECE {baselineEce:0.0000}→{calibratedEce:0.0000}"
                    : "ECE collecting";
            string interval = settlement.DeltaCiLow is { } ciLow
                && settlement.DeltaCiHigh is { } ciHigh
                    ? $"Δ95% [{ciLow:+0.0000;-0.0000;0.0000}, {ciHigh:+0.0000;-0.0000;0.0000}]"
                    : "Δ95% collecting";
            AccuracySettlementCalibrationText.Text =
                $"Settlement β · {status} · {settlement.TrainingBlocks}/{settlement.MinimumBlocks} blocks "
                + $"({settlement.TrainingBuckets} buckets) · {brier} · {ece} · {interval} · "
                + $"α={settlement.Alpha:0.000} β={settlement.Beta:0.000}";
            AccuracySettlementCalibrationText.Foreground = AccentBrush(
                settlement.Applied ? "positive"
                : settlement.Eligible ? "warning"
                : settlement.TrainingBlocks >= settlement.MinimumBlocks ? "negative"
                : "warning");
        }

        CensoredChallengerDto censored = accuracy.CensoredChallenger;
        string censoredBias = censored.BiasC is { } bias
            ? $"bias {bias:+0.00;-0.00;0.00}°C"
            : "bias collecting";
        string censoredScale = censored.SigmaScale is { } sigmaScale
            ? $"σ×{sigmaScale:0.000}"
            : "scale collecting";
        string censoredHistorical = censored.HistoricalBaselineBrier is { } historicalBase
            && censored.HistoricalCalibratedBrier is { } historicalAdjusted
                ? $"historical OOF {historicalBase:0.0000}→{historicalAdjusted:0.0000}"
                : "historical OOF collecting";
        string censoredHistoricalCi = censored.HistoricalCiLow is { } historicalLow
            && censored.HistoricalCiHigh is { } historicalHigh
                ? $"95% [{historicalLow:+0.0000;-0.0000;0.0000}, "
                    + $"{historicalHigh:+0.0000;-0.0000;0.0000}]"
                : "95% collecting";
        string censoredProspective = censored.DeltaBrier is { } prospectiveDelta
            ? $"future Δ {prospectiveDelta:+0.0000;-0.0000;0.0000}"
            : "future Δ collecting";
        AccuracyCensoredText.Text =
            $"Interval-censored Gaussian · SHADOW · {censored.CurrentMarkets} markets · "
            + $"train {censored.TrainingBlocks} winning-bucket blocks · {censoredBias} · "
            + $"{censoredScale} · {censoredHistorical} · {censoredHistoricalCi} · "
            + $"future max {censored.MaximumBlocks}/{censored.MinimumBlocks} blocks · "
            + $"{censored.EligibleSlices}/{censored.EvaluatedSlices} eligible · "
            + censoredProspective;
        AccuracyCensoredText.Foreground = AccentBrush(
            censored.Applied ? "positive"
            : censored.MaximumBlocks >= censored.MinimumBlocks ? "negative"
            : "warning");

        WeightedEnsembleDto weighted = accuracy.WeightedEnsemble;
        string weightedHistorical = weighted.HistoricalBaselineRmseC is { } baselineRmse
            && weighted.HistoricalCandidateRmseC is { } candidateRmse
                ? $"walk-forward RMSE {baselineRmse:0.000}→{candidateRmse:0.000} °C"
                : "walk-forward RMSE collecting";
        string weightedHistoricalCi = weighted.HistoricalCiLow is { } weightedLow
            && weighted.HistoricalCiHigh is { } weightedHigh
                ? $"ΔMSE 95% [{weightedLow:+0.000;-0.000;0.000}, "
                    + $"{weightedHigh:+0.000;-0.000;0.000}]"
                : "ΔMSE CI collecting";
        string weightedWeights = weighted.Weights.Count > 0
            ? string.Join(
                ", ",
                weighted.Weights
                    .OrderByDescending(item => item.Value)
                    .Select(item => $"{item.Key} {item.Value:P0}"))
            : "weights collecting";
        string weightedProspective = weighted.DeltaBrier is { } weightedDelta
            ? $"future Brier Δ {weightedDelta:+0.0000;-0.0000;0.0000}"
            : "future Brier collecting";
        AccuracyWeightedEnsembleText.Text =
            $"Historical convex ensemble · SHADOW · {weighted.CurrentMarkets} markets · "
            + $"{weighted.FittedScopes} fitted scopes · local train "
            + $"{weighted.MinimumTrainingSampleN}–{weighted.MaximumTrainingSampleN} · historical "
            + $"{weighted.HistoricalSamples} predictions/{weighted.HistoricalBlocks} blocks · "
            + $"{weightedHistorical} · {weightedHistoricalCi} · {weightedWeights} · "
            + $"future max {weighted.MaximumBlocks}/{weighted.MinimumBlocks} blocks · "
            + $"{weighted.EligibleSlices}/{weighted.EvaluatedSlices} eligible · "
            + weightedProspective;
        AccuracyWeightedEnsembleText.Foreground = AccentBrush(
            weighted.Applied ? "positive"
            : !weighted.HistoricalOofEligible && weighted.HistoricalSamples > 0 ? "negative"
            : "warning");
        string weightedSettlementBrier = weighted.SettlementBaselineBrier is { } settlementBase
            && weighted.SettlementCandidateBrier is { } settlementCandidate
                ? $"Gaussian {settlementCandidate:0.0000}"
                : "Gaussian collecting";
        string weightedSettlementDelta = weighted.SettlementDeltaBrier is { } settlementDelta
            ? $"Δ {settlementDelta:+0.0000;-0.0000;0.0000}"
            : "Δ collecting";
        string weightedSettlementCi = weighted.SettlementCiLow is { } settlementLow
            && weighted.SettlementCiHigh is { } settlementHigh
                ? $"95% [{settlementLow:+0.0000;-0.0000;0.0000}, "
                    + $"{settlementHigh:+0.0000;-0.0000;0.0000}]"
                : "95% CI collecting";
        string weightedEmpirical = weighted.SettlementEmpiricalCandidateBrier is { } empiricalBrier
            ? $"empirical {empiricalBrier:0.0000}"
            : "empirical collecting";
        string weightedShrunk = weighted.SettlementBaselineBrier is { } shrunkBaseline
            && weighted.SettlementShrunkEmpiricalBrier is { } shrunkBrier
            && weighted.SettlementShrunkEmpiricalDeltaBrier is { } shrunkDelta
                ? $"shrink {weighted.SettlementShrunkEmpiricalWeight:P0}: "
                    + $"{shrunkBaseline:0.0000}→{shrunkBrier:0.0000} "
                    + $"(Δ {shrunkDelta:+0.0000;-0.0000;0.0000})"
                : "shrink candidate collecting";
        string weightedShrunkCi = weighted.SettlementShrunkEmpiricalCiLow is { } shrunkLow
            && weighted.SettlementShrunkEmpiricalCiHigh is { } shrunkHigh
                ? $"95% [{shrunkLow:+0.0000;-0.0000;0.0000}, "
                    + $"{shrunkHigh:+0.0000;-0.0000;0.0000}]"
                : "95% CI collecting";
        string weightedShrunkFuture = weighted.ShrunkDeltaBrier is { } shrunkFutureDelta
            ? $"future Δ {shrunkFutureDelta:+0.0000;-0.0000;0.0000}"
            : "future Brier collecting";
        AccuracyWeightedSettlementText.Text =
            $"Settlement-aligned PIT backtest · "
            + $"{weighted.SettlementFittedEvents}/{weighted.SettlementAvailableEvents} events · "
            + $"{weighted.SettlementBacktestBlocks}/"
            + $"{weighted.MinimumSettlementBacktestBlocks} independent blocks · "
            + $"{weighted.SettlementBacktestMarketRows} bucket rows · "
            + $"{weightedSettlementBrier} · {weightedSettlementDelta} · {weightedSettlementCi} · "
            + $"{weightedEmpirical} · {weightedShrunk} · {weightedShrunkCi} · "
            + (weighted.SettlementShrunkEmpiricalEligible
                ? "HISTORICAL PASS · "
                : "NOT PROVEN · ")
            + $"SEALED v2 {weighted.ShrunkCurrentMarkets} markets · "
            + $"legacy excluded {weighted.ShrunkLegacyMarkets} · future "
            + $"{weighted.ShrunkMaximumBlocks}/{weighted.ShrunkMinimumBlocks} blocks · "
            + weightedShrunkFuture;
        AccuracyWeightedSettlementText.Foreground = AccentBrush(
            weighted.SettlementShrunkEmpiricalEligible ? "positive"
            : weighted.SettlementBacktestBlocks >= weighted.MinimumSettlementBacktestBlocks
                ? "negative"
                : "warning");

        TrajectoryChallengerDto trajectory = accuracy.TrajectoryChallenger;
        string trajectoryStatus = trajectory.Applied ? "APPLIED" : "SHADOW";
        string trajectoryBrier = trajectory.Brier is { } trajectoryBrierValue
            ? $"Brier {trajectoryBrierValue:0.0000}"
            : "Brier collecting";
        string trajectoryDelta = trajectory.DeltaBrier is { } trajectoryDeltaValue
            ? $"Δ {trajectoryDeltaValue:+0.0000;-0.0000;0.0000}"
            : "Δ collecting";
        string trajectoryCi = trajectory.CiLow is { } trajectoryCiLow
            && trajectory.CiHigh is { } trajectoryCiHigh
                ? $"95% [{trajectoryCiLow:+0.0000;-0.0000;0.0000}, "
                    + $"{trajectoryCiHigh:+0.0000;-0.0000;0.0000}]"
                : "95% collecting";
        AccuracyTrajectoryText.Text =
            $"Hourly AR(1) trajectories · {trajectoryStatus} · "
            + $"{trajectory.StoredTrajectories} stored · "
            + $"max {trajectory.MaximumBlocks}/{trajectory.MinimumBlocks} blocks · "
            + $"{trajectory.EligibleSlices}/{trajectory.EvaluatedSlices} eligible slices · "
            + $"{trajectoryBrier} · {trajectoryDelta} · {trajectoryCi}";
        AccuracyTrajectoryText.Foreground = AccentBrush(
            trajectory.Applied ? "positive"
            : trajectory.StoredTrajectories == 0 ? "warning"
            : trajectory.MaximumBlocks >= trajectory.MinimumBlocks ? "negative"
            : "warning");

        ConformalGuardrailDto conformal = accuracy.ConformalGuardrail;
        string conformalStatus = conformal.Applied ? "APPLIED" : "SHADOW";
        string conformalScale = conformal.Scale is { } scale
            ? $"σ×{scale:0.000}"
            : "scale collecting";
        string conformalCoverage = conformal.EmpiricalCoverageBefore is { } coverageBefore
            && conformal.EmpiricalCoverageAfter is { } coverageAfter
                ? $"coverage {coverageBefore:P1}→{coverageAfter:P1} "
                    + $"(target {conformal.TargetCoverage:P0})"
                : "coverage collecting";
        string conformalCi = conformal.CiLow is { } conformalCiLow
            && conformal.CiHigh is { } conformalCiHigh
                ? $"Δ95% [{conformalCiLow:+0.0000;-0.0000;0.0000}, "
                    + $"{conformalCiHigh:+0.0000;-0.0000;0.0000}]"
                : "Δ95% collecting";
        AccuracyConformalText.Text =
            $"Conformal uncertainty · {conformalStatus} · {conformalScale} · "
            + $"{conformal.ResidualSamples} residuals · {conformalCoverage} · "
            + $"max {conformal.MaximumBlocks}/{conformal.MinimumBlocks} blocks · "
            + $"{conformal.EligibleSlices}/{conformal.EvaluatedSlices} eligible · {conformalCi}";
        AccuracyConformalText.Foreground = AccentBrush(
            conformal.Applied ? "positive"
            : conformal.ResidualSamples == 0 ? "warning"
            : conformal.MaximumBlocks >= conformal.MinimumBlocks ? "negative"
            : "warning");

        NowcastChallengerDto nowcast = accuracy.NowcastChallenger;
        string nowcastStatus = nowcast.Applied ? "APPLIED" : "SHADOW";
        string nowcastBias = nowcast.RecentBiasC is { } recentBias
            ? $"recent bias {recentBias:+0.00;-0.00;0.00} °C"
            : "bias collecting";
        string nowcastShift = nowcast.ExtremeShiftC is { } extremeShift
            ? $"extreme shift {extremeShift:+0.00;-0.00;0.00} °C"
            : "shift collecting";
        string nowcastCi = nowcast.CiLow is { } nowcastCiLow
            && nowcast.CiHigh is { } nowcastCiHigh
                ? $"Δ95% [{nowcastCiLow:+0.0000;-0.0000;0.0000}, "
                    + $"{nowcastCiHigh:+0.0000;-0.0000;0.0000}]"
                : "Δ95% collecting";
        AccuracyNowcastText.Text =
            $"Remaining-day nowcast · {nowcastStatus} · {nowcast.CurrentMarkets} markets · "
            + $"{nowcastBias} · {nowcastShift} · {nowcast.MatchedHours} matched hours · "
            + $"max {nowcast.MaximumBlocks}/{nowcast.MinimumBlocks} blocks · "
            + $"{nowcast.EligibleSlices}/{nowcast.EvaluatedSlices} eligible · {nowcastCi}";
        AccuracyNowcastText.Foreground = AccentBrush(
            nowcast.Applied ? "positive"
            : nowcast.CurrentMarkets == 0 ? "warning"
            : nowcast.MaximumBlocks >= nowcast.MinimumBlocks ? "negative"
            : "warning");

        HierarchicalChallengerDto hierarchical = accuracy.HierarchicalChallenger;
        string hierarchicalStatus = hierarchical.Applied ? "APPLIED" : "SHADOW";
        string hierarchicalWeight = hierarchical.LocalWeight is { } localWeight
            ? $"local weight {localWeight:P0}"
            : "weight collecting";
        string hierarchicalCi = hierarchical.CiLow is { } hierarchicalCiLow
            && hierarchical.CiHigh is { } hierarchicalCiHigh
                ? $"Δ95% [{hierarchicalCiLow:+0.0000;-0.0000;0.0000}, "
                    + $"{hierarchicalCiHigh:+0.0000;-0.0000;0.0000}]"
                : "Δ95% collecting";
        AccuracyHierarchicalText.Text =
            $"Hierarchical EMOS · {hierarchicalStatus} · {hierarchical.CurrentMarkets} markets · "
            + $"{hierarchicalWeight} · {hierarchical.LocalSampleN} local + "
            + $"{hierarchical.PeerSampleN} peer samples across "
            + $"{hierarchical.PeerStations} stations · max "
            + $"{hierarchical.MaximumBlocks}/{hierarchical.MinimumBlocks} blocks · "
            + $"{hierarchical.EligibleSlices}/{hierarchical.EvaluatedSlices} eligible · "
            + hierarchicalCi;
        AccuracyHierarchicalText.Foreground = AccentBrush(
            hierarchical.Applied ? "positive"
            : hierarchical.CurrentMarkets == 0 ? "warning"
            : hierarchical.MaximumBlocks >= hierarchical.MinimumBlocks ? "negative"
            : "warning");

        RegimeChallengerDto regime = accuracy.RegimeChallenger;
        string regimeStatus = regime.Applied ? "APPLIED" : "SHADOW";
        string regimeAdjustment = regime.StrongestAdjustmentC is { } adjustment
            ? $"strongest {adjustment:+0.00;-0.00;0.00} C"
            : "adjustment collecting";
        string regimeSource = regime.StrongestModel is { } regimeModel
            ? $"{regimeModel}/{regime.ModelVersion ?? "unversioned"}"
            : "model collecting";
        string regimeBoundary = regime.BoundaryKind is { } boundaryKind
            ? $"{boundaryKind.Replace('_', ' ')} {regime.BoundaryDate ?? ""}".Trim()
            : "boundary collecting";
        string regimeDelta = regime.DeltaBrier is { } regimeDeltaValue
            ? $"delta {regimeDeltaValue:+0.0000;-0.0000;0.0000}"
            : "delta collecting";
        string regimeCi = regime.CiLow is { } regimeCiLow
            && regime.CiHigh is { } regimeCiHigh
                ? $"95% [{regimeCiLow:+0.0000;-0.0000;0.0000}, "
                    + $"{regimeCiHigh:+0.0000;-0.0000;0.0000}]"
                : "95% collecting";
        AccuracyRegimeText.Text =
            $"Version-aware EMOS · {regimeStatus} · {regime.ActiveShifts} active shifts "
            + $"({regime.CriticalShifts} critical) · {regime.CurrentMarkets} markets · "
            + $"{regimeSource} · {regimeAdjustment} · {regimeBoundary} · max "
            + $"{regime.MaximumBlocks}/{regime.MinimumBlocks} blocks · "
            + $"{regime.EligibleSlices}/{regime.EvaluatedSlices} eligible · "
            + $"{regimeDelta} · {regimeCi}";
        AccuracyRegimeText.Foreground = AccentBrush(
            regime.Applied ? "positive"
            : regime.ActiveShifts == 0 ? "warning"
            : regime.MaximumBlocks >= regime.MinimumBlocks ? "negative"
            : "warning");

        StackChallengerDto stack = accuracy.StackChallenger;
        string stackStatus = stack.Applied ? "APPLIED" : "SHADOW";
        string stackDelta = stack.DeltaBrier is { } stackDeltaValue
            ? $"delta {stackDeltaValue:+0.0000;-0.0000;0.0000}"
            : "delta collecting";
        string stackCi = stack.CiLow is { } stackCiLow
            && stack.CiHigh is { } stackCiHigh
                ? $"95% [{stackCiLow:+0.0000;-0.0000;0.0000}, "
                    + $"{stackCiHigh:+0.0000;-0.0000;0.0000}]"
                : "95% collecting";
        AccuracyStackText.Text =
            $"Regularized stack · {stackStatus} · {stack.CurrentMarkets} markets · "
            + $"train {stack.TrainingSampleN}/{stack.MinimumTrainingSamples} · "
            + $"{stack.FitSource ?? "no fit"} ({stack.LocalSampleN} local + "
            + $"{stack.PeerSampleN} peer/{stack.PeerStations} stations) · max "
            + $"{stack.MaximumBlocks}/{stack.MinimumBlocks} future blocks · "
            + $"{stack.EligibleSlices}/{stack.EvaluatedSlices} eligible · "
            + $"{stackDelta} · {stackCi}";
        AccuracyStackText.Foreground = AccentBrush(
            stack.Applied ? "positive"
            : stack.MaximumBlocks >= stack.MinimumBlocks ? "negative"
            : "warning");

        NonlinearChallengerDto nonlinear = accuracy.NonlinearChallengers;
        string nonlinearDetails = string.Join(
            " · ",
            nonlinear.Experts.Select(expert =>
            {
                string name = expert.Expert switch
                {
                    "qrf" => "QRF",
                    "analog" => "Analog",
                    "mixture" => "Mixture",
                    _ => expert.Expert,
                };
                string status = expert.Applied ? "APPLIED" : "SHADOW";
                string delta = expert.DeltaBrier is { } value
                    ? $"delta {value:+0.0000;-0.0000;0.0000}"
                    : "delta collecting";
                string ci = expert.CiLow is { } low && expert.CiHigh is { } high
                    ? $"95% [{low:+0.0000;-0.0000;0.0000}, {high:+0.0000;-0.0000;0.0000}]"
                    : "95% collecting";
                return $"{name} {status}: {expert.CurrentMarkets} markets, "
                    + $"train {expert.TrainingSampleN}, "
                    + $"max {expert.MaximumBlocks}/{nonlinear.MinimumBlocks} blocks, "
                    + $"{expert.EligibleSlices}/{expert.EvaluatedSlices} eligible, {delta}, {ci}";
            }));
        AccuracyNonlinearText.Text =
            $"Nonlinear distribution models · minimum training {nonlinear.MinimumTrainingSamples} · "
            + nonlinearDetails;
        AccuracyNonlinearText.Foreground = AccentBrush(
            nonlinear.Experts.Any(expert => expert.Applied) ? "positive"
            : nonlinear.Experts.Any(expert => expert.MaximumBlocks >= nonlinear.MinimumBlocks)
                ? "negative"
                : "warning");

        AccuracyModelItems.ItemsSource = accuracy.Models
            .OrderByDescending(item => item.Enabled)
            .ThenBy(item => item.Provider)
            .ThenBy(item => item.Model)
            .Take(16)
            .Select(item => new MonitoringListItem(
                $"{item.Provider.ToUpperInvariant()} · {item.Model}",
                $"{item.Region} · {item.ModelVersion}",
                item.ExpectedLatencyMinutes is { } latency
                    ? $"expected availability +{latency} min · {item.LicenseStatus}"
                    : item.LicenseStatus,
                item.SourceUrl,
                item.Enabled ? "Active" : item.ShadowOnly ? "Shadow" : "Disabled",
                item.Enabled ? "positive" : "neutral"))
            .ToList();

        AccuracyDriftItems.ItemsSource = accuracy.Drift.Count == 0
            ? new List<MonitoringListItem>
            {
                new(
                    "No recent structural drift",
                    "30/60/90/180-day monitors are quiet",
                    "A new event appears here when recent scoring degrades against its baseline.",
                    string.Empty,
                    "Clear",
                    "positive"),
            }
            : accuracy.Drift.Take(8)
                .Select(item => new MonitoringListItem(
                    $"{Pretty(item.Metric)} · station #{item.StationId}",
                    $"{item.WindowDays}-day window · {item.DetectedAt.LocalDateTime:g}",
                    $"{item.BaselineValue:0.0000} → {item.RecentValue:0.0000} · Δ {item.Delta:+0.0000;-0.0000;0.0000}",
                    string.Empty,
                    Pretty(item.Severity),
                    item.Severity.Equals("critical", StringComparison.OrdinalIgnoreCase)
                        ? "negative" : "warning"))
                .ToList();
    }

    private void RenderSelectionPolicy(SelectionPolicyReportDto report)
    {
        string currentScope = EvidenceScope(report.BenchmarkProtocol, report.SampleScope);
        string historicalScope = EvidenceScope(
            report.HistoricalBenchmarkProtocol,
            report.HistoricalSampleScope);
        string historicalSlope = report.HistoricalEdgeSlope is { } slope
            ? $"legacy edge slope {slope:+0.000;-0.000;0.000}"
            : "legacy edge slope unavailable";
        string historicalBrier = report.HistoricalModelMinusMarket is { } delta
            ? $"legacy Brier delta {delta:+0.000;-0.000;0.000}"
            : "legacy Brier unavailable";
        SelectionPolicySummaryText.Text =
            $"{historicalScope}: {report.HistoricalOpportunities} audit-only · "
            + $"{historicalSlope} · {historicalBrier} · {currentScope}: "
            + $"{report.CurrentOpportunities}/100 resolved · "
            + $"{report.RecordedOpportunities} decisions fixed pre-outcome · "
            + (report.PipelineVersion ?? "pipeline unavailable");
        SelectionPolicySummaryText.Foreground = AccentBrush(
            report.HistoricalEdgeSlope is < 0 ? "negative" : null);

        SelectionPolicyItems.ItemsSource = report.Candidates
            .Select(item =>
            {
                bool baseline = item.IsReference;
                string criterion = item.Description;
                string interval = item.DeltaCiLow is { } ciLow
                    && item.DeltaCiHigh is { } ciHigh
                        ? $"Δ-info 95% [{ciLow:+0.0000;-0.0000;0.0000}, "
                            + $"{ciHigh:+0.0000;-0.0000;0.0000}]"
                        : "Δ-info CI collecting";
                string brier = item.BrierModel is { } model
                    && item.BrierMarket is { } market
                        ? $"Brier {model:0.000} vs {market:0.000}"
                        : "Brier collecting";
                string status = baseline ? "Reference"
                    : item.Eligible ? "Qualified shadow"
                    : item.Opportunities < 100 || item.IndependentBlocks < 60
                        ? "Collecting"
                        : "Not qualified";
                string accent = baseline ? "neutral"
                    : item.Eligible ? "positive"
                    : item.Opportunities >= 100 ? "negative"
                    : "warning";
                return new MonitoringListItem(
                    baseline ? "Baseline execution" : Pretty(item.Name),
                    $"{criterion} · retained {item.Accepted}/{item.Opportunities} "
                        + $"({Percent(item.Retention)}) · {item.IndependentBlocks}/60 blocks",
                    $"{brier} · information Δ {item.DeltaInformationValue:+0.0000;-0.0000;0.0000} "
                        + $"· {interval} · realised ROI {Percent(item.AvgRealizedReturn)}",
                    string.Empty,
                    status,
                    accent);
            })
            .ToList();
    }

    private void RenderRootCauseDiagnostics(
        BrierReportDto brier,
        ClvReportDto clv,
        RootCauseActionsDto actions)
    {
        string brierScope = EvidenceScope(brier.BenchmarkProtocol, brier.SampleScope);
        string clvScope = EvidenceScope(clv.BenchmarkProtocol, clv.SampleScope);
        bool prospectiveDual = IsProspectiveDual(brier.BenchmarkProtocol, brier.SampleScope)
            && IsProspectiveDual(clv.BenchmarkProtocol, clv.SampleScope);
        bool modelDrag = brier.ModelMinusMarket is > 0;
        bool executionDrag = clv.AvgClv is < 0;

        RootCauseBadgeText.Text = (modelDrag, executionDrag) switch
        {
            (true, true) => "MODEL + EXECUTION",
            (true, false) => "MODEL DRAG",
            (false, true) => "EXECUTION DRAG",
            _ => "WATCH",
        };
        RootCauseBadge.Background = AccentBrush(modelDrag || executionDrag ? "negative" : "positive");
        RootCauseScopeText.Text = prospectiveDual
            ? "Prospective dual-token · legacy rows excluded from Brier, CLV, and actions"
            : $"Brier: {brierScope} · CLV: {clvScope}";
        RootCauseScopeText.Foreground = AccentBrush(prospectiveDual ? "positive" : "warning");
        RootCauseSubtitle.Text =
            $"{brier.ResolvedTrades} prospective resolved trade(s) · "
            + $"{clv.ClosedPositions} prospective closed position(s). "
            + "Negative CLV means bad entry; positive Brier delta means model worse than market.";

        BrierDiagnosticSummary.Text =
            brier.BrierModel is { } model && brier.BrierMarket is { } market
                ? $"{brierScope} · Brier {Number(model)} vs market {Number(market)} · delta {DeltaNumber(brier.ModelMinusMarket)}"
                    + (brier.BrierRawModel is { } raw
                        ? $" · raw {Number(raw)} on {brier.RawCount} trade(s)"
                        : " · raw model unavailable")
                : $"{brierScope} is collecting. Legacy resolved trades are intentionally excluded.";
        Dictionary<string, RootCauseActionDto> actionLookup = actions.Items
            .ToDictionary(ActionKey, StringComparer.OrdinalIgnoreCase);
        BrierDiagnosticItems.ItemsSource = BuildBrierDiagnosticItems(brier, actionLookup);

        ClvDiagnosticSummary.Text =
            clv.AvgClv is { } avg
                ? $"{clvScope} · average CLV {Cents(avg)} · beat rate {Percent(clv.ClvBeatRate)} · lower rows are worse entries"
                : $"{clvScope} is collecting. Legacy closing lines are intentionally excluded.";
        ClvDiagnosticItems.ItemsSource = BuildClvDiagnosticItems(clv, actionLookup);
    }

    private static List<DiagnosticBarItem> BuildBrierDiagnosticItems(
        BrierReportDto report,
        IReadOnlyDictionary<string, RootCauseActionDto> actions)
    {
        List<BrierSliceDto> slices = report.Slices
            .OrderByDescending(slice => slice.ModelMinusMarket)
            .ThenByDescending(slice => slice.Count)
            .Take(5)
            .ToList();
        if (slices.Count == 0)
        {
            return
            [
                new DiagnosticBarItem(
                    "No Brier slices yet",
                    "Waiting for resolved trades",
                    "This panel will show station, horizon, side, confidence, and market-kind root cause.",
                    "Idle",
                    null,
                    0,
                    "brier",
                    string.Empty,
                    string.Empty),
            ];
        }

        double maxMagnitude = slices.Max(slice => Math.Abs(slice.ModelMinusMarket));
        return slices
            .Select(slice =>
            {
                RootCauseActionDto action =
                    RootCauseAction("brier", slice.Dimension, slice.Value, actions);
                return new DiagnosticBarItem(
                    $"{Pretty(slice.Dimension)} · {slice.Value}",
                    $"delta {DeltaNumber(slice.ModelMinusMarket)} · n={slice.Count} · edge {Cents(slice.AvgPredictedEdge)}",
                    $"model {Number(slice.BrierModel)} vs market {Number(slice.BrierMarket)} · "
                        + $"return {Percent(slice.AvgRealizedReturn)} · win {Percent(slice.WinRate)}",
                    action.Action,
                    action.Severity,
                    DiagnosticMagnitude(slice.ModelMinusMarket, maxMagnitude),
                    "brier",
                    slice.Dimension,
                    slice.Value,
                    action.Detail);
            })
            .ToList();
    }

    private static List<DiagnosticBarItem> BuildClvDiagnosticItems(
        ClvReportDto report,
        IReadOnlyDictionary<string, RootCauseActionDto> actions)
    {
        List<ClvSliceDto> slices = report.Slices
            .OrderBy(slice => slice.AvgClv)
            .ThenByDescending(slice => slice.Count)
            .Take(5)
            .ToList();
        if (slices.Count == 0)
        {
            return
            [
                new DiagnosticBarItem(
                    "No CLV slices yet",
                    "Waiting for closed positions",
                    "This panel will show where entries are worse than the closing line.",
                    "Idle",
                    null,
                    0,
                    "clv",
                    string.Empty,
                    string.Empty),
            ];
        }

        double maxMagnitude = slices.Max(slice => Math.Abs(slice.AvgClv));
        return slices
            .Select(slice =>
            {
                RootCauseActionDto action =
                    RootCauseAction("clv", slice.Dimension, slice.Value, actions);
                return new DiagnosticBarItem(
                    $"{Pretty(slice.Dimension)} · {slice.Value}",
                    $"CLV {Cents(slice.AvgClv)} · beat {Percent(slice.ClvBeatRate)} · n={slice.Count}",
                    $"entry {Percent(slice.AvgEntryPrice)} · fee {Usd(slice.AvgFeeUsd)} · "
                        + $"return {Percent(slice.AvgRealizedReturn)} · win {Percent(slice.WinRate)}",
                    action.Action,
                    action.Severity,
                    DiagnosticMagnitude(slice.AvgClv, maxMagnitude),
                    "clv",
                    slice.Dimension,
                    slice.Value,
                    action.Detail);
            })
            .ToList();
    }

    private static RootCauseActionDto RootCauseAction(
        string metric,
        string dimension,
        string value,
        IReadOnlyDictionary<string, RootCauseActionDto> actions) =>
        actions.TryGetValue(ActionKey(metric, dimension, value), out RootCauseActionDto? action)
            ? action
            : new RootCauseActionDto(
                metric,
                dimension,
                value,
                0,
                "Watch",
                "warning",
                "Action: backend recommendation unavailable; monitor this slice.");

    private static string ActionKey(RootCauseActionDto action) =>
        ActionKey(action.Metric, action.Dimension, action.Value);

    private static string ActionKey(string metric, string dimension, string value) =>
        $"{metric}:{dimension}:{value}";

    private static double DiagnosticMagnitude(double value, double maxMagnitude)
    {
        if (maxMagnitude <= 0 || double.IsNaN(maxMagnitude) || double.IsInfinity(maxMagnitude))
        {
            return 0;
        }

        return Math.Clamp(Math.Abs(value) / maxMagnitude * 100, 4, 100);
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
                $"unrealized {Signed(position.UnrealizedPnlUsd)} · liquidation {Signed(position.LiquidationPnlUsd)}"
                    + $" · Δ5m {Signed(position.PnlChange5mUsd)}",
                position.MarkAt is null
                    ? position.PolymarketId
                    : $"mark {RelativeAge(position.MarkAt.Value)} ago · {position.PolymarketId}",
                position.MarkStale ? "Stale" : "Open",
                PnlAccent(position.UnrealizedPnlUsd),
                position.MarketId));
        return signalItems.Concat(positionItems).ToList();
    }

    private static List<MonitoringListItem> BuildCockpitActivityFeed(
        OverviewResponse overview,
        IReadOnlyList<SignalDto> signals,
        IReadOnlyList<PositionDto> positions,
        IReadOnlyList<CommandItem> commands,
        IReadOnlyList<JobDto> jobs)
    {
        List<(DateTimeOffset Timestamp, MonitoringListItem Item)> feed = [];

        feed.Add((
            overview.GeneratedAt,
            new MonitoringListItem(
                "System snapshot",
                $"Mode {overview.Runtime.Mode.ToUpperInvariant()} · {overview.ActiveMarkets} active market(s)",
                overview.Runtime.KillSwitch
                    ? $"Kill switch active{ReasonSuffix(overview.Runtime.HaltedReason)}"
                    : $"{overview.OpenPositions} open position(s) · {overview.PendingSignals} pending signal(s)",
                LocalTime(overview.GeneratedAt),
                overview.Runtime.KillSwitch ? "STOP" : "OK",
                overview.Runtime.KillSwitch ? "negative" : "positive")));

        if (overview.HealthAudit is not null)
        {
            bool healthy = string.Equals(
                overview.HealthAudit.Status,
                "healthy",
                StringComparison.OrdinalIgnoreCase);
            feed.Add((
                overview.HealthAudit.Timestamp,
                new MonitoringListItem(
                    "Health audit",
                    Pretty(overview.HealthAudit.Status),
                    overview.HealthAudit.Issues.Count == 0
                        ? "No recorded issues"
                        : $"{overview.HealthAudit.Issues.Count} issue(s) need review",
                    LocalTime(overview.HealthAudit.Timestamp),
                    healthy ? "OK" : "Review",
                    healthy ? "positive" : "warning")));
        }

        feed.AddRange(commands
            .OrderByDescending(command => command.CreatedAt)
            .Take(8)
            .Select(command => (
                command.ExecutedAt ?? command.CreatedAt,
                new MonitoringListItem(
                    $"Command · {Pretty(command.Command)}",
                    Pretty(command.Status),
                    command.Detail ?? command.Reason,
                    $"{RelativeAge(command.CreatedAt)} ago · {command.Actor} · {command.RequestId}",
                    Pretty(command.Status),
                    CommandAccent(command)))));

        feed.AddRange(jobs
            .OrderByDescending(job => job.FinishedAt)
            .Take(10)
            .Select(job =>
            {
                double duration = JobDurationSeconds(job);
                bool failed = !IsJobSuccess(job);
                bool slow = IsJobSlow(job);
                return (
                    job.FinishedAt,
                    new MonitoringListItem(
                        $"Job · {job.JobId}",
                        $"{Pretty(job.Status)} · {duration:0.00}s",
                        job.Error ?? (slow ? "Slow scheduler run" : "Completed without recorded error"),
                        $"{RelativeAge(job.FinishedAt)} ago · scheduled {LocalTime(job.ScheduledAt)}",
                        failed ? "Failed" : slow ? "Slow" : "OK",
                        JobAccent(job)));
            }));

        feed.AddRange(signals
            .OrderByDescending(signal => signal.Timestamp)
            .Take(10)
            .Select(signal => (
                signal.Timestamp,
                new MonitoringListItem(
                    $"Signal · {signal.StationIcao ?? "Unmapped"} · {Pretty(signal.Side)}",
                    $"edge {Percent(signal.Edge)} · model {Percent(signal.ModelProbability)} vs market {Percent(signal.MarketPrice)}",
                    signal.SkipReason is null
                        ? $"size {Usd(signal.SizeUsd)} · confidence {signal.Confidence}"
                        : HumanizeReasons(signal.SkipReason),
                    $"{RelativeAge(signal.Timestamp)} ago · {signal.PolymarketId}",
                    Pretty(signal.Status),
                    IsActionableSignal(signal) ? PnlAccent(signal.Edge) : null,
                    signal.MarketId))));

        feed.AddRange(positions
            .Where(position => position.ClosedAt is not null
                || string.Equals(position.Status, "open", StringComparison.OrdinalIgnoreCase))
            .OrderByDescending(position => position.ClosedAt ?? DateTimeOffset.UtcNow)
            .Take(10)
            .Select(position =>
            {
                bool open = string.Equals(position.Status, "open", StringComparison.OrdinalIgnoreCase);
                double? pnl = open ? position.UnrealizedPnlUsd : position.PnlUsd;
                return (
                    position.ClosedAt ?? DateTimeOffset.UtcNow,
                    new MonitoringListItem(
                        $"{(open ? "Open position" : "Closed position")} · {position.StationIcao ?? "Unmapped"}",
                        $"{Pretty(position.Side)} · entry {Percent(position.AveragePrice)}"
                            + (position.MarkPrice is null ? string.Empty : $" → mark {Percent(position.MarkPrice)}"),
                        $"{(open ? "unrealized" : "realized")} {Signed(pnl)} · size {Usd(position.SizeUsd)}",
                        open
                            ? $"{position.MarketDate:yyyy-MM-dd} · {position.PolymarketId}"
                            : $"{RelativeAge(position.ClosedAt!.Value)} ago · {position.PolymarketId}",
                        open ? "Open" : Signed(pnl),
                        PnlAccent(pnl),
                        position.MarketId));
            }));

        return feed
            .OrderByDescending(entry => entry.Timestamp)
            .Take(24)
            .Select(entry => entry.Item)
            .ToList();
    }

    private static string? CommandAccent(CommandItem command)
    {
        string status = command.Status;
        if (status.Contains("fail", StringComparison.OrdinalIgnoreCase)
            || status.Contains("reject", StringComparison.OrdinalIgnoreCase)
            || status.Contains("error", StringComparison.OrdinalIgnoreCase))
        {
            return "negative";
        }

        if (status.Contains("pending", StringComparison.OrdinalIgnoreCase)
            || status.Contains("queued", StringComparison.OrdinalIgnoreCase)
            || status.Contains("requested", StringComparison.OrdinalIgnoreCase))
        {
            return "warning";
        }

        return "positive";
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

    private async void CockpitActivityList_ItemClick(object sender, ItemClickEventArgs e)
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

    private async void RootCauseDiagnosticItems_ItemClick(object sender, ItemClickEventArgs e)
    {
        if (e.ClickedItem is DiagnosticBarItem item
            && !string.IsNullOrWhiteSpace(item.Metric)
            && !string.IsNullOrWhiteSpace(item.Dimension)
            && !string.IsNullOrWhiteSpace(item.Value))
        {
            await LoadRootCauseDrilldownAsync(item);
        }
    }

    private async Task LoadRootCauseDrilldownAsync(DiagnosticBarItem item)
    {
        RootCauseDrilldownPanel.Visibility = Visibility.Visible;
        RootCauseDrilldownTitle.Text = $"Loading {item.Title}...";
        RootCauseDrilldownSubtitle.Text = "Fetching the concrete resolved trades behind this diagnostic slice.";
        RootCauseDrilldownCount.Text = "...";
        RootCauseDrilldownMetricLabel.Text = item.Metric == "brier" ? "Average Brier drag" : "Average CLV";
        RootCauseDrilldownMetric.Text = "...";
        RootCauseDrilldownPnl.Text = "...";
        RootCauseDrilldownWinRate.Text = "...";
        RootCauseTradeList.ItemsSource = null;
        CockpitDetailStatusTag.Text = "Loading";
        CockpitDetailStatusBadge.Background = AccentBrush(item.Accent);
        CockpitDetailTitle.Text = $"Root cause · {item.Title}";
        CockpitDetailPrimary.Text = $"{item.Status}: {item.ActionDetail}";
        CockpitDetailSecondary.Text = "Loading concrete trades behind this slice…";
        CockpitDetailMeta.Text = $"{item.Subtitle} · {item.Detail}";
        CockpitDetailHint.Text = "Diagnostic slice: concrete trades and positions behind the selected Brier/CLV bar.";

        try
        {
            RootCauseDrilldownDto drilldown =
                await _apiClient.GetRootCauseDrilldownAsync(
                    "paper",
                    item.Metric,
                    item.Dimension,
                    item.Value,
                    _lifetime.Token);
            RenderRootCauseDrilldown(item, drilldown);
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception ex)
        {
            RootCauseDrilldownTitle.Text = "Diagnostic trades unavailable";
            RootCauseDrilldownSubtitle.Text = ex.Message;
            RootCauseDrilldownCount.Text = "—";
            RootCauseDrilldownMetric.Text = "—";
            RootCauseDrilldownPnl.Text = "—";
            RootCauseDrilldownWinRate.Text = "—";
            CockpitDetailStatusTag.Text = "Unavailable";
            CockpitDetailStatusBadge.Background = AccentBrush("warning");
            CockpitDetailSecondary.Text = $"Root-cause drill-down unavailable: {ex.Message}";
        }
    }

    private void RenderRootCauseDrilldown(
        DiagnosticBarItem slice,
        RootCauseDrilldownDto drilldown)
    {
        CockpitDetailStatusTag.Text =
            $"{drilldown.Metric.ToUpperInvariant()} · {drilldown.Count}";
        CockpitDetailStatusBadge.Background = AccentBrush(slice.Accent);
        CockpitDetailTitle.Text =
            $"{Pretty(drilldown.Dimension)} · {drilldown.Value}";
        CockpitDetailPrimary.Text = $"{slice.Status}: {slice.ActionDetail}";
        CockpitDetailSecondary.Text = drilldown.Items.Count == 0
            ? "No concrete rows matched this slice."
            : $"{drilldown.Items.Count} source trade(s) loaded below. Select a row to inspect its market chart.";
        CockpitDetailMeta.Text =
            $"{slice.Subtitle} · {slice.Detail} · "
            + $"showing {drilldown.Items.Count}/{drilldown.Count} row(s) · "
            + $"sorted by {(drilldown.Metric == "brier" ? "Brier drag" : "worst CLV")} · "
            + $"generated {LocalTime(drilldown.GeneratedAt)}";
        CockpitDetailHint.Text =
            "Selected diagnostic slice. Choose a concrete row in the table to switch the chart to that market.";

        IReadOnlyList<RootCauseTradeListItem> rows = BuildRootCauseTradeItems(drilldown);
        RootCauseDrilldownPanel.Visibility = Visibility.Visible;
        RootCauseDrilldownTitle.Text = $"{Pretty(drilldown.Dimension)} · {drilldown.Value}";
        RootCauseDrilldownSubtitle.Text =
            $"{slice.Status}: {slice.ActionDetail} · click any trade to open its market chart";
        RootCauseDrilldownCount.Text = $"{drilldown.Items.Count} / {drilldown.Count}";
        RootCauseDrilldownMetricLabel.Text =
            drilldown.Metric == "brier" ? "Average Brier drag" : "Average CLV";
        double? metric = drilldown.Metric == "brier"
            ? AverageOrNull(drilldown.Items.Select(row => row.BrierDelta))
            : AverageOrNull(drilldown.Items.Select(row => row.Clv));
        RootCauseDrilldownMetric.Text = drilldown.Metric == "brier"
            ? DeltaNumber(metric)
            : Cents(metric);
        RootCauseDrilldownMetric.Foreground = AccentBrush(
            metric is null ? null
            : metric is > 0 && drilldown.Metric == "brier" ? "negative"
            : metric is < 0 && drilldown.Metric == "clv" ? "negative"
            : "positive");
        double pnl = drilldown.Items.Sum(row => row.PnlUsd);
        RootCauseDrilldownPnl.Text = Signed(pnl);
        RootCauseDrilldownPnl.Foreground = AccentBrush(PnlAccent(pnl));
        RootCauseDrilldownWinRate.Text = drilldown.Items.Count == 0
            ? "—"
            : Percent((double)drilldown.Items.Count(row => row.Won) / drilldown.Items.Count);
        RootCauseTradeList.ItemsSource = rows;
    }

    private static IReadOnlyList<RootCauseTradeListItem> BuildRootCauseTradeItems(
        RootCauseDrilldownDto drilldown)
    {
        return drilldown.Items
            .Select(row => new RootCauseTradeListItem(
                $"{row.StationIcao ?? "Unmapped"} · {row.MarketDate:MM-dd} · {Pretty(row.Kind)}",
                $"{Pretty(row.Side)} · {row.Confidence ?? "—"} · {row.HorizonBucket}",
                drilldown.Metric == "brier"
                    ? $"Δ {DeltaNumber(row.BrierDelta)} · {Number(row.BrierModelError)} vs {Number(row.BrierMarketError)}"
                    : $"p {Percent(row.ModelProbability)} vs market {Percent(row.MarketProbability)}",
                $"entry {Percent(row.EntryPrice)} → close {Percent(row.ClosingLinePrice)} · CLV {Cents(row.Clv)}",
                $"{Signed(row.PnlUsd)} · cost {Usd(row.CostUsd)}",
                row.Won ? "WIN" : "LOSS",
                row.Won ? "positive" : "negative",
                row))
            .ToList();
    }

    private static double? AverageOrNull(IEnumerable<double?> values)
    {
        double[] available = values
            .Where(value => value is not null)
            .Select(value => value!.Value)
            .ToArray();
        return available.Length == 0 ? null : available.Average();
    }

    private async void RootCauseTradeList_ItemClick(object sender, ItemClickEventArgs e)
    {
        if (e.ClickedItem is not RootCauseTradeListItem item)
        {
            return;
        }

        RootCauseTradeDto trade = item.Trade;
        CockpitDetailStatusTag.Text = item.Status;
        CockpitDetailStatusBadge.Background = AccentBrush(item.Accent);
        CockpitDetailTitle.Text = item.Market;
        CockpitDetailPrimary.Text = $"{item.Setup} · {item.Outcome}";
        CockpitDetailSecondary.Text = $"{item.Model} · {item.Execution}";
        CockpitDetailMeta.Text =
            $"source #{trade.Id} · market #{trade.MarketId} · {trade.PolymarketId}"
            + (trade.ClosedAt is null ? string.Empty : $" · closed {LocalTime(trade.ClosedAt.Value)}");
        CockpitDetailHint.Text =
            "Concrete diagnostic trade selected. The chart now shows this market's price and probability history.";

        await LoadCockpitMarketChartAsync(new MonitoringListItem(
            item.Market,
            item.Setup,
            item.Execution,
            trade.PolymarketId,
            item.Status,
            item.Accent,
            trade.MarketId));
    }

    private void RootCauseDrilldownClose_Click(object sender, RoutedEventArgs e)
    {
        RootCauseDrilldownPanel.Visibility = Visibility.Collapsed;
        RootCauseTradeList.SelectedItem = null;
    }

    private void CockpitActivityFilterBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        ApplyCockpitActivityFilters();
    }

    private void CockpitActivitySearchBox_TextChanged(object sender, TextChangedEventArgs e)
    {
        _cockpitActivitySearchQuery = CockpitActivitySearchBox.Text.Trim();
        ApplyCockpitActivityFilters();
    }

    private void ApplyCockpitActivityFilters()
    {
        if (CockpitActivityList is null
            || CockpitActivitySubtitle is null
            || CockpitActivityFilterBox is null)
        {
            return;
        }

        string category =
            (CockpitActivityFilterBox.SelectedItem as ComboBoxItem)?.Content as string ?? "All";
        IReadOnlyList<MonitoringListItem> source = _cockpitActivityItems;
        List<MonitoringListItem> visible = source
            .Where(item => MatchesActivityCategory(item, category))
            .Where(item => MatchesActivitySearch(item, _cockpitActivitySearchQuery))
            .ToList();

        CockpitActivityList.ItemsSource = visible;
        CockpitActivitySubtitle.Text =
            $"{visible.Count}/{source.Count} event(s) shown"
            + (category == "All" ? string.Empty : $" · {category.ToLowerInvariant()}")
            + (string.IsNullOrWhiteSpace(_cockpitActivitySearchQuery)
                ? string.Empty
                : $" · \"{_cockpitActivitySearchQuery}\"")
            + " · click signal/position rows for market overlay";
    }

    private static bool MatchesActivityCategory(MonitoringListItem item, string category) =>
        category switch
        {
            "Issues" => IsActivityIssue(item),
            "Risk" => StartsWithActivityType(item, "System")
                || StartsWithActivityType(item, "Health")
                || IsActivityIssue(item),
            "Commands" => StartsWithActivityType(item, "Command"),
            "Jobs" => StartsWithActivityType(item, "Job"),
            "Signals" => StartsWithActivityType(item, "Signal"),
            "Positions" => item.Title.Contains("position", StringComparison.OrdinalIgnoreCase),
            _ => true,
        };

    private static bool StartsWithActivityType(MonitoringListItem item, string type) =>
        item.Title.StartsWith(type, StringComparison.OrdinalIgnoreCase);

    private static bool IsActivityIssue(MonitoringListItem item) =>
        item.Accent is "negative" or "warning"
        || item.Status.Contains("fail", StringComparison.OrdinalIgnoreCase)
        || item.Status.Contains("slow", StringComparison.OrdinalIgnoreCase)
        || item.Status.Contains("review", StringComparison.OrdinalIgnoreCase)
        || item.Status.Contains("stop", StringComparison.OrdinalIgnoreCase);

    private static bool MatchesActivitySearch(MonitoringListItem item, string query) =>
        string.IsNullOrWhiteSpace(query)
        || ContainsSearch(item.Title, query)
        || ContainsSearch(item.Subtitle, query)
        || ContainsSearch(item.Detail, query)
        || ContainsSearch(item.Meta, query)
        || ContainsSearch(item.Status, query)
        || ContainsSearch(item.Accent, query)
        || ContainsSearch(item.MarketId?.ToString(CultureInfo.InvariantCulture), query);

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
            _cockpitHoverPoint = null;
            CockpitDetailMeta.Text +=
                $" · chart prices: {EvidenceScope(prices.BenchmarkProtocol, prices.SampleScope)}"
                + $" · probabilities: {EvidenceScope(probabilities.BenchmarkProtocol, probabilities.SampleScope)}";
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
        _drilldownHoverPoint = null;
        _drilldownBaseMetaText = "Chart and orderbook will load after selection";
    }

    private async Task LoadMarketDrilldownAsync(MonitoringListItem item)
    {
        if (item.MarketId is null)
        {
            HideMarketDrilldown();
            return;
        }

        _selectedDrilldownItem = item;
        _drilldownHoverPoint = null;
        CollectionDetailColumn.Width = new GridLength(460);
        MarketDrilldownPanel.Visibility = Visibility.Visible;
        DrilldownTitle.Text = item.Title;
        DrilldownSummaryText.Text = "Loading market series and orderbook...";
        _drilldownBaseMetaText = item.Meta;
        DrilldownMetaText.Text = _drilldownBaseMetaText;
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
            _drilldownBaseMetaText =
                $"{_drilldownPrices.Count} price point(s) · {_drilldownProbabilities.Count} probability/signal point(s)"
                + $" · prices {EvidenceScope(pricesTask.Result.BenchmarkProtocol, pricesTask.Result.SampleScope)}"
                + $" · probabilities {EvidenceScope(probabilitiesTask.Result.BenchmarkProtocol, probabilitiesTask.Result.SampleScope)}"
                + $" · book {_drilldownOrderBook.SourceStatus}";
            if (!string.IsNullOrWhiteSpace(_drilldownOrderBook.Error))
            {
                _drilldownBaseMetaText += $" · {_drilldownOrderBook.Error}";
            }
            DrilldownMetaText.Text = _drilldownBaseMetaText;
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
            _drilldownBaseMetaText = ex.Message;
            DrilldownMetaText.Text = _drilldownBaseMetaText;
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
                + (signal.SkipReason is null ? signal.PolymarketId : HumanizeReasons(signal.SkipReason))
                + EvidenceScopeSuffix(signal.BenchmarkProtocol, signal.SampleScope),
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
                    ? $"Unrealized {Signed(position.UnrealizedPnlUsd)} · liquidation {Signed(position.LiquidationPnlUsd)}"
                    : $"P&L {Signed(position.PnlUsd)} · CLV {Percent(position.Clv)}")
                    + $" · fees {Usd(position.FeeUsd)}",
                (position.ClosedAt is null
                    ? $"Δ1m {Signed(position.PnlChange1mUsd)} · Δ5m {Signed(position.PnlChange5mUsd)}"
                        + $" · Δ60m {Signed(position.PnlChange60mUsd)}"
                        + (position.MarkAt is null
                            ? string.Empty
                            : $" · mark {RelativeAge(position.MarkAt.Value)} ago")
                        + (position.MarkStale ? " · STALE" : string.Empty)
                    : $"Closed {LocalTime(position.ClosedAt.Value)} · {position.PolymarketId}")
                    + EvidenceScopeSuffix(position.BenchmarkProtocol, position.SampleScope),
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

    private void RenderCalibrations(
        IReadOnlyList<CalibrationDto> calibrations,
        StationLifecycleResponse? stationPool,
        string? stationPoolError)
    {
        List<CalibrationDto> ordered = calibrations
            .OrderBy(calibration => calibration.StationIcao)
            .ThenBy(calibration => calibration.HorizonBucket)
            .ThenBy(calibration => calibration.Kind)
            .ToList();
        List<CalibrationDto> filtered = FilterCalibrations(ordered).ToList();
        _calibrationLabItems = filtered;

        StationLifecycleResponse effectiveStationPool = stationPool
            ?? new StationLifecycleResponse(
                DateTimeOffset.UtcNow,
                PaperStations: 0,
                ShadowStations: 0,
                ApprovedStations: 0,
                InactiveStations: 0,
                Items: []);

        SetCollection(filtered.Select(calibration => new MonitoringListItem(
            $"{calibration.StationIcao} · {calibration.Model.ToUpperInvariant()} · {Pretty(calibration.Kind)}",
            $"Horizon {calibration.HorizonBucket} · n={calibration.SampleCount}",
            $"Bias {calibration.BiasC:+0.00;-0.00;0.00} °C · sigma {calibration.SigmaC:0.00} °C · Brier {Number(calibration.Brier)}",
            $"Updated {LocalTime(calibration.UpdatedAt)}",
            CalibrationStatus(calibration),
            CalibrationAccent(calibration))),
            _calibrationFilter == "All"
                ? "No calibration records yet"
                : "No calibration records match this filter");
        RenderCalibrationLab(ordered, filtered, effectiveStationPool, stationPoolError);
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
        IReadOnlyList<CalibrationDto> filtered,
        StationLifecycleResponse stationPool,
        string? stationPoolError)
    {
        CalibrationLabPanel.Visibility = Visibility.Visible;
        int mature = filtered.Count(calibration => calibration.SampleCount >= 100);
        int gateReady = filtered.Count(calibration => CalibrationStatus(calibration) == "Gate ready");
        int risk = filtered.Count(IsCalibrationRisky);
        int totalSamples = filtered.Sum(calibration => calibration.SampleCount);
        int reliableStations = stationPool.Items.Count(station =>
            station.Active
            &&
            string.Equals(station.ReliabilityStatus, "reliable", StringComparison.OrdinalIgnoreCase));
        int collectingStations = stationPool.Items.Count(station =>
            station.Active
            &&
            string.Equals(station.ReliabilityStatus, "collecting", StringComparison.OrdinalIgnoreCase));
        int blockedStations = stationPool.Items.Count(station => station.Active && station.SelectionBlocked);
        double? averageBrier = filtered
            .Where(calibration => calibration.Brier is not null)
            .Select(calibration => calibration.Brier!.Value)
            .DefaultIfEmpty(double.NaN)
            .Average();
        if (double.IsNaN(averageBrier.Value))
        {
            averageBrier = null;
        }

        CalibrationLabSummaryText.Text = stationPoolError is null
            ? $"{stationPool.PaperStations} paper · {stationPool.ShadowStations} shadow · "
                + $"{stationPool.ApprovedStations} approved · "
                + $"{stationPool.InactiveStations} archived · "
                + $"{filtered.Count}/{calibrations.Count} calibration rows shown"
            : $"Station trust degraded · {filtered.Count}/{calibrations.Count} calibration rows shown";
        CalibrationMatrixSummaryText.Text = filtered.Count == 0
            ? $"No station/horizon rows match filter {_calibrationFilter}"
            : $"{filtered.Select(calibration => calibration.StationIcao).Distinct().Count()} station(s)"
                + $" · {filtered.Select(calibration => calibration.HorizonBucket).Distinct().Count()} horizon bucket(s)"
                + $" · {totalSamples} samples · filter {_calibrationFilter}";
        CalibrationQualitySummaryText.Text =
            $"mature {mature} · gate-ready {gateReady} · review {risk} · avg Brier {Number(averageBrier)}"
            + $" · filter {_calibrationFilter}";
        CalibrationReadinessText.Text = filtered.Count == 0
            ? $"No forecast calibration rows match filter {_calibrationFilter}. Change the filter to inspect another scope."
            : $"Gate-ready rows {gateReady}/{filtered.Count} in filter {_calibrationFilter}"
                + Environment.NewLine
                + $"Mature rows {mature}/{filtered.Count} · scoped samples {totalSamples}"
                + Environment.NewLine
                + $"Shadow data-ready {stationPool.Items.Count(station => station.Stage == "shadow" && station.DataReadyForPaper)}/{stationPool.ShadowStations}"
                + Environment.NewLine
                + (stationPoolError is null
                    ? $"Station trust: {reliableStations} reliable · {collectingStations} collecting · {blockedStations} selection-blocked"
                    : "Station trust endpoint unavailable; forecast calibration remains visible.");

        CalibrationDto? worstBrier = filtered
            .Where(calibration => calibration.Brier is not null)
            .OrderByDescending(calibration => calibration.Brier)
            .FirstOrDefault();
        CalibrationDto? biggestBias = filtered
            .OrderByDescending(calibration => Math.Abs(calibration.BiasC))
            .FirstOrDefault();
        CalibrationOutlierText.Text = filtered.Count == 0
            ? "Expected here: worst Brier, largest absolute bias, and sparse station/horizon buckets."
            : string.Join(
                Environment.NewLine,
                worstBrier is null
                    ? "Worst Brier: not available"
                    : $"Worst Brier · {worstBrier.StationIcao} · {worstBrier.HorizonBucket} · {Number(worstBrier.Brier)}",
                biggestBias is null
                    ? "Largest bias: not available"
                    : $"Largest bias · {biggestBias.StationIcao} · {biggestBias.HorizonBucket} · {biggestBias.BiasC:+0.00;-0.00;0.00} °C",
                blockedStations == 0
                    ? "Selection blocks: none active"
                    : "Selection blocks · " + string.Join(
                        ", ",
                        stationPool.Items
                            .Where(station => station.Active && station.SelectionBlocked)
                            .Take(5)
                            .Select(station =>
                                $"{station.Icao}: {Pretty((station.SelectionReasons ?? []).FirstOrDefault() ?? "blocked")}")));

        RenderStationTrust(stationPool, stationPoolError);
        DrawCalibrationMatrix();
        DrawCalibrationQualityMix();
    }

    private void RenderStationTrust(
        StationLifecycleResponse stationPool,
        string? stationPoolError)
    {
        if (stationPoolError is not null)
        {
            StationTrustSummaryText.Text = "DEGRADED · station evidence unavailable";
            StationTrustList.ItemsSource = new[]
            {
                new StationTrustListItem(
                    "Station API",
                    "Forecast calibration remains available",
                    "No current Wilson snapshot",
                    "No station evidence was inferred from calibration rows.",
                    stationPoolError,
                    "Refresh after the backend endpoint is restored.",
                    "policy — · source — · record —",
                    "DEGRADED",
                    "warning"),
            };
            return;
        }

        int active = stationPool.Items.Count(station => station.Active);
        int allowed = stationPool.Items.Count(station => station.Active && !station.SelectionBlocked);
        int blocked = stationPool.Items.Count(station => station.Active && station.SelectionBlocked);
        StationTrustSummaryText.Text =
            $"{active} active · {allowed} selection-allowed · {blocked} blocked · {stationPool.InactiveStations} archived";
        StationTrustList.ItemsSource = stationPool.Items
            .OrderByDescending(station => station.Active)
            .ThenBy(station => station.SelectionBlocked)
            .ThenBy(station => station.Icao)
            .Select(StationTrustItem)
            .ToList();
    }

    private static StationTrustListItem StationTrustItem(StationLifecycleDto station)
    {
        string status = !station.Active
            ? "INACTIVE"
            : station.SelectionBlocked
                ? "BLOCKED"
                : station.ReliabilityStatus.ToUpperInvariant();
        string? accent = !station.Active
            ? null
            : station.SelectionBlocked
                ? "negative"
                : string.Equals(station.ReliabilityStatus, "reliable", StringComparison.OrdinalIgnoreCase)
                    ? "positive"
                    : "warning";
        string trust = station.ReliabilityLowerBound is null
            ? $"No Wilson estimate · n={station.ReliabilitySamples}"
            : $"LB {Percent(station.ReliabilityLowerBound)} · observed {Percent(station.ReliabilityScore)}"
                + Environment.NewLine
                + $"n={station.ReliabilitySamples} comparable";

        List<string> evidence =
        [
            $"{station.ReliabilityConflicts} conflict(s)",
            $"{station.ReliabilityBoundarySamples} boundary (excluded)",
            $"{station.ReliabilityUnavailableSamples} truth unavailable (excluded)",
        ];
        if (station.LatestConflictDate is not null)
        {
            evidence.Add(
                $"Latest conflict {station.LatestConflictDate:yyyy-MM-dd}"
                + (string.IsNullOrWhiteSpace(station.LatestConflictKind)
                    ? string.Empty
                    : $" · {station.LatestConflictKind}"));
        }
        if (station.ReliabilityReasons is { Count: > 0 })
        {
            evidence.Add("Trust reasons: " + string.Join(", ", station.ReliabilityReasons));
        }

        string selection;
        if (!station.Active)
        {
            selection = "Inactive registry row; excluded from active selection-block counts.";
        }
        else if (station.SelectionBlocked)
        {
            selection = "Blocked: " + string.Join(", ", station.SelectionReasons ?? ["unspecified"]);
        }
        else
        {
            selection = string.Equals(station.ReliabilityStatus, "collecting", StringComparison.OrdinalIgnoreCase)
                ? "PAPER collection allowed; LIVE still requires RELIABLE, approved lifecycle, and the global gate."
                : "Selection allowed for the evaluated date; lifecycle and global gates still apply.";
        }

        List<string> freshness =
        [
            station.SelectionEvaluatedForDate is null
                ? "Selection date —"
                : $"Selection date {station.SelectionEvaluatedForDate:yyyy-MM-dd}",
            station.ReliabilityEvaluatedAt is null
                ? "Trust evaluated —"
                : $"Trust evaluated {LocalTime(station.ReliabilityEvaluatedAt.Value)}",
            station.LatestObservationAt is null
                ? "Observation —"
                : $"Observation {LocalTime(station.LatestObservationAt.Value)}",
        ];
        if (station.ResolutionCooldownUntil is not null)
        {
            freshness.Add($"Resolution block until {LocalTime(station.ResolutionCooldownUntil.Value)}");
        }
        if (station.ObservationCooldownUntil is not null)
        {
            freshness.Add($"Observation block until {LocalTime(station.ObservationCooldownUntil.Value)}");
        }

        return new StationTrustListItem(
            $"{station.Icao} · {station.City}",
            $"{Pretty(station.Stage)} · {(station.Active ? "active" : "archived")}",
            trust,
            string.Join(Environment.NewLine, evidence),
            selection,
            string.Join(Environment.NewLine, freshness),
            $"policy {ShortEvidenceHash(station.ReliabilityPolicyHash)} · "
                + $"source {ShortEvidenceHash(station.ReliabilitySourceHash)} · "
                + $"record {ShortEvidenceHash(station.ReliabilityRecordHash)}",
            status,
            accent);
    }

    private static string ShortEvidenceHash(string? value) =>
        string.IsNullOrWhiteSpace(value)
            ? "—"
            : value[..Math.Min(10, value.Length)];

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
        if (_calibrationLabItems.Count == 0)
        {
            CalibrationMatrixCanvas.Height = 220;
            double emptyWidth = CalibrationMatrixCanvas.ActualWidth;
            if (emptyWidth <= 1)
            {
                return;
            }
            AddCanvasText(
                CalibrationMatrixCanvas,
                "No forecast calibration evidence in this filter",
                18,
                20,
                new SolidColorBrush(Color.FromArgb(0xDD, 0xFF, 0xFF, 0xFF)),
                18,
                new FontWeight { Weight = 600 });
            AddCanvasText(
                CalibrationMatrixCanvas,
                "Change the page filter or wait for resolved markets to populate station-by-horizon Brier evidence.",
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

        double desiredHeight = Math.Max(220, 38 + stations.Length * 31);
        if (Math.Abs(CalibrationMatrixCanvas.Height - desiredHeight) > 0.5)
        {
            CalibrationMatrixCanvas.Height = desiredHeight;
        }
        double width = CalibrationMatrixCanvas.ActualWidth;
        double height = desiredHeight;
        if (width <= 1)
        {
            return;
        }

        double left = 76;
        double top = 28;
        double gap = 5;
        double cellWidth = Math.Max(34, (width - left - 6 - gap * (horizons.Length - 1)) / horizons.Length);
        double cellHeight = Math.Max(24, (height - top - 6 - gap * (stations.Length - 1)) / stations.Length);
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

    private string _jobsFilter = "All";

    private async void JobsFilterBox_SelectionChanged(
        object sender,
        SelectionChangedEventArgs e)
    {
        if (!IsLoaded)
        {
            return;
        }

        _jobsFilter =
            (JobsFilterBox.SelectedItem as ComboBoxItem)?.Content as string ?? "All";
        await RefreshAsync();
    }

    private void RenderJobs(IReadOnlyList<JobDto> jobs)
    {
        List<JobDto> ordered = jobs
            .OrderByDescending(job => job.FinishedAt)
            .ToList();
        List<JobDto> filtered = FilterJobs(ordered).ToList();
        _jobsBoardItems = ordered;

        SetCollection(filtered.Select(job => new MonitoringListItem(
            job.JobId,
            $"Scheduled {LocalTime(job.ScheduledAt)}",
            $"Duration {JobDurationSeconds(job):0.0}s{ReasonSuffix(job.Error)}",
            $"Finished {LocalTime(job.FinishedAt)} · {RelativeAge(job.FinishedAt)} ago",
            Pretty(job.Status),
            JobAccent(job))),
            _jobsFilter == "All"
                ? "No job runs yet"
                : "No job runs match this filter");
        RenderJobsBoard(ordered, filtered);
    }

    private IEnumerable<JobDto> FilterJobs(IReadOnlyList<JobDto> jobs) =>
        _jobsFilter switch
        {
            "Failures" => jobs.Where(job => !IsJobSuccess(job)),
            "Slow" => jobs.Where(IsJobSlow),
            "Prices" => jobs.Where(job => job.JobId.Contains("price", StringComparison.OrdinalIgnoreCase)
                || job.JobId.Contains("signal", StringComparison.OrdinalIgnoreCase)),
            "Execution" => jobs.Where(job => job.JobId.Contains("execution", StringComparison.OrdinalIgnoreCase)),
            _ => jobs,
        };

    private static bool IsJobSuccess(JobDto job) =>
        job.Status.Equals("success", StringComparison.OrdinalIgnoreCase)
        || job.Status.Equals("succeeded", StringComparison.OrdinalIgnoreCase);

    private static double JobDurationSeconds(JobDto job) =>
        Math.Max(0, (job.FinishedAt - job.ScheduledAt).TotalSeconds);

    private static bool IsJobSlow(JobDto job) => JobDurationSeconds(job) > 1.0;

    private static string? JobAccent(JobDto job) =>
        !IsJobSuccess(job)
            ? "negative"
            : IsJobSlow(job)
                ? null
                : "positive";

    private static string RelativeAge(DateTimeOffset value)
    {
        TimeSpan age = DateTimeOffset.Now - value.ToLocalTime();
        if (age.TotalMinutes < 1)
        {
            return "<1m";
        }

        if (age.TotalHours < 1)
        {
            return $"{age.TotalMinutes:0}m";
        }

        if (age.TotalDays < 1)
        {
            return $"{age.TotalHours:0.0}h";
        }

        return $"{age.TotalDays:0.0}d";
    }

    private static string CompactDurationSeconds(int seconds)
    {
        if (seconds <= 0)
        {
            return "not reported";
        }

        TimeSpan duration = TimeSpan.FromSeconds(seconds);
        if (duration.TotalDays >= 1)
        {
            return $"{duration.TotalDays:0.#}d";
        }

        if (duration.TotalHours >= 1)
        {
            return $"{duration.TotalHours:0.#}h";
        }

        if (duration.TotalMinutes >= 1)
        {
            return $"{duration.TotalMinutes:0.#}m";
        }

        return $"{duration.TotalSeconds:0}s";
    }

    private void RenderJobsBoard(
        IReadOnlyList<JobDto> jobs,
        IReadOnlyList<JobDto> filtered)
    {
        JobsBoardPanel.Visibility = Visibility.Visible;
        int failures = jobs.Count(job => !IsJobSuccess(job));
        int slow = jobs.Count(IsJobSlow);
        double avgDuration = jobs.Count == 0 ? 0 : jobs.Average(JobDurationSeconds);
        JobDto? latest = jobs.OrderByDescending(job => job.FinishedAt).FirstOrDefault();
        bool stale = latest is null || DateTimeOffset.Now - latest.FinishedAt.ToLocalTime() > TimeSpan.FromMinutes(15);

        JobsBoardSummaryText.Text =
            $"{filtered.Count}/{jobs.Count} shown · {failures} failed · {slow} slow"
            + (stale ? " · stale" : " · live cadence");
        JobsDurationSummaryText.Text = jobs.Count == 0
            ? "No job duration history"
            : $"avg {avgDuration:0.00}s · max {jobs.Max(JobDurationSeconds):0.00}s · filter {_jobsFilter}";
        JobsMixSummaryText.Text =
            $"success {jobs.Count(IsJobSuccess)} · failed {failures} · slow {slow}";

        List<string> cadence = jobs
            .GroupBy(job => job.JobId)
            .OrderBy(group => group.Key)
            .Take(6)
            .Select(group =>
            {
                JobDto last = group.OrderByDescending(job => job.FinishedAt).First();
                bool jobStale = DateTimeOffset.Now - last.FinishedAt.ToLocalTime() > TimeSpan.FromMinutes(15);
                return $"{(jobStale ? "[STALE]" : "[OK]")} {group.Key}: {RelativeAge(last.FinishedAt)} ago";
            })
            .ToList();
        JobsCadenceText.Text = cadence.Count == 0
            ? "No scheduler cadence has been recorded yet."
            : string.Join(Environment.NewLine, cadence);

        List<string> failureRows = jobs
            .Where(job => !IsJobSuccess(job))
            .Take(4)
            .Select(job => $"{job.JobId} · {LocalTime(job.FinishedAt)} · {job.Error ?? Pretty(job.Status)}")
            .ToList();
        JobsFailureText.Text = failureRows.Count == 0
            ? stale
                ? "No failures in the latest sample, but cadence is stale. The worker is probably stopped."
                : "No failures in the latest sample."
            : string.Join(Environment.NewLine, failureRows);

        DrawJobsDurationTape();
        DrawJobsMix();
    }

    private void JobsDurationCanvas_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        if (JobsBoardPanel.Visibility == Visibility.Visible)
        {
            DrawJobsDurationTape();
        }
    }

    private void JobsMixCanvas_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        if (JobsBoardPanel.Visibility == Visibility.Visible)
        {
            DrawJobsMix();
        }
    }

    private void DrawJobsDurationTape()
    {
        JobsDurationCanvas.Children.Clear();
        double width = JobsDurationCanvas.ActualWidth;
        double height = JobsDurationCanvas.ActualHeight;
        if (width <= 1 || height <= 1)
        {
            return;
        }

        List<JobDto> recent = _jobsBoardItems
            .OrderBy(job => job.FinishedAt)
            .TakeLast(48)
            .ToList();
        if (recent.Count == 0)
        {
            AddCanvasText(
                JobsDurationCanvas,
                "No job runs yet",
                18,
                20,
                new SolidColorBrush(Color.FromArgb(0xDD, 0xFF, 0xFF, 0xFF)),
                18,
                new FontWeight { Weight = 600 });
            AddCanvasText(
                JobsDurationCanvas,
                "The scheduler tape will fill after the worker writes job_runs.",
                18,
                56,
                new SolidColorBrush(Color.FromArgb(0xAA, 0xFF, 0xFF, 0xFF)),
                13);
            return;
        }

        double maxDuration = Math.Max(0.1, recent.Max(JobDurationSeconds));
        double gap = 4;
        double barWidth = Math.Max(5, (width - gap * (recent.Count - 1)) / recent.Count);
        double baseline = height - 22;
        var baselineLine = new Line
        {
            X1 = 0,
            X2 = width,
            Y1 = baseline,
            Y2 = baseline,
            Stroke = new SolidColorBrush(Color.FromArgb(0x55, 0xAA, 0xAA, 0xAA)),
            StrokeThickness = 1,
        };
        JobsDurationCanvas.Children.Add(baselineLine);

        for (int i = 0; i < recent.Count; i++)
        {
            JobDto job = recent[i];
            double duration = JobDurationSeconds(job);
            double barHeight = Math.Max(2, duration / maxDuration * (height - 54));
            Brush fill = !IsJobSuccess(job)
                ? AccentBrush("negative")
                : IsJobSlow(job)
                    ? new SolidColorBrush(Color.FromArgb(0xFF, 0xC7, 0x9A, 0x3D))
                    : AccentBrush("positive");
            var rect = new Rectangle
            {
                Width = barWidth,
                Height = barHeight,
                RadiusX = 3,
                RadiusY = 3,
                Fill = fill,
            };
            Canvas.SetLeft(rect, i * (barWidth + gap));
            Canvas.SetTop(rect, baseline - barHeight);
            JobsDurationCanvas.Children.Add(rect);
        }

        JobDto latest = recent[^1];
        AddCanvasText(
            JobsDurationCanvas,
            $"latest {latest.JobId} · {JobDurationSeconds(latest):0.00}s · {RelativeAge(latest.FinishedAt)} ago",
            16,
            12,
            new SolidColorBrush(Color.FromArgb(0xDD, 0xFF, 0xFF, 0xFF)),
            13,
            new FontWeight { Weight = 600 });
    }

    private void DrawJobsMix()
    {
        JobsMixCanvas.Children.Clear();
        double width = JobsMixCanvas.ActualWidth;
        double height = JobsMixCanvas.ActualHeight;
        if (width <= 1 || height <= 1)
        {
            return;
        }

        int success = _jobsBoardItems.Count(IsJobSuccess);
        int failures = _jobsBoardItems.Count(job => !IsJobSuccess(job));
        int slow = _jobsBoardItems.Count(job => IsJobSuccess(job) && IsJobSlow(job));
        int fast = Math.Max(0, success - slow);
        int total = fast + slow + failures;
        if (total == 0)
        {
            AddCanvasText(
                JobsMixCanvas,
                "No run mix yet",
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
            ("Fast", fast, AccentBrush("positive")),
            ("Slow", slow, new SolidColorBrush(Color.FromArgb(0xFF, 0xC7, 0x9A, 0x3D))),
            ("Failed", failures, AccentBrush("negative")),
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
            JobsMixCanvas.Children.Add(rect);
            if (segmentWidth > 70)
            {
                AddCanvasText(
                    JobsMixCanvas,
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

    private async void AlertsFilterBox_SelectionChanged(
        object sender,
        SelectionChangedEventArgs e)
    {
        string nextState = AlertsFilterBox.SelectedIndex == 1
            ? "all"
            : "unacknowledged";
        if (!IsLoaded || nextState == _alertsQueryState)
        {
            return;
        }

        _alertsQueryState = nextState;
        if (_selectedDestination == "Alerts")
        {
            await RefreshAsync();
        }
    }

    private void RenderAlerts(AlertsResponseDto response)
    {
        _visibleAlertItems = response.Items
            .OrderByDescending(item => item.Id)
            .ToList();
        List<AlertInboxListItem> rows = _visibleAlertItems
            .Select(ToAlertInboxListItem)
            .ToList();

        AlertsInfoBar.IsOpen = false;
        AlertsList.ItemsSource = rows;
        AlertsListHeaderText.Text =
            $"{rows.Count} shown | {Pretty(response.QueryState)} | snapshot {LocalTime(response.GeneratedAt)}";
        AlertsEmptyState.Visibility = rows.Count == 0
            ? Visibility.Visible
            : Visibility.Collapsed;
        AlertsEmptyText.Text = response.QueryState.Equals(
            "all",
            StringComparison.OrdinalIgnoreCase)
            ? "No local alerts recorded"
            : "No unacknowledged local alerts";
        AlertsEmptyDetailText.Text = response.QueryState.Equals(
            "all",
            StringComparison.OrdinalIgnoreCase)
            ? "The durable local inbox has no records yet. Telegram activation is optional."
            : "The local inbox is clear. Telegram send and retry state is tracked separately.";

        AlertsUnacknowledgedText.Text = $"{response.UnacknowledgedCount} unacknowledged";
        AlertsUnacknowledgedText.Foreground = AccentBrush(
            response.UnacknowledgedCount == 0 ? "positive" : "warning");
        AlertsLocalDetailText.Text =
            $"{response.TotalCount} total | {rows.Count} in current {Pretty(response.QueryState)} view";
        AlertsLocalFreshnessText.Text = response.OldestUnacknowledgedAt is { } oldest
            ? $"Oldest unacknowledged {LocalTime(oldest)} | age {AlertAge(response.OldestUnacknowledgedAgeSeconds)}"
            : $"No unacknowledged records | generated {LocalTime(response.GeneratedAt)}";

        RenderAlertChannelSummary(response);
        RenderAlertBacklogSummary(response);
        AlertsAckThroughButton.IsEnabled =
            !_isAcknowledgingAlert && LatestVisibleUnacknowledgedId() is not null;
    }

    private void RenderAlertChannelSummary(AlertsResponseDto response)
    {
        bool notActivated = response.ChannelState.Equals(
            "not_activated",
            StringComparison.OrdinalIgnoreCase);
        string accent = response.ChannelState.ToLowerInvariant() switch
        {
            "healthy" => "positive",
            "stale" or "degraded" => "warning",
            _ => string.Empty,
        };
        AlertsChannelBadge.Background = AccentBrush(
            string.IsNullOrEmpty(accent) ? null : accent);
        AlertsChannelStatusText.Foreground = AccentBrush(
            string.IsNullOrEmpty(accent) ? null : accent);

        if (notActivated)
        {
            AlertsChannelBadgeText.Text = "OPTIONAL";
            AlertsChannelStatusText.Text = "Not activated";
            AlertsChannelDetailText.Text =
                "The durable local inbox is active. Telegram delivery has not been activated and is not required.";
            AlertsHeartbeatText.Text =
                "Activation: not configured | Heartbeat: not expected";
            return;
        }

        AlertsChannelBadgeText.Text = response.ChannelState.ToUpperInvariant();
        AlertsChannelStatusText.Text = Pretty(response.ChannelState);
        AlertsChannelDetailText.Text =
            $"Channel {Pretty(response.Channel)} | activated {NullableLocalTime(response.ActivatedAt)}";
        AlertsHeartbeatText.Text =
            $"Heartbeat {NullableLocalTime(response.HeartbeatAt)}"
            + (response.HeartbeatStale ? " (stale)" : "")
            + $" | last success {NullableLocalTime(response.LastSuccessAt)}"
            + $" | last failure {NullableLocalTime(response.LastFailureAt)}";
    }

    private void RenderAlertBacklogSummary(AlertsResponseDto response)
    {
        bool notActivated = response.ChannelState.Equals(
            "not_activated",
            StringComparison.OrdinalIgnoreCase);
        string badge;
        string? accent;
        if (notActivated)
        {
            badge = "LOCAL ONLY";
            accent = null;
        }
        else if (response.DeadLetterCount > 0)
        {
            badge = "DEAD LETTER";
            accent = "negative";
        }
        else if (response.BacklogOverdue)
        {
            badge = "OVERDUE";
            accent = "warning";
        }
        else if (response.PendingAfterActivation > 0)
        {
            badge = "PENDING";
            accent = "warning";
        }
        else
        {
            badge = "CLEAR";
            accent = "positive";
        }

        AlertsBacklogBadgeText.Text = badge;
        AlertsBacklogBadge.Background = AccentBrush(accent);
        AlertsBacklogText.Text =
            $"{response.PendingAfterActivation} pending | {response.DeadLetterCount} dead letter";
        AlertsBacklogText.Foreground = AccentBrush(accent);
        AlertsBacklogDetailText.Text = notActivated
            ? "Pre-activation and legacy alerts remain local by design. No external backlog is expected."
            : response.OldestPendingAfterActivationAt is { } oldestPending
                ? $"Oldest pending {LocalTime(oldestPending)}"
                    + (response.BacklogOverdue ? " | backlog SLA exceeded" : " | within backlog SLA")
                : "No pending post-activation delivery";
        AlertsDeliveryErrorText.Text = string.IsNullOrWhiteSpace(response.LastError)
            ? "No delivery error reported"
            : $"Last delivery error: {BoundedAlertText(response.LastError, 280)}";
    }

    private void RenderAlertsUnavailable(string? error)
    {
        _visibleAlertItems = [];
        AlertsList.ItemsSource = Array.Empty<AlertInboxListItem>();
        AlertsListHeaderText.Text = "Snapshot unavailable";
        AlertsEmptyState.Visibility = Visibility.Visible;
        AlertsEmptyText.Text = "Alerts API unavailable";
        AlertsEmptyDetailText.Text =
            "The console cannot verify the alert contract. No zero counts or legacy rows are substituted.";
        AlertsAckThroughButton.IsEnabled = false;

        AlertsUnacknowledgedText.Text = "-";
        AlertsUnacknowledgedText.Foreground = AccentBrush("warning");
        AlertsLocalDetailText.Text = "Local inbox counts unavailable";
        AlertsLocalFreshnessText.Text = "Snapshot freshness unavailable";
        AlertsChannelBadgeText.Text = "DEGRADED";
        AlertsChannelBadge.Background = AccentBrush("warning");
        AlertsChannelStatusText.Text = "Unavailable";
        AlertsChannelStatusText.Foreground = AccentBrush("warning");
        AlertsChannelDetailText.Text =
            "Telegram activation state is unknown. This is not reported as Not activated.";
        AlertsHeartbeatText.Text = "Activation and heartbeat unavailable";
        AlertsBacklogBadgeText.Text = "UNKNOWN";
        AlertsBacklogBadge.Background = AccentBrush("warning");
        AlertsBacklogText.Text = "-";
        AlertsBacklogText.Foreground = AccentBrush("warning");
        AlertsBacklogDetailText.Text = "Pending and dead-letter counts unavailable";
        AlertsDeliveryErrorText.Text = "Delivery error state unavailable";

        AlertsInfoBar.IsOpen = true;
        AlertsInfoBar.Severity = InfoBarSeverity.Warning;
        AlertsInfoBar.Title = "Alert inbox degraded";
        AlertsInfoBar.Message = AlertApiErrorMessage(error);
    }

    private void UpdateAlertsNavigationBadge(AlertsResponseDto? response, string? error)
    {
        if (response is null)
        {
            AlertsInfoBadge.Value = -1;
            AlertsInfoBadge.Visibility = Visibility.Visible;
            AlertsInfoBadge.Background = AccentBrush("warning");
            AutomationProperties.SetName(
                AlertsNavigationItem,
                "Alerts, status unavailable");
            ToolTipService.SetToolTip(
                AlertsNavigationItem,
                AlertApiErrorMessage(error));
            return;
        }

        int count = Math.Max(0, response.UnacknowledgedCount);
        AlertsInfoBadge.Value = Math.Min(99, count);
        AlertsInfoBadge.Visibility = count > 0
            ? Visibility.Visible
            : Visibility.Collapsed;
        AlertsInfoBadge.Background = AccentBrush(count > 0 ? "warning" : null);
        AutomationProperties.SetName(
            AlertsNavigationItem,
            count == 0 ? "Alerts, inbox clear" : $"Alerts, {count} unacknowledged");
        ToolTipService.SetToolTip(
            AlertsNavigationItem,
            count == 0 ? "No unacknowledged local alerts" : $"{count} unacknowledged local alerts");
    }

    private AlertInboxListItem ToAlertInboxListItem(AlertItemDto item)
    {
        bool acknowledged = item.AcknowledgedAt is not null;
        string externalStatus = AlertExternalStatus(item.ExternalState);
        string? accent = AlertExternalAccent(item.ExternalState);
        return new AlertInboxListItem(
            item.Id,
            $"{Pretty(item.Category)} | alert #{item.Id}",
            item.EventKey,
            BoundedAlertText(item.Payload, 900),
            $"Created {LocalTime(item.CreatedAt)} | {RelativeAge(item.CreatedAt)} ago",
            acknowledged
                ? $"Local status: acknowledged {NullableLocalTime(item.AcknowledgedAt)}"
                : "Local status: unacknowledged",
            $"External status: {externalStatus}",
            AlertDeliveryDetail(item),
            string.IsNullOrWhiteSpace(item.Error)
                ? "Delivery error: none"
                : $"Delivery error: {BoundedAlertText(item.Error, 360)}",
            externalStatus,
            accent,
            acknowledged ? "Acknowledged" : "Acknowledge",
            !acknowledged);
    }

    private static string AlertExternalStatus(string state) => state.ToLowerInvariant() switch
    {
        "local_only" => "Local only (legacy or pre-activation)",
        "sent" => "Sent",
        "dead_letter" => "Dead letter",
        "in_flight" => "In flight",
        "retry_scheduled" => "Retry scheduled",
        "pending" => "Pending",
        _ => "Unknown",
    };

    private static string? AlertExternalAccent(string state) => state.ToLowerInvariant() switch
    {
        "sent" => "positive",
        "dead_letter" => "negative",
        "in_flight" or "retry_scheduled" or "pending" => "warning",
        _ => null,
    };

    private static string AlertDeliveryDetail(AlertItemDto item)
    {
        string attempts = $"attempts {item.Attempts}";
        return item.ExternalState.ToLowerInvariant() switch
        {
            "local_only" => $"{attempts} | intentionally excluded from external delivery",
            "sent" => $"{attempts} | sent {NullableLocalTime(item.SentAt)}",
            "dead_letter" => $"{attempts} | dead-lettered {NullableLocalTime(item.DeadLetteredAt)}",
            "in_flight" => $"{attempts} | lease until {NullableLocalTime(item.LeaseUntil)}",
            "retry_scheduled" => $"{attempts} | last {NullableLocalTime(item.LastAttemptAt)}"
                + $" | next {NullableLocalTime(item.NextAttemptAt)}",
            _ => $"{attempts} | next attempt {NullableLocalTime(item.NextAttemptAt)}",
        };
    }

    private static string AlertAge(int? seconds)
    {
        if (seconds is null)
        {
            return "unknown";
        }

        if (seconds <= 0)
        {
            return "just now";
        }

        TimeSpan age = TimeSpan.FromSeconds(seconds.Value);
        if (age.TotalDays >= 1)
        {
            return $"{age.TotalDays:0.#}d";
        }

        if (age.TotalHours >= 1)
        {
            return $"{age.TotalHours:0.#}h";
        }

        if (age.TotalMinutes >= 1)
        {
            return $"{age.TotalMinutes:0.#}m";
        }

        return $"{age.TotalSeconds:0}s";
    }

    private static string NullableLocalTime(DateTimeOffset? value) =>
        value is { } timestamp ? LocalTime(timestamp) : "not reported";

    private static string BoundedAlertText(string? value, int limit)
    {
        string text = string.IsNullOrWhiteSpace(value) ? "(empty)" : value.Trim();
        return text.Length <= limit ? text : $"{text[..limit]}...";
    }

    private long? LatestVisibleUnacknowledgedId() => _visibleAlertItems
        .Where(item => item.AcknowledgedAt is null)
        .Select(item => (long?)item.Id)
        .Max();

    private async void AlertAckButton_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button button
            || button.Tag is not long alertId
            || alertId < 1)
        {
            ShowAlertsNotice(
                InfoBarSeverity.Error,
                "Invalid alert",
                "The selected alert does not have a positive local id.");
            return;
        }

        button.IsEnabled = false;
        await RunAlertAcknowledgementAsync(
            () => _apiClient.AcknowledgeAlertAsync(alertId, _lifetime.Token),
            result => $"Alert #{alertId} acknowledged locally. Server accepted {result.Acknowledged} row.");
    }

    private async void AlertsAckThroughButton_Click(object sender, RoutedEventArgs e)
    {
        long? throughId = LatestVisibleUnacknowledgedId();
        if (throughId is null)
        {
            ShowAlertsNotice(
                InfoBarSeverity.Informational,
                "Inbox already clear",
                "There is no visible unacknowledged alert boundary.");
            return;
        }

        var explanation = new TextBlock
        {
            Text =
                $"Acknowledge every currently unacknowledged local alert with id <= {throughId}? "
                + "This includes older rows outside the 100-row view. New alerts receive larger ids and remain untouched. "
                + "Telegram send and retry state, trading controls, and the live gate do not change.",
            TextWrapping = TextWrapping.Wrap,
            MaxWidth = 520,
        };
        ContentDialog dialog = new()
        {
            Title = $"Acknowledge through alert #{throughId}",
            Content = explanation,
            PrimaryButtonText = "Acknowledge local range",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Close,
            XamlRoot = XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }

        long boundary = throughId.Value;
        await RunAlertAcknowledgementAsync(
            () => _apiClient.AcknowledgeAlertsThroughAsync(boundary, _lifetime.Token),
            result => $"Acknowledged {result.Acknowledged} local alert(s) through #{boundary}.");
    }

    private async Task RunAlertAcknowledgementAsync(
        Func<Task<AlertAcknowledgementResponseDto>> operation,
        Func<AlertAcknowledgementResponseDto, string> successMessage)
    {
        if (_isAcknowledgingAlert)
        {
            return;
        }

        _isAcknowledgingAlert = true;
        AlertsAckThroughButton.IsEnabled = false;
        try
        {
            AlertAcknowledgementResponseDto result = await operation();
            await RefreshAsync();
            ShowAlertsNotice(
                InfoBarSeverity.Success,
                "Local acknowledgement saved",
                successMessage(result)
                    + " External delivery state remains independent.");
        }
        catch (OperationCanceledException) when (_lifetime.IsCancellationRequested)
        {
            return;
        }
        catch (Exception exception)
        {
            ShowAlertsNotice(
                InfoBarSeverity.Error,
                "Acknowledgement rejected",
                AlertMutationErrorMessage(exception));
        }
        finally
        {
            _isAcknowledgingAlert = false;
            AlertsAckThroughButton.IsEnabled =
                _selectedDestination == "Alerts"
                && LatestVisibleUnacknowledgedId() is not null;
        }
    }

    private void ShowAlertsNotice(InfoBarSeverity severity, string title, string message)
    {
        AlertsInfoBar.Severity = severity;
        AlertsInfoBar.Title = title;
        AlertsInfoBar.Message = message;
        AlertsInfoBar.IsOpen = true;
    }

    private static string AlertApiErrorMessage(string? error)
    {
        if (string.IsNullOrWhiteSpace(error))
        {
            return "The alerts endpoint did not return a compatible response.";
        }

        if (error.Contains("401", StringComparison.OrdinalIgnoreCase))
        {
            return "Alert inbox authentication failed. Configure the matching operator API key in Settings.";
        }

        if (error.Contains("404", StringComparison.OrdinalIgnoreCase)
            || error.Contains("incompatible", StringComparison.OrdinalIgnoreCase)
            || error.Contains("JSON", StringComparison.OrdinalIgnoreCase))
        {
            return "The backend alert API is missing or incompatible with this console version.";
        }

        return BoundedAlertText(error, 420);
    }

    private static string AlertMutationErrorMessage(Exception exception)
    {
        string message = exception.Message;
        if (message.Contains("401", StringComparison.OrdinalIgnoreCase)
            || message.Contains("403", StringComparison.OrdinalIgnoreCase))
        {
            return "The backend rejected this mutation. Configure the same OPERATOR_API_KEY on the API and in this console.";
        }

        if (message.Contains("404", StringComparison.OrdinalIgnoreCase))
        {
            return "The alert no longer exists. Refresh the inbox and try again.";
        }

        return BoundedAlertText(message, 420);
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
            $"All-protocol history: {liveSummary.Positions} positions · {liveSummary.Open} open · {liveSummary.Closed} closed",
            $"P&L {Usd(liveSummary.TotalPnlUsd)} · ROI {Percent(liveSummary.Roi)}",
            $"{EvidenceScope(liveSummary.BenchmarkProtocol, liveSummary.BenchmarkSampleScope)}: "
                + $"{liveSummary.BenchmarkPositions} positions · ROI {Percent(liveSummary.BenchmarkRoi)} · "
                + $"CLV {Number(liveSummary.AvgClv)}",
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
                "Live performance · all-protocol history",
                summary.Positions == 0
                    ? "No live positions yet"
                    : $"{summary.Positions} positions · {summary.Open} open · {summary.Closed} closed",
                $"P&L {Usd(summary.TotalPnlUsd)} · ROI {Percent(summary.Roi)} · win {Percent(summary.WinRate)}",
                $"{EvidenceScope(summary.BenchmarkProtocol, summary.BenchmarkSampleScope)}: "
                    + $"{summary.BenchmarkPositions} position(s) · ROI {Percent(summary.BenchmarkRoi)} · "
                    + $"CLV {Number(summary.AvgClv)} · fees {Usd(summary.TotalFeesUsd)}",
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
            $"limit {Percent(order.RequestedLimitPrice ?? order.Price)} · "
                + $"fill VWAP {Percent(order.ActualFillVwap)} · "
                + $"size {Usd(order.SizeUsd)} · filled {Usd(order.FilledUsd)}",
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

    private void ValidationLedgerCanvas_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        if (_selectedDestination == "Dashboard")
        {
            DrawValidationLedgerChart();
        }
    }

    private void ValidationLedgerCanvas_PointerMoved(object sender, PointerRoutedEventArgs e)
    {
        _validationLedgerHoverPoint = e.GetCurrentPoint(ValidationLedgerCanvas).Position;
        DrawValidationLedgerChart();
    }

    private void ValidationLedgerCanvas_PointerExited(object sender, PointerRoutedEventArgs e)
    {
        _validationLedgerHoverPoint = null;
        ValidationLedgerChartSubtitle.Text = _validationLedgerBaseChartSubtitle;
        DrawValidationLedgerChart();
    }

    private void DrawValidationLedgerChart()
    {
        ValidationLedgerCanvas.Children.Clear();
        double width = ValidationLedgerCanvas.ActualWidth;
        double height = ValidationLedgerCanvas.ActualHeight;
        if (width <= 1 || height <= 1)
        {
            return;
        }

        if (_validationLedgerPoints.Count == 0)
        {
            ValidationLedgerEmptyText.Visibility = Visibility.Visible;
            return;
        }

        ValidationLedgerEmptyText.Visibility = Visibility.Collapsed;
        DrawChartGrid(ValidationLedgerCanvas, width, height);
        double[] values = _validationLedgerPoints
            .Select(point => point.CumulativePnlUsd)
            .ToArray();
        if (values.Length >= 2)
        {
            DrawLineSeries(
                ValidationLedgerCanvas,
                values,
                width,
                height,
                PnlAccent(values[^1]));
        }
        else
        {
            double lo = Math.Min(0, values[0]);
            double hi = Math.Max(0, values[0]);
            double span = hi - lo <= 0 ? 1 : hi - lo;
            double y(double value) => height - ((value - lo) / span * height);
            ValidationLedgerCanvas.Children.Add(new Line
            {
                X1 = 0,
                X2 = width,
                Y1 = y(0),
                Y2 = y(0),
                Stroke = new SolidColorBrush(Color.FromArgb(0x66, 0x99, 0x99, 0x99)),
                StrokeThickness = 1,
            });
            Brush pointBrush = AccentBrush(PnlAccent(values[0]));
            var dot = new Ellipse
            {
                Width = 10,
                Height = 10,
                Fill = pointBrush,
            };
            Canvas.SetLeft(dot, width / 2 - 5);
            Canvas.SetTop(dot, y(values[0]) - 5);
            ValidationLedgerCanvas.Children.Add(dot);
        }

        if (_validationLedgerHoverPoint is not Point hoverPoint)
        {
            return;
        }

        int index = NearestSeriesIndex(hoverPoint.X, width, _validationLedgerPoints.Count);
        ProspectiveValidationPointDto point = _validationLedgerPoints[index];
        double min = Math.Min(0, values.Min());
        double max = Math.Max(0, values.Max());
        double chartSpan = max - min <= 0 ? 1 : max - min;
        double x = values.Length == 1 ? width / 2 : SeriesX(index, width, values.Length);
        double yPoint = height - ((point.CumulativePnlUsd - min) / chartSpan * height);
        string text =
            $"{point.StationIcao} | {point.MarketDate:yyyy-MM-dd} | {Pretty(point.Kind)}\n"
            + $"block {Signed(point.BlockPnlUsd)} | total {Signed(point.CumulativePnlUsd)}";
        string meta =
            $"Block {point.Sequence}/{_validationLedgerPoints.Count} | "
            + $"recognized {LocalTime(point.RecognizedAt)} | {point.Orders} order(s) | "
            + $"cost {Usd(point.BlockCostUsd)} | equity {Usd(point.EquityUsd)} | "
            + $"drawdown {Usd(point.DrawdownUsd)}";
        DrawHoverCrosshair(
            ValidationLedgerCanvas,
            new DrilldownHoverReadout(
                x,
                yPoint,
                text,
                meta,
                AccentBrush(PnlAccent(point.CumulativePnlUsd))),
            width,
            height);
        ValidationLedgerChartSubtitle.Text = meta;
    }

    private void CockpitChartCanvas_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        if (_selectedDestination == "Dashboard")
        {
            DrawCockpitChart();
        }
    }

    private void CockpitChartCanvas_PointerMoved(object sender, PointerRoutedEventArgs e)
    {
        _cockpitHoverPoint = e.GetCurrentPoint(CockpitChartCanvas).Position;
        DrawCockpitChart();
    }

    private void CockpitChartCanvas_PointerExited(object sender, PointerRoutedEventArgs e)
    {
        _cockpitHoverPoint = null;
        CockpitChartSubtitle.Text = _cockpitBaseChartSubtitle;
        DrawCockpitChart();
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
            _cockpitBaseChartSubtitle =
                $"mid {Percent(latestPrice?.Midpoint)} · model {Percent(latestSignal?.ModelProbability)}"
                + $" · edge {Percent(latestSignal?.Edge)}";
            CockpitChartSubtitle.Text = _cockpitBaseChartSubtitle;
            CockpitLegendText.Text = "Cyan market midpoint · violet model probability";
            DrawCockpitHoverOverlay(width, height, "market");
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
            _cockpitBaseChartSubtitle =
                $"{Usd(values[^1])} over {_cockpitEquity.Count} closed trade points";
            CockpitChartSubtitle.Text = _cockpitBaseChartSubtitle;
            CockpitLegendText.Text = "Realised P&L curve";
            DrawCockpitHoverOverlay(width, height, "equity");
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
            _cockpitBaseChartSubtitle = "Waiting for mark-to-market or realised trade history";
            CockpitChartSubtitle.Text = _cockpitBaseChartSubtitle;
            CockpitLegendText.Text = "No series";
            return;
        }

        double[] pnl = marked.Select(p => p.UnrealizedPnlUsd ?? 0).ToArray();
        DrawBarSeries(CockpitChartCanvas, pnl, width, height);
        double total = pnl.Sum();
        CockpitChartTitle.Text = "Open mark-to-market distribution";
        _cockpitBaseChartSubtitle =
            $"{Signed(total)} across {marked.Count} marked open position(s)";
        CockpitChartSubtitle.Text = _cockpitBaseChartSubtitle;
        CockpitLegendText.Text = "Green positive · red negative";
        DrawCockpitHoverOverlay(width, height, "pnl");
    }

    private void DrawCockpitHoverOverlay(double width, double height, string chartMode)
    {
        if (_cockpitHoverPoint is not Point point || width <= 1 || height <= 1)
        {
            return;
        }

        DrilldownHoverReadout? readout = chartMode switch
        {
            "market" => CockpitMarketHoverReadout(point, width, height),
            "equity" => CockpitEquityHoverReadout(point, width, height),
            "pnl" => CockpitPnlHoverReadout(point, width, height),
            _ => null,
        };
        if (readout is null)
        {
            return;
        }

        DrawHoverCrosshair(CockpitChartCanvas, readout, width, height);
        CockpitChartSubtitle.Text = readout.Meta;
    }

    private DrilldownHoverReadout? CockpitMarketHoverReadout(Point point, double width, double height)
    {
        if (_cockpitMarketPrices.Count >= 2)
        {
            int index = NearestSeriesIndex(point.X, width, _cockpitMarketPrices.Count);
            MarketPricePointDto price = _cockpitMarketPrices[index];
            ProbabilityPointDto? probability = NearestProbabilityByTime(price.T);
            double x = SeriesX(index, width, _cockpitMarketPrices.Count);
            double y = height - Math.Clamp(price.Midpoint, 0, 1) * height;
            string text =
                $"{LocalTime(price.T)}\nmid {Percent(price.Midpoint)} · model {Percent(probability?.ModelProbability)} · edge {Percent(probability?.Edge)}";
            string meta =
                $"Hover market · {LocalTime(price.T)} · bid {Percent(price.BestBid)} · ask {Percent(price.BestAsk)}"
                + (probability is null ? string.Empty : $" · model {Percent(probability.ModelProbability)}");
            return new DrilldownHoverReadout(
                x,
                y,
                text,
                meta,
                new SolidColorBrush(Color.FromArgb(0xFF, 0x43, 0xD8, 0xFF)));
        }

        List<ProbabilityPointDto> points = _cockpitProbabilities
            .Where(p => p.ModelProbability is not null || p.MarketPrice is not null)
            .ToList();
        if (points.Count < 2)
        {
            return null;
        }

        int probabilityIndex = NearestSeriesIndex(point.X, width, points.Count);
        ProbabilityPointDto probabilityOnly = points[probabilityIndex];
        double value = probabilityOnly.ModelProbability ?? probabilityOnly.MarketPrice ?? 0;
        double px = SeriesX(probabilityIndex, width, points.Count);
        double py = height - Math.Clamp(value, 0, 1) * height;
        return new DrilldownHoverReadout(
            px,
            py,
            $"{LocalTime(probabilityOnly.T)}\nmodel {Percent(probabilityOnly.ModelProbability)} · market {Percent(probabilityOnly.MarketPrice)} · edge {Percent(probabilityOnly.Edge)}",
            $"Hover probability · {LocalTime(probabilityOnly.T)} · {probabilityOnly.Source}",
            new SolidColorBrush(Color.FromArgb(0xFF, 0xB2, 0x7C, 0xFF)));
    }

    private ProbabilityPointDto? NearestProbabilityByTime(DateTimeOffset timestamp) =>
        _cockpitProbabilities
            .Where(p => p.ModelProbability is not null || p.Edge is not null)
            .OrderBy(p => Math.Abs((p.T - timestamp).TotalSeconds))
            .FirstOrDefault();

    private DrilldownHoverReadout? CockpitEquityHoverReadout(Point point, double width, double height)
    {
        if (_cockpitEquity.Count < 2)
        {
            return null;
        }

        int index = NearestSeriesIndex(point.X, width, _cockpitEquity.Count);
        EquityPointDto equity = _cockpitEquity[index];
        double[] values = _cockpitEquity.Select(p => p.CumulativePnlUsd).ToArray();
        double lo = Math.Min(0, values.Min());
        double hi = Math.Max(0, values.Max());
        double span = hi - lo <= 0 ? 1 : hi - lo;
        double x = SeriesX(index, width, _cockpitEquity.Count);
        double y = height - ((equity.CumulativePnlUsd - lo) / span * height);
        return new DrilldownHoverReadout(
            x,
            y,
            $"{LocalTime(equity.T)}\nrealised {Signed(equity.CumulativePnlUsd)}",
            $"Hover equity · {LocalTime(equity.T)} · realised {Signed(equity.CumulativePnlUsd)} · point {index + 1}/{_cockpitEquity.Count}",
            AccentBrush(PnlAccent(equity.CumulativePnlUsd)));
    }

    private DrilldownHoverReadout? CockpitPnlHoverReadout(Point point, double width, double height)
    {
        List<PositionDto> marked = _cockpitPositions
            .Where(p => string.Equals(p.Status, "open", StringComparison.OrdinalIgnoreCase)
                && p.UnrealizedPnlUsd is not null)
            .OrderBy(p => p.UnrealizedPnlUsd)
            .Take(18)
            .ToList();
        if (marked.Count == 0)
        {
            return null;
        }

        double gap = 4;
        double barWidth = Math.Max(8, (width - gap * (marked.Count - 1)) / marked.Count);
        int index = Math.Clamp((int)Math.Floor(point.X / Math.Max(1, barWidth + gap)), 0, marked.Count - 1);
        PositionDto position = marked[index];
        double value = position.UnrealizedPnlUsd ?? 0;
        double absMax = Math.Max(1, marked.Select(p => Math.Abs(p.UnrealizedPnlUsd ?? 0)).Max());
        double baseline = height / 2;
        double barHeight = Math.Abs(value) / absMax * (height * 0.42);
        double x = index * (barWidth + gap) + barWidth / 2;
        double y = value >= 0 ? baseline - barHeight : baseline + barHeight;
        return new DrilldownHoverReadout(
            x,
            y,
            $"{position.StationIcao ?? "Unmapped"} · {Pretty(position.Side)}\nPnL {Signed(value)} · mark {Percent(position.MarkPrice)}",
            $"Hover position · {position.StationIcao ?? "Unmapped"} · {position.MarketDate:yyyy-MM-dd} · size {Usd(position.SizeUsd)} · unrealised {Signed(value)}",
            AccentBrush(PnlAccent(value)));
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

    private void DrilldownChartCanvas_PointerMoved(object sender, PointerRoutedEventArgs e)
    {
        _drilldownHoverPoint = e.GetCurrentPoint(DrilldownChartCanvas).Position;
        DrawDrilldownChart();
    }

    private void DrilldownChartCanvas_PointerExited(object sender, PointerRoutedEventArgs e)
    {
        _drilldownHoverPoint = null;
        DrilldownMetaText.Text = _drilldownBaseMetaText;
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
                DrawDrilldownHoverOverlay(width, height);
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
                DrawDrilldownHoverOverlay(width, height);
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
                DrawDrilldownHoverOverlay(width, height);
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

    private void DrawDrilldownHoverOverlay(double width, double height)
    {
        if (_drilldownHoverPoint is not Point point || width <= 1 || height <= 1)
        {
            return;
        }

        DrilldownHoverReadout? readout = DrilldownSeriesBox.SelectedIndex switch
        {
            0 => PriceHoverReadout(point, width, height),
            1 => ProbabilityHoverReadout(point, width, height),
            _ => EdgeHoverReadout(point, width, height),
        };
        if (readout is null)
        {
            return;
        }

        DrawHoverCrosshair(DrilldownChartCanvas, readout, width, height);
        DrilldownMetaText.Text = readout.Meta;
    }

    private DrilldownHoverReadout? PriceHoverReadout(Point point, double width, double height)
    {
        if (_drilldownPrices.Count < 2)
        {
            return null;
        }

        int index = NearestSeriesIndex(point.X, width, _drilldownPrices.Count);
        MarketPricePointDto price = _drilldownPrices[index];
        double x = SeriesX(index, width, _drilldownPrices.Count);
        double y = height - Math.Clamp(price.Midpoint, 0, 1) * height;
        string text =
            $"{LocalTime(price.T)}\nmid {Percent(price.Midpoint)} · bid {Percent(price.BestBid)} · ask {Percent(price.BestAsk)}";
        string meta =
            $"Hover price · {LocalTime(price.T)} · bid depth {Usd(price.BidDepthUsd)} · ask depth {Usd(price.AskDepthUsd)}";
        return new DrilldownHoverReadout(x, y, text, meta, new SolidColorBrush(Color.FromArgb(0xFF, 0x43, 0xD8, 0xFF)));
    }

    private DrilldownHoverReadout? ProbabilityHoverReadout(Point point, double width, double height)
    {
        List<ProbabilityPointDto> points = _drilldownProbabilities
            .Where(p => p.ModelProbability is not null || p.MarketPrice is not null)
            .ToList();
        if (points.Count < 2)
        {
            return null;
        }

        int index = NearestSeriesIndex(point.X, width, points.Count);
        ProbabilityPointDto probability = points[index];
        double value = probability.ModelProbability ?? probability.MarketPrice ?? 0;
        double x = SeriesX(index, width, points.Count);
        double y = height - Math.Clamp(value, 0, 1) * height;
        string text =
            $"{LocalTime(probability.T)}\nmodel {Percent(probability.ModelProbability)} · market {Percent(probability.MarketPrice)} · edge {Percent(probability.Edge)}";
        string meta =
            $"Hover probability · {LocalTime(probability.T)} · {probability.Source}"
            + (probability.Confidence is null ? string.Empty : $" · confidence {probability.Confidence}");
        return new DrilldownHoverReadout(x, y, text, meta, new SolidColorBrush(Color.FromArgb(0xFF, 0xB2, 0x7C, 0xFF)));
    }

    private DrilldownHoverReadout? EdgeHoverReadout(Point point, double width, double height)
    {
        List<ProbabilityPointDto> points = _drilldownProbabilities
            .Where(p => p.Edge is not null)
            .ToList();
        if (points.Count < 2)
        {
            return null;
        }

        int index = NearestSeriesIndex(point.X, width, points.Count);
        ProbabilityPointDto edge = points[index];
        double[] values = points.Select(p => p.Edge!.Value).ToArray();
        double lo = Math.Min(0, values.Min());
        double hi = Math.Max(0, values.Max());
        double span = hi - lo <= 0 ? 1 : hi - lo;
        double x = SeriesX(index, width, points.Count);
        double y = height - ((edge.Edge!.Value - lo) / span * height);
        string text =
            $"{LocalTime(edge.T)}\nedge {Percent(edge.Edge)} · model {Percent(edge.ModelProbability)} · market {Percent(edge.MarketPrice)}";
        string meta =
            $"Hover edge · {LocalTime(edge.T)} · {edge.Status ?? edge.Source}"
            + (edge.Confidence is null ? string.Empty : $" · confidence {edge.Confidence}");
        return new DrilldownHoverReadout(x, y, text, meta, AccentBrush(PnlAccent(edge.Edge)));
    }

    private static int NearestSeriesIndex(double x, double width, int count)
    {
        if (count <= 1 || width <= 0)
        {
            return 0;
        }

        double scaled = x / width * (count - 1);
        return Math.Clamp((int)Math.Round(scaled), 0, count - 1);
    }

    private static double SeriesX(int index, double width, int count) =>
        count <= 1 ? 0 : index * (width / (count - 1));

    private static void DrawHoverCrosshair(
        Canvas canvas,
        DrilldownHoverReadout readout,
        double width,
        double height)
    {
        Brush lineBrush = new SolidColorBrush(Color.FromArgb(0x99, 0xFF, 0xFF, 0xFF));
        canvas.Children.Add(new Line
        {
            X1 = readout.X,
            X2 = readout.X,
            Y1 = 0,
            Y2 = height,
            Stroke = lineBrush,
            StrokeThickness = 1,
        });
        canvas.Children.Add(new Line
        {
            X1 = 0,
            X2 = width,
            Y1 = readout.Y,
            Y2 = readout.Y,
            Stroke = new SolidColorBrush(Color.FromArgb(0x55, 0xFF, 0xFF, 0xFF)),
            StrokeThickness = 1,
        });

        var dot = new Ellipse
        {
            Width = 11,
            Height = 11,
            Fill = readout.Brush,
            Stroke = new SolidColorBrush(Colors.White),
            StrokeThickness = 1,
        };
        Canvas.SetLeft(dot, readout.X - 5.5);
        Canvas.SetTop(dot, readout.Y - 5.5);
        canvas.Children.Add(dot);

        var label = new Border
        {
            Padding = new Thickness(8, 5, 8, 5),
            CornerRadius = new CornerRadius(6),
            Background = new SolidColorBrush(Color.FromArgb(0xDD, 0x20, 0x20, 0x20)),
            BorderBrush = new SolidColorBrush(Color.FromArgb(0x77, 0xFF, 0xFF, 0xFF)),
            BorderThickness = new Thickness(1),
            Child = new TextBlock
            {
                Text = readout.Text,
                FontSize = 11,
                Foreground = new SolidColorBrush(Colors.White),
                TextWrapping = TextWrapping.Wrap,
            },
        };
        double labelLeft = Math.Clamp(readout.X + 10, 0, Math.Max(0, width - 205));
        double labelTop = Math.Clamp(readout.Y - 34, 0, Math.Max(0, height - 62));
        label.Width = Math.Min(205, Math.Max(150, width - labelLeft));
        Canvas.SetLeft(label, labelLeft);
        Canvas.SetTop(label, labelTop);
        canvas.Children.Add(label);
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

    private static double? SumAvailable(
        IEnumerable<PositionDto> positions,
        Func<PositionDto, double?> selector)
    {
        List<double> values = positions
            .Select(selector)
            .Where(value => value.HasValue)
            .Select(value => value!.Value)
            .ToList();
        return values.Count == 0 ? null : values.Sum();
    }

    private void RenderSettings(OverviewResponse overview, HealthSnapshot? health)
    {
        _lastRuntime = overview.Runtime;
        bool credentialConfigured = _credentialSource != "Not configured";
        bool apiHealthy = string.Equals(health?.HealthStatus, "ok", StringComparison.OrdinalIgnoreCase);
        bool apiReady = string.Equals(health?.ReadyStatus, "ready", StringComparison.OrdinalIgnoreCase);
        bool killClear = !overview.Runtime.KillSwitch;
        bool validationPassed = overview.Validation?.Passed is true;
        bool healthAuditOk = string.Equals(
            overview.HealthAudit?.Status,
            "healthy",
            StringComparison.OrdinalIgnoreCase);

        SetCollection(
        [
            new(
                "Local API endpoint",
                ApiBaseAddress.ToString(),
                "Loopback-only connection; direct database access is disabled",
                health is null
                    ? "Health snapshot not loaded yet"
                    : $"healthz {health.HealthStatus} · readyz {health.ReadyStatus} · checked {LocalTime(health.CheckedAt)}",
                apiHealthy && apiReady ? "Ready" : "Review",
                apiHealthy && apiReady ? "positive" : "warning"),
            new(
                "Operator API credential",
                _credentialSource,
                "Stored secrets are never written to app settings or displayed after saving",
                "Use API key in the command bar to save or remove the credential",
                credentialConfigured ? "Configured" : "Missing",
                credentialConfigured ? "positive" : "negative"),
            new(
                "Runtime mode",
                overview.Runtime.Mode.ToUpperInvariant(),
                overview.Runtime.KillSwitch
                    ? $"Kill switch active{ReasonSuffix(overview.Runtime.HaltedReason)}"
                    : "Kill switch off; backend risk limits remain authoritative",
                $"Open positions {overview.OpenPositions} · pending signals {overview.PendingSignals}",
                overview.Runtime.KillSwitch ? "Stopped" : "Running",
                overview.Runtime.KillSwitch ? "negative" : "positive"),
            new(
                "Validation gate",
                overview.Validation is null
                    ? "No checkpoint"
                    : $"Day {overview.Validation.CheckpointDay}",
                overview.Validation is null
                    ? "Paper evidence has not reached the configured checkpoint"
                    : (overview.Validation.Passed ? "Gate passed" : "Gate not passed"),
                overview.Validation is null
                    ? "Live remains unavailable"
                    : $"Eligible {overview.Validation.Eligible} · evaluated {LocalTime(overview.Validation.EvaluatedAt)}",
                validationPassed ? "Passed" : "Locked",
                validationPassed ? "positive" : "warning"),
            new(
                "Refresh cadence",
                "10 seconds",
                AutoRefreshButton.IsChecked == true
                    ? "Automatic refresh is enabled"
                    : "Automatic refresh is paused",
                "Manual refresh remains available from the command bar",
                AutoRefreshButton.IsChecked == true ? "Active" : "Paused",
                AutoRefreshButton.IsChecked == true ? "positive" : "warning"),
        ]);

        SettingsCenterPanel.Visibility = Visibility.Visible;
        SettingsCenterSummaryText.Text =
            $"{(apiReady ? "API ready" : apiHealthy ? "API not ready" : "API review")} · "
            + $"{(credentialConfigured ? "credential set" : "credential missing")} · "
            + $"runtime {overview.Runtime.Mode.ToUpperInvariant()}";
        SettingsApiText.Text = apiHealthy && apiReady
            ? "Connected to local operator API"
            : "Local operator API needs attention";
        SettingsApiDetailText.Text = health is null
            ? "No health snapshot has been loaded yet."
            : health.Reasons.Count == 0
                ? $"healthz {health.HealthStatus} · readyz {health.ReadyStatus} · {LocalTime(health.CheckedAt)}"
                : $"healthz {health.HealthStatus} · readyz {health.ReadyStatus} · "
                    + string.Join(" · ", health.Reasons);
        SettingsCredentialText.Text = credentialConfigured
            ? $"Configured via {_credentialSource}"
            : "Operator API key is not configured";
        SettingsCredentialDetailText.Text = credentialConfigured
            ? "Authenticated commands can be submitted from this console."
            : "Read-only monitoring still works; command submission will fail until a key is saved.";
        SettingsRuntimeText.Text = overview.Runtime.KillSwitch
            ? "Kill switch active"
            : $"Runtime {overview.Runtime.Mode.ToUpperInvariant()}";
        SettingsRuntimeDetailText.Text = overview.Runtime.KillSwitch
            ? $"Trading stopped{ReasonSuffix(overview.Runtime.HaltedReason)}"
            : $"Open {overview.OpenPositions} · pending {overview.PendingSignals} · exposure {Usd(overview.OpenExposureUsd)}";
        SettingsChecklistText.Text = string.Join(
            Environment.NewLine,
            ReadinessLine(apiHealthy, "healthz ok"),
            ReadinessLine(apiReady, "readyz ready"),
            ReadinessLine(credentialConfigured, "operator credential"),
            ReadinessLine(killClear, "kill switch clear"),
            ReadinessLine(healthAuditOk, $"health audit {overview.HealthAudit?.Status ?? "missing"}"),
            ReadinessLine(validationPassed, overview.Validation is null
                ? "validation checkpoint missing"
                : $"validation day {overview.Validation.CheckpointDay}"),
            "[…] Legal/tax/wallet: backend gated");
    }

    private void SetCollection(IEnumerable<MonitoringListItem> items, string emptyText = "No records yet")
    {
        // Page-specific top panels opt in after SetCollection has reset shared surfaces.
        EquityChartCard.Visibility = Visibility.Collapsed;
        PortfolioAnalyticsPanel.Visibility = Visibility.Collapsed;
        ControlRoomPanel.Visibility = Visibility.Collapsed;
        SignalIntelligencePanel.Visibility = Visibility.Collapsed;
        MarketBoardPanel.Visibility = Visibility.Collapsed;
        CalibrationLabPanel.Visibility = Visibility.Collapsed;
        JobsBoardPanel.Visibility = Visibility.Collapsed;
        SettingsCenterPanel.Visibility = Visibility.Collapsed;
        if (SupportsMarketDrilldown(_selectedDestination) && _selectedDrilldownItem?.MarketId is not null)
        {
            CollectionDetailColumn.Width = new GridLength(460);
            MarketDrilldownPanel.Visibility = Visibility.Visible;
        }
        else
        {
            HideMarketDrilldown();
        }
        _collectionAllItems = items.ToList();
        _collectionDefaultEmptyText = emptyText;
        ApplyCollectionSearch();
    }

    private void CollectionSearchBox_TextChanged(object sender, TextChangedEventArgs e)
    {
        _collectionSearchQuery = CollectionSearchBox.Text.Trim();
        ApplyCollectionSearch();
    }

    private void ApplyCollectionSearch()
    {
        IReadOnlyList<MonitoringListItem> source = _collectionAllItems;
        List<MonitoringListItem> visible = string.IsNullOrWhiteSpace(_collectionSearchQuery)
            ? source.ToList()
            : source.Where(MatchesCollectionSearch).ToList();
        CollectionList.ItemsSource = visible;
        CollectionEmptyText.Text = string.IsNullOrWhiteSpace(_collectionSearchQuery)
            ? _collectionDefaultEmptyText
            : $"No matches for \"{_collectionSearchQuery}\"";
        CollectionEmptyState.Visibility = visible.Count == 0
            ? Visibility.Visible
            : Visibility.Collapsed;
    }

    private bool MatchesCollectionSearch(MonitoringListItem item)
    {
        string query = _collectionSearchQuery;
        return ContainsSearch(item.Title, query)
            || ContainsSearch(item.Subtitle, query)
            || ContainsSearch(item.Detail, query)
            || ContainsSearch(item.Meta, query)
            || ContainsSearch(item.Status, query)
            || ContainsSearch(item.Accent, query)
            || ContainsSearch(item.MarketId?.ToString(CultureInfo.InvariantCulture), query);
    }

    private static bool ContainsSearch(string? value, string query) =>
        !string.IsNullOrWhiteSpace(value)
        && value.Contains(query, StringComparison.OrdinalIgnoreCase);

    private static bool SupportsMarketDrilldown(string destination) =>
        destination is "Markets" or "Signals" or "Positions";

    private void RenderMonitoringError(string error)
    {
        if (_selectedDestination == "Dashboard")
        {
            UpdatedStatusText.Text = "Displayed dashboard data is stale";
        }
        else
        {
            _collectionAllItems = [];
            _collectionDefaultEmptyText = "Monitoring data is unavailable";
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
        if (_selectedDestination != "Dashboard")
        {
            _collectionAllItems = [];
            _collectionDefaultEmptyText = "The local STRATUS API is unreachable";
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
        RootCauseBadgeText.Text = "—";
        RootCauseSubtitle.Text = "Root-cause data unavailable";
        BrierDiagnosticSummary.Text = "Brier diagnostics unavailable";
        ClvDiagnosticSummary.Text = "CLV diagnostics unavailable";
        BrierDiagnosticItems.ItemsSource = null;
        ClvDiagnosticItems.ItemsSource = null;
        EvidenceOpsBadgeText.Text = "UNAVAILABLE";
        EvidenceOpsBadge.Background = AccentBrush("warning");
        EvidenceResolutionText.Text = "--";
        EvidenceResolutionExactText.Text = "Official exact truth unavailable";
        EvidenceResolutionDetailText.Text = "Resolution QA unavailable";
        EvidenceResolutionFreshnessText.Text = "Freshness unavailable";
        EvidenceSettlementText.Text = "--";
        EvidenceSettlementDetailText.Text = "Settlement lifecycle unavailable";
        EvidenceSettlementFreshnessText.Text = "Polling freshness unavailable";
        EvidenceStorageText.Text = "--";
        EvidenceStorageProgress.Value = 0;
        EvidenceStorageDetailText.Text = "Capacity measurement unavailable";
        EvidenceOpsHintText.Text = "Evidence policy unavailable";
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
            if (!string.IsNullOrEmpty(CollectionSearchBox.Text))
            {
                _collectionSearchQuery = string.Empty;
                CollectionSearchBox.Text = string.Empty;
            }
        }

        bool dashboard = _selectedDestination == "Dashboard";
        bool alerts = _selectedDestination == "Alerts";
        DashboardView.Visibility = dashboard ? Visibility.Visible : Visibility.Collapsed;
        AlertsView.Visibility = alerts ? Visibility.Visible : Visibility.Collapsed;
        CollectionView.Visibility = dashboard || alerts
            ? Visibility.Collapsed
            : Visibility.Visible;
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
        JobsFilterContainer.Visibility =
            _selectedDestination == "Jobs" ? Visibility.Visible : Visibility.Collapsed;
        AlertsFilterContainer.Visibility =
            alerts ? Visibility.Visible : Visibility.Collapsed;
        AlertsAckThroughButton.Visibility =
            alerts ? Visibility.Visible : Visibility.Collapsed;
        CollectionSearchContainer.Visibility =
            dashboard || alerts ? Visibility.Collapsed : Visibility.Visible;
        ApiKeyButton.Visibility =
            _selectedDestination is "Settings" or "Alerts"
                ? Visibility.Visible
                : Visibility.Collapsed;

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
            "Calibration" => "Forecast calibration and station-selection trust evidence",
            "Jobs" => "Recent scheduler outcomes and execution durations",
            "Alerts" => "Durable local inbox and optional Telegram delivery status",
            "Live" => "Live orders, positions, and pending manual redemptions",
            "Operations" => "Runtime control, operator command audit, and validation state",
            "Settings" => "Local client configuration",
            _ => "STRATUS monitoring",
        };
    }

    private static string Pretty(string value) =>
        CultureInfo.InvariantCulture.TextInfo.ToTitleCase(value.Replace('_', ' '));

    private static bool IsProspectiveDual(string? protocol, string? scope) =>
        string.Equals(protocol, "clob-dual-v1", StringComparison.OrdinalIgnoreCase)
        && string.Equals(scope, "prospective", StringComparison.OrdinalIgnoreCase);

    private static string EvidenceScope(string? protocol, string? scope)
    {
        if (IsProspectiveDual(protocol, scope))
        {
            return "Prospective dual-token";
        }

        if (string.Equals(scope, "historical", StringComparison.OrdinalIgnoreCase)
            || string.Equals(protocol, "legacy-synthetic-yes-v0", StringComparison.OrdinalIgnoreCase))
        {
            return "Legacy historical";
        }

        if (string.Equals(scope, "all-protocol-history", StringComparison.OrdinalIgnoreCase))
        {
            return "All-protocol portfolio history";
        }

        return "Evidence scope unavailable";
    }

    private static string EvidenceScopeSuffix(string? protocol, string? scope) =>
        string.IsNullOrWhiteSpace(protocol) && string.IsNullOrWhiteSpace(scope)
            ? string.Empty
            : $" · {EvidenceScope(protocol, scope)}";

    private static string Usd(double? value) =>
        value.HasValue ? value.Value.ToString("$0.00", CultureInfo.InvariantCulture) : "—";

    private static string Percent(double? value) =>
        value.HasValue ? value.Value.ToString("P1", CultureInfo.InvariantCulture) : "—";

    private static string Number(double? value) =>
        value.HasValue ? value.Value.ToString("0.000", CultureInfo.InvariantCulture) : "—";

    private static string DeltaNumber(double? value) =>
        value.HasValue ? value.Value.ToString("+0.000;-0.000;0.000", CultureInfo.InvariantCulture) : "—";

    private static string Cents(double? value) =>
        value.HasValue ? $"{value.Value * 100:+0.00;-0.00;0.00}¢" : "—";

    private static string FormatBytes(double value)
    {
        string[] units = ["B", "KiB", "MiB", "GiB", "TiB"];
        double magnitude = Math.Max(0, value);
        int unit = 0;
        while (magnitude >= 1024 && unit < units.Length - 1)
        {
            magnitude /= 1024;
            unit++;
        }

        return $"{magnitude:0.##} {units[unit]}";
    }

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
