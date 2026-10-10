[CmdletBinding()]
param([string] $RuntimePath)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if (-not $RuntimePath) { $RuntimePath=Join-Path $PSScriptRoot '..\Stratus.ParentStage.Runtime.cs' }
# SOURCE-TOKEN CONTRACTS ONLY. No runtime compilation, member invocation,
# pipe, thread, native API, process or gate body is executed by this suite.
# Balanced comment/literal-aware tokens are not semantic C# AST or behavioral
# scheduling/EOF qualification. Negative controls mutate only in-memory text.
$sourceBytes=[IO.File]::ReadAllBytes([IO.Path]::GetFullPath($RuntimePath))
$source=[Text.UTF8Encoding]::new($false,$true).GetString($sourceBytes)
$sha=[Security.Cryptography.SHA256]::Create()
try { $sourceHash=[BitConverter]::ToString($sha.ComputeHash($sourceBytes)).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
function Assert-DrainSource {
    param([bool] $Okay,[string] $Label)
    if (-not $Okay) { throw ('DRAIN source contract: '+$Label) }
}
function Get-DrainTokens {
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
function Find-DrainSequence {
    param([string[]] $Tokens,[string] $Sequence)
    $wanted=Get-DrainTokens $Sequence
    Assert-DrainSource ($wanted.Count -gt 0) 'Empty predicate.'
    $found=[Collections.Generic.List[int]]::new()
    for ($index=0;$index -le $Tokens.Count-$wanted.Count;$index++) {
        $same=$true
        for ($part=0;$part -lt $wanted.Count;$part++) { if ($Tokens[$index+$part] -cne $wanted[$part]) { $same=$false;break } }
        if ($same) { $found.Add($index) }
    }
    return ,$found.ToArray()
}
function Require-DrainSequence {
    param([string[]] $Tokens,[string] $Sequence,[int] $Count=1)
    Assert-DrainSource ((Find-DrainSequence $Tokens $Sequence).Count -eq $Count) ('Token sequence differs: '+$Sequence)
}
function Get-DrainBlock {
    param([string[]] $Tokens,[string] $Signature)
    $found=Find-DrainSequence $Tokens $Signature
    Assert-DrainSource ($found.Count -eq 1) ('Missing/duplicate block: '+$Signature)
    $start=$found[0];$open=$start
    while ($open -lt $Tokens.Count -and $Tokens[$open] -cne '{') { $open++ }
    Assert-DrainSource ($open -lt $Tokens.Count) 'Opening brace missing.'
    $depth=0
    for ($index=$open;$index -lt $Tokens.Count;$index++) {
        if ($Tokens[$index] -ceq '{') { $depth++ } elseif ($Tokens[$index] -ceq '}') { $depth-- }
        if ($depth -eq 0) { return ,([string[]]$Tokens[$start..$index]) }
    }
    throw 'DRAIN source contract: Unbalanced block.'
}
function Require-DrainOrder {
    param([string[]] $Tokens,[string[]] $Sequences)
    $previous=-1
    foreach ($sequence in $Sequences) {
        $found=Find-DrainSequence $Tokens $sequence
        Assert-DrainSource ($found.Count -eq 1 -and $found[0] -gt $previous) ('Ordering differs: '+$sequence)
        $previous=$found[0]
    }
}
function Assert-ControlDrainContract {
    param([string] $Name,[string] $Text)
    $tokens=Get-DrainTokens $Text
    $io=Get-DrainBlock $tokens 'internal sealed class BoundedControlIo : IDisposable'
    $client=Get-DrainBlock $tokens 'internal sealed class PrivateControlClient : IDisposable'
    $endpoint=Get-DrainBlock $tokens 'public sealed class PrivateControlEndpoint : IDisposable'
    $launch=Get-DrainBlock $tokens 'public sealed class IndependentWatchdogLaunch : IDisposable'
    switch ($Name) {
        'normal_io_rejects_drain_and_eof' {
            Require-DrainSequence (Get-DrainBlock $io 'private void CheckLocked()') 'if(disposed || stopping || eof || drainRequested)'
        }
        'drain_requires_live_started_io' {
            Require-DrainSequence (Get-DrainBlock $io 'private void CheckDrainLocked()') 'if(disposed || stopping || !started || !drainRequested)'
        }
        'drain_rejects_latched_faults' {
            Require-DrainSequence (Get-DrainBlock $io 'private void CheckDrainLocked()') 'if(faults.Count!=0) throw new AggregateException(faults.ToArray());'
        }
        'drain_cannot_discard_bytes' {
            Require-DrainSequence (Get-DrainBlock $io 'private void CheckDrainLocked()') 'if(received!=null || readUsed!=0)'
        }
        'begin_is_single_use_and_latches_before_checks' {
            $fn=Get-DrainBlock $io 'internal void BeginDrain('
            Require-DrainOrder $fn @('if(drainRequested)','drainRequested=true; this.closeFirst=closeFirst;','CheckDrainLocked();','if(eof)')
        }
        'preterminal_eof_not_promoted' {
            Require-DrainSequence (Get-DrainBlock $io 'internal void BeginDrain(') 'if(eof) throw new EndOfStreamException("Pre-terminal private control EOF cannot become successful drain.");'
        }
        'inner_begin_failure_latched_without_wrapper' {
            Require-DrainSequence (Get-DrainBlock $io 'internal void BeginDrain(') 'catch(Exception error) { Fail(error); throw; }'
        }
        'inner_poll_failure_latched_without_wrapper' {
            Require-DrainSequence (Get-DrainBlock $io 'internal bool PollDrain()') 'catch(Exception error) { Fail(error); throw; }'
        }
        'pending_write_completes_before_drain_started' {
            Require-DrainSequence (Get-DrainBlock $io 'internal bool PollDrain()') 'if(!drainStarted) { if(pendingWrite!=null || writeBusy) return false; drainStarted=true; }'
        }
        'close_first_or_peer_eof_only' {
            Require-DrainSequence (Get-DrainBlock $io 'internal bool PollDrain()') 'if(!closeWritesRequested && (closeFirst || eof)) { closeWritesRequested=true; Monitor.PulseAll(sync); }'
        }
        'both_eof_and_writer_close_required' {
            Require-DrainSequence (Get-DrainBlock $io 'internal bool PollDrain()') 'if(!eof || !writeClosed) return false;'
        }
        'join_zero_observation_only' {
            $fn=Get-DrainBlock $io 'internal bool PollDrain()'
            Require-DrainSequence $fn 'if(reader==null || writer==null || !reader.Join(0) || !writer.Join(0)) return false;'
        }
        'post_join_facts_rechecked_before_completion' {
            Require-DrainSequence (Get-DrainBlock $io 'internal bool PollDrain()') 'lock(sync) { CheckDrainLocked(); if(!eof || !writeClosed || pendingWrite!=null || writeBusy) return false; drainComplete=true; return true; }'
        }
        'poll_has_no_blocking_io_or_wait' {
            $fn=Get-DrainBlock $io 'internal bool PollDrain()'
            foreach ($forbidden in @('Thread.Sleep(','Monitor.Wait(','output.Dispose(','input.Dispose(','output.Flush(','output.Write(','input.Read','Join(2000)')) { Require-DrainSequence $fn $forbidden 0 }
        }
        'reader_rejects_bytes_after_drain' {
            $fn=Get-DrainBlock $io 'private void ReadLoop()'
            Require-DrainOrder $fn @('if(drainRequested) throw new InvalidDataException("Private control received bytes after terminal drain began.");','if(readUsed>=frame.Length)','frame[readUsed++]=(byte)value;')
        }
        'writer_wakes_for_half_close' {
            Require-DrainSequence (Get-DrainBlock $io 'private void WriteLoop()') 'while(pendingWrite==null && !closeWritesRequested && !stopping && faults.Count==0) Monitor.Wait(sync);'
        }
        'writer_close_requires_idle_started_drain' {
            Require-DrainSequence (Get-DrainBlock $io 'private void WriteLoop()') 'if(pendingWrite!=null || writeBusy || !drainStarted)'
        }
        'worker_flush_close_then_publish' {
            Require-DrainSequence (Get-DrainBlock $io 'private void WriteLoop()') 'if(close) { output.Flush(); output.Dispose(); lock(sync) { writeClosed=true; Monitor.PulseAll(sync); } return; }'
        }
        'writer_fault_not_clean_close' {
            Require-DrainSequence (Get-DrainBlock $io 'private void WriteLoop()') 'catch(Exception error) { Fail(error); }'
        }
        'unproved_dispose_cannot_stop_workers' {
            Require-DrainSequence (Get-DrainBlock $io 'public void Dispose()') 'if(drainRequested && !drainComplete) throw new InvalidOperationException("Unproved private control drain retains IO ownership."); stopping=true;'
        }
        'client_terminal_latches_before_admission' {
            Require-DrainOrder (Get-DrainBlock $client 'internal void EnterTerminal(') @('if(terminal)','terminal=true;','if(disposed || !consumed || (this.owner!=null && !Object.ReferenceEquals(this.owner,owner)))','this.owner=owner; io.BeginDrain(false);')
        }
        'client_waits_for_peer_close' {
            Require-DrainSequence (Get-DrainBlock $client 'internal void EnterTerminal(') 'this.owner=owner; io.BeginDrain(false);'
        }
        'client_drain_exact_terminal_owner' {
            Require-DrainSequence (Get-DrainBlock $client 'internal bool PollTerminalDrain(') 'if(disposed || !terminal || !Object.ReferenceEquals(this.owner,owner))'
        }
        'client_no_return_to_live' {
            Require-DrainSequence (Get-DrainBlock $client 'private void CheckHealth()') 'if(terminal) throw new InvalidOperationException("Private control live operations are unavailable in terminal mode.");'
        }
        'client_new_operations_serialized_and_fault_latched' {
            foreach ($signature in @('internal void EnterTerminal(','internal bool PollTerminalDrain(')) {
                $fn=Get-DrainBlock $client $signature
                Require-DrainSequence $fn 'EnterOperation(); try'
                Require-DrainSequence $fn 'catch(Exception error) { io.Fail(error); throw; }'
                Require-DrainSequence $fn 'finally { ExitOperation(); }'
            }
        }
        'endpoint_owns_one_original_cleanup_clock' {
            Require-DrainSequence $endpoint 'drainClock=Stopwatch.StartNew();' 1
            Require-DrainSequence (Get-DrainBlock $endpoint 'private void AssertDrainDeadline()') 'if(drainClock==null || drainClock.ElapsedMilliseconds>=30000)'
        }
        'endpoint_latches_and_preserves_pending_request' {
            Require-DrainOrder (Get-DrainBlock $endpoint 'public void BeginTerminalDrain()') @('if(terminal)','drainClock=Stopwatch.StartNew(); terminal=true;','if(disposed || pending!=null)','io.BeginDrain(true); AssertDrainDeadline();')
        }
        'endpoint_closes_first' {
            Require-DrainSequence (Get-DrainBlock $endpoint 'public void BeginTerminalDrain()') 'io.BeginDrain(true); AssertDrainDeadline();'
        }
        'endpoint_poll_deadline_before_and_after' {
            Require-DrainSequence (Get-DrainBlock $endpoint 'public bool PollTerminalDrain()') 'AssertDrainDeadline(); bool complete=io.PollDrain(); AssertDrainDeadline(); return complete;'
        }
        'endpoint_no_live_service_after_terminal' {
            Require-DrainSequence (Get-DrainBlock $endpoint 'private void CheckHealth()') 'if(terminal) throw new InvalidOperationException("Private control endpoint cannot return to live service after terminal admission.");'
        }
        'endpoint_new_operations_serialized_and_fault_latched' {
            foreach ($signature in @('public void BeginTerminalDrain()','public bool PollTerminalDrain()')) {
                $fn=Get-DrainBlock $endpoint $signature
                Require-DrainSequence $fn 'EnterOperation(); try'
                Require-DrainSequence $fn 'catch(Exception error) { io.Fail(error); throw; }'
                Require-DrainSequence $fn 'finally { ExitOperation(); }'
            }
        }
        'bootstrap_terminal_latch_precedes_control' {
            Require-DrainSequence (Get-DrainBlock $launch 'public void BeginTerminalObservation()') 'terminalObservationStarted=true; if(control!=null) control.EnterTerminal(this); AssertTerminalDeadline();'
        }
        'bootstrap_drain_serviced_before_alive_null' {
            Require-DrainOrder (Get-DrainBlock $launch 'public TerminalExitProof PollTerminalExit()') @('bool controlDrained=control==null || control.PollTerminalDrain(this);','bool alive=IsAlive();','if(alive) return null;')
        }
        'bootstrap_second_drain_after_native_exit_checks' {
            Require-DrainSequence (Get-DrainBlock $launch 'public TerminalExitProof PollTerminalExit()') 'AssertIdentity(); AssertNoJob(); ThrowFaults(); controlDrained=control==null || control.PollTerminalDrain(this); lock(collectionGate)'
        }
        'bootstrap_drain_guards_cached_and_new_proof' {
            Require-DrainOrder (Get-DrainBlock $launch 'public TerminalExitProof PollTerminalExit()') @('if(!outputEof || !errorEof || !controlDrained) return null;','if(terminalProof!=null)','terminalExitedAt=exitedAt; terminalProofReady=true;','terminalProof=TerminalExitProof.FromOwnedObservation(this);')
        }
        'bootstrap_original_deadline_and_exit_zero_retained' {
            Require-DrainSequence (Get-DrainBlock $launch 'private void AssertTerminalDeadline()') 'if(terminalClock==null || terminalClock.ElapsedMilliseconds>=30000)'
            Require-DrainSequence (Get-DrainBlock $launch 'public TerminalExitProof PollTerminalExit()') 'if(exitCode!=0) throw new InvalidDataException("Independent watchdog did not exit with code zero.");'
        }
        'transport_cannot_publish_native_acceptance' {
            foreach ($block in @($io,$client,$endpoint)) {
                foreach ($forbidden in @('terminalProofReady=true','TerminalExitProof.FromOwnedObservation(','TerminateProcess(','StopAndProve(')) { Require-DrainSequence $block $forbidden 0 }
            }
        }
        default { throw 'DRAIN source contract: Unknown predicate.' }
    }
}
$rows=@(
    @('normal_io_rejects_drain_and_eof','if(disposed || stopping || eof || drainRequested)','if(disposed || stopping || eof)'),
    @('drain_requires_live_started_io','if(disposed || stopping || !started || !drainRequested)','if(disposed || stopping)'),
    @('drain_rejects_latched_faults','if(faults.Count!=0) throw new AggregateException(faults.ToArray());',''),
    @('drain_cannot_discard_bytes','if(received!=null || readUsed!=0)','if(received!=null)'),
    @('begin_is_single_use_and_latches_before_checks','drainRequested=true; this.closeFirst=closeFirst;','this.closeFirst=closeFirst;'),
    @('preterminal_eof_not_promoted','if(eof) throw new EndOfStreamException("Pre-terminal private control EOF cannot become successful drain.");',''),
    @('inner_begin_failure_latched_without_wrapper','catch(Exception error) { Fail(error); throw; }','catch(Exception error) { throw; }'),
    @('inner_poll_failure_latched_without_wrapper','catch(Exception error) { Fail(error); throw; }','catch(Exception error) { throw; }'),
    @('pending_write_completes_before_drain_started','if(pendingWrite!=null || writeBusy) return false;','if(pendingWrite!=null) return false;'),
    @('close_first_or_peer_eof_only','if(!closeWritesRequested && (closeFirst || eof))','if(!closeWritesRequested)'),
    @('both_eof_and_writer_close_required','if(!eof || !writeClosed) return false;','if(!eof) return false;'),
    @('join_zero_observation_only','!reader.Join(0) || !writer.Join(0)','!reader.Join(2000) || !writer.Join(2000)'),
    @('post_join_facts_rechecked_before_completion','if(!eof || !writeClosed || pendingWrite!=null || writeBusy) return false;','if(!eof || !writeClosed) return false;'),
    @('poll_has_no_blocking_io_or_wait','internal bool PollDrain() {','internal bool PollDrain() { Thread.Sleep(1);'),
    @('reader_rejects_bytes_after_drain','if(drainRequested) throw new InvalidDataException("Private control received bytes after terminal drain began.");',''),
    @('writer_wakes_for_half_close','pendingWrite==null && !closeWritesRequested && !stopping','pendingWrite==null && !stopping'),
    @('writer_close_requires_idle_started_drain','if(pendingWrite!=null || writeBusy || !drainStarted)','if(!drainStarted)'),
    @('worker_flush_close_then_publish','output.Flush(); output.Dispose();','output.Flush();'),
    @('writer_fault_not_clean_close','catch(Exception error) { Fail(error); }','catch(Exception error) {}'),
    @('unproved_dispose_cannot_stop_workers','if(drainRequested && !drainComplete)','if(false)'),
    @('client_terminal_latches_before_admission','terminal=true;','terminal=false;'),
    @('client_waits_for_peer_close','this.owner=owner; io.BeginDrain(false);','this.owner=owner; io.BeginDrain(true);'),
    @('client_drain_exact_terminal_owner','if(disposed || !terminal || !Object.ReferenceEquals(this.owner,owner))','if(disposed || !terminal)'),
    @('client_no_return_to_live','if(terminal) throw new InvalidOperationException("Private control live operations are unavailable in terminal mode.");',''),
    @('client_new_operations_serialized_and_fault_latched','EnterOperation();',''),
    @('endpoint_owns_one_original_cleanup_clock','drainClock.ElapsedMilliseconds>=30000','drainClock.ElapsedMilliseconds>=60000'),
    @('endpoint_latches_and_preserves_pending_request','if(disposed || pending!=null)','if(disposed)'),
    @('endpoint_closes_first','io.BeginDrain(true); AssertDrainDeadline();','io.BeginDrain(false); AssertDrainDeadline();'),
    @('endpoint_poll_deadline_before_and_after','AssertDrainDeadline(); bool complete=io.PollDrain(); AssertDrainDeadline(); return complete;','AssertDrainDeadline(); bool complete=io.PollDrain(); return complete;'),
    @('endpoint_no_live_service_after_terminal','if(terminal) throw new InvalidOperationException("Private control endpoint cannot return to live service after terminal admission.");',''),
    @('endpoint_new_operations_serialized_and_fault_latched','finally { ExitOperation(); }','finally {}'),
    @('bootstrap_terminal_latch_precedes_control','terminalObservationStarted=true;',''),
    @('bootstrap_drain_serviced_before_alive_null','bool controlDrained=control==null || control.PollTerminalDrain(this);','bool controlDrained=true;'),
    @('bootstrap_second_drain_after_native_exit_checks','controlDrained=control==null || control.PollTerminalDrain(this);','controlDrained=true;'),
    @('bootstrap_drain_guards_cached_and_new_proof','if(!outputEof || !errorEof || !controlDrained) return null;','if(!outputEof || !errorEof) return null;'),
    @('bootstrap_original_deadline_and_exit_zero_retained','if(exitCode!=0) throw new InvalidDataException("Independent watchdog did not exit with code zero.");',''),
    @('transport_cannot_publish_native_acceptance','drainComplete=true; return true;','drainComplete=true; terminalProofReady=true; return true;')
)
$results=[Collections.Generic.List[object]]::new()
foreach ($row in $rows) {
    Assert-ControlDrainContract $row[0] $source
    $results.Add([pscustomobject]@{name=$row[0];negative_control=$false;passed=$true})
    Assert-DrainSource ($source.Contains($row[1])) ('Missing mutation target: '+$row[0])
    $failure=$null
    try { Assert-ControlDrainContract $row[0] $source.Replace($row[1],$row[2]) } catch { $failure=$_.Exception.Message }
    Assert-DrainSource ($null -ne $failure -and $failure.StartsWith('DRAIN source contract:')) ('Negative survived: '+$row[0])
    $results.Add([pscustomobject]@{name=($row[0]+' mutation rejected');negative_control=$true;passed=$true})
}
$expectedCases=74
Assert-DrainSource ($results.Count -eq $expectedCases) 'Fixed case count differs.'
[pscustomobject]@{suite='parent-control-drain-source-contracts';expected=$expectedCases;executed=$results.Count;passed=$results.Count;failed=0
    runtime_sha256=$sourceHash;native_executed=$false;runtime_compiled=$false;runtime_members_invoked=$false
    pipes_or_threads_executed=$false;semantic_csharp_ast=$false;native_drain_qualified=$false;full_protocol_accepted=$false
    results=@($results.ToArray())} | ConvertTo-Json -Depth 5 -Compress
