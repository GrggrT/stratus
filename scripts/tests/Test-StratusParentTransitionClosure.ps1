param([string] $LibraryPath, [string] $LineagePath)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrEmpty($LibraryPath)) { $LibraryPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'Stratus.ParentTransitionClosure.ps1' }
if ([string]::IsNullOrEmpty($LineagePath)) { $LineagePath = Join-Path (Split-Path $PSScriptRoot -Parent) 'Stratus.ParentTransitionLineage.ps1' }
# Only the two function-only pure libraries are loaded. No gate, watchdog,
# bootstrap runtime, daemon, filesystem fixture or native child is executed.
foreach ($sourcePath in @($LineagePath, $LibraryPath)) {
    $tokens = $null; $parseErrors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($sourcePath, [ref]$tokens, [ref]$parseErrors)
    if (@($parseErrors).Count -ne 0) { throw 'Pure closure library has parser errors.' }
    foreach ($statement in $ast.EndBlock.Statements) {
        if ($statement -isnot [Management.Automation.Language.FunctionDefinitionAst]) { throw 'Pure closure library contains executable top-level statements.' }
    }
    $forbidden = $ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.CommandAst] -and
        ($node.InvocationOperator -ne [Management.Automation.Language.TokenKind]::Unknown -or
         $node.GetCommandName() -cnotmatch '^(Assert-Stratus(?:Lineage|Closure|ParentTransition)[A-Za-z]*|Get-StratusLineageUtc|ForEach-Object|Microsoft\.PowerShell\.Utility\\ConvertTo-Json)$')
    }, $true)
    if (@($forbidden).Count -ne 0) { throw 'Pure closure library contains an unreviewed command.' }
    $allowedTypes = @('Array', 'bool', 'Collections.Generic.HashSet[string]', 'DateTimeOffset', 'Globalization.CultureInfo',
        'int', 'IO.Path', 'long', 'Management.Automation.PSMemberTypes', 'regex', 'string', 'StringComparer', 'TimeSpan',
        'BitConverter', 'Math', 'Security.Cryptography.SHA256', 'StringComparison', 'Text.UTF8Encoding')
    foreach ($type in $ast.FindAll({ param($n) $n -is [Management.Automation.Language.TypeExpressionAst] }, $true)) {
        if ($type.TypeName.FullName -cnotin $allowedTypes) { throw 'Pure closure library names an unreviewed static type.' }
    }
    $allowedMembers = @('Add', 'AddSeconds', 'AddTicks', 'ComputeHash', 'Create', 'Dispose', 'Floor', 'FromUnixTimeMilliseconds',
        'GetBytes', 'GetFullPath', 'GetType', 'IsMatch', 'IsNullOrEmpty', 'new', 'Parse', 'ParseExact', 'Replace', 'StartsWith',
        'Substring', 'Subtract', 'ToLowerInvariant', 'ToString', 'Sort', 'Compare', 'GetByteCount', 'ToUnixTimeSeconds')
    foreach ($member in $ast.FindAll({ param($n) $n -is [Management.Automation.Language.InvokeMemberExpressionAst] }, $true)) {
        if ($member.Member -isnot [Management.Automation.Language.StringConstantExpressionAst] -or
            $member.Member.Value -cnotin $allowedMembers) { throw 'Pure closure library invokes an unreviewed or dynamic member.' }
        if ($member.Static -and $member.Expression -isnot [Management.Automation.Language.TypeExpressionAst]) {
            throw 'Pure closure library dynamically dispatches a static member.'
        }
    }
    . ([scriptblock]::Create($ast.Extent.Text))
}

function Copy-ClosureFixture {
    param([object] $Value)
    $arguments = @{ InputObject = ($Value | ConvertTo-Json -Depth 100 -Compress) }
    if ((Get-Command Microsoft.PowerShell.Utility\ConvertFrom-Json).Parameters.ContainsKey('DateKind')) { $arguments.DateKind = 'String' }
    return Microsoft.PowerShell.Utility\ConvertFrom-Json @arguments
}

function Get-RepairFixtureTextHash {
    param([string] $Text)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash([Text.UTF8Encoding]::new($false).GetBytes($Text))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
}
function Get-RepairFixtureJsonHash {
    param([object] $Value, [int] $Depth)
    return Get-RepairFixtureTextHash (Microsoft.PowerShell.Utility\ConvertTo-Json -InputObject $Value -Depth $Depth -Compress)
}
function Update-RepairFixtureSnapshot {
    param([object] $Snapshot)
    foreach ($name in @('weatherpoly_default', 'weatherpoly_safety-plane')) {
        $network = $Snapshot.production_networks.$name
        $body = [ordered]@{}
        foreach ($field in @('name', 'id', 'compose_name', 'created', 'scope', 'driver', 'subnet', 'gateway', 'enable_ipv4', 'enable_ipv6',
            'internal', 'attachable', 'ingress', 'attached_container_ids', 'labels_sha256', 'options_sha256')) { $body[$field] = $network.$field }
        $network.canonical_sha256 = Get-RepairFixtureJsonHash $body 8
    }
    foreach ($property in @($Snapshot.postgres.attachments.PSObject.Properties)) {
        $attachment = $property.Value; $body = [ordered]@{}
        foreach ($field in @('name', 'configuration_state', 'network_id', 'ipam_configuration', 'driver_options', 'gateway_priority', 'aliases', 'dns_names',
            'endpoint_id', 'gateway', 'ip_address', 'ip_prefix_length', 'ipv6_gateway', 'global_ipv6_address', 'global_ipv6_prefix_length', 'mac_address')) { $body[$field] = $attachment.$field }
        $attachment.canonical_sha256 = Get-RepairFixtureJsonHash $body 8
    }
    $Snapshot.postgres.stable_identity_sha256 = Get-RepairFixtureJsonHash $Snapshot.postgres.stable_identity 10
    $Snapshot.postgres.attachments_sha256 = Get-RepairFixtureJsonHash $Snapshot.postgres.attachments 12
    $Snapshot.lifecycle_inventory_sha256 = Get-RepairFixtureJsonHash $Snapshot.lifecycle_inventory 8
    $Snapshot.production_networks_sha256 = Get-RepairFixtureJsonHash $Snapshot.production_networks 12
    $body = [ordered]@{}
    foreach ($field in @('docker_engine', 'running_container_ids', 'lifecycle_inventory', 'lifecycle_inventory_sha256', 'postgres', 'production_networks', 'production_networks_sha256')) { $body[$field] = $Snapshot.$field }
    $Snapshot.canonical_sha256 = Get-RepairFixtureJsonHash $body 20
}
function New-RepairFixtureSnapshot {
    param([object] $Core, [object] $Expected, [object] $Stopped, [string] $State)
    $networkMetadata = @(
        @('weatherpoly_default', '77551e8d1119201f778fdb90e151ec26f1d9cc23832ba7b88c4db6203e3d8ba2', 'default', '2026-07-05T17:30:21.808081932Z',
            '172.20.0.0/16', '172.20.0.1', '4a06f3121da4b275c077a18cc2b17a4c12258754e48c3ac2d8de10be0b96ad0d'),
        @('weatherpoly_safety-plane', '0d1ad122718694dbc5e6714f69a601a7d429e23a0b74eecdb10ace944e5c8620', 'safety-plane', '2026-07-30T09:19:55.325597748Z',
            '172.22.0.0/16', '172.22.0.1', '44ff0d23c63cad8c3e898bdb1378e958ddcedbcd202055903cd0c7949de103e7'))
    $networks = [ordered]@{}; $attachments = [ordered]@{}
    foreach ($m in $networkMetadata) {
        $networks[$m[0]] = [ordered]@{ name = $m[0]; id = $m[1]; compose_name = $m[2]; created = $m[3]; scope = 'local'; driver = 'bridge'; subnet = $m[4]; gateway = $m[5]
            enable_ipv4 = $true; enable_ipv6 = $false; internal = $false; attachable = $false; ingress = $false; attached_container_ids = @()
            labels_sha256 = $m[6]; options_sha256 = 'e0d52002c06510e7c6f227700d07f412845fc15686f0723a262d8c3e1459f569'; canonical_sha256 = '' }
        if ($State -ceq 'default_only' -and $m[0] -ceq 'weatherpoly_safety-plane') { continue }
        $pending = $State -ceq 'configured_pending' -and $m[0] -ceq 'weatherpoly_safety-plane'
        $attachments[$m[0]] = [ordered]@{ name = $m[0]; configuration_state = $(if ($pending) { 'ConfiguredPending' } else { 'ExactStopped' })
            network_id = $(if ($pending) { '' } else { $m[1] }); ipam_configuration = $(if ($pending) { 'empty_object' } else { 'null' })
            driver_options = $(if ($pending) { 'empty_object' } else { 'null' }); gateway_priority = 0; aliases = @('postgres', 'weatherpoly-postgres-1')
            dns_names = @($Expected.postgres.container_id.Substring(0, 12), 'postgres', 'weatherpoly-postgres-1')
            endpoint_id = ''; gateway = ''; ip_address = ''; ip_prefix_length = 0; ipv6_gateway = ''; global_ipv6_address = ''; global_ipv6_prefix_length = 0; mac_address = ''; canonical_sha256 = '' }
    }
    $stable = [ordered]@{ container_id = $Expected.postgres.container_id; name = '/weatherpoly-postgres-1'; image_id = $Expected.postgres.image_id; config_image = $Expected.postgres.config_image
        command_sha256 = '3e1396d8c083a0274909460787db163a1f9070eeabb659c403c5c7ca890436b3'; compose_config_hash = 'bb02ff39b208731cad693da86ac0f83e76aab0b548eca9a0ae310d35ed2576c1'
        state = 'exited'; running = $false; started_at = $Stopped.started_at; finished_at = $Stopped.finished_at; restart_count = 0; oom_killed = $false; exit_code = 0; network_mode = 'weatherpoly_default'
        hba_sha256 = $Expected.hba.sha256; hba_source = $Expected.hba.path; hba_mode = 'ro'; hba_propagation = 'rprivate'; data_volume = 'weatherpoly_stratus-postgres'; data_volume_driver = 'local'
        data_volume_source = '/var/lib/docker/volumes/weatherpoly_stratus-postgres/_data'; data_volume_mode = 'rw'; data_volume_propagation = ''; published_postgres = '127.0.0.1:5432'; labels_sha256 = 'a' * 64 }
    $entry = [ordered]@{ container_id = $Expected.postgres.container_id; name = $stable.name; state = 'exited'; running = $false; started_at = $Stopped.started_at; finished_at = $Stopped.finished_at
        restart_count = 0; oom_killed = $false; compose_project = 'weatherpoly'; compose_service = 'postgres'
        compose_labels_sha256 = Get-RepairFixtureTextHash "com.docker.compose.project`0weatherpoly`ncom.docker.compose.service`0postgres`n" }
    $snapshot = Copy-ClosureFixture ([ordered]@{ canonical_sha256 = ''; docker_engine = $Core; running_container_ids = @(); lifecycle_inventory = @($entry); lifecycle_inventory_sha256 = ''
        postgres = [ordered]@{ stable_identity = $stable; stable_identity_sha256 = ''; attachment_state = $State; attachments = $attachments; attachments_sha256 = '' }
        production_networks = $networks; production_networks_sha256 = '' })
    Update-RepairFixtureSnapshot $snapshot
    return $snapshot
}
function Update-RepairFixtureEvents {
    param([object] $Witness)
    $lines = @($Witness.events | ForEach-Object { Microsoft.PowerShell.Utility\ConvertTo-Json -InputObject $_ -Depth 8 -Compress })
    $journal = ($lines -join "`n") + "`n"
    $Witness.journal_bytes = [Text.UTF8Encoding]::new($false).GetByteCount($journal)
    $Witness.journal_sha256 = Get-RepairFixtureTextHash $journal
    $Witness.events_sha256 = Get-RepairFixtureJsonHash $Witness.events 8
}
function New-RepairFixtureEvents {
    param([object] $Expected, [bool] $Mutation)
    $base = [DateTimeOffset]::ParseExact('2026-09-15T12:00:00.0000000+00:00', 'o', [Globalization.CultureInfo]::InvariantCulture)
    $volume = 'b' * 64
    $events = @([ordered]@{ type = 'volume'; action = 'create'; actor_id = $volume; attributes = [ordered]@{ driver = 'local' }; scope = 'local'; time = $base.AddSeconds(-39).ToUnixTimeSeconds(); time_nano = [long]($base.AddSeconds(-39).ToUnixTimeMilliseconds() * 1000000) })
    if ($Mutation) { $events += [ordered]@{ type = 'network'; action = 'connect'; actor_id = '0d1ad122718694dbc5e6714f69a601a7d429e23a0b74eecdb10ace944e5c8620'
            attributes = [ordered]@{ container = $Expected.postgres.container_id; name = 'weatherpoly_safety-plane'; type = 'bridge' }; scope = 'local'
            time = $base.AddSeconds(-35).ToUnixTimeSeconds(); time_nano = [long]($base.AddSeconds(-35).ToUnixTimeMilliseconds() * 1000000) } }
    $events += [ordered]@{ type = 'volume'; action = 'destroy'; actor_id = $volume; attributes = [ordered]@{ driver = 'local' }; scope = 'local'; time = $base.AddSeconds(-31).ToUnixTimeSeconds(); time_nano = [long]($base.AddSeconds(-31).ToUnixTimeMilliseconds() * 1000000) }
    $template = '090809477bdd74506256d557f11a2d73a470675de98c41d3f5923735251f51c5'
    $witness = Copy-ClosureFixture ([ordered]@{ format = 'stratus-stopped-repair-docker-event-witness-v1'; session_id = 'e' * 32; process_id = 1350; process_started_at = $base.AddSeconds(-40).ToString('o')
        event_command = @('--context', $Expected.docker_engine.context, 'events', '--format', '{"type":{{json .Type}},"action":{{json .Action}},"actor_id":{{json .Actor.ID}},"attributes":{{json .Actor.Attributes}},"scope":{{json .Scope}},"time":{{.Time}},"time_nano":{{.TimeNano}}}')
        collector = [ordered]@{ type_name = 'StratusRepairDockerCollector_090809477bdd7450'; contract_identity = 'repair-v2:' + $template; template_sha256 = $template
            source_sha256 = '621e556478237ab88a6cb20247953d92569e6f00fd3fa700bfa706c3bcc37a5e'; assembly_full_name_sha256 = 'c' * 64; assembly_is_dynamic = $false; module_version_id = '12345678-1234-5678-9abc-123456789abc' }
        barrier_volume_name = $volume; pre_barrier = [ordered]@{ actor_id = $volume; time = $events[0].time; time_nano = $events[0].time_nano }
        final_barrier = [ordered]@{ actor_id = $volume; time = $events[-1].time; time_nano = $events[-1].time_nano }; authority_as_of_time_nano = $events[-1].time_nano
        journal_bytes = 0; journal_sha256 = ''; event_count = $events.Count; events_sha256 = ''; events = $events; network_connect_count = $(if ($Mutation) { 1 } else { 0 })
        network_connect_time_nano = $(if ($Mutation) { $events[1].time_nano } else { $null }); stderr_bytes = 0; truncated = $false; eof_before_seal = $false; unexpected_event_count = 0 })
    Update-RepairFixtureEvents $witness
    return $witness
}
function Update-RepairFixtureBindings {
    param([object] $Fixture)
    $repair = $Fixture.Packet.repair.value.payload
    foreach ($binding in @($Fixture.Packet.prestart.value.payload.network_repair, $Fixture.Packet.live.value.payload.network_repair)) {
        $binding.disposition = $repair.disposition
        $binding.post_first_sha256 = $repair.snapshots.post_first.canonical_sha256
        $binding.post_second_sha256 = $repair.snapshots.post_second.canonical_sha256
        $binding.final_attachment_sha256 = $repair.snapshots.post_second.postgres.attachments_sha256
    }
}
function Set-RepairFixtureDisposition {
    param([object] $Fixture, [string] $Disposition, [string] $Final = 'configured_pending')
    $repair = $Fixture.Packet.repair.value.payload
    $repair.disposition = $Disposition
    $repair.mutation.issued = $Disposition -ceq 'connected_by_this_invocation'
    $before = if ($repair.mutation.issued) { 'default_only' } elseif ($Disposition -ceq 'verified_exact_existing') { 'exact' } else { 'configured_pending' }
    $after = if ($repair.mutation.issued) { $Final } else { $before }
    foreach ($name in @('pre', 'armed', 'post_first', 'post_second')) {
        $state = if ($name -cin @('pre', 'armed')) { $before } else { $after }
        $repair.snapshots.$name = New-RepairFixtureSnapshot $repair.docker_engine $Fixture.Expected $Fixture.Packet.prestart.value.payload.postgres_prestart $state
    }
    $repair.event_witness = New-RepairFixtureEvents $Fixture.Expected $repair.mutation.issued
    Update-RepairFixtureBindings $Fixture
}

