[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string]$Revision,

    [Parameter(Mandatory = $true)]
    [string]$ProductionRoot,

    [Parameter(Mandatory = $true)]
    [string]$BuildWorktree,

    [Parameter(Mandatory = $true)]
    [string]$EvidenceInputDirectory,

    [Parameter(Mandatory = $true)]
    [string]$GitExecutablePath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$ExpectedGitSha256,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$ExpectedDockerSha256,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$Remote,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._/-]*$')]
    [string]$Branch,

    [ValidateRange(1000, 30000)]
    [int]$LiveRemoteTimeoutMilliseconds = 30000,

    [ValidateRange(0, 10000)]
    [int]$PrePublicationDelayMilliseconds = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:Utf8NoBomStrict = [Text.UTF8Encoding]::new($false, $true)
$script:PathComparison = if (
    [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
) {
    [StringComparison]::OrdinalIgnoreCase
}
else {
    [StringComparison]::Ordinal
}

$script:HistoricalBackupRelativePath =
    'backups\stratus_pre_0047_20260822T195653Z_d4c5ce2295714d439bf4f0fb21191547.backup'
$script:HistoricalBackupFiles = [ordered]@{
    'catalog.list' = 'cd7f7d94bd9e3614086ead5d1dd0d20d47e7573c16a1f566c9417845d9935f65'
    'manifest.txt' = 'e2129a1149936048c164ff934e31a8fb3531adcdf6163568730c3969576d1796'
    'pre-migration-attestation.json' =
        'a8717ba0656bc0f5abde739dd0352284f48f598f89597866c55bc7627d97d189'
    'restore-attestation.json' =
        '73939f689e3add8b92ba72cb4824a1c2243fa35228eaed25ffc5dab4144a3e4e'
    'stratus.dump' = 'eabd66ff4e07560c00ff04deac18613497bddf3e355fddae6b688917a4e3b5fa'
}
$script:HistoricalLineageRelativePath =
    '.stratus-cache\cutovers\section3-6d093b49cd53-20260822T193939Z-c70786a5a00349ca93e9c1d53ca7f3d7.in-progress'
$script:HistoricalLineageFiles = [ordered]@{
    '09-stopped-migrate-stage-acceptance.json' =
        'bc8006665fc1365fb51a55904d21f4a6b783e3929a4f1060d8168f01093766dd'
    '17-pre-migration-wrapper-false-negative.json' =
        'ba881647611219d29ccf6fb68d42f1a745a3a431ede5f52fc9cf54ba028a4bea'
    '18-pre-migration-compatibility-holds.json' =
        '829ccf65b644fb7905a525d0a68a29bd3e9483cf2962b2ec2af144accf7c6033'
    '19-pre-migration-gate-and-0047-migration-accepted.json' =
        '200d1b10bfe05537c4385d87dec81c2bbb2669a69c0abcdfa2ba9217b7d9fb4f'
    '20-strict-post-ddl-0047-accepted.json' =
        'c6e30ccea288f3f8c3b57130c4e8af2f40da53ad33b808cb4ddeaa43437503ff'
    '21-post-ddl-hba-accepted.json' =
        'f42d979d98ec9feaa949cb23cb0add1925443bb406967cc1802e701bc0a4926c'
}
$script:ForensicClosureRelativePath =
    '.stratus-cache\cutovers\section3-6d093b49cd53-20260822T193939Z-c70786a5a00349ca93e9c1d53ca7f3d7-forensic-closure-20260823T032906Z.json'
$script:ForensicClosureSha256 =
    '4e021de90492baab9bf025d7799cd7129b579fb5ebba78833952486eb1f39979'
$script:ExpectedRemoteUrl = 'https://github.com/GrggrT/stratus.git'
$script:ExpectedDockerContext = 'desktop-linux'
$script:ExpectedDockerEndpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
$script:MaximumGitOutputBytes = 131072

$script:RequiredInputFiles = @(
    'build-command.json',
    'build-history.json',
    'ci-evidence.json',
    'image-inspect.json',
    'pre-start-topology.json',
    'release-guard.json'
)

function Get-FullPath {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)
    return [IO.Path]::GetFullPath($LiteralPath)
}

function Test-PathEqual {
    param(
        [Parameter(Mandatory = $true)][string]$Left,
        [Parameter(Mandatory = $true)][string]$Right
    )
    return [string]::Equals(
        (Get-FullPath -LiteralPath $Left),
        (Get-FullPath -LiteralPath $Right),
        $script:PathComparison
    )
}

function Assert-RegularDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if (-not (Test-Path -LiteralPath $LiteralPath -PathType Container)) {
        throw "$Label is not an existing directory: $LiteralPath"
    }
    $item = Get-Item -LiteralPath $LiteralPath -Force
    if (
        -not $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
    ) {
        throw "$Label is linked or is not a regular directory: $LiteralPath"
    }
}

function Assert-RegularFile {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][string]$Label,
        [switch]$AllowEmpty
    )
    if (-not (Test-Path -LiteralPath $LiteralPath -PathType Leaf)) {
        throw "$Label is not an existing file: $LiteralPath"
    }
    $item = Get-Item -LiteralPath $LiteralPath -Force
    if (
        $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        (-not $AllowEmpty -and $item.Length -le 0)
    ) {
        throw "$Label is linked, empty, or not a regular file: $LiteralPath"
    }
}

function Assert-PathBelowRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][string]$Label,
        [switch]$AllowRoot
    )
    $rootFull = Get-FullPath -LiteralPath $Root
    $pathFull = Get-FullPath -LiteralPath $LiteralPath
    $rootPrefix = $rootFull.TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    ) + [IO.Path]::DirectorySeparatorChar
    $isRoot = [string]::Equals($rootFull, $pathFull, $script:PathComparison)
    if (
        (-not ($AllowRoot -and $isRoot)) -and
        -not $pathFull.StartsWith($rootPrefix, $script:PathComparison)
    ) {
        throw "$Label is outside the production root: $pathFull"
    }
    return $pathFull
}

