[CmdletBinding()]
param([string] $RuntimePath)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if (-not $RuntimePath) { $RuntimePath=Join-Path $PSScriptRoot '..\Stratus.ParentStage.Runtime.cs' }
# SOURCE-TOKEN CONTRACTS ONLY, including negative source mutations. This does
# not compile or invoke any runtime member, thread, pipe, Job or native API.
# Token-balanced C# blocks are not semantic AST or native qualification.
$sourceBytes=[IO.File]::ReadAllBytes([IO.Path]::GetFullPath($RuntimePath))
$source=[Text.UTF8Encoding]::new($false,$true).GetString($sourceBytes)
$sha=[Security.Cryptography.SHA256]::Create()
try { $sourceHash=[BitConverter]::ToString($sha.ComputeHash($sourceBytes)).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
function Assert-ControlSource { param([bool] $Okay,[string] $Label) if (-not $Okay) { throw ('CONTROL source contract: '+$Label) } }
function Get-ControlTokens {
    param([string] $Text)
    $pattern=@'
//[^\r\n]* | /\*.*?\*/ | @"(?:""|[^"])*" | "(?:\\.|[^"\\])*" | '(?:\\.|[^'\\])*'
| [A-Za-z_][A-Za-z_0-9]* | [0-9]+ | == | != | >= | <= | \+\+ | && | \|\| | => | [^\s]
'@
    $tokens=[Collections.Generic.List[string]]::new()
    foreach ($match in [regex]::Matches($Text,$pattern,[Text.RegularExpressions.RegexOptions]::Singleline -bor [Text.RegularExpressions.RegexOptions]::IgnorePatternWhitespace)) {
        if (-not $match.Value.StartsWith('//') -and -not $match.Value.StartsWith('/*')) { $tokens.Add($match.Value) }
    }
    return ,$tokens.ToArray()
}
function Find-ControlSequence {
    param([string[]] $Tokens,[string] $Sequence)
    $wanted=Get-ControlTokens $Sequence
    Assert-ControlSource ($wanted.Count -gt 0) 'Empty predicate.'
    $found=[Collections.Generic.List[int]]::new()
    for ($index=0;$index -le $Tokens.Count-$wanted.Count;$index++) {
        $same=$true
        for ($part=0;$part -lt $wanted.Count;$part++) { if ($Tokens[$index+$part] -cne $wanted[$part]) { $same=$false;break } }
        if ($same) { $found.Add($index) }
    }
    return ,$found.ToArray()
}
function Require-ControlSequence {
    param([string[]] $Tokens,[string] $Sequence,[int] $Count=1)
    Assert-ControlSource ((Find-ControlSequence $Tokens $Sequence).Count -eq $Count) ('Token sequence differs: '+$Sequence)
}
function Get-ControlBlock {
    param([string[]] $Tokens,[string] $Signature)
    $found=Find-ControlSequence $Tokens $Signature
    Assert-ControlSource ($found.Count -eq 1) ('Missing/duplicate block: '+$Signature)
    $start=$found[0];$open=$start
    while ($open -lt $Tokens.Count -and $Tokens[$open] -cne '{') { $open++ }
    Assert-ControlSource ($open -lt $Tokens.Count) 'Opening brace missing.'
    $depth=0
    for ($index=$open;$index -lt $Tokens.Count;$index++) {
        if ($Tokens[$index] -ceq '{') { $depth++ } elseif ($Tokens[$index] -ceq '}') { $depth-- }
        if ($depth -eq 0) { return ,([string[]]$Tokens[$start..$index]) }
    }
    throw 'CONTROL source contract: Unbalanced block.'
}
function Require-ControlOrder {
    param([string[]] $Tokens,[string[]] $Sequences)
    $previous=-1
    foreach ($sequence in $Sequences) {
        $found=Find-ControlSequence $Tokens $sequence
        Assert-ControlSource ($found.Count -eq 1 -and $found[0] -gt $previous) ('Ordering differs: '+$sequence)
        $previous=$found[0]
    }
}
function Assert-NativeControlContract {
    param([string] $Name,[string] $Text)
    $tokens=Get-ControlTokens $Text
    $wire=Get-ControlBlock $tokens 'internal static class ControlWire'
    $io=Get-ControlBlock $tokens 'internal sealed class BoundedControlIo : IDisposable'
    $client=Get-ControlBlock $tokens 'internal sealed class PrivateControlClient : IDisposable'
    $endpoint=Get-ControlBlock $tokens 'public sealed class PrivateControlEndpoint : IDisposable'
    $launch=Get-ControlBlock $tokens 'public sealed class IndependentWatchdogLaunch : IDisposable'
    switch ($Name) {
        'fixed_frame_payload_sequence_bounds' { Require-ControlSequence $wire 'internal const int FrameMaximum=8192, PayloadMaximum=4096, RequestMaximum=1024;' }
        'bounded_cloned_strict_payload' {
            $fn=Get-ControlBlock $wire 'internal static byte[] Payload('
            Require-ControlSequence $fn 'bytes==null || bytes.Length<2 || bytes.Length>PayloadMaximum'
            Require-ControlOrder $fn @('byte[] copy=(byte[])bytes.Clone();','StrictJson.Validate(copy);','return copy;')
        }
        'frame_bounded_ascii_lf' {
            $fn=Get-ControlBlock $wire 'internal static string[] Decode('
            Require-ControlSequence $fn 'frame==null || frame.Length<2 || frame.Length>FrameMaximum || frame[frame.Length-1]!=10'
            Require-ControlSequence $fn 'for(int i=0;i<frame.Length-1;i++) if(frame[i]<32 || frame[i]>126)'
        }
        'exact_role_session_fields' {
            $fn=Get-ControlBlock $wire 'internal static string[] Decode('
            Require-ControlSequence $fn 'parts.Length!=(response ? 6 : 5) || parts[0]!=(response ? "STRATUS-CONTROL-RESPONSE/1" : "STRATUS-CONTROL-REQUEST/1") || parts[1]!=session'
        }
        'sequence_canonical_and_bounded' {
            $fn=Get-ControlBlock $wire 'internal static string[] Decode('
            Require-ControlSequence $fn 'sequence>RequestMaximum || sequence.ToString(CultureInfo.InvariantCulture)!=parts[2]'
            Require-ControlSequence $fn 'Int32.TryParse(parts[2],NumberStyles.None,CultureInfo.InvariantCulture,out sequence)'
        }
        'base64_exact_roundtrip' {
            $fn=Get-ControlBlock $wire 'internal static string[] Decode('
            Require-ControlSequence $fn 'if(Convert.ToBase64String(payload)!=encoded)'
            Require-ControlSequence $fn 'payload=Payload(payload); return parts;'
        }
        'read_bound_before_append' {
            $fn=Get-ControlBlock $io 'private void ReadLoop()'
            Require-ControlOrder $fn @('byte[] frame=new byte[ControlWire.FrameMaximum];','if(readUsed>=frame.Length)','frame[readUsed++]=(byte)value;')
        }
        'complete_duplicate_and_partial_reject' {
            $read=Get-ControlBlock $io 'private void ReadLoop()'
            $take=Get-ControlBlock $io 'internal byte[] Take()'
            Require-ControlSequence $read 'if(received!=null) throw new InvalidDataException("Multiple unconsumed private control frames.");'
            Require-ControlSequence $take 'if(received!=null && readUsed!=0)'
        }
        'known_idle_partial_reject' {
            $fn=Get-ControlBlock $io 'internal void AssertNoTrailing()'
            Require-ControlSequence $fn 'if(received!=null || readUsed!=0)'
        }
        'write_copy_one_inflight' {
            $fn=Get-ControlBlock $io 'internal void Enqueue('
            Require-ControlSequence $fn 'if(!started || pendingWrite!=null || writeBusy)'
            Require-ControlSequence $fn 'pendingWrite=(byte[])frame.Clone();'
        }
        'eof_and_faults_are_not_healthy' {
            $fn=Get-ControlBlock $io 'private void CheckLocked()'
            Require-ControlSequence $fn 'if(disposed || stopping || eof || drainRequested)'
            Require-ControlSequence $fn 'if(faults.Count!=0) throw new AggregateException(faults.ToArray());'
            Require-ControlSequence $io 'if(faults.Count<8) faults.Add(error);'
        }
        'original_1000ms_roundtrip' {
            Require-ControlSequence $client 'private const int RoundtripMilliseconds=1000;'
            $fn=Get-ControlBlock $client 'private void Deadline()'
            Require-ControlSequence $fn 'requestClock==null || requestClock.ElapsedMilliseconds>=RoundtripMilliseconds'
            Require-ControlSequence $client 'requestClock=Stopwatch.StartNew();' 1
        }
        'timer_covers_initial_ready_and_enqueue' {
            $fn=Get-ControlBlock $client 'internal void Begin('
            Require-ControlOrder $fn @('requestClock=Stopwatch.StartNew();','CheckHealth();','consumed=false;','RequireOwnerReady();','ControlWire.Payload(payload)','io.Enqueue(frame);')
            Require-ControlSequence $fn 'Deadline(); io.Enqueue(frame); Deadline();'
        }
        'original_native_ready_must_exist' {
            $fn=Get-ControlBlock $client 'private void RequireOwnerReady()'
            Require-ControlSequence $fn 'if(owner==null || owner.PollReady()==null)'
            Require-ControlSequence (Get-ControlBlock $client 'internal void Begin(') 'RequireOwnerReady();'
            Require-ControlSequence (Get-ControlBlock $client 'internal PrivateControlResponse Poll(') 'RequireOwnerReady();' 2
            Require-ControlSequence (Get-ControlBlock $client 'internal void Consume(') 'RequireOwnerReady();'
        }
        'idle_health_checks_trailing' {
            $fn=Get-ControlBlock $client 'private void CheckHealth()'
            Require-ControlSequence $fn 'io.AssertHealthy(); if(consumed) io.AssertNoTrailing();'
        }
        'one_outstanding_original_owner' {
            $fn=Get-ControlBlock $client 'internal void Begin('
            Require-ControlSequence $fn '!consumed || sequence>=ControlWire.RequestMaximum || (this.owner!=null && !Object.ReferenceEquals(this.owner,owner))'
            Require-ControlSequence (Get-ControlBlock $client 'internal PrivateControlResponse Poll(') 'consumed || !Object.ReferenceEquals(this.owner,owner)'
        }
        'fresh_nonce_and_exact_request_hash' {
            $fn=Get-ControlBlock $client 'internal void Begin('
            Require-ControlSequence $fn 'randomBytes=new byte[32]'
            Require-ControlSequence $fn 'using(RandomNumberGenerator random=RandomNumberGenerator.Create()) random.GetBytes(randomBytes);'
            Require-ControlSequence $fn 'requestHash=Files.Hash(frame); requestPayloadHash=Files.Hash(body);'
        }
        'response_all_request_bindings' {
            $fn=Get-ControlBlock $client 'internal PrivateControlResponse Poll('
            Require-ControlSequence $fn 'if(actualSequence!=sequence || parts[3]!=challenge || parts[4]!=requestHash)'
            Require-ControlSequence $fn 'ControlWire.Decode(frame,true,session,out actualSequence,out payload)'
        }
        'response_publication_live_clean_deadline' {
            $fn=Get-ControlBlock $client 'internal PrivateControlResponse Poll('
            Require-ControlSequence $fn 'RequireOwnerReady(); io.AssertNoTrailing(); Deadline(); response=new PrivateControlResponse'
            Require-ControlSequence $fn 'Deadline(); return response;' 2
        }
        'consume_exact_current_proof' {
            $fn=Get-ControlBlock $client 'internal void Consume('
            Require-ControlSequence $fn 'if(consumed || !Object.ReferenceEquals(response,proof))'
            Require-ControlSequence $fn 'if(io.Take()!=null)'
            Require-ControlSequence $fn 'io.AssertNoTrailing(); Deadline(); consumed=true;'
        }
        'readonly_response_bound_to_client' {
            $fn=Get-ControlBlock $tokens 'public sealed class PrivateControlResponse'
            Require-ControlSequence $fn 'private readonly PrivateControlClient client;'
            Require-ControlSequence $fn 'public readonly string PayloadText,RequestSha256,RequestPayloadSha256,Challenge;'
            Require-ControlSequence $fn 'public void Consume() { client.Consume(this); }'
        }
        'endpoint_exact_pipes_noninheritable' {
            $fn=Get-ControlBlock $endpoint 'private PrivateControlEndpoint('
            Require-ControlSequence $fn 'input==output || GetFileType(input)!=3 || GetFileType(output)!=3'
            Require-ControlOrder $fn @('SetHandleInformation(input,1,0)','new AnonymousPipeClientStream(PipeDirection.In,new SafePipeHandle(input,true))','io.Start();')
            Require-ControlSequence $fn 'SetHandleInformation(output,1,0)'
        }
        'endpoint_handle_syntax' {
            $fn=Get-ControlBlock $endpoint 'private static IntPtr ParseHandle('
            Require-ControlSequence $fn 'Int64.TryParse(value,NumberStyles.None,CultureInfo.InvariantCulture,out parsed)'
            Require-ControlSequence $fn 'parsed.ToString(CultureInfo.InvariantCulture)!=value || (IntPtr.Size==4 && parsed>Int32.MaxValue)'
        }
        'endpoint_sequence_and_original_request' {
            Require-ControlSequence (Get-ControlBlock $endpoint 'public PrivateControlRequest PollRequest()') 'if(actualSequence!=sequence+1)'
            Require-ControlSequence (Get-ControlBlock $endpoint 'public void Respond(') 'request==null || !Object.ReferenceEquals(pending,request) || !Object.ReferenceEquals(request.Owner,this)'
        }
        'endpoint_response_exact_request' {
            $fn=Get-ControlBlock $endpoint 'public void Respond('
            Require-ControlSequence $fn 'ControlWire.Response(session,request.Sequence,request.Challenge,request.Sha256,jsonPayload)'
            Require-ControlSequence $fn 'io.Enqueue(frame); pending=null;'
        }
        'client_owns_exact_pipe_pair' {
            $fn=Get-ControlBlock $client 'internal PrivateControlClient('
            Require-ControlSequence $fn 'toChild=new AnonymousPipeServerStream(PipeDirection.Out,HandleInheritability.Inheritable);'
            Require-ControlSequence $fn 'fromChild=new AnonymousPipeServerStream(PipeDirection.In,HandleInheritability.Inheritable);'
            Require-ControlSequence $client 'toChild.DisposeLocalCopyOfClientHandle(); fromChild.DisposeLocalCopyOfClientHandle(); io.Start();'
        }
        'factory_adds_only_two_control_handles' {
            $fn=Get-ControlBlock $launch 'private IndependentWatchdogLaunch('
            Require-ControlSequence $fn 'handles.Add(control.ChildInputHandle); handles.Add(control.ChildOutputHandle);'
            Require-ControlSequence $fn 'command.Append(" -ControlInputHandle ").Append(NativeChild.Quote(control.ChildInputHandleText));'
            Require-ControlSequence $fn 'command.Append(" -ControlOutputHandle ").Append(NativeChild.Quote(control.ChildOutputHandleText));'
            Require-ControlSequence $fn 'handles.Add(control.' 2
        }
        'control_factory_no_breakaway' {
            $fn=Get-ControlBlock $launch 'private IndependentWatchdogLaunch('
            Require-ControlSequence $fn 'uint creationFlags=withControl ? 0x08080404u : 0x09080404u;'
            Require-ControlSequence $fn 'AssertIdentity(); identityEstablished=true; AssertNoJob();'
            Require-ControlSequence $fn '"-ExecutionPolicy"' 0
        }
        'control_dispose_precedes_capability_release' {
            $fn=Get-ControlBlock $launch 'public void Dispose()'
            Require-ControlOrder $fn @('if(control!=null) control.Dispose();','Exception failure=null;',
                'foreach(IDisposable item in new IDisposable[] {input,output,error,outputBytes})',
                'foreach(HeldFile file in held)','foreach(IntPtr handle in new IntPtr[] {thread,process})')
        }
        'io_shutdown_failure_retains_streams' {
            $fn=Get-ControlBlock $io 'public void Dispose()'
            Require-ControlOrder $fn @('stopping=true;','foreach(Thread thread in new Thread[] {reader,writer})',
                'throw new TimeoutException("Private control IO shutdown is unproved; handles retained.");','foreach(Stream stream in new Stream[] {input,output})')
            foreach ($token in @('Kill','TerminateProcess','TerminateJobObject','Thread.Abort','CloseHandle')) { Require-ControlSequence $fn $token 0 }
        }
        'client_concurrency_rejected_without_wait' {
            $fn=Get-ControlBlock $client 'private void EnterOperation()'
            Require-ControlSequence $fn 'if(Interlocked.CompareExchange(ref operation,1,0)!=0)'
            Require-ControlSequence $fn 'io.Fail(error); throw error;'
            Require-ControlSequence (Get-ControlBlock $client 'private void ExitOperation()') 'Interlocked.Exchange(ref operation,0);'
            Require-ControlSequence $fn 'Monitor.Wait' 0
        }
        'endpoint_concurrency_rejected_without_wait' {
            $fn=Get-ControlBlock $endpoint 'private void EnterOperation()'
            Require-ControlSequence $fn 'if(Interlocked.CompareExchange(ref operation,1,0)!=0)'
            Require-ControlSequence $fn 'io.Fail(error); throw error;'
            Require-ControlSequence (Get-ControlBlock $endpoint 'private void ExitOperation()') 'Interlocked.Exchange(ref operation,0);'
        }
        'every_client_state_operation_serialized' {
            foreach ($signature in @('internal void AssertHealthy()','internal void Begin(','internal PrivateControlResponse Poll(','internal void Consume(','public void Dispose()')) {
                $fn=Get-ControlBlock $client $signature
                Require-ControlSequence $fn 'EnterOperation(); try'
                Require-ControlSequence $fn 'finally { ExitOperation(); }'
            }
            Require-ControlSequence (Get-ControlBlock $client 'private void Deadline()') 'CheckHealth();'
        }
        'every_endpoint_state_operation_serialized' {
            foreach ($signature in @('public void AssertHealthy()','public PrivateControlRequest PollRequest()','public void Respond(','public void Dispose()')) {
                $fn=Get-ControlBlock $endpoint $signature
                Require-ControlSequence $fn 'EnterOperation(); try'
                Require-ControlSequence $fn 'finally { ExitOperation(); }'
            }
        }
        'partial_endpoint_start_retains_io_on_failure' {
            $fn=Get-ControlBlock $endpoint 'private PrivateControlEndpoint('
            Require-ControlSequence $fn 'if(io!=null)'
            Require-ControlSequence $fn 'io.Fail(failure); try { io.Dispose(); } catch(Exception cleanup) { failure=Files.Combine(failure,cleanup); }'
            Require-ControlSequence $fn 'foreach(Stream stream in new Stream[] {read,write})'
            Require-ControlSequence $fn 'throw failure;'
        }
        default { throw 'Unknown CONTROL source predicate.' }
    }
}
$rows=@(
    @('fixed_frame_payload_sequence_bounds','PayloadMaximum=4096','PayloadMaximum=65536'),
    @('bounded_cloned_strict_payload','byte[] copy=(byte[])bytes.Clone(); StrictJson.Validate(copy); return copy;','byte[] copy=bytes; StrictJson.Validate(copy); return copy;'),
    @('frame_bounded_ascii_lf','for(int i=0;i<frame.Length-1;i++) if(frame[i]<32 || frame[i]>126)','for(int i=0;i<frame.Length-1;i++) if(false)'),
    @('exact_role_session_fields','parts.Length!=(response ? 6 : 5)','parts.Length<5'),
    @('sequence_canonical_and_bounded','sequence.ToString(CultureInfo.InvariantCulture)!=parts[2]','false'),
    @('base64_exact_roundtrip','if(Convert.ToBase64String(payload)!=encoded)','if(false)'),
    @('read_bound_before_append','if(readUsed>=frame.Length)','if(false)'),
    @('complete_duplicate_and_partial_reject','if(received!=null && readUsed!=0)','if(false)'),
    @('known_idle_partial_reject','if(received!=null || readUsed!=0)','if(received!=null)'),
    @('write_copy_one_inflight','pendingWrite=(byte[])frame.Clone();','pendingWrite=frame;'),
    @('eof_and_faults_are_not_healthy','if(disposed || stopping || eof || drainRequested)','if(disposed || stopping || drainRequested)'),
    @('original_1000ms_roundtrip','private const int RoundtripMilliseconds=1000;','private const int RoundtripMilliseconds=30000;'),
    @('timer_covers_initial_ready_and_enqueue','requestClock=Stopwatch.StartNew(); CheckHealth();','CheckHealth(); requestClock=Stopwatch.StartNew();'),
    @('original_native_ready_must_exist','if(owner==null || owner.PollReady()==null)','if(owner==null)'),
    @('idle_health_checks_trailing','io.AssertHealthy(); if(consumed) io.AssertNoTrailing();','io.AssertHealthy();'),
    @('one_outstanding_original_owner','!consumed || sequence>=ControlWire.RequestMaximum','sequence>=ControlWire.RequestMaximum'),
    @('fresh_nonce_and_exact_request_hash','randomBytes=new byte[32]','randomBytes=new byte[8]'),
    @('response_all_request_bindings','actualSequence!=sequence || parts[3]!=challenge || parts[4]!=requestHash','actualSequence!=sequence || parts[3]!=challenge'),
    @('response_publication_live_clean_deadline','RequireOwnerReady(); io.AssertNoTrailing(); Deadline();','io.AssertNoTrailing(); Deadline();'),
    @('consume_exact_current_proof','if(consumed || !Object.ReferenceEquals(response,proof))','if(consumed)'),
    @('readonly_response_bound_to_client','private readonly PrivateControlClient client;','private PrivateControlClient client;'),
    @('endpoint_exact_pipes_noninheritable','SetHandleInformation(input,1,0)','SetHandleInformation(input,1,1)'),
    @('endpoint_handle_syntax','parsed.ToString(CultureInfo.InvariantCulture)!=value','false'),
    @('endpoint_sequence_and_original_request','if(actualSequence!=sequence+1)','if(actualSequence<sequence)'),
    @('endpoint_response_exact_request','ControlWire.Response(session,request.Sequence,request.Challenge,request.Sha256,jsonPayload)','ControlWire.Response(session,request.Sequence,request.Challenge,"",jsonPayload)'),
    @('client_owns_exact_pipe_pair','toChild.DisposeLocalCopyOfClientHandle(); fromChild.DisposeLocalCopyOfClientHandle(); io.Start();','toChild.DisposeLocalCopyOfClientHandle(); io.Start();'),
    @('factory_adds_only_two_control_handles','handles.Add(control.ChildInputHandle); handles.Add(control.ChildOutputHandle);','handles.Add(control.ChildInputHandle);'),
    @('control_factory_no_breakaway','uint creationFlags=withControl ? 0x08080404u : 0x09080404u;','uint creationFlags=0x09080404u;'),
    @('control_dispose_precedes_capability_release','if(control!=null) control.Dispose();',''),
    @('io_shutdown_failure_retains_streams','throw new TimeoutException("Private control IO shutdown is unproved; handles retained.");','return;'),
    @('client_concurrency_rejected_without_wait','if(Interlocked.CompareExchange(ref operation,1,0)!=0)','if(operation!=0)'),
    @('endpoint_concurrency_rejected_without_wait','if(Interlocked.CompareExchange(ref operation,1,0)!=0)','if(operation!=0)'),
    @('every_client_state_operation_serialized','EnterOperation();',''),
    @('every_endpoint_state_operation_serialized','finally { ExitOperation(); }','finally {}'),
    @('partial_endpoint_start_retains_io_on_failure','io.Fail(failure);','')
)
$results=[Collections.Generic.List[object]]::new()
foreach ($row in $rows) {
    Assert-NativeControlContract $row[0] $source
    $results.Add([pscustomobject]@{name=$row[0];negative_control=$false;passed=$true})
    Assert-ControlSource ($source.Contains($row[1])) ('Missing mutation target: '+$row[0])
    $failure=$null
    try { Assert-NativeControlContract $row[0] $source.Replace($row[1],$row[2]) } catch { $failure=$_.Exception.Message }
    Assert-ControlSource ($null -ne $failure -and $failure.StartsWith('CONTROL source contract:')) ('Negative survived: '+$row[0])
    $results.Add([pscustomobject]@{name=($row[0]+' removal rejected');negative_control=$true;passed=$true})
}
$expectedCases=70
Assert-ControlSource ($results.Count -eq $expectedCases) 'Fixed case count differs.'
[pscustomobject]@{suite='parent-control-native-source-contracts';expected=$expectedCases;executed=$results.Count;passed=$results.Count;failed=0
    runtime_sha256=$sourceHash;native_executed=$false;runtime_compiled=$false;runtime_members_invoked=$false
    semantic_csharp_ast=$false;graceful_endpoint_eof_qualified=$false;full_protocol_accepted=$false;results=@($results.ToArray())} | ConvertTo-Json -Depth 5 -Compress
