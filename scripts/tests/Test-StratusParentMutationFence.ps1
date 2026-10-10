[CmdletBinding()]
param(
    [string] $WatchdogPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Windows PowerShell 5.1 does not reliably expose PSScriptRoot while evaluating
# this -File parameter default. Resolve the default only inside the script body.
if (-not $PSBoundParameters.ContainsKey('WatchdogPath')) {
    $WatchdogPath = Join-Path $PSScriptRoot '..\Invoke-StratusParentStageWatchdog.ps1'
}

# Managed filesystem and extracted-helper regressions only. Never execute the
# production entry, compile the native runtime, launch a process, or contact Docker.
# Passing these tests proves loss detection, NOT fence ownership or full bootstrap.
$expectedCases = 12
$script:caseCount = 0
$script:passedCases = 0
$script:caseFailures = [Collections.Generic.List[string]]::new()
$WatchdogPath = [IO.Path]::GetFullPath($WatchdogPath)
$source = [IO.File]::ReadAllText($WatchdogPath)
$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $WatchdogPath, [ref]$tokens, [ref]$parseErrors
)
$utf8 = [Text.UTF8Encoding]::new($false, $true)
$workspace = [IO.Path]::GetFullPath((Join-Path ([IO.Path]::GetTempPath()) (
    'stratus-parent-fence-tests-' + [guid]::NewGuid().ToString('N')
)))
$script:fenceLock = $null

function Assert-FenceTest {
    param([bool] $Condition, [string] $Message)
    if (-not $Condition) { throw $Message }
}

function Assert-FenceRejected {
    param([scriptblock] $Action, [string] $Pattern)
    $failure = $null
    try { & $Action | Out-Null }
    catch { $failure = $_.Exception.GetBaseException().Message }
    if ($null -eq $failure) { throw 'A rejected operation unexpectedly succeeded.' }
    if ($Pattern -and $failure -notlike $Pattern) {
        throw "Rejection did not reach the intended boundary: $failure"
    }
}

function Invoke-FenceCase {
    param([string] $Name, [scriptblock] $Action)
    $script:caseCount++
    try {
        & $Action
        $script:passedCases++
        Write-Output ('PASS mutation-fence: ' + $Name)
    }
    catch {
        $script:caseFailures.Add($Name + ': ' + $_.Exception.GetBaseException().Message)
        Write-Output ('FAIL mutation-fence: ' + $Name)
    }
}

function Get-FenceHelperAst {
    param([string] $Name)
    $definitions = @($ast.FindAll({
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -ceq $Name
    }, $false))
    if ($definitions.Count -ne 1) { throw "Expected one extracted helper: $Name" }
    return $definitions[0]
}

Invoke-FenceCase 'blocked entry and fence checks precede every heartbeat and bootstrap readiness' {
    Assert-FenceTest (@($parseErrors).Count -eq 0) 'Watchdog source does not parse.'
    $statements = @($ast.EndBlock.Statements)
    Assert-FenceTest ($statements.Count -ge 3 -and
        $statements[2] -is [Management.Automation.Language.ThrowStatementAst] -and
        $statements[2].Extent.Text -match 'WIP and non-authorizing') `
        'The unconditional early production entry blocker is missing.'
    $heartbeat = Get-FenceHelperAst 'Assert-ParentHeartbeat'
    $heartbeatStatements = @($heartbeat.Body.EndBlock.Statements)
    Assert-FenceTest ($heartbeatStatements.Count -gt 1 -and
        $heartbeatStatements[0].Extent.Text.Trim() -ceq 'Assert-ParentMutationFenceHeld' -and
        $heartbeatStatements[1].Extent.Text.Trim() -ceq 'Assert-ParentOwner') `
        'Fence loss must be checked before owner checks, heartbeat reads, or state updates.'
    $detector = Get-FenceHelperAst 'Assert-ParentMutationFenceHeld'
    $detectorStatements = @($detector.Body.EndBlock.Statements)
    Assert-FenceTest ($detectorStatements[0] -is [Management.Automation.Language.IfStatementAst] -and
        $detectorStatements[0].Extent.Text -match '\$script:fenceLock' -and
        $detectorStatements[0].Extent.Text -match '\bthrow\b' -and
        $detector.Extent.Text -notmatch '(?i)\.\s*(Dispose|Close)\s*\(') `
        'Recovered fence ownership must latch failure without disposal.'
    $bootstrapChecks = @($ast.FindAll({
        param($node)
        if ($node -isnot [Management.Automation.Language.CommandAst] -or
            $node.GetCommandName() -cne 'Assert-ParentMutationFenceHeld') { return $false }
        $ancestor = $node.Parent
        while ($null -ne $ancestor) {
            if ($ancestor -is [Management.Automation.Language.FunctionDefinitionAst]) { return $false }
            $ancestor = $ancestor.Parent
        }
        return $true
    }, $true))
    Assert-FenceTest ($bootstrapChecks.Count -eq 1) 'Bootstrap must have exactly one direct fence-loss check.'
    $bootstrapStatement = $bootstrapChecks[0].Parent
    while ($bootstrapStatement.Parent -isnot [Management.Automation.Language.StatementBlockAst]) {
        $bootstrapStatement = $bootstrapStatement.Parent
        if ($null -eq $bootstrapStatement) { throw 'Bootstrap statement boundary is missing.' }
    }
    $bootstrapStatements = @($bootstrapStatement.Parent.Statements)
    $bootstrapIndex = [Array]::IndexOf($bootstrapStatements, $bootstrapStatement)
    Assert-FenceTest ($bootstrapIndex -ge 1 -and $bootstrapIndex + 2 -lt $bootstrapStatements.Count -and
        $bootstrapStatements[$bootstrapIndex - 1].Extent.Text -match 'Parent armed acknowledgement differs' -and
        $bootstrapStatements[$bootstrapIndex + 1].Extent.Text.Trim() -ceq 'Assert-ParentHeartbeat' -and
        $bootstrapStatements[$bootstrapIndex + 2].Extent.Text -match '^Write-ParentAck ready\b') `
        'Bootstrap must check the fence after arming and before heartbeat/readiness publication.'
}