function Assert-NoReparseComponents {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $rootFull = Assert-PathBelowRoot -Root $Root -LiteralPath $LiteralPath `
        -Label $Label -AllowRoot
    Assert-RegularDirectory -LiteralPath $Root -Label 'production root'
    if (Test-PathEqual -Left $Root -Right $rootFull) {
        return
    }
    $relative = $rootFull.Substring(
        (Get-FullPath -LiteralPath $Root).TrimEnd(
            [IO.Path]::DirectorySeparatorChar,
            [IO.Path]::AltDirectorySeparatorChar
        ).Length + 1
    )
    $cursor = Get-FullPath -LiteralPath $Root
    foreach ($part in $relative.Split(@(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    ), [StringSplitOptions]::RemoveEmptyEntries)) {
        $cursor = Join-Path $cursor $part
        if (-not (Test-Path -LiteralPath $cursor)) {
            throw "$Label contains a missing path component: $cursor"
        }
        $item = Get-Item -LiteralPath $cursor -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "$Label crosses a reparse point: $cursor"
        }
    }
}

function Assert-NoReparseAbsolutePath {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $fullPath = Get-FullPath -LiteralPath $LiteralPath
    $pathRoot = [IO.Path]::GetPathRoot($fullPath)
    if ([string]::IsNullOrWhiteSpace($pathRoot)) {
        throw "$Label has no absolute filesystem root."
    }
    $cursor = $pathRoot
    $relative = $fullPath.Substring($pathRoot.Length)
    foreach ($part in $relative.Split(@(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    ), [StringSplitOptions]::RemoveEmptyEntries)) {
        $cursor = Join-Path $cursor $part
        if (-not (Test-Path -LiteralPath $cursor)) {
            throw "$Label contains a missing path component: $cursor"
        }
        $item = Get-Item -LiteralPath $cursor -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "$Label crosses a reparse point: $cursor"
        }
    }
}

function Get-Sha256Bytes {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString(
            $algorithm.ComputeHash($Bytes)
        ).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $algorithm.Dispose()
    }
}

function Get-ExclusiveFileBytes {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][Int64]$MaximumBytes,
        [Parameter(Mandatory = $true)][string]$Label
    )
    Assert-RegularFile -LiteralPath $LiteralPath -Label $Label
    $stream = [IO.File]::Open(
        $LiteralPath,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::None
    )
    try {
        if ($stream.Length -le 0 -or $stream.Length -gt $MaximumBytes) {
            throw "$Label is outside its accepted byte bound."
        }
        $bytes = [byte[]]::new([int]$stream.Length)
        $offset = 0
        while ($offset -lt $bytes.Length) {
            $read = $stream.Read($bytes, $offset, $bytes.Length - $offset)
            if ($read -le 0) {
                throw "$Label ended before its advertised byte length."
            }
            $offset += $read
        }
        if ($stream.ReadByte() -ne -1) {
            throw "$Label grew during its exclusive snapshot."
        }
        return $bytes
    }
    finally {
        $stream.Dispose()
    }
}

function Get-RegularFileSha256 {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][string]$Label
    )
    Assert-RegularFile -LiteralPath $LiteralPath -Label $Label
    $stream = [IO.File]::Open(
        $LiteralPath,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::None
    )
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString(
            $algorithm.ComputeHash($stream)
        ).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function ConvertFrom-StrictUtf8Text {
    param(
        [Parameter(Mandatory = $true)][byte[]]$Bytes,
        [Parameter(Mandatory = $true)][string]$Label,
        [switch]$RequireTrailingLf
    )
    if (
        $Bytes.Length -ge 3 -and
        $Bytes[0] -eq 0xEF -and
        $Bytes[1] -eq 0xBB -and
        $Bytes[2] -eq 0xBF
    ) {
        throw "$Label must be UTF-8 without BOM."
    }
    if (@($Bytes | Where-Object { $_ -eq 0x0D -or $_ -eq 0x00 }).Count -ne 0) {
        throw "$Label contains CR or NUL bytes."
    }
    if ($RequireTrailingLf -and $Bytes[$Bytes.Length - 1] -ne 0x0A) {
        throw "$Label must end in exactly one LF-delimited record boundary."
    }
    try {
        return $script:Utf8NoBomStrict.GetString($Bytes)
    }
    catch {
        throw "$Label is not strict UTF-8: $($_.Exception.Message)"
    }
}

function Assert-NoSecretText {
    param(
        [Parameter(Mandatory = $true)][string]$Text,
        [Parameter(Mandatory = $true)][string]$Label
    )
    foreach ($pattern in @(
        '(?i)(?:password|secret|api[_-]?key|authorization|bearer|token)\s*[:=]\s*[^\s"'']+',
        '(?i)\b(?:op|safe)_[A-Za-z0-9]{20,}\b',
        '-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'
    )) {
        if ($Text -match $pattern) {
            throw "$Label contains secret-shaped material."
        }
    }
}

function Assert-NoSensitiveJsonProperties {
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if ($null -eq $Value) {
        return
    }
    if ($Value -is [Array]) {
        foreach ($entry in $Value) {
            Assert-NoSensitiveJsonProperties -Value $entry -Label $Label
        }
        return
    }
    if ($Value.GetType().FullName -eq 'System.Management.Automation.PSCustomObject') {
        foreach ($property in $Value.PSObject.Properties) {
            if ($property.Name -match '(?i)^(?:password|secret|token|api_?key|cookie|private_?key|credential)$') {
                throw "$Label contains a forbidden sensitive property: $($property.Name)"
            }
            Assert-NoSensitiveJsonProperties -Value $property.Value -Label $Label
        }
    }
}

function ConvertFrom-StrictJsonBytes {
    param(
        [Parameter(Mandatory = $true)][byte[]]$Bytes,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $text = ConvertFrom-StrictUtf8Text -Bytes $Bytes -Label $Label -RequireTrailingLf
    Assert-NoSecretText -Text $text -Label $Label
    Assert-NoDuplicateJsonProperties -Bytes $Bytes -Label $Label
    try {
        if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) {
            $value = ConvertFrom-Json -InputObject $text -DateKind String -ErrorAction Stop
        }
        else {
            $value = ConvertFrom-Json -InputObject $text -ErrorAction Stop
        }
    }
    catch {
        throw "$Label is not valid JSON: $($_.Exception.Message)"
    }
    if (
        $null -eq $value -or
        $value.GetType().FullName -cne 'System.Management.Automation.PSCustomObject'
    ) {
        throw "$Label must contain one native JSON object."
    }
    Assert-NoSensitiveJsonProperties -Value $value -Label $Label
    return $value
}

function Assert-NoDuplicateJsonProperties {
    param(
        [Parameter(Mandatory = $true)][byte[]]$Bytes,
        [Parameter(Mandatory = $true)][string]$Label
    )
    Add-Type -AssemblyName System.Runtime.Serialization -ErrorAction Stop
    $quotas = [Xml.XmlDictionaryReaderQuotas]::new()
    $quotas.MaxDepth = 64
    $quotas.MaxStringContentLength = 4194304
    $quotas.MaxArrayLength = 131072
    $quotas.MaxBytesPerRead = 65536
    $quotas.MaxNameTableCharCount = 1048576
    $reader = [Runtime.Serialization.Json.JsonReaderWriterFactory]::CreateJsonReader(
        $Bytes,
        $quotas
    )
    $containers = @{}
    try {
        while ($reader.Read()) {
            if ($reader.NodeType -eq [Xml.XmlNodeType]::Element) {
                $depth = [int]$reader.Depth
                if ($depth -gt 0 -and $containers.ContainsKey($depth - 1)) {
                    $parent = $containers[$depth - 1]
                    if ($parent.kind -ceq 'object' -and -not $parent.names.Add(
                        [string]$reader.Name
                    )) {
                        throw "$Label contains a duplicate JSON property."
                    }
                }
                $type = [string]$reader.GetAttribute('type')
                if ($type -cin @('object', 'array')) {
                    $containers[$depth] = [pscustomobject]@{
                        kind = $type
                        names = [Collections.Generic.HashSet[string]]::new(
                            [StringComparer]::Ordinal
                        )
                    }
                }
                elseif ($containers.ContainsKey($depth)) {
                    $containers.Remove($depth)
                }
            }
            elseif (
                $reader.NodeType -eq [Xml.XmlNodeType]::EndElement -and
                $containers.ContainsKey([int]$reader.Depth)
            ) {
                $containers.Remove([int]$reader.Depth)
            }
        }
    }
    catch {
        if ($_.Exception.Message -like "$Label contains a duplicate JSON property.*") {
            throw
        }
        throw "$Label failed strict duplicate-aware JSON parsing: $($_.Exception.Message)"
    }
    finally {
        $reader.Dispose()
    }
}

function Assert-ExactProperties {
    param(
        [Parameter(Mandatory = $true)][object]$Value,
        [Parameter(Mandatory = $true)][string[]]$Expected,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if (
        $null -eq $Value -or
        $Value.GetType().FullName -cne 'System.Management.Automation.PSCustomObject'
    ) {
        throw "$Label must be a native JSON object."
    }
    $actual = @($Value.PSObject.Properties.Name)
    [Array]::Sort($actual, [StringComparer]::Ordinal)
    $wanted = @($Expected)
    [Array]::Sort($wanted, [StringComparer]::Ordinal)
    if (($actual -join "`n") -cne ($wanted -join "`n")) {
        throw "$Label does not have the exact reviewed property set."
    }
}

function Assert-JsonString {
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory = $true)][string]$Label,
        [switch]$AllowEmpty
    )
    if (
        $null -eq $Value -or
        $Value.GetType() -ne [string] -or
        (-not $AllowEmpty -and [string]::IsNullOrWhiteSpace([string]$Value))
    ) {
        throw "$Label must be a native non-empty JSON string."
    }
}

function Assert-JsonBoolean {
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if ($null -eq $Value -or $Value.GetType() -ne [bool]) {
        throw "$Label must be a native JSON boolean."
    }
}

function Assert-JsonInteger {
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory = $true)][string]$Label,
        [Int64]$Minimum = [Int64]::MinValue
    )
    if (
        $null -eq $Value -or
        $Value.GetType().FullName -cnotin @('System.Int32', 'System.Int64') -or
        [Int64]$Value -lt $Minimum
    ) {
        throw "$Label must be a native JSON integer at or above $Minimum."
    }
}

function Assert-JsonArray {
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if ($null -eq $Value -or $Value -isnot [Array]) {
        throw "$Label must be a native JSON array."
    }
}

function Assert-HashString {
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory = $true)][string]$Label,
        [switch]$WithPrefix
    )
    Assert-JsonString -Value $Value -Label $Label
    $pattern = if ($WithPrefix) { '^sha256:[0-9a-f]{64}$' } else { '^[0-9a-f]{64}$' }
    if ([string]$Value -cnotmatch $pattern) {
        throw "$Label is not a canonical lowercase SHA-256 value."
    }
}

function ConvertTo-CanonicalUtcTime {
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if ($Value -is [DateTime]) {
        if ($Value.Kind -eq [DateTimeKind]::Unspecified) {
            throw "$Label was parsed without a UTC kind."
        }
        return [DateTimeOffset]::new($Value.ToUniversalTime())
    }
    if ($Value -is [DateTimeOffset]) {
        return $Value.ToUniversalTime()
    }
    Assert-JsonString -Value $Value -Label $Label
    if ([string]$Value -cnotmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$') {
        throw "$Label is not a canonical UTC timestamp."
    }
    $parsed = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse(
        [string]$Value,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::AssumeUniversal,
        [ref]$parsed
    )) {
        throw "$Label is not a valid UTC timestamp."
    }
    return $parsed.ToUniversalTime()
}

function Assert-ExactStringArray {
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$Expected,
        [Parameter(Mandatory = $true)][string]$Label
    )
    Assert-JsonArray -Value $Value -Label $Label
    $actual = @($Value)
    if ($actual.Count -ne $Expected.Count) {
        throw "$Label does not have the exact reviewed length."
    }
    for ($index = 0; $index -lt $Expected.Count; $index++) {
        Assert-JsonString -Value $actual[$index] -Label "$Label[$index]"
        if ([string]$actual[$index] -cne [string]$Expected[$index]) {
            throw "$Label is not the exact reviewed ordered array."
        }
    }
}

function Assert-DockerCollectorBinding {
    param(
        [Parameter(Mandatory = $true)][object]$Value,
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string]$ExpectedDockerSha256,
        [AllowNull()][string]$ExpectedDockerPath,
        [Parameter(Mandatory = $true)][string]$ExpectedCollector,
        [Parameter(Mandatory = $true)][string[]]$ExpectedArgv
    )
    foreach ($name in @(
        'docker_executable_path', 'docker_context', 'docker_endpoint', 'collector'
    )) {
        Assert-JsonString -Value $Value.$name -Label "$Label $name"
    }
    Assert-HashString -Value $Value.docker_executable_sha256 `
        -Label "$Label docker_executable_sha256"
    $dockerPath = Get-FullPath -LiteralPath ([string]$Value.docker_executable_path)
    Assert-NoReparseAbsolutePath -LiteralPath $dockerPath `
        -Label "$Label Docker executable"
    Assert-RegularFile -LiteralPath $dockerPath -Label "$Label Docker executable"
    if (
        $Value.docker_executable_sha256 -cne $ExpectedDockerSha256 -or
        (-not [string]::IsNullOrWhiteSpace($ExpectedDockerPath) -and
            -not (Test-PathEqual -Left $dockerPath -Right $ExpectedDockerPath)) -or
        $Value.docker_context -cne $script:ExpectedDockerContext -or
        $Value.docker_endpoint -cne $script:ExpectedDockerEndpoint -or
        $Value.collector -cne $ExpectedCollector
    ) {
        throw "$Label does not bind the exact pinned desktop-linux Docker boundary."
    }
    if ((Get-RegularFileSha256 -LiteralPath $dockerPath `
        -Label "$Label Docker executable") -cne $ExpectedDockerSha256) {
        throw "$Label Docker executable bytes do not match the pinned SHA-256."
    }
    Assert-ExactStringArray -Value $Value.collector_argv `
        -Label "$Label collector_argv" -Expected $ExpectedArgv
    return $dockerPath
}

function Initialize-StratusR1JobObjectInterop {
    if ($null -ne ('StratusR1SuspendedJobProcess' -as [type])) {
        return
    }
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'R1 Git process-tree containment requires Windows Job Objects.'
    }
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;

public sealed class StratusR1SuspendedJobProcess : IDisposable
{
    private const UInt32 JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;
    private const Int32 JobObjectExtendedLimitInformation = 9;
    private const UInt32 CREATE_SUSPENDED = 0x00000004;
    private const UInt32 CREATE_NO_WINDOW = 0x08000000;
    private const UInt32 CREATE_UNICODE_ENVIRONMENT = 0x00000400;
    private const UInt32 EXTENDED_STARTUPINFO_PRESENT = 0x00080000;
    private const UInt32 STARTF_USESTDHANDLES = 0x00000100;
    private const UInt32 STARTF_USESHOWWINDOW = 0x00000001;
    private const UInt16 SW_HIDE = 0;
    private const UInt32 HANDLE_FLAG_INHERIT = 0x00000001;
    private const UInt32 GENERIC_READ = 0x80000000;
    private const UInt32 FILE_SHARE_READ = 0x00000001;
    private const UInt32 FILE_SHARE_WRITE = 0x00000002;
    private const UInt32 OPEN_EXISTING = 3;
    private const UInt32 WAIT_OBJECT_0 = 0;
    private const UInt32 WAIT_TIMEOUT = 258;
    private const UInt32 WAIT_FAILED = 0xFFFFFFFF;
    private const Int32 ERROR_INSUFFICIENT_BUFFER = 122;
    private const Int32 JobObjectBasicAccountingInformation = 1;
    private static readonly IntPtr PROC_THREAD_ATTRIBUTE_HANDLE_LIST =
        new IntPtr(0x00020002);

    private IntPtr processHandle;
    private IntPtr jobHandle;
    private bool disposed;

    public Stream StandardOutput { get; private set; }
    public Stream StandardError { get; private set; }
    public Int32 Id { get; private set; }

    [StructLayout(LayoutKind.Sequential)]
    private struct IO_COUNTERS
    {
        public UInt64 ReadOperationCount;
        public UInt64 WriteOperationCount;
        public UInt64 OtherOperationCount;
        public UInt64 ReadTransferCount;
        public UInt64 WriteTransferCount;
        public UInt64 OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_BASIC_LIMIT_INFORMATION
    {
        public Int64 PerProcessUserTimeLimit;
        public Int64 PerJobUserTimeLimit;
        public UInt32 LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public UInt32 ActiveProcessLimit;
        public UIntPtr Affinity;
        public UInt32 PriorityClass;
        public UInt32 SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct SECURITY_ATTRIBUTES
    {
        public UInt32 nLength;
        public IntPtr lpSecurityDescriptor;
        [MarshalAs(UnmanagedType.Bool)] public bool bInheritHandle;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct STARTUPINFO
    {
        public UInt32 cb;
        public string lpReserved;
        public string lpDesktop;
        public string lpTitle;
        public UInt32 dwX;
        public UInt32 dwY;
        public UInt32 dwXSize;
        public UInt32 dwYSize;
        public UInt32 dwXCountChars;
        public UInt32 dwYCountChars;
        public UInt32 dwFillAttribute;
        public UInt32 dwFlags;
        public UInt16 wShowWindow;
        public UInt16 cbReserved2;
        public IntPtr lpReserved2;
        public IntPtr hStdInput;
        public IntPtr hStdOutput;
        public IntPtr hStdError;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct PROCESS_INFORMATION
    {
        public IntPtr hProcess;
        public IntPtr hThread;
        public UInt32 dwProcessId;
        public UInt32 dwThreadId;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct STARTUPINFOEX
    {
        public STARTUPINFO StartupInfo;
        public IntPtr lpAttributeList;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_BASIC_ACCOUNTING_INFORMATION
    {
        public Int64 TotalUserTime;
        public Int64 TotalKernelTime;
        public Int64 ThisPeriodTotalUserTime;
        public Int64 ThisPeriodTotalKernelTime;
        public UInt32 TotalPageFaultCount;
        public UInt32 TotalProcesses;
        public UInt32 ActiveProcesses;
        public UInt32 TotalTerminatedProcesses;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObject(IntPtr attributes, string name);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CreatePipe(
        out IntPtr readPipe,
        out IntPtr writePipe,
        ref SECURITY_ATTRIBUTES pipeAttributes,
        UInt32 size);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetHandleInformation(
        IntPtr handle,
        UInt32 mask,
        UInt32 flags);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateFile(
        string fileName,
        UInt32 desiredAccess,
        UInt32 shareMode,
        ref SECURITY_ATTRIBUTES securityAttributes,
        UInt32 creationDisposition,
        UInt32 flagsAndAttributes,
        IntPtr templateFile);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CreateProcess(
        string applicationName,
        StringBuilder commandLine,
        IntPtr processAttributes,
        IntPtr threadAttributes,
        [MarshalAs(UnmanagedType.Bool)] bool inheritHandles,
        UInt32 creationFlags,
        IntPtr environment,
        string currentDirectory,
        ref STARTUPINFOEX startupInfo,
        out PROCESS_INFORMATION processInformation);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool InitializeProcThreadAttributeList(
        IntPtr attributeList,
        Int32 attributeCount,
        Int32 flags,
        ref UIntPtr size);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool UpdateProcThreadAttribute(
        IntPtr attributeList,
        UInt32 flags,
        IntPtr attribute,
        IntPtr value,
        IntPtr size,
        IntPtr previousValue,
        IntPtr returnSize);

    [DllImport("kernel32.dll")]
    private static extern void DeleteProcThreadAttributeList(IntPtr attributeList);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(
        IntPtr job,
        Int32 informationClass,
        ref JOBOBJECT_EXTENDED_LIMIT_INFORMATION information,
        UInt32 informationLength);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool result);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool TerminateJobObject(IntPtr job, UInt32 exitCode);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern UInt32 ResumeThread(IntPtr thread);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern UInt32 WaitForSingleObject(IntPtr handle, UInt32 milliseconds);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetExitCodeProcess(IntPtr process, out UInt32 exitCode);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool TerminateProcess(IntPtr process, UInt32 exitCode);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool QueryInformationJobObject(
        IntPtr job,
        Int32 informationClass,
        out JOBOBJECT_BASIC_ACCOUNTING_INFORMATION information,
        UInt32 informationLength,
        IntPtr returnLength);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);

    private StratusR1SuspendedJobProcess() { }

    private static string QuoteArgument(string value)
    {
        if (value.Length > 0 && value.IndexOfAny(new[] { ' ', '\t', '"' }) < 0)
            return value;
        var result = new StringBuilder();
        result.Append('"');
        int slashes = 0;
        foreach (char character in value)
        {
            if (character == '\\') { slashes++; continue; }
            if (character == '"')
            {
                result.Append('\\', (slashes * 2) + 1);
                result.Append('"');
                slashes = 0;
                continue;
            }
            if (slashes > 0) { result.Append('\\', slashes); slashes = 0; }
            result.Append(character);
        }
        if (slashes > 0) result.Append('\\', slashes * 2);
        result.Append('"');
        return result.ToString();
    }

    private static void CloseChecked(IntPtr handle, string label)
    {
        if (handle != IntPtr.Zero && handle.ToInt64() != -1 && !CloseHandle(handle))
            throw new Win32Exception(Marshal.GetLastWin32Error(), label + " close failed");
    }

    public static StratusR1SuspendedJobProcess Start(
        string executablePath,
        string[] arguments,
        string[] environmentEntries,
        string workingDirectory)
    {
        if (String.IsNullOrWhiteSpace(executablePath))
            throw new ArgumentException("Executable path is required", "executablePath");
        if (String.IsNullOrWhiteSpace(workingDirectory) ||
            !Directory.Exists(workingDirectory))
            throw new ArgumentException(
                "Existing working directory is required", "workingDirectory");
        if (arguments == null || environmentEntries == null)
            throw new ArgumentNullException();

        IntPtr job = IntPtr.Zero;
        IntPtr stdoutRead = IntPtr.Zero;
        IntPtr stdoutWrite = IntPtr.Zero;
        IntPtr stderrRead = IntPtr.Zero;
        IntPtr stderrWrite = IntPtr.Zero;
        IntPtr nullInput = IntPtr.Zero;
        IntPtr environment = IntPtr.Zero;
        IntPtr attributeList = IntPtr.Zero;
        IntPtr inheritHandleList = IntPtr.Zero;
        PROCESS_INFORMATION process = new PROCESS_INFORMATION();
        bool assigned = false;
        bool attributeListInitialized = false;
        Stream stdoutStream = null;
        Stream stderrStream = null;
        try
        {
            job = CreateJobObject(IntPtr.Zero, null);
            if (job == IntPtr.Zero)
                throw new Win32Exception(Marshal.GetLastWin32Error(), "CreateJobObject failed");
            var information = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
            information.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            UInt32 length = (UInt32)Marshal.SizeOf(
                typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
            if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation,
                ref information, length))
                throw new Win32Exception(Marshal.GetLastWin32Error(),
                    "SetInformationJobObject failed");

            var security = new SECURITY_ATTRIBUTES();
            security.nLength = (UInt32)Marshal.SizeOf(typeof(SECURITY_ATTRIBUTES));
            security.bInheritHandle = true;
            if (!CreatePipe(out stdoutRead, out stdoutWrite, ref security, 0) ||
                !SetHandleInformation(stdoutRead, HANDLE_FLAG_INHERIT, 0))
                throw new Win32Exception(Marshal.GetLastWin32Error(),
                    "stdout pipe creation failed");
            if (!CreatePipe(out stderrRead, out stderrWrite, ref security, 0) ||
                !SetHandleInformation(stderrRead, HANDLE_FLAG_INHERIT, 0))
                throw new Win32Exception(Marshal.GetLastWin32Error(),
                    "stderr pipe creation failed");
            nullInput = CreateFile("NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                ref security, OPEN_EXISTING, 0, IntPtr.Zero);
            if (nullInput == new IntPtr(-1))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "NUL open failed");

            var command = new StringBuilder(QuoteArgument(executablePath));
            foreach (string argument in arguments)
                command.Append(' ').Append(QuoteArgument(argument ?? String.Empty));
            string environmentBlock = String.Join("\0", environmentEntries) + "\0\0";
            environment = Marshal.StringToHGlobalUni(environmentBlock);

            UIntPtr attributeListBytes = UIntPtr.Zero;
            bool sizingResult = InitializeProcThreadAttributeList(
                IntPtr.Zero, 1, 0, ref attributeListBytes);
            Int32 sizingError = Marshal.GetLastWin32Error();
            if (sizingResult || sizingError != ERROR_INSUFFICIENT_BUFFER ||
                attributeListBytes == UIntPtr.Zero)
                throw new Win32Exception(sizingError,
                    "Proc-thread attribute-list sizing failed");
            attributeList = Marshal.AllocHGlobal(
                new IntPtr(checked((Int64)attributeListBytes.ToUInt64())));
            if (!InitializeProcThreadAttributeList(
                attributeList, 1, 0, ref attributeListBytes))
                throw new Win32Exception(Marshal.GetLastWin32Error(),
                    "Proc-thread attribute-list initialization failed");
            attributeListInitialized = true;

            Int32 inheritedHandleBytes = checked(IntPtr.Size * 3);
            inheritHandleList = Marshal.AllocHGlobal(inheritedHandleBytes);
            Marshal.WriteIntPtr(inheritHandleList, 0, nullInput);
            Marshal.WriteIntPtr(inheritHandleList, IntPtr.Size, stdoutWrite);
            Marshal.WriteIntPtr(inheritHandleList, IntPtr.Size * 2, stderrWrite);
            if (!UpdateProcThreadAttribute(
                attributeList,
                0,
                PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                inheritHandleList,
                new IntPtr(inheritedHandleBytes),
                IntPtr.Zero,
                IntPtr.Zero))
                throw new Win32Exception(Marshal.GetLastWin32Error(),
                    "Exact inherited-handle list installation failed");

            var startup = new STARTUPINFOEX();
            startup.StartupInfo.cb = (UInt32)Marshal.SizeOf(typeof(STARTUPINFOEX));
            startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW;
            startup.StartupInfo.wShowWindow = SW_HIDE;
            startup.StartupInfo.hStdInput = nullInput;
            startup.StartupInfo.hStdOutput = stdoutWrite;
            startup.StartupInfo.hStdError = stderrWrite;
            startup.lpAttributeList = attributeList;
            if (!CreateProcess(executablePath, command, IntPtr.Zero, IntPtr.Zero, true,
                CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT |
                    EXTENDED_STARTUPINFO_PRESENT,
                environment, workingDirectory, ref startup, out process))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "CreateProcess failed");

            CloseChecked(stdoutWrite, "parent stdout write pipe"); stdoutWrite = IntPtr.Zero;
            CloseChecked(stderrWrite, "parent stderr write pipe"); stderrWrite = IntPtr.Zero;
            CloseChecked(nullInput, "parent NUL input"); nullInput = IntPtr.Zero;

            if (!AssignProcessToJobObject(job, process.hProcess))
                throw new Win32Exception(Marshal.GetLastWin32Error(),
                    "AssignProcessToJobObject failed");
            assigned = true;
            bool inJob;
            if (!IsProcessInJob(process.hProcess, job, out inJob))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "IsProcessInJob failed");
            if (!inJob)
                throw new InvalidOperationException("Suspended process was not retained in its job");
            UInt32 previousSuspendCount = ResumeThread(process.hThread);
            if (previousSuspendCount != 1)
                throw new InvalidOperationException(
                    "Suspended process did not resume exactly once");
            CloseChecked(process.hThread, "primary thread"); process.hThread = IntPtr.Zero;

            stdoutStream = new FileStream(
                new SafeFileHandle(stdoutRead, true), FileAccess.Read, 4096, false);
            stdoutRead = IntPtr.Zero;
            stderrStream = new FileStream(
                new SafeFileHandle(stderrRead, true), FileAccess.Read, 4096, false);
            stderrRead = IntPtr.Zero;

            var instance = new StratusR1SuspendedJobProcess();
            instance.processHandle = process.hProcess; process.hProcess = IntPtr.Zero;
            instance.jobHandle = job; job = IntPtr.Zero;
            instance.Id = (Int32)process.dwProcessId;
            instance.StandardOutput = stdoutStream; stdoutStream = null;
            instance.StandardError = stderrStream; stderrStream = null;
            return instance;
        }
        catch (Exception primary)
        {
            var failures = new List<Exception>();
            failures.Add(primary);
            if (process.hProcess != IntPtr.Zero)
            {
                try
                {
                    if (assigned)
                    {
                        if (!TerminateJobObject(job, 1))
                            throw new Win32Exception(Marshal.GetLastWin32Error(),
                                "startup job termination failed");
                    }
                    else if (!TerminateProcess(process.hProcess, 1))
                        throw new Win32Exception(Marshal.GetLastWin32Error(),
                            "startup process termination failed");
                }
                catch (Exception cleanup) { failures.Add(cleanup); }
                try
                {
                    UInt32 waitResult = WaitForSingleObject(process.hProcess, 2000);
                    if (waitResult == WAIT_TIMEOUT)
                        throw new TimeoutException(
                            "Startup process did not terminate inside the cleanup bound");
                    if (waitResult == WAIT_FAILED)
                        throw new Win32Exception(Marshal.GetLastWin32Error(),
                            "Startup process cleanup wait failed");
                    if (waitResult != WAIT_OBJECT_0)
                        throw new InvalidOperationException(
                            "Startup process cleanup wait returned an unknown result");
                }
                catch (Exception cleanup) { failures.Add(cleanup); }
            }
            if (assigned && job != IntPtr.Zero)
            {
                try
                {
                    if (!WaitForJobEmpty(job, 2000))
                        throw new TimeoutException(
                            "Startup job did not become empty inside the cleanup bound");
                }
                catch (Exception cleanup) { failures.Add(cleanup); }
            }
            try { if (stdoutStream != null) stdoutStream.Dispose(); }
            catch (Exception cleanup) { failures.Add(cleanup); }
            try { if (stderrStream != null) stderrStream.Dispose(); }
            catch (Exception cleanup) { failures.Add(cleanup); }
            foreach (var pair in new[] {
                new KeyValuePair<IntPtr,string>(process.hThread, "primary thread"),
                new KeyValuePair<IntPtr,string>(process.hProcess, "process"),
                new KeyValuePair<IntPtr,string>(stdoutRead, "stdout read pipe"),
                new KeyValuePair<IntPtr,string>(stdoutWrite, "stdout write pipe"),
                new KeyValuePair<IntPtr,string>(stderrRead, "stderr read pipe"),
                new KeyValuePair<IntPtr,string>(stderrWrite, "stderr write pipe"),
                new KeyValuePair<IntPtr,string>(nullInput, "NUL input"),
                new KeyValuePair<IntPtr,string>(job, "job") })
            {
                try { CloseChecked(pair.Key, pair.Value); }
                catch (Exception cleanup) { failures.Add(cleanup); }
            }
            throw new AggregateException("Contained Git startup failed", failures);
        }
        finally
        {
            if (attributeListInitialized)
                DeleteProcThreadAttributeList(attributeList);
            if (attributeList != IntPtr.Zero)
                Marshal.FreeHGlobal(attributeList);
            if (inheritHandleList != IntPtr.Zero)
                Marshal.FreeHGlobal(inheritHandleList);
            if (environment != IntPtr.Zero) Marshal.FreeHGlobal(environment);
        }
    }

    private static UInt32 GetActiveProcessCount(IntPtr job)
    {
        JOBOBJECT_BASIC_ACCOUNTING_INFORMATION information;
        if (!QueryInformationJobObject(
            job,
            JobObjectBasicAccountingInformation,
            out information,
            (UInt32)Marshal.SizeOf(typeof(JOBOBJECT_BASIC_ACCOUNTING_INFORMATION)),
            IntPtr.Zero))
            throw new Win32Exception(Marshal.GetLastWin32Error(),
                "QueryInformationJobObject failed");
        return information.ActiveProcesses;
    }

    private static bool WaitForJobEmpty(IntPtr job, Int32 milliseconds)
    {
        if (milliseconds < 0)
            throw new ArgumentOutOfRangeException("milliseconds");
        var stopwatch = Stopwatch.StartNew();
        while (GetActiveProcessCount(job) != 0)
        {
            if (stopwatch.ElapsedMilliseconds >= milliseconds) return false;
            Thread.Sleep(10);
        }
        return true;
    }

    public bool HasExited
    {
        get
        {
            UInt32 result = WaitForSingleObject(processHandle, 0);
            if (result == WAIT_OBJECT_0) return true;
            if (result == WAIT_TIMEOUT) return false;
            throw new Win32Exception(Marshal.GetLastWin32Error(), "process wait failed");
        }
    }

    public bool WaitForExit(Int32 milliseconds)
    {
        UInt32 result = WaitForSingleObject(processHandle, (UInt32)milliseconds);
        if (result == WAIT_OBJECT_0) return true;
        if (result == WAIT_TIMEOUT) return false;
        throw new Win32Exception(Marshal.GetLastWin32Error(), "process wait failed");
    }

    public Int32 ExitCode
    {
        get
        {
            UInt32 exitCode;
            if (!GetExitCodeProcess(processHandle, out exitCode))
                throw new Win32Exception(Marshal.GetLastWin32Error(),
                    "GetExitCodeProcess failed");
            return unchecked((Int32)exitCode);
        }
    }

    public UInt32 ActiveProcessCount
    {
        get { return GetActiveProcessCount(jobHandle); }
    }

    public bool WaitForTreeExit(Int32 milliseconds)
    {
        return WaitForJobEmpty(jobHandle, milliseconds);
    }

    public void TerminateTree()
    {
        if (!TerminateJobObject(jobHandle, 1))
            throw new Win32Exception(Marshal.GetLastWin32Error(), "TerminateJobObject failed");
    }

    public void Dispose()
    {
        if (disposed) return;
        disposed = true;
        var failures = new List<Exception>();
        bool terminateTree = false;
        try { terminateTree = GetActiveProcessCount(jobHandle) != 0; }
        catch (Exception failure) { failures.Add(failure); terminateTree = true; }
        if (terminateTree)
        {
            try { TerminateTree(); }
            catch (Exception failure) { failures.Add(failure); }
        }
        try
        {
            if (!WaitForJobEmpty(jobHandle, 2000))
                throw new TimeoutException(
                    "Contained Git job did not become empty before disposal");
        }
        catch (Exception failure) { failures.Add(failure); }
        try { if (StandardOutput != null) StandardOutput.Dispose(); }
        catch (Exception failure) { failures.Add(failure); }
        try { if (StandardError != null) StandardError.Dispose(); }
        catch (Exception failure) { failures.Add(failure); }
        try { CloseChecked(processHandle, "process"); processHandle = IntPtr.Zero; }
        catch (Exception failure) { failures.Add(failure); }
        try { CloseChecked(jobHandle, "job"); jobHandle = IntPtr.Zero; }
        catch (Exception failure) { failures.Add(failure); }
        if (failures.Count > 0)
            throw new AggregateException("Contained Git cleanup failed", failures);
    }
}
'@ -Language CSharp -ErrorAction Stop
}

function Invoke-BoundedGit {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [ValidateRange(1000, 30000)][int]$TimeoutMilliseconds = 10000,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [int[]]$AcceptedExitCodes
    )
    if (
        $null -eq $script:GitExecutableHandle -or
        -not $script:GitExecutableHandle.CanRead
    ) {
        throw 'The held source-approved Git executable identity is unavailable.'
    }
    if (
        [string]::IsNullOrWhiteSpace($script:GitWorkingDirectory) -or
        -not (Test-Path -LiteralPath $script:GitWorkingDirectory -PathType Container)
    ) {
        throw 'The neutral bounded Git working directory is unavailable.'
    }
    if (
        @($AcceptedExitCodes).Count -eq 0 -or
        @($AcceptedExitCodes | Sort-Object -Unique).Count -ne
            @($AcceptedExitCodes).Count
    ) {
        throw 'Accepted Git exit codes must be a non-empty unique set.'
    }
    Initialize-StratusR1JobObjectInterop
    $childEnvironment =
        [Collections.Generic.SortedDictionary[string, string]]::new(
            [StringComparer]::OrdinalIgnoreCase
        )
    foreach ($entry in [Environment]::GetEnvironmentVariables().GetEnumerator()) {
        $name = [string]$entry.Key
        $value = [string]$entry.Value
        if (
            [string]::IsNullOrEmpty($name) -or
            $name.Contains('=') -or
            $name.IndexOf([char]0) -ge 0 -or
            $value.IndexOf([char]0) -ge 0
        ) {
            throw 'The inherited process environment cannot form a canonical Git environment.'
        }
        $childEnvironment[$name] = $value
    }
    $childEnvironment['GIT_TERMINAL_PROMPT'] = '0'
    $childEnvironment['GCM_INTERACTIVE'] = 'Never'
    $childEnvironment['GIT_PAGER'] = 'cat'
    $childEnvironment['GIT_OPTIONAL_LOCKS'] = '0'
    $childEnvironment['GIT_NO_REPLACE_OBJECTS'] = '1'
    $childEnvironment['GIT_CEILING_DIRECTORIES'] = $script:GitWorkingDirectory
    $childEnvironment['GIT_CONFIG_GLOBAL'] = 'NUL'
    $childEnvironment['GIT_CONFIG_NOSYSTEM'] = '1'
    $childEnvironment['GIT_ATTR_NOSYSTEM'] = '1'
    $childEnvironment['GIT_CONFIG_COUNT'] = '0'
    $childEnvironment['LC_ALL'] = 'C'
    $childEnvironment['LANG'] = 'C'
    $childEnvironmentEntries = @(
        $childEnvironment.GetEnumerator() | ForEach-Object {
            "$($_.Key)=$($_.Value)"
        }
    )

    $process = $null
    $started = $false
    $primaryError = $null
    $cleanupError = $null
    $result = $null
    $stdoutTask = $null
    $stderrTask = $null
    $stdoutCapture = [IO.MemoryStream]::new()
    $stderrCapture = [IO.MemoryStream]::new()
    try {
        $process = [StratusR1SuspendedJobProcess]::Start(
            $script:GitExecutable,
            [string[]]$Arguments,
            [string[]]$childEnvironmentEntries,
            $script:GitWorkingDirectory
        )
        $started = $true

        $stdoutBuffer = [byte[]]::new(4096)
        $stderrBuffer = [byte[]]::new(4096)
        $stdoutEof = $false
        $stderrEof = $false
        $capturedBytes = [Int64]0
        $stdoutTask = $process.StandardOutput.ReadAsync(
            $stdoutBuffer,
            0,
            $stdoutBuffer.Length
        )
        $stderrTask = $process.StandardError.ReadAsync(
            $stderrBuffer,
            0,
            $stderrBuffer.Length
        )
        $stopwatch = [Diagnostics.Stopwatch]::StartNew()
        while (-not ($stdoutEof -and $stderrEof -and $process.HasExited)) {
            if ($stopwatch.ElapsedMilliseconds -ge $TimeoutMilliseconds) {
                throw 'The bounded read-only Git process exceeded its wall-clock limit.'
            }
            $madeProgress = $false
            if (-not $stdoutEof -and $stdoutTask.IsCompleted) {
                $read = [int]$stdoutTask.GetAwaiter().GetResult()
                if ($read -eq 0) {
                    $stdoutEof = $true
                }
                else {
                    $capturedBytes += $read
                    if ($capturedBytes -gt $script:MaximumGitOutputBytes) {
                        throw (
                            'The bounded read-only Git process exceeded its ' +
                            "$($script:MaximumGitOutputBytes)-byte combined output bound."
                        )
                    }
                    $stdoutCapture.Write($stdoutBuffer, 0, $read)
                    $stdoutTask = $process.StandardOutput.ReadAsync(
                        $stdoutBuffer,
                        0,
                        $stdoutBuffer.Length
                    )
                }
                $madeProgress = $true
            }
            if (-not $stderrEof -and $stderrTask.IsCompleted) {
                $read = [int]$stderrTask.GetAwaiter().GetResult()
                if ($read -eq 0) {
                    $stderrEof = $true
                }
                else {
                    $capturedBytes += $read
                    if ($capturedBytes -gt $script:MaximumGitOutputBytes) {
                        throw (
                            'The bounded read-only Git process exceeded its ' +
                            "$($script:MaximumGitOutputBytes)-byte combined output bound."
                        )
                    }
                    $stderrCapture.Write($stderrBuffer, 0, $read)
                    $stderrTask = $process.StandardError.ReadAsync(
                        $stderrBuffer,
                        0,
                        $stderrBuffer.Length
                    )
                }
                $madeProgress = $true
            }
            if (-not $madeProgress) {
                $remaining = $TimeoutMilliseconds - [int]$stopwatch.ElapsedMilliseconds
                if ($remaining -le 0) {
                    throw 'The bounded read-only Git process exceeded its wall-clock limit.'
                }
                if ($process.HasExited) {
                    [Threading.Thread]::Sleep([Math]::Min(10, $remaining))
                }
                else {
                    [void]$process.WaitForExit([Math]::Min(25, $remaining))
                }
            }
        }
        $stopwatch.Stop()
        $treeWaitMilliseconds =
            $TimeoutMilliseconds - [int]$stopwatch.ElapsedMilliseconds
        if (
            $treeWaitMilliseconds -le 0 -or
            -not $process.WaitForTreeExit($treeWaitMilliseconds)
        ) {
            throw 'The bounded read-only Git process tree exceeded its wall-clock limit.'
        }
        $stdout = $script:Utf8NoBomStrict.GetString($stdoutCapture.ToArray())
        $stderr = $script:Utf8NoBomStrict.GetString($stderrCapture.ToArray())
        if ($AcceptedExitCodes -notcontains $process.ExitCode) {
            $stderrHash = Get-Sha256Bytes -Bytes (
                $script:Utf8NoBomStrict.GetBytes($stderr)
            )
            throw (
                "The bounded read-only Git process returned exit $($process.ExitCode); " +
                "stderr_sha256=$stderrHash."
            )
        }
        if (-not [string]::IsNullOrEmpty($stderr)) {
            throw 'The bounded read-only Git process emitted unexpected stderr.'
        }
        if ($stdout.IndexOf([char]0) -ge 0) {
            throw 'The bounded read-only Git process emitted NUL bytes.'
        }
        $result = [pscustomobject]@{
            exit_code = [int]$process.ExitCode
            stdout = $stdout
        }
    }
    catch {
        $primaryError = $_
    }
    $cleanupFailures = [Collections.Generic.List[string]]::new()
    $terminateTree = $null -ne $primaryError
    try {
        if ($started -and $process.ActiveProcessCount -ne 0) {
            $terminateTree = $true
        }
    }
    catch {
        $terminateTree = $true
        [void]$cleanupFailures.Add([string]$_.Exception.Message)
    }
    try {
        if ($started -and $terminateTree) {
            $process.TerminateTree()
        }
    }
    catch {
        [void]$cleanupFailures.Add([string]$_.Exception.Message)
    }
    try {
        if ($started -and -not $process.HasExited) {
            if (-not $process.WaitForExit(2000)) {
                throw 'Git primary process did not terminate inside the cleanup bound.'
            }
        }
    }
    catch {
        [void]$cleanupFailures.Add([string]$_.Exception.Message)
    }
    try {
        if ($started -and -not $process.WaitForTreeExit(2000)) {
            throw 'Git process tree did not terminate inside the cleanup bound.'
        }
    }
    catch {
        [void]$cleanupFailures.Add([string]$_.Exception.Message)
    }
    foreach ($collectorTask in @($stdoutTask, $stderrTask)) {
        try {
            if ($null -ne $collectorTask -and -not $collectorTask.Wait(2000)) {
                throw 'Git output collector did not terminate inside the cleanup bound.'
            }
        }
        catch {
            [void]$cleanupFailures.Add([string]$_.Exception.Message)
        }
    }
    foreach ($resource in @($stdoutCapture, $stderrCapture)) {
        try {
            $resource.Dispose()
        }
        catch {
            [void]$cleanupFailures.Add([string]$_.Exception.Message)
        }
    }
    try {
        if ($null -ne $process) {
            $process.Dispose()
            $process = $null
        }
    }
    catch {
        [void]$cleanupFailures.Add([string]$_.Exception.Message)
    }
    if ($cleanupFailures.Count -ne 0) {
        $cleanupError = $cleanupFailures -join ' '
    }
    if ($null -ne $primaryError -and $null -ne $cleanupError) {
        throw (
            "Git proof failed: $($primaryError.Exception.Message) " +
            "Git cleanup also failed: $cleanupError"
        )
    }
    if ($null -ne $primaryError) {
        throw $primaryError
    }
    if ($null -ne $cleanupError) {
        throw $cleanupError
    }
    return $result
}

function Invoke-ExactGit {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [ValidateRange(1000, 30000)][int]$TimeoutMilliseconds = 10000,
        [int[]]$AcceptedExitCodes = @(0)
    )
    $proof = Invoke-BoundedGit -Arguments $Arguments `
        -TimeoutMilliseconds $TimeoutMilliseconds `
        -AcceptedExitCodes $AcceptedExitCodes
    $lines = @(
        $proof.stdout -split '\r?\n' |
            Where-Object { -not [string]::IsNullOrEmpty([string]$_) }
    )
    return $lines
}

function Write-CreateNewBytes {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][byte[]]$Bytes
    )
    $stream = [IO.File]::Open(
        $LiteralPath,
        [IO.FileMode]::CreateNew,
        [IO.FileAccess]::Write,
        [IO.FileShare]::None
    )
    try {
        $stream.Write($Bytes, 0, $Bytes.Length)
        $stream.Flush($true)
    }
    finally {
        $stream.Dispose()
    }
}

function ConvertTo-JsonBytes {
    param([Parameter(Mandatory = $true)][object]$Value)
    $json = ($Value | ConvertTo-Json -Depth 20) + "`n"
    return $script:Utf8NoBomStrict.GetBytes($json)
}

