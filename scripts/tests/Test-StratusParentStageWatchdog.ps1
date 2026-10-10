[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# These are primitive/entry-blocker regressions, NOT acceptance of the incomplete
# parent-stage state machine. No Docker daemon or application database is contacted.
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$scriptPath = Join-Path $root 'scripts\Invoke-StratusParentStageWatchdog.ps1'
$runtimePath = Join-Path $root 'scripts\Stratus.ParentStage.Runtime.cs'
$source = [IO.File]::ReadAllText($scriptPath)
$runtimeSource = [IO.File]::ReadAllText($runtimePath)
$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$parseErrors)
if (@($parseErrors).Count -ne 0) { throw 'Parent-stage watchdog source does not parse.' }
$statements = @($ast.EndBlock.Statements)
if ($statements.Count -lt 3 -or
    $statements[2] -isnot [Management.Automation.Language.ThrowStatementAst] -or
    $statements[2].Extent.Text -notmatch 'WIP and non-authorizing') {
    throw 'An unconditional WIP entry blocker must precede artifacts, Add-Type and processes.'
}
$artifactAssignment = @($ast.FindAll({ param($node)
    $node -is [Management.Automation.Language.AssignmentStatementAst] -and
    $node.Left -is [Management.Automation.Language.VariableExpressionAst] -and
    $node.Left.VariablePath.UserPath -ceq 'artifactNames'
}, $false))
if ($artifactAssignment.Count -ne 1) { throw 'Parent artifact universe is ambiguous.' }
$artifactLiterals = @($artifactAssignment[0].Right.FindAll({ param($node)
    $node -is [Management.Automation.Language.StringConstantExpressionAst]
}, $false) | ForEach-Object { $_.Value })
if ($artifactLiterals.Count -ne 40 -or @($artifactLiterals | Sort-Object -Unique).Count -ne 40) {
    throw 'The blocked parent protocol must document exactly 40 distinct attempt artifact names.'
}
foreach ($required in @(
    'CreateProcess suspended', 'Assign suspended process to Job',
    'Verify suspended Job membership', 'previous != 1', 'wait != 258',
    'ProveJobEmpty(5000', 'Explicit inherited handle list',
    'Native output overflow', 'File.Replace(temporary, path, null)',
    'StringComparer.OrdinalIgnoreCase', 'Duplicate JSON key'
)) {
    if (-not $runtimeSource.Contains($required)) { throw "Missing native contract: $required" }
}
foreach ($forbidden in @('ReadToEndAsync', 'Process.Start(', 'catch { }', 'SilentlyContinue')) {
    if ($runtimeSource.Contains($forbidden) -or $source.Contains($forbidden)) {
        throw "Unsafe parent-stage primitive found: $forbidden"
    }
}
$dockerFunction = @($ast.FindAll({ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq 'Invoke-ParentDocker'
}, $false))
if ($dockerFunction.Count -ne 1 -or
    $dockerFunction[0].Extent.Text -notmatch "@\('--context', 'desktop-linux'\)" -or
    $dockerFunction[0].Extent.Text -notmatch "@\('context', 'info', 'inspect', 'ps', 'stop', 'compose', 'image'\)") {
    throw 'Docker containment allowlist/context contract differs.'
}
$receiverGuard = @($ast.FindAll({ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -ceq 'Assert-ParentSuccessorSupervisor'
}, $false))
$receiverCheckIndex = $source.IndexOf('                    Assert-ParentSuccessorSupervisor', [StringComparison]::Ordinal)
$acceptedTerminalIndex = $source.IndexOf("status = 'handoff_accepted'", [StringComparison]::Ordinal)
if ($receiverGuard.Count -ne 1 -or $receiverCheckIndex -lt 0 -or $acceptedTerminalIndex -lt 0 -or
    $receiverGuard[0].Body.EndBlock.Statements[0] -isnot [Management.Automation.Language.ThrowStatementAst] -or
    $receiverCheckIndex -gt $acceptedTerminalIndex) {
    throw 'No accepted terminal may precede an independently armed downstream supervisor.'
}

Add-Type -Path $runtimePath -ErrorAction Stop
$workspace = Join-Path ([IO.Path]::GetTempPath()) ('stratus-parent-primitives-' + [Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($workspace)
$workspace = [IO.Path]::GetFullPath($workspace)
$activeChildren = [Collections.Generic.List[IDisposable]]::new()
$testFailures = [Collections.Generic.List[string]]::new()
$passed = 0
$script:validatedCompletions = @{}

function Assert-Primitive {
    param([bool] $Condition, [string] $Message)
    if (-not $Condition) { throw $Message }
}

function Assert-Rejected {
    param([scriptblock] $Action, [string] $Message)
    $rejected = $false
    try { & $Action }
    catch { $rejected = $true }
    if (-not $rejected) { throw $Message }
}

function Invoke-PrimitiveCase {
    param([string] $Name, [scriptblock] $Action)
    try { & $Action; $script:passed += 1; Write-Output ('PASS primitive: ' + $Name) }
    catch { $testFailures.Add($Name + ': ' + $_.Exception.GetBaseException().Message) }
}

function Invoke-HermeticChild {
    param([string[]] $Arguments, [int] $MaximumBytes = 4096, [int] $TimeoutMilliseconds = 5000)
    $child = $null
    $primary = $null
    $result = $null
    try {
        $child = [Stratus.ParentStage.NativeChild]::StartSuspended($fakeExecutable, $fakeHash,
            $fakeLock, $Arguments, $workspace, $MaximumBytes)
        $child.Resume()
        $result = $child.Wait($TimeoutMilliseconds)
    }
    catch { $primary = $_.Exception }
    finally {
        if ($null -ne $child) {
            try { $child.Dispose() }
            catch { $primary = [Stratus.ParentStage.Files]::Combine($primary, $_.Exception) }
        }
    }
    if ($null -ne $primary) { throw $primary }
    return $result
}

# Execute only individually parsed helpers under hermetic substitutes. The blocked
# production entry/state machine is never dot-sourced or made executable by tests.
foreach ($helper in @('Add-ParentFailure', 'Assert-ParentRegularAncestry', 'ConvertFrom-ParentJson',
    'Get-ParentProcessIdentity', 'Assert-ParentBootstrapHeldFile', 'Read-ParentBootstrapPublication',
    'New-ParentBootstrapFrame', 'Write-ParentBootstrapReady', 'Write-ParentAck', 'Write-ParentDocument', 'Get-ParentNonAuthorization',
    'Read-ParentDocument', 'Write-ParentRecoveryDocument', 'New-ParentContainmentProgress',
    'Get-ParentContainmentContainer', 'Test-ParentValidatedCompletionUnchanged',
    'Read-ParentCanonicalManifest', 'Get-ParentR1Utc', 'Get-ParentOptionalProperty', 'Test-ParentInt32', 'Assert-ParentIntendedContainer',
    'Invoke-ParentContainerContainmentRound', 'Invoke-ParentContainment')) {
    $definition = @($ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $helper
    }, $false))
    if ($definition.Count -ne 1) { throw "Hermetic helper is ambiguous: $helper" }
    if ($helper -ceq 'Invoke-ParentContainerContainmentRound' -and
        @($definition[0].FindAll({ param($node)
            $node -is [Management.Automation.Language.WhileStatementAst] -or
            $node -is [Management.Automation.Language.DoWhileStatementAst] -or
            $node -is [Management.Automation.Language.DoUntilStatementAst]
        }, $true)).Count -ne 0) {
        throw 'A containment round cannot contain an unbounded retry loop.'
    }
    . ([scriptblock]::Create($definition[0].Extent.Text))
}

function Invoke-BootstrapFixture {
    param([scriptblock] $Action)
    $fixture = Join-Path $workspace ('bootstrap-fixture-' + [Guid]::NewGuid().ToString('N'))
    [void][IO.Directory]::CreateDirectory($fixture)
    $ContractPath = Join-Path $fixture 'contract.json'
    $fixtureScript = Join-Path $fixture 'source.ps1'
    [IO.File]::WriteAllText($ContractPath, '{"contract":true}', [Stratus.ParentStage.Files]::Utf8)
    [IO.File]::WriteAllText($fixtureScript, '# hermetic producer source', [Stratus.ParentStage.Files]::Utf8)
    $ContractSha256 = [Stratus.ParentStage.Files]::Hash([IO.File]::ReadAllBytes($ContractPath))
    $RuntimeSha256 = [Stratus.ParentStage.Files]::Hash([IO.File]::ReadAllBytes($runtimePath))
    $watchdogIdentity = Get-ParentProcessIdentity $PID
    $contract = [pscustomobject]@{ session_id = ('b' * 32); watchdog = [pscustomobject]@{
        script_path = $fixtureScript; script_sha256 = [Stratus.ParentStage.Files]::Hash([IO.File]::ReadAllBytes($fixtureScript))
        runtime_path = $runtimePath; runtime_sha256 = $RuntimeSha256
        host_path = $watchdogIdentity.executable
        host_sha256 = [Stratus.ParentStage.Files]::Hash([IO.File]::ReadAllBytes($watchdogIdentity.executable))
    } }
    $paths = [pscustomobject]@{ lease = (Join-Path $fixture 'lease.json'); ready = (Join-Path $fixture 'ready.json') }
    [IO.File]::WriteAllText($paths.lease, '{"lease":true}', [Stratus.ParentStage.Files]::Utf8)
    [IO.File]::WriteAllText($paths.ready, '{"ready":true}', [Stratus.ParentStage.Files]::Utf8)
    $script:leaseHash = [Stratus.ParentStage.Files]::Hash([IO.File]::ReadAllBytes($paths.lease))
    $observed = @{ ready = [pscustomobject]@{ sha256 = [Stratus.ParentStage.Files]::Hash([IO.File]::ReadAllBytes($paths.ready)) } }
    $locks = [Collections.Generic.List[IDisposable]]::new()
    $script:armed = $true
    $script:bootstrapWriteAttempted = $false
    $script:bootstrapOutput = $null
    try {
        $script:contractLock = [Stratus.ParentStage.Files]::Lock($ContractPath, $ContractSha256); $locks.Add($script:contractLock)
        $script:watchdogScriptLock = [Stratus.ParentStage.Files]::Lock($fixtureScript, $contract.watchdog.script_sha256); $locks.Add($script:watchdogScriptLock)
        $script:runtimeLock = [Stratus.ParentStage.Files]::Lock($runtimePath, $RuntimeSha256); $locks.Add($script:runtimeLock)
        $script:hostLock = [Stratus.ParentStage.Files]::Lock($watchdogIdentity.executable, $contract.watchdog.host_sha256); $locks.Add($script:hostLock)
        & $Action
    }
    finally {
        for ($index = $locks.Count - 1; $index -ge 0; $index--) { $locks[$index].Dispose() }
        $script:armed = $false
    }
}

