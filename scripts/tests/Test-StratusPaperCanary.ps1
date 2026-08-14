[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-Contract {
    param(
        [Parameter(Mandatory = $true)][bool] $Condition,
        [Parameter(Mandatory = $true)][string] $Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

$scriptPath = [IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot '..\Invoke-StratusPaperCanary.ps1')
)
Assert-Contract (Test-Path -LiteralPath $scriptPath -PathType Leaf) (
    "Missing PAPER canary collector: $scriptPath"
)

$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
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
foreach ($forbidden in @(
    'docker\s+(start|stop|restart|rm|kill)\b',
    'docker\s+compose[^\r\n]*(up|down|start|stop|restart|rm|kill)\b',
    'Stop-Process|taskkill|Invoke-Expression|\biex\b',
    "payload\s*=\s*\[ordered\]@\{\s*venue\s*=\s*'all'",
    "payload\s*=\s*\[ordered\]@\{\s*venue\s*=\s*'binance_spot'",
    (
        '\bINSERT\s+INTO\b|\bUPDATE\s+\w+|\bDELETE\s+FROM\b|' +
        '\bTRUNCATE\b|\bALTER\s+TABLE\b'
    ),
    'MaxRuntimeDatabaseSessions|database_session_budget',
    'pg_(terminate|cancel)_backend|SELECT\s+.*\s+FOR\s+UPDATE|LOCK\s+TABLE',
    '\b(nextval|setval|set_config|lo_import)\s*\(',
    'COPY\s+.+\s+FROM\s+PROGRAM|(?m)^\s*\\[!a-z]',
    (
        '(?:ApiKey|SafetyApiKey|STRATUS_OPERATOR_API_KEY|' +
        'STRATUS_SAFETY_API_KEY).*ConvertTo-Json'
    ),
    'AllowAutoRedirect\s*=\s*\$true'
)) {
    if ($source -match $forbidden) {
        throw "PAPER canary contains a forbidden pattern: $forbidden"
    }
}

foreach ($required in @(
    'MinuteOffsets = @\(0, 5, 15, 30, 45, 60\)',
    'GuardIntervalSeconds = 5',
    "SafetyBaseUri = 'http://127\.0\.0\.1:8001/'",
    '\[Security\.SecureString\]\s+\$SafetyApiKey',
    'STRATUS_SAFETY_API_KEY',
    'Operator and safety API keys must be distinct',
    'COMPOSE_PATH_SEPARATOR',
    'reserved Compose override variable',
    '-Uri\s+\$SafetyBaseUri\s+-Secret\s+\$effectiveSafetyApiKey',
    'distinct_control_plane_credentials = \$true',
    'AllowAutoRedirect = \$false',
    'UseProxy = \$false',
    '\[IO\.FileMode\]::CreateNew',
    '\.owner\.lock',
    'REPEATABLE READ READ ONLY',
    "command = 'stop'",
    "payload = \[ordered\]@\{ venue = 'polymarket' \}",
    "Path 'api/v1/emergency-stop'",
    'Assert-ExactStopItem',
    "status -eq 'succeeded'",
    'post-stop controls are unsafe',
    'ExpectedResumeRequestId',
    'ExpectedResumeActor',
    'ExpectedResumeReason',
    '\[ValidateLength\(1, 64\)\]\s*\[string\]\s+\$ExpectedResumeActor',
    "\[ValidateSet\('20260730_0047'\)\]",
    "ExpectedSchemaHead = '20260730_0047'",
    'Get-PreflightViolations',
    'PAPER canary preflight rejected',
    'preflight_safe = \$true',
    'emergency_stop_armed = \$true',
    '\$preflightAccepted = \$true',
    'Release PAPER canary offsets must be exactly 0,5,15,30,45,60',
    'Release PAPER canary requires the Polymarket-only emergency stop to be armed',
    'emergency_max_post_attempts = 2',
    'safety_lease = \[ordered\]@\{',
    'direct_stop_request_id = \$leaseDirectStopRequestId',
    'divergence_stop_revokes_atomically = \$true',
    'success_release_stops_atomically = \$true',
    "Path 'api/v1/canary-leases/acquire'",
    'release_request_id = \$leaseReleaseRequestId',
    'Invoke-CanaryLeaseHeartbeat',
    'Invoke-CanaryLeaseRelease',
    'nonce_sha256',
    'first_completed_post_must_create = \$true',
    'ambiguous_post_database_recovery = \$true',
    'bounded_same_id_retry = \$true',
    'heartbeat_checkpoints_between_multicall_io = \$true',
    'Invoke-FailClosedStopBarrier',
    'resume_executed_at',
    'monotonic_elapsed_seconds',
    'Get-GuardSql',
    'Get-FullSql',
    'New-DatabaseSessionBoundary',
    'Get-DatabaseSessionSql',
    'exact_database_session_identity = \$true',
    'stratus-paper-canary-collector',
    "'client_backends'",
    "'unexpected_sessions'",
    'unexpected_database_session',
    'assert_stratus_runtime_boundary\(false\)',
    'assert_stratus_safety_boundary\(false\)',
    'database_authority_boundary_drifted',
    'pending_signals_present',
    'PAPER canary collector admission requires zero pending Signals',
    "database_boundary -ne 'exact'",
    'unexpected_commands_since_boundary',
    'post_grace_missing_marks',
    'execution-cost-maintenance',
    'execution-markouts',
    'execution-cost-finalization',
    'Get-WorkerMaintenanceLogSnapshot',
    'mark_ids',
    'finalize_ids',
    'claim_identity_mismatches',
    'repeated_zero_mark_finalize_ids',
    'scheduler_lock_contention',
    'zero_leg_sealed_runs',
    'invalid_record_hashes',
    'seals_before_required_marks',
    'finalizable_unsealed',
    'prospective_validation_outcomes',
    'previous_snapshot_sha256',
    '\$previousSnapshotHash = \$readyWritten\.sha256',
    'evidence_chain = \[ordered\]@\{',
    'preflight = \$preflightWritten',
    'lease_acquire = \$leaseWritten',
    'ready = \$readyWritten',
    'normal_collection_stopped = \$true',
    'Get-ResourceGuardViolations',
    'postgres_cpu_two_consecutive_samples',
    'postgres_cpu_rolling_30s_average',
    'resource_sampler_schedule_lag',
    'Get-CoherentHttpDatabaseSnapshot',
    'safety-control',
    'exhaustive_running_project_topology = \$true',
    'continuous_network_topology_guard = \$true',
    'ExpectedContainerMemoryLimits = \[ordered\]@\{',
    'api = 1073741824L',
    'safety_control = 268435456L',
    'worker = 3221225472L',
    'HostConfig',
    'memory_limit_bytes',
    'memory_limit_\$service',
    'operator_api_key_entry_count',
    'operator_credential_\$service',
    'Assert-ApiJsonResponseShape',
    'Assert-SafetyMutationJsonResponseShape',
    'native JSON boolean',
    'Get-RunningProjectTopology',
    'running_project_topology',
    'network_topology_',
    'com\.docker\.compose\.project',
    'com\.docker\.compose\.service',
    "allowed_services = @\('api', 'safety-control', 'worker', 'postgres'\)"
)) {
    if ($source -notmatch $required) {
        throw "PAPER canary is missing a required safety control: $required"
    }
}

function Get-EnclosingFunctionName {
    param([Parameter(Mandatory = $true)][object] $Node)

    $parent = $Node.Parent
    while (
        $null -ne $parent -and
        $parent -isnot [Management.Automation.Language.FunctionDefinitionAst]
    ) {
        $parent = $parent.Parent
    }
    if ($null -eq $parent) {
        return '<script>'
    }
    return [string]$parent.Name
}

# Direct native execution is deliberately tiny and allowlisted by AST. This
# catches aliases, line wrapping, and future call sites that source regexes miss.
$directDocker = @(
    $ast.FindAll(
        {
            param($node)
            $node -is [Management.Automation.Language.CommandAst] -and
                [string]$node.GetCommandName() -eq 'docker'
        },
        $true
    )
)
Assert-Contract ($directDocker.Count -eq 0) (
    'PAPER canary invokes docker directly instead of the bounded wrapper.'
)

# The plaintext lease nonce may exist only in the three authenticated lease
# requests and in its create/hash/zero lifecycle. It must never enter an
# artifact hashtable, snapshot, error record, or manifest.
$leaseNonceReferences = @(
    $ast.FindAll(
        {
            param($node)
            $node -is [Management.Automation.Language.VariableExpressionAst] -and
                $node.VariablePath.UserPath -eq 'leaseNonce'
        },
        $true
    )
)
$nonceFunctionInventory = @(
    $leaseNonceReferences |
        Where-Object { (Get-EnclosingFunctionName -Node $_) -ne '<script>' } |
        ForEach-Object { Get-EnclosingFunctionName -Node $_ } |
        Sort-Object
)
$expectedNonceFunctionInventory = @(
    'Invoke-CanaryLeaseAcquire',
    'Invoke-CanaryLeaseHeartbeat',
    'Invoke-CanaryLeaseRelease'
) | Sort-Object
Assert-Contract (
    ($nonceFunctionInventory -join ',') -eq
        ($expectedNonceFunctionInventory -join ',')
) 'Plaintext lease nonce escaped the authenticated lease-request functions.'
$scriptNonceContexts = @(
    $leaseNonceReferences |
        Where-Object { (Get-EnclosingFunctionName -Node $_) -eq '<script>' } |
        ForEach-Object { $_.Parent.Extent.Text }
)
Assert-Contract (
    $scriptNonceContexts.Count -eq 3 -and
    @($scriptNonceContexts | Where-Object {
        $_ -match '^\$leaseNonce\s*=\s*New-SecureCanaryNonce$'
    }).Count -eq 1 -and
    @($scriptNonceContexts | Where-Object {
        $_ -match 'GetBytes\(\$leaseNonce\)'
    }).Count -eq 1 -and
    @($scriptNonceContexts | Where-Object {
        $_ -match '^\$leaseNonce\s*=\s*\$null$'
    }).Count -eq 1
) 'Plaintext lease nonce lifecycle changed or entered serialized evidence.'

$nativeCalls = @(
    $ast.FindAll(
        {
            param($node)
            $node -is [Management.Automation.Language.CommandAst] -and
                [string]$node.GetCommandName() -eq 'Invoke-BoundedNative'
        },
        $true
    )
)
$nativeInventory = @(
    $nativeCalls |
        ForEach-Object { Get-EnclosingFunctionName -Node $_ }
)
$expectedNativeInventory = @(
    'Get-ContainerSnapshot',
    'Get-RunningProjectTopology',
    'Get-ResourceSnapshot',
    'Get-WorkerMaintenanceLogSnapshot',
    'Invoke-Compose',
    'Invoke-ReadOnlySql'
)
Assert-Contract (
    (@($nativeInventory | Sort-Object) -join ',') -eq
        (@($expectedNativeInventory | Sort-Object) -join ',')
) (
    'Bounded native-call inventory changed outside the Docker read-only allowlist: ' +
    ($nativeInventory -join ',')
)

$nativePatterns = @{
    'Invoke-Compose' = @(
        "-FileName\s+'docker'",
        '-Arguments\s+\$nativeArguments'
    )
    'Get-ContainerSnapshot' = @(
        "-FileName\s+'docker'",
        "'inspect'",
        '\$ContainerId'
    )
    'Get-RunningProjectTopology' = @(
        "-FileName\s+'docker'",
        "'ps'",
        "'--no-trunc'",
        "'--format'",
        '\$format'
    )
    'Get-ResourceSnapshot' = @(
        "-FileName\s+'docker'",
        '-Arguments\s+\$arguments'
    )
    'Get-WorkerMaintenanceLogSnapshot' = @(
        "-FileName\s+'docker'",
        "'logs'",
        "'--timestamps'",
        "'--since'",
        "'--tail'",
        '\[string\]\$Tail'
    )
    'Invoke-ReadOnlySql' = @(
        "-FileName\s+'docker'",
        "'exec'",
        "'-i'",
        'psql\s+-XAtq\s+-v\s+ON_ERROR_STOP=1',
        '-StandardInput\s+\$Sql'
    )
}
foreach ($call in $nativeCalls) {
    $functionName = Get-EnclosingFunctionName -Node $call
    foreach ($pattern in $nativePatterns[$functionName]) {
        Assert-Contract ($call.Extent.Text -match $pattern) (
            "Native call in $functionName escaped its allowlist: $pattern"
        )
    }
}

$composeCalls = @(
    $ast.FindAll(
        {
            param($node)
            $node -is [Management.Automation.Language.CommandAst] -and
                [string]$node.GetCommandName() -eq 'Invoke-Compose'
        },
        $true
    )
)
Assert-Contract (
    $composeCalls.Count -eq 1 -and
    (Get-EnclosingFunctionName -Node $composeCalls[0]) -eq 'Get-ServiceContainerId' -and
    $composeCalls[0].Extent.Text -match (
        '-Arguments\s+@\(''ps'',\s*''--all'',\s*''-q'',\s*\$Service\)'
    )
) 'Docker Compose allowlist must contain only compose ps --all -q <service>.'

$readOnlySqlFunction = @(
    $ast.FindAll(
        {
            param($node)
            $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
                $node.Name -eq 'Invoke-ReadOnlySql'
        },
        $true
    )
)
Assert-Contract (
    $readOnlySqlFunction.Count -eq 1 -and
    $readOnlySqlFunction[0].Extent.Text -match "'sh',\s*'-lc'" -and
    $readOnlySqlFunction[0].Extent.Text -match (
        'PGPASSWORD="\$POSTGRES_PASSWORD"\s+' +
        'PGAPPNAME="stratus-paper-canary-collector"\s+' +
        'exec\s+psql\s+-XAtq'
    )
) 'Docker exec is not pinned to the read-only psql evidence command.'

$functionDefinitions = @{}
foreach ($functionAst in @(
    $ast.FindAll(
        {
            param($node)
            $node -is [Management.Automation.Language.FunctionDefinitionAst]
        },
        $true
    )
)) {
    $functionDefinitions[[string]$functionAst.Name] = $functionAst
}
foreach ($guardFunctionName in @('Test-GuardState', 'Test-FailClosedGuardState')) {
    Assert-Contract (
        $functionDefinitions[$guardFunctionName].Extent.Text -match
            'Get-DatabaseSessionGuardViolations\s+-State\s+\$State'
    ) "$guardFunctionName lost its continuous exact database-session guard."
}
Assert-Contract (
    $functionDefinitions['Invoke-Compose'].Extent.Text -match (
        '\$nativeArguments\s*=\s*@\(\s*''compose'',\s*' +
        '''--project-directory'',\s*\$absoluteRoot,\s*''-f'',\s*' +
        '\$composePath,\s*''--env-file'',\s*\$composeEnvPath,\s*' +
        '''--project-name'',\s*''weatherpoly''\s*\)\s*\+\s*@\(\$Arguments\)'
    )
) 'Invoke-Compose changed its fixed read-only compose prefix.'
Assert-Contract (
    $functionDefinitions['Get-ResourceSnapshot'].Extent.Text -match (
        '\$arguments\s*=\s*@\(''stats'',\s*''--no-stream'',\s*' +
        '''--format'',\s*''\{\{json \.\}\}''\)\s*\+\s*\$names'
    )
) 'Docker stats arguments changed outside the read-only allowlist.'
Assert-Contract (
    $functionDefinitions['Get-WorkerMaintenanceLogSnapshot'].Extent.Text -match (
        '\[ValidateRange\(100,\s*5000\)\]\[int\]\s*\$Tail\s*=\s*5000'
    ) -and
    $functionDefinitions['Get-WorkerMaintenanceLogSnapshot'].Extent.Text -match (
        'possibly_truncated\s*=\s*\$nonEmptyLineCount\s*-ge\s*\$Tail'
    ) -and
    $functionDefinitions['Get-WorkerMaintenanceLogSnapshot'].Extent.Text -match (
        'timestamp_parse_failures\s*=\s*\$timestampParseFailures'
    )
) 'Worker-log reader lost its explicit tail/truncation/completeness contract.'
Assert-Contract (
    $functionDefinitions['Get-ContainerSet'].Extent.Text -match (
        '\[scriptblock\]\s*\$HeartbeatAction'
    ) -and
    $functionDefinitions['Get-ContainerSet'].Extent.Text -match (
        'foreach\s*\(\$descriptor[\s\S]+Get-ContainerSnapshot' +
        '[\s\S]+&\s*\$HeartbeatAction'
    ) -and
    $functionDefinitions['Get-ContainerSet'].Extent.Text -match (
        'Get-RunningProjectTopology\s+-Containers\s+\$snapshots' +
        '[\s\S]+&\s*\$HeartbeatAction'
    ) -and
    $functionDefinitions['Get-HttpSnapshot'].Extent.Text -match (
        '\[scriptblock\]\s*\$HeartbeatAction'
    ) -and
    $functionDefinitions['Get-HttpSnapshot'].Extent.Text -match (
        'foreach\s*\(\$endpoint\s+in\s+\$EndpointInventory\)' +
        '[\s\S]+&\s*\$HeartbeatAction'
    ) -and
    $functionDefinitions['Get-SafetyHttpSnapshot'].Extent.Text -match (
        'foreach[\s\S]+&\s*\$HeartbeatAction'
    ) -and
    $functionDefinitions['Get-CoherentHttpDatabaseSnapshot'].Extent.Text -match (
        'Get-HttpSnapshot[\s\S]+-HeartbeatAction\s+\$HeartbeatAction'
    ) -and
    @(
        [regex]::Matches(
            $functionDefinitions[
                'Get-CoherentHttpDatabaseSnapshot'
            ].Extent.Text,
            '&\s*\$HeartbeatAction'
        )
    ).Count -ge 2
) (
    'Container/HTTP/database collection lost its heartbeat checkpoints ' +
    'between bounded external calls.'
)
$interMilestoneStart = $source.IndexOf(
    '$guardDocker = Get-ContainerSet',
    [StringComparison]::Ordinal
)
$interMilestoneEnd = $source.IndexOf(
    '$nextGuardDueSeconds += $GuardIntervalSeconds',
    $interMilestoneStart,
    [StringComparison]::Ordinal
)
$interMilestoneText = $source.Substring(
    $interMilestoneStart,
    $interMilestoneEnd - $interMilestoneStart
)
Assert-Contract (
    $interMilestoneStart -ge 0 -and
    $interMilestoneEnd -gt $interMilestoneStart -and
    @(
        [regex]::Matches(
            $interMilestoneText,
            'Invoke-CanaryLeaseHeartbeat'
        )
    ).Count -ge 5
) (
    'Inter-milestone resource/log/HTTP sequence can outlive its lease ' +
    'without heartbeat checkpoints.'
)
$milestoneCollectionStart = $source.IndexOf(
    '$dockerState = Get-ContainerSet',
    [StringComparison]::Ordinal
)
$milestoneCollectionEnd = $source.IndexOf(
    '$violations = @(',
    $milestoneCollectionStart,
    [StringComparison]::Ordinal
)
$milestoneCollectionText = $source.Substring(
    $milestoneCollectionStart,
    $milestoneCollectionEnd - $milestoneCollectionStart
)
Assert-Contract (
    $milestoneCollectionStart -ge 0 -and
    $milestoneCollectionEnd -gt $milestoneCollectionStart -and
    $milestoneCollectionText -match (
        'Get-CoherentHttpDatabaseSnapshot[\s\S]+-HeartbeatAction'
    ) -and
    $milestoneCollectionText -match (
        'Get-SafetyHttpSnapshot[\s\S]+-HeartbeatAction'
    ) -and
    @(
        [regex]::Matches(
            $milestoneCollectionText,
            'Invoke-CanaryLeaseHeartbeat'
        )
    ).Count -ge 5
) (
    'Milestone snapshot/resource/log sequence can outlive its lease ' +
    'without heartbeat checkpoints.'
)
Assert-Contract (
    $source -match (
        '\$guardLogSince\s*=\s*\$workerLogCursor\.AddSeconds\(-2\)'
    ) -and
    $source -match (
        'Get-WorkerMaintenanceLogSnapshot[\s\S]{0,180}' +
        '-Since\s+\$guardLogSince\s+-Tail\s+500'
    ) -and
    $source -match (
        '\[bool\]\$guardLogs\.possibly_truncated[\s\S]{0,180}' +
        '\$guardLogs\.timestamp_parse_failures'
    ) -and
    $source -match (
        '\$latestGuardLogAt\s*=\s*' +
        '\[DateTimeOffset\]\$guardLogs\.latest_docker_timestamp'
    ) -and
    $source -match '\$workerLogCursor\s*=\s*\$latestGuardLogAt'
) 'Incremental worker-log cursor, overlap, or fail-closed checks are incomplete.'
foreach ($liveEvidenceTable in @(
    'orders',
    'positions',
    'execution_order_intents',
    'execution_fill_legs',
    'prospective_validation_enrollments',
    'prospective_validation_outcomes'
)) {
    Assert-Contract (
        $functionDefinitions['Get-CanaryLeaseState'].Extent.Text -match (
            '\b' + [regex]::Escape($liveEvidenceTable) + '\b'
        )
    ) "Lease terminal-state SQL omits LIVE evidence table $liveEvidenceTable."
}
Assert-Contract (
    $functionDefinitions['Get-CanaryLeaseState'].Extent.Text -match (
        "'pending_commands'[\s\S]+operator_commands\s+WHERE\s+status='pending'"
    ) -and
    $functionDefinitions['Assert-ReleasedCanaryLeaseState'].Extent.Text -match (
        '\[int\]\$State\.pending_commands\s*-ne\s*0'
    ) -and
    $functionDefinitions['Assert-ReleasedCanaryLeaseState'].Extent.Text -match (
        '\[int\]\$State\.unexpected_commands_since_boundary\s*-ne\s*0'
    ) -and
    $functionDefinitions['Assert-ReleasedCanaryLeaseState'].Extent.Text -match (
        '\[int\]\$State\.live_rows\s*-ne\s*0'
    )
) 'Successful release can be confirmed with pending commands or LIVE evidence.'
Assert-Contract (
    $functionDefinitions['Invoke-CanaryLeaseAcquire'].Extent.Text -match (
        'release_request_id\s*=\s*\$leaseReleaseRequestId'
    ) -and
    $functionDefinitions['Invoke-CanaryLeaseAcquire'].Extent.Text -match (
        'direct_stop_request_id\s*=\s*\$leaseDirectStopRequestId'
    )
) (
    'PAPER canary lease acquire request omits immutable stop authority.'
)
Assert-Contract (
    $functionDefinitions['Assert-ExactCanaryLeaseItem'].Extent.Text -match (
        '\[string\]\$Item\.release_request_id\s*-ne\s*' +
        '\$leaseReleaseRequestId'
    )
) (
    'PAPER canary lease identity validation omits release_request_id.'
)

$preflightIndex = $source.IndexOf(
    '$preflightViolations = @(',
    [StringComparison]::Ordinal
)
$readyIndex = $source.IndexOf(
    "Join-Path `$runDirectory 'ready.json'",
    [StringComparison]::Ordinal
)
Assert-Contract ($preflightIndex -ge 0 -and $readyIndex -gt $preflightIndex) (
    'PAPER canary emits its ready marker before the safety preflight.'
)
$preflightAcceptedIndex = $source.IndexOf(
    '$preflightAccepted = $true',
    [StringComparison]::Ordinal
)
$leaseAcquireIndex = $source.IndexOf(
    '$leaseAcquire = Invoke-CanaryLeaseAcquire',
    [StringComparison]::Ordinal
)
Assert-Contract (
    $preflightAcceptedIndex -ge 0 -and
    $leaseAcquireIndex -gt $preflightAcceptedIndex
) 'Collector process-death protection is not armed before lease acquisition.'

$leaseArtifactIndex = $source.IndexOf(
    '$leaseArtifact = [ordered]@{',
    [StringComparison]::Ordinal
)
$leaseWriteIndex = $source.IndexOf(
    '$leaseWritten = Write-ImmutableJson',
    [StringComparison]::Ordinal
)
$readyWriteIndex = $source.IndexOf(
    '$readyWritten = Write-ImmutableJson',
    [StringComparison]::Ordinal
)
$chainSeedIndex = $source.IndexOf(
    '$previousSnapshotHash = $readyWritten.sha256',
    [StringComparison]::Ordinal
)
$manifestChainIndex = $source.IndexOf(
    'evidence_chain = [ordered]@{',
    [StringComparison]::Ordinal
)
Assert-Contract (
    $leaseArtifactIndex -ge 0 -and
    $leaseWriteIndex -gt $leaseArtifactIndex -and
    $readyWriteIndex -gt $leaseWriteIndex -and
    $chainSeedIndex -gt $readyWriteIndex -and
    $manifestChainIndex -gt $chainSeedIndex
) 'Preflight, lease, ready, snapshot, and manifest evidence are not one chain.'
Assert-Contract (
    $source.Substring(
        $leaseArtifactIndex,
        $leaseWriteIndex - $leaseArtifactIndex
    ) -match 'preflight_artifact_sha256\s*=\s*\$preflightWritten\.sha256'
) 'Lease artifact does not bind the immutable preflight artifact.'
$successReleaseIndex = $source.IndexOf(
    '$stopEvidence = Invoke-CanaryLeaseRelease',
    [StringComparison]::Ordinal
)
$manifestIndex = $source.IndexOf(
    '$manifest = [ordered]@{',
    [StringComparison]::Ordinal
)
$mainCatchIndex = $source.IndexOf(
    '$failureMessage = $_.Exception.GetBaseException().Message',
    [StringComparison]::Ordinal
)
Assert-Contract (
    $successReleaseIndex -gt $chainSeedIndex -and
    $manifestIndex -gt $successReleaseIndex -and
    $mainCatchIndex -gt $manifestIndex
) (
    'Success manifest can be written before atomic lease release and its ' +
    'fail-closed database confirmation.'
)

# The main catch must issue the independent stop before it asks Docker, SQL,
# logs, HTTP, or the filesystem for diagnostics.
$catchCandidates = @(
    $ast.FindAll(
        {
            param($node)
            $node -is [Management.Automation.Language.CatchClauseAst] -and
                $node.Extent.Text -match '\$preflightAccepted' -and
                $node.Extent.Text -match 'Invoke-EmergencyStop'
        },
        $true
    )
)
Assert-Contract ($catchCandidates.Count -eq 1) (
    'Expected exactly one top-level fail-closed catch clause.'
)
$catchText = $catchCandidates[0].Extent.Text
$stopBarrierIndex = $catchText.IndexOf(
    'Invoke-FailClosedStopBarrier',
    [StringComparison]::Ordinal
)
$stopIndex = $catchText.IndexOf(
    'Invoke-EmergencyStop',
    [StringComparison]::Ordinal
)
Assert-Contract (
    $stopBarrierIndex -ge 0 -and $stopIndex -gt $stopBarrierIndex
) 'Fail-closed catch does not route its emergency stop through the tested barrier.'
foreach ($diagnostic in @(
    'Invoke-ReadOnlySql',
    'Get-ContainerSet',
    'Get-ResourceSnapshot',
    'Get-WorkerMaintenanceLogSnapshot',
    'Invoke-JsonRequest',
    'Write-ImmutableJson'
)) {
    $diagnosticIndex = $catchText.IndexOf($diagnostic, [StringComparison]::Ordinal)
    Assert-Contract (
        $diagnosticIndex -lt 0 -or
        (
            $diagnosticIndex -gt $stopBarrierIndex -and
            $diagnosticIndex -gt $stopIndex
        )
    ) "Fail-closed catch runs $diagnostic before the emergency stop."
}

$postCalls = @(
    $ast.FindAll(
        {
            param($node)
            $node -is [Management.Automation.Language.CommandAst] -and
                [string]$node.GetCommandName() -eq 'Invoke-JsonRequest' -and
                $node.Extent.Text -match '-Method\s+POST\b'
        },
        $true
    )
)
$postInventory = @(
    $postCalls |
        ForEach-Object { Get-EnclosingFunctionName -Node $_ } |
        Sort-Object
)
$expectedPostInventory = @(
    'Invoke-CanaryLeaseAcquire',
    'Invoke-CanaryLeaseHeartbeat',
    'Invoke-CanaryLeaseRelease',
    'Invoke-EmergencyStop'
) | Sort-Object
Assert-Contract (
    ($postInventory -join ',') -eq ($expectedPostInventory -join ',')
) (
    'Authenticated mutation-path inventory changed: ' +
    ($postInventory -join ',')
)
$postPathPatterns = @{
    'Invoke-CanaryLeaseAcquire' = (
        "-Path\s+'api/v1/canary-leases/acquire'"
    )
    'Invoke-CanaryLeaseHeartbeat' = (
        '-Path\s+"api/v1/canary-leases/\$leaseId/heartbeat"'
    )
    'Invoke-CanaryLeaseRelease' = (
        '-Path\s+"api/v1/canary-leases/\$leaseId/release"'
    )
    'Invoke-EmergencyStop' = "-Path\s+'api/v1/emergency-stop'"
}
foreach ($postCall in $postCalls) {
    $functionName = Get-EnclosingFunctionName -Node $postCall
    Assert-Contract (
        $postCall.Extent.Text -match $postPathPatterns[$functionName]
    ) "Authenticated mutation path in $functionName escaped its allowlist."
}
Assert-Contract (
    $postCalls.Count -ge 1 -and
    @(
        $postCalls |
            Where-Object {
                $_.Extent.Text -match "-Path\s+'api/v1/emergency-stop'"
            }
    ).Count -eq 1
) 'Independent safety-control emergency-stop POST path is missing or duplicated.'
foreach ($postCall in $postCalls) {
    Assert-Contract (
        $postCall.Extent.Text -notmatch "api/v1/commands"
    ) 'PAPER canary must not send its stop through the main operator API.'
}

$hostExecutable = (Get-Process -Id $PID).Path
$validationOutputRoot = Join-Path ([IO.Path]::GetTempPath()) (
    'stratus-paper-canary-validation-' + [guid]::NewGuid().ToString('N')
)
$validationOutput = & $hostExecutable `
    -NoLogo `
    -NoProfile `
    -NonInteractive `
    -File $scriptPath `
    -OutputRoot $validationOutputRoot `
    -StopPolymarketOnDivergence `
    -ValidateOnly
if ($LASTEXITCODE -ne 0) {
    throw "PAPER canary validation mode exited with $LASTEXITCODE."
}
$validation = ($validationOutput -join [Environment]::NewLine) | ConvertFrom-Json
Assert-Contract (-not (Test-Path -LiteralPath $validationOutputRoot)) (
    'PAPER canary validation mode performed filesystem I/O.'
)

Assert-Contract (
    $validation.schema_version -eq 1 -and
    $validation.collector -eq 'stratus_polymarket_paper_canary' -and
    [bool]$validation.validated -and
    [bool]$validation.validation_only_has_no_io -and
    [bool]$validation.starts_before_resume -and
    [bool]$validation.loopback_only -and
    -not [bool]$validation.proxy_allowed -and
    -not [bool]$validation.redirects_allowed -and
    [bool]$validation.immutable_create_new_artifacts -and
    [bool]$validation.hash_chained_snapshots
) 'PAPER canary validation contract is unsafe or incomplete.'
Assert-Contract (
    (@($validation.minute_offsets) -join ',') -eq '0,5,15,30,45,60' -and
    $validation.guard_interval_seconds -eq 5 -and
    $validation.max_snapshots -eq 6 -and
    $validation.max_duration_minutes -eq 60 -and
    $validation.max_response_bytes -eq 1048576 -and
    $validation.native_timeout_seconds -eq 12
) 'PAPER canary timing or response-size contract changed unexpectedly.'
Assert-Contract (
    $validation.direct_sql.isolation -eq 'REPEATABLE READ READ ONLY' -and
    $validation.direct_sql.database_clock -eq 'clock_timestamp()' -and
    [bool]$validation.direct_sql.complete_job_window -and
    (@($validation.direct_sql.live_tables) -join ',') -eq (
        'orders,positions,execution_order_intents,execution_fill_legs,' +
        'prospective_validation_enrollments,prospective_validation_outcomes'
    )
) 'PAPER canary direct-SQL evidence contract is incomplete.'
Assert-Contract (
    $validation.mutation_default -eq 'none' -and
    $validation.emergency_max_post_attempts -eq 2 -and
    $validation.emergency_command.command -eq 'stop' -and
    $validation.emergency_command.payload.venue -eq 'polymarket' -and
    [bool]$validation.emergency_command.idempotent_request_id -and
    [bool]$validation.emergency_command.exact_response_identity_required -and
    [bool]$validation.emergency_command.exact_sql_poll_required -and
    [bool]$validation.emergency_command.post_stop_controls_required -and
    [bool]$validation.emergency_command.first_completed_post_must_create -and
    [bool]$validation.emergency_command.ambiguous_post_database_recovery -and
    [bool]$validation.emergency_command.bounded_same_id_retry -and
    [bool]$validation.emergency_command.cleanup_state_sql_required -and
    [bool]$validation.emergency_command.same_id_cleanup_replay -and
    [bool]$validation.emergency_command.cleanup_retry_bounded_by_confirmation_window -and
    [bool]$validation.emergency_stop_must_be_armed -and
    [bool]$validation.emergency_stop_after_accepted_preflight_failure -and
    [bool]$validation.distinct_control_plane_credentials -and
    [bool]$validation.scheduler_lock_contention_identity_detection
) 'PAPER canary emergency-stop contract is unsafe.'
Assert-Contract (
    [bool]$validation.safety_lease.database_clock -and
    [bool]$validation.safety_lease.single_active -and
    $validation.safety_lease.ttl_seconds_max -eq 30 -and
    $validation.safety_lease.heartbeat_interval_seconds -eq 5 -and
    [bool]$validation.safety_lease.heartbeat_checkpoints_between_multicall_io -and
    [bool]$validation.safety_lease.independent_watchdog -and
    [bool]$validation.safety_lease.resume_request_bound -and
    [bool]$validation.safety_lease.nonce_not_persisted -and
    [bool]$validation.safety_lease.divergence_stop_revokes_atomically -and
    [bool]$validation.safety_lease.success_release_stops_atomically
) 'PAPER canary database-clock safety-lease contract is incomplete.'
Assert-Contract (
    ($validation | ConvertTo-Json -Depth 8 -Compress) -notmatch '"nonce"\s*:'
) 'ValidateOnly leaked a plaintext safety-lease nonce.'

$endpointNames = @($validation.endpoint_inventory | ForEach-Object { $_.Name })
foreach ($requiredEndpoint in @(
    'healthz',
    'readyz',
    'overview',
    'venues',
    'commands',
    'jobs',
    'continuity',
    'execution_cost',
    'validation_ledger'
)) {
    Assert-Contract ($endpointNames -contains $requiredEndpoint) (
        "PAPER canary endpoint inventory is missing $requiredEndpoint."
    )
}

function Import-CanaryFunction {
    param([Parameter(Mandatory = $true)][string] $Name)

    $functionAst = @(
        $ast.FindAll(
            {
                param($node)
                $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
                    $node.Name -eq $Name
            },
            $true
        )
    )
    if ($functionAst.Count -ne 1) {
        throw "Expected exactly one canary function named $Name."
    }
    $functionPattern = '^function\s+' + [regex]::Escape($Name)
    $definition = $functionAst[0].Extent.Text -replace $functionPattern, (
        "function script:$Name"
    )
    Invoke-Expression $definition
}

foreach ($name in @(
    'ConvertTo-NativeArgument',
    'Invoke-BoundedNative',
    'Get-OptionalProperty',
    'Assert-NativeJsonObject',
    'Assert-RequiredJsonObjectProperty',
    'Assert-NativeJsonBooleanProperty',
    'Assert-NativeJsonIntegerProperty',
    'Assert-NativeJsonArrayProperty',
    'Assert-ApiJsonResponseShape',
    'Assert-SafetyHealthJsonResponseShape',
    'Assert-SafetyMutationJsonResponseShape',
    'Get-ContainerSnapshot',
    'New-DatabaseSessionBoundary',
    'ConvertTo-DatabaseAddressArraySql',
    'Get-DatabaseSessionSql',
    'Get-GuardSql',
    'Get-FullSql',
    'Get-RunningProjectTopology',
    'Get-ContainerGuardViolations',
    'ConvertFrom-ClaimIdentityToken',
    'Get-WorkerMaintenanceLogSnapshot',
    'Get-ResourceGuardViolations',
    'Add-ResourceEvidenceSample',
    'Get-Sha256',
    'Write-ImmutableJson',
    'Get-DatabaseSessionGuardViolations',
    'Test-GuardState',
    'Test-FailClosedGuardState',
    'Assert-ExactResume',
    'New-StopBody',
    'Assert-ExactStopItem',
    'Assert-ExactCanaryLeaseItem',
    'Assert-ActiveCanaryLease',
    'Assert-ReleasedCanaryLeaseState',
    'Assert-EmergencyStopControls',
    'Invoke-EmergencyStop',
    'Invoke-FailClosedStopBarrier'
)) {
    Import-CanaryFunction -Name $name
}

$NativeTimeoutSeconds = 12
$ExpectedContainerMemoryLimits = [ordered]@{
    api = 1073741824L
    safety_control = 268435456L
    worker = 3221225472L
}
$runId = 'paper-contract-run'
$leaseId = "paper-lease-$runId"
$leaseAcquireRequestId = "paper-lease-acquire-$runId"
$leaseDirectStopRequestId = "paper-canary-stop-$runId"
$leaseReleaseRequestId = "paper-lease-release-$runId"
$stopRequestId = 'paper-canary-stop-contract-test'
$leaseNonceSha256 = 'c' * 64

function Assert-NativeJsonShapeRejected {
    param(
        [Parameter(Mandatory = $true)][scriptblock] $Action,
        [Parameter(Mandatory = $true)][string] $Description
    )

    $rejected = $false
    try {
        & $Action
    }
    catch {
        $rejected = (
            $_.Exception.GetBaseException().Message -match
                'native JSON (object|boolean|integer|array)'
        )
    }
    Assert-Contract $rejected $Description
}

$nativeContinuity = [pscustomobject]@{
    active_epoch = [pscustomobject]@{
        id = 42
        cohort = 'paper-contract'
        image_digest = ('d' * 64)
    }
    cohort_match = $true
    image_match = $true
    execution_gate_allowed = $true
    execution_gate_reason = 'evidence_continuity_match'
    frozen_lanes = @()
    lane_identity_mismatches = @()
    continuity_lanes = @([pscustomobject]@{ epoch_id = 42 })
    collection_window = [pscustomobject]@{
        missing_scheduler_jobs = @()
        stalled_days = 0
    }
}
Assert-ApiJsonResponseShape -Name 'continuity' -Json $nativeContinuity

foreach ($booleanName in @(
    'cohort_match',
    'image_match',
    'execution_gate_allowed'
)) {
    $nativeContinuity.$booleanName = 'false'
    Assert-NativeJsonShapeRejected -Action {
        Assert-ApiJsonResponseShape -Name 'continuity' -Json $nativeContinuity
    } -Description "Continuity accepted string 'false' for $booleanName."
    $nativeContinuity.$booleanName = $true
}

$nativeContinuity.frozen_lanes = 'not-an-array'
Assert-NativeJsonShapeRejected -Action {
    Assert-ApiJsonResponseShape -Name 'continuity' -Json $nativeContinuity
} -Description 'Continuity accepted a scalar where a native JSON array is required.'
$nativeContinuity.frozen_lanes = @()

$nativeContinuity.collection_window.stalled_days = '0'
Assert-NativeJsonShapeRejected -Action {
    Assert-ApiJsonResponseShape -Name 'continuity' -Json $nativeContinuity
} -Description 'Continuity accepted a string where a native JSON integer is required.'
$nativeContinuity.collection_window.stalled_days = 0

Assert-NativeJsonShapeRejected -Action {
    Assert-ApiJsonResponseShape -Name 'continuity' -Json 1
} -Description 'Continuity accepted a scalar JSON response root.'
Assert-NativeJsonShapeRejected -Action {
    Assert-ApiJsonResponseShape -Name 'continuity' -Json (, $nativeContinuity)
} -Description 'Continuity accepted an array JSON response root.'

$stringCreatedResponse = [pscustomobject]@{
    created = 'false'
    idempotent = $true
    item = [pscustomobject]@{ request_id = 'contract' }
}
Assert-NativeJsonShapeRejected -Action {
    Assert-SafetyMutationJsonResponseShape -Json $stringCreatedResponse `
        -Context 'lease/stop contract response' -Schema 'lease-success'
} -Description 'Lease/stop response accepted string "false" for created.'

$stringIdempotentResponse = [pscustomobject]@{
    created = $true
    idempotent = 'false'
    item = [pscustomobject]@{ request_id = 'contract' }
}
Assert-NativeJsonShapeRejected -Action {
    Assert-SafetyMutationJsonResponseShape -Json $stringIdempotentResponse `
        -Context 'lease/stop contract response' -Schema 'lease-success'
} -Description 'Lease/stop response accepted string "false" for idempotent.'

$stringRetryableResponse = [pscustomobject]@{
    detail = 'contract failure'
    retryable = 'false'
}
Assert-NativeJsonShapeRejected -Action {
    Assert-SafetyMutationJsonResponseShape -Json $stringRetryableResponse `
        -Context 'lease/stop contract response' -Schema 'error'
} -Description 'Lease/stop response accepted string "false" for retryable.'

$unexpectedMutationProperty = [pscustomobject]@{
    created = $true
    idempotent = $false
    item = [pscustomobject]@{ request_id = 'contract' }
    unexpected = $false
}
$unexpectedMutationPropertyRejected = $false
try {
    Assert-SafetyMutationJsonResponseShape -Json $unexpectedMutationProperty `
        -Context 'lease contract response' -Schema 'lease-success'
}
catch {
    $unexpectedMutationPropertyRejected = (
        $_.Exception.GetBaseException().Message -match 'property inventory'
    )
}
Assert-Contract $unexpectedMutationPropertyRejected (
    'Lease response accepted an unexpected success-envelope property.'
)

$stringLedgerBoolean = [pscustomobject]@{
    mode = 'paper'
    selected_is_current = 'false'
    selected_pipeline_cohort = 'paper-contract'
    enrolled_orders = 0
    pending_outcomes = 0
}
Assert-NativeJsonShapeRejected -Action {
    Assert-ApiJsonResponseShape -Name 'validation_ledger' `
        -Json $stringLedgerBoolean
} -Description 'Validation ledger accepted string "false" as a required boolean.'

# Invoke-BoundedNative must be executable under Windows PowerShell 5, must
# close stdin, drain stdout/stderr concurrently, kill on timeout, and cap the
# combined output before materializing it as strings.
$stdinResult = Invoke-BoundedNative -FileName $hostExecutable -Arguments @(
    '-NoLogo',
    '-NoProfile',
    '-NonInteractive',
    '-Command',
    '[Console]::In.ReadToEnd()'
) -StandardInput 'stdin-roundtrip' -TimeoutSeconds 5
if (
    $null -eq $stdinResult -or
    $null -eq $stdinResult.PSObject.Properties['stdout']
) {
    throw (
        'Bounded native stdin test returned an unexpected object: ' +
        ($stdinResult | ConvertTo-Json -Depth 4 -Compress)
    )
}
Assert-Contract ($stdinResult.stdout.Trim() -eq 'stdin-roundtrip') (
    'Bounded native runner did not close or round-trip stdin.'
)

$duplexResult = Invoke-BoundedNative -FileName $hostExecutable -Arguments @(
    '-NoLogo',
    '-NoProfile',
    '-NonInteractive',
    '-Command',
    (
        '[Console]::Out.Write((''o'' * 40000)); ' +
        '[Console]::Error.Write((''e'' * 40000))'
    )
) -TimeoutSeconds 5 -MaxOutputBytes 131072
Assert-Contract (
    $duplexResult.stdout.Length -eq 40000 -and
    $duplexResult.stderr.Length -eq 40000
) 'Bounded native runner did not concurrently drain stdout and stderr.'

$timeoutWatch = [Diagnostics.Stopwatch]::StartNew()
$timeoutRejected = $false
try {
    [void](
        Invoke-BoundedNative -FileName $hostExecutable -Arguments @(
            '-NoLogo',
            '-NoProfile',
            '-NonInteractive',
            '-Command',
            'Start-Sleep -Seconds 5'
        ) -TimeoutSeconds 1
    )
}
catch {
    $timeoutRejected = $_.Exception.GetBaseException().Message -match (
        'exceeded 1s'
    )
}
finally {
    $timeoutWatch.Stop()
}
Assert-Contract (
    $timeoutRejected -and $timeoutWatch.Elapsed.TotalSeconds -lt 4
) 'Bounded native runner failed to enforce and terminate on its timeout.'

$outputRejected = $false
try {
    [void](
        Invoke-BoundedNative -FileName $hostExecutable -Arguments @(
            '-NoLogo',
            '-NoProfile',
            '-NonInteractive',
            '-Command',
            '[Console]::Out.Write((''x'' * 131072))'
        ) -TimeoutSeconds 5 -MaxOutputBytes 65536
    )
}
catch {
    $outputRejected = $_.Exception.GetBaseException().Message -match (
        'output exceeded 65536 bytes'
    )
}
Assert-Contract $outputRejected (
    'Bounded native runner accepted output above its combined byte budget.'
)

# Optional Docker-inspect fields must not fail under StrictMode when a service
# has no healthcheck, labels, or published ports.
Assert-Contract (
    $null -eq (Get-OptionalProperty -InputObject $null -Name 'missing') -and
    $null -eq (
        Get-OptionalProperty -InputObject ([pscustomobject]@{ present = 1 }) `
            -Name 'missing'
    )
) 'Get-OptionalProperty does not tolerate absent optional state.'

$script:mockInspect = [pscustomobject]@{
    Name = '/mock-worker'
    State = [pscustomobject]@{
        Status = 'running'
        Running = $true
        OOMKilled = $false
        StartedAt = '2026-07-29T12:00:00Z'
    }
    Config = [pscustomobject]@{
        Env = @(
            'RELEASE_IMAGE_DIGEST=' + ('a' * 64)
        )
    }
    HostConfig = [pscustomobject]@{
        Memory = 3221225472L
    }
    Image = 'sha256:' + ('b' * 64)
    RestartCount = 0
}
function script:Invoke-BoundedNative {
    param(
        [string] $FileName,
        [string[]] $Arguments,
        [string] $StandardInput,
        [int] $TimeoutSeconds,
        [int] $MaxOutputBytes
    )

    return [pscustomobject]@{
        exit_code = 0
        stdout = $script:mockInspect | ConvertTo-Json -Depth 8 -Compress
        stderr = ''
    }
}
$optionalContainer = Get-ContainerSnapshot `
    -Service 'worker' -ContainerId 'mock-container'
Assert-Contract (
    $null -eq $optionalContainer.health -and
    $null -eq $optionalContainer.revision -and
    @($optionalContainer.published_ports).Count -eq 0 -and
    @($optionalContainer.networks).Count -eq 0 -and
    @($optionalContainer.client_addresses).Count -eq 0 -and
    $null -eq $optionalContainer.compose_project -and
    [int64]$optionalContainer.memory_limit_bytes -eq 3221225472L -and
    [int]$optionalContainer.operator_api_key_entry_count -eq 0 -and
    $optionalContainer.release_digest -eq ('a' * 64)
) 'Get-ContainerSnapshot rejected absent optional Health/Labels/ports.'

$script:mockInspect | Add-Member -NotePropertyName NetworkSettings `
    -NotePropertyValue ([pscustomobject]@{
        Networks = [pscustomobject]@{
            weatherpoly_default = [pscustomobject]@{
                IPAddress = '172.30.0.3'
                GlobalIPv6Address = '2001:db8::3'
            }
        }
    })
$networkedContainer = Get-ContainerSnapshot `
    -Service 'worker' -ContainerId 'mock-container'
Assert-Contract (
    (@($networkedContainer.client_addresses | Sort-Object) -join ',') -eq
        '172.30.0.3,2001:db8::3'
) 'Get-ContainerSnapshot did not seal exact IPv4/IPv6 client identities.'

$topologyContainers = [ordered]@{
    api = [pscustomobject]@{
        container_id = 'api-id'; compose_project = 'weatherpoly';
        networks = @('weatherpoly_default')
    }
    safety_control = [pscustomobject]@{
        container_id = 'safety-id'; compose_project = 'weatherpoly';
        networks = @('weatherpoly_safety-plane')
    }
    worker = [pscustomobject]@{
        container_id = 'worker-id'; compose_project = 'weatherpoly';
        networks = @('weatherpoly_default')
    }
    postgres = [pscustomobject]@{
        container_id = 'postgres-id'; compose_project = 'weatherpoly';
        networks = @('weatherpoly_default','weatherpoly_safety-plane')
    }
}
$script:mockTopologyStdout = @(
    'api-id|weatherpoly|api|False|weatherpoly-api-1|weatherpoly_default',
    'safety-id|weatherpoly|safety-control|False|weatherpoly-safety-control-1|weatherpoly_safety-plane',
    'worker-id|weatherpoly|worker|False|weatherpoly-worker-1|weatherpoly_default',
    'postgres-id|weatherpoly|postgres|False|weatherpoly-postgres-1|weatherpoly_default,weatherpoly_safety-plane'
) -join [Environment]::NewLine
function script:Invoke-BoundedNative {
    param(
        [string] $FileName,
        [string[]] $Arguments,
        [string] $StandardInput,
        [int] $TimeoutSeconds,
        [int] $MaxOutputBytes
    )

    return [pscustomobject]@{
        exit_code = 0
        stdout = $script:mockTopologyStdout
        stderr = ''
    }
}
$safeTopology = Get-RunningProjectTopology -Containers $topologyContainers
Assert-Contract (
    [bool]$safeTopology.safe -and
    @($safeTopology.running_containers).Count -eq 4 -and
    @($safeTopology.unexpected_containers).Count -eq 0
) 'Exact running project topology was rejected.'
$script:mockTopologyStdout += (
    [Environment]::NewLine +
    'migrate-id|weatherpoly|migrate|False|weatherpoly-migrate-run-deadbeef|weatherpoly_default'
)
$unsafeTopology = Get-RunningProjectTopology -Containers $topologyContainers
Assert-Contract (
    -not [bool]$unsafeTopology.safe -and
    @($unsafeTopology.unexpected_containers).Count -eq 1
) 'Unexpected running project container was not rejected.'
$script:mockTopologyStdout = @(
    'api-id|weatherpoly|api|False|weatherpoly-api-1|weatherpoly_default',
    'safety-id|weatherpoly|safety-control|False|weatherpoly-safety-control-1|weatherpoly_safety-plane',
    'worker-id|weatherpoly|worker|False|weatherpoly-worker-1|weatherpoly_default',
    'postgres-id|weatherpoly|postgres|False|weatherpoly-postgres-1|weatherpoly_default,weatherpoly_safety-plane',
    'debug-id|other-project|debug|False|foreign-debug|weatherpoly_default'
) -join [Environment]::NewLine
$foreignNetworkTopology = Get-RunningProjectTopology -Containers $topologyContainers
Assert-Contract (
    -not [bool]$foreignNetworkTopology.safe -and
    @($foreignNetworkTopology.unexpected_containers).Count -eq 1
) 'Foreign container on a protected project network was not rejected.'
$script:mockTopologyStdout = @(
    'api-id|weatherpoly|api|False|weatherpoly-api-1|weatherpoly_default',
    'safety-id|weatherpoly|safety-control|False|weatherpoly-safety-control-1|weatherpoly_safety-plane',
    'worker-id|weatherpoly|worker|False|weatherpoly-worker-1|weatherpoly_default'
) -join [Environment]::NewLine
$missingTopology = Get-RunningProjectTopology -Containers $topologyContainers
Assert-Contract (
    -not [bool]$missingTopology.safe -and
    (@($missingTopology.missing_container_ids) -join ',') -eq 'postgres-id'
) 'Missing required running project container was not rejected.'

$ExpectedImageId = 'sha256:' + ('b' * 64)
$ExpectedRevision = 'c' * 40
$ExpectedReleaseDigest = 'd' * 64
$BaseUri = [Uri]'http://127.0.0.1:8000/'
$SafetyBaseUri = [Uri]'http://127.0.0.1:8001/'
function New-ContainerGuardFixture {
    param(
        [string] $Service,
        [string[]] $Networks,
        [string[]] $ClientAddresses = @(),
        [object[]] $PublishedPorts = @()
    )

    return [pscustomobject]@{
        running = $true
        oom_killed = $false
        restart_count = 0
        memory_limit_bytes = switch ($Service) {
            'api' { 1073741824L; break }
            'safety-control' { 268435456L; break }
            'worker' { 3221225472L; break }
            default { 0L; break }
        }
        operator_api_key_entry_count = if ($Service -eq 'api') { 1 } else { 0 }
        health = if ($Service -eq 'worker') { $null } else { 'healthy' }
        image_id = if ($Service -eq 'postgres') { 'postgres-image' } else { $ExpectedImageId }
        revision = if ($Service -eq 'postgres') { $null } else { $ExpectedRevision }
        release_digest = if ($Service -eq 'postgres') { $null } else { $ExpectedReleaseDigest }
        compose_project = 'weatherpoly'
        compose_service = $Service
        compose_oneoff = 'False'
        networks = @($Networks)
        client_addresses = @($ClientAddresses)
        published_ports = @($PublishedPorts)
    }
}
$guardContainers = [ordered]@{
    api = New-ContainerGuardFixture -Service 'api' `
        -Networks @('weatherpoly_default') -ClientAddresses @('172.30.0.2') `
        -PublishedPorts @(
            [pscustomobject]@{
                container_port = '8000/tcp'; host_ip = '127.0.0.1'; host_port = '8000'
            }
        )
    safety_control = New-ContainerGuardFixture -Service 'safety-control' `
        -Networks @('weatherpoly_safety-plane') `
        -ClientAddresses @('172.31.0.2') -PublishedPorts @(
            [pscustomobject]@{
                container_port = '8001/tcp'; host_ip = '127.0.0.1'; host_port = '8001'
            }
        )
    worker = New-ContainerGuardFixture -Service 'worker' `
        -Networks @('weatherpoly_default') -ClientAddresses @('172.30.0.3')
    postgres = New-ContainerGuardFixture -Service 'postgres' `
        -Networks @('weatherpoly_default','weatherpoly_safety-plane') `
        -PublishedPorts @(
            [pscustomobject]@{
                container_port = '5432/tcp'; host_ip = '127.0.0.1'; host_port = '5432'
            }
        )
    topology = [ordered]@{ safe = $true }
}
Assert-Contract (
    @(Get-ContainerGuardViolations -Containers $guardContainers).Count -eq 0
) 'Exact Compose/network topology fixture was rejected.'
$guardContainers.worker.memory_limit_bytes = 6442450944L
$memoryDrift = @(Get-ContainerGuardViolations -Containers $guardContainers)
Assert-Contract (
    'memory_limit_worker' -in $memoryDrift
) 'Worker memory-cap drift was not rejected continuously.'
$guardContainers.worker.memory_limit_bytes = 3221225472L
$guardContainers.safety_control.operator_api_key_entry_count = 1
$credentialDrift = @(Get-ContainerGuardViolations -Containers $guardContainers)
Assert-Contract (
    'operator_credential_safety_control' -in $credentialDrift
) 'Safety-control operator-credential leak was not rejected continuously.'
$guardContainers.safety_control.operator_api_key_entry_count = 0
$databaseSessionBoundary = New-DatabaseSessionBoundary -Containers $guardContainers
Assert-Contract (
    (@($databaseSessionBoundary.api_addresses) -join ',') -eq '172.30.0.2' -and
    (@($databaseSessionBoundary.worker_addresses) -join ',') -eq '172.30.0.3' -and
    (@($databaseSessionBoundary.safety_control_addresses) -join ',') -eq
        '172.31.0.2'
) 'Container-derived database session boundary was not exact.'
$guardContainers.safety_control.networks = @('weatherpoly_default')
$networkDrift = @(Get-ContainerGuardViolations -Containers $guardContainers)
Assert-Contract (
    'network_topology_safety_control' -in $networkDrift
) 'Safety-control network drift was not rejected continuously.'
Import-CanaryFunction -Name 'Invoke-BoundedNative'

$script:mockLogStdout = @(
    (
        '2026-07-29T12:00:01.000000000Z INFO execution cost maintenance: ' +
        'at=2026-07-29T12:00:01Z mark_claimed=1 observed=1 missing=0 ' +
        'deferred=0 mark_ids=41 finalize_claimed=1 sealed=1 deferred=0 ' +
        'finalize_ids=52'
    ),
    (
        '2026-07-29T12:00:02.000000000Z INFO ordinary cycle ' +
        'error=none'
    )
) -join [Environment]::NewLine
$script:mockLogStderr = ''
function script:Invoke-BoundedNative {
    param(
        [string] $FileName,
        [string[]] $Arguments,
        [string] $StandardInput,
        [int] $TimeoutSeconds,
        [int] $MaxOutputBytes
    )

    return [pscustomobject]@{
        exit_code = 0
        stdout = $script:mockLogStdout
        stderr = $script:mockLogStderr
    }
}
$safeLogs = Get-WorkerMaintenanceLogSnapshot `
    -WorkerContainer 'mock-worker' `
    -Since ([DateTimeOffset]'2026-07-29T12:00:00Z') `
    -Tail 100
Assert-Contract (
    $safeLogs.maintenance_rows -eq 1 -and
    $safeLogs.timestamp_parse_failures -eq 0 -and
    -not [bool]$safeLogs.possibly_truncated -and
    @($safeLogs.dangerous_log_lines).Count -eq 0 -and
    $safeLogs.claim_identity_mismatches -eq 0 -and
    @($safeLogs.repeated_zero_mark_finalize_ids).Count -eq 0 -and
    $safeLogs.latest_docker_timestamp -eq '2026-07-29T12:00:02.0000000+00:00'
) 'Timestamped normal worker logs were misparsed or falsely classified as errors.'

$script:mockLogStdout = @(
    (
        '2026-07-29T12:00:05.000000000Z INFO execution cost maintenance: ' +
        'at=2026-07-29T12:00:05Z mark_claimed=0 observed=0 missing=0 ' +
        'deferred=0 mark_ids=- finalize_claimed=1 sealed=0 deferred=1 ' +
        'finalize_ids=73'
    ),
    (
        '2026-07-29T12:00:20.000000000Z INFO execution cost maintenance: ' +
        'at=2026-07-29T12:00:20Z mark_claimed=0 observed=0 missing=0 ' +
        'deferred=0 mark_ids=- finalize_claimed=1 sealed=0 deferred=1 ' +
        'finalize_ids=73'
    )
) -join [Environment]::NewLine
$repeatedClaimLogs = Get-WorkerMaintenanceLogSnapshot `
    -WorkerContainer 'mock-worker' `
    -Since ([DateTimeOffset]'2026-07-29T12:00:00Z') `
    -Tail 100
Assert-Contract (
    $repeatedClaimLogs.claim_identity_mismatches -eq 0 -and
    (@($repeatedClaimLogs.repeated_zero_mark_finalize_ids) -join ',') -eq '73'
) (
    'Repeated zero-mark finalizer claim was not surfaced as scheduler contention.'
)

$script:mockLogStdout = (
    '2026-07-29T12:00:30.000000000Z INFO execution cost maintenance: ' +
    'at=2026-07-29T12:00:30Z mark_claimed=2 observed=0 missing=2 ' +
    'deferred=0 mark_ids=81 finalize_claimed=0 sealed=0 deferred=0 ' +
    'finalize_ids=-'
)
$invalidIdentityLogs = Get-WorkerMaintenanceLogSnapshot `
    -WorkerContainer 'mock-worker' `
    -Since ([DateTimeOffset]'2026-07-29T12:00:00Z') `
    -Tail 100
Assert-Contract ($invalidIdentityLogs.claim_identity_mismatches -eq 1) (
    'Claim count/identity mismatch was not rejected fail-closed.'
)

$script:mockLogStdout = (
    '2026-07-29T12:00:31.000000000Z INFO execution cost maintenance: ' +
    'at=2026-07-29T12:00:31Z mark_claimed=0 observed=0 missing=0 ' +
    'deferred=0 finalize_claimed=0 sealed=0 deferred=0'
)
$legacySchemaLogs = Get-WorkerMaintenanceLogSnapshot `
    -WorkerContainer 'mock-worker' `
    -Since ([DateTimeOffset]'2026-07-29T12:00:00Z') `
    -Tail 100
Assert-Contract (
    $legacySchemaLogs.maintenance_rows -eq 0 -and
    $legacySchemaLogs.claim_identity_mismatches -eq 1
) 'Legacy maintenance log schema was not rejected fail-closed.'

$script:mockLogStdout = (
    'not-a-docker-timestamp INFO ordinary cycle error=none' +
    [Environment]::NewLine +
    '2026-07-29T12:00:03.000000000Z ERROR scheduler overlap'
)
$unsafeLogs = Get-WorkerMaintenanceLogSnapshot `
    -WorkerContainer 'mock-worker' `
    -Since ([DateTimeOffset]'2026-07-29T12:00:00Z') `
    -Tail 100
Assert-Contract (
    $unsafeLogs.timestamp_parse_failures -eq 1 -and
    @($unsafeLogs.dangerous_log_lines).Count -eq 1
) 'Incomplete timestamps or a genuine ERROR log line were not surfaced.'

$script:mockLogStdout = @(
    1..100 |
        ForEach-Object {
            '2026-07-29T12:00:04.000000000Z INFO bounded-line-' + $_
        }
) -join [Environment]::NewLine
$truncatedLogs = Get-WorkerMaintenanceLogSnapshot `
    -WorkerContainer 'mock-worker' `
    -Since ([DateTimeOffset]'2026-07-29T12:00:00Z') `
    -Tail 100
Assert-Contract ([bool]$truncatedLogs.possibly_truncated) (
    'Worker-log tail saturation was not marked as potentially truncated.'
)
Import-CanaryFunction -Name 'Invoke-BoundedNative'

function New-ResourceSample {
    param(
        [double] $PostgresCpu = 0,
        [double] $ApiMemory = 1,
        [double] $SafetyMemory = 1,
        [double] $WorkerMemory = 1,
        [double] $PostgresMemory = 1
    )

    return [ordered]@{
        captured_at = '2026-07-29T12:00:00Z'
        api_cpu_percent = 0
        api_memory_percent = $ApiMemory
        safety_cpu_percent = 0
        safety_memory_percent = $SafetyMemory
        worker_cpu_percent = 0
        worker_memory_percent = $WorkerMemory
        postgres_cpu_percent = $PostgresCpu
        postgres_memory_percent = $PostgresMemory
    }
}

Assert-Contract (
    @(Get-ResourceGuardViolations -Samples @()).Count -eq 1 -and
    @(Get-ResourceGuardViolations -Samples @())[0] -eq 'resource_sample_missing'
) 'Empty resource evidence was accepted.'
$memoryViolations = @(
    Get-ResourceGuardViolations -Samples @(
        [pscustomobject](New-ResourceSample -ApiMemory 90)
    )
)
Assert-Contract ($memoryViolations -contains 'container_memory_budget') (
    '90% container memory threshold was not enforced.'
)
$singleCpuViolations = @(
    Get-ResourceGuardViolations -Samples @(
        [pscustomobject](New-ResourceSample -PostgresCpu 200)
    )
)
Assert-Contract (
    $singleCpuViolations -contains 'postgres_cpu_single_sample'
) '200% single-sample PostgreSQL CPU threshold was not enforced.'
$twoCpuViolations = @(
    Get-ResourceGuardViolations -Samples @(
        [pscustomobject](New-ResourceSample -PostgresCpu 150),
        [pscustomobject](New-ResourceSample -PostgresCpu 150)
    )
)
Assert-Contract (
    $twoCpuViolations -contains 'postgres_cpu_two_consecutive_samples'
) 'Two-consecutive-sample PostgreSQL CPU threshold was not enforced.'
$rollingSamples = @(
    1..7 | ForEach-Object {
        [pscustomobject](New-ResourceSample -PostgresCpu 140)
    }
)
$rollingViolations = @(Get-ResourceGuardViolations -Samples $rollingSamples)
Assert-Contract (
    $rollingViolations -contains 'postgres_cpu_rolling_30s_average'
) '30-second rolling PostgreSQL CPU threshold was not enforced.'
$belowRolling = @(
    1..7 | ForEach-Object {
        [pscustomobject](New-ResourceSample -PostgresCpu 139)
    }
)
Assert-Contract (
    @(Get-ResourceGuardViolations -Samples $belowRolling) -notcontains
        'postgres_cpu_rolling_30s_average'
) 'Rolling PostgreSQL CPU threshold produced a below-budget false positive.'
$resourceWindow = [Collections.Generic.List[object]]::new()
$lagViolations = @()
foreach ($sampleNumber in 1..8) {
    $actual = if ($sampleNumber -eq 8) { 43.0 } else { $sampleNumber * 5.0 }
    $lagViolations = @(
        Add-ResourceEvidenceSample -Window $resourceWindow `
            -Sample (New-ResourceSample) `
            -ScheduledElapsedSeconds ($sampleNumber * 5.0) `
            -ActualElapsedSeconds $actual
    )
}
Assert-Contract (
    $resourceWindow.Count -eq 7 -and
    $lagViolations -contains 'resource_sampler_schedule_lag'
) 'Resource rolling window or schedule-lag threshold is not enforced.'

$ExpectedResumeRequestId = 'release-paper-contract-test'
$ExpectedResumeActor = 'codex-release-owner'
$ExpectedResumeReason = (
    'Successor epoch and cold scheduler canary accepted; ' +
    'resume Polymarket PAPER only.'
)
$databaseSessionSql = Get-DatabaseSessionSql -Boundary $databaseSessionBoundary
$guardSql = Get-GuardSql -CommandBoundaryTime '2026-07-29T11:59:59Z' `
    -DatabaseSessionBoundary $databaseSessionBoundary
$fullSql = Get-FullSql -AnchorDatabaseTime '2026-07-29T12:00:00Z' `
    -CommandBoundaryTime '2026-07-29T11:59:59Z' `
    -DatabaseSessionBoundary $databaseSessionBoundary
foreach ($sqlFixture in @(
    [pscustomobject]@{ Name = 'guard'; Sql = $guardSql },
    [pscustomobject]@{ Name = 'full'; Sql = $fullSql }
)) {
    Assert-Contract (
        $sqlFixture.Sql.Contains("'runtime_database', $databaseSessionSql") -and
        $sqlFixture.Sql -match (
            "'database_boundaries',\s*jsonb_build_object\(\s*" +
            "'runtime_exact',\s*public\.assert_stratus_runtime_boundary\(false\),\s*" +
            "'safety_exact',\s*public\.assert_stratus_safety_boundary\(false\)"
        ) -and
        $sqlFixture.Sql -match "activity\.pid<>pg_backend_pid\(\)" -and
        $sqlFixture.Sql -match "activity\.usename='stratus'" -and
        $sqlFixture.Sql -match "activity\.application_name='stratus-api'" -and
        $sqlFixture.Sql -match "activity\.application_name='stratus-runtime'" -and
        $sqlFixture.Sql -match (
            "activity\.application_name='stratus-safety-control'"
        ) -and
        $sqlFixture.Sql -match (
            "SELECT count\(\*\) FROM signals WHERE " +
            "status='pending'::signal_status"
        ) -and
        $sqlFixture.Sql -match "ARRAY\['172\.30\.0\.2'\]::text\[\]" -and
        $sqlFixture.Sql -match "ARRAY\['172\.30\.0\.3'\]::text\[\]" -and
        $sqlFixture.Sql -match "ARRAY\['172\.31\.0\.2'\]::text\[\]" -and
        $sqlFixture.Sql -notmatch '__[A-Z][A-Z0-9_]+__'
    ) "Generated $($sqlFixture.Name) SQL lost its exact database boundary."
}
$activeState = [pscustomobject]@{
    database_now = '2026-07-29T12:00:05Z'
    database_boundaries = [pscustomobject]@{
        runtime_exact = $true
        safety_exact = $true
    }
    active_canary_leases = 1
    canary_lease = [pscustomobject]@{
        lease_id = $leaseId
        run_id = $runId
        acquire_request_id = $leaseAcquireRequestId
        resume_request_id = 'release-paper-contract-test'
        resume_actor = $ExpectedResumeActor
        resume_reason = $ExpectedResumeReason
        direct_stop_request_id = $leaseDirectStopRequestId
        release_request_id = $leaseReleaseRequestId
        nonce_sha256 = $leaseNonceSha256
        state = 'active'
        acquired_at = '2026-07-29T12:00:00Z'
        heartbeat_at = '2026-07-29T12:00:03Z'
        watchdog_at = '2026-07-29T12:00:04Z'
        expires_at = '2026-07-29T12:00:20Z'
    }
    global_control = [pscustomobject]@{
        mode = 'paper'
        kill_switch = $false
        halted_reason = $null
        live_confirmation_until = $null
    }
    venues = [pscustomobject]@{
        polymarket = [pscustomobject]@{
            enabled = $true
            mode = 'paper'
            kill_switch = $false
            halted_reason = $null
            live_confirmation_until = $null
        }
        binance_spot = [pscustomobject]@{
            enabled = $true
            mode = 'backtest'
            kill_switch = $true
            halted_reason = $null
            live_confirmation_until = $null
        }
    }
    pending_commands = 0
    pending_signals = 0
    unexpected_pending_commands = 0
    unexpected_commands_since_boundary = 0
    stop = $null
    release_stop = $null
    live = [pscustomobject]@{
        orders = 0
        positions = 0
        intents = 0
        fill_legs = 0
        enrollments = 0
        outcomes = 0
    }
    runtime_database = [pscustomobject]@{
        database_name = 'stratus'
        collector_exact = $true
        collector = [pscustomobject]@{
            pid = 900
            backend_type = 'client backend'
            usename = 'stratus'
            current_user = 'stratus'
            application_name = 'stratus-paper-canary-collector'
            client_addr = $null
        }
        expected_identities = [pscustomobject]@{
            api = [pscustomobject]@{
                backend_type = 'client backend'
                usename = 'stratus_app_752875213a111f12d876fef68bbf8259'
                application_name = 'stratus-api'
                client_addresses = @('172.30.0.2')
                max_sessions = 4
            }
            worker = [pscustomobject]@{
                backend_type = 'client backend'
                usename = 'stratus_app_752875213a111f12d876fef68bbf8259'
                application_name = 'stratus-runtime'
                client_addresses = @('172.30.0.3')
                max_sessions = 15
            }
            safety_control = [pscustomobject]@{
                backend_type = 'client backend'
                usename = 'stratus_safety_app_752875213a111f12d876fef68bbf8259'
                application_name = 'stratus-safety-control'
                client_addresses = @('172.31.0.2')
                max_sessions = 2
            }
        }
        client_backends = @(
            [pscustomobject]@{
                pid = 901
                backend_type = 'client backend'
                usename = 'stratus_app_752875213a111f12d876fef68bbf8259'
                application_name = 'stratus-api'
                client_addr = '172.30.0.2'
                identity = 'api'
            },
            [pscustomobject]@{
                pid = 902
                backend_type = 'client backend'
                usename = 'stratus_app_752875213a111f12d876fef68bbf8259'
                application_name = 'stratus-runtime'
                client_addr = '172.30.0.3'
                identity = 'worker'
            },
            [pscustomobject]@{
                pid = 903
                backend_type = 'client backend'
                usename = 'stratus_safety_app_752875213a111f12d876fef68bbf8259'
                application_name = 'stratus-safety-control'
                client_addr = '172.31.0.2'
                identity = 'safety_control'
            }
        )
        client_backend_sessions = 3
        api_sessions = 1
        worker_sessions = 1
        safety_control_sessions = 1
        unexpected_sessions = 0
        idle_in_transaction = 0
        lock_waiters = 0
    }
    scheduler = [pscustomobject]@{
        latest_maintenance_age_seconds = 2
        recent_maintenance_failures = 0
        recent_legacy_runs = 0
    }
    execution_guard = [pscustomobject]@{
        post_grace_missing_marks = 0
        finalizable_unsealed = 0
    }
}
Assert-Contract (@(Test-GuardState -State $activeState).Count -eq 0) (
    'Known-safe active PAPER guard state was rejected.'
)
$activePendingSignalState = (
    $activeState | ConvertTo-Json -Depth 20 -Compress | ConvertFrom-Json
)
$activePendingSignalState.pending_signals = 1
Assert-Contract (
    @(Test-GuardState -State $activePendingSignalState) -contains
        'pending_signals_present'
) 'Active PAPER admission accepted a pending Signal.'
$boundaryDriftState = $activeState.PSObject.Copy()
$boundaryDriftState.database_boundaries = [pscustomobject]@{
    runtime_exact = $true
    safety_exact = $false
}
Assert-Contract (
    @(Test-GuardState -State $boundaryDriftState) -contains
        'database_authority_boundary_drifted'
) 'Database authority drift was accepted by the active canary guard.'
$missingBoundaryState = (
    $activeState | ConvertTo-Json -Depth 20 -Compress | ConvertFrom-Json
)
$missingBoundaryState.PSObject.Properties.Remove('database_boundaries')
Assert-Contract (
    @(Test-GuardState -State $missingBoundaryState) -contains
        'database_authority_boundary_drifted'
) 'Missing database authority evidence was accepted by the active canary guard.'

foreach ($hostileFixture in @(
    [pscustomobject]@{
        Name = 'owner'
        Row = [pscustomobject]@{
            pid = 904
            backend_type = 'client backend'
            usename = 'stratus'
            application_name = 'psql'
            client_addr = '172.30.0.1'
            identity = 'unexpected'
        }
    },
    [pscustomobject]@{
        Name = 'general'
        Row = [pscustomobject]@{
            pid = 905
            backend_type = 'client backend'
            usename = 'postgres'
            application_name = 'reporting-client'
            client_addr = '172.30.0.99'
            identity = 'unexpected'
        }
    }
)) {
    $hostileState = (
        $activeState | ConvertTo-Json -Depth 20 -Compress | ConvertFrom-Json
    )
    $hostileState.runtime_database.client_backends = @(
        @($hostileState.runtime_database.client_backends) + $hostileFixture.Row
    )
    $hostileState.runtime_database.client_backend_sessions = 4
    $hostileState.runtime_database.unexpected_sessions = 1
    Assert-Contract (
        @(Test-GuardState -State $hostileState) -contains
            'unexpected_database_session'
    ) "Hostile $($hostileFixture.Name) database session was accepted."
}

$runtimeSourceState = (
    $activeState | ConvertTo-Json -Depth 20 -Compress | ConvertFrom-Json
)
$runtimeSourceState.runtime_database.client_backends[0].client_addr = '172.30.0.99'
Assert-Contract (
    @(Test-GuardState -State $runtimeSourceState) -contains
        'unexpected_database_session'
) 'Runtime identity from an unsealed source address was accepted.'
$staleLeaseState = $activeState.PSObject.Copy()
$staleLeaseState.canary_lease = $activeState.canary_lease.PSObject.Copy()
$staleLeaseState.canary_lease.watchdog_at = '2026-07-29T11:59:50Z'
Assert-Contract (
    @(Test-GuardState -State $staleLeaseState) -contains
        'paper_canary_safety_lease_diverged'
) 'Stale independent-watchdog evidence was accepted.'
$wrongLeaseIdentity = $activeState.PSObject.Copy()
$wrongLeaseIdentity.canary_lease = $activeState.canary_lease.PSObject.Copy()
$wrongLeaseIdentity.canary_lease.resume_actor = 'unexpected-actor'
Assert-Contract (
    @(Test-GuardState -State $wrongLeaseIdentity) -contains
        'paper_canary_safety_lease_diverged'
) 'Safety lease bound to the wrong resume actor was accepted.'
$wrongReleaseIdentity = $activeState.PSObject.Copy()
$wrongReleaseIdentity.canary_lease = $activeState.canary_lease.PSObject.Copy()
$wrongReleaseIdentity.canary_lease.release_request_id = (
    'paper-lease-release-unexpected'
)
Assert-Contract (
    @(Test-GuardState -State $wrongReleaseIdentity) -contains
        'paper_canary_safety_lease_diverged'
) 'Safety lease bound to the wrong immutable release authority was accepted.'
$duplicateLeaseState = $activeState.PSObject.Copy()
$duplicateLeaseState.active_canary_leases = 2
Assert-Contract (
    @(Test-GuardState -State $duplicateLeaseState) -contains
        'paper_canary_safety_lease_diverged'
) 'Multiple active PAPER canary safety leases were accepted.'
$failClosedState = $activeState.PSObject.Copy()
$failClosedState.global_control = $activeState.global_control.PSObject.Copy()
$failClosedState.global_control.kill_switch = $true
$failClosedState.venues = $activeState.venues.PSObject.Copy()
$failClosedState.venues.polymarket = (
    $activeState.venues.polymarket.PSObject.Copy()
)
$failClosedState.venues.polymarket.kill_switch = $true
Assert-Contract (
    @(Test-FailClosedGuardState -State $failClosedState).Count -eq 0
) 'Known-safe fail-closed preflight state was rejected.'
$failClosedPendingSignalState = (
    $failClosedState | ConvertTo-Json -Depth 20 -Compress | ConvertFrom-Json
)
$failClosedPendingSignalState.pending_signals = 1
Assert-Contract (
    @(Test-FailClosedGuardState -State $failClosedPendingSignalState) -contains
        'pending_signals_present'
) 'Fail-closed collector preflight accepted a pending Signal.'
$failClosedHostileState = (
    $failClosedState | ConvertTo-Json -Depth 20 -Compress | ConvertFrom-Json
)
$failClosedHostileState.runtime_database.client_backends = @(
    @($failClosedHostileState.runtime_database.client_backends) +
        [pscustomobject]@{
            pid = 906
            backend_type = 'client backend'
            usename = 'stratus'
            application_name = 'psql'
            client_addr = '172.30.0.1'
            identity = 'unexpected'
        }
)
$failClosedHostileState.runtime_database.client_backend_sessions = 4
$failClosedHostileState.runtime_database.unexpected_sessions = 1
Assert-Contract (
    @(Test-FailClosedGuardState -State $failClosedHostileState) -contains
        'unexpected_database_session'
) 'Hostile owner session was accepted by the fail-closed preflight guard.'
$failClosedState.venues.binance_spot = (
    $activeState.venues.binance_spot.PSObject.Copy()
)
$failClosedState.venues.binance_spot.kill_switch = $false
Assert-Contract (
    @(Test-FailClosedGuardState -State $failClosedState) -contains
        'binance_not_fail_closed'
) 'Unsafe Binance control was accepted by the preflight guard.'

$script:preflightDatabaseTime = '2026-07-29T12:00:00Z'
$resumeItem = [pscustomobject]@{
    request_id = $ExpectedResumeRequestId
    command = 'resume'
    actor = $ExpectedResumeActor
    reason = $ExpectedResumeReason
    payload = [pscustomobject]@{ venue = 'polymarket' }
    status = 'succeeded'
    created_at = '2026-07-29T12:00:01Z'
    executed_at = '2026-07-29T12:00:02Z'
}
Assert-ExactResume `
    -Resume $resumeItem -DatabaseNow ([DateTimeOffset]'2026-07-29T12:00:03Z')
$missingDatabaseNowRejected = $false
try {
    Assert-ExactResume -Resume $resumeItem
}
catch {
    $missingDatabaseNowRejected = $true
}
Assert-Contract $missingDatabaseNowRejected (
    'Assert-ExactResume accepted a call without authoritative DatabaseNow.'
)
$futureResume = $resumeItem.PSObject.Copy()
$futureResume.executed_at = '2026-07-29T12:00:20Z'
$futureResumeRejected = $false
try {
    Assert-ExactResume `
        -Resume $futureResume -DatabaseNow ([DateTimeOffset]'2026-07-29T12:00:03Z')
}
catch {
    $futureResumeRejected = $true
}
Assert-Contract $futureResumeRejected (
    'Resume execution timestamp beyond the database-clock bound was accepted.'
)

$artifactRoot = Join-Path ([IO.Path]::GetTempPath()) (
    'stratus-paper-canary-artifact-' + [guid]::NewGuid().ToString('N')
)
[void][IO.Directory]::CreateDirectory($artifactRoot)
try {
    $artifactPath = Join-Path $artifactRoot 'immutable.json'
    [void](
        Write-ImmutableJson `
            -Path $artifactPath -Payload ([ordered]@{ safe = $true })
    )
    $duplicateRejected = $false
    try {
        [void](
            Write-ImmutableJson `
                -Path $artifactPath -Payload ([ordered]@{ safe = $false })
        )
    }
    catch {
        $duplicateRejected = $true
    }
    Assert-Contract $duplicateRejected (
        'CreateNew artifact protection accepted an overwrite.'
    )
}
finally {
    Remove-Item -LiteralPath $artifactRoot -Recurse -Force
}

Add-Type -AssemblyName System.Net.Http
$StopConfirmationSeconds = 1
$script:stopScenario = $null
$script:stopBody = $null
$script:stopPostCalls = 0
$script:stopPollCalls = 0
$script:stopPaths = [Collections.Generic.List[string]]::new()
$script:stopBodies = [Collections.Generic.List[string]]::new()

function New-MockStopState {
    param([bool] $Succeeded)

    $command = if ($Succeeded) {
        [pscustomobject]@{
            id = 88
            request_id = $script:stopBody.request_id
            command = 'stop'
            actor = $script:stopBody.actor
            reason = $script:stopBody.reason
            payload = [pscustomobject]@{ venue = 'polymarket' }
            status = 'succeeded'
            created_at = '2026-07-29T12:00:02Z'
            executed_at = '2026-07-29T12:00:03Z'
        }
    }
    else {
        $null
    }
    return [pscustomobject]@{
        database_now = '2026-07-29T12:00:04Z'
        command = $command
        active_canary_leases = 0
        canary_lease = $null
        lease_stop_command = $null
        global = [pscustomobject]@{
            mode = 'paper'
            kill_switch = $true
            live_confirmation_until = $null
        }
        polymarket = [pscustomobject]@{
            enabled = $true
            mode = 'paper'
            kill_switch = $true
            live_confirmation_until = $null
        }
        binance = [pscustomobject]@{
            enabled = $true
            mode = 'backtest'
            kill_switch = $true
            halted_reason = $null
            live_confirmation_until = $null
        }
        pending_commands = 0
        pending_signals = 0
        nonterminal_live_orders = 0
        live_rows = 0
    }
}

function New-MockStopResponseJson {
    param(
        [Parameter(Mandatory = $true)][bool] $Created,
        [int] $ExpiredSignals = 0,
        [int] $CancelRequestedLiveOrders = 0
    )

    return [pscustomobject]@{
        created = $Created
        idempotent = -not $Created
        expired_signals = $ExpiredSignals
        cancel_requested_live_orders = $CancelRequestedLiveOrders
        rejected_pending_resume = 0
        item = (New-MockStopState -Succeeded $true).command
    }
}

function script:Invoke-JsonRequest {
    param(
        [Net.Http.HttpClient] $Client,
        [string] $Method,
        [string] $Path,
        [string] $JsonBody
    )

    $script:stopPostCalls += 1
    $script:stopPaths.Add($Path)
    $script:stopBodies.Add($JsonBody)
    switch ($script:stopScenario) {
        'transport_then_replay' {
            if ($script:stopPostCalls -eq 1) {
                throw [IO.IOException]::new('ambiguous transport failure')
            }
            return [pscustomobject]@{
                status_code = 200
                json = New-MockStopResponseJson -Created $false
            }
        }
        'committed_before_transport_failure' {
            throw [IO.IOException]::new('response lost after commit')
        }
        'retryable_503_then_replay' {
            if ($script:stopPostCalls -eq 1) {
                return [pscustomobject]@{
                    status_code = 503
                    json = [pscustomobject]@{
                        detail = 'mock retryable failure'
                        retryable = $true
                    }
                }
            }
            return [pscustomobject]@{
                status_code = 200
                json = New-MockStopResponseJson -Created $false
            }
        }
        'cleanup_then_replay' {
            return [pscustomobject]@{
                status_code = 200
                json = New-MockStopResponseJson `
                    -Created ($script:stopPostCalls -eq 1) `
                    -ExpiredSignals $(if ($script:stopPostCalls -gt 1) { 1 } else { 0 }) `
                    -CancelRequestedLiveOrders $(
                        if ($script:stopPostCalls -gt 1) { 1 } else { 0 }
                    )
            }
        }
        'cleanup_never_converges' {
            return [pscustomobject]@{
                status_code = 200
                json = New-MockStopResponseJson `
                    -Created ($script:stopPostCalls -eq 1)
            }
        }
        'cleanup_rejected' {
            if ($script:stopPostCalls -gt 1) {
                return [pscustomobject]@{
                    status_code = 409
                    json = [pscustomobject]@{
                        detail = 'mock non-retryable failure'
                        retryable = $false
                    }
                }
            }
            return [pscustomobject]@{
                status_code = 200
                json = New-MockStopResponseJson -Created $true
            }
        }
        'cleanup_transport_ambiguous' {
            if ($script:stopPostCalls -gt 1) {
                throw [IO.IOException]::new(
                    'cleanup replay transport ambiguity'
                )
            }
            return [pscustomobject]@{
                status_code = 200
                json = New-MockStopResponseJson -Created $true
            }
        }
        'always_ambiguous' {
            throw [IO.IOException]::new('persistent transport failure')
        }
        default {
            throw "Unknown stop test scenario: $($script:stopScenario)"
        }
    }
}

function script:Get-EmergencyStopState {
    param(
        [string] $PostgresContainer,
        [string] $RequestId
    )

    $script:stopPollCalls += 1
    switch ($script:stopScenario) {
        'committed_before_transport_failure' {
            return New-MockStopState -Succeeded $true
        }
        'always_ambiguous' {
            return New-MockStopState -Succeeded $false
        }
        'cleanup_then_replay' {
            $state = New-MockStopState -Succeeded $true
            if ($script:stopPostCalls -lt 2) {
                $state.pending_signals = 1
                $state.nonterminal_live_orders = 1
            }
            return $state
        }
        { $_ -in @(
            'cleanup_never_converges',
            'cleanup_rejected',
            'cleanup_transport_ambiguous'
        ) } {
            $state = New-MockStopState -Succeeded $true
            $state.pending_signals = 1
            return $state
        }
        default {
            if ($script:stopPostCalls -lt 2) {
                return New-MockStopState -Succeeded $false
            }
            return New-MockStopState -Succeeded $true
        }
    }
}

$binanceBaseline = [pscustomobject]@{
    enabled = $true
    mode = 'backtest'
    kill_switch = $true
    halted_reason = $null
    live_confirmation_until = $null
}
$script:failClosedEvents = [Collections.Generic.List[string]]::new()
$barrierResult = Invoke-FailClosedStopBarrier `
    -StopRequired $true `
    -CurrentStopEvidence $null `
    -StopRequestId 'paper-canary-stop-barrier-test' `
    -EmergencyStopAction {
        $script:failClosedEvents.Add('emergency-stop')
        return [pscustomobject]@{
            request_id = 'paper-canary-stop-barrier-test'
            status = 'succeeded'
            controls_confirmed = $true
        }
    }
$script:failClosedEvents.Add('diagnostics')
Assert-Contract (
    [bool]$barrierResult.controls_confirmed -and
    ($script:failClosedEvents -join ',') -eq 'emergency-stop,diagnostics'
) (
    'Executed fail-closed injection did not complete the emergency-stop ' +
    'barrier before diagnostics.'
)
$failedBarrier = Invoke-FailClosedStopBarrier `
    -StopRequired $true `
    -CurrentStopEvidence $null `
    -StopRequestId 'paper-canary-stop-barrier-failed' `
    -EmergencyStopAction {
        throw 'injected independent safety-control failure'
    }
Assert-Contract (
    -not [bool]$failedBarrier.controls_confirmed -and
    [string]$failedBarrier.status -eq 'unconfirmed' -and
    [string]$failedBarrier.error -match 'injected independent'
) 'Fail-closed barrier did not preserve an injected stop failure.'
$script:stopBarrierUnexpectedCall = $false
$alreadyStopped = [pscustomobject]@{
    request_id = 'paper-canary-stop-already-confirmed'
    status = 'succeeded'
    controls_confirmed = $true
}
$idempotentBarrier = Invoke-FailClosedStopBarrier `
    -StopRequired $true `
    -CurrentStopEvidence $alreadyStopped `
    -StopRequestId 'paper-canary-stop-already-confirmed' `
    -EmergencyStopAction {
        $script:stopBarrierUnexpectedCall = $true
        throw 'confirmed stop must not be repeated'
    }
Assert-Contract (
    -not $script:stopBarrierUnexpectedCall -and
    [bool]$idempotentBarrier.controls_confirmed
) 'Fail-closed barrier repeated an already confirmed emergency stop.'

$script:stopBody = New-StopBody -RunId ([guid]::NewGuid().ToString('N'))
foreach ($terminalState in @(
    [pscustomobject]@{
        state = 'released'
        request_id = 'paper-lease-release-terminal-link'
    },
    [pscustomobject]@{
        state = 'expired_stopped'
        request_id = 'paper-lease-watchdog-terminal-link'
    },
    [pscustomobject]@{
        state = 'revoked_stopped'
        request_id = [string]$script:stopBody.request_id
    }
)) {
    $linkedState = New-MockStopState -Succeeded $true
    $linkedCommandId = if ($terminalState.state -eq 'revoked_stopped') {
        88
    }
    else {
        89
    }
    $linkedState.canary_lease = [pscustomobject]@{
        state = $terminalState.state
        acquired_at = '2026-07-29T12:00:01Z'
        release_request_id = 'paper-lease-release-terminal-link'
        watchdog_stop_request_id = 'paper-lease-watchdog-terminal-link'
        direct_stop_request_id = [string]$script:stopBody.request_id
        stop_command_id = $linkedCommandId
    }
    $linkedState.lease_stop_command = [pscustomobject]@{
        id = $linkedCommandId
        request_id = $terminalState.request_id
        command = 'stop'
        actor = 'terminal-link-test'
        reason = 'terminal link test'
        payload = [pscustomobject]@{ venue = 'polymarket' }
        status = 'succeeded'
        created_at = '2026-07-29T12:00:02Z'
        executed_at = '2026-07-29T12:00:03Z'
    }
    Assert-Contract (
        Assert-EmergencyStopControls -State $linkedState `
            -Body $script:stopBody -BinanceBaseline $binanceBaseline
    ) "Emergency-stop validation rejected linked $($terminalState.state) state."

    $unlinkedState = $linkedState.PSObject.Copy()
    $unlinkedState.lease_stop_command = (
        $linkedState.lease_stop_command.PSObject.Copy()
    )
    $unlinkedState.lease_stop_command.request_id = 'wrong-terminal-stop-id'
    $unlinkedRejected = $false
    try {
        [void](
            Assert-EmergencyStopControls -State $unlinkedState `
                -Body $script:stopBody -BinanceBaseline $binanceBaseline
        )
    }
    catch {
        $unlinkedRejected = $true
    }
    Assert-Contract $unlinkedRejected (
        "Emergency-stop validation accepted unlinked $($terminalState.state) state."
    )
}
$priorRuntimeStopState = New-MockStopState -Succeeded $true
$priorRuntimeStopState.canary_lease = [pscustomobject]@{
    state = 'revoked_stopped'
    acquired_at = '2026-07-29T12:00:01Z'
    release_request_id = 'paper-lease-release-prior-runtime'
    watchdog_stop_request_id = 'paper-lease-watchdog-prior-runtime'
    direct_stop_request_id = [string]$script:stopBody.request_id
    stop_command_id = 77
}
$priorRuntimeStopState.lease_stop_command = [pscustomobject]@{
    id = 77
    request_id = 'paper-runtime-prior-stop'
    command = 'stop'
    actor = 'runtime-stop-test'
    reason = 'runtime stop won the race before direct safety stop'
    payload = [pscustomobject]@{ venue = 'polymarket' }
    status = 'succeeded'
    created_at = '2026-07-29T12:00:01Z'
    executed_at = '2026-07-29T12:00:02Z'
}
Assert-Contract (
    Assert-EmergencyStopControls -State $priorRuntimeStopState `
        -Body $script:stopBody -BinanceBaseline $binanceBaseline
) 'Emergency-stop validation rejected a proven prior runtime STOP race.'
foreach ($staleTimestampField in @('created_at','executed_at')) {
    $staleRuntimeStopState = $priorRuntimeStopState.PSObject.Copy()
    $staleRuntimeStopState.canary_lease = (
        $priorRuntimeStopState.canary_lease.PSObject.Copy()
    )
    $staleRuntimeStopState.lease_stop_command = (
        $priorRuntimeStopState.lease_stop_command.PSObject.Copy()
    )
    $staleRuntimeStopState.lease_stop_command.$staleTimestampField = (
        '2026-07-29T12:00:00Z'
    )
    $staleRuntimeStopRejected = $false
    try {
        [void](
            Assert-EmergencyStopControls -State $staleRuntimeStopState `
                -Body $script:stopBody -BinanceBaseline $binanceBaseline
        )
    }
    catch {
        $staleRuntimeStopRejected = $true
    }
    Assert-Contract $staleRuntimeStopRejected (
        'Emergency-stop validation accepted a prior runtime STOP whose ' +
        "$staleTimestampField predates lease acquisition."
    )
}
$futureRuntimeStopState = $priorRuntimeStopState.PSObject.Copy()
$futureRuntimeStopState.lease_stop_command = (
    $priorRuntimeStopState.lease_stop_command.PSObject.Copy()
)
$futureRuntimeStopState.lease_stop_command.executed_at = (
    '2026-07-29T12:00:04Z'
)
$futureRuntimeStopRejected = $false
try {
    [void](
        Assert-EmergencyStopControls -State $futureRuntimeStopState `
            -Body $script:stopBody -BinanceBaseline $binanceBaseline
    )
}
catch {
    $futureRuntimeStopRejected = $true
}
Assert-Contract $futureRuntimeStopRejected (
    'Emergency-stop validation accepted a lease stop ordered after direct STOP.'
)
$expiredSafeState = New-MockStopState -Succeeded $true
$expiredSafeState.canary_lease = [pscustomobject]@{
    state = 'expired_safe'
    stop_command_id = $null
}
Assert-Contract (
    Assert-EmergencyStopControls -State $expiredSafeState `
        -Body $script:stopBody -BinanceBaseline $binanceBaseline
) 'Emergency-stop validation rejected a correctly safe-expired lease.'

$releasedLeaseState = [pscustomobject]@{
    database_now = '2026-07-29T12:01:00Z'
    lease = [pscustomobject]@{
        lease_id = $leaseId
        run_id = $runId
        acquire_request_id = $leaseAcquireRequestId
        resume_request_id = $ExpectedResumeRequestId
        resume_actor = $ExpectedResumeActor
        resume_reason = $ExpectedResumeReason
        direct_stop_request_id = $leaseDirectStopRequestId
        release_request_id = $leaseReleaseRequestId
        nonce_sha256 = $leaseNonceSha256
        state = 'released'
        released_at = '2026-07-29T12:00:59Z'
        stop_command_id = 99
    }
    stop_command = [pscustomobject]@{
        id = 99
        request_id = $leaseReleaseRequestId
        command = 'stop'
        actor = 'stratus-safety-release'
        reason = "Successful PAPER canary $leaseId completed."
        payload = [pscustomobject]@{ venue = 'polymarket' }
        status = 'succeeded'
        executed_at = '2026-07-29T12:00:59Z'
    }
    active_leases = 0
    global_control = [pscustomobject]@{
        mode = 'paper'
        kill_switch = $true
        live_confirmation_until = $null
    }
    polymarket = [pscustomobject]@{
        enabled = $true
        mode = 'paper'
        kill_switch = $true
        live_confirmation_until = $null
    }
    binance = [pscustomobject]@{
        enabled = $true
        mode = 'backtest'
        kill_switch = $true
        halted_reason = $null
        live_confirmation_until = $null
    }
    pending_commands = 0
    unexpected_commands_since_boundary = 0
    live_rows = 0
}
Assert-ReleasedCanaryLeaseState `
    -State $releasedLeaseState -BinanceBaseline $binanceBaseline
foreach ($unsafeTerminalField in @(
    'active_leases',
    'pending_commands',
    'unexpected_commands_since_boundary',
    'live_rows'
)) {
    $unsafeTerminalState = $releasedLeaseState.PSObject.Copy()
    $unsafeTerminalState.PSObject.Properties[$unsafeTerminalField].Value = 1
    $unsafeTerminalRejected = $false
    try {
        Assert-ReleasedCanaryLeaseState `
            -State $unsafeTerminalState -BinanceBaseline $binanceBaseline
    }
    catch {
        $unsafeTerminalRejected = $true
    }
    Assert-Contract $unsafeTerminalRejected (
        "Released lease accepted unsafe terminal field $unsafeTerminalField."
    )
}
$httpClient = [Net.Http.HttpClient]::new()
try {
    foreach ($scenario in @(
        'transport_then_replay',
        'retryable_503_then_replay'
    )) {
        $script:stopScenario = $scenario
        $script:stopBody = New-StopBody -RunId ([guid]::NewGuid().ToString('N'))
        $script:stopPostCalls = 0
        $script:stopPollCalls = 0
        $script:stopPaths.Clear()
        $script:stopBodies.Clear()
        $confirmed = Invoke-EmergencyStop `
            -SafetyClient $httpClient `
            -Body $script:stopBody `
            -PostgresContainer 'mock-postgres' `
            -BinanceBaseline $binanceBaseline
        $bodyIds = @(
            $script:stopBodies |
                ForEach-Object { ($_ | ConvertFrom-Json).request_id } |
                Select-Object -Unique
        )
        Assert-Contract (
            $script:stopPostCalls -eq 2 -and
            $script:stopPollCalls -ge 2 -and
            $confirmed.post_attempts -eq 2 -and
            [bool]$confirmed.ambiguous_first_post -and
            [bool]$confirmed.controls_confirmed -and
            @($script:stopPaths | Select-Object -Unique).Count -eq 1 -and
            $script:stopPaths[0] -eq 'api/v1/emergency-stop' -and
            $bodyIds.Count -eq 1 -and
            $bodyIds[0] -eq $script:stopBody.request_id
        ) "Emergency-stop ambiguity scenario $scenario violated idempotency."
    }

    $script:stopScenario = 'committed_before_transport_failure'
    $script:stopBody = New-StopBody -RunId ([guid]::NewGuid().ToString('N'))
    $script:stopPostCalls = 0
    $script:stopPollCalls = 0
    $script:stopPaths.Clear()
    $script:stopBodies.Clear()
    $committed = Invoke-EmergencyStop `
        -SafetyClient $httpClient `
        -Body $script:stopBody `
        -PostgresContainer 'mock-postgres' `
        -BinanceBaseline $binanceBaseline
    Assert-Contract (
        $script:stopPostCalls -eq 1 -and
        $script:stopPollCalls -ge 2 -and
        $committed.post_attempts -eq 1 -and
        [bool]$committed.ambiguous_first_post -and
        [bool]$committed.controls_confirmed
    ) 'Committed-but-response-lost emergency stop was not recovered from the database.'

    $StopConfirmationSeconds = 3
    $script:stopScenario = 'cleanup_then_replay'
    $script:stopBody = New-StopBody -RunId ([guid]::NewGuid().ToString('N'))
    $script:stopPostCalls = 0
    $script:stopPollCalls = 0
    $script:stopPaths.Clear()
    $script:stopBodies.Clear()
    $cleaned = Invoke-EmergencyStop `
        -SafetyClient $httpClient `
        -Body $script:stopBody `
        -PostgresContainer 'mock-postgres' `
        -BinanceBaseline $binanceBaseline
    $cleanupBodyIds = @(
        $script:stopBodies |
            ForEach-Object { ($_ | ConvertFrom-Json).request_id } |
            Select-Object -Unique
    )
    Assert-Contract (
        $script:stopPostCalls -eq 2 -and
        $script:stopPollCalls -ge 2 -and
        $cleaned.post_attempts -eq 1 -and
        $cleaned.cleanup_retry_attempts -eq 1 -and
        [int]$cleaned.pending_signals -eq 0 -and
        [int]$cleaned.nonterminal_live_orders -eq 0 -and
        [int]$cleaned.last_cleanup_expired_signals -eq 1 -and
        [int]$cleaned.last_cleanup_cancel_requested_live_orders -eq 1 -and
        [bool]$cleaned.controls_confirmed -and
        @($script:stopPaths | Select-Object -Unique).Count -eq 1 -and
        $cleanupBodyIds.Count -eq 1 -and
        $cleanupBodyIds[0] -eq $script:stopBody.request_id
    ) (
        'Emergency-stop cleanup did not replay the exact request and ' +
        'converge its skipped rows.'
    )

    $StopConfirmationSeconds = 1
    $script:stopScenario = 'cleanup_never_converges'
    $script:stopBody = New-StopBody -RunId ([guid]::NewGuid().ToString('N'))
    $script:stopPostCalls = 0
    $script:stopPollCalls = 0
    $script:stopPaths.Clear()
    $script:stopBodies.Clear()
    $cleanupTimeoutRejected = $false
    $cleanupTimeoutMessage = $null
    try {
        [void](
            Invoke-EmergencyStop `
                -SafetyClient $httpClient `
                -Body $script:stopBody `
                -PostgresContainer 'mock-postgres' `
                -BinanceBaseline $binanceBaseline
        )
    }
    catch {
        $cleanupTimeoutRejected = $true
        $cleanupTimeoutMessage = $_.Exception.GetBaseException().Message
    }
    $cleanupRetryMatch = [regex]::Match(
        [string]$cleanupTimeoutMessage,
        'cleanup_retries=(\d+)'
    )
    $cleanupRetries = if ($cleanupRetryMatch.Success) {
        [int]$cleanupRetryMatch.Groups[1].Value
    }
    else {
        0
    }
    $cleanupTimeoutBodyIds = @(
        $script:stopBodies |
            ForEach-Object { ($_ | ConvertFrom-Json).request_id } |
            Select-Object -Unique
    )
    Assert-Contract (
        $cleanupTimeoutRejected -and
        $cleanupRetries -ge 1 -and
        $script:stopPostCalls -eq (1 + $cleanupRetries) -and
        $cleanupTimeoutMessage -match 'pending_signals=1' -and
        $cleanupTimeoutMessage -match 'last_cleanup_expired=0' -and
        $cleanupTimeoutMessage -match (
            'last_cleanup_error=accepted cleanup replay did not converge ' +
            'before the confirmation deadline$'
        ) -and
        $cleanupTimeoutBodyIds.Count -eq 1 -and
        $cleanupTimeoutBodyIds[0] -eq $script:stopBody.request_id
    ) (
        'Emergency-stop accepted non-converging cleanup or omitted its ' +
        'bounded failure diagnostics. ' +
        "rejected=$cleanupTimeoutRejected; posts=$script:stopPostCalls; " +
        "message=$cleanupTimeoutMessage"
    )

    foreach ($cleanupFailure in @(
        [pscustomobject]@{
            scenario = 'cleanup_rejected'
            expected = 'Exact Polymarket cleanup replay was not accepted \(HTTP 409\)\.'
        },
        [pscustomobject]@{
            scenario = 'cleanup_transport_ambiguous'
            expected = (
                'last_cleanup_error=cleanup replay transport ambiguity$'
            )
        }
    )) {
        $script:stopScenario = $cleanupFailure.scenario
        $script:stopBody = New-StopBody -RunId ([guid]::NewGuid().ToString('N'))
        $script:stopPostCalls = 0
        $script:stopPollCalls = 0
        $script:stopPaths.Clear()
        $script:stopBodies.Clear()
        $cleanupFailureMessage = $null
        try {
            [void](
                Invoke-EmergencyStop `
                    -SafetyClient $httpClient `
                    -Body $script:stopBody `
                    -PostgresContainer 'mock-postgres' `
                    -BinanceBaseline $binanceBaseline
            )
        }
        catch {
            $cleanupFailureMessage = $_.Exception.GetBaseException().Message
        }
        $cleanupFailureBodyIds = @(
            $script:stopBodies |
                ForEach-Object { ($_ | ConvertFrom-Json).request_id } |
                Select-Object -Unique
        )
        Assert-Contract (
            $script:stopPostCalls -ge 2 -and
            $cleanupFailureMessage -match $cleanupFailure.expected -and
            $cleanupFailureBodyIds.Count -eq 1 -and
            $cleanupFailureBodyIds[0] -eq $script:stopBody.request_id
        ) (
            "Cleanup scenario $($cleanupFailure.scenario) masked its " +
            'failure or changed STOP request identity. ' +
            "message=$cleanupFailureMessage"
        )
    }

    $script:stopScenario = 'always_ambiguous'
    $script:stopBody = New-StopBody -RunId ([guid]::NewGuid().ToString('N'))
    $script:stopPostCalls = 0
    $script:stopPollCalls = 0
    $script:stopPaths.Clear()
    $script:stopBodies.Clear()
    $boundedAmbiguityRejected = $false
    try {
        [void](
            Invoke-EmergencyStop `
                -SafetyClient $httpClient `
                -Body $script:stopBody `
                -PostgresContainer 'mock-postgres' `
                -BinanceBaseline $binanceBaseline
        )
    }
    catch {
        $boundedAmbiguityRejected = (
            $_.Exception.GetBaseException().Message -match (
                'remained ambiguous after one idempotent retry'
            )
        )
    }
    Assert-Contract (
        $boundedAmbiguityRejected -and
        $script:stopPostCalls -eq 2 -and
        $script:stopPollCalls -eq 2
    ) 'Emergency stop exceeded or failed to enforce its two-attempt ambiguity bound.'
}
finally {
    $httpClient.Dispose()
}

# Exercise the 15-second lease timing contract with logical maximum-latency
# calls.  Every bounded multi-call collector must heartbeat after each call,
# and a heartbeat failure must stop collection immediately.
foreach ($name in @(
    'Get-ContainerSet',
    'Get-HttpSnapshot',
    'Get-SafetyHttpSnapshot',
    'Get-CoherentHttpDatabaseSnapshot'
)) {
    Import-CanaryFunction -Name $name
}
$script:heartbeatLogicalSeconds = 0
$script:heartbeatEvents = [Collections.Generic.List[string]]::new()
$heartbeatAction = {
    $script:heartbeatEvents.Add(
        "heartbeat:$script:heartbeatLogicalSeconds"
    )
}
function script:Get-ContainerSnapshot {
    param([string] $Service, [string] $ContainerId)

    $script:heartbeatLogicalSeconds += 12
    $script:heartbeatEvents.Add(
        "container:${Service}:$script:heartbeatLogicalSeconds"
    )
    return [pscustomobject]@{
        service = $Service
        container_id = $ContainerId
    }
}
function script:Get-RunningProjectTopology {
    param([Collections.IDictionary] $Containers)

    $script:heartbeatLogicalSeconds += 12
    $script:heartbeatEvents.Add(
        "topology:$script:heartbeatLogicalSeconds"
    )
    return [ordered]@{ safe = $true }
}
[void](
    Get-ContainerSet -ApiContainer 'api-id' -SafetyContainer 'safety-id' `
        -WorkerContainer 'worker-id' -PostgresContainer 'postgres-id' `
        -HeartbeatAction $heartbeatAction
)
Assert-Contract (
    ($script:heartbeatEvents -join ',') -eq (
        'container:api:12,heartbeat:12,' +
        'container:safety-control:24,heartbeat:24,' +
        'container:worker:36,heartbeat:36,' +
        'container:postgres:48,heartbeat:48,' +
        'topology:60,heartbeat:60'
    )
) 'Container inspection does not heartbeat in exact bounded-call order.'

$script:EndpointInventory = @(
    [pscustomobject]@{ Name = 'healthz'; Path = 'healthz' },
    [pscustomobject]@{ Name = 'readyz'; Path = 'readyz' }
)
function script:Invoke-JsonRequest {
    param(
        [Net.Http.HttpClient] $Client,
        [string] $Method,
        [string] $Path,
        [string] $JsonBody
    )

    $script:heartbeatLogicalSeconds += 12
    $script:heartbeatEvents.Add(
        "http:${Path}:$script:heartbeatLogicalSeconds"
    )
    return [pscustomobject]@{
        status_code = 200
        elapsed_ms = 12000
        bytes = 2
        content_sha256 = 'd' * 64
        json = if ($Path -eq 'readyz') {
            [pscustomobject]@{ status = 'ready'; reasons = @() }
        }
        else {
            [pscustomobject]@{ status = 'ok' }
        }
    }
}
$heartbeatClient = [Net.Http.HttpClient]::new()
try {
    $script:heartbeatLogicalSeconds = 0
    $script:heartbeatEvents.Clear()
    [void](
        Get-HttpSnapshot -Client $heartbeatClient `
            -HeartbeatAction $heartbeatAction
    )
    Assert-Contract (
        ($script:heartbeatEvents -join ',') -eq (
            'http:healthz:12,heartbeat:12,http:readyz:24,heartbeat:24'
        )
    ) 'HTTP snapshot does not heartbeat in exact bounded-call order.'

    $script:heartbeatLogicalSeconds = 0
    $script:heartbeatEvents.Clear()
    [void](
        Get-SafetyHttpSnapshot -Client $heartbeatClient `
            -HeartbeatAction $heartbeatAction
    )
    Assert-Contract (
        ($script:heartbeatEvents -join ',') -eq (
            'http:healthz:12,heartbeat:12,http:readyz:24,heartbeat:24'
        )
    ) 'Safety HTTP snapshot does not heartbeat in exact bounded-call order.'

    function script:Get-FullSql {
        param(
            [string] $AnchorDatabaseTime,
            [string] $CommandBoundaryTime,
            [Collections.IDictionary] $DatabaseSessionBoundary
        )
        return "SELECT '$AnchorDatabaseTime', '$CommandBoundaryTime'"
    }
    function script:Invoke-ReadOnlySql {
        param([string] $PostgresContainer, [string] $Sql)
        $script:heartbeatLogicalSeconds += 3
        $script:heartbeatEvents.Add(
            "sql:$script:heartbeatLogicalSeconds"
        )
        return [pscustomobject]@{
            api_expected = [ordered]@{ stable = $true }
        }
    }
    function script:Get-ApiDatabaseCoherenceViolations {
        param([object] $Http, [object] $Database)
        return @()
    }
    $script:heartbeatLogicalSeconds = 0
    $script:heartbeatEvents.Clear()
    $coherentHeartbeat = Get-CoherentHttpDatabaseSnapshot `
        -Client $heartbeatClient -PostgresContainer 'mock-postgres' `
        -AnchorDatabaseTime '2026-07-29T12:00:00Z' `
        -CommandBoundaryTime '2026-07-29T12:00:00Z' `
        -DatabaseSessionBoundary ([ordered]@{
            api_addresses = @('172.30.0.2')
            worker_addresses = @('172.30.0.3')
            safety_control_addresses = @('172.31.0.2')
        }) `
        -HeartbeatAction $heartbeatAction
    Assert-Contract (
        $coherentHeartbeat.attempts -eq 1 -and
        ($script:heartbeatEvents -join ',') -eq (
            'sql:3,heartbeat:3,http:healthz:15,heartbeat:15,' +
            'http:readyz:27,heartbeat:27,sql:30,heartbeat:30'
        )
    ) (
        'Coherent database/HTTP snapshot exceeded the 15-second lease gap ' +
        'or changed checkpoint order.'
    )

    $script:heartbeatLogicalSeconds = 0
    $script:heartbeatEvents.Clear()
    $heartbeatFailureClosed = $false
    try {
        [void](
            Get-HttpSnapshot -Client $heartbeatClient -HeartbeatAction {
                throw 'simulated lease heartbeat failure'
            }
        )
    }
    catch {
        $heartbeatFailureClosed = (
            $_.Exception.GetBaseException().Message -eq
                'simulated lease heartbeat failure'
        )
    }
    Assert-Contract (
        $heartbeatFailureClosed -and
        @($script:heartbeatEvents | Where-Object { $_ -like 'http:*' }).Count -eq 1
    ) 'Snapshot collection continued after its lease heartbeat failed.'
}
finally {
    $heartbeatClient.Dispose()
}

[pscustomobject]@{
    script = $scriptPath
    syntax_valid = $true
    validation_has_no_io = $true
    bounded_timing = $true
    bounded_native_io = $true
    immutable_artifacts = $true
    exact_resume_anchor = $true
    database_clock_required = $true
    direct_sql_window = $true
    full_sql_database_boundaries = $true
    exact_database_session_identity = $true
    zero_live_inventory = $true
    polymarket_only_stop = $true
    independent_safety_endpoint = $true
    exact_stop_poll = $true
    ambiguous_stop_recovery = $true
    executable_guard_tests = $true
    executable_stop_tests = $true
    executable_fail_closed_barrier = $true
    optional_container_fields = $true
    rolling_resource_thresholds = $true
    executable_heartbeat_order = $true
    stop_before_diagnostics = $true
    docker_ast_allowlist = $true
    no_docker_mutation = $true
    validated = $true
} | ConvertTo-Json -Compress

$global:LASTEXITCODE = 0