function Write-CreateNewJson {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][object]$Value
    )
    $bytes = ConvertTo-JsonBytes -Value $Value
    Write-CreateNewBytes -LiteralPath $LiteralPath -Bytes $bytes
    return $bytes
}

function Read-InputEvidence {
    param([Parameter(Mandatory = $true)][string]$InputDirectory)
    $expectedEntries = @($script:RequiredInputFiles + 'input-manifest.tsv')
    [Array]::Sort($expectedEntries, [StringComparer]::Ordinal)
    $actualEntries = @(
        Get-ChildItem -LiteralPath $InputDirectory -Force |
            ForEach-Object { $_.Name }
    )
    [Array]::Sort($actualEntries, [StringComparer]::Ordinal)
    if (($actualEntries -join "`n") -cne ($expectedEntries -join "`n")) {
        throw 'The R1 input directory does not contain exactly the reviewed evidence files.'
    }
    foreach ($name in $expectedEntries) {
        $path = Join-Path $InputDirectory $name
        Assert-RegularFile -LiteralPath $path -Label "R1 input $name"
    }

    $manifestPath = Join-Path $InputDirectory 'input-manifest.tsv'
    $manifestBytes = Get-ExclusiveFileBytes -LiteralPath $manifestPath `
        -MaximumBytes 65536 -Label 'R1 input manifest'
    $manifestText = ConvertFrom-StrictUtf8Text -Bytes $manifestBytes `
        -Label 'R1 input manifest' -RequireTrailingLf
    Assert-NoSecretText -Text $manifestText -Label 'R1 input manifest'
    $lines = @($manifestText.TrimEnd("`n").Split("`n"))
    if ($lines.Count -ne $script:RequiredInputFiles.Count) {
        throw 'The R1 input manifest does not have exactly six records.'
    }
    $snapshots = [ordered]@{}
    for ($index = 0; $index -lt $script:RequiredInputFiles.Count; $index++) {
        $parts = @($lines[$index].Split("`t"))
        $expectedName = $script:RequiredInputFiles[$index]
        if (
            $parts.Count -ne 3 -or
            $parts[0] -cne $expectedName -or
            $parts[1] -cnotmatch '^[1-9][0-9]*$' -or
            $parts[2] -cnotmatch '^[0-9a-f]{64}$'
        ) {
            throw "R1 input manifest record $index is not canonical."
        }
        $path = Join-Path $InputDirectory $expectedName
        $bytes = Get-ExclusiveFileBytes -LiteralPath $path `
            -MaximumBytes 4194304 -Label "R1 input $expectedName"
        if (
            [Int64]$bytes.Length -ne [Int64]::Parse(
                $parts[1],
                [Globalization.CultureInfo]::InvariantCulture
            ) -or
            (Get-Sha256Bytes -Bytes $bytes) -cne $parts[2]
        ) {
            throw "R1 input manifest binding drifted for $expectedName."
        }
        $snapshots[$expectedName] = $bytes
    }
    return [pscustomobject]@{
        manifest_bytes = $manifestBytes
        manifest_sha256 = Get-Sha256Bytes -Bytes $manifestBytes
        files = $snapshots
    }
}

function Assert-CiEvidence {
    param(
        [Parameter(Mandatory = $true)][object]$Value,
        [Parameter(Mandatory = $true)][string]$ExpectedRevision,
        [Parameter(Mandatory = $true)][string]$ExpectedRemote,
        [Parameter(Mandatory = $true)][string]$ExpectedBranch
    )
    Assert-ExactProperties -Value $Value -Label 'CI evidence' -Expected @(
        'format', 'revision', 'remote', 'branch', 'remote_revision',
        'observed_at_utc', 'overall', 'required_contexts'
    )
    if (
        $Value.format -cne 'stratus-r0-exact-sha-ci-evidence-v3' -or
        $Value.revision -cne $ExpectedRevision -or
        $Value.remote -cne $ExpectedRemote -or
        $Value.branch -cne $ExpectedBranch -or
        $Value.remote_revision -cne $ExpectedRevision -or
        $Value.overall -cne 'success'
    ) {
        throw 'CI evidence is not a successful exact-revision push result.'
    }
    $observedAt = ConvertTo-CanonicalUtcTime -Value $Value.observed_at_utc `
        -Label 'CI evidence observed_at_utc'
    Assert-JsonArray -Value $Value.required_contexts -Label 'CI required contexts'
    $expectedContexts = @(
        'CI / checks',
        'CI / release-contracts-windows-powershell',
        'Operator console / build'
    )
    $contexts = @($Value.required_contexts)
    if ($contexts.Count -ne $expectedContexts.Count) {
        throw 'CI evidence does not contain exactly the three required contexts.'
    }
    $seenRunJobs = @{}
    for ($index = 0; $index -lt $contexts.Count; $index++) {
        $context = $contexts[$index]
        Assert-ExactProperties -Value $context -Label "CI context $index" -Expected @(
            'context', 'workflow_run_id', 'job_id', 'head_sha', 'event',
            'status', 'conclusion', 'url'
        )
        if (
            $context.context -cne $expectedContexts[$index] -or
            $context.head_sha -cne $ExpectedRevision -or
            $context.event -cne 'push' -or
            $context.status -cne 'completed' -or
            $context.conclusion -cne 'success'
        ) {
            throw "CI context $index is not an exact successful push context."
        }
        Assert-JsonInteger -Value $context.workflow_run_id `
            -Label "CI context $index workflow_run_id" -Minimum 1
        Assert-JsonInteger -Value $context.job_id -Label "CI context $index job_id" -Minimum 1
        $runJob = "$($context.workflow_run_id):$($context.job_id)"
        if ($seenRunJobs.ContainsKey($runJob)) {
            throw 'CI evidence reuses a workflow-run/job identity.'
        }
        $seenRunJobs[$runJob] = $true
        Assert-JsonString -Value $context.url -Label "CI context $index url"
        $expectedUrl = '^https://github\.com/GrggrT/stratus/actions/runs/' +
            [regex]::Escape([string]$context.workflow_run_id) + '/job/' +
            [regex]::Escape([string]$context.job_id) + '$'
        if ([string]$context.url -cnotmatch $expectedUrl) {
            throw "CI context $index URL is not the exact reviewed job URL."
        }
    }
    return $observedAt
}

function Assert-ImageEvidence {
    param(
        [Parameter(Mandatory = $true)][object]$Value,
        [Parameter(Mandatory = $true)][string]$ExpectedRevision,
        [Parameter(Mandatory = $true)][string]$ExpectedImage,
        [Parameter(Mandatory = $true)][string]$ExpectedDockerSha256
    )
    Assert-ExactProperties -Value $Value -Label 'image evidence' -Expected @(
        'format', 'captured_at_utc', 'tag', 'id', 'repo_digest', 'size_bytes',
        'os', 'architecture', 'revision_label', 'baked_operator_credential_entries',
        'docker_executable_path', 'docker_executable_sha256', 'docker_context',
        'docker_endpoint', 'collector', 'collector_argv'
    )
    Assert-HashString -Value $Value.id -Label 'image evidence id' -WithPrefix
    $digest = ([string]$Value.id).Substring(7)
    if (
        $Value.format -cne 'stratus-local-image-inspect-v4' -or
        $Value.tag -cne $ExpectedImage -or
        $Value.repo_digest -cne "stratus@sha256:$digest" -or
        $Value.os -cne 'linux' -or
        $Value.architecture -cne 'amd64' -or
        $Value.revision_label -cne $ExpectedRevision
    ) {
        throw 'Image evidence is not the exact immutable linux/amd64 candidate.'
    }
    $capturedAt = ConvertTo-CanonicalUtcTime -Value $Value.captured_at_utc `
        -Label 'image evidence captured_at_utc'
    Assert-JsonInteger -Value $Value.size_bytes -Label 'image evidence size_bytes' -Minimum 1
    Assert-JsonInteger -Value $Value.baked_operator_credential_entries `
        -Label 'image evidence baked credential count' -Minimum 0
    if ([Int64]$Value.baked_operator_credential_entries -ne 0) {
        throw 'Candidate image contains baked operator credential material.'
    }
    $dockerPath = Assert-DockerCollectorBinding -Value $Value `
        -Label 'image evidence' -ExpectedDockerSha256 $ExpectedDockerSha256 `
        -ExpectedCollector 'docker-cli-image-inspect-v1' -ExpectedArgv @(
            '--context', $script:ExpectedDockerContext, 'image', 'inspect',
            $ExpectedImage, '--format', '{{json .}}'
        )
    return [pscustomobject]@{
        digest = $digest
        captured_at = $capturedAt
        docker_executable_path = $dockerPath
    }
}

function Assert-ReleaseGuardEvidence {
    param(
        [Parameter(Mandatory = $true)][object]$Value,
        [Parameter(Mandatory = $true)][string]$ExpectedRevision,
        [Parameter(Mandatory = $true)][string]$ExpectedImage,
        [Parameter(Mandatory = $true)][string]$ExpectedImageId,
        [Parameter(Mandatory = $true)][string]$Root
    )
    Assert-ExactProperties -Value $Value -Label 'release guard evidence' -Expected @(
        'image', 'image_id', 'revision', 'platform', 'compose_services',
        'default_safe_services', 'release_services', 'isolated_profile_services',
        'profiled_services', 'release_profiles', 'compose_project', 'compose_file',
        'compose_env_file', 'migrate_command', 'resource_limited_services', 'verified'
    )
    Assert-JsonBoolean -Value $Value.verified -Label 'release guard verified'
    if (
        $Value.verified -ne $true -or
        $Value.image -cne $ExpectedImage -or
        $Value.image_id -cne $ExpectedImageId -or
        $Value.revision -cne $ExpectedRevision -or
        $Value.platform -cne 'linux/amd64' -or
        $Value.compose_project -cne 'weatherpoly' -or
        -not (Test-PathEqual -Left ([string]$Value.compose_file) `
            -Right (Join-Path $Root 'docker-compose.yml')) -or
        -not (Test-PathEqual -Left ([string]$Value.compose_env_file) `
            -Right (Join-Path $Root '.env'))
    ) {
        throw 'Release guard evidence is not bound to the exact candidate and production root.'
    }
    Assert-ExactStringArray -Value $Value.compose_services -Label 'guard compose_services' `
        -Expected @('migrate', 'worker', 'binance-market-stream', 'api', 'safety-control', 'telegram')
    Assert-ExactStringArray -Value $Value.default_safe_services `
        -Label 'guard default_safe_services' -Expected @()
    Assert-ExactStringArray -Value $Value.release_services -Label 'guard release_services' `
        -Expected @(
            'api', 'backup', 'migrate', 'postgres', 'provision-runtime-role',
            'provision-safety-role', 'safety-control', 'validate-owner-password',
            'validate-safety-credentials', 'worker'
        )
    Assert-ExactStringArray -Value $Value.profiled_services -Label 'guard profiled_services' `
        -Expected @(
            'api', 'backup', 'backup-offsite', 'binance-market-stream', 'migrate',
            'postgres', 'provision-runtime-role', 'provision-safety-role',
            'safety-control', 'telegram', 'validate-owner-password',
            'validate-safety-credentials', 'worker'
        )
    Assert-ExactStringArray -Value $Value.release_profiles -Label 'guard release_profiles' `
        -Expected @('release', 'infrastructure', 'binance', 'telegram', 'offsite')
    Assert-ExactStringArray -Value $Value.migrate_command -Label 'guard migrate_command' `
        -Expected @('alembic', 'upgrade', 'head')
    Assert-ExactStringArray -Value $Value.resource_limited_services `
        -Label 'guard resource_limited_services' -Expected @('worker', 'api', 'safety-control')
    Assert-ExactProperties -Value $Value.isolated_profile_services `
        -Label 'guard isolated_profile_services' `
        -Expected @('release', 'infrastructure', 'binance', 'telegram', 'offsite')
    Assert-ExactStringArray -Value $Value.isolated_profile_services.release `
        -Label 'guard isolated release profile' -Expected @(
            'api', 'backup', 'migrate', 'postgres', 'provision-runtime-role',
            'provision-safety-role', 'safety-control', 'validate-owner-password',
            'validate-safety-credentials', 'worker'
        )
    Assert-ExactStringArray -Value $Value.isolated_profile_services.infrastructure `
        -Label 'guard isolated infrastructure profile' `
        -Expected @('backup', 'postgres', 'validate-owner-password')
    Assert-ExactStringArray -Value $Value.isolated_profile_services.binance `
        -Label 'guard isolated binance profile' -Expected @(
            'binance-market-stream', 'migrate', 'postgres',
            'provision-runtime-role', 'validate-owner-password'
        )
    Assert-ExactStringArray -Value $Value.isolated_profile_services.telegram `
        -Label 'guard isolated telegram profile' -Expected @(
            'migrate', 'postgres', 'provision-runtime-role', 'telegram',
            'validate-owner-password'
        )
    Assert-ExactStringArray -Value $Value.isolated_profile_services.offsite `
        -Label 'guard isolated offsite profile' `
        -Expected @('backup', 'backup-offsite', 'postgres', 'validate-owner-password')
}

function Assert-BuildCommandEvidence {
    param(
        [Parameter(Mandatory = $true)][object]$Value,
        [Parameter(Mandatory = $true)][string]$ExpectedRevision,
        [Parameter(Mandatory = $true)][string]$ExpectedImage,
        [Parameter(Mandatory = $true)][string]$ExpectedImageId,
        [Parameter(Mandatory = $true)][string]$ExpectedDigest,
        [Parameter(Mandatory = $true)][string]$ExpectedWorktree,
        [Parameter(Mandatory = $true)][string]$DockerSha256,
        [Parameter(Mandatory = $true)][string]$ExpectedDockerPath
    )
    Assert-ExactProperties -Value $Value -Label 'build command evidence' -Expected @(
        'format', 'revision', 'build_worktree', 'docker_executable_path',
        'docker_executable_sha256', 'docker_context', 'docker_endpoint', 'collector',
        'collector_argv', 'image', 'image_id', 'release_digest', 'started_at_utc',
        'completed_at_utc', 'exit_code'
    )
    Assert-HashString -Value $Value.docker_executable_sha256 `
        -Label 'build Docker executable hash'
    Assert-HashString -Value $Value.image_id -Label 'build image id' -WithPrefix
    Assert-HashString -Value $Value.release_digest -Label 'build release digest'
    if (
        $Value.format -cne 'stratus-build-command-v4' -or
        $Value.revision -cne $ExpectedRevision -or
        -not (Test-PathEqual -Left ([string]$Value.build_worktree) -Right $ExpectedWorktree) -or
        $Value.docker_executable_sha256 -cne $DockerSha256 -or
        $Value.image -cne $ExpectedImage -or
        $Value.image_id -cne $ExpectedImageId -or
        $Value.release_digest -cne $ExpectedDigest
    ) {
        throw 'Build command evidence is not bound to the exact candidate.'
    }
    $expectedArguments = @(
        '--context', $script:ExpectedDockerContext, 'buildx', 'build',
        '--platform', 'linux/amd64', '--pull', '--load',
        '--label', "org.opencontainers.image.revision=$ExpectedRevision",
        '--tag', $ExpectedImage, $ExpectedWorktree
    )
    [void](Assert-DockerCollectorBinding -Value $Value -Label 'build command evidence' `
        -ExpectedDockerSha256 $DockerSha256 -ExpectedDockerPath $ExpectedDockerPath `
        -ExpectedCollector 'docker-cli-buildx-build-v1' -ExpectedArgv $expectedArguments)
    Assert-JsonInteger -Value $Value.exit_code -Label 'build exit_code' -Minimum 0
    $started = ConvertTo-CanonicalUtcTime -Value $Value.started_at_utc `
        -Label 'build started_at_utc'
    $completed = ConvertTo-CanonicalUtcTime -Value $Value.completed_at_utc `
        -Label 'build completed_at_utc'
    if ($Value.exit_code -ne 0 -or $completed -lt $started) {
        throw 'Build command evidence is not a completed successful build.'
    }
    return [pscustomobject]@{
        started_at = $started
        completed_at = $completed
    }
}

function Assert-BuildHistoryEvidence {
    param(
        [Parameter(Mandatory = $true)][object]$Value,
        [Parameter(Mandatory = $true)][string]$ExpectedRevision,
        [Parameter(Mandatory = $true)][string]$ExpectedImage,
        [Parameter(Mandatory = $true)][string]$ExpectedImageId,
        [Parameter(Mandatory = $true)][string]$ExpectedDigest,
        [Parameter(Mandatory = $true)][string]$ExpectedWorktree,
        [Parameter(Mandatory = $true)][string]$ExpectedDockerSha256,
        [Parameter(Mandatory = $true)][string]$ExpectedDockerPath
    )
    Assert-ExactProperties -Value $Value -Label 'build history evidence' -Expected @(
        'format', 'revision', 'build_worktree', 'platform', 'image', 'image_id',
        'release_digest', 'status', 'image_resolve_mode', 'build_ref',
        'started_at_utc', 'completed_at_utc', 'attachments', 'docker_executable_path',
        'docker_executable_sha256', 'docker_context', 'docker_endpoint', 'collector',
        'collector_argv'
    )
    Assert-ExactProperties -Value $Value.attachments -Label 'build history attachments' `
        -Expected @('image_index')
    if (
        $Value.format -cne 'stratus-buildx-history-v4' -or
        $Value.revision -cne $ExpectedRevision -or
        -not (Test-PathEqual -Left ([string]$Value.build_worktree) -Right $ExpectedWorktree) -or
        $Value.platform -cne 'linux/amd64' -or
        $Value.image -cne $ExpectedImage -or
        $Value.image_id -cne $ExpectedImageId -or
        $Value.release_digest -cne $ExpectedDigest -or
        $Value.status -cne 'completed' -or
        $Value.image_resolve_mode -cne 'pull' -or
        $Value.attachments.image_index -cne $ExpectedImageId
    ) {
        throw 'Build history is not the exact completed immutable-image history.'
    }
    Assert-JsonString -Value $Value.build_ref -Label 'build history build_ref'
    [void](Assert-DockerCollectorBinding -Value $Value -Label 'build history evidence' `
        -ExpectedDockerSha256 $ExpectedDockerSha256 `
        -ExpectedDockerPath $ExpectedDockerPath `
        -ExpectedCollector 'docker-cli-buildx-history-inspect-v1' -ExpectedArgv @(
            '--context', $script:ExpectedDockerContext, 'buildx', 'history',
            'inspect', [string]$Value.build_ref, '--format', '{{json .}}'
        ))
    $started = ConvertTo-CanonicalUtcTime -Value $Value.started_at_utc `
        -Label 'build history started_at_utc'
    $completed = ConvertTo-CanonicalUtcTime -Value $Value.completed_at_utc `
        -Label 'build history completed_at_utc'
    if ($completed -lt $started) {
        throw 'Build history completion precedes its start.'
    }
    return [pscustomobject]@{
        started_at = $started
        completed_at = $completed
    }
}

function Assert-ProductionServiceStopped {
    param(
        [Parameter(Mandatory = $true)][object]$Value,
        [Parameter(Mandatory = $true)][string]$ExpectedName,
        [Parameter(Mandatory = $true)][string]$Label
    )
    Assert-ExactProperties -Value $Value -Label $Label -Expected @(
        'name', 'container_id', 'image_id', 'running', 'restart_count', 'oom_killed'
    )
    Assert-HashString -Value $Value.image_id -Label "$Label image_id" -WithPrefix
    if ([string]$Value.container_id -cnotmatch '^[0-9a-f]{64}$') {
        throw "$Label container_id is not a full lowercase Docker ID."
    }
    Assert-JsonBoolean -Value $Value.running -Label "$Label running"
    Assert-JsonInteger -Value $Value.restart_count -Label "$Label restart_count" -Minimum 0
    Assert-JsonBoolean -Value $Value.oom_killed -Label "$Label oom_killed"
    if (
        $Value.name -cne $ExpectedName -or
        $Value.running -ne $false -or
        $Value.oom_killed -ne $false
    ) {
        throw "$Label is not exact, stopped, and free of OOM evidence."
    }
}

function Assert-TopologyEvidence {
    param(
        [Parameter(Mandatory = $true)][object]$Value,
        [Parameter(Mandatory = $true)][string]$ExpectedDockerSha256,
        [Parameter(Mandatory = $true)][string]$ExpectedDockerPath
    )
    Assert-ExactProperties -Value $Value -Label 'pre-start topology evidence' -Expected @(
        'format', 'captured_at_utc', 'compose_project',
        'production_or_protected_running_count', 'allowed_running_backup_count',
        'unexpected_running_count', 'active_operator_or_migration_count',
        'live_container_count', 'docker_observation_complete', 'production_services',
        'allowed_backup', 'docker_executable_path', 'docker_executable_sha256',
        'docker_context', 'docker_endpoint', 'collector', 'collector_argv'
    )
    if (
        $Value.format -cne 'stratus-r1-stopped-topology-v4' -or
        $Value.compose_project -cne 'weatherpoly'
    ) {
        throw 'Pre-start topology evidence has the wrong format or Compose project.'
    }
    $capturedAt = ConvertTo-CanonicalUtcTime -Value $Value.captured_at_utc `
        -Label 'topology captured_at_utc'
    foreach ($name in @(
        'production_or_protected_running_count', 'allowed_running_backup_count',
        'unexpected_running_count', 'active_operator_or_migration_count',
        'live_container_count'
    )) {
        Assert-JsonInteger -Value $Value.$name -Label "topology $name" -Minimum 0
    }
    Assert-JsonBoolean -Value $Value.docker_observation_complete `
        -Label 'topology docker_observation_complete'
    if (
        $Value.docker_observation_complete -ne $true -or
        $Value.allowed_running_backup_count -notin @(0, 1) -or
        $Value.production_or_protected_running_count -ne
            $Value.allowed_running_backup_count -or
        $Value.unexpected_running_count -ne 0 -or
        $Value.active_operator_or_migration_count -ne 0 -or
        $Value.live_container_count -ne 0
    ) {
        throw 'Pre-start topology is not the exact fail-closed stopped topology.'
    }
    [void](Assert-DockerCollectorBinding -Value $Value `
        -Label 'pre-start topology evidence' `
        -ExpectedDockerSha256 $ExpectedDockerSha256 `
        -ExpectedDockerPath $ExpectedDockerPath `
        -ExpectedCollector 'stratus-r1-stopped-topology-collector-v1' `
        -ExpectedArgv @(
            '--context', $script:ExpectedDockerContext,
            '--endpoint', $script:ExpectedDockerEndpoint,
            '--project', 'weatherpoly', '--all', '--no-trunc', '--inspect-state'
        ))
    Assert-ExactProperties -Value $Value.production_services `
        -Label 'topology production_services' `
        -Expected @('postgres', 'api', 'worker', 'safety_control')
    Assert-ProductionServiceStopped -Value $Value.production_services.postgres `
        -ExpectedName 'weatherpoly-postgres-1' -Label 'production PostgreSQL'
    Assert-ProductionServiceStopped -Value $Value.production_services.api `
        -ExpectedName 'weatherpoly-api-1' -Label 'production API'
    Assert-ProductionServiceStopped -Value $Value.production_services.worker `
        -ExpectedName 'weatherpoly-worker-1' -Label 'production worker'
    Assert-ProductionServiceStopped -Value $Value.production_services.safety_control `
        -ExpectedName 'safety-control' -Label 'production safety-control'
    if ($Value.allowed_running_backup_count -eq 0) {
        if ($null -ne $Value.allowed_backup) {
            throw 'Topology reports an allowed backup object while its count is zero.'
        }
    }
    else {
        Assert-ExactProperties -Value $Value.allowed_backup -Label 'allowed backup' -Expected @(
            'name', 'container_id', 'image_id', 'running', 'restart_count', 'oom_killed',
            'compose_project', 'compose_service'
        )
        Assert-HashString -Value $Value.allowed_backup.image_id `
            -Label 'allowed backup image_id' -WithPrefix
        if (
            [string]$Value.allowed_backup.container_id -cnotmatch '^[0-9a-f]{64}$' -or
            $Value.allowed_backup.name -cne 'weatherpoly-backup-1' -or
            $Value.allowed_backup.compose_project -cne 'weatherpoly' -or
            $Value.allowed_backup.compose_service -cne 'backup'
        ) {
            throw 'Allowed backup does not have the exact reviewed identity.'
        }
        Assert-JsonBoolean -Value $Value.allowed_backup.running -Label 'allowed backup running'
        Assert-JsonInteger -Value $Value.allowed_backup.restart_count `
            -Label 'allowed backup restart_count' -Minimum 0
        Assert-JsonBoolean -Value $Value.allowed_backup.oom_killed `
            -Label 'allowed backup oom_killed'
        if ($Value.allowed_backup.running -ne $true -or $Value.allowed_backup.oom_killed -ne $false) {
            throw 'Allowed backup is not a running, non-OOM sidecar.'
        }
    }
    return $capturedAt
}

function Assert-SafeGitConfiguration {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $configurationProof = Invoke-BoundedGit -Arguments @(
        '-C', $Root, 'config', '--name-only', '--get-regexp', '.*'
    ) -TimeoutMilliseconds 10000 -AcceptedExitCodes @(0, 1)
    if (
        $configurationProof.exit_code -eq 1 -and
        -not [string]::IsNullOrEmpty([string]$configurationProof.stdout)
    ) {
        throw "$Label Git configuration returned output with its no-match exit."
    }
    $names = @(
        $configurationProof.stdout -split '\r?\n' |
            Where-Object { -not [string]::IsNullOrEmpty([string]$_) }
    )
    foreach ($name in $names) {
        if (
            $name -isnot [string] -or
            [string]::IsNullOrWhiteSpace([string]$name) -or
            [string]$name -notmatch '^[A-Za-z0-9][A-Za-z0-9./-]*$'
        ) {
            throw "$Label Git configuration contains a non-canonical key name."
        }
        $normalizedName = ([string]$name).ToLowerInvariant()
        if ($normalizedName -match (
            '^(?:filter\.|url\.|include(?:if)?\.|http\.|credential\.|feature\.|' +
            'core\.(?:attributesfile|excludesfile|fsmonitor|hookspath|ignorestat|' +
            'trustctime|checkstat|untrackedcache|worktree|sshcommand)(?:\.|$))'
        )) {
            throw "$Label Git configuration contains a forbidden key: $name"
        }
        if ($normalizedName -notmatch (
            '^(?:core\.(?:repositoryformatversion|filemode|bare|logallrefupdates|' +
            'symlinks|ignorecase|autocrlf|eol)|user\.(?:name|email)|' +
            'remote\.[a-z0-9._/-]+\.(?:url|fetch)|' +
            'branch\.[a-z0-9._/-]+\.(?:remote|merge))$'
        )) {
            throw "$Label Git configuration contains an unsupported key: $name"
        }
    }
}

function Get-ExactGitTreeEntries {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$ExpectedRevision
    )
    $records = @(
        Invoke-ExactGit -Arguments @(
            '-c', 'core.quotePath=false', '-C', $Root, 'ls-tree', '-r',
            '--full-tree',
            '--format=%(objectmode)%x09%(objecttype)%x09%(objectname)%x09%(path)',
            $ExpectedRevision
        )
    )
    if ($records.Count -eq 0) {
        throw 'Exact Git tree enumeration is empty.'
    }
    $byPath = [Collections.Generic.Dictionary[string, object]]::new(
        [StringComparer]::Ordinal
    )
    $nativePaths = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::OrdinalIgnoreCase
    )
    foreach ($record in $records) {
        $parts = @(([string]$record).Split("`t"))
        if (
            $parts.Count -ne 4 -or
            $parts[0] -cnotmatch '^100(?:644|755)$' -or
            $parts[1] -cne 'blob' -or
            $parts[2] -cnotmatch '^[0-9a-f]{40}$' -or
            [string]::IsNullOrWhiteSpace($parts[3]) -or
            $parts[3] -match '[\t\r\n]' -or
            $parts[3] -match '^"' -or
            $byPath.ContainsKey($parts[3]) -or
            -not $nativePaths.Add($parts[3])
        ) {
            throw 'Exact Git tree contains an unsupported or ambiguous path/blob record.'
        }
        $byPath.Add($parts[3], [pscustomobject]@{
            path = $parts[3]
            mode = $parts[0]
            oid = $parts[2]
        })
    }
    $paths = [string[]]@($byPath.Keys)
    [Array]::Sort($paths, [StringComparer]::Ordinal)
    if ($paths.Count -ne $records.Count) {
        throw 'Exact Git tree path/blob mapping lost a record while sorting.'
    }
    return [pscustomobject]@{
        by_path = $byPath
        paths = $paths
    }
}

