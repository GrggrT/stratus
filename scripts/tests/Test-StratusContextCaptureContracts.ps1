param([string] $ContextPath)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if([string]::IsNullOrWhiteSpace($ContextPath)){$ContextPath=Join-Path $PSScriptRoot '..\Stratus.RuntimeBuild.ContextCapture.cs'}
# Fixed source-token predicates and in-memory negative mutations only. This does
# not compile, load, evaluate or invoke the C# subject or create a pipe/process.
# Token presence/order is not semantic/native behavior or peer-provenance proof.
# Standalone source predicates only: no MapView, copy, hashing worker or CLR
# subject runs. Passing proves token contracts, not physical mapping semantics.
function Assert-Context {param([bool]$Okay,[string]$Message) if(-not $Okay){throw ('Context capture source contract: '+$Message)}}
function Read-ContextSource {
    param([string]$Path)
    $full=[IO.Path]::GetFullPath($Path);$length=[IO.FileInfo]::new($full).Length
    Assert-Context ($length -gt 0 -and $length -le 1048576) 'Source exceeds the fixed inspection bound.'
    $raw=[IO.File]::ReadAllBytes($full)
    Assert-Context ($raw.LongLength -eq $length) 'Source length changed during read.'
    $text=[Text.UTF8Encoding]::new($false,$true).GetString($raw)
    $hash=[Security.Cryptography.SHA256]::Create()
    try{$digest=[BitConverter]::ToString($hash.ComputeHash($raw)).Replace('-','').ToLowerInvariant()}finally{$hash.Dispose()}
    return [pscustomobject]@{path=$full;text=$text;sha256=$digest}
}
function Tokens {
    param([string]$Text)
    $pattern=@'
//[^\r\n]* | /\*.*?\*/ | @"(?:""|[^"])*" | "(?:\\.|[^"\\])*" | '(?:\\.|[^'\\])*'
| [A-Za-z_][A-Za-z_0-9]* | [0-9]+ | == | != | >= | <= | \+\+ | -- | && | \|\| | => | [^\s]
'@
    $result=[Collections.Generic.List[string]]::new()
    foreach($m in [regex]::Matches($Text,$pattern,[Text.RegularExpressions.RegexOptions]::Singleline -bor [Text.RegularExpressions.RegexOptions]::IgnorePatternWhitespace)){
        if(-not $m.Value.StartsWith('//') -and -not $m.Value.StartsWith('/*')){$result.Add($m.Value)}
    }
    return ,$result.ToArray()
}
function Find {
    param([string[]]$Source,[string]$Sequence)
    $wanted=Tokens $Sequence;Assert-Context ($wanted.Count -gt 0) 'Empty predicate.'
    $found=[Collections.Generic.List[int]]::new()
    for($i=0;$i -le $Source.Count-$wanted.Count;$i++){
        $same=$true
        for($j=0;$j -lt $wanted.Count;$j++){if($Source[$i+$j] -cne $wanted[$j]){$same=$false;break}}
        if($same){$found.Add($i)}
    }
    return ,$found.ToArray()
}
function Has {param([string[]]$Source,[string]$Sequence,[int]$Count=1) Assert-Context ((Find $Source $Sequence).Count -eq $Count) ('Sequence differs: '+$Sequence)}
function Order {
    param([string[]]$Source,[string[]]$Sequences)
    $last=-1
    foreach($sequence in $Sequences){$found=Find $Source $sequence;Assert-Context ($found.Count -eq 1 -and $found[0] -gt $last) ('Order differs: '+$sequence);$last=$found[0]}
}
function Block {
    param([string[]]$Source,[string]$Signature,[switch]$Direct)
    $found=Find $Source $Signature
    if($Direct){
        $directMatches=[Collections.Generic.List[int]]::new();$depth=0
        for($i=0;$i -lt $Source.Count;$i++){
            if($depth -eq 1 -and $i -in $found){$directMatches.Add($i)}
            if($Source[$i] -ceq '{'){$depth++}elseif($Source[$i] -ceq '}'){$depth--}
        }
        $found=$directMatches.ToArray()
    }
    Assert-Context ($found.Count -eq 1) ('Missing/ambiguous scoped block: '+$Signature)
    $open=$found[0]
    while($open -lt $Source.Count -and $Source[$open] -cne '{'){$open++}
    Assert-Context ($open -lt $Source.Count) 'Opening brace missing.'
    $depth=0
    for($i=$open;$i -lt $Source.Count;$i++){
        if($Source[$i] -ceq '{'){$depth++}elseif($Source[$i] -ceq '}'){$depth--}
        if($depth -eq 0){return ,([string[]]$Source[$found[0]..$i])}
    }
    throw 'Context capture source contract: Unbalanced source block.'
}
function Method {param([string[]]$Class,[string]$Signature) return ,(Block $Class $Signature -Direct)}
function Replace-Tokens {
    param([string[]]$Source,[string]$Needle,[string]$Replacement)
    $at=Find $Source $Needle;Assert-Context ($at.Count -eq 1) ('Mutation anchor differs: '+$Needle)
    $old=Tokens $Needle;$new=Tokens $Replacement;$result=[Collections.Generic.List[string]]::new()
    for($i=0;$i -lt $at[0];$i++){$result.Add($Source[$i])}
    foreach($token in $new){$result.Add($token)}
    for($i=$at[0]+$old.Count;$i -lt $Source.Count;$i++){$result.Add($Source[$i])}
    return ,$result.ToArray()
}
function Move-Tokens {
    param([string[]]$Source,[string]$Needle,[string]$Before)
    $without=Replace-Tokens $Source $Needle '/* removed selected statement */'
    return ,(Replace-Tokens $without $Before ($Needle+' '+$Before))
}
$subject=Read-ContextSource $ContextPath
$testSource=Read-ContextSource $PSCommandPath
$class=Block (Tokens $subject.text) 'internal sealed class ContextCapture'
$blocks=@{
    class=$class
    decimal=Method $class 'private static ulong Decimal('
    handle=Method $class 'private static IntPtr Handle('
    constructor=Method $class 'private ContextCapture('
    prepare=Method $class 'internal static ContextCapture Prepare('
    start=Method $class 'internal void Start('
    capture=Method $class 'private void Capture('
    healthy=Method $class 'internal void AssertHealthy('
    complete=Block $class 'internal bool IsComplete'
    copy=Method $class 'internal byte[] CopyBytes('
    metadata=Block $class 'internal ContextMetadata AdmittedMetadata'
}
$rows=@(
    @('receiver_is_internal_sealed_without_authorizing_public_API','class',{
        param($s)
        Has $s 'internal sealed class ContextCapture'
        Has $s 'public' 0
    },'internal sealed class ContextCapture','public sealed class ContextCapture'),
    @('fixed_eight_MiB_bound_is_not_caller_adjustable','class',{
        param($s)
        Has $s 'internal const int MaximumBytes = 8388608;'
    },'MaximumBytes = 8388608','MaximumBytes = 134217728'),
    @('canonical_positive_decimal_is_bounded_before_handle_or_length_use','decimal',{
        param($s)
        Has $s 'text != null && text.Length > 0 && text.Length <= 20'
        Has $s 'UInt64.TryParse(text, NumberStyles.None, CultureInfo.InvariantCulture, out value)'
        Has $s 'Require(value > 0 && value.ToString(CultureInfo.InvariantCulture) == text);'
    },'value > 0','value >= 0'),
    @('handle_matches_unsigned_x86_range_and_excludes_pseudo_values','handle',{
        param($s)
        Has $s 'Require(IntPtr.Size == 8 ? value <= Int64.MaxValue : value < UInt32.MaxValue);'
        Has $s 'new IntPtr(unchecked((int)(uint)value))'
        Has $s 'Require(handle != new IntPtr(-2) && handle != new IntPtr(-3) && handle != new IntPtr(-4) && handle != new IntPtr(-5) && handle != new IntPtr(-6));'
    },'value < UInt32.MaxValue','value <= UInt32.MaxValue'),
    @('prepare_checks_fixed_length_and_exact_lowercase_digest','prepare',{
        param($s)
        Order $s @('ulong count = Decimal(lengthText);','Require(count <= MaximumBytes && sha256 != null && sha256.Length == 64);','return new ContextCapture(handle, (int)count, sha256, aliases);')
        Has $s "foreach (char c in sha256) Require((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'));"
    },'count <= MaximumBytes','count <= Int32.MaxValue'),
    @('prepare_excludes_exact_supplied_alias_set','prepare',{
        param($s)
        Has $s 'Require(aliases != null && aliases.Length >= 2 && aliases.Length <= 4);'
        Has $s 'foreach (IntPtr alias in aliases) Require(handle != alias);'
    },'Require(handle != alias);','Require(handle != IntPtr.Zero);'),
    @('constructor_retains_exact_values_and_copies_alias_array','constructor',{
        param($s)
        Has $s 'this.section = section; this.length = length; expectedSha256 = sha256;'
        Has $s 'forbiddenAliases = (IntPtr[])aliases.Clone();'
    },'forbiddenAliases = (IntPtr[])aliases.Clone();','forbiddenAliases = aliases;'),
    @('prepare_is_managed_only_before_retention','prepare',{
        param($s)
        foreach($bad in @('MapViewOfFile(','UnmapViewOfFile(','GetStdHandle(','GetHandleInformation(','SetHandleInformation(','new Thread(','Marshal.Copy(','SHA256.Create(','File.')){Has $s $bad 0}
    },'IntPtr handle = Handle(handleText);','GetStdHandle(-10); IntPtr handle = Handle(handleText);'),
    @('start_attempt_is_atomic_before_thread_creation','start',{
        param($s)
        Order $s @('try { AssertHealthy();','Require(Interlocked.CompareExchange(ref startAttempted, 1, 0) == 0);','worker = new Thread(delegate() { Capture(); });')
    },'Interlocked.CompareExchange(ref startAttempted, 1, 0) == 0','Interlocked.CompareExchange(ref startAttempted, 0, 0) == 0'),
    @('retained_background_thread_precedes_actual_start','start',{
        param($s)
        Order $s @('worker = new Thread(delegate() { Capture(); });','worker.IsBackground = true;','worker.Start();')
        Has $s 'new Thread(' 1
    },'worker.IsBackground = true;','worker.IsBackground = false;'),
    @('thread_start_failure_is_sticky_and_propagated','start',{
        param($s)
        Has $s 'catch (Exception) { Interlocked.Exchange(ref failed, 1); throw; }'
    },'Interlocked.Exchange(ref failed, 1);','Interlocked.Exchange(ref failed, 0);'),
    @('worker_rejects_all_standard_and_supplied_aliases_before_map','capture',{
        param($s)
        Order $s @('Require(section != GetStdHandle(-10) && section != GetStdHandle(-11) && section != GetStdHandle(-12));','foreach (IntPtr alias in forbiddenAliases) Require(section != alias);','mapAttempted = true;')
    },'section != GetStdHandle(-12)','section != IntPtr.Zero'),
    @('worker_clears_and_rechecks_original_inheritance_before_mapping','capture',{
        param($s)
        Order $s @('Require(GetHandleInformation(section, out flags) && (flags & 1) == 1 && (flags & 2) == 0);','Require(SetHandleInformation(section, 1, 0));','Require(GetHandleInformation(section, out flags) && (flags & 3) == 0);','mapAttempted = true;')
    },'Require(SetHandleInformation(section, 1, 0));','/* inheritance retained */'),
    @('map_attempt_and_return_outcomes_precede_any_view_use','capture',{
        param($s)
        Order $s @('mapAttempted = true;','view = MapViewOfFile(section, 4, 0, 0, new UIntPtr((uint)length));','mapSucceeded = view != IntPtr.Zero; mapReturned = true;','Require(mapAttempted && mapReturned && mapSucceeded);','byte[] privateBytes = new byte[length];')
    },'Require(mapAttempted && mapReturned && mapSucceeded);','Require(mapAttempted);'),
    @('mapping_is_read_only_zero_offset_and_exact_declared_size','capture',{
        param($s)
        Has $s 'view = MapViewOfFile(section, 4, 0, 0, new UIntPtr((uint)length));'
        Has $s 'MapViewOfFile(' 1
    },'MapViewOfFile(section, 4, 0, 0,','MapViewOfFile(section, 6, 0, 0,'),
    @('copy_reads_original_view_into_private_exact_length_array','capture',{
        param($s)
        Order $s @('byte[] privateBytes = new byte[length];','Marshal.Copy(view, privateBytes, 0, length);','unmapAttempted = true;')
    },'Marshal.Copy(view, privateBytes, 0, length);','Marshal.Copy(view, privateBytes, 0, length - 1);'),
    @('unmap_outcomes_are_recorded_before_view_clear','capture',{
        param($s)
        Order $s @('unmapAttempted = true;','bool unmapped = UnmapViewOfFile(view);','unmapSucceeded = unmapped; unmapReturned = true;','Require(unmapAttempted && unmapReturned && unmapSucceeded);','view = IntPtr.Zero;')
    },'unmapSucceeded = unmapped;','unmapSucceeded = true;'),
    @('unknown_unmap_cannot_skip_guard_or_retry_from_finally','capture',{
        param($s)
        Has $s 'UnmapViewOfFile(' 1
        Has $s 'finally' 0
        Has $s 'Require(unmapAttempted && unmapReturned && unmapSucceeded);'
    },'Require(unmapAttempted && unmapReturned && unmapSucceeded);','Require(unmapAttempted);'),
    @('same_count_private_publish_cannot_precede_confirmed_unmap','capture',{
        param($s)
        Order $s @('Require(unmapAttempted && unmapReturned && unmapSucceeded);','view = IntPtr.Zero;','hash.ComputeHash(privateBytes)','captured = privateBytes;','Interlocked.Exchange(ref complete, 1);')
    },'captured = privateBytes;','Require(unmapAttempted && unmapReturned && unmapSucceeded);','move'),
    @('hash_is_computed_over_private_actual_bytes_not_handle_metadata','capture',{
        param($s)
        Has $s 'digest = BitConverter.ToString(hash.ComputeHash(privateBytes)).Replace("-", "").ToLowerInvariant();'
        Has $s 'Require(privateBytes.Length == length && String.Equals(digest, expectedSha256, StringComparison.Ordinal));'
    },'hash.ComputeHash(privateBytes)','hash.ComputeHash(new byte[length])'),
    @('digest_check_and_fresh_health_precede_release_publication','capture',{
        param($s)
        Has $s 'Require(privateBytes.Length == length && String.Equals(digest, expectedSha256, StringComparison.Ordinal)); AssertHealthy(); captured = privateBytes;'
        Order $s @('captured = privateBytes;','metadata = ContextMetadata.ParseAndDerive(privateBytes);','ContextTrust.DemandAdmission(metadata);','Interlocked.Exchange(ref complete, 1);')
    },'String.Equals(digest, expectedSha256, StringComparison.Ordinal)','true'),
    @('worker_exception_latches_failure_without_cleanup_retry','capture',{
        param($s)
        Has $s 'catch (Exception) { Interlocked.Exchange(ref failed, 1); }'
        Has $s 'CloseHandle(' 0
        Has $s '.Dispose(' 0
    },'catch (Exception) { Interlocked.Exchange(ref failed, 1); }','catch (Exception) { Interlocked.Exchange(ref complete, 1); }'),
    @('completion_read_is_guarded_on_both_sides','complete',{
        param($s)
        Order $s @('AssertHealthy(); bool value = Interlocked.CompareExchange(ref complete, 0, 0) == 1;','AssertHealthy(); return value;')
    },'AssertHealthy(); return value;','return value;'),
    @('raw_bytes_escape_only_as_defensive_copy_after_completion','copy',{
        param($s)
        Order $s @('Require(IsComplete);','byte[] result = (byte[])captured.Clone();','AssertHealthy();','return result;')
    },'byte[] result = (byte[])captured.Clone();','byte[] result = captured;'),
    @('failure_state_is_observed_atomically','healthy',{
        param($s)
        Has $s 'Require(Interlocked.CompareExchange(ref failed, 0, 0) == 0);'
    },'== 0','>= 0'),
    @('receiver_has_no_clock_renewal_native_close_or_child_authority','class',{
        param($s)
        foreach($bad in @('GetTickCount64(','Stopwatch','CloseHandle(','OpenFileMapping(','CreateFileMapping(','OpenProcess(','CreateProcess(','ResumeThread(','TerminateProcess(','TerminateJobObject(','.Join(','.Abort(','.Dispose(')){Has $s $bad 0}
        Has $s 'complete, 0, 1' 0
        Has $s 'failed = 0' 0
    },'internal void Start() {','internal void Start() { CloseHandle(section);'),
    @('metadata_is_derived_only_from_actual_private_worker_bytes','capture',{
        param($s)
        Order $s @('Require(privateBytes.Length == length && String.Equals(digest, expectedSha256, StringComparison.Ordinal));','captured = privateBytes;','metadata = ContextMetadata.ParseAndDerive(privateBytes);')
        Has $s 'ContextMetadata.ParseAndDerive(' 1
    },'ContextMetadata.ParseAndDerive(privateBytes)','ContextMetadata.ParseAndDerive(new byte[length])'),
    @('independent_admission_cannot_be_skipped_after_semantic_derivation','capture',{
        param($s)
        Has $s 'metadata = ContextMetadata.ParseAndDerive(privateBytes); AssertHealthy(); ContextTrust.DemandAdmission(metadata); AssertHealthy(); Interlocked.Exchange(ref complete, 1);'
    },'ContextTrust.DemandAdmission(metadata);','/* no independent root */'),
    @('retained_metadata_only_escapes_after_admission_completion','metadata',{
        param($s)
        Order $s @('Require(IsComplete && metadata != null);','ContextMetadata result = metadata;','AssertHealthy();','return result;')
    },'Require(IsComplete && metadata != null);','Require(metadata != null);'),
    @('metadata_worker_has_no_candidate_verifier_or_caller_approval','class',{
        param($s)
        Has $s 'private ContextMetadata metadata;'
        Has $s 'ContextTrust.DemandAdmission(metadata);' 1
        foreach($bad in @('Func<','Action<','approved = true','Environment.','Assembly.Load','Activator.','File.','BuildInputLeaseSet.')){Has $s $bad 0}
    },'private ContextMetadata metadata;','internal ContextMetadata metadata;')
)
$expected=60
Assert-Context ($rows.Count -eq 30) 'Fixed capture source pair count differs.'
$executed=0;$passed=0;$results=[Collections.Generic.List[object]]::new()
foreach($row in $rows){
    $name=$row[0];$selected=$blocks[$row[1]];$check=$row[2]
    $executed++; & $check $selected; $passed++
    $results.Add([pscustomobject]@{name=$name;variant='original_source';passed=$true})
    if($row.Count -eq 6){$mutated=Move-Tokens $selected $row[3] $row[4]}else{$mutated=Replace-Tokens $selected $row[3] $row[4]}
    $executed++;$rejected=$false
    try{& $check $mutated}catch{
        Assert-Context ($_.Exception.Message.StartsWith('Context capture source contract:',[StringComparison]::Ordinal)) ('Unexpected negative-control exception: '+$name)
        $rejected=$true
    }
    Assert-Context $rejected ('The predicate accepted its negative mutation: '+$name)
    $passed++;$results.Add([pscustomobject]@{name=$name;variant='mutated_source_rejected';passed=$true})
}
Assert-Context ($executed -eq $expected -and $passed -eq $expected) 'Fixed capture cases did not complete.'
$subjectAfter=Read-ContextSource $ContextPath;$testAfter=Read-ContextSource $PSCommandPath
Assert-Context ($subjectAfter.sha256 -ceq $subject.sha256 -and $testAfter.sha256 -ceq $testSource.sha256) 'Source/test bytes changed during inspection.'
[pscustomobject]@{
    format='stratus-context-capture-source-contract-tests-v1'
    expected=$expected;executed=$executed;passed=$passed;failed=0
    powershell_version=$PSVersionTable.PSVersion.ToString();powershell_edition=$PSVersionTable.PSEdition
    context_capture_source_sha256=$subject.sha256;test_sha256=$testSource.sha256;source_hashes_unchanged=$true
    source_tokens_and_directed_mutations_only=$true
    target_compiled=$false;target_loaded=$false;target_lifecycle_executed=$false;native_qualified=$false
    section_provenance_proven=$false;exclusive_writer_retirement_proven=$false;context_schema_approved=$false
    mapping_cleanup_native_behavior_proven=$false;payload_open_authorized=$false;producer_resume_authorized=$false;full_build_accepted=$false
    cases=$results.ToArray()
}|ConvertTo-Json -Depth 6 -Compress
