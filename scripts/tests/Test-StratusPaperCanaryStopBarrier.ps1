[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$barrierPath = [IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot '..\Invoke-StratusPaperCanaryStopBarrier.ps1')
)
$tokens = $null
$parseErrors = $null
[void][Management.Automation.Language.Parser]::ParseFile(
    $barrierPath,
    [ref]$tokens,
    [ref]$parseErrors
)
if ($parseErrors.Count -ne 0) {
    throw 'PAPER canary STOP barrier has PowerShell syntax errors.'
}

$source = Get-Content -LiteralPath $barrierPath -Raw
foreach ($required in @(
    '$BarrierTimeoutSeconds = 20',
    '$NativeTimeoutSeconds = 3',
    '$StopRequestId = "paper-canary-stop-$RunId"',
    '$StopActor = ''codex-paper-canary''',
    '$StopReason = ''STRATUS PAPER canary terminal fail-closed stop.''',
    'Invoke-RestMethod -Uri $Uri -Method Post',
    "Get-Command -Name 'docker' -CommandType Application",
    '$start.FileName = [string]$dockerCommand.Source',
    "'X-API-Key'",
    "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;",
    "SET LOCAL statement_timeout = '3s';",
    'PGPASSWORD="$POSTGRES_PASSWORD" PGAPPNAME="stratus-paper-canary-stop-barrier" exec psql',
    "'orders', 'positions', 'intents', 'fill_legs', 'enrollments', 'outcomes'",
    'matching_lease_terminal = $true'
)) {
    if (-not $source.Contains($required)) {
        throw "PAPER canary STOP barrier lacks contract: $required"
    }
}

$firstPostIndex = $source.IndexOf(
    '$firstResponse = Invoke-AuthenticatedStopPost -Uri $stopUri'
)
$rootDiagnosticIndex = $source.IndexOf(
    '$absoluteRoot = [IO.Path]::GetFullPath($Root)'
)
$sqlDiagnosticIndex = $source.IndexOf(
    '$state = Get-StopState -WorkingDirectory $absoluteRoot'
)
if (
    $firstPostIndex -lt 0 -or
    $rootDiagnosticIndex -le $firstPostIndex -or
    $sqlDiagnosticIndex -le $rootDiagnosticIndex
) {
    throw 'Authenticated STOP is not ordered before every diagnostic.'
}

foreach ($forbidden in @(
    '(?im)\bdocker\s+(start|stop|restart|rm|remove|up|run|network)\b',
    '(?im)\bdocker\s+compose\b',
    '(?im)\b(Start|Stop|Restart)-Service\b',
    '(?im)\bSet-Content\b',
    '(?im)\bAdd-Content\b',
    '(?im)\bOut-File\b'
)) {
    if ($source -match $forbidden) {
        throw "PAPER canary STOP barrier contains forbidden mutation: $forbidden"
    }
}

function New-StopItem {
    param(
        [Parameter(Mandatory = $true)][string] $RunId,
        [string] $Status = 'succeeded',
        [string] $Actor = 'codex-paper-canary'
    )

    return [pscustomobject]@{
        id = 91
        request_id = "paper-canary-stop-$RunId"
        command = 'stop'
        actor = $Actor
        reason = 'STRATUS PAPER canary terminal fail-closed stop.'
        payload = [pscustomobject]@{ venue = 'polymarket' }
        status = $Status
        created_at = '2026-08-14T12:00:00Z'
        executed_at = if ($Status -eq 'succeeded') {
            '2026-08-14T12:00:00.100000Z'
        }
        else {
            $null
        }
    }
}

