[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptPath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot `
    '..\Invoke-StratusSuccessorTransitionWatchdog.ps1'))
$source = [IO.File]::ReadAllText($scriptPath)
$tokens = $null
$parseErrors = $null
[void][Management.Automation.Language.Parser]::ParseFile(
    $scriptPath, [ref]$tokens, [ref]$parseErrors
)
if ($parseErrors.Count -ne 0) {
    throw "Watchdog script does not parse: $($parseErrors[0].Message)"
}
foreach ($required in @(
    'stratus-successor-transition-watchdog-lease-v3',
    'stratus-successor-transition-start-fence-v1',
    'stratus-successor-transition-start-revoked-v1',
    'stratus-successor-transition-start-attempt-v1',
    'stratus-successor-transition-start-complete-v1',
    'stratus-successor-transition-watchdog-heartbeat-v1',
    'stratus-successor-transition-watchdog-commit-v1',
    'stratus-successor-transition-watchdog-terminal-v1',
    'verified_configured_pending_existing',
    'configured-pending',
    '388f231898cff8674a2aa341e306b77fd1f93a5954dd33ff556575a69227824d',
    "'stop', '--timeout'", 'FileMode]::CreateNew', 'WaitForExit($milliseconds)',
    "@('--context', `$expectedDockerContext)",
    'Watchdog failed without masking earlier state.',
    'exact child remained live after the bounded kill wait'
)) {
    if (-not $source.Contains($required)) {
        throw "Watchdog source is missing required fail-closed marker: $required"
    }
}
foreach ($forbidden in @(
    "-Arguments @('start'", "'restart',", "'rm',", "'network', 'connect'",
    "'network', 'disconnect'", 'Start-Process', 'Invoke-Expression'
)) {
    if ($source.Contains($forbidden)) {
        throw "Watchdog source contains forbidden mutation path: $forbidden"
    }
}

$postgresId =
    '6ce82603e44abb95be94dbe95830af2d00c0c36ca3fe8a122b798087ecc1b299'
$postgresImage = (
    'postgres:16-alpine@sha256:' +
    '20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'
)
$postgresImageId =
    'sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'
$defaultNetworkId =
    '77551e8d1119201f778fdb90e151ec26f1d9cc23832ba7b88c4db6203e3d8ba2'
$safetyNetworkId =
    '0d1ad122718694dbc5e6714f69a601a7d429e23a0b74eecdb10ace944e5c8620'
$utf8 = [Text.UTF8Encoding]::new($false)
$temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) (
    'stratus-watchdog-test-' + [Guid]::NewGuid().ToString('N')
)
$fakeBin = Join-Path $temporaryRoot 'bin'
$fixtureData = Join-Path $temporaryRoot 'fixtures'
[void](New-Item -ItemType Directory -Path $fakeBin, $fixtureData -Force)

function Get-Sha256Text {
    param([Parameter(Mandatory = $true)][string] $Value)

    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($algorithm.ComputeHash(
            $utf8.GetBytes($Value)
        )).Replace('-', '').ToLowerInvariant()
    }
    finally { $algorithm.Dispose() }
}

function ConvertFrom-TestJsonPreservingDates {
    param([Parameter(Mandatory = $true)][string] $Json)

    $parameters = @{ InputObject = $Json }
    $cmdlet = Get-Command -Name 'Microsoft.PowerShell.Utility\ConvertFrom-Json' `
        -CommandType Cmdlet -ErrorAction Stop
    if ($cmdlet.Parameters.ContainsKey('DateKind')) {
        $parameters['DateKind'] = 'String'
    }
    return Microsoft.PowerShell.Utility\ConvertFrom-Json @parameters
}

function Get-FileSha256 {
    param([Parameter(Mandatory = $true)][string] $Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Write-JsonCreateNew {
    param(
        [Parameter(Mandatory = $true)][string] $Path,
        [object] $Value,
        [ValidateRange(0, 5000)][int] $HoldOpenMilliseconds = 0
    )

    $json = $Value | ConvertTo-Json -Depth 30
    $stream = [IO.FileStream]::new(
        $Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write,
        [IO.FileShare]::None, 4096, [IO.FileOptions]::WriteThrough
    )
    try {
        $bytes = $utf8.GetBytes($json)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
        if ($HoldOpenMilliseconds -gt 0) {
            # Exercise the real CreateNew publication window: the path exists,
            # but readers must retry until the durable FileShare.None writer
            # releases the complete file.
            Start-Sleep -Milliseconds $HoldOpenMilliseconds
        }
    }
    finally { $stream.Dispose() }
}

function Write-FixtureJson {
    param([string] $Name, [object] $Value)
    [IO.File]::WriteAllText(
        (Join-Path $fixtureData $Name),
        ($Value | ConvertTo-Json -Depth 30 -Compress),
        $utf8
    )
}

function New-NetworkInspect {
    param(
        [string] $Name, [string] $Id, [string] $ComposeName,
        [string] $Subnet, [string] $Gateway,
        [switch] $Running
    )
    $created = if ($ComposeName -ceq 'default') {
        '2026-07-05T17:30:21.808081932Z'
    }
    else {
        '2026-07-30T09:19:55.325597748Z'
    }
    $configHash = if ($ComposeName -ceq 'default') {
        '46e59c4f5c259be13fae41f47db52fd04daf6179514ee815f6a8a61ccd86aee9'
    }
    else {
        '85d9bb404d6e93d77b9c26b6862a7c2a52dd26aa3ba8eb092f28711a675a26a8'
    }
    $containers = [ordered]@{}
    if ($Running) {
        $prefix = if ($ComposeName -ceq 'default') { '20' } else { '22' }
        $containers[$postgresId] = [ordered]@{
            Name = 'weatherpoly-postgres-1'
            EndpointID = $prefix * 32
            MacAddress = "02:42:ac:$prefix`:00:02"
            IPv4Address = "172.$prefix.0.2/16"
            IPv6Address = ''
        }
    }
    return [ordered]@{
        Name = $Name
        Id = $Id
        Created = $created
        Scope = 'local'
        Driver = 'bridge'
        EnableIPv4 = $true
        EnableIPv6 = $false
        IPAM = [ordered]@{
            Driver = 'default'
            Options = $null
            Config = @([ordered]@{
                Subnet = $Subnet
                Gateway = $Gateway
                IPRange = ''
                AuxiliaryAddresses = $null
            })
        }
        Internal = $false
        Attachable = $false
        Ingress = $false
        ConfigFrom = [ordered]@{ Network = '' }
        ConfigOnly = $false
        Containers = $containers
        Options = [ordered]@{
            'com.docker.network.enable_ipv4' = 'true'
            'com.docker.network.enable_ipv6' = 'false'
        }
        Labels = [ordered]@{
            'com.docker.compose.config-hash' = $configHash
            'com.docker.compose.project' = 'weatherpoly'
            'com.docker.compose.network' = $ComposeName
            'com.docker.compose.version' = '2.38.2'
        }
    }
}

function New-Attachment {
    param(
        [string] $NetworkId,
        [string] $Network,
        [switch] $Running,
        [switch] $ConfiguredPending
    )
    $prefix = if ($Network -ceq 'default') { '20' } else { '22' }
    return [ordered]@{
        IPAMConfig = if ($ConfiguredPending) { [ordered]@{} } else { $null }
        Links = $null
        Aliases = @('postgres', 'weatherpoly-postgres-1')
        NetworkID = if ($ConfiguredPending) { '' } else { $NetworkId }
        EndpointID = if ($Running) { $prefix * 32 } else { '' }
        Gateway = if ($Running) { "172.$prefix.0.1" } else { '' }
        IPAddress = if ($Running) { "172.$prefix.0.2" } else { '' }
        IPPrefixLen = if ($Running) { 16 } else { 0 }
        IPv6Gateway = ''
        GlobalIPv6Address = ''
        GlobalIPv6PrefixLen = 0
        MacAddress = if ($Running) { "02:42:ac:$prefix`:00:02" } else { '' }
        DriverOpts = if ($ConfiguredPending) { [ordered]@{} } else { $null }
        GwPriority = 0
        DNSNames = @(
            $postgresId.Substring(0, 12), 'postgres', 'weatherpoly-postgres-1'
        )
    }
}

