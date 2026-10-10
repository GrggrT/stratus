param()
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
# Fixed source data only. Never compile, load, evaluate or invoke a target.
# All callbacks below are literal test predicates; mutations remain token data.
# This suite cannot qualify the compiler environment, original clock or native ownership.
function Assert-ProducerCompiler {param([bool]$Okay,[string]$Message) if(-not $Okay){throw ('Producer compiler source contract: '+$Message)}}
function Read-ProducerCompilerSource {
    param([string]$Path)
    $full=[IO.Path]::GetFullPath($Path);$length=[IO.FileInfo]::new($full).Length
    Assert-ProducerCompiler ($length -gt 0 -and $length -le 1048576) 'Inspection file exceeds one MiB.'
    $raw=[IO.File]::ReadAllBytes($full);Assert-ProducerCompiler ($raw.LongLength -eq $length) 'File length changed while reading.'
    $source=[Text.UTF8Encoding]::new($false,$true).GetString($raw)
    $hash=[Security.Cryptography.SHA256]::Create()
    try{$digest=[BitConverter]::ToString($hash.ComputeHash($raw)).Replace('-','').ToLowerInvariant()}finally{$hash.Dispose()}
    return [pscustomobject]@{path=$full;text=$source;sha256=$digest}
}
function Tokens {
    param([string]$Text)
    $pattern=@'
//[^\r\n]* | /\*.*?\*/ | \#[^\r\n]* | @"(?:""|[^"])*" | "(?:\\.|[^"\\])*" | '(?:\\.|[^'\\])*'
| [A-Za-z_][A-Za-z_0-9]* | [0-9]+ | == | != | >= | <= | \+\+ | -- | && | \|\| | => | [^\s]
'@
    $result=[Collections.Generic.List[string]]::new()
    $options=[Text.RegularExpressions.RegexOptions]::Singleline -bor [Text.RegularExpressions.RegexOptions]::IgnorePatternWhitespace
    foreach($m in [regex]::Matches($Text,$pattern,$options,[TimeSpan]::FromSeconds(5))){
        if(-not $m.Value.StartsWith('//') -and -not $m.Value.StartsWith('/*') -and -not $m.Value.StartsWith('#')){$result.Add($m.Value)}
    }
    Assert-ProducerCompiler ($result.Count -le 262144) 'Token inspection bound exceeded.'
    return ,$result.ToArray()
}
function Find {
    param([string[]]$Source,[string]$Sequence)
    $wanted=Tokens $Sequence
    Assert-ProducerCompiler ($wanted.Count -gt 0) 'Empty predicate.'
    $found=[Collections.Generic.List[int]]::new()
    for($i=0;$i -le $Source.Count-$wanted.Count;$i++){
        $same=$true
        for($j=0;$j -lt $wanted.Count;$j++){if($Source[$i+$j] -cne $wanted[$j]){$same=$false;break}}
        if($same){$found.Add($i)}
    }
    return ,$found.ToArray()
}
function Has {param([string[]]$Source,[string]$Sequence,[int]$Count=1) Assert-ProducerCompiler ((Find $Source $Sequence).Count -eq $Count) ('Sequence differs: '+$Sequence)}
function Order {
    param([string[]]$Source,[string[]]$Sequences)
    $last=-1
    foreach($sequence in $Sequences){$found=Find $Source $sequence;Assert-ProducerCompiler ($found.Count -eq 1 -and $found[0] -gt $last) ('Order differs: '+$sequence);$last=$found[0]}
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
    Assert-ProducerCompiler ($found.Count -eq 1) ('Missing or ambiguous block: '+$Signature)
    $open=$found[0]
    while($open -lt $Source.Count -and $Source[$open] -cne '{'){$open++}
    Assert-ProducerCompiler ($open -lt $Source.Count) 'Opening brace missing.'
    $depth=0
    for($i=$open;$i -lt $Source.Count;$i++){
        if($Source[$i] -ceq '{'){$depth++}elseif($Source[$i] -ceq '}'){$depth--}
        if($depth -eq 0){return ,([string[]]$Source[$found[0]..$i])}
    }
    throw 'Producer compiler source contract: Unbalanced source block.'
}
function Method {param([string[]]$Class,[string]$Signature) return ,(Block $Class $Signature -Direct)}
function Replace-Tokens {
    param([string[]]$Source,[string]$Needle,[string]$Replacement)
    $at=Find $Source $Needle;Assert-ProducerCompiler ($at.Count -eq 1) ('Mutation anchor differs: '+$Needle)
    $old=Tokens $Needle
    $new=Tokens $Replacement
    $result=[Collections.Generic.List[string]]::new()
    for($i=0;$i -lt $at[0];$i++){$result.Add($Source[$i])}
    foreach($token in $new){$result.Add($token)}
    for($i=$at[0]+$old.Count;$i -lt $Source.Count;$i++){$result.Add($Source[$i])}
    return ,$result.ToArray()
}
$sources=[ordered]@{}
foreach($name in @('ProducerInvocation','ProducerProgram','ProducerInputs','ProducerCompiler','ProducerCompilerScope','ContextMetadata','Bootstrap','Inputs','ContextTrust','ProducerTerminal')){
    $sources[$name]=Read-ProducerCompilerSource ([IO.Path]::Combine($PSScriptRoot,'..',('Stratus.RuntimeBuild.'+$name+'.cs')))
}
$testBefore=Read-ProducerCompilerSource $PSCommandPath
$inv=Block (Tokens $sources.ProducerInvocation.text) 'internal sealed partial class ProducerInvocation'
$program=Block (Tokens $sources.ProducerProgram.text) 'internal static class Program'
$inputs=Block (Tokens $sources.ProducerInputs.text) 'internal sealed partial class ProducerInvocation'
$compiler=Block (Tokens $sources.ProducerCompiler.text) 'internal sealed partial class ProducerInvocation'
$owner=Block (Tokens $sources.ProducerCompiler.text) 'internal sealed partial class ProducerCompilerOwner'
$pTerminal=Block (Tokens $sources.ProducerTerminal.text) 'internal sealed partial class ProducerInvocation'
$pTerminalOwner=Block (Tokens $sources.ProducerTerminal.text) 'internal sealed partial class ProducerCompilerOwner'
$scope=Block (Tokens $sources.ProducerCompilerScope.text) 'internal static class ProducerCompilerScope'
$metadata=Block (Tokens $sources.ContextMetadata.text) 'internal sealed class ContextMetadata'
$b=Block (Tokens $sources.Bootstrap.text) 'public sealed class CompilerCommand : IDisposable'
$lease=Block (Tokens $sources.Inputs.text) 'public sealed class BuildInputLeaseSet : IDisposable'
$trust=Block (Tokens $sources.ContextTrust.text) 'internal static class ContextTrust'
$blocks=@{
    inv=$inv;inputs=$inputs;compiler=$compiler;owner=$owner;scope=$scope;metadata=$metadata;b=$b
    entry=Method $inv 'internal static ProducerInvocation PrepareFromInheritedEntry('
    clock=Method $inv 'internal void AssertDeadline('
    main=[string[]]@((Method $program 'private static int Main(')+(Method $pTerminal 'internal bool PollTerminalExit(')+(Method $pTerminalOwner 'internal ProducerCompilerClosure PollPreparedLocalClosure('))
    start=Method $inv 'internal void Start('
    poll=Method $inv 'internal void Poll('
    runinputs=Method $inputs 'private void RunInputs('
    startcompiler=Method $compiler 'private void StartCompiler('
    verified=Method $compiler 'private void AssertVerifiedCompilerInputs('
    compilerowner=Method $compiler 'internal void AssertCompilerOwner('
    compilerclock=Method $compiler 'internal void AssertCompilerClock('
    derive=Method $compiler 'internal ProducerCompilerPreparation DeriveCompilerPreparation('
    elapsed=Method $compiler 'internal long CompilerElapsedMilliseconds('
    worker=Method $owner 'private void AssertWorker('
    commandadmission=Method $owner 'internal void AssertCommandAdmission('
    register=Method $owner 'internal void RegisterOriginalCommand('
    release=Method $owner 'internal void AssertKnownRegistrationForRelease('
    run=[string[]]@((Method $owner 'private void Run(')+(Method $pTerminalOwner 'private void PrepareLocalTerminal('))
    qualification=Method $owner 'private static void DemandQualifiedCompilerJobPreparation('
    factory=Method $b 'internal static CompilerCommand PrepareProducerCompiler('
    commandclock=Method $b 'private void ClockAndFaultBoundary('
    result=Method $b 'public CompilerResult PollResult('
    dispose=Method $b 'public void Dispose('
    birth=$b
    resume=Method $b 'public void Resume('
    capture=Method $lease 'public BuildInputSnapshot[] CaptureSnapshots('
    trust=Method $trust 'private static ApprovalDescriptor SelectIndependentDescriptor('
}
# Historical no-success labels remain case IDs; zero exit now requires the
# selected original terminal proof. The focused read set is not a compile closure.
# Managed Dispose/registration ordering is not native CloseHandle-success proof.
# Literal predicates and one literal token mutation per row. Class-level rows
# extract individual balanced method bodies; declaration order is not call order.
# No token array is converted into a scriptblock, C# definition or executable.
$rows=@(
    @('entry_factory_copies_once_and_publishes_before_any_native_operation','entry',{
        param($s) Order $s @('Require(Interlocked.CompareExchange(ref preparationAttempted, 1, 0) == 0);','string[] copied = (string[])args.Clone();','ProducerInvocation prepared = new ProducerInvocation(','Require(Interlocked.CompareExchange(ref current, prepared, null) == null);','return prepared;')
        Has $s 'Require(start <= UInt64.MaxValue - StartupMilliseconds && deadline == checked(start + StartupMilliseconds));'
        foreach($bad in @('GetTickCount64(','GetProcessTimes(','GetCurrentProcessId(','GetStdHandle(','new Thread(','new AnonymousPipeClientStream(','ContextCapture.Prepare(','Stopwatch.StartNew(')){Has $s $bad 0}
        Has $s 'Interlocked.Exchange(ref preparationFailed, 1);'
    },'return prepared;','GetTickCount64(); return prepared;'),
    @('original_invocation_uses_readonly_origin_and_exact_static_backlink','inv',{
        param($s) Has $s 'private readonly ulong startedTick, deadlineTick;';Has $s 'private readonly string channelNonce, contextHandleText, contextLengthText, contextSha256;';Has $s 'private readonly IntPtr inputValue, outputValue;'
        $m=Method $s 'internal void AssertOriginal(';Has $m 'Require(Object.ReferenceEquals(Interlocked.CompareExchange(ref current, null, null), this));';Has $m 'Interlocked.CompareExchange(ref preparationFailed, 0, 0) == 0 && Interlocked.CompareExchange(ref failed, 0, 0) == 0';Has $m 'GetTickCount64(' 0
    },'Object.ReferenceEquals(Interlocked.CompareExchange(ref current, null, null), this)','true'),
    @('original_clock_preserves_monotonic_origin_and_sticky_failure','clock',{
        param($s) Has $s 'lock (clockGate)';Has $s 'AssertClean(); ulong now = GetTickCount64();';Has $s 'Require(now >= startedTick && now >= previousTick && now < deadlineTick); previousTick = now;';Has $s 'catch (Exception) { RecordFailure(); throw; }';Has $s 'Stopwatch.StartNew(' 0
    },'now >= startedTick && now >= previousTick && now < deadlineTick','now >= startedTick && now < deadlineTick'),
    @('thin_main_retains_before_start_and_polls_original_clock_without_success_terminal','main',{
        param($s) $main=Block $s 'private static int Main('
        Has $main 'invocation = Stratus.RuntimeBuild.ProducerInvocation.PrepareFromInheritedEntry(args); invocation.Start(); while (true) { invocation.Poll(); if (invocation.PollTerminalExit()) return 0; Thread.Sleep(10); invocation.AssertDeadline(); }'
        Has $main 'catch (Exception) { if (invocation != null) invocation.RecordFailure(); return 205; }'
        Has $main 'return' 2;Has $main 'return 0;' 1
        foreach($bad in @('return 202;','.Open(','.AssertHeld(','.CaptureSnapshots(','.Dispose(','.Join(','GetTickCount64(')){Has $main $bad 0}
        $exit=Block $s 'internal bool PollTerminalExit('
        Order $exit @('Enter();','AssertOriginal(); AssertOwnIdentity(); AssertDeadline(); AssertClean();','if (original == null || Interlocked.CompareExchange(ref terminalReaderSettled, 0, 0) != 1 || Interlocked.CompareExchange(ref terminalWriterSettled, 0, 0) != 1) return false;','if (!readerThread.Join(0) || !writerThread.Join(0) || !inputWorker.Join(0) || !contextCapture.IsWorkerSettledForTerminal) return false;','Require(Object.ReferenceEquals(original.Owner.PollPreparedLocalClosure(), original.Closure)','TerminalWire.RequireExact(terminalActualAck, original.ExpectedAck);','AssertPreparedTerminalObservation(original); AssertDeadline();','return true;')
        Has $exit 'Interlocked.CompareExchange(ref inputWorkerSettled, 0, 0) == 1'
        foreach($name in @('terminalPreparedWriteAttempted','terminalPreparedWriteReturned','terminalPreparedFlushAttempted','terminalPreparedFlushReturned','terminalPreparedWritten','terminalAckReceived','terminalEofReadAttempted','terminalEofReadReturned','terminalGuardianEof','terminalCommitWriteAttempted','terminalCommitWriteReturned','terminalCommitFlushAttempted','terminalCommitFlushReturned','terminalCommitWritten','terminalHalfCloseAttempted','terminalHalfCloseReturned','terminalHalfCloseSucceeded','terminalStdoutOpenAttempted','terminalStdoutOpenReturned','terminalStdoutWriteAttempted','terminalStdoutWriteReturned','terminalStdoutFlushAttempted','terminalStdoutFlushReturned','terminalStdoutWritten')){Has $exit ('Interlocked.CompareExchange(ref '+$name+', 0, 0) == 1')}
        Has $exit 'terminalActualAck != null && Interlocked.CompareExchange(ref terminalAckBytes, 0, 0) == original.ExpectedAck.Length'
        Has $exit 'Interlocked.CompareExchange(ref terminalEofReadCount, 0, 0) == 0'
        Has $exit 'terminalHalfCloseOriginal == outputValue'
        Has $exit 'catch (Exception) { RecordFailure(); throw; } finally { Leave(); }'
        $local=Block $s 'internal ProducerCompilerClosure PollPreparedLocalClosure('
        Order $local @('invocation.AssertCompilerTerminalClock(this);','if (Interlocked.CompareExchange(ref terminalLocalPrepared, 0, 0) != 1 || Interlocked.CompareExchange(ref workerSettled, 0, 0) != 1 || Interlocked.CompareExchange(ref threadStartReturned, 0, 0) != 1) return null;','if (!worker.Join(0)) return null;','ProducerCompilerClosure closure = Interlocked.CompareExchange(ref terminalClosure, null, null);','AssertTerminalObservation(closure); invocation.AssertProducerTerminalInputsReleased(this, closure);','return closure;')
    },'return 205;','return 202;'),
    @('invocation_start_retains_context_before_start_after_original_identity_and_pipes','start',{
        param($s) Order $s @('AssertOriginal(); Require(!startAttempted && !started && contextCapture == null);','startAttempted = true;','AssertDeadline(); CaptureOwnIdentity(); AdmitInheritedPipes();','contextCapture = ContextCapture.Prepare(contextHandleText, contextLengthText, contextSha256, new IntPtr[] { inputValue, outputValue });','AssertOwnIdentity(); AssertDeadline(); contextCapture.Start();','started = true;')
        Has $s 'catch (Exception) { RecordFailure(); throw; } finally { Leave(); }'
    },'contextCapture = ContextCapture.Prepare','ContextCapture ignored = ContextCapture.Prepare'),
    @('actual_poll_dispatches_verified_inputs_to_one_compiler_phase_without_input_io','poll',{
        param($s) Has $s 'if (!inputStartAttempted && Interlocked.CompareExchange(ref responseWritten, 0, 0) == 1 && Interlocked.CompareExchange(ref guardianAckReceived, 0, 0) == 1) { StartInputs(); }'
        Has $s 'if (Interlocked.CompareExchange(ref inputVerified, 0, 0) == 1 && !compilerStartAttempted) { AssertInputAdmission(); StartCompiler(); }'
        foreach($bad in @('.Open(','.AssertHeld(','.CaptureSnapshots(','.Dispose(','.Join(','return 202;')){Has $s $bad 0}
    },'AssertInputAdmission(); StartCompiler();','AssertInputAdmission(); payloadInputs.AssertHeld(); StartCompiler();'),
    @('input_phase_is_instance_owned_and_admits_only_the_original_invocation','inputs',{
        param($s) Has $s 'internal sealed partial class ProducerInvocation';Has $s 'private int inputVerified;';Has $s 'private Stratus.RuntimeBuild.BuildInputLeaseSet payloadInputs;'
        $m=Method $s 'private void AssertInputAdmission(';Has $m 'AssertOriginal(); AssertOwnIdentity(); AssertDeadline(); AssertClean();';Has $m 'Interlocked.CompareExchange(ref guardianAckReceived, 0, 0) == 1'
    },'private int inputVerified;','private static int inputVerified;'),
    @('input_worker_validates_actual_snapshots_and_held_owner_before_publication','runinputs',{
        param($s) Order $s @('DemandQualifiedPayloadOpen();','originalInputs.Open();','payloadSnapshots = originalInputs.CaptureSnapshots();','VerifyInputSnapshots(originalCapture, originalMetadata, originalRecords, originalInputs, originalSnapshots);','originalInputs.AssertHeld();','Require(Object.ReferenceEquals(payloadSnapshots, originalSnapshots));','Interlocked.Exchange(ref inputVerified, 1);')
        foreach($bad in @('RegisterCommand(','.Dispose(','.Join(')){Has $s $bad 0}
    },'VerifyInputSnapshots(originalCapture, originalMetadata, originalRecords, originalInputs, originalSnapshots);','/* verification omitted */'),
    @('compiler_phase_retains_exact_owner_before_start_and_never_retries','startcompiler',{
        param($s) Has $s 'Require(!compilerStartAttempted && compilerOwner == null && Interlocked.CompareExchange(ref inputVerified, 0, 0) == 1);'
        Order $s @('compilerStartAttempted = true;','ProducerCompilerOwner prepared = ProducerCompilerOwner.Prepare(this);','Require(Interlocked.CompareExchange(ref compilerOwner, prepared, null) == null);','prepared.Start();')
        Has $s 'compilerStartAttempted = false' 0;Has $s 'catch (Exception) { RecordFailure(); throw; }'
    },'Require(Interlocked.CompareExchange(ref compilerOwner, prepared, null) == null); prepared.Start();','prepared.Start(); Require(Interlocked.CompareExchange(ref compilerOwner, prepared, null) == null);'),
    @('compiler_admission_requires_original_capture_metadata_inventory_snapshots_and_ack','verified',{
        param($s) Has $s 'started && inputStartAttempted && Interlocked.CompareExchange(ref inputVerified, 0, 0) == 1 && Interlocked.CompareExchange(ref helloReceived, 0, 0) == 1 && Interlocked.CompareExchange(ref responseWritten, 0, 0) == 1 && Interlocked.CompareExchange(ref guardianAckReceived, 0, 0) == 1'
        Has $s 'Object.ReferenceEquals(contextCapture, payloadContextCapture) && Object.ReferenceEquals(contextCapture.AdmittedMetadata, payloadMetadata)'
        Has $s 'payloadInputRecords != null && payloadInputs != null && payloadSnapshots != null && payloadInputRecords.Length == payloadSnapshots.Length'
        foreach($bad in @('.AssertHeld(','.CaptureSnapshots(','.Open(')){Has $s $bad 0}
    },'Object.ReferenceEquals(contextCapture.AdmittedMetadata, payloadMetadata)','true'),
    @('owner_is_prepared_for_same_invocation_and_retains_worker_before_start','owner',{
        param($s) Has $s 'private readonly ProducerInvocation invocation;'
        $p=Method $s 'internal static ProducerCompilerOwner Prepare(';Has $p 'Require(invocation != null); invocation.AssertCompilerPreparation(); return new ProducerCompilerOwner(invocation);'
        $m=Method $s 'internal void Start(';Order $m @('Require(Interlocked.CompareExchange(ref startAttempted, 1, 0) == 0 && worker == null);','worker = new Thread(Run); worker.IsBackground = true;','Require(Interlocked.CompareExchange(ref threadStartAttempted, 1, 0) == 0);','worker.Start();','Interlocked.Exchange(ref threadStartReturned, 1);')
    },'worker = new Thread(Run);','Thread unretained = new Thread(Run);'),
    @('factory_admission_is_bound_to_exact_private_worker_and_phase','worker',{
        param($s) Has $s 'Interlocked.CompareExchange(ref failed, 0, 0) == 0 && Interlocked.CompareExchange(ref startAttempted, 0, 0) == 1 && Interlocked.CompareExchange(ref threadStartAttempted, 0, 0) == 1'
        Has $s 'worker != null && Object.ReferenceEquals(Thread.CurrentThread, worker) && Thread.CurrentThread.ManagedThreadId == worker.ManagedThreadId && Interlocked.CompareExchange(ref phase, 0, 0) == expectedPhase'
        Has $s 'invocation.AssertCompilerOwner(this);'
    },'Object.ReferenceEquals(Thread.CurrentThread, worker)','true'),
    @('preparation_derives_compiler_role_argv_and_environment_from_verified_private_state','derive',{
        param($s) Has $s 'AssertCompilerOwner(owner); owner.AssertFactoryAdmission();' 2
        Has $s 'ContextInputRecord compiler = payloadMetadata.CopyCompilerRecord(); ContextInputRecord inventoryCompiler = payloadInputRecords[5];'
        Has $s 'Require(compiler.Path == inventoryCompiler.Path && compiler.Sha256 == inventoryCompiler.Sha256 && compiler.Bytes == inventoryCompiler.Bytes && compiler.MaximumBytes == inventoryCompiler.MaximumBytes);'
        Has $s 'Dictionary<string, string> environment = ProducerCompilerScope.DeriveEnvironment(payloadMetadata);'
        Has $s 'new ProducerCompilerPreparation(payloadMetadata, payloadInputs, compiler, payloadMetadata.CopyCompilerArguments(), environment)'
    },'payloadInputRecords[5]','payloadInputRecords[4]'),
    @('metadata_keeps_defensive_compiler_role_from_actual_profile_compiler','metadata',{
        param($s) Has $s 'private readonly ContextInputRecord compiler;'
        $p=Method $s 'internal static ContextMetadata ParseAndDerive(';Has $p 'Node compiler = Member(profileValue, "compiler");';Has $p 'ContextInputRecord compilerRecord = FileProperties(compiler, FileLimit);'
        Has $p 'selectedWorktree, selectedDirectory, profile, plan, manifest, outputPath, derived, arguments, compilerRecord);'
        $c=Method $s 'private ContextMetadata(';Has $c 'compiler = new ContextInputRecord(compilerRecord.Path, compilerRecord.Sha256, compilerRecord.Bytes, compilerRecord.MaximumBytes);'
        $g=Method $s 'internal ContextInputRecord CopyCompilerRecord(';Has $g 'return new ContextInputRecord(compiler.Path, compiler.Sha256, compiler.Bytes, compiler.MaximumBytes);'
    },'Node compiler = Member(profileValue, "compiler");','Node compiler = Member(profileValue, "host");'),
    @('compiler_scope_selects_no_candidate_authority_and_remains_deny_all','scope',{
        param($s) $m=Method $s 'private static Descriptor SelectIndependentDescriptor(';Has $m 'private static Descriptor SelectIndependentDescriptor() { return null; }'
        $d=Method $s 'internal static Dictionary<string, string> DeriveEnvironment(';Has $d 'Require(metadata != null); Descriptor descriptor = SelectIndependentDescriptor(); Require(descriptor != null); return descriptor.DeriveExactEnvironment(metadata);'
        Has $s 'public' 0;Has $s 'new Descriptor(' 0
    },'return null;','return new Descriptor();'),
    @('compiler_scope_binds_full_context_identity_cwd_and_exact_compiler_record','scope',{
        param($s) $d=Block $s 'private sealed class Descriptor';$m=Method $d 'internal Dictionary<string, string> DeriveExactEnvironment('
        foreach($pair in @('contextHash, metadata.ContextSha256','profileHash, metadata.ProfileSha256','rootId, metadata.RootId','root, metadata.Root','revision, metadata.Revision','buildId, metadata.BuildId','worktree, metadata.BuildWorktree','directory, metadata.BuildDirectory','compilerPath, compiler.Path','compilerHash, compiler.Sha256')){Has $m ('String.Equals('+$pair+', StringComparison.Ordinal)')}
        Has $m 'compilerBytes == compiler.Bytes && compilerMaximum == compiler.MaximumBytes'
        Has $d 'private readonly long compilerBytes, compilerMaximum;'
    },'contextHash, metadata.ContextSha256','contextHash, metadata.ProfileSha256'),
    @('environment_is_exact_four_entry_explicit_scope_not_ambient_or_inferred','scope',{
        param($s) Has $s 'return new Dictionary<string, string>(StringComparer.Ordinal) { { "SystemRoot", systemRoot }, { "TEMP", directory }, { "TMP", directory }, { "WINDIR", windir } };'
        foreach($bad in @('Environment.GetEnvironmentVariable(','Environment.GetEnvironmentVariables(','Environment.ExpandEnvironmentVariables(','Environment.SystemDirectory','GetFolderPath(','Path.GetDirectoryName(','File.ReadAllBytes(','File.ReadAllText(','Directory.GetFiles(','Directory.GetDirectories(','Assembly.Load(','Assembly.LoadFrom(','GetType(','Func<','Action<')){Has $s $bad 0}
    },'{ "SystemRoot", systemRoot }','{ "SystemRoot", Environment.GetEnvironmentVariable("SystemRoot") }'),
    @('dedicated_factory_accepts_only_typed_owner_without_clock_or_input_overrides','factory',{
        param($s) Has $s 'internal static CompilerCommand PrepareProducerCompiler(ProducerCompilerOwner owner)'
        Order $s @('owner.AssertFactoryAdmission();','ProducerCompilerPreparation preparation = owner.BorrowPreparation();','string executable = preparation.Compiler.Path, expectedHash = preparation.Compiler.Sha256;','string directory = preparation.Metadata.BuildDirectory;','string block = BuildEnvironment(preparation.Environment, directory);')
        Has $s 'block, null, preparation.Inputs, null, null, null, null, owner);'
        foreach($bad in @('Stopwatch.StartNew(','GetTickCount64(','BuildInputLeaseSet.Prepare(','CaptureSnapshots(','new Dictionary<')){Has $s $bad 0}
    },'block, null, preparation.Inputs, null, null, null, null, owner);','block, Stopwatch.StartNew(), preparation.Inputs, null, null, null, null, owner);'),
    @('factory_and_owner_retain_exact_command_before_fallible_registration','owner',{
        param($s) $m=Method $s 'internal void RetainCommandBeforeRegistration(';Has $m 'AssertFactoryAdmission(); Require(original != null && preparation != null && registrationAttempted == 0); Require(Interlocked.CompareExchange(ref command, original, null) == null);'
        Has $m 'RegisterCommand(' 0
        $r=Method $s 'internal void RegisterOriginalCommand(';Has $r 'Require(Object.ReferenceEquals(command, original) && original != null && preparation != null);'
    },'Interlocked.CompareExchange(ref command, original, null)','Interlocked.CompareExchange(ref command, null, null)'),
    @('typed_factory_retention_registration_return_order_is_actual_call_chain','factory',{
        param($s) Has $s 'owner.RetainCommandBeforeRegistration(prepared); owner.RegisterOriginalCommand(prepared); return prepared;';Has $s 'RegisterCommand(' 0
    },'owner.RetainCommandBeforeRegistration(prepared); owner.RegisterOriginalCommand(prepared);','owner.RegisterOriginalCommand(prepared); owner.RetainCommandBeforeRegistration(prepared);'),
    @('registration_tracks_attempt_return_success_and_keeps_throwing_unknown_retained','register',{
        param($s) Has $s 'Require(Interlocked.CompareExchange(ref registrationAttempted, 1, 0) == 0);'
        Has $s 'preparation.Inputs.RegisterCommand(original); Interlocked.Exchange(ref registrationSucceeded, 1); Interlocked.Exchange(ref registrationReturned, 1);'
        Has $s 'catch (Exception) { Interlocked.Exchange(ref registrationReturned, 1); RecordFailure(); throw; }'
        foreach($bad in @('ReleaseCommand(','command = null','registrationAttempted = 0','.Dispose(')){Has $s $bad 0}
    },'Interlocked.Exchange(ref registrationSucceeded, 1);','Interlocked.Exchange(ref registrationSucceeded, 0);'),
    @('unknown_or_failed_registration_cannot_authorize_original_lease_release','release',{
        param($s) Has $s 'Require(original != null && Object.ReferenceEquals(command, original) && Interlocked.CompareExchange(ref registrationAttempted, 0, 0) == 1 && Interlocked.CompareExchange(ref registrationReturned, 0, 0) == 1 && Interlocked.CompareExchange(ref registrationSucceeded, 0, 0) == 1);'
        Has $s 'AssertWorker(' 0;Has $s 'AssertCommandAdmission(' 0
    },'Interlocked.CompareExchange(ref registrationSucceeded, 0, 0) == 1','true'),
    @('physical_command_disposal_checks_known_registration_before_releasing_inputs','dispose',{
        param($s) Order $s @('RejectTransferredCompilerCleanup();','if (!closureProven || !CollectorsClosed())','CloseOwned(ref thread, threadClose); CloseOwned(ref process, processClose); CloseOwned(ref job, jobClose);','if (producerCompilerOwner != null) producerCompilerOwner.AssertKnownRegistrationForRelease(this);','ownedInputs.ReleaseCommand(this); ownedInputsReleased = true;','disposed = true;')
    },'if (producerCompilerOwner != null) producerCompilerOwner.AssertKnownRegistrationForRelease(this);','/* unknown registration guard omitted */'),
    @('command_boundary_uses_original_invocation_and_rejects_replacement_clock_context','commandclock',{
        param($s) Has $s 'if (producerCompilerOwner != null) { if (successClock != null || sharedStartupDeadline != null || originalDeadlineOwner != null || producerControlChannel != null || producerContextSection != null) throw new InvalidOperationException("Compiler-in-P cannot adopt another clock or outer-P launch context."); producerCompilerOwner.AssertCommandAdmission(this); return; }'
    },'producerCompilerOwner.AssertCommandAdmission(this); return;','return;'),
    @('cached_result_and_reported_elapsed_cannot_bypass_original_p_clock','result',{
        param($s) Has $s 'if (producerCompilerOwner != null) ClockAndFaultBoundary();' 2
        Has $s 'if (completedResult != null) { if (producerCompilerOwner != null) ClockAndFaultBoundary(); return completedResult; }'
        Has $s 'producerCompilerOwner == null ? successClock.ElapsedMilliseconds : producerCompilerOwner.ElapsedMilliseconds(this)'
    },'if (completedResult != null) { if (producerCompilerOwner != null) ClockAndFaultBoundary(); return completedResult; }','if (completedResult != null) { return completedResult; }'),
    @('compiler_elapsed_is_original_tick_difference_not_new_clock','elapsed',{
        param($s) Has $s 'AssertCompilerClock(owner); lock (clockGate) { AssertOriginal(); Require(previousTick >= startedTick && previousTick < deadlineTick); return checked((long)(previousTick - startedTick)); }'
        Has $s 'GetTickCount64(' 0;Has $s 'Stopwatch.StartNew(' 0
    },'previousTick - startedTick','previousTick - previousTick'),
    @('worker_factory_phase_transitions_and_qualification_precede_dedicated_job','run',{
        param($s) $run=Block $s 'private void Run(';Order $run @('Require(Interlocked.CompareExchange(ref phase, 1, 0) == 0);','CompilerCommand prepared = CompilerCommand.PrepareProducerCompiler(this);','Require(Object.ReferenceEquals(command, prepared));','Require(Interlocked.CompareExchange(ref phase, 2, 1) == 1);','AssertCommandAdmission(prepared); DemandQualifiedCompilerJobPreparation();','prepared.PrepareProducerCompilerJob(this);','AssertCommandAdmission(prepared); Interlocked.Exchange(ref jobPrepared, 1);','prepared.PublishOriginalCompilerJobOffer(this);','WaitForOriginalCompilerJobAck(prepared);','AssertCommandAdmission(prepared); PrepareTerminalArtifactsBeforeBirth(prepared); prepared.StartProducerCompiler(this);','prepared.PublishOriginalCompilerRootOffer(this);','WaitForOriginalCompilerRootAck(prepared);','prepared.ResumeProducerCompiler(this);','while (prepared.PollResult() == null)','DemandQualifiedCompilerTerminal();','PrepareLocalTerminal(prepared);')
        Has $run 'catch (Exception) { RecordFailure(); } finally { if (Interlocked.CompareExchange(ref failed, 0, 0) != 0) Interlocked.Exchange(ref phase, 0); Interlocked.Exchange(ref workerSettled, 1); }'
        foreach($bad in @('.Dispose(','.Join(','.PollContainment(','.StartSuspended(','.Resume(','.CaptureSnapshots(','.PrepareBirthJob(')){Has $run $bad 0}
        $local=Block $s 'private void PrepareLocalTerminal('
        Order $local @('ProducerCompilerClosure closure = original.SealProducerLocalClosure(this);','Require(closure != null && Interlocked.CompareExchange(ref terminalClosure, closure, null) == null);','Require(Interlocked.CompareExchange(ref phase, 3, 2) == 2);','original.ReleaseProducerInputRegistration(this);','invocation.RecaptureProducerTerminalInputs(this, closure);','terminalArtifacts.CaptureAfterCompilerClosure(closure);','invocation.ReleaseProducerTerminalInputs(this, closure);','Require(Interlocked.CompareExchange(ref terminalLocalPrepared, 1, 0) == 0);')
        Has $local 'Interlocked.Exchange(ref phase, 0)' 0
    },'DemandQualifiedCompilerJobPreparation();','/* qualification omitted */'),
    @('compiler_job_qualification_is_separate_unconditional_denial','qualification',{
        param($s) Has $s 'private static void DemandQualifiedCompilerJobPreparation() { throw new InvalidOperationException("Compiler Job preparation needs separately qualified P/W, environment, writer and nested-Job scope."); }'
    },'throw new InvalidOperationException("Compiler Job preparation needs separately qualified P/W, environment, writer and nested-Job scope.");','return;'),
    @('generic_job_cannot_bypass_typed_owner_and_both_paths_use_same_retained_core','b',{
        param($s) $g=Method $s 'public void PrepareBirthJob(';Has $g 'if (producerCompilerOwner != null) throw new InvalidOperationException("Compiler-in-P requires its exact retained worker preparation path."); PrepareBirthJobOriginal();'
        $d=Method $s 'internal void PrepareProducerCompilerJob(';Has $d 'if (owner == null || !Object.ReferenceEquals(producerCompilerOwner, owner))';Has $d 'owner.AssertCommandAdmission(this); PrepareBirthJobOriginal();'
        $c=Method $s 'private void PrepareBirthJobOriginal(';Has $c 'job = CreateJobObject(IntPtr.Zero, null);';Has $c 'jobPreparationAttempted = true; SuccessBoundary();'
    },'if (producerCompilerOwner != null) throw new InvalidOperationException("Compiler-in-P requires its exact retained worker preparation path.");','/* generic job mode guard omitted */'),
    @('generic_birth_rejects_compiler_in_p_before_any_birth_attempt','birth',{
        param($s) $generic=Method $s 'public void StartSuspended('
        Order $generic @('Enter();','if (producerCompilerOwner != null) throw new InvalidOperationException("Compiler-in-P birth is unavailable before actual W Job handoff and qualification.");','StartSuspendedOriginal(null);','catch (Exception failure) { Record(failure); throw; }','finally { Leave(); }')
        Order (Method $s 'private void StartSuspendedOriginal(') @('if (startAttempted)','startAttempted = true;','creationAttempted = true;')
        Order (Method $s 'internal void StartProducerCompiler(') @('Enter();','if (owner == null || !Object.ReferenceEquals(producerCompilerOwner, owner))','AssertOriginalJobPrebirthAdmission(owner);','owner.DemandQualifiedCompilerBirth(this);','StartSuspendedOriginal(owner);','catch (Exception failure) { Record(failure); throw; }','finally { Leave(); }')
    },'if (producerCompilerOwner != null) throw new InvalidOperationException("Compiler-in-P birth is unavailable before actual W Job handoff and qualification.");','/* birth mode guard omitted */'),
    @('generic_resume_rejects_compiler_in_p_before_native_continuation','resume',{
        param($s) Order $s @('if (producerCompilerOwner != null) throw new InvalidOperationException("Compiler-in-P continuation is unavailable before actual W root/Job ACK and qualification.");','if (originalDeadlineOwner != null)','ResumeOriginal(null);')
    },'if (producerCompilerOwner != null) throw new InvalidOperationException("Compiler-in-P continuation is unavailable before actual W root/Job ACK and qualification.");','/* continuation mode guard omitted */'),
    @('recovery_returns_only_original_reference_after_failure_without_admission_or_cleanup','owner',{
        param($s) $m=Method $s 'internal CompilerCommand GetOriginalCommandForRecovery(';Has $m 'internal CompilerCommand GetOriginalCommandForRecovery() { return Interlocked.CompareExchange(ref command, null, null); }'
        foreach($bad in @('.Dispose(','.Join(','.PollContainment(','.CaptureSnapshots(')){Has $s $bad 0}
    },'return Interlocked.CompareExchange(ref command, null, null);','AssertFactoryAdmission(); return Interlocked.CompareExchange(ref command, null, null);'),
    @('registered_input_owner_forbids_fresh_snapshots_until_exact_release','capture',{
        param($s) Order $s @('RequireState(true);','if (borrower != null || guardianBorrower != null) throw new InvalidOperationException("Fresh input capture cannot race an active command or guardian borrower.");','AssertHeldCore(); BuildInputSnapshot[] result = new BuildInputSnapshot[inputs.Length];','CaptureFile(item);')
    },'borrower != null || guardianBorrower != null','guardianBorrower != null'),
    @('independent_context_trust_stays_closed_despite_compiler_integration','trust',{
        param($s) Has $s 'private static ApprovalDescriptor SelectIndependentDescriptor() { return null; }'
    },'return null;','return new ApprovalDescriptor();'),
    @('command_admission_requires_phase_two_exact_command_known_registration_then_original_clock','commandadmission',{
        param($s) Has $s 'AssertWorker(2); Require(original != null && Object.ReferenceEquals(command, original) && preparation != null && Interlocked.CompareExchange(ref registrationAttempted, 0, 0) == 1 && Interlocked.CompareExchange(ref registrationReturned, 0, 0) == 1 && Interlocked.CompareExchange(ref registrationSucceeded, 0, 0) == 1); invocation.AssertCompilerClock(this);'
    },'AssertWorker(2);','/* original phase-two worker edge omitted */'),
    @('invocation_compiler_owner_guard_requires_exact_retained_owner_and_verified_state','compilerowner',{
        param($s) Has $s 'AssertOriginal(); Require(owner != null && compilerStartAttempted && Object.ReferenceEquals(Interlocked.CompareExchange(ref compilerOwner, null, null), owner)); AssertVerifiedCompilerInputs();'
    },'Object.ReferenceEquals(Interlocked.CompareExchange(ref compilerOwner, null, null), owner)','owner != null'),
    @('compiler_clock_uses_original_input_admission_between_same_owner_checks','compilerclock',{
        param($s) Has $s 'AssertCompilerOwner(owner); AssertInputAdmission(); AssertCompilerOwner(owner);'
    },'AssertInputAdmission();','/* original identity and deadline admission edge omitted */')
)
$expectedPairs=37;$expected=74
Assert-ProducerCompiler ($rows.Count -eq $expectedPairs -and $expected -eq 2*$expectedPairs) 'Fixed case inventory differs.'
$names=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
$executed=0;$results=[Collections.Generic.List[object]]::new()
foreach($row in $rows){
    Assert-ProducerCompiler ($row.Count -eq 5 -and $names.Add([string]$row[0])) 'Fixed row shape or unique identity differs.'
    $selected=$blocks[$row[1]];$check=$row[2]
    & $check $selected;$executed++;$results.Add([pscustomobject]@{name=$row[0];variant='original_source';passed=$true})
    $mutated=Replace-Tokens $selected $row[3] $row[4];$rejected=$false
    try{& $check $mutated}catch{
        Assert-ProducerCompiler ($_.Exception.Message.StartsWith('Producer compiler source contract:',[StringComparison]::Ordinal)) ('Unexpected negative-control exception: '+$row[0])
        $rejected=$true
    }
    Assert-ProducerCompiler $rejected ('Weakening mutation survived: '+$row[0])
    $executed++;$results.Add([pscustomobject]@{name=$row[0];variant='mutated_source_rejected';passed=$true})
}
Assert-ProducerCompiler ($executed -eq $expected -and $results.Count -eq $expected) 'Fixed cases did not complete.'
$before=[Collections.Generic.List[object]]::new();$after=[Collections.Generic.List[object]]::new()
foreach($name in $sources.Keys){
    $old=$sources[$name];$latest=Read-ProducerCompilerSource $old.path
    Assert-ProducerCompiler ($latest.sha256 -ceq $old.sha256) ('Subject changed during inspection: '+$name)
    $before.Add([pscustomobject]@{name=$name;path=$old.path;sha256=$old.sha256})
    $after.Add([pscustomobject]@{name=$name;path=$latest.path;sha256=$latest.sha256})
}
$testAfter=Read-ProducerCompilerSource $PSCommandPath
Assert-ProducerCompiler ($testAfter.sha256 -ceq $testBefore.sha256) 'Test changed during inspection.'
[pscustomobject]@{
    format='stratus-producer-compiler-source-contract-tests-v1'
    expected_pairs=$expectedPairs;expected=$expected;executed=$executed;passed=$executed;failed=0;skipped=0
    powershell_version=$PSVersionTable.PSVersion.ToString();powershell_edition=$PSVersionTable.PSEdition
    source_hashes_before=$before.ToArray();source_hashes_after=$after.ToArray()
    test_sha256_before=$testBefore.sha256;test_sha256_after=$testAfter.sha256;source_and_test_hashes_unchanged=$true
    source_tokens_and_directed_inert_mutations_only=$true
    target_compiled=$false;target_loaded=$false;target_lifecycle_executed=$false;native_qualified=$false;nativeQualification=$false
    target_powershell_defined=$false;target_powershell_executed=$false;bootstrap_loader_executed=$false
    filesystem_fixture_created=$false;csharp_semantics_proven=$false
    actual_job_created=$false;actual_child_born=$false;compiler_job_root_acknowledged=$false
    independent_compiler_environment_configured=$false;compiler_job_preparation_authorized=$false
    preload_open_authorized=$false;payload_open_authorized=$false;producer_resume_authorized=$false
    physical_ownership_proven=$false;timing_qualified=$false;full_build_accepted=$false
    cases=$results.ToArray()
}|ConvertTo-Json -Depth 6 -Compress
