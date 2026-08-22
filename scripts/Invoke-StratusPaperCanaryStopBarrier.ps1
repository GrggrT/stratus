[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $Root,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{32}$')]
    [string] $RunId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9._:-]{8,64}$')]
    [string] $ResumeRequestId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string] $PostgresContainerId,

    [Parameter(Mandatory = $true)]
    [Security.SecureString] $SafetyApiKey,

    [Uri] $SafetyBaseUri = 'http://127.0.0.1:8001/'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$BarrierTimeoutSeconds = 20
$HttpTimeoutSeconds = 3
$NativeTimeoutSeconds = 3
$MaximumPollAttempts = 20
$LatestSafePollStartSeconds = (
    $BarrierTimeoutSeconds - $NativeTimeoutSeconds - $HttpTimeoutSeconds - 1
)
$StopRequestId = "paper-canary-stop-$RunId"
$LeaseId = "paper-lease-$RunId"
$StopActor = 'codex-paper-canary'
$StopReason = 'STRATUS PAPER canary terminal fail-closed stop.'
$StopBody = [ordered]@{
    request_id = $StopRequestId
    command = 'stop'
    reason = $StopReason
    actor = $StopActor
    payload = [ordered]@{ venue = 'polymarket' }
}

function Get-OptionalProperty {
    param(
        [AllowNull()][object] $InputObject,
        [Parameter(Mandatory = $true)][string] $Name
    )

    if ($null -eq $InputObject) {
        return $null
    }
    $property = $InputObject.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }
    return $property.Value
}

function Assert-LoopbackSafetyUri {
    param([Parameter(Mandatory = $true)][Uri] $Uri)

    $address = $null
    if (
        -not $Uri.IsAbsoluteUri -or
        $Uri.Scheme -cne 'http' -or
        -not [Net.IPAddress]::TryParse($Uri.Host, [ref]$address) -or
        -not [Net.IPAddress]::IsLoopback($address) -or
        -not [string]::IsNullOrEmpty($Uri.UserInfo) -or
        -not [string]::IsNullOrEmpty($Uri.Query) -or
        -not [string]::IsNullOrEmpty($Uri.Fragment) -or
        $Uri.AbsolutePath -cne '/'
    ) {
        throw 'SafetyBaseUri must be an absolute HTTP loopback origin.'
    }
}

