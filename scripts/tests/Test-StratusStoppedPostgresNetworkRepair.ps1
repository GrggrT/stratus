[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$repairPath = Join-Path $repoRoot 'scripts\Invoke-StratusStoppedPostgresNetworkRepair.ps1'
$tokens = $null
$parseErrors = $null
[void][Management.Automation.Language.Parser]::ParseFile(
    $repairPath,
    [ref]$tokens,
    [ref]$parseErrors
)
if ($parseErrors.Count -ne 0) {
    throw 'Stopped PostgreSQL network repair has PowerShell syntax errors.'
}

$source = Get-Content -LiteralPath $repairPath -Raw
foreach ($required in @(
    'stratus-stopped-postgres-safety-plane-repair-v2',
    '6ce82603e44abb95be94dbe95830af2d00c0c36ca3fe8a122b798087ecc1b299',
    '77551e8d1119201f778fdb90e151ec26f1d9cc23832ba7b88c4db6203e3d8ba2',
    '0d1ad122718694dbc5e6714f69a601a7d429e23a0b74eecdb10ace944e5c8620',
    '388f231898cff8674a2aa341e306b77fd1f93a5954dd33ff556575a69227824d',
    'desktop-linux',
    'd98e03f5-06d7-4af4-9f21-b6f7a70ebf68',
    '4a06f3121da4b275c077a18cc2b17a4c12258754e48c3ac2d8de10be0b96ad0d',
    '44ff0d23c63cad8c3e898bdb1378e958ddcedbcd202055903cd0c7949de103e7',
    'e0d52002c06510e7c6f227700d07f412845fc15686f0723a262d8c3e1459f569',
    'DOCKER_API_VERSION',
    "'network', 'connect'",
    "'--alias', 'postgres'",
    "'--alias', `$productionPostgresName",
    "'--gw-priority', '0'",
    '[IO.FileMode]::CreateNew',
    '[IO.FileOptions]::WriteThrough',
    'connected_by_this_invocation',
    'verified_configured_pending_existing',
    'verified_exact_existing',
    'post_attachment_admissible_twice',
    'all_container_lifecycle_inventory_unchanged',
    'continuous_docker_event_history_exact',
    'stratus-stopped-repair-docker-event-witness-v1',
    'Docker event journal has a partial final record',
    'Remove-RepairDockerEventCapturePaths',
    'Assert-RepairDockerEventWitnessHealthy',
    'Volatile.Read(ref stdoutExceeded)',
    'Volatile.Write(ref stderrExceeded, 1)',
    'Interlocked.Add(ref stderrBytes, accepted)',
    'Interlocked.CompareExchange(ref stderrError, candidate, null)',
    "'volume', 'create', '--driver', 'local'",
    'postgres_start_permitted = $false',
    'api_start_permitted = $false',
    'worker_start_permitted = $false',
    'safety_control_start_permitted = $false',
    'live_permitted = $false',
    'binance_kill_switch_removal_permitted = $false'
    'Stopped PostgreSQL network repair failed. Original:'
    'event capture cleanup failed:'
)) {
    if (-not $source.Contains($required)) {
        throw "Stopped repair lacks required source contract: $required"
    }
}
$stderrPublicationIndex = $source.IndexOf(
    'else Interlocked.Add(ref stderrBytes, accepted);',
    [StringComparison]::Ordinal
)
$overflowPublicationIndex = $source.IndexOf(
    'else Volatile.Write(ref stderrExceeded, 1);',
    [StringComparison]::Ordinal
)
$captureWriteIndex = $source.IndexOf(
    'target.Write(buffer, 0, accepted);',
    [StringComparison]::Ordinal
)
if (
    $stderrPublicationIndex -lt 0 -or
    $overflowPublicationIndex -lt 0 -or
    $captureWriteIndex -lt 0 -or
    $stderrPublicationIndex -gt $captureWriteIndex -or
    $overflowPublicationIndex -gt $captureWriteIndex
) {
    throw 'Event-pump health publication is not fail-closed before capture I/O.'
}
foreach ($forbiddenInvocation in @(
    "@('start'",
    "@('stop'",
    "@('restart'",
    "@('rm'",
    "@('run'",
    "@('exec'",
    "@('network', 'disconnect'"
)) {
    if ($source.Contains($forbiddenInvocation)) {
        throw "Stopped repair contains a forbidden Docker mutation: $forbiddenInvocation"
    }
}
if (([regex]::Matches($source, "'network', 'connect'")).Count -ne 1) {
    throw 'Stopped repair must define exactly one network-connect mutation command.'
}

function Get-Sha256Text {
    param([Parameter(Mandatory = $true)][string] $Value)

    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Value)
        return [BitConverter]::ToString(
            $algorithm.ComputeHash($bytes)
        ).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $algorithm.Dispose()
    }
}

function Read-JsonObjectPreservingDates {
    param([Parameter(Mandatory = $true)][string] $LiteralPath)

    $parameters = @{
        InputObject = Get-Content -LiteralPath $LiteralPath -Raw
    }
    $cmdlet = Get-Command -Name 'Microsoft.PowerShell.Utility\ConvertFrom-Json' `
        -CommandType Cmdlet -ErrorAction Stop
    if ($cmdlet.Parameters.ContainsKey('DateKind')) {
        $parameters['DateKind'] = 'String'
    }
    return Microsoft.PowerShell.Utility\ConvertFrom-Json @parameters
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
    'stratus-network-repair-test-' + [Guid]::NewGuid().ToString('N')
)
[void][IO.Directory]::CreateDirectory($temporaryRoot)
$fakeBin = Join-Path $temporaryRoot 'bin'
$fixtureRoot = Join-Path $temporaryRoot 'root'
$evidenceRoot = Join-Path $temporaryRoot 'evidence'
$fixtureData = Join-Path $temporaryRoot 'fixture-data'
foreach ($directory in @(
    $fakeBin,
    $fixtureRoot,
    $evidenceRoot,
    $fixtureData,
    (Join-Path $fixtureRoot 'ops\postgres')
)) {
    [void][IO.Directory]::CreateDirectory($directory)
}
$utf8 = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText(
    (Join-Path $fixtureRoot 'docker-compose.yml'),
    "services:`n  postgres:`n    image: postgres:16-alpine`n",
    $utf8
)
[IO.File]::WriteAllText(
    (Join-Path $fixtureRoot 'docker-compose.override.yml'),
    "services:`n  migrate:`n    command: [alembic, upgrade, head]`n",
    $utf8
)
[IO.File]::Copy(
    (Join-Path $repoRoot 'ops\postgres\stratus-pg_hba.conf'),
    (Join-Path $fixtureRoot 'ops\postgres\stratus-pg_hba.conf')
)

$postgresId =
    '6ce82603e44abb95be94dbe95830af2d00c0c36ca3fe8a122b798087ecc1b299'
$defaultNetworkId =
    '77551e8d1119201f778fdb90e151ec26f1d9cc23832ba7b88c4db6203e3d8ba2'
$safetyNetworkId =
    '0d1ad122718694dbc5e6714f69a601a7d429e23a0b74eecdb10ace944e5c8620'
$apiId = 'a' * 64
$workerId = 'b' * 64
$postgresImage = (
    'postgres:16-alpine@sha256:' +
    '20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'
)
$postgresImageId =
    'sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'
$allowedNetworkConnectArguments = @(
    'network', 'connect',
    '--alias', 'postgres',
    '--alias', 'weatherpoly-postgres-1',
    '--gw-priority', '0',
    $safetyNetworkId,
    $postgresId
)

function New-Attachment {
    param(
        [Parameter(Mandatory = $true)][string] $NetworkId,
        [switch] $ConfiguredPending,
        [switch] $UnsafeAlias
    )

    [string[]]$aliases = if ($UnsafeAlias) {
        [string[]]@('weatherpoly-postgres-1')
    }
    else {
        [string[]]@('weatherpoly-postgres-1', 'postgres')
    }
    return [ordered]@{
        IPAMConfig = if ($ConfiguredPending) { [ordered]@{} } else { $null }
        Links = $null
        Aliases = $aliases
        MacAddress = ''
        DriverOpts = if ($ConfiguredPending) { [ordered]@{} } else { $null }
        GwPriority = 0
        NetworkID = if ($ConfiguredPending) { '' } else { $NetworkId }
        EndpointID = ''
        Gateway = ''
        IPAddress = ''
        IPPrefixLen = 0
        IPv6Gateway = ''
        GlobalIPv6Address = ''
        GlobalIPv6PrefixLen = 0
        DNSNames = @(
            'weatherpoly-postgres-1', 'postgres', $postgresId.Substring(0, 12)
        )
    }
}

