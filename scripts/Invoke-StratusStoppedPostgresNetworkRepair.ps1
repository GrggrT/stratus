[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $Root,

    [Parameter(Mandatory = $true)]
    [string] $EvidencePath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# These identities are deliberately source-pinned. Discovering replacement
# objects at repair time is not accepted as production identity evidence.
$productionPostgresId =
    '6ce82603e44abb95be94dbe95830af2d00c0c36ca3fe8a122b798087ecc1b299'
$productionPostgresName = 'weatherpoly-postgres-1'
$postgresImage = (
    'postgres:16-alpine@sha256:' +
    '20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'
)
$postgresImageId =
    'sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'
$postgresConfigHash =
    'bb02ff39b208731cad693da86ac0f83e76aab0b548eca9a0ae310d35ed2576c1'
$postgresCommandSha256 =
    '3e1396d8c083a0274909460787db163a1f9070eeabb659c403c5c7ca890436b3'
$hbaSha256 =
    '164276f8a7337705c3933e3201cb53a5a10e354ec7627768c4ac8b3e951380a4'
$defaultNetworkName = 'weatherpoly_default'
$defaultNetworkId =
    '77551e8d1119201f778fdb90e151ec26f1d9cc23832ba7b88c4db6203e3d8ba2'
$safetyNetworkName = 'weatherpoly_safety-plane'
$safetyNetworkId =
    '0d1ad122718694dbc5e6714f69a601a7d429e23a0b74eecdb10ace944e5c8620'
$approvedDockerExecutableSha256 =
    '388f231898cff8674a2aa341e306b77fd1f93a5954dd33ff556575a69227824d'
$approvedDockerContext = 'desktop-linux'
$approvedDockerEndpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
$approvedDockerEngineId = 'd98e03f5-06d7-4af4-9f21-b6f7a70ebf68'
$approvedDockerServerVersion = '28.3.2'
$approvedDockerEngineName = 'docker-desktop'
$approvedDockerRootDir = '/var/lib/docker'
$productionNetworkOptionsSha256 =
    'e0d52002c06510e7c6f227700d07f412845fc15686f0723a262d8c3e1459f569'
$script:dockerExecutable = $null
$script:dockerExecutableSha256 = $null
$script:dockerExecutableLock = $null
$script:dockerCollectorType = $null
$dockerReadTimeoutMilliseconds = 10000
$dockerMutationTimeoutMilliseconds = 20000
$dockerEventStopTimeoutMilliseconds = 5000
$dockerEventBarrierTimeoutSeconds = 5
$dockerEventJournalMaximumBytes = 65536
$dockerEventJournalMaximumEvents = 64
$dockerEventLineMaximumBytes = 16384
$dockerEventFormat = (
    '{"type":{{json .Type}},"action":{{json .Action}},' +
    '"actor_id":{{json .Actor.ID}},"attributes":{{json .Actor.Attributes}},' +
    '"scope":{{json .Scope}},"time":{{.Time}},' +
    '"time_nano":{{.TimeNano}}}'
)

$networkContract = [ordered]@{
    $defaultNetworkName = [pscustomobject]@{
        id = $defaultNetworkId
        compose_name = 'default'
        subnet = '172.20.0.0/16'
        gateway = '172.20.0.1'
        created = '2026-07-05T17:30:21.808081932Z'
        labels_sha256 = '4a06f3121da4b275c077a18cc2b17a4c12258754e48c3ac2d8de10be0b96ad0d'
    }
    $safetyNetworkName = [pscustomobject]@{
        id = $safetyNetworkId
        compose_name = 'safety-plane'
        subnet = '172.22.0.0/16'
        gateway = '172.22.0.1'
        created = '2026-07-30T09:19:55.325597748Z'
        labels_sha256 = '44ff0d23c63cad8c3e898bdb1378e958ddcedbcd202055903cd0c7949de103e7'
    }
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

function Get-FileSha256 {
    param([Parameter(Mandatory = $true)][string] $LiteralPath)

    return (Get-FileHash -LiteralPath $LiteralPath -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-RegularFile {
    param([Parameter(Mandatory = $true)][string] $LiteralPath)

    $item = Get-Item -LiteralPath $LiteralPath -Force -ErrorAction Stop
    if (
        $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        $item.Length -le 0
    ) {
        throw "Required path is not a non-empty regular file: $LiteralPath"
    }
    return $item
}

function Assert-RegularDirectory {
    param([Parameter(Mandatory = $true)][string] $LiteralPath)

    $item = Get-Item -LiteralPath $LiteralPath -Force -ErrorAction Stop
    if (
        -not $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
    ) {
        throw "Required path is not a regular directory: $LiteralPath"
    }
    return $item
}

function Get-OptionalPropertyValue {
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

function Assert-NativeJsonObject {
    param(
        [AllowNull()][object] $Value,
        [Parameter(Mandatory = $true)][string] $Label
    )

    if ($null -eq $Value -or $Value -isnot [pscustomobject]) {
        throw "$Label must be a native JSON object."
    }
}

function Assert-NativeJsonString {
    param(
        [AllowNull()][object] $Value,
        [Parameter(Mandatory = $true)][string] $Label
    )

    if ($Value -isnot [string]) {
        throw "$Label must be a native JSON string."
    }
}

function Assert-NativeJsonBoolean {
    param(
        [AllowNull()][object] $Value,
        [Parameter(Mandatory = $true)][string] $Label
    )

    if ($Value -isnot [bool]) {
        throw "$Label must be a native JSON boolean."
    }
}

function Assert-NativeJsonInteger {
    param(
        [AllowNull()][object] $Value,
        [Parameter(Mandatory = $true)][string] $Label
    )

    if ($Value -isnot [int] -and $Value -isnot [long]) {
        throw "$Label must be a native JSON integer."
    }
}

function Assert-ExactProperties {
    param(
        [Parameter(Mandatory = $true)][object] $Value,
        [Parameter(Mandatory = $true)][string[]] $Expected,
        [Parameter(Mandatory = $true)][string] $Label
    )

    Assert-NativeJsonObject -Value $Value -Label $Label
    $actual = @($Value.PSObject.Properties | ForEach-Object { [string]$_.Name })
    $expectedNames = @($Expected)
    [Array]::Sort($actual, [StringComparer]::Ordinal)
    [Array]::Sort($expectedNames, [StringComparer]::Ordinal)
    if (($actual -join "`n") -cne ($expectedNames -join "`n")) {
        throw "$Label does not have the exact expected properties."
    }
}

function ConvertFrom-StrictJsonObjectText {
    param(
        [Parameter(Mandatory = $true)][string] $Json,
        [Parameter(Mandatory = $true)][string] $Label
    )

    $trimmed = $Json.Trim()
    if (-not $trimmed.StartsWith('{') -or -not $trimmed.EndsWith('}')) {
        throw "$Label must have a native JSON object at its root."
    }
    try {
        $cmdlet = Get-Command -Name 'Microsoft.PowerShell.Utility\ConvertFrom-Json' `
            -CommandType Cmdlet -ErrorAction Stop
        $parameters = @{ InputObject = $trimmed }
        if ($cmdlet.Parameters.ContainsKey('DateKind')) {
            $parameters['DateKind'] = 'String'
        }
        $value = Microsoft.PowerShell.Utility\ConvertFrom-Json @parameters
    }
    catch {
        throw "$Label returned invalid JSON."
    }
    Assert-NativeJsonObject -Value $value -Label $Label
    return $value
}

function Get-OneJsonObject {
    param(
        [Parameter(Mandatory = $true)][string[]] $Lines,
        [Parameter(Mandatory = $true)][string] $Label
    )

    $meaningful = @($Lines | Where-Object {
        -not [string]::IsNullOrWhiteSpace([string]$_)
    })
    if ($meaningful.Count -ne 1) {
        throw "$Label did not return exactly one JSON object."
    }
    return ConvertFrom-StrictJsonObjectText -Json ([string]$meaningful[0]) -Label $Label
}

function Get-OneNativeLine {
    param(
        [Parameter(Mandatory = $true)][string[]] $Lines,
        [Parameter(Mandatory = $true)][string] $Label
    )

    $meaningful = @($Lines | Where-Object {
        -not [string]::IsNullOrWhiteSpace([string]$_)
    })
    if ($meaningful.Count -ne 1) {
        throw "$Label did not return exactly one line."
    }
    return [string]$meaningful[0]
}

function ConvertTo-NativeArgumentToken {
    param([Parameter(Mandatory = $true)][string] $Value)

    if ($Value.IndexOf([char]0) -ge 0 -or $Value.Contains("`r") -or
        $Value.Contains("`n")) {
        throw 'A bounded Docker argument contains a forbidden control character.'
    }
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
        }
        else {
            if ($slashes -gt 0) {
                [void]$builder.Append(('\' * $slashes))
            }
            [void]$builder.Append($character)
        }
        $slashes = 0
    }
    if ($slashes -gt 0) {
        [void]$builder.Append(('\' * ($slashes * 2)))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Initialize-RepairDockerCollectorType {
    $sourceTemplate = @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Threading;

public sealed class __STRATUS_TYPE__ : IDisposable
{
    public const string ContractIdentity = "__STRATUS_CONTRACT__";

    public sealed class BoundedResult
    {
        public int ExitCode;
        public bool TimedOut;
        public bool OutputExceeded;
        public bool ProcessStarted;
        public bool ProcessExited;
        public bool KillIssued;
        public int ProcessId;
        public string Stdout = String.Empty;
        public string Stderr = String.Empty;
        public string PumpError = String.Empty;
    }

    private sealed class BoundedPump
    {
        private readonly Stream input;
        private readonly int maximumBytes;
        private readonly MemoryStream captured = new MemoryStream();
        public bool Exceeded;
        public Exception Error;

        public BoundedPump(Stream value, int maximum)
        {
            input = value;
            maximumBytes = maximum;
        }

        public void Run()
        {
            try
            {
                byte[] buffer = new byte[4096];
                int count;
                while ((count = input.Read(buffer, 0, buffer.Length)) > 0)
                {
                    int remaining = maximumBytes - (int)captured.Length;
                    if (remaining > 0)
                        captured.Write(buffer, 0, Math.Min(remaining, count));
                    if (count > remaining) Exceeded = true;
                }
            }
            catch (Exception candidate) { Error = candidate; }
        }

        public string Text()
        {
            return new UTF8Encoding(false, true).GetString(captured.ToArray());
        }
    }

    public static BoundedResult RunBounded(
        string executable, string arguments, int timeoutMilliseconds,
        int stdoutMaximumBytes, int stderrMaximumBytes)
    {
        BoundedResult result = new BoundedResult();
        ProcessStartInfo info = new ProcessStartInfo();
        info.FileName = executable;
        info.Arguments = arguments;
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        info.RedirectStandardOutput = true;
        info.RedirectStandardError = true;
        using (Process child = new Process())
        {
            child.StartInfo = info;
            if (!child.Start())
                throw new InvalidOperationException("bounded Docker process did not start");
            result.ProcessStarted = true;
            result.ProcessId = child.Id;
            BoundedPump stdout = new BoundedPump(
                child.StandardOutput.BaseStream, stdoutMaximumBytes);
            BoundedPump stderr = new BoundedPump(
                child.StandardError.BaseStream, stderrMaximumBytes);
            Thread stdoutThread = new Thread(stdout.Run);
            Thread stderrThread = new Thread(stderr.Run);
            stdoutThread.IsBackground = true;
            stderrThread.IsBackground = true;
            stdoutThread.Start();
            stderrThread.Start();
            if (!child.WaitForExit(timeoutMilliseconds))
            {
                result.TimedOut = true;
                result.KillIssued = true;
                try { child.Kill(); }
                catch (Exception candidate)
                {
                    result.PumpError = "bounded Docker kill failed: " + candidate.Message;
                }
                try
                {
                    if (!child.WaitForExit(5000))
                    {
                        string candidate = "bounded Docker child remained live after kill";
                        result.PumpError = String.IsNullOrEmpty(result.PumpError) ?
                            candidate : result.PumpError + " | " + candidate;
                    }
                }
                catch (Exception candidate)
                {
                    string detail = "bounded Docker exit wait failed: " + candidate.Message;
                    result.PumpError = String.IsNullOrEmpty(result.PumpError) ?
                        detail : result.PumpError + " | " + detail;
                }
            }
            bool stdoutStopped = stdoutThread.Join(5000);
            bool stderrStopped = stderrThread.Join(5000);
            if (!stdoutStopped || !stderrStopped)
            {
                result.TimedOut = true;
                string detail = "bounded Docker output pump did not stop";
                result.PumpError = String.IsNullOrEmpty(result.PumpError) ?
                    detail : result.PumpError + " | " + detail;
            }
            result.OutputExceeded = stdout.Exceeded || stderr.Exceeded;
            if (stdout.Error != null)
            {
                string detail = "stdout: " + stdout.Error.Message;
                result.PumpError = String.IsNullOrEmpty(result.PumpError) ?
                    detail : result.PumpError + " | " + detail;
            }
            if (stderr.Error != null)
            {
                string detail = "stderr: " + stderr.Error.Message;
                result.PumpError = String.IsNullOrEmpty(result.PumpError) ?
                    detail : result.PumpError + " | " + detail;
            }
            result.Stdout = stdout.Text();
            result.Stderr = stderr.Text();
            result.ProcessExited = child.HasExited;
            result.ExitCode = result.ProcessExited ? child.ExitCode : -1;
            if (!result.ProcessExited)
            {
                string detail = "bounded Docker process did not exit after termination";
                result.PumpError = String.IsNullOrEmpty(result.PumpError) ?
                    detail : result.PumpError + " | " + detail;
            }
        }
        return result;
    }

    private Process process;
    private FileStream stdoutFile;
    private FileStream stderrFile;
    private Thread stdoutThread;
    private Thread stderrThread;
    private int processStarted;
    private int stdoutEof;
    private int stderrEof;
    private int stopping;
    private int stdoutExceeded;
    private int stderrExceeded;
    private Exception stdoutError;
    private Exception stderrError;
    private long stdoutBytes;
    private long stderrBytes;
    private string stdoutSha256 = String.Empty;
    private SHA256 stdoutHash = SHA256.Create();

    public Process Process { get { return process; } }
    public bool StdoutEof { get { return Volatile.Read(ref stdoutEof) != 0; } }
    public bool StderrEof { get { return Volatile.Read(ref stderrEof) != 0; } }
    public bool OutputExceeded
    {
        get
        {
            return Volatile.Read(ref stdoutExceeded) != 0 ||
                Volatile.Read(ref stderrExceeded) != 0;
        }
    }
    public long StdoutBytes { get { return Interlocked.Read(ref stdoutBytes); } }
    public long StderrBytes { get { return Interlocked.Read(ref stderrBytes); } }
    public string StdoutSha256 { get { return Volatile.Read(ref stdoutSha256); } }
    public string PumpError
    {
        get
        {
            Exception stdout = Volatile.Read(ref stdoutError);
            Exception stderr = Volatile.Read(ref stderrError);
            if (stdout != null) return "stdout: " + stdout.Message;
            if (stderr != null) return "stderr: " + stderr.Message;
            if (OutputExceeded) return "Docker event output exceeded its bound";
            return String.Empty;
        }
    }

    public void Start(
        string executable, string arguments, string stdoutPath, string stderrPath,
        int stdoutMaximumBytes, int stderrMaximumBytes)
    {
        if (process != null) throw new InvalidOperationException("collector already started");
        if (stdoutMaximumBytes < 1 || stderrMaximumBytes < 1)
            throw new ArgumentOutOfRangeException("event output bound");
        stdoutFile = new FileStream(
            stdoutPath, FileMode.CreateNew, FileAccess.Write, FileShare.Read,
            4096, FileOptions.WriteThrough);
        stderrFile = new FileStream(
            stderrPath, FileMode.CreateNew, FileAccess.Write, FileShare.Read,
            4096, FileOptions.WriteThrough);
        ProcessStartInfo info = new ProcessStartInfo();
        info.FileName = executable;
        info.Arguments = arguments;
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        info.RedirectStandardOutput = true;
        info.RedirectStandardError = true;
        process = new Process();
        process.StartInfo = info;
        if (!process.Start())
            throw new InvalidOperationException("Docker event witness did not start");
        Volatile.Write(ref processStarted, 1);
        stdoutThread = new Thread(delegate() {
            Pump(process.StandardOutput.BaseStream, stdoutFile, true, stdoutMaximumBytes);
        });
        stderrThread = new Thread(delegate() {
            Pump(process.StandardError.BaseStream, stderrFile, false, stderrMaximumBytes);
        });
        stdoutThread.IsBackground = true;
        stderrThread.IsBackground = true;
        stdoutThread.Start();
        stderrThread.Start();
    }

    private void Pump(Stream input, FileStream target, bool isStdout, int maximumBytes)
    {
        long captured = 0;
        try
        {
            byte[] buffer = new byte[4096];
            int count;
            while ((count = input.Read(buffer, 0, buffer.Length)) > 0)
            {
                int accepted = 0;
                if (captured < maximumBytes)
                {
                    accepted = (int)Math.Min((long)count, maximumBytes - captured);
                }
                if (accepted != count)
                {
                    if (isStdout) Volatile.Write(ref stdoutExceeded, 1);
                    else Volatile.Write(ref stderrExceeded, 1);
                }
                if (accepted > 0)
                {
                    if (isStdout) Interlocked.Add(ref stdoutBytes, accepted);
                    else Interlocked.Add(ref stderrBytes, accepted);
                    target.Write(buffer, 0, accepted);
                    target.Flush(true);
                    captured += accepted;
                    if (isStdout)
                    {
                        stdoutHash.TransformBlock(buffer, 0, accepted, buffer, 0);
                    }
                }
            }
        }
        catch (Exception candidate)
        {
            if (isStdout)
                Interlocked.CompareExchange(ref stdoutError, candidate, null);
            else Interlocked.CompareExchange(ref stderrError, candidate, null);
        }
        finally
        {
            try { target.Flush(true); }
            catch (Exception candidate)
            {
                if (isStdout)
                    Interlocked.CompareExchange(ref stdoutError, candidate, null);
                else Interlocked.CompareExchange(ref stderrError, candidate, null);
            }
            if (isStdout)
            {
                try
                {
                    stdoutHash.TransformFinalBlock(new byte[0], 0, 0);
                    Volatile.Write(
                        ref stdoutSha256,
                        BitConverter.ToString(stdoutHash.Hash)
                            .Replace("-", String.Empty).ToLowerInvariant()
                    );
                }
                catch (Exception candidate)
                {
                    Interlocked.CompareExchange(ref stdoutError, candidate, null);
                }
                Volatile.Write(ref stdoutEof, 1);
            }
            else Volatile.Write(ref stderrEof, 1);
        }
    }

    public void StopAndWait(int milliseconds)
    {
        Volatile.Write(ref stopping, 1);
        List<string> failures = new List<string>();
        Exception first = null;
        try
        {
            if (process != null && Volatile.Read(ref processStarted) != 0 &&
                !process.HasExited)
            {
                process.Kill();
                if (!process.WaitForExit(milliseconds))
                    throw new TimeoutException("Docker event witness did not stop");
            }
        }
        catch (Exception candidate)
        {
            first = candidate;
            failures.Add("process stop: " + candidate.Message);
        }
        try
        {
            if (stdoutThread != null && !stdoutThread.Join(milliseconds))
                throw new TimeoutException("Docker event stdout pump did not stop");
        }
        catch (Exception candidate)
        {
            if (first == null) first = candidate;
            failures.Add("stdout join: " + candidate.Message);
        }
        try
        {
            if (stderrThread != null && !stderrThread.Join(milliseconds))
                throw new TimeoutException("Docker event stderr pump did not stop");
        }
        catch (Exception candidate)
        {
            if (first == null) first = candidate;
            failures.Add("stderr join: " + candidate.Message);
        }
        try { if (stdoutFile != null) stdoutFile.Flush(true); }
        catch (Exception candidate)
        {
            if (first == null) first = candidate;
            failures.Add("stdout flush: " + candidate.Message);
        }
        try { if (stderrFile != null) stderrFile.Flush(true); }
        catch (Exception candidate)
        {
            if (first == null) first = candidate;
            failures.Add("stderr flush: " + candidate.Message);
        }
        if (first != null)
            throw new InvalidOperationException(
                "Docker event collector stop failed: " +
                String.Join(" | ", failures.ToArray()), first);
    }

    public void CloseOutputFiles()
    {
        if ((stdoutThread != null && stdoutThread.IsAlive) ||
            (stderrThread != null && stderrThread.IsAlive))
            throw new InvalidOperationException("event output pumps are still running");
        List<string> failures = new List<string>();
        Exception first = null;
        try
        {
            if (stdoutFile != null) { stdoutFile.Dispose(); stdoutFile = null; }
        }
        catch (Exception candidate)
        {
            first = candidate;
            failures.Add("stdout: " + candidate.Message);
        }
        try
        {
            if (stderrFile != null) { stderrFile.Dispose(); stderrFile = null; }
        }
        catch (Exception candidate)
        {
            if (first == null) first = candidate;
            failures.Add("stderr: " + candidate.Message);
        }
        if (first != null)
            throw new InvalidOperationException(
                "Docker event output close failed: " +
                String.Join(" | ", failures.ToArray()), first);
    }

    public void Dispose()
    {
        List<string> failures = new List<string>();
        Exception failure = null;
        try { StopAndWait(5000); }
        catch (Exception candidate)
        {
            failure = candidate;
            failures.Add("stop: " + candidate.Message);
        }
        try { CloseOutputFiles(); }
        catch (Exception candidate)
        {
            if (failure == null) failure = candidate;
            failures.Add("output files: " + candidate.Message);
        }
        try
        {
            if (process != null) { process.Dispose(); process = null; }
        }
        catch (Exception candidate)
        {
            if (failure == null) failure = candidate;
            failures.Add("process: " + candidate.Message);
        }
        try
        {
            if (stdoutHash != null) { stdoutHash.Dispose(); stdoutHash = null; }
        }
        catch (Exception candidate)
        {
            if (failure == null) failure = candidate;
            failures.Add("hash: " + candidate.Message);
        }
        if (failure != null)
            throw new InvalidOperationException(
                "Docker event collector cleanup did not complete: " +
                String.Join(" | ", failures.ToArray()), failure);
    }
}
'@
    $contractSha256 = Get-Sha256Text -Value $sourceTemplate
    $typeName = 'StratusRepairDockerCollector_' +
        $contractSha256.Substring(0, 16)
    $contractIdentity = "repair-v2:$contractSha256"
    $source = $sourceTemplate.Replace('__STRATUS_TYPE__', $typeName)
    $source = $source.Replace('__STRATUS_CONTRACT__', $contractIdentity)
    $sourceSha256 = Get-Sha256Text -Value $source
    $type = $typeName -as [type]
    if ($null -eq $type) {
        $compiled = @(Microsoft.PowerShell.Utility\Add-Type `
            -TypeDefinition $source -PassThru -ErrorAction Stop)
        $matches = @($compiled | Where-Object { $_.FullName -ceq $typeName })
        if ($matches.Count -ne 1) {
            throw 'The repair Docker collector did not compile to one exact type.'
        }
        $type = $matches[0]
    }
    $field = $type.GetField(
        'ContractIdentity',
        [Reflection.BindingFlags]::Public -bor [Reflection.BindingFlags]::Static
    )
    if ($null -eq $field -or -not $field.IsLiteral -or
        [string]$field.GetRawConstantValue() -cne $contractIdentity) {
        throw 'An existing repair Docker collector type has the wrong identity.'
    }
    return [pscustomobject]@{
        type = $type
        type_name = $typeName
        contract_identity = $contractIdentity
        template_sha256 = $contractSha256
        source_sha256 = $sourceSha256
        assembly_full_name_sha256 = Get-Sha256Text -Value (
            [string]$type.Assembly.FullName + "`n"
        )
        assembly_is_dynamic = [bool]$type.Assembly.IsDynamic
        module_version_id = [string]$type.Module.ModuleVersionId
    }
}

function Resolve-PinnedDockerApplication {
    $commands = @(Microsoft.PowerShell.Core\Get-Command -Name 'docker' -All `
        -CommandType Application -ErrorAction Stop)
    if ($commands.Count -lt 1) {
        throw 'Docker application could not be resolved.'
    }
    $matches = [Collections.Generic.List[object]]::new()
    $seenPaths = @{}
    foreach ($command in $commands) {
        $source = [string]$command.Source
        if ([string]::IsNullOrWhiteSpace($source)) {
            continue
        }
        $path = [IO.Path]::GetFullPath($source)
        if ($seenPaths.ContainsKey($path)) {
            continue
        }
        $seenPaths[$path] = $true
        $item = Assert-RegularFile -LiteralPath $path
        $sha256 = Get-FileSha256 -LiteralPath $path
        if ($sha256 -ceq $approvedDockerExecutableSha256) {
            $matches.Add([pscustomobject]@{
                path = $path
                sha256 = $sha256
                bytes = [int64]$item.Length
            })
        }
    }
    if ($matches.Count -ne 1) {
        throw 'Docker application resolution did not find exactly one approved executable hash.'
    }
    $match = $matches[0]
    $lock = [IO.FileStream]::new(
        [string]$match.path,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read,
        4096,
        [IO.FileOptions]::SequentialScan
    )
    try {
        $algorithm = [Security.Cryptography.SHA256]::Create()
        try {
            $lockedHash = [BitConverter]::ToString(
                $algorithm.ComputeHash($lock)
            ).Replace('-', '').ToLowerInvariant()
            $lock.Position = 0
        }
        finally {
            $algorithm.Dispose()
        }
        if ($lockedHash -cne $approvedDockerExecutableSha256) {
            throw 'Docker executable changed while acquiring its byte lock.'
        }
        $script:dockerExecutableLock = $lock
        return [pscustomobject]@{
            path = [string]$match.path
            sha256 = $lockedHash
            bytes = [int64]$lock.Length
        }
    }
    catch {
        $lock.Dispose()
        throw
    }
}

function Get-ByteArraySha256 {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [byte[]] $Bytes
    )

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

function Get-PinnedDockerApplicationBoundary {
    if (
        $null -eq $script:dockerExecutableLock -or
        $script:dockerExecutableLock.SafeFileHandle.IsClosed -or
        $script:dockerExecutableLock.SafeFileHandle.IsInvalid
    ) {
        throw 'The pinned Docker executable byte lock is not held.'
    }
    $script:dockerExecutableLock.Position = 0
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $sha256 = [BitConverter]::ToString(
            $algorithm.ComputeHash($script:dockerExecutableLock)
        ).Replace('-', '').ToLowerInvariant()
        $script:dockerExecutableLock.Position = 0
    }
    finally {
        $algorithm.Dispose()
    }
    if ($sha256 -cne $script:dockerExecutableSha256) {
        throw 'Docker executable changed after it was pinned.'
    }
    return [pscustomobject]@{
        path = [IO.Path]::GetFullPath($script:dockerExecutable)
        sha256 = $sha256
        bytes = [int64]$script:dockerExecutableLock.Length
    }
}

function Invoke-PinnedDocker {
    param(
        [Parameter(Mandatory = $true)][string[]] $Arguments,
        [int] $ProcessTimeoutMilliseconds = $dockerReadTimeoutMilliseconds,
        [int] $MaximumStdoutBytes = 2097152
    )

    if (
        [string]::IsNullOrWhiteSpace([string]$script:dockerExecutable) -or
        $null -eq $script:dockerExecutableLock -or
        $script:dockerExecutableLock.SafeFileHandle.IsClosed -or
        $script:dockerExecutableLock.SafeFileHandle.IsInvalid
    ) {
        throw 'Docker application was not pinned before use.'
    }
    if (
        $null -eq $script:dockerCollectorType -or
        $null -eq $script:dockerCollectorType.type
    ) {
        throw 'The bounded Docker collector was not initialized before use.'
    }
    if (
        $Arguments.Count -eq 0 -or
        $ProcessTimeoutMilliseconds -lt 1 -or
        $ProcessTimeoutMilliseconds -gt 120000 -or
        $MaximumStdoutBytes -lt 1 -or
        $MaximumStdoutBytes -gt 4194304
    ) {
        throw 'A bounded Docker invocation has invalid arguments or limits.'
    }
    $before = Get-PinnedDockerApplicationBoundary
    $effectiveArguments = @('--context', $approvedDockerContext) + @($Arguments)
    $argumentText = @($effectiveArguments | ForEach-Object {
        ConvertTo-NativeArgumentToken -Value ([string]$_)
    }) -join ' '
    $method = $script:dockerCollectorType.type.GetMethod(
        'RunBounded',
        [Reflection.BindingFlags]::Public -bor [Reflection.BindingFlags]::Static
    )
    if ($null -eq $method) {
        throw 'The bounded Docker collector entry point is unavailable.'
    }
    try {
        $result = $method.Invoke($null, @(
            [string]$before.path,
            $argumentText,
            [int]$ProcessTimeoutMilliseconds,
            [int]$MaximumStdoutBytes,
            [int]65536
        ))
    }
    catch {
        throw "A bounded Docker command could not be executed: $($_.Exception.Message)"
    }
    $after = Get-PinnedDockerApplicationBoundary
    if (
        $after.path -cne $before.path -or
        $after.sha256 -cne $before.sha256 -or
        [int64]$after.bytes -ne [int64]$before.bytes
    ) {
        throw 'Docker executable identity changed across a bounded invocation.'
    }
    if (
        -not [bool]$result.ProcessStarted -or
        -not [bool]$result.ProcessExited -or
        [bool]$result.TimedOut -or
        [bool]$result.OutputExceeded -or
        -not [string]::IsNullOrEmpty([string]$result.PumpError)
    ) {
        throw (
            'A required Docker command did not complete inside its strict process/output bounds ' +
            "(started=$([bool]$result.ProcessStarted), exited=$([bool]$result.ProcessExited), " +
            "timed_out=$([bool]$result.TimedOut), kill_issued=$([bool]$result.KillIssued), " +
            "output_exceeded=$([bool]$result.OutputExceeded), " +
            "pump_error=$([string]$result.PumpError), command=$($Arguments -join ' '))."
        )
    }
    if ([int]$result.ExitCode -ne 0) {
        throw (
            "A required Docker command failed (exit $([int]$result.ExitCode), " +
            "command=$($Arguments -join ' '))."
        )
    }
    if (-not [string]::IsNullOrEmpty([string]$result.Stderr)) {
        throw 'A successful Docker command produced unexpected stderr.'
    }
    $stdout = ([string]$result.Stdout).Replace("`r`n", "`n")
    if ($stdout.Contains("`r") -or $stdout.IndexOf([char]0) -ge 0) {
        throw 'A successful Docker command produced non-canonical output.'
    }
    if ($stdout.Length -eq 0) {
        return @()
    }
    $lines = @($stdout -split "`n")
    if ($lines.Count -gt 0 -and $lines[$lines.Count - 1] -ceq '') {
        if ($lines.Count -eq 1) {
            return @()
        }
        $lines = @($lines[0..($lines.Count - 2)])
    }
    return $lines
}

function Read-RepairDockerEventJournal {
    param(
        [Parameter(Mandatory = $true)][string] $LiteralPath,
        [switch] $AllowPartialTail,
        [switch] $Sealed
    )

    $item = Get-Item -LiteralPath $LiteralPath -Force -ErrorAction Stop
    if ($item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Docker event journal is not a regular file.'
    }
    $stream = [IO.FileStream]::new(
        $LiteralPath,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        $(if ($Sealed) { [IO.FileShare]::None } else { [IO.FileShare]::ReadWrite })
    )
    try {
        if ($stream.Length -gt $dockerEventJournalMaximumBytes) {
            throw 'Docker event journal exceeded its strict byte bound.'
        }
        $bytes = [byte[]]::new([int]$stream.Length)
        $offset = 0
        while ($offset -lt $bytes.Length) {
            $count = $stream.Read($bytes, $offset, $bytes.Length - $offset)
            if ($count -le 0) {
                throw 'Docker event journal ended during a bounded read.'
            }
            $offset += $count
        }
    }
    finally {
        $stream.Dispose()
    }
    if (
        $bytes.Length -ge 3 -and
        $bytes[0] -eq 0xef -and $bytes[1] -eq 0xbb -and $bytes[2] -eq 0xbf
    ) {
        throw 'Docker event journal must not contain a UTF-8 BOM.'
    }
    try {
        $text = [Text.UTF8Encoding]::new($false, $true).GetString($bytes)
    }
    catch {
        throw 'Docker event journal is not strict UTF-8.'
    }
    if ($text.Contains("`r") -or $text.IndexOf([char]0) -ge 0) {
        throw 'Docker event journal contains a forbidden control character.'
    }
    $hasFinalLf = $text.EndsWith("`n", [StringComparison]::Ordinal)
    if (-not $AllowPartialTail -and $text.Length -gt 0 -and -not $hasFinalLf) {
        throw 'Sealed Docker event journal has a partial final record.'
    }
    $segments = @($text.Split([char]10))
    $completeCount = if ($hasFinalLf) {
        [Math]::Max(0, $segments.Count - 1)
    }
    elseif ($AllowPartialTail) {
        [Math]::Max(0, $segments.Count - 1)
    }
    else {
        $segments.Count
    }
    if ($completeCount -gt $dockerEventJournalMaximumEvents) {
        throw 'Docker event journal exceeded its strict record bound.'
    }
    $records = [Collections.Generic.List[object]]::new()
    for ($index = 0; $index -lt $completeCount; $index += 1) {
        $line = [string]$segments[$index]
        if ([string]::IsNullOrWhiteSpace($line)) {
            throw 'Docker event journal contains an empty record.'
        }
        if ([Text.UTF8Encoding]::new($false).GetByteCount($line) -gt
            $dockerEventLineMaximumBytes) {
            throw 'Docker event journal contains an oversized record.'
        }
        $record = ConvertFrom-StrictJsonObjectText -Json $line `
            -Label "Docker event record $index"
        Assert-ExactProperties -Value $record `
            -Expected @(
                'type', 'action', 'actor_id', 'attributes', 'scope',
                'time', 'time_nano'
            ) -Label "Docker event record $index"
        foreach ($name in @('type', 'action', 'actor_id', 'scope')) {
            Assert-NativeJsonString -Value $record.$name `
                -Label "Docker event record $index $name"
        }
        Assert-NativeJsonObject -Value $record.attributes `
            -Label "Docker event record $index attributes"
        foreach ($property in @($record.attributes.PSObject.Properties)) {
            Assert-NativeJsonString -Value $property.Value `
                -Label "Docker event record $index attribute $($property.Name)"
        }
        Assert-NativeJsonInteger -Value $record.time `
            -Label "Docker event record $index time"
        Assert-NativeJsonInteger -Value $record.time_nano `
            -Label "Docker event record $index time_nano"
        if (
            [int64]$record.time -le 0 -or
            [int64]$record.time_nano -le 0 -or
            $record.scope -cne 'local'
        ) {
            throw "Docker event record $index has an invalid time or scope."
        }
        $eventSeconds = [decimal]$record.time_nano / [decimal]1000000000
        if ([int64][Math]::Floor($eventSeconds) -ne [int64]$record.time) {
            throw "Docker event record $index has inconsistent time fields."
        }
        $canonical = ConvertTo-Json -InputObject $record -Depth 8 -Compress
        if ($canonical -cne $line) {
            throw "Docker event record $index is not canonical or contains duplicate fields."
        }
        $records.Add($record)
    }
    return [pscustomobject]@{
        bytes = $bytes
        sha256 = Get-ByteArraySha256 -Bytes $bytes
        has_final_lf = $hasFinalLf
        records = @($records.ToArray())
    }
}

function Start-RepairDockerEventWitness {
    param([Parameter(Mandatory = $true)][string] $ParentDirectory)

    if ($null -eq $script:dockerCollectorType) {
        throw 'The bounded Docker collector is unavailable for event witnessing.'
    }
    $sessionId = [Guid]::NewGuid().ToString('N')
    $captureDirectory = Join-Path $ParentDirectory (
        ".stratus-repair-event-witness-$sessionId"
    )
    if (Test-Path -LiteralPath $captureDirectory) {
        throw 'The private Docker event witness directory already exists.'
    }
    $stdoutPath = Join-Path $captureDirectory 'events.jsonl'
    $stderrPath = Join-Path $captureDirectory 'events.stderr'
    $collector = $null
    $witness = $null
    try {
        [void][IO.Directory]::CreateDirectory($captureDirectory)
        [void](Assert-RegularDirectory -LiteralPath $captureDirectory)
        $collector = [Activator]::CreateInstance($script:dockerCollectorType.type)
        $docker = Get-PinnedDockerApplicationBoundary
        $eventArguments = @(
            '--context', $approvedDockerContext, 'events', '--format', $dockerEventFormat
        )
        $argumentText = @($eventArguments | ForEach-Object {
            ConvertTo-NativeArgumentToken -Value ([string]$_)
        }) -join ' '
        $collector.Start(
            [string]$docker.path,
            $argumentText,
            $stdoutPath,
            $stderrPath,
            [int]$dockerEventJournalMaximumBytes,
            [int]65536
        )
        $after = Get-PinnedDockerApplicationBoundary
        if (
            $after.path -cne $docker.path -or
            $after.sha256 -cne $docker.sha256 -or
            [int64]$after.bytes -ne [int64]$docker.bytes
        ) {
            throw 'Docker executable identity changed while starting the event witness.'
        }
        $witness = [pscustomobject]@{
            session_id = $sessionId
            barrier_name = $null
            capture_directory = $captureDirectory
            stdout_path = $stdoutPath
            stderr_path = $stderrPath
            collector = $collector
            process_id = [int]$collector.Process.Id
            process_started_at = [DateTimeOffset]::UtcNow.ToString('o')
            barrier_create_command_succeeded = $false
            barrier_create_observed = $false
            barrier_remove_command_succeeded = $false
            barrier_destroy_observed = $false
            sealed = $false
        }
    }
    catch {
        $startFailure = $_
        $cleanupFailures = [Collections.Generic.List[string]]::new()
        if ($null -ne $collector) {
            try { $collector.Dispose() }
            catch { $cleanupFailures.Add($_.Exception.GetBaseException().Message) }
        }
        try {
            Remove-RepairDockerEventCapturePaths `
                -CaptureDirectory $captureDirectory `
                -StdoutPath $stdoutPath `
                -StderrPath $stderrPath
        }
        catch { $cleanupFailures.Add($_.Exception.GetBaseException().Message) }
        if ($cleanupFailures.Count -ne 0) {
            throw (
                'Docker event witness start failed and its exact private capture cleanup ' +
                'did not complete. Start failure: ' +
                $startFailure.Exception.GetBaseException().Message +
                ' Cleanup failure: ' + ($cleanupFailures -join '; ')
            )
        }
        throw $startFailure
    }
    return $witness
}

function Assert-RepairDockerEventWitnessHealthy {
    param(
        [Parameter(Mandatory = $true)][object] $Witness,
        [Parameter(Mandatory = $true)][string] $Label
    )

    # Each collector property performs a Volatile/Interlocked read. Read once
    # into this local snapshot, then reject any state already published by a
    # pump before the caller crosses its next boundary.
    $pumpError = [string]$Witness.collector.PumpError
    $stderrBytes = [int64]$Witness.collector.StderrBytes
    $outputExceeded = [bool]$Witness.collector.OutputExceeded
    $processExited = [bool]$Witness.collector.Process.HasExited
    if (
        $processExited -or
        $outputExceeded -or
        $stderrBytes -ne 0 -or
        -not [string]::IsNullOrEmpty($pumpError)
    ) {
        throw "Docker event witness failed before $Label."
    }
}

function Wait-RepairDockerEvent {
    param(
        [Parameter(Mandatory = $true)][object] $Witness,
        [Parameter(Mandatory = $true)][string] $Type,
        [Parameter(Mandatory = $true)][string] $Action,
        [Parameter(Mandatory = $true)][string] $ActorId,
        [Parameter(Mandatory = $true)][string] $Label
    )

    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    while ($stopwatch.Elapsed.TotalSeconds -lt $dockerEventBarrierTimeoutSeconds) {
        Assert-RepairDockerEventWitnessHealthy -Witness $Witness `
            -Label "$Label was observed"
        $journal = Read-RepairDockerEventJournal `
            -LiteralPath $Witness.stdout_path -AllowPartialTail
        $matches = @($journal.records | Where-Object {
            $_.type -ceq $Type -and $_.action -ceq $Action -and
            $_.actor_id -ceq $ActorId
        })
        if ($matches.Count -gt 1) {
            throw "Docker event witness observed duplicate $Label records."
        }
        if ($matches.Count -eq 1) {
            Assert-RepairDockerEventWitnessHealthy -Witness $Witness `
                -Label "$Label was accepted"
            if ([int64]$journal.bytes.Length -ne
                [int64]$Witness.collector.StdoutBytes) {
                throw "Docker event witness changed while $Label was accepted."
            }
            return $matches[0]
        }
        [Threading.Thread]::Sleep(25)
    }
    throw "Docker event witness did not observe $Label inside its strict deadline."
}

function New-RepairDockerEventBarrier {
    param([Parameter(Mandatory = $true)][object] $Witness)

    $output = @(Invoke-PinnedDocker -Arguments @(
        'volume', 'create', '--driver', 'local'
    ) -ProcessTimeoutMilliseconds $dockerMutationTimeoutMilliseconds `
        -MaximumStdoutBytes 4096 | Where-Object {
            -not [string]::IsNullOrWhiteSpace([string]$_)
        })
    if ($output.Count -ne 1 -or [string]$output[0] -cnotmatch '^[0-9a-f]{64}$') {
        throw 'Docker event pre-barrier creation did not return one anonymous volume ID.'
    }
    $Witness.barrier_name = [string]$output[0]
    $Witness.barrier_create_command_succeeded = $true
    [void](Wait-RepairDockerEvent -Witness $Witness -Type 'volume' `
        -Action 'create' -ActorId $Witness.barrier_name -Label 'pre-barrier')
    $Witness.barrier_create_observed = $true
}

function Remove-RepairDockerEventBarrier {
    param([Parameter(Mandatory = $true)][object] $Witness)

    $output = @(Invoke-PinnedDocker -Arguments @(
        'volume', 'rm', [string]$Witness.barrier_name
    ) -ProcessTimeoutMilliseconds $dockerMutationTimeoutMilliseconds `
        -MaximumStdoutBytes 4096 | Where-Object {
            -not [string]::IsNullOrWhiteSpace([string]$_)
        })
    $Witness.barrier_remove_command_succeeded = $true
    if ($output.Count -ne 1 -or $output[0] -cne [string]$Witness.barrier_name) {
        throw 'Docker event final-barrier removal did not return its exact owned name.'
    }
    [void](Wait-RepairDockerEvent -Witness $Witness -Type 'volume' `
        -Action 'destroy' -ActorId $Witness.barrier_name -Label 'final-barrier')
    $Witness.barrier_destroy_observed = $true
}

function Assert-RepairDockerEventPreMutationBoundary {
    param([Parameter(Mandatory = $true)][object] $Witness)

    Assert-RepairDockerEventWitnessHealthy -Witness $Witness `
        -Label 'the repair mutation boundary was inspected'
    $stdoutBytesBefore = [int64]$Witness.collector.StdoutBytes
    $journal = Read-RepairDockerEventJournal `
        -LiteralPath $Witness.stdout_path -AllowPartialTail
    $records = @($journal.records)
    if (-not [bool]$journal.has_final_lf) {
        throw 'Docker event witness has a partial record before mutation.'
    }
    if ($records.Count -ne 1) {
        throw 'Docker event witness observed an unexpected event before mutation.'
    }
    $record = $records[0]
    Assert-ExactProperties -Value $record.attributes -Expected @('driver') `
        -Label 'Docker event pre-barrier attributes'
    if (
        $record.type -cne 'volume' -or
        $record.action -cne 'create' -or
        $record.actor_id -cne [string]$Witness.barrier_name -or
        $record.attributes.driver -cne 'local'
    ) {
        throw 'Docker event witness did not retain the exact pre-mutation barrier.'
    }
    $stdoutBytesAfter = [int64]$Witness.collector.StdoutBytes
    if (
        $stdoutBytesBefore -ne $stdoutBytesAfter -or
        [int64]$journal.bytes.Length -ne $stdoutBytesAfter
    ) {
        throw 'Docker event witness changed while crossing the repair mutation boundary.'
    }
    Assert-RepairDockerEventWitnessHealthy -Witness $Witness `
        -Label 'the repair mutation boundary was crossed'
}

function Stop-RepairDockerEventWitness {
    param([Parameter(Mandatory = $true)][object] $Witness)

    if (-not [bool]$Witness.barrier_destroy_observed) {
        throw 'Docker event witness cannot seal before the final barrier is observed.'
    }
    if ([bool]$Witness.collector.Process.HasExited) {
        throw 'Docker event witness exited before the final barrier was sealed.'
    }
    $Witness.collector.StopAndWait([int]$dockerEventStopTimeoutMilliseconds)
    if (
        -not [bool]$Witness.collector.Process.HasExited -or
        -not [bool]$Witness.collector.StdoutEof -or
        -not [bool]$Witness.collector.StderrEof -or
        [bool]$Witness.collector.OutputExceeded -or
        -not [string]::IsNullOrEmpty([string]$Witness.collector.PumpError)
    ) {
        throw 'Docker event witness did not seal cleanly inside its strict bounds.'
    }
    $Witness.collector.CloseOutputFiles()
    $stderrItem = Get-Item -LiteralPath $Witness.stderr_path -Force -ErrorAction Stop
    if ($stderrItem.PSIsContainer -or
        ($stderrItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Docker event stderr capture is not a regular file.'
    }
    $stderrStream = [IO.FileStream]::new(
        [string]$Witness.stderr_path,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::None
    )
    try {
        $stderrLength = [int64]$stderrStream.Length
    }
    finally {
        $stderrStream.Dispose()
    }
    if ($stderrLength -ne 0 -or [int64]$Witness.collector.StderrBytes -ne 0) {
        throw 'Docker event witness produced unexpected stderr.'
    }
    $journal = Read-RepairDockerEventJournal -LiteralPath $Witness.stdout_path -Sealed
    if (
        $journal.bytes.Length -ne [int64]$Witness.collector.StdoutBytes -or
        $journal.sha256 -cne [string]$Witness.collector.StdoutSha256
    ) {
        throw 'Sealed Docker event journal bytes do not match the collector receipt.'
    }
    $Witness.sealed = $true
    return $journal
}

function Assert-RepairDockerEventContract {
    param(
        [Parameter(Mandatory = $true)][object] $Witness,
        [Parameter(Mandatory = $true)][object] $Journal,
        [Parameter(Mandatory = $true)][bool] $MutationIssued
    )

    $expectedCount = if ($MutationIssued) { 3 } else { 2 }
    $records = @($Journal.records)
    if ($records.Count -ne $expectedCount) {
        throw 'Docker event witness observed an unexpected lifecycle event count.'
    }
    $expected = [Collections.Generic.List[object]]::new()
    $expected.Add([pscustomobject]@{
        type = 'volume'; action = 'create'; actor_id = [string]$Witness.barrier_name
        attributes = [ordered]@{ driver = 'local' }
    })
    if ($MutationIssued) {
        $expected.Add([pscustomobject]@{
            type = 'network'; action = 'connect'; actor_id = $safetyNetworkId
            attributes = [ordered]@{
                container = $productionPostgresId
                name = $safetyNetworkName
                type = 'bridge'
            }
        })
    }
    $expected.Add([pscustomobject]@{
        type = 'volume'; action = 'destroy'; actor_id = [string]$Witness.barrier_name
        attributes = [ordered]@{ driver = 'local' }
    })
    $previousTimeNano = [int64]0
    for ($index = 0; $index -lt $records.Count; $index += 1) {
        $record = $records[$index]
        $contract = $expected[$index]
        Assert-ExactProperties -Value $record.attributes `
            -Expected @($contract.attributes.Keys) `
            -Label "Docker event contract record $index attributes"
        if (
            $record.type -cne $contract.type -or
            $record.action -cne $contract.action -or
            $record.actor_id -cne $contract.actor_id -or
            [int64]$record.time_nano -le $previousTimeNano
        ) {
            throw "Docker event contract record $index is not the exact allowed event."
        }
        foreach ($name in @($contract.attributes.Keys)) {
            if ($record.attributes.$name -cne [string]$contract.attributes[$name]) {
                throw "Docker event contract record $index has an unexpected attribute."
            }
        }
        $previousTimeNano = [int64]$record.time_nano
    }
    $eventsJson = ConvertTo-Json -InputObject $records -Depth 8 -Compress
    $observedConnectCount = @($records | Where-Object {
        $_.type -ceq 'network' -and $_.action -ceq 'connect'
    }).Count
    $connectEvent = if ($MutationIssued) { $records[1] } else { $null }
    return [ordered]@{
        format = 'stratus-stopped-repair-docker-event-witness-v1'
        session_id = [string]$Witness.session_id
        process_id = [int]$Witness.process_id
        process_started_at = [string]$Witness.process_started_at
        event_command = @(
            '--context', $approvedDockerContext,
            'events', '--format', $dockerEventFormat
        )
        collector = [ordered]@{
            type_name = [string]$script:dockerCollectorType.type_name
            contract_identity = [string]$script:dockerCollectorType.contract_identity
            template_sha256 = [string]$script:dockerCollectorType.template_sha256
            source_sha256 = [string]$script:dockerCollectorType.source_sha256
            assembly_full_name_sha256 =
                [string]$script:dockerCollectorType.assembly_full_name_sha256
            assembly_is_dynamic = [bool]$script:dockerCollectorType.assembly_is_dynamic
            module_version_id = [string]$script:dockerCollectorType.module_version_id
        }
        barrier_volume_name = [string]$Witness.barrier_name
        pre_barrier = [ordered]@{
            actor_id = [string]$records[0].actor_id
            time = [int64]$records[0].time
            time_nano = [int64]$records[0].time_nano
        }
        final_barrier = [ordered]@{
            actor_id = [string]$records[$records.Count - 1].actor_id
            time = [int64]$records[$records.Count - 1].time
            time_nano = [int64]$records[$records.Count - 1].time_nano
        }
        authority_as_of_time_nano =
            [int64]$records[$records.Count - 1].time_nano
        journal_bytes = [int64]$Journal.bytes.Length
        journal_sha256 = [string]$Journal.sha256
        event_count = $records.Count
        events_sha256 = Get-Sha256Text -Value $eventsJson
        events = $records
        network_connect_count = $observedConnectCount
        network_connect_time_nano = if ($null -eq $connectEvent) {
            $null
        }
        else {
            [int64]$connectEvent.time_nano
        }
        stderr_bytes = 0
        truncated = $false
        eof_before_seal = $false
        unexpected_event_count = [Math]::Max(0, $records.Count - $expectedCount)
    }
}

function Remove-RepairDockerEventCapturePaths {
    param(
        [Parameter(Mandatory = $true)][string] $CaptureDirectory,
        [Parameter(Mandatory = $true)][string] $StdoutPath,
        [Parameter(Mandatory = $true)][string] $StderrPath
    )

    $captureFull = [IO.Path]::GetFullPath($CaptureDirectory).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    $stdoutFull = [IO.Path]::GetFullPath($StdoutPath)
    $stderrFull = [IO.Path]::GetFullPath($StderrPath)
    if (
        [IO.Path]::GetDirectoryName($stdoutFull) -ine $captureFull -or
        [IO.Path]::GetDirectoryName($stderrFull) -ine $captureFull -or
        [IO.Path]::GetFileName($stdoutFull) -cne 'events.jsonl' -or
        [IO.Path]::GetFileName($stderrFull) -cne 'events.stderr'
    ) {
        throw 'Docker event capture cleanup paths are not the exact private pair.'
    }
    foreach ($path in @($stdoutFull, $stderrFull)) {
        if ([IO.File]::Exists($path)) {
            [IO.File]::Delete($path)
        }
        if ([IO.File]::Exists($path)) {
            throw "Docker event capture file remained after cleanup: $path"
        }
    }
    if ([IO.Directory]::Exists($captureFull)) {
        [IO.Directory]::Delete($captureFull, $false)
    }
    if ([IO.Directory]::Exists($captureFull)) {
        throw 'Docker event private capture directory remained after cleanup.'
    }
}

function Remove-RepairDockerEventCapture {
    param([AllowNull()][object] $Witness)

    if ($null -eq $Witness) {
        return
    }
    Remove-RepairDockerEventCapturePaths `
        -CaptureDirectory ([string]$Witness.capture_directory) `
        -StdoutPath ([string]$Witness.stdout_path) `
        -StderrPath ([string]$Witness.stderr_path)
}

function Get-CanonicalPropertyHash {
    param(
        [AllowNull()][object] $Value,
        [Parameter(Mandatory = $true)][string] $Label
    )

    if ($null -eq $Value) {
        return Get-Sha256Text -Value "<null>`n"
    }
    Assert-NativeJsonObject -Value $Value -Label $Label
    $lines = @($Value.PSObject.Properties | ForEach-Object {
        $serialized = $_.Value | ConvertTo-Json -Depth 12 -Compress
        "$($_.Name)`0$serialized"
    })
    [Array]::Sort($lines, [StringComparer]::Ordinal)
    return Get-Sha256Text -Value (($lines -join "`n") + "`n")
}

function Assert-NoDockerEndpointOverrides {
    foreach ($name in @(
        'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH',
        'DOCKER_CONFIG', 'DOCKER_API_VERSION', 'DOCKER_CLI_PLUGIN_EXTRA_DIRS'
    )) {
        if (Test-Path -LiteralPath "Env:$name") {
            throw "Docker endpoint override is forbidden for stopped repair: $name"
        }
    }
}

function Get-DockerEngineBoundary {
    Assert-NoDockerEndpointOverrides
    $application = Get-PinnedDockerApplicationBoundary
    $context = Get-OneNativeLine -Label 'Docker context name' -Lines (
        Invoke-PinnedDocker -Arguments @('context', 'show')
    )
    $endpoint = Get-OneJsonObject -Label 'Docker context endpoint' -Lines (
        Invoke-PinnedDocker -Arguments @(
            'context', 'inspect', $context, '--format', '{{json .Endpoints.docker}}'
        )
    )
    $info = Get-OneJsonObject -Label 'Docker engine info' -Lines (
        Invoke-PinnedDocker -Arguments @('info', '--format', '{{json .}}')
    )
    Assert-NativeJsonString -Value $endpoint.Host -Label 'Docker endpoint Host'
    Assert-NativeJsonBoolean -Value $endpoint.SkipTLSVerify `
        -Label 'Docker endpoint SkipTLSVerify'
    foreach ($name in @('ID', 'ServerVersion', 'Name', 'DockerRootDir')) {
        Assert-NativeJsonString -Value $info.$name -Label "Docker engine $name"
    }
    if (
        [string]::IsNullOrWhiteSpace($context) -or
        [string]::IsNullOrWhiteSpace([string]$endpoint.Host) -or
        [string]::IsNullOrWhiteSpace([string]$info.ID) -or
        [string]::IsNullOrWhiteSpace([string]$info.ServerVersion) -or
        [string]::IsNullOrWhiteSpace([string]$info.Name) -or
        [string]::IsNullOrWhiteSpace([string]$info.DockerRootDir)
    ) {
        throw 'Docker context or engine identity is incomplete.'
    }
    $skipTlsVerify = [bool]$endpoint.SkipTLSVerify
    if (
        $context -cne $approvedDockerContext -or
        $endpoint.Host -cne $approvedDockerEndpoint -or
        $skipTlsVerify -or
        $info.ID -cne $approvedDockerEngineId -or
        $info.ServerVersion -cne $approvedDockerServerVersion -or
        $info.Name -cne $approvedDockerEngineName -or
        $info.DockerRootDir -cne $approvedDockerRootDir
    ) {
        throw 'Docker context or engine identity is not the exact approved production boundary.'
    }
    $contract = @(
        [string]$application.path,
        [string]$application.sha256,
        [string]$application.bytes,
        $context,
        [string]$endpoint.Host,
        $skipTlsVerify.ToString().ToLowerInvariant(),
        [string]$info.ID,
        [string]$info.ServerVersion,
        [string]$info.Name,
        [string]$info.DockerRootDir
    ) -join "`0"
    return [ordered]@{
        docker_executable_path = [string]$application.path
        docker_executable_sha256 = [string]$application.sha256
        docker_executable_bytes = [int64]$application.bytes
        context = $context
        endpoint = [string]$endpoint.Host
        skip_tls_verify = $skipTlsVerify
        engine_id = [string]$info.ID
        server_version = [string]$info.ServerVersion
        engine_name = [string]$info.Name
        docker_root_dir = [string]$info.DockerRootDir
        contract_sha256 = Get-Sha256Text -Value ($contract + "`n")
    }
}

function Get-DockerHostPathAliases {
    param([Parameter(Mandatory = $true)][string] $Path)

    $fullPath = [IO.Path]::GetFullPath($Path)
    $normalized = $fullPath.Replace('\', '/')
    $aliases = [Collections.Generic.List[string]]::new()
    $aliases.Add($normalized)
    if ($normalized -match '^([A-Za-z]):/(.*)$') {
        $drive = $Matches[1].ToLowerInvariant()
        $tail = $Matches[2]
        $aliases.Add("/run/desktop/mnt/host/$drive/$tail")
        $aliases.Add("/host_mnt/$drive/$tail")
    }
    return @($aliases)
}

function Test-ComposeConfigFilesLabel {
    param(
        [AllowNull()][string] $Label,
        [Parameter(Mandatory = $true)][string] $ComposePath,
        [Parameter(Mandatory = $true)][string] $OverridePath
    )

    if ([string]::IsNullOrWhiteSpace($Label)) {
        return $false
    }
    $parts = @($Label -split ',' | Where-Object {
        -not [string]::IsNullOrWhiteSpace([string]$_)
    })
    if ($parts.Count -ne 2) {
        return $false
    }
    return (
        [IO.Path]::GetFullPath([string]$parts[0]) -ieq
            [IO.Path]::GetFullPath($ComposePath) -and
        [IO.Path]::GetFullPath([string]$parts[1]) -ieq
            [IO.Path]::GetFullPath($OverridePath)
    )
}

function Get-CommandSha256 {
    param([Parameter(Mandatory = $true)][object] $Inspect)

    foreach ($name in @('Entrypoint', 'Cmd')) {
        if ($Inspect.Config.$name -isnot [Array]) {
            throw "PostgreSQL Config.$name must be a native JSON array."
        }
        foreach ($value in @($Inspect.Config.$name)) {
            Assert-NativeJsonString -Value $value `
                -Label "PostgreSQL Config.$name entry"
        }
    }
    $command = @(@($Inspect.Config.Entrypoint) + @($Inspect.Config.Cmd)) -join "`0"
    return Get-Sha256Text -Value $command
}

function Get-ContainerLifecycleInventory {
    $runningIds = @(
        Invoke-PinnedDocker -Arguments @('ps', '-q', '--no-trunc') |
            Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    if ($runningIds.Count -ne 0) {
        throw 'Stopped network repair requires zero running containers.'
    }
    $containerIds = @(
        Invoke-PinnedDocker -Arguments @('ps', '-a', '-q', '--no-trunc') |
            Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    foreach ($containerId in $containerIds) {
        if ([string]$containerId -cnotmatch '^[0-9a-f]{64}$') {
            throw 'Docker returned a malformed all-container inventory ID.'
        }
    }
    [Array]::Sort($containerIds, [StringComparer]::Ordinal)
    if (@($containerIds | Select-Object -Unique).Count -ne $containerIds.Count) {
        throw 'Docker returned duplicate all-container inventory IDs.'
    }
    $entries = [Collections.Generic.List[object]]::new()
    foreach ($containerId in $containerIds) {
        $inspect = Get-OneJsonObject -Label "container lifecycle $containerId" -Lines (
            Invoke-PinnedDocker -Arguments @(
                'inspect', [string]$containerId, '--format', '{{json .}}'
            )
        )
        foreach ($name in @('Config', 'State')) {
            Assert-NativeJsonObject -Value $inspect.$name `
                -Label "container lifecycle $containerId $name"
        }
        foreach ($name in @('Id', 'Name')) {
            Assert-NativeJsonString -Value $inspect.$name `
                -Label "container lifecycle $containerId $name"
        }
        foreach ($name in @('Status', 'StartedAt', 'FinishedAt')) {
            Assert-NativeJsonString -Value $inspect.State.$name `
                -Label "container lifecycle $containerId State.$name"
        }
        Assert-NativeJsonBoolean -Value $inspect.State.Running `
            -Label "container lifecycle $containerId Running"
        Assert-NativeJsonBoolean -Value $inspect.State.OOMKilled `
            -Label "container lifecycle $containerId OOMKilled"
        Assert-NativeJsonInteger -Value $inspect.RestartCount `
            -Label "container lifecycle $containerId RestartCount"
        if ($inspect.Id -cne [string]$containerId -or [bool]$inspect.State.Running) {
            throw 'All-container lifecycle inventory drifted or contains a running container.'
        }
        $labels = Get-OptionalPropertyValue -InputObject $inspect.Config -Name 'Labels'
        Assert-NativeJsonObject -Value $labels `
            -Label "container lifecycle $containerId labels"
        foreach ($name in @('com.docker.compose.project', 'com.docker.compose.service')) {
            Assert-NativeJsonString -Value $labels.$name `
                -Label "container lifecycle $containerId label $name"
        }
        $project = [string](Get-OptionalPropertyValue `
            -InputObject $labels -Name 'com.docker.compose.project')
        $service = [string](Get-OptionalPropertyValue `
            -InputObject $labels -Name 'com.docker.compose.service')
        $composeLines = @(
            "com.docker.compose.project`0$project",
            "com.docker.compose.service`0$service"
        )
        [Array]::Sort($composeLines, [StringComparer]::Ordinal)
        $entries.Add([ordered]@{
            container_id = [string]$inspect.Id
            name = [string]$inspect.Name
            state = [string]$inspect.State.Status
            running = [bool]$inspect.State.Running
            started_at = [string]$inspect.State.StartedAt
            finished_at = [string]$inspect.State.FinishedAt
            restart_count = [int]$inspect.RestartCount
            oom_killed = [bool]$inspect.State.OOMKilled
            compose_project = $project
            compose_service = $service
            compose_labels_sha256 = Get-Sha256Text -Value (
                ($composeLines -join "`n") + "`n"
            )
        })
    }
    if (@($containerIds | Where-Object { $_ -ceq $productionPostgresId }).Count -ne 1) {
        throw 'All-container inventory does not contain the exact retained PostgreSQL once.'
    }
    $inventory = @($entries.ToArray())
    $inventoryJson = ConvertTo-Json -InputObject $inventory -Depth 8 -Compress
    return [ordered]@{
        running_container_ids = @()
        containers = $inventory
        inventory_sha256 = Get-Sha256Text -Value $inventoryJson
    }
}

function Get-ProductionNetworkBoundary {
    param([Parameter(Mandatory = $true)][string] $NetworkName)

    $expected = $networkContract[$NetworkName]
    if ($null -eq $expected) {
        throw "Unknown source-pinned production network: $NetworkName"
    }
    $network = Get-OneJsonObject -Label "production network $NetworkName" -Lines (
        Invoke-PinnedDocker -Arguments @(
            'network', 'inspect', [string]$expected.id, '--format', '{{json .}}'
        )
    )
    foreach ($name in @('IPAM', 'Labels', 'Containers', 'Options', 'ConfigFrom')) {
        Assert-NativeJsonObject -Value $network.$name `
            -Label "production network $NetworkName $name"
    }
    foreach ($name in @(
        'EnableIPv4', 'EnableIPv6', 'Internal', 'Attachable', 'Ingress', 'ConfigOnly'
    )) {
        Assert-NativeJsonBoolean -Value $network.$name `
            -Label "production network $NetworkName $name"
    }
    Assert-NativeJsonString -Value $network.Created `
        -Label "production network $NetworkName Created"
    foreach ($name in @('Name', 'Id', 'Scope', 'Driver')) {
        Assert-NativeJsonString -Value $network.$name `
            -Label "production network $NetworkName $name"
    }
    Assert-NativeJsonString -Value $network.IPAM.Driver `
        -Label "production network $NetworkName IPAM.Driver"
    Assert-ExactProperties -Value $network.ConfigFrom `
        -Expected @('Network') -Label "production network $NetworkName ConfigFrom"
    Assert-NativeJsonString -Value $network.ConfigFrom.Network `
        -Label "production network $NetworkName ConfigFrom.Network"
    if ($network.IPAM.Config -isnot [Array]) {
        throw "Production network $NetworkName IPAM.Config must be a native JSON array."
    }
    $ipamConfig = @($network.IPAM.Config)
    $ipRange = $null
    $auxiliaryAddresses = $null
    if ($ipamConfig.Count -eq 1) {
        Assert-ExactProperties -Value $ipamConfig[0] -Expected @(
            'Subnet', 'Gateway', 'IPRange', 'AuxiliaryAddresses'
        ) -Label "production network $NetworkName IPAM.Config entry"
        foreach ($name in @('Subnet', 'Gateway', 'IPRange')) {
            Assert-NativeJsonString -Value $ipamConfig[0].$name `
                -Label "production network $NetworkName IPAM.Config.$name"
        }
        $ipRange = Get-OptionalPropertyValue -InputObject $ipamConfig[0] -Name 'IPRange'
        $auxiliaryAddresses = Get-OptionalPropertyValue `
            -InputObject $ipamConfig[0] -Name 'AuxiliaryAddresses'
    }
    $members = @($network.Containers.PSObject.Properties | ForEach-Object {
        [string]$_.Name
    })
    [Array]::Sort($members, [StringComparer]::Ordinal)
    if (
        $network.Name -cne $NetworkName -or
        $network.Id -cne [string]$expected.id -or
        $network.Created -cne [string]$expected.created -or
        $network.Scope -cne 'local' -or
        $network.Driver -cne 'bridge' -or
        -not [bool]$network.EnableIPv4 -or
        [bool]$network.EnableIPv6 -or
        [bool]$network.Internal -or
        [bool]$network.Attachable -or
        [bool]$network.Ingress -or
        $network.ConfigOnly -or
        $network.IPAM.Driver -cne 'default' -or
        $null -ne (Get-OptionalPropertyValue -InputObject $network.IPAM -Name 'Options') -or
        $network.ConfigFrom.Network -cne '' -or
        $ipamConfig.Count -ne 1 -or
        $ipamConfig[0].Subnet -cne [string]$expected.subnet -or
        $ipamConfig[0].Gateway -cne [string]$expected.gateway -or
        -not [string]::IsNullOrEmpty([string]$ipRange) -or
        $null -ne $auxiliaryAddresses -or
        $network.Labels.'com.docker.compose.project' -cne 'weatherpoly' -or
        $network.Labels.'com.docker.compose.network' -cne [string]$expected.compose_name -or
        $members.Count -ne 0
    ) {
        throw "Production Docker network identity/topology is not exact: $NetworkName"
    }
    $labelsHash = Get-CanonicalPropertyHash -Value $network.Labels `
        -Label "production network $NetworkName labels"
    $optionsHash = Get-CanonicalPropertyHash `
        -Value (Get-OptionalPropertyValue -InputObject $network -Name 'Options') `
        -Label "production network $NetworkName options"
    if (
        $labelsHash -cne [string]$expected.labels_sha256 -or
        $optionsHash -cne $productionNetworkOptionsSha256
    ) {
        throw "Production Docker network labels/options drifted: $NetworkName"
    }
    $body = [ordered]@{
        name = $NetworkName
        id = [string]$network.Id
        compose_name = [string]$expected.compose_name
        created = [string]$network.Created
        scope = [string]$network.Scope
        driver = [string]$network.Driver
        subnet = [string]$ipamConfig[0].Subnet
        gateway = [string]$ipamConfig[0].Gateway
        enable_ipv4 = [bool]$network.EnableIPv4
        enable_ipv6 = [bool]$network.EnableIPv6
        internal = [bool]$network.Internal
        attachable = [bool]$network.Attachable
        ingress = [bool]$network.Ingress
        attached_container_ids = $members
        labels_sha256 = $labelsHash
        options_sha256 = $optionsHash
    }
    $bodyJson = $body | ConvertTo-Json -Depth 8 -Compress
    $body['canonical_sha256'] = Get-Sha256Text -Value $bodyJson
    return $body
}

function Get-PostgresAttachmentBoundary {
    param(
        [Parameter(Mandatory = $true)][object] $Attachment,
        [Parameter(Mandatory = $true)][string] $NetworkName,
        [Parameter(Mandatory = $true)]
        [ValidateSet('ExactStopped', 'ConfiguredPending')]
        [string] $ConfigurationState
    )

    Assert-NativeJsonObject -Value $Attachment -Label "PostgreSQL attachment $NetworkName"
    Assert-ExactProperties -Value $Attachment -Label "PostgreSQL attachment $NetworkName" `
        -Expected @(
            'IPAMConfig', 'Links', 'Aliases', 'MacAddress', 'DriverOpts',
            'GwPriority', 'NetworkID', 'EndpointID', 'Gateway', 'IPAddress',
            'IPPrefixLen', 'IPv6Gateway', 'GlobalIPv6Address',
            'GlobalIPv6PrefixLen', 'DNSNames'
        )
    foreach ($name in @(
        'NetworkID', 'EndpointID', 'Gateway', 'IPAddress', 'IPv6Gateway',
        'GlobalIPv6Address', 'MacAddress'
    )) {
        Assert-NativeJsonString -Value $Attachment.$name `
            -Label "PostgreSQL attachment $NetworkName $name"
    }
    foreach ($name in @('GwPriority', 'IPPrefixLen', 'GlobalIPv6PrefixLen')) {
        Assert-NativeJsonInteger -Value $Attachment.$name `
            -Label "PostgreSQL attachment $NetworkName $name"
    }
    $expected = $networkContract[$NetworkName]
    if ($Attachment.Aliases -isnot [Array] -or $Attachment.DNSNames -isnot [Array]) {
        throw "Stopped PostgreSQL attachment arrays are not native: $NetworkName"
    }
    foreach ($value in @($Attachment.Aliases) + @($Attachment.DNSNames)) {
        Assert-NativeJsonString -Value $value `
            -Label "PostgreSQL attachment $NetworkName name"
    }
    $aliases = @($Attachment.Aliases)
    $dnsNames = @($Attachment.DNSNames)
    [Array]::Sort($aliases, [StringComparer]::Ordinal)
    [Array]::Sort($dnsNames, [StringComparer]::Ordinal)
    $expectedAliases = @('postgres', $productionPostgresName)
    [Array]::Sort($expectedAliases, [StringComparer]::Ordinal)
    $expectedDnsNames = @(
        $productionPostgresId.Substring(0, 12), 'postgres', $productionPostgresName
    )
    [Array]::Sort($expectedDnsNames, [StringComparer]::Ordinal)
    $ipamConfiguration = if ($null -eq $Attachment.IPAMConfig) {
        'null'
    }
    elseif (
        $Attachment.IPAMConfig -is [pscustomobject] -and
        @($Attachment.IPAMConfig.PSObject.Properties).Count -eq 0
    ) {
        'empty_object'
    }
    else {
        'other'
    }
    $driverOptions = if ($null -eq $Attachment.DriverOpts) {
        'null'
    }
    elseif (
        $Attachment.DriverOpts -is [pscustomobject] -and
        @($Attachment.DriverOpts.PSObject.Properties).Count -eq 0
    ) {
        'empty_object'
    }
    else {
        'other'
    }
    $configurationExact = if ($ConfigurationState -ceq 'ExactStopped') {
        $Attachment.NetworkID -ceq [string]$expected.id -and
        $ipamConfiguration -ceq 'null' -and
        $driverOptions -ceq 'null'
    }
    else {
        $NetworkName -ceq $safetyNetworkName -and
        $Attachment.NetworkID -ceq '' -and
        $ipamConfiguration -ceq 'empty_object' -and
        $driverOptions -ceq 'empty_object'
    }
    if (
        -not $configurationExact -or
        $null -ne $Attachment.Links -or
        [int]$Attachment.GwPriority -ne 0 -or
        ($aliases -join "`n") -cne ($expectedAliases -join "`n") -or
        ($dnsNames -join "`n") -cne ($expectedDnsNames -join "`n") -or
        -not [string]::IsNullOrEmpty([string]$Attachment.EndpointID) -or
        -not [string]::IsNullOrEmpty([string]$Attachment.Gateway) -or
        -not [string]::IsNullOrEmpty([string]$Attachment.IPAddress) -or
        [int]$Attachment.IPPrefixLen -ne 0 -or
        -not [string]::IsNullOrEmpty([string]$Attachment.IPv6Gateway) -or
        -not [string]::IsNullOrEmpty([string]$Attachment.GlobalIPv6Address) -or
        [int]$Attachment.GlobalIPv6PrefixLen -ne 0 -or
        -not [string]::IsNullOrEmpty([string]$Attachment.MacAddress)
    ) {
        throw "Stopped PostgreSQL attachment is not exact or configured-pending: $NetworkName"
    }
    $body = [ordered]@{
        name = $NetworkName
        configuration_state = $ConfigurationState
        network_id = [string]$Attachment.NetworkID
        ipam_configuration = $ipamConfiguration
        driver_options = $driverOptions
        gateway_priority = [int]$Attachment.GwPriority
        aliases = $aliases
        dns_names = $dnsNames
        endpoint_id = [string]$Attachment.EndpointID
        gateway = [string]$Attachment.Gateway
        ip_address = [string]$Attachment.IPAddress
        ip_prefix_length = [int]$Attachment.IPPrefixLen
        ipv6_gateway = [string]$Attachment.IPv6Gateway
        global_ipv6_address = [string]$Attachment.GlobalIPv6Address
        global_ipv6_prefix_length = [int]$Attachment.GlobalIPv6PrefixLen
        mac_address = [string]$Attachment.MacAddress
    }
    $bodyJson = $body | ConvertTo-Json -Depth 8 -Compress
    $body['canonical_sha256'] = Get-Sha256Text -Value $bodyJson
    return $body
}

function Get-PostgresBoundary {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('AllowedPre', 'PostConnect')]
        [string] $AttachmentRequirement
    )

    $inspect = Get-OneJsonObject -Label 'retained PostgreSQL inspect' -Lines (
        Invoke-PinnedDocker -Arguments @(
            'inspect', $productionPostgresId, '--format', '{{json .}}'
        )
    )
    foreach ($name in @('Config', 'State', 'HostConfig', 'NetworkSettings')) {
        Assert-NativeJsonObject -Value $inspect.$name -Label "PostgreSQL $name"
    }
    foreach ($name in @('Labels')) {
        Assert-NativeJsonObject -Value $inspect.Config.$name -Label "PostgreSQL Config.$name"
    }
    Assert-NativeJsonObject -Value $inspect.NetworkSettings.Networks `
        -Label 'PostgreSQL NetworkSettings.Networks'
    Assert-NativeJsonObject -Value $inspect.NetworkSettings.Ports `
        -Label 'PostgreSQL NetworkSettings.Ports'
    Assert-NativeJsonObject -Value $inspect.HostConfig.RestartPolicy `
        -Label 'PostgreSQL HostConfig.RestartPolicy'
    Assert-NativeJsonObject -Value $inspect.HostConfig.PortBindings `
        -Label 'PostgreSQL HostConfig.PortBindings'
    if ($inspect.Mounts -isnot [Array]) {
        throw 'PostgreSQL Mounts must be a native JSON array.'
    }
    foreach ($name in @('Id', 'Name', 'Image')) {
        Assert-NativeJsonString -Value $inspect.$name -Label "PostgreSQL $name"
    }
    Assert-NativeJsonString -Value $inspect.Config.Image -Label 'PostgreSQL Config.Image'
    Assert-NativeJsonString -Value $inspect.HostConfig.NetworkMode `
        -Label 'PostgreSQL HostConfig.NetworkMode'
    Assert-NativeJsonString -Value $inspect.HostConfig.RestartPolicy.Name `
        -Label 'PostgreSQL HostConfig.RestartPolicy.Name'
    foreach ($name in @('Status', 'StartedAt', 'FinishedAt', 'Error')) {
        Assert-NativeJsonString -Value $inspect.State.$name -Label "PostgreSQL State.$name"
    }
    foreach ($name in @('Running', 'Paused', 'Restarting', 'OOMKilled', 'Dead')) {
        Assert-NativeJsonBoolean -Value $inspect.State.$name -Label "PostgreSQL $name"
    }
    foreach ($name in @('Pid', 'ExitCode')) {
        Assert-NativeJsonInteger -Value $inspect.State.$name -Label "PostgreSQL $name"
    }
    Assert-NativeJsonInteger -Value $inspect.RestartCount -Label 'PostgreSQL RestartCount'
    Assert-NativeJsonInteger -Value $inspect.HostConfig.RestartPolicy.MaximumRetryCount `
        -Label 'PostgreSQL HostConfig.RestartPolicy.MaximumRetryCount'

    $labels = $inspect.Config.Labels
    foreach ($name in @(
        'com.docker.compose.project',
        'com.docker.compose.project.working_dir',
        'com.docker.compose.service',
        'com.docker.compose.config-hash',
        'com.docker.compose.image',
        'com.docker.compose.oneoff',
        'com.docker.compose.container-number',
        'com.docker.compose.project.config_files'
    )) {
        Assert-NativeJsonString -Value $labels.$name `
            -Label "PostgreSQL label $name"
    }
    $composePath = Join-Path $script:absoluteRoot 'docker-compose.yml'
    $overridePath = Join-Path $script:absoluteRoot 'docker-compose.override.yml'
    $hbaPath = Join-Path $script:absoluteRoot 'ops\postgres\stratus-pg_hba.conf'
    [void](Assert-RegularFile -LiteralPath $hbaPath)
    $currentHbaSha256 = Get-FileSha256 -LiteralPath $hbaPath
    if ($currentHbaSha256 -cne $hbaSha256) {
        throw 'Source-pinned PostgreSQL HBA file drifted during stopped repair.'
    }
    $hbaSources = @(Get-DockerHostPathAliases -Path $hbaPath)
    $portProperties = @(
        $inspect.HostConfig.PortBindings.PSObject.Properties |
            Where-Object { $null -ne $_.Value }
    )
    $port = $inspect.HostConfig.PortBindings.'5432/tcp'
    $runtimePortProperties = @(
        $inspect.NetworkSettings.Ports.PSObject.Properties |
            Where-Object { $null -ne $_.Value }
    )
    $runtimePort = $inspect.NetworkSettings.Ports.'5432/tcp'
    if ($port -isnot [Array] -or $runtimePort -isnot [Array]) {
        throw 'PostgreSQL port bindings must be native JSON arrays.'
    }
    foreach ($entry in @($port) + @($runtimePort)) {
        Assert-ExactProperties -Value $entry -Expected @('HostIp', 'HostPort') `
            -Label 'PostgreSQL port binding entry'
        Assert-NativeJsonString -Value $entry.HostIp `
            -Label 'PostgreSQL port binding HostIp'
        Assert-NativeJsonString -Value $entry.HostPort `
            -Label 'PostgreSQL port binding HostPort'
    }
    foreach ($mount in @($inspect.Mounts)) {
        Assert-NativeJsonObject -Value $mount -Label 'PostgreSQL mount entry'
        $expectedMountProperties = if ($mount.Type -ceq 'bind') {
            @('Type', 'Source', 'Destination', 'Mode', 'RW', 'Propagation')
        }
        elseif ($mount.Type -ceq 'volume') {
            @('Type', 'Name', 'Driver', 'Source', 'Destination', 'Mode', 'RW', 'Propagation')
        }
        else {
            throw 'PostgreSQL mount entry has an unsupported native type.'
        }
        Assert-ExactProperties -Value $mount -Expected $expectedMountProperties `
            -Label 'PostgreSQL mount entry'
        foreach ($name in @($expectedMountProperties | Where-Object { $_ -ne 'RW' })) {
            Assert-NativeJsonString -Value $mount.$name `
                -Label "PostgreSQL mount entry $name"
        }
        Assert-NativeJsonBoolean -Value $mount.RW -Label 'PostgreSQL mount entry RW'
    }
    $hbaMounts = @($inspect.Mounts | Where-Object {
        [string]$_.Destination -ceq '/etc/postgresql/stratus-pg_hba.conf'
    })
    $dataMounts = @($inspect.Mounts | Where-Object {
        [string]$_.Destination -ceq '/var/lib/postgresql/data'
    })
    if (
        $inspect.Id -cne $productionPostgresId -or
        $inspect.Name -cne "/$productionPostgresName" -or
        $inspect.Image -cne $postgresImageId -or
        $inspect.Config.Image -cne $postgresImage -or
        (Get-CommandSha256 -Inspect $inspect) -cne $postgresCommandSha256 -or
        $labels.'com.docker.compose.project' -cne 'weatherpoly' -or
        $labels.'com.docker.compose.service' -cne 'postgres' -or
        $labels.'com.docker.compose.config-hash' -cne $postgresConfigHash -or
        $labels.'com.docker.compose.image' -cne $postgresImageId -or
        $labels.'com.docker.compose.oneoff' -cne 'False' -or
        $labels.'com.docker.compose.container-number' -cne '1' -or
        [IO.Path]::GetFullPath(
            [string]$labels.'com.docker.compose.project.working_dir'
        ) -ine $script:absoluteRoot -or
        -not (Test-ComposeConfigFilesLabel `
            -Label ([string]$labels.'com.docker.compose.project.config_files') `
            -ComposePath $composePath -OverridePath $overridePath) -or
        $inspect.State.Status -cne 'exited' -or
        [bool]$inspect.State.Running -or
        [bool]$inspect.State.Paused -or
        [bool]$inspect.State.Restarting -or
        [bool]$inspect.State.OOMKilled -or
        [bool]$inspect.State.Dead -or
        [int]$inspect.State.Pid -ne 0 -or
        [int]$inspect.State.ExitCode -ne 0 -or
        $inspect.State.Error -cne '' -or
        [int]$inspect.RestartCount -ne 0 -or
        $inspect.HostConfig.RestartPolicy.Name -cne 'unless-stopped' -or
        [int]$inspect.HostConfig.RestartPolicy.MaximumRetryCount -ne 0 -or
        $inspect.HostConfig.NetworkMode -cne $defaultNetworkName -or
        $portProperties.Count -ne 1 -or
        $portProperties[0].Name -cne '5432/tcp' -or
        @($port).Count -ne 1 -or
        $port[0].HostIp -cne '127.0.0.1' -or
        $port[0].HostPort -cne '5432' -or
        $runtimePortProperties.Count -ne 1 -or
        $runtimePortProperties[0].Name -cne '5432/tcp' -or
        @($runtimePort).Count -ne 1 -or
        $runtimePort[0].HostIp -cne '127.0.0.1' -or
        $runtimePort[0].HostPort -cne '5432' -or
        @($inspect.Mounts).Count -ne 2 -or
        $hbaMounts.Count -ne 1 -or
        $hbaMounts[0].Type -cne 'bind' -or
        $hbaMounts[0].Mode -cne 'ro' -or
        $hbaMounts[0].Propagation -cne 'rprivate' -or
        [bool]$hbaMounts[0].RW -or
        ([string]$hbaMounts[0].Source).Replace('\', '/') -notin $hbaSources -or
        $dataMounts.Count -ne 1 -or
        $dataMounts[0].Type -cne 'volume' -or
        $dataMounts[0].Name -cne 'weatherpoly_stratus-postgres' -or
        $dataMounts[0].Driver -cne 'local' -or
        $dataMounts[0].Source -cne
            '/var/lib/docker/volumes/weatherpoly_stratus-postgres/_data' -or
        $dataMounts[0].Mode -cne 'rw' -or
        $dataMounts[0].Propagation -cne '' -or
        -not [bool]$dataMounts[0].RW
    ) {
        throw 'Retained production PostgreSQL stopped identity/configuration is not exact.'
    }
    $startedAt = [DateTimeOffset]::MinValue
    $finishedAt = [DateTimeOffset]::MinValue
    if (
        -not [DateTimeOffset]::TryParse([string]$inspect.State.StartedAt, [ref]$startedAt) -or
        -not [DateTimeOffset]::TryParse([string]$inspect.State.FinishedAt, [ref]$finishedAt) -or
        $finishedAt -le $startedAt
    ) {
        throw 'Retained production PostgreSQL has an invalid stopped lifecycle boundary.'
    }

    $networkProperties = @($inspect.NetworkSettings.Networks.PSObject.Properties)
    $networkNames = @($networkProperties.Name)
    [Array]::Sort($networkNames, [StringComparer]::Ordinal)
    $defaultOnly = @($defaultNetworkName)
    $exactNetworks = @($defaultNetworkName, $safetyNetworkName)
    [Array]::Sort($exactNetworks, [StringComparer]::Ordinal)
    $attachmentState = $null
    if (($networkNames -join "`n") -ceq ($defaultOnly -join "`n")) {
        $attachmentState = 'default_only'
    }
    elseif (($networkNames -join "`n") -ceq ($exactNetworks -join "`n")) {
        $safetyAttachment =
            $inspect.NetworkSettings.Networks.PSObject.Properties[$safetyNetworkName].Value
        Assert-NativeJsonObject -Value $safetyAttachment `
            -Label 'PostgreSQL configured safety-plane attachment'
        Assert-NativeJsonString -Value $safetyAttachment.NetworkID `
            -Label 'PostgreSQL configured safety-plane NetworkID'
        $safetyIpamEmpty =
            $safetyAttachment.IPAMConfig -is [pscustomobject] -and
            @($safetyAttachment.IPAMConfig.PSObject.Properties).Count -eq 0
        $safetyDriverOptionsEmpty =
            $safetyAttachment.DriverOpts -is [pscustomobject] -and
            @($safetyAttachment.DriverOpts.PSObject.Properties).Count -eq 0
        if (
            $safetyAttachment.NetworkID -ceq '' -and
            $safetyIpamEmpty -and
            $safetyDriverOptionsEmpty
        ) {
            # The pinned Docker Desktop 28.3.2 engine was observed to persist
            # this exact empty-object/blank form after connecting a stopped
            # container. Transition owns the one start that must materialize and
            # independently prove both live endpoints.
            $attachmentState = 'configured_pending'
        }
        else {
            $attachmentState = 'exact'
        }
    }
    else {
        throw 'Retained PostgreSQL attachment set is neither exact default-only nor exact final.'
    }
    if (
        $AttachmentRequirement -ceq 'PostConnect' -and
        $attachmentState -cnotin @('configured_pending', 'exact')
    ) {
        throw 'Retained PostgreSQL does not have an admissible final two-network configuration.'
    }
    $attachments = [ordered]@{}
    foreach ($networkName in $networkNames) {
        $property = $inspect.NetworkSettings.Networks.PSObject.Properties[$networkName]
        $configurationState = if (
            $attachmentState -ceq 'configured_pending' -and
            $networkName -ceq $safetyNetworkName
        ) {
            'ConfiguredPending'
        }
        else {
            'ExactStopped'
        }
        $attachments[$networkName] = Get-PostgresAttachmentBoundary `
            -Attachment $property.Value -NetworkName $networkName `
            -ConfigurationState $configurationState
    }
    $stableBody = [ordered]@{
        container_id = [string]$inspect.Id
        name = [string]$inspect.Name
        image_id = [string]$inspect.Image
        config_image = [string]$inspect.Config.Image
        command_sha256 = $postgresCommandSha256
        compose_config_hash = $postgresConfigHash
        state = [string]$inspect.State.Status
        running = [bool]$inspect.State.Running
        started_at = [string]$inspect.State.StartedAt
        finished_at = [string]$inspect.State.FinishedAt
        restart_count = [int]$inspect.RestartCount
        oom_killed = [bool]$inspect.State.OOMKilled
        exit_code = [int]$inspect.State.ExitCode
        network_mode = [string]$inspect.HostConfig.NetworkMode
        hba_sha256 = $currentHbaSha256
        hba_source = [string]$hbaMounts[0].Source
        hba_mode = [string]$hbaMounts[0].Mode
        hba_propagation = [string]$hbaMounts[0].Propagation
        data_volume = [string]$dataMounts[0].Name
        data_volume_driver = [string]$dataMounts[0].Driver
        data_volume_source = [string]$dataMounts[0].Source
        data_volume_mode = [string]$dataMounts[0].Mode
        data_volume_propagation = [string]$dataMounts[0].Propagation
        published_postgres = '127.0.0.1:5432'
        labels_sha256 = Get-CanonicalPropertyHash -Value $labels -Label 'PostgreSQL labels'
    }
    $stableHash = Get-Sha256Text -Value (
        ($stableBody | ConvertTo-Json -Depth 10 -Compress)
    )
    return [ordered]@{
        stable_identity = $stableBody
        stable_identity_sha256 = $stableHash
        attachment_state = $attachmentState
        attachments = [pscustomobject]$attachments
        attachments_sha256 = Get-Sha256Text -Value (
            ([pscustomobject]$attachments | ConvertTo-Json -Depth 12 -Compress)
        )
    }
}

function Get-RepairSnapshot {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('AllowedPre', 'PostConnect')]
        [string] $AttachmentRequirement
    )

    $engine = Get-DockerEngineBoundary
    $lifecycle = Get-ContainerLifecycleInventory
    $postgres = Get-PostgresBoundary -AttachmentRequirement $AttachmentRequirement
    $defaultNetwork = Get-ProductionNetworkBoundary -NetworkName $defaultNetworkName
    $safetyNetwork = Get-ProductionNetworkBoundary -NetworkName $safetyNetworkName
    $networkBody = [ordered]@{
        $defaultNetworkName = $defaultNetwork
        $safetyNetworkName = $safetyNetwork
    }
    $networkHash = Get-Sha256Text -Value (
        ([pscustomobject]$networkBody | ConvertTo-Json -Depth 12 -Compress)
    )
    $body = [ordered]@{
        docker_engine = $engine
        running_container_ids = @($lifecycle.running_container_ids)
        lifecycle_inventory = @($lifecycle.containers)
        lifecycle_inventory_sha256 = [string]$lifecycle.inventory_sha256
        postgres = $postgres
        production_networks = [pscustomobject]$networkBody
        production_networks_sha256 = $networkHash
    }
    $canonicalHash = Get-Sha256Text -Value (
        ($body | ConvertTo-Json -Depth 20 -Compress)
    )
    $snapshot = [ordered]@{ canonical_sha256 = $canonicalHash }
    foreach ($entry in $body.GetEnumerator()) {
        $snapshot[$entry.Key] = $entry.Value
    }
    return $snapshot
}

function Assert-StableCrossSnapshotBoundary {
    param(
        [Parameter(Mandatory = $true)][object[]] $Snapshots
    )

    $first = $Snapshots[0]
    foreach ($snapshot in $Snapshots) {
        if (
            $snapshot.docker_engine.contract_sha256 -cne
                $first.docker_engine.contract_sha256 -or
            $snapshot.lifecycle_inventory_sha256 -cne
                $first.lifecycle_inventory_sha256 -or
            $snapshot.postgres.stable_identity_sha256 -cne
                $first.postgres.stable_identity_sha256 -or
            $snapshot.production_networks_sha256 -cne
                $first.production_networks_sha256
        ) {
            throw 'Docker engine, lifecycle inventory, PostgreSQL identity, or network identity drifted.'
        }
    }
}

function Write-ReservedEvidence {
    param(
        [Parameter(Mandatory = $true)][IO.FileStream] $Stream,
        [Parameter(Mandatory = $true)][object] $Value
    )

    if ($Stream.Length -ne 0 -or $Stream.Position -ne 0) {
        throw 'Reserved evidence stream was unexpectedly modified before acceptance.'
    }
    $serialized = $Value | ConvertTo-Json -Depth 30
    $writer = [IO.StreamWriter]::new($Stream, [Text.UTF8Encoding]::new($false))
    try {
        $writer.Write($serialized)
        $writer.Flush()
        $Stream.Flush($true)
    }
    finally {
        $writer.Dispose()
    }
}

$script:absoluteRoot = [IO.Path]::GetFullPath($Root)
$absoluteEvidencePath = [IO.Path]::GetFullPath($EvidencePath)
[void](Assert-RegularDirectory -LiteralPath $script:absoluteRoot)
$composePath = Join-Path $script:absoluteRoot 'docker-compose.yml'
$overridePath = Join-Path $script:absoluteRoot 'docker-compose.override.yml'
$hbaPath = Join-Path $script:absoluteRoot 'ops\postgres\stratus-pg_hba.conf'
[void](Assert-RegularFile -LiteralPath $composePath)
[void](Assert-RegularFile -LiteralPath $overridePath)
[void](Assert-RegularFile -LiteralPath $hbaPath)
if ((Get-FileSha256 -LiteralPath $hbaPath) -cne $hbaSha256) {
    throw 'Source-pinned PostgreSQL HBA file bytes are not exact.'
}
$evidenceParent = [IO.Path]::GetDirectoryName($absoluteEvidencePath)
[void](Assert-RegularDirectory -LiteralPath $evidenceParent)
if (Test-Path -LiteralPath $absoluteEvidencePath) {
    throw 'Stopped PostgreSQL network repair refuses an existing or partial evidence path.'
}

$evidenceStream = $null
$eventWitness = $null
$repairResult = $null
$primaryFailure = $null
$cleanupFailures = [Collections.Generic.List[string]]::new()
try {
    # Reserving the final path before any Docker command makes concurrent or
    # crash-recovery reuse fail closed. A failed invocation intentionally leaves
    # a zero-byte, non-authorizing partial file; retry with a new evidence path.
    $evidenceStream = [IO.FileStream]::new(
        $absoluteEvidencePath,
        [IO.FileMode]::CreateNew,
        [IO.FileAccess]::Write,
        [IO.FileShare]::None,
        4096,
        [IO.FileOptions]::WriteThrough
    )
    Assert-NoDockerEndpointOverrides
    $docker = Resolve-PinnedDockerApplication
    $script:dockerExecutable = [string]$docker.path
    $script:dockerExecutableSha256 = [string]$docker.sha256
    $script:dockerCollectorType = Initialize-RepairDockerCollectorType
    $bootstrapEngine = Get-DockerEngineBoundary
    $eventWitness = Start-RepairDockerEventWitness -ParentDirectory $evidenceParent
    New-RepairDockerEventBarrier -Witness $eventWitness

    $pre = Get-RepairSnapshot -AttachmentRequirement AllowedPre
    if ($pre.docker_engine.contract_sha256 -cne $bootstrapEngine.contract_sha256) {
        throw 'Docker engine boundary changed between bootstrap and witnessed snapshot.'
    }
    $armed = Get-RepairSnapshot -AttachmentRequirement AllowedPre
    if ($armed.canonical_sha256 -cne $pre.canonical_sha256) {
        throw 'Stopped repair boundary changed between pre and armed snapshots.'
    }
    Assert-RepairDockerEventPreMutationBoundary -Witness $eventWitness

    $mutationArguments = @(
        'network', 'connect',
        '--alias', 'postgres',
        '--alias', $productionPostgresName,
        '--gw-priority', '0',
        $safetyNetworkId,
        $productionPostgresId
    )
    $mutationIssued = $false
    $disposition = $null
    if ($armed.postgres.attachment_state -ceq 'default_only') {
        $mutationIssued = $true
        $mutationOutput = @(
            Invoke-PinnedDocker -Arguments $mutationArguments `
                -ProcessTimeoutMilliseconds $dockerMutationTimeoutMilliseconds `
                -MaximumStdoutBytes 4096 |
                Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
        )
        if ($mutationOutput.Count -ne 0) {
            throw 'Docker network attachment mutation returned unexpected output.'
        }
        $disposition = 'connected_by_this_invocation'
    }
    elseif ($armed.postgres.attachment_state -ceq 'exact') {
        $disposition = 'verified_exact_existing'
    }
    elseif ($armed.postgres.attachment_state -ceq 'configured_pending') {
        $disposition = 'verified_configured_pending_existing'
    }
    else {
        throw 'Armed PostgreSQL network disposition is not recoverable.'
    }

    $postFirst = Get-RepairSnapshot -AttachmentRequirement PostConnect
    $postSecond = Get-RepairSnapshot -AttachmentRequirement PostConnect
    if ($postFirst.canonical_sha256 -cne $postSecond.canonical_sha256) {
        throw 'Admissible post-repair boundary was not stable across two consecutive snapshots.'
    }
    Assert-StableCrossSnapshotBoundary -Snapshots @(
        $pre, $armed, $postFirst, $postSecond
    )
    if (
        $postFirst.postgres.attachment_state -cnotin @('configured_pending', 'exact') -or
        $postSecond.postgres.attachment_state -cne $postFirst.postgres.attachment_state
    ) {
        throw 'Post-repair PostgreSQL attachment is not stably admissible.'
    }
    if (
        -not $mutationIssued -and
        $postSecond.canonical_sha256 -cne $pre.canonical_sha256
    ) {
        throw 'Already-exact recovery changed the stopped Docker boundary.'
    }

    Remove-RepairDockerEventBarrier -Witness $eventWitness
    $eventJournal = Stop-RepairDockerEventWitness -Witness $eventWitness
    $eventReceipt = Assert-RepairDockerEventContract -Witness $eventWitness `
        -Journal $eventJournal -MutationIssued $mutationIssued
    $eventWitness.collector.Dispose()
    Remove-RepairDockerEventCapture -Witness $eventWitness
    $eventWitness = $null

    $acceptedAt = [DateTimeOffset]::UtcNow.ToString('o')
    $payload = [ordered]@{
        accepted_at = $acceptedAt
        disposition = $disposition
        fixed_identity = [ordered]@{
            postgres_container_id = $productionPostgresId
            postgres_container_name = $productionPostgresName
            default_network_id = $defaultNetworkId
            safety_network_id = $safetyNetworkId
        }
        docker_engine = $postSecond.docker_engine
        event_witness = $eventReceipt
        snapshots = [ordered]@{
            pre = $pre
            armed = $armed
            post_first = $postFirst
            post_second = $postSecond
        }
        comparisons = [ordered]@{
            pre_equals_armed = $true
            post_first_equals_post_second = $true
            docker_engine_unchanged = $true
            all_container_lifecycle_inventory_unchanged = $true
            continuous_docker_event_history_exact = $true
            postgres_stopped_identity_unchanged = $true
            production_network_identity_unchanged = $true
            post_attachment_admissible_twice = $true
        }
        mutation = [ordered]@{
            issued = $mutationIssued
            command = $mutationArguments
            output_line_count = 0
        }
        authorization = [ordered]@{
            postgres_start_permitted = $false
            api_start_permitted = $false
            worker_start_permitted = $false
            safety_control_start_permitted = $false
            live_permitted = $false
            binance_kill_switch_removal_permitted = $false
        }
    }
    $payloadJson = $payload | ConvertTo-Json -Depth 30 -Compress
    $envelope = [ordered]@{
        format = 'stratus-stopped-postgres-safety-plane-repair-v2'
        accepted = $true
        payload = $payload
        payload_sha256 = Get-Sha256Text -Value $payloadJson
    }
    Write-ReservedEvidence -Stream $evidenceStream -Value $envelope
    $evidenceStream = $null

    $repairResult = [pscustomobject]@{
        accepted = $true
        disposition = $disposition
        evidence_path = $absoluteEvidencePath
    }
}
catch {
    $primaryFailure = $_
}
finally {
    if ($null -ne $eventWitness) {
        if (
            [bool]$eventWitness.barrier_create_command_succeeded -and
            -not [bool]$eventWitness.barrier_remove_command_succeeded -and
            $null -ne $script:dockerCollectorType
        ) {
            try {
                [void](Invoke-PinnedDocker -Arguments @(
                    'volume', 'rm', [string]$eventWitness.barrier_name
                ) -ProcessTimeoutMilliseconds $dockerMutationTimeoutMilliseconds `
                    -MaximumStdoutBytes 4096)
                $eventWitness.barrier_remove_command_succeeded = $true
            }
            catch {
                $cleanupFailures.Add(
                    "event barrier cleanup failed: $($_.Exception.Message)"
                )
            }
        }
        try { $eventWitness.collector.Dispose() }
        catch {
            $cleanupFailures.Add(
                "event collector cleanup failed: $($_.Exception.Message)"
            )
        }
        try { Remove-RepairDockerEventCapture -Witness $eventWitness }
        catch {
            $cleanupFailures.Add(
                "event capture cleanup failed: $($_.Exception.Message)"
            )
        }
    }
    if ($null -ne $script:dockerExecutableLock) {
        try { $script:dockerExecutableLock.Dispose() }
        catch {
            $cleanupFailures.Add(
                "Docker executable lock cleanup failed: $($_.Exception.Message)"
            )
        }
    }
    if ($null -ne $evidenceStream) {
        try { $evidenceStream.Dispose() }
        catch {
            $cleanupFailures.Add(
                "evidence reservation cleanup failed: $($_.Exception.Message)"
            )
        }
    }
}
if ($null -ne $primaryFailure) {
    if ($cleanupFailures.Count -gt 0) {
        throw [InvalidOperationException]::new(
            "Stopped PostgreSQL network repair failed. Original: $($primaryFailure.Exception.Message) Cleanup: $($cleanupFailures -join ' | ')",
            $primaryFailure.Exception
        )
    }
    throw $primaryFailure
}
if ($cleanupFailures.Count -gt 0) {
    throw [InvalidOperationException]::new(
        "Stopped PostgreSQL network repair cleanup failed after acceptance: $($cleanupFailures -join ' | ')"
    )
}
$repairResult
