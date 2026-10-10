[CmdletBinding()]
param([string] $InputsPath,[string] $BootstrapPath,[string] $AdapterPath)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if (-not $InputsPath) { $InputsPath=Join-Path $PSScriptRoot '..\Stratus.RuntimeBuild.Inputs.cs' }
if (-not $BootstrapPath) { $BootstrapPath=Join-Path $PSScriptRoot '..\Stratus.RuntimeBuild.Bootstrap.cs' }
if (-not $AdapterPath) { $AdapterPath=Join-Path $PSScriptRoot '..\Stratus.RuntimeBuild.InputOwner.ps1' }
# Source inspection only. Subject C# is never compiled/loaded/invoked, and subject
# PowerShell functions are parsed as data, never defined or evaluated. The only
# scriptblocks executed below are the fixed local test predicates in this file.
# Two additional in-memory cases exercise only BCL String.Concat MethodInfo array
# argument packaging; they do not load or invoke any subject type or function.
# Token/block checks are deliberately not a semantic C# or Windows lifecycle proof.
function Assert-Ownership { param([bool] $Okay,[string] $Message) if(-not $Okay){throw ('Ownership source contract: '+$Message)} }
function Read-OwnershipSource {
    param([string] $Path)
    $full=[IO.Path]::GetFullPath($Path);$length=[IO.FileInfo]::new($full).Length
    Assert-Ownership ($length -gt 0 -and $length -le 1048576) 'Inspection file exceeds one MiB.'
    $raw=[IO.File]::ReadAllBytes($full)
    Assert-Ownership ($raw.LongLength -eq $length) 'Inspection file length changed while reading.'
    $text=[Text.UTF8Encoding]::new($false,$true).GetString($raw)
    $hash=[Security.Cryptography.SHA256]::Create()
    try {$digest=[BitConverter]::ToString($hash.ComputeHash($raw)).Replace('-','').ToLowerInvariant()} finally {$hash.Dispose()}
    return [pscustomobject]@{path=$Path;text=$text;sha256=$digest}
}
$script:sourceSyntax='CSharp'
function Tokens {
    param([string] $Text,[ValidateSet('CSharp','PowerShell')][string]$Syntax=$script:sourceSyntax)
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
    foreach($match in [regex]::Matches($Text,$pattern,$options,[TimeSpan]::FromSeconds(5))) {
        $comment=if($Syntax -ceq 'PowerShell'){$match.Value.StartsWith('#') -or $match.Value.StartsWith('<#')}else{$match.Value.StartsWith('//') -or $match.Value.StartsWith('/*') -or $match.Value.StartsWith('#')}
        if(-not $comment) {$result.Add($match.Value)}
    }
    Assert-Ownership ($result.Count -le 262144) 'Token inspection bound exceeded.'
    return ,$result.ToArray()
}
function Find {
    param([string[]] $Source,[string] $Sequence)
    $wanted=Tokens $Sequence
    Assert-Ownership ($wanted.Count -gt 0) 'Empty predicate.'
    $found=[Collections.Generic.List[int]]::new()
    for($i=0;$i -le $Source.Count-$wanted.Count;$i++) {
        $same=$true
        for($j=0;$j -lt $wanted.Count;$j++){if($Source[$i+$j] -cne $wanted[$j]){$same=$false;break}}
        if($same){$found.Add($i)}
    }
    return ,$found.ToArray()
}
function Has { param([string[]] $Source,[string] $Sequence,[int] $Count=1) Assert-Ownership ((Find $Source $Sequence).Count -eq $Count) ('Sequence count differs: '+$Sequence) }
function Order {
    param([string[]] $Source,[string[]] $Sequences)
    $last=-1
    foreach($sequence in $Sequences){$found=Find $Source $sequence;Assert-Ownership ($found.Count -eq 1 -and $found[0] -gt $last) ('Order differs: '+$sequence);$last=$found[0]}
}
function Block {
    param([string[]] $Source,[string] $Signature,[switch] $Direct)
    $found=Find $Source $Signature
    if($Direct){
        $selected=[Collections.Generic.List[int]]::new();$depth=0
        for($i=0;$i -lt $Source.Count;$i++){
            if($depth -eq 1 -and $i -in $found){$selected.Add($i)}
            if($Source[$i] -ceq '{'){$depth++}elseif($Source[$i] -ceq '}'){$depth--}
        }
        $found=$selected.ToArray()
    }
    Assert-Ownership ($found.Count -eq 1) ('Missing/ambiguous scoped block: '+$Signature)
    $open=$found[0]
    while($open -lt $Source.Count -and $Source[$open] -cne '{'){$open++}
    Assert-Ownership ($open -lt $Source.Count) 'Missing opening brace.'
    $depth=0
    for($i=$open;$i -lt $Source.Count;$i++){
        if($Source[$i] -ceq '{'){$depth++}elseif($Source[$i] -ceq '}'){$depth--}
        if($depth -eq 0){return ,([string[]]$Source[$found[0]..$i])}
    }
    throw 'Ownership source contract: Unbalanced block.'
}
function Method { param([string[]] $Class,[string] $Signature) return ,(Block $Class $Signature -Direct) }
function ManagedOnly {
    param([string[]] $Source)
    foreach($call in @('CreateFile(','new FileStream(','File.','Directory.','GetFileInformationByHandle(',
        'GetFinalPathNameByHandle(','GetVolumeInformation(','GetDriveType(','SHA256.Create(',
        'CreateProcess(','new Thread(','new AnonymousPipeServerStream(','Assembly.Load(','Add-Type',
        'CreatePrivateFile(','CreatePipe(','OpenOriginalExecutableLock(','AcquireNativeFile(',
        'PrepareStdioPairs(','OpenOriginalStdioPair(','EnsureCollectors(','EnsureCollector(',
        'new AnonymousPipeClientStream(')){Has $Source $call 0}
}
function AdapterFunction {
    param([string] $Text,[string] $Name)
    $astTokens=$null;$errors=$null
    $ast=[Management.Automation.Language.Parser]::ParseInput($Text,[ref]$astTokens,[ref]$errors)
    Assert-Ownership ($errors.Count -eq 0) 'Adapter AST does not parse.'
    $matches=@($ast.EndBlock.Statements | Where-Object {$_ -is [Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -ceq $Name})
    Assert-Ownership ($matches.Count -eq 1) ('Exact adapter function missing: '+$Name)
    return ,(Tokens $matches[0].Body.Extent.Text)
}
function AdapterRoot {
    param([string] $Text)
    $astTokens=$null;$errors=$null
    $ast=[Management.Automation.Language.Parser]::ParseInput($Text,[ref]$astTokens,[ref]$errors)
    Assert-Ownership ($errors.Count -eq 0 -and $null -eq $ast.ParamBlock -and $null -eq $ast.BeginBlock -and
        $null -eq $ast.ProcessBlock -and $null -eq $ast.DynamicParamBlock -and $ast.UsingStatements.Count -eq 0) 'Adapter has an executable root block.'
    $clean=$ast.PSObject.Properties['CleanBlock']
    Assert-Ownership ($null -eq $clean -or $null -eq $clean.Value) 'Adapter has a clean block.'
    Assert-Ownership ($ast.EndBlock.Statements.Count -eq 4) 'Adapter root count differs.'
    foreach($statement in $ast.EndBlock.Statements){Assert-Ownership ($statement -is [Management.Automation.Language.FunctionDefinitionAst]) 'Adapter has top-level effects.'}
}
# Fixed lexical harness checks are inert data, not additional STRATUS case IDs.
$psLexical=@'
.Split('\'); $value='it''s'; $quoted="a`"b"; $after=1
'@
$psLexicalExpected=@'
.|Split|(|'\'|)|;|$|value|=|'it''s'|;|$|quoted|=|"a`"b"|;|$|after|=|1
'@
Assert-Ownership (((Tokens $psLexical -Syntax PowerShell) -join '|') -ceq $psLexicalExpected) 'PowerShell literal lexer regression.'
$csLexical=@'
char slash = '\\'; char quote = '\''; string value = "a\"b";
'@
$csLexicalExpected=@'
char|slash|=|'\\'|;|char|quote|=|'\''|;|string|value|=|"a\"b"|;
'@
Assert-Ownership (((Tokens $csLexical -Syntax CSharp) -join '|') -ceq $csLexicalExpected) 'CSharp literal lexer regression.'
$testBefore=Read-OwnershipSource $PSCommandPath
$documents=@{I=(Read-OwnershipSource $InputsPath);B=(Read-OwnershipSource $BootstrapPath);P=(Read-OwnershipSource $AdapterPath)}
function Check-Case {
    param([string] $Name,[string] $Text)
    # Fixed local case names choose the grammar for source and every predicate.
    # PowerShell literal backslashes must never escape its single quotes.
    $script:sourceSyntax=if($Name.StartsWith('adapter_')){'PowerShell'}else{'CSharp'}
    $tokens=Tokens $Text
    if($Name.StartsWith('adapter_')){
        switch($Name){
            'adapter_exact_returned_assembly' {
                $fn=AdapterFunction $Text 'Get-StratusBuildInputTypes'
                Order $fn @('$specType=$BootstrapAssembly.GetType(', '$ownerType=$BootstrapAssembly.GetType(', '$snapshotType=$BootstrapAssembly.GetType(', '[object]::ReferenceEquals($type.Assembly,$BootstrapAssembly)', '$abi=$ownerType.GetField(')
                Has $fn "'stratus-runtime-build-inputs-v1'"
            }
            'adapter_factory_only_managed_one_array_argument' {
                $fn=AdapterFunction $Text 'New-StratusBuildInputOwner'
                Order $fn @('$prepare=$types.owner_type.GetMethod(', '$owner=$prepare.Invoke($null,[object[]]@(,$specs))', 'return $owner')
                Has $fn "GetMethod('Open'" 0
                Has $fn '.Open(' 0
                Has $fn '.Dispose(' 0
            }
            'adapter_factory_raw_metadata_parameters_only' {
                $astTokens=$null;$errors=$null
                $ast=[Management.Automation.Language.Parser]::ParseInput($Text,[ref]$astTokens,[ref]$errors)
                Assert-Ownership ($errors.Count -eq 0) 'Factory parameter AST differs.'
                $factory=@($ast.EndBlock.Statements | Where-Object {$_ -is [Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -ceq 'New-StratusBuildInputOwner'})
                Assert-Ownership ($factory.Count -eq 1) 'Exact managed factory missing.'
                $names=@($factory[0].Body.ParamBlock.Parameters | ForEach-Object {$_.Name.VariablePath.UserPath})
                $required=@('BootstrapAssembly','ProfileDocument','ProfileBytes','PlanDocument','PlanBytes','SourceManifestPath','SourceManifestBytes','Expected')
                Assert-Ownership ($names.Count -eq $required.Count) 'Raw metadata parameter count differs.'
                for($i=0;$i -lt $required.Count;$i++){Assert-Ownership ($names[$i] -ceq $required[$i]) 'Caller-selected inventory or projection accepted.'}
            }
            'adapter_factory_derivation_precedes_clr_selection' {
                $fn=AdapterFunction $Text 'New-StratusBuildInputOwner'
                Order $fn @('$SpecRecords=Get-StratusParentCommandBuildInputSpecifications', '$types=Get-StratusBuildInputTypes $BootstrapAssembly', '$constructor=$types.spec_type.GetConstructor(')
            }
            'adapter_factory_forwards_all_original_metadata' {
                $fn=AdapterFunction $Text 'New-StratusBuildInputOwner'
                Has $fn 'Get-StratusParentCommandBuildInputSpecifications -ProfileDocument $ProfileDocument -ProfileBytes $ProfileBytes'
                Has $fn '-PlanDocument $PlanDocument -PlanBytes $PlanBytes -SourceManifestPath $SourceManifestPath'
                Has $fn '-SourceManifestBytes $SourceManifestBytes -Expected $Expected'
            }
            'adapter_factory_checks_derived_count_before_clr' {
                $fn=AdapterFunction $Text 'New-StratusBuildInputOwner'
                Order $fn @('$SpecRecords=Get-StratusParentCommandBuildInputSpecifications', "Assert-StratusBuildInputArray `$SpecRecords 10 104 'owned build input specifications'", '$types=Get-StratusBuildInputTypes $BootstrapAssembly')
            }
            'adapter_factory_role_limits_include_producer_and_verifier' {
                $fn=AdapterFunction $Text 'New-StratusBuildInputOwner'
                Has $fn '$maximum=if($i -lt 2){262144}elseif($i -eq 2){4194304}elseif($i -eq 3){2097152}elseif($i -eq 6 -or $i -eq 7){16777216}else{134217728}'
                Has $fn "Assert-StratusCommandRuntimeInteger `$record.maximum_bytes `$maximum `$maximum 'fixed role byte bound'"
            }
            'adapter_factory_validates_each_derived_record_before_constructor' {
                $fn=AdapterFunction $Text 'New-StratusBuildInputOwner'
                Order $fn @('$record=$SpecRecords[$i]', "Assert-StratusCommandRuntimeObject `$record @('path','sha256','bytes','maximum_bytes')", 'Assert-StratusCommandRuntimePath $record.path', 'Assert-StratusCommandRuntimeHash $record.sha256', 'Assert-StratusCommandRuntimeInteger $record.bytes 1 $maximum', '$spec=$constructor.Invoke(')
            }
            'adapter_factory_typed_array_uses_only_derived_inventory' {
                $fn=AdapterFunction $Text 'New-StratusBuildInputOwner'
                Order $fn @('$SpecRecords=Get-StratusParentCommandBuildInputSpecifications', '$specs=[Array]::CreateInstance($types.spec_type,$SpecRecords.Count)', 'for ($i=0;$i -lt $SpecRecords.Count;$i++)', '$specs.SetValue($spec,$i)', '$owner=$prepare.Invoke($null,[object[]]@(,$specs))')
            }
            'adapter_binding_still_calls_full_byte_validator' {
                $fn=AdapterFunction $Text 'Get-StratusOwnedBuildInputBinding'
                Has $fn '$binding=Assert-StratusParentCommandBuildInputs'
                Has $fn '-ProfileBytes $arrays[0]'
                Has $fn '-PlanBytes $arrays[1]'
                Has $fn '-SourceManifestBytes $arrays[2] -SourceBytes $arrays[3]'
                Has $fn '-InputSnapshots $inventory.ToArray() -Expected $Expected'
                Has $fn 'Get-StratusParentCommandBuildPreflight' 0
                Has $fn 'Get-StratusParentCommandBuildInputSpecifications' 0
            }
            'adapter_open_uses_retained_clr_owner' {
                $fn=AdapterFunction $Text 'Open-StratusBuildInputOwner'
                Order $fn @('$types.owner_type.IsInstanceOfType($Owner)', '$open=$types.owner_type.GetMethod(', '$null=$open.Invoke($Owner,[object[]]@())')
                Has $fn '$Owner.Open(' 0
                Has $fn 'Dispose' 0
            }
            'adapter_actual_capture_precedes_v2_and_final_hold' {
                $fn=AdapterFunction $Text 'Get-StratusOwnedBuildInputBinding'
                Order $fn @('$snapshots=$capture.Invoke($Owner,[object[]]@())', '$pathField=$types.snapshot_type.GetField(', '$bytesProperty=$types.snapshot_type.GetProperty(', '$paths.Add([string]$pathField.GetValue($snapshot))', '$arrays.Add([byte[]]$bytesProperty.GetValue($snapshot,$null))', '$binding=Assert-StratusParentCommandBuildInputs', '$null=$held.Invoke($Owner,[object[]]@())', 'return [pscustomobject]')
                Has $fn '-ProfileBytes $arrays[0]'
                Has $fn '-SourceManifestBytes $arrays[2] -SourceBytes $arrays[3]'
            }
            'adapter_no_import_or_path_read_fallback' {
                foreach($call in @('Add-Type','Assembly]::Load','ReadAllBytes(','ReadAllText(','Import-StratusPinnedBuildBootstrap','Get-Content','[Stratus.')){Has $tokens $call 0}
                Has $tokens 'profile_approval_proven=$false;watchdog_armed=$false;output_writer_scope_proven=$false'
                Has $tokens 'compiler_executed=$false;bootstrap_qualified=$false;full_goal_accepted=$false'
            }
            'adapter_root_definitions_only' {AdapterRoot $Text}
            default {throw 'Ownership source contract: Unknown adapter predicate.'}
        }
        return
    }
    if($Name.StartsWith('compiler_') -or $Name.StartsWith('result_')){
        $class=Block $tokens 'public sealed class CompilerCommand : IDisposable'
        switch($Name){
            'compiler_owned_factory_managed_and_retained_registration' {
                $fn=Method $class 'public static CompilerCommand PrepareOwned('
                ManagedOnly $fn
                Order $fn @('Stopwatch clock = Stopwatch.StartNew();','CompilerCommand prepared = new CompilerCommand(', 'inputs.RegisterCommand(prepared);', 'return prepared;')
                Has $class 'private readonly BuildInputLeaseSet ownedInputs;'
                Has (Method $class 'private CompilerCommand(') 'ownedInputs = inputs;'
            }
            'compiler_borrow_before_path_stream' {
                $fn=Method $class 'private void PrepareBirthJobOriginal()'
                Order $fn @('jobPreparationAttempted = true; SuccessBoundary();','ownedExecutable = ownedInputs.BorrowCompilerExecutable(this, executable, expectedHash);','OpenOriginalExecutableLock();', 'HeldExecutableBoundary(); capturedCommandLine = commandLine;', 'byte[] environmentBytes', 'jobAttempted = true;')
                Order (Method $class 'private void OpenOriginalExecutableLock()') @('executableLockOwner = original;','original.Attempted = true;','original.Slot.Raw = CreatePrivateFile(','slot.Wrapper = new SafeFileHandle(slot.Raw, false);','slot.Stream = new FileStream(')
            }
            'compiler_owned_native_identity_not_path_only' {
                $fn=Method $class 'private void HeldExecutableBoundary()'
                Order $fn @('FileStream suppliedStream = ownedInputs == null ? suppliedExecutable : ownedInputs.AssertCompilerExecutableBorrow(this, ownedExecutable);', 'supplied = ownedInputs == null ? HeldInformation(suppliedStream) : HeldInputExecutableInformation(suppliedStream);','FileStream executableLock = BorrowOriginalExecutableLock();','FileInformation before = HeldOwnedExecutableInformation(executableLock);','if (!SameFile(supplied, before))')
                Has (Method $class 'private FileInformation HeldInputExecutableInformation(') '!Object.ReferenceEquals(ownedInputs.AssertCompilerExecutableBorrow(this, ownedExecutable), exactStream)'
            }
            'compiler_admission_sandwiches_input_hold' {
                $fn=Method $class 'private void SuccessBoundary()'
                Has $fn 'ClockAndFaultBoundary(); AssertOwnedInputsHeld(); ClockAndFaultBoundary();'
                $hold=Method $class 'private void AssertOwnedInputsHeld()'
                Order $hold @('if (ownedInputs == null) return;','if (ownedInputsReleased) throw','ownedInputs.AssertHeld();')
            }
            'compiler_cached_result_still_checks_input_owner' {
                $fn=Method $class 'public CompilerResult PollResult()'
                Order $fn @('SuccessState(); if (producerCompilerOwner != null) ClockAndFaultBoundary();','AssertOwnedInputsHeld(); SuccessState();','if (completedResult != null) { if (producerCompilerOwner != null) ClockAndFaultBoundary(); return completedResult; }')
            }
            'compiler_start_boundary_immediately_precedes_native_create' {
                $generic=Method $class 'public void StartSuspended()'
                Order $generic @('Enter();','if (producerCompilerOwner != null)','StartSuspendedOriginal(null);','catch (Exception failure) { Record(failure); throw; }','finally { Leave(); }')
                $fn=Method $class 'private void StartSuspendedOriginal(ProducerCompilerOwner owner)'
                Has $fn 'HeldExecutableBoundary(); SuccessBoundary(); AssertOriginalStdioForBirth(); if (producerControlChannel != null) producerControlChannel.AssertProducerDeliveryPending(originalDeadlineOwner); if (producerContextSection != null) producerContextSection.AssertProducerDeliveryPending(originalDeadlineOwner); SuccessBoundary(); RequirePreparedJob(); AssertEmptyJobBeforeBirth(); ProducerCompilerBirthBoundary(owner); ClockAndFaultBoundary(); creationAttempted = true; bool created = CreateProcess('
            }
            'compiler_resume_stays_owned_and_sticky' {
                $fn=Method $class 'private void ResumeOriginal(GuardianLaunch guardian)'
                Order $fn @('SuccessBoundary(); HeldExecutableBoundary(); RootIdentity(false);', 'uint previous = SuspendThread(thread);', 'if (previous != 1) throw', 'uint restored = ResumeThread(thread);', 'resumeAttempted = true;', 'uint count = ResumeThread(thread);')
                $generic=Method $class 'public void Resume()'
                Order $generic @('Enter();','if (producerCompilerOwner != null)','if (originalDeadlineOwner != null)','ResumeOriginal(null);','catch (Exception failure) { Record(failure); throw; }','finally { Leave(); }')
                $producer=Method $class 'internal void ResumeProducer(DisposableProducerLaunch owner, GuardianLaunch guardian)'
                Order $producer @('Enter();','if (owner == null || guardian == null || !Object.ReferenceEquals(originalDeadlineOwner, owner))','owner.AssertGuardianContinuation(this, guardian);','ResumeOriginal(guardian);','catch (Exception failure) { Record(failure); throw; }','finally { Leave(); }')
                $compiler=Method $class 'internal void ResumeProducerCompiler(ProducerCompilerOwner owner)'
                Order $compiler @('Enter();','if (owner == null || !Object.ReferenceEquals(producerCompilerOwner, owner))','owner.AssertContinuationAdmission(this);','owner.DemandQualifiedCompilerContinuation(this);','ResumeOriginal(null);','catch (Exception failure) { Record(failure); throw; }','finally { Leave(); }')
            }
            'compiler_completed_at_is_observed_after_physical_closure' {
                $fn=Method $class 'public CompilerResult PollResult()'
                Order $fn @('if (!OriginalRootClosed(true) || JobAccounting().active != 0 || !CollectorsClosed())', 'string completedAt = DateTimeOffset.UtcNow.ToString("o", CultureInfo.InvariantCulture);','CompilerResult candidate = new CompilerResult(pid, startedAt, completedAt,', 'SuccessBoundary(); completedResult = candidate; closureProven = true;')
                Has $fn '.AddMilliseconds(' 0
            }
            'compiler_release_owner_after_every_resource_closes' {
                $fn=Method $class 'public void Dispose()'
                Order $fn @('RejectTransferredCompilerCleanup();','if (!closureProven || !CollectorsClosed()) throw','releaseStarted = true;','ReleaseOriginalPrivateResources(); AssertCleanupPrivateResourcesReleased();','CloseOwned(ref thread, threadClose); CloseOwned(ref process, processClose); CloseOwned(ref job, jobClose);','ReleaseTerminalResource(stdoutBytes, terminalStdoutRelease); stdoutStorageDisposed = true;','ReleaseTerminalResource(stderrBytes, terminalStderrRelease); stderrStorageDisposed = true;', 'ownedInputs.ReleaseCommand(this); ownedInputsReleased = true;', 'disposed = true;')
                Has (Method $class 'private void AssertCleanupPrivateResourcesReleased()') '!ExecutableLockHasNoAcquisition() && !PrivateSlotReleased(executableLockOwner.Slot)'
            }
            'compiler_exact_release_has_no_other_callsite' {
                Has $class 'ownedInputs.ReleaseCommand(this);' 1
                Has (Method $class 'public bool PollContainment()') 'ReleaseCommand(' 0
                Has (Method $class 'public CompilerResult PollResult()') 'ReleaseCommand(' 0
            }
            'result_native_fact_and_defensive_arrays_not_authority' {
                $result=Block $tokens 'public sealed class CompilerResult'
                Has $result 'public readonly string StartedAt, CompletedAt, ExecutableSha256, CommandLine, EnvironmentSha256;'
                Has $result 'CompletedAt = completed;'
                Has $result 'stdout = (byte[])output.Clone(); stderr = (byte[])error.Clone();'
                Has $result 'BuildCausalityQualified = false, MetadataVerified = false, ImportPermitted = false, ProductionStartPermitted = false;'
            }
            'compiler_generic_name_validation_remains_required' {
                $fn=Method $class 'private FileInformation HeldInformation('
                Order $fn @('!String.Equals(stream.Name, executable, StringComparison.OrdinalIgnoreCase)','return ExecutableInformation(stream);')
                Has $fn '!stream.CanRead || stream.CanWrite || !stream.CanSeek'
                Has (Method $class 'private void HeldExecutableBoundary()') 'ownedInputs == null ? HeldInformation(suppliedStream) : HeldInputExecutableInformation(suppliedStream)' 2
                Has (Method $class 'public void Dispose()') 'suppliedExecutable.Dispose(' 0
            }
            'compiler_typed_descriptor_validates_before_native_metadata' {
                Order (Method $class 'private FileInformation HeldInputExecutableInformation(') @('ownedInputsReleased || !Object.ReferenceEquals(ownedInputs.AssertCompilerExecutableBorrow(this, ownedExecutable), exactStream)','return ExecutableInformation(exactStream);')
                $native=Method $class 'private FileInformation ExecutableInformation('
                Order $native @('GetFileInformationByHandle(stream.SafeFileHandle, out information)','information.links != 1','GetFinalPathNameByHandle(stream.SafeFileHandle, path,','length > ExecutableLimit')
                Has $native 'FileAttributes.Directory | (uint)FileAttributes.ReparsePoint'
                Has $native '.Name' 0
            }
            'compiler_owned_lock_retained_nonowning_original' {
                $fn=Method $class 'private void OpenOriginalExecutableLock()'
                Order $fn @('executableLockOwner = original;','original.Attempted = true;','original.Slot.Raw = CreatePrivateFile(executable, 0x80000000, 1, IntPtr.Zero, 3, 0x00200080, IntPtr.Zero);','original.Error = Marshal.GetLastWin32Error();','original.Succeeded = ValidHandle(original.Slot.Raw); original.Returned = true;','slot.Wrapper = new SafeFileHandle(slot.Raw, false);','slot.Stream = new FileStream((SafeFileHandle)slot.Wrapper, FileAccess.Read, 65536, false);','GC.SuppressFinalize(slot.Stream); slot.FinalizerSuppressed = true;')
                Has (Method $class 'private FileInformation HeldOwnedExecutableInformation(') '!Object.ReferenceEquals(BorrowOriginalExecutableLock(), exactStream)'
            }
            'compiler_close_requires_known_owned_slot_once' {
                $fn=Method $class 'private void RetirePrivateSlot('
                Order $fn @('RequireOriginalPrivateSlot(slot); RequireSettledPrivateWrapper(slot);','if (outcome.Attempted)','if (outcome.Original != slot.Raw || !outcome.Returned || !outcome.Succeeded)','slot.Wrapper.IsClosed || slot.Wrapper.IsInvalid || slot.Wrapper.DangerousGetHandle() != slot.Raw','outcome.Original = slot.Raw; outcome.Attempted = true;','bool closed = CloseHandle(outcome.Original);')
                Has $fn 'CloseHandle(' 1
                Has $fn 'slot.Raw = IntPtr.Zero;' 0
                Has $fn 'GetHandleInformation(' 0
            }
            'compiler_close_outcome_and_wrapper_disposal_separate' {
                $fn=Method $class 'private void RetirePrivateSlot('
                Order $fn @('bool closed = CloseHandle(outcome.Original);','slot.CloseError = Marshal.GetLastWin32Error(); outcome.Succeeded = closed; outcome.Returned = true;','if (!closed) throw','slot.Wrapper.SetHandleAsInvalid();','slot.Invalidation.Succeeded = true; slot.Invalidation.Returned = true;','ReleaseTerminalResource(slot.Stream == null ? (IDisposable)slot.Wrapper : slot.Stream, slot.Disposal);')
                Has (Method $class 'private bool PrivateSlotReleased(') 'slot.Close.Attempted && slot.Close.Original == slot.Raw && slot.Close.Returned && slot.Close.Succeeded && (slot.Wrapper == null || (KnownManagedSuccess(slot.Invalidation) && KnownManagedSuccess(slot.Disposal)))'
                Has (Method $class 'private static void ReleaseTerminalResource(') 'CloseHandle(' 0
            }
            'compiler_failed_file_acquisition_zero_not_absence' {
                $fn=Method $class 'private bool ExecutableLockHasNoAcquisition()'
                Has $fn 'return original.Returned && !original.Succeeded && original.Slot.Raw == new IntPtr(-1);'
                Has $fn 'original.Slot.WrapperConstruction.Attempted || original.Slot.StreamConstruction.Attempted || original.Slot.Wrapper != null || original.Slot.Stream != null || original.Slot.Close.Attempted'
                Has (Method $class 'private void ReleaseOriginalPrivateResources()') 'if (!ExecutableLockHasNoAcquisition()) RetirePrivateSlot(executableLockOwner.Slot);'
            }
            'compiler_terminal_release_native_resources_before_registration' {
                $fn=Method $class 'internal void ReleaseProducerInputRegistration('
                Order $fn @('owner.AssertTerminalWorker(closure); AssertProducerTerminalObservation(owner, closure);','ReleaseOriginalPrivateResources(); AssertProducerPrivateResourcesReleased();','ReleaseTerminalResource(stdoutBytes, terminalStdoutRelease);','ReleaseTerminalResource(stderrBytes, terminalStderrRelease);','terminalRegistrationRelease.Attempted = true;','ownedInputs.ReleaseCommandForProducerTerminal(owner, this);','terminalRegistrationRelease.Succeeded = true; terminalRegistrationRelease.Returned = true;','ownedInputsReleased = true;')
                Has $fn 'CloseOwned(' 0
            }
            'compiler_terminal_backlink_checks_native_results_not_dispose' {
                $fn=Method $class 'internal void AssertProducerTerminalRegistrationRelease('
                Order $fn @('AssertProducerTerminalObservation(owner, closure); owner.AssertTerminalWorkerManaged(closure);','!Object.ReferenceEquals(ownedInputs, originalInputs)','!KnownManagedSuccess(terminalStdoutRelease) || !KnownManagedSuccess(terminalStderrRelease)','AssertProducerPrivateResourcesReleased();')
                $proof=Method $class 'private void AssertProducerPrivateResourcesReleased()'
                Has $proof '!PrivateSlotReleased(executableLockOwner.Slot) || !PrivateSlotReleased(outputPair.Parent) || !PrivateSlotReleased(errorPair.Parent)'
                Has $proof '!ActualCollectorClosureRecorded(outputPair, outputCollector, false) || !ActualCollectorClosureRecorded(errorPair, errorCollector, true)'
                foreach($call in @('CloseHandle(','Dispose(','Join(','GetHandleInformation(')){Has $proof $call 0}
            }
            'compiler_stdio_pair_outputs_retained_before_validation' {
                Has (Method $class 'private void StartSuspendedOriginal(') 'PrepareStdioPairs();'
                $prepare=Method $class 'private void PrepareStdioPairs()'
                Order $prepare @('outputPair = new CompilerPipePair(this, 1); OpenOriginalStdioPair(outputPair);','errorPair = new CompilerPipePair(this, 2); OpenOriginalStdioPair(errorPair);','inputPair = new CompilerPipePair(this, 0); OpenOriginalStdioPair(inputPair);')
                $fn=Method $class 'private void OpenOriginalStdioPair('
                Order $fn @('if (!IsOriginalPair(pair) || pair.Attempted)','pair.Attempted = true;','bool created = CreatePipe(out pair.Read.Raw, out pair.Write.Raw, ref security, 0);','pair.Error = Marshal.GetLastWin32Error(); pair.Succeeded = created; pair.Returned = true;','if (!created) throw','RequireOriginalPair(pair);','SetOriginalPipeInheritance(pair.Parent, 0); SetOriginalPipeInheritance(pair.Child, 1);','new SafePipeHandle(slot.Raw, false)')
                Has (Method $class 'private bool PairHasNoAcquisition(') 'return !pair.Attempted && !pair.Returned && !pair.Succeeded && pair.Read.Raw == IntPtr.Zero && pair.Write.Raw == IntPtr.Zero'
            }
            'compiler_collector_last_use_join_eof_before_parent_close' {
                $fn=Method $class 'private bool CollectorClosed('
                Order $fn @('!KnownManagedSuccess(original.Construction) || !KnownManagedSuccess(original.Start)','Interlocked.CompareExchange(ref original.LastUseSettled, 0, 0) != 1','if (isError ? !stderrEof : !stdoutEof) return false;','bool joined = original.Worker.Join(0);','original.Join.Succeeded = joined; original.Join.Returned = true;')
                Order (Method $class 'private void ReleaseOriginalPrivateResources()') @('if (!CollectorClosed(pair, collector, pair.Role == 2))','RetirePrivateSlot(pair.Parent);')
                $pure=Method $class 'private bool ActualCollectorClosureRecorded('
                Has $pure '!KnownManagedSuccess(original.Start) || !KnownManagedSuccess(original.Join)'
                Has $pure 'lock (captureGate) { return isError ? stderrEof : stdoutEof; }'
                Has $pure '.Join(' 0
            }
            'compiler_stdio_pair_requires_known_successful_original_acquisition' {
                $fn=Method $class 'private void RequireOriginalPair('
                Has $fn 'if (!IsOriginalPair(pair) || !pair.Attempted || !pair.Returned || !pair.Succeeded || !ValidHandle(pair.Read.Raw) || !ValidHandle(pair.Write.Raw) || pair.Read.Raw == pair.Write.Raw) throw'
                Has (Method $class 'private bool IsOriginalPair(') 'pair != null && Object.ReferenceEquals(pair.Owner, this) && ((pair.Role == 0 && Object.ReferenceEquals(pair, inputPair)) || (pair.Role == 1 && Object.ReferenceEquals(pair, outputPair)) || (pair.Role == 2 && Object.ReferenceEquals(pair, errorPair)))'
                Order (Method $class 'private void OpenOriginalStdioPair(') @('pair.Error = Marshal.GetLastWin32Error(); pair.Succeeded = created; pair.Returned = true;','if (!created) throw','RequireOriginalPair(pair);','SetOriginalPipeInheritance(pair.Parent, 0);')
            }
            'compiler_early_retirement_covers_all_three_original_local_child_ends' {
                $fn=Method $class 'private void CloseLocalChildEnds()'
                Order $fn @('if (containmentStarted)','if (!(NoChildCreated() || OriginalRootClosed(false)))','else RequireOriginalCreation();','foreach (CompilerPipePair pair in new CompilerPipePair[] { outputPair, errorPair, inputPair })','RequireOriginalPair(pair); RetirePrivateSlot(pair.Child);','if (pending.Count != 0) throw')
                Order (Method $class 'private void StartSuspendedOriginal(') @('creationSucceeded = created; creationReturned = true;','SuccessBoundary(); RootIdentity(true);','CloseLocalChildEnds(); EnsureCollectors(); HeldExecutableBoundary(); SuccessBoundary(); started = true;')
                Has (Block $class 'private sealed class CompilerPipePair') 'internal PrivateSlot Child { get { return Role == 0 ? Read : Write; } }'
            }
            'compiler_early_retirement_closes_unused_original_parent_stdin' {
                $fn=Method $class 'private void CloseLocalChildEnds()'
                Order $fn @('RequireOriginalPair(pair); RetirePrivateSlot(pair.Child);','if (inputPair != null && !PairHasNoAcquisition(inputPair))','RequireOriginalPair(inputPair); RetirePrivateSlot(inputPair.Parent);','if (pending.Count != 0) throw')
                Has (Method $class 'private void OpenOriginalStdioPair(') 'if (pair.Role == 0) return;'
                Has (Method $class 'private void AssertEarlyStdioRetired()') 'if (!PrivateSlotReleased(inputPair.Parent)) throw'
                Has (Block $class 'private sealed class CompilerPipePair') 'internal PrivateSlot Parent { get { return Role == 0 ? Write : Read; } }'
            }
            'compiler_resume_requires_all_four_known_early_retirements' {
                $fn=Method $class 'private void ResumeOriginal('
                Order $fn @('if (!started || resumeAttempted)','AssertEarlyStdioRetired();','ProducerContinuationBoundary(guardian); CompilerContinuationBoundary(); SuccessBoundary(); HeldExecutableBoundary(); RootIdentity(false);','uint previous = SuspendThread(thread);','uint restored = ResumeThread(thread);','resumeAttempted = true;','uint count = ResumeThread(thread);')
                $proof=Method $class 'private void AssertEarlyStdioRetired()'
                Order $proof @('foreach (CompilerPipePair pair in new CompilerPipePair[] { inputPair, outputPair, errorPair })','RequireOriginalPair(pair);','if (!PrivateSlotReleased(pair.Child)) throw','if (!PrivateSlotReleased(inputPair.Parent)) throw')
                Has (Method $class 'private bool PrivateSlotReleased(') 'slot.Close.Attempted && slot.Close.Original == slot.Raw && slot.Close.Returned && slot.Close.Succeeded'
            }
            'compiler_collector_finally_publishes_last_use_on_the_original_worker' {
                Has (Method $class 'private void EnsureCollector(') 'original.Worker = new Thread(delegate() { Collect(original); });'
                $fn=Method $class 'private void Collect('
                Order $fn @('!Object.ReferenceEquals(original, original.IsError ? errorCollector : outputCollector)','!Object.ReferenceEquals(Thread.CurrentThread, original.Worker) || !original.Start.Attempted','int count = source.Read(buffer, 0, buffer.Length);','catch (Exception failure) { Record(failure); }','finally { if (original != null) Interlocked.Exchange(ref original.LastUseSettled, 1); }')
                Has (Method $class 'private bool CollectorClosed(') 'Interlocked.CompareExchange(ref original.LastUseSettled, 0, 0) != 1'
                Has (Method $class 'private bool ActualCollectorClosureRecorded(') 'Interlocked.CompareExchange(ref original.LastUseSettled, 0, 0) != 1'
                Order (Method $class 'private void ReleaseOriginalPrivateResources()') @('if (!CollectorClosed(pair, collector, pair.Role == 2))','RetirePrivateSlot(pair.Parent);')
            }
            'compiler_managed_factory_rejects_new_native_acquisition_helper' {
                $fn=Method $class 'public static CompilerCommand PrepareOwned('
                ManagedOnly $fn
                Order $fn @('CompilerCommand prepared = new CompilerCommand(','inputs.RegisterCommand(prepared);','return prepared;')
            }
            default {throw 'Ownership source contract: Unknown compiler predicate.'}
        }
        return
    }
    $class=Block $tokens 'public sealed class BuildInputLeaseSet : IDisposable'
    switch($Name){
        'inputs_prepare_managed_before_retention' {ManagedOnly (Method $class 'public static BuildInputLeaseSet Prepare(');ManagedOnly (Method $class 'private BuildInputLeaseSet(')}
        'inputs_spec_validation_managed' {
            ManagedOnly (Block $tokens 'internal static class BuildInputValidation')
            $spec=Block $tokens 'public sealed class BuildInputSpec'
            ManagedOnly $spec
            Has $spec 'public readonly string Path, Sha256;'
            Has $spec 'public readonly long Bytes, MaximumBytes;'
        }
        'inputs_fixed_count_and_aggregate_bounds' {
            Has $tokens 'internal const long FileLimit = 134217728;'
            Has $tokens 'internal const long SetLimit = 275251200;'
            Has $tokens 'internal const int FileCountLimit = 104;'
            $fn=Method $class 'public static BuildInputLeaseSet Prepare('
            Has $fn 'specifications.Length > BuildInputValidation.FileCountLimit'
            Has $fn 'if (total > BuildInputValidation.SetLimit) throw'
        }
        'inputs_private_spec_copy_and_case_alias_rejection' {
            $fn=Method $class 'public static BuildInputLeaseSet Prepare('
            Has $fn 'new HashSet<string>(StringComparer.OrdinalIgnoreCase)'
            Order $fn @('copied[i] = new BuildInputSpec(item.Path, item.Sha256, item.Bytes, item.MaximumBytes);','if (!paths.Add(copied[i].Path)) throw','total += copied[i].Bytes;','return new BuildInputLeaseSet(copied);')
        }
        'inputs_reserved_device_before_native_open' {Has $tokens "string stem = part.Split('.')[0].TrimEnd(' ').ToUpperInvariant();";Has $tokens "path.IndexOf(':', 2) >= 0"}
        'inputs_reserved_device_superscript_one' {
            $validation=Block $tokens 'internal static class BuildInputValidation'
            $fn=Method $validation 'internal static void Path('
            ManagedOnly $fn
            Has $fn 'if (stem == "CON" || stem == "PRN" || stem == "AUX" || stem == "NUL" || (stem.Length == 4 && (stem.StartsWith("COM", StringComparison.Ordinal) || stem.StartsWith("LPT", StringComparison.Ordinal)) && ((stem[3] >= ''1'' && stem[3] <= ''9'') || stem[3] == ''\u00b9'' || stem[3] == ''\u00b2'' || stem[3] == ''\u00b3''))) throw new ArgumentException("Reserved device input path.");'
        }
        'inputs_reserved_device_superscript_two' {
            $validation=Block $tokens 'internal static class BuildInputValidation'
            $fn=Method $validation 'internal static void Path('
            Has $fn "((stem[3] >= '1' && stem[3] <= '9') || stem[3] == '\u00b9' || stem[3] == '\u00b2' || stem[3] == '\u00b3')"
        }
        'inputs_reserved_device_superscript_three' {
            $validation=Block $tokens 'internal static class BuildInputValidation'
            $fn=Method $validation 'internal static void Path('
            Has $fn "((stem[3] >= '1' && stem[3] <= '9') || stem[3] == '\u00b9' || stem[3] == '\u00b2' || stem[3] == '\u00b3')"
        }
        'inputs_directory_record_before_native_handle' {
            $fn=Method $class 'private void RetainDirectory('
            Order $fn @('DirectoryLease entry = new DirectoryLease(this, path);','directories.Add(entry); directoryPaths.Add(path, entry);','AcquireNativeFile(entry.Native);', 'entry.Identity = Information(entry.Native); entry.IdentityEstablished = true;')
            Has (Block $class 'private sealed class DirectoryLease') 'Native = new NativeInputFile(owner, this, path, true, false);'
            Order (Method $class 'private void AcquireNativeFile(') @('AssertNativeLineage(original);','original.AcquireAttempted = true;','original.Original = CreateFile(')
        }
        'inputs_directory_nofollow_no_delete_share' {
            Has (Method $class 'private void RetainDirectory(') 'AcquireNativeFile(entry.Native);'
            Has (Method $class 'private void AcquireNativeFile(') 'CreateFile(original.Path, original.Directory ? 0x80u : 0x80000000u, original.Directory ? 3u : 1u, IntPtr.Zero, 3, original.Directory ? 0x02200000u : 0x00200080u, IntPtr.Zero)'
        }
        'inputs_fixed_ntfs_and_root_to_leaf_ancestry' {
            $fn=Method $class 'private void RetainAncestry('
            Order $fn @('if (GetDriveType(root) != 3) throw','if (!String.Equals(fs.ToString(), "NTFS", StringComparison.Ordinal)) throw','RetainDirectory(root);','foreach (string part in parent.Substring(3).Split(','RetainDirectory(current);')
        }
        'inputs_native_regular_single_link_and_finalpath' {
            $fn=Method $class 'private FileInformation Information('
            Has $fn 'GetFileType(handle) != 1 || Index(info) == 0'
            Has $fn '(info.attributes & (uint)FileAttributes.ReparsePoint) != 0'
            Has $fn '(!original.Directory && info.links != 1)'
            Order $fn @('AssertLiveNativeFile(original);','GetFileInformationByHandle(handle, out info)','GetFinalPathNameByHandle(handle, finalPath,','count >= finalPath.Capacity || !String.Equals(finalPath.ToString(),')
        }
        'inputs_both_native_handles_same_file_not_name_only' {
            $fn=Method $class 'private FileInformation AssertFile('
            Order $fn @('FileInformation current = Information(item.IdentityFile);','FileInformation streamIdentity = Information(item.ReadFile);','if (item.IdentityFile.Original == item.ReadFile.Original)','!SameIdentity(item.Identity, current, false) || !SameIdentity(current, streamIdentity, false)')
            Has $fn 'Length(current) != (ulong)item.Spec.Bytes || item.Stream.Length != item.Spec.Bytes'
        }
        'inputs_open_attempt_is_irreversible' {
            $fn=Method $class 'public void Open()'
            Order $fn @('RequireState(false); if (openAttempted) throw','openAttempted = true;','foreach (InputLease item in inputs)')
            Has $class 'openAttempted = false' 0
        }
        'inputs_leaf_native_handle_retained_before_stream' {
            $fn=Method $class 'public void Open()'
            Order $fn @('AcquireNativeFile(item.IdentityFile);', 'item.Identity = Information(item.IdentityFile); item.IdentityEstablished = true;', 'AcquireNativeFile(item.ReadFile);', 'CaptureFile(item);')
            $lease=Block $class 'private sealed class InputLease'
            Order $lease @('IdentityFile = new NativeInputFile(owner, this, spec.Path, false, false);','ReadFile = new NativeInputFile(owner, this, spec.Path, false, true);')
            Order (Method $class 'private void AcquireNativeFile(') @('original.Original = CreateFile(','original.AcquireError = Marshal.GetLastWin32Error();','original.Handle = new SafeFileHandle(original.Original, false);','original.Stream = new FileStream(original.Handle, FileAccess.Read, 65536, false);')
        }
        'inputs_length_and_physical_aliases_before_capture' {
            $fn=Method $class 'public void Open()'
            Order $fn @('Length(item.Identity) != (ulong)item.Spec.Bytes || Length(item.Identity) > (ulong)item.Spec.MaximumBytes','if (!physicalFiles.Add(physical)) throw','AcquireNativeFile(item.ReadFile);','CaptureFile(item);')
            Has $fn 'item.Identity.volume.ToString("x8", CultureInfo.InvariantCulture) + ":" + Index(item.Identity).ToString("x16", CultureInfo.InvariantCulture)'
        }
        'inputs_leaf_readonly_no_write_delete_share' {
            $fn=Method $class 'private void AcquireNativeFile('
            Has $fn 'CreateFile(original.Path, original.Directory ? 0x80u : 0x80000000u, original.Directory ? 3u : 1u, IntPtr.Zero, 3, original.Directory ? 0x02200000u : 0x00200080u, IntPtr.Zero)'
            Has $fn 'new FileStream(original.Handle, FileAccess.Read, 65536, false)'
            Has (Method $class 'public void Open()') 'AcquireNativeFile(item.ReadFile);'
        }
        'inputs_partial_open_failure_retains_all_acquisitions' {
            $fn=Method $class 'public void Open()'
            Has $fn 'catch (Exception error) { Record(error); throw; }'
            Has $fn 'finally { Leave(); }'
            Has $fn 'Dispose(' 0
            Has $fn 'ready = true; AssertHeldCore();'
        }
        'inputs_bounded_capture_reads_original_stream' {
            $fn=Method $class 'private void CaptureFile('
            Has $fn 'captured = new byte[(int)item.Spec.Bytes];'
            Has $fn 'int read = item.Stream.Read(captured, offset, Math.Min(65536, captured.Length - offset));'
            Has $fn 'if (read <= 0) throw'
            Has $fn 'finally { item.Stream.Position = position; }'
        }
        'inputs_capture_hash_and_final_identity_before_private_publish' {
            $fn=Method $class 'private void CaptureFile('
            Order $fn @('if (item.Stream.ReadByte() != -1) throw','hash.ComputeHash(captured)','AssertFile(item); RequireState(false);','if (actual != item.Spec.Sha256) throw','finally { item.Stream.Position = position; }','RequireState(false); item.Captured = captured;')
        }
        'inputs_snapshot_arrays_never_escape_by_reference' {
            $snapshot=Block $tokens 'public sealed class BuildInputSnapshot'
            Has $snapshot 'internal BuildInputSnapshot('
            Has $snapshot 'bytes = (byte[])captured.Clone();'
            Has $snapshot 'public byte[] Bytes { get { return (byte[])bytes.Clone(); } }'
        }
        'inputs_assertheld_checks_complete_ancestry_and_files' {
            $fn=Method $class 'private void AssertHeldCore()'
            Has $fn 'RequireState(true);' 2
            Order $fn @('foreach (DirectoryLease entry in directories) AssertDirectory(entry);','foreach (InputLease item in inputs)','AssertFile(item);','if (item.Captured == null || item.Captured.LongLength != item.Spec.Bytes) throw')
        }
        'inputs_snapshot_projection_guarded_before_and_after' {
            $fn=Method $class 'public BuildInputSnapshot[] CaptureSnapshots()'
            Has $fn 'AssertHeldCore();' 2
            Order $fn @('AssertHeldCore(); BuildInputSnapshot[] result =','new BuildInputSnapshot(item.Spec, item.Captured,','AssertHeldCore(); return result;')
        }
        'inputs_final_capture_refuses_active_borrower' {
            $fn=Method $class 'public BuildInputSnapshot[] CaptureSnapshots()'
            Order $fn @('RequireState(true);','if (borrower != null || guardianBorrower != null) throw','AssertHeldCore(); BuildInputSnapshot[] result =','CaptureFile(item);','new BuildInputSnapshot(item.Spec, item.Captured,')
        }
        'inputs_final_capture_rehashes_same_stream_before_snapshot' {
            $fn=Method $class 'public BuildInputSnapshot[] CaptureSnapshots()'
            Order $fn @('InputLease item = inputs[i];','CaptureFile(item);','result[i] = new BuildInputSnapshot(item.Spec, item.Captured,')
        }
        'inputs_borrow_requires_registered_exact_path_hash' {
            $fn=Method $class 'internal ExecutableBorrow BorrowCompilerExecutable('
            Order $fn @('AssertHeldCore();','if (original == null || !Object.ReferenceEquals(borrower, original) || guardianBorrower != null) throw','if (String.Equals(item.Spec.Path, path, StringComparison.Ordinal) && item.Spec.Sha256 == sha256)', 'executableBorrow = new ExecutableBorrow(this, original, item.Spec, item, item.IdentityFile, item.ReadFile, item.Stream);','executableBorrowRevoked = false;')
            Has $fn 'AssertExecutableBorrowCore(original, executableBorrow);'
        }
        'inputs_register_managed_single_exact_command' {
            $fn=Method $class 'internal void RegisterCommand('
            ManagedOnly $fn
            Order $fn @('RequireState(true);','if (command == null || borrower != null || guardianBorrower != null || (executableBorrow != null && !executableBorrowRevoked)) throw','borrower = command;')
        }
        'inputs_release_only_original_registered_reference' {
            $fn=Method $class 'internal void ReleaseCommand('
            Order $fn @('if (command == null || guardianBorrower != null || !Object.ReferenceEquals(borrower, command)) throw','executableBorrowRevoked = true; borrower = null;')
            $terminal=Method $class 'internal void ReleaseCommandForProducerTerminal('
            Order $terminal @('RequireState(true);','if (owner == null || command == null || guardianBorrower != null || !Object.ReferenceEquals(borrower, command))','command.AssertProducerTerminalRegistrationRelease(this, owner);','executableBorrowRevoked = true; borrower = null;')
            Has $class 'borrower = null;' 2
        }
        'inputs_dispose_refuses_borrower_before_any_release' {
            $fn=Method $class 'public void Dispose()'
            Order $fn @('if (borrower != null || guardianBorrower != null || (executableBorrow != null && !executableBorrowRevoked)) throw','releaseStarted = true;','for (int i = inputs.Length - 1; i >= 0; --i)','for (int i = directories.Count - 1; i >= 0; --i)','if (releaseErrors.Count != 0) throw','disposed = true;')
        }
        'inputs_guardian_register_excludes_both_borrowers' {
            $fn=Method $class 'internal void RegisterGuardian('
            ManagedOnly $fn
            Order $fn @('RequireState(true);','if (guardian == null || borrower != null || guardianBorrower != null || (executableBorrow != null && !executableBorrowRevoked))','guardianBorrower = guardian;')
        }
        'inputs_guardian_borrow_requires_exact_owner_and_bytes' {
            $fn=Method $class 'internal FileStream BorrowExactForGuardian('
            Order $fn @('AssertHeldCore();','if (borrower != null || guardian == null || !Object.ReferenceEquals(guardianBorrower, guardian))','if (String.Equals(item.Spec.Path, path, StringComparison.Ordinal) && item.Spec.Sha256 == sha256) return item.Stream;')
        }
        'inputs_guardian_release_only_exact_exclusive_owner' {
            $fn=Method $class 'internal void ReleaseGuardian('
            Order $fn @('if (borrower != null || guardian == null || !Object.ReferenceEquals(guardianBorrower, guardian))','guardianBorrower = null;')
            Has $class 'guardianBorrower = null;' 1
        }
        'inputs_individual_release_flags_follow_success' {
            $fn=Method $class 'public void Dispose()'
            Has $fn 'item.StreamReleased = TryReleaseNativeFile(item.ReadFile, releaseErrors);'
            Has $fn 'item.HandleReleased = TryReleaseNativeFile(item.IdentityFile, releaseErrors);'
            Has $fn 'item.Released = TryReleaseNativeFile(item.Native, releaseErrors);'
            Has (Method $class 'private bool TryReleaseNativeFile(') 'try { ReleaseNativeFile(original, false); return true; } catch (Exception error) { errors.Add(error); return false; }'
            Has (Method $class 'private void ReleaseNativeFile(') 'AssertNativeFileRetired(original, requireAcquired);' 3
            Has $fn 'catch (Exception error) { Record(error); throw; }'
        }
        'inputs_fault_and_concurrency_rejection_are_sticky' {
            Has (Method $class 'private void Record(') 'failed = true;'
            Has (Method $class 'private void RequireState(') 'if (failed) throw new AggregateException(failures.ToArray());'
            Has (Method $class 'private void Enter()') 'Interlocked.CompareExchange(ref operation, 1, 0) != 0'
            Has (Method $class 'private void Enter()') 'Record(error); throw error;'
            Has $class 'failed = false' 0
        }
        'inputs_native_acquire_outcome_before_wrappers' {
            Has $class 'private static extern IntPtr CreateFile('
            $fn=Method $class 'private void AcquireNativeFile('
            Order $fn @('AssertNativeLineage(original);','if (original.AcquireAttempted || releaseStarted || disposed)','original.AcquireAttempted = true;','original.Original = CreateFile(','original.AcquireError = Marshal.GetLastWin32Error();','original.AcquireSucceeded = ValidNativeValue(original.Original); original.AcquireReturned = true;','if (!original.AcquireSucceeded) throw','original.HandleAttempted = true;')
        }
        'inputs_nonowning_wrappers_from_first_construction' {
            $fn=Method $class 'private void AcquireNativeFile('
            Order $fn @('original.Handle = new SafeFileHandle(original.Original, false);','original.HandleSucceeded = true; original.HandleReturned = true;','GC.SuppressFinalize(original.Handle); original.HandleFinalizerSuppressed = true;','original.Stream = new FileStream(original.Handle, FileAccess.Read, 65536, false);')
            Has $class 'new SafeFileHandle(' 1
            Has $class 'new FileStream(' 1
        }
        'inputs_stream_construction_and_finalizer_before_use' {
            $fn=Method $class 'private void AcquireNativeFile('
            Order $fn @('original.StreamAttempted = true;','original.Stream = new FileStream(','original.StreamSucceeded = true; original.StreamReturned = true;','catch (Exception) { original.StreamReturned = true; throw; }','GC.SuppressFinalize(original.Stream); original.StreamFinalizerSuppressed = true;')
            Has (Method $class 'private void AssertLiveNativeFile(') '!original.StreamFinalizerSuppressed || original.Stream == null'
        }
        'inputs_live_close_attempt_blocks_all_queries' {
            $fn=Method $class 'private void AssertLiveNativeFile('
            Order $fn @('original.Aliased || original.CloseAttempted ||','original.Handle.IsClosed || original.Handle.IsInvalid','original.Stream.SafeFileHandle')
            Order (Method $class 'private FileInformation Information(') @('AssertLiveNativeFile(original);','GetFileInformationByHandle(handle, out info)')
        }
        'inputs_raw_alias_quarantines_both_records' {
            $fn=Method $class 'private static void RejectNativeAlias('
            Order $fn @('!Object.ReferenceEquals(original, other) && other.AcquireAttempted','original.Original == other.Original','original.Aliased = true; other.Aliased = true;','throw')
            Has (Method $class 'private void AcquireNativeFile(') 'RejectNativeAlias(original, item.IdentityFile); RejectNativeAlias(original, item.ReadFile);'
            Has (Method $class 'private void ReleaseNativeFile(') '!ValidNativeValue(original.Original) || original.Aliased ||'
        }
        'inputs_failed_handle_constructor_denies_close' {
            Order (Method $class 'private void ReleaseNativeFile(') @('if (original.HandleAttempted && (!original.HandleReturned || !original.HandleSucceeded || !original.HandleFinalizerSuppressed || original.Handle == null))','original.CloseAttempted = true;','original.CloseSucceeded = CloseHandle(original.Original);')
        }
        'inputs_failed_stream_constructor_denies_close' {
            Order (Method $class 'private void ReleaseNativeFile(') @('if (original.StreamAttempted && (!original.StreamReturned || !original.StreamSucceeded || !original.StreamFinalizerSuppressed || original.Stream == null))','original.CloseAttempted = true;','original.CloseSucceeded = CloseHandle(original.Original);')
        }
        'inputs_preclose_wrapper_state_quarantines_stale_value' {
            $fn=Method $class 'private void ReleaseNativeFile('
            Order $fn @('if (original.CloseAttempted) { AssertNativeFileRetired(original, requireAcquired); return; }','if (original.HandleAttempted && (original.Handle.IsClosed || original.Handle.IsInvalid || original.Handle.DangerousGetHandle() != original.Original))','original.CloseAttempted = true;','original.CloseSucceeded = CloseHandle(original.Original);')
            Has $fn 'Stream.SafeFileHandle' 0
            Has $fn 'GetHandleInformation(' 0
        }
        'inputs_close_unknown_or_failed_never_retried' {
            $fn=Method $class 'private void ReleaseNativeFile('
            Order $fn @('if (original.CloseAttempted) { AssertNativeFileRetired(original, requireAcquired); return; }','original.CloseAttempted = true;','original.CloseSucceeded = CloseHandle(original.Original);')
            Has $fn 'CloseHandle(' 1
            Has (Method $class 'private void AssertNativeFileRetired(') '!original.CloseAttempted || !original.CloseReturned || !original.CloseSucceeded'
            Has $class 'original.Original = IntPtr.Zero;' 0
        }
        'inputs_native_close_result_precedes_invalidate_and_dispose' {
            Order (Method $class 'private void ReleaseNativeFile(') @('original.CloseAttempted = true;','original.CloseSucceeded = CloseHandle(original.Original);','original.CloseError = Marshal.GetLastWin32Error(); original.CloseReturned = true;','if (!original.CloseSucceeded) throw','original.InvalidateAttempted = true;','original.Handle.SetHandleAsInvalid();','original.InvalidateSucceeded = true; original.InvalidateReturned = true;','original.StreamDisposeAttempted = true;','original.Stream.Dispose();','original.Handle.Dispose();')
        }
        'inputs_managed_disposal_never_native_close_authority' {
            $fn=Method $class 'private void ReleaseNativeFile('
            Has $fn 'original.CloseSucceeded = CloseHandle(original.Original);'
            Has $fn 'original.Stream.Dispose(); original.StreamDisposeSucceeded = true; original.StreamDisposeReturned = true;'
            Has $fn 'original.Handle.Dispose(); original.HandleDisposeSucceeded = true; original.HandleDisposeReturned = true;'
            Has $class 'original.CloseSucceeded = true;' 0
        }
        'inputs_only_minus_one_is_known_failed_no_handle' {
            $fn=Method $class 'private void AssertNativeFileRetired('
            Has $fn 'if (!original.AcquireReturned || original.Aliased)'
            Has $fn 'if (requireAcquired || original.Original != new IntPtr(-1) || original.HandleAttempted || original.StreamAttempted || original.CloseAttempted)'
            Has $fn 'CloseHandle(' 0
        }
        'inputs_generic_cleanup_continues_proven_resources_without_success_on_errors' {
            $fn=Method $class 'public void Dispose()'
            Order $fn @('if (terminalReleaseAttempted)','item.StreamReleased = TryReleaseNativeFile(item.ReadFile, releaseErrors);','item.HandleReleased = TryReleaseNativeFile(item.IdentityFile, releaseErrors);','item.Released = TryReleaseNativeFile(item.Native, releaseErrors);','if (releaseErrors.Count != 0) throw','disposed = true;')
            Has (Method $class 'private bool TryReleaseNativeFile(') 'catch (Exception error) { errors.Add(error); return false; }'
        }
        'inputs_terminal_requires_successfully_acquired_wrappers' {
            $fn=Method $class 'internal void ReleaseForProducerTerminal('
            Has $fn 'ReleaseNativeFile(item.ReadFile, true);'
            Has $fn 'ReleaseNativeFile(item.IdentityFile, true);'
            Has $fn 'ReleaseNativeFile(item.Native, true);'
            Has (Method $class 'private void ReleaseNativeFile(') 'if (requireAcquired && (!original.HandleAttempted || (original.HasStream && !original.StreamAttempted)))'
            Has (Method $class 'private void AssertNativeFileRetired(') 'if (requireAcquired || original.Original != new IntPtr(-1)'
        }
        'inputs_terminal_observer_checks_every_native_slot_without_io' {
            $fn=Method $class 'internal void AssertProducerTerminalReleased('
            Order $fn @('!Object.ReferenceEquals(terminalReleaseInvocation, invocation)','!terminalReleaseAttempted || !terminalReleaseCompleted || !releaseStarted || !disposed','foreach (InputLease item in inputs)','AssertNativeFileRetired(item.ReadFile, true); AssertNativeFileRetired(item.IdentityFile, true);','foreach (DirectoryLease item in directories)','AssertNativeFileRetired(item.Native, true);')
            $retired=Method $class 'private void AssertNativeFileRetired('
            Has $retired '!original.InvalidateAttempted || !original.InvalidateReturned || !original.InvalidateSucceeded'
            Has $retired '!original.StreamDisposeAttempted || !original.StreamDisposeReturned || !original.StreamDisposeSucceeded'
            foreach($call in @('Information(','CloseHandle(','RequireState(','AssertHeld','Dispose(','DangerousGetHandle(','.IsClosed','.IsInvalid')){Has $fn $call 0;Has $retired $call 0}
        }
        'inputs_terminal_release_reverse_native_order_and_original_admission' {
            Order (Method $class 'internal void ReleaseForProducerTerminal(') @('invocation.AssertTerminalPayloadReleaseAdmission(this, owner, closure);','terminalReleaseInvocation = invocation; terminalReleaseOwner = owner; terminalReleaseClosure = closure;','terminalReleaseAttempted = true; releaseStarted = true;','for (int i = inputs.Length - 1; i >= 0; --i)','ReleaseNativeFile(item.ReadFile, true); item.StreamReleased = true;','ReleaseNativeFile(item.IdentityFile, true); item.HandleReleased = true;','for (int i = directories.Count - 1; i >= 0; --i)','ReleaseNativeFile(item.Native, true); item.Released = true;','disposed = true; terminalReleaseCompleted = true;')
        }
        'inputs_descriptor_exact_retained_reference_is_authority' {
            $fn=Method $class 'private FileStream AssertExecutableBorrowCore('
            Order $fn @('!Object.ReferenceEquals(borrower, original) || !Object.ReferenceEquals(executableBorrow, exactBorrow)','!Object.ReferenceEquals(exactBorrow.Owner, this) || !Object.ReferenceEquals(exactBorrow.Command, original)','AssertHeldCore();','return item.Stream;')
            Has (Method $class 'internal FileStream AssertCompilerExecutableBorrow(') 'try { return AssertExecutableBorrowCore(original, exactBorrow); }'
            Has $fn 'executableBorrowRevoked ||'
        }
        'inputs_descriptor_binds_native_records_spec_and_stream' {
            $fn=Method $class 'private FileStream AssertExecutableBorrowCore('
            Has $fn '!Object.ReferenceEquals(exactBorrow.Lease, item)'
            Has $fn '!Object.ReferenceEquals(exactBorrow.Spec, item.Spec) || !Object.ReferenceEquals(exactBorrow.IdentityFile, item.IdentityFile) || !Object.ReferenceEquals(exactBorrow.ReadFile, item.ReadFile) || !Object.ReferenceEquals(exactBorrow.Stream, item.Stream)'
            Order $fn @('AssertHeldCore();','AssertFile(item);','return item.Stream;')
            Has $fn 'original.' 0
        }
        'inputs_registration_clear_revokes_without_fallible_tail' {
            foreach($signature in @('internal void ReleaseCommand(','internal void ReleaseCommandForProducerTerminal(')){
                Has (Method $class $signature) 'executableBorrowRevoked = true; borrower = null; } catch (Exception error) { Record(error); throw; } finally { Leave(); }'
            }
            Has (Method $class 'internal void ReleaseCommandForProducerTerminal(') 'command.AssertProducerTerminalRegistrationRelease(this, owner);'
        }
        'inputs_internal_stream_identity_replaces_name_not_physical_proof' {
            $fn=Method $class 'private void AssertLiveNativeFile('
            Has $fn '!Object.ReferenceEquals(original.Stream.SafeFileHandle, original.Handle)'
            Has $fn 'original.Handle.DangerousGetHandle() != original.Original'
            Has (Method $class 'private FileInformation AssertFile(') '!SameIdentity(item.Identity, current, false) || !SameIdentity(current, streamIdentity, false)'
            Has $class 'item.Stream.Name' 0
            Has $class ': FileStream' 0
        }
        'inputs_managed_factory_rejects_new_native_acquisition_helper' {
            ManagedOnly (Method $class 'public static BuildInputLeaseSet Prepare(')
            ManagedOnly (Method $class 'private BuildInputLeaseSet(')
        }
        default {throw 'Ownership source contract: Unknown input predicate.'}
    }
}
# Each mutation is applied only to source text. The subject is never evaluated.
$rows=@(
    @('inputs_prepare_managed_before_retention','I','return new BuildInputLeaseSet(copied);','File.GetAttributes("C:\\untrusted"); return new BuildInputLeaseSet(copied);'),
    @('inputs_spec_validation_managed','I','BuildInputValidation.Path(path); BuildInputValidation.Hash(sha256);','File.GetAttributes(path); BuildInputValidation.Path(path); BuildInputValidation.Hash(sha256);'),
    @('inputs_fixed_count_and_aggregate_bounds','I','FileCountLimit = 104;','FileCountLimit = 105;'),
    @('inputs_private_spec_copy_and_case_alias_rejection','I','if (!paths.Add(copied[i].Path))','if (false)'),
    @('inputs_reserved_device_before_native_open','I',"part.Split('.')[0].TrimEnd(' ').ToUpperInvariant()","part.Split('.')[0].ToUpperInvariant()"),
    @('inputs_directory_record_before_native_handle','I','directories.Add(entry); directoryPaths.Add(path, entry);','directoryPaths.Add(path, entry);'),
    @('inputs_directory_nofollow_no_delete_share','I','original.Directory ? 3u : 1u','original.Directory ? 7u : 1u'),
    @('inputs_fixed_ntfs_and_root_to_leaf_ancestry','I','if (GetDriveType(root) != 3)','if (false)'),
    @('inputs_native_regular_single_link_and_finalpath','I','(!original.Directory && info.links != 1)','(!original.Directory && info.links < 1)'),
    @('inputs_both_native_handles_same_file_not_name_only','I','!SameIdentity(current, streamIdentity, false)','false'),
    @('inputs_open_attempt_is_irreversible','I','openAttempted = true;','openAttempted = false;'),
    @('inputs_leaf_native_handle_retained_before_stream','I','original.Original = CreateFile(','IntPtr unretained = CreateFile('),
    @('inputs_length_and_physical_aliases_before_capture','I','if (!physicalFiles.Add(physical))','if (false)'),
    @('inputs_leaf_readonly_no_write_delete_share','I','original.Directory ? 3u : 1u','original.Directory ? 3u : 3u'),
    @('inputs_partial_open_failure_retains_all_acquisitions','I','RequireState(false); ready = true; AssertHeldCore();','RequireState(false); ready = true; AssertHeldCore(); Dispose();'),
    @('inputs_bounded_capture_reads_original_stream','I','Math.Min(65536, captured.Length - offset)','captured.Length - offset'),
    @('inputs_capture_hash_and_final_identity_before_private_publish','I','if (actual != item.Spec.Sha256)','if (false)'),
    @('inputs_snapshot_arrays_never_escape_by_reference','I','return (byte[])bytes.Clone();','return bytes;'),
    @('inputs_assertheld_checks_complete_ancestry_and_files','I','foreach (DirectoryLease entry in directories) AssertDirectory(entry);',''),
    @('inputs_snapshot_projection_guarded_before_and_after','I','AssertHeldCore(); return result;','return result;'),
    @('inputs_final_capture_refuses_active_borrower','I','if (borrower != null || guardianBorrower != null) throw new InvalidOperationException("Fresh input capture cannot race an active command or guardian borrower.");','if (false) throw new InvalidOperationException("Fresh input capture cannot race an active command or guardian borrower.");'),
    @('inputs_final_capture_rehashes_same_stream_before_snapshot','I','CaptureFile(item);',''),
    @('inputs_borrow_requires_registered_exact_path_hash','I','if (original == null || !Object.ReferenceEquals(borrower, original) || guardianBorrower != null)','if (false)'),
    @('inputs_register_managed_single_exact_command','I','if (command == null || borrower != null || guardianBorrower != null || (executableBorrow != null && !executableBorrowRevoked))','if (command == null)'),
    @('inputs_release_only_original_registered_reference','I','!Object.ReferenceEquals(borrower, command)','borrower == null'),
    @('inputs_dispose_refuses_borrower_before_any_release','I','if (borrower != null || guardianBorrower != null || (executableBorrow != null && !executableBorrowRevoked))','if (false)'),
    @('inputs_individual_release_flags_follow_success','I','item.StreamReleased = TryReleaseNativeFile(item.ReadFile, releaseErrors);','item.StreamReleased = true; TryReleaseNativeFile(item.ReadFile, releaseErrors);'),
    @('inputs_fault_and_concurrency_rejection_are_sticky','I','failed = true;','failed = false;'),
    @('compiler_owned_factory_managed_and_retained_registration','B','inputs.RegisterCommand(prepared);',''),
    @('compiler_borrow_before_path_stream','B','ownedExecutable = ownedInputs.BorrowCompilerExecutable(this, executable, expectedHash);','ownedExecutable = null;'),
    @('compiler_owned_native_identity_not_path_only','B','if (!SameFile(supplied, before))','if (false)'),
    @('compiler_admission_sandwiches_input_hold','B','ClockAndFaultBoundary(); AssertOwnedInputsHeld(); ClockAndFaultBoundary();','ClockAndFaultBoundary();'),
    @('compiler_cached_result_still_checks_input_owner','B','AssertOwnedInputsHeld(); SuccessState();','SuccessState();'),
    @('compiler_start_boundary_immediately_precedes_native_create','B','creationAttempted = true;','HeldExecutableBoundary(); creationAttempted = true;'),
    @('compiler_resume_stays_owned_and_sticky','B','if (previous != 1) throw new InvalidOperationException("Compiler original suspension was disproved; leave it paused for containment.");','if (false) throw new InvalidOperationException("Compiler original suspension was disproved; leave it paused for containment.");'),
    @('compiler_completed_at_is_observed_after_physical_closure','B','string completedAt = DateTimeOffset.UtcNow.ToString("o", CultureInfo.InvariantCulture);','string completedAt = startedAt;'),
    @('compiler_release_owner_after_every_resource_closes','B','ownedInputs.ReleaseCommand(this); ownedInputsReleased = true;','ownedInputsReleased = true;'),
    @('compiler_exact_release_has_no_other_callsite','B','if (releaseStarted) throw new InvalidOperationException("Resource release began; retry Dispose only.");','ownedInputs.ReleaseCommand(this); if (releaseStarted) throw new InvalidOperationException("Resource release began; retry Dispose only.");'),
    @('result_native_fact_and_defensive_arrays_not_authority','B','BuildCausalityQualified = false','BuildCausalityQualified = true'),
    @('adapter_exact_returned_assembly','P','[object]::ReferenceEquals($type.Assembly,$BootstrapAssembly)','$true'),
    @('adapter_factory_only_managed_one_array_argument','P','$owner=$prepare.Invoke($null,[object[]]@(,$specs))','$owner=$prepare.Invoke($null,[object[]]@($specs))'),
    @('adapter_open_uses_retained_clr_owner','P','$null=$open.Invoke($Owner,[object[]]@())','$Owner.Open()'),
    @('adapter_actual_capture_precedes_v2_and_final_hold','P','$snapshots=$capture.Invoke($Owner,[object[]]@())','$snapshots=$SuppliedSnapshots'),
    @('adapter_no_import_or_path_read_fallback','P','compiler_executed=$false;bootstrap_qualified=$false;full_goal_accepted=$false','compiler_executed=$true;bootstrap_qualified=$false;full_goal_accepted=$false'),
    @('adapter_root_definitions_only','P','function Get-StratusBuildInputTypes {','Write-Output "unexpected effect"; function Get-StratusBuildInputTypes {')
)
# Preserve the original 45 source pairs and two BCL packaging cases, adding
# directed coverage for the mutually exclusive exact guardian owner.
Assert-Ownership ($rows.Count -eq 45) 'Original case inventory changed.'
$rows+=@(
    @('inputs_guardian_register_excludes_both_borrowers','I','if (guardian == null || borrower != null || guardianBorrower != null || (executableBorrow != null && !executableBorrowRevoked))','if (guardian == null || guardianBorrower != null)'),
    @('inputs_guardian_borrow_requires_exact_owner_and_bytes','I','if (borrower != null || guardian == null || !Object.ReferenceEquals(guardianBorrower, guardian))','if (borrower != null || guardian == null)'),
    @('inputs_guardian_release_only_exact_exclusive_owner','I','if (borrower != null || guardian == null || !Object.ReferenceEquals(guardianBorrower, guardian))','if (borrower != null || guardian == null)')
)
Assert-Ownership ($rows.Count -eq 48) 'Prior 98-case inventory changed.'
# Metadata derivation is inspected as AST/tokens only. No adapter or CLR owner is
# invoked; actual in-memory preflight behavior belongs to the pure input suite.
$rows+=@(
    @('adapter_factory_raw_metadata_parameters_only','P','[AllowNull()][object] $Expected)','[AllowNull()][object] $Expected,[object[]] $SpecRecords)'),
    @('adapter_factory_derivation_precedes_clr_selection','P','$types=Get-StratusBuildInputTypes $BootstrapAssembly', '$types=Get-StratusBuildInputTypes $BootstrapAssembly; $SpecRecords=Get-StratusParentCommandBuildInputSpecifications'),
    @('adapter_factory_forwards_all_original_metadata','P','-ProfileDocument $ProfileDocument -ProfileBytes $ProfileBytes','-ProfileDocument $ProfileDocument -ProfileBytes $SuppliedProfileBytes'),
    @('adapter_factory_checks_derived_count_before_clr','P',"Assert-StratusBuildInputArray `$SpecRecords 10 104 'owned build input specifications'", "Assert-StratusBuildInputArray `$SpecRecords 1 104 'owned build input specifications'"),
    @('adapter_factory_role_limits_include_producer_and_verifier','P','elseif($i -eq 6 -or $i -eq 7){16777216}','elseif($i -eq 6){16777216}'),
    @('adapter_factory_validates_each_derived_record_before_constructor','P',"Assert-StratusCommandRuntimeHash `$record.sha256 'owned input hash'", ''),
    @('adapter_factory_typed_array_uses_only_derived_inventory','P','$specs=[Array]::CreateInstance($types.spec_type,$SpecRecords.Count)','$specs=[Array]::CreateInstance($types.spec_type,$SuppliedSpecRecords.Count)'),
    @('adapter_binding_still_calls_full_byte_validator','P','$binding=Assert-StratusParentCommandBuildInputs','$binding=Get-StratusParentCommandBuildPreflight')
)
Assert-Ownership ($rows.Count -eq 56) 'Prior 114-case inventory changed.'
# Three source-only pairs extend the managed lexical guard to Windows COM/LPT
# superscript device digits. Inert source mutations do not prove owned-file
# identity, actual native rejection or any filesystem/ownership provenance.
$rows+=@(
    @('inputs_reserved_device_superscript_one','I',"stem[3] == '\u00b9'",'false'),
    @('inputs_reserved_device_superscript_two','I',"stem[3] == '\u00b2'",'false'),
    @('inputs_reserved_device_superscript_three','I',"stem[3] == '\u00b3'",'false')
)
Assert-Ownership ($rows.Count -eq 59) 'Historical 59-source-pair prefix changed.'
# Native ownership redesign: appended directed source pairs, never replacements
# for the historical IDs. They are bounded callsite/outcome correspondence checks,
# not execution of these paths or exhaustive Windows lifetime/exception proof.
$rows+=@(
    @('inputs_native_acquire_outcome_before_wrappers','I','original.AcquireAttempted = true;','original.AcquireAttempted = false;'),
    @('inputs_nonowning_wrappers_from_first_construction','I','new SafeFileHandle(original.Original, false)','new SafeFileHandle(original.Original, true)'),
    @('inputs_stream_construction_and_finalizer_before_use','I','GC.SuppressFinalize(original.Stream); original.StreamFinalizerSuppressed = true;','original.StreamFinalizerSuppressed = true;'),
    @('inputs_live_close_attempt_blocks_all_queries','I','original.Aliased || original.CloseAttempted ||','original.Aliased || false ||'),
    @('inputs_raw_alias_quarantines_both_records','I','original.Aliased = true; other.Aliased = true;','original.Aliased = true;'),
    @('inputs_failed_handle_constructor_denies_close','I','original.HandleAttempted && (!original.HandleReturned || !original.HandleSucceeded ||','original.HandleAttempted && (false || !original.HandleSucceeded ||'),
    @('inputs_failed_stream_constructor_denies_close','I','original.StreamAttempted && (!original.StreamReturned || !original.StreamSucceeded ||','original.StreamAttempted && (false || !original.StreamSucceeded ||'),
    @('inputs_preclose_wrapper_state_quarantines_stale_value','I','original.HandleAttempted && (original.Handle.IsClosed || original.Handle.IsInvalid ||','original.HandleAttempted && (false || original.Handle.IsInvalid ||'),
    @('inputs_close_unknown_or_failed_never_retried','I','if (original.CloseAttempted) {','if (false) {'),
    @('inputs_native_close_result_precedes_invalidate_and_dispose','I','original.CloseError = Marshal.GetLastWin32Error(); original.CloseReturned = true;','original.CloseError = Marshal.GetLastWin32Error(); original.CloseReturned = false;'),
    @('inputs_managed_disposal_never_native_close_authority','I','original.CloseSucceeded = CloseHandle(original.Original);','original.CloseSucceeded = true;'),
    @('inputs_only_minus_one_is_known_failed_no_handle','I','original.Original != new IntPtr(-1) || original.HandleAttempted','ValidNativeValue(original.Original) || original.HandleAttempted'),
    @('inputs_generic_cleanup_continues_proven_resources_without_success_on_errors','I','if (releaseErrors.Count != 0) throw','if (false) throw'),
    @('inputs_terminal_requires_successfully_acquired_wrappers','I','ReleaseNativeFile(item.ReadFile, true);','ReleaseNativeFile(item.ReadFile, false);'),
    @('inputs_terminal_observer_checks_every_native_slot_without_io','I','AssertNativeFileRetired(item.ReadFile, true); AssertNativeFileRetired(item.IdentityFile, true);','AssertNativeFileRetired(item.IdentityFile, true);'),
    @('inputs_terminal_release_reverse_native_order_and_original_admission','I','invocation.AssertTerminalPayloadReleaseAdmission(this, owner, closure);',''),
    @('inputs_descriptor_exact_retained_reference_is_authority','I','!Object.ReferenceEquals(executableBorrow, exactBorrow)','false'),
    @('inputs_descriptor_binds_native_records_spec_and_stream','I','!Object.ReferenceEquals(exactBorrow.ReadFile, item.ReadFile)','false'),
    @('inputs_registration_clear_revokes_without_fallible_tail','I','executableBorrowRevoked = true; borrower = null;','executableBorrowRevoked = false; borrower = null;'),
    @('inputs_internal_stream_identity_replaces_name_not_physical_proof','I','!Object.ReferenceEquals(original.Stream.SafeFileHandle, original.Handle)','false'),
    @('compiler_generic_name_validation_remains_required','B','!String.Equals(stream.Name, executable, StringComparison.OrdinalIgnoreCase)','false'),
    @('compiler_typed_descriptor_validates_before_native_metadata','B','!Object.ReferenceEquals(ownedInputs.AssertCompilerExecutableBorrow(this, ownedExecutable), exactStream)','false'),
    @('compiler_owned_lock_retained_nonowning_original','B','new SafeFileHandle(slot.Raw, false)','new SafeFileHandle(slot.Raw, true)'),
    @('compiler_close_requires_known_owned_slot_once','B','outcome.Original != slot.Raw || !outcome.Returned || !outcome.Succeeded','outcome.Original != slot.Raw || false || !outcome.Succeeded'),
    @('compiler_close_outcome_and_wrapper_disposal_separate','B','slot.CloseError = Marshal.GetLastWin32Error(); outcome.Succeeded = closed; outcome.Returned = true;','slot.CloseError = Marshal.GetLastWin32Error(); outcome.Succeeded = true; outcome.Returned = true;'),
    @('compiler_failed_file_acquisition_zero_not_absence','B','original.Slot.Raw == new IntPtr(-1);','!ValidHandle(original.Slot.Raw);'),
    @('compiler_terminal_release_native_resources_before_registration','B','ReleaseOriginalPrivateResources(); AssertProducerPrivateResourcesReleased();','AssertProducerPrivateResourcesReleased();'),
    @('compiler_terminal_backlink_checks_native_results_not_dispose','B','!PrivateSlotReleased(executableLockOwner.Slot) || !PrivateSlotReleased(outputPair.Parent)','false || !PrivateSlotReleased(outputPair.Parent)'),
    @('compiler_stdio_pair_outputs_retained_before_validation','B','CreatePipe(out pair.Read.Raw, out pair.Write.Raw, ref security, 0)','CreatePipe(out discardedRead, out pair.Write.Raw, ref security, 0)'),
    @('compiler_collector_last_use_join_eof_before_parent_close','B','bool joined = original.Worker.Join(0);','bool joined = true;')
)
Assert-Ownership ($rows.Count -eq 89) 'Prior native-ownership 89-source-pair prefix changed.'
# Separate directed negatives for the independent review's bounded lifecycle
# gaps. In particular, local child-end retirement is not inherited-child closure.
$rows+=@(
    @('compiler_stdio_pair_requires_known_successful_original_acquisition','B','!IsOriginalPair(pair) || !pair.Attempted || !pair.Returned || !pair.Succeeded ||','!IsOriginalPair(pair) || !pair.Attempted || false ||'),
    @('compiler_early_retirement_covers_all_three_original_local_child_ends','B','new CompilerPipePair[] { outputPair, errorPair, inputPair }','new CompilerPipePair[] { outputPair, errorPair }'),
    @('compiler_early_retirement_closes_unused_original_parent_stdin','B','RetirePrivateSlot(inputPair.Parent);',''),
    @('compiler_resume_requires_all_four_known_early_retirements','B','AssertEarlyStdioRetired();',''),
    @('compiler_collector_finally_publishes_last_use_on_the_original_worker','B','finally { if (original != null) Interlocked.Exchange(ref original.LastUseSettled, 1); }','finally { if (original != null) Interlocked.Exchange(ref original.LastUseSettled, 0); }')
)
Assert-Ownership ($rows.Count -eq 94) 'Early-retirement/collector source inventory changed.'
# Direct helper-call purity controls; this is not a transitive call-graph proof.
$rows+=@(
    @('inputs_managed_factory_rejects_new_native_acquisition_helper','I','return new BuildInputLeaseSet(copied);','BuildInputLeaseSet unretained = new BuildInputLeaseSet(copied); unretained.AcquireNativeFile(unretained.inputs[0].ReadFile); return unretained;'),
    @('compiler_managed_factory_rejects_new_native_acquisition_helper','B','inputs.RegisterCommand(prepared);','prepared.OpenOriginalExecutableLock(); inputs.RegisterCommand(prepared);')
)
$results=[Collections.Generic.List[object]]::new()
foreach($row in $rows){
    $text=$documents[$row[1]].text
    Check-Case $row[0] $text
    $results.Add([pscustomobject]@{name=$row[0];negative_control=$false;passed=$true})
    Assert-Ownership ($text.Contains($row[2])) ('Missing mutation target: '+$row[0])
    $changed=$text.Replace($row[2],$row[3]);$failure=$null
    if($row[0] -ceq 'compiler_resume_requires_all_four_known_early_retirements') {
        # Fixed literal source slice only: preserve the other terminal/Seal
        # callers. No subject parsing, evaluation or newline spelling dependency.
        $startMarker='private void ResumeOriginal(GuardianLaunch guardian)'
        $endMarker='private bool CollectorsClosed()'
        $start=$text.IndexOf($startMarker,[StringComparison]::Ordinal)
        $end=$text.IndexOf($endMarker,[StringComparison]::Ordinal)
        Assert-Ownership ($start -ge 0 -and $end -gt $start -and
            $text.LastIndexOf($startMarker,[StringComparison]::Ordinal) -eq $start -and
            $text.LastIndexOf($endMarker,[StringComparison]::Ordinal) -eq $end) 'Resume mutation literal scope differs.'
        $original=$text.Substring($start,$end-$start)
        $anchor=$original.IndexOf($row[2],[StringComparison]::Ordinal)
        Assert-Ownership ($anchor -ge 0 -and $original.LastIndexOf($row[2],[StringComparison]::Ordinal) -eq $anchor) 'Resume mutation needs one original early-retirement gate.'
        $changed=$text.Remove($start,$original.Length).Insert($start,$original.Replace($row[2],$row[3]))
    }
    if($row[0] -ceq 'adapter_factory_derivation_precedes_clr_selection') {
        # Move the one existing selection, preserving its call count. Inserting
        # an extra call would test only uniqueness, not this ordering barrier.
        $moveTokens=$null;$moveErrors=$null
        $moveAst=[Management.Automation.Language.Parser]::ParseInput($text,[ref]$moveTokens,[ref]$moveErrors)
        $factory=@($moveAst.EndBlock.Statements | Where-Object {$_ -is [Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -ceq 'New-StratusBuildInputOwner'})
        Assert-Ownership ($moveErrors.Count -eq 0 -and $factory.Count -eq 1) 'Ordering mutation scope differs.'
        $original=$factory[0].Extent.Text
        $moved=$original.Replace('$types=Get-StratusBuildInputTypes $BootstrapAssembly','')
        $moved=$moved.Replace('$SpecRecords=Get-StratusParentCommandBuildInputSpecifications', '$types=Get-StratusBuildInputTypes $BootstrapAssembly; $SpecRecords=Get-StratusParentCommandBuildInputSpecifications')
        $changed=$text.Remove($factory[0].Extent.StartOffset,$original.Length).Insert($factory[0].Extent.StartOffset,$moved)
    }
    try{Check-Case $row[0] $changed}catch{$failure=$_.Exception.Message}
    Assert-Ownership ($null -ne $failure -and $failure.StartsWith('Ownership source contract:')) ('Negative survived or failed outside the source predicate: '+$row[0])
    $results.Add([pscustomobject]@{name=($row[0]+' mutation rejected');negative_control=$true;passed=$true})
}
# Deliberately exercise a built-in, side-effect-free managed method, not a subject
# assembly. The old packaging expands ten strings into ten MethodInfo parameters.
$values=[string[]]@('a','b','c','d','e','f','g','h','i','j')
$concat=[string].GetMethod('Concat',[Type[]]@([string[]]))
Assert-Ownership ($null -ne $concat -and $concat.DeclaringType -eq [string] -and $concat.IsStatic) 'Exact BCL String.Concat overload unavailable.'
$joined=$concat.Invoke($null,[object[]]@(,$values))
Assert-Ownership ($joined -ceq 'abcdefghij') 'Single typed-array argument packaging differs.'
$results.Add([pscustomobject]@{name='BCL typed ten-element array remains one MethodInfo argument';negative_control=$false;passed=$true})
$packagingFailure=$null
try{$null=$concat.Invoke($null,[object[]]@($values))}catch{$packagingFailure=$_.Exception}
Assert-Ownership ($null -ne $packagingFailure -and ($packagingFailure -is [Reflection.TargetParameterCountException] -or
    $packagingFailure.InnerException -is [Reflection.TargetParameterCountException])) 'Old array-unrolling form did not fail specifically on parameter count.'
$results.Add([pscustomobject]@{name='BCL old ten-parameter expansion is rejected';negative_control=$true;passed=$true})
$expected=194
Assert-Ownership ($rows.Count -eq 96 -and $results.Count -eq $expected) 'Fixed case count differs.'
foreach($document in $documents.Values){Assert-Ownership ((Get-FileHash -LiteralPath $document.path -Algorithm SHA256).Hash.ToLowerInvariant() -ceq $document.sha256) 'Subject source drifted after its byte snapshot.'}
$testAfter=Read-OwnershipSource $PSCommandPath
Assert-Ownership ($testAfter.sha256 -ceq $testBefore.sha256) 'Test changed during inspection.'
[pscustomobject][ordered]@{format='stratus-build-input-ownership-source-contract-tests-v1';expected=$expected;executed=$results.Count;passed=$results.Count;failed=0
    ps_version=$PSVersionTable.PSVersion.ToString();ps_edition=$PSVersionTable.PSEdition
    inputs_sha256=$documents.I.sha256;bootstrap_sha256=$documents.B.sha256;adapter_sha256=$documents.P.sha256
    test_sha256=$testAfter.sha256;test_sha256_before=$testBefore.sha256;lexical_harness_preflight_passed=$true
    subject_source_tokens_and_mutations_only=$true;bcl_array_packaging_regression_executed=$true
    semantic_csharp_verified=$false;subject_powershell_executed=$false
    compiler_executed=$false;runtime_loaded=$false;native_executed=$false;filesystem_fixture_created=$false
    watchdog_qualified=$false;output_writer_scope_proven=$false;build_causality_qualified=$false;full_goal_accepted=$false
    results=$results.ToArray()} | ConvertTo-Json -Depth 5 -Compress
