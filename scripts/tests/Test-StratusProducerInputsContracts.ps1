param()
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
# Fixed source-token predicates and directed inert mutations only. These four
# C# files are read as bounded UTF8 data, never compiled, loaded or executed.
# No input owner, native operation, filesystem fixture or runtime parser runs.
# Passing cannot prove actual byte ownership, supervision, or permission to Open.
function Assert-Phase {param([bool]$Okay,[string]$Message) if(-not $Okay){throw ('Producer input source contract: '+$Message)}}
function Read-PhaseSource {
    param([string]$Path)
    $full=[IO.Path]::GetFullPath($Path);$length=[IO.FileInfo]::new($full).Length
    Assert-Phase ($length -gt 0 -and $length -le 1048576) 'Inspection file exceeds one MiB.'
    $bytes=[IO.File]::ReadAllBytes($full);Assert-Phase ($bytes.LongLength -eq $length) 'File length changed during read.'
    $source=[Text.UTF8Encoding]::new($false,$true).GetString($bytes)
    $hash=[Security.Cryptography.SHA256]::Create()
    try{$digest=[BitConverter]::ToString($hash.ComputeHash($bytes)).Replace('-','').ToLowerInvariant()}finally{$hash.Dispose()}
    return [pscustomobject]@{path=$full;text=$source;sha256=$digest}
}
function Tokens {
    param([string]$Text)
    $pattern=@'
//[^\r\n]* | /\*.*?\*/ | @"(?:""|[^"])*" | "(?:\\.|[^"\\])*" | '(?:\\.|[^'\\])*'
| [A-Za-z_][A-Za-z_0-9]* | [0-9]+ | == | != | >= | <= | \+\+ | -- | && | \|\| | => | [^\s]
'@
    $result=[Collections.Generic.List[string]]::new()
    $options=[Text.RegularExpressions.RegexOptions]::Singleline -bor [Text.RegularExpressions.RegexOptions]::IgnorePatternWhitespace
    foreach($m in [regex]::Matches($Text,$pattern,$options,[TimeSpan]::FromSeconds(5))){
        if(-not $m.Value.StartsWith('//') -and -not $m.Value.StartsWith('/*')){$result.Add($m.Value)}
    }
    Assert-Phase ($result.Count -le 262144) 'Token count exceeds fixed inspection bound.'
    return ,$result.ToArray()
}
function Find {
    param([string[]]$Source,[string]$Sequence)
    $wanted=Tokens $Sequence;Assert-Phase ($wanted.Count -gt 0) 'Empty predicate.'
    $found=[Collections.Generic.List[int]]::new()
    for($i=0;$i -le $Source.Count-$wanted.Count;$i++){
        $same=$true
        for($j=0;$j -lt $wanted.Count;$j++){if($Source[$i+$j] -cne $wanted[$j]){$same=$false;break}}
        if($same){$found.Add($i)}
    }
    return ,$found.ToArray()
}
function Has {param([string[]]$Source,[string]$Sequence,[int]$Count=1) Assert-Phase ((Find $Source $Sequence).Count -eq $Count) ('Sequence differs: '+$Sequence)}
function Order {
    param([string[]]$Source,[string[]]$Sequences)
    $last=-1
    foreach($sequence in $Sequences){$found=Find $Source $sequence;Assert-Phase ($found.Count -eq 1 -and $found[0] -gt $last) ('Order differs: '+$sequence);$last=$found[0]}
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
    Assert-Phase ($found.Count -eq 1) ('Missing or ambiguous scoped block: '+$Signature)
    $open=$found[0]
    while($open -lt $Source.Count -and $Source[$open] -cne '{'){$open++}
    Assert-Phase ($open -lt $Source.Count) 'Opening brace missing.'
    $depth=0
    for($i=$open;$i -lt $Source.Count;$i++){
        if($Source[$i] -ceq '{'){$depth++}elseif($Source[$i] -ceq '}'){$depth--}
        if($depth -eq 0){return ,([string[]]$Source[$found[0]..$i])}
    }
    throw 'Producer input source contract: Unbalanced block.'
}
function Method {param([string[]]$Class,[string]$Signature) return ,(Block $Class $Signature -Direct)}
function Replace-Tokens {
    param([string[]]$Source,[string]$Needle,[string]$Replacement)
    $at=Find $Source $Needle;Assert-Phase ($at.Count -eq 1) ('Mutation anchor differs: '+$Needle)
    $old=Tokens $Needle;$new=Tokens $Replacement;$result=[Collections.Generic.List[string]]::new()
    for($i=0;$i -lt $at[0];$i++){$result.Add($Source[$i])}
    foreach($token in $new){$result.Add($token)}
    for($i=$at[0]+$old.Count;$i -lt $Source.Count;$i++){$result.Add($Source[$i])}
    return ,$result.ToArray()
}
$sources=[ordered]@{}
foreach($name in @('ProducerInputs','ProducerProgram','ProducerInvocation','Inputs')){
    $sources[$name]=Read-PhaseSource ([IO.Path]::Combine($PSScriptRoot,'..',('Stratus.RuntimeBuild.'+$name+'.cs')))
}
$testBefore=Read-PhaseSource $PSCommandPath
$phase=Block (Tokens $sources.ProducerInputs.text) 'internal sealed partial class ProducerInvocation'
$entry=Block (Tokens $sources.ProducerProgram.text) 'internal static class Program'
$invocation=Block (Tokens $sources.ProducerInvocation.text) 'internal sealed partial class ProducerInvocation'
$owner=Block (Tokens $sources.Inputs.text) 'public sealed class BuildInputLeaseSet : IDisposable'
# The main container keeps both classes selectable; only method-local token
# order establishes the retained entry -> Start -> Poll callsite checks.
$blocks=@{
    phase=$phase
    admission=Method $phase 'private void AssertInputAdmission('
    retained=Method $phase 'private void AssertRetainedInputs('
    blocker=Method $phase 'private static void DemandQualifiedPayloadOpen('
    start=Method $phase 'private void StartInputs('
    run=Method $phase 'private void RunInputs('
    verify=Method $phase 'private void VerifyInputSnapshots('
    bytes=Method $phase 'private static void RequireInputBytesEqual('
    hash=Method $phase 'private static string InputBytesHash('
    main=[string[]]@($entry+$invocation)
    prepare=Method $owner 'public static BuildInputLeaseSet Prepare('
}
# The only invoked callbacks are these locally authored literal scriptblocks.
# Source text and mutation strings never become PowerShell commands or code.
$rows=@(
    @('phase_is_private_partial_with_no_caller_inventory_entry','phase',{
        param($s) Has $s 'internal sealed partial class ProducerInvocation';Has $s 'private void StartInputs()';Has $s 'public' 0;Has $s 'Expected' 0
    },'private void StartInputs()','public void StartInputs()'),
    @('original_context_owner_worker_and_snapshots_are_privately_retained','phase',{
        param($s)
        foreach($sequence in @('private Thread inputWorker;','private Stratus.RuntimeBuild.ContextCapture payloadContextCapture;','private Stratus.RuntimeBuild.ContextMetadata payloadMetadata;','private Stratus.RuntimeBuild.ContextInputRecord[] payloadInputRecords;','private Stratus.RuntimeBuild.BuildInputLeaseSet payloadInputs;','private Stratus.RuntimeBuild.BuildInputSnapshot[] payloadSnapshots;')){Has $s $sequence}
    },'private Stratus.RuntimeBuild.BuildInputLeaseSet payloadInputs;','internal Stratus.RuntimeBuild.BuildInputLeaseSet payloadInputs;'),
    @('admission_requires_complete_context_and_all_three_private_flags','admission',{
        param($s) Has $s 'AssertOriginal();';Has $s 'Require(contextCapture != null && contextCapture.IsComplete);';Has $s 'Require(Interlocked.CompareExchange(ref helloReceived, 0, 0) == 1 && Interlocked.CompareExchange(ref responseWritten, 0, 0) == 1 && Interlocked.CompareExchange(ref guardianAckReceived, 0, 0) == 1);';Has $s 'AssertOwnIdentity(); AssertDeadline(); AssertClean();' 2;Has $s 'contextCapture.AssertHealthy();'
    },'Interlocked.CompareExchange(ref guardianAckReceived, 0, 0) == 1','true'),
    @('retained_owner_check_binds_all_original_object_references','retained',{
        param($s) Has $s 'AssertInputAdmission();' 2;Has $s 'Require(inputStartAttempted && originalCapture != null && originalMetadata != null && originalRecords != null && originalInputs != null);'
        Has $s 'Require(Object.ReferenceEquals(contextCapture, originalCapture) && Object.ReferenceEquals(payloadContextCapture, originalCapture) && Object.ReferenceEquals(payloadMetadata, originalMetadata) && Object.ReferenceEquals(payloadInputRecords, originalRecords) && Object.ReferenceEquals(payloadInputs, originalInputs) && Object.ReferenceEquals(originalCapture.AdmittedMetadata, originalMetadata));'
    },'Object.ReferenceEquals(payloadInputs, originalInputs)','true'),
    @('qualified_payload_open_blocker_is_unconditional_private_throw','blocker',{
        param($s) Has $s 'private static void DemandQualifiedPayloadOpen() { throw new InvalidOperationException("Payload Open requires separately qualified P/W supervision and execution scope."); }'
    },'throw new InvalidOperationException("Payload Open requires separately qualified P/W supervision and execution scope.");','return;'),
    @('input_attempt_is_single_use_before_metadata_and_owner_creation','start',{
        param($s) Order $s @('AssertInputAdmission(); Require(!inputStartAttempted && inputWorker == null && payloadContextCapture == null && payloadMetadata == null && payloadInputRecords == null && payloadInputs == null && payloadSnapshots == null && Interlocked.CompareExchange(ref inputVerified, 0, 0) == 0);','inputStartAttempted = true;','payloadContextCapture = contextCapture;','payloadMetadata = payloadContextCapture.AdmittedMetadata;','payloadInputRecords = payloadMetadata.CopyInputs();')
    },'inputStartAttempted = true;','inputStartAttempted = false;'),
    @('input_records_come_only_from_same_admitted_context','start',{
        param($s) Has $s 'Require(Object.ReferenceEquals(contextCapture, payloadContextCapture) && Object.ReferenceEquals(contextCapture.AdmittedMetadata, payloadMetadata));' 2;Has $s 'payloadInputRecords = payloadMetadata.CopyInputs();' 1;Has $s 'Require(payloadInputRecords != null && payloadInputRecords.Length >= 10 && payloadInputRecords.Length <= 104);'
    },'payloadInputRecords = payloadMetadata.CopyInputs();','payloadInputRecords = new Stratus.RuntimeBuild.ContextInputRecord[10];'),
    @('specification_maximum_is_fixed_by_exact_derived_role_position','start',{
        param($s) Has $s 'long maximum = i < 2 ? 262144 : (i == 2 ? 4194304 : (i == 3 ? 2097152 : (i == 6 || i == 7 ? 16777216 : 134217728)));';Has $s 'Require(record != null && record.MaximumBytes == maximum && record.Bytes >= 1 && record.Bytes <= maximum);'
    },'record.MaximumBytes == maximum','record.MaximumBytes >= maximum'),
    @('both_declared_and_whole_owned_aggregate_budgets_are_checked','start',{
        param($s) Has $s 'total = checked(total + record.Bytes); Require(total <= 275251200);';Has $s 'if (i >= 4) { declared = checked(declared + record.Bytes); Require(declared <= 268435456); }'
    },'Require(declared <= 268435456);','/* declared aggregate unchecked */'),
    @('real_specs_and_retained_prepare_precede_worker_allocation','start',{
        param($s) Order $s @('specifications[i] = new Stratus.RuntimeBuild.BuildInputSpec(record.Path, record.Sha256, record.Bytes, maximum);','payloadInputs = Stratus.RuntimeBuild.BuildInputLeaseSet.Prepare(specifications);','Stratus.RuntimeBuild.BuildInputLeaseSet originalInputs = payloadInputs;','inputWorker = new Thread(delegate() {')
    },'payloadInputs = Stratus.RuntimeBuild.BuildInputLeaseSet.Prepare(specifications);','Stratus.RuntimeBuild.BuildInputLeaseSet unretained = Stratus.RuntimeBuild.BuildInputLeaseSet.Prepare(specifications);'),
    @('worker_captures_original_retained_objects_not_later_global_choices','start',{
        param($s) Has $s 'Stratus.RuntimeBuild.ContextCapture originalCapture = payloadContextCapture;';Has $s 'Stratus.RuntimeBuild.ContextMetadata originalMetadata = payloadMetadata;';Has $s 'Stratus.RuntimeBuild.ContextInputRecord[] originalRecords = payloadInputRecords;';Has $s 'inputWorker = new Thread(delegate() { RunInputs(originalCapture, originalMetadata, originalRecords, originalInputs); });'
    },'RunInputs(originalCapture, originalMetadata, originalRecords, originalInputs);','RunInputs(contextCapture, payloadMetadata, payloadInputRecords, payloadInputs);'),
    @('worker_retained_background_before_start_with_reference_guards','start',{
        param($s) Has $s 'inputWorker.IsBackground = true; AssertRetainedInputs(originalCapture, originalMetadata, originalRecords, originalInputs); inputWorker.Start(); AssertRetainedInputs(originalCapture, originalMetadata, originalRecords, originalInputs);';Has $s 'new Thread(' 1;Has $s 'catch (Exception) { RecordFailure(); throw; }'
    },'inputWorker.IsBackground = true;','inputWorker.IsBackground = false;'),
    @('managed_preparation_has_no_open_capture_native_or_dispose_call','start',{
        param($s) foreach($bad in @('.Open(','.CaptureSnapshots(','.Dispose(','.Join(','CreateFile(','File.','Directory.','Process.','DllImport','InputBytesHash(')){Has $s $bad 0}
    },'inputWorker.Start();','payloadInputs.Open(); inputWorker.Start();'),
    @('actual_owner_prepare_is_managed_and_copies_specs','prepare',{
        param($s) Has $s 'copied[i] = new BuildInputSpec(item.Path, item.Sha256, item.Bytes, item.MaximumBytes);';Has $s 'new HashSet<string>(StringComparer.OrdinalIgnoreCase)';Has $s 'if (!paths.Add(copied[i].Path)) throw';Has $s 'return new BuildInputLeaseSet(copied);'
        foreach($bad in @('CreateFile(','File.','Directory.','new FileStream(','.Open(','SHA256.Create(')){Has $s $bad 0}
    },'return new BuildInputLeaseSet(copied);','File.ReadAllBytes("candidate"); return new BuildInputLeaseSet(copied);'),
    @('worker_must_cross_unconditional_blocker_immediately_before_open','run',{
        param($s) Has $s 'AssertRetainedInputs(originalCapture, originalMetadata, originalRecords, originalInputs); DemandQualifiedPayloadOpen(); originalInputs.Open(); AssertRetainedInputs(originalCapture, originalMetadata, originalRecords, originalInputs);';Has $s 'originalInputs.Open();' 1
    },'DemandQualifiedPayloadOpen();','/* qualification blocker skipped */'),
    @('actual_snapshots_are_obtained_only_from_same_original_open_owner','run',{
        param($s) Order $s @('originalInputs.Open();','payloadSnapshots = originalInputs.CaptureSnapshots();','Stratus.RuntimeBuild.BuildInputSnapshot[] originalSnapshots = payloadSnapshots;','VerifyInputSnapshots(originalCapture, originalMetadata, originalRecords, originalInputs, originalSnapshots);')
    },'payloadSnapshots = originalInputs.CaptureSnapshots();','payloadSnapshots = payloadInputs.CaptureSnapshots();'),
    @('actual_snapshot_validation_and_final_hold_precede_verified_flag','run',{
        param($s) Has $s 'VerifyInputSnapshots(originalCapture, originalMetadata, originalRecords, originalInputs, originalSnapshots); originalInputs.AssertHeld(); AssertRetainedInputs(originalCapture, originalMetadata, originalRecords, originalInputs); Require(Object.ReferenceEquals(payloadSnapshots, originalSnapshots)); Interlocked.Exchange(ref inputVerified, 1);'
    },'originalInputs.AssertHeld();','/* final owner hold omitted */'),
    @('worker_failure_is_sticky_without_cleanup_or_retry','run',{
        param($s) Has $s 'catch (Exception) { RecordFailure(); }';Has $s 'RunInputs(' 1;foreach($bad in @('finally','.Dispose(','.Close(','.Join(','OpenProcess(','Process.')){Has $s $bad 0}
    },'catch (Exception) { RecordFailure(); }','catch (Exception) { Interlocked.Exchange(ref inputVerified, 1); }'),
    @('snapshot_count_and_retained_array_reference_must_match','verify',{
        param($s) Has $s 'Require(originalSnapshots != null && originalSnapshots.Length == originalRecords.Length && Object.ReferenceEquals(payloadSnapshots, originalSnapshots));'
    },'originalSnapshots.Length == originalRecords.Length','originalSnapshots.Length >= originalRecords.Length'),
    @('each_snapshot_binds_exact_derived_path_before_actual_bytes','verify',{
        param($s) Order $s @('Stratus.RuntimeBuild.ContextInputRecord expected = originalRecords[i];','Stratus.RuntimeBuild.BuildInputSnapshot snapshot = originalSnapshots[i];','Require(snapshot != null && String.Equals(snapshot.Path, expected.Path, StringComparison.Ordinal));','byte[] actualBytes = snapshot.Bytes;')
    },'String.Equals(snapshot.Path, expected.Path, StringComparison.Ordinal)','String.Equals(snapshot.Path, expected.Path, StringComparison.OrdinalIgnoreCase)'),
    @('actual_snapshot_byte_count_matches_spec_length_and_role_bound','verify',{
        param($s) Has $s 'Require(actualBytes != null && actualBytes.LongLength == expected.Bytes && actualBytes.LongLength <= expected.MaximumBytes && snapshot.Length == actualBytes.LongLength);'
    },'actualBytes.LongLength == expected.Bytes','actualBytes.LongLength >= expected.Bytes'),
    @('hash_recomputed_from_actual_snapshot_bytes_not_copied_hash_field','verify',{
        param($s) Has $s 'Require(String.Equals(InputBytesHash(actualBytes), expected.Sha256, StringComparison.Ordinal));';Has $s 'snapshot.Sha256' 0
    },'InputBytesHash(actualBytes)','snapshot.Sha256'),
    @('profile_actual_bytes_equal_private_original_component_bytes','verify',{
        param($s) Has $s 'if (i == 0) RequireInputBytesEqual(actualBytes, originalMetadata.CopyProfileBytes());'
    },'originalMetadata.CopyProfileBytes()','actualBytes'),
    @('plan_actual_bytes_equal_private_original_component_bytes','verify',{
        param($s) Has $s 'else if (i == 1) RequireInputBytesEqual(actualBytes, originalMetadata.CopyPlanBytes());'
    },'originalMetadata.CopyPlanBytes()','actualBytes'),
    @('manifest_actual_bytes_equal_private_original_component_bytes','verify',{
        param($s) Has $s 'else if (i == 2) RequireInputBytesEqual(actualBytes, originalMetadata.CopyManifestBytes());';Has $s 'AssertRetainedInputs(originalCapture, originalMetadata, originalRecords, originalInputs);' 2
    },'originalMetadata.CopyManifestBytes()','actualBytes'),
    @('byte_correspondence_compares_every_byte_and_exact_length','bytes',{
        param($s) Has $s 'Require(actual != null && expected != null && actual.Length == expected.Length);';Has $s 'for (int i = 0; i < actual.Length; ++i) Require(actual[i] == expected[i]);'
    },'actual[i] == expected[i]','actual[i] == actual[i]'),
    @('digest_uses_sha256_over_supplied_actual_bytes','hash',{
        param($s) Has $s 'using (SHA256 hash = SHA256.Create())';Has $s 'return BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();'
    },'hash.ComputeHash(bytes)','hash.ComputeHash(new byte[0])'),
    @('input_phase_no_public_owner_export_compiler_or_permission_route','phase',{
        param($s) foreach($bad in @('public','ResumeThread(','CreateProcess(','CompilerCommand.','RegisterCommand(','RegisterGuardian(','.Dispose(','.Join(','File.','Directory.','Environment.','Assembly.','Activator.','DllImport','return 0;','ContextTrust.')){Has $s $bad 0}
    },'private void StartInputs() {','private void StartInputs() { ResumeThread(IntPtr.Zero);'),
    @('single_verified_publication_cannot_reset_or_become_main_success','phase',{
        param($s) Has $s 'Interlocked.Exchange(ref inputVerified, 1);' 1;Has $s 'inputVerified = 0' 0;Has $s 'inputStartAttempted = false' 0;Has $s 'inputStartAttempted = true;' 1
    },'inputStartAttempted = true;','inputStartAttempted = false;'),
    @('main_starts_phase_only_after_response_and_ack_and_returns_diagnostic','main',{
        param($s)
        $poll=Method (Block $s 'internal sealed partial class ProducerInvocation') 'internal void Poll('
        Has $poll 'if (!inputStartAttempted && Interlocked.CompareExchange(ref responseWritten, 0, 0) == 1 && Interlocked.CompareExchange(ref guardianAckReceived, 0, 0) == 1) { StartInputs(); }'
        Has $poll 'if (Interlocked.CompareExchange(ref inputVerified, 0, 0) == 1 && !compilerStartAttempted) { AssertInputAdmission(); StartCompiler(); }'
        $main=Method (Block $s 'internal static class Program') 'private static int Main('
        Order $main @('invocation = Stratus.RuntimeBuild.ProducerInvocation.PrepareFromInheritedEntry(args);','invocation.Start();','invocation.Poll();')
        Has $main 'if (invocation != null) invocation.RecordFailure(); return 205;';Has $s 'return 0;' 0;Has $s 'return 202;' 0
    },'Interlocked.CompareExchange(ref guardianAckReceived, 0, 0) == 1','true'),
    @('input_phase_cannot_restart_original_deadline_or_join_watcher','phase',{
        param($s) foreach($bad in @('startedTick =','deadlineTick =','previousTick =','GetTickCount64(','Stopwatch.StartNew(','WaitForSingleObject(','Thread.Sleep(','.Join(')){Has $s $bad 0}
    },'inputStartAttempted = true;','inputStartAttempted = true; startedTick = GetTickCount64();'),
    @('guarded_phase_does_not_install_approval_or_offer_caller_bypass','phase',{
        param($s) Has $s 'private static void DemandQualifiedPayloadOpen()';Has $s 'DemandQualifiedPayloadOpen();' 1;foreach($bad in @('Func<','Action<','bool approved','bool qualified','Environment.','SelectIndependentDescriptor','Install')){Has $s $bad 0}
    },'private static void DemandQualifiedPayloadOpen()','private static void DemandQualifiedPayloadOpen(bool approved)')
)
$expectedPairs=32;$expected=64
Assert-Phase ($rows.Count -eq $expectedPairs -and $expected -eq 2*$expectedPairs) 'Fixed paired inventory differs.'
$executed=0;$results=[Collections.Generic.List[object]]::new()
foreach($row in $rows){
    Assert-Phase ($row.Count -eq 5) 'Row shape differs.'
    $selected=$blocks[$row[1]];$check=$row[2]
    & $check $selected;$executed++;$results.Add([pscustomobject]@{name=$row[0];variant='original_source';passed=$true})
    $mutated=Replace-Tokens $selected $row[3] $row[4];$rejected=$false
    try{& $check $mutated}catch{
        Assert-Phase ($_.Exception.Message.StartsWith('Producer input source contract:',[StringComparison]::Ordinal)) ('Unexpected mutation exception: '+$row[0])
        $rejected=$true
    }
    Assert-Phase $rejected ('Weakening mutation survived: '+$row[0])
    $executed++;$results.Add([pscustomobject]@{name=$row[0];variant='mutated_source_rejected';passed=$true})
}
Assert-Phase ($executed -eq $expected -and $results.Count -eq $expected) 'Fixed cases did not complete.'
$before=[Collections.Generic.List[object]]::new();$after=[Collections.Generic.List[object]]::new()
foreach($name in $sources.Keys){
    $old=$sources[$name];$latest=Read-PhaseSource $old.path
    Assert-Phase ($latest.sha256 -ceq $old.sha256) ('Source changed: '+$name)
    $before.Add([pscustomobject]@{name=$name;path=$old.path;sha256=$old.sha256})
    $after.Add([pscustomobject]@{name=$name;path=$latest.path;sha256=$latest.sha256})
}
$testAfter=Read-PhaseSource $PSCommandPath
Assert-Phase ($testAfter.sha256 -ceq $testBefore.sha256) 'Test changed during inspection.'
[pscustomobject]@{
    format='stratus-producer-inputs-source-contract-tests-v1'
    expected_pairs=$expectedPairs;expected=$expected;executed=$executed;passed=$executed;failed=0;skipped=0
    powershell_version=$PSVersionTable.PSVersion.ToString();powershell_edition=$PSVersionTable.PSEdition
    source_hashes_before=$before.ToArray();source_hashes_after=$after.ToArray()
    test_sha256_before=$testBefore.sha256;test_sha256_after=$testAfter.sha256;source_and_test_hashes_unchanged=$true
    source_tokens_and_directed_inert_mutations_only=$true
    target_compiled=$false;target_loaded=$false;target_lifecycle_executed=$false;target_parser_executed=$false
    csharp_semantics_proven=$false;native_qualified=$false;filesystem_fixture_created=$false
    independent_approval_root_configured=$false;qualified_payload_open_configured=$false
    physical_input_ownership_proven=$false;actual_snapshot_bytes_validated=$false;watcher_supervision_qualified=$false
    preload_provenance_proven=$false;output_writer_scope_proven=$false;payload_open_authorized=$false
    producer_resume_authorized=$false;compiler_creation_authorized=$false;compiler_job_root_acknowledged=$false
    full_terminal_accepted=$false;full_build_accepted=$false
    cases=$results.ToArray()
}|ConvertTo-Json -Depth 6 -Compress
