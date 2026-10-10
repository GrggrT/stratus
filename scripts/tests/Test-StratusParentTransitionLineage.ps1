[CmdletBinding()]
param([string] $LibraryPath)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $PSBoundParameters.ContainsKey('LibraryPath')) {
    $LibraryPath = Join-Path $PSScriptRoot '..\Stratus.ParentTransitionLineage.ps1'
}

# Read and parse source, then extract only reviewed function definitions. No
# production entry, native runtime, subprocess, Docker, or filesystem fixture runs.
# Inputs below are in-memory schema fixtures, NOT trusted runtime attestations.
$expectedCases = 70
$script:lineageCaseCount = 0
$script:lineagePassed = 0
$script:lineageFailures = [Collections.Generic.List[string]]::new()
$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    [IO.Path]::GetFullPath($LibraryPath), [ref]$tokens, [ref]$parseErrors
)
$functionNames = @('Assert-StratusLineageObject', 'Assert-StratusLineageString',
    'Assert-StratusLineageInteger', 'Assert-StratusLineageHash', 'Assert-StratusLineagePath',
    'Get-StratusLineageUtc', 'Assert-StratusLineageProcess', 'Assert-StratusLineageDocument',
    'Assert-StratusParentTransitionLineageLeaf')

function Assert-LineageTest {
    param([bool] $Condition, [string] $Message)
    if (-not $Condition) { throw $Message }
}

function Invoke-LineageCase {
    param([string] $Name, [scriptblock] $Action)
    $script:lineageCaseCount++
    try { & $Action; $script:lineagePassed++; Write-Output ('PASS parent-lineage leaf: ' + $Name) }
    catch {
        $script:lineageFailures.Add($Name + ': ' + $_.Exception.GetBaseException().Message)
        Write-Output ('FAIL parent-lineage leaf: ' + $Name)
    }
}

Invoke-LineageCase 'source contains only the exact pure function definitions' {
    Assert-LineageTest (@($parseErrors).Count -eq 0) 'Library source does not parse.'
    $statements = @($ast.EndBlock.Statements)
    Assert-LineageTest ($statements.Count -eq $functionNames.Count) 'Library has executable top-level statements.'
    foreach ($statement in $statements) {
        Assert-LineageTest ($statement -is [Management.Automation.Language.FunctionDefinitionAst] -and
            $statement.Name -cin $functionNames) 'Unexpected library top-level definition.'
    }
    foreach ($name in $functionNames) {
        Assert-LineageTest (@($statements | Where-Object { $_.Name -ceq $name }).Count -eq 1) "Missing or duplicate helper: $name"
    }
}

