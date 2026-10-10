param([string] $GuardianPath,[string] $WirePath,[string] $TerminalPath)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if([string]::IsNullOrWhiteSpace($GuardianPath)){$GuardianPath=Join-Path $PSScriptRoot '..\Stratus.RuntimeBuild.GuardianProgram.cs'}
if([string]::IsNullOrWhiteSpace($WirePath)){$WirePath=Join-Path $PSScriptRoot '..\Stratus.RuntimeBuild.CompilerJobWire.cs'}
if([string]::IsNullOrWhiteSpace($TerminalPath)){$TerminalPath=Join-Path $PSScriptRoot '..\Stratus.RuntimeBuild.GuardianTerminal.cs'}
# Entry-only SOURCE checks. Fixed local predicates inspect C# tokens and their
# in-memory mutations. Subject C# is never compiled, evaluated, loaded or invoked.
# GetHandleInformation checks flags, NOT granted access rights or launcher origin.
# Historical case IDs remain fixed; v8 has fourteen arguments and captures
# context bytes before HELLO/READY, without asserting approval or schema validity.
# Legacy no-success/EOF IDs now bind only the actual normal terminal proof;
# STOP still forbids Join, and every original negative guarantee is retained.
# Root-handoff selector adaptation keeps all 162 cases/order and old HELLO/Job
# bounds: the one reader/writer now admits the third offer/fourth outbound frame.
# Existing STOP-order predicates also follow both retained-worker settlements;
# these are source links, not root-transfer/native or terminal qualification.
# These token checks
# do not prove their launcher provenance, native clock behavior, or the pending
# independently approved build-wide deadline/context. STOP retains its local 25s.
function Assert-Guardian {param([bool]$Okay,[string]$Message) if(-not $Okay){throw ('Guardian source contract: '+$Message)}}
function Read-GuardianSource {
    param([string]$Path)
    $full=[IO.Path]::GetFullPath($Path)
    $length=[IO.FileInfo]::new($full).Length
    Assert-Guardian ($length -gt 0 -and $length -le 1048576) 'Source length is outside the one-MiB inspection bound.'
    $raw=[IO.File]::ReadAllBytes($full)
    Assert-Guardian ($raw.LongLength -eq $length) 'Source length changed during read.'
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
    $wanted=Tokens $Sequence;Assert-Guardian ($wanted.Count -gt 0) 'Empty predicate.'
    $found=[Collections.Generic.List[int]]::new()
    for($i=0;$i -le $Source.Count-$wanted.Count;$i++){
        $same=$true
        for($j=0;$j -lt $wanted.Count;$j++){if($Source[$i+$j] -cne $wanted[$j]){$same=$false;break}}
        if($same){$found.Add($i)}
    }
    return ,$found.ToArray()
}
function Has {param([string[]]$Source,[string]$Sequence,[int]$Count=1) Assert-Guardian ((Find $Source $Sequence).Count -eq $Count) ('Sequence differs: '+$Sequence)}
function Order {
    param([string[]]$Source,[string[]]$Sequences)
    $last=-1
    foreach($sequence in $Sequences){$found=Find $Source $sequence;Assert-Guardian ($found.Count -eq 1 -and $found[0] -gt $last) ('Order differs: '+$sequence);$last=$found[0]}
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
    Assert-Guardian ($found.Count -eq 1) ('Missing/ambiguous scoped block: '+$Signature)
    $open=$found[0]
    while($open -lt $Source.Count -and $Source[$open] -cne '{'){$open++}
    Assert-Guardian ($open -lt $Source.Count) 'Opening brace missing.'
    $depth=0
    for($i=$open;$i -lt $Source.Count;$i++){
        if($Source[$i] -ceq '{'){$depth++}elseif($Source[$i] -ceq '}'){$depth--}
        if($depth -eq 0){return ,([string[]]$Source[$found[0]..$i])}
    }
    throw 'Guardian source contract: Unbalanced source block.'
}
function Method {param([string[]]$Class,[string]$Signature) return ,(Block $Class $Signature -Direct)}
function Replace-Tokens {
    param([string[]]$Source,[string]$Needle,[string]$Replacement)
    $at=Find $Source $Needle;Assert-Guardian ($at.Count -eq 1) ('Mutation anchor differs: '+$Needle)
    $old=Tokens $Needle;$new=Tokens $Replacement;$result=[Collections.Generic.List[string]]::new()
    for($i=0;$i -lt $at[0];$i++){$result.Add($Source[$i])}
    foreach($token in $new){$result.Add($token)}
    for($i=$at[0]+$old.Count;$i -lt $Source.Count;$i++){$result.Add($Source[$i])}
    return ,$result.ToArray()
}
function Move-Tokens {
    param([string[]]$Source,[string]$Needle,[string]$Before)
    $without=Replace-Tokens $Source $Needle '/* removed selected original statement */'
    return ,(Replace-Tokens $without $Before ($Needle+' '+$Before))
}
$subject=Read-GuardianSource $GuardianPath
$wireSource=Read-GuardianSource $WirePath
$terminalSource=Read-GuardianSource $TerminalPath
$testSource=Read-GuardianSource $PSCommandPath
$program=Block (Tokens $subject.text) 'internal static partial class Program'
$terminal=Block (Tokens $terminalSource.text) 'internal static partial class Program'
$main=Method $program 'private static int Main('
$stop=Method $program 'private static bool StopOriginalProducer('
$blocks=@{
    program=$program;main=$main;stop=$stop
    normal=[string[]]@($program+$terminal)
    writerflow=[string[]]@((Method $program 'private static void AdoptControl(')+$terminal)
    controlflow=[string[]]@($program+(Block (Tokens $wireSource.text) 'internal static class CompilerJobWire'))
    decimal=Method $program 'private static ulong Decimal('
    deadline=Method $program 'private static void AssertStartupDeadline('
    independent=Method $program 'private static void AssertIndependent('
    observe=Method $program 'private static bool ObserveProducer('
    writer=Method $program 'private static void StartReadyWriter('
    endpoint=Method $program 'private static IntPtr ControlEndpoint('
    controlfields=Method $program 'private static string ControlFields('
    control=Method $program 'private static void AdoptControl('
    controlworker=Method $program 'private static void AssertControlWorker('
    mainloop=Block $main 'while (true)'
    stoploop=$stop
    contextgate=Block $main 'if (!protocolStartAttempted && contextCapture.IsComplete)'
}
$rows=@(
    @('entry_is_private_and_has_no_public_authority','program',{
        param($s)
        Has $s 'internal static partial class Program'
        Has $s 'private static int Main(string[] args)'
        Has $s 'public' 0
    },'private static int Main(string[] args)','public static int Main(string[] args)'),
    @('fixed_original_startup_and_local_stop_bounds','program',{
        param($s)
        Has $s 'private const ulong StartupMilliseconds = 60000;'
        Has $s 'private const int StopMilliseconds = 25000;'
    },'StopMilliseconds = 25000','StopMilliseconds = 250000'),
    @('decimal_values_are_positive_bounded_and_canonical','decimal',{
        param($s)
        Has $s 'value != null && value.Length > 0 && value.Length <= 20'
        Has $s 'UInt64.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out number)'
        Has $s 'Require(number > 0 && number.ToString(CultureInfo.InvariantCulture) == value);'
    },'number > 0','number >= 0'),
    @('exact_ten_argument_v3_entry','main',{
        param($s)
        Has $s 'Require(args != null && args.Length == 14 && args[0] == "--stratus-owner-watch-v8");'
        Has $s 'ulong raw = Decimal(args[1]), pid = Decimal(args[2]), birth = Decimal(args[3]);'
    },'args.Length == 14','args.Length >= 14'),
    @('handle_pid_birth_integer_bounds','main',{
        param($s)
        Has $s 'Require((IntPtr.Size == 8 ? raw <= Int64.MaxValue : raw < UInt32.MaxValue) && pid <= Int32.MaxValue && birth <= Int64.MaxValue);'
    },'pid <= Int32.MaxValue','pid <= UInt64.MaxValue'),
    @('nonce_has_exact_lower_hex_shape','main',{
        param($s)
        Has $s 'Require(args[4] != null && args[4].Length == 64);'
        Has $s "foreach (char c in args[4]) Require((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'));"
    },'args[4].Length == 64','args[4].Length <= 64'),
    @('original_inherited_values_are_retained_without_pid_reopen','main',{
        param($s)
        Has $s 'producer = ControlEndpoint(args[1]);'
        Has $s 'producerPid = (uint)pid; producerBirth = (long)birth;'
        Has $s 'OpenProcess(' 0
    },'producerPid = (uint)pid;','producerPid = GetCurrentProcessId();'),
    @('inherited_flags_cleared_and_rechecked_not_access_rights_proof','main',{
        param($s)
        Order $s @('Require(GetHandleInformation(producer, out flags) && (flags & 1) == 1 && (flags & 2) == 0);','Require(SetHandleInformation(producer, 1, 0));','Require(GetHandleInformation(producer, out flags) && (flags & 3) == 0);')
    },'Require(SetHandleInformation(producer, 1, 0));','/* inherited flag not cleared */'),
    @('inherited_flag_admission_precedes_actual_identity','main',{
        param($s)
        Order $s @('Require(GetHandleInformation(producer, out flags) && (flags & 3) == 0);','bool dead = ObserveProducer(); identityEstablished = true;')
    },'bool dead = ObserveProducer(); identityEstablished = true;','Require(GetHandleInformation(producer, out flags) && (flags & 3) == 0);','move'),
    @('native_identity_rejects_self_and_wrong_pid','observe',{
        param($s)
        Has $s 'producer != IntPtr.Zero && producer != new IntPtr(-1) && producerPid > 0 && producerPid != GetCurrentProcessId() && GetProcessId(producer) == producerPid'
    },'producerPid != GetCurrentProcessId()','producerPid > 0'),
    @('native_times_are_sandwiched_by_original_handle_waits','observe',{
        param($s)
        Order $s @('uint before = WaitForSingleObject(producer, 0);','Require(before == WaitObject || before == WaitTimeout);','Require(GetProcessTimes(producer, out created, out exited, out kernel, out user));','Require(created == producerBirth && created > 0);','uint after = WaitForSingleObject(producer, 0);')
    },'created == producerBirth && created > 0','created > 0'),
    @('signaled_root_requires_native_physical_exit_time','observe',{
        param($s)
        Has $s 'if (before == WaitObject) Require(exited > 0 && exited >= created);'
        Has $s 'if (before == WaitObject) Require(after == WaitObject);'
    },'exited > 0 && exited >= created','exited >= 0'),
    @('death_between_queries_is_not_yet_death_proof','observe',{
        param($s)
        Has $s 'Require(after == WaitObject || after == WaitTimeout);'
        Has $s 'return before == WaitObject && after == WaitObject;'
    },'return before == WaitObject && after == WaitObject;','return before == WaitObject || after == WaitObject;'),
    @('independence_queries_any_job_for_current_guardian','independent',{
        param($s)
        Has $s 'Require(IsProcessInJob(GetCurrentProcess(), IntPtr.Zero, out member) && !member);'
    },'GetCurrentProcess(), IntPtr.Zero, out member','producer, IntPtr.Zero, out member'),
    @('identity_before_independence_and_ready_allows_only_exact_stop','main',{
        param($s)
        Order $s @('bool dead = ObserveProducer(); identityEstablished = true; AssertIndependent();','Require(!dead);','startedTick = Decimal(args[5]); deadlineTick = Decimal(args[6]); AssertStartupDeadline();','StartReadyWriter(args[4], args[9]);')
    },'StartReadyWriter(args[4], args[9]);','bool dead = ObserveProducer(); identityEstablished = true;','move'),
    @('single_original_startup_origin_is_imported_without_fresh_main_clock','main',{
        param($s)
        Order $s @('Require(args != null && args.Length == 14 && args[0] == "--stratus-owner-watch-v8");','startedTick = Decimal(args[5]); deadlineTick = Decimal(args[6]);','while (true)')
        Has $s 'startedTick =' 1
        Has $s 'deadlineTick =' 1
        Has $s 'Stopwatch.StartNew(' 0
        Has $s 'GetTickCount64(' 0
        Has $s '.Restart(' 0
        Has $s '.Reset(' 0
    },'while (true) {','while (true) { Stopwatch stage = Stopwatch.StartNew();'),
    @('ready_frame_binds_nonce_and_original_native_identity','writer',{
        param($s)
        Has $s 'Encoding.ASCII.GetBytes("STRATUS_OWNER_READY_V8 " + nonce + " " + producerPid.ToString(CultureInfo.InvariantCulture) + " " + producerBirth.ToString(CultureInfo.InvariantCulture) + " " + startedTick.ToString(CultureInfo.InvariantCulture) + " " + deadlineTick.ToString(CultureInfo.InvariantCulture) + " " + controlNonce + " " + contextCapture.Length.ToString(CultureInfo.InvariantCulture) + " " + contextCapture.Sha256 + " " + Stratus.RuntimeBuild.CompilerJobWire.HandleText(producerTransfer) + "\n")'
    },'producerBirth.ToString(CultureInfo.InvariantCulture)','"0"'),
    @('ready_frame_has_fixed_size_before_writer_creation','writer',{
        param($s)
        Order $s @('Require(frame.Length <= 352);','readyWriter = new Thread(delegate() {')
    },'frame.Length <= 352','frame.Length <= 1048576'),
    @('ready_writer_is_background_before_start','writer',{
        param($s)
        Order $s @('readyWriter = new Thread(delegate() {','readyWriter.IsBackground = true;','readyWriter.Start();')
        Has $s 'new Thread(' 1
    },'readyWriter.IsBackground = true;','readyWriter.IsBackground = false;'),
    @('ready_stream_io_stays_inside_writer_not_owner_main','writer',{
        param($s)
        $body=Block $s 'new Thread(delegate()'
        Order $body @('readyStream = Console.OpenStandardOutput();','readyStream.Write(frame, 0, frame.Length);','readyStream.Flush();')
        Has $s 'Console.OpenStandardOutput(' 1
        Has $s 'readyStream.Write(' 1
        Has $s 'readyStream.Flush(' 1
    },'readyStream.Flush();','/* no completed publication flush */'),
    @('writer_failure_is_recorded_for_owner_loop','writer',{
        param($s)
        Has $s 'catch (Exception) { Interlocked.Exchange(ref writerFailed, 1); }'
    },'Interlocked.Exchange(ref writerFailed, 1);','Interlocked.Exchange(ref writerFailed, 0);'),
    @('owner_loop_never_joins_or_writes_ready_stream','main',{
        param($s)
        foreach($forbidden in @('readyWriter.Join(','Console.OpenStandardOutput(','readyStream.Write(','readyStream.Flush(')){Has $s $forbidden 0}
        Has $s 'if (Interlocked.CompareExchange(ref writerFailed, 0, 0) != 0 || Interlocked.CompareExchange(ref controlFailed, 0, 0) != 0) break;'
    },'StartReadyWriter(args[4], args[9]);','StartReadyWriter(args[4], args[9]); readyWriter.Join();'),
    @('main_loop_rechecks_independence_identity_fault_and_bounded_wait','mainloop',{
        param($s)
        Order $s @('while (true) { AssertStartupDeadline(); AssertIndependent();','bool ownerExited = ObserveProducer(); AssertStartupDeadline(); if (ownerExited) {','if (!MayDrainNormalTerminal()) break;','RequireOriginalProducerNormalExitForArmedDrain();','if (TryCompleteNormalTerminal()) return 0;','Thread.Sleep(10); continue;','if (Interlocked.CompareExchange(ref writerFailed, 0, 0) != 0 || Interlocked.CompareExchange(ref controlFailed, 0, 0) != 0) break;','uint wait = WaitForSingleObject(producer, 50);','Require(wait != WaitFailed && (wait == WaitObject || wait == WaitTimeout)); AssertStartupDeadline();')
    },'WaitForSingleObject(producer, 50)','WaitForSingleObject(producer, UInt32.MaxValue)'),
    @('every_entry_terminal_is_nonzero_without_build_success','normal',{
        param($s)
        $m=Method $s 'private static int Main('; $complete=Method $s 'private static bool TryCompleteNormalTerminal('
        Has $m 'return' 3;Has $m 'return 0;' 1;Has $m 'return 202;' 0;Has $m 'return 203;' 0
        Has $m 'return StopOriginalProducer() ? 204 : 205;' 2
        Has $m 'if (ownerExited) { if (!MayDrainNormalTerminal()) break; RequireOriginalProducerNormalExitForArmedDrain(); if (TryCompleteNormalTerminal()) return 0; Thread.Sleep(10); continue; }'
        Order $complete @('AssertNormalTerminalDrainAdmission(); AssertStartupDeadline();','if (!NormalTerminalChannelSettled()) return false;','AssertNormalTerminalProofAdmission(jobTransfer, rootTransfer); if (Interlocked.CompareExchange(ref normalProofCompleted, 0, 0) == 0)','jobTransfer.ProveNormalClosureAfterProducerExit(rootTransfer);','rootTransfer.ProveNormalClosureAfterProducerExit(jobTransfer);','Interlocked.Exchange(ref terminalOutputPermit, 1);','!readyWriterStartReturned || !readyWriter.Join(0)) return false;','Require(terminalOutputWriteReturned && jobTransfer.NormalClosureProven && rootTransfer.NormalClosureProven);','AssertStartupDeadline(); return true;')
        $admission=Method $s 'private static void AssertNormalTerminalProofAdmission('
        Has $admission 'Object.ReferenceEquals(Thread.CurrentThread, originalMainThread) && identityEstablished'
        Has $admission 'Object.ReferenceEquals(jobTransfer, originalJobOwner) && Object.ReferenceEquals(rootTransfer, originalRootOwner)'
        Has $admission 'Interlocked.CompareExchange(ref stopAttempted, 0, 0) == 0'
        Has $admission 'Interlocked.CompareExchange(ref observation.CommitObserved, 0, 0) == 1 && observation.CommitFrame != null'
        Has $admission 'Interlocked.CompareExchange(ref observation.InputEofObserved, 0, 0) == 1'
        Has $admission 'Interlocked.CompareExchange(ref observation.HalfCloseReturned, 0, 0) == 1 && observation.HalfCloseSucceeded'
        Has $admission 'controlWriterStartAttempted && controlWriterStartReturned && controlReader.Join(0) && controlWriter.Join(0) && contextCapture.IsWorkerSettledForTerminal'
        Has $admission 'DemandQualifiedNormalTerminalObservation();'
        Has (Method $s 'private static void DemandQualifiedNormalTerminalObservation(') 'throw new InvalidOperationException("W normal terminal observation is not independently qualified.");'
        Has (Method $s 'private static bool StopOriginalProducer(') '.Join(' 0
    },'if (TryCompleteNormalTerminal()) return 0;','return 0;'),
    @('stop_requires_previously_established_original_identity','stop',{
        param($s)
        Order $s @('stopClock = Stopwatch.StartNew();','if (!identityEstablished) return false;','producerStopSucceeded = TerminateProcess(producer, 203);')
        Has $s 'identityEstablished = true' 0
    },'if (!identityEstablished) return false;','/* unknown owner admitted */'),
    @('stop_targets_only_exact_retained_P_not_launcher_or_lookup','stop',{
        param($s)
        Has $s 'TerminateProcess(producer, 203)' 1
        Has $s 'TerminateProcess(' 1
        foreach($forbidden in @('GetCurrentProcess(','GetCurrentProcessId(','OpenProcess(','Process.','TerminateJobObject(')){Has $s $forbidden 0}
    },'TerminateProcess(producer, 203)','TerminateProcess(GetCurrentProcess(), 203)'),
    @('single_local_stop_clock_has_no_reset_or_renewal','stop',{
        param($s)
        Has $s 'if (Interlocked.CompareExchange(ref stopAttempted, 1, 0) != 0) return false; stopClock = Stopwatch.StartNew();' 1
        Has $s 'Stopwatch.StartNew(' 1
        Has $s '.Restart(' 0
        Has $s '.Reset(' 0
    },'while (!dead && stopClock.ElapsedMilliseconds < StopMilliseconds) {','while (!dead && stopClock.ElapsedMilliseconds < StopMilliseconds) { stopClock.Restart();'),
    @('initial_native_death_observation_rechecks_stop_deadline','stop',{
        param($s)
        Has $s 'bool dead = ObserveProducer(); if (!dead) {'
        Order $s @('stopClock = Stopwatch.StartNew();','bool dead = ObserveProducer();','producerStopSucceeded = TerminateProcess(producer, 203);','if (!dead || stopClock.ElapsedMilliseconds >= StopMilliseconds) return false;','if (jobTransfer != null && !jobTransfer.SettleAfterProducerDeath(stopClock)) return false;','if (rootTransfer != null && !rootTransfer.SettleAfterProducerDeath(stopClock)) return false;','if (jobTransfer != null && !jobTransfer.StopAfterProducerDeath(stopClock)) return false;','if (rootTransfer != null && !rootTransfer.ObservePhysicalDeathAfterJobStop(stopClock)) return false;','return stopClock.ElapsedMilliseconds < StopMilliseconds;')
        Has $s 'return true;' 0
    },'bool dead = ObserveProducer();','bool dead = ObserveProducer(); if (dead) return true;'),
    @('terminate_return_is_not_a_physical_death_proof','stop',{
        param($s)
        Has $s 'if (!producerStopSucceeded && !ObserveProducer()) return false;'
        Has $s 'return producerStopSucceeded;' 0
        Has $s 'return true;' 0
    },'if (!producerStopSucceeded && !ObserveProducer()) return false;','if (!producerStopSucceeded) return false;'),
    @('stop_poll_proves_death_with_post_observation_deadline','stoploop',{
        param($s)
        Order $s @('while (!dead && stopClock.ElapsedMilliseconds < StopMilliseconds)','dead = ObserveProducer(); if (dead) break;','uint wait = WaitForSingleObject(producer, 50);','Require(wait == WaitObject || wait == WaitTimeout);','if (!dead || stopClock.ElapsedMilliseconds >= StopMilliseconds) return false;','return stopClock.ElapsedMilliseconds < StopMilliseconds;')
    },'return stopClock.ElapsedMilliseconds < StopMilliseconds;','return true;'),
    @('stop_fault_or_timeout_never_reports_success','stop',{
        param($s)
        Has $s 'catch (Exception) { return false; } }'
    },'catch (Exception) { return false; }','catch (Exception) { return true; }'),
    @('entry_has_no_resume_adoption_job_stop_or_manual_handle_close','program',{
        param($s)
        foreach($forbidden in @('ResumeThread(','CreateProcess(','OpenProcess(','GetProcessById(','TerminateJobObject(','CloseHandle(','Environment.Exit(','Process.Start(')){Has $s $forbidden 0}
    },'StartReadyWriter(args[4], args[9]);','ResumeThread(producer); StartReadyWriter(args[4], args[9]);'),
    @('startup_clock_is_native_unsigned_windows_boot_tick','program',{
        param($s)
        Has $s '[DllImport("kernel32.dll")] private static extern ulong GetTickCount64();'
    },'private static extern ulong GetTickCount64();','private static extern uint GetTickCount64();'),
    @('deadline_observation_uses_actual_native_tick_not_local_replacement','deadline',{
        param($s)
        Has $s 'ulong now = GetTickCount64();'
        Has $s 'GetTickCount64(' 1
        Has $s 'Stopwatch.StartNew(' 0
    },'ulong now = GetTickCount64();','ulong now = startedTick;'),
    @('original_start_tick_is_strictly_positive','deadline',{
        param($s)
        Has $s 'Require(startedTick > 0 &&'
    },'startedTick > 0','startedTick >= 0'),
    @('original_tick_range_checks_addition_overflow_before_expiry','deadline',{
        param($s)
        Has $s 'startedTick <= UInt64.MaxValue - StartupMilliseconds && deadlineTick == checked(startedTick + StartupMilliseconds)'
    },'startedTick <= UInt64.MaxValue - StartupMilliseconds','startedTick <= UInt64.MaxValue'),
    @('expiry_is_exact_checked_original_sixty_second_interval','deadline',{
        param($s)
        Has $s 'deadlineTick == checked(startedTick + StartupMilliseconds)'
    },'deadlineTick == checked(startedTick + StartupMilliseconds)','deadlineTick >= checked(startedTick + StartupMilliseconds)'),
    @('deadline_guard_rejects_an_origin_ahead_of_actual_clock','deadline',{
        param($s)
        Has $s 'Require(now >= startedTick && now >= previousTick && now < deadlineTick);'
    },'now >= startedTick','now >= 0'),
    @('deadline_guard_rejects_backwards_native_observation','deadline',{
        param($s)
        Has $s 'now >= previousTick'
    },'now >= previousTick','true'),
    @('expiry_boundary_is_strict_without_extra_millisecond','deadline',{
        param($s)
        Has $s 'now < deadlineTick'
    },'now < deadlineTick','now <= deadlineTick'),
    @('previous_tick_advances_only_after_all_deadline_checks','deadline',{
        param($s)
        Order $s @('ulong now = GetTickCount64();','Require(startedTick > 0 && startedTick <= UInt64.MaxValue - StartupMilliseconds && deadlineTick == checked(startedTick + StartupMilliseconds));','Require(now >= startedTick && now >= previousTick && now < deadlineTick);','previousTick = now;')
    },'previousTick = now;','Require(now >= startedTick && now >= previousTick && now < deadlineTick);','move'),
    @('clock_argument_rejection_retains_previously_proven_original_P','main',{
        param($s)
        Order $s @('bool dead = ObserveProducer(); identityEstablished = true;','startedTick = Decimal(args[5]); deadlineTick = Decimal(args[6]);')
    },'startedTick = Decimal(args[5]); deadlineTick = Decimal(args[6]);','bool dead = ObserveProducer(); identityEstablished = true;','move'),
    @('ready_echoes_original_start_tick_without_recapture','writer',{
        param($s)
        Has $s 'startedTick.ToString(CultureInfo.InvariantCulture) + " " + deadlineTick.ToString(CultureInfo.InvariantCulture)'
        Has $s 'GetTickCount64(' 0
    },'startedTick.ToString(CultureInfo.InvariantCulture)','GetTickCount64().ToString(CultureInfo.InvariantCulture)'),
    @('ready_echoes_original_expiry_not_a_renewed_interval','writer',{
        param($s)
        Has $s 'deadlineTick.ToString(CultureInfo.InvariantCulture) + " " + controlNonce + " " + contextCapture.Length.ToString(CultureInfo.InvariantCulture)'
    },'deadlineTick.ToString(CultureInfo.InvariantCulture)','checked(startedTick + StartupMilliseconds + 1).ToString(CultureInfo.InvariantCulture)'),
    @('startup_boundary_precedes_ready_publication_with_same_call_count','main',{
        param($s)
        Has $s 'AssertStartupDeadline(); contextCapture.AssertHealthy(); StartReadyWriter(args[4], args[9]); AssertStartupDeadline();'
    },'AssertStartupDeadline(); contextCapture.AssertHealthy(); StartReadyWriter(args[4], args[9]);','StartReadyWriter(args[4], args[9]); AssertStartupDeadline(); contextCapture.AssertHealthy();'),
    @('main_loop_checks_deadline_before_native_owner_observation','mainloop',{
        param($s)
        Has $s 'while (true) { AssertStartupDeadline(); AssertIndependent(); bool ownerExited = ObserveProducer();'
    },'while (true) { AssertStartupDeadline();','while (true) {'),
    @('main_loop_checks_deadline_after_native_owner_observation','mainloop',{
        param($s)
        Has $s 'bool ownerExited = ObserveProducer(); AssertStartupDeadline(); if (ownerExited) {'
    },'bool ownerExited = ObserveProducer(); AssertStartupDeadline();','bool ownerExited = ObserveProducer();'),
    @('main_loop_checks_deadline_after_native_bounded_wait','mainloop',{
        param($s)
        Has $s 'uint wait = WaitForSingleObject(producer, 50); Require(wait != WaitFailed && (wait == WaitObject || wait == WaitTimeout)); AssertStartupDeadline();'
    },'Require(wait != WaitFailed && (wait == WaitObject || wait == WaitTimeout)); AssertStartupDeadline();','Require(wait != WaitFailed && (wait == WaitObject || wait == WaitTimeout));'),
    @('deadline_exception_irreversibly_routes_entry_to_exact_stop','main',{
        param($s)
        Has $s 'catch (Exception) { return StopOriginalProducer() ? 204 : 205; }'
    },'catch (Exception) { return StopOriginalProducer() ? 204 : 205; }','catch (Exception) { return 205; }'),
    @('main_loop_never_replaces_original_origin_or_expiry','mainloop',{
        param($s)
        foreach($forbidden in @('startedTick =','deadlineTick =','previousTick =','Stopwatch.StartNew(','GetTickCount64(')){Has $s $forbidden 0}
    },'while (true) {','while (true) { startedTick = GetTickCount64(); deadlineTick = checked(startedTick + StartupMilliseconds);'),
    @('control_endpoint_uses_canonical_unsigned_launch_handle_range','endpoint',{
        param($s)
        Has $s 'ulong raw = Decimal(value);'
        Has $s 'Require(IntPtr.Size == 8 ? raw <= Int64.MaxValue : raw < UInt32.MaxValue);'
        Has $s 'IntPtr valueHandle = IntPtr.Size == 8 ? new IntPtr((long)raw) : new IntPtr(unchecked((int)(uint)raw)); Require(Stratus.RuntimeBuild.CompilerJobWire.HandleText(valueHandle) == value); return valueHandle;'
    },'raw < UInt32.MaxValue','raw <= UInt32.MaxValue'),
    @('control_nonce_has_exact_private_channel_shape','control',{
        param($s)
        Has $s 'Require(nonce != null && nonce.Length == 64);'
        Has $s "foreach (char c in nonce) Require((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'));"
    },'nonce.Length == 64','nonce.Length <= 64'),
    @('control_handles_are_distinct_from_each_other_and_original_P','control',{
        param($s)
        Has $s 'controlReadHandle = ControlEndpoint(read); controlWriteHandle = ControlEndpoint(write);'
        Has $s 'Require(controlReadHandle != controlWriteHandle && controlReadHandle != producer && controlWriteHandle != producer && controlReadHandle != producerTransfer && controlWriteHandle != producerTransfer);'
    },'controlWriteHandle != producer','controlWriteHandle != IntPtr.Zero'),
    @('both_control_handles_are_pipes_and_not_stdio_aliases','control',{
        param($s)
        Has $s 'foreach (IntPtr endpoint in new IntPtr[] { controlReadHandle, controlWriteHandle }) { Require(endpoint != GetStdHandle(-10) && endpoint != GetStdHandle(-11) && endpoint != GetStdHandle(-12)); Require(GetFileType(endpoint) == 3);'
        Order $s @('Require(GetFileType(endpoint) == 3);','Require(SetHandleInformation(endpoint, 1, 0));')
    },'GetFileType(endpoint) == 3','GetFileType(endpoint) != 0'),
    @('control_inheritance_is_cleared_and_rechecked_before_streams','control',{
        param($s)
        Order $s @('Require(GetHandleInformation(endpoint, out flags) && (flags & 1) == 1 && (flags & 2) == 0);','Require(SetHandleInformation(endpoint, 1, 0));','Require(GetHandleInformation(endpoint, out flags) && (flags & 3) == 0);','controlReadSafe = new SafePipeHandle(controlReadHandle, false);')
    },'Require(SetHandleInformation(endpoint, 1, 0));','/* clear omitted */'),
    @('retained_control_wrappers_are_nonowning_and_directional','control',{
        param($s)
        Has $s 'controlReadSafe = new SafePipeHandle(controlReadHandle, false);'
        Has $s 'controlWriteSafe = new SafePipeHandle(controlWriteHandle, false);'
        Has $s 'controlInput = new AnonymousPipeClientStream(PipeDirection.In, controlReadSafe);'
        Has $s 'controlOutput = new AnonymousPipeClientStream(PipeDirection.Out, controlWriteSafe);'
        Has $s '.Dispose(' 0
    },'new SafePipeHandle(controlReadHandle, false)','new SafePipeHandle(controlReadHandle, true)'),
    @('private_hello_roles_are_fixed_opposite_peer_prefixes','control',{
        param($s)
        Has $s 'byte[] expected = Encoding.ASCII.GetBytes("STRATUS_PRODUCER_HELLO_V6 " + ControlFields(nonce));'
        Has $s 'byte[] hello = Encoding.ASCII.GetBytes("STRATUS_GUARDIAN_HELLO_V6 " + ControlFields(nonce));'
    },'"STRATUS_PRODUCER_HELLO_V6 "','"STRATUS_GUARDIAN_HELLO_V6 "'),
    @('private_hello_fields_bind_original_P_and_shared_ticks','controlfields',{
        param($s)
        Has $s 'return nonce + " " + producerPid.ToString(CultureInfo.InvariantCulture) + " " + producerBirth.ToString(CultureInfo.InvariantCulture) + " " + startedTick.ToString(CultureInfo.InvariantCulture) + " " + deadlineTick.ToString(CultureInfo.InvariantCulture) + " " + contextCapture.Length.ToString(CultureInfo.InvariantCulture) + " " + contextCapture.Sha256 + "\n";'
        Has $s 'GetTickCount64(' 0
    },'deadlineTick.ToString(CultureInfo.InvariantCulture)','"0"'),
    @('control_frames_and_read_buffer_have_fixed_small_bounds','control',{
        param($s)
        Order $s @('Require(expected.Length <= 256 && hello.Length <= 256 && acknowledgement.Length <= 256);','controlReader = new Thread(delegate() {','int storageMaximum = Math.Max(Stratus.RuntimeBuild.TerminalWire.MaximumPreparedBytes, Math.Max(Stratus.RuntimeBuild.TerminalWire.MaximumCommitBytes, Math.Max(256, Math.Max(Stratus.RuntimeBuild.CompilerJobWire.MaximumBytes, Stratus.RuntimeBuild.CompilerRootWire.MaximumBytes))));','byte[] buffer = new byte[storageMaximum];','byte[] frame = new byte[storageMaximum];')
        Has $s 'int maximum = stage == 0 ? 256 : (stage == 1 ? Stratus.RuntimeBuild.CompilerJobWire.MaximumBytes : (stage == 2 ? Stratus.RuntimeBuild.CompilerRootWire.MaximumBytes : (stage == 3 ? Stratus.RuntimeBuild.TerminalWire.MaximumPreparedBytes : Stratus.RuntimeBuild.TerminalWire.MaximumCommitBytes)));'
    },'Require(expected.Length <= 256 && hello.Length <= 256 && acknowledgement.Length <= 256);','Require(expected.Length <= 1048576 && hello.Length <= 1048576 && acknowledgement.Length <= 1048576);'),
    @('both_control_workers_are_background_and_main_has_no_pipe_IO','program',{
        param($s)
        $adopt=Method $s 'private static void AdoptControl('
        Order $adopt @('controlReader = new Thread(delegate() {','controlWriter = new Thread(delegate() {','terminalObservation = new GuardianTerminalObservation(binding);','controlReader.IsBackground = true; controlWriter.IsBackground = true;','controlReaderStartAttempted = true; controlReader.Start(); controlReaderStartReturned = true;','controlWriterStartAttempted = true; controlWriter.Start(); controlWriterStartReturned = true;')
        Has $adopt 'new Thread(' 2
        $m=Method $s 'private static int Main('
        foreach($forbidden in @('controlInput.Read(','controlOutput.Write(','controlOutput.Flush(','controlReader.Join(','controlWriter.Join(')){Has $m $forbidden 0}
    },'controlReader.IsBackground = true;','controlReader.IsBackground = false;'),
    @('control_reader_checks_exact_bytes_and_all_observed_surplus','controlflow',{
        param($s)
        $reader=Block (Method (Block $s 'internal static partial class Program') 'private static void AdoptControl(') 'controlReader = new Thread(delegate()'
        Has $reader 'count = controlInput.Read(buffer, 0, buffer.Length); int admissionAttemptAtRead = Interlocked.CompareExchange(ref controlAckWriteAttempted, 0, 0); int jobAckAttemptAtRead = Interlocked.CompareExchange(ref jobAckWriteAttempted, 0, 0); int rootAckAttemptAtRead = Interlocked.CompareExchange(ref rootAckWriteAttempted, 0, 0); int terminalAckAttemptAtRead = Interlocked.CompareExchange(ref terminalObservation.AckWriteAttempted, 0, 0); int terminalCloseAttemptAtRead = Interlocked.CompareExchange(ref terminalObservation.HalfCloseAttempted, 0, 0); int preparedObservedAtRead = Interlocked.CompareExchange(ref terminalObservation.PreparedObserved, 0, 0); Stratus.RuntimeBuild.TerminalRecord preparedAtRead = preparedObservedAtRead == 1 ? terminalObservation.Record : null; if (stage < 3) AssertControlWorker(ref workerTick); else AssertTerminalControlWorker(ref workerTick);'
        Has $reader 'Require(stage < 5 && offset < maximum && (buffer[i] == 10 || (buffer[i] >= 32 && buffer[i] <= 126)));'
        Has $reader 'if (stage == 1) Require(admissionAttemptAtRead == 1); if (stage == 2) Require(jobAckAttemptAtRead == 1); if (stage == 3) Require(rootAckAttemptAtRead == 1); if (stage == 4) Require(terminalAckAttemptAtRead == 1 && terminalCloseAttemptAtRead == 1 && preparedAtRead != null && Object.ReferenceEquals(preparedAtRead, terminalObservation.Record)); frame[offset++] = buffer[i];'
        Has $reader 'if (stage == 0) { Stratus.RuntimeBuild.CompilerJobWire.RequireExact(actual, expected); AssertControlWorker(ref workerTick); Interlocked.Exchange(ref controlHelloObserved, 1);'
        Has $reader 'else if (stage == 1) { Require(Interlocked.CompareExchange(ref controlAckPermit, 0, 0) == 1 && Interlocked.CompareExchange(ref controlHelloWritten, 0, 0) == 1); AssertControlWorker(ref workerTick); jobTransfer.ClaimOffer(actual); } else if (stage == 2) { Require(jobAckAttemptAtRead == 1); AssertControlWorker(ref workerTick); rootTransfer.ClaimOffer(actual); } else if (stage == 3) { ReceiveNormalTerminalPrepared(actual, rootAckAttemptAtRead, ref workerTick); } else { ReceiveNormalTerminalCommit(actual, preparedAtRead, terminalAckAttemptAtRead, terminalCloseAttemptAtRead, ref workerTick); } ++stage; offset = 0;'
        Has (Method (Block $s 'internal static class CompilerJobWire') 'internal static void RequireExact(') 'Require(actual != null && expected != null && actual.Length == expected.Length && actual.Length <= MaximumBytes); for (int i = 0; i < actual.Length; ++i) Require(actual[i] == expected[i]);'
    },'offset < maximum','offset <= maximum'),
    @('control_reader_EOF_and_exception_both_close_admission','control',{
        param($s)
        $reader=Block $s 'controlReader = new Thread(delegate()'
        Has $reader 'if (count == 0) { Require(stage == 5 && offset == 0 && terminalAckAttemptAtRead == 1 && terminalCloseAttemptAtRead == 1 && preparedAtRead != null && Object.ReferenceEquals(preparedAtRead, terminalObservation.Record) && Interlocked.CompareExchange(ref terminalObservation.CommitObserved, 0, 0) == 1); Interlocked.Exchange(ref terminalObservation.InputEofObserved, 1); return; }'
        Has $reader 'Interlocked.Exchange(ref controlFailed, 1);' 1
        Has $reader 'catch (Exception) { Interlocked.Exchange(ref controlFailed, 1); }'
        Has $reader 'finally { Interlocked.Exchange(ref terminalObservation.ReaderSettled, 1); }'
    },'catch (Exception) { Interlocked.Exchange(ref controlFailed, 1); } finally { Interlocked.Exchange(ref terminalObservation.ReaderSettled, 1); }','catch (Exception) { Interlocked.Exchange(ref controlFailed, 0); } finally { Interlocked.Exchange(ref terminalObservation.ReaderSettled, 1); }'),
    @('private_writer_IO_and_fault_record_stay_in_background_worker','writerflow',{
        param($s)
        $writer=Block $s 'controlWriter = new Thread(delegate()'
        Has $writer 'AssertControlWorker(ref workerTick); controlOutput.Write(hello, 0, hello.Length); controlOutput.Flush(); AssertControlWorker(ref workerTick);'
        Has $writer 'catch (Exception) { Interlocked.Exchange(ref controlFailed, 1); }'
        Has $s 'controlOutput.Write(' 5;Has $s 'controlOutput.Flush(' 5
        Has $writer 'controlOutput.Write(' 4;Has $writer 'controlOutput.Flush(' 4
        Order $writer @('controlOutput.Write(hello, 0, hello.Length); controlOutput.Flush();','controlOutput.Write(acknowledgement, 0, acknowledgement.Length); controlOutput.Flush();','controlOutput.Write(jobAcknowledgement, 0, jobAcknowledgement.Length); controlOutput.Flush();','controlOutput.Write(rootAcknowledgement, 0, rootAcknowledgement.Length); controlOutput.Flush();','SendNormalTerminalAcknowledgement(ref workerTick);','finally { Interlocked.Exchange(ref terminalObservation.WriterSettled, 1); }')
        $ack=Method $s 'private static void SendNormalTerminalAcknowledgement('
        Order $ack @('Require(Object.ReferenceEquals(Thread.CurrentThread, controlWriter));','DemandQualifiedNormalTerminalObservation();','Require(Interlocked.CompareExchange(ref terminalObservation.AckWriteAttempted, 1, 0) == 0);','controlOutput.Write(acknowledgement, 0, acknowledgement.Length); controlOutput.Flush();','terminalObservation.AckWriteReturned = true;','Interlocked.Exchange(ref terminalObservation.AckWritten, 1);','Require(Interlocked.CompareExchange(ref terminalObservation.HalfCloseAttempted, 1, 0) == 0);','terminalObservation.HalfCloseSucceeded = CloseHandle(terminalObservation.WriteHandle);','Interlocked.Exchange(ref terminalObservation.HalfCloseReturned, 1);','Require(terminalObservation.HalfCloseSucceeded);','terminalObservation.WriteSafe.SetHandleAsInvalid();')
    },'controlOutput.Write(hello, 0, hello.Length); controlOutput.Flush();','controlOutput.Write(hello, 0, hello.Length); /* flush omitted */'),
    @('observed_control_hello_never_grants_main_or_Job_authority','main',{
        param($s)
        Has $s 'if (!controlAckAttempted && Interlocked.CompareExchange(ref controlHelloObserved, 0, 0) == 1 && Interlocked.CompareExchange(ref controlHelloWritten, 0, 0) == 1)'
        foreach($forbidden in @('ResumeThread(','AssignProcessToJobObject(','TerminateJobObject(')){Has $s $forbidden 0}
        Has $s 'return 0;' 1;Has $s 'if (TryCompleteNormalTerminal()) return 0;'
        $permit=Block $s 'if (!controlAckAttempted && Interlocked.CompareExchange(ref controlHelloObserved, 0, 0) == 1 && Interlocked.CompareExchange(ref controlHelloWritten, 0, 0) == 1)'
        Has $permit 'return' 0
        Has $s 'Interlocked.CompareExchange(ref controlFailed, 0, 0) != 0'
    },'StartReadyWriter(args[4], args[9]);','StartReadyWriter(args[4], args[9]); if (controlHelloObserved == 1) return 0;')
)
Assert-Guardian ($rows.Count -eq 64) 'The fixed 64 predicate/mutation pairs changed (50 retained semantic pairs plus 14 v3 control pairs).'
$rows+=@(
    @('context_arguments_are_parsed_only_after_exact_P_identity','main',{
        param($s)
        Order $s @('bool dead = ObserveProducer(); identityEstablished = true;', 'startedTick = Decimal(args[5]); deadlineTick = Decimal(args[6]);', 'contextCapture = Stratus.RuntimeBuild.ContextCapture.Prepare(args[10], args[11], args[12],')
    },'contextCapture = Stratus.RuntimeBuild.ContextCapture.Prepare(args[10], args[11], args[12], new IntPtr[] { producer, ControlEndpoint(args[7]), ControlEndpoint(args[8]), producerTransfer });','bool dead = ObserveProducer(); identityEstablished = true;','move'),
    @('context_worker_is_retained_before_start_and_main_watch','main',{
        param($s)
        Order $s @('contextCapture = Stratus.RuntimeBuild.ContextCapture.Prepare(', 'contextCapture.Start();', 'while (true) { AssertStartupDeadline(); AssertIndependent(); bool ownerExited = ObserveProducer();')
    },'contextCapture = Stratus.RuntimeBuild.ContextCapture.Prepare','Stratus.RuntimeBuild.ContextCapture unretained = Stratus.RuntimeBuild.ContextCapture.Prepare'),
    @('context_mapping_excludes_P_and_both_control_aliases','main',{
        param($s)
        Has $s 'new IntPtr[] { producer, ControlEndpoint(args[7]), ControlEndpoint(args[8]), producerTransfer }'
    },'new IntPtr[] { producer, ControlEndpoint(args[7]), ControlEndpoint(args[8]), producerTransfer }','new IntPtr[] { producer, ControlEndpoint(args[7]), ControlEndpoint(args[7]), producerTransfer }'),
    @('pending_capture_never_moves_mapping_or_hashing_to_main','main',{
        param($s)
        foreach($forbidden in @('MapViewOfFile(','UnmapViewOfFile(','Marshal.Copy(','ComputeHash(','CopyBytes(','.Join(')){Has $s $forbidden 0}
        Has $s 'Thread.Sleep(' 1;Has (Block $s 'if (ownerExited)') 'Thread.Sleep(10); continue;'
        Has $s 'uint wait = WaitForSingleObject(producer, 50);'
    },'contextCapture.Start();','contextCapture.Start(); contextCapture.CopyBytes();'),
    @('capture_completion_gates_single_protocol_start','contextgate',{
        param($s)
        Has $s 'if (!protocolStartAttempted && contextCapture.IsComplete)'
        Order $s @('if (!protocolStartAttempted && contextCapture.IsComplete) { AssertStartupDeadline(); AssertIndependent(); if (ObserveProducer()) break;', 'AssertStartupDeadline(); contextCapture.AssertHealthy(); protocolStartAttempted = true;', 'AdoptControl(args[7], args[8], args[9]);')
    },'contextCapture.IsComplete','true'),
    @('same_count_HELLO_cannot_move_before_fresh_capture_boundary','contextgate',{
        param($s)
        Order $s @('protocolStartAttempted = true;', 'AdoptControl(args[7], args[8], args[9]);', 'StartReadyWriter(args[4], args[9]);')
    },'AdoptControl(args[7], args[8], args[9]);','protocolStartAttempted = true;','move'),
    @('READY_rechecks_owner_independence_and_deadline_after_HELLO_start','contextgate',{
        param($s)
        Has $s 'AdoptControl(args[7], args[8], args[9]); AssertStartupDeadline(); AssertIndependent(); if (ObserveProducer()) break; AssertStartupDeadline(); contextCapture.AssertHealthy(); StartReadyWriter(args[4], args[9]); AssertStartupDeadline();'
    },'AdoptControl(args[7], args[8], args[9]); AssertStartupDeadline(); AssertIndependent();','AdoptControl(args[7], args[8], args[9]); AssertStartupDeadline();'),
    @('READY_binds_complete_private_context_digest_and_length','writer',{
        param($s)
        Order $s @('Require(contextCapture != null && contextCapture.IsComplete);', 'byte[] frame = Encoding.ASCII.GetBytes(', 'contextCapture.Length.ToString(CultureInfo.InvariantCulture) + " " + contextCapture.Sha256 + " " + Stratus.RuntimeBuild.CompilerJobWire.HandleText(producerTransfer) + "\n");', 'Require(frame.Length <= 352);')
    },'contextCapture.Sha256','"unverified"'),
    @('HELLO_binds_complete_private_context_digest_and_length','controlfields',{
        param($s)
        Has $s 'Require(contextCapture != null && contextCapture.IsComplete);'
        Has $s 'contextCapture.Length.ToString(CultureInfo.InvariantCulture) + " " + contextCapture.Sha256 + "\n";'
    },'contextCapture.Length.ToString(CultureInfo.InvariantCulture)','"0"'),
    @('capture_fault_throws_to_original_STOP_without_reset','main',{
        param($s)
        Has $s 'contextCapture.AssertHealthy();' 4
        Has $s 'catch (Exception) { return StopOriginalProducer() ? 204 : 205; }'
        Has $s 'protocolStartAttempted = true;' 1
        Has $s 'protocolStartAttempted = false' 0
    },'protocolStartAttempted = true;','protocolStartAttempted = false;')
)
Assert-Guardian ($rows.Count -eq 74) 'Fixed context-enabled predicate/mutation count differs.'
$rows+=@(
    @('admission_ack_uses_distinct_exact_prefix_and_original_context_fields','control',{
        param($s) Has $s 'byte[] acknowledgement = Encoding.ASCII.GetBytes("STRATUS_GUARDIAN_ADMISSION_ACK_V6 " + ControlFields(nonce));';Has $s 'acknowledgement.Length <= 256'
    },'"STRATUS_GUARDIAN_ADMISSION_ACK_V6 "','"STRATUS_GUARDIAN_HELLO_V6 "'),
    @('one_writer_serializes_flushed_hello_then_permitted_ack','control',{
        param($s) $writer=Block $s 'controlWriter = new Thread(delegate()'
        Order $writer @('controlOutput.Write(hello, 0, hello.Length); controlOutput.Flush();','Interlocked.Exchange(ref controlHelloWritten, 1);','while (Interlocked.CompareExchange(ref controlAckPermit, 0, 0) == 0)','Require(Interlocked.CompareExchange(ref controlAckWriteAttempted, 1, 0) == 0);','controlOutput.Write(acknowledgement, 0, acknowledgement.Length); controlOutput.Flush();','Interlocked.Exchange(ref controlAckWritten, 1);')
        Has $s 'controlWriter = new Thread(' 1
    },'while (Interlocked.CompareExchange(ref controlAckPermit, 0, 0) == 0)','while (false)'),
    @('ack_main_permit_requires_completed_hello_fresh_owner_and_no_fault','main',{
        param($s) Has $s 'if (!controlAckAttempted && Interlocked.CompareExchange(ref controlHelloObserved, 0, 0) == 1 && Interlocked.CompareExchange(ref controlHelloWritten, 0, 0) == 1) { AssertStartupDeadline(); AssertIndependent(); if (ObserveProducer()) break; AssertStartupDeadline(); contextCapture.AssertHealthy(); Require(contextCapture.IsComplete && Interlocked.CompareExchange(ref controlFailed, 0, 0) == 0 && Interlocked.CompareExchange(ref writerFailed, 0, 0) == 0); controlAckAttempted = true; Interlocked.Exchange(ref controlAckPermit, 1); AssertStartupDeadline(); }'
    },'Interlocked.CompareExchange(ref controlHelloWritten, 0, 0) == 1','true'),
    @('control_worker_uses_original_deadline_and_local_monotonic_clock','controlworker',{
        param($s) Has $s 'deadlineTick == checked(startedTick + StartupMilliseconds)';Has $s 'Require(now >= startedTick && now >= lastTick && now < deadlineTick); lastTick = now;';Has $s 'AssertIndependent(); Require(!ObserveProducer());';Has $s 'Require(after >= lastTick && after < deadlineTick); lastTick = after;'
        Has $s 'Require(Interlocked.CompareExchange(ref controlFailed, 0, 0) == 0 && Interlocked.CompareExchange(ref writerFailed, 0, 0) == 0);' 2
        Has $s 'contextCapture.AssertHealthy();' 2;Has $s 'Require(contextCapture.IsComplete);'
        foreach($bad in @('lock (','previousTick','startedTick =','deadlineTick =','Stopwatch.StartNew(')){Has $s $bad 0}
    },'now < deadlineTick','now <= deadlineTick'),
    @('ready_remains_independent_of_ack_progress','writer',{
        param($s) Has $s 'Require(contextCapture != null && contextCapture.IsComplete);';Has $s 'readyWriter.IsBackground = true;';foreach($bad in @('controlAckPermit','controlAckWritten','controlHelloObserved','controlHelloWritten','.Join(','Thread.Sleep(')){Has $s $bad 0}
    },'Require(contextCapture != null && contextCapture.IsComplete);','Require(contextCapture != null && contextCapture.IsComplete && controlAckWritten == 1);'),
    @('ack_does_not_block_or_move_worker_io_and_clock_lock_to_watch_loop','main',{
        param($s) foreach($bad in @('controlOutput.Write(','controlOutput.Flush(','controlInput.Read(','.Join(','lock (','AssertControlWorker(')){Has $s $bad 0};Has $s 'Thread.Sleep(' 1;Has (Block $s 'if (ownerExited)') 'Thread.Sleep(10); continue;';Has $s 'Interlocked.Exchange(ref controlAckPermit, 1);' 1;Has $s 'uint wait = WaitForSingleObject(producer, 50);'
    },'Interlocked.Exchange(ref controlAckPermit, 1);','Interlocked.Exchange(ref controlAckPermit, 1); controlWriter.Join();'),
    @('stop_latches_control_failure_before_identity_probe_or_native_work','stop',{
        param($s) Has $s 'private static bool StopOriginalProducer() { Interlocked.Exchange(ref controlFailed, 1); if (jobTransfer != null) jobTransfer.CloseAdmission(); if (rootTransfer != null) rootTransfer.CloseAdmission();';Order $s @('Interlocked.Exchange(ref controlFailed, 1);','if (jobTransfer != null) jobTransfer.CloseAdmission();','if (rootTransfer != null) rootTransfer.CloseAdmission();','Interlocked.CompareExchange(ref stopAttempted, 1, 0)','stopClock = Stopwatch.StartNew();','if (!identityEstablished) return false;','try { bool dead = ObserveProducer();');Has $s 'Interlocked.Exchange(ref controlFailed, 0)' 0
    },'Interlocked.Exchange(ref controlFailed, 1);','/* STOP did not close ACK admission */')
)
Assert-Guardian ($rows.Count -eq 81) 'Fixed ACK-enabled predicate/mutation count differs.'
# Separate directed terminal pairs follow all 81 historical pairs unchanged in ID/order.
$rows+=@(
    @('terminal_native_zero_exit_is_required_before_normal_drain_success','normal',{
        param($s) $drain=Method $s 'private static bool MayDrainNormalTerminal('
        Has $drain 'Interlocked.CompareExchange(ref observation.PreparedObserved, 0, 0) == 1 && observation.Record != null'
        Has $drain 'Interlocked.CompareExchange(ref observation.AckWriteAttempted, 0, 0) == 1 && Interlocked.CompareExchange(ref observation.HalfCloseAttempted, 0, 0) == 1'
        Has $drain 'Interlocked.CompareExchange(ref observation.HalfCloseReturned, 0, 0) == 0 || observation.HalfCloseSucceeded'
        $proof=Method $s 'private static void RequireOriginalProducerNormalExitForArmedDrain('
        Order $proof @('AssertNormalTerminalDrainAdmission();','DemandQualifiedNormalTerminalObservation();','normalProducerExit = observation; observation.Attempted = true;','observation.Pid = GetProcessId(producer);','Require(observation.Pid == producerPid);','Require(observation.BeforeWait == WaitObject);','observation.TimesSucceeded = GetProcessTimes(producer, out observation.Created, out observation.Exited, out observation.Kernel, out observation.User);','Require(observation.TimesSucceeded && observation.Created == producerBirth && observation.Created > 0 && observation.Exited > 0 && observation.Exited >= observation.Created);','observation.ExitSucceeded = GetExitCodeProcess(producer, out observation.ExitCode);','Require(observation.ExitSucceeded && observation.ExitCode == 0);','Require(observation.AfterWait == WaitObject);','observation.Proven = true;')
        Has $proof 'normalProducerExit.Attempted && normalProducerExit.Proven'
        Has $proof 'catch (Exception) { Interlocked.Exchange(ref normalTerminalFailed, 1); throw; }'
        foreach($bad in @('TerminateProcess(','TerminateJobObject(','OpenProcess(','Stopwatch.StartNew(')){Has $proof $bad 0}
    },'observation.ExitCode == 0','observation.ExitCode >= 0'),
    @('terminal_stdout_is_serialized_by_original_ready_writer_after_normal_proof','normal',{
        param($s) $ready=Method $s 'private static void StartReadyWriter('; $output=Method $s 'private static void WriteNormalTerminalStdout('
        Order $ready @('readyStream.Write(frame, 0, frame.Length);','readyStream.Flush();','readyWriteReturned = true;','Interlocked.Exchange(ref readyWritten, 1);','WriteNormalTerminalStdout(ref workerTick);','finally { Interlocked.Exchange(ref readyWriterSettled, 1); }','readyWriterStartAttempted = true; readyWriter.Start(); readyWriterStartReturned = true;')
        Has $s 'private const int MaximumGuardianStdoutBytes = 4096;'
        Has $output 'Require(Object.ReferenceEquals(Thread.CurrentThread, readyWriter));'
        Has $output 'Interlocked.CompareExchange(ref normalProofCompleted, 0, 0) == 1 && jobTransfer.NormalClosureProven && rootTransfer.NormalClosureProven'
        Has $output 'Stratus.RuntimeBuild.TerminalWire.RequireExact(terminalStdoutFrame, Stratus.RuntimeBuild.TerminalWire.GuardianTerminal(terminalObservation.Record, originalReadyNonce, normalProducerExit.Exited));'
        Order $output @('Require(checked(originalReadyFrame.Length + terminalStdoutFrame.Length) <= MaximumGuardianStdoutBytes);','DemandQualifiedNormalTerminalObservation();','Require(Interlocked.CompareExchange(ref terminalOutputWriteAttempted, 1, 0) == 0);','readyStream.Write(terminalStdoutFrame, 0, terminalStdoutFrame.Length); readyStream.Flush();','terminalOutputWriteReturned = true;','Interlocked.Exchange(ref terminalOutputWritten, 1);')
        Has $output 'new Thread(' 0;Has $output 'Console.OpenStandardOutput(' 0
    },'WriteNormalTerminalStdout(ref workerTick);','/* actual original stdout terminal call omitted */'),
    @('terminal_reader_eof_requires_exact_completed_commit_stage_not_earlier_phase','control',{
        param($s) $reader=Block $s 'controlReader = new Thread(delegate()'
        Has $reader 'if (count == 0) { Require(stage == 5 && offset == 0 && terminalAckAttemptAtRead == 1 && terminalCloseAttemptAtRead == 1 && preparedAtRead != null && Object.ReferenceEquals(preparedAtRead, terminalObservation.Record) && Interlocked.CompareExchange(ref terminalObservation.CommitObserved, 0, 0) == 1); Interlocked.Exchange(ref terminalObservation.InputEofObserved, 1); return; }'
        Has $reader 'catch (Exception) { Interlocked.Exchange(ref controlFailed, 1); }'
    },'stage == 5 && offset == 0','stage >= 3 && offset == 0')
)
$expected=168
Assert-Guardian ($rows.Count -eq 84) 'Historical plus directed terminal case inventory differs.'
$executed=0;$passed=0;$results=[Collections.Generic.List[object]]::new()
foreach($row in $rows){
    $name=$row[0];$selected=$blocks[$row[1]];$check=$row[2]
    $executed++; & $check $selected; $passed++
    $results.Add([pscustomobject]@{name=$name;variant='original_source';passed=$true})
    if($row.Count -eq 6){$mutated=Move-Tokens $selected $row[3] $row[4]}else{$mutated=Replace-Tokens $selected $row[3] $row[4]}
    $executed++;$rejected=$false
    try{& $check $mutated}catch{
        Assert-Guardian ($_.Exception.Message.StartsWith('Guardian source contract:',[StringComparison]::Ordinal)) ('Unexpected negative-control exception: '+$name)
        $rejected=$true
    }
    Assert-Guardian $rejected ('The predicate accepted its negative mutation: '+$name)
    $passed++;$results.Add([pscustomobject]@{name=$name;variant='mutated_source_rejected';passed=$true})
}
Assert-Guardian ($executed -eq $expected -and $passed -eq $expected) 'The fixed case count did not complete.'
$subjectAfter=Read-GuardianSource $GuardianPath
$wireAfter=Read-GuardianSource $WirePath
$terminalAfter=Read-GuardianSource $TerminalPath
$testAfter=Read-GuardianSource $PSCommandPath
Assert-Guardian ($subjectAfter.sha256 -ceq $subject.sha256 -and $wireAfter.sha256 -ceq $wireSource.sha256 -and $terminalAfter.sha256 -ceq $terminalSource.sha256 -and $testAfter.sha256 -ceq $testSource.sha256) 'Source/test bytes changed during inspection.'
[pscustomobject]@{
    format='stratus-guardian-owner-watch-source-contract-tests-v3'
    expected=$expected;executed=$executed;passed=$passed;failed=0
    powershell_version=$PSVersionTable.PSVersion.ToString();powershell_edition=$PSVersionTable.PSEdition
    guardian_source_sha256=$subject.sha256;test_sha256=$testSource.sha256;source_hashes_unchanged=$true
    wire_source_sha256_before=$wireSource.sha256;wire_source_sha256_after=$wireAfter.sha256
    terminal_source_sha256_before=$terminalSource.sha256;terminal_source_sha256_after=$terminalAfter.sha256
    entry_source_tokens_and_mutations_only=$true
    target_compiled=$false;target_loaded=$false;target_lifecycle_executed=$false;native_qualified=$false
    inherited_access_rights_proven=$false;launcher_provenance_proven=$false;original_global_deadline_proven=$false
    shared_startup_deadline_native_behavior_proven=$false;approved_build_context_proven=$false
    private_control_transport_qualified=$false;compiler_job_handoff_acknowledged=$false
    guardian_transport_qualified=$false;producer_resume_authorized=$false;full_build_accepted=$false
    cases=$results.ToArray()
}|ConvertTo-Json -Depth 6 -Compress
