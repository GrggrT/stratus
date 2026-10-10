# Pure ten-document Transition closure checks. The trusted receiver must load
# Stratus.ParentTransitionLineage.ps1 first, strictly read/hash/hold original bytes,
# and supply independently established Expected facts. No I/O, native calls,
# process/container actions, clock reads, or authorization occur in this library.
# A successful projection proves document consistency only: it cannot substitute
# for the missing independent successor launch/exit capture or populate a causal
# completion cache. All returned permission/provenance flags remain false.

function Assert-StratusClosureRecord {
    param([object] $Value, [string] $Label, [string[]] $Strings = @(),
        [string[]] $Integers = @(), [string[]] $Booleans = @(),
        [string[]] $Objects = @(), [string[]] $Arrays = @(), [string[]] $Nulls = @())
    Assert-StratusLineageObject $Value @($Strings + $Integers + $Booleans + $Objects + $Arrays + $Nulls) $Label
    foreach ($name in $Strings) { Assert-StratusLineageString $Value.$name "$Label.$name" }
    foreach ($name in $Integers) { Assert-StratusLineageInteger $Value.$name "$Label.$name" }
    foreach ($name in $Booleans) {
        if ($Value.$name -isnot [bool]) { throw "$Label.$name must be a native JSON boolean." }
    }
    foreach ($name in $Objects) {
        if ($null -eq $Value.$name -or $Value.$name.GetType().FullName -cne 'System.Management.Automation.PSCustomObject') {
            throw "$Label.$name must be a native JSON object."
        }
    }
    foreach ($name in $Arrays) {
        if ($Value.$name -isnot [Array] -or $Value.$name.Rank -ne 1) { throw "$Label.$name must be a native JSON array." }
    }
    foreach ($name in $Nulls) { if ($null -ne $Value.$name) { throw "$Label.$name must be null." } }
}

function Assert-StratusClosureEqual {
    param([AllowNull()][object] $Actual, [AllowNull()][object] $Expected, [string] $Label)
    if ($null -eq $Actual -or $null -eq $Expected) {
        if ($null -ne $Actual -or $null -ne $Expected) { throw "$Label differs." }
        return
    }
    if ($Expected -is [Array]) {
        if ($Actual -isnot [Array] -or $Actual.Rank -ne 1 -or $Actual.Count -ne $Expected.Count) { throw "$Label array differs." }
        for ($index = 0; $index -lt $Expected.Count; $index++) {
            Assert-StratusClosureEqual $Actual[$index] $Expected[$index] "$Label[$index]"
        }
        return
    }
    if ($Expected.GetType().FullName -ceq 'System.Management.Automation.PSCustomObject') {
        $fields = @($Expected.PSObject.Properties | ForEach-Object { $_.Name })
        Assert-StratusLineageObject $Actual $fields $Label
        foreach ($field in $fields) { Assert-StratusClosureEqual $Actual.$field $Expected.$field "$Label.$field" }
        return
    }
    if ($Expected -is [int] -or $Expected -is [long]) {
        Assert-StratusLineageInteger $Actual $Label
        if ($Actual -ne $Expected) { throw "$Label integer differs." }
        return
    }
    if (($Expected -isnot [string] -and $Expected -isnot [bool]) -or
        $Actual.GetType() -ne $Expected.GetType() -or $Actual -cne $Expected) { throw "$Label scalar differs." }
}

function Assert-StratusClosureAuthorization {
    param([object] $Value, [string] $Label, [bool] $ApiStage = $false)
    $fields = @('api_stage_permitted', 'worker_start_permitted', 'safety_control_start_permitted',
        'live_transition_permitted', 'binance_kill_switch_removal_permitted', 'migration_container_permitted')
    Assert-StratusClosureRecord $Value $Label -Booleans $fields
    foreach ($field in $fields) {
        $wanted = $field -ceq 'api_stage_permitted' -and $ApiStage
        if ($Value.$field -ne $wanted) { throw "$Label.$field differs from the producer's fail-closed declaration." }
    }
}

function Assert-StratusClosureOwner {
    param([object] $Value, [object] $Owner, [string] $Session, [string] $Label)
    Assert-StratusLineageInteger $Value.owner_pid "$Label owner PID"
    if ($Value.owner_pid -ne $Owner.pid -or $Value.owner_start_time_utc -cne $Owner.start_time_utc -or
        $Value.session_id -cne $Session) { throw "$Label owner/session linkage differs." }
}

function Assert-StratusClosureEnvelope {
    param([object] $Value, [string] $Format, [string[]] $Fields, [string] $Label)
    Assert-StratusClosureRecord $Value $Label -Strings @('format', 'payload_sha256') -Booleans @('accepted') -Objects @('payload')
    Assert-StratusLineageHash $Value.payload_sha256 "$Label payload digest"
    if ($Value.format -cne $Format -or -not $Value.accepted) { throw "$Label is not the accepted producer envelope." }
    Assert-StratusLineageObject $Value.payload $Fields "$Label payload"
    # Original envelope bytes and producer payload serialization/hash must be
    # independently checked by the receiver; supplied digest metadata is not bytes.
}

function Assert-StratusClosureCoreEngine {
    param([object] $Full)
    Assert-StratusClosureRecord $Full 'expected full Docker engine' -Strings @('docker_executable_path', 'docker_executable_sha256',
        'compose_executable_path', 'compose_executable_sha256', 'compose_version', 'context', 'endpoint', 'engine_id', 'server_version',
        'engine_name', 'docker_root_dir', 'contract_sha256') -Integers @('docker_executable_bytes', 'compose_executable_bytes') -Booleans @('skip_tls_verify')
    # Successor watchdog and repair producers omit Compose from their eleven-key
    # boundary. Derive their hash from independent full context, never from a
    # packet's self-declared core fields or its supplied contract digest.
    $core = [ordered]@{}
    $fields = @('docker_executable_path', 'docker_executable_sha256', 'docker_executable_bytes', 'context', 'endpoint',
        'skip_tls_verify', 'engine_id', 'server_version', 'engine_name', 'docker_root_dir')
    foreach ($name in $fields) { $core[$name] = $Full.$name }
    $values = @($Full.docker_executable_path, $Full.docker_executable_sha256, [string]$Full.docker_executable_bytes,
        $Full.context, $Full.endpoint, $Full.skip_tls_verify.ToString().ToLowerInvariant(), $Full.engine_id,
        $Full.server_version, $Full.engine_name, $Full.docker_root_dir)
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($values -join "`0") + "`n")
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { $core.contract_sha256 = [BitConverter]::ToString($algorithm.ComputeHash($bytes)).Replace('-', '').ToLowerInvariant() }
    finally { $algorithm.Dispose() }
    return [pscustomobject]$core
}

function Assert-StratusClosurePgClock {
    param([object] $Value, [string] $Label)
    Assert-StratusLineageString $Value $Label
    # These two fields are raw PostgreSQL json_build_object timestamptz values,
    # unlike the producer-normalized .NET 'o' protocol/HBA timestamps. PostgreSQL
    # emits zero through six fractional digits; never relax other UTC parsers.
    if ($Value -cnotmatch '\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|\+00(?::00)?)\z') {
        throw "$Label must use the exact UTC PostgreSQL timestamp grammar."
    }
    $time = [DateTimeOffset]::Parse($Value, [Globalization.CultureInfo]::InvariantCulture)
    if ($time.Offset -ne [TimeSpan]::Zero) { throw "$Label must be UTC." }
    return $time
}

function Assert-StratusClosureTextHash {
    param([string] $Text)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($algorithm.ComputeHash([Text.UTF8Encoding]::new($false).GetBytes($Text))).Replace('-', '').ToLowerInvariant() }
    finally { $algorithm.Dispose() }
}

function Assert-StratusClosureJsonHash {
    param([object] $Value, [int] $Depth, [object] $Digest, [string] $Label)
    Assert-StratusLineageHash $Digest $Label
    # Exact producer serialization, not a replacement for the caller's raw-byte
    # envelope validation. Input objects retain the producer's property order.
    $json = Microsoft.PowerShell.Utility\ConvertTo-Json -InputObject $Value -Depth $Depth -Compress
    if ((Assert-StratusClosureTextHash $json) -cne $Digest) { throw "$Label is not reproducible." }
}

function Assert-StratusClosureDockerTime {
    param([object] $Value, [string] $Label)
    Assert-StratusLineageString $Value $Label
    # Docker preserves RFC3339Nano strings, including nine fractional digits.
    # This parser is only for raw Docker lifecycle fields, never protocol clocks.
    if ($Value -cnotmatch '\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?(?:Z|\+00:00)\z') {
        throw "$Label must be an exact UTC Docker timestamp."
    }
    return [DateTimeOffset]::Parse($Value, [Globalization.CultureInfo]::InvariantCulture)
}

function Assert-StratusClosureRepairNetworks {
    # Reviewed source-fixed stopped-repair/gate constants, not packet assertions.
    return [pscustomobject][ordered]@{
        weatherpoly_default = [pscustomobject]@{ id = '77551e8d1119201f778fdb90e151ec26f1d9cc23832ba7b88c4db6203e3d8ba2'; compose_name = 'default'
            subnet = '172.20.0.0/16'; gateway = '172.20.0.1'; created = '2026-07-05T17:30:21.808081932Z'
            labels_sha256 = '4a06f3121da4b275c077a18cc2b17a4c12258754e48c3ac2d8de10be0b96ad0d' }
        'weatherpoly_safety-plane' = [pscustomobject]@{ id = '0d1ad122718694dbc5e6714f69a601a7d429e23a0b74eecdb10ace944e5c8620'; compose_name = 'safety-plane'
            subnet = '172.22.0.0/16'; gateway = '172.22.0.1'; created = '2026-07-30T09:19:55.325597748Z'
            labels_sha256 = '44ff0d23c63cad8c3e898bdb1378e958ddcedbcd202055903cd0c7949de103e7' }
    }
}

