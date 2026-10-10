[CmdletBinding()]
param([string] $ProducerPath,[string] $BootstrapPath,[string] $DeadlinePath,[string] $GuardianPath,[string] $ControlChannelPath)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if(-not $ProducerPath){$ProducerPath=Join-Path $PSScriptRoot '..\Stratus.RuntimeBuild.Producer.cs'}
if(-not $BootstrapPath){$BootstrapPath=Join-Path $PSScriptRoot '..\Stratus.RuntimeBuild.Bootstrap.cs'}
if(-not $DeadlinePath){$DeadlinePath=Join-Path $PSScriptRoot '..\Stratus.RuntimeBuild.Deadline.cs'}
if(-not $GuardianPath){$GuardianPath=Join-Path $PSScriptRoot '..\Stratus.RuntimeBuild.GuardianLaunch.cs'}
if(-not $ControlChannelPath){$ControlChannelPath=Join-Path $PSScriptRoot '..\Stratus.RuntimeBuild.ControlChannel.cs'}
# Subject source tokens only. No C# compilation, loading, native invocation,
# producer lifecycle, process/Job/pipe or filesystem fixture is executed.
# These are scoped source-shape checks, not a semantic C# or Windows proof.
function Assert-Producer {param([bool] $Okay,[string] $Message) if(-not $Okay){throw ('Producer source contract: '+$Message)}}
function Read-ProducerSource {
    param([string]$Path)
    $full=[IO.Path]::GetFullPath($Path)
    # Bound allocation and every delivered read; a FileInfo precheck alone
    # cannot stop ReadAllBytes from allocating after concurrent file growth.
    $stream=[IO.FileStream]::new($full,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{
        $length=$stream.Length
        Assert-Producer ($length -gt 0 -and $length -le 1048576) 'Inspection file exceeds one MiB.'
        $raw=[byte[]]::new([int]$length);$offset=0
        while($offset -lt $raw.Length){
            $count=$stream.Read($raw,$offset,$raw.Length-$offset)
            Assert-Producer ($count -gt 0) 'Inspection file ended before its bounded length.'
            $offset+=$count
        }
        Assert-Producer ($stream.ReadByte() -eq -1 -and $stream.Length -eq $length) 'File length changed while reading.'
    }finally{$stream.Dispose()}
    $text=[Text.UTF8Encoding]::new($false,$true).GetString($raw)
    $hash=[Security.Cryptography.SHA256]::Create()
    try{$digest=[BitConverter]::ToString($hash.ComputeHash($raw)).Replace('-','').ToLowerInvariant()}finally{$hash.Dispose()}
    return [pscustomobject]@{path=$Path;text=$text;sha256=$digest}
}
function Tokens {
    param([string] $Text)
    Assert-Producer ($null -ne $Text -and $Text.Length -le 1048576) 'Token input exceeds its fixed character bound.'
    $pattern=@'
//[^\r\n]* | /\*.*?\*/ | @"(?:""|[^"])*" | "(?:\\.|[^"\\])*" | '(?:\\.|[^'\\])*'
| [A-Za-z_][A-Za-z_0-9]* | [0-9]+ | == | != | >= | <= | \+\+ | -- | && | \|\| | => | [^\s]
'@
    $result=[Collections.Generic.List[string]]::new()
    $options=[Text.RegularExpressions.RegexOptions]::Singleline -bor [Text.RegularExpressions.RegexOptions]::IgnorePatternWhitespace
    foreach($m in [regex]::Matches($Text,$pattern,$options,[TimeSpan]::FromSeconds(5))){
        if(-not $m.Value.StartsWith('//') -and -not $m.Value.StartsWith('/*')){Assert-Producer ($result.Count -lt 262144) 'Token inspection bound exceeded.';$result.Add($m.Value)}
    }
    Assert-Producer ($result.Count -le 262144) 'Token inspection bound exceeded.'
    return ,$result.ToArray()
}
function Find {
    param([string[]] $Source,[string] $Sequence)
    $wanted=Tokens $Sequence;Assert-Producer ($wanted.Count -gt 0) 'Empty predicate.'
    $found=[Collections.Generic.List[int]]::new()
    for($i=0;$i -le $Source.Count-$wanted.Count;$i++){
        $same=$true
        for($j=0;$j -lt $wanted.Count;$j++){if($Source[$i+$j] -cne $wanted[$j]){$same=$false;break}}
        if($same){$found.Add($i)}
    }
    return ,$found.ToArray()
}
function Has {param([string[]] $Source,[string] $Sequence,[int] $Count=1) Assert-Producer ((Find $Source $Sequence).Count -eq $Count) ('Sequence differs: '+$Sequence)}
function Order {
    param([string[]] $Source,[string[]] $Sequences)
    $last=-1
    foreach($sequence in $Sequences){$found=Find $Source $sequence;Assert-Producer ($found.Count -eq 1 -and $found[0] -gt $last) ('Order differs: '+$sequence);$last=$found[0]}
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
    Assert-Producer ($matches.Count -eq 1) ('Missing/ambiguous scoped block: '+$Signature)
    $open=$matches[0]
    while($open -lt $Source.Count -and $Source[$open] -cne '{'){$open++}
    Assert-Producer ($open -lt $Source.Count) 'Opening brace missing.'
    $depth=0
    for($i=$open;$i -lt $Source.Count;$i++){
        if($Source[$i] -ceq '{'){$depth++}elseif($Source[$i] -ceq '}'){$depth--}
        if($depth -eq 0){return ,([string[]]$Source[$matches[0]..$i])}
    }
    throw 'Producer source contract: Unbalanced source block.'
}
function Method {param([string[]] $Class,[string] $Signature) return ,(Block $Class $Signature -Direct)}
function Replace-Tokens {
    param([string[]] $Source,[string] $Needle,[string] $Replacement)
    $at=Find $Source $Needle;Assert-Producer ($at.Count -eq 1) ('Mutation anchor differs: '+$Needle)
    $old=Tokens $Needle;$new=Tokens $Replacement;$result=[Collections.Generic.List[string]]::new()
    for($i=0;$i -lt $at[0];$i++){$result.Add($Source[$i])}
    foreach($token in $new){$result.Add($token)}
    for($i=$at[0]+$old.Count;$i -lt $Source.Count;$i++){$result.Add($Source[$i])}
    return ,$result.ToArray()
}
function Move-Tokens {
    param([string[]] $Source,[string] $Needle,[string] $Before)
    $without=Replace-Tokens $Source $Needle '/* removed only the selected original declaration */'
    return ,(Replace-Tokens $without $Before ($Needle+' '+$Before))
}
$producer=Read-ProducerSource $ProducerPath
$bootstrap=Read-ProducerSource $BootstrapPath
$deadline=Read-ProducerSource $DeadlinePath
$guardian=Read-ProducerSource $GuardianPath
$channel=Read-ProducerSource $ControlChannelPath
$testSource=Read-ProducerSource $PSCommandPath
$p=Block (Tokens $producer.text) 'public sealed class DisposableProducerLaunch : IDisposable'
$c=Block (Tokens $producer.text) 'internal sealed class ProducerStopCapability'
$copy=Block $c 'internal sealed class LaunchCopy'
$b=Block (Tokens $bootstrap.text) 'public sealed class CompilerCommand : IDisposable'
$d=Block (Tokens $deadline.text) 'internal sealed class ProducerDeadline'
$g=Block (Tokens $guardian.text) 'public sealed class GuardianLaunch : IDisposable'
$h=Block (Tokens $channel.text) 'internal sealed class ProducerGuardianChannel'
$blocks=@{
    wrapper=$p
    prepare=Method $p 'internal static DisposableProducerLaunch PreparePreloaded('
    start=Method $p 'public void StartSuspended('
    preparecap=Method $p 'public void PrepareGuardianStopCapability('
    owns=Method $p 'internal bool OwnsCommand('
    enter=Method $p 'private void Enter('
    admission=Method $p 'private void RequireAdmission('
    contain=Method $p 'public bool PollContainment('
    dispose=[string[]]@((Method $p 'public void Dispose(')+(Method $p 'internal CompilerResult PollNormalTerminal(')+(Method $b 'internal long ObserveProducerNormalExit('))
    capconstructor=Method $c 'internal ProducerStopCapability('
    disposition=Method $c 'private DuplicateDisposition Disposition('
    identity=Method $c 'private void AssertIdentity('
    acquire=Method $c 'internal void AcquireOriginal('
    close=Method $c 'internal void CloseAfterProducerDeath('
    bootstrap=Method $b 'internal void AcquireProducerStopCapability('
    bootstrapclass=$b
    closeowned=Method $b 'private static void CloseOwned('
    bootstrapdispose=Method $b 'public void Dispose('
    bootstrapstart=Method $b 'private void StartSuspendedOriginal('
    releaselaunch=Method $b 'private void ReleaseLaunchMemory('
    freeallocation=Method $b 'private static void FreeOwnedAllocation('
    launchcopyacquire=Method $copy 'internal void Acquire('
    launchcopyretire=Method $copy 'internal void RetireLaunchInheritance('
    launchcopyretired=Method $copy 'internal void AssertLaunchInheritanceRetired('
    launchcopyborrow=Method $copy 'internal IntPtr BorrowForLaunch('
    serializedcopy=Method $p 'internal void AcquireGuardianLaunchCopy('
    producerconstructor=Method $p 'private DisposableProducerLaunch('
    binddeadline=Method $p 'internal ProducerDeadline BindGuardianDeadline('
    deadlineclass=$d
    deadlineconstructor=Method $d 'internal ProducerDeadline('
    deadlineowner=Method $d 'private void RequireOwner('
    deadlinebegun=Method $d 'private void RequireBegun('
    deadlinebegin=Method $d 'internal void Begin('
    deadlineprepared=Method $d 'internal void AssertPreparedOwner('
    deadlineactive=Method $d 'private void AssertActiveCore('
    deadlineguardian=Method $d 'private void RequireGuardian('
    deadlinesnapshot=Method $d 'internal ProducerDeadlineSnapshot SnapshotForGuardian('
    sharedfactory=Method $b 'internal static CompilerCommand PrepareOwnedWithDeadline('
    bootstrapclock=Method $b 'private void ClockAndFaultBoundary('
    bootstrapcleanup=Method $b 'private void CleanupBoundary('
    guardianstart=Method $g 'public void StartRunning('
    guardianadmission=Method $g 'private void Admission('
    guardianready=Method $g 'private GuardianReadyObservation ReadReadyCore('
    channelclass=$h
    channelowner=Method $h 'private void RequireOwner('
    channelpair=Method $h 'private static void RequirePair('
    channelcreate=Method $h 'private static void CreateOriginalPair('
    channelopen=Method $h 'internal void OpenForProducer('
    channeldistinct=Method $h 'private void RequireDistinct('
    channelpborrow=Method $h 'internal ChannelEndpoints BorrowProducerEndpoints('
    channelwborrow=Method $h 'internal ChannelEndpoints BorrowGuardianEndpoints('
    channelppending=Method $h 'internal void AssertProducerDeliveryPending('
    channelwpending=Method $h 'internal void AssertGuardianDeliveryPending('
    channelinherit=Method $h 'private static void RetireInheritance('
    channelclose=Method $h 'private static void CloseOriginal('
    channelpretire=Method $h 'internal void RetireProducerDelivery('
    channelwretire=Method $h 'internal void RetireGuardianDelivery('
    channelboth=Method $h 'internal void AssertBothDeliveriesRetired('
    channelclosed=Method $h 'private static void RequireClosed('
    channelclosepair=Method $h 'private static void CloseKnownPair('
    channeldispose=Method $h 'internal void DisposeAfterContainment('
}
# Each row executes only our fixed token predicate, first on the selected source
# and then on an independently mutated token copy. Neither copy is C#-evaluated.
Has (Method $b 'public void StartSuspended(') 'StartSuspendedOriginal(null);'
$rows=@(
    @('prepare_is_managed_and_copies_inputs','prepare',{
        param($s)
        Has $s 'internal static DisposableProducerLaunch PreparePreloaded(StartupPreloadOwner preload)'
        Has $s 'StartupPreloadOwner.LaunchData selected = preload.BorrowProducerPreparation();'
        Has $s 'string[] copiedArguments = (string[])arguments.Clone();'
        Has $s 'foreach (KeyValuePair<string, string> entry in environment) copiedEnvironment.Add(entry.Key, entry.Value);'
        Has $s 'return new DisposableProducerLaunch(preload, inputs, executable, hash, copiedArguments, directory, copiedEnvironment, contextBytes, contextSha256, contextLength);'
        foreach($forbidden in @('PrepareOwned(','PrepareOwnedWithDeadline(','GetTickCount64(','StartSuspended(','DuplicateHandle(','File.','Directory.','Process.','Assembly.')){Has $s $forbidden 0}
    },'return new DisposableProducerLaunch(preload, inputs, executable, hash, copiedArguments, directory, copiedEnvironment, contextBytes, contextSha256, contextLength);','CompilerCommand.PrepareOwned(inputs, executable, hash, arguments, directory, environment); return new DisposableProducerLaunch(preload, inputs, executable, hash, copiedArguments, directory, copiedEnvironment, contextBytes, contextSha256, contextLength);'),
    @('command_retained_before_native_start','start',{
        param($s)
        Order $s @('RequireAdmission(); if (startAttempted)','startAttempted = true;','startupDeadline.Begin(this);','contextSection.OpenForProducer(this); RequireAdmission();','controlChannel.OpenForProducer(this); RequireAdmission();','command = CompilerCommand.PrepareOwnedWithDeadline(inputs, executable, executableHash, arguments, directory, environment, startupDeadline, this, controlChannel, contextSection);','nativeStartAttempted = true;','command.PrepareBirthJob(); RequireAdmission(); command.StartSuspended();','RequireAdmission(); startCompleted = true;')
        Has $s 'catch (Exception error) { Record(error); throw; } finally { Leave(); }'
    },'command = CompilerCommand.PrepareOwnedWithDeadline','CompilerCommand.PrepareOwnedWithDeadline'),
    @('capability_retained_before_native_acquisition','preparecap',{
        param($s)
        Has $s 'if (!startCompleted || command == null || capabilityAttempted)'
        Order $s @('capabilityAttempted = true;','stopCapability = new ProducerStopCapability(this, command);','command.AcquireProducerStopCapability(this, stopCapability);')
        Has $s 'catch (Exception error) { Record(error); throw; } finally { Leave(); }'
    },'stopCapability = new ProducerStopCapability(this, command);','new ProducerStopCapability(this, command);'),
    @('owner_reference_closure','owns',{
        param($s)
        Has $s '!failed && !disposed && !releaseStarted && !containmentStarted && startCompleted && capabilityAttempted'
        Has $s 'Object.ReferenceEquals(command, candidate) && Object.ReferenceEquals(stopCapability, capability)'
    },'Object.ReferenceEquals(command, candidate)','candidate != null'),
    @('public_surface_has_no_resume_or_raw_export','wrapper',{
        param($s)
        Has $s 'public' 5
        foreach($api in @('public sealed class DisposableProducerLaunch','internal static DisposableProducerLaunch PreparePreloaded(StartupPreloadOwner preload)','public void StartSuspended(','public void PrepareGuardianStopCapability(','public bool PollContainment(','public void Dispose(')){Has $s $api}
        Has $s 'public static DisposableProducerLaunch Prepare(' 0
        foreach($forbidden in @('public void Resume(','public IntPtr','public CompilerCommand','OpenProcess(','Process.GetProcessById(','GetCurrentProcess(')){Has $s $forbidden 0}
    },'public void Dispose()','public void Resume() {} public void Dispose()'),
    @('overlap_records_sticky_failure','enter',{
        param($s)
        Has $s 'if (Interlocked.CompareExchange(ref operation, 1, 0) != 0)'
        Has $s 'Record(error); throw error;'
    },'Interlocked.CompareExchange(ref operation, 1, 0) != 0','Interlocked.CompareExchange(ref operation, 1, 0) == 0'),
    @('failed_or_containing_wrapper_cannot_admit','admission',{
        param($s)
        Has $s 'if (disposed || releaseStarted || containmentStarted)'
        Has $s 'lock (failureGate) { if (failed) throw new AggregateException(failures.ToArray()); }'
    },'disposed || releaseStarted || containmentStarted','disposed || releaseStarted'),
    @('missing_command_after_native_attempt_is_not_proof','contain',{
        param($s)
        Has $s 'if (command == null) { if (nativeStartAttempted || stopCapability != null || transferCapability != null)'
        Order $s @('containmentStarted = true;','if (command == null)','if (!command.PollContainment()) return false;')
    },'nativeStartAttempted || stopCapability != null','stopCapability != null'),
    @('containment_precedes_cap_close_and_proof','contain',{
        param($s)
        Order $s @('if (!command.PollContainment()) return false;','if (stopCapability != null) stopCapability.CloseAfterProducerDeath(); if (transferCapability != null) transferCapability.CloseAfterProducerDeath(); containmentProven = true; return true;')
        Has $s 'command.Dispose(' 0
    },'if (!command.PollContainment()) return false;','/* missing exact original command containment */'),
    @('dispose_requires_proof_and_cap_closure_before_input_release','dispose',{
        param($s)
        $dispose=Block $s 'public void Dispose('
        Order $dispose @('if (!containmentProven && !normalClosureProven)','if (guardian != null)','if (stopCapability != null && !stopCapability.IsClosed)','releaseStarted = true;','if (command != null) command.Dispose();','disposed = true;')
        $normal=Block $s 'internal CompilerResult PollNormalTerminal('
        Order $normal @('if (!continuationCompleted || !OwnsGuardian(originalGuardian, stopCapability) || command == null)','originalGuardian.AssertNormalProducerObservation(this); CompilerResult observed = command.PollResult();','if (observed == null) return null;','long nativeExit = command.ObserveProducerNormalExit(this, observed);','if (stopCapability != null) stopCapability.CloseAfterProducerDeath();','if (transferCapability != null) transferCapability.CloseAfterProducerDeath();','RequireAdmission(); originalGuardian.AssertNormalProducerObservation(this); normalClosureProven = true; return normalResult;')
        Has $normal 'containmentProven = true;' 0;Has $normal 'PollContainment(' 0
        $native=Block $s 'internal long ObserveProducerNormalExit('
        Has $native '!Object.ReferenceEquals(originalDeadlineOwner, originalOwner) || sharedStartupDeadline == null || !closureProven || !Object.ReferenceEquals(completedResult, originalResult)'
        Has $native 'code != 0 || RootWait() != WaitObject || JobAccounting().active != 0 || !CollectorsClosed()'
        Has $s 'inputs.Dispose(' 0
    },'stopCapability != null && !stopCapability.IsClosed','stopCapability != null && false'),
    @('capability_constructor_has_no_native_effect','capconstructor',{
        param($s)
        Has $s 'if (owner == null || command == null)'
        Has $s 'this.owner = owner; this.command = command;'
        foreach($forbidden in @('DuplicateHandle(','AssertIdentity(','CloseHandle(','GetCurrentProcess(','AcquireOriginal(')){Has $s $forbidden 0}
    },'this.owner = owner;','DuplicateHandle(); this.owner = owner;'),
    @('known_absent_duplicate_requires_zero_and_known_return','disposition',{
        param($s)
        Has $s 'if (duplicate == IntPtr.Zero && (!duplicateAttempted || (duplicateReturned && !duplicateSucceeded))) return DuplicateDisposition.None;'
    },'duplicateReturned && !duplicateSucceeded','!duplicateSucceeded'),
    @('owned_duplicate_requires_complete_retained_outcome','disposition',{
        param($s)
        Has $s 'acquisitionStarted && duplicateAttempted && duplicateReturned && duplicateSucceeded && Valid(duplicate) && Valid(originalProcess) && duplicate != originalProcess && originalPid > 0 && originalBirth > 0'
        Has $s 'return DuplicateDisposition.Ambiguous;'
    },'duplicateReturned && duplicateSucceeded','duplicateReturned'),
    @('identity_excludes_shared_host_and_checks_original_pid','identity',{
        param($s)
        Has $s 'before != expectedWait || GetProcessId(handle) != (uint)originalPid || (uint)originalPid == GetCurrentProcessId()'
        Has $s 'uint expectedWait = alive ? 258u : 0u;'
    },'|| (uint)originalPid == GetCurrentProcessId()','/* current-host exclusion removed */'),
    @('identity_requires_exact_native_birth_and_physical_death','identity',{
        param($s)
        Order $s @('uint before = WaitForSingleObject(handle, 0);','GetProcessTimes(handle, out created, out exited, out kernel, out user)','if (created != originalBirth || (!alive && (exited <= 0 || exited < created)))','uint after = WaitForSingleObject(handle, 0);','if (after != expectedWait)')
    },'created != originalBirth','created <= 0'),
    @('acquire_is_single_use_from_exact_owner','acquire',{
        param($s)
        Order $s @('if (closed || acquisitionStarted || !owner.OwnsCommand(command, this))','acquisitionStarted = true;','originalProcess = process; originalPid = pid; originalBirth = birth;','AssertIdentity(originalProcess, true); duplicateAttempted = true;')
    },'!owner.OwnsCommand(command, this)','false'),
    @('duplicate_only_original_P_with_local_limited_rights','acquire',{
        param($s)
        Has $s 'DuplicateHandle(GetCurrentProcess(), originalProcess, GetCurrentProcess(), out duplicate, 0x101001, false, 0)'
        Has $s 'DuplicateHandle(' 1
        Has $s 'OpenProcess(' 0
    },'GetCurrentProcess(), originalProcess, GetCurrentProcess(), out duplicate','GetCurrentProcess(), GetCurrentProcess(), GetCurrentProcess(), out duplicate'),
    @('duplicate_raw_outcome_is_retained_before_acceptance','acquire',{
        param($s)
        Order $s @('duplicateAttempted = true;','bool copied = DuplicateHandle(','duplicateSucceeded = copied; duplicateReturned = true;','if (!copied)','if (Disposition() != DuplicateDisposition.Owned)')
    },'duplicateSucceeded = copied; duplicateReturned = true;','duplicateSucceeded = copied;'),
    @('duplicate_and_original_rechecked_after_acquisition','acquire',{
        param($s)
        Order $s @('if (Disposition() != DuplicateDisposition.Owned)','AssertIdentity(duplicate, true); AssertIdentity(originalProcess, true);')
    },'AssertIdentity(duplicate, true); AssertIdentity(originalProcess, true);','AssertIdentity(duplicate, true);'),
    @('unknown_close_blocks_every_later_handle_use','close',{
        param($s)
        Order $s @('if (closeAttempted && !closeReturned)','if (closeReturned && closeSucceeded)','DuplicateDisposition disposition = Disposition();','AssertIdentity(originalProcess, false); AssertIdentity(duplicate, false);','bool released = CloseHandle(duplicate);')
    },'if (closeAttempted && !closeReturned)','if (false)'),
    @('known_successful_close_is_completed_without_requery','close',{
        param($s)
        Has $s 'if (closeReturned && closeSucceeded) { duplicate = IntPtr.Zero; closed = true; return; }'
        Order $s @('if (closeReturned && closeSucceeded)','DuplicateDisposition disposition = Disposition();')
    },'closeReturned && closeSucceeded','closeReturned'),
    @('ambiguous_duplicate_never_queried_or_closed','close',{
        param($s)
        Order $s @('if (disposition == DuplicateDisposition.None) { closed = true; return; }','if (disposition != DuplicateDisposition.Owned)','AssertIdentity(originalProcess, false); AssertIdentity(duplicate, false);','closeAttempted = true; closeReturned = false; closeSucceeded = false;')
    },'if (disposition != DuplicateDisposition.Owned)','if (false)'),
    @('native_close_outcome_latches_precede_final_clear','close',{
        param($s)
        Order $s @('closeAttempted = true; closeReturned = false; closeSucceeded = false;','bool released = CloseHandle(duplicate);','closeSucceeded = released; closeReturned = true;','if (!released) throw new Win32Exception(error, "Close original local producer stop duplicate after physical death"); duplicate = IntPtr.Zero; closed = true;')
    },'closeSucceeded = released; closeReturned = true;','closeSucceeded = released;'),
    @('bootstrap_exact_owner_never_resumed_and_attempt_latch','bootstrap',{
        param($s)
        Order $s @('producerStopAcquisitionAttempted = true;','if (owner == null || cap == null || !owner.OwnsCommand(this, cap))','if (!started || resumeAttempted || resumed)','uint previous = SuspendThread(thread);')
        Has $s 'if (producerStopAcquisitionAttempted)'
    },'!started || resumeAttempted || resumed','!started || resumed'),
    @('suspension_rejects_before_same_restoring_call','bootstrap',{
        param($s)
        Order $s @('uint previous = SuspendThread(thread);','if (previous == UInt32.MaxValue)','if (previous != 1)','uint restored = ResumeThread(thread);','if (restored == UInt32.MaxValue)','if (restored != 2)')
        Has $s 'ResumeThread(' 1
    },'uint restored = ResumeThread(thread);','if (previous != 1)','move'),
    @('bootstrap_uses_private_original_and_checks_both_boundaries','bootstrap',{
        param($s)
        Has $s 'SuccessBoundary(); HeldExecutableBoundary(); RootIdentity(false);' 2
        Has $s 'if (RootWait() != WaitTimeout || JobAccounting().active != 1 || !owner.OwnsCommand(this, cap))' 2
        Has $s 'cap.AcquireOriginal(process, pid, birth);'
        Order $s @('SuccessBoundary(); RootIdentity(false);','cap.AcquireOriginal(process, pid, birth);','SuccessBoundary(); HeldExecutableBoundary(); RootIdentity(false); if (RootWait() != WaitTimeout || JobAccounting().active != 1 || !owner.OwnsCommand(this, cap))')
    },'cap.AcquireOriginal(process, pid, birth);','cap.AcquireOriginal(GetCurrentProcess(), pid, birth);'),
    @('bootstrap_acquisition_fault_is_sticky_and_operation_released','bootstrap',{
        param($s)
        Has $s 'Enter();'
        Has $s 'catch (Exception failure) { Record(failure); throw; } finally { Leave(); }'
    },'Record(failure);','/* fault record removed */')
)
Assert-Producer ($rows.Count -eq 27) 'The original 27 predicate/mutation pairs changed.'
# The original 54 cases remain intact. These additional pairs guard actual
# retained native-close outcomes in the command used to create the producer.
$rows+=@(
    @('compiler_has_separate_retained_close_records','bootstrapclass',{
        param($s)
        foreach($name in @('thread','process','job')){Has $s ('private readonly HandleCloseOutcome '+$name+'Close = new HandleCloseOutcome();')}
    },'private readonly HandleCloseOutcome processClose = new HandleCloseOutcome();','private readonly HandleCloseOutcome processClose = threadClose;'),
    @('compiler_unknown_close_rejects_before_native_retry','closeowned',{
        param($s)
        Order $s @('if (outcome.Attempted) { if (!outcome.Returned) throw new InvalidOperationException(', 'if (outcome.Succeeded)', 'bool released = CloseHandle(outcome.Original);')
        Has $s 'CloseHandle(' 1
    },'if (!outcome.Returned)','if (false)'),
    @('compiler_completed_close_only_clears_original_value','closeowned',{
        param($s)
        $success=Block $s 'if (outcome.Succeeded)'
        Order $success @('if (handle != IntPtr.Zero && handle != outcome.Original)','handle = IntPtr.Zero; return;')
        Has $success 'CloseHandle(' 0
        Has $success 'WaitForSingleObject(' 0
    },'handle = IntPtr.Zero; return;','CloseHandle(outcome.Original); handle = IntPtr.Zero; return;'),
    @('compiler_failed_close_requires_same_original','closeowned',{
        param($s)
        Order $s @('if (!ValidHandle(handle) || handle != outcome.Original)','outcome.Attempted = true; outcome.Returned = false; outcome.Succeeded = false;','bool released = CloseHandle(outcome.Original);')
    },'!ValidHandle(handle) || handle != outcome.Original','!ValidHandle(handle)'),
    @('compiler_close_records_outcome_before_clear','closeowned',{
        param($s)
        Order $s @('outcome.Attempted = true; outcome.Returned = false; outcome.Succeeded = false;','bool released = CloseHandle(outcome.Original);','int error = Marshal.GetLastWin32Error();','outcome.Succeeded = released; outcome.Returned = true;','if (!released) throw new Win32Exception(error, "Close proved-owned compiler handle"); handle = IntPtr.Zero;')
    },'outcome.Succeeded = released; outcome.Returned = true;','outcome.Succeeded = released;'),
    @('compiler_release_latch_and_inputs_surround_exact_closes','bootstrapdispose',{
        param($s)
        Order $s @('if (!releaseStarted)','releaseStarted = true;','CloseOwned(ref thread, threadClose); CloseOwned(ref process, processClose); CloseOwned(ref job, jobClose);','ownedInputs.ReleaseCommand(this); ownedInputsReleased = true;')
    },'CloseOwned(ref process, processClose);','CloseOwned(ref process, threadClose);')
)
Assert-Producer ($rows.Count -eq 33) 'The fixed close-outcome pairs changed.'
$rows+=@(
    @('attribute_actual_init_outcome_retained_separately','bootstrapstart',{
        param($s)
        Order $s @('InitializeProcThreadAttributeList(IntPtr.Zero, 2, 0, ref size);','attributesInitializationAttempted = true;','bool initialized = InitializeProcThreadAttributeList(launchAttributes, 2, 0, ref size);','attributesInitializationSucceeded = initialized; attributesInitializationReturned = true;','if (!initialized)','attributesInitialized = true;')
    },'attributesInitializationSucceeded = initialized; attributesInitializationReturned = true;','attributesInitializationSucceeded = initialized;'),
    @('unknown_attribute_init_blocks_all_releases','releaselaunch',{
        param($s)
        Order $s @('if (attributesInitializationAttempted && !attributesInitializationReturned) throw new InvalidOperationException(','bool knownInitialized =','DeleteProcThreadAttributeList(','FreeOwnedAllocation(ref launchAttributes, attributesFree);')
    },'attributesInitializationAttempted && !attributesInitializationReturned','false'),
    @('known_init_not_late_managed_latch_controls_deletion','releaselaunch',{
        param($s)
        Has $s 'bool knownInitialized = attributesInitializationAttempted && attributesInitializationReturned && attributesInitializationSucceeded;'
        Order $s @('if (knownInitialized)','if (attributesDeletion.Attempted)','if (!attributesDeletion.Returned)','DeleteProcThreadAttributeList(')
    },'if (knownInitialized)','if (attributesInitialized)'),
    @('attribute_deletion_return_precedes_dependent_frees','releaselaunch',{
        param($s)
        Order $s @('attributesDeletion.Original = launchAttributes;','attributesDeletion.Attempted = true;','DeleteProcThreadAttributeList(attributesDeletion.Original);','attributesDeletion.Returned = true;','attributesInitialized = false;','FreeOwnedAllocation(ref launchAttributes, attributesFree);','FreeOwnedAllocation(ref inheritedHandles, inheritedHandlesFree);','FreeOwnedAllocation(ref launchJobs, launchJobsFree);','FreeOwnedAllocation(ref launchEnvironment, launchEnvironmentFree);')
    },'attributesDeletion.Returned = true;','/* lost native deletion return */'),
    @('unknown_free_cannot_reuse_allocation_address','freeallocation',{
        param($s)
        Order $s @('if (outcome.Attempted)','if (!outcome.Returned) throw new InvalidOperationException(','if (allocation != IntPtr.Zero && allocation != outcome.Original)','allocation = IntPtr.Zero; return;','outcome.Original = allocation;','outcome.Attempted = true;','Marshal.FreeHGlobal(outcome.Original);','outcome.Returned = true;')
        Has $s 'Marshal.FreeHGlobal(' 1
    },'if (!outcome.Returned)','if (false)'),
    @('native_free_return_is_saved_before_clear','freeallocation',{
        param($s)
        Has $s 'Marshal.FreeHGlobal(outcome.Original); outcome.Returned = true; allocation = IntPtr.Zero;'
    },'outcome.Returned = true;','/* lost native free return */')
)
Assert-Producer ($rows.Count -eq 39) 'The existing 39 predicate/mutation pairs changed.'
$rows+=@(
    @('launch_copy_uses_only_original_restricted_stop_capability','launchcopyacquire',{
        param($s)
        Has $s 'DuplicateHandle(GetCurrentProcess(), source.duplicate, GetCurrentProcess(), out handle, 0x101001, true, 0)'
        Has $s 'DuplicateHandle(' 1
        Order $s @('attempted = true;','bool copied = DuplicateHandle(','succeeded = copied; returned = true;','if (!copied)','AssertLive();')
    },'source.duplicate, GetCurrentProcess(), out handle, 0x101001','GetCurrentProcess(), GetCurrentProcess(), out handle, 0x101001'),
    @('launch_copy_acquisition_serialized_with_producer_containment','serializedcopy',{
        param($s)
        Order $s @('Enter();','RequireAdmission(); if (!OwnsGuardian(candidate, stopCapability) || copy == null || !copy.BelongsTo(stopCapability, candidate))','copy.Acquire(); RequireAdmission();','catch (Exception error) { Record(error); throw; } finally { Leave(); }')
    },'Enter();','/* lost producer lifecycle gate */'),
    @('retirement_changes_only_the_separate_copy_once','launchcopyretire',{
        param($s)
        Order $s @('if (!Object.ReferenceEquals(guardian, candidate) || inheritanceRetirementAttempted)','RequireKnownOwned(); inheritanceRetirementAttempted = true;','bool changed = SetHandleInformation(handle, 1, 0);')
        Has $s 'SetHandleInformation(' 1
    },'SetHandleInformation(handle, 1, 0)','SetHandleInformation(source.duplicate, 1, 0)'),
    @('retirement_outcome_and_flags_before_verified_latch','launchcopyretire',{
        param($s)
        Order $s @('bool changed = SetHandleInformation(handle, 1, 0);','inheritanceRetirementSucceeded = changed; inheritanceRetirementReturned = true;','if (!changed)','GetHandleInformation(handle, out flags)','if ((flags & 3) != 0)','inheritanceRetirementVerified = true;')
    },'inheritanceRetirementSucceeded = changed; inheritanceRetirementReturned = true;','inheritanceRetirementSucceeded = changed;'),
    @('ready_boundary_requires_known_retirement_and_fresh_flags','launchcopyretired',{
        param($s)
        Order $s @('RequireKnownOwned();','if (!inheritanceRetirementAttempted || !inheritanceRetirementReturned || !inheritanceRetirementSucceeded || !inheritanceRetirementVerified)','GetHandleInformation(handle, out flags)','if ((flags & 3) != 0)')
    },'!inheritanceRetirementSucceeded || !inheritanceRetirementVerified','!inheritanceRetirementVerified'),
    @('retired_copy_cannot_be_exported_again','launchcopyborrow',{
        param($s)
        Order $s @('if (!Object.ReferenceEquals(guardian, candidate))','if (inheritanceRetirementAttempted)','AssertLive(); return handle;')
    },'if (inheritanceRetirementAttempted)','if (false)')
)
Assert-Producer ($rows.Count -eq 45) 'The fixed 45 predicate/mutation pairs changed.'
# Preserve all previous 90 semantic cases. These additional source-only pairs
# exercise the actual shared STARTUP wiring, not the future compiler-in-P path.
$rows+=@(
    @('deadline_has_private_original_owner_and_no_public_adoption','deadlineclass',{
        param($s)
        Has $s 'private readonly DisposableProducerLaunch originalOwner;'
        Has $s 'internal const ulong StartupMilliseconds = 60000;'
        Has $s 'public' 0
        foreach($forbidden in @('Reset(','Extend(','SetDeadline(','DateTime.UtcNow','Stopwatch.StartNew(')){Has $s $forbidden 0}
    },'private readonly DisposableProducerLaunch originalOwner;','private DisposableProducerLaunch originalOwner;'),
    @('deadline_constructor_only_binds_original_managed_owner','deadlineconstructor',{
        param($s)
        Has $s 'if (originalOwner == null)'
        Has $s 'this.originalOwner = originalOwner;'
        foreach($forbidden in @('GetTickCount64(','Begin(','AssertActive(','File.','Process.')){Has $s $forbidden 0}
    },'this.originalOwner = originalOwner;','GetTickCount64(); this.originalOwner = originalOwner;'),
    @('deadline_owner_requires_both_exact_reference_links','deadlineowner',{
        param($s)
        Has $s 'candidate == null || !Object.ReferenceEquals(originalOwner, candidate) || !candidate.OwnsDeadline(this)'
    },'!candidate.OwnsDeadline(this)','false'),
    @('deadline_begin_is_one_use_and_retains_native_return','deadlinebegin',{
        param($s)
        Order $s @('lock (gate)','RequireOwner(candidate);','if (failed || beginAttempted)','beginAttempted = true;','startedTick = GetTickCount64(); beginReturned = true;')
        Has $s 'GetTickCount64(' 1
    },'startedTick = GetTickCount64(); beginReturned = true;','startedTick = GetTickCount64();'),
    @('deadline_origin_and_checked_bound_precede_started_latch','deadlinebegin',{
        param($s)
        Order $s @('if (startedTick == 0)','deadlineTick = checked(startedTick + StartupMilliseconds);','lastObservedTick = startedTick; begun = true;')
    },'checked(startedTick + StartupMilliseconds)','startedTick + StartupMilliseconds'),
    @('deadline_begin_failure_cannot_restart_or_be_forgotten','deadlinebegin',{
        param($s)
        Has $s 'catch (Exception) { failed = true; throw; }'
        Has $s 'failed = false;' 0
        Has $s 'beginAttempted = false;' 0
    },'failed = true; throw;','throw;'),
    @('deadline_active_uses_fresh_native_monotonic_observation','deadlineactive',{
        param($s)
        Order $s @('RequireBegun();','ulong now = GetTickCount64();','if (now < startedTick || now < lastObservedTick)','if (now >= deadlineTick)','lastObservedTick = now;')
    },'now < startedTick || now < lastObservedTick','now < startedTick'),
    @('deadline_expiry_rejects_exact_terminal_tick','deadlineactive',{
        param($s)
        Has $s 'if (now >= deadlineTick) throw new TimeoutException('
    },'now >= deadlineTick','now > deadlineTick'),
    @('deadline_requires_known_origin_and_exact_fixed_duration','deadlinebegun',{
        param($s)
        Has $s 'failed || !beginAttempted || !beginReturned || !begun || startedTick == 0 || deadlineTick <= startedTick || deadlineTick - startedTick != StartupMilliseconds'
    },'!beginAttempted || !beginReturned || !begun','!beginAttempted || !begun'),
    @('deadline_factory_validation_remains_managed_only','deadlineprepared',{
        param($s)
        Has $s 'lock (gate)'
        Has $s 'RequireOwner(candidate); RequireBegun();'
        Has $s 'catch (Exception) { failed = true; throw; }'
        Has $s 'GetTickCount64(' 0
    },'RequireOwner(candidate); RequireBegun();','RequireOwner(candidate); GetTickCount64(); RequireBegun();'),
    @('deadline_snapshot_is_serialized_fresh_and_original','deadlinesnapshot',{
        param($s)
        Order $s @('lock (gate)','RequireGuardian(guardian, capability); AssertActiveCore();','return new ProducerDeadlineSnapshot(startedTick, deadlineTick);','catch (Exception) { failed = true; throw; }')
    },'RequireGuardian(guardian, capability); AssertActiveCore();','RequireGuardian(guardian, capability);'),
    @('deadline_guardian_requires_registered_original_capability','deadlineguardian',{
        param($s)
        Order $s @('RequireOwner(originalOwner);','if (!originalOwner.OwnsGuardian(guardian, capability))')
    },'!originalOwner.OwnsGuardian(guardian, capability)','false'),
    @('producer_constructs_own_deadline_without_starting_it','producerconstructor',{
        param($s)
        Has $s 'startupDeadline = new ProducerDeadline(this);'
        Has $s 'Begin(' 0
        Has $s 'GetTickCount64(' 0
    },'new ProducerDeadline(this)','new ProducerDeadline(null)'),
    @('producer_admission_checks_started_original_deadline','admission',{
        param($s)
        Order $s @('if (disposed || releaseStarted || containmentStarted)','lock (failureGate) { if (failed) throw new AggregateException(failures.ToArray()); }','preload.AssertProducer(this, inputs);','if (startAttempted) startupDeadline.AssertActive(this);')
    },'if (startAttempted) startupDeadline.AssertActive(this);','/* shared startup boundary removed */'),
    @('producer_binds_only_exact_registered_guardian_under_gate','binddeadline',{
        param($s)
        Order $s @('Enter();','RequireAdmission();','if (!OwnsGuardian(candidate, capability))','startupDeadline.AssertActive(this);','return startupDeadline;','catch (Exception error) { Record(error); throw; } finally { Leave(); }')
    },'!OwnsGuardian(candidate, capability)','false'),
    @('shared_factory_binds_original_context_before_registration_tail','sharedfactory',{
        param($s)
        Order $s @('startupDeadline.AssertPreparedOwner(originalOwner);','controlChannel.AssertPreparedOwner(originalOwner);','contextSection.AssertPreparedOwner(originalOwner);','CompilerCommand prepared = new CompilerCommand(executable, expectedHash, null, command.ToString(), directory, block, clock, inputs, startupDeadline, originalOwner, controlChannel, contextSection);','inputs.RegisterCommand(prepared); return prepared;')
        foreach($forbidden in @('GetTickCount64(','AssertActive(','StartSuspended(','File.','Process.')){Has $s $forbidden 0}
        $tail=Find $s 'inputs.RegisterCommand(prepared); return prepared;'
        $tailTokens=Tokens 'inputs.RegisterCommand(prepared); return prepared;'
        Assert-Producer ($tail.Count -eq 1 -and $tail[0]+$tailTokens.Count -eq $s.Count-1) 'Shared registration is not the final factory action.'
    },'inputs, startupDeadline, originalOwner, controlChannel, contextSection);','inputs, startupDeadline, null, controlChannel, contextSection);'),
    @('bootstrap_shared_context_and_owner_are_readonly','bootstrapclass',{
        param($s)
        Has $s 'private readonly ProducerDeadline sharedStartupDeadline;'
        Has $s 'private readonly DisposableProducerLaunch originalDeadlineOwner;'
        Has $s 'sharedStartupDeadline = startupDeadline; originalDeadlineOwner = deadlineOwner;'
    },'private readonly ProducerDeadline sharedStartupDeadline;','private ProducerDeadline sharedStartupDeadline;'),
    @('bootstrap_checks_original_local_and_shared_startup_bounds','bootstrapclock',{
        param($s)
        Order $s @('SuccessState();','if (originalDeadlineOwner != null) originalDeadlineOwner.AssertPreloadAdmission(this);','if (successClock.ElapsedMilliseconds >= SuccessMilliseconds)','if (sharedStartupDeadline != null) sharedStartupDeadline.AssertActive(originalDeadlineOwner);')
    },'if (sharedStartupDeadline != null) sharedStartupDeadline.AssertActive(originalDeadlineOwner);','/* lost shared startup check */'),
    @('expired_success_does_not_gate_original_cleanup_budget','bootstrapcleanup',{
        param($s)
        Has $s 'containmentClock == null || containmentClock.ElapsedMilliseconds >= CleanupMilliseconds'
        foreach($forbidden in @('sharedStartupDeadline','successClock','ClockAndFaultBoundary(','AssertActive(')){Has $s $forbidden 0}
    },'if (containmentClock == null','sharedStartupDeadline.AssertActive(originalDeadlineOwner); if (containmentClock == null'),
    @('guardian_binds_original_deadline_before_input_and_native_work','guardianstart',{
        param($s)
        Order $s @('sourceCapability = producer.RegisterGuardian(this, inputs); producerRegistered = true;','startupDeadline = producer.BindGuardianDeadline(this, sourceCapability);','startupSnapshot = startupDeadline.SnapshotForGuardian(this, sourceCapability);','inputs.RegisterGuardian(this); inputsRegistered = true;','heldExecutable = inputs.BorrowExactForGuardian(this, executable, expectedHash); HeldBoundary();','NoJob(GetCurrentProcess()); launchCopy = sourceCapability.PrepareLaunchCopy(this);')
    },'startupDeadline = producer.BindGuardianDeadline(this, sourceCapability);','/* original deadline binding removed */'),
    @('guardian_v2_argv_carries_both_original_numeric_ticks','guardianstart',{
        param($s)
        Has $s 'string rawStart = startupSnapshot.StartedTick.ToString(CultureInfo.InvariantCulture), rawDeadline = startupSnapshot.DeadlineTick.ToString(CultureInfo.InvariantCulture);'
        # The historical case name is retained; its numeric deadline intent now
        # covers the exact fourteen-argument v6 context transport ABI.
        Has $s 'arguments.Append(" --stratus-owner-watch-v8 ").Append(rawHandle).Append('' '').Append(rawPid).Append('' '').Append(rawBirth).Append('' '').Append(nonce).Append('' '').Append(rawStart).Append('' '').Append(rawDeadline).Append('' '').Append(controlRead).Append('' '').Append(controlWrite).Append('' '').Append(controlEndpoints.Nonce).Append('' '').Append(contextHandle).Append('' '').Append(contextLength).Append('' '').Append(contextDelivery.Sha256).Append('' '').Append(rawTransfer);'
    },'Append(rawStart).Append('' '').Append(rawDeadline)','Append(rawStart).Append('' '').Append(rawStart)'),
    @('guardian_expected_ready_echoes_both_original_tick_fields','guardianstart',{
        param($s)
        Has $s 'expectedReady = "STRATUS_OWNER_READY_V8 " + nonce + " " + rawPid + " " + rawBirth + " " + rawStart + " " + rawDeadline + " " + controlEndpoints.Nonce + " " + contextLength + " " + contextDelivery.Sha256 + " " + rawTransfer + "\n";'
        Has $s 'if (expectedReady.Length > 352)'
    },'rawStart + " " + rawDeadline + " " + controlEndpoints.Nonce','rawStart + " " + rawStart + " " + controlEndpoints.Nonce'),
    @('guardian_admission_requires_shared_deadline_after_registration','guardianadmission',{
        param($s)
        Has $s 'if (startupDeadline != null) startupDeadline.AssertGuardianActive(this, sourceCapability); else if (producerRegistered) throw new InvalidOperationException('
    },'startupDeadline.AssertGuardianActive(this, sourceCapability);','/* omitted fresh shared deadline check */'),
    @('guardian_ready_final_publication_rechecks_admission_and_ticks','guardianready',{
        param($s)
        Has $s 'Admission(); if (readyObservation == null) readyObservation = new GuardianReadyObservation(this, nonce, creationOutputs.pid, birth, launchCopy.ProducerId, launchCopy.ProducerBirth, startupSnapshot.StartedTick, startupSnapshot.DeadlineTick, controlEndpoints.Nonce, contextDelivery.Bytes, contextDelivery.Sha256);'
        Order $s @('if (captureFailed || failures.Count != 0 || stdout.Length != expected.Length || stderr.Length != 0 || stdoutEof || stderrEof)','Admission(); if (readyObservation == null)','return readyObservation;')
    },'Admission(); if (readyObservation == null)','if (readyObservation == null)'),
    @('guardian_checks_deadline_immediately_before_create_attempt','guardianstart',{
        param($s)
        Has $s 'Admission(); creationAttempted = true; bool created = CreateProcess('
        Order $s @('NoJob(GetCurrentProcess()); AssertOriginalStdioForBirth(); AssertAllowlist(allowed);','Admission(); creationAttempted = true;','bool created = CreateProcess(')
    },'Admission(); creationAttempted = true;','creationAttempted = true;')
)
Assert-Producer ($rows.Count -eq 70) 'The fixed 70 predicate/mutation pairs changed.'
# The previous 140 cases remain named and enforced. New cases concern real
# retained pipe/birth wiring only; no subject text or native member is evaluated.
$rows+=@(
    @('channel_private_owner_and_constructor_are_managed_only','channelclass',{
        param($s)
        Has $s 'private readonly DisposableProducerLaunch originalOwner;'
        Has $s 'public' 0
        $constructor=Method $s 'internal ProducerGuardianChannel('
        Has $constructor 'this.originalOwner = originalOwner;'
        foreach($forbidden in @('CreatePipe(','GetHandleInformation(','RandomNumberGenerator.','OpenForProducer(')){Has $constructor $forbidden 0}
    },'this.originalOwner = originalOwner;','CreatePipe(); this.originalOwner = originalOwner;'),
    @('channel_owner_has_exact_reference_backlink','channelowner',{
        param($s)
        Has $s 'candidate == null || !Object.ReferenceEquals(originalOwner, candidate) || !candidate.OwnsControlChannel(this)'
    },'!candidate.OwnsControlChannel(this)','false'),
    @('channel_createpipe_raw_outcomes_precede_every_use','channelcreate',{
        param($s)
        Order $s @('if (pair.Attempted)','pair.Attempted = true;','bool created = CreatePipe(out pair.Read.Raw, out pair.Write.Raw, ref security, 4096);','pair.Succeeded = created; pair.Returned = true;','if (!created)','RequirePair(pair); AssertInheritedPipe(pair.Read); AssertInheritedPipe(pair.Write);')
    },'pair.Succeeded = created; pair.Returned = true;','pair.Succeeded = created;'),
    @('failed_createpipe_even_zero_is_not_owned_or_absent','channelpair',{
        param($s)
        Has $s '!pair.Attempted || !pair.Returned || !pair.Succeeded || !Valid(pair.Read.Raw) || !Valid(pair.Write.Raw) || pair.Read.Raw == pair.Write.Raw'
    },'!pair.Returned || !pair.Succeeded','!pair.Returned'),
    @('channel_opens_exact_two_pairs_after_private_crypto_nonce','channelopen',{
        param($s)
        Order $s @('if (failed || disposed || openAttempted)','openAttempted = true;','byte[] entropy = new byte[32];','RandomNumberGenerator.Create()','nonce = BitConverter.ToString(entropy).Replace("-", "").ToLowerInvariant();','CreateOriginalPair(producerToGuardian); CreateOriginalPair(guardianToProducer);','RequireDistinct(); opened = true;')
        Has $s 'CreateOriginalPair(' 2
        Has $s 'catch (Exception) { failed = true; throw; }'
    },'CreateOriginalPair(guardianToProducer);','CreateOriginalPair(producerToGuardian);'),
    @('all_four_original_pipe_roles_must_be_distinct','channeldistinct',{
        param($s)
        Has $s 'producerToGuardian.Read.Raw == guardianToProducer.Read.Raw || producerToGuardian.Read.Raw == guardianToProducer.Write.Raw || producerToGuardian.Write.Raw == guardianToProducer.Read.Raw || producerToGuardian.Write.Raw == guardianToProducer.Write.Raw'
    },'producerToGuardian.Write.Raw == guardianToProducer.Write.Raw','false'),
    @('producer_endpoint_delivery_is_exact_direction_and_single_use','channelpborrow',{
        param($s)
        Order $s @('RequireOwner(candidate); RequireOpened();','if (producerBorrowed || producerRetirementAttempted)','AssertInheritedPipe(guardianToProducer.Read); AssertInheritedPipe(producerToGuardian.Write);','producerBorrowed = true;','return new ChannelEndpoints(guardianToProducer.Read.Raw, producerToGuardian.Write.Raw, nonce);')
    },'return new ChannelEndpoints(guardianToProducer.Read.Raw, producerToGuardian.Write.Raw, nonce);','return new ChannelEndpoints(producerToGuardian.Read.Raw, guardianToProducer.Write.Raw, nonce);'),
    @('guardian_endpoint_delivery_binds_original_and_opposite_direction','channelwborrow',{
        param($s)
        Order $s @('RequireGuardian(candidate, capability, originalGuardian != null); RequireOpened(); RequireProducerRetired();','if (guardianBorrowed || guardianRetirementAttempted || originalGuardian != null)','originalGuardian = candidate; originalGuardianCapability = capability;','AssertInheritedPipe(producerToGuardian.Read); AssertInheritedPipe(guardianToProducer.Write);','guardianBorrowed = true;','return new ChannelEndpoints(producerToGuardian.Read.Raw, guardianToProducer.Write.Raw, nonce);')
    },'if (guardianBorrowed || guardianRetirementAttempted || originalGuardian != null)','if (guardianRetirementAttempted)'),
    @('producer_pending_delivery_freshly_rechecks_original_handles','channelppending',{
        param($s)
        Order $s @('RequireOwner(candidate); RequireOpened();','if (!producerBorrowed || producerRetirementAttempted || producerRetired)','AssertInheritedPipe(guardianToProducer.Read); AssertInheritedPipe(producerToGuardian.Write);')
    },'AssertInheritedPipe(guardianToProducer.Read); AssertInheritedPipe(producerToGuardian.Write);','/* missing fresh P endpoint check */'),
    @('guardian_pending_delivery_freshly_rechecks_original_handles','channelwpending',{
        param($s)
        Order $s @('RequireGuardian(candidate, capability, true); RequireOpened(); RequireProducerRetired();','if (!guardianBorrowed || guardianRetirementAttempted || guardianRetired)','AssertInheritedPipe(producerToGuardian.Read); AssertInheritedPipe(guardianToProducer.Write);')
    },'AssertInheritedPipe(producerToGuardian.Read); AssertInheritedPipe(guardianToProducer.Write);','/* missing fresh W endpoint check */'),
    @('endpoint_inheritance_retirement_records_actual_outcome','channelinherit',{
        param($s)
        Order $s @('RequireUsable(endpoint);','if (endpoint.InheritAttempted)','endpoint.InheritAttempted = true;','bool changed = SetHandleInformation(endpoint.Raw, 1, 0);','endpoint.InheritSucceeded = changed; endpoint.InheritReturned = true;','if (!changed)','GetHandleInformation(endpoint.Raw, out flags)','if ((flags & 3) != 0)','endpoint.InheritVerified = true;')
    },'endpoint.InheritSucceeded = changed; endpoint.InheritReturned = true;','endpoint.InheritSucceeded = changed;'),
    @('endpoint_unknown_close_blocks_retry_and_known_success_never_requeries','channelclose',{
        param($s)
        Order $s @('if (endpoint.CloseAttempted && !endpoint.CloseReturned)','if (endpoint.CloseReturned && endpoint.CloseSucceeded) return;','RequireUsable(endpoint);','endpoint.CloseAttempted = true; endpoint.CloseReturned = false;','bool closed = CloseHandle(endpoint.Raw);','endpoint.CloseSucceeded = closed; endpoint.CloseReturned = true;')
        Has $s 'CloseHandle(' 1
    },'endpoint.CloseAttempted && !endpoint.CloseReturned','false'),
    @('producer_retirement_closes_only_its_two_local_endpoints','channelpretire',{
        param($s)
        Order $s @('if (!producerBorrowed || producerRetirementAttempted)','producerRetirementAttempted = true;','RetireInheritance(guardianToProducer.Read); CloseOriginal(guardianToProducer.Read);','RetireInheritance(producerToGuardian.Write); CloseOriginal(producerToGuardian.Write);','producerRetired = true;')
        Has $s 'CloseOriginal(' 2
    },'CloseOriginal(guardianToProducer.Read);','CloseOriginal(producerToGuardian.Read);'),
    @('guardian_retirement_closes_only_remaining_two_local_endpoints','channelwretire',{
        param($s)
        Order $s @('RequireGuardian(candidate, capability, true); RequireOpened(); RequireProducerRetired();','guardianRetirementAttempted = true;','RetireInheritance(producerToGuardian.Read); CloseOriginal(producerToGuardian.Read);','RetireInheritance(guardianToProducer.Write); CloseOriginal(guardianToProducer.Write);','guardianRetired = true;')
        Has $s 'CloseOriginal(' 2
    },'CloseOriginal(guardianToProducer.Write);','CloseOriginal(producerToGuardian.Write);'),
    @('closed_endpoint_proof_requires_recorded_success_without_native_query','channelclosed',{
        param($s)
        Has $s '!endpoint.CloseAttempted || !endpoint.CloseReturned || !endpoint.CloseSucceeded'
        foreach($forbidden in @('GetHandleInformation(','GetFileType(','CloseHandle(')){Has $s $forbidden 0}
    },'!endpoint.CloseReturned || !endpoint.CloseSucceeded','!endpoint.CloseReturned'),
    @('both_deliveries_proof_uses_only_original_closed_outcomes','channelboth',{
        param($s)
        Order $s @('RequireGuardian(candidate, capability, true); RequireOpened(); RequireProducerRetired();','if (!guardianRetired)','RequireClosed(producerToGuardian.Read); RequireClosed(guardianToProducer.Write);')
        foreach($forbidden in @('GetHandleInformation(','GetFileType(','AssertInheritedPipe(')){Has $s $forbidden 0}
    },'RequireProducerRetired();','/* missing P retirement */'),
    @('failed_pipe_creation_is_quarantined_during_cleanup','channelclosepair',{
        param($s)
        Order $s @('if (!pair.Attempted) return;','RequirePair(pair);','CloseOriginal(pair.Read); CloseOriginal(pair.Write);')
    },'RequirePair(pair);','/* failed rawoutputs cannot be trusted */'),
    @('channel_cleanup_requires_original_containment_not_success_admission','channeldispose',{
        param($s)
        Order $s @('RequireOwner(candidate); candidate.AssertChannelReleaseAllowed(this);','if (disposed) return;','CloseKnownPair(producerToGuardian); CloseKnownPair(guardianToProducer);','disposed = true;')
        foreach($forbidden in @('RequireOpened(','AssertGuardianAdmission(','GetTickCount64(')){Has $s $forbidden 0}
    },'candidate.AssertChannelReleaseAllowed(this);','/* missing original physical containment proof */'),
    @('producer_fixed_entry_marker_cannot_accept_arbitrary_workload','prepare',{
        param($s)
        Has $s 'if (arguments.Length != 1 || arguments[0] != "--stratus-producer-entry-v6")'
    },'arguments.Length != 1 || arguments[0] != "--stratus-producer-entry-v6"','arguments.Length != 1'),
    @('producer_disposes_channel_before_original_command_and_inputs','dispose',{
        param($s)
        $dispose=Block $s 'public void Dispose('
        Order $dispose @('if (!containmentProven && !normalClosureProven)','if (guardian != null)','releaseStarted = true;','controlChannel.DisposeAfterContainment(this);','contextSection.DisposeAfterContainment(this);','if (command != null) command.Dispose();','disposed = true;')
    },'controlChannel.DisposeAfterContainment(this);','/* lost channel-first containment cleanup */'),
    @('bootstrap_inherits_three_stdio_or_exact_five_producer_handles','bootstrapstart',{
        param($s)
        # Historical ID retained: same generic-versus-P separation, now with the
        # one explicit read-only context delivery in addition to five old handles.
        Has $s 'int inheritedCount = producerControlChannel == null ? 3 : 6;'
        Has $s 'Marshal.WriteIntPtr(inheritedHandles, IntPtr.Size * 3, producerControlEndpoints.Read);'
        Has $s 'Marshal.WriteIntPtr(inheritedHandles, IntPtr.Size * 4, producerControlEndpoints.Write);'
        Has $s 'Marshal.WriteIntPtr(inheritedHandles, IntPtr.Size * 5, producerContextDelivery.Handle);'
        Has $s 'new IntPtr(0x20002), inheritedHandles, new IntPtr(IntPtr.Size * inheritedCount)'
        Has $s 'new IntPtr(0x2000D), launchJobs, new IntPtr(IntPtr.Size)'
        Has $s '0x08080404, launchEnvironment, directory, ref startup, out creationOutputs'
    },'producerControlChannel == null ? 3 : 6','producerControlChannel == null ? 6 : 6'),
    @('bootstrap_native_argv_uses_original_control_endpoints_nonce_and_ticks','bootstrapclass',{
        param($s)
        $prepare=Method $s 'private void PrepareBirthJobOriginal()';$start=Method $s 'private void StartSuspendedOriginal(ProducerCompilerOwner owner)'
        Order $prepare @('producerControlEndpoints = producerControlChannel.BorrowProducerEndpoints(originalDeadlineOwner);','producerContextDelivery = producerContextSection.BorrowProducerDelivery(originalDeadlineOwner);','ProducerDeadlineSnapshot producerStartupSnapshot = sharedStartupDeadline.SnapshotForProducer(originalDeadlineOwner);','capturedCommandLine = commandLine + " --stratus-producer-control-v6 " + ControlHandleText(producerControlEndpoints.Read) + " " + ControlHandleText(producerControlEndpoints.Write) + " " + producerControlEndpoints.Nonce + " " + producerStartupSnapshot.StartedTick.ToString(CultureInfo.InvariantCulture) + " " + producerStartupSnapshot.DeadlineTick.ToString(CultureInfo.InvariantCulture) + " " + ControlHandleText(producerContextDelivery.Handle) + " " + producerContextDelivery.Bytes.ToString(CultureInfo.InvariantCulture) + " " + producerContextDelivery.Sha256;')
        Order $start @('startAttempted = true; SuccessBoundary(); RequirePreparedJob(); AssertEmptyJobBeforeBirth();','bool created = CreateProcess(executable, new StringBuilder(capturedCommandLine)')
    },'new StringBuilder(capturedCommandLine)','new StringBuilder(commandLine)'),
    @('bootstrap_retires_P_delivery_only_after_known_complete_birth','bootstrapstart',{
        param($s)
        Has $s 'producerControlChannel.AssertProducerDeliveryPending(originalDeadlineOwner); if (producerContextSection != null) producerContextSection.AssertProducerDeliveryPending(originalDeadlineOwner); SuccessBoundary(); RequirePreparedJob(); AssertEmptyJobBeforeBirth(); ProducerCompilerBirthBoundary(owner); ClockAndFaultBoundary(); creationAttempted = true;'
        Order $s @('creationSucceeded = created; creationReturned = true;','if (!created)','if (!ValidHandle(creationOutputs.process) || !ValidHandle(creationOutputs.thread) || creationOutputs.process == creationOutputs.thread || creationOutputs.pid <= 0 || creationOutputs.tid <= 0)','process = creationOutputs.process; thread = creationOutputs.thread; pid = creationOutputs.pid; tid = creationOutputs.tid;','if (producerControlChannel != null) producerControlChannel.RetireProducerDelivery(originalDeadlineOwner);')
    },'if (!created)','if (false)'),
    @('guardian_six_handle_birth_and_retirement_precede_started_latch','guardianstart',{
        param($s)
        Has $s 'BorrowOriginalChildEndForBirth(errorPair), ownerHandle, controlEndpoints.Read, controlEndpoints.Write'
        Has $s 'new IntPtr(0x20002), inherited.Value, new IntPtr(IntPtr.Size * 8)'
        Has $s 'Marshal.WriteIntPtr(inherited.Value, IntPtr.Size * 7, transferHandle);'
        Order $s @('controlChannel.AssertGuardianDeliveryPending(this, sourceCapability);','contextSection.AssertGuardianDeliveryPending(this, sourceCapability);','Admission(); creationAttempted = true;','creationSucceeded = created; creationReturned = true;','if (!created)','RequireOriginalCreation(); controlChannel.RetireGuardianDelivery(this, sourceCapability); contextSection.RetireGuardianDelivery(this, sourceCapability); RootBoundary(true);','controlChannel.AssertBothDeliveriesRetired(this, sourceCapability); contextSection.AssertBothDeliveriesRetired(this, sourceCapability); AssertEarlyStdioRetired(); started = true;')
    },'RequireOriginalCreation(); controlChannel.RetireGuardianDelivery(this, sourceCapability); contextSection.RetireGuardianDelivery(this, sourceCapability); RootBoundary(true);','controlChannel.RetireGuardianDelivery(this, sourceCapability); contextSection.RetireGuardianDelivery(this, sourceCapability); RootBoundary(true);'),
    @('guardian_ready_requires_local_retirement_and_original_control_nonce','guardianready',{
        param($s)
        Order $s @('controlChannel.AssertBothDeliveriesRetired(this, sourceCapability);','contextSection.AssertBothDeliveriesRetired(this, sourceCapability);','captured = stdout.ToArray();','Admission(); if (readyObservation == null)','startupSnapshot.StartedTick, startupSnapshot.DeadlineTick, controlEndpoints.Nonce, contextDelivery.Bytes, contextDelivery.Sha256);','return readyObservation;')
    },'controlChannel.AssertBothDeliveriesRetired(this, sourceCapability);','/* missing retired L endpoint proof */')
)
$expected=190
Assert-Producer ($rows.Count -eq 95) 'The fixed 95 predicate/mutation pairs changed.'
$executed=0;$passed=0;$caseResults=[Collections.Generic.List[object]]::new()
foreach($row in $rows){
    $name=$row[0];$selected=$blocks[$row[1]];$check=$row[2]
    $executed++; & $check $selected; $passed++
    $caseResults.Add([pscustomobject]@{name=$name;variant='original_source';passed=$true})
    if($row.Count -eq 6){$mutated=Move-Tokens $selected $row[3] $row[4]}else{$mutated=Replace-Tokens $selected $row[3] $row[4]}
    $executed++;$rejected=$false
    try{& $check $mutated}catch{
        Assert-Producer ($_.Exception.Message.StartsWith('Producer source contract:',[StringComparison]::Ordinal)) ('Unexpected negative-control exception: '+$name)
        $rejected=$true
    }
    Assert-Producer $rejected ('The predicate accepted its negative mutation: '+$name)
    $passed++;$caseResults.Add([pscustomobject]@{name=$name;variant='mutated_source_rejected';passed=$true})
}
Assert-Producer ($executed -eq $expected -and $passed -eq $expected) 'Fixed case count did not complete.'
$producerAfter=Read-ProducerSource $ProducerPath
$bootstrapAfter=Read-ProducerSource $BootstrapPath
$deadlineAfter=Read-ProducerSource $DeadlinePath
$guardianAfter=Read-ProducerSource $GuardianPath
$channelAfter=Read-ProducerSource $ControlChannelPath
$testAfter=Read-ProducerSource $PSCommandPath
Assert-Producer ($producerAfter.sha256 -ceq $producer.sha256 -and $bootstrapAfter.sha256 -ceq $bootstrap.sha256 -and $deadlineAfter.sha256 -ceq $deadline.sha256 -and $guardianAfter.sha256 -ceq $guardian.sha256 -and $channelAfter.sha256 -ceq $channel.sha256 -and $testAfter.sha256 -ceq $testSource.sha256) 'Source bytes changed during this source-only run.'
[pscustomobject]@{
    format='stratus-disposable-producer-source-contract-tests-v1'
    expected=$expected;executed=$executed;passed=$passed;failed=0
    powershell_version=$PSVersionTable.PSVersion.ToString();powershell_edition=$PSVersionTable.PSEdition
    producer_source_sha256=$producer.sha256;bootstrap_source_sha256=$bootstrap.sha256
    deadline_source_sha256=$deadline.sha256;guardian_source_sha256=$guardian.sha256
    control_channel_source_sha256=$channel.sha256
    test_sha256=$testSource.sha256;source_hashes_unchanged=$true
    target_compiled=$false;target_loaded=$false;target_lifecycle_executed=$false;native_qualified=$false
    independent_guardian_implemented=$false;resume_authorized=$false;full_build_accepted=$false
    shared_startup_deadline_qualified=$false;full_build_deadline_implemented=$false
    private_control_transport_qualified=$false;compiler_job_ack_implemented=$false
    cases=$caseResults.ToArray()
} | ConvertTo-Json -Depth 6 -Compress
