# STRATUS Operator Console UX v2

Status: proposed redesign.

## Problem

The current console is operationally useful but visually flat. It exposes data as static cards and list rows. That is enough for monitoring, but it does not feel like a professional trading cockpit because it lacks:

- a primary visual market/portfolio canvas;
- interactive charts;
- drill-down panels;
- watchlists and filters that change the whole view;
- live visual feedback when values change;
- dense but readable trading layout;
- clear hierarchy between “observe”, “decide”, and “act”.

The product direction should shift from “admin summary” to “weather-market trading terminal”.

## Benchmarked patterns

References checked:

- TradingView: chart-first workflow, screeners, alerts, calendars, large market universe.
  <https://www.tradingview.com/features/>
- Interactive Brokers TWS Mosaic: customizable single-window workspace combining trading, charting, order management, portfolio, watchlists, scanners, news, and monitors.
  <https://www.interactivebrokers.com/en/trading/tws.php>
- Coinbase Advanced: TradingView-powered advanced charting, watchlists, technical overlays.
  <https://www.coinbase.com/advanced-trade>
- Robinhood Legend: desktop trading platform positioned around customization, speed, analysis, and execution.
  <https://robinhood.com/us/en/legend/>
- Grafana: pan/zoom time-series interaction and event annotations.
  <https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/visualizations/time-series/>
- Datadog dashboards: widgets, tabs, saved views, template variables, contextual links.
  <https://docs.datadoghq.com/dashboards/widgets/>
  <https://docs.datadoghq.com/dashboards/template_variables/>
- Kalshi orderbook: prediction-market-specific orderbook with bid/ask quantities and prices.
  <https://help.kalshi.com/en/articles/13823828-the-orderbook>

Extracted requirements:

1. A trading app needs one dominant chart/analysis region, not equal-weight cards.
2. Lists should behave like watchlists/scanners: sortable, filterable, clickable, and stateful.
3. A selected market/position should open a detail panel with chart, probability history, price history, order book, and rationale.
4. Portfolio/risk must be visual: equity curve, drawdown, exposure heatmap, realized/unrealized PnL, loss-limit progress.
5. Actions must sit next to context, but dangerous actions need gated dialogs and audit trail.
6. Time range, station, mode, market kind, and confidence should be global filters that affect charts and lists.
7. Live changes should be visible: pulse, changed-since-last-refresh accents, and event timeline.

## Target information architecture

Keep the left `NavigationView`, but change page semantics:

1. **Cockpit**
   Main landing page. This replaces the current “System overview” as the primary work surface.

2. **Markets**
   Watchlist/scanner of tradable markets, with drill-down.

3. **Signals**
   Signal lab: generated candidates, skipped reasons, model-vs-market gap.

4. **Portfolio**
   Positions, realized/unrealized PnL, equity, exposure, closed trades.

5. **Risk & Live**
   Runtime mode, kill switch, validation gate, live readiness, command audit.

6. **Data Health**
   Jobs, collectors, station freshness, health audit, source delays.

7. **Settings**
   API key, refresh cadence, dev/prod mode indicators.

This reduces “everything is a page of cards” and groups by operator workflow.

## Cockpit layout

Desktop wide layout:

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ STRATUS Weather Trading Cockpit                    PAPER · Ready · 10s refresh│
├──────────────┬────────────────────────────────────────────┬──────────────────┤
│ Watchlist    │ Main chart canvas                          │ Risk rail         │
│              │                                            │                  │
│ EGLC High    │ [Price vs model probability over time]      │ Bankroll          │
│ LFPB Low     │ [Market price, model probability, edge]     │ Equity today      │
│ KLGA High    │                                            │ Open exposure     │
│              │ Time range: 1h 6h 24h 7d                   │ Drawdown          │
│              │ Station: All/KLGA/EGLC/LFPB                │ Gate status       │
├──────────────┴────────────────────────────────────────────┴──────────────────┤
│ Opportunity scanner: edge, confidence, spread, depth, freshness, action       │
└──────────────────────────────────────────────────────────────────────────────┘
```

Core widgets:

- **Hero equity/risk strip**
  - Bankroll
  - Realized PnL
  - Unrealized PnL
  - Open exposure
  - Daily/weekly loss-limit progress
  - Validation gate progress

- **Main market chart**
  - market midpoint / YES price over time;
  - model probability over time;
  - edge as shaded band;
  - signal markers;
  - paper/live fill markers;
  - settlement marker when resolved.

- **Weather model panel**
  - ECMWF/GFS/ICON/GEM/GEFS components;
  - EMOS adjusted probability;
  - station bias/sigma;
  - bucket boundary.

- **Opportunity scanner**
  - rows grouped by station/date;
  - sortable columns: edge, confidence, spread, depth, age, recommended size;
  - row click updates the main chart and right detail rail.

## Market drill-down

Clicking any market opens a right-side details rail or a split view:

Sections:

1. Market header
   - station, date, high/low, bucket, resolution source;
   - current market probability;
   - model probability;
   - edge;
   - liquidity and spread status.

2. Chart tabs
   - Price
   - Probability
   - Edge
   - Weather forecast members
   - Trade history

3. Orderbook
   - YES bid/ask ladder;
   - NO bid/ask ladder;
   - executable depth within 1 cent;
   - stale-book warning.

4. Signal rationale
   - accepted or skipped;
   - exact skip reasons;
   - Kelly raw / capped / final;
   - exposure budget remaining.

5. Actions
   - verify market;
   - run price refresh;
   - generate signal for this market;
   - live actions disabled unless all gates pass.

## Portfolio page

The current Positions page is closer, but still list-first. Target layout:

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Portfolio                         Open Closed All     Station  Kind  Date     │
├──────────────────────────────────────────────┬───────────────────────────────┤
│ Equity curve / bankroll curve                │ Exposure heatmap               │
│ realized + unrealized                        │ station × date                 │
├──────────────────────────────────────────────┴───────────────────────────────┤
│ Positions table/card hybrid                                                   │
│ Market | Side | Entry | Mark | Size | uPnL | rPnL | CLV | Age | State         │
└──────────────────────────────────────────────────────────────────────────────┘
```

