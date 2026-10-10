[CmdletBinding()]
param([string] $RuntimePath)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $RuntimePath) { $RuntimePath = Join-Path $PSScriptRoot '..\Stratus.ParentStage.Runtime.cs' }
# SOURCE CONTRACTS ONLY. Comment/literal-aware tokenization and balanced blocks
# are not a semantic C# AST or native execution. No runtime type is compiled,
# loaded, invoked or reflected; no process, pipe or filesystem fixture is made.
$sourceBytes = [IO.File]::ReadAllBytes([IO.Path]::GetFullPath($RuntimePath))
$source = [Text.UTF8Encoding]::new($false,$true).GetString($sourceBytes)
$sha = [Security.Cryptography.SHA256]::Create()
try { $sourceHashAtRead = [BitConverter]::ToString($sha.ComputeHash($sourceBytes)).Replace('-','').ToLowerInvariant() }
finally { $sha.Dispose() }
function Assert-ObservationSource { param([bool] $Okay,[string] $Label) if (-not $Okay) { throw ('Root observation source contract: '+$Label) } }
function Get-ObservationTokens {
    param([string] $Text)
    $pattern = @'
//[^\r\n]* | /\*.*?\*/ | @"(?:""|[^"])*" | "(?:\\.|[^"\\])*" | '(?:\\.|[^'\\])*'
| [A-Za-z_][A-Za-z_0-9]* | [0-9]+ | == | != | >= | <= | \+\+ | && | \|\| | => | [^\s]
'@
    $tokens = [Collections.Generic.List[string]]::new()
    foreach ($match in [regex]::Matches($Text,$pattern,[Text.RegularExpressions.RegexOptions]::Singleline -bor [Text.RegularExpressions.RegexOptions]::IgnorePatternWhitespace)) {
        if (-not $match.Value.StartsWith('//') -and -not $match.Value.StartsWith('/*')) { $tokens.Add($match.Value) }
    }
    return ,$tokens.ToArray()
}
function Find-ObservationSequence {
    param([string[]] $Tokens,[string] $Sequence)
    $wanted=Get-ObservationTokens $Sequence
    Assert-ObservationSource ($wanted.Count -gt 0) 'Empty predicate.'
    $found=[Collections.Generic.List[int]]::new()
    for ($index=0;$index -le $Tokens.Count-$wanted.Count;$index++) {
        $same=$true
        for ($part=0;$part -lt $wanted.Count;$part++) { if ($Tokens[$index+$part] -cne $wanted[$part]) { $same=$false;break } }
        if ($same) { $found.Add($index) }
    }
    return ,$found.ToArray()
}
function Require-ObservationSequence {
    param([string[]] $Tokens,[string] $Sequence,[int] $Count=1)
    Assert-ObservationSource ((Find-ObservationSequence $Tokens $Sequence).Count -eq $Count) ('Token sequence differs: '+$Sequence)
}
function Get-ObservationBlock {
    param([string[]] $Tokens,[string] $Signature)
    $found=Find-ObservationSequence $Tokens $Signature
    Assert-ObservationSource ($found.Count -eq 1) ('Missing/duplicate block: '+$Signature)
    $start=$found[0];$open=$start
    while ($open -lt $Tokens.Count -and $Tokens[$open] -cne '{') { $open++ }
    Assert-ObservationSource ($open -lt $Tokens.Count) 'Opening brace missing.'
    $depth=0
    for ($index=$open;$index -lt $Tokens.Count;$index++) {
        if ($Tokens[$index] -ceq '{') { $depth++ } elseif ($Tokens[$index] -ceq '}') { $depth-- }
        if ($depth -eq 0) { return ,([string[]]$Tokens[$start..$index]) }
    }
    throw 'Root observation source contract: Unbalanced block.'
}
function Require-ObservationOrder {
    param([string[]] $Tokens,[string[]] $Sequences)
    $previous=-1
    foreach ($sequence in $Sequences) {
        $found=Find-ObservationSequence $Tokens $sequence
        Assert-ObservationSource ($found.Count -eq 1 -and $found[0] -gt $previous) ('Ordering differs: '+$sequence)
        $previous=$found[0]
    }
}
function Assert-RootObservationContract {
    param([string] $Name,[string] $Text)
    $class=Get-ObservationBlock (Get-ObservationTokens $Text) 'public sealed class IndependentTransitionLaunch : IDisposable'
    $poll=Get-ObservationBlock $class 'public RootTerminationObservation PollRootTermination()'
    $value=Get-ObservationBlock $class 'public sealed class RootTerminationObservation'
    switch ($Name) {
        'original_retained_identity' { Require-ObservationSequence $poll 'if(disposed || process==IntPtr.Zero || !identityEstablished)' }
        'one_actual_live_pending_branch' {
            Require-ObservationSequence $poll 'uint wait=WaitForSingleObject(heldProcess,0); if(wait==258) return null;'
            Require-ObservationSequence $poll 'return null;' 1
        }
        'unknown_wait_is_not_death' {
            Require-ObservationSequence $poll 'Check(wait!=UInt32.MaxValue,"Transition root-death wait");'
            Require-ObservationSequence $poll 'if(wait!=0) throw new InvalidOperationException'
        }
        'held_handle_native_queries' {
            Require-ObservationSequence $poll 'GetProcessId(heldProcess)'
            Require-ObservationSequence $poll 'GetProcessTimes(heldProcess,out born,out exited,out kernel,out user)'
            Require-ObservationSequence $poll 'GetExitCodeProcess(heldProcess,out code)'
        }
        'native_birth_and_exit_order' {
            Require-ObservationSequence $poll 'observedPid!=(uint)ProcessId || born<=0 || exited<born || exited<=0'
            Require-ObservationSequence $poll 'new DateTimeOffset(DateTime.FromFileTimeUtc(born)).ToString("o")!=StartedAt'
        }
        'second_signal_mandatory' {
            Require-ObservationSequence $poll 'uint after=WaitForSingleObject(heldProcess,0);'
            Require-ObservationSequence $poll 'Check(after!=UInt32.MaxValue,"Transition root-death recheck");'
            Require-ObservationSequence $poll 'if(after!=0) throw new InvalidOperationException'
        }
        'stable_native_death_facts' {
            Require-ObservationSequence $poll 'GetProcessTimes(heldProcess,out bornAgain,out exitedAgain,out kernelAgain,out userAgain)'
            Require-ObservationSequence $poll 'GetExitCodeProcess(heldProcess,out codeAgain)'
            Require-ObservationSequence $poll 'if(bornAgain!=born || exitedAgain!=exited || codeAgain!=code)'
        }
        'kernel_query_before_publication' {
            Require-ObservationOrder $poll @('IntPtr heldProcess=process;','uint wait=WaitForSingleObject(heldProcess,0);',
                'uint after=WaitForSingleObject(heldProcess,0);','if(bornAgain!=born || exitedAgain!=exited || codeAgain!=code)',
                'rootTerminationFacts=new RootTerminationFacts','rootTerminationObservation=RootTerminationObservation.FromOwned(this);')
        }
        'exact_handle_at_publication' { Require-ObservationSequence $poll 'if(disposed || process!=heldProcess || !identityEstablished)' }
        'no_protocol_or_frame_prerequisite' {
            foreach ($token in @('ThrowFaults','AssertClock','AssertIdentity','AssertNoJob','PollTerminalExit','PollReady','ParseFrame','ReadFrameLocked','readyProof','resumeSucceeded')) { Require-ObservationSequence $poll $token 0 }
        }
        'no_lifecycle_or_pid_reopen' {
            foreach ($token in @('TerminateProcess','TerminateJobObject','CloseHandle','Dispose','Kill','OpenProcess','GetProcessById','SuspendThread','ResumeThread','Thread.Sleep')) { Require-ObservationSequence $poll $token 0 }
        }
        'no_protocol_authority_setters' {
            foreach ($token in @('terminalProof','terminalProofReady','readyProofReady','resumeAttempted','resumeSucceeded','disposed =','process =','thread =')) { Require-ObservationSequence $poll $token 0 }
        }
        'unsigned_any_exit_code' {
            Require-ObservationSequence $value 'public readonly uint ExitCode;'
            foreach ($token in @('code!=0','code==0','code!=2','code==2','code!=3','code==3','code==259','code!=259')) { Require-ObservationSequence $poll $token 0 }
        }
        'immutable_first_snapshot' {
            Require-ObservationSequence $class 'private RootTerminationObservation rootTerminationObservation;'
            Require-ObservationSequence $poll 'if(rootTerminationObservation!=null) return rootTerminationObservation;' 2
            Require-ObservationSequence $value 'private RootTerminationObservation(IndependentTransitionLaunch owner)'
            Require-ObservationSequence $value 'public readonly int ProcessId,RecordedFaultCount;'
            Require-ObservationSequence $value 'public readonly string StartedAt,SessionId,Nonce,ExitedAt,ObservedAt,FaultSummary;'
        }
        'factory_requires_original_facts' { Require-ObservationSequence $value 'if(owner==null || !owner.identityEstablished || owner.rootTerminationFacts==null)' }
        'all_authorities_false' { Require-ObservationSequence $value 'public readonly bool DescendantEmptinessProven=false,ProtocolAccepted=false,ContainmentAccepted=false,RetirementPermitted=false;' }
        'original_deadline_only_diagnostic' {
            Require-ObservationSequence $poll 'DateTimeOffset observedAt=DateTimeOffset.UtcNow;'
            Require-ObservationSequence $poll 'long elapsed=lifetimeClock.ElapsedMilliseconds;'
            Require-ObservationSequence $poll 'ObservedAfterProtocolDeadline=observedAt>=deadline || elapsed>=maximumLifetimeMilliseconds'
            foreach ($token in @('Restart','StartNew','maximumLifetimeMilliseconds =','deadline =')) { Require-ObservationSequence $poll $token 0 }
        }
        'collector_observation_nonblocking' {
            Require-ObservationSequence $poll 'bool stdoutCompleted=outputThread!=null && outputThread.Join(0);'
            Require-ObservationSequence $poll 'bool stderrCompleted=errorThread!=null && errorThread.Join(0);'
            Require-ObservationSequence $poll 'Join(' 2
        }
        'eof_not_required_for_root_death' {
            Require-ObservationSequence $poll 'outputEof' 0
            Require-ObservationSequence $poll 'errorEof' 0
            Require-ObservationSequence $value 'StdoutEofAtObservation=owner.outputEof; StderrEofAtObservation=owner.errorEof;'
            Require-ObservationSequence $value 'PipeDrainCompleteAtObservation=StdoutEofAtObservation && StderrEofAtObservation && StdoutCollectorCompletedAtObservation && StderrCollectorCompletedAtObservation;'
        }
        'capture_lower_bounds_not_exact_totals' {
            Require-ObservationSequence $value 'StdoutRetainedBytes=owner.outputBytes.Length;'
            Require-ObservationSequence $value 'StdoutObservedBytesLowerBound=owner.outputCount; StderrObservedBytesLowerBound=owner.errorCount;'
            Require-ObservationSequence $value 'StdoutCaptureTruncatedAtObservation=owner.outputCount!=owner.outputBytes.Length;'
        }
        'fault_summary_bounded' {
            Require-ObservationSequence $value 'RecordedFaultCount=Math.Min(owner.faults.Count,8);'
            Require-ObservationSequence $value 'for(int index=0;index<RecordedFaultCount;index++)'
            Require-ObservationSequence $value 'message.Substring(0,Math.Min(message.Length,512))'
            Require-ObservationSequence $value 'FaultSummary=summary.ToString();'
        }
        'query_errors_retained_not_null' { Require-ObservationSequence $poll 'catch(Exception failure) { Record(failure); throw; }' }
        'success_exitproof_unchanged_strictness' {
            $success=Get-ObservationBlock $class 'public TerminalExitProof PollTerminalExit()'
            Require-ObservationSequence $success 'if(!resumeSucceeded || readyProof==null)'
            Require-ObservationSequence $success 'AssertClock(false); AssertIdentity(); AssertNoJob(); ThrowFaults();'
            Require-ObservationSequence $success 'code!=0 || exited<=0 || exited<born'
            Require-ObservationSequence $success 'if(!outputEof || !errorEof || errorCount!=0 || outputCount!=readyCapture.Length+completeCapture.Length)'
            Require-ObservationSequence $success 'RootTerminationObservation' 0
        }
        'dispose_not_enabled_by_observation' {
            $dispose=Get-ObservationBlock $class 'public void Dispose()'
            Require-ObservationSequence $dispose 'if(resumeAttempted && terminalProof==null)'
            foreach ($token in @('rootTerminationObservation','rootTerminationFacts','RootTerminationObservation','PollRootTermination')) { Require-ObservationSequence $dispose $token 0 }
        }
        default { throw 'Unknown root observation source predicate.' }
    }
}
$rows=@(
    @('original_retained_identity','if(disposed || process==IntPtr.Zero || !identityEstablished)','if(disposed || process==IntPtr.Zero)'),
    @('one_actual_live_pending_branch','if(wait==258) return null;','if(wait==258) return rootTerminationObservation;'),
    @('unknown_wait_is_not_death','Check(wait!=UInt32.MaxValue,"Transition root-death wait");',''),
    @('held_handle_native_queries','GetProcessId(heldProcess)','GetProcessId(process)'),
    @('native_birth_and_exit_order','observedPid!=(uint)ProcessId || born<=0 || exited<born || exited<=0','observedPid!=(uint)ProcessId'),
    @('second_signal_mandatory','if(after!=0) throw new InvalidOperationException','if(false) throw new InvalidOperationException'),
    @('stable_native_death_facts','if(bornAgain!=born || exitedAgain!=exited || codeAgain!=code)','if(bornAgain!=born)'),
    @('kernel_query_before_publication','rootTerminationFacts=new RootTerminationFacts','rootTerminationFacts=UnownedFacts'),
    @('exact_handle_at_publication','if(disposed || process!=heldProcess || !identityEstablished)','if(disposed || !identityEstablished)'),
    @('no_protocol_or_frame_prerequisite','IntPtr heldProcess=process;','ThrowFaults(); IntPtr heldProcess=process;'),
    @('no_lifecycle_or_pid_reopen','IntPtr heldProcess=process;','CloseHandle(process); IntPtr heldProcess=process;'),
    @('no_protocol_authority_setters','IntPtr heldProcess=process;','terminalProofReady=true; IntPtr heldProcess=process;'),
    @('unsigned_any_exit_code','public readonly uint ExitCode;','public readonly int ExitCode;'),
    @('immutable_first_snapshot','public readonly int ProcessId,RecordedFaultCount;','public int ProcessId,RecordedFaultCount;'),
    @('factory_requires_original_facts','if(owner==null || !owner.identityEstablished || owner.rootTerminationFacts==null)','if(owner==null)'),
    @('all_authorities_false','ContainmentAccepted=false,RetirementPermitted=false;','ContainmentAccepted=true,RetirementPermitted=false;'),
    @('original_deadline_only_diagnostic','ObservedAfterProtocolDeadline=observedAt>=deadline || elapsed>=maximumLifetimeMilliseconds','ObservedAfterProtocolDeadline=false'),
    @('collector_observation_nonblocking','bool stdoutCompleted=outputThread!=null && outputThread.Join(0);','bool stdoutCompleted=outputThread!=null && outputThread.Join(5000);'),
    @('eof_not_required_for_root_death','IntPtr heldProcess=process;','if(!outputEof) return null; IntPtr heldProcess=process;'),
    @('capture_lower_bounds_not_exact_totals','StdoutCaptureTruncatedAtObservation=owner.outputCount!=owner.outputBytes.Length;','StdoutCaptureTruncatedAtObservation=false;'),
    @('fault_summary_bounded','message.Substring(0,Math.Min(message.Length,512))','message'),
    @('query_errors_retained_not_null','} catch(Exception failure) { Record(failure); throw; }','} catch(Exception failure) { Record(failure); return null; }'),
    @('success_exitproof_unchanged_strictness','code!=0 || exited<=0 || exited<born','exited<=0 || exited<born'),
    @('dispose_not_enabled_by_observation','if(resumeAttempted && terminalProof==null)','if(resumeAttempted && terminalProof==null && rootTerminationObservation==null)')
)
$results=[Collections.Generic.List[object]]::new()
foreach ($row in $rows) {
    Assert-RootObservationContract $row[0] $source
    $results.Add([pscustomobject]@{name=$row[0];negative_control=$false;passed=$true})
    Assert-ObservationSource ($source.Contains($row[1])) ('Mutation target missing: '+$row[0])
    $mutated=$source.Replace($row[1],$row[2]);$failure=$null
    try { Assert-RootObservationContract $row[0] $mutated } catch { $failure=$_.Exception.Message }
    Assert-ObservationSource ($null -ne $failure -and $failure.StartsWith('Root observation source contract:')) ('Negative control survived: '+$row[0])
    $results.Add([pscustomobject]@{name=($row[0]+' removal rejected');negative_control=$true;passed=$true})
}
$expectedCases=48
Assert-ObservationSource ($results.Count -eq $expectedCases) 'Fixed case count differs.'
[pscustomobject]@{suite='transition-root-termination-observation-source';expected=$expectedCases;executed=$results.Count;passed=$results.Count;failed=0
    runtime_sha256=$sourceHashAtRead
    native_executed=$false;runtime_compiled=$false;runtime_members_invoked=$false;semantic_csharp_ast=$false
    full_protocol_accepted=$false;results=@($results.ToArray())} | ConvertTo-Json -Depth 5 -Compress
