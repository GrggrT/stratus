[CmdletBinding()]
param([string] $RuntimePath)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if (-not $RuntimePath) { $RuntimePath=Join-Path $PSScriptRoot '..\Stratus.ParentStage.Runtime.cs' }
# Source-token contracts and in-memory mutation negatives ONLY. No runtime is
# compiled, loaded or invoked. No native member, process, pipe or thread runs.
# This checks the existing per-child Job factory, not session-C ownership or
# G/T takeover. Balanced tokens are not semantic C# or OS lifecycle evidence.
$bytes=[IO.File]::ReadAllBytes([IO.Path]::GetFullPath($RuntimePath))
$source=[Text.UTF8Encoding]::new($false,$true).GetString($bytes)
$sha=[Security.Cryptography.SHA256]::Create()
try { $sourceHash=[BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
function Assert-BirthSource {
    param([bool] $Okay,[string] $Message)
    if (-not $Okay) { throw ('BIRTH source contract: '+$Message) }
}
function Get-BirthTokens {
    param([string] $Text)
    $pattern=@'
//[^\r\n]* | /\*.*?\*/ | @"(?:""|[^"])*" | "(?:\\.|[^"\\])*" | '(?:\\.|[^'\\])*'
| [A-Za-z_][A-Za-z_0-9]* | [0-9]+ | == | != | >= | <= | \+\+ | && | \|\| | => | [^\s]
'@
    $result=[Collections.Generic.List[string]]::new()
    foreach ($match in [regex]::Matches($Text,$pattern,[Text.RegularExpressions.RegexOptions]::Singleline -bor [Text.RegularExpressions.RegexOptions]::IgnorePatternWhitespace)) {
        if (-not $match.Value.StartsWith('//') -and -not $match.Value.StartsWith('/*')) { $result.Add($match.Value) }
    }
    return ,$result.ToArray()
}
function Find-BirthSequence {
    param([string[]] $Tokens,[string] $Sequence)
    $wanted=Get-BirthTokens $Sequence
    Assert-BirthSource ($wanted.Count -gt 0) 'Empty predicate.'
    $found=[Collections.Generic.List[int]]::new()
    for ($i=0;$i -le $Tokens.Count-$wanted.Count;$i++) {
        $same=$true
        for ($j=0;$j -lt $wanted.Count;$j++) { if ($Tokens[$i+$j] -cne $wanted[$j]) { $same=$false;break } }
        if ($same) { $found.Add($i) }
    }
    return ,$found.ToArray()
}
function Require-BirthSequence {
    param([string[]] $Tokens,[string] $Sequence,[int] $Count=1)
    Assert-BirthSource ((Find-BirthSequence $Tokens $Sequence).Count -eq $Count) ('Sequence differs: '+$Sequence)
}
function Get-BirthBlock {
    param([string[]] $Tokens,[string] $Signature)
    $matches=Find-BirthSequence $Tokens $Signature
    Assert-BirthSource ($matches.Count -eq 1) ('Missing/duplicate block: '+$Signature)
    $start=$matches[0];$open=$start
    while ($open -lt $Tokens.Count -and $Tokens[$open] -cne '{') { $open++ }
    Assert-BirthSource ($open -lt $Tokens.Count) 'Opening brace missing.'
    $depth=0
    for ($i=$open;$i -lt $Tokens.Count;$i++) {
        if ($Tokens[$i] -ceq '{') { $depth++ } elseif ($Tokens[$i] -ceq '}') { $depth-- }
        if ($depth -eq 0) { return ,([string[]]$Tokens[$start..$i]) }
    }
    throw 'BIRTH source contract: Unbalanced block.'
}
function Require-BirthOrder {
    param([string[]] $Tokens,[string[]] $Sequences)
    $previous=-1
    foreach ($sequence in $Sequences) {
        $found=Find-BirthSequence $Tokens $sequence
        Assert-BirthSource ($found.Count -eq 1 -and $found[0] -gt $previous) ('Order differs: '+$sequence)
        $previous=$found[0]
    }
}
function Require-BirthSuspendOrder {
    param([string[]] $Tokens)
    Require-BirthSequence $Tokens 'SuspendThread(' 1
    Require-BirthSequence $Tokens 'ResumeThread(' 1
    Require-BirthOrder $Tokens @(
        'uint previous = SuspendThread(thread);',
        'if (previous == UInt32.MaxValue) Check(false, "Inspect held suspend count");',
        'if (previous != 1) throw new InvalidOperationException(',
        'uint restored = ResumeThread(thread);',
        'if (restored == UInt32.MaxValue) Check(false, "Restore held suspend count");',
        'if (restored != 2) throw new InvalidOperationException('
    )
}
function Assert-NativeBirthContract {
    param([string] $Name,[string] $Text)
    $native=Get-BirthBlock (Get-BirthTokens $Text) 'public sealed class NativeChild : IDisposable'
    $ctor=Get-BirthBlock $native 'private NativeChild('
    switch ($Name) {
        'job_created_private_noninheritable' {
            Require-BirthSequence $ctor 'job = CreateJobObject(IntPtr.Zero, null); Check(job != IntPtr.Zero, "CreateJobObject");'
        }
        'kill_on_close_without_breakaway_before_birth' {
            Require-BirthOrder $ctor @('limits.basic.flags = 0x2000;','Check(SetInformationJobObject(job, 9, ref limits, (uint)Marshal.SizeOf(limits)), "Job kill-on-close");','Check(CreateProcess(')
        }
        'two_attribute_capacity_both_initializers' {
            Require-BirthSequence $ctor 'InitializeProcThreadAttributeList(IntPtr.Zero, 2, 0, ref size);'
            Require-BirthSequence $ctor 'Check(InitializeProcThreadAttributeList(attributes, 2, 0, ref size), "Attribute list"); initialized = true;'
        }
        'attribute_allocation_and_size_guard' {
            Require-BirthOrder $ctor @('if (size == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(), "Attribute size");','attributes = Marshal.AllocHGlobal(size);','Check(InitializeProcThreadAttributeList(attributes, 2, 0, ref size), "Attribute list");')
        }
        'exact_three_inherited_pipe_handles' {
            Require-BirthSequence $ctor 'handles = Marshal.AllocHGlobal(IntPtr.Size * 3);'
            Require-BirthSequence $ctor 'Marshal.WriteIntPtr(handles,' 3
            Require-BirthSequence $ctor 'Marshal.WriteIntPtr(handles, 0, input.ClientSafePipeHandle.DangerousGetHandle());'
            Require-BirthSequence $ctor 'Marshal.WriteIntPtr(handles, IntPtr.Size, output.ClientSafePipeHandle.DangerousGetHandle());'
            Require-BirthSequence $ctor 'Marshal.WriteIntPtr(handles, IntPtr.Size * 2, error.ClientSafePipeHandle.DangerousGetHandle());'
        }
        'handle_attribute_exact_bound_and_checked' {
            Require-BirthSequence $ctor 'Check(UpdateProcThreadAttribute(attributes, 0, new IntPtr(0x20002), handles, new IntPtr(IntPtr.Size * 3), IntPtr.Zero, IntPtr.Zero), "Explicit inherited handle list");'
        }
        'job_value_distinct_single_pointer_buffer' {
            Require-BirthSequence $ctor 'IntPtr attributes = IntPtr.Zero, handles = IntPtr.Zero, jobs = IntPtr.Zero;'
            Require-BirthOrder $ctor @('jobs = Marshal.AllocHGlobal(IntPtr.Size);','Marshal.WriteIntPtr(jobs, 0, job);','Check(UpdateProcThreadAttribute(attributes, 0, new IntPtr(0x2000D), jobs,')
        }
        'required_job_attribute_checked_exact_size' {
            Require-BirthSequence $ctor 'Check(UpdateProcThreadAttribute(attributes, 0, new IntPtr(0x2000D), jobs, new IntPtr(IntPtr.Size), IntPtr.Zero, IntPtr.Zero), "Required birth-time Job list");'
        }
        'both_attributes_bound_before_only_create' {
            Require-BirthSequence $ctor 'UpdateProcThreadAttribute(' 2
            Require-BirthSequence $ctor 'CreateProcess(' 1
            Require-BirthOrder $ctor @('new IntPtr(0x20002), handles,','new IntPtr(0x2000D), jobs,','start.attributes = attributes;','Check(CreateProcess(')
        }
        'job_not_in_inheritance_allowlist' {
            Require-BirthSequence $ctor 'Marshal.WriteIntPtr(handles, 0, job);' 0
            Require-BirthSequence $ctor 'Marshal.WriteIntPtr(handles, IntPtr.Size * 3, job);' 0
            Require-BirthSequence $native 'SetHandleInformation(' 0
            Require-BirthSequence $native 'DuplicateHandle(' 0
        }
        'no_post_create_assignment_or_fallback_api' {
            Require-BirthSequence $native 'AssignProcessToJobObject' 0
            Require-BirthSequence $ctor 'Check(CreateProcess(' 1
        }
        'suspended_extended_startup_no_breakaway' {
            Require-BirthSequence $ctor 'Check(CreateProcess(executable, command, IntPtr.Zero, IntPtr.Zero, true, 0x08080004, IntPtr.Zero, directory, ref start, out info), "CreateProcess suspended");'
        }
        'startup_std_handles_match_allowlist' {
            Require-BirthSequence $ctor 'start.startup.cb = Marshal.SizeOf(start); start.startup.flags = 0x101;'
            Require-BirthSequence $ctor 'start.startup.stdin = input.ClientSafePipeHandle.DangerousGetHandle();'
            Require-BirthSequence $ctor 'start.startup.stdout = output.ClientSafePipeHandle.DangerousGetHandle();'
            Require-BirthSequence $ctor 'start.startup.stderr = error.ClientSafePipeHandle.DangerousGetHandle();'
        }
        'original_native_result_retained_before_membership' {
            Require-BirthOrder $ctor @('Check(CreateProcess(','process = info.process; thread = info.thread; ProcessId = info.pid;','Check(IsProcessInJob(process, job, out member), "Verify suspended Job membership");')
        }
        'membership_false_rejects_before_collectors' {
            Require-BirthOrder $ctor @('if (!member) throw new InvalidOperationException("Suspended child Job membership is false.");','outputThread.Start(); errorThread.Start();')
        }
        'native_birth_and_image_still_bound' {
            Require-BirthOrder $ctor @('Check(GetProcessTimes(process, out created, out exited, out kernelTime, out userTime), "Suspended child creation time");','StartedAt = new DateTimeOffset(DateTime.FromFileTimeUtc(created)).ToString("o");','Check(QueryFullProcessImageName(process, 0, processImage, ref imageCharacters), "Suspended child image identity");','if (!String.Equals(Path.GetFullPath(processImage.ToString()), executable, StringComparison.OrdinalIgnoreCase))')
        }
        'constructor_never_resumes' {
            Require-BirthSequence $ctor 'ResumeThread(' 0
            Require-BirthSequence $ctor 'Resume(' 0
        }
        'held_executable_validated_before_factory' {
            $fn=Get-BirthBlock $native 'public static NativeChild StartSuspended('
            Require-BirthSequence $fn '!heldExecutable.CanRead || Files.HashStream(heldExecutable) != expectedHash'
            Require-BirthOrder $fn @('Files.RegularAncestry(executable, true);','if (!String.Equals(heldExecutable.Name, executable, StringComparison.OrdinalIgnoreCase)','return new NativeChild(executable, arguments, directory, maximumBytes);')
        }
        'argument_tokens_quoted_not_shell_rebuilt' {
            Require-BirthSequence $ctor 'StringBuilder command = new StringBuilder(Quote(executable)); foreach (string arg in arguments) command.Append(" ").Append(Quote(arg));'
        }
        'local_client_pipe_copies_closed_before_collectors' {
            Require-BirthOrder $ctor @('output.DisposeLocalCopyOfClientHandle(); error.DisposeLocalCopyOfClientHandle();','input.DisposeLocalCopyOfClientHandle(); input.Dispose(); input = null;','outputThread.Start(); errorThread.Start();')
        }
        'constructor_preserves_primary_and_cleanup_error' {
            Require-BirthSequence $ctor 'catch (Exception primary) { try { Dispose(); } catch (Exception cleanup) { throw new AggregateException(primary, cleanup); } throw; }'
        }
        'attribute_value_buffers_live_until_list_deleted' {
            $fn=Get-BirthBlock $ctor 'finally'
            Require-BirthOrder $fn @('if (initialized) DeleteProcThreadAttributeList(attributes);','if (attributes != IntPtr.Zero) Marshal.FreeHGlobal(attributes);','if (handles != IntPtr.Zero) Marshal.FreeHGlobal(handles);','if (jobs != IntPtr.Zero) Marshal.FreeHGlobal(jobs);')
            Require-BirthSequence $ctor 'Marshal.FreeHGlobal(jobs);' 1
            Require-BirthSequence $ctor 'Marshal.FreeHGlobal(handles);' 1
        }
        'resume_checks_actual_suspended_ownership_first' {
            Require-BirthOrder (Get-BirthBlock $native 'public void Resume()') @('AssertSuspendedOwned();','runClock.Restart();','uint previous = ResumeThread(thread);')
        }
        'suspended_job_exact_one_process_and_count' {
            $fn=Get-BirthBlock $native 'public void AssertSuspendedOwned()'
            Require-BirthSequence $fn 'if (!member || WaitForSingleObject(process, 0) != 258)'
            Require-BirthSequence $fn 'if (accounting.active != 1)'
            Require-BirthSuspendOrder $fn
        }
        'suspend_prior_count_rejection_cannot_be_removed' {
            Require-BirthSuspendOrder (Get-BirthBlock $native 'public void AssertSuspendedOwned()')
        }
        'suspend_restore_cannot_precede_prior_count_rejection' {
            Require-BirthSuspendOrder (Get-BirthBlock $native 'public void AssertSuspendedOwned()')
        }
        'suspend_sole_restore_must_follow_prior_count_rejection' {
            Require-BirthSuspendOrder (Get-BirthBlock $native 'public void AssertSuspendedOwned()')
        }
        'suspend_native_error_rejection_precedes_restore' {
            Require-BirthSuspendOrder (Get-BirthBlock $native 'public void AssertSuspendedOwned()')
        }
        'suspend_restoration_requires_exact_prior_two' {
            Require-BirthSuspendOrder (Get-BirthBlock $native 'public void AssertSuspendedOwned()')
        }
        'suspend_fault_is_recorded_and_blocks_reprobe' {
            $fn=Get-BirthBlock $native 'public void AssertSuspendedOwned()'
            Require-BirthOrder $fn @('if (disposed || resumed || resumeAttempted)',
                'if (Failed) throw new AggregateException(FailureSnapshot());', 'uint previous = SuspendThread(thread);',
                'catch (Exception failure) { Record(failure); throw; }')
        }
        'resume_attempt_is_latched_before_native_call' {
            $fn=Get-BirthBlock $native 'public void Resume()'
            Require-BirthSequence $native 'private bool resumed,resumeAttempted,disposed;'
            Require-BirthOrder $fn @('AssertSuspendedOwned();', 'runClock.Restart();', 'resumeAttempted = true;',
                'uint previous = ResumeThread(thread);', 'if (previous == UInt32.MaxValue) Check(false, "Resume assigned child");',
                'if (previous != 1) throw new InvalidOperationException(', 'resumed = true;',
                'catch (Exception failure) { Record(failure); throw; }')
        }
        'resume_reentry_rejected_before_clock_restart' {
            $fn=Get-BirthBlock $native 'public void Resume()'
            Require-BirthOrder $fn @('if (disposed || resumed || resumeAttempted) throw new InvalidOperationException("Child can attempt resume exactly once.");',
                'if (Failed) throw new AggregateException(FailureSnapshot());', 'AssertSuspendedOwned();',
                'runClock.Restart();', 'resumeAttempted = true;', 'uint previous = ResumeThread(thread);')
        }
        'lifetime_bound_remains_single_assignment' {
            Require-BirthSequence (Get-BirthBlock $native 'private void BindObservationLifetime(') 'maximumLifetimeMilliseconds < 1 || maximumLifetimeMilliseconds > 600000'
            Require-BirthSequence (Get-BirthBlock $native 'private void BindObservationLifetime(') 'else if (observationLifetimeMilliseconds != maximumLifetimeMilliseconds)'
        }
        'cleanup_attempts_exact_process_and_job_bounded' {
            $fn=Get-BirthBlock $native 'private void StopOwnedProcessAndJob('
            Require-BirthSequence $fn 'if (before != 0 && !TerminateProcess(process, 200))'
            Require-BirthSequence $fn 'if (job != IntPtr.Zero && !TerminateJobObject(job, 200))'
            Require-BirthSequence $fn 'ClassifiedWait(process, 5000, label + " final wait", errors)'
            Require-BirthSequence $fn 'if (job != IntPtr.Zero) ProveJobEmpty(5000, errors);'
        }
        'dispose_attempts_cleanup_before_native_release' {
            $fn=Get-BirthBlock $native 'public void Dispose()'
            Require-BirthOrder $fn @('StopOwnedProcessAndJob(cleanup, "Cleanup containment");','foreach (IntPtr handle in new IntPtr[] { thread,process,job })','if (cleanup.Count != 0) throw new AggregateException(cleanup.ToArray());')
            # This deliberately does NOT assert retained handles on cleanup error:
            # existing Dispose still closes them and is not fixed by JOB_LIST.
        }
        'native_result_still_requires_job_empty_and_eof' {
            $fn=Get-BirthBlock $native 'public NativeResult PollResult('
            Require-BirthOrder $fn @('if (accounting.active != 0 || outputThread.IsAlive || errorThread.IsAlive) return null;','if (!outputThread.Join(0) || !errorThread.Join(0)) return null;','completedResult = new NativeResult')
        }
        default { throw 'BIRTH source contract: Unknown predicate.' }
    }
}
$rows=@(
    @('job_created_private_noninheritable','job = CreateJobObject(IntPtr.Zero, null);','job = CreateJobObject(attributes, null);'),
    @('kill_on_close_without_breakaway_before_birth','limits.basic.flags = 0x2000;','limits.basic.flags = 0x2800;'),
    @('two_attribute_capacity_both_initializers','InitializeProcThreadAttributeList(IntPtr.Zero, 2, 0, ref size);','InitializeProcThreadAttributeList(IntPtr.Zero, 1, 0, ref size);'),
    @('attribute_allocation_and_size_guard','if (size == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(), "Attribute size");',''),
    @('exact_three_inherited_pipe_handles','handles = Marshal.AllocHGlobal(IntPtr.Size * 3);','handles = Marshal.AllocHGlobal(IntPtr.Size * 4);'),
    @('handle_attribute_exact_bound_and_checked','new IntPtr(IntPtr.Size * 3), IntPtr.Zero, IntPtr.Zero), "Explicit inherited handle list"','new IntPtr(IntPtr.Size * 4), IntPtr.Zero, IntPtr.Zero), "Explicit inherited handle list"'),
    @('job_value_distinct_single_pointer_buffer','jobs = Marshal.AllocHGlobal(IntPtr.Size);','jobs = handles;'),
    @('required_job_attribute_checked_exact_size','new IntPtr(0x2000D), jobs,','new IntPtr(0x2000E), jobs,'),
    @('both_attributes_bound_before_only_create','start.attributes = attributes;','start.attributes = IntPtr.Zero;'),
    @('job_not_in_inheritance_allowlist','Marshal.WriteIntPtr(jobs, 0, job);','Marshal.WriteIntPtr(jobs, 0, job); Marshal.WriteIntPtr(handles, IntPtr.Size * 3, job);'),
    @('no_post_create_assignment_or_fallback_api','process = info.process; thread = info.thread; ProcessId = info.pid;','process = info.process; thread = info.thread; ProcessId = info.pid; AssignProcessToJobObject(job,process);'),
    @('suspended_extended_startup_no_breakaway','0x08080004, IntPtr.Zero, directory','0x09080004, IntPtr.Zero, directory'),
    @('startup_std_handles_match_allowlist','start.startup.flags = 0x101;','start.startup.flags = 0x1;'),
    @('original_native_result_retained_before_membership','process = info.process; thread = info.thread; ProcessId = info.pid;','thread = info.thread; ProcessId = info.pid;'),
    @('membership_false_rejects_before_collectors','if (!member) throw new InvalidOperationException("Suspended child Job membership is false.");',''),
    @('native_birth_and_image_still_bound','StartedAt = new DateTimeOffset(DateTime.FromFileTimeUtc(created)).ToString("o");','StartedAt = "claimed";'),
    @('constructor_never_resumes','process = info.process; thread = info.thread; ProcessId = info.pid;','process = info.process; thread = info.thread; ProcessId = info.pid; ResumeThread(thread);'),
    @('held_executable_validated_before_factory','!heldExecutable.CanRead || Files.HashStream(heldExecutable) != expectedHash','!heldExecutable.CanRead'),
    @('argument_tokens_quoted_not_shell_rebuilt','command.Append(" ").Append(Quote(arg))','command.Append(" ").Append(arg)'),
    @('local_client_pipe_copies_closed_before_collectors','output.DisposeLocalCopyOfClientHandle(); error.DisposeLocalCopyOfClientHandle();','output.DisposeLocalCopyOfClientHandle();'),
    @('constructor_preserves_primary_and_cleanup_error','throw new AggregateException(primary, cleanup);','throw cleanup;'),
    @('attribute_value_buffers_live_until_list_deleted','if (initialized) DeleteProcThreadAttributeList(attributes);',''),
    @('resume_checks_actual_suspended_ownership_first','AssertSuspendedOwned();',''),
    @('suspended_job_exact_one_process_and_count','if (accounting.active != 1)','if (accounting.active < 1)'),
    @('lifetime_bound_remains_single_assignment','else if (observationLifetimeMilliseconds != maximumLifetimeMilliseconds)','else if (false)'),
    @('cleanup_attempts_exact_process_and_job_bounded','if (job != IntPtr.Zero) ProveJobEmpty(5000, errors);',''),
    @('dispose_attempts_cleanup_before_native_release','StopOwnedProcessAndJob(cleanup, "Cleanup containment");',''),
    @('native_result_still_requires_job_empty_and_eof','if (accounting.active != 0 || outputThread.IsAlive || errorThread.IsAlive) return null;','if (outputThread.IsAlive || errorThread.IsAlive) return null;'),
    @('suspend_prior_count_rejection_cannot_be_removed','if (previous != 1) throw new InvalidOperationException("Held suspension was disproved; retain the inspection increment for containment.");','if (false) throw new InvalidOperationException("Held suspension was disproved; retain the inspection increment for containment.");'),
    @('suspend_restore_cannot_precede_prior_count_rejection','if (previous != 1) throw new InvalidOperationException("Held suspension was disproved; retain the inspection increment for containment.");','if (ResumeThread(thread) == 0 || previous != 1) throw new InvalidOperationException("Held suspension was disproved; retain the inspection increment for containment.");'),
    @('suspend_native_error_rejection_precedes_restore','if (previous == UInt32.MaxValue) Check(false, "Inspect held suspend count");','if (false) Check(false, "Inspect held suspend count");'),
    @('suspend_restoration_requires_exact_prior_two','if (restored != 2)','if (restored < 1)'),
    @('suspend_fault_is_recorded_and_blocks_reprobe','if (Failed) throw new AggregateException(FailureSnapshot());',''),
    @('resume_attempt_is_latched_before_native_call','resumeAttempted = true;','resumeAttempted = false;'),
    @('resume_reentry_rejected_before_clock_restart','if (disposed || resumed || resumeAttempted) throw new InvalidOperationException("Child can attempt resume exactly once.");','if (disposed || resumed) throw new InvalidOperationException("Child can attempt resume exactly once.");'),
    @('suspend_sole_restore_must_follow_prior_count_rejection','uint restored = ResumeThread(thread);','uint restored = ResumeThread(thread);','if (previous != 1) throw new InvalidOperationException("Held suspension was disproved; retain the inspection increment for containment.");')
)
$results=[Collections.Generic.List[object]]::new()
foreach ($row in $rows) {
    Assert-NativeBirthContract $row[0] $source
    $results.Add([pscustomobject]@{name=$row[0];negative_control=$false;passed=$true})
    Assert-BirthSource ($source.Contains($row[1])) ('Missing mutation target: '+$row[0])
    if ($row.Count -eq 4) {
        # Move the sole existing call, keeping its count unchanged. This isolates
        # ordering from the separate extra-call rejection negative control.
        Assert-BirthSource ($source.Contains($row[3])) ('Missing move anchor: '+$row[0])
        $changed=$source.Replace($row[1],'').Replace($row[3],($row[2]+' '+$row[3]))
    } else { $changed=$source.Replace($row[1],$row[2]) }
    $failure=$null
    try { Assert-NativeBirthContract $row[0] $changed } catch { $failure=$_.Exception.Message }
    Assert-BirthSource ($null -ne $failure -and $failure.StartsWith('BIRTH source contract:')) ('Negative survived: '+$row[0])
    $results.Add([pscustomobject]@{name=($row[0]+' mutation rejected');negative_control=$true;passed=$true})
}
$expectedCases=72
Assert-BirthSource ($results.Count -eq $expectedCases -and $rows.Count -eq 36) 'Fixed case count differs.'
[pscustomobject]@{suite='native-child-birth-source-contracts';expected=$expectedCases;executed=$results.Count;passed=$results.Count;failed=0
    ps_version=$PSVersionTable.PSVersion.ToString();ps_edition=$PSVersionTable.PSEdition
    runtime_sha256_at_read=$sourceHash;native_executed=$false;runtime_compiled=$false;runtime_members_invoked=$false
    pipes_or_threads_executed=$false;semantic_csharp_ast=$false;birth_job_native_qualified=$false
    cleanup_retry_retention_qualified=$false;session_command_job_qualified=$false;full_protocol_accepted=$false
    results=@($results.ToArray())} | ConvertTo-Json -Depth 5 -Compress
