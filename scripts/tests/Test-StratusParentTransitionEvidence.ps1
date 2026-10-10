[CmdletBinding()]
param([string] $WatchdogPath)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $PSBoundParameters.ContainsKey('WatchdogPath')) {
    $WatchdogPath = Join-Path $PSScriptRoot '..\Invoke-StratusParentStageWatchdog.ps1'
}
# Only ten reviewed managed helpers are extracted. Production entry, native
# runtime, owner termination, Docker and Sandbox are NEVER executed. Ancestry,
# JSON validation and owner liveness below are deliberately narrow test doubles;
# these tests do not establish their production correctness or lock provenance.
$expectedCases = 75
$script:caseCount = 0; $script:passedCases = 0
$script:caseFailures = [Collections.Generic.List[string]]::new()
$script:cleanupFailures = [Collections.Generic.List[string]]::new()
$WatchdogPath = [IO.Path]::GetFullPath($WatchdogPath)
$source = [IO.File]::ReadAllText($WatchdogPath)
$tokens = $null; $parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$parseErrors)
$helperNames = @('Open-ParentFenceOnce', 'Read-ParentRetainedEvidenceSnapshot',
    'Initialize-ParentTransitionEvidenceFence', 'Read-ParentTransitionEvidenceBytes', 'Read-ParentTransitionEvidence',
    'Assert-ParentTransitionEvidenceHeld', 'Enter-ParentTransitionEvidenceContainment',
    'Assert-ParentMutationFenceContainmentOwnership', 'Assert-ParentTransitionContainmentOwnership', 'Assert-ParentTransitionContainmentFence')
$utf8 = [Text.UTF8Encoding]::new($false, $true)
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$workspace = [IO.Path]::GetFullPath((Join-Path $tempRoot ('stratus-transition-evidence-tests-' + [guid]::NewGuid().ToString('N'))))