# Historical source is rejected above, before creation of any temporary artifact.
if ($script:caseFailures.Count -ne 0) {
    $global:LASTEXITCODE = 1
    throw ('Mutation-fence static contract failed before fixtures: ' + ($script:caseFailures -join '; '))
}
foreach ($name in @('Assert-ParentRegularAncestry', 'Open-ParentFenceOnce',
    'Assert-ParentMutationFenceHeld', 'Assert-ParentProperties', 'Get-ParentUtc',
    'Test-ParentInt32', 'Assert-ParentHeartbeat')) {
    $definition = Get-FenceHelperAst $name
    . ([scriptblock]::Create($definition.Extent.Text))
}

# Only the unrelated owner/document dependencies are replaced. The fence detector,
# filesystem opener, heartbeat ordering and heartbeat state transitions are real.
function Assert-ParentOwner { $script:ownerProbeCalls++ }
function Read-ParentDocument {
    param([string] $Path, [int] $MaximumBytes = 262144, [switch] $AtomicReplacement)
    Assert-FenceTest ($Path -ceq $script:paths.heartbeat -and
        $MaximumBytes -eq 8192 -and $AtomicReplacement) 'Unexpected heartbeat read dependency.'
    $script:heartbeatReadCalls++
    return [pscustomobject]@{ value = $script:heartbeatFixture; sha256 = ('c' * 64); path = $Path }
}

function Remove-OwnedFenceFixture {
    param([string] $Path)
    $target = [IO.Path]::GetFullPath($Path)
    $tempPrefix = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    Assert-FenceTest ($workspace.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $workspace) -cmatch '^stratus-parent-fence-tests-[0-9a-f]{32}$' -and
        $target.StartsWith($workspace + '\', [StringComparison]::OrdinalIgnoreCase) -and
        [IO.Path]::GetDirectoryName($target) -ceq $workspace -and
        (Split-Path -Leaf $target) -cmatch '^case-[0-9a-f]{32}$') `
        'Refusing cleanup outside the exact owned mutation-fence fixture.'
    if (-not (Test-Path -LiteralPath $target)) { return }
    $directory = Get-Item -LiteralPath $target -Force
    Assert-FenceTest ($directory.PSIsContainer -and
        -not ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint)) 'Refusing linked fixture cleanup.'
    $entries = @(Get-ChildItem -LiteralPath $target -Force)
    foreach ($entry in $entries) {
        Assert-FenceTest (-not $entry.PSIsContainer -and
            -not ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -and
            $entry.Name -cin @('fence.json', 'unrelated.txt')) `
            'Unknown or linked fixture artifact preserved instead of recursively removed.'
    }
    foreach ($entry in $entries) { Remove-Item -LiteralPath $entry.FullName -Force -ErrorAction Stop }
    Remove-Item -LiteralPath $target -Force -ErrorAction Stop
    Assert-FenceTest (-not (Test-Path -LiteralPath $target)) 'Owned fixture directory remained after cleanup.'
}