function Assert-NoMutableGitAttributes {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][object]$TreeEntries,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $infoAttributePaths = @(
        Invoke-ExactGit -Arguments @(
            '-C', $Root, 'rev-parse', '--git-path', 'info/attributes'
        )
    )
    if ($infoAttributePaths.Count -ne 1) {
        throw "$Label Git info/attributes path is ambiguous."
    }
    $infoAttributesPath = [string]$infoAttributePaths[0]
    if (-not [IO.Path]::IsPathRooted($infoAttributesPath)) {
        $infoAttributesPath = Get-FullPath -LiteralPath (
            Join-Path $Root $infoAttributesPath
        )
    }
    if (Test-Path -LiteralPath $infoAttributesPath) {
        throw "$Label Git repository contains forbidden mutable info/attributes."
    }

    foreach ($relativePath in $TreeEntries.paths) {
        if ([string]$relativePath -cnotmatch '(?:^|/)\.gitattributes$') {
            continue
        }
        $nativeRelative = ([string]$relativePath).Replace(
            '/',
            [IO.Path]::DirectorySeparatorChar
        )
        $attributePath = Join-Path $Root $nativeRelative
        [void](Assert-PathBelowRoot -Root $Root -LiteralPath $attributePath `
            -Label "$Label tracked attributes")
        Assert-NoReparseComponents -Root $Root -LiteralPath $attributePath `
            -Label "$Label tracked attributes"
        $attributeBytes = Get-ExclusiveFileBytes -LiteralPath $attributePath `
            -MaximumBytes 1048576 -Label "$Label tracked attributes"
        $attributeText = ConvertFrom-StrictUtf8Text -Bytes $attributeBytes `
            -Label "$Label tracked attributes"
        foreach ($rawLine in $attributeText.Split("`n")) {
            $line = $rawLine.Trim()
            if ([string]::IsNullOrEmpty($line) -or $line.StartsWith('#')) {
                continue
            }
            $tokens = @([regex]::Split($line, '\s+'))
            for ($index = 1; $index -lt $tokens.Count; $index++) {
                if ($tokens[$index] -cmatch (
                    '^(?:[-!]?(?:filter|working-tree-encoding|ident)(?:=.*)?)$'
                )) {
                    throw (
                        "$Label tracked attributes enable forbidden filter or " +
                        "working-tree-encoding or ident semantics: $relativePath"
                    )
                }
            }
        }
    }
}