function New-PostgresInspect {
    param(
        [ValidateSet('default', 'configured-pending', 'exact', 'unsafe')]
        [string] $AttachmentState,
        [switch] $ConfigDrift
    )

    $networks = [ordered]@{
        'weatherpoly_default' = New-Attachment -NetworkId $defaultNetworkId
    }
    if ($AttachmentState -in @('configured-pending', 'exact', 'unsafe')) {
        $networks['weatherpoly_safety-plane'] = New-Attachment `
            -NetworkId $safetyNetworkId `
            -ConfiguredPending:($AttachmentState -eq 'configured-pending') `
            -UnsafeAlias:($AttachmentState -eq 'unsafe')
    }
    return [ordered]@{
        Id = $postgresId
        Name = '/weatherpoly-postgres-1'
        Image = $postgresImageId
        Config = [ordered]@{
            Image = $postgresImage
            Entrypoint = @('docker-entrypoint.sh')
            Cmd = @(
                'postgres', '-c',
                'hba_file=/etc/postgresql/stratus-pg_hba.conf',
                '-c', 'password_encryption=scram-sha-256'
            )
            Labels = [ordered]@{
                'com.docker.compose.project' = 'weatherpoly'
                'com.docker.compose.project.working_dir' = $fixtureRoot
                'com.docker.compose.service' = 'postgres'
                'com.docker.compose.config-hash' = if ($ConfigDrift) {
                    'f' * 64
                }
                else {
                    'bb02ff39b208731cad693da86ac0f83e76aab0b548eca9a0ae310d35ed2576c1'
                }
                'com.docker.compose.image' = $postgresImageId
                'com.docker.compose.oneoff' = 'False'
                'com.docker.compose.container-number' = '1'
                'com.docker.compose.project.config_files' = @(
                    (Join-Path $fixtureRoot 'docker-compose.yml'),
                    (Join-Path $fixtureRoot 'docker-compose.override.yml')
                ) -join ','
            }
        }
        State = [ordered]@{
            Status = 'exited'
            Running = $false
            Paused = $false
            Restarting = $false
            OOMKilled = $false
            Dead = $false
            Pid = 0
            ExitCode = 0
            Error = ''
            StartedAt = '2026-08-22T19:57:00.0000000Z'
            FinishedAt = '2026-08-22T20:02:00.0000000Z'
        }
        RestartCount = 0
        HostConfig = [ordered]@{
            RestartPolicy = [ordered]@{
                Name = 'unless-stopped'
                MaximumRetryCount = 0
            }
            NetworkMode = 'weatherpoly_default'
            PortBindings = [ordered]@{
                '5432/tcp' = @([ordered]@{
                    HostIp = '127.0.0.1'
                    HostPort = '5432'
                })
            }
        }
        Mounts = @(
            [ordered]@{
                Type = 'bind'
                Source = (Join-Path $fixtureRoot 'ops\postgres\stratus-pg_hba.conf')
                Destination = '/etc/postgresql/stratus-pg_hba.conf'
                Mode = 'ro'
                RW = $false
                Propagation = 'rprivate'
            },
            [ordered]@{
                Type = 'volume'
                Name = 'weatherpoly_stratus-postgres'
                Driver = 'local'
                Source = '/var/lib/docker/volumes/weatherpoly_stratus-postgres/_data'
                Destination = '/var/lib/postgresql/data'
                Mode = 'rw'
                RW = $true
                Propagation = ''
            }
        )
        NetworkSettings = [ordered]@{
            Ports = [ordered]@{
                '5432/tcp' = @([ordered]@{
                    HostIp = '127.0.0.1'
                    HostPort = '5432'
                })
            }
            Networks = $networks
        }
    }
}

function New-OtherInspect {
    param(
        [Parameter(Mandatory = $true)][string] $Id,
        [Parameter(Mandatory = $true)][string] $Name,
        [Parameter(Mandatory = $true)][string] $Service,
        [switch] $Drift
    )

    return [ordered]@{
        Id = $Id
        Name = "/$Name"
        Config = [ordered]@{
            Labels = [ordered]@{
                'com.docker.compose.project' = 'weatherpoly'
                'com.docker.compose.service' = $Service
            }
        }
        State = [ordered]@{
            Status = 'exited'
            Running = $false
            OOMKilled = $false
            StartedAt = '2026-08-20T10:00:00.0000000Z'
            FinishedAt = if ($Drift) {
                '2026-08-20T10:05:01.0000000Z'
            }
            else {
                '2026-08-20T10:05:00.0000000Z'
            }
        }
        RestartCount = if ($Service -eq 'worker') { 8 } else { 0 }
    }
}

function New-NetworkInspect {
    param(
        [Parameter(Mandatory = $true)][string] $Name,
        [Parameter(Mandatory = $true)][string] $Id,
        [Parameter(Mandatory = $true)][string] $ComposeName,
        [Parameter(Mandatory = $true)][string] $Subnet,
        [Parameter(Mandatory = $true)][string] $Gateway
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
                IPRange = ''
                Gateway = $Gateway
                AuxiliaryAddresses = $null
            })
        }
        Internal = $false
        Attachable = $false
        Ingress = $false
        ConfigFrom = [ordered]@{ Network = '' }
        ConfigOnly = $false
        Containers = [pscustomobject]@{}
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

function Write-FixtureJson {
    param(
        [Parameter(Mandatory = $true)][string] $Name,
        [Parameter(Mandatory = $true)][object] $Value
    )

    [IO.File]::WriteAllText(
        (Join-Path $fixtureData $Name),
        ($Value | ConvertTo-Json -Depth 20 -Compress),
        $utf8
    )
}

Write-FixtureJson -Name 'postgres-default.json' `
    -Value (New-PostgresInspect -AttachmentState default)
Write-FixtureJson -Name 'postgres-exact.json' `
    -Value (New-PostgresInspect -AttachmentState exact)
Write-FixtureJson -Name 'postgres-configured-pending.json' `
    -Value (New-PostgresInspect -AttachmentState 'configured-pending')
$pendingNullNetworkId = New-PostgresInspect -AttachmentState 'configured-pending'
$pendingNullNetworkId.NetworkSettings.Networks['weatherpoly_safety-plane'].NetworkID = $null
Write-FixtureJson -Name 'postgres-configured-pending-null-network-id.json' `
    -Value $pendingNullNetworkId
$pendingBadDriverOptions = New-PostgresInspect -AttachmentState 'configured-pending'
$pendingBadDriverOptions.NetworkSettings.Networks['weatherpoly_safety-plane'].DriverOpts =
    [ordered]@{ x = 'unexpected' }
Write-FixtureJson -Name 'postgres-configured-pending-bad-driver-options.json' `
    -Value $pendingBadDriverOptions
$pendingDefaultNetwork = New-PostgresInspect -AttachmentState 'configured-pending'
$pendingDefaultNetwork.NetworkSettings.Networks.weatherpoly_default.IPAMConfig = [ordered]@{}
$pendingDefaultNetwork.NetworkSettings.Networks.weatherpoly_default.DriverOpts = [ordered]@{}
$pendingDefaultNetwork.NetworkSettings.Networks.weatherpoly_default.NetworkID = ''
Write-FixtureJson -Name 'postgres-configured-pending-default-network.json' `
    -Value $pendingDefaultNetwork
$pendingRuntimeEndpoint = New-PostgresInspect -AttachmentState 'configured-pending'
$pendingRuntimeEndpoint.NetworkSettings.Networks['weatherpoly_safety-plane'].EndpointID =
    'unexpected-runtime-endpoint'
Write-FixtureJson -Name 'postgres-configured-pending-runtime-endpoint.json' `
    -Value $pendingRuntimeEndpoint
$pendingWrongDns = New-PostgresInspect -AttachmentState 'configured-pending'
$pendingWrongDns.NetworkSettings.Networks['weatherpoly_safety-plane'].DNSNames =
    [string[]]@('postgres', 'weatherpoly-postgres-1', 'wrong-short-id')
Write-FixtureJson -Name 'postgres-configured-pending-wrong-dns.json' `
    -Value $pendingWrongDns
Write-FixtureJson -Name 'postgres-unsafe.json' `
    -Value (New-PostgresInspect -AttachmentState unsafe)
Write-FixtureJson -Name 'postgres-drift.json' `
    -Value (New-PostgresInspect -AttachmentState default -ConfigDrift)
$nativeEntrypoint = New-PostgresInspect -AttachmentState default
$nativeEntrypoint.Config.Entrypoint = 'docker-entrypoint.sh'
Write-FixtureJson -Name 'native-postgres-entrypoint.json' -Value $nativeEntrypoint
$nativePaused = New-PostgresInspect -AttachmentState default
$nativePaused.State.Paused = 'false'
Write-FixtureJson -Name 'native-postgres-paused.json' -Value $nativePaused
$nativePortObject = New-PostgresInspect -AttachmentState default
$nativePortObject.HostConfig.PortBindings.'5432/tcp' = [ordered]@{
    HostIp = '127.0.0.1'; HostPort = '5432'
}
Write-FixtureJson -Name 'native-postgres-port-object.json' -Value $nativePortObject
$nativePortNumber = New-PostgresInspect -AttachmentState default
$nativePortNumber.HostConfig.PortBindings.'5432/tcp'[0].HostPort = 5432
Write-FixtureJson -Name 'native-postgres-port-number.json' -Value $nativePortNumber
$nativeMountsObject = New-PostgresInspect -AttachmentState default
$nativeMountsObject.Mounts = [ordered]@{ unexpected = 'object' }
Write-FixtureJson -Name 'native-postgres-mounts-object.json' -Value $nativeMountsObject
$nativeMountRw = New-PostgresInspect -AttachmentState default
$nativeMountRw.Mounts[0].RW = 'false'
Write-FixtureJson -Name 'native-postgres-mount-rw.json' -Value $nativeMountRw
$nativeLabelNumber = New-PostgresInspect -AttachmentState default
$nativeLabelNumber.Config.Labels.'com.docker.compose.container-number' = 1
Write-FixtureJson -Name 'native-postgres-label-number.json' -Value $nativeLabelNumber
Write-FixtureJson -Name 'api.json' `
    -Value (New-OtherInspect -Id $apiId -Name 'weatherpoly-api-1' -Service api)
Write-FixtureJson -Name 'api-drift.json' `
    -Value (New-OtherInspect -Id $apiId -Name 'weatherpoly-api-1' -Service api -Drift)
Write-FixtureJson -Name 'worker.json' `
    -Value (New-OtherInspect -Id $workerId -Name 'weatherpoly-worker-1' -Service worker)
Write-FixtureJson -Name 'network-default.json' -Value (
    New-NetworkInspect -Name 'weatherpoly_default' -Id $defaultNetworkId `
        -ComposeName default -Subnet '172.20.0.0/16' -Gateway '172.20.0.1'
)
Write-FixtureJson -Name 'network-safety.json' -Value (
    New-NetworkInspect -Name 'weatherpoly_safety-plane' -Id $safetyNetworkId `
        -ComposeName safety-plane -Subnet '172.22.0.0/16' -Gateway '172.22.0.1'
)
Write-FixtureJson -Name 'network-default-drift.json' -Value (
    New-NetworkInspect -Name 'weatherpoly_wrong' -Id $defaultNetworkId `
        -ComposeName default -Subnet '172.20.0.0/16' -Gateway '172.20.0.1'
)
$nativeNetworkIpamObject = New-NetworkInspect -Name 'weatherpoly_default' `
    -Id $defaultNetworkId -ComposeName default -Subnet '172.20.0.0/16' `
    -Gateway '172.20.0.1'
$nativeNetworkIpamObject.IPAM.Config = $nativeNetworkIpamObject.IPAM.Config[0]
Write-FixtureJson -Name 'native-network-ipam-object.json' -Value $nativeNetworkIpamObject
Write-FixtureJson -Name 'endpoint.json' -Value ([ordered]@{
    Host = 'npipe:////./pipe/dockerDesktopLinuxEngine'
    SkipTLSVerify = $false
})
Write-FixtureJson -Name 'endpoint-drift.json' -Value ([ordered]@{
    Host = 'npipe:////./pipe/notProductionDocker'
    SkipTLSVerify = $false
})
Write-FixtureJson -Name 'native-endpoint-skip-string.json' -Value ([ordered]@{
    Host = 'npipe:////./pipe/dockerDesktopLinuxEngine'
    SkipTLSVerify = 'false'
})
Write-FixtureJson -Name 'engine.json' -Value ([ordered]@{
    ID = 'd98e03f5-06d7-4af4-9f21-b6f7a70ebf68'
    ServerVersion = '28.3.2'
    Name = 'docker-desktop'
    DockerRootDir = '/var/lib/docker'
})
Write-FixtureJson -Name 'engine-drift.json' -Value ([ordered]@{
    ID = 'FIXTURE-ENGINE-DRIFT'
    ServerVersion = '28.3.2'
    Name = 'docker-desktop'
    DockerRootDir = '/var/lib/docker'
})

$runningOnWindows = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
$dockerFileName = if ($runningOnWindows) { 'docker.exe' } else { 'docker' }
$dockerPath = Join-Path $fakeBin $dockerFileName
$mockTypeName = 'StratusNetworkRepairDockerMock' + [Guid]::NewGuid().ToString('N')
$dockerSource = @"
using System;
using System.IO;
using System.Text;
using System.Threading;

public static class $mockTypeName
{
    static string Env(string name)
    {
        return Environment.GetEnvironmentVariable(name);
    }

    static string Fixture(string name)
    {
        return File.ReadAllText(Path.Combine(Env("STRATUS_REPAIR_FIXTURE_DATA"), name));
    }

    static int SnapshotNumber()
    {
        string path = Env("STRATUS_REPAIR_SNAPSHOT_FILE");
        int value = 0;
        if (File.Exists(path)) Int32.TryParse(File.ReadAllText(path), out value);
        return value;
    }

    static void SetSnapshotNumber(int value)
    {
        File.WriteAllText(Env("STRATUS_REPAIR_SNAPSHOT_FILE"), value.ToString());
    }

    static void Emit(string fixture)
    {
        Console.Write(Fixture(fixture));
    }

    static bool Exact(string[] args, params string[] expected)
    {
        if (args.Length != expected.Length) return false;
        for (int i = 0; i < args.Length; i++)
            if (!String.Equals(args[i], expected[i], StringComparison.Ordinal)) return false;
        return true;
    }

    static void AppendCommandLog(string line)
    {
        for (int attempt = 0; attempt < 500; attempt++)
        {
            try
            {
                File.AppendAllText(Env("STRATUS_REPAIR_COMMAND_LOG"), line);
                return;
            }
            catch (IOException)
            {
                Thread.Sleep(10);
            }
        }
        throw new IOException("fake Docker command ledger remained locked");
    }

    static long NextTimeNano()
    {
        string path = Env("STRATUS_REPAIR_EVENT_SEQUENCE_FILE");
        long value = 1786200000000000000L;
        if (File.Exists(path)) Int64.TryParse(File.ReadAllText(path), out value);
        value += 1000L;
        File.WriteAllText(path, value.ToString());
        return value;
    }

    static string EventLine(string type, string action, string actor, string attributes)
    {
        long timeNano = NextTimeNano();
        long time = timeNano / 1000000000L;
        return
            "{\"type\":\"" + type + "\",\"action\":\"" + action +
            "\",\"actor_id\":\"" + actor + "\",\"attributes\":" + attributes +
            ",\"scope\":\"local\",\"time\":" + time.ToString() +
            ",\"time_nano\":" + timeNano.ToString() + "}\n";
    }

    static void AppendEvent(string type, string action, string actor, string attributes)
    {
        File.AppendAllText(
            Env("STRATUS_REPAIR_EVENT_FEED"),
            EventLine(type, action, actor, attributes),
            new UTF8Encoding(false)
        );
    }

    static bool WaitForEventObserver(string scenario)
    {
        if (scenario == "event-observer-exit") return true;
        string ready = Env("STRATUS_REPAIR_EVENT_READY_FILE");
        for (int index = 0; index < 1500 && !File.Exists(ready); index++)
            Thread.Sleep(10);
        return File.Exists(ready);
    }

    static int StreamEvents(string scenario)
    {
        if (scenario == "event-observer-exit") return 0;
        string feed = Env("STRATUS_REPAIR_EVENT_FEED");
        using (FileStream stream = new FileStream(
            feed, FileMode.OpenOrCreate, FileAccess.Read, FileShare.ReadWrite
        ))
        {
            long position = stream.Length;
            File.WriteAllText(
                Env("STRATUS_REPAIR_EVENT_PID_FILE"),
                System.Diagnostics.Process.GetCurrentProcess().Id.ToString()
            );
            if (scenario == "event-observer-stderr")
            {
                Console.Error.Write("synthetic-event-stderr");
                Console.Error.Flush();
                Thread.Sleep(250);
            }
            File.WriteAllText(Env("STRATUS_REPAIR_EVENT_READY_FILE"), "ready");
            Stream output = Console.OpenStandardOutput();
            byte[] buffer = new byte[4096];
            bool lateHealthPublished = false;
            while (true)
            {
                if (
                    scenario == "event-late-health-stderr" &&
                    !lateHealthPublished &&
                    File.Exists(Env("STRATUS_REPAIR_EVENT_HEALTH_TRIGGER_FILE"))
                )
                {
                    Console.Error.Write("synthetic-late-event-stderr");
                    Console.Error.Flush();
                    lateHealthPublished = true;
                    File.WriteAllText(
                        Env("STRATUS_REPAIR_EVENT_HEALTH_ACK_FILE"), "published"
                    );
                }
                long length = stream.Length;
                if (length > position)
                {
                    stream.Position = position;
                    int count = stream.Read(
                        buffer, 0, (int)Math.Min((long)buffer.Length, length - position)
                    );
                    if (count > 0)
                    {
                        output.Write(buffer, 0, count);
                        output.Flush();
                        position += count;
                    }
                }
                else Thread.Sleep(10);
            }
        }
    }

    public static int Main(string[] args)
    {
        AppendCommandLog(String.Join("\u001f", args) + Environment.NewLine);
        string scenario = Env("STRATUS_REPAIR_SCENARIO") ?? "default-only";
        int snapshot = SnapshotNumber();

        if (args.Length < 2 || args[0] != "--context" || args[1] != "desktop-linux")
            return 91;
        string[] effective = new string[args.Length - 2];
        Array.Copy(args, 2, effective, 0, effective.Length);
        args = effective;

        if (Exact(
            args, "events", "--format",
            "{\"type\":{{json .Type}},\"action\":{{json .Action}}," +
            "\"actor_id\":{{json .Actor.ID}},\"attributes\":{{json .Actor.Attributes}}," +
            "\"scope\":{{json .Scope}},\"time\":{{.Time}},\"time_nano\":{{.TimeNano}}}"
        ))
            return StreamEvents(scenario);

        if (Exact(args, "volume", "create", "--driver", "local"))
        {
          try
          {
            if (!WaitForEventObserver(scenario)) return 92;
            string barrier = new String('e', 64);
            if (scenario == "event-malformed-create")
                File.AppendAllText(Env("STRATUS_REPAIR_EVENT_FEED"), "{\"type\":\n");
            else if (scenario == "event-native-time-string")
                File.AppendAllText(
                    Env("STRATUS_REPAIR_EVENT_FEED"),
                    "{\"type\":\"volume\",\"action\":\"create\",\"actor_id\":\"" +
                    barrier + "\",\"attributes\":{\"driver\":\"local\"},\"scope\":\"local\"," +
                    "\"time\":\"1786200000\",\"time_nano\":1786200000000001000}\n"
                );
            else if (scenario == "event-native-attributes-array")
                File.AppendAllText(
                    Env("STRATUS_REPAIR_EVENT_FEED"),
                    "{\"type\":\"volume\",\"action\":\"create\",\"actor_id\":\"" +
                    barrier + "\",\"attributes\":[],\"scope\":\"local\"," +
                    "\"time\":1786200000,\"time_nano\":1786200000000001000}\n"
                );
            else if (scenario == "event-duplicate-key")
                File.AppendAllText(
                    Env("STRATUS_REPAIR_EVENT_FEED"),
                    "{\"type\":\"volume\",\"type\":\"volume\",\"action\":\"create\"," +
                    "\"actor_id\":\"" + barrier + "\",\"attributes\":{\"driver\":\"local\"}," +
                    "\"scope\":\"local\",\"time\":1786200000," +
                    "\"time_nano\":1786200000000001000}\n"
                );
            else if (scenario == "event-crlf")
                File.AppendAllText(
                    Env("STRATUS_REPAIR_EVENT_FEED"),
                    "{\"type\":\"volume\",\"action\":\"create\",\"actor_id\":\"" +
                    barrier + "\",\"attributes\":{\"driver\":\"local\"},\"scope\":\"local\"," +
                    "\"time\":1786200000,\"time_nano\":1786200000000001000}\r\n"
                );
            else if (scenario == "event-output-overflow")
                File.AppendAllText(
                    Env("STRATUS_REPAIR_EVENT_FEED"), new String('x', 70000)
                );
            else if (scenario != "event-missing-create")
                AppendEvent("volume", "create", barrier, "{\"driver\":\"local\"}");
            if (scenario == "event-spurious-connect")
                AppendEvent(
                    "network", "connect", "$safetyNetworkId",
                    "{\"container\":\"$postgresId\",\"name\":\"weatherpoly_safety-plane\",\"type\":\"bridge\"}"
                );
            Console.WriteLine(barrier);
            return 0;
          }
          catch (Exception error)
          {
            File.WriteAllText(Env("STRATUS_REPAIR_FAKE_ERROR"), error.ToString());
            return 96;
          }
        }
        if (args.Length == 3 && args[0] == "volume" && args[1] == "rm")
        {
            string barrier = args[2];
            if (scenario == "event-wrong-destroy")
                AppendEvent("volume", "destroy", new String('f', 64), "{\"driver\":\"local\"}");
            else if (scenario == "event-duplicate-destroy")
                File.AppendAllText(
                    Env("STRATUS_REPAIR_EVENT_FEED"),
                    EventLine("volume", "destroy", barrier, "{\"driver\":\"local\"}") +
                    EventLine("volume", "destroy", barrier, "{\"driver\":\"local\"}")
                );
            else if (scenario == "event-extra-after-destroy")
                File.AppendAllText(
                    Env("STRATUS_REPAIR_EVENT_FEED"),
                    EventLine("container", "create", new String('9', 64),
                        "{\"image\":\"evil\",\"name\":\"operator\"}") +
                    EventLine("volume", "destroy", barrier, "{\"driver\":\"local\"}")
                );
            else if (scenario == "event-partial-final")
                File.AppendAllText(
                    Env("STRATUS_REPAIR_EVENT_FEED"),
                    EventLine("volume", "destroy", barrier, "{\"driver\":\"local\"}") +
                    "{\"type\":"
                );
            else if (scenario != "event-missing-destroy")
                AppendEvent("volume", "destroy", barrier, "{\"driver\":\"local\"}");
            Console.WriteLine(barrier);
            return 0;
        }

        if (Exact(args, "context", "show"))
        {
            snapshot += 1;
            SetSnapshotNumber(snapshot);
            Console.WriteLine(scenario == "context-drift" ? "fixture-context" : "desktop-linux");
            return 0;
        }
        if (
            Exact(args, "context", "inspect", "desktop-linux", "--format", "{{json .Endpoints.docker}}") ||
            Exact(args, "context", "inspect", "fixture-context", "--format", "{{json .Endpoints.docker}}")
        )
        {
            if (scenario == "endpoint-drift") Emit("endpoint-drift.json");
            else if (scenario == "native-endpoint-skip-string")
                Emit("native-endpoint-skip-string.json");
            else Emit("endpoint.json");
            return 0;
        }
        if (Exact(args, "info", "--format", "{{json .}}"))
        {
            if (scenario == "info-hang")
            {
                Thread.Sleep(30000);
                return 0;
            }
            if (scenario == "info-fail")
            {
                Console.Error.WriteLine("synthetic-info-failure");
                return 29;
            }
            Emit(scenario == "engine-drift" && snapshot >= 2 ? "engine-drift.json" : "engine.json");
            return 0;
        }
        if (Exact(args, "ps", "-q", "--no-trunc"))
        {
            if (scenario == "transient-lifecycle")
                AppendEvent("container", "create", new String('9', 64), "{\"image\":\"evil\",\"name\":\"migrate\"}");
            if (scenario == "event-partial-before-mutation" && snapshot == 2)
                File.AppendAllText(Env("STRATUS_REPAIR_EVENT_FEED"), "{\"type\":");
            if (scenario == "running") Console.WriteLine("$apiId");
            return 0;
        }
        if (Exact(args, "ps", "-a", "-q", "--no-trunc"))
        {
            Console.WriteLine("$postgresId");
            Console.WriteLine("$apiId");
            Console.WriteLine("$workerId");
            return 0;
        }
        if (args.Length == 4 && args[0] == "inspect" && args[2] == "--format" && args[3] == "{{json .}}")
        {
            if (args[1] == "$postgresId")
            {
                string state = File.ReadAllText(Env("STRATUS_REPAIR_STATE_FILE")).Trim();
                if (scenario == "pg-drift") Emit("postgres-drift.json");
                else if (scenario.StartsWith("native-postgres-", StringComparison.Ordinal))
                    Emit(scenario + ".json");
                else if (state == "exact") Emit("postgres-exact.json");
                else if (state.StartsWith("configured-pending", StringComparison.Ordinal))
                    Emit("postgres-" + state + ".json");
                else if (state == "unsafe") Emit("postgres-unsafe.json");
                else Emit("postgres-default.json");
                return 0;
            }
            if (args[1] == "$apiId")
            {
                bool drift =
                    (scenario == "armed-lifecycle-drift" && snapshot >= 3) ||
                    (scenario == "post-second-lifecycle-drift" && snapshot >= 5);
                Emit(drift ? "api-drift.json" : "api.json");
                return 0;
            }
            if (args[1] == "$workerId")
            {
                Emit("worker.json");
                return 0;
            }
            return 87;
        }
        if (args.Length == 5 && args[0] == "network" && args[1] == "inspect" && args[3] == "--format" && args[4] == "{{json .}}")
        {
            if (args[2] == "$defaultNetworkId")
            {
                if (scenario == "network-drift") Emit("network-default-drift.json");
                else if (scenario == "native-network-ipam-object")
                    Emit("native-network-ipam-object.json");
                else Emit("network-default.json");
                return 0;
            }
            if (args[2] == "$safetyNetworkId")
            {
                Emit("network-safety.json");
                return 0;
            }
            return 88;
        }
        if (Exact(
            args,
            "network", "connect",
            "--alias", "postgres",
            "--alias", "weatherpoly-postgres-1",
            "--gw-priority", "0",
            "$safetyNetworkId",
            "$postgresId"
        ))
        {
            if (scenario != "event-missing-connect")
            {
                string networkActor = scenario == "event-wrong-connect"
                    ? "$defaultNetworkId" : "$safetyNetworkId";
                AppendEvent(
                    "network", "connect", networkActor,
                    "{\"container\":\"$postgresId\",\"name\":\"weatherpoly_safety-plane\",\"type\":\"bridge\"}"
                );
                if (scenario == "event-duplicate-connect")
                    AppendEvent(
                        "network", "connect", "$safetyNetworkId",
                        "{\"container\":\"$postgresId\",\"name\":\"weatherpoly_safety-plane\",\"type\":\"bridge\"}"
                    );
            }
            if (scenario != "connect-no-effect")
                File.WriteAllText(
                    Env("STRATUS_REPAIR_STATE_FILE"),
                    scenario == "connect-materialized" ? "exact" : "configured-pending"
                );
            if (scenario == "connect-fail")
            {
                Console.Error.WriteLine("synthetic-connect-failure");
                return 41;
            }
            if (scenario == "connect-output") Console.WriteLine("unexpected-output");
            return 0;
        }
        return 90;
    }
}
"@

if ($runningOnWindows) {
    $sourcePath = Join-Path $temporaryRoot 'DockerRepairMock.cs'
    [IO.File]::WriteAllText($sourcePath, $dockerSource, $utf8)
    $compiler = @(
        (Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'),
        (Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe')
    ) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
        Select-Object -First 1
    if ([string]::IsNullOrWhiteSpace([string]$compiler)) {
        throw 'Could not find the Windows C# compiler for the Docker repair fixture.'
    }
    $compilerOutput = @(
        & $compiler '/nologo' '/target:exe' "/out:$dockerPath" $sourcePath 2>&1
    )
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $dockerPath -PathType Leaf)) {
        throw (
            'Could not compile the Docker repair fixture: ' +
            ($compilerOutput -join [Environment]::NewLine)
        )
    }

    $fakeDockerSha256 = (Get-FileHash -LiteralPath $dockerPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $testRepairPath = Join-Path $temporaryRoot 'Invoke-StratusStoppedPostgresNetworkRepair.test.ps1'
    $approvedDockerSha256 =
        '388f231898cff8674a2aa341e306b77fd1f93a5954dd33ff556575a69227824d'
    $testRepairSource = [IO.File]::ReadAllText($repairPath)
    if (([regex]::Matches($testRepairSource, $approvedDockerSha256)).Count -ne 1) {
        throw 'Repair source does not contain exactly one approved Docker hash constant.'
    }
    $testRepairSource = $testRepairSource.Replace(
        $approvedDockerSha256,
        $fakeDockerSha256
    )
    [IO.File]::WriteAllText($testRepairPath, $testRepairSource, $utf8)
    $testSourceNewLine = if ($testRepairSource.Contains("`r`n")) { "`r`n" } else { "`n" }

    function New-InjectedRepairCopy {
        param(
            [Parameter(Mandatory = $true)][string] $LiteralPath,
            [Parameter(Mandatory = $true)][string] $Needle,
            [Parameter(Mandatory = $true)][string] $Replacement
        )

        $exactNeedle = $Needle.Replace("`r`n", "`n").Replace("`n", $testSourceNewLine)
        $exactReplacement = $Replacement.Replace("`r`n", "`n").Replace(
            "`n", $testSourceNewLine
        )
        if (([regex]::Matches(
            $testRepairSource,
            [regex]::Escape($exactNeedle)
        )).Count -ne 1) {
            throw "Repair fault-injection anchor is not unique for $LiteralPath"
        }
        [IO.File]::WriteAllText(
            $LiteralPath,
            $testRepairSource.Replace($exactNeedle, $exactReplacement),
            $utf8
        )
    }

    $collectorStartFailureRepairPath = Join-Path $temporaryRoot `
        'Invoke-StratusStoppedPostgresNetworkRepair.collector-start-failure.ps1'
    $collectorStartNeedle = '        stdoutThread = new Thread(delegate() {'
    $collectorStartReplacement = (
        '        if (Environment.GetEnvironmentVariable(' +
        '"STRATUS_REPAIR_SCENARIO") != null)' + $testSourceNewLine +
        '            throw new InvalidOperationException(' +
        '"synthetic collector.Start failure");' + $testSourceNewLine +
        $collectorStartNeedle
    )
    New-InjectedRepairCopy -LiteralPath $collectorStartFailureRepairPath `
        -Needle $collectorStartNeedle -Replacement $collectorStartReplacement

    $postStartIdentityFailureRepairPath = Join-Path $temporaryRoot `
        'Invoke-StratusStoppedPostgresNetworkRepair.post-start-identity-failure.ps1'
    $postStartIdentityNeedle = '        $after = Get-PinnedDockerApplicationBoundary'
    $postStartIdentityWait = @'
        $identityProbeDeadline = [DateTime]::UtcNow.AddSeconds(5)
        while (
            -not (Test-Path -LiteralPath $env:STRATUS_REPAIR_EVENT_READY_FILE) -and
            [DateTime]::UtcNow -lt $identityProbeDeadline
        ) {
            [Threading.Thread]::Sleep(10)
        }
'@
    $postStartIdentityReplacement = (
        $postStartIdentityNeedle + $testSourceNewLine +
        $postStartIdentityWait.Replace("`r`n", "`n").Replace("`n", $testSourceNewLine)
    )
    $postStartIdentitySource = $testRepairSource.Replace(
        $postStartIdentityNeedle,
        $postStartIdentityReplacement
    )
    $postStartConditionNeedle = '            $after.path -cne $docker.path -or'
    if (([regex]::Matches(
        $postStartIdentitySource,
        [regex]::Escape($postStartConditionNeedle)
    )).Count -ne 1) {
        throw 'Post-start identity failure condition anchor is not unique.'
    }
    $postStartIdentitySource = $postStartIdentitySource.Replace(
        $postStartConditionNeedle,
        '            $true -or' + $testSourceNewLine + $postStartConditionNeedle
    )
    [IO.File]::WriteAllText(
        $postStartIdentityFailureRepairPath,
        $postStartIdentitySource,
        $utf8
    )

    $lateHealthFailureRepairPath = Join-Path $temporaryRoot `
        'Invoke-StratusStoppedPostgresNetworkRepair.late-health-failure.ps1'
    $lateHealthNeedle = '    $stdoutBytesBefore = [int64]$Witness.collector.StdoutBytes'
    $lateHealthReplacement = @'
    $stdoutBytesBefore = [int64]$Witness.collector.StdoutBytes
    [IO.File]::WriteAllText(
        $env:STRATUS_REPAIR_EVENT_HEALTH_TRIGGER_FILE,
        'publish',
        [Text.UTF8Encoding]::new($false)
    )
    $healthAckDeadline = [DateTime]::UtcNow.AddSeconds(5)
    while (
        -not (Test-Path -LiteralPath $env:STRATUS_REPAIR_EVENT_HEALTH_ACK_FILE) -and
        [DateTime]::UtcNow -lt $healthAckDeadline
    ) {
        [Threading.Thread]::Sleep(10)
    }
    if (-not (Test-Path -LiteralPath $env:STRATUS_REPAIR_EVENT_HEALTH_ACK_FILE)) {
        throw 'Synthetic late event health publication was not acknowledged.'
    }
    [Threading.Thread]::Sleep(250)
'@
    New-InjectedRepairCopy -LiteralPath $lateHealthFailureRepairPath `
        -Needle $lateHealthNeedle -Replacement $lateHealthReplacement

    $cleanupFailureRepairPath = Join-Path $temporaryRoot `
        'Invoke-StratusStoppedPostgresNetworkRepair.cleanup-failure.ps1'
    $cleanupFailureNeedle =
        'try { Remove-RepairDockerEventCapture -Witness $eventWitness }'
    $cleanupFailureReplacement =
        "try { throw 'synthetic event capture cleanup failure' }"
    New-InjectedRepairCopy -LiteralPath $cleanupFailureRepairPath `
        -Needle $cleanupFailureNeedle -Replacement $cleanupFailureReplacement
}
else {
    throw 'This Windows production repair fixture requires the compiled fake Docker executable.'
}

$originalPath = $env:PATH
$dockerOverrides = [ordered]@{}
foreach ($name in @(
    'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH',
    'DOCKER_CONFIG', 'DOCKER_API_VERSION', 'DOCKER_CLI_PLUGIN_EXTRA_DIRS'
)) {
    $dockerOverrides[$name] = [Environment]::GetEnvironmentVariable($name)
    Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
}
$env:PATH = $fakeBin
$env:STRATUS_REPAIR_FIXTURE_DATA = $fixtureData
$stateFile = Join-Path $temporaryRoot 'state.txt'
$snapshotFile = Join-Path $temporaryRoot 'snapshot.txt'
$commandLog = Join-Path $temporaryRoot 'commands.log'
$eventFeed = Join-Path $temporaryRoot 'events.jsonl'
$eventSequenceFile = Join-Path $temporaryRoot 'event-sequence.txt'
$eventReadyFile = Join-Path $temporaryRoot 'event-ready.txt'
$eventPidFile = Join-Path $temporaryRoot 'event-pid.txt'
$eventHealthTriggerFile = Join-Path $temporaryRoot 'event-health-trigger.txt'
$eventHealthAckFile = Join-Path $temporaryRoot 'event-health-ack.txt'
$fakeErrorFile = Join-Path $temporaryRoot 'fake-error.txt'
$env:STRATUS_REPAIR_STATE_FILE = $stateFile
$env:STRATUS_REPAIR_SNAPSHOT_FILE = $snapshotFile
$env:STRATUS_REPAIR_COMMAND_LOG = $commandLog
$env:STRATUS_REPAIR_EVENT_FEED = $eventFeed
$env:STRATUS_REPAIR_EVENT_SEQUENCE_FILE = $eventSequenceFile
$env:STRATUS_REPAIR_EVENT_READY_FILE = $eventReadyFile
$env:STRATUS_REPAIR_EVENT_PID_FILE = $eventPidFile
$env:STRATUS_REPAIR_EVENT_HEALTH_TRIGGER_FILE = $eventHealthTriggerFile
$env:STRATUS_REPAIR_EVENT_HEALTH_ACK_FILE = $eventHealthAckFile
$env:STRATUS_REPAIR_FAKE_ERROR = $fakeErrorFile

function Reset-Scenario {
    param(
        [Parameter(Mandatory = $true)][string] $Scenario,
        [ValidateSet(
            'default', 'configured-pending',
            'configured-pending-null-network-id',
            'configured-pending-bad-driver-options',
            'configured-pending-default-network',
            'configured-pending-runtime-endpoint',
            'configured-pending-wrong-dns',
            'exact', 'unsafe'
        )]
        [string] $AttachmentState = 'default'
    )

    $env:STRATUS_REPAIR_SCENARIO = $Scenario
    [IO.File]::WriteAllText($stateFile, $AttachmentState, $utf8)
    [IO.File]::WriteAllText($snapshotFile, '0', $utf8)
    [IO.File]::WriteAllText($commandLog, '', $utf8)
    [IO.File]::WriteAllText($eventFeed, '', $utf8)
    [IO.File]::WriteAllText($eventSequenceFile, '1786200000000000000', $utf8)
    if (Test-Path -LiteralPath $eventReadyFile) {
        Remove-Item -LiteralPath $eventReadyFile -Force
    }
    if (Test-Path -LiteralPath $eventPidFile) {
        Remove-Item -LiteralPath $eventPidFile -Force
    }
    foreach ($healthPath in @($eventHealthTriggerFile, $eventHealthAckFile)) {
        if (Test-Path -LiteralPath $healthPath) {
            Remove-Item -LiteralPath $healthPath -Force
        }
    }
    if (Test-Path -LiteralPath $fakeErrorFile) {
        Remove-Item -LiteralPath $fakeErrorFile -Force
    }
}

function Invoke-Repair {
    param(
        [Parameter(Mandatory = $true)][string] $Evidence,
        [string] $ScriptPath = $testRepairPath
    )

    if (Test-Path -LiteralPath $eventReadyFile) {
        Remove-Item -LiteralPath $eventReadyFile -Force
    }
    try {
        return & $ScriptPath -Root $fixtureRoot -EvidencePath $Evidence
    }
    catch {
        $fakeError = if (Test-Path -LiteralPath $fakeErrorFile) {
            [IO.File]::ReadAllText($fakeErrorFile)
        }
        else { '' }
        throw (
            "Repair fixture $($env:STRATUS_REPAIR_SCENARIO) at $Evidence failed: " +
            "$($_.Exception.Message) $fakeError"
        )
    }
}

function Assert-NoEventCaptureResidue {
    $residue = @(Get-ChildItem -LiteralPath $evidenceRoot -Force -Directory |
        Where-Object { $_.Name -like '.stratus-repair-event-witness-*' })
    if ($residue.Count -ne 0) {
        throw (
            'Docker event witness left private capture residue: ' +
            (@($residue.FullName) -join ', ')
        )
    }
}

function Assert-RecordedEventObserverStopped {
    param([switch] $RequireRecordedProcess)

    if (-not (Test-Path -LiteralPath $eventPidFile -PathType Leaf)) {
        if ($RequireRecordedProcess) {
            throw 'Docker event witness did not record its fake observer process ID.'
        }
        return
    }
    $observerProcessIdValue = 0
    if (-not [int]::TryParse(
        [IO.File]::ReadAllText($eventPidFile).Trim(),
        [ref]$observerProcessIdValue
    ) -or $observerProcessIdValue -le 0) {
        throw 'Docker event witness recorded an invalid fake observer process ID.'
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(8)
    do {
        $candidate = Get-Process -Id $observerProcessIdValue `
            -ErrorAction SilentlyContinue
        if ($null -eq $candidate) {
            return
        }
        [Threading.Thread]::Sleep(25)
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Docker event witness observer remained alive: $observerProcessIdValue"
}

function Get-CommandRows {
    if (-not (Test-Path -LiteralPath $commandLog -PathType Leaf)) {
        return @()
    }
    $rows = [Collections.Generic.List[string]]::new()
    foreach ($row in @(Get-Content -LiteralPath $commandLog | Where-Object {
        -not [string]::IsNullOrWhiteSpace([string]$_)
    })) {
        $parts = @([string]$row -split ([char]0x1f))
        if (
            $parts.Count -lt 3 -or
            $parts[0] -cne '--context' -or
            $parts[1] -cne 'desktop-linux'
        ) {
            throw "Docker fixture observed an invocation without the pinned context: $row"
        }
        $rows.Add((@($parts[2..($parts.Count - 1)]) -join ([char]0x1f)))
    }
    return @($rows.ToArray())
}

function Assert-NoForbiddenDockerMutation {
    foreach ($row in @(Get-CommandRows)) {
        $arguments = @($row -split ([char]0x1f))
        if (
            $arguments[0] -in @('start', 'stop', 'restart', 'rm', 'run', 'exec') -or
            ($arguments[0] -ceq 'volume' -and -not (
                ($arguments.Count -eq 4 -and $arguments[1] -ceq 'create' -and
                    $arguments[2] -ceq '--driver' -and $arguments[3] -ceq 'local') -or
                ($arguments.Count -eq 3 -and $arguments[1] -ceq 'rm' -and
                    $arguments[2] -cmatch '^[0-9a-f]{64}$')
            )) -or
            ($arguments.Count -ge 2 -and
                $arguments[0] -ceq 'network' -and
                $arguments[1] -ceq 'disconnect') -or
            ($arguments.Count -ge 2 -and
                $arguments[0] -ceq 'network' -and
                $arguments[1] -ceq 'connect' -and
                ($arguments -join "`n") -cne
                    ($allowedNetworkConnectArguments -join "`n"))
        ) {
            throw "Fixture observed a forbidden Docker mutation: $row"
        }
    }
}

function Assert-ZeroBytePartialEvidence {
    param([Parameter(Mandatory = $true)][string] $Path)

    if (
        -not (Test-Path -LiteralPath $Path -PathType Leaf) -or
        (Get-Item -LiteralPath $Path).Length -ne 0
    ) {
        throw 'Failed repair must leave only a zero-byte non-authorizing partial evidence file.'
    }
}

function Assert-ExactEventWitness {
    param(
        [Parameter(Mandatory = $true)][object] $Evidence,
        [Parameter(Mandatory = $true)][bool] $MutationIssued
    )

    $witness = $Evidence.payload.event_witness
    $events = @($witness.events)
    $expectedCount = if ($MutationIssued) { 3 } else { 2 }
    $expectedCommand = @(
        '--context', 'desktop-linux', 'events', '--format',
        (
            '{"type":{{json .Type}},"action":{{json .Action}},' +
            '"actor_id":{{json .Actor.ID}},"attributes":{{json .Actor.Attributes}},' +
            '"scope":{{json .Scope}},"time":{{.Time}},"time_nano":{{.TimeNano}}}'
        )
    )
    if (
        $witness.format -isnot [string] -or
        $witness.session_id -isnot [string] -or
        $witness.barrier_volume_name -isnot [string] -or
        ($witness.event_count -isnot [int] -and $witness.event_count -isnot [long]) -or
        ($witness.journal_bytes -isnot [int] -and $witness.journal_bytes -isnot [long]) -or
        $witness.journal_sha256 -isnot [string] -or
        $witness.events_sha256 -isnot [string] -or
        ($witness.network_connect_count -isnot [int] -and
            $witness.network_connect_count -isnot [long]) -or
        ($witness.stderr_bytes -isnot [int] -and $witness.stderr_bytes -isnot [long]) -or
        $witness.truncated -isnot [bool] -or
        $witness.eof_before_seal -isnot [bool] -or
        ($witness.unexpected_event_count -isnot [int] -and
            $witness.unexpected_event_count -isnot [long]) -or
        $witness.pre_barrier.actor_id -isnot [string] -or
        $witness.final_barrier.actor_id -isnot [string] -or
        ($witness.pre_barrier.time -isnot [int] -and
            $witness.pre_barrier.time -isnot [long]) -or
        ($witness.pre_barrier.time_nano -isnot [int] -and
            $witness.pre_barrier.time_nano -isnot [long]) -or
        ($witness.final_barrier.time -isnot [int] -and
            $witness.final_barrier.time -isnot [long]) -or
        ($witness.final_barrier.time_nano -isnot [int] -and
            $witness.final_barrier.time_nano -isnot [long]) -or
        ($witness.authority_as_of_time_nano -isnot [int] -and
            $witness.authority_as_of_time_nano -isnot [long]) -or
        $witness.format -cne 'stratus-stopped-repair-docker-event-witness-v1' -or
        $witness.session_id -cnotmatch '^[0-9a-f]{32}$' -or
        $witness.barrier_volume_name -cnotmatch '^[0-9a-f]{64}$' -or
        $witness.pre_barrier.actor_id -cne $witness.barrier_volume_name -or
        $witness.final_barrier.actor_id -cne $witness.barrier_volume_name -or
        [int64]$witness.authority_as_of_time_nano -ne
            [int64]$witness.final_barrier.time_nano -or
        (@($witness.event_command) -join "`n") -cne ($expectedCommand -join "`n") -or
        $events.Count -ne $expectedCount -or
        [int]$witness.event_count -ne $expectedCount -or
        [int]$witness.network_connect_count -ne $(if ($MutationIssued) { 1 } else { 0 }) -or
        ($MutationIssued -and $witness.network_connect_time_nano -isnot [long]) -or
        (-not $MutationIssued -and $null -ne $witness.network_connect_time_nano) -or
        [int64]$witness.stderr_bytes -ne 0 -or
        [bool]$witness.truncated -or
        [bool]$witness.eof_before_seal -or
        [int]$witness.unexpected_event_count -ne 0
    ) {
        throw 'Docker event witness receipt is incomplete or inconsistent.'
    }
    if (
        [int64]$witness.pre_barrier.time -ne [int64]$events[0].time -or
        [int64]$witness.pre_barrier.time_nano -ne [int64]$events[0].time_nano -or
        [int64]$witness.final_barrier.time -ne
            [int64]$events[$events.Count - 1].time -or
        [int64]$witness.final_barrier.time_nano -ne
            [int64]$events[$events.Count - 1].time_nano -or
        ($MutationIssued -and [int64]$witness.network_connect_time_nano -ne
            [int64]$events[1].time_nano)
    ) {
        throw 'Docker event witness barrier receipt is not bound to its event records.'
    }
    $commandRows = @(Get-CommandRows)
    $eventRows = @($commandRows | Where-Object {
        $_ -like "events$([char]0x1f)--format$([char]0x1f)*"
    })
    $createRows = @($commandRows | Where-Object {
        $_ -ceq (@('volume', 'create', '--driver', 'local') -join ([char]0x1f))
    })
    $removeRows = @($commandRows | Where-Object {
        $_ -ceq (@('volume', 'rm', [string]$witness.barrier_volume_name) -join ([char]0x1f))
    })
    if (
        $eventRows.Count -ne 1 -or
        (@($eventRows[0] -split ([char]0x1f)) -join "`n") -cne
            (@($witness.event_command)[2..4] -join "`n") -or
        $createRows.Count -ne 1 -or
        $removeRows.Count -ne 1
    ) {
        throw 'Docker event witness command ledger is not exact.'
    }
    $expectedKinds = if ($MutationIssued) {
        @('volume/create', 'network/connect', 'volume/destroy')
    }
    else {
        @('volume/create', 'volume/destroy')
    }
    $previousTimeNano = [int64]0
    for ($index = 0; $index -lt $events.Count; $index += 1) {
        $event = $events[$index]
        if (
            "$($event.type)/$($event.action)" -cne $expectedKinds[$index] -or
            $event.scope -cne 'local' -or
            ($event.time -isnot [int] -and $event.time -isnot [long]) -or
            ($event.time_nano -isnot [int] -and $event.time_nano -isnot [long]) -or
            [int64]$event.time_nano -le $previousTimeNano
        ) {
            throw "Docker event witness record $index is not strictly typed and ordered."
        }
        if ($event.type -ceq 'volume') {
            if (
                $event.actor_id -cne $witness.barrier_volume_name -or
                @($event.attributes.PSObject.Properties).Count -ne 1 -or
                $event.attributes.driver -cne 'local'
            ) {
                throw "Docker event witness volume record $index is not exact."
            }
        }
        else {
            if (
                $event.actor_id -cne $safetyNetworkId -or
                @($event.attributes.PSObject.Properties).Count -ne 3 -or
                $event.attributes.container -cne $postgresId -or
                $event.attributes.name -cne 'weatherpoly_safety-plane' -or
                $event.attributes.type -cne 'bridge'
            ) {
                throw 'Docker event witness network-connect record is not exact.'
            }
        }
        $previousTimeNano = [int64]$event.time_nano
    }
    $eventLines = @($events | ForEach-Object {
        ConvertTo-Json -InputObject $_ -Depth 8 -Compress
    })
    $journalText = ($eventLines -join "`n") + "`n"
    $journalBytes = [Text.UTF8Encoding]::new($false).GetBytes($journalText)
    if (
        [int64]$witness.journal_bytes -ne $journalBytes.Length -or
        $witness.journal_sha256 -cne (Get-Sha256Text -Value $journalText) -or
        $witness.events_sha256 -cne (Get-Sha256Text -Value (
            ConvertTo-Json -InputObject $events -Depth 8 -Compress
        ))
    ) {
        throw 'Docker event witness sealed journal hashes are not reproducible.'
    }
}

try {
    $resolvedDocker = Get-Command -Name 'docker' -CommandType Application `
        -ErrorAction Stop | Select-Object -First 1
    if (
        [IO.Path]::GetFullPath([string]$resolvedDocker.Source) -ine
            [IO.Path]::GetFullPath($dockerPath)
    ) {
        throw 'The fake Docker executable was not first on PATH.'
    }

    Reset-Scenario -Scenario 'default-only'
    $unapprovedDockerEvidencePath = Join-Path $evidenceRoot 'unapproved-docker.json'
    Assert-Throws -Label 'unapproved Docker executable' `
        -ExpectedMessage 'exactly one approved executable hash' -Action {
            & $repairPath -Root $fixtureRoot -EvidencePath $unapprovedDockerEvidencePath
        }
    Assert-ZeroBytePartialEvidence -Path $unapprovedDockerEvidencePath
    if (@(Get-CommandRows).Count -ne 0) {
        throw 'Unapproved Docker executable was invoked before its hash was accepted.'
    }

    Reset-Scenario -Scenario 'default-only'
    $collectorStartFailureEvidencePath = Join-Path $evidenceRoot `
        'collector-start-failure.json'
    Assert-Throws -Label 'collector.Start failure cleanup' `
        -ExpectedMessage 'synthetic collector.Start failure' -Action {
            Invoke-Repair -Evidence $collectorStartFailureEvidencePath `
                -ScriptPath $collectorStartFailureRepairPath
        }
    Assert-ZeroBytePartialEvidence -Path $collectorStartFailureEvidencePath
    Assert-RecordedEventObserverStopped
    Assert-NoEventCaptureResidue
    if (@(Get-CommandRows | Where-Object {
        $_ -like "events$([char]0x1f)*" -or
        $_ -like "volume$([char]0x1f)*" -or
        $_ -like "network$([char]0x1f)connect*"
    }).Count -ne 0) {
        throw 'collector.Start failure crossed the event-start or mutation boundary.'
    }

    Reset-Scenario -Scenario 'default-only'
    $postStartIdentityFailureEvidencePath = Join-Path $evidenceRoot `
        'post-start-identity-failure.json'
    Assert-Throws -Label 'post-start identity failure cleanup' `
        -ExpectedMessage 'identity changed while starting' -Action {
            Invoke-Repair -Evidence $postStartIdentityFailureEvidencePath `
                -ScriptPath $postStartIdentityFailureRepairPath
        }
    Assert-ZeroBytePartialEvidence -Path $postStartIdentityFailureEvidencePath
    Assert-RecordedEventObserverStopped -RequireRecordedProcess
    Assert-NoEventCaptureResidue
    if (@(Get-CommandRows | Where-Object {
        $_ -like "volume$([char]0x1f)*" -or
        $_ -like "network$([char]0x1f)connect*"
    }).Count -ne 0) {
        throw 'Post-start identity failure crossed the Docker barrier or mutation boundary.'
    }

    Reset-Scenario -Scenario 'event-late-health-stderr'
    $lateHealthFailureEvidencePath = Join-Path $evidenceRoot `
        'event-late-health-stderr.json'
    Assert-Throws -Label 'late event-pump health publication' `
        -ExpectedMessage 'Docker event witness failed before' -Action {
            Invoke-Repair -Evidence $lateHealthFailureEvidencePath `
                -ScriptPath $lateHealthFailureRepairPath
        }
    Assert-ZeroBytePartialEvidence -Path $lateHealthFailureEvidencePath
    Assert-RecordedEventObserverStopped -RequireRecordedProcess
    Assert-NoEventCaptureResidue
    if (@(Get-CommandRows | Where-Object {
        $_ -like "network$([char]0x1f)connect*"
    }).Count -ne 0) {
        throw 'Late event-pump health publication crossed the mutation boundary.'
    }

    Reset-Scenario -Scenario 'default-only'
    $connectedEvidencePath = Join-Path $evidenceRoot 'connected.json'
    $connectedResult = Invoke-Repair -Evidence $connectedEvidencePath
    if (
        -not $connectedResult.accepted -or
        $connectedResult.disposition -cne 'connected_by_this_invocation'
    ) {
        throw 'Default-only repair did not return the accepted mutation disposition.'
    }
    $connectedEvidence = Read-JsonObjectPreservingDates `
        -LiteralPath $connectedEvidencePath
    $connectedDefaultAttachment =
        $connectedEvidence.payload.snapshots.post_second.postgres.attachments.weatherpoly_default
    $connectedSafetyAttachment =
        $connectedEvidence.payload.snapshots.post_second.postgres.attachments.'weatherpoly_safety-plane'
    $connectedDefaultNetwork =
        $connectedEvidence.payload.snapshots.post_second.production_networks.weatherpoly_default
    $connectedSafetyNetwork =
        $connectedEvidence.payload.snapshots.post_second.production_networks.'weatherpoly_safety-plane'
    $expectedMutation = @(
        'network', 'connect',
        '--alias', 'postgres',
        '--alias', 'weatherpoly-postgres-1',
        '--gw-priority', '0',
        $safetyNetworkId,
        $postgresId
    )
    $connectRows = @(Get-CommandRows | Where-Object {
        $parts = @($_ -split ([char]0x1f))
        $parts.Count -ge 2 -and $parts[0] -ceq 'network' -and $parts[1] -ceq 'connect'
    })
    if (
        $connectedEvidence.format -cne
            'stratus-stopped-postgres-safety-plane-repair-v2' -or
        -not $connectedEvidence.accepted -or
        $connectedEvidence.payload.disposition -cne 'connected_by_this_invocation' -or
        -not $connectedEvidence.payload.mutation.issued -or
        (@($connectedEvidence.payload.mutation.command) -join "`n") -cne
            ($expectedMutation -join "`n") -or
        $connectRows.Count -ne 1 -or
        (@($connectRows[0] -split ([char]0x1f)) -join "`n") -cne
            ($expectedMutation -join "`n") -or
        $connectedEvidence.payload.snapshots.pre.canonical_sha256 -cne
            $connectedEvidence.payload.snapshots.armed.canonical_sha256 -or
        $connectedEvidence.payload.snapshots.post_first.canonical_sha256 -cne
            $connectedEvidence.payload.snapshots.post_second.canonical_sha256 -or
        $connectedEvidence.payload.snapshots.pre.postgres.attachment_state -cne
            'default_only' -or
        $connectedEvidence.payload.snapshots.post_second.postgres.attachment_state -cne
            'configured_pending' -or
        $connectedDefaultAttachment.configuration_state -cne 'ExactStopped' -or
        $connectedDefaultAttachment.network_id -cne $defaultNetworkId -or
        $connectedDefaultAttachment.ipam_configuration -cne 'null' -or
        $connectedDefaultAttachment.driver_options -cne 'null' -or
        $connectedSafetyAttachment.configuration_state -cne 'ConfiguredPending' -or
        $connectedSafetyAttachment.network_id -cne '' -or
        $connectedSafetyAttachment.ipam_configuration -cne 'empty_object' -or
        $connectedSafetyAttachment.driver_options -cne 'empty_object' -or
        $connectedDefaultNetwork.created -cne '2026-07-05T17:30:21.808081932Z' -or
        $connectedSafetyNetwork.created -cne '2026-07-30T09:19:55.325597748Z' -or
        @($connectedEvidence.payload.snapshots.pre.lifecycle_inventory).Count -ne 3 -or
        $connectedEvidence.payload.snapshots.pre.lifecycle_inventory_sha256 -cne
            $connectedEvidence.payload.snapshots.post_second.lifecycle_inventory_sha256
    ) {
        throw 'Default-only repair evidence is incomplete or not exact.'
    }
    $authorizationProperties = @(
        $connectedEvidence.payload.authorization.PSObject.Properties
    )
    if (
        $authorizationProperties.Count -ne 6 -or
        @($authorizationProperties | Where-Object { [bool]$_.Value }).Count -ne 0
    ) {
        throw 'Repair evidence must not authorize any runtime or LIVE action.'
    }
    $recomputedPayloadHash = Get-Sha256Text -Value (
        $connectedEvidence.payload | ConvertTo-Json -Depth 30 -Compress
    )
    if ($connectedEvidence.payload_sha256 -cne $recomputedPayloadHash) {
        throw 'Repair evidence payload hash is not reproducible.'
    }
    Assert-ExactEventWitness -Evidence $connectedEvidence -MutationIssued $true
    Assert-NoForbiddenDockerMutation

    Reset-Scenario -Scenario 'connect-materialized'
    $materializedEvidencePath = Join-Path $evidenceRoot 'connected-materialized.json'
    $materializedResult = Invoke-Repair -Evidence $materializedEvidencePath
    $materializedEvidence = Read-JsonObjectPreservingDates `
        -LiteralPath $materializedEvidencePath
    if (
        -not $materializedResult.accepted -or
        $materializedResult.disposition -cne 'connected_by_this_invocation' -or
        $materializedEvidence.payload.snapshots.post_second.postgres.attachment_state -cne
            'exact'
    ) {
        throw 'Materialized stopped endpoint was not accepted after the single connect mutation.'
    }
    Assert-ExactEventWitness -Evidence $materializedEvidence -MutationIssued $true
    Assert-NoForbiddenDockerMutation

    Reset-Scenario -Scenario 'already-configured-pending' `
        -AttachmentState 'configured-pending'
    $pendingEvidencePath = Join-Path $evidenceRoot 'already-configured-pending.json'
    $pendingResult = Invoke-Repair -Evidence $pendingEvidencePath
    $pendingEvidence = Read-JsonObjectPreservingDates -LiteralPath $pendingEvidencePath
    if (
        -not $pendingResult.accepted -or
        $pendingResult.disposition -cne 'verified_configured_pending_existing' -or
        $pendingEvidence.payload.mutation.issued -or
        $pendingEvidence.payload.snapshots.pre.postgres.attachment_state -cne
            'configured_pending' -or
        $pendingEvidence.payload.snapshots.pre.canonical_sha256 -cne
            $pendingEvidence.payload.snapshots.post_second.canonical_sha256 -or
        @(Get-CommandRows | Where-Object { $_ -like "network$([char]0x1f)connect*" }).Count -ne 0
    ) {
        throw 'Configured-pending crash recovery was not an immutable no-op acceptance.'
    }
    Assert-ExactEventWitness -Evidence $pendingEvidence -MutationIssued $false
    Assert-NoForbiddenDockerMutation

    Reset-Scenario -Scenario 'already-exact' -AttachmentState exact
    $exactEvidencePath = Join-Path $evidenceRoot 'already-exact.json'
    $exactResult = Invoke-Repair -Evidence $exactEvidencePath
    $exactEvidence = Read-JsonObjectPreservingDates -LiteralPath $exactEvidencePath
    if (
        -not $exactResult.accepted -or
        $exactResult.disposition -cne 'verified_exact_existing' -or
        $exactEvidence.payload.mutation.issued -or
        $exactEvidence.payload.snapshots.pre.canonical_sha256 -cne
            $exactEvidence.payload.snapshots.post_second.canonical_sha256 -or
        @(Get-CommandRows | Where-Object { $_ -like "network$([char]0x1f)connect*" }).Count -ne 0
    ) {
        throw 'Already-exact crash recovery was not an immutable no-op acceptance.'
    }
    Assert-ExactEventWitness -Evidence $exactEvidence -MutationIssued $false
    Assert-NoForbiddenDockerMutation

    Reset-Scenario -Scenario 'default-only'
    $existingEvidencePath = Join-Path $evidenceRoot 'existing.json'
    [IO.File]::WriteAllText($existingEvidencePath, '{"partial":true}', $utf8)
    Assert-Throws -Label 'existing evidence path' -ExpectedMessage 'existing or partial' -Action {
        Invoke-Repair -Evidence $existingEvidencePath
    }
    if (@(Get-CommandRows).Count -ne 0) {
        throw 'Existing evidence path did not block before every Docker command.'
    }

    Reset-Scenario -Scenario 'connect-fail'
    $connectFailurePath = Join-Path $evidenceRoot 'connect-fail.json'
    Assert-Throws -Label 'nonzero network connect' -ExpectedMessage 'exit 41' -Action {
        Invoke-Repair -Evidence $connectFailurePath
    }
    Assert-ZeroBytePartialEvidence -Path $connectFailurePath
    if (
        [IO.File]::ReadAllText($stateFile).Trim() -cne 'configured-pending' -or
        @(Get-CommandRows | Where-Object {
            $_ -like "network$([char]0x1f)connect*"
        }).Count -ne 1
    ) {
        throw 'Nonzero connect fixture did not model an ambiguous applied mutation.'
    }
    Assert-NoForbiddenDockerMutation

    $recoveryEvidencePath = Join-Path $evidenceRoot 'connect-fail-recovery.json'
    $recoveryResult = Invoke-Repair -Evidence $recoveryEvidencePath
    if (
        -not $recoveryResult.accepted -or
        $recoveryResult.disposition -cne 'verified_configured_pending_existing' -or
        (Get-Item -LiteralPath $connectFailurePath).Length -ne 0 -or
        @(Get-CommandRows | Where-Object {
            $_ -like "network$([char]0x1f)connect*"
        }).Count -ne 1
    ) {
        throw 'Fresh-path recovery did not preserve the prior zero-byte terminal artifact.'
    }
    Assert-NoForbiddenDockerMutation

    Reset-Scenario -Scenario 'connect-output'
    $connectOutputPath = Join-Path $evidenceRoot 'connect-output.json'
    Assert-Throws -Label 'unexpected connect output' -ExpectedMessage 'unexpected output' -Action {
        Invoke-Repair -Evidence $connectOutputPath
    }
    Assert-ZeroBytePartialEvidence -Path $connectOutputPath
    Assert-NoForbiddenDockerMutation

    Reset-Scenario -Scenario 'connect-no-effect'
    $noEffectPath = Join-Path $evidenceRoot 'connect-no-effect.json'
    Assert-Throws -Label 'connect no effect' -ExpectedMessage 'admissible final' -Action {
        Invoke-Repair -Evidence $noEffectPath
    }
    Assert-ZeroBytePartialEvidence -Path $noEffectPath
    Assert-NoForbiddenDockerMutation

    Reset-Scenario -Scenario 'armed-lifecycle-drift'
    $armedDriftPath = Join-Path $evidenceRoot 'armed-drift.json'
    Assert-Throws -Label 'armed lifecycle drift' -ExpectedMessage 'pre and armed' -Action {
        Invoke-Repair -Evidence $armedDriftPath
    }
    Assert-ZeroBytePartialEvidence -Path $armedDriftPath
    if (@(Get-CommandRows | Where-Object {
        $_ -like "network$([char]0x1f)connect*"
    }).Count -ne 0) {
        throw 'Armed lifecycle drift did not block before mutation.'
    }

    Reset-Scenario -Scenario 'post-second-lifecycle-drift'
    $postDriftPath = Join-Path $evidenceRoot 'post-drift.json'
    Assert-Throws -Label 'post lifecycle drift' -ExpectedMessage 'two consecutive' -Action {
        Invoke-Repair -Evidence $postDriftPath
    }
    Assert-ZeroBytePartialEvidence -Path $postDriftPath
    Assert-NoForbiddenDockerMutation

    Reset-Scenario -Scenario 'engine-drift'
    $engineDriftPath = Join-Path $evidenceRoot 'engine-drift.json'
    Assert-Throws -Label 'engine drift' -ExpectedMessage 'exact approved production boundary' -Action {
        Invoke-Repair -Evidence $engineDriftPath
    }
    Assert-ZeroBytePartialEvidence -Path $engineDriftPath

    Reset-Scenario -Scenario 'context-drift'
    $contextDriftPath = Join-Path $evidenceRoot 'context-drift.json'
    Assert-Throws -Label 'Docker context drift' `
        -ExpectedMessage 'exact approved production boundary' -Action {
            Invoke-Repair -Evidence $contextDriftPath
        }
    Assert-ZeroBytePartialEvidence -Path $contextDriftPath

    Reset-Scenario -Scenario 'endpoint-drift'
    $endpointDriftPath = Join-Path $evidenceRoot 'endpoint-drift.json'
    Assert-Throws -Label 'Docker endpoint drift' `
        -ExpectedMessage 'exact approved production boundary' -Action {
            Invoke-Repair -Evidence $endpointDriftPath
        }
    Assert-ZeroBytePartialEvidence -Path $endpointDriftPath

    Reset-Scenario -Scenario 'default-only'
    $env:DOCKER_API_VERSION = '1.24'
    try {
        $apiOverridePath = Join-Path $evidenceRoot 'api-version-override.json'
        Assert-Throws -Label 'Docker API version override' `
            -ExpectedMessage 'DOCKER_API_VERSION' -Action {
                Invoke-Repair -Evidence $apiOverridePath
            }
        Assert-ZeroBytePartialEvidence -Path $apiOverridePath
        if (@(Get-CommandRows).Count -ne 0) {
            throw 'Docker API version override did not block before Docker execution.'
        }
    }
    finally {
        Remove-Item -LiteralPath 'Env:DOCKER_API_VERSION' -ErrorAction SilentlyContinue
    }

    Reset-Scenario -Scenario 'running'
    $runningPath = Join-Path $evidenceRoot 'running.json'
    Assert-Throws -Label 'running container' -ExpectedMessage 'zero running' -Action {
        Invoke-Repair -Evidence $runningPath
    }
    Assert-ZeroBytePartialEvidence -Path $runningPath
    if (@(Get-CommandRows | Where-Object {
        $_ -like "network$([char]0x1f)connect*"
    }).Count -ne 0) {
        throw 'Running-container boundary did not block before mutation.'
    }

    Reset-Scenario -Scenario 'pg-drift'
    $pgDriftPath = Join-Path $evidenceRoot 'pg-drift.json'
    Assert-Throws -Label 'PostgreSQL config drift' -ExpectedMessage 'identity/configuration' -Action {
        Invoke-Repair -Evidence $pgDriftPath
    }
    Assert-ZeroBytePartialEvidence -Path $pgDriftPath

    Reset-Scenario -Scenario 'unsafe-attachment' -AttachmentState unsafe
    $unsafePath = Join-Path $evidenceRoot 'unsafe.json'
    Assert-Throws -Label 'unsafe partial attachment' -ExpectedMessage 'attachment is not exact' -Action {
        Invoke-Repair -Evidence $unsafePath
    }
    Assert-ZeroBytePartialEvidence -Path $unsafePath
    if (@(Get-CommandRows | Where-Object {
        $_ -like "network$([char]0x1f)connect*"
    }).Count -ne 0) {
        throw 'Unsafe partial attachment did not fail closed before mutation.'
    }

    $pendingNearMisses = @(
        [pscustomobject]@{
            state = 'configured-pending-null-network-id'
            expected = 'must be a native JSON string'
        },
        [pscustomobject]@{
            state = 'configured-pending-bad-driver-options'
            expected = 'attachment is not exact or configured-pending'
        },
        [pscustomobject]@{
            state = 'configured-pending-default-network'
            expected = 'attachment is not exact or configured-pending'
        },
        [pscustomobject]@{
            state = 'configured-pending-runtime-endpoint'
            expected = 'attachment is not exact or configured-pending'
        },
        [pscustomobject]@{
            state = 'configured-pending-wrong-dns'
            expected = 'attachment is not exact or configured-pending'
        }
    )
    foreach ($nearMiss in $pendingNearMisses) {
        Reset-Scenario -Scenario ([string]$nearMiss.state) `
            -AttachmentState ([string]$nearMiss.state)
        $nearMissPath = Join-Path $evidenceRoot (([string]$nearMiss.state) + '.json')
        Assert-Throws -Label ([string]$nearMiss.state) `
            -ExpectedMessage ([string]$nearMiss.expected) -Action {
                Invoke-Repair -Evidence $nearMissPath
            }
        Assert-ZeroBytePartialEvidence -Path $nearMissPath
        if (@(Get-CommandRows | Where-Object {
            $_ -like "network$([char]0x1f)connect*"
        }).Count -ne 0) {
            throw "Configured-pending near miss reached mutation: $($nearMiss.state)"
        }
    }

    $nativeJsonFailures = @(
        [pscustomobject]@{ scenario = 'native-endpoint-skip-string'; expected = 'native JSON boolean' },
        [pscustomobject]@{ scenario = 'native-network-ipam-object'; expected = 'native JSON array' },
        [pscustomobject]@{ scenario = 'native-postgres-entrypoint'; expected = 'native JSON array' },
        [pscustomobject]@{ scenario = 'native-postgres-paused'; expected = 'native JSON boolean' },
        [pscustomobject]@{ scenario = 'native-postgres-port-object'; expected = 'native JSON arrays' },
        [pscustomobject]@{ scenario = 'native-postgres-port-number'; expected = 'native JSON string' },
        [pscustomobject]@{ scenario = 'native-postgres-mounts-object'; expected = 'native JSON array' },
        [pscustomobject]@{ scenario = 'native-postgres-mount-rw'; expected = 'native JSON boolean' },
        [pscustomobject]@{ scenario = 'native-postgres-label-number'; expected = 'native JSON string' }
    )
    foreach ($failure in $nativeJsonFailures) {
        Reset-Scenario -Scenario ([string]$failure.scenario)
        $nativeFailurePath = Join-Path $evidenceRoot (
            ([string]$failure.scenario) + '.json'
        )
        Assert-Throws -Label ([string]$failure.scenario) `
            -ExpectedMessage ([string]$failure.expected) -Action {
                Invoke-Repair -Evidence $nativeFailurePath
            }
        Assert-ZeroBytePartialEvidence -Path $nativeFailurePath
        if (@(Get-CommandRows | Where-Object {
            $_ -like "network$([char]0x1f)connect*"
        }).Count -ne 0) {
            throw "Native JSON failure reached PostgreSQL mutation: $($failure.scenario)"
        }
        Assert-NoForbiddenDockerMutation
        Assert-RecordedEventObserverStopped
        Assert-NoEventCaptureResidue
    }

    $preMutationEventFailures = @(
        [pscustomobject]@{ scenario = 'event-observer-exit'; state = 'default' },
        [pscustomobject]@{ scenario = 'event-observer-stderr'; state = 'default' },
        [pscustomobject]@{ scenario = 'event-missing-create'; state = 'default' },
        [pscustomobject]@{ scenario = 'event-malformed-create'; state = 'default' },
        [pscustomobject]@{ scenario = 'event-native-time-string'; state = 'default' },
        [pscustomobject]@{ scenario = 'event-native-attributes-array'; state = 'default' },
        [pscustomobject]@{ scenario = 'event-duplicate-key'; state = 'default' },
        [pscustomobject]@{ scenario = 'event-crlf'; state = 'default' },
        [pscustomobject]@{ scenario = 'event-output-overflow'; state = 'default' },
        [pscustomobject]@{ scenario = 'event-spurious-connect'; state = 'exact' },
        [pscustomobject]@{ scenario = 'event-partial-before-mutation'; state = 'default' },
        [pscustomobject]@{ scenario = 'transient-lifecycle'; state = 'default' }
    )
    foreach ($failure in $preMutationEventFailures) {
        Reset-Scenario -Scenario ([string]$failure.scenario) `
            -AttachmentState ([string]$failure.state)
        $eventFailurePath = Join-Path $evidenceRoot (
            ([string]$failure.scenario) + '.json'
        )
        Assert-Throws -Label ([string]$failure.scenario) `
            -ExpectedMessage 'Docker event' -Action {
                Invoke-Repair -Evidence $eventFailurePath
            }
        Assert-ZeroBytePartialEvidence -Path $eventFailurePath
        if (@(Get-CommandRows | Where-Object {
            $_ -like "network$([char]0x1f)connect*"
        }).Count -ne 0) {
            throw "Pre-mutation event failure reached PostgreSQL mutation: $($failure.scenario)"
        }
        Assert-NoForbiddenDockerMutation
        Assert-RecordedEventObserverStopped
        Assert-NoEventCaptureResidue
    }

    $postMutationEventFailures = @(
        'event-missing-connect',
        'event-wrong-connect',
        'event-duplicate-connect',
        'event-missing-destroy',
        'event-wrong-destroy',
        'event-duplicate-destroy',
        'event-extra-after-destroy',
        'event-partial-final'
    )
    foreach ($scenario in $postMutationEventFailures) {
        Reset-Scenario -Scenario $scenario
        $eventFailurePath = Join-Path $evidenceRoot ($scenario + '.json')
        Assert-Throws -Label $scenario -ExpectedMessage 'Docker event' -Action {
            Invoke-Repair -Evidence $eventFailurePath
        }
        Assert-ZeroBytePartialEvidence -Path $eventFailurePath
        if (@(Get-CommandRows | Where-Object {
            $_ -like "network$([char]0x1f)connect*"
        }).Count -ne 1) {
            throw "Post-mutation event failure did not contain one exact repair mutation: $scenario"
        }
        Assert-NoForbiddenDockerMutation
        Assert-RecordedEventObserverStopped
        Assert-NoEventCaptureResidue
    }

    Reset-Scenario -Scenario 'info-fail'
    $infoFailurePath = Join-Path $evidenceRoot 'info-fail.json'
    Assert-Throws -Label 'nonzero read proof' -ExpectedMessage 'exit 29' -Action {
        Invoke-Repair -Evidence $infoFailurePath
    }
    Assert-ZeroBytePartialEvidence -Path $infoFailurePath

    Reset-Scenario -Scenario 'info-hang'
    $infoHangPath = Join-Path $evidenceRoot 'info-hang.json'
    $boundedStopwatch = [Diagnostics.Stopwatch]::StartNew()
    Assert-Throws -Label 'bounded read proof' `
        -ExpectedMessage 'strict process/output bounds' -Action {
            Invoke-Repair -Evidence $infoHangPath
        }
    $boundedStopwatch.Stop()
    Assert-ZeroBytePartialEvidence -Path $infoHangPath
    if ($boundedStopwatch.Elapsed.TotalSeconds -gt 20) {
        throw 'Hung Docker read did not terminate inside the testable hard bound.'
    }

    Reset-Scenario -Scenario 'network-drift'
    $networkDriftPath = Join-Path $evidenceRoot 'network-drift.json'
    Assert-Throws -Label 'network identity drift' -ExpectedMessage 'identity/topology' -Action {
        Invoke-Repair -Evidence $networkDriftPath
    }
    Assert-ZeroBytePartialEvidence -Path $networkDriftPath

    Reset-Scenario -Scenario 'network-drift'
    $cleanupFailurePath = Join-Path $evidenceRoot 'cleanup-failure.json'
    Assert-Throws -Label 'primary plus cleanup failure aggregation' `
        -ExpectedMessage (
            'Original: Production Docker network identity/topology is not exact: ' +
            'weatherpoly_default Cleanup: event capture cleanup failed: ' +
            'synthetic event capture cleanup failure'
        ) -Action {
            Invoke-Repair -Evidence $cleanupFailurePath `
                -ScriptPath $cleanupFailureRepairPath
        }
    Assert-ZeroBytePartialEvidence -Path $cleanupFailurePath

    'Stopped PostgreSQL network repair contract tests passed.'
}
finally {
    $env:PATH = $originalPath
    foreach ($entry in $dockerOverrides.GetEnumerator()) {
        if ($null -eq $entry.Value) {
            Remove-Item -LiteralPath "Env:$($entry.Key)" -ErrorAction SilentlyContinue
        }
        else {
            Set-Item -LiteralPath "Env:$($entry.Key)" -Value ([string]$entry.Value)
        }
    }
    foreach ($name in @(
        'STRATUS_REPAIR_FIXTURE_DATA',
        'STRATUS_REPAIR_STATE_FILE',
        'STRATUS_REPAIR_SNAPSHOT_FILE',
        'STRATUS_REPAIR_COMMAND_LOG',
        'STRATUS_REPAIR_EVENT_FEED',
        'STRATUS_REPAIR_EVENT_SEQUENCE_FILE',
        'STRATUS_REPAIR_EVENT_READY_FILE',
        'STRATUS_REPAIR_EVENT_PID_FILE',
        'STRATUS_REPAIR_EVENT_HEALTH_TRIGGER_FILE',
        'STRATUS_REPAIR_EVENT_HEALTH_ACK_FILE',
        'STRATUS_REPAIR_FAKE_ERROR',
        'STRATUS_REPAIR_SCENARIO'
    )) {
        Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
    }
    $resolvedTemporaryRoot = [IO.Path]::GetFullPath($temporaryRoot)
    $resolvedSystemTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if (
        $resolvedTemporaryRoot.StartsWith(
            $resolvedSystemTemp,
            [StringComparison]::OrdinalIgnoreCase
        ) -and
        (Test-Path -LiteralPath $resolvedTemporaryRoot)
    ) {
        Remove-Item -LiteralPath $resolvedTemporaryRoot -Recurse -Force
    }
}
