[CmdletBinding()]
param([string] $GatePath)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $PSBoundParameters.ContainsKey('GatePath')) {
    $GatePath = Join-Path $PSScriptRoot '..\Assert-StratusPostMigrationSuccessorGate.ps1'
}

# Managed lease/transition-ACK/cleanup models ONLY: extract a closed helper set. Never
# execute the gate body, bootstrap, Initialize, Add-Type, native runtime, child
# process, Docker, Sandbox, or an on-disk fixture. Filesystem ancestry is explicitly
# replaced by a lexical path stub below; actual native ownership is NOT tested.
$expectedCases = 94
$script:bootstrapCases = 0
$script:bootstrapPassed = 0
$script:bootstrapFailures = [Collections.Generic.List[string]]::new()
$script:disposeOrder = [Collections.Generic.List[string]]::new()
$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    [IO.Path]::GetFullPath($GatePath), [ref]$tokens, [ref]$parseErrors
)
$helperNames = @('Assert-ExactProperties', 'Assert-NativeJsonObject',
    'Assert-NativeJsonString', 'Assert-NativeJsonBoolean', 'Assert-NativeJsonInteger',
    'ConvertTo-RequiredUtcTime', 'ConvertTo-CanonicalUtcTime',
    'Assert-ParentStageProperties', 'Assert-ParentStageNoAuthorization',
    'Assert-ParentStageProcessIdentity', 'Assert-ParentStagePreparedLease',
    'Close-ParentStageBoundary', 'Assert-ParentStageTransitionAck')
$script:helperAsts = [Collections.Generic.List[object]]::new()

function Assert-BootstrapTest {
    param([bool] $Condition, [string] $Message)
    if (-not $Condition) { throw $Message }
}

function Invoke-BootstrapCase {
    param([string] $Name, [scriptblock] $Action)
    $script:bootstrapCases++
    try {
        & $Action
        $script:bootstrapPassed++
        Write-Output ('PASS bootstrap managed model: ' + $Name)
    }
    catch {
        $script:bootstrapFailures.Add($Name + ': ' + $_.Exception.GetBaseException().Message)
        Write-Output ('FAIL bootstrap managed model: ' + $Name)
    }
}

function Assert-BootstrapRejected {
    param([scriptblock] $Action, [string] $Pattern)
    $failure = $null
    try { & $Action | Out-Null }
    catch { $failure = $_.Exception.GetBaseException().Message }
    Assert-BootstrapTest ($null -ne $failure) 'An invalid managed fixture was accepted.'
    Assert-BootstrapTest ($failure -like $Pattern) ('Unexpected rejection boundary: ' + $failure)
}

Invoke-BootstrapCase 'only the closed reviewed helper ASTs are selected' {
    Assert-BootstrapTest (@($parseErrors).Count -eq 0) 'Gate source does not parse.'
    foreach ($name in $helperNames) {
        $definitions = @($ast.FindAll({ param($node)
            $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
                $node.Name -ceq $name
        }, $false))
        Assert-BootstrapTest ($definitions.Count -eq 1) ('Missing or duplicate helper: ' + $name)
        $script:helperAsts.Add($definitions[0])
    }
    Assert-BootstrapTest ($script:helperAsts.Count -eq 13) 'The extraction set changed.'
}