function New-PostgresInspect {
    param(
        [Parameter(Mandatory = $true)][string] $Root,
        [switch] $Running,
        [switch] $ConfiguredPending
    )
    $hbaSource = (Join-Path $Root 'ops\postgres\stratus-pg_hba.conf').Replace('\', '/')
    $ports = if ($Running) {
        [ordered]@{ '5432/tcp' = @([ordered]@{ HostIp = '127.0.0.1'; HostPort = '5432' }) }
    }
    else { [pscustomobject]@{} }
    return [ordered]@{
        Id = $postgresId
        Name = '/weatherpoly-postgres-1'
        Image = $postgresImageId
        Config = [ordered]@{ Image = $postgresImage }
        State = [ordered]@{
            Status = if ($Running) { 'running' } else { 'exited' }
            Running = [bool]$Running
            Paused = $false
            Restarting = $false
            OOMKilled = $false
            Dead = $false
            Pid = if ($Running) { 4242 } else { 0 }
            ExitCode = 0
            Error = ''
            StartedAt = if ($Running) {
                '2026-08-23T12:00:01.0000000Z'
            }
            else { '2026-08-20T10:00:00.0000000Z' }
            FinishedAt = if ($Running) {
                '0001-01-01T00:00:00Z'
            }
            else { '2026-08-20T10:05:00.0000000Z' }
            Health = if ($Running) { [ordered]@{ Status = 'healthy' } } else { $null }
        }
        RestartCount = 0
        HostConfig = [ordered]@{
            RestartPolicy = [ordered]@{ Name = 'no'; MaximumRetryCount = 0 }
            NetworkMode = 'weatherpoly_default'
            PortBindings = [ordered]@{
                '5432/tcp' = @([ordered]@{ HostIp = '127.0.0.1'; HostPort = '5432' })
            }
        }
        Mounts = @(
            [ordered]@{
                Type = 'bind'; Source = $hbaSource
                Destination = '/etc/postgresql/stratus-pg_hba.conf'
                Mode = 'ro'; RW = $false; Propagation = 'rprivate'
            },
            [ordered]@{
                Type = 'volume'; Name = 'weatherpoly_stratus-postgres'
                Driver = 'local'
                Source = '/var/lib/docker/volumes/weatherpoly_stratus-postgres/_data'
                Destination = '/var/lib/postgresql/data'
                Mode = 'rw'; RW = $true; Propagation = ''
            }
        )
        NetworkSettings = [ordered]@{
            Ports = $ports
            Networks = [ordered]@{
                weatherpoly_default = New-Attachment -NetworkId $defaultNetworkId `
                    -Network default -Running:$Running
                'weatherpoly_safety-plane' = New-Attachment -NetworkId $safetyNetworkId `
                    -Network safety -Running:$Running `
                    -ConfiguredPending:$ConfiguredPending
            }
        }
    }
}

Write-FixtureJson -Name 'endpoint.json' -Value ([ordered]@{
    Host = 'npipe:////./pipe/dockerDesktopLinuxEngine'
    SkipTLSVerify = $false
})
Write-FixtureJson -Name 'engine.json' -Value ([ordered]@{
    ID = 'd98e03f5-06d7-4af4-9f21-b6f7a70ebf68'
    ServerVersion = '28.3.2'
    Name = 'docker-desktop'
    DockerRootDir = '/var/lib/docker'
})
Write-FixtureJson -Name 'engine-drift.json' -Value ([ordered]@{
    ID = 'fixture-engine-drift'
    ServerVersion = '28.3.2'
    Name = 'docker-desktop'
    DockerRootDir = '/var/lib/docker'
})
Write-FixtureJson -Name 'network-default.json' -Value (
    New-NetworkInspect -Name 'weatherpoly_default' -Id $defaultNetworkId `
        -ComposeName default -Subnet '172.20.0.0/16' -Gateway '172.20.0.1'
)
Write-FixtureJson -Name 'network-default-running.json' -Value (
    New-NetworkInspect -Name 'weatherpoly_default' -Id $defaultNetworkId `
        -ComposeName default -Subnet '172.20.0.0/16' -Gateway '172.20.0.1' -Running
)
Write-FixtureJson -Name 'network-safety.json' -Value (
    New-NetworkInspect -Name 'weatherpoly_safety-plane' -Id $safetyNetworkId `
        -ComposeName safety-plane -Subnet '172.22.0.0/16' -Gateway '172.22.0.1'
)
Write-FixtureJson -Name 'network-safety-running.json' -Value (
    New-NetworkInspect -Name 'weatherpoly_safety-plane' -Id $safetyNetworkId `
        -ComposeName safety-plane -Subnet '172.22.0.0/16' -Gateway '172.22.0.1' -Running
)

$dockerPath = Join-Path $fakeBin 'docker.exe'
$mockTypeName = 'StratusWatchdogDockerMock' + [Guid]::NewGuid().ToString('N')
$dockerSource = @"
using System;
using System.IO;
using System.Threading;

public static class $mockTypeName
{
    static string Env(string name) { return Environment.GetEnvironmentVariable(name); }
    static string P(string name) { return Path.Combine(Env("STRATUS_WATCHDOG_CONTROL"), name); }
    static string Fixture(string name) { return File.ReadAllText(Path.Combine(Env("STRATUS_WATCHDOG_FIXTURES"), name)); }
    static bool Exact(string[] args, params string[] wanted)
    {
        if (args.Length != wanted.Length) return false;
        for (int i = 0; i < args.Length; i++)
            if (!String.Equals(args[i], wanted[i], StringComparison.Ordinal)) return false;
        return true;
    }
    public static int Main(string[] args)
    {
        File.AppendAllText(P("scoped-commands.log"), String.Join("\u001f", args) + Environment.NewLine);
        if (args.Length < 3 || args[0] != "--context" || args[1] != "desktop-linux")
            return 96;
        string[] semantic = new string[args.Length - 2];
        Array.Copy(args, 2, semantic, 0, semantic.Length);
        args = semantic;
        File.AppendAllText(P("commands.log"), String.Join("\u001f", args) + Environment.NewLine);
        if (Exact(args, "context", "show")) { Console.WriteLine("desktop-linux"); return 0; }
        if (Exact(args, "context", "inspect", "desktop-linux", "--format", "{{json .Endpoints.docker}}"))
        { Console.Write(Fixture("endpoint.json")); return 0; }
        if (Exact(args, "info", "--format", "{{json .}}"))
        {
            string delayPath = P("validation-delay-ms.txt");
            if (File.Exists(delayPath))
            {
                int delay = Math.Max(0, Convert.ToInt32(File.ReadAllText(delayPath).Trim()));
                File.Delete(delayPath);
                File.WriteAllText(P("validation-entered.flag"), "1");
                Thread.Sleep(delay);
            }
            Console.Write(Fixture(File.Exists(P("engine-drift.flag")) ? "engine-drift.json" : "engine.json"));
            return 0;
        }
        if (Exact(args, "inspect", "$postgresId", "--format", "{{json .}}"))
        {
            if (File.Exists(P("ambiguous.flag"))) return 31;
            string state = File.ReadAllText(P("state.txt")).Trim();
            Console.Write(Fixture(state == "running" ? "postgres-running.json" : "postgres-stopped.json"));
            return 0;
        }
        if (args.Length == 5 && args[0] == "network" && args[1] == "inspect" &&
            args[3] == "--format" && args[4] == "{{json .}}")
        {
            string state = File.ReadAllText(P("state.txt")).Trim();
            string suffix = state == "running" ? "-running.json" : ".json";
            if (args[2] == "$defaultNetworkId") { Console.Write(Fixture("network-default" + suffix)); return 0; }
            if (args[2] == "$safetyNetworkId") { Console.Write(Fixture("network-safety" + suffix)); return 0; }
            return 32;
        }
        if (Exact(args, "ps", "-q", "--no-trunc"))
        {
            if (File.ReadAllText(P("state.txt")).Trim() == "running") Console.WriteLine("$postgresId");
            return 0;
        }
        if (Exact(args, "stop", "--timeout", "15", "$postgresId"))
        {
            if (File.Exists(P("hang-stop.flag"))) { Thread.Sleep(60000); return 91; }
            if (File.ReadAllText(P("state.txt")).Trim() != "running") return 34;
            if (File.Exists(P("ambiguous.flag"))) File.Delete(P("ambiguous.flag"));
            File.WriteAllText(P("state.txt"), "stopped");
            Console.WriteLine("$postgresId");
            return 0;
        }
        return 90;
    }
}
"@
$sourcePath = Join-Path $temporaryRoot 'DockerWatchdogMock.cs'
[IO.File]::WriteAllText($sourcePath, $dockerSource, $utf8)
$compiler = @(
    (Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'),
    (Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe')
) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
    Select-Object -First 1
if ([string]::IsNullOrWhiteSpace([string]$compiler)) {
    throw 'Could not find the Windows C# compiler for watchdog fixtures.'
}
$compilerOutput = @(& $compiler '/nologo' '/target:exe' "/out:$dockerPath" $sourcePath 2>&1)
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $dockerPath)) {
    throw "Could not compile fake Docker: $($compilerOutput -join [Environment]::NewLine)"
}
$fakeDockerSha = Get-FileSha256 -Path $dockerPath
$patchedWatchdog = Join-Path $temporaryRoot 'Invoke-Watchdog-Test.ps1'
$patched = $source.Replace(
    '388f231898cff8674a2aa341e306b77fd1f93a5954dd33ff556575a69227824d',
    $fakeDockerSha
).Replace(
    '$dockerProofWallClockTimeoutSeconds = 15',
    '$dockerProofWallClockTimeoutSeconds = 12'
).Replace(
    '$dockerStopWallClockTimeoutSeconds = 25',
    '$dockerStopWallClockTimeoutSeconds = 1'
).Replace(
    '$startFenceAcquireAdvisorySeconds = 20',
    '$startFenceAcquireAdvisorySeconds = 2'
).Replace(
    '$pollMilliseconds = 200',
    '$pollMilliseconds = 50'
)
[IO.File]::WriteAllText($patchedWatchdog, $patched, $utf8)

function ConvertTo-ArgumentToken {
    param([AllowEmptyString()][string] $Value)
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
    return '"' + $Value.Replace('\', '\\').Replace('"', '\"') + '"'
}

$shellPath = (Get-Process -Id $PID).Path
$currentOwnerStart = ([DateTimeOffset]::new(
    (Get-Process -Id $PID).StartTime.ToUniversalTime(), [TimeSpan]::Zero
)).ToString('o')

function New-Case {
    param([string] $Name, [int] $Owner = $PID, [string] $OwnerStart = $currentOwnerStart,
        [int] $HeartbeatTimeout = 5, [int] $DeadlineSeconds = 30,
        [switch] $ConfiguredPending)
    $root = Join-Path $temporaryRoot $Name
    $evidence = Join-Path $root 'evidence'
    $hbaParent = Join-Path $root 'ops\postgres'
    [void](New-Item -ItemType Directory -Path $evidence, $hbaParent -Force)
    [IO.File]::WriteAllText((Join-Path $hbaParent 'stratus-pg_hba.conf'), 'fixture', $utf8)
    Write-FixtureJson -Name 'postgres-stopped.json' -Value (
        New-PostgresInspect -Root $root -ConfiguredPending:$ConfiguredPending
    )
    Write-FixtureJson -Name 'postgres-running.json' -Value (
        New-PostgresInspect -Root $root -Running
    )
    $session = [Guid]::NewGuid().ToString('N')
    $acceptedAt = [DateTimeOffset]::UtcNow
    $deadline = $acceptedAt.AddSeconds($DeadlineSeconds).ToString('o')
    $preStartPath = Join-Path $evidence 'prestart.json'
    $leasePath = Join-Path $evidence 'lease.json'
    $receiptPath = Join-Path $evidence 'receipt.json'
    $heartbeatPath = Join-Path $evidence 'heartbeat.json'
    $commitPath = Join-Path $evidence 'commit.json'
    $livePath = Join-Path $evidence 'live.json'
    $startFencePath = Join-Path $evidence 'start-fence.lock'
    $startRevokePath = Join-Path $evidence 'start-revoked.json'
    $startAttemptPath = Join-Path $evidence 'start-attempt.json'
    $startCompletionPath = Join-Path $evidence 'start-complete.json'
    $repairPath = Join-Path $evidence 'network-repair.json'
    Write-JsonCreateNew -Path $startFencePath -Value ([ordered]@{
        format = 'stratus-successor-transition-start-fence-v1'
        session_id = $session
        owner_pid = [int]$Owner
        owner_start_time_utc = $OwnerStart
        created_at = $acceptedAt.ToString('o')
    })
    $startFenceSha = Get-FileSha256 -Path $startFencePath
    [IO.File]::WriteAllText(
        $repairPath,
        '{"format":"stratus-stopped-postgres-safety-plane-repair-v2","accepted":true}',
        $utf8
    )
    $repairSha = Get-FileSha256 -Path $repairPath
    $networkRepair = [ordered]@{
        path = $repairPath
        sha256 = $repairSha
        payload_sha256 = '1' * 64
        accepted_at = $acceptedAt.ToString('o')
        expires_at = $acceptedAt.AddSeconds(600).ToString('o')
        disposition = if ($ConfiguredPending) {
            'verified_configured_pending_existing'
        }
        else {
            'connected_by_this_invocation'
        }
        post_first_sha256 = '2' * 64
        post_second_sha256 = '2' * 64
        final_attachment_sha256 = '3' * 64
        docker_engine_contract_sha256 = '4' * 64
    }
    $preStartPayload = [ordered]@{
        accepted_at = $acceptedAt.ToString('o')
        validity = [ordered]@{
            max_age_seconds = 600
            postgres_start_window_seconds = 300
            expires_at = $acceptedAt.AddSeconds(600).ToString('o')
            postgres_start_deadline = $acceptedAt.AddSeconds(300).ToString('o')
        }
        network_repair = $networkRepair
        postgres_prestart = [ordered]@{
            container_id = $postgresId; image_id = $postgresImageId
            config_image = $postgresImage; restart_count = 0
            restart_policy = 'no'; state = 'exited'; oom_killed = $false
            started_at = '2026-08-20T10:00:00.0000000Z'
            finished_at = '2026-08-20T10:05:00.0000000Z'
        }
        transition_session = [ordered]@{
            session_id = $session
            restart_policy_transition = [ordered]@{
                previous = 'unless-stopped'; armed = 'no'
                command = @('update', '--restart=no', $postgresId)
                command_issued = $true; stopped_identity_rechecked = $true
                restore_on_success = $false
            }
            observation = [ordered]@{
                start_command_issued = $false; start_completed = $false
            }
            watchdog_handoff = [ordered]@{
                format = 'stratus-successor-transition-watchdog-handoff-v3'
                session_id = $session
                owner_pid = [int]$Owner
                owner_start_time_utc = $OwnerStart
                hard_deadline_utc = $deadline
                heartbeat_timeout_seconds = [int]$HeartbeatTimeout
                lease_path = $leasePath
                receipt_path = $receiptPath
                heartbeat_path = $heartbeatPath
                commit_path = $commitPath
                live_path = $livePath
                prestart_path = $preStartPath
                start_fence_path = $startFencePath
                start_fence_sha256 = $startFenceSha
                start_revoke_path = $startRevokePath
                start_attempt_path = $startAttemptPath
                start_completion_path = $startCompletionPath
            }
        }
        authorization = [ordered]@{
            postgres_start_permitted = $false
            external_postgres_start_permitted = $false
            transition_process_start_permitted = $true
            api_stage_permitted = $false; worker_start_permitted = $false
            safety_control_start_permitted = $false
            live_transition_permitted = $false
            binance_kill_switch_removal_permitted = $false
        }
    }
    $preStart = [ordered]@{
        format = 'stratus-post-migration-successor-transition-prestart-v2'
        accepted = $true
        payload = $preStartPayload
        payload_sha256 = Get-Sha256Text -Value (
            $preStartPayload | ConvertTo-Json -Depth 20 -Compress
        )
    }
    Write-JsonCreateNew -Path $preStartPath -Value $preStart
    return [pscustomobject]@{
        root = $root; evidence = $evidence; session = $session
        owner_pid = $Owner; owner_start = $OwnerStart; deadline = $deadline
        heartbeat_timeout = $HeartbeatTimeout
        prestart = $preStartPath; prestart_sha = Get-FileSha256 -Path $preStartPath
        repair = $repairPath
        lease = $leasePath
        receipt = $receiptPath
        heartbeat = $heartbeatPath
        commit = $commitPath
        live = $livePath
        start_fence = $startFencePath
        start_fence_sha = $startFenceSha
        start_revoke = $startRevokePath
        start_attempt = $startAttemptPath
        start_completion = $startCompletionPath
        control = Join-Path $evidence 'control'
    }
}

function Start-Watchdog {
    param([Parameter(Mandatory = $true)][object] $Case)
    [void](New-Item -ItemType Directory -Path $Case.control -Force)
    [IO.File]::WriteAllText((Join-Path $Case.control 'state.txt'), 'stopped', $utf8)
    $arguments = @(
        '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', $patchedWatchdog,
        '-Root', $Case.root,
        '-SessionId', $Case.session,
        '-OwnerPid', [string]$Case.owner_pid,
        '-OwnerStartTimeUtc', $Case.owner_start,
        '-DeadlineUtc', $Case.deadline,
        '-HeartbeatTimeoutSeconds', [string]$Case.heartbeat_timeout,
        '-LeasePath', $Case.lease,
        '-ReceiptPath', $Case.receipt,
        '-HeartbeatPath', $Case.heartbeat,
        '-CommitPath', $Case.commit,
        '-LiveAcceptancePath', $Case.live,
        '-PreStartAttestationPath', $Case.prestart,
        '-ExpectedPreStartSha256', $Case.prestart_sha,
        '-StartFencePath', $Case.start_fence,
        '-ExpectedStartFenceSha256', $Case.start_fence_sha,
        '-StartRevokePath', $Case.start_revoke,
        '-StartAttemptPath', $Case.start_attempt,
        '-StartCompletionPath', $Case.start_completion,
        '-PostgresContainer', $postgresId
    )
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $shellPath
    $info.Arguments = (($arguments | ForEach-Object {
        ConvertTo-ArgumentToken -Value ([string]$_)
    }) -join ' ')
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.EnvironmentVariables['PATH'] = "$fakeBin$([IO.Path]::PathSeparator)$env:PATH"
    $info.EnvironmentVariables['STRATUS_WATCHDOG_FIXTURES'] = $fixtureData
    $info.EnvironmentVariables['STRATUS_WATCHDOG_CONTROL'] = $Case.control
    foreach ($name in @(
        'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH',
        'DOCKER_CONFIG', 'DOCKER_API_VERSION', 'DOCKER_CLI_PLUGIN_EXTRA_DIRS'
    )) { [void]$info.EnvironmentVariables.Remove($name) }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $info
    if (-not $process.Start()) { throw 'Could not start watchdog fixture.' }
    return [pscustomobject]@{
        process = $process
        stdout = $process.StandardOutput.ReadToEndAsync()
        stderr = $process.StandardError.ReadToEndAsync()
    }
}

function Wait-Lease {
    param([object] $Case, [object] $Run)
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(15)
    do {
        if ((Test-Path $Case.lease) -and (Get-Item $Case.lease).Length -gt 0) {
            try {
                # CreateNew makes the path visible before the watchdog closes its
                # write-through, FileShare.None handle. Treat that sharing window
                # as "not published yet" on both Windows PowerShell and pwsh.
                return (Get-Content -Raw -LiteralPath $Case.lease | ConvertFrom-Json)
            }
            catch [IO.IOException] {
                # Retry until the durable writer releases the completed lease.
            }
        }
        if ($Run.process.HasExited) {
            $stdout = $Run.stdout.GetAwaiter().GetResult()
            $stderr = $Run.stderr.GetAwaiter().GetResult()
            $receipt = if ((Test-Path $Case.receipt) -and (Get-Item $Case.receipt).Length -gt 0) {
                Get-Content -Raw -LiteralPath $Case.receipt
            }
            else { '<no receipt>' }
            throw "Watchdog exited before lease: stderr=$stderr stdout=$stdout receipt=$receipt"
        }
        Start-Sleep -Milliseconds 50
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw 'Timed out waiting for watchdog lease.'
}

function Wait-Watchdog {
    param([object] $Case, [object] $Run, [int] $ExpectedExit, [string] $ExpectedStatus)
    if (-not $Run.process.WaitForExit(20000)) {
        try { $Run.process.Kill() } catch { }
        throw "Watchdog fixture did not terminate: $($Case.root)"
    }
    $stdout = $Run.stdout.GetAwaiter().GetResult()
    $stderr = $Run.stderr.GetAwaiter().GetResult()
    if ($ExpectedStatus -ceq 'success' -and $stderr.Length -ne 0) {
        throw "Successful watchdog emitted stderr: $stderr"
    }
    if ($Run.process.ExitCode -ne $ExpectedExit) {
        throw "Watchdog exit $($Run.process.ExitCode), expected $ExpectedExit. stderr=$stderr stdout=$stdout"
    }
    if (-not (Test-Path $Case.receipt) -or (Get-Item $Case.receipt).Length -le 0) {
        throw 'Watchdog did not durably publish a terminal receipt.'
    }
    $receipt = Get-Content -Raw -LiteralPath $Case.receipt | ConvertFrom-Json
    if ($receipt.status -cne $ExpectedStatus) {
        throw "Watchdog status $($receipt.status), expected $ExpectedStatus."
    }
    $authorizationNames = @(
        'api_stage_permitted', 'worker_start_permitted',
        'safety_control_start_permitted', 'live_transition_permitted',
        'binance_kill_switch_removal_permitted', 'migration_container_permitted'
    )
    $actualAuthorizationNames = @($receipt.authorization.PSObject.Properties.Name)
    [Array]::Sort($authorizationNames, [StringComparer]::Ordinal)
    [Array]::Sort($actualAuthorizationNames, [StringComparer]::Ordinal)
    $expectedApiAuthorization = $false
    if (
        ($actualAuthorizationNames -join "`n") -cne
            ($authorizationNames -join "`n") -or
        [bool]$receipt.authorization.api_stage_permitted -ne
            $expectedApiAuthorization -or
        [bool]$receipt.authorization.worker_start_permitted -or
        [bool]$receipt.authorization.safety_control_start_permitted -or
        [bool]$receipt.authorization.live_transition_permitted -or
        [bool]$receipt.authorization.binance_kill_switch_removal_permitted -or
        [bool]$receipt.authorization.migration_container_permitted
    ) {
        throw 'Watchdog terminal authorization is not exact and fail-closed.'
    }
    if ($ExpectedStatus -ceq 'success' -and $null -ne $receipt.failure) {
        throw 'Successful watchdog receipt failure must be native JSON null.'
    }
    if ($ExpectedStatus -ceq 'success') {
        if (Test-Path -LiteralPath $Case.start_revoke) {
            throw 'Successful watchdog unexpectedly published a start revoke marker.'
        }
    }
    elseif (
        -not (Test-Path -LiteralPath $Case.start_revoke -PathType Leaf) -or
        (Get-Item -LiteralPath $Case.start_revoke).Length -le 0 -or
        -not $receipt.containment.start_revoke_present -or
        -not $receipt.containment.start_fence_lock_acquired -or
        -not $receipt.containment.final_after_fence
    ) {
        throw 'Contained watchdog exit lacks durable revoke and final fenced containment.'
    }
    $Run.process.Dispose()
    return $receipt
}

function Wait-ControlMarker {
    param(
        [Parameter(Mandatory = $true)][object] $Case,
        [Parameter(Mandatory = $true)][string] $Name
    )
    $path = Join-Path $Case.control $Name
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(10)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        if (Test-Path -LiteralPath $path -PathType Leaf) { return $path }
        Start-Sleep -Milliseconds 25
    }
    throw "Timed out waiting for watchdog test marker $Name."
}

function Write-StartProtocol {
    param(
        [Parameter(Mandatory = $true)][object] $Case,
        [switch] $Complete
    )

    $fence = [IO.FileStream]::new(
        $Case.start_fence, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        [IO.FileShare]::None
    )
    try {
        $issuedAt = [DateTimeOffset]::UtcNow
        Write-JsonCreateNew -Path $Case.start_attempt -Value ([ordered]@{
            format = 'stratus-successor-transition-start-attempt-v1'
            session_id = $Case.session
            owner_pid = [int]$Case.owner_pid
            owner_start_time_utc = $Case.owner_start
            start_fence_path = $Case.start_fence
            start_fence_sha256 = $Case.start_fence_sha
            command = @('start', $postgresId)
            issued_at = $issuedAt.ToString('o')
        })
        $attemptSha = Get-FileSha256 -Path $Case.start_attempt
        $completionSha = $null
        if ($Complete) {
            $requestedAt = [DateTimeOffset]::UtcNow
            $completedAt = [DateTimeOffset]::UtcNow
            $boundedStart = [ordered]@{
                command = @('start', $postgresId)
                docker_executable_path = $dockerPath
                docker_executable_sha256 = $fakeDockerSha
                process_timeout_milliseconds = 15000
                stdout_maximum_bytes = 4096
                stderr_maximum_bytes = 4096
                requested_at = $requestedAt.ToString('o')
                completed_at = $completedAt.ToString('o')
                exit_code = 0
                output_container_id = $postgresId
                timed_out = $false
                output_exceeded = $false
                process_started = $true
                process_exited = $true
                process_id = [int]$PID
                kill_issued = $false
            }
            Write-JsonCreateNew -Path $Case.start_completion -Value ([ordered]@{
                format = 'stratus-successor-transition-start-complete-v1'
                session_id = $Case.session
                owner_pid = [int]$Case.owner_pid
                owner_start_time_utc = $Case.owner_start
                start_attempt_path = $Case.start_attempt
                start_attempt_sha256 = $attemptSha
                outcome = 'started'
                completed_at = $completedAt.ToString('o')
                bounded_start = $boundedStart
            })
            $completionSha = Get-FileSha256 -Path $Case.start_completion
        }
        return [pscustomobject]@{
            attempt_sha256 = $attemptSha
            completion_sha256 = $completionSha
        }
    }
    finally {
        $fence.Dispose()
    }
}

function New-LiveAndCommit {
    param(
        [object] $Case,
        [object] $Lease,
        [switch] $ForgeHash,
        [switch] $MalformedLive,
        [ValidateRange(0, 5000)][int] $HeartbeatHoldOpenMilliseconds = 0,
        [ValidateRange(0, 5000)][int] $CommitHoldOpenMilliseconds = 0
    )
    $startProtocol = Write-StartProtocol -Case $Case -Complete
    [IO.File]::WriteAllText((Join-Path $Case.control 'state.txt'), 'running', $utf8)
    Write-JsonCreateNew -Path $Case.heartbeat -Value ([ordered]@{
        format = 'stratus-successor-transition-watchdog-heartbeat-v1'
        session_id = $Case.session
        owner_pid = [int]$Case.owner_pid
        owner_start_time_utc = $Case.owner_start
        lease_path = $Case.lease
        lease_sha256 = Get-FileSha256 -Path $Case.lease
        sequence = [int64]1
        written_at = [DateTimeOffset]::UtcNow.ToString('o')
    }) -HoldOpenMilliseconds $HeartbeatHoldOpenMilliseconds
    if ($MalformedLive) {
        [IO.File]::WriteAllText($Case.live, '{"accepted":"true"}', $utf8)
    }
    else {
        $now = [DateTimeOffset]::UtcNow
        $preStartEnvelope = ConvertFrom-TestJsonPreservingDates `
            -Json (Get-Content -Raw $Case.prestart)
        $payload = [ordered]@{
            accepted_at = $now.ToString('o')
            artifact_written_at = $now.ToString('o')
            historical = [ordered]@{}
            network_repair = $preStartEnvelope.payload.network_repair
            prestart = [ordered]@{
                path = $Case.prestart; sha256 = $Case.prestart_sha
                payload_sha256 = $preStartEnvelope.payload_sha256
                accepted_at = $preStartEnvelope.payload.accepted_at
            }
            successor = [ordered]@{
                revision = 'a' * 40
                image = 'stratus:candidate-aaaaaaaaaaaa'
                image_id = 'sha256:' + ('b' * 64)
            }
            closed_contract = [ordered]@{}
            postgres_transition = [ordered]@{
                container_id = $postgresId; image_id = $postgresImageId
                config_image = $postgresImage; restart_count = 0
                restart_policy = 'no'
                previous_started_at = '2026-08-20T10:00:00.0000000Z'
                previous_finished_at = '2026-08-20T10:05:00.0000000Z'
                successor_started_at = '2026-08-23T12:00:01.0000000Z'
                state = 'running'; oom_killed = $false
                health = [ordered]@{ status = 'healthy'; checked_at = $now.ToString('o') }
            }
            transition_session = [ordered]@{
                session_id = $Case.session
                observation = [ordered]@{
                    start_command_issued = $true; start_completed = $true
                }
                watchdog = [ordered]@{
                    format = 'stratus-successor-transition-watchdog-binding-v3'
                    session_id = $Case.session
                    owner_pid = [int]$Case.owner_pid
                    owner_start_time_utc = $Case.owner_start
                    hard_deadline_utc = $Case.deadline
                    heartbeat_timeout_seconds = [int]$Case.heartbeat_timeout
                    lease_path = $Case.lease
                    lease_sha256 = Get-FileSha256 -Path $Case.lease
                    receipt_path = $Case.receipt
                    heartbeat_path = $Case.heartbeat
                    commit_path = $Case.commit
                    start_fence_path = $Case.start_fence
                    start_fence_sha256 = $Case.start_fence_sha
                    start_revoke_path = $Case.start_revoke
                    start_attempt_path = $Case.start_attempt
                    start_attempt_sha256 = $startProtocol.attempt_sha256
                    start_completion_path = $Case.start_completion
                    start_completion_sha256 = $startProtocol.completion_sha256
                    start_revoke_absent = $true
                }
                rollback = [ordered]@{
                    on_failure = $true; stop_timeout_seconds = 15; executed = $false
                }
            }
            topology = [ordered]@{
                running_count = 1
                postgres_only = $true
                application_writers_running = $false
            }
            hba = [ordered]@{}
            final_boundary = [ordered]@{}
            database = [ordered]@{
                schema_head = '20260730_0047'
                indexes_exact = $true; functions_exact = $true
                runtime_boundary = $true; safety_boundary = $true
                control_triggers_exact = $true; commit_triggers_exact = $true
                identity_triggers_exact = $true; fail_closed_controls = $true
                zero_live_state = $true; zero_pending_commands = $true
                zero_other_sessions = $true; bootstrap_audit_exact = $true
                fence_armed = $true; active_lease_exists = $false
                execution_authorized = $false
                first_database_clock = $now.ToString('o')
                second_database_clock = $now.ToString('o')
            }
            authorization = [ordered]@{
                api_stage_permitted = $false; worker_start_permitted = $false
                safety_control_start_permitted = $false
                live_transition_permitted = $false
                binance_kill_switch_removal_permitted = $false
                migration_container_permitted = $false
            }
        }
        $envelope = [ordered]@{
            format = 'stratus-post-migration-successor-transition-live-v2'
            accepted = $true
            payload = $payload
            payload_sha256 = Get-Sha256Text -Value (
                $payload | ConvertTo-Json -Depth 20 -Compress
            )
        }
        Write-JsonCreateNew -Path $Case.live -Value $envelope
    }
    $liveSha = Get-FileSha256 -Path $Case.live
    if ($ForgeHash) { $liveSha = '0' * 64 }
    Write-JsonCreateNew -Path $Case.commit -Value ([ordered]@{
        format = 'stratus-successor-transition-watchdog-commit-v1'
        session_id = $Case.session
        owner_pid = [int]$Case.owner_pid
        owner_start_time_utc = $Case.owner_start
        lease_path = $Case.lease
        lease_sha256 = Get-FileSha256 -Path $Case.lease
        live_path = $Case.live
        live_sha256 = $liveSha
        committed_at = [DateTimeOffset]::UtcNow.ToString('o')
    }) -HoldOpenMilliseconds $CommitHoldOpenMilliseconds
}

function New-Sleeper {
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $shellPath
    $info.Arguments = '-NoLogo -NoProfile -NonInteractive -Command "Start-Sleep -Seconds 60"'
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $process = [Diagnostics.Process]::Start($info)
    $process.Refresh()
    $started = ([DateTimeOffset]::new(
        $process.StartTime.ToUniversalTime(), [TimeSpan]::Zero
    )).ToString('o')
    return [pscustomobject]@{ process = $process; started = $started }
}

function New-FenceHoldingOwner {
    param([Parameter(Mandatory = $true)][string] $Name)

    $holderRoot = Join-Path $temporaryRoot ("$Name-owner")
    [void](New-Item -ItemType Directory -Path $holderRoot -Force)
    $commandPath = Join-Path $holderRoot 'fence-path.txt'
    $readyPath = Join-Path $holderRoot 'fence-held.flag'
    $scriptPath = Join-Path $holderRoot 'hold-fence.ps1'
    $holderSource = @'
param([string] $CommandPath, [string] $ReadyPath)
$ErrorActionPreference = 'Stop'
while (-not (Test-Path -LiteralPath $CommandPath -PathType Leaf)) {
    Start-Sleep -Milliseconds 20
}
$fencePath = [IO.File]::ReadAllText($CommandPath).Trim()
$stream = [IO.FileStream]::new(
    $fencePath, [IO.FileMode]::Open, [IO.FileAccess]::Read,
    [IO.FileShare]::None
)
try {
    [IO.File]::WriteAllText($ReadyPath, 'held', [Text.UTF8Encoding]::new($false))
    while ($true) { Start-Sleep -Seconds 1 }
}
finally {
    $stream.Dispose()
}
'@
    [IO.File]::WriteAllText($scriptPath, $holderSource, $utf8)
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $shellPath
    $info.Arguments = @(
        '-NoLogo', '-NoProfile', '-NonInteractive', '-File',
        (ConvertTo-ArgumentToken -Value $scriptPath),
        '-CommandPath', (ConvertTo-ArgumentToken -Value $commandPath),
        '-ReadyPath', (ConvertTo-ArgumentToken -Value $readyPath)
    ) -join ' '
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $process = [Diagnostics.Process]::Start($info)
    $process.Refresh()
    $started = ([DateTimeOffset]::new(
        $process.StartTime.ToUniversalTime(), [TimeSpan]::Zero
    )).ToString('o')
    return [pscustomobject]@{
        process = $process
        started = $started
        command = $commandPath
        ready = $readyPath
    }
}

$active = [Collections.Generic.List[Diagnostics.Process]]::new()
try {
    # A valid, hash-bound Live commit is the only path that leaves PostgreSQL running.
    $case = New-Case -Name 'valid-commit'
    # Represents a concurrent change to Docker's process-global current
    # context. The fake accepts only an explicit immutable context prefix, so
    # this transition remains bound to desktop-linux.
    [void](New-Item -ItemType Directory -Path $case.control -Force)
    [IO.File]::WriteAllText(
        (Join-Path $case.control 'current-context-swapped.flag'), '1', $utf8
    )
    $run = Start-Watchdog -Case $case
    $active.Add($run.process)
    $lease = Wait-Lease -Case $case -Run $run
    New-LiveAndCommit -Case $case -Lease $lease `
        -HeartbeatHoldOpenMilliseconds 500 -CommitHoldOpenMilliseconds 500
    $receipt = Wait-Watchdog -Case $case -Run $run -ExpectedExit 0 -ExpectedStatus success
    if ((Get-Content -Raw (Join-Path $case.control 'state.txt')).Trim() -cne 'running') {
        throw 'Valid commit unexpectedly stopped PostgreSQL.'
    }
    if ($receipt.trigger -cne 'accepted_live_commit') {
        throw 'Valid commit receipt has wrong trigger.'
    }

    # The observed Docker Desktop 28.3.2 stopped safety-plane placeholder is
    # admissible only before start; the running proof must materialize it.
    $case = New-Case -Name 'valid-configured-pending-commit' -ConfiguredPending
    $run = Start-Watchdog -Case $case
    $active.Add($run.process)
    $lease = Wait-Lease -Case $case -Run $run
    New-LiveAndCommit -Case $case -Lease $lease
    $receipt = Wait-Watchdog -Case $case -Run $run -ExpectedExit 0 `
        -ExpectedStatus success
    if (
        (Get-Content -Raw (Join-Path $case.control 'state.txt')).Trim() -cne 'running' -or
        $receipt.trigger -cne 'accepted_live_commit'
    ) {
        throw 'Configured-pending stopped attachment did not materialize into exact Live proof.'
    }

    # Before any durable start attempt, winning the fence and publishing the
    # revoke proves that no start crossed the boundary; exact stopped proof is
    # sufficient and an already-stopped Docker stop must not be misclassified.
    $owner = New-Sleeper
    $active.Add($owner.process)
    $case = New-Case -Name 'configured-pending-before-start-containment' `
        -Owner $owner.process.Id -OwnerStart $owner.started -HeartbeatTimeout 10 `
        -ConfiguredPending
    $run = Start-Watchdog -Case $case
    $active.Add($run.process)
    [void](Wait-Lease -Case $case -Run $run)
    $owner.process.Kill(); [void]$owner.process.WaitForExit(5000)
    $receipt = Wait-Watchdog -Case $case -Run $run -ExpectedExit 2 `
        -ExpectedStatus contained
    $commands = @(Get-Content -LiteralPath (Join-Path $case.control 'commands.log'))
    if (
        $receipt.trigger -cne 'owner_process_lost' -or
        $receipt.containment.attempted_stop -or
        -not $receipt.containment.verified_stopped -or
        @($commands | Where-Object { $_ -like "stop*${postgresId}" }).Count -ne 0
    ) {
        throw 'Pre-attempt stopped containment issued an unnecessary Docker stop.'
    }

    # A placeholder is never admissible after start, even if all endpoint fields
    # otherwise look live; commit validation must stop the exact retained ID.
    $case = New-Case -Name 'running-configured-pending-rejected' -ConfiguredPending
    $run = Start-Watchdog -Case $case
    $active.Add($run.process)
    $lease = Wait-Lease -Case $case -Run $run
    Write-FixtureJson -Name 'postgres-running.json' -Value (
        New-PostgresInspect -Root $case.root -Running -ConfiguredPending
    )
    New-LiveAndCommit -Case $case -Lease $lease
    $receipt = Wait-Watchdog -Case $case -Run $run -ExpectedExit 2 `
        -ExpectedStatus contained
    $commands = @(Get-Content -LiteralPath (Join-Path $case.control 'commands.log'))
    if (
        $receipt.trigger -cne 'invalid_or_forged_commit' -or
        -not $receipt.containment.attempted_stop -or
        -not $receipt.containment.verified_stopped -or
        @($commands | Where-Object { $_ -like "stop*${postgresId}" }).Count -ne 1
    ) {
        throw 'Running configured-pending near miss was not contained exactly once.'
    }
    Write-FixtureJson -Name 'postgres-running.json' -Value (
        New-PostgresInspect -Root $case.root -Running
    )

    # A real start followed by failure can materialize exact live endpoints and
    # return to the original configured-pending stopped representation.
    $owner = New-Sleeper
    $active.Add($owner.process)
    $case = New-Case -Name 'configured-pending-after-start-containment' `
        -Owner $owner.process.Id -OwnerStart $owner.started -HeartbeatTimeout 10 `
        -ConfiguredPending
    $run = Start-Watchdog -Case $case
    $active.Add($run.process)
    [void](Wait-Lease -Case $case -Run $run)
    [IO.File]::WriteAllText((Join-Path $case.control 'state.txt'), 'running', $utf8)
    $owner.process.Kill(); [void]$owner.process.WaitForExit(5000)
    $receipt = Wait-Watchdog -Case $case -Run $run -ExpectedExit 2 `
        -ExpectedStatus contained
    $commands = @(Get-Content -LiteralPath (Join-Path $case.control 'commands.log'))
    if (
        $receipt.trigger -cne 'owner_process_lost' -or
        -not $receipt.containment.attempted_stop -or
        -not $receipt.containment.verified_stopped -or
        @($commands | Where-Object { $_ -like "stop*${postgresId}" }).Count -ne 1
    ) {
        throw 'Post-start containment did not accept the exact stopped pending result.'
    }

    # A commit cannot outrun owner continuity. Hold the watchdog inside its
    # Docker validation, then terminate the exact PID/start-time owner.
    $owner = New-Sleeper
    $active.Add($owner.process)
    $case = New-Case -Name 'owner-lost-during-commit' -Owner $owner.process.Id `
        -OwnerStart $owner.started -HeartbeatTimeout 10
    $run = Start-Watchdog -Case $case
    $active.Add($run.process)
    $lease = Wait-Lease -Case $case -Run $run
    [IO.File]::WriteAllText(
        (Join-Path $case.control 'validation-delay-ms.txt'), '1500', $utf8
    )
    New-LiveAndCommit -Case $case -Lease $lease
    [void](Wait-ControlMarker -Case $case -Name 'validation-entered.flag')
    $owner.process.Kill(); [void]$owner.process.WaitForExit(5000)
    $receipt = Wait-Watchdog -Case $case -Run $run -ExpectedExit 2 `
        -ExpectedStatus contained
    if ($receipt.trigger -cne 'owner_process_lost') {
        throw 'Owner loss during commit validation did not outrank commit success.'
    }

    # A heartbeat that was fresh when commit validation began must still be
    # fresh by both wall clock and local monotonic age when validation ends.
    $case = New-Case -Name 'heartbeat-stale-during-commit' `
        -HeartbeatTimeout 1 -DeadlineSeconds 20
    $run = Start-Watchdog -Case $case
    $active.Add($run.process)
    $lease = Wait-Lease -Case $case -Run $run
    [IO.File]::WriteAllText(
        (Join-Path $case.control 'validation-delay-ms.txt'), '1500', $utf8
    )
    New-LiveAndCommit -Case $case -Lease $lease
    [void](Wait-ControlMarker -Case $case -Name 'validation-entered.flag')
    $receipt = Wait-Watchdog -Case $case -Run $run -ExpectedExit 2 `
        -ExpectedStatus contained
    if ($receipt.trigger -cne 'heartbeat_stale') {
        throw 'Heartbeat expiry during commit validation did not outrank commit success.'
    }

    # A commit that begins in time cannot publish success after Docker proof
    # work crosses the hard deadline.
    $case = New-Case -Name 'deadline-during-commit' `
        -HeartbeatTimeout 10 -DeadlineSeconds 10
    $run = Start-Watchdog -Case $case
    $active.Add($run.process)
    $lease = Wait-Lease -Case $case -Run $run
    [IO.File]::WriteAllText(
        (Join-Path $case.control 'validation-delay-ms.txt'), '11000', $utf8
    )
    New-LiveAndCommit -Case $case -Lease $lease
    [void](Wait-ControlMarker -Case $case -Name 'validation-entered.flag')
    $receipt = Wait-Watchdog -Case $case -Run $run -ExpectedExit 2 `
        -ExpectedStatus contained
    if ($receipt.trigger -cne 'hard_deadline_expired') {
        throw 'Hard deadline crossed during commit validation did not block success.'
    }

    # Parent death after a fresh start must stop the exact protected full ID.
    $owner = New-Sleeper
    $active.Add($owner.process)
    $case = New-Case -Name 'parent-death' -Owner $owner.process.Id `
        -OwnerStart $owner.started -HeartbeatTimeout 10
    $run = Start-Watchdog -Case $case
    $active.Add($run.process)
    [void](Wait-Lease -Case $case -Run $run)
    [IO.File]::WriteAllText((Join-Path $case.control 'state.txt'), 'running', $utf8)
    $owner.process.Kill()
    [void]$owner.process.WaitForExit(5000)
    $receipt = Wait-Watchdog -Case $case -Run $run -ExpectedExit 2 -ExpectedStatus contained
    if ($receipt.trigger -cne 'owner_process_lost' -or -not $receipt.containment.verified_stopped) {
        throw 'Parent-death containment was not proven.'
    }

    # A hard deadline is independent of a still-live owner.
    $case = New-Case -Name 'deadline' -HeartbeatTimeout 60 -DeadlineSeconds 6
    $run = Start-Watchdog -Case $case
    $active.Add($run.process)
    [void](Wait-Lease -Case $case -Run $run)
    [IO.File]::WriteAllText((Join-Path $case.control 'state.txt'), 'running', $utf8)
    $receipt = Wait-Watchdog -Case $case -Run $run -ExpectedExit 2 -ExpectedStatus contained
    if ($receipt.trigger -cne 'hard_deadline_expired') {
        throw 'Hard deadline did not cause containment.'
    }

    # The local monotonic heartbeat lease expires even while owner PID stays live.
    $case = New-Case -Name 'heartbeat-stale' -HeartbeatTimeout 1 -DeadlineSeconds 30
    $run = Start-Watchdog -Case $case
    $active.Add($run.process)
    [void](Wait-Lease -Case $case -Run $run)
    [IO.File]::WriteAllText((Join-Path $case.control 'state.txt'), 'running', $utf8)
    $receipt = Wait-Watchdog -Case $case -Run $run -ExpectedExit 2 -ExpectedStatus contained
    if ($receipt.trigger -cne 'heartbeat_stale') {
        throw 'Stale heartbeat did not cause containment.'
    }

    # A suspended parent may hold the start fence indefinitely after its final
    # local checks.  The watchdog must remain live and non-terminal until it
    # wins that fence; only then may it durably revoke and claim containment.
    $heldFenceOwner = New-FenceHoldingOwner -Name 'held-start-fence'
    $active.Add($heldFenceOwner.process)
    $case = New-Case -Name 'held-start-fence' `
        -Owner $heldFenceOwner.process.Id -OwnerStart $heldFenceOwner.started `
        -HeartbeatTimeout 3 -DeadlineSeconds 30
    $run = Start-Watchdog -Case $case
    $active.Add($run.process)
    [void](Wait-Lease -Case $case -Run $run)
    [IO.File]::WriteAllText(
        $heldFenceOwner.command, $case.start_fence, $utf8
    )
    $holderReadyDeadline = [DateTimeOffset]::UtcNow.AddSeconds(5)
    while (-not (Test-Path -LiteralPath $heldFenceOwner.ready -PathType Leaf)) {
        if ($heldFenceOwner.process.HasExited -or
            [DateTimeOffset]::UtcNow -ge $holderReadyDeadline) {
            throw 'Exact owner did not acquire the start fence for takeover testing.'
        }
        Start-Sleep -Milliseconds 20
    }
    [IO.File]::WriteAllText((Join-Path $case.control 'state.txt'), 'running', $utf8)
    $receipt = Wait-Watchdog -Case $case -Run $run -ExpectedExit 2 `
        -ExpectedStatus contained
    if (
        $receipt.trigger -cne 'heartbeat_stale' -or
        -not $receipt.containment.fence_acquire_advisory_exceeded -or
        -not $receipt.containment.owner_terminated_for_fence_recovery -or
        $receipt.containment.owner_termination_attempt_count -lt 1 -or
        -not $heldFenceOwner.process.HasExited
    ) {
        throw 'Watchdog did not terminate the exact stale owner and then contain under the fence.'
    }

    # A killed start client can leave a daemon request queued while every
    # stopped snapshot still looks exact.  The durable attempt without a
    # completion must remain nonterminal for longer than the former quiet
    # window, then stop only after the delayed start becomes observable.
    $case = New-Case -Name 'late-daemon-start-after-client-timeout' `
        -HeartbeatTimeout 10 -DeadlineSeconds 30
    $run = Start-Watchdog -Case $case
    $active.Add($run.process)
    [void](Wait-Lease -Case $case -Run $run)
    [void](Write-StartProtocol -Case $case)
    Start-Sleep -Seconds 3
    if (
        $run.process.HasExited -or
        (Get-Item -LiteralPath $case.receipt).Length -ne 0 -or
        -not (Test-Path -LiteralPath $case.start_revoke -PathType Leaf)
    ) {
        throw 'Unresolved daemon start was terminalized before materialization.'
    }
    [IO.File]::WriteAllText(
        (Join-Path $case.control 'state.txt'), 'running', $utf8
    )
    $receipt = Wait-Watchdog -Case $case -Run $run -ExpectedExit 2 `
        -ExpectedStatus contained
    $commands = @(Get-Content -LiteralPath (Join-Path $case.control 'commands.log'))
    if (
        $receipt.trigger -cne 'ambiguous_start_attempt' -or
        $receipt.containment.start_protocol_status -cne 'unresolved' -or
        -not $receipt.containment.causal_materialization_required -or
        -not $receipt.containment.materialization_observed -or
        -not $receipt.containment.docker_stop_succeeded -or
        @($commands | Where-Object { $_ -like "stop*${postgresId}" }).Count -ne 1
    ) {
        throw 'Delayed daemon start did not receive causal post-materialization containment.'
    }

    # Malformed and forged commits fail closed.
    foreach ($variant in @('malformed-marker', 'forged-live-hash', 'malformed-live')) {
        $case = New-Case -Name $variant
        $run = Start-Watchdog -Case $case
        $active.Add($run.process)
        $lease = Wait-Lease -Case $case -Run $run
        if ($variant -ceq 'malformed-marker') {
            [IO.File]::WriteAllText((Join-Path $case.control 'state.txt'), 'running', $utf8)
            Write-JsonCreateNew -Path $case.heartbeat -Value ([ordered]@{
                format = 'stratus-successor-transition-watchdog-heartbeat-v1'
                session_id = $case.session
                owner_pid = [int]$case.owner_pid
                owner_start_time_utc = $case.owner_start
                lease_path = $case.lease
                lease_sha256 = Get-FileSha256 -Path $case.lease
                sequence = [int64]1
                written_at = [DateTimeOffset]::UtcNow.ToString('o')
            })
            [IO.File]::WriteAllText($case.commit, '{"bad":true}', $utf8)
        }
        else {
            New-LiveAndCommit -Case $case -Lease $lease `
                -ForgeHash:($variant -ceq 'forged-live-hash') `
                -MalformedLive:($variant -ceq 'malformed-live')
        }
        $receipt = Wait-Watchdog -Case $case -Run $run -ExpectedExit 2 `
            -ExpectedStatus contained
        if ($receipt.trigger -cne 'invalid_or_forged_commit') {
            throw "$variant did not take invalid-commit containment path."
        }
    }

    # Ambiguous inspect still attempts stop against the exact protected full ID.
    $owner = New-Sleeper
    $active.Add($owner.process)
    $case = New-Case -Name 'ambiguous-start' -Owner $owner.process.Id `
        -OwnerStart $owner.started -HeartbeatTimeout 10
    $run = Start-Watchdog -Case $case
    $active.Add($run.process)
    [void](Wait-Lease -Case $case -Run $run)
    [IO.File]::WriteAllText((Join-Path $case.control 'state.txt'), 'running', $utf8)
    [IO.File]::WriteAllText((Join-Path $case.control 'ambiguous.flag'), '1', $utf8)
    $owner.process.Kill(); [void]$owner.process.WaitForExit(5000)
    $receipt = Wait-Watchdog -Case $case -Run $run -ExpectedExit 2 -ExpectedStatus contained
    if (-not $receipt.containment.attempted_stop -or -not $receipt.containment.verified_stopped) {
        throw 'Ambiguous start did not attempt and verify exact containment.'
    }

    # A hung Docker stop is killed by wall clock, but the recovery authority
    # stays live and retries instead of publishing an unsafe terminal failure.
    $owner = New-Sleeper
    $active.Add($owner.process)
    $case = New-Case -Name 'hung-stop' -Owner $owner.process.Id `
        -OwnerStart $owner.started -HeartbeatTimeout 10
    $run = Start-Watchdog -Case $case
    $active.Add($run.process)
    [void](Wait-Lease -Case $case -Run $run)
    [IO.File]::WriteAllText((Join-Path $case.control 'state.txt'), 'running', $utf8)
    [IO.File]::WriteAllText((Join-Path $case.control 'hang-stop.flag'), '1', $utf8)
    $owner.process.Kill(); [void]$owner.process.WaitForExit(5000)
    Start-Sleep -Seconds 3
    if ($run.process.HasExited -or (Get-Item -LiteralPath $case.receipt).Length -ne 0) {
        throw 'Hung stop incorrectly produced a terminal watchdog result.'
    }
    Remove-Item -LiteralPath (Join-Path $case.control 'hang-stop.flag') -Force
    $receipt = Wait-Watchdog -Case $case -Run $run -ExpectedExit 2 `
        -ExpectedStatus contained
    if (-not $receipt.containment.verified_stopped) {
        throw 'Watchdog did not recover and contain after the hung stop cleared.'
    }

    # Engine drift is nonterminal: the watchdog must not mutate an unapproved
    # engine or exit.  Once the exact engine returns, containment may finish.
    $owner = New-Sleeper
    $active.Add($owner.process)
    $case = New-Case -Name 'engine-drift' -Owner $owner.process.Id `
        -OwnerStart $owner.started -HeartbeatTimeout 10
    $run = Start-Watchdog -Case $case
    $active.Add($run.process)
    [void](Wait-Lease -Case $case -Run $run)
    [IO.File]::WriteAllText((Join-Path $case.control 'state.txt'), 'running', $utf8)
    [IO.File]::WriteAllText((Join-Path $case.control 'engine-drift.flag'), '1', $utf8)
    $owner.process.Kill(); [void]$owner.process.WaitForExit(5000)
    Start-Sleep -Seconds 2
    if ($run.process.HasExited -or (Get-Item -LiteralPath $case.receipt).Length -ne 0) {
        throw 'Engine drift incorrectly produced a terminal watchdog result.'
    }
    $commands = Get-Content -LiteralPath (Join-Path $case.control 'commands.log')
    if (@($commands | Where-Object { $_ -like "stop*${postgresId}" }).Count -ne 0) {
        throw 'Engine drift attempted mutation against an unapproved engine.'
    }
    Remove-Item -LiteralPath (Join-Path $case.control 'engine-drift.flag') -Force
    $receipt = Wait-Watchdog -Case $case -Run $run -ExpectedExit 2 `
        -ExpectedStatus contained
    if (-not $receipt.containment.verified_stopped) {
        throw 'Watchdog did not contain after the approved engine returned.'
    }

    # Cleanup failure must not erase the primary watchdog failure or the
    # durable containment record.
    $patchedNewLine = if ($patched.Contains("`r`n")) { "`r`n" } else { "`n" }
    $cleanupInjected = $patched.Replace(
        '        $script:leaseArmed = $true',
        '        $script:leaseArmed = $true' + $patchedNewLine +
            "        throw 'synthetic watchdog primary failure'"
    ).Replace(
        'try { $script:dockerExecutableLock.Dispose() }',
        "try { throw 'synthetic watchdog cleanup failure' }"
    )
    if (
        -not $cleanupInjected.Contains('synthetic watchdog primary failure') -or
        -not $cleanupInjected.Contains('synthetic watchdog cleanup failure')
    ) {
        throw 'Could not instrument watchdog cleanup aggregation.'
    }
    try {
        [IO.File]::WriteAllText($patchedWatchdog, $cleanupInjected, $utf8)
        $case = New-Case -Name 'cleanup-aggregation'
        $run = Start-Watchdog -Case $case
        $active.Add($run.process)
        if (-not $run.process.WaitForExit(15000)) {
            $run.process.Kill()
            throw 'Cleanup aggregation watchdog did not terminate.'
        }
        $cleanupStdout = $run.stdout.GetAwaiter().GetResult()
        $cleanupStderr = $run.stderr.GetAwaiter().GetResult()
        if (
            $run.process.ExitCode -ne 3 -or
            $cleanupStderr -notlike '*synthetic watchdog primary failure*' -or
            $cleanupStderr -notlike '*synthetic watchdog cleanup failure*' -or
            -not (Test-Path -LiteralPath $case.start_revoke) -or
            -not (Test-Path -LiteralPath $case.receipt)
        ) {
            throw (
                'Watchdog cleanup aggregation lost the primary, cleanup, ' +
                'revoke, or terminal receipt. stdout=' + $cleanupStdout +
                ' stderr=' + $cleanupStderr
            )
        }
        $cleanupReceipt = ConvertFrom-TestJsonPreservingDates -Json (
            Get-Content -LiteralPath $case.receipt -Raw
        )
        if ($cleanupReceipt.status -notin @('contained', 'failure')) {
            throw 'Cleanup aggregation weakened the watchdog containment receipt.'
        }
        $run.process.Dispose()
    }
    finally {
        [IO.File]::WriteAllText($patchedWatchdog, $patched, $utf8)
    }

    # Every pre-existing output/control path blocks before the first Docker call.
    foreach ($leaf in @(
        'lease', 'receipt', 'heartbeat', 'commit', 'live', 'start_revoke',
        'start_attempt', 'start_completion'
    )) {
        $case = New-Case -Name "existing-$leaf"
        [void](New-Item -ItemType Directory -Path $case.control -Force)
        [IO.File]::WriteAllText((Join-Path $case.control 'state.txt'), 'stopped', $utf8)
        [IO.File]::WriteAllText($case.$leaf, 'occupied', $utf8)
        $run = Start-Watchdog -Case $case
        $active.Add($run.process)
        if (-not $run.process.WaitForExit(10000)) {
            $run.process.Kill(); throw "Existing $leaf path did not fail promptly."
        }
        if ($run.process.ExitCode -eq 0) {
            throw "Existing $leaf path was unexpectedly accepted."
        }
        $log = Join-Path $case.control 'commands.log'
        if ((Test-Path $log) -and (Get-Item $log).Length -gt 0) {
            throw "Existing $leaf path was not rejected before Docker."
        }
        [void]$run.stdout.GetAwaiter().GetResult()
        [void]$run.stderr.GetAwaiter().GetResult()
        $run.process.Dispose()
    }

    $scopedLogs = @(Get-ChildItem -LiteralPath $temporaryRoot -Recurse `
        -Filter 'scoped-commands.log' -File)
    if ($scopedLogs.Count -eq 0) {
        throw 'Watchdog tests observed no scoped Docker commands.'
    }
    $separator = [string][char]31
    foreach ($log in $scopedLogs) {
        foreach ($line in @(Get-Content -LiteralPath $log.FullName)) {
            $parts = @([string]$line -split $separator)
            if (
                $parts.Count -lt 3 -or
                $parts[0] -cne '--context' -or
                $parts[1] -cne 'desktop-linux'
            ) {
                throw 'Watchdog issued a Docker child without the immutable context prefix.'
            }
        }
    }

    'Stratus successor transition watchdog regression checks passed.'
}
finally {
    foreach ($process in $active) {
        try {
            if (-not $process.HasExited) { $process.Kill() }
        }
        catch { }
        try { $process.Dispose() } catch { }
    }
    if (Test-Path -LiteralPath $temporaryRoot) {
        Remove-Item -LiteralPath $temporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