function Assert-TrackedBytesMatchGitTree {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][object]$TreeEntries,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $manifestBuilder = [Text.StringBuilder]::new()
    foreach ($relativePath in $TreeEntries.paths) {
        $nativeRelative = ([string]$relativePath).Replace(
            '/',
            [IO.Path]::DirectorySeparatorChar
        )
        $sourcePath = Join-Path $Root $nativeRelative
        [void](Assert-PathBelowRoot -Root $Root -LiteralPath $sourcePath `
            -Label "$Label tracked source path")
        Assert-NoReparseComponents -Root $Root -LiteralPath $sourcePath `
            -Label "$Label tracked source path"
        Assert-RegularFile -LiteralPath $sourcePath `
            -Label "$Label tracked source path" -AllowEmpty
        # Keep the exact file immutable while Git normalizes its tracked EOL
        # semantics and while the manifest hashes its actual on-disk bytes.
        $sourceStream = [IO.File]::Open(
            $sourcePath, [IO.FileMode]::Open, [IO.FileAccess]::Read,
            [IO.FileShare]::Read
        )
        $sourceHasher = [Security.Cryptography.SHA256]::Create()
        try {
            $blobProof = @(
                Invoke-ExactGit -Arguments @(
                    '-C', $Root, 'hash-object', "--path=$relativePath", '--', $sourcePath
                )
            )
            $expectedBlob = [string]$TreeEntries.by_path[$relativePath].oid
            if (
                $blobProof.Count -ne 1 -or
                $blobProof[0] -cnotmatch '^[0-9a-f]{40}$' -or
                $blobProof[0] -cne $expectedBlob
            ) {
                throw "$Label tracked bytes do not normalize to the exact tree blob: $relativePath"
            }
            $actualLength = $sourceStream.Length
            $actualHash = [BitConverter]::ToString(
                $sourceHasher.ComputeHash($sourceStream)
            ).Replace('-', '').ToLowerInvariant()
            [void]$manifestBuilder.Append([string]$relativePath)
            [void]$manifestBuilder.Append("`t")
            [void]$manifestBuilder.Append(
                $actualLength.ToString([Globalization.CultureInfo]::InvariantCulture)
            )
            [void]$manifestBuilder.Append("`t")
            [void]$manifestBuilder.Append($actualHash)
            [void]$manifestBuilder.Append("`n")
        }
        finally {
            $sourceHasher.Dispose()
            $sourceStream.Dispose()
        }
    }
    return $script:Utf8NoBomStrict.GetBytes($manifestBuilder.ToString())
}