function New-StopState {
    param(
        [Parameter(Mandatory = $true)][string] $RunId,
        [Parameter(Mandatory = $true)][string] $ResumeRequestId,
        [ValidateSet('safe', 'partial', 'pending')]
        [string] $Mode = 'safe',
        [switch] $LeaseAbsent
    )

    $commandStatus = if ($Mode -eq 'pending') { 'pending' } else { 'succeeded' }
    $matchingLeases = [object[]]@()
    if (-not $LeaseAbsent) {
        $matchingLeases = @([pscustomobject][ordered]@{
            id = 8
            lease_id = "paper-lease-$RunId"
            run_id = $RunId
            resume_request_id = $ResumeRequestId
            direct_stop_request_id = "paper-canary-stop-$RunId"
            state = if ($Mode -eq 'pending') { 'active' } else { 'revoked_stopped' }
            stop_command_id = if ($Mode -eq 'pending') { $null } else { 91 }
        })
    }
    return [ordered]@{
        database_now = '2026-08-14T12:00:01Z'
        command = New-StopItem -RunId $RunId -Status $commandStatus
        matching_leases = $matchingLeases
        active_canary_leases = if ($Mode -eq 'pending') { 1 } else { 0 }
        resume_status = if ($Mode -eq 'pending') { 'pending' } else { 'succeeded' }
        global_control = [ordered]@{
            mode = 'paper'
            kill_switch = $Mode -ne 'pending'
            live_confirmation_until = $null
        }
        polymarket = [ordered]@{
            enabled = $true
            mode = 'paper'
            kill_switch = $Mode -ne 'pending'
            live_confirmation_until = $null
        }
        binance = [ordered]@{
            enabled = $true
            mode = 'backtest'
            kill_switch = $true
            live_confirmation_until = $null
        }
        pending_commands = if ($Mode -eq 'pending') { 2 } else { 0 }
        pending_signals = if ($Mode -eq 'partial') { 1 } elseif ($Mode -eq 'pending') { 2 } else { 0 }
        live = [ordered]@{
            orders = 0
            positions = 0
            intents = 0
            fill_legs = 0
            enrollments = 0
            outcomes = 0
        }
    }
}

function Assert-Throws {
    param(
        [Parameter(Mandatory = $true)][string] $Label,
        [Parameter(Mandatory = $true)][string] $ExpectedMessage,
        [Parameter(Mandatory = $true)][scriptblock] $Action
    )

    $caught = $null
    try {
        [void](& $Action)
    }
    catch {
        $caught = $_
    }
    if ($null -eq $caught) {
        throw "$Label unexpectedly succeeded."
    }
    $message = $caught.Exception.GetBaseException().Message
    if ($message -notlike "*$ExpectedMessage*") {
        throw "$Label failed for the wrong reason: $message"
    }
}

$temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) (
    'stratus-stop-barrier-test-' + [Guid]::NewGuid().ToString('N')
)
[void][IO.Directory]::CreateDirectory($temporaryRoot)
$fakeBin = Join-Path $temporaryRoot 'bin'
[void][IO.Directory]::CreateDirectory($fakeBin)
$eventLog = Join-Path $temporaryRoot 'events.log'
$goodStatePath = Join-Path $temporaryRoot 'good.json'
$absentStatePath = Join-Path $temporaryRoot 'absent.json'
$partialStatePath = Join-Path $temporaryRoot 'partial.json'
$pendingStatePath = Join-Path $temporaryRoot 'pending.json'
$runningOnWindows = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
$dockerFileName = if ($runningOnWindows) { 'docker.exe' } else { 'docker' }
$dockerPath = Join-Path $fakeBin $dockerFileName
$runId = '0123456789abcdef0123456789abcdef'
$resumeRequestId = 'resume-0123456789abcdef'
$postgresContainerId = 'a' * 64
$secretText = 'stop-barrier-test-key'
$secureKey = ConvertTo-SecureString $secretText -AsPlainText -Force
$utf8 = [Text.UTF8Encoding]::new($false)

[IO.File]::WriteAllText(
    $goodStatePath,
    ((New-StopState -RunId $runId -ResumeRequestId $resumeRequestId `
        -Mode safe) | ConvertTo-Json -Depth 12 -Compress),
    $utf8
)
[IO.File]::WriteAllText(
    $absentStatePath,
    ((New-StopState -RunId $runId -ResumeRequestId $resumeRequestId `
        -Mode safe -LeaseAbsent) | ConvertTo-Json -Depth 12 -Compress),
    $utf8
)
[IO.File]::WriteAllText(
    $partialStatePath,
    ((New-StopState -RunId $runId -ResumeRequestId $resumeRequestId `
        -Mode partial) | ConvertTo-Json -Depth 12 -Compress),
    $utf8
)
[IO.File]::WriteAllText(
    $pendingStatePath,
    ((New-StopState -RunId $runId -ResumeRequestId $resumeRequestId `
        -Mode pending) | ConvertTo-Json -Depth 12 -Compress),
    $utf8
)
$mockTypeName = 'StratusStopBarrierDockerMock' + [Guid]::NewGuid().ToString('N')
$dockerSource = @"
using System;
using System.IO;

