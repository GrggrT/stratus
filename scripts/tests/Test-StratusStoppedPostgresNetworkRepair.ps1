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
    'stratus-stopped-postgres-safety-plane-repair-v1',
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
    'verified_exact_existing',
    'all_container_lifecycle_inventory_unchanged',
    'postgres_start_permitted = $false',
    'api_start_permitted = $false',
    'worker_start_permitted = $false',
    'safety_control_start_permitted = $false',
    'live_permitted = $false',
    'binance_kill_switch_removal_permitted = $false'
)) {
    if (-not $source.Contains($required)) {
        throw "Stopped repair lacks required source contract: $required"
    }
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

function New-Attachment {
    param(
        [Parameter(Mandatory = $true)][string] $NetworkId,
        [switch] $UnsafeAlias
    )

    $aliases = if ($UnsafeAlias) {
        @('weatherpoly-postgres-1')
    }
    else {
        @('weatherpoly-postgres-1', 'postgres')
    }
    return [ordered]@{
        IPAMConfig = $null
        Links = $null
        Aliases = $aliases
        MacAddress = ''
        DriverOpts = $null
        GwPriority = 0
        NetworkID = $NetworkId
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
        [ValidateSet('default', 'exact', 'unsafe')]
        [string] $AttachmentState,
        [switch] $ConfigDrift
    )

    $networks = [ordered]@{
        'weatherpoly_default' = New-Attachment -NetworkId $defaultNetworkId
    }
    if ($AttachmentState -in @('exact', 'unsafe')) {
        $networks['weatherpoly_safety-plane'] = New-Attachment `
            -NetworkId $safetyNetworkId `
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
                RW = $false
            },
            [ordered]@{
                Type = 'volume'
                Name = 'weatherpoly_stratus-postgres'
                Source = '/var/lib/docker/volumes/weatherpoly_stratus-postgres/_data'
                Destination = '/var/lib/postgresql/data'
                RW = $true
            }
        )
        NetworkSettings = [ordered]@{ Networks = $networks }
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
                Gateway = $Gateway
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
Write-FixtureJson -Name 'postgres-unsafe.json' `
    -Value (New-PostgresInspect -AttachmentState unsafe)
Write-FixtureJson -Name 'postgres-drift.json' `
    -Value (New-PostgresInspect -AttachmentState default -ConfigDrift)
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
Write-FixtureJson -Name 'endpoint.json' -Value ([ordered]@{
    Host = 'npipe:////./pipe/dockerDesktopLinuxEngine'
    SkipTLSVerify = $false
})
Write-FixtureJson -Name 'endpoint-drift.json' -Value ([ordered]@{
    Host = 'npipe:////./pipe/notProductionDocker'
    SkipTLSVerify = $false
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

    public static int Main(string[] args)
    {
        File.AppendAllText(
            Env("STRATUS_REPAIR_COMMAND_LOG"),
            String.Join("\u001f", args) + Environment.NewLine
        );
        string scenario = Env("STRATUS_REPAIR_SCENARIO") ?? "default-only";
        int snapshot = SnapshotNumber();

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
            Emit(scenario == "endpoint-drift" ? "endpoint-drift.json" : "endpoint.json");
            return 0;
        }
        if (Exact(args, "info", "--format", "{{json .}}"))
        {
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
                else if (state == "exact") Emit("postgres-exact.json");
                else if (state == "unsafe") Emit("postgres-unsafe.json");
                else Emit("postgres-default.json");
                return 0;
            }
            if (args[1] == "$apiId")
            {
                bool drift =
                    (scenario == "armed-lifecycle-drift" && snapshot >= 2) ||
                    (scenario == "post-second-lifecycle-drift" && snapshot >= 4);
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
                Emit(scenario == "network-drift" ? "network-default-drift.json" : "network-default.json");
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
            if (scenario != "connect-no-effect")
                File.WriteAllText(Env("STRATUS_REPAIR_STATE_FILE"), "exact");
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
$env:STRATUS_REPAIR_STATE_FILE = $stateFile
$env:STRATUS_REPAIR_SNAPSHOT_FILE = $snapshotFile
$env:STRATUS_REPAIR_COMMAND_LOG = $commandLog

function Reset-Scenario {
    param(
        [Parameter(Mandatory = $true)][string] $Scenario,
        [ValidateSet('default', 'exact', 'unsafe')]
        [string] $AttachmentState = 'default'
    )

    $env:STRATUS_REPAIR_SCENARIO = $Scenario
    [IO.File]::WriteAllText($stateFile, $AttachmentState, $utf8)
    [IO.File]::WriteAllText($snapshotFile, '0', $utf8)
    [IO.File]::WriteAllText($commandLog, '', $utf8)
}

function Invoke-Repair {
    param([Parameter(Mandatory = $true)][string] $Evidence)

    return & $testRepairPath -Root $fixtureRoot -EvidencePath $Evidence
}

function Get-CommandRows {
    if (-not (Test-Path -LiteralPath $commandLog -PathType Leaf)) {
        return @()
    }
    return @(
        Get-Content -LiteralPath $commandLog |
            Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
}

function Assert-NoForbiddenDockerMutation {
    foreach ($row in @(Get-CommandRows)) {
        $arguments = @($row -split ([char]0x1f))
        if (
            $arguments[0] -in @('start', 'stop', 'restart', 'rm', 'run', 'exec') -or
            ($arguments.Count -ge 2 -and
                $arguments[0] -ceq 'network' -and
                $arguments[1] -ceq 'disconnect')
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
            'stratus-stopped-postgres-safety-plane-repair-v1' -or
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
            'exact' -or
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
        [IO.File]::ReadAllText($stateFile).Trim() -cne 'exact' -or
        @(Get-CommandRows | Where-Object {
            $_ -like "network$([char]0x1f)connect*"
        }).Count -ne 1
    ) {
        throw 'Nonzero connect fixture did not model an ambiguous applied mutation.'
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
    Assert-Throws -Label 'connect no effect' -ExpectedMessage 'exact final' -Action {
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

    Reset-Scenario -Scenario 'info-fail'
    $infoFailurePath = Join-Path $evidenceRoot 'info-fail.json'
    Assert-Throws -Label 'nonzero read proof' -ExpectedMessage 'exit 29' -Action {
        Invoke-Repair -Evidence $infoFailurePath
    }
    Assert-ZeroBytePartialEvidence -Path $infoFailurePath

    Reset-Scenario -Scenario 'network-drift'
    $networkDriftPath = Join-Path $evidenceRoot 'network-drift.json'
    Assert-Throws -Label 'network identity drift' -ExpectedMessage 'identity/topology' -Action {
        Invoke-Repair -Evidence $networkDriftPath
    }
    Assert-ZeroBytePartialEvidence -Path $networkDriftPath

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
