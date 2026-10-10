param()
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
# Read four fixed C# files and this test as bounded data. Only fixed local token
# predicates and inert weakening mutations run; no C# or target script is ever
# compiled, loaded, evaluated or invoked. No owner/native/fixture action runs.
# Source contracts do not qualify preload provenance, held bytes, or Open.
function Assert-Preload {param([bool]$Okay,[string]$Message) if(-not $Okay){throw ('Startup preload source contract: '+$Message)}}
function Read-PreloadSource {
    param([string]$Path)
    $full=[IO.Path]::GetFullPath($Path)
    # Bound allocation and every delivered read; a FileInfo precheck alone
    # cannot stop ReadAllBytes from allocating after concurrent file growth.
    $stream=[IO.FileStream]::new($full,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{
        $length=$stream.Length
        Assert-Preload ($length -gt 0 -and $length -le 1048576) 'Inspection file exceeds one MiB.'
        $raw=[byte[]]::new([int]$length);$offset=0
        while($offset -lt $raw.Length){
            $count=$stream.Read($raw,$offset,$raw.Length-$offset)
            Assert-Preload ($count -gt 0) 'Inspection file ended before its bounded length.'
            $offset+=$count
        }
        Assert-Preload ($stream.ReadByte() -eq -1 -and $stream.Length -eq $length) 'File length changed while reading.'
    }finally{$stream.Dispose()}
    $source=[Text.UTF8Encoding]::new($false,$true).GetString($raw)
    $hash=[Security.Cryptography.SHA256]::Create()
    try{$digest=[BitConverter]::ToString($hash.ComputeHash($raw)).Replace('-','').ToLowerInvariant()}finally{$hash.Dispose()}
    return [pscustomobject]@{path=$full;text=$source;sha256=$digest}
}
function Tokens {
    param([string]$Text)
    Assert-Preload ($null -ne $Text -and $Text.Length -le 1048576) 'Token input exceeds its fixed character bound.'
    $pattern=@'
//[^\r\n]* | /\*.*?\*/ | @"(?:""|[^"])*" | "(?:\\.|[^"\\])*" | '(?:\\.|[^'\\])*'
| [A-Za-z_][A-Za-z_0-9]* | [0-9]+ | == | != | >= | <= | \+\+ | -- | && | \|\| | => | [^\s]
'@
    $result=[Collections.Generic.List[string]]::new()
    $options=[Text.RegularExpressions.RegexOptions]::Singleline -bor [Text.RegularExpressions.RegexOptions]::IgnorePatternWhitespace
    foreach($m in [regex]::Matches($Text,$pattern,$options,[TimeSpan]::FromSeconds(5))){
        if(-not $m.Value.StartsWith('//') -and -not $m.Value.StartsWith('/*')){Assert-Preload ($result.Count -lt 262144) 'Token inspection bound exceeded.';$result.Add($m.Value)}
    }
    Assert-Preload ($result.Count -le 262144) 'Token inspection bound exceeded.'
    return ,$result.ToArray()
}
function Find {
    param([string[]]$Source,[string]$Sequence)
    $wanted=Tokens $Sequence;Assert-Preload ($wanted.Count -gt 0) 'Empty predicate.'
    $found=[Collections.Generic.List[int]]::new()
    for($i=0;$i -le $Source.Count-$wanted.Count;$i++){
        $same=$true
        for($j=0;$j -lt $wanted.Count;$j++){if($Source[$i+$j] -cne $wanted[$j]){$same=$false;break}}
        if($same){$found.Add($i)}
    }
    return ,$found.ToArray()
}
function Has {param([string[]]$Source,[string]$Sequence,[int]$Count=1) Assert-Preload ((Find $Source $Sequence).Count -eq $Count) ('Sequence differs: '+$Sequence)}
function Order {
    param([string[]]$Source,[string[]]$Sequences)
    $last=-1
    foreach($sequence in $Sequences){$found=Find $Source $sequence;Assert-Preload ($found.Count -eq 1 -and $found[0] -gt $last) ('Order differs: '+$sequence);$last=$found[0]}
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
    Assert-Preload ($found.Count -eq 1) ('Missing or ambiguous block: '+$Signature)
    $open=$found[0]
    while($open -lt $Source.Count -and $Source[$open] -cne '{'){$open++}
    Assert-Preload ($open -lt $Source.Count) 'Opening brace missing.'
    $depth=0
    for($i=$open;$i -lt $Source.Count;$i++){
        if($Source[$i] -ceq '{'){$depth++}elseif($Source[$i] -ceq '}'){$depth--}
        if($depth -eq 0){return ,([string[]]$Source[$found[0]..$i])}
    }
    throw 'Startup preload source contract: Unbalanced source block.'
}
function Method {param([string[]]$Class,[string]$Signature) return ,(Block $Class $Signature -Direct)}
function Replace-Tokens {
    param([string[]]$Source,[string]$Needle,[string]$Replacement)
    $at=Find $Source $Needle;Assert-Preload ($at.Count -eq 1) ('Mutation anchor differs: '+$Needle)
    $old=Tokens $Needle;$new=Tokens $Replacement;$result=[Collections.Generic.List[string]]::new()
    for($i=0;$i -lt $at[0];$i++){$result.Add($Source[$i])}
    foreach($token in $new){$result.Add($token)}
    for($i=$at[0]+$old.Count;$i -lt $Source.Count;$i++){$result.Add($Source[$i])}
    return ,$result.ToArray()
}
$sources=[ordered]@{}
foreach($name in @('StartupPreload','Producer','GuardianLaunch','Bootstrap')){
    $sources[$name]=Read-PreloadSource ([IO.Path]::Combine($PSScriptRoot,'..',('Stratus.RuntimeBuild.'+$name+'.cs')))
}
$testBefore=Read-PreloadSource $PSCommandPath
$owner=Block (Tokens $sources.StartupPreload.text) 'public sealed class StartupPreloadOwner : IDisposable'
$p=Block (Tokens $sources.Producer.text) 'public sealed class DisposableProducerLaunch : IDisposable'
$w=Block (Tokens $sources.GuardianLaunch.text) 'public sealed class GuardianLaunch : IDisposable'
$b=Block (Tokens $sources.Bootstrap.text) 'public sealed class CompilerCommand : IDisposable'
$blocks=@{
    owner=$owner
    descriptor=Block $owner 'private sealed class Descriptor'
    record=Block $owner 'private sealed class PreloadRecord'
    data=Block $owner 'internal sealed class LaunchData'
    constructor=Method $owner 'private StartupPreloadOwner('
    prepare=Method $owner 'public static StartupPreloadOwner Prepare('
    selector=Method $owner 'private static Descriptor SelectIndependentDescriptor('
    blocker=Method $owner 'private static void DemandQualifiedPreloadOpen('
    admission=Method $owner 'private void AssertAdmission('
    enter=Method $owner 'private void Enter('
    open=Method $owner 'public void Open('
    preparep=Method $owner 'public DisposableProducerLaunch PrepareProducer('
    preparew=Method $owner 'public GuardianLaunch PrepareGuardian('
    retainedp=Method $owner 'public DisposableProducerLaunch RetainedProducer'
    retainedw=Method $owner 'public GuardianLaunch RetainedGuardian'
    borrowp=Method $owner 'internal LaunchData BorrowProducerPreparation('
    borroww=Method $owner 'internal LaunchData BorrowGuardianPreparation('
    assertp=Method $owner 'internal void AssertProducer('
    assertw=Method $owner 'internal void AssertGuardian('
    dispose=Method $owner 'public void Dispose('
    disposeinputs=Method $owner 'private static void DisposeInputs('
    records=Method $owner 'private static PreloadRecord[] CopyRecords('
    shared=Method $owner 'private static void AssertSharedDeclarations('
    specifications=Method $owner 'private static BuildInputSpec[] Specifications('
    environment=Method $owner 'private static Dictionary<string, string> ExactEnvironment('
    snapshots=Method $owner 'private void VerifySnapshots('
    hash=Method $owner 'private static string HashBytes('
    p=$p;w=$w
    pfactory=Method $p 'internal static DisposableProducerLaunch PreparePreloaded('
    wfactory=Method $w 'internal static GuardianLaunch PreparePreloaded('
    padmission=Method $p 'private void RequireAdmission('
    pcommand=Method $p 'internal void AssertPreloadAdmission('
    wadmission=Method $w 'private void Admission('
    prelease=[string[]]@((Method $p 'internal void AssertPreloadReleaseAllowed(')+(Method $p 'internal CompilerResult PollNormalTerminal(')+(Method $b 'internal long ObserveProducerNormalExit('))
    wrelease=[string[]]@((Method $w 'internal void AssertPreloadReleaseAllowed(')+(Method $w 'internal BuildTerminalClosure PollNormalTerminal('))
    clock=Method $b 'private void ClockAndFaultBoundary('
    boundary=Method $b 'private void SuccessBoundary('
    start=Method $b 'private void StartSuspendedOriginal('
}
# Only the fixed literal callbacks below are executed. Neither source nor a
# mutation supplies executable PowerShell, a type definition, or an assembly.
Has (Method $b 'public void StartSuspended(') 'StartSuspendedOriginal(null);'
$rows=@(
    @('caller_can_supply_only_bounded_cloned_context_not_descriptor','prepare',{
        param($s) Has $s 'public static StartupPreloadOwner Prepare(byte[] rawContext)';Order $s @('Require(rawContext != null && rawContext.Length >= 1 && rawContext.Length <= ContextLimit);','byte[] capturedContext = (byte[])rawContext.Clone();','Descriptor selected = SelectIndependentDescriptor();','Require(selected != null);','return new StartupPreloadOwner(capturedContext, selected);')
    },'Descriptor selected = SelectIndependentDescriptor();','Descriptor selected = SelectIndependentDescriptor(rawContext);'),
    @('independent_selector_is_exactly_parameterless_deny_all','selector',{
        param($s) Has $s 'private static Descriptor SelectIndependentDescriptor() { return null; }'
    },'return null;','return new Descriptor();'),
    @('separate_qualification_blocker_unconditionally_throws','blocker',{
        param($s) Has $s 'private static void DemandQualifiedPreloadOpen() { throw new InvalidOperationException("P/W preload Open requires independently qualified L/bootstrap, preload, environment and execution scope."); }'
    },'throw new InvalidOperationException("P/W preload Open requires independently qualified L/bootstrap, preload, environment and execution scope.");','return;'),
    @('no_candidate_profile_approval_token_or_runtime_selection_route','owner',{
        param($s) Has $s 'private StartupPreloadOwner(';foreach($bad in @('ContextTrust.','ContextMetadata.','Environment.','File.','Directory.','Assembly.','Activator.','Type.GetType','Func<','Action<','bool approved','bool qualified','DllImport','extern','InstallDescriptor','TargetFramework')){Has $s $bad 0}
    },'public static StartupPreloadOwner Prepare(byte[] rawContext) {','public static StartupPreloadOwner Prepare(byte[] rawContext) { Environment.GetEnvironmentVariable("approval");'),
    @('descriptor_private_immutable_and_copies_both_record_sets','descriptor',{
        param($s) Has $s 'private sealed class Descriptor';Has $s 'internal readonly string Version, SystemRoot, ProducerDirectory, GuardianDirectory;';Has $s 'internal readonly string ContextSha256;';Has $s 'internal readonly long ContextLength;';Has $s 'private readonly PreloadRecord[] producerRecords, guardianRecords;';Has $s 'this.producerRecords = CopyRecords(producerRecords, true); this.guardianRecords = CopyRecords(guardianRecords, false); AssertSharedDeclarations(this.producerRecords, this.guardianRecords);'
    },'private readonly PreloadRecord[] producerRecords, guardianRecords;','internal PreloadRecord[] producerRecords, guardianRecords;'),
    @('descriptor_fixed_version_path_hash_and_context_length_boundary','descriptor',{
        param($s) Has $s 'Require(version == DescriptorVersion);';Has $s 'BuildInputValidation.Path(systemRoot);';Has $s 'BuildInputValidation.Path(producerDirectory);';Has $s 'BuildInputValidation.Path(guardianDirectory);';Has $s 'BuildInputValidation.Hash(contextSha256);';Has $s 'Require(contextLength >= 1 && contextLength <= ContextLimit);'
    },'Require(version == DescriptorVersion);','/* descriptor version unchecked */'),
    @('preload_records_are_sealed_immutable_scalars','record',{
        param($s) Has $s 'private sealed class PreloadRecord';Has $s 'internal readonly PreloadRole Role;';Has $s 'internal readonly string Path, Sha256;';Has $s 'internal readonly long Bytes;'
    },'internal readonly long Bytes;','internal long Bytes;'),
    @('closed_role_sets_require_only_role_executable_at_index_zero','records',{
        param($s) Has $s 'bool executable = item.Role == (producerRole ? PreloadRole.ProducerExecutable : PreloadRole.GuardianExecutable);';Has $s 'bool allowed = producerRole ? (item.Role == PreloadRole.ProducerExecutable || item.Role == PreloadRole.ProducerRuntime || item.Role == PreloadRole.ProducerDependency || item.Role == PreloadRole.ProducerConfiguration) : (item.Role == PreloadRole.GuardianExecutable || item.Role == PreloadRole.GuardianRuntime || item.Role == PreloadRole.GuardianDependency || item.Role == PreloadRole.GuardianConfiguration);';Has $s 'Require(allowed && executable == (i == 0));';Has $s 'Require(executables == 1); return copy;'
    },'Require(allowed && executable == (i == 0));','Require(allowed);'),
    @('record_inventory_has_fixed_count_hash_path_alias_and_checked_budget','records',{
        param($s) Has $s 'Require(source != null && source.Length >= 1 && source.Length <= InventoryCountLimit);';Has $s 'new HashSet<string>(StringComparer.OrdinalIgnoreCase)';Has $s 'BuildInputValidation.Path(item.Path); BuildInputValidation.Hash(item.Sha256);';Has $s 'Require(paths.Add(item.Path) && item.Bytes >= 1 && item.Bytes <= FileLimit);';Has $s 'total = checked(total + item.Bytes); Require(total <= InventoryBytesLimit);';Has $s 'copy[i] = new PreloadRecord(item.Role, item.Path, item.Sha256, item.Bytes);'
    },'Require(total <= InventoryBytesLimit);','/* whole-owner budget unchecked */'),
    @('overlapping_cross_owner_paths_require_exact_declared_hash_and_length','shared',{
        param($s) Has $s 'if (!String.Equals(left.Path, right.Path, StringComparison.OrdinalIgnoreCase)) continue;';Has $s 'Require(left.Bytes == right.Bytes && String.Equals(left.Sha256, right.Sha256, StringComparison.Ordinal));'
    },'left.Bytes == right.Bytes','true'),
    @('both_real_managed_owners_retained_before_launch_data_construction','constructor',{
        param($s) Order $s @('descriptor = selected.Copy();','contextBytes = (byte[])capturedContext.Clone();','producerRecords = descriptor.CopyProducerRecords();','guardianRecords = descriptor.CopyGuardianRecords();','producerInputs = BuildInputLeaseSet.Prepare(Specifications(producerRecords));','guardianInputs = BuildInputLeaseSet.Prepare(Specifications(guardianRecords));','Require(!Object.ReferenceEquals(producerInputs, guardianInputs));','producerData = new LaunchData(producerInputs, producerRecords[0].Path, producerRecords[0].Sha256, descriptor.ProducerDirectory, descriptor.SystemRoot, contextBytes, descriptor.ContextSha256);','guardianData = new LaunchData(guardianInputs, guardianRecords[0].Path, guardianRecords[0].Sha256, descriptor.GuardianDirectory, descriptor.SystemRoot, contextBytes, descriptor.ContextSha256);');foreach($bad in @('.Open(','.CaptureSnapshots(','new Thread(','CreateProcess(','.Dispose(')){Has $s $bad 0}
    },'guardianInputs = BuildInputLeaseSet.Prepare(Specifications(guardianRecords));','guardianInputs = producerInputs;'),
    @('real_specs_derived_only_from_private_record_scalars','specifications',{
        param($s) Has $s 'BuildInputSpec[] specs = new BuildInputSpec[records.Length];';Has $s 'specs[i] = new BuildInputSpec(records[i].Path, records[i].Sha256, records[i].Bytes, FileLimit);'
    },'records[i].Sha256','records[0].Sha256'),
    @('fixed_environment_is_exactly_four_declared_directory_entries','environment',{
        param($s) Has $s 'new Dictionary<string, string>(StringComparer.Ordinal)';Has $s 'result.Add("SystemRoot", systemRoot); result.Add("TEMP", directory); result.Add("TMP", directory); result.Add("WINDIR", systemRoot);';Has $s 'result.Add(' 4
    },'result.Add("TMP", directory);','result.Add("TMP", systemRoot);'),
    @('launch_data_retains_exact_owner_and_defensively_copies_environment_context','data',{
        param($s) Has $s 'internal readonly BuildInputLeaseSet Inputs;';Has $s 'internal readonly string Executable, Sha256, Directory, ContextSha256;';Has $s 'Inputs = inputs; Executable = executable; Sha256 = sha256; Directory = directory; ContextSha256 = contextSha256; ContextLength = context.LongLength; environment = ExactEnvironment(systemRoot, directory); contextBytes = (byte[])context.Clone();';Has $s 'return new Dictionary<string, string>(environment, StringComparer.Ordinal);';Has $s 'return (byte[])contextBytes.Clone();'
    },'return (byte[])contextBytes.Clone();','return contextBytes;'),
    @('admission_closes_on_fault_release_disposal_or_missing_ready','admission',{
        param($s) Has $s 'Require(Interlocked.CompareExchange(ref failed, 0, 0) == 0 && Interlocked.CompareExchange(ref releaseStarted, 0, 0) == 0 && Interlocked.CompareExchange(ref disposed, 0, 0) == 0 && (!requireReady || Interlocked.CompareExchange(ref ready, 0, 0) == 1));'
    },'Interlocked.CompareExchange(ref releaseStarted, 0, 0) == 0','true'),
    @('overlapping_owner_operation_is_sticky_failure','enter',{
        param($s) Has $s 'if (Interlocked.CompareExchange(ref operation, 1, 0) != 0)';Has $s 'RecordFailure(); throw new InvalidOperationException("Concurrent startup preload operations are forbidden.");'
    },'Interlocked.CompareExchange(ref operation, 1, 0) != 0','Interlocked.CompareExchange(ref operation, 0, 0) != 0'),
    @('context_actual_hash_and_unconditional_barrier_precede_any_open','open',{
        param($s) Order $s @('AssertAdmission(false); Require(!openAttempted);','openAttempted = true;','Require(contextBytes.LongLength == descriptor.ContextLength && String.Equals(HashBytes(contextBytes), descriptor.ContextSha256, StringComparison.Ordinal));','AssertAdmission(false); DemandQualifiedPreloadOpen(); producerInputs.Open();')
    },'DemandQualifiedPreloadOpen();','/* preload qualification skipped */'),
    @('both_opened_owner_snapshots_validate_before_ready_publication','open',{
        param($s) Order $s @('producerInputs.Open();','producerSnapshots = producerInputs.CaptureSnapshots();','VerifySnapshots(producerRecords, producerSnapshots);','guardianInputs.Open();','guardianSnapshots = guardianInputs.CaptureSnapshots();','VerifySnapshots(guardianRecords, guardianSnapshots);','producerInputs.AssertHeld(); guardianInputs.AssertHeld();','AssertAdmission(false); Interlocked.Exchange(ref ready, 1);')
    },'VerifySnapshots(guardianRecords, guardianSnapshots);','/* guardian actual snapshot validation skipped */'),
    @('partial_open_failure_retains_both_owners_without_cleanup_or_reopen','open',{
        param($s) Has $s 'catch (Exception) { RecordFailure(); throw; } finally { Leave(); }';Has $s 'producerInputs.Open();' 1;Has $s 'guardianInputs.Open();' 1;foreach($bad in @('.Dispose(','producerInputs =','guardianInputs =','openAttempted = false','new Thread(','.Join(')){Has $s $bad 0}
    },'catch (Exception) { RecordFailure(); throw; }','catch (Exception) { guardianInputs.Dispose(); RecordFailure(); throw; }'),
    @('actual_snapshot_path_length_and_hash_not_specification_hash_field','snapshots',{
        param($s) Has $s 'Require(snapshots != null && snapshots.Length == records.Length);';Has $s 'Require(snapshot != null && String.Equals(snapshot.Path, expected.Path, StringComparison.Ordinal));';Has $s 'byte[] actual = snapshot.Bytes;';Has $s 'Require(actual != null && actual.LongLength == expected.Bytes && actual.LongLength <= FileLimit && snapshot.Length == actual.LongLength && String.Equals(HashBytes(actual), expected.Sha256, StringComparison.Ordinal));';Has $s 'snapshot.Sha256' 0;Has $s 'AssertAdmission(false);' 2
    },'HashBytes(actual)','snapshot.Sha256'),
    @('actual_snapshot_hash_is_bcl_sha256_of_actual_byte_array','hash',{
        param($s) Has $s 'using (SHA256 hash = SHA256.Create())';Has $s 'return BitConverter.ToString(hash.ComputeHash(actual)).Replace("-", "").ToLowerInvariant();'
    },'hash.ComputeHash(actual)','hash.ComputeHash(new byte[0])'),
    @('producer_wrapper_is_retained_before_return_without_native_start','preparep',{
        param($s) Order $s @('AssertAdmission(true);','Require(!producerPreparationAttempted && producer == null && guardian == null && preparationPhase == PreparationPhase.None);','producerPreparationAttempted = true;','preparationThreadId = Thread.CurrentThread.ManagedThreadId;','preparationPhase = PreparationPhase.Producer;','DisposableProducerLaunch prepared = DisposableProducerLaunch.PreparePreloaded(this);','Interlocked.Exchange(ref producer, prepared); return prepared; } catch (Exception) { RecordFailure(); throw; }');Has $s 'StartSuspended(' 0;Has $s 'finally { preparationPhase = PreparationPhase.None; preparationThreadId = 0; Leave(); }'
    },'Interlocked.Exchange(ref producer, prepared); return prepared;','Interlocked.Exchange(ref producer, prepared); AssertProducer(prepared, producerInputs); return prepared;'),
    @('guardian_wrapper_is_retained_with_exact_original_producer_before_return','preparew',{
        param($s) Order $s @('Require(producer != null && producerPreparationAttempted && !guardianPreparationAttempted && guardian == null && preparationPhase == PreparationPhase.None);','AssertProducer(producer, producerInputs);','guardianPreparationAttempted = true;','preparationThreadId = Thread.CurrentThread.ManagedThreadId;','preparationPhase = PreparationPhase.Guardian;','GuardianLaunch prepared = GuardianLaunch.PreparePreloaded(this, producer);','Interlocked.Exchange(ref guardian, prepared); return prepared; } catch (Exception) { RecordFailure(); throw; }');Has $s 'StartRunning(' 0;Has $s 'finally { preparationPhase = PreparationPhase.None; preparationThreadId = 0; Leave(); }'
    },'Interlocked.Exchange(ref guardian, prepared); return prepared;','Interlocked.Exchange(ref guardian, prepared); AssertGuardian(prepared, producer, guardianInputs); return prepared;'),
    @('producer_recovery_getter_returns_only_atomic_historical_original_reference','retainedp',{
        param($s) Has $s 'public DisposableProducerLaunch RetainedProducer { get { return Interlocked.CompareExchange(ref producer, null, null); } }'
    },'return Interlocked.CompareExchange(ref producer, null, null);','AssertAdmission(true); return Interlocked.CompareExchange(ref producer, null, null);'),
    @('guardian_recovery_getter_returns_only_atomic_historical_original_reference','retainedw',{
        param($s) Has $s 'public GuardianLaunch RetainedGuardian { get { return Interlocked.CompareExchange(ref guardian, null, null); } }'
    },'return Interlocked.CompareExchange(ref guardian, null, null);','return Interlocked.CompareExchange(ref guardian, new GuardianLaunch(), null);'),
    @('producer_borrow_requires_ready_exact_active_phase_and_thread','borrowp',{
        param($s) Has $s 'AssertAdmission(true);' 2;Has $s 'Require(Interlocked.CompareExchange(ref operation, 0, 0) == 1 && preparationPhase == PreparationPhase.Producer && preparationThreadId == Thread.CurrentThread.ManagedThreadId && producerPreparationAttempted && producer == null && guardian == null);';Has $s 'return producerData;'
    },'preparationThreadId == Thread.CurrentThread.ManagedThreadId','true'),
    @('guardian_borrow_requires_ready_phase_thread_and_original_producer','borroww',{
        param($s) Has $s 'AssertAdmission(true);' 2;Has $s 'Require(Interlocked.CompareExchange(ref operation, 0, 0) == 1 && preparationPhase == PreparationPhase.Guardian && preparationThreadId == Thread.CurrentThread.ManagedThreadId && guardianPreparationAttempted && guardian == null && candidate != null && Object.ReferenceEquals(candidate, producer));';Has $s 'return guardianData;'
    },'Object.ReferenceEquals(candidate, producer)','candidate != null'),
    @('producer_owner_callback_exact_reference_and_nonblocking_managed_state','assertp',{
        param($s) Has $s 'Require(candidate != null && inputs != null && producerPreparationAttempted && Object.ReferenceEquals(candidate, producer) && Object.ReferenceEquals(inputs, producerInputs));';Has $s 'AssertAdmission(true);' 2;foreach($bad in @('lock (','Enter(','.AssertHeld(','.RequireAdmission(','.Open(','.Join(')){Has $s $bad 0}
    },'Object.ReferenceEquals(inputs, producerInputs)','inputs != null'),
    @('guardian_owner_callback_binds_w_p_and_distinct_owner','assertw',{
        param($s) Has $s 'Require(candidate != null && p != null && inputs != null && guardianPreparationAttempted && Object.ReferenceEquals(candidate, guardian) && Object.ReferenceEquals(p, producer) && Object.ReferenceEquals(inputs, guardianInputs));';Has $s 'AssertAdmission(true);' 2
    },'Object.ReferenceEquals(inputs, guardianInputs)','Object.ReferenceEquals(inputs, producerInputs)'),
    @('actual_p_factory_is_typed_uses_owner_data_and_no_raw_public_factory','p',{
        param($s) Has $s 'private readonly StartupPreloadOwner preload;';Has $s 'public static DisposableProducerLaunch Prepare(' 0
        $factory=Method $s 'internal static DisposableProducerLaunch PreparePreloaded(StartupPreloadOwner preload)'
        Order $factory @('StartupPreloadOwner.LaunchData selected = preload.BorrowProducerPreparation();','BuildInputLeaseSet inputs = selected.Inputs;','string executable = selected.Executable, hash = selected.Sha256, directory = selected.Directory;','Dictionary<string, string> environment = selected.Environment;','byte[] contextBytes = selected.ContextBytes;','string contextSha256 = selected.ContextSha256; long contextLength = selected.ContextLength;','string[] arguments = new string[] { "--stratus-producer-entry-v6" };','return new DisposableProducerLaunch(preload, inputs, executable, hash, copiedArguments, directory, copiedEnvironment, contextBytes, contextSha256, contextLength);')
    },'preload.BorrowProducerPreparation()','preload.BorrowGuardianPreparation(null)'),
    @('actual_w_factory_is_typed_and_binds_original_preload_and_p','w',{
        param($s) Has $s 'private readonly StartupPreloadOwner preload;';Has $s 'public static GuardianLaunch Prepare(' 0
        $factory=Method $s 'internal static GuardianLaunch PreparePreloaded(StartupPreloadOwner preload, DisposableProducerLaunch producer)'
        Order $factory @('StartupPreloadOwner.LaunchData selected = preload.BorrowGuardianPreparation(producer);','BuildInputLeaseSet guardianInputs = selected.Inputs;','string executable = selected.Executable, hash = selected.Sha256, directory = selected.Directory;','Dictionary<string, string> environment = selected.Environment;','return new GuardianLaunch(preload, producer, guardianInputs, executable, hash, directory, block.ToString(), clock);')
    },'preload.BorrowGuardianPreparation(producer)','preload.BorrowGuardianPreparation(null)'),
    @('p_admission_checks_original_preload_before_started_deadline','padmission',{
        param($s) Order $s @('if (disposed || releaseStarted || containmentStarted)','lock (failureGate) { if (failed) throw new AggregateException(failures.ToArray()); }','preload.AssertProducer(this, inputs);','if (startAttempted) startupDeadline.AssertActive(this);')
    },'preload.AssertProducer(this, inputs);','/* owner admission bypass */'),
    @('w_admission_checks_original_preload_before_clock_and_transport','wadmission',{
        param($s) Order $s @('if (disposed || releaseStarted || containmentStarted)','preload.AssertGuardian(this, producer, inputs);','if (readyClock.ElapsedMilliseconds >= ReadyMilliseconds)','if (startupDeadline != null) startupDeadline.AssertGuardianActive(this, sourceCapability);','if (controlChannel != null) controlChannel.AssertGuardianAdmission(this, sourceCapability);')
    },'preload.AssertGuardian(this, producer, inputs);','/* owner admission bypass */'),
    @('bootstrap_clock_routes_exact_original_command_through_preload_owner','clock',{
        param($s) Order $s @('SuccessState();','if (originalDeadlineOwner != null) originalDeadlineOwner.AssertPreloadAdmission(this);','if (successClock.ElapsedMilliseconds >= SuccessMilliseconds)','if (sharedStartupDeadline != null) sharedStartupDeadline.AssertActive(originalDeadlineOwner);')
    },'originalDeadlineOwner.AssertPreloadAdmission(this);','originalDeadlineOwner.AssertPreloadAdmission(null);'),
    @('command_backlink_guard_cannot_accept_an_alternate_command','pcommand',{
        param($s) Has $s 'candidate == null || !Object.ReferenceEquals(command, candidate) || disposed || releaseStarted || containmentStarted';Has $s 'lock (failureGate) { if (failed) throw new AggregateException(failures.ToArray()); }';Has $s 'preload.AssertProducer(this, inputs);';foreach($bad in @('Enter(','lock (gate)','StartSuspended(','.Open(')){Has $s $bad 0}
    },'!Object.ReferenceEquals(command, candidate)','false'),
    @('success_boundary_sandwiches_owner_hold_with_preload_clock_checks','boundary',{
        param($s) Has $s 'ClockAndFaultBoundary(); AssertOwnedInputsHeld(); ClockAndFaultBoundary();'
    },'ClockAndFaultBoundary(); AssertOwnedInputsHeld(); ClockAndFaultBoundary();','AssertOwnedInputsHeld();'),
    @('real_native_birth_still_has_fresh_success_boundary_immediately_before_attempt','start',{
        param($s) Has $s 'HeldExecutableBoundary(); SuccessBoundary(); AssertOriginalStdioForBirth(); if (producerControlChannel != null) producerControlChannel.AssertProducerDeliveryPending(originalDeadlineOwner); if (producerContextSection != null) producerContextSection.AssertProducerDeliveryPending(originalDeadlineOwner); SuccessBoundary(); RequirePreparedJob(); AssertEmptyJobBeforeBirth(); ProducerCompilerBirthBoundary(owner); ClockAndFaultBoundary(); creationAttempted = true; bool created = CreateProcess('
    },'SuccessBoundary(); RequirePreparedJob(); AssertEmptyJobBeforeBirth(); ProducerCompilerBirthBoundary(owner); ClockAndFaultBoundary(); creationAttempted = true;','RequirePreparedJob(); AssertEmptyJobBeforeBirth(); ProducerCompilerBirthBoundary(owner); ClockAndFaultBoundary(); creationAttempted = true;'),
    @('release_closes_admission_then_checks_wrappers_before_reverse_owner_disposal','dispose',{
        param($s) Order $s @('Interlocked.Exchange(ref releaseStarted, 1);','if (guardian != null) guardian.AssertPreloadReleaseAllowed(this);','if (producer != null) producer.AssertPreloadReleaseAllowed(this);','DisposeInputs(guardianInputs, ref guardianInputsDisposeAttempted, ref guardianInputsDisposeReturned, ref guardianInputsDisposed);','DisposeInputs(producerInputs, ref producerInputsDisposeAttempted, ref producerInputsDisposeReturned, ref producerInputsDisposed);','Interlocked.Exchange(ref disposed, 1);');foreach($bad in @('.PollContainment(','.StartRunning(','.StartSuspended(','.Join(')){Has $s $bad 0}
    },'if (guardian != null) guardian.AssertPreloadReleaseAllowed(this);','/* W closure unchecked */'),
    @('unknown_owner_dispose_outcome_cannot_be_retried_or_cleared','disposeinputs',{
        param($s) Order $s @('if (succeeded) return;','Require(!attempted || returned);','attempted = true; returned = false;','try { inputs.Dispose(); succeeded = true; returned = true; }','catch (Exception) { returned = true; throw; }');Has $s 'inputs = null' 0
    },'Require(!attempted || returned);','/* unknown dispose return retried */'),
    @('p_release_requires_exact_owner_idle_operation_and_irreversible_closure','prelease',{
        param($s) $release=Block $s 'internal void AssertPreloadReleaseAllowed('
        Has $release 'if (!Object.ReferenceEquals(preload, candidate) || Interlocked.CompareExchange(ref operation, 0, 0) != 0 || !disposed || (!containmentProven && !normalClosureProven) || guardian != null || (stopCapability != null && !stopCapability.IsClosed) || (transferCapability != null && !transferCapability.IsClosed))';Has $release 'RequireAdmission(' 0
        $normal=Block $s 'internal CompilerResult PollNormalTerminal('
        Order $normal @('if (!continuationCompleted || !OwnsGuardian(originalGuardian, stopCapability) || command == null)','originalGuardian.AssertNormalProducerObservation(this); CompilerResult observed = command.PollResult();','if (observed == null) return null;','long nativeExit = command.ObserveProducerNormalExit(this, observed);','if (stopCapability != null) stopCapability.CloseAfterProducerDeath();','if (transferCapability != null) transferCapability.CloseAfterProducerDeath();','RequireAdmission(); originalGuardian.AssertNormalProducerObservation(this); normalClosureProven = true; return normalResult;')
        Has $normal 'containmentProven = true;' 0;Has $normal 'PollContainment(' 0
        Has (Block $s 'internal long ObserveProducerNormalExit(') 'code != 0 || RootWait() != WaitObject || JobAccounting().active != 0 || !CollectorsClosed()'
    },'!disposed || (!containmentProven && !normalClosureProven)','(!containmentProven && !normalClosureProven)'),
    @('w_release_requires_exact_owner_physical_closure_and_all_resource_releases','wrelease',{
        param($s) $release=Block $s 'internal void AssertPreloadReleaseAllowed('
        Has $release 'if (!Object.ReferenceEquals(preload, candidate) || Interlocked.CompareExchange(ref operation, 0, 0) != 0 || !disposed || !closureProven || (!producerContained && !normalTerminalProven) || !resourcesClosed || !inputsReleased || (producerRegistered && !producerReleased) || (launchCopy != null && !launchCopy.IsClosed) || (transferLaunchCopy != null && !transferLaunchCopy.IsClosed) || (transferSourceCapability != null && !transferSourceCapability.IsClosed))';Has $release 'Admission(' 0
        $normal=Block $s 'internal BuildTerminalClosure PollNormalTerminal('
        Order $normal @('DemandQualifiedLauncherTerminal();','CompilerResult actualProducer = producer.PollNormalTerminal(this);','TerminalRecord actualTerminal = TerminalWire.ParseProducerTerminal(actualProducer.Stdout, binding);','if (normalWait != WaitObject)','if (normalExitCode != 0)','if (captureFailed || failures.Count != 0 || stderr.Length != 0 || !stdoutEof || !stderrEof || !stdoutDone || !stderrDone)','long peerPExit = TerminalWire.RequireGuardianTerminal(terminal, actualTerminal, nonce);','long actualPExit = producer.OriginalNormalExitFileTime(this, actualProducer);','if (peerPExit != actualPExit)','if (!RootBoundary(false) || !CollectorsClosed())','normalClosure = new BuildTerminalClosure(this, actualTerminal, actualPExit, normalExited);','closureProven = true; normalTerminalProven = true;')
        Has $normal 'producerContained = true;' 0;Has $normal 'PollContainment(' 0
    },'!disposed || !closureProven','!closureProven'),
    @('fixed_limits_and_no_implicit_start_resume_or_default_runtime_profile','owner',{
        param($s) Has $s 'private const string DescriptorVersion = "stratus-runtime-build-startup-preload-v1";';Has $s 'private const int ContextLimit = 8388608, InventoryCountLimit = 104;';Has $s 'private const long FileLimit = 134217728, InventoryBytesLimit = 275251200;';foreach($bad in @('.StartSuspended(','.StartRunning(','.Resume(','ResumeThread(','CreateProcess(','GetTickCount64(','Stopwatch.StartNew(','net48','net8','RuntimeInformation.')){Has $s $bad 0}
    },'InventoryCountLimit = 104','InventoryCountLimit = 1024')
)
$expectedPairs=42;$expected=84
Assert-Preload ($rows.Count -eq $expectedPairs -and $expected -eq 2*$expectedPairs) 'Fixed case inventory differs.'
$executed=0;$results=[Collections.Generic.List[object]]::new()
foreach($row in $rows){
    Assert-Preload ($row.Count -eq 5) 'Fixed row shape differs.'
    $selected=$blocks[$row[1]];$check=$row[2]
    & $check $selected;$executed++;$results.Add([pscustomobject]@{name=$row[0];variant='original_source';passed=$true})
    $mutated=Replace-Tokens $selected $row[3] $row[4];$rejected=$false
    try{& $check $mutated}catch{
        Assert-Preload ($_.Exception.Message.StartsWith('Startup preload source contract:',[StringComparison]::Ordinal)) ('Unexpected negative-control exception: '+$row[0])
        $rejected=$true
    }
    Assert-Preload $rejected ('Weakening mutation survived: '+$row[0])
    $executed++;$results.Add([pscustomobject]@{name=$row[0];variant='mutated_source_rejected';passed=$true})
}
Assert-Preload ($executed -eq $expected -and $results.Count -eq $expected) 'Fixed cases did not complete.'
$before=[Collections.Generic.List[object]]::new();$after=[Collections.Generic.List[object]]::new()
foreach($name in $sources.Keys){
    $old=$sources[$name];$latest=Read-PreloadSource $old.path
    Assert-Preload ($latest.sha256 -ceq $old.sha256) ('Subject changed during inspection: '+$name)
    $before.Add([pscustomobject]@{name=$name;path=$old.path;sha256=$old.sha256})
    $after.Add([pscustomobject]@{name=$name;path=$latest.path;sha256=$latest.sha256})
}
$testAfter=Read-PreloadSource $PSCommandPath
Assert-Preload ($testAfter.sha256 -ceq $testBefore.sha256) 'Test changed during inspection.'
[pscustomobject]@{
    format='stratus-startup-preload-source-contract-tests-v1'
    expected_pairs=$expectedPairs;expected=$expected;executed=$executed;passed=$executed;failed=0;skipped=0
    powershell_version=$PSVersionTable.PSVersion.ToString();powershell_edition=$PSVersionTable.PSEdition
    source_hashes_before=$before.ToArray();source_hashes_after=$after.ToArray()
    test_sha256_before=$testBefore.sha256;test_sha256_after=$testAfter.sha256;source_and_test_hashes_unchanged=$true
    source_tokens_and_directed_inert_mutations_only=$true
    target_compiled=$false;target_loaded=$false;target_lifecycle_executed=$false;native_qualified=$false
    filesystem_fixture_created=$false;csharp_semantics_proven=$false
    independent_preload_descriptor_configured=$false;qualified_preload_open_configured=$false
    l_bootstrap_preload_provenance_proven=$false;runtime_dependency_resolution_qualified=$false
    actual_preload_bytes_validated=$false;physical_ownership_proven=$false;writer_scope_proven=$false
    pre_w_l_stall_bounded=$false;preload_open_authorized=$false;payload_open_authorized=$false
    producer_resume_authorized=$false;compiler_job_root_acknowledged=$false;full_build_accepted=$false
    cases=$results.ToArray()
}|ConvertTo-Json -Depth 6 -Compress
