[CmdletBinding()]
param([string] $RuntimePath)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if (-not $RuntimePath) { $RuntimePath=Join-Path $PSScriptRoot '..\Stratus.ParentStage.Runtime.cs' }
# Source tokens and in-memory mutation negatives only. The target C# is never
# compiled, imported, evaluated or invoked. No native/process/pipe/Job experiment.
# Lexical predicates are not semantic C# or completed C/D/G integration evidence.
$bytes=[IO.File]::ReadAllBytes([IO.Path]::GetFullPath($RuntimePath))
$source=[Text.UTF8Encoding]::new($false,$true).GetString($bytes)
$sha=[Security.Cryptography.SHA256]::Create()
try { $sourceHash=[BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
function Assert-SessionSource {
    param([bool] $Okay,[string] $Message)
    if (-not $Okay) { throw ('SESSION source contract: '+$Message) }
}
function Get-SessionTokens {
    param([string] $Text)
    $pattern=@'
//[^\r\n]* | /\*.*?\*/ | @"(?:""|[^"])*" | "(?:\\.|[^"\\])*" | '(?:\\.|[^'\\])*'
| [A-Za-z_][A-Za-z_0-9]* | [0-9]+ | == | != | >= | <= | \+\+ | && | \|\| | => | [^\s]
'@
    $result=[Collections.Generic.List[string]]::new()
    foreach($match in [regex]::Matches($Text,$pattern,[Text.RegularExpressions.RegexOptions]::Singleline -bor [Text.RegularExpressions.RegexOptions]::IgnorePatternWhitespace)) {
        if (-not $match.Value.StartsWith('//') -and -not $match.Value.StartsWith('/*')) { $result.Add($match.Value) }
    }
    return ,$result.ToArray()
}
function Find-SessionSequence {
    param([string[]] $Tokens,[string] $Sequence)
    $wanted=Get-SessionTokens $Sequence
    Assert-SessionSource ($wanted.Count -gt 0) 'Empty predicate.'
    $found=[Collections.Generic.List[int]]::new()
    for($i=0;$i -le $Tokens.Count-$wanted.Count;$i++) {
        $same=$true
        for($j=0;$j -lt $wanted.Count;$j++) { if($Tokens[$i+$j] -cne $wanted[$j]) { $same=$false;break } }
        if($same) { $found.Add($i) }
    }
    return ,$found.ToArray()
}
function Require-SessionSequence {
    param([string[]] $Tokens,[string] $Sequence,[int] $Count=1)
    Assert-SessionSource ((Find-SessionSequence $Tokens $Sequence).Count -eq $Count) ('Sequence differs: '+$Sequence)
}
function Get-SessionBlock {
    param([string[]] $Tokens,[string] $Signature)
    $matches=Find-SessionSequence $Tokens $Signature
    Assert-SessionSource ($matches.Count -eq 1) ('Missing/duplicate block: '+$Signature)
    $start=$matches[0];$open=$start
    while($open -lt $Tokens.Count -and $Tokens[$open] -cne '{') { $open++ }
    Assert-SessionSource ($open -lt $Tokens.Count) 'Opening brace missing.'
    $depth=0
    for($i=$open;$i -lt $Tokens.Count;$i++) {
        if($Tokens[$i] -ceq '{') { $depth++ } elseif($Tokens[$i] -ceq '}') { $depth-- }
        if($depth -eq 0) { return ,([string[]]$Tokens[$start..$i]) }
    }
    throw 'SESSION source contract: Unbalanced block.'
}
function Require-SessionOrder {
    param([string[]] $Tokens,[string[]] $Sequences)
    $previous=-1
    foreach($sequence in $Sequences) {
        $found=Find-SessionSequence $Tokens $sequence
        Assert-SessionSource ($found.Count -eq 1 -and $found[0] -gt $previous) ('Order differs: '+$sequence)
        $previous=$found[0]
    }
}
function Assert-CommandSessionContract {
    param([string] $Name,[string] $Text)
    $tokens=Get-SessionTokens $Text
    $owner=Get-SessionBlock $tokens 'internal sealed class CommandSessionOwner : IDisposable'
    $client=Get-SessionBlock $tokens 'public sealed class CommandSessionClient : IDisposable'
    $job=Get-SessionBlock $tokens 'internal static class CommandJobNative'
    $transition=Get-SessionBlock $tokens 'public sealed class IndependentTransitionLaunch : IDisposable'
    switch($Name) {
        'creator_is_constructor_bound_readonly' {
            Require-SessionSequence $owner 'private readonly IndependentTransitionLaunch creator;'
            Require-SessionSequence $owner 'creator=originalCreator;' 1
            Require-SessionSequence (Get-SessionBlock $owner 'internal CommandSessionOwner(') 'if(originalCreator==null) throw new ArgumentNullException("originalCreator"); creator=originalCreator;'
        }
        'initialization_latches_before_native_work' {
            Require-SessionOrder (Get-SessionBlock $owner 'internal void Initialize()') @('if(disposed || initializeAttempted || retirementStarted)','initializeAttempted=true;','job=CommandJobNative.CreateJobObject(IntPtr.Zero,null);')
        }
        'fresh_private_job_exact_kill_limit' {
            Require-SessionOrder (Get-SessionBlock $owner 'internal void Initialize()') @('job=CommandJobNative.CreateJobObject(IntPtr.Zero,null);','CommandJobNative.Check(job!=IntPtr.Zero,"Create private command session Job");','limits.basic.flags=0x2000;','CommandJobNative.Check(CommandJobNative.SetInformationJobObject(job,9,ref limits,(uint)Marshal.SizeOf(limits)),"Command session kill-on-close");','CommandJobNative.RequireCommandJob(job); initialized=true;')
        }
        'required_job_is_real_exact_limits' {
            $fn=Get-SessionBlock $job 'internal static void RequireCommandJob('
            Require-SessionSequence $fn 'if(handle==IntPtr.Zero || handle==new IntPtr(-1))'
            Require-SessionSequence $fn 'Check(QueryLimits(handle,9,out limits,(uint)Marshal.SizeOf(typeof(ExtendedLimit)),IntPtr.Zero),"Command Job limits");'
            Require-SessionSequence $fn 'if(limits.basic.flags!=0x2000)'
        }
        'admission_export_is_single_use' {
            Require-SessionOrder (Get-SessionBlock $owner 'internal IntPtr DuplicateAdmissionForLaunch()') @('if(disposed || !initialized || admissionAttempted || retirementStarted)','admissionAttempted=true;','bool copied=CommandJobNative.DuplicateHandle(','launchCopy=copy; admissionSucceeded=copied; admissionReturned=true;','admissionIssued=true; return launchCopy;')
        }
        'restricted_duplicate_exact_rights_no_same_access' {
            Require-SessionSequence (Get-SessionBlock $owner 'internal IntPtr DuplicateAdmissionForLaunch()') 'bool copied=CommandJobNative.DuplicateHandle(CommandJobNative.GetCurrentProcess(),job, CommandJobNative.GetCurrentProcess(),out copy,0x0005,true,0); launchCopy=copy; admissionSucceeded=copied; admissionReturned=true; CommandJobNative.Check(copied,"Duplicate restricted command admission");'
        }
        'session_has_no_name_or_pid_reopen' {
            foreach($block in @($owner,$client,$job)) { foreach($name in @('OpenJobObject','OpenProcess','DUPLICATE_SAME_ACCESS')) { Require-SessionSequence $block $name 0 } }
        }
        'bind_has_no_replacement_argument' {
            $fn=Get-SessionBlock $owner 'internal void BindCreator()'
            Require-SessionSequence $fn 'if(disposed || !initialized || !admissionIssued || launchCopy==IntPtr.Zero || bindingAttempted)'
            Require-SessionSequence $fn 'bindingAttempted=true; creator.AssertSuspended();'
            Require-SessionSequence $fn 'creator=' 0
        }
        'launch_copy_close_is_checked_before_forgetting' {
            Require-SessionSequence (Get-SessionBlock $owner 'internal void CloseLaunchCopy()') 'CommandJobNative.Check(CommandJobNative.CloseHandle(launchCopy),"Close parent command admission copy"); launchCopy=IntPtr.Zero;'
        }
        'retirement_requires_bound_creator_and_closed_export' {
            Require-SessionSequence (Get-SessionBlock $owner 'internal void AssertCreatorBoundForResume()') 'if(disposed || !initialized || !admissionIssued || !bindingSucceeded || launchCopy!=IntPtr.Zero || retirementStarted)'
            Require-SessionOrder (Get-SessionBlock $owner 'internal bool PollRetirement()') @('IndependentTransitionLaunch.CommandCreatorDisposition disposition=creator.GetCommandCreatorDisposition();','if(!initialized || !admissionIssued)','creator.AssertCommandCreatorTerminated(); AssertRetirementDeadline(); CloseLaunchCopy(); creator.AssertCommandCreatorTerminated(); AssertRetirementDeadline();','CommandJobNative.TerminateJobObject(job,201)')
        }
        'physical_death_precedes_whole_C_termination' {
            $fn=Get-SessionBlock $owner 'internal bool PollRetirement()'
            Require-SessionOrder $fn @('creator.AssertCommandCreatorTerminated(); AssertRetirementDeadline(); CloseLaunchCopy(); creator.AssertCommandCreatorTerminated(); AssertRetirementDeadline();','if(!retired) { CommandJobNative.Check(CommandJobNative.TerminateJobObject(job,201),"Retired creator command session termination");')
        }
        'retirement_clock_starts_once_before_native_death_query' {
            Require-SessionSequence $owner 'retirementClock=Stopwatch.StartNew();' 1
            Require-SessionSequence (Get-SessionBlock $owner 'internal bool PollRetirement()') 'if(!retirementStarted) { retirementStarted=true; retirementClock=Stopwatch.StartNew(); } AssertRetirementDeadline(); IndependentTransitionLaunch.CommandCreatorDisposition disposition=creator.GetCommandCreatorDisposition();'
            Require-SessionSequence $owner 'Restart(' 0
        }
        'retirement_deadline_exact_thirty_seconds' {
            Require-SessionSequence (Get-SessionBlock $owner 'private void AssertRetirementDeadline()') 'if(retirementClock==null || retirementClock.ElapsedMilliseconds>=30000)'
        }
        'retirement_clock_cannot_be_reset_or_rebound' {
            Require-SessionSequence $owner 'retirementClock=' 1
            foreach($name in @('Reset(','Restart(','Stop(','Start(')) { Require-SessionSequence $owner $name 0 }
        }
        'retirement_fresh_accounting_and_final_death' {
            Require-SessionSequence (Get-SessionBlock $owner 'internal bool PollRetirement()') 'CommandJobNative.Check(CommandJobNative.QueryInformationJobObject(job,1,out accounting, (uint)Marshal.SizeOf(typeof(CommandJobNative.Accounting)),IntPtr.Zero),"Command session descendant accounting"); creator.AssertCommandCreatorTerminated(); AssertRetirementDeadline(); if(accounting.active!=0) return false; retired=true; return true;'
        }
        'retirement_errors_do_not_release_handles' {
            $fn=Get-SessionBlock $owner 'internal bool PollRetirement()'
            foreach($name in @('CloseHandle','Dispose','catch','job=IntPtr.Zero','creator=null')) { Require-SessionSequence $fn $name 0 }
        }
        'unbound_or_unretired_export_cannot_dispose' {
            $fn=Get-SessionBlock $owner 'public void Dispose()'
            Require-SessionSequence $fn 'if(launchCopy!=IntPtr.Zero) throw new InvalidOperationException("Outstanding command admission copy must remain retained.");'
            Require-SessionSequence $fn 'if(!retired || (initializeAttempted && !jobCreationReturned) || (admissionAttempted && !admissionReturned)) throw new InvalidOperationException("Unretired or ambiguous command session cannot be disposed.");'
            Require-SessionOrder $fn @('AssertAdmissionOutcomeKnown();','IndependentTransitionLaunch.CommandCreatorDisposition disposition=creator.GetCommandCreatorDisposition();','if(disposition==IndependentTransitionLaunch.CommandCreatorDisposition.NeverCreated) creator.AssertNoChildCreated(); else creator.AssertCommandCreatorTerminated(); if(job!=IntPtr.Zero)')
        }
        'owner_dispose_rechecks_death_and_empty' {
            Require-SessionSequence (Get-SessionBlock $owner 'public void Dispose()') 'if(disposition==IndependentTransitionLaunch.CommandCreatorDisposition.NeverCreated) creator.AssertNoChildCreated(); else creator.AssertCommandCreatorTerminated(); if(job!=IntPtr.Zero) { CommandJobNative.Accounting accounting; CommandJobNative.Check(CommandJobNative.QueryInformationJobObject(job,1,out accounting, (uint)Marshal.SizeOf(typeof(CommandJobNative.Accounting)),IntPtr.Zero),"Final command session accounting"); if(accounting.active!=0) throw new InvalidOperationException("Command session still has descendants; handles retained."); if(disposition==IndependentTransitionLaunch.CommandCreatorDisposition.NeverCreated) creator.AssertNoChildCreated(); else creator.AssertCommandCreatorTerminated();'
        }
        'owner_close_checked_before_disposed_latch' {
            Require-SessionSequence (Get-SessionBlock $owner 'public void Dispose()') 'CommandJobNative.Check(CommandJobNative.CloseHandle(job),"Close original command session Job"); job=IntPtr.Zero; } disposed=true;'
        }
        'adoption_canonical_handle_and_width' {
            Require-SessionSequence (Get-SessionBlock $client 'public static CommandSessionClient AdoptInherited(') 'if(String.IsNullOrEmpty(inheritedHandle) || !UInt64.TryParse(inheritedHandle,NumberStyles.None,CultureInfo.InvariantCulture,out number) || number==0 || number.ToString(CultureInfo.InvariantCulture)!=inheritedHandle || (IntPtr.Size==4 ? number>=UInt32.MaxValue : number>=Int64.MaxValue))'
        }
        'adoption_requires_native_inheritable_flag' {
            Require-SessionSequence (Get-SessionBlock $client 'public static CommandSessionClient AdoptInherited(') 'CommandJobNative.Check(CommandJobNative.GetHandleInformation(handle,out flags),"Inherited command admission flags"); if((flags & 1)==0) throw new InvalidDataException("Command admission is not an inherited handle.");'
        }
        'adoption_clears_and_rechecks_before_return' {
            Require-SessionSequence (Get-SessionBlock $client 'public static CommandSessionClient AdoptInherited(') 'CommandJobNative.Check(CommandJobNative.SetHandleInformation(handle,1,0),"Clear command admission inheritance"); CommandJobNative.Check(CommandJobNative.GetHandleInformation(handle,out flags),"Recheck command admission flags"); if((flags & 1)!=0) throw new InvalidDataException("Command admission remained inheritable."); CommandJobNative.RequireCommandJob(handle); return new CommandSessionClient(handle);'
        }
        'client_cannot_create_or_terminate_C' {
            foreach($name in @('CreateJobObject','SetInformationJobObject','TerminateJobObject','DuplicateHandle')) { Require-SessionSequence $client $name 0 }
        }
        'registration_is_closed_managed_only_body' {
            $fn=Get-SessionBlock $client 'internal IntPtr RegisterCommand('
            $expected='internal IntPtr RegisterCommand(NativeCommand command) { lock(stateGate) { if(disposed || job==IntPtr.Zero || command==null || activeCommand!=null) throw new InvalidOperationException("Command admission requires a live idle session."); activeCommand=command; return job; } }'
            Require-SessionSequence $fn $expected
            Assert-SessionSource ($fn.Count -eq (Get-SessionTokens $expected).Count) 'Register body is not exact managed-only registration.'
        }
        'registration_retains_exact_slot_before_return' {
            Require-SessionSequence (Get-SessionBlock $client 'internal IntPtr RegisterCommand(') 'activeCommand=command; return job;'
        }
        'release_requires_same_command_reference' {
            Require-SessionSequence (Get-SessionBlock $client 'internal void ReleaseCommand(') 'if(disposed || command==null || !Object.ReferenceEquals(activeCommand,command))'
        }
        'release_is_closed_managed_only_body' {
            $fn=Get-SessionBlock $client 'internal void ReleaseCommand('
            $expected='internal void ReleaseCommand(NativeCommand command) { lock(stateGate) { if(disposed || command==null || !Object.ReferenceEquals(activeCommand,command)) throw new InvalidOperationException("Only the registered native command may release its slot."); activeCommand=null; } }'
            Require-SessionSequence $fn $expected
            Assert-SessionSource ($fn.Count -eq (Get-SessionTokens $expected).Count) 'Release body is not exact managed-only release.'
        }
        'active_slot_is_observable_under_lock' {
            Require-SessionSequence $client 'public NativeCommand ActiveCommand { get { lock(stateGate) { return activeCommand; } } }'
        }
        'client_dispose_rejects_active_before_close' {
            Require-SessionOrder (Get-SessionBlock $client 'public void Dispose()') @('if(activeCommand!=null) throw new InvalidOperationException("Active or unresolved native command must remain retained.");','CommandJobNative.Check(CommandJobNative.CloseHandle(job),"Close adopted command session handle");')
        }
        'client_close_is_checked_before_forgetting' {
            Require-SessionSequence (Get-SessionBlock $client 'public void Dispose()') 'CommandJobNative.Check(CommandJobNative.CloseHandle(job),"Close adopted command session handle"); job=IntPtr.Zero; disposed=true;'
        }
        'creator_proof_requires_original_live_capability' {
            Require-SessionSequence (Get-SessionBlock $transition 'internal void AssertCommandCreatorTerminated()') 'if(disposed || process==IntPtr.Zero || !identityEstablished)'
        }
        'creator_proof_waits_exact_original_handle' {
            Require-SessionSequence (Get-SessionBlock $transition 'internal void AssertCommandCreatorTerminated()') 'IntPtr original=process; uint wait=WaitForSingleObject(original,0); Check(wait!=UInt32.MaxValue,"Command creator native wait"); if(wait!=0) throw new InvalidOperationException("Original command creator is not physically terminated.");'
        }
        'creator_proof_binds_native_birth_and_PID' {
            Require-SessionSequence (Get-SessionBlock $transition 'internal void AssertCommandCreatorTerminated()') 'Check(GetProcessTimes(original,out born,out exited,out kernel,out user),"Command creator native times"); if(GetProcessId(original)!=(uint)ProcessId || born<=0 || exited<born || exited<=0 || new DateTimeOffset(DateTime.FromFileTimeUtc(born)).ToString("o")!=StartedAt)'
        }
        'creator_proof_rechecks_same_handle_and_death' {
            Require-SessionSequence (Get-SessionBlock $transition 'internal void AssertCommandCreatorTerminated()') 'uint after=WaitForSingleObject(original,0); Check(after!=UInt32.MaxValue,"Command creator native death recheck"); if(disposed || process!=original || after!=0)'
        }
        'creator_proof_never_uses_cached_or_json_authority' {
            $fn=Get-SessionBlock $transition 'internal void AssertCommandCreatorTerminated()'
            foreach($name in @('rootTerminationObservation','terminalProof','OpenProcess','Process.GetProcessById','AssertClock','ThrowFaults')) { Require-SessionSequence $fn $name 0 }
        }
        'factory_registers_created_object_at_return_tail' {
            $command=Get-SessionBlock $tokens 'public sealed class NativeCommand : IDisposable'
            $fn=Get-SessionBlock $command 'public static NativeCommand Prepare('
            Require-SessionSequence $fn 'RegisterCommand(' 1
            Require-SessionSequence $fn 'NativeCommand command=new NativeCommand(session,executable,expectedHash,heldExecutable,line.ToString(),directory, timeoutMilliseconds,maximumBytes,clock); command.sessionJob=session.RegisterCommand(command); return command; }'
        }
        'factory_prepare_does_not_start_native_work' {
            $command=Get-SessionBlock $tokens 'public sealed class NativeCommand : IDisposable'
            $fn=Get-SessionBlock $command 'public static NativeCommand Prepare('
            Require-SessionOrder $fn @('Stopwatch clock=Stopwatch.StartNew();','NativeCommand command=new NativeCommand(','command.sessionJob=session.RegisterCommand(command);')
            foreach($name in @('StartSuspended','HeldExecutableBoundary','Files','CommandJobNative','CreateJobObject','CreateProcess','File.Open','SafeFileHandle','Thread','new AnonymousPipeServerStream','SuccessBoundary')) { Require-SessionSequence $fn $name 0 }
        }
        'command_constructor_is_managed_field_retention_only' {
            $command=Get-SessionBlock $tokens 'public sealed class NativeCommand : IDisposable'
            $fn=Get-SessionBlock $command 'private NativeCommand('
            $expected='private NativeCommand(CommandSessionClient session,string executable,string hash,FileStream heldExecutable, string commandLine,string directory,int timeout,int maximum,Stopwatch clock) { this.session=session; this.executable=executable; expectedHash=hash; this.heldExecutable=heldExecutable; this.commandLine=commandLine; this.directory=directory; timeoutMilliseconds=timeout; maximumBytes=maximum; successClock=clock; }'
            Require-SessionSequence $fn $expected
            Assert-SessionSource ($fn.Count -eq (Get-SessionTokens $expected).Count) 'Command constructor acquired resources before registration.'
        }
        'raw_export_result_retained_before_validation' {
            Require-SessionOrder (Get-SessionBlock $owner 'internal IntPtr DuplicateAdmissionForLaunch()') @('admissionAttempted=true;','bool copied=CommandJobNative.DuplicateHandle(','launchCopy=copy; admissionSucceeded=copied; admissionReturned=true;','CommandJobNative.Check(copied,"Duplicate restricted command admission");','if(launchCopy==IntPtr.Zero || launchCopy==new IntPtr(-1))','admissionIssued=true; return launchCopy;')
        }
        'ambiguous_export_cannot_become_owned_handle' {
            Require-SessionSequence (Get-SessionBlock $owner 'private void AssertAdmissionOutcomeKnown()') 'if(admissionAttempted && (!admissionReturned || (admissionSucceeded && !admissionIssued) || (!admissionSucceeded && launchCopy!=IntPtr.Zero))) throw new InvalidOperationException("Ambiguous command admission export must remain retained without handle operations.");'
        }
        'export_disposition_checked_before_any_copy_close' {
            Require-SessionOrder (Get-SessionBlock $owner 'internal void CloseLaunchCopy()') @('AssertAdmissionOutcomeKnown();','if(launchCopy==IntPtr.Zero) return;','CommandJobNative.CloseHandle(launchCopy)')
        }
        'retirement_rejects_unknown_creation_before_creator_query' {
            Require-SessionOrder (Get-SessionBlock $owner 'internal bool PollRetirement()') @('AssertAdmissionOutcomeKnown();','if(disposed || (initializeAttempted && !jobCreationReturned))','IndependentTransitionLaunch.CommandCreatorDisposition disposition=creator.GetCommandCreatorDisposition();')
        }
        'never_created_requires_all_original_outputs_zero' {
            Require-SessionSequence (Get-SessionBlock $transition 'internal CommandCreatorDisposition GetCommandCreatorDisposition()') 'bool zeroOutputs=creationOutput.process==IntPtr.Zero && creationOutput.thread==IntPtr.Zero && creationOutput.pid==0 && creationOutput.tid==0 && process==IntPtr.Zero && thread==IntPtr.Zero;'
        }
        'never_created_requires_no_resume_and_known_native_failure' {
            Require-SessionSequence (Get-SessionBlock $transition 'internal CommandCreatorDisposition GetCommandCreatorDisposition()') 'if(!resumeAttempted && zeroOutputs && (!createAttempted || (createReturned && !createSucceeded))) return CommandCreatorDisposition.NeverCreated;'
        }
        'materialized_creator_requires_success_and_original_identity' {
            Require-SessionSequence (Get-SessionBlock $transition 'internal CommandCreatorDisposition GetCommandCreatorDisposition()') 'if(createAttempted && createReturned && createSucceeded && identityEstablished && process!=IntPtr.Zero && thread!=IntPtr.Zero && process==creationOutput.process && thread==creationOutput.thread && nativeProcessId==creationOutput.pid && nativeProcessId>0 && creationOutput.tid>0) return CommandCreatorDisposition.RetainedOriginal;'
        }
        'nochild_retirement_rechecks_proof_around_fresh_empty_accounting' {
            $fn=Get-SessionBlock (Get-SessionBlock $owner 'internal bool PollRetirement()') 'if(disposition==IndependentTransitionLaunch.CommandCreatorDisposition.NeverCreated)'
            Require-SessionSequence $fn 'creator.AssertNoChildCreated(); AssertRetirementDeadline(); CloseLaunchCopy(); if(job!=IntPtr.Zero) { CommandJobNative.Accounting empty; CommandJobNative.Check(CommandJobNative.QueryInformationJobObject(job,1,out empty, (uint)Marshal.SizeOf(typeof(CommandJobNative.Accounting)),IntPtr.Zero),"Uninherited command session accounting"); creator.AssertNoChildCreated(); AssertRetirementDeadline(); if(empty.active!=0) return false; } creator.AssertNoChildCreated(); AssertRetirementDeadline(); retired=true; return true;'
            foreach($name in @('TerminateJobObject','AssertCommandCreatorTerminated','CloseHandle','catch')) { Require-SessionSequence $fn $name 0 }
        }
        'resume_requires_checked_binding_not_attempt_only' {
            Require-SessionSequence (Get-SessionBlock $owner 'internal void BindCreator()') 'bindingAttempted=true; creator.AssertSuspended(); bindingSucceeded=true;'
            Require-SessionSequence (Get-SessionBlock $owner 'internal void AssertCreatorBoundForResume()') 'if(disposed || !initialized || !admissionIssued || !bindingSucceeded || launchCopy!=IntPtr.Zero || retirementStarted)'
        }
        'owner_native_mutations_and_retirement_are_locked' {
            foreach($signature in @('internal void Initialize()','internal IntPtr DuplicateAdmissionForLaunch()','internal void BindCreator()','internal void AssertCreatorBoundForResume()','internal void CloseLaunchCopy()','internal bool PollRetirement()','public void Dispose()')) {
                Require-SessionSequence (Get-SessionBlock $owner $signature) ($signature+' { lock(stateGate) {')
            }
        }
        'creator_death_proof_requires_native_success_before_wait' {
            Require-SessionOrder (Get-SessionBlock $transition 'internal void AssertCommandCreatorTerminated()') @('AssertOriginalCreationIdentity();','IntPtr original=process;','uint wait=WaitForSingleObject(original,0);')
            Require-SessionSequence (Get-SessionBlock $transition 'private void AssertOriginalCreationIdentity()') 'if(disposed || !createAttempted || !createReturned || !createSucceeded || !identityEstablished || process==IntPtr.Zero || thread==IntPtr.Zero || process!=creationOutput.process || thread!=creationOutput.thread || nativeProcessId<=0 || nativeProcessId!=creationOutput.pid || creationOutput.tid<=0 || nativeStartedAt==null)'
        }
        'job_native_creation_return_is_recorded_before_validation' {
            Require-SessionSequence (Get-SessionBlock $owner 'internal void Initialize()') 'job=CommandJobNative.CreateJobObject(IntPtr.Zero,null); jobCreationReturned=true; CommandJobNative.Check(job!=IntPtr.Zero,"Create private command session Job");'
        }
        default { throw 'SESSION source contract: Unknown predicate.' }
    }
}
$rows=@(
    @('creator_is_constructor_bound_readonly','private readonly IndependentTransitionLaunch creator;','private IndependentTransitionLaunch creator;'),
    @('initialization_latches_before_native_work','initializeAttempted=true;',''),
    @('fresh_private_job_exact_kill_limit','CommandJobNative.CreateJobObject(IntPtr.Zero,null)','CommandJobNative.CreateJobObject(IntPtr.Zero,"shared")'),
    @('required_job_is_real_exact_limits','if(limits.basic.flags!=0x2000)','if(limits.basic.flags==0)'),
    @('admission_export_is_single_use','if(disposed || !initialized || admissionAttempted || retirementStarted)','if(disposed || !initialized || retirementStarted)'),
    @('restricted_duplicate_exact_rights_no_same_access','out copy,0x0005,true,0)','out copy,0x0005,true,2)'),
    @('session_has_no_name_or_pid_reopen','admissionIssued=true; return launchCopy;','OpenJobObject("name"); admissionIssued=true; return launchCopy;'),
    @('bind_has_no_replacement_argument','internal void BindCreator()','internal void BindCreator(IndependentTransitionLaunch replacement)'),
    @('launch_copy_close_is_checked_before_forgetting','CommandJobNative.Check(CommandJobNative.CloseHandle(launchCopy),"Close parent command admission copy");',''),
    @('retirement_requires_bound_creator_and_closed_export','!admissionIssued || !bindingSucceeded || launchCopy!=IntPtr.Zero','!admissionIssued || launchCopy!=IntPtr.Zero'),
    @('physical_death_precedes_whole_C_termination','creator.AssertCommandCreatorTerminated();',''),
    @('retirement_clock_starts_once_before_native_death_query','if(!retirementStarted) { retirementStarted=true; retirementClock=Stopwatch.StartNew(); }','retirementStarted=true; retirementClock=Stopwatch.StartNew();'),
    @('retirement_deadline_exact_thirty_seconds','retirementClock.ElapsedMilliseconds>=30000','retirementClock.ElapsedMilliseconds>=30001'),
    @('retirement_clock_cannot_be_reset_or_rebound','retirementClock=Stopwatch.StartNew();','retirementClock=Stopwatch.StartNew(); retirementClock.Reset();'),
    @('retirement_fresh_accounting_and_final_death','if(accounting.active!=0) return false;','if(accounting.active>1) return false;'),
    @('retirement_errors_do_not_release_handles','retired=true; return true;','retired=true; CommandJobNative.CloseHandle(job); return true;'),
    @('unbound_or_unretired_export_cannot_dispose','if(!retired || (initializeAttempted && !jobCreationReturned) || (admissionAttempted && !admissionReturned))','if((initializeAttempted && !jobCreationReturned) || (admissionAttempted && !admissionReturned))'),
    @('owner_dispose_rechecks_death_and_empty','if(accounting.active!=0) throw new InvalidOperationException("Command session still has descendants; handles retained.");',''),
    @('owner_close_checked_before_disposed_latch','CommandJobNative.Check(CommandJobNative.CloseHandle(job),"Close original command session Job");',''),
    @('adoption_canonical_handle_and_width','number.ToString(CultureInfo.InvariantCulture)!=inheritedHandle','false'),
    @('adoption_requires_native_inheritable_flag','if((flags & 1)==0) throw new InvalidDataException("Command admission is not an inherited handle.");',''),
    @('adoption_clears_and_rechecks_before_return','CommandJobNative.SetHandleInformation(handle,1,0)','CommandJobNative.SetHandleInformation(handle,1,1)'),
    @('client_cannot_create_or_terminate_C','private CommandSessionClient(IntPtr inherited) { job=inherited; }','private CommandSessionClient(IntPtr inherited) { job=inherited; CommandJobNative.TerminateJobObject(job,1); }'),
    @('registration_is_closed_managed_only_body','activeCommand=command; return job;','activeCommand=command; CommandJobNative.RequireCommandJob(job); return job;'),
    @('registration_retains_exact_slot_before_return','activeCommand=command; return job;','return job;'),
    @('release_requires_same_command_reference','!Object.ReferenceEquals(activeCommand,command)','activeCommand==null'),
    @('release_is_closed_managed_only_body','activeCommand=null;','CommandJobNative.CloseHandle(job); activeCommand=null;'),
    @('active_slot_is_observable_under_lock','public NativeCommand ActiveCommand { get { lock(stateGate) { return activeCommand; } } }','public NativeCommand ActiveCommand { get { return activeCommand; } }'),
    @('client_dispose_rejects_active_before_close','if(activeCommand!=null) throw new InvalidOperationException("Active or unresolved native command must remain retained.");',''),
    @('client_close_is_checked_before_forgetting','CommandJobNative.Check(CommandJobNative.CloseHandle(job),"Close adopted command session handle");',''),
    @('creator_proof_requires_original_live_capability','if(disposed || process==IntPtr.Zero || !identityEstablished)','if(disposed || process==IntPtr.Zero)'),
    @('creator_proof_waits_exact_original_handle','uint wait=WaitForSingleObject(original,0);','uint wait=0;'),
    @('creator_proof_binds_native_birth_and_PID','if(GetProcessId(original)!=(uint)ProcessId || born<=0 || exited<born || exited<=0 ||','if(born<=0 || exited<born || exited<=0 ||'),
    @('creator_proof_rechecks_same_handle_and_death','if(disposed || process!=original || after!=0)','if(disposed || after!=0)'),
    @('creator_proof_never_uses_cached_or_json_authority','internal void AssertCommandCreatorTerminated() {','internal void AssertCommandCreatorTerminated() { if(rootTerminationObservation!=null) return;'),
    @('factory_registers_created_object_at_return_tail','command.sessionJob=session.RegisterCommand(command);','command.sessionJob=session.RegisterCommand(command); command.StartSuspended();'),
    @('factory_prepare_does_not_start_native_work','command.sessionJob=session.RegisterCommand(command);','command.StartSuspended(); command.sessionJob=session.RegisterCommand(command);'),
    @('command_constructor_is_managed_field_retention_only','successClock=clock;','successClock=clock; StartSuspended();'),
    @('raw_export_result_retained_before_validation','launchCopy=copy; admissionSucceeded=copied; admissionReturned=true;','admissionSucceeded=copied; admissionReturned=true;'),
    @('ambiguous_export_cannot_become_owned_handle','(!admissionSucceeded && launchCopy!=IntPtr.Zero)','(!admissionSucceeded && false)'),
    @('export_disposition_checked_before_any_copy_close','AssertAdmissionOutcomeKnown();',''),
    @('retirement_rejects_unknown_creation_before_creator_query','if(disposed || (initializeAttempted && !jobCreationReturned))','if(disposed)'),
    @('never_created_requires_all_original_outputs_zero','creationOutput.pid==0 && creationOutput.tid==0','creationOutput.pid==0'),
    @('never_created_requires_no_resume_and_known_native_failure','(!createAttempted || (createReturned && !createSucceeded))','(!createAttempted || !createSucceeded)'),
    @('materialized_creator_requires_success_and_original_identity','if(createAttempted && createReturned && createSucceeded && identityEstablished &&','if(createAttempted && createReturned && identityEstablished &&'),
    @('nochild_retirement_rechecks_proof_around_fresh_empty_accounting','if(empty.active!=0) return false;','if(empty.active>1) return false;'),
    @('resume_requires_checked_binding_not_attempt_only','bindingAttempted=true; creator.AssertSuspended(); bindingSucceeded=true;','bindingAttempted=true; bindingSucceeded=true; creator.AssertSuspended();'),
    @('owner_native_mutations_and_retirement_are_locked','internal void Initialize() {\n            lock(stateGate) {','internal void Initialize() {\n            {'),
    @('creator_death_proof_requires_native_success_before_wait','AssertOriginalCreationIdentity();',''),
    @('job_native_creation_return_is_recorded_before_validation','jobCreationReturned=true;','')
)
$results=[Collections.Generic.List[object]]::new()
foreach($row in $rows) {
    Assert-CommandSessionContract $row[0] $source
    $results.Add([pscustomobject]@{name=$row[0];negative_control=$false;passed=$true})
    $needle=$row[1].Replace('\n',"`n")
    $normalized=$source.Replace("`r`n","`n")
    Assert-SessionSource ($normalized.Contains($needle)) ('Missing mutation target: '+$row[0])
    $failure=$null
    try { Assert-CommandSessionContract $row[0] $normalized.Replace($needle,$row[2]) } catch { $failure=$_.Exception.Message }
    Assert-SessionSource ($null -ne $failure -and $failure.StartsWith('SESSION source contract:')) ('Negative survived: '+$row[0])
    $results.Add([pscustomobject]@{name=($row[0]+' mutation rejected');negative_control=$true;passed=$true})
}
$expectedCases=100
Assert-SessionSource ($results.Count -eq $expectedCases) 'Fixed case count differs.'
[pscustomobject]@{suite='command-session-source-contracts';expected=$expectedCases;executed=$results.Count;passed=$results.Count;failed=0
    ps_version=$PSVersionTable.PSVersion.ToString();ps_edition=$PSVersionTable.PSEdition;runtime_sha256_at_read=$sourceHash
    native_executed=$false;runtime_compiled=$false;runtime_members_invoked=$false;pipes_or_threads_executed=$false
    semantic_csharp_ast=$false;capability_provenance_qualified=$false;restricted_JOB_LIST_rights_qualified=$false
    creator_disposal_order_integrated=$false;command_factory_integration_qualified=$false;full_protocol_accepted=$false
    results=@($results.ToArray())} | ConvertTo-Json -Depth 5 -Compress
