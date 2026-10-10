param()
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
# Fixed C# source data only. No target AST, import, evaluation, reflection,
# compilation/loading, target/native/fixture/network/operational invocation.
# Only the literal local predicates below execute; source/mutations never do.
# A token assertion is not C# semantics or native lifetime qualification.
function Assert-GuardianPipe {
    param([bool]$Okay,[string]$Message)
    if(-not $Okay){throw ('Guardian pipe source contract: '+$Message)}
}
function Read-GuardianPipeSource {
    param([string]$Path)
    $full=[IO.Path]::GetFullPath($Path);$length=[IO.FileInfo]::new($full).Length
    Assert-GuardianPipe ($length -gt 0 -and $length -le 1048576) 'Inspection file exceeds one MiB.'
    $raw=[byte[]]::new([int]$length)
    $stream=[IO.File]::Open($full,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {
        Assert-GuardianPipe ($stream.Length -eq $length) 'Inspection length changed before read.'
        $offset=0
        while($offset -lt $raw.Length){
            $count=$stream.Read($raw,$offset,[Math]::Min(65536,$raw.Length-$offset))
            Assert-GuardianPipe ($count -gt 0 -and $count -le $raw.Length-$offset) 'Inspection read truncated or exceeded its fixed buffer.'
            $offset+=$count
        }
        Assert-GuardianPipe ($stream.ReadByte() -eq -1 -and $stream.Length -eq $length) 'Inspection length changed during bounded read.'
    } finally {$stream.Dispose()}
    $text=[Text.UTF8Encoding]::new($false,$true).GetString($raw)
    $hash=[Security.Cryptography.SHA256]::Create()
    try{$digest=[BitConverter]::ToString($hash.ComputeHash($raw)).Replace('-','').ToLowerInvariant()}finally{$hash.Dispose()}
    return [pscustomobject]@{path=$full;bytes=$length;text=$text;sha256=$digest}
}
function Tokens {
    param([string]$Text)
    Assert-GuardianPipe ($Text.Length -le 1048576) 'Token input exceeds one MiB of characters.'
    # Deliberately limited C# data lexer. Quoted braces stay inside one token.
    $pattern=@'
//[^\r\n]* | /\*.*?\*/ | \#[^\r\n]* | @"(?:""|[^"])*" | "(?:\\.|[^"\\])*" | '(?:\\.|[^'\\])*'
| [A-Za-z_][A-Za-z_0-9]* | [0-9]+ | == | != | >= | <= | \+\+ | -- | && | \|\| | => | [^\s]
'@
    $result=[Collections.Generic.List[string]]::new()
    $options=[Text.RegularExpressions.RegexOptions]::Singleline -bor [Text.RegularExpressions.RegexOptions]::IgnorePatternWhitespace
    foreach($m in [regex]::Matches($Text,$pattern,$options,[TimeSpan]::FromSeconds(5))){
        if(-not ($m.Value.StartsWith('//') -or $m.Value.StartsWith('/*') -or $m.Value.StartsWith('#'))){
            Assert-GuardianPipe ($result.Count -lt 262144) 'Token inspection bound exceeded.'
            $result.Add($m.Value)
        }
    }
    return ,$result.ToArray()
}
function Find {
    param([string[]]$Source,[string]$Sequence)
    $wanted=Tokens $Sequence;Assert-GuardianPipe ($wanted.Count -gt 0) 'Empty predicate.'
    $found=[Collections.Generic.List[int]]::new()
    for($i=0;$i -le $Source.Count-$wanted.Count;$i++){
        $same=$true
        for($j=0;$j -lt $wanted.Count;$j++){if($Source[$i+$j] -cne $wanted[$j]){$same=$false;break}}
        if($same){$found.Add($i)}
    }
    return ,$found.ToArray()
}
function Has {
    param([string[]]$Source,[string]$Sequence,[int]$Count=1)
    Assert-GuardianPipe ((Find $Source $Sequence).Count -eq $Count) ('Sequence differs: '+$Sequence)
}
function Order {
    param([string[]]$Source,[string[]]$Sequences)
    $last=-1
    foreach($sequence in $Sequences){
        $found=Find $Source $sequence
        Assert-GuardianPipe ($found.Count -eq 1 -and $found[0] -gt $last) ('Order differs: '+$sequence)
        $last=$found[0]
    }
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
    Assert-GuardianPipe ($found.Count -eq 1) ('Missing or ambiguous block: '+$Signature)
    $open=$found[0];while($open -lt $Source.Count -and $Source[$open] -cne '{'){$open++}
    Assert-GuardianPipe ($open -lt $Source.Count) 'Opening brace missing.'
    $depth=0
    for($i=$open;$i -lt $Source.Count;$i++){
        if($Source[$i] -ceq '{'){$depth++}elseif($Source[$i] -ceq '}'){$depth--}
        if($depth -eq 0){return ,([string[]]$Source[$found[0]..$i])}
    }
    throw 'Guardian pipe source contract: Unbalanced source block.'
}
function Method {param([string[]]$Class,[string]$Signature) return ,(Block $Class $Signature -Direct)}
function Replace-Tokens {
    param([string[]]$Source,[string]$Needle,[string]$Replacement)
    $at=Find $Source $Needle;Assert-GuardianPipe ($at.Count -eq 1) ('Mutation anchor differs: '+$Needle)
    $old=Tokens $Needle;$new=Tokens $Replacement;$result=[Collections.Generic.List[string]]::new()
    for($i=0;$i -lt $at[0];$i++){$result.Add($Source[$i])}
    foreach($token in $new){$result.Add($token)}
    for($i=$at[0]+$old.Count;$i -lt $Source.Count;$i++){$result.Add($Source[$i])}
    return ,$result.ToArray()
}
function Mutate-Method {
    param([string[]]$Class,[string]$Signature,[string]$Needle,[string]$Replacement)
    # Exact direct method selection plus one unique in-method token anchor.
    # Reinsert only that method's inert tokens; other causal helpers stay intact.
    $original=Method $Class $Signature
    $changed=Replace-Tokens $original $Needle $Replacement
    return ,(Replace-Tokens $Class ($original -join ' ') ($changed -join ' '))
}
function Pure-RecordedObserver {
    param([string[]]$Source)
    # Fixed direct-call exclusions; the rows additionally enumerate the actual
    # reachable local helper chain. This is not a general call-graph proof.
    foreach($bad in @('.Join(','.Read(','.ReadByte(','.Write(','.Dispose(','.SafePipeHandle','.SafeFileHandle',
        '.IsClosed','.IsInvalid','.DangerousGetHandle(','.SetHandleAsInvalid(','GetHandleInformation(',
        'SetHandleInformation(','GetProcessTimes(','GetProcessId(','WaitForSingleObject(','CreatePipe(',
        'CreateProcess(','CloseHandle(','Admission(','Enter(','StartNew(','AssertLivePipeReader(',
        'RootBoundary(','CollectorClosed(')){Has $Source $bad 0}
}
$testBefore=Read-GuardianPipeSource $PSCommandPath
$sources=[ordered]@{}
foreach($name in @('GuardianLaunch','Producer','Inputs','StartupPreload')){
    $sources[$name]=Read-GuardianPipeSource ([IO.Path]::Combine($PSScriptRoot,'..',('Stratus.RuntimeBuild.'+$name+'.cs')))
}
# Fixed lexical preflight is harness validation, not an extra subject case.
$sample=@'
char slash = '\\'; char quote = '\''; string value = "a\"b"; /* {ignored} */ string brace = @"{""}";
'@
$expectedTokens=@'
char|slash|=|'\\'|;|char|quote|=|'\''|;|string|value|=|"a\"b"|;|string|brace|=|@"{""}"|;
'@
Assert-GuardianPipe (((Tokens $sample) -join '|') -ceq $expectedTokens) 'CSharp literal/comment lexer regression.'
$g=Block (Tokens $sources.GuardianLaunch.text) 'public sealed class GuardianLaunch : IDisposable'
$p=Block (Tokens $sources.Producer.text) 'public sealed class DisposableProducerLaunch : IDisposable'
$i=Block (Tokens $sources.Inputs.text) 'public sealed class BuildInputLeaseSet : IDisposable'
$l=Block (Tokens $sources.StartupPreload.text) 'public sealed class StartupPreloadOwner : IDisposable'
$subjects=@{G=$g;P=$p;I=$i;L=$l}
$rows=@(
    @('l_pair_retained_before_native_outs','G','private void PrepareStdioPairs(',{
        param($s)
        $m=Method $s 'private void PrepareStdioPairs('
        Has $m 'inputPair = new GuardianPipePair(this, 0); OpenOriginalStdioPair(inputPair); Admission();'
        Has $m 'outputPair = new GuardianPipePair(this, 1); OpenOriginalStdioPair(outputPair); Admission();'
        Has $m 'errorPair = new GuardianPipePair(this, 2); OpenOriginalStdioPair(errorPair); Admission();'
        Has (Method $s 'private void OpenOriginalStdioPair(') '!IsOriginalPair(pair) || !PairHasNoAcquisition(pair)'
        Has (Block $s 'private sealed class GuardianPipePair') 'Read = new GuardianPipeSlot(owner, this, true); Write = new GuardianPipeSlot(owner, this, false);'
    },'inputPair = new GuardianPipePair(this, 0); OpenOriginalStdioPair(inputPair);','OpenOriginalStdioPair(inputPair); inputPair = new GuardianPipePair(this, 0);'),
    @('l_pair_native_out_values_and_error_are_retained_before_validation','G','private void OpenOriginalStdioPair(',{
        param($s) Order (Method $s 'private void OpenOriginalStdioPair(') @('pair.Attempted = true;','bool created = CreatePipe(out pair.Read.Raw, out pair.Write.Raw, ref security, 0);','pair.Error = Marshal.GetLastWin32Error(); pair.Succeeded = created; pair.Returned = true;','if (!created) throw','RequireOriginalPair(pair);','SetOriginalPipeInheritance(pair.Parent, 0);')
    },'out pair.Read.Raw','out unretainedRead'),
    @('l_pair_failed_or_unknown_never_absence','G','private void RequireOriginalPair(',{
        param($s) Has (Method $s 'private void RequireOriginalPair(') '!IsOriginalPair(pair) || !pair.Attempted || !pair.Returned || !pair.Succeeded || !Valid(pair.Read.Raw) || !Valid(pair.Write.Raw) || pair.Read.Raw == pair.Write.Raw'
        Has (Method $s 'private void RequireOriginalPipeSlot(') 'RequireOriginalPair(slot.Pair);'
        Has (Method $s 'private void RetirePipeSlot(') 'RequireOriginalPipeSlot(slot); RequireSettledPipeWrapper(slot);'
    },'!pair.Returned','false'),
    @('l_pair_known_failure_cannot_become_native_use','G','private void RequireOriginalPair(',{
        param($s) Has (Method $s 'private void RequireOriginalPair(') '!pair.Attempted || !pair.Returned || !pair.Succeeded'
        Has (Method $s 'private void SetOriginalPipeInheritance(') 'RequireOriginalPipeSlot(slot);'
        Has (Method $s 'private void AssertLivePipeReader(') 'RequireOriginalPipeSlot(slot); RequireSettledPipeWrapper(slot);'
        Has (Method $s 'private IntPtr BorrowOriginalChildEndForBirth(') 'RequireOriginalPair(pair); RequireOriginalPipeSlot(pair.Child);'
    },'!pair.Succeeded','false'),
    @('l_no_acquisition_is_only_canonical_unattempted','G','private bool PairHasNoAcquisition(',{
        param($s) Has (Method $s 'private bool PairHasNoAcquisition(') 'return !pair.Attempted && !pair.Returned && !pair.Succeeded && pair.Error == 0 && UntouchedSlot(pair.Read) && UntouchedSlot(pair.Write);'
        $u=Method $s 'private static bool UntouchedSlot(';Has $u 'slot.Raw == IntPtr.Zero && !slot.Close.Attempted && !slot.Close.Returned && !slot.Close.Succeeded'
        foreach($field in @('WrapperConstruction','StreamConstruction','WrapperSuppression','StreamSuppression','Invalidation','Disposal')){Has $u ('Unattempted(slot.'+$field+')')}
        Has (Method $s 'private void StartCollector(') 'if (retained != null || !NoChild()) throw'
    },'!pair.Attempted && !pair.Returned','!pair.Returned'),
    @('l_alias_quarantine_precedes_every_slot_use','G','private void RequireOriginalPipeSlot(',{
        param($s) Has (Method $s 'private void RequireOriginalPipeSlot(') 'foreach (GuardianPipeSlot other in RetainedPipeSlots()) { if (other != null && !Object.ReferenceEquals(other, slot) && other.Raw == slot.Raw)'
        $all=Method $s 'private GuardianPipeSlot[] RetainedPipeSlots(';foreach($pair in @('inputPair','outputPair','errorPair')){foreach($end in @('Read','Write')){Has $all ($pair+' == null ? null : '+$pair+'.'+$end)}}
        foreach($sig in @('private void SetOriginalPipeInheritance(','private void RetirePipeSlot(','private void AssertLivePipeReader(')){Has (Method $s $sig) 'RequireOriginalPipeSlot(slot);'}
    },'other != null && !Object.ReferenceEquals(other, slot)','other != null && other.Pair.Succeeded && !Object.ReferenceEquals(other, slot)'),
    @('l_alias_quarantine_includes_original_non_stdio_and_process_slots','G','private void RequireOriginalPipeSlot(',{
        param($s) Has (Method $s 'private void RequireOriginalPipeSlot(') 'slot.Raw == originalStopLaunchHandle || slot.Raw == originalTransferLaunchHandle || slot.Raw == originalExecutableHandle || slot.Raw == creationOutputs.process || slot.Raw == creationOutputs.thread || (controlEndpoints != null && (slot.Raw == controlEndpoints.Read || slot.Raw == controlEndpoints.Write)) || (contextDelivery != null && slot.Raw == contextDelivery.Handle)'
        Has (Method $s 'private void RequireOriginalCreation(') 'slot.Raw == creationOutputs.process || slot.Raw == creationOutputs.thread'
        Has (Method $s 'public void StartRunning(') 'originalStopLaunchHandle = ownerHandle;'
        Has (Method $s 'public void StartRunning(') 'originalTransferLaunchHandle = transferHandle;'
    },'slot.Raw == creationOutputs.process || slot.Raw == creationOutputs.thread','false'),
    @('l_birth_pair_starts_noninheritable','G','private void OpenOriginalStdioPair(',{
        param($s) Order (Method $s 'private void OpenOriginalStdioPair(') @('security.Inherit = 0;','pair.Attempted = true;','bool created = CreatePipe(','SetOriginalPipeInheritance(pair.Parent, 0); SetOriginalPipeInheritance(pair.Child, 1);')
    },'security.Inherit = 0;','security.Inherit = 1;'),
    @('l_birth_parent_noninheritance_and_child_inheritance_are_observed','G','private void OpenOriginalStdioPair(',{
        param($s) Has (Method $s 'private void OpenOriginalStdioPair(') 'SetOriginalPipeInheritance(pair.Parent, 0); SetOriginalPipeInheritance(pair.Child, 1);'
        Order (Method $s 'private void SetOriginalPipeInheritance(') @('slot.InheritanceAttempted = true;','bool configured = SetHandleInformation(slot.Raw, 1, expected);','slot.InheritanceError = Marshal.GetLastWin32Error(); slot.InheritanceSucceeded = configured; slot.InheritanceReturned = true;','slot.FlagsAttempted = true;','bool observed = GetHandleInformation(slot.Raw, out slot.Flags);','if (slot.Flags != expected) throw')
        Has (Method $s 'private void AssertOriginalStdioForBirth(') 'uint expected = Object.ReferenceEquals(slot, pair.Child) ? 1u : 0u;'
        Has (Method $s 'public void StartRunning(') 'AssertOriginalStdioForBirth(); AssertAllowlist(allowed);'
    },'SetOriginalPipeInheritance(pair.Parent, 0);','SetOriginalPipeInheritance(pair.Parent, 1);'),
    @('l_birth_inheritance_and_eight_roles_remain_exact','G','public void StartRunning(',{
        param($s) $m=Method $s 'public void StartRunning('
        Has $m 'IntPtr[] allowed = new IntPtr[] { BorrowOriginalChildEndForBirth(inputPair), BorrowOriginalChildEndForBirth(outputPair), BorrowOriginalChildEndForBirth(errorPair), ownerHandle, controlEndpoints.Read, controlEndpoints.Write, contextDelivery.Handle, transferHandle };'
        Has $m 'Marshal.WriteIntPtr(inherited.Value, IntPtr.Size * 7, transferHandle);'
        Has $m 'startup.startup.stdin = BorrowOriginalChildEndForBirth(inputPair); startup.startup.stdout = BorrowOriginalChildEndForBirth(outputPair); startup.startup.stderr = BorrowOriginalChildEndForBirth(errorPair);'
        Has (Method $s 'private static void AssertAllowlist(') 'handles == null || handles.Length != 8'
    },'IntPtr[] allowed = new IntPtr[] { BorrowOriginalChildEndForBirth(inputPair),','IntPtr[] allowed = new IntPtr[] { inputPair.Parent.Raw,'),
    @('l_read_wrappers_are_original_nonowning_only','G','private void OpenOriginalStdioPair(',{
        param($s) $m=Method $s 'private void OpenOriginalStdioPair('
        Order $m @('if (pair.Role == 0) return;','GuardianPipeSlot slot = pair.Parent;','slot.WrapperConstruction.Attempted = true;','slot.Wrapper = new SafePipeHandle(slot.Raw, false);','slot.StreamConstruction.Attempted = true;','slot.Stream = new AnonymousPipeClientStream(PipeDirection.In, slot.Wrapper);')
        Has (Method $s 'private void AssertLivePipeReader(') '!slot.Stream.CanRead || slot.Stream.CanWrite'
        Has (Method $s 'private void AssertOriginalStdioForBirth(') '!NoWrapperAttempt(pair.Child) || (pair.Role == 0 && !NoWrapperAttempt(pair.Parent))'
        Has $s 'new AnonymousPipeServerStream(' 0
    },'new SafePipeHandle(slot.Raw, false)','new SafePipeHandle(slot.Raw, true)'),
    @('l_constructor_unknown_cannot_retry_or_retire','G','private static bool NoWrapperAttempt(',{
        param($s) $m=Method $s 'private static bool NoWrapperAttempt('
        Has $m 'slot.Wrapper == null && slot.Stream == null && Unattempted(slot.WrapperConstruction) && Unattempted(slot.StreamConstruction)'
        Has (Method $s 'private bool UnusedReaderDisposition(') 'collector == null && NoWrapperAttempt(pair.Parent)'
        Has (Method $s 'private void RequireSettledPipeWrapper(') '!KnownManagedSuccess(slot.StreamConstruction)'
        Has (Method $s 'private void RetirePipeSlot(') 'RequireOriginalPipeSlot(slot); RequireSettledPipeWrapper(slot);'
    },'Unattempted(slot.StreamConstruction)','true'),
    @('l_finalizer_suppression_is_retained_before_reader_use','G','private void RequireSettledPipeWrapper(',{
        param($s) Has (Method $s 'private void RequireSettledPipeWrapper(') '!KnownManagedSuccess(slot.WrapperSuppression) || !KnownManagedSuccess(slot.StreamConstruction) || slot.Stream == null || !KnownManagedSuccess(slot.StreamSuppression)'
        Order (Method $s 'private void OpenOriginalStdioPair(') @('slot.WrapperConstruction.Succeeded = true; slot.WrapperConstruction.Returned = true;','SuppressOriginalFinalizer(slot.Wrapper, slot.WrapperSuppression);','slot.StreamConstruction.Attempted = true;','slot.StreamConstruction.Succeeded = true; slot.StreamConstruction.Returned = true;','SuppressOriginalFinalizer(slot.Stream, slot.StreamSuppression);')
        Has (Method $s 'private static void SuppressOriginalFinalizer(') 'outcome.Attempted = true; try { GC.SuppressFinalize(original); outcome.Succeeded = true; outcome.Returned = true; }'
    },'!KnownManagedSuccess(slot.StreamSuppression)','false'),
    @('l_early_retirement_precedes_later_live_w_gate','G','public void StartRunning(',{
        param($s) $m=Method $s 'public void StartRunning('; $born=Block $m 'if (created)'
        Order $m @('creationSucceeded = created; creationReturned = true;','if (created) {','try { CloseClients(); }','launchCopy.RetireLaunchInheritance(this);','contextSection.RetireGuardianDelivery(this, sourceCapability); RootBoundary(true);')
        Has $born 'RequireOriginalCreation();';Has $born 'RootBoundary(' 0
        Has (Method $s 'private void CloseClients(') 'RequireOriginalCreation();'
    },'try { CloseClients(); }','RootBoundary(true); try { CloseClients(); }'),
    @('l_three_local_child_copies_all_retire','G','private void CloseClients(',{
        param($s) $m=Method $s 'private void CloseClients('
        Has $m 'foreach (GuardianPipePair pair in new GuardianPipePair[] { inputPair, outputPair, errorPair })'
        Has $m 'RequireOriginalPair(pair); RetirePipeSlot(pair.Child);'
        Has (Block $s 'private sealed class GuardianPipePair') 'internal GuardianPipeSlot Child { get { return Role == 0 ? Read : Write; } }'
        Has (Method $s 'public void StartRunning(') 'try { CloseClients(); }'
    },'new GuardianPipePair[] { inputPair, outputPair, errorPair }','new GuardianPipePair[] { outputPair, errorPair }'),
    @('l_stdout_local_child_copy_cannot_be_omitted','G','private void CloseClients(',{
        param($s) Has (Method $s 'private void CloseClients(') 'foreach (GuardianPipePair pair in new GuardianPipePair[] { inputPair, outputPair, errorPair }) { try { if (pair == null || PairHasNoAcquisition(pair)) continue; RequireOriginalPair(pair); RetirePipeSlot(pair.Child); } catch (Exception failure) { pending.Add(failure); } }'
    },'new GuardianPipePair[] { inputPair, outputPair, errorPair }','new GuardianPipePair[] { inputPair, errorPair }'),
    @('l_stderr_local_child_copy_cannot_be_omitted','G','private void CloseClients(',{
        param($s) Has (Method $s 'private void CloseClients(') 'foreach (GuardianPipePair pair in new GuardianPipePair[] { inputPair, outputPair, errorPair }) { try { if (pair == null || PairHasNoAcquisition(pair)) continue; RequireOriginalPair(pair); RetirePipeSlot(pair.Child); } catch (Exception failure) { pending.Add(failure); } }'
    },'new GuardianPipePair[] { inputPair, outputPair, errorPair }','new GuardianPipePair[] { inputPair, outputPair }'),
    @('l_unused_parent_stdin_has_own_native_retirement','G','private void CloseClients(',{
        param($s) Has (Method $s 'private void CloseClients(') 'if (inputPair != null && !PairHasNoAcquisition(inputPair)) { RequireOriginalPair(inputPair); RetirePipeSlot(inputPair.Parent); }'
        Has (Block $s 'private sealed class GuardianPipePair') 'internal GuardianPipeSlot Parent { get { return Role == 0 ? Write : Read; } }'
        Has (Method $s 'private void RetirePipeSlot(') 'bool closed = CloseHandle(outcome.Original);'
    },'RetirePipeSlot(inputPair.Parent);',''),
    @('l_early_four_known_outcomes_gate_started_and_ready','G','public void StartRunning(',{
        param($s) Has (Method $s 'public void StartRunning(') 'contextSection.AssertBothDeliveriesRetired(this, sourceCapability); AssertEarlyStdioRetired(); started = true;'
        $m=Method $s 'private void AssertEarlyStdioRetired(';Has $m 'foreach (GuardianPipePair pair in new GuardianPipePair[] { inputPair, outputPair, errorPair })'
        Has $m 'if (!PipeSlotReleased(pair.Child)) throw';Has $m 'if (!PipeSlotReleased(inputPair.Parent)) throw'
        Has (Method $s 'private bool PipeSlotReleased(') 'slot.Close.Attempted && slot.Close.Original == slot.Raw && slot.Close.Returned && slot.Close.Succeeded'
    },'contextSection.AssertBothDeliveriesRetired(this, sourceCapability); AssertEarlyStdioRetired(); started = true;','contextSection.AssertBothDeliveriesRetired(this, sourceCapability); started = true;'),
    @('l_ready_rechecks_all_early_native_outcomes','G','private GuardianReadyObservation ReadReadyCore(',{
        param($s) Order (Method $s 'private GuardianReadyObservation ReadReadyCore(') @('Admission(); if (!started) throw','AssertEarlyStdioRetired();','controlChannel.AssertBothDeliveriesRetired(this, sourceCapability);','HeldBoundary(); RootBoundary(true);')
        Has (Method $s 'private void AssertEarlyStdioRetired(') 'if (!PipeSlotReleased(inputPair.Parent)) throw'
    },'AssertEarlyStdioRetired();',''),
    @('l_collector_binds_original_owner_slot_and_destination','G','private bool IsOriginalCollector(',{
        param($s) Has (Method $s 'private bool IsOriginalCollector(') 'Object.ReferenceEquals(original.Owner, this) && Object.ReferenceEquals(original.Pair, pair) && Object.ReferenceEquals(original, isError ? errorCollector : outputCollector) && Object.ReferenceEquals(original.Parent, pair.Parent) && Object.ReferenceEquals(original.Stream, pair.Parent.Stream) && Object.ReferenceEquals(original.Destination, isError ? stderr : stdout) && original.IsError == isError'
        Order (Method $s 'private void StartCollector(') @('GuardianCollector original = new GuardianCollector(this, pair, destination, isError);','retained = original;','original.Construction.Attempted = true;','original.Worker = new Thread(delegate() { Capture(original); });','original.Start.Attempted = true;','original.Worker.Start();')
        Has (Method $s 'private void Capture(') '!Object.ReferenceEquals(Thread.CurrentThread, original.Worker)'
    },'Object.ReferenceEquals(original.Stream, pair.Parent.Stream)','original.Stream != null'),
    @('l_worker_entry_allows_pre_start_return_execution','G','private void Capture(',{
        param($s) $m=Method $s 'private void Capture('
        Has $m '!KnownManagedSuccess(original.Construction) || !original.Start.Attempted'
        Has $m 'original.Start.Returned' 0;Has $m 'KnownManagedSuccess(original.Start)' 0
        Has (Method $s 'private bool ActualCollectorClosureRecorded(') '!KnownManagedSuccess(original.Start)'
    },'!original.Start.Attempted','!original.Start.Attempted || !original.Start.Returned'),
    @('l_unknown_start_is_never_threadstate_retry','G','private void StartCollector(',{
        param($s) $m=Method $s 'private void StartCollector('
        Has $m 'if (!IsOriginalCollector(pair, retained, isError) || !KnownManagedSuccess(retained.Construction) || !KnownManagedSuccess(retained.Start)) throw'
        Has $m 'original.Start.Attempted = true; try { original.Worker.Start(); original.Start.Succeeded = true; original.Start.Returned = true; } catch (Exception) { original.Start.Returned = true; throw; }'
        Has $s 'ThreadState' 0;Has $s '.Start();' 1
        Has (Method $s 'public bool PollContainment(') 'try { StartCollectors(); }'
    },'!KnownManagedSuccess(retained.Start)','false'),
    @('l_known_existing_collector_cannot_fallthrough_to_replacement','G','private void StartCollector(',{
        param($s) $m=Method $s 'private void StartCollector('
        Has (Block $m 'if (retained != null)') 'throw new InvalidOperationException("Original guardian construction/Start failed or is unknown; no retry/replacement."); return;'
        Order $m @('if (retained != null) {','GuardianCollector original = new GuardianCollector(this, pair, destination, isError);','retained = original;')
    },'throw new InvalidOperationException("Original guardian construction/Start failed or is unknown; no retry/replacement."); return;','throw new InvalidOperationException("Original guardian construction/Start failed or is unknown; no retry/replacement.");'),
    @('l_actual_eof_is_not_finally_or_join','G','private void Capture(',{
        param($s) $m=Method $s 'private void Capture('
        Has $m 'if (count == 0) { lock (captureGate) { if (original.IsError) stderrEof = true; else stdoutEof = true; } return; }'
        $f=Block $m 'finally';Has $f 'stdoutEof = true;' 0;Has $f 'stderrEof = true;' 0
        Has $f 'stderrDone = true;';Has $f 'stdoutDone = true;'
        Has (Method $s 'private bool CollectorClosed(') 'if (isError ? !stderrEof || !stderrDone : !stdoutEof || !stdoutDone) return false;'
    },'Interlocked.Exchange(ref original.LastUseSettled, 1);','stdoutEof = true; Interlocked.Exchange(ref original.LastUseSettled, 1);'),
    @('l_finally_last_use_follows_final_access','G','private void Capture(',{
        param($s) $f=Block (Method $s 'private void Capture(') 'finally'
        Has $f 'if (original != null) { lock (captureGate) { if (original.IsError) stderrDone = true; else stdoutDone = true; } Interlocked.Exchange(ref original.LastUseSettled, 1); }'
        Has (Method $s 'private bool ActualCollectorClosureRecorded(') 'Interlocked.CompareExchange(ref original.LastUseSettled, 0, 0) != 1'
    },'Interlocked.Exchange(ref original.LastUseSettled, 1);',''),
    @('l_faulted_capture_keeps_original_bounded_drain','G','private void Capture(',{
        param($s) $m=Method $s 'private void Capture('; $loop=Block $m 'while (true)'
        Has $loop 'return;' 1;Has $loop 'break;' 0
        Has $loop 'if (count == 0) { lock (captureGate) { if (original.IsError) stderrEof = true; else stdoutEof = true; } return; }'
        Has $loop 'captureFailed = true;';Has $loop 'int retained = Math.Min(count, CaptureLimit - (int)original.Destination.Length);'
        Has $m 'Admission(' 0
    },'captureFailed = true; } int retained','captureFailed = true; return; } int retained'),
    @('l_collector_reads_fixed_512_byte_chunks','G','private void Capture(',{
        param($s) Has (Method $s 'private void Capture(') 'byte[] buffer = new byte[512];'
        Has (Method $s 'private void Capture(') 'int count = original.Stream.Read(buffer, 0, buffer.Length);'
        Has $s 'CaptureLimit = 4096'
    },'new byte[512]','new byte[4096]'),
    @('l_collector_retention_never_exceeds_fixed_capture_limit','G','private void Capture(',{
        param($s) Has $s 'CaptureLimit = 4096'
        Has (Method $s 'private void Capture(') 'int retained = Math.Min(count, CaptureLimit - (int)original.Destination.Length); if (retained > 0) original.Destination.Write(buffer, 0, retained);'
    },'Math.Min(count, CaptureLimit - (int)original.Destination.Length)','count'),
    @('l_any_stderr_is_sticky_even_below_capture_bound','G','private void Capture(',{
        param($s) Has (Method $s 'private void Capture(') 'if (original.IsError || original.Destination.Length + count > CaptureLimit) { if (!captureFailed) Record(new InvalidDataException("Guardian stderr or output overflow permanently rejects readiness.")); captureFailed = true; }'
        Has (Method $s 'private GuardianReadyObservation ReadReadyCore(') 'captureFailed || stderr.Length != 0 || stdoutEof || stderrEof'
    },'original.IsError || original.Destination.Length + count > CaptureLimit','original.Destination.Length + count > CaptureLimit'),
    @('l_join_unknown_differs_from_known_pending','G','private bool CollectorClosed(',{
        param($s) Order (Method $s 'private bool CollectorClosed(') @('if (ActualCollectorClosureRecorded(pair, original, isError)) return true;','if (pair.Parent.Close.Attempted) throw','if (original.Join.Attempted && !original.Join.Returned) throw','original.Join.Attempted = true; original.Join.Returned = false;','bool joined = original.Worker.Join(0);','original.Join.Succeeded = joined; original.Join.Returned = true;','return joined;')
    },'original.Join.Attempted && !original.Join.Returned','false'),
    @('l_join_pending_cannot_publish_known_success','G','private bool CollectorClosed(',{
        param($s) Has (Method $s 'private bool CollectorClosed(') 'bool joined = original.Worker.Join(0); original.Join.Succeeded = joined; original.Join.Returned = true; return joined;'
        Has (Method $s 'private bool ActualCollectorClosureRecorded(') '!KnownManagedSuccess(original.Join)'
    },'original.Join.Succeeded = joined;','original.Join.Succeeded = true;'),
    @('l_reader_retirement_requires_start_eof_lastuse_join','G','private bool ActualCollectorClosureRecorded(',{
        param($s) Has (Method $s 'private bool ActualCollectorClosureRecorded(') '!KnownManagedSuccess(original.Construction) || !KnownManagedSuccess(original.Start) || !KnownManagedSuccess(original.Join) || Interlocked.CompareExchange(ref original.LastUseSettled, 0, 0) != 1'
        Has (Method $s 'private void RetirePipeSlot(') '!ActualCollectorClosureRecorded(slot.Pair, collector, slot.Pair.Role == 2) && !UnusedReaderDisposition(slot.Pair, collector)'
        Has (Method $s 'private bool ReleaseSettledParentReaders(') 'if (!CollectorClosed(pair, collector, pair.Role == 2)) { settled = false; continue; } RetirePipeSlot(pair.Parent);'
    },'!KnownManagedSuccess(original.Start)','false'),
    @('l_reader_native_retirement_requires_actual_eof','G','private bool ActualCollectorClosureRecorded(',{
        param($s) Has (Method $s 'private bool ActualCollectorClosureRecorded(') 'lock (captureGate) { return isError ? stderrEof && stderrDone : stdoutEof && stdoutDone; }'
        Has (Method $s 'private void RetirePipeSlot(') '!ActualCollectorClosureRecorded(slot.Pair, collector, slot.Pair.Role == 2)'
    },'isError ? stderrEof && stderrDone : stdoutEof && stdoutDone','isError ? stderrDone : stdoutDone'),
    @('l_reader_native_retirement_requires_acquired_last_use','G','private bool ActualCollectorClosureRecorded(',{
        param($s) Has (Method $s 'private bool ActualCollectorClosureRecorded(') 'Interlocked.CompareExchange(ref original.LastUseSettled, 0, 0) != 1'
        Has (Method $s 'private void Capture(') 'Interlocked.Exchange(ref original.LastUseSettled, 1);'
        Has (Method $s 'private void RetirePipeSlot(') '!ActualCollectorClosureRecorded(slot.Pair, collector, slot.Pair.Role == 2)'
    },'Interlocked.CompareExchange(ref original.LastUseSettled, 0, 0) != 1','false'),
    @('l_reader_native_retirement_requires_recorded_join_success','G','private bool ActualCollectorClosureRecorded(',{
        param($s) Has (Method $s 'private bool ActualCollectorClosureRecorded(') '!KnownManagedSuccess(original.Join)'
        Has (Method $s 'private bool CollectorClosed(') 'bool joined = original.Worker.Join(0); original.Join.Succeeded = joined; original.Join.Returned = true;'
        Has (Method $s 'private void RetirePipeSlot(') '!ActualCollectorClosureRecorded(slot.Pair, collector, slot.Pair.Role == 2)'
    },'!KnownManagedSuccess(original.Join)','false'),
    @('l_containment_stays_p_first_then_w_natural_death','G','public bool PollContainment(',{
        param($s) $m=Method $s 'public bool PollContainment('
        Order $m @('if (!containmentStarted) { containmentStarted = true; containmentClock = Stopwatch.StartNew(); }','if (containmentClock.ElapsedMilliseconds >= ContainmentMilliseconds) throw new TimeoutException("Guardian containment observation expired; keep all original ownership.");','if (!producerContained) { if (!producer.PollContainment()) return false; producerContained = true; }','if (!NoChild()) { RequireOriginalCreation(); if (!RootBoundary(false)) return false; }','try { CloseClients(); }','try { StartCollectors(); }','try { readersReleased = ReleaseSettledParentReaders(); }')
        Has $s 'TerminateProcess(' 0;Has $s 'TerminateJobObject(' 0;Has $s 'CreateJobObject(' 0
        Has (Method $s 'private void AssertPipeCleanupAdmission(') '!containmentStarted || !producerContained || (!NoChild() && !RootBoundary(false))'
    },'if (!producer.PollContainment()) return false;',''),
    @('l_containment_cannot_skip_original_w_physical_death','G','public bool PollContainment(',{
        param($s) Has (Method $s 'public bool PollContainment(') 'if (!NoChild()) { RequireOriginalCreation(); if (!RootBoundary(false)) return false; }'
        Has (Method $s 'private bool RootBoundary(') 'uint wait = WaitForSingleObject(creationOutputs.process, 0);'
        Has (Method $s 'private bool RootBoundary(') 'return wait == WaitObject;'
    },'if (!RootBoundary(false)) return false;',''),
    @('l_partial_cleanup_continues_after_unrelated_fault','G','private void CloseClients(',{
        param($s) $m=Method $s 'private void CloseClients('
        Has $m 'RequireOriginalPair(pair); RetirePipeSlot(pair.Child); } catch (Exception failure) { pending.Add(failure); }'
        Order $m @('foreach (GuardianPipePair pair in new GuardianPipePair[] { inputPair, outputPair, errorPair })','RetirePipeSlot(inputPair.Parent);','if (pending.Count != 0) throw new AggregateException(pending.ToArray());')
        $c=Method $s 'public bool PollContainment(';foreach($call in @('CloseClients();','StartCollectors();','readersReleased = ReleaseSettledParentReaders();','ReleaseLaunchStorage();')){Has $c ('try { '+$call+' } catch (Exception failure) { pending.Add(failure); }')}
    },'RequireOriginalPair(pair); RetirePipeSlot(pair.Child); } catch (Exception failure) { pending.Add(failure); }','RequireOriginalPair(pair); RetirePipeSlot(pair.Child); } catch (Exception failure) { throw; }'),
    @('l_partial_parent_cleanup_attempts_other_reader_after_failure','G','private bool ReleaseSettledParentReaders(',{
        param($s) $m=Method $s 'private bool ReleaseSettledParentReaders('
        Has $m 'foreach (GuardianPipePair pair in new GuardianPipePair[] { outputPair, errorPair })'
        Has $m 'RetirePipeSlot(pair.Parent); } catch (Exception failure) { pending.Add(failure); }'
        Has $m 'if (pending.Count != 0) throw new AggregateException(pending.ToArray()); return settled;'
    },'RetirePipeSlot(pair.Parent); } catch (Exception failure) { pending.Add(failure); }','RetirePipeSlot(pair.Parent); } catch (Exception failure) { throw; }'),
    @('l_parent_retirement_is_per_reader_not_global','G','private bool ReleaseSettledParentReaders(',{
        param($s) $m=Method $s 'private bool ReleaseSettledParentReaders('
        Has $m 'CollectorClosed(pair, collector, pair.Role == 2)' 1;Has $m 'CollectorsClosed(' 0
        Has $m 'if (!CollectorClosed(pair, collector, pair.Role == 2)) { settled = false; continue; } RetirePipeSlot(pair.Parent);'
        Order (Method $s 'public bool PollContainment(') @('readersReleased = ReleaseSettledParentReaders();','if (pending.Count != 0) throw','if (!readersReleased || !CollectorsClosed()) return false;','AssertAllOriginalPipeResourcesReleased();')
    },'bool settled = true;','if (!CollectorsClosed()) return false; bool settled = true;'),
    @('l_unused_reader_disposition_needs_proven_no_child','G','private bool UnusedReaderDisposition(',{
        param($s) Has (Method $s 'private bool UnusedReaderDisposition(') 'return IsOriginalPair(pair) && pair.Role != 0 && NoChild() && containmentStarted && producerContained && collector == null && NoWrapperAttempt(pair.Parent);'
        Has (Method $s 'private void StartCollector(') 'if (UnusedReaderDisposition(pair, retained)) return;'
        Has (Method $s 'private void RetirePipeSlot(') '!UnusedReaderDisposition(slot.Pair, collector)'
    },'NoChild() && containmentStarted','containmentStarted'),
    @('l_partial_progress_never_publishes_global_release','G','private void AssertAllOriginalPipeResourcesReleased(',{
        param($s) $m=Method $s 'private void AssertAllOriginalPipeResourcesReleased('
        Has $m 'foreach (GuardianPipePair pair in new GuardianPipePair[] { inputPair, outputPair, errorPair })'
        Has $m 'if (!PipeSlotReleased(pair.Read) || !PipeSlotReleased(pair.Write)) throw'
        Has $m '!ActualCollectorClosureRecorded(pair, collector, pair.Role == 2) && !UnusedReaderDisposition(pair, collector)'
        Order (Method $s 'public bool PollContainment(') @('AssertAllOriginalPipeResourcesReleased();','closureProven = true;')
        $d=Method $s 'public void Dispose(';Has $d 'try { AssertAllOriginalPipeResourcesReleased(); } catch (Exception failure) { pending.Add(failure); }'
        Order $d @('if (pending.Count != 0) throw','resourcesClosed = true;','inputs.ReleaseGuardian(this);','producer.ReleaseGuardian(this);','disposed = true;')
    },'!PipeSlotReleased(pair.Read) || !PipeSlotReleased(pair.Write)','!PipeSlotReleased(pair.Read)'),
    @('l_no_child_does_not_classify_unknown_creation_as_absent','G','private bool NoChild(',{
        param($s) Has (Method $s 'private bool NoChild(') 'return !creationAttempted || (creationReturned && !creationSucceeded && creationOutputs.process == IntPtr.Zero && creationOutputs.thread == IntPtr.Zero && creationOutputs.pid == 0 && creationOutputs.tid == 0);'
        Has (Method $s 'private void AssertPipeCleanupAdmission(') '(!NoChild() && !RootBoundary(false))'
        Has (Method $s 'private bool UnusedReaderDisposition(') 'NoChild() && containmentStarted && producerContained'
    },'creationReturned && !creationSucceeded','!creationSucceeded'),
    @('l_first_close_checks_retained_wrapper_without_stream_getter','G','private void RetirePipeSlot(',{
        param($s) $m=Method $s 'private void RetirePipeSlot('
        Has $m 'slot.Wrapper.IsClosed || slot.Wrapper.IsInvalid || slot.Wrapper.DangerousGetHandle() != slot.Raw'
        Has $m 'slot.Stream.SafePipeHandle' 0;Has $m 'slot.Stream.SafeFileHandle' 0
        Order $m @('if (outcome.Attempted) {','slot.Wrapper.IsClosed','outcome.Original = slot.Raw; outcome.Attempted = true;','bool closed = CloseHandle(outcome.Original);')
    },'slot.Wrapper.IsClosed || slot.Wrapper.IsInvalid','slot.Wrapper.IsInvalid'),
    @('l_first_close_rejects_unexpected_invalid_wrapper','G','private void RetirePipeSlot(',{
        param($s) Has (Method $s 'private void RetirePipeSlot(') 'slot.Wrapper.IsClosed || slot.Wrapper.IsInvalid || slot.Wrapper.DangerousGetHandle() != slot.Raw'
    },'slot.Wrapper.IsInvalid || slot.Wrapper.DangerousGetHandle() != slot.Raw','slot.Wrapper.DangerousGetHandle() != slot.Raw'),
    @('l_first_close_rejects_wrapper_raw_value_mismatch','G','private void RetirePipeSlot(',{
        param($s) Has (Method $s 'private void RetirePipeSlot(') 'slot.Wrapper.IsClosed || slot.Wrapper.IsInvalid || slot.Wrapper.DangerousGetHandle() != slot.Raw'
    },'slot.Wrapper.DangerousGetHandle() != slot.Raw','false'),
    @('l_any_close_attempt_blocks_requery_and_reclose','G','private void RetirePipeSlot(',{
        param($s) $m=Method $s 'private void RetirePipeSlot('; $prior=Block $m 'if (outcome.Attempted)'
        Has $prior 'outcome.Original != slot.Raw || !outcome.Returned || !outcome.Succeeded';Has $prior 'CloseHandle(' 0
        Has $m 'bool closed = CloseHandle(outcome.Original);' 1
        Has (Method $s 'private void AssertLivePipeReader(') 'slot.Close.Attempted'
        Has (Method $s 'private bool CollectorClosed(') 'if (pair.Parent.Close.Attempted) throw'
    },'if (outcome.Attempted)','if (outcome.Attempted && outcome.Succeeded)'),
    @('l_native_bool_error_precede_wrapper_retirement','G','private void RetirePipeSlot(',{
        param($s) $m=Method $s 'private void RetirePipeSlot('
        Order $m @('outcome.Original = slot.Raw; outcome.Attempted = true;','bool closed = CloseHandle(outcome.Original);','slot.CloseError = Marshal.GetLastWin32Error(); outcome.Succeeded = closed; outcome.Returned = true;','if (!closed) throw','slot.Wrapper.SetHandleAsInvalid();','DisposeOriginalWrapper(slot.Stream, slot.Disposal);')
        Has $m 'slot.Raw = IntPtr.Zero;' 0
    },'outcome.Succeeded = closed;','outcome.Succeeded = true;'),
    @('l_native_invalidation_and_dispose_outcomes_are_distinct','G','private bool PipeSlotReleased(',{
        param($s) Has (Method $s 'private bool PipeSlotReleased(') 'slot.Close.Attempted && slot.Close.Original == slot.Raw && slot.Close.Returned && slot.Close.Succeeded && (slot.Wrapper == null || (KnownManagedSuccess(slot.Invalidation) && KnownManagedSuccess(slot.Disposal)))'
        Has (Method $s 'private static void DisposeOriginalWrapper(') 'if (original == null || outcome.Attempted) throw'
        Has (Method $s 'private void RetirePipeSlot(') 'if (!KnownManagedSuccess(slot.Invalidation)) throw'
    },'KnownManagedSuccess(slot.Invalidation) && KnownManagedSuccess(slot.Disposal)','KnownManagedSuccess(slot.Invalidation)'),
    @('l_postclose_collector_and_resource_observers_are_pure','G','private bool ActualCollectorClosureRecorded(',{
        param($s)
        foreach($sig in @('private bool ActualCollectorClosureRecorded(','private bool IsOriginalCollector(','private bool PipeSlotReleased(','private void RequireOriginalPipeSlot(','private void RequireSettledPipeWrapper(','private void RequireOriginalPair(','private bool IsOriginalPair(','private GuardianPipeSlot[] RetainedPipeSlots(','private bool PairHasNoAcquisition(','private static bool UntouchedSlot(','private static bool NoWrapperAttempt(','private bool UnusedReaderDisposition(','private bool NoChild(','private static bool KnownManagedSuccess(','private static bool Unattempted(','private static bool Valid(','private void AssertAllOriginalPipeResourcesReleased(')){
            Pure-RecordedObserver (Method $s $sig)
        }
        Has (Method $s 'private bool ActualCollectorClosureRecorded(') 'IsOriginalCollector(pair, original, isError)'
    },'lock (captureGate) { return isError ? stderrEof && stderrDone : stdoutEof && stdoutDone; }','original.Worker.Join(0); lock (captureGate) { return isError ? stderrEof && stderrDone : stdoutEof && stdoutDone; }'),
    @('l_pure_complete_proof_immediately_precedes_inputs_release','G','public void Dispose(',{
        param($s) Has (Method $s 'public void Dispose(') 'resourcesClosed = true; AssertAllOriginalPipeResourcesReleased(); if (inputsRegistered && !inputsReleased) { inputs.ReleaseGuardian(this); inputsReleased = true; }'
        $release=Method $i 'internal void ReleaseGuardian(';Has $release '!Object.ReferenceEquals(guardianBorrower, guardian)';Has $release 'guardianBorrower = null;'
        Has $release 'guardian.' 0
    },'resourcesClosed = true; AssertAllOriginalPipeResourcesReleased();','resourcesClosed = true;'),
    @('l_producer_backlink_requires_complete_local_pipe_proof','G','internal void AssertProducerReleaseAllowed(',{
        param($s) Has (Method $p 'internal void ReleaseGuardian(') 'candidate.AssertProducerReleaseAllowed(this); guardian = null;'
        $m=Method $s 'internal void AssertProducerReleaseAllowed(';Has $m '!Object.ReferenceEquals(producer, candidate) || !resourcesClosed || !inputsReleased'
        Has $m 'AssertAllOriginalPipeResourcesReleased();';Has $m '.Join(' 0;Has $m 'Enter(' 0
        Has $m '(transferSourceCapability != null && !transferSourceCapability.IsClosed)'
    },'AssertAllOriginalPipeResourcesReleased();',''),
    @('l_preload_backlink_requires_complete_local_pipe_proof','G','internal void AssertPreloadReleaseAllowed(',{
        param($s) Has (Method $l 'public void Dispose(') 'if (guardian != null) guardian.AssertPreloadReleaseAllowed(this);'
        $m=Method $s 'internal void AssertPreloadReleaseAllowed(';Has $m '!Object.ReferenceEquals(preload, candidate) || Interlocked.CompareExchange(ref operation, 0, 0) != 0 || !disposed || !closureProven || (!producerContained && !normalTerminalProven) || !resourcesClosed || !inputsReleased || (producerRegistered && !producerReleased)'
        Has $m 'AssertAllOriginalPipeResourcesReleased();';Has $m '.Join(' 0;Has $m 'Enter(' 0
    },'AssertAllOriginalPipeResourcesReleased();',''),
    @('l_postretirement_output_recheck_keeps_original_native_authority','G','private bool CollectorClosed(',{
        param($s) $m=Method $s 'internal void AssertCoordinatorOutputBoundary('
        Has $m 'CompilerResult actualProducer = producer.PollNormalTerminal(this);'
        Has $m 'producer.OriginalNormalExitFileTime(this, actualProducer) != observed.ProducerExitFileTime'
        Has $m 'if (!RootBoundary(false) || !CollectorsClosed()) throw'
        Has $m 'GetProcessTimes(creationOutputs.process, out created, out exited, out kernel, out user)'
        Has $m 'created != birth || exited != observed.GuardianExitFileTime || code != 0'
        Has (Method $s 'private bool CollectorsClosed(') 'CollectorClosed(outputPair, outputCollector, false)'
        Has (Method $s 'private bool CollectorsClosed(') 'CollectorClosed(errorPair, errorCollector, true)'
        Has (Method $s 'private bool CollectorClosed(') 'if (ActualCollectorClosureRecorded(pair, original, isError)) return true;'
        Pure-RecordedObserver (Method $s 'private bool ActualCollectorClosureRecorded(')
    },'if (ActualCollectorClosureRecorded(pair, original, isError)) return true;','if (ActualCollectorClosureRecorded(pair, original, isError)) { AssertLivePipeReader(pair.Parent); return true; }'),
    @('l_both_execution_qualification_gates_stay_unconditional','G','private static void DemandQualifiedLauncherTerminal(',{
        param($s) Has (Method $s 'private static void DemandQualifiedFirstProducerContinuation(') '{ throw new InvalidOperationException("First P continuation remains closed until independent full-protocol native qualification."); }'
        Has (Method $s 'private static void DemandQualifiedLauncherTerminal(') '{ throw new InvalidOperationException("Original P/W normal closure and output transport require independent full-protocol qualification."); }'
        Has (Method $s 'public void ContinueProducer(') 'DemandQualifiedFirstProducerContinuation();'
        Has (Method $s 'internal BuildTerminalClosure PollNormalTerminal(') 'DemandQualifiedLauncherTerminal();'
    },'throw new InvalidOperationException("Original P/W normal closure and output transport require independent full-protocol qualification.");','return;'),
    @('l_running_no_job_birth_abi_and_original_clock_bounds_are_unchanged','G','public void StartRunning(',{
        param($s) Has $s 'public const string Abi = "stratus-owner-watch-launch-v8";'
        Has $s 'ReadyMilliseconds = 60000, ContainmentMilliseconds = 30000, CaptureLimit = 4096'
        Has (Method $s 'public void StartRunning(') 'bool created = CreateProcess(executable, arguments, IntPtr.Zero, IntPtr.Zero, true, 0x08080400, environment.Value, directory, ref startup, out creationOutputs);'
        Has (Method $s 'public void StartRunning(') 'if (expectedReady.Length > 352) throw'
        Has (Method $s 'private static void NoJob(') 'Check(IsProcessInJob(handle, IntPtr.Zero, out member),'
        Has (Method $s 'private static void NoJob(') 'if (member) throw'
        Has (Method $s 'private void Admission(') 'startupDeadline.AssertGuardianActive(this, sourceCapability)'
        Has (Method $s 'public bool PollContainment(') 'containmentClock.ElapsedMilliseconds >= ContainmentMilliseconds' 2
    },'true, 0x08080400,','true, 0x08080404,'),
    @('l_guardian_executable_remains_original_inputs_borrow','G','private void HeldBoundary(',{
        param($s) Has (Method $s 'private void HeldBoundary(') 'inputs.AssertHeld(); if (heldExecutable == null || !Object.ReferenceEquals(heldExecutable, inputs.BorrowExactForGuardian(this, executable, expectedHash))) throw'
        Has (Method $i 'internal FileStream BorrowExactForGuardian(') '!Object.ReferenceEquals(guardianBorrower, guardian)'
        Has (Method $i 'internal FileStream BorrowExactForGuardian(') 'String.Equals(item.Spec.Path, path, StringComparison.Ordinal) && item.Spec.Sha256 == sha256'
        Has (Method $s 'public void Dispose(') 'heldExecutable.Dispose(' 0
        Has $s 'new FileStream(' 0
    },'inputs.AssertHeld();',''),
    @('l_actual_start_prepares_retained_stdio_before_allowlist_and_native_birth','G','public void StartRunning(',{
        param($s) Order (Method $s 'public void StartRunning(') @('PrepareStdioPairs();','IntPtr[] allowed = new IntPtr[] {','creationAttempted = true;','bool created = CreateProcess(')
    },'PrepareStdioPairs();',''),
    @('l_actual_stdout_collector_start_cannot_be_omitted','G','private void StartCollectors(',{
        param($s) Has (Method $s 'private void StartCollectors(') 'try { StartCollector(outputPair, ref outputCollector, stdout, false); } catch (Exception failure) { pending.Add(failure); }'
        Has (Method $s 'public void StartRunning(') 'try { StartCollectors(); } catch (Exception failure) { stdioFailures.Add(failure); }'
    },'StartCollector(outputPair, ref outputCollector, stdout, false);',''),
    @('l_actual_stderr_collector_start_cannot_be_omitted','G','private void StartCollectors(',{
        param($s) Has (Method $s 'private void StartCollectors(') 'try { StartCollector(errorPair, ref errorCollector, stderr, true); } catch (Exception failure) { pending.Add(failure); }'
        Has (Method $s 'public void StartRunning(') 'try { StartCollectors(); } catch (Exception failure) { stdioFailures.Add(failure); }'
    },'StartCollector(errorPair, ref errorCollector, stderr, true);',''),
    @('l_stdout_start_failure_cannot_shortcircuit_stderr_start_attempt','G','private void StartCollectors(',{
        param($s) Order (Method $s 'private void StartCollectors(') @('List<Exception> pending = new List<Exception>();','try { StartCollector(outputPair, ref outputCollector, stdout, false); } catch (Exception failure) { pending.Add(failure); }','try { StartCollector(errorPair, ref errorCollector, stderr, true); } catch (Exception failure) { pending.Add(failure); }','if (pending.Count != 0) throw new AggregateException(pending.ToArray());')
    },'try { StartCollector(outputPair, ref outputCollector, stdout, false); } catch (Exception failure) { pending.Add(failure); }','try { StartCollector(outputPair, ref outputCollector, stdout, false); } catch (Exception failure) { throw; }'),
    @('l_known_managed_success_requires_original_attempt','G','private static bool KnownManagedSuccess(',{
        param($s) Has (Method $s 'private static bool KnownManagedSuccess(') '{ return outcome.Attempted && outcome.Returned && outcome.Succeeded; }'
    },'outcome.Attempted && outcome.Returned','outcome.Returned'),
    @('l_known_managed_success_requires_recorded_return','G','private static bool KnownManagedSuccess(',{
        param($s) Has (Method $s 'private static bool KnownManagedSuccess(') '{ return outcome.Attempted && outcome.Returned && outcome.Succeeded; }'
    },'outcome.Returned && outcome.Succeeded','outcome.Succeeded'),
    @('l_known_managed_success_requires_actual_success','G','private static bool KnownManagedSuccess(',{
        param($s) Has (Method $s 'private static bool KnownManagedSuccess(') '{ return outcome.Attempted && outcome.Returned && outcome.Succeeded; }'
    },'outcome.Returned && outcome.Succeeded','outcome.Returned'),
    @('l_unattempted_managed_outcome_rejects_attempted_work','G','private static bool Unattempted(',{
        param($s) Has (Method $s 'private static bool Unattempted(') '{ return !outcome.Attempted && !outcome.Returned && !outcome.Succeeded; }'
    },'!outcome.Attempted && !outcome.Returned','!outcome.Returned'),
    @('l_unattempted_managed_outcome_rejects_returned_work','G','private static bool Unattempted(',{
        param($s) Has (Method $s 'private static bool Unattempted(') '{ return !outcome.Attempted && !outcome.Returned && !outcome.Succeeded; }'
    },'!outcome.Returned && !outcome.Succeeded','!outcome.Succeeded'),
    @('l_unattempted_managed_outcome_rejects_successful_work','G','private static bool Unattempted(',{
        param($s) Has (Method $s 'private static bool Unattempted(') '{ return !outcome.Attempted && !outcome.Returned && !outcome.Succeeded; }'
    },'!outcome.Returned && !outcome.Succeeded','!outcome.Returned')
)
$expectedPairs=68;$expected=136
Assert-GuardianPipe ($rows.Count -eq $expectedPairs -and $expected -eq 2*$expectedPairs) 'Fixed case inventory differs.'
$names=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
foreach($row in $rows){
    Assert-GuardianPipe ($row.Count -eq 6 -and $row[0] -cmatch '^[a-z][a-z0-9_]{1,100}$') 'Fixed row shape or ID differs.'
    Assert-GuardianPipe ($names.Add([string]$row[0])) ('Duplicate row ID: '+$row[0])
    Assert-GuardianPipe ($row[1] -ceq 'G' -and $row[3] -is [scriptblock]) 'Fixed literal subject/predicate table differs.'
}
$executed=0;$results=[Collections.Generic.List[object]]::new()
foreach($row in $rows){
    $selected=$subjects[$row[1]];$check=$row[3]
    & $check $selected
    $executed++;$results.Add([pscustomobject]@{name=$row[0];variant='original_source';passed=$true})
    $mutated=Mutate-Method $selected $row[2] $row[4] $row[5]
    Assert-GuardianPipe (($mutated -join ' ') -cne ($selected -join ' ')) ('Mutation made no change: '+$row[0])
    $rejected=$false
    try{& $check $mutated}catch{
        Assert-GuardianPipe ($_.Exception.Message.StartsWith('Guardian pipe source contract:',[StringComparison]::Ordinal)) ('Unexpected negative-control exception: '+$row[0])
        $rejected=$true
    }
    Assert-GuardianPipe $rejected ('Weakening mutation survived: '+$row[0])
    $executed++;$results.Add([pscustomobject]@{name=$row[0];variant='mutated_source_rejected';passed=$true})
}
Assert-GuardianPipe ($executed -eq $expected -and $results.Count -eq $expected) 'Fixed cases did not complete.'
$before=[Collections.Generic.List[object]]::new();$after=[Collections.Generic.List[object]]::new()
foreach($name in $sources.Keys){
    $old=$sources[$name];$latest=Read-GuardianPipeSource $old.path
    Assert-GuardianPipe ($latest.sha256 -ceq $old.sha256 -and $latest.bytes -eq $old.bytes) ('Subject changed during inspection: '+$name)
    $before.Add([pscustomobject]@{name=$name;path=$old.path;bytes=$old.bytes;sha256=$old.sha256})
    $after.Add([pscustomobject]@{name=$name;path=$latest.path;bytes=$latest.bytes;sha256=$latest.sha256})
}
$testAfter=Read-GuardianPipeSource $PSCommandPath
Assert-GuardianPipe ($testAfter.sha256 -ceq $testBefore.sha256 -and $testAfter.bytes -eq $testBefore.bytes) 'Test changed during inspection.'
[pscustomobject]@{
    format='stratus-guardian-pipe-ownership-source-contract-tests-v1'
    expected_pairs=$expectedPairs;expected=$expected;executed=$executed;passed=$executed;failed=0;skipped=0
    powershell_version=$PSVersionTable.PSVersion.ToString();powershell_edition=$PSVersionTable.PSEdition
    source_hashes_before=$before.ToArray();source_hashes_after=$after.ToArray()
    test_sha256_before=$testBefore.sha256;test_sha256_after=$testAfter.sha256;source_and_test_hashes_unchanged=$true
    fixed_unique_row_ids=$true;source_tokens_and_directed_inert_mutations_only=$true
    strict_utf8_bounded_source_reads=$true;lexical_harness_preflight_passed=$true
    target_ast_parsed=$false;target_imported=$false;target_evaluated=$false;reflection_used=$false
    target_compiled=$false;target_loaded=$false;target_lifecycle_executed=$false;native_qualified=$false
    filesystem_fixture_created=$false;network_or_operations_invoked=$false;csharp_semantics_proven=$false
    transitive_purity_proven=$false;physical_ownership_proven=$false;writer_isolation_qualified=$false
    independent_launcher_watchdog_configured=$false;actual_terminal_protocol_executed=$false
    output_capture_executed=$false;accepted_output_sealed=$false;full_protocol_accepted=$false
    full_build_accepted=$false;import_permitted=$false;publication_permitted=$false;production_authorized=$false
    cases=$results.ToArray()
}|ConvertTo-Json -Depth 6 -Compress
