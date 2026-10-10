[CmdletBinding()]
param([string] $RuntimePath)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $PSBoundParameters.ContainsKey('RuntimePath')) {
    $RuntimePath = Join-Path $PSScriptRoot '..\Stratus.ParentStage.Runtime.cs'
}
# STATIC SOURCE CONTRACTS ONLY. This lexer removes C# comments and treats string
# and character literals atomically before balancing method/class braces. It is
# not a C# compiler/semantic AST, native test, process test or proof of ownership.
# No runtime source is compiled, dot-sourced, reflected over or executed here.
$source = [IO.File]::ReadAllText([IO.Path]::GetFullPath($RuntimePath))
$results = [Collections.Generic.List[object]]::new()
$expectedCases = 42

function Assert-NativeSource {
    param([bool] $Condition, [string] $Message)
    if (-not $Condition) { throw ('Native observer contract: ' + $Message) }
}
function Get-CSharpTokens {
    param([string] $Text)
    $pattern = @'
//[^\r\n]* | /\*.*?\*/ | @"(?:""|[^"])*" | "(?:\\.|[^"\\])*" | '(?:\\.|[^'\\])*'
| [A-Za-z_][A-Za-z_0-9]* | [0-9]+ | == | != | >= | <= | \+\+ | && | \|\| | => | [^\s]
'@
    $tokens = [Collections.Generic.List[string]]::new()
    foreach ($match in [regex]::Matches($Text, $pattern, [Text.RegularExpressions.RegexOptions]::Singleline -bor [Text.RegularExpressions.RegexOptions]::IgnorePatternWhitespace)) {
        if ($match.Value.StartsWith('//') -or $match.Value.StartsWith('/*')) { continue }
        $tokens.Add($match.Value)
    }
    return ,$tokens.ToArray()
}
function Find-CSharpSequence {
    param([string[]] $Tokens, [string] $Sequence)
    $wanted = Get-CSharpTokens $Sequence
    Assert-NativeSource ($wanted.Count -gt 0) 'Empty source predicate.'
    $found = [Collections.Generic.List[int]]::new()
    for ($index = 0; $index -le $Tokens.Count - $wanted.Count; $index++) {
        $matches = $true
        for ($part = 0; $part -lt $wanted.Count; $part++) {
            if ($Tokens[$index + $part] -cne $wanted[$part]) { $matches = $false; break }
        }
        if ($matches) { $found.Add($index) }
    }
    return ,$found.ToArray()
}
function Assert-CSharpSequence {
    param([string[]] $Tokens, [string] $Sequence, [int] $Count = 1)
    Assert-NativeSource ((Find-CSharpSequence $Tokens $Sequence).Count -eq $Count) ('Required exact source sequence differs: ' + $Sequence)
}
function Get-CSharpBlock {
    param([string[]] $Tokens, [string] $Signature)
    $starts = Find-CSharpSequence $Tokens $Signature
    Assert-NativeSource ($starts.Count -eq 1) ('Missing/duplicate block: ' + $Signature)
    $start = $starts[0]; $open = $start
    while ($open -lt $Tokens.Count -and $Tokens[$open] -cne '{') { $open++ }
    Assert-NativeSource ($open -lt $Tokens.Count) 'Block has no opening brace.'
    $depth = 0
    for ($index = $open; $index -lt $Tokens.Count; $index++) {
        if ($Tokens[$index] -ceq '{') { $depth++ }
        elseif ($Tokens[$index] -ceq '}') { $depth-- }
        if ($depth -eq 0) { return ,([string[]]$Tokens[$start..$index]) }
    }
    throw 'Native observer contract: Unterminated source block.'
}
function Assert-CSharpOrder {
    param([string[]] $Tokens, [string[]] $Sequences)
    $previous = -1
    foreach ($sequence in $Sequences) {
        $locations = Find-CSharpSequence $Tokens $sequence
        Assert-NativeSource ($locations.Count -eq 1 -and $locations[0] -gt $previous) ('Source order differs: ' + $sequence)
        $previous = $locations[0]
    }
}
function Assert-ObserverNativeContract {
    param([string] $Name, [string] $Text)
    $tokens = Get-CSharpTokens $Text
    $child = Get-CSharpBlock $tokens 'public sealed class NativeChild : IDisposable'
    $frame = Get-CSharpBlock $tokens 'public sealed class NativeOutputFrame'
    $strict = Get-CSharpBlock $tokens 'public sealed class StrictJson'
    $live = Get-CSharpBlock $child 'private void AssertLiveObservationBoundary()'
    $running = Get-CSharpBlock $child 'public void AssertRunningOwned(int maximumLifetimeMilliseconds)'
    $poll = Get-CSharpBlock $child 'public NativeOutputFrame PollFirstOutputLine(int maximumLifetimeMilliseconds, int maximumFrameBytes)'
    switch ($Name) {
        'immutable_frame' {
            Assert-CSharpSequence $frame 'public readonly string Text, Sha256, StartedAt;'
            Assert-CSharpSequence $frame 'public readonly long ByteCount;'
            Assert-CSharpSequence $frame 'public readonly int ProcessId;'
            Assert-CSharpSequence $frame 'internal NativeOutputFrame(string text, string sha256, long byteCount, int processId, string startedAt)'
        }
        'synchronized_collectors' {
            Assert-CSharpSequence $child 'private readonly object captureGate = new object();'
            $collect = Get-CSharpBlock $child 'private void Collect(Stream pipe, MemoryStream destination)'
            $locked = Get-CSharpBlock $collect 'lock (captureGate)'
            Assert-CSharpSequence $locked 'overflow = destination.Length + read > limit;'
            Assert-CSharpSequence $locked 'if (!overflow) destination.Write(bytes, 0, read);'
        }
        'live_stderr_rejection' {
            Assert-CSharpSequence $live 'AssertCleanCapture(observationLifetimeMilliseconds);' 2
            $guards = Find-CSharpSequence $live 'AssertCleanCapture(observationLifetimeMilliseconds);'
            $wait = Find-CSharpSequence $live 'uint wait = WaitForSingleObject(process, 0);'
            Assert-NativeSource ($guards[0] -lt $wait[0] -and $guards[1] -gt $wait[0]) 'Kernel liveness query escapes clean-capture/deadline guards.'
        }
        'clean_capture_not_liveness' {
            $clean = Get-CSharpBlock $child 'public void AssertCleanCapture(int maximumLifetimeMilliseconds)'
            Assert-CSharpSequence $clean 'BindObservationLifetime(maximumLifetimeMilliseconds);'
            Assert-CSharpSequence $clean 'completedResult == null && runClock.ElapsedMilliseconds >= observationLifetimeMilliseconds'
            Assert-CSharpSequence $clean 'if (Failed) throw new AggregateException(FailureSnapshot());'
            $locked = Get-CSharpBlock $clean 'lock (captureGate)'
            Assert-CSharpSequence $locked 'if (errorBytes.Length != 0) throw new InvalidDataException("Owned child capture contains stderr.");'
            Assert-CSharpSequence $clean 'WaitForSingleObject' 0
            Assert-CSharpSequence $clean 'TerminateJobObject' 0
            Assert-CSharpSequence $clean 'Restart' 0
        }
        'frame_stderr_rejection' {
            $locked = Get-CSharpBlock $poll 'lock (captureGate)'
            Assert-CSharpOrder $locked @('if (errorBytes.Length != 0)', 'byte[] buffer = outputBytes.GetBuffer();', 'Buffer.BlockCopy(buffer, 0, frameBytes, 0, frameBytes.Length);')
        }
        'exact_running_job' {
            Assert-CSharpSequence $live 'uint wait = WaitForSingleObject(process, 0);'
            Assert-CSharpSequence $live 'if (wait != 258) throw new InvalidOperationException("Exact owned child is not running.");'
            Assert-CSharpSequence $running 'Check(IsProcessInJob(process, job, out member), "Running child Job membership");'
            Assert-CSharpSequence $running 'if (!member) throw new InvalidOperationException("Running child lost its exact held Job membership.");'
            Assert-CSharpSequence $running 'if (accounting.active < 1) throw new InvalidOperationException("Running child Job has no active process.");'
        }
        'live_before_after_job' {
            Assert-CSharpSequence $running 'AssertLiveObservationBoundary();' 2
            $checks = Find-CSharpSequence $running 'AssertLiveObservationBoundary();'
            $job = Find-CSharpSequence $running 'QueryInformationJobObject(job, 1, out accounting'
            Assert-NativeSource ($checks[0] -lt $job[0] -and $checks[1] -gt $job[0]) 'Job query is not enclosed by live checks.'
        }
        'original_lifetime' {
            $bind = Get-CSharpBlock $child 'private void BindObservationLifetime(int maximumLifetimeMilliseconds)'
            Assert-CSharpSequence $bind 'disposed || !resumed || maximumLifetimeMilliseconds < 1 || maximumLifetimeMilliseconds > 600000'
            Assert-CSharpSequence $bind 'if (observationLifetimeMilliseconds == 0) observationLifetimeMilliseconds = maximumLifetimeMilliseconds;'
            Assert-CSharpSequence $bind 'else if (observationLifetimeMilliseconds != maximumLifetimeMilliseconds) throw new InvalidOperationException("Owned child observation lifetime cannot be rebound.");'
            Assert-CSharpSequence $live 'completedResult != null || runClock.ElapsedMilliseconds >= observationLifetimeMilliseconds'
        }
        'result_shares_lifetime' {
            $result = Get-CSharpBlock $child 'public NativeResult PollResult(int maximumLifetimeMilliseconds)'
            Assert-CSharpOrder $result @('BindObservationLifetime(maximumLifetimeMilliseconds);', 'if (completedResult != null) return completedResult;')
        }
        'blocking_wait_no_rebind' {
            $wait = Get-CSharpBlock $child 'public NativeResult Wait(int timeoutMilliseconds)'
            Assert-CSharpOrder $wait @('if (observationLifetimeMilliseconds != 0)', 'Stopwatch clock = Stopwatch.StartNew();')
        }
        'frame_exact_cap' {
            Assert-CSharpSequence $poll 'maximumFrameBytes < 2 || maximumFrameBytes > 8192 || maximumFrameBytes > limit'
            Assert-CSharpSequence $poll 'if (frameBytes == null && available >= maximumFrameBytes) throw new InvalidDataException("First output frame exceeded its exact LF-inclusive byte bound.");'
        }
        'frame_bound_latch' {
            Assert-CSharpSequence $poll 'if (firstFrameMaximumBytes == 0) firstFrameMaximumBytes = maximumFrameBytes;'
            Assert-CSharpSequence $poll 'else if (firstFrameMaximumBytes != maximumFrameBytes) throw new InvalidOperationException("First output frame byte bound cannot be rebound.");'
        }
        'frame_live_before_after' {
            Assert-CSharpSequence $poll 'AssertRunningOwned(maximumLifetimeMilliseconds);' 2
            $checks = Find-CSharpSequence $poll 'AssertRunningOwned(maximumLifetimeMilliseconds);'
            $capture = Find-CSharpSequence $poll 'lock (captureGate)'
            $publication = Find-CSharpSequence $poll 'if (firstOutputFrame == null && candidate != null) firstOutputFrame = candidate;'
            Assert-NativeSource ($checks[0] -lt $capture[0] -and $checks[1] -gt $capture[0] -and $publication[0] -gt $checks[1]) 'Captured/cached frame escapes exact live boundaries.'
        }
        'frame_bounded_scan' {
            Assert-CSharpSequence $poll 'int available = checked((int)outputBytes.Length);'
            Assert-CSharpSequence $poll 'int count = Math.Min(available, maximumFrameBytes);'
            Assert-CSharpSequence $poll 'for (int index = 0; index < count; index++)'
        }
        'frame_exact_lf' {
            Assert-CSharpSequence $poll 'if (value == 13 || value == 0) throw new InvalidDataException("First output frame contains CR or NUL.");'
            Assert-CSharpSequence $poll 'if (value != 10) continue;'
            Assert-CSharpSequence $poll 'if (index == 0) throw new InvalidDataException("First output frame is empty.");'
            Assert-CSharpSequence $poll 'frameBytes = new byte[index + 1];'
        }
        'frame_exact_bytes' {
            Assert-CSharpSequence $poll 'Buffer.BlockCopy(buffer, 0, frameBytes, 0, frameBytes.Length);'
            Assert-CSharpSequence $poll 'new NativeOutputFrame(Files.Utf8.GetString(frameBytes), Files.Hash(frameBytes), frameBytes.Length, ProcessId, StartedAt)'
            Assert-CSharpSequence $tokens 'public static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);'
        }
        'frame_single_capture' {
            Assert-CSharpSequence $poll 'NativeOutputFrame candidate = firstOutputFrame;'
            Assert-CSharpSequence $poll 'if (candidate == null)'
            Assert-CSharpSequence $poll 'if (firstOutputFrame == null && candidate != null) firstOutputFrame = candidate;'
            Assert-CSharpSequence $poll 'return firstOutputFrame;'
        }
        'terminal_tree_and_eof' {
            $result = Get-CSharpBlock $child 'public NativeResult PollResult(int maximumLifetimeMilliseconds)'
            Assert-CSharpOrder $result @('if (accounting.active != 0 || outputThread.IsAlive || errorThread.IsAlive) return null;',
                'if (!outputThread.Join(0) || !errorThread.Join(0)) return null;',
                'Check(GetExitCodeProcess(process, out code), "Nonblocking exit code");', 'completedResult = new NativeResult')
        }
        'containment_retains_handles' {
            $stop = Get-CSharpBlock $child 'public void StopAndProve()'
            Assert-CSharpSequence $stop 'StopOwnedProcessAndJob(errors, "Owned observer containment");'
            Assert-CSharpSequence $stop 'if (errors.Count != 0) throw new AggregateException(errors.ToArray());'
            Assert-CSharpSequence $stop 'CloseHandle' 0
            Assert-CSharpSequence $stop 'disposed = true' 0
        }
        'json_distinct_fixed_caps' {
            $default = Get-CSharpBlock $strict 'public static void Validate(byte[] bytes)'
            $observer = Get-CSharpBlock $strict 'public static void ValidateObserverOutput(byte[] bytes)'
            Assert-CSharpSequence $default 'ValidateBounded(bytes, 1048576);'
            Assert-CSharpSequence $observer 'ValidateBounded(bytes, 67108864);'
            Assert-CSharpSequence $strict 'private static void ValidateBounded(byte[] bytes, int maximumBytes)'
        }
        'json_same_strict_parser' {
            $bound = Get-CSharpBlock $strict 'private static void ValidateBounded(byte[] bytes, int maximumBytes)'
            Assert-CSharpOrder $bound @('if (bytes == null) throw new ArgumentNullException("bytes");',
                'if (bytes.Length == 0 || bytes.Length > maximumBytes)', 'StrictJson parser = new StrictJson(Files.Utf8.GetString(bytes));',
                'parser.Value(0);', 'if (parser.offset != parser.text.Length)')
            Assert-CSharpSequence $strict 'if (depth > 40) throw new InvalidDataException("JSON nesting bound exceeded.");'
            Assert-CSharpSequence $strict 'if (!keys.Add(key)) throw new InvalidDataException("Duplicate JSON key: " + key);'
        }
        default { throw 'Unknown static source predicate.' }
    }
}

