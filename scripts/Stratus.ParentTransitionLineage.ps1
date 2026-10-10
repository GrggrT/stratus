# Pure, non-authorizing PostgreSQL start-lineage leaf checks. This library is not
# connected to the parent watchdog, gate, bootstrap, or validatedCompletions.
#
# Documents are a leaf projection { path, sha256, value }, not drop-in outputs of
# the gate reader (path/bytes/sha256/last_write_time_utc/json/value) or successor
# reader (bytes/sha256/value). A future trusted caller must project the exact
# reader result and separately established canonical path without inventing trust.
# Strict raw JSON reading and byte hashing remain outside this leaf and unverified
# by it. This leaf compares supplied digest metadata with explicit expected digests;
# it does NOT parse raw JSON, recompute byte hashes,
# inspect filesystem ancestry, acquire handles, or establish publication ownership.
# A supplied Expected.parent_stage is data, not a trusted runtime capability.
# Authorization/terminal/live linkage, held sources, R1 ownership, exact launch
# provenance, live fence/heartbeat ownership, and durable rereads remain required
# before any later integration may populate a completion cache or grant authority.

function Assert-StratusLineageObject {
    param([object] $Value, [string[]] $Fields, [string] $Label)
    if ($null -eq $Value -or $Value.GetType().FullName -cne 'System.Management.Automation.PSCustomObject') {
        throw "$Label must be a native JSON object."
    }
    $properties = @($Value.PSObject.Properties)
    if ($properties.Count -ne $Fields.Count) { throw "$Label property set differs." }
    foreach ($property in $properties) {
        if ($property.MemberType -ne [Management.Automation.PSMemberTypes]::NoteProperty -or
            $property.Name -cnotin $Fields) { throw "$Label property set differs." }
    }
}

function Assert-StratusLineageString {
    param([object] $Value, [string] $Label)
    if ($Value -isnot [string]) { throw "$Label must be a native JSON string." }
}

function Assert-StratusLineageInteger {
    param([object] $Value, [string] $Label)
    if ($Value -isnot [int] -and $Value -isnot [long]) { throw "$Label must be a native JSON integer." }
}

function Assert-StratusLineageHash {
    param([object] $Value, [string] $Label)
    Assert-StratusLineageString $Value $Label
    if (-not [regex]::IsMatch($Value, '\A[0-9a-f]{64}\z')) { throw "$Label must be an exact lowercase SHA256." }
}

function Assert-StratusLineagePath {
    param([object] $Value, [string] $Label)
    Assert-StratusLineageString $Value $Label
    # GetFullPath performs string normalization only; no file or directory is read.
    if ([string]::IsNullOrEmpty($Value) -or [IO.Path]::GetFullPath($Value) -cne $Value) {
        throw "$Label must be a canonical absolute path."
    }
}

function Get-StratusLineageUtc {
    param([object] $Value, [string] $Label)
    Assert-StratusLineageString $Value $Label
    $time = [DateTimeOffset]::ParseExact($Value, 'o', [Globalization.CultureInfo]::InvariantCulture)
    if ($time.Offset -ne [TimeSpan]::Zero -or $time.ToString('o') -cne $Value) {
        throw "$Label must be a canonical UTC timestamp."
    }
    return $time
}

function Assert-StratusLineageProcess {
    param([object] $Value, [string] $Label)
    Assert-StratusLineageObject $Value @('pid', 'start_time_utc', 'executable', 'sha256') $Label
    Assert-StratusLineageInteger $Value.pid "$Label PID"
    if ($Value.pid -le 0 -or $Value.pid -gt [int]::MaxValue) { throw "$Label PID is outside the process range." }
    [void](Get-StratusLineageUtc $Value.start_time_utc "$Label creation time")
    Assert-StratusLineagePath $Value.executable "$Label executable"
    Assert-StratusLineageHash $Value.sha256 "$Label executable hash"
}

function Assert-StratusLineageDocument {
    param([object] $Document, [string] $Path, [string] $Sha256, [string] $Label)
    Assert-StratusLineageObject $Document @('path', 'sha256', 'value') $Label
    Assert-StratusLineagePath $Document.path "$Label path"
    Assert-StratusLineageHash $Document.sha256 "$Label hash"
    if ($Document.path -cne $Path -or $Document.sha256 -cne $Sha256) {
        throw "$Label does not match the explicit expected path/hash."
    }
}

