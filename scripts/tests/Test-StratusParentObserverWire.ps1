[CmdletBinding()]
param([string] $ObserverPath)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $PSBoundParameters.ContainsKey('ObserverPath')) { $ObserverPath = Join-Path $PSScriptRoot '..\Invoke-StratusRuntimeLoadObserver.ps1' }
$source = [IO.File]::ReadAllText([IO.Path]::GetFullPath($ObserverPath))
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)
$expectedCases = 46; $script:executed = 0; $script:passed = 0
$script:failures = [Collections.Generic.List[string]]::new()
function Assert-Test { param([bool] $Condition, [string] $Message) if (-not $Condition) { throw $Message } }
function Invoke-Case {
    param([string] $Name, [scriptblock] $Action)
    $script:executed++
    try { & $Action; $script:passed++; Write-Output ('PASS observer-wire: ' + $Name) }
    catch { $script:failures.Add($Name + ': ' + $_.Exception.GetBaseException().Message); Write-Output ('FAIL observer-wire: ' + $Name) }
}
function Assert-Rejected {
    param([scriptblock] $Action)
    $rejected = $false
    try { & $Action | Out-Null } catch { $rejected = $true }
    Assert-Test $rejected 'Invalid wire input unexpectedly succeeded.'
}
function Get-FunctionAst {
    param([string] $Name)
    $found = @($ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $Name
    }, $false))
    Assert-Test ($found.Count -eq 1) ('Expected one source function: ' + $Name)
    return $found[0]
}
Invoke-Case 'producer parses and explicit nonce presence cannot select legacy fallback' {
    Assert-Test (@($errors).Count -eq 0) 'Observer source did not parse.'
    Assert-Test ($source.Contains('$script:ParentStageObserverMode = $PSBoundParameters.ContainsKey(''ParentStageNonce'')') -and
        $source.Contains('if ($script:ParentStageObserverMode -and -not [regex]::IsMatch($ParentStageNonce, ''\A[0-9a-f]{64}\z''))') -and
        $source.Contains('throw ''Parent observer mode requires an explicit lowercase 64-hex nonce.''')) 'Explicit invalid nonce may fall back to legacy mode.'
}
Invoke-Case 'only four pure managed framing helpers are eligible for extraction' {
    foreach ($name in @('Get-ObserverBytesSha256', 'ConvertTo-ParentObserverSampleUtc', 'New-ParentObserverReadyFrameBytes', 'New-ParentObserverCompleteFrameBytes')) {
        $fn = Get-FunctionAst $name
        foreach ($command in @($fn.FindAll({ param($node) $node -is [Management.Automation.Language.CommandAst] }, $true))) {
            Assert-Test ($command.InvocationOperator -eq [Management.Automation.Language.TokenKind]::Unknown -and
                $command.GetCommandName() -cin @('ConvertTo-Json', 'Where-Object', 'New-ParentObserverReadyFrameBytes')) ('Unreviewed pure-helper command: ' + $command.Extent.Text)
        }
        foreach ($type in @($fn.FindAll({ param($node) $node -is [Management.Automation.Language.TypeExpressionAst] }, $true))) {
            Assert-Test ($type.TypeName.FullName -cin @('Security.Cryptography.SHA256', 'BitConverter', 'string', 'regex',
                'int', 'long', 'DateTimeOffset', 'DateTime', 'DateTimeKind', 'Globalization.CultureInfo',
                'Globalization.DateTimeStyles', 'TimeSpan', 'IO.Path', 'Text.UTF8Encoding')) ('Unreviewed pure-helper type: ' + $type.TypeName.FullName)
        }
    }
}
Invoke-Case 'parent artifact writer creates new bytes flushes and moves without overwrite' {
    $fn = Get-FunctionAst 'Write-ObserverArtifact'
    $branch = @($fn.Body.EndBlock.Statements | Where-Object {
        $_ -is [Management.Automation.Language.IfStatementAst] -and $_.Clauses[0].Item1.Extent.Text -ceq '$script:ParentStageObserverMode'
    })[0].Clauses[0].Item2
    foreach ($required in @('[IO.FileMode]::CreateNew', '$stream.Flush($true)', '[IO.File]::Move($temporaryOutput, $Path)',
        'sha256 = Get-ObserverBytesSha256 $bytes')) { Assert-Test ($branch.Extent.Text.Contains($required)) ('Missing parent publication rule: ' + $required) }
    Assert-Test (-not $branch.Extent.Text.Contains('Remove-Item') -and -not $branch.Extent.Text.Contains('-Force') -and
        -not $branch.Extent.Text.Contains('WriteAllText') -and $branch.Statements[-1] -is [Management.Automation.Language.ReturnStatementAst]) 'Parent publication can reach a replacing legacy writer.'
}
Invoke-Case 'parent output ready and temporary slots are checked while removals remain legacy only' {
    $branches = @($ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.IfStatementAst] -and
        $node.Clauses[0].Item1.Extent.Text -ceq '$script:ParentStageObserverMode' -and
        $node.Clauses[0].Item2.Extent.Text.Contains('$parentDestinations =')
    }, $true))
    Assert-Test ($branches.Count -eq 1 -and $branches[0].Clauses[0].Item2.Extent.Text.Contains('@($absoluteOutput, "$absoluteOutput.tmp", $absoluteReady, "$absoluteReady.tmp")') -and
        $branches[0].Clauses[0].Item2.Extent.Text.Contains('-not $parentDestinations.Add($path) -or (Test-Path -LiteralPath $path)')) 'Parent path absence/distinctness guard differs.'
    $removals = @($ast.FindAll({ param($node) $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -ceq 'Remove-Item' }, $true))
    Assert-Test ($removals.Count -eq 4) 'Unexpected observer removal surface.'
    foreach ($remove in $removals) {
        Assert-Test ($remove.Extent.StartOffset -gt $branches[0].ElseClause.Extent.StartOffset -and $remove.Extent.EndOffset -lt $branches[0].ElseClause.Extent.EndOffset) 'Parent mode may delete existing evidence.'
    }
}
Invoke-Case 'raw ready frame follows durable publication and uses own process birth PID and digest' {
    $calls = @($ast.FindAll({ param($node) $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -ceq 'New-ParentObserverReadyFrameBytes' -and $node.Extent.Text.Contains('-Nonce $ParentStageNonce') }, $true))
    Assert-Test ($calls.Count -eq 1 -and $calls[0].Extent.Text.Contains('-Nonce $ParentStageNonce -ProcessId $PID') -and
        $calls[0].Extent.Text.Contains('-StartedAt $ownBirth -ReadyPath $absoluteReady -ReadySha256 $readyPublication.sha256')) 'Frame uses supplied or script-start identity instead of own launch identity.'
    foreach ($required in @('$ownProcess = [Diagnostics.Process]::GetCurrentProcess()',
        '$ownProcess.StartTime.ToUniversalTime()', '$parentStdout = [Console]::OpenStandardOutput()',
        '$parentStdout.Write($readyFrameBytes, 0, $readyFrameBytes.Length)', '$parentStdout.Flush()')) {
        Assert-Test ($source.Contains($required)) ('Ready wire omits actual own-process/raw-byte call: ' + $required)
    }
    $publishAt = $source.IndexOf('$readyPublication = Write-ObserverArtifact')
    Assert-Test ($publishAt -ge 0 -and $publishAt -lt $calls[0].Extent.StartOffset -and
        $calls[0].Extent.EndOffset -lt $source.IndexOf('$readyWritten = $true')) 'Ready frame does not follow durable publication.'
}
Invoke-Case 'legacy final summary and fatal exit contracts remain without replacing history' {
    $branches = @($ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.IfStatementAst] -and
        $node.Clauses[0].Item1.Extent.Text -ceq '$script:ParentStageObserverMode' -and
        $node.Clauses[0].Item2.Extent.Text.Contains('$completeFrameBytes =')
    }, $true))
    Assert-Test ($branches.Count -eq 1 -and $branches[0].ElseClause.Extent.Text -ceq '{ $summary | ConvertTo-Json -Compress }' -and
        $source.Contains('if (-not $summary.runtime_passed)') -and
        $source.Contains('$fatal | ConvertTo-Json -Compress') -and $source.Contains('exit 2')) 'Final or fatal legacy wire contract changed.'
}
Invoke-Case 'complete frame follows full output publication and binds actual hash bytes and own identity' {
    $calls = @($ast.FindAll({ param($node) $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -ceq 'New-ParentObserverCompleteFrameBytes' }, $true))
    Assert-Test ($calls.Count -eq 1 -and $calls[0].Extent.Text.Contains('-Nonce $ParentStageNonce -ProcessId $PID') -and
        $calls[0].Extent.Text.Contains('-StartedAt $ownBirth -OutputPath $absoluteOutput -OutputSha256 $resultPublication.sha256') -and
        $calls[0].Extent.Text.Contains('-OutputBytes $resultPublication.bytes -Summary $summary')) 'Completion frame omits actual published-output metadata or own identity.'
    $publishAt = $source.IndexOf('$resultPublication = Write-ObserverArtifact -Path $absoluteOutput -Artifact $result -PassThru:$script:ParentStageObserverMode')
    $writeAt = $source.IndexOf('$parentStdout.Write($completeFrameBytes, 0, $completeFrameBytes.Length)')
    Assert-Test ($publishAt -ge 0 -and $publishAt -lt $calls[0].Extent.StartOffset -and
        $calls[0].Extent.EndOffset -lt $writeAt -and $writeAt -lt $source.IndexOf('if (-not $summary.runtime_passed)')) 'Terminal frame is not published after durable output and before the final exit decision.'
}
Invoke-Case 'completion depth and joint wire budget are fixed and output bytes come from bounded serialization' {
    $fn = Get-FunctionAst 'New-ParentObserverCompleteFrameBytes'
    Assert-Test ($fn.Extent.Text.Contains('ConvertTo-Json -Depth 8 -Compress') -and
        $fn.Extent.Text.Contains('if ($bytes.Length -gt 57344)') -and
        $fn.Extent.Text.Contains('$OutputBytes -gt 67108864')) 'Completion frame or artifact bound differs.'
    $writer = (Get-FunctionAst 'Write-ObserverArtifact').Extent.Text
    Assert-Test ($writer.Contains('if ($bytes.Length -gt 67108864)') -and
        $writer.Contains('sha256 = Get-ObserverBytesSha256 $bytes; bytes = [long]$bytes.Length') -and
        $writer.IndexOf('if ($bytes.Length -gt 67108864)') -lt $writer.IndexOf('[IO.FileStream]::new')) 'Publication metadata is not from bounded actual serialized bytes.'
}
Invoke-Case 'parent database readiness normalizes the actual sample while legacy string behavior stays unchanged' {
    $calls = @($ast.FindAll({ param($node) $node -is [Management.Automation.Language.CommandAst] -and
        $node.GetCommandName() -ceq 'ConvertTo-ParentObserverSampleUtc' }, $true))
    Assert-Test ($calls.Count -eq 1 -and $calls[0].Extent.Text -ceq 'ConvertTo-ParentObserverSampleUtc $firstDatabaseSample.at') 'Database readiness is not based on its actual sample timestamp.'
    $branch = $calls[0].Parent
    while ($null -ne $branch -and $branch -isnot [Management.Automation.Language.IfStatementAst]) { $branch = $branch.Parent }
    Assert-Test ($null -ne $branch -and $branch.Clauses[0].Item1.Extent.Text -ceq '$script:ParentStageObserverMode' -and
        $branch.ElseClause.Extent.Text -ceq '{ [string]$firstDatabaseSample.at }') 'Timestamp normalization changed the legacy path or is not parent-mode-only.'
}
if ($script:failures.Count -ne 0) { throw ('Safe extraction rejected: ' + ($script:failures -join '; ')) }
# Do not dot-source the producer, invoke ValidateOnly, compile native code, write
# fixtures, launch processes/runspaces, or call Docker/DB. Only these pure helpers.
foreach ($name in @('Get-ObserverBytesSha256', 'ConvertTo-ParentObserverSampleUtc', 'New-ParentObserverReadyFrameBytes', 'New-ParentObserverCompleteFrameBytes')) { . ([scriptblock]::Create((Get-FunctionAst $name).Extent.Text)) }
$inputFields = @{ RunId = 'a' * 32; Nonce = 'b' * 64; ProcessId = [int]1234
    StartedAt = '2026-09-14T00:00:00.0000000+00:00'; ReadyPath = 'C:\stratus\observer-ready.json'; ReadySha256 = 'c' * 64 }