Required visual upgrades:

- equity curve above the list;
- drawdown overlay;
- PnL badges with consistent red/green semantics;
- station/date exposure heatmap;
- grouped position rows by market;
- hover/select row opens mini chart in side rail;
- closed trades show entry marker, close marker, CLV marker.

## Risk & Live page

This should not be a plain card list. It should be a control room:

- left: runtime state and kill switch;
- center: validation gates as checklist/timeline;
- right: command audit log;
- bottom: live order monitor.

Live mode request should visually show:

```text
Live readiness
[✓] Paper gate passed
[✕] Legal approval
[✕] Tax approval
[✕] Wallet configured
[✓] No active kill switch
[ ] Second confirmation inside 5 minutes
```

The backend remains authoritative; the UI only visualizes gates and submits commands.

## Data Health page

Convert Jobs/Calibration health into an observability dashboard:

- collector freshness timeline;
- station coverage heatmap;
- failed jobs/event stream;
- source latency cards;
- “last good run” markers;
- health audit issues with severity colors.

This should borrow from Grafana/Datadog rather than look like a settings page.

## Interaction model

Global controls:

- Time range: 1h, 6h, 24h, 7d, 30d.
- Mode: paper/live/all.
- Station: KLGA/EGLC/LFPB/all.
- Market kind: high/low/all.
- Confidence: A/B/C/all.
- Search: market id, station, date.

Interactions:

- click row -> selected context updates charts and detail rail;
- hover chart -> tooltip with time, market price, model probability, edge;
- click chart signal marker -> opens signal detail;
- click fill marker -> opens position detail;
- changed values pulse for one refresh cycle;
- stale values dim and show timestamp;
- dangerous actions use ContentDialog + command audit result.

## Visual language

Use a darker professional terminal theme, but keep WinUI/Fluent constraints:

- base: Mica/dark layered surface;
- cards: fewer borders, more section typography and spacing;
- accent colors:
  - cyan/blue: selected market / active data;
  - green: positive PnL / passed gate;
  - red: negative PnL / blocked gate / live danger;
  - amber: stale / warning / gate pending;
  - violet: model probability / ensemble;
- charts:
  - market price: cyan;
  - model probability: violet;
  - edge: green/red fill band;
  - fills: small triangle markers;
  - settlements: vertical rule.

Avoid:

- every page as one vertical list of equal cards;
- badges floating in empty space;
- huge blank right side on wide monitors;
- raw IDs as primary visual content;
- dangerous live controls looking like ordinary buttons.

## Implementation phases

### Phase 1 — Make it feel like a cockpit

No backend schema changes required.

- Rename Dashboard to Cockpit.
- Add top KPI strip: bankroll, realized/unrealized PnL, open exposure, gate state.
- Add large equity chart on Cockpit.
- Add opportunity scanner under chart.
- Add global filter row.
- Add selected-market side rail with current model/market/edge details.

### Phase 2 — Real interactive charts

Likely needs new endpoints or richer existing endpoints.

- `/api/v1/market-timeseries?market_id=...`
- `/api/v1/probability-timeseries?market_id=...`
- `/api/v1/orderbook?market_id=...`
- chart pan/zoom/time ranges;
- signal/fill annotations.

### Phase 3 — Portfolio analytics

- bankroll curve;
- drawdown chart;
- exposure heatmap;
- station/date grouped position view;
- CSV export.

### Phase 4 — Live control room

- gate checklist visualization;
- live order monitor;
- command audit event stream;
- Windows toast notifications for critical events.

## Acceptance criteria

The console is not “professional” until:

- first screen shows at least one meaningful chart without clicking;
- every market/position row is clickable and changes a detail panel;
- at least one chart has hover tooltips;
- risk state is visible without reading paragraphs;
- stale/fresh/live-changing data has visual state;
- Actions are contextual and audited;
- wide windows use 2–3 columns instead of a centered list;
- narrow windows gracefully collapse to one column.