public static class $mockTypeName
{
    public static int Main(string[] args)
    {
        Console.In.ReadToEnd();
        File.AppendAllText(
            Environment.GetEnvironmentVariable("STRATUS_STOP_BARRIER_EVENT_LOG"),
            "DOCKER " + string.Join(" ", args) + Environment.NewLine
        );
        Console.Write(File.ReadAllText(
            Environment.GetEnvironmentVariable("STRATUS_STOP_BARRIER_DOCKER_JSON")
        ));
        return 0;
    }
}
"@
if ($runningOnWindows) {
    $dockerSourcePath = Join-Path $temporaryRoot 'DockerMock.cs'
    [IO.File]::WriteAllText($dockerSourcePath, $dockerSource, $utf8)
    $compiler = @(
        (Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'),
        (Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe')
    ) | Where-Object {
        Test-Path -LiteralPath $_ -PathType Leaf
    } | Select-Object -First 1
    if ([string]::IsNullOrWhiteSpace([string]$compiler)) {
        throw 'Could not find the Windows C# compiler for the Docker fixture.'
    }
    $compilerOutput = @(
        & $compiler '/nologo' '/target:exe' "/out:$dockerPath" $dockerSourcePath 2>&1
    )
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $dockerPath -PathType Leaf)) {
        throw (
            'Could not compile the Windows Docker fixture: ' +
            ($compilerOutput -join [Environment]::NewLine)
        )
    }
}
else {
    $dockerShell = @'
#!/bin/sh
cat >/dev/null
{
  printf 'DOCKER'
  for argument in "$@"; do
    printf ' %s' "$argument"
  done
  printf '\n'
} >> "$STRATUS_STOP_BARRIER_EVENT_LOG"
cat "$STRATUS_STOP_BARRIER_DOCKER_JSON"
'@
    $dockerShell = $dockerShell.Replace("`r`n", "`n")
    [IO.File]::WriteAllText($dockerPath, $dockerShell, $utf8)
    $dockerBytes = [IO.File]::ReadAllBytes($dockerPath)
    $expectedShebang = [Text.Encoding]::ASCII.GetBytes("#!/bin/sh`n")
    if (
        $dockerBytes.Length -lt $expectedShebang.Length -or
        $dockerBytes -contains [byte]0x0D
    ) {
        throw 'Unix Docker fixture does not have strict LF-only script bytes.'
    }
    for ($index = 0; $index -lt $expectedShebang.Length; $index++) {
        if ($dockerBytes[$index] -ne $expectedShebang[$index]) {
            throw 'Unix Docker fixture does not have an executable /bin/sh shebang.'
        }
    }
    & chmod 700 $dockerPath
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not make the Unix Docker fixture executable.'
    }
}