function Assert-StratusClosureRepairAttachment {
    param([object] $Value, [string] $Name, [string] $State, [object] $Networks, [string] $PostgresId, [string] $Label)
    Assert-StratusClosureRecord $Value $Label -Strings @('name', 'configuration_state', 'network_id', 'ipam_configuration', 'driver_options',
        'endpoint_id', 'gateway', 'ip_address', 'ipv6_gateway', 'global_ipv6_address', 'mac_address', 'canonical_sha256') `
        -Integers @('gateway_priority', 'ip_prefix_length', 'global_ipv6_prefix_length') -Arrays @('aliases', 'dns_names')
    foreach ($item in @($Value.aliases) + @($Value.dns_names)) { Assert-StratusLineageString $item "$Label alias/DNS" }
    $aliases = @($Value.aliases); $dns = @($Value.dns_names)
    [Array]::Sort($aliases, [StringComparer]::Ordinal); [Array]::Sort($dns, [StringComparer]::Ordinal)
    $wantedAliases = @('postgres', 'weatherpoly-postgres-1')
    $wantedDns = @($PostgresId.Substring(0, 12), 'postgres', 'weatherpoly-postgres-1')
    [Array]::Sort($wantedDns, [StringComparer]::Ordinal)
    Assert-StratusClosureEqual $aliases $wantedAliases "$Label aliases"
    Assert-StratusClosureEqual $dns $wantedDns "$Label DNS"
    $configuration = if ($State -ceq 'configured_pending' -and $Name -ceq 'weatherpoly_safety-plane') { 'ConfiguredPending' } else { 'ExactStopped' }
    if ($Value.name -cne $Name -or $Value.configuration_state -cne $configuration -or $Value.gateway_priority -ne 0 -or
        $Value.ip_prefix_length -ne 0 -or $Value.global_ipv6_prefix_length -ne 0) { throw "$Label configuration differs." }
    if ($configuration -ceq 'ConfiguredPending') {
        if ($Value.network_id -cne '' -or $Value.ipam_configuration -cne 'empty_object' -or $Value.driver_options -cne 'empty_object') { throw "$Label pending configuration differs." }
    }
    elseif ($Value.network_id -cne $Networks.$Name.id -or $Value.ipam_configuration -cne 'null' -or $Value.driver_options -cne 'null') { throw "$Label exact configuration differs." }
    foreach ($name in @('endpoint_id', 'gateway', 'ip_address', 'ipv6_gateway', 'global_ipv6_address', 'mac_address')) {
        if ($Value.$name -cne '') { throw "$Label has an active endpoint." }
    }
    $body = [ordered]@{ name = $Value.name; configuration_state = $Value.configuration_state; network_id = $Value.network_id
        ipam_configuration = $Value.ipam_configuration; driver_options = $Value.driver_options; gateway_priority = $Value.gateway_priority
        aliases = $aliases; dns_names = $dns; endpoint_id = $Value.endpoint_id; gateway = $Value.gateway; ip_address = $Value.ip_address
        ip_prefix_length = $Value.ip_prefix_length; ipv6_gateway = $Value.ipv6_gateway; global_ipv6_address = $Value.global_ipv6_address
        global_ipv6_prefix_length = $Value.global_ipv6_prefix_length; mac_address = $Value.mac_address }
    Assert-StratusClosureJsonHash $body 8 $Value.canonical_sha256 "$Label canonical digest"
}

function Assert-StratusClosureRepairSnapshot {
    param([object] $Value, [object] $Core, [object] $Expected, [object] $Stopped, [object] $Networks, [bool] $Post, [string] $Label)
    Assert-StratusClosureRecord $Value $Label -Strings @('canonical_sha256', 'lifecycle_inventory_sha256', 'production_networks_sha256') `
        -Objects @('docker_engine', 'postgres', 'production_networks') -Arrays @('running_container_ids', 'lifecycle_inventory')
    Assert-StratusClosureEqual $Value.docker_engine $Core "$Label core engine"
    if ($Value.running_container_ids.Count -ne 0 -or $Value.lifecycle_inventory.Count -lt 1) { throw "$Label has a running container or empty lifecycle inventory." }
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $previousId = ''; $retained = $null
    foreach ($entry in $Value.lifecycle_inventory) {
        Assert-StratusClosureRecord $entry "$Label lifecycle entry" -Strings @('container_id', 'name', 'state', 'started_at', 'finished_at',
            'compose_project', 'compose_service', 'compose_labels_sha256') -Integers @('restart_count') -Booleans @('running', 'oom_killed')
        Assert-StratusLineageHash $entry.container_id "$Label lifecycle container ID"
        if ($entry.running -or -not $seen.Add($entry.container_id) -or [StringComparer]::Ordinal.Compare($previousId, $entry.container_id) -ge 0) {
            throw "$Label lifecycle inventory is running, duplicated or not ordinal-sorted."
        }
        [void](Assert-StratusClosureDockerTime $entry.started_at "$Label lifecycle StartedAt")
        [void](Assert-StratusClosureDockerTime $entry.finished_at "$Label lifecycle FinishedAt")
        $composeText = "com.docker.compose.project`0$($entry.compose_project)`ncom.docker.compose.service`0$($entry.compose_service)`n"
        Assert-StratusClosureEqual $entry.compose_labels_sha256 (Assert-StratusClosureTextHash $composeText) "$Label lifecycle compose-label digest"
        $previousId = $entry.container_id
        if ($entry.container_id -ceq $Expected.postgres.container_id) { $retained = $entry }
    }
    if ($null -eq $retained) { throw "$Label lacks retained PostgreSQL lifecycle identity." }
    Assert-StratusClosureJsonHash $Value.lifecycle_inventory 8 $Value.lifecycle_inventory_sha256 "$Label lifecycle digest"
    $pg = $Value.postgres
    Assert-StratusClosureRecord $pg "$Label PostgreSQL" -Strings @('stable_identity_sha256', 'attachment_state', 'attachments_sha256') -Objects @('stable_identity', 'attachments')
    $stable = $pg.stable_identity
    Assert-StratusClosureRecord $stable "$Label stable identity" -Strings @('container_id', 'name', 'image_id', 'config_image', 'command_sha256',
        'compose_config_hash', 'state', 'started_at', 'finished_at', 'network_mode', 'hba_sha256', 'hba_source', 'hba_mode', 'hba_propagation',
        'data_volume', 'data_volume_driver', 'data_volume_source', 'data_volume_mode', 'data_volume_propagation', 'published_postgres', 'labels_sha256') `
        -Integers @('restart_count', 'exit_code') -Booleans @('running', 'oom_killed')
    foreach ($name in @('container_id', 'image_id', 'config_image', 'restart_count')) { Assert-StratusClosureEqual $stable.$name $Expected.postgres.$name "$Label retained $name" }
    foreach ($name in @('started_at', 'finished_at')) { Assert-StratusClosureEqual $stable.$name $Stopped.$name "$Label stopped $name" }
    $started = Assert-StratusClosureDockerTime $stable.started_at "$Label PostgreSQL StartedAt"
    $finished = Assert-StratusClosureDockerTime $stable.finished_at "$Label PostgreSQL FinishedAt"
    $hbaPath = $Expected.hba.path.Replace('\', '/')
    $hbaAliases = @($hbaPath)
    if ($hbaPath -match '^([A-Za-z]):/(.*)$') { $drive = $Matches[1].ToLowerInvariant(); $tail = $Matches[2]; $hbaAliases += @("/run/desktop/mnt/host/$drive/$tail", "/host_mnt/$drive/$tail") }
    if ($stable.name -cne '/weatherpoly-postgres-1' -or $stable.state -cne 'exited' -or $stable.running -or $stable.oom_killed -or
        $stable.restart_count -ne 0 -or $stable.exit_code -ne 0 -or $finished -le $started -or
        $stable.command_sha256 -cne '3e1396d8c083a0274909460787db163a1f9070eeabb659c403c5c7ca890436b3' -or
        $stable.compose_config_hash -cne 'bb02ff39b208731cad693da86ac0f83e76aab0b548eca9a0ae310d35ed2576c1' -or
        $stable.network_mode -cne 'weatherpoly_default' -or $stable.hba_sha256 -cne $Expected.hba.sha256 -or
        $stable.hba_source.Replace('\', '/') -notin $hbaAliases -or $stable.hba_mode -cne 'ro' -or $stable.hba_propagation -cne 'rprivate' -or
        $stable.data_volume -cne 'weatherpoly_stratus-postgres' -or $stable.data_volume_driver -cne 'local' -or
        $stable.data_volume_source -cne '/var/lib/docker/volumes/weatherpoly_stratus-postgres/_data' -or $stable.data_volume_mode -cne 'rw' -or
        $stable.data_volume_propagation -cne '' -or $stable.published_postgres -cne '127.0.0.1:5432') { throw "$Label stopped PostgreSQL configuration differs." }
    Assert-StratusLineageHash $stable.labels_sha256 "$Label stable label digest"
    Assert-StratusClosureJsonHash $stable 10 $pg.stable_identity_sha256 "$Label stable identity digest"
    foreach ($name in @('name', 'state', 'running', 'started_at', 'finished_at', 'restart_count', 'oom_killed')) {
        Assert-StratusClosureEqual $retained.$name $stable.$name "$Label retained lifecycle $name"
    }
    if ($retained.compose_project -cne 'weatherpoly' -or $retained.compose_service -cne 'postgres') { throw "$Label retained lifecycle labels differ." }
    if ($pg.attachment_state -cnotin @('default_only', 'exact', 'configured_pending') -or ($Post -and $pg.attachment_state -ceq 'default_only')) {
        throw "$Label attachment disposition differs."
    }
    $names = if ($pg.attachment_state -ceq 'default_only') { @('weatherpoly_default') } else { @('weatherpoly_default', 'weatherpoly_safety-plane') }
    Assert-StratusLineageObject $pg.attachments $names "$Label attachments"
    foreach ($name in $names) { Assert-StratusClosureRepairAttachment $pg.attachments.$name $name $pg.attachment_state $Networks $Expected.postgres.container_id "$Label attachment $name" }
    Assert-StratusClosureJsonHash $pg.attachments 12 $pg.attachments_sha256 "$Label attachment-set digest"
    Assert-StratusLineageObject $Value.production_networks @('weatherpoly_default', 'weatherpoly_safety-plane') "$Label production networks"
    foreach ($name in @('weatherpoly_default', 'weatherpoly_safety-plane')) {
        $network = $Value.production_networks.$name
        Assert-StratusClosureRecord $network "$Label network $name" -Strings @('name', 'id', 'compose_name', 'created', 'scope', 'driver', 'subnet',
            'gateway', 'labels_sha256', 'options_sha256', 'canonical_sha256') -Booleans @('enable_ipv4', 'enable_ipv6', 'internal', 'attachable', 'ingress') -Arrays @('attached_container_ids')
        foreach ($field in @('id', 'compose_name', 'created', 'subnet', 'gateway', 'labels_sha256')) {
            Assert-StratusClosureEqual $network.$field $Networks.$name.$field "$Label source-fixed network $name $field"
        }
        if ($network.name -cne $name -or $network.scope -cne 'local' -or $network.driver -cne 'bridge' -or -not $network.enable_ipv4 -or
            $network.enable_ipv6 -or $network.internal -or $network.attachable -or $network.ingress -or $network.attached_container_ids.Count -ne 0 -or
            $network.options_sha256 -cne 'e0d52002c06510e7c6f227700d07f412845fc15686f0723a262d8c3e1459f569') { throw "$Label stopped network flags/options differ." }
        $body = [ordered]@{}
        foreach ($field in @('name', 'id', 'compose_name', 'created', 'scope', 'driver', 'subnet', 'gateway', 'enable_ipv4', 'enable_ipv6',
            'internal', 'attachable', 'ingress', 'attached_container_ids', 'labels_sha256', 'options_sha256')) { $body[$field] = $network.$field }
        Assert-StratusClosureJsonHash $body 8 $network.canonical_sha256 "$Label network canonical digest"
    }
    Assert-StratusClosureJsonHash $Value.production_networks 12 $Value.production_networks_sha256 "$Label production network-set digest"
    $body = [ordered]@{}
    foreach ($field in @('docker_engine', 'running_container_ids', 'lifecycle_inventory', 'lifecycle_inventory_sha256', 'postgres', 'production_networks', 'production_networks_sha256')) { $body[$field] = $Value.$field }
    Assert-StratusClosureJsonHash $body 20 $Value.canonical_sha256 "$Label canonical digest"
}

function Assert-StratusClosureRepairEvents {
    param([object] $Value, [bool] $Mutation, [DateTimeOffset] $Accepted, [object] $Expected, [object] $Networks)
    $label = 'repair event witness'
    $connectInteger = @(); $connectNull = @()
    if ($Mutation) { $connectInteger = @('network_connect_time_nano') } else { $connectNull = @('network_connect_time_nano') }
    Assert-StratusClosureRecord $Value $label -Strings @('format', 'session_id', 'process_started_at', 'barrier_volume_name', 'journal_sha256', 'events_sha256') `
        -Integers (@('process_id', 'authority_as_of_time_nano', 'journal_bytes', 'event_count', 'network_connect_count', 'stderr_bytes', 'unexpected_event_count') + $connectInteger) `
        -Booleans @('truncated', 'eof_before_seal') -Objects @('collector', 'pre_barrier', 'final_barrier') -Arrays @('event_command', 'events') -Nulls $connectNull
    if ($Value.format -cne 'stratus-stopped-repair-docker-event-witness-v1' -or $Value.session_id -cnotmatch '\A[0-9a-f]{32}\z' -or
        $Value.process_id -le 0 -or $Value.process_id -gt [int]::MaxValue -or $Value.journal_bytes -le 0 -or $Value.journal_bytes -gt 65536 -or
        $Value.stderr_bytes -ne 0 -or $Value.unexpected_event_count -ne 0 -or $Value.truncated -or $Value.eof_before_seal) { throw "$label sealed identity/state differs." }
    foreach ($name in @('barrier_volume_name', 'journal_sha256', 'events_sha256')) { Assert-StratusLineageHash $Value.$name "$label $name" }
    $started = Get-StratusLineageUtc $Value.process_started_at "$label process birth"
    if ($started -gt $Accepted.AddSeconds(5)) { throw "$label process birth is after acceptance." }
    $format = '{"type":{{json .Type}},"action":{{json .Action}},"actor_id":{{json .Actor.ID}},"attributes":{{json .Actor.Attributes}},"scope":{{json .Scope}},"time":{{.Time}},"time_nano":{{.TimeNano}}}'
    Assert-StratusClosureEqual $Value.event_command @('--context', $Expected.docker_engine.context, 'events', '--format', $format) "$label exact command"
    $collector = $Value.collector
    Assert-StratusClosureRecord $collector "$label collector" -Strings @('type_name', 'contract_identity', 'template_sha256', 'source_sha256', 'assembly_full_name_sha256', 'module_version_id') -Booleans @('assembly_is_dynamic')
    $template = '090809477bdd74506256d557f11a2d73a470675de98c41d3f5923735251f51c5'
    if ($collector.template_sha256 -cne $template -or $collector.source_sha256 -cne '621e556478237ab88a6cb20247953d92569e6f00fd3fa700bfa706c3bcc37a5e' -or
        $collector.type_name -cne ('StratusRepairDockerCollector_' + $template.Substring(0, 16)) -or $collector.contract_identity -cne ('repair-v2:' + $template) -or
        $collector.assembly_is_dynamic -or $collector.module_version_id -cnotmatch '\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\z' -or
        $collector.module_version_id -ceq '00000000-0000-0000-0000-000000000000') { throw "$label collector metadata differs." }
    Assert-StratusLineageHash $collector.assembly_full_name_sha256 "$label collector assembly digest"
    $count = if ($Mutation) { 3 } else { 2 }; $connectCount = if ($Mutation) { 1 } else { 0 }
    if ($Value.events.Count -ne $count -or $Value.event_count -ne $count -or $Value.network_connect_count -ne $connectCount) { throw "$label event count differs." }
    $prior = [long]0
    for ($index = 0; $index -lt $count; $index++) {
        $event = $Value.events[$index]
        Assert-StratusClosureRecord $event "$label record $index" -Strings @('type', 'action', 'actor_id', 'scope') -Integers @('time', 'time_nano') -Objects @('attributes')
        $connect = $Mutation -and $index -eq 1
        $type = if ($connect) { 'network' } else { 'volume' }
        $action = if ($connect) { 'connect' } elseif ($index -eq 0) { 'create' } else { 'destroy' }
        $actor = if ($connect) { $Networks.'weatherpoly_safety-plane'.id } else { $Value.barrier_volume_name }
        $attributes = if ($connect) { [pscustomobject][ordered]@{ container = $Expected.postgres.container_id; name = 'weatherpoly_safety-plane'; type = 'bridge' } } else { [pscustomobject][ordered]@{ driver = 'local' } }
        Assert-StratusClosureEqual $event.attributes $attributes "$label record $index attributes"
        if ($event.type -cne $type -or $event.action -cne $action -or $event.actor_id -cne $actor -or $event.scope -cne 'local' -or
            $event.time -le 0 -or $event.time_nano -le $prior -or [long][Math]::Floor([decimal]$event.time_nano / [decimal]1000000000) -ne $event.time -or
            $event.time -lt ($started.ToUnixTimeSeconds() - 5) -or $event.time -gt ($Accepted.ToUnixTimeSeconds() + 5)) { throw "$label record $index causal identity/time differs." }
        $prior = $event.time_nano
    }
    foreach ($name in @('pre_barrier', 'final_barrier')) {
        Assert-StratusClosureRecord $Value.$name "$label $name" -Strings @('actor_id') -Integers @('time', 'time_nano')
        $event = if ($name -ceq 'pre_barrier') { $Value.events[0] } else { $Value.events[$count - 1] }
        foreach ($field in @('actor_id', 'time', 'time_nano')) { Assert-StratusClosureEqual $Value.$name.$field $event.$field "$label $name $field" }
    }
    if ($Value.authority_as_of_time_nano -ne $prior -or ($Mutation -and $Value.network_connect_time_nano -ne $Value.events[1].time_nano)) { throw "$label authority/connect time differs." }
    $acceptedNano = [long](($Accepted.UtcDateTime.Ticks - [long]621355968000000000) * [long]100)
    if ($acceptedNano - $prior -gt [long]30000000000 -or $prior - $acceptedNano -gt [long]5000000000) { throw "$label final barrier seal lag differs." }
    $lines = @($Value.events | ForEach-Object { Microsoft.PowerShell.Utility\ConvertTo-Json -InputObject $_ -Depth 8 -Compress })
    $journal = ($lines -join "`n") + "`n"
    if ($Value.journal_bytes -ne [Text.UTF8Encoding]::new($false).GetByteCount($journal) -or $Value.journal_sha256 -cne (Assert-StratusClosureTextHash $journal)) { throw "$label journal digest/bytes differ." }
    Assert-StratusClosureJsonHash $Value.events 8 $Value.events_sha256 "$label events digest"
}

function Assert-StratusClosureRepairGraph {
    param([object] $Value, [object] $Core, [object] $Expected, [object] $Stopped, [DateTimeOffset] $Accepted, [DateTimeOffset] $PreStart)
    # Complete durable producer-graph consistency. Capture provenance, filesystem
    # publication times and current ownership are still NOT established here.
    $networks = Assert-StratusClosureRepairNetworks
    if ($Value.fixed_identity.default_network_id -cne $networks.weatherpoly_default.id -or
        $Value.fixed_identity.safety_network_id -cne $networks.'weatherpoly_safety-plane'.id) { throw 'Repair fixed network identities differ from reviewed source.' }
    foreach ($name in @('pre', 'armed', 'post_first', 'post_second')) {
        Assert-StratusClosureRepairSnapshot $Value.snapshots.$name $Core $Expected $Stopped $networks ($name -cin @('post_first', 'post_second')) "repair $name snapshot"
    }
    Assert-StratusClosureEqual $Value.snapshots.pre $Value.snapshots.armed 'repair pre/armed stable boundary'
    Assert-StratusClosureEqual $Value.snapshots.post_first $Value.snapshots.post_second 'repair repeated final boundary'
    foreach ($name in @('armed', 'post_first', 'post_second')) {
        foreach ($field in @('docker_engine', 'lifecycle_inventory', 'lifecycle_inventory_sha256', 'production_networks', 'production_networks_sha256')) {
            Assert-StratusClosureEqual $Value.snapshots.$name.$field $Value.snapshots.pre.$field "repair cross-snapshot $field"
        }
        Assert-StratusClosureEqual $Value.snapshots.$name.postgres.stable_identity $Value.snapshots.pre.postgres.stable_identity 'repair cross-snapshot stopped identity'
    }
    Assert-StratusClosureRecord $Value.mutation 'repair mutation' -Booleans @('issued') -Integers @('output_line_count') -Arrays @('command')
    Assert-StratusClosureEqual $Value.mutation.command @('network', 'connect', '--alias', 'postgres', '--alias', 'weatherpoly-postgres-1', '--gw-priority', '0',
        $networks.'weatherpoly_safety-plane'.id, $Expected.postgres.container_id) 'repair mutation command'
    if ($Value.mutation.output_line_count -ne 0 -or (($Value.disposition -ceq 'connected_by_this_invocation') -ne $Value.mutation.issued)) { throw 'Repair mutation/disposition differs.' }
    if ($Value.mutation.issued) {
        if ($Value.snapshots.pre.postgres.attachment_state -cne 'default_only') { throw 'Repair issued mutation without default-only predecessor.' }
    }
    else {
        $wanted = if ($Value.disposition -ceq 'verified_exact_existing') { 'exact' } elseif ($Value.disposition -ceq 'verified_configured_pending_existing') { 'configured_pending' } else { throw 'Repair disposition differs.' }
        if ($Value.snapshots.pre.postgres.attachment_state -cne $wanted) { throw 'Repair non-mutation attachment disposition differs.' }
        Assert-StratusClosureEqual $Value.snapshots.pre $Value.snapshots.post_second 'repair non-mutation unchanged boundary'
    }
    if ($Accepted -gt $PreStart.AddSeconds(5) -or $Accepted -lt $PreStart.AddSeconds(-600)) { throw 'Repair acceptance does not precede the bounded PreStart handoff.' }
    Assert-StratusClosureRepairEvents $Value.event_witness $Value.mutation.issued $Accepted $Expected $networks
}

function Assert-StratusClosureFreshHba {
    param([object] $Value, [object] $Hba, [string] $Label)
    Assert-StratusClosureRecord $Value $Label -Strings @('conf_load_time', 'postmaster_started_at', 'backend_started_at', 'hba_file',
        'inventory_sha256') -Integers @('backend_pid', 'inventory_line_count', 'inventory_rule_count', 'inventory_errors')
    if ($Value.inventory_sha256 -cne 'e6b26bdffeba11e892100ce05f855db8ede8ce407a24fbef6144d2a458cc3567' -or
        $Value.inventory_line_count -ne 16 -or $Value.inventory_rule_count -ne 15 -or $Value.inventory_errors -ne 0 -or
        $Value.backend_pid -le 0 -or $Value.backend_pid -gt [int]::MaxValue -or $Value.hba_file -cne '/etc/postgresql/stratus-pg_hba.conf' -or
        $Value.postmaster_started_at -cne $Hba.postmaster_started_at) { throw "$Label generation/inventory identity differs." }
    $conf = Get-StratusLineageUtc $Value.conf_load_time "$Label configuration time"
    $backend = Get-StratusLineageUtc $Value.backend_started_at "$Label backend birth"
    $requested = Get-StratusLineageUtc $Hba.reload_requested_at 'HBA reload request'
    $acknowledged = Get-StratusLineageUtc $Hba.reload_acknowledged_at 'HBA reload acknowledgement'
    if ($conf -lt $requested -or $conf -gt $acknowledged -or $backend -lt $acknowledged -or $conf -gt $backend) {
        throw "$Label did not inherit the acknowledged HBA generation."
    }
}

function Assert-StratusClosurePostgresSnapshot {
    param([object] $Value, [object] $Expected, [bool] $Running, [string] $Label)
    Assert-StratusClosureRecord $Value $Label -Strings @('container_id', 'image_id', 'config_image', 'started_at', 'finished_at', 'state',
        'restart_policy', 'health_status', 'network_mode', 'mount_contract_sha256', 'ports_contract_sha256',
        'network_contract_sha256', 'active_endpoint_sha256') -Integers @('restart_count', 'exit_code') -Booleans @('oom_killed') -Objects @('networks')
    foreach ($name in @('container_id', 'image_id', 'config_image', 'restart_count')) {
        Assert-StratusClosureEqual $Value.$name $Expected.$name "$Label $name"
    }
    foreach ($name in @('mount_contract_sha256', 'ports_contract_sha256', 'network_contract_sha256', 'active_endpoint_sha256')) {
        Assert-StratusLineageHash $Value.$name "$Label $name"
    }
    if ($Value.oom_killed -or $Value.restart_policy -cne 'no' -or $Value.restart_count -ne 0 -or
        ($Running -and ($Value.state -cne 'running' -or $Value.health_status -cne 'healthy' -or $Value.started_at -cne $Expected.started_at)) -or
        (-not $Running -and ($Value.state -cne 'exited' -or $Value.health_status -cne 'not-running'))) {
        throw "$Label lifecycle policy differs."
    }
}

function Assert-StratusParentTransitionClosure {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][object] $Packet,
        [Parameter(Mandatory = $true)][object] $Expected)

    $documentNames = @('authorization', 'terminal', 'commit', 'lease', 'live', 'prestart', 'fence', 'attempt', 'completion', 'repair')
    Assert-StratusLineageObject $Packet $documentNames 'Transition packet'
    Assert-StratusClosureRecord $Expected 'expected closure context' -Strings @('root', 'parent_armed_at', 'observed_at',
        'revision', 'candidate_image', 'candidate_image_id') -Objects @('parent_stage', 'postgres', 'docker_engine',
        'program', 'gate_program', 'paths', 'registration', 'terminal_stdout', 'hba', 'historical_inputs')
    Assert-StratusLineagePath $Expected.root 'expected root'
    Assert-StratusLineageObject $Expected.paths @($documentNames + @('heartbeat', 'revoke')) 'expected packet paths'
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($name in @('contract_path', 'lease_path', 'ready_path', 'heartbeat_path', 'mutation_fence_path')) {
        Assert-StratusLineagePath $Expected.parent_stage.$name "parent $name"
        if (-not $seen.Add($Expected.parent_stage.$name)) { throw 'Parent protocol paths alias.' }
    }
    foreach ($name in @($documentNames + @('heartbeat', 'revoke'))) {
        $path = $Expected.paths.$name
        Assert-StratusLineagePath $path "expected $name path"
        if (-not $path.StartsWith($Expected.root + '\', [StringComparison]::OrdinalIgnoreCase) -or -not $seen.Add($path)) {
            throw 'Transition packet path escapes root or aliases another artifact.'
        }
        if ($name -cin $documentNames) {
            Assert-StratusLineageDocument $Packet.$name $path $Packet.$name.sha256 "$name document"
        }
    }
    Assert-StratusClosureRecord $Expected.registration 'transition registration projection' -Strings @(
        'parent_authorization_sha256', 'transition_terminal_sha256', 'postgres_started_at')
    Assert-StratusClosureEqual $Packet.authorization.sha256 $Expected.registration.parent_authorization_sha256 'registered authorization hash'
    Assert-StratusClosureEqual $Packet.terminal.sha256 $Expected.registration.transition_terminal_sha256 'registered terminal hash'
    Assert-StratusClosureRecord $Expected.postgres 'expected current PostgreSQL' -Strings @(
        'container_id', 'image_id', 'config_image', 'started_at') -Integers @('restart_count')
    Assert-StratusLineageHash $Expected.postgres.container_id 'expected PostgreSQL ID'
    Assert-StratusClosureEqual $Expected.registration.postgres_started_at $Expected.postgres.started_at 'registered PostgreSQL incarnation'
    Assert-StratusClosureRecord $Expected.program 'expected successor watchdog program' -Strings @(
        'script_path', 'script_sha256', 'host_path', 'host_sha256') -Integers @('script_bytes', 'host_bytes')
    Assert-StratusClosureRecord $Expected.gate_program 'expected gate program' -Strings @('script_path', 'script_sha256') -Integers @('script_bytes')
    foreach ($program in @($Expected.program, $Expected.gate_program)) {
        Assert-StratusLineagePath $program.script_path 'expected pinned script path'
        Assert-StratusLineageHash $program.script_sha256 'expected pinned script hash'
        if ($program.script_bytes -le 0) { throw 'Expected pinned script size must be positive.' }
    }
    Assert-StratusLineagePath $Expected.program.host_path 'expected host path'
    Assert-StratusLineageHash $Expected.program.host_sha256 'expected host hash'
    if ($Expected.program.host_bytes -le 0 -or $Expected.revision -cnotmatch '^[0-9a-f]{40}$' -or
        $Expected.candidate_image_id -cnotmatch '^sha256:[0-9a-f]{64}$' -or
        $Expected.candidate_image -cne ('stratus:candidate-' + $Expected.revision.Substring(0, 12))) {
        throw 'Expected candidate or host identity differs.'
    }
    $owner = $Expected.parent_stage.owner
    $coreEngine = Assert-StratusClosureCoreEngine $Expected.docker_engine
    $observed = Get-StratusLineageUtc $Expected.observed_at 'expected observation time'
    $parentArmed = Get-StratusLineageUtc $Expected.parent_armed_at 'expected parent armed time'
    $auth = $Packet.authorization.value; $terminal = $Packet.terminal.value
    $commit = $Packet.commit.value; $lease = $Packet.lease.value
    $live = $Packet.live.value; $prestart = $Packet.prestart.value

    Assert-StratusClosureRecord $auth 'parent authorization' -Strings @('format', 'status', 'authorized_at',
        'publication_started_at', 'valid_until', 'session_id', 'owner_start_time_utc', 'parent_authorization_path',
        'watchdog_program_path', 'watchdog_program_sha256', 'watchdog_process_started_at', 'sealed_stdout_sha256',
        'sealed_stderr_sha256', 'lease_path', 'lease_sha256', 'receipt_path', 'receipt_sha256', 'commit_path',
        'commit_sha256', 'live_path', 'live_sha256', 'live_payload_sha256', 'start_fence_path', 'start_fence_sha256',
        'start_attempt_path', 'start_attempt_sha256', 'start_completion_path', 'start_completion_sha256',
        'docker_engine_contract_sha256', 'postgres_container_id', 'postgres_image_id', 'postgres_config_image',
        'postgres_started_at') -Integers @('docker_state_observed_through_time_nano', 'owner_pid', 'watchdog_process_id',
        'watchdog_exit_code', 'sealed_stdout_bytes', 'sealed_stderr_bytes', 'postgres_restart_count') -Objects @(
        'authorizer', 'authorization_witness', 'parent_stage', 'authorization')
    if ($auth.format -cne 'stratus-successor-transition-parent-authorization-v1' -or $auth.status -cne 'authorized' -or
        $auth.session_id -cnotmatch '^[0-9a-f]{32}$') { throw 'Transition authorization format/status/session differs.' }
    Assert-StratusClosureOwner $auth $owner $auth.session_id 'authorization'
    Assert-StratusClosureEqual $auth.parent_stage $Expected.parent_stage 'independently bound parent-stage/R1/source context'
    Assert-StratusClosureAuthorization $auth.authorization 'authorization permissions' -ApiStage $true
    Assert-StratusClosureEqual $auth.parent_authorization_path $Expected.paths.authorization 'authorization output path'
    foreach ($pair in @(@('lease', 'lease'), @('receipt', 'terminal'), @('commit', 'commit'), @('live', 'live'),
        @('start_fence', 'fence'), @('start_attempt', 'attempt'), @('start_completion', 'completion'))) {
        Assert-StratusClosureEqual $auth.($pair[0] + '_path') $Packet.($pair[1]).path "authorization $($pair[0]) path"
        Assert-StratusClosureEqual $auth.($pair[0] + '_sha256') $Packet.($pair[1]).sha256 "authorization $($pair[0]) hash"
    }
    foreach ($name in @('container_id', 'image_id', 'config_image', 'restart_count', 'started_at')) {
        Assert-StratusClosureEqual $auth.('postgres_' + $name) $Expected.postgres.$name "authorization PostgreSQL $name"
    }
    Assert-StratusClosureRecord $auth.authorizer 'authorization authorizer' -Strings @('gate_script_path', 'gate_script_sha256',
        'host_executable_path', 'host_executable_sha256', 'process_started_at') -Integers @('gate_script_bytes', 'host_executable_bytes', 'process_id')
    foreach ($name in @('path', 'sha256', 'bytes')) {
        Assert-StratusClosureEqual $auth.authorizer.('gate_script_' + $name) $Expected.gate_program.('script_' + $name) "authorizer gate $name"
        Assert-StratusClosureEqual $auth.authorizer.('host_executable_' + $name) $Expected.program.('host_' + $name) "authorizer host $name"
    }
    Assert-StratusClosureEqual $auth.authorizer.process_id $owner.pid 'authorizer PID'
    Assert-StratusClosureEqual $auth.authorizer.process_started_at $owner.start_time_utc 'authorizer birth'
    Assert-StratusClosureEqual $auth.watchdog_program_path $Expected.program.script_path 'authorization watchdog script'
    Assert-StratusClosureEqual $auth.watchdog_program_sha256 $Expected.program.script_sha256 'authorization watchdog script hash'
    Assert-StratusClosureRecord $Expected.terminal_stdout 'normalized terminal stdout projection' -Strings @('sha256') -Integers @('bytes')
    Assert-StratusLineageHash $Expected.terminal_stdout.sha256 'normalized terminal stdout digest'
    if ($Expected.terminal_stdout.bytes -le 0 -or $auth.watchdog_exit_code -ne 0 -or $auth.sealed_stderr_bytes -ne 0 -or
        $auth.sealed_stderr_sha256 -cne 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855') {
        throw 'Authorization declared exit/stdout/stderr boundary differs.'
    }
    Assert-StratusClosureEqual $auth.sealed_stdout_sha256 $Expected.terminal_stdout.sha256 'declared stdout digest versus durable terminal bytes'
    Assert-StratusClosureEqual $auth.sealed_stdout_bytes $Expected.terminal_stdout.bytes 'declared stdout bytes versus durable terminal bytes'

    Assert-StratusClosureRecord $lease 'successor lease' -Strings @('format', 'armed_at', 'session_id') -Booleans @('armed') -Objects @(
        'watchdog', 'owner', 'deadline', 'artifacts', 'docker_engine', 'postgres', 'containment')
    if ($lease.format -cne 'stratus-successor-transition-watchdog-lease-v3' -or -not $lease.armed -or
        $lease.session_id -cne $auth.session_id) { throw 'Successor lease format/session/arming differs.' }
    foreach ($name in @('owner', 'watchdog')) {
        Assert-StratusClosureRecord $lease.$name "lease $name" -Strings @('process_started_at') -Integers @('process_id')
        if ($lease.$name.process_id -le 0 -or $lease.$name.process_id -gt [int]::MaxValue) { throw 'Lease process PID is outside range.' }
        [void](Get-StratusLineageUtc $lease.$name.process_started_at "lease $name birth")
    }
    Assert-StratusClosureEqual $lease.owner.process_id $owner.pid 'lease owner PID'
    Assert-StratusClosureEqual $lease.owner.process_started_at $owner.start_time_utc 'lease owner birth'
    Assert-StratusClosureEqual $lease.watchdog.process_id $auth.watchdog_process_id 'declared successor watchdog PID'
    Assert-StratusClosureEqual $lease.watchdog.process_started_at $auth.watchdog_process_started_at 'declared successor watchdog birth'
    if ($lease.watchdog.process_id -eq $owner.pid -or $lease.watchdog.process_id -eq $Expected.parent_stage.watchdog.pid) {
        throw 'Successor watchdog is not a distinct declared process.'
    }
    Assert-StratusClosureRecord $lease.deadline 'lease deadline' -Strings @('hard_deadline_utc') -Integers @('heartbeat_timeout_seconds', 'maximum_lease_seconds')
    if ($lease.deadline.heartbeat_timeout_seconds -ne 15 -or $lease.deadline.maximum_lease_seconds -ne 600) { throw 'Successor lease bounds differ.' }
    Assert-StratusClosureEqual $lease.deadline.hard_deadline_utc $auth.valid_until 'authorization hard deadline'
    Assert-StratusClosureRecord $lease.artifacts 'lease artifacts' -Strings @('lease_path', 'receipt_path', 'heartbeat_path', 'commit_path',
        'live_path', 'prestart_path', 'prestart_sha256', 'prestart_payload_sha256', 'start_fence_path', 'start_fence_sha256',
        'start_revoke_path', 'start_attempt_path', 'start_completion_path')
    foreach ($pair in @(@('lease', 'lease'), @('receipt', 'terminal'), @('heartbeat', 'heartbeat'), @('commit', 'commit'), @('live', 'live'),
        @('prestart', 'prestart'), @('start_fence', 'fence'), @('start_revoke', 'revoke'), @('start_attempt', 'attempt'), @('start_completion', 'completion'))) {
        Assert-StratusClosureEqual $lease.artifacts.($pair[0] + '_path') $Expected.paths.($pair[1]) "lease $($pair[0]) path"
    }
    Assert-StratusClosureEqual $lease.artifacts.prestart_sha256 $Packet.prestart.sha256 'lease PreStart raw digest'
    Assert-StratusClosureEqual $lease.artifacts.start_fence_sha256 $Packet.fence.sha256 'lease fence digest'
    Assert-StratusClosureRecord $lease.postgres 'lease PostgreSQL' -Strings @('container_id', 'image_id', 'config_image',
        'restart_policy_before', 'restart_policy_armed', 'state', 'started_at', 'finished_at') -Integers @('restart_count')
    foreach ($name in @('container_id', 'image_id', 'config_image', 'restart_count')) {
        Assert-StratusClosureEqual $lease.postgres.$name $Expected.postgres.$name "lease PostgreSQL $name"
    }
    if ($lease.postgres.state -cne 'exited' -or $lease.postgres.restart_policy_before -cne 'unless-stopped' -or
        $lease.postgres.restart_policy_armed -cne 'no') { throw 'Lease PostgreSQL stopped policy differs.' }
    Assert-StratusClosureRecord $lease.containment 'lease containment' -Integers @('stop_timeout_seconds', 'stop_wall_clock_timeout_seconds') -Booleans @(
        'exact_container_only', 'ambiguous_start_requires_causal_resolution')
    if ($lease.containment.stop_timeout_seconds -ne 15 -or $lease.containment.stop_wall_clock_timeout_seconds -ne 25 -or
        -not $lease.containment.exact_container_only -or -not $lease.containment.ambiguous_start_requires_causal_resolution) { throw 'Lease causal containment policy differs.' }

    Assert-StratusClosureRecord $commit 'successor commit' -Strings @('format', 'session_id', 'owner_start_time_utc', 'lease_path',
        'lease_sha256', 'live_path', 'live_sha256', 'committed_at') -Integers @('owner_pid')
    if ($commit.format -cne 'stratus-successor-transition-watchdog-commit-v1') { throw 'Commit format differs.' }
    Assert-StratusClosureOwner $commit $owner $auth.session_id 'commit'
    foreach ($name in @('lease_path', 'lease_sha256', 'live_path', 'live_sha256')) { Assert-StratusClosureEqual $commit.$name $auth.$name "commit $name" }
    Assert-StratusClosureRecord $terminal 'successor terminal' -Strings @('format', 'status', 'terminal_at', 'session_id',
        'owner_start_time_utc', 'lease_path', 'lease_sha256', 'receipt_path', 'commit_path', 'live_path', 'trigger') -Integers @('owner_pid') -Objects @(
        'docker_engine', 'commit', 'authorization') -Nulls @('containment', 'failure')
    Assert-StratusClosureOwner $terminal $owner $auth.session_id 'terminal'
    if ($terminal.format -cne 'stratus-successor-transition-watchdog-terminal-v1' -or $terminal.status -cne 'success' -or
        $terminal.trigger -cne 'accepted_live_commit') { throw 'Transition terminal is not the exact success kind.' }
    Assert-StratusClosureAuthorization $terminal.authorization 'successor terminal permissions'
    foreach ($name in @('lease_path', 'lease_sha256', 'receipt_path', 'commit_path', 'live_path')) { Assert-StratusClosureEqual $terminal.$name $auth.$name "terminal $name" }
    Assert-StratusClosureRecord $terminal.commit 'terminal commit closure' -Strings @('marker_sha256', 'committed_at', 'live_sha256',
        'live_payload_sha256', 'postgres_started_at', 'docker_engine_contract_sha256')
    Assert-StratusClosureEqual $terminal.commit.marker_sha256 $Packet.commit.sha256 'terminal commit raw hash'
    Assert-StratusClosureEqual $terminal.commit.committed_at $commit.committed_at 'terminal commit time'
    foreach ($name in @('live_sha256', 'live_payload_sha256', 'postgres_started_at')) {
        Assert-StratusClosureEqual $terminal.commit.$name $auth.$name "terminal commit $name"
    }
    Assert-StratusClosureEqual $terminal.commit.docker_engine_contract_sha256 $coreEngine.contract_sha256 'terminal commit core Docker contract'

    Assert-StratusClosureEnvelope $prestart 'stratus-post-migration-successor-transition-prestart-v2' @('accepted_at', 'validity',
        'historical', 'network_repair', 'successor', 'closed_contract', 'compose_migrate', 'postgres_prestart', 'hba_host_read_lock',
        'topology', 'transition_session', 'authorization') 'PreStart'
    Assert-StratusClosureEnvelope $live 'stratus-post-migration-successor-transition-live-v2' @('accepted_at', 'artifact_written_at',
        'historical', 'network_repair', 'prestart', 'successor', 'closed_contract', 'postgres_transition', 'transition_session',
        'topology', 'hba', 'final_boundary', 'database', 'authorization') 'Live'
    Assert-StratusClosureEqual $lease.artifacts.prestart_payload_sha256 $prestart.payload_sha256 'lease PreStart payload digest'
    Assert-StratusClosureEqual $auth.live_payload_sha256 $live.payload_sha256 'authorization Live payload digest'
    $pre = $prestart.payload; $lp = $live.payload
    $successor = [pscustomobject]@{ revision = $Expected.revision; image = $Expected.candidate_image; image_id = $Expected.candidate_image_id }
    Assert-StratusClosureEqual $pre.successor $successor 'PreStart immutable candidate'
    Assert-StratusClosureEqual $lp.successor $successor 'Live immutable candidate'
    Assert-StratusClosureEqual $lp.network_repair $pre.network_repair 'Live/PreStart network repair closure'
    Assert-StratusClosureEqual $lp.closed_contract $pre.closed_contract 'Live/PreStart closed source contract'
    Assert-StratusClosureRecord $lp.prestart 'Live PreStart link' -Strings @('path', 'sha256', 'payload_sha256', 'accepted_at')
    Assert-StratusClosureEqual $lp.prestart.path $Packet.prestart.path 'Live PreStart path'
    Assert-StratusClosureEqual $lp.prestart.sha256 $Packet.prestart.sha256 'Live PreStart digest'
    Assert-StratusClosureEqual $lp.prestart.payload_sha256 $prestart.payload_sha256 'Live PreStart payload digest'
    Assert-StratusClosureEqual $lp.prestart.accepted_at $pre.accepted_at 'Live PreStart acceptance time'
    Assert-StratusClosureRecord $pre.validity 'PreStart validity' -Strings @('expires_at', 'postgres_start_deadline') -Integers @('max_age_seconds', 'postgres_start_window_seconds')
    $preAt = Get-StratusLineageUtc $pre.accepted_at 'PreStart accepted time'
    if ($pre.validity.max_age_seconds -ne 600 -or $pre.validity.postgres_start_window_seconds -ne 300 -or
        $pre.validity.expires_at -cne $preAt.AddSeconds(600).ToString('o') -or
        $pre.validity.postgres_start_deadline -cne $preAt.AddSeconds(300).ToString('o') -or
        $auth.valid_until -cne $pre.validity.postgres_start_deadline) { throw 'PreStart fixed validity/start window differs.' }
    Assert-StratusClosureAuthorization $lp.authorization 'Live permissions'
    Assert-StratusClosureRecord $pre.authorization 'PreStart permissions' -Booleans @('postgres_start_permitted', 'external_postgres_start_permitted',
        'transition_process_start_permitted', 'api_stage_permitted', 'worker_start_permitted', 'safety_control_start_permitted',
        'live_transition_permitted', 'binance_kill_switch_removal_permitted')
    foreach ($property in $pre.authorization.PSObject.Properties) {
        if ($property.Value -ne ($property.Name -ceq 'transition_process_start_permitted')) { throw 'PreStart permission boundary differs.' }
    }
    $ps = $pre.transition_session; $ls = $lp.transition_session
    Assert-StratusClosureRecord $ps 'PreStart session' -Strings @('mode', 'session_id', 'collector_process_started_at', 'collector_requested_at',
        'accepted_journal_prefix_sha256') -Integers @('collector_process_id', 'accepted_journal_prefix_bytes') -Booleans @('reconnect_permitted') -Objects @(
        'docker_engine', 'initial_barrier', 'watchdog_program', 'watchdog_handoff', 'restart_policy_transition', 'observation', 'start_proof')
    Assert-StratusClosureRecord $ls 'Live session' -Strings @('mode', 'session_id') -Objects @('docker_engine', 'event_witness', 'watchdog_program',
        'watchdog', 'watchdog_lease', 'heartbeat_at_live', 'restart_policy_transition', 'observation', 'start_proof', 'rollback')
    foreach ($session in @($ps, $ls)) {
        if ($session.mode -cne 'single-process' -or $session.session_id -cne $auth.session_id) { throw 'PreStart/Live main Transition session differs.' }
        Assert-StratusClosureEqual $session.docker_engine $Expected.docker_engine 'independent Docker engine context'
        Assert-StratusClosureRecord $session.watchdog_program 'declared watchdog program' -Strings @('script_path', 'script_sha256', 'host_path',
            'host_sha256', 'owner_start_time_utc') -Integers @('script_bytes', 'host_bytes', 'owner_pid') -Booleans @('read_locks_held')
        foreach ($name in @('script_path', 'script_sha256', 'script_bytes', 'host_path', 'host_sha256', 'host_bytes')) {
            Assert-StratusClosureEqual $session.watchdog_program.$name $Expected.program.$name "declared watchdog program $name"
        }
        Assert-StratusClosureEqual $session.watchdog_program.owner_pid $owner.pid 'program owner PID'
        Assert-StratusClosureEqual $session.watchdog_program.owner_start_time_utc $owner.start_time_utc 'program owner birth'
        if (-not $session.watchdog_program.read_locks_held) { throw 'Program read-lock declaration differs.' }
        Assert-StratusClosureRecord $session.restart_policy_transition 'restart policy transition' -Strings @('previous', 'armed') -Arrays @('command') -Booleans @(
            'command_issued', 'stopped_identity_rechecked', 'restore_on_success')
        $restart = $session.restart_policy_transition
        Assert-StratusClosureEqual $restart.command @('update', '--restart=no', $Expected.postgres.container_id) 'exact restart arming command'
        if ($restart.previous -cne 'unless-stopped' -or $restart.armed -cne 'no' -or -not $restart.command_issued -or
            -not $restart.stopped_identity_rechecked -or $restart.restore_on_success) { throw 'Restart policy arming differs.' }
        Assert-StratusClosureRecord $session.observation 'start observation' -Booleans @('start_command_issued', 'start_completed')
    }
    if ($ps.reconnect_permitted -or $ps.observation.start_command_issued -or $ps.observation.start_completed -or
        -not $ls.observation.start_command_issued -or -not $ls.observation.start_completed) { throw 'Stopped-to-started observation ordering differs.' }
    Assert-StratusClosureRecord $ps.start_proof 'PreStart start proof' -Booleans @('start_owned_by_transition_process') -Nulls @('successor_started_at', 'docker_start_event_time_nano')
    if ($ps.start_proof.start_owned_by_transition_process) { throw 'PreStart cannot already own a completed start.' }
    Assert-StratusClosureRecord $ps.watchdog_handoff 'PreStart watchdog handoff' -Strings @('format', 'session_id', 'owner_start_time_utc',
        'hard_deadline_utc', 'lease_path', 'receipt_path', 'heartbeat_path', 'commit_path', 'live_path', 'prestart_path',
        'start_fence_path', 'start_fence_sha256', 'start_revoke_path', 'start_attempt_path', 'start_completion_path') -Integers @('owner_pid', 'heartbeat_timeout_seconds')
    Assert-StratusClosureRecord $ls.watchdog 'Live watchdog binding' -Strings @('format', 'session_id', 'owner_start_time_utc', 'hard_deadline_utc',
        'lease_path', 'lease_sha256', 'receipt_path', 'heartbeat_path', 'commit_path', 'start_fence_path', 'start_fence_sha256',
        'start_revoke_path', 'start_attempt_path', 'start_attempt_sha256', 'start_completion_path', 'start_completion_sha256') -Integers @(
        'owner_pid', 'heartbeat_timeout_seconds') -Booleans @('start_revoke_absent')
    if ($ps.watchdog_handoff.format -cne 'stratus-successor-transition-watchdog-handoff-v3' -or
        $ls.watchdog.format -cne 'stratus-successor-transition-watchdog-binding-v3' -or -not $ls.watchdog.start_revoke_absent) {
        throw 'PreStart/Live watchdog binding kinds differ.'
    }
    foreach ($binding in @($ps.watchdog_handoff, $ls.watchdog)) {
        Assert-StratusClosureOwner $binding $owner $auth.session_id 'watchdog binding'
        Assert-StratusClosureEqual $binding.hard_deadline_utc $auth.valid_until 'watchdog binding deadline'
        Assert-StratusClosureEqual $binding.heartbeat_timeout_seconds $lease.deadline.heartbeat_timeout_seconds 'watchdog heartbeat limit'
        foreach ($pair in @(@('lease', 'lease'), @('receipt', 'terminal'), @('heartbeat', 'heartbeat'), @('commit', 'commit'),
            @('start_fence', 'fence'), @('start_revoke', 'revoke'), @('start_attempt', 'attempt'), @('start_completion', 'completion'))) {
            Assert-StratusClosureEqual $binding.($pair[0] + '_path') $Expected.paths.($pair[1]) "watchdog binding $($pair[0]) path"
        }
        Assert-StratusClosureEqual $binding.start_fence_sha256 $Packet.fence.sha256 'watchdog fence digest'
    }
    Assert-StratusClosureEqual $ps.watchdog_handoff.live_path $Packet.live.path 'PreStart handoff Live path'
    Assert-StratusClosureEqual $ps.watchdog_handoff.prestart_path $Packet.prestart.path 'PreStart handoff self path'
    foreach ($name in @('lease_sha256', 'start_attempt_sha256', 'start_completion_sha256')) { Assert-StratusClosureEqual $ls.watchdog.$name $auth.$name "Live watchdog $name" }
    Assert-StratusClosureRecord $ls.watchdog_lease 'Live lease projection' -Strings @('path', 'sha256', 'armed_at', 'docker_engine_contract_sha256')
    Assert-StratusClosureEqual $ls.watchdog_lease.path $Packet.lease.path 'Live lease path'
    Assert-StratusClosureEqual $ls.watchdog_lease.sha256 $Packet.lease.sha256 'Live lease digest'
    Assert-StratusClosureEqual $ls.watchdog_lease.armed_at $lease.armed_at 'Live lease arming'
    Assert-StratusClosureRecord $ls.start_proof 'Live completed start proof' -Strings @('successor_started_at', 'start_fence_path', 'start_fence_sha256',
        'start_attempt_path', 'start_attempt_sha256', 'start_completion_path', 'start_completion_sha256') -Integers @('docker_start_event_time_nano') -Booleans @(
        'start_owned_by_transition_process', 'start_revoke_absent') -Objects @('bounded_start')
    if (-not $ls.start_proof.start_owned_by_transition_process -or -not $ls.start_proof.start_revoke_absent) { throw 'Live start ownership/revocation declaration differs.' }
    foreach ($name in @('start_fence_path', 'start_fence_sha256', 'start_attempt_path', 'start_attempt_sha256', 'start_completion_path', 'start_completion_sha256')) {
        Assert-StratusClosureEqual $ls.start_proof.$name $auth.$name "Live start proof $name"
    }
    Assert-StratusClosureEqual $ls.start_proof.successor_started_at $Expected.postgres.started_at 'Live started incarnation'
    Assert-StratusClosureEqual $ls.start_proof.bounded_start $Packet.completion.value.bounded_start 'Live exact bounded start completion'
    Assert-StratusClosureEqual $ls.start_proof.docker_start_event_time_nano $ls.event_witness.postgres_start_time_nano 'Live start event time'
    Assert-StratusClosureRecord $ls.rollback 'Live rollback' -Booleans @('on_failure', 'executed', 'emergency_containment') -Integers @('stop_timeout_seconds')
    if (-not $ls.rollback.on_failure -or $ls.rollback.executed -or $ls.rollback.emergency_containment -or $ls.rollback.stop_timeout_seconds -ne 15) {
        throw 'Live rollback declaration differs.'
    }

    Assert-StratusClosureRecord $Expected.docker_engine 'expected Docker engine' -Strings @('docker_executable_path', 'docker_executable_sha256',
        'compose_executable_path', 'compose_executable_sha256', 'compose_version', 'context', 'endpoint', 'engine_id', 'server_version',
        'engine_name', 'docker_root_dir', 'contract_sha256') -Integers @('docker_executable_bytes', 'compose_executable_bytes') -Booleans @('skip_tls_verify')
    foreach ($engine in @($lease.docker_engine, $terminal.docker_engine)) { Assert-StratusClosureEqual $engine $coreEngine 'successor core Docker engine closure' }
    Assert-StratusClosureEqual $auth.docker_engine_contract_sha256 $Expected.docker_engine.contract_sha256 'authorization Docker contract'
    Assert-StratusClosureEqual $ls.watchdog_lease.docker_engine_contract_sha256 $coreEngine.contract_sha256 'Live lease core Docker contract'
    $armedAt = Get-StratusLineageUtc $lease.armed_at 'successor lease armed time'
    $deadline = Get-StratusLineageUtc $auth.valid_until 'Transition deadline'
    $leaf = Assert-StratusParentTransitionLineageLeaf -FenceDocument $Packet.fence -AttemptDocument $Packet.attempt -CompletionDocument $Packet.completion -Expected ([pscustomobject]@{
        parent_stage = $Expected.parent_stage; parent_armed_at = $Expected.parent_armed_at
        transition_session_id = $auth.session_id; transition_armed_at = $lease.armed_at; transition_deadline_utc = $auth.valid_until
        observed_at = $Expected.observed_at; start_fence_path = $Packet.fence.path; start_fence_sha256 = $Packet.fence.sha256
        start_attempt_path = $Packet.attempt.path; start_attempt_sha256 = $Packet.attempt.sha256
        start_completion_path = $Packet.completion.path; start_completion_sha256 = $Packet.completion.sha256
        postgres_container_id = $Expected.postgres.container_id; docker_executable_path = $Expected.docker_engine.docker_executable_path
        docker_executable_sha256 = $Expected.docker_engine.docker_executable_sha256
    })
    $liveAt = Get-StratusLineageUtc $lp.accepted_at 'Live accepted time'
    $writtenAt = Get-StratusLineageUtc $lp.artifact_written_at 'Live written time'
    $committedAt = Get-StratusLineageUtc $commit.committed_at 'commit time'
    $terminalAt = Get-StratusLineageUtc $terminal.terminal_at 'terminal time'
    $authorizedAt = Get-StratusLineageUtc $auth.authorized_at 'authorization observation time'
    $publishedAt = Get-StratusLineageUtc $auth.publication_started_at 'authorization publication time'
    $completedAt = Get-StratusLineageUtc $Packet.completion.value.completed_at 'native start completion time'
    if ($preAt -lt $parentArmed.AddSeconds(-2) -or $armedAt -lt $preAt -or $liveAt -lt $completedAt.AddSeconds(-2) -or
        $writtenAt -lt $liveAt -or $committedAt -lt $writtenAt -or $terminalAt -lt $committedAt -or
        $authorizedAt -lt $terminalAt.AddSeconds(-5) -or $publishedAt -lt $authorizedAt.AddSeconds(-5) -or
        $publishedAt -gt $authorizedAt.AddSeconds(5) -or $committedAt -gt $deadline -or $terminalAt -gt $deadline.AddSeconds(5) -or
        $publishedAt -gt $deadline -or $observed -lt $publishedAt.AddSeconds(-2) -or $observed -ge $deadline) {
        throw 'Full Transition publication chronology or current validity differs.'
    }

    # Historical immutable input hashes come from the separately held R1 input
    # inventory. Cross-envelope agreement alone must not invent that trust root.
    Assert-StratusClosureRecord $Expected.historical_inputs 'expected held historical input maps' -Objects @('backup_sha256', 'lineage_sha256')
    Assert-StratusClosureRecord $pre.historical 'PreStart history' -Strings @('pre_migration_attestation_sha256', 'pre_migration_payload_sha256',
        'predecessor_revision', 'predecessor_image', 'predecessor_image_id') -Objects @('lineage_sha256', 'backup')
    Assert-StratusClosureRecord $lp.historical 'Live history' -Strings @('pre_migration_attestation_sha256', 'pre_migration_payload_sha256',
        'predecessor_revision', 'predecessor_image_id') -Objects @('backup')
    foreach ($name in @('pre_migration_attestation_sha256', 'pre_migration_payload_sha256', 'predecessor_revision', 'predecessor_image_id', 'backup')) {
        Assert-StratusClosureEqual $lp.historical.$name $pre.historical.$name "historical $name closure"
    }
    Assert-StratusClosureEqual $pre.historical.lineage_sha256 $Expected.historical_inputs.lineage_sha256 'R1 historical lineage input inventory'
    if ($pre.historical.pre_migration_attestation_sha256 -cne 'a8717ba0656bc0f5abde739dd0352284f48f598f89597866c55bc7627d97d189' -or
        $pre.historical.pre_migration_payload_sha256 -cne 'd1ad1ed1511653cc394b70a118ca41b8751c9504cd0fdd449936868a5045eabd' -or
        $pre.historical.predecessor_revision -cne '6d093b49cd5397d1e2bdb41fb4640d3e59127281' -or
        $pre.historical.predecessor_image -cne 'stratus:candidate-6d093b49cd53' -or
        $pre.historical.predecessor_image_id -cne 'sha256:3d8c2904224328efb5604a28778c138abf26d8f43a06c02c733c8ad270ee2d24') {
        throw 'Historical predecessor differs from the fixed accepted lineage.'
    }
    Assert-StratusClosureRecord $pre.historical.backup 'historical backup metadata' -Strings @('manifest_sha256', 'archive_sha256',
        'catalog_sha256', 'restore_attestation_sha256') -Integers @('archive_bytes', 'catalog_entries')
    foreach ($pair in @(@('manifest_sha256', 'manifest.txt'), @('archive_sha256', 'stratus.dump'),
        @('catalog_sha256', 'catalog.list'), @('restore_attestation_sha256', 'restore-attestation.json'))) {
        Assert-StratusLineageHash $Expected.historical_inputs.backup_sha256.($pair[1]) "R1 historical $($pair[1]) digest"
        Assert-StratusClosureEqual $pre.historical.backup.($pair[0]) $Expected.historical_inputs.backup_sha256.($pair[1]) "R1 backup $($pair[0])"
    }
    Assert-StratusClosureEqual $pre.historical.pre_migration_attestation_sha256 $Expected.historical_inputs.backup_sha256.'pre-migration-attestation.json' 'R1 pre-migration attestation digest'
    if ($pre.historical.backup.archive_bytes -le 0 -or $pre.historical.backup.catalog_entries -le 0 -or
        $pre.historical.backup.restore_attestation_sha256 -cne '73939f689e3add8b92ba72cb4824a1c2243fa35228eaed25ffc5dab4144a3e4e') {
        throw 'Historical backup metadata/restore binding differs.'
    }
    Assert-StratusClosureRecord $pre.closed_contract 'closed source contract' -Strings @('git_executable_path', 'git_executable_sha256',
        'git_sha256', 'image_sha256') -Integers @('git_executable_bytes', 'git_entries', 'image_files') -Booleans @('predecessor_contract_equals_successor')
    Assert-StratusLineagePath $pre.closed_contract.git_executable_path 'closed contract Git path'
    foreach ($name in @('git_executable_sha256', 'git_sha256', 'image_sha256')) { Assert-StratusLineageHash $pre.closed_contract.$name "closed contract $name" }
    if (-not $pre.closed_contract.predecessor_contract_equals_successor -or $pre.closed_contract.git_executable_bytes -le 0 -or
        $pre.closed_contract.git_entries -le 0 -or $pre.closed_contract.image_files -le 0) { throw 'Closed source contract is incomplete.' }
    Assert-StratusClosureRecord $pre.compose_migrate 'read-only rendered migration contract' -Strings @('restart', 'dependency', 'image', 'image_id') -Arrays @('command') -Booleans @('credential_values_persisted')
    Assert-StratusClosureEqual $pre.compose_migrate.command @('alembic', 'upgrade', 'head') 'rendered migration command'
    if ($pre.compose_migrate.restart -cne 'no' -or $pre.compose_migrate.dependency -cne 'postgres:service_healthy' -or
        $pre.compose_migrate.image -cne $Expected.candidate_image -or $pre.compose_migrate.image_id -cne $Expected.candidate_image_id -or
        $pre.compose_migrate.credential_values_persisted) { throw 'Rendered migration proof differs; it never permits migration execution.' }

    Assert-StratusClosurePostgresSnapshot $pre.postgres_prestart $Expected.postgres $false 'PreStart PostgreSQL'
    foreach ($name in @('container_id', 'image_id', 'config_image', 'restart_count', 'started_at', 'finished_at')) {
        Assert-StratusClosureEqual $lease.postgres.$name $pre.postgres_prestart.$name "lease/PreStart PostgreSQL $name"
    }
    $pg = $lp.postgres_transition
    Assert-StratusClosureRecord $pg 'Live PostgreSQL transition' -Strings @('container_id', 'image_id', 'config_image', 'previous_started_at',
        'previous_finished_at', 'successor_started_at', 'state', 'restart_policy', 'health_status', 'network_mode', 'mount_contract_sha256',
        'ports_contract_sha256', 'network_contract_sha256', 'active_endpoint_sha256') -Integers @('restart_count') -Booleans @('oom_killed') -Objects @('health')
    foreach ($name in @('container_id', 'image_id', 'config_image', 'restart_count')) { Assert-StratusClosureEqual $pg.$name $Expected.postgres.$name "Live PostgreSQL $name" }
    Assert-StratusClosureEqual $pg.previous_started_at $pre.postgres_prestart.started_at 'predecessor PostgreSQL start time'
    Assert-StratusClosureEqual $pg.previous_finished_at $pre.postgres_prestart.finished_at 'predecessor PostgreSQL finish time'
    Assert-StratusClosureEqual $pg.successor_started_at $Expected.postgres.started_at 'current PostgreSQL start time'
    foreach ($name in @('network_mode', 'mount_contract_sha256', 'ports_contract_sha256')) { Assert-StratusClosureEqual $pg.$name $pre.postgres_prestart.$name "unchanged PostgreSQL $name" }
    foreach ($name in @('mount_contract_sha256', 'ports_contract_sha256', 'network_contract_sha256', 'active_endpoint_sha256')) { Assert-StratusLineageHash $pg.$name "Live PostgreSQL $name" }
    Assert-StratusClosureRecord $pg.health 'Live PostgreSQL health' -Strings @('status', 'checked_at')
    if ($pg.state -cne 'running' -or $pg.restart_policy -cne 'no' -or $pg.oom_killed -or $pg.health_status -cne 'healthy' -or
        $pg.health.status -cne 'healthy' -or $pg.restart_count -ne 0) { throw 'Live PostgreSQL is not the exact healthy fail-closed state.' }
    foreach ($topology in @($pre.topology, $lp.topology)) {
        Assert-StratusClosureRecord $topology 'Transition topology' -Integers @('running_count') -Booleans @('postgres_only', 'application_writers_running')
        if ($topology.application_writers_running) { throw 'Transition topology includes an application writer.' }
    }
    if ($pre.topology.running_count -ne 0 -or $pre.topology.postgres_only -or $lp.topology.running_count -ne 1 -or -not $lp.topology.postgres_only) {
        throw 'Stopped-to-PostgreSQL-only topology differs.'
    }
    Assert-StratusClosureRecord $Expected.hba 'expected held HBA' -Strings @('path', 'sha256') -Integers @('bytes')
    if ($Expected.hba.path -cne ($Expected.root + '\ops\postgres\stratus-pg_hba.conf') -or
        $Expected.hba.sha256 -cne '164276f8a7337705c3933e3201cb53a5a10e354ec7627768c4ac8b3e951380a4' -or $Expected.hba.bytes -le 0) {
        throw 'Expected held HBA does not equal the approved fixed bytes.'
    }
    Assert-StratusClosureRecord $pre.hba_host_read_lock 'PreStart HBA read lock' -Strings @('path', 'sha256', 'access', 'share') -Integers @('bytes') -Booleans @('held', 'write_share_permitted', 'delete_share_permitted')
    foreach ($name in @('path', 'sha256', 'bytes')) { Assert-StratusClosureEqual $pre.hba_host_read_lock.$name $Expected.hba.$name "PreStart HBA $name" }
    if (-not $pre.hba_host_read_lock.held -or $pre.hba_host_read_lock.access -cne 'Read' -or $pre.hba_host_read_lock.share -cne 'Read' -or
        $pre.hba_host_read_lock.write_share_permitted -or $pre.hba_host_read_lock.delete_share_permitted) { throw 'PreStart HBA lock declaration differs.' }
    $hba = $lp.hba
    Assert-StratusClosureRecord $hba 'Live HBA' -Strings @('host_sha256', 'host_read_lock_path', 'host_read_lock_access', 'host_read_lock_share',
        'mounted_sha256', 'loaded_inventory_sha256', 'conf_load_time_before', 'reload_requested_at', 'conf_load_time_after',
        'reload_acknowledged_at', 'postmaster_started_at', 'proof_backend_started_at', 'hba_file') -Integers @('loaded_line_count',
        'loaded_rule_count', 'loaded_rule_errors', 'reload_poll_count', 'proof_backend_pid') -Booleans @('host_read_lock_held',
        'host_write_share_permitted', 'host_delete_share_permitted', 'reload_accepted', 'reload_acknowledged') -Objects @('fresh_loaded_state')
    if ($hba.host_sha256 -cne $Expected.hba.sha256 -or $hba.mounted_sha256 -cne $Expected.hba.sha256 -or $hba.host_read_lock_path -cne $Expected.hba.path -or
        $hba.host_read_lock_access -cne 'Read' -or $hba.host_read_lock_share -cne 'Read' -or -not $hba.host_read_lock_held -or
        $hba.host_write_share_permitted -or $hba.host_delete_share_permitted -or -not $hba.reload_accepted -or -not $hba.reload_acknowledged -or
        $hba.loaded_inventory_sha256 -cne 'e6b26bdffeba11e892100ce05f855db8ede8ce407a24fbef6144d2a458cc3567' -or
        $hba.loaded_line_count -ne 16 -or $hba.loaded_rule_count -ne 15 -or $hba.loaded_rule_errors -ne 0 -or
        $hba.reload_poll_count -lt 1 -or $hba.reload_poll_count -gt 40 -or $hba.proof_backend_pid -le 0 -or
        $hba.hba_file -cne '/etc/postgresql/stratus-pg_hba.conf') { throw 'Live HBA fixed byte/rule/lock proof differs.' }
    $before = Get-StratusLineageUtc $hba.conf_load_time_before 'HBA generation before reload'
    $requested = Get-StratusLineageUtc $hba.reload_requested_at 'HBA reload request'
    $after = Get-StratusLineageUtc $hba.conf_load_time_after 'HBA generation after reload'
    $ack = Get-StratusLineageUtc $hba.reload_acknowledged_at 'HBA reload acknowledgement'
    $backend = Get-StratusLineageUtc $hba.proof_backend_started_at 'HBA proof backend birth'
    $postmaster = Get-StratusLineageUtc $hba.postmaster_started_at 'HBA postmaster birth'
    if ($after -le $before -or $after -lt $requested -or $ack -lt $after -or $backend -gt $requested -or $backend -lt $postmaster) {
        throw 'HBA reload generation chronology differs.'
    }
    Assert-StratusClosureFreshHba $hba.fresh_loaded_state $hba 'Live fresh HBA session'
    $final = $lp.final_boundary
    Assert-StratusClosureRecord $final 'Live final boundary' -Strings @('checked_at', 'hba_host_sha256', 'hba_host_read_lock_path',
        'hba_mounted_sha256') -Booleans @('hba_host_read_lock_held', 'endpoints_unchanged') -Objects @('postgres', 'topology', 'docker_engine', 'fresh_hba_state')
    Assert-StratusClosurePostgresSnapshot $final.postgres $Expected.postgres $true 'final PostgreSQL snapshot'
    foreach ($name in @('container_id', 'image_id', 'config_image', 'restart_count', 'state', 'oom_killed', 'restart_policy', 'health_status',
        'network_mode', 'mount_contract_sha256', 'ports_contract_sha256', 'network_contract_sha256', 'active_endpoint_sha256')) {
        Assert-StratusClosureEqual $final.postgres.$name $pg.$name "final PostgreSQL $name"
    }
    Assert-StratusClosureEqual $final.topology $lp.topology 'final topology'
    Assert-StratusClosureEqual $final.docker_engine $Expected.docker_engine 'final Docker engine'
    if (-not $final.endpoints_unchanged -or -not $final.hba_host_read_lock_held -or $final.hba_host_sha256 -cne $Expected.hba.sha256 -or
        $final.hba_mounted_sha256 -cne $Expected.hba.sha256 -or $final.hba_host_read_lock_path -cne $Expected.hba.path) { throw 'Final HBA/endpoint boundary differs.' }
    Assert-StratusClosureFreshHba $final.fresh_hba_state $hba 'final fresh HBA session'
    foreach ($name in @('conf_load_time', 'postmaster_started_at', 'hba_file', 'inventory_sha256', 'inventory_line_count', 'inventory_rule_count', 'inventory_errors')) {
        Assert-StratusClosureEqual $final.fresh_hba_state.$name $hba.fresh_loaded_state.$name "final HBA generation $name"
    }
    # The fresh final backend is a new database session, not the earlier backend.
    $finalAt = Get-StratusLineageUtc $final.checked_at 'final boundary time'
    if ($finalAt -lt $ack -or $finalAt -gt $liveAt.AddSeconds(5)) { throw 'Final HBA boundary chronology differs.' }
    $db = $lp.database
    $positiveDatabase = @('indexes_exact', 'functions_exact', 'runtime_boundary', 'safety_boundary', 'control_triggers_exact',
        'commit_triggers_exact', 'identity_triggers_exact', 'fail_closed_controls', 'zero_live_state', 'zero_pending_commands',
        'zero_other_sessions', 'bootstrap_audit_exact', 'fence_armed')
    Assert-StratusClosureRecord $db 'Live database proof' -Strings @('schema_head', 'first_database_clock', 'second_database_clock') -Booleans @(
        $positiveDatabase + @('active_lease_exists', 'execution_authorized'))
    foreach ($name in $positiveDatabase) { if (-not $db.$name) { throw "Live database proof failed: $name" } }
    if ($db.schema_head -cne '20260730_0047' -or $db.active_lease_exists -or $db.execution_authorized) { throw 'Live database safety boundary differs.' }
    $firstClock = Assert-StratusClosurePgClock $db.first_database_clock 'first database clock'
    $secondClock = Assert-StratusClosurePgClock $db.second_database_clock 'second database clock'
    if ($secondClock -lt $firstClock -or $secondClock.Subtract($firstClock).TotalSeconds -gt 60) { throw 'Database snapshot interval differs.' }

    $witness = $auth.authorization_witness
    Assert-StratusClosureRecord $witness 'authorization event witness' -Strings @('format', 'session_id', 'process_started_at',
        'docker_executable_path', 'docker_executable_sha256', 'journal_sha256', 'initial_barrier_id', 'final_barrier_id') -Integers @(
        'process_id', 'reconnect_count', 'journal_bytes', 'event_count', 'initial_barrier_destroy_time_nano',
        'final_barrier_create_time_nano', 'final_barrier_destroy_time_nano', 'unexpected_event_count') -Booleans @('eof_before_seal', 'truncated')
    if ($witness.format -cne 'stratus-successor-transition-authorization-witness-v1' -or $witness.session_id -cnotmatch '^[0-9a-f]{32}$' -or
        $witness.process_id -le 0 -or $witness.process_id -gt [int]::MaxValue -or $witness.reconnect_count -ne 0 -or
        $witness.eof_before_seal -or $witness.truncated -or $witness.journal_bytes -le 0 -or $witness.journal_bytes -gt 8388608 -or
        $witness.event_count -ne 4 -or $witness.unexpected_event_count -ne 0 -or
        $witness.initial_barrier_id -ceq $witness.final_barrier_id -or
        $witness.initial_barrier_destroy_time_nano -ge $witness.final_barrier_create_time_nano -or
        $witness.final_barrier_create_time_nano -ge $witness.final_barrier_destroy_time_nano) { throw 'Authorization event-witness barrier closure differs.' }
    # This collector has a separately generated session; it is not the main
    # Transition session or the independent parent's session.
    foreach ($name in @('journal_sha256', 'initial_barrier_id', 'final_barrier_id')) { Assert-StratusLineageHash $witness.$name "authorization witness $name" }
    Assert-StratusClosureEqual $witness.docker_executable_path $Expected.docker_engine.docker_executable_path 'authorization witness Docker path'
    Assert-StratusClosureEqual $witness.docker_executable_sha256 $Expected.docker_engine.docker_executable_sha256 'authorization witness Docker digest'
    Assert-StratusClosureEqual $auth.docker_state_observed_through_time_nano $witness.final_barrier_destroy_time_nano 'authorization final event timestamp'
    $nano = $witness.final_barrier_destroy_time_nano
    $milliseconds = [int64][Math]::Floor(([double]$nano) / 1000000.0)
    $projected = [DateTimeOffset]::FromUnixTimeMilliseconds($milliseconds).AddTicks([int64][Math]::Floor(
        ([double]($nano - ($milliseconds * 1000000))) / 100.0))
    if ($projected -ne $authorizedAt -or (Get-StratusLineageUtc $witness.process_started_at 'authorization collector birth') -gt $authorizedAt) {
        throw 'Authorization observation is not the exact producer event-time projection.'
    }
    $events = $ls.event_witness
    Assert-StratusClosureRecord $events 'main Transition event witness' -Strings @('session_id', 'process_started_at', 'docker_executable_path',
        'docker_executable_sha256', 'collector_type_name', 'collector_contract_identity', 'collector_template_sha256', 'collector_source_sha256',
        'collector_assembly_full_name_sha256', 'collector_module_version_id', 'journal_sha256') -Integers @('process_id', 'docker_executable_bytes',
        'journal_bytes', 'event_count', 'postgres_start_count', 'postgres_start_time_nano', 'postgres_restart_policy_update_count',
        'postgres_restart_policy_update_time_nano', 'postgres_attach_count', 'network_connect_count', 'data_volume_mount_count',
        'data_volume_mount_time_nano', 'gate_exec_triplets', 'health_exec_triplets', 'healthy_status_event_count', 'healthy_status_time_nano',
        'reconnect_count') -Booleans @('collector_assembly_is_dynamic', 'eof_before_seal', 'truncated') -Objects @('gate_exec_classes',
        'initial_barrier', 'final_barrier') -Arrays @('network_ids', 'gate_exec_sequence')
    if ($events.session_id -cne $auth.session_id -or $events.process_id -ne $ps.collector_process_id -or
        $events.process_started_at -cne $ps.collector_process_started_at -or $events.process_id -le 0 -or
        $events.reconnect_count -ne 0 -or $events.eof_before_seal -or $events.truncated -or $events.journal_bytes -le 0 -or
        $events.journal_bytes -gt 8388608 -or $events.event_count -lt 1 -or $events.event_count -gt 4096 -or
        $events.postgres_start_count -ne 1 -or $events.postgres_restart_policy_update_count -ne 1 -or
        $events.postgres_attach_count -ne 0 -or $events.network_connect_count -ne 2 -or $events.network_ids.Count -ne 2 -or
        $events.data_volume_mount_count -ne 1 -or $events.gate_exec_triplets -ne 8 -or $events.health_exec_triplets -lt 1 -or
        $events.health_exec_triplets -gt 64 -or $events.healthy_status_event_count -ne 1 -or $events.gate_exec_sequence.Count -ne 8) {
        throw 'Main Transition event count/session/collector closure differs.'
    }
    foreach ($name in @('docker_executable_path', 'docker_executable_sha256', 'docker_executable_bytes')) {
        Assert-StratusClosureEqual $events.$name $Expected.docker_engine.$name "event collector $name"
    }
    foreach ($name in @('journal_sha256', 'collector_template_sha256', 'collector_source_sha256', 'collector_assembly_full_name_sha256')) {
        Assert-StratusLineageHash $events.$name "event collector $name"
    }
    foreach ($id in $events.network_ids) { Assert-StratusLineageHash $id 'event network ID' }
    if ($events.network_ids[0] -ceq $events.network_ids[1]) { throw 'Event witness duplicates a network ID.' }
    Assert-StratusClosureEqual $events.initial_barrier $ps.initial_barrier 'continuous initial event barrier'
    Assert-StratusClosureEqual $witness.initial_barrier_id $events.final_barrier.container_id 'overlapping authorization barrier ID'
    Assert-StratusClosureEqual $witness.initial_barrier_destroy_time_nano $events.final_barrier.destroy_time_nano 'overlapping authorization barrier timestamp'
    foreach ($barrier in @($events.initial_barrier, $events.final_barrier)) {
        Assert-StratusClosureRecord $barrier 'main event barrier' -Strings @('phase', 'container_id', 'prefix_sha256') -Integers @(
            'create_time_nano', 'destroy_time_nano', 'observed_event_count', 'prefix_length')
        Assert-StratusLineageHash $barrier.container_id 'barrier container ID'
        Assert-StratusLineageHash $barrier.prefix_sha256 'barrier journal prefix hash'
        if ($barrier.create_time_nano -le 0 -or $barrier.destroy_time_nano -le $barrier.create_time_nano -or
            $barrier.prefix_length -le 0 -or $barrier.prefix_length -gt $events.journal_bytes -or
            $barrier.observed_event_count -le 0 -or $barrier.observed_event_count -gt $events.event_count) { throw 'Main barrier chronology/journal bounds differ.' }
    }
    Assert-StratusClosureEqual $ps.accepted_journal_prefix_bytes $ps.initial_barrier.prefix_length 'PreStart accepted journal prefix bytes'
    Assert-StratusClosureEqual $ps.accepted_journal_prefix_sha256 $ps.initial_barrier.prefix_sha256 'PreStart accepted journal prefix digest'
    $liveProjected = [DateTimeOffset]::FromUnixTimeMilliseconds([int64][Math]::Floor(([double]$events.final_barrier.destroy_time_nano) / 1000000.0))
    if ($liveAt -ne $liveProjected -or $events.initial_barrier.phase -cne 'prestart' -or $events.final_barrier.phase -cne 'final') {
        throw 'Live acceptance is not the exact final barrier time/phase projection.'
    }
    if ($events.initial_barrier.container_id -ceq $events.final_barrier.container_id -or
        $events.postgres_restart_policy_update_time_nano -le $events.initial_barrier.destroy_time_nano -or
        $events.data_volume_mount_time_nano -le $events.postgres_restart_policy_update_time_nano -or
        $events.postgres_start_time_nano -le $events.data_volume_mount_time_nano -or
        $events.healthy_status_time_nano -le $events.postgres_start_time_nano -or
        $events.healthy_status_time_nano -ge $events.final_barrier.create_time_nano) { throw 'Main PostgreSQL lifecycle event ordering differs.' }
    Assert-StratusClosureEqual $events.gate_exec_classes ([pscustomobject]@{ sha256 = 3; reload = 1; fresh_hba = 2; snapshot = 2 }) 'exact database operation event classes'
    $classes = @('sha256', 'reload', 'fresh_hba', 'sha256', 'snapshot', 'snapshot', 'fresh_hba', 'sha256')
    $lastEventTime = $events.postgres_start_time_nano
    for ($index = 0; $index -lt $classes.Count; $index++) {
        $step = $events.gate_exec_sequence[$index]
        Assert-StratusClosureRecord $step 'database event sequence item' -Strings @('class') -Integers @('create_time_nano')
        if ($step.class -cne $classes[$index] -or $step.create_time_nano -le $lastEventTime -or
            $step.create_time_nano -ge $events.final_barrier.create_time_nano) { throw 'Exact database operation event sequence differs.' }
        $lastEventTime = $step.create_time_nano
    }
    Assert-StratusClosureRecord $ls.heartbeat_at_live 'Live heartbeat projection' -Strings @('written_at', 'sha256') -Integers @('sequence')
    Assert-StratusLineageHash $ls.heartbeat_at_live.sha256 'Live heartbeat digest'
    $heartbeatAt = Get-StratusLineageUtc $ls.heartbeat_at_live.written_at 'Live heartbeat time'
    if ($ls.heartbeat_at_live.sequence -le 0 -or $heartbeatAt -lt $armedAt -or $heartbeatAt -gt $writtenAt.AddSeconds(5) -or
        $writtenAt.Subtract($heartbeatAt).TotalSeconds -gt 15) { throw 'Live heartbeat projection is outside the exact active lease interval.' }

    $repair = $Packet.repair.value; $network = $pre.network_repair
    Assert-StratusClosureEnvelope $repair 'stratus-stopped-postgres-safety-plane-repair-v2' @('accepted_at', 'disposition', 'fixed_identity',
        'docker_engine', 'event_witness', 'snapshots', 'comparisons', 'mutation', 'authorization') 'stopped network repair'
    Assert-StratusClosureRecord $network 'network repair binding' -Strings @('path', 'sha256', 'payload_sha256', 'accepted_at', 'expires_at',
        'disposition', 'post_first_sha256', 'post_second_sha256', 'final_attachment_sha256', 'docker_engine_contract_sha256')
    Assert-StratusClosureEqual $network.path $Packet.repair.path 'repair source path'
    Assert-StratusClosureEqual $network.sha256 $Packet.repair.sha256 'repair raw source digest'
    Assert-StratusClosureEqual $network.payload_sha256 $repair.payload_sha256 'repair payload digest'
    foreach ($name in @('accepted_at', 'disposition')) { Assert-StratusClosureEqual $network.$name $repair.payload.$name "repair $name" }
    Assert-StratusClosureEqual $repair.payload.docker_engine $coreEngine 'repair core engine context'
    Assert-StratusClosureEqual $network.docker_engine_contract_sha256 $coreEngine.contract_sha256 'repair core engine digest'
    Assert-StratusClosureRecord $repair.payload.snapshots 'repair snapshots' -Objects @('pre', 'armed', 'post_first', 'post_second')
    Assert-StratusClosureEqual $network.post_first_sha256 $repair.payload.snapshots.post_first.canonical_sha256 'repair first snapshot digest'
    Assert-StratusClosureEqual $network.post_second_sha256 $repair.payload.snapshots.post_second.canonical_sha256 'repair second snapshot digest'
    Assert-StratusClosureEqual $network.post_first_sha256 $network.post_second_sha256 'repair stable snapshot digests'
    Assert-StratusClosureEqual $network.final_attachment_sha256 $repair.payload.snapshots.post_second.postgres.attachments_sha256 'repair final attachment digest'
    Assert-StratusClosureRecord $repair.payload.fixed_identity 'repair fixed identity' -Strings @('postgres_container_id', 'postgres_container_name',
        'default_network_id', 'safety_network_id')
    if ($repair.payload.fixed_identity.postgres_container_id -cne $Expected.postgres.container_id -or
        $repair.payload.fixed_identity.postgres_container_name -cne 'weatherpoly-postgres-1') { throw 'Repair retained PostgreSQL identity differs.' }
    foreach ($name in @('default_network_id', 'safety_network_id')) {
        Assert-StratusLineageHash $repair.payload.fixed_identity.$name "repair $name"
        if ($repair.payload.fixed_identity.$name -cnotin $events.network_ids) { throw 'Live event networks do not close over repaired networks.' }
    }
    if ($repair.payload.fixed_identity.default_network_id -ceq $repair.payload.fixed_identity.safety_network_id) { throw 'Repair network identities alias.' }
    $comparisons = @('pre_equals_armed', 'post_first_equals_post_second', 'docker_engine_unchanged',
        'all_container_lifecycle_inventory_unchanged', 'continuous_docker_event_history_exact', 'postgres_stopped_identity_unchanged',
        'production_network_identity_unchanged', 'post_attachment_admissible_twice')
    Assert-StratusClosureRecord $repair.payload.comparisons 'repair comparisons' -Booleans $comparisons
    foreach ($name in $comparisons) { if (-not $repair.payload.comparisons.$name) { throw "Repair comparison failed: $name" } }
    Assert-StratusClosureRecord $repair.payload.authorization 'repair non-authorization' -Booleans @('postgres_start_permitted',
        'api_start_permitted', 'worker_start_permitted', 'safety_control_start_permitted', 'live_permitted', 'binance_kill_switch_removal_permitted')
    foreach ($property in $repair.payload.authorization.PSObject.Properties) { if ($property.Value) { throw 'Repair grants forbidden authority.' } }
    $repairAt = Get-StratusLineageUtc $network.accepted_at 'repair acceptance time'
    if ($network.expires_at -cne $repairAt.AddSeconds(600).ToString('o') -or $observed -gt $repairAt.AddSeconds(605) -or
        $network.disposition -cnotin @('connected_by_this_invocation', 'verified_configured_pending_existing', 'verified_exact_existing')) {
        throw 'Repair validity/disposition differs.'
    }
    foreach ($name in @('post_first_sha256', 'post_second_sha256', 'final_attachment_sha256')) { Assert-StratusLineageHash $network.$name "repair $name" }
    Assert-StratusClosureRepairGraph $repair.payload $coreEngine $Expected $pre.postgres_prestart $repairAt $preAt

    # This deliberately cannot authorize or populate validatedCompletions. The
    # caller still lacks independent successor native launch/exit capture; a
    # self-consistent packet never supplies that missing provenance capability.
    return [pscustomobject]@{
        format = 'stratus-parent-transition-closure-check-v1'; document_count = 10
        parent_session_id = [string]$Expected.parent_stage.session_id; transition_session_id = [string]$auth.session_id
        authorization_sha256 = [string]$Packet.authorization.sha256; terminal_sha256 = [string]$Packet.terminal.sha256
        commit_sha256 = [string]$Packet.commit.sha256; lease_sha256 = [string]$Packet.lease.sha256
        live_sha256 = [string]$Packet.live.sha256; prestart_sha256 = [string]$Packet.prestart.sha256
        repair_sha256 = [string]$Packet.repair.sha256
        start_fence_sha256 = [string]$Packet.fence.sha256; start_attempt_sha256 = [string]$Packet.attempt.sha256
        start_completion_sha256 = [string]$Packet.completion.sha256
        document_bytes_verified = $false; payload_bytes_verified = $false; native_exit_provenance_verified = $false
        provenance_verified = $false; full_closure_accepted = $false; cache_use_permitted = $false
        authorization = [pscustomobject]@{
            postgres_start_permitted = $false; api_start_permitted = $false; worker_start_permitted = $false
            safety_control_start_permitted = $false; live_transition_permitted = $false
            binance_kill_switch_removal_permitted = $false; migration_container_permitted = $false; container_remove_permitted = $false
        }
    }
}
