[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string] $ContractPath,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{64}$')][string] $ContractSha256,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{64}$')][string] $RuntimeSha256,
    [Parameter(Mandatory = $true)][ValidateScript({ [regex]::IsMatch($_, '\A[0-9a-f]{64}\z') })][string] $BootstrapNonce,
    [string] $ControlInputHandle,
    [string] $ControlOutputHandle
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

throw (
    'STRATUS parent-stage watchdog is WIP and non-authorizing. ' +
    'The unconditional entry blocker must remain until the complete shared-fence, ' +
    'registration, containment and successor-supervisor handoff protocol passes independent review.'
)

# This protocol is not release authority. Section 3 remains unconditionally blocked
# until its held-fence gate hook and parent orchestration are independently accepted.
$approvedRoot = 'C:\Users\Administrator\Documents\weatherpoly'
$approvedDockerHash = '388f231898cff8674a2aa341e306b77fd1f93a5954dd33ff556575a69227824d'
$approvedComposePath = 'C:\Program Files\Docker\Docker\resources\cli-plugins\docker-compose.exe'
$approvedComposeHash = '8482829f77d14da8dfdfd0b553847946cee5161c90312219cf4f47d1f132d2bf'
$approvedEngineId = 'd98e03f5-06d7-4af4-9f21-b6f7a70ebf68'
$approvedPostgresId = '6ce82603e44abb95be94dbe95830af2d00c0c36ca3fe8a122b798087ecc1b299'
$approvedPostgresImage = 'sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'
$approvedPostgresConfigImage = 'postgres:16-alpine@sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'
$artifactNames = @(
    'mutation_fence', 'lease', 'parent_armed', 'ready', 'heartbeat', 'terminal',
    'containment', 'transition_registration', 'transition_ack',
    'provision_intent', 'provision_intent_ack', 'provision_attempt',
    'provision_registration', 'provision_registration_ack', 'provision_complete',
    'api_intent', 'api_intent_ack', 'api_attempt', 'api_registration',
    'api_registration_ack', 'api_start_intent', 'api_start_ack', 'api_start_attempt',
    'api_start_complete', 'observer_registration', 'observer_ack',
    'observer_intent', 'observer_complete', 'observer_ready', 'observer_output', 'load_accepted',
    'handoff_request', 'handoff_ack', 'handoff_commit',
    'transition_start_fence', 'transition_start_revoke', 'transition_start_attempt',
    'transition_start_completion', 'transition_terminal', 'parent_authorization',
    'transition_launch_intent', 'transition_launch_ack', 'transition_capture_complete', 'transition_live'
)
$locks = [Collections.Generic.List[IDisposable]]::new()
$failures = [Collections.Generic.List[string]]::new()
$observed = @{}
$objectIds = @{ provision = $null; api = $null }
$objectStarts = @{ provision = $null; api = $null; postgres = $null }
$script:leaseHash = $null
$script:armed = $false
$script:fenceLock = $null
$script:transitionFenceLock = $null
$script:transitionFenceCapture = $null
$script:observer = $null
$script:ownerLease = $null
$script:observerChild = $null
$script:observerLaunchAttempted = $false
$script:observerChildResumed = $false
$script:observerResult = $null
$script:observerResultObservedAt = $null
$script:observerPlan = $null
$script:observerFrame = $null
$script:observerEvidence = @{}
$script:observerCompleted = $null
$script:heartbeatSequence = 0L
$script:heartbeatPublished = [DateTimeOffset]::MinValue
$script:heartbeatSeenClock = [Diagnostics.Stopwatch]::StartNew()
$script:runtimeLock = $null
$script:contractLock = $null
$script:watchdogScriptLock = $null
$script:hostLock = $null
$script:bootstrapWriteAttempted = $false
$script:bootstrapOutput = $null
$script:dockerLock = $null
$script:composeLock = $null
$script:hbaLock = $null
$script:contract = $null
$script:paths = $null
$script:watchClock = [Diagnostics.Stopwatch]::StartNew()
$script:exitCode = 3
$script:containmentProgress = $null
$script:recoveryDocuments = @{}
$script:validatedCompletions = @{}
$script:predecessorApi = $null
$script:intendedServices = @{}
$script:objectStaticHashes = @{}
$script:transitionEvidence = @{}
$script:transitionEvidenceMode = 'receiving'
$script:transitionClosure = $null
$script:transitionLaunchAttempted = $false
$script:transitionLaunchResumed = $false
$script:transitionLaunchAbortProven = $false
$script:transitionLaunch = $null
$script:transitionLaunchPlan = $null
$script:transitionLaunchReady = $null
$script:transitionLaunchTerminal = $null
$script:transitionLaunchEvidence = @{}
$script:controlEndpoint = $null
$script:transitionControlAttempted = $false
$script:controlDrainEligible = $false
$script:controlDrainAttempted = $false
$script:controlDrainCompleted = $false

function Add-ParentFailure {
    param([string] $Message)
    # A daemon ambiguity can remain unresolved indefinitely. Keep diagnostic memory
    # bounded while retaining every distinct failure category in the terminal data.
    if (-not $failures.Contains($Message) -and $failures.Count -lt 128) { $failures.Add($Message) }
}

function Assert-ParentRegularAncestry {
    param([string] $Path, [switch] $Absent)
    $absolute = [IO.Path]::GetFullPath($Path)
    if ($absolute -cne $Path) { throw 'Every protocol path must be canonical and absolute.' }
    $cursor = $absolute
    $first = $true
    while (-not [string]::IsNullOrEmpty($cursor)) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Reparse ancestry is forbidden: $cursor"
            }
            if ($first -and $Absent) { throw "Attempt artifact already exists: $cursor" }
            if (-not $first -and -not $item.PSIsContainer) { throw 'Non-directory ancestry.' }
        }
        elseif (-not ($first -and $Absent)) { throw "Missing ancestry: $cursor" }
        $first = $false
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
    return $absolute
}

function Assert-ParentProperties {
    param([object] $Value, [string[]] $Expected, [string] $Label)
    if ($null -eq $Value -or $Value.GetType().FullName -cne 'System.Management.Automation.PSCustomObject') {
        throw "$Label must be a native JSON object."
    }
    $actual = [string[]]@($Value.PSObject.Properties.Name)
    $wanted = [string[]]@($Expected)
    [Array]::Sort($actual, [StringComparer]::Ordinal)
    [Array]::Sort($wanted, [StringComparer]::Ordinal)
    if (($actual -join "`n") -cne ($wanted -join "`n")) { throw "$Label property set differs." }
}

function Get-ParentUtc {
    param([object] $Value)
    if ($Value -isnot [string]) { throw 'Canonical UTC time must be a string.' }
    $parsed = [DateTimeOffset]::ParseExact($Value, 'o', [Globalization.CultureInfo]::InvariantCulture)
    if ($parsed.Offset -ne [TimeSpan]::Zero -or $parsed.ToString('o') -cne $Value) {
        throw 'Canonical UTC time required.'
    }
    return $parsed
}

function ConvertFrom-ParentJson {
    param([byte[]] $Bytes)
    [Stratus.ParentStage.StrictJson]::Validate($Bytes)
    $text = [Stratus.ParentStage.Files]::Utf8.GetString($Bytes)
    $parameters = @{ InputObject = $text }
    $command = Get-Command 'Microsoft.PowerShell.Utility\ConvertFrom-Json' -CommandType Cmdlet
    if ($command.Parameters.ContainsKey('DateKind')) { $parameters.DateKind = 'String' }
    return Microsoft.PowerShell.Utility\ConvertFrom-Json @parameters
}

function Read-ParentDocument {
    param([string] $Path, [int] $MaximumBytes = 262144, [switch] $AtomicReplacement)
    [void](Assert-ParentRegularAncestry -Path $Path)
    $bytes = [Stratus.ParentStage.Files]::ReadBounded($Path, $MaximumBytes, [bool]$AtomicReplacement)
    return [pscustomobject]@{
        value = ConvertFrom-ParentJson -Bytes $bytes
        sha256 = [Stratus.ParentStage.Files]::Hash($bytes)
        path = $Path
    }
}

function Write-ParentDocument {
    param([string] $Path, [object] $Value)
    [void](Assert-ParentRegularAncestry -Path $Path -Absent)
    $bytes = [Stratus.ParentStage.Files]::Utf8.GetBytes(($Value | ConvertTo-Json -Depth 35 -Compress))
    [Stratus.ParentStage.Files]::PublishNew($Path, $bytes)
    return [Stratus.ParentStage.Files]::Hash($bytes)
}

function Write-ParentRecoveryDocument {
    param([string] $Name, [string] $Path, [object] $Value)
    # Remember the exact bytes before publication. If a later receipt/read fails,
    # retry may reuse only this process's successfully published, identical document.
    # A preexisting document on the first attempt is never an owned publication.
    if (-not $script:recoveryDocuments.ContainsKey($Name)) {
        [void](Assert-ParentRegularAncestry -Path $Path -Absent)
        $bytes = [Stratus.ParentStage.Files]::Utf8.GetBytes(($Value | ConvertTo-Json -Depth 35 -Compress))
        $script:recoveryDocuments[$Name] = [pscustomobject]@{
            path = $Path; bytes = $bytes; sha256 = [Stratus.ParentStage.Files]::Hash($bytes); published = $false
        }
    }
    $planned = $script:recoveryDocuments[$Name]
    if ($planned.path -cne $Path) { throw 'Recovery receipt path changed.' }
    $exists = Test-Path -LiteralPath $Path
    if ($planned.published -and -not $exists) { throw 'Previously published recovery receipt disappeared.' }
    if (-not $planned.published -and $exists) { throw 'A foreign target occupies an unpublished recovery receipt.' }
    if (-not $planned.published) {
        [Stratus.ParentStage.Files]::PublishNew($Path, $planned.bytes)
        $planned.published = $true
    }
    $published = Read-ParentDocument $Path
    if ($published.sha256 -cne $planned.sha256) { throw 'Recovery receipt differs from its owned publication.' }
    return $planned.sha256
}

function Get-ParentNonAuthorization {
    return [ordered]@{
        postgres_start_permitted = $false; api_start_permitted = $false
        worker_start_permitted = $false; safety_control_start_permitted = $false
        live_transition_permitted = $false; binance_kill_switch_removal_permitted = $false
        migration_container_permitted = $false; container_remove_permitted = $false
    }
}

function Get-ParentProcessIdentity {
    param([int] $ProcessId)
    $process = [Diagnostics.Process]::GetProcessById($ProcessId)
    try {
        if ($process.HasExited) { throw 'Process has exited.' }
        return [pscustomobject]@{
            pid = $process.Id
            start_time_utc = [DateTimeOffset]::new($process.StartTime.ToUniversalTime()).ToString('o')
            executable = [IO.Path]::GetFullPath($process.MainModule.FileName)
        }
    }
    finally { $process.Dispose() }
}

function Assert-ParentOwner {
    if ($null -eq $script:ownerLease) { throw 'Exact owner process handle has not been acquired.' }
    $script:ownerLease.AssertAlive()
}

function Assert-ParentBootstrapHeldFile {
    param([IO.FileStream] $Held, [string] $Path, [string] $Sha256)
    if ($null -eq $Held -or -not $Held.CanRead -or $Held.CanWrite -or -not $Held.CanSeek -or
        $Held.SafeFileHandle.IsClosed -or $Held.SafeFileHandle.IsInvalid -or
        $Held.Name -cne $Path -or -not [regex]::IsMatch($Sha256, '\A[0-9a-f]{64}\z')) {
        throw 'Bootstrap source lacks its exact live held file.'
    }
    [void](Assert-ParentRegularAncestry $Path)
    $actual = [Stratus.ParentStage.Files]::HashStream($Held)
    if ($actual -cne $Sha256) { throw 'Bootstrap held source hash differs.' }
    return $actual
}

function Read-ParentBootstrapPublication {
    param([string] $Path, [string] $Sha256)
    if (-not [regex]::IsMatch($Sha256, '\A[0-9a-f]{64}\z')) { throw 'Bootstrap publication hash is invalid.' }
    [void](Assert-ParentRegularAncestry $Path)
    $held = [IO.FileStream]::new($Path, 'Open', 'Read', 'Read')
    $locks.Add($held) # Same armed cleanup ownership, including failed readback.
    if ($held.Length -lt 1 -or $held.Length -gt 65536) { throw 'Bootstrap publication byte bound exceeded.' }
    $bytes = [byte[]]::new([int]$held.Length)
    $offset = 0
    while ($offset -lt $bytes.Length) {
        $count = $held.Read($bytes, $offset, $bytes.Length - $offset)
        if ($count -eq 0) { throw 'Bootstrap publication was truncated.' }
        $offset += $count
    }
    if ($held.ReadByte() -ne -1) { throw 'Bootstrap publication grew during readback.' }
    $held.Position = 0
    [Stratus.ParentStage.StrictJson]::Validate($bytes)
    $actual = [Stratus.ParentStage.Files]::Hash($bytes)
    if ($actual -cne $Sha256) { throw 'Bootstrap publication differs from its own published bytes.' }
    return $actual
}

function New-ParentBootstrapFrame {
    param([string] $Nonce)
    if (-not $script:armed -or -not [regex]::IsMatch($Nonce, '\A[0-9a-f]{64}\z') -or
        -not [regex]::IsMatch($contract.session_id, '\A[0-9a-f]{32}\z')) {
        throw 'Bootstrap requires an armed session and exact lowercase nonce.'
    }
    $contractHash = Assert-ParentBootstrapHeldFile $script:contractLock $ContractPath $ContractSha256
    $sourceHash = Assert-ParentBootstrapHeldFile $script:watchdogScriptLock $contract.watchdog.script_path $contract.watchdog.script_sha256
    $runtimeHash = Assert-ParentBootstrapHeldFile $script:runtimeLock $contract.watchdog.runtime_path $RuntimeSha256
    $hostHash = Assert-ParentBootstrapHeldFile $script:hostLock $contract.watchdog.host_path $contract.watchdog.host_sha256
    $leaseHash = Read-ParentBootstrapPublication $paths.lease $script:leaseHash
    $readyHash = Read-ParentBootstrapPublication $paths.ready $observed.ready.sha256
    $identity = Get-ParentProcessIdentity $PID
    if ($identity.executable -cne $contract.watchdog.host_path -or
        $identity.pid -ne $watchdogIdentity.pid -or
        $identity.start_time_utc -cne $watchdogIdentity.start_time_utc -or
        $identity.executable -cne $watchdogIdentity.executable) { throw 'Bootstrap current watchdog identity differs.' }
    # This exact order/string representation is the native PollReady wire contract.
    $frame = [ordered]@{
        format = 'stratus-independent-watchdog-ready-v1'; session_id = $contract.session_id
        nonce = $Nonce; contract_sha256 = $contractHash; source_sha256 = $sourceHash
        runtime_sha256 = $runtimeHash; host_sha256 = $hostHash
        pid = $identity.pid.ToString([Globalization.CultureInfo]::InvariantCulture)
        start_time_utc = $identity.start_time_utc; lease_sha256 = $leaseHash; ready_sha256 = $readyHash
    }
    $bytes = [Stratus.ParentStage.Files]::Utf8.GetBytes(($frame | ConvertTo-Json -Depth 3 -Compress) + "`n")
    return ,$bytes
}

function Write-ParentBootstrapReady {
    param([byte[]] $Frame)
    if ($script:bootstrapWriteAttempted) { throw 'Bootstrap stdout publication may be attempted only once.' }
    # Latch before opening/writing/flushing raw stdout; ambiguity is never retried.
    $script:bootstrapWriteAttempted = $true
    $script:bootstrapOutput = [Console]::OpenStandardOutput()
    $script:bootstrapOutput.Write($Frame, 0, $Frame.Length)
    $script:bootstrapOutput.Flush()
    # Do not close stdout. A broken pipe propagates into the armed containment catch.
}

function Stop-ParentExactProcess {
    param([Stratus.ParentStage.ProcessLease] $Lease)
    if ($null -eq $Lease) { throw 'No held exact process lease is available for containment.' }
    $Lease.TerminateAndWait(5000)
}

function Get-ParentComposeConfigArguments {
    return [string[]]@('compose', '--project-name', 'weatherpoly', '--project-directory', $contract.root,
        '--env-file', (Join-Path $contract.root '.env'), '--file', (Join-Path $contract.root 'docker-compose.yml'),
        '--profile', 'release', 'config', '--format', 'json')
}

function Invoke-ParentDocker {
    param([string[]] $Arguments, [int] $TimeoutMilliseconds = 15000, [switch] $AllowAbsent)
    foreach ($name in @([Environment]::GetEnvironmentVariables('Process').Keys)) {
        if ([string]$name -match '^(DOCKER_|COMPOSE_)') {
            throw "Docker environment override forbidden: $name"
        }
    }
    # No caller-controlled shell command or Docker lifecycle verb is accepted.
    $verb = if ($Arguments.Count -gt 0) { [string]$Arguments[0] } else { '' }
    if ($verb -cnotin @('context', 'info', 'inspect', 'ps', 'stop', 'compose', 'image')) {
        throw 'Parent watchdog permits only proof commands and exact containment stop.'
    }
    $argumentText = $Arguments -join "`n"
    $validRead = switch -CaseSensitive ($verb) {
        'context' { $argumentText -ceq (@('context', 'inspect', 'desktop-linux', '--format', '{{json .}}') -join "`n") }
        'info' { $argumentText -ceq (@('info', '--format', '{{json .}}') -join "`n") }
        'ps' { $argumentText -ceq (@('ps', '--quiet', '--no-trunc') -join "`n") }
        'compose' { $argumentText -ceq ((Get-ParentComposeConfigArguments) -join "`n") }
        'image' {
            $Arguments.Count -eq 5 -and $Arguments[1] -ceq 'inspect' -and
                $Arguments[2] -cin @($contract.api.image_id, $approvedPostgresImage) -and
                $Arguments[3] -ceq '--format' -and $Arguments[4] -ceq '{{json .}}'
        }
        'inspect' {
            $Arguments.Count -eq 6 -and
                (($Arguments[0..4] -join "`n") -ceq (@('inspect', '--type', 'container', '--format', '{{json .}}') -join "`n")) -and
                $Arguments[5] -cin @($approvedPostgresId, $objectIds.api, $objectIds.provision,
                    $contract.api.name, $contract.provision.name, $script:r1Proof.topology.production_services.api.container_id)
        }
        'stop' { $true }
        default { $false }
    }
    if (-not $validRead) { throw 'Docker proof argv is not one exact allowlisted command.' }
    if ($verb -ceq 'stop' -and ($Arguments.Count -ne 4 -or $Arguments[1] -cne '--timeout' -or
            $Arguments[2] -cne '15' -or $Arguments[3] -notmatch '^[0-9a-f]{64}$' -or
            $Arguments[3] -cnotin @($approvedPostgresId, $objectIds.api, $objectIds.provision,
                $script:r1Proof.topology.production_services.api.container_id))) {
        throw 'Containment stop must name one exact registered full ID.'
    }
    $child = $null
    $primary = $null
    $result = $null
    $missing = $false
    try {
        $child = [Stratus.ParentStage.NativeChild]::StartSuspended(
            $contract.docker.executable, $approvedDockerHash, $script:dockerLock,
            [string[]](@('--context', 'desktop-linux') + $Arguments), $contract.root, 1048576
        )
        $child.Resume()
        $result = $child.Wait($TimeoutMilliseconds)
        if ($result.ExitCode -ne 0) {
            # No object is accepted as absent from a arbitrary CLI failure.
            $missing = $AllowAbsent -and $result.ExitCode -eq 1 -and
                $result.Stdout -ceq '' -and $result.Stderr.Trim() -ceq
                    ('Error: No such object: ' + $Arguments[-1])
            if (-not $missing) { throw "Docker proof failed with exit $($result.ExitCode)." }
        }
        if (-not $missing -and -not [string]::IsNullOrWhiteSpace($result.Stderr)) { throw 'Docker proof emitted stderr.' }
    }
    catch { $primary = $_.Exception }
    finally {
        if ($null -ne $child) {
            try { $child.Dispose() }
            catch {
                if ($null -eq $primary) { $primary = $_.Exception }
                else { $primary = [AggregateException]::new([Exception[]]@($primary, $_.Exception)) }
            }
        }
    }
    if ($null -ne $primary) { throw $primary }
    if ($missing) { return $null }
    return $result.Stdout.Trim()
}

function Assert-ParentEngine {
    param([switch] $AsTransitionBoundary)
    $context = Invoke-ParentDocker @('context', 'inspect', 'desktop-linux', '--format', '{{json .}}')
    $value = ConvertFrom-ParentJson ([Stratus.ParentStage.Files]::Utf8.GetBytes($context))
    if ($value.Name -isnot [string] -or $value.Endpoints.docker.Host -isnot [string] -or
        $value.Endpoints.docker.SkipTLSVerify -isnot [bool] -or $value.Endpoints.docker.SkipTLSVerify -or
        $value.Name -cne 'desktop-linux' -or
        $value.Endpoints.docker.Host -cne 'npipe:////./pipe/dockerDesktopLinuxEngine') {
        throw 'Pinned Docker endpoint drift.'
    }
    $info = Invoke-ParentDocker @('info', '--format', '{{json .}}')
    $engine = ConvertFrom-ParentJson ([Stratus.ParentStage.Files]::Utf8.GetBytes($info))
    foreach ($field in @('ID', 'ServerVersion', 'Name', 'DockerRootDir', 'OSType')) {
        if ($engine.$field -isnot [string]) { throw 'Docker engine identity requires native strings.' }
    }
    if ($engine.ID -cne $approvedEngineId -or $engine.ServerVersion -cne '28.3.2' -or
        $engine.Name -cne 'docker-desktop' -or $engine.DockerRootDir -cne '/var/lib/docker' -or
        $engine.OSType -cne 'linux') { throw 'Pinned Docker engine drift.' }
    if ($AsTransitionBoundary) {
        if ($engine.ClientInfo.Plugins -isnot [Array]) { throw 'Compose plugin inventory must be an array.' }
        $plugins = @($engine.ClientInfo.Plugins | Where-Object { $_.Name -is [string] -and $_.Name -ceq 'compose' })
        if ($plugins.Count -ne 1 -or $plugins[0].Path -isnot [string] -or
            $plugins[0].Version -isnot [string] -or $plugins[0].Path -cne $approvedComposePath -or
            $plugins[0].Version -cne 'v2.38.2-desktop.1') { throw 'Exact approved Compose plugin differs.' }
        [void](Assert-ParentBootstrapHeldFile $script:dockerLock $contract.docker.executable $approvedDockerHash)
        [void](Assert-ParentBootstrapHeldFile $script:composeLock $approvedComposePath $approvedComposeHash)
        $boundary = [ordered]@{
            docker_executable_path = $contract.docker.executable; docker_executable_sha256 = $approvedDockerHash
            docker_executable_bytes = [long]$script:dockerLock.Length
            compose_executable_path = $approvedComposePath; compose_executable_sha256 = $approvedComposeHash
            compose_executable_bytes = [long]$script:composeLock.Length; compose_version = $plugins[0].Version
            context = $value.Name; endpoint = $value.Endpoints.docker.Host; skip_tls_verify = $false
            engine_id = $engine.ID; server_version = $engine.ServerVersion; engine_name = $engine.Name
            docker_root_dir = $engine.DockerRootDir
        }
        $tokens = @($boundary.Values | ForEach-Object {
            if ($_ -is [bool]) { $_.ToString().ToLowerInvariant() }
            else { [Convert]::ToString($_, [Globalization.CultureInfo]::InvariantCulture) }
        })
        $boundary.contract_sha256 = [Stratus.ParentStage.Files]::Hash(
            [Stratus.ParentStage.Files]::Utf8.GetBytes(($tokens -join "`0") + "`n"))
        return [pscustomobject]$boundary
    }
}