$originalPath = $env:PATH
$env:PATH = "$fakeBin$([IO.Path]::PathSeparator)$originalPath"
$resolvedDocker = Get-Command -Name 'docker' -CommandType Application `
    -ErrorAction Stop | Select-Object -First 1
$pathComparison = if ($runningOnWindows) {
    [StringComparison]::OrdinalIgnoreCase
}
else {
    [StringComparison]::Ordinal
}
if (
    $null -eq $resolvedDocker -or
    -not [string]::Equals(
        [IO.Path]::GetFullPath([string]$resolvedDocker.Source),
        [IO.Path]::GetFullPath($dockerPath),
        $pathComparison
    )
) {
    throw 'Docker fixture was not the first application resolved from PATH.'
}
$env:STRATUS_STOP_BARRIER_EVENT_LOG = $eventLog
$env:STRATUS_STOP_BARRIER_DOCKER_JSON = $goodStatePath
$global:StratusStopBarrierScenario = 'safe'
$global:StratusStopBarrierHttpCalls = 0
$global:StratusStopBarrierBodies = [Collections.Generic.List[string]]::new()
$global:StratusStopBarrierPartialState = $partialStatePath
$global:StratusStopBarrierGoodState = $goodStatePath
$global:StratusStopBarrierExpectedRunId = $runId
$global:StratusStopBarrierExpectedSecret = $secretText

function global:Invoke-RestMethod {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][Uri] $Uri,
        [Parameter(Mandatory = $true)][string] $Method,
        [Parameter(Mandatory = $true)][hashtable] $Headers,
        [Parameter(Mandatory = $true)][string] $ContentType,
        [Parameter(Mandatory = $true)][string] $Body,
        [Parameter(Mandatory = $true)][int] $TimeoutSec,
        [Parameter(Mandatory = $true)][int] $MaximumRedirection
    )

    $global:StratusStopBarrierHttpCalls += 1
    $global:StratusStopBarrierBodies.Add($Body)
    [IO.File]::AppendAllText(
        $env:STRATUS_STOP_BARRIER_EVENT_LOG,
        "POST $($Uri.AbsoluteUri)`r`n",
        [Text.Encoding]::ASCII
    )
    if (
        $Uri.AbsoluteUri -cne 'http://127.0.0.1:8001/api/v1/emergency-stop' -or
        $Method -cne 'Post' -or
        $ContentType -cne 'application/json' -or
        $TimeoutSec -ne 3 -or
        $MaximumRedirection -ne 0 -or
        $Headers.Count -ne 1 -or
        [string]$Headers['X-API-Key'] -cne $global:StratusStopBarrierExpectedSecret
    ) {
        throw 'HTTP mock observed an unsafe authenticated request contract.'
    }

    $bodyObject = $Body | ConvertFrom-Json
    if (
        @($bodyObject.PSObject.Properties).Count -ne 5 -or
        [string]$bodyObject.request_id -cne
            "paper-canary-stop-$global:StratusStopBarrierExpectedRunId" -or
        [string]$bodyObject.command -cne 'stop' -or
        [string]$bodyObject.actor -cne 'codex-paper-canary' -or
        [string]$bodyObject.reason -cne
            'STRATUS PAPER canary terminal fail-closed stop.' -or
        @($bodyObject.payload.PSObject.Properties).Count -ne 1 -or
        [string]$bodyObject.payload.venue -cne 'polymarket'
    ) {
        throw 'HTTP mock observed an unsafe emergency STOP body.'
    }

    if ($global:StratusStopBarrierScenario -eq 'hostile') {
        return [pscustomobject]@{
            created = $true
            idempotent = $false
            item = New-StopItem `
                -RunId $global:StratusStopBarrierExpectedRunId `
                -Actor 'hostile-actor'
        }
    }
    $created = (
        $global:StratusStopBarrierScenario -ne 'replay' -and
        $global:StratusStopBarrierHttpCalls -eq 1
    )
    if (
        $global:StratusStopBarrierScenario -eq 'partial' -and
        $global:StratusStopBarrierHttpCalls -ge 2
    ) {
        $env:STRATUS_STOP_BARRIER_DOCKER_JSON = (
            $global:StratusStopBarrierGoodState
        )
    }
    return [pscustomobject]@{
        created = $created
        idempotent = -not $created
        expired_signals = 0
        cancel_requested_live_orders = 0
        item = New-StopItem -RunId $global:StratusStopBarrierExpectedRunId
    }
}

function global:Start-Sleep {
    param([int] $Milliseconds)

    # Make the bounded-attempt timeout fixture deterministic and fast.
}

function Invoke-Barrier {
    return & $barrierPath `
        -Root $temporaryRoot `
        -RunId $runId `
        -ResumeRequestId $resumeRequestId `
        -PostgresContainerId $postgresContainerId `
        -SafetyApiKey $secureKey
}

function Reset-Fixture {
    param(
        [Parameter(Mandatory = $true)][string] $Scenario,
        [Parameter(Mandatory = $true)][string] $StatePath
    )

    $global:StratusStopBarrierScenario = $Scenario
    $global:StratusStopBarrierHttpCalls = 0
    $global:StratusStopBarrierBodies.Clear()
    $env:STRATUS_STOP_BARRIER_DOCKER_JSON = $StatePath
    [IO.File]::WriteAllText($eventLog, '', [Text.Encoding]::ASCII)
}