function Assert-StratusParentTransitionLineageLeaf {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][AllowNull()][object] $FenceDocument,
        [Parameter(Mandatory = $true)][AllowNull()][object] $AttemptDocument,
        [Parameter(Mandatory = $true)][AllowNull()][object] $CompletionDocument,
        [Parameter(Mandatory = $true)][AllowNull()][object] $Expected
    )

    Assert-StratusLineageObject $Expected @('parent_stage', 'parent_armed_at',
        'transition_session_id', 'transition_armed_at', 'transition_deadline_utc', 'observed_at',
        'start_fence_path', 'start_fence_sha256', 'start_attempt_path', 'start_attempt_sha256',
        'start_completion_path', 'start_completion_sha256', 'postgres_container_id',
        'docker_executable_path', 'docker_executable_sha256') 'expected leaf boundary'
    $binding = $Expected.parent_stage
    Assert-StratusLineageObject $binding @('format', 'session_id', 'contract_path', 'contract_sha256',
        'lease_path', 'lease_sha256', 'ready_path', 'ready_sha256', 'heartbeat_path',
        'mutation_fence_path', 'mutation_fence_sha256', 'watchdog', 'owner', 'deadline_utc',
        'r1_anchor', 'source_manifest_sha256') 'expected parent-stage binding'
    Assert-StratusLineageString $binding.format 'parent-stage format'
    Assert-StratusLineageString $binding.session_id 'parent-stage session'
    Assert-StratusLineageString $Expected.transition_session_id 'transition session'
    if ($binding.format -cne 'stratus-parent-stage-binding-v1' -or
        -not [regex]::IsMatch($binding.session_id, '\A[0-9a-f]{32}\z') -or
        -not [regex]::IsMatch($Expected.transition_session_id, '\A[0-9a-f]{32}\z')) {
        throw 'Expected parent/transition session identity differs.'
    }
    foreach ($name in @('contract_path', 'lease_path', 'ready_path', 'heartbeat_path', 'mutation_fence_path')) {
        Assert-StratusLineagePath $binding.$name "parent-stage $name"
    }
    foreach ($name in @('contract_sha256', 'lease_sha256', 'ready_sha256', 'mutation_fence_sha256', 'source_manifest_sha256')) {
        Assert-StratusLineageHash $binding.$name "parent-stage $name"
    }
    Assert-StratusLineageProcess $binding.owner 'expected parent owner'
    Assert-StratusLineageProcess $binding.watchdog 'expected parent watchdog'
    if ($binding.owner.pid -eq $binding.watchdog.pid) { throw 'Expected parent and watchdog must have distinct process identities.' }
    Assert-StratusLineageObject $binding.r1_anchor @('directory', 'acceptance_sha256', 'evidence_manifest_sha256', 'sealed_sha256') 'expected R1 anchor'
    Assert-StratusLineagePath $binding.r1_anchor.directory 'expected R1 directory'
    foreach ($name in @('acceptance_sha256', 'evidence_manifest_sha256', 'sealed_sha256')) {
        Assert-StratusLineageHash $binding.r1_anchor.$name "expected R1 $name"
    }
    foreach ($name in @('start_fence_path', 'start_attempt_path', 'start_completion_path', 'docker_executable_path')) {
        Assert-StratusLineagePath $Expected.$name "expected $name"
    }
    foreach ($name in @('start_fence_sha256', 'start_attempt_sha256', 'start_completion_sha256',
        'postgres_container_id', 'docker_executable_sha256')) {
        Assert-StratusLineageHash $Expected.$name "expected $name"
    }
    $distinct = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($path in @($binding.contract_path, $binding.lease_path, $binding.ready_path, $binding.heartbeat_path,
        $binding.mutation_fence_path, $Expected.start_fence_path, $Expected.start_attempt_path, $Expected.start_completion_path)) {
        if (-not $distinct.Add($path)) { throw 'Expected protocol paths alias another artifact.' }
    }
    $parentArmed = Get-StratusLineageUtc $Expected.parent_armed_at 'parent armed time'
    $parentDeadline = Get-StratusLineageUtc $binding.deadline_utc 'parent deadline'
    $armed = Get-StratusLineageUtc $Expected.transition_armed_at 'transition armed time'
    $deadline = Get-StratusLineageUtc $Expected.transition_deadline_utc 'transition deadline'
    $observed = Get-StratusLineageUtc $Expected.observed_at 'fixed observation time'
    $ownerBirth = Get-StratusLineageUtc $binding.owner.start_time_utc 'parent owner birth time'
    $watchdogBirth = Get-StratusLineageUtc $binding.watchdog.start_time_utc 'parent watchdog birth time'
    if ($parentArmed -gt $armed -or $armed -ge $deadline -or $deadline -ge $parentDeadline -or $observed -lt $armed) {
        throw 'Expected parent/transition lifetime ordering differs.'
    }
    if ($ownerBirth -gt $parentArmed.AddSeconds(2) -or $watchdogBirth -gt $parentArmed.AddSeconds(2)) {
        throw 'Expected owner/watchdog birth postdates parent arming.'
    }

    Assert-StratusLineageDocument $FenceDocument $Expected.start_fence_path $Expected.start_fence_sha256 'start fence'
    Assert-StratusLineageDocument $AttemptDocument $Expected.start_attempt_path $Expected.start_attempt_sha256 'start attempt'
    Assert-StratusLineageDocument $CompletionDocument $Expected.start_completion_path $Expected.start_completion_sha256 'start completion'
    $fence = $FenceDocument.value
    $attempt = $AttemptDocument.value
    $completion = $CompletionDocument.value
    Assert-StratusLineageObject $fence @('format', 'session_id', 'owner_pid', 'owner_start_time_utc', 'created_at') 'start fence value'
    Assert-StratusLineageObject $attempt @('format', 'session_id', 'owner_pid', 'owner_start_time_utc',
        'start_fence_path', 'start_fence_sha256', 'command', 'issued_at') 'start attempt value'
    Assert-StratusLineageObject $completion @('format', 'session_id', 'owner_pid', 'owner_start_time_utc',
        'start_attempt_path', 'start_attempt_sha256', 'outcome', 'completed_at', 'bounded_start') 'start completion value'
    $documents = @($fence, $attempt, $completion)
    $formats = @('stratus-successor-transition-start-fence-v1',
        'stratus-successor-transition-start-attempt-v1', 'stratus-successor-transition-start-complete-v1')
    for ($index = 0; $index -lt $documents.Count; $index++) {
        $value = $documents[$index]
        foreach ($name in @('format', 'session_id', 'owner_start_time_utc')) {
            Assert-StratusLineageString $value.$name "start document $name"
        }
        Assert-StratusLineageInteger $value.owner_pid 'start document owner PID'
        [void](Get-StratusLineageUtc $value.owner_start_time_utc 'start document owner creation time')
        if ($value.format -cne $formats[$index] -or $value.session_id -cne $Expected.transition_session_id -or
            $value.owner_pid -ne $binding.owner.pid -or $value.owner_start_time_utc -cne $binding.owner.start_time_utc) {
            throw 'Start document is not bound to the explicit transition/owner identity.'
        }
    }
    Assert-StratusLineagePath $attempt.start_fence_path 'attempt fence path'
    Assert-StratusLineageHash $attempt.start_fence_sha256 'attempt fence hash'
    Assert-StratusLineagePath $completion.start_attempt_path 'completion attempt path'
    Assert-StratusLineageHash $completion.start_attempt_sha256 'completion attempt hash'
    Assert-StratusLineageString $completion.outcome 'completion outcome'
    if ($attempt.start_fence_path -cne $Expected.start_fence_path -or $attempt.start_fence_sha256 -cne $Expected.start_fence_sha256 -or
        $completion.start_attempt_path -cne $Expected.start_attempt_path -or $completion.start_attempt_sha256 -cne $Expected.start_attempt_sha256 -or
        $completion.outcome -cne 'started') { throw 'Start fence/attempt/completion linkage differs.' }

    $bounded = $completion.bounded_start
    Assert-StratusLineageObject $bounded @('command', 'docker_executable_path', 'docker_executable_sha256',
        'process_timeout_milliseconds', 'stdout_maximum_bytes', 'stderr_maximum_bytes', 'requested_at', 'completed_at',
        'exit_code', 'output_container_id', 'timed_out', 'output_exceeded', 'process_started', 'process_exited',
        'process_id', 'kill_issued') 'bounded start'
    foreach ($command in @($attempt.command, $bounded.command)) {
        if ($command -isnot [Array] -or $command.Rank -ne 1 -or $command.Count -ne 2) {
            throw 'Start command must be a two-token native JSON array.'
        }
        foreach ($token in $command) { Assert-StratusLineageString $token 'start command token' }
        if ($command[0] -cne 'start' -or $command[1] -cne $Expected.postgres_container_id) {
            throw 'Start command differs from the exact expected PostgreSQL start.'
        }
    }
    Assert-StratusLineagePath $bounded.docker_executable_path 'bounded Docker path'
    Assert-StratusLineageHash $bounded.docker_executable_sha256 'bounded Docker hash'
    Assert-StratusLineageHash $bounded.output_container_id 'bounded output container ID'
    foreach ($name in @('process_timeout_milliseconds', 'stdout_maximum_bytes', 'stderr_maximum_bytes', 'exit_code', 'process_id')) {
        Assert-StratusLineageInteger $bounded.$name "bounded start $name"
    }
    foreach ($name in @('timed_out', 'output_exceeded', 'process_started', 'process_exited', 'kill_issued')) {
        if ($bounded.$name -isnot [bool]) { throw "bounded start $name must be a native JSON boolean." }
    }
    if ($bounded.docker_executable_path -cne $Expected.docker_executable_path -or
        $bounded.docker_executable_sha256 -cne $Expected.docker_executable_sha256 -or
        $bounded.process_timeout_milliseconds -ne 15000 -or $bounded.stdout_maximum_bytes -ne 4096 -or
        $bounded.stderr_maximum_bytes -ne 4096 -or $bounded.exit_code -ne 0 -or
        $bounded.output_container_id -cne $Expected.postgres_container_id -or $bounded.timed_out -or $bounded.output_exceeded -or
        -not $bounded.process_started -or -not $bounded.process_exited -or $bounded.process_id -le 0 -or
        $bounded.process_id -gt [int]::MaxValue -or $bounded.kill_issued) {
        throw 'Bounded start is not the exact positive producer-shaped completion.'
    }
    $created = Get-StratusLineageUtc $fence.created_at 'fence creation time'
    $issued = Get-StratusLineageUtc $attempt.issued_at 'attempt issue time'
    $requested = Get-StratusLineageUtc $bounded.requested_at 'bounded request time'
    $completed = Get-StratusLineageUtc $completion.completed_at 'completion time'
    $boundedCompleted = Get-StratusLineageUtc $bounded.completed_at 'bounded completion time'
    # The gate publishes this fence after parent arming and before both the
    # successor lease and start attempt. Preserve the same two-second allowance;
    # reject impossible producer order without asserting runtime provenance.
    if ($created -lt $ownerBirth.AddSeconds(-2) -or $created -lt $parentArmed.AddSeconds(-2) -or
        $created -gt $armed.AddSeconds(2) -or $created -gt $issued.AddSeconds(2)) {
        throw 'Start-fence publication order differs from the producer.'
    }
    # Preserve the existing successor validator's two-second clock-skew allowance.
    if ($created -gt $deadline -or $created -gt $observed.AddSeconds(2) -or
        $issued -lt $armed.AddSeconds(-2) -or $issued -gt $deadline -or $issued -gt $observed.AddSeconds(2) -or
        $requested -lt $issued.AddSeconds(-2) -or $boundedCompleted -lt $requested -or
        $boundedCompleted -gt $deadline -or $boundedCompleted -gt $observed.AddSeconds(2) -or $completed -ne $boundedCompleted) {
        throw 'Start-lineage chronology differs from the existing producer contract.'
    }

    # Return only fresh scalar data. No supplied object becomes a capability.
    return [pscustomobject]@{
        format = 'stratus-parent-transition-lineage-leaf-check-v1'
        schema_only = $true
        parent_session_id = [string]$binding.session_id
        transition_session_id = [string]$Expected.transition_session_id
        start_attempt_sha256 = [string]$AttemptDocument.sha256
        start_completion_sha256 = [string]$CompletionDocument.sha256
        document_bytes_verified = $false
        provenance_verified = $false
        cache_use_permitted = $false
        full_lineage_accepted = $false
        authorization = [pscustomobject]@{
            postgres_start_permitted = $false; api_start_permitted = $false
            worker_start_permitted = $false; safety_control_start_permitted = $false
            live_transition_permitted = $false; binance_kill_switch_removal_permitted = $false
            migration_container_permitted = $false; container_remove_permitted = $false
        }
    }
}