function Assert-Test {
    param([bool] $Condition, [string] $Message)
    if (-not $Condition) { throw $Message }
}
function Assert-Rejected {
    param([scriptblock] $Action, [string] $Pattern)
    $failure = $null
    try { & $Action | Out-Null } catch { $failure = $_.Exception.GetBaseException().Message }
    Assert-Test ($null -ne $failure) 'Operation unexpectedly succeeded.'
    Assert-Test ($failure -like $Pattern) ('Wrong rejection boundary: ' + $failure)
}
function Invoke-TestCase {
    param([string] $Name, [scriptblock] $Action)
    $script:caseCount++
    try { & $Action; $script:passedCases++; Write-Output ('PASS transition-evidence: ' + $Name) }
    catch {
        $script:caseFailures.Add($Name + ': ' + $_.Exception.GetBaseException().Message)
        Write-Output ('FAIL transition-evidence: ' + $Name)
    }
}
function Get-HelperAst {
    param([string] $Name)
    $found = @($ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $Name
    }, $false))
    Assert-Test ($found.Count -eq 1) ('Expected one direct helper: ' + $Name)
    return $found[0]
}
function Get-Commands {
    param([Management.Automation.Language.Ast] $Within, [string] $Name)
    return @($Within.FindAll({ param($node)
        $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -ceq $Name
    }, $true))
}
function Get-TestHash {
    param([byte[]] $Bytes)
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hash.ComputeHash($Bytes)).Replace('-', '').ToLowerInvariant() }
    finally { $hash.Dispose() }
}
function Get-MutatedHelperAst {
    param([string] $Text, [string] $Name)
    $localTokens = $null; $localErrors = $null
    $localAst = [Management.Automation.Language.Parser]::ParseInput($Text, [ref]$localTokens, [ref]$localErrors)
    Assert-Test (@($localErrors).Count -eq 0) 'Static negative-control source did not parse.'
    $found = @($localAst.EndBlock.Statements | Where-Object {
        $_ -is [Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -ceq $Name
    })
    Assert-Test ($found.Count -eq 1) 'Static negative control lost its one helper.'
    return $found[0]
}
function Assert-PacketRetainedBindingsAst {
    param([Management.Automation.Language.FunctionDefinitionAst] $Fn)
    $required = @(
        'Read-ParentTransitionEvidence registration $paths.transition_registration $Registration.sha256 262144',
        'Read-ParentTransitionEvidence authorization $paths.parent_authorization $registration.value.parent_authorization_sha256',
        'Read-ParentTransitionEvidence terminal $paths.transition_terminal $registration.value.transition_terminal_sha256',
        'Read-ParentTransitionEvidence lease $fixed.lease $a.lease_sha256',
        'Read-ParentTransitionEvidence commit $fixed.commit $a.commit_sha256',
        'Read-ParentTransitionEvidence live $fixed.live $a.live_sha256',
        'Read-ParentTransitionEvidence prestart $fixed.prestart $lease.value.artifacts.prestart_sha256',
        'Read-ParentTransitionEvidence fence $fixed.fence $a.start_fence_sha256 8192',
        'Read-ParentTransitionEvidence attempt $fixed.attempt $a.start_attempt_sha256 65536',
        'Read-ParentTransitionEvidence completion $fixed.completion $a.start_completion_sha256 65536',
        'Read-ParentTransitionEvidence repair $fixed.repair $repair.sha256')
    $calls = @(Get-Commands $Fn 'Read-ParentTransitionEvidence')
    Assert-Test ($calls.Count -eq $required.Count) 'Packet retained dependency count differs.'
    for ($i = 0; $i -lt $required.Count; $i++) {
        Assert-Test ($calls[$i].Extent.Text -ceq $required[$i]) ('Packet retained binding differs: ' + $required[$i])
    }
    $assignment = @($Fn.Body.EndBlock.Statements | Where-Object {
        $_ -is [Management.Automation.Language.AssignmentStatementAst] -and $_.Left.Extent.Text -ceq '$packet'
    })
    Assert-Test ($assignment.Count -eq 1) 'Packet assembly assignment differs.'
    $tables = @($assignment[0].FindAll({ param($node) $node -is [Management.Automation.Language.HashtableAst] }, $true))
    Assert-Test ($tables.Count -eq 1 -and $tables[0].KeyValuePairs.Count -eq 9) 'Packet base document set differs.'
    $names = @('authorization', 'terminal', 'lease', 'commit', 'live', 'prestart', 'fence', 'attempt', 'completion')
    for ($i = 0; $i -lt $names.Count; $i++) {
        Assert-Test ($tables[0].KeyValuePairs[$i].Item1.Value -ceq $names[$i]) 'Packet document key binding differs.'
        $expectedValue = if ($i -lt 3) { '$' + $names[$i] } else { $required[$i + 1] }
        Assert-Test ($tables[0].KeyValuePairs[$i].Item2.Extent.Text -ceq $expectedValue) 'Packet document value binding differs.'
    }
    foreach ($expected in @(
        '$a = $authorization.value', '$fixed.live = $a.live_path',
        '$repair = $packet.prestart.value.payload.network_repair', '$fixed.repair = $repair.path',
        '$packet.repair = Read-ParentTransitionEvidence repair $fixed.repair $repair.sha256')) {
        Assert-Test (@($Fn.Body.EndBlock.Statements | Where-Object { $_.Extent.Text -ceq $expected }).Count -eq 1) ('Packet dependency source differs: ' + $expected)
    }
    Assert-Test (@(Get-Commands $Fn 'Read-ParentDocument').Count -eq 0) 'Packet dependencies bypass the retained-handle registry.'
}
function Assert-PacketPayloadDepthAst {
    param([Management.Automation.Language.FunctionDefinitionAst] $Fn)
    $loops = @($Fn.Body.EndBlock.Statements | Where-Object {
        $_ -is [Management.Automation.Language.ForEachStatementAst] -and
            $_.Condition.Extent.Text -ceq "@('prestart', 'live', 'repair')"
    })
    Assert-Test ($loops.Count -eq 1) 'Envelope revalidation document set differs.'
    $statements = $loops[0].Body.Statements
    Assert-Test ($statements.Count -eq 5 -and $statements[0].Extent.Text -ceq '$envelope = $packet[$name].value' -and
        $statements[2].Extent.Text -ceq '$payloadDepth = if ($name -ceq ''repair'') { 30 } else { 20 }' -and
        $statements[3].Extent.Text -ceq '$payloadText = $envelope.payload | ConvertTo-Json -Depth $payloadDepth -Compress') 'Producer-specific envelope serialization depth differs.'
    $guard = $statements[4]
    Assert-Test ($guard -is [Management.Automation.Language.IfStatementAst] -and
        $guard.Clauses[0].Item1.Extent.Text -ceq '[Stratus.ParentStage.Files]::Hash([Stratus.ParentStage.Files]::Utf8.GetBytes($payloadText)) -cne $envelope.payload_sha256' -and
        $guard.Clauses[0].Item2.Statements.Count -eq 1 -and
        $guard.Clauses[0].Item2.Statements[0].Extent.Text -ceq 'throw ''Held envelope payload serialization/hash differs.''') 'Serialized payload hash mismatch is not rejected.'
}
function Assert-PacketRawTerminalAst {
    param([Management.Automation.Language.FunctionDefinitionAst] $Fn)
    $required = @(
        '$terminalBytes = Read-ParentTransitionEvidenceBytes $script:transitionEvidence.terminal',
        '$terminalText = [Stratus.ParentStage.Files]::Utf8.GetString($terminalBytes).Replace("`r`n", "`n") + "`n"',
        'if ($terminalText.Contains("`r")) { throw ''Terminal receipt has noncanonical carriage returns.'' }',
        '$terminalStdout = [Stratus.ParentStage.Files]::Utf8.GetBytes($terminalText)',
        'Assert-ParentTransitionEvidenceHeld', 'Assert-ParentHeartbeat')
    $tail = @($Fn.Body.EndBlock.Statements | Select-Object -Last 7)
    Assert-Test ($tail.Count -eq 7) 'Packet terminal reconstruction tail is incomplete.'
    for ($i = 0; $i -lt $required.Count; $i++) {
        Assert-Test ($tail[$i].Extent.Text -ceq $required[$i]) ('Raw terminal reconstruction/order differs: ' + $required[$i])
    }
    Assert-Test ($tail[6] -is [Management.Automation.Language.ReturnStatementAst]) 'Packet output is not the final projection.'
    $tables = @($tail[6].FindAll({ param($node) $node -is [Management.Automation.Language.HashtableAst] }, $true))
    Assert-Test ($tables.Count -eq 2 -and $tables[1].KeyValuePairs.Count -eq 2 -and
        $tables[1].KeyValuePairs[0].Item1.Value -ceq 'sha256' -and
        $tables[1].KeyValuePairs[0].Item2.Extent.Text -ceq '[Stratus.ParentStage.Files]::Hash($terminalStdout)' -and
        $tables[1].KeyValuePairs[1].Item1.Value -ceq 'bytes' -and
        $tables[1].KeyValuePairs[1].Item2.Extent.Text -ceq '[long]$terminalStdout.LongLength') 'Terminal hash/length is not derived from retained raw-byte stdout reconstruction.'
}
function Assert-AckEvidenceBoundariesAst {
    param([Management.Automation.Language.FunctionDefinitionAst] $Fn)
    $statements = $Fn.Body.EndBlock.Statements
    Assert-Test ($statements[0].Extent.Text -ceq 'Assert-ParentTransitionEvidenceHeld' -and
        $statements[1].Extent.Text -ceq 'Assert-ParentHeartbeat') 'ACK entry lacks same-handle evidence and heartbeat checks.'
    Assert-Test ($statements[-4].Extent.Text -ceq 'Assert-ParentTransitionEvidenceHeld' -and
        $statements[-3].Extent.Text -ceq 'Assert-ParentHeartbeat' -and
        $statements[-2].Extent.Text -ceq 'if ($Name -ceq ''observer_ack'') { Assert-ParentObserverAckBoundary }' -and
        $statements[-1].Extent.Text -ceq '$observed[$Name] = $published') 'ACK publication boundary lacks same-handle evidence and heartbeat checks before acceptance.'
    foreach ($expected in @(
        '$publishedHash = Write-ParentDocument $paths.$Name $ack',
        '$published = Read-ParentDocument $paths.$Name',
        'if ($published.sha256 -cne $publishedHash) { throw ''Acknowledgement readback differs from its own publication.'' }')) {
        $matches = @($statements | Where-Object { $_.Extent.Text -ceq $expected })
        Assert-Test ($matches.Count -eq 1 -and $matches[0].Extent.StartOffset -gt $statements[1].Extent.EndOffset -and
            $matches[0].Extent.EndOffset -lt $statements[-4].Extent.StartOffset) 'ACK own publication/readback is not enclosed by evidence boundaries.'
    }
    Assert-Test (@(Get-Commands $Fn 'Assert-ParentTransitionEvidenceHeld').Count -eq 2 -and
        @(Get-Commands $Fn 'Assert-ParentHeartbeat').Count -eq 2) 'ACK evidence/heartbeat boundary count differs.'
}
function Assert-PrimaryAggregateAst {
    param([Management.Automation.Language.FunctionDefinitionAst] $Fn)
    $constructors = @($Fn.FindAll({ param($node)
        $node -is [Management.Automation.Language.InvokeMemberExpressionAst] -and
            $node.Member.Value -ceq 'new' -and
            $node.Expression -is [Management.Automation.Language.TypeExpressionAst] -and
            $node.Expression.TypeName.FullName -ceq 'AggregateException'
    }, $true))
    Assert-Test ($constructors.Count -eq 1 -and $constructors[0].Arguments.Count -eq 1 -and
        $constructors[0].Arguments[0].Extent.Text -ceq '[Exception[]]@($primary, $_.Exception)') 'Failure aggregation must preserve primary and cleanup exceptions using one exact typed array.'
}
function Assert-ExclusiveFirstPacketAst {
    param([Management.Automation.Language.FunctionDefinitionAst] $Fn)
    $init = @(Get-Commands $Fn 'Initialize-ParentTransitionEvidenceFence')
    $reads = @(Get-Commands $Fn 'Read-ParentTransitionEvidence')
    Assert-Test ($init.Count -eq 1 -and $Fn.Body.EndBlock.Statements[0].Extent.Text -ceq 'Assert-ParentHeartbeat' -and
        $Fn.Body.EndBlock.Statements[1].Extent.Text -ceq 'Initialize-ParentTransitionEvidenceFence' -and $reads.Count -eq 11) 'Packet does not initialize exclusive ownership before evidence acquisition.'
    foreach ($read in $reads) {
        Assert-Test ($init[0].Extent.EndOffset -lt $read.Extent.StartOffset) 'Packet evidence acquisition precedes exclusive ownership.'
    }
}
function Assert-CaptureOwnershipAst {
    param([Management.Automation.Language.FunctionDefinitionAst] $Fn)
    $open = @(Get-Commands $Fn 'Open-ParentFenceOnce')
    $snap = @(Get-Commands $Fn 'Read-ParentRetainedEvidenceSnapshot')
    $assignments = @($Fn.FindAll({ param($node) $node -is [Management.Automation.Language.AssignmentStatementAst] }, $true))
    $capture = @($assignments | Where-Object { $_.Left.Extent.Text -ceq '$script:transitionFenceCapture' })
    $sha = @($assignments | Where-Object { $_.Left.Extent.Text -ceq '$script:transitionFenceCapture.sha256' })
    $registry = @($assignments | Where-Object { $_.Left.Extent.Text -ceq '$script:transitionEvidence[''fence'']' })
    Assert-Test ($open.Count -eq 1 -and $open[0].Extent.Text -ceq 'Open-ParentFenceOnce $paths.transition_start_fence' -and
        $open[0].Parent.Parent -is [Management.Automation.Language.AssignmentStatementAst] -and
        $open[0].Parent.Parent.Left.Extent.Text -ceq '$script:transitionFenceLock') 'Exclusive acquisition is not immediately retained in dedicated ownership state.'
    Assert-Test ($capture.Count -eq 1 -and $snap.Count -eq 1 -and $sha.Count -eq 1 -and $registry.Count -eq 1 -and
        $capture[0].Extent.Text.Contains('sha256 = $null') -and $capture[0].Extent.Text.Contains('handle = $script:transitionFenceLock') -and
        $open[0].Extent.EndOffset -lt $capture[0].Extent.StartOffset -and $capture[0].Extent.EndOffset -lt $snap[0].Extent.StartOffset -and
        $snap[0].Extent.EndOffset -lt $sha[0].Extent.StartOffset -and $sha[0].Extent.EndOffset -lt $registry[0].Extent.StartOffset -and
        $sha[0].Extent.Text -ceq '$script:transitionFenceCapture.sha256 = $snapshot.sha256' -and
        $registry[0].Extent.Text -ceq '$script:transitionEvidence[''fence''] = $script:transitionFenceCapture') 'Capture is not owned before fallible snapshot or hash can be rebound from caller input.'
}
function Assert-ContainmentDiagnosticGuardsAst {
    param([Management.Automation.Language.FunctionDefinitionAst] $Fn, [string] $Guard)
    $guards = @(Get-Commands $Fn $Guard)
    $reads = @(Get-Commands $Fn 'Read-ParentTransitionEvidenceBytes')
    Assert-Test ($guards.Count -eq 2 -and $reads.Count -eq 1 -and
        $guards[0].Extent.EndOffset -lt $reads[0].Extent.StartOffset -and
        $reads[0].Extent.EndOffset -lt $guards[1].Extent.StartOffset -and
        $Fn.Body.EndBlock.Statements[-1].Extent.Text -ceq $Guard) 'Containment diagnostics lack mandatory structural guards on both boundaries.'
    $required = if ($Fn.Name -ceq 'Enter-ParentTransitionEvidenceContainment') {
        @('$ownedParentFence = $script:fenceLock', '-not [object]::ReferenceEquals($ownedParentFence, $script:fenceLock)')
    } else {
        @('$ownedTransitionFence = $script:transitionFenceLock', '$ownedCapture = $script:transitionFenceCapture',
            '-not [object]::ReferenceEquals($ownedTransitionFence, $script:transitionFenceLock)',
            '-not [object]::ReferenceEquals($ownedCapture, $script:transitionFenceCapture)')
    }
    foreach ($text in $required) { Assert-Test ($Fn.Extent.Text.Contains($text)) 'Containment diagnostic interval does not preserve original ownership references.' }
}

Invoke-TestCase 'production entry remains unconditionally blocked and helpers parse' {
    Assert-Test (@($parseErrors).Count -eq 0) 'Watchdog syntax failed.'
    Assert-Test ($ast.EndBlock.Statements[2] -is [Management.Automation.Language.ThrowStatementAst] -and
        $ast.EndBlock.Statements[2].Extent.Text.Contains('WIP and non-authorizing')) 'Production entry blocker was removed.'
    foreach ($name in $helperNames) { [void](Get-HelperAst $name) }
}
Invoke-TestCase 'extracted helper dependencies contain no native process or external command' {
    foreach ($name in $helperNames) {
        $fn = Get-HelperAst $name
        foreach ($call in @($fn.FindAll({ param($node) $node -is [Management.Automation.Language.CommandAst] }, $true))) {
            Assert-Test ($call.InvocationOperator -eq [Management.Automation.Language.TokenKind]::Unknown -and
                $call.GetCommandName() -cin @('Assert-ParentRegularAncestry', 'Read-ParentTransitionEvidenceBytes',
                    'Read-ParentRetainedEvidenceSnapshot', 'Open-ParentFenceOnce', 'ConvertFrom-ParentJson', 'Assert-ParentTransitionSingleLink',
                    'Assert-ParentTransitionContainmentOwnership', 'Assert-ParentMutationFenceContainmentOwnership', 'Add-ParentFailure')) ('Unreviewed extracted command: ' + $call.Extent.Text)
        }
        foreach ($type in @($fn.FindAll({ param($node) $node -is [Management.Automation.Language.TypeExpressionAst] }, $true))) {
            Assert-Test ($type.TypeName.FullName -cin @('IO.FileStream', 'regex', 'StringComparison', 'StringComparer',
                'byte[]', 'Security.Cryptography.SHA256', 'BitConverter', 'AggregateException', 'Exception[]',
                'object', 'bool', 'string', 'IO.FileMode', 'IO.FileAccess', 'IO.FileShare')) ('Unreviewed extracted type: ' + $type.TypeName.FullName)
        }
    }
}
Invoke-TestCase 'real evidence byte bound precedes allocation read and hash' {
    $fn = Get-HelperAst 'Read-ParentRetainedEvidenceSnapshot'
    $bounds = @($fn.FindAll({ param($node)
        $node -is [Management.Automation.Language.IfStatementAst] -and $node.Extent.Text.Contains('Transition evidence byte bound exceeded.')
    }, $true))
    Assert-Test ($bounds.Count -eq 1) 'Expected one rejecting byte bound.'
    foreach ($operation in @($fn.FindAll({ param($node)
        $node -is [Management.Automation.Language.InvokeMemberExpressionAst] -and
            $node.Member.Value -cin @('new', 'Read', 'ReadByte', 'Create', 'ComputeHash')
    }, $true))) {
        Assert-Test ($bounds[0].Extent.EndOffset -lt $operation.Extent.StartOffset) 'Evidence allocation/read/hash precedes its size bound.'
    }
}
Invoke-TestCase 'real containment stops owner then recovers parent fence and never reopens a captured Transition fence' {
    $fn = Get-HelperAst 'Invoke-ParentContainment'
    $stop = @(Get-Commands $fn 'Stop-ParentExactProcess')[0]
    $enter = @(Get-Commands $fn 'Enter-ParentTransitionEvidenceContainment')
    $validate = @(Get-Commands $fn 'Assert-ParentTransitionContainmentFence')
    $opens = @(Get-Commands $fn 'Open-ParentFenceOnce')
    $revoke = @(Get-Commands $fn 'Write-ParentDocument')
    Assert-Test ($enter.Count -eq 1 -and $validate.Count -eq 1 -and $opens.Count -eq 2 -and $revoke.Count -eq 1) 'Containment evidence hooks differ.'
    Assert-Test ($stop.Extent.Text -ceq 'Stop-ParentExactProcess $script:ownerLease' -and
        $opens[0].Extent.Text -ceq 'Open-ParentFenceOnce $paths.mutation_fence' -and
        $opens[1].Extent.Text -ceq 'Open-ParentFenceOnce $paths.transition_start_fence') 'Containment recovery does not use exact owned fences.'
    Assert-Test ($stop.Extent.EndOffset -lt $opens[0].Extent.StartOffset -and
        $opens[0].Extent.EndOffset -lt $enter[0].Extent.StartOffset -and
        $enter[0].Extent.EndOffset -lt $opens[1].Extent.StartOffset -and
        $opens[1].Extent.EndOffset -lt $validate[0].Extent.StartOffset -and
        $validate[0].Extent.EndOffset -lt $revoke[0].Extent.StartOffset) 'Containment retired evidence or revoked before exact owner/fence recovery.'
    $ownerStopGuard = @($fn.FindAll({ param($node)
        $node -is [Management.Automation.Language.IfStatementAst] -and
            $node.Clauses[0].Item1.Extent.Text -ceq '-not $ownerStopped' -and
            $node.Clauses[0].Item2.Statements[0] -is [Management.Automation.Language.ReturnStatementAst]
    }, $true))
    Assert-Test ($ownerStopGuard.Count -eq 1 -and $ownerStopGuard[0].Extent.EndOffset -lt $enter[0].Extent.StartOffset) 'Unproved owner death may reach evidence containment.'
    $knownFenceGuard = @($fn.FindAll({ param($node)
        $node -is [Management.Automation.Language.IfStatementAst] -and
            $node.Clauses[0].Item1.Extent.Text.Contains('$null -ne $script:transitionFenceCapture -or $null -ne $script:transitionFenceLock -or') -and
            $node.Clauses[0].Item1.Extent.Text.Contains('$script:transitionEvidence.ContainsKey(''fence'') -or (Test-Path -LiteralPath $paths.transition_start_fence)')
    }, $true))
    Assert-Test ($knownFenceGuard.Count -eq 1 -and $knownFenceGuard[0].Extent.StartOffset -lt $validate[0].Extent.StartOffset -and
        $knownFenceGuard[0].Extent.EndOffset -gt $validate[0].Extent.EndOffset) 'Known fence disappearance must not bypass mandatory same-handle verification.'
    $noReopen = @($fn.FindAll({ param($node)
        $node -is [Management.Automation.Language.IfStatementAst] -and
        $node.Clauses[0].Item1.Extent.Text -ceq '$null -ne $script:transitionFenceCapture -or $script:transitionEvidence.Count -ne 0' -and
        $node.Clauses[0].Item2.Statements[0].Extent.Text -ceq 'throw ''Containment cannot reopen a lost captured Transition fence.'''
    }, $true))
    Assert-Test ($noReopen.Count -eq 1 -and $noReopen[0].Extent.EndOffset -lt $opens[1].Extent.StartOffset) 'Captured fence loss may enter pathname recovery.'
    foreach ($name in @('Enter-ParentTransitionEvidenceContainment', 'Assert-ParentTransitionContainmentFence')) {
        $helper = Get-HelperAst $name
        Assert-Test (@(Get-Commands $helper 'Open-ParentFenceOnce').Count -eq 0 -and
            @($helper.FindAll({ param($node)
                $node -is [Management.Automation.Language.InvokeMemberExpressionAst] -and $node.Member.Value -ceq 'Dispose'
            }, $true)).Count -eq 0) 'Containment releases or reopens the original exclusive fence.'
    }
}
Invoke-TestCase 'real fence opener uses fixed existing read-only FileShare.None' {
    $fn = Get-HelperAst 'Open-ParentFenceOnce'
    $factories = @($fn.FindAll({ param($node)
        $node -is [Management.Automation.Language.InvokeMemberExpressionAst] -and $node.Member.Value -ceq 'new'
    }, $true))
    Assert-Test ($factories.Count -eq 1 -and $factories[0].Extent.Text -ceq '[IO.FileStream]::new($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::None)') 'Fence recovery lost fixed exclusive constructor provenance.'
    Assert-Test ($fn.Extent.Text.Contains('if ($native -notin @(32, 33)) { throw }')) 'Fence opener must not hide errors other than sharing denial.'
}
Invoke-TestCase 'actual receiver validates registration in the same branch before transition ACK' {
    $main = @($ast.EndBlock.Statements | Where-Object { $_ -is [Management.Automation.Language.TryStatementAst] })
    Assert-Test ($main.Count -eq 1) 'Expected one direct production try block.'
    $calls = @(Get-Commands $main[0].Body 'Assert-ParentTransitionRegistration')
    $acks = @(Get-Commands $main[0].Body 'Write-ParentAck' | Where-Object {
        $_.CommandElements[1].Extent.Text -ceq 'transition_ack'
    })
    Assert-Test ($calls.Count -eq 1 -and $acks.Count -eq 1 -and
        $calls[0].Extent.Text -ceq 'Assert-ParentTransitionRegistration $transition' -and
        $calls[0].Extent.EndOffset -lt $acks[0].Extent.StartOffset) 'Transition ACK no longer follows full registration validation.'
    $branch = $calls[0].Parent.Parent
    Assert-Test ($branch -is [Management.Automation.Language.StatementBlockAst] -and
        $acks[0].Parent.Parent -eq $branch -and $branch.Parent -is [Management.Automation.Language.IfStatementAst] -and
        $branch.Parent.Clauses[0].Item1.Extent.Text -ceq '$null -ne $transition') 'Registration validation and ACK are not in the same guarded receiving branch.'
}
Invoke-TestCase 'receiver invokes full closure then held evidence heartbeat and unconditional non-authorizing barrier' {
    $fn = Get-HelperAst 'Assert-ParentTransitionRegistration'
    $closure = @(Get-Commands $fn 'Assert-StratusParentTransitionClosure')
    $held = @(Get-Commands $fn 'Assert-ParentTransitionEvidenceHeld')
    $heartbeat = @(Get-Commands $fn 'Assert-ParentHeartbeat')
    $last = $fn.Body.EndBlock.Statements[-1]
    Assert-Test ($closure.Count -eq 1 -and $held.Count -eq 1 -and $heartbeat.Count -eq 1 -and
        $closure[0].Extent.Text -ceq 'Assert-StratusParentTransitionClosure -Packet $bundle.packet -Expected $expected' -and
        $closure[0].Extent.EndOffset -lt $held[0].Extent.StartOffset -and
        $held[0].Extent.EndOffset -lt $heartbeat[0].Extent.StartOffset -and
        $heartbeat[0].Extent.EndOffset -lt $last.Extent.StartOffset) 'Receiver closure/evidence/heartbeat order differs.'
    Assert-Test ($last -is [Management.Automation.Language.ThrowStatementAst] -and
        $last.Extent.Text -ceq "throw 'Transition document closure is integrated but execution provenance is unqualified; downstream ACK remains blocked.'") 'Receiver execution-provenance barrier is no longer unconditional.'
    Assert-Test (@($fn.FindAll({ param($node)
        $node -is [Management.Automation.Language.ReturnStatementAst] -or
        ($node -is [Management.Automation.Language.VariableExpressionAst] -and
            $node.VariablePath.UserPath -match '^(?:script:)?validatedCompletions$')
    }, $true)).Count -eq 0) 'Document-only projection may return authorization or touch validated native completion cache.'
}
Invoke-TestCase 'validator loader fixes R1-bound function-only source paths and rejects preloaded definitions' {
    $fn = Get-HelperAst 'Get-ParentTransitionValidatorProgram'
    $loop = @($fn.Body.EndBlock.Statements | Where-Object { $_ -is [Management.Automation.Language.ForEachStatementAst] })
    Assert-Test ($loop.Count -eq 1 -and $loop[0].Condition.Extent.Text -ceq
        "@('scripts/Stratus.ParentTransitionLineage.ps1', 'scripts/Stratus.ParentTransitionClosure.ps1')") 'Transition validator loader accepts an unfixed source list.'
    foreach ($required in @(
        'if (-not $script:r1Proof.source.ContainsKey($relative)) { throw ''Accepted source omits Transition validation code.'' }',
        '$expected = $script:r1Proof.source[$relative]',
        '$bytes = Read-ParentHeldBytes (Join-Path $contract.root $relative.Replace(''/'', ''\'')) $expected.sha256',
        'if ($bytes.LongLength -ne $expected.bytes) { throw ''Transition validation source byte count differs.'' }',
        '@($errors).Count -ne 0 -or $null -ne $ast.ParamBlock -or $null -ne $ast.BeginBlock',
        '$null -ne $ast.ProcessBlock -or $null -eq $ast.EndBlock -or $ast.EndBlock.Statements.Count -eq 0',
        '$statement -isnot [Management.Automation.Language.FunctionDefinitionAst]',
        '$statement.Name -cnotmatch ''^(Assert-Stratus(?:Lineage|Closure|ParentTransition)|Get-StratusLineage)[A-Za-z]*$''',
        '$null -ne (Get-Command -Name $statement.Name -CommandType Function -ErrorAction SilentlyContinue)',
        'throw ''Transition validator definition is unexpected or preloaded.''',
        'return [scriptblock]::Create(($parts -join "`n"))')) {
        Assert-Test ($fn.Extent.Text.Contains($required)) ('Missing held fixed-library constraint: ' + $required)
    }
}
Invoke-TestCase 'fixed validator dot-load follows R1 acceptance and precedes parent lease publication and arming' {
    $main = @($ast.EndBlock.Statements | Where-Object { $_ -is [Management.Automation.Language.TryStatementAst] })[0].Body
    $r1 = @(Get-Commands $main 'Assert-ParentR1Anchor')
    $loads = @($main.FindAll({ param($node)
        $node -is [Management.Automation.Language.CommandAst] -and
            $node.InvocationOperator -eq [Management.Automation.Language.TokenKind]::Dot
    }, $true))
    $lease = @(Get-Commands $main 'Write-ParentDocument' | Where-Object {
        $_.CommandElements[1].Extent.Text -ceq '$paths.lease'
    })
    $arm = @($main.Statements | Where-Object {
        $_ -is [Management.Automation.Language.AssignmentStatementAst] -and $_.Left.Extent.Text -ceq '$script:armed'
    })
    Assert-Test ($r1.Count -eq 1 -and $loads.Count -eq 1 -and $lease.Count -eq 1 -and $arm.Count -eq 1 -and
        $loads[0].Extent.Text -ceq '. (Get-ParentTransitionValidatorProgram)' -and
        $r1[0].Extent.EndOffset -lt $loads[0].Extent.StartOffset -and
        $loads[0].Extent.EndOffset -lt $lease[0].Extent.StartOffset -and
        $lease[0].Extent.EndOffset -lt $arm[0].Extent.StartOffset) 'Parent lease may arm before accepted fixed validator definitions load.'
}
Invoke-TestCase 'actual packet retains registration plus all ten bound dependency documents' {
    Assert-PacketRetainedBindingsAst (Get-HelperAst 'Get-ParentTransitionPacket')
}
Invoke-TestCase 'packet retained-binding predicate rejects an unheld repair dependency' {
    $fn = Get-HelperAst 'Get-ParentTransitionPacket'
    $changed = $fn.Extent.Text.Replace('Read-ParentTransitionEvidence repair $fixed.repair $repair.sha256', 'Read-ParentDocument $fixed.repair')
    Assert-Test ($changed -cne $fn.Extent.Text) 'Repair negative control did not change its input.'
    Assert-Rejected { Assert-PacketRetainedBindingsAst (Get-MutatedHelperAst $changed $fn.Name) } '*Packet retained dependency count differs*'
}
Invoke-TestCase 'actual envelope payload depths match repair 30 and PreStart Live 20 producers' {
    Assert-PacketPayloadDepthAst (Get-HelperAst 'Get-ParentTransitionPacket')
}
Invoke-TestCase 'payload-depth predicate rejects repair silently serialized at depth 20' {
    $fn = Get-HelperAst 'Get-ParentTransitionPacket'
    $changed = $fn.Extent.Text.Replace('$payloadDepth = if ($name -ceq ''repair'') { 30 } else { 20 }', '$payloadDepth = if ($name -ceq ''repair'') { 20 } else { 20 }')
    Assert-Test ($changed -cne $fn.Extent.Text) 'Depth negative control did not change its input.'
    Assert-Rejected { Assert-PacketPayloadDepthAst (Get-MutatedHelperAst $changed $fn.Name) } '*Producer-specific envelope serialization depth differs*'
}
Invoke-TestCase 'actual terminal stdout hash and byte count come from retained raw bytes not reserialized values' {
    Assert-PacketRawTerminalAst (Get-HelperAst 'Get-ParentTransitionPacket')
}
Invoke-TestCase 'raw-terminal predicate rejects reconstructing stdout from parsed JSON' {
    $fn = Get-HelperAst 'Get-ParentTransitionPacket'
    $changed = $fn.Extent.Text.Replace('$terminalBytes = Read-ParentTransitionEvidenceBytes $script:transitionEvidence.terminal', '$terminalBytes = [Stratus.ParentStage.Files]::Utf8.GetBytes(($terminal.value | ConvertTo-Json -Depth 20 -Compress))')
    Assert-Test ($changed -cne $fn.Extent.Text) 'Raw terminal negative control did not change its input.'
    Assert-Rejected { Assert-PacketRawTerminalAst (Get-MutatedHelperAst $changed $fn.Name) } '*Raw terminal reconstruction/order differs*'
}
Invoke-TestCase 'raw-terminal predicate rejects removal of the producer mandatory lone carriage return barrier' {
    $fn = Get-HelperAst 'Get-ParentTransitionPacket'
    $changed = $fn.Extent.Text.Replace('if ($terminalText.Contains("`r")) { throw ''Terminal receipt has noncanonical carriage returns.'' }', '')
    Assert-Test ($changed -cne $fn.Extent.Text) 'Terminal carriage-return negative control did not change its input.'
    Assert-Rejected { Assert-PacketRawTerminalAst (Get-MutatedHelperAst $changed $fn.Name) } '*Raw terminal reconstruction/order differs*'
}
Invoke-TestCase 'actual ACK checks same retained registry and heartbeat before entry and post-readback acceptance' {
    Assert-AckEvidenceBoundariesAst (Get-HelperAst 'Write-ParentAck')
}
Invoke-TestCase 'ACK boundary predicate rejects dropping either evidence or heartbeat check' {
    $fn = Get-HelperAst 'Write-ParentAck'
    $checks = @($fn.Body.EndBlock.Statements | Where-Object {
        $_.Extent.Text -cin @('Assert-ParentTransitionEvidenceHeld', 'Assert-ParentHeartbeat')
    })
    Assert-Test ($checks.Count -eq 4) 'ACK negative-control target count differs.'
    foreach ($check in $checks) {
        $changed = $fn.Extent.Text.Remove($check.Extent.StartOffset - $fn.Extent.StartOffset, $check.Extent.Text.Length)
        Assert-Rejected { Assert-AckEvidenceBoundariesAst (Get-MutatedHelperAst $changed $fn.Name) } '*ACK*'
    }
}
Invoke-TestCase 'ACK boundary predicate requires the added observer live guard before cache' {
    $fn = Get-HelperAst 'Write-ParentAck'; $guard = $fn.Body.EndBlock.Statements[-2]
    $changed = $fn.Extent.Text.Remove($guard.Extent.StartOffset - $fn.Extent.StartOffset, $guard.Extent.Text.Length)
    Assert-Rejected { Assert-AckEvidenceBoundariesAst (Get-MutatedHelperAst $changed $fn.Name) } '*ACK publication boundary*'
}
Invoke-TestCase 'ACK boundary predicate rejects observer live guard before evidence and heartbeat recheck' {
    $fn = Get-HelperAst 'Write-ParentAck'; $guard = $fn.Body.EndBlock.Statements[-2]
    $changed = $fn.Extent.Text.Remove($guard.Extent.StartOffset - $fn.Extent.StartOffset, $guard.Extent.Text.Length)
    $changed = $changed.Insert($fn.Body.EndBlock.Statements[-4].Extent.StartOffset - $fn.Extent.StartOffset, ($guard.Extent.Text + "`n"))
    Assert-Rejected { Assert-AckEvidenceBoundariesAst (Get-MutatedHelperAst $changed $fn.Name) } '*ACK publication boundary*'
}
Invoke-TestCase 'actual registry and child cleanup use unambiguous aggregate arrays retaining primary failures' {
    foreach ($name in @('Read-ParentTransitionEvidence', 'Invoke-ParentDocker')) {
        Assert-PrimaryAggregateAst (Get-HelperAst $name)
    }
}
Invoke-TestCase 'aggregate predicate rejects scalar overloads and arrays losing the primary exception' {
    foreach ($name in @('Read-ParentTransitionEvidence', 'Invoke-ParentDocker')) {
        $fn = Get-HelperAst $name
        foreach ($replacement in @('$primary, $_.Exception', '[Exception[]]@($_.Exception)')) {
            $changed = $fn.Extent.Text.Replace('[Exception[]]@($primary, $_.Exception)', $replacement)
            Assert-Test ($changed -cne $fn.Extent.Text) 'Aggregate negative control did not change its input.'
            Assert-Rejected { Assert-PrimaryAggregateAst (Get-MutatedHelperAst $changed $fn.Name) } '*Failure aggregation must preserve primary and cleanup exceptions*'
        }
    }
}
Invoke-TestCase 'actual packet acquires exclusive Transition fence before registration and every dependency' {
    Assert-ExclusiveFirstPacketAst (Get-HelperAst 'Get-ParentTransitionPacket')
}
Invoke-TestCase 'exclusive-first predicate rejects omitted acquisition before packet reads' {
    $fn = Get-HelperAst 'Get-ParentTransitionPacket'
    $changed = $fn.Extent.Text.Replace('Initialize-ParentTransitionEvidenceFence', '')
    Assert-Rejected { Assert-ExclusiveFirstPacketAst (Get-MutatedHelperAst $changed $fn.Name) } '*Packet does not initialize exclusive ownership*'
}
Invoke-TestCase 'actual capture owns its fixed exclusive handle before snapshot and records only actual first hash' {
    Assert-CaptureOwnershipAst (Get-HelperAst 'Initialize-ParentTransitionEvidenceFence')
}
Invoke-TestCase 'capture predicate rejects caller-selected digest and unretained initial handle' {
    $fn = Get-HelperAst 'Initialize-ParentTransitionEvidenceFence'
    foreach ($pair in @(
        @('$script:transitionFenceCapture.sha256 = $snapshot.sha256', '$script:transitionFenceCapture.sha256 = $Sha256'),
        @('$script:transitionFenceLock = Open-ParentFenceOnce', '$localUnretained = Open-ParentFenceOnce'))) {
        $changed = $fn.Extent.Text.Replace($pair[0], $pair[1])
        Assert-Rejected { Assert-CaptureOwnershipAst (Get-MutatedHelperAst $changed $fn.Name) } '*'
    }
}
Invoke-TestCase 'actual parent and Transition diagnostics preserve original ownership and guard before and after read failure' {
    foreach ($pair in @(
        @('Enter-ParentTransitionEvidenceContainment', 'Assert-ParentMutationFenceContainmentOwnership'),
        @('Assert-ParentTransitionContainmentFence', 'Assert-ParentTransitionContainmentOwnership'))) {
        Assert-ContainmentDiagnosticGuardsAst (Get-HelperAst $pair[0]) $pair[1]
    }
}
Invoke-TestCase 'diagnostic guard predicate rejects omission of either final ownership check' {
    foreach ($pair in @(
        @('Enter-ParentTransitionEvidenceContainment', 'Assert-ParentMutationFenceContainmentOwnership'),
        @('Assert-ParentTransitionContainmentFence', 'Assert-ParentTransitionContainmentOwnership'))) {
        $fn = Get-HelperAst $pair[0]; $last = $fn.Body.EndBlock.Statements[-1]
        $changed = $fn.Extent.Text.Remove($last.Extent.StartOffset - $fn.Extent.StartOffset, $last.Extent.Text.Length)
        Assert-Rejected { Assert-ContainmentDiagnosticGuardsAst (Get-MutatedHelperAst $changed $fn.Name) $pair[1] } '*mandatory structural guards*'
    }
}
if ($script:caseFailures.Count -ne 0) {
    $global:LASTEXITCODE = 1
    throw ('Evidence extraction rejected before filesystem fixtures: ' + ($script:caseFailures -join '; '))
}

