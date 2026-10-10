[CmdletBinding()]
param([string] $RuntimePath)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if (-not $RuntimePath) { $RuntimePath=Join-Path $PSScriptRoot '..\Stratus.ParentStage.Runtime.cs' }
# Source-token and negative-source-mutation checks ONLY. No runtime compilation,
# member invocation, native process/pipe/thread, Docker or gate/watchdog body.
# Balanced tokens are not semantic C#, kernel ownership or timing qualification.
$bytes=[IO.File]::ReadAllBytes([IO.Path]::GetFullPath($RuntimePath))
$source=[Text.UTF8Encoding]::new($false,$true).GetString($bytes)
$sha=[Security.Cryptography.SHA256]::Create()
try {$sourceHash=[BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','').ToLowerInvariant()} finally {$sha.Dispose()}
function Assert-CommandSource {param([bool]$Okay,[string]$Message) if(-not $Okay){throw ('COMMAND source contract: '+$Message)}}
function Get-CommandTokens {
    param([string]$Text)
    $pattern=@'
//[^\r\n]* | /\*.*?\*/ | @"(?:""|[^"])*" | "(?:\\.|[^"\\])*" | '(?:\\.|[^'\\])*'
| [A-Za-z_][A-Za-z_0-9]* | [0-9]+ | == | != | >= | <= | \+\+ | && | \|\| | => | [^\s]
'@
    $r=[Collections.Generic.List[string]]::new()
    foreach($m in [regex]::Matches($Text,$pattern,[Text.RegularExpressions.RegexOptions]::Singleline -bor [Text.RegularExpressions.RegexOptions]::IgnorePatternWhitespace)){
        if(-not $m.Value.StartsWith('//') -and -not $m.Value.StartsWith('/*')){$r.Add($m.Value)}
    }
    return ,$r.ToArray()
}
function Find-CommandSequence {
    param([string[]]$Tokens,[string]$Sequence)
    $wanted=Get-CommandTokens $Sequence; Assert-CommandSource ($wanted.Count -gt 0) 'Empty predicate.'
    $r=[Collections.Generic.List[int]]::new()
    for($i=0;$i -le $Tokens.Count-$wanted.Count;$i++){
        $same=$true
        for($j=0;$j -lt $wanted.Count;$j++){if($Tokens[$i+$j] -cne $wanted[$j]){$same=$false;break}}
        if($same){$r.Add($i)}
    }
    return ,$r.ToArray()
}
function Require-CommandSequence {
    param([string[]]$Tokens,[string]$Sequence,[int]$Count=1)
    Assert-CommandSource ((Find-CommandSequence $Tokens $Sequence).Count -eq $Count) ('Sequence differs: '+$Sequence)
}
function Get-CommandBlock {
    param([string[]]$Tokens,[string]$Signature)
    $found=Find-CommandSequence $Tokens $Signature; Assert-CommandSource ($found.Count -eq 1) ('Missing/duplicate block: '+$Signature)
    $start=$found[0];$open=$start
    while($open -lt $Tokens.Count -and $Tokens[$open] -cne '{'){$open++}
    Assert-CommandSource ($open -lt $Tokens.Count) 'Opening brace missing.'
    $depth=0
    for($i=$open;$i -lt $Tokens.Count;$i++){
        if($Tokens[$i] -ceq '{'){$depth++}elseif($Tokens[$i] -ceq '}'){$depth--}
        if($depth -eq 0){return ,([string[]]$Tokens[$start..$i])}
    }
    throw 'COMMAND source contract: Unbalanced block.'
}
function Require-CommandOrder {
    param([string[]]$Tokens,[string[]]$Sequences)
    $previous=-1
    foreach($sequence in $Sequences){
        $found=Find-CommandSequence $Tokens $sequence
        Assert-CommandSource ($found.Count -eq 1 -and $found[0] -gt $previous) ('Order differs: '+$sequence)
        $previous=$found[0]
    }
}
function Assert-NativeCommandContract {
    param([string]$Name,[string]$Text)
    $tokens=Get-CommandTokens $Text
    $c=Get-CommandBlock $tokens 'public sealed class NativeCommand : IDisposable'
    $prepare=Get-CommandBlock $c 'public static NativeCommand Prepare('
    $start=Get-CommandBlock $c 'public void StartSuspended()'
    switch($Name){
        'clock_precedes_validation_and_registration' {
            Require-CommandOrder $prepare @('Stopwatch clock=Stopwatch.StartNew();','if(session==null || heldExecutable==null || arguments==null)','new NativeCommand(','command.sessionJob=session.RegisterCommand(command);')
            Require-CommandSequence $c 'successClock.Restart(' 0
        }
        'prepare_fixed_budget_and_capture_bounds' {
            Require-CommandSequence $prepare 'if(timeoutMilliseconds!=15000 && timeoutMilliseconds!=25000)'
            Require-CommandSequence $prepare 'if(maximumBytes<1 || maximumBytes>1048576)'
        }
        'registration_is_last_factory_operation' {
            Require-CommandSequence $prepare 'command.sessionJob=session.RegisterCommand(command); return command; }'
            foreach($bad in @('CreateProcess(','CreateJobObject(','Files.RegularAncestry(','Files.HashStream(')){Require-CommandSequence $prepare $bad 0}
        }
        'managed_constructor_has_no_native_launch' {
            $fn=Get-CommandBlock $c 'private NativeCommand('
            Require-CommandSequence $fn 'this.session=session;'
            foreach($bad in @('CreateProcess(','CreateJobObject(','StartSuspended(','Files.HashStream(')){Require-CommandSequence $fn $bad 0}
        }
        'frozen_quoted_argument_and_path_bounds' {
            Require-CommandSequence $prepare 'if(arguments.Length>256)'
            Require-CommandSequence $prepare 'line.Append(" ").Append(NativeChild.Quote(argument));'
            Require-CommandSequence $prepare 'if(line.Length>32767)' 2
            Require-CommandSequence (Get-CommandBlock $c 'private static void CanonicalCommandPath(') "path.IndexOf(':',2)>=0"
        }
        'start_single_use_before_native_work' {
            Require-CommandOrder $start @('if(startAttempted)','startAttempted=true; SuccessBoundary(); HeldExecutableBoundary(); SuccessBoundary();','commandJob=CreateJobObject(IntPtr.Zero,null);')
        }
        'original_executable_handle_hash_and_metadata' {
            $fn=Get-CommandBlock $c 'private void HeldExecutableBoundary()'
            Require-CommandSequence $fn 'heldExecutable.SafeFileHandle.IsClosed || heldExecutable.SafeFileHandle.IsInvalid || !heldExecutable.CanRead'
            Require-CommandSequence $fn '!String.Equals(heldExecutable.Name,executable,StringComparison.OrdinalIgnoreCase)'
            Require-CommandSequence $fn 'Files.RequireSingleLink(heldExecutable);'
            Require-CommandSequence $fn 'if(Files.HashStream(heldExecutable)!=expectedHash)'
        }
        'private_d_kill_on_close_before_birth' {
            Require-CommandOrder $start @('commandJob=CreateJobObject(IntPtr.Zero,null);','limits.basic.flags=0x2000;','Check(SetInformationJobObject(commandJob,9,ref limits,(uint)Marshal.SizeOf(limits)),"Command D kill-on-close");','Check(CreateProcess(')
        }
        'two_attributes_stdio_exact_three' {
            Require-CommandSequence $start 'InitializeProcThreadAttributeList(IntPtr.Zero,2,0,ref size);'
            Require-CommandSequence $start 'InitializeProcThreadAttributeList(attributes,2,0,ref size)'
            Require-CommandSequence $start 'Marshal.WriteIntPtr(handles,' 3
            Require-CommandSequence $start 'Check(UpdateProcThreadAttribute(attributes,0,new IntPtr(0x20002),handles,new IntPtr(IntPtr.Size*3),IntPtr.Zero,IntPtr.Zero),"Command stdio-only inheritance");'
        }
        'birth_job_list_outer_c_then_own_d' {
            Require-CommandSequence $start 'jobs=Marshal.AllocHGlobal(IntPtr.Size*2); Marshal.WriteIntPtr(jobs,0,sessionJob); Marshal.WriteIntPtr(jobs,IntPtr.Size,commandJob);'
            Require-CommandSequence $start 'Check(UpdateProcThreadAttribute(attributes,0,new IntPtr(0x2000D),jobs,new IntPtr(IntPtr.Size*2),IntPtr.Zero,IntPtr.Zero),"Required command birth C/D list");'
        }
        'creation_exact_suspended_no_breakaway_or_fallback' {
            Require-CommandSequence $start 'Check(CreateProcess(executable,new StringBuilder(commandLine),IntPtr.Zero,IntPtr.Zero,true,0x08080004, IntPtr.Zero,directory,ref startup,out info),"Create command suspended in C/D");'
            Require-CommandSequence $start 'CreateProcess(' 1
            Require-CommandSequence $c 'AssignProcessToJobObject' 0
        }
        'birth_backing_buffers_outlive_attributes' {
            Require-CommandOrder (Get-CommandBlock $start 'finally { if(initialized)') @('if(initialized) DeleteProcThreadAttributeList(attributes);','Marshal.FreeHGlobal(attributes);','Marshal.FreeHGlobal(handles);','Marshal.FreeHGlobal(jobs);')
        }
        'actual_root_identity_and_both_memberships' {
            $fn=Get-CommandBlock $c 'private void RootIdentity('
            Require-CommandSequence $fn 'GetProcessId(process)!=(uint)pid'
            Require-CommandSequence $fn 'else if(created!=birth)'
            Require-CommandSequence $fn 'IsProcessInJob(process,sessionJob,out member)'
            Require-CommandSequence $fn 'IsProcessInJob(process,commandJob,out member)'
            Require-CommandSequence $fn 'if(!member)' 2
        }
        'suspended_one_process_requirement_uses_d_not_c' {
            Require-CommandSequence $start 'AccountingFor(commandJob,"Suspended command D accounting").active!=1'
            Require-CommandSequence (Get-CommandBlock $c 'public void Resume()') 'AccountingFor(commandJob,"Command pre-resume D accounting").active!=1'
        }
        'partial_start_failure_remains_registered' {
            Require-CommandSequence $start 'catch(Exception error) { Record(error); throw; }'
            Require-CommandSequence $start 'Dispose(' 0
            Require-CommandSequence $start 'ReleaseCommand(' 0
        }
        'collectors_retry_only_unstarted_owned_threads' {
            $fn=Get-CommandBlock $c 'private void EnsureCollectors()'
            Require-CommandSequence $fn 'if((outputThread.ThreadState & System.Threading.ThreadState.Unstarted)!=0) outputThread.Start();'
            Require-CommandSequence $fn 'if((errorThread.ThreadState & System.Threading.ThreadState.Unstarted)!=0) errorThread.Start();'
        }
        'collector_fixed_storage_and_any_stderr_fault' {
            $fn=Get-CommandBlock $c 'private void Collect('
            Require-CommandSequence $fn 'bad=stderr || destination.Length+count>maximumBytes;'
            Require-CommandSequence $fn 'if(destination.Length+count<=maximumBytes) destination.Write(buffer,0,count);'
            Require-CommandSequence $fn 'if(count==0) { lock(sync) { if(stderr) stderrEof=true; else stdoutEof=true; } return; }'
        }
        'failure_never_terminates_or_closes_session_c' {
            Require-CommandSequence $c 'TerminateJobObject(commandJob,' 2
            foreach($bad in @('TerminateJobObject(sessionJob,','CloseHandle(sessionJob)','CloseOwned(ref sessionJob)','TerminateProcess(')){Require-CommandSequence $c $bad 0}
        }
        'resume_attempt_latched_before_actual_resume' {
            Require-CommandSequence (Get-CommandBlock $c 'public void Resume()') 'SuccessBoundary(); resumeAttempted=true; uint resumedCount=ResumeThread(thread);'
            Require-CommandOrder (Get-CommandBlock $c 'public void Resume()') @(
                'uint previous=SuspendThread(thread);', 'if(previous==UInt32.MaxValue) Check(false,"Inspect command suspend count");',
                'if(previous!=1) throw new InvalidOperationException("Command suspension was disproved; retain the inspection increment for containment.");',
                'uint restored=ResumeThread(thread);', 'if(restored==UInt32.MaxValue) Check(false,"Restore command inspection increment");',
                'if(restored!=2) throw new InvalidOperationException("Command suspension restoration was ambiguous.");')
        }
        'disproved_suspend_never_compensated' {
            Require-CommandOrder (Get-CommandBlock $c 'public void Resume()') @(
                'if(previous!=1) throw new InvalidOperationException("Command suspension was disproved; retain the inspection increment for containment.");',
                'uint restored=ResumeThread(thread);')
        }
        'cached_result_guarded_but_historical' {
            Require-CommandSequence (Get-CommandBlock $c 'public NativeCommandResult PollResult()') 'SuccessState(); if(completedResult!=null) return completedResult;'
            Require-CommandSequence (Get-CommandBlock $c 'private void SuccessState()') 'if(disposed || releaseStarted || containmentStarted)'
            Require-CommandSequence (Get-CommandBlock $c 'private void SuccessState()') 'if(failures.Count!=0) throw new AggregateException(failures.ToArray());'
        }
        'pending_success_paths_recheck_deadline' {
            $fn=Get-CommandBlock $c 'public NativeCommandResult PollResult()'
            Require-CommandSequence $fn 'uint wait=RootWait(); SuccessBoundary(); if(wait==258) return null;'
            Require-CommandSequence $fn 'SuccessBoundary(); if(!treeClosed) return null;'
        }
        'success_requires_actual_exit_time_and_zero' {
            $fn=Get-CommandBlock $c 'private void RequireSuccessfulRootExit()'
            Require-CommandSequence $fn 'if(RootWait()!=0 || GetProcessId(process)!=(uint)pid)'
            Require-CommandSequence $fn 'if(birth<=0 || created!=birth || exited<birth || exited==0)'
            Require-CommandSequence $fn 'if(code!=0)'
            Require-CommandSequence (Get-CommandBlock $c 'public NativeCommandResult PollResult()') 'RequireSuccessfulRootExit(); SuccessBoundary();' 2
        }
        'command_tree_eof_and_join_required' {
            $fn=Get-CommandBlock $c 'private bool CollectorsClosed()'
            Require-CommandSequence $fn 'if((output!=null && !stdoutEof) || (error!=null && !stderrEof)) return false;'
            Require-CommandSequence $fn '!outputThread.Join(0)'
            Require-CommandSequence $fn '!errorThread.Join(0)'
            Require-CommandSequence (Get-CommandBlock $c 'public NativeCommandResult PollResult()') 'AccountingFor(commandJob,"Final command D accounting").active!=0 || !CollectorsClosed()'
        }
        'decoded_result_publication_after_last_deadline' {
            Require-CommandSequence (Get-CommandBlock $c 'public NativeCommandResult PollResult()') 'SuccessBoundary(); completedResult=candidate; closureProven=true; return completedResult;'
        }
        'containment_one_separate_fixed_cleanup_clock' {
            Require-CommandSequence $c 'containmentClock=Stopwatch.StartNew();' 1
            Require-CommandSequence (Get-CommandBlock $c 'public bool PollContainment()') 'containmentClock=Stopwatch.StartNew(); containmentStarted=true;'
            Require-CommandSequence (Get-CommandBlock $c 'private void ContainmentBoundary()') 'containmentClock.ElapsedMilliseconds>=30000'
        }
        'containment_native_identity_without_fabricated_birth' {
            $fn=Get-CommandBlock $c 'private bool ContainmentRootClosed()'
            Require-CommandSequence $fn 'GetProcessId(process)!=(uint)pid'
            Require-CommandSequence $fn 'if(created<=0 || exited<created || exited==0 || (birth!=0 && created!=birth))'
            Require-CommandSequence $fn 'birth=created' 0
        }
        'containment_pending_and_final_budget_rechecks' {
            $fn=Get-CommandBlock $c 'public bool PollContainment()'
            Require-CommandSequence $fn 'ContainmentBoundary(); if(!closed) return false;' 2
            Require-CommandSequence $fn 'ContainmentBoundary(); closureProven=true; return true;'
        }
        'release_refuses_unproved_closure_and_latches_first' {
            Require-CommandOrder (Get-CommandBlock $c 'public void Dispose()') @('if(!closureProven || !CollectorsClosed())','releaseStarted=true;','if(input!=null && !inputDisposed)')
        }
        'close_failure_keeps_handle_and_registration_retryable' {
            Require-CommandSequence (Get-CommandBlock $c 'private static void CloseOwned(') 'Check(CloseHandle(handle),"Release proved-closed command handle"); handle=IntPtr.Zero;'
            Require-CommandOrder (Get-CommandBlock $c 'public void Dispose()') @('CloseOwned(ref thread); CloseOwned(ref process); CloseOwned(ref commandJob);','session.ReleaseCommand(this);','disposed=true;')
        }
        'result_readonly_and_not_session_authority' {
            $r=Get-CommandBlock $tokens 'public sealed class NativeCommandResult'
            Require-CommandSequence $r 'public readonly int ExitCode, ProcessId;'
            Require-CommandSequence $r 'public readonly string StartedAt, Stdout, Stderr;'
            Require-CommandSequence $r 'public readonly bool SessionTreeEmptyProven=false, ContainmentAccepted=false, RetirementPermitted=false;'
        }
        'all_command_lifecycle_operations_nonconcurrent' {
            foreach($signature in @('public void StartSuspended()','public void Resume()','public NativeCommandResult PollResult()','public bool PollContainment()','public void Dispose()')){
                $fn=Get-CommandBlock $c $signature
                Require-CommandSequence $fn 'Enter(); try'
                Require-CommandSequence $fn 'finally { Leave(); }'
            }
            Require-CommandSequence (Get-CommandBlock $c 'private void Enter()') 'Interlocked.CompareExchange(ref operation,1,0)!=0'
        }
        default {throw 'COMMAND source contract: Unknown predicate.'}
    }
}
$rows=@(
    @('clock_precedes_validation_and_registration','Stopwatch clock=Stopwatch.StartNew();','Stopwatch clock=new Stopwatch();'),
    @('prepare_fixed_budget_and_capture_bounds','timeoutMilliseconds!=15000 && timeoutMilliseconds!=25000','timeoutMilliseconds<1'),
    @('registration_is_last_factory_operation','command.sessionJob=session.RegisterCommand(command);','command.sessionJob=IntPtr.Zero;'),
    @('managed_constructor_has_no_native_launch','this.session=session; this.executable=executable;','this.session=session; CreateJobObject(IntPtr.Zero,null); this.executable=executable;'),
    @('frozen_quoted_argument_and_path_bounds','Append(NativeChild.Quote(argument))','Append(argument)'),
    @('start_single_use_before_native_work','startAttempted=true; SuccessBoundary();','SuccessBoundary();'),
    @('original_executable_handle_hash_and_metadata','Files.RequireSingleLink(heldExecutable);',''),
    @('private_d_kill_on_close_before_birth','limits.basic.flags=0x2000;','limits.basic.flags=0x2800;'),
    @('two_attributes_stdio_exact_three','IntPtr.Size*3),IntPtr.Zero,IntPtr.Zero),"Command stdio-only inheritance"','IntPtr.Size*4),IntPtr.Zero,IntPtr.Zero),"Command stdio-only inheritance"'),
    @('birth_job_list_outer_c_then_own_d','Marshal.WriteIntPtr(jobs,0,sessionJob); Marshal.WriteIntPtr(jobs,IntPtr.Size,commandJob);','Marshal.WriteIntPtr(jobs,0,commandJob); Marshal.WriteIntPtr(jobs,IntPtr.Size,sessionJob);'),
    @('creation_exact_suspended_no_breakaway_or_fallback','true,0x08080004,','true,0x09080004,'),
    @('birth_backing_buffers_outlive_attributes','if(initialized) DeleteProcThreadAttributeList(attributes);',''),
    @('actual_root_identity_and_both_memberships','IsProcessInJob(process,sessionJob,out member)','IsProcessInJob(process,commandJob,out member)'),
    @('suspended_one_process_requirement_uses_d_not_c','AccountingFor(commandJob,"Suspended command D accounting")','AccountingFor(sessionJob,"Suspended command D accounting")'),
    @('partial_start_failure_remains_registered','catch(Exception error) { Record(error); throw; }','catch(Exception error) { Dispose(); throw; }'),
    @('collectors_retry_only_unstarted_owned_threads','if((outputThread.ThreadState & System.Threading.ThreadState.Unstarted)!=0) outputThread.Start();','outputThread.Start();'),
    @('collector_fixed_storage_and_any_stderr_fault','bad=stderr || destination.Length+count>maximumBytes;','bad=destination.Length+count>maximumBytes;'),
    @('failure_never_terminates_or_closes_session_c','TerminateJobObject(commandJob,200)','TerminateJobObject(sessionJob,200)'),
    @('resume_attempt_latched_before_actual_resume','SuccessBoundary(); resumeAttempted=true;','SuccessBoundary();'),
    @('disproved_suspend_never_compensated','if(previous!=1) throw new InvalidOperationException("Command suspension was disproved; retain the inspection increment for containment.");',''),
    @('cached_result_guarded_but_historical','if(disposed || releaseStarted || containmentStarted)','if(disposed || containmentStarted)'),
    @('pending_success_paths_recheck_deadline','uint wait=RootWait(); SuccessBoundary(); if(wait==258) return null;','uint wait=RootWait(); if(wait==258) return null;'),
    @('success_requires_actual_exit_time_and_zero','birth<=0 || created!=birth || exited<birth || exited==0','birth<=0 || created!=birth'),
    @('command_tree_eof_and_join_required','!outputThread.Join(0)','false'),
    @('decoded_result_publication_after_last_deadline','SuccessBoundary(); completedResult=candidate;','completedResult=candidate;'),
    @('containment_one_separate_fixed_cleanup_clock','containmentClock.ElapsedMilliseconds>=30000','containmentClock.ElapsedMilliseconds>=60000'),
    @('containment_native_identity_without_fabricated_birth','created<=0 || exited<created || exited==0 || (birth!=0 && created!=birth)','created<=0 || exited<created || exited==0'),
    @('containment_pending_and_final_budget_rechecks','ContainmentBoundary(); if(!closed) return false;','if(!closed) return false;'),
    @('release_refuses_unproved_closure_and_latches_first','releaseStarted=true;',''),
    @('close_failure_keeps_handle_and_registration_retryable','Check(CloseHandle(handle),"Release proved-closed command handle"); handle=IntPtr.Zero;','handle=IntPtr.Zero;'),
    @('result_readonly_and_not_session_authority','public readonly bool SessionTreeEmptyProven=false, ContainmentAccepted=false, RetirementPermitted=false;','public readonly bool SessionTreeEmptyProven=true, ContainmentAccepted=false, RetirementPermitted=false;'),
    @('all_command_lifecycle_operations_nonconcurrent','Interlocked.CompareExchange(ref operation,1,0)!=0','operation!=0')
)
$results=[Collections.Generic.List[object]]::new()
foreach($row in $rows){
    Assert-NativeCommandContract $row[0] $source
    $results.Add([pscustomobject]@{name=$row[0];negative_control=$false;passed=$true})
    Assert-CommandSource ($source.Contains($row[1])) ('Missing mutation target: '+$row[0])
    $failure=$null
    try{Assert-NativeCommandContract $row[0] $source.Replace($row[1],$row[2])}catch{$failure=$_.Exception.Message}
    Assert-CommandSource ($null -ne $failure -and $failure.StartsWith('COMMAND source contract:')) ('Negative survived: '+$row[0])
    $results.Add([pscustomobject]@{name=($row[0]+' mutation rejected');negative_control=$true;passed=$true})
}
$expectedCases=64
Assert-CommandSource ($results.Count -eq $expectedCases) 'Fixed case count differs.'
[pscustomobject]@{suite='native-command-source-contracts';expected=$expectedCases;executed=$results.Count;passed=$results.Count;failed=0
    ps_version=$PSVersionTable.PSVersion.ToString();ps_edition=$PSVersionTable.PSEdition;runtime_sha256_at_read=$sourceHash
    runtime_compiled=$false;runtime_members_invoked=$false;native_executed=$false;pipes_or_threads_executed=$false
    semantic_csharp_ast=$false;native_command_qualified=$false;session_takeover_qualified=$false;full_protocol_accepted=$false
    results=@($results.ToArray())}|ConvertTo-Json -Depth 5 -Compress
