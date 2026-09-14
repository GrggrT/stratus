[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string] $ContractPath,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{64}$')][string] $ContractSha256,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{64}$')][string] $RuntimeSha256,
    [Parameter(Mandatory = $true)][ValidateScript({ [regex]::IsMatch($_, '\A[0-9a-f]{64}\z') })][string] $BootstrapNonce
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
    'transition_start_completion', 'transition_terminal', 'parent_authorization'
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
$script:observer = $null
$script:ownerLease = $null
$script:observerLease = $null
$script:observerChild = $null
$script:observerChildResumed = $false
$script:observerResult = $null
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
                else { $primary = [AggregateException]::new($primary, $_.Exception) }
            }
        }
    }
    if ($null -ne $primary) { throw $primary }
    if ($missing) { return $null }
    return $result.Stdout.Trim()
}

function Assert-ParentEngine {
    $context = Invoke-ParentDocker @('context', 'inspect', 'desktop-linux', '--format', '{{json .}}')
    $value = ConvertFrom-ParentJson ([Stratus.ParentStage.Files]::Utf8.GetBytes($context))
    if ($value.Name -cne 'desktop-linux' -or
        $value.Endpoints.docker.Host -cne 'npipe:////./pipe/dockerDesktopLinuxEngine') {
        throw 'Pinned Docker endpoint drift.'
    }
    $info = Invoke-ParentDocker @('info', '--format', '{{json .}}')
    $engine = ConvertFrom-ParentJson ([Stratus.ParentStage.Files]::Utf8.GetBytes($info))
    if ($engine.ID -cne $approvedEngineId -or $engine.ServerVersion -cne '28.3.2' -or
        $engine.Name -cne 'docker-desktop' -or $engine.DockerRootDir -cne '/var/lib/docker' -or
        $engine.OSType -cne 'linux') { throw 'Pinned Docker engine drift.' }
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
    $locks.Add([Stratus.ParentStage.Files]::Lock($approvedComposePath, $approvedComposeHash))
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
    $published = Get-ParentUtc $value.published_at
    if ($value.format -cne ('stratus-parent-' + $Name.Replace('_', '-') + '-v1') -or
        $value.session_id -cne $contract.session_id -or $value.lease_sha256 -cne $script:leaseHash -or
        -not (Test-ParentInt32 $value.owner_pid) -or $value.owner_pid -ne $contract.owner.pid -or
        $value.owner_start_time_utc -cne $contract.owner.start_time_utc -or
        $published -gt [DateTimeOffset]::UtcNow.AddSeconds(2) -or $published -lt $leaseTime) {
        throw "Message is not bound to the live parent lease: $Name"
    }
    $observed[$Name] = $document
    return $document
}

function Write-ParentAck {
    param([string] $Name, [object] $Document, [object] $Extra = $null)
    if (Test-Path -LiteralPath $paths.$Name) {
        $existing = Read-ParentDocument $paths.$Name
        if (-not $observed.ContainsKey($Name) -or $existing.sha256 -cne $observed[$Name].sha256) {
            throw "Acknowledgement preexists or changed: $Name"
        }
        return
    }
    $ack = [ordered]@{
        format = 'stratus-parent-ack-v1'; session_id = $contract.session_id
        lease_sha256 = $script:leaseHash; acknowledged_at = [DateTimeOffset]::UtcNow.ToString('o')
        watchdog_pid = $PID; watchdog_start_time_utc = $watchdogIdentity.start_time_utc
        source_path = $Document.path; source_sha256 = $Document.sha256
        boundary = $Extra; authorization = Get-ParentNonAuthorization
    }
    $publishedHash = Write-ParentDocument $paths.$Name $ack
    $published = Read-ParentDocument $paths.$Name
    if ($published.sha256 -cne $publishedHash) { throw 'Acknowledgement readback differs from its own publication.' }
    $observed[$Name] = $published
}

