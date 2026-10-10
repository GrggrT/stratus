[CmdletBinding()]
param([string] $RuntimePath)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $PSBoundParameters.ContainsKey('RuntimePath')) { $RuntimePath = Join-Path $PSScriptRoot '..\Stratus.ParentStage.Runtime.cs' }
# C# SOURCE CONTRACTS ONLY: comment/literal-aware tokenization and balanced
# blocks, not semantic AST, native lifecycle or full protocol qualification.
# No runtime source, type, constructor, P/Invoke or process is executed/compiled.
$source = [IO.File]::ReadAllText([IO.Path]::GetFullPath($RuntimePath))
$begin = $source.IndexOf('    // Separate safety-guard capability:', [StringComparison]::Ordinal)
$end = $source.IndexOf('    public sealed class NativeResult {', [StringComparison]::Ordinal)
if ($begin -lt 0 -or $end -le $begin -or $source.LastIndexOf('public sealed class IndependentTransitionLaunch', [StringComparison]::Ordinal) -ne
    $source.IndexOf('public sealed class IndependentTransitionLaunch', [StringComparison]::Ordinal)) { throw 'Transition source slice is missing or duplicated.' }
$classSource = $source.Substring($begin, $end - $begin)
$results = [Collections.Generic.List[object]]::new()

