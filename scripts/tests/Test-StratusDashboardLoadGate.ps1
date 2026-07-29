[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptPath = [IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot '..\Invoke-StratusDashboardLoadGate.ps1')
)
if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
    throw "Missing load-gate harness: $scriptPath"
}

$tokens = $null
$parseErrors = $null
[void][Management.Automation.Language.Parser]::ParseFile(
    $scriptPath,
    [ref]$tokens,
    [ref]$parseErrors
)
if ($parseErrors.Count -ne 0) {
    $details = $parseErrors |
        ForEach-Object { "$($_.Extent.StartLineNumber): $($_.Message)" }
    throw "PowerShell syntax errors:`n$($details -join [Environment]::NewLine)"
}

$source = Get-Content -LiteralPath $scriptPath -Raw
$forbiddenMutations = @(
    'PostAsync',
    'PostAsJsonAsync',
    'Invoke-RestMethod\s+[^\r\n]*-Method\s+Post',
    'Invoke-WebRequest\s+[^\r\n]*-Method\s+Post'
)
foreach ($pattern in $forbiddenMutations) {
    if ($source -match $pattern) {
        throw "Read-only harness contains a forbidden mutation pattern: $pattern"
    }
}

$hostExecutable = (Get-Process -Id $PID).Path
$validationOutput = & $hostExecutable `
    -NoLogo `
    -NoProfile `
    -NonInteractive `
    -File $scriptPath `
    -ValidateOnly
if ($LASTEXITCODE -ne 0) {
    throw "Load-gate validation mode exited with $LASTEXITCODE."
}
$validation = ($validationOutput -join [Environment]::NewLine) | ConvertFrom-Json

if (-not $validation.validated) {
    throw 'Load-gate validation contract did not report validated=true.'
}
if (
    $validation.schema_version -ne 2 -or
    $validation.gate -ne 'stratus_dashboard_http_load' -or
    -not $validation.http_subgate_only -or
    $null -ne $validation.http_passed -or
    -not $validation.requires_external_runtime_observation -or
    $null -ne $validation.production_gate_passed
) {
    throw 'Load-gate validation contract does not identify an HTTP-only subgate.'
}
if ($validation.request_method -ne 'GET' -or $validation.mutation_requests -ne 0) {
    throw 'Load-gate validation contract is not read-only.'
}
if ($validation.max_concurrent_get -gt 3) {
    throw 'Load-gate validation contract exceeds three concurrent GET requests.'
}
if ($validation.scheduling_policy -ne 'work_conserving') {
    throw 'Load-gate validation contract does not use the work-conserving queue.'
}
if (
    $validation.cadence_seconds.core -ne 10 -or
    $validation.cadence_seconds.evidence_execution -ne 60 -or
    $validation.cadence_seconds.accuracy_provenance -ne 300
) {
    throw 'Load-gate cadence differs from the Cockpit contract.'
}
if (
    $validation.cold_refresh.core_requests -ne 9 -or
    $validation.cold_refresh.evidence_execution_requests -ne 8 -or
    $validation.cold_refresh.accuracy_provenance_requests -ne 6 -or
    $validation.cold_refresh.total_requests -ne 23
) {
    throw 'Cold-refresh endpoint inventory is incomplete.'
}

$endpointNames = @($validation.endpoints | ForEach-Object { $_.Name })
$requiredEndpoints = @(
    'healthz',
    'readyz',
    'overview',
    'paper_equity',
    'edge_report',
    'execution_cost',
    'evidence_continuity',
    'brier_report',
    'accuracy_overview',
    'forecast_provenance',
    'official_resolution_sources'
)
foreach ($endpoint in $requiredEndpoints) {
    if ($endpointNames -notcontains $endpoint) {
        throw "Load-gate inventory is missing $endpoint."
    }
}

$dotSourceValidation = . $scriptPath -ValidateOnly
if ([string]::IsNullOrWhiteSpace(($dotSourceValidation -join ''))) {
    throw 'Dot-sourced validation mode returned no contract.'
}
$handler = [Net.Http.HttpClientHandler]::new()
try {
    $handler.MaxConnectionsPerServer = 3
    if ($handler.MaxConnectionsPerServer -ne 3) {
        throw 'HttpClientHandler did not retain the three-connection limit.'
    }
}
finally {
    $handler.Dispose()
}
if (-not (Test-ReadyFailClosed -Body '{"status":"not_ready","reasons":["kill_switch"]}')) {
    throw 'Expected kill-switch readiness response was rejected.'
}
if (-not (Test-ReadyFailClosed -Body '{"status":"not_ready","reasons":["kill_switch","project_halted"]}')) {
    throw 'Expected halted kill-switch readiness response was rejected.'
}
if (-not (Test-ReadyFailClosed -Body '{"status":"not_ready","reasons":["kill_switch","health_audit_stale"]}')) {
    throw 'Expected worker-off stale-audit readiness response was rejected.'
}
if (-not (Test-ReadyFailClosed -Body '{"status":"not_ready","reasons":["kill_switch","health_audit_missing"]}')) {
    throw 'Expected worker-off missing-audit readiness response was rejected.'
}
if (-not (Test-ReadyHttpResponse -StatusCode 503 -Body '{"status":"not_ready","reasons":["kill_switch"]}')) {
    throw 'Expected staged readyz HTTP 503 response was rejected.'
}
if (Test-ReadyHttpResponse -StatusCode 200 -Body '{"status":"not_ready","reasons":["kill_switch"]}') {
    throw 'readyz HTTP 200 was accepted even though the staged API must remain fail closed.'
}
if (Test-ReadyHttpResponse -StatusCode 200 -Body '{"status":"ready","reasons":[]}') {
    throw 'readyz HTTP 200 ready response was accepted during fail-closed staging.'
}
$unexpectedReadinessBodies = @(
    '{"status":"not_ready","reasons":["database_unavailable"]}',
    '{"status":"not_ready","reasons":["health_audit_stale"]}',
    '{"status":"not_ready","reasons":["kill_switch","health_audit_critical"]}',
    '{"status":"not_ready","reasons":["kill_switch","health_audit_blocking"]}',
    '{"status":"not_ready","reasons":["kill_switch","unknown_reason"]}',
    '{"status":"ready","reasons":["kill_switch"]}',
    'not-json'
)
foreach ($body in $unexpectedReadinessBodies) {
    if (Test-ReadyFailClosed -Body $body) {
        throw "Unexpected readiness response was accepted: $body"
    }
}

if ($source -match '\bWaitAll\b') {
    throw 'Load-gate queue still contains a fixed batch barrier.'
}
foreach ($queueToken in @(
    '[Threading.Tasks.Task]::WaitAny',
    'function Start-GetRequest',
    'function Complete-GetRequest'
)) {
    if (-not $source.Contains($queueToken)) {
        throw "Load-gate work-conserving queue is missing: $queueToken"
    }
}

if (-not ('StratusDashboardLoadGateTestHandler' -as [type])) {
    Add-Type -ReferencedAssemblies @('System.Net.Http.dll') -TypeDefinition @'
using System;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;

public sealed class StratusDashboardLoadGateTestHandler : HttpMessageHandler
{
    private int _active;
    private int _maximumActive;
    private readonly ConcurrentDictionary<string, long> _started;
    private readonly ConcurrentDictionary<string, long> _completed;

    public StratusDashboardLoadGateTestHandler()
    {
        _started = new ConcurrentDictionary<string, long>();
        _completed = new ConcurrentDictionary<string, long>();
    }

    public ConcurrentDictionary<string, long> Started
    {
        get { return _started; }
    }

    public ConcurrentDictionary<string, long> Completed
    {
        get { return _completed; }
    }

    public int MaximumActive
    {
        get { return _maximumActive; }
    }

    protected override async Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request,
        CancellationToken cancellationToken)
    {
        string path = request.RequestUri.AbsolutePath;
        Started[path] = Stopwatch.GetTimestamp();
        int active = Interlocked.Increment(ref _active);
        int observed;
        while (active > (observed = _maximumActive))
        {
            if (Interlocked.CompareExchange(ref _maximumActive, active, observed) == observed)
            {
                break;
            }
        }

        try
        {
            await Task.Delay(path == "/slow" ? 250 : 20, cancellationToken);
            return new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent("{}")
            };
        }
        finally
        {
            Completed[path] = Stopwatch.GetTimestamp();
            Interlocked.Decrement(ref _active);
        }
    }
}
'@
}

$queueHandler = [StratusDashboardLoadGateTestHandler]::new()
$queueClient = [Net.Http.HttpClient]::new($queueHandler, $true)
try {
    $queueClient.BaseAddress = [Uri]'http://127.0.0.1/'
    $queueClient.Timeout = [Threading.Timeout]::InfiniteTimeSpan
    $queueResults = [Collections.Generic.List[object]]::new()
    $queueEndpoints = @(
        [pscustomobject]@{ Tier = 'test'; Name = 'slow'; Path = 'slow' }
        [pscustomobject]@{ Tier = 'test'; Name = 'fast_one'; Path = 'fast-one' }
        [pscustomobject]@{ Tier = 'test'; Name = 'fast_two'; Path = 'fast-two' }
        [pscustomobject]@{ Tier = 'test'; Name = 'fourth'; Path = 'fourth' }
        [pscustomobject]@{ Tier = 'test'; Name = 'fifth'; Path = 'fifth' }
    )
    Invoke-EndpointTier `
        -Client $queueClient `
        -Endpoints $queueEndpoints `
        -Phase 'self_test' `
        -Iteration 1 `
        -ScheduledOffsetSeconds 0 `
        -Concurrency 3 `
        -RequestTimeoutSeconds 1 `
        -Results $queueResults

    if ($queueResults.Count -ne 5 -or @($queueResults | Where-Object accepted).Count -ne 5) {
        throw 'Work-conserving queue self-test did not complete all five GETs.'
    }
    if ($queueHandler.MaximumActive -gt 3) {
        throw 'Work-conserving queue exceeded its three-request concurrency limit.'
    }
    if ($queueHandler.Started['/fourth'] -ge $queueHandler.Completed['/slow']) {
        throw 'Fourth GET waited for the slow first batch instead of refilling a free slot.'
    }
}
finally {
    $queueClient.Dispose()
}

[pscustomobject]@{
    script = $scriptPath
    syntax_valid = $true
    read_only = $true
    cadence_valid = $true
    endpoint_inventory_valid = $true
    readyz_policy_valid = $true
    work_conserving_queue_valid = $true
    http_subgate_contract_valid = $true
    validated = $true
} | ConvertTo-Json -Compress
