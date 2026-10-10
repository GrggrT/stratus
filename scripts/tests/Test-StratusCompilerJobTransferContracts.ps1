param()
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
# Fixed source data only. Never compile, load, evaluate or invoke a target.
# All callbacks below are literal test predicates; mutations remain token data.
# This suite cannot qualify native transfer, ACK, STOP, timing or ownership.
# Root-handoff adaptation retains all 54 pairs/108 case IDs and ordering. Where
# a guard moved to a dedicated phase, select its actual caller AND callee: pinned
# lifetime/prebirth, received ACK/worker write-completion, typed birth blocker,
# and both-worker settlement before Job mutation. No target is executed here.
function Assert-JobTransfer {param([bool]$Okay,[string]$Message) if(-not $Okay){throw ('Compiler Job transfer source contract: '+$Message)}}
function Read-JobTransferSource {
    param([string]$Path)
    $full=[IO.Path]::GetFullPath($Path)
    # Bound allocation and every delivered read; a FileInfo precheck alone
    # cannot stop ReadAllBytes from allocating after concurrent file growth.
    $stream=[IO.FileStream]::new($full,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{
        $length=$stream.Length
        Assert-JobTransfer ($length -gt 0 -and $length -le 1048576) 'Inspection file exceeds one MiB.'
        $raw=[byte[]]::new([int]$length);$offset=0
        while($offset -lt $raw.Length){
            $count=$stream.Read($raw,$offset,$raw.Length-$offset)
            Assert-JobTransfer ($count -gt 0) 'Inspection file ended before its bounded length.'
            $offset+=$count
        }
        Assert-JobTransfer ($stream.ReadByte() -eq -1 -and $stream.Length -eq $length) 'File length changed while reading.'
    }finally{$stream.Dispose()}
    $source=[Text.UTF8Encoding]::new($false,$true).GetString($raw)
    $hash=[Security.Cryptography.SHA256]::Create()
    try{$digest=[BitConverter]::ToString($hash.ComputeHash($raw)).Replace('-','').ToLowerInvariant()}finally{$hash.Dispose()}
    return [pscustomobject]@{path=$full;text=$source;sha256=$digest}
}
function Tokens {
    param([string]$Text)
    Assert-JobTransfer ($null -ne $Text -and $Text.Length -le 1048576) 'Token input exceeds its fixed character bound.'
    $pattern=@'
//[^\r\n]* | /\*.*?\*/ | @"(?:""|[^"])*" | "(?:\\.|[^"\\])*" | '(?:\\.|[^'\\])*'
| [A-Za-z_][A-Za-z_0-9]* | [0-9]+ | == | != | >= | <= | \+\+ | -- | && | \|\| | => | [^\s]
'@
    $result=[Collections.Generic.List[string]]::new()
    $options=[Text.RegularExpressions.RegexOptions]::Singleline -bor [Text.RegularExpressions.RegexOptions]::IgnorePatternWhitespace
    foreach($m in [regex]::Matches($Text,$pattern,$options,[TimeSpan]::FromSeconds(5))){
        if(-not $m.Value.StartsWith('//') -and -not $m.Value.StartsWith('/*')){Assert-JobTransfer ($result.Count -lt 262144) 'Token inspection bound exceeded.';$result.Add($m.Value)}
    }
    Assert-JobTransfer ($result.Count -le 262144) 'Token inspection bound exceeded.'
    return ,$result.ToArray()
}
function Find {
    param([string[]]$Source,[string]$Sequence)
    $wanted=Tokens $Sequence;Assert-JobTransfer ($wanted.Count -gt 0) 'Empty predicate.'
    $found=[Collections.Generic.List[int]]::new()
    for($i=0;$i -le $Source.Count-$wanted.Count;$i++){
        $same=$true
        for($j=0;$j -lt $wanted.Count;$j++){if($Source[$i+$j] -cne $wanted[$j]){$same=$false;break}}
        if($same){$found.Add($i)}
    }
    return ,$found.ToArray()
}
function Has {param([string[]]$Source,[string]$Sequence,[int]$Count=1) Assert-JobTransfer ((Find $Source $Sequence).Count -eq $Count) ('Sequence differs: '+$Sequence)}
function Order {
    param([string[]]$Source,[string[]]$Sequences)
    $last=-1
    foreach($sequence in $Sequences){$found=Find $Source $sequence;Assert-JobTransfer ($found.Count -eq 1 -and $found[0] -gt $last) ('Order differs: '+$sequence);$last=$found[0]}
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
    Assert-JobTransfer ($found.Count -eq 1) ('Missing or ambiguous block: '+$Signature)
    $open=$found[0]
    while($open -lt $Source.Count -and $Source[$open] -cne '{'){$open++}
    Assert-JobTransfer ($open -lt $Source.Count) 'Opening brace missing.'
    $depth=0
    for($i=$open;$i -lt $Source.Count;$i++){
        if($Source[$i] -ceq '{'){$depth++}elseif($Source[$i] -ceq '}'){$depth--}
        if($depth -eq 0){return ,([string[]]$Source[$found[0]..$i])}
    }
    throw 'Compiler Job transfer source contract: Unbalanced source block.'
}
function Method {param([string[]]$Class,[string]$Signature) return ,(Block $Class $Signature -Direct)}
function Replace-Tokens {
    param([string[]]$Source,[string]$Needle,[string]$Replacement)
    $at=Find $Source $Needle;Assert-JobTransfer ($at.Count -eq 1) ('Mutation anchor differs: '+$Needle)
    $old=Tokens $Needle;$new=Tokens $Replacement;$result=[Collections.Generic.List[string]]::new()
    for($i=0;$i -lt $at[0];$i++){$result.Add($Source[$i])}
    foreach($token in $new){$result.Add($token)}
    for($i=$at[0]+$old.Count;$i -lt $Source.Count;$i++){$result.Add($Source[$i])}
    return ,$result.ToArray()
}
$sources=[ordered]@{}
foreach($name in @('ProducerTransfer','Producer','GuardianLaunch','Bootstrap','CompilerJobWire','ProducerInvocation','ProducerCompiler','ProducerJobTransfer','GuardianProgram','GuardianJobTransfer','ProducerTerminal','GuardianTerminal','GuardianRootTransfer')){
    $sources[$name]=Read-JobTransferSource ([IO.Path]::Combine($PSScriptRoot,'..',('Stratus.RuntimeBuild.'+$name+'.cs')))
}
$testBefore=Read-JobTransferSource $PSCommandPath
$l=Block (Tokens $sources.ProducerTransfer.text) 'internal sealed class ProducerTransferCapability'
$copy=Block $l 'internal sealed class LaunchCopy'
$p=Block (Tokens $sources.Producer.text) 'public sealed class DisposableProducerLaunch : IDisposable'
$stopcap=Block (Tokens $sources.Producer.text) 'internal sealed class ProducerStopCapability'
$g=Block (Tokens $sources.GuardianLaunch.text) 'public sealed class GuardianLaunch : IDisposable'
$b=Block (Tokens $sources.Bootstrap.text) 'public sealed class CompilerCommand : IDisposable'
$wire=Block (Tokens $sources.CompilerJobWire.text) 'internal static class CompilerJobWire'
$inv=Block (Tokens $sources.ProducerInvocation.text) 'internal sealed partial class ProducerInvocation'
$owner=Block (Tokens $sources.ProducerCompiler.text) 'internal sealed partial class ProducerCompilerOwner'
$pTerminal=Block (Tokens $sources.ProducerTerminal.text) 'internal sealed partial class ProducerInvocation'
$wTerminal=Block (Tokens $sources.GuardianTerminal.text) 'internal static partial class Program'
$pt=Block (Tokens $sources.ProducerJobTransfer.text) 'internal sealed partial class ProducerInvocation'
$po=Block (Tokens $sources.ProducerJobTransfer.text) 'internal sealed partial class ProducerCompilerOwner'
$w=Block (Tokens $sources.GuardianProgram.text) 'internal static partial class Program'
$wt=Block (Tokens $sources.GuardianJobTransfer.text) 'private sealed class GuardianJobTransfer'
$wr=Block (Tokens $sources.GuardianRootTransfer.text) 'private sealed class GuardianRootTransfer'
$blocks=@{
    wire=$wire;l=$l;copy=$copy;b=$b;p=$p;g=$g;inv=[string[]]@($inv+$pTerminal);owner=$owner;pt=$pt;po=$po;w=$w;wt=$wt
    stopcap=Method $stopcap 'internal void AcquireOriginal('
    binding=Method $wire 'internal static string Binding('
    frame=Method $wire 'private static byte[] Frame('
    parse=Method $wire 'internal static IntPtr ParseOffer('
    exact=Method $wire 'internal static void RequireExact('
    lacquire=Method $l 'internal void AcquireOriginal('
    lsame=Method $l 'private void AssertSameOriginal('
    lidentity=Method $l 'private void AssertOriginalIdentity('
    lblock=Method $l 'private static void DemandQualifiedTransferCapability('
    pretain=Method $p 'internal ProducerTransferCapability PrepareGuardianTransferCapability('
    acquire=Method $b 'internal void AcquireProducerTransferCapability('
    gstart=Method $g 'public void StartRunning('
    ccopy=Method $copy 'internal void Acquire('
    publish=Method $b 'internal void PublishOriginalCompilerJobOffer('
    offercheck=$b
    ppublish=Method $pt 'internal void PublishCompilerJobOffer('
    pread=Method $pt 'private void ReceiveOriginalCompilerJobAck('
    pwrite=Method $pt 'private void SendOriginalCompilerJobOffer('
    prun=[string[]]@((Method $owner 'private void Run(')+(Method $b 'internal void StartProducerCompiler(')+(Method $po 'internal void DemandQualifiedCompilerBirth(')+(Method $po 'internal void AssertBirthAdmission('))
    pblock=Method $po 'internal void DemandQualifiedCompilerBirth('
    claim=Method $wt 'internal void ClaimOffer('
    wrun=Method $wt 'private void Run('
    wblock=Method $wt 'private static void DemandQualifiedCompilerJobTransfer('
    limits=Method $wt 'private void AssertInitialLimits('
    ack=Method $wt 'internal byte[] CopyAcknowledgementIfSettled('
    close=Method $wt 'internal void CloseAdmission('
    stop=Method $wt 'internal bool StopAfterProducerDeath('
    settlement=[string[]]@((Method $wt 'internal bool SettleAfterProducerDeath(')+(Method $wt 'internal bool StopAfterProducerDeath(')+(Method $w 'private static bool StopOriginalProducer('))
    wmain=Method $w 'private static int Main('
    wnormal=[string[]]@((Method $w 'private static int Main(')+$wTerminal+(Method $w 'private static void RequireOriginalProducerNormalExitForArmedDrain(')+(Method $wt 'internal void ProveNormalClosureAfterProducerExit(GuardianRootTransfer originalRootOwner)')+(Method $wr 'internal void ProveNormalClosureAfterProducerExit(GuardianJobTransfer originalOwner)')+(Method $w 'private static void DemandQualifiedNormalTerminalObservation('))
    wcontrol=Method $w 'private static void AdoptControl('
    wControlTerminal=[string[]]@((Method $w 'private static void AdoptControl(')+(Method $wTerminal 'private static void SendNormalTerminalAcknowledgement('))
    wadopt=Method $w 'private static void AdoptProducerTransfer('
    wstop=Method $w 'private static bool StopOriginalProducer('
}
# Historical case IDs (including former phase/version labels) stay stable;
# predicates below follow actual terminal flow. These read sets are not compile closures.
# Fixed local callbacks inspect actual separately selected source methods. No
# source, mutation, handle text, class name or reflection result becomes code.
$rows=@(
    @('wire_has_separate_job_bound_and_exact_role_prefixes','wire',{
        param($s) Has $s 'internal const int MaximumBytes = 320;';Has $s 'private const string OfferPrefix = "STRATUS_COMPILER_JOB_OFFER_V1 ";';Has $s 'private const string AckPrefix = "STRATUS_COMPILER_JOB_ACK_V1 ";'
    },'MaximumBytes = 320','MaximumBytes = 65536'),
    @('wire_binding_contains_exact_original_identity_clock_and_context','binding',{
        param($s) Has $s 'Hex(nonce); Hex(contextHash);';Has $s 'start <= UInt64.MaxValue - 60000 && deadline == checked(start + 60000)';Has $s 'contextBytes > 0 && contextBytes <= ContextCapture.MaximumBytes'
        Has $s 'start.ToString(CultureInfo.InvariantCulture) + " " + deadline.ToString(CultureInfo.InvariantCulture) + " " + contextBytes.ToString(CultureInfo.InvariantCulture) + " " + contextHash'
    },'deadline == checked(start + 60000)','deadline >= checked(start + 60000)'),
    @('wire_offer_parse_requires_canonical_handle_and_full_byte_roundtrip','parse',{
        param($s) Has $s 'actual.Length <= MaximumBytes && actual[actual.Length - 1] == 10';Has $s 'Require(text.StartsWith(prefix, StringComparison.Ordinal));';Has $s 'raw.ToString(CultureInfo.InvariantCulture) == value'
        Order $s @('Require(HandleText(handle) == value);','RequireExact(actual, Offer(originalBinding, handle));','return handle;')
    },'RequireExact(actual, Offer(originalBinding, handle));','/* exact original frame omitted */'),
    @('wire_exact_comparison_requires_same_length_and_every_actual_byte','exact',{
        param($s) Has $s 'Require(actual != null && expected != null && actual.Length == expected.Length && actual.Length <= MaximumBytes);';Has $s 'for (int i = 0; i < actual.Length; ++i) Require(actual[i] == expected[i]);'
    },'actual[i] == expected[i]','actual[i] == actual[i]'),
    @('durable_transfer_duplicate_has_only_process_dup_handle_right','lacquire',{
        param($s) Has $s 'DuplicateHandle(GetCurrentProcess(), originalProcess, GetCurrentProcess(), out duplicate, 0x40, false, 0)';Order $s @('originalProcess = process; originalPid = pid; originalBirth = birth;','AssertOriginalIdentity(true);','DemandQualifiedTransferCapability();','duplicateAttempted = true;','duplicateError = Marshal.GetLastWin32Error(); duplicateSucceeded = copied; duplicateReturned = true;')
    },'out duplicate, 0x40, false, 0','out duplicate, 0x101041, false, 0'),
    @('original_stop_capability_rights_are_not_widened_for_transfer','stopcap',{
        param($s) Has $s 'DuplicateHandle(GetCurrentProcess(), originalProcess, GetCurrentProcess(), out duplicate, 0x101001, false, 0)'
    },'out duplicate, 0x101001, false, 0','out duplicate, 0x101041, false, 0'),
    @('dup_only_identity_uses_comparison_not_process_query_or_wait','lsame',{
        param($s) Has $s 'AssertOriginalIdentity(alive); Check(CompareObjectHandles(originalProcess, handle), "Compare exact original P transfer object"); AssertOriginalIdentity(alive);';foreach($bad in @('GetProcessId(handle)','GetProcessTimes(handle','WaitForSingleObject(handle')){Has $s $bad 0}
    },'CompareObjectHandles(originalProcess, handle)','CompareObjectHandles(originalProcess, originalProcess)'),
    @('full_original_process_queries_bind_birth_death_and_wait_both_sides','lidentity',{
        param($s) Has $s 'WaitForSingleObject(originalProcess, 0)' 2;Has $s 'GetProcessId(originalProcess) != (uint)originalPid';Has $s 'created != originalBirth || (!alive && (exited <= 0 || exited < created))';Has $s 'if (after != expected)'
    },'created != originalBirth','created <= 0'),
    @('transfer_capability_native_qualification_stays_unconditional','lblock',{
        param($s) Has $s 'private static void DemandQualifiedTransferCapability() { throw new InvalidOperationException("Original-P process-table transfer capability and CompareObjectHandles require independent native qualification."); }'
    },'throw new InvalidOperationException("Original-P process-table transfer capability and CompareObjectHandles require independent native qualification.");','return;'),
    @('launcher_retains_exact_new_capability_before_original_command_acquires','pretain',{
        param($s) Order $s @('if (!OwnsGuardian(candidate, originalStop) || command == null || !startCompleted || transferCapabilityAttempted || transferCapability != null)','transferCapabilityAttempted = true;','ProducerTransferCapability prepared = new ProducerTransferCapability(this, command);','Interlocked.CompareExchange(ref transferCapability, prepared, null)','command.AcquireProducerTransferCapability(this, prepared);')
    },'Interlocked.CompareExchange(ref transferCapability, prepared, null)','Interlocked.CompareExchange(ref transferCapability, null, null)'),
    @('command_transfer_acquisition_requires_original_suspended_root_and_job','acquire',{
        param($s) Has $s 'if (!started || resumeAttempted || resumed)';Order $s @('uint previous = SuspendThread(thread);','if (previous != 1)','uint restored = ResumeThread(thread);','if (restored != 2)','cap.AcquireOriginal(process, pid, birth);')
        Has $s 'RootWait() != WaitTimeout || JobAccounting().active != 1 || !owner.OwnsTransferCommand(this, cap)' 2
    },'cap.AcquireOriginal(process, pid, birth);','cap.AcquireOriginal(GetCurrentProcess(), pid, birth);'),
    @('guardian_retains_separate_launch_copy_before_acquire_and_eight_handle_birth','gstart',{
        param($s) Order $s @('transferSourceCapability = producer.PrepareGuardianTransferCapability(this, sourceCapability);','ProducerTransferCapability.LaunchCopy preparedTransferCopy = transferSourceCapability.PrepareLaunchCopy(this);','Interlocked.CompareExchange(ref transferLaunchCopy, preparedTransferCopy, null)','producer.AcquireGuardianTransferLaunchCopy(this, preparedTransferCopy);','IntPtr transferHandle = transferLaunchCopy.BorrowForLaunch(this);','Marshal.WriteIntPtr(inherited.Value, IntPtr.Size * 7, transferHandle);','new IntPtr(0x20002), inherited.Value, new IntPtr(IntPtr.Size * 8)')
    },'IntPtr.Size * 7, transferHandle','IntPtr.Size * 7, ownerHandle'),
    @('inherited_transfer_copy_has_only_dup_right_and_exact_inherit_flags','ccopy',{
        param($s) Has $s 'DuplicateHandle(GetCurrentProcess(), source.duplicate, GetCurrentProcess(), out handle, 0x40, true, 0)';Has $s 'if ((flags & 3) != 1)';Has $s 'DemandQualifiedTransferCapability(); attempted = true;'
    },'out handle, 0x40, true, 0','out handle, 0x40, true, 2'),
    @('command_offer_irreversibly_binds_slot_before_any_publication','publish',{
        param($s) Order $s @('owner.AssertCommandAdmission(this); SuccessBoundary(); RequirePreparedJob();','AssertEmptyJobBeforeBirth(); SuccessBoundary();','Interlocked.CompareExchange(ref compilerJobTransferBound, 1, 0)','owner.PublishOriginalJob(this, job);');Has $s 'catch (Exception failure) { Record(failure); throw; } finally { Leave(); }'
    },'Interlocked.CompareExchange(ref compilerJobTransferBound, 1, 0)','Interlocked.CompareExchange(ref compilerJobTransferBound, 0, 0)'),
    @('generic_containment_and_disposal_reject_irreversibly_offered_job','b',{
        param($s) $guard=Method $s 'private void RejectTransferredCompilerCleanup(';Has $guard 'if (Interlocked.CompareExchange(ref compilerJobTransferBound, 0, 0) != 0 || Interlocked.CompareExchange(ref compilerRootTransferBound, 0, 0) != 0)';Has $guard 'throw new InvalidOperationException("Offered compiler Job/root remain pinned until physical P exit; typed terminal cleanup is not implemented.");'
        foreach($signature in @('public bool PollContainment(','public void Dispose(')){Has (Method $s $signature) 'Enter(); try { RejectTransferredCompilerCleanup();'}
        Has $s 'compilerJobTransferBound, 0, 1' 0
    },'Interlocked.CompareExchange(ref compilerJobTransferBound, 0, 0) != 0','false'),
    @('offered_job_observation_binds_exact_owner_slot_and_never_created_child','offercheck',{
        param($s) $observe=Method $s 'internal void AssertOriginalJobOfferObservation('
        Has $observe '!Object.ReferenceEquals(producerCompilerOwner, owner)';Has $observe 'Interlocked.CompareExchange(ref compilerJobTransferBound, 0, 0) != 1';Has $observe 'job != originalJob || jobClose.Attempted';Has $observe 'RequirePreparedJob(); SuccessState();'
        foreach($bad in @('Enter(','WaitForSingleObject(','QueryInformationJobObject(','creationAttempted','NoChildCreated(')){Has $observe $bad 0}
        Has (Method $s 'private void AssertOriginalJobPrebirthAdmission(') 'owner.AssertBirthAdmission(this); AssertOriginalJobOfferObservation(owner, job); AssertEmptyJobBeforeBirth(); owner.AssertBirthAdmission(this);'
        $empty=Method $s 'private void AssertEmptyJobBeforeBirth(';Has $empty 'if (creationAttempted || !NoChildCreated())';Has $empty 'value.active != 0 || value.total != 0 || value.terminated != 0'
        Order (Method $s 'internal void StartProducerCompiler(') @('AssertOriginalJobPrebirthAdmission(owner);','owner.DemandQualifiedCompilerBirth(this);','StartSuspendedOriginal(owner);')
        Has (Method $s 'private void ProducerCompilerBirthBoundary(') 'AssertOriginalJobPrebirthAdmission(owner);'
        Has (Method $s 'private void StartSuspendedOriginal(') 'SuccessBoundary(); RequirePreparedJob(); AssertEmptyJobBeforeBirth(); ProducerCompilerBirthBoundary(owner); ClockAndFaultBoundary(); creationAttempted = true;'
    },'job != originalJob','false'),
    @('invocation_retains_exact_offer_binding_bytes_before_writer_visibility','ppublish',{
        param($s) Order $s @('command.AssertOriginalJobOfferObservation(owner, sourceJob);','Interlocked.CompareExchange(ref compilerJobOfferAttempted, 1, 0)','CompilerJobWire.Binding(channelNonce, producerPid, producerBirth, startedTick, deadlineTick, contextCapture.Length, contextCapture.Sha256)','CompilerJobOffer prepared = new CompilerJobOffer(owner, command, sourceJob, binding);','Interlocked.CompareExchange(ref compilerJobOffer, prepared, null)','AssertJobOfferObservation(prepared);')
    },'Interlocked.CompareExchange(ref compilerJobOffer, prepared, null)','Interlocked.CompareExchange(ref compilerJobOffer, null, null)'),
    @('p_reader_remains_pending_and_faults_eof_or_unsolicited_early_byte','pread',{
        param($s) Order $s @('int firstCount = inputStream.Read(first, 0, 1);','CompilerJobOffer offer = Interlocked.CompareExchange(ref compilerJobOffer, null, null);','int writeAttemptedAtRead = Interlocked.CompareExchange(ref compilerJobOfferWriteAttempted, 0, 0);','Require(firstCount == 1);','Require(offer != null && writeAttemptedAtRead == 1); AssertJobOfferObservation(offer);','byte[] actual = new byte[offer.ExpectedAck.Length];')
        Has $s 'WaitForPublishedCompilerJobOffer(' 0;Has $s 'actual[0] = first[0]; Require(actual[0] == offer.ExpectedAck[0]); int used = 1;'
    },'Require(firstCount == 1);','Require(firstCount >= 0);'),
    @('p_exact_job_ack_requires_all_bytes_then_original_offer_write_completion','pt',{
        param($s) $read=Method $s 'private void ReceiveOriginalCompilerJobAck('
        Has $read 'Require(actual.Length > 0 && actual.Length <= CompilerJobWire.MaximumBytes);';Has $read 'Require(count > 0 && count <= actual.Length - used);';Order $read @('CompilerJobWire.RequireExact(actual, offer.ExpectedAck);','AssertJobOfferObservation(offer); Require(Interlocked.CompareExchange(ref compilerJobAckReceived, 1, 0) == 0);')
        Has $read 'compilerJobOfferWritten' 0;Has $read 'Thread.Sleep(' 0
        Has (Method $s 'internal void WaitForCompilerJobAck(') 'while (Interlocked.CompareExchange(ref compilerJobAckReceived, 0, 0) != 1 || Interlocked.CompareExchange(ref compilerJobOfferWritten, 0, 0) != 1) { owner.AssertCommandAdmission(command); AssertJobOfferObservation(offer); Thread.Sleep(10); }'
        Has (Method $s 'internal void AssertCompilerJobAck(') 'Interlocked.CompareExchange(ref compilerJobOfferWriteAttempted, 0, 0) == 1 && Interlocked.CompareExchange(ref compilerJobOfferWritten, 0, 0) == 1 && Interlocked.CompareExchange(ref compilerJobAckReceived, 0, 0) == 1'
    },'CompilerJobWire.RequireExact(actual, offer.ExpectedAck);','/* exact received bytes unchecked */'),
    @('p_job_offer_writer_is_original_thread_and_flushes_before_publication','pwrite',{
        param($s) Has $s 'Require(Object.ReferenceEquals(Thread.CurrentThread, writerThread));';Order $s @('CompilerJobOffer offer = WaitForPublishedCompilerJobOffer();','Require(Interlocked.CompareExchange(ref compilerJobOfferWriteAttempted, 1, 0) == 0);','outputStream.Write(offer.Bytes, 0, offer.Bytes.Length);','outputStream.Flush();','AssertJobOfferObservation(offer); Require(Interlocked.CompareExchange(ref compilerJobOfferWritten, 1, 0) == 0);')
    },'outputStream.Flush();','/* flush omitted */'),
    @('actual_p_pipe_workers_append_one_job_phase_after_hello_admission','inv',{
        param($s) $read=Method $s 'private void StartReader(';Order $read @('Interlocked.Exchange(ref guardianAckReceived, 1);','ReceiveOriginalCompilerJobAck();','ReceiveOriginalCompilerRootAck();','ReceiveOriginalTerminalAcknowledgement();')
        $write=Method $s 'private void StartWriter(';Order $write @('outputStream.Flush();','Interlocked.Exchange(ref responseWritten, 1);','SendOriginalCompilerJobOffer();','SendOriginalCompilerRootOffer();','SendOriginalTerminal();')
        Has $s 'private const int MaximumFrameBytes = 256;'
        $terminal=Block $s 'private void ReceiveOriginalTerminalAcknowledgement('
        Has $terminal 'Require(Object.ReferenceEquals(Thread.CurrentThread, readerThread) && Interlocked.CompareExchange(ref compilerRootAckReceived, 0, 0) == 1);'
        Has $terminal 'int firstCount = inputStream.Read(first, 0, 1); RetainedProducerTerminal originalAtRead = Interlocked.CompareExchange(ref producerTerminal, null, null); int writeAttemptedAtRead = Interlocked.CompareExchange(ref terminalPreparedWriteAttempted, 0, 0); AssertTerminalChannelPending(); Require(firstCount == 1 && originalAtRead != null && writeAttemptedAtRead == 1);'
        Has $terminal 'Require(expected.Length > 0 && expected.Length <= TerminalWire.MaximumAckBytes);'
        Has $terminal 'Require(count > 0 && count <= actual.Length - used);'
        Order $terminal @('TerminalWire.RequireExact(actual, expected);','Require(Interlocked.CompareExchange(ref terminalAckReceived, 1, 0) == 0);','Require(Interlocked.CompareExchange(ref terminalEofReadAttempted, 1, 0) == 0);','int eof = inputStream.Read(first, 0, 1);','Interlocked.Exchange(ref terminalEofReadCount, eof); Interlocked.Exchange(ref terminalEofReadReturned, 1);','Require(eof == 0);','Require(Interlocked.CompareExchange(ref terminalGuardianEof, 1, 0) == 0);')
        Has $terminal 'Thread.Sleep(' 0;Has $terminal 'terminalPreparedWritten' 0
    },'ReceiveOriginalCompilerJobAck();','/* reader-owned Job ACK omitted */'),
    @('actual_compiler_worker_prepares_offers_waits_then_hits_closed_birth_gate','prun',{
        param($s) $run=Block $s 'private void Run('
        Order $run @('DemandQualifiedCompilerJobPreparation();','prepared.PrepareProducerCompilerJob(this);','Interlocked.Exchange(ref jobPrepared, 1);','prepared.PublishOriginalCompilerJobOffer(this);','WaitForOriginalCompilerJobAck(prepared);','AssertCommandAdmission(prepared); PrepareTerminalArtifactsBeforeBirth(prepared); prepared.StartProducerCompiler(this);')
        Order (Block $s 'internal void StartProducerCompiler(') @('!Object.ReferenceEquals(producerCompilerOwner, owner)','AssertOriginalJobPrebirthAdmission(owner);','owner.DemandQualifiedCompilerBirth(this);','StartSuspendedOriginal(owner);')
        Has (Block $s 'internal void DemandQualifiedCompilerBirth(') 'AssertBirthAdmission(original); throw new InvalidOperationException("Compiler birth requires separate native qualification and complete root/terminal supervision.");'
        Has (Block $s 'internal void AssertBirthAdmission(') 'AssertCommandAdmission(original); invocation.AssertCompilerJobAck(this, original); AssertCommandAdmission(original);'
        foreach($bad in @('.StartSuspended(','.Resume(','.Dispose(','.PollContainment(')){Has $run $bad 0}
    },'WaitForOriginalCompilerJobAck(prepared);','/* actual ACK omitted */'),
    @('actual_job_ack_never_removes_separate_compiler_birth_blocker','pblock',{
        param($s) Has $s 'internal void DemandQualifiedCompilerBirth(CompilerCommand original) { AssertBirthAdmission(original); throw new InvalidOperationException("Compiler birth requires separate native qualification and complete root/terminal supervision."); }'
    },'throw new InvalidOperationException("Compiler birth requires separate native qualification and complete root/terminal supervision.");','return;'),
    @('w_claim_is_single_cas_with_actual_offer_copy_and_retained_worker','claim',{
        param($s) Order $s @('IntPtr offered = Stratus.RuntimeBuild.CompilerJobWire.ParseOffer(actual, binding);','Require(Interlocked.CompareExchange(ref stopClosed, 0, 0) == 0 && Interlocked.CompareExchange(ref phase, 1, 0) == 0);','sourceHandle = offered; originalOffer = (byte[])actual.Clone();','worker = new Thread(Run); worker.IsBackground = true;','workerStartAttempted = true;','worker.Start();','workerStartReturned = true;')
    },'Interlocked.CompareExchange(ref phase, 1, 0)','Interlocked.CompareExchange(ref phase, 1, 1)'),
    @('w_native_job_transfer_has_independent_unconditional_qualification_gate','wblock',{
        param($s) Has $s 'private static void DemandQualifiedCompilerJobTransfer() { throw new InvalidOperationException("W compiler Job transfer is not independently qualified."); }'
    },'throw new InvalidOperationException("W compiler Job transfer is not independently qualified.");','return;'),
    @('w_duplicates_only_from_original_p_table_with_query_and_terminate_rights','wrun',{
        param($s) Has $s 'duplicateSucceeded = DuplicateHandle(originalTransfer, sourceHandle, GetCurrentProcess(), out localJob, 0x000C, false, 0);'
        Order $s @('DemandQualifiedCompilerJobTransfer();','duplicateAttempted = true;','duplicateSucceeded = DuplicateHandle(','duplicateError = Marshal.GetLastWin32Error();','duplicateReturned = true;','Require(duplicateSucceeded && ValidHandle(localJob));')
        Has $s 'flagsSucceeded && (observedFlags & 3) == 0'
    },'out localJob, 0x000C, false, 0','out localJob, 0x000C, true, 2'),
    @('w_verifies_exact_limits_query_size_and_only_kill_on_close','wt',{
        param($s) $run=Method $s 'private void Run(';Has $run 'Require(limitsSucceeded && limitsBytes == (uint)Marshal.SizeOf(typeof(ExtendedLimit))); AssertInitialLimits();'
        $limits=Method $s 'private void AssertInitialLimits(';Has $limits 'initialLimits.basic.flags == 0x2000';Has $limits 'initialLimits.basic.active == 0';Has $limits 'initialLimits.processMemory == UIntPtr.Zero && initialLimits.jobMemory == UIntPtr.Zero'
    },'initialLimits.basic.flags == 0x2000','(initialLimits.basic.flags & 0x2000) != 0'),
    @('w_requires_fresh_zero_job_accounting_before_ack_bytes_are_created','wrun',{
        param($s) Order $s @('Require(accountingSucceeded && accountingBytes == (uint)Marshal.SizeOf(typeof(Accounting)));','Require(initialAccounting.active == 0 && initialAccounting.total == 0 && initialAccounting.terminated == 0 && initialAccounting.user == 0 && initialAccounting.kernel == 0 && initialAccounting.periodUser == 0 && initialAccounting.periodKernel == 0 && initialAccounting.faults == 0);','acknowledgement = Stratus.RuntimeBuild.CompilerJobWire.Acknowledgement(binding, sourceHandle);','Interlocked.Exchange(ref verified, 1);')
    },'initialAccounting.total == 0','initialAccounting.total >= 0'),
    @('w_worker_publishes_settlement_as_final_action_even_after_fault','wrun',{
        param($s) Has $s 'catch (Exception) { Interlocked.Exchange(ref controlFailed, 1); } finally { Interlocked.Exchange(ref phase, 2); }';Has $s 'Interlocked.Exchange(ref phase, 2);' 1
    },'finally { Interlocked.Exchange(ref phase, 2); }','finally { Interlocked.Exchange(ref phase, 2); QueryAccounting(localJob); }'),
    @('w_ack_copy_requires_settled_verified_state_and_defensive_bytes','ack',{
        param($s) Has $s 'if (Interlocked.CompareExchange(ref stopClosed, 0, 0) != 0 || Interlocked.CompareExchange(ref phase, 0, 0) != 2 || Interlocked.CompareExchange(ref verified, 0, 0) != 1) return null;';Has $s 'Require(acknowledgement != null); byte[] result = (byte[])acknowledgement.Clone(); return Interlocked.CompareExchange(ref stopClosed, 0, 0) == 0 ? result : null;'
    },'Interlocked.CompareExchange(ref phase, 0, 0) != 2','false'),
    @('w_stop_atomically_prevents_unclaimed_transfer_without_erasing_claim','close',{
        param($s) Has $s 'internal void CloseAdmission() { Interlocked.Exchange(ref stopClosed, 1); Interlocked.CompareExchange(ref phase, 3, 0); }'
    },'Interlocked.CompareExchange(ref phase, 3, 0);','Interlocked.Exchange(ref phase, 3);'),
    @('w_stop_requires_physical_p_death_and_waits_claim_settlement_on_original_clock','settlement',{
        param($s) $settle=Block $s 'internal bool SettleAfterProducerDeath('
        Has $settle 'Require(originalStopClock != null && identityEstablished && ObserveProducer());';Has $settle 'while (Interlocked.CompareExchange(ref phase, 0, 0) == 1) { if (originalStopClock.ElapsedMilliseconds >= StopMilliseconds) return false; Thread.Sleep(10); }';Has $settle 'if (originalStopClock.ElapsedMilliseconds >= StopMilliseconds) return false; return IsSettledForStop;';Has $settle 'Stopwatch.StartNew(' 0
        $stop=Block $s 'internal bool StopAfterProducerDeath(';Has $stop 'Require(originalStopClock != null && identityEstablished && ObserveProducer() && IsSettledForStop);';Has $stop 'Require(rootTransfer == null || rootTransfer.IsSettledForStop);';Has $stop 'Stopwatch.StartNew(' 0
        Order (Block $s 'private static bool StopOriginalProducer(') @('if (!dead || stopClock.ElapsedMilliseconds >= StopMilliseconds) return false;','if (jobTransfer != null && !jobTransfer.SettleAfterProducerDeath(stopClock)) return false;','if (rootTransfer != null && !rootTransfer.SettleAfterProducerDeath(stopClock)) return false;','if (jobTransfer != null && !jobTransfer.StopAfterProducerDeath(stopClock)) return false;')
    },'while (Interlocked.CompareExchange(ref phase, 0, 0) == 1)','while (false)'),
    @('w_stop_never_treats_unknown_or_inconsistent_duplicate_as_absent','stop',{
        param($s) Has $s 'if (settled == 3) return true; Require(settled == 2); if (!duplicateAttempted) return true; if (!duplicateReturned) return false; if (!duplicateSucceeded) return false; if (!ValidHandle(localJob)) return false;'
    },'if (!duplicateReturned) return false;','if (!duplicateReturned) return true;'),
    @('w_stop_terminates_known_local_job_once_then_requires_actual_zero_accounting','stop',{
        param($s) Order $s @('Require(!terminateAttempted);','terminateAttempted = true;','terminateSucceeded = TerminateJobObject(localJob, 203);','terminateError = Marshal.GetLastWin32Error(); terminateReturned = true;','if (!terminateSucceeded || originalStopClock.ElapsedMilliseconds >= StopMilliseconds) return false;','Require(!stopQueryAttempted || (stopQueryReturned && stopQuerySucceeded));','stopQuerySucceeded = QueryAccounting(localJob, 1, out stoppedAccounting,','if (!stopQuerySucceeded || stoppedAccountingBytes != (uint)Marshal.SizeOf(typeof(Accounting))) return false;','if (stoppedAccounting.active == 0) { if (originalStopClock.ElapsedMilliseconds >= StopMilliseconds) return false; Interlocked.Exchange(ref exactJobStopped, 1); return true; }')
    },'if (stoppedAccounting.active == 0)','if (true)'),
    @('w_original_process_comparison_brackets_actual_cross_process_duplication','wrun',{
        param($s) Order $s @('preCompareAttempted = true;','preCompareSucceeded = CompareObjectHandles(producer, originalTransfer);','preCompareError = Marshal.GetLastWin32Error(); preCompareReturned = true; Require(preCompareSucceeded);','duplicateSucceeded = DuplicateHandle(','duplicateReturned = true;','AssertNoLocalAlias();','postCompareAttempted = true;','postCompareSucceeded = CompareObjectHandles(producer, originalTransfer);','postCompareError = Marshal.GetLastWin32Error(); postCompareReturned = true; Require(postCompareSucceeded);')
    },'Require(postCompareSucceeded);','/* post-duplicate identity comparison omitted */'),
    @('w_local_job_rejects_all_process_control_context_and_standard_aliases','wt',{
        param($s) $m=Method $s 'private void AssertNoLocalAlias(';Has $m 'Require(localJob != producer && localJob != producerTransfer && localJob != controlReadHandle && localJob != controlWriteHandle && localJob != contextSection && localJob != GetStdHandle(-10) && localJob != GetStdHandle(-11) && localJob != GetStdHandle(-12));'
        Has (Method $s 'private void Run(') 'AssertNoLocalAlias();';Has (Method $s 'internal bool StopAfterProducerDeath(') 'AssertNoLocalAlias();'
    },'localJob != contextSection','true'),
    @('w_stop_freshly_proves_job_type_before_any_job_termination','stop',{
        param($s) Order $s @('AssertNoLocalAlias();','Require(!stopTypeAttempted);','stopTypeAttempted = true;','stopTypeSucceeded = QueryLimits(localJob, 9, out stopTypeLimits,','stopTypeError = Marshal.GetLastWin32Error(); stopTypeReturned = true;','if (!stopTypeSucceeded || stopTypeBytes != (uint)Marshal.SizeOf(typeof(ExtendedLimit)) || originalStopClock.ElapsedMilliseconds >= StopMilliseconds) return false;','terminateSucceeded = TerminateJobObject(localJob, 203);')
    },'!stopTypeSucceeded || stopTypeBytes != (uint)Marshal.SizeOf(typeof(ExtendedLimit))','false'),
    @('actual_w_entry_binds_fourteenth_transfer_argument_and_all_context_aliases','wmain',{
        param($s) Order $s @('Require(args != null && args.Length == 14 && args[0] == "--stratus-owner-watch-v8");','bool dead = ObserveProducer(); identityEstablished = true;','AdoptProducerTransfer(args[13], args[7], args[8]);','contextSection = ControlEndpoint(args[10]);','contextCapture = Stratus.RuntimeBuild.ContextCapture.Prepare(args[10], args[11], args[12], new IntPtr[] { producer, ControlEndpoint(args[7]), ControlEndpoint(args[8]), producerTransfer });')
    },'ControlEndpoint(args[8]), producerTransfer','ControlEndpoint(args[8]), producer'),
    @('actual_w_adopts_distinct_dup_capability_clears_inherit_and_compares_original_object','wadopt',{
        param($s) Has $s 'Require(producerTransfer != producer && producerTransfer != ControlEndpoint(read) && producerTransfer != ControlEndpoint(write));'
        Order $s @('Require(SetHandleInformation(producerTransfer, 1, 0));','Require(GetHandleInformation(producerTransfer, out flags) && (flags & 3) == 0);','transferCompareAttempted = true;','transferCompareSucceeded = CompareObjectHandles(producer, producerTransfer);','transferCompareError = Marshal.GetLastWin32Error(); transferCompareReturned = true;','Require(transferCompareSucceeded);','Require(!ObserveProducer());')
        foreach($bad in @('WaitForSingleObject(producerTransfer','GetProcessId(producerTransfer','GetProcessTimes(producerTransfer','OpenProcess(')){Has $s $bad 0}
    },'CompareObjectHandles(producer, producerTransfer)','CompareObjectHandles(producer, producer)'),
    @('actual_w_retains_owner_before_workers_and_persistent_reader_claims_actual_offer','wcontrol',{
        param($s) Order $s @('Require(jobTransfer == null && rootTransfer == null && Interlocked.CompareExchange(ref stopAttempted, 0, 0) == 0 && Interlocked.CompareExchange(ref controlFailed, 0, 0) == 0);','jobTransfer = new GuardianJobTransfer(producerTransfer, binding);','rootTransfer = new GuardianRootTransfer(producerTransfer, jobTransfer, contextCapture.AdmittedMetadata, binding);','controlReader = new Thread(delegate()')
        $reader=Block $s 'controlReader = new Thread(delegate()'
        Has $reader 'int maximum = stage == 0 ? 256 : (stage == 1 ? Stratus.RuntimeBuild.CompilerJobWire.MaximumBytes : (stage == 2 ? Stratus.RuntimeBuild.CompilerRootWire.MaximumBytes : (stage == 3 ? Stratus.RuntimeBuild.TerminalWire.MaximumPreparedBytes : Stratus.RuntimeBuild.TerminalWire.MaximumCommitBytes)));'
        Has $reader 'Require(stage < 5 && offset < maximum && (buffer[i] == 10 || (buffer[i] >= 32 && buffer[i] <= 126)));'
        Has $reader 'count = controlInput.Read(buffer, 0, buffer.Length); int admissionAttemptAtRead = Interlocked.CompareExchange(ref controlAckWriteAttempted, 0, 0); int jobAckAttemptAtRead = Interlocked.CompareExchange(ref jobAckWriteAttempted, 0, 0); int rootAckAttemptAtRead = Interlocked.CompareExchange(ref rootAckWriteAttempted, 0, 0); int terminalAckAttemptAtRead = Interlocked.CompareExchange(ref terminalObservation.AckWriteAttempted, 0, 0); int terminalCloseAttemptAtRead = Interlocked.CompareExchange(ref terminalObservation.HalfCloseAttempted, 0, 0); int preparedObservedAtRead = Interlocked.CompareExchange(ref terminalObservation.PreparedObserved, 0, 0); Stratus.RuntimeBuild.TerminalRecord preparedAtRead = preparedObservedAtRead == 1 ? terminalObservation.Record : null; if (stage < 3) AssertControlWorker(ref workerTick); else AssertTerminalControlWorker(ref workerTick);'
        Has $reader 'if (stage == 1) Require(admissionAttemptAtRead == 1); if (stage == 2) Require(jobAckAttemptAtRead == 1);'
        Has $reader 'Require(Interlocked.CompareExchange(ref controlAckPermit, 0, 0) == 1 && Interlocked.CompareExchange(ref controlHelloWritten, 0, 0) == 1); AssertControlWorker(ref workerTick); jobTransfer.ClaimOffer(actual);'
        Has $reader 'else if (stage == 2) { Require(jobAckAttemptAtRead == 1); AssertControlWorker(ref workerTick); rootTransfer.ClaimOffer(actual); }'
        Has $reader 'else if (stage == 3) { ReceiveNormalTerminalPrepared(actual, rootAckAttemptAtRead, ref workerTick); } else { ReceiveNormalTerminalCommit(actual, preparedAtRead, terminalAckAttemptAtRead, terminalCloseAttemptAtRead, ref workerTick); }'
        Has $reader '++stage; offset = 0;'
        Has $reader 'if (count == 0) { Require(stage == 5 && offset == 0 && terminalAckAttemptAtRead == 1 && terminalCloseAttemptAtRead == 1 && preparedAtRead != null && Object.ReferenceEquals(preparedAtRead, terminalObservation.Record) && Interlocked.CompareExchange(ref terminalObservation.CommitObserved, 0, 0) == 1); Interlocked.Exchange(ref terminalObservation.InputEofObserved, 1); return; }'
        Has $reader 'catch (Exception) { Interlocked.Exchange(ref controlFailed, 1); } finally { Interlocked.Exchange(ref terminalObservation.ReaderSettled, 1); }';Has $reader 'Interlocked.Exchange(ref controlFailed, 1);' 1
    },'jobTransfer.ClaimOffer(actual);','/* actual offer never claimed */'),
    @('actual_w_single_writer_serializes_hello_admission_and_settled_job_ack','wControlTerminal',{
        param($s) $control=Block $s 'private static void AdoptControl('; $writer=Block $control 'controlWriter = new Thread(delegate()'
        Order $writer @('controlOutput.Write(hello, 0, hello.Length); controlOutput.Flush();','Interlocked.Exchange(ref controlHelloWritten, 1);','while (Interlocked.CompareExchange(ref controlAckPermit, 0, 0) == 0)','Require(Interlocked.CompareExchange(ref controlAckWriteAttempted, 1, 0) == 0);','controlOutput.Write(acknowledgement, 0, acknowledgement.Length); controlOutput.Flush();','Interlocked.Exchange(ref controlAckWritten, 1);','while ((jobAcknowledgement = jobTransfer.CopyAcknowledgementIfSettled()) == null)','AssertControlWorker(ref workerTick); Require(Interlocked.CompareExchange(ref jobAckWriteAttempted, 1, 0) == 0);','controlOutput.Write(jobAcknowledgement, 0, jobAcknowledgement.Length); controlOutput.Flush();','jobAckWriteReturned = true; AssertControlWorker(ref workerTick);','Interlocked.Exchange(ref jobAckWritten, 1);','while ((rootAcknowledgement = rootTransfer.CopyAcknowledgementIfSettled()) == null)','Require(Interlocked.CompareExchange(ref rootAckWriteAttempted, 1, 0) == 0);','controlOutput.Write(rootAcknowledgement, 0, rootAcknowledgement.Length); controlOutput.Flush();','rootAckWriteReturned = true; AssertControlWorker(ref workerTick); Interlocked.Exchange(ref rootAckWritten, 1);','SendNormalTerminalAcknowledgement(ref workerTick);')
        Has $control 'controlWriter = new Thread(' 1
        Has $control 'controlOutput.Write(' 4;Has $writer 'controlOutput.Write(' 4;Has $control 'controlOutput.Flush(' 4;Has $writer 'controlOutput.Flush(' 4
        $terminal=Block $s 'private static void SendNormalTerminalAcknowledgement('
        Has $terminal 'Require(Object.ReferenceEquals(Thread.CurrentThread, controlWriter));'
        Has $terminal 'Require(Interlocked.CompareExchange(ref rootAckWritten, 0, 0) == 1 && rootAckWriteReturned && jobTransfer.IsSettledForNormal && rootTransfer.IsSettledForNormal && terminalObservation.Record != null);'
        Order $terminal @('byte[] acknowledgement = Stratus.RuntimeBuild.TerminalWire.Acknowledgement(terminalObservation.Record);','Require(acknowledgement.Length <= Stratus.RuntimeBuild.TerminalWire.MaximumAckBytes);','DemandQualifiedNormalTerminalObservation();','Require(Interlocked.CompareExchange(ref terminalObservation.AckWriteAttempted, 1, 0) == 0);','controlOutput.Write(acknowledgement, 0, acknowledgement.Length); controlOutput.Flush();','terminalObservation.AckWriteReturned = true;','Interlocked.Exchange(ref terminalObservation.AckWritten, 1);','Require(Interlocked.CompareExchange(ref terminalObservation.HalfCloseAttempted, 1, 0) == 0);','terminalObservation.HalfCloseSucceeded = CloseHandle(terminalObservation.WriteHandle);','terminalObservation.HalfCloseError = Marshal.GetLastWin32Error();','Interlocked.Exchange(ref terminalObservation.HalfCloseReturned, 1);','Require(terminalObservation.HalfCloseSucceeded);','terminalObservation.WriteWrapperInvalidationAttempted = true;','terminalObservation.WriteSafe.SetHandleAsInvalid();','terminalObservation.WriteWrapperInvalidationReturned = true;')
        Has $terminal 'controlOutput.Write(' 1;Has $terminal 'controlOutput.Flush(' 1;Has $terminal 'new Thread(' 0
    },'jobTransfer.CopyAcknowledgementIfSettled()','new byte[1]'),
    @('actual_w_stop_closes_admission_then_proves_p_death_and_job_settlement_same_clock','wstop',{
        param($s) Order $s @('Interlocked.Exchange(ref controlFailed, 1);','if (jobTransfer != null) jobTransfer.CloseAdmission();','if (rootTransfer != null) rootTransfer.CloseAdmission();','if (Interlocked.CompareExchange(ref stopAttempted, 1, 0) != 0) return false;','stopClock = Stopwatch.StartNew();','if (!identityEstablished) return false;','bool dead = ObserveProducer();','producerStopSucceeded = TerminateProcess(producer, 203);','if (!dead || stopClock.ElapsedMilliseconds >= StopMilliseconds) return false;','if (jobTransfer != null && !jobTransfer.SettleAfterProducerDeath(stopClock)) return false;','if (rootTransfer != null && !rootTransfer.SettleAfterProducerDeath(stopClock)) return false;','if (jobTransfer != null && !jobTransfer.StopAfterProducerDeath(stopClock)) return false;','if (rootTransfer != null && !rootTransfer.ObservePhysicalDeathAfterJobStop(stopClock)) return false;','return stopClock.ElapsedMilliseconds < StopMilliseconds;')
        Has $s 'Stopwatch.StartNew(' 1;Has $s 'return true;' 0
    },'if (jobTransfer != null && !jobTransfer.StopAfterProducerDeath(stopClock)) return false;','/* transferred Job settlement omitted */'),
    @('actual_w_death_and_fault_paths_reach_common_stop_without_blocking_main','wnormal',{
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
    },'if (!MayDrainNormalTerminal()) break;','if (!MayDrainNormalTerminal()) return 203;'),
    @('outgoing_wire_helpers_include_original_job_handle_and_exact_ascii_frame','wire',{
        param($s) Has (Method $s 'internal static byte[] Offer(') 'return Frame(OfferPrefix, binding, sourceHandle);';Has (Method $s 'internal static byte[] Acknowledgement(') 'return Frame(AckPrefix, binding, sourceHandle);'
        $frame=Method $s 'private static byte[] Frame(';Has $frame 'string text = prefix + binding + " " + HandleText(sourceHandle) + "\n";';Has $frame 'Require(text.Length > 0 && text.Length <= MaximumBytes);';Has $frame 'for (int i = 0; i < text.Length - 1; ++i) Require(text[i] >= 32 && text[i] <= 126);'
        $handle=Method $s 'internal static string HandleText(';Has $handle 'handle != IntPtr.Zero && handle != new IntPtr(-1) && handle != new IntPtr(-2) && handle != new IntPtr(-3) && handle != new IntPtr(-4) && handle != new IntPtr(-5) && handle != new IntPtr(-6)';Has $handle 'IntPtr.Size == 8 ? raw <= Int64.MaxValue : raw < UInt32.MaxValue'
    },'HandleText(sourceHandle) + "\n"','"1\n"'),
    @('compiler_owner_publication_calls_actual_invocation_with_original_job','po',{
        param($s) $m=Method $s 'internal void PublishOriginalJob(';Order $m @('AssertCommandAdmission(original); Require(Interlocked.CompareExchange(ref jobPrepared, 0, 0) == 1);','original.AssertOriginalJobOfferObservation(this, originalJob);','invocation.PublishCompilerJobOffer(this, original, originalJob);')
        Has $m 'AssertCommandAdmission(original);' 2
    },'invocation.PublishCompilerJobOffer(this, original, originalJob);','/* actual invocation link omitted */'),
    @('compiler_owner_ack_wait_calls_actual_invocation_not_a_synthetic_receipt','po',{
        param($s) Has (Method $s 'private void WaitForOriginalCompilerJobAck(') 'AssertCommandAdmission(original); invocation.WaitForCompilerJobAck(this, original); AssertCommandAdmission(original);'
    },'invocation.WaitForCompilerJobAck(this, original);','/* actual reader-owned wait omitted */'),
    @('invocation_wait_requires_original_refs_reader_ack_and_writer_completion','pt',{
        param($s) $m=Method $s 'internal void WaitForCompilerJobAck(';Has $m 'Require(offer != null && Object.ReferenceEquals(offer.Owner, owner) && Object.ReferenceEquals(offer.Command, command));';Has $m 'while (Interlocked.CompareExchange(ref compilerJobAckReceived, 0, 0) != 1 || Interlocked.CompareExchange(ref compilerJobOfferWritten, 0, 0) != 1) { owner.AssertCommandAdmission(command); AssertJobOfferObservation(offer); Thread.Sleep(10); }';Has $m 'AssertJobOfferObservation(offer); Require(Interlocked.CompareExchange(ref compilerJobOfferWritten, 0, 0) == 1); owner.AssertCommandAdmission(command);'
        Has (Method $s 'private void ReceiveOriginalCompilerJobAck(') 'Require(Object.ReferenceEquals(Thread.CurrentThread, readerThread));'
    },'Interlocked.CompareExchange(ref compilerJobAckReceived, 0, 0) != 1','false'),
    @('invocation_offer_observation_follows_exact_owner_and_pinned_command_backlinks','pt',{
        param($s) $m=Method $s 'private void AssertJobOfferObservation(';Has $m 'Object.ReferenceEquals(Interlocked.CompareExchange(ref compilerJobOffer, null, null), offer)';Has $m 'Interlocked.CompareExchange(ref compilerJobOfferAttempted, 0, 0) == 1';Has $m 'AssertCompilerOwner(offer.Owner); offer.Owner.AssertTransferObservation(offer.Command); offer.Command.AssertOriginalJobOfferObservation(offer.Owner, offer.SourceJob); AssertInputAdmission();'
    },'offer.Command.AssertOriginalJobOfferObservation(offer.Owner, offer.SourceJob);','/* pinned slot observation omitted */'),
    @('cross_thread_transfer_observation_requires_phase_known_registration_and_original_clock','po',{
        param($s) $m=Method $s 'internal void AssertTransferObservation(';Has $m 'Interlocked.CompareExchange(ref failed, 0, 0) == 0 && Interlocked.CompareExchange(ref phase, 0, 0) == 2 && Interlocked.CompareExchange(ref jobPrepared, 0, 0) == 1';Has $m 'original != null && Object.ReferenceEquals(command, original) && preparation != null';Has $m 'Interlocked.CompareExchange(ref registrationAttempted, 0, 0) == 1 && Interlocked.CompareExchange(ref registrationReturned, 0, 0) == 1 && Interlocked.CompareExchange(ref registrationSucceeded, 0, 0) == 1';Has $m 'invocation.AssertCompilerOwner(this); invocation.AssertCompilerClock(this);';Has $m 'AssertWorker(' 0
    },'Interlocked.CompareExchange(ref registrationReturned, 0, 0) == 1','true'),
    @('original_p_closure_precedes_transfer_cap_close_and_command_release','p',{
        param($s) Order (Method $s 'public bool PollContainment(') @('if (!command.PollContainment()) return false;','if (stopCapability != null) stopCapability.CloseAfterProducerDeath();','if (transferCapability != null) transferCapability.CloseAfterProducerDeath();','containmentProven = true; return true; } catch')
        Order (Method $s 'public void Dispose(') @('if (!containmentProven && !normalClosureProven)','if (transferCapability != null && !transferCapability.IsClosed)','releaseStarted = true;','if (command != null) command.Dispose();')
        $normal=Method $s 'internal CompilerResult PollNormalTerminal('
        Has $normal '!continuationCompleted || !OwnsGuardian(originalGuardian, stopCapability) || command == null'
        Order $normal @('CompilerResult observed = command.PollResult();','if (observed == null) return null;','if (normalResult != null && !Object.ReferenceEquals(normalResult, observed))','normalResult = observed;','long nativeExit = command.ObserveProducerNormalExit(this, observed);','normalExitFileTime = nativeExit;','if (stopCapability != null) stopCapability.CloseAfterProducerDeath();','if (transferCapability != null) transferCapability.CloseAfterProducerDeath();','RequireAdmission(); originalGuardian.AssertNormalProducerObservation(this);','normalClosureProven = true; return normalResult;')
        Has $normal 'containmentProven = true;' 0
    },'if (transferCapability != null && !transferCapability.IsClosed)','if (false)'),
    @('guardian_copy_retires_after_creation_and_closes_only_after_exact_p_w_containment','g',{
        param($s) Order (Method $s 'public void StartRunning(') @('creationSucceeded = created; creationReturned = true;','transferLaunchCopy.RetireLaunchInheritance(this);','transferLaunchCopy.AssertLive(); transferLaunchCopy.AssertLaunchInheritanceRetired();')
        Order (Method $s 'public bool PollContainment(') @('if (!producerContained) { if (!producer.PollContainment()) return false; producerContained = true; }','RequireOriginalCreation(); if (!RootBoundary(false)) return false;','try { readersReleased = ReleaseSettledParentReaders(); } catch (Exception failure) { pending.Add(failure); }','if (!readersReleased || !CollectorsClosed()) return false;','AssertAllOriginalPipeResourcesReleased();','closureProven = true;','if (transferLaunchCopy != null) transferLaunchCopy.CloseAfterContainment();')
        Has (Method $s 'internal void AssertTransferCopyReleaseAllowed(') 'candidate == null || !Object.ReferenceEquals(transferLaunchCopy, candidate) || (!producerContained && !normalTerminalProven) || !closureProven'
        $normal=Method $s 'internal BuildTerminalClosure PollNormalTerminal('
        Order $normal @('DemandQualifiedLauncherTerminal();','CompilerResult actualProducer = producer.PollNormalTerminal(this);','if (normalWait != WaitObject) throw new InvalidOperationException("Original W wait failed.");','if (!CollectorsClosed()) return null;','if (normalExitCode != 0) throw new InvalidDataException("Original W exited nonzero.");','long peerPExit = TerminalWire.RequireGuardianTerminal(terminal, actualTerminal, nonce);','long actualPExit = producer.OriginalNormalExitFileTime(this, actualProducer);','if (peerPExit != actualPExit)','normalClosure = new BuildTerminalClosure(this, actualTerminal, actualPExit, normalExited);','closureProven = true; normalTerminalProven = true;','if (transferLaunchCopy != null) transferLaunchCopy.CloseAfterContainment();')
        Has $normal 'captureFailed || failures.Count != 0 || stderr.Length != 0 || !stdoutEof || !stderrEof || !stdoutDone || !stderrDone'
        Has $normal 'producerContained = true;' 0
        foreach($sig in @('internal void AssertProducerReleaseAllowed(','internal void AssertPreloadReleaseAllowed(','public void Dispose(')){Has (Method $s $sig) '(transferLaunchCopy != null && !transferLaunchCopy.IsClosed) || (transferSourceCapability != null && !transferSourceCapability.IsClosed)'}
    },'transferLaunchCopy.RetireLaunchInheritance(this);','/* inheritance not retired */'),
    @('durable_transfer_close_rejects_unknown_outcomes_and_requires_original_physical_death','l',{
        param($s) $m=Method $s 'internal void CloseAfterProducerDeath(';Order $m @('if (closeAttempted && !closeReturned)','DuplicateDisposition disposition = Disposition();','if (disposition != DuplicateDisposition.Owned)','AssertSameOriginal(duplicate, false);','closeAttempted = true; closeReturned = false; closeSucceeded = false;','bool released = CloseHandle(duplicate);','closeSucceeded = released; closeReturned = true;','if (!released)')
        Has (Method $s 'private DuplicateDisposition Disposition(') 'acquisitionStarted && duplicateAttempted && duplicateReturned && duplicateSucceeded && Valid(duplicate)'
    },'if (closeAttempted && !closeReturned) throw new InvalidOperationException("Unknown P transfer close outcome cannot be queried or retried.");','if (false) throw new InvalidOperationException("Unknown P transfer close outcome cannot be queried or retried.");'),
    @('transfer_copy_close_requires_exact_guardian_and_never_retries_unknown_close','copy',{
        param($s) $m=Method $s 'internal void CloseAfterContainment(';Order $m @('guardian.AssertTransferCopyReleaseAllowed(this);','if (closeAttempted && !closeReturned)','RequireKnownOwned(); source.AssertSameOriginal(handle, false);','bool released = CloseHandle(handle);')
        Has (Method $s 'internal void RetireLaunchInheritance(') 'inheritanceRetirementSucceeded = changed; inheritanceRetirementReturned = true;'
        Has (Method $s 'internal void AssertLaunchInheritanceRetired(') '!inheritanceRetirementAttempted || !inheritanceRetirementReturned || !inheritanceRetirementSucceeded || !inheritanceRetirementVerified'
    },'guardian.AssertTransferCopyReleaseAllowed(this);','/* exact guardian closure omitted */'),
    @('private_offer_constructor_binds_exact_owner_command_slot_and_opposite_wire_roles','pt',{
        param($s) $offer=Block $s 'private sealed class CompilerJobOffer';Has $offer 'internal readonly ProducerCompilerOwner Owner;';Has $offer 'internal readonly CompilerCommand Command;';Has $offer 'internal readonly IntPtr SourceJob;';Has $offer 'internal readonly byte[] Bytes, ExpectedAck;'
        Has $offer 'internal readonly string Binding;'
        Has (Method $offer 'internal CompilerJobOffer(') 'Owner = owner; Command = command; SourceJob = sourceJob; Binding = binding; Bytes = CompilerJobWire.Offer(binding, sourceJob); ExpectedAck = CompilerJobWire.Acknowledgement(binding, sourceJob);'
    },'ExpectedAck = CompilerJobWire.Acknowledgement(binding, sourceJob);','ExpectedAck = CompilerJobWire.Offer(binding, sourceJob);')
)
Assert-JobTransfer ($rows.Count -eq 54) 'Historical case inventory differs before new immutable-limit coverage.'
# Separate directed pair; the historical IDs/order above remain unchanged.
$rows+=@(
    ,@('normal_job_closure_preserves_all_original_immutable_limits_before_accounting','wnormal',{
        param($s) $job=Block $s 'internal void ProveNormalClosureAfterProducerExit(GuardianRootTransfer originalRootOwner)'
        Has (Block $s 'private static bool TryCompleteNormalTerminal(') 'jobTransfer.ProveNormalClosureAfterProducerExit(rootTransfer);'
        Order $job @('normalTypeSucceeded = QueryLimits(localJob, 9, out normalTypeLimits,','Require(normalTypeSucceeded && normalTypeBytes == (uint)Marshal.SizeOf(typeof(ExtendedLimit)));','Require(normalTypeLimits.basic.flags == initialLimits.basic.flags && normalTypeLimits.basic.processTime == initialLimits.basic.processTime && normalTypeLimits.basic.jobTime == initialLimits.basic.jobTime && normalTypeLimits.basic.min == initialLimits.basic.min && normalTypeLimits.basic.max == initialLimits.basic.max && normalTypeLimits.basic.active == initialLimits.basic.active && normalTypeLimits.basic.affinity == initialLimits.basic.affinity && normalTypeLimits.basic.priority == initialLimits.basic.priority && normalTypeLimits.basic.scheduling == initialLimits.basic.scheduling && normalTypeLimits.processMemory == initialLimits.processMemory && normalTypeLimits.jobMemory == initialLimits.jobMemory);','normalQueryAttempted = true;','normalQuerySucceeded = QueryAccounting(localJob, 1, out normalAccounting,','Require(normalQuerySucceeded && normalAccountingBytes == (uint)Marshal.SizeOf(typeof(Accounting)) && normalAccounting.active == 0 && normalAccounting.total == 1 && normalAccounting.terminated == 0);','Interlocked.Exchange(ref normalClosureProven, 1);')
    },'normalTypeLimits.basic.flags == initialLimits.basic.flags','true')
)
$expectedPairs=55;$expected=110
Assert-JobTransfer ($rows.Count -eq $expectedPairs -and $expected -eq 2*$expectedPairs) 'Fixed case inventory differs.'
$executed=0;$results=[Collections.Generic.List[object]]::new()
foreach($row in $rows){
    Assert-JobTransfer ($row.Count -eq 5) 'Fixed row shape differs.'
    $selected=$blocks[$row[1]];$check=$row[2]
    & $check $selected;$executed++;$results.Add([pscustomobject]@{name=$row[0];variant='original_source';passed=$true})
    $mutated=Replace-Tokens $selected $row[3] $row[4];$rejected=$false
    try{& $check $mutated}catch{
        Assert-JobTransfer ($_.Exception.Message.StartsWith('Compiler Job transfer source contract:',[StringComparison]::Ordinal)) ('Unexpected negative-control exception: '+$row[0])
        $rejected=$true
    }
    Assert-JobTransfer $rejected ('Weakening mutation survived: '+$row[0])
    $executed++;$results.Add([pscustomobject]@{name=$row[0];variant='mutated_source_rejected';passed=$true})
}
Assert-JobTransfer ($executed -eq $expected -and $results.Count -eq $expected) 'Fixed cases did not complete.'
$before=[Collections.Generic.List[object]]::new();$after=[Collections.Generic.List[object]]::new()
foreach($name in $sources.Keys){
    $old=$sources[$name];$latest=Read-JobTransferSource $old.path
    Assert-JobTransfer ($latest.sha256 -ceq $old.sha256) ('Subject changed during inspection: '+$name)
    $before.Add([pscustomobject]@{name=$name;path=$old.path;sha256=$old.sha256})
    $after.Add([pscustomobject]@{name=$name;path=$latest.path;sha256=$latest.sha256})
}
$testAfter=Read-JobTransferSource $PSCommandPath
Assert-JobTransfer ($testAfter.sha256 -ceq $testBefore.sha256) 'Test changed during inspection.'
[pscustomobject]@{
    format='stratus-compiler-job-transfer-source-contract-tests-v1'
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
    cases=$results.ToArray()
}|ConvertTo-Json -Depth 6 -Compress