foreach ($name in $helperNames) { . ([scriptblock]::Create((Get-HelperAst $name).Extent.Text)) }

function Assert-ParentRegularAncestry {
    param([string] $Path)
    # Lexical/regular fixture-only substitute, not production path validation.
    if (-not [IO.Path]::IsPathRooted($Path) -or [IO.Path]::GetFullPath($Path) -cne $Path -or
        -not $Path.StartsWith($script:fixtureRoot + '\', [StringComparison]::Ordinal)) {
        throw 'Fixture ancestry rejected noncanonical or foreign path.'
    }
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Fixture ancestry requires a regular file.' }
    return $Path
}
function ConvertFrom-ParentJson {
    param([byte[]] $Bytes)
    # Basic parser substitute only. Duplicate-key/native strict JSON qualification
    # remains the separate production parser's responsibility, not this suite.
    $script:jsonParseCalls++
    $text = $utf8.GetString($Bytes)
    if (-not $text.StartsWith('{') -or -not $text.EndsWith('}')) { throw 'Fixture JSON object rejected.' }
    return Microsoft.PowerShell.Utility\ConvertFrom-Json -InputObject $text -ErrorAction Stop
}
function Assert-ParentTransitionSingleLink {
    param([IO.FileStream] $Held)
    # Controllable admission/revalidation stub. It intentionally does NOT query
    # Windows file metadata or prove a physical single-link identity.
    $script:singleLinkCalls++
    $script:lastSingleLinkHandle = $Held
    if ($script:rejectSingleLink -and ($null -eq $script:rejectSingleLinkPath -or $Held.Name -ceq $script:rejectSingleLinkPath)) {
        throw 'Fixture single-link metadata rejected.'
    }
}
function Add-ParentFailure {
    param([string] $Message)
    # Recording-only test double: no production diagnostics, clocks or I/O.
    $script:fixtureDiagnostics.Add($Message)
    if ($null -ne $script:fixtureDiagnosticHook) { & $script:fixtureDiagnosticHook $Message }
}
function New-FixtureFile {
    param([object] $Fixture, [string] $Name, [AllowEmptyString()][string] $Text)
    $path = [IO.Path]::GetFullPath((Join-Path $Fixture.root $Name))
    Assert-Test ($path.StartsWith($Fixture.root + '\', [StringComparison]::Ordinal) -and
        [IO.Path]::GetDirectoryName($path) -ceq $Fixture.root) 'Fixture file escaped its owned directory.'
    $stream = [IO.FileStream]::new($path, 'CreateNew', 'Write', 'None')
    try { $bytes = $utf8.GetBytes($Text); $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) }
    finally { $stream.Dispose() }
    [void]$Fixture.files.Add($path)
    return $path
}
function Open-FixtureRead {
    param([object] $Fixture, [string] $Path, [IO.FileShare] $Share = [IO.FileShare]::None)
    [void](Assert-ParentRegularAncestry $Path)
    $stream = [IO.FileStream]::new($Path, 'Open', 'Read', $Share)
    $Fixture.handles.Add($stream)
    return $stream
}
function Read-MainEvidence {
    param([object] $Fixture)
    return Read-ParentTransitionEvidence 'authorization' $Fixture.main $Fixture.hash 4096
}
function Read-FenceEvidence {
    param([object] $Fixture)
    return Read-ParentTransitionEvidence 'fence' $Fixture.transition $Fixture.transition_hash 8192
}
function Assert-WriteDenied {
    param([string] $Path)
    Assert-SharingDenied {
        $stream = [IO.FileStream]::new($Path, 'Open', 'Write', 'ReadWrite')
        $stream.Dispose()
    }
}
function Assert-SharingDenied {
    param([scriptblock] $Action)
    $failure = $null
    try { & $Action | Out-Null } catch { $failure = $_.Exception.GetBaseException() }
    Assert-Test ($null -ne $failure -and $failure -is [IO.IOException] -and ($failure.HResult -band 0xffff) -in @(32, 33)) 'Expected native sharing denial, not success or another failure.'
}
function Enter-FixtureContainment {
    param([object] $Fixture)
    $script:ownerLease.Alive = $false
    $script:fenceLock = Open-FixtureRead $Fixture $Fixture.parent
    Enter-ParentTransitionEvidenceContainment
}
function Invoke-ManagedFixture {
    param([scriptblock] $Action, [AllowEmptyString()][string] $MainText = '{"value":1,"nested":{"n":1}}',
        [AllowEmptyString()][string] $FenceText = '{"fence":1}', [switch] $SkipFenceCapture)
    $root = [IO.Path]::GetFullPath((Join-Path $workspace ([guid]::NewGuid().ToString('N'))))
    Assert-Test ([IO.Path]::GetDirectoryName($root) -ceq $workspace -and -not (Test-Path -LiteralPath $root)) 'Fixture directory is not fresh and isolated.'
    [void][IO.Directory]::CreateDirectory($root)
    $fixture = [pscustomobject]@{ root = $root; files = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        handles = [Collections.Generic.List[IDisposable]]::new(); main = $null; second = $null; parent = $null; transition = $null
        hash = Get-TestHash ($utf8.GetBytes($MainText)); transition_hash = Get-TestHash ($utf8.GetBytes($FenceText)) }
    $script:fixtureRoot = $root
    $script:locks = [Collections.Generic.List[IDisposable]]::new()
    $script:transitionEvidence = @{}; $script:transitionEvidenceMode = 'receiving'
    $script:fenceLock = $null; $script:transitionFenceLock = $null; $script:transitionFenceCapture = $null; $script:jsonParseCalls = 0
    $script:rejectSingleLink = $false; $script:rejectSingleLinkPath = $null; $script:singleLinkCalls = 0; $script:lastSingleLinkHandle = $null
    $script:fixtureDiagnostics = [Collections.Generic.List[string]]::new()
    $script:fixtureDiagnosticHook = $null; $script:armed = $true
    $script:ownerLease = [pscustomobject]@{ Alive = $true }
    $script:ownerLease | Add-Member -MemberType ScriptMethod -Name IsAlive -Value { return $this.Alive }
    $script:contract = [pscustomobject]@{ root = $root }
    try {
        $fixture.main = New-FixtureFile $fixture 'main.json' $MainText
        $fixture.second = New-FixtureFile $fixture 'second.json' $MainText
        $fixture.parent = New-FixtureFile $fixture 'parent-fence.json' '{"parent":1}'
        $fixture.transition = New-FixtureFile $fixture 'transition-fence.json' $FenceText
        $script:paths = [pscustomobject]@{ mutation_fence = $fixture.parent; transition_start_fence = $fixture.transition }
        $script:fenceHash = Get-TestHash ($utf8.GetBytes('{"parent":1}'))
        if (-not $SkipFenceCapture) { Initialize-ParentTransitionEvidenceFence }
        $script:singleLinkCalls = 0; $script:lastSingleLinkHandle = $null
        & $Action $fixture
    }
    finally {
        if ($null -ne $script:transitionFenceLock) { $script:transitionFenceLock.Dispose() }
        if ($null -ne $script:transitionFenceCapture) { $script:transitionFenceCapture.handle.Dispose() }
        foreach ($handle in @($script:locks.ToArray()) + @($fixture.handles.ToArray())) { $handle.Dispose() }
        # Check absolute containment and every item BEFORE deletion. Never recurse,
        # enumerate foreign directories or delete unexpected/reparse entries.
        try {
            Assert-Test ([IO.Path]::GetFullPath($root) -ceq $root -and [IO.Path]::GetDirectoryName($root) -ceq $workspace) 'Refusing cleanup outside the exact fixture workspace.'
            $rootItem = Get-Item -LiteralPath $root -Force
            Assert-Test (-not ($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) 'Linked fixture directory preserved.'
            $items = @(Get-ChildItem -LiteralPath $root -Force)
            foreach ($item in $items) {
                Assert-Test (-not $item.PSIsContainer -and -not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -and
                    $fixture.files.Contains($item.FullName) -and [IO.Path]::GetDirectoryName($item.FullName) -ceq $root) 'Unexpected fixture content preserved.'
            }
            foreach ($item in $items) { Remove-Item -LiteralPath $item.FullName -Force -ErrorAction Stop }
            Assert-Test (@(Get-ChildItem -LiteralPath $root -Force).Count -eq 0) 'Fixture cleanup is incomplete.'
            Remove-Item -LiteralPath $root -Force -ErrorAction Stop
        }
        catch { $script:cleanupFailures.Add($_.Exception.GetBaseException().Message); throw }
    }
}

Assert-Test ([IO.Path]::GetDirectoryName($workspace) -ceq $tempRoot -and -not (Test-Path -LiteralPath $workspace)) 'Test workspace must be fresh under the exact temporary directory.'
[void][IO.Directory]::CreateDirectory($workspace)
try {
    Invoke-TestCase 'initial evidence retains a real read-only handle and exact projection' {
        Invoke-ManagedFixture { param($f)
            $doc = Read-MainEvidence $f
            $entry = $script:transitionEvidence.authorization
            Assert-Test (($doc.PSObject.Properties.Name -join ',') -ceq 'path,sha256,value' -and
                $doc.value.nested.n -eq 1 -and $doc.sha256 -ceq $f.hash -and $entry.handle.CanRead -and
                -not $entry.handle.CanWrite -and $entry.handle.Position -eq 0 -and $script:locks.Count -eq 1) 'Initial retained evidence differs.'
        }
    }
    Invoke-TestCase 'retained evidence denies both write and delete access' {
        Invoke-ManagedFixture { param($f)
            [void](Read-MainEvidence $f); Assert-WriteDenied $f.main
            Assert-SharingDenied { [IO.File]::Delete($f.main) }
            Assert-Test (Test-Path -LiteralPath $f.main) 'Retained evidence was deleted.'
        }
    }
    Invoke-TestCase 'repeated same binding reuses its handle but reparses a fresh nested value' {
        Invoke-ManagedFixture { param($f)
            $first = Read-MainEvidence $f; $held = $script:transitionEvidence.authorization.handle
            $first.value.nested.n = 999
            $second = Read-MainEvidence $f
            Assert-Test ($second.value.nested.n -eq 1 -and -not [object]::ReferenceEquals($first.value, $second.value) -and
                [object]::ReferenceEquals($held, $script:transitionEvidence.authorization.handle) -and
                $script:locks.Count -eq 1 -and $script:jsonParseCalls -eq 2) 'Repeated evidence leaked a handle or reused caller-mutated parsed authority.'
        }
    }
    Invoke-TestCase 'same evidence name cannot change path' {
        Invoke-ManagedFixture { param($f)
            [void](Read-MainEvidence $f)
            Assert-Rejected { Read-ParentTransitionEvidence 'authorization' $f.second $f.hash 4096 } '*cannot be replaced*'
            Assert-Test ($script:locks.Count -eq 1) 'Rejected rebinding allocated a handle.'
        }
    }
    Invoke-TestCase 'same evidence name cannot change digest' {
        Invoke-ManagedFixture { param($f)
            [void](Read-MainEvidence $f)
            Assert-Rejected { Read-ParentTransitionEvidence 'authorization' $f.main ('0' * 64) 4096 } '*cannot be replaced*'
        }
    }
    Invoke-TestCase 'same evidence name cannot change byte limit' {
        Invoke-ManagedFixture { param($f)
            [void](Read-MainEvidence $f)
            Assert-Rejected { Read-ParentTransitionEvidence 'authorization' $f.main $f.hash 4095 } '*cannot be replaced*'
        }
    }
    Invoke-TestCase 'different evidence names cannot alias the same exact path' {
        Invoke-ManagedFixture { param($f)
            [void](Read-MainEvidence $f)
            Assert-Rejected { Read-ParentTransitionEvidence 'terminal' $f.main $f.hash 4096 } '*paths alias*'
            Assert-Test ($script:locks.Count -eq 1) 'Alias allocated another handle.'
        }
    }
    Invoke-TestCase 'case-variant filename aliases cannot gain another evidence identity' {
        Invoke-ManagedFixture { param($f)
            [void](Read-MainEvidence $f)
            $alias = Join-Path $f.root 'MAIN.JSON'
            Assert-Rejected { Read-ParentTransitionEvidence 'terminal' $alias $f.hash 4096 } '*paths alias*'
        }
    }
    Invoke-TestCase 'root escapes and noncanonical paths are rejected before acquisition' {
        Invoke-ManagedFixture { param($f)
            $outside = [IO.Path]::GetFullPath((Join-Path $f.root '..\outside.json'))
            Assert-Rejected { Read-ParentTransitionEvidence 'authorization' $outside $f.hash 4096 } '*escaped the approved root*'
            Assert-Rejected { Read-ParentTransitionEvidence 'authorization' (Join-Path $f.root '.\main.json') $f.hash 4096 } '*Fixture ancestry rejected*'
            Assert-Test ($script:locks.Count -eq 0) 'Invalid path allocated an evidence handle.'
        }
    }
    Invoke-TestCase 'parent mutation fence never enters the ordinary evidence registry' {
        Invoke-ManagedFixture { param($f)
            Assert-Rejected { Read-ParentTransitionEvidence 'authorization' $f.parent $script:fenceHash 8192 } '*protocol fence*'
            Assert-Test ($script:locks.Count -eq 0) 'Parent fence was acquired as ordinary evidence.'
        }
    }
    Invoke-TestCase 'transition fence path and fence registry name must match in both directions' {
        Invoke-ManagedFixture { param($f)
            Assert-Rejected { Read-ParentTransitionEvidence 'terminal' $f.transition $f.transition_hash 8192 } '*protocol fence*'
            Assert-Rejected { Read-ParentTransitionEvidence 'fence' $f.main $f.hash 8192 } '*protocol fence*'
        }
    }
    Invoke-TestCase 'case-variant parent and transition fence paths cannot masquerade as ordinary evidence' {
        Invoke-ManagedFixture { param($f)
            $parentAlias = Join-Path $f.root 'PARENT-FENCE.JSON'
            $transitionAlias = Join-Path $f.root 'TRANSITION-FENCE.JSON'
            foreach ($name in @('authorization', 'fence')) {
                Assert-Rejected { Read-ParentTransitionEvidence $name $parentAlias $script:fenceHash 8192 } '*protocol fence*'
                Assert-Rejected { Read-ParentTransitionEvidence $name $transitionAlias $f.transition_hash 8192 } '*protocol fence*'
            }
            Assert-Test ($script:locks.Count -eq 0) 'Case-variant fence alias acquired a shared handle.'
        }
    }
    Invoke-TestCase 'invalid evidence names digests and byte limits reject without handles' {
        Invoke-ManagedFixture { param($f)
            Assert-Rejected { Read-ParentTransitionEvidence 'foreign' $f.main $f.hash 4096 } '*Invalid Transition evidence request*'
            foreach ($hash in @(('A' * 64), ('0' * 63), ($f.hash + "`n"))) {
                Assert-Rejected { Read-ParentTransitionEvidence 'authorization' $f.main $hash 4096 } '*Invalid Transition evidence request*'
            }
            foreach ($limit in @(0, 1048577)) {
                Assert-Rejected { Read-ParentTransitionEvidence 'authorization' $f.main $f.hash $limit } '*Invalid Transition evidence request*'
            }
            Assert-Test ($script:locks.Count -eq 0) 'Invalid request allocated handles.'
        }
    }
    Invoke-TestCase 'oversized evidence rejects before hashing or JSON parsing' {
        Invoke-ManagedFixture -MainText ('x' * 129) -Action { param($f)
            Assert-Rejected { Read-ParentTransitionEvidence 'authorization' $f.main ('0' * 64) 128 } '*byte bound exceeded*'
            Assert-Test ($script:jsonParseCalls -eq 0 -and $script:locks.Count -eq 1 -and $script:transitionEvidence.authorization.handle.Position -eq 0) 'Oversized file escaped its pre-read bound or lost retained ownership.'
            Assert-WriteDenied $f.main
        }
    }
    Invoke-TestCase 'zero-length evidence rejects before hashing and parsing' {
        Invoke-ManagedFixture -MainText '' -Action { param($f)
            Assert-Rejected { Read-MainEvidence $f } '*byte bound exceeded*'
            Assert-Test ($script:jsonParseCalls -eq 0) 'Empty file reached JSON parsing.'
        }
    }
    Invoke-TestCase 'hash mismatch remains retained and non-authorizing' {
        Invoke-ManagedFixture { param($f)
            Assert-Rejected { Read-ParentTransitionEvidence 'authorization' $f.main ('0' * 64) 4096 } '*differs from its retained hash*'
            Assert-Test ($script:locks.Count -eq 1 -and $script:jsonParseCalls -eq 0) 'Hash mismatch lost its held evidence or was parsed.'
            Assert-WriteDenied $f.main
            Assert-Rejected { Assert-ParentTransitionEvidenceHeld } '*differs from its retained hash*'
        }
    }
    Invoke-TestCase 'JSON failure remains held for containment rather than silently replaced' {
        Invoke-ManagedFixture -MainText 'not-json' -Action { param($f)
            Assert-Rejected { Read-MainEvidence $f } '*Fixture JSON object rejected*'
            Assert-Test ($script:locks.Count -eq 1 -and $script:jsonParseCalls -eq 1) 'Parser failure did not retain exact evidence.'
            Assert-WriteDenied $f.main
        }
    }
    Invoke-TestCase 'new metadata-rejected handle is disposed without registering or retaining it' {
        Invoke-ManagedFixture { param($f)
            $script:rejectSingleLink = $true; $script:rejectSingleLinkPath = $f.main
            Assert-Rejected { Read-MainEvidence $f } '*Fixture single-link metadata rejected*'
            Assert-Test ($script:locks.Count -eq 0 -and $script:transitionEvidence.Count -eq 1 -and
                $script:transitionEvidence.ContainsKey('fence') -and $script:singleLinkCalls -eq 2 -and
                -not $script:lastSingleLinkHandle.CanRead -and $script:transitionFenceLock.CanRead) 'Rejected ordinary metadata leaked its handle or released the original exclusive fence.'
            $reopened = Open-FixtureRead $f $f.main
            Assert-Test ($reopened.CanRead) 'Unregistered metadata rejection leaked the file handle.'
        }
    }
    Invoke-TestCase 'retained metadata is rechecked and rejection keeps established evidence held' {
        Invoke-ManagedFixture { param($f)
            [void](Read-MainEvidence $f); $before = $script:singleLinkCalls; $script:rejectSingleLink = $true
            Assert-Rejected { Assert-ParentTransitionEvidenceHeld } '*Fixture single-link metadata rejected*'
            Assert-Test ($script:singleLinkCalls -eq $before + 1 -and $script:transitionEvidence.authorization.handle.CanRead -and
                $script:locks.Count -eq 1) 'Metadata revalidation was bypassed or released accepted evidence.'
            Assert-WriteDenied $f.main
        }
    }
    Invoke-TestCase 'closed read handle fails held-evidence revalidation' {
        Invoke-ManagedFixture { param($f)
            [void](Read-MainEvidence $f); $script:transitionEvidence.authorization.handle.Dispose()
            Assert-Rejected { Assert-ParentTransitionEvidenceHeld } '*lost its exact retained read-only handle*'
        }
    }
    Invoke-TestCase 'writable handles cannot substitute for an evidence read handle' {
        Invoke-ManagedFixture { param($f)
            $handle = [IO.FileStream]::new($f.main, 'Open', 'ReadWrite', 'ReadWrite'); $f.handles.Add($handle)
            $entry = [pscustomobject]@{ handle = $handle; path = $f.main; sha256 = $f.hash; maximum_bytes = 4096; retired = $false }
            Assert-Rejected { Read-ParentTransitionEvidenceBytes $entry } '*lost its exact retained read-only handle*'
        }
    }
    Invoke-TestCase 'read handle with another path cannot substitute despite identical bytes' {
        Invoke-ManagedFixture { param($f)
            $handle = Open-FixtureRead $f $f.second
            $entry = [pscustomobject]@{ handle = $handle; path = $f.main; sha256 = $f.hash; maximum_bytes = 4096; retired = $false }
            Assert-Rejected { Read-ParentTransitionEvidenceBytes $entry } '*lost its exact retained read-only handle*'
        }
    }
    Invoke-TestCase 'all retained ordinary evidence handles are revalidated' {
        Invoke-ManagedFixture { param($f)
            [void](Read-MainEvidence $f); [void](Read-ParentTransitionEvidence 'terminal' $f.second $f.hash 4096)
            Assert-ParentTransitionEvidenceHeld
            $script:transitionEvidence.terminal.handle.Dispose()
            Assert-Rejected { Assert-ParentTransitionEvidenceHeld } '*lost its exact retained read-only handle*'
            Assert-Test ($script:transitionEvidence.authorization.handle.CanRead) 'Unrelated evidence was released.'
        }
    }
    Invoke-TestCase 'live fake owner prevents containment while exclusive Transition fence stays held' {
        Invoke-ManagedFixture { param($f)
            [void](Read-FenceEvidence $f); $script:fenceLock = Open-FixtureRead $f $f.parent
            Assert-Rejected { Enter-ParentTransitionEvidenceContainment } '*exact owner stopped*'
            Assert-Test ($script:transitionEvidenceMode -ceq 'receiving' -and -not $script:transitionEvidence.fence.retired -and
                $script:transitionFenceLock.CanRead) 'Live owner did not block containment or exclusive ownership was lost.'
            Assert-WriteDenied $f.transition
        }
    }
    Invoke-TestCase 'missing owner or parent fence cannot enter containment or release exclusive evidence' {
        Invoke-ManagedFixture { param($f)
            [void](Read-FenceEvidence $f); $savedOwner = $script:ownerLease; $script:ownerLease = $null
            Assert-Rejected { Enter-ParentTransitionEvidenceContainment } '*exact owner stopped*'
            $script:ownerLease = $savedOwner; $script:ownerLease.Alive = $false
            Assert-Rejected { Enter-ParentTransitionEvidenceContainment } '*exact live read-only exclusive parent fence*'
            Assert-Test ($script:transitionEvidenceMode -ceq 'receiving' -and -not $script:transitionEvidence.fence.retired) 'Unproved parent fence allowed retirement.'
        }
    }
    Invoke-TestCase 'wrong recovered parent-fence hash is diagnostic only and cannot restore normal evidence authority' {
        Invoke-ManagedFixture { param($f)
            [void](Read-FenceEvidence $f); $script:ownerLease.Alive = $false
            $script:fenceLock = Open-FixtureRead $f $f.parent; $script:fenceHash = '0' * 64
            Enter-ParentTransitionEvidenceContainment
            Assert-Test ($script:transitionEvidenceMode -ceq 'containing' -and -not $script:transitionEvidence.fence.retired -and
                $script:fixtureDiagnostics.Count -eq 1 -and $script:fixtureDiagnostics[0] -like '*differs from its retained hash*' -and
                $script:fenceHash -ceq ('0' * 64) -and $script:fenceLock.CanRead -and $script:transitionFenceLock.CanRead) 'Parent hash failure was repaired, omitted or lost ownership.'
            Assert-Rejected { Read-MainEvidence $f } '*terminally disabled*'
            Assert-Rejected { Assert-ParentTransitionEvidenceHeld } '*cannot authorize after containment*'
        }
    }
    Invoke-TestCase 'exclusive Transition fence remains the same handle through exact owner stop and parent fence recovery' {
        Invoke-ManagedFixture { param($f)
            [void](Read-FenceEvidence $f); [void](Read-MainEvidence $f)
            $original = $script:transitionFenceLock; $capture = $script:transitionFenceCapture
            Assert-SharingDenied { Open-FixtureRead $f $f.transition }
            Enter-FixtureContainment $f
            Assert-Test ($script:transitionEvidenceMode -ceq 'containing' -and -not $script:transitionEvidence.fence.retired -and
                [object]::ReferenceEquals($original, $script:transitionFenceLock) -and
                [object]::ReferenceEquals($capture, $script:transitionEvidence.fence) -and
                $original.CanRead -and $script:transitionEvidence.authorization.handle.CanRead) 'Containment released or replaced exclusive/ordinary evidence.'
            Assert-WriteDenied $f.main
            Assert-SharingDenied { Open-FixtureRead $f $f.transition }
            Assert-ParentTransitionContainmentFence
        }
    }
    Invoke-TestCase 'containment permanently disables new evidence and ordinary authorization rechecks' {
        Invoke-ManagedFixture { param($f)
            [void](Read-FenceEvidence $f); Enter-FixtureContainment $f
            Assert-Rejected { Read-MainEvidence $f } '*terminally disabled*'
            Assert-Rejected { Assert-ParentTransitionEvidenceHeld } '*cannot authorize after containment*'
            $bytes = Read-ParentTransitionEvidenceBytes $script:transitionEvidence.fence
            Assert-Test ((Get-TestHash $bytes) -ceq $f.transition_hash -and $script:transitionFenceLock.CanRead) 'Containment lost non-authorizing access through its original handle.'
        }
    }
    Invoke-TestCase 'repeated containment entry retains all evidence without releasing or reopening the exclusive fence' {
        Invoke-ManagedFixture { param($f)
            [void](Read-FenceEvidence $f); [void](Read-MainEvidence $f); Enter-FixtureContainment $f
            $before = $script:locks.Count; $ordinary = $script:transitionEvidence.authorization.handle; $exclusive = $script:transitionFenceLock
            Enter-ParentTransitionEvidenceContainment
            Assert-Test ($script:locks.Count -eq $before -and [object]::ReferenceEquals($ordinary, $script:transitionEvidence.authorization.handle) -and $ordinary.CanRead -and
                [object]::ReferenceEquals($exclusive, $script:transitionFenceLock) -and $exclusive.CanRead) 'Containment retry reopened or released evidence.'
            Assert-WriteDenied $f.main
            Assert-WriteDenied $f.transition
        }
    }
    Invoke-TestCase 'exclusive Transition fence permits no write delete or read gap across containment' {
        Invoke-ManagedFixture { param($f)
            [void](Read-FenceEvidence $f)
            foreach ($phase in @('receiving', 'containing')) {
                if ($phase -ceq 'containing') { Enter-FixtureContainment $f }
                Assert-WriteDenied $f.transition
                Assert-SharingDenied { [IO.File]::Delete($f.transition) }
                Assert-SharingDenied { Open-FixtureRead $f $f.transition ([IO.FileShare]::Read) }
            }
            Assert-ParentTransitionContainmentFence
            Assert-Test ($script:transitionFenceCapture.sha256 -ceq $f.transition_hash) 'Rejected mutation changed captured identity.'
        }
    }
    Invoke-TestCase 'exclusive transition validation rejects wrong path and premature phase' {
        Invoke-ManagedFixture { param($f)
            [void](Read-FenceEvidence $f)
            Assert-Rejected { Assert-ParentTransitionContainmentFence } '*irreversible containment entry*'
            Enter-FixtureContainment $f
            $script:transitionFenceLock = Open-FixtureRead $f $f.second
            Assert-Rejected { Assert-ParentTransitionContainmentFence } '*lost its exact first-acquired Transition fence*'
        }
    }
    Invoke-TestCase 'previously captured Transition fence cannot disappear during containment' {
        Invoke-ManagedFixture { param($f)
            [void](Read-FenceEvidence $f); Enter-FixtureContainment $f
            Assert-Test ($f.files.Contains($f.transition) -and [IO.Path]::GetDirectoryName($f.transition) -ceq $f.root) 'Refusing deletion outside owned fixture.'
            Assert-SharingDenied { [IO.File]::Delete($f.transition) }
            Assert-ParentTransitionContainmentFence
            Assert-Test ((Test-Path -LiteralPath $f.transition) -and $script:transitionEvidence.ContainsKey('fence') -and
                $script:transitionEvidenceMode -ceq 'containing') 'Captured fence disappeared or discarded its causal obligation.'
        }
    }
    Invoke-TestCase 'pre-capture fallback fence remains containment-only and preserves unrelated owned handles' {
        Invoke-ManagedFixture -SkipFenceCapture -Action { param($f)
            $unrelated = Open-FixtureRead $f $f.main
            Enter-FixtureContainment $f
            $script:transitionFenceLock = Open-ParentFenceOnce $f.transition
            Assert-ParentTransitionContainmentFence
            Assert-Test ($unrelated.CanRead -and $script:transitionEvidence.Count -eq 0 -and
                $null -eq $script:transitionFenceCapture -and $script:transitionFenceLock.CanRead) 'Fallback invented active evidence or released unrelated ownership.'
            Assert-Rejected { Read-MainEvidence $f } '*terminally disabled*'
            Assert-WriteDenied $f.main
        }
    }
    Invoke-TestCase 'no ordinary evidence may enter before first exclusive capture' {
        Invoke-ManagedFixture -SkipFenceCapture -Action { param($f)
            Assert-Rejected { Read-MainEvidence $f } '*requires its exact first-acquired exclusive fence*'
            Assert-Test ($script:locks.Count -eq 0 -and $script:transitionEvidence.Count -eq 0 -and $null -eq $script:transitionFenceLock) 'Pre-capture read retained ordinary evidence.'
        }
    }
    Invoke-TestCase 'a competing shared holder blocks initial exclusive capture and all ordinary admission' {
        Invoke-ManagedFixture -SkipFenceCapture -Action { param($f)
            $competitor = Open-FixtureRead $f $f.transition ([IO.FileShare]::Read)
            Assert-Rejected { Initialize-ParentTransitionEvidenceFence } '*Transition fence unavailable*'
            Assert-Rejected { Read-MainEvidence $f } '*requires its exact first-acquired exclusive fence*'
            Assert-Test ($script:transitionEvidence.Count -eq 0 -and $null -eq $script:transitionFenceCapture -and $null -eq $script:transitionFenceLock) 'Failed initial acquisition created evidence authority.'
            $competitor.Dispose()
        }
    }
    Invoke-TestCase 'repeat initialization retains the identical exclusive handle capture and actual digest' {
        Invoke-ManagedFixture { param($f)
            $held = $script:transitionFenceLock; $capture = $script:transitionFenceCapture
            Initialize-ParentTransitionEvidenceFence; Initialize-ParentTransitionEvidenceFence
            Assert-Test ([object]::ReferenceEquals($held, $script:transitionFenceLock) -and
                [object]::ReferenceEquals($capture, $script:transitionFenceCapture) -and
                [object]::ReferenceEquals($capture, $script:transitionEvidence.fence) -and
                $capture.sha256 -ceq $f.transition_hash -and $script:locks.Count -eq 0) 'Repeated initialization reopened or rebound the fence.'
            Assert-SharingDenied { Open-FixtureRead $f $f.transition ([IO.FileShare]::Read) }
        }
    }
    Invoke-TestCase 'caller expected fence digest cannot replace actual first captured bytes identity' {
        Invoke-ManagedFixture { param($f)
            $capture = $script:transitionFenceCapture
            Assert-Rejected { Read-ParentTransitionEvidence 'fence' $f.transition ('0' * 64) 8192 } '*binding cannot be replaced*'
            Assert-Test ($capture.sha256 -ceq $f.transition_hash -and [object]::ReferenceEquals($capture, $script:transitionEvidence.fence) -and
                $script:transitionFenceLock.CanRead -and $script:locks.Count -eq 0) 'Untrusted fence digest rebound or released the capture.'
        }
    }
    Invoke-TestCase 'first metadata failure retains incomplete exclusive ownership without a retryable hash identity' {
        Invoke-ManagedFixture -SkipFenceCapture -Action { param($f)
            $script:rejectSingleLink = $true; $script:rejectSingleLinkPath = $f.transition
            Assert-Rejected { Initialize-ParentTransitionEvidenceFence } '*Fixture single-link metadata rejected*'
            $held = $script:transitionFenceLock; $before = $script:singleLinkCalls
            $script:rejectSingleLink = $false
            Assert-Rejected { Initialize-ParentTransitionEvidenceFence } '*capture is incomplete*'
            Assert-Rejected { Read-MainEvidence $f } '*requires its exact first-acquired exclusive fence*'
            Assert-Test ($held.CanRead -and [object]::ReferenceEquals($held, $script:transitionFenceCapture.handle) -and
                $null -eq $script:transitionFenceCapture.sha256 -and $script:transitionEvidence.Count -eq 0 -and
                $script:singleLinkCalls -eq $before) 'Failed metadata capture was dropped, retried or promoted.'
            Assert-SharingDenied { [IO.File]::Delete($f.transition) }
        }
    }
    Invoke-TestCase 'first oversized fence failure retains its handle but never hashes parses or admits evidence' {
        Invoke-ManagedFixture -SkipFenceCapture -FenceText ('x' * 8193) -Action { param($f)
            Assert-Rejected { Initialize-ParentTransitionEvidenceFence } '*byte bound exceeded*'
            Assert-Rejected { Initialize-ParentTransitionEvidenceFence } '*capture is incomplete*'
            Assert-Test ($script:transitionFenceLock.CanRead -and $null -eq $script:transitionFenceCapture.sha256 -and
                $script:transitionEvidence.Count -eq 0 -and $script:jsonParseCalls -eq 0 -and $script:transitionFenceLock.Position -eq 0) 'Oversized capture lost ownership or invented a digest.'
            Assert-WriteDenied $f.transition
            Enter-FixtureContainment $f; Assert-ParentTransitionContainmentFence
            Assert-Test ($script:fixtureDiagnostics.Count -eq 1 -and $null -eq $script:transitionFenceCapture.sha256 -and
                $script:transitionEvidence.Count -eq 0) 'Containment promoted bad-size evidence or lost its diagnostic.'
        }
    }
    Invoke-TestCase 'first empty fence failure retains exclusive ownership without a usable evidence identity' {
        Invoke-ManagedFixture -SkipFenceCapture -FenceText '' -Action { param($f)
            Assert-Rejected { Initialize-ParentTransitionEvidenceFence } '*byte bound exceeded*'
            Assert-Rejected { Read-MainEvidence $f } '*requires its exact first-acquired exclusive fence*'
            Assert-Test ($script:transitionFenceLock.CanRead -and $null -eq $script:transitionFenceCapture.sha256 -and
                $script:transitionEvidence.Count -eq 0 -and $script:jsonParseCalls -eq 0) 'Empty capture became active evidence.'
            Enter-FixtureContainment $f; Assert-ParentTransitionContainmentFence
            Assert-Test ($script:fixtureDiagnostics.Count -eq 1 -and $null -eq $script:transitionFenceCapture.sha256 -and
                $script:transitionEvidence.Count -eq 0) 'Containment promoted empty evidence or lost its diagnostic.'
        }
    }
    Invoke-TestCase 'malformed JSON retains captured raw digest and exclusive continuity while parsing rejects' {
        Invoke-ManagedFixture -FenceText 'not-json' -Action { param($f)
            Assert-Rejected { Read-FenceEvidence $f } '*Fixture JSON object rejected*'
            Assert-Test ($script:transitionFenceCapture.sha256 -ceq $f.transition_hash -and $script:transitionFenceLock.CanRead -and
                $script:transitionEvidence.Count -eq 1) 'JSON rejection erased actual captured byte identity.'
            Enter-FixtureContainment $f; Assert-ParentTransitionContainmentFence
            Assert-WriteDenied $f.transition
            Assert-Rejected { Read-MainEvidence $f } '*terminally disabled*'
        }
    }
    Invoke-TestCase 'incomplete capture containment keeps its original handle without promoting a later successful read' {
        Invoke-ManagedFixture -SkipFenceCapture -Action { param($f)
            $script:rejectSingleLink = $true; $script:rejectSingleLinkPath = $f.transition
            Assert-Rejected { Initialize-ParentTransitionEvidenceFence } '*Fixture single-link metadata rejected*'
            $held = $script:transitionFenceLock; $script:rejectSingleLink = $false
            Enter-FixtureContainment $f; Assert-ParentTransitionContainmentFence
            Assert-Test ([object]::ReferenceEquals($held, $script:transitionFenceLock) -and $held.CanRead -and
                $null -eq $script:transitionFenceCapture.sha256 -and $script:transitionEvidence.Count -eq 0) 'Containment promoted or rebound incomplete capture.'
            Assert-Rejected { Initialize-ParentTransitionEvidenceFence } '*terminally disabled*'
            Assert-Rejected { Read-MainEvidence $f } '*terminally disabled*'
        }
    }
    Invoke-TestCase 'persistent parent and Transition metadata failure remains diagnostic with exact ownership and no normal authority' {
        foreach ($which in @('parent', 'transition')) {
            Invoke-ManagedFixture { param($f)
                $script:rejectSingleLink = $true
                $script:rejectSingleLinkPath = if ($which -ceq 'parent') { $f.parent } else { $f.transition }
                Enter-FixtureContainment $f; Assert-ParentTransitionContainmentFence
                Assert-Test ($script:fixtureDiagnostics.Count -eq 1 -and $script:fixtureDiagnostics[0] -like '*Fixture single-link metadata rejected*' -and
                    $script:transitionFenceCapture.sha256 -ceq $f.transition_hash -and $script:transitionEvidence.Count -eq 1 -and
                    $script:fenceLock.CanRead -and $script:transitionFenceLock.CanRead) 'Diagnostic metadata failure changed identity or ownership.'
                Assert-Rejected { Read-MainEvidence $f } '*terminally disabled*'
            }
        }
    }
    Invoke-TestCase 'Transition diagnostic disposal swap or coherent capture replacement is rejected after the diagnostic callback' {
        foreach ($fault in @('dispose', 'swap', 'capture')) {
            Invoke-ManagedFixture { param($f)
                Enter-FixtureContainment $f
                $script:rejectSingleLink = $true; $script:rejectSingleLinkPath = $f.transition
                $f.handles.Add($script:transitionFenceLock)
                $script:fixtureDiagnosticHook = {
                    param($message)
                    if ($fault -ceq 'dispose') { $script:transitionFenceLock.Dispose() }
                    elseif ($fault -ceq 'swap') { $script:transitionFenceLock = Open-FixtureRead $f $f.second }
                    else {
                        $old = $script:transitionFenceCapture
                        $script:transitionFenceCapture = [pscustomobject]@{ name = $old.name; path = $old.path; sha256 = $old.sha256
                            maximum_bytes = $old.maximum_bytes; handle = $old.handle; retired = $false }
                        $script:transitionEvidence.fence = $script:transitionFenceCapture
                    }
                }
                $pattern = if ($fault -ceq 'dispose') { '*exact live read-only exclusive Transition fence*' } else { '*ownership changed during its diagnostic evidence read*' }
                Assert-Rejected { Assert-ParentTransitionContainmentFence } $pattern
                Assert-Test ($script:fixtureDiagnostics.Count -eq 1 -and $script:transitionEvidenceMode -ceq 'containing') 'Post-diagnostic ownership fault did not traverse the expected boundary.'
                Assert-Rejected { Read-MainEvidence $f } '*terminally disabled*'
            }
        }
    }
    Invoke-TestCase 'parent diagnostic disposal or handle substitution is rejected after the diagnostic callback' {
        foreach ($fault in @('dispose', 'swap')) {
            Invoke-ManagedFixture { param($f)
                $script:ownerLease.Alive = $false; $script:fenceLock = Open-FixtureRead $f $f.parent
                $script:rejectSingleLink = $true; $script:rejectSingleLinkPath = $f.parent
                $script:fixtureDiagnosticHook = {
                    param($message)
                    if ($fault -ceq 'dispose') { $script:fenceLock.Dispose() }
                    else { $script:fenceLock = Open-FixtureRead $f $f.second }
                }
                $pattern = if ($fault -ceq 'dispose') { '*exact live read-only exclusive parent fence*' } else { '*ownership changed during its diagnostic evidence read*' }
                Assert-Rejected { Enter-ParentTransitionEvidenceContainment } $pattern
                Assert-Test ($script:fixtureDiagnostics.Count -eq 1 -and $script:transitionEvidenceMode -ceq 'containing') 'Parent post-diagnostic ownership fault bypassed the expected boundary.'
                Assert-Rejected { Read-MainEvidence $f } '*terminally disabled*'
            }
        }
    }
    Invoke-TestCase 'disposed and wrong-path parent handles remain hard failures before any diagnostic downgrade' {
        foreach ($fault in @('disposed', 'wrong_path')) {
            Invoke-ManagedFixture { param($f)
                $script:ownerLease.Alive = $false
                $path = if ($fault -ceq 'disposed') { $f.parent } else { $f.second }
                $script:fenceLock = Open-FixtureRead $f $path
                if ($fault -ceq 'disposed') { $script:fenceLock.Dispose() }
                Assert-Rejected { Enter-ParentTransitionEvidenceContainment } '*exact live read-only exclusive parent fence*'
                Assert-Test ($script:fixtureDiagnostics.Count -eq 0 -and $script:transitionEvidenceMode -ceq 'receiving') 'Invalid parent ownership was downgraded to a diagnostic.'
            }
        }
    }
    Invoke-TestCase 'unarmed or unknown parent fence identity cannot enter the containment-only downgrade' {
        Invoke-ManagedFixture { param($f)
            $script:ownerLease.Alive = $false; $script:fenceLock = Open-FixtureRead $f $f.parent
            foreach ($value in @($false, 'true', $null)) {
                $script:armed = $value
                Assert-Rejected { Enter-ParentTransitionEvidenceContainment } '*requires its armed lease and exact owner stopped*'
            }
            $script:armed = $true
            foreach ($value in @($null, '', ('z' * 64))) {
                $script:fenceHash = $value
                Assert-Rejected { Enter-ParentTransitionEvidenceContainment } '*requires its armed lease and exact owner stopped*'
            }
            Assert-Test ($script:fixtureDiagnostics.Count -eq 0 -and $script:transitionEvidenceMode -ceq 'receiving' -and
                $script:transitionFenceLock.CanRead) 'Unknown armed identity entered diagnostic-only containment.'
        }
    }
}
finally {
    if (Test-Path -LiteralPath $workspace) {
        Assert-Test ([IO.Path]::GetFullPath($workspace) -ceq $workspace -and [IO.Path]::GetDirectoryName($workspace) -ceq $tempRoot) 'Refusing cleanup outside the exact generated workspace.'
        $item = Get-Item -LiteralPath $workspace -Force
        Assert-Test (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -and
            @(Get-ChildItem -LiteralPath $workspace -Force).Count -eq 0) 'Nonempty or linked test workspace preserved.'
        Remove-Item -LiteralPath $workspace -Force -ErrorAction Stop
    }
}
[pscustomobject]@{
    format = 'stratus-parent-transition-evidence-tests-v1'; watchdog_path = $WatchdogPath
    expected_cases = $expectedCases; executed_cases = $script:caseCount; passed_cases = $script:passedCases
    failures = @($script:caseFailures.ToArray()); cleanup_failures = @($script:cleanupFailures.ToArray())
    managed_filesystem_only = $true; native_runtime_executed = $false; production_entry_executed = $false
    owner_liveness_stubbed = $true; ancestry_and_json_dependencies_stubbed = $true
    single_link_metadata_stubbed = $true; physical_file_identity_verified = $false
    full_protocol_accepted = $false; lock_provenance_accepted = $false
    fixture_cleanup_verified = -not (Test-Path -LiteralPath $workspace)
} | ConvertTo-Json -Depth 5 -Compress
if ($script:caseCount -ne $expectedCases -or $script:passedCases -ne $expectedCases -or
    $script:caseFailures.Count -ne 0 -or $script:cleanupFailures.Count -ne 0) {
    $global:LASTEXITCODE = 1
    throw ('Transition evidence regressions failed: ' + ($script:caseFailures -join '; ') + '; cases=' + $script:caseCount)
}
$global:LASTEXITCODE = 0