function ConvertFrom-SecureStringValue {
    param([Parameter(Mandatory = $true)][Security.SecureString] $Secret)

    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function Assert-ExactPayload {
    param(
        [Parameter(Mandatory = $true)][object] $Payload,
        [Parameter(Mandatory = $true)][string] $Label
    )

    $properties = @($Payload.PSObject.Properties.Name)
    if (
        $properties.Count -ne 1 -or
        $properties[0] -cne 'venue' -or
        [string]$Payload.venue -cne 'polymarket'
    ) {
        throw "$Label is not the exact Polymarket-only payload."
    }
}

function Assert-ExactStopItem {
    param(
        [Parameter(Mandatory = $true)][object] $Item,
        [Parameter(Mandatory = $true)][string] $Label
    )

    if (
        [string](Get-OptionalProperty $Item 'request_id') -cne $StopRequestId -or
        [string](Get-OptionalProperty $Item 'command') -cne 'stop' -or
        [string](Get-OptionalProperty $Item 'actor') -cne $StopActor -or
        [string](Get-OptionalProperty $Item 'reason') -cne $StopReason
    ) {
        throw "$Label collided with the reserved emergency-stop identity."
    }
    $payload = Get-OptionalProperty $Item 'payload'
    if ($null -eq $payload) {
        throw "$Label omitted the emergency-stop payload."
    }
    Assert-ExactPayload -Payload $payload -Label $Label
}

function Assert-AcceptedStopResponse {
    param([Parameter(Mandatory = $true)][object] $Response)

    $item = Get-OptionalProperty $Response 'item'
    if ($null -eq $item) {
        throw 'Safety-control response omitted the accepted stop command.'
    }
    Assert-ExactStopItem -Item $item -Label 'Safety-control response'
    $status = [string](Get-OptionalProperty $item 'status')
    if ($status -in @('failed', 'rejected')) {
        throw "Safety-control returned terminal stop status $status."
    }
    if ($status -notin @('pending', 'succeeded')) {
        throw "Safety-control returned unexpected stop status $status."
    }

    $createdProperty = $Response.PSObject.Properties['created']
    if ($null -eq $createdProperty) {
        throw 'Safety-control response omitted created.'
    }
    $created = [bool]$createdProperty.Value
    $idempotent = [bool](Get-OptionalProperty $Response 'idempotent')
    if ($created -eq $idempotent) {
        throw 'Stop response did not prove exactly one create/replay outcome.'
    }
    return [pscustomobject]@{
        created = $created
        idempotent = $idempotent
    }
}

function Invoke-AuthenticatedStopPost {
    param([Parameter(Mandatory = $true)][Uri] $Uri)

    $plainKey = ConvertFrom-SecureStringValue -Secret $SafetyApiKey
    $headers = @{}
    try {
        if ([string]::IsNullOrWhiteSpace($plainKey)) {
            throw 'SafetyApiKey must not be empty.'
        }
        $headers['X-API-Key'] = $plainKey
        $jsonBody = $StopBody | ConvertTo-Json -Depth 4 -Compress
        return Invoke-RestMethod -Uri $Uri -Method Post -Headers $headers `
            -ContentType 'application/json' -Body $jsonBody `
            -TimeoutSec $HttpTimeoutSeconds -MaximumRedirection 0
    }
    finally {
        $headers.Clear()
        $plainKey = $null
    }
}

function ConvertTo-NativeArgument {
    param([AllowEmptyString()][string] $Value)

    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') {
        return $Value
    }
    $builder = [Text.StringBuilder]::new()
    [void]$builder.Append('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') {
            $slashes += 1
            continue
        }
        if ($character -eq '"') {
            [void]$builder.Append(('\' * (($slashes * 2) + 1)))
            [void]$builder.Append('"')
            $slashes = 0
            continue
        }
        if ($slashes -gt 0) {
            [void]$builder.Append(('\' * $slashes))
            $slashes = 0
        }
        [void]$builder.Append($character)
    }
    if ($slashes -gt 0) {
        [void]$builder.Append(('\' * ($slashes * 2)))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Invoke-BoundedDocker {
    param(
        [Parameter(Mandatory = $true)][string[]] $Arguments,
        [Parameter(Mandatory = $true)][string] $StandardInput,
        [Parameter(Mandatory = $true)][string] $WorkingDirectory
    )

    $dockerCommand = Get-Command -Name 'docker' -CommandType Application `
        -ErrorAction Stop | Select-Object -First 1
    if (
        $null -eq $dockerCommand -or
        [string]::IsNullOrWhiteSpace([string]$dockerCommand.Source)
    ) {
        throw 'Could not resolve the Docker application executable.'
    }

    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = [string]$dockerCommand.Source
    $start.WorkingDirectory = $WorkingDirectory
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardInput = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $argumentList = $start.PSObject.Properties['ArgumentList']
    if ($null -ne $argumentList) {
        foreach ($argument in $Arguments) {
            [void]$start.ArgumentList.Add($argument)
        }
    }
    else {
        $start.Arguments = (@(
            $Arguments | ForEach-Object {
                ConvertTo-NativeArgument -Value $_
            }
        ) -join ' ')
    }

    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    $started = $false
    try {
        if (-not $process.Start()) {
            throw 'Could not start the bounded docker exec process.'
        }
        $started = $true
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $stdinTask = $process.StandardInput.WriteAsync($StandardInput)
        $stdinClosed = $false
        $nativeWatch = [Diagnostics.Stopwatch]::StartNew()
        try {
            while (-not $process.HasExited) {
                if (-not $stdinClosed -and $stdinTask.IsCompleted) {
                    [void]$stdinTask.GetAwaiter().GetResult()
                    $process.StandardInput.Close()
                    $stdinClosed = $true
                }
                if ($nativeWatch.Elapsed.TotalSeconds -ge $NativeTimeoutSeconds) {
                    try {
                        $process.Kill()
                    }
                    catch {
                        # The SQL statement timeout remains authoritative.
                    }
                    throw 'Read-only PostgreSQL docker exec exceeded 3 seconds.'
                }
                [Threading.Thread]::Sleep(10)
            }
        }
        finally {
            $nativeWatch.Stop()
            if (-not $stdinClosed) {
                try {
                    $process.StandardInput.Close()
                }
                catch {
                    # Preserve the process result or bounded timeout.
                }
            }
        }
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        if ($process.ExitCode -ne 0) {
            $boundedError = if ($stderr.Length -gt 1000) {
                $stderr.Substring(0, 1000)
            }
            else {
                $stderr
            }
            throw "Read-only PostgreSQL docker exec failed: $boundedError"
        }
        if ([Text.Encoding]::UTF8.GetByteCount($stdout) -gt 262144) {
            throw 'Read-only PostgreSQL response exceeded 256 KiB.'
        }
        return $stdout
    }
    finally {
        if ($started -and -not $process.HasExited) {
            try {
                $process.Kill()
            }
            catch {
                # Preserve the primary bounded-process failure.
            }
        }
        $process.Dispose()
    }
}

function Get-StopState {
    param([Parameter(Mandatory = $true)][string] $WorkingDirectory)

    $sql = @"
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '3s';
SET LOCAL lock_timeout = '3s';
SELECT jsonb_build_object(
  'database_now', clock_timestamp(),
  'command', (
    SELECT jsonb_build_object(
      'id', id, 'request_id', request_id, 'command', command,
      'payload', payload, 'actor', actor, 'reason', reason,
      'status', status, 'created_at', created_at, 'executed_at', executed_at
    )
    FROM operator_commands
    WHERE request_id='$StopRequestId'
  ),
  'matching_leases', COALESCE((
    SELECT jsonb_agg(jsonb_build_object(
      'id', id, 'lease_id', lease_id, 'run_id', run_id,
      'resume_request_id', resume_request_id,
      'direct_stop_request_id', direct_stop_request_id,
      'state', state, 'stop_command_id', stop_command_id
    ) ORDER BY id)
    FROM paper_canary_safety_leases
    WHERE lease_id='$LeaseId'
       OR run_id='$RunId'
       OR resume_request_id='$ResumeRequestId'
       OR direct_stop_request_id='$StopRequestId'
  ), '[]'::jsonb),
  'active_canary_leases', (
    SELECT count(*) FROM paper_canary_safety_leases WHERE state='active'
  ),
  'resume_status', (
    SELECT status::text FROM operator_commands
    WHERE request_id='$ResumeRequestId'
  ),
  'global_control', (
    SELECT jsonb_build_object(
      'mode', mode::text, 'kill_switch', kill_switch,
      'live_confirmation_until', live_confirmation_until
    ) FROM runtime_control WHERE id=1
  ),
  'polymarket', (
    SELECT jsonb_build_object(
      'enabled', enabled, 'mode', mode::text, 'kill_switch', kill_switch,
      'live_confirmation_until', live_confirmation_until
    ) FROM venue_runtime_control WHERE venue='polymarket'::trading_venue
  ),
  'binance', (
    SELECT jsonb_build_object(
      'enabled', enabled, 'mode', mode::text, 'kill_switch', kill_switch,
      'live_confirmation_until', live_confirmation_until
    ) FROM venue_runtime_control WHERE venue='binance_spot'::trading_venue
  ),
  'pending_commands', (
    SELECT count(*) FROM operator_commands WHERE status='pending'
  ),
  'pending_signals', (
    SELECT count(*) FROM signals WHERE status='pending'::signal_status
  ),
  'live', jsonb_build_object(
    'orders', (SELECT count(*) FROM orders WHERE mode='live'::execution_mode),
    'positions', (SELECT count(*) FROM positions WHERE mode='live'::execution_mode),
    'intents', (SELECT count(*) FROM execution_order_intents WHERE mode='live'::execution_mode),
    'fill_legs', (SELECT count(*) FROM execution_fill_legs WHERE mode='live'::execution_mode),
    'enrollments', (SELECT count(*) FROM prospective_validation_enrollments WHERE mode='live'::execution_mode),
    'outcomes', (
      SELECT count(*)
      FROM prospective_validation_outcomes outcome
      JOIN prospective_validation_enrollments enrollment
        ON enrollment.id=outcome.enrollment_id
      WHERE enrollment.mode='live'::execution_mode
    )
  )
)::text;
COMMIT;
"@
    $arguments = @(
        'exec', '-i', $PostgresContainerId, 'sh', '-lc',
        'PGPASSWORD="$POSTGRES_PASSWORD" PGAPPNAME="stratus-paper-canary-stop-barrier" exec psql -XAtq -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
    )
    $stdout = Invoke-BoundedDocker -Arguments $arguments `
        -StandardInput $sql -WorkingDirectory $WorkingDirectory
    $text = $stdout.Trim()
    if ([string]::IsNullOrWhiteSpace($text)) {
        throw 'Read-only PostgreSQL stop verification returned no JSON.'
    }
    try {
        return $text | ConvertFrom-Json
    }
    catch {
        throw 'Read-only PostgreSQL stop verification returned invalid JSON.'
    }
}

function Test-StopConvergence {
    param([Parameter(Mandatory = $true)][object] $State)

    foreach ($requiredProperty in @(
        'database_now',
        'command',
        'matching_leases',
        'active_canary_leases',
        'resume_status',
        'global_control',
        'polymarket',
        'binance',
        'pending_commands',
        'pending_signals',
        'live'
    )) {
        if ($null -eq $State.PSObject.Properties[$requiredProperty]) {
            throw "Post-stop database state omitted $requiredProperty."
        }
    }
    if ([string]::IsNullOrWhiteSpace([string]$State.database_now)) {
        throw 'Post-stop database state omitted its database timestamp.'
    }
    $command = Get-OptionalProperty $State 'command'
    if ($null -eq $command) {
        return $false
    }
    Assert-ExactStopItem -Item $command -Label 'Persisted stop command'
    $status = [string](Get-OptionalProperty $command 'status')
    if ($status -in @('failed', 'rejected')) {
        throw "Persisted stop command ended as $status."
    }
    if ($status -ne 'succeeded') {
        return $false
    }
    $createdAtValue = Get-OptionalProperty $command 'created_at'
    $executedAtValue = Get-OptionalProperty $command 'executed_at'
    if ($null -eq $createdAtValue -or $null -eq $executedAtValue) {
        throw 'Persisted succeeded stop command has incomplete timestamps.'
    }
    $createdAt = [DateTimeOffset]$createdAtValue
    $executedAt = [DateTimeOffset]$executedAtValue
    $databaseNow = [DateTimeOffset]$State.database_now
    $clockTolerance = [TimeSpan]::FromSeconds(5)
    if (
        $executedAt -lt $createdAt.Subtract($clockTolerance) -or
        $executedAt -gt $databaseNow.Add($clockTolerance)
    ) {
        throw 'Persisted stop timestamps violate database-clock ordering.'
    }

    $matchingLeaseValue = Get-OptionalProperty $State 'matching_leases'
    $matchingLeases = [object[]]@()
    if ($null -ne $matchingLeaseValue) {
        $matchingLeases = @($matchingLeaseValue)
    }
    if ($matchingLeases.Count -gt 1) {
        throw 'Multiple canary leases collide with the stop barrier identity.'
    }
    $leaseState = $null
    if ($matchingLeases.Count -eq 1) {
        $lease = $matchingLeases[0]
        if (
            [string](Get-OptionalProperty $lease 'lease_id') -cne $LeaseId -or
            [string](Get-OptionalProperty $lease 'run_id') -cne $RunId -or
            [string](Get-OptionalProperty $lease 'resume_request_id') -cne $ResumeRequestId -or
            [string](Get-OptionalProperty $lease 'direct_stop_request_id') -cne $StopRequestId
        ) {
            throw (
                'Matching canary lease collides with the barrier identity: ' +
                "lease_id=$([string](Get-OptionalProperty $lease 'lease_id')); " +
                "run_id=$([string](Get-OptionalProperty $lease 'run_id')); " +
                'resume_request_id=' +
                "$([string](Get-OptionalProperty $lease 'resume_request_id')); " +
                'direct_stop_request_id=' +
                "$([string](Get-OptionalProperty $lease 'direct_stop_request_id'))"
            )
        }
        $leaseState = [string](Get-OptionalProperty $lease 'state')
        if ($leaseState -eq 'active') {
            return $false
        }
        if ($leaseState -notin @(
            'released', 'expired_stopped', 'expired_safe', 'revoked_stopped'
        )) {
            throw "Matching canary lease has unexpected state $leaseState."
        }
    }

    if ([int](Get-OptionalProperty $State 'active_canary_leases') -ne 0) {
        return $false
    }
    $resumeStatus = [string](Get-OptionalProperty $State 'resume_status')
    if ($resumeStatus -eq 'pending') {
        return $false
    }
    if (
        -not [string]::IsNullOrWhiteSpace($resumeStatus) -and
        $resumeStatus -notin @('succeeded', 'rejected', 'failed')
    ) {
        throw "Expected resume command has unexpected status $resumeStatus."
    }

    $globalControl = Get-OptionalProperty $State 'global_control'
    $polymarket = Get-OptionalProperty $State 'polymarket'
    $binance = Get-OptionalProperty $State 'binance'
    if ($null -eq $globalControl -or $null -eq $polymarket -or $null -eq $binance) {
        throw 'Post-stop runtime control inventory is incomplete.'
    }
    if (
        [string]$globalControl.mode -cne 'paper' -or
        -not [bool]$globalControl.kill_switch -or
        $null -ne $globalControl.live_confirmation_until -or
        -not [bool]$polymarket.enabled -or
        [string]$polymarket.mode -cne 'paper' -or
        -not [bool]$polymarket.kill_switch -or
        $null -ne $polymarket.live_confirmation_until -or
        -not [bool]$binance.enabled -or
        [string]$binance.mode -cne 'backtest' -or
        -not [bool]$binance.kill_switch -or
        $null -ne $binance.live_confirmation_until
    ) {
        return $false
    }
    if (
        [int](Get-OptionalProperty $State 'pending_commands') -ne 0 -or
        [int](Get-OptionalProperty $State 'pending_signals') -ne 0
    ) {
        return $false
    }

    $live = Get-OptionalProperty $State 'live'
    if ($null -eq $live) {
        throw 'Post-stop LIVE inventory is missing.'
    }
    foreach ($property in @(
        'orders', 'positions', 'intents', 'fill_legs', 'enrollments', 'outcomes'
    )) {
        if ($null -eq $live.PSObject.Properties[$property]) {
            throw "Post-stop LIVE inventory omitted $property."
        }
        if ([int]$live.$property -ne 0) {
            return $false
        }
    }
    return $true
}

Assert-LoopbackSafetyUri -Uri $SafetyBaseUri
$stopUri = [Uri]::new($SafetyBaseUri, 'api/v1/emergency-stop')
$watch = [Diagnostics.Stopwatch]::StartNew()
$postAttempts = 1
$acceptedResponses = 0
$created = $null
$lastPostError = $null
$firstResponse = $null

# SAFETY ORDERING BARRIER: this authenticated, exact-ID POST must remain the
# first external operation. No filesystem, Docker, SQL, or artifact diagnostic
# may move above it.
try {
    $firstResponse = Invoke-AuthenticatedStopPost -Uri $stopUri
    $acceptance = Assert-AcceptedStopResponse -Response $firstResponse
    $acceptedResponses += 1
    $created = [bool]$acceptance.created
}
catch {
    $lastPostError = $_.Exception.GetBaseException().Message
    if ($null -ne $firstResponse) {
        throw
    }
}

# Root inspection is deliberately below the mandatory STOP POST.
$absoluteRoot = [IO.Path]::GetFullPath($Root)
if (-not [IO.Directory]::Exists($absoluteRoot)) {
    throw "Root does not exist: $absoluteRoot"
}

$lastState = $null
$lastSqlError = $null
$pollAttempts = 0
try {
    while (
        $watch.Elapsed.TotalSeconds -lt $LatestSafePollStartSeconds -and
        $pollAttempts -lt $MaximumPollAttempts
    ) {
        $pollAttempts += 1
        $state = $null
        try {
            $state = Get-StopState -WorkingDirectory $absoluteRoot
            $lastSqlError = $null
        }
        catch {
            $lastSqlError = $_.Exception.GetBaseException().Message
        }
        if ($null -ne $state) {
            $lastState = $state
            if (Test-StopConvergence -State $state) {
                $matchingLeases = [object[]]@()
                if ($null -ne $state.matching_leases) {
                    $matchingLeases = @($state.matching_leases)
                }
                $leaseState = if ($matchingLeases.Count -eq 0) {
                    $null
                }
                else {
                    [string]$matchingLeases[0].state
                }
                [ordered]@{
                    schema_version = 1
                    verified = $true
                    run_id = $RunId
                    request_id = $StopRequestId
                    stop_request_id = $StopRequestId
                    command = 'stop'
                    actor = $StopActor
                    reason = $StopReason
                    payload = [ordered]@{ venue = 'polymarket' }
                    resume_request_id = $ResumeRequestId
                    postgres_container_id = $PostgresContainerId
                    post_attempts = $postAttempts
                    accepted_responses = $acceptedResponses
                    exact_id_replays = $postAttempts - 1
                    initially_created = $created
                    database_now = [string]$state.database_now
                    command_status = 'succeeded'
                    matching_lease_terminal = $true
                    matching_lease_state = $leaseState
                    active_canary_leases = 0
                    pending_commands = 0
                    pending_signals = 0
                    live = [ordered]@{
                        orders = 0
                        positions = 0
                        intents = 0
                        fill_legs = 0
                        enrollments = 0
                        outcomes = 0
                    }
                    controls = [ordered]@{
                        global = [ordered]@{
                            mode = 'paper'
                            kill_switch = $true
                        }
                        polymarket = [ordered]@{
                            enabled = $true
                            mode = 'paper'
                            kill_switch = $true
                        }
                        binance_spot = [ordered]@{
                            enabled = $true
                            mode = 'backtest'
                            kill_switch = $true
                        }
                    }
                } | ConvertTo-Json -Depth 8 -Compress
                return
            }
        }

        if ($watch.Elapsed.TotalSeconds -ge $BarrierTimeoutSeconds) {
            break
        }
        $postAttempts += 1
        $replayResponse = $null
        try {
            $replayResponse = Invoke-AuthenticatedStopPost -Uri $stopUri
            [void](Assert-AcceptedStopResponse -Response $replayResponse)
            $acceptedResponses += 1
            $lastPostError = $null
        }
        catch {
            $lastPostError = $_.Exception.GetBaseException().Message
            if ($null -ne $replayResponse) {
                throw
            }
        }
        Start-Sleep -Milliseconds 250
    }
}
finally {
    $watch.Stop()
}

$lastStatus = if ($null -eq $lastState -or $null -eq $lastState.command) {
    'missing'
}
else {
    [string]$lastState.command.status
}
throw (
    'PAPER canary STOP barrier did not converge within its bounded window; ' +
    "command_status=$lastStatus; post_attempts=$postAttempts; " +
    "last_post_error=$lastPostError; last_sql_error=$lastSqlError"
)