try {
    Invoke-PrimitiveCase 'strict JSON accepts exact objects and valid escaped unicode' {
        foreach ($json in @('{}', '{"a":[true,false,null,1,-2,0.5,1e2],"b":"\ud83c\udf26"}')) {
            [Stratus.ParentStage.StrictJson]::Validate([Text.UTF8Encoding]::new($false).GetBytes($json))
        }
    }
    Invoke-PrimitiveCase 'native JSON integer policy agrees across shells without coercion' {
        $document = ConvertFrom-ParentJson ([Stratus.ParentStage.Files]::Utf8.GetBytes('{"pid":42}'))
        Assert-Primitive (Test-ParentInt32 $document.pid) 'Native integer emitted by this shell was rejected.'
        foreach ($valid in @([int]0, [long]42, [long][int]::MaxValue)) {
            Assert-Primitive (Test-ParentInt32 $valid) 'Bounded native integer rejected.'
        }
        foreach ($invalid in @('42', [double]42, $true, $null, ([long][int]::MaxValue + 1L))) {
            Assert-Primitive (-not (Test-ParentInt32 $invalid)) 'Coercible or out-of-range value was accepted as a native process integer.'
        }
    }
    Invoke-PrimitiveCase 'duplicate/case/escaped keys and non-JSON grammar fail closed' {
        foreach ($json in @('{"x":1,"x":2}', '{"x":1,"X":2}', '{"x":1,"\u0078":2}',
            '{"x":NaN}', '{"x":01}', '{"x":1,}', '{} {}', '[]', '{/*comment*/}',
            '{"x":"\ud800"}', '{"x":.5}', '{"x":1e}', '{"x":+1}')) {
            $bytes = [Text.UTF8Encoding]::new($false).GetBytes($json)
            Assert-Rejected { [Stratus.ParentStage.StrictJson]::Validate($bytes) } ('Accepted malformed JSON: ' + $json)
        }
    }
    Invoke-PrimitiveCase 'BOM and malformed UTF-8 rejected' {
        Assert-Rejected { [Stratus.ParentStage.StrictJson]::Validate([byte[]]@(239,187,191,123,125)) } 'Accepted BOM.'
        Assert-Rejected { [Stratus.ParentStage.StrictJson]::Validate([byte[]]@(123,34,120,34,58,34,255,34,125)) } 'Accepted invalid UTF-8.'
    }
    Invoke-PrimitiveCase 'create-new publication cannot replace old evidence and cleans its temporary' {
        $path = Join-Path $workspace 'create-new.json'
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes('{"one":1}')
        [Stratus.ParentStage.Files]::PublishNew($path, $bytes)
        Assert-Rejected { [Stratus.ParentStage.Files]::PublishNew($path, $bytes) } 'Accepted duplicate publication.'
        Assert-Primitive (([IO.File]::ReadAllText($path)) -ceq '{"one":1}') 'Old evidence changed.'
        Assert-Primitive (@(Get-ChildItem -LiteralPath $workspace -Filter '*.publishing-*').Count -eq 0) 'Leaked temporary evidence.'
    }
    Invoke-PrimitiveCase 'replacement publication is atomic and read-lock failure is surfaced' {
        $path = Join-Path $workspace 'replace.json'
        [Stratus.ParentStage.Files]::PublishNew($path, [Text.UTF8Encoding]::new($false).GetBytes('{"n":1}'))
        [Stratus.ParentStage.Files]::ReplacePublished($path, [Text.UTF8Encoding]::new($false).GetBytes('{"n":2}'))
        $held = [Stratus.ParentStage.Files]::Lock($path, [Stratus.ParentStage.Files]::Hash([IO.File]::ReadAllBytes($path)))
        try {
            Assert-Rejected { [Stratus.ParentStage.Files]::ReplacePublished($path, [Text.UTF8Encoding]::new($false).GetBytes('{"n":3}')) } 'Replaced read-locked evidence.'
        }
        finally { $held.Dispose() }
        Assert-Primitive (([IO.File]::ReadAllText($path)) -ceq '{"n":2}') 'Failed replacement changed evidence.'
        Assert-Primitive (@(Get-ChildItem -LiteralPath $workspace -Filter '*.publishing-*').Count -eq 0) 'Replacement leaked temporary evidence.'
    }
    Invoke-PrimitiveCase 'bounded artifact reads reject oversize content' {
        $path = Join-Path $workspace 'create-new.json'
        Assert-Rejected { [void][Stratus.ParentStage.Files]::ReadBounded($path, 2) } 'Oversize artifact accepted.'
    }
    Invoke-PrimitiveCase 'borrowed fences prove denial of read write and delete access independently' {
        $path = Join-Path $workspace 'borrowed-fence.json'
        [Stratus.ParentStage.Files]::PublishNew($path, [Stratus.ParentStage.Files]::Utf8.GetBytes('{}'))
        foreach ($sharing in @([IO.FileShare]::None, [IO.FileShare]::Read, [IO.FileShare]::Write, [IO.FileShare]::Delete,
            ([IO.FileShare]::Read -bor [IO.FileShare]::Write -bor [IO.FileShare]::Delete))) {
            $stream = [IO.FileStream]::new($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, $sharing)
            try {
                if ($sharing -eq [IO.FileShare]::None) { [Stratus.ParentStage.Files]::AssertExclusiveFence($stream) }
                else { Assert-Rejected { [Stratus.ParentStage.Files]::AssertExclusiveFence($stream) } 'A sharing fence was accepted as exclusive.' }
                Assert-Primitive ($stream.CanRead -and $stream.ReadByte() -eq 123) 'Fence probe disposed or modified the borrowed stream.'
            }
            finally { $stream.Dispose() }
        }
        Assert-Rejected { [Stratus.ParentStage.Files]::AssertExclusiveFence($stream) } 'A disposed fence was accepted.'
        Assert-Primitive ([IO.File]::ReadAllText($path) -ceq '{}') 'Access probe wrote, deleted or renamed evidence.'
    }
    Invoke-PrimitiveCase 'composite holders fool legacy probes but cannot enter exact fence acquisition' {
        $path = Join-Path $workspace 'exact-fence-composite.json'
        $initial = [Stratus.ParentStage.Files]::Utf8.GetBytes('{"n":1}')
        $changed = [Stratus.ParentStage.Files]::Utf8.GetBytes('{"n":2}')
        [IO.File]::WriteAllBytes($path, $initial)
        $reader = [IO.FileStream]::new($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Write)
        $writer = $null
        try {
            $writer = [IO.FileStream]::new($path, [IO.FileMode]::Open, [IO.FileAccess]::Write, [IO.FileShare]::Read)
            # The legacy helper observes three ERROR_SHARING_VIOLATION probes and
            # returns successfully, even though this already-held writer can write.
            [Stratus.ParentStage.Files]::AssertExclusiveFence($reader)
            $acquisitionFailure = $null
            try { [void][Stratus.ParentStage.ExactFence]::AcquireExisting($path, [Stratus.ParentStage.Files]::Hash($initial)) }
            catch { $acquisitionFailure = $_.Exception.GetBaseException() }
            Assert-Primitive ($null -ne $acquisitionFailure -and $acquisitionFailure -is [IO.IOException] -and
                ($acquisitionFailure.HResult -band 65535) -eq 32) 'Exact factory did not reject composite holders through native sharing exclusion.'
            $writer.Write($changed, 0, $changed.Length); $writer.Flush($true)
            $actual = [byte[]]::new($changed.Length)
            Assert-Primitive ($reader.Read($actual, 0, $actual.Length) -eq $actual.Length) 'Composite writer observation was incomplete.'
            Assert-Primitive ([Stratus.ParentStage.Files]::Hash($actual) -ceq [Stratus.ParentStage.Files]::Hash($changed)) 'Composite test did not demonstrate an actual mutation.'
            Assert-Primitive ($reader.CanRead -and $writer.CanWrite) 'Rejected exact acquisition closed foreign holders.'
        }
        finally { if ($null -ne $writer) { $writer.Dispose() }; $reader.Dispose() }
        Assert-Primitive ([Stratus.ParentStage.Files]::Hash([IO.File]::ReadAllBytes($path)) -ceq [Stratus.ParentStage.Files]::Hash($changed)) 'Rejected factory changed or deleted the composite fixture.'
        $after = [Stratus.ParentStage.ExactFence]::AcquireExisting($path, [Stratus.ParentStage.Files]::Hash($changed))
        try { $after.AssertHeld() }
        finally { $after.Dispose() }
    }
    Invoke-PrimitiveCase 'exact fence acquisition preserves bounded bytes and excludes read write delete' {
        $path = Join-Path $workspace 'exact-fence-exclusive.json'
        $bytes = [Stratus.ParentStage.Files]::Utf8.GetBytes('{"p":"' + ('x' * 8184) + '"}')
        Assert-Primitive ($bytes.Length -eq 8192) 'Exact byte-bound fixture differs.'
        $hash = [Stratus.ParentStage.Files]::Hash($bytes)
        [IO.File]::WriteAllBytes($path, $bytes)
        $fence = [Stratus.ParentStage.ExactFence]::AcquireExisting($path, $hash)
        try {
            Assert-Primitive ($fence.Path -ceq $path -and $fence.Sha256 -ceq $hash) 'Exact fence identity differs.'
            $fence.AssertHeld()
            Assert-Primitive ([Stratus.ParentStage.Files]::Hash($fence.ReadBytes()) -ceq $hash) 'Exact held bytes differ.'
            Assert-Rejected { [void][IO.File]::ReadAllBytes($path) } 'Exact acquisition allowed another reader.'
            Assert-Rejected { [IO.File]::WriteAllText($path, 'forbidden') } 'Exact acquisition allowed writing.'
            Assert-Rejected { [IO.File]::Delete($path) } 'Exact acquisition allowed deleting its file.'
            $fence.AssertHeld()
        }
        finally { $fence.Dispose() }
        Assert-Primitive ([Stratus.ParentStage.Files]::Hash([IO.File]::ReadAllBytes($path)) -ceq $hash) 'Fence disposal mutated or deleted its artifact.'
    }
    Invoke-PrimitiveCase 'exact fence rejection preserves files and releases every acquired handle' {
        foreach ($mode in @('wrong-hash', 'uppercase-hash', 'malformed', 'oversize', 'empty')) {
            $path = Join-Path $workspace ('exact-fence-reject-' + $mode + '.json')
            [byte[]]$bytes = [Stratus.ParentStage.Files]::Utf8.GetBytes('{}')
            $expectedError = '*hash differs*'
            if ($mode -ceq 'malformed') { $bytes = [Stratus.ParentStage.Files]::Utf8.GetBytes('[]'); $expectedError = '*JSON*' }
            if ($mode -ceq 'oversize') { $bytes = [Stratus.ParentStage.Files]::Utf8.GetBytes('{}' + (' ' * 8191)); $expectedError = '*byte bound*' }
            if ($mode -ceq 'empty') { $bytes = [byte[]]@(); $expectedError = '*byte bound*' }
            $actualHash = [Stratus.ParentStage.Files]::Hash($bytes)
            $expectedHash = $actualHash
            if ($mode -ceq 'wrong-hash') { $expectedHash = '0' * 64 }
            if ($mode -ceq 'uppercase-hash') { $expectedHash = 'A' * 64; $expectedError = '*lowercase*' }
            [IO.File]::WriteAllBytes($path, $bytes)
            $failure = ''
            try { [void][Stratus.ParentStage.ExactFence]::AcquireExisting($path, $expectedHash) }
            catch { $failure = $_.Exception.GetBaseException().Message }
            Assert-Primitive ($failure -like $expectedError) 'Invalid exact fence did not fail at its required validation boundary.'
            Assert-Primitive ((Test-Path -LiteralPath $path) -and [Stratus.ParentStage.Files]::Hash([IO.File]::ReadAllBytes($path)) -ceq $actualHash) 'Rejected exact fence deleted or changed existing bytes.'
            $probe = [IO.FileStream]::new($path, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
            $probe.Dispose()
        }
        $absent = Join-Path $workspace 'exact-fence-never-created.json'
        Assert-Rejected { [void][Stratus.ParentStage.ExactFence]::AcquireExisting($absent, ('0' * 64)) } 'Absent exact fence was accepted.'
        Assert-Primitive (-not (Test-Path -LiteralPath $absent)) 'Exact acquisition created an absent artifact.'
    }
    Invoke-PrimitiveCase 'exact fence returns defensive copies and has no raw handle import or live disposal reuse' {
        $path = Join-Path $workspace 'exact-fence-defensive.json'
        $bytes = [Stratus.ParentStage.Files]::Utf8.GetBytes('{"owned":true}')
        $hash = [Stratus.ParentStage.Files]::Hash($bytes)
        [IO.File]::WriteAllBytes($path, $bytes)
        $fence = [Stratus.ParentStage.ExactFence]::AcquireExisting($path, $hash)
        try {
            $first = $fence.ReadBytes(); $second = $fence.ReadBytes()
            Assert-Primitive (-not [object]::ReferenceEquals($first, $second)) 'Exact fence exported its private byte array.'
            $first[0] = 0
            $fence.AssertHeld()
            Assert-Primitive ([Stratus.ParentStage.Files]::Hash($fence.ReadBytes()) -ceq $hash) 'Mutating a returned copy changed owned fence evidence.'
            $type = $fence.GetType()
            Assert-Primitive ($type.IsSealed -and $type.GetConstructors().Count -eq 0) 'Exact capability has a public import constructor.'
            foreach ($property in $type.GetProperties()) {
                Assert-Primitive (-not $property.CanWrite -and $property.PropertyType -ne [IO.FileStream] -and
                    -not [Runtime.InteropServices.SafeHandle].IsAssignableFrom($property.PropertyType)) 'Exact capability exposes mutable metadata or a raw handle.'
            }
        }
        finally { $fence.Dispose() }
        $fence.Dispose()
        Assert-Rejected { $fence.AssertHeld() } 'Disposed exact fence still claimed ownership.'
        Assert-Rejected { [void]$fence.ReadBytes() } 'Disposed exact fence returned operational bytes.'
        # Invoke getters explicitly: PowerShell's property adapter suppresses getter exceptions.
        Assert-Rejected { [void]$fence.get_Path() } 'Disposed exact fence exposed a live path property.'
        Assert-Rejected { [void]$fence.get_Sha256() } 'Disposed exact fence exposed a live hash property.'
        $probe = [IO.FileStream]::new($path, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        $probe.Dispose()
        Assert-Primitive ([Stratus.ParentStage.Files]::Hash([IO.File]::ReadAllBytes($path)) -ceq $hash) 'Repeated disposal altered the artifact.'
    }
    Invoke-PrimitiveCase 'bootstrap producer emits exact native field order string PID hashes and one LF' {
        Invoke-BootstrapFixture {
            $bytes = New-ParentBootstrapFrame ('a' * 64)
            $expected = '{"format":"stratus-independent-watchdog-ready-v1","session_id":"' + $contract.session_id +
                '","nonce":"' + ('a' * 64) + '","contract_sha256":"' + $ContractSha256 +
                '","source_sha256":"' + $contract.watchdog.script_sha256 + '","runtime_sha256":"' + $RuntimeSha256 +
                '","host_sha256":"' + $contract.watchdog.host_sha256 + '","pid":"' + $PID +
                '","start_time_utc":"' + $watchdogIdentity.start_time_utc + '","lease_sha256":"' + $script:leaseHash +
                '","ready_sha256":"' + $observed.ready.sha256 + '"}' + "`n"
            Assert-Primitive ($bytes -is [byte[]] -and [Stratus.ParentStage.Files]::Utf8.GetString($bytes) -ceq $expected) 'Producer bytes differ from native launch-bound frame contract.'
            Assert-Primitive ($bytes[0] -eq 123 -and $bytes[-1] -eq 10 -and @($bytes | Where-Object { $_ -eq 10 }).Count -eq 1 -and
                @($bytes | Where-Object { $_ -eq 13 }).Count -eq 0) 'Producer added BOM, CRLF or extra frames.'
            foreach ($path in @($paths.lease, $paths.ready)) {
                Assert-Rejected { [IO.File]::WriteAllText($path, '{}') } 'Producer released a published receipt after validation.'
            }
        }
    }
    Invoke-PrimitiveCase 'bootstrap rejects noncanonical nonce unarmed session and mismatched current identity' {
        Invoke-BootstrapFixture {
            foreach ($nonce in @('', ('A' * 64), ('a' * 63), ('a' * 65), (('a' * 64) + "`n"), ('g' * 64))) {
                Assert-Rejected { [void](New-ParentBootstrapFrame $nonce) } 'Noncanonical bootstrap nonce accepted.'
            }
            $script:armed = $false
            Assert-Rejected { [void](New-ParentBootstrapFrame ('a' * 64)) } 'Unarmed producer emitted a frame.'
            $script:armed = $true
            $watchdogIdentity.pid += 1
            Assert-Rejected { [void](New-ParentBootstrapFrame ('a' * 64)) } 'Caller identity replaced the actual producer PID.'
            $watchdogIdentity.pid = $PID
            $watchdogIdentity.start_time_utc = [DateTimeOffset]::UtcNow.AddDays(-1).ToString('o')
            Assert-Rejected { [void](New-ParentBootstrapFrame ('a' * 64)) } 'Wrong producer start time accepted.'
        }
    }
    Invoke-PrimitiveCase 'bootstrap rejects mismatched and closed held program or contract bytes' {
        $writablePath = Join-Path $workspace 'bootstrap-writable-source.txt'
        [IO.File]::WriteAllText($writablePath, 'held', [Stratus.ParentStage.Files]::Utf8)
        $writable = [IO.FileStream]::new($writablePath, 'Open', 'ReadWrite', 'Read')
        try {
            Assert-Rejected { [void](Assert-ParentBootstrapHeldFile $writable $writablePath ([Stratus.ParentStage.Files]::HashStream($writable))) } 'Writable source handle was accepted as a held read-only source.'
        }
        finally { $writable.Dispose() }
        foreach ($kind in @('contract', 'source', 'runtime', 'host')) {
            Invoke-BootstrapFixture {
                $held = switch ($kind) { contract { $script:contractLock }; source { $script:watchdogScriptLock }; runtime { $script:runtimeLock }; host { $script:hostLock } }
                Assert-Rejected { [void](Assert-ParentBootstrapHeldFile $held $held.Name ('0' * 64)) } 'Wrong held source hash accepted.'
                Assert-Rejected { [void](Assert-ParentBootstrapHeldFile $held $paths.lease ([Stratus.ParentStage.Files]::HashStream($held))) } 'Wrong held source path accepted.'
                $held.Dispose()
                Assert-Rejected { [void](New-ParentBootstrapFrame ('a' * 64)) } 'Closed held source accepted by producer.'
            }
        }
    }
    Invoke-PrimitiveCase 'bootstrap receipt readback rejects drift malformed bytes and oversize before hashing' {
        foreach ($kind in @('lease', 'ready')) {
            foreach ($fault in @('drift', 'malformed', 'oversize')) {
                Invoke-BootstrapFixture {
                    $value = switch ($fault) { drift { '{"foreign":true}' }; malformed { '[]' }; oversize { '{}' + (' ' * 65535) } }
                    [IO.File]::WriteAllText($paths.$kind, $value, [Stratus.ParentStage.Files]::Utf8)
                    $failure = $null
                    try { [void](New-ParentBootstrapFrame ('a' * 64)) } catch { $failure = $_.Exception.GetBaseException().Message }
                    $expected = switch ($fault) { drift { '*own published bytes*' }; malformed { '*JSON object*' }; oversize { '*byte bound*' } }
                    Assert-Primitive ($null -ne $failure -and $failure -like $expected) 'Receipt fault did not fail at its required bounded boundary.'
                    Assert-Primitive (-not $script:bootstrapWriteAttempted) 'Readback failure attempted stdout publication.'
                }
            }
        }
    }
    Invoke-PrimitiveCase 'bootstrap ready acknowledgement retains only its own exact publication hash' {
        Invoke-BootstrapFixture {
            $paths.ready = Join-Path $fixture 'new-ready.json'
            $observed.Clear()
            $document = [pscustomobject]@{ path = (Join-Path $fixture 'parent-armed.json'); sha256 = ('a' * 64) }
            Write-ParentAck ready $document
            Assert-Primitive ($observed.ready.sha256 -ceq [Stratus.ParentStage.Files]::Hash([IO.File]::ReadAllBytes($paths.ready))) 'Acknowledgement did not retain its exact publication hash.'
            $paths.ready = Join-Path $fixture 'foreign-readback-ready.json'
            $observed.Clear()
            function Read-ParentDocument { param([string] $Path) return [pscustomobject]@{ path = $Path; sha256 = ('0' * 64); value = $null } }
            Assert-Rejected { Write-ParentAck ready $document } 'Acknowledgement adopted a foreign readback hash.'
            Assert-Primitive (-not $observed.ContainsKey('ready')) 'Failed acknowledgement became trusted bootstrap evidence.'
        }
    }
    Invoke-PrimitiveCase 'bootstrap write and flush failures latch one attempt and retain open output' {
        Add-Type -TypeDefinition @'
using System;
using System.IO;
namespace Stratus.ParentStage.Tests {
    public sealed class BootstrapFaultStream : MemoryStream {
        public bool FailWrite, FailFlush, Closed;
        public int Writes, Flushes;
        public override void Write(byte[] bytes,int offset,int count) { Writes++; if(FailWrite) throw new IOException("injected write"); base.Write(bytes,offset,count); }
        public override void Flush() { Flushes++; if(FailFlush) throw new IOException("injected flush"); base.Flush(); }
        protected override void Dispose(bool disposing) { Closed=true; base.Dispose(disposing); }
    }
}
'@
        # Test-only stream substitution in the extracted helper; production has no injectable stdio parameter.
        $definition = @($ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq 'Write-ParentBootstrapReady' }, $false))[0].Extent.Text
        $needle = '[Console]::OpenStandardOutput()'
        Assert-Primitive (($definition.Split(@($needle), [StringSplitOptions]::None)).Count -eq 2) 'Fixed production stdout opener is ambiguous.'
        . ([scriptblock]::Create($definition.Replace($needle, '$faultStream')))
        foreach ($fault in @('write', 'flush')) {
            $faultStream = [Stratus.ParentStage.Tests.BootstrapFaultStream]::new()
            try {
                $faultStream.FailWrite = $fault -ceq 'write'; $faultStream.FailFlush = $fault -ceq 'flush'
                $script:bootstrapWriteAttempted = $false
                Assert-Rejected { Write-ParentBootstrapReady ([byte[]]@(123,125,10)) } 'Output fault was swallowed.'
                Assert-Rejected { Write-ParentBootstrapReady ([byte[]]@(123,125,10)) } 'Output fault permitted a second attempt.'
                Assert-Primitive ($script:bootstrapWriteAttempted -and $faultStream.Writes -eq 1 -and
                    -not $faultStream.Closed -and [object]::ReferenceEquals($script:bootstrapOutput, $faultStream)) 'Failed stdout write was retried, closed or lost lifetime ownership.'
            }
            finally { $faultStream.Dispose() }
        }
    }
    Invoke-PrimitiveCase 'recovery receipts survive failed readback without adopting or rewriting foreign evidence' {
        & {
            $script:recoveryDocuments = @{}
            $path = Join-Path $workspace 'recovery-receipt.json'
            $originalReader = ${function:Read-ParentDocument}
            $readState = @{ fail = $true }
            function Read-ParentDocument {
                param([string] $Path)
                if ($readState.fail) { $readState.fail = $false; throw 'Injected readback failure after publication.' }
                return & $originalReader $Path
            }
            Assert-Rejected {
                [void](Write-ParentRecoveryDocument 'contained' $path ([ordered]@{ sequence = 1 }))
            } 'Injected receipt readback failure was not retained.'
            $before = [IO.File]::ReadAllBytes($path)
            $hash = Write-ParentRecoveryDocument 'contained' $path ([ordered]@{ sequence = 2 })
            Assert-Primitive ($hash -ceq [Stratus.ParentStage.Files]::Hash($before)) 'Retry changed owned receipt hash.'
            Assert-Primitive ([IO.File]::ReadAllText($path) -ceq '{"sequence":1}') 'Retry rewrote published receipt.'
            Assert-Rejected {
                [void](Write-ParentRecoveryDocument 'foreign' $path ([ordered]@{ sequence = 1 }))
            } 'Preexisting identical evidence was adopted without ownership.'
            [Stratus.ParentStage.Files]::ReplacePublished($path,
                [Stratus.ParentStage.Files]::Utf8.GetBytes('{"sequence":3}'))
            Assert-Rejected {
                [void](Write-ParentRecoveryDocument 'contained' $path ([ordered]@{ sequence = 1 }))
            } 'Tampered recovery receipt was accepted.'
            Remove-Item -LiteralPath $path -Force -ErrorAction Stop
            Assert-Rejected {
                [void](Write-ParentRecoveryDocument 'contained' $path ([ordered]@{ sequence = 1 }))
            } 'Deleted published receipt was silently republished.'
            Assert-Primitive (-not (Test-Path -LiteralPath $path)) 'Deleted receipt was recreated.'
            $failedPath = Join-Path $workspace 'failed-publication-receipt.json'
            # A non-object value reaches native PublishNew but fails its strict JSON
            # boundary after the plan is cached and before the target is published.
            Assert-Rejected {
                [void](Write-ParentRecoveryDocument 'planned-only' $failedPath 'not-an-object')
            } 'Injected native pre-publication rejection did not occur.'
            Assert-Primitive (-not $script:recoveryDocuments['planned-only'].published) 'Failed native publication was marked successful.'
            [IO.File]::WriteAllBytes($failedPath, $script:recoveryDocuments['planned-only'].bytes)
            $ownershipFailure = ''
            try { [void](Write-ParentRecoveryDocument 'planned-only' $failedPath 'not-an-object') }
            catch { $ownershipFailure = $_.Exception.Message }
            Assert-Primitive ($ownershipFailure -like '*foreign target occupies an unpublished*') 'Planned-only retry adopted a byte-identical foreign target.'
        }
    }
    Invoke-PrimitiveCase 'pending or failing provision cannot starve exact API and PostgreSQL containment' {
        & {
            $paths = @{}
            foreach ($name in @('provision_attempt', 'provision_complete', 'api_attempt', 'api_start_attempt',
                'api_start_complete', 'transition_start_attempt', 'transition_start_completion')) {
                $paths[$name] = Join-Path $workspace ('round-' + $name + '.json')
            }
            [Stratus.ParentStage.Files]::PublishNew($paths.provision_attempt,
                [Stratus.ParentStage.Files]::Utf8.GetBytes('{}'))
            foreach ($mode in @('pending', 'error')) {
                $failures = [Collections.Generic.List[string]]::new()
                $calls = [Collections.Generic.List[string]]::new()
                $fixture = @{ mode = $mode; provision = $false; api = $true; postgres = $true }
                $ids = @{ provision = ('1' * 64); api = ('2' * 64); postgres = ('3' * 64) }
                function Assert-ParentEngine { }
                function Get-ParentContainmentContainer {
                    param([string] $Kind, [switch] $AllowAbsent)
                    $calls.Add('inspect:' + $Kind)
                    if ($Kind -ceq 'provision' -and $fixture.mode -ceq 'pending') { return $null }
                    if ($Kind -ceq 'provision' -and $fixture.mode -ceq 'error') { throw 'Injected provision probe failure.' }
                    return [pscustomobject]@{
                        Id = $ids[$Kind]; HostConfig = [pscustomobject]@{ RestartPolicy = [pscustomobject]@{ Name = 'no' } }
                        State = [pscustomobject]@{ Running = $fixture[$Kind]; Pid = $(if ($fixture[$Kind]) { 10 } else { 0 }); Status = $(if ($fixture[$Kind]) { 'running' } else { 'exited' }) }
                    }
                }
                function Invoke-ParentDocker {
                    param([string[]] $Arguments, [int] $TimeoutMilliseconds)
                    Assert-Primitive ($Arguments[0] -ceq 'stop' -and $TimeoutMilliseconds -eq 25000) 'Unbounded/foreign mutation attempted.'
                    $kind = @($ids.Keys | Where-Object { $ids[$_] -ceq $Arguments[-1] })[0]
                    $calls.Add('stop:' + $kind); $fixture[$kind] = $false
                    return $Arguments[-1]
                }
                $progress = New-ParentContainmentProgress
                Assert-Primitive (-not (Invoke-ParentContainerContainmentRound $progress)) 'Unresolved provision counted as contained.'
                Assert-Primitive (($calls | Where-Object { $_.StartsWith('stop:') }) -join ',' -ceq 'stop:api,stop:postgres') 'Provision starved later containment slots.'
                Assert-Primitive ($progress.api.stopped -and $progress.postgres.stopped -and -not $progress.provision.stopped) 'Round lost independent object outcomes.'
                $fixture.mode = 'ready'; $fixture.provision = $true
                Assert-Primitive (Invoke-ParentContainerContainmentRound $progress) 'Delayed provision failed to finish in a later round.'
                Assert-Primitive ($progress.provision.materialized -and $progress.provision.result -ceq 'causal_exact_stop') 'Late start lacks causally later exact stop.'
                Assert-Primitive (Invoke-ParentContainerContainmentRound $progress) 'Receipt retry lost prior materialization history.'
            }
        }
    }
    Invoke-PrimitiveCase 'live or unproved owner and observer prevent contained terminal without starving container attempts' {
        & {
            $failures = [Collections.Generic.List[string]]::new()
            $fixture = @{ owner_alive = $true; observer_alive = $true; rounds = 0 }
            $paths = @{
                mutation_fence = (Join-Path $workspace 'process-containment-fence.json')
                transition_start_fence = (Join-Path $workspace 'absent-transition-fence.json')
                transition_start_revoke = (Join-Path $workspace 'process-containment-revoke.json')
            }
            [Stratus.ParentStage.Files]::PublishNew($paths.mutation_fence, [Stratus.ParentStage.Files]::Utf8.GetBytes('{}'))
            [Stratus.ParentStage.Files]::PublishNew($paths.transition_start_revoke, [Stratus.ParentStage.Files]::Utf8.GetBytes('{}'))
            $fenceHash = [Stratus.ParentStage.Files]::Hash([Stratus.ParentStage.Files]::Utf8.GetBytes('{}'))
            $script:fenceLock = [IO.FileStream]::new($paths.mutation_fence, 'Open', 'Read', 'None')
            $script:ownerLease = [pscustomobject]@{ kind = 'owner'; alive = $true }
            $script:ownerLease | Add-Member ScriptMethod IsAlive { return $this.alive }
            $script:observerLease = [pscustomobject]@{ kind = 'observer'; alive = $true }
            $script:observerLease | Add-Member ScriptMethod IsAlive { return $this.alive }
            $script:observer = [pscustomobject]@{ pid = 1 }
            $script:containmentProgress = $null
            function Stop-ParentExactProcess {
                param([object] $Lease)
                if ($Lease.IsAlive()) { throw ('Injected ' + $Lease.kind + ' termination failure.') }
            }
            function Invoke-ParentContainerContainmentRound {
                param([Collections.IDictionary] $Progress)
                $fixture.rounds += 1
                return $true
            }
            try {
                Assert-Primitive ($null -eq (Invoke-ParentContainment 'fixture')) 'Unproved owner produced a containment receipt.'
                Assert-Primitive ($fixture.rounds -eq 0) 'Container containment preceded owner revocation.'
                $script:ownerLease.alive = $false
                Assert-Primitive ($null -eq (Invoke-ParentContainment 'fixture')) 'Live observer produced a containment receipt.'
                Assert-Primitive ($fixture.rounds -eq 1) 'Observer failure starved exact container attempts.'
            }
            finally { $script:fenceLock.Dispose(); $script:fenceLock = $null }
        }
    }
    Invoke-PrimitiveCase 'registered restarted or OOM API is stopped while unresolved provision stays nonterminal' {
        & {
            $failures = [Collections.Generic.List[string]]::new()
            $objectIds = @{ provision = $null; api = ('4' * 64) }
            $approvedPostgresId = '5' * 64
            $objectStarts = @{ provision = $null; api = 'before'; postgres = 'same' }
            $contract = [pscustomobject]@{
                api = [pscustomobject]@{ name = 'weatherpoly-api-1'; image_id = ('sha256:' + ('6' * 64)); config_image = 'candidate'; service = 'api' }
                postgres = [pscustomobject]@{ name = 'weatherpoly-postgres-1'; image_id = ('sha256:' + ('7' * 64)); config_image = 'postgres'; service = 'postgres' }
            }
            $paths = @{}
            foreach ($name in @('provision_attempt', 'provision_complete', 'api_attempt', 'api_start_attempt',
                'api_start_complete', 'transition_start_attempt', 'transition_start_completion')) {
                $paths[$name] = Join-Path $workspace ('runtime-drift-' + $name + '.json')
            }
            [Stratus.ParentStage.Files]::PublishNew($paths.provision_attempt, [Stratus.ParentStage.Files]::Utf8.GetBytes('{}'))
            $fixture = @{ api = $true; postgres = $true; foreign = $false }
            $stops = [Collections.Generic.List[string]]::new()
            function Assert-ParentEngine { }
            function Get-ParentContainer {
                param([string] $Kind, [switch] $AllowAbsent)
                if ($Kind -cne 'provision' -or -not $AllowAbsent) { throw 'Strict acceptance getter reached for an owned runtime drift.' }
                return $null
            }
            function Invoke-ParentDocker {
                param([string[]] $Arguments, [int] $TimeoutMilliseconds, [switch] $AllowAbsent)
                $kind = if ($Arguments[-1] -ceq $objectIds.api) { 'api' } elseif ($Arguments[-1] -ceq $approvedPostgresId) { 'postgres' } else { throw 'Foreign stop/inspect target.' }
                if ($Arguments[0] -ceq 'stop') { $stops.Add($Arguments[-1]); $fixture[$kind] = $false; return $Arguments[-1] }
                if ($Arguments[0] -cne 'inspect') { throw 'Unexpected containment command.' }
                $definition = $contract.$kind
                return ([ordered]@{
                    Id = $(if ($fixture.foreign) { '8' * 64 } else { $Arguments[-1] }); Name = ('/' + $definition.name)
                    Image = $definition.image_id; RestartCount = $(if ($kind -ceq 'api') { 1 } else { 0 })
                    Config = @{ Image = $definition.config_image; Labels = @{ 'com.docker.compose.project' = 'weatherpoly'; 'com.docker.compose.service' = $kind } }
                    HostConfig = @{ RestartPolicy = @{ Name = 'unless-stopped' } }
                    State = @{ Running = $fixture[$kind]; Pid = $(if ($fixture[$kind]) { 12 } else { 0 }); OOMKilled = ($kind -ceq 'api'); Status = $(if ($fixture[$kind]) { 'running' } else { 'exited' })
                        Paused = $false; Restarting = $false; Dead = $false; Error = ''; StartedAt = $(if ($kind -ceq 'api') { 'after' } else { 'same' }) }
                } | ConvertTo-Json -Depth 8 -Compress)
            }
            $progress = New-ParentContainmentProgress
            Assert-Primitive (-not (Invoke-ParentContainerContainmentRound $progress)) 'Pending provision became terminal.'
            Assert-Primitive ($stops.Contains($objectIds.api) -and $stops.Contains($approvedPostgresId)) 'Runtime drift vetoed exact owned stop.'
            Assert-Primitive ($failures.Contains('api registered runtime drift observed during exact containment.')) 'Runtime drift vanished from failure evidence.'
            $fixture.foreign = $true
            Assert-Rejected { [void](Get-ParentContainmentContainer api) } 'Foreign full ID gained containment authority.'
        }
    }
    Invoke-PrimitiveCase 'zero-byte or foreign completion never resolves an unacknowledged daemon start' {
        & {
            foreach ($mode in @('empty', 'foreign')) {
                $script:validatedCompletions = @{}
                $failures = [Collections.Generic.List[string]]::new()
                $paths = @{}
                foreach ($name in @('provision_attempt', 'provision_complete', 'api_attempt', 'api_start_attempt',
                    'api_start_complete', 'transition_start_attempt', 'transition_start_completion')) {
                    $paths[$name] = Join-Path $workspace ('incomplete-' + $mode + '-' + $name + '.json')
                }
                [Stratus.ParentStage.Files]::PublishNew($paths.provision_attempt, [Stratus.ParentStage.Files]::Utf8.GetBytes('{}'))
                [byte[]]$completionBytes = @()
                if ($mode -cne 'empty') { $completionBytes = [Stratus.ParentStage.Files]::Utf8.GetBytes('{"exit_code":0,"session_id":"foreign"}') }
                [IO.File]::WriteAllBytes($paths.provision_complete, $completionBytes)
                function Assert-ParentEngine { }
                function Get-ParentContainmentContainer {
                    param([string] $Kind, [switch] $AllowAbsent)
                    return [pscustomobject]@{ Id = ('9' * 64); State = [pscustomobject]@{ Running = $false; Pid = 0; Status = 'exited' } }
                }
                $progress = New-ParentContainmentProgress
                Assert-Primitive (-not (Invoke-ParentContainerContainmentRound $progress)) 'Unvalidated completion permitted terminal containment.'
                Assert-Primitive ($progress.provision.attempt_seen -and $progress.provision.start_seen -and -not $progress.provision.stopped) 'Ambiguous start lost its durable unresolved state.'
                Remove-Item -LiteralPath $paths.provision_attempt,$paths.provision_complete -Force -ErrorAction Stop
                Assert-Primitive (-not (Invoke-ParentContainerContainmentRound $progress)) 'Deleted attempt/completion downgraded ambiguity to never attempted.'
            }
        }
    }
    Invoke-PrimitiveCase 'API start marker without create marker cannot be downgraded to never attempted' {
        & {
            $script:validatedCompletions = @{}
            $failures = [Collections.Generic.List[string]]::new()
            $paths = @{}
            foreach ($name in @('provision_attempt', 'provision_complete', 'api_attempt', 'api_start_attempt',
                'api_start_complete', 'transition_start_attempt', 'transition_start_completion')) {
                $paths[$name] = Join-Path $workspace ('orphan-start-' + $name + '.json')
            }
            [Stratus.ParentStage.Files]::PublishNew($paths.api_start_attempt, [Stratus.ParentStage.Files]::Utf8.GetBytes('{}'))
            function Assert-ParentEngine { }
            function Get-ParentContainmentContainer {
                param([string] $Kind, [switch] $AllowAbsent)
                if ($Kind -cne 'postgres') { return $null }
                return [pscustomobject]@{ Id = ('9' * 64); State = [pscustomobject]@{ Running = $false; Pid = 0; Status = 'exited' } }
            }
            $progress = New-ParentContainmentProgress
            Assert-Primitive (-not (Invoke-ParentContainerContainmentRound $progress)) 'Orphan start marker with absent API produced terminal containment.'
            Assert-Primitive ($progress.api.attempt_seen -and $progress.api.start_seen -and -not $progress.api.stopped) 'API start evidence failed to latch attempted state.'
        }
    }
    Invoke-PrimitiveCase 'contradictory stopped snapshots cannot prove exact containment' {
        & {
            $paths = @{}
            foreach ($name in @('provision_attempt', 'provision_complete', 'api_attempt', 'api_start_attempt',
                'api_start_complete', 'transition_start_attempt', 'transition_start_completion')) {
                $paths[$name] = Join-Path $workspace ('contradictory-' + $name + '.json')
            }
            foreach ($mode in @('pid', 'status')) {
                $failures = [Collections.Generic.List[string]]::new()
                $calls = [Collections.Generic.List[string]]::new()
                function Assert-ParentEngine { }
                function Get-ParentContainmentContainer {
                    param([string] $Kind, [switch] $AllowAbsent)
                    $bad = $Kind -ceq 'api'
                    return [pscustomobject]@{ Id = ($Kind.Substring(0, 1) * 64); State = [pscustomobject]@{
                        Running = $false; Pid = $(if ($bad -and $mode -ceq 'pid') { 42 } else { 0 })
                        Status = $(if ($bad -and $mode -ceq 'status') { 'restarting' } else { 'exited' })
                    } }
                }
                function Invoke-ParentDocker {
                    param([string[]] $Arguments, [int] $TimeoutMilliseconds)
                    $calls.Add($Arguments[-1]); return $Arguments[-1]
                }
                $progress = New-ParentContainmentProgress
                Assert-Primitive (-not (Invoke-ParentContainerContainmentRound $progress)) 'Contradictory stopped snapshot became contained.'
                Assert-Primitive (-not $progress.api.stopped -and $progress.api.materialized) 'Contradictory API state lost unresolved materialization.'
            }
        }
    }
    Invoke-PrimitiveCase 'intended container policy binds environment secret and image-declared anonymous volume' {
        & {
            $image = [pscustomobject]@{ Env = @('BASE=value'); Cmd = @('postgres'); Entrypoint = @('entry'); User = ''; WorkingDir = ''
                Volumes = [pscustomobject]@{ '/var/lib/postgresql/data' = [pscustomobject]@{} } }
            $service = [pscustomobject]@{ environment = [pscustomobject]@{ MODE = 'PAPER' }; command = @('postgres'); entrypoint = @('entry') }
            $script:intendedServices = @{ provision = [pscustomobject]@{ service = $service; image = $image; secret_source = 'C:\fixture\runtime-password' } }
            $volumeName = 'a' * 64
            $inspect = [pscustomobject]@{
                Id = ('b' * 64)
                Config = [pscustomobject]@{ Env = @('BASE=value', 'MODE=PAPER'); Cmd = @('postgres'); Entrypoint = @('entry'); User = ''; WorkingDir = ''; Hostname = ('b' * 12); Domainname = '' }
                HostConfig = [pscustomobject]@{ Privileged = $false; AutoRemove = $false; NetworkMode = 'weatherpoly_default'; PidMode = ''; UTSMode = ''; CapAdd = $null; Devices = $null; DeviceRequests = $null; VolumesFrom = $null; SecurityOpt = $null; Memory = 0; PortBindings = [pscustomobject]@{} }
                Mounts = @(
                    [pscustomobject]@{ Type = 'bind'; RW = $false; Destination = '/run/secrets/stratus_runtime_password'; Source = '/run/desktop/mnt/host/c/fixture/runtime-password' },
                    [pscustomobject]@{ Type = 'volume'; Driver = 'local'; Name = $volumeName; Source = ('/var/lib/docker/volumes/' + $volumeName + '/_data'); Destination = '/var/lib/postgresql/data'; RW = $true; Propagation = '' }
                )
            }
            Assert-ParentIntendedContainer provision $inspect
            $inspect.Config.Env = @('BASE=value', 'MODE=LIVE')
            Assert-Rejected { Assert-ParentIntendedContainer provision $inspect } 'Unintended runtime environment was accepted.'
            $inspect.Config.Env = @('BASE=value', 'MODE=PAPER')
            $inspect.Mounts[0].RW = $true
            Assert-Rejected { Assert-ParentIntendedContainer provision $inspect } 'Writable runtime secret was accepted.'
            $inspect.Mounts[0].RW = $false
            $inspect.Mounts[1].Source = '/var/lib/docker/volumes/foreign/_data'
            Assert-Rejected { Assert-ParentIntendedContainer provision $inspect } 'Foreign volume source was accepted.'
            $inspect.Mounts[1].Source = '/var/lib/docker/volumes/' + $volumeName + '/_data'
            $inspect.Mounts += [pscustomobject]@{ Type = 'bind'; Destination = '/extra'; Source = 'C:/foreign'; RW = $true }
            Assert-Rejected { Assert-ParentIntendedContainer provision $inspect } 'Undeclared writable mount was accepted.'
        }
    }
    Invoke-PrimitiveCase 'R1 manifests and publication timestamps require canonical native encoding' {
        $valid = "a.json`t12`t$('a' * 64)`nb.json`t0`t$('b' * 64)`n"
        $manifest = Read-ParentCanonicalManifest ([Stratus.ParentStage.Files]::Utf8.GetBytes($valid))
        Assert-Primitive ($manifest.Count -eq 2 -and $manifest['b.json'].bytes -eq 0) 'Canonical R1 manifest rejected.'
        foreach ($invalid in @($valid.Replace("`n", "`r`n"), $valid.TrimEnd("`n"),
            "A.json`t1`t$('a' * 64)`na.json`t1`t$('b' * 64)`n", "../x`t1`t$('a' * 64)`n")) {
            Assert-Rejected { [void](Read-ParentCanonicalManifest ([Stratus.ParentStage.Files]::Utf8.GetBytes($invalid))) } 'Noncanonical R1 manifest accepted.'
        }
        Assert-Primitive ((Get-ParentR1Utc '2026-09-13T20:17:02.3150000Z').Offset -eq [TimeSpan]::Zero) 'R1 canonical Z timestamp rejected.'
        Assert-Rejected { [void](Get-ParentR1Utc '2026-09-13T20:17:02.3150000+00:00') } 'R1 alternate timestamp spelling accepted.'
    }
    Invoke-PrimitiveCase 'noncanonical and reparse ancestry are rejected before publication' {
        $noncanonical = Join-Path $workspace '..\must-not-publish.json'
        Assert-Rejected { [Stratus.ParentStage.Files]::RegularAncestry($noncanonical, $false) } 'Noncanonical path accepted.'
        $target = Join-Path $workspace 'junction-target'
        $link = Join-Path $workspace 'junction-link'
        [void][IO.Directory]::CreateDirectory($target)
        [void](New-Item -ItemType Junction -Path $link -Target $target -ErrorAction Stop)
        try {
            $linkedPath = Join-Path $link 'never-created.json'
            Assert-Rejected {
                [Stratus.ParentStage.Files]::PublishNew($linkedPath, [Text.UTF8Encoding]::new($false).GetBytes('{}'))
            } 'Artifact publication followed reparse ancestry.'
            Assert-Primitive (-not (Test-Path -LiteralPath (Join-Path $target 'never-created.json'))) 'Reparse publication had a side effect.'
        }
        finally { Remove-Item -LiteralPath $link -Force -ErrorAction Stop }
    }
    Invoke-PrimitiveCase 'owner-process heartbeat is monotonic and disposal stops publication' {
        $path = Join-Path $workspace 'heartbeat.json'
        $publisher = [Stratus.ParentStage.Heartbeat]::new($path, ('a' * 32), ('b' * 64), 100)
        try {
            $first = [Stratus.ParentStage.Files]::Utf8.GetString(
                [Stratus.ParentStage.Files]::ReadBounded($path, 8192, $true)
            ) | ConvertFrom-Json
            Start-Sleep -Milliseconds 300
            $publisher.AssertHealthy()
            $second = [Stratus.ParentStage.Files]::Utf8.GetString(
                [Stratus.ParentStage.Files]::ReadBounded($path, 8192, $true)
            ) | ConvertFrom-Json
            Assert-Primitive ($first.owner_pid -eq $PID -and $second.sequence -gt $first.sequence) 'Heartbeat is not owner-bound/monotonic.'
        }
        finally { $publisher.Dispose() }
        $finalHash = [Stratus.ParentStage.Files]::Hash([IO.File]::ReadAllBytes($path))
        Start-Sleep -Milliseconds 250
        Assert-Primitive ($finalHash -ceq [Stratus.ParentStage.Files]::Hash([IO.File]::ReadAllBytes($path))) 'Disposed timer kept publishing.'
        Assert-Rejected { $publisher.AssertHealthy() } 'Disposed heartbeat appeared healthy.'
    }
    Invoke-PrimitiveCase 'heartbeat publication fault is retained and surfaced on dispose' {
        $path = Join-Path $workspace 'heartbeat-fault.json'
        $publisher = [Stratus.ParentStage.Heartbeat]::new($path, ('c' * 32), ('d' * 64), 100)
        $held = [IO.FileStream]::new($path, 'Open', 'Read', 'Read')
        try {
            Start-Sleep -Milliseconds 300
            Assert-Rejected { $publisher.AssertHealthy() } 'Heartbeat suppressed replacement failure.'
            Assert-Rejected { $publisher.Dispose() } 'Heartbeat disposal suppressed publication failure.'
        }
        finally { $held.Dispose(); $publisher.Dispose() }
    }

    # A standalone fake child is compiled locally. It never delegates to Docker,
    # reads credentials, connects to a service, or starts an application container.
    $fakeSource = @'
using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Collections.Generic;
using System.Text;
using System.Text.RegularExpressions;
using Stratus.ParentStage;
public static class ParentPrimitiveChild {
  [System.Runtime.InteropServices.DllImport("kernel32.dll",CharSet=System.Runtime.InteropServices.CharSet.Unicode,SetLastError=true)]
  static extern uint GetFinalPathNameByHandle(IntPtr handle,StringBuilder path,uint characters,uint flags);
  [System.Runtime.InteropServices.DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr OpenThread(uint access,bool inherit,uint id);
  [System.Runtime.InteropServices.DllImport("kernel32.dll",SetLastError=true)] static extern uint ResumeThread(IntPtr thread);
  [System.Runtime.InteropServices.DllImport("kernel32.dll",SetLastError=true)] static extern bool CloseHandle(IntPtr handle);
  static string Digest(string path) { return Files.Hash(File.ReadAllBytes(path)); }
  static Dictionary<string,string> CleanEnvironment(string directory) {
    Dictionary<string,string> env = new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase);
    foreach(string key in new string[] { "SystemRoot", "WINDIR", "PATH", "USERPROFILE", "LOCALAPPDATA" }) {
      string value=Environment.GetEnvironmentVariable(key); if(value!=null) env.Add(key,value);
    }
    env["TEMP"]=directory; env["TMP"]=directory; return env;
  }
  static IndependentWatchdogLaunch Launch(string source, int timeout) {
    string directory=Path.GetDirectoryName(source), contract=source+".contract.json";
    string runtime=File.Exists(source+".runtime.cs") ? source+".runtime.cs" : Path.Combine(directory,"Stratus.ParentStage.Runtime.cs");
    string session=Regex.Match(File.ReadAllText(contract),"\"session_id\":\"([0-9a-f]{32})\"").Groups[1].Value;
    string host=File.Exists(source+".host.exe") ? source+".host.exe" : Process.GetCurrentProcess().MainModule.FileName;
    return IndependentWatchdogLaunch.StartSuspended(host,Digest(host),source,Digest(source),runtime,Digest(runtime),
      contract,Digest(contract),session,source+".lease.json",source+".ready.json",directory,CleanEnvironment(directory),timeout,4096);
  }
  static int Watchdog(string[] args) {
    if(args.Length!=13 || args[1]!="-NoProfile" || args[2]!="-NonInteractive" || args[3]!="-File" ||
      args[5]!="-ContractPath" || args[7]!="-ContractSha256" || args[9]!="-RuntimeSha256" || args[11]!="-BootstrapNonce") return 91;
    string source=args[4], mode=File.ReadAllText(source).Trim();
    string contractText=File.ReadAllText(args[6]);
    string session=Regex.Match(contractText,"\"session_id\":\"([0-9a-f]{32})\"").Groups[1].Value;
    bool unrelatedMatched=false;
    Match probe=Regex.Match(contractText,"\"probe_handle\":([0-9]+)");
    if(probe.Success) {
      StringBuilder target=new StringBuilder(32768);
      uint length=GetFinalPathNameByHandle(new IntPtr(Int64.Parse(probe.Groups[1].Value)),target,(uint)target.Capacity,0);
      unrelatedMatched=length>0 && target.ToString().EndsWith("\\unrelated-inheritance-proof.bin",StringComparison.OrdinalIgnoreCase);
    }
    if(mode=="missing-ready" || mode=="overflow" || mode=="stderr") {
      if(mode=="overflow") { Console.Write(new String('x',8192)); Console.Out.Flush(); }
      if(mode=="stderr") { Console.Error.Write("fixture failure"); Console.Error.Flush(); }
      Thread.Sleep(700);
      Console.Write(new String('d',131072)); Console.Out.Flush();
      Console.Error.Write(new String('e',131072)); Console.Error.Flush();
      File.WriteAllText(source+".drained", "bounded-drain-completed");
      Thread.Sleep(2000); return 0;
    }
    IndependentWatchdogLaunch child=null;
    if(mode=="owner" || mode=="owner-suspended") {
      child=Launch(source+".child.ps1",2500);
      if(mode=="owner") {
        child.Resume(); Stopwatch readyClock=Stopwatch.StartNew();
        while(child.PollReady()==null && readyClock.ElapsedMilliseconds<2500) Thread.Sleep(10);
      }
    }
    string childFields=child==null ? "" : ",\"child_pid\":"+child.ProcessId+",\"child_start\":\""+child.StartedAt+"\"";
    string environmentFields=",\"unrelated_handle_matched\":"+(unrelatedMatched ? "true":"false")+
      ",\"ambient_leaked\":"+(Environment.GetEnvironmentVariable("STRATUS_PRIMITIVE_LEAK")==null ? "false":"true")+
      ",\"temp_base64\":\""+Convert.ToBase64String(Encoding.UTF8.GetBytes(Environment.GetEnvironmentVariable("TEMP") ?? ""))+"\"";
    Files.PublishNew(source+".lease.json",Files.Utf8.GetBytes("{\"session_id\":\""+session+"\"}"));
    Files.PublishNew(source+".ready.json",Files.Utf8.GetBytes("{\"fixture\":true"+childFields+environmentFields+"}"));
    string started=new DateTimeOffset(Process.GetCurrentProcess().StartTime.ToUniversalTime()).ToString("o");
    string frame="{\"format\":\"stratus-independent-watchdog-ready-v1\",\"session_id\":\""+session+
      "\",\"nonce\":\""+args[12]+"\",\"contract_sha256\":\""+args[8]+"\",\"source_sha256\":\""+Digest(source)+
      "\",\"runtime_sha256\":\""+args[10]+"\",\"host_sha256\":\""+Digest(Process.GetCurrentProcess().MainModule.FileName)+
      "\",\"pid\":\""+Process.GetCurrentProcess().Id+"\",\"start_time_utc\":\""+started+
      "\",\"lease_sha256\":\""+Digest(source+".lease.json")+"\",\"ready_sha256\":\""+Digest(source+".ready.json")+"\"}";
    if(mode=="wrong-frame") frame=frame.Replace("\"session_id\":\""+session,"\"session_id\":\""+new String('0',32));
    if(mode=="wrong-nonce") frame=frame.Replace(args[12],new String('0',64));
    if(mode=="wrong-contract") frame=frame.Replace(args[8],new String('0',64));
    if(mode=="wrong-pid") frame=frame.Replace("\"pid\":\""+Process.GetCurrentProcess().Id,"\"pid\":\"1");
    if(mode=="wrong-start") frame=frame.Replace(started,new DateTimeOffset(Process.GetCurrentProcess().StartTime.ToUniversalTime()).AddTicks(-1).ToString("o"));
    if(mode=="duplicate-field") frame=frame.Replace("{\"format\":","{\"session_id\":\""+session+"\",\"format\":");
    if(mode=="unknown-field") frame=frame.Substring(0,frame.Length-1)+",\"foreign\":false}";
    if(mode=="wrong-ready-hash") frame=frame.Replace(Digest(source+".ready.json"),new String('0',64));
    Console.Write(frame+"\n"); Console.Out.Flush();
    if(mode=="ready-then-extra") { Thread.Sleep(200); Console.Write("{}\n"); Console.Out.Flush(); }
    if(mode=="ready-then-exit") { Thread.Sleep(150); return 0; }
    Thread.Sleep(mode=="owner" || mode=="ready-long" ? 5000 : 3000);
    GC.KeepAlive(child); return 0;
  }
  public static int Main(string[] args) {
    if(args[0]=="-NoLogo") return Watchdog(args);
    string mode = args[0];
    if(mode=="resume-thread") {
      IntPtr thread=OpenThread(2,false,UInt32.Parse(args[1]));
      if(thread==IntPtr.Zero) return 93;
      try { uint previous=ResumeThread(thread); Console.Write(previous.ToString()); return previous==1 ? 0:94; }
      finally { CloseHandle(thread); }
    }
    if(mode=="no-breakaway") {
      IndependentWatchdogLaunch child=null;
      try { child=Launch(args[1],1000); child.AbortBeforeResume(); Console.Write("{\"rejected\":false}"); }
      catch { Console.Write("{\"rejected\":true}"); }
      finally { if(child!=null) child.Dispose(); }
      return 0;
    }
    if (mode == "success") { Console.Write("{\"ok\":true}"); return 0; }
    if (mode == "marker") { File.WriteAllText(args[1], Process.GetCurrentProcess().Id.ToString()); return 0; }
    if (mode == "arguments") { for (int i=1;i<args.Length;i++) Console.WriteLine(Convert.ToBase64String(System.Text.Encoding.UTF8.GetBytes(args[i]))); return 0; }
    if (mode == "stderr") { Console.Error.Write(new String('e', 32768)); Thread.Sleep(60000); return 0; }
    if (mode == "overflow") { Console.Write(new String('x', 32768)); Thread.Sleep(60000); return 0; }
    if (mode == "hang") { File.WriteAllText(args[1], Process.GetCurrentProcess().Id.ToString()); Thread.Sleep(60000); return 0; }
    if (mode == "brief") { Thread.Sleep(150); return 0; }
    if (mode == "brief-descendant") {
      ProcessStartInfo info = new ProcessStartInfo(Process.GetCurrentProcess().MainModule.FileName);
      info.Arguments = "brief"; info.UseShellExecute=false; info.CreateNoWindow=true;
      using(Process child=Process.Start(info)) { }
      return 0;
    }
    if (mode == "descendant") {
      ProcessStartInfo info = new ProcessStartInfo(Process.GetCurrentProcess().MainModule.FileName);
      info.Arguments = "hang \"" + args[1] + "\""; info.UseShellExecute=false; info.CreateNoWindow=true;
      using(Process child=Process.Start(info)) {
        Stopwatch clock=Stopwatch.StartNew(); while(!File.Exists(args[1]) && clock.ElapsedMilliseconds<5000) Thread.Sleep(10);
      }
      return 0;
    }
    return 13;
  }
}
'@
    $fakeSourcePath = Join-Path $workspace 'ParentPrimitiveChild.cs'
    [IO.File]::WriteAllText($fakeSourcePath, $fakeSource, [Text.UTF8Encoding]::new($false))
    $fakeExecutable = Join-Path $workspace 'ParentPrimitiveChild.exe'
    $fixtureRuntimePath = Join-Path $workspace 'Stratus.ParentStage.Runtime.cs'
    [IO.File]::WriteAllText($fixtureRuntimePath, $runtimeSource, [Text.UTF8Encoding]::new($false))
    $compiler = @(
        (Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'),
        (Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe')
    ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ([string]::IsNullOrEmpty($compiler)) { throw 'Windows .NET Framework compiler required for hermetic native tests.' }
    $compilerHash = [Stratus.ParentStage.Files]::Hash([IO.File]::ReadAllBytes($compiler))
    $compilerLock = [Stratus.ParentStage.Files]::Lock($compiler, $compilerHash)
    $compilerChild = $null
    try {
        $compilerChild = [Stratus.ParentStage.NativeChild]::StartSuspended($compiler, $compilerHash, $compilerLock,
            [string[]]@('/nologo', '/target:exe', ('/out:' + $fakeExecutable), $fakeSourcePath, $fixtureRuntimePath), $workspace, 65536)
        $compilerChild.Resume()
        $compileResult = $compilerChild.Wait(15000)
        if ($compileResult.ExitCode -ne 0) { throw ('Fake child compilation failed: ' + $compileResult.Stdout) }
    }
    finally {
        if ($null -ne $compilerChild) { $compilerChild.Dispose() }
        $compilerLock.Dispose()
    }
    $fakeHash = [Stratus.ParentStage.Files]::Hash([IO.File]::ReadAllBytes($fakeExecutable))
    $fakeLock = [Stratus.ParentStage.Files]::Lock($fakeExecutable, $fakeHash)
    $activeChildren.Add($fakeLock)

    function New-IndependentFixture {
        param([string] $Name, [string] $Mode = 'ready')
        $path = Join-Path $workspace ($Name + '.ps1')
        [IO.File]::WriteAllText($path, $Mode, [Text.UTF8Encoding]::new($false))
        $session = [Guid]::NewGuid().ToString('N')
        [Stratus.ParentStage.Files]::PublishNew(($path + '.contract.json'),
            [Stratus.ParentStage.Files]::Utf8.GetBytes('{"session_id":"' + $session + '"}'))
        return [pscustomobject]@{ source = $path; session = $session }
    }
    function New-IndependentEnvironment {
        $environment = [Collections.Generic.Dictionary[string,string]]::new([StringComparer]::OrdinalIgnoreCase)
        foreach ($key in @('SystemRoot', 'WINDIR', 'PATH', 'USERPROFILE', 'LOCALAPPDATA')) {
            $value = [Environment]::GetEnvironmentVariable($key)
            if ($null -ne $value) { $environment.Add($key, $value) }
        }
        $environment['TEMP'] = $workspace; $environment['TMP'] = $workspace
        return ,$environment
    }
    function Start-IndependentFixture {
        param([object] $Fixture, [int] $Timeout = 2000, [object] $Environment = (New-IndependentEnvironment), [string] $SourceHash = '')
        if ($SourceHash -ceq '') { $SourceHash = [Stratus.ParentStage.Files]::Hash([IO.File]::ReadAllBytes($Fixture.source)) }
        return [Stratus.ParentStage.IndependentWatchdogLaunch]::StartSuspended($fakeExecutable, $fakeHash,
            $Fixture.source, $SourceHash, $fixtureRuntimePath, [Stratus.ParentStage.Files]::Hash([IO.File]::ReadAllBytes($fixtureRuntimePath)),
            ($Fixture.source + '.contract.json'), [Stratus.ParentStage.Files]::Hash([IO.File]::ReadAllBytes($Fixture.source + '.contract.json')),
            $Fixture.session, ($Fixture.source + '.lease.json'), ($Fixture.source + '.ready.json'), $workspace, $Environment, $Timeout, 4096)
    }
    function Wait-IndependentReady {
        param([object] $Capability)
        $clock = [Diagnostics.Stopwatch]::StartNew()
        while ($clock.Elapsed.TotalSeconds -lt 5) {
            $ready = $Capability.PollReady()
            if ($null -ne $ready) { return $ready }
            Start-Sleep -Milliseconds 10
        }
        throw 'Fixture readiness wait exceeded its outer test bound.'
    }
    function Close-IndependentFixture {
        param([object] $Capability)
        if ($null -eq $Capability) { return }
        # Test-only cleanup: fixture executable + native creation time, never production.
        $lease = $null
        try {
            if ($Capability.IsAlive()) {
                $lease = [Stratus.ParentStage.ProcessLease]::Open($Capability.ProcessId, $Capability.StartedAt,
                    $fakeExecutable, $fakeHash, $fakeLock)
                $lease.TerminateAndWait(5000)
            }
        }
        finally {
            if ($null -ne $lease) { $lease.Dispose() }
            $Capability.Dispose()
        }
    }
    Invoke-PrimitiveCase 'independent watchdog remains suspended and can abort only before resume' {
        $fixture = New-IndependentFixture 'independent-suspended'
        $capability = Start-IndependentFixture $fixture
        try {
            $capability.AssertSuspended()
            Assert-Primitive (-not (Test-Path -LiteralPath ($fixture.source + '.lease.json'))) 'Independent child ran before resume.'
            $capability.AbortBeforeResume()
            Assert-Primitive (-not $capability.IsAlive()) 'Never-resumed independent child was not stopped.'
        }
        finally { Close-IndependentFixture $capability }
    }
    Invoke-PrimitiveCase 'independent startup deadline includes time retained before Resume' {
        $fixture = New-IndependentFixture 'independent-expired-suspended'
        $capability = Start-IndependentFixture $fixture -Timeout 100
        try {
            Start-Sleep -Milliseconds 150
            Assert-Rejected { $capability.Resume() } 'Suspended retention reset the constructor deadline.'
            Assert-Primitive ($capability.IsAlive() -and -not (Test-Path -LiteralPath ($fixture.source + '.lease.json'))) 'Expired suspended child executed.'
            $capability.AbortBeforeResume()
            Assert-Primitive (-not $capability.IsAlive()) 'Expired never-resumed child could not be safely aborted.'
        }
        finally { Close-IndependentFixture $capability }
    }
    Invoke-PrimitiveCase 'independent abort cannot kill a child resumed outside its wrapper' {
        $fixture = New-IndependentFixture 'independent-untracked-resume' 'ready-long'
        $capability = Start-IndependentFixture $fixture
        $process = $null
        try {
            $process = [Diagnostics.Process]::GetProcessById($capability.ProcessId)
            Assert-Primitive ($process.Threads.Count -eq 1) 'Suspended fixture thread inventory differs.'
            $result = Invoke-HermeticChild @('resume-thread', [string]$process.Threads[0].Id)
            Assert-Primitive ($result.ExitCode -eq 0 -and $result.Stdout -ceq '1') 'Test-only exact external resume failed.'
            Assert-Rejected { $capability.AbortBeforeResume() } 'Owner boolean permitted killing an externally resumed watchdog.'
            Assert-Rejected { $capability.Dispose() } 'Externally resumed watchdog could be disposed.'
            Assert-Rejected { [void]$capability.PollReady() } 'Unowned resume manufactured an accepted readiness capability.'
            Assert-Primitive ($capability.IsAlive()) 'Failed abort killed a possibly executing watchdog.'
        }
        finally { if ($null -ne $process) { $process.Dispose() }; Close-IndependentFixture $capability }
    }
    Invoke-PrimitiveCase 'independent readiness binds exact source frame files and explicit cloned environment' {
        $fixture = New-IndependentFixture 'independent-ready'
        $environment = New-IndependentEnvironment
        $oldAmbient = [Environment]::GetEnvironmentVariable('STRATUS_PRIMITIVE_LEAK')
        $capability = $null
        try {
            [Environment]::SetEnvironmentVariable('STRATUS_PRIMITIVE_LEAK', 'must-not-be-inherited')
            $capability = Start-IndependentFixture $fixture -Environment $environment
            $environment['TEMP'] = 'mutated-after-create'
            Assert-Rejected { [IO.File]::WriteAllText($fixture.source, 'changed') } 'Held watchdog source could be replaced.'
            $capability.Resume()
            Assert-Rejected { $capability.Resume() } 'Independent child resumed twice.'
            $ready = Wait-IndependentReady $capability
            Assert-Primitive ($ready.ProcessId -eq $capability.ProcessId -and $ready.SessionId -ceq $fixture.session) 'Ready capability lost native/session binding.'
            Assert-Primitive ([object]::ReferenceEquals($ready, $capability.PollReady())) 'Readiness capability was recreated.'
            $document = (Read-ParentDocument ($fixture.source + '.ready.json')).value
            Assert-Primitive (-not $document.ambient_leaked) 'Independent child inherited ambient environment.'
            Assert-Primitive ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($document.temp_base64)) -ceq $workspace) 'Caller mutation changed the constructed explicit environment.'
            Assert-Rejected { $capability.AbortBeforeResume() } 'Owner flag allowed termination after resume.'
            Assert-Rejected { $capability.Dispose() } 'Live resumed capability was disposed.'
            Assert-Primitive ($capability.IsAlive()) 'Rejected cleanup killed independent watchdog.'
        }
        finally { [Environment]::SetEnvironmentVariable('STRATUS_PRIMITIVE_LEAK', $oldAmbient); Close-IndependentFixture $capability }
    }
    Invoke-PrimitiveCase 'independent source and environment negatives reject before launch' {
        $fixture = New-IndependentFixture 'independent-input-negative'
        Assert-Rejected { [void](Start-IndependentFixture $fixture -SourceHash ('0' * 64)) } 'Wrong source hash was accepted.'
        $environment = New-IndependentEnvironment
        $environment['DOTNET_STARTUP_HOOKS'] = 'foreign.dll'
        Assert-Rejected { [void](Start-IndependentFixture $fixture -Environment $environment) } 'Code-injection environment variable accepted.'
        Assert-Primitive (-not (Test-Path -LiteralPath ($fixture.source + '.lease.json'))) 'Rejected launch executed helper.'
    }
    Invoke-PrimitiveCase 'independent invalid frame or file hash preserves resumed watchdog' {
        foreach ($mode in @('wrong-frame', 'wrong-ready-hash', 'wrong-nonce', 'wrong-contract', 'wrong-pid', 'wrong-start', 'duplicate-field', 'unknown-field')) {
            $fixture = New-IndependentFixture ('independent-' + $mode) $mode
            $capability = Start-IndependentFixture $fixture
            try {
                $capability.Resume()
                Assert-Rejected { [void](Wait-IndependentReady $capability) } 'Foreign startup readiness was accepted.'
                Assert-Primitive ($capability.IsAlive()) 'Invalid readiness killed possible containment watchdog.'
                Assert-Rejected { $capability.Dispose() } 'Invalid readiness permitted live disposal.'
            }
            finally { Close-IndependentFixture $capability }
        }
    }
    Invoke-PrimitiveCase 'independent fixed deadline and bounded stream faults never kill resumed watchdog' {
        foreach ($mode in @('missing-ready', 'overflow', 'stderr')) {
            $fixture = New-IndependentFixture ('independent-stream-' + $mode) $mode
            $capability = Start-IndependentFixture $fixture -Timeout 400
            try {
                $capability.Resume()
                $clock = [Diagnostics.Stopwatch]::StartNew()
                Assert-Rejected { [void](Wait-IndependentReady $capability) } 'Missing or invalid stream accepted.'
                Assert-Primitive ($clock.Elapsed.TotalSeconds -lt 2 -and $capability.IsAlive()) 'Deadline reset or stream fault killed independent child.'
                Assert-Rejected { [void]$capability.PollReady() } 'Latched startup failure disappeared.'
                Assert-Rejected { $capability.Dispose() } 'Stream fault permitted live disposal.'
                $drainClock = [Diagnostics.Stopwatch]::StartNew()
                while (-not (Test-Path -LiteralPath ($fixture.source + '.drained')) -and $drainClock.Elapsed.TotalSeconds -lt 2) {
                    Start-Sleep -Milliseconds 20
                }
                Assert-Primitive ((Test-Path -LiteralPath ($fixture.source + '.drained')) -and $capability.IsAlive()) 'Fault stopped pipe draining, closed writer handles or killed child.'
            }
            finally { Close-IndependentFixture $capability }
        }
    }
    Invoke-PrimitiveCase 'independent extra frame after readiness latches fault without killing watchdog' {
        $fixture = New-IndependentFixture 'independent-extra' 'ready-then-extra'
        $capability = Start-IndependentFixture $fixture
        try {
            $capability.Resume(); [void](Wait-IndependentReady $capability)
            Start-Sleep -Milliseconds 350
            Assert-Rejected { [void]$capability.PollReady() } 'Second startup frame was accepted.'
            Assert-Primitive ($capability.IsAlive()) 'Post-ready output fault killed independent child.'
        }
        finally { Close-IndependentFixture $capability }
    }
    Invoke-PrimitiveCase 'independent captured readiness never survives exact child exit' {
        $fixture = New-IndependentFixture 'independent-ready-exit' 'ready-then-exit'
        $capability = Start-IndependentFixture $fixture
        try {
            $capability.Resume(); [void](Wait-IndependentReady $capability)
            $clock = [Diagnostics.Stopwatch]::StartNew()
            while ($capability.IsAlive() -and $clock.Elapsed.TotalSeconds -lt 3) { Start-Sleep -Milliseconds 20 }
            Assert-Primitive (-not $capability.IsAlive()) 'Self-bounded helper did not exit.'
            Assert-Rejected { [void]$capability.PollReady() } 'Cached readiness was accepted after held process exit.'
        }
        finally { Close-IndependentFixture $capability }
    }
    Invoke-PrimitiveCase 'independent launch rejects an owner Job that forbids breakaway' {
        $fixture = New-IndependentFixture 'independent-no-breakaway'
        $result = Invoke-HermeticChild @('no-breakaway', $fixture.source)
        Assert-Primitive ($result.ExitCode -eq 0 -and $result.Stdout -ceq '{"rejected":true}') 'Restricted owner Job silently retained or allowed watchdog child.'
        Assert-Primitive (-not (Test-Path -LiteralPath ($fixture.source + '.lease.json'))) 'Rejected breakaway child ran.'
    }
    Invoke-PrimitiveCase 'independent watchdog and inherited source locks survive owner death' {
        $ownerFixture = New-IndependentFixture 'independent-owner' 'owner'
        $childSource = $ownerFixture.source + '.child.ps1'
        [IO.File]::WriteAllText($childSource, 'ready-long', [Text.UTF8Encoding]::new($false))
        [Stratus.ParentStage.Files]::PublishNew(($childSource + '.contract.json'),
            [Stratus.ParentStage.Files]::Utf8.GetBytes('{"session_id":"' + [Guid]::NewGuid().ToString('N') + '"}'))
        $ownerCapability = Start-IndependentFixture $ownerFixture
        $childLease = $null
        try {
            $ownerCapability.Resume(); [void](Wait-IndependentReady $ownerCapability)
            $ready = (Read-ParentDocument ($ownerFixture.source + '.ready.json')).value
            $childLease = [Stratus.ParentStage.ProcessLease]::Open([int]$ready.child_pid, $ready.child_start, $fakeExecutable, $fakeHash, $fakeLock)
            Close-IndependentFixture $ownerCapability; $ownerCapability = $null
            $childLease.AssertAlive()
            Start-Sleep -Milliseconds 300
            $childLease.AssertAlive()
            Assert-Rejected { [IO.File]::WriteAllText($childSource, 'replaced-after-owner-death') } 'Owner death released inherited child source lock.'
            Assert-Primitive ($childLease.IsAlive()) 'Owner death killed the containment watchdog.'
        }
        finally {
            if ($null -ne $childLease) { $childLease.TerminateAndWait(5000); $childLease.Dispose() }
            Close-IndependentFixture $ownerCapability
        }
    }
    Invoke-PrimitiveCase 'never-resumed independent child inherits unique script contract and runtime locks across owner death' {
        $ownerFixture = New-IndependentFixture 'independent-owner-suspended' 'owner-suspended'
        $childSource = $ownerFixture.source + '.child.ps1'
        $uniqueHost = $childSource + '.host.exe'; $uniqueRuntime = $childSource + '.runtime.cs'
        [IO.File]::WriteAllText($childSource, 'ready', [Text.UTF8Encoding]::new($false))
        [IO.File]::WriteAllBytes($uniqueHost, [IO.File]::ReadAllBytes($fakeExecutable))
        [IO.File]::WriteAllText($uniqueRuntime, $runtimeSource, [Text.UTF8Encoding]::new($false))
        [Stratus.ParentStage.Files]::PublishNew(($childSource + '.contract.json'),
            [Stratus.ParentStage.Files]::Utf8.GetBytes('{"session_id":"' + [Guid]::NewGuid().ToString('N') + '"}'))
        $ownerCapability = Start-IndependentFixture $ownerFixture
        $childLease = $null; $childHostLock = $null
        try {
            $ownerCapability.Resume(); [void](Wait-IndependentReady $ownerCapability)
            $ready = (Read-ParentDocument ($ownerFixture.source + '.ready.json')).value
            # Acquire cleanup ownership BEFORE any assertion that can fail. This
            # host lock deliberately remains held; therefore this case makes no
            # behavioural claim about inheritance of the executable lock itself.
            $childHostLock = [Stratus.ParentStage.Files]::Lock($uniqueHost, $fakeHash)
            $childLease = [Stratus.ParentStage.ProcessLease]::Open([int]$ready.child_pid, $ready.child_start, $uniqueHost, $fakeHash, $childHostLock)
            Close-IndependentFixture $ownerCapability; $ownerCapability = $null
            Assert-Primitive (-not (Test-Path -LiteralPath ($childSource + '.lease.json'))) 'Inner child executed before owner-death lock proof.'
            foreach ($path in @($childSource, ($childSource + '.contract.json'), $uniqueRuntime)) {
                Assert-Rejected { [IO.File]::WriteAllText($path, 'forbidden-replacement') } 'An inherited source lock disappeared with its owner.'
            }
            $childLease.AssertAlive()
        }
        finally {
            if ($null -ne $childLease) { $childLease.TerminateAndWait(5000); $childLease.Dispose() }
            if ($null -ne $childHostLock) { $childHostLock.Dispose() }
            Close-IndependentFixture $ownerCapability
        }
    }
    Invoke-PrimitiveCase 'independent explicit handle allowlist excludes unrelated inheritable files' {
        $fixture = New-IndependentFixture 'independent-handle-allowlist'
        $probePath = Join-Path $workspace 'unrelated-inheritance-proof.bin'
        $probe = [IO.FileStream]::new($probePath, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite,
            ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Inheritable))
        $capability = $null
        try {
            [Stratus.ParentStage.Files]::ReplacePublished(($fixture.source + '.contract.json'),
                [Stratus.ParentStage.Files]::Utf8.GetBytes('{"session_id":"' + $fixture.session + '","probe_handle":' +
                    $probe.SafeFileHandle.DangerousGetHandle().ToInt64().ToString([Globalization.CultureInfo]::InvariantCulture) + '}'))
            $capability = Start-IndependentFixture $fixture
            $capability.Resume(); [void](Wait-IndependentReady $capability)
            $ready = (Read-ParentDocument ($fixture.source + '.ready.json')).value
            Assert-Primitive (-not $ready.unrelated_handle_matched) 'Independent startup inherited a file outside its precise handle allowlist.'
        }
        finally { Close-IndependentFixture $capability; $probe.Dispose() }
    }

    Invoke-PrimitiveCase 'suspended child never runs before explicit resume and dispose kills it' {
        $marker = Join-Path $workspace 'suspended-marker.txt'
        $child = [Stratus.ParentStage.NativeChild]::StartSuspended($fakeExecutable, $fakeHash, $fakeLock,
            [string[]]@('marker', $marker), $workspace, 4096)
        $childId = $child.ProcessId
        try {
            $child.AssertSuspendedOwned()
            $child.AssertSuspendedOwned()
            Start-Sleep -Milliseconds 200
            Assert-Primitive (-not (Test-Path -LiteralPath $marker)) 'Suspended child ran before registration/ack.'
        }
        finally { $child.Dispose() }
        Assert-Rejected { [void][Diagnostics.Process]::GetProcessById($childId) } 'Suspended child was orphaned.'
    }
    Invoke-PrimitiveCase 'successful native completion returns exact output and identity' {
        $result = Invoke-HermeticChild @('success')
        Assert-Primitive ($result.ExitCode -eq 0 -and $result.Stdout -ceq '{"ok":true}' -and
            $result.Stderr -ceq '' -and $result.ProcessId -gt 0) 'Native completion evidence differs.'
    }
    Invoke-PrimitiveCase 'owned observer polling stays nonblocking and waits for full Job and EOF completion' {
        $child = [Stratus.ParentStage.NativeChild]::StartSuspended($fakeExecutable, $fakeHash, $fakeLock,
            [string[]]@('brief-descendant'), $workspace, 4096)
        try {
            $child.Resume()
            $clock = [Diagnostics.Stopwatch]::StartNew()
            $result = $child.PollResult(5000)
            Assert-Primitive ($null -eq $result -and $clock.Elapsed.TotalMilliseconds -lt 1000) 'Polling blocked during live observation.'
            while ($null -eq $result -and $clock.Elapsed.TotalSeconds -lt 5) {
                Start-Sleep -Milliseconds 20
                $result = $child.PollResult(5000)
            }
            Assert-Primitive ($null -ne $result -and $result.ExitCode -eq 0) 'Owned Job/EOF completion was not collected.'
            Assert-Primitive ([object]::ReferenceEquals($result, $child.PollResult(5000))) 'Completed native result was not stable.'
        }
        finally { $child.Dispose() }
    }
    Invoke-PrimitiveCase 'owned observer polling deadline kills descendants without resetting the lifetime' {
        $marker = Join-Path $workspace 'polled-descendant-pid.txt'
        $child = [Stratus.ParentStage.NativeChild]::StartSuspended($fakeExecutable, $fakeHash, $fakeLock,
            [string[]]@('descendant', $marker), $workspace, 4096)
        try {
            $child.Resume()
            $clock = [Diagnostics.Stopwatch]::StartNew()
            $pollFailure = ''
            $result = $null
            while ($clock.Elapsed.TotalSeconds -lt 5 -and $pollFailure -ceq '' -and $null -eq $result) {
                try { $result = $child.PollResult(500) }
                catch { $pollFailure = $_.Exception.ToString() }
                Start-Sleep -Milliseconds 20
            }
            Assert-Primitive ($null -eq $result -and $pollFailure -like '*lifetime*') 'Polling returned false completion or ignored its shared deadline.'
            $childId = [int][IO.File]::ReadAllText($marker)
            Assert-Rejected { [void][Diagnostics.Process]::GetProcessById($childId) } 'Polled descendant was orphaned.'
        }
        finally { $child.Dispose() }
    }
    Invoke-PrimitiveCase 'held process lease rejects PID-reuse identity and terminates only its exact handle' {
        $marker = Join-Path $workspace 'leased-marker.txt'
        $child = [Stratus.ParentStage.NativeChild]::StartSuspended($fakeExecutable, $fakeHash, $fakeLock,
            [string[]]@('marker', $marker), $workspace, 4096)
        $lease = $null
        try {
            $wrongStart = [DateTimeOffset]::Parse($child.StartedAt).AddTicks(-1).ToString('o')
            Assert-Rejected {
                [void][Stratus.ParentStage.ProcessLease]::Open($child.ProcessId, $wrongStart,
                    $fakeExecutable, $fakeHash, $fakeLock)
            } 'Creation-time/PID-reuse mismatch was accepted.'
            $lease = [Stratus.ParentStage.ProcessLease]::Open($child.ProcessId, $child.StartedAt,
                $fakeExecutable, $fakeHash, $fakeLock)
            $lease.AssertAlive()
            $lease.TerminateAndWait(5000)
            Assert-Primitive (-not $lease.IsAlive()) 'Held exact process was not terminated.'
            $lease.TerminateAndWait(5000)
            Assert-Primitive (-not (Test-Path -LiteralPath $marker)) 'Suspended leased child was resumed.'
        }
        finally {
            if ($null -ne $lease) { $lease.Dispose() }
            $child.Dispose()
        }
    }
    Invoke-PrimitiveCase 'native argument quoting preserves spaces, quotes, slashes and empty tokens' {
        $arguments = [string[]]@('', 'two words', 'quote"inside', 'C:\with space\', '\\host\share', 'plain')
        $result = Invoke-HermeticChild (@('arguments') + $arguments)
        $expected = @($arguments | ForEach-Object { [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($_)) }) -join "`r`n"
        Assert-Primitive ($result.Stdout -ceq ($expected + "`r`n")) 'Native token roundtrip differs.'
    }
    Invoke-PrimitiveCase 'byte overflow on either stream terminates the whole job early' {
        foreach ($mode in @('overflow', 'stderr')) {
            $clock = [Diagnostics.Stopwatch]::StartNew()
            Assert-Rejected { [void](Invoke-HermeticChild @($mode) -MaximumBytes 1024 -TimeoutMilliseconds 10000) } 'Native overflow accepted.'
            Assert-Primitive ($clock.Elapsed.TotalSeconds -lt 8) 'Overflow was not terminated early.'
        }
    }
    Invoke-PrimitiveCase 'hung child obeys wall-clock bound without leaving an orphan' {
        $marker = Join-Path $workspace 'hung-pid.txt'
        Assert-Rejected { [void](Invoke-HermeticChild @('hang', $marker) -TimeoutMilliseconds 300) } 'Hung child accepted.'
        $childId = [int][IO.File]::ReadAllText($marker)
        Assert-Rejected { [void][Diagnostics.Process]::GetProcessById($childId) } 'Hung child was orphaned.'
    }
    Invoke-PrimitiveCase 'a descendant surviving primary exit is killed and cannot count as success' {
        $marker = Join-Path $workspace 'descendant-pid.txt'
        Assert-Rejected { [void](Invoke-HermeticChild @('descendant', $marker)) } 'Descendant escape accepted.'
        $childId = [int][IO.File]::ReadAllText($marker)
        Assert-Rejected { [void][Diagnostics.Process]::GetProcessById($childId) } 'Descendant was orphaned.'
    }
    Invoke-PrimitiveCase 'a short-lived descendant may finish within the original shared deadline' {
        $clock = [Diagnostics.Stopwatch]::StartNew()
        $result = Invoke-HermeticChild @('brief-descendant') -TimeoutMilliseconds 5000
        Assert-Primitive ($result.ExitCode -eq 0 -and $clock.Elapsed.TotalSeconds -lt 5) 'Natural descendant completion was rejected or reset the deadline.'
    }
    Invoke-PrimitiveCase 'executable hash mismatch prevents launch and held bytes cannot be replaced' {
        Assert-Rejected {
            [void][Stratus.ParentStage.NativeChild]::StartSuspended($fakeExecutable, ('0' * 64), $fakeLock,
                [string[]]@('success'), $workspace, 4096)
        } 'Wrong executable hash accepted.'
        Assert-Rejected { [IO.File]::WriteAllText($fakeExecutable, 'changed') } 'Executable replaced while held.'
    }

    Invoke-PrimitiveCase 'direct production invocation stops before every side effect' {
        $currentHost = (Get-Process -Id $PID).Path
        $hostHash = [Stratus.ParentStage.Files]::Hash([IO.File]::ReadAllBytes($currentHost))
        $hostLock = [Stratus.ParentStage.Files]::Lock($currentHost, $hostHash)
        $native = $null
        $before = @(Get-ChildItem -LiteralPath $workspace -Force | ForEach-Object { $_.Name } | Sort-Object)
        try {
            $native = [Stratus.ParentStage.NativeChild]::StartSuspended($currentHost, $hostHash, $hostLock,
                [string[]]@('-NoLogo', '-NoProfile', '-NonInteractive', '-File', $scriptPath,
                    '-ContractPath', (Join-Path $workspace 'must-not-read-or-create.json'),
                    '-ContractSha256', ('0' * 64), '-RuntimeSha256', ('0' * 64), '-BootstrapNonce', ('a' * 64)), $workspace, 32768)
            $native.Resume()
            $result = $native.Wait(10000)
            Assert-Primitive ($result.ExitCode -ne 0 -and $result.Stderr.Contains('WIP and non-authorizing')) 'Production entry blocker did not fire.'
        }
        finally {
            if ($null -ne $native) { $native.Dispose() }
            $hostLock.Dispose()
        }
        $after = @(Get-ChildItem -LiteralPath $workspace -Force | ForEach-Object { $_.Name } | Sort-Object)
        Assert-Primitive (($before -join "`n") -ceq ($after -join "`n")) 'Blocked entry created an artifact.'
    }
    Invoke-PrimitiveCase 'real PowerShell helper emits one raw native-compatible frame and binds its native child identity' {
        $producerPath = Join-Path $workspace 'bootstrap-producer-only.ps1'
        $fixtureDefinition = 'function Invoke-BootstrapFixture {' + ${function:Invoke-BootstrapFixture}.ToString() + "`n}"
        $producer = @'
param([string] $runtimePath, [string] $sourcePath, [string] $workspace)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Add-Type -Path $runtimePath
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($sourcePath, [ref]$tokens, [ref]$errors)
if (@($errors).Count -ne 0) { throw 'Producer source parser failure.' }
foreach ($name in @('Get-ParentProcessIdentity', 'Assert-ParentRegularAncestry', 'Assert-ParentBootstrapHeldFile',
    'Read-ParentBootstrapPublication', 'New-ParentBootstrapFrame', 'Write-ParentBootstrapReady')) {
    $definition = @($ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $name }, $false))
    if ($definition.Count -ne 1) { throw 'Producer helper is ambiguous.' }
    . ([scriptblock]::Create($definition[0].Extent.Text))
}
'@ + "`n" + $fixtureDefinition + @'

Invoke-BootstrapFixture {
    $frame = New-ParentBootstrapFrame ('a' * 64)
    Write-ParentBootstrapReady $frame
    $denied = $false
    try { Write-ParentBootstrapReady $frame } catch { $denied = $true }
    if (-not $denied -or -not $script:bootstrapWriteAttempted -or $null -eq $script:bootstrapOutput) { throw 'Producer retried or lost stdout ownership.' }
}
'@
        [IO.File]::WriteAllText($producerPath, $producer, [Stratus.ParentStage.Files]::Utf8)
        $currentHost = (Get-ParentProcessIdentity $PID).executable
        $hostHash = [Stratus.ParentStage.Files]::Hash([IO.File]::ReadAllBytes($currentHost))
        $hostLock = [Stratus.ParentStage.Files]::Lock($currentHost, $hostHash)
        $native = $null
        try {
            $native = [Stratus.ParentStage.NativeChild]::StartSuspended($currentHost, $hostHash, $hostLock,
                [string[]]@('-NoLogo', '-NoProfile', '-NonInteractive', '-File', $producerPath,
                    '-runtimePath', $runtimePath, '-sourcePath', $scriptPath, '-workspace', $workspace), $workspace, 4096)
            $native.Resume()
            $result = $native.Wait(15000)
            Assert-Primitive ($result.ExitCode -eq 0 -and $result.Stderr -ceq '') 'Extracted producer child failed or wrote diagnostics.'
            $expected = '{"format":"stratus-independent-watchdog-ready-v1","session_id":"' + ('b' * 32) +
                '","nonce":"' + ('a' * 64) + '","contract_sha256":"' + [Stratus.ParentStage.Files]::Hash([Stratus.ParentStage.Files]::Utf8.GetBytes('{"contract":true}')) +
                '","source_sha256":"' + [Stratus.ParentStage.Files]::Hash([Stratus.ParentStage.Files]::Utf8.GetBytes('# hermetic producer source')) +
                '","runtime_sha256":"' + [Stratus.ParentStage.Files]::Hash([IO.File]::ReadAllBytes($runtimePath)) +
                '","host_sha256":"' + $hostHash + '","pid":"' + $result.ProcessId + '","start_time_utc":"' + $result.StartedAt +
                '","lease_sha256":"' + [Stratus.ParentStage.Files]::Hash([Stratus.ParentStage.Files]::Utf8.GetBytes('{"lease":true}')) +
                '","ready_sha256":"' + [Stratus.ParentStage.Files]::Hash([Stratus.ParentStage.Files]::Utf8.GetBytes('{"ready":true}')) + '"}' + "`n"
            Assert-Primitive ($result.Stdout -ceq $expected -and $result.StdoutBytes -eq [Stratus.ParentStage.Files]::Utf8.GetByteCount($expected)) 'Raw stdout differs from native frame prefix, identity, LF or exact byte count.'
        }
        finally { if ($null -ne $native) { $native.Dispose() }; $hostLock.Dispose() }
    }
}
finally {
    foreach ($child in $activeChildren) {
        try { $child.Dispose() } catch { $testFailures.Add('fixture disposal: ' + $_.Exception.Message) }
    }
    $resolved = [IO.Path]::GetFullPath($workspace)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($resolved) -notmatch '^stratus-parent-primitives-[0-9a-f]{32}$') {
        throw 'Refusing cleanup outside this exact hermetic test workspace.'
    }
    try { Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction Stop }
    catch { $testFailures.Add('fixture cleanup: ' + $_.Exception.Message) }
}
if ($testFailures.Count -ne 0) { throw ($testFailures -join "`n") }
Write-Output ("PASS: $passed parent-stage primitive/entry-blocker cases; full protocol acceptance remains BLOCKED.")