Invoke-BootstrapCase 'selected helpers have no IO native launch or dynamic command surface' {
    $allowedCommands = $helperNames + @('Assert-ParentStageCanonicalPath', 'ForEach-Object')
    $allowedStaticTypes = @('Array', 'StringComparer', 'string', 'bool', 'DateTimeOffset',
        'Globalization.CultureInfo', 'Globalization.DateTimeStyles', 'int')
    $allowedMembers = @('Sort', 'GetType', 'ToString', 'TryParse', 'ToUniversalTime',
        'AddSeconds', 'Subtract', 'Dispose', 'Clear')
    foreach ($definition in $script:helperAsts) {
        foreach ($command in @($definition.FindAll({ param($node)
            $node -is [Management.Automation.Language.CommandAst]
        }, $true))) {
            Assert-BootstrapTest ($command.GetCommandName() -cin $allowedCommands -and
                $command.InvocationOperator -eq [Management.Automation.Language.TokenKind]::Unknown) `
                ('Unexpected command in extracted source: ' + $command.Extent.Text)
        }
        foreach ($type in @($definition.FindAll({ param($node)
            $node -is [Management.Automation.Language.TypeExpressionAst]
        }, $true))) {
            Assert-BootstrapTest ($type.TypeName.FullName -cin $allowedStaticTypes) `
                ('Unexpected static type: ' + $type.TypeName.FullName)
        }
        foreach ($call in @($definition.FindAll({ param($node)
            $node -is [Management.Automation.Language.InvokeMemberExpressionAst]
        }, $true))) {
            Assert-BootstrapTest ($call.Member -is [Management.Automation.Language.StringConstantExpressionAst] -and
                $call.Member.Value -cin $allowedMembers) ('Unexpected method surface: ' + $call.Extent.Text)
            if ($call.Member.Value -cin @('Dispose', 'Clear')) {
                Assert-BootstrapTest ($definition.Name -ceq 'Close-ParentStageBoundary') `
                    'Only the cleanup helper may dispose the managed fake resources.'
            }
        }
    }
}

if ($script:bootstrapFailures.Count -ne 0) {
    $global:LASTEXITCODE = 1
    throw ('Bootstrap source contract rejected before helper execution: ' + ($script:bootstrapFailures -join '; '))
}
foreach ($definition in $script:helperAsts) {
    . ([scriptblock]::Create($definition.Extent.Text))
}

# Conscious stub: valid synthetic absolute path strings need no real executable.
# This preserves lexical rejection but provides NO ancestry, reparse-point,
# executable existence, file pin, process identity, or ownership evidence.
function Assert-ParentStageCanonicalPath {
    param([string] $Path, [switch] $MayBeAbsent)
    if ([string]::IsNullOrWhiteSpace($Path) -or -not [IO.Path]::IsPathRooted($Path) -or
        $Path -cne [IO.Path]::GetFullPath($Path)) {
        throw 'Managed lexical path stub requires a canonical absolute path.'
    }
}

function New-BootstrapFakeHandle {
    param([string] $Name, [bool] $Throws = $false)
    $handle = [pscustomobject]@{ Name = $Name; DisposeCount = 0; Throws = $Throws }
    $handle | Add-Member -MemberType ScriptMethod -Name Dispose -Value {
        $this.DisposeCount++
        $script:disposeOrder.Add($this.Name)
        if ($this.Throws) { throw ('Synthetic dispose failure: ' + $this.Name) }
    }
    return $handle
}

function New-BootstrapFixture {
    $now = [DateTimeOffset]::UtcNow
    $birth = $now.AddSeconds(-20)
    $armed = $now.AddSeconds(-10)
    $deadline = $now.AddMinutes(10)
    $owner = [pscustomobject]@{
        pid = [int]1234; start_time_utc = $now.AddMinutes(-1).ToString('o')
        executable = 'C:\stratus-managed-model\powershell.exe'; sha256 = ('a' * 64)
    }
    $anchor = [pscustomobject]@{
        directory = 'C:\stratus-managed-model\r1'; acceptance_sha256 = ('b' * 64)
        evidence_manifest_sha256 = ('c' * 64); sealed_sha256 = ('d' * 64)
    }
    $contract = [pscustomobject]@{
        session_id = ('e' * 32); owner = $owner
        watchdog = [pscustomobject]@{ host_path = $owner.executable }
        artifacts = [pscustomobject]@{ mutation_fence = 'C:\stratus-managed-model\fence.json' }
        r1 = $anchor; deadline_utc = $deadline.ToString('o')
    }
    $script:ParentStageContractPath = 'C:\stratus-managed-model\contract.json'
    $script:ParentStageContractSha256 = ('f' * 64)
    $launch = New-BootstrapFakeHandle 'launch'
    $launch | Add-Member -NotePropertyName ProcessId -NotePropertyValue ([int]2345)
    $launch | Add-Member -NotePropertyName StartedAt -NotePropertyValue $birth.ToString('o')
    $script:parentStage = [pscustomobject]@{
        contract = $contract; launch = $launch; resume_attempted = $false
        source_manifest_sha256 = ('0' * 64); deadline = $deadline
        armed_at = [DateTimeOffset]::MinValue
        fence = (New-BootstrapFakeHandle 'fence')
        heartbeat = (New-BootstrapFakeHandle 'heartbeat')
    }
    # List[object] deliberately holds fake ScriptMethod disposables, not native
    # IDisposable capabilities. Cleanup tests prove branch/lifetime decisions only.
    $script:parentStageOwnedHandles = [Collections.Generic.List[object]]::new()
    $script:parentStageOwnedHandles.Add((New-BootstrapFakeHandle 'source'))
    $script:parentStageOwnedHandles.Add((New-BootstrapFakeHandle 'hba'))
    $script:disposeOrder.Clear()
    $authorization = [pscustomobject]@{
        postgres_start_permitted = $false; api_start_permitted = $false
        worker_start_permitted = $false; safety_control_start_permitted = $false
        live_transition_permitted = $false; binance_kill_switch_removal_permitted = $false
        migration_container_permitted = $false; container_remove_permitted = $false
    }
    # No shared mutable owner/R1 instances: every alteration must traverse the
    # real equality check against a distinct, unchanged contract projection.
    return [pscustomobject]@{ value = [pscustomobject]@{
        format = 'stratus-parent-lease-v2'; session_id = $contract.session_id
        contract_path = $script:ParentStageContractPath; contract_sha256 = $script:ParentStageContractSha256
        watchdog = [pscustomobject]@{ pid = [int]2345; start_time_utc = $birth.ToString('o'); executable = $owner.executable }
        owner = [pscustomobject]@{ pid = $owner.pid; start_time_utc = $owner.start_time_utc; executable = $owner.executable; sha256 = $owner.sha256 }
        mutation_fence_path = $contract.artifacts.mutation_fence; mutation_fence_sha256 = ('1' * 64)
        r1_anchor = [pscustomobject]@{ directory = $anchor.directory; acceptance_sha256 = $anchor.acceptance_sha256;
            evidence_manifest_sha256 = $anchor.evidence_manifest_sha256; sealed_sha256 = $anchor.sealed_sha256 }
        source_manifest_sha256 = $script:parentStage.source_manifest_sha256
        armed_at = $armed.ToString('o'); deadline_utc = $deadline.ToString('o'); authorization = $authorization
    } }
}

function Invoke-LeaseRejection {
    param([string] $Name, [scriptblock] $Mutate, [string] $Pattern)
    Invoke-BootstrapCase $Name {
        $document = New-BootstrapFixture
        & $Mutate $document.value
        Assert-BootstrapRejected { Assert-ParentStagePreparedLease $document } $Pattern
        Assert-BootstrapTest ($script:parentStage.armed_at -eq [DateTimeOffset]::MinValue) `
            'Rejected lease advanced the accepted armed time.'
    }
}

Invoke-BootstrapCase 'valid prepared lease records armed time only' {
    $document = New-BootstrapFixture
    Assert-ParentStagePreparedLease $document
    Assert-BootstrapTest ($script:parentStage.armed_at.ToString('o') -ceq $document.value.armed_at) 'Valid armed time was not recorded.'
    Assert-BootstrapTest ($script:disposeOrder.Count -eq 0) 'Lease validation disposed a resource.'
}
Invoke-BootstrapCase 'native Int64 process IDs retain valid identity' {
    $document = New-BootstrapFixture
    $document.value.owner.pid = [long]1234
    $document.value.watchdog.pid = [long]2345
    Assert-ParentStagePreparedLease $document
}
Invoke-BootstrapCase 'exact two-second pre-birth skew is accepted' {
    $document = New-BootstrapFixture
    $document.value.armed_at = ([DateTimeOffset]::Parse($script:parentStage.launch.StartedAt)).AddSeconds(-2).ToString('o')
    Assert-ParentStagePreparedLease $document
}
Invoke-BootstrapCase 'exact 1800-second lifetime is accepted' {
    $document = New-BootstrapFixture
    $armed = [DateTimeOffset]::Parse($document.value.armed_at)
    $script:parentStage.deadline = $armed.AddSeconds(1800)
    $script:parentStage.contract.deadline_utc = $script:parentStage.deadline.ToString('o')
    $document.value.deadline_utc = $script:parentStage.contract.deadline_utc
    Assert-ParentStagePreparedLease $document
}
foreach ($rootKind in @('null', 'scalar', 'array')) {
    Invoke-BootstrapCase ('lease root rejects native ' + $rootKind) {
        $document = New-BootstrapFixture
        switch ($rootKind) {
            'null' { $document.value = $null }
            'scalar' { $document.value = 'stratus-parent-lease-v2' }
            'array' { $document.value = @($document.value, $document.value) }
        }
        Assert-BootstrapRejected { Assert-ParentStagePreparedLease $document } '*native JSON object*'
        Assert-BootstrapTest ($script:parentStage.armed_at -eq [DateTimeOffset]::MinValue) `
            'Invalid root advanced the accepted armed time.'
    }
}

$bindingPattern = '*not bound to the invocation-owned launch and accepted R1*'
Invoke-LeaseRejection 'wrong lease version' { param($v) $v.format = 'stratus-parent-lease-v1' } $bindingPattern
Invoke-LeaseRejection 'wrong session' { param($v) $v.session_id = ('9' * 32) } $bindingPattern
Invoke-LeaseRejection 'wrong contract path' { param($v) $v.contract_path = 'C:\stratus-managed-model\other.json' } $bindingPattern
Invoke-LeaseRejection 'wrong contract hash' { param($v) $v.contract_sha256 = ('9' * 64) } $bindingPattern
Invoke-LeaseRejection 'wrong launch PID' { param($v) $v.watchdog.pid = [int]3456 } $bindingPattern
Invoke-LeaseRejection 'wrong launch birth' { param($v) $v.watchdog.start_time_utc = ([DateTimeOffset]::Parse($v.watchdog.start_time_utc)).AddTicks(1).ToString('o') } $bindingPattern
Invoke-LeaseRejection 'wrong launch executable' { param($v) $v.watchdog.executable = 'C:\stratus-managed-model\pwsh.exe' } $bindingPattern
Invoke-LeaseRejection 'wrong mutation-fence path' { param($v) $v.mutation_fence_path = 'C:\stratus-managed-model\other.json' } $bindingPattern
Invoke-LeaseRejection 'uppercase mutation-fence hash' { param($v) $v.mutation_fence_sha256 = ('A' * 64) } $bindingPattern
Invoke-LeaseRejection 'short mutation-fence hash' { param($v) $v.mutation_fence_sha256 = ('1' * 63) } $bindingPattern
Invoke-LeaseRejection 'wrong source manifest' { param($v) $v.source_manifest_sha256 = ('9' * 64) } $bindingPattern
Invoke-LeaseRejection 'wrong deadline binding' { param($v) $v.deadline_utc = ([DateTimeOffset]::Parse($v.deadline_utc)).AddTicks(1).ToString('o') } $bindingPattern
foreach ($field in @('pid', 'start_time_utc', 'executable', 'sha256')) {
    Invoke-LeaseRejection ('wrong owner ' + $field) {
        param($v)
        switch ($field) {
            'pid' { $v.owner.pid = [int]3456 }
            'start_time_utc' { $v.owner.start_time_utc = ([DateTimeOffset]::Parse($v.owner.start_time_utc)).AddTicks(1).ToString('o') }
            'executable' { $v.owner.executable = 'C:\stratus-managed-model\other.exe' }
            'sha256' { $v.owner.sha256 = ('9' * 64) }
        }
    } '*Prepared lease owner differs*'
}
foreach ($field in @('directory', 'acceptance_sha256', 'evidence_manifest_sha256', 'sealed_sha256')) {
    Invoke-LeaseRejection ('wrong R1 ' + $field) { param($v) $v.r1_anchor.$field = 'different' } '*Prepared lease R1 differs*'
}
Invoke-LeaseRejection 'missing required lease field' { param($v) $v.PSObject.Properties.Remove('format') } '*exact required property set*'
Invoke-LeaseRejection 'extra lease field' { param($v) $v | Add-Member -NotePropertyName extra -NotePropertyValue $false } '*exact required property set*'
Invoke-LeaseRejection 'lease hash must be native string' { param($v) $v.contract_sha256 = 123 } '*native JSON string*'
Invoke-LeaseRejection 'deadline must be native string' { param($v) $v.deadline_utc = [DateTimeOffset]::UtcNow } '*native JSON string*'
Invoke-LeaseRejection 'watchdog PID string rejected' { param($v) $v.watchdog.pid = '2345' } '*native JSON integer*'
Invoke-LeaseRejection 'watchdog PID double rejected' { param($v) $v.watchdog.pid = [double]2345 } '*native JSON integer*'
Invoke-LeaseRejection 'owner PID boolean rejected' { param($v) $v.owner.pid = $true } '*native JSON integer*'
Invoke-LeaseRejection 'zero watchdog PID rejected' { param($v) $v.watchdog.pid = 0 } '*process PID is invalid*'
Invoke-LeaseRejection 'oversized watchdog PID rejected' { param($v) $v.watchdog.pid = [long]2147483648 } '*process PID is invalid*'
Invoke-LeaseRejection 'process date requires canonical form' { param($v) $v.watchdog.start_time_utc = '2026-09-14T12:00:00Z' } '*canonical UTC DateTimeOffset*'
Invoke-LeaseRejection 'R1 field must be native string' { param($v) $v.r1_anchor.acceptance_sha256 = $null } '*native JSON string*'
Invoke-LeaseRejection 'extra R1 field rejected' { param($v) $v.r1_anchor | Add-Member -NotePropertyName extra -NotePropertyValue $false } '*exact required property set*'
Invoke-LeaseRejection 'authorization must be native boolean' { param($v) $v.authorization.api_start_permitted = 'false' } '*native JSON boolean*'
foreach ($field in @('postgres_start_permitted', 'api_start_permitted', 'worker_start_permitted',
    'safety_control_start_permitted', 'live_transition_permitted', 'binance_kill_switch_removal_permitted',
    'migration_container_permitted', 'container_remove_permitted')) {
    Invoke-LeaseRejection ('authority remains forbidden: ' + $field) { param($v) $v.authorization.$field = $true } '*must not grant lifecycle authority*'
}
Invoke-LeaseRejection 'one tick beyond pre-birth skew rejected' {
    param($v) $v.armed_at = ([DateTimeOffset]::Parse($script:parentStage.launch.StartedAt)).AddSeconds(-2).AddTicks(-1).ToString('o')
} '*Prepared lease chronology differs*'
Invoke-LeaseRejection 'future arming beyond clock skew rejected' { param($v) $v.armed_at = [DateTimeOffset]::UtcNow.AddSeconds(60).ToString('o') } '*Prepared lease chronology differs*'
Invoke-LeaseRejection 'arming at deadline rejected' {
    param($v)
    $script:parentStage.deadline = [DateTimeOffset]::Parse($v.armed_at)
    $script:parentStage.contract.deadline_utc = $v.armed_at
    $v.deadline_utc = $v.armed_at
} '*Prepared lease chronology differs*'
Invoke-LeaseRejection 'one tick beyond maximum lifetime rejected' {
    param($v)
    $script:parentStage.deadline = ([DateTimeOffset]::Parse($v.armed_at)).AddSeconds(1800).AddTicks(1)
    $script:parentStage.contract.deadline_utc = $script:parentStage.deadline.ToString('o')
    $v.deadline_utc = $script:parentStage.contract.deadline_utc
} '*Prepared lease chronology differs*'
Invoke-LeaseRejection 'noncanonical arming timestamp rejected' { param($v) $v.armed_at = '2026-09-14T12:00:00Z' } '*canonical UTC DateTimeOffset*'

Invoke-BootstrapCase 'resumed cleanup retains launch fence heartbeat and evidence' {
    $null = New-BootstrapFixture
    $script:parentStage.resume_attempted = $true
    $stateBefore = $script:parentStage
    $handlesBefore = $script:parentStageOwnedHandles
    Assert-BootstrapRejected { Close-ParentStageBoundary } '*Resumed parent session retained*'
    Assert-BootstrapTest ([object]::ReferenceEquals($stateBefore, $script:parentStage) -and
        [object]::ReferenceEquals($handlesBefore, $script:parentStageOwnedHandles)) 'Cleanup replaced owned state.'
    Assert-BootstrapTest ($script:disposeOrder.Count -eq 0 -and $script:parentStageOwnedHandles.Count -eq 2 -and
        $stateBefore.launch.DisposeCount -eq 0 -and $stateBefore.fence.DisposeCount -eq 0 -and
        $stateBefore.heartbeat.DisposeCount -eq 0) 'Cleanup disposed a resumed resource.'
}
Invoke-BootstrapCase 'repeated resumed cleanup cannot erode retained evidence' {
    $null = New-BootstrapFixture
    $script:parentStage.resume_attempted = $true
    foreach ($attempt in 1..3) { Assert-BootstrapRejected { Close-ParentStageBoundary } '*Resumed parent session retained*' }
    Assert-BootstrapTest ($script:disposeOrder.Count -eq 0 -and $script:parentStageOwnedHandles.Count -eq 2) 'Repeated cleanup eroded retention.'
}
Invoke-BootstrapCase 'ambiguous resume latch blocks cleanup even without launch object' {
    $null = New-BootstrapFixture
    $script:parentStage.resume_attempted = $true
    $script:parentStage.launch = $null
    Assert-BootstrapRejected { Close-ParentStageBoundary } '*Resumed parent session retained*'
    Assert-BootstrapTest ($script:disposeOrder.Count -eq 0 -and $script:parentStageOwnedHandles.Count -eq 2) 'Resume latch was bypassed.'
}
Invoke-BootstrapCase 'never-resumed launch disposes before reverse-order evidence' {
    $null = New-BootstrapFixture
    $launch = $script:parentStage.launch
    Close-ParentStageBoundary
    Assert-BootstrapTest (($script:disposeOrder -join ',') -ceq 'launch,hba,source' -and
        $launch.DisposeCount -eq 1 -and $script:parentStageOwnedHandles.Count -eq 0) 'Never-resumed disposal order differs.'
}
Invoke-BootstrapCase 'failed launch disposal preserves all evidence handles' {
    $null = New-BootstrapFixture
    $script:parentStage.launch.Throws = $true
    Assert-BootstrapRejected { Close-ParentStageBoundary } '*Synthetic dispose failure: launch*'
    Assert-BootstrapTest (($script:disposeOrder -join ',') -ceq 'launch' -and
        $script:parentStageOwnedHandles.Count -eq 2) 'Cleanup discarded evidence after launch disposal failure.'
}
Invoke-BootstrapCase 'failed evidence disposal stops without clearing retained list' {
    $null = New-BootstrapFixture
    $script:parentStageOwnedHandles[1].Throws = $true
    Assert-BootstrapRejected { Close-ParentStageBoundary } '*Synthetic dispose failure: hba*'
    Assert-BootstrapTest (($script:disposeOrder -join ',') -ceq 'launch,hba' -and
        $script:parentStageOwnedHandles.Count -eq 2 -and $script:parentStageOwnedHandles[0].DisposeCount -eq 0) `
        'Cleanup advanced past evidence disposal failure.'
}
Invoke-BootstrapCase 'pre-launch cleanup releases reverse-order evidence without launch' {
    $null = New-BootstrapFixture
    $script:parentStage.launch = $null
    Close-ParentStageBoundary
    Assert-BootstrapTest (($script:disposeOrder -join ',') -ceq 'hba,source' -and
        $script:parentStageOwnedHandles.Count -eq 0) 'Pre-launch evidence cleanup differs.'
}
Invoke-BootstrapCase 'runtime initialization failure can release evidence before state exists' {
    $null = New-BootstrapFixture
    $script:parentStage = $null
    Close-ParentStageBoundary
    Assert-BootstrapTest (($script:disposeOrder -join ',') -ceq 'hba,source' -and
        $script:parentStageOwnedHandles.Count -eq 0) 'Initialization failure cleanup differs.'
}

function New-TransitionAckFixture {
    $null = New-BootstrapFixture
    $now = [DateTimeOffset]::UtcNow
    $script:ackPublished = $now.AddSeconds(-2)
    $script:ackRegistrationHash = '2' * 64
    $script:ackPostgresStart = '2026-09-14T12:00:00.123456789Z'
    $script:parentStage | Add-Member -NotePropertyName lease_sha256 -NotePropertyValue ('3' * 64)
    $script:parentStage.contract.artifacts | Add-Member -NotePropertyName transition_registration `
        -NotePropertyValue 'C:\stratus-managed-model\transition-registration.json'
    return [pscustomobject]@{ value = [pscustomobject]@{
        format = 'stratus-parent-ack-v1'; session_id = $script:parentStage.contract.session_id
        lease_sha256 = $script:parentStage.lease_sha256; acknowledged_at = $now.AddSeconds(-1).ToString('o')
        watchdog_pid = $script:parentStage.launch.ProcessId; watchdog_start_time_utc = $script:parentStage.launch.StartedAt
        source_path = $script:parentStage.contract.artifacts.transition_registration; source_sha256 = $script:ackRegistrationHash
        boundary = [pscustomobject]@{ postgres_started_at = $script:ackPostgresStart }
        authorization = [pscustomobject]@{
            postgres_start_permitted = $false; api_start_permitted = $false; worker_start_permitted = $false
            safety_control_start_permitted = $false; live_transition_permitted = $false
            binance_kill_switch_removal_permitted = $false; migration_container_permitted = $false; container_remove_permitted = $false
        }
    } }
}
function Invoke-TransitionAckRejection {
    param([string] $Name, [scriptblock] $Mutate, [string] $Pattern)
    Invoke-BootstrapCase ('transition ack ' + $Name) {
        $document = New-TransitionAckFixture
        & $Mutate $document.value
        Assert-BootstrapRejected {
            Assert-ParentStageTransitionAck $document $script:ackRegistrationHash $script:ackPublished $script:ackPostgresStart
        } $Pattern
        Assert-BootstrapTest ($script:disposeOrder.Count -eq 0) 'Ack validation disposed a resource.'
    }
}
Invoke-BootstrapCase 'transition ack exact launch and publication accepted without authority' {
    $document = New-TransitionAckFixture
    Assert-ParentStageTransitionAck $document $script:ackRegistrationHash $script:ackPublished $script:ackPostgresStart
    Assert-BootstrapTest ($script:disposeOrder.Count -eq 0) 'Ack validation disposed a resource.'
}
Invoke-TransitionAckRejection 'unknown field' { param($v) $v | Add-Member -NotePropertyName extra -NotePropertyValue 1 } '*exact required property set*'
foreach ($field in @('format', 'session_id', 'lease_sha256', 'acknowledged_at', 'watchdog_start_time_utc', 'source_path', 'source_sha256')) {
    Invoke-TransitionAckRejection ('native string required for ' + $field) { param($v) $v.$field = 1 } '*native JSON string*'
}
$ackBindingPattern = '*not exact, fresh and launch-bound*'
foreach ($field in @('format', 'session_id', 'lease_sha256', 'source_path', 'source_sha256', 'postgres_started_at')) {
    Invoke-TransitionAckRejection ('binding mismatch ' + $field) {
        param($v)
        if ($field -ceq 'postgres_started_at') { $v.boundary.postgres_started_at = 'different' }
        else { $v.$field = 'different' }
    } $ackBindingPattern
}
Invoke-TransitionAckRejection 'wrong held PID' { param($v) $v.watchdog_pid = 9999 } $ackBindingPattern
Invoke-TransitionAckRejection 'wrong held birth' { param($v) $v.watchdog_start_time_utc = 'different' } $ackBindingPattern
Invoke-TransitionAckRejection 'PID string' { param($v) $v.watchdog_pid = '2345' } '*native JSON integer*'
Invoke-TransitionAckRejection 'extra boundary field' { param($v) $v.boundary | Add-Member -NotePropertyName extra -NotePropertyValue 1 } '*exact required property set*'
foreach ($field in @('postgres_start_permitted', 'api_start_permitted', 'worker_start_permitted',
    'safety_control_start_permitted', 'live_transition_permitted', 'binance_kill_switch_removal_permitted',
    'migration_container_permitted', 'container_remove_permitted')) {
    Invoke-TransitionAckRejection ('forbidden authority ' + $field) { param($v) $v.authorization.$field = $true } '*must not grant lifecycle authority*'
}
Invoke-TransitionAckRejection 'nonboolean authority' { param($v) $v.authorization.api_start_permitted = 'false' } '*native JSON boolean*'
Invoke-TransitionAckRejection 'before own publication' { param($v) $v.acknowledged_at = $script:ackPublished.AddTicks(-1).ToString('o') } $ackBindingPattern
Invoke-TransitionAckRejection 'at hard deadline' { param($v) $v.acknowledged_at = $script:parentStage.deadline.ToString('o') } $ackBindingPattern
Invoke-TransitionAckRejection 'beyond future skew' { param($v) $v.acknowledged_at = [DateTimeOffset]::UtcNow.AddSeconds(60).ToString('o') } $ackBindingPattern

if ($script:bootstrapCases -ne $expectedCases) {
    $script:bootstrapFailures.Add(('Expected ' + $expectedCases + ' managed cases; observed ' + $script:bootstrapCases + '.'))
}
[pscustomobject]@{
    scope = 'extracted prepared-lease/transition-ACK validation and fake-resource cleanup managed models'
    passed = $script:bootstrapPassed; total = $script:bootstrapCases; expected = $expectedCases
    failed = $script:bootstrapFailures.Count; failures = @($script:bootstrapFailures)
    gate_body_executed = $false; bootstrap_executed = $false; native_runtime_executed = $false
    filesystem_ancestry_verified = $false; process_ownership_verified = $false
    production_authorized = $false; full_parent_protocol_accepted = $false
} | ConvertTo-Json -Depth 5 -Compress
if ($script:bootstrapFailures.Count -ne 0) {
    $global:LASTEXITCODE = 1
    throw ('Bootstrap managed-model regressions failed: ' + ($script:bootstrapFailures -join '; '))
}
$global:LASTEXITCODE = 0