$contracts = @(
    @('immutable_frame', 'public readonly long ByteCount;', 'public long ByteCount;'),
    @('synchronized_collectors', 'lock (captureGate) {', 'lock (destination) {'),
    @('live_stderr_rejection', '            AssertCleanCapture(observationLifetimeMilliseconds);', '            /* removed clean-capture boundary */'),
    @('clean_capture_not_liveness', 'Owned child capture contains stderr.', 'stderr allowed during EOF drain'),
    @('frame_stderr_rejection', 'if (errorBytes.Length != 0)', 'if (false)'),
    @('exact_running_job', 'IsProcessInJob(process, job, out member)', 'IsProcessInJob(process, IntPtr.Zero, out member)'),
    @('live_before_after_job', '            AssertLiveObservationBoundary();', '            /* removed live check */'),
    @('original_lifetime', 'observationLifetimeMilliseconds != maximumLifetimeMilliseconds', 'observationLifetimeMilliseconds < maximumLifetimeMilliseconds'),
    @('result_shares_lifetime', '            BindObservationLifetime(maximumLifetimeMilliseconds);', '            /* removed lifetime binding */'),
    @('blocking_wait_no_rebind', 'if (observationLifetimeMilliseconds != 0)', 'if (false)'),
    @('frame_exact_cap', 'maximumFrameBytes > 8192', 'maximumFrameBytes > 16384'),
    @('frame_bound_latch', 'firstFrameMaximumBytes != maximumFrameBytes', 'firstFrameMaximumBytes < maximumFrameBytes'),
    @('frame_live_before_after', '            AssertRunningOwned(maximumLifetimeMilliseconds);', '            /* removed running boundary */'),
    @('frame_bounded_scan', 'Math.Min(available, maximumFrameBytes)', 'available'),
    @('frame_exact_lf', 'if (value == 13 || value == 0)', 'if (value == 0)'),
    @('frame_exact_bytes', 'Files.Hash(frameBytes)', 'Files.Hash(outputBytes.ToArray())'),
    @('frame_single_capture', 'if (firstOutputFrame == null && candidate != null) firstOutputFrame = candidate;', 'firstOutputFrame = candidate;'),
    @('terminal_tree_and_eof', 'accounting.active != 0 || outputThread.IsAlive || errorThread.IsAlive', 'accounting.active > 1 || outputThread.IsAlive || errorThread.IsAlive'),
    @('containment_retains_handles', 'StopOwnedProcessAndJob(errors, "Owned observer containment");', 'disposed = true; StopOwnedProcessAndJob(errors, "Owned observer containment");'),
    @('json_distinct_fixed_caps', 'ValidateBounded(bytes, 1048576);', 'ValidateBounded(bytes, 67108864);'),
    @('json_same_strict_parser', 'if (depth > 40)', 'if (depth > 400)')
)
foreach ($contract in $contracts) {
    $name = $contract[0]
    try {
        Assert-ObserverNativeContract $name $source
        $results.Add([pscustomobject]@{ name = $name; negative_control = $false; passed = $true })
    }
    catch { $results.Add([pscustomobject]@{ name = $name; negative_control = $false; passed = $false; error = $_.Exception.Message }) }
    try {
        Assert-NativeSource ($source.Contains($contract[1])) 'Negative-control replacement target is absent.'
        $changed = $source.Replace($contract[1], $contract[2])
        if ($name -ceq 'live_stderr_rejection') {
            # Remove ONLY the final post-wait guard; the pre-wait check remains.
            $last = $source.LastIndexOf($contract[1], [StringComparison]::Ordinal)
            $changed = $source.Remove($last, $contract[1].Length).Insert($last, $contract[2])
        }
        $failure = $null
        try { Assert-ObserverNativeContract $name $changed }
        catch { $failure = $_.Exception.Message }
        Assert-NativeSource ($null -ne $failure -and $failure.StartsWith('Native observer contract: ')) 'Negative control escaped its predicate.'
        $results.Add([pscustomobject]@{ name = $name; negative_control = $true; passed = $true })
    }
    catch { $results.Add([pscustomobject]@{ name = $name; negative_control = $true; passed = $false; error = $_.Exception.Message }) }
}
$failed = @($results.ToArray() | Where-Object { -not $_.passed })
[pscustomobject]@{ suite = 'parent-observer-native-static-contracts'; expected = $expectedCases; executed = $results.Count
    passed = $results.Count - $failed.Count; failed = $failed.Count
    source_sha256 = (Get-FileHash -LiteralPath $RuntimePath -Algorithm SHA256).Hash.ToLowerInvariant()
    static_source_only = $true; csharp_semantic_ast = $false; native_compiled = $false; native_executed = $false
    processes_launched = $false; full_protocol_accepted = $false; results = @($results.ToArray()) } | ConvertTo-Json -Depth 6 -Compress
if ($results.Count -ne $expectedCases -or $failed.Count -ne 0) {
    $global:LASTEXITCODE = 1
    throw ('Static native observer contracts failed: ' + (($failed | ForEach-Object { $_.name + ': ' + $_.error }) -join '; '))
}
$global:LASTEXITCODE = 0
