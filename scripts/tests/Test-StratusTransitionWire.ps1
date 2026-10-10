[CmdletBinding()]
param([string] $WatchdogPath)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $PSBoundParameters.ContainsKey('WatchdogPath')) { $WatchdogPath = Join-Path $PSScriptRoot '..\Invoke-StratusSuccessorTransitionWatchdog.ps1' }
$source = [IO.File]::ReadAllText([IO.Path]::GetFullPath($WatchdogPath))
$tokens = $null; $parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$parseErrors)
$expectedCases = 38; $script:executed = 0; $script:passed = 0
$script:failures = [Collections.Generic.List[string]]::new()
function Assert-Test { param([bool] $Condition, [string] $Message) if (-not $Condition) { throw $Message } }
function Invoke-Case {
    param([string] $Name, [scriptblock] $Action)
    $script:executed++
    try { & $Action; $script:passed++; Write-Output ('PASS transition-wire: ' + $Name) }
    catch { $script:failures.Add($Name + ': ' + $_.Exception.GetBaseException().Message); Write-Output ('FAIL transition-wire: ' + $Name) }
}
function Assert-Rejected {
    param([scriptblock] $Action)
    $rejected = $false
    try { & $Action | Out-Null } catch { $rejected = $true }
    Assert-Test $rejected 'Invalid pure wire input unexpectedly succeeded.'
}
function Get-FunctionAst {
    param([string] $Name)
    $found = @($ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $Name }, $false))
    Assert-Test ($found.Count -eq 1) ('Expected one function: ' + $Name)
    return $found[0]
}
function Test-ModePresence {
    param([string] $Text)
    return ($Text.Contains('$script:parentTransitionMode = $PSBoundParameters.ContainsKey(''ParentStageNonce'')') -and
        $Text.Contains('if ($script:parentTransitionMode -and -not [regex]::IsMatch($ParentStageNonce, ''\A[0-9a-f]{64}\z''))') -and
        $Text.Contains('throw ''Parent Transition mode requires an explicit lowercase 64-hex nonce.'''))
}
function Test-LeasePublicationOrder {
    param([string] $Text)
    $write = $Text.IndexOf('$leaseResult = Write-JsonToReservedStream -Stream $leaseStream -Value $lease')
    $hash = $Text.IndexOf('$script:leaseSha256 = $leaseResult.sha256', $write)
    $armed = $Text.IndexOf('$script:leaseArmed = $true', $write)
    $close = $Text.IndexOf('if ($null -ne $leaseStream) { $leaseStream.Dispose() }', $write)
    $frame = $Text.IndexOf('$readyFrame = New-ParentTransitionReadyFrameBytes', $write)
    return ($write -ge 0 -and $hash -gt $write -and $armed -gt $hash -and $close -gt $armed -and $frame -gt $close -and
        $Text.Contains('-StartedAt $script:parentTransitionStartedAt -LeasePath $absoluteLeasePath -LeaseSha256 $leaseResult.sha256'))
}
function Test-TerminalWireBindings {
    param([string] $Text)
    return ($Text.Contains('-RunId $SessionId -Nonce $ParentStageNonce -ProcessId $PID') -and
        $Text.Contains('-StartedAt $script:parentTransitionStartedAt -ReceiptPath $absoluteReceiptPath') -and
        $Text.Contains('-ReceiptSha256 $Terminal.sha256 -ReceiptBytes $Terminal.byte_count') -and
        $Text.Contains('$script:parentTransitionTerminalAttempted = $true') -and
        $Text.IndexOf('$script:parentTransitionTerminalAttempted = $true') -lt $Text.IndexOf('$stdout.Write($frame, 0, $frame.Length)'))
}
Invoke-Case 'producer parses and explicit invalid nonce cannot select legacy mode' {
    Assert-Test (@($parseErrors).Count -eq 0 -and (Test-ModePresence $source)) 'Parent mode selection or syntax differs.'
}
Invoke-Case 'only four reviewed pure managed helpers are eligible for extraction' {
    foreach ($name in @('Get-Sha256Bytes', 'Assert-ParentTransitionWireIdentity', 'New-ParentTransitionReadyFrameBytes', 'New-ParentTransitionCompleteFrameBytes')) {
        $fn = Get-FunctionAst $name
        foreach ($command in @($fn.FindAll({ param($node) $node -is [Management.Automation.Language.CommandAst] }, $true))) {
            Assert-Test ($command.InvocationOperator -eq [Management.Automation.Language.TokenKind]::Unknown -and
                $command.GetCommandName() -cin @('ConvertTo-Json', 'Assert-ParentTransitionWireIdentity')) ('Unreviewed pure-helper command: ' + $command.Extent.Text)
        }
        foreach ($type in @($fn.FindAll({ param($node) $node -is [Management.Automation.Language.TypeExpressionAst] }, $true))) {
            Assert-Test ($type.TypeName.FullName -cin @('Security.Cryptography.SHA256', 'BitConverter', 'string', 'regex', 'int', 'long',
                'DateTimeOffset', 'Globalization.CultureInfo', 'TimeSpan', 'IO.Path', 'Text.UTF8Encoding')) ('Unreviewed pure-helper type: ' + $type.TypeName.FullName)
        }
    }
}
Invoke-Case 'parent raw input paths and every ancestor are checked before fence reservations or Docker' {
    $calls = @($ast.FindAll({ param($node) $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -ceq 'Assert-ParentTransitionWirePaths' }, $true))
    Assert-Test ($calls.Count -eq 1 -and $calls[0].Extent.Text.Contains('-RootPath $Root -ArtifactPaths @($LeasePath, $ReceiptPath, $HeartbeatPath,') -and
        $calls[0].Extent.Text.Contains('$StartAttemptPath, $StartCompletionPath)')) 'Actual raw path guard is missing.'
    foreach ($later in @('$initialStartFenceLock = Open-ExclusiveStartFenceOnce', '$script:receiptStream = [IO.FileStream]::new(', '$docker = Resolve-AndLockApprovedDocker')) {
        Assert-Test ($source.IndexOf($later) -gt $calls[0].Extent.EndOffset) ('Path guard follows actual operation: ' + $later)
    }
    foreach ($anchor in @('Assert-ParentTransitionWireIdentity $SessionId $ParentStageNonce $PID $script:parentTransitionStartedAt $PreStartAttestationPath $ExpectedPreStartSha256',
        'Assert-ParentTransitionWireIdentity $SessionId $ParentStageNonce $PID $script:parentTransitionStartedAt $StartFencePath $ExpectedStartFenceSha256')) {
        Assert-Test ($source.IndexOf($anchor) -gt $calls[0].Extent.EndOffset -and $source.IndexOf($anchor) -lt $source.IndexOf('$initialStartFenceLock = Open-ExclusiveStartFenceOnce')) 'Parent identity/digest anchors are not strict before resource operations.'
    }
    $body = (Get-FunctionAst 'Assert-ParentTransitionWirePaths').Extent.Text
    foreach ($required in @('[IO.Path]::GetFullPath($RootPath) -cne $RootPath', '[StringComparer]::OrdinalIgnoreCase', '-not $seen.Add($path)',
        'Get-Item -LiteralPath $cursor -Force -ErrorAction Stop', '[IO.FileAttributes]::ReparsePoint',
        'catch [Management.Automation.ItemNotFoundException]', 'if (-not $leaf)', '$cursor = [IO.Path]::GetDirectoryName($cursor)', '$seen.Count -ne 10')) {
        Assert-Test ($body.Contains($required)) ('Missing ancestry/path rule: ' + $required)
    }
}
Invoke-Case 'parent frames and lease use actual own process birth instead of watched gate birth' {
    $birth = $source.IndexOf('$ownProcess = [Diagnostics.Process]::GetCurrentProcess()')
    Assert-Test ($birth -gt 0 -and $birth -lt $source.IndexOf('$script:receiptStream = [IO.FileStream]::new(') -and
        $source.Contains('$script:parentTransitionStartedAt = [DateTimeOffset]::new($ownProcess.StartTime.ToUniversalTime(), [TimeSpan]::Zero).ToString(''o'')') -and
        $source.Contains('process_started_at = if ($script:parentTransitionMode) { $script:parentTransitionStartedAt } else')) 'Own process birth is not independently obtained for parent wire.'
}
Invoke-Case 'ready follows actual preflight lease bytes arming and writer close' {
    Assert-Test (Test-LeasePublicationOrder $source) 'Lease publication/ready ordering differs.'
    $write = $source.IndexOf('$leaseResult = Write-JsonToReservedStream')
    foreach ($before in @('$preStartProof = Assert-PreStartEnvelope', '$script:initialPostgres = Get-PostgresBoundary -State Stopped', '$armedAt -ge $hardDeadline')) {
        Assert-Test ($source.IndexOf($before) -gt 0 -and $source.IndexOf($before) -lt $write) ('Ready predates preflight: ' + $before)
    }
    Assert-Test ($source.Contains('$stdout.Write($readyFrame, 0, $readyFrame.Length)') -and $source.Contains('$stdout.Flush()')) 'Ready is not emitted as raw bytes.'
}
Invoke-Case 'reserved artifact bytes are bounded before write flushed and used for receipt metadata' {
    $writer = (Get-FunctionAst 'Write-JsonToReservedStream').Extent.Text
    Assert-Test ($writer.Contains('$Stream.Length -ne 0 -or $Stream.Position -ne 0') -and
        $writer.Contains('if ($script:parentTransitionMode -and $bytes.Length -gt 262144)') -and
        $writer.IndexOf('$bytes.Length -gt 262144') -lt $writer.IndexOf('$Stream.Write($bytes, 0, $bytes.Length)') -and
        $writer.Contains('$Stream.Flush($true)') -and $writer.Contains('sha256 = Get-Sha256Bytes -Bytes $bytes')) 'Actual durable byte publication differs.'
    $terminal = (Get-FunctionAst 'Write-TerminalReceipt').Extent.Text
    Assert-Test ($terminal.Contains('byte_count = [int]$result.bytes.Length') -and $terminal.Contains('sha256 = $result.sha256') -and
        $terminal.IndexOf('$result = Write-JsonToReservedStream') -lt $terminal.IndexOf('$script:terminalReceiptWritten = $true')) 'Receipt metadata is not based on its written bytes.'
}
Invoke-Case 'all six success contained and failure stdout sites use the single terminal wrapper' {
    $calls = @($ast.FindAll({ param($node) $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -ceq 'Write-TransitionTerminalOutput' }, $true))
    Assert-Test ($calls.Count -eq 6 -and @($calls | Where-Object { $_.Extent.Text -ceq 'Write-TransitionTerminalOutput $terminal' }).Count -eq 1 -and
        @($calls | Where-Object { $_.Extent.Text -ceq 'Write-TransitionTerminalOutput $completion.terminal' }).Count -eq 2 -and
        @($calls | Where-Object { $_.Extent.Text -ceq 'Write-TransitionTerminalOutput $terminalFailure' }).Count -eq 3) 'A terminal branch bypasses the parent wire.'
    $fn = Get-FunctionAst 'Write-TransitionTerminalOutput'
    Assert-Test ($fn.Extent.Text.Contains('if (-not $script:parentTransitionMode) { $Terminal.json; return }') -and
        -not $source.Contains('$completion.terminal.json') -and -not $source.Contains('$terminalFailure.json')) 'Legacy or parent terminal routing differs.'
}
Invoke-Case 'terminal wire binds actual receipt metadata and latches before raw stdout publication' {
    $fn = (Get-FunctionAst 'Write-TransitionTerminalOutput').Extent.Text
    Assert-Test ((Test-TerminalWireBindings $fn) -and $fn.Contains('-not $script:terminalReceiptWritten -or $script:parentTransitionTerminalAttempted') -and
        $fn.Contains('$stdout = [Console]::OpenStandardOutput()') -and $fn.Contains('$stdout.Flush()')) 'Terminal raw-byte identity/publication rule differs.'
}
Invoke-Case 'cleanup failure exit3 and stderr remain after terminal publication' {
    Assert-Test ($source.Contains('if ($null -ne $terminalHandlingFailure -or $script:cleanupFailures.Count -gt 0)') -and
        $source.Contains('Watchdog failed without masking earlier state.') -and $source.Contains('-ErrorAction Continue') -and
        $source.LastIndexOf('$exitCode = 3') -gt $source.LastIndexOf('$script:receiptStream.Dispose()') -and
        $source.TrimEnd().EndsWith('exit $exitCode', [StringComparison]::Ordinal)) 'Terminal frame masks final cleanup failure or exit code.'
}
Invoke-Case 'terminal authorization and existing fixed guard limits remain fail closed' {
    $fn = (Get-FunctionAst 'Write-TerminalReceipt').Extent.Text
    foreach ($field in @('api_stage_permitted', 'worker_start_permitted', 'safety_control_start_permitted', 'live_transition_permitted', 'binance_kill_switch_removal_permitted', 'migration_container_permitted')) {
        Assert-Test ($fn.Contains($field + ' = $false')) ('Terminal authority was widened: ' + $field)
    }
    foreach ($required in @('$maximumLeaseSeconds = 600', '$containmentStopTimeoutSeconds = 15', '$dockerStopWallClockTimeoutSeconds = 25',
        '$expectedStartProcessTimeoutMilliseconds = 15000', '$clockSkewSeconds = 2', '$pollMilliseconds = 200')) {
        Assert-Test ($source.Contains($required)) ('Existing guard limit changed: ' + $required)
    }
}
Invoke-Case 'negative control rejects a nonce presence fallback regression' {
    Assert-Test (-not (Test-ModePresence ($source.Replace('$PSBoundParameters.ContainsKey(''ParentStageNonce'')', '$false')))) 'Presence negative control failed.'
}
Invoke-Case 'negative control rejects lease digest not taken from the held writer result' {
    Assert-Test (-not (Test-LeasePublicationOrder ($source.Replace('-LeaseSha256 $leaseResult.sha256', '-LeaseSha256 $ExpectedPreStartSha256')))) 'Lease hash negative control failed.'
}
Invoke-Case 'negative control rejects ready before the lease writer is closed' {
    Assert-Test (-not (Test-LeasePublicationOrder ($source.Replace('if ($null -ne $leaseStream) { $leaseStream.Dispose() }', '$null')))) 'Lease close negative control failed.'
}
Invoke-Case 'negative control rejects terminal count not taken from actual serialized receipt bytes' {
    $fn = (Get-FunctionAst 'Write-TransitionTerminalOutput').Extent.Text
    Assert-Test (-not (Test-TerminalWireBindings ($fn.Replace('-ReceiptBytes $Terminal.byte_count', '-ReceiptBytes 1')))) 'Receipt byte count negative control failed.'
}
if ($script:failures.Count -ne 0) { throw ('Safe extraction rejected: ' + ($script:failures -join '; ')) }
# NEVER dot-source the watchdog or invoke its body, writers, ancestry guard, Docker,
# native runtime, process/Job APIs, runspaces, or historical fault experiments.
# The only extracted functions below operate on scalar values and managed bytes.
foreach ($name in @('Get-Sha256Bytes', 'Assert-ParentTransitionWireIdentity', 'New-ParentTransitionReadyFrameBytes', 'New-ParentTransitionCompleteFrameBytes')) {
    . ([scriptblock]::Create((Get-FunctionAst $name).Extent.Text))
}
$readyFields = @{ RunId = 'a' * 32; Nonce = 'b' * 64; ProcessId = [int]1234; StartedAt = '2026-09-15T12:34:56.1234567+00:00'
    LeasePath = 'C:\stratus\transition-lease.json'; LeaseSha256 = 'c' * 64 }
