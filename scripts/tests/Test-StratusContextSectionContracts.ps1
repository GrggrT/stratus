[CmdletBinding()]
param([string] $ContextPath,[string] $ProducerPath,[string] $BootstrapPath,[string] $GuardianPath)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if(-not $ContextPath){$ContextPath=Join-Path $PSScriptRoot '..\Stratus.RuntimeBuild.ContextSection.cs'}
if(-not $ProducerPath){$ProducerPath=Join-Path $PSScriptRoot '..\Stratus.RuntimeBuild.Producer.cs'}
if(-not $BootstrapPath){$BootstrapPath=Join-Path $PSScriptRoot '..\Stratus.RuntimeBuild.Bootstrap.cs'}
if(-not $GuardianPath){$GuardianPath=Join-Path $PSScriptRoot '..\Stratus.RuntimeBuild.GuardianLaunch.cs'}
# Read/tokenize subject source only. Never compile, load or evaluate C# or call
# subject members. Mutations are inert token arrays checked by these predicates.
# These scoped shapes are not C#/Windows semantics, approval-origin proof, a
# native ownership/race qualification, or authority to resume the producer.
function Assert-Context {param([bool] $Okay,[string] $Message) if(-not $Okay){throw ('Context section source contract: '+$Message)}}
function Read-ContextSource {
    param([string]$Path)
    $full=[IO.Path]::GetFullPath($Path)
    # Bound allocation and every delivered read; a FileInfo precheck alone
    # cannot stop ReadAllBytes from allocating after concurrent file growth.
    $stream=[IO.FileStream]::new($full,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{
        $length=$stream.Length
        Assert-Context ($length -gt 0 -and $length -le 1048576) 'Inspection file exceeds one MiB.'
        $raw=[byte[]]::new([int]$length);$offset=0
        while($offset -lt $raw.Length){
            $count=$stream.Read($raw,$offset,$raw.Length-$offset)
            Assert-Context ($count -gt 0) 'Inspection file ended before its bounded length.'
            $offset+=$count
        }
        Assert-Context ($stream.ReadByte() -eq -1 -and $stream.Length -eq $length) 'File length changed while reading.'
    }finally{$stream.Dispose()}
    $text=[Text.UTF8Encoding]::new($false,$true).GetString($raw)
    $hash=[Security.Cryptography.SHA256]::Create()
    try{$digest=[BitConverter]::ToString($hash.ComputeHash($raw)).Replace('-','').ToLowerInvariant()}finally{$hash.Dispose()}
    return [pscustomobject]@{path=$Path;text=$text;sha256=$digest}
}
function Tokens {
    param([string] $Text)
    Assert-Context ($null -ne $Text -and $Text.Length -le 1048576) 'Token input exceeds its fixed character bound.'
    $pattern=@'
//[^\r\n]* | /\*.*?\*/ | @"(?:""|[^"])*" | "(?:\\.|[^"\\])*" | '(?:\\.|[^'\\])*'
| [A-Za-z_][A-Za-z_0-9]* | [0-9]+ | == | != | >= | <= | \+\+ | -- | && | \|\| | => | [^\s]
'@
    $result=[Collections.Generic.List[string]]::new()
    $options=[Text.RegularExpressions.RegexOptions]::Singleline -bor [Text.RegularExpressions.RegexOptions]::IgnorePatternWhitespace
    foreach($m in [regex]::Matches($Text,$pattern,$options,[TimeSpan]::FromSeconds(5))){
        if(-not $m.Value.StartsWith('//') -and -not $m.Value.StartsWith('/*')){Assert-Context ($result.Count -lt 262144) 'Token inspection bound exceeded.';$result.Add($m.Value)}
    }
    Assert-Context ($result.Count -le 262144) 'Token inspection bound exceeded.'
    return ,$result.ToArray()
}
function Find {
    param([string[]] $Source,[string] $Sequence)
    $wanted=Tokens $Sequence;Assert-Context ($wanted.Count -gt 0) 'Empty predicate.'
    $found=[Collections.Generic.List[int]]::new()
    for($i=0;$i -le $Source.Count-$wanted.Count;$i++){
        $same=$true
        for($j=0;$j -lt $wanted.Count;$j++){if($Source[$i+$j] -cne $wanted[$j]){$same=$false;break}}
        if($same){$found.Add($i)}
    }
    return ,$found.ToArray()
}
function Has {param([string[]] $Source,[string] $Sequence,[int] $Count=1) Assert-Context ((Find $Source $Sequence).Count -eq $Count) ('Sequence differs: '+$Sequence)}
function Order {
    param([string[]] $Source,[string[]] $Sequences)
    $last=-1
    foreach($sequence in $Sequences){$found=Find $Source $sequence;Assert-Context ($found.Count -eq 1 -and $found[0] -gt $last) ('Order differs: '+$sequence);$last=$found[0]}
}
function Block {
    param([string[]] $Source,[string] $Signature,[switch] $Direct)
    $matches=Find $Source $Signature
    if($Direct){
        $directMatches=[Collections.Generic.List[int]]::new();$depth=0
        for($i=0;$i -lt $Source.Count;$i++){
            if($depth -eq 1 -and $i -in $matches){$directMatches.Add($i)}
            if($Source[$i] -ceq '{'){$depth++}elseif($Source[$i] -ceq '}'){$depth--}
        }
        $matches=$directMatches.ToArray()
    }
    Assert-Context ($matches.Count -eq 1) ('Missing/ambiguous scoped block: '+$Signature)
    $open=$matches[0]
    while($open -lt $Source.Count -and $Source[$open] -cne '{'){$open++}
    Assert-Context ($open -lt $Source.Count) 'Opening brace missing.'
    $depth=0
    for($i=$open;$i -lt $Source.Count;$i++){
        if($Source[$i] -ceq '{'){$depth++}elseif($Source[$i] -ceq '}'){$depth--}
        if($depth -eq 0){return ,([string[]]$Source[$matches[0]..$i])}
    }
    throw 'Context section source contract: Unbalanced source block.'
}
function Method {param([string[]] $Class,[string] $Signature) return ,(Block $Class $Signature -Direct)}
function Replace-Tokens {
    param([string[]] $Source,[string] $Needle,[string] $Replacement)
    $at=Find $Source $Needle;Assert-Context ($at.Count -eq 1) ('Mutation anchor differs: '+$Needle)
    $old=Tokens $Needle;$new=Tokens $Replacement;$result=[Collections.Generic.List[string]]::new()
    for($i=0;$i -lt $at[0];$i++){$result.Add($Source[$i])}
    foreach($token in $new){$result.Add($token)}
    for($i=$at[0]+$old.Count;$i -lt $Source.Count;$i++){$result.Add($Source[$i])}
    return ,$result.ToArray()
}
function Move-Tokens {
    param([string[]] $Source,[string] $Needle,[string] $Before)
    $without=Replace-Tokens $Source $Needle '/* selected original call moved, not duplicated */'
    return ,(Replace-Tokens $without $Before ($Needle+' '+$Before))
}
$context=Read-ContextSource $ContextPath;$producer=Read-ContextSource $ProducerPath
$bootstrap=Read-ContextSource $BootstrapPath;$guardian=Read-ContextSource $GuardianPath
$testSource=Read-ContextSource $PSCommandPath
$c=Block (Tokens $context.text) 'internal sealed class ProducerContextSection'
$delivery=Block (Tokens $context.text) 'internal sealed class ContextSectionDelivery'
$p=Block (Tokens $producer.text) 'public sealed class DisposableProducerLaunch : IDisposable'
$b=Block (Tokens $bootstrap.text) 'public sealed class CompilerCommand : IDisposable'
$g=Block (Tokens $guardian.text) 'public sealed class GuardianLaunch : IDisposable'
$observation=Block (Tokens $guardian.text) 'public sealed class GuardianReadyObservation'
$blocks=@{
    context=$c;delivery=$delivery;producer=$p;compiler=$b;observation=$observation
    ctor=(Method $c 'internal ProducerContextSection(')
    owner=(Method $c 'private void RequireOwner(')
    acquired=(Method $c 'private static void RequireAcquired(')
    usable=(Method $c 'private static void RequireUsable(')
    distinct=(Method $c 'private void RequireDistinct(')
    flags=(Method $c 'private static void AssertFlags(')
    writable=(Method $c 'private void RequireWritableRetired(')
    closed=(Method $c 'private static void RequireClosed(')
    producerretired=(Method $c 'private void RequireProducerRetired(')
    opened=(Method $c 'private void RequireOpened(')
    duplicate=(Method $c 'private static void DuplicateReadOnly(')
    unmap=(Method $c 'private void UnmapWritable(')
    close=(Method $c 'private static void CloseOriginal(')
    open=(Method $c 'internal void OpenForProducer(')
    prepared=(Method $c 'internal void AssertPreparedOwner(')
    pborrow=(Method $c 'internal ContextSectionDelivery BorrowProducerDelivery(')
    ppending=(Method $c 'internal void AssertProducerDeliveryPending(')
    wbinding=(Method $c 'private void RequireGuardian(')
    wborrow=(Method $c 'internal ContextSectionDelivery BorrowGuardianDelivery(')
    wpending=(Method $c 'internal void AssertGuardianDeliveryPending(')
    inheritance=(Method $c 'private static void RetireInheritance(')
    pretire=(Method $c 'internal void RetireProducerDelivery(')
    wretire=(Method $c 'internal void RetireGuardianDelivery(')
    both=(Method $c 'internal void AssertBothDeliveriesRetired(')
    cleanupacquisition=(Method $c 'private static void RequireCleanupAcquisition(')
    cleanup=(Method $c 'internal void DisposeAfterContainment(')
    producerctor=(Method $p 'private DisposableProducerLaunch(')
    producerstart=(Method $p 'public void StartSuspended(')
    producerdispose=(Method $p 'public void Dispose(')
    factory=(Method $b 'internal static CompilerCommand PrepareOwnedWithDeadline(')
    compilerstart=(Method $b 'private void StartSuspendedOriginal(')
    guardianstart=(Method $g 'public void StartRunning(')
    guardianready=(Method $g 'private GuardianReadyObservation ReadReadyCore(')
    guardianadmission=(Method $g 'private void Admission(')
    guardianallowlist=(Method $g 'private static void AssertAllowlist(')
}
Has (Method $b 'public void StartSuspended(') 'StartSuspendedOriginal(null);'
$rows=@(
    @('private_original_owner_no_public_adoption_or_named_lookup','context',{
        param($s) Has $s 'private readonly DisposableProducerLaunch originalOwner;';Has $s 'private readonly byte[] originalBytes;';Has $s 'public' 0;Has $s 'OpenFileMapping' 0
    },'private readonly DisposableProducerLaunch originalOwner;','private DisposableProducerLaunch originalOwner;'),
    @('managed_constructor_exact_positive_eight_mib_bound','ctor',{
        param($s) Has $s 'if (expectedBytes < 1 || expectedBytes > 8388608 || raw.LongLength != expectedBytes)';Has $s 'expectedLength = checked((int)expectedBytes);';Has $s 'CreateFileMapping' 0;Has $s 'MapViewOfFile' 0;Has $s 'DuplicateHandle' 0
    },'expectedBytes > 8388608','expectedBytes > 16777216'),
    @('own_defensive_byte_copy_is_the_hashed_pin','ctor',{
        param($s) Order $s @('BuildInputValidation.Hash(expectedSha);','originalBytes = (byte[])raw.Clone();','hash.ComputeHash(originalBytes)','if (actual != expectedSha256)')
    },'hash.ComputeHash(originalBytes)','hash.ComputeHash(raw)'),
    @('delivery_handle_length_hash_are_internal_immutable','delivery',{
        param($s) Has $s 'internal readonly IntPtr Handle;';Has $s 'internal readonly int Bytes;';Has $s 'internal readonly string Sha256;';Has $s 'Handle = handle; Bytes = bytes; Sha256 = sha256;';Has $s 'public' 0
    },'internal readonly int Bytes;','internal int Bytes;'),
    @('every_owner_binding_requires_same_original_and_backlink','owner',{
        param($s) Has $s 'candidate == null || !Object.ReferenceEquals(originalOwner, candidate) || !candidate.OwnsContextSection(this)'
    },'!Object.ReferenceEquals(originalOwner, candidate)','false'),
    @('handle_acquisition_requires_all_recorded_positive_facts','acquired',{
        param($s) Has $s '!outcome.AcquireAttempted || !outcome.AcquireReturned || !outcome.AcquireSucceeded || !Valid(outcome.Raw)'
    },'!outcome.AcquireReturned ||',''),
    @('unknown_or_successful_close_embargoes_handle_use','usable',{
        param($s) Order $s @('RequireAcquired(outcome);','if (outcome.CloseSucceeded || (outcome.CloseAttempted && !outcome.CloseReturned))')
    },'outcome.CloseSucceeded ||',''),
    @('all_three_original_handle_roles_are_distinct','distinct',{
        param($s) Has $s 'mapping.AcquireSucceeded && producerCopy.AcquireSucceeded && mapping.Raw == producerCopy.Raw';Has $s 'mapping.AcquireSucceeded && guardianCopy.AcquireSucceeded && mapping.Raw == guardianCopy.Raw';Has $s 'producerCopy.AcquireSucceeded && guardianCopy.AcquireSucceeded && producerCopy.Raw == guardianCopy.Raw'
    },'producerCopy.Raw == guardianCopy.Raw','false'),
    @('fresh_native_flags_require_known_usable_handle','flags',{
        param($s) Order $s @('RequireUsable(outcome);','GetHandleInformation(outcome.Raw, out flags)','if ((flags & 3) != expected)')
    },'RequireUsable(outcome);',''),
    @('writable_view_and_full_access_mapping_must_both_be_retired','writable',{
        param($s) Has $s '!viewAttempted || !viewReturned || !viewSucceeded || !Valid(writableView) || !unmapAttempted || !unmapReturned || !unmapSucceeded';Has $s 'RequireClosed(mapping);'
    },'RequireClosed(mapping);',''),
    @('opened_admission_requires_readonly_copies_and_writer_retirement','opened',{
        param($s) Has $s 'failed || disposed || !openAttempted || !opened';Has $s 'RequireAcquired(producerCopy); RequireAcquired(guardianCopy); RequireDistinct(); RequireWritableRetired();'
    },'RequireWritableRetired();',''),
    @('readonly_duplicate_uses_exact_access_four_and_recorded_outcome','duplicate',{
        param($s) Order $s @('RequireUsable(source);','if (target.AcquireAttempted)','target.AcquireAttempted = true;','DuplicateHandle(GetCurrentProcess(), source.Raw, GetCurrentProcess(), out target.Raw, 4, true, 0)','target.AcquireSucceeded = copied; target.AcquireReturned = true;','if (!copied)','RequireAcquired(target); AssertFlags(target, 1);')
    },'out target.Raw, 4, true, 0','out target.Raw, 0, true, 2'),
    @('unknown_unmap_never_retries_and_success_never_reuses_pointer','unmap',{
        param($s) Order $s @('if (unmapAttempted && !unmapReturned)','if (unmapReturned && unmapSucceeded) return;','if (!viewAttempted || !viewReturned || !viewSucceeded || !Valid(writableView))','UnmapViewOfFile(writableView)')
    },'if (unmapReturned && unmapSucceeded) return;',''),
    @('unmap_attempt_return_and_result_remain_retained','unmap',{
        param($s) Order $s @('unmapAttempted = true; unmapReturned = false; unmapSucceeded = false;','bool released = UnmapViewOfFile(writableView);','unmapSucceeded = released; unmapReturned = true;','if (!released)')
    },'unmapSucceeded = released; unmapReturned = true;','unmapReturned = true;'),
    @('unknown_close_never_retries_and_known_close_is_idempotent','close',{
        param($s) Order $s @('if (outcome.CloseAttempted && !outcome.CloseReturned)','if (outcome.CloseReturned && outcome.CloseSucceeded) return;','RequireUsable(outcome);','CloseHandle(outcome.Raw)')
    },'if (outcome.CloseReturned && outcome.CloseSucceeded) return;',''),
    @('close_attempt_return_and_result_remain_retained','close',{
        param($s) Order $s @('outcome.CloseAttempted = true; outcome.CloseReturned = false; outcome.CloseSucceeded = false;','bool released = CloseHandle(outcome.Raw);','outcome.CloseSucceeded = released; outcome.CloseReturned = true;','if (!released)')
    },'outcome.CloseAttempted = true;',''),
    @('unnamed_pagefile_mapping_is_retained_before_any_view','open',{
        param($s) Order $s @('mapping.AcquireAttempted = true;','mapping.Raw = CreateFileMapping(new IntPtr(-1), IntPtr.Zero, 4, 0, checked((uint)expectedLength), null);','mapping.AcquireSucceeded = mapping.Raw != IntPtr.Zero; mapping.AcquireReturned = true;','if (!mapping.AcquireSucceeded)','RequireAcquired(mapping); AssertFlags(mapping, 0);','viewAttempted = true;')
    },'checked((uint)expectedLength), null','checked((uint)expectedLength), "named-context"'),
    @('exact_writable_view_uses_owned_bytes_only_after_known_result','open',{
        param($s) Order $s @('viewAttempted = true;','writableView = MapViewOfFile(mapping.Raw, 2, 0, 0, new UIntPtr(checked((uint)expectedLength)));','viewSucceeded = writableView != IntPtr.Zero; viewReturned = true;','if (!viewSucceeded)','if (!Valid(writableView))','Marshal.Copy(originalBytes, 0, writableView, expectedLength);')
    },'viewSucceeded = writableView != IntPtr.Zero; viewReturned = true;','viewSucceeded = writableView != IntPtr.Zero;'),
    @('both_readonly_copies_and_both_writer_releases_precede_opened','open',{
        param($s) Order $s @('Marshal.Copy(originalBytes, 0, writableView, expectedLength);','DuplicateReadOnly(mapping, producerCopy);','DuplicateReadOnly(mapping, guardianCopy);','UnmapWritable();','CloseOriginal(mapping);','RequireWritableRetired(); AssertFlags(producerCopy, 1); AssertFlags(guardianCopy, 1);','opened = true;')
    },'opened = true;','UnmapWritable();','move'),
    @('open_is_one_use_locked_and_failures_are_sticky','open',{
        param($s) Has $s 'lock (gate)';Order $s @('RequireOwner(candidate);','if (failed || disposed || openAttempted)','openAttempted = true;','mapping.AcquireAttempted = true;');Has $s 'catch (Exception) { failed = true; throw; }'
    },'catch (Exception) { failed = true; throw; }','catch (Exception) { throw; }'),
    @('producer_borrow_requires_retired_writer_and_one_use_live_flags','pborrow',{
        param($s) Order $s @('RequireOwner(candidate); RequireOpened();','if (producerBorrowed || producerRetirementAttempted)','AssertFlags(producerCopy, 1); producerBorrowed = true;','return new ContextSectionDelivery(producerCopy.Raw, expectedLength, expectedSha256);')
    },'RequireOpened();',''),
    @('producer_final_pending_check_rejects_retirement_and_refreshes_flags','ppending',{
        param($s) Order $s @('RequireOwner(candidate); RequireOpened();','if (!producerBorrowed || producerRetirementAttempted || producerRetired)','AssertFlags(producerCopy, 1);')
    },'producerRetirementAttempted ||',''),
    @('guardian_binding_joins_exact_registered_owner_capability','wbinding',{
        param($s) Has $s 'candidate == null || capability == null || !originalOwner.OwnsGuardian(candidate, capability)';Has $s 'bound && (!Object.ReferenceEquals(originalGuardian, candidate) || !Object.ReferenceEquals(originalGuardianCapability, capability))'
    },'!Object.ReferenceEquals(originalGuardianCapability, capability)','false'),
    @('guardian_one_use_borrow_waits_for_producer_delivery_retirement','wborrow',{
        param($s) Order $s @('RequireGuardian(candidate, capability, originalGuardian != null); RequireOpened(); RequireProducerRetired();','if (guardianBorrowed || guardianRetirementAttempted || originalGuardian != null)','originalGuardian = candidate; originalGuardianCapability = capability;','AssertFlags(guardianCopy, 1); guardianBorrowed = true;','return new ContextSectionDelivery(guardianCopy.Raw, expectedLength, expectedSha256);')
    },'RequireProducerRetired();',''),
    @('guardian_final_pending_check_refreshes_original_delivery_flags','wpending',{
        param($s) Order $s @('RequireGuardian(candidate, capability, true); RequireOpened(); RequireProducerRetired();','if (!guardianBorrowed || guardianRetirementAttempted || guardianRetired)','AssertFlags(guardianCopy, 1);')
    },'AssertFlags(guardianCopy, 1);',''),
    @('inheritance_retirement_records_setter_then_verifies_native_flags','inheritance',{
        param($s) Order $s @('RequireUsable(outcome);','if (outcome.InheritAttempted)','outcome.InheritAttempted = true;','SetHandleInformation(outcome.Raw, 1, 0)','outcome.InheritSucceeded = changed; outcome.InheritReturned = true;','if (!changed)','AssertFlags(outcome, 0); outcome.InheritVerified = true;')
    },'AssertFlags(outcome, 0);',''),
    @('producer_retirement_closes_only_its_original_delivery_copy','pretire',{
        param($s) Order $s @('RequireOwner(candidate); RequireOpened();','if (!producerBorrowed || producerRetirementAttempted)','producerRetirementAttempted = true;','RetireInheritance(producerCopy); CloseOriginal(producerCopy); producerRetired = true;');Has $s 'guardianCopy' 0
    },'CloseOriginal(producerCopy);','CloseOriginal(guardianCopy);'),
    @('guardian_retirement_closes_only_its_original_delivery_copy','wretire',{
        param($s) Order $s @('RequireGuardian(candidate, capability, true); RequireOpened(); RequireProducerRetired();','if (!guardianBorrowed || guardianRetirementAttempted)','guardianRetirementAttempted = true;','RetireInheritance(guardianCopy); CloseOriginal(guardianCopy); guardianRetired = true;');Has $s 'producerCopy' 0
    },'CloseOriginal(guardianCopy);','CloseOriginal(producerCopy);'),
    @('both_retired_proof_uses_outcomes_not_closed_handle_queries','both',{
        param($s) Order $s @('RequireGuardian(candidate, capability, true); RequireOpened(); RequireProducerRetired();','if (!guardianRetired)','RequireClosed(guardianCopy);');Has $s 'AssertFlags' 0;Has $s 'GetHandleInformation' 0
    },'RequireClosed(guardianCopy);','AssertFlags(guardianCopy, 0);'),
    @('only_recorded_null_mapping_failure_is_known_absence','cleanupacquisition',{
        param($s) Order $s @('if (!outcome.AcquireAttempted) return;','if (mappingCreation && outcome.AcquireReturned && !outcome.AcquireSucceeded && outcome.Raw == IntPtr.Zero) return;','RequireAcquired(outcome);','if (outcome.CloseAttempted && !outcome.CloseReturned)')
    },'mappingCreation &&',''),
    @('cleanup_requires_original_containment_not_success_admission','cleanup',{
        param($s) Order $s @('RequireOwner(candidate); candidate.AssertContextReleaseAllowed(this);','if (disposed) return;','RequireCleanupAcquisition(mapping, true); RequireCleanupAcquisition(producerCopy, false); RequireCleanupAcquisition(guardianCopy, false);','RequireDistinct();');Has $s 'RequireOpened' 0;Has $s 'RequireAdmission' 0
    },'candidate.AssertContextReleaseAllowed(this);',''),
    @('cleanup_rejects_unknown_view_and_releases_only_recorded_owned_handles','cleanup',{
        param($s) Order $s @('if (!viewReturned || (viewSucceeded && !Valid(writableView)) || (!viewSucceeded && writableView != IntPtr.Zero))','if (viewSucceeded) UnmapWritable();','if (producerCopy.AcquireAttempted) CloseOriginal(producerCopy);','if (guardianCopy.AcquireAttempted) CloseOriginal(guardianCopy);','if (mapping.AcquireSucceeded) CloseOriginal(mapping);','disposed = true;')
    },'if (viewSucceeded) UnmapWritable();',''),
    @('prepared_owner_assertion_is_managed_reference_state_only','prepared',{
        param($s) Has $s 'lock (gate) { try { RequireOwner(candidate); RequireOpened(); } catch (Exception) { failed = true; throw; } }';Has $s 'AssertFlags' 0;Has $s 'GetHandleInformation' 0
    },'RequireOwner(candidate);',''),
    @('retained_producer_constructs_context_with_original_this','producerctor',{
        param($s) Has $s 'this.preload = preload; this.inputs = inputs;';Has $s 'contextSection = new ProducerContextSection(this, contextBytes, contextSha256, contextLength);';Has $s 'OpenForProducer' 0
    },'new ProducerContextSection(this, contextBytes, contextSha256, contextLength)','new ProducerContextSection(null, contextBytes, contextSha256, contextLength)'),
    @('retained_producer_deadline_precedes_open_and_factory_assignment','producerstart',{
        param($s) Order $s @('startupDeadline.Begin(this);','contextSection.OpenForProducer(this);','controlChannel.OpenForProducer(this);','command = CompilerCommand.PrepareOwnedWithDeadline(inputs, executable, executableHash, arguments, directory, environment, startupDeadline, this, controlChannel, contextSection);','nativeStartAttempted = true;','command.PrepareBirthJob(); RequireAdmission(); command.StartSuspended();')
    },'contextSection.OpenForProducer(this);','startupDeadline.Begin(this);','move'),
    @('producer_context_backlink_and_cleanup_authority_do_not_require_admission','producer',{
        param($s)
        Has $s 'private readonly StartupPreloadOwner preload;';Has $s 'public static DisposableProducerLaunch Prepare(' 0
        $prepare=Method $s 'internal static DisposableProducerLaunch PreparePreloaded(StartupPreloadOwner preload)'
        Order $prepare @('StartupPreloadOwner.LaunchData selected = preload.BorrowProducerPreparation();','byte[] contextBytes = selected.ContextBytes;','string contextSha256 = selected.ContextSha256; long contextLength = selected.ContextLength;','return new DisposableProducerLaunch(preload, inputs, executable, hash, copiedArguments, directory, copiedEnvironment, contextBytes, contextSha256, contextLength);')
        $identity=Method $s 'internal bool OwnsContextSection(';Has $identity 'return Object.ReferenceEquals(contextSection, candidate);';Has $identity 'RequireAdmission' 0
        $release=Method $s 'internal void AssertContextReleaseAllowed(';Has $release '!Object.ReferenceEquals(contextSection, candidate) || (!containmentProven && !normalClosureProven) || guardian != null';Has $release 'RequireAdmission' 0;Has $release 'failed' 0
        $normal=Method $s 'internal CompilerResult PollNormalTerminal('
        Order $normal @('if (!continuationCompleted || !OwnsGuardian(originalGuardian, stopCapability) || command == null)','originalGuardian.AssertNormalProducerObservation(this); CompilerResult observed = command.PollResult();','if (observed == null) return null;','normalResult = observed;','long nativeExit = command.ObserveProducerNormalExit(this, observed);','normalExitFileTime = nativeExit;','if (stopCapability != null) stopCapability.CloseAfterProducerDeath();','if (transferCapability != null) transferCapability.CloseAfterProducerDeath();','RequireAdmission(); originalGuardian.AssertNormalProducerObservation(this); normalClosureProven = true; return normalResult;')
        Has $normal 'containmentProven = true;' 0;Has $normal 'PollContainment(' 0
    },'!Object.ReferenceEquals(contextSection, candidate) || (!containmentProven && !normalClosureProven) || guardian != null','!Object.ReferenceEquals(contextSection, candidate) || guardian != null'),
    @('producer_cleanup_releases_context_before_command_inputs','producerdispose',{
        param($s) Order $s @('if (!containmentProven && !normalClosureProven)','if (guardian != null)','releaseStarted = true;','controlChannel.DisposeAfterContainment(this);','contextSection.DisposeAfterContainment(this);','if (command != null) command.Dispose();','disposed = true;')
    },'contextSection.DisposeAfterContainment(this);','disposed = true;','move'),
    @('producer_factory_requires_typed_original_context_and_registers_last','factory',{
        param($s) Has $s 'ProducerContextSection contextSection';Has $s 'controlChannel == null || contextSection == null';Order $s @('contextSection.AssertPreparedOwner(originalOwner);','if (arguments.Length != 1 || arguments[0] != "--stratus-producer-entry-v6")','inputs, startupDeadline, originalOwner, controlChannel, contextSection);','inputs.RegisterCommand(prepared); return prepared;');Has $s 'OpenForProducer' 0
    },'contextSection.AssertPreparedOwner(originalOwner);',''),
    @('producer_six_handles_keep_generic_three_and_reject_context_aliases','compilerstart',{
        param($s) Has $s 'int inheritedCount = producerControlChannel == null ? 3 : 6;';Has $s 'producerContextDelivery.Handle == producerControlEndpoints.Read || producerContextDelivery.Handle == producerControlEndpoints.Write';Has $s 'standardHandle == producerControlEndpoints.Read || standardHandle == producerControlEndpoints.Write || standardHandle == producerContextDelivery.Handle';Has $s 'Marshal.WriteIntPtr(inheritedHandles, IntPtr.Size * 5, producerContextDelivery.Handle);';Has $s 'new IntPtr(0x2000D), launchJobs, new IntPtr(IntPtr.Size)'
    },'producerControlChannel == null ? 3 : 6','producerControlChannel == null ? 6 : 6'),
    @('producer_native_argv_captures_original_context_handle_bytes_and_hash','compiler',{
        param($s) $prepare=Method $s 'private void PrepareBirthJobOriginal()';$start=Method $s 'private void StartSuspendedOriginal(ProducerCompilerOwner owner)'
        Order $prepare @('producerContextDelivery = producerContextSection.BorrowProducerDelivery(originalDeadlineOwner);','ProducerDeadlineSnapshot producerStartupSnapshot = sharedStartupDeadline.SnapshotForProducer(originalDeadlineOwner);','capturedCommandLine = commandLine + " --stratus-producer-control-v6 "','ControlHandleText(producerContextDelivery.Handle) + " " + producerContextDelivery.Bytes.ToString(CultureInfo.InvariantCulture) + " " + producerContextDelivery.Sha256;')
        Order $start @('startAttempted = true; SuccessBoundary(); RequirePreparedJob(); AssertEmptyJobBeforeBirth();','CreateProcess(executable, new StringBuilder(capturedCommandLine)')
    },'new StringBuilder(capturedCommandLine)','new StringBuilder(commandLine)'),
    @('producer_fresh_context_pending_then_known_complete_birth_before_retirement','compilerstart',{
        param($s) Has $s 'if (producerContextSection != null) producerContextSection.AssertProducerDeliveryPending(originalDeadlineOwner); SuccessBoundary(); RequirePreparedJob(); AssertEmptyJobBeforeBirth(); ProducerCompilerBirthBoundary(owner); ClockAndFaultBoundary(); creationAttempted = true;';Order $s @('creationSucceeded = created; creationReturned = true;','if (!created)','if (!ValidHandle(creationOutputs.process) || !ValidHandle(creationOutputs.thread) || creationOutputs.process == creationOutputs.thread || creationOutputs.pid <= 0 || creationOutputs.tid <= 0)','process = creationOutputs.process; thread = creationOutputs.thread; pid = creationOutputs.pid; tid = creationOutputs.tid;','if (producerContextSection != null) producerContextSection.RetireProducerDelivery(originalDeadlineOwner);')
    },'producerContextSection.AssertProducerDeliveryPending(originalDeadlineOwner);',''),
    @('guardian_binds_original_context_before_its_inputs_and_native_launch','guardianstart',{
        param($s) Order $s @('sourceCapability = producer.RegisterGuardian(this, inputs);','contextSection = producer.BorrowContextSection(this, sourceCapability);','contextDelivery = contextSection.BorrowGuardianDelivery(this, sourceCapability);','inputs.RegisterGuardian(this);','NoJob(GetCurrentProcess()); launchCopy =','CreateProcess(executable, arguments, IntPtr.Zero, IntPtr.Zero, true, 0x08080400,')
    },'contextDelivery = contextSection.BorrowGuardianDelivery(this, sourceCapability);',''),
    @('guardian_seven_allowlisted_handles_and_fresh_context_pending','guardianstart',{
        param($s) Has $s 'BorrowOriginalChildEndForBirth(errorPair), ownerHandle, controlEndpoints.Read, controlEndpoints.Write, contextDelivery.Handle';Has $s 'Marshal.WriteIntPtr(inherited.Value, IntPtr.Size * 6, contextDelivery.Handle);';Has $s 'new IntPtr(0x20002), inherited.Value, new IntPtr(IntPtr.Size * 8)';Has $s 'NoJob(GetCurrentProcess()); AssertOriginalStdioForBirth(); AssertAllowlist(allowed); controlChannel.AssertGuardianDeliveryPending(this, sourceCapability); contextSection.AssertGuardianDeliveryPending(this, sourceCapability); Admission(); creationAttempted = true;';Order $s @('creationSucceeded = created; creationReturned = true;','if (!created)','RequireOriginalCreation(); controlChannel.RetireGuardianDelivery(this, sourceCapability);','contextSection.RetireGuardianDelivery(this, sourceCapability);','contextSection.AssertBothDeliveriesRetired(this, sourceCapability); AssertEarlyStdioRetired(); started = true;')
    },'contextSection.AssertGuardianDeliveryPending(this, sourceCapability);',''),
    @('guardian_v4_argv_and_bounded_ready_echo_the_same_context_pin','guardianstart',{
        param($s) Has $s 'arguments.Append(" --stratus-owner-watch-v8 ")';Has $s '.Append(controlEndpoints.Nonce).Append('' '').Append(contextHandle).Append('' '').Append(contextLength).Append('' '').Append(contextDelivery.Sha256).Append('' '').Append(rawTransfer);';Has $s 'expectedReady = "STRATUS_OWNER_READY_V8 " + nonce + " " + rawPid + " " + rawBirth + " " + rawStart + " " + rawDeadline + " " + controlEndpoints.Nonce + " " + contextLength + " " + contextDelivery.Sha256 + " " + rawTransfer + "\n";';Has $s 'if (expectedReady.Length > 352)'
    },'if (expectedReady.Length > 352)','if (expectedReady.Length > 65536)'),
    @('actual_ready_observation_requires_retired_context_and_original_capture','guardianready',{
        param($s) Order $s @('contextSection.AssertBothDeliveriesRetired(this, sourceCapability);','captured = stdout.ToArray();','if (captured.Length > expected.Length)','if (captured[i] != expected[i])','if (captureFailed || failures.Count != 0 || stdout.Length != expected.Length || stderr.Length != 0 || stdoutEof || stderrEof)','startupSnapshot.StartedTick, startupSnapshot.DeadlineTick, controlEndpoints.Nonce, contextDelivery.Bytes, contextDelivery.Sha256);','return readyObservation;')
    },'contextSection.AssertBothDeliveriesRetired(this, sourceCapability);',''),
    @('context_observation_is_immutable_and_does_not_authorize_resume','observation',{
        param($s) Has $s 'public readonly string ContextSha256;';Has $s 'public readonly int ContextBytes;';Has $s 'ContextBytes = contextBytes; ContextSha256 = contextSha256;';Has $s 'public readonly bool AuthorizesProducerResume = false, NativeQualified = false;'
    },'AuthorizesProducerResume = false','AuthorizesProducerResume = true'),
    @('actual_closed_helper_requires_original_acquisition_and_successful_release','closed',{
        param($s) Order $s @('RequireAcquired(outcome);','if (!outcome.CloseAttempted || !outcome.CloseReturned || !outcome.CloseSucceeded)');Has $s 'GetHandleInformation' 0;Has $s 'CloseHandle' 0
    },'!outcome.CloseSucceeded','false'),
    @('actual_producer_retired_helper_requires_closed_original_delivery','producerretired',{
        param($s) Order $s @('if (!producerRetired)','RequireClosed(producerCopy);');Has $s 'AssertFlags' 0
    },'RequireClosed(producerCopy);',''),
    @('guardian_admission_rechecks_context_binding_with_shared_original_deadline','guardianadmission',{
        param($s) Order $s @('if (disposed || releaseStarted || containmentStarted)','startupDeadline.AssertGuardianActive(this, sourceCapability);','if (contextSection != null) contextSection.AssertGuardianAdmission(this, sourceCapability);')
    },'contextSection.AssertGuardianAdmission(this, sourceCapability);',''),
    @('actual_guardian_allowlist_rejects_aliases_and_refreshes_each_handle','guardianallowlist',{
        param($s) Has $s 'if (handles == null || handles.Length != 8)';Order $s @('if (!Valid(handles[i]))','if (handles[j] == handles[i])','GetHandleInformation(handles[i], out flags)','if ((flags & 1) == 0 || (flags & 2) != 0)')
    },'handles[j] == handles[i]','false')
)
$expected=100
Assert-Context ($rows.Count -eq 50) 'The fixed 50 predicate/mutation pairs changed.'
$executed=0;$passed=0;$caseResults=[Collections.Generic.List[object]]::new()
foreach($row in $rows){
    $name=$row[0];$selected=$blocks[$row[1]];$check=$row[2]
    $executed++; & $check $selected; $passed++
    $caseResults.Add([pscustomobject]@{name=$name;variant='original_source';passed=$true})
    if($row.Count -eq 6){$mutated=Move-Tokens $selected $row[3] $row[4]}else{$mutated=Replace-Tokens $selected $row[3] $row[4]}
    $executed++;$rejected=$false
    try{& $check $mutated}catch{
        Assert-Context ($_.Exception.Message.StartsWith('Context section source contract:',[StringComparison]::Ordinal)) ('Unexpected negative-control exception: '+$name)
        $rejected=$true
    }
    Assert-Context $rejected ('The predicate accepted its negative mutation: '+$name)
    $passed++;$caseResults.Add([pscustomobject]@{name=$name;variant='mutated_source_rejected';passed=$true})
}
Assert-Context ($executed -eq $expected -and $passed -eq $expected) 'Fixed case count did not complete.'
$contextAfter=Read-ContextSource $ContextPath;$producerAfter=Read-ContextSource $ProducerPath
$bootstrapAfter=Read-ContextSource $BootstrapPath;$guardianAfter=Read-ContextSource $GuardianPath
$testAfter=Read-ContextSource $PSCommandPath
Assert-Context ($contextAfter.sha256 -ceq $context.sha256 -and $producerAfter.sha256 -ceq $producer.sha256 -and $bootstrapAfter.sha256 -ceq $bootstrap.sha256 -and $guardianAfter.sha256 -ceq $guardian.sha256 -and $testAfter.sha256 -ceq $testSource.sha256) 'Source bytes changed during this source-only run.'
[pscustomobject]@{
    format='stratus-context-section-source-contract-tests-v1'
    expected=$expected;executed=$executed;passed=$passed;failed=0
    powershell_version=$PSVersionTable.PSVersion.ToString();powershell_edition=$PSVersionTable.PSEdition
    context_source_sha256=$context.sha256;producer_source_sha256=$producer.sha256
    bootstrap_source_sha256=$bootstrap.sha256;guardian_source_sha256=$guardian.sha256
    test_sha256=$testSource.sha256;source_hashes_unchanged=$true
    target_compiled=$false;target_loaded=$false;target_lifecycle_executed=$false
    native_qualified=$false;context_origin_approved=$false;context_semantics_accepted=$false
    context_delivery_qualified=$false;resume_authorized=$false;full_build_accepted=$false
    l_pre_guardian_stall_bounded=$false;compiler_job_ack_implemented=$false
    cases=$caseResults.ToArray()
} | ConvertTo-Json -Depth 6 -Compress