function New-Frame { return New-ParentObserverReadyFrameBytes @inputFields }
function Assert-BadField {
    param([string] $Name, [object[]] $Values)
    $original = $inputFields[$Name]
    try { foreach ($value in $Values) { $inputFields[$Name] = $value; Assert-Rejected { New-Frame } } }
    finally { $inputFields[$Name] = $original }
}
Invoke-Case 'valid frame has exactly seven correctly bound fields' {
    $text = [Text.UTF8Encoding]::new($false, $true).GetString((New-Frame))
    $parameters = @{ InputObject = $text }
    if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) { $parameters.DateKind = 'String' }
    $frame = ConvertFrom-Json @parameters
    Assert-Test (($frame.PSObject.Properties.Name -join ',') -ceq 'format,session_id,nonce,pid,started_at,ready_path,ready_sha256' -and
        $frame.format -ceq 'stratus-parent-observer-ready-v1' -and $frame.session_id -ceq $inputFields.RunId -and
        $frame.nonce -ceq $inputFields.Nonce -and $frame.started_at -ceq $inputFields.StartedAt -and
        $frame.ready_path -ceq $inputFields.ReadyPath -and $frame.ready_sha256 -ceq $inputFields.ReadySha256) 'Ready wire fields differ.'
}
Invoke-Case 'wire is compact BOM-free UTF8 with exactly one terminal LF and no raw CR' {
    $bytes = New-Frame; $text = [Text.UTF8Encoding]::new($false, $true).GetString($bytes)
    Assert-Test ($bytes.Length -le 8192 -and $bytes[0] -eq 123 -and $bytes[-1] -eq 10 -and
        -not $text.Contains("`r") -and @($bytes | Where-Object { $_ -eq 10 }).Count -eq 1) 'Frame bytes are not one compact bounded raw LF frame.'
}
Invoke-Case 'PID is serialized as an integer and not a string' {
    $text = [Text.UTF8Encoding]::new($false, $true).GetString((New-Frame))
    Assert-Test ($text.Contains('"pid":1234,') -and -not $text.Contains('"pid":"')) 'PID wire type differs.'
}
Invoke-Case 'actual serialized byte hash helper matches known SHA256' {
    Assert-Test ((Get-ObserverBytesSha256 ([Text.Encoding]::UTF8.GetBytes('abc'))) -ceq 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad') 'Ready byte hash helper differs.'
}
Invoke-Case 'run ID rejects wrong width case and nonstrings' { Assert-BadField RunId @('', ('A' * 32), ('a' * 31), 123, $null) }
Invoke-Case 'nonce rejects explicit empty wrong width case and nonstrings' { Assert-BadField Nonce @('', ('B' * 64), ('b' * 63), $false, $null) }
Invoke-Case 'nonce rejects trailing LF despite matching prefix' { Assert-BadField Nonce @(('b' * 64) + "`n") }
Invoke-Case 'ready digest rejects wrong width case and nonstrings' { Assert-BadField ReadySha256 @('', ('C' * 64), ('c' * 65), 0, $null) }
Invoke-Case 'PID rejects coercible strings booleans floats nonpositive and out of range' { Assert-BadField ProcessId @('1234', $true, 1234.0, 0, -1, ([long][int]::MaxValue + 1), $null) }
Invoke-Case 'process birth rejects nonstrings noncanonical and nonUTC times' { Assert-BadField StartedAt @($null, [DateTimeOffset]::UtcNow, '2026-09-14T00:00:00Z', '2026-09-14T00:00:00.0000000+01:00') }
Invoke-Case 'ready path rejects relative and noncanonical paths' { Assert-BadField ReadyPath @('ready.json', 'C:\stratus\.\ready.json', '', $null) }
Invoke-Case 'ready path rejects line injection and NUL' { Assert-BadField ReadyPath @("C:\stratus\ready`n.json", "C:\stratus\ready`r.json", ('C:\stratus\ready' + [char]0 + '.json')) }
Invoke-Case 'oversized path cannot produce an unbounded frame' { Assert-BadField ReadyPath @(('C:\stratus\' + ('x' * 9000) + '.json')) }
Invoke-Case 'changing nonce changes exact captured bytes while preserving the frame schema' {
    $first = New-Frame; $original = $inputFields.Nonce
    try { $inputFields.Nonce = 'd' * 64; $second = New-Frame }
    finally { $inputFields.Nonce = $original }
    Assert-Test ((Get-ObserverBytesSha256 $first) -cne (Get-ObserverBytesSha256 $second)) 'Nonce is absent from captured frame bytes.'
}
$summaryFields = @('database_sample_count', 'resource_sample_count', 'topology_sample_count',
    'minimum_database_samples', 'minimum_resource_samples', 'sample_errors', 'max_api_sessions', 'max_api_active',
    'max_runtime_writer_sessions', 'max_api_idle_in_tx', 'max_global_idle_in_tx', 'max_api_idle_tx_seconds',
    'max_lock_waiters', 'max_oldest_api_query_seconds', 'max_api_cpu_percent', 'max_api_memory_percent',
    'max_postgres_cpu_percent', 'rolling_30s_postgres_cpu_max', 'consecutive_postgres_cpu_150', 'identity_changed',
    'restart_changed', 'oom_observed', 'containers_healthy', 'candidate_identity_held', 'application_writers_stopped',
    'exact_project_topology_held', 'invariants_changed', 'drain_passed', 'runtime_passed')
$summaryMap = [ordered]@{}
foreach ($name in $summaryFields) { $summaryMap[$name] = 0 }
$summaryMap['runtime_passed'] = $true
$completeFields = @{ RunId = $inputFields.RunId; Nonce = $inputFields.Nonce; ProcessId = $inputFields.ProcessId
    StartedAt = $inputFields.StartedAt; OutputPath = 'C:\stratus\observer-runtime.json'; OutputSha256 = 'd' * 64
    OutputBytes = [long]12345; Summary = [pscustomobject]$summaryMap }
function New-CompleteFrame { return New-ParentObserverCompleteFrameBytes @completeFields }
function Assert-BadCompleteField {
    param([string] $Name, [object[]] $Values)
    $original = $completeFields[$Name]
    try { foreach ($value in $Values) { $completeFields[$Name] = $value; Assert-Rejected { New-CompleteFrame } } }
    finally { $completeFields[$Name] = $original }
}
Invoke-Case 'complete frame has exactly nine fields including the full durable-output binding and summary' {
    $text = [Text.UTF8Encoding]::new($false, $true).GetString((New-CompleteFrame))
    $parameters = @{ InputObject = $text }
    if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) { $parameters.DateKind = 'String' }
    $frame = ConvertFrom-Json @parameters
    Assert-Test (($frame.PSObject.Properties.Name -join ',') -ceq 'format,session_id,nonce,pid,started_at,output_path,output_sha256,output_bytes,summary' -and
        $frame.format -ceq 'stratus-parent-observer-complete-v1' -and $frame.session_id -ceq $completeFields.RunId -and
        $frame.nonce -ceq $completeFields.Nonce -and $frame.started_at -ceq $completeFields.StartedAt -and
        $frame.output_path -ceq $completeFields.OutputPath -and $frame.output_sha256 -ceq $completeFields.OutputSha256 -and
        $frame.output_bytes -eq $completeFields.OutputBytes -and ($frame.summary.PSObject.Properties.Name -join ',') -ceq ($summaryFields -join ',')) 'Completion frame schema or bound values differ.'
}
Invoke-Case 'complete wire is compact strict UTF8 with one LF and fits the joint collector budget' {
    $bytes = New-CompleteFrame; $text = [Text.UTF8Encoding]::new($false, $true).GetString($bytes)
    Assert-Test ($bytes.Length -le 57344 -and (57344 + 8192) -eq 65536 -and $bytes[0] -eq 123 -and $bytes[-1] -eq 10 -and
        -not $text.Contains("`r") -and @($bytes | Where-Object { $_ -eq 10 }).Count -eq 1) 'Completion frame exceeds or violates the raw wire budget.'
}
Invoke-Case 'complete output byte count is serialized as a native integer' {
    $text = [Text.UTF8Encoding]::new($false, $true).GetString((New-CompleteFrame))
    Assert-Test ($text.Contains('"output_bytes":12345,') -and -not $text.Contains('"output_bytes":"')) 'Output byte count wire type differs.'
}
Invoke-Case 'complete output count rejects strings booleans floats nonpositive and oversized values' {
    Assert-BadCompleteField OutputBytes @('12345', $true, 12345.0, 0, -1, [long]67108865, $null)
}
Invoke-Case 'complete frame reuses strict nonce PID and process-birth checks' {
    Assert-BadCompleteField Nonce @('', ('B' * 64), (('b' * 64) + "`n"))
    Assert-BadCompleteField ProcessId @('1234', $false, 0)
    Assert-BadCompleteField StartedAt @('2026-09-14T00:00:00Z', '2026-09-14T00:00:00.0000000+01:00')
}
Invoke-Case 'complete output path and hash reject unbound or noncanonical values' {
    Assert-BadCompleteField OutputPath @('runtime.json', 'C:\stratus\.\runtime.json', "C:\stratus\runtime`n.json")
    Assert-BadCompleteField OutputSha256 @('', ('D' * 64), ('d' * 63), $null)
}
Invoke-Case 'completion summary rejects non-native objects' { Assert-BadCompleteField Summary @($null, @{}, 'summary', $true) }
Invoke-Case 'completion summary rejects missing extra and case-substituted fields' {
    $missing = [pscustomobject]$summaryMap; $missing.PSObject.Properties.Remove('runtime_passed')
    $extraMap = [ordered]@{}; foreach ($name in $summaryFields) { $extraMap[$name] = $summaryMap[$name] }; $extraMap['untrusted'] = $true
    $caseMap = [ordered]@{}; foreach ($name in $summaryFields) { if ($name -ceq 'runtime_passed') { $caseMap['RUNTIME_PASSED'] = $true } else { $caseMap[$name] = $summaryMap[$name] } }
    Assert-BadCompleteField Summary @($missing, [pscustomobject]$extraMap, [pscustomobject]$caseMap)
}
Invoke-Case 'native output size accepts the exact positive and 64MiB boundaries' {
    $original = $completeFields.OutputBytes
    try { foreach ($count in @([int]1, [long]67108864)) { $completeFields.OutputBytes = $count; Assert-Test ((New-CompleteFrame).Length -gt 0) 'Exact bounded output size rejected.' } }
    finally { $completeFields.OutputBytes = $original }
}
Invoke-Case 'changing full output digest changes exact terminal frame bytes' {
    $first = New-CompleteFrame; $original = $completeFields.OutputSha256
    try { $completeFields.OutputSha256 = 'e' * 64; $second = New-CompleteFrame }
    finally { $completeFields.OutputSha256 = $original }
    Assert-Test ((Get-ObserverBytesSha256 $first) -cne (Get-ObserverBytesSha256 $second)) 'Full output digest is absent from the terminal wire.'
}
Invoke-Case 'changing summary content changes exact terminal frame bytes' {
    $first = New-CompleteFrame; $original = $completeFields.Summary.runtime_passed
    try { $completeFields.Summary.runtime_passed = $false; $second = New-CompleteFrame }
    finally { $completeFields.Summary.runtime_passed = $original }
    Assert-Test ((Get-ObserverBytesSha256 $first) -cne (Get-ObserverBytesSha256 $second)) 'Actual summary content is absent from the terminal wire.'
}
Invoke-Case 'oversized completion content cannot exceed the reserved second-frame budget' {
    $original = $completeFields.Summary.max_api_sessions
    try { $completeFields.Summary.max_api_sessions = 'x' * 57344; Assert-Rejected { New-CompleteFrame } }
    finally { $completeFields.Summary.max_api_sessions = $original }
}
Invoke-Case 'PostgreSQL microsecond and whole-second timestamps normalize to exact UTC round-trip strings' {
    Assert-Test ((ConvertTo-ParentObserverSampleUtc '2026-09-14T12:34:56.123456+00:00') -ceq '2026-09-14T12:34:56.1234560+00:00') 'Microseconds changed or did not gain the canonical seventh digit.'
    Assert-Test ((ConvertTo-ParentObserverSampleUtc '2026-09-14T12:34:56+00:00') -ceq '2026-09-14T12:34:56.0000000+00:00') 'Whole-second database timestamp failed.'
}
Invoke-Case 'database offset and Z timestamp strings preserve their exact UTC instant' {
    Assert-Test ((ConvertTo-ParentObserverSampleUtc '2026-09-14T14:34:56.123456+02:00') -ceq '2026-09-14T12:34:56.1234560+00:00') 'Explicit offset was discarded.'
    Assert-Test ((ConvertTo-ParentObserverSampleUtc '2026-09-14T12:34:56.1234567Z') -ceq '2026-09-14T12:34:56.1234567+00:00') 'UTC Z instant changed.'
}
Invoke-Case 'PowerShell native UTC DateTime keeps its instant and precision' {
    $value = [DateTime]::SpecifyKind([DateTime]::new(2026, 9, 14, 12, 34, 56).AddTicks(1234567), [DateTimeKind]::Utc)
    Assert-Test ((ConvertTo-ParentObserverSampleUtc $value) -ceq '2026-09-14T12:34:56.1234567+00:00') 'Native UTC date was culture-cast or reinterpreted.'
}
Invoke-Case 'PowerShell native Local DateTime converts using its declared local Kind' {
    $utc = [DateTime]::SpecifyKind([DateTime]::new(2026, 9, 14, 12, 34, 56).AddTicks(1234567), [DateTimeKind]::Utc)
    $local = $utc.ToLocalTime()
    Assert-Test ((ConvertTo-ParentObserverSampleUtc $local) -ceq '2026-09-14T12:34:56.1234567+00:00') 'Native local date lost its known timezone.'
}
Invoke-Case 'native DateTimeOffset preserves its offset-derived instant' {
    $value = [DateTimeOffset]::new(2026, 9, 14, 14, 34, 56, [TimeSpan]::FromHours(2)).AddTicks(1234567)
    Assert-Test ((ConvertTo-ParentObserverSampleUtc $value) -ceq '2026-09-14T12:34:56.1234567+00:00') 'Native offset date lost its actual instant.'
}
Invoke-Case 'unspecified native DateTime cannot silently acquire a timezone' {
    Assert-Rejected { ConvertTo-ParentObserverSampleUtc ([DateTime]::new(2026, 9, 14, 12, 34, 56)) }
}
Invoke-Case 'database normalization is independent of current display culture' {
    $original = [Threading.Thread]::CurrentThread.CurrentCulture
    try {
        foreach ($culture in @('de-DE', 'en-US')) {
            [Threading.Thread]::CurrentThread.CurrentCulture = [Globalization.CultureInfo]::GetCultureInfo($culture)
            Assert-Test ((ConvertTo-ParentObserverSampleUtc '2026-09-14T12:34:56.123456+00:00') -ceq '2026-09-14T12:34:56.1234560+00:00') 'Current culture changed the database timestamp.'
        }
    }
    finally { [Threading.Thread]::CurrentThread.CurrentCulture = $original }
}
Invoke-Case 'culture-formatted database date strings are rejected' {
    foreach ($value in @('14.09.2026 12:34:56', '09/14/2026 12:34:56 PM', 'September 14, 2026 12:34:56 +00:00')) {
        Assert-Rejected { ConvertTo-ParentObserverSampleUtc $value }
    }
}
Invoke-Case 'timestamp strings without an exact timezone or with whitespace are rejected' {
    foreach ($value in @('2026-09-14T12:34:56.123456', '2026-09-14T12:34:56.123456z', ' 2026-09-14T12:34:56+00:00', '2026-09-14 12:34:56+00:00')) {
        Assert-Rejected { ConvertTo-ParentObserverSampleUtc $value }
    }
}
Invoke-Case 'database timestamp rejects null numbers and booleans instead of coercion' {
    foreach ($value in @($null, '', 1234, $true, @{})) { Assert-Rejected { ConvertTo-ParentObserverSampleUtc $value } }
}
Invoke-Case 'invalid calendar offsets excess precision and trailing controls are rejected' {
    foreach ($value in @('2026-02-30T12:34:56+00:00', '2026-09-14T12:34:56+15:00', '2026-09-14T12:34:56.12345678Z', "2026-09-14T12:34:56+00:00`n")) {
        Assert-Rejected { ConvertTo-ParentObserverSampleUtc $value }
    }
}
[pscustomobject]@{ format = 'stratus-parent-observer-wire-tests-v1'; expected_cases = $expectedCases; executed_cases = $script:executed
    passed_cases = $script:passed; failures = @($script:failures.ToArray()); source_and_pure_frame_only = $true
    observer_body_executed = $false; native_executed = $false; process_or_runspace_started = $false
    docker_or_database_executed = $false; filesystem_fixtures_written = $false; full_protocol_accepted = $false } | ConvertTo-Json -Compress
if ($script:executed -ne $expectedCases -or $script:passed -ne $expectedCases) { throw ('Observer wire checks failed: ' + ($script:failures -join '; ')) }