function Assert-ParentHeartbeat {
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
        $source = Read-ParentDocument $path 1048576
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
    if ($null -ne $script:observer) {
        if ($null -eq $script:observerLease -or $script:observerLease.IsAlive()) {
            throw 'Load observer has not exited under the held exact process handle.'
        }
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

function Read-ParentHeldBytes {
    param([string] $Path, [string] $ExpectedSha256, [int] $MaximumBytes = 1048576)
    if ($ExpectedSha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'Held evidence requires an exact SHA-256.' }
    $held = [Stratus.ParentStage.Files]::Lock($Path, $ExpectedSha256)
    $locks.Add($held)
    $bytes = [Stratus.ParentStage.Files]::ReadRawBounded($Path, $MaximumBytes, $false)
    if ([Stratus.ParentStage.Files]::Hash($bytes) -cne $ExpectedSha256) { throw 'Held evidence bytes changed.' }
    return ,$bytes
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
        'scripts/Invoke-StratusSuccessorTransitionWatchdog.ps1', 'scripts/Invoke-StratusStoppedPostgresNetworkRepair.ps1',
        'scripts/Invoke-StratusRuntimeLoadObserver.ps1', 'scripts/Invoke-StratusDashboardLoadGate.ps1',
        'scripts/Assert-StratusLoadGateArtifacts.ps1')) {
        if (-not $source.ContainsKey($path)) { throw 'R1 source manifest lacks a required release program.' }
        $bytes = Read-ParentHeldBytes (Join-Path $contract.root $path.Replace('/', '\')) $source[$path].sha256 67108864
        if ($bytes.LongLength -ne $source[$path].bytes) { throw 'R1 release program length differs.' }
    }
    return [pscustomobject]@{ acceptance = $accepted; source = $source
        topology = (ConvertFrom-ParentJson $documents['pre-start-topology.json']); reservation = $reservation }
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
                    $candidate.State.Status -cin @('created', 'exited') -and (-not $unresolvedStart -or $state.materialized)) {
                    $state.stopped = $true; $state.result = 'exact_stopped'
                }
                elseif ($state.materialized) {
                    $id = [string]$candidate.Id
                    $stop = Invoke-ParentDocker -Arguments @('stop', '--timeout', '15', $id) -TimeoutMilliseconds 25000
                    if ($stop -cne $id) { throw 'Stop returned another container identity.' }
                    $proof = Get-ParentContainmentContainer $kind
                    if ($proof.State.Running -or $proof.State.Pid -ne 0 -or $proof.State.Status -cnotin @('created', 'exited')) {
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
    # A live parent is terminated by exact held process identity before recovering
    # the mutation fence. Merely seeing a stopped snapshot cannot revoke queued work.
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
    if ([Stratus.ParentStage.Files]::HashStream($script:fenceLock) -cne $fenceHash) { throw 'Recovered parent fence was tampered.' }
    if (Test-Path -LiteralPath $paths.transition_start_fence) {
        if ($null -eq $script:transitionFenceLock) {
            try { $script:transitionFenceLock = Open-ParentFenceOnce $paths.transition_start_fence }
            catch { Add-ParentFailure ('transition fence: ' + $_.Exception.Message) }
            if ($null -eq $script:transitionFenceLock) { return $null }
        }
    }
    if (-not (Test-Path -LiteralPath $paths.transition_start_revoke)) {
        [void](Write-ParentDocument $paths.transition_start_revoke ([ordered]@{
            format = 'stratus-parent-transition-revoke-v1'; session_id = $contract.session_id
            revoked_at = [DateTimeOffset]::UtcNow.ToString('o'); owner_pid = $contract.owner.pid
            mutation_fence_sha256 = $fenceHash; postgres_start_permitted = $false
        }))
    }
    $observerStopped = $null -eq $script:observer
    if ($null -ne $script:observer) {
        try {
            Stop-ParentExactProcess $script:observerLease
            $observerStopped = -not $script:observerLease.IsAlive()
            if (-not $observerStopped) { throw 'Exact observer process remains alive.' }
        }
        catch { Add-ParentFailure ('observer containment: ' + $_.Exception.Message) }
    }
    if ($null -eq $script:containmentProgress) { $script:containmentProgress = New-ParentContainmentProgress }
    $containersStopped = Invoke-ParentContainerContainmentRound $script:containmentProgress
    if (-not $containersStopped -or -not $observerStopped) { return $null }
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
    $locks.Add([Stratus.ParentStage.Files]::Lock($contract.hba.path, $contract.hba.sha256))
    $ownerExecutableLock = [Stratus.ParentStage.Files]::Lock($contract.owner.executable, $contract.owner.sha256)
    $locks.Add($ownerExecutableLock)
    $script:dockerLock = [Stratus.ParentStage.Files]::Lock($contract.docker.executable, $approvedDockerHash)
    $locks.Add($script:dockerLock)
    $script:ownerLease = [Stratus.ParentStage.ProcessLease]::Open($contract.owner.pid,
        $contract.owner.start_time_utc, $contract.owner.executable, $contract.owner.sha256, $ownerExecutableLock)
    $locks.Add($script:ownerLease)
    Assert-ParentOwner
    $script:r1Proof = Assert-ParentR1Anchor $contract.r1
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
    $probe = Open-ParentFenceOnce $paths.mutation_fence
    if ($null -ne $probe) { $probe.Dispose(); throw 'Parent mutation fence is not held exclusively.' }
    Assert-ParentHeartbeat
    Write-ParentAck ready $parentArmed ([ordered]@{ mutation_fence_sha256 = $fenceHash; postgres_stopped = $true
        r1_acceptance_sha256 = $contract.r1.acceptance_sha256; phase = 'transition' })
    Assert-ParentHeartbeat
    $bootstrapFrame = New-ParentBootstrapFrame $BootstrapNonce
    Assert-ParentHeartbeat
    Write-ParentBootstrapReady $bootstrapFrame
    while ($true) {
        Assert-ParentHeartbeat
        if (-not (Test-Path -LiteralPath $paths.api_attempt)) { [void](Get-ParentPredecessorApi) }
        foreach ($name in @($observed.Keys)) {
            if ((Read-ParentDocument $paths.$name).sha256 -cne $observed[$name].sha256) { throw "Protocol artifact tampered: $name" }
        }
        $transition = Get-ParentMessage transition_registration @('parent_authorization_sha256', 'transition_terminal_sha256', 'postgres_started_at')
        if ($null -ne $transition) {
            $authorization = Read-ParentDocument $paths.parent_authorization 1048576
            $terminal = Read-ParentDocument $paths.transition_terminal 1048576
            $postgres = Get-ParentContainer postgres
            if ($authorization.sha256 -cne $transition.value.parent_authorization_sha256 -or
                $terminal.sha256 -cne $transition.value.transition_terminal_sha256 -or
                $authorization.value.format -cne 'stratus-successor-transition-parent-authorization-v1' -or
                $authorization.value.owner_pid -ne $contract.owner.pid -or
                $authorization.value.owner_start_time_utc -cne $contract.owner.start_time_utc -or
                $authorization.value.postgres_started_at -cne $postgres.State.StartedAt -or
                $postgres.State.StartedAt -cne $transition.value.postgres_started_at -or
                -not $postgres.State.Running -or $postgres.HostConfig.RestartPolicy.Name -cne 'no') {
                throw 'Transition registration lacks exact live parent authorization.'
            }
            Write-ParentAck transition_ack $transition ([ordered]@{ postgres_started_at = $postgres.State.StartedAt })
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
        $observer = Get-ParentMessage observer_registration @('pid', 'start_time_utc', 'executable', 'sha256', 'job_assigned', 'suspended')
        if ($null -ne $observer -and $null -eq $script:observer) {
            if (-not $observed.ContainsKey('api_start_ack') -or $observer.value.job_assigned -isnot [bool] -or
                -not $observer.value.job_assigned -or $observer.value.suspended -isnot [bool] -or -not $observer.value.suspended) {
                throw 'Observer must be Job-contained and suspended before registration acknowledgement.'
            }
            if (-not (Test-ParentInt32 $observer.value.pid) -or $observer.value.pid -le 0 -or
                $observer.value.start_time_utc -isnot [string] -or $observer.value.executable -isnot [string] -or
                $observer.value.sha256 -isnot [string] -or $observer.value.sha256 -notmatch '^[0-9a-f]{64}$') {
                throw 'Observer process identity must use exact native JSON types.'
            }
            $identity = [pscustomobject]@{ pid = $observer.value.pid
                start_time_utc = $observer.value.start_time_utc; executable = $observer.value.executable }
            $observerExecutableLock = [Stratus.ParentStage.Files]::Lock($identity.executable, $observer.value.sha256)
            $locks.Add($observerExecutableLock)
            $script:observerLease = [Stratus.ParentStage.ProcessLease]::Open($identity.pid,
                $identity.start_time_utc, $identity.executable, $observer.value.sha256, $observerExecutableLock)
            $locks.Add($script:observerLease)
            $script:observer = $identity
            Write-ParentAck observer_ack $observer $identity
        }
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