Invoke-LineageCase 'AST permits no IO process cache or dynamic execution surface' {
    $commands = @($ast.FindAll({ param($node) $node -is [Management.Automation.Language.CommandAst] }, $true))
    foreach ($command in $commands) {
        Assert-LineageTest ($command.GetCommandName() -cin $functionNames -and
            $command.InvocationOperator -eq [Management.Automation.Language.TokenKind]::Unknown) `
            ('Unexpected or dynamic command: ' + $command.Extent.Text)
    }
    $variables = @($ast.FindAll({ param($node) $node -is [Management.Automation.Language.VariableExpressionAst] }, $true))
    foreach ($variable in $variables) {
        Assert-LineageTest ($variable.VariablePath.UserPath -notmatch '^(?i:script|global|env):' -and
            $variable.VariablePath.UserPath -notmatch '(?i)validatedCompletions') 'Library touches nonlocal state.'
    }
    $types = @($ast.FindAll({ param($node) $node -is [Management.Automation.Language.TypeExpressionAst] }, $true))
    $allowedTypes = @('Management.Automation.PSMemberTypes', 'regex', 'string', 'IO.Path', 'DateTimeOffset',
        'Globalization.CultureInfo', 'TimeSpan', 'int', 'long', 'Array', 'bool',
        'Collections.Generic.HashSet[string]', 'StringComparer')
    foreach ($type in $types) {
        Assert-LineageTest ($type.TypeName.FullName -cin $allowedTypes) ('Unexpected static type surface: ' + $type.TypeName.FullName)
    }
    $pathCalls = @($ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.InvokeMemberExpressionAst] -and
        $node.Expression.Extent.Text -ceq '[IO.Path]'
    }, $true))
    Assert-LineageTest ($pathCalls.Count -eq 1 -and $pathCalls[0].Member.Extent.Text -ceq 'GetFullPath') `
        'Only pure Path.GetFullPath string normalization is permitted.'
}

if ($script:lineageFailures.Count -ne 0) {
    $global:LASTEXITCODE = 1
    throw ('Leaf AST contract failed before extraction: ' + ($script:lineageFailures -join '; '))
}
foreach ($name in $functionNames) {
    $definition = @($ast.EndBlock.Statements | Where-Object { $_.Name -ceq $name })[0]
    . ([scriptblock]::Create($definition.Extent.Text))
}

function New-LineageFixture {
    $ownerStart = '2026-09-14T12:00:00.0000000+00:00'
    $owner = [pscustomobject]@{ pid = 42; start_time_utc = $ownerStart; executable = 'C:\lineage-fixture\pwsh.exe'; sha256 = ('a' * 64) }
    $binding = [pscustomobject]@{
        format = 'stratus-parent-stage-binding-v1'; session_id = ('1' * 32)
        contract_path = 'C:\lineage-fixture\parent-contract.json'; contract_sha256 = ('a' * 64)
        lease_path = 'C:\lineage-fixture\parent-lease.json'; lease_sha256 = ('b' * 64)
        ready_path = 'C:\lineage-fixture\parent-ready.json'; ready_sha256 = ('c' * 64)
        heartbeat_path = 'C:\lineage-fixture\parent-heartbeat.json'
        mutation_fence_path = 'C:\lineage-fixture\parent-fence.json'; mutation_fence_sha256 = ('d' * 64)
        owner = $owner
        watchdog = [pscustomobject]@{ pid = 84; start_time_utc = $ownerStart; executable = $owner.executable; sha256 = $owner.sha256 }
        deadline_utc = '2026-09-14T12:20:00.0000000+00:00'
        r1_anchor = [pscustomobject]@{ directory = 'C:\lineage-fixture\r1.accepted'
            acceptance_sha256 = ('e' * 64); evidence_manifest_sha256 = ('f' * 64); sealed_sha256 = ('a' * 64) }
        source_manifest_sha256 = ('b' * 64)
    }
    $expected = [pscustomobject]@{
        parent_stage = $binding; parent_armed_at = '2026-09-14T12:00:10.0000000+00:00'
        transition_session_id = ('2' * 32); transition_armed_at = '2026-09-14T12:00:20.0000000+00:00'
        transition_deadline_utc = '2026-09-14T12:05:00.0000000+00:00'; observed_at = '2026-09-14T12:01:00.0000000+00:00'
        start_fence_path = 'C:\lineage-fixture\start-fence.json'; start_fence_sha256 = ('c' * 64)
        start_attempt_path = 'C:\lineage-fixture\start-attempt.json'; start_attempt_sha256 = ('d' * 64)
        start_completion_path = 'C:\lineage-fixture\start-complete.json'; start_completion_sha256 = ('e' * 64)
        postgres_container_id = ('6' * 64); docker_executable_path = 'C:\lineage-fixture\docker.exe'; docker_executable_sha256 = ('f' * 64)
    }
    $fence = [pscustomobject]@{ path = $expected.start_fence_path; sha256 = $expected.start_fence_sha256
        value = [pscustomobject]@{ format = 'stratus-successor-transition-start-fence-v1'
            session_id = $expected.transition_session_id; owner_pid = $owner.pid; owner_start_time_utc = $ownerStart
            created_at = '2026-09-14T12:00:15.0000000+00:00' } }
    $attempt = [pscustomobject]@{ path = $expected.start_attempt_path; sha256 = $expected.start_attempt_sha256
        value = [pscustomobject]@{ format = 'stratus-successor-transition-start-attempt-v1'
            session_id = $expected.transition_session_id; owner_pid = $owner.pid; owner_start_time_utc = $ownerStart
            start_fence_path = $expected.start_fence_path; start_fence_sha256 = $expected.start_fence_sha256
            command = @('start', $expected.postgres_container_id); issued_at = '2026-09-14T12:00:30.0000000+00:00' } }
    $completion = [pscustomobject]@{ path = $expected.start_completion_path; sha256 = $expected.start_completion_sha256
        value = [pscustomobject]@{ format = 'stratus-successor-transition-start-complete-v1'
            session_id = $expected.transition_session_id; owner_pid = $owner.pid; owner_start_time_utc = $ownerStart
            start_attempt_path = $expected.start_attempt_path; start_attempt_sha256 = $expected.start_attempt_sha256
            outcome = 'started'; completed_at = '2026-09-14T12:00:32.0000000+00:00'
            bounded_start = [pscustomobject]@{ command = @('start', $expected.postgres_container_id)
                docker_executable_path = $expected.docker_executable_path; docker_executable_sha256 = $expected.docker_executable_sha256
                process_timeout_milliseconds = 15000; stdout_maximum_bytes = 4096; stderr_maximum_bytes = 4096
                requested_at = '2026-09-14T12:00:30.0000000+00:00'; completed_at = '2026-09-14T12:00:32.0000000+00:00'
                exit_code = 0; output_container_id = $expected.postgres_container_id; timed_out = $false
                output_exceeded = $false; process_started = $true; process_exited = $true; process_id = 126; kill_issued = $false } } }
    return [pscustomobject]@{ expected = $expected; fence = $fence; attempt = $attempt; completion = $completion }
}

function Invoke-LineageFixture {
    param([object] $Fixture)
    Assert-StratusParentTransitionLineageLeaf -FenceDocument $Fixture.fence -AttemptDocument $Fixture.attempt `
        -CompletionDocument $Fixture.completion -Expected $Fixture.expected
}

function Set-LineageOwnerCreation {
    param([object] $Fixture, [string] $CreatedAt)
    # Keep every owner identity equal so chronology tests cannot pass merely by
    # triggering the older cross-document identity-mismatch rejection.
    $Fixture.expected.parent_stage.owner.start_time_utc = $CreatedAt
    foreach ($document in @($Fixture.fence, $Fixture.attempt, $Fixture.completion)) {
        $document.value.owner_start_time_utc = $CreatedAt
    }
}

Invoke-LineageCase 'valid distinct parent and transition sessions return only nonauthorizing schema data' {
    $fixture = New-LineageFixture
    $before = $fixture | ConvertTo-Json -Depth 12 -Compress
    $script:validatedCompletions = @{ sentinel = 'unchanged' }
    $result = @(Invoke-LineageFixture $fixture)
    Assert-LineageTest ($result.Count -eq 1) 'Leaf leaked pipeline output.'
    $result = $result[0]
    Assert-LineageTest ($result.schema_only -is [bool] -and $result.schema_only -and
        $result.format -ceq 'stratus-parent-transition-lineage-leaf-check-v1' -and
        $result.parent_session_id -ceq $fixture.expected.parent_stage.session_id -and
        $result.transition_session_id -ceq $fixture.expected.transition_session_id) 'Schema result identity differs.'
    foreach ($name in @('document_bytes_verified', 'provenance_verified', 'cache_use_permitted', 'full_lineage_accepted')) {
        Assert-LineageTest ($result.$name -is [bool] -and -not $result.$name) "Leaf granted $name."
    }
    $authorityNames = @('postgres_start_permitted', 'api_start_permitted', 'worker_start_permitted',
        'safety_control_start_permitted', 'live_transition_permitted', 'binance_kill_switch_removal_permitted',
        'migration_container_permitted', 'container_remove_permitted')
    Assert-LineageTest (@($result.authorization.PSObject.Properties).Count -eq $authorityNames.Count) 'Authority schema differs.'
    foreach ($name in $authorityNames) {
        Assert-LineageTest ($result.authorization.$name -is [bool] -and -not $result.authorization.$name) "Leaf authorized $name."
    }
    Assert-LineageTest (($fixture | ConvertTo-Json -Depth 12 -Compress) -ceq $before -and
        $script:validatedCompletions.Count -eq 1 -and $script:validatedCompletions.sentinel -ceq 'unchanged') 'Leaf mutated fixture or cache.'
    $fixture.expected.transition_session_id = ('9' * 32)
    Assert-LineageTest ($result.transition_session_id -ceq ('2' * 32)) 'Result retained mutable input identity.'
}

Invoke-LineageCase 'existing two-second skew allowance and native Int64 remain valid' {
    $fixture = New-LineageFixture
    $fixture.attempt.value.issued_at = '2026-09-14T12:00:18.0000000+00:00'
    $fixture.completion.value.bounded_start.requested_at = '2026-09-14T12:00:16.0000000+00:00'
    $fixture.completion.value.bounded_start.process_id = [long]126
    $result = Invoke-LineageFixture $fixture
    Assert-LineageTest ($result.schema_only -and -not $result.cache_use_permitted) 'Existing leaf tolerance changed.'
}

# Exact equality at each new two-second boundary remains accepted. Paired
# rejections below move just one 100-nanosecond tick beyond that boundary.
$chronologyBoundaries = @(
    @{ name = 'owner birth at parent arming plus two seconds'; mutate = {
        param($f) Set-LineageOwnerCreation $f '2026-09-14T12:00:12.0000000+00:00'
    } },
    @{ name = 'watchdog birth at parent arming plus two seconds'; mutate = {
        param($f) $f.expected.parent_stage.watchdog.start_time_utc = '2026-09-14T12:00:12.0000000+00:00'
    } },
    @{ name = 'fence at owner birth minus two seconds'; mutate = {
        param($f)
        Set-LineageOwnerCreation $f '2026-09-14T12:00:12.0000000+00:00'
        $f.fence.value.created_at = '2026-09-14T12:00:10.0000000+00:00'
    } },
    @{ name = 'fence at parent arming minus two seconds'; mutate = {
        param($f) $f.fence.value.created_at = '2026-09-14T12:00:08.0000000+00:00'
    } },
    @{ name = 'fence at transition arming plus two seconds'; mutate = {
        param($f) $f.fence.value.created_at = '2026-09-14T12:00:22.0000000+00:00'
    } },
    @{ name = 'fence at attempt issue plus two seconds'; mutate = {
        param($f)
        $f.attempt.value.issued_at = '2026-09-14T12:00:18.0000000+00:00'
        $f.fence.value.created_at = '2026-09-14T12:00:20.0000000+00:00'
    } }
)
Assert-LineageTest ($chronologyBoundaries.Count -eq 6) 'The fixed chronology boundary inventory changed.'
foreach ($case in $chronologyBoundaries) {
    Invoke-LineageCase $case.name {
        $fixture = New-LineageFixture
        & $case.mutate $fixture
        $script:validatedCompletions = @{ sentinel = 'unchanged' }
        $result = Invoke-LineageFixture $fixture
        Assert-LineageTest ($result.schema_only -and -not $result.cache_use_permitted -and
            -not $result.provenance_verified -and -not $result.full_lineage_accepted) 'Boundary fixture became authority.'
        Assert-LineageTest ($script:validatedCompletions.Count -eq 1 -and
            $script:validatedCompletions.sentinel -ceq 'unchanged') 'Boundary fixture changed a completion cache.'
    }
}

# Each mutation starts with fresh in-memory values. Fixed case count guards against
# accidentally dropping a rejection group. No malformed fixture receives cache use.
$rejections = @(
    @{ name = 'missing expected binding'; mutate = { param($f) $f.expected = $null } },
    @{ name = 'missing parent-stage binding'; mutate = { param($f) $f.expected.parent_stage = $null } },
    @{ name = 'incomplete parent-stage binding'; mutate = { param($f) $f.expected.parent_stage.PSObject.Properties.Remove('lease_sha256') } },
    @{ name = 'unknown expected field'; mutate = { param($f) $f.expected | Add-Member -NotePropertyName foreign -NotePropertyValue $true } },
    @{ name = 'incomplete R1 anchor'; mutate = { param($f) $f.expected.parent_stage.r1_anchor.PSObject.Properties.Remove('sealed_sha256') } },
    @{ name = 'expected owner PID string'; mutate = { param($f) $f.expected.parent_stage.owner.pid = '42' } },
    @{ name = 'expected watcher aliases owner'; mutate = { param($f) $f.expected.parent_stage.watchdog.pid = 42 } },
    @{ name = 'expected malformed session'; mutate = { param($f) $f.expected.transition_session_id = ('2' * 32) + "`n" } },
    @{ name = 'expected hash uppercase'; mutate = { param($f) $f.expected.start_attempt_sha256 = ('D' * 64) } },
    @{ name = 'expected relative path'; mutate = { param($f) $f.expected.start_attempt_path = 'start-attempt.json' } },
    @{ name = 'expected path alias'; mutate = { param($f) $f.expected.start_completion_path = $f.expected.start_attempt_path } },
    @{ name = 'expected deadline outside parent'; mutate = { param($f) $f.expected.transition_deadline_utc = $f.expected.parent_stage.deadline_utc } },
    @{ name = 'expected parent armed after transition'; mutate = { param($f) $f.expected.parent_armed_at = '2026-09-14T12:00:21.0000000+00:00' } },
    @{ name = 'expected observation before transition'; mutate = { param($f) $f.expected.observed_at = '2026-09-14T12:00:19.0000000+00:00' } },
    @{ name = 'missing fence'; mutate = { param($f) $f.fence = $null } },
    @{ name = 'missing attempt'; mutate = { param($f) $f.attempt = $null } },
    @{ name = 'missing completion'; mutate = { param($f) $f.completion = $null } },
    @{ name = 'document unknown field'; mutate = { param($f) $f.completion | Add-Member -NotePropertyName foreign -NotePropertyValue $true } },
    @{ name = 'document hash mismatch'; mutate = { param($f) $f.completion.sha256 = ('a' * 64) } },
    @{ name = 'document noncanonical path'; mutate = { param($f) $f.attempt.path = 'C:\lineage-fixture\.\start-attempt.json' } },
    @{ name = 'fence session substituted with parent'; mutate = { param($f) $f.fence.value.session_id = $f.expected.parent_stage.session_id } },
    @{ name = 'attempt wrong owner'; mutate = { param($f) $f.attempt.value.owner_pid = 43 } },
    @{ name = 'completion owner PID string'; mutate = { param($f) $f.completion.value.owner_pid = '42' } },
    @{ name = 'completion reused owner creation time'; mutate = { param($f) $f.completion.value.owner_start_time_utc = '2026-09-14T11:59:59.0000000+00:00' } },
    @{ name = 'attempt wrong fence hash'; mutate = { param($f) $f.attempt.value.start_fence_sha256 = ('b' * 64) } },
    @{ name = 'completion wrong attempt hash'; mutate = { param($f) $f.completion.value.start_attempt_sha256 = ('b' * 64) } },
    @{ name = 'completion unknown schema field'; mutate = { param($f) $f.completion.value | Add-Member -NotePropertyName foreign -NotePropertyValue $true } },
    @{ name = 'completion wrong outcome'; mutate = { param($f) $f.completion.value.outcome = 'failed' } },
    @{ name = 'command scalar'; mutate = { param($f) $f.attempt.value.command = 'start' } },
    @{ name = 'command extra token'; mutate = { param($f) $f.attempt.value.command += '--foreign' } },
    @{ name = 'bounded command wrong container'; mutate = { param($f) $f.completion.value.bounded_start.command = @('start', ('7' * 64)) } },
    @{ name = 'bounded command scalar'; mutate = { param($f) $f.completion.value.bounded_start.command = 'start' } },
    @{ name = 'bounded command non-string token'; mutate = { param($f) $f.completion.value.bounded_start.command = @('start', 6) } },
    @{ name = 'bounded Docker hash mismatch'; mutate = { param($f) $f.completion.value.bounded_start.docker_executable_sha256 = ('a' * 64) } },
    @{ name = 'bounded timeout changed'; mutate = { param($f) $f.completion.value.bounded_start.process_timeout_milliseconds = 15001 } },
    @{ name = 'bounded output limit changed'; mutate = { param($f) $f.completion.value.bounded_start.stdout_maximum_bytes = 4097 } },
    @{ name = 'bounded stderr limit changed'; mutate = { param($f) $f.completion.value.bounded_start.stderr_maximum_bytes = 4097 } },
    @{ name = 'bounded nonzero exit'; mutate = { param($f) $f.completion.value.bounded_start.exit_code = 1 } },
    @{ name = 'bounded boolean string'; mutate = { param($f) $f.completion.value.bounded_start.process_exited = 'true' } },
    @{ name = 'bounded timed out'; mutate = { param($f) $f.completion.value.bounded_start.timed_out = $true } },
    @{ name = 'bounded overflow'; mutate = { param($f) $f.completion.value.bounded_start.output_exceeded = $true } },
    @{ name = 'bounded kill issued'; mutate = { param($f) $f.completion.value.bounded_start.kill_issued = $true } },
    @{ name = 'bounded process not exited'; mutate = { param($f) $f.completion.value.bounded_start.process_exited = $false } },
    @{ name = 'bounded process not started'; mutate = { param($f) $f.completion.value.bounded_start.process_started = $false } },
    @{ name = 'bounded process PID zero'; mutate = { param($f) $f.completion.value.bounded_start.process_id = 0 } },
    @{ name = 'bounded process PID exceeds range'; mutate = { param($f) $f.completion.value.bounded_start.process_id = [long]2147483648 } },
    @{ name = 'bounded process PID string'; mutate = { param($f) $f.completion.value.bounded_start.process_id = '126' } },
    @{ name = 'noncanonical UTC'; mutate = { param($f) $f.completion.value.completed_at = '2026-09-14T12:00:32Z' } },
    @{ name = 'completion precedes request'; mutate = { param($f) $f.completion.value.bounded_start.completed_at = '2026-09-14T12:00:29.0000000+00:00' } },
    @{ name = 'bounded and outer completion times disagree'; mutate = { param($f) $f.completion.value.completed_at = '2026-09-14T12:00:33.0000000+00:00' } },
    @{ name = 'attempt precedes armed time beyond skew'; mutate = { param($f) $f.attempt.value.issued_at = '2026-09-14T12:00:17.0000000+00:00' } },
    @{ name = 'request precedes attempt beyond skew'; mutate = { param($f) $f.completion.value.bounded_start.requested_at = '2026-09-14T12:00:27.0000000+00:00' } },
    @{ name = 'completion beyond fixed observation'; mutate = { param($f) $f.completion.value.completed_at = '2026-09-14T12:01:03.0000000+00:00'; $f.completion.value.bounded_start.completed_at = $f.completion.value.completed_at } },
    @{ name = 'owner birth one tick after parent arming allowance';
        expected_failure = 'Expected owner/watchdog birth postdates parent arming.'; mutate = {
            param($f) Set-LineageOwnerCreation $f '2026-09-14T12:00:12.0000001+00:00'
        } },
    @{ name = 'watchdog birth one tick after parent arming allowance';
        expected_failure = 'Expected owner/watchdog birth postdates parent arming.'; mutate = {
            param($f) $f.expected.parent_stage.watchdog.start_time_utc = '2026-09-14T12:00:12.0000001+00:00'
        } },
    @{ name = 'fence one tick before owner birth allowance';
        expected_failure = 'Start-fence publication order differs from the producer.'; mutate = {
            param($f)
            Set-LineageOwnerCreation $f '2026-09-14T12:00:12.0000000+00:00'
            $f.fence.value.created_at = '2026-09-14T12:00:09.9999999+00:00'
        } },
    @{ name = 'fence one tick before parent arming allowance';
        expected_failure = 'Start-fence publication order differs from the producer.'; mutate = {
            param($f) $f.fence.value.created_at = '2026-09-14T12:00:07.9999999+00:00'
        } },
    @{ name = 'fence one tick after transition arming allowance';
        expected_failure = 'Start-fence publication order differs from the producer.'; mutate = {
            param($f) $f.fence.value.created_at = '2026-09-14T12:00:22.0000001+00:00'
        } },
    @{ name = 'fence one tick after attempt issue allowance';
        expected_failure = 'Start-fence publication order differs from the producer.'; mutate = {
            param($f)
            $f.attempt.value.issued_at = '2026-09-14T12:00:18.0000000+00:00'
            $f.fence.value.created_at = '2026-09-14T12:00:20.0000001+00:00'
        } },
    @{ name = 'regression fence created after completed start';
        expected_failure = 'Start-fence publication order differs from the producer.'; mutate = {
            param($f) $f.fence.value.created_at = '2026-09-14T12:00:50.0000000+00:00'
        } }
)
Assert-LineageTest ($rejections.Count -eq 60) 'The fixed rejection inventory changed.'
foreach ($case in $rejections) {
    Invoke-LineageCase $case.name {
        $fixture = New-LineageFixture
        & $case.mutate $fixture
        $script:validatedCompletions = @{ sentinel = 'unchanged' }
        $failure = $null
        try { $null = Invoke-LineageFixture $fixture }
        catch { $failure = $_.Exception.GetBaseException().Message }
        Assert-LineageTest (-not [string]::IsNullOrWhiteSpace($failure)) 'Malformed lineage was accepted.'
        if ($case.ContainsKey('expected_failure')) {
            Assert-LineageTest ($failure -ceq $case.expected_failure) ('Wrong chronology rejection boundary: ' + $failure)
        }
        Assert-LineageTest ($script:validatedCompletions.Count -eq 1 -and $script:validatedCompletions.sentinel -ceq 'unchanged') `
            'Rejected lineage changed a completion cache.'
    }
}

if ($script:lineageCaseCount -ne $expectedCases -or $script:lineagePassed -ne $expectedCases -or $script:lineageFailures.Count -ne 0) {
    $global:LASTEXITCODE = 1
    throw ("Parent transition leaf checks failed: $($script:lineagePassed)/$expectedCases; " + ($script:lineageFailures -join '; '))
}
$global:LASTEXITCODE = 0
Write-Output ("PASS $expectedCases/$expectedCases parent transition leaf checks; schema-only, nonauthorizing, not completion-cache acceptance.")