function Assert-ExactBuildContextEntries {
    param(
        [Parameter(Mandatory = $true)][string]$Worktree,
        [Parameter(Mandatory = $true)][string[]]$TrackedPaths
    )
    if ($TrackedPaths -cnotcontains '.dockerignore') {
        throw 'Exact build context requires a tracked root .dockerignore file.'
    }
    if (Test-Path -LiteralPath (Join-Path $Worktree 'Dockerfile.dockerignore')) {
        throw 'Exact build context forbids a Dockerfile-specific ignore override.'
    }
    $dockerIgnorePath = Join-Path $Worktree '.dockerignore'
    $dockerIgnoreBytes = Get-ExclusiveFileBytes -LiteralPath $dockerIgnorePath `
        -MaximumBytes 1048576 -Label 'build worktree .dockerignore'
    $dockerIgnoreText = ConvertFrom-StrictUtf8Text -Bytes $dockerIgnoreBytes `
        -Label 'build worktree .dockerignore' -RequireTrailingLf
    $gitAdministrativeEntryExcluded = $false
    foreach ($rawRule in $dockerIgnoreText.TrimEnd("`n").Split("`n")) {
        $rule = $rawRule.Trim()
        if ([string]::IsNullOrEmpty($rule) -or $rule.StartsWith('#')) {
            continue
        }
        if ($rule -cin @('.git', '/.git')) {
            $gitAdministrativeEntryExcluded = $true
            continue
        }
        if ($rule.StartsWith('!')) {
            # This is deliberately a restricted root-exclusion policy, not a
            # partial implementation of Docker's pattern language. Re-inclusion
            # may only begin with a literal non-dot root name and may not have
            # path-cleaning or escape syntax that can change that root name.
            $includeRule = $rule.Substring(1)
            $firstSegment = ($includeRule -split '/')[0]
            if (
                $firstSegment -cnotmatch '^[A-Za-z0-9_-][A-Za-z0-9_.-]*$' -or
                $includeRule.Contains('\') -or
                @($includeRule.Split('/') | Where-Object { $_ -cin @('.', '..') }).Count -ne 0
            ) {
                throw 'Build worktree .dockerignore has an unsupported root re-inclusion rule.'
            }
        }
    }
    if (-not $gitAdministrativeEntryExcluded) {
        throw 'Build worktree .dockerignore does not exclude the root .git entry.'
    }

    $tracked = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal
    )
    $trackedDirectories = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal
    )
    [void]$trackedDirectories.Add('')
    foreach ($trackedPath in $TrackedPaths) {
        [void]$tracked.Add($trackedPath)
        $separatorIndex = $trackedPath.LastIndexOf('/')
        while ($separatorIndex -ge 0) {
            $directoryPath = $trackedPath.Substring(0, $separatorIndex)
            [void]$trackedDirectories.Add($directoryPath)
            $separatorIndex = $directoryPath.LastIndexOf('/')
        }
    }

    $worktreePrefix = $Worktree.TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    ) + [IO.Path]::DirectorySeparatorChar
    $pending = [Collections.Generic.Stack[IO.DirectoryInfo]]::new()
    $pending.Push([IO.DirectoryInfo]::new($Worktree))
    while ($pending.Count -ne 0) {
        $directory = $pending.Pop()
        foreach ($entry in $directory.GetFileSystemInfos()) {
            $fullEntryPath = Get-FullPath -LiteralPath $entry.FullName
            if (-not $fullEntryPath.StartsWith($worktreePrefix, $script:PathComparison)) {
                throw 'Build context enumeration escaped the detached worktree.'
            }
            $relativePath = $fullEntryPath.Substring($worktreePrefix.Length).
                Replace([IO.Path]::DirectorySeparatorChar, '/')
            if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Build context contains a reparse entry: $relativePath"
            }
            if ($entry -is [IO.DirectoryInfo]) {
                if (-not $trackedDirectories.Contains($relativePath)) {
                    throw "Build context contains an untracked directory: $relativePath"
                }
                $pending.Push([IO.DirectoryInfo]$entry)
                continue
            }
            if ($relativePath -ceq '.git') {
                if ($entry -isnot [IO.FileInfo] -or $entry.Length -le 0) {
                    throw 'Detached worktree .git entry is not one regular metadata file.'
                }
                continue
            }
            if (-not $tracked.Contains($relativePath)) {
                throw "Build context contains an untracked file: $relativePath"
            }
        }
    }
}

function Get-GitAndSourceEvidence {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Worktree,
        [Parameter(Mandatory = $true)][string]$ExpectedRevision,
        [Parameter(Mandatory = $true)][string]$ExpectedRemote,
        [Parameter(Mandatory = $true)][string]$ExpectedBranch,
        [ValidateRange(1000, 30000)][int]$RemoteTimeoutMilliseconds = 30000
    )
    Assert-SafeGitConfiguration -Root $Root -Label 'Production checkout'
    Assert-SafeGitConfiguration -Root $Worktree -Label 'Build worktree'
    $productionHead = @(
        Invoke-ExactGit -Arguments @('-C', $Root, 'rev-parse', 'HEAD')
    )
    $verifiedCommit = @(
        Invoke-ExactGit -Arguments @('-C', $Root, 'rev-parse', '--verify',
            "$ExpectedRevision^{commit}")
    )
    $tree = @(
        Invoke-ExactGit -Arguments @('-C', $Root, 'rev-parse', "$ExpectedRevision^{tree}")
    )
    $treeEntries = Get-ExactGitTreeEntries -Root $Root `
        -ExpectedRevision $ExpectedRevision
    Assert-NoMutableGitAttributes -Root $Root -TreeEntries $treeEntries `
        -Label 'Production checkout'
    Assert-NoMutableGitAttributes -Root $Worktree -TreeEntries $treeEntries `
        -Label 'Build worktree'
    $productionStatus = @(
        Invoke-ExactGit -Arguments @('-C', $Root, 'status', '--porcelain=v1',
            '--untracked-files=all')
    )
    $productionBranch = @(
        Invoke-ExactGit -Arguments @('-C', $Root, 'symbolic-ref', '-q', '--short', 'HEAD')
    )
    $upstream = @(
        Invoke-ExactGit -Arguments @('-C', $Root, 'rev-parse', '--abbrev-ref',
            '--symbolic-full-name', '@{upstream}')
    )
    $remoteRevision = @(
        Invoke-ExactGit -Arguments @('-C', $Root, 'rev-parse',
            "refs/remotes/$ExpectedRemote/$ExpectedBranch")
    )
    $remoteUrls = @(
        Invoke-ExactGit -Arguments @('-C', $Root, 'remote', 'get-url', '--all',
            $ExpectedRemote)
    )
    $aheadBehind = @(
        Invoke-ExactGit -Arguments @('-C', $Root, 'rev-list', '--left-right', '--count',
            "$ExpectedRevision...refs/remotes/$ExpectedRemote/$ExpectedBranch")
    )
    $productionIndexFlags = @(
        Invoke-ExactGit -Arguments @('-C', $Root, 'ls-files', '-v')
    )
    $productionReplaceRefs = @(
        Invoke-ExactGit -Arguments @('-C', $Root, 'for-each-ref',
            '--format=%(refname)', 'refs/replace')
    )
    $productionForbiddenIndexFlags = @(
        $productionIndexFlags | Where-Object { $_ -cmatch '^(?:[a-z]|S) ' }
    )
    $localRewriteProof = Invoke-BoundedGit -Arguments @(
        '-C', $Root, 'config', '--get-regexp',
        '^(url\..*\.(insteadof|pushinsteadof)|include(\.path|if\..*\.path))$'
    ) -TimeoutMilliseconds 10000 -AcceptedExitCodes @(1)
    if (
        $productionHead.Count -ne 1 -or $productionHead[0] -cne $ExpectedRevision -or
        $verifiedCommit.Count -ne 1 -or $verifiedCommit[0] -cne $ExpectedRevision -or
        $tree.Count -ne 1 -or [string]$tree[0] -cnotmatch '^[0-9a-f]{40}$' -or
        $productionStatus.Count -ne 0 -or
        $productionBranch.Count -ne 1 -or $productionBranch[0] -cne $ExpectedBranch -or
        $upstream.Count -ne 1 -or $upstream[0] -cne "$ExpectedRemote/$ExpectedBranch" -or
        $remoteRevision.Count -ne 1 -or $remoteRevision[0] -cne $ExpectedRevision -or
        $remoteUrls.Count -ne 1 -or $remoteUrls[0] -cne $script:ExpectedRemoteUrl -or
        $localRewriteProof.exit_code -ne 1 -or
        -not [string]::IsNullOrEmpty([string]$localRewriteProof.stdout) -or
        $aheadBehind.Count -ne 1 -or
        ([string]$aheadBehind[0]).Trim() -cnotmatch '^0\s+0$'
    ) {
        throw 'Production Git checkout is not the exact clean pushed release revision.'
    }
    if ($productionReplaceRefs.Count -ne 0) {
        throw 'Production Git repository contains forbidden refs/replace entries.'
    }
    if ($productionForbiddenIndexFlags.Count -ne 0) {
        throw 'Production Git index contains assume-unchanged or skip-worktree flags.'
    }
    $neutralConfiguration = @(Invoke-ExactGit -Arguments @(
        '-C', $script:GitWorkingDirectory, 'config', '--name-only', '--list'
    ))
    if ($neutralConfiguration.Count -ne 0) {
        throw 'Neutral Git working directory unexpectedly supplies Git configuration.'
    }
    $liveRemoteProof = Invoke-BoundedGit -Arguments @(
        '-C', $script:GitWorkingDirectory, 'ls-remote', '--exit-code', $script:ExpectedRemoteUrl,
        "refs/heads/$ExpectedBranch"
    ) -TimeoutMilliseconds $RemoteTimeoutMilliseconds -AcceptedExitCodes @(0)
    $expectedLiveRemoteLine =
        "$ExpectedRevision`trefs/heads/$ExpectedBranch"
    $liveRemoteLines = @(
        $liveRemoteProof.stdout -split '\r?\n' |
            Where-Object { -not [string]::IsNullOrEmpty([string]$_) }
    )
    if (
        $liveRemoteLines.Count -ne 1 -or
        $liveRemoteLines[0] -cne $expectedLiveRemoteLine
    ) {
        throw 'Live remote branch proof did not return the exact release revision.'
    }

    $worktreeRoot = @(
        Invoke-ExactGit -Arguments @('-C', $Worktree, 'rev-parse', '--show-toplevel')
    )
    $worktreeHead = @(
        Invoke-ExactGit -Arguments @('-C', $Worktree, 'rev-parse', 'HEAD')
    )
    $worktreeStatus = @(
        Invoke-ExactGit -Arguments @('-C', $Worktree, 'status', '--porcelain=v1',
            '--untracked-files=all')
    )
    $worktreeDockerContextStatus = @(
        Invoke-ExactGit -Arguments @('-C', $Worktree, 'status', '--porcelain=v1',
            '--untracked-files=all', '--ignored=matching')
    )
    $worktreeIndexFlags = @(
        Invoke-ExactGit -Arguments @('-C', $Worktree, 'ls-files', '-v')
    )
    $worktreeReplaceRefs = @(
        Invoke-ExactGit -Arguments @('-C', $Worktree, 'for-each-ref',
            '--format=%(refname)', 'refs/replace')
    )
    $worktreeForbiddenIndexFlags = @(
        $worktreeIndexFlags | Where-Object { $_ -cmatch '^(?:[a-z]|S) ' }
    )
    $detachedProof = Invoke-BoundedGit -Arguments @(
        '-C', $Worktree, 'symbolic-ref', '-q', '--short', 'HEAD'
    ) -TimeoutMilliseconds 10000 -AcceptedExitCodes @(1)
    if (
        $worktreeRoot.Count -ne 1 -or
        -not (Test-PathEqual -Left ([string]$worktreeRoot[0]) -Right $Worktree) -or
        $worktreeHead.Count -ne 1 -or $worktreeHead[0] -cne $ExpectedRevision -or
        $worktreeStatus.Count -ne 0 -or
        $detachedProof.exit_code -ne 1 -or
        -not [string]::IsNullOrEmpty([string]$detachedProof.stdout)
    ) {
        throw 'Build worktree is not clean, detached, and exact-revision bound.'
    }
    if ($worktreeReplaceRefs.Count -ne 0) {
        throw 'Build worktree Git repository contains forbidden refs/replace entries.'
    }
    if ($worktreeForbiddenIndexFlags.Count -ne 0) {
        throw 'Build worktree Git index contains assume-unchanged or skip-worktree flags.'
    }
    if ($worktreeDockerContextStatus.Count -ne 0) {
        throw 'Build worktree contains ignored entries outside the exact Git source context.'
    }

    [void](Assert-TrackedBytesMatchGitTree -Root $Root -TreeEntries $treeEntries `
        -Label 'Production checkout')
    $manifestBytes = [byte[]](Assert-TrackedBytesMatchGitTree -Root $Worktree -TreeEntries $treeEntries `
        -Label 'Build worktree')
    $trackedPaths = [string[]]$treeEntries.paths
    Assert-ExactBuildContextEntries -Worktree $Worktree -TrackedPaths $trackedPaths
    $manifestHash = Get-Sha256Bytes -Bytes $manifestBytes
    $capturedAt = [DateTimeOffset]::UtcNow.UtcDateTime.ToString('o')
    $gitEvidence = [ordered]@{
        format = 'stratus-r0-git-evidence-v3'
        captured_at_utc = $capturedAt
        production_root = $Root
        revision = $ExpectedRevision
        tree = [string]$tree[0]
        branch = $ExpectedBranch
        upstream = "$ExpectedRemote/$ExpectedBranch"
        remote_url = $script:ExpectedRemoteUrl
        remote_ref = "refs/remotes/$ExpectedRemote/$ExpectedBranch"
        remote_revision = $ExpectedRevision
        ahead = 0
        behind = 0
        production_checkout_clean = $true
        replacement_refs_absent = $true
        dangerous_git_configuration_absent = $true
        mutable_git_attributes_absent = $true
        tracked_bytes_match_tree_blobs = $true
        live_remote_repository_config_absent = $true
        build_worktree = $Worktree
        build_worktree_revision = $ExpectedRevision
        build_worktree_clean = $true
        build_worktree_detached = $true
        build_worktree_ignored_entries_absent = $true
        build_context_entries_exact = $true
        root_git_entry_docker_excluded = $true
    }
    $sourceSummary = [ordered]@{
        format = 'stratus-source-digest-v3'
        revision = $ExpectedRevision
        tree = [string]$tree[0]
        worktree = $Worktree
        detached = $true
        clean = $true
        path_count = $trackedPaths.Count
        aggregate_sha256 = $manifestHash
        record_format = 'git_path<TAB>working_tree_byte_length<TAB>lowercase_sha256'
        record_delimiter = 'LF'
        trailing_delimiter = $true
        encoding = 'UTF-8 without BOM'
    }
    return [pscustomobject]@{
        git = $gitEvidence
        source_manifest_bytes = $manifestBytes
        source_summary = $sourceSummary
        source_path_count = $trackedPaths.Count
        source_aggregate_sha256 = $manifestHash
        tree = [string]$tree[0]
    }
}

