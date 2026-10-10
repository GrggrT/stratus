param()
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
# Fixed source data only. Never compile, load, evaluate or invoke a target.
# All callbacks below are literal test predicates; mutations remain token data.
# This suite cannot qualify native continuation, timing or ownership.
function Assert-Continuation {param([bool]$Okay,[string]$Message) if(-not $Okay){throw ('Producer continuation source contract: '+$Message)}}
function Read-ContinuationSource {
    param([string]$Path)
    $full=[IO.Path]::GetFullPath($Path)
    # Bound allocation and every delivered read; a FileInfo precheck alone
    # cannot stop ReadAllBytes from allocating after concurrent file growth.
    $stream=[IO.FileStream]::new($full,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{
        $length=$stream.Length
        Assert-Continuation ($length -gt 0 -and $length -le 1048576) 'Inspection file exceeds one MiB.'
        $raw=[byte[]]::new([int]$length);$offset=0
        while($offset -lt $raw.Length){
            $count=$stream.Read($raw,$offset,$raw.Length-$offset)
            Assert-Continuation ($count -gt 0) 'Inspection file ended before its bounded length.'
            $offset+=$count
        }
        Assert-Continuation ($stream.ReadByte() -eq -1 -and $stream.Length -eq $length) 'File length changed while reading.'
    }finally{$stream.Dispose()}
    $source=[Text.UTF8Encoding]::new($false,$true).GetString($raw)
    $hash=[Security.Cryptography.SHA256]::Create()
    try{$digest=[BitConverter]::ToString($hash.ComputeHash($raw)).Replace('-','').ToLowerInvariant()}finally{$hash.Dispose()}
    return [pscustomobject]@{path=$full;text=$source;sha256=$digest}
}
function Tokens {
    param([string]$Text)
    Assert-Continuation ($null -ne $Text -and $Text.Length -le 1048576) 'Token input exceeds its fixed character bound.'
    $pattern=@'
//[^\r\n]* | /\*.*?\*/ | @"(?:""|[^"])*" | "(?:\\.|[^"\\])*" | '(?:\\.|[^'\\])*'
| [A-Za-z_][A-Za-z_0-9]* | [0-9]+ | == | != | >= | <= | \+\+ | -- | && | \|\| | => | [^\s]
'@
    $result=[Collections.Generic.List[string]]::new()
    $options=[Text.RegularExpressions.RegexOptions]::Singleline -bor [Text.RegularExpressions.RegexOptions]::IgnorePatternWhitespace
    foreach($m in [regex]::Matches($Text,$pattern,$options,[TimeSpan]::FromSeconds(5))){
        if(-not $m.Value.StartsWith('//') -and -not $m.Value.StartsWith('/*')){Assert-Continuation ($result.Count -lt 262144) 'Token inspection bound exceeded.';$result.Add($m.Value)}
    }
    Assert-Continuation ($result.Count -le 262144) 'Token inspection bound exceeded.'
    return ,$result.ToArray()
}
function Find {
    param([string[]]$Source,[string]$Sequence)
    $wanted=Tokens $Sequence;Assert-Continuation ($wanted.Count -gt 0) 'Empty predicate.'
    $found=[Collections.Generic.List[int]]::new()
    for($i=0;$i -le $Source.Count-$wanted.Count;$i++){
        $same=$true
        for($j=0;$j -lt $wanted.Count;$j++){if($Source[$i+$j] -cne $wanted[$j]){$same=$false;break}}
        if($same){$found.Add($i)}
    }
    return ,$found.ToArray()
}
function Has {param([string[]]$Source,[string]$Sequence,[int]$Count=1) Assert-Continuation ((Find $Source $Sequence).Count -eq $Count) ('Sequence differs: '+$Sequence)}
function Order {
    param([string[]]$Source,[string[]]$Sequences)
    $last=-1
    foreach($sequence in $Sequences){$found=Find $Source $sequence;Assert-Continuation ($found.Count -eq 1 -and $found[0] -gt $last) ('Order differs: '+$sequence);$last=$found[0]}
}
function Block {
    param([string[]]$Source,[string]$Signature,[switch]$Direct)
    $found=Find $Source $Signature
    if($Direct){
        $selected=[Collections.Generic.List[int]]::new();$depth=0
        for($i=0;$i -lt $Source.Count;$i++){
            if($depth -eq 1 -and $i -in $found){$selected.Add($i)}
            if($Source[$i] -ceq '{'){$depth++}elseif($Source[$i] -ceq '}'){$depth--}
        }
        $found=$selected.ToArray()
    }
    Assert-Continuation ($found.Count -eq 1) ('Missing or ambiguous block: '+$Signature)
    $open=$found[0]
    while($open -lt $Source.Count -and $Source[$open] -cne '{'){$open++}
    Assert-Continuation ($open -lt $Source.Count) 'Opening brace missing.'
    $depth=0
    for($i=$open;$i -lt $Source.Count;$i++){
        if($Source[$i] -ceq '{'){$depth++}elseif($Source[$i] -ceq '}'){$depth--}
        if($depth -eq 0){return ,([string[]]$Source[$found[0]..$i])}
    }
    throw 'Producer continuation source contract: Unbalanced source block.'
}
function Method {param([string[]]$Class,[string]$Signature) return ,(Block $Class $Signature -Direct)}
function Replace-Tokens {
    param([string[]]$Source,[string]$Needle,[string]$Replacement)
    $at=Find $Source $Needle;Assert-Continuation ($at.Count -eq 1) ('Mutation anchor differs: '+$Needle)
    $old=Tokens $Needle;$new=Tokens $Replacement;$result=[Collections.Generic.List[string]]::new()
    for($i=0;$i -lt $at[0];$i++){$result.Add($Source[$i])}
    foreach($token in $new){$result.Add($token)}
    for($i=$at[0]+$old.Count;$i -lt $Source.Count;$i++){$result.Add($Source[$i])}
    return ,$result.ToArray()
}
$sources=[ordered]@{}
foreach($name in @('GuardianLaunch','Producer','Bootstrap')){
    $sources[$name]=Read-ContinuationSource ([IO.Path]::Combine($PSScriptRoot,'..',('Stratus.RuntimeBuild.'+$name+'.cs')))
}
$testBefore=Read-ContinuationSource $PSCommandPath
$w=Block (Tokens $sources.GuardianLaunch.text) 'public sealed class GuardianLaunch : IDisposable'
$p=Block (Tokens $sources.Producer.text) 'public sealed class DisposableProducerLaunch : IDisposable'
$b=Block (Tokens $sources.Bootstrap.text) 'public sealed class CompilerCommand : IDisposable'
$blocks=@{
    w=$w;p=$p;b=$b
    observation=Block (Tokens $sources.GuardianLaunch.text) 'public sealed class GuardianReadyObservation'
    wentry=Method $w 'public void ContinueProducer('
    blocker=Method $w 'private static void DemandQualifiedFirstProducerContinuation('
    wpoll=Method $w 'public GuardianReadyObservation PollReady('
    wready=Method $w 'private GuardianReadyObservation ReadReadyCore('
    wcheck=Method $w 'internal void AssertProducerContinuation('
    wadmission=Method $w 'private void Admission('
    pentry=Method $p 'internal void ContinueUnderGuardian('
    pcheck=Method $p 'internal void AssertGuardianContinuation('
    padmission=Method $p 'private void RequireAdmission('
    generic=Method $b 'public void Resume('
    typed=Method $b 'internal void ResumeProducer('
    boundary=Method $b 'private void ProducerContinuationBoundary('
    core=Method $b 'private void ResumeOriginal('
    clock=Method $b 'private void ClockAndFaultBoundary('
    success=Method $b 'private void SuccessBoundary('
}
# Every original fixed predicate has one rejected weakening mutation. No input
# is converted into a ScriptBlock or used as a command/type/member to execute.
$rows=@(
    @('w_entry_accepts_no_external_ready_or_authority_argument','wentry',{
        param($s) Has $s 'public void ContinueProducer()';Has $s 'Enter();';foreach($bad in @('GuardianReadyObservation','bool approved','IntPtr','Func<','Action<')){Has $s $bad 0}
    },'public void ContinueProducer()','public void ContinueProducer(GuardianReadyObservation supplied)'),
    @('w_continuation_state_is_private_and_not_exported','w',{
        param($s) Has $s 'private bool continuationAttempted, continuationActive, continuationCompleted;';Has $s 'private int continuationThreadId;';Has $s 'public void ContinueProducer()'
    },'private bool continuationAttempted, continuationActive, continuationCompleted;','public bool continuationAttempted, continuationActive, continuationCompleted;'),
    @('w_completed_start_and_single_attempt_precede_ready_check','wentry',{
        param($s) Order $s @('Admission();','if (!started || continuationAttempted)','continuationAttempted = true;','if (ReadReadyCore() == null)');Has $s 'continuationAttempted = false' 0
    },'!started || continuationAttempted','!started'),
    @('w_actual_ready_then_separate_barrier_before_private_phase','wentry',{
        param($s) Order $s @('if (ReadReadyCore() == null)','DemandQualifiedFirstProducerContinuation();','continuationThreadId = Thread.CurrentThread.ManagedThreadId;','continuationActive = true;','producer.ContinueUnderGuardian(this, sourceCapability);')
    },'DemandQualifiedFirstProducerContinuation();','/* qualification barrier omitted */'),
    @('first_p_qualification_barrier_is_unconditional_and_parameterless','blocker',{
        param($s) Has $s 'private static void DemandQualifiedFirstProducerContinuation() { throw new InvalidOperationException("First P continuation remains closed until independent full-protocol native qualification."); }'
    },'throw new InvalidOperationException("First P continuation remains closed until independent full-protocol native qualification.");','return;'),
    @('w_exact_p_call_is_rechecked_before_completed_publication','wentry',{
        param($s) Has $s 'producer.ContinueUnderGuardian(this, sourceCapability); AssertProducerContinuation(producer, sourceCapability); continuationCompleted = true;';Has $s 'producer.ContinueUnderGuardian(' 1
    },'AssertProducerContinuation(producer, sourceCapability);','/* post-call original W check omitted */'),
    @('w_failed_continuation_is_sticky_and_only_phase_is_cleared','wentry',{
        param($s) Has $s 'catch (Exception exception) { Record(exception); throw; } finally { continuationActive = false; continuationThreadId = 0; Leave(); }';foreach($bad in @('.Dispose(','.PollContainment(','.Join(','continuationAttempted = false','continuationCompleted = false')){Has $s $bad 0}
    },'Record(exception); throw;','throw;'),
    @('public_ready_poll_keeps_serialized_sticky_wrapper','wpoll',{
        param($s) Has $s 'public GuardianReadyObservation PollReady() { Enter(); try { return ReadReadyCore(); } catch (Exception exception) { Record(exception); throw; } finally { Leave(); } }'
    },'return ReadReadyCore();','return readyObservation;'),
    @('w_callback_requires_current_operation_phase_thread_and_uncompleted_attempt','wcheck',{
        param($s) Has $s 'Interlocked.CompareExchange(ref operation, 0, 0) != 1 || !continuationActive || continuationThreadId != Thread.CurrentThread.ManagedThreadId || !continuationAttempted || continuationCompleted';Has $s 'Admission();' 2
    },'continuationThreadId != Thread.CurrentThread.ManagedThreadId','false'),
    @('w_callback_binds_exact_p_capability_and_registered_backlink','wcheck',{
        param($s) Has $s '!Object.ReferenceEquals(producer, candidate) || !Object.ReferenceEquals(sourceCapability, capability) || !producer.OwnsGuardian(this, capability)'
    },'!Object.ReferenceEquals(sourceCapability, capability)','false'),
    @('w_callback_reads_actual_ready_again_then_final_admission','wcheck',{
        param($s) Has $s 'if (ReadReadyCore() == null) throw new InvalidOperationException("Original W READY was lost before producer continuation."); Admission();';foreach($bad in @('Enter(','.Join(','while (','readyObservation','ResumeThread(','PollReady(')){Has $s $bad 0}
    },'if (ReadReadyCore() == null)','if (readyObservation == null)'),
    @('ready_core_refreshes_transport_retirement_and_original_live_identities','wready',{
        param($s) Order $s @('Admission(); if (!started)','controlChannel.AssertBothDeliveriesRetired(this, sourceCapability);','contextSection.AssertBothDeliveriesRetired(this, sourceCapability);','HeldBoundary(); RootBoundary(true); launchCopy.AssertLive(); launchCopy.AssertLaunchInheritanceRetired();','captured = stdout.ToArray();')
    },'contextSection.AssertBothDeliveriesRetired(this, sourceCapability);','/* context retirement omitted */'),
    @('ready_core_rejects_failed_stderr_or_eof_before_actual_copy','wready',{
        param($s) Has $s 'if (captureFailed || stderr.Length != 0 || stdoutEof || stderrEof)';Has $s 'lock (captureGate)' 2;Has $s 'captured = stdout.ToArray();'
    },'captureFailed || stderr.Length != 0 || stdoutEof || stderrEof','captureFailed || stderr.Length != 0'),
    @('ready_core_compares_exact_bytes_and_does_not_accept_a_prefix','wready',{
        param($s) Order $s @('byte[] expected = Encoding.ASCII.GetBytes(expectedReady);','if (captured.Length > expected.Length)','for (int i = 0; i < captured.Length; ++i) if (captured[i] != expected[i])','if (captured.Length != expected.Length) { Admission(); RootBoundary(true); return null; }')
    },'captured[i] != expected[i]','false'),
    @('ready_final_capture_recheck_follows_live_native_checks','wready',{
        param($s) Order $s @('HeldBoundary(); launchCopy.AssertLive(); launchCopy.AssertLaunchInheritanceRetired(); transferLaunchCopy.AssertLive(); transferLaunchCopy.AssertLaunchInheritanceRetired(); RootBoundary(true); Admission();','if (captureFailed || failures.Count != 0 || stdout.Length != expected.Length || stderr.Length != 0 || stdoutEof || stderrEof)','Admission(); if (readyObservation == null)','return readyObservation;')
    },'stdout.Length != expected.Length','stdout.Length > expected.Length'),
    @('historical_ready_never_claims_resume_authority','observation',{
        param($s) Has $s 'public readonly bool AuthorizesProducerResume = false, NativeQualified = false;';Has $s 'public readonly string ContextSha256;';Has $s 'public readonly int ContextBytes;'
    },'AuthorizesProducerResume = false','AuthorizesProducerResume = true'),
    @('p_continuation_phase_is_private_and_entry_is_internal','p',{
        param($s) Has $s 'private bool continuationAttempted, continuationActive, continuationCompleted;';Has $s 'private int continuationThreadId;';Has $s 'internal void ContinueUnderGuardian(GuardianLaunch candidate, ProducerStopCapability capability)';Has $s 'public void Resume(' 0
    },'internal void ContinueUnderGuardian(','public void ContinueUnderGuardian('),
    @('p_entry_requires_completed_original_command_and_registered_w_once','pentry',{
        param($s) Order $s @('Enter();','RequireAdmission();','if (!startCompleted || command == null || continuationAttempted || !OwnsGuardian(candidate, capability))','continuationAttempted = true;','continuationThreadId = Thread.CurrentThread.ManagedThreadId;','continuationActive = true;')
    },'!OwnsGuardian(candidate, capability)','false'),
    @('p_exact_command_typed_resume_is_sandwiched_by_guardian_callbacks','pentry',{
        param($s) Has $s 'AssertGuardianContinuation(command, candidate); command.ResumeProducer(this, candidate); AssertGuardianContinuation(command, candidate); continuationCompleted = true;';Has $s 'command.ResumeProducer(' 1;Has $s 'command.Resume(' 0
    },'command.ResumeProducer(this, candidate);','command.Resume();'),
    @('p_failure_is_sticky_without_retry_cleanup_or_phase_leak','pentry',{
        param($s) Has $s 'catch (Exception error) { Record(error); throw; } finally { continuationActive = false; continuationThreadId = 0; Leave(); }';foreach($bad in @('continuationAttempted = false','command = null','.Dispose(','.PollContainment(','.Join(')){Has $s $bad 0}
    },'Record(error); throw;','throw;'),
    @('p_callback_requires_active_same_thread_exact_original_command','pcheck',{
        param($s) Has $s 'Interlocked.CompareExchange(ref operation, 0, 0) != 1 || !continuationActive || continuationThreadId != Thread.CurrentThread.ManagedThreadId || !continuationAttempted || continuationCompleted || candidate == null || !Object.ReferenceEquals(command, candidate) || !startCompleted || !OwnsGuardian(originalGuardian, stopCapability)'
    },'!Object.ReferenceEquals(command, candidate)','false'),
    @('p_callback_routes_exact_original_capability_without_reentrant_operation','pcheck',{
        param($s) Has $s 'originalGuardian.AssertProducerContinuation(this, stopCapability); RequireAdmission();';Has $s 'RequireAdmission();' 2;foreach($bad in @('Enter(','lock (','command.','ResumeThread(','.Join(')){Has $s $bad 0}
    },'originalGuardian.AssertProducerContinuation(this, stopCapability);','originalGuardian.AssertProducerContinuation(this, null);'),
    @('p_admission_preserves_fault_preload_and_original_deadline','padmission',{
        param($s) Order $s @('if (disposed || releaseStarted || containmentStarted)','lock (failureGate) { if (failed) throw new AggregateException(failures.ToArray()); }','preload.AssertProducer(this, inputs);','if (startAttempted) startupDeadline.AssertActive(this);')
    },'preload.AssertProducer(this, inputs);','/* preload admission omitted */'),
    @('w_admission_preserves_preload_clocks_control_and_context','wadmission',{
        param($s) Order $s @('if (disposed || releaseStarted || containmentStarted)','lock (captureGate) { if (failures.Count != 0) throw new AggregateException(failures.ToArray()); }','preload.AssertGuardian(this, producer, inputs);','if (readyClock.ElapsedMilliseconds >= ReadyMilliseconds)','if (startupDeadline != null) startupDeadline.AssertGuardianActive(this, sourceCapability);','if (controlChannel != null) controlChannel.AssertGuardianAdmission(this, sourceCapability);','if (contextSection != null) contextSection.AssertGuardianAdmission(this, sourceCapability);')
    },'startupDeadline.AssertGuardianActive(this, sourceCapability);','/* startup deadline omitted */'),
    @('generic_public_resume_rejects_producer_bound_command_before_core','generic',{
        param($s) Has $s 'public void Resume() { Enter(); try { if (producerCompilerOwner != null) throw new InvalidOperationException("Compiler-in-P continuation is unavailable before actual W root/Job ACK and qualification."); if (originalDeadlineOwner != null) throw new InvalidOperationException("Producer commands require their exact guardian continuation path."); ResumeOriginal(null); } catch (Exception failure) { Record(failure); throw; } finally { Leave(); } }'
    },'if (originalDeadlineOwner != null)','if (false)'),
    @('typed_command_entry_requires_exact_original_owner_and_nonnull_w','typed',{
        param($s) Order $s @('Enter();','if (owner == null || guardian == null || !Object.ReferenceEquals(originalDeadlineOwner, owner))','owner.AssertGuardianContinuation(this, guardian);','ResumeOriginal(guardian);');Has $s 'ResumeOriginal(null)' 0
    },'!Object.ReferenceEquals(originalDeadlineOwner, owner)','false'),
    @('typed_command_entry_keeps_sticky_record_and_operation_release','typed',{
        param($s) Has $s 'catch (Exception failure) { Record(failure); throw; } finally { Leave(); }';Has $s 'ResumeOriginal(' 1;Has $s 'ResumeThread(' 0
    },'Record(failure); throw;','throw;'),
    @('common_boundary_keeps_generic_and_producer_roles_disjoint','boundary',{
        param($s) Has $s 'if (originalDeadlineOwner == null) { if (guardian != null) throw new InvalidOperationException("A generic command cannot adopt a guardian."); return; }';Has $s 'if (guardian == null) throw new InvalidOperationException("Original producer guardian is required.");'
    },'if (guardian == null)','if (false)'),
    @('common_boundary_calls_only_original_p_with_exact_command_and_w','boundary',{
        param($s) Has $s 'originalDeadlineOwner.AssertGuardianContinuation(this, guardian);';foreach($bad in @('Enter(','ResumeThread(','GuardianReadyObservation','Func<','Action<')){Has $s $bad 0}
    },'originalDeadlineOwner.AssertGuardianContinuation(this, guardian);','originalDeadlineOwner.AssertGuardianContinuation(null, guardian);'),
    @('native_core_is_private_single_use_with_initial_typed_boundary','core',{
        param($s) Has $s 'private void ResumeOriginal(GuardianLaunch guardian)';Has $s 'if (!started || resumeAttempted)';Has $s 'ProducerContinuationBoundary(guardian); CompilerContinuationBoundary(); SuccessBoundary(); HeldExecutableBoundary(); RootIdentity(false);';Has $s 'Enter(' 0
    },'!started || resumeAttempted','!started'),
    @('native_inspection_follows_root_job_and_fresh_typed_boundary','core',{
        param($s) Has $s 'if (RootWait() != WaitTimeout || JobAccounting().active != 1) throw new InvalidOperationException("Compiler pre-resume ownership differs."); if (producerCompilerOwner != null) AssertSoleSuspendedCompilerRoot(); ProducerContinuationBoundary(guardian); CompilerContinuationBoundary(); uint previous = SuspendThread(thread);'
    },'JobAccounting().active != 1) throw new InvalidOperationException("Compiler pre-resume ownership differs.");','false) throw new InvalidOperationException("Compiler pre-resume ownership differs.");'),
    @('inspection_requires_exact_prior_count_before_any_restore','core',{
        param($s) Order $s @('uint previous = SuspendThread(thread);','if (previous == UInt32.MaxValue)','if (previous != 1) throw new InvalidOperationException("Compiler original suspension was disproved; leave it paused for containment.");','uint restored = ResumeThread(thread);');Has $s 'ResumeThread(' 2
    },'if (previous != 1)','if (false)'),
    @('restoration_requires_exact_count_two_before_final_ownership_checks','core',{
        param($s) Has $s 'uint restored = ResumeThread(thread); if (restored == UInt32.MaxValue) Check(false, "Restore compiler inspection increment"); if (restored != 2) throw new InvalidOperationException("Compiler inspection restoration was ambiguous."); SuccessBoundary(); RootIdentity(false);'
    },'if (restored != 2)','if (false)'),
    @('post_inspection_root_and_job_are_rechecked_before_final_continuation','core',{
        param($s) Has $s 'SuccessBoundary(); RootIdentity(false); if (RootWait() != WaitTimeout || JobAccounting().active != 1) throw new InvalidOperationException("Original root/Job changed after suspension inspection.");'
    },'RootWait() != WaitTimeout || JobAccounting().active != 1) throw new InvalidOperationException("Original root/Job changed after suspension inspection.");','false) throw new InvalidOperationException("Original root/Job changed after suspension inspection.");'),
    @('final_input_check_precedes_typed_w_check_then_clock_only_to_resume','core',{
        param($s) Has $s 'SuccessBoundary(); ProducerContinuationBoundary(guardian); CompilerContinuationBoundary(); ClockAndFaultBoundary(); resumeAttempted = true; uint count = ResumeThread(thread);'
    },'ClockAndFaultBoundary(); resumeAttempted = true;','SuccessBoundary(); resumeAttempted = true;'),
    @('actual_resume_requires_exact_original_count_and_records_completion_after_return','core',{
        param($s) Has $s 'resumeAttempted = true; uint count = ResumeThread(thread); if (count == UInt32.MaxValue) Check(false, "Resume original compiler"); if (count != 1) throw new InvalidOperationException("Compiler resume was ambiguous."); resumed = true;'
    },'if (count != 1)','if (false)'),
    @('post_resume_revalidates_current_typed_chain_and_original_budgets','core',{
        param($s) Has $s 'resumed = true; SuccessBoundary(); ProducerContinuationBoundary(guardian); CompilerContinuationBoundary(); SuccessBoundary();';foreach($bad in @('resumeAttempted = false','Stopwatch.StartNew(','.Restart(','.Reset(','.Dispose(','.PollContainment(','GetProcessById(','OpenProcess(')){Has $s $bad 0}
    },'resumed = true; SuccessBoundary(); ProducerContinuationBoundary(guardian); CompilerContinuationBoundary(); SuccessBoundary();','resumed = true; SuccessBoundary();'),
    @('final_clock_checks_fault_original_preload_local_and_shared_deadline_without_io','clock',{
        param($s) Has $s 'private void ClockAndFaultBoundary() { SuccessState(); if (producerCompilerOwner != null) { if (successClock != null || sharedStartupDeadline != null || originalDeadlineOwner != null || producerControlChannel != null || producerContextSection != null) throw new InvalidOperationException("Compiler-in-P cannot adopt another clock or outer-P launch context."); producerCompilerOwner.AssertCommandAdmission(this); return; } if (originalDeadlineOwner != null) originalDeadlineOwner.AssertPreloadAdmission(this); if (successClock.ElapsedMilliseconds >= SuccessMilliseconds) throw new TimeoutException("Original 60-second compiler budget expired."); if (sharedStartupDeadline != null) sharedStartupDeadline.AssertActive(originalDeadlineOwner); }'
    },'sharedStartupDeadline.AssertActive(originalDeadlineOwner);','/* original shared clock omitted */'),
    @('held_input_boundary_preserves_clock_sandwich','success',{
        param($s) Has $s 'private void SuccessBoundary() { ClockAndFaultBoundary(); AssertOwnedInputsHeld(); ClockAndFaultBoundary(); }'
    },'ClockAndFaultBoundary(); AssertOwnedInputsHeld(); ClockAndFaultBoundary();','AssertOwnedInputsHeld();'),
    @('w_continuation_has_no_clock_reset_wait_worker_or_auto_cleanup','wentry',{
        param($s) foreach($bad in @('Stopwatch.StartNew(','.Restart(','.Reset(','new Thread(','while (','.Join(','.Dispose(','.PollContainment(','ResumeThread(','CreateProcess(','readyObservation')){Has $s $bad 0};Has $s 'DemandQualifiedFirstProducerContinuation();'
    },'continuationAttempted = true;','continuationAttempted = true; readyClock.Restart();')
)
$expectedPairs=40;$expected=80
Assert-Continuation ($rows.Count -eq $expectedPairs -and $expected -eq 2*$expectedPairs) 'Fixed case inventory differs.'
$executed=0;$results=[Collections.Generic.List[object]]::new()
foreach($row in $rows){
    Assert-Continuation ($row.Count -eq 5) 'Fixed row shape differs.'
    $selected=$blocks[$row[1]];$check=$row[2]
    & $check $selected;$executed++;$results.Add([pscustomobject]@{name=$row[0];variant='original_source';passed=$true})
    $mutated=Replace-Tokens $selected $row[3] $row[4];$rejected=$false
    try{& $check $mutated}catch{
        Assert-Continuation ($_.Exception.Message.StartsWith('Producer continuation source contract:',[StringComparison]::Ordinal)) ('Unexpected negative-control exception: '+$row[0])
        $rejected=$true
    }
    Assert-Continuation $rejected ('Weakening mutation survived: '+$row[0])
    $executed++;$results.Add([pscustomobject]@{name=$row[0];variant='mutated_source_rejected';passed=$true})
}
Assert-Continuation ($executed -eq $expected -and $results.Count -eq $expected) 'Fixed cases did not complete.'
$before=[Collections.Generic.List[object]]::new();$after=[Collections.Generic.List[object]]::new()
foreach($name in $sources.Keys){
    $old=$sources[$name];$latest=Read-ContinuationSource $old.path
    Assert-Continuation ($latest.sha256 -ceq $old.sha256) ('Subject changed during inspection: '+$name)
    $before.Add([pscustomobject]@{name=$name;path=$old.path;sha256=$old.sha256})
    $after.Add([pscustomobject]@{name=$name;path=$latest.path;sha256=$latest.sha256})
}
$testAfter=Read-ContinuationSource $PSCommandPath
Assert-Continuation ($testAfter.sha256 -ceq $testBefore.sha256) 'Test changed during inspection.'
[pscustomobject]@{
    format='stratus-producer-continuation-source-contract-tests-v1'
    expected_pairs=$expectedPairs;expected=$expected;executed=$executed;passed=$executed;failed=0;skipped=0
    powershell_version=$PSVersionTable.PSVersion.ToString();powershell_edition=$PSVersionTable.PSEdition
    source_hashes_before=$before.ToArray();source_hashes_after=$after.ToArray()
    test_sha256_before=$testBefore.sha256;test_sha256_after=$testAfter.sha256;source_and_test_hashes_unchanged=$true
    source_tokens_and_directed_inert_mutations_only=$true
    target_compiled=$false;target_loaded=$false;target_lifecycle_executed=$false;native_qualified=$false
    filesystem_fixture_created=$false;csharp_semantics_proven=$false
    first_producer_continuation_qualification_configured=$false;producer_resume_authorized=$false
    independent_preload_descriptor_configured=$false;preload_open_authorized=$false;payload_open_authorized=$false
    historical_ready_is_authority=$false;native_race_freedom_proven=$false;timing_qualified=$false
    physical_ownership_proven=$false;compiler_job_root_acknowledged=$false;full_build_accepted=$false
    cases=$results.ToArray()
}|ConvertTo-Json -Depth 6 -Compress
