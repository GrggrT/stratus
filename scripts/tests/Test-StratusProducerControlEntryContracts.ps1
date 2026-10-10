param([string] $ProducerPath,[string] $InvocationPath,[string] $TerminalPath)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if([string]::IsNullOrWhiteSpace($ProducerPath)){$ProducerPath=Join-Path $PSScriptRoot '..\Stratus.RuntimeBuild.ProducerProgram.cs'}
if([string]::IsNullOrWhiteSpace($InvocationPath)){$InvocationPath=Join-Path $PSScriptRoot '..\Stratus.RuntimeBuild.ProducerInvocation.cs'}
if([string]::IsNullOrWhiteSpace($TerminalPath)){$TerminalPath=Join-Path $PSScriptRoot '..\Stratus.RuntimeBuild.ProducerTerminal.cs'}
# Fixed source-token predicates and in-memory negative mutations only. This does
# not compile, load, evaluate or invoke the C# subject or create a pipe/process.
# Token presence/order is not semantic/native behavior or peer-provenance proof.
# Historical IDs/order/count remain fixed; legacy no-success/EOF IDs now bind
# actual terminal-only success and reject premature bytes, EOF and zero exit.
# Historical case IDs are retained; v6 requires ten arguments and independently
# admitted context before HELLO, then exact ACK before the closed input phase.
function Assert-Peer {param([bool]$Okay,[string]$Message) if(-not $Okay){throw ('Producer entry source contract: '+$Message)}}
function Read-PeerSource {
    param([string]$Path)
    $full=[IO.Path]::GetFullPath($Path);$length=[IO.FileInfo]::new($full).Length
    Assert-Peer ($length -gt 0 -and $length -le 1048576) 'Source exceeds the fixed inspection bound.'
    $raw=[IO.File]::ReadAllBytes($full)
    Assert-Peer ($raw.LongLength -eq $length) 'Source length changed during read.'
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
    $wanted=Tokens $Sequence;Assert-Peer ($wanted.Count -gt 0) 'Empty predicate.'
    $found=[Collections.Generic.List[int]]::new()
    for($i=0;$i -le $Source.Count-$wanted.Count;$i++){
        $same=$true
        for($j=0;$j -lt $wanted.Count;$j++){if($Source[$i+$j] -cne $wanted[$j]){$same=$false;break}}
        if($same){$found.Add($i)}
    }
    return ,$found.ToArray()
}
function Has {param([string[]]$Source,[string]$Sequence,[int]$Count=1) Assert-Peer ((Find $Source $Sequence).Count -eq $Count) ('Sequence differs: '+$Sequence)}
function Order {
    param([string[]]$Source,[string[]]$Sequences)
    $last=-1
    foreach($sequence in $Sequences){$found=Find $Source $sequence;Assert-Peer ($found.Count -eq 1 -and $found[0] -gt $last) ('Order differs: '+$sequence);$last=$found[0]}
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
    Assert-Peer ($found.Count -eq 1) ('Missing/ambiguous scoped block: '+$Signature)
    $open=$found[0]
    while($open -lt $Source.Count -and $Source[$open] -cne '{'){$open++}
    Assert-Peer ($open -lt $Source.Count) 'Opening brace missing.'
    $depth=0
    for($i=$open;$i -lt $Source.Count;$i++){
        if($Source[$i] -ceq '{'){$depth++}elseif($Source[$i] -ceq '}'){$depth--}
        if($depth -eq 0){return ,([string[]]$Source[$found[0]..$i])}
    }
    throw 'Producer entry source contract: Unbalanced source block.'
}
function Method {param([string[]]$Class,[string]$Signature) return ,(Block $Class $Signature -Direct)}
function Replace-Tokens {
    param([string[]]$Source,[string]$Needle,[string]$Replacement)
    $at=Find $Source $Needle;Assert-Peer ($at.Count -eq 1) ('Mutation anchor differs: '+$Needle)
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
$subject=Read-PeerSource $ProducerPath
$invocationSource=Read-PeerSource $InvocationPath
$terminalSource=Read-PeerSource $TerminalPath
$testSource=Read-PeerSource $PSCommandPath
$entry=Block (Tokens $subject.text) 'internal static class Program'
$program=Block (Tokens $invocationSource.text) 'internal sealed partial class ProducerInvocation'
$terminal=Block (Tokens $terminalSource.text) 'internal sealed partial class ProducerInvocation'
# Combined tokens are only a class-selection container. Actual execution order
# is checked inside methods and their callsites, never from declaration order.
# Flattened method containers below use Block, not depth-1 Method: each original
# method was already selected at its real class depth before concatenation.
$blocks=@{
    program=$program
    flow=[string[]]@($entry+$program)
    normalflow=[string[]]@($entry+$program+(Method $terminal 'internal bool PollTerminalExit(')+(Method $terminal 'private void AssertPreparedTerminalObservation('))
    terminalreader=[string[]]@((Method $program 'private void StartReader(')+(Method $terminal 'private void ReceiveOriginalTerminalAcknowledgement('))
    terminalwriter=[string[]]@((Method $program 'private void StartWriter(')+$terminal)
    main=Method $program 'internal void Poll('
    start=Method $program 'internal void Start('
    prepare=Method $program 'internal static ProducerInvocation PrepareFromInheritedEntry('
    decimal=Method $program 'private static ulong Decimal('
    handle=Method $program 'private static IntPtr PositiveHandle('
    deadline=Method $program 'internal void AssertDeadline('
    capture=Method $program 'private void CaptureOwnIdentity('
    identity=Method $program 'internal void AssertOwnIdentity('
    pipes=Method $program 'private void AdmitInheritedPipes('
    frames=Method $program 'private void PrepareFrames('
    reader=Method $program 'private void StartReader('
    writer=Method $program 'private void StartWriter('
    contextgate=Block (Method $program 'internal void Poll(') 'if (!protocolStartAttempted && contextCapture.IsComplete)'
}
$rows=@(
    @('private_entry_has_fixed_bounds_and_no_public_authority','flow',{
        param($s)
        Has $s 'private static int Main(string[] args)'
        Has $s 'private const ulong StartupMilliseconds = 60000;'
        Has $s 'private const int MaximumFrameBytes = 256;'
        Has $s 'public' 0
    },'private static int Main(string[] args)','public static int Main(string[] args)'),
    @('exact_seven_args_require_both_fixed_version_markers','prepare',{
        param($s)
        Order $s @('Require(args != null && args.Length == 10);','string[] copied = (string[])args.Clone();','Require(copied[0] == "--stratus-producer-entry-v6" && copied[1] == "--stratus-producer-control-v6");')
    },'args.Length == 10','args.Length >= 10'),
    @('decimal_is_positive_canonical_bounded_uint64','decimal',{
        param($s)
        Has $s 'value != null && value.Length > 0 && value.Length <= 20'
        Has $s 'UInt64.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out number)'
        Has $s 'Require(number > 0 && number.ToString(CultureInfo.InvariantCulture) == value);'
    },'number > 0','number >= 0'),
    @('deadline_uses_original_native_clock_checked_range_and_sticky_failure','deadline',{
        param($s)
        Has $s 'lock (clockGate)'
        Has $s 'ulong now = GetTickCount64();'
        Has $s 'Require(startedTick > 0 && startedTick <= UInt64.MaxValue - StartupMilliseconds && deadlineTick == checked(startedTick + StartupMilliseconds));'
        Has $s 'catch (Exception) { RecordFailure(); throw; }'
        Has $s 'Stopwatch.StartNew(' 0
    },'deadlineTick == checked(startedTick + StartupMilliseconds)','deadlineTick >= checked(startedTick + StartupMilliseconds)'),
    @('handle_decoder_matches_unsigned_x86_export_and_rejects_pseudo_values','handle',{
        param($s)
        Has $s 'Require(IntPtr.Size == 8 ? value <= Int64.MaxValue : value < UInt32.MaxValue);'
        Has $s 'Require(value > 0);'
        Has $s 'IntPtr handle = IntPtr.Size == 8 ? new IntPtr((long)value) : new IntPtr(unchecked((int)(uint)value));'
        Has $s 'Require(handle != new IntPtr(-2) && handle != new IntPtr(-3) && handle != new IntPtr(-4) && handle != new IntPtr(-5) && handle != new IntPtr(-6));'
    },'value < UInt32.MaxValue','value <= UInt32.MaxValue'),
    @('nonce_and_startup_ticks_are_parsed_once_before_native_admission','program',{
        param($s)
        $prepare=Method $s 'internal static ProducerInvocation PrepareFromInheritedEntry('
        Order $prepare @('LowerHex(copied[4], 64);','ulong start = Decimal(copied[5]), deadline = Decimal(copied[6]);','deadline == checked(start + StartupMilliseconds)','new ProducerInvocation(input, output, copied[4], start, deadline, copied[7], copied[8], copied[9])')
        $hex=Method $s 'private static void LowerHex('
        Has $hex 'value != null && value.Length == length'
        Has $hex "foreach (char c in value) Require((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'));"
        $ctor=Method $s 'private ProducerInvocation('
        Has $ctor 'startedTick = start; deadlineTick = deadline;'
        Has $s 'startedTick =' 1;Has $s 'deadlineTick =' 1
        Has (Method $s 'internal void Start(') 'AssertDeadline(); CaptureOwnIdentity(); AdmitInheritedPipes();'
    },'LowerHex(copied[4], 64);','/* nonce validation omitted */'),
    @('identity_is_actual_executing_P_not_caller_supplied_PID','capture',{
        param($s)
        Order $s @('uint actualPid = GetCurrentProcessId();','Require(producerPid == 0 && producerBirth == 0 && actualPid > 0);','Require(GetProcessTimes(GetCurrentProcess(), out created, out exited, out kernel, out user));','Require(created > 0);','producerPid = actualPid; producerBirth = created;')
        Has $s 'AssertDeadline();' 2
    },'producerPid = actualPid;','producerPid = 1;'),
    @('own_identity_recheck_binds_native_birth_with_deadline_on_both_sides','identity',{
        param($s)
        Has $s 'Require(producerPid > 0 && producerBirth > 0 && GetCurrentProcessId() == producerPid);'
        Has $s 'Require(GetProcessTimes(GetCurrentProcess(), out created, out exited, out kernel, out user)); Require(created == producerBirth); AssertDeadline();'
        Has $s 'AssertDeadline();' 2
    },'created == producerBirth','created > 0'),
    @('private_endpoints_are_distinct_and_never_stdio','pipes',{
        param($s)
        Has $s 'Require(inputValue != outputValue);'
        Has $s 'IntPtr stdin = GetStdHandle(-10), stdout = GetStdHandle(-11), stderr = GetStdHandle(-12);'
        Has $s 'Require(inputValue != stdin && inputValue != stdout && inputValue != stderr && outputValue != stdin && outputValue != stdout && outputValue != stderr);'
    },'inputValue != outputValue','inputValue != IntPtr.Zero'),
    @('both_original_endpoints_must_be_native_pipes_before_flag_mutation','pipes',{
        param($s)
        Order $s @('Require(GetFileType(inputValue) == FileTypePipe && GetFileType(outputValue) == FileTypePipe);','Require(SetHandleInformation(inputValue, 1, 0));','Require(SetHandleInformation(outputValue, 1, 0));')
    },'GetFileType(outputValue) == FileTypePipe','GetFileType(outputValue) != 0'),
    @('both_inherited_flags_are_cleared_and_rechecked_before_wrapping','pipes',{
        param($s)
        Order $s @('Require(GetHandleInformation(inputValue, out inputFlags) && (inputFlags & 1) == 1 && (inputFlags & 2) == 0);','Require(GetHandleInformation(outputValue, out outputFlags) && (outputFlags & 1) == 1 && (outputFlags & 2) == 0);','Require(SetHandleInformation(inputValue, 1, 0));','Require(SetHandleInformation(outputValue, 1, 0));','Require(GetHandleInformation(inputValue, out inputFlags) && (inputFlags & 3) == 0);','Require(GetHandleInformation(outputValue, out outputFlags) && (outputFlags & 3) == 0);','inputHandle = new SafePipeHandle(inputValue, false);')
    },'Require(SetHandleInformation(outputValue, 1, 0));','/* output inheritance left enabled */'),
    @('nonowning_retained_streams_have_exact_read_write_direction','pipes',{
        param($s)
        Has $s 'inputHandle = new SafePipeHandle(inputValue, false);'
        Has $s 'outputHandle = new SafePipeHandle(outputValue, false);'
        Has $s 'inputStream = new AnonymousPipeClientStream(PipeDirection.In, inputHandle);'
        Has $s 'outputStream = new AnonymousPipeClientStream(PipeDirection.Out, outputHandle);'
        Has $s '.Dispose(' 0
    },'new SafePipeHandle(inputValue, false)','new SafePipeHandle(inputValue, true)'),
    @('bounded_hello_frames_bind_original_fields_and_opposite_peer_roles','frames',{
        param($s)
        Has $s 'string values = channelNonce + " " + producerPid.ToString(CultureInfo.InvariantCulture) + " " + producerBirth.ToString(CultureInfo.InvariantCulture) + " " + startedTick.ToString(CultureInfo.InvariantCulture) + " " + deadlineTick.ToString(CultureInfo.InvariantCulture) + " " + contextCapture.Length.ToString(CultureInfo.InvariantCulture) + " " + contextCapture.Sha256 + "\n";'
        Has $s 'expectedHello = Encoding.ASCII.GetBytes("STRATUS_GUARDIAN_HELLO_V6 " + values);'
        Has $s 'responseHello = Encoding.ASCII.GetBytes("STRATUS_PRODUCER_HELLO_V6 " + values);'
        Has $s 'expectedAdmissionAck = Encoding.ASCII.GetBytes("STRATUS_GUARDIAN_ADMISSION_ACK_V6 " + values);'
        Has $s 'Require(expectedHello.Length > 0 && expectedHello.Length <= MaximumFrameBytes && responseHello.Length > 0 && responseHello.Length <= MaximumFrameBytes && expectedAdmissionAck.Length > 0 && expectedAdmissionAck.Length <= MaximumFrameBytes);'
    },'"STRATUS_GUARDIAN_HELLO_V6 "','"STRATUS_PRODUCER_HELLO_V6 "'),
    @('exactly_one_reader_is_background_before_start','reader',{
        param($s)
        Order $s @('Require(readerThread == null);','readerThread = new Thread(delegate() {','readerThread.IsBackground = true;','readerThread.Start();')
        Has $s 'new Thread(' 1
    },'readerThread.IsBackground = true;','readerThread.IsBackground = false;'),
    @('reader_bounds_actual_bytes_and_rejects_partial_EOF_or_mismatch','reader',{
        param($s)
        Has $s 'byte[] captured = new byte[MaximumFrameBytes + 1];'
        Has $s 'int count = inputStream.Read(captured, used, captured.Length - used); AssertDeadline(); Require(count > 0 && count <= captured.Length - used);'
        Has $s 'int end = checked(used + count); Require(end <= expectedHello.Length);'
        Has $s 'for (int i = used; i < end; ++i) Require(captured[i] == expectedHello[i]);'
    },'Require(end <= expectedHello.Length);','Require(end <= captured.Length);'),
    @('received_hello_is_latched_only_after_original_identity_and_deadline','reader',{
        param($s)
        Has $s 'AssertOwnIdentity(); AssertDeadline(); Interlocked.Exchange(ref helloReceived, 1);'
        Has $s 'Interlocked.Exchange(ref helloReceived, 1);' 1
    },'AssertOwnIdentity(); AssertDeadline(); Interlocked.Exchange(ref helloReceived, 1);','Interlocked.Exchange(ref helloReceived, 1); AssertOwnIdentity(); AssertDeadline();'),
    @('observed_extra_byte_or_EOF_latches_fault_not_terminal_acceptance','terminalreader',{
        param($s)
        $reader=Block $s 'private void StartReader('; $ack=Block $s 'private void ReceiveOriginalTerminalAcknowledgement('
        Has $reader 'ReceiveOriginalCompilerJobAck(); ReceiveOriginalCompilerRootAck(); ReceiveOriginalTerminalAcknowledgement();'
        Has $reader 'catch (Exception) { RecordFailure(); }';Has $reader 'finally { Interlocked.Exchange(ref terminalReaderSettled, 1); }'
        Order $ack @('int firstCount = inputStream.Read(first, 0, 1);','RetainedProducerTerminal originalAtRead = Interlocked.CompareExchange(ref producerTerminal, null, null);','int writeAttemptedAtRead = Interlocked.CompareExchange(ref terminalPreparedWriteAttempted, 0, 0);','Require(firstCount == 1 && originalAtRead != null && writeAttemptedAtRead == 1);','Require(expected.Length > 0 && expected.Length <= TerminalWire.MaximumAckBytes);','Require(count > 0 && count <= actual.Length - used);','TerminalWire.RequireExact(actual, expected);','Require(Interlocked.CompareExchange(ref terminalAckReceived, 1, 0) == 0);','Require(Interlocked.CompareExchange(ref terminalEofReadAttempted, 1, 0) == 0);','int eof = inputStream.Read(first, 0, 1);','Interlocked.Exchange(ref terminalEofReadCount, eof); Interlocked.Exchange(ref terminalEofReadReturned, 1);','AssertPreparedTerminalObservation(originalAtRead); Require(eof == 0);','Require(Interlocked.CompareExchange(ref terminalGuardianEof, 1, 0) == 0);')
        foreach($bad in @('terminalPreparedWritten','Thread.Sleep(','.Join(','return true;')){Has $ack $bad 0}
    },'Require(eof == 0);','Require(eof >= 0);'),
    @('response_writer_is_single_use_and_requires_received_hello','writer',{
        param($s)
        Order $s @('Require(!writerStartAttempted && writerThread == null && Interlocked.CompareExchange(ref helloReceived, 0, 0) == 1);','writerStartAttempted = true;','writerThread = new Thread(delegate() {')
        Has $s 'new Thread(' 1
    },'Interlocked.CompareExchange(ref helloReceived, 0, 0) == 1','Interlocked.CompareExchange(ref helloReceived, 0, 0) >= 0'),
    @('writer_is_background_and_its_IO_failure_is_sticky','writer',{
        param($s)
        Order $s @('writerThread = new Thread(delegate() {','writerThread.IsBackground = true;','writerThread.Start();')
        $worker=Block $s 'writerThread = new Thread(delegate()'
        Has $worker 'AssertOwnIdentity(); AssertDeadline(); AssertClean(); outputStream.Write(responseHello, 0, responseHello.Length); outputStream.Flush();'
        Has $worker 'catch (Exception) { RecordFailure(); }'
    },'writerThread.IsBackground = true;','writerThread.IsBackground = false;'),
    @('response_completion_follows_flush_and_fresh_boundaries_same_count','writer',{
        param($s)
        Order $s @('outputStream.Write(responseHello, 0, responseHello.Length);','outputStream.Flush();','AssertDeadline(); AssertOwnIdentity(); AssertClean();','Interlocked.Exchange(ref responseWritten, 1);','SendOriginalCompilerJobOffer();')
    },'Interlocked.Exchange(ref responseWritten, 1);','outputStream.Flush();','move'),
    @('main_preserves_identity_admission_frames_reader_order','flow',{
        param($s)
        $p=Block $s 'internal sealed partial class ProducerInvocation'
        Order (Method (Block $s 'internal static class Program') 'private static int Main(') @('invocation = Stratus.RuntimeBuild.ProducerInvocation.PrepareFromInheritedEntry(args);','invocation.Start();','while (true)','invocation.Poll();')
        Order (Method $p 'internal void Start(') @('CaptureOwnIdentity(); AdmitInheritedPipes();','contextCapture = ContextCapture.Prepare(','contextCapture.Start();','started = true;')
        Order (Method $p 'internal void Poll(') @('Require(startAttempted && started && contextCapture != null);','if (!protocolStartAttempted && contextCapture.IsComplete)','PrepareFrames(); StartReader();')
    },'StartReader();','AdmitInheritedPipes();','move'),
    @('main_never_blocks_on_control_IO_or_worker_join','flow',{
        param($s)
        $main=Method (Block $s 'internal static class Program') 'private static int Main('
        $poll=Method (Block $s 'internal sealed partial class ProducerInvocation') 'internal void Poll('
        foreach($selected in @($main,$poll)){foreach($forbidden in @('inputStream.Read(','outputStream.Write(','outputStream.Flush(','readerThread.Join(','writerThread.Join(')){Has $selected $forbidden 0}}
        Has $poll 'if (Interlocked.CompareExchange(ref helloReceived, 0, 0) == 1 && !writerStartAttempted) StartWriter();'
        Has $main 'Thread.Sleep(10); invocation.AssertDeadline();'
    },'StartReader();','StartReader(); readerThread.Join();'),
    @('main_returns_only_nonzero_diagnostic_or_sticky_failure','normalflow',{
        param($s)
        $main=Method (Block $s 'internal static class Program') 'private static int Main('
        $poll=Method (Block $s 'internal sealed partial class ProducerInvocation') 'internal void Poll('
        Has $poll 'if (Interlocked.CompareExchange(ref inputVerified, 0, 0) == 1 && !compilerStartAttempted) { AssertInputAdmission(); StartCompiler(); }'
        Has $poll 'if (!inputStartAttempted && Interlocked.CompareExchange(ref responseWritten, 0, 0) == 1 && Interlocked.CompareExchange(ref guardianAckReceived, 0, 0) == 1) { StartInputs(); }'
        Has $poll 'catch (Exception) { RecordFailure(); throw; }'
        Has $main 'catch (Exception) { if (invocation != null) invocation.RecordFailure(); return 205; }'
        Has $main 'return' 2;Has $main 'return 0;' 1;Has $s 'return 202;' 0
        Has $main 'if (invocation.PollTerminalExit()) return 0;'
        $exit=Block $s 'internal bool PollTerminalExit('
        Order $exit @('AssertOriginal(); AssertOwnIdentity(); AssertDeadline(); AssertClean();','RetainedProducerTerminal original = Interlocked.CompareExchange(ref producerTerminal, null, null);','if (original == null || Interlocked.CompareExchange(ref terminalReaderSettled, 0, 0) != 1 || Interlocked.CompareExchange(ref terminalWriterSettled, 0, 0) != 1) return false;','AssertPreparedTerminalObservation(original); Require(readerThread != null && writerThread != null && inputWorker != null && Interlocked.CompareExchange(ref inputWorkerSettled, 0, 0) == 1);','if (!readerThread.Join(0) || !writerThread.Join(0) || !inputWorker.Join(0) || !contextCapture.IsWorkerSettledForTerminal) return false;','TerminalWire.RequireExact(terminalActualAck, original.ExpectedAck);','AssertPreparedTerminalObservation(original); AssertDeadline(); return true;','catch (Exception) { RecordFailure(); throw; }')
        Has $exit 'Object.ReferenceEquals(original.Owner.PollPreparedLocalClosure(), original.Closure)'
        Has $exit 'Interlocked.CompareExchange(ref terminalPreparedFlushReturned, 0, 0) == 1'
        Has $exit 'Interlocked.CompareExchange(ref terminalAckReceived, 0, 0) == 1'
        Has $exit 'Interlocked.CompareExchange(ref terminalEofReadReturned, 0, 0) == 1 && Interlocked.CompareExchange(ref terminalEofReadCount, 0, 0) == 0'
        Has $exit 'Interlocked.CompareExchange(ref terminalCommitFlushReturned, 0, 0) == 1'
        Has $exit 'terminalHalfCloseOriginal == outputValue && Interlocked.CompareExchange(ref terminalHalfCloseAttempted, 0, 0) == 1 && Interlocked.CompareExchange(ref terminalHalfCloseReturned, 0, 0) == 1 && Interlocked.CompareExchange(ref terminalHalfCloseSucceeded, 0, 0) == 1'
        Has $exit 'Interlocked.CompareExchange(ref terminalStdoutFlushReturned, 0, 0) == 1 && Interlocked.CompareExchange(ref terminalStdoutWritten, 0, 0) == 1'
        $bound=Block $s 'private void AssertPreparedTerminalObservation('
        Has $bound 'Object.ReferenceEquals(Interlocked.CompareExchange(ref producerTerminal, null, null), original)'
        Has $bound 'original.Owner.AssertTerminalObservation(original.Closure); AssertProducerTerminalInputsReleased(original.Owner, original.Closure);'
    },'return 205;','return 0;'),
    @('entry_has_no_payload_child_Job_Resume_or_manual_close','program',{
        param($s)
        foreach($forbidden in @('File.','FileStream','Directory.','CreateProcess(','Process.Start(','OpenProcess(','GetProcessById(','ResumeThread(','TerminateProcess(','TerminateJobObject(','AssignProcessToJobObject(','CloseHandle(','.Dispose(','.Close(')){Has $s $forbidden 0}
    },'PrepareFrames();','PrepareFrames(); ResumeThread(inputValue);'),
    @('deadline_checks_future_regression_expiry_before_advancing_clock','deadline',{
        param($s)
        Order $s @('ulong now = GetTickCount64();','Require(now >= startedTick && now >= previousTick && now < deadlineTick);','previousTick = now;')
        Has $s 'startedTick =' 0
        Has $s 'deadlineTick =' 0
    },'previousTick = now;','Require(now >= startedTick && now >= previousTick && now < deadlineTick);','move')
)
Assert-Peer ($rows.Count -eq 25) 'The fixed 25 predicate/mutation pairs changed.'
$rows+=@(
    @('context_capture_is_retained_before_its_worker_start','start',{
        param($s)
        Order $s @('contextCapture = ContextCapture.Prepare(contextHandleText, contextLengthText, contextSha256, new IntPtr[] { inputValue, outputValue });','AssertOwnIdentity(); AssertDeadline(); contextCapture.Start();','started = true;')
    },'contextCapture = ContextCapture.Prepare','ContextCapture unretained = ContextCapture.Prepare'),
    @('both_control_endpoints_are_excluded_from_context_handle','start',{
        param($s)
        Has $s 'new IntPtr[] { inputValue, outputValue }'
    },'new IntPtr[] { inputValue, outputValue }','new IntPtr[] { inputValue, inputValue }'),
    @('pending_capture_does_not_block_original_clock_and_identity_loop','flow',{
        param($s)
        $p=Block $s 'internal sealed partial class ProducerInvocation'
        $main=Method (Block $s 'internal static class Program') 'private static int Main('
        $poll=Method $p 'internal void Poll('; $start=Method $p 'internal void Start('
        Has $poll 'AssertDeadline(); AssertOwnIdentity(); AssertClean(); contextCapture.AssertHealthy();'
        foreach($selected in @($main,$poll,$start)){foreach($forbidden in @('MapViewOfFile(','Marshal.Copy(','ComputeHash(','CopyBytes(','.Join(')){Has $selected $forbidden 0}}
        Has $main 'while (true) { invocation.Poll(); if (invocation.PollTerminalExit()) return 0; Thread.Sleep(10); invocation.AssertDeadline(); }'
    },'contextCapture.Start();','contextCapture.Start(); contextCapture.CopyBytes();'),
    @('context_completion_is_required_before_single_protocol_start','contextgate',{
        param($s)
        Has $s 'if (!protocolStartAttempted && contextCapture.IsComplete)'
        Order $s @('AssertOwnIdentity(); AssertDeadline(); AssertClean(); contextCapture.AssertHealthy();','protocolStartAttempted = true;','PrepareFrames(); StartReader();','AssertOwnIdentity(); AssertDeadline(); AssertClean(); }')
    },'contextCapture.IsComplete','true'),
    @('same_count_protocol_move_cannot_precede_fresh_context_boundary','contextgate',{
        param($s)
        Order $s @('AssertOwnIdentity(); AssertDeadline(); AssertClean(); contextCapture.AssertHealthy();','protocolStartAttempted = true;','PrepareFrames(); StartReader();')
    },'PrepareFrames(); StartReader();','AssertOwnIdentity(); AssertDeadline(); AssertClean(); contextCapture.AssertHealthy();','move'),
    @('hello_frames_bind_captured_context_before_encoding','frames',{
        param($s)
        Order $s @('Require(contextCapture != null && contextCapture.IsComplete);','string values =','contextCapture.Length.ToString(CultureInfo.InvariantCulture) + " " + contextCapture.Sha256 + "\n";','expectedHello = Encoding.ASCII.GetBytes(')
    },'contextCapture.Sha256','"unverified"'),
    @('protocol_start_latch_cannot_reset_or_bypass_capture','program',{
        param($s)
        Has $s 'protocolStartAttempted = true;' 1
        Has $s 'protocolStartAttempted = false' 0
        Has $s 'PrepareFrames(); StartReader();' 1
    },'protocolStartAttempted = true;','protocolStartAttempted = false;'),
    @('capture_failure_remains_on_nonzero_main_failure_route','flow',{
        param($s)
        $poll=Method (Block $s 'internal sealed partial class ProducerInvocation') 'internal void Poll('
        Has $poll 'contextCapture.AssertHealthy();' 2
        Has $poll 'catch (Exception) { RecordFailure(); throw; }'
        Has (Method (Block $s 'internal static class Program') 'private static int Main(') 'catch (Exception) { if (invocation != null) invocation.RecordFailure(); return 205; }'
    },'AssertDeadline(); AssertOwnIdentity(); AssertClean(); contextCapture.AssertHealthy();','AssertDeadline(); AssertOwnIdentity(); AssertClean();')
)
Assert-Peer ($rows.Count -eq 33) 'Fixed context-enabled predicate/mutation count differs.'
$rows+=@(
    @('ack_uses_same_exact_original_fields_and_distinct_prefix','frames',{
        param($s) Has $s 'expectedAdmissionAck = Encoding.ASCII.GetBytes("STRATUS_GUARDIAN_ADMISSION_ACK_V6 " + values);';Has $s 'expectedAdmissionAck.Length > 0 && expectedAdmissionAck.Length <= MaximumFrameBytes'
    },'"STRATUS_GUARDIAN_ADMISSION_ACK_V6 "','"STRATUS_GUARDIAN_HELLO_V6 "'),
    @('ack_read_follows_hello_and_resets_only_buffer_offset','reader',{
        param($s) Order $s @('Interlocked.Exchange(ref helloReceived, 1);','used = 0; while (used < expectedAdmissionAck.Length)','Interlocked.Exchange(ref guardianAckReceived, 1);','ReceiveOriginalCompilerJobAck();','int extra = inputStream.Read(captured, 0, 1);');Has $s 'startedTick =' 0;Has $s 'deadlineTick =' 0
    },'used = 0; while (used < expectedAdmissionAck.Length)','used = expectedAdmissionAck.Length; while (used < expectedAdmissionAck.Length)'),
    @('ack_read_rejects_EOF_surplus_and_any_different_actual_byte','reader',{
        param($s) Has $s 'int count = inputStream.Read(captured, used, captured.Length - used); AssertDeadline(); AssertClean(); Require(count > 0 && count <= captured.Length - used); int end = checked(used + count); Require(end <= expectedAdmissionAck.Length);';Has $s 'for (int i = used; i < end; ++i) Require(captured[i] == expectedAdmissionAck[i]);'
    },'Require(end <= expectedAdmissionAck.Length);','Require(end <= captured.Length);'),
    @('ack_flag_requires_fresh_identity_deadline_health_and_admission','reader',{
        param($s) Has $s 'AssertOwnIdentity(); AssertDeadline(); AssertClean(); contextCapture.AssertHealthy(); Require(contextCapture.IsComplete); Interlocked.Exchange(ref guardianAckReceived, 1);';Has $s 'Interlocked.Exchange(ref guardianAckReceived, 1);' 1
    },'Require(contextCapture.IsComplete);','/* admission omitted */'),
    @('input_start_requires_both_response_flush_and_received_ack','main',{
        param($s) Has $s 'if (!inputStartAttempted && Interlocked.CompareExchange(ref responseWritten, 0, 0) == 1 && Interlocked.CompareExchange(ref guardianAckReceived, 0, 0) == 1) { StartInputs(); }';Has $s 'StartInputs();' 1
    },'Interlocked.CompareExchange(ref guardianAckReceived, 0, 0) == 1','true'),
    @('no_input_IO_or_new_deadline_is_added_to_entry_main','main',{
        param($s) foreach($bad in @('.Open(','.CaptureSnapshots(','.Join(','BuildInputLeaseSet.','Stopwatch.StartNew(','GetTickCount64(')){Has $s $bad 0};Has $s 'startedTick =' 0;Has $s 'deadlineTick =' 0
    },'StartInputs();','StartInputs(); inputThread.Join();')
)
Assert-Peer ($rows.Count -eq 39) 'Fixed ACK-enabled predicate/mutation count differs.'
# New directed pairs follow all 39 historical IDs in their original order.
$rows+=@(
    @('terminal_original_writer_requires_matching_ack_and_actual_eof_before_commit','terminalwriter',{
        param($s) $writer=Block $s 'private void StartWriter('; $send=Method $s 'private void SendOriginalTerminal('
        Order $writer @('SendOriginalCompilerJobOffer();','SendOriginalCompilerRootOffer();','SendOriginalTerminal();','finally { Interlocked.Exchange(ref terminalWriterSettled, 1); }')
        Has $writer 'writerThread = new Thread(' 1
        Order $send @('Require(Object.ReferenceEquals(Thread.CurrentThread, writerThread));','Require(Interlocked.CompareExchange(ref producerTerminal, original, null) == null);','DemandQualifiedProducerTerminalTransport();','Require(Interlocked.CompareExchange(ref terminalPreparedWriteAttempted, 1, 0) == 0);','outputStream.Write(original.Prepared, 0, original.Prepared.Length);','outputStream.Flush(); Interlocked.Exchange(ref terminalPreparedFlushReturned, 1);','Require(Interlocked.CompareExchange(ref terminalPreparedWritten, 1, 0) == 0);','while (Interlocked.CompareExchange(ref terminalAckReceived, 0, 0) != 1 || Interlocked.CompareExchange(ref terminalGuardianEof, 0, 0) != 1)','Require(Interlocked.CompareExchange(ref terminalPreparedWritten, 0, 0) == 1 && Interlocked.CompareExchange(ref terminalCommitWriteAttempted, 1, 0) == 0);','outputStream.Write(original.Commit, 0, original.Commit.Length);','outputStream.Flush(); Interlocked.Exchange(ref terminalCommitFlushReturned, 1);','Require(Interlocked.CompareExchange(ref terminalCommitWritten, 1, 0) == 0);','HalfCloseOriginalTerminalOutput(original);','WriteOriginalTerminalStdout(original);')
        Has $send 'new Thread(' 0;Has $send 'WaitForSingleObject(' 0
    },'SendOriginalTerminal();','/* actual terminal writer call omitted */'),
    @('terminal_halfclose_is_single_original_native_attempt_and_never_dispose_proof','terminalwriter',{
        param($s) $close=Method $s 'private void HalfCloseOriginalTerminalOutput('; $exit=Method $s 'internal bool PollTerminalExit('
        Order $close @('Object.ReferenceEquals(Thread.CurrentThread, original.Writer)','Interlocked.CompareExchange(ref terminalCommitWritten, 0, 0) == 1','terminalHalfCloseOriginal = outputValue;','Require(Interlocked.CompareExchange(ref terminalHalfCloseAttempted, 1, 0) == 0);','bool closed = CloseHandle(terminalHalfCloseOriginal);','terminalHalfCloseError = Marshal.GetLastWin32Error();','if (closed) Interlocked.Exchange(ref terminalHalfCloseSucceeded, 1);','Interlocked.Exchange(ref terminalHalfCloseReturned, 1);','if (!closed) throw new Win32Exception(','outputHandle.SetHandleAsInvalid();')
        Has $close 'CloseHandle(' 1;foreach($bad in @('.Dispose(','.Close(','outputStream.Write(','outputStream.Flush(')){Has $close $bad 0}
        Has $exit 'terminalHalfCloseOriginal == outputValue && Interlocked.CompareExchange(ref terminalHalfCloseAttempted, 0, 0) == 1 && Interlocked.CompareExchange(ref terminalHalfCloseReturned, 0, 0) == 1 && Interlocked.CompareExchange(ref terminalHalfCloseSucceeded, 0, 0) == 1'
    },'bool closed = CloseHandle(terminalHalfCloseOriginal);','bool closed = true;')
)
$expected=82
Assert-Peer ($rows.Count -eq 41) 'Historical plus directed terminal case inventory differs.'
$executed=0;$passed=0;$results=[Collections.Generic.List[object]]::new()
foreach($row in $rows){
    $name=$row[0];$selected=$blocks[$row[1]];$check=$row[2]
    $executed++; & $check $selected; $passed++
    $results.Add([pscustomobject]@{name=$name;variant='original_source';passed=$true})
    if($row.Count -eq 6){$mutated=Move-Tokens $selected $row[3] $row[4]}else{$mutated=Replace-Tokens $selected $row[3] $row[4]}
    $executed++;$rejected=$false
    try{& $check $mutated}catch{
        Assert-Peer ($_.Exception.Message.StartsWith('Producer entry source contract:',[StringComparison]::Ordinal)) ('Unexpected negative-control exception: '+$name)
        $rejected=$true
    }
    Assert-Peer $rejected ('The predicate accepted its negative mutation: '+$name)
    $passed++;$results.Add([pscustomobject]@{name=$name;variant='mutated_source_rejected';passed=$true})
}
Assert-Peer ($executed -eq $expected -and $passed -eq $expected) 'Fixed cases did not complete.'
$subjectAfter=Read-PeerSource $ProducerPath;$invocationAfter=Read-PeerSource $InvocationPath;$terminalAfter=Read-PeerSource $TerminalPath;$testAfter=Read-PeerSource $PSCommandPath
Assert-Peer ($subjectAfter.sha256 -ceq $subject.sha256 -and $invocationAfter.sha256 -ceq $invocationSource.sha256 -and $terminalAfter.sha256 -ceq $terminalSource.sha256 -and $testAfter.sha256 -ceq $testSource.sha256) 'Source/test bytes changed during inspection.'
[pscustomobject]@{
    format='stratus-producer-control-entry-source-contract-tests-v1'
    expected=$expected;executed=$executed;passed=$passed;failed=0
    powershell_version=$PSVersionTable.PSVersion.ToString();powershell_edition=$PSVersionTable.PSEdition
    producer_entry_source_sha256=$subject.sha256;test_sha256=$testSource.sha256;source_hashes_unchanged=$true
    producer_invocation_source_sha256_before=$invocationSource.sha256;producer_invocation_source_sha256_after=$invocationAfter.sha256
    producer_terminal_source_sha256_before=$terminalSource.sha256;producer_terminal_source_sha256_after=$terminalAfter.sha256
    source_tokens_and_directed_mutations_only=$true
    target_compiled=$false;target_loaded=$false;target_lifecycle_executed=$false;native_qualified=$false
    private_channel_provenance_proven=$false;guardian_identity_proven=$false;transport_qualified=$false
    original_deadline_native_behavior_proven=$false;complete_EOF_proven=$false;no_later_bytes_proven=$false
    compiler_job_handoff_acknowledged=$false;producer_resume_authorized=$false;full_build_accepted=$false
    cases=$results.ToArray()
}|ConvertTo-Json -Depth 6 -Compress