function Assert-Source { param([bool] $Value, [string] $Message) if (-not $Value) { throw ('Transition source contract: ' + $Message) } }
function Get-Tokens {
    param([string] $Text)
    $pattern = @'
//[^\r\n]* | /\*.*?\*/ | @"(?:""|[^"])*" | "(?:\\.|[^"\\])*" | '(?:\\.|[^'\\])*'
| [A-Za-z_][A-Za-z_0-9]* | [0-9]+ | == | != | >= | <= | \+\+ | && | \|\| | => | [^\s]
'@
    $tokens = [Collections.Generic.List[string]]::new()
    foreach ($match in [regex]::Matches($Text, $pattern, [Text.RegularExpressions.RegexOptions]::Singleline -bor [Text.RegularExpressions.RegexOptions]::IgnorePatternWhitespace)) {
        if (-not $match.Value.StartsWith('//') -and -not $match.Value.StartsWith('/*')) { $tokens.Add($match.Value) }
    }
    return ,$tokens.ToArray()
}
function Find-Sequence {
    param([string[]] $Tokens, [string] $Sequence)
    $wanted = Get-Tokens $Sequence; Assert-Source ($wanted.Count -gt 0) 'Empty source predicate.'
    $found = [Collections.Generic.List[int]]::new()
    for ($index = 0; $index -le $Tokens.Count - $wanted.Count; $index++) {
        $same = $true
        for ($part = 0; $part -lt $wanted.Count; $part++) { if ($Tokens[$index + $part] -cne $wanted[$part]) { $same = $false; break } }
        if ($same) { $found.Add($index) }
    }
    return ,$found.ToArray()
}
function Require-Sequence {
    param([string[]] $Tokens, [string] $Sequence, [int] $Count = 1)
    Assert-Source ((Find-Sequence $Tokens $Sequence).Count -eq $Count) ('Required token sequence differs: ' + $Sequence)
}
function Get-Block {
    param([string[]] $Tokens, [string] $Signature)
    $matches = Find-Sequence $Tokens $Signature
    Assert-Source ($matches.Count -eq 1) ('Missing/duplicate block: ' + $Signature)
    $start = $matches[0]; $open = $start
    while ($open -lt $Tokens.Count -and $Tokens[$open] -cne '{') { $open++ }
    Assert-Source ($open -lt $Tokens.Count) 'Opening brace missing.'
    $depth = 0
    for ($index = $open; $index -lt $Tokens.Count; $index++) {
        if ($Tokens[$index] -ceq '{') { $depth++ } elseif ($Tokens[$index] -ceq '}') { $depth-- }
        if ($depth -eq 0) { return ,([string[]]$Tokens[$start..$index]) }
    }
    throw 'Transition source contract: Unbalanced block.'
}
function Require-Order {
    param([string[]] $Tokens, [string[]] $Sequences)
    $previous = -1
    foreach ($sequence in $Sequences) {
        $matches = Find-Sequence $Tokens $sequence
        Assert-Source ($matches.Count -eq 1 -and $matches[0] -gt $previous) ('Ordering differs: ' + $sequence)
        $previous = $matches[0]
    }
}
function Require-TransitionSuspendOrder {
    param([string[]] $Tokens)
    Require-Sequence $Tokens 'SuspendThread(' 1
    Require-Sequence $Tokens 'ResumeThread(' 1
    Require-Order $Tokens @('uint previous=SuspendThread(thread);',
        'Check(previous!=UInt32.MaxValue,"Transition suspended count");',
        'if(previous!=1) throw new InvalidOperationException(',
        'uint restored=ResumeThread(thread);',
        'Check(restored!=UInt32.MaxValue,"Transition suspended count restoration");',
        'if(restored!=2) throw new InvalidOperationException(')
    Require-Sequence $Tokens 'catch(Exception failure) { resumeAttempted=true; Record(failure); throw; }'
}
function Require-TransitionAbortNoResume {
    param([string[]] $Tokens)
    Require-Sequence $Tokens 'ResumeThread(' 0
    $wrongCount=Get-Block $Tokens 'if(previous!=1)'
    Require-Order $wrongCount @('resumeAttempted=true;',
        'Exception failure=new InvalidOperationException("Transition abort lacks exact never-resumed suspended proof.");',
        'Record(failure);', 'throw failure;')
    Require-Sequence $wrongCount 'resumeAttempted=false' 0
    $nativeError=Get-Block $Tokens 'if(previous==UInt32.MaxValue)'
    Require-Order $nativeError @('resumeAttempted=true;',
        'Exception failure=new Win32Exception(Marshal.GetLastWin32Error(),"Transition abort suspended proof");',
        'Record(failure);', 'throw failure;')
}
function Assert-TransitionContract {
    param([string] $Name, [string] $Text)
    $tokens = Get-Tokens $Text
    $class = Get-Block $tokens 'public sealed class IndependentTransitionLaunch : IDisposable'
    switch ($Name) {
        'separate_no_job_no_reopen' {
            foreach ($forbidden in @('NativeChild','IndependentWatchdogLaunch','CreateJobObject','AssignProcessToJobObject','TerminateJobObject','GetProcessById','OpenProcess','StopAndProve')) { Require-Sequence $class $forbidden 0 }
        }
        'fixed_create_flags' {
            $ctor = Get-Block $class 'public void StartSuspended()'
            Require-Sequence $ctor 'bool createdOkay=CreateProcess(executable,command,IntPtr.Zero,IntPtr.Zero,true,0x08080404,environmentMemory,root,ref startup,out created);'
            Require-Order $ctor @('bool createdOkay=CreateProcess(', 'creationOutput=created;', 'createSucceeded=createdOkay; createReturned=true;', 'Check(createdOkay,"Create Transition guard suspended");')
            Require-Sequence $ctor 'environmentMemory=Marshal.StringToHGlobalUni(env);'
        }
        'exact_nineteen_plan' {
            Require-Sequence $class 'private static readonly string[] PlanKeys={"Root","SessionId","OwnerPid","OwnerStartTimeUtc","DeadlineUtc","HeartbeatTimeoutSeconds","LeasePath","ReceiptPath","HeartbeatPath","CommitPath","LiveAcceptancePath","PreStartAttestationPath","ExpectedPreStartSha256","StartFencePath","ExpectedStartFenceSha256","StartRevokePath","StartAttemptPath","StartCompletionPath","PostgresContainer"};'
            $copy = Get-Block $class 'private static Dictionary<string,string> CopyPlan('
            Require-Sequence $copy 'plan==null || plan.Count!=PlanKeys.Length'
            Require-Sequence $copy 'Array.IndexOf(PlanKeys,item.Key)<0 || copy.ContainsKey(item.Key)'
            Require-Sequence $copy 'copy["PostgresContainer"]!=RetainedPostgresId'
        }
        'fixed_argv_private_nonce' {
            $ctor = Get-Block $class 'public void StartSuspended()'
            Require-Sequence $ctor 'foreach(string name in PlanKeys) { arguments.Add("-"+name); arguments.Add(copy[name]); }'
            Require-Sequence $ctor 'arguments.Add("-ParentStageNonce"); arguments.Add(Nonce);'
            Require-Sequence $ctor 'using(RandomNumberGenerator random=RandomNumberGenerator.Create()) random.GetBytes(nonceBytes);'
            Require-Sequence $ctor '"-ExecutionPolicy"' 0
        }
        'fixed_sources_and_four_holds' {
            $ctor = Get-Block $class 'public void StartSuspended()'
            Require-Sequence $ctor 'source!=Path.Combine(root,"scripts","Invoke-StratusSuccessorTransitionWatchdog.ps1")'
            Require-Sequence $ctor 'runtime!=Path.Combine(root,"scripts","Stratus.ParentStage.Runtime.cs")'
            Require-Sequence $ctor 'Hold(executable,executableHash,134217728); Hold(source,sourceHash,2097152); Hold(runtime,runtimeHash,2097152); Hold(contract,contractHash,1048576);'
            Require-Sequence $ctor 'Hold(' 4
        }
        'early_fence_not_opened' {
            $ctor = Get-Block $class 'public void StartSuspended()'
            foreach ($forbidden in @('new FileStream','Files.Lock','Files.ReadBounded','Files.ReadRawBounded','HashStream')) { Require-Sequence $ctor $forbidden 0 }
            Require-Sequence $ctor 'Files.RegularAncestry(path,name=="PreStartAttestationPath" || name=="StartFencePath");'
        }
        'path_alias_rejection' {
            $path = Get-Block $class 'private static void CanonicalPath('
            Require-Sequence $path "path.IndexOf(':',2)>=0"
            Require-Sequence $path 'component.EndsWith(".",StringComparison.Ordinal)'
            Require-Sequence $path 'component.EndsWith(" ",StringComparison.Ordinal)'
            Require-Sequence $path 'stem=="CON" || stem=="PRN" || stem=="AUX" || stem=="NUL"'
            $ctor = Get-Block $class 'public void StartSuspended()'
            Require-Sequence $ctor 'HashSet<string> paths=new HashSet<string>(StringComparer.OrdinalIgnoreCase);'
        }
        'environment_explicit_allowlist' {
            $env = Get-Block $class 'private static string EnvironmentBlock('
            Require-Sequence $env 'if(environment==null) throw new ArgumentNullException("environment");'
            Require-Sequence $env '"SystemRoot","WINDIR","PATH","TEMP","TMP","USERPROFILE","LOCALAPPDATA","APPDATA","ProgramData","ProgramFiles","ProgramFiles(x86)","CommonProgramFiles","ComSpec","SystemDrive"'
            Require-Sequence $env 'copy.ContainsKey(item.Key)'
            Require-Sequence $env 'block.Length>32767'
            Require-Sequence $class 'GetEnvironmentVariables' 0
        }
        'held_bounds_before_hash' {
            $hold = Get-Block $class 'private void Hold('
            Require-Order $hold @('new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.Read)', 'stream.Length<1 || stream.Length>maximum', 'Files.RequireSingleLink(stream);', 'Files.HashStream(stream)!=hash', 'held.Add(')
        }
        'inherited_exact_locks_and_pipes' {
            $ctor = Get-Block $class 'public void StartSuspended()'
            Require-Order $ctor @('foreach(HeldFile file in held)', 'DuplicateHandle(GetCurrentProcess(),file.Stream.SafeFileHandle.DangerousGetHandle(),GetCurrentProcess(),out duplicate,0,true,2)', 'List<IntPtr> handles=new List<IntPtr>();', 'handles.Add(duplicate.Raw);', 'UpdateProcThreadAttribute(attributes,0,new IntPtr(0x20002)')
            Require-Sequence $ctor 'handles.Add(input.ClientSafePipeHandle.DangerousGetHandle());'
            Require-Sequence $ctor 'handles.Add(output.ClientSafePipeHandle.DangerousGetHandle());'
            Require-Sequence $ctor 'handles.Add(error.ClientSafePipeHandle.DangerousGetHandle());'
        }
        'any_job_rechecked' {
            $job = Get-Block $class 'private void AssertNoJob()'
            Require-Sequence $job 'IsProcessInJob(process,IntPtr.Zero,out member)'
            Require-Sequence $job 'if(member) throw new InvalidOperationException("Transition safety guard belongs to a Job.");'
            foreach ($signature in @('public void StartSuspended()', 'public void AssertSuspended()', 'public void Resume()', 'private void AssertLive(', 'public TerminalExitProof PollTerminalExit()')) {
                Assert-Source ((Find-Sequence (Get-Block $class $signature) 'AssertNoJob();').Count -gt 0) 'ANY-Job check missing at owned boundary.'
            }
        }
        'one_original_deadline' {
            Require-Sequence $class 'private readonly Stopwatch lifetimeClock=Stopwatch.StartNew();'
            Require-Sequence $class 'maximumLifetimeMilliseconds=(deadline-createdUtc).TotalMilliseconds;'
            Require-Sequence $class 'maximumLifetimeMilliseconds<=0 || maximumLifetimeMilliseconds>600000'
            $clock = Get-Block $class 'private void AssertClock('
            Require-Sequence $clock 'DateTimeOffset.UtcNow>=deadline || lifetimeClock.Elapsed.TotalMilliseconds>=maximumLifetimeMilliseconds'
            Require-Sequence $clock 'readiness && lifetimeClock.ElapsedMilliseconds>=30000'
            Require-Sequence $class 'Restart' 0
        }
        'suspend_proof_uncertainty_latched' {
            $suspended = Get-Block $class 'public void AssertSuspended()'
            Require-TransitionSuspendOrder $suspended
        }
        'resume_latch_before_kernel' {
            $resume = Get-Block $class 'public void Resume()'
            Require-Order $resume @('if(resumeAttempted)', 'AssertSuspended();', 'resumeAttempted=true;', 'uint previous=ResumeThread(thread);', 'resumeSucceeded=true;')
        }
        'abort_never_resumed_exact_handle' {
            $abort = Get-Block $class 'public void AbortBeforeResume()'
            Require-Order $abort @('if(resumeAttempted)', 'uint previous=SuspendThread(thread);', 'if(previous==UInt32.MaxValue)', 'if(previous!=1)', 'TerminateProcess(process,202)', 'WaitForSingleObject(process,5000)')
            Require-TransitionAbortNoResume $abort
        }
        'suspend_prior_count_guard_cannot_be_removed' {
            Require-TransitionSuspendOrder (Get-Block $class 'public void AssertSuspended()')
        }
        'suspend_native_failure_guard_precedes_restore' {
            Require-TransitionSuspendOrder (Get-Block $class 'public void AssertSuspended()')
        }
        'suspend_restore_cannot_precede_count_guard' {
            Require-TransitionSuspendOrder (Get-Block $class 'public void AssertSuspended()')
        }
        'suspend_sole_restore_must_follow_count_guard' {
            Require-TransitionSuspendOrder (Get-Block $class 'public void AssertSuspended()')
        }
        'suspend_restored_count_guard_cannot_be_removed' {
            Require-TransitionSuspendOrder (Get-Block $class 'public void AssertSuspended()')
        }
        'abort_never_restores_disproved_suspension' {
            Require-TransitionAbortNoResume (Get-Block $class 'public void AbortBeforeResume()')
        }
        'abort_wrongcount_latch_is_mandatory' {
            Require-TransitionAbortNoResume (Get-Block $class 'public void AbortBeforeResume()')
        }
        'dispose_retains_ambiguity' {
            $dispose = Get-Block $class 'public void Dispose()'
            Require-Order $dispose @('resumeAttempted && terminalProof==null', 'if(IsAlive())', 'foreach(IDisposable item', 'disposed=true;')
            Require-Sequence $dispose 'if(resumeAttempted) throw new InvalidOperationException("Live Transition guard cannot be disposed.");'
        }
        'bounded_drain_never_kills' {
            $collect = Get-Block $class 'private void Collect('
            Require-Sequence $class 'private const int OutputMaximumBytes=327680, ErrorMaximumBytes=65536;'
            Require-Sequence $collect 'Math.Min((long)maximum+1,before+count)'
            Require-Sequence $collect 'if(retained>0) outputBytes.Write(buffer,0,retained);'
            foreach ($forbidden in @('TerminateProcess','CloseHandle','Dispose','Kill')) { Require-Sequence $collect $forbidden 0 }
        }
        'stderr_fault_is_latched' {
            $collect = Get-Block $class 'private void Collect('
            Require-Sequence $collect 'if(!stdout && before==0) Record(new InvalidDataException("Transition guard wrote stderr."));'
            $read = Get-Block $class 'private byte[] ReadFrameLocked('
            Require-Sequence $read 'errorCount!=0 || outputCount!=outputBytes.Length'
        }
        'live_before_after_identity' {
            $live = Get-Block $class 'private void AssertLive('
            Require-Sequence $live 'AssertClock(readiness); ThrowFaults();' 2
            Require-Order $live @('if(!IsAlive()) throw new InvalidOperationException("Transition guard is not live.");', 'AssertIdentity(); AssertNoJob();', 'if(!IsAlive()) throw new InvalidOperationException("Transition exited during live proof.");')
        }
        'ready_immutable_private_proof' {
            $proof = Get-Block $class 'public sealed class ReadyProof'
            Require-Sequence $proof 'public readonly string Text,Sha256,StartedAt,SessionId,Nonce,LeasePath,LeaseSha256;'
            Require-Sequence $proof 'public readonly int ProcessId;'
            Require-Sequence $proof 'public readonly long ByteCount;'
            Require-Sequence $proof 'private ReadyProof(IndependentTransitionLaunch owner)'
            Require-Sequence $proof 'owner==null || !owner.readyProofReady || owner.readyCapture==null || owner.readyLeaseHash==null'
        }
        'terminal_immutable_private_proof' {
            $proof = Get-Block $class 'public sealed class TerminalExitProof'
            Require-Sequence $proof 'public readonly ReadyProof Ready;'
            Require-Sequence $proof 'public readonly int ProcessId,ExitCode,ReceiptBytes;'
            Require-Sequence $proof 'public readonly long CompleteBytes,StdoutBytes,StderrBytes;'
            Require-Sequence $proof 'private TerminalExitProof(IndependentTransitionLaunch owner)'
            Require-Sequence $proof 'owner==null || !owner.terminalProofReady || owner.readyProof==null'
        }
        'frame_limits_before_decode' {
            $parse = Get-Block $class 'private Dictionary<string,string> ParseFrame('
            Require-Sequence $class 'private const int ReadyMaximumBytes=8192, CompleteMaximumBytes=8192;'
            Require-Order $parse @('bytes.Length>maximum', 'StrictJson.Validate(bytes);', 'Files.Utf8.GetString(bytes)')
            Require-Sequence $parse 'bytes[i]==0 || bytes[i]==10 || bytes[i]==13'
            Require-Sequence $parse "Punctuation(text,ref position,'\n');"
        }
        'exact_frame_fields_and_native_ints' {
            $parse = Get-Block $class 'private Dictionary<string,string> ParseFrame('
            Require-Sequence $parse 'new string[] {"format","session_id","nonce","pid","started_at","receipt_path","receipt_sha256","receipt_bytes"}'
            Require-Sequence $parse 'new string[] {"format","session_id","nonce","pid","started_at","lease_path","lease_sha256"}'
            Require-Sequence $parse 'if(name=="pid" || name=="receipt_bytes")'
            Require-Sequence $parse 'BoundedInteger(number,1,name=="pid" ? Int32.MaxValue : 262144)'
        }
        'frames_bound_to_private_launch' {
            $parse = Get-Block $class 'private Dictionary<string,string> ParseFrame('
            Require-Sequence $parse 'values["session_id"]!=SessionId || values["nonce"]!=Nonce || values["pid"]!=ProcessId.ToString(CultureInfo.InvariantCulture)'
            Require-Sequence $parse 'values["started_at"]!=StartedAt'
            Require-Sequence $parse 'values[complete ? "receipt_path" : "lease_path"]!=(complete ? receiptPath : leasePath)'
        }
        'frame_scan_bounded_before_copy' {
            $read = Get-Block $class 'private byte[] ReadFrameLocked('
            Require-Order $read @('for(int i=0;i<Math.Min(available,maximum);i++)', 'byte[] frame=new byte[i+1];', 'Buffer.BlockCopy(buffer,start,frame,0,frame.Length);')
            Require-Sequence $read 'if(available>=maximum) throw new InvalidDataException("Transition frame exceeds its fixed limit.");'
        }
        'ready_live_before_publication' {
            $ready = Get-Block $class 'public ReadyProof PollReady()'
            Require-Order $ready @('parsed=ParseFrame(frame,false);', 'AssertLive(true);', 'if(!IsAlive()) throw new InvalidOperationException("Transition exited before READY capability publication.");', 'readyCapture=frame;', 'readyProof=ReadyProof.FromOwned(this);')
            Require-Sequence $ready 'if(outputCount!=frame.Length) throw new InvalidDataException("Transition READY must be the sole captured frame.");'
        }
        'terminal_requires_ready_no_begin' {
            $terminal = Get-Block $class 'public TerminalExitProof PollTerminalExit()'
            Require-Sequence $terminal 'if(!resumeSucceeded || readyProof==null)'
            Require-Sequence $class 'BeginTerminalObservation' 0
            Require-Sequence $terminal 'Files.Hash(first)!=readyProof.Sha256'
        }
        'terminal_exact_second_frame' {
            $terminal = Get-Block $class 'public TerminalExitProof PollTerminalExit()'
            Require-Sequence $terminal 'if(outputCount!=first.Length+complete.Length) throw new InvalidDataException("Unexpected Transition trailing output.");'
            Require-Sequence $terminal 'ParseFrame(complete,true)'
            Require-Sequence $terminal 'Files.Hash(completeCapture)!=Files.Hash(complete)'
        }
        'terminal_exit_and_collectors' {
            $terminal = Get-Block $class 'public TerminalExitProof PollTerminalExit()'
            Require-Order $terminal @('if(!outputEof || !errorEof)', 'if(!outputThread.Join(0) || !errorThread.Join(0))', 'GetExitCodeProcess(process,out code)', 'code!=0 || exited<=0 || exited<born', 'terminalProof=TerminalExitProof.FromOwned(this);')
        }
        'polling_cannot_retire_guard' {
            foreach ($signature in @('public ReadyProof PollReady()', 'public void AssertReadyLive()', 'public TerminalExitProof PollTerminalExit()')) {
                $method = Get-Block $class $signature
                foreach ($forbidden in @('TerminateProcess','Dispose','AbortBeforeResume','CloseHandle')) { Require-Sequence $method $forbidden 0 }
            }
        }
        'managed_prepare_before_ownership' {
            $prepare = Get-Block $class 'public static IndependentTransitionLaunch Prepare('
            Require-Sequence $prepare 'return new IndependentTransitionLaunch(executable,executableHash,source,sourceHash,runtime,runtimeHash,contract,contractHash,plan,environment);'
            $ctor = Get-Block $class 'private IndependentTransitionLaunch('
            foreach ($forbidden in @('Hold(', 'Files.', 'Directory.Exists', 'File.Exists', 'RandomNumberGenerator', 'CreateProcess', 'Initialize(', 'DuplicateAdmissionForLaunch', 'AnonymousPipeServerStream', 'new Thread')) { Require-Sequence $ctor $forbidden 0 }
            Require-Sequence $ctor 'CanonicalPath(root,false);'
            Require-Sequence $ctor 'CanonicalPath(path,false);'
            Require-Sequence $ctor 'CanonicalPath(launchPlan[name],false);'
            Require-Sequence $class 'public static IndependentTransitionLaunch StartSuspended(' 0
        }
        'original_command_owner_is_readonly' {
            Require-Sequence $class 'private readonly CommandSessionOwner commandSession;'
            $ctor = Get-Block $class 'private IndependentTransitionLaunch('
            Require-Sequence $ctor 'commandSession=new CommandSessionOwner(this);'
            Require-Sequence $class 'commandSession=' 1
        }
        'start_admission_is_single_use' {
            $start = Get-Block $class 'public void StartSuspended()'
            Require-Order $start @('lock(lifecycleGate)', 'disposed || releaseStarted || retirementRequested || startAttempted', 'startAttempted=true; startInProgress=true;', 'commandSession.Initialize();')
            Require-Sequence $start 'finally { startInProgress=false; }'
        }
        'command_export_is_exact_owner' {
            $start = Get-Block $class 'public void StartSuspended()'
            Require-Order $start @('commandSession.Initialize();', 'IntPtr commandAdmission=commandSession.DuplicateAdmissionForLaunch();', 'long admissionNumber=commandAdmission.ToInt64();', 'admissionNumber<=0 || admissionNumber==Int64.MaxValue')
            Require-Sequence $start 'DuplicateAdmissionForLaunch(' 1
        }
        'private_handle_is_not_domain_input' {
            $start = Get-Block $class 'public void StartSuspended()'
            Require-Order $start @('foreach(string name in PlanKeys)', 'arguments.Add("-ParentStageNonce"); arguments.Add(Nonce);', 'arguments.Add("-ParentCommandSessionHandle"); arguments.Add(admissionNumber.ToString(CultureInfo.InvariantCulture));')
            Require-Sequence (Get-Block $class 'private static Dictionary<string,string> CopyPlan(') '"ParentCommandSessionHandle"' 0
            Require-Sequence $start 'copy["ParentCommandSessionHandle"]' 0
        }
        'full_argv_hash_includes_admission' {
            $start = Get-Block $class 'public void StartSuspended()'
            Require-Order $start @('arguments.Add("-ParentCommandSessionHandle");', 'StringBuilder command=new StringBuilder(Quote(executable));', 'foreach(string argument in arguments) command.Append', 'command.Length>32766', 'argvSha256=Files.Hash(Files.Utf8.GetBytes(command.ToString()));')
        }
        'exact_eight_inherited_handles' {
            $start = Get-Block $class 'public void StartSuspended()'
            Require-Sequence $start 'Hold(' 4
            Require-Sequence $start 'foreach(HeldFile file in held)' 1
            Require-Sequence $start 'List<IntPtr> handles=new List<IntPtr>();'
            Require-Sequence $start 'foreach(SourceDuplicateOutcome duplicate in sourceDuplicates)'
            Require-Sequence $start 'handles.Add(duplicate.Raw);'
            Require-Sequence $start 'handles.Add(' 5
            Require-Sequence $start 'handles.Add(input.ClientSafePipeHandle.DangerousGetHandle());'
            Require-Sequence $start 'handles.Add(output.ClientSafePipeHandle.DangerousGetHandle());'
            Require-Sequence $start 'handles.Add(error.ClientSafePipeHandle.DangerousGetHandle());'
            Require-Sequence $start 'handles.Add(commandAdmission);'
            Require-Order $start @('handles.Add(commandAdmission);', 'for(int i=0;i<handles.Count;i++) Marshal.WriteIntPtr(inheritList,i*IntPtr.Size,handles[i]);', 'new IntPtr(IntPtr.Size*handles.Count)')
        }
        'transition_is_not_assigned_to_command_job' {
            $start = Get-Block $class 'public void StartSuspended()'
            Require-Sequence $start 'InitializeProcThreadAttributeList(attributes,1,0,ref size)'
            Require-Sequence $start 'UpdateProcThreadAttribute(' 1
            Require-Sequence $start 'new IntPtr(0x20002)'
            foreach ($forbidden in @('0x2000D','AssignProcessToJobObject','TerminateJobObject','CreateJobObject')) { Require-Sequence $class $forbidden 0 }
        }
        'raw_creation_outputs_precede_success_check' {
            $start = Get-Block $class 'public void StartSuspended()'
            Require-Order $start @('ProcessInfo created=new ProcessInfo();', 'createAttempted=true;', 'bool createdOkay=CreateProcess(',
                'creationOutput=created; process=created.process; thread=created.thread; nativeProcessId=created.pid;',
                'createSucceeded=createdOkay; createReturned=true;', 'Check(createdOkay,"Create Transition guard suspended");')
        }
        'no_child_requires_all_raw_outputs_zero' {
            $kind = Get-Block $class 'internal CommandCreatorDisposition GetCommandCreatorDisposition()'
            Require-Sequence $kind 'bool zeroOutputs=creationOutput.process==IntPtr.Zero && creationOutput.thread==IntPtr.Zero && creationOutput.pid==0 && creationOutput.tid==0 && process==IntPtr.Zero && thread==IntPtr.Zero;'
            Require-Sequence $kind 'if(!resumeAttempted && zeroOutputs && (!createAttempted || (createReturned && !createSucceeded))) return CommandCreatorDisposition.NeverCreated;'
            Require-Sequence $kind 'if(disposed || startInProgress)'
        }
        'materialized_creator_requires_original_success' {
            $kind = Get-Block $class 'internal CommandCreatorDisposition GetCommandCreatorDisposition()'
            Require-Sequence $kind 'if(createAttempted && createReturned && createSucceeded && identityEstablished && process!=IntPtr.Zero && thread!=IntPtr.Zero && process==creationOutput.process && thread==creationOutput.thread && nativeProcessId==creationOutput.pid && nativeProcessId>0 && creationOutput.tid>0) return CommandCreatorDisposition.RetainedOriginal;'
            Require-Sequence $kind 'throw new InvalidOperationException("Ambiguous Transition creation or missing native identity must remain retained.");'
        }
        'abort_classifies_before_native_action' {
            $abort = Get-Block $class 'public void AbortBeforeResume()'
            Require-Order $abort @('if(resumeAttempted)', 'CommandCreatorDisposition disposition=GetCommandCreatorDisposition();',
                'if(disposition==CommandCreatorDisposition.NeverCreated) { AssertNoChildCreated(); return; }',
                'AssertOriginalCreationIdentity();', 'if(!IsAlive()) return;', 'uint previous=SuspendThread(thread);', 'TerminateProcess(process,202)')
        }
        'dispose_classifies_before_native_query' {
            $dispose = Get-Block $class 'public void Dispose()'
            Require-Order $dispose @('if(!commandSessionDisposed)', 'CommandCreatorDisposition disposition=GetCommandCreatorDisposition();',
                'if(disposition==CommandCreatorDisposition.NeverCreated) AssertNoChildCreated();', 'else if(IsAlive())', 'AbortBeforeResume();', 'PollCommandSessionRetirement()')
        }
        'native_start_failure_never_self_retires' {
            $start = Get-Block $class 'public void StartSuspended()'
            Require-Sequence $start 'if(failure!=null) { throw failure; }'
            Require-Sequence $start 'catch(Exception failure) { Record(failure); throw; }'
            foreach ($forbidden in @('AbortBeforeResume(', 'Dispose(', 'TerminateProcess(', 'TerminateJobObject(', 'PollCommandSessionRetirement(')) { Require-Sequence $start $forbidden 0 }
        }
        'bound_original_c_before_start_completion' {
            $start = Get-Block $class 'public void StartSuspended()'
            Require-Order $start @('AssertIdentity(); identityEstablished=true; AssertNoJob(); AssertClock(true);',
                'commandSession.BindCreator(); commandSession.CloseLaunchCopy();', 'CloseLocalClientEnds(); EnsureCollectors();',
                'AssertClock(true); ThrowFaults(); startCompleted=true;')
        }
        'resume_requires_original_c_admission' {
            $resume = Get-Block $class 'public void Resume()'
            Require-Sequence $resume 'if(!startCompleted || startInProgress || retirementRequested || releaseStarted)'
            Require-Order $resume @('commandSession.AssertCreatorBoundForResume();', 'AssertSuspended();', 'resumeAttempted=true;', 'ResumeThread(thread)')
        }
        'uncaptured_native_identity_is_unavailable' {
            Require-Sequence $class 'public int ProcessId { get { if(!identityEstablished) throw new InvalidOperationException("Transition native identity is not established."); return nativeProcessId; } }'
            Require-Sequence $class 'public string StartedAt { get { if(!identityEstablished) throw new InvalidOperationException("Transition native birth is not established."); return nativeStartedAt; } }'
        }
        'uncaptured_wire_metadata_is_unavailable' {
            Require-Sequence $class 'public string Nonce { get { if(nonce==null) throw new InvalidOperationException("Transition nonce is not captured."); return nonce; } }'
            Require-Sequence $class 'public string ArgvSha256 { get { if(argvSha256==null) throw new InvalidOperationException("Transition full argv is not captured."); return argvSha256; } }'
            Require-Sequence $class 'public string EnvironmentSha256 { get { if(environmentSha256==null) throw new InvalidOperationException("Transition environment is not captured."); return environmentSha256; } }'
        }
        'command_retirement_before_creator_release' {
            $dispose = Get-Block $class 'public void Dispose()'
            Require-Order $dispose @('PollCommandSessionRetirement()', 'commandSession.Dispose(); commandSessionDisposed=true;',
                'CloseLocalClientEnds(); EnsureCollectors();', 'foreach(IDisposable item', 'CloseHandle(thread)', 'CloseHandle(process)', 'disposed=true;')
        }
        'retirement_permanently_closes_start_admission' {
            $retire = Get-Block $class 'public bool PollCommandSessionRetirement()'
            Require-Order $retire @('lock(lifecycleGate)', 'if(disposed || startInProgress)', 'retirementRequested=true;',
                'if(commandSessionRetired) return true;', 'bool retired=commandSession.PollRetirement();', 'if(retired) commandSessionRetired=true;')
            Require-Sequence $class 'retirementRequested=false' 0
        }
        'source_duplicate_close_is_checked_and_retained' {
            Require-Sequence $class 'private readonly List<SourceDuplicateOutcome> sourceDuplicates=new List<SourceDuplicateOutcome>();'
            $close = Get-Block $class 'private void CloseSourceDuplicates()'
            Require-Sequence $close 'if(CloseHandle(duplicate.Raw)) duplicate.Closed=true;'
            Require-Sequence $close 'else failure=Files.Combine(failure,new Win32Exception(Marshal.GetLastWin32Error(),"Close parent Transition source duplicate"));'
            Require-Order $close @('foreach(SourceDuplicateOutcome duplicate in sourceDuplicates) AssertSourceDuplicateOutcome(duplicate);', 'if(CloseHandle(duplicate.Raw))')
            Require-Sequence $close 'if(failure!=null) throw failure;'
        }
        'local_pipe_end_closure_is_retry_bound' {
            $close = Get-Block $class 'private void CloseLocalClientEnds()'
            Require-Sequence $close 'if(output!=null && !outputClientClosed) { output.DisposeLocalCopyOfClientHandle(); outputClientClosed=true; }'
            Require-Sequence $close 'if(error!=null && !errorClientClosed) { error.DisposeLocalCopyOfClientHandle(); errorClientClosed=true; }'
            Require-Sequence $close 'if(input!=null && !inputClientClosed) { input.DisposeLocalCopyOfClientHandle(); inputClientClosed=true; }'
        }
        'actual_pipe_eof_before_capture_release' {
            $dispose = Get-Block $class 'public void Dispose()'
            Require-Order $dispose @('!outputThread.Join(2000)', '!errorThread.Join(2000)',
                'if((output!=null && !outputEof) || (error!=null && !errorEof))', 'CloseSourceDuplicates();', 'foreach(IDisposable item')
        }
        'native_mutations_share_original_lifecycle_gate' {
            foreach ($signature in @('public void StartSuspended()', 'public void AssertSuspended()', 'public void Resume()',
                'public void AbortBeforeResume()', 'public void Dispose()', 'public bool PollCommandSessionRetirement()')) {
                Require-Sequence (Get-Block $class $signature) 'lock(lifecycleGate)' 1
            }
        }
        'source_duplicate_outcome_rooted_before_native' {
            $start = Get-Block $class 'public void StartSuspended()'
            Require-Order $start @('SourceDuplicateOutcome outcome=new SourceDuplicateOutcome(); sourceDuplicates.Add(outcome);',
                'IntPtr duplicate=IntPtr.Zero; outcome.Attempted=true;', 'bool copied=DuplicateHandle(',
                'outcome.Raw=duplicate; outcome.Succeeded=copied; outcome.Returned=true;', 'Check(copied,"Duplicate Transition source read lock");')
        }
        'source_duplicate_ambiguity_never_becomes_handle' {
            $proof = Get-Block $class 'private static void AssertSourceDuplicateOutcome('
            Require-Sequence $proof 'duplicate==null || !duplicate.Attempted || !duplicate.Returned || (duplicate.Succeeded && (duplicate.Raw==IntPtr.Zero || duplicate.Raw==new IntPtr(-1))) || (!duplicate.Succeeded && duplicate.Raw!=IntPtr.Zero)'
        }
        'source_duplicate_proof_precedes_inheritance' {
            $start = Get-Block $class 'public void StartSuspended()'
            Require-Order $start @('foreach(SourceDuplicateOutcome duplicate in sourceDuplicates)', 'AssertSourceDuplicateOutcome(duplicate);',
                'if(!duplicate.Succeeded || duplicate.Closed)', 'handles.Add(duplicate.Raw);', 'UpdateProcThreadAttribute(')
        }
        'active_wait_requires_original_creation_identity' {
            Require-Order (Get-Block $class 'private bool IsAlive()') @('AssertOriginalCreationIdentity();', 'WaitForSingleObject(process,0)')
            $proof = Get-Block $class 'private void AssertOriginalCreationIdentity()'
            Require-Sequence $proof 'disposed || !createAttempted || !createReturned || !createSucceeded || !identityEstablished || process==IntPtr.Zero || thread==IntPtr.Zero || process!=creationOutput.process || thread!=creationOutput.thread || nativeProcessId<=0 || nativeProcessId!=creationOutput.pid || creationOutput.tid<=0 || nativeStartedAt==null'
        }
        'command_export_ambiguity_blocks_close' {
            $owner = Get-Block $tokens 'internal sealed class CommandSessionOwner : IDisposable'
            Require-Order (Get-Block $owner 'internal IntPtr DuplicateAdmissionForLaunch()') @('admissionAttempted=true;',
                'bool copied=CommandJobNative.DuplicateHandle(', 'launchCopy=copy; admissionSucceeded=copied; admissionReturned=true;',
                'CommandJobNative.Check(copied,"Duplicate restricted command admission");')
            Require-Order (Get-Block $owner 'internal void CloseLaunchCopy()') @('AssertAdmissionOutcomeKnown();', 'if(launchCopy==IntPtr.Zero)', 'CommandJobNative.CloseHandle(launchCopy)')
            Require-Sequence (Get-Block $owner 'private void AssertAdmissionOutcomeKnown()') 'admissionAttempted && (!admissionReturned || (admissionSucceeded && !admissionIssued) || (!admissionSucceeded && launchCopy!=IntPtr.Zero))'
        }
        'partial_release_retry_requires_private_closed_command_session' {
            $abort = Get-Block $class 'public void AbortBeforeResume()'
            Require-Order $abort @('if(startInProgress)', 'if(resumeAttempted) throw new InvalidOperationException("Any Transition Resume uncertainty permanently forbids abort.");',
                'if(commandSessionDisposed && releaseStarted) return;', 'CommandCreatorDisposition disposition=GetCommandCreatorDisposition();', 'AssertOriginalCreationIdentity();', 'if(!IsAlive()) return;')
            Require-Sequence $class 'commandSessionDisposed=true;' 1
            Require-Order (Get-Block $class 'public void Dispose()') @('releaseStarted=true;', 'PollCommandSessionRetirement()', 'commandSession.Dispose(); commandSessionDisposed=true;', 'CloseHandle(thread)', 'CloseHandle(process)')
        }
        default { throw 'Unknown Transition source contract.' }
    }
}

