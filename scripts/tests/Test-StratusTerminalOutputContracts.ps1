param()
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
# Source data only. No dot-source, AST evaluation, target invocation, assembly
# loading, compiler, fixture or native execution. Literal local predicates below
# inspect token sequences and directed inert mutations, not runtime semantics.
function Assert-TerminalOutput {
    param([bool]$Okay,[string]$Message)
    if(-not $Okay){throw ('Terminal output source contract: '+$Message)}
}
function Read-TerminalOutputSource {
    param([string]$Path)
    $full=[IO.Path]::GetFullPath($Path)
    # Bound allocation and every delivered read; a FileInfo precheck alone
    # cannot stop ReadAllBytes from allocating after concurrent file growth.
    $stream=[IO.FileStream]::new($full,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{
        $length=$stream.Length
        Assert-TerminalOutput ($length -gt 0 -and $length -le 1048576) 'Inspection file exceeds one MiB.'
        $raw=[byte[]]::new([int]$length);$offset=0
        while($offset -lt $raw.Length){
            $count=$stream.Read($raw,$offset,$raw.Length-$offset)
            Assert-TerminalOutput ($count -gt 0) 'Inspection file ended before its bounded length.'
            $offset+=$count
        }
        Assert-TerminalOutput ($stream.ReadByte() -eq -1 -and $stream.Length -eq $length) 'File length changed while reading.'
    }finally{$stream.Dispose()}
    $text=[Text.UTF8Encoding]::new($false,$true).GetString($raw)
    $hash=[Security.Cryptography.SHA256]::Create()
    try{$digest=[BitConverter]::ToString($hash.ComputeHash($raw)).Replace('-','').ToLowerInvariant()}finally{$hash.Dispose()}
    return [pscustomobject]@{path=$full;text=$text;sha256=$digest}
}
$script:sourceSyntax='CSharp'
function Tokens {
    param([string]$Text,[ValidateSet('CSharp','PowerShell')][string]$Syntax=$script:sourceSyntax)
    Assert-TerminalOutput ($null -ne $Text -and $Text.Length -le 1048576) 'Token input exceeds its fixed character bound.'
    # This deliberately limited data lexer never evaluates either language.
    # A PowerShell single-quoted trailing backslash does NOT escape its quote.
    $pattern=if($Syntax -ceq 'PowerShell'){@'
<\#.*?\#> | \#[^\r\n]* | "(?:`.|[^"`])*" | '(?:''|[^'])*'
| [A-Za-z_][A-Za-z_0-9]* | [0-9]+ | == | != | >= | <= | \+\+ | -- | && | \|\| | => | [^\s]
'@
    }else{@'
//[^\r\n]* | /\*.*?\*/ | \#[^\r\n]* | @"(?:""|[^"])*" | "(?:\\.|[^"\\])*" | '(?:\\.|[^'\\])*'
| [A-Za-z_][A-Za-z_0-9]* | [0-9]+ | == | != | >= | <= | \+\+ | -- | && | \|\| | => | [^\s]
'@
    }
    $result=[Collections.Generic.List[string]]::new()
    $options=[Text.RegularExpressions.RegexOptions]::Singleline -bor [Text.RegularExpressions.RegexOptions]::IgnorePatternWhitespace
    foreach($m in [regex]::Matches($Text,$pattern,$options,[TimeSpan]::FromSeconds(5))){
        $comment=if($Syntax -ceq 'PowerShell'){$m.Value.StartsWith('#') -or $m.Value.StartsWith('<#')}else{$m.Value.StartsWith('//') -or $m.Value.StartsWith('/*') -or $m.Value.StartsWith('#')}
        if(-not $comment){Assert-TerminalOutput ($result.Count -lt 262144) 'Token inspection bound exceeded.';$result.Add($m.Value)}
    }
    Assert-TerminalOutput ($result.Count -le 262144) 'Token inspection bound exceeded.'
    return ,$result.ToArray()
}
function Find {
    param([string[]]$Source,[string]$Sequence)
    $wanted=Tokens $Sequence;Assert-TerminalOutput ($wanted.Count -gt 0) 'Empty predicate.'
    $found=[Collections.Generic.List[int]]::new()
    for($i=0;$i -le $Source.Count-$wanted.Count;$i++){
        $same=$true
        for($j=0;$j -lt $wanted.Count;$j++){if($Source[$i+$j] -cne $wanted[$j]){$same=$false;break}}
        if($same){$found.Add($i)}
    }
    return ,$found.ToArray()
}
function Has {param([string[]]$Source,[string]$Sequence,[int]$Count=1) Assert-TerminalOutput ((Find $Source $Sequence).Count -eq $Count) ('Sequence differs: '+$Sequence)}
function Order {
    param([string[]]$Source,[string[]]$Sequences)
    $last=-1
    foreach($sequence in $Sequences){$found=Find $Source $sequence;Assert-TerminalOutput ($found.Count -eq 1 -and $found[0] -gt $last) ('Order differs: '+$sequence);$last=$found[0]}
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
    Assert-TerminalOutput ($found.Count -eq 1) ('Missing or ambiguous block: '+$Signature)
    $open=$found[0];while($open -lt $Source.Count -and $Source[$open] -cne '{'){$open++}
    Assert-TerminalOutput ($open -lt $Source.Count) 'Opening brace missing.'
    $depth=0
    for($i=$open;$i -lt $Source.Count;$i++){
        if($Source[$i] -ceq '{'){$depth++}elseif($Source[$i] -ceq '}'){$depth--}
        if($depth -eq 0){return ,([string[]]$Source[$found[0]..$i])}
    }
    throw 'Terminal output source contract: Unbalanced source block.'
}
function Method {param([string[]]$Class,[string]$Signature) return ,(Block $Class $Signature -Direct)}
function Replace-Tokens {
    param([string[]]$Source,[string]$Needle,[string]$Replacement)
    $at=Find $Source $Needle;Assert-TerminalOutput ($at.Count -eq 1) ('Mutation anchor differs: '+$Needle)
    $old=Tokens $Needle;$new=Tokens $Replacement;$result=[Collections.Generic.List[string]]::new()
    for($i=0;$i -lt $at[0];$i++){$result.Add($Source[$i])}
    foreach($token in $new){$result.Add($token)}
    for($i=$at[0]+$old.Count;$i -lt $Source.Count;$i++){$result.Add($Source[$i])}
    return ,$result.ToArray()
}
# Golden lexical preflight is harness validation, not additional STRATUS cases.
# These fixed strings remain inert data, including the PowerShell-looking text.
$psSample=@'
.Split('\'); $value='it''s'; $quoted="a`"b"; $after=1
'@
$psExpected=@'
.|Split|(|'\'|)|;|$|value|=|'it''s'|;|$|quoted|=|"a`"b"|;|$|after|=|1
'@
Assert-TerminalOutput (((Tokens $psSample -Syntax PowerShell) -join '|') -ceq $psExpected) 'PowerShell literal lexer regression.'
$csSample=@'
char slash = '\\'; char quote = '\''; string value = "a\"b";
'@
$csExpected=@'
char|slash|=|'\\'|;|char|quote|=|'\''|;|string|value|=|"a\"b"|;
'@
Assert-TerminalOutput (((Tokens $csSample -Syntax CSharp) -join '|') -ceq $csExpected) 'CSharp literal lexer regression.'
$sources=[ordered]@{}
foreach($name in @('TerminalCoordinator','OutputOwner','GuardianLaunch','GuardianTerminal','Producer','StartupPreload')){
    $sources[$name]=Read-TerminalOutputSource ([IO.Path]::Combine($PSScriptRoot,'..',('Stratus.RuntimeBuild.'+$name+'.cs')))
}
$sources['Loader']=Read-TerminalOutputSource ([IO.Path]::Combine($PSScriptRoot,'..','Stratus.RuntimeBuild.Bootstrap.ps1'))
$testBefore=Read-TerminalOutputSource $PSCommandPath
$c=Block (Tokens $sources.TerminalCoordinator.text) 'public sealed class BuildTerminalCoordinator'
$o=Block (Tokens $sources.OutputOwner.text) 'internal sealed class BuildOutputOwner'
$v=Block (Tokens $sources.OutputOwner.text) 'public sealed class BuildOutputCapture'
$w=Block (Tokens $sources.GuardianLaunch.text) 'public sealed class GuardianLaunch : IDisposable'
$p=Block (Tokens $sources.Producer.text) 'public sealed class DisposableProducerLaunch : IDisposable'
$wt=Block (Tokens $sources.GuardianTerminal.text) 'internal static partial class Program'
$blocks=@{
    coordinator=$c;owner=$o;capture=$v;guardian=$w;producer=$p
    prepare=Method $c 'public static BuildTerminalCoordinator Prepare('
    ctor=Method $c 'private BuildTerminalCoordinator('
    start=Method $c 'public void Start('
    poll=Method $c 'public BuildOutputCapture Poll('
    enter=Method $c 'private void Enter('
    admission=Method $c 'private void Admission('
    operation=Method $c 'private void AssertOperation('
    prebirth=Method $c 'internal void AssertOutputPreBirthBoundary('
    scopeCallback=Method $c 'internal void AssertOutputScopePreparation('
    captureCallback=Method $c 'internal void AssertOutputAdmission('
    boundary=Method $c 'internal void AssertOutputBoundary('
    recover=Method $c 'private void RecoverOriginalWrappers('
    contain=Method $c 'public bool PollContainment('
    release=Method $c 'public void ReleaseAfterContainment('
    ownerScope=Method $o 'internal void PrepareWriterScopeBeforeProducerBirth('
    ownerHeld=Method $o 'internal void AssertWriterScopeHeldBeforeProducerBirth('
    ownerBoundary=Method $o 'private void Boundary('
    fixedPaths=Method $o 'private void RequireFixedPaths('
    open=Method $o 'private void OpenOriginal('
    information=Method $o 'private FileInfo Information('
    ancestry=Method $o 'private void RetainAncestry('
    read=Method $o 'private TerminalArtifactCommitment CaptureFile('
    actualCapture=Method $o 'internal BuildOutputCapture CaptureOriginalTerminalOutputs('
    held=Method $o 'private void AssertHeldCaptureCore('
    copy=Method $o 'private byte[] CopyOriginalBytes('
    normal=Method $w 'internal BuildTerminalClosure PollNormalTerminal('
    wBoundary=Method $w 'internal void AssertCoordinatorOutputBoundary('
    wBind=Method $w 'internal void BindTerminalCoordinator('
    wPrepared=Method $wt 'private static void ReceiveNormalTerminalPrepared('
    wDrain=Method $wt 'private static bool MayDrainNormalTerminal('
    loader=Tokens $sources.Loader.text -Syntax PowerShell
}
$rows=@(
    @('l_prepare_is_bounded_and_private_context_is_cloned','coordinator',{
        param($s) Has (Method $s 'public static BuildTerminalCoordinator Prepare(') 'rawContext == null || rawContext.Length < 1 || rawContext.Length > ContextCapture.MaximumBytes';Order (Method $s 'private BuildTerminalCoordinator(') @('context = (byte[])rawContext.Clone();','metadata = ContextMetadata.ParseAndDerive(context);','phase = Phase.Prepared;');Has $s 'public BuildTerminalCoordinator(' 0
    },'context = (byte[])rawContext.Clone();','context = rawContext;'),
    @('l_start_retains_output_before_admission_and_scope_before_preload','start',{
        param($s) Order $s @('output = BuildOutputOwner.Prepare(this);','ContextTrust.DemandAdmission(metadata); contextAdmitted = true;','DemandQualifiedCoordinatorExecution();','writerPreparationAttempted = true;','output.PrepareWriterScopeBeforeProducerBirth();','writerPreparationReturned = true; writerPrepared = true;','preload = StartupPreloadOwner.Prepare(context);','preload.Open(); Admission();');Has $s 'catch (Exception) { RecordFailure(); throw; }';Has $s '.Dispose(' 0
    },'output.PrepareWriterScopeBeforeProducerBirth();','/* scope bypass */'),
    @('l_start_rechecks_prebirth_scope_and_retains_exact_factories','start',{
        param($s) Has $s 'output.AssertWriterScopeHeldBeforeProducerBirth();' 3;Has $s 'Require(producer != null && Object.ReferenceEquals(producer, preload.RetainedProducer)); output.AssertWriterScopeHeldBeforeProducerBirth(); producerStartAttempted = true; producer.StartSuspended();';Order $s @('guardian = preload.PrepareGuardian(); guardianPreparationReturned = true;','Require(guardian != null && Object.ReferenceEquals(guardian, preload.RetainedGuardian));','guardian.BindTerminalCoordinator(this);','guardianStartAttempted = true; guardian.StartRunning();')
    },'Object.ReferenceEquals(producer, preload.RetainedProducer)','producer != null'),
    @('l_execution_gate_stays_unconditional','coordinator',{
        param($s) Has (Method $s 'private static void DemandQualifiedCoordinatorExecution(') 'throw new InvalidOperationException("The complete L/P/W, writer isolation and external L-lifetime scope require independent qualification.");'
    },'throw new InvalidOperationException("The complete L/P/W, writer isolation and external L-lifetime scope require independent qualification.");','return;'),
    @('l_same_thread_operation_and_sticky_failure_are_required','coordinator',{
        param($s) Has (Method $s 'private void Enter(') 'if (Interlocked.CompareExchange(ref operation, 1, 0) != 0) { RecordFailure(); throw';Has (Method $s 'private void AssertOperation(') 'operationThread == Thread.CurrentThread.ManagedThreadId';Has (Method $s 'private void Admission(') 'Interlocked.CompareExchange(ref failed, 0, 0) == 0 && phase != Phase.Containing && phase != Phase.Contained && phase != Phase.Releasing && phase != Phase.Released'
    },'operationThread == Thread.CurrentThread.ManagedThreadId','true'),
    @('l_scope_preparation_cannot_follow_birth','scopeCallback',{
        param($s) Has $s 'contextAdmitted && writerPreparationAttempted && !writerPreparationReturned && !writerPrepared && !preloadPreparationAttempted && preload == null && !producerStartAttempted && !guardianStartAttempted';Has $s 'ContextTrust.DemandAdmission(metadata);'
    },'!preloadPreparationAttempted && preload == null','true'),
    @('l_prebirth_callback_requires_returned_scope_and_no_start','prebirth',{
        param($s) Has $s 'writerPreparationAttempted && writerPreparationReturned && writerPrepared && !producerStartAttempted && !guardianStartAttempted && terminal == null && !captureAttempted';Has $s 'Object.ReferenceEquals(output, original)'
    },'!producerStartAttempted && !guardianStartAttempted','true'),
    @('l_ready_is_only_observed_before_single_continuation','poll',{
        param($s) $ready=Block $s 'if (phase == Phase.AwaitingReady)';Order $ready @('Require(!continuationAttempted);','if (guardian.PollReady() == null) return null;','continuationAttempted = true;','guardian.ContinueProducer(); continuationReturned = true;','Admission(); phase = Phase.Running;');Has $s 'guardian.PollReady()' 1;Has $s 'guardian.ContinueProducer()' 1
    },'if (phase == Phase.AwaitingReady)','if (phase == Phase.Running)'),
    @('l_capture_follows_exact_terminal_and_fresh_boundary','poll',{
        param($s) Order $s @('BuildTerminalClosure observed = guardian.PollNormalTerminal();','Require(observed.BelongsTo(guardian));','terminal = observed; phase = Phase.Capturing;','AssertOutputBoundary(output);','captureAttempted = true;','capture = output.CaptureOriginalTerminalOutputs(); captureReturned = true;','Require(capture != null && capture.IsOwnedBy(this, output)); phase = Phase.Captured;');Has $s 'if (terminal != null) Require(Object.ReferenceEquals(terminal, observed));'
    },'AssertOutputBoundary(output);','/* boundary bypass */'),
    @('l_cached_capture_is_revalidated_not_recreated','poll',{
        param($s) Has (Block $s 'if (phase == Phase.Captured)') 'Require(captureReturned && capture != null); output.AssertHeldCapture(capture); Admission(); return capture;';Has $s 'CaptureOriginalTerminalOutputs(' 1;Has $s '!captureAttempted'
    },'Require(captureReturned && capture != null);','Require(capture != null);'),
    @('l_output_admission_requires_original_p_w_preload_refs','captureCallback',{
        param($s) Has $s '(phase == Phase.Capturing || phase == Phase.Captured) && contextAdmitted && writerPrepared && continuationReturned && terminal != null';Has $s 'terminal.BelongsTo(guardian) && Object.ReferenceEquals(preload.RetainedGuardian, guardian) && Object.ReferenceEquals(preload.RetainedProducer, producer)'
    },'Object.ReferenceEquals(preload.RetainedProducer, producer)','producer != null'),
    @('l_boundary_uses_original_metadata_and_context_length','boundary',{
        param($s) Has $s 'AssertOutputAdmission(original); ContextTrust.DemandAdmission(metadata); guardian.AssertCoordinatorOutputBoundary(this, terminal, metadata, context.Length); AssertOutputAdmission(original);';Has $s 'Stopwatch' 0
    },'guardian.AssertCoordinatorOutputBoundary(this, terminal, metadata, context.Length);','/* original W boundary omitted */'),
    @('l_bytes_only_read_through_captured_coordinator_operation','coordinator',{
        param($s) foreach($name in @('ReadOutput','ReadStdout')){$m=Method $s ('public byte[] '+$name+'(');Has $m 'Enter();';Has $m 'Admission(); Require(phase == Phase.Captured && captureReturned && capture != null);';Has $m 'catch (Exception) { RecordFailure(); throw; } finally { Leave(); }'};Has (Method $s 'public byte[] ReadOutput(') 'byte[] bytes = output.CopyOutput(capture); Admission(); return bytes;';Has (Method $s 'public byte[] ReadStdout(') 'byte[] bytes = output.CopyStdout(capture); Admission(); return bytes;'
    },'output.CopyOutput(capture)','new byte[0]'),
    @('l_recovery_adopts_only_original_retained_wrappers','recover',{
        param($s) Has $s 'DisposableProducerLaunch p = preload.RetainedProducer; GuardianLaunch w = preload.RetainedGuardian;';Has $s 'if (producer == null) producer = p; else Require(Object.ReferenceEquals(producer, p));';Has $s 'if (guardian == null) guardian = w; else Require(Object.ReferenceEquals(guardian, w));';Has $s 'Require(!producerStartAttempted || producer != null);';Has $s 'Require(!guardianStartAttempted || guardian != null);';Has $s 'OpenProcess' 0
    },'Require(!producerStartAttempted || producer != null);','/* missing original P allowed */'),
    @('l_containment_uses_w_else_p_without_rival_cleanup','contain',{
        param($s) Has $s 'RecordFailure(); phase = Phase.Containing; RecoverOriginalWrappers();';Has $s 'if (guardian != null) { if (!guardian.PollContainment()) return false; } else if (producer != null) { if (!producer.PollContainment()) return false; } else Require(!producerStartAttempted && !guardianStartAttempted);';Has $s 'TerminateProcess' 0;Has $s 'phase = Phase.Contained; return true;'
    },'else if (producer != null)','if (producer != null)'),
    @('l_explicit_release_requires_containment_known_ordered_returns','release',{
        param($s) Order $s @('Require(phase == Phase.Contained);','phase = Phase.Releasing;','guardian.Dispose(); guardianReleaseReturned = true;','producer.Dispose(); producerReleaseReturned = true;','preload.Dispose(); preloadReleaseReturned = true;','phase = Phase.Released;');Has $s '(guardian == null || guardianReleaseReturned) && (producer == null || producerReleaseReturned) && (preload == null || preloadReleaseReturned)';Has $s 'output.Dispose(' 0
    },'Require(phase == Phase.Contained);','Require(phase != Phase.Released);'),
    @('l_writer_selector_is_null_and_both_owner_gates_remain_closed','owner',{
        param($s) Has (Method $s 'private WriterDescriptor SelectIndependentWriterDescriptor(') 'return null;';Has (Method $s 'private static void DemandQualifiedPrebirthWriterScope(') 'throw new InvalidOperationException(';Has (Method $s 'private static void DemandQualifiedOutputCapture(') 'throw new InvalidOperationException(';Has (Method $s 'internal void PrepareWriterScopeBeforeProducerBirth(') 'DemandQualifiedPrebirthWriterScope();';Has (Method $s 'internal BuildOutputCapture CaptureOriginalTerminalOutputs(') 'DemandQualifiedOutputCapture();'
    },'return null;','return new WriterDescriptor(this, metadata);'),
    @('l_scope_retains_metadata_and_descriptor_before_prepared_flag','ownerScope',{
        param($s) Has $s 'owner.AssertOutputScopePreparation(this);' 2;Order $s @('owner.AssertOutputScopePreparation(this); Require(!scopePreparationAttempted','scopePreparationAttempted = true; metadata = owner.BorrowOutputMetadata(this); RequireFixedPaths();','scope = SelectIndependentWriterDescriptor(); scopeSelectionReturned = true;','DemandQualifiedPrebirthWriterScope();','Require(scope != null); scope.AssertContext(this, metadata);','owner.AssertOutputScopePreparation(this); scopePrepared = true;');Has $s 'OpenOriginal(' 0
    },'scope = SelectIndependentWriterDescriptor();','scope = null;'),
    @('l_held_prebirth_scope_cannot_be_adopted_after_terminal','ownerHeld',{
        param($s) Has $s 'scopePreparationAttempted && scopeSelectionReturned && scopePrepared && scope != null && metadata != null && terminal == null && !captureAttempted && !captureCompleted && Object.ReferenceEquals(metadata, owner.BorrowOutputMetadata(this))';Has $s 'owner.AssertOutputPreBirthBoundary(this);' 2
    },'terminal == null && !captureAttempted && !captureCompleted','true'),
    @('l_owner_boundary_checks_same_scope_and_original_coordinator','ownerBoundary',{
        param($s) Has $s 'Interlocked.CompareExchange(ref failed, 0, 0) == 0 && scopePrepared && scopeSelectionReturned && scope != null';Has $s 'owner.AssertOutputBoundary(this); scope.AssertContext(this, metadata);';Has $s 'StartNew(' 0
    },'owner.AssertOutputBoundary(this);','/* boundary bypass */'),
    @('l_output_paths_are_fixed_and_separate_from_input_paths','fixedPaths',{
        param($s) Has $s 'metadata.OutputPath == metadata.BuildDirectory + "\\Stratus.ParentStage.Runtime.dll"';Has $s 'string stdoutPath = metadata.BuildDirectory + "\\compiler.stdout.bin";';Has $s '!String.Equals(input.Path, metadata.OutputPath, StringComparison.OrdinalIgnoreCase) && !String.Equals(input.Path, stdoutPath, StringComparison.OrdinalIgnoreCase)'
    },'!String.Equals(input.Path, stdoutPath, StringComparison.OrdinalIgnoreCase)','true'),
    @('l_native_acquisition_retains_raw_outcome_before_nonowning_wrap','open',{
        param($s) Order $s @('item.Attempted = true;','item.Original = CreateFile(','item.OpenError = Marshal.GetLastWin32Error(); item.Returned = true;','item.Handle = new SafeFileHandle(item.Original, false);','item.Identity = Information(item); item.Known = true;');Has $s 'Require(item != null && !item.Attempted && !item.StreamAttempted);'
    },'new SafeFileHandle(item.Original, false)','new SafeFileHandle(item.Original, true)'),
    @('l_file_handles_allow_read_only_and_deny_new_write_delete_shares','open',{
        param($s) Has $s 'CreateFile(item.Path, item.Directory ? 0x80U : 0x80000000U, item.Directory ? 3U : 1U, IntPtr.Zero, 3, item.Directory ? 0x02200000U : 0x00200000U, IntPtr.Zero)';Has $s 'item.Stream = new FileStream(item.Handle, FileAccess.Read, 65536); item.StreamReturned = true;';Has $s 'GC.SuppressFinalize(item.Stream); item.FinalizerSuppressed = true;'
    },'item.Directory ? 3U : 1U','item.Directory ? 3U : 7U'),
    @('l_query_unknown_outcome_cannot_be_retried','information',{
        param($s) Order $s @('Require(!item.InformationAttempted || item.InformationReturned);','item.InformationAttempted = true; item.InformationReturned = false; item.InformationSucceeded = false;','bool okay = GetFileInformationByHandle(item.Handle, out item.LastInformation);','item.InformationError = Marshal.GetLastWin32Error(); item.InformationSucceeded = okay; item.InformationReturned = true;');Has $s 'Require(!item.TypeAttempted || item.TypeReturned);';Has $s 'Require(!item.PathAttempted || item.PathReturned);'
    },'Require(!item.InformationAttempted || item.InformationReturned);','/* unknown query retry */'),
    @('l_identity_is_disk_same_volume_nonreparse_exact_type_single_link','information',{
        param($s) Has $s 'item.TypeValue == 1 && Index(info) != 0 && info.Volume == volumeSerial && (info.Attributes & (uint)FileAttributes.ReparsePoint) == 0 && ((info.Attributes & (uint)FileAttributes.Directory) != 0) == item.Directory && (item.Directory || info.Links == 1)';Has $s 'item.PathCount > 0 && item.PathCount < item.FinalPath.Capacity && String.Equals(item.FinalPath.ToString(), "\\\\?\\" + item.Path, StringComparison.OrdinalIgnoreCase)'
    },'(item.Directory || info.Links == 1)','true'),
    @('l_ancestry_is_pinned_before_each_open_on_local_ntfs','ancestry',{
        param($s) Has $s 'Require(driveType == 3);';Has $s 'Require(volumeFileSystem.ToString() == "NTFS");';Has $s 'HeldSlot drive = new HeldSlot(root, true); directories.Add(drive); OpenOriginal(drive);';Has $s 'HeldSlot item = new HeldSlot(current, true); directories.Add(item); OpenOriginal(item);';Has $s 'Require(parent == metadata.BuildDirectory);'
    },'directories.Add(item); OpenOriginal(item);','OpenOriginal(item); directories.Add(item);'),
    @('l_actual_read_uses_bounded_bytes_and_readonly_original_stream','read',{
        param($s) Has $s 'Require(expected.Bytes >= minimum && expected.Bytes <= maximum); RequireCommittedIdentity(before, expected);';Has $s 'length >= (ulong)minimum && length <= (ulong)maximum && length == (ulong)expected.Bytes';Has $s 'item.Stream.CanRead && item.Stream.CanSeek && !item.Stream.CanWrite && item.Stream.Length == (long)length';Has $s 'item.Stream.Read(item.Captured, item.CapturedBytes, Math.Min(65536, item.Captured.Length - item.CapturedBytes))';Has $s 'File.ReadAllBytes(' 0
    },'!item.Stream.CanWrite','item.Stream.CanWrite'),
    @('l_each_read_tracks_outcome_and_rejects_truncation','read',{
        param($s) Has $s 'Require(!item.ReadAttempted || item.ReadReturned); Boundary(); item.ReadAttempted = true; item.ReadReturned = false;';Has $s 'item.ReadReturned = true; Boundary(); Require(item.ReadCount > 0 && item.ReadCount <= item.Captured.Length - item.CapturedBytes);';Has $s 'item.CapturedBytes = checked(item.CapturedBytes + item.ReadCount);'
    },'item.ReadCount > 0 && item.ReadCount <= item.Captured.Length - item.CapturedBytes','item.ReadCount >= 0'),
    @('l_actual_eof_and_identity_are_proved_before_actual_hash','read',{
        param($s) Order $s @('item.EofAttempted = true; item.EofValue = item.Stream.ReadByte(); item.EofReturned = true;','Boundary(); Require(item.EofValue == -1);','FileInfo after = Information(item); Require(Same(before, after, false) && item.Stream.Length == item.Captured.LongLength);','RequireCommittedIdentity(after, expected);','sha.ComputeHash(item.Captured)','RequireCommitment(actual, expected); item.Identity = after; item.CaptureCompleted = true; return actual;')
    },'Require(item.EofValue == -1);','Require(item.EofReturned);'),
    @('l_committed_identity_and_digest_compare_every_field','owner',{
        param($s) $m=Method $s 'private static void RequireCommitment(';foreach($field in @('Bytes','Sha256','Volume','FileIndex','LinkCount','Attributes','CreatedFileTime','WrittenFileTime')){Has $m ('actual.'+$field+' == expected.'+$field)};Has (Method $s 'private static bool Same(') 'Time(left.Created) == Time(right.Created) && Time(left.Written) == Time(right.Written)'
    },'actual.Sha256 == expected.Sha256','actual.Sha256 != null'),
    @('l_capture_slots_are_retained_before_io_and_outputs_cannot_alias','actualCapture',{
        param($s) Order $s @('terminal = owner.BorrowOutputTerminal(this);','DemandQualifiedOutputCapture();','output = new HeldSlot(metadata.OutputPath, false);','stdout = new HeldSlot(metadata.BuildDirectory + "\\compiler.stdout.bin", false);','RetainAncestry(); AssertAncestry(); OpenOriginal(output); OpenOriginal(stdout);','Require(output.Identity.Volume != stdout.Identity.Volume || Index(output.Identity) != Index(stdout.Identity));','TerminalArtifactCommitment assembly = CaptureFile(output, terminal.Record.Artifacts.Output, 1, OutputMaximum);','TerminalArtifactCommitment log = CaptureFile(stdout, terminal.Record.Artifacts.Stdout, 0, StdoutMaximum);')
    },'Require(output.Identity.Volume != stdout.Identity.Volume || Index(output.Identity) != Index(stdout.Identity));','/* physical alias allowed */'),
    @('l_capture_publication_binds_exact_current_terminal_and_bytes','actualCapture',{
        param($s) Has $s 'Require(Object.ReferenceEquals(metadata, owner.BorrowOutputMetadata(this)));';Has $s 'Require(Object.ReferenceEquals(terminal, owner.BorrowOutputTerminal(this)));';Order $s @('BuildOutputCapture prepared = new BuildOutputCapture(this, owner, terminal, actual, output.Captured, stdout.Captured);','Require(Interlocked.CompareExchange(ref capture, prepared, null) == null);','captureCompleted = true; return prepared;')
    },'Interlocked.CompareExchange(ref capture, prepared, null) == null','true'),
    @('l_held_capture_requires_exact_original_owner_eof_and_recheck','held',{
        param($s) Has $s 'Object.ReferenceEquals(Interlocked.CompareExchange(ref capture, null, null), original) && original.IsOwnedBy(owner, this) && captureCompleted';Has $s 'output.CaptureCompleted && stdout.CaptureCompleted && output.EofReturned && stdout.EofReturned && output.EofValue == -1 && stdout.EofValue == -1';Has $s 'Require(Same(output.Identity, Information(output), false) && Same(stdout.Identity, Information(stdout), false));';Has $s 'RequireCommitment(original.Commitments.Output, terminal.Record.Artifacts.Output);'
    },'original.IsOwnedBy(owner, this)','original != null'),
    @('l_byte_copies_revalidate_both_sides_of_private_clone','copy',{
        param($s) Has $s 'Enter();';Has $s 'AssertHeldCaptureCore(original); byte[] bytes = original.CopyHeldBytes(this, standardOutput); AssertHeldCaptureCore(original); return bytes;';Has $s 'catch (Exception) { Interlocked.Exchange(ref failed, 1); throw; }'
    },'AssertHeldCaptureCore(original); return bytes;','return bytes;'),
    @('l_capture_clones_arrays_and_checks_original_owner_before_copy','capture',{
        param($s) Has $s 'this.output = (byte[])output.Clone(); this.stdout = (byte[])stdout.Clone();';Has (Method $s 'internal byte[] CopyHeldBytes(') 'if (!Object.ReferenceEquals(original, owner)) throw';Has $s 'return (byte[])(standardOutput ? stdout : output).Clone();';Has $s 'public byte[]' 0
    },'return (byte[])(standardOutput ? stdout : output).Clone();','return standardOutput ? stdout : output;'),
    @('l_capture_never_claims_acceptance_or_execution_permission','capture',{
        param($s) foreach($field in @('BuildAccepted','FullProtocolAccepted','OutputAccepted','NativeQualified','ImportPermitted','PublicationPermitted','ProductionAuthorized')){Has $s ($field+' = false')};Has $s 'public const string Abi = "stratus-runtime-build-output-capture-v1";'
    },'FullProtocolAccepted = false','FullProtocolAccepted = true'),
    @('l_output_owner_has_no_native_close_or_path_reopen_recovery','owner',{
        param($s) foreach($bad in @('CloseHandle(','.Dispose(','~BuildOutputOwner(','.Seek(','File.Open(','File.ReadAllBytes(','Directory.CreateDirectory(')){Has $s $bad 0};Has $s 'private const long OutputMaximum = 16777216, StdoutMaximum = 1048576;'
    },'private const long OutputMaximum = 16777216','private const long OutputMaximum = 33554432'),
    @('w_binding_registers_exact_l_once_before_start','wBind',{
        param($s) Has $s 'terminalCoordinatorBindingAttempted || terminalCoordinator != null || started || disposed || releaseStarted || containmentStarted || original == null || !original.OwnsPreparedGuardian(this)';Has $s 'terminalCoordinatorBindingAttempted = true; terminalCoordinator = original;'
    },'!original.OwnsPreparedGuardian(this)','false'),
    @('w_terminal_binding_is_retained_before_launch_copy_close','normal',{
        param($s) Order $s @('if (normalBinding == null) normalBinding = binding;','else if (normalBinding != binding) throw','TerminalRecord actualTerminal = TerminalWire.ParseProducerTerminal(actualProducer.Stdout, binding);','normalClosure = new BuildTerminalClosure(this, actualTerminal, actualPExit, normalExited);','closureProven = true; normalTerminalProven = true;','if (launchCopy != null) launchCopy.CloseAfterContainment();');Has $s 'CompilerJobWire.Binding(controlEndpoints.Nonce, (uint)launchCopy.ProducerId, launchCopy.ProducerBirth, startupSnapshot.StartedTick, startupSnapshot.DeadlineTick, contextDelivery.Bytes, contextDelivery.Sha256)'
    },'else if (normalBinding != binding) throw','else if (normalBinding == null) throw'),
    @('w_output_boundary_uses_retained_binding_not_closed_getters','wBoundary',{
        param($s) Has $s 'Object.ReferenceEquals(normalClosure, observed)';Has $s 'original.OwnsNormalGuardian(this, observed, metadata)';Has $s 'metadata.ContextSha256 != contextDelivery.Sha256 || originalContextBytes != contextDelivery.Bytes';Has $s 'if (normalBinding == null || observed.Record.Binding != normalBinding) throw';Has $s 'launchCopy.ProducerId' 0;Has $s 'launchCopy.ProducerBirth' 0
    },'observed.Record.Binding != normalBinding','observed.Record.Binding == null'),
    @('w_output_boundary_freshly_observes_original_p_and_w','wBoundary',{
        param($s) Order $s @('normalObservationThread = Thread.CurrentThread.ManagedThreadId; normalObservationActive = true;','CompilerResult actualProducer = producer.PollNormalTerminal(this);','producer.OriginalNormalExitFileTime(this, actualProducer) != observed.ProducerExitFileTime','if (!RootBoundary(false) || !CollectorsClosed()) throw','GetProcessTimes(creationOutputs.process, out created, out exited, out kernel, out user)','GetExitCodeProcess(creationOutputs.process, out code)','if (created != birth || exited != observed.GuardianExitFileTime || code != 0)');Has $s 'finally { normalObservationActive = false; normalObservationThread = 0; Leave(); }'
    },'producer.OriginalNormalExitFileTime(this, actualProducer) != observed.ProducerExitFileTime','false'),
    @('loader_inspects_four_exact_returned_assembly_types','loader',{
        param($s) Has $s '$coordinatorType=$assembly.GetType(''Stratus.RuntimeBuild.BuildTerminalCoordinator'',$true,$false)';Has $s '$captureType=$assembly.GetType(''Stratus.RuntimeBuild.BuildOutputCapture'',$true,$false)';Has $s 'foreach ($type in @($commandType,$resultType,$coordinatorType,$captureType))';Has $s '-not [object]::ReferenceEquals($type.Assembly,$assembly)'
    },'foreach ($type in @($commandType,$resultType,$coordinatorType,$captureType))','foreach ($type in @($commandType,$resultType))'),
    @('loader_separate_abis_do_not_replace_generic_v2','loader',{
        param($s) Has $s '$abi.GetRawConstantValue() -cne ''stratus-runtime-build-bootstrap-v2''';Has $s '@{Type=$coordinatorType;Abi=''stratus-runtime-build-terminal-coordinator-v1''}';Has $s '@{Type=$captureType;Abi=''stratus-runtime-build-output-capture-v1''}';Has $s '$entry.Type.GetConstructors().Length -ne 0'
    },'@{Type=$coordinatorType;Abi=''stratus-runtime-build-terminal-coordinator-v1''}','@{Type=$coordinatorType;Abi=''stratus-runtime-build-bootstrap-v2''}'),
    @('loader_exact_method_table_matches_declared_api_surface','loader',{
        param($s) Has $s '@{Name=''Prepare'';Static=$true;Return=$coordinatorType;Parameters=[Type[]]@([byte[]])}';foreach($name in @('Start','ReleaseAfterContainment')){Has $s ('@{Name='''+$name+''';Static=$false;Return=[void];Parameters=[Type[]]@()}')};Has $s '@{Name=''Poll'';Static=$false;Return=$captureType;Parameters=[Type[]]@()}';foreach($name in @('ReadOutput','ReadStdout')){Has $s ('@{Name='''+$name+''';Static=$false;Return=[byte[]];Parameters=[Type[]]@()}')};Has $s '@{Name=''PollContainment'';Static=$false;Return=[bool];Parameters=[Type[]]@()}';Has $s '$coordinatorType.GetMethods([Reflection.BindingFlags]''Public,Instance,Static,DeclaredOnly'').Length -ne $shapes.Length';Has $s '$captureType.GetMethods([Reflection.BindingFlags]''Public,Instance,Static,DeclaredOnly'').Length -ne 0'
    },'@{Name=''Poll'';Static=$false;Return=$captureType;Parameters=[Type[]]@()}','@{Name=''Poll'';Static=$false;Return=[object];Parameters=[Type[]]@()}'),
    @('loader_rejects_generic_mutable_optional_or_wrong_signature','loader',{
        param($s) Has $s '$method.IsGenericMethod -or $method.ReturnType -ne $shape.Return -or $method.IsStatic -ne $shape.Static -or -not [object]::ReferenceEquals($method.DeclaringType,$coordinatorType)';Has $s '$parameter.IsOptional -or $parameter.IsOut -or $parameter.ParameterType.IsByRef'
    },'$parameter.IsOptional -or $parameter.IsOut -or $parameter.ParameterType.IsByRef','$parameter.IsOut'),
    @('loader_exact_capture_table_includes_all_eleven_fields','loader',{
        param($s) foreach($name in @('OutputBytes','StdoutBytes')){Has $s ('@{Name='''+$name+''';Type=[long]}')};foreach($name in @('OutputSha256','StdoutSha256')){Has $s ('@{Name='''+$name+''';Type=[string]}')};foreach($name in @('BuildAccepted','FullProtocolAccepted','OutputAccepted','NativeQualified','ImportPermitted','PublicationPermitted','ProductionAuthorized')){Has $s ('@{Name='''+$name+''';Type=[bool]}')};Has $s '$captureType.GetFields($instanceFlags).Length -ne $captureFields.Length'
    },'@{Name=''FullProtocolAccepted'';Type=[bool]},',''),
    @('loader_rejects_mutable_capture_and_extra_instance_surface','loader',{
        param($s) Has $s '$coordinatorType.GetFields($instanceFlags).Length -ne 0';Has $s '$captureType.GetProperties($instanceFlags).Length -ne 0';Has $s '$coordinatorType.GetProperties($instanceFlags).Length -ne 0';Has $s '-not $field.IsInitOnly -or $field.IsStatic -or $field.FieldType -ne $shape.Type'
    },'-not $field.IsInitOnly -or $field.IsStatic -or $field.FieldType -ne $shape.Type','$field.IsStatic -or $field.FieldType -ne $shape.Type'),
    @('loader_returns_retained_types_but_no_execution_permission','loader',{
        param($s) Has $s 'terminal_coordinator_type=$coordinatorType;output_capture_type=$captureType';Has $s 'terminal_coordinator_abi=''stratus-runtime-build-terminal-coordinator-v1''';Has $s 'approved_profile_origin_proven=$false;dependency_closure_proven=$false';Has $s 'compiler_executed=$false;candidate_qualified=$false;production_permitted=$false'
    },'production_permitted=$false','production_permitted=$true'),
    @('w_prepared_count_matches_original_admitted_inputs_before_quiescence','wPrepared',{
        param($s) Order $s @('TerminalWire.ParsePrepared(','Require(record.InputCount == terminalObservation.Context.CopyInputs().Length);','jobTransfer.CloseAdmissionForNormalTerminal(); rootTransfer.CloseAdmissionForNormalTerminal();','terminalObservation.PreparedFrame = (byte[])actual.Clone(); terminalObservation.Record = record;','Interlocked.Exchange(ref terminalObservation.PreparedObserved, 1);')
    },'Require(record.InputCount == terminalObservation.Context.CopyInputs().Length);','Require(record.InputCount >= 10 && record.InputCount <= 104);'),
    @('w_drain_acquires_publication_before_reading_prepared_record','wDrain',{
        param($s) Has $s 'return observation != null && Interlocked.CompareExchange(ref observation.PreparedObserved, 0, 0) == 1 && observation.Record != null &&';Has $s 'Interlocked.CompareExchange(ref observation.AckWriteAttempted, 0, 0) == 1 && Interlocked.CompareExchange(ref observation.HalfCloseAttempted, 0, 0) == 1';Has $s 'observation.HalfCloseSucceeded'
    },'Interlocked.CompareExchange(ref observation.PreparedObserved, 0, 0) == 1 && observation.Record != null','observation.Record != null && Interlocked.CompareExchange(ref observation.PreparedObserved, 0, 0) == 1'),
    @('l_terminal_count_matches_original_inputs_before_retaining_capture','poll',{
        param($s) Order $s @('Require(observed.BelongsTo(guardian));','Require(observed.Record != null && observed.Record.InputCount == metadata.CopyInputs().Length);','terminal = observed; phase = Phase.Capturing;','capture = output.CaptureOriginalTerminalOutputs(); captureReturned = true;')
    },'observed.Record.InputCount == metadata.CopyInputs().Length','observed.Record.InputCount >= 10')
)
$expectedPairs=51;$expected=102
Assert-TerminalOutput ($rows.Count -eq $expectedPairs -and $expected -eq 2*$expectedPairs) 'Fixed case inventory differs.'
$executed=0;$results=[Collections.Generic.List[object]]::new()
foreach($row in $rows){
    Assert-TerminalOutput ($row.Count -eq 5) 'Fixed row shape differs.'
    # Find/Has/Order/Block and both mutation operands use the same language as
    # the selected source. The mode comes only from this fixed literal table.
    $script:sourceSyntax=if($row[1] -ceq 'loader'){'PowerShell'}else{'CSharp'}
    $selected=$blocks[$row[1]];$check=$row[2]
    & $check $selected;$executed++;$results.Add([pscustomobject]@{name=$row[0];variant='original_source';passed=$true})
    $mutated=Replace-Tokens $selected $row[3] $row[4];$rejected=$false
    try{& $check $mutated}catch{
        Assert-TerminalOutput ($_.Exception.Message.StartsWith('Terminal output source contract:',[StringComparison]::Ordinal)) ('Unexpected negative-control exception: '+$row[0])
        $rejected=$true
    }
    Assert-TerminalOutput $rejected ('Weakening mutation survived: '+$row[0])
    $executed++;$results.Add([pscustomobject]@{name=$row[0];variant='mutated_source_rejected';passed=$true})
}
Assert-TerminalOutput ($executed -eq $expected -and $results.Count -eq $expected) 'Fixed cases did not complete.'
$before=[Collections.Generic.List[object]]::new();$after=[Collections.Generic.List[object]]::new()
foreach($name in $sources.Keys){
    $old=$sources[$name];$latest=Read-TerminalOutputSource $old.path
    Assert-TerminalOutput ($latest.sha256 -ceq $old.sha256) ('Subject changed during inspection: '+$name)
    $before.Add([pscustomobject]@{name=$name;path=$old.path;sha256=$old.sha256})
    $after.Add([pscustomobject]@{name=$name;path=$latest.path;sha256=$latest.sha256})
}
$testAfter=Read-TerminalOutputSource $PSCommandPath
Assert-TerminalOutput ($testAfter.sha256 -ceq $testBefore.sha256) 'Test changed during inspection.'
[pscustomobject]@{
    format='stratus-terminal-output-source-contract-tests-v1'
    expected_pairs=$expectedPairs;expected=$expected;executed=$executed;passed=$executed;failed=0;skipped=0
    powershell_version=$PSVersionTable.PSVersion.ToString();powershell_edition=$PSVersionTable.PSEdition
    source_hashes_before=$before.ToArray();source_hashes_after=$after.ToArray()
    test_sha256_before=$testBefore.sha256;test_sha256_after=$testAfter.sha256;source_and_test_hashes_unchanged=$true
    source_tokens_and_directed_inert_mutations_only=$true
    lexical_harness_preflight_passed=$true
    target_compiled=$false;target_loaded=$false;target_lifecycle_executed=$false;native_qualified=$false
    filesystem_fixture_created=$false;csharp_semantics_proven=$false;writer_isolation_qualified=$false
    independent_launcher_watchdog_configured=$false;physical_ownership_proven=$false
    actual_terminal_protocol_executed=$false;output_capture_executed=$false;accepted_output_sealed=$false
    full_protocol_accepted=$false;full_build_accepted=$false;import_permitted=$false;production_authorized=$false
    cases=$results.ToArray()
}|ConvertTo-Json -Depth 6 -Compress