$completeFields = @{ RunId = $readyFields.RunId; Nonce = $readyFields.Nonce; ProcessId = $readyFields.ProcessId; StartedAt = $readyFields.StartedAt
    ReceiptPath = 'C:\stratus\transition-terminal.json'; ReceiptSha256 = 'd' * 64; ReceiptBytes = [long]12345 }
function New-Ready { return New-ParentTransitionReadyFrameBytes @readyFields }
function New-Complete { return New-ParentTransitionCompleteFrameBytes @completeFields }
function Assert-BadField {
    param([hashtable] $Fields, [string] $Name, [object[]] $Values, [scriptblock] $Make)
    $original = $Fields[$Name]
    try { foreach ($value in $Values) { $Fields[$Name] = $value; Assert-Rejected $Make } }
    finally { $Fields[$Name] = $original }
}
function Read-TestJson {
    param([byte[]] $Bytes)
    $parameters = @{ InputObject = [Text.UTF8Encoding]::new($false, $true).GetString($Bytes) }
    if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) { $parameters.DateKind = 'String' }
    return ConvertFrom-Json @parameters
}
Invoke-Case 'ready frame contains exactly seven ordered launch and lease bindings' {
    $frame = Read-TestJson (New-Ready)
    Assert-Test (($frame.PSObject.Properties.Name -join ',') -ceq 'format,session_id,nonce,pid,started_at,lease_path,lease_sha256' -and
        $frame.format -ceq 'stratus-parent-transition-ready-v1' -and $frame.session_id -ceq $readyFields.RunId -and
        $frame.nonce -ceq $readyFields.Nonce -and $frame.started_at -ceq $readyFields.StartedAt -and
        $frame.lease_path -ceq $readyFields.LeasePath -and $frame.lease_sha256 -ceq $readyFields.LeaseSha256) 'Ready exact schema/bindings differ.'
}
Invoke-Case 'complete frame contains exactly eight ordered launch and actual receipt bindings' {
    $frame = Read-TestJson (New-Complete)
    Assert-Test (($frame.PSObject.Properties.Name -join ',') -ceq 'format,session_id,nonce,pid,started_at,receipt_path,receipt_sha256,receipt_bytes' -and
        $frame.format -ceq 'stratus-parent-transition-complete-v1' -and $frame.session_id -ceq $completeFields.RunId -and
        $frame.nonce -ceq $completeFields.Nonce -and $frame.started_at -ceq $completeFields.StartedAt -and
        $frame.receipt_path -ceq $completeFields.ReceiptPath -and $frame.receipt_sha256 -ceq $completeFields.ReceiptSha256 -and
        $frame.receipt_bytes -eq $completeFields.ReceiptBytes) 'Complete exact schema/bindings differ.'
}
Invoke-Case 'ready is bounded strict UTF8 without BOM CR or extra lines' {
    $bytes = New-Ready; $text = [Text.UTF8Encoding]::new($false, $true).GetString($bytes)
    Assert-Test ($bytes.Length -le 8192 -and $bytes[0] -eq 123 -and $bytes[-1] -eq 10 -and
        @($bytes | Where-Object { $_ -eq 10 }).Count -eq 1 -and -not $text.Contains("`r")) 'Ready framing differs.'
}
Invoke-Case 'complete is bounded strict UTF8 without BOM CR or extra lines' {
    $bytes = New-Complete; $text = [Text.UTF8Encoding]::new($false, $true).GetString($bytes)
    Assert-Test ($bytes.Length -le 8192 -and $bytes[0] -eq 123 -and $bytes[-1] -eq 10 -and
        @($bytes | Where-Object { $_ -eq 10 }).Count -eq 1 -and -not $text.Contains("`r")) 'Complete framing differs.'
}
Invoke-Case 'both wire PIDs are serialized as native JSON integers' {
    foreach ($bytes in @((New-Ready), (New-Complete))) {
        $text = [Text.UTF8Encoding]::new($false, $true).GetString($bytes)
        Assert-Test ($text.Contains('"pid":1234,') -and -not $text.Contains('"pid":"')) 'PID is not an integer.'
    }
}
Invoke-Case 'receipt byte count is serialized as a native JSON integer' {
    $text = [Text.UTF8Encoding]::new($false, $true).GetString((New-Complete))
    Assert-Test ($text.Contains('"receipt_bytes":12345}') -and -not $text.Contains('"receipt_bytes":"')) 'Receipt byte count is not an integer.'
}
Invoke-Case 'actual byte digest helper matches known SHA256' {
    Assert-Test ((Get-Sha256Bytes ([Text.Encoding]::UTF8.GetBytes('abc'))) -ceq 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad') 'Byte hash helper differs.'
}
Invoke-Case 'ready session rejects case width newline and nonstring errors' { Assert-BadField $readyFields RunId @('', ('A' * 32), ('a' * 31), (('a' * 32) + "`n"), 123, $null) { New-Ready } }
Invoke-Case 'ready nonce rejects explicit empty case width and nonstring values' { Assert-BadField $readyFields Nonce @('', ('B' * 64), ('b' * 63), (('b' * 64) + "`n"), $false, $null) { New-Ready } }
Invoke-Case 'ready lease digest rejects malformed native hashes' { Assert-BadField $readyFields LeaseSha256 @('', ('C' * 64), ('c' * 65), $true, $null) { New-Ready } }
Invoke-Case 'both frames reject coercible and invalid process IDs' {
    $bad = @('1234', $true, 1234.0, 0, -1, ([long][int]::MaxValue + 1), $null)
    Assert-BadField $readyFields ProcessId $bad { New-Ready }; Assert-BadField $completeFields ProcessId $bad { New-Complete }
}
Invoke-Case 'ready process birth rejects noncanonical nonUTC and nonstring values' { Assert-BadField $readyFields StartedAt @($null, [DateTimeOffset]::UtcNow, '2026-09-15T12:34:56Z', '2026-09-15T12:34:56.1234567+01:00') { New-Ready } }
Invoke-Case 'ready lease path rejects relative and noncanonical paths' { Assert-BadField $readyFields LeasePath @('lease.json', 'C:\stratus\.\lease.json', '', $null) { New-Ready } }
Invoke-Case 'ready lease path rejects CR LF and NUL injection' { Assert-BadField $readyFields LeasePath @("C:\stratus\lease`r.json", "C:\stratus\lease`n.json", ('C:\stratus\lease' + [char]0 + '.json')) { New-Ready } }
Invoke-Case 'both frame types reject oversized paths before unbounded output' {
    $path = 'C:\stratus\' + ('x' * 9000) + '.json'
    Assert-BadField $readyFields LeasePath @($path) { New-Ready }; Assert-BadField $completeFields ReceiptPath @($path) { New-Complete }
}
Invoke-Case 'complete session rejects malformed or supplied nonstring identities' { Assert-BadField $completeFields RunId @('', ('A' * 32), ('a' * 31), $true, $null) { New-Complete } }
Invoke-Case 'complete nonce rejects empty malformed and trailing-control values' { Assert-BadField $completeFields Nonce @('', ('B' * 64), (('b' * 64) + "`n"), $null) { New-Complete } }
Invoke-Case 'complete receipt digest rejects case width and type ambiguity' { Assert-BadField $completeFields ReceiptSha256 @('', ('D' * 64), ('d' * 63), 1, $null) { New-Complete } }
Invoke-Case 'complete birth rejects native dates nonUTC and noncanonical strings' { Assert-BadField $completeFields StartedAt @([DateTimeOffset]::UtcNow, '2026-09-15T12:34:56Z', '2026-09-15T12:34:56.1234567+01:00', $null) { New-Complete } }
Invoke-Case 'complete path rejects relative noncanonical and control values' { Assert-BadField $completeFields ReceiptPath @('receipt.json', 'C:\stratus\.\receipt.json', "C:\stratus\receipt`n.json", $null) { New-Complete } }
Invoke-Case 'receipt size rejects strings booleans floats nonpositive and oversized values' { Assert-BadField $completeFields ReceiptBytes @('12345', $true, 12345.0, 0, -1, [long]262145, $null) { New-Complete } }
Invoke-Case 'receipt size accepts exact one-byte and maximum bounds' {
    $original = $completeFields.ReceiptBytes
    try { foreach ($count in @([int]1, [long]262144)) { $completeFields.ReceiptBytes = $count; Assert-Test ((New-Complete).Length -gt 0) 'Exact receipt size rejected.' } }
    finally { $completeFields.ReceiptBytes = $original }
}
Invoke-Case 'changing nonce changes exact ready capture bytes' {
    $first = New-Ready; $original = $readyFields.Nonce
    try { $readyFields.Nonce = 'e' * 64; $second = New-Ready } finally { $readyFields.Nonce = $original }
    Assert-Test ((Get-Sha256Bytes $first) -cne (Get-Sha256Bytes $second)) 'Ready bytes omit nonce.'
}
Invoke-Case 'changing actual receipt hash or size changes exact completion bytes' {
    $first = New-Complete; $originalHash = $completeFields.ReceiptSha256; $originalSize = $completeFields.ReceiptBytes
    try {
        $completeFields.ReceiptSha256 = 'f' * 64; $second = New-Complete
        $completeFields.ReceiptSha256 = $originalHash; $completeFields.ReceiptBytes = 12346; $third = New-Complete
    }
    finally { $completeFields.ReceiptSha256 = $originalHash; $completeFields.ReceiptBytes = $originalSize }
    Assert-Test ((Get-Sha256Bytes $first) -cne (Get-Sha256Bytes $second) -and (Get-Sha256Bytes $first) -cne (Get-Sha256Bytes $third)) 'Completion omits actual receipt hash or size.'
}
[pscustomobject]@{ format = 'stratus-transition-wire-tests-v1'; expected_cases = $expectedCases; executed_cases = $script:executed
    passed_cases = $script:passed; failures = @($script:failures.ToArray()); source_and_pure_frame_only = $true
    successor_body_executed = $false; native_executed = $false; process_or_runspace_started = $false
    docker_or_database_executed = $false; filesystem_fixtures_written = $false; full_protocol_accepted = $false } | ConvertTo-Json -Compress
if ($script:executed -ne $expectedCases -or $script:passed -ne $expectedCases) { throw ('Transition wire checks failed: ' + ($script:failures -join '; ')) }
