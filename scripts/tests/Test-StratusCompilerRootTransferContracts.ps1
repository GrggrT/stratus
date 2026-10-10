param()
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
# Fixed source data only. Never compile, load, evaluate or invoke a target.
# All callbacks below are literal test predicates; mutations remain token data.
# This suite cannot qualify native transfer, ACK, STOP, timing or ownership.
function Assert-RootTransfer {param([bool]$Okay,[string]$Message) if(-not $Okay){throw ('Compiler root transfer source contract: '+$Message)}}
function Read-RootTransferSource {
    param([string]$Path)
    $full=[IO.Path]::GetFullPath($Path)
    # Bound allocation and every delivered read; a FileInfo precheck alone
    # cannot stop ReadAllBytes from allocating after concurrent file growth.
    $stream=[IO.FileStream]::new($full,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{
        $length=$stream.Length
        Assert-RootTransfer ($length -gt 0 -and $length -le 1048576) 'Inspection file exceeds one MiB.'
        $raw=[byte[]]::new([int]$length);$offset=0
        while($offset -lt $raw.Length){
            $count=$stream.Read($raw,$offset,$raw.Length-$offset)
            Assert-RootTransfer ($count -gt 0) 'Inspection file ended before its bounded length.'
            $offset+=$count
        }
        Assert-RootTransfer ($stream.ReadByte() -eq -1 -and $stream.Length -eq $length) 'File length changed while reading.'
    }finally{$stream.Dispose()}
    $source=[Text.UTF8Encoding]::new($false,$true).GetString($raw)
    $hash=[Security.Cryptography.SHA256]::Create()
    try{$digest=[BitConverter]::ToString($hash.ComputeHash($raw)).Replace('-','').ToLowerInvariant()}finally{$hash.Dispose()}
    return [pscustomobject]@{path=$full;text=$source;sha256=$digest}
}
function Tokens {
    param([string]$Text)
    Assert-RootTransfer ($null -ne $Text -and $Text.Length -le 1048576) 'Token input exceeds its fixed character bound.'
    $pattern=@'
//[^\r\n]* | /\*.*?\*/ | @"(?:""|[^"])*" | "(?:\\.|[^"\\])*" | '(?:\\.|[^'\\])*'
| [A-Za-z_][A-Za-z_0-9]* | [0-9]+ | == | != | >= | <= | \+\+ | -- | && | \|\| | => | [^\s]
'@
    $result=[Collections.Generic.List[string]]::new()
    $options=[Text.RegularExpressions.RegexOptions]::Singleline -bor [Text.RegularExpressions.RegexOptions]::IgnorePatternWhitespace
    foreach($m in [regex]::Matches($Text,$pattern,$options,[TimeSpan]::FromSeconds(5))){
        if(-not $m.Value.StartsWith('//') -and -not $m.Value.StartsWith('/*')){Assert-RootTransfer ($result.Count -lt 262144) 'Token inspection bound exceeded.';$result.Add($m.Value)}
    }
    Assert-RootTransfer ($result.Count -le 262144) 'Token inspection bound exceeded.'
    return ,$result.ToArray()
}
function Find {
    param([string[]]$Source,[string]$Sequence)
    $wanted=Tokens $Sequence;Assert-RootTransfer ($wanted.Count -gt 0) 'Empty predicate.'
    $found=[Collections.Generic.List[int]]::new()
    for($i=0;$i -le $Source.Count-$wanted.Count;$i++){
        $same=$true
        for($j=0;$j -lt $wanted.Count;$j++){if($Source[$i+$j] -cne $wanted[$j]){$same=$false;break}}
        if($same){$found.Add($i)}
    }
    return ,$found.ToArray()
}
function Has {param([string[]]$Source,[string]$Sequence,[int]$Count=1) Assert-RootTransfer ((Find $Source $Sequence).Count -eq $Count) ('Sequence differs: '+$Sequence)}
function Order {
    param([string[]]$Source,[string[]]$Sequences)
    $last=-1
    foreach($sequence in $Sequences){$found=Find $Source $sequence;Assert-RootTransfer ($found.Count -eq 1 -and $found[0] -gt $last) ('Order differs: '+$sequence);$last=$found[0]}
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
    Assert-RootTransfer ($found.Count -eq 1) ('Missing or ambiguous block: '+$Signature)
    $open=$found[0]
    while($open -lt $Source.Count -and $Source[$open] -cne '{'){$open++}
    Assert-RootTransfer ($open -lt $Source.Count) 'Opening brace missing.'
    $depth=0
    for($i=$open;$i -lt $Source.Count;$i++){
        if($Source[$i] -ceq '{'){$depth++}elseif($Source[$i] -ceq '}'){$depth--}
        if($depth -eq 0){return ,([string[]]$Source[$found[0]..$i])}
    }
    throw 'Compiler root transfer source contract: Unbalanced source block.'
}
function Method {param([string[]]$Class,[string]$Signature) return ,(Block $Class $Signature -Direct)}
function Replace-Tokens {
    param([string[]]$Source,[string]$Needle,[string]$Replacement)
    $at=Find $Source $Needle;Assert-RootTransfer ($at.Count -eq 1) ('Mutation anchor differs: '+$Needle)
    $old=Tokens $Needle;$new=Tokens $Replacement;$result=[Collections.Generic.List[string]]::new()
    for($i=0;$i -lt $at[0];$i++){$result.Add($Source[$i])}
    foreach($token in $new){$result.Add($token)}
    for($i=$at[0]+$old.Count;$i -lt $Source.Count;$i++){$result.Add($Source[$i])}
    return ,$result.ToArray()
}
$sources=[ordered]@{}
foreach($name in @('Bootstrap','CompilerJobWire','CompilerRootWire','ProducerCompiler','ProducerJobTransfer','ProducerRootTransfer','ProducerInvocation','GuardianProgram','GuardianJobTransfer','GuardianRootTransfer','Producer','GuardianLaunch','ProducerProgram','ProducerTerminal','GuardianTerminal')){
    $sources[$name]=Read-RootTransferSource ([IO.Path]::Combine($PSScriptRoot,'..',('Stratus.RuntimeBuild.'+$name+'.cs')))
}
$testBefore=Read-RootTransferSource $PSCommandPath
$b=Block (Tokens $sources.Bootstrap.text) 'public sealed class CompilerCommand : IDisposable'
$wire=Block (Tokens $sources.CompilerRootWire.text) 'internal static class CompilerRootWire'
$descriptor=Block (Tokens $sources.CompilerRootWire.text) 'internal sealed class CompilerRootOffer'
$owner=Block (Tokens $sources.ProducerCompiler.text) 'internal sealed partial class ProducerCompilerOwner'
$pt=Block (Tokens $sources.ProducerRootTransfer.text) 'internal sealed partial class ProducerInvocation'
$po=Block (Tokens $sources.ProducerRootTransfer.text) 'internal sealed partial class ProducerCompilerOwner'
$wr=Block (Tokens $sources.GuardianRootTransfer.text) 'private sealed class GuardianRootTransfer'
$wj=Block (Tokens $sources.GuardianJobTransfer.text) 'private sealed class GuardianJobTransfer'
$w=Block (Tokens $sources.GuardianProgram.text) 'internal static partial class Program'
$control=Method $w 'private static void AdoptControl('
$pTerminal=Block (Tokens $sources.ProducerTerminal.text) 'internal sealed partial class ProducerInvocation'
$pTerminalOwner=Block (Tokens $sources.ProducerTerminal.text) 'internal sealed partial class ProducerCompilerOwner'
$wTerminal=Block (Tokens $sources.GuardianTerminal.text) 'internal static partial class Program'
$inv=Block (Tokens $sources.ProducerInvocation.text) 'internal sealed partial class ProducerInvocation'
$producer=Block (Tokens $sources.Producer.text) 'public sealed class DisposableProducerLaunch : IDisposable'
$launch=Block (Tokens $sources.GuardianLaunch.text) 'public sealed class GuardianLaunch : IDisposable'
$entry=Block (Tokens $sources.ProducerProgram.text) 'internal static class Program'
$blocks=@{
    wire=$wire;descriptor=$descriptor
    frame=Method $wire 'private static byte[] Frame('
    decimal=Method $wire 'private static ulong Decimal('
    handle=Method $wire 'private static IntPtr Handle('
    parse=Method $wire 'internal static CompilerRootOffer ParseOffer('
    exact=Method $wire 'internal static void RequireExact('
    jobObservation=Method $b 'internal void AssertOriginalJobOfferObservation('
    prebirth=Method $b 'private void AssertOriginalJobPrebirthAdmission('
    typedBirth=Method $b 'internal void StartProducerCompiler('
    publicBirth=Method $b 'public void StartSuspended('
    birthCore=Method $b 'private void StartSuspendedOriginal('
    publishRoot=Method $b 'internal void PublishOriginalCompilerRootOffer('
    rootObservation=Method $b 'internal void AssertOriginalRootOfferObservation('
    soleRoot=Method $b 'private void AssertSoleSuspendedCompilerRoot('
    resumeCore=Method $b 'private void ResumeOriginal('
    typedResume=Method $b 'internal void ResumeProducerCompiler('
    ownerRun=[string[]]@((Method $owner 'private void Run(')+(Method $pTerminalOwner 'private void PrepareLocalTerminal('))
    pPublish=Method $pt 'internal void PublishCompilerRootOffer('
    pOffer=Block $pt 'private sealed class RetainedCompilerRootOffer'
    pSend=Method $pt 'private void SendOriginalCompilerRootOffer('
    pRead=Method $pt 'private void ReceiveOriginalCompilerRootAck('
    pWait=Method $pt 'internal void WaitForCompilerRootAck('
    pAssertAck=Method $pt 'internal void AssertCompilerRootAck('
    ownerRoot=$po
    terminal=Method $po 'private static void DemandQualifiedCompilerTerminal('
    wRoot=$wr;wJob=$wj;wProgram=$w;wControl=$control
    wCtor=Method $wr 'internal GuardianRootTransfer('
    wContext=Method $wr 'private void AssertOriginalContext('
    wClaim=Method $wr 'internal void ClaimOffer('
    wRun=Method $wr 'private void Run('
    wGate=Method $wr 'private static void DemandQualifiedCompilerRootTransfer('
    wAliases=Method $wr 'private void AssertNoLocalAlias('
    wAck=Method $wr 'internal byte[] CopyAcknowledgementIfSettled('
    wClose=Method $wr 'internal void CloseAdmission('
    wSettled=Method $wr 'internal bool IsSettledForStop'
    wSettle=Method $wr 'internal bool SettleAfterProducerDeath('
    wDeath=Method $wr 'internal bool ObservePhysicalDeathAfterJobStop('
    jobClaim=Method $wj 'internal void AssertRootSourceForClaim('
    jobBorrow=Method $wj 'internal IntPtr BorrowOriginalJobForRoot('
    jobStop=Method $wj 'internal bool StopAfterProducerDeath('
    wStop=Method $w 'private static bool StopOriginalProducer('
    wMain=[string[]]@((Method $w 'private static int Main(')+$wTerminal+(Method $w 'private static void RequireOriginalProducerNormalExitForArmedDrain(')+(Method $wj 'internal void ProveNormalClosureAfterProducerExit(GuardianRootTransfer originalRootOwner)')+(Method $wr 'internal void ProveNormalClosureAfterProducerExit(GuardianJobTransfer originalOwner)')+(Method $w 'private static void DemandQualifiedNormalTerminalObservation('))
    wReader=Block $control 'controlReader = new Thread(delegate()'
    wWriter=[string[]]@((Block $control 'controlWriter = new Thread(delegate()')+(Method $wTerminal 'private static void SendNormalTerminalAcknowledgement('))
    invReader=[string[]]@((Method $inv 'private void StartReader(')+(Method $pTerminal 'private void ReceiveOriginalTerminalAcknowledgement('))
    invWriter=Method $inv 'private void StartWriter('
    pAbi=[string[]]@($b+$inv+$producer+$entry)
    wAbi=[string[]]@($launch+$w)
    helloAbi=[string[]]@($inv+$w)
}
# Historical case IDs retain their original phase/version labels. Predicates
# follow the actual terminal chain; this focused read set is not a compile closure.
$rows=@(
    @('root_wire_has_separate_derived_bound_and_roles','wire',{
        param($s) Has $s 'internal const int MaximumBytes = 31 + MaximumBindingBytes + 19 + 19 + 10 + 19 + 4 + 1;';Has $s 'private const string OfferPrefix = "STRATUS_COMPILER_ROOT_OFFER_V1 ";';Has $s 'private const string AckPrefix = "STRATUS_COMPILER_ROOT_ACK_V1 ";'
    },'MaximumBytes = 31 + MaximumBindingBytes + 19 + 19 + 10 + 19 + 4 + 1','MaximumBytes = 65536'),
    @('root_wire_binds_distinct_original_slots_pid_and_birth','frame',{
        param($s) Has $s 'sourceJob != sourceRoot && pid > 0 && birth > 0';Has $s 'CompilerJobWire.HandleText(sourceJob) + " " + CompilerJobWire.HandleText(sourceRoot) + " " + pid.ToString(CultureInfo.InvariantCulture) + " " + birth.ToString(CultureInfo.InvariantCulture) + "\n"'
    },'sourceJob != sourceRoot','sourceJob == sourceRoot'),
    @('root_wire_emits_only_bounded_printable_ascii_and_lf','frame',{
        param($s) Has $s 'Require(text.Length > 0 && text.Length <= MaximumBytes);';Has $s 'for (int i = 0; i < text.Length - 1; ++i) Require(text[i] >= 32 && text[i] <= 126);'
    },'text.Length <= MaximumBytes','text.Length <= 65536'),
    @('root_wire_decimal_is_positive_bounded_and_canonical','decimal',{
        param($s) Has $s 'UInt64.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out raw)';Has $s 'Require(raw > 0 && raw <= maximum && raw.ToString(CultureInfo.InvariantCulture) == value);'
    },'raw.ToString(CultureInfo.InvariantCulture) == value','raw.ToString(CultureInfo.InvariantCulture) != null'),
    @('root_wire_handle_uses_exact_architecture_bound_and_roundtrip','handle',{
        param($s) Has $s 'Decimal(value, IntPtr.Size == 8 ? (ulong)Int64.MaxValue : UInt32.MaxValue - 1UL)';Has $s 'Require(CompilerJobWire.HandleText(handle) == value);'
    },'Require(CompilerJobWire.HandleText(handle) == value);','/* handle canonicalization omitted */'),
    @('root_offer_parser_requires_four_fields_and_exact_roundtrip','parse',{
        param($s) Has $s 'Require(parts.Length == 4);';Has $s 'IntPtr job = Handle(parts[0]), root = Handle(parts[1]);';Has $s 'int pid = checked((int)Decimal(parts[2], Int32.MaxValue));';Has $s 'long birth = checked((long)Decimal(parts[3], Int64.MaxValue));';Order $s @('RequireExact(actual, Offer(binding, job, root, pid, birth));','return new CompilerRootOffer(job, root, pid, birth);')
    },'RequireExact(actual, Offer(binding, job, root, pid, birth));','/* original binding roundtrip omitted */'),
    @('root_offer_parser_is_bounded_and_rejects_nonascii','parse',{
        param($s) Has $s 'actual.Length <= MaximumBytes && actual[actual.Length - 1] == 10';Has $s 'for (int i = 0; i < actual.Length - 1; ++i) Require(actual[i] >= 32 && actual[i] <= 126);';Has $s 'Require(binding != null && text.StartsWith(prefix, StringComparison.Ordinal));'
    },'StringComparison.Ordinal','StringComparison.OrdinalIgnoreCase'),
    @('root_ack_comparison_requires_all_actual_bytes_and_equal_lengths','exact',{
        param($s) Has $s 'actual.Length == expected.Length && actual.Length <= MaximumBytes';Has $s 'for (int i = 0; i < actual.Length; ++i) Require(actual[i] == expected[i]);'
    },'actual[i] == expected[i]','actual[i] == actual[i]'),
    @('root_descriptor_retains_both_slots_and_native_identity','descriptor',{
        param($s) Has $s 'internal readonly IntPtr SourceJob, SourceRoot;';Has $s 'internal readonly int ProcessId;';Has $s 'internal readonly long Birth;';Has $s 'SourceJob = sourceJob; SourceRoot = sourceRoot; ProcessId = processId; Birth = birth;'
    },'SourceRoot = sourceRoot;','SourceRoot = sourceJob;'),
    @('job_lifetime_observation_remains_pinned_after_birth','jobObservation',{
        param($s) Has $s 'job != originalJob || jobClose.Attempted';Has $s 'Interlocked.CompareExchange(ref compilerJobTransferBound, 0, 0) != 1';Has $s 'RequirePreparedJob(); SuccessState();';Has $s 'creationAttempted' 0;Has $s 'NoChildCreated(' 0
    },'job != originalJob','false'),
    @('prebirth_admission_preserves_empty_never_used_job_and_actual_ack','prebirth',{
        param($s) Has $s 'AssertOriginalJobOfferObservation(owner, job);';Order $s @('owner.AssertBirthAdmission(this); AssertOriginalJobOfferObservation(owner, job);','AssertEmptyJobBeforeBirth(); owner.AssertBirthAdmission(this);')
    },'AssertEmptyJobBeforeBirth();','/* empty/never-used job omitted */'),
    @('typed_birth_requires_same_owner_ack_and_unconditional_gate','typedBirth',{
        param($s) Has $s 'owner == null || !Object.ReferenceEquals(producerCompilerOwner, owner)';Order $s @('Enter();','AssertOriginalJobPrebirthAdmission(owner);','owner.DemandQualifiedCompilerBirth(this);','StartSuspendedOriginal(owner);','finally { Leave(); }')
    },'owner.DemandQualifiedCompilerBirth(this);','/* qualification omitted */'),
    @('public_birth_stays_closed_to_compiler_in_p','publicBirth',{
        param($s) Has $s 'if (producerCompilerOwner != null) throw new InvalidOperationException("Compiler-in-P birth is unavailable before actual W Job handoff and qualification.");';Has $s 'StartSuspendedOriginal(null);'
    },'if (producerCompilerOwner != null)','if (false)'),
    @('shared_birth_rechecks_exact_ack_before_original_native_attempt','birthCore',{
        param($s) Has $s 'ProducerCompilerBirthBoundary(owner); ClockAndFaultBoundary(); creationAttempted = true; bool created = CreateProcess('
    },'ProducerCompilerBirthBoundary(owner); ClockAndFaultBoundary(); creationAttempted = true;','ClockAndFaultBoundary(); creationAttempted = true;'),
    @('shared_birth_retains_original_raw_create_outcome_before_admission','birthCore',{
        param($s) Has $s '0x08080404, launchEnvironment, directory, ref startup, out creationOutputs';Has $s 'creationSucceeded = created; creationReturned = true;';Order $s @('creationAttempted = true;','bool created = CreateProcess(','creationSucceeded = created; creationReturned = true;','if (!created)')
    },'creationReturned = true;','/* returned outcome lost */'),
    @('root_publication_requires_suspended_original_and_pins_before_send','publishRoot',{
        param($s) Has $s 'if (!started || resumeAttempted || resumed)';Has $s 'SuccessBoundary(); HeldExecutableBoundary(); RootIdentity(false); AssertSoleSuspendedCompilerRoot();';Order $s @('owner.DemandQualifiedCompilerRootTransfer(this);','Interlocked.CompareExchange(ref compilerRootTransferBound, 1, 0)','owner.PublishOriginalRoot(this, job, process, pid, birth);')
    },'Interlocked.CompareExchange(ref compilerRootTransferBound, 1, 0)','Interlocked.CompareExchange(ref compilerRootTransferBound, 0, 0)'),
    @('root_lifetime_observation_binds_native_identity_and_retained_handles','rootObservation',{
        param($s) Has $s 'AssertOriginalJobOfferObservation(owner, originalJob);';Has $s 'RequireOriginalCreation();';Has $s '!started || !identityEstablished || process != originalRoot || processClose.Attempted || threadClose.Attempted || pid != originalPid || birth <= 0 || birth != originalBirth';Has $s 'RootWait(' 0;Has $s 'ResumeThread(' 0
    },'process != originalRoot','false'),
    @('suspended_root_requires_only_its_original_birth_job','soleRoot',{
        param($s) Has $s 'RootWait() != WaitTimeout || value.active != 1 || value.total != 1 || value.terminated != 0'
    },'value.total != 1','false'),
    @('actual_compiler_worker_keeps_phase_until_result_and_closed_terminal','ownerRun',{
        param($s) $run=Block $s 'private void Run('
        Has $run 'while (prepared.PollResult() == null) { AssertContinuationAdmission(prepared); Thread.Sleep(10); }'
        Has $run 'finally { if (Interlocked.CompareExchange(ref failed, 0, 0) != 0) Interlocked.Exchange(ref phase, 0); Interlocked.Exchange(ref workerSettled, 1); }'
        Order $run @('WaitForOriginalCompilerJobAck(prepared);','PrepareTerminalArtifactsBeforeBirth(prepared);','prepared.StartProducerCompiler(this);','prepared.PublishOriginalCompilerRootOffer(this);','WaitForOriginalCompilerRootAck(prepared);','prepared.ResumeProducerCompiler(this);','while (prepared.PollResult() == null)','DemandQualifiedCompilerTerminal();','PrepareLocalTerminal(prepared);')
        Has $run 'return 0;' 0;Has $run '.Join(' 0
        $local=Block $s 'private void PrepareLocalTerminal('
        Order $local @('ProducerCompilerClosure closure = original.SealProducerLocalClosure(this);','Require(closure != null && Interlocked.CompareExchange(ref terminalClosure, closure, null) == null);','Require(Interlocked.CompareExchange(ref phase, 3, 2) == 2);','original.ReleaseProducerInputRegistration(this);','invocation.RecaptureProducerTerminalInputs(this, closure);','terminalArtifacts.CaptureAfterCompilerClosure(closure);','invocation.ReleaseProducerTerminalInputs(this, closure);','Require(Interlocked.CompareExchange(ref terminalLocalPrepared, 1, 0) == 0);')
        Has $local 'Interlocked.Exchange(ref phase, 0)' 0
    },'WaitForOriginalCompilerRootAck(prepared);','/* actual root ACK omitted */'),
    @('root_invocation_publication_binds_actual_job_owner_and_command','pPublish',{
        param($s) Has $s 'job != null && job.SourceJob == sourceJob && Object.ReferenceEquals(job.Owner, owner) && Object.ReferenceEquals(job.Command, command)';Order $s @('AssertCompilerJobAck(owner, command);','command.AssertOriginalRootOfferObservation(owner, sourceJob, sourceRoot, pid, birth);','Interlocked.CompareExchange(ref compilerRootOfferAttempted, 1, 0)','Interlocked.CompareExchange(ref compilerRootOffer, prepared, null)')
    },'job.SourceJob == sourceJob','true'),
    @('root_retained_frame_and_expected_ack_use_same_original_binding','pOffer',{
        param($s) Has $s 'internal readonly CompilerJobOffer Job;';Has $s 'Bytes = CompilerRootWire.Offer(job.Binding, job.SourceJob, root, pid, birth);';Has $s 'ExpectedAck = CompilerRootWire.Acknowledgement(job.Binding, job.SourceJob, root, pid, birth);'
    },'ExpectedAck = CompilerRootWire.Acknowledgement(job.Binding, job.SourceJob, root, pid, birth);','ExpectedAck = CompilerRootWire.Offer(job.Binding, job.SourceJob, root, pid, birth);'),
    @('root_writer_is_original_single_thread_attempt_before_bytes','pSend',{
        param($s) Has $s 'Require(Object.ReferenceEquals(Thread.CurrentThread, writerThread));';Order $s @('AssertRootOfferObservation(offer); Require(Interlocked.CompareExchange(ref compilerRootOfferWriteAttempted, 1, 0) == 0);','outputStream.Write(offer.Bytes, 0, offer.Bytes.Length);','outputStream.Flush();','AssertRootOfferObservation(offer); Require(Interlocked.CompareExchange(ref compilerRootOfferWritten, 1, 0) == 0);')
    },'Require(Interlocked.CompareExchange(ref compilerRootOfferWriteAttempted, 1, 0) == 0);','/* write attempt omitted */'),
    @('root_reader_remains_pending_through_birth_and_rejects_early_ack','pRead',{
        param($s) Has $s 'Require(Object.ReferenceEquals(Thread.CurrentThread, readerThread));';Has $s 'Require(firstCount == 1);';Has $s 'offer != null';Has $s 'Require(offer != null && writeAttemptedAtRead == 1);';Order $s @('int firstCount = inputStream.Read(first, 0, 1);','RetainedCompilerRootOffer offer = Interlocked.CompareExchange(ref compilerRootOffer, null, null);','byte[] actual = new byte[offer.ExpectedAck.Length];');Has $s 'WaitForPublishedCompilerRootOffer(' 0
    },'Require(firstCount == 1);','Require(firstCount >= 0);'),
    @('root_reader_requires_exact_bytes_then_publishes_without_waiting_writer','pRead',{
        param($s) Has $s 'actual.Length <= CompilerRootWire.MaximumBytes';Has $s 'Require(count > 0 && count <= actual.Length - used);';Order $s @('CompilerRootWire.RequireExact(actual, offer.ExpectedAck);','Require(Interlocked.CompareExchange(ref compilerRootAckReceived, 1, 0) == 0);');Has $s 'Thread.Sleep(' 0;Has $s 'compilerRootOfferWritten' 0
    },'CompilerRootWire.RequireExact(actual, offer.ExpectedAck);','/* exact ACK skipped */'),
    @('compiler_worker_waits_actual_received_and_written_root_facts','pWait',{
        param($s) Has $s 'Object.ReferenceEquals(offer.Job.Owner, owner) && Object.ReferenceEquals(offer.Job.Command, command)';Has $s 'while (Interlocked.CompareExchange(ref compilerRootAckReceived, 0, 0) != 1 || Interlocked.CompareExchange(ref compilerRootOfferWritten, 0, 0) != 1)'
    },'Interlocked.CompareExchange(ref compilerRootAckReceived, 0, 0) != 1','false'),
    @('fresh_root_ack_check_requires_original_backlinks_and_both_facts','pAssertAck',{
        param($s) Has $s 'Object.ReferenceEquals(offer.Job.Owner, owner) && Object.ReferenceEquals(offer.Job.Command, command)';Has $s 'Interlocked.CompareExchange(ref compilerRootOfferWriteAttempted, 0, 0) == 1 && Interlocked.CompareExchange(ref compilerRootOfferWritten, 0, 0) == 1 && Interlocked.CompareExchange(ref compilerRootAckReceived, 0, 0) == 1';Has $s 'AssertRootOfferObservation(offer);'
    },'Interlocked.CompareExchange(ref compilerRootAckReceived, 0, 0) == 1','true'),
    @('typed_compiler_resume_requires_actual_ack_and_closed_qualification','typedResume',{
        param($s) Has $s 'owner == null || !Object.ReferenceEquals(producerCompilerOwner, owner)';Order $s @('Enter();','owner.AssertContinuationAdmission(this);','owner.DemandQualifiedCompilerContinuation(this);','ResumeOriginal(null);','finally { Leave(); }')
    },'owner.DemandQualifiedCompilerContinuation(this);','/* continuation qualifier omitted */'),
    @('shared_resume_keeps_suspend_count_inspection_and_final_boundary','resumeCore',{
        param($s) Has $s 'ProducerContinuationBoundary(guardian); CompilerContinuationBoundary(); ClockAndFaultBoundary(); resumeAttempted = true;';Order $s @('uint previous = SuspendThread(thread);','if (previous != 1)','uint restored = ResumeThread(thread);','if (restored != 2)','ClockAndFaultBoundary(); resumeAttempted = true;','uint count = ResumeThread(thread);','if (count != 1)')
    },'if (previous != 1)','if (previous != 0)'),
    @('root_continuation_owner_calls_real_invocation_ack','ownerRoot',{
        param($s) Has $s 'internal void AssertContinuationAdmission(CompilerCommand original) { AssertCommandAdmission(original); invocation.AssertCompilerRootAck(this, original); AssertCommandAdmission(original); }'
    },'invocation.AssertCompilerRootAck(this, original);','/* actual invocation root ACK omitted */'),
    @('unqualified_compiler_terminal_never_returns_success','terminal',{
        param($s) Has $s 'throw new InvalidOperationException("Compiler completion is not qualified terminal success: P/W physical exit, EOF, input recapture/release and output writer acceptance remain unavailable.");'
    },'throw new InvalidOperationException("Compiler completion is not qualified terminal success: P/W physical exit, EOF, input recapture/release and output writer acceptance remain unavailable.");','return;')
)
# The preceding 30 P/wire case IDs and order are preserved; the worker row
# follows immutable terminal phase 3. All predicates use only fixed token data.
Assert-RootTransfer ($rows.Count -eq 30) 'Original P/wire case inventory changed.'
$rows+=@(
    @('w_root_owner_retains_original_transfer_job_context_and_binding','wCtor',{
        param($s) Has $s 'transfer != IntPtr.Zero && transfer == producerTransfer && jobOwner != null && Object.ReferenceEquals(jobTransfer, jobOwner) && context != null && Object.ReferenceEquals(contextCapture.AdmittedMetadata, context) && originalBinding != null';Has $s 'originalTransfer = transfer; originalJobOwner = jobOwner; originalContext = context; binding = originalBinding;'
    },'Object.ReferenceEquals(contextCapture.AdmittedMetadata, context)','true'),
    @('w_root_worker_context_checks_same_owner_backlinks','wContext',{
        param($s) Has $s 'Require(Object.ReferenceEquals(rootTransfer, this) && Object.ReferenceEquals(jobTransfer, originalJobOwner) && Object.ReferenceEquals(contextCapture.AdmittedMetadata, originalContext) && originalTransfer == producerTransfer);'
    },'Object.ReferenceEquals(jobTransfer, originalJobOwner)','true'),
    @('w_root_claim_requires_actual_wire_job_write_attempt_and_single_cas','wClaim',{
        param($s) Order $s @('Stratus.RuntimeBuild.CompilerRootOffer parsed = Stratus.RuntimeBuild.CompilerRootWire.ParseOffer(actual, binding);','Require(Interlocked.CompareExchange(ref jobAckWriteAttempted, 0, 0) == 1);','originalJobOwner.AssertRootSourceForClaim(parsed.SourceJob);','Require(Interlocked.CompareExchange(ref stopClosed, 0, 0) == 0 && Interlocked.CompareExchange(ref phase, 1, 0) == 0);')
    },'Interlocked.CompareExchange(ref phase, 1, 0)','Interlocked.CompareExchange(ref phase, 1, 1)'),
    @('w_root_claim_is_retained_before_copy_thread_construction_and_start','wClaim',{
        param($s) Order $s @('Interlocked.CompareExchange(ref phase, 1, 0) == 0','offered = parsed; originalOffer = (byte[])actual.Clone();','worker = new Thread(Run); worker.IsBackground = true;','workerStartAttempted = true; worker.Start(); workerStartReturned = true;');Has $s 'Interlocked.Exchange(ref phase, 0)' 0
    },'workerStartAttempted = true; worker.Start(); workerStartReturned = true;','worker.Start(); workerStartAttempted = true; workerStartReturned = true;'),
    @('w_root_claim_does_not_wait_for_writer_postflush_or_block_reader','wClaim',{
        param($s) Has $s 'Require(Interlocked.CompareExchange(ref jobAckWriteAttempted, 0, 0) == 1);';foreach($bad in @('jobAckWritten','Thread.Sleep(','.Join(','WaitForSingleObject(','DuplicateHandle(')){Has $s $bad 0}
    },'offered = parsed;','Thread.Sleep(10); offered = parsed;'),
    @('w_root_claim_matches_verified_settled_original_job_source_slot','jobClaim',{
        param($s) Has $s 'Object.ReferenceEquals(jobTransfer, this) && Interlocked.CompareExchange(ref stopClosed, 0, 0) == 0 && Interlocked.CompareExchange(ref phase, 0, 0) == 2 && Interlocked.CompareExchange(ref verified, 0, 0) == 1 && sourceHandle == offeredSourceJob && originalOffer != null && acknowledgement != null';Has $s 'jobAckWritten' 0
    },'sourceHandle == offeredSourceJob','true'),
    @('w_root_borrows_held_original_job_without_second_job_duplication','jobBorrow',{
        param($s) Order $s @('Require(originalRootOwner != null && Object.ReferenceEquals(rootTransfer, originalRootOwner));','AssertRootSourceForClaim(offeredSourceJob);','Require(duplicateAttempted && duplicateReturned && duplicateSucceeded && ValidHandle(localJob));','return localJob;');Has $s 'DuplicateHandle(' 0
    },'return localJob;','return offeredSourceJob;'),
    @('w_root_worker_waits_actual_job_ack_postflush_before_native_use','wRun',{
        param($s) Order $s @('while (Interlocked.CompareExchange(ref jobAckWritten, 0, 0) == 0) { AssertControlWorker(ref workerTick); Thread.Sleep(10); }','Require(Interlocked.CompareExchange(ref jobAckWriteAttempted, 0, 0) == 1 && jobAckWriteReturned);','originalLocalJob = originalJobOwner.BorrowOriginalJobForRoot(this, offered.SourceJob);','duplicateAttempted = true;')
    },'while (Interlocked.CompareExchange(ref jobAckWritten, 0, 0) == 0)','while (false)'),
    @('w_root_worker_requires_exact_original_offer_binding_before_transfer','wRun',{
        param($s) Order $s @('Stratus.RuntimeBuild.CompilerRootWire.RequireExact(originalOffer, Stratus.RuntimeBuild.CompilerRootWire.Offer(binding, offered.SourceJob, offered.SourceRoot, offered.ProcessId, offered.Birth));','originalLocalJob = originalJobOwner.BorrowOriginalJobForRoot(this, offered.SourceJob);','DemandQualifiedCompilerRootTransfer();','preCompareAttempted = true;')
    },'offered.SourceJob, offered.SourceRoot, offered.ProcessId, offered.Birth));','offered.SourceJob, offered.SourceJob, offered.ProcessId, offered.Birth));'),
    @('w_root_transfer_qualification_gate_is_unconditional','wGate',{
        param($s) Has $s 'private static void DemandQualifiedCompilerRootTransfer() { throw new InvalidOperationException("W compiler root transfer is not independently qualified."); }'
    },'throw new InvalidOperationException("W compiler root transfer is not independently qualified.");','return;'),
    @('w_root_duplicate_uses_exact_p_table_minimum_rights_and_retained_raw_result','wRun',{
        param($s) Order $s @('duplicateAttempted = true;','duplicateSucceeded = DuplicateHandle(originalTransfer, offered.SourceRoot, GetCurrentProcess(), out localRoot, 0x101000, false, 0);','duplicateError = Marshal.GetLastWin32Error(); duplicateReturned = true;','Require(duplicateSucceeded && ValidHandle(localRoot));');Has $s 'DuplicateHandle(' 1
    },'out localRoot, 0x101000, false, 0','out localRoot, 0x101001, false, 0'),
    @('w_root_has_no_pid_reopen_handle_close_or_direct_stop_rights_path','wRoot',{
        param($s) foreach($bad in @('OpenProcess(','GetProcessById(','TerminateProcess(','TerminateJobObject(','SuspendThread(','ResumeThread(','CloseHandle(','DUPLICATE_SAME_ACCESS','DUPLICATE_CLOSE_SOURCE')){Has $s $bad 0};Has $s 'out localRoot, 0x101000, false, 0'
    },'Require(duplicateSucceeded && ValidHandle(localRoot));','OpenProcess(offered.ProcessId); Require(duplicateSucceeded && ValidHandle(localRoot));'),
    @('w_root_original_p_identity_comparison_brackets_duplicate','wRun',{
        param($s) Order $s @('preCompareSucceeded = CompareObjectHandles(producer, originalTransfer);','preCompareError = Marshal.GetLastWin32Error(); preCompareReturned = true; Require(preCompareSucceeded);','duplicateSucceeded = DuplicateHandle(','duplicateReturned = true;','postCompareSucceeded = CompareObjectHandles(producer, originalTransfer);','postCompareError = Marshal.GetLastWin32Error(); postCompareReturned = true; Require(postCompareSucceeded);')
    },'Require(postCompareSucceeded);','/* original P identity recheck omitted */'),
    @('w_root_rejects_job_process_pipe_context_and_stdio_handle_aliases','wAliases',{
        param($s) Has $s 'Require(localRoot != producer && localRoot != producerTransfer && localRoot != originalLocalJob && localRoot != controlReadHandle && localRoot != controlWriteHandle && localRoot != contextSection && localRoot != GetStdHandle(-10) && localRoot != GetStdHandle(-11) && localRoot != GetStdHandle(-12));'
    },'localRoot != originalLocalJob','true'),
    @('w_root_inheritance_and_protect_close_flags_are_observed_and_rejected','wRun',{
        param($s) Order $s @('flagsAttempted = true; flagsSucceeded = GetHandleInformation(localRoot, out observedFlags);','flagsError = Marshal.GetLastWin32Error(); flagsReturned = true;','Require(flagsSucceeded && (observedFlags & 3) == 0);')
    },'(observedFlags & 3) == 0','(observedFlags & 2) == 0'),
    @('w_root_pid_is_original_native_identity_not_p_or_w','wRun',{
        param($s) Has $s 'pidAttempted = true; observedPid = GetProcessId(localRoot); pidReturned = true;';Has $s 'Require(observedPid == (uint)offered.ProcessId && observedPid != producerPid && observedPid != GetCurrentProcessId());'
    },'observedPid == (uint)offered.ProcessId','observedPid > 0'),
    @('w_root_native_birth_is_exact_and_sandwiched_by_unsignaled_waits','wRun',{
        param($s) Order $s @('beforeWait = WaitForSingleObject(localRoot, 0); beforeWaitReturned = true;','Require(beforeWait == WaitTimeout);','timesSucceeded = GetProcessTimes(localRoot, out created, out exited, out kernel, out user);','timesError = Marshal.GetLastWin32Error(); timesReturned = true;','Require(timesSucceeded && created > 0 && created == offered.Birth);','afterWait = WaitForSingleObject(localRoot, 0); afterWaitReturned = true;','Require(afterWait == WaitTimeout);')
    },'created == offered.Birth','created > 0'),
    @('w_root_live_process_never_infers_defined_exit_time','wRun',{
        param($s) Has $s 'Require(timesSucceeded && created > 0 && created == offered.Birth);';foreach($bad in @('exited ==','exited !=','exited >','exited <')){Has $s $bad 0}
    },'created == offered.Birth);','created == offered.Birth && exited == 0);'),
    @('w_root_image_is_native_bounded_and_exact_original_context_compiler_role','wRun',{
        param($s) Has $s 'image = new StringBuilder(32768); imageCharacters = (uint)image.Capacity;';Order $s @('imageAttempted = true; imageSucceeded = QueryFullProcessImageName(localRoot, 0, image, ref imageCharacters);','imageError = Marshal.GetLastWin32Error(); imageReturned = true;','Require(imageSucceeded && imageCharacters > 0 && imageCharacters < image.Capacity && String.Equals(image.ToString(), originalContext.CopyCompilerRecord().Path, StringComparison.OrdinalIgnoreCase));')
    },'originalContext.CopyCompilerRecord().Path','image.ToString()'),
    @('w_root_membership_uses_the_same_borrowed_original_job','wRun',{
        param($s) Order $s @('Require(originalJobOwner.BorrowOriginalJobForRoot(this, offered.SourceJob) == originalLocalJob);','membershipAttempted = true; membershipSucceeded = IsProcessInJob(localRoot, originalLocalJob, out member);','membershipError = Marshal.GetLastWin32Error(); membershipReturned = true;','Require(membershipSucceeded && member);')
    },'IsProcessInJob(localRoot, originalLocalJob, out member)','IsProcessInJob(localRoot, IntPtr.Zero, out member)'),
    @('w_root_job_accounting_requires_exact_one_total_one_active_zero_terminated','wRun',{
        param($s) Has $s 'accountingSucceeded = QueryAccounting(originalLocalJob, 1, out rootAccounting, (uint)Marshal.SizeOf(typeof(Accounting)), out accountingBytes);';Has $s 'Require(accountingSucceeded && accountingBytes == (uint)Marshal.SizeOf(typeof(Accounting)) && rootAccounting.active == 1 && rootAccounting.total == 1 && rootAccounting.terminated == 0);'
    },'rootAccounting.total == 1','rootAccounting.total >= 1'),
    @('w_root_ack_bytes_are_bound_only_after_native_verification','wRun',{
        param($s) Order $s @('Require(membershipSucceeded && member);','rootAccounting.active == 1 && rootAccounting.total == 1 && rootAccounting.terminated == 0','Require(afterWait == WaitTimeout); AssertControlWorker(ref workerTick); AssertOriginalContext();','acknowledgement = Stratus.RuntimeBuild.CompilerRootWire.Acknowledgement(binding, offered.SourceJob, offered.SourceRoot, offered.ProcessId, offered.Birth);','Interlocked.Exchange(ref verified, 1);')
    },'Acknowledgement(binding, offered.SourceJob, offered.SourceRoot, offered.ProcessId, offered.Birth)','Acknowledgement(binding, offered.SourceJob, offered.SourceJob, offered.ProcessId, offered.Birth)'),
    @('w_root_worker_settlement_is_last_action_even_after_verification_failure','wRun',{
        param($s) Has $s 'catch (Exception) { Interlocked.Exchange(ref controlFailed, 1); } finally { Interlocked.Exchange(ref phase, 2); }';Has $s 'Interlocked.Exchange(ref phase, 2);' 1;Has $s 'localRoot = IntPtr.Zero' 0
    },'finally { Interlocked.Exchange(ref phase, 2); }','finally { Interlocked.Exchange(ref phase, 2); QueryAccounting(originalLocalJob); }'),
    @('w_root_ack_copy_requires_settled_verified_open_state_and_defensive_copy','wAck',{
        param($s) Has $s 'if (Interlocked.CompareExchange(ref stopClosed, 0, 0) != 0 || Interlocked.CompareExchange(ref phase, 0, 0) != 2 || Interlocked.CompareExchange(ref verified, 0, 0) != 1) return null;';Has $s 'Require(acknowledgement != null); byte[] result = (byte[])acknowledgement.Clone(); return Interlocked.CompareExchange(ref stopClosed, 0, 0) == 0 ? result : null;';Has $s 'QueryAccounting(' 0;Has $s 'WaitForSingleObject(' 0
    },'Interlocked.CompareExchange(ref phase, 0, 0) != 2','false'),
    @('w_root_stop_closes_future_claims_without_erasing_an_existing_claim','wClose',{
        param($s) Has $s 'internal void CloseAdmission() { Interlocked.Exchange(ref stopClosed, 1); Interlocked.CompareExchange(ref phase, 3, 0); }'
    },'Interlocked.CompareExchange(ref phase, 3, 0);','Interlocked.Exchange(ref phase, 3);'),
    @('w_root_stop_settled_state_requires_closed_and_settled_or_prevented_claim','wSettled',{
        param($s) Has $s 'int settled = Interlocked.CompareExchange(ref phase, 0, 0);';Has $s 'return Interlocked.CompareExchange(ref stopClosed, 0, 0) == 1 && (settled == 2 || settled == 3);'
    },'settled == 2 || settled == 3','settled == 1 || settled == 2 || settled == 3'),
    @('w_root_pending_worker_settles_only_after_physical_p_death_on_original_clock','wSettle',{
        param($s) Has $s 'Require(originalStopClock != null && identityEstablished && ObserveProducer());';Has $s 'while (Interlocked.CompareExchange(ref phase, 0, 0) == 1) { if (originalStopClock.ElapsedMilliseconds >= StopMilliseconds) return false; Thread.Sleep(10); }';Has $s 'return originalStopClock.ElapsedMilliseconds < StopMilliseconds && IsSettledForStop;';Has $s '.Join(' 0
    },'while (Interlocked.CompareExchange(ref phase, 0, 0) == 1)','while (false)'),
    @('w_actual_stop_closes_both_admissions_before_identity_or_native_work','wStop',{
        param($s) Order $s @('Interlocked.Exchange(ref controlFailed, 1);','if (jobTransfer != null) jobTransfer.CloseAdmission();','if (rootTransfer != null) rootTransfer.CloseAdmission();','if (Interlocked.CompareExchange(ref stopAttempted, 1, 0) != 0) return false;','stopClock = Stopwatch.StartNew();','if (!identityEstablished) return false;','bool dead = ObserveProducer();')
    },'if (rootTransfer != null) rootTransfer.CloseAdmission();','/* root admission remained open */'),
    @('w_actual_stop_requires_physical_p_death_not_terminate_return','wStop',{
        param($s) Has $s 'if (!producerStopSucceeded && !ObserveProducer()) return false;';Order $s @('producerStopSucceeded = TerminateProcess(producer, 203);','while (!dead && stopClock.ElapsedMilliseconds < StopMilliseconds)','dead = ObserveProducer(); if (dead) break;','if (!dead || stopClock.ElapsedMilliseconds >= StopMilliseconds) return false;','if (jobTransfer != null && !jobTransfer.SettleAfterProducerDeath(stopClock)) return false;');Has $s 'return producerStopSucceeded;' 0
    },'if (!dead || stopClock.ElapsedMilliseconds >= StopMilliseconds) return false;','/* physical P death not proved */'),
    @('w_job_stop_requires_both_workers_settled_before_type_query_or_mutation','jobStop',{
        param($s) Order $s @('Require(originalStopClock != null && identityEstablished && ObserveProducer() && IsSettledForStop);','Require(rootTransfer == null || rootTransfer.IsSettledForStop);','stopTypeSucceeded = QueryLimits(localJob, 9, out stopTypeLimits,','terminateSucceeded = TerminateJobObject(localJob, 203);')
    },'Require(rootTransfer == null || rootTransfer.IsSettledForStop);','/* root worker may still use held Job */'),
    @('w_exact_job_cleanup_publishes_only_after_type_termination_and_zero_active','jobStop',{
        param($s) Order $s @('if (!stopTypeSucceeded || stopTypeBytes != (uint)Marshal.SizeOf(typeof(ExtendedLimit)) || originalStopClock.ElapsedMilliseconds >= StopMilliseconds) return false;','Require(!terminateAttempted);','terminateSucceeded = TerminateJobObject(localJob, 203);','if (!terminateSucceeded || originalStopClock.ElapsedMilliseconds >= StopMilliseconds) return false;','if (!stopQuerySucceeded || stoppedAccountingBytes != (uint)Marshal.SizeOf(typeof(Accounting))) return false;','if (stoppedAccounting.active == 0) { if (originalStopClock.ElapsedMilliseconds >= StopMilliseconds) return false; Interlocked.Exchange(ref exactJobStopped, 1); return true; }')
    },'if (stoppedAccounting.active == 0)','if (true)'),
    @('w_actual_stop_settles_job_and_root_before_exact_job_mutation','wStop',{
        param($s) Order $s @('if (jobTransfer != null && !jobTransfer.SettleAfterProducerDeath(stopClock)) return false;','if (rootTransfer != null && !rootTransfer.SettleAfterProducerDeath(stopClock)) return false;','if (jobTransfer != null && !jobTransfer.StopAfterProducerDeath(stopClock)) return false;')
    },'if (rootTransfer != null && !rootTransfer.SettleAfterProducerDeath(stopClock)) return false;','/* root worker settlement omitted */'),
    @('w_actual_stop_proves_known_root_death_only_after_exact_job_cleanup','wStop',{
        param($s) Order $s @('if (jobTransfer != null && !jobTransfer.StopAfterProducerDeath(stopClock)) return false;','if (rootTransfer != null && !rootTransfer.ObservePhysicalDeathAfterJobStop(stopClock)) return false;','return stopClock.ElapsedMilliseconds < StopMilliseconds;');Has $s 'return true;' 0
    },'if (rootTransfer != null && !rootTransfer.ObservePhysicalDeathAfterJobStop(stopClock)) return false;','/* root death proof omitted */'),
    @('w_root_physical_death_path_requires_p_death_and_both_settled_owners','wDeath',{
        param($s) Has $s 'Require(originalStopClock != null && identityEstablished && ObserveProducer() && IsSettledForStop && originalJobOwner.IsSettledForStop);';Has $s 'if (originalStopClock.ElapsedMilliseconds >= StopMilliseconds) return false;'
    },'IsSettledForStop && originalJobOwner.IsSettledForStop','IsSettledForStop'),
    @('w_claimed_no_local_copy_still_requires_exact_job_cleanup_not_no_birth','wDeath',{
        param($s) Order $s @('if (settled == 3) return true;','Require(settled == 2);','Require(originalJobOwner.ExactJobStopped && offered != null);','if (!duplicateAttempted) return true;')
    },'Require(originalJobOwner.ExactJobStopped && offered != null);','/* no local root copy misread as no child */'),
    @('w_root_unknown_failed_or_invalid_duplicate_never_means_absence','wDeath',{
        param($s) Has $s 'if (!duplicateReturned || !duplicateSucceeded || !ValidHandle(localRoot)) return false;';Order $s @('if (!duplicateAttempted) return true;','if (!duplicateReturned || !duplicateSucceeded || !ValidHandle(localRoot)) return false;','AssertNoLocalAlias();')
    },'if (!duplicateReturned || !duplicateSucceeded || !ValidHandle(localRoot)) return false;','if (!duplicateReturned || !duplicateSucceeded || !ValidHandle(localRoot)) return true;'),
    @('w_known_duplicate_cleanup_does_not_require_successful_verification','wDeath',{
        param($s) Has $s 'verified' 0;Has $s 'duplicateSucceeded' 1;Has $s 'AssertNoLocalAlias();';Has $s 'stopPid = GetProcessId(localRoot);';Has $s 'localRoot = IntPtr.Zero' 0
    },'AssertNoLocalAlias();','Require(verified == 1); AssertNoLocalAlias();'),
    @('w_root_death_rechecks_original_pid_not_p_or_w','wDeath',{
        param($s) Has $s 'stopPidAttempted = true; stopPid = GetProcessId(localRoot); stopPidReturned = true;';Has $s 'if (stopPid != (uint)offered.ProcessId || stopPid == producerPid || stopPid == GetCurrentProcessId()) return false;'
    },'stopPid != (uint)offered.ProcessId','false'),
    @('w_root_death_requires_signaled_wait_exact_birth_and_native_exit_time','wDeath',{
        param($s) Order $s @('stopWait = WaitForSingleObject(localRoot, 50); stopWaitReturned = true;','if (stopWait != WaitObject && stopWait != WaitTimeout) return false;','if (stopWait == WaitTimeout) continue;','stopTimesSucceeded = GetProcessTimes(localRoot, out stopCreated, out stopExited, out stopKernel, out stopUser);','if (!stopTimesSucceeded || stopCreated != offered.Birth || stopCreated <= 0 || stopExited < stopCreated) return false;','stopWait = WaitForSingleObject(localRoot, 0); stopWaitReturned = true;','return stopWait == WaitObject && originalStopClock.ElapsedMilliseconds < StopMilliseconds;')
    },'stopExited < stopCreated','false'),
    @('w_root_death_does_not_retry_unknown_or_failed_wait_observation','wDeath',{
        param($s) Has $s 'Require(!stopWaitAttempted || (stopWaitReturned && (stopWait == WaitObject || stopWait == WaitTimeout)));';Has $s 'stopWaitAttempted = true; stopWaitReturned = false;' 2;Has $s 'if (stopWait != WaitObject && stopWait != WaitTimeout) return false;';Has $s 'stopTimesAttempted = true;' 1
    },'stopWaitReturned && (stopWait == WaitObject || stopWait == WaitTimeout)','true'),
    @('w_stop_preserves_single_twenty_five_second_clock_without_reset','wProgram',{
        param($s) Has $s 'private const int StopMilliseconds = 25000;';$stop=Method $s 'private static bool StopOriginalProducer(';Has $stop 'Stopwatch.StartNew(' 1;Has $stop 'stopClock = Stopwatch.StartNew();';foreach($bad in @('.Restart(','.Reset(','return true;','.Join(')){Has $stop $bad 0}
    },'StopMilliseconds = 25000','StopMilliseconds = 250000'),
    @('w_root_transfer_and_stop_do_not_create_new_clock_or_join_worker','wRoot',{
        param($s) foreach($bad in @('Stopwatch.StartNew(','.Restart(','.Reset(','DateTime.UtcNow','Environment.TickCount')){Has $s $bad 0}
        Has $s '.Join(' 1
        Has (Method $s 'internal bool IsWorkerSettledForNormal') 'Require(Object.ReferenceEquals(Thread.CurrentThread, originalMainThread)); return IsSettledForNormal && workerStartAttempted && workerStartReturned && worker != null && worker.Join(0);'
        foreach($sig in @('private void Run(','internal void ClaimOffer(','internal bool SettleAfterProducerDeath(','internal bool ObservePhysicalDeathAfterJobStop(')){Has (Method $s $sig) '.Join(' 0}
        Has (Method $s 'internal bool SettleAfterProducerDeath(') 'originalStopClock.ElapsedMilliseconds >= StopMilliseconds'
        Has (Method $s 'internal bool ObservePhysicalDeathAfterJobStop(') 'return stopWait == WaitObject && originalStopClock.ElapsedMilliseconds < StopMilliseconds;'
    },'while (originalStopClock.ElapsedMilliseconds < StopMilliseconds)','while (originalStopClock.ElapsedMilliseconds < StopMilliseconds) { originalStopClock.Restart(); } while (false)'),
    @('w_actual_entry_retains_both_transfer_owners_before_private_workers_start','wControl',{
        param($s) Order $s @('Require(jobTransfer == null && rootTransfer == null && Interlocked.CompareExchange(ref stopAttempted, 0, 0) == 0 && Interlocked.CompareExchange(ref controlFailed, 0, 0) == 0);','jobTransfer = new GuardianJobTransfer(producerTransfer, binding);','rootTransfer = new GuardianRootTransfer(producerTransfer, jobTransfer, contextCapture.AdmittedMetadata, binding);','controlReader = new Thread(delegate()','controlWriter = new Thread(delegate()','terminalObservation = new GuardianTerminalObservation(binding);','controlReader.IsBackground = true; controlWriter.IsBackground = true;','controlReaderStartAttempted = true; controlReader.Start(); controlReaderStartReturned = true;','controlWriterStartAttempted = true; controlWriter.Start(); controlWriterStartReturned = true;')
    },'rootTransfer = new GuardianRootTransfer(producerTransfer, jobTransfer, contextCapture.AdmittedMetadata, binding);','GuardianRootTransfer unretained = new GuardianRootTransfer(producerTransfer, jobTransfer, contextCapture.AdmittedMetadata, binding);'),
    @('w_actual_reader_preserves_old_frame_bounds_with_separate_root_bound','wReader',{
        param($s) Has $s 'int storageMaximum = Math.Max(Stratus.RuntimeBuild.TerminalWire.MaximumPreparedBytes, Math.Max(Stratus.RuntimeBuild.TerminalWire.MaximumCommitBytes, Math.Max(256, Math.Max(Stratus.RuntimeBuild.CompilerJobWire.MaximumBytes, Stratus.RuntimeBuild.CompilerRootWire.MaximumBytes))));'
        Has $s 'byte[] buffer = new byte[storageMaximum]; byte[] frame = new byte[storageMaximum];'
        Has $s 'int maximum = stage == 0 ? 256 : (stage == 1 ? Stratus.RuntimeBuild.CompilerJobWire.MaximumBytes : (stage == 2 ? Stratus.RuntimeBuild.CompilerRootWire.MaximumBytes : (stage == 3 ? Stratus.RuntimeBuild.TerminalWire.MaximumPreparedBytes : Stratus.RuntimeBuild.TerminalWire.MaximumCommitBytes)));'
    },'stage == 0 ? 256','stage == 0 ? 65536'),
    @('w_actual_reader_snapshots_ack_attempts_immediately_after_read','wReader',{
        param($s) Has $s 'count = controlInput.Read(buffer, 0, buffer.Length); int admissionAttemptAtRead = Interlocked.CompareExchange(ref controlAckWriteAttempted, 0, 0); int jobAckAttemptAtRead = Interlocked.CompareExchange(ref jobAckWriteAttempted, 0, 0); int rootAckAttemptAtRead = Interlocked.CompareExchange(ref rootAckWriteAttempted, 0, 0); int terminalAckAttemptAtRead = Interlocked.CompareExchange(ref terminalObservation.AckWriteAttempted, 0, 0); int terminalCloseAttemptAtRead = Interlocked.CompareExchange(ref terminalObservation.HalfCloseAttempted, 0, 0); int preparedObservedAtRead = Interlocked.CompareExchange(ref terminalObservation.PreparedObserved, 0, 0); Stratus.RuntimeBuild.TerminalRecord preparedAtRead = preparedObservedAtRead == 1 ? terminalObservation.Record : null; if (stage < 3) AssertControlWorker(ref workerTick); else AssertTerminalControlWorker(ref workerTick);'
        Has $s 'controlInput.Read(' 1
    },'int jobAckAttemptAtRead = Interlocked.CompareExchange(ref jobAckWriteAttempted, 0, 0);','int jobAckAttemptAtRead = 1;'),
    @('w_actual_reader_rejects_coalesced_early_job_and_root_bytes_before_buffering','wReader',{
        param($s) Has $s 'if (stage == 1) Require(admissionAttemptAtRead == 1); if (stage == 2) Require(jobAckAttemptAtRead == 1); if (stage == 3) Require(rootAckAttemptAtRead == 1); if (stage == 4) Require(terminalAckAttemptAtRead == 1 && terminalCloseAttemptAtRead == 1 && preparedAtRead != null && Object.ReferenceEquals(preparedAtRead, terminalObservation.Record)); frame[offset++] = buffer[i];'
        Has $s 'jobAckWritten' 0;Has $s 'Thread.Sleep(' 0
    },'if (stage == 2) Require(jobAckAttemptAtRead == 1);','/* early root bytes accepted */'),
    @('w_actual_reader_has_exact_three_phases_and_rejects_every_surplus_byte','wReader',{
        param($s) Has $s 'Require(stage < 5 && offset < maximum && (buffer[i] == 10 || (buffer[i] >= 32 && buffer[i] <= 126)));'
        Has $s 'if (buffer[i] != 10) continue;';Has $s 'byte[] actual = new byte[offset]; Buffer.BlockCopy(frame, 0, actual, 0, offset);';Has $s '++stage; offset = 0;'
        Has $s 'else if (stage == 3) { ReceiveNormalTerminalPrepared(actual, rootAckAttemptAtRead, ref workerTick); } else { ReceiveNormalTerminalCommit(actual, preparedAtRead, terminalAckAttemptAtRead, terminalCloseAttemptAtRead, ref workerTick); }'
    },'stage < 5','stage <= 5'),
    @('w_actual_reader_claims_job_then_root_from_actual_serialized_frames','wReader',{
        param($s) Order $s @('if (stage == 0) { Stratus.RuntimeBuild.CompilerJobWire.RequireExact(actual, expected);','else if (stage == 1)','jobTransfer.ClaimOffer(actual);','else if (stage == 2) { Require(jobAckAttemptAtRead == 1); AssertControlWorker(ref workerTick); rootTransfer.ClaimOffer(actual); }','ReceiveNormalTerminalPrepared(actual, rootAckAttemptAtRead, ref workerTick);','ReceiveNormalTerminalCommit(actual, preparedAtRead, terminalAckAttemptAtRead, terminalCloseAttemptAtRead, ref workerTick);','++stage; offset = 0;')
        Has $s 'jobTransfer.ClaimOffer(actual);' 1;Has $s 'rootTransfer.ClaimOffer(actual);' 1
    },'rootTransfer.ClaimOffer(actual);','/* actual third frame not claimed */'),
    @('w_actual_reader_closes_admission_on_eof_partial_eof_or_exception','wReader',{
        param($s) Has $s 'if (count == 0) { Require(stage == 5 && offset == 0 && terminalAckAttemptAtRead == 1 && terminalCloseAttemptAtRead == 1 && preparedAtRead != null && Object.ReferenceEquals(preparedAtRead, terminalObservation.Record) && Interlocked.CompareExchange(ref terminalObservation.CommitObserved, 0, 0) == 1); Interlocked.Exchange(ref terminalObservation.InputEofObserved, 1); return; }'
        Has $s 'Interlocked.Exchange(ref controlFailed, 1);' 1;Has $s 'catch (Exception) { Interlocked.Exchange(ref controlFailed, 1); } finally { Interlocked.Exchange(ref terminalObservation.ReaderSettled, 1); }';Has $s 'return true;' 0
    },'catch (Exception) { Interlocked.Exchange(ref controlFailed, 1); }','catch (Exception) { Interlocked.Exchange(ref controlFailed, 0); }'),
    @('w_actual_sole_writer_serializes_all_four_flushed_private_messages','wControl',{
        param($s) Has $s 'controlReader = new Thread(' 1;Has $s 'controlWriter = new Thread(' 1;$writer=Block $s 'controlWriter = new Thread(delegate()';Has $s 'controlOutput.Write(' 4;Has $writer 'controlOutput.Write(' 4;Has $s 'controlOutput.Flush(' 4;Has $writer 'controlOutput.Flush(' 4;Order $writer @('controlOutput.Write(hello, 0, hello.Length); controlOutput.Flush();','controlOutput.Write(acknowledgement, 0, acknowledgement.Length); controlOutput.Flush();','controlOutput.Write(jobAcknowledgement, 0, jobAcknowledgement.Length); controlOutput.Flush();','controlOutput.Write(rootAcknowledgement, 0, rootAcknowledgement.Length); controlOutput.Flush();')
    },'controlOutput.Write(rootAcknowledgement, 0, rootAcknowledgement.Length); controlOutput.Flush();','controlOutput.Write(rootAcknowledgement, 0, rootAcknowledgement.Length);'),
    @('w_actual_root_ack_write_requires_settled_copy_and_records_actual_postflush','wWriter',{
        param($s) Order $s @('jobAckWriteReturned = true; AssertControlWorker(ref workerTick); Interlocked.Exchange(ref jobAckWritten, 1);','while ((rootAcknowledgement = rootTransfer.CopyAcknowledgementIfSettled()) == null) { AssertControlWorker(ref workerTick); Thread.Sleep(10); }','Require(Interlocked.CompareExchange(ref rootAckWriteAttempted, 1, 0) == 0);','controlOutput.Write(rootAcknowledgement, 0, rootAcknowledgement.Length); controlOutput.Flush();','rootAckWriteReturned = true; AssertControlWorker(ref workerTick); Interlocked.Exchange(ref rootAckWritten, 1);','SendNormalTerminalAcknowledgement(ref workerTick);')
        $terminal=Block $s 'private static void SendNormalTerminalAcknowledgement('
        Has $terminal 'Require(Object.ReferenceEquals(Thread.CurrentThread, controlWriter));'
        Has $terminal 'Require(Interlocked.CompareExchange(ref rootAckWritten, 0, 0) == 1 && rootAckWriteReturned && jobTransfer.IsSettledForNormal && rootTransfer.IsSettledForNormal && terminalObservation.Record != null);'
        Order $terminal @('byte[] acknowledgement = Stratus.RuntimeBuild.TerminalWire.Acknowledgement(terminalObservation.Record);','Require(acknowledgement.Length <= Stratus.RuntimeBuild.TerminalWire.MaximumAckBytes);','DemandQualifiedNormalTerminalObservation();','Require(Interlocked.CompareExchange(ref terminalObservation.AckWriteAttempted, 1, 0) == 0);','controlOutput.Write(acknowledgement, 0, acknowledgement.Length); controlOutput.Flush();','terminalObservation.AckWriteReturned = true;','Interlocked.Exchange(ref terminalObservation.AckWritten, 1);','Require(Interlocked.CompareExchange(ref terminalObservation.HalfCloseAttempted, 1, 0) == 0);','terminalObservation.HalfCloseSucceeded = CloseHandle(terminalObservation.WriteHandle);','terminalObservation.HalfCloseError = Marshal.GetLastWin32Error();','Interlocked.Exchange(ref terminalObservation.HalfCloseReturned, 1);','Require(terminalObservation.HalfCloseSucceeded);','terminalObservation.WriteWrapperInvalidationAttempted = true;','terminalObservation.WriteSafe.SetHandleAsInvalid();','terminalObservation.WriteWrapperInvalidationReturned = true;')
        Has $terminal 'controlOutput.Write(' 1;Has $terminal 'controlOutput.Flush(' 1;Has $terminal 'new Thread(' 0
    },'rootTransfer.CopyAcknowledgementIfSettled()','new byte[1]'),
    @('w_main_remains_nonblocking_watch_with_no_success_terminal','wMain',{
        param($s) $main=Block $s 'private static int Main('
        Has $main 'Require(!dead);';Has $main 'if (ObserveProducer()) break;' 3;Has $main 'return StopOriginalProducer() ? 204 : 205;' 2;Has $main 'return' 3;Has $main 'return 0;' 1
        Has $main 'if (ownerExited) { if (!MayDrainNormalTerminal()) break; RequireOriginalProducerNormalExitForArmedDrain(); if (TryCompleteNormalTerminal()) return 0; Thread.Sleep(10); continue; }'
        Has $main 'Thread.Sleep(' 1
        foreach($bad in @('return 202;','return 203;','controlInput.Read(','controlOutput.Write(','.Join(','QueryLimits(','QueryAccounting(','TerminateJobObject(','DuplicateHandle(')){Has $main $bad 0}
        $drain=Block $s 'private static bool MayDrainNormalTerminal('
        Has $drain 'Interlocked.CompareExchange(ref observation.PreparedObserved, 0, 0) == 1 && observation.Record != null && Interlocked.CompareExchange(ref observation.AckWriteAttempted, 0, 0) == 1 && Interlocked.CompareExchange(ref observation.HalfCloseAttempted, 0, 0) == 1'
        $exit=Block $s 'private static void RequireOriginalProducerNormalExitForArmedDrain('
        Order $exit @('AssertNormalTerminalDrainAdmission();','DemandQualifiedNormalTerminalObservation();','normalProducerExit = observation; observation.Attempted = true;','Require(observation.BeforeWait == WaitObject);','Require(observation.TimesSucceeded && observation.Created == producerBirth && observation.Created > 0 && observation.Exited > 0 && observation.Exited >= observation.Created);','Require(observation.ExitSucceeded && observation.ExitCode == 0);','Require(observation.AfterWait == WaitObject);','observation.Proven = true;')
        $settle=Block $s 'private static bool NormalTerminalChannelSettled('
        Has $settle 'Interlocked.CompareExchange(ref observation.CommitObserved, 0, 0) == 1 && Interlocked.CompareExchange(ref observation.InputEofObserved, 0, 0) == 1 && Interlocked.CompareExchange(ref observation.HalfCloseReturned, 0, 0) == 1 && observation.HalfCloseSucceeded'
        Has $settle 'controlReaderStartAttempted && controlReaderStartReturned && controlWriterStartAttempted && controlWriterStartReturned && controlReader.Join(0) && controlWriter.Join(0) && jobTransfer.IsWorkerSettledForNormal && rootTransfer.IsWorkerSettledForNormal && contextCapture.IsWorkerSettledForTerminal'
        $complete=Block $s 'private static bool TryCompleteNormalTerminal('
        Order $complete @('if (!NormalTerminalChannelSettled()) return false;','jobTransfer.ProveNormalClosureAfterProducerExit(rootTransfer);','rootTransfer.ProveNormalClosureAfterProducerExit(jobTransfer);','Interlocked.Exchange(ref normalProofCompleted, 1);','Interlocked.Exchange(ref terminalOutputPermit, 1);','if (Interlocked.CompareExchange(ref terminalOutputWritten, 0, 0) == 0 || Interlocked.CompareExchange(ref readyWriterSettled, 0, 0) == 0 || !readyWriterStartAttempted || !readyWriterStartReturned || !readyWriter.Join(0)) return false;','Require(terminalOutputWriteReturned && jobTransfer.NormalClosureProven && rootTransfer.NormalClosureProven);','AssertStartupDeadline(); return true;')
        Has $complete 'StopOriginalProducer(' 0
        $job=Block $s 'internal void ProveNormalClosureAfterProducerExit(GuardianRootTransfer originalRootOwner)'
        Order $job @('originalRootOwner.AssertNormalJobBinding(this, sourceHandle, localJob);','RequireOriginalProducerNormalExit();','normalTypeAttempted = true;','normalTypeSucceeded = QueryLimits(localJob, 9, out normalTypeLimits,','Require(normalTypeSucceeded && normalTypeBytes == (uint)Marshal.SizeOf(typeof(ExtendedLimit)));','Require(normalTypeLimits.basic.flags == initialLimits.basic.flags && normalTypeLimits.basic.processTime == initialLimits.basic.processTime && normalTypeLimits.basic.jobTime == initialLimits.basic.jobTime && normalTypeLimits.basic.min == initialLimits.basic.min && normalTypeLimits.basic.max == initialLimits.basic.max && normalTypeLimits.basic.active == initialLimits.basic.active && normalTypeLimits.basic.affinity == initialLimits.basic.affinity && normalTypeLimits.basic.priority == initialLimits.basic.priority && normalTypeLimits.basic.scheduling == initialLimits.basic.scheduling && normalTypeLimits.processMemory == initialLimits.processMemory && normalTypeLimits.jobMemory == initialLimits.jobMemory);','normalQueryAttempted = true;','normalQuerySucceeded = QueryAccounting(localJob, 1, out normalAccounting,','Require(normalQuerySucceeded && normalAccountingBytes == (uint)Marshal.SizeOf(typeof(Accounting)) && normalAccounting.active == 0 && normalAccounting.total == 1 && normalAccounting.terminated == 0);','Interlocked.Exchange(ref normalClosureProven, 1);')
        Has $job 'duplicateAttempted && duplicateReturned && duplicateSucceeded && ValidHandle(localJob)'
        $root=Block $s 'internal void ProveNormalClosureAfterProducerExit(GuardianJobTransfer originalOwner)'
        Has $root 'Object.ReferenceEquals(originalOwner, originalJobOwner) && originalOwner.NormalClosureProven'
        Order $root @('originalOwner.AssertNormalRootBinding(this, offered.SourceJob, originalLocalJob);','RequireOriginalProducerNormalExit();','normalPidAttempted = true; normalPid = GetProcessId(localRoot);','Require(normalPid == (uint)offered.ProcessId && normalPid != producerPid && normalPid != GetCurrentProcessId());','normalBeforeWaitAttempted = true; normalBeforeWait = WaitForSingleObject(localRoot, 0);','Require(normalBeforeWait == WaitObject);','normalTimesSucceeded = GetProcessTimes(localRoot, out normalCreated, out normalExited, out normalKernel, out normalUser);','Require(normalTimesSucceeded && normalCreated == offered.Birth && normalCreated > 0 && normalExited > 0 && normalExited >= normalCreated && terminalObservation.Record != null && normalExited == terminalObservation.Record.NativeCompilerExitFileTime && normalProducerExit != null && normalProducerExit.Proven && normalExited <= normalProducerExit.Exited);','normalExitAttempted = true; normalExitSucceeded = GetExitCodeProcess(localRoot, out normalExitCode);','Require(normalExitSucceeded && normalExitCode == 0);','normalAfterWaitAttempted = true; normalAfterWait = WaitForSingleObject(localRoot, 0);','Require(normalAfterWait == WaitObject);','Interlocked.Exchange(ref normalClosureProven, 1);')
        foreach($proof in @($job,$root)){Has $proof 'catch (Exception) { Interlocked.Exchange(ref normalTerminalFailed, 1); throw; }';Has $proof 'Interlocked.CompareExchange(ref normalClosureAttempted, 1, 0) == 0';Has $proof 'TerminateJobObject(' 0;Has $proof '.Join(' 0}
        Has (Block $s 'private static void DemandQualifiedNormalTerminalObservation(') 'private static void DemandQualifiedNormalTerminalObservation() { throw new InvalidOperationException("W normal terminal observation is not independently qualified."); }'
    },'if (!MayDrainNormalTerminal()) break;','if (!MayDrainNormalTerminal()) return 0;'),
    @('p_actual_reader_immediately_sequences_job_root_and_surplus_eof_read','invReader',{
        param($s) $read=Block $s 'private void StartReader('
        Has $read 'Interlocked.Exchange(ref guardianAckReceived, 1); ReceiveOriginalCompilerJobAck(); ReceiveOriginalCompilerRootAck(); ReceiveOriginalTerminalAcknowledgement();'
        Has $read 'ReceiveOriginalCompilerJobAck();' 1;Has $read 'ReceiveOriginalCompilerRootAck();' 1;Has $read 'Thread.Sleep(' 0
        $terminal=Block $s 'private void ReceiveOriginalTerminalAcknowledgement('
        Has $terminal 'Require(Object.ReferenceEquals(Thread.CurrentThread, readerThread) && Interlocked.CompareExchange(ref compilerRootAckReceived, 0, 0) == 1);'
        Has $terminal 'int firstCount = inputStream.Read(first, 0, 1); RetainedProducerTerminal originalAtRead = Interlocked.CompareExchange(ref producerTerminal, null, null); int writeAttemptedAtRead = Interlocked.CompareExchange(ref terminalPreparedWriteAttempted, 0, 0); AssertTerminalChannelPending(); Require(firstCount == 1 && originalAtRead != null && writeAttemptedAtRead == 1);'
        Has $terminal 'Require(expected.Length > 0 && expected.Length <= TerminalWire.MaximumAckBytes);'
        Has $terminal 'Require(count > 0 && count <= actual.Length - used);'
        Order $terminal @('TerminalWire.RequireExact(actual, expected);','Require(Interlocked.CompareExchange(ref terminalAckReceived, 1, 0) == 0);','Require(Interlocked.CompareExchange(ref terminalEofReadAttempted, 1, 0) == 0);','int eof = inputStream.Read(first, 0, 1);','Interlocked.Exchange(ref terminalEofReadCount, eof); Interlocked.Exchange(ref terminalEofReadReturned, 1);','Require(eof == 0);','Require(Interlocked.CompareExchange(ref terminalGuardianEof, 1, 0) == 0);')
        Has $terminal 'Thread.Sleep(' 0;Has $terminal 'terminalPreparedWritten' 0
    },'ReceiveOriginalCompilerRootAck();','/* actual root ACK reader omitted */'),
    @('p_actual_single_writer_sequences_hello_job_then_root_offer','invWriter',{
        param($s) Has $s 'writerThread = new Thread(' 1;Order $s @('outputStream.Write(responseHello, 0, responseHello.Length);','outputStream.Flush();','Interlocked.Exchange(ref responseWritten, 1);','SendOriginalCompilerJobOffer();','SendOriginalCompilerRootOffer();','SendOriginalTerminal();');Has $s 'writerThread.IsBackground = true; writerThread.Start();'
    },'SendOriginalCompilerRootOffer();','/* actual root offer writer omitted */'),
    @('p_entry_and_control_v5_are_exactly_matched_by_actual_caller_chain','pAbi',{
        param($s) $p=Block $s 'public sealed class DisposableProducerLaunch : IDisposable';$command=Block $s 'public sealed class CompilerCommand : IDisposable';$i=Block $s 'internal sealed partial class ProducerInvocation';$entry=Block $s 'internal static class Program'
        Has (Method $p 'internal static DisposableProducerLaunch PreparePreloaded(') 'string[] arguments = new string[] { "--stratus-producer-entry-v6" };';Has (Method $command 'internal static CompilerCommand PrepareOwnedWithDeadline(') 'arguments.Length != 1 || arguments[0] != "--stratus-producer-entry-v6"';Has (Method $command 'private void PrepareBirthJobOriginal(') 'capturedCommandLine = commandLine + " --stratus-producer-control-v6 " +';Has (Method $i 'internal static ProducerInvocation PrepareFromInheritedEntry(') 'Require(copied[0] == "--stratus-producer-entry-v6" && copied[1] == "--stratus-producer-control-v6");';Has (Method $entry 'private static int Main(') 'invocation = Stratus.RuntimeBuild.ProducerInvocation.PrepareFromInheritedEntry(args); invocation.Start();'
    },'copied[1] == "--stratus-producer-control-v6"','copied[1] == "--stratus-producer-control-v5"'),
    @('w_watch_and_ready_v7_are_exactly_matched_by_launcher_and_entry','wAbi',{
        param($s) $g=Block $s 'public sealed class GuardianLaunch : IDisposable';$program=Block $s 'internal static partial class Program';Has $g 'public const string Abi = "stratus-owner-watch-launch-v8";';$start=Method $g 'public void StartRunning(';Has $start 'arguments.Append(" --stratus-owner-watch-v8 ").Append(rawHandle)';Has $start 'expectedReady = "STRATUS_OWNER_READY_V8 " + nonce';Has $start 'if (expectedReady.Length > 352)';Has (Method $program 'private static int Main(') 'Require(args != null && args.Length == 14 && args[0] == "--stratus-owner-watch-v8");';$ready=Method $program 'private static void StartReadyWriter(';Has $ready 'Encoding.ASCII.GetBytes("STRATUS_OWNER_READY_V8 " + nonce';Has $ready 'Require(frame.Length <= 352);'
    },'expectedReady = "STRATUS_OWNER_READY_V8 " + nonce','expectedReady = "STRATUS_OWNER_READY_V7 " + nonce'),
    @('p_w_hello_and_admission_v5_roles_match_without_enlarging_old_bounds','helloAbi',{
        param($s) $i=Block $s 'internal sealed partial class ProducerInvocation';$program=Block $s 'internal static partial class Program';Has $i 'private const int MaximumFrameBytes = 256;';$frames=Method $i 'private void PrepareFrames(';Has $frames 'expectedHello = Encoding.ASCII.GetBytes("STRATUS_GUARDIAN_HELLO_V6 " + values);';Has $frames 'responseHello = Encoding.ASCII.GetBytes("STRATUS_PRODUCER_HELLO_V6 " + values);';Has $frames 'expectedAdmissionAck = Encoding.ASCII.GetBytes("STRATUS_GUARDIAN_ADMISSION_ACK_V6 " + values);';$c=Method $program 'private static void AdoptControl(';Has $c 'byte[] expected = Encoding.ASCII.GetBytes("STRATUS_PRODUCER_HELLO_V6 " + ControlFields(nonce));';Has $c 'byte[] hello = Encoding.ASCII.GetBytes("STRATUS_GUARDIAN_HELLO_V6 " + ControlFields(nonce));';Has $c 'byte[] acknowledgement = Encoding.ASCII.GetBytes("STRATUS_GUARDIAN_ADMISSION_ACK_V6 " + ControlFields(nonce));';Has $c 'Require(expected.Length <= 256 && hello.Length <= 256 && acknowledgement.Length <= 256);'
    },'expectedAdmissionAck = Encoding.ASCII.GetBytes("STRATUS_GUARDIAN_ADMISSION_ACK_V6 " + values);','expectedAdmissionAck = Encoding.ASCII.GetBytes("STRATUS_GUARDIAN_ADMISSION_ACK_V5 " + values);'),
    @('p_root_transfer_and_continuation_remain_separately_unconditionally_closed','ownerRoot',{
        param($s) Has (Method $s 'internal void DemandQualifiedCompilerRootTransfer(') 'AssertBirthAdmission(original); throw new InvalidOperationException("Original compiler root transfer requires separately qualified P/W native capabilities and STOP.");';Has (Method $s 'internal void DemandQualifiedCompilerContinuation(') 'AssertContinuationAdmission(original); throw new InvalidOperationException("Compiler continuation requires separately qualified root ACK, native scheduling and terminal supervision.");'
    },'throw new InvalidOperationException("Original compiler root transfer requires separately qualified P/W native capabilities and STOP.");','return;'),
    @('root_wire_binding_bound_is_derived_from_original_identity_clock_context_fields','wire',{
        param($s) Has $s 'private const int MaximumBindingBytes = 64 + 10 + 19 + 20 + 20 + 7 + 64 + 6;';Has $s 'binding != null && binding.Length > 0 && binding.Length <= MaximumBindingBytes'
    },'MaximumBindingBytes = 64 + 10 + 19 + 20 + 20 + 7 + 64 + 6','MaximumBindingBytes = 65536'),
    @('w_root_alias_verification_is_called_on_actual_duplicate_and_cleanup_paths','wRoot',{
        param($s) $run=Method $s 'private void Run(';Order $run @('Require(duplicateSucceeded && ValidHandle(localRoot)); AssertNoLocalAlias();','postCompareAttempted = true;','flagsAttempted = true;');$death=Method $s 'internal bool ObservePhysicalDeathAfterJobStop(';Has $death 'if (!duplicateReturned || !duplicateSucceeded || !ValidHandle(localRoot)) return false; AssertNoLocalAlias(); stopPidAttempted = true;'
    },'Require(duplicateSucceeded && ValidHandle(localRoot)); AssertNoLocalAlias();','Require(duplicateSucceeded && ValidHandle(localRoot));'),
    @('w_root_returned_capability_rejects_zero_and_all_reserved_process_pseudohandles','wRoot',{
        param($s) Has (Method $s 'private static bool ValidHandle(') 'return value != IntPtr.Zero && value != new IntPtr(-1) && value != new IntPtr(-2) && value != new IntPtr(-3) && value != new IntPtr(-4) && value != new IntPtr(-5) && value != new IntPtr(-6);';Has (Method $s 'private void Run(') 'Require(duplicateSucceeded && ValidHandle(localRoot));';Has (Method $s 'internal bool ObservePhysicalDeathAfterJobStop(') '!ValidHandle(localRoot)'
    },'value != IntPtr.Zero && value != new IntPtr(-1)','true && value != new IntPtr(-1)')
)
Assert-RootTransfer ($rows.Count -eq 91) 'Historical case inventory differs before new immutable-limit coverage.'
# Separate directed pair; the historical IDs/order above remain unchanged.
$rows+=@(
    ,@('normal_job_closure_preserves_all_original_immutable_limits_before_accounting','wMain',{
        param($s) $job=Block $s 'internal void ProveNormalClosureAfterProducerExit(GuardianRootTransfer originalRootOwner)'
        Has (Block $s 'private static bool TryCompleteNormalTerminal(') 'jobTransfer.ProveNormalClosureAfterProducerExit(rootTransfer);'
        Order $job @('normalTypeSucceeded = QueryLimits(localJob, 9, out normalTypeLimits,','Require(normalTypeSucceeded && normalTypeBytes == (uint)Marshal.SizeOf(typeof(ExtendedLimit)));','Require(normalTypeLimits.basic.flags == initialLimits.basic.flags && normalTypeLimits.basic.processTime == initialLimits.basic.processTime && normalTypeLimits.basic.jobTime == initialLimits.basic.jobTime && normalTypeLimits.basic.min == initialLimits.basic.min && normalTypeLimits.basic.max == initialLimits.basic.max && normalTypeLimits.basic.active == initialLimits.basic.active && normalTypeLimits.basic.affinity == initialLimits.basic.affinity && normalTypeLimits.basic.priority == initialLimits.basic.priority && normalTypeLimits.basic.scheduling == initialLimits.basic.scheduling && normalTypeLimits.processMemory == initialLimits.processMemory && normalTypeLimits.jobMemory == initialLimits.jobMemory);','normalQueryAttempted = true;','normalQuerySucceeded = QueryAccounting(localJob, 1, out normalAccounting,','Require(normalQuerySucceeded && normalAccountingBytes == (uint)Marshal.SizeOf(typeof(Accounting)) && normalAccounting.active == 0 && normalAccounting.total == 1 && normalAccounting.terminated == 0);','Interlocked.Exchange(ref normalClosureProven, 1);')
    },'normalTypeLimits.basic.flags == initialLimits.basic.flags','true')
)
$expectedPairs=92;$expected=184
Assert-RootTransfer ($rows.Count -eq $expectedPairs -and $expected -eq 2*$expectedPairs) 'Fixed case inventory differs.'
$executed=0;$results=[Collections.Generic.List[object]]::new()
foreach($row in $rows){
    Assert-RootTransfer ($row.Count -eq 5) 'Fixed row shape differs.'
    $selected=$blocks[$row[1]];$check=$row[2]
    & $check $selected;$executed++;$results.Add([pscustomobject]@{name=$row[0];variant='original_source';passed=$true})
    $mutated=Replace-Tokens $selected $row[3] $row[4];$rejected=$false
    try{& $check $mutated}catch{
        Assert-RootTransfer ($_.Exception.Message.StartsWith('Compiler root transfer source contract:',[StringComparison]::Ordinal)) ('Unexpected negative-control exception: '+$row[0])
        $rejected=$true
    }
    Assert-RootTransfer $rejected ('Weakening mutation survived: '+$row[0])
    $executed++;$results.Add([pscustomobject]@{name=$row[0];variant='mutated_source_rejected';passed=$true})
}
Assert-RootTransfer ($executed -eq $expected -and $results.Count -eq $expected) 'Fixed cases did not complete.'
$before=[Collections.Generic.List[object]]::new();$after=[Collections.Generic.List[object]]::new()
foreach($name in $sources.Keys){
    $old=$sources[$name];$latest=Read-RootTransferSource $old.path
    Assert-RootTransfer ($latest.sha256 -ceq $old.sha256) ('Subject changed during inspection: '+$name)
    $before.Add([pscustomobject]@{name=$name;path=$old.path;sha256=$old.sha256})
    $after.Add([pscustomobject]@{name=$name;path=$latest.path;sha256=$latest.sha256})
}
$testAfter=Read-RootTransferSource $PSCommandPath
Assert-RootTransfer ($testAfter.sha256 -ceq $testBefore.sha256) 'Test changed during inspection.'
[pscustomobject]@{
    format='stratus-compiler-root-transfer-source-contract-tests-v1'
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
    actual_job_transfer_executed=$false;actual_job_ack_received=$false;stop_job_settlement_proven=$false;compiler_birth_authorized=$false
    actual_root_transfer_executed=$false;actual_root_ack_received=$false;compiler_resume_authorized=$false
    cases=$results.ToArray()
}|ConvertTo-Json -Depth 6 -Compress