function Get-HistoricalEvidence {
    param([Parameter(Mandatory = $true)][string]$Root)
    $backupDirectory = Get-FullPath -LiteralPath (
        Join-Path $Root $script:HistoricalBackupRelativePath
    )
    Assert-NoReparseComponents -Root $Root -LiteralPath $backupDirectory `
        -Label 'historical backup directory'
    Assert-RegularDirectory -LiteralPath $backupDirectory -Label 'historical backup directory'
    $actualBackupNames = @(
        Get-ChildItem -LiteralPath $backupDirectory -Force |
            ForEach-Object { $_.Name }
    )
    $expectedBackupNames = @($script:HistoricalBackupFiles.Keys)
    [Array]::Sort($actualBackupNames, [StringComparer]::Ordinal)
    [Array]::Sort($expectedBackupNames, [StringComparer]::Ordinal)
    if (($actualBackupNames -join "`n") -cne ($expectedBackupNames -join "`n")) {
        throw 'Historical pre-0047 backup directory contents drifted.'
    }
    $backupHashes = [ordered]@{}
    foreach ($entry in $script:HistoricalBackupFiles.GetEnumerator()) {
        $path = Join-Path $backupDirectory ([string]$entry.Key)
        Assert-NoReparseComponents -Root $Root -LiteralPath $path `
            -Label "historical backup $($entry.Key)"
        $actualHash = Get-RegularFileSha256 -LiteralPath $path `
            -Label "historical backup $($entry.Key)"
        if ($actualHash -cne [string]$entry.Value) {
            throw "Historical pre-0047 backup hash drifted: $($entry.Key)"
        }
        $backupHashes[[string]$entry.Key] = $actualHash
    }

    $lineageDirectory = Get-FullPath -LiteralPath (
        Join-Path $Root $script:HistoricalLineageRelativePath
    )
    Assert-NoReparseComponents -Root $Root -LiteralPath $lineageDirectory `
        -Label 'historical 0046-to-0047 lineage directory'
    Assert-RegularDirectory -LiteralPath $lineageDirectory `
        -Label 'historical 0046-to-0047 lineage directory'
    $lineageHashes = [ordered]@{}
    foreach ($entry in $script:HistoricalLineageFiles.GetEnumerator()) {
        $path = Join-Path $lineageDirectory ([string]$entry.Key)
        Assert-NoReparseComponents -Root $Root -LiteralPath $path `
            -Label "historical lineage $($entry.Key)"
        $actualHash = Get-RegularFileSha256 -LiteralPath $path `
            -Label "historical lineage $($entry.Key)"
        if ($actualHash -cne [string]$entry.Value) {
            throw "Historical 0046-to-0047 lineage hash drifted: $($entry.Key)"
        }
        $lineageHashes[[string]$entry.Key] = $actualHash
    }

    $closurePath = Get-FullPath -LiteralPath (
        Join-Path $Root $script:ForensicClosureRelativePath
    )
    Assert-NoReparseComponents -Root $Root -LiteralPath $closurePath `
        -Label 'historical forensic closure'
    if ((Get-RegularFileSha256 -LiteralPath $closurePath `
        -Label 'historical forensic closure') -cne $script:ForensicClosureSha256) {
        throw 'Historical Section 3 forensic closure hash drifted.'
    }
    $closureBytes = Get-ExclusiveFileBytes -LiteralPath $closurePath `
        -MaximumBytes 1048576 -Label 'historical forensic closure'
    $closure = ConvertFrom-StrictJsonBytes -Bytes $closureBytes `
        -Label 'historical forensic closure'
    if (
        $closure.format -cne 'stratus-section3-forensic-attempt-closure-v1' -or
        $closure.classification -cne 'SEV-2_RELEASE_CONTROL' -or
        $closure.decision -cne 'ABORTED_AFTER_SUCCESSFUL_0047_MIGRATION_AT_SECTION4' -or
        $closure.successor_candidate_separation.previous_start_authorization_superseded -ne $true -or
        $closure.authorization.migration_container_start_permitted -ne $false -or
        $closure.authorization.live_transition_permitted -ne $false -or
        $closure.authorization.binance_kill_switch_removal_permitted -ne $false
    ) {
        throw 'Historical Section 3 forensic closure is not the exact fail-closed closure.'
    }
    return [ordered]@{
        format = 'stratus-r1-historical-0047-inputs-v3'
        role = 'read_only_historical_input_only'
        backup_directory = $backupDirectory
        backup_sha256 = $backupHashes
        lineage_directory = $lineageDirectory
        lineage_sha256 = $lineageHashes
        forensic_closure_path = $closurePath
        forensic_closure_sha256 = $script:ForensicClosureSha256
        previous_start_authorization_superseded = $true
        historical_attempt_authorizes_new_start = $false
        migration_start_permitted = $false
    }
}

function New-ExclusiveDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $reservationPath = "$LiteralPath.reservation"
    $reservationToken = [guid]::NewGuid().ToString('N')
    $reservationBytes = $script:Utf8NoBomStrict.GetBytes("$reservationToken`n")
    $reservation = $null
    $primaryError = $null
    $cleanupError = $null
    try {
        $reservation = [IO.File]::Open(
            $reservationPath,
            [IO.FileMode]::CreateNew,
            [IO.FileAccess]::ReadWrite,
            [IO.FileShare]::None
        )
        $reservation.Write($reservationBytes, 0, $reservationBytes.Length)
        $reservation.Flush($true)
        if (Test-Path -LiteralPath $LiteralPath) {
            throw "$Label already exists: $LiteralPath"
        }
        [void][IO.Directory]::CreateDirectory($LiteralPath)
        Assert-RegularDirectory -LiteralPath $LiteralPath -Label $Label
        if (@(Get-ChildItem -LiteralPath $LiteralPath -Force).Count -ne 0) {
            throw "$Label was not created empty."
        }
    }
    catch {
        $primaryError = $_
    }
    try {
        if ($null -ne $reservation) {
            $reservation.Dispose()
            $reservation = $null
            Assert-RegularFile -LiteralPath $reservationPath `
                -Label "$Label reservation"
            $currentBytes = [IO.File]::ReadAllBytes($reservationPath)
            if (
                $currentBytes.Length -ne $reservationBytes.Length -or
                (Get-Sha256Bytes -Bytes $currentBytes) -cne
                    (Get-Sha256Bytes -Bytes $reservationBytes)
            ) {
                throw "$Label reservation ownership changed; it was preserved."
            }
            Remove-Item -LiteralPath $reservationPath -Force
        }
    }
    catch {
        $cleanupError = $_
    }
    if ($null -ne $primaryError -and $null -ne $cleanupError) {
        throw (
            "$Label creation failed: $($primaryError.Exception.Message) " +
            "Reservation cleanup also failed: $($cleanupError.Exception.Message)"
        )
    }
    if ($null -ne $primaryError) {
        throw $primaryError
    }
    if ($null -ne $cleanupError) {
        throw $cleanupError
    }
}

if ($Branch.Contains('..') -or $Branch.EndsWith('/') -or $Branch.Contains('//')) {
    throw 'Branch is not a canonical safe branch name.'
}

$productionRootFull = Get-FullPath -LiteralPath $ProductionRoot
$buildWorktreeFull = Get-FullPath -LiteralPath $BuildWorktree
$evidenceInputFull = Get-FullPath -LiteralPath $EvidenceInputDirectory
$gitExecutableFull = Get-FullPath -LiteralPath $GitExecutablePath

Assert-RegularDirectory -LiteralPath $productionRootFull -Label 'production root'
Assert-RegularDirectory -LiteralPath $buildWorktreeFull -Label 'build worktree'
Assert-NoReparseAbsolutePath -LiteralPath $productionRootFull -Label 'production root'
Assert-NoReparseAbsolutePath -LiteralPath $buildWorktreeFull -Label 'build worktree'
Assert-NoReparseAbsolutePath -LiteralPath $gitExecutableFull -Label 'Git executable'
if (Test-PathEqual -Left $buildWorktreeFull -Right $productionRootFull) {
    throw 'Build worktree must be a separate detached checkout.'
}
Assert-NoReparseComponents -Root $productionRootFull -LiteralPath $evidenceInputFull `
    -Label 'R1 evidence input directory'
Assert-RegularDirectory -LiteralPath $evidenceInputFull -Label 'R1 evidence input directory'
Assert-RegularFile -LiteralPath $gitExecutableFull -Label 'Git executable'
$script:GitExecutable = $gitExecutableFull

foreach ($environmentName in [Environment]::GetEnvironmentVariables().Keys) {
    $candidateName = [string]$environmentName
    if (
        $candidateName.StartsWith('GIT_', [StringComparison]::OrdinalIgnoreCase) -or
        $candidateName -iin @('SSH_ASKPASS', 'XDG_CONFIG_HOME')
    ) {
        throw "Git override environment variable is forbidden: $candidateName"
    }
}

$outputRoot = Get-FullPath -LiteralPath (
    Join-Path $productionRootFull '.stratus-cache\release-transitions'
)
$cutoversRoot = Get-FullPath -LiteralPath (
    Join-Path $productionRootFull '.stratus-cache\cutovers'
)
Assert-NoReparseComponents -Root $productionRootFull -LiteralPath $outputRoot `
    -Label 'R1 seal output root'
Assert-RegularDirectory -LiteralPath $outputRoot -Label 'R1 seal output root'
Assert-NoReparseComponents -Root $productionRootFull -LiteralPath $cutoversRoot `
    -Label 'successor cutover root'
Assert-RegularDirectory -LiteralPath $cutoversRoot -Label 'successor cutover root'
$script:GitWorkingDirectory = Get-FullPath -LiteralPath (
    Split-Path -Parent $gitExecutableFull
)
Assert-NoReparseAbsolutePath -LiteralPath $script:GitWorkingDirectory `
    -Label 'neutral Git working directory'
Assert-RegularDirectory -LiteralPath $script:GitWorkingDirectory `
    -Label 'neutral Git working directory'
foreach ($metadataName in @('.git', 'HEAD', 'objects', 'refs')) {
    if (Test-Path -LiteralPath (Join-Path $script:GitWorkingDirectory $metadataName)) {
        throw 'Neutral Git working directory must not contain repository metadata.'
    }
}
if ($script:GitWorkingDirectory.Contains([string][IO.Path]::PathSeparator)) {
    throw 'Neutral Git working directory cannot contain the path-list separator.'
}

$lockPath = Join-Path $outputRoot 'r1-seal.lock'
$lockToken = [guid]::NewGuid().ToString('N')
$lockBytes = $script:Utf8NoBomStrict.GetBytes("$lockToken`n")
$lockStream = $null
$script:GitExecutableHandle = $null
$primaryError = $null
$cleanupError = $null
$result = $null
$resultJson = $null
$publicationCommitted = $false

try {
    $script:GitExecutableHandle = [IO.File]::Open(
        $gitExecutableFull,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read
    )
    $heldGitHasher = [Security.Cryptography.SHA256]::Create()
    try {
        $heldGitSha256 = [BitConverter]::ToString(
            $heldGitHasher.ComputeHash($script:GitExecutableHandle)
        ).Replace('-', '').ToLowerInvariant()
        $script:GitExecutableHandle.Position = 0
    }
    finally {
        $heldGitHasher.Dispose()
    }
    if ($heldGitSha256 -cne $ExpectedGitSha256) {
        throw 'Held Git executable bytes do not match the source-approved SHA-256.'
    }
    $lockStream = [IO.File]::Open(
        $lockPath,
        [IO.FileMode]::CreateNew,
        [IO.FileAccess]::ReadWrite,
        [IO.FileShare]::None
    )
    $lockStream.Write($lockBytes, 0, $lockBytes.Length)
    $lockStream.Flush($true)

    $input = Read-InputEvidence -InputDirectory $evidenceInputFull
    $ci = ConvertFrom-StrictJsonBytes -Bytes $input.files['ci-evidence.json'] `
        -Label 'CI evidence'
    $imageEvidence = ConvertFrom-StrictJsonBytes `
        -Bytes $input.files['image-inspect.json'] -Label 'image evidence'
    $guard = ConvertFrom-StrictJsonBytes -Bytes $input.files['release-guard.json'] `
        -Label 'release guard evidence'
    $buildCommand = ConvertFrom-StrictJsonBytes `
        -Bytes $input.files['build-command.json'] -Label 'build command evidence'
    $buildHistory = ConvertFrom-StrictJsonBytes `
        -Bytes $input.files['build-history.json'] -Label 'build history evidence'
    $topology = ConvertFrom-StrictJsonBytes `
        -Bytes $input.files['pre-start-topology.json'] -Label 'pre-start topology evidence'

    $image = "stratus:candidate-$($Revision.Substring(0, 12))"
    $ciObservedAt = Assert-CiEvidence -Value $ci -ExpectedRevision $Revision `
        -ExpectedRemote $Remote -ExpectedBranch $Branch
    $imageProof = Assert-ImageEvidence -Value $imageEvidence `
        -ExpectedRevision $Revision -ExpectedImage $image `
        -ExpectedDockerSha256 $ExpectedDockerSha256
    $releaseDigest = [string]$imageProof.digest
    Assert-ReleaseGuardEvidence -Value $guard -ExpectedRevision $Revision `
        -ExpectedImage $image -ExpectedImageId ([string]$imageEvidence.id) `
        -Root $productionRootFull
    $buildCommandTimes = Assert-BuildCommandEvidence -Value $buildCommand `
        -ExpectedRevision $Revision `
        -ExpectedImage $image -ExpectedImageId ([string]$imageEvidence.id) `
        -ExpectedDigest $releaseDigest -ExpectedWorktree $buildWorktreeFull `
        -DockerSha256 $ExpectedDockerSha256 `
        -ExpectedDockerPath $imageProof.docker_executable_path
    $buildHistoryTimes = Assert-BuildHistoryEvidence -Value $buildHistory `
        -ExpectedRevision $Revision `
        -ExpectedImage $image -ExpectedImageId ([string]$imageEvidence.id) `
        -ExpectedDigest $releaseDigest -ExpectedWorktree $buildWorktreeFull `
        -ExpectedDockerSha256 $ExpectedDockerSha256 `
        -ExpectedDockerPath $imageProof.docker_executable_path
    $topologyCapturedAt = Assert-TopologyEvidence -Value $topology `
        -ExpectedDockerSha256 $ExpectedDockerSha256 `
        -ExpectedDockerPath $imageProof.docker_executable_path
    $now = [DateTimeOffset]::UtcNow
    if (
        $buildCommandTimes.started_at -ne $buildHistoryTimes.started_at -or
        $buildCommandTimes.completed_at -ne $buildHistoryTimes.completed_at -or
        $imageProof.captured_at -lt $buildCommandTimes.completed_at -or
        $topologyCapturedAt -lt $imageProof.captured_at -or
        $topologyCapturedAt -lt $ciObservedAt -or
        $topologyCapturedAt -lt $now.AddMinutes(-5) -or
        $topologyCapturedAt -gt $now.AddSeconds(30)
    ) {
        throw 'R0/R1 build, image, CI, and fresh stopped-topology chronology is invalid.'
    }

    $gitSource = Get-GitAndSourceEvidence -Root $productionRootFull `
        -Worktree $buildWorktreeFull -ExpectedRevision $Revision `
        -ExpectedRemote $Remote -ExpectedBranch $Branch `
        -RemoteTimeoutMilliseconds $LiveRemoteTimeoutMilliseconds
    $historical = Get-HistoricalEvidence -Root $productionRootFull

    $recordedAt = [DateTimeOffset]::UtcNow
    $timestamp = $recordedAt.ToString('yyyyMMddTHHmmssZ')
    $attemptId = [guid]::NewGuid().ToString('N')
    $recordId = "r1-$($Revision.Substring(0, 12))-$timestamp-$attemptId"
    $successorAttemptName =
        "section3-$($Revision.Substring(0, 12))-$timestamp-$attemptId.in-progress"
    $successorAttemptDirectory = Join-Path $cutoversRoot $successorAttemptName
    $successorEvidenceDirectory = Join-Path $successorAttemptDirectory 'successor'
    $stagingDirectory = Join-Path $outputRoot "$recordId.in-progress"
    $acceptedDirectory = Join-Path $outputRoot "r1-$Revision.accepted"

    foreach ($path in @(
        $successorAttemptDirectory, $successorEvidenceDirectory,
        $stagingDirectory, $acceptedDirectory
    )) {
        if (Test-Path -LiteralPath $path) {
            throw "A create-new R1/successor path already exists: $path"
        }
    }
    New-ExclusiveDirectory -LiteralPath $successorAttemptDirectory `
        -Label 'fresh successor attempt directory'
    New-ExclusiveDirectory -LiteralPath $successorEvidenceDirectory `
        -Label 'fresh successor evidence directory'
    New-ExclusiveDirectory -LiteralPath $stagingDirectory `
        -Label 'R1 seal staging directory'

    $historicalBytes = ConvertTo-JsonBytes -Value $historical
    $gitBytes = ConvertTo-JsonBytes -Value $gitSource.git
    $sourceSummaryBytes = ConvertTo-JsonBytes -Value $gitSource.source_summary
    $successorReservation = [ordered]@{
        format = 'stratus-r1-successor-reservation-v3'
        record_id = $recordId
        reserved_at_utc = $recordedAt.UtcDateTime.ToString('o')
        revision = $Revision
        attempt_id = $attemptId
        attempt_directory = $successorAttemptDirectory
        evidence_directory = $successorEvidenceDirectory
        separate_from_historical_lineage = $true
        empty_at_acceptance = $true
        regular_directory = $true
        reparse_point = $false
        create_new_only = $true
        reuse_after_failure = $false
        section3_started = $false
    }
    $successorReservationBytes = ConvertTo-JsonBytes -Value $successorReservation

    $artifacts = [ordered]@{}
    foreach ($name in $script:RequiredInputFiles) {
        $artifacts[$name] = [byte[]]$input.files[$name]
    }
    $artifacts['input-manifest.tsv'] = [byte[]]$input.manifest_bytes
    $artifacts['git-evidence.json'] = $gitBytes
    $artifacts['source-manifest.tsv'] = [byte[]]$gitSource.source_manifest_bytes
    $artifacts['source-summary.json'] = $sourceSummaryBytes
    $artifacts['historical-0047-inputs.json'] = $historicalBytes
    $artifacts['successor-reservation.json'] = $successorReservationBytes

    foreach ($entry in $artifacts.GetEnumerator()) {
        Write-CreateNewBytes -LiteralPath (
            Join-Path $stagingDirectory ([string]$entry.Key)
        ) -Bytes ([byte[]]$entry.Value)
    }

    $acceptance = [ordered]@{
        format = 'stratus-r0-r1-acceptance-v3'
        record_id = $recordId
        recorded_at_utc = $recordedAt.UtcDateTime.ToString('o')
        production_root = $productionRootFull
        candidate = [ordered]@{
            revision = $Revision
            tree = $gitSource.tree
            source_path_count = $gitSource.source_path_count
            source_aggregate_sha256 = $gitSource.source_aggregate_sha256
            build_worktree = $buildWorktreeFull
            detached = $true
            clean = $true
            image = $image
            image_id = [string]$imageEvidence.id
            release_digest = $releaseDigest
            platform = 'linux/amd64'
        }
        remote_ci = [ordered]@{
            remote = $Remote
            branch = $Branch
            remote_revision = $Revision
            ahead = 0
            behind = 0
            overall = 'success'
            evidence_sha256 = Get-Sha256Bytes -Bytes $input.files['ci-evidence.json']
        }
        historical_0047 = [ordered]@{
            role = 'read_only_historical_input_only'
            evidence_sha256 = Get-Sha256Bytes -Bytes $historicalBytes
            migration_start_permitted = $false
            historical_attempt_authorizes_new_start = $false
        }
        successor_attempt = [ordered]@{
            directory = $successorAttemptDirectory
            evidence_directory = $successorEvidenceDirectory
            reservation_sha256 = Get-Sha256Bytes -Bytes $successorReservationBytes
            separate_from_historical_lineage = $true
            empty_at_acceptance = $true
            create_new_only = $true
            reuse_after_failure = $false
        }
        pre_start = [ordered]@{
            topology_sha256 = Get-Sha256Bytes -Bytes $input.files['pre-start-topology.json']
            docker_observation_complete = $true
            production_postgres_running = $false
            production_api_running = $false
            production_worker_running = $false
            safety_control_running = $false
            active_operator_or_migration_count = 0
            live_container_count = 0
        }
        decisions = [ordered]@{
            R0 = 'ACCEPT'
            R1 = 'ACCEPT'
            immutable_image_load_gate_transition = 'RECORDED'
            section3_authorized = $false
            requires_parent_stage_watchdog_acceptance = $true
            section3_started = $false
        }
        authorization = [ordered]@{
            scope = 'PAPER_ONLY'
            retained_postgres_start_permitted_only_inside_single_process_transition = $false
            migration_start_permitted = $false
            api_start_permitted_before_matching_successful_terminal_receipt = $false
            worker_start_permitted = $false
            safety_control_start_permitted = $false
            live_transition_permitted = $false
            binance_kill_switch_removal_permitted = $false
        }
        artifacts = @($artifacts.Keys)
    }
    $acceptanceBytes = Write-CreateNewJson -LiteralPath (
        Join-Path $stagingDirectory 'acceptance.json'
    ) -Value $acceptance

    $manifestArtifacts = [ordered]@{}
    foreach ($entry in $artifacts.GetEnumerator()) {
        $manifestArtifacts[[string]$entry.Key] = [byte[]]$entry.Value
    }
    $manifestArtifacts['acceptance.json'] = $acceptanceBytes
    $manifestNames = @($manifestArtifacts.Keys)
    [Array]::Sort($manifestNames, [StringComparer]::Ordinal)
    $manifestBuilder = [Text.StringBuilder]::new()
    foreach ($name in $manifestNames) {
        $bytes = [byte[]]$manifestArtifacts[$name]
        [void]$manifestBuilder.Append($name)
        [void]$manifestBuilder.Append("`t")
        [void]$manifestBuilder.Append(
            $bytes.LongLength.ToString([Globalization.CultureInfo]::InvariantCulture)
        )
        [void]$manifestBuilder.Append("`t")
        [void]$manifestBuilder.Append((Get-Sha256Bytes -Bytes $bytes))
        [void]$manifestBuilder.Append("`n")
    }
    $evidenceManifestBytes = $script:Utf8NoBomStrict.GetBytes(
        $manifestBuilder.ToString()
    )
    Write-CreateNewBytes -LiteralPath (
        Join-Path $stagingDirectory 'evidence-manifest.tsv'
    ) -Bytes $evidenceManifestBytes

    $seal = [ordered]@{
        format = 'stratus-r0-r1-seal-v3'
        record_id = $recordId
        sealed_at_utc = [DateTimeOffset]::UtcNow.UtcDateTime.ToString('o')
        revision = $Revision
        acceptance_sha256 = Get-Sha256Bytes -Bytes $acceptanceBytes
        evidence_manifest_sha256 = Get-Sha256Bytes -Bytes $evidenceManifestBytes
        input_manifest_sha256 = $input.manifest_sha256
        artifact_count = $manifestNames.Count
        state = 'ACCEPTED_R1_PREREQUISITE_PARENT_STAGE_PENDING'
        section3_authorized = $false
        requires_parent_stage_watchdog_acceptance = $true
    }
    $sealBytes = Write-CreateNewJson -LiteralPath (
        Join-Path $stagingDirectory 'SEALED.json'
    ) -Value $seal

    $expectedPublishedNames = @($manifestNames + 'evidence-manifest.tsv' + 'SEALED.json')
    [Array]::Sort($expectedPublishedNames, [StringComparer]::Ordinal)
    $actualStagingNames = @(
        Get-ChildItem -LiteralPath $stagingDirectory -Force |
            ForEach-Object { $_.Name }
    )
    [Array]::Sort($actualStagingNames, [StringComparer]::Ordinal)
    if (($actualStagingNames -join "`n") -cne ($expectedPublishedNames -join "`n")) {
        throw 'R1 staging publication contains an unexpected or missing artifact.'
    }
    foreach ($name in $manifestNames) {
        $path = Join-Path $stagingDirectory $name
        if ((Get-RegularFileSha256 -LiteralPath $path `
            -Label "staged R1 artifact $name") -cne
            (Get-Sha256Bytes -Bytes ([byte[]]$manifestArtifacts[$name]))) {
            throw "Staged R1 artifact changed before publication: $name"
        }
    }
    if (
        (Get-RegularFileSha256 -LiteralPath (
            Join-Path $stagingDirectory 'evidence-manifest.tsv'
        ) -Label 'staged R1 evidence manifest') -cne
            (Get-Sha256Bytes -Bytes $evidenceManifestBytes) -or
        (Get-RegularFileSha256 -LiteralPath (
            Join-Path $stagingDirectory 'SEALED.json'
        ) -Label 'staged R1 seal') -cne (Get-Sha256Bytes -Bytes $sealBytes)
    ) {
        throw 'R1 manifest or seal changed before atomic publication.'
    }
    if ($PrePublicationDelayMilliseconds -gt 0) {
        [Threading.Thread]::Sleep($PrePublicationDelayMilliseconds)
    }
    $finalGitSource = Get-GitAndSourceEvidence -Root $productionRootFull `
        -Worktree $buildWorktreeFull -ExpectedRevision $Revision `
        -ExpectedRemote $Remote -ExpectedBranch $Branch `
        -RemoteTimeoutMilliseconds $LiveRemoteTimeoutMilliseconds
    if (
        $finalGitSource.tree -cne $gitSource.tree -or
        $finalGitSource.source_path_count -ne $gitSource.source_path_count -or
        $finalGitSource.source_aggregate_sha256 -cne
            $gitSource.source_aggregate_sha256 -or
        (Get-Sha256Bytes -Bytes $finalGitSource.source_manifest_bytes) -cne
            (Get-Sha256Bytes -Bytes $gitSource.source_manifest_bytes)
    ) {
        throw 'Source or Git identity changed before the R1 publication barrier.'
    }
    $finalHistorical = Get-HistoricalEvidence -Root $productionRootFull
    if (
        (Get-Sha256Bytes -Bytes (ConvertTo-JsonBytes -Value $finalHistorical)) -cne
            (Get-Sha256Bytes -Bytes $historicalBytes)
    ) {
        throw 'Historical already-0047 inputs changed before the R1 publication barrier.'
    }
    $finalDockerPath = Get-FullPath -LiteralPath (
        [string]$buildCommand.docker_executable_path
    )
    Assert-NoReparseAbsolutePath -LiteralPath $finalDockerPath `
        -Label 'build Docker executable'
    if ((Get-RegularFileSha256 -LiteralPath $finalDockerPath `
        -Label 'build Docker executable') -cne $ExpectedDockerSha256) {
        throw 'Build Docker executable changed before the R1 publication barrier.'
    }
    if (@(Get-ChildItem -LiteralPath $successorEvidenceDirectory -Force).Count -ne 0) {
        throw 'Successor reservation changed before publication.'
    }
    if (Test-Path -LiteralPath $acceptedDirectory) {
        throw 'The accepted R1 publication path already exists.'
    }
    $result = [ordered]@{
        format = 'stratus-r1-seal-result-v3'
        record_id = $recordId
        accepted_directory = $acceptedDirectory
        revision = $Revision
        image = $image
        image_id = [string]$imageEvidence.id
        release_digest = $releaseDigest
        successor_attempt_directory = $successorAttemptDirectory
        successor_evidence_directory = $successorEvidenceDirectory
        acceptance_sha256 = Get-Sha256Bytes -Bytes $acceptanceBytes
        evidence_manifest_sha256 = Get-Sha256Bytes -Bytes $evidenceManifestBytes
        sealed_sha256 = Get-Sha256Bytes -Bytes $sealBytes
        state = 'ACCEPTED_R1_PREREQUISITE_PARENT_STAGE_PENDING'
        section3_authorized = $false
        requires_parent_stage_watchdog_acceptance = $true
        section3_started = $false
        migration_start_permitted = $false
        live_transition_permitted = $false
        binance_kill_switch_removal_permitted = $false
    }
    $resultJson = $result | ConvertTo-Json -Depth 10 -Compress

    $script:GitExecutableHandle.Position = 0
    $finalHeldGitHasher = [Security.Cryptography.SHA256]::Create()
    try {
        $finalHeldGitSha256 = [BitConverter]::ToString(
            $finalHeldGitHasher.ComputeHash($script:GitExecutableHandle)
        ).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $finalHeldGitHasher.Dispose()
    }
    if ($finalHeldGitSha256 -cne $ExpectedGitSha256) {
        throw 'Held Git executable changed before the R1 publication barrier.'
    }
    $script:GitExecutableHandle.Dispose()
    $script:GitExecutableHandle = $null

    $lockStream.Dispose()
    $lockStream = $null
    Assert-RegularFile -LiteralPath $lockPath -Label 'R1 seal lock'
    $currentLockBytes = [IO.File]::ReadAllBytes($lockPath)
    if (
        $currentLockBytes.Length -ne $lockBytes.Length -or
        (Get-Sha256Bytes -Bytes $currentLockBytes) -cne
            (Get-Sha256Bytes -Bytes $lockBytes)
    ) {
        throw 'R1 seal lock ownership changed; lock was preserved.'
    }
    Remove-Item -LiteralPath $lockPath -Force

    # Point of no return: the prepared result text and every resource cleanup are
    # complete before this single same-volume atomic directory rename. No
    # throwable validation or cleanup operation follows a successful return.
    if ([DateTimeOffset]::UtcNow -gt $topologyCapturedAt.AddMinutes(5)) {
        throw 'Stopped-topology evidence expired at the final publication barrier.'
    }
    [IO.Directory]::Move($stagingDirectory, $acceptedDirectory)
    $publicationCommitted = $true
}
catch {
    $primaryError = $_
}

try {
    if ($null -ne $script:GitExecutableHandle) {
        $script:GitExecutableHandle.Dispose()
        $script:GitExecutableHandle = $null
    }
    if ($null -ne $lockStream) {
        $lockStream.Dispose()
        $lockStream = $null
        Assert-RegularFile -LiteralPath $lockPath -Label 'R1 seal lock'
        $currentLockBytes = [IO.File]::ReadAllBytes($lockPath)
        if (
            $currentLockBytes.Length -ne $lockBytes.Length -or
            (Get-Sha256Bytes -Bytes $currentLockBytes) -cne
                (Get-Sha256Bytes -Bytes $lockBytes)
        ) {
            throw 'R1 seal lock ownership changed; lock was preserved.'
        }
        Remove-Item -LiteralPath $lockPath -Force
    }
}
catch {
    $cleanupError = $_
}

if ($null -ne $primaryError -and $null -ne $cleanupError) {
    throw (
        "R1 seal failed: $($primaryError.Exception.Message) " +
        "Owned-resource cleanup also failed: $($cleanupError.Exception.Message)"
    )
}
if ($null -ne $primaryError) {
    throw $primaryError
}
if ($null -ne $cleanupError) {
    throw $cleanupError
}

if (-not $publicationCommitted -or [string]::IsNullOrWhiteSpace($resultJson)) {
    throw 'R1 acceptance was not atomically published.'
}
Write-Output $resultJson