$cases = @(
    @('separate_no_job_no_reopen', 'public sealed class IndependentTransitionLaunch : IDisposable {', 'public sealed class IndependentTransitionLaunch : IDisposable { private void Invalid(){ CreateJobObject(); }'),
    @('fixed_create_flags', 'true,0x08080404,environmentMemory,root', 'true,0x09080404,environmentMemory,root'),
    @('exact_nineteen_plan', '"StartCompletionPath","PostgresContainer"};', '"StartCompletionPath","Container"};'),
    @('fixed_argv_private_nonce', 'arguments.Add("-ParentStageNonce"); arguments.Add(Nonce);', 'arguments.Add("-ParentStageNonce"); arguments.Add(copy["SessionId"]);'),
    @('fixed_sources_and_four_holds', 'Hold(runtime,runtimeHash,2097152);', 'Hold(runtime,runtimeHash,4194304);'),
    @('early_fence_not_opened', 'AssertClock(true);', 'Files.ReadBounded(copy["StartFencePath"],262144); AssertClock(true);'),
    @('path_alias_rejection', "path.IndexOf(':',2)>=0", '$false'),
    @('environment_explicit_allowlist', '"CommonProgramFiles","ComSpec","SystemDrive"', '"CommonProgramFiles","ComSpec","DOTNET_STARTUP_HOOKS"'),
    @('held_bounds_before_hash', 'stream.Length<1 || stream.Length>maximum', 'stream.Length<1'),
    @('inherited_exact_locks_and_pipes', 'handles.Add(duplicate.Raw);', ''),
    @('any_job_rechecked', 'IsProcessInJob(process,IntPtr.Zero,out member)', 'IsProcessInJob(process,process,out member)'),
    @('one_original_deadline', 'maximumLifetimeMilliseconds>600000', 'maximumLifetimeMilliseconds>1200000'),
    @('suspend_proof_uncertainty_latched', 'if(previous!=1)', 'if(previous!=0)'),
    @('resume_latch_before_kernel', 'resumeAttempted=true; // A possible Resume irreversibly forbids abort.', 'resumeAttempted=false; // Deliberately broken negative control.'),
    @('abort_never_resumed_exact_handle', 'if(resumeAttempted) throw new InvalidOperationException("Any Transition Resume uncertainty permanently forbids abort.");', ''),
    @('dispose_retains_ambiguity', 'resumeAttempted && terminalProof==null', 'false'),
    @('bounded_drain_never_kills', 'Math.Min((long)maximum+1,before+count)', 'before+count'),
    @('stderr_fault_is_latched', 'if(!stdout && before==0) Record(new InvalidDataException("Transition guard wrote stderr."));', ''),
    @('live_before_after_identity', 'if(!IsAlive()) throw new InvalidOperationException("Transition exited during live proof.");', ''),
    @('ready_immutable_private_proof', 'public readonly string Text,Sha256,StartedAt,SessionId,Nonce,LeasePath,LeaseSha256;', 'public string Text,Sha256,StartedAt,SessionId,Nonce,LeasePath,LeaseSha256;'),
    @('terminal_immutable_private_proof', 'public readonly ReadyProof Ready;', 'public ReadyProof Ready;'),
    @('frame_limits_before_decode', 'ReadyMaximumBytes=8192, CompleteMaximumBytes=8192', 'ReadyMaximumBytes=65536, CompleteMaximumBytes=65536'),
    @('exact_frame_fields_and_native_ints', 'BoundedInteger(number,1,name=="pid" ? Int32.MaxValue : 262144)', 'BoundedInteger(number,0,Int32.MaxValue)'),
    @('frames_bound_to_private_launch', 'values["nonce"]!=Nonce', 'values["nonce"]==null'),
    @('frame_scan_bounded_before_copy', 'i<Math.Min(available,maximum)', 'i<available'),
    @('ready_live_before_publication', 'if(!IsAlive()) throw new InvalidOperationException("Transition exited before READY capability publication.");', ''),
    @('terminal_requires_ready_no_begin', '!resumeSucceeded || readyProof==null', '!resumeSucceeded'),
    @('terminal_exact_second_frame', 'if(outputCount!=first.Length+complete.Length)', 'if(false)'),
    @('terminal_exit_and_collectors', 'if(!outputThread.Join(0) || !errorThread.Join(0))', 'if(false)'),
    @('polling_cannot_retire_guard', 'public void AssertReadyLive() {', 'public void AssertReadyLive() { Dispose();'),
    @('managed_prepare_before_ownership', 'launchPlan=CopyPlan(plan); root=launchPlan["Root"];', 'Files.RegularAncestry(executable,true); launchPlan=CopyPlan(plan); root=launchPlan["Root"];'),
    @('original_command_owner_is_readonly', 'commandSession=new CommandSessionOwner(this);', 'commandSession=new CommandSessionOwner(null);'),
    @('start_admission_is_single_use', 'disposed || releaseStarted || retirementRequested || startAttempted', 'disposed || releaseStarted || retirementRequested'),
    @('command_export_is_exact_owner', 'IntPtr commandAdmission=commandSession.DuplicateAdmissionForLaunch();', 'IntPtr commandAdmission=IntPtr.Zero;'),
    @('private_handle_is_not_domain_input', 'arguments.Add(admissionNumber.ToString(CultureInfo.InvariantCulture));', 'arguments.Add(copy["ParentCommandSessionHandle"]);'),
    @('full_argv_hash_includes_admission', 'argvSha256=Files.Hash(Files.Utf8.GetBytes(command.ToString()));', 'argvSha256=Files.Hash(Files.Utf8.GetBytes(source));'),
    @('exact_eight_inherited_handles', 'handles.Add(commandAdmission);', ''),
    @('transition_is_not_assigned_to_command_job', 'new IntPtr(0x20002),inheritList', 'new IntPtr(0x2000D),inheritList'),
    @('raw_creation_outputs_precede_success_check', 'createSucceeded=createdOkay; createReturned=true;', 'createSucceeded=createdOkay;'),
    @('no_child_requires_all_raw_outputs_zero', 'creationOutput.pid==0 && creationOutput.tid==0 && process==IntPtr.Zero', 'process==IntPtr.Zero'),
    @('materialized_creator_requires_original_success', 'if(createAttempted && createReturned && createSucceeded && identityEstablished &&', 'if(createAttempted && createReturned && identityEstablished &&'),
    @('abort_classifies_before_native_action', 'AssertOriginalCreationIdentity();', ''),
    @('dispose_classifies_before_native_query', 'CommandCreatorDisposition disposition=GetCommandCreatorDisposition();', 'CommandCreatorDisposition disposition=CommandCreatorDisposition.NeverCreated;'),
    @('native_start_failure_never_self_retires', 'startCompleted=true;', 'Dispose(); startCompleted=true;'),
    @('bound_original_c_before_start_completion', 'commandSession.BindCreator(); commandSession.CloseLaunchCopy();', 'commandSession.CloseLaunchCopy();'),
    @('resume_requires_original_c_admission', 'commandSession.AssertCreatorBoundForResume();', ''),
    @('uncaptured_native_identity_is_unavailable', 'if(!identityEstablished) throw new InvalidOperationException("Transition native identity is not established.");', ''),
    @('uncaptured_wire_metadata_is_unavailable', 'if(argvSha256==null) throw new InvalidOperationException("Transition full argv is not captured.");', ''),
    @('command_retirement_before_creator_release', 'commandSession.Dispose(); commandSessionDisposed=true;', 'commandSessionDisposed=true;'),
    @('retirement_permanently_closes_start_admission', 'retirementRequested=true;', 'retirementRequested=false;'),
    @('source_duplicate_close_is_checked_and_retained', 'if(CloseHandle(duplicate.Raw)) duplicate.Closed=true;', 'CloseHandle(duplicate.Raw); duplicate.Closed=true;'),
    @('local_pipe_end_closure_is_retry_bound', 'output.DisposeLocalCopyOfClientHandle(); outputClientClosed=true;', 'outputClientClosed=true; output.DisposeLocalCopyOfClientHandle();'),
    @('actual_pipe_eof_before_capture_release', 'if((output!=null && !outputEof) || (error!=null && !errorEof))', 'if(false)'),
    @('native_mutations_share_original_lifecycle_gate', 'lock(lifecycleGate)', 'lock(captureGate)'),
    @('source_duplicate_outcome_rooted_before_native', 'sourceDuplicates.Add(outcome);', ''),
    @('source_duplicate_ambiguity_never_becomes_handle', '(!duplicate.Succeeded && duplicate.Raw!=IntPtr.Zero)', 'false'),
    @('source_duplicate_proof_precedes_inheritance', 'AssertSourceDuplicateOutcome(duplicate);', ''),
    @('active_wait_requires_original_creation_identity', 'AssertOriginalCreationIdentity();', ''),
    @('command_export_ambiguity_blocks_close', 'AssertAdmissionOutcomeKnown();', ''),
    @('partial_release_retry_requires_private_closed_command_session', 'if(commandSessionDisposed && releaseStarted) return;', 'if(commandSessionDisposed || releaseStarted) return;'),
    @('suspend_prior_count_guard_cannot_be_removed', 'if(previous!=1) throw new InvalidOperationException("Transition suspension was disproved; retain the inspection increment.");', 'if(false) throw new InvalidOperationException("Transition suspension was disproved; retain the inspection increment.");'),
    @('suspend_native_failure_guard_precedes_restore', 'Check(previous!=UInt32.MaxValue,"Transition suspended count");', 'Check(true,"Transition suspended count");'),
    @('suspend_restore_cannot_precede_count_guard', 'if(previous!=1) throw new InvalidOperationException("Transition suspension was disproved; retain the inspection increment.");', 'if(ResumeThread(thread)==0 || previous!=1) throw new InvalidOperationException("Transition suspension was disproved; retain the inspection increment.");'),
    @('suspend_restored_count_guard_cannot_be_removed', 'if(restored!=2) throw new InvalidOperationException("Transition suspension restoration was ambiguous.");', 'if(false) throw new InvalidOperationException("Transition suspension restoration was ambiguous.");'),
    @('abort_never_restores_disproved_suspension', 'Exception failure=new InvalidOperationException("Transition abort lacks exact never-resumed suspended proof.");', 'ResumeThread(thread); Exception failure=new InvalidOperationException("Transition abort lacks exact never-resumed suspended proof.");'),
    @('abort_wrongcount_latch_is_mandatory', 'resumeAttempted=true;', ''),
    @('suspend_sole_restore_must_follow_count_guard', 'uint restored=ResumeThread(thread);', 'uint restored=ResumeThread(thread);', 'if(previous!=1) throw new InvalidOperationException("Transition suspension was disproved; retain the inspection increment.");')
)
foreach ($case in $cases) {
    Assert-TransitionContract $case[0] $classSource
    $results.Add([pscustomobject]@{name=$case[0];negative_control=$false;passed=$true})
    Assert-Source ($classSource.Contains($case[1])) ('Missing negative mutation target: ' + $case[0])
    if ($case.Count -eq 4) {
        # Preserve one restoring call while moving it before the prior-count
        # rejection, so only order (not extra-call count) detects this regression.
        Assert-Source ($classSource.Contains($case[3])) ('Missing move anchor: ' + $case[0])
        $changed = $classSource.Replace($case[1], '').Replace($case[3], ($case[2] + ' ' + $case[3]))
    } else { $changed = $classSource.Replace($case[1], $case[2]) }
    $rejected = $false
    try { Assert-TransitionContract $case[0] $changed } catch { $rejected = $true }
    Assert-Source $rejected ('Negative control did not reject: ' + $case[0])
    $results.Add([pscustomobject]@{name=$case[0];negative_control=$true;passed=$true})
}
$expected = 134
Assert-Source ($results.Count -eq $expected -and $cases.Count -eq 67) 'Fixed Transition source case count differs.'
[pscustomobject]@{suite='independent-transition-native-source-contracts';expected=$expected;executed=$results.Count;passed=$results.Count;failed=0
    source_sha256=(Get-FileHash -LiteralPath $RuntimePath -Algorithm SHA256).Hash.ToLowerInvariant()
    static_source_only=$true;csharp_semantic_ast=$false;native_compiled=$false;native_executed=$false
    process_or_docker_executed=$false;full_protocol_accepted=$false;results=@($results.ToArray())} | ConvertTo-Json -Depth 5 -Compress