function Get-ParentOptionalProperty {
    param([object] $Value, [string] $Name, [object] $Default = $null)
    if ($null -eq $Value) { return $Default }
    $property = $Value.PSObject.Properties[$Name]
    if ($null -eq $property) { return $Default }
    return $property.Value
}

function Test-ParentInt32 {
    param([object] $Value)
    # ConvertFrom-Json emits Int32 on Windows PowerShell and Int64 on PowerShell 7.
    # Both are native JSON integers; coercible strings/floats/bools are not.
    return (($Value -is [int] -or $Value -is [long]) -and
        $Value -ge [int]::MinValue -and $Value -le [int]::MaxValue)
}

function Initialize-ParentIntendedServices {
    # R1 has bound source bytes independently. The pinned Compose executable
    # derives service policy; a caller's arbitrary static hash is not a policy.
    # Rendered environment values stay in memory and never enter an artifact.
    $script:composeLock = [Stratus.ParentStage.Files]::Lock($approvedComposePath, $approvedComposeHash)
    $locks.Add($script:composeLock)
    $envPath = Join-Path $contract.root '.env'
    [void](Assert-ParentRegularAncestry $envPath)
    $locks.Add([IO.FileStream]::new($envPath, 'Open', 'Read', 'Read'))
    $modelText = Invoke-ParentDocker (Get-ParentComposeConfigArguments)
    $model = ConvertFrom-ParentJson ([Stratus.ParentStage.Files]::Utf8.GetBytes($modelText))
    if ($model.name -cne 'weatherpoly') { throw 'Rendered intended project differs.' }
    foreach ($kind in @('api', 'provision')) {
        $service = $model.services.($contract.$kind.service)
        $imageText = Invoke-ParentDocker @('image', 'inspect', $contract.$kind.image_id, '--format', '{{json .}}')
        $image = ConvertFrom-ParentJson ([Stratus.ParentStage.Files]::Utf8.GetBytes($imageText))
        if ($image.Id -cne $contract.$kind.image_id -or $service.image -cne $contract.$kind.config_image -or
            $image.Os -cne 'linux' -or $image.Architecture -cne 'amd64') { throw 'Rendered intended image differs from accepted identity.' }
        if ($kind -ceq 'api' -and ($image.Config.Labels.'org.opencontainers.image.revision' -cne $contract.revision -or
            $service.mem_limit -ne 1073741824L -or $service.command -isnot [Array] -or
            ($service.command -join "`n") -cne 'stratus-api' -or
            ($service.entrypoint -join "`n") -cne '/usr/local/bin/stratus-runtime-entrypoint')) {
            throw 'Rendered API changed its source-defined entrypoint or resource ceiling.'
        }
        $secret = @($service.secrets)
        if ($secret.Count -ne 1 -or $secret[0].source -cne 'stratus-runtime-password' -or
            $secret[0].target -cne 'stratus_runtime_password') { throw 'Intended secret mount inventory differs.' }
        $secretSource = [IO.Path]::GetFullPath([string]$model.secrets.'stratus-runtime-password'.file)
        if ($secretSource -cne (Join-Path $contract.root '.secrets\stratus_runtime_password')) {
            throw 'Intended runtime secret source escaped its canonical fixed path.'
        }
        $script:intendedServices[$kind] = [pscustomobject]@{ service = $service; image = $image.Config; secret_source = $secretSource }
    }
}