function New-ClosureFixture {
    # Synthetic binding data following the consumed producer schemas, including
    # complete repair snapshots, mutations and event-journal consistency. No
    # synthetic record establishes an actual event capture or repair acceptance.
    # Hashes/PIDs never prove raw bytes, native execution or a running boundary.
    $base = [DateTimeOffset]::ParseExact('2026-09-15T12:00:00.0000000+00:00', 'o', [Globalization.CultureInfo]::InvariantCulture)
    function Stamp([double] $Seconds) { return $base.AddTicks([long]($Seconds * 10000000)).ToString('o') }
    function Nano([double] $Seconds) { return [long]($base.ToUnixTimeMilliseconds() * 1000000 + [long]($Seconds * 1000000000)) }
    function Hash([string] $Character) { return ($Character * 64) }
    function Permissions([bool] $Api = $false) { return [ordered]@{ api_stage_permitted = $Api; worker_start_permitted = $false;
        safety_control_start_permitted = $false; live_transition_permitted = $false; binance_kill_switch_removal_permitted = $false; migration_container_permitted = $false } }
    $root = 'C:\Users\Administrator\Documents\weatherpoly'
    $parentSession = 'a' * 32; $session = 'b' * 32; $authorizationSession = 'c' * 32
    $revision = 'd' * 40; $image = 'sha256:' + (Hash 'e'); $tag = 'stratus:candidate-' + $revision.Substring(0, 12)
    $owner = [ordered]@{ pid = 1200; start_time_utc = Stamp -100; executable = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'; sha256 = Hash 'f' }
    $parentWatchdog = [ordered]@{ pid = 1201; start_time_utc = Stamp -80; executable = $owner.executable; sha256 = $owner.sha256 }
    $pgId = Hash '1'; $pgImage = 'sha256:' + (Hash '2'); $pgConfig = 'postgres:16-alpine@sha256:' + (Hash '2')
    $defaultNetworkId = '77551e8d1119201f778fdb90e151ec26f1d9cc23832ba7b88c4db6203e3d8ba2'
    $safetyNetworkId = '0d1ad122718694dbc5e6714f69a601a7d429e23a0b74eecdb10ace944e5c8620'
    $paths = [ordered]@{}; $packet = [ordered]@{}
    $names = @('authorization', 'terminal', 'commit', 'lease', 'live', 'prestart', 'fence', 'attempt', 'completion', 'repair')
    $chars = @('0', '1', '2', '3', '4', '5', '6', '7', '8', '9')
    for ($i = 0; $i -lt $names.Count; $i++) {
        $paths[$names[$i]] = "$root\.stratus-cache\closure-fixture\$($names[$i]).json"
        $packet[$names[$i]] = [ordered]@{ path = $paths[$names[$i]]; sha256 = Hash $chars[$i]; value = $null }
    }
    $paths.heartbeat = "$root\.stratus-cache\closure-fixture\transition-heartbeat.json"
    $paths.revoke = "$root\.stratus-cache\closure-fixture\start-revoke.json"
    $binding = [ordered]@{ format = 'stratus-parent-stage-binding-v1'; session_id = $parentSession
        contract_path = "$root\.stratus-cache\closure-fixture\parent-contract.json"; contract_sha256 = Hash 'a'
        lease_path = "$root\.stratus-cache\closure-fixture\parent-lease.json"; lease_sha256 = Hash 'b'
        ready_path = "$root\.stratus-cache\closure-fixture\parent-ready.json"; ready_sha256 = Hash 'c'
        heartbeat_path = "$root\.stratus-cache\closure-fixture\parent-heartbeat.json"
        mutation_fence_path = "$root\.stratus-cache\closure-fixture\parent-fence.json"; mutation_fence_sha256 = Hash 'd'
        watchdog = $parentWatchdog; owner = $owner; deadline_utc = Stamp 1200
        r1_anchor = [ordered]@{ directory = "$root\.stratus-cache\closure-fixture\r1.accepted"; acceptance_sha256 = Hash 'a'; evidence_manifest_sha256 = Hash 'b'; sealed_sha256 = Hash 'c' }
        source_manifest_sha256 = Hash 'd' }
    $engine = [ordered]@{ docker_executable_path = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'; docker_executable_sha256 = Hash 'a'; docker_executable_bytes = 1024
        compose_executable_path = 'C:\Program Files\Docker\Docker\resources\cli-plugins\docker-compose.exe'; compose_executable_sha256 = Hash 'b'; compose_executable_bytes = 2048
        compose_version = 'v2.38.2-desktop.1'; context = 'desktop-linux'; endpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'; skip_tls_verify = $false
        engine_id = 'd98e03f5-06d7-4af4-9f21-b6f7a70ebf68'; server_version = '28.3.2'; engine_name = 'docker-desktop'; docker_root_dir = '/var/lib/docker'; contract_sha256 = Hash 'c' }
    # Independently spell the successor/repair producer's core projection. Those
    # producers exclude Compose; full gate/authorization contexts retain Compose.
    $core = [ordered]@{ docker_executable_path = $engine.docker_executable_path; docker_executable_sha256 = $engine.docker_executable_sha256
        docker_executable_bytes = $engine.docker_executable_bytes; context = $engine.context; endpoint = $engine.endpoint; skip_tls_verify = $false
        engine_id = $engine.engine_id; server_version = $engine.server_version; engine_name = $engine.engine_name; docker_root_dir = $engine.docker_root_dir }
    $coreText = (@($core.docker_executable_path, $core.docker_executable_sha256, [string]$core.docker_executable_bytes,
        $core.context, $core.endpoint, 'false', $core.engine_id, $core.server_version, $core.engine_name, $core.docker_root_dir) -join "`0") + "`n"
    $hashAlgorithm = [Security.Cryptography.SHA256]::Create()
    try { $core.contract_sha256 = [BitConverter]::ToString($hashAlgorithm.ComputeHash([Text.UTF8Encoding]::new($false).GetBytes($coreText))).Replace('-', '').ToLowerInvariant() }
    finally { $hashAlgorithm.Dispose() }
    $program = [ordered]@{ script_path = "$root\scripts\Invoke-StratusSuccessorTransitionWatchdog.ps1"; script_sha256 = Hash 'd'; script_bytes = 4096
        host_path = $owner.executable; host_sha256 = $owner.sha256; host_bytes = 8192 }
    $gate = [ordered]@{ script_path = "$root\scripts\Assert-StratusPostMigrationSuccessorGate.ps1"; script_sha256 = Hash 'e'; script_bytes = 16384 }
    $declaredProgram = [ordered]@{}
    foreach ($key in $program.Keys) { $declaredProgram[$key] = $program[$key] }
    $declaredProgram.owner_pid = $owner.pid; $declaredProgram.owner_start_time_utc = $owner.start_time_utc; $declaredProgram.read_locks_held = $true
    $expected = [ordered]@{ root = $root; parent_stage = $binding; parent_armed_at = Stamp -60; observed_at = Stamp 25
        revision = $revision; candidate_image = $tag; candidate_image_id = $image
        postgres = [ordered]@{ container_id = $pgId; image_id = $pgImage; config_image = $pgConfig; restart_count = 0; started_at = Stamp 10 }
        docker_engine = $engine; program = $program; gate_program = $gate; paths = $paths
        registration = [ordered]@{ parent_authorization_sha256 = $packet.authorization.sha256; transition_terminal_sha256 = $packet.terminal.sha256; postgres_started_at = Stamp 10 }
        terminal_stdout = [ordered]@{ sha256 = Hash 'f'; bytes = 1000 }
        hba = [ordered]@{ path = "$root\ops\postgres\stratus-pg_hba.conf"; sha256 = '164276f8a7337705c3933e3201cb53a5a10e354ec7627768c4ac8b3e951380a4'; bytes = 1234 }
        historical_inputs = [ordered]@{ backup_sha256 = [ordered]@{ 'manifest.txt' = Hash 'a'; 'stratus.dump' = Hash 'b'; 'catalog.list' = Hash 'c'
            'restore-attestation.json' = '73939f689e3add8b92ba72cb4824a1c2243fa35228eaed25ffc5dab4144a3e4e'
            'pre-migration-attestation.json' = 'a8717ba0656bc0f5abde739dd0352284f48f598f89597866c55bc7627d97d189' }
            lineage_sha256 = [ordered]@{ '0047-proof.json' = Hash 'd' } } }
    $bounded = [ordered]@{ command = @('start', $pgId); docker_executable_path = $engine.docker_executable_path; docker_executable_sha256 = $engine.docker_executable_sha256
        process_timeout_milliseconds = 15000; stdout_maximum_bytes = 4096; stderr_maximum_bytes = 4096; requested_at = Stamp 10; completed_at = Stamp 11
        exit_code = 0; output_container_id = $pgId; timed_out = $false; output_exceeded = $false; process_started = $true; process_exited = $true; process_id = 1301; kill_issued = $false }
    $packet.fence.value = [ordered]@{ format = 'stratus-successor-transition-start-fence-v1'; session_id = $session; owner_pid = $owner.pid; owner_start_time_utc = $owner.start_time_utc; created_at = Stamp -2 }
    $packet.attempt.value = [ordered]@{ format = 'stratus-successor-transition-start-attempt-v1'; session_id = $session; owner_pid = $owner.pid; owner_start_time_utc = $owner.start_time_utc
        start_fence_path = $paths.fence; start_fence_sha256 = $packet.fence.sha256; command = @('start', $pgId); issued_at = Stamp 10 }
    $packet.completion.value = [ordered]@{ format = 'stratus-successor-transition-start-complete-v1'; session_id = $session; owner_pid = $owner.pid; owner_start_time_utc = $owner.start_time_utc
        start_attempt_path = $paths.attempt; start_attempt_sha256 = $packet.attempt.sha256; outcome = 'started'; completed_at = Stamp 11; bounded_start = $bounded }
    $packet.lease.value = [ordered]@{ format = 'stratus-successor-transition-watchdog-lease-v3'; armed = $true; armed_at = Stamp 5; session_id = $session
        watchdog = [ordered]@{ process_id = 1300; process_started_at = Stamp 4 }; owner = [ordered]@{ process_id = $owner.pid; process_started_at = $owner.start_time_utc }
        deadline = [ordered]@{ hard_deadline_utc = Stamp 300; heartbeat_timeout_seconds = 15; maximum_lease_seconds = 600 }
        artifacts = [ordered]@{ lease_path = $paths.lease; receipt_path = $paths.terminal; heartbeat_path = $paths.heartbeat; commit_path = $paths.commit
            live_path = $paths.live; prestart_path = $paths.prestart; prestart_sha256 = $packet.prestart.sha256; prestart_payload_sha256 = Hash 'a'
            start_fence_path = $paths.fence; start_fence_sha256 = $packet.fence.sha256; start_revoke_path = $paths.revoke; start_attempt_path = $paths.attempt; start_completion_path = $paths.completion }
        docker_engine = $core; postgres = [ordered]@{ container_id = $pgId; image_id = $pgImage; config_image = $pgConfig; restart_count = 0
            restart_policy_before = 'unless-stopped'; restart_policy_armed = 'no'; state = 'exited'; started_at = Stamp -10000; finished_at = Stamp -500 }
        containment = [ordered]@{ stop_timeout_seconds = 15; stop_wall_clock_timeout_seconds = 25; exact_container_only = $true; ambiguous_start_requires_causal_resolution = $true } }
    $packet.commit.value = [ordered]@{ format = 'stratus-successor-transition-watchdog-commit-v1'; session_id = $session; owner_pid = $owner.pid; owner_start_time_utc = $owner.start_time_utc
        lease_path = $paths.lease; lease_sha256 = $packet.lease.sha256; live_path = $paths.live; live_sha256 = $packet.live.sha256; committed_at = Stamp 22 }
    $packet.terminal.value = [ordered]@{ format = 'stratus-successor-transition-watchdog-terminal-v1'; status = 'success'; terminal_at = Stamp 23
        session_id = $session; owner_pid = $owner.pid; owner_start_time_utc = $owner.start_time_utc; lease_path = $paths.lease; lease_sha256 = $packet.lease.sha256
        receipt_path = $paths.terminal; commit_path = $paths.commit; live_path = $paths.live; trigger = 'accepted_live_commit'; docker_engine = $core; containment = $null; failure = $null
        commit = [ordered]@{ marker_sha256 = $packet.commit.sha256; committed_at = Stamp 22; live_sha256 = $packet.live.sha256; live_payload_sha256 = Hash 'b'; postgres_started_at = Stamp 10; docker_engine_contract_sha256 = $core.contract_sha256 }
        authorization = Permissions }
    $packet.authorization.value = [ordered]@{ format = 'stratus-successor-transition-parent-authorization-v1'; status = 'authorized'; authorized_at = Stamp 24; publication_started_at = Stamp 24.5; valid_until = Stamp 300
        docker_state_observed_through_time_nano = Nano 24; session_id = $session; owner_pid = $owner.pid; owner_start_time_utc = $owner.start_time_utc
        authorizer = [ordered]@{ gate_script_path = $gate.script_path; gate_script_sha256 = $gate.script_sha256; gate_script_bytes = $gate.script_bytes
            host_executable_path = $program.host_path; host_executable_sha256 = $program.host_sha256; host_executable_bytes = $program.host_bytes; process_id = $owner.pid; process_started_at = $owner.start_time_utc }
        parent_authorization_path = $paths.authorization; watchdog_program_path = $program.script_path; watchdog_program_sha256 = $program.script_sha256
        watchdog_process_id = 1300; watchdog_process_started_at = Stamp 4; watchdog_exit_code = 0; sealed_stdout_sha256 = $expected.terminal_stdout.sha256; sealed_stdout_bytes = 1000
        sealed_stderr_sha256 = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'; sealed_stderr_bytes = 0
        lease_path = $paths.lease; lease_sha256 = $packet.lease.sha256; receipt_path = $paths.terminal; receipt_sha256 = $packet.terminal.sha256
        commit_path = $paths.commit; commit_sha256 = $packet.commit.sha256; live_path = $paths.live; live_sha256 = $packet.live.sha256; live_payload_sha256 = Hash 'b'
        start_fence_path = $paths.fence; start_fence_sha256 = $packet.fence.sha256; start_attempt_path = $paths.attempt; start_attempt_sha256 = $packet.attempt.sha256
        start_completion_path = $paths.completion; start_completion_sha256 = $packet.completion.sha256; docker_engine_contract_sha256 = $engine.contract_sha256
        postgres_container_id = $pgId; postgres_image_id = $pgImage; postgres_config_image = $pgConfig; postgres_restart_count = 0; postgres_started_at = Stamp 10
        authorization_witness = [ordered]@{ format = 'stratus-successor-transition-authorization-witness-v1'; session_id = $authorizationSession; process_id = 1401; process_started_at = Stamp 18.5
            docker_executable_path = $engine.docker_executable_path; docker_executable_sha256 = $engine.docker_executable_sha256; reconnect_count = 0; eof_before_seal = $false; truncated = $false
            journal_bytes = 1000; journal_sha256 = Hash 'd'; event_count = 4; initial_barrier_id = Hash 'b'; initial_barrier_destroy_time_nano = Nano 20
            final_barrier_id = Hash 'c'; final_barrier_create_time_nano = Nano 23.5; final_barrier_destroy_time_nano = Nano 24; unexpected_event_count = 0 }
        parent_stage = $binding; authorization = Permissions $true }
    $initialBarrier = [ordered]@{ phase = 'prestart'; container_id = Hash 'a'; create_time_nano = Nano -5; destroy_time_nano = Nano -4; observed_event_count = 2; prefix_length = 500; prefix_sha256 = Hash 'a' }
    $finalBarrier = [ordered]@{ phase = 'final'; container_id = Hash 'b'; create_time_nano = Nano 19; destroy_time_nano = Nano 20; observed_event_count = 2; prefix_length = 500; prefix_sha256 = Hash 'b' }
    $restart = [ordered]@{ previous = 'unless-stopped'; armed = 'no'; command = @('update', '--restart=no', $pgId); command_issued = $true; stopped_identity_rechecked = $true; restore_on_success = $false }
    $handoff = [ordered]@{ format = 'stratus-successor-transition-watchdog-handoff-v3'; session_id = $session; owner_pid = $owner.pid; owner_start_time_utc = $owner.start_time_utc
        hard_deadline_utc = Stamp 300; heartbeat_timeout_seconds = 15; lease_path = $paths.lease; receipt_path = $paths.terminal; heartbeat_path = $paths.heartbeat; commit_path = $paths.commit
        live_path = $paths.live; prestart_path = $paths.prestart; start_fence_path = $paths.fence; start_fence_sha256 = $packet.fence.sha256; start_revoke_path = $paths.revoke
        start_attempt_path = $paths.attempt; start_completion_path = $paths.completion }
    $watchdogBinding = [ordered]@{ format = 'stratus-successor-transition-watchdog-binding-v3'; session_id = $session; owner_pid = $owner.pid; owner_start_time_utc = $owner.start_time_utc
        hard_deadline_utc = Stamp 300; heartbeat_timeout_seconds = 15; lease_path = $paths.lease; lease_sha256 = $packet.lease.sha256; receipt_path = $paths.terminal; heartbeat_path = $paths.heartbeat; commit_path = $paths.commit
        start_fence_path = $paths.fence; start_fence_sha256 = $packet.fence.sha256; start_revoke_path = $paths.revoke; start_attempt_path = $paths.attempt; start_attempt_sha256 = $packet.attempt.sha256
        start_completion_path = $paths.completion; start_completion_sha256 = $packet.completion.sha256; start_revoke_absent = $true }
    $backup = [ordered]@{ manifest_sha256 = Hash 'a'; archive_sha256 = Hash 'b'; archive_bytes = 10000; catalog_sha256 = Hash 'c'; catalog_entries = 100
        restore_attestation_sha256 = $expected.historical_inputs.backup_sha256.'restore-attestation.json' }
    $history = [ordered]@{ pre_migration_attestation_sha256 = $expected.historical_inputs.backup_sha256.'pre-migration-attestation.json'
        pre_migration_payload_sha256 = 'd1ad1ed1511653cc394b70a118ca41b8751c9504cd0fdd449936868a5045eabd'
        predecessor_revision = '6d093b49cd5397d1e2bdb41fb4640d3e59127281'; predecessor_image = 'stratus:candidate-6d093b49cd53'
        predecessor_image_id = 'sha256:3d8c2904224328efb5604a28778c138abf26d8f43a06c02c733c8ad270ee2d24'; lineage_sha256 = $expected.historical_inputs.lineage_sha256; backup = $backup }
    $liveHistory = [ordered]@{}
    foreach ($key in @('pre_migration_attestation_sha256', 'pre_migration_payload_sha256', 'predecessor_revision', 'predecessor_image_id', 'backup')) { $liveHistory[$key] = $history[$key] }
    $closed = [ordered]@{ git_executable_path = 'C:\Program Files\Git\cmd\git.exe'; git_executable_sha256 = Hash 'a'; git_executable_bytes = 3000; git_sha256 = Hash 'b'; git_entries = 10
        image_sha256 = Hash 'c'; image_files = 10; predecessor_contract_equals_successor = $true }
    $network = [ordered]@{ path = $paths.repair; sha256 = $packet.repair.sha256; payload_sha256 = Hash 'c'; accepted_at = Stamp -30; expires_at = Stamp 570
        disposition = 'verified_exact_existing'; post_first_sha256 = Hash 'd'; post_second_sha256 = Hash 'd'; final_attachment_sha256 = Hash 'e'; docker_engine_contract_sha256 = $core.contract_sha256 }
    $stopped = [ordered]@{ container_id = $pgId; image_id = $pgImage; config_image = $pgConfig; restart_count = 0; started_at = Stamp -10000; finished_at = Stamp -500
        state = 'exited'; oom_killed = $false; exit_code = 0; restart_policy = 'no'; health_status = 'not-running'; network_mode = 'weatherpoly_default'
        mount_contract_sha256 = Hash 'a'; ports_contract_sha256 = Hash 'b'; networks = [ordered]@{}; network_contract_sha256 = Hash 'c'; active_endpoint_sha256 = Hash 'd' }
    $running = [ordered]@{}
    foreach ($key in $stopped.Keys) { $running[$key] = $stopped[$key] }
    $running.started_at = Stamp 10; $running.state = 'running'; $running.health_status = 'healthy'; $running.active_endpoint_sha256 = Hash 'e'
    $pgLive = [ordered]@{ container_id = $pgId; image_id = $pgImage; config_image = $pgConfig; restart_count = 0; previous_started_at = $stopped.started_at; previous_finished_at = $stopped.finished_at
        successor_started_at = Stamp 10; state = 'running'; oom_killed = $false; restart_policy = 'no'; health_status = 'healthy'; health = [ordered]@{ status = 'healthy'; checked_at = Stamp 15 }
        network_mode = $running.network_mode; mount_contract_sha256 = $running.mount_contract_sha256; ports_contract_sha256 = $running.ports_contract_sha256
        network_contract_sha256 = $running.network_contract_sha256; active_endpoint_sha256 = $running.active_endpoint_sha256 }
    $hbaLock = [ordered]@{ held = $true; path = $expected.hba.path; sha256 = $expected.hba.sha256; bytes = $expected.hba.bytes; access = 'Read'; share = 'Read'; write_share_permitted = $false; delete_share_permitted = $false }
    function Fresh([int] $PidValue, [double] $Birth) { return [ordered]@{ conf_load_time = Stamp 13; postmaster_started_at = Stamp 11.2; backend_pid = $PidValue; backend_started_at = Stamp $Birth
        hba_file = '/etc/postgresql/stratus-pg_hba.conf'; inventory_sha256 = 'e6b26bdffeba11e892100ce05f855db8ede8ce407a24fbef6144d2a458cc3567'; inventory_line_count = 16; inventory_rule_count = 15; inventory_errors = 0 } }
    $hba = [ordered]@{ host_sha256 = $expected.hba.sha256; host_read_lock_held = $true; host_read_lock_path = $expected.hba.path; host_read_lock_access = 'Read'; host_read_lock_share = 'Read'
        host_write_share_permitted = $false; host_delete_share_permitted = $false; mounted_sha256 = $expected.hba.sha256
        loaded_inventory_sha256 = 'e6b26bdffeba11e892100ce05f855db8ede8ce407a24fbef6144d2a458cc3567'; loaded_line_count = 16; loaded_rule_count = 15; loaded_rule_errors = 0
        reload_accepted = $true; reload_acknowledged = $true; conf_load_time_before = Stamp 11.25; reload_requested_at = Stamp 12; conf_load_time_after = Stamp 13; reload_acknowledged_at = Stamp 14
        reload_poll_count = 1; postmaster_started_at = Stamp 11.2; proof_backend_pid = 5000; proof_backend_started_at = Stamp 11.3; hba_file = '/etc/postgresql/stratus-pg_hba.conf'; fresh_loaded_state = Fresh 5001 14.1 }
    $topology = [ordered]@{ running_count = 1; postgres_only = $true; application_writers_running = $false }
    $eventSequence = @(); $classes = @('sha256', 'reload', 'fresh_hba', 'sha256', 'snapshot', 'snapshot', 'fresh_hba', 'sha256'); $times = @(11.1, 12, 14.1, 14.2, 16, 17, 18.1, 18.2)
    for ($i = 0; $i -lt 8; $i++) { $eventSequence += [ordered]@{ class = $classes[$i]; create_time_nano = Nano $times[$i] } }
    $events = [ordered]@{ session_id = $session; process_id = 1400; process_started_at = Stamp -11; docker_executable_path = $engine.docker_executable_path
        docker_executable_sha256 = $engine.docker_executable_sha256; docker_executable_bytes = $engine.docker_executable_bytes
        collector_type_name = 'Stratus.Collector'; collector_contract_identity = 'stratus-test-collector'; collector_template_sha256 = Hash 'a'; collector_source_sha256 = Hash 'b'
        collector_assembly_full_name_sha256 = Hash 'c'; collector_assembly_is_dynamic = $false; collector_module_version_id = '11111111-2222-3333-4444-555555555555'
        journal_bytes = 10000; journal_sha256 = Hash 'd'; event_count = 40; postgres_start_count = 1; postgres_start_time_nano = Nano 10
        postgres_restart_policy_update_count = 1; postgres_restart_policy_update_time_nano = Nano -3; postgres_attach_count = 0; network_connect_count = 2
        network_ids = @($defaultNetworkId, $safetyNetworkId); data_volume_mount_count = 1; data_volume_mount_time_nano = Nano 9; gate_exec_triplets = 8
        gate_exec_classes = [ordered]@{ sha256 = 3; reload = 1; fresh_hba = 2; snapshot = 2 }; gate_exec_sequence = $eventSequence
        health_exec_triplets = 1; healthy_status_event_count = 1; healthy_status_time_nano = Nano 15; reconnect_count = 0; eof_before_seal = $false; truncated = $false
        initial_barrier = $initialBarrier; final_barrier = $finalBarrier }
    $prePayload = [ordered]@{ accepted_at = Stamp 0; validity = [ordered]@{ max_age_seconds = 600; postgres_start_window_seconds = 300; expires_at = Stamp 600; postgres_start_deadline = Stamp 300 }
        historical = $history; network_repair = $network; successor = [ordered]@{ revision = $revision; image = $tag; image_id = $image }; closed_contract = $closed
        compose_migrate = [ordered]@{ command = @('alembic', 'upgrade', 'head'); restart = 'no'; dependency = 'postgres:service_healthy'; image = $tag; image_id = $image; credential_values_persisted = $false }
        postgres_prestart = $stopped; hba_host_read_lock = $hbaLock; topology = [ordered]@{ running_count = 0; postgres_only = $false; application_writers_running = $false }
        transition_session = [ordered]@{ mode = 'single-process'; session_id = $session; collector_process_id = 1400; collector_process_started_at = Stamp -11; collector_requested_at = Stamp -10
            docker_engine = $engine; initial_barrier = $initialBarrier; accepted_journal_prefix_bytes = 500; accepted_journal_prefix_sha256 = Hash 'a'; reconnect_permitted = $false
            watchdog_program = $declaredProgram; watchdog_handoff = $handoff; restart_policy_transition = $restart; observation = [ordered]@{ start_command_issued = $false; start_completed = $false }
            start_proof = [ordered]@{ start_owned_by_transition_process = $false; successor_started_at = $null; docker_start_event_time_nano = $null } }
        authorization = [ordered]@{ postgres_start_permitted = $false; external_postgres_start_permitted = $false; transition_process_start_permitted = $true; api_stage_permitted = $false
            worker_start_permitted = $false; safety_control_start_permitted = $false; live_transition_permitted = $false; binance_kill_switch_removal_permitted = $false } }
    $livePayload = [ordered]@{ accepted_at = Stamp 20; artifact_written_at = Stamp 21; historical = $liveHistory; network_repair = $network
        prestart = [ordered]@{ path = $paths.prestart; sha256 = $packet.prestart.sha256; payload_sha256 = Hash 'a'; accepted_at = Stamp 0 }
        successor = $prePayload.successor; closed_contract = $closed; postgres_transition = $pgLive
        transition_session = [ordered]@{ mode = 'single-process'; session_id = $session; docker_engine = $engine; event_witness = $events; watchdog_program = $declaredProgram; watchdog = $watchdogBinding
            watchdog_lease = [ordered]@{ path = $paths.lease; sha256 = $packet.lease.sha256; armed_at = Stamp 5; docker_engine_contract_sha256 = $core.contract_sha256 }
            heartbeat_at_live = [ordered]@{ sequence = 20; written_at = Stamp 20; sha256 = Hash 'a' }; restart_policy_transition = $restart
            observation = [ordered]@{ start_command_issued = $true; start_completed = $true }
            start_proof = [ordered]@{ start_owned_by_transition_process = $true; successor_started_at = Stamp 10; docker_start_event_time_nano = Nano 10
                start_fence_path = $paths.fence; start_fence_sha256 = $packet.fence.sha256; start_attempt_path = $paths.attempt; start_attempt_sha256 = $packet.attempt.sha256
                start_completion_path = $paths.completion; start_completion_sha256 = $packet.completion.sha256; start_revoke_absent = $true; bounded_start = $bounded }
            rollback = [ordered]@{ on_failure = $true; stop_timeout_seconds = 15; executed = $false; emergency_containment = $false } }
        topology = $topology; hba = $hba; final_boundary = [ordered]@{ checked_at = Stamp 18; postgres = $running; topology = $topology; docker_engine = $engine
            hba_host_sha256 = $expected.hba.sha256; hba_host_read_lock_held = $true; hba_host_read_lock_path = $expected.hba.path; hba_mounted_sha256 = $expected.hba.sha256
            fresh_hba_state = Fresh 5002 18; endpoints_unchanged = $true }
        database = [ordered]@{ schema_head = '20260730_0047'; indexes_exact = $true; functions_exact = $true; runtime_boundary = $true; safety_boundary = $true
            control_triggers_exact = $true; commit_triggers_exact = $true; identity_triggers_exact = $true; fail_closed_controls = $true; zero_live_state = $true; zero_pending_commands = $true
            zero_other_sessions = $true; bootstrap_audit_exact = $true; fence_armed = $true; active_lease_exists = $false; execution_authorized = $false
            first_database_clock = '2026-09-15T12:00:16.123456+00:00'; second_database_clock = '2026-09-15T12:00:17.123456+00:00' }
        authorization = Permissions }
    $packet.prestart.value = [ordered]@{ format = 'stratus-post-migration-successor-transition-prestart-v2'; accepted = $true; payload = $prePayload; payload_sha256 = Hash 'a' }
    $packet.live.value = [ordered]@{ format = 'stratus-post-migration-successor-transition-live-v2'; accepted = $true; payload = $livePayload; payload_sha256 = Hash 'b' }
    $repairSnapshot = New-RepairFixtureSnapshot $core $expected $stopped 'exact'
    $repairEvents = New-RepairFixtureEvents $expected $false
    $packet.repair.value = [ordered]@{ format = 'stratus-stopped-postgres-safety-plane-repair-v2'; accepted = $true; payload_sha256 = Hash 'c'
        payload = [ordered]@{ accepted_at = Stamp -30; disposition = 'verified_exact_existing'; fixed_identity = [ordered]@{ postgres_container_id = $pgId; postgres_container_name = 'weatherpoly-postgres-1'
                default_network_id = $defaultNetworkId; safety_network_id = $safetyNetworkId }; docker_engine = $core; event_witness = $repairEvents
            snapshots = [ordered]@{ pre = $repairSnapshot; armed = $repairSnapshot; post_first = $repairSnapshot; post_second = $repairSnapshot }
            comparisons = [ordered]@{ pre_equals_armed = $true; post_first_equals_post_second = $true; docker_engine_unchanged = $true; all_container_lifecycle_inventory_unchanged = $true
                continuous_docker_event_history_exact = $true; postgres_stopped_identity_unchanged = $true; production_network_identity_unchanged = $true; post_attachment_admissible_twice = $true }
            mutation = [ordered]@{ issued = $false; command = @('network', 'connect', '--alias', 'postgres', '--alias', 'weatherpoly-postgres-1', '--gw-priority', '0', $safetyNetworkId, $pgId); output_line_count = 0 }
            authorization = [ordered]@{ postgres_start_permitted = $false; api_start_permitted = $false; worker_start_permitted = $false; safety_control_start_permitted = $false; live_permitted = $false; binance_kill_switch_removal_permitted = $false } } }
    $network.post_first_sha256 = $repairSnapshot.canonical_sha256
    $network.post_second_sha256 = $repairSnapshot.canonical_sha256
    $network.final_attachment_sha256 = $repairSnapshot.postgres.attachments_sha256
    return Copy-ClosureFixture ([ordered]@{ Packet = $packet; Expected = $expected })
}

$results = [Collections.Generic.List[object]]::new()
function Invoke-ClosurePositive {
    param([string] $Name, [scriptblock] $Mutate = {})
    $fixture = New-ClosureFixture
    & $Mutate $fixture
    try { $result = Assert-StratusParentTransitionClosure -Packet $fixture.Packet -Expected $fixture.Expected }
    catch { throw "Positive case $Name failed: $($_.Exception.Message)" }
    if ($result.document_count -ne 10 -or $result.full_closure_accepted -or $result.cache_use_permitted -or
        $result.native_exit_provenance_verified -or $result.document_bytes_verified -or $result.payload_bytes_verified -or $result.provenance_verified -or
        @($result.authorization.PSObject.Properties | Where-Object { $_.Value -ne $false }).Count -ne 0) { throw 'Pure closure result granted authority.' }
    $results.Add([pscustomobject]@{ name = $Name; passed = $true; rejected = $false })
}
function Invoke-ClosureNegative {
    param([string] $Name, [scriptblock] $Mutate, [string] $Message = '.')
    $fixture = New-ClosureFixture
    & $Mutate $fixture
    $failure = $null
    try { [void](Assert-StratusParentTransitionClosure -Packet $fixture.Packet -Expected $fixture.Expected) }
    catch { $failure = $_.Exception.Message }
    if ($null -eq $failure -or $failure -notmatch $Message) { throw "Negative case $Name did not reach its intended rejection: $failure" }
    $results.Add([pscustomobject]@{ name = $Name; passed = $true; rejected = $true; reason = $failure })
}
Invoke-ClosurePositive 'synthetic ten-document graph with complete repair subproof consistency'
Invoke-ClosurePositive 'separate authorization witness session remains valid' { param($f) $f.Packet.authorization.value.authorization_witness.session_id = 'd' * 32 }
Invoke-ClosurePositive 'final fresh HBA backend has its own identity' { param($f) $f.Packet.live.value.payload.final_boundary.fresh_hba_state.backend_pid = 6000 }
Invoke-ClosurePositive 'raw PostgreSQL timestamps with no fraction' { param($f)
    $f.Packet.live.value.payload.database.first_database_clock = '2026-09-15T12:00:16+00:00'
    $f.Packet.live.value.payload.database.second_database_clock = '2026-09-15T12:00:17+00:00' }
Invoke-ClosurePositive 'raw PostgreSQL timestamps with millisecond precision' { param($f)
    $f.Packet.live.value.payload.database.first_database_clock = '2026-09-15T12:00:16.123+00:00'
    $f.Packet.live.value.payload.database.second_database_clock = '2026-09-15T12:00:17.123+00:00' }
Invoke-ClosureNegative 'authorization unknown field' { param($f) $f.Packet.authorization.value | Add-Member bogus $true } 'property set'
Invoke-ClosureNegative 'authorization API flag string' { param($f) $f.Packet.authorization.value.authorization.api_stage_permitted = 'true' } 'native JSON boolean'
Invoke-ClosureNegative 'terminal authority' { param($f) $f.Packet.terminal.value.authorization.worker_start_permitted = $true } 'fail-closed declaration'
Invoke-ClosureNegative 'Live authority' { param($f) $f.Packet.live.value.payload.authorization.live_transition_permitted = $true } 'fail-closed declaration'
Invoke-ClosureNegative 'PreStart external start authority' { param($f) $f.Packet.prestart.value.payload.authorization.external_postgres_start_permitted = $true } 'permission boundary'
Invoke-ClosureNegative 'repair authority' { param($f) $f.Packet.repair.value.payload.authorization.live_permitted = $true } 'forbidden authority'
Invoke-ClosureNegative 'authorization receipt hash mismatch' { param($f) $f.Packet.authorization.value.receipt_sha256 = 'f' * 64 } 'receipt hash'
Invoke-ClosureNegative 'terminal commit hash mismatch' { param($f) $f.Packet.terminal.value.commit.marker_sha256 = 'f' * 64 } 'commit raw hash'
Invoke-ClosureNegative 'commit Live hash mismatch' { param($f) $f.Packet.commit.value.live_sha256 = 'f' * 64 } 'commit live_sha256'
Invoke-ClosureNegative 'lease PreStart hash mismatch' { param($f) $f.Packet.lease.value.artifacts.prestart_sha256 = 'f' * 64 } 'PreStart raw digest'
Invoke-ClosureNegative 'Live PreStart payload hash mismatch' { param($f) $f.Packet.live.value.payload.prestart.payload_sha256 = 'f' * 64 } 'PreStart payload digest'
Invoke-ClosureNegative 'parent R1 spoof' { param($f) $f.Packet.authorization.value.parent_stage.r1_anchor.acceptance_sha256 = 'f' * 64 } 'parent-stage/R1/source'
Invoke-ClosureNegative 'parent source manifest spoof' { param($f) $f.Packet.authorization.value.parent_stage.source_manifest_sha256 = 'f' * 64 } 'parent-stage/R1/source'
Invoke-ClosureNegative 'parent session substituted into commit' { param($f) $f.Packet.commit.value.session_id = $f.Expected.parent_stage.session_id } 'owner/session'
Invoke-ClosureNegative 'main session substituted into parent binding' { param($f) $f.Packet.authorization.value.parent_stage.session_id = $f.Packet.authorization.value.session_id } 'parent-stage/R1/source'
Invoke-ClosureNegative 'watchdog process PID string' { param($f) $f.Packet.authorization.value.watchdog_process_id = '1300' } 'native JSON integer'
Invoke-ClosureNegative 'watchdog process PID owner alias' { param($f) $f.Packet.authorization.value.watchdog_process_id = 1200; $f.Packet.lease.value.watchdog.process_id = 1200 } 'distinct declared process'
Invoke-ClosureNegative 'gate source hash spoof' { param($f) $f.Packet.authorization.value.authorizer.gate_script_sha256 = 'f' * 64 } 'authorizer gate'
Invoke-ClosureNegative 'nonzero declared native exit' { param($f) $f.Packet.authorization.value.watchdog_exit_code = 2 } 'exit/stdout/stderr'
Invoke-ClosureNegative 'stderr appeared' { param($f) $f.Packet.authorization.value.sealed_stderr_bytes = 1 } 'exit/stdout/stderr'
Invoke-ClosureNegative 'stdout digest mismatch' { param($f) $f.Packet.authorization.value.sealed_stdout_sha256 = 'a' * 64 } 'stdout digest'
Invoke-ClosureNegative 'lease maximum changed' { param($f) $f.Packet.lease.value.deadline.maximum_lease_seconds = 601 } 'lease bounds'
Invoke-ClosureNegative 'lease containment weakened' { param($f) $f.Packet.lease.value.containment.ambiguous_start_requires_causal_resolution = $false } 'causal containment'
Invoke-ClosureNegative 'start completion claimed timeout' { param($f) $f.Packet.completion.value.bounded_start.timed_out = $true; $f.Packet.live.value.payload.transition_session.start_proof.bounded_start.timed_out = $true } 'exact positive producer'
Invoke-ClosureNegative 'start command replaced' { param($f) $f.Packet.attempt.value.command[0] = 'restart' } 'exact expected PostgreSQL start'
Invoke-ClosureNegative 'wrong completion incarnation' { param($f) $f.Packet.live.value.payload.transition_session.start_proof.successor_started_at = '2026-09-15T12:00:09.0000000+00:00' } 'started incarnation'
Invoke-ClosureNegative 'repair path aliases commit' { param($f) $f.Expected.paths.repair = $f.Expected.paths.commit } 'aliases another artifact'
Invoke-ClosureNegative 'packet path outside root' { param($f) $f.Expected.paths.repair = 'C:\outside\repair.json' } 'escapes root'
Invoke-ClosureNegative 'packet path aliases parent fence' { param($f) $f.Expected.paths.repair = $f.Expected.parent_stage.mutation_fence_path } 'aliases another artifact'
Invoke-ClosureNegative 'early commit' { param($f) $f.Packet.commit.value.committed_at = '2026-09-15T12:00:20.0000000+00:00'; $f.Packet.terminal.value.commit.committed_at = $f.Packet.commit.value.committed_at } 'publication chronology'
Invoke-ClosureNegative 'expired observation' { param($f) $f.Expected.observed_at = '2026-09-15T12:05:00.0000000+00:00' } 'current validity'
Invoke-ClosureNegative 'longer PreStart window' { param($f) $f.Packet.prestart.value.payload.validity.postgres_start_window_seconds = 301 } 'fixed validity'
Invoke-ClosureNegative 'authorization nano mismatch' { param($f) $f.Packet.authorization.value.docker_state_observed_through_time_nano++ } 'final event timestamp'
Invoke-ClosureNegative 'authorization time not event projection' { param($f) $f.Packet.authorization.value.authorized_at = '2026-09-15T12:00:24.0000001+00:00' } 'event-time projection'
Invoke-ClosureNegative 'authorization collector reconnect' { param($f) $f.Packet.authorization.value.authorization_witness.reconnect_count = 1 } 'event-witness'
Invoke-ClosureNegative 'overlap barrier spoof' { param($f) $f.Packet.authorization.value.authorization_witness.initial_barrier_id = 'e' * 64 } 'overlapping authorization barrier'
Invoke-ClosureNegative 'database operation order changed' { param($f) $f.Packet.live.value.payload.transition_session.event_witness.gate_exec_sequence[0].class = 'reload' } 'operation event sequence'
Invoke-ClosureNegative 'duplicate network event ID' { param($f) $f.Packet.live.value.payload.transition_session.event_witness.network_ids[1] = $f.Packet.live.value.payload.transition_session.event_witness.network_ids[0] } 'duplicates a network'
Invoke-ClosureNegative 'HBA rule count changed' { param($f) $f.Packet.live.value.payload.hba.loaded_rule_count = 14 } 'fixed byte/rule/lock'
Invoke-ClosureNegative 'HBA write sharing enabled' { param($f) $f.Packet.live.value.payload.hba.host_write_share_permitted = $true } 'fixed byte/rule/lock'
Invoke-ClosureNegative 'fresh HBA wrong generation' { param($f) $f.Packet.live.value.payload.hba.fresh_loaded_state.conf_load_time = '2026-09-15T12:00:11.0000000+00:00' } 'acknowledged HBA generation'
Invoke-ClosureNegative 'final HBA changed postmaster' { param($f) $f.Packet.live.value.payload.final_boundary.fresh_hba_state.postmaster_started_at = '2026-09-15T12:00:11.1000000+00:00' } 'generation/inventory'
Invoke-ClosureNegative 'final engine drift' { param($f) $f.Packet.live.value.payload.final_boundary.docker_engine.engine_id = 'other-engine' } 'final Docker engine'
Invoke-ClosureNegative 'final container wrong StartedAt' { param($f) $f.Packet.live.value.payload.final_boundary.postgres.started_at = '2026-09-15T12:00:09.0000000+00:00' } 'lifecycle policy'
Invoke-ClosureNegative 'execution fence absent' { param($f) $f.Packet.live.value.payload.database.fence_armed = $false } 'database proof failed'
Invoke-ClosureNegative 'database schema changed' { param($f) $f.Packet.live.value.payload.database.schema_head = '20260730_0046' } 'database safety boundary'
Invoke-ClosureNegative 'runtime execution authorized' { param($f) $f.Packet.live.value.payload.database.execution_authorized = $true } 'database safety boundary'
Invoke-ClosureNegative 'historical R1 backup hash changed consistently' { param($f) $f.Packet.prestart.value.payload.historical.backup.archive_sha256 = 'f' * 64; $f.Packet.live.value.payload.historical.backup.archive_sha256 = 'f' * 64 } 'R1 backup'
Invoke-ClosureNegative 'migration command changed' { param($f) $f.Packet.prestart.value.payload.compose_migrate.command[1] = 'downgrade' } 'rendered migration command'
Invoke-ClosureNegative 'repair raw hash changed' { param($f) $f.Packet.repair.sha256 = 'f' * 64 } 'repair raw source digest'
Invoke-ClosureNegative 'repair final attachment changed' { param($f) $f.Packet.repair.value.payload.snapshots.post_second.postgres.attachments_sha256 = 'f' * 64 } 'final attachment digest'
Invoke-ClosureNegative 'repair comparison failed' { param($f) $f.Packet.repair.value.payload.comparisons.post_attachment_admissible_twice = $false } 'Repair comparison failed'
Invoke-ClosureNegative 'terminal unknown nested flag' { param($f) $f.Packet.terminal.value.authorization | Add-Member api_start_permitted $true } 'property set'
Invoke-ClosureNegative 'Compose-aware engine substituted for successor core' { param($f) $f.Packet.lease.value.docker_engine = $f.Expected.docker_engine } 'core Docker engine closure'
Invoke-ClosureNegative 'terminal core hash replaced by full hash' { param($f) $f.Packet.terminal.value.commit.docker_engine_contract_sha256 = $f.Expected.docker_engine.contract_sha256 } 'terminal commit core Docker'
Invoke-ClosureNegative 'repair core replaced by full engine' { param($f) $f.Packet.repair.value.payload.docker_engine = $f.Expected.docker_engine } 'repair core engine context'
Invoke-ClosureNegative 'raw database clock seven fractions rejected' { param($f) $f.Packet.live.value.payload.database.first_database_clock = '2026-09-15T12:00:16.1234567+00:00' } 'PostgreSQL timestamp grammar'
Invoke-ClosureNegative 'raw database clock wrong zone' { param($f) $f.Packet.live.value.payload.database.first_database_clock = '2026-09-15T12:00:16+01:00' } 'PostgreSQL timestamp grammar'
Invoke-ClosureNegative 'raw database clock non-string' { param($f) $f.Packet.live.value.payload.database.first_database_clock = 123 } 'native JSON string'
Invoke-ClosureNegative 'raw database clock locale notation' { param($f) $f.Packet.live.value.payload.database.first_database_clock = '15/09/2026 12:00:16' } 'PostgreSQL timestamp grammar'
Invoke-ClosureNegative 'raw database clock reversed' { param($f) $f.Packet.live.value.payload.database.first_database_clock = '2026-09-15T12:00:18.123456+00:00' } 'snapshot interval'
Invoke-ClosurePositive 'repair configured-pending existing boundary' { param($f) Set-RepairFixtureDisposition $f 'verified_configured_pending_existing' }
Invoke-ClosurePositive 'repair connected mutation ends configured-pending' { param($f) Set-RepairFixtureDisposition $f 'connected_by_this_invocation' }
Invoke-ClosurePositive 'repair connected mutation ends exact' { param($f) Set-RepairFixtureDisposition $f 'connected_by_this_invocation' 'exact' }
Invoke-ClosurePositive 'repair witness has an independent session' { param($f) $f.Packet.repair.value.payload.event_witness.session_id = 'f' * 32 }
Invoke-ClosurePositive 'repair accepts Docker Desktop HBA path alias' { param($f)
    foreach ($n in @('pre', 'armed', 'post_first', 'post_second')) { $s = $f.Packet.repair.value.payload.snapshots.$n
        $s.postgres.stable_identity.hba_source = '/run/desktop/mnt/host/c/Users/Administrator/Documents/weatherpoly/ops/postgres/stratus-pg_hba.conf'; Update-RepairFixtureSnapshot $s }
    Update-RepairFixtureBindings $f
}
Invoke-ClosurePositive 'repair lifecycle includes other stopped nine-fraction Docker record' { param($f)
    foreach ($n in @('pre', 'armed', 'post_first', 'post_second')) { $s = $f.Packet.repair.value.payload.snapshots.$n
        $entry = Copy-ClosureFixture $s.lifecycle_inventory[0]; $entry.container_id = '0' * 64; $entry.name = '/test-container'; $entry.compose_service = 'test'
        $entry.started_at = '2026-09-15T11:40:00.123456789Z'; $entry.finished_at = '2026-09-15T11:41:00.987654321Z'
        $entry.compose_labels_sha256 = Get-RepairFixtureTextHash "com.docker.compose.project`0weatherpoly`ncom.docker.compose.service`0test`n"
        $s.lifecycle_inventory = @($entry, $s.lifecycle_inventory[0]); Update-RepairFixtureSnapshot $s }
    Update-RepairFixtureBindings $f
}
Invoke-ClosureNegative 'repair unknown snapshot property' { param($f) $f.Packet.repair.value.payload.snapshots.pre | Add-Member bypass $true } 'repair pre snapshot property set'
Invoke-ClosureNegative 'repair running inventory nonempty' { param($f) $f.Packet.repair.value.payload.snapshots.pre.running_container_ids = @('0' * 64) } 'running container or empty lifecycle'
Invoke-ClosureNegative 'repair lifecycle empty' { param($f) $f.Packet.repair.value.payload.snapshots.pre.lifecycle_inventory = @() } 'running container or empty lifecycle'
Invoke-ClosureNegative 'repair lifecycle duplicate' { param($f) $s = $f.Packet.repair.value.payload.snapshots.pre; $s.lifecycle_inventory = @($s.lifecycle_inventory[0], $s.lifecycle_inventory[0]) } 'duplicated or not ordinal-sorted'
Invoke-ClosureNegative 'repair lifecycle unsorted' { param($f) $s = $f.Packet.repair.value.payload.snapshots.pre; $entry = Copy-ClosureFixture $s.lifecycle_inventory[0]; $entry.container_id = '0' * 64; $s.lifecycle_inventory = @($s.lifecycle_inventory[0], $entry) } 'not ordinal-sorted'
Invoke-ClosureNegative 'repair lifecycle retained container missing' { param($f) $f.Packet.repair.value.payload.snapshots.pre.lifecycle_inventory[0].container_id = '0' * 64 } 'lacks retained PostgreSQL'
Invoke-ClosureNegative 'repair lifecycle running flag' { param($f) $f.Packet.repair.value.payload.snapshots.pre.lifecycle_inventory[0].running = $true } 'inventory is running'
Invoke-ClosureNegative 'repair lifecycle integer is string' { param($f) $f.Packet.repair.value.payload.snapshots.pre.lifecycle_inventory[0].restart_count = '0' } 'native JSON integer'
Invoke-ClosureNegative 'repair lifecycle boolean is string' { param($f) $f.Packet.repair.value.payload.snapshots.pre.lifecycle_inventory[0].oom_killed = 'false' } 'native JSON boolean'
Invoke-ClosureNegative 'repair lifecycle timestamp wrong zone' { param($f) $f.Packet.repair.value.payload.snapshots.pre.lifecycle_inventory[0].started_at = '2026-09-15T11:00:00+01:00' } 'UTC Docker timestamp'
Invoke-ClosureNegative 'repair lifecycle timestamp invalid calendar' { param($f) $f.Packet.repair.value.payload.snapshots.pre.lifecycle_inventory[0].started_at = '2026-99-15T11:00:00Z' } 'DateTime|date|valid'
Invoke-ClosureNegative 'repair lifecycle label digest changed' { param($f) $f.Packet.repair.value.payload.snapshots.pre.lifecycle_inventory[0].compose_labels_sha256 = 'f' * 64 } 'compose-label digest'
Invoke-ClosureNegative 'repair lifecycle aggregate digest changed' { param($f) $f.Packet.repair.value.payload.snapshots.pre.lifecycle_inventory_sha256 = 'f' * 64 } 'lifecycle digest is not reproducible'
Invoke-ClosureNegative 'repair stopped generation differs from PreStart' { param($f) $f.Packet.repair.value.payload.snapshots.pre.postgres.stable_identity.started_at = '2026-09-15T08:00:00.0000000+00:00' } 'stopped started_at'
Invoke-ClosureNegative 'repair stopped image differs' { param($f) $f.Packet.repair.value.payload.snapshots.pre.postgres.stable_identity.image_id = 'sha256:' + ('f' * 64) } 'retained image_id'
Invoke-ClosureNegative 'repair HBA source path differs' { param($f) $f.Packet.repair.value.payload.snapshots.pre.postgres.stable_identity.hba_source = 'C:\untrusted.conf' } 'stopped PostgreSQL configuration'
Invoke-ClosureNegative 'repair HBA writable mount' { param($f) $f.Packet.repair.value.payload.snapshots.pre.postgres.stable_identity.hba_mode = 'rw' } 'stopped PostgreSQL configuration'
Invoke-ClosureNegative 'repair unexpected postgres command digest' { param($f) $f.Packet.repair.value.payload.snapshots.pre.postgres.stable_identity.command_sha256 = 'f' * 64 } 'stopped PostgreSQL configuration'
Invoke-ClosureNegative 'repair exposed postgres port' { param($f) $f.Packet.repair.value.payload.snapshots.pre.postgres.stable_identity.published_postgres = '0.0.0.0:5432' } 'stopped PostgreSQL configuration'
Invoke-ClosureNegative 'repair stable identity digest changed' { param($f) $f.Packet.repair.value.payload.snapshots.pre.postgres.stable_identity_sha256 = 'f' * 64 } 'stable identity digest is not reproducible'
Invoke-ClosureNegative 'repair retained lifecycle name crosslink differs after rehash' { param($f) $s = $f.Packet.repair.value.payload.snapshots.pre; $s.lifecycle_inventory[0].name = '/wrong'; Update-RepairFixtureSnapshot $s } 'retained lifecycle name'
Invoke-ClosureNegative 'repair attachment native array required' { param($f) $f.Packet.repair.value.payload.snapshots.pre.postgres.attachments.weatherpoly_default.aliases = 'postgres' } 'native JSON array'
Invoke-ClosureNegative 'repair attachment duplicate alias' { param($f) $f.Packet.repair.value.payload.snapshots.pre.postgres.attachments.weatherpoly_default.aliases = @('postgres', 'postgres') } 'aliases'
Invoke-ClosureNegative 'repair attachment DNS identity changed' { param($f) $f.Packet.repair.value.payload.snapshots.pre.postgres.attachments.weatherpoly_default.dns_names[0] = 'deadbeef0000' } 'DNS'
Invoke-ClosureNegative 'repair active endpoint in stopped snapshot' { param($f) $f.Packet.repair.value.payload.snapshots.pre.postgres.attachments.weatherpoly_default.endpoint_id = 'f' * 64 } 'active endpoint'
Invoke-ClosureNegative 'repair attachment gateway priority changed' { param($f) $f.Packet.repair.value.payload.snapshots.pre.postgres.attachments.weatherpoly_default.gateway_priority = 1 } 'configuration differs'
Invoke-ClosureNegative 'repair exact attachment network changed' { param($f) $f.Packet.repair.value.payload.snapshots.pre.postgres.attachments.weatherpoly_default.network_id = 'f' * 64 } 'exact configuration differs'
Invoke-ClosureNegative 'repair attachment canonical hash changed' { param($f) $f.Packet.repair.value.payload.snapshots.pre.postgres.attachments.weatherpoly_default.canonical_sha256 = 'f' * 64 } 'attachment weatherpoly_default canonical digest'
Invoke-ClosureNegative 'repair attachment set digest changed' { param($f) $f.Packet.repair.value.payload.snapshots.pre.postgres.attachments_sha256 = 'f' * 64 } 'attachment-set digest'
Invoke-ClosureNegative 'repair pending attachment uses null marker' { param($f) Set-RepairFixtureDisposition $f 'verified_configured_pending_existing'; $f.Packet.repair.value.payload.snapshots.pre.postgres.attachments.'weatherpoly_safety-plane'.ipam_configuration = 'null' } 'pending configuration differs'
Invoke-ClosureNegative 'repair pending attachment falsely claims exact state' { param($f) Set-RepairFixtureDisposition $f 'verified_configured_pending_existing'; $f.Packet.repair.value.payload.snapshots.pre.postgres.attachments.'weatherpoly_safety-plane'.configuration_state = 'ExactStopped' } 'configuration differs'
Invoke-ClosureNegative 'repair post snapshot default-only is inadmissible' { param($f) Set-RepairFixtureDisposition $f 'connected_by_this_invocation'; $r = $f.Packet.repair.value.payload
    $r.snapshots.post_first = Copy-ClosureFixture $r.snapshots.pre; $r.snapshots.post_second = Copy-ClosureFixture $r.snapshots.pre; Update-RepairFixtureBindings $f } 'attachment disposition differs'
Invoke-ClosureNegative 'repair source-fixed network changed' { param($f) $f.Packet.repair.value.payload.snapshots.pre.production_networks.weatherpoly_default.id = 'f' * 64 } 'source-fixed network'
Invoke-ClosureNegative 'repair source-fixed network labels changed' { param($f) $f.Packet.repair.value.payload.snapshots.pre.production_networks.weatherpoly_default.labels_sha256 = 'f' * 64 } 'source-fixed network'
Invoke-ClosureNegative 'repair network attached-container set nonempty' { param($f) $f.Packet.repair.value.payload.snapshots.pre.production_networks.weatherpoly_default.attached_container_ids = @($f.Expected.postgres.container_id) } 'stopped network flags/options'
Invoke-ClosureNegative 'repair network IPv6 enabled' { param($f) $f.Packet.repair.value.payload.snapshots.pre.production_networks.weatherpoly_default.enable_ipv6 = $true } 'stopped network flags/options'
Invoke-ClosureNegative 'repair network canonical digest changed' { param($f) $f.Packet.repair.value.payload.snapshots.pre.production_networks.weatherpoly_default.canonical_sha256 = 'f' * 64 } 'network canonical digest'
Invoke-ClosureNegative 'repair network-set digest changed' { param($f) $f.Packet.repair.value.payload.snapshots.pre.production_networks_sha256 = 'f' * 64 } 'production network-set digest'
Invoke-ClosureNegative 'repair snapshot canonical digest changed' { param($f) $f.Packet.repair.value.payload.snapshots.pre.canonical_sha256 = 'f' * 64 } 'repair pre snapshot canonical digest'
Invoke-ClosureNegative 'repair pre and armed differ with valid nested hashes' { param($f) $s = $f.Packet.repair.value.payload.snapshots.armed; $s.postgres.stable_identity.labels_sha256 = 'd' * 64; Update-RepairFixtureSnapshot $s } 'pre/armed stable boundary'
Invoke-ClosureNegative 'repair cross-snapshot stable identity changed with valid hashes' { param($f)
    foreach ($n in @('post_first', 'post_second')) { $s = $f.Packet.repair.value.payload.snapshots.$n; $s.postgres.stable_identity.labels_sha256 = 'd' * 64; Update-RepairFixtureSnapshot $s }; Update-RepairFixtureBindings $f } 'cross-snapshot stopped identity'
Invoke-ClosureNegative 'repair mutation command empty even if not issued' { param($f) $f.Packet.repair.value.payload.mutation.command = @() } 'repair mutation command'
Invoke-ClosureNegative 'repair mutation command extra token' { param($f) $f.Packet.repair.value.payload.mutation.command += '--force' } 'repair mutation command'
Invoke-ClosureNegative 'repair mutation output nonzero' { param($f) $f.Packet.repair.value.payload.mutation.output_line_count = 1 } 'mutation/disposition'
Invoke-ClosureNegative 'repair mutation false flag is string' { param($f) $f.Packet.repair.value.payload.mutation.issued = 'false' } 'native JSON boolean'
Invoke-ClosureNegative 'repair disposition and issued flag disagree' { param($f) $f.Packet.repair.value.payload.mutation.issued = $true } 'mutation/disposition'
Invoke-ClosureNegative 'repair issued mutation without default-only predecessor' { param($f) $r = $f.Packet.repair.value.payload; $r.disposition = 'connected_by_this_invocation'; $r.mutation.issued = $true; Update-RepairFixtureBindings $f } 'without default-only predecessor'
Invoke-ClosureNegative 'repair nonmutation disposition disagrees with attachments' { param($f) $f.Packet.repair.value.payload.disposition = 'verified_configured_pending_existing'; Update-RepairFixtureBindings $f } 'non-mutation attachment disposition'
Invoke-ClosureNegative 'repair event witness unknown property' { param($f) $f.Packet.repair.value.payload.event_witness | Add-Member accepted $true } 'event witness property set'
Invoke-ClosureNegative 'repair event witness process PID invalid' { param($f) $f.Packet.repair.value.payload.event_witness.process_id = 0 } 'sealed identity/state'
Invoke-ClosureNegative 'repair event witness session malformed' { param($f) $f.Packet.repair.value.payload.event_witness.session_id = 'E' * 32 } 'sealed identity/state'
Invoke-ClosureNegative 'repair event witness process birth is future' { param($f) $f.Packet.repair.value.payload.event_witness.process_started_at = '2026-09-15T12:00:00.0000000+00:00' } 'process birth is after acceptance'
Invoke-ClosureNegative 'repair event command alternate Docker context' { param($f) $f.Packet.repair.value.payload.event_witness.event_command[1] = 'default' } 'exact command'
Invoke-ClosureNegative 'repair collector source digest mismatch' { param($f) $f.Packet.repair.value.payload.event_witness.collector.source_sha256 = 'f' * 64 } 'collector metadata'
Invoke-ClosureNegative 'repair collector dynamic assembly' { param($f) $f.Packet.repair.value.payload.event_witness.collector.assembly_is_dynamic = $true } 'collector metadata'
Invoke-ClosureNegative 'repair collector empty module GUID' { param($f) $f.Packet.repair.value.payload.event_witness.collector.module_version_id = '00000000-0000-0000-0000-000000000000' } 'collector metadata'
Invoke-ClosureNegative 'repair event count mismatch' { param($f) $f.Packet.repair.value.payload.event_witness.event_count = 3 } 'event count differs'
Invoke-ClosureNegative 'repair connect timestamp must be null without mutation' { param($f) $f.Packet.repair.value.payload.event_witness.network_connect_time_nano = 0 } 'must be null'
Invoke-ClosureNegative 'repair connect timestamp integer required with mutation' { param($f) Set-RepairFixtureDisposition $f 'connected_by_this_invocation'; $f.Packet.repair.value.payload.event_witness.network_connect_time_nano = '123' } 'native JSON integer'
Invoke-ClosureNegative 'repair event unexpected actor' { param($f) $f.Packet.repair.value.payload.event_witness.events[0].actor_id = 'f' * 64 } 'causal identity/time'
Invoke-ClosureNegative 'repair event unexpected lifecycle action' { param($f) $f.Packet.repair.value.payload.event_witness.events[0].action = 'start' } 'causal identity/time'
Invoke-ClosureNegative 'repair event unexpected attribute' { param($f) $f.Packet.repair.value.payload.event_witness.events[0].attributes | Add-Member hidden 'x' } 'attributes property set'
Invoke-ClosureNegative 'repair event nonlocal scope' { param($f) $f.Packet.repair.value.payload.event_witness.events[0].scope = 'swarm' } 'causal identity/time'
Invoke-ClosureNegative 'repair event seconds differ from nanos' { param($f) $f.Packet.repair.value.payload.event_witness.events[0].time += 1 } 'causal identity/time'
Invoke-ClosureNegative 'repair event nonmonotonic nanos' { param($f) $w = $f.Packet.repair.value.payload.event_witness; $w.events[1].time_nano = $w.events[0].time_nano } 'causal identity/time'
Invoke-ClosureNegative 'repair connect event wrong container' { param($f) Set-RepairFixtureDisposition $f 'connected_by_this_invocation'; $f.Packet.repair.value.payload.event_witness.events[1].attributes.container = 'f' * 64 } 'attributes.container'
Invoke-ClosureNegative 'repair pre barrier record crosslink differs' { param($f) $f.Packet.repair.value.payload.event_witness.pre_barrier.time_nano += 1000 } 'pre_barrier time_nano'
Invoke-ClosureNegative 'repair final barrier record crosslink differs' { param($f) $f.Packet.repair.value.payload.event_witness.final_barrier.actor_id = 'f' * 64 } 'final_barrier actor_id'
Invoke-ClosureNegative 'repair authority time crosslink differs' { param($f) $f.Packet.repair.value.payload.event_witness.authority_as_of_time_nano += 1000 } 'authority/connect time'
Invoke-ClosureNegative 'repair connect time crosslink differs' { param($f) Set-RepairFixtureDisposition $f 'connected_by_this_invocation'; $f.Packet.repair.value.payload.event_witness.network_connect_time_nano += 1000 } 'authority/connect time'
Invoke-ClosureNegative 'repair final barrier too old after causal rebind' { param($f) $w = $f.Packet.repair.value.payload.event_witness
    $w.process_started_at = '2026-09-15T11:58:40.0000000+00:00'
    foreach ($e in $w.events) { $e.time -= 40; $e.time_nano -= [long]40000000000 }
    foreach ($n in @('pre_barrier', 'final_barrier')) { $w.$n.time -= 40; $w.$n.time_nano -= [long]40000000000 }; $w.authority_as_of_time_nano -= [long]40000000000
    Update-RepairFixtureEvents $w } 'final barrier seal lag'
Invoke-ClosureNegative 'repair journal hash mismatch' { param($f) $f.Packet.repair.value.payload.event_witness.journal_sha256 = 'f' * 64 } 'journal digest/bytes'
Invoke-ClosureNegative 'repair journal byte count mismatch' { param($f) $f.Packet.repair.value.payload.event_witness.journal_bytes += 1 } 'journal digest/bytes'
Invoke-ClosureNegative 'repair journal exceeds fixed limit' { param($f) $f.Packet.repair.value.payload.event_witness.journal_bytes = 65537 } 'sealed identity/state'
Invoke-ClosureNegative 'repair event array hash mismatch' { param($f) $f.Packet.repair.value.payload.event_witness.events_sha256 = 'f' * 64 } 'events digest is not reproducible'
Invoke-ClosureNegative 'repair capture stderr nonempty' { param($f) $f.Packet.repair.value.payload.event_witness.stderr_bytes = 1 } 'sealed identity/state'
Invoke-ClosureNegative 'repair capture truncated' { param($f) $f.Packet.repair.value.payload.event_witness.truncated = $true } 'sealed identity/state'
Invoke-ClosureNegative 'repair capture EOF before seal' { param($f) $f.Packet.repair.value.payload.event_witness.eof_before_seal = $true } 'sealed identity/state'
Invoke-ClosureNegative 'repair unexpected event declared' { param($f) $f.Packet.repair.value.payload.event_witness.unexpected_event_count = 1 } 'sealed identity/state'
$expectedCaseCount = 151
if ($results.Count -ne $expectedCaseCount -or @($results.ToArray() | Where-Object { -not $_.passed }).Count -ne 0) { throw 'Fixed pure closure test case count/result differs.' }
[pscustomobject]@{ suite = 'parent-transition-closure-pure'; passed = $results.Count; failed = 0
    expected = $expectedCaseCount; executed = $results.Count
    native_executed = $false; docker_executed = $false; filesystem_fixtures_created = $false
    full_protocol_accepted = $false; results = @($results.ToArray()) } | ConvertTo-Json -Depth 6 -Compress