function Assert-StopFirstAndNoDockerMutation {
    $events = @(
        Get-Content -LiteralPath $eventLog |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    if ($events.Count -eq 0 -or $events[0] -notlike 'POST *') {
        throw 'Authenticated emergency STOP was not the first external event.'
    }
    foreach ($event in $events) {
        if (
            $event -like 'DOCKER *' -and
            (
                $event -notmatch '^DOCKER exec -i [0-9a-f]{64} sh -lc ' -or
                $event -match '(?i)^DOCKER (start|stop|restart|rm|remove|up|run|network)\b'
            )
        ) {
            throw "Docker mock observed a forbidden lifecycle command: $event"
        }
    }
}

try {
    Reset-Fixture -Scenario 'partial' -StatePath $partialStatePath
    $partial = Invoke-Barrier | ConvertFrom-Json
    if (
        -not [bool]$partial.verified -or
        [string]$partial.run_id -cne $runId -or
        [string]$partial.stop_request_id -cne "paper-canary-stop-$runId" -or
        [string]$partial.request_id -cne [string]$partial.stop_request_id -or
        [string]$partial.resume_request_id -cne $resumeRequestId -or
        [string]$partial.postgres_container_id -cne $postgresContainerId -or
        [int]$partial.post_attempts -ne 2 -or
        [int]$partial.exact_id_replays -ne 1 -or
        -not [bool]$partial.matching_lease_terminal -or
        [string]$partial.matching_lease_state -cne 'revoked_stopped' -or
        [int]$partial.pending_signals -ne 0 -or
        -not [bool]$partial.controls.binance_spot.kill_switch
    ) {
        throw (
            'Partial-cleanup fixture did not converge through one exact-ID ' +
            "replay: $($partial | ConvertTo-Json -Depth 8 -Compress)"
        )
    }
    if (
        $global:StratusStopBarrierBodies.Count -ne 2 -or
        $global:StratusStopBarrierBodies[0] -cne
            $global:StratusStopBarrierBodies[1]
    ) {
        throw 'Partial cleanup did not replay the byte-identical STOP body.'
    }
    Assert-StopFirstAndNoDockerMutation

    Reset-Fixture -Scenario 'replay' -StatePath $absentStatePath
    $replay = Invoke-Barrier | ConvertFrom-Json
    if (
        -not [bool]$replay.verified -or
        [bool]$replay.initially_created -or
        [int]$replay.post_attempts -ne 1 -or
        [int]$replay.exact_id_replays -ne 0 -or
        $null -ne $replay.matching_lease_state
    ) {
        throw 'Existing exact-ID STOP replay was not accepted safely.'
    }
    Assert-StopFirstAndNoDockerMutation

    Reset-Fixture -Scenario 'hostile' -StatePath $goodStatePath
    Assert-Throws -Label 'hostile HTTP identity' `
        -ExpectedMessage 'collided with the reserved emergency-stop identity' `
        -Action { Invoke-Barrier }
    $hostileEvents = @(
        Get-Content -LiteralPath $eventLog |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    if ($hostileEvents.Count -ne 1 -or $hostileEvents[0] -notlike 'POST *') {
        throw 'Hostile HTTP identity reached a Docker/SQL diagnostic.'
    }

    Reset-Fixture -Scenario 'timeout' -StatePath $pendingStatePath
    Assert-Throws -Label 'bounded pending timeout' `
        -ExpectedMessage 'did not converge within its bounded window' `
        -Action { Invoke-Barrier }
    if ($global:StratusStopBarrierHttpCalls -ne 21) {
        throw 'Pending timeout did not stop at the exact bounded attempt count.'
    }
    Assert-StopFirstAndNoDockerMutation
}
finally {
    $env:PATH = $originalPath
    Remove-Item Env:\STRATUS_STOP_BARRIER_EVENT_LOG -ErrorAction SilentlyContinue
    Remove-Item Env:\STRATUS_STOP_BARRIER_DOCKER_JSON -ErrorAction SilentlyContinue
    Remove-Item Function:\Invoke-RestMethod -Force -ErrorAction SilentlyContinue
    Remove-Item Function:\Start-Sleep -Force -ErrorAction SilentlyContinue
    foreach ($name in @(
        'StratusStopBarrierScenario',
        'StratusStopBarrierHttpCalls',
        'StratusStopBarrierBodies',
        'StratusStopBarrierPartialState',
        'StratusStopBarrierGoodState',
        'StratusStopBarrierExpectedRunId',
        'StratusStopBarrierExpectedSecret'
    )) {
        Remove-Variable -Name $name -Scope Global -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $temporaryRoot -Recurse -Force `
        -ErrorAction SilentlyContinue
}

[pscustomobject]@{
    script = $barrierPath
    syntax_valid = $true
    authenticated_stop_first = $true
    exact_body_verified = $true
    exact_id_replay_verified = $true
    partial_cleanup_verified = $true
    hostile_identity_rejected = $true
    bounded_timeout_verified = $true
    docker_lifecycle_mutations = 0
    validated = $true
} | ConvertTo-Json -Compress
