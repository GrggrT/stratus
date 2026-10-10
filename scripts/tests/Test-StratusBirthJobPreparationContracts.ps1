param()
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
# Fixed source data only. Never compile, load, evaluate or invoke a target.
# All callbacks below are literal test predicates; mutations remain token data.
# This suite cannot qualify native Job preparation, timing or ownership.
function Assert-BirthJob {param([bool]$Okay,[string]$Message) if(-not $Okay){throw ('Birth Job preparation source contract: '+$Message)}}
function Read-BirthJobSource {
    param([string]$Path)
    $full=[IO.Path]::GetFullPath($Path)
    # Bound allocation and every delivered read; a FileInfo precheck alone
    # cannot stop ReadAllBytes from allocating after concurrent file growth.
    $stream=[IO.FileStream]::new($full,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{
        $length=$stream.Length
        Assert-BirthJob ($length -gt 0 -and $length -le 1048576) 'Inspection file exceeds one MiB.'
        $raw=[byte[]]::new([int]$length);$offset=0
        while($offset -lt $raw.Length){
            $count=$stream.Read($raw,$offset,$raw.Length-$offset)
            Assert-BirthJob ($count -gt 0) 'Inspection file ended before its bounded length.'
            $offset+=$count
        }
        Assert-BirthJob ($stream.ReadByte() -eq -1 -and $stream.Length -eq $length) 'File length changed while reading.'
    }finally{$stream.Dispose()}
    $source=[Text.UTF8Encoding]::new($false,$true).GetString($raw)
    $hash=[Security.Cryptography.SHA256]::Create()
    try{$digest=[BitConverter]::ToString($hash.ComputeHash($raw)).Replace('-','').ToLowerInvariant()}finally{$hash.Dispose()}
    return [pscustomobject]@{path=$full;text=$source;sha256=$digest}
}
function Tokens {
    param([string]$Text)
    Assert-BirthJob ($null -ne $Text -and $Text.Length -le 1048576) 'Token input exceeds its fixed character bound.'
    $pattern=@'
//[^\r\n]* | /\*.*?\*/ | \#[^\r\n]* | @"(?:""|[^"])*" | "(?:\\.|[^"\\])*" | '(?:\\.|[^'\\])*'
| [A-Za-z_][A-Za-z_0-9]* | [0-9]+ | == | != | >= | <= | \+\+ | -- | && | \|\| | => | [^\s]
'@
    $result=[Collections.Generic.List[string]]::new()
    $options=[Text.RegularExpressions.RegexOptions]::Singleline -bor [Text.RegularExpressions.RegexOptions]::IgnorePatternWhitespace
    foreach($m in [regex]::Matches($Text,$pattern,$options,[TimeSpan]::FromSeconds(5))){
        if(-not $m.Value.StartsWith('//') -and -not $m.Value.StartsWith('/*') -and -not $m.Value.StartsWith('#')){Assert-BirthJob ($result.Count -lt 262144) 'Token inspection bound exceeded.';$result.Add($m.Value)}
    }
    Assert-BirthJob ($result.Count -le 262144) 'Token inspection bound exceeded.'
    return ,$result.ToArray()
}
function PowerShell-Tokens {
    param([string]$Text)
    Assert-BirthJob ($null -ne $Text -and $Text.Length -le 1048576) 'PowerShell inspection input exceeds its fixed character bound.'
    # Parse as inert syntax only. PowerShell single-quoted backslashes are not
    # C# escapes; source, fixed snippets and mutation anchors use this same path.
    # Never call GetScriptBlock/Create, define functions, or evaluate this AST.
    $parsedTokens=$null;$errors=$null
    $null=[Management.Automation.Language.Parser]::ParseInput($Text,[ref]$parsedTokens,[ref]$errors)
    Assert-BirthJob (@($errors).Count -eq 0) 'PowerShell inspection text did not parse.'
    $result=[Collections.Generic.List[string]]::new()
    foreach($token in $parsedTokens){
        if($token.Kind -notin @('Comment','NewLine','LineContinuation','EndOfInput')){Assert-BirthJob ($result.Count -lt 262144) 'PowerShell token inspection bound exceeded.';$result.Add($token.Text)}
    }
    Assert-BirthJob ($result.Count -le 262144) 'PowerShell token inspection bound exceeded.'
    return ,$result.ToArray()
}
function Find {
    param([string[]]$Source,[string]$Sequence,[switch]$PowerShell)
    $wanted=if($PowerShell){PowerShell-Tokens $Sequence}else{Tokens $Sequence}
    Assert-BirthJob ($wanted.Count -gt 0) 'Empty predicate.'
    $found=[Collections.Generic.List[int]]::new()
    for($i=0;$i -le $Source.Count-$wanted.Count;$i++){
        $same=$true
        for($j=0;$j -lt $wanted.Count;$j++){if($Source[$i+$j] -cne $wanted[$j]){$same=$false;break}}
        if($same){$found.Add($i)}
    }
    return ,$found.ToArray()
}
function Has {param([string[]]$Source,[string]$Sequence,[int]$Count=1) Assert-BirthJob ((Find $Source $Sequence).Count -eq $Count) ('Sequence differs: '+$Sequence)}
function Has-PowerShell {param([string[]]$Source,[string]$Sequence,[int]$Count=1) Assert-BirthJob ((Find $Source $Sequence -PowerShell).Count -eq $Count) ('PowerShell sequence differs: '+$Sequence)}
function Order {
    param([string[]]$Source,[string[]]$Sequences)
    $last=-1
    foreach($sequence in $Sequences){$found=Find $Source $sequence;Assert-BirthJob ($found.Count -eq 1 -and $found[0] -gt $last) ('Order differs: '+$sequence);$last=$found[0]}
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
    Assert-BirthJob ($found.Count -eq 1) ('Missing or ambiguous block: '+$Signature)
    $open=$found[0]
    while($open -lt $Source.Count -and $Source[$open] -cne '{'){$open++}
    Assert-BirthJob ($open -lt $Source.Count) 'Opening brace missing.'
    $depth=0
    for($i=$open;$i -lt $Source.Count;$i++){
        if($Source[$i] -ceq '{'){$depth++}elseif($Source[$i] -ceq '}'){$depth--}
        if($depth -eq 0){return ,([string[]]$Source[$found[0]..$i])}
    }
    throw 'Birth Job preparation source contract: Unbalanced source block.'
}
function Method {param([string[]]$Class,[string]$Signature) return ,(Block $Class $Signature -Direct)}
function Replace-Tokens {
    param([string[]]$Source,[string]$Needle,[string]$Replacement,[switch]$PowerShell)
    $at=Find $Source $Needle -PowerShell:$PowerShell;Assert-BirthJob ($at.Count -eq 1) ('Mutation anchor differs: '+$Needle)
    $old=if($PowerShell){PowerShell-Tokens $Needle}else{Tokens $Needle}
    $new=if($PowerShell){PowerShell-Tokens $Replacement}else{Tokens $Replacement}
    $result=[Collections.Generic.List[string]]::new()
    for($i=0;$i -lt $at[0];$i++){$result.Add($Source[$i])}
    foreach($token in $new){$result.Add($token)}
    for($i=$at[0]+$old.Count;$i -lt $Source.Count;$i++){$result.Add($Source[$i])}
    return ,$result.ToArray()
}
$sources=[ordered]@{}
foreach($name in @('Bootstrap','Producer','StartupPreload','GuardianLaunch','ProducerInputs')){
    $sources[$name]=Read-BirthJobSource ([IO.Path]::Combine($PSScriptRoot,'..',('Stratus.RuntimeBuild.'+$name+'.cs')))
}
$sources['Loader']=Read-BirthJobSource ([IO.Path]::Combine($PSScriptRoot,'..','Stratus.RuntimeBuild.Bootstrap.ps1'))
$testBefore=Read-BirthJobSource $PSCommandPath
$b=Block (Tokens $sources.Bootstrap.text) 'public sealed class CompilerCommand : IDisposable'
$p=Block (Tokens $sources.Producer.text) 'public sealed class DisposableProducerLaunch : IDisposable'
$preload=Block (Tokens $sources.StartupPreload.text) 'public sealed class StartupPreloadOwner : IDisposable'
$w=Block (Tokens $sources.GuardianLaunch.text) 'public sealed class GuardianLaunch : IDisposable'
$inputs=Block (Tokens $sources.ProducerInputs.text) 'internal sealed partial class ProducerInvocation'
$blocks=@{
    b=$b;p=$p;loader=(PowerShell-Tokens $sources.Loader.text)
    prepare=Method $b 'private void PrepareBirthJobOriginal('
    start=$b
    outcome=Method $b 'private void RequireJobOutcome('
    owned=Method $b 'private void RequireOwnedJob('
    prepared=Method $b 'private void RequirePreparedJob('
    empty=Method $b 'private void AssertEmptyJobBeforeBirth('
    accounting=Method $b 'private Accounting JobAccounting('
    nochild=Method $b 'private bool NoChildCreated('
    contain=Method $b 'public bool PollContainment('
    dispose=Method $b 'public void Dispose('
    close=Method $b 'private static void CloseOwned('
    clock=Method $b 'private void ClockAndFaultBoundary('
    pstart=Method $p 'public void StartSuspended('
    pcontain=Method $p 'public bool PollContainment('
    selector=Method $preload 'private static Descriptor SelectIndependentDescriptor('
    preloadblock=Method $preload 'private static void DemandQualifiedPreloadOpen('
    continuationblock=Method $w 'private static void DemandQualifiedFirstProducerContinuation('
    payloadblock=Method $inputs 'private static void DemandQualifiedPayloadOpen('
}
# Fixed callbacks below inspect only inert tokens. In particular Loader tokens
# are never defined, dot-sourced, evaluated, or used to select callable members.
$rows=@(
    @('compiler_declares_exact_explicit_job_lifecycle_abi_v2','b',{
        param($s) Has $s 'public const string Abi = "stratus-runtime-build-bootstrap-v2";';Has $s 'public void StartSuspended()'
        Has (Method $s 'public void PrepareBirthJob(') 'public void PrepareBirthJob() { try { if (producerCompilerOwner != null) throw new InvalidOperationException("Compiler-in-P requires its exact retained worker preparation path."); PrepareBirthJobOriginal(); } catch (Exception failure) { Record(failure); throw; } }'
        Has (Method $s 'internal void PrepareProducerCompilerJob(') 'internal void PrepareProducerCompilerJob(ProducerCompilerOwner owner) { try { if (owner == null || !Object.ReferenceEquals(producerCompilerOwner, owner)) throw new InvalidOperationException("Only the original P compiler owner may prepare its Job."); owner.AssertCommandAdmission(this); PrepareBirthJobOriginal(); } catch (Exception failure) { Record(failure); throw; } }'
        Has $s 'private void PrepareBirthJobOriginal()'
    },'"stratus-runtime-build-bootstrap-v2"','"stratus-runtime-build-bootstrap-v1"'),
    @('loader_requires_exact_v2_literal_on_original_returned_command_type','loader',{
        param($s) Has-PowerShell $s '$commandType=$assembly.GetType(''Stratus.RuntimeBuild.CompilerCommand'',$true,$false)';Has-PowerShell $s '$abi=$commandType.GetField(''Abi'',[Reflection.BindingFlags]''Public,Static,DeclaredOnly'')';Has-PowerShell $s '$abi.GetRawConstantValue() -cne ''stratus-runtime-build-bootstrap-v2''';Has-PowerShell $s '$null -eq $abi -or -not $abi.IsLiteral -or $abi.FieldType -ne [string]'
    },'''stratus-runtime-build-bootstrap-v2''','''stratus-runtime-build-bootstrap-v1'''),
    @('creation_and_configuration_keep_separate_private_outcome_flags','b',{
        param($s) Has $s 'private bool jobPreparationAttempted, jobPrepared, jobAttempted, jobReturned, jobSucceeded;';Has $s 'private bool jobConfigurationAttempted, jobConfigurationReturned, jobConfigurationSucceeded;';Has $s 'private int jobCreationError, jobConfigurationError;'
    },'private bool jobConfigurationAttempted, jobConfigurationReturned, jobConfigurationSucceeded;','private bool jobConfigurationSucceeded;'),
    @('separate_job_operation_is_single_use_before_any_start','prepare',{
        param($s) Has $s 'private void PrepareBirthJobOriginal()';Order $s @('Enter();','if (jobPreparationAttempted || startAttempted)','jobPreparationAttempted = true; SuccessBoundary();');Has $s 'jobPreparationAttempted = false' 0
    },'jobPreparationAttempted || startAttempted','startAttempted'),
    @('environment_hash_is_derived_before_job_creation_from_exact_retained_block','prepare',{
        param($s) Has $s 'byte[] environmentBytes = Encoding.Unicode.GetBytes(environmentBlock); using (SHA256 hash = SHA256.Create()) environmentHash = BitConverter.ToString(hash.ComputeHash(environmentBytes)).Replace("-", "").ToLowerInvariant(); SuccessBoundary(); jobAttempted = true;'
    },'hash.ComputeHash(environmentBytes)','hash.ComputeHash(new byte[0])'),
    @('unnamed_job_raw_handle_and_error_are_retained_before_success_return_flags','prepare',{
        param($s) Has $s 'SuccessBoundary(); jobAttempted = true; job = CreateJobObject(IntPtr.Zero, null); jobCreationError = Marshal.GetLastWin32Error(); jobSucceeded = ValidHandle(job); jobReturned = true; if (!jobSucceeded) throw new Win32Exception(jobCreationError, "Create compiler-only Job");';Has $s 'CreateJobObject(' 1
    },'CreateJobObject(IntPtr.Zero, null)','CreateJobObject(IntPtr.Zero, "shared")'),
    @('configuration_has_distinct_attempt_actual_result_error_and_return_order','prepare',{
        param($s) Has $s 'ExtendedLimit limits = new ExtendedLimit(); limits.basic.flags = 0x2000; jobConfigurationAttempted = true; jobConfigurationSucceeded = SetInformationJobObject(job, 9, ref limits, (uint)Marshal.SizeOf(typeof(ExtendedLimit))); jobConfigurationError = Marshal.GetLastWin32Error(); jobConfigurationReturned = true;'
    },'jobConfigurationSucceeded = SetInformationJobObject','jobSucceeded = SetInformationJobObject'),
    @('configuration_failure_cannot_publish_prepared_state','prepare',{
        param($s) Order $s @('if (!jobConfigurationSucceeded) throw new Win32Exception(jobConfigurationError, "Compiler Job kill-on-close without breakaway");','SuccessBoundary(); AssertEmptyJobBeforeBirth(); SuccessBoundary(); jobPrepared = true;');Has $s 'jobPrepared = true;' 1
    },'if (!jobConfigurationSucceeded)','if (false)'),
    @('preparation_failure_retains_ownership_without_cleanup_or_retry','prepare',{
        param($s) Has $s 'catch (Exception failure) { Record(failure); throw; } finally { Leave(); }';foreach($bad in @('CloseHandle(','TerminateJobObject(','job = IntPtr.Zero','jobAttempted = false','jobConfigurationAttempted = false','.Dispose(','.PollContainment(')){Has $s $bad 0}
    },'Record(failure); throw;','CloseHandle(job); Record(failure); throw;'),
    @('preparation_creates_no_root_capture_pipe_worker_or_resume','prepare',{
        param($s) foreach($bad in @('CreateProcess(','CreatePipe(','PrepareStdioPairs(','OpenOriginalStdioPair(','new CompilerPipePair(','new AnonymousPipeServerStream(','new AnonymousPipeClientStream(','EnsureCollectors(','EnsureCollector(','new Thread(','ResumeThread(','creationAttempted = true','StartSuspended(','.Join(')){Has $s $bad 0};Has $s 'jobPrepared = true;'
    },'jobPrepared = true;','jobPrepared = true; StartSuspended();'),
    @('unattempted_job_rejects_every_inconsistent_retained_output','outcome',{
        param($s) Has $s 'if (!jobAttempted) { if (jobReturned || jobSucceeded || job != IntPtr.Zero) throw new InvalidOperationException("Unattempted compiler Job has inconsistent retained outputs."); return; }'
    },'jobReturned || jobSucceeded || job != IntPtr.Zero','jobReturned || jobSucceeded'),
    @('unknown_creation_and_mismatched_success_never_become_owned','outcome',{
        param($s) Has $s 'if (!jobReturned || job == new IntPtr(-1) || jobSucceeded != ValidHandle(job))';Has $s 'if (ValidHandle(job)) RequireOwnedJob();'
    },'!jobReturned || job == new IntPtr(-1)','job == new IntPtr(-1)'),
    @('owned_job_requires_known_success_and_rejects_unknown_or_completed_close','owned',{
        param($s) Has $s 'if (!jobAttempted || !jobReturned || !jobSucceeded || !ValidHandle(job) || (jobClose.Attempted && (!jobClose.Returned || jobClose.Succeeded)))'
    },'!jobClose.Returned || jobClose.Succeeded','jobClose.Succeeded'),
    @('known_job_ownership_does_not_require_configuration_success_for_cleanup','owned',{
        param($s) Has $s '!jobAttempted || !jobReturned || !jobSucceeded || !ValidHandle(job)';foreach($bad in @('jobConfigurationAttempted','jobConfigurationReturned','jobConfigurationSucceeded','jobPrepared','SuccessBoundary(')){Has $s $bad 0}
    },'!jobAttempted || !jobReturned','!jobConfigurationSucceeded || !jobAttempted || !jobReturned'),
    @('birth_requires_all_original_creation_preparation_and_configuration_states','prepared',{
        param($s) Has $s 'RequireOwnedJob(); if (!jobPreparationAttempted || !jobPrepared || !jobConfigurationAttempted || !jobConfigurationReturned || !jobConfigurationSucceeded)';Has $s 'PrepareBirthJob(' 0
    },'!jobConfigurationReturned || !jobConfigurationSucceeded','!jobConfigurationSucceeded'),
    @('empty_job_guard_requires_never_attempted_child_and_original_no_child_evidence','empty',{
        param($s) Order $s @('if (creationAttempted || !NoChildCreated())','Accounting value = JobAccounting();','if (value.active != 0 || value.total != 0 || value.terminated != 0)','ClockAndFaultBoundary();')
    },'creationAttempted || !NoChildCreated()','!NoChildCreated()'),
    @('accounting_queries_only_known_retained_original_job','accounting',{
        param($s) Has $s 'RequireOwnedJob(); Accounting value; Check(QueryInformationJobObject(job, 1, out value, (uint)Marshal.SizeOf(typeof(Accounting)), IntPtr.Zero), "Compiler private-Job accounting"); return value;'
    },'RequireOwnedJob();','/* original ownership guard omitted */'),
    @('start_requires_explicit_prepared_and_empty_job_before_any_birth_allocations','start',{
        param($s) $generic=Method $s 'public void StartSuspended('
        Order $generic @('Enter();','if (producerCompilerOwner != null)','StartSuspendedOriginal(null);','catch (Exception failure) { Record(failure); throw; }','finally { Leave(); }')
        $core=Method $s 'private void StartSuspendedOriginal('
        Order $core @('private void StartSuspendedOriginal(ProducerCompilerOwner owner) { ProducerCompilerBirthBoundary(owner);','if (startAttempted)','startAttempted = true; SuccessBoundary(); RequirePreparedJob(); AssertEmptyJobBeforeBirth();','PrepareStdioPairs();','creationAttempted = true;','bool created = CreateProcess(')
        $pairs=Method $s 'private void PrepareStdioPairs('
        Order $pairs @('if (inputPair != null || outputPair != null || errorPair != null)','outputPair = new CompilerPipePair(this, 1); OpenOriginalStdioPair(outputPair);','errorPair = new CompilerPipePair(this, 2); OpenOriginalStdioPair(errorPair);','inputPair = new CompilerPipePair(this, 0); OpenOriginalStdioPair(inputPair);','AssertOriginalStdioForBirth();')
        $acquire=Method $s 'private void OpenOriginalStdioPair('
        Order $acquire @('if (!IsOriginalPair(pair) || pair.Attempted)','pair.Attempted = true;','bool created = CreatePipe(out pair.Read.Raw, out pair.Write.Raw, ref security, 0);','pair.Error = Marshal.GetLastWin32Error(); pair.Succeeded = created; pair.Returned = true;','if (!created)','RequireOriginalPair(pair);')
    },'startAttempted = true; SuccessBoundary(); RequirePreparedJob(); AssertEmptyJobBeforeBirth();','startAttempted = true; SuccessBoundary(); AssertEmptyJobBeforeBirth();'),
    @('start_has_no_implicit_job_creation_configuration_or_preparation_fallback','start',{
        param($s) $s=Method $s 'private void StartSuspendedOriginal('
        foreach($bad in @('PrepareBirthJob(','CreateJobObject(','SetInformationJobObject(','jobAttempted = true','jobConfigurationAttempted = true','jobPrepared = true')){Has $s $bad 0};Has $s 'RequirePreparedJob();' 2
    },'if (startAttempted)','PrepareBirthJob(); if (startAttempted)'),
    @('cwd_is_refreshed_again_after_birth_allocation_before_final_native_boundary','start',{
        param($s) $s=Method $s 'private void StartSuspendedOriginal('
        Has $s 'startAttempted = true; SuccessBoundary(); RequirePreparedJob(); AssertEmptyJobBeforeBirth(); RegularAncestry(directory, true); HeldExecutableBoundary(); SuccessBoundary();';Has $s 'startup.startup.stderr = BorrowOriginalChildEndForBirth(errorPair); startup.attributes = launchAttributes; RegularAncestry(directory, true); HeldExecutableBoundary(); SuccessBoundary(); AssertOriginalStdioForBirth();';Has $s 'RegularAncestry(directory, true);' 2
    },'startup.attributes = launchAttributes; RegularAncestry(directory, true);','startup.attributes = launchAttributes;'),
    @('final_prepared_empty_job_and_clock_checks_are_adjacent_to_native_attempt','start',{
        param($s) $s=Method $s 'private void StartSuspendedOriginal('
        Has $s 'SuccessBoundary(); RequirePreparedJob(); AssertEmptyJobBeforeBirth(); ProducerCompilerBirthBoundary(owner); ClockAndFaultBoundary(); creationAttempted = true; bool created = CreateProcess('
    },'AssertEmptyJobBeforeBirth(); ProducerCompilerBirthBoundary(owner); ClockAndFaultBoundary(); creationAttempted = true;','AssertEmptyJobBeforeBirth(); ProducerCompilerBirthBoundary(owner); SuccessBoundary(); creationAttempted = true;'),
    @('birth_job_attribute_uses_only_the_original_retained_job','start',{
        param($s) $s=Method $s 'private void StartSuspendedOriginal('
        Has $s 'launchJobs = Marshal.AllocHGlobal(IntPtr.Size); Marshal.WriteIntPtr(launchJobs, job);';Has $s 'Check(UpdateProcThreadAttribute(launchAttributes, 0, new IntPtr(0x2000D), launchJobs, new IntPtr(IntPtr.Size), IntPtr.Zero, IntPtr.Zero), "Required compiler birth Job list");';Has $s '0x08080404, launchEnvironment, directory, ref startup, out creationOutputs'
    },'Marshal.WriteIntPtr(launchJobs, job);','Marshal.WriteIntPtr(launchJobs, IntPtr.Zero);'),
    @('whole_command_has_no_assignment_shell_named_job_or_handle_adoption_fallback','b',{
        param($s) foreach($bad in @('AssignProcessToJobObject(','OpenJobObject(','Process.Start(','UseShellExecute','GetProcessById(','OpenProcess(')){Has $s $bad 0};Has $s 'job = CreateJobObject(IntPtr.Zero, null);' 1
    },'job = CreateJobObject(IntPtr.Zero, null);','job = OpenJobObject(0, false, "shared");'),
    @('producer_retains_exact_command_before_first_job_native_operation','pstart',{
        param($s) Order $s @('startupDeadline.Begin(this);','contextSection.OpenForProducer(this);','controlChannel.OpenForProducer(this);','command = CompilerCommand.PrepareOwnedWithDeadline(inputs, executable, executableHash, arguments, directory, environment, startupDeadline, this, controlChannel, contextSection);','nativeStartAttempted = true;','command.PrepareBirthJob(); RequireAdmission(); command.StartSuspended();','RequireAdmission(); startCompleted = true;')
    },'command.PrepareBirthJob(); RequireAdmission(); command.StartSuspended();','command.StartSuspended(); command.PrepareBirthJob();'),
    @('no_child_proof_retains_zero_raw_outputs_and_known_failure_requirements','nochild',{
        param($s) Has $s 'bool allZero = creationOutputs.process == IntPtr.Zero && creationOutputs.thread == IntPtr.Zero && creationOutputs.pid == 0 && creationOutputs.tid == 0;';Has $s 'return !resumeAttempted && allZero && process == IntPtr.Zero && thread == IntPtr.Zero && (!creationAttempted || (creationReturned && !creationSucceeded));'
    },'creationReturned && !creationSucceeded','!creationSucceeded'),
    @('containment_handles_known_job_before_no_child_closure_without_success_gates','contain',{
        param($s) Order $s @('CleanupBoundary(); RequireJobOutcome();','if (ValidHandle(job)) Check(TerminateJobObject(job, 200), "Stop compiler-owned Job only");','bool rootClosed = NoChildCreated() || OriginalRootClosed(false);','bool closed = rootClosed && (!ValidHandle(job) || JobAccounting().active == 0) && CollectorsClosed();','CleanupBoundary(); closureProven = true; return true;');foreach($bad in @('RequirePreparedJob(','jobConfigurationSucceeded','SuccessBoundary(')){Has $s $bad 0}
    },'CleanupBoundary(); RequireJobOutcome();','CleanupBoundary();'),
    @('dispose_requires_original_no_child_or_root_and_job_closure_before_release','dispose',{
        param($s) Order $s @('if (!closureProven || !CollectorsClosed())','if (!releaseStarted) { RequireJobOutcome();','if (!(NoChildCreated() || OriginalRootClosed(false)) || (ValidHandle(job) && JobAccounting().active != 0))','releaseStarted = true;','CloseOwned(ref thread, threadClose); CloseOwned(ref process, processClose); CloseOwned(ref job, jobClose);','ownedInputs.ReleaseCommand(this); ownedInputsReleased = true;');Has $s 'RequirePreparedJob(' 0
    },'if (!releaseStarted) { RequireJobOutcome();','if (!releaseStarted) {'),
    @('independent_preload_descriptor_remains_deny_all','selector',{
        param($s) Has $s 'private static Descriptor SelectIndependentDescriptor() { return null; }'
    },'return null;','return new Descriptor();'),
    @('preload_open_blocker_remains_unconditional','preloadblock',{
        param($s) Has $s 'private static void DemandQualifiedPreloadOpen() { throw new InvalidOperationException("P/W preload Open requires independently qualified L/bootstrap, preload, environment and execution scope."); }'
    },'throw new InvalidOperationException("P/W preload Open requires independently qualified L/bootstrap, preload, environment and execution scope.");','return;'),
    @('first_producer_continuation_blocker_remains_unconditional','continuationblock',{
        param($s) Has $s 'private static void DemandQualifiedFirstProducerContinuation() { throw new InvalidOperationException("First P continuation remains closed until independent full-protocol native qualification."); }'
    },'throw new InvalidOperationException("First P continuation remains closed until independent full-protocol native qualification.");','return;'),
    @('payload_open_blocker_remains_unconditional','payloadblock',{
        param($s) Has $s 'private static void DemandQualifiedPayloadOpen() { throw new InvalidOperationException("Payload Open requires separately qualified P/W supervision and execution scope."); }'
    },'throw new InvalidOperationException("Payload Open requires separately qualified P/W supervision and execution scope.");','return;')
)
$expectedPairs=31;$expected=62
Assert-BirthJob ($rows.Count -eq $expectedPairs -and $expected -eq 2*$expectedPairs) 'Fixed case inventory differs.'
$executed=0;$results=[Collections.Generic.List[object]]::new()
foreach($row in $rows){
    Assert-BirthJob ($row.Count -eq 5) 'Fixed row shape differs.'
    $selected=$blocks[$row[1]];$check=$row[2]
    & $check $selected;$executed++;$results.Add([pscustomobject]@{name=$row[0];variant='original_source';passed=$true})
    $mutated=Replace-Tokens $selected $row[3] $row[4] -PowerShell:($row[1] -ceq 'loader');$rejected=$false
    try{& $check $mutated}catch{
        Assert-BirthJob ($_.Exception.Message.StartsWith('Birth Job preparation source contract:',[StringComparison]::Ordinal)) ('Unexpected negative-control exception: '+$row[0])
        $rejected=$true
    }
    Assert-BirthJob $rejected ('Weakening mutation survived: '+$row[0])
    $executed++;$results.Add([pscustomobject]@{name=$row[0];variant='mutated_source_rejected';passed=$true})
}
Assert-BirthJob ($executed -eq $expected -and $results.Count -eq $expected) 'Fixed cases did not complete.'
$before=[Collections.Generic.List[object]]::new();$after=[Collections.Generic.List[object]]::new()
foreach($name in $sources.Keys){
    $old=$sources[$name];$latest=Read-BirthJobSource $old.path
    Assert-BirthJob ($latest.sha256 -ceq $old.sha256) ('Subject changed during inspection: '+$name)
    $before.Add([pscustomobject]@{name=$name;path=$old.path;sha256=$old.sha256})
    $after.Add([pscustomobject]@{name=$name;path=$latest.path;sha256=$latest.sha256})
}
$testAfter=Read-BirthJobSource $PSCommandPath
Assert-BirthJob ($testAfter.sha256 -ceq $testBefore.sha256) 'Test changed during inspection.'
[pscustomobject]@{
    format='stratus-birth-job-preparation-source-contract-tests-v1'
    expected_pairs=$expectedPairs;expected=$expected;executed=$executed;passed=$executed;failed=0;skipped=0
    powershell_version=$PSVersionTable.PSVersion.ToString();powershell_edition=$PSVersionTable.PSEdition
    source_hashes_before=$before.ToArray();source_hashes_after=$after.ToArray()
    test_sha256_before=$testBefore.sha256;test_sha256_after=$testAfter.sha256;source_and_test_hashes_unchanged=$true
    source_tokens_and_directed_inert_mutations_only=$true
    loader_source_predicates_and_mutation_anchors_use_powershell_parser=$true
    target_compiled=$false;target_loaded=$false;target_lifecycle_executed=$false;native_qualified=$false
    target_powershell_defined=$false;target_powershell_executed=$false;bootstrap_loader_executed=$false
    filesystem_fixture_created=$false;csharp_semantics_proven=$false
    actual_job_created=$false;actual_job_configured=$false;actual_child_born=$false
    compiler_job_root_acknowledged=$false;producer_resume_authorized=$false
    independent_preload_descriptor_configured=$false;preload_open_authorized=$false;payload_open_authorized=$false
    physical_ownership_proven=$false;timing_qualified=$false;full_build_accepted=$false
    cases=$results.ToArray()
}|ConvertTo-Json -Depth 6 -Compress