function Invoke-ManagedFenceFixture {
    param([scriptblock] $Action)
    $fixtureRoot = Join-Path $workspace ('case-' + [guid]::NewGuid().ToString('N'))
    [void][IO.Directory]::CreateDirectory($fixtureRoot)
    $fixture = [pscustomobject]@{
        root = $fixtureRoot; fence = (Join-Path $fixtureRoot 'fence.json')
        unrelated = (Join-Path $fixtureRoot 'unrelated.txt'); owner = $null
        others = [Collections.Generic.List[IDisposable]]::new()
    }
    [IO.File]::WriteAllText($fixture.fence, '{"owned_fixture":true}', $utf8)
    [IO.File]::WriteAllText($fixture.unrelated, 'unrelated artifact must remain unchanged', $utf8)
    $script:paths = [pscustomobject]@{
        mutation_fence = $fixture.fence; heartbeat = (Join-Path $fixtureRoot 'heartbeat.json')
        transition_start_revoke = (Join-Path $fixtureRoot 'never-created-revoke.json')
    }
    $script:fenceLock = $null
    $script:ownerProbeCalls = 0
    $script:heartbeatReadCalls = 0
    $script:leaseHash = 'b' * 64
    $script:watchClock = [Diagnostics.Stopwatch]::StartNew()
    $script:heartbeatSeenClock = [Diagnostics.Stopwatch]::StartNew()
    $script:heartbeatSequence = [long]0
    $script:heartbeatPublished = [DateTimeOffset]::MinValue
    $script:deadline = [DateTimeOffset]::UtcNow.AddMinutes(5)
    $script:maximumLifetime = 300
    $script:contract = [pscustomobject]@{
        session_id = ('a' * 32); heartbeat_ttl_seconds = 60
        owner = [pscustomobject]@{ pid = 42; start_time_utc = [DateTimeOffset]::UtcNow.AddMinutes(-5).ToString('o') }
    }
    $script:heartbeatFixture = [pscustomobject]@{
        format = 'stratus-parent-heartbeat-v1'; session_id = $script:contract.session_id
        lease_sha256 = $script:leaseHash; owner_pid = $script:contract.owner.pid
        owner_start_time_utc = $script:contract.owner.start_time_utc; sequence = [long]1
        published_at = [DateTimeOffset]::UtcNow.AddSeconds(-2).ToString('o')
    }
    try { & $Action $fixture }
    finally {
        try {
            if ($null -ne $script:fenceLock) { $script:fenceLock.Dispose() }
            if ($null -ne $fixture.owner) { $fixture.owner.Dispose() }
            foreach ($held in $fixture.others) { $held.Dispose() }
            $script:fenceLock = $null
            Assert-FenceTest ([IO.File]::ReadAllText($fixture.fence, $utf8) -ceq '{"owned_fixture":true}') `
                'Fence evidence was removed or modified.'
            Assert-FenceTest ([IO.File]::ReadAllText($fixture.unrelated, $utf8) -ceq
                'unrelated artifact must remain unchanged') 'Unrelated artifact was removed or modified.'
        }
        finally { Remove-OwnedFenceFixture $fixtureRoot }
    }
}

function Open-FixtureOwnerFence {
    param([object] $Fixture)
    $Fixture.owner = [IO.FileStream]::new($Fixture.fence, [IO.FileMode]::Open,
        [IO.FileAccess]::Read, [IO.FileShare]::None)
}

try {
    [void][IO.Directory]::CreateDirectory($workspace)
    Invoke-FenceCase 'held exclusive owner fence produces no output and no recovery latch' {
        Invoke-ManagedFenceFixture {
            param($fixture)
            Open-FixtureOwnerFence $fixture
            $output = @(Assert-ParentMutationFenceHeld)
            Assert-FenceTest ($output.Count -eq 0 -and $null -eq $script:fenceLock -and
                $fixture.owner.CanRead -and -not $fixture.owner.SafeFileHandle.IsClosed) 'Healthy held fence was changed.'
        }
    }
    Invoke-FenceCase 'release fails closed and retains the exact recovered exclusive handle' {
        Invoke-ManagedFenceFixture {
            param($fixture)
            Open-FixtureOwnerFence $fixture
            Assert-ParentMutationFenceHeld
            $fixture.owner.Dispose()
            Assert-FenceRejected { Assert-ParentMutationFenceHeld } '*was released*retained for containment*'
            Assert-FenceTest ($script:fenceLock -is [IO.FileStream] -and $script:fenceLock.CanRead -and
                -not $script:fenceLock.CanWrite -and $script:fenceLock.Name -ceq $fixture.fence) `
                'The recovered read-only fence was not retained.'
            foreach ($access in @([IO.FileAccess]::Read, [IO.FileAccess]::Write, [IO.FileAccess]::ReadWrite)) {
                Assert-FenceRejected {
                    $unexpected = [IO.FileStream]::new($fixture.fence, [IO.FileMode]::Open,
                        $access, [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)
                    $unexpected.Dispose()
                } '*'
            }
            Assert-FenceRejected { [IO.File]::Delete($fixture.fence) } '*'
        }
    }
    Invoke-FenceCase 'repeated loss checks cannot replace dispose or reopen the recovered handle' {
        Invoke-ManagedFenceFixture {
            param($fixture)
            Assert-FenceRejected { Assert-ParentMutationFenceHeld } '*was released*'
            $recovered = $script:fenceLock
            $script:paths.mutation_fence = Join-Path $fixture.root 'missing-after-recovery.json'
            foreach ($retry in 1..3) {
                Assert-FenceRejected { Assert-ParentMutationFenceHeld } '*already recovered*'
                Assert-FenceTest ([object]::ReferenceEquals($recovered, $script:fenceLock) -and
                    $recovered.CanRead -and -not $recovered.SafeFileHandle.IsClosed) 'A retry replaced or disposed recovery ownership.'
            }
            Assert-FenceRejected {
                $unexpected = [IO.File]::OpenRead($fixture.fence)
                $unexpected.Dispose()
            } '*'
        }
    }
    Invoke-FenceCase 'missing fence is rejected without creating it or latching a handle' {
        Invoke-ManagedFenceFixture {
            param($fixture)
            $script:paths.mutation_fence = Join-Path $fixture.root 'absent.json'
            Assert-FenceRejected { Assert-ParentMutationFenceHeld } '*Missing ancestry*'
            Assert-FenceTest ($null -eq $script:fenceLock -and
                -not (Test-Path -LiteralPath $script:paths.mutation_fence)) 'Missing fence was created or adopted.'
        }
    }
    Invoke-FenceCase 'relative noncanonical and empty paths are rejected without recovery' {
        Invoke-ManagedFenceFixture {
            param($fixture)
            foreach ($badPath in @('relative-fence.json', (Join-Path $fixture.root '.\fence.json'), '')) {
                $script:paths.mutation_fence = $badPath
                Assert-FenceRejected { Assert-ParentMutationFenceHeld } '*'
                Assert-FenceTest ($null -eq $script:fenceLock) 'Malformed path produced a recovery handle.'
            }
        }
    }
    Invoke-FenceCase 'directory path failures are not mistaken for sharing denial' {
        Invoke-ManagedFenceFixture {
            param($fixture)
            $script:paths.mutation_fence = $fixture.root
            Assert-FenceRejected { Assert-ParentMutationFenceHeld } '*'
            Assert-FenceTest ($null -eq $script:fenceLock) 'Directory path was accepted as a held fence.'
        }
    }
    Invoke-FenceCase 'lost fence stops heartbeat before owner probing reads or sequence progress' {
        Invoke-ManagedFenceFixture {
            param($fixture)
            Assert-FenceRejected { Assert-ParentHeartbeat } '*was released*'
            Assert-FenceTest ($script:ownerProbeCalls -eq 0 -and $script:heartbeatReadCalls -eq 0 -and
                $script:heartbeatSequence -eq 0 -and $null -ne $script:fenceLock) `
                'Heartbeat performed work after fence loss.'
        }
    }
    Invoke-FenceCase 'healthy heartbeat advances but subsequent fence loss blocks further progress' {
        Invoke-ManagedFenceFixture {
            param($fixture)
            Open-FixtureOwnerFence $fixture
            $output = @(Assert-ParentHeartbeat)
            $firstTime = $script:heartbeatPublished
            $script:heartbeatFixture.sequence = [long]2
            $script:heartbeatFixture.published_at = $firstTime.AddSeconds(1).ToString('o')
            $output += @(Assert-ParentHeartbeat)
            Assert-FenceTest ($output.Count -eq 0 -and $script:heartbeatSequence -eq 2 -and
                $script:heartbeatPublished -gt $firstTime -and $script:ownerProbeCalls -eq 2 -and
                $script:heartbeatReadCalls -eq 2 -and $null -eq $script:fenceLock) 'Healthy heartbeat did not advance normally.'
            $lastTime = $script:heartbeatPublished
            $script:heartbeatFixture.sequence = [long]3
            $script:heartbeatFixture.published_at = $lastTime.AddMilliseconds(1).ToString('o')
            $fixture.owner.Dispose()
            Assert-FenceRejected { Assert-ParentHeartbeat } '*was released*'
            Assert-FenceRejected { Assert-ParentHeartbeat } '*already recovered*'
            Assert-FenceTest ($script:heartbeatSequence -eq 2 -and $script:heartbeatPublished -eq $lastTime -and
                $script:ownerProbeCalls -eq 2 -and $script:heartbeatReadCalls -eq 2) `
                'Heartbeat advanced after recovery had latched failure.'
        }
    }
    Invoke-FenceCase 'heartbeat rejects coercible and out-of-range owner identifiers before progress' {
        foreach ($badOwner in @('42', [double]42, $true, [long]2147483648)) {
            Invoke-ManagedFenceFixture {
                param($fixture)
                Open-FixtureOwnerFence $fixture
                $script:heartbeatFixture.owner_pid = $badOwner
                Assert-FenceRejected { Assert-ParentHeartbeat } '*owner must be a native integer*'
                Assert-FenceTest ($script:heartbeatSequence -eq 0 -and
                    $script:heartbeatPublished -eq [DateTimeOffset]::MinValue) 'Malformed owner advanced heartbeat.'
            }
        }
    }
    Invoke-FenceCase 'heartbeat rejects non-native identity strings before progress' {
        foreach ($field in @('format', 'session_id', 'lease_sha256', 'owner_start_time_utc', 'published_at')) {
            Invoke-ManagedFenceFixture {
                param($fixture)
                Open-FixtureOwnerFence $fixture
                $script:heartbeatFixture.$field = 42
                Assert-FenceRejected { Assert-ParentHeartbeat } '*identity must use native strings*'
                Assert-FenceTest ($script:heartbeatSequence -eq 0 -and
                    $script:heartbeatPublished -eq [DateTimeOffset]::MinValue) 'Malformed identity advanced heartbeat.'
            }
        }
    }
    Invoke-FenceCase 'compatible composite holders remain a documented ownership-proof gap' {
        Invoke-ManagedFenceFixture {
            param($fixture)
            $reader = [IO.FileStream]::new($fixture.fence, [IO.FileMode]::Open,
                [IO.FileAccess]::Read, [IO.FileShare]::Write)
            $fixture.others.Add($reader)
            $writer = [IO.FileStream]::new($fixture.fence, [IO.FileMode]::Open,
                [IO.FileAccess]::Write, [IO.FileShare]::Read)
            $fixture.others.Add($writer)
            $output = @(Assert-ParentMutationFenceHeld)
            Assert-FenceTest ($output.Count -eq 0 -and $null -eq $script:fenceLock -and
                $reader.CanRead -and $writer.CanWrite) 'The test must not claim sharing denial proves ownership.'
        }
    }
}
finally {
    if (Test-Path -LiteralPath $workspace) {
        $rootItem = Get-Item -LiteralPath $workspace -Force
        if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            @(Get-ChildItem -LiteralPath $workspace -Force).Count -ne 0) {
            throw 'Nonempty or linked mutation-fence test root was preserved for investigation.'
        }
        Remove-Item -LiteralPath $workspace -Force -ErrorAction Stop
    }
}

[pscustomobject]@{
    format = 'stratus-parent-mutation-fence-tests-v1'; watchdog_path = $WatchdogPath
    expected_cases = $expectedCases; executed_cases = $script:caseCount
    passed_cases = $script:passedCases; failures = @($script:caseFailures.ToArray())
    managed_filesystem_only = $true; native_runtime_executed = $false
    production_entry_executed = $false; protocol_acceptance = $false
    fixture_cleanup_verified = -not (Test-Path -LiteralPath $workspace)
} | ConvertTo-Json -Depth 5 -Compress
if ($script:caseCount -ne $expectedCases -or $script:passedCases -ne $expectedCases -or
    $script:caseFailures.Count -ne 0) {
    $global:LASTEXITCODE = 1
    throw ('Mutation-fence regression failed: ' + ($script:caseFailures -join '; '))
}
$global:LASTEXITCODE = 0