function Assert-ParentIntendedContainer {
    param([string] $Kind, [object] $Inspect)
    if ($Kind -ceq 'postgres') { return }
    $intended = $script:intendedServices[$Kind]
    if ($null -eq $intended) { throw 'Independent intended service has not been derived.' }
    $service = $intended.service
    $image = $intended.image
    $environment = [Collections.Generic.Dictionary[string,string]]::new([StringComparer]::Ordinal)
    foreach ($entry in @($image.Env)) {
        if ($entry -isnot [string] -or $entry.IndexOf('=') -lt 1) { throw 'Image environment inventory is malformed.' }
        $split = $entry.IndexOf('='); $environment.Add($entry.Substring(0, $split), $entry.Substring($split + 1))
    }
    foreach ($entry in $service.environment.PSObject.Properties) { $environment[$entry.Name] = [string]$entry.Value }
    $wantedEnvironment = [string[]]@($environment.Keys | ForEach-Object { $_ + '=' + $environment[$_] })
    $actualEnvironment = [string[]]@($Inspect.Config.Env)
    [Array]::Sort($wantedEnvironment, [StringComparer]::Ordinal); [Array]::Sort($actualEnvironment, [StringComparer]::Ordinal)
    if (($actualEnvironment -join "`n") -cne ($wantedEnvironment -join "`n")) { throw 'Container environment differs from independently rendered intent.' }
    foreach ($pair in @(@('Cmd', 'command'), @('Entrypoint', 'entrypoint'))) {
        $wanted = @(Get-ParentOptionalProperty $service $pair[1] (Get-ParentOptionalProperty $image $pair[0]))
        $actual = @(Get-ParentOptionalProperty $Inspect.Config $pair[0])
        if (($actual -join "`n") -cne ($wanted -join "`n")) { throw 'Container executable argv differs from independently rendered intent.' }
    }
    if ($Inspect.Config.User -cne [string](Get-ParentOptionalProperty $service 'user' $image.User) -or
        $Inspect.Config.WorkingDir -cne [string](Get-ParentOptionalProperty $service 'working_dir' $image.WorkingDir) -or
        $Inspect.Config.Hostname -cne $Inspect.Id.Substring(0, 12) -or $Inspect.Config.Domainname -cne '' -or
        $Inspect.HostConfig.Privileged -or $Inspect.HostConfig.AutoRemove -or $Inspect.HostConfig.NetworkMode -cne 'weatherpoly_default' -or
        $Inspect.HostConfig.PidMode -cne '' -or $Inspect.HostConfig.UTSMode -cne '' -or
        ($null -ne $Inspect.HostConfig.CapAdd -and @($Inspect.HostConfig.CapAdd).Count -ne 0) -or
        ($null -ne $Inspect.HostConfig.Devices -and @($Inspect.HostConfig.Devices).Count -ne 0) -or
        ($null -ne $Inspect.HostConfig.DeviceRequests -and @($Inspect.HostConfig.DeviceRequests).Count -ne 0) -or
        ($null -ne $Inspect.HostConfig.VolumesFrom -and @($Inspect.HostConfig.VolumesFrom).Count -ne 0) -or
        ($null -ne $Inspect.HostConfig.SecurityOpt -and @($Inspect.HostConfig.SecurityOpt).Count -ne 0) -or
        $Inspect.HostConfig.Memory -ne [long](Get-ParentOptionalProperty $service 'mem_limit' 0)) {
        throw 'Container privileges, runtime identity, retention or resource policy differs.'
    }
    $mounts = @($Inspect.Mounts)
    $windowsSource = $intended.secret_source.Replace('\', '/')
    $linuxSource = '/run/desktop/mnt/host/' + $windowsSource.Substring(0, 1).ToLowerInvariant() + $windowsSource.Substring(2)
    $volumeDeclaration = Get-ParentOptionalProperty $image 'Volumes'
    $imageVolumes = @(if ($null -ne $volumeDeclaration) { $volumeDeclaration.PSObject.Properties.Name })
    if ($Kind -ceq 'provision' -and ($imageVolumes.Count -ne 1 -or $imageVolumes[0] -cne '/var/lib/postgresql/data')) {
        throw 'Pinned PostgreSQL image volume declaration differs.'
    }
    if ($Kind -ceq 'api' -and $imageVolumes.Count -ne 0) { throw 'Candidate API image must not declare writable volumes.' }
    $secretMounts = @($mounts | Where-Object { $_.Destination -ceq '/run/secrets/stratus_runtime_password' })
    if ($mounts.Count -ne (1 + $imageVolumes.Count) -or $secretMounts.Count -ne 1 -or
        $secretMounts[0].Type -cne 'bind' -or $secretMounts[0].RW -isnot [bool] -or $secretMounts[0].RW -or
        $secretMounts[0].Source.Replace('\', '/') -cnotin @($windowsSource, $linuxSource)) {
        throw 'Container mounts differ from the intended secret and pinned image volume declarations.'
    }
    foreach ($destination in $imageVolumes) {
        $volume = @($mounts | Where-Object { $_.Destination -ceq $destination })
        if ($volume.Count -ne 1 -or $volume[0].Type -cne 'volume' -or $volume[0].Driver -cne 'local' -or
            $volume[0].Name -isnot [string] -or $volume[0].Name -cnotmatch '^[0-9a-f]{64}$' -or
            $volume[0].Source -cne ('/var/lib/docker/volumes/' + $volume[0].Name + '/_data') -or
            $volume[0].RW -isnot [bool] -or -not $volume[0].RW -or $volume[0].Propagation -cne '') {
            throw 'Image-declared volume is not its exact anonymous local-volume projection.'
        }
    }
    $ports = $Inspect.HostConfig.PortBindings
    if ($Kind -ceq 'api') {
        $port = @($ports.'8000/tcp')
        if (@($ports.PSObject.Properties).Count -ne 1 -or $port.Count -ne 1 -or
            $port[0].HostIp -cne '127.0.0.1' -or $port[0].HostPort -cne '8000') {
            throw 'API port binding differs from exact loopback load-gate endpoint.'
        }
    }
    elseif ($null -ne $ports -and @($ports.PSObject.Properties).Count -ne 0) { throw 'Provision service cannot publish a port.' }
}

function Get-ParentStaticBoundaryHash {
    param([object] $Inspect)
    $networks = [ordered]@{}
    foreach ($name in @($Inspect.NetworkSettings.Networks.PSObject.Properties.Name | Sort-Object -CaseSensitive)) {
        $attachment = $Inspect.NetworkSettings.Networks.$name
        $networks[$name] = [ordered]@{ Aliases = @($attachment.Aliases); DriverOpts = $attachment.DriverOpts }
    }
    # Includes Config.Env only in the in-memory hash; secret values are never emitted.
    # Runtime endpoint addresses/StartedAt and one-way restart policy have separate checks.
    $hostConfiguration = [ordered]@{}
    foreach ($property in @($Inspect.HostConfig.PSObject.Properties | Sort-Object Name -CaseSensitive)) {
        if ($property.Name -cne 'RestartPolicy') { $hostConfiguration[$property.Name] = $property.Value }
    }
    $boundary = [ordered]@{
        Id = $Inspect.Id; Name = $Inspect.Name; Image = $Inspect.Image
        Config = $Inspect.Config; HostConfig = $hostConfiguration; Mounts = @($Inspect.Mounts)
        Networks = $networks
    }
    return [Stratus.ParentStage.Files]::Hash([Stratus.ParentStage.Files]::Utf8.GetBytes(
        ($boundary | ConvertTo-Json -Depth 30 -Compress)
    ))
}

function Get-ParentContainer {
    param([ValidateSet('postgres', 'provision', 'api')][string] $Kind, [switch] $AllowAbsent)
    $definition = $contract.$Kind
    $target = if ($Kind -ceq 'postgres') { $approvedPostgresId }
        elseif ($null -ne $objectIds[$Kind]) { [string]$objectIds[$Kind] }
        else { [string]$definition.name }
    $json = Invoke-ParentDocker -Arguments @('inspect', '--type', 'container', '--format', '{{json .}}', $target) -AllowAbsent:$AllowAbsent
    if ($null -eq $json) { return $null }
    $inspect = ConvertFrom-ParentJson ([Stratus.ParentStage.Files]::Utf8.GetBytes($json))
    if ($inspect.Id -isnot [string] -or $inspect.Id -notmatch '^[0-9a-f]{64}$' -or
        $inspect.Name -cne ('/' + $definition.name) -or $inspect.Image -cne $definition.image_id -or
        $inspect.Config.Image -cne $definition.config_image -or $inspect.RestartCount -ne 0 -or
        $inspect.Config.Labels.'com.docker.compose.project' -cne 'weatherpoly' -or
        $inspect.Config.Labels.'com.docker.compose.service' -cne $definition.service -or
        $inspect.State.Running -isnot [bool] -or $inspect.State.OOMKilled -isnot [bool] -or
        $inspect.State.OOMKilled -or $inspect.State.Paused -or $inspect.State.Restarting -or
        $inspect.State.Dead -or $inspect.State.Error -cne '' -or
        $inspect.HostConfig.RestartPolicy.MaximumRetryCount -ne 0) {
        throw "Exact $Kind object contract drift."
    }
    if ($Kind -ceq 'postgres') {
        if ($inspect.Id -cne $approvedPostgresId -or $inspect.Image -cne $approvedPostgresImage -or
            $inspect.Config.Image -cne $approvedPostgresConfigImage) { throw 'Retained PostgreSQL differs.' }
        if ($inspect.HostConfig.RestartPolicy.Name -cnotin @('no', 'unless-stopped')) { throw 'PostgreSQL restart policy differs.' }
        if ($inspect.State.Running -and $inspect.HostConfig.RestartPolicy.Name -cne 'no') { throw 'Running PostgreSQL must have restart=no.' }
    }
    else {
        if ($inspect.HostConfig.RestartPolicy.Name -cne 'no' -and
            ($Kind -cne 'api' -or $inspect.State.Running -or $inspect.HostConfig.RestartPolicy.Name -cne 'unless-stopped')) {
            throw "$Kind restart policy is not an allowed stopped-to-armed transition."
        }
        if ($null -ne $objectIds[$Kind] -and $inspect.Id -cne $objectIds[$Kind]) { throw "$Kind was recreated." }
        Assert-ParentIntendedContainer $Kind $inspect
        $objectIds[$Kind] = [string]$inspect.Id
    }
    $staticHash = Get-ParentStaticBoundaryHash -Inspect $inspect
    if ($script:objectStaticHashes.ContainsKey($Kind) -and $script:objectStaticHashes[$Kind] -cne $staticHash) {
        throw "Registered $Kind static configuration changed."
    }
    $script:objectStaticHashes[$Kind] = $staticHash
    if ($inspect.State.Running) {
        if ($inspect.State.Status -cne 'running' -or $inspect.State.Pid -le 0) { throw "$Kind runtime contradiction." }
        if ($null -ne $objectStarts[$Kind] -and $objectStarts[$Kind] -cne $inspect.State.StartedAt) { throw "$Kind restarted." }
        $objectStarts[$Kind] = [string]$inspect.State.StartedAt
    }
    elseif ($inspect.State.Status -cnotin @('created', 'exited') -or $inspect.State.Pid -ne 0) {
        throw "$Kind stopped state contradiction."
    }
    return $inspect
}

function Get-ParentPredecessorApi {
    param([switch] $ForContainment)
    $approved = $script:r1Proof.topology.production_services.api
    $json = Invoke-ParentDocker @('inspect', '--type', 'container', '--format', '{{json .}}', $approved.container_id)
    $inspect = ConvertFrom-ParentJson ([Stratus.ParentStage.Files]::Utf8.GetBytes($json))
    if ($inspect.Id -cne $approved.container_id -or $inspect.Image -cne $approved.image_id -or
        $inspect.Name -cne '/weatherpoly-api-1' -or
        $inspect.Config.Labels.'com.docker.compose.project' -cne 'weatherpoly' -or
        $inspect.Config.Labels.'com.docker.compose.service' -cne 'api' -or
        $inspect.State.Running -isnot [bool] -or -not (Test-ParentInt32 $inspect.State.Pid) -or $inspect.State.Pid -lt 0) {
        throw 'Retained API no longer has its exact accepted R1 ownership.'
    }
    $drift = $inspect.State.Running -or $inspect.State.Pid -ne 0 -or $inspect.State.OOMKilled -or
        $inspect.RestartCount -ne $approved.restart_count -or $inspect.State.Paused -or $inspect.State.Restarting -or
        $inspect.State.Dead -or $inspect.State.Error -cne ''
    if ($drift) {
        if (-not $ForContainment) { throw 'Retained predecessor API is not in its accepted stopped state.' }
        Add-ParentFailure 'retained predecessor API runtime drift observed during exact containment.'
    }
    if (-not $ForContainment -and $null -ne $script:predecessorApi -and
        (Get-ParentStaticBoundaryHash $inspect) -cne $script:predecessorApi.static_sha256) {
        throw 'Retained predecessor API changed before its declared replacement attempt.'
    }
    return $inspect
}

function Get-ParentContainmentContainer {
    param([ValidateSet('postgres', 'provision', 'api')][string] $Kind, [switch] $AllowAbsent)
    $id = if ($Kind -ceq 'postgres') { $approvedPostgresId } else { $objectIds[$Kind] }
    if ($null -eq $id) {
        if ($Kind -ceq 'api' -and $null -ne $script:predecessorApi -and
            -not (Test-Path -LiteralPath $paths.api_attempt)) { return Get-ParentPredecessorApi -ForContainment }
        # Only a full intended-policy match may discover a still-unregistered
        # attempt object. Already registered objects use the separate stop boundary.
        return Get-ParentContainer $Kind -AllowAbsent:$AllowAbsent
    }
    $definition = $contract.$Kind
    $json = Invoke-ParentDocker @('inspect', '--type', 'container', '--format', '{{json .}}', [string]$id) -AllowAbsent:$AllowAbsent
    if ($null -eq $json) { return $null }
    $inspect = ConvertFrom-ParentJson ([Stratus.ParentStage.Files]::Utf8.GetBytes($json))
    if ($inspect.Id -cne $id -or $inspect.Id -cnotmatch '^[0-9a-f]{64}$' -or
        $inspect.Name -cne ('/' + $definition.name) -or $inspect.Image -cne $definition.image_id -or
        $inspect.Config.Image -cne $definition.config_image -or
        $inspect.Config.Labels.'com.docker.compose.project' -cne 'weatherpoly' -or
        $inspect.Config.Labels.'com.docker.compose.service' -cne $definition.service -or
        $inspect.State.Running -isnot [bool] -or -not (Test-ParentInt32 $inspect.State.Pid) -or $inspect.State.Pid -lt 0) {
        throw 'Containment object lost exact registered ownership; no name-based stop is authorized.'
    }
    # Runtime drift caused containment and cannot veto stopping the same owned ID.
    # It remains failure evidence, never permission to accept/restart that object.
    if ($inspect.RestartCount -ne 0 -or $inspect.State.OOMKilled -or $inspect.State.Paused -or
        $inspect.State.Restarting -or $inspect.State.Dead -or $inspect.State.Error -cne '' -or
        $inspect.HostConfig.RestartPolicy.Name -cne 'no' -or
        ($null -ne $objectStarts[$Kind] -and $objectStarts[$Kind] -cne $inspect.State.StartedAt)) {
        Add-ParentFailure ($Kind + ' registered runtime drift observed during exact containment.')
    }
    return $inspect
}

function Assert-ParentTopology {
    $running = Invoke-ParentDocker @('ps', '--quiet', '--no-trunc')
    foreach ($id in @($running -split '\r?\n' | Where-Object { $_ -cne '' })) {
        if ($id -notmatch '^[0-9a-f]{64}$' -or $id -cnotin @($approvedPostgresId, $objectIds.api, $objectIds.provision)) {
            throw 'Unexpected running topology; no broad containment is authorized.'
        }
    }
}

function Get-ParentTransitionLaunchFamily {
    # Pure namespace derivation. Do not acquire the start fence here: the gate
    # still has to acquire it exclusively around its later authorized start.
    $suffix = '.watchdog-terminal.json'
    if ($paths.transition_terminal -isnot [string] -or
        -not $paths.transition_terminal.EndsWith($suffix, [StringComparison]::Ordinal)) {
        throw 'Early Transition terminal does not define the fixed artifact family.'
    }
    $prestart = $paths.transition_terminal.Substring(0, $paths.transition_terminal.Length - $suffix.Length)
    $family = [ordered]@{
        prestart = $prestart; lease = $prestart + '.watchdog-lease.json'
        terminal = $paths.transition_terminal; heartbeat = $prestart + '.watchdog-heartbeat.json'
        commit = $prestart + '.watchdog-commit.json'; live = $paths.transition_live
        fence = $prestart + '.watchdog-start-fence.lock'; revoke = $prestart + '.watchdog-start-revoked.json'
        attempt = $prestart + '.watchdog-start-attempt.json'; completion = $prestart + '.watchdog-start-complete.json'
        authorization = $prestart + '.parent-authorization.json'
    }
    foreach ($pair in @(@('fence','transition_start_fence'), @('revoke','transition_start_revoke'),
        @('attempt','transition_start_attempt'), @('completion','transition_start_completion'),
        @('authorization','parent_authorization'))) {
        if ($family[$pair[0]] -cne $paths.($pair[1])) { throw 'Early Transition family differs from the parent contract.' }
    }
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($path in @($ContractPath, $contract.load_acceptance_path) + @($artifactNames | ForEach-Object { $paths.$_ })) {
        if ($path -isnot [string] -or [IO.Path]::GetFullPath($path) -cne $path -or
            -not $path.StartsWith($contract.root + '\', [StringComparison]::OrdinalIgnoreCase) -or
            -not $seen.Add($path)) { throw 'Early Transition contract path is noncanonical, outside root or aliased.' }
    }
    foreach ($name in @('prestart','lease','heartbeat','commit')) {
        $path = $family[$name]
        if ([IO.Path]::GetFullPath($path) -cne $path -or
            -not $path.StartsWith($contract.root + '\', [StringComparison]::OrdinalIgnoreCase) -or
            -not $seen.Add($path)) { throw 'Derived early Transition artifact aliases or escapes its namespace.' }
    }
    return $family
}

function Get-ParentTransitionLaunchPlan {
    param([object] $Intent, [object] $PreStart, [object] $Fence, [DateTimeOffset] $Now)
    $family = Get-ParentTransitionLaunchFamily
    $message = $Intent.value
    foreach ($name in @('transition_session_id','prestart_sha256','prestart_payload_sha256','start_fence_sha256','hard_deadline_utc')) {
        if ($message.$name -isnot [string]) { throw 'Early Transition intent fields must be native strings.' }
    }
    foreach ($name in @('prestart_sha256','prestart_payload_sha256','start_fence_sha256')) {
        if ($message.$name -cnotmatch '\A[0-9a-f]{64}\z') { throw 'Early Transition intent digest is malformed.' }
    }
    if ($message.transition_session_id -cnotmatch '\A[0-9a-f]{32}\z' -or
        $PreStart.path -cne $family.prestart -or $PreStart.sha256 -cne $message.prestart_sha256 -or
        $Fence.path -cne $family.fence -or $Fence.sha256 -cne $message.start_fence_sha256) {
        throw 'Early Transition intent is not bound to the fixed exact source documents.'
    }
    $envelope = $PreStart.value
    Assert-ParentProperties $envelope @('format','accepted','payload','payload_sha256') 'early Transition PreStart'
    if ($envelope.format -cne 'stratus-post-migration-successor-transition-prestart-v2' -or
        $envelope.accepted -isnot [bool] -or -not $envelope.accepted -or
        $envelope.payload_sha256 -isnot [string] -or $envelope.payload_sha256 -cne $message.prestart_payload_sha256) {
        throw 'Early Transition PreStart envelope differs.'
    }
    $payloadBytes = [Stratus.ParentStage.Files]::Utf8.GetBytes(
        (Microsoft.PowerShell.Utility\ConvertTo-Json -InputObject $envelope.payload -Depth 20 -Compress))
    if ([Stratus.ParentStage.Files]::Hash($payloadBytes) -cne $envelope.payload_sha256) {
        throw 'Early Transition PreStart payload digest differs.'
    }
    $payload = $envelope.payload; $transition = $payload.transition_session
    $handoff = $transition.watchdog_handoff; $program = $transition.watchdog_program
    Assert-ParentProperties $handoff @('format','session_id','owner_pid','owner_start_time_utc',
        'hard_deadline_utc','heartbeat_timeout_seconds','lease_path','receipt_path','heartbeat_path','commit_path',
        'live_path','prestart_path','start_fence_path','start_fence_sha256','start_revoke_path',
        'start_attempt_path','start_completion_path') 'early Transition handoff'
    foreach ($property in $handoff.PSObject.Properties) {
        if ($property.Name -cin @('owner_pid','heartbeat_timeout_seconds')) {
            if (-not (Test-ParentInt32 $property.Value)) { throw 'Early Transition handoff integer differs.' }
        }
        elseif ($property.Value -isnot [string]) { throw 'Early Transition handoff requires native strings.' }
    }
    $accepted = Get-ParentUtc $payload.accepted_at
    $hardDeadline = Get-ParentUtc $message.hard_deadline_utc
    Assert-ParentProperties $payload.validity @('max_age_seconds','postgres_start_window_seconds','expires_at','postgres_start_deadline') 'early Transition validity'
    if (-not (Test-ParentInt32 $payload.validity.max_age_seconds) -or
        -not (Test-ParentInt32 $payload.validity.postgres_start_window_seconds) -or
        (Get-ParentUtc $payload.validity.expires_at) -ne $accepted.AddSeconds(600)) {
        throw 'Early Transition validity types or expiry differ.'
    }
    if ($transition.mode -cne 'single-process' -or $transition.session_id -cne $message.transition_session_id -or
        $handoff.format -cne 'stratus-successor-transition-watchdog-handoff-v3' -or
        $handoff.session_id -cne $message.transition_session_id -or $handoff.owner_pid -ne $contract.owner.pid -or
        $handoff.owner_start_time_utc -cne $contract.owner.start_time_utc -or
        $handoff.hard_deadline_utc -cne $message.hard_deadline_utc -or $handoff.heartbeat_timeout_seconds -ne 15 -or
        $accepted -gt $Now.AddSeconds(2) -or $accepted -lt $leaseTime -or
        $hardDeadline -ne $accepted.AddSeconds(300) -or $hardDeadline -ge $deadline -or
        $hardDeadline -le $Now.AddSeconds(30) -or
        $payload.validity.postgres_start_deadline -cne $message.hard_deadline_utc -or
        $payload.validity.max_age_seconds -ne 600 -or $payload.validity.postgres_start_window_seconds -ne 300 -or
        $handoff.start_fence_sha256 -cne $message.start_fence_sha256) {
        throw 'Early Transition session, owner or original bounded deadline differs.'
    }
    foreach ($pair in @(@('lease_path','lease'), @('receipt_path','terminal'), @('heartbeat_path','heartbeat'),
        @('commit_path','commit'), @('live_path','live'), @('prestart_path','prestart'), @('start_fence_path','fence'),
        @('start_revoke_path','revoke'), @('start_attempt_path','attempt'), @('start_completion_path','completion'))) {
        if ($handoff.($pair[0]) -cne $family[$pair[1]]) { throw 'Early Transition handoff path differs.' }
    }
    Assert-ParentProperties $Fence.value @('format','session_id','owner_pid','owner_start_time_utc','created_at') 'early fence marker'
    $created = Get-ParentUtc $Fence.value.created_at
    if ($Fence.value.format -cne 'stratus-successor-transition-start-fence-v1' -or
        $Fence.value.session_id -cne $handoff.session_id -or -not (Test-ParentInt32 $Fence.value.owner_pid) -or
        $Fence.value.owner_pid -ne $contract.owner.pid -or $Fence.value.owner_start_time_utc -cne $contract.owner.start_time_utc -or
        $created -lt $leaseTime -or $created -gt $accepted) { throw 'Early Transition fence marker identity differs.' }
    Assert-ParentProperties $program @('script_path','script_sha256','script_bytes','host_path','host_sha256',
        'host_bytes','owner_pid','owner_start_time_utc','read_locks_held') 'early Transition program'
    $relative = 'scripts/Invoke-StratusSuccessorTransitionWatchdog.ps1'
    if (-not $script:r1Proof.source.ContainsKey($relative)) { throw 'R1 omits Transition source.' }
    $source = $script:r1Proof.source[$relative]
    $sourcePath = Join-Path $contract.root $relative.Replace('/', '\')
    foreach ($name in @('script_path','script_sha256','host_path','host_sha256','owner_start_time_utc')) {
        if ($program.$name -isnot [string]) { throw 'Early Transition program identity requires native strings.' }
    }
    foreach ($name in @('script_bytes','host_bytes')) {
        if (($program.$name -isnot [int] -and $program.$name -isnot [long]) -or $program.$name -lt 1 -or
            $program.$name -gt 67108864) { throw 'Early Transition program byte count is invalid.' }
    }
    if ($program.script_path -cne $sourcePath -or $program.script_sha256 -cne $source.sha256 -or
        $program.script_bytes -ne $source.bytes -or $program.host_path -cne $contract.watchdog.host_path -or
        $program.host_sha256 -cne $contract.watchdog.host_sha256 -or
        -not (Test-ParentInt32 $program.owner_pid) -or $program.owner_pid -ne $contract.owner.pid -or
        $program.owner_start_time_utc -cne $contract.owner.start_time_utc -or
        $program.read_locks_held -isnot [bool] -or -not $program.read_locks_held -or
        $payload.successor.revision -cne $contract.revision -or $payload.successor.image_id -cne $contract.api.image_id -or
        $payload.successor.image -cne $contract.api.config_image) {
        throw 'Early Transition source, host, owner or candidate differs from independent R1/contract.'
    }
    $plan = [Collections.Generic.Dictionary[string,string]]::new([StringComparer]::Ordinal)
    $plan.Add('Root', $contract.root); $plan.Add('SessionId', $handoff.session_id)
    $plan.Add('OwnerPid', $contract.owner.pid.ToString([Globalization.CultureInfo]::InvariantCulture))
    $plan.Add('OwnerStartTimeUtc', $contract.owner.start_time_utc); $plan.Add('DeadlineUtc', $handoff.hard_deadline_utc)
    $plan.Add('HeartbeatTimeoutSeconds', '15'); $plan.Add('ExpectedPreStartSha256', $PreStart.sha256)
    $plan.Add('ExpectedStartFenceSha256', $Fence.sha256); $plan.Add('PostgresContainer', $approvedPostgresId)
    foreach ($pair in @(@('LeasePath','lease'), @('ReceiptPath','terminal'), @('HeartbeatPath','heartbeat'),
        @('CommitPath','commit'), @('LiveAcceptancePath','live'), @('PreStartAttestationPath','prestart'),
        @('StartFencePath','fence'), @('StartRevokePath','revoke'), @('StartAttemptPath','attempt'), @('StartCompletionPath','completion'))) {
        $plan.Add($pair[0], $family[$pair[1]])
    }
    # Scoped launch binding only. Full durable causal closure/R1/raw repair and
    # fresh native liveness at mutation boundaries remain separately blocked.
    return [pscustomobject]@{ arguments = $plan; family = $family; source_path = $sourcePath
        source_sha256 = $source.sha256; source_bytes = $source.bytes; host_bytes = $program.host_bytes; intent = $Intent }
}

function Start-ParentTransitionLaunch {
    param([object] $Intent)
    if ($script:transitionLaunchAttempted -or $null -ne $script:transitionLaunch -or $null -ne $script:transitionLaunchPlan) {
        throw 'Independent Transition launch is single-use, including uncertain failure.'
    }
    Assert-ParentHeartbeat
    $family = Get-ParentTransitionLaunchFamily
    $prestartBytes = Read-ParentHeldBytes $family.prestart $Intent.value.prestart_sha256
    $prestart = [pscustomobject]@{ value = ConvertFrom-ParentJson $prestartBytes
        path = $family.prestart; sha256 = [Stratus.ParentStage.Files]::Hash($prestartBytes) }
    # Bounded transient marker read only, never a shared early start-fence lock.
    $fence = Read-ParentDocument $family.fence 8192
    $plan = Get-ParentTransitionLaunchPlan $Intent $prestart $fence ([DateTimeOffset]::UtcNow)
    $sourceBytes = Read-ParentHeldBytes $plan.source_path $plan.source_sha256
    if ($sourceBytes.LongLength -ne $plan.source_bytes -or $script:hostLock.Length -ne $plan.host_bytes) {
        throw 'Early Transition held source or host byte count differs.'
    }
    [void](Assert-ParentBootstrapHeldFile $script:hostLock $contract.watchdog.host_path $contract.watchdog.host_sha256)
    foreach ($name in @('lease','terminal','heartbeat','commit','live','revoke','attempt','completion','authorization')) {
        [void](Assert-ParentRegularAncestry $family[$name] -Absent)
    }
    foreach ($name in @('transition_launch_ack','transition_capture_complete')) {
        [void](Assert-ParentRegularAncestry $paths.$name -Absent)
    }
    Assert-ParentEngine
    if ((Get-ParentContainer postgres).State.Running) { throw 'Early Transition requires stopped exact retained PostgreSQL.' }
    Assert-ParentTopology
    $windows = [Environment]::GetFolderPath([Environment+SpecialFolder]::Windows)
    [void](Assert-ParentRegularAncestry $windows)
    $environment = [Collections.Generic.Dictionary[string,string]]::new([StringComparer]::OrdinalIgnoreCase)
    $environment.Add('SystemRoot', $windows); $environment.Add('WINDIR', $windows)
    $environment.Add('PATH', (([IO.Path]::GetDirectoryName($contract.watchdog.host_path)) + ';' +
        ([IO.Path]::GetDirectoryName($contract.docker.executable)) + ';' + (Join-Path $windows 'System32')))
    foreach ($entry in @(@('USERPROFILE','UserProfile'), @('LOCALAPPDATA','LocalApplicationData'),
        @('APPDATA','ApplicationData'), @('ProgramData','CommonApplicationData'))) {
        $value = [Environment]::GetFolderPath([Environment+SpecialFolder]$entry[1])
        [void](Assert-ParentRegularAncestry $value); $environment.Add($entry[0], $value)
    }
    $temp = Join-Path $environment['LOCALAPPDATA'] 'Temp'; [void](Assert-ParentRegularAncestry $temp)
    $environment.Add('TEMP', $temp); $environment.Add('TMP', $temp)
    $environment.Add('ComSpec', (Join-Path $windows 'System32\cmd.exe'))
    $script:transitionLaunchPlan = $plan
    $script:transitionLaunchEvidence.prestart = $prestart
    Assert-ParentHeartbeat
    # Prepare is managed-only. Retain the exact capability BEFORE native start;
    # a failed native launch/cleanup must never disappear inside a constructor.
    $script:transitionLaunch = [Stratus.ParentStage.IndependentTransitionLaunch]::Prepare(
        $contract.watchdog.host_path, $contract.watchdog.host_sha256, $plan.source_path, $plan.source_sha256,
        $contract.watchdog.runtime_path, $RuntimeSha256, $ContractPath, $ContractSha256, $plan.arguments, $environment)
    $script:transitionLaunchAttempted = $true # native-attempt latch, never retry
    Assert-ParentHeartbeat
    $script:transitionLaunch.StartSuspended()
    $script:transitionLaunch.AssertSuspended()
    Assert-ParentHeartbeat
    $script:transitionLaunchResumed = $true # latch before Resume, never retry after ambiguity
    $script:transitionLaunch.Resume()
}

function Assert-ParentTransitionLaunchReady {
    $launch = $script:transitionLaunch; $ready = $script:transitionLaunchReady
    if ($null -eq $launch -or $null -eq $ready -or -not $script:transitionLaunchResumed) {
        throw 'Transition readiness requires this parent native launch capability.'
    }
    $launch.AssertReadyLive()
    if (-not [object]::ReferenceEquals($ready, $launch.PollReady()) -or
        $ready.ProcessId -ne $launch.ProcessId -or $ready.StartedAt -cne $launch.StartedAt -or
        $ready.SessionId -cne $launch.SessionId -or $ready.Nonce -cne $launch.Nonce -or
        $ready.LeasePath -cne $script:transitionLaunchPlan.family.lease) {
        throw 'Transition READY lost the exact original process/private-pipe binding.'
    }
    $bytes = [Stratus.ParentStage.Files]::Utf8.GetBytes($ready.Text)
    if ($bytes.LongLength -ne $ready.ByteCount -or $bytes.Length -gt 8192 -or
        [Stratus.ParentStage.Files]::Hash($bytes) -cne $ready.Sha256) { throw 'Transition READY captured bytes differ.' }
    foreach ($name in @($script:transitionLaunchEvidence.Keys)) {
        $document = $script:transitionLaunchEvidence[$name]
        if ((Read-ParentDocument $document.path 1048576).sha256 -cne $document.sha256) {
            throw 'Early Transition immutable evidence changed.'
        }
    }
    $launch.AssertReadyLive()
}

function Write-ParentTransitionLaunchAck {
    # Separate from later causal-closure ACK. This only acknowledges our live
    # captured READY; every mutation authorization is false.
    Assert-ParentHeartbeat
    Assert-ParentTransitionLaunchReady
    $intent = $script:transitionLaunchPlan.intent; $ready = $script:transitionLaunchReady
    $boundary = [ordered]@{
        transition_session_id = $ready.SessionId; process_id = $ready.ProcessId
        process_started_at = $ready.StartedAt; nonce = $ready.Nonce
        lease_path = $ready.LeasePath; lease_sha256 = $ready.LeaseSha256
        ready_sha256 = $ready.Sha256; ready_bytes = $ready.ByteCount
        argv_sha256 = $script:transitionLaunch.ArgvSha256
        environment_sha256 = $script:transitionLaunch.EnvironmentSha256
        prestart_sha256 = $script:transitionLaunchEvidence.prestart.sha256
        hard_deadline_utc = $script:transitionLaunchPlan.arguments['DeadlineUtc']
    }
    $hash = Write-ParentRecoveryDocument 'transition_launch_ack' $paths.transition_launch_ack ([ordered]@{
        format = 'stratus-parent-transition-launch-ack-v1'; session_id = $contract.session_id
        lease_sha256 = $script:leaseHash; acknowledged_at = [DateTimeOffset]::UtcNow.ToString('o')
        watchdog_pid = $PID; watchdog_start_time_utc = $watchdogIdentity.start_time_utc
        source_path = $intent.path; source_sha256 = $intent.sha256
        boundary = $boundary; authorization = Get-ParentNonAuthorization
    })
    $published = Read-ParentDocument $paths.transition_launch_ack
    if ($published.sha256 -cne $hash) { throw 'Early Transition ACK differs from own publication.' }
    Assert-ParentHeartbeat
    Assert-ParentTransitionLaunchReady
    $observed.transition_launch_ack = $published
}

function Update-ParentTransitionLaunch {
    if ($null -eq $script:transitionLaunch) { return }
    if ($null -eq $script:transitionLaunchReady) {
        $ready = $script:transitionLaunch.PollReady()
        if ($null -eq $ready) { return }
        $bytes = Read-ParentHeldBytes $ready.LeasePath $ready.LeaseSha256 262144
        $lease = ConvertFrom-ParentJson $bytes
        Assert-ParentProperties $lease @('format','armed','armed_at','session_id','watchdog','owner','deadline',
            'artifacts','docker_engine','postgres','containment') 'early captured Transition lease'
        $plan = $script:transitionLaunchPlan; $arguments = $plan.arguments
        if ($lease.format -cne 'stratus-successor-transition-watchdog-lease-v3' -or
            $lease.armed -isnot [bool] -or -not $lease.armed -or $lease.session_id -cne $arguments['SessionId'] -or
            -not (Test-ParentInt32 $lease.watchdog.process_id) -or $lease.watchdog.process_id -ne $ready.ProcessId -or
            $lease.watchdog.process_started_at -cne $ready.StartedAt -or
            -not (Test-ParentInt32 $lease.owner.process_id) -or $lease.owner.process_id -ne $contract.owner.pid -or
            $lease.owner.process_started_at -cne $contract.owner.start_time_utc -or
            $lease.deadline.hard_deadline_utc -cne $arguments['DeadlineUtc'] -or
            $lease.deadline.heartbeat_timeout_seconds -ne 15 -or $lease.deadline.maximum_lease_seconds -ne 600 -or
            $lease.artifacts.prestart_sha256 -cne $arguments['ExpectedPreStartSha256'] -or
            $lease.artifacts.prestart_payload_sha256 -cne $plan.intent.value.prestart_payload_sha256 -or
            $lease.artifacts.start_fence_sha256 -cne $arguments['ExpectedStartFenceSha256']) {
            throw 'Captured Transition lease is not bound to the launched source plan.'
        }
        $armedAt = Get-ParentUtc $lease.armed_at
        if ($armedAt -lt (Get-ParentUtc $ready.StartedAt) -or $armedAt -gt [DateTimeOffset]::UtcNow.AddSeconds(2) -or
            $armedAt -ge (Get-ParentUtc $arguments['DeadlineUtc'])) { throw 'Captured Transition lease time differs.' }
        foreach ($pair in @(@('lease_path','lease'), @('receipt_path','terminal'), @('heartbeat_path','heartbeat'),
            @('commit_path','commit'), @('live_path','live'), @('prestart_path','prestart'), @('start_fence_path','fence'),
            @('start_revoke_path','revoke'), @('start_attempt_path','attempt'), @('start_completion_path','completion'))) {
            if ($lease.artifacts.($pair[0]) -cne $plan.family[$pair[1]]) { throw 'Captured Transition lease artifact path differs.' }
        }
        $script:transitionLaunchEvidence.lease = [pscustomobject]@{ path = $ready.LeasePath; sha256 = $ready.LeaseSha256; value = $lease }
        $script:transitionLaunchReady = $ready
        Write-ParentTransitionLaunchAck
    }
    if ($null -ne $script:transitionLaunchTerminal) { return }
    $terminal = $script:transitionLaunch.PollTerminalExit()
    # COMPLETE, root exit and pipe EOF arrive independently. PollTerminalExit
    # validates that pending capture; sole-READY liveness would poison a normal
    # terminal tail. Mutation callers need a separate fresh-live exchange.
    if ($null -eq $terminal) { return }
    if (-not [object]::ReferenceEquals($terminal.Ready, $script:transitionLaunchReady) -or
        $terminal.ProcessId -ne $script:transitionLaunch.ProcessId -or
        $terminal.StartedAt -cne $script:transitionLaunch.StartedAt -or
        $terminal.SessionId -cne $script:transitionLaunch.SessionId -or $terminal.Nonce -cne $script:transitionLaunch.Nonce -or
        $terminal.ExitCode -ne 0 -or $terminal.StderrBytes -ne 0 -or
        $terminal.ReceiptPath -cne $script:transitionLaunchPlan.family.terminal) {
        throw 'Transition COMPLETE lost its retained original native capture.'
    }
    $receiptBytes = Read-ParentHeldBytes $terminal.ReceiptPath $terminal.ReceiptSha256 262144
    if ($receiptBytes.LongLength -ne $terminal.ReceiptBytes) { throw 'Transition COMPLETE receipt length differs.' }
    $receipt = ConvertFrom-ParentJson $receiptBytes
    if ($receipt.format -cne 'stratus-successor-transition-watchdog-terminal-v1' -or
        $receipt.status -cne 'success' -or $receipt.session_id -cne $terminal.SessionId -or
        $receipt.owner_pid -ne $contract.owner.pid -or $receipt.owner_start_time_utc -cne $contract.owner.start_time_utc -or
        $receipt.lease_sha256 -cne $terminal.Ready.LeaseSha256 -or $receipt.lease_path -cne $terminal.Ready.LeasePath -or
        $receipt.receipt_path -cne $terminal.ReceiptPath) { throw 'Captured Transition terminal identity differs.' }
    # This receipt attests private capture, not causal PostgreSQL start, child-tree
    # exhaustion, full terminal semantics or eligibility for the completion cache.
    [void](Write-ParentRecoveryDocument 'transition_capture_complete' $paths.transition_capture_complete ([ordered]@{
        format = 'stratus-parent-transition-capture-v1'; session_id = $contract.session_id
        lease_sha256 = $script:leaseHash; transition_session_id = $terminal.SessionId
        process_id = $terminal.ProcessId; process_started_at = $terminal.StartedAt; exited_at = $terminal.ExitedAt
        exit_code = $terminal.ExitCode; ready_sha256 = $terminal.Ready.Sha256
        complete_sha256 = $terminal.CompleteSha256; complete_bytes = $terminal.CompleteBytes
        stdout_sha256 = $terminal.StdoutSha256; stdout_bytes = $terminal.StdoutBytes; stderr_bytes = $terminal.StderrBytes
        receipt_path = $terminal.ReceiptPath; receipt_sha256 = $terminal.ReceiptSha256; receipt_bytes = $terminal.ReceiptBytes
        authorization = Get-ParentNonAuthorization
    }))
    $script:transitionLaunchEvidence.terminal = [pscustomobject]@{ path = $terminal.ReceiptPath; sha256 = $terminal.ReceiptSha256; value = $receipt }
    $script:transitionLaunchTerminal = $terminal
}

function Assert-ParentTransitionControlRequest {
    param([object] $Value, [object] $Expected, [DateTimeOffset] $Now)
    # Pure domain consistency only. A caller-created object cannot establish the
    # private endpoint, launch identity, original request challenge or freshness.
    $fields = @('format','operation','parent_session_id','parent_lease_sha256','transition_session_id',
        'launch_intent_sha256','launch_ack_sha256','transition_nonce','transition_lease_sha256',
        'transition_ready_sha256','prestart_sha256','hard_deadline_utc','boundary')
    Assert-ParentProperties $Value $fields 'Transition control request'
    Assert-ParentProperties $Expected $fields 'Transition control expected binding'
    foreach ($field in $fields) {
        if ($Value.$field -isnot [string] -or $Expected.$field -isnot [string] -or
            $Value.$field -cne $Expected.$field) { throw ('Transition control binding differs: ' + $field) }
    }
    if ($Value.format -cne 'stratus-parent-transition-control-request-v1' -or
        $Value.operation -cne 'observe-transition-live' -or
        $Value.boundary -cne 'early-transition-control-probe') { throw 'Only the non-authorizing early control probe is implemented.' }
    foreach ($field in @('parent_session_id','transition_session_id')) {
        if ($Value.$field -cnotmatch '\A[0-9a-f]{32}\z') { throw 'Transition control session is malformed.' }
    }
    foreach ($field in @('parent_lease_sha256','launch_intent_sha256','launch_ack_sha256','transition_nonce',
        'transition_lease_sha256','transition_ready_sha256','prestart_sha256')) {
        if ($Value.$field -cnotmatch '\A[0-9a-f]{64}\z') { throw 'Transition control digest/nonce is malformed.' }
    }
    if ((Get-ParentUtc $Value.hard_deadline_utc) -le $Now) { throw 'Transition control original deadline expired.' }
}

function New-ParentTransitionControlResponse {
    param([object] $Value, [object] $Expected, [object] $ProcessId, [object] $ProcessStartedAt,
        [DateTimeOffset] $ObservedAt)
    Assert-ParentTransitionControlRequest $Value $Expected $ObservedAt
    if (-not (Test-ParentInt32 $ProcessId) -or $ProcessId -le 0 -or
        (Get-ParentUtc $ProcessStartedAt) -gt $ObservedAt -or $ObservedAt.Offset -ne [TimeSpan]::Zero) {
        throw 'Transition control observed process identity/time differs.'
    }
    $response = [ordered]@{
        format = 'stratus-parent-transition-control-response-v1'; operation = $Expected.operation
        parent_session_id = $Expected.parent_session_id; parent_lease_sha256 = $Expected.parent_lease_sha256
        transition_session_id = $Expected.transition_session_id; launch_intent_sha256 = $Expected.launch_intent_sha256
        launch_ack_sha256 = $Expected.launch_ack_sha256; transition_nonce = $Expected.transition_nonce
        transition_lease_sha256 = $Expected.transition_lease_sha256; transition_ready_sha256 = $Expected.transition_ready_sha256
        prestart_sha256 = $Expected.prestart_sha256; hard_deadline_utc = $Expected.hard_deadline_utc; boundary = $Expected.boundary
        process_id = $ProcessId; process_started_at = $ProcessStartedAt; observed_at = $ObservedAt.ToString('o')
        status = 'live'; authorization = Get-ParentNonAuthorization
    }
    $bytes = [Stratus.ParentStage.Files]::Utf8.GetBytes(($response | Microsoft.PowerShell.Utility\ConvertTo-Json -Depth 4 -Compress))
    if ($bytes.Length -lt 1 -or $bytes.Length -gt 4096) { throw 'Transition control response payload exceeds its fixed bound.' }
    return ,$bytes
}

function Get-ParentTransitionControlContext {
    # These bindings come from this owner's original launch and successful own
    # publication. Incoming JSON/ACK files never create a native capability.
    if (-not $script:armed -or -not $script:transitionLaunchResumed -or
        $null -eq $script:transitionLaunch -or $null -eq $script:transitionLaunchReady -or
        $null -eq $script:transitionLaunchPlan -or $null -ne $script:transitionLaunchTerminal -or
        -not $observed.ContainsKey('transition_launch_ack') -or
        -not $script:recoveryDocuments.ContainsKey('transition_launch_ack')) {
        throw 'Transition control lacks its actual live launch and owned ACK.'
    }
    $ack = $script:recoveryDocuments.transition_launch_ack
    $intent = $script:transitionLaunchPlan.intent
    if (-not $ack.published -or $ack.path -cne $paths.transition_launch_ack -or
        $ack.sha256 -cne $observed.transition_launch_ack.sha256 -or
        [Stratus.ParentStage.Files]::Hash($ack.bytes) -cne $ack.sha256 -or
        (Read-ParentDocument $ack.path 65536).sha256 -cne $ack.sha256 -or
        $intent.path -cne $paths.transition_launch_intent -or
        (Read-ParentDocument $intent.path 65536).sha256 -cne $intent.sha256) {
        throw 'Transition control owned ACK/intent publication changed.'
    }
    $ready = $script:transitionLaunchReady
    return [pscustomobject][ordered]@{
        format = 'stratus-parent-transition-control-request-v1'; operation = 'observe-transition-live'
        parent_session_id = $contract.session_id; parent_lease_sha256 = $script:leaseHash
        transition_session_id = $ready.SessionId; launch_intent_sha256 = $intent.sha256
        launch_ack_sha256 = $ack.sha256; transition_nonce = $ready.Nonce
        transition_lease_sha256 = $ready.LeaseSha256; transition_ready_sha256 = $ready.Sha256
        prestart_sha256 = $script:transitionLaunchEvidence.prestart.sha256
        hard_deadline_utc = $script:transitionLaunchPlan.arguments['DeadlineUtc']; boundary = 'early-transition-control-probe'
    }
}

function Service-ParentTransitionControl {
    # Only the owner main thread samples state. Native workers move bounded bytes;
    # they do not invoke PowerShell or respond from cached READY/ACK state. The
    # proposed 1000ms native roundtrip still requires independent qualification.
    if ($null -eq $script:controlEndpoint) { throw 'Parent private control endpoint is missing.' }
    $script:controlEndpoint.AssertHealthy()
    $request = $script:controlEndpoint.PollRequest()
    if ($null -eq $request) { return }
    if ($script:transitionControlAttempted) { throw 'The early control probe is one-shot; replay is not progress.' }
    $script:transitionControlAttempted = $true # uncertain validation/publication cannot become a retry
    if ($request.GetType() -ne [Stratus.ParentStage.PrivateControlRequest]) { throw 'Control request lacks the owned native endpoint type.' }
    $payload = [Stratus.ParentStage.Files]::Utf8.GetBytes($request.PayloadText)
    if ($payload.Length -lt 1 -or $payload.Length -gt 4096) { throw 'Transition control request payload exceeds its fixed bound.' }
    $value = ConvertFrom-ParentJson $payload
    Assert-ParentHeartbeat
    Assert-ParentTransitionLaunchReady
    $expected = Get-ParentTransitionControlContext
    $observedAt = [DateTimeOffset]::UtcNow
    $bytes = New-ParentTransitionControlResponse $value $expected $script:transitionLaunchReady.ProcessId `
        $script:transitionLaunchReady.StartedAt $observedAt
    # Serialization and held readback can delay a response. Repeat immutable
    # bindings, heartbeat, native identity, exact pipe state and clock before queueing.
    $again = Get-ParentTransitionControlContext
    Assert-ParentTransitionControlRequest $value $again ([DateTimeOffset]::UtcNow)
    $script:controlEndpoint.AssertHealthy()
    Assert-ParentHeartbeat
    Assert-ParentTransitionLaunchReady
    $script:controlEndpoint.Respond($request, $bytes)
    # No completion cache, authorization artifact, lease renewal or mutation is
    # published here. The gate still stops at its unchanged WIP boundary.
}

function Complete-ParentControlTransport {
    # Transport-only terminal half-close. The success handoff branch alone sets
    # eligibility after actual terminal publication and its existing supervisor
    # validation. No contained/dead-owner/unarmed cleanup may enter this path.
    if (-not $script:armed -or -not $script:controlDrainEligible -or $script:controlDrainAttempted -or
        $script:controlDrainCompleted -or $null -eq $script:controlEndpoint) {
        throw 'Parent terminal control drain requires the single proven handoff terminal.'
    }
    $endpoint = $script:controlEndpoint
    if ($endpoint.GetType() -ne [Stratus.ParentStage.PrivateControlEndpoint]) {
        throw 'Parent terminal control drain requires its original native endpoint.'
    }
    $script:controlDrainAttempted = $true # an uncertain Begin/Poll is never retried
    $endpoint.BeginTerminalDrain()
    while ($true) {
        if (-not [object]::ReferenceEquals($script:controlEndpoint, $endpoint)) {
            throw 'Parent terminal control drain lost its original endpoint.'
        }
        $complete = $endpoint.PollTerminalDrain()
        if (-not [object]::ReferenceEquals($script:controlEndpoint, $endpoint)) {
            throw 'Parent terminal control drain endpoint changed during observation.'
        }
        if ($complete -isnot [bool]) { throw 'Parent terminal control drain result must be a native boolean.' }
        if ($complete) { break }
        # The native Begin owns the original fixed30000ms deadline. Do not reset
        # it, renew heartbeat/authorization, or dispose resources while waiting.
        Start-Sleep -Milliseconds 10
    }
    $script:controlDrainCompleted = $true
    # No lifecycle authority or process/exit proof is returned by this drain.
}

function Assert-ParentTransitionCaptureContained {
    if ($null -eq $script:transitionLaunch) {
        if ($script:transitionLaunchAttempted -and -not $script:transitionLaunchAbortProven) {
            throw 'Transition creation outcome is ambiguous; missing capability is not containment proof.'
        }
        return
    }
    if (-not $script:transitionLaunchResumed) {
        $launch = $script:transitionLaunch
        $launch.AbortBeforeResume()
        if (-not [object]::ReferenceEquals($script:transitionLaunch, $launch)) {
            throw 'Transition abort lost the original retained launch capability.'
        }
        # Native Dispose must retire C before releasing its original T handle.
        # Failed abort, command retirement or release keeps the same script slot.
        $launch.Dispose()
        if (-not [object]::ReferenceEquals($script:transitionLaunch, $launch)) {
            throw 'Transition release lost the original retained launch capability.'
        }
        $script:transitionLaunchAbortProven = $true
        $script:transitionLaunch = $null
        return
    }
    # Do not kill/dispose the possibly last independent guard, or infer safety
    # from its PID, an exit-only proof, a receipt status or stopped DB snapshot.
    # Nonzero terminal and independent descendant/causal takeover remain WIP.
    throw 'Independent Transition terminal/containment takeover is unqualified; retain guard, HBA and fences.'
}

function Get-ParentMessage {
    param([string] $Name, [string[]] $Fields)
    if (-not (Test-Path -LiteralPath $paths.$Name)) { return $null }
    $document = Read-ParentDocument $paths.$Name
    if ($observed.ContainsKey($Name)) {
        if ($document.sha256 -cne $observed[$Name].sha256) { throw "Immutable artifact changed: $Name" }
        return $observed[$Name]
    }
    Assert-ParentProperties $document.value (@('format', 'session_id', 'lease_sha256', 'owner_pid',
        'owner_start_time_utc', 'published_at') + $Fields) $Name
    $value = $document.value
    foreach ($field in @('format', 'session_id', 'lease_sha256', 'owner_start_time_utc', 'published_at')) {
        if ($value.$field -isnot [string]) { throw "Message identity must use native strings: $Name/$field" }
    }
    $published = Get-ParentUtc $value.published_at
    if ($value.format -cne ('stratus-parent-' + $Name.Replace('_', '-') + '-v1') -or
        $value.session_id -cne $contract.session_id -or $value.lease_sha256 -cne $script:leaseHash -or
        -not (Test-ParentInt32 $value.owner_pid) -or $value.owner_pid -ne $contract.owner.pid -or
        $value.owner_start_time_utc -cne $contract.owner.start_time_utc -or
        $published -gt [DateTimeOffset]::UtcNow.AddSeconds(2) -or $published -lt $leaseTime -or
        $published -ge $deadline) {
        throw "Message is not bound to the live parent lease: $Name"
    }
    $observed[$Name] = $document
    return $document
}

function Write-ParentAck {
    param([string] $Name, [object] $Document, [object] $Extra = $null)
    Assert-ParentTransitionEvidenceHeld
    Assert-ParentHeartbeat
    if (Test-Path -LiteralPath $paths.$Name) {
        $existing = Read-ParentDocument $paths.$Name
        if (-not $observed.ContainsKey($Name) -or $existing.sha256 -cne $observed[$Name].sha256) {
            throw "Acknowledgement preexists or changed: $Name"
        }
        if ($Name -ceq 'observer_ack') { Assert-ParentObserverAckBoundary }
        return
    }
    $ack = [ordered]@{
        format = 'stratus-parent-ack-v1'; session_id = $contract.session_id
        lease_sha256 = $script:leaseHash; acknowledged_at = [DateTimeOffset]::UtcNow.ToString('o')
        watchdog_pid = $PID; watchdog_start_time_utc = $watchdogIdentity.start_time_utc
        source_path = $Document.path; source_sha256 = $Document.sha256
        boundary = $Extra; authorization = Get-ParentNonAuthorization
    }
    if ($Name -ceq 'observer_ack') { Assert-ParentObserverAckBoundary }
    $publishedHash = Write-ParentDocument $paths.$Name $ack
    $published = Read-ParentDocument $paths.$Name
    if ($published.sha256 -cne $publishedHash) { throw 'Acknowledgement readback differs from its own publication.' }
    Assert-ParentTransitionEvidenceHeld
    Assert-ParentHeartbeat
    if ($Name -ceq 'observer_ack') { Assert-ParentObserverAckBoundary }
    $observed[$Name] = $published
}

function Assert-ParentHeartbeat {
    # A fresh heartbeat cannot compensate for loss of the continuously held
    # mutation fence. Recover and retain any unexpectedly available fence before
    # accepting more progress; containment owns its eventual release.
    Assert-ParentMutationFenceHeld
    Assert-ParentOwner
    # A published authorization is historical evidence, never permission to ignore
    # a later causal revocation (including gate loss after authorization publish).
    if (Test-Path -LiteralPath $paths.transition_start_revoke) { throw 'Transition start authority was revoked.' }
    if ([DateTimeOffset]::UtcNow -ge $deadline -or $script:watchClock.Elapsed.TotalSeconds -ge $maximumLifetime) {
        throw 'Parent hard deadline expired.'
    }
    $document = Read-ParentDocument $paths.heartbeat 8192 -AtomicReplacement
    $heartbeat = $document.value
    Assert-ParentProperties $heartbeat @('format', 'session_id', 'lease_sha256', 'owner_pid',
        'owner_start_time_utc', 'sequence', 'published_at') 'heartbeat'
    foreach ($field in @('format', 'session_id', 'lease_sha256', 'owner_start_time_utc', 'published_at')) {
        if ($heartbeat.$field -isnot [string]) { throw 'Heartbeat identity must use native strings.' }
    }
    if (-not (Test-ParentInt32 $heartbeat.owner_pid)) { throw 'Heartbeat owner must be a native integer.' }
    $time = Get-ParentUtc $heartbeat.published_at
    if ($heartbeat.sequence -isnot [int] -and $heartbeat.sequence -isnot [long]) { throw 'Heartbeat sequence must be integer.' }
    if ($heartbeat.format -cne 'stratus-parent-heartbeat-v1' -or $heartbeat.session_id -cne $contract.session_id -or
        $heartbeat.lease_sha256 -cne $script:leaseHash -or $heartbeat.owner_pid -ne $contract.owner.pid -or
        $heartbeat.owner_start_time_utc -cne $contract.owner.start_time_utc -or $heartbeat.sequence -le 0 -or
        $heartbeat.sequence -lt $script:heartbeatSequence -or $time -gt [DateTimeOffset]::UtcNow.AddSeconds(2) -or
        $time -lt $script:heartbeatPublished -or [DateTimeOffset]::UtcNow.Subtract($time).TotalSeconds -gt $contract.heartbeat_ttl_seconds) {
        throw 'Heartbeat is stale, replayed, malformed, or from another owner.'
    }
    if ($heartbeat.sequence -eq $script:heartbeatSequence) {
        if ($time -ne $script:heartbeatPublished -or
            $script:heartbeatSeenClock.Elapsed.TotalSeconds -gt $contract.heartbeat_ttl_seconds) { throw 'Heartbeat publication stopped or replayed.' }
    }
    else {
        if ($script:heartbeatSequence -gt 0 -and $time -le $script:heartbeatPublished) { throw 'Heartbeat time failed to advance.' }
        $script:heartbeatSequence = [long]$heartbeat.sequence
        $script:heartbeatPublished = $time
        $script:heartbeatSeenClock.Restart()
    }
}

function Assert-ParentHandoff {
    param([object] $Request)
    Assert-ParentObserverCompleted
    $evidence = Read-ParentDocument $contract.load_acceptance_path 1048576
    if ($Request.value.load_acceptance_sha256 -cne $evidence.sha256 -or
        $Request.value.load_acceptance_path -cne $contract.load_acceptance_path -or
        $evidence.value.gate -cne 'stratus_candidate_production_load' -or
        $evidence.value.schema_version -ne 1 -or $evidence.value.production_gate_passed -isnot [bool] -or
        -not $evidence.value.production_gate_passed -or $evidence.value.exit_code -ne 0 -or
        @($evidence.value.violations).Count -ne 0 -or
        $evidence.value.candidate.api_image_id -cne $contract.api.image_id -or
        $evidence.value.candidate.revision -cne $contract.revision -or
        $evidence.value.candidate.release_digest -cne $contract.api.image_id.Substring(7)) {
        throw 'Handoff lacks exact successful immutable load evidence.'
    }
    foreach ($pair in @(@('http_artifact', 'http_sha256'), @('runtime_artifact', 'runtime_sha256'))) {
        $path = [string]$evidence.value.evidence.($pair[0])
        if (-not $path.StartsWith($contract.root + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Load evidence escaped root.' }
        if ($pair[0] -ceq 'runtime_artifact') {
            if ($path -cne $paths.observer_output -or
                $evidence.value.evidence.runtime_sha256 -cne $script:observerCompleted.output_sha256) {
                throw 'Handoff runtime artifact is not the complete output captured by this owned observer.'
            }
            $source = Read-ParentObserverEvidence output
        }
        else { $source = Read-ParentDocument $path 1048576 }
        if ($source.sha256 -cne $evidence.value.evidence.($pair[1])) { throw 'Load evidence source changed.' }
    }
    Assert-ParentEngine
    $postgres = Get-ParentContainer postgres
    $api = Get-ParentContainer api
    $provision = Get-ParentContainer provision
    Assert-ParentTopology
    if (-not $postgres.State.Running -or -not $api.State.Running -or $provision.State.Running -or
        $api.State.Health.Status -cne 'healthy' -or $postgres.State.Health.Status -cne 'healthy' -or
        $Request.value.postgres_started_at -cne $postgres.State.StartedAt -or
        $Request.value.api_id -cne $api.Id -or $Request.value.api_started_at -cne $api.State.StartedAt) {
        throw 'Handoff current PostgreSQL/API/provision state differs.'
    }
    return [ordered]@{ load_acceptance_sha256 = $evidence.sha256; api_id = $api.Id
        api_started_at = $api.State.StartedAt; postgres_id = $postgres.Id
        postgres_started_at = $postgres.State.StartedAt; provision_stopped = $true }
}

function Assert-ParentSuccessorSupervisor {
    # The same watchdog retains supervision; its load receipt is nonterminal.
    # Keep this terminal blocker until exact next-phase/stop acknowledgement is
    # independently accepted. No additional supervisor process is required.
    throw 'Continuous next-phase ownership is unimplemented; terminal handoff and HBA release are forbidden.'
}

function Assert-ParentObserverAckBoundary {
    # Sample the actual retained capability after generic evidence I/O, directly
    # at publication/readback-cache boundaries. This is not an atomic lifetime
    # guarantee; the subsequent watchdog loop and containment remain mandatory.
    if ($null -eq $script:observerChild -or -not $script:observerChildResumed -or
        $null -eq $script:observerPlan -or $null -eq $script:observerFrame) {
        throw 'Observer acknowledgement lacks this watchdog live readiness capability.'
    }
    $script:observerChild.AssertRunningOwned(450000)
    if ($script:observerPlan.clock.ElapsedMilliseconds -ge 30000) {
        throw 'Observer acknowledgement exceeded its original readiness deadline.'
    }
}

function Get-ParentObserverArguments {
    # Every token is fixed or derived from our accepted contract/private plan.
    # No caller executable, PID, script, argument vector or environment is admitted.
    if ($null -eq $script:observerPlan -or $script:observerPlan.nonce -cnotmatch '\A[0-9a-f]{64}\z') {
        throw 'Observer requires its private one-attempt launch plan.'
    }
    return [string[]]@('-NoLogo', '-NoProfile', '-NonInteractive', '-File', $script:observerPlan.script_path,
        '-Root', $contract.root, '-DurationSeconds', '390', '-DatabaseIntervalSeconds', '1',
        '-ResourceIntervalSeconds', '5', '-DrainSeconds', '5', '-ExpectedSchemaHead', '20260730_0047',
        '-MaxApiSessions', '4', '-LoadGateRunId', $contract.session_id,
        '-ExpectedApiImageId', $contract.api.image_id, '-ExpectedRevision', $contract.revision,
        '-ExpectedReleaseDigest', $contract.api.image_id.Substring(7),
        '-ReadyPath', $paths.observer_ready, '-OutputPath', $paths.observer_output,
        '-ParentStageNonce', $script:observerPlan.nonce)
}

function ConvertFrom-ParentObserverOutput {
    param([byte[]] $Bytes)
    # Only the full raw-sample artifact has the separately fixed 64 MiB bound.
    # Protocol messages and ready/terminal frames keep StrictJson.Validate's 1 MiB limit.
    [Stratus.ParentStage.StrictJson]::ValidateObserverOutput($Bytes)
    $parameters = @{ InputObject = [Stratus.ParentStage.Files]::Utf8.GetString($Bytes) }
    $command = Get-Command 'Microsoft.PowerShell.Utility\ConvertFrom-Json' -CommandType Cmdlet
    if ($command.Parameters.ContainsKey('DateKind')) { $parameters.DateKind = 'String' }
    return Microsoft.PowerShell.Utility\ConvertFrom-Json @parameters
}

function Read-ParentObserverEvidence {
    param([ValidateSet('ready', 'output')][string] $Name)
    $path = if ($Name -ceq 'ready') { $paths.observer_ready } else { $paths.observer_output }
    $maximum = if ($Name -ceq 'ready') { 65536L } else { 67108864L }
    if (-not $script:observerEvidence.ContainsKey($Name)) {
        [void](Assert-ParentRegularAncestry $path)
        $held = [IO.FileStream]::new($path, 'Open', 'Read', 'Read')
        $locks.Add($held)
        $script:observerEvidence[$Name] = [pscustomobject]@{ path = $path; handle = $held; sha256 = $null }
    }
    $entry = $script:observerEvidence[$Name]; $held = $entry.handle
    if ($entry.path -cne $path -or $held -isnot [IO.FileStream] -or -not $held.CanRead -or $held.CanWrite -or
        -not $held.CanSeek -or $held.SafeFileHandle.IsClosed -or $held.SafeFileHandle.IsInvalid -or $held.Name -cne $path) {
        throw 'Observer evidence lost its retained file identity.'
    }
    [void](Assert-ParentRegularAncestry $path)
    Assert-ParentTransitionSingleLink $held
    if ($held.Length -lt 1 -or $held.Length -gt $maximum) { throw 'Observer evidence exceeds its fixed byte bound.' }
    $held.Position = 0
    try {
        $bytes = [byte[]]::new([int]$held.Length); $offset = 0
        while ($offset -lt $bytes.Length) {
            $read = $held.Read($bytes, $offset, $bytes.Length - $offset)
            if ($read -le 0) { throw 'Observer evidence was truncated.' }
            $offset += $read
        }
        if ($held.ReadByte() -ne -1) { throw 'Observer evidence grew during readback.' }
        $actual = [Stratus.ParentStage.Files]::Hash($bytes)
        if ($null -eq $entry.sha256) { $entry.sha256 = $actual }
        if ($actual -cne $entry.sha256) { throw 'Observer evidence differs from its first retained bytes.' }
        $value = if ($Name -ceq 'output') { ConvertFrom-ParentObserverOutput $bytes } else { ConvertFrom-ParentJson $bytes }
        return [pscustomobject]@{ path = $path; sha256 = $actual; bytes = $bytes.LongLength; value = $value }
    }
    finally { $held.Position = 0 }
}

function Start-ParentOwnedObserver {
    param([object] $Intent)
    if ($script:observerLaunchAttempted -or $null -ne $script:observerChild -or $null -ne $script:observerPlan) {
        throw 'Owned observer launch is single-use, including uncertain failure.'
    }
    Assert-ParentHeartbeat
    Assert-ParentTransitionEvidenceHeld
    if (-not $observed.ContainsKey('api_start_ack') -or
        -not (Test-ParentValidatedCompletionUnchanged api) -or
        $Intent.value.api_start_complete_sha256 -isnot [string] -or
        $Intent.value.api_start_complete_sha256 -cne $script:validatedCompletions.api.completion_sha256) {
        throw 'Observer intent lacks independently accepted exact API completion.'
    }
    $api = Get-ParentContainer api; $postgres = Get-ParentContainer postgres
    if (-not $api.State.Running -or $api.State.Health.Status -cne 'healthy' -or
        -not $postgres.State.Running -or $postgres.State.Health.Status -cne 'healthy') {
        throw 'Owned observer requires the exact healthy API/PostgreSQL incarnation.'
    }
    Assert-ParentTopology
    # Include temporary publication siblings in the same namespace check. They
    # cannot alias another contract artifact or preexisting evidence.
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($path in @($ContractPath, $contract.load_acceptance_path) + @($artifactNames | ForEach-Object { $paths.$_ })) {
        if (-not $seen.Add($path)) { throw 'Observer launch contract paths alias.' }
    }
    foreach ($path in @(($paths.observer_ready + '.tmp'), ($paths.observer_output + '.tmp'))) {
        if (-not $seen.Add($path)) { throw 'Observer temporary publication aliases a protocol artifact.' }
        [void](Assert-ParentRegularAncestry $path -Absent)
    }
    foreach ($path in @($paths.observer_ready, $paths.observer_output, $paths.observer_complete)) {
        [void](Assert-ParentRegularAncestry $path -Absent)
    }
    $relative = 'scripts/Invoke-StratusRuntimeLoadObserver.ps1'
    if (-not $script:r1Proof.source.ContainsKey($relative)) { throw 'R1 omits the fixed observer source.' }
    $source = $script:r1Proof.source[$relative]
    $sourcePath = Join-Path $contract.root $relative.Replace('/', '\')
    $sourceBytes = Read-ParentHeldBytes $sourcePath $source.sha256
    if ($sourceBytes.LongLength -ne $source.bytes) { throw 'Held observer source length differs.' }
    [void](Assert-ParentBootstrapHeldFile $script:hostLock $contract.watchdog.host_path $contract.watchdog.host_sha256)
    if ($deadline.Subtract([DateTimeOffset]::UtcNow).TotalMilliseconds -lt 450000 -or
        $maximumLifetime - $script:watchClock.Elapsed.TotalSeconds -lt 450) {
        throw 'Parent lease cannot cover the complete bounded observer lifetime.'
    }
    $random = [Security.Cryptography.RandomNumberGenerator]::Create(); $nonceBytes = [byte[]]::new(32)
    try { $random.GetBytes($nonceBytes) } finally { $random.Dispose() }
    $script:observerPlan = [pscustomobject]@{ script_path = $sourcePath; script_sha256 = $source.sha256
        nonce = [BitConverter]::ToString($nonceBytes).Replace('-', '').ToLowerInvariant()
        intent = $Intent; clock = [Diagnostics.Stopwatch]::StartNew() }
    $arguments = Get-ParentObserverArguments
    Assert-ParentHeartbeat
    Assert-ParentTransitionEvidenceHeld
    $script:observerLaunchAttempted = $true
    $script:observerChild = [Stratus.ParentStage.NativeChild]::StartSuspended(
        $contract.watchdog.host_path, $contract.watchdog.host_sha256, $script:hostLock,
        $arguments, $contract.root, 65536)
    # Keep the capability reachable even when suspended proof or Resume fails.
    $script:observerChild.AssertSuspendedOwned()
    $script:observer = [pscustomobject]@{ pid = $script:observerChild.ProcessId
        start_time_utc = $script:observerChild.StartedAt; executable = $contract.watchdog.host_path }
    Assert-ParentHeartbeat
    $script:observerChildResumed = $true # attempt latch BEFORE the native call
    $script:observerChild.Resume()
}

function Assert-ParentObserverReadyFrame {
    param([object] $Frame, [object] $Ready)
    # Frame is delivered only by our retained child pipe collector. JSON fields
    # cross-check that capability; supplied booleans/PIDs cannot replace it.
    $bytes = [Stratus.ParentStage.Files]::Utf8.GetBytes($Frame.Text)
    if ($Frame.ProcessId -ne $script:observerChild.ProcessId -or $Frame.StartedAt -cne $script:observerChild.StartedAt -or
        $Frame.ByteCount -ne $bytes.LongLength -or $Frame.Sha256 -cne [Stratus.ParentStage.Files]::Hash($bytes)) {
        throw 'Owned observer frame lost its exact native capture binding.'
    }
    $frameValue = ConvertFrom-ParentJson $bytes
    Assert-ParentProperties $frameValue @('format', 'session_id', 'nonce', 'pid', 'started_at', 'ready_path', 'ready_sha256') 'observer ready frame'
    if ($bytes.Length -lt 2 -or $bytes.Length -gt 8192 -or
        $Frame.Text -cne ((Microsoft.PowerShell.Utility\ConvertTo-Json -InputObject $frameValue -Depth 4 -Compress) + "`n")) {
        throw 'Observer readiness capture is not one exact compact LF frame.'
    }
    foreach ($name in @('format', 'session_id', 'nonce', 'started_at', 'ready_path', 'ready_sha256')) {
        if ($frameValue.$name -isnot [string]) { throw 'Observer frame identity must use native strings.' }
    }
    if (-not (Test-ParentInt32 $frameValue.pid) -or $frameValue.pid -ne $Frame.ProcessId -or
        $frameValue.started_at -cne $Frame.StartedAt -or $frameValue.format -cne 'stratus-parent-observer-ready-v1' -or
        $frameValue.session_id -cne $contract.session_id -or $frameValue.nonce -cne $script:observerPlan.nonce -or
        $frameValue.ready_path -cne $paths.observer_ready -or $frameValue.ready_sha256 -cne $Ready.sha256) {
        throw 'Observer frame is not bound to this launch and its held ready bytes.'
    }
    $r = $Ready.value
    Assert-ParentProperties $r @('schema_version', 'gate', 'load_gate_run_id', 'ready_at', 'first_database_sample_at',
        'first_resource_sample_at', 'first_dynamic_topology_sample_at', 'preflight_database_sample_count',
        'preflight_resource_sample_count', 'preflight_topology_sample_count', 'api_image_id', 'revision',
        'release_digest', 'compose_project', 'preflight_safe', 'database_preflight', 'resource_preflight') 'observer ready marker'
    foreach ($name in @('gate', 'load_gate_run_id', 'ready_at', 'first_database_sample_at', 'first_resource_sample_at',
        'first_dynamic_topology_sample_at', 'api_image_id', 'revision', 'release_digest', 'compose_project')) {
        if ($r.$name -isnot [string]) { throw 'Observer ready identity requires native strings.' }
    }
    foreach ($name in @('schema_version', 'preflight_database_sample_count', 'preflight_resource_sample_count', 'preflight_topology_sample_count')) {
        if (-not (Test-ParentInt32 $r.$name)) { throw 'Observer ready counts require native integers.' }
    }
    if ($r.schema_version -ne 1 -or $r.gate -cne 'stratus_dashboard_runtime_observation_ready' -or
        $r.load_gate_run_id -cne $contract.session_id -or $r.api_image_id -cne $contract.api.image_id -or
        $r.revision -cne $contract.revision -or $r.release_digest -cne $contract.api.image_id.Substring(7) -or
        $r.compose_project -cne 'weatherpoly' -or $r.preflight_safe -isnot [bool] -or -not $r.preflight_safe -or
        $r.preflight_database_sample_count -lt 1 -or $r.preflight_resource_sample_count -lt 1 -or $r.preflight_topology_sample_count -lt 2) {
        throw 'Observer ready marker identity or preflight differs.'
    }
    $readyAt = Get-ParentUtc $r.ready_at; $birth = Get-ParentUtc $script:observerChild.StartedAt
    if ($readyAt -lt $birth -or $readyAt -gt [DateTimeOffset]::UtcNow.AddSeconds(2)) { throw 'Observer ready time differs from its launch.' }
    foreach ($name in @('first_database_sample_at', 'first_resource_sample_at', 'first_dynamic_topology_sample_at')) {
        $at = Get-ParentUtc $r.$name
        if ($at -lt $birth -or $at -gt $readyAt) { throw 'Observer preflight sample is outside its captured readiness interval.' }
    }
    Assert-ParentProperties $r.database_preflight @('api_sessions', 'api_idle_in_tx', 'global_idle_in_tx', 'lock_waiters', 'runtime_writer_sessions') 'observer database preflight'
    foreach ($p in $r.database_preflight.PSObject.Properties) {
        if (-not (Test-ParentInt32 $p.Value) -or $p.Value -lt 0 -or
            ($p.Name -ceq 'api_sessions' -and $p.Value -gt 4) -or ($p.Name -cne 'api_sessions' -and $p.Value -ne 0)) {
            throw 'Observer database preflight is not writer/contention-free.'
        }
    }
    Assert-ParentProperties $r.resource_preflight @('api_memory_percent', 'postgres_cpu_percent') 'observer resource preflight'
    foreach ($p in $r.resource_preflight.PSObject.Properties) {
        if ($p.Value -isnot [double] -and $p.Value -isnot [int] -and $p.Value -isnot [long] -and $p.Value -isnot [decimal]) {
            throw 'Observer resource preflight requires native JSON numbers.'
        }
        if ([double]::IsNaN([double]$p.Value) -or [double]::IsInfinity([double]$p.Value) -or $p.Value -lt 0 -or
            ($p.Name -ceq 'api_memory_percent' -and $p.Value -ge 90) -or ($p.Name -ceq 'postgres_cpu_percent' -and $p.Value -ge 200)) {
            throw 'Observer resource preflight exceeds the fixed boundary.'
        }
    }
}

function Update-ParentOwnedObserver {
    if ($null -eq $script:observerChild) { return }
    if (-not $script:observerChildResumed) { throw 'Observer child was not resumed by this watchdog.' }
    if ($null -eq $script:observerFrame) {
        if ($script:observerPlan.clock.ElapsedMilliseconds -ge 30000) { throw 'Owned observer readiness deadline expired.' }
        $frame = $script:observerChild.PollFirstOutputLine(450000, 8192)
        if ($null -eq $frame) { return }
        $ready = Read-ParentObserverEvidence ready
        Assert-ParentObserverReadyFrame $frame $ready
        $script:observerChild.AssertRunningOwned(450000)
        Assert-ParentHeartbeat
        Assert-ParentTransitionEvidenceHeld
        if ($script:observerPlan.clock.ElapsedMilliseconds -ge 30000) { throw 'Observer readiness readback exceeded its original deadline.' }
        $script:observerFrame = $frame
        $script:observerChild.AssertRunningOwned(450000)
        Write-ParentAck observer_ack $script:observerPlan.intent ([ordered]@{ observer_pid = $frame.ProcessId
            observer_started_at = $frame.StartedAt; ready_sha256 = $ready.sha256; frame_sha256 = $frame.Sha256 })
    }
    [void](Read-ParentObserverEvidence ready)
    $script:observerChild.AssertCleanCapture(450000)
    $result = $script:observerChild.PollResult(450000)
    $script:observerChild.AssertCleanCapture(450000)
    if ($null -eq $result) { return }
    if ($null -ne $script:observerResult -and -not [object]::ReferenceEquals($script:observerResult, $result)) {
        throw 'Owned observer completion capability was replaced.'
    }
    if ($null -eq $script:observerResult) {
        $script:observerResult = $result
        $script:observerResultObservedAt = [DateTimeOffset]::UtcNow.ToString('o')
    }
    if ($result.ProcessId -ne $script:observerChild.ProcessId -or $result.StartedAt -cne $script:observerChild.StartedAt -or
        $result.ExitCode -ne 0 -or $result.Stderr -cne '' -or $result.StderrBytes -ne 0) {
        throw 'Owned observer did not complete its exact tree/EOF capture successfully.'
    }
    # Parent mode has two raw LF frames. The terminal frame binds the complete
    # durable output bytes, not only a summary that could survive file replacement.
    if ($result.Stdout -isnot [string] -or -not $result.Stdout.StartsWith($script:observerFrame.Text, [StringComparison]::Ordinal)) {
        throw 'Observer stdout lost its first captured readiness frame.'
    }
    $terminalText = $result.Stdout.Substring($script:observerFrame.Text.Length)
    if (-not $terminalText.EndsWith("`n", [StringComparison]::Ordinal) -or $terminalText.Contains("`r") -or
        $terminalText.Substring(0, $terminalText.Length - 1).Contains("`n")) { throw 'Observer terminal capture must be one exact LF frame.' }
    $terminal = ConvertFrom-ParentJson ([Stratus.ParentStage.Files]::Utf8.GetBytes($terminalText))
    Assert-ParentProperties $terminal @('format', 'session_id', 'nonce', 'pid', 'started_at', 'output_path',
        'output_sha256', 'output_bytes', 'summary') 'observer completion frame'
    foreach ($name in @('format', 'session_id', 'nonce', 'started_at', 'output_path', 'output_sha256')) {
        if ($terminal.$name -isnot [string]) { throw 'Observer completion identity requires native strings.' }
    }
    if ($terminal.format -cne 'stratus-parent-observer-complete-v1' -or $terminal.session_id -cne $contract.session_id -or
        $terminal.nonce -cne $script:observerPlan.nonce -or -not (Test-ParentInt32 $terminal.pid) -or
        $terminal.pid -ne $script:observerChild.ProcessId -or $terminal.started_at -cne $script:observerChild.StartedAt -or
        $terminal.output_path -cne $paths.observer_output -or $terminal.output_sha256 -cnotmatch '\A[0-9a-f]{64}\z' -or
        -not (Test-ParentInt32 $terminal.output_bytes) -or $terminal.output_bytes -lt 1 -or $terminal.output_bytes -gt 67108864) {
        throw 'Observer terminal frame differs from this exact owned launch.'
    }
    $output = Read-ParentObserverEvidence output
    if ($terminal.output_sha256 -cne $output.sha256 -or $terminal.output_bytes -ne $output.bytes) {
        throw 'Observer held full output differs from its native captured terminal digest.'
    }
    $value = $output.value
    if ($value.gate -cne 'stratus_dashboard_runtime_observation' -or $value.schema_version -ne 2 -or
        $value.load_gate_run_id -cne $contract.session_id -or $value.expected_api_image_id -cne $contract.api.image_id -or
        $value.expected_revision -cne $contract.revision -or $value.expected_release_digest -cne $contract.api.image_id.Substring(7) -or
        $value.ready_marker -cne $paths.observer_ready -or $value.duration_seconds -ne 390 -or
        $value.database_interval_seconds -ne 1 -or $value.resource_interval_seconds -ne 5 -or $value.drain_seconds -ne 5 -or
        $value.maximum_api_sessions -ne 4 -or $value.expected_schema_head -cne '20260730_0047' -or
        $value.baseline.api_container_id -cne $objectIds.api -or $value.baseline.postgres_container_id -cne $approvedPostgresId -or
        $value.summary.runtime_passed -isnot [bool] -or
        -not $value.summary.runtime_passed -or @($value.violations).Count -ne 0 -or @($value.sample_errors).Count -ne 0) {
        throw 'Owned observer output identity or runtime outcome differs.'
    }
    $summaryText = Microsoft.PowerShell.Utility\ConvertTo-Json -InputObject $value.summary -Compress
    if ((Microsoft.PowerShell.Utility\ConvertTo-Json -InputObject $terminal.summary -Compress) -cne $summaryText) {
        throw 'Observer captured summary differs from its complete held output.'
    }
    $expectedText = $script:observerFrame.Text + (Microsoft.PowerShell.Utility\ConvertTo-Json -InputObject $terminal -Depth 8 -Compress) + "`n"
    $expectedBytes = [Stratus.ParentStage.Files]::Utf8.GetBytes($expectedText)
    if ($result.Stdout -cne $expectedText -or $result.StdoutBytes -ne $expectedBytes.LongLength) {
        throw 'Observer captured stdout differs from its ready frame and held final summary.'
    }
    $digest = [Stratus.ParentStage.Files]::Hash($expectedBytes)
    Assert-ParentHeartbeat
    Assert-ParentTransitionEvidenceHeld
    if ($null -eq $script:observerCompleted) {
        $script:observerCompleted = [pscustomobject]@{ output_sha256 = $output.sha256; stdout_sha256 = $digest
            stdout_bytes = $expectedBytes.LongLength; observed_at = $script:observerResultObservedAt; receipt_sha256 = $null }
    }
    elseif ($script:observerCompleted.output_sha256 -cne $output.sha256 -or $script:observerCompleted.stdout_sha256 -cne $digest -or
        $script:observerCompleted.stdout_bytes -ne $expectedBytes.LongLength) { throw 'Observer accepted capture was mutated after completion.' }
    $receiptHash = Write-ParentRecoveryDocument 'observer_complete' $paths.observer_complete ([ordered]@{
        format = 'stratus-parent-owned-observer-complete-v1'; session_id = $contract.session_id
        lease_sha256 = $script:leaseHash; observer_pid = $result.ProcessId; observer_started_at = $result.StartedAt
        output_sha256 = $output.sha256; stdout_sha256 = $digest; stdout_bytes = $expectedBytes.LongLength
        completed_at = $script:observerCompleted.observed_at; authorization = Get-ParentNonAuthorization })
    if ($null -eq $script:observerCompleted.receipt_sha256) { $script:observerCompleted.receipt_sha256 = $receiptHash }
    elseif ($script:observerCompleted.receipt_sha256 -cne $receiptHash) { throw 'Owned observer completion publication changed.' }
}

function Assert-ParentObserverCompleted {
    if ($null -eq $script:observerChild -or $null -eq $script:observerFrame -or $null -eq $script:observerCompleted -or
        $null -eq $script:observerCompleted.receipt_sha256 -or
        -not $observed.ContainsKey('observer_ack')) { throw 'Handoff requires this watchdog owned observer completion.' }
    Update-ParentOwnedObserver
    # Raw sample/composite acceptance is still a distinct required check. Native
    # exit/summary equality by itself does not accept the full load gate.
}

function Stop-ParentOwnedObserver {
    if ($null -eq $script:observerChild) {
        if ($script:observerLaunchAttempted) { throw 'Observer creation remains ambiguous; no empty-tree claim is permitted.' }
        return
    }
    # This exact capability owns the entire Job and both pipe collectors. Keep it
    # reachable on failure; root-PID death or wrapper disposal is not empty-tree proof.
    $script:observerChild.StopAndProve()
}

function Open-ParentFenceOnce {
    param([string] $Path)
    [void](Assert-ParentRegularAncestry $Path)
    try { return [IO.FileStream]::new($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::None) }
    catch [IO.IOException] {
        $native = $_.Exception.HResult -band 0xffff
        if ($native -notin @(32, 33)) { throw }
        return $null
    }
}

function Read-ParentRetainedEvidenceSnapshot {
    param([object] $Entry)
    $held = $Entry.handle
    if ($Entry.retired -or $held -isnot [IO.FileStream] -or -not $held.CanRead -or
        $held.CanWrite -or -not $held.CanSeek -or $held.SafeFileHandle.IsClosed -or
        $held.SafeFileHandle.IsInvalid -or $held.Name -cne $Entry.path) {
        throw 'Transition evidence lost its exact retained read-only handle.'
    }
    [void](Assert-ParentRegularAncestry $Entry.path)
    Assert-ParentTransitionSingleLink $held
    # Bound BEFORE allocating, reading or hashing. Never hash an unbounded file
    # merely to find out that it is not an admissible protocol document.
    if ($Entry.maximum_bytes -lt 1 -or $Entry.maximum_bytes -gt 1048576 -or
        $held.Length -lt 1 -or $held.Length -gt $Entry.maximum_bytes) {
        throw 'Transition evidence byte bound exceeded.'
    }
    $held.Position = 0
    try {
        $bytes = [byte[]]::new([int]$held.Length)
        $offset = 0
        while ($offset -lt $bytes.Length) {
            $count = $held.Read($bytes, $offset, $bytes.Length - $offset)
            if ($count -le 0) { throw 'Transition evidence was truncated.' }
            $offset += $count
        }
        if ($held.ReadByte() -ne -1) { throw 'Transition evidence grew during readback.' }
        $hash = [Security.Cryptography.SHA256]::Create()
        try { $actual = [BitConverter]::ToString($hash.ComputeHash($bytes)).Replace('-', '').ToLowerInvariant() }
        finally { $hash.Dispose() }
        return [pscustomobject]@{ bytes = $bytes; sha256 = $actual }
    }
    finally { $held.Position = 0 }
}

function Read-ParentTransitionEvidenceBytes {
    param([object] $Entry)
    $snapshot = Read-ParentRetainedEvidenceSnapshot $Entry
    if ($snapshot.sha256 -cne $Entry.sha256) { throw 'Transition evidence differs from its retained hash.' }
    return ,$snapshot.bytes
}

function Initialize-ParentTransitionEvidenceFence {
    # Gate releases its start fence before publishing this registration. Take
    # exclusive ownership FIRST, before retaining any ordinary Transition file.
    # There is no later shared-to-exclusive upgrade, pathname reopen or handoff.
    # The actual captured digest is provisional evidence, not authorization.
    if ($script:transitionEvidenceMode -cne 'receiving') { throw 'Transition fence capture is terminally disabled.' }
    if ($null -eq $script:transitionFenceCapture) {
        if ($script:transitionEvidence.Count -ne 0 -or $null -ne $script:transitionFenceLock) {
            throw 'Transition fence must be captured before any Transition evidence.'
        }
        $script:transitionFenceLock = Open-ParentFenceOnce $paths.transition_start_fence
        if ($null -eq $script:transitionFenceLock) { throw 'Transition fence unavailable; no evidence may be admitted.' }
        # Publish ownership before bounded read/hash/JSON can fail. The dedicated
        # containment field, not a local or an ordinary lock list, owns this handle.
        $script:transitionFenceCapture = [pscustomobject]@{
            name = 'fence'; path = $paths.transition_start_fence; sha256 = $null
            maximum_bytes = 8192; handle = $script:transitionFenceLock; retired = $false
        }
        $snapshot = Read-ParentRetainedEvidenceSnapshot $script:transitionFenceCapture
        $script:transitionFenceCapture.sha256 = $snapshot.sha256
        $script:transitionEvidence['fence'] = $script:transitionFenceCapture
    }
    # A failed/incomplete first capture must never be silently retried/rebound.
    if ($null -eq $script:transitionFenceCapture.sha256 -or
        -not $script:transitionEvidence.ContainsKey('fence') -or
        -not [object]::ReferenceEquals($script:transitionEvidence.fence, $script:transitionFenceCapture) -or
        -not [object]::ReferenceEquals($script:transitionFenceCapture.handle, $script:transitionFenceLock)) {
        throw 'Transition fence capture is incomplete or its exact ownership was lost.'
    }
    [void](Read-ParentTransitionEvidenceBytes $script:transitionFenceCapture)
}

function Read-ParentTransitionEvidence {
    param([string] $Name, [string] $Path, [string] $Sha256, [int] $MaximumBytes = 1048576)
    if ($script:transitionEvidenceMode -cne 'receiving') { throw 'Transition evidence acquisition is terminally disabled.' }
    if ($Name -cnotin @('registration', 'authorization', 'terminal', 'commit', 'lease', 'live',
        'prestart', 'fence', 'attempt', 'completion', 'repair') -or
        -not [regex]::IsMatch($Sha256, '\A[0-9a-f]{64}\z') -or
        $MaximumBytes -lt 1 -or $MaximumBytes -gt 1048576) { throw 'Invalid Transition evidence request.' }
    if ($null -eq $script:transitionFenceCapture -or
        -not $script:transitionEvidence.ContainsKey('fence') -or
        -not [object]::ReferenceEquals($script:transitionEvidence.fence, $script:transitionFenceCapture) -or
        -not [object]::ReferenceEquals($script:transitionFenceCapture.handle, $script:transitionFenceLock)) {
        throw 'Transition evidence requires its exact first-acquired exclusive fence.'
    }
    [void](Read-ParentTransitionEvidenceBytes $script:transitionFenceCapture)
    if ([StringComparer]::OrdinalIgnoreCase.Equals($Path, $paths.mutation_fence) -or
        ([StringComparer]::OrdinalIgnoreCase.Equals($Path, $paths.transition_start_fence) -and
            ($Name -cne 'fence' -or $Path -cne $paths.transition_start_fence)) -or
        ($Name -ceq 'fence' -and $Path -cne $paths.transition_start_fence)) {
        throw 'A protocol fence cannot enter the ordinary evidence registry.'
    }
    if (-not $Path.StartsWith($contract.root + '\', [StringComparison]::Ordinal)) {
        throw 'Transition evidence escaped the approved root.'
    }
    [void](Assert-ParentRegularAncestry $Path)
    if ($script:transitionEvidence.ContainsKey($Name)) {
        $entry = $script:transitionEvidence[$Name]
        if ($entry.path -cne $Path -or $entry.sha256 -cne $Sha256 -or $entry.maximum_bytes -ne $MaximumBytes) {
            throw 'A retained Transition evidence binding cannot be replaced.'
        }
    }
    else {
        foreach ($entry in $script:transitionEvidence.Values) {
            if ([StringComparer]::OrdinalIgnoreCase.Equals($entry.path, $Path)) { throw 'Transition evidence paths alias.' }
        }
        $held = [IO.FileStream]::new($Path, 'Open', 'Read', 'Read')
        try { Assert-ParentTransitionSingleLink $held }
        catch {
            # An inadmissible new handle is not retained: a hard-link alias could
            # otherwise block recovery of our own exclusive mutation fence.
            $primary = $_.Exception
            try { $held.Dispose() }
            catch { throw [AggregateException]::new([Exception[]]@($primary, $_.Exception)) }
            throw
        }
        $locks.Add($held) # Retain even after a parse/hash failure; armed containment owns cleanup.
        $entry = [pscustomobject]@{ name = $Name; path = $Path; sha256 = $Sha256
            maximum_bytes = $MaximumBytes; handle = $held; retired = $false }
        $script:transitionEvidence[$Name] = $entry
    }
    $bytes = Read-ParentTransitionEvidenceBytes $entry
    # Reparse exactly these checked bytes. A caller cannot mutate cached JSON and
    # turn a former parsed object into accepted authority on the next boundary.
    return [pscustomobject]@{ path = $Path; sha256 = $Sha256; value = (ConvertFrom-ParentJson $bytes) }
}

function Assert-ParentTransitionEvidenceHeld {
    if ($script:transitionEvidenceMode -cne 'receiving') { throw 'Transition evidence cannot authorize after containment entry.' }
    if ($script:transitionEvidence.Count -gt 0 -and
        ($null -eq $script:transitionFenceCapture -or
        -not $script:transitionEvidence.ContainsKey('fence') -or
        -not [object]::ReferenceEquals($script:transitionEvidence.fence, $script:transitionFenceCapture) -or
        -not [object]::ReferenceEquals($script:transitionFenceCapture.handle, $script:transitionFenceLock))) {
        throw 'Held Transition evidence lost its exact exclusive fence capture.'
    }
    foreach ($entry in $script:transitionEvidence.Values) { [void](Read-ParentTransitionEvidenceBytes $entry) }
}

function Assert-ParentTransitionSingleLink {
    param([IO.FileStream] $Held)
    [Stratus.ParentStage.Files]::RequireSingleLink($Held)
}

function Assert-ParentMutationFenceContainmentOwnership {
    # Armed-only emergency ownership, established at the fixed acquisition sites.
    # It is not a substitute for the strict active fence/arming/handoff checks.
    if ($script:armed -isnot [bool] -or -not $script:armed -or
        $fenceHash -isnot [string] -or -not [regex]::IsMatch($fenceHash, '\A[0-9a-f]{64}\z') -or
        $null -eq $script:ownerLease -or $script:ownerLease.IsAlive()) {
        throw 'Parent containment ownership requires its armed lease and exact owner stopped.'
    }
    $held = $script:fenceLock
    if ($held -isnot [IO.FileStream] -or -not $held.CanRead -or $held.CanWrite -or
        -not $held.CanSeek -or $held.SafeFileHandle.IsClosed -or $held.SafeFileHandle.IsInvalid -or
        $held.Name -cne $paths.mutation_fence) {
        throw 'Containment requires its exact live read-only exclusive parent fence.'
    }
}

function Enter-ParentTransitionEvidenceContainment {
    # Failure-only irreversible phase. Exact owner death and recovered parent
    # fence remain mandatory. The original exclusive Transition fence is NEVER
    # retired, closed, replaced or reopened; all other evidence remains held too.
    if ($null -eq $script:ownerLease -or $script:ownerLease.IsAlive()) { throw 'Evidence containment requires exact owner stopped.' }
    Assert-ParentMutationFenceContainmentOwnership
    $ownedParentFence = $script:fenceLock
    $parentFence = [pscustomobject]@{ path = $paths.mutation_fence; sha256 = $fenceHash
        maximum_bytes = 8192; handle = $ownedParentFence; retired = $false }
    $script:transitionEvidenceMode = 'containing'
    try { [void](Read-ParentTransitionEvidenceBytes $parentFence) }
    catch { Add-ParentFailure ('Parent fence evidence rejected during containment: ' + $_.Exception.Message) }
    if (-not [object]::ReferenceEquals($ownedParentFence, $script:fenceLock)) {
        throw 'Parent containment ownership changed during its diagnostic evidence read.'
    }
    Assert-ParentMutationFenceContainmentOwnership
}

function Assert-ParentTransitionContainmentOwnership {
    # Ownership here comes only from our fixed Open/Read/None acquisition site,
    # not a supplied flag, a digest or a sharing-denial probe. Losing that exact
    # live handle remains fatal even when document validation is only diagnostic.
    if ($null -ne $script:transitionFenceCapture) {
        $original = $script:transitionFenceCapture
        if ($original.retired -or $original.path -cne $paths.transition_start_fence -or
            -not [object]::ReferenceEquals($original.handle, $script:transitionFenceLock)) {
            throw 'Containment lost its exact first-acquired Transition fence.'
        }
    }
    else {
        if ($script:transitionEvidence.Count -ne 0) { throw 'Uncaptured Transition fence cannot own retained evidence.' }
    }
    $held = $script:transitionFenceLock
    if ($held -isnot [IO.FileStream] -or -not $held.CanRead -or $held.CanWrite -or
        -not $held.CanSeek -or $held.SafeFileHandle.IsClosed -or $held.SafeFileHandle.IsInvalid -or
        $held.Name -cne $paths.transition_start_fence) {
        throw 'Containment requires its exact live read-only exclusive Transition fence.'
    }
}

function Assert-ParentTransitionContainmentFence {
    if ($script:transitionEvidenceMode -cne 'containing') { throw 'Transition fence recovery requires irreversible containment entry.' }
    Assert-ParentTransitionContainmentOwnership
    $ownedTransitionFence = $script:transitionFenceLock
    $ownedCapture = $script:transitionFenceCapture
    # Invalid evidence forbids active continuation, but must not deadlock an
    # already-owned exclusive fence before causal STOP. Evidence failure stays
    # explicit; do not repair its hash, add a registry entry or grant authority.
    if ($null -eq $script:transitionFenceCapture -or $null -eq $script:transitionFenceCapture.sha256) {
        Add-ParentFailure 'Transition fence document remains unaccepted; exact exclusive ownership is containment-only.'
    }
    else {
        try { [void](Read-ParentTransitionEvidenceBytes $script:transitionFenceCapture) }
        catch { Add-ParentFailure ('Transition fence evidence rejected during containment: ' + $_.Exception.Message) }
    }
    # Diagnostic failure must never mask loss/disposal/substitution of ownership.
    if (-not [object]::ReferenceEquals($ownedTransitionFence, $script:transitionFenceLock) -or
        -not [object]::ReferenceEquals($ownedCapture, $script:transitionFenceCapture)) {
        throw 'Transition containment ownership changed during its diagnostic evidence read.'
    }
    Assert-ParentTransitionContainmentOwnership
}

function Assert-ParentMutationFenceHeld {
    # This is a loss detector, NOT proof of which process owns an exclusive
    # handle. Trusted bootstrap/ExactFence creation provenance is still required.
    # A recovered fence latches the active protocol closed, including on retry;
    # do not replace, dispose or reopen the containment-owned handle here.
    if ($null -ne $script:fenceLock) {
        throw 'Parent mutation fence is already recovered; active supervision cannot continue.'
    }
    $recoveredFence = Open-ParentFenceOnce $paths.mutation_fence
    if ($null -ne $recoveredFence) {
        $script:fenceLock = $recoveredFence
        throw 'Parent mutation fence was released; recovered fence retained for containment.'
    }
}

function Read-ParentHeldBytes {
    param([string] $Path, [string] $ExpectedSha256, [int] $MaximumBytes = 1048576)
    if ($ExpectedSha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'Held evidence requires an exact SHA-256.' }
    [void](Assert-ParentRegularAncestry $Path)
    $held = [IO.FileStream]::new($Path, 'Open', 'Read', 'Read')
    $locks.Add($held)
    if ($MaximumBytes -lt 1 -or $MaximumBytes -gt 67108864 -or $held.Length -lt 1 -or $held.Length -gt $MaximumBytes) {
        throw 'Held evidence byte bound exceeded before hashing.'
    }
    try {
        $bytes = [byte[]]::new([int]$held.Length); $offset = 0
        while ($offset -lt $bytes.Length) {
            $count = $held.Read($bytes, $offset, $bytes.Length - $offset)
            if ($count -le 0) { throw 'Held evidence was truncated.' }
            $offset += $count
        }
        if ($held.ReadByte() -ne -1) { throw 'Held evidence grew during readback.' }
        if ([Stratus.ParentStage.Files]::Hash($bytes) -cne $ExpectedSha256) { throw 'Held evidence bytes changed.' }
        return ,$bytes
    }
    finally { $held.Position = 0 }
}

function Get-ParentTransitionValidatorProgram {
    # Fixed R1-held function libraries only, loaded into the watchdog's own script
    # scope. No caller-supplied callback, script path, preloaded definition or type.
    $parts = [Collections.Generic.List[string]]::new()
    foreach ($relative in @('scripts/Stratus.ParentTransitionLineage.ps1', 'scripts/Stratus.ParentTransitionClosure.ps1')) {
        if (-not $script:r1Proof.source.ContainsKey($relative)) { throw 'Accepted source omits Transition validation code.' }
        $expected = $script:r1Proof.source[$relative]
        $bytes = Read-ParentHeldBytes (Join-Path $contract.root $relative.Replace('/', '\')) $expected.sha256
        if ($bytes.LongLength -ne $expected.bytes) { throw 'Transition validation source byte count differs.' }
        $source = [Stratus.ParentStage.Files]::Utf8.GetString($bytes)
        $tokens = $null; $errors = $null
        $ast = [Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)
        if (@($errors).Count -ne 0 -or $null -ne $ast.ParamBlock -or $null -ne $ast.BeginBlock -or
            $null -ne $ast.ProcessBlock -or $null -eq $ast.EndBlock -or $ast.EndBlock.Statements.Count -eq 0) {
            throw 'Transition validation source is not a function-only library.'
        }
        foreach ($statement in $ast.EndBlock.Statements) {
            if ($statement -isnot [Management.Automation.Language.FunctionDefinitionAst] -or
                $statement.Name -cnotmatch '^(Assert-Stratus(?:Lineage|Closure|ParentTransition)|Get-StratusLineage)[A-Za-z]*$' -or
                $null -ne (Get-Command -Name $statement.Name -CommandType Function -ErrorAction SilentlyContinue)) {
                throw 'Transition validator definition is unexpected or preloaded.'
            }
        }
        $parts.Add($source)
    }
    return [scriptblock]::Create(($parts -join "`n"))
}

function Get-ParentTransitionPacket {
    param([object] $Registration)
    Assert-ParentHeartbeat
    Initialize-ParentTransitionEvidenceFence
    $registration = Read-ParentTransitionEvidence registration $paths.transition_registration $Registration.sha256 262144
    foreach ($field in @('parent_authorization_sha256', 'transition_terminal_sha256', 'postgres_started_at')) {
        if ($registration.value.$field -isnot [string]) { throw 'Transition registration payload requires native strings.' }
    }
    $authorization = Read-ParentTransitionEvidence authorization $paths.parent_authorization $registration.value.parent_authorization_sha256
    $terminal = Read-ParentTransitionEvidence terminal $paths.transition_terminal $registration.value.transition_terminal_sha256
    $a = $authorization.value
    $suffix = '.watchdog-terminal.json'
    if (-not $paths.transition_terminal.EndsWith($suffix, [StringComparison]::Ordinal)) { throw 'Transition terminal path is not producer-derived.' }
    $prestartPath = $paths.transition_terminal.Substring(0, $paths.transition_terminal.Length - $suffix.Length)
    $fixed = [ordered]@{
        authorization = $prestartPath + '.parent-authorization.json'; terminal = $paths.transition_terminal
        lease = $prestartPath + '.watchdog-lease.json'; commit = $prestartPath + '.watchdog-commit.json'
        prestart = $prestartPath; fence = $prestartPath + '.watchdog-start-fence.lock'
        attempt = $prestartPath + '.watchdog-start-attempt.json'; completion = $prestartPath + '.watchdog-start-complete.json'
        heartbeat = $prestartPath + '.watchdog-heartbeat.json'; revoke = $prestartPath + '.watchdog-start-revoked.json'
    }
    foreach ($pair in @(@('authorization', $paths.parent_authorization), @('fence', $paths.transition_start_fence),
        @('attempt', $paths.transition_start_attempt), @('completion', $paths.transition_start_completion),
        @('revoke', $paths.transition_start_revoke))) {
        if ($fixed[$pair[0]] -cne $pair[1]) { throw 'Transition artifact paths are not one accepted producer family.' }
    }
    foreach ($pair in @(@('parent_authorization_path', 'authorization'), @('receipt_path', 'terminal'),
        @('lease_path', 'lease'), @('commit_path', 'commit'), @('start_fence_path', 'fence'),
        @('start_attempt_path', 'attempt'), @('start_completion_path', 'completion'))) {
        if ($a.($pair[0]) -isnot [string] -or $a.($pair[0]) -cne $fixed[$pair[1]]) { throw 'Authorization dependency path differs.' }
    }
    foreach ($field in @('lease_sha256', 'commit_sha256', 'live_sha256', 'start_fence_sha256', 'start_attempt_sha256', 'start_completion_sha256')) {
        if ($a.$field -isnot [string] -or -not [regex]::IsMatch($a.$field, '\A[0-9a-f]{64}\z')) { throw 'Authorization dependency hash is invalid.' }
    }
    if ($a.live_path -isnot [string]) { throw 'Live dependency path must be a native string.' }
    $fixed.live = $a.live_path
    $lease = Read-ParentTransitionEvidence lease $fixed.lease $a.lease_sha256
    if ($lease.value.artifacts.prestart_path -isnot [string] -or $lease.value.artifacts.prestart_path -cne $fixed.prestart -or
        $lease.value.artifacts.prestart_sha256 -isnot [string]) { throw 'Lease PreStart dependency differs.' }
    $packet = [ordered]@{
        authorization = $authorization; terminal = $terminal; lease = $lease
        commit = Read-ParentTransitionEvidence commit $fixed.commit $a.commit_sha256
        live = Read-ParentTransitionEvidence live $fixed.live $a.live_sha256
        prestart = Read-ParentTransitionEvidence prestart $fixed.prestart $lease.value.artifacts.prestart_sha256
        fence = Read-ParentTransitionEvidence fence $fixed.fence $a.start_fence_sha256 8192
        attempt = Read-ParentTransitionEvidence attempt $fixed.attempt $a.start_attempt_sha256 65536
        completion = Read-ParentTransitionEvidence completion $fixed.completion $a.start_completion_sha256 65536
    }
    $repair = $packet.prestart.value.payload.network_repair
    if ($repair.path -isnot [string] -or $repair.sha256 -isnot [string]) { throw 'Repair dependency must name native path/hash strings.' }
    $fixed.repair = $repair.path
    $packet.repair = Read-ParentTransitionEvidence repair $fixed.repair $repair.sha256
    foreach ($name in @('prestart', 'live', 'repair')) {
        $envelope = $packet[$name].value
        if ($envelope.payload_sha256 -isnot [string]) { throw 'Envelope payload hash must be a native string.' }
        # Match each producer's exact serialization contract; repair uses 30.
        $payloadDepth = if ($name -ceq 'repair') { 30 } else { 20 }
        $payloadText = $envelope.payload | ConvertTo-Json -Depth $payloadDepth -Compress
        if ([Stratus.ParentStage.Files]::Hash([Stratus.ParentStage.Files]::Utf8.GetBytes($payloadText)) -cne $envelope.payload_sha256) {
            throw 'Held envelope payload serialization/hash differs.'
        }
    }
    $terminalBytes = Read-ParentTransitionEvidenceBytes $script:transitionEvidence.terminal
    $terminalText = [Stratus.ParentStage.Files]::Utf8.GetString($terminalBytes).Replace("`r`n", "`n") + "`n"
    if ($terminalText.Contains("`r")) { throw 'Terminal receipt has noncanonical carriage returns.' }
    $terminalStdout = [Stratus.ParentStage.Files]::Utf8.GetBytes($terminalText)
    Assert-ParentTransitionEvidenceHeld
    Assert-ParentHeartbeat
    return [pscustomobject]@{ packet = [pscustomobject]$packet; paths = [pscustomobject]$fixed
        registration = $registration; terminal_stdout = [pscustomobject]@{
            sha256 = [Stratus.ParentStage.Files]::Hash($terminalStdout); bytes = [long]$terminalStdout.LongLength } }
}

function Assert-ParentTransitionRegistration {
    param([object] $Registration)
    $bundle = Get-ParentTransitionPacket $Registration
    $engine = Assert-ParentEngine -AsTransitionBoundary
    $postgres = Get-ParentContainer postgres
    if (-not $postgres.State.Running -or $postgres.HostConfig.RestartPolicy.Name -cne 'no') {
        throw 'Transition registration requires the exact running restart-disabled PostgreSQL.'
    }
    [void](Assert-ParentBootstrapHeldFile $script:hbaLock $contract.hba.path $contract.hba.sha256)
    [void](Assert-ParentBootstrapHeldFile $script:hostLock $contract.watchdog.host_path $contract.watchdog.host_sha256)
    $source = $script:r1Proof.source
    $programPath = 'scripts/Invoke-StratusSuccessorTransitionWatchdog.ps1'
    $gatePath = 'scripts/Assert-StratusPostMigrationSuccessorGate.ps1'
    $binding = [pscustomobject]@{
        format = 'stratus-parent-stage-binding-v1'; session_id = $contract.session_id
        contract_path = $ContractPath; contract_sha256 = $ContractSha256; lease_path = $paths.lease; lease_sha256 = $script:leaseHash
        ready_path = $paths.ready; ready_sha256 = $observed.ready.sha256; heartbeat_path = $paths.heartbeat
        mutation_fence_path = $paths.mutation_fence; mutation_fence_sha256 = $fenceHash
        watchdog = [pscustomobject]@{ pid = $PID; start_time_utc = $watchdogIdentity.start_time_utc
            executable = $watchdogIdentity.executable; sha256 = $contract.watchdog.host_sha256 }
        owner = $contract.owner; deadline_utc = $contract.deadline_utc; r1_anchor = $contract.r1
        source_manifest_sha256 = $script:r1Proof.acceptance.candidate.source_aggregate_sha256
    }
    $expected = [pscustomobject]@{
        root = $contract.root; parent_stage = $binding; parent_armed_at = $leaseTime.ToString('o')
        observed_at = [DateTimeOffset]::UtcNow.ToString('o'); revision = $contract.revision
        candidate_image = $contract.api.config_image; candidate_image_id = $contract.api.image_id
        postgres = [pscustomobject]@{ container_id = $postgres.Id; image_id = $postgres.Image; config_image = $postgres.Config.Image
            restart_count = $postgres.RestartCount; started_at = $postgres.State.StartedAt }
        docker_engine = $engine; program = [pscustomobject]@{ script_path = Join-Path $contract.root $programPath.Replace('/', '\')
            script_sha256 = $source[$programPath].sha256; script_bytes = $source[$programPath].bytes
            host_path = $contract.watchdog.host_path; host_sha256 = $contract.watchdog.host_sha256; host_bytes = [long]$script:hostLock.Length }
        gate_program = [pscustomobject]@{ script_path = Join-Path $contract.root $gatePath.Replace('/', '\')
            script_sha256 = $source[$gatePath].sha256; script_bytes = $source[$gatePath].bytes }
        paths = $bundle.paths; terminal_stdout = $bundle.terminal_stdout
        registration = [pscustomobject]@{ parent_authorization_sha256 = $bundle.registration.value.parent_authorization_sha256
            transition_terminal_sha256 = $bundle.registration.value.transition_terminal_sha256
            postgres_started_at = $bundle.registration.value.postgres_started_at }
        hba = [pscustomobject]@{ path = $contract.hba.path; sha256 = $contract.hba.sha256; bytes = [long]$script:hbaLock.Length }
        historical_inputs = [pscustomobject]@{
            backup_sha256 = $script:r1Proof.historical_inputs.backup_sha256
            lineage_sha256 = $script:r1Proof.historical_inputs.lineage_sha256
        }
    }
    $projection = Assert-StratusParentTransitionClosure -Packet $bundle.packet -Expected $expected
    Assert-ParentTransitionEvidenceHeld
    Assert-ParentHeartbeat
    # Keep received/consistent evidence separate from accepted native completion.
    # Neither this projection nor self-declared exit fields populate the cache.
    $script:transitionClosure = $projection
    throw 'Transition document closure is integrated but execution provenance is unqualified; downstream ACK remains blocked.'
}

function Read-ParentCanonicalManifest {
    param([byte[]] $Bytes, [switch] $SourcePaths)
    $text = [Stratus.ParentStage.Files]::Utf8.GetString($Bytes)
    if (-not $text.EndsWith("`n", [StringComparison]::Ordinal) -or $text.Contains("`r") -or
        $text.StartsWith([string][char]0xfeff, [StringComparison]::Ordinal)) {
        throw 'Manifest must use canonical UTF-8 without BOM and terminal LF.'
    }
    $entries = [Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal)
    $unique = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $last = $null
    foreach ($line in $text.Substring(0, $text.Length - 1).Split("`n")) {
        $fields = $line.Split("`t")
        if ($fields.Count -ne 3 -or $fields[1] -cnotmatch '^(0|[1-9][0-9]*)$' -or
            $fields[2] -cnotmatch '^[0-9a-f]{64}$' -or -not $unique.Add($fields[0])) {
            throw 'Manifest record is malformed or aliased.'
        }
        $name = $fields[0]
        if ($name -cnotmatch '^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$' -or
            $name.Split('/') -ccontains '..' -or $name.Split('/') -ccontains '.' -or
            (-not $SourcePaths -and $name.Contains('/')) -or
            ($null -ne $last -and [StringComparer]::Ordinal.Compare($last, $name) -ge 0)) {
            throw 'Manifest path/order is not canonical.'
        }
        $entries.Add($name, [pscustomobject]@{ bytes = [long]$fields[1]; sha256 = $fields[2] })
        $last = $name
    }
    return ,$entries
}

function Get-ParentR1Utc {
    param([object] $Value)
    if ($Value -isnot [string]) { throw 'R1 UTC timestamp must be a native string.' }
    $instant = [DateTime]::ParseExact($Value, 'o', [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::RoundtripKind)
    if ($instant.Kind -ne [DateTimeKind]::Utc -or $instant.ToString('o') -cne $Value) {
        throw 'R1 UTC timestamp must use its canonical seven-fraction Z format.'
    }
    return [DateTimeOffset]::new($instant)
}

function Assert-ParentR1Anchor {
    param([object] $Binding)
    Assert-ParentProperties $Binding @('directory', 'acceptance_sha256', 'evidence_manifest_sha256', 'sealed_sha256') 'R1 anchor'
    $expectedDirectory = Join-Path $contract.root ('.stratus-cache\release-transitions\r1-' + $contract.revision + '.accepted')
    if ($Binding.directory -cne $expectedDirectory) { throw 'Parent R1 anchor is not the exact accepted revision directory.' }
    [void](Assert-ParentRegularAncestry $expectedDirectory)
    $manifestBytes = Read-ParentHeldBytes (Join-Path $expectedDirectory 'evidence-manifest.tsv') $Binding.evidence_manifest_sha256
    $entries = Read-ParentCanonicalManifest $manifestBytes
    $names = @('acceptance.json', 'build-command.json', 'build-history.json', 'ci-evidence.json',
        'git-evidence.json', 'historical-0047-inputs.json', 'image-inspect.json', 'input-manifest.tsv',
        'pre-start-topology.json', 'release-guard.json', 'source-manifest.tsv', 'source-summary.json', 'successor-reservation.json')
    [Array]::Sort($names, [StringComparer]::Ordinal)
    $manifestNames = [string[]]@($entries.Keys)
    [Array]::Sort($manifestNames, [StringComparer]::Ordinal)
    if (($manifestNames -join "`n") -cne ($names -join "`n")) { throw 'R1 artifact manifest inventory differs.' }
    $publishedNames = [string[]]@(Get-ChildItem -LiteralPath $expectedDirectory -Force -ErrorAction Stop | ForEach-Object { $_.Name })
    $expectedNames = [string[]]@($names + @('SEALED.json', 'evidence-manifest.tsv'))
    [Array]::Sort($publishedNames, [StringComparer]::Ordinal); [Array]::Sort($expectedNames, [StringComparer]::Ordinal)
    if (($publishedNames -join "`n") -cne ($expectedNames -join "`n")) { throw 'Accepted R1 directory has extra or missing evidence.' }
    $documents = @{}
    foreach ($name in $names) {
        $bytes = Read-ParentHeldBytes (Join-Path $expectedDirectory $name) $entries[$name].sha256
        if ($bytes.LongLength -ne $entries[$name].bytes) { throw 'R1 artifact byte length differs.' }
        $documents[$name] = $bytes
    }
    $sealed = ConvertFrom-ParentJson (Read-ParentHeldBytes (Join-Path $expectedDirectory 'SEALED.json') $Binding.sealed_sha256)
    Assert-ParentProperties $sealed @('format', 'record_id', 'sealed_at_utc', 'revision', 'acceptance_sha256',
        'evidence_manifest_sha256', 'input_manifest_sha256', 'artifact_count', 'state', 'section3_authorized',
        'requires_parent_stage_watchdog_acceptance') 'R1 seal'
    if ($sealed.format -cne 'stratus-r0-r1-seal-v3' -or $sealed.revision -cne $contract.revision -or
        $sealed.acceptance_sha256 -cne $Binding.acceptance_sha256 -or
        $sealed.acceptance_sha256 -cne $entries['acceptance.json'].sha256 -or
        $sealed.evidence_manifest_sha256 -cne $Binding.evidence_manifest_sha256 -or
        $sealed.input_manifest_sha256 -cne $entries['input-manifest.tsv'].sha256 -or
        -not (Test-ParentInt32 $sealed.artifact_count) -or $sealed.artifact_count -ne $names.Count -or
        $sealed.state -cne 'ACCEPTED_R1_PREREQUISITE_PARENT_STAGE_PENDING' -or
        $sealed.section3_authorized -isnot [bool] -or $sealed.section3_authorized -or
        $sealed.requires_parent_stage_watchdog_acceptance -isnot [bool] -or -not $sealed.requires_parent_stage_watchdog_acceptance) {
        throw 'R1 seal is not the exact non-authorizing accepted prerequisite.'
    }
    $accepted = ConvertFrom-ParentJson $documents['acceptance.json']
    Assert-ParentProperties $accepted @('format', 'record_id', 'recorded_at_utc', 'production_root', 'candidate',
        'remote_ci', 'historical_0047', 'successor_attempt', 'pre_start', 'decisions', 'authorization', 'artifacts') 'R1 acceptance'
    $candidate = $accepted.candidate
    Assert-ParentProperties $candidate @('revision', 'tree', 'source_path_count', 'source_aggregate_sha256', 'build_worktree',
        'detached', 'clean', 'image', 'image_id', 'release_digest', 'platform') 'R1 candidate'
    if ($accepted.format -cne 'stratus-r0-r1-acceptance-v3' -or $accepted.record_id -cne $sealed.record_id -or
        $accepted.production_root -cne $contract.root -or $candidate.revision -cne $contract.revision -or
        $candidate.image_id -cne $contract.api.image_id -or $candidate.image -cne $contract.api.config_image -or
        $candidate.release_digest -cne $contract.api.image_id.Substring(7) -or $candidate.platform -cne 'linux/amd64' -or
        $candidate.detached -isnot [bool] -or -not $candidate.detached -or $candidate.clean -isnot [bool] -or -not $candidate.clean -or
        $candidate.source_aggregate_sha256 -cne $entries['source-manifest.tsv'].sha256 -or
        $accepted.pre_start.topology_sha256 -cne $entries['pre-start-topology.json'].sha256 -or
        $accepted.successor_attempt.reservation_sha256 -cne $entries['successor-reservation.json'].sha256 -or
        $accepted.remote_ci.evidence_sha256 -cne $entries['ci-evidence.json'].sha256 -or
        $accepted.historical_0047.evidence_sha256 -cne $entries['historical-0047-inputs.json'].sha256 -or
        $accepted.decisions.R0 -cne 'ACCEPT' -or $accepted.decisions.R1 -cne 'ACCEPT' -or
        $accepted.decisions.immutable_image_load_gate_transition -cne 'RECORDED' -or
        $accepted.decisions.section3_authorized -isnot [bool] -or $accepted.decisions.section3_authorized -or
        $accepted.authorization.scope -cne 'PAPER_ONLY') { throw 'Parent runtime candidate lacks exact accepted R1 lineage.' }
    foreach ($property in $accepted.authorization.PSObject.Properties) {
        if ($property.Name -ceq 'scope') { continue }
        if ($property.Value -isnot [bool] -or $property.Value) { throw 'R1 prerequisite granted unexpected authority.' }
    }
    $recorded = Get-ParentR1Utc $accepted.recorded_at_utc
    $sealedAt = Get-ParentR1Utc $sealed.sealed_at_utc
    if ($recorded -gt $sealedAt -or $sealedAt -gt [DateTimeOffset]::UtcNow.AddSeconds(2)) { throw 'R1 publication chronology differs.' }
    $reservation = ConvertFrom-ParentJson $documents['successor-reservation.json']
    if ($reservation.format -cne 'stratus-r1-successor-reservation-v3' -or $reservation.record_id -cne $sealed.record_id -or
        $reservation.revision -cne $contract.revision -or $reservation.attempt_id -cne $contract.session_id -or
        $reservation.evidence_directory -cne $accepted.successor_attempt.evidence_directory -or
        -not $ContractPath.StartsWith($reservation.evidence_directory + '\', [StringComparison]::Ordinal) -or
        $reservation.reuse_after_failure -isnot [bool] -or $reservation.reuse_after_failure) {
        throw 'Parent lease is outside its accepted fresh successor reservation.'
    }
    $source = Read-ParentCanonicalManifest $documents['source-manifest.tsv'] -SourcePaths
    if (-not (Test-ParentInt32 $candidate.source_path_count) -or $source.Count -ne $candidate.source_path_count) { throw 'R1 source manifest count differs.' }
    foreach ($path in @('docker-compose.yml', 'scripts/Invoke-StratusParentStageWatchdog.ps1',
        'scripts/Stratus.ParentStage.Runtime.cs', 'scripts/Assert-StratusPostMigrationSuccessorGate.ps1',
        'scripts/Stratus.ParentTransitionLineage.ps1', 'scripts/Stratus.ParentTransitionClosure.ps1',
        'scripts/Invoke-StratusSuccessorTransitionWatchdog.ps1', 'scripts/Invoke-StratusStoppedPostgresNetworkRepair.ps1',
        'scripts/Invoke-StratusRuntimeLoadObserver.ps1', 'scripts/Invoke-StratusDashboardLoadGate.ps1',
        'scripts/Assert-StratusLoadGateArtifacts.ps1')) {
        if (-not $source.ContainsKey($path)) { throw 'R1 source manifest lacks a required release program.' }
        $bytes = Read-ParentHeldBytes (Join-Path $contract.root $path.Replace('/', '\')) $source[$path].sha256 67108864
        if ($bytes.LongLength -ne $source[$path].bytes) { throw 'R1 release program length differs.' }
    }
    return [pscustomobject]@{ acceptance = $accepted; source = $source
        topology = (ConvertFrom-ParentJson $documents['pre-start-topology.json']); reservation = $reservation
        historical_inputs = (ConvertFrom-ParentJson $documents['historical-0047-inputs.json']) }
}

function New-ParentContainmentProgress {
    $progress = @{}
    foreach ($kind in @('provision', 'api', 'postgres')) {
        $progress[$kind] = @{ materialized = $false; stopped = $false; result = $null; attempt_seen = $false; start_seen = $false }
    }
    return $progress
}

function Test-ParentValidatedCompletionUnchanged {
    param([ValidateSet('postgres', 'provision', 'api')][string] $Kind)
    # Only a completed lineage validation can populate this cache. Mere file
    # presence, JSON parse success or a self-declared exit code cannot resolve a
    # daemon request whose lifecycle is otherwise still ambiguous.
    if (-not $script:validatedCompletions.ContainsKey($Kind)) { return $false }
    $proof = $script:validatedCompletions[$Kind]
    try {
        $attempt = Read-ParentDocument $proof.attempt_path
        $complete = Read-ParentDocument $proof.completion_path
        if ($attempt.sha256 -cne $proof.attempt_sha256 -or $complete.sha256 -cne $proof.completion_sha256) {
            throw 'Validated native completion lineage changed.'
        }
        return $true
    }
    catch { Add-ParentFailure ($Kind + ' completion remains unresolved: ' + $_.Exception.Message); return $false }
}

function Invoke-ParentContainerContainmentRound {
    param([Collections.IDictionary] $Progress)
    # Each object receives exactly one bounded attempt per round. An ambiguous or
    # unreachable provision object cannot starve API/PostgreSQL containment. Keep
    # materialization history across rounds and publication failures: absence or a
    # stopped snapshot alone cannot finish an unacknowledged daemon start request.
    foreach ($kind in @('provision', 'api', 'postgres')) {
        $state = $Progress[$kind]
        $state.stopped = $false
        $state.result = $null
        try {
            $attempted = if ($kind -ceq 'postgres') { Test-Path -LiteralPath $paths.transition_start_attempt }
                else { Test-Path -LiteralPath $paths.($kind + '_attempt') }
            $completePath = if ($kind -ceq 'postgres') { $paths.transition_start_completion }
                elseif ($kind -ceq 'api') { $paths.api_start_complete } else { $paths.provision_complete }
            $startSeen = if ($kind -ceq 'api') { Test-Path -LiteralPath $paths.api_start_attempt } else { $attempted }
            $state.attempt_seen = $state.attempt_seen -or $attempted -or $startSeen -or (Test-Path -LiteralPath $completePath)
            $state.start_seen = $state.start_seen -or $startSeen -or (Test-Path -LiteralPath $completePath)
            $attempted = $state.attempt_seen
            $unresolvedStart = $state.start_seen -and -not (Test-ParentValidatedCompletionUnchanged $kind)
            Assert-ParentEngine
            $candidate = Get-ParentContainmentContainer $kind -AllowAbsent:($kind -cne 'postgres')
            if ($null -eq $candidate) {
                if (-not $attempted) { $state.stopped = $true; $state.result = 'never_attempted_absent' }
            }
            else {
                if ($candidate.State.Running -or $candidate.State.Pid -gt 0 -or $candidate.State.Status -ceq 'restarting') { $state.materialized = $true }
                if (-not $candidate.State.Running -and $candidate.State.Pid -eq 0 -and
                    $candidate.State.Status -cin @('created', 'exited') -and -not $unresolvedStart) {
                    $state.stopped = $true; $state.result = 'exact_stopped'
                }
                elseif ($state.materialized) {
                    # Materialization is not causal STOP evidence. Even if the
                    # first snapshot is now stopped, an unresolved start needs
                    # an exact successful STOP followed by fresh stopped proof.
                    # Do not cache this round's success as daemon-queue closure.
                    $id = [string]$candidate.Id
                    $stop = Invoke-ParentDocker -Arguments @('stop', '--timeout', '15', $id) -TimeoutMilliseconds 25000
                    if ($stop -cne $id) { throw 'Stop returned another container identity.' }
                    $proof = Get-ParentContainmentContainer $kind
                    if ($proof.Id -cne $id -or $proof.State.Running -or $proof.State.Pid -ne 0 -or
                        $proof.State.Status -cnotin @('created', 'exited')) {
                        throw 'Exact causal stopped proof failed.'
                    }
                    $state.stopped = $true; $state.result = 'causal_exact_stop'
                }
            }
        }
        catch { Add-ParentFailure ($kind + ' containment: ' + $_.Exception.Message) }
    }
    return ($Progress.provision.stopped -and $Progress.api.stopped -and $Progress.postgres.stopped)
}

function Invoke-ParentContainment {
    param([string] $Trigger)
    # A live parent must be terminated by exact held process identity before
    # transition revocation or container containment. The loss detector may already
    # hold the recovered mutation fence; retain it while owner termination retries.
    # Merely seeing a stopped snapshot cannot revoke queued work.
    $ownerStopped = $false
    try {
        Stop-ParentExactProcess $script:ownerLease
        $ownerStopped = -not $script:ownerLease.IsAlive()
        if (-not $ownerStopped) { throw 'Exact owner process remains alive.' }
    }
    catch { Add-ParentFailure ('owner containment: ' + $_.Exception.Message) }
    if (-not $ownerStopped) { return $null }
    if ($null -eq $script:fenceLock) {
        try { $script:fenceLock = Open-ParentFenceOnce $paths.mutation_fence }
        catch { Add-ParentFailure ('parent fence: ' + $_.Exception.Message) }
        if ($null -eq $script:fenceLock) { return $null }
    }
    Enter-ParentTransitionEvidenceContainment
    if ($null -ne $script:transitionFenceCapture -or $null -ne $script:transitionFenceLock -or
        $script:transitionEvidence.ContainsKey('fence') -or (Test-Path -LiteralPath $paths.transition_start_fence)) {
        if ($null -eq $script:transitionFenceLock) {
            if ($null -ne $script:transitionFenceCapture -or $script:transitionEvidence.Count -ne 0) {
                throw 'Containment cannot reopen a lost captured Transition fence.'
            }
            try { $script:transitionFenceLock = Open-ParentFenceOnce $paths.transition_start_fence }
            catch { Add-ParentFailure ('transition fence: ' + $_.Exception.Message) }
            if ($null -eq $script:transitionFenceLock) { return $null }
        }
        Assert-ParentTransitionContainmentFence
    }
    if (-not (Test-Path -LiteralPath $paths.transition_start_revoke)) {
        [void](Write-ParentDocument $paths.transition_start_revoke ([ordered]@{
            format = 'stratus-parent-transition-revoke-v1'; session_id = $contract.session_id
            revoked_at = [DateTimeOffset]::UtcNow.ToString('o'); owner_pid = $contract.owner.pid
            mutation_fence_sha256 = $fenceHash; postgres_start_permitted = $false
        }))
    }
    $observerStopped = $false
    try { Stop-ParentOwnedObserver; $observerStopped = $true }
    catch { Add-ParentFailure ('observer containment: ' + $_.Exception.Message) }
    if ($null -eq $script:containmentProgress) { $script:containmentProgress = New-ParentContainmentProgress }
    $containersStopped = Invoke-ParentContainerContainmentRound $script:containmentProgress
    if (-not $containersStopped -or -not $observerStopped) { return $null }
    Assert-ParentTransitionCaptureContained
    $results = [ordered]@{}
    foreach ($kind in @('provision', 'api', 'postgres')) { $results[$kind] = $script:containmentProgress[$kind].result }
    Assert-ParentEngine
    $inventory = Invoke-ParentDocker @('ps', '--quiet', '--no-trunc')
    if (-not [string]::IsNullOrWhiteSpace($inventory)) { throw 'Containment proof found unexpected running containers.' }
    $containmentHash = Write-ParentRecoveryDocument 'containment' $paths.containment ([ordered]@{
        format = 'stratus-parent-containment-v1'; session_id = $contract.session_id
        lease_sha256 = $script:leaseHash; trigger = $Trigger; contained_at = [DateTimeOffset]::UtcNow.ToString('o')
        mutation_fence_held = $true; results = $results; errors = @($failures)
        authorization = Get-ParentNonAuthorization
    })
    return [ordered]@{ containment_path = $paths.containment; containment_sha256 = $containmentHash }
}

try {
    [void](Assert-ParentRegularAncestry $ContractPath)
    $runtimePath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'Stratus.ParentStage.Runtime.cs'))
    [void](Assert-ParentRegularAncestry $runtimePath)
    $script:runtimeLock = [IO.FileStream]::new($runtimePath, 'Open', 'Read', 'Read')
    $locks.Add($script:runtimeLock)
    $hash = [Security.Cryptography.SHA256]::Create()
    try { $runtimeActualHash = [BitConverter]::ToString($hash.ComputeHash($script:runtimeLock)).Replace('-', '').ToLowerInvariant() }
    finally { $hash.Dispose(); $script:runtimeLock.Position = 0 }
    if ($runtimeActualHash -cne $RuntimeSha256) { throw 'Native runtime source hash differs.' }
    Add-Type -Path $runtimePath -ErrorAction Stop
    $script:contractLock = [Stratus.ParentStage.Files]::Lock($ContractPath, $ContractSha256)
    $locks.Add($script:contractLock)
    $contract = (Read-ParentDocument $ContractPath).value
    Assert-ParentProperties $contract @('format', 'root', 'session_id', 'revision', 'owner', 'watchdog',
        'docker', 'hba', 'deadline_utc', 'heartbeat_ttl_seconds', 'artifacts',
        'postgres', 'provision', 'api', 'load_acceptance_path', 'r1') 'parent contract'
    if ($contract.format -cne 'stratus-parent-contract-v2' -or $contract.root -cne $approvedRoot -or
        $contract.session_id -notmatch '^[0-9a-f]{32}$' -or $contract.revision -notmatch '^[0-9a-f]{40}$' -or
        -not (Test-ParentInt32 $contract.heartbeat_ttl_seconds) -or $contract.heartbeat_ttl_seconds -lt 2 -or
        $contract.heartbeat_ttl_seconds -gt 30) { throw 'Parent contract identity/TTL differs.' }
    [void](Assert-ParentRegularAncestry $contract.root)
    Assert-ParentProperties $contract.owner @('pid', 'start_time_utc', 'executable', 'sha256') 'owner'
    if (-not (Test-ParentInt32 $contract.owner.pid) -or $contract.owner.pid -le 0 -or $contract.owner.pid -eq $PID) { throw 'Independent owner required.' }
    Assert-ParentProperties $contract.watchdog @('script_path', 'script_sha256', 'host_path', 'host_sha256', 'runtime_path', 'runtime_sha256') 'watchdog'
    Assert-ParentProperties $contract.docker @('executable', 'sha256') 'docker'
    Assert-ParentProperties $contract.hba @('path', 'sha256') 'hba'
    if ($contract.watchdog.script_path -cne $PSCommandPath -or $contract.watchdog.runtime_path -cne $runtimePath -or
        $contract.watchdog.runtime_sha256 -cne $RuntimeSha256 -or $contract.docker.sha256 -cne $approvedDockerHash -or
        $contract.hba.path -cne (Join-Path $contract.root 'ops\postgres\stratus-pg_hba.conf')) { throw 'Pinned program/HBA path differs.' }
    $watchdogIdentity = Get-ParentProcessIdentity $PID
    if ($watchdogIdentity.executable -cne $contract.watchdog.host_path) { throw 'Watchdog host executable differs.' }
    $script:watchdogScriptLock = [Stratus.ParentStage.Files]::Lock($contract.watchdog.script_path, $contract.watchdog.script_sha256)
    $locks.Add($script:watchdogScriptLock)
    $script:hostLock = [Stratus.ParentStage.Files]::Lock($contract.watchdog.host_path, $contract.watchdog.host_sha256)
    $locks.Add($script:hostLock)
    # The new bootstrap path always supplies both inherited endpoints. Clear
    # their inheritance before any Docker/R1/external child can be created.
    # No path/name/PID lookup or legacy transport fallback is permitted.
    if (-not $PSBoundParameters.ContainsKey('ControlInputHandle') -or
        -not $PSBoundParameters.ContainsKey('ControlOutputHandle') -or
        [string]::IsNullOrEmpty($ControlInputHandle) -or [string]::IsNullOrEmpty($ControlOutputHandle)) {
        throw 'Both private inherited control endpoints are required.'
    }
    $script:controlEndpoint = [Stratus.ParentStage.PrivateControlEndpoint]::Open(
        $ControlInputHandle, $ControlOutputHandle, $contract.session_id)
    $locks.Add($script:controlEndpoint) # Only eventual unarmed/proven-terminal cleanup may close it.
    $script:controlEndpoint.AssertHealthy()
    $script:hbaLock = [Stratus.ParentStage.Files]::Lock($contract.hba.path, $contract.hba.sha256)
    $locks.Add($script:hbaLock)
    $ownerExecutableLock = [Stratus.ParentStage.Files]::Lock($contract.owner.executable, $contract.owner.sha256)
    $locks.Add($ownerExecutableLock)
    $script:dockerLock = [Stratus.ParentStage.Files]::Lock($contract.docker.executable, $approvedDockerHash)
    $locks.Add($script:dockerLock)
    $script:ownerLease = [Stratus.ParentStage.ProcessLease]::Open($contract.owner.pid,
        $contract.owner.start_time_utc, $contract.owner.executable, $contract.owner.sha256, $ownerExecutableLock)
    $locks.Add($script:ownerLease)
    Assert-ParentOwner
    $script:r1Proof = Assert-ParentR1Anchor $contract.r1
    . (Get-ParentTransitionValidatorProgram)
    $deadline = Get-ParentUtc $contract.deadline_utc
    $maximumLifetime = $deadline.Subtract([DateTimeOffset]::UtcNow).TotalSeconds
    if ($maximumLifetime -lt 5 -or $maximumLifetime -gt 1800) { throw 'Parent lease deadline must be within 5..1800 seconds.' }
    Assert-ParentProperties $contract.artifacts $artifactNames 'artifact paths'
    $paths = $contract.artifacts
    $distinct = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($path in @($ContractPath, $contract.load_acceptance_path) + @($artifactNames | ForEach-Object { $paths.$_ })) {
        if ($path -isnot [string] -or -not $path.StartsWith($contract.root + '\', [StringComparison]::OrdinalIgnoreCase) -or
            -not $distinct.Add($path)) { throw 'Artifact path escaped root or aliases another protocol path.' }
        if ($path -cne $ContractPath) { [void](Assert-ParentRegularAncestry $path -Absent) }
    }
    foreach ($kind in @('postgres', 'api', 'provision')) {
        Assert-ParentProperties $contract.$kind @('name', 'image_id', 'config_image', 'service') $kind
        if ($contract.$kind.image_id -notmatch '^sha256:[0-9a-f]{64}$') {
            throw 'Container contracts require exact image hashes.'
        }
    }
    if ($contract.postgres.name -cne 'weatherpoly-postgres-1' -or $contract.postgres.service -cne 'postgres' -or
        $contract.api.name -cne 'weatherpoly-api-1' -or $contract.api.service -cne 'api' -or
        $contract.provision.name -cne ('stratus-runtime-provision-' + $contract.session_id) -or
        $contract.provision.service -cne 'provision-runtime-role' -or $contract.provision.image_id -cne $approvedPostgresImage -or
        $contract.provision.config_image -cne $approvedPostgresConfigImage) {
        throw 'Only retained PostgreSQL and predeclared PAPER API/provision names are in scope.'
    }
    Assert-ParentEngine
    Initialize-ParentIntendedServices
    $initial = Get-ParentContainer postgres
    if ($initial.State.Running) { throw 'Pre-arm requires exactly stopped retained PostgreSQL.' }
    $initialApi = Get-ParentPredecessorApi
    $script:predecessorApi = [pscustomobject]@{ id = $initialApi.Id; image_id = $initialApi.Image
        static_sha256 = (Get-ParentStaticBoundaryHash $initialApi) }
    if ($null -ne (Get-ParentContainer provision -AllowAbsent)) {
        throw 'Pre-arm requires an absent attempt provision object.'
    }
    if (-not [string]::IsNullOrWhiteSpace((Invoke-ParentDocker @('ps', '--quiet', '--no-trunc')))) { throw 'Pre-arm requires stopped runtime topology.' }
    $fenceHash = Write-ParentDocument $paths.mutation_fence ([ordered]@{
        format = 'stratus-parent-mutation-fence-v1'; session_id = $contract.session_id
        owner_pid = $contract.owner.pid; owner_start_time_utc = $contract.owner.start_time_utc
        contract_sha256 = $ContractSha256
    })
    $leaseTime = [DateTimeOffset]::UtcNow
    $script:leaseHash = Write-ParentDocument $paths.lease ([ordered]@{
        format = 'stratus-parent-lease-v2'; session_id = $contract.session_id
        contract_path = $ContractPath; contract_sha256 = $ContractSha256
        watchdog = $watchdogIdentity; owner = $contract.owner
        mutation_fence_path = $paths.mutation_fence; mutation_fence_sha256 = $fenceHash
        r1_anchor = $contract.r1; source_manifest_sha256 = $script:r1Proof.acceptance.candidate.source_aggregate_sha256
        armed_at = $leaseTime.ToString('o'); deadline_utc = $deadline.ToString('o')
        authorization = Get-ParentNonAuthorization
    })
    $script:armed = $true
    while (-not (Test-Path -LiteralPath $paths.parent_armed)) {
        Assert-ParentOwner
        if ([DateTimeOffset]::UtcNow -ge $leaseTime.AddSeconds($contract.heartbeat_ttl_seconds)) { throw 'Parent did not acquire its mutation fence.' }
        Start-Sleep -Milliseconds 100
    }
    $parentArmed = Get-ParentMessage parent_armed @('mutation_fence_sha256')
    if ($parentArmed.value.mutation_fence_sha256 -cne $fenceHash) { throw 'Parent armed acknowledgement differs.' }
    Assert-ParentMutationFenceHeld
    Assert-ParentHeartbeat
    Write-ParentAck ready $parentArmed ([ordered]@{ mutation_fence_sha256 = $fenceHash; postgres_stopped = $true
        r1_acceptance_sha256 = $contract.r1.acceptance_sha256; phase = 'transition' })
    Assert-ParentHeartbeat
    $bootstrapFrame = New-ParentBootstrapFrame $BootstrapNonce
    Assert-ParentHeartbeat
    Write-ParentBootstrapReady $bootstrapFrame
    while ($true) {
        Service-ParentTransitionControl
        Assert-ParentHeartbeat
        Assert-ParentTransitionEvidenceHeld
        if (-not (Test-Path -LiteralPath $paths.api_attempt)) { [void](Get-ParentPredecessorApi) }
        foreach ($name in @($observed.Keys)) {
            if ((Read-ParentDocument $paths.$name).sha256 -cne $observed[$name].sha256) { throw "Protocol artifact tampered: $name" }
        }
        $launchIntent = Get-ParentMessage transition_launch_intent @('transition_session_id','prestart_sha256',
            'prestart_payload_sha256','start_fence_sha256','hard_deadline_utc')
        if ($null -ne $launchIntent -and -not $script:transitionLaunchAttempted) { Start-ParentTransitionLaunch $launchIntent }
        Update-ParentTransitionLaunch
        $transition = Get-ParentMessage transition_registration @('parent_authorization_sha256', 'transition_terminal_sha256', 'postgres_started_at')
        if ($null -ne $transition) {
            Assert-ParentTransitionRegistration $transition
            # The integrated receiver remains non-authorizing until its complete
            # native/fault-matrix qualification removes the internal WIP barrier.
            Write-ParentAck transition_ack $transition ([ordered]@{ postgres_started_at = $transition.value.postgres_started_at })
        }
        foreach ($kind in @('provision', 'api')) {
            $intentFields = @('name', 'image_id')
            if ($kind -ceq 'api') { $intentFields += 'predecessor_container_id' }
            $intent = Get-ParentMessage ($kind + '_intent') $intentFields
            if ($null -eq $intent) { continue }
            if (-not $observed.ContainsKey('transition_ack') -or $intent.value.name -cne $contract.$kind.name -or
                $intent.value.image_id -cne $contract.$kind.image_id) {
                throw 'Object intent differs from the predeclared immutable contract.'
            }
            if ($kind -ceq 'api' -and $intent.value.predecessor_container_id -cne $script:predecessorApi.id) {
                throw 'API replacement intent is not bound to the exact stopped R1 predecessor.'
            }
            Write-ParentAck ($kind + '_intent_ack') $intent
            $attempt = Get-ParentMessage ($kind + '_attempt') @('intent_sha256')
            if ($null -ne $attempt -and $attempt.value.intent_sha256 -cne $intent.sha256) { throw 'Creation attempt lost intent binding.' }
            $registration = Get-ParentMessage ($kind + '_registration') @('intent_sha256', 'container_id')
            if ($null -ne $registration) {
                if ($null -eq $attempt) { throw 'Registration exists before creation attempt.' }
                $container = Get-ParentContainer $kind
                if ($registration.value.intent_sha256 -cne $intent.sha256 -or $registration.value.container_id -cne $container.Id) {
                    throw 'Full-ID registration differs from intent/object.'
                }
                if ($kind -ceq 'api' -and -not $observed.ContainsKey('api_start_ack') -and $container.State.Running) {
                    throw 'API started before watchdog registration/start acknowledgement.'
                }
                Write-ParentAck ($kind + '_registration_ack') $registration ([ordered]@{ container_id = $container.Id })
            }
        }
        $apiStart = Get-ParentMessage api_start_intent @('registration_sha256', 'container_id')
        if ($null -ne $apiStart) {
            if (-not $observed.ContainsKey('api_registration_ack') -or
                $apiStart.value.registration_sha256 -cne $observed.api_registration.sha256 -or
                $apiStart.value.container_id -cne $objectIds.api) { throw 'API start intent lacks exact full-ID registration.' }
            Write-ParentAck api_start_ack $apiStart
        }
        if (Test-Path -LiteralPath $paths.observer_registration) {
            throw 'External observer PID/Job registration is forbidden; watchdog-owned launch is required.'
        }
        $observerIntent = Get-ParentMessage observer_intent @('api_start_complete_sha256')
        if ($null -ne $observerIntent -and -not $script:observerLaunchAttempted) { Start-ParentOwnedObserver $observerIntent }
        Update-ParentOwnedObserver
        $handoff = Get-ParentMessage handoff_request @('load_acceptance_path', 'load_acceptance_sha256', 'postgres_started_at', 'api_id', 'api_started_at')
        if ($null -ne $handoff) {
            if (-not $observed.ContainsKey('observer_ack')) { throw 'Handoff predates accepted load observer.' }
            $boundary = Assert-ParentHandoff $handoff
            Write-ParentAck handoff_ack $handoff $boundary
            $commit = Get-ParentMessage handoff_commit @('request_sha256', 'ack_sha256')
            if ($null -ne $commit) {
                if ($commit.value.request_sha256 -cne $handoff.sha256 -or $commit.value.ack_sha256 -cne $observed.handoff_ack.sha256) {
                    throw 'Handoff commit is not two-party/hash-bound.'
                }
                $script:fenceLock = Open-ParentFenceOnce $paths.mutation_fence
                if ($null -ne $script:fenceLock) {
                    if ([Stratus.ParentStage.Files]::HashStream($script:fenceLock) -cne $fenceHash) { throw 'Handoff fence changed.' }
                    Assert-ParentHeartbeat
                    $boundary = Assert-ParentHandoff $handoff
                    Assert-ParentSuccessorSupervisor
                    [void](Write-ParentDocument $paths.terminal ([ordered]@{
                        format = 'stratus-parent-terminal-v1'; status = 'handoff_accepted'
                        session_id = $contract.session_id; lease_sha256 = $script:leaseHash
                        handoff_request_sha256 = $handoff.sha256; handoff_ack_sha256 = $observed.handoff_ack.sha256
                        handoff_commit_sha256 = $commit.sha256; accepted_at = [DateTimeOffset]::UtcNow.ToString('o')
                        mutation_fence_held = $true; boundary = $boundary; authorization = Get-ParentNonAuthorization
                    }))
                    $script:controlDrainEligible = $true
                    Complete-ParentControlTransport
                    $script:exitCode = 0
                    break
                }
            }
        }
        Start-Sleep -Milliseconds 200
    }
}
catch {
    $trigger = $_.Exception.Message
    Add-ParentFailure $trigger
    if ($script:armed) {
        # Internal failure or an unavailable daemon never disposes the armed HBA
        # lock/fence and exits with an unproved stopped state. Stay nonterminal.
        while ($true) {
            try {
                $containment = Invoke-ParentContainment -Trigger $trigger
                if ($null -eq $containment) { Start-Sleep -Milliseconds 200; continue }
                [void](Write-ParentRecoveryDocument 'contained_terminal' $paths.terminal ([ordered]@{
                    format = 'stratus-parent-terminal-v1'; status = 'contained'
                    session_id = $contract.session_id; lease_sha256 = $script:leaseHash
                    completed_at = [DateTimeOffset]::UtcNow.ToString('o'); containment = $containment
                    errors = @($failures); authorization = Get-ParentNonAuthorization
                }))
                $script:exitCode = 2
                break
            }
            catch { Add-ParentFailure ('containment could not complete: ' + $_.Exception.Message) }
            Start-Sleep -Milliseconds 200
        }
    }
}
finally {
    # Armed containment cannot reach here until StopAndProve succeeded. Do not
    # dispose the observer in the polling path or while containment is retrying.
    if ($null -ne $script:observerChild) {
        try { $script:observerChild.Dispose() } catch { $failures.Add('owned observer cleanup: ' + $_.Exception.Message) }
    }
    foreach ($item in @($script:transitionFenceLock, $script:fenceLock)) {
        if ($null -ne $item) { try { $item.Dispose() } catch { $failures.Add('fence cleanup: ' + $_.Exception.Message) } }
    }
    for ($index = $locks.Count - 1; $index -ge 0; $index--) {
        try { $locks[$index].Dispose() } catch { $failures.Add('held file cleanup: ' + $_.Exception.Message) }
    }
}
if ($failures.Count -ne 0) {
    [Console]::Error.WriteLine(($failures -join "`n"))
    if ($script:exitCode -eq 0) { $script:exitCode = 3 }
}
exit $script:exitCode
