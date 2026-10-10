[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Transition')]
    [string] $Mode,

    [Parameter(Mandatory = $true)]
    [string] $Root,

    [Parameter(Mandatory = $true)]
    [string] $BuildContext,

    [Parameter(Mandatory = $true)]
    [string] $BackupDirectory,

    [Parameter(Mandatory = $true)]
    [string] $PreMigrationAttestationPath,

    [Parameter(Mandatory = $true)]
    [string] $LineageDirectory,

    [Parameter(Mandatory = $true)]
    [string] $NetworkRepairAttestationPath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string] $ExpectedRevision,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^sha256:[0-9a-f]{64}$')]
    [string] $ExpectedImageId,

    [Parameter(Mandatory = $true)]
    [string] $CandidateImage,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(?:[0-9a-f]{64}|[A-Za-z0-9][A-Za-z0-9_.-]{1,127})$')]
    [string] $PostgresContainer,

    [Parameter(Mandatory = $true)]
    [string] $PreStartAttestationPath,

    [Parameter(Mandatory = $true)]
    [string] $LiveAcceptancePath,

    [Parameter(Mandatory = $true)][string] $ParentStageContractPath,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{64}$')]
    [string] $ParentStageContractSha256
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# This entry must not launch even the parent watchdog until same-invocation
# downstream supervision and the terminal lifetime have passed independent review.
throw 'STRATUS gate parent integration is WIP and non-authorizing.'

# Reject same-scope reentry BEFORE dropping any previous session or read-lock
# reference. A completed local Transition is not a completed parent lifetime.
if ($null -ne (Get-Variable -Name parentStage -Scope Script -ErrorAction SilentlyContinue) -or
    $null -ne (Get-Variable -Name parentStageOwnedHandles -Scope Script -ErrorAction SilentlyContinue)) {
    throw 'Parent-stage script scope already exists; reentry may not discard its resources.'
}

$sourceSchemaHead = '20260730_0046'
$targetSchemaHead = '20260730_0047'
$predecessorRevision = '6d093b49cd5397d1e2bdb41fb4640d3e59127281'
$predecessorImage = 'stratus:candidate-6d093b49cd53'
$predecessorImageId = 'sha256:3d8c2904224328efb5604a28778c138abf26d8f43a06c02c733c8ad270ee2d24'
$productionPostgresId = '6ce82603e44abb95be94dbe95830af2d00c0c36ca3fe8a122b798087ecc1b299'
$postgres16Image = (
    'postgres:16-alpine@sha256:' +
    '20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'
)
$postgres16ImageId = 'sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'
$fixedHbaSha256 = '164276f8a7337705c3933e3201cb53a5a10e354ec7627768c4ac8b3e951380a4'
$fixedHbaInventorySha256 = 'e6b26bdffeba11e892100ce05f855db8ede8ce407a24fbef6144d2a458cc3567'
$preMigrationAttestationSha256 = 'a8717ba0656bc0f5abde739dd0352284f48f598f89597866c55bc7627d97d189'
$preMigrationPayloadSha256 = 'd1ad1ed1511653cc394b70a118ca41b8751c9504cd0fdd449936868a5045eabd'
$restoreAttestationSha256 = '73939f689e3add8b92ba72cb4824a1c2243fa35228eaed25ffc5dab4144a3e4e'
$preStartMaxAgeSeconds = 600
$postgresStartWindowSeconds = 300
$clockSkewSeconds = 5
$dockerEventJournalMaxBytes = 8388608
$dockerEventJournalMaxEvents = 4096
$dockerEventBarrierTimeoutSeconds = 15
$postgresRollbackTimeoutSeconds = 15
$networkRepairMaxAgeSeconds = 600
$networkRepairEventSealMaxLagSeconds = 30
$watchdogHeartbeatTimeoutSeconds = 15
$watchdogLeaseReadyTimeoutSeconds = 30
$watchdogProcessExitGraceSeconds = 30
$postgresStartWallClockTimeoutSeconds = 15
$postgresStartContainmentReserveSeconds = 45
$parentAuthorizationPublishWindowSeconds = 5
$approvedWatchdogSha256 =
    '39419488bc38de5421f229be05b8f00ccb8085b8fa27a05df2b0cb4c1bfe1260'
$approvedParentStageRuntimeSha256 =
    '71f0619da9aad5769ff7528984dee95de590ba4f920fd62b7e053356130ba249'
$approvedParentStageWatchdogSha256 =
    '7dcf593d44aaae5d77e7250191a91dba576b4c3d8d17e169b2ad9a6b0d54b161'
$approvedDockerExecutableSha256 =
    '388f231898cff8674a2aa341e306b77fd1f93a5954dd33ff556575a69227824d'
$approvedRepairDockerCollectorTemplateSha256 =
    '090809477bdd74506256d557f11a2d73a470675de98c41d3f5923735251f51c5'
$approvedRepairDockerCollectorSourceSha256 =
    '621e556478237ab88a6cb20247953d92569e6f00fd3fa700bfa706c3bcc37a5e'
$approvedGitExecutableSha256 =
    'fec691d80fccc35fcc309fbc9f720536c1d795b8a562ec169f28c9923da9600f'
$approvedComposePluginSha256 =
    '8482829f77d14da8dfdfd0b553847946cee5161c90312219cf4f47d1f132d2bf'
$approvedComposePluginVersion = 'v2.38.2-desktop.1'
$approvedDockerContext = 'desktop-linux'
$approvedDockerEndpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
$approvedDockerEngineId = 'd98e03f5-06d7-4af4-9f21-b6f7a70ebf68'
$approvedDockerServerVersion = '28.3.2'
$approvedDockerEngineName = 'docker-desktop'
$approvedDockerRootDirectory = '/var/lib/docker'
$productionNetworkOptionsSha256 =
    'e0d52002c06510e7c6f227700d07f412845fc15686f0723a262d8c3e1459f569'
$productionDataVolumeSource = '/var/lib/docker/volumes/weatherpoly_stratus-postgres/_data'
$productionPostgresConfigHash =
    'bb02ff39b208731cad693da86ac0f83e76aab0b548eca9a0ae310d35ed2576c1'
$productionPostgresCommandSha256 =
    '3e1396d8c083a0274909460787db163a1f9070eeabb659c403c5c7ca890436b3'
$script:dockerExecutable = $null
$script:dockerExecutableSha256 = $null
$script:dockerExecutableLock = $null
$script:gitExecutable = $null
$script:gitExecutableSha256 = $null
$script:hbaReadLock = $null
$script:hbaReadLockPath = $null
$script:watchdogScriptReadLock = $null
$script:watchdogHostReadLock = $null
$script:gateScriptReadLock = $null
$script:parentStage = $null
$script:parentStageOwnedHandles = [Collections.Generic.List[IDisposable]]::new()
$productionNetworks = [ordered]@{
    'weatherpoly_default' = [pscustomobject]@{
        id = '77551e8d1119201f778fdb90e151ec26f1d9cc23832ba7b88c4db6203e3d8ba2'
        compose_name = 'default'
        subnet = '172.20.0.0/16'
        gateway = '172.20.0.1'
        ipv4_pattern = '^172\.20\.(?:[0-9]{1,3})\.(?:[0-9]{1,3})$'
        labels_sha256 = '4a06f3121da4b275c077a18cc2b17a4c12258754e48c3ac2d8de10be0b96ad0d'
        created = '2026-07-05T17:30:21.808081932Z'
    }
    'weatherpoly_safety-plane' = [pscustomobject]@{
        id = '0d1ad122718694dbc5e6714f69a601a7d429e23a0b74eecdb10ace944e5c8620'
        compose_name = 'safety-plane'
        subnet = '172.22.0.0/16'
        gateway = '172.22.0.1'
        ipv4_pattern = '^172\.22\.(?:[0-9]{1,3})\.(?:[0-9]{1,3})$'
        labels_sha256 = '44ff0d23c63cad8c3e898bdb1378e958ddcedbcd202055903cd0c7949de103e7'
        created = '2026-07-30T09:19:55.325597748Z'
    }
}
$lineageArtifacts = [ordered]@{
    '09-stopped-migrate-stage-acceptance.json' =
        'bc8006665fc1365fb51a55904d21f4a6b783e3929a4f1060d8168f01093766dd'
    '17-pre-migration-wrapper-false-negative.json' =
        'ba881647611219d29ccf6fb68d42f1a745a3a431ede5f52fc9cf54ba028a4bea'
    '18-pre-migration-compatibility-holds.json' =
        '829ccf65b644fb7905a525d0a68a29bd3e9483cf2962b2ec2af144accf7c6033'
    '19-pre-migration-gate-and-0047-migration-accepted.json' =
        '200d1b10bfe05537c4385d87dec81c2bbb2669a69c0abcdfa2ba9217b7d9fb4f'
    '20-strict-post-ddl-0047-accepted.json' =
        'c6e30ccea288f3f8c3b57130c4e8af2f40da53ad33b808cb4ddeaa43437503ff'
    '21-post-ddl-hba-accepted.json' =
        'f42d979d98ec9feaa949cb23cb0add1925443bb406967cc1802e701bc0a4926c'
}

$closedGitContract = [ordered]@{
    '.gitattributes' = '0e7e5b4fc7af0523d798fc4a01979d2ad20f178b'
    '.dockerignore' = '7b30162a290eb8df806d9bf299ecba874052ecb3'
    'Dockerfile' = '554aaec8b175912723b686c7bc2f02c29c037f48'
    'README.md' = 'e39cc4f5324ce5c6c9e9cce35e9d27e4dc6ea571'
    'docker-compose.yml' = '53422981828ca0e00635d1c36778285f5e7221b9'
    'alembic.ini' = 'e109e86453d6913d5cfa41d9318a5f94544d921b'
    'migrations' = 'c1eabc51a09a79ce80fcc7aeb05156dd27d437e3'
    'ops/postgres/stratus-pg_hba.conf' = '59071191059e64f02d40f5daa62178b8b467c95a'
    'pyproject.toml' = 'f148c93eacae0539a6155c10dbd971baeb590fe3'
    'uv.lock' = '055c2fd18d0c3856c923ad8c9e011c027656aad4'
    'src/stratus/__init__.py' = '15fef34b5aae8ee9c1c6004685d9c98a581260f7'
    'src/stratus/config.py' = '4424ea6189413ca026afa7c52517c7c26d79409e'
    'src/stratus/db/__init__.py' = '331fd004a38d5cfcb60d87492f6628b3ab88a606'
    'src/stratus/db/base.py' = '3a60ede62c3ffd368d2473e5fb476e8d4656caef'
    'src/stratus/db/models.py' = '0354452bd74cfd7021bcbe07cee8861a1d957f82'
}

function Assert-ParentStageCanonicalPath {
    param([string] $Path, [switch] $MayBeAbsent)
    if ([string]::IsNullOrWhiteSpace($Path) -or
        $Path -cne [IO.Path]::GetFullPath($Path)) {
        throw 'Parent-stage paths must be canonical absolute paths.'
    }
    $cursor = $Path
    $leaf = $true
    while (-not [string]::IsNullOrEmpty($cursor)) {
        $exists = [IO.File]::Exists($cursor) -or [IO.Directory]::Exists($cursor)
        if (-not $exists -and (-not $leaf -or -not $MayBeAbsent)) {
            throw 'Parent-stage path ancestry is missing.'
        }
        if ($exists -and ([IO.File]::GetAttributes($cursor) -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Parent-stage path ancestry contains a reparse point.'
        }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
        $leaf = $false
    }
}

function Assert-ParentStageProperties {
    param([object] $Value, [string[]] $Expected, [string] $Label)
    Assert-NativeJsonObject -Value $Value -Label $Label
    Assert-ExactProperties -Value $Value -Expected $Expected -Label $Label
}

function Read-ParentStageDocument {
    param([string] $Path, [string] $ExpectedSha256, [switch] $AtomicReplacement)
    Assert-ParentStageCanonicalPath -Path $Path
    $files = $script:parentStage.files
    $bytes = $files::ReadBounded($Path, 1048576, [bool]$AtomicReplacement)
    $hash = Get-ByteArraySha256 -Bytes $bytes
    if (-not [string]::IsNullOrEmpty($ExpectedSha256) -and $hash -cne $ExpectedSha256) {
        throw 'Parent-stage document hash differs from its immutable binding.'
    }
    return [pscustomobject]@{
        sha256 = $hash
        value = ConvertFrom-StrictJsonObjectText `
            -Json ([Text.UTF8Encoding]::new($false, $true).GetString($bytes)) -Label 'parent-stage document'
    }
}

function Assert-ParentStageNoAuthorization {
    param([object] $Value)
    $names = @('postgres_start_permitted', 'api_start_permitted', 'worker_start_permitted',
        'safety_control_start_permitted', 'live_transition_permitted',
        'binance_kill_switch_removal_permitted', 'migration_container_permitted', 'container_remove_permitted')
    Assert-ParentStageProperties $Value $names 'parent-stage non-authorization'
    foreach ($name in $names) {
        Assert-NativeJsonBoolean -Value $Value.$name -Label "parent-stage $name"
        if ($Value.$name) { throw 'Parent-stage lease/ready must not grant lifecycle authority.' }
    }
}

function Assert-ParentStageProcessIdentity {
    param([object] $Value, [switch] $WithoutHash)
    $names = @('pid', 'start_time_utc', 'executable')
    if (-not $WithoutHash) { $names += 'sha256' }
    Assert-ParentStageProperties $Value $names 'parent-stage process'
    Assert-NativeJsonInteger -Value $Value.pid -Label 'parent-stage process pid'
    if ($Value.pid -le 0 -or $Value.pid -gt [int]::MaxValue) { throw 'Parent-stage process PID is invalid.' }
    foreach ($name in @('start_time_utc', 'executable')) {
        Assert-NativeJsonString -Value $Value.$name -Label "parent-stage process $name"
    }
    [void](ConvertTo-CanonicalUtcTime -Value $Value.start_time_utc -Label 'parent-stage process start')
    Assert-ParentStageCanonicalPath -Path $Value.executable
    if (-not $WithoutHash) {
        Assert-NativeJsonString -Value $Value.sha256 -Label 'parent-stage process hash'
        if ($Value.sha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'Parent-stage process hash is invalid.' }
    }
}

function Open-ParentStageEvidenceLock {
    param([string] $Path, [string] $Hash)
    $files = $script:parentStage.files
    $handle = $files::Lock($Path, $Hash)
    $script:parentStageOwnedHandles.Add($handle)
    $script:parentStage.evidence.Add([pscustomobject]@{ handle = $handle; hash = $Hash })
    return $handle
}

function Assert-ParentStageR1Anchor {
    param([object] $Contract, [object] $Lease, [object] $Ready, [switch] $BeforeLaunch)
    if ($BeforeLaunch -and ($null -ne $Lease -or $null -ne $Ready)) {
        throw 'R1 preflight cannot consume provisional process receipts.'
    }
    $anchorFields = @('directory', 'acceptance_sha256', 'evidence_manifest_sha256', 'sealed_sha256')
    Assert-ParentStageProperties $Contract.r1 $anchorFields 'parent-stage R1 anchor'
    if (-not $BeforeLaunch) {
        Assert-ParentStageProperties $Lease.r1_anchor $anchorFields 'parent-stage lease R1 anchor'
    }
    foreach ($name in $anchorFields) {
        Assert-NativeJsonString $Contract.r1.$name "parent-stage R1 $name"
        if ($name -cne 'directory' -and $Contract.r1.$name -cnotmatch '^[0-9a-f]{64}$') {
            throw 'Parent-stage R1 anchors disagree or contain an invalid hash.'
        }
        if (-not $BeforeLaunch) {
            Assert-NativeJsonString $Lease.r1_anchor.$name "parent-stage lease R1 $name"
            if ($Lease.r1_anchor.$name -cne $Contract.r1.$name) { throw 'Parent-stage lease R1 anchor differs.' }
        }
    }
    $r1 = $Contract.r1
    if ($r1.directory -cne (Join-Path $absoluteRoot ".stratus-cache\release-transitions\r1-$ExpectedRevision.accepted") -or
        (-not $BeforeLaunch -and $Ready.boundary.r1_acceptance_sha256 -cne $r1.acceptance_sha256)) {
        throw 'Parent-stage R1 anchor is not the canonical accepted revision.'
    }
    $files = $script:parentStage.files
    foreach ($pair in @(@('SEALED.json', $r1.sealed_sha256), @('acceptance.json', $r1.acceptance_sha256),
        @('evidence-manifest.tsv', $r1.evidence_manifest_sha256))) {
        [void](Open-ParentStageEvidenceLock (Join-Path $r1.directory $pair[0]) $pair[1])
    }
    $sealed = (Read-ParentStageDocument (Join-Path $r1.directory 'SEALED.json') $r1.sealed_sha256).value
    $accepted = (Read-ParentStageDocument (Join-Path $r1.directory 'acceptance.json') $r1.acceptance_sha256).value
    Assert-ParentStageProperties $sealed @('format', 'record_id', 'sealed_at_utc', 'revision', 'acceptance_sha256',
        'evidence_manifest_sha256', 'input_manifest_sha256', 'artifact_count', 'state',
        'section3_authorized', 'requires_parent_stage_watchdog_acceptance') 'parent-stage R1 seal'
    Assert-ParentStageProperties $accepted @('format', 'record_id', 'recorded_at_utc', 'production_root', 'candidate',
        'remote_ci', 'historical_0047', 'successor_attempt', 'pre_start', 'decisions', 'authorization', 'artifacts') 'parent-stage R1 acceptance'
    Assert-ParentStageProperties $accepted.candidate @('revision', 'tree', 'source_path_count', 'source_aggregate_sha256',
        'build_worktree', 'detached', 'clean', 'image', 'image_id', 'release_digest', 'platform') 'parent-stage R1 candidate'
    Assert-ParentStageProperties $accepted.decisions @('R0', 'R1', 'immutable_image_load_gate_transition',
        'section3_authorized', 'requires_parent_stage_watchdog_acceptance', 'section3_started') 'parent-stage R1 decisions'
    Assert-ParentStageProperties $accepted.successor_attempt @('directory', 'evidence_directory', 'reservation_sha256',
        'separate_from_historical_lineage', 'empty_at_acceptance', 'create_new_only', 'reuse_after_failure') 'R1 successor attempt'
    Assert-ParentStageProperties $accepted.authorization @('scope',
        'retained_postgres_start_permitted_only_inside_single_process_transition', 'migration_start_permitted',
        'api_start_permitted_before_matching_successful_terminal_receipt', 'worker_start_permitted',
        'safety_control_start_permitted', 'live_transition_permitted', 'binance_kill_switch_removal_permitted') 'R1 authorization'
    Assert-NativeJsonString $accepted.authorization.scope 'R1 authorization scope'
    if ($accepted.authorization.scope -cne 'PAPER_ONLY') { throw 'R1 authorization scope is not PAPER_ONLY.' }
    foreach ($property in $accepted.authorization.PSObject.Properties) {
        if ($property.Name -ceq 'scope') { continue }
        Assert-NativeJsonBoolean $property.Value 'R1 prerequisite authority'
        if ($property.Value) { throw 'R1 prerequisite granted unexpected authority.' }
    }
    foreach ($obj in @($sealed, $accepted.decisions)) {
        Assert-NativeJsonBoolean $obj.section3_authorized 'R1 Section 3 authority'
        Assert-NativeJsonBoolean $obj.requires_parent_stage_watchdog_acceptance 'R1 parent supervision requirement'
        if ($obj.section3_authorized -or -not $obj.requires_parent_stage_watchdog_acceptance) {
            throw 'R1 must remain prerequisite-only while parent supervision is armed.'
        }
    }
    foreach ($value in @($sealed.format, $sealed.record_id, $sealed.revision, $sealed.acceptance_sha256,
        $sealed.evidence_manifest_sha256, $sealed.input_manifest_sha256, $sealed.state,
        $accepted.format, $accepted.record_id, $accepted.production_root, $accepted.candidate.revision,
        $accepted.candidate.source_aggregate_sha256, $accepted.candidate.build_worktree, $accepted.candidate.image,
        $accepted.candidate.image_id, $accepted.candidate.release_digest, $accepted.candidate.platform,
        $accepted.decisions.R0, $accepted.decisions.R1, $accepted.decisions.immutable_image_load_gate_transition)) {
        Assert-NativeJsonString $value 'parent-stage R1 native string'
    }
    Assert-NativeJsonInteger $sealed.artifact_count 'parent-stage R1 artifact count'
    Assert-NativeJsonBoolean $accepted.decisions.section3_started 'parent-stage R1 started'
    foreach ($name in @('clean', 'detached')) {
        Assert-NativeJsonBoolean $accepted.candidate.$name 'parent-stage R1 source flag'
        if (-not $accepted.candidate.$name) { throw 'R1 candidate source was not clean and detached.' }
    }
    if ($sealed.format -cne 'stratus-r0-r1-seal-v3' -or $sealed.revision -cne $ExpectedRevision -or
        $sealed.state -cne 'ACCEPTED_R1_PREREQUISITE_PARENT_STAGE_PENDING' -or
        $sealed.acceptance_sha256 -cne $r1.acceptance_sha256 -or
        $sealed.evidence_manifest_sha256 -cne $r1.evidence_manifest_sha256 -or
        $accepted.format -cne 'stratus-r0-r1-acceptance-v3' -or $accepted.record_id -cne $sealed.record_id -or
        $accepted.production_root -cne $absoluteRoot -or $accepted.candidate.revision -cne $ExpectedRevision -or
        (-not $BeforeLaunch -and $accepted.candidate.source_aggregate_sha256 -cne $Lease.source_manifest_sha256) -or
        $accepted.candidate.build_worktree -cne $absoluteBuildContext -or
        $accepted.candidate.image -cne $CandidateImage -or $accepted.candidate.image_id -cne $ExpectedImageId -or
        $accepted.candidate.release_digest -cne $ExpectedImageId.Substring(7) -or
        $accepted.candidate.platform -cne 'linux/amd64' -or $accepted.decisions.R0 -cne 'ACCEPT' -or
        $accepted.decisions.R1 -cne 'ACCEPT' -or $accepted.decisions.immutable_image_load_gate_transition -cne 'RECORDED' -or
        $accepted.decisions.section3_started) { throw 'Parent-stage R1 acceptance candidate differs.' }
    if ($accepted.candidate.source_aggregate_sha256 -cnotmatch '^[0-9a-f]{64}$') {
        throw 'R1 source aggregate hash is malformed.'
    }
    $expectedNames = @('acceptance.json', 'build-command.json', 'build-history.json', 'ci-evidence.json',
        'git-evidence.json', 'historical-0047-inputs.json', 'image-inspect.json', 'input-manifest.tsv',
        'pre-start-topology.json', 'release-guard.json', 'source-manifest.tsv', 'source-summary.json', 'successor-reservation.json')
    $manifestBytes = $files::ReadRawBounded((Join-Path $r1.directory 'evidence-manifest.tsv'), 65536, $false)
    $manifestText = [Text.UTF8Encoding]::new($false, $true).GetString($manifestBytes)
    $lines = @($manifestText.TrimEnd("`n").Split("`n"))
    if ($manifestText.Contains("`r") -or -not $manifestText.EndsWith("`n") -or
        $lines.Count -ne $expectedNames.Count -or $sealed.artifact_count -ne $expectedNames.Count) {
        throw 'Parent-stage R1 evidence manifest is not canonical.'
    }
    $manifestHashes = @{}
    for ($index = 0; $index -lt $expectedNames.Count; $index++) {
        $parts = @($lines[$index].Split("`t"))
        if ($parts.Count -ne 3 -or $parts[0] -cne $expectedNames[$index] -or
            $parts[1] -cnotmatch '^[1-9][0-9]*$' -or $parts[2] -cnotmatch '^[0-9a-f]{64}$') {
            throw 'Parent-stage R1 evidence manifest entry differs.'
        }
        $held = Open-ParentStageEvidenceLock (Join-Path $r1.directory $parts[0]) $parts[2]
        $manifestHashes[$parts[0]] = $parts[2]
        if ($held.Length.ToString([Globalization.CultureInfo]::InvariantCulture) -cne $parts[1] -or
            ($parts[0] -ceq 'input-manifest.tsv' -and $parts[2] -cne $sealed.input_manifest_sha256) -or
            ($parts[0] -ceq 'source-manifest.tsv' -and $parts[2] -cne $accepted.candidate.source_aggregate_sha256)) {
            throw 'Parent-stage R1 artifact bytes differ.'
        }
    }
    foreach ($name in @('directory', 'evidence_directory', 'reservation_sha256')) {
        Assert-NativeJsonString $accepted.successor_attempt.$name "R1 successor $name"
    }
    foreach ($binding in @(@('pre_start', 'topology_sha256', 'pre-start-topology.json'),
        @('remote_ci', 'evidence_sha256', 'ci-evidence.json'),
        @('historical_0047', 'evidence_sha256', 'historical-0047-inputs.json'))) {
        $section = $accepted.($binding[0])
        Assert-NativeJsonObject $section 'R1 evidence lineage section'
        Assert-NativeJsonString $section.($binding[1]) 'R1 evidence lineage hash'
        if ($section.($binding[1]) -cne $manifestHashes[$binding[2]]) {
            throw 'R1 evidence lineage hash differs from the held evidence manifest.'
        }
    }
    foreach ($name in @('separate_from_historical_lineage', 'empty_at_acceptance', 'create_new_only', 'reuse_after_failure')) {
        Assert-NativeJsonBoolean $accepted.successor_attempt.$name "R1 successor $name"
        if ($accepted.successor_attempt.$name -ne ($name -cne 'reuse_after_failure')) {
            throw 'R1 successor reservation is not fresh and create-new-only.'
        }
    }
    if ($accepted.successor_attempt.reservation_sha256 -cne $manifestHashes['successor-reservation.json']) {
        throw 'R1 successor reservation hash differs from the held evidence manifest.'
    }
    $reservation = (Read-ParentStageDocument -Path (Join-Path $r1.directory 'successor-reservation.json') `
        -ExpectedSha256 $manifestHashes['successor-reservation.json']).value
    Assert-ParentStageProperties $reservation @('format', 'record_id', 'reserved_at_utc', 'revision', 'attempt_id',
        'attempt_directory', 'evidence_directory', 'separate_from_historical_lineage', 'empty_at_acceptance',
        'regular_directory', 'reparse_point', 'create_new_only', 'reuse_after_failure', 'section3_started') 'R1 reservation'
    foreach ($name in @('format', 'record_id', 'reserved_at_utc', 'revision', 'attempt_id', 'attempt_directory', 'evidence_directory')) {
        Assert-NativeJsonString $reservation.$name "R1 reservation $name"
    }
    foreach ($name in @('separate_from_historical_lineage', 'empty_at_acceptance', 'regular_directory',
        'reparse_point', 'create_new_only', 'reuse_after_failure', 'section3_started')) {
        Assert-NativeJsonBoolean $reservation.$name "R1 reservation $name"
        if ($reservation.$name -ne ($name -cin @('separate_from_historical_lineage', 'empty_at_acceptance',
            'regular_directory', 'create_new_only'))) { throw 'R1 reservation safety flags differ.' }
    }
    Assert-ParentStageCanonicalPath $reservation.attempt_directory
    Assert-ParentStageCanonicalPath $reservation.evidence_directory
    if ($reservation.format -cne 'stratus-r1-successor-reservation-v3' -or
        $reservation.record_id -cne $sealed.record_id -or $reservation.revision -cne $ExpectedRevision -or
        $reservation.attempt_id -cne $Contract.session_id -or
        $reservation.attempt_directory -cne $accepted.successor_attempt.directory -or
        $reservation.evidence_directory -cne $accepted.successor_attempt.evidence_directory -or
        -not $ParentStageContractPath.StartsWith($reservation.evidence_directory + '\', [StringComparison]::Ordinal)) {
        throw 'Parent stage is outside the accepted fresh successor reservation/session.'
    }
    $r1Times = @{}
    foreach ($pair in @(@('reserved', $reservation.reserved_at_utc), @('recorded', $accepted.recorded_at_utc),
        @('sealed', $sealed.sealed_at_utc))) {
        Assert-NativeJsonString $pair[1] 'R1 canonical UTC time'
        $instant = [DateTime]::ParseExact($pair[1], 'o', [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind)
        if ($instant.Kind -ne [DateTimeKind]::Utc -or $instant.ToString('o') -cne $pair[1]) {
            throw 'R1 UTC timestamp must use canonical seven-fraction Z format.'
        }
        $r1Times[$pair[0]] = [DateTimeOffset]::new($instant)
    }
    if ($r1Times.reserved -gt $r1Times.recorded -or $r1Times.recorded -gt $r1Times.sealed -or
        $r1Times.sealed -gt [DateTimeOffset]::UtcNow.AddSeconds(2)) { throw 'R1 publication chronology differs.' }
    $sourceBytes = $files::ReadRawBounded((Join-Path $r1.directory 'source-manifest.tsv'), 8388608, $false)
    $sourceText = [Text.UTF8Encoding]::new($false, $true).GetString($sourceBytes)
    if ($sourceText.Contains("`r") -or -not $sourceText.EndsWith("`n")) { throw 'R1 source manifest is not canonical.' }
    $sourceEntries = @{}; $lastSource = $null
    foreach ($line in $sourceText.TrimEnd("`n").Split("`n")) {
        $parts = @($line.Split("`t")); $entryLength = 0L
        if ($parts.Count -ne 3 -or $parts[0] -cnotmatch '^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$' -or
            @($parts[0].Split('/') | Where-Object { $_ -cin @('.', '..') }).Count -ne 0 -or
            $parts[1] -cnotmatch '^[1-9][0-9]*$' -or -not [long]::TryParse($parts[1], [ref]$entryLength) -or
            $parts[2] -cnotmatch '^[0-9a-f]{64}$' -or $sourceEntries.ContainsKey($parts[0]) -or
            ($null -ne $lastSource -and [StringComparer]::Ordinal.Compare($lastSource, $parts[0]) -ge 0)) {
            throw 'R1 source manifest path/order/entry is not canonical.'
        }
        $sourceEntries[$parts[0]] = $parts; $lastSource = $parts[0]
    }
    Assert-NativeJsonInteger $accepted.candidate.source_path_count 'R1 source count'
    if ($accepted.candidate.source_path_count -ne $sourceEntries.Count) { throw 'R1 source manifest count differs.' }
    foreach ($relative in @('docker-compose.yml', 'scripts/Invoke-StratusParentStageWatchdog.ps1',
        'scripts/Stratus.ParentStage.Runtime.cs', 'scripts/Assert-StratusPostMigrationSuccessorGate.ps1',
        'scripts/Stratus.ParentTransitionLineage.ps1', 'scripts/Stratus.ParentTransitionClosure.ps1',
        'scripts/Invoke-StratusSuccessorTransitionWatchdog.ps1', 'scripts/Invoke-StratusStoppedPostgresNetworkRepair.ps1',
        'scripts/Invoke-StratusRuntimeLoadObserver.ps1', 'scripts/Invoke-StratusDashboardLoadGate.ps1',
        'scripts/Assert-StratusLoadGateArtifacts.ps1')) {
        $sourceLines = @($sourceText.TrimEnd("`n").Split("`n") | Where-Object {
            $_.StartsWith($relative + "`t", [StringComparison]::Ordinal)
        })
        if ($sourceLines.Count -ne 1) { throw 'R1 source manifest does not uniquely bind the parent/gate program.' }
        $parts = @($sourceLines[0].Split("`t"))
        if ($parts.Count -ne 3 -or $parts[1] -cnotmatch '^[1-9][0-9]*$' -or
            $parts[2] -cnotmatch '^[0-9a-f]{64}$') { throw 'R1 parent/gate source entry is malformed.' }
        foreach ($rootPath in @($absoluteRoot, $absoluteBuildContext)) {
            $held = Open-ParentStageEvidenceLock (Join-Path $rootPath $relative) $parts[2]
            if ($held.Length.ToString([Globalization.CultureInfo]::InvariantCulture) -cne $parts[1]) {
                throw 'R1 source manifest parent/gate byte length differs.'
            }
        }
    }
    $script:parentStage.source_manifest_sha256 = $accepted.candidate.source_aggregate_sha256
}

function Initialize-ParentStageRuntime {
    if ($null -ne $script:parentStage) { throw 'A parent-stage invocation cannot replace an existing session.' }
    $runtimePath = Join-Path $absoluteRoot 'scripts\Stratus.ParentStage.Runtime.cs'
    Assert-ParentStageCanonicalPath -Path $runtimePath
    $runtimeLock = [IO.FileStream]::new($runtimePath, 'Open', 'Read', 'Read')
    $script:parentStageOwnedHandles.Add($runtimeLock)
    if ($runtimeLock.Length -lt 1 -or $runtimeLock.Length -gt 1048576 -or
        (Get-HeldFileStreamSha256 -Stream $runtimeLock -Label 'parent native source') -cne
            $approvedParentStageRuntimeSha256) { throw 'The trusted parent native source pin differs.' }
    $runtimeLock.Position = 0
    $reader = [IO.StreamReader]::new($runtimeLock, [Text.UTF8Encoding]::new($false, $true), $false, 4096, $true)
    try { $nativeSource = $reader.ReadToEnd() } finally { $reader.Dispose(); $runtimeLock.Position = 0 }
    $originalNamespace = 'namespace Stratus.ParentStage {'
    if ([regex]::Matches($nativeSource, [regex]::Escape($originalNamespace)).Count -ne 1) {
        throw 'The pinned native source must expose one reviewed namespace.'
    }
    # Compile only pinned bytes into a fresh namespace. A preloaded type with
    # the normal parent runtime name is not trusted merely because it exists.
    $namespace = 'Stratus.ParentStageGate' + [guid]::NewGuid().ToString('N')
    $compiled = @(Add-Type -TypeDefinition ($nativeSource.Replace(
        $originalNamespace, ('namespace ' + $namespace + ' {')
    )) -PassThru -ErrorAction Stop)
    $filesType = @($compiled | Where-Object { $_.FullName -ceq ($namespace + '.Files') })
    $processType = @($compiled | Where-Object { $_.FullName -ceq ($namespace + '.ProcessLease') })
    $launchType = @($compiled | Where-Object { $_.FullName -ceq ($namespace + '.IndependentWatchdogLaunch') })
    $fenceType = @($compiled | Where-Object { $_.FullName -ceq ($namespace + '.ExactFence') })
    $heartbeatType = @($compiled | Where-Object { $_.FullName -ceq ($namespace + '.Heartbeat') })
    $strictType = @($compiled | Where-Object { $_.FullName -ceq ($namespace + '.StrictJson') })
    foreach ($count in @($filesType.Count, $processType.Count, $launchType.Count,
        $fenceType.Count, $heartbeatType.Count, $strictType.Count)) {
        if ($count -ne 1) { throw 'Pinned parent native types are incomplete.' }
    }
    $script:parentStage = [pscustomobject]@{
        files = $filesType[0]; process_type = $processType[0]
        launch_type = $launchType[0]; fence_type = $fenceType[0]
        heartbeat_type = $heartbeatType[0]; strict_type = $strictType[0]
        evidence = [Collections.Generic.List[object]]::new()
        initialized = $false; contract = $null; lease = $null; binding = $null
        owner_process = $null; launch = $null; fence = $null; heartbeat = $null
        resume_attempted = $false; readiness = $null; source_manifest_sha256 = $null
        lease_sha256 = $null; ready_sha256 = $null; parent_armed_sha256 = $null
        preflight_complete = $false; bootstrap_complete = $false
        continuation_entered = $false; transition_registration_sha256 = $null
        transition_ack_sha256 = $null; continuation_phase = 'bootstrap'
        transition_launch_requested = $false; transition_launch_intent_sha256 = $null
        transition_launch_ack_sha256 = $null; transition_launch_ack = $null
        transition_control_attempted = $false; transition_control_consumed = $false
        deadline = [DateTimeOffset]::MinValue; armed_at = [DateTimeOffset]::MinValue
        maximum_lifetime = 0.0; clock = [Diagnostics.Stopwatch]::StartNew()
        sequence = 0L; last_publication = [DateTimeOffset]::MinValue
        heartbeat_clock = [Diagnostics.Stopwatch]::StartNew()
    }
    $state = $script:parentStage
    $state.evidence.Add([pscustomobject]@{ handle = $runtimeLock; hash = $approvedParentStageRuntimeSha256 })
}

function Assert-ParentStageLaunchContract {
    $state = $script:parentStage
    if ($null -eq $state -or $state.preflight_complete -or $null -ne $state.launch) {
        throw 'Parent launch preflight is single-use and precedes native creation.'
    }
    [void](Open-ParentStageEvidenceLock $ParentStageContractPath $ParentStageContractSha256)
    $contract = (Read-ParentStageDocument $ParentStageContractPath $ParentStageContractSha256).value
    Assert-ParentStageProperties $contract @('format', 'root', 'session_id', 'revision', 'owner', 'watchdog',
        'docker', 'hba', 'deadline_utc', 'heartbeat_ttl_seconds', 'artifacts',
        'postgres', 'provision', 'api', 'load_acceptance_path', 'r1') 'parent launch contract'
    foreach ($name in @('format', 'root', 'session_id', 'revision', 'deadline_utc', 'load_acceptance_path')) {
        Assert-NativeJsonString $contract.$name "parent launch $name"
    }
    Assert-NativeJsonInteger $contract.heartbeat_ttl_seconds 'parent launch heartbeat TTL'
    Assert-ParentStageProcessIdentity $contract.owner
    if ($contract.format -cne 'stratus-parent-contract-v2' -or $contract.root -cne $absoluteRoot -or
        $contract.session_id -cnotmatch '^[0-9a-f]{32}$' -or $contract.revision -cne $ExpectedRevision -or
        $contract.owner.pid -ne $PID -or $contract.heartbeat_ttl_seconds -lt 2 -or
        $contract.heartbeat_ttl_seconds -gt 30) { throw 'Parent launch contract identity differs.' }
    $artifactNames = @('mutation_fence', 'lease', 'parent_armed', 'ready', 'heartbeat', 'terminal',
        'containment', 'transition_registration', 'transition_ack', 'provision_intent', 'provision_intent_ack',
        'provision_attempt', 'provision_registration', 'provision_registration_ack', 'provision_complete',
        'api_intent', 'api_intent_ack', 'api_attempt', 'api_registration', 'api_registration_ack',
        'api_start_intent', 'api_start_ack', 'api_start_attempt', 'api_start_complete', 'observer_registration',
        'observer_ack', 'handoff_request', 'handoff_ack', 'handoff_commit', 'transition_start_fence',
        'transition_start_revoke', 'transition_start_attempt', 'transition_start_completion', 'transition_terminal',
        'parent_authorization', 'observer_intent', 'observer_complete', 'observer_ready', 'observer_output', 'load_accepted',
        'transition_launch_intent', 'transition_launch_ack', 'transition_capture_complete', 'transition_live')
    Assert-ParentStageProperties $contract.artifacts $artifactNames 'parent launch paths'
    $paths = $contract.artifacts
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    [void]$seen.Add($ParentStageContractPath)
    foreach ($path in @($contract.load_acceptance_path) + @($artifactNames | ForEach-Object { $paths.$_ })) {
        Assert-NativeJsonString $path 'parent launch artifact path'
        Assert-ParentStageCanonicalPath $path -MayBeAbsent
        if (-not $path.StartsWith($absoluteRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or
            -not $seen.Add($path) -or (Test-Path -LiteralPath $path)) {
            throw 'Parent launch artifacts must be absent, in root and pairwise distinct.'
        }
    }
    foreach ($pair in @(@($paths.transition_start_fence, $watchdogStartFencePath),
        @($paths.transition_start_revoke, $watchdogStartRevokePath), @($paths.transition_start_attempt, $watchdogStartAttemptPath),
        @($paths.transition_start_completion, $watchdogStartCompletionPath),
        @($paths.transition_terminal, $watchdogReceiptPath), @($paths.parent_authorization, $parentAuthorizationPath),
        @($paths.transition_live, $absoluteLivePath))) {
        if ($pair[0] -cne $pair[1]) { throw 'Parent launch paths do not bind this Transition.' }
    }
    Assert-ParentStageProperties $contract.watchdog @('script_path', 'script_sha256', 'host_path',
        'host_sha256', 'runtime_path', 'runtime_sha256') 'parent launch program'
    Assert-ParentStageProperties $contract.docker @('executable', 'sha256') 'parent launch Docker'
    Assert-ParentStageProperties $contract.hba @('path', 'sha256') 'parent launch HBA'
    foreach ($obj in @($contract.watchdog, $contract.docker, $contract.hba)) {
        foreach ($property in $obj.PSObject.Properties) { Assert-NativeJsonString $property.Value 'parent launch file field' }
    }
    $owner = [Diagnostics.Process]::GetCurrentProcess()
    try {
        $ownerHost = $owner.MainModule.FileName
        $ownerStarted = [DateTimeOffset]::new($owner.StartTime.ToUniversalTime()).ToString('o')
    }
    finally { $owner.Dispose() }
    # No arbitrary hash-matching executable from the contract may implement the
    # ready protocol. The child uses this exact gate host, derived independently.
    if ($contract.owner.executable -cne $ownerHost -or $contract.owner.start_time_utc -cne $ownerStarted -or
        $contract.watchdog.host_path -cne $ownerHost -or $contract.watchdog.host_sha256 -cne $contract.owner.sha256 -or
        $contract.watchdog.runtime_path -cne (Join-Path $absoluteRoot 'scripts\Stratus.ParentStage.Runtime.cs') -or
        $contract.watchdog.runtime_sha256 -cne $approvedParentStageRuntimeSha256 -or
        $contract.watchdog.script_path -cne (Join-Path $absoluteRoot 'scripts\Invoke-StratusParentStageWatchdog.ps1') -or
        $contract.watchdog.script_sha256 -cne $approvedParentStageWatchdogSha256 -or
        $contract.docker.executable -cne $script:dockerExecutable -or $contract.docker.sha256 -cne $approvedDockerExecutableSha256 -or
        $contract.hba.path -cne (Join-Path $absoluteRoot 'ops\postgres\stratus-pg_hba.conf') -or
        $contract.hba.sha256 -cne $fixedHbaSha256) { throw 'Parent launch trusted program/HBA binding differs.' }
    foreach ($pair in @(@($contract.watchdog.script_path, $approvedParentStageWatchdogSha256),
        @($contract.hba.path, $fixedHbaSha256), @($contract.docker.executable, $approvedDockerExecutableSha256))) {
        [void](Open-ParentStageEvidenceLock $pair[0] $pair[1])
    }
    $ownerExe = Open-ParentStageEvidenceLock $ownerHost $contract.owner.sha256
    $processType = $state.process_type
    $state.owner_process = $processType::Open($PID, $ownerStarted, $ownerHost, $contract.owner.sha256, $ownerExe)
    $script:parentStageOwnedHandles.Add($state.owner_process)
    foreach ($kind in @('postgres', 'api', 'provision')) {
        Assert-ParentStageProperties $contract.$kind @('name', 'image_id', 'config_image', 'service') "parent launch $kind"
        foreach ($property in $contract.$kind.PSObject.Properties) { Assert-NativeJsonString $property.Value "parent launch $kind field" }
        if ($contract.$kind.image_id -cnotmatch '^sha256:[0-9a-f]{64}$') { throw 'Parent launch image is not immutable.' }
    }
    if ($contract.postgres.name -cne 'weatherpoly-postgres-1' -or $contract.postgres.service -cne 'postgres' -or
        $contract.postgres.image_id -cne $postgres16ImageId -or $contract.postgres.config_image -cne $postgres16Image -or
        $contract.api.name -cne 'weatherpoly-api-1' -or $contract.api.service -cne 'api' -or
        $contract.api.image_id -cne $ExpectedImageId -or $contract.api.config_image -cne $CandidateImage -or
        $contract.provision.name -cne ('stratus-runtime-provision-' + $contract.session_id) -or
        $contract.provision.service -cne 'provision-runtime-role' -or
        $contract.provision.image_id -cne $postgres16ImageId -or $contract.provision.config_image -cne $postgres16Image) {
        throw 'Parent launch container identities differ.'
    }
    $state.contract = $contract
    Assert-ParentStageR1Anchor -Contract $contract -BeforeLaunch
    $state.deadline = ConvertTo-CanonicalUtcTime $contract.deadline_utc 'parent launch deadline'
    $state.maximum_lifetime = $state.deadline.Subtract([DateTimeOffset]::UtcNow).TotalSeconds
    if ($state.maximum_lifetime -lt 5 -or $state.maximum_lifetime -gt 1800) { throw 'Parent launch deadline is stale or invalid.' }
    $state.clock.Restart()
    $state.preflight_complete = $true
}

function Assert-ParentStagePreparedLease {
    param([object] $Document)
    $state = $script:parentStage; $contract = $state.contract; $lease = $Document.value
    Assert-ParentStageProperties $lease @('format', 'session_id', 'contract_path', 'contract_sha256',
        'watchdog', 'owner', 'mutation_fence_path', 'mutation_fence_sha256', 'r1_anchor',
        'source_manifest_sha256', 'armed_at', 'deadline_utc', 'authorization') 'parent prepared lease'
    foreach ($name in @('format', 'session_id', 'contract_path', 'contract_sha256', 'mutation_fence_path',
        'mutation_fence_sha256', 'source_manifest_sha256', 'armed_at', 'deadline_utc')) {
        Assert-NativeJsonString $lease.$name "parent prepared lease $name"
    }
    Assert-ParentStageProcessIdentity $lease.owner
    Assert-ParentStageProcessIdentity $lease.watchdog -WithoutHash
    Assert-ParentStageNoAuthorization $lease.authorization
    if ($lease.format -cne 'stratus-parent-lease-v2' -or $lease.session_id -cne $contract.session_id -or
        $lease.contract_path -cne $ParentStageContractPath -or $lease.contract_sha256 -cne $ParentStageContractSha256 -or
        $lease.watchdog.pid -ne $state.launch.ProcessId -or $lease.watchdog.start_time_utc -cne $state.launch.StartedAt -or
        $lease.watchdog.executable -cne $contract.watchdog.host_path -or
        $lease.mutation_fence_path -cne $contract.artifacts.mutation_fence -or
        $lease.mutation_fence_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        $lease.source_manifest_sha256 -cne $state.source_manifest_sha256 -or $lease.deadline_utc -cne $contract.deadline_utc) {
        throw 'Prepared lease is not bound to the invocation-owned launch and accepted R1.'
    }
    foreach ($name in @('pid', 'start_time_utc', 'executable', 'sha256')) {
        if ($lease.owner.$name -cne $contract.owner.$name) { throw 'Prepared lease owner differs.' }
    }
    $anchorFields = @('directory', 'acceptance_sha256', 'evidence_manifest_sha256', 'sealed_sha256')
    Assert-ParentStageProperties $lease.r1_anchor $anchorFields 'prepared lease R1'
    foreach ($name in $anchorFields) {
        Assert-NativeJsonString $lease.r1_anchor.$name "prepared lease R1 $name"
        if ($lease.r1_anchor.$name -cne $contract.r1.$name) { throw 'Prepared lease R1 differs.' }
    }
    $armed = ConvertTo-CanonicalUtcTime $lease.armed_at 'prepared lease armed time'
    $birth = ConvertTo-CanonicalUtcTime $state.launch.StartedAt 'owned launch birth'
    if ($armed -lt $birth.AddSeconds(-2) -or $armed -gt [DateTimeOffset]::UtcNow.AddSeconds(2) -or
        $armed -ge $state.deadline -or $state.deadline.Subtract($armed).TotalSeconds -gt 1800) {
        throw 'Prepared lease chronology differs.'
    }
    $state.armed_at = $armed
}

function Assert-ParentStageOwnedFence {
    $state = $script:parentStage
    if ($null -eq $state.fence -or $state.fence.GetType() -ne $state.fence_type -or
        $state.fence.Path -cne $state.contract.artifacts.mutation_fence -or
        $state.fence.Sha256 -cne $state.lease.mutation_fence_sha256) { throw 'Invocation-owned parent fence differs.' }
    $state.fence.AssertHeld()
    $bytes = $state.fence.ReadBytes()
    $strictType = $state.strict_type; $strictType::Validate($bytes)
    $value = ConvertFrom-StrictJsonObjectText -Json ([Text.Encoding]::UTF8.GetString($bytes)) -Label 'owned parent fence'
    Assert-ParentStageProperties $value @('format', 'session_id', 'owner_pid', 'owner_start_time_utc', 'contract_sha256') 'owned parent fence'
    Assert-NativeJsonInteger $value.owner_pid 'owned fence owner'
    foreach ($name in @('format', 'session_id', 'owner_start_time_utc', 'contract_sha256')) {
        Assert-NativeJsonString $value.$name "owned fence $name"
    }
    if ($value.format -cne 'stratus-parent-mutation-fence-v1' -or $value.session_id -cne $state.contract.session_id -or
        $value.owner_pid -ne $PID -or $value.owner_start_time_utc -cne $state.contract.owner.start_time_utc -or
        $value.contract_sha256 -cne $ParentStageContractSha256) { throw 'Owned fence document identity differs.' }
}

function Assert-ParentStageBootstrapWait {
    $state = $script:parentStage
    $state.owner_process.AssertAlive()
    if ([DateTimeOffset]::UtcNow -ge $state.deadline -or $state.clock.Elapsed.TotalSeconds -ge $state.maximum_lifetime) {
        throw 'Parent bootstrap hard deadline expired.'
    }
    foreach ($path in @($state.contract.artifacts.terminal, $state.contract.artifacts.containment)) {
        if (Test-Path -LiteralPath $path) { throw 'Parent bootstrap became terminal.' }
    }
    if ($null -ne $state.fence) { Assert-ParentStageOwnedFence }
    if ($null -ne $state.heartbeat) { $state.heartbeat.AssertHealthy() }
}

function Start-ParentStageBootstrap {
    $state = $script:parentStage
    if ($null -eq $state -or -not $state.preflight_complete -or $null -ne $state.launch -or $state.resume_attempted) {
        throw 'Parent bootstrap requires unused invocation-owned preflight.'
    }
    $contract = $state.contract; $paths = $contract.artifacts
    $launchType = $state.launch_type; $fenceType = $state.fence_type
    $heartbeatType = $state.heartbeat_type; $files = $state.files
    # Deliberate child environment; no inherited profiler, module, startup,
    # Docker endpoint or arbitrary PATH overrides. Runtime also checks its allowlist.
    $windows = [Environment]::GetFolderPath([Environment+SpecialFolder]::Windows)
    Assert-ParentStageCanonicalPath $windows
    $environment = [Collections.Generic.Dictionary[string,string]]::new([StringComparer]::OrdinalIgnoreCase)
    $environment.Add('SystemRoot', $windows); $environment.Add('WINDIR', $windows)
    $environment.Add('PATH', (([IO.Path]::GetDirectoryName($contract.watchdog.host_path)) + ';' + (Join-Path $windows 'System32')))
    foreach ($entry in @(@('USERPROFILE', 'UserProfile'), @('LOCALAPPDATA', 'LocalApplicationData'),
        @('APPDATA', 'ApplicationData'), @('ProgramData', 'CommonApplicationData'))) {
        $value = [Environment]::GetFolderPath([Environment+SpecialFolder]$entry[1])
        Assert-ParentStageCanonicalPath $value; $environment.Add($entry[0], $value)
    }
    $temp = Join-Path $environment['LOCALAPPDATA'] 'Temp'
    Assert-ParentStageCanonicalPath $temp
    $environment.Add('TEMP', $temp); $environment.Add('TMP', $temp)
    $environment.Add('ComSpec', (Join-Path $windows 'System32\cmd.exe'))
    Assert-ParentStageBootstrapWait
    $state.launch = $launchType::StartSuspendedWithControl($contract.watchdog.host_path, $contract.watchdog.host_sha256,
        $contract.watchdog.script_path, $contract.watchdog.script_sha256, $contract.watchdog.runtime_path,
        $contract.watchdog.runtime_sha256, $ParentStageContractPath, $ParentStageContractSha256,
        $contract.session_id, $paths.lease, $paths.ready, $absoluteRoot, $environment, 30000, 65536)
    # Latch before calling Resume: any ambiguous native result preserves ownership.
    $state.resume_attempted = $true
    $state.launch.Resume()
    while ($true) {
        Assert-ParentStageBootstrapWait
        if ($null -ne $state.launch.PollReady()) { throw 'Parent ready arrived before owner arming.' }
        if (Test-Path -LiteralPath $paths.lease) { break }
        Start-Sleep -Milliseconds 50
    }
    $leaseDocument = Read-ParentStageDocument $paths.lease
    Assert-ParentStagePreparedLease -Document $leaseDocument
    [void](Open-ParentStageEvidenceLock $paths.lease $leaseDocument.sha256)
    $state.lease = $leaseDocument.value; $state.lease_sha256 = $leaseDocument.sha256
    $state.fence = $fenceType::AcquireExisting($paths.mutation_fence, $state.lease.mutation_fence_sha256)
    Assert-ParentStageOwnedFence
    $state.heartbeat = $heartbeatType::new($paths.heartbeat, $contract.session_id, $state.lease_sha256, 500)
    $state.heartbeat.AssertHealthy()
    Assert-ParentStageOwnedHeartbeat -Boundary 'bootstrap before parent-armed'
    Assert-ParentStageBootstrapWait
    if ($null -ne $state.launch.PollReady()) { throw 'Parent ready arrived before owner arming.' }
    $armedBytes = [Text.UTF8Encoding]::new($false).GetBytes((([ordered]@{
        format = 'stratus-parent-parent-armed-v1'; session_id = $contract.session_id
        lease_sha256 = $state.lease_sha256; owner_pid = $PID; owner_start_time_utc = $contract.owner.start_time_utc
        published_at = [DateTimeOffset]::UtcNow.ToString('o'); mutation_fence_sha256 = $state.fence.Sha256
    }) | ConvertTo-Json -Depth 10 -Compress))
    $files::PublishNew($paths.parent_armed, $armedBytes)
    $state.parent_armed_sha256 = $files::Hash($armedBytes)
    [void](Open-ParentStageEvidenceLock $paths.parent_armed $state.parent_armed_sha256)
    do {
        Assert-ParentStageBootstrapWait
        $captured = $state.launch.PollReady()
        if ($null -eq $captured) { Start-Sleep -Milliseconds 50 }
    } while ($null -eq $captured)
    if ($captured.ProcessId -ne $state.launch.ProcessId -or $captured.StartedAt -cne $state.launch.StartedAt -or
        $captured.SessionId -cne $contract.session_id -or $captured.Nonce -cne $state.launch.Nonce -or
        $captured.LeaseSha256 -cne $state.lease_sha256) { throw 'Captured ready differs from owned prepared lease.' }
    $state.readiness = $captured; $state.ready_sha256 = $captured.ReadySha256
    [void](Open-ParentStageEvidenceLock $paths.ready $state.ready_sha256)
    $state.bootstrap_complete = $true
}

function Open-ParentStageBoundary {
    # Deliberately unreachable until the SAME invocation owns downstream phases
    # and terminal containment. Primitive/bootstrap wiring is not release acceptance.
    throw 'Parent-stage integration is WIP: same-process continuation and terminal lifetime are not accepted.'
    Initialize-ParentStageRuntime
    Assert-ParentStageLaunchContract
    Start-ParentStageBootstrap
    Complete-ParentStageBoundary
}

function Close-ParentStageBoundary {
    $state = $script:parentStage
    if ($null -ne $state -and $state.resume_attempted) {
        # No generic Transition return/exception may clear the independent launch,
        # exclusive fence, heartbeat or held source/HBA evidence. The entry blocker
        # remains until a bounded continuation/containment path owns this lifetime.
        throw 'Resumed parent session retained; ordinary Transition cleanup is not release authority.'
    }
    if ($null -ne $state -and $null -ne $state.launch) {
        # Only a never-resumed child is eligible. The native capability additionally
        # rejects ambiguous suspend/resume history and retains its handles on failure.
        $state.launch.Dispose()
    }
    for ($index = $script:parentStageOwnedHandles.Count - 1; $index -ge 0; $index--) {
        $script:parentStageOwnedHandles[$index].Dispose()
    }
    $script:parentStageOwnedHandles.Clear()
}

function Complete-ParentStageBoundary {
    $state = $script:parentStage
    if ($null -eq $state -or -not $state.bootstrap_complete -or $null -eq $state.readiness) {
        throw 'Parent-stage validation requires this invocation''s captured bootstrap readiness.'
    }
    $files = $state.files
    $ParentStageLeasePath = $state.contract.artifacts.lease
    $ParentStageLeaseSha256 = $state.lease_sha256
    $ParentStageReadyPath = $state.contract.artifacts.ready
    $ParentStageReadySha256 = $state.ready_sha256
    $ParentStageHeartbeatPath = $state.contract.artifacts.heartbeat
    $runtimePath = Join-Path $absoluteRoot 'scripts\Stratus.ParentStage.Runtime.cs'
    foreach ($pair in @(@($ParentStageContractPath, $ParentStageContractSha256),
        @($ParentStageLeasePath, $ParentStageLeaseSha256), @($ParentStageReadyPath, $ParentStageReadySha256))) {
        [void](Open-ParentStageEvidenceLock -Path $pair[0] -Hash $pair[1])
    }
    $contract = (Read-ParentStageDocument $ParentStageContractPath $ParentStageContractSha256).value
    $lease = (Read-ParentStageDocument $ParentStageLeasePath $ParentStageLeaseSha256).value
    $ready = (Read-ParentStageDocument $ParentStageReadyPath $ParentStageReadySha256).value
    $state.contract = $contract; $state.lease = $lease
    Assert-ParentStageProperties $contract @('format', 'root', 'session_id', 'revision', 'owner', 'watchdog',
        'docker', 'hba', 'deadline_utc', 'heartbeat_ttl_seconds', 'artifacts',
        'postgres', 'provision', 'api', 'load_acceptance_path', 'r1') 'parent-stage contract'
    Assert-ParentStageProperties $lease @('format', 'session_id', 'contract_path', 'contract_sha256',
        'watchdog', 'owner', 'mutation_fence_path', 'mutation_fence_sha256', 'r1_anchor',
        'source_manifest_sha256', 'armed_at', 'deadline_utc', 'authorization') 'parent-stage lease'
    Assert-ParentStageProperties $ready @('format', 'session_id', 'lease_sha256', 'acknowledged_at',
        'watchdog_pid', 'watchdog_start_time_utc', 'source_path', 'source_sha256',
        'boundary', 'authorization') 'parent-stage ready'
    foreach ($value in @($contract.format, $contract.root, $contract.session_id, $contract.revision,
        $contract.deadline_utc, $contract.load_acceptance_path, $lease.format, $lease.session_id,
        $lease.contract_path, $lease.contract_sha256, $lease.mutation_fence_path,
        $lease.mutation_fence_sha256, $lease.source_manifest_sha256, $lease.armed_at, $lease.deadline_utc,
        $ready.format, $ready.session_id, $ready.lease_sha256, $ready.acknowledged_at,
        $ready.watchdog_start_time_utc, $ready.source_path, $ready.source_sha256)) {
        Assert-NativeJsonString $value 'parent-stage contract/lease/ready string'
    }
    Assert-NativeJsonInteger $contract.heartbeat_ttl_seconds 'parent-stage heartbeat TTL'
    Assert-NativeJsonInteger $ready.watchdog_pid 'parent-stage ready PID'
    Assert-ParentStageProcessIdentity $contract.owner
    Assert-ParentStageProcessIdentity $lease.owner
    Assert-ParentStageProcessIdentity $lease.watchdog -WithoutHash
    if ($contract.format -cne 'stratus-parent-contract-v2' -or $contract.root -cne $absoluteRoot -or
        $contract.session_id -cnotmatch '^[0-9a-f]{32}$' -or $contract.revision -cne $ExpectedRevision -or
        $contract.owner.pid -ne $PID -or $lease.watchdog.pid -eq $PID -or
        $contract.heartbeat_ttl_seconds -lt 2 -or $contract.heartbeat_ttl_seconds -gt 30 -or
        $lease.format -cne 'stratus-parent-lease-v2' -or $lease.session_id -cne $contract.session_id -or
        $lease.contract_path -cne $ParentStageContractPath -or $lease.contract_sha256 -cne $ParentStageContractSha256 -or
        $lease.mutation_fence_path -cne $state.fence.Path -or
        $lease.mutation_fence_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        $lease.source_manifest_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        $lease.deadline_utc -cne $contract.deadline_utc -or
        $ready.format -cne 'stratus-parent-ack-v1' -or $ready.session_id -cne $contract.session_id -or
        $ready.lease_sha256 -cne $ParentStageLeaseSha256 -or $ready.watchdog_pid -ne $lease.watchdog.pid -or
        $ready.watchdog_start_time_utc -cne $lease.watchdog.start_time_utc -or
        $ready.source_sha256 -cnotmatch '^[0-9a-f]{64}$') {
        throw 'Parent-stage contract/lease/ready identity does not match this Transition owner.'
    }
    foreach ($name in @('pid', 'start_time_utc', 'executable', 'sha256')) {
        if ($lease.owner.$name -cne $contract.owner.$name) { throw 'Parent-stage lease owner differs.' }
    }
    $state.deadline = ConvertTo-CanonicalUtcTime $contract.deadline_utc 'parent-stage deadline'
    $state.armed_at = ConvertTo-CanonicalUtcTime $lease.armed_at 'parent-stage armed time'
    $readyTime = ConvertTo-CanonicalUtcTime $ready.acknowledged_at 'parent-stage ready time'
    # Do not reset the preflight monotonic deadline after bootstrap.
    if ($state.maximum_lifetime -le 0 -or $state.maximum_lifetime -gt 1800 -or
        $state.deadline.Subtract($state.armed_at).TotalSeconds -gt 1800 -or
        $state.armed_at -gt [DateTimeOffset]::UtcNow.AddSeconds(2) -or
        $readyTime -lt $state.armed_at -or $readyTime -gt [DateTimeOffset]::UtcNow.AddSeconds(2)) {
        throw 'Parent-stage lease/ready lifetime is stale or invalid.'
    }
    $artifactNames = @('mutation_fence', 'lease', 'parent_armed', 'ready', 'heartbeat', 'terminal',
        'containment', 'transition_registration', 'transition_ack', 'provision_intent', 'provision_intent_ack',
        'provision_attempt', 'provision_registration', 'provision_registration_ack', 'provision_complete',
        'api_intent', 'api_intent_ack', 'api_attempt', 'api_registration', 'api_registration_ack',
        'api_start_intent', 'api_start_ack', 'api_start_attempt', 'api_start_complete', 'observer_registration',
        'observer_ack', 'handoff_request', 'handoff_ack', 'handoff_commit', 'transition_start_fence',
        'transition_start_revoke', 'transition_start_attempt', 'transition_start_completion', 'transition_terminal',
        'parent_authorization', 'observer_intent', 'observer_complete', 'observer_ready', 'observer_output', 'load_accepted',
        'transition_launch_intent', 'transition_launch_ack', 'transition_capture_complete', 'transition_live')
    Assert-ParentStageProperties $contract.artifacts $artifactNames 'parent-stage artifact paths'
    $paths = $contract.artifacts
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($path in @($ParentStageContractPath, $contract.load_acceptance_path) +
        @($artifactNames | ForEach-Object { $paths.$_ })) {
        Assert-NativeJsonString $path 'parent-stage artifact path'
        Assert-ParentStageCanonicalPath $path -MayBeAbsent
        if (-not $path.StartsWith($absoluteRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or
            -not $seen.Add($path)) { throw 'Parent-stage paths escape root or alias another artifact.' }
    }
    foreach ($pair in @(@($paths.lease, $ParentStageLeasePath), @($paths.ready, $ParentStageReadyPath),
        @($paths.heartbeat, $ParentStageHeartbeatPath), @($paths.mutation_fence, $state.fence.Path),
        @($paths.parent_armed, $ready.source_path), @($paths.transition_start_fence, $watchdogStartFencePath),
        @($paths.transition_start_revoke, $watchdogStartRevokePath), @($paths.transition_start_attempt, $watchdogStartAttemptPath),
        @($paths.transition_start_completion, $watchdogStartCompletionPath),
        @($paths.transition_terminal, $watchdogReceiptPath), @($paths.parent_authorization, $parentAuthorizationPath),
        @($paths.transition_live, $absoluteLivePath))) {
        if ($pair[0] -cne $pair[1]) { throw 'Parent-stage artifact path does not bind this Transition.' }
    }
    Assert-ParentStageProperties $contract.watchdog @('script_path', 'script_sha256', 'host_path',
        'host_sha256', 'runtime_path', 'runtime_sha256') 'parent-stage program'
    Assert-ParentStageProperties $contract.docker @('executable', 'sha256') 'parent-stage Docker'
    Assert-ParentStageProperties $contract.hba @('path', 'sha256') 'parent-stage HBA'
    foreach ($obj in @($contract.watchdog, $contract.docker, $contract.hba)) {
        foreach ($property in $obj.PSObject.Properties) {
            Assert-NativeJsonString $property.Value 'parent-stage pinned file field'
        }
    }
    if ($contract.watchdog.runtime_path -cne $runtimePath -or
        $contract.watchdog.runtime_sha256 -cne $approvedParentStageRuntimeSha256 -or
        $contract.watchdog.script_path -cne (Join-Path $absoluteRoot 'scripts\Invoke-StratusParentStageWatchdog.ps1') -or
        $contract.watchdog.script_sha256 -cne $approvedParentStageWatchdogSha256 -or
        $contract.watchdog.host_path -cne $lease.watchdog.executable -or
        $contract.watchdog.host_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        $contract.docker.executable -cne $script:dockerExecutable -or
        $contract.docker.sha256 -cne $approvedDockerExecutableSha256 -or
        $contract.hba.path -cne (Join-Path $absoluteRoot 'ops\postgres\stratus-pg_hba.conf') -or
        $contract.hba.sha256 -cne $fixedHbaSha256) { throw 'Parent-stage pinned executable/HBA binding differs.' }
    foreach ($kind in @('postgres', 'api', 'provision')) {
        Assert-ParentStageProperties $contract.$kind @('name', 'image_id', 'config_image', 'service') `
            "parent-stage $kind identity"
        foreach ($property in $contract.$kind.PSObject.Properties) {
            Assert-NativeJsonString $property.Value "parent-stage $kind identity field"
        }
        if ($contract.$kind.image_id -cnotmatch '^sha256:[0-9a-f]{64}$') {
            throw 'Parent-stage container image identity is malformed.'
        }
    }
    if ($contract.postgres.name -cne 'weatherpoly-postgres-1' -or $contract.postgres.service -cne 'postgres' -or
        $contract.postgres.image_id -cne $postgres16ImageId -or $contract.postgres.config_image -cne $postgres16Image -or
        $contract.api.name -cne 'weatherpoly-api-1' -or $contract.api.service -cne 'api' -or
        $contract.api.image_id -cne $ExpectedImageId -or $contract.api.config_image -cne $CandidateImage -or
        $contract.provision.name -cne ('stratus-runtime-provision-' + $contract.session_id) -or
        $contract.provision.service -cne 'provision-runtime-role' -or
        $contract.provision.image_id -cne $postgres16ImageId -or $contract.provision.config_image -cne $postgres16Image) {
        throw 'Parent-stage retained PostgreSQL/candidate API/provision identities differ.'
    }
    foreach ($pair in @(@($contract.watchdog.script_path, $approvedParentStageWatchdogSha256),
        @($contract.hba.path, $fixedHbaSha256))) { [void](Open-ParentStageEvidenceLock $pair[0] $pair[1]) }
    $state.owner_process.AssertAlive()
    $captured = $state.launch.PollReady()
    if (-not [object]::ReferenceEquals($captured, $state.readiness) -or
        $lease.watchdog.pid -ne $captured.ProcessId -or $lease.watchdog.start_time_utc -cne $captured.StartedAt -or
        $ParentStageLeaseSha256 -cne $captured.LeaseSha256 -or $ParentStageReadySha256 -cne $captured.ReadySha256) {
        throw 'Parent-stage receipt is not from the held invocation-owned watchdog.'
    }
    Assert-ParentStageNoAuthorization $lease.authorization
    Assert-ParentStageNoAuthorization $ready.authorization
    Assert-ParentStageProperties $ready.boundary @('mutation_fence_sha256', 'postgres_stopped',
        'r1_acceptance_sha256', 'phase') 'parent-stage ready boundary'
    Assert-NativeJsonBoolean $ready.boundary.postgres_stopped 'parent-stage stopped proof'
    foreach ($name in @('mutation_fence_sha256', 'r1_acceptance_sha256', 'phase')) {
        Assert-NativeJsonString $ready.boundary.$name 'parent-stage ready boundary string'
    }
    [void](Open-ParentStageEvidenceLock $ready.source_path $ready.source_sha256)
    $armed = (Read-ParentStageDocument $ready.source_path $ready.source_sha256).value
    Assert-ParentStageProperties $armed @('format', 'session_id', 'lease_sha256', 'owner_pid',
        'owner_start_time_utc', 'published_at', 'mutation_fence_sha256') 'parent-stage parent-armed'
    Assert-NativeJsonInteger $armed.owner_pid 'parent-stage parent-armed owner'
    foreach ($name in @('format', 'session_id', 'lease_sha256', 'owner_start_time_utc',
        'published_at', 'mutation_fence_sha256')) { Assert-NativeJsonString $armed.$name 'parent-armed string' }
    $armedTime = ConvertTo-CanonicalUtcTime $armed.published_at 'parent-armed publication'
    if ($armed.format -cne 'stratus-parent-parent-armed-v1' -or $armed.session_id -cne $contract.session_id -or
        $armed.lease_sha256 -cne $ParentStageLeaseSha256 -or $armed.owner_pid -ne $PID -or
        $armed.owner_start_time_utc -cne $contract.owner.start_time_utc -or
        $armed.mutation_fence_sha256 -cne $lease.mutation_fence_sha256 -or
        $ready.source_sha256 -cne $state.parent_armed_sha256 -or
        $armedTime -lt $state.armed_at -or $armedTime -gt $readyTime -or
        $ready.boundary.mutation_fence_sha256 -cne $lease.mutation_fence_sha256 -or
        -not $ready.boundary.postgres_stopped -or $ready.boundary.phase -cne 'transition') {
        throw 'Parent-stage ready is not an acknowledgement of this owner-held fence.'
    }
    Assert-ParentStageR1Anchor -Contract $contract -Lease $lease -Ready $ready
    Assert-ParentStageOwnedFence
    $fenceBytes = $state.fence.ReadBytes()
    $strictType = $state.strict_type
    $strictType::Validate($fenceBytes)
    $fenceValue = ConvertFrom-StrictJsonObjectText -Json ([Text.Encoding]::UTF8.GetString($fenceBytes)) -Label 'parent mutation fence'
    Assert-ParentStageProperties $fenceValue @('format', 'session_id', 'owner_pid', 'owner_start_time_utc',
        'contract_sha256') 'parent mutation fence'
    Assert-NativeJsonInteger $fenceValue.owner_pid 'parent mutation fence owner'
    foreach ($name in @('format', 'session_id', 'owner_start_time_utc', 'contract_sha256')) {
        Assert-NativeJsonString $fenceValue.$name 'parent mutation fence string'
    }
    if ($fenceValue.format -cne 'stratus-parent-mutation-fence-v1' -or
        $fenceValue.session_id -cne $contract.session_id -or $fenceValue.owner_pid -ne $PID -or
        $fenceValue.owner_start_time_utc -cne $contract.owner.start_time_utc -or
        $fenceValue.contract_sha256 -cne $ParentStageContractSha256) { throw 'Parent mutation fence identity differs.' }
    $state.binding = [ordered]@{
        format = 'stratus-parent-stage-binding-v1'; session_id = $contract.session_id
        contract_path = $ParentStageContractPath; contract_sha256 = $ParentStageContractSha256
        lease_path = $ParentStageLeasePath; lease_sha256 = $ParentStageLeaseSha256
        ready_path = $ParentStageReadyPath; ready_sha256 = $ParentStageReadySha256
        heartbeat_path = $ParentStageHeartbeatPath; mutation_fence_path = $state.fence.Path
        mutation_fence_sha256 = $lease.mutation_fence_sha256
        watchdog = [ordered]@{ pid = $lease.watchdog.pid; start_time_utc = $lease.watchdog.start_time_utc
            executable = $lease.watchdog.executable; sha256 = $contract.watchdog.host_sha256 }
        owner = $contract.owner; deadline_utc = $contract.deadline_utc
        r1_anchor = $contract.r1; source_manifest_sha256 = $lease.source_manifest_sha256
    }
    $state.initialized = $true
    try { Assert-ParentStageBoundary -Boundary 'initial parent-stage acceptance' }
    catch { $state.initialized = $false; throw }
    $state.continuation_phase = 'transition'
}

function Assert-ParentStageTransitionAck {
    param([object] $Document, [string] $RegistrationSha256, [DateTimeOffset] $PublishedAt,
        [string] $PostgresStartedAt)
    $state = $script:parentStage; $ack = $Document.value
    Assert-ParentStageProperties $ack @('format', 'session_id', 'lease_sha256', 'acknowledged_at',
        'watchdog_pid', 'watchdog_start_time_utc', 'source_path', 'source_sha256',
        'boundary', 'authorization') 'parent transition acknowledgement'
    foreach ($name in @('format', 'session_id', 'lease_sha256', 'acknowledged_at',
        'watchdog_start_time_utc', 'source_path', 'source_sha256')) {
        Assert-NativeJsonString $ack.$name "parent transition acknowledgement $name"
    }
    Assert-NativeJsonInteger $ack.watchdog_pid 'parent transition acknowledgement PID'
    Assert-ParentStageProperties $ack.boundary @('postgres_started_at') 'parent transition acknowledgement boundary'
    Assert-NativeJsonString $ack.boundary.postgres_started_at 'parent transition acknowledged PostgreSQL start'
    Assert-ParentStageNoAuthorization $ack.authorization
    $ackTime = ConvertTo-CanonicalUtcTime $ack.acknowledged_at 'parent transition acknowledgement time'
    if ($ack.format -cne 'stratus-parent-ack-v1' -or $ack.session_id -cne $state.contract.session_id -or
        $ack.lease_sha256 -cne $state.lease_sha256 -or $ack.watchdog_pid -ne $state.launch.ProcessId -or
        $ack.watchdog_start_time_utc -cne $state.launch.StartedAt -or
        $ack.source_path -cne $state.contract.artifacts.transition_registration -or
        $ack.source_sha256 -cne $RegistrationSha256 -or $ack.boundary.postgres_started_at -cne $PostgresStartedAt -or
        $ackTime -lt $PublishedAt -or $ackTime -ge $state.deadline -or $ackTime -gt [DateTimeOffset]::UtcNow.AddSeconds(2)) {
        throw 'Parent transition acknowledgement is not exact, fresh and launch-bound.'
    }
}

function Invoke-ParentStageTransitionRegistration {
    param([string] $AuthorizationJson, [string] $PostgresStartedAt)
    $state = $script:parentStage
    if (-not $state.continuation_entered -or $state.continuation_phase -cne 'transition' -or
        $null -ne $state.transition_registration_sha256 -or $null -ne $state.transition_ack_sha256) {
        throw 'Parent transition registration requires the unused owned continuation phase.'
    }
    $paths = $state.contract.artifacts; $files = $state.files
    Assert-ParentStageBoundary -Boundary 'transition registration pre-publication'
    # Retain the actual bytes produced/validated by this invocation. Do not
    # accept a caller-provided receipt hash or reopen a replacement as authority.
    $authorizationHash = $files::Hash([Text.UTF8Encoding]::new($false).GetBytes($AuthorizationJson))
    if ($null -eq $watchdogTerminalReceipt -or $null -eq $watchdogExitProof) {
        throw 'Continuation lacks the retained Transition terminal and exit proof.'
    }
    $terminalHash = [string]$watchdogTerminalReceipt.sha256
    if ($terminalHash -cnotmatch '^[0-9a-f]{64}$') { throw 'Retained Transition terminal digest is malformed.' }
    [void](Open-ParentStageEvidenceLock $paths.parent_authorization $authorizationHash)
    [void](Open-ParentStageEvidenceLock $paths.transition_terminal $terminalHash)
    $authorization = (Read-ParentStageDocument $paths.parent_authorization $authorizationHash).value
    $terminal = (Read-ParentStageDocument $paths.transition_terminal $terminalHash).value
    # The complete Transition validators already ran in the same stack. Bind to
    # their retained proof objects, not just files whose format happens to match.
    if ($authorization.postgres_started_at -cne $PostgresStartedAt -or
        $authorization.session_id -cne $transitionSessionId -or
        $authorization.parent_stage.session_id -cne $state.contract.session_id -or
        $authorization.parent_stage.ready_sha256 -cne $state.ready_sha256 -or
        $terminal.session_id -cne $transitionSessionId) {
        throw 'Continuation lacks retained exact Transition authorization/terminal lineage.'
    }
    Assert-ParentStageBoundary -Boundary 'transition registration held-evidence publication'
    $published = [DateTimeOffset]::UtcNow
    $registration = [ordered]@{
        format = 'stratus-parent-transition-registration-v1'; session_id = $state.contract.session_id
        lease_sha256 = $state.lease_sha256; owner_pid = $PID
        owner_start_time_utc = $state.contract.owner.start_time_utc; published_at = $published.ToString('o')
        parent_authorization_sha256 = $authorizationHash; transition_terminal_sha256 = $terminalHash
        postgres_started_at = $PostgresStartedAt
    }
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($registration | ConvertTo-Json -Depth 10 -Compress))
    $files::PublishNew($paths.transition_registration, $bytes)
    $state.transition_registration_sha256 = $files::Hash($bytes)
    [void](Open-ParentStageEvidenceLock $paths.transition_registration $state.transition_registration_sha256)
    $wait = [Diagnostics.Stopwatch]::StartNew()
    while ($true) {
        Assert-ParentStageBoundary -Boundary 'transition registration acknowledgement wait'
        if ($wait.ElapsedMilliseconds -ge 15000) { throw 'Parent transition acknowledgement deadline expired.' }
        if (Test-Path -LiteralPath $paths.transition_ack) {
            $ack = Read-ParentStageDocument $paths.transition_ack
            Assert-ParentStageTransitionAck -Document $ack -RegistrationSha256 $state.transition_registration_sha256 `
                -PublishedAt $published -PostgresStartedAt $PostgresStartedAt
            [void](Open-ParentStageEvidenceLock $paths.transition_ack $ack.sha256)
            Assert-ParentStageBoundary -Boundary 'transition acknowledgement post-readback'
            if ($wait.ElapsedMilliseconds -ge 15000) { throw 'Parent transition acknowledgement readback exceeded deadline.' }
            $state.transition_ack_sha256 = $ack.sha256
            $state.continuation_phase = 'transition_registered'
            break
        }
        Start-Sleep -Milliseconds 50
    }
    # This binds message transport/ownership only. The child's current receiver
    # still needs the full dependency-closure validator before any later mutation.
}

function Assert-ParentStageControlFields {
    param([object] $Value, [string[]] $Strings, [string[]] $Integers = @(), [string[]] $Objects = @(), [string[]] $Booleans = @())
    # Pure schema/type guard only. This function cannot turn supplied metadata
    # into a native capability; the private response must be consumed separately.
    if ($null -eq $Value -or $Value.GetType().FullName -cne 'System.Management.Automation.PSCustomObject') {
        throw 'Parent CONTROL requires a native JSON object.'
    }
    $expected = @($Strings) + @($Integers) + @($Objects) + @($Booleans)
    $actual = @($Value.PSObject.Properties.Name)
    if ($actual.Count -ne $expected.Count) { throw 'Parent CONTROL property set differs.' }
    foreach ($name in $actual) {
        if ($name -cnotin $expected) { throw 'Parent CONTROL has an unknown or aliased property.' }
    }
    foreach ($name in $Strings) {
        if ($Value.$name -isnot [string]) { throw 'Parent CONTROL field must be a native string.' }
        if ($Value.$name.Length -gt 4096 -or $Value.$name.IndexOf([char]0) -ge 0 -or
            $Value.$name.Contains("`r") -or $Value.$name.Contains("`n")) {
            throw 'Parent CONTROL string exceeds its bound or contains a control character.'
        }
    }
    foreach ($name in $Integers) {
        if ($Value.$name -isnot [int] -and $Value.$name -isnot [long]) {
            throw 'Parent CONTROL field must be a native integer.'
        }
    }
    foreach ($name in $Objects) {
        if ($null -eq $Value.$name -or $Value.$name.GetType().FullName -cne 'System.Management.Automation.PSCustomObject') {
            throw 'Parent CONTROL nested field must be a native object.'
        }
    }
    foreach ($name in $Booleans) {
        if ($Value.$name -isnot [bool]) { throw 'Parent CONTROL field must be a native boolean.' }
    }
}

function ConvertTo-ParentStageControlTime {
    param([object] $Value)
    if ($Value -isnot [string]) { throw 'Parent CONTROL timestamp must be a native string.' }
    $parsed = [DateTimeOffset]::ParseExact($Value, 'o', [Globalization.CultureInfo]::InvariantCulture)
    if ($parsed.Offset -ne [TimeSpan]::Zero -or $parsed.ToString('o', [Globalization.CultureInfo]::InvariantCulture) -cne $Value) {
        throw 'Parent CONTROL timestamp must be canonical UTC.'
    }
    return $parsed
}

function Assert-ParentStageControlRequest {
    param([object] $Value)
    $strings = @('format','operation','parent_session_id','parent_lease_sha256','transition_session_id',
        'launch_intent_sha256','launch_ack_sha256','transition_nonce','transition_lease_sha256',
        'transition_ready_sha256','prestart_sha256','hard_deadline_utc','boundary')
    Assert-ParentStageControlFields -Value $Value -Strings $strings
    if ($Value.format -cne 'stratus-parent-transition-control-request-v1' -or
        $Value.operation -cne 'observe-transition-live' -or $Value.boundary -cne 'early-transition-control-probe') {
        throw 'Parent CONTROL request protocol or pilot boundary differs.'
    }
    foreach ($name in @('parent_session_id','transition_session_id')) {
        if ($Value.$name -cnotmatch '\A[0-9a-f]{32}\z') { throw 'Parent CONTROL session is invalid.' }
    }
    foreach ($name in @('parent_lease_sha256','launch_intent_sha256','launch_ack_sha256','transition_nonce',
        'transition_lease_sha256','transition_ready_sha256','prestart_sha256')) {
        if ($Value.$name -cnotmatch '\A[0-9a-f]{64}\z') { throw 'Parent CONTROL digest or nonce is invalid.' }
    }
    [void](ConvertTo-ParentStageControlTime $Value.hard_deadline_utc)
}

function New-ParentStageControlRequestBytes {
    param([object] $Value)
    Assert-ParentStageControlRequest $Value
    $bytes = [Text.UTF8Encoding]::new($false, $true).GetBytes(($Value | ConvertTo-Json -Depth 4 -Compress))
    if ($bytes.Length -lt 2 -or $bytes.Length -gt 4096) { throw 'Parent CONTROL request exceeds its byte bound.' }
    return ,$bytes
}

function Assert-ParentStageControlResponse {
    param([object] $Value, [object] $Request, [object] $ProcessId, [object] $ProcessStartedAt,
        [DateTimeOffset] $RequestStartedAt, [DateTimeOffset] $ResponseReceivedAt)
    Assert-ParentStageControlRequest $Request
    $strings = @('format','operation','parent_session_id','parent_lease_sha256','transition_session_id',
        'launch_intent_sha256','launch_ack_sha256','transition_nonce','transition_lease_sha256',
        'transition_ready_sha256','prestart_sha256','hard_deadline_utc','boundary',
        'process_started_at','observed_at','status')
    Assert-ParentStageControlFields -Value $Value -Strings $strings -Integers @('process_id') -Objects @('authorization')
    if ($Value.format -cne 'stratus-parent-transition-control-response-v1' -or $Value.status -cne 'live') {
        throw 'Parent CONTROL response is not a live pilot observation.'
    }
    foreach ($property in $Request.PSObject.Properties) {
        if ($property.Name -cne 'format' -and $Value.($property.Name) -cne $property.Value) {
            throw 'Parent CONTROL response does not bind the exact request.'
        }
    }
    if (($ProcessId -isnot [int] -and $ProcessId -isnot [long]) -or $ProcessId -le 0 -or $ProcessId -gt [int]::MaxValue -or
        $Value.process_id -ne $ProcessId -or $Value.process_started_at -cne $ProcessStartedAt) {
        throw 'Parent CONTROL response differs from the captured Transition identity.'
    }
    $birth = ConvertTo-ParentStageControlTime $ProcessStartedAt
    $observed = ConvertTo-ParentStageControlTime $Value.observed_at
    $deadline = ConvertTo-ParentStageControlTime $Request.hard_deadline_utc
    if ($ResponseReceivedAt -lt $RequestStartedAt -or $observed -lt $birth -or $observed -ge $deadline -or
        $observed -lt $RequestStartedAt.AddSeconds(-2) -or $observed -gt $ResponseReceivedAt.AddSeconds(2)) {
        throw 'Parent CONTROL observation time does not bind the current roundtrip.'
    }
    $flags = @('postgres_start_permitted','api_start_permitted','worker_start_permitted','safety_control_start_permitted',
        'live_transition_permitted','binance_kill_switch_removal_permitted','migration_container_permitted','container_remove_permitted')
    Assert-ParentStageControlFields -Value $Value.authorization -Strings @() -Booleans $flags
    foreach ($name in $flags) {
        if ($Value.authorization.$name) { throw 'Parent CONTROL telemetry cannot grant lifecycle authority.' }
    }
}

function Invoke-ParentStageTransitionLiveQuery {
    # Private capability pilot only. None of the parsed JSON or diagnostic fields
    # substitutes for the original native response's one-use Consume operation.
    $state = $script:parentStage
    if ($null -eq $state -or -not $state.initialized -or $state.continuation_phase -cne 'transition' -or
        $state.transition_control_attempted -or $state.transition_control_consumed -or $null -eq $state.transition_launch_ack -or
        $state.transition_launch_ack_sha256 -cnotmatch '\A[0-9a-f]{64}\z') {
        throw 'Parent CONTROL pilot requires the single held early launch acknowledgement.'
    }
    $launch = $state.launch
    $ack = $state.transition_launch_ack
    $request = [pscustomobject][ordered]@{
        format = 'stratus-parent-transition-control-request-v1'; operation = 'observe-transition-live'
        parent_session_id = $state.contract.session_id; parent_lease_sha256 = $state.lease_sha256
        transition_session_id = $ack.boundary.transition_session_id; launch_intent_sha256 = $state.transition_launch_intent_sha256
        launch_ack_sha256 = $state.transition_launch_ack_sha256; transition_nonce = $ack.boundary.nonce
        transition_lease_sha256 = $ack.boundary.lease_sha256; transition_ready_sha256 = $ack.boundary.ready_sha256
        prestart_sha256 = $ack.boundary.prestart_sha256; hard_deadline_utc = $ack.boundary.hard_deadline_utc
        boundary = 'early-transition-control-probe'
    }
    $bytes = New-ParentStageControlRequestBytes $request
    $payloadSha256 = Get-ByteArraySha256 -Bytes $bytes
    $responseType = $state.launch_type.Assembly.GetType($state.launch_type.Namespace + '.PrivateControlResponse', $true)
    Assert-ParentStageBoundary -Boundary 'private Transition CONTROL before request'
    $launch.AssertControlHealthy()
    $requestStartedAt = [DateTimeOffset]::UtcNow
    $state.transition_control_attempted = $true # never retry an uncertain private request
    $launch.BeginControlRequest($bytes)
    while ($true) {
        Assert-ParentStageBoundary -Boundary 'private Transition CONTROL response wait'
        $launch.AssertControlHealthy()
        $response = $launch.PollControlResponse()
        if ($null -ne $response) { break }
        Start-Sleep -Milliseconds 10
    }
    if (-not [object]::ReferenceEquals($state.launch, $launch) -or $response.GetType() -ne $responseType -or
        $response.RequestPayloadSha256 -cne $payloadSha256 -or $response.RequestSha256 -cnotmatch '\A[0-9a-f]{64}\z' -or
        $response.Challenge -cnotmatch '\A[0-9a-f]{64}\z' -or $response.Sequence -ne 1 -or
        $response.ObservedRoundtripMilliseconds -lt 0 -or $response.ObservedRoundtripMilliseconds -ge 1000) {
        throw 'Parent CONTROL response is not the exact original native private capability.'
    }
    $responseBytes = [Text.UTF8Encoding]::new($false, $true).GetBytes($response.PayloadText)
    if ($responseBytes.Length -lt 2 -or $responseBytes.Length -gt 4096 -or
        $response.PayloadText.Contains("`r") -or $response.PayloadText.Contains("`n")) {
        throw 'Parent CONTROL response exceeds its strict byte frame.'
    }
    $strictType = $state.strict_type
    $strictType::Validate($responseBytes)
    $value = ConvertFrom-StrictJsonObjectText -Json $response.PayloadText -Label 'private Transition CONTROL response'
    Assert-ParentStageControlResponse -Value $value -Request $request -ProcessId $ack.boundary.process_id `
        -ProcessStartedAt $ack.boundary.process_started_at -RequestStartedAt $requestStartedAt -ResponseReceivedAt ([DateTimeOffset]::UtcNow)
    Assert-ParentStageBoundary -Boundary 'private Transition CONTROL before consumption'
    $launch.AssertControlHealthy()
    $response.Consume()
    $launch.AssertControlHealthy()
    $state.transition_control_consumed = $true
    # Deliberately no emitted DTO/token and no mutation. The unconditional caller
    # WIP barrier remains until every downstream boundary and containment qualifies.
}

function Request-ParentStageTransitionLaunch {
    param([string] $TransitionSessionId, [string] $PreStartSha256, [string] $PreStartPayloadSha256,
        [string] $StartFenceSha256, [DateTimeOffset] $HardDeadline)
    $state = $script:parentStage
    if ($null -eq $state -or -not $state.initialized -or $state.transition_launch_requested -or
        $state.continuation_phase -cne 'transition') { throw 'Early Transition launch request must be single-use by its armed owner.' }
    Assert-ParentStageBoundary -Boundary 'early Transition launch intent'
    $paths = $state.contract.artifacts; $files = $state.files
    if ($TransitionSessionId -cnotmatch '\A[0-9a-f]{32}\z' -or $HardDeadline -ge $state.deadline -or
        $HardDeadline -le [DateTimeOffset]::UtcNow.AddSeconds(30)) { throw 'Early Transition launch session/deadline differs.' }
    foreach ($hash in @($PreStartSha256,$PreStartPayloadSha256,$StartFenceSha256)) {
        if ($hash -cnotmatch '\A[0-9a-f]{64}\z') { throw 'Early Transition launch requires exact source hashes.' }
    }
    [void](Open-ParentStageEvidenceLock $absolutePreStartPath $PreStartSha256)
    $publishedAt = [DateTimeOffset]::UtcNow
    $intent = [ordered]@{
        format = 'stratus-parent-transition-launch-intent-v1'; session_id = $state.contract.session_id
        lease_sha256 = $state.lease_sha256; owner_pid = $PID
        owner_start_time_utc = $state.contract.owner.start_time_utc; published_at = $publishedAt.ToString('o')
        transition_session_id = $TransitionSessionId; prestart_sha256 = $PreStartSha256
        prestart_payload_sha256 = $PreStartPayloadSha256; start_fence_sha256 = $StartFenceSha256
        hard_deadline_utc = $HardDeadline.ToString('o')
    }
    $bytes = [Text.UTF8Encoding]::new($false, $true).GetBytes(($intent | ConvertTo-Json -Depth 8 -Compress))
    $state.transition_launch_requested = $true # latch before publication; uncertain writes are not retries
    $files::PublishNew($paths.transition_launch_intent, $bytes)
    $state.transition_launch_intent_sha256 = $files::Hash($bytes)
    [void](Open-ParentStageEvidenceLock $paths.transition_launch_intent $state.transition_launch_intent_sha256)
    $wait = [Diagnostics.Stopwatch]::StartNew()
    while ($true) {
        Assert-ParentStageBoundary -Boundary 'early Transition captured READY acknowledgement'
        if ($wait.ElapsedMilliseconds -ge 30000) { throw 'Early Transition READY acknowledgement deadline expired.' }
        if (Test-Path -LiteralPath $paths.transition_launch_ack) {
            $document = Read-ParentStageDocument $paths.transition_launch_ack
            $ack = $document.value
            Assert-ParentStageProperties $ack @('format','session_id','lease_sha256','acknowledged_at','watchdog_pid',
                'watchdog_start_time_utc','source_path','source_sha256','boundary','authorization') 'early Transition ACK'
            foreach ($name in @('format','session_id','lease_sha256','acknowledged_at','watchdog_start_time_utc','source_path','source_sha256')) {
                Assert-NativeJsonString $ack.$name 'early Transition ACK string'
            }
            Assert-NativeJsonInteger $ack.watchdog_pid 'early Transition ACK parent PID'
            Assert-ParentStageNoAuthorization $ack.authorization
            $boundary = $ack.boundary
            Assert-ParentStageProperties $boundary @('transition_session_id','process_id','process_started_at','nonce',
                'lease_path','lease_sha256','ready_sha256','ready_bytes','argv_sha256','environment_sha256',
                'prestart_sha256','hard_deadline_utc') 'early Transition ACK boundary'
            foreach ($property in $boundary.PSObject.Properties) {
                if ($property.Name -cin @('process_id','ready_bytes')) { Assert-NativeJsonInteger $property.Value 'early Transition ACK integer' }
                else { Assert-NativeJsonString $property.Value 'early Transition ACK boundary string' }
            }
            foreach ($name in @('nonce','lease_sha256','ready_sha256','argv_sha256','environment_sha256','prestart_sha256')) {
                if ($boundary.$name -cnotmatch '\A[0-9a-f]{64}\z') { throw 'Early Transition ACK digest/nonce malformed.' }
            }
            $ackAt = ConvertTo-CanonicalUtcTime $ack.acknowledged_at 'early Transition ACK time'
            $startedAt = ConvertTo-CanonicalUtcTime $boundary.process_started_at 'early Transition process birth'
            if ($ack.format -cne 'stratus-parent-transition-launch-ack-v1' -or
                $ack.session_id -cne $state.contract.session_id -or $ack.lease_sha256 -cne $state.lease_sha256 -or
                $ack.watchdog_pid -ne $state.launch.ProcessId -or $ack.watchdog_start_time_utc -cne $state.launch.StartedAt -or
                $ack.source_path -cne $paths.transition_launch_intent -or $ack.source_sha256 -cne $state.transition_launch_intent_sha256 -or
                $boundary.transition_session_id -cne $TransitionSessionId -or $boundary.process_id -le 0 -or
                $boundary.process_id -eq $PID -or $boundary.process_id -eq $state.launch.ProcessId -or
                $boundary.lease_path -cne $watchdogLeasePath -or $boundary.prestart_sha256 -cne $PreStartSha256 -or
                $boundary.hard_deadline_utc -cne $HardDeadline.ToString('o') -or $boundary.ready_bytes -lt 2 -or
                $boundary.ready_bytes -gt 8192 -or $startedAt -lt $publishedAt -or $startedAt -gt $ackAt -or
                $ackAt -ge $HardDeadline -or $ackAt -gt [DateTimeOffset]::UtcNow.AddSeconds(2)) {
                throw 'Early Transition ACK is not from the invocation-owned parent and fixed intent.'
            }
            [void](Open-ParentStageEvidenceLock $paths.transition_launch_ack $document.sha256)
            Assert-ParentStageBoundary -Boundary 'early Transition ACK held readback'
            if ($wait.ElapsedMilliseconds -ge 30000) { throw 'Early Transition ACK readback exceeded deadline.' }
            $state.transition_launch_ack_sha256 = $document.sha256
            $state.transition_launch_ack = $ack
            break
        }
        Start-Sleep -Milliseconds 50
    }
    Invoke-ParentStageTransitionLiveQuery
    # An ACK is a historical capture statement, not a fresh native capability in
    # this process. Do not fabricate a Process/ExitProof for the legacy gate ABI.
    # Gate-owned heartbeat, each mutation's fresh-live exchange, two-frame terminal
    # ABI and independent containment/takeover must be qualified before proceeding.
    throw 'Parent-owned Transition fresh-live/terminal/containment integration is WIP; no PostgreSQL start is authorized.'
}

function Invoke-ParentStageContinuation {
    param([string] $AuthorizationJson, [string] $PostgresStartedAt)
    $state = $script:parentStage
    if ($null -eq $state -or -not $state.initialized -or $state.continuation_entered -or
        $state.continuation_phase -cne 'transition') { throw 'Parent continuation must be entered exactly once by the armed owner.' }
    Assert-ParentStageBoundary -Boundary 'owned continuation entry'
    $state.continuation_entered = $true
    Invoke-ParentStageTransitionRegistration -AuthorizationJson $AuthorizationJson -PostgresStartedAt $PostgresStartedAt
    # No caller callback, detached process, or returned intermediate JSON may own
    # the next phase. Its fixed provision/API/load command and completion protocol
    # is not implemented yet. Keep this blocker and both earlier entry blockers.
    throw 'Parent downstream provision/API/load continuation is WIP; intermediate authorization cannot be returned.'
}

function Assert-ParentStageBoundary {
    param([Parameter(Mandatory = $true)][string] $Boundary)
    $state = $script:parentStage
    if ($null -eq $state -or -not $state.initialized) {
        throw "Parent-stage guard is not armed at $Boundary."
    }
    $files = $state.files
    $state.owner_process.AssertAlive()
    $captured = $state.launch.PollReady()
    if (-not [object]::ReferenceEquals($captured, $state.readiness) -or
        $captured.LeaseSha256 -cne $state.lease_sha256 -or $captured.ReadySha256 -cne $state.ready_sha256) {
        throw "Invocation-owned parent readiness changed at $Boundary."
    }
    $state.heartbeat.AssertHealthy()
    foreach ($proof in $state.evidence) {
        if ($files::HashStream($proof.handle) -cne $proof.hash) {
            throw "Parent-stage held evidence drifted at $Boundary."
        }
    }
    Assert-ParentStageOwnedFence
    $now = [DateTimeOffset]::UtcNow
    if ($now -ge $state.deadline -or $state.clock.Elapsed.TotalSeconds -ge $state.maximum_lifetime) {
        throw "Parent-stage hard deadline expired at $Boundary."
    }
    foreach ($terminal in @($state.contract.artifacts.terminal, $state.contract.artifacts.containment)) {
        if (Test-Path -LiteralPath $terminal) { throw 'Parent-stage supervision has already become terminal.' }
    }
    Assert-ParentStageOwnedHeartbeat -Boundary $Boundary
}

function Assert-ParentStageOwnedHeartbeat {
    param([Parameter(Mandatory = $true)][string] $Boundary)
    $state = $script:parentStage
    $state.heartbeat.AssertHealthy()
    $now = [DateTimeOffset]::UtcNow
    $heartbeat = (Read-ParentStageDocument -Path $state.contract.artifacts.heartbeat -AtomicReplacement).value
    Assert-ParentStageProperties $heartbeat @('format', 'session_id', 'lease_sha256', 'owner_pid',
        'owner_start_time_utc', 'sequence', 'published_at') 'parent-stage heartbeat'
    foreach ($name in @('format', 'session_id', 'lease_sha256', 'owner_start_time_utc', 'published_at')) {
        Assert-NativeJsonString -Value $heartbeat.$name -Label "parent-stage heartbeat $name"
    }
    foreach ($name in @('owner_pid', 'sequence')) {
        Assert-NativeJsonInteger -Value $heartbeat.$name -Label "parent-stage heartbeat $name"
    }
    $published = ConvertTo-CanonicalUtcTime -Value $heartbeat.published_at -Label 'parent-stage heartbeat publication'
    if ($heartbeat.format -cne 'stratus-parent-heartbeat-v1' -or
        $heartbeat.session_id -cne $state.contract.session_id -or
        $heartbeat.lease_sha256 -cne $state.lease_sha256 -or
        $heartbeat.owner_pid -ne $PID -or
        $heartbeat.owner_start_time_utc -cne $state.contract.owner.start_time_utc -or
        $heartbeat.sequence -le 0 -or $heartbeat.sequence -lt $state.sequence -or
        $published -lt $state.armed_at -or $published -gt $now.AddSeconds(2) -or
        $published -lt $state.last_publication -or
        $now.Subtract($published).TotalSeconds -gt $state.contract.heartbeat_ttl_seconds) {
        throw "Parent-stage heartbeat is stale or not exact at $Boundary."
    }
    if ($heartbeat.sequence -eq $state.sequence) {
        if ($published -ne $state.last_publication -or
            $state.heartbeat_clock.Elapsed.TotalSeconds -gt $state.contract.heartbeat_ttl_seconds) {
            throw 'Parent-stage heartbeat publication stalled or was replayed.'
        }
    }
    else {
        if ($state.sequence -gt 0 -and $published -le $state.last_publication) {
            throw 'Parent-stage heartbeat timestamp did not advance.'
        }
        $state.sequence = [long]$heartbeat.sequence
        $state.last_publication = $published
        $state.heartbeat_clock.Restart()
    }
}

function Get-Sha256Text {
    param([Parameter(Mandatory = $true)][string] $Value)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Value)
        return [BitConverter]::ToString(
            $algorithm.ComputeHash($bytes)
        ).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $algorithm.Dispose()
    }
}

function Get-FileSha256 {
    param([Parameter(Mandatory = $true)][string] $LiteralPath)
    return (Get-FileHash -LiteralPath $LiteralPath -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-DirectFileSha256 {
    param([Parameter(Mandatory = $true)][string] $LiteralPath)
    $stream = [IO.FileStream]::new(
        [IO.Path]::GetFullPath($LiteralPath), [IO.FileMode]::Open,
        [IO.FileAccess]::Read, [IO.FileShare]::Read, 65536,
        [IO.FileOptions]::SequentialScan
    )
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString(
            $algorithm.ComputeHash($stream)
        ).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function Assert-RegularFile {
    param([Parameter(Mandatory = $true)][string] $LiteralPath)
    $item = Get-Item -LiteralPath $LiteralPath -Force
    if (
        $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        $item.Length -le 0
    ) {
        throw "Release evidence must be a non-empty regular file: $LiteralPath"
    }
    return $item
}

function Assert-RegularDirectory {
    param([Parameter(Mandatory = $true)][string] $LiteralPath)
    $item = Get-Item -LiteralPath $LiteralPath -Force
    if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Release path must be a regular directory, not a link: $LiteralPath"
    }
    return $item
}

function Assert-ExactProperties {
    param(
        [Parameter(Mandatory = $true)][object] $Value,
        [Parameter(Mandatory = $true)][string[]] $Expected,
        [Parameter(Mandatory = $true)][string] $Label
    )
    $actual = [string[]]@(
        $Value.PSObject.Properties |
            ForEach-Object { [string]$_.Name }
    )
    $wanted = [string[]]@($Expected)
    [Array]::Sort($actual, [StringComparer]::Ordinal)
    [Array]::Sort($wanted, [StringComparer]::Ordinal)
    if (($actual -join "`n") -cne ($wanted -join "`n")) {
        throw "$Label does not have the exact required property set."
    }
}

function Assert-NativeJsonObject {
    param([AllowNull()][object] $Value, [Parameter(Mandatory = $true)][string] $Label)
    if ($null -eq $Value -or $Value.GetType().FullName -cne 'System.Management.Automation.PSCustomObject') {
        throw "$Label must be a native JSON object."
    }
}

function Assert-NativeJsonString {
    param([AllowNull()][object] $Value, [Parameter(Mandatory = $true)][string] $Label)
    if ($null -eq $Value -or $Value.GetType() -ne [string]) {
        throw "$Label must be a native JSON string."
    }
}

function Assert-NativeJsonBoolean {
    param([AllowNull()][object] $Value, [Parameter(Mandatory = $true)][string] $Label)
    if ($null -eq $Value -or $Value.GetType() -ne [bool]) {
        throw "$Label must be a native JSON boolean."
    }
}

function Assert-NativeJsonInteger {
    param([AllowNull()][object] $Value, [Parameter(Mandatory = $true)][string] $Label)
    if ($null -eq $Value -or $Value.GetType().FullName -cnotin @('System.Int32', 'System.Int64')) {
        throw "$Label must be a native JSON integer."
    }
}

function ConvertFrom-StrictJsonObjectText {
    param(
        [Parameter(Mandatory = $true)][string] $Json,
        [Parameter(Mandatory = $true)][string] $Label
    )
    $trimmed = $Json.Trim()
    if (-not $trimmed.StartsWith('{') -or -not $trimmed.EndsWith('}')) {
        throw "$Label must have a native JSON object at its root."
    }
    try {
        $cmdlet = Get-Command -Name 'Microsoft.PowerShell.Utility\ConvertFrom-Json' `
            -CommandType Cmdlet -ErrorAction Stop
        $parameters = @{ InputObject = $trimmed }
        if ($cmdlet.Parameters.ContainsKey('DateKind')) {
            $parameters['DateKind'] = 'String'
        }
        $value = Microsoft.PowerShell.Utility\ConvertFrom-Json @parameters
    }
    catch {
        throw "$Label is not valid strict JSON."
    }
    Assert-NativeJsonObject -Value $value -Label $Label
    return $value
}

function Read-StrictJsonObjectFile {
    param(
        [Parameter(Mandatory = $true)][string] $LiteralPath,
        [Parameter(Mandatory = $true)][string] $Label
    )
    [void](Assert-RegularFile -LiteralPath $LiteralPath)
    return ConvertFrom-StrictJsonObjectText `
        -Json (Get-Content -LiteralPath $LiteralPath -Raw) -Label $Label
}

function Read-BoundedStrictJsonDocument {
    param(
        [Parameter(Mandatory = $true)][string] $LiteralPath,
        [Parameter(Mandatory = $true)][string] $Label,
        [Parameter(Mandatory = $true)][int64] $MaximumBytes
    )
    $item = Assert-RegularFile -LiteralPath $LiteralPath
    if ($MaximumBytes -lt 1 -or $item.Length -gt $MaximumBytes) {
        throw "$Label exceeds its fixed byte limit."
    }
    $stream = [IO.FileStream]::new(
        $item.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        [IO.FileShare]::Read, 4096, [IO.FileOptions]::SequentialScan
    )
    try {
        if ($stream.Length -ne $item.Length -or $stream.Length -gt $MaximumBytes) {
            throw "$Label changed before its bounded read."
        }
        $bytes = [byte[]]::new([int]$stream.Length)
        $offset = 0
        while ($offset -lt $bytes.Length) {
            $count = $stream.Read($bytes, $offset, $bytes.Length - $offset)
            if ($count -le 0) {
                throw "$Label ended before its declared byte length."
            }
            $offset += $count
        }
    }
    finally {
        $stream.Dispose()
    }
    if (
        $bytes.Length -ge 3 -and $bytes[0] -eq 0xef -and
        $bytes[1] -eq 0xbb -and $bytes[2] -eq 0xbf
    ) {
        throw "$Label must be UTF-8 without a byte-order mark."
    }
    try {
        $json = [Text.UTF8Encoding]::new($false, $true).GetString($bytes)
    }
    catch {
        throw "$Label is not canonical UTF-8."
    }
    $value = ConvertFrom-StrictJsonObjectText -Json $json -Label $Label
    return [pscustomobject]@{
        path = [IO.Path]::GetFullPath($item.FullName)
        bytes = [int64]$bytes.Length
        sha256 = Get-ByteArraySha256 -Bytes $bytes
        last_write_time_utc = ([DateTimeOffset]$item.LastWriteTimeUtc).ToString('o')
        json = $json
        value = $value
    }
}

function Assert-PathBelowRoot {
    param(
        [Parameter(Mandatory = $true)][string] $LiteralPath,
        [Parameter(Mandatory = $true)][string] $AbsoluteRoot,
        [Parameter(Mandatory = $true)][string] $Label
    )
    if ($LiteralPath.IndexOf([char]0) -ge 0) {
        throw "$Label contains a forbidden null character."
    }
    $resolved = [IO.Path]::GetFullPath($LiteralPath)
    $rootPrefix = [IO.Path]::GetFullPath($AbsoluteRoot).TrimEnd('\', '/') + `
        [IO.Path]::DirectorySeparatorChar
    if (-not $resolved.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label must be a create-new path below the production root."
    }
    [void](Assert-RegularDirectory -LiteralPath ([IO.Path]::GetDirectoryName($resolved)))
    return $resolved
}

function ConvertTo-CanonicalUtcTime {
    param(
        [Parameter(Mandatory = $true)][string] $Value,
        [Parameter(Mandatory = $true)][string] $Label
    )
    $parsed = ConvertTo-RequiredUtcTime -Value $Value -Label $Label
    if ($Value -cne $parsed.ToString('o')) {
        throw "$Label is not a canonical UTC DateTimeOffset value."
    }
    return $parsed
}

function Test-ExactPathEqual {
    param(
        [Parameter(Mandatory = $true)][string] $Left,
        [Parameter(Mandatory = $true)][string] $Right
    )
    return [StringComparer]::OrdinalIgnoreCase.Equals(
        [IO.Path]::GetFullPath($Left), [IO.Path]::GetFullPath($Right)
    )
}

function ConvertTo-WindowsCommandLineArgument {
    param([Parameter(Mandatory = $true)][string] $Value)
    if ($Value.IndexOf([char]0) -ge 0 -or $Value.Contains("`r") -or $Value.Contains("`n")) {
        throw 'A watchdog process argument contains a forbidden control character.'
    }
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') {
        return $Value
    }
    $builder = [Text.StringBuilder]::new()
    [void]$builder.Append('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') {
            $slashes += 1
            continue
        }
        if ($character -eq '"') {
            [void]$builder.Append(('\' * (($slashes * 2) + 1)))
            [void]$builder.Append('"')
        }
        else {
            if ($slashes -gt 0) {
                [void]$builder.Append(('\' * $slashes))
            }
            [void]$builder.Append($character)
        }
        $slashes = 0
    }
    if ($slashes -gt 0) {
        [void]$builder.Append(('\' * ($slashes * 2)))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Get-OneJsonObject {
    param(
        [Parameter(Mandatory = $true)][string[]] $Lines,
        [Parameter(Mandatory = $true)][string] $Label
    )
    $meaningful = @($Lines | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
    if ($meaningful.Count -ne 1) {
        throw "$Label did not return exactly one JSON object."
    }
    return ConvertFrom-StrictJsonObjectText -Json ([string]$meaningful[0]) -Label $Label
}

function Resolve-PinnedApplication {
    param(
        [Parameter(Mandatory = $true)][string] $Name,
        [Parameter(Mandatory = $true)][string] $Label,
        [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{64}$')]
        [string] $ApprovedSha256
    )
    $commands = @(Microsoft.PowerShell.Core\Get-Command -Name $Name -All `
        -CommandType Application -ErrorAction Stop)
    if ($commands.Count -lt 1) {
        throw "$Label application could not be resolved."
    }
    $matches = [Collections.Generic.List[object]]::new()
    $seenPaths = @{}
    foreach ($command in $commands) {
        $source = [string]$command.Source
        if ([string]::IsNullOrWhiteSpace($source)) {
            continue
        }
        $path = [IO.Path]::GetFullPath($source)
        if ($seenPaths.ContainsKey($path)) {
            continue
        }
        $seenPaths[$path] = $true
        $item = Assert-RegularFile -LiteralPath $path
        $sha256 = Get-FileSha256 -LiteralPath $path
        if ($sha256 -ceq $ApprovedSha256) {
            $matches.Add([pscustomobject]@{
                path = $path
                sha256 = $sha256
                bytes = [int64]$item.Length
            })
        }
    }
    if ($matches.Count -ne 1) {
        throw "$Label application resolution did not find exactly one approved executable hash."
    }
    return $matches[0]
}

function Open-PinnedDockerApplicationLock {
    param([Parameter(Mandatory = $true)][object] $Application)

    if ($null -ne $script:dockerExecutableLock) {
        throw 'The Docker executable byte lock was already acquired.'
    }
    $lock = [IO.FileStream]::new(
        [string]$Application.path,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read,
        4096,
        [IO.FileOptions]::SequentialScan
    )
    try {
        $algorithm = [Security.Cryptography.SHA256]::Create()
        try {
            $lockedHash = [BitConverter]::ToString(
                $algorithm.ComputeHash($lock)
            ).Replace('-', '').ToLowerInvariant()
            $lock.Position = 0
        }
        finally {
            $algorithm.Dispose()
        }
        if (
            $lockedHash -cne [string]$Application.sha256 -or
            $lock.Length -ne [int64]$Application.bytes
        ) {
            throw 'Docker executable changed while acquiring its byte lock.'
        }
        $script:dockerExecutableLock = $lock
        return [pscustomobject]@{
            path = [IO.Path]::GetFullPath([string]$Application.path)
            sha256 = $lockedHash
            bytes = [int64]$lock.Length
            read_lock_held = $true
        }
    }
    catch {
        $lock.Dispose()
        throw
    }
}

function Assert-ToolEnvironmentBoundary {
    $exactForbidden = @(
        'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH',
        'DOCKER_API_VERSION',
        'DOCKER_CONFIG', 'DOCKER_CLI_PLUGIN_EXTRA_DIRS', 'COMPOSE_PROVIDER',
        'GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_OBJECT_DIRECTORY',
        'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_INDEX_FILE'
    )
    foreach ($name in $exactForbidden) {
        if (Test-Path -LiteralPath "Env:$name") {
            throw "Release tool override environment variable is forbidden: $name"
        }
    }
    foreach ($key in [Environment]::GetEnvironmentVariables().Keys) {
        $name = [string]$key
        if ($name -imatch '^GIT_CONFIG_') {
            throw "Release Git configuration environment override is forbidden: $name"
        }
    }
}

function Get-PinnedApplicationBoundary {
    param(
        [Parameter(Mandatory = $true)][string] $LiteralPath,
        [Parameter(Mandatory = $true)][string] $ExpectedSha256,
        [Parameter(Mandatory = $true)][string] $Label
    )
    $item = Assert-RegularFile -LiteralPath $LiteralPath
    $isPinnedDocker = (
        -not [string]::IsNullOrWhiteSpace([string]$script:dockerExecutable) -and
        (Test-ExactPathEqual -Left $LiteralPath -Right $script:dockerExecutable)
    )
    if ($isPinnedDocker) {
        if (
            $null -eq $script:dockerExecutableLock -or
            $script:dockerExecutableLock.SafeFileHandle.IsClosed -or
            $script:dockerExecutableLock.SafeFileHandle.IsInvalid
        ) {
            throw "$Label executable byte lock is not held."
        }
        $script:dockerExecutableLock.Position = 0
        $algorithm = [Security.Cryptography.SHA256]::Create()
        try {
            $actualHash = [BitConverter]::ToString(
                $algorithm.ComputeHash($script:dockerExecutableLock)
            ).Replace('-', '').ToLowerInvariant()
            $script:dockerExecutableLock.Position = 0
        }
        finally {
            $algorithm.Dispose()
        }
        if ($script:dockerExecutableLock.Length -ne $item.Length) {
            throw "$Label executable byte length changed after it was pinned."
        }
    }
    else {
        $actualHash = Get-FileSha256 -LiteralPath $LiteralPath
    }
    if ($actualHash -cne $ExpectedSha256) {
        throw "$Label executable changed after it was pinned."
    }
    return [pscustomobject]@{
        path = [IO.Path]::GetFullPath($LiteralPath)
        sha256 = $actualHash
        bytes = [int64]$item.Length
    }
}

function Invoke-Docker {
    param([Parameter(Mandatory = $true)][string[]] $Arguments)
    if (
        [string]::IsNullOrWhiteSpace([string]$script:dockerExecutable) -or
        $null -eq $script:dockerExecutableLock -or
        $script:dockerExecutableLock.SafeFileHandle.IsClosed -or
        $script:dockerExecutableLock.SafeFileHandle.IsInvalid
    ) {
        throw 'The Docker application was not pinned before use.'
    }
    $priorErrorActionPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5.1 promotes redirected native stderr to a
        # terminating NativeCommandError when the caller uses Stop. Capture
        # it under Continue and decide only from the immediately sealed exit.
        $ErrorActionPreference = 'Continue'
        $effectiveArguments = @('--context', $approvedDockerContext) + @($Arguments)
        $output = @(& $script:dockerExecutable @effectiveArguments 2>&1)
        $nativeExitCode = [int]$LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $priorErrorActionPreference
    }
    if ($nativeExitCode -ne 0) {
        throw "A required Docker proof command failed (exit $nativeExitCode)."
    }
    if (
        $script:transitionWitnessArmed -and
        $Arguments.Count -ge 2 -and
        $Arguments[0] -ceq 'exec' -and
        $Arguments[1] -ceq $productionPostgresId
    ) {
        $script:transitionExpectedPostgresExecCount += 1
    }
    return @($output | ForEach-Object { [string]$_ })
}

function Invoke-Git {
    param(
        [Parameter(Mandatory = $true)][string] $Repository,
        [Parameter(Mandatory = $true)][string[]] $Arguments
    )
    if ([string]::IsNullOrWhiteSpace([string]$script:gitExecutable)) {
        throw 'The Git application was not pinned before use.'
    }
    $priorErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& $script:gitExecutable -C $Repository @Arguments 2>&1)
        $nativeExitCode = [int]$LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $priorErrorActionPreference
    }
    if ($nativeExitCode -ne 0) {
        throw "A required Git provenance command failed (exit $nativeExitCode)."
    }
    return @($output | ForEach-Object { [string]$_ })
}

function ConvertTo-RequiredUtcTime {
    param(
        [Parameter(Mandatory = $true)][string] $Value,
        [Parameter(Mandatory = $true)][string] $Label
    )
    $parsed = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse(
        $Value,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::AssumeUniversal,
        [ref]$parsed
    )) {
        throw "$Label is not an ISO-8601 timestamp."
    }
    return $parsed.ToUniversalTime()
}

function Write-CreateNewJson {
    param(
        [Parameter(Mandatory = $true)][string] $LiteralPath,
        [Parameter(Mandatory = $true)][object] $Value
    )
    $parent = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($LiteralPath))
    [void](Assert-RegularDirectory -LiteralPath $parent)
    $serialized = $Value | ConvertTo-Json -Depth 20
    if ($serialized -match '(?i)password|api[_-]?key|secret|database_url|dsn|token') {
        throw 'Successor evidence serialization contains a forbidden secret-shaped field.'
    }
    $resolvedPath = [IO.Path]::GetFullPath($LiteralPath)
    $created = $false
    $stream = $null
    $failure = $null
    $cleanupFailures = [Collections.Generic.List[string]]::new()
    try {
        $stream = [IO.FileStream]::new(
            $resolvedPath,
            [IO.FileMode]::CreateNew,
            [IO.FileAccess]::Write,
            [IO.FileShare]::None,
            4096,
            [IO.FileOptions]::WriteThrough
        )
        $created = $true
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($serialized)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
        # A CreateNew artifact is not accepted until its exclusive handle has
        # closed cleanly. A close failure therefore enters the same
        # invalidation/removal path as a write or durable-flush failure.
        $stream.Dispose()
        $stream = $null
    }
    catch {
        $failure = $_
    }
    if ($null -ne $failure -and $created -and $null -ne $stream) {
        try {
            $stream.SetLength(0)
            $stream.Position = 0
            $stream.Flush($true)
        }
        catch {
            $cleanupFailures.Add(
                "partial JSON invalidation failed: $($_.Exception.Message)"
            )
        }
    }
    try {
        if ($null -ne $stream) { $stream.Dispose() }
    }
    catch {
        $cleanupFailures.Add("JSON stream close failed: $($_.Exception.Message)")
    }
    if ($null -ne $failure -and $created -and
        (Test-Path -LiteralPath $resolvedPath)) {
        try { Remove-Item -LiteralPath $resolvedPath -Force -ErrorAction Stop }
        catch {
            $cleanupFailures.Add("partial JSON removal failed: $($_.Exception.Message)")
        }
    }
    if ($null -ne $failure) {
        if ($cleanupFailures.Count -gt 0) {
            throw [InvalidOperationException]::new(
                "CreateNew JSON publication failed. Original: $($failure.Exception.Message) Cleanup: $($cleanupFailures -join ' | ')",
                $failure.Exception
            )
        }
        throw $failure
    }
    if ($cleanupFailures.Count -gt 0) {
        throw [InvalidOperationException]::new(
            "CreateNew JSON publication cleanup failed: $($cleanupFailures -join ' | ')"
        )
    }
}

function Publish-ParentAuthorization {
    param(
        [Parameter(Mandatory = $true)][string] $LiteralPath,
        [Parameter(Mandatory = $true)][object] $Value,
        [Parameter(Mandatory = $true)][ref] $Published,
        [Parameter(Mandatory = $true)][DateTimeOffset] $ObservedAt,
        [Parameter(Mandatory = $true)][DateTimeOffset] $NotAfter,
        [Parameter(Mandatory = $true)][int] $MaximumDelaySeconds
    )

    $Published.Value = $false
    $parent = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($LiteralPath))
    [void](Assert-RegularDirectory -LiteralPath $parent)
    $resolvedPath = [IO.Path]::GetFullPath($LiteralPath)
    $stream = $null
    $created = $false
    $failure = $null
    $cleanupFailures = [Collections.Generic.List[string]]::new()
    $serialized = $null
    try {
        $stream = [IO.FileStream]::new(
            $resolvedPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite,
            [IO.FileShare]::None, 4096, [IO.FileOptions]::WriteThrough
        )
        $created = $true
        $publishStartedAt = [DateTimeOffset]::UtcNow
        if (
            $publishStartedAt -gt $NotAfter -or
            $publishStartedAt -lt $ObservedAt.AddSeconds(-$clockSkewSeconds) -or
            $publishStartedAt -gt $ObservedAt.AddSeconds($MaximumDelaySeconds)
        ) {
            throw 'Parent authorization publication missed its barrier-bound window.'
        }
        $Value.publication_started_at = $publishStartedAt.ToString('o')
        $serialized = $Value | ConvertTo-Json -Depth 20
        if ($serialized -match '(?i)password|api[_-]?key|secret|database_url|dsn|token') {
            throw 'Parent authorization contains a forbidden secret-shaped field.'
        }
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($serialized)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
        $durableFlushObservedAt = [DateTimeOffset]::UtcNow
        if (
            $durableFlushObservedAt -gt $NotAfter -or
            $durableFlushObservedAt -gt
                $ObservedAt.AddSeconds($MaximumDelaySeconds)
        ) {
            throw 'Parent authorization durable flush missed its barrier-bound window.'
        }
        $stream.Position = 0
        $readback = [byte[]]::new($bytes.Length)
        $readOffset = 0
        while ($readOffset -lt $readback.Length) {
            $readCount = $stream.Read(
                $readback, $readOffset, $readback.Length - $readOffset
            )
            if ($readCount -le 0) {
                throw 'Parent authorization same-handle readback ended early.'
            }
            $readOffset += $readCount
        }
        if ($stream.ReadByte() -ne -1 -or
            (Get-ByteArraySha256 -Bytes $readback) -cne
                (Get-ByteArraySha256 -Bytes $bytes)) {
            throw 'Parent authorization same-handle readback differs from written bytes.'
        }
        # Handle closure is the last throwable publication check. Until it
        # succeeds, the artifact remains revocable and must never be reported
        # as published.
        $stream.Dispose()
        $stream = $null
        # The final path remains exclusively held until this point. Only
        # bytes that passed the post-flush time and same-handle hash boundary
        # are durably published. Authority still requires the parent-stage
        # post-publication boundary and absence of a later start revocation.
        $Published.Value = $true
    }
    catch {
        $failure = $_
    }
    if (-not $Published.Value -and $created -and $null -ne $stream) {
        try {
            # Keep a failed final-path reservation non-authorizing even when
            # later deletion is denied. FileShare.None excludes readers until
            # the truncation has itself reached stable storage.
            $stream.SetLength(0)
            $stream.Position = 0
            $stream.Flush($true)
        }
        catch {
            $cleanupFailures.Add(
                "authorization invalidation failed: $($_.Exception.Message)"
            )
        }
    }
    try {
        if ($null -ne $stream) { $stream.Dispose() }
    }
    catch {
        if (-not $Published.Value) {
            $cleanupFailures.Add(
                "authorization stream close failed: $($_.Exception.Message)"
            )
        }
    }
    if ($null -ne $failure) {
        if (-not $Published.Value -and $created -and
            (Test-Path -LiteralPath $resolvedPath)) {
            try {
                Remove-Item -LiteralPath $resolvedPath -Force -ErrorAction Stop
            }
            catch {
                $cleanupFailures.Add(
                    "authorization removal failed: $($_.Exception.Message)"
                )
            }
        }
        if ($cleanupFailures.Count -gt 0) {
            throw [InvalidOperationException]::new(
                "Parent authorization publication failed. Original: $($failure.Exception.Message) Cleanup: $($cleanupFailures -join ' | ')",
                $failure.Exception
            )
        }
        throw $failure
    }
    return $serialized
}

function Get-HeldFileStreamSha256 {
    param(
        [Parameter(Mandatory = $true)]
        [IO.FileStream] $Stream,
        [Parameter(Mandatory = $true)]
        [string] $Label
    )
    if ($Stream.SafeFileHandle.IsClosed -or $Stream.SafeFileHandle.IsInvalid) {
        throw "$Label handle is not held."
    }
    $Stream.Position = 0
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString(
            $algorithm.ComputeHash($Stream)
        ).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $algorithm.Dispose()
        $Stream.Position = 0
    }
}

function Assert-PostgresStartNotRevoked {
    param([Parameter(Mandatory = $true)][string] $RevokePath)

    # Path existence alone is the fail-closed signal.  A truncated, malformed,
    # or adversarial marker must still make a PostgreSQL start impossible.
    if (Test-Path -LiteralPath $RevokePath) {
        throw 'The successor watchdog durably revoked PostgreSQL start authorization.'
    }
}

function Open-ExclusivePostgresStartFence {
    param(
        [Parameter(Mandatory = $true)][string] $FencePath,
        [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{64}$')]
        [string] $ExpectedSha256,
        [Parameter(Mandatory = $true)][string] $RevokePath,
        [Parameter(Mandatory = $true)][DateTimeOffset] $AcquireBy
    )

    while ([DateTimeOffset]::UtcNow -lt $AcquireBy) {
        Assert-PostgresStartNotRevoked -RevokePath $RevokePath
        $stream = $null
        try {
            $stream = [IO.FileStream]::new(
                $FencePath, [IO.FileMode]::Open, [IO.FileAccess]::Read,
                [IO.FileShare]::None, 4096, [IO.FileOptions]::SequentialScan
            )
        }
        catch [IO.IOException] {
            Start-Sleep -Milliseconds 50
            continue
        }
        try {
            if ($stream.Length -le 0) {
                throw 'The successor PostgreSQL start fence is empty.'
            }
            $sha256 = Get-HeldFileStreamSha256 -Stream $stream `
                -Label 'successor PostgreSQL start fence'
            if ($sha256 -cne $ExpectedSha256) {
                throw 'The successor PostgreSQL start fence changed after handoff.'
            }
            Assert-PostgresStartNotRevoked -RevokePath $RevokePath
            return $stream
        }
        catch {
            $stream.Dispose()
            throw
        }
    }
    Assert-PostgresStartNotRevoked -RevokePath $RevokePath
    throw 'The successor PostgreSQL start fence could not be acquired before its reserve boundary.'
}

function Get-OptionalPropertyValue {
    param(
        [AllowNull()][object] $InputObject,
        [Parameter(Mandatory = $true)][string] $Name
    )
    if ($null -eq $InputObject) {
        return $null
    }
    $property = $InputObject.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }
    return $property.Value
}

function Assert-StringArray {
    param([AllowNull()][object] $Value, [Parameter(Mandatory = $true)][string] $Label)
    if ($null -eq $Value -or $Value.GetType() -ne [object[]]) {
        throw "$Label must be a native JSON array."
    }
    foreach ($entry in $Value) {
        Assert-NativeJsonString -Value $entry -Label "$Label entry"
    }
}

function ConvertFrom-StrictManifest {
    param([Parameter(Mandatory = $true)][string] $LiteralPath)
    $required = @(
        'format', 'archive', 'database', 'schema_head', 'schema_head_before',
        'schema_head_after', 'started_at', 'completed_at', 'archive_bytes',
        'archive_sha256', 'catalog_entries', 'catalog_sha256', 'catalog_verified',
        'schema_stable_during_dump', 'full_restore_verified'
    )
    $values = [ordered]@{}
    foreach ($line in @(Get-Content -LiteralPath $LiteralPath)) {
        if ([string]::IsNullOrWhiteSpace([string]$line)) {
            continue
        }
        if ([string]$line -notmatch '^[a-z0-9_]+=[^\r\n]+$') {
            throw 'Backup manifest contains a malformed line.'
        }
        $pair = ([string]$line).Split('=', 2)
        if ($values.Contains($pair[0])) {
            throw "Backup manifest repeats key $($pair[0])."
        }
        $values[$pair[0]] = $pair[1]
    }
    $actualKeys = [string[]]@($values.Keys)
    $requiredKeys = [string[]]@($required)
    [Array]::Sort($actualKeys, [StringComparer]::Ordinal)
    [Array]::Sort($requiredKeys, [StringComparer]::Ordinal)
    if (($actualKeys -join "`n") -cne ($requiredKeys -join "`n")) {
        throw 'Backup manifest does not have the exact v2 property set.'
    }
    return $values
}

function Assert-HistoricalLineage {
    param(
        [switch] $ValidateBackupPayload,
        [switch] $ValidateCatalogProbe
    )

    if ($ValidateCatalogProbe -and -not $ValidateBackupPayload) {
        throw 'The archive catalog probe requires immutable backup validation.'
    }

    $resolvedBackup = [IO.Path]::GetFullPath($BackupDirectory)
    $resolvedAttestation = [IO.Path]::GetFullPath($PreMigrationAttestationPath)
    $resolvedLineage = [IO.Path]::GetFullPath($LineageDirectory)
    [void](Assert-RegularDirectory -LiteralPath $resolvedBackup)
    [void](Assert-RegularDirectory -LiteralPath $resolvedLineage)
    if ($resolvedAttestation -ine (Join-Path $resolvedBackup 'pre-migration-attestation.json')) {
        throw 'PreMigrationAttestationPath is not the exact file inside BackupDirectory.'
    }
    [void](Assert-RegularFile -LiteralPath $resolvedAttestation)
    if ((Get-FileSha256 -LiteralPath $resolvedAttestation) -cne $preMigrationAttestationSha256) {
        throw 'The historical pre-migration attestation outer SHA-256 is not the reviewed trust root.'
    }

    $attestation = Read-StrictJsonObjectFile -LiteralPath $resolvedAttestation `
        -Label 'historical pre-migration attestation'
    Assert-ExactProperties -Value $attestation -Label 'historical pre-migration attestation' `
        -Expected @('format', 'accepted', 'payload', 'payload_sha256')
    Assert-NativeJsonString -Value $attestation.format -Label 'attestation format'
    Assert-NativeJsonBoolean -Value $attestation.accepted -Label 'attestation accepted'
    Assert-NativeJsonObject -Value $attestation.payload -Label 'attestation payload'
    Assert-NativeJsonString -Value $attestation.payload_sha256 -Label 'attestation payload hash'
    if (
        $attestation.format -cne 'stratus-pre-migration-gate-v1' -or
        -not $attestation.accepted -or
        $attestation.payload_sha256 -cne $preMigrationPayloadSha256
    ) {
        throw 'The historical pre-migration attestation envelope is not exact.'
    }
    Assert-ExactProperties -Value $attestation.payload -Label 'attestation payload' -Expected @(
        'source_schema_head', 'target_schema_head', 'snapshot', 'restore', 'candidate',
        'postgres', 'migrate', 'initial_live_preconditions', 'accepted_at'
    )
    foreach ($name in @('snapshot', 'restore', 'candidate', 'postgres', 'migrate', 'initial_live_preconditions')) {
        Assert-NativeJsonObject -Value $attestation.payload.$name -Label "attestation payload $name"
    }
    foreach ($name in @('source_schema_head', 'target_schema_head', 'accepted_at')) {
        Assert-NativeJsonString -Value $attestation.payload.$name -Label "attestation payload $name"
    }
    $payloadJson = $attestation.payload | ConvertTo-Json -Depth 12 -Compress
    if ((Get-Sha256Text -Value $payloadJson) -cne $attestation.payload_sha256) {
        throw 'The historical pre-migration attestation payload hash is invalid.'
    }

    $candidate = $attestation.payload.candidate
    Assert-ExactProperties -Value $candidate -Label 'historical candidate' `
        -Expected @('revision', 'image', 'image_id')
    $migrate = $attestation.payload.migrate
    Assert-ExactProperties -Value $migrate -Label 'historical migrate identity' -Expected @(
        'container_id', 'image_id', 'config_image', 'command', 'entrypoint', 'user',
        'mounts', 'compose_project', 'compose_working_dir', 'compose_config_file',
        'compose_config_hash', 'compose_depends_on'
    )
    Assert-StringArray -Value $migrate.command -Label 'historical migrate command'
    if (
        $attestation.payload.source_schema_head -cne $sourceSchemaHead -or
        $attestation.payload.target_schema_head -cne $targetSchemaHead -or
        $candidate.revision -cne $predecessorRevision -or
        $candidate.image -cne $predecessorImage -or
        $candidate.image_id -cne $predecessorImageId -or
        $migrate.image_id -cne $predecessorImageId -or
        $migrate.config_image -cne $predecessorImage -or
        ($migrate.command -join "`n") -cne "alembic`nupgrade`nhead" -or
        $null -ne $migrate.entrypoint -or
        $migrate.user -cne 'stratus' -or
        @($migrate.mounts).Count -ne 0 -or
        $migrate.compose_project -cne 'weatherpoly' -or
        $migrate.compose_depends_on -cne ''
    ) {
        throw 'The historical candidate/migrate identity is not the exact reviewed legacy boundary.'
    }

    $historicalPostgres = $attestation.payload.postgres
    Assert-ExactProperties -Value $historicalPostgres -Label 'historical PostgreSQL identity' `
        -Expected @('container_id', 'image_id', 'config_image', 'restart_count', 'started_at')
    if (
        $historicalPostgres.container_id -cne $productionPostgresId -or
        $historicalPostgres.image_id -cne $postgres16ImageId -or
        $historicalPostgres.config_image -cne $postgres16Image -or
        $historicalPostgres.restart_count -ne 0
    ) {
        throw 'The historical PostgreSQL identity is not exact.'
    }
    [void](ConvertTo-RequiredUtcTime -Value ([string]$historicalPostgres.started_at) `
        -Label 'historical PostgreSQL started_at')

    $lineage = [ordered]@{}
    foreach ($entry in $lineageArtifacts.GetEnumerator()) {
        $path = Join-Path $resolvedLineage ([string]$entry.Key)
        [void](Assert-RegularFile -LiteralPath $path)
        $actualHash = Get-FileSha256 -LiteralPath $path
        if ($actualHash -cne [string]$entry.Value) {
            throw "Historical lineage SHA-256 drifted: $($entry.Key)"
        }
        $lineage[[string]$entry.Key] = Read-StrictJsonObjectFile `
            -LiteralPath $path -Label "historical lineage $($entry.Key)"
    }
    $stage = $lineage['09-stopped-migrate-stage-acceptance.json']
    if (
        $stage.format -cne 'stratus-section3-stopped-migrate-stage-v1' -or
        -not $stage.accepted -or
        $stage.compose.source_revision -cne $predecessorRevision -or
        $stage.compose.container_depends_on_label -cne '' -or
        -not $stage.compose.empty_label_accepted -or
        $stage.migrate.image_id -cne $predecessorImageId -or
        $stage.migrate.container_id -cne $migrate.container_id -or
        $stage.migrate.ddl_started -or
        $stage.decision -cne 'PROCEED_TO_FRESH_PRE_0047_BACKUP'
    ) {
        throw 'Stopped predecessor migrate staging lineage is not exact.'
    }
    $wrapperHold = $lineage['17-pre-migration-wrapper-false-negative.json']
    if (
        $wrapperHold.decision -cne 'HOLD_FAIL_CLOSED' -or
        $wrapperHold.migration_started -or
        $wrapperHold.pre_migration_attestation_created -or
        -not $wrapperHold.fail_closed -or
        $wrapperHold.live_enabled
    ) {
        throw 'The wrapper false-negative lineage was not fail closed.'
    }
    $compatibility = $lineage['18-pre-migration-compatibility-holds.json']
    if (
        $compatibility.decision -cne 'RESOLVED_FAIL_CLOSED_HOLDS' -or
        $compatibility.accepted_compatibility_normalizations.tracked_guard_modified -or
        $compatibility.accepted_compatibility_normalizations.patched_ast_errors -ne 0 -or
        $compatibility.independent_audits.git_blob_and_eol -cne 'PASS' -or
        $compatibility.independent_audits.patch_cardinality_and_ast -cne 'PASS' -or
        -not $compatibility.fail_closed -or
        $compatibility.live_enabled
    ) {
        throw 'The exact legacy compatibility normalization lineage is not accepted.'
    }
    $migrationAcceptance = $lineage['19-pre-migration-gate-and-0047-migration-accepted.json']
    if (
        $migrationAcceptance.format -cne 'stratus-section3-migration-acceptance-v1' -or
        $migrationAcceptance.decision -cne 'ACCEPT' -or
        $migrationAcceptance.candidate_revision -cne $predecessorRevision -or
        $migrationAcceptance.candidate_image_id -cne $predecessorImageId -or
        $migrationAcceptance.pre_migration_attestation.sha256 -cne $preMigrationAttestationSha256 -or
        $migrationAcceptance.pre_migration_attestation.payload_sha256 -cne $preMigrationPayloadSha256 -or
        $migrationAcceptance.migration.exit_code -ne 0 -or
        $migrationAcceptance.migration.restart_count -ne 0 -or
        $migrationAcceptance.migration.oom_killed -or
        $migrationAcceptance.postgres.container_id -cne $productionPostgresId -or
        -not $migrationAcceptance.postgres.identity_unchanged -or
        $migrationAcceptance.live_enabled -or
        $migrationAcceptance.binance_kill_switch_removed
    ) {
        throw 'The predecessor migration acceptance lineage is not exact.'
    }
    $postDdl = $lineage['20-strict-post-ddl-0047-accepted.json']
    if (
        $postDdl.format -cne 'stratus-section3-strict-post-ddl-v1' -or
        $postDdl.decision -cne 'ACCEPT' -or
        -not $postDdl.transaction.read_only -or
        $postDdl.schema.head_count -ne 1 -or
        $postDdl.schema.head -cne $targetSchemaHead -or
        -not $postDdl.indexes.all_exact -or
        -not $postDdl.boundaries.runtime -or
        -not $postDdl.boundaries.safety -or
        $postDdl.lease.total_rows -ne 0 -or
        $postDdl.lease.active_rows -ne 0 -or
        $postDdl.live_enabled -or
        $postDdl.binance_kill_switch_removed
    ) {
        throw 'The strict predecessor post-DDL lineage is not exact.'
    }
    $postDdlHba = $lineage['21-post-ddl-hba-accepted.json']
    if (
        $postDdlHba.format -cne 'stratus-section3-post-ddl-hba-v1' -or
        $postDdlHba.decision -cne 'ACCEPT' -or
        $postDdlHba.postgres_container_id -cne $productionPostgresId -or
        $postDdlHba.host_sha256 -cne $fixedHbaSha256 -or
        $postDdlHba.mounted_sha256 -cne $fixedHbaSha256 -or
        $postDdlHba.loaded_inventory_sha256 -cne $fixedHbaInventorySha256 -or
        $postDdlHba.loaded_line_count -ne 16 -or
        $postDdlHba.loaded_rule_count -ne 15 -or
        $postDdlHba.loaded_rule_errors -ne 0 -or
        -not $postDdlHba.reload_accepted -or
        $postDdlHba.live_enabled
    ) {
        throw 'The predecessor post-DDL HBA lineage is not exact.'
    }

    $snapshot = $attestation.payload.snapshot
    $restore = $attestation.payload.restore
    if ($ValidateBackupPayload) {
        $manifestPath = Join-Path $resolvedBackup 'manifest.txt'
        $archivePath = Join-Path $resolvedBackup 'stratus.dump'
        $catalogPath = Join-Path $resolvedBackup 'catalog.list'
        $restorePath = Join-Path $resolvedBackup 'restore-attestation.json'
        $archiveItem = Assert-RegularFile -LiteralPath $archivePath
        [void](Assert-RegularFile -LiteralPath $manifestPath)
        [void](Assert-RegularFile -LiteralPath $catalogPath)
        [void](Assert-RegularFile -LiteralPath $restorePath)
        $manifest = ConvertFrom-StrictManifest -LiteralPath $manifestPath
        if (
            $manifest.format -cne 'stratus-backup-manifest-v2' -or
            $manifest.schema_head -cne $sourceSchemaHead -or
            $manifest.schema_head_before -cne $sourceSchemaHead -or
            $manifest.schema_head_after -cne $sourceSchemaHead -or
            $manifest.catalog_verified -cne 'true' -or
            $manifest.schema_stable_during_dump -cne 'true' -or
            $manifest.full_restore_verified -cne 'false' -or
            [int64]$manifest.archive_bytes -ne $archiveItem.Length -or
            (Get-FileSha256 -LiteralPath $manifestPath) -cne $snapshot.manifest_sha256 -or
            (Get-FileSha256 -LiteralPath $archivePath) -cne $snapshot.archive_sha256 -or
            (Get-FileSha256 -LiteralPath $catalogPath) -cne $snapshot.catalog_sha256
        ) {
            throw 'The immutable pre-0047 backup payload drifted from its attestation.'
        }
        $restoreArtifact = Read-StrictJsonObjectFile -LiteralPath $restorePath `
            -Label 'historical restore attestation'
        if (
            (Get-FileSha256 -LiteralPath $restorePath) -cne $restoreAttestationSha256 -or
            $restoreArtifact.format -cne 'stratus-isolated-pg16-restore-v1' -or
            -not $restoreArtifact.accepted -or
            -not $restoreArtifact.isolated -or
            $restoreArtifact.postgres_image -cne $postgres16Image -or
            $restoreArtifact.postgres_major -ne 16 -or
            $restoreArtifact.source_manifest_sha256 -cne $snapshot.manifest_sha256 -or
            $restoreArtifact.source_archive_sha256 -cne $snapshot.archive_sha256 -or
            $restoreArtifact.source_catalog_sha256 -cne $snapshot.catalog_sha256 -or
            $restoreArtifact.source_schema_head -cne $sourceSchemaHead -or
            $restoreArtifact.restored_schema_head -cne $sourceSchemaHead -or
            -not $restoreArtifact.full_restore_verified -or
            $restoreArtifact.restore_database -cne $restore.restore_database -or
            $restoreArtifact.restore_started_at -cne $restore.restore_started_at -or
            $restoreArtifact.restore_completed_at -cne $restore.restore_completed_at
        ) {
            throw 'The isolated restore no longer binds the exact pre-0047 backup.'
        }
        if ($ValidateCatalogProbe) {
            if ($resolvedBackup -match '[,\r\n]') {
                throw 'BackupDirectory cannot be represented as a read-only Docker mount.'
            }
            $catalogProbe = @(
                Invoke-Docker -Arguments @(
                    'run', '--rm', '--network', 'none', '--read-only',
                    '--mount', "type=bind,source=$resolvedBackup,target=/evidence,readonly",
                    $postgres16Image, 'sh', '-ec',
                    'pg_restore --list /evidence/stratus.dump | sha256sum | cut -d" " -f1'
                ) | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
            )
            if ($catalogProbe.Count -ne 1 -or $catalogProbe[0].Trim() -cne $snapshot.catalog_sha256) {
                throw 'Pinned PostgreSQL 16 did not reproduce the historical archive catalog.'
            }
        }
    }

    return [pscustomobject]@{
        attestation = $attestation
        attestation_sha256 = $preMigrationAttestationSha256
        payload_sha256 = $preMigrationPayloadSha256
        lineage_sha256 = $lineageArtifacts
        predecessor_revision = $predecessorRevision
        predecessor_image = $predecessorImage
        predecessor_image_id = $predecessorImageId
        postgres = $historicalPostgres
        backup = [pscustomobject]@{
            manifest_sha256 = [string]$snapshot.manifest_sha256
            archive_sha256 = [string]$snapshot.archive_sha256
            archive_bytes = [int64]$snapshot.archive_bytes
            catalog_sha256 = [string]$snapshot.catalog_sha256
            catalog_entries = [int]$snapshot.catalog_entries
            restore_attestation_sha256 = $restoreAttestationSha256
        }
    }
}

function Get-OneNativeLine {
    param(
        [Parameter(Mandatory = $true)][string[]] $Lines,
        [Parameter(Mandatory = $true)][string] $Label
    )
    $meaningful = @($Lines | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
    if ($meaningful.Count -ne 1) {
        throw "$Label did not return exactly one line."
    }
    return ([string]$meaningful[0]).Trim()
}

function Assert-ImageIdentity {
    param(
        [Parameter(Mandatory = $true)][string] $Reference,
        [Parameter(Mandatory = $true)][string] $ImageId,
        [Parameter(Mandatory = $true)][string] $Revision,
        [Parameter(Mandatory = $true)][string] $Label
    )
    $inspect = Get-OneJsonObject -Label "$Label image inspect" -Lines (
        Invoke-Docker -Arguments @('image', 'inspect', $Reference, '--format', '{{json .}}')
    )
    Assert-NativeJsonObject -Value $inspect.Config -Label "$Label image Config"
    Assert-NativeJsonObject -Value $inspect.Config.Labels -Label "$Label image Labels"
    Assert-NativeJsonString -Value $inspect.Id -Label "$Label image Id"
    Assert-NativeJsonString -Value $inspect.Os -Label "$Label image Os"
    Assert-NativeJsonString -Value $inspect.Architecture -Label "$Label image Architecture"
    Assert-NativeJsonString -Value $inspect.Config.User -Label "$Label image User"
    Assert-NativeJsonString -Value $inspect.Config.WorkingDir -Label "$Label image WorkingDir"
    Assert-NativeJsonString -Value $inspect.Config.Labels.'org.opencontainers.image.revision' `
        -Label "$Label image revision"
    $entrypoint = Get-OptionalPropertyValue -InputObject $inspect.Config -Name 'Entrypoint'
    $environment = @(Get-OptionalPropertyValue -InputObject $inspect.Config -Name 'Env')
    $cmd = @(Get-OptionalPropertyValue -InputObject $inspect.Config -Name 'Cmd')
    if (
        $inspect.Id -cne $ImageId -or
        $inspect.Os -cne 'linux' -or
        $inspect.Architecture -cne 'amd64' -or
        $inspect.Config.User -cne 'stratus' -or
        $inspect.Config.WorkingDir -cne '/app' -or
        $inspect.Config.Labels.'org.opencontainers.image.revision' -cne $Revision -or
        $null -ne $entrypoint -or
        ($cmd -join "`n") -cne 'stratus-run' -or
        @($environment | Where-Object {
            [string]$_ -imatch '^(?:OPERATOR_API_KEY|SAFETY_API_KEY|DATABASE_URL|MIGRATION_DATABASE_URL|POSTGRES_PASSWORD)='
        }).Count -ne 0
    ) {
        throw "$Label candidate image identity/runtime boundary is not exact."
    }
    return $inspect
}

function Assert-GitAndWorktreeContract {
    $gitBefore = Get-PinnedApplicationBoundary -LiteralPath $script:gitExecutable `
        -ExpectedSha256 $script:gitExecutableSha256 -Label 'Git'
    $rootRevision = Get-OneNativeLine -Label 'production revision' -Lines (
        Invoke-Git -Repository $absoluteRoot -Arguments @('rev-parse', 'HEAD')
    )
    $buildRevision = Get-OneNativeLine -Label 'build revision' -Lines (
        Invoke-Git -Repository $absoluteBuildContext -Arguments @('rev-parse', 'HEAD')
    )
    if ($rootRevision -cne $ExpectedRevision -or $buildRevision -cne $ExpectedRevision) {
        throw 'Production and detached build worktrees must be the exact successor revision.'
    }
    $rootStatus = @(
        Invoke-Git -Repository $absoluteRoot -Arguments @(
            'status', '--porcelain=v1', '--untracked-files=all'
        ) | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    $buildStatus = @(
        Invoke-Git -Repository $absoluteBuildContext -Arguments @(
            'status', '--porcelain=v1', '--untracked-files=all'
        ) | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    if ($rootStatus.Count -ne 0 -or $buildStatus.Count -ne 0) {
        throw 'Production and detached build worktrees must both be clean.'
    }
    $priorErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $branchOutput = @(
            & $script:gitExecutable -C $absoluteBuildContext `
                symbolic-ref -q --short HEAD 2>$null
        )
        $branchExit = [int]$LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $priorErrorActionPreference
    }
    if ($branchExit -ne 1 -or @($branchOutput | Where-Object {
        -not [string]::IsNullOrWhiteSpace([string]$_)
    }).Count -ne 0) {
        throw 'BuildContext must be a detached worktree.'
    }
    $priorErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $mergeBaseOutput = @(
            & $script:gitExecutable -C $absoluteBuildContext `
                merge-base --is-ancestor $predecessorRevision `
                $ExpectedRevision 2>$null
        )
        $mergeBaseExit = [int]$LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $priorErrorActionPreference
    }
    if ($mergeBaseExit -ne 0) {
        throw 'The attested predecessor is not an ancestor of the successor revision.'
    }

    $manifestLines = [Collections.Generic.List[string]]::new()
    foreach ($entry in $closedGitContract.GetEnumerator()) {
        $path = [string]$entry.Key
        $expectedOid = [string]$entry.Value
        $oldOid = Get-OneNativeLine -Label "predecessor Git object $path" -Lines (
            Invoke-Git -Repository $absoluteBuildContext -Arguments @(
                'rev-parse', "$predecessorRevision`:$path"
            )
        )
        $newOid = Get-OneNativeLine -Label "successor Git object $path" -Lines (
            Invoke-Git -Repository $absoluteBuildContext -Arguments @(
                'rev-parse', "$ExpectedRevision`:$path"
            )
        )
        if ($oldOid -cne $expectedOid -or $newOid -cne $expectedOid) {
            throw "Closed migration Git contract drifted: $path"
        }
        $manifestLines.Add("$path`0$expectedOid")
    }
    $manifestArray = $manifestLines.ToArray()
    [Array]::Sort($manifestArray, [StringComparer]::Ordinal)
    $migrationFiles = @(
        Invoke-Git -Repository $absoluteBuildContext -Arguments @(
            'ls-tree', '-r', '--name-only', $ExpectedRevision, '--', 'migrations'
        ) | ForEach-Object { ([string]$_).Replace('\', '/') } |
            Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    if ($migrationFiles.Count -lt 47 -or @($migrationFiles | Where-Object {
        $_ -notmatch '^migrations/(?:[^/]+/)*[^/]+$'
    }).Count -ne 0) {
        throw 'Closed migration tree file inventory is malformed.'
    }
    $migrationFilesSorted = [string[]]$migrationFiles.Clone()
    [Array]::Sort($migrationFilesSorted, [StringComparer]::Ordinal)
    $gitAfter = Get-PinnedApplicationBoundary -LiteralPath $script:gitExecutable `
        -ExpectedSha256 $script:gitExecutableSha256 -Label 'Git'
    if (
        $gitAfter.path -cne $gitBefore.path -or
        $gitAfter.sha256 -cne $gitBefore.sha256 -or
        $gitAfter.bytes -ne $gitBefore.bytes
    ) {
        throw 'Git executable identity drifted during successor contract proof.'
    }
    return [pscustomobject]@{
        root_revision = $rootRevision
        build_revision = $buildRevision
        git_executable_path = $gitAfter.path
        git_executable_sha256 = $gitAfter.sha256
        git_executable_bytes = $gitAfter.bytes
        git_contract_sha256 = Get-Sha256Text -Value (($manifestArray -join "`n") + "`n")
        git_contract_entries = $manifestArray.Count
        migration_files = $migrationFilesSorted
    }
}

function Get-ImageContract {
    param(
        [Parameter(Mandatory = $true)][string] $ImageId,
        [Parameter(Mandatory = $true)][string[]] $MigrationFiles,
        [Parameter(Mandatory = $true)][string] $Label
    )
    $explicitPaths = @(
        'Dockerfile', 'README.md', 'docker-compose.yml', 'alembic.ini',
        'ops/postgres/stratus-pg_hba.conf', 'pyproject.toml', 'uv.lock',
        'src/stratus/__init__.py', 'src/stratus/config.py',
        'src/stratus/db/__init__.py', 'src/stratus/db/base.py',
        'src/stratus/db/models.py'
    )
    $tempRoot = Join-Path ([IO.Path]::GetTempPath()) (
        'stratus-successor-contract-' + [guid]::NewGuid().ToString('N')
    )
    [void](New-Item -ItemType Directory -Path $tempRoot)
    $proofContainer = ''
    $proofOwned = $false
    try {
        $proofName = 'stratus-successor-proof-' + [guid]::NewGuid().ToString('N')
        $proofContainer = Get-OneNativeLine -Label "$Label stopped proof container" -Lines (
            Invoke-Docker -Arguments @(
                'create', '--name', $proofName, '--network', 'none',
                '--entrypoint', '/bin/false', $ImageId
            )
        )
        if ($proofContainer -notmatch '^[0-9a-f]{64}$') {
            throw "$Label proof container did not have an exact ID."
        }
        if ($proofContainer -ceq $productionPostgresId) {
            throw "$Label proof container collided with a protected identity."
        }
        # A successful `docker create` returning a new exact ID transfers
        # cleanup ownership immediately. Inspect is validation, not ownership;
        # failures below must not leak the stopped proof container.
        $proofOwned = $true
        $proofInspect = Get-OneJsonObject -Label "$Label stopped proof inspect" -Lines (
            Invoke-Docker -Arguments @(
                'inspect', $proofContainer, '--format', '{{json .}}'
            )
        )
        if (
            $proofInspect.Id -cne $proofContainer -or
            $proofInspect.Name -cne "/$proofName" -or
            $proofInspect.Image -cne $ImageId -or
            $proofInspect.State.Status -cne 'created' -or
            $proofInspect.State.Running -or
            $proofInspect.State.Pid -ne 0 -or
            @($proofInspect.Mounts).Count -ne 0
        ) {
            throw "$Label proof container is not the exact newly-created stopped boundary."
        }
        [void](Invoke-Docker -Arguments @(
            'cp', "${proofContainer}:/app/migrations", $tempRoot
        ))
        foreach ($path in $explicitPaths) {
            $destination = Join-Path $tempRoot ($path.Replace('/', [IO.Path]::DirectorySeparatorChar))
            $parent = [IO.Path]::GetDirectoryName($destination)
            if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
                [void](New-Item -ItemType Directory -Path $parent -Force)
            }
            [void](Invoke-Docker -Arguments @(
                'cp', "${proofContainer}:/app/$path", $destination
            ))
        }
        $linked = @(Get-ChildItem -LiteralPath $tempRoot -Force -Recurse | Where-Object {
            $_.Attributes -band [IO.FileAttributes]::ReparsePoint
        })
        if ($linked.Count -ne 0) {
            throw "$Label image contract contains a link."
        }
        $actualMigrationSources = @(
            Get-ChildItem -LiteralPath (Join-Path $tempRoot 'migrations') -File -Recurse -Force |
                Where-Object { $_.Extension -cne '.pyc' -and $_.FullName -notmatch '[\\/]__pycache__[\\/]' } |
                ForEach-Object {
                    $relative = $_.FullName.Substring($tempRoot.Length).TrimStart('\', '/')
                    $relative.Replace('\', '/')
                }
        )
        $actualSorted = [string[]]$actualMigrationSources.Clone()
        [Array]::Sort($actualSorted, [StringComparer]::Ordinal)
        if (($actualSorted -join "`n") -cne ($MigrationFiles -join "`n")) {
            throw "$Label image migration file inventory drifted from Git."
        }
        $allPaths = @($explicitPaths + $MigrationFiles)
        $sortedPaths = [string[]]$allPaths.Clone()
        [Array]::Sort($sortedPaths, [StringComparer]::Ordinal)
        $manifest = [Text.StringBuilder]::new()
        foreach ($path in $sortedPaths) {
            $localPath = Join-Path $tempRoot ($path.Replace('/', [IO.Path]::DirectorySeparatorChar))
            $item = Assert-RegularFile -LiteralPath $localPath
            [void]$manifest.Append($path)
            [void]$manifest.Append("`0")
            [void]$manifest.Append([string]$item.Length)
            [void]$manifest.Append("`0")
            [void]$manifest.Append((Get-FileSha256 -LiteralPath $localPath))
            [void]$manifest.Append("`n")
        }
        return [pscustomobject]@{
            sha256 = Get-Sha256Text -Value $manifest.ToString()
            file_count = $sortedPaths.Count
        }
    }
    finally {
        if ($proofOwned) {
            try {
                $removed = Get-OneNativeLine -Label "$Label proof cleanup" -Lines (
                    Invoke-Docker -Arguments @('rm', $proofContainer)
                )
                if ($removed -cne $proofContainer) {
                    throw "$Label proof cleanup returned the wrong identity."
                }
            }
            catch {
                throw "$Label stopped proof container could not be removed."
            }
        }
        if (Test-Path -LiteralPath $tempRoot) {
            Remove-Item -LiteralPath $tempRoot -Recurse -Force
        }
    }
}

function Assert-ClosedSuccessorContract {
    $gitContract = Assert-GitAndWorktreeContract
    [void](Assert-ImageIdentity -Reference $predecessorImage -ImageId $predecessorImageId `
        -Revision $predecessorRevision -Label 'predecessor')
    [void](Assert-ImageIdentity -Reference $predecessorImageId -ImageId $predecessorImageId `
        -Revision $predecessorRevision -Label 'predecessor immutable ID')
    [void](Assert-ImageIdentity -Reference $CandidateImage -ImageId $ExpectedImageId `
        -Revision $ExpectedRevision -Label 'successor')
    [void](Assert-ImageIdentity -Reference $ExpectedImageId -ImageId $ExpectedImageId `
        -Revision $ExpectedRevision -Label 'successor immutable ID')
    if ($ExpectedRevision -ceq $predecessorRevision -or $ExpectedImageId -ceq $predecessorImageId) {
        throw 'A post-migration successor must have a distinct revision and immutable image ID.'
    }
    $oldImageContract = Get-ImageContract -ImageId $predecessorImageId `
        -MigrationFiles $gitContract.migration_files -Label 'predecessor'
    $newImageContract = Get-ImageContract -ImageId $ExpectedImageId `
        -MigrationFiles $gitContract.migration_files -Label 'successor'
    if (
        $oldImageContract.file_count -ne $newImageContract.file_count -or
        $oldImageContract.sha256 -cne $newImageContract.sha256
    ) {
        throw 'Successor image migration bytes drifted from the attested predecessor image.'
    }
    return [pscustomobject]@{
        git_executable_path = $gitContract.git_executable_path
        git_executable_sha256 = $gitContract.git_executable_sha256
        git_executable_bytes = $gitContract.git_executable_bytes
        git_contract_sha256 = $gitContract.git_contract_sha256
        git_contract_entries = $gitContract.git_contract_entries
        image_contract_sha256 = $oldImageContract.sha256
        image_contract_files = $oldImageContract.file_count
    }
}

function Assert-RenderedMigrateContract {
    $renderedText = (
        Invoke-Docker -Arguments (
            $composeArguments + @('--profile', 'release', 'config', '--format', 'json')
        )
    ) -join "`n"
    $rendered = ConvertFrom-StrictJsonObjectText -Json $renderedText `
        -Label 'rendered release Compose model'
    Assert-NativeJsonObject -Value $rendered.services -Label 'rendered Compose services'
    $migrate = $rendered.services.migrate
    Assert-NativeJsonObject -Value $migrate -Label 'rendered migrate service'
    Assert-StringArray -Value $migrate.command -Label 'rendered migrate command'
    Assert-NativeJsonObject -Value $migrate.depends_on -Label 'rendered migrate depends_on'
    Assert-NativeJsonObject -Value $migrate.depends_on.postgres `
        -Label 'rendered migrate postgres dependency'
    Assert-NativeJsonObject -Value $migrate.environment -Label 'rendered migrate environment'
    $entrypoint = Get-OptionalPropertyValue -InputObject $migrate -Name 'entrypoint'
    $volumes = Get-OptionalPropertyValue -InputObject $migrate -Name 'volumes'
    $user = Get-OptionalPropertyValue -InputObject $migrate -Name 'user'
    $environmentNames = @(
        $migrate.environment.PSObject.Properties |
            ForEach-Object { [string]$_.Name }
    )
    $migrationUrl = [string](Get-OptionalPropertyValue `
        -InputObject $migrate.environment -Name 'MIGRATION_DATABASE_URL')
    $releaseDigest = [string](Get-OptionalPropertyValue `
        -InputObject $migrate.environment -Name 'RELEASE_IMAGE_DIGEST')
    if (
        $migrate.image -cne $CandidateImage -or
        ($migrate.command -join "`n") -cne "alembic`nupgrade`nhead" -or
        $null -ne $entrypoint -or
        $null -ne $volumes -or
        $null -ne $user -or
        $migrate.restart -cne 'no' -or
        @($migrate.depends_on.PSObject.Properties).Count -ne 1 -or
        $migrate.depends_on.postgres.condition -cne 'service_healthy' -or
        -not $migrate.depends_on.postgres.required -or
        $environmentNames -contains 'OPERATOR_API_KEY' -or
        $environmentNames -contains 'SAFETY_API_KEY' -or
        $migrationUrl -notmatch '^postgresql\+asyncpg://stratus:[A-Za-z0-9._~-]{24,256}@postgres:5432/stratus$' -or
        $releaseDigest -cne $ExpectedImageId.Substring(7)
    ) {
        throw 'Rendered successor migrate service is not the exact no-mount one-shot contract.'
    }
    return [pscustomobject]@{
        command = @('alembic', 'upgrade', 'head')
        restart = 'no'
        dependency = 'postgres:service_healthy'
        image = $CandidateImage
        image_id = $ExpectedImageId
        credential_values_persisted = $false
    }
}

function Get-CanonicalPropertyHash {
    param(
        [AllowNull()][object] $Value,
        [Parameter(Mandatory = $true)][string] $Label
    )
    if ($null -eq $Value) {
        return Get-Sha256Text -Value "<null>`n"
    }
    Assert-NativeJsonObject -Value $Value -Label $Label
    $lines = @($Value.PSObject.Properties | ForEach-Object {
        $serialized = $_.Value | ConvertTo-Json -Depth 10 -Compress
        "$($_.Name)`0$serialized"
    })
    [Array]::Sort($lines, [StringComparer]::Ordinal)
    return Get-Sha256Text -Value (($lines -join "`n") + "`n")
}

function Get-DockerEngineBoundary {
    Assert-ToolEnvironmentBoundary
    $dockerCli = Get-PinnedApplicationBoundary `
        -LiteralPath $script:dockerExecutable `
        -ExpectedSha256 $script:dockerExecutableSha256 `
        -Label 'Docker'
    foreach ($overrideName in @(
        'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH',
        'DOCKER_API_VERSION'
    )) {
        if (Test-Path -LiteralPath "Env:$overrideName") {
            throw "Docker endpoint override is forbidden for the successor transition: $overrideName"
        }
    }
    $contextName = Get-OneNativeLine -Label 'Docker context name' -Lines (
        Invoke-Docker -Arguments @('context', 'show')
    )
    $endpoint = Get-OneJsonObject -Label 'Docker context endpoint' -Lines (
        Invoke-Docker -Arguments @(
            'context', 'inspect', $contextName,
            '--format', '{{json .Endpoints.docker}}'
        )
    )
    $info = Get-OneJsonObject -Label 'Docker engine info' -Lines (
        Invoke-Docker -Arguments @('info', '--format', '{{json .}}')
    )
    Assert-NativeJsonObject -Value $info.ClientInfo -Label 'Docker ClientInfo'
    if ($info.ClientInfo.Plugins -isnot [Array]) {
        throw 'Docker ClientInfo.Plugins must be a native JSON array.'
    }
    $composePlugins = @($info.ClientInfo.Plugins | Where-Object {
        [string]$_.Name -ceq 'compose'
    })
    if ($composePlugins.Count -ne 1) {
        throw 'Docker ClientInfo did not expose exactly one Compose plugin.'
    }
    $composePlugin = $composePlugins[0]
    Assert-NativeJsonObject -Value $composePlugin -Label 'Docker Compose plugin'
    foreach ($name in @('Name', 'Version', 'Path')) {
        Assert-NativeJsonString -Value $composePlugin.$name `
            -Label "Docker Compose plugin $name"
    }
    if ($composePlugin.Version -cne $approvedComposePluginVersion) {
        throw 'Docker Compose plugin version is not the approved production version.'
    }
    $composeCli = Get-PinnedApplicationBoundary `
        -LiteralPath ([IO.Path]::GetFullPath([string]$composePlugin.Path)) `
        -ExpectedSha256 $approvedComposePluginSha256 `
        -Label 'Docker Compose plugin'
    foreach ($name in @('Host')) {
        Assert-NativeJsonString -Value $endpoint.$name -Label "Docker endpoint $name"
    }
    Assert-NativeJsonBoolean -Value $endpoint.SkipTLSVerify `
        -Label 'Docker endpoint SkipTLSVerify'
    foreach ($name in @('ID', 'ServerVersion', 'Name', 'DockerRootDir')) {
        Assert-NativeJsonString -Value $info.$name -Label "Docker engine $name"
    }
    if (
        [string]::IsNullOrWhiteSpace($contextName) -or
        [string]::IsNullOrWhiteSpace([string]$endpoint.Host) -or
        [string]::IsNullOrWhiteSpace([string]$info.ID) -or
        [string]::IsNullOrWhiteSpace([string]$info.ServerVersion) -or
        [string]::IsNullOrWhiteSpace([string]$info.DockerRootDir) -or
        $contextName -cne $approvedDockerContext -or
        $endpoint.Host -cne $approvedDockerEndpoint -or
        $endpoint.SkipTLSVerify -or
        $info.ID -cne $approvedDockerEngineId -or
        $info.ServerVersion -cne $approvedDockerServerVersion -or
        $info.Name -cne $approvedDockerEngineName -or
        $info.DockerRootDir -cne $approvedDockerRootDirectory
    ) {
        throw 'Docker context or engine identity is incomplete.'
    }
    $skipTlsVerify = [bool](Get-OptionalPropertyValue `
        -InputObject $endpoint -Name 'SkipTLSVerify')
    $contract = @(
        [string]$dockerCli.path,
        [string]$dockerCli.sha256,
        [string]$dockerCli.bytes,
        [string]$composeCli.path,
        [string]$composeCli.sha256,
        [string]$composeCli.bytes,
        [string]$composePlugin.Version,
        $contextName,
        [string]$endpoint.Host,
        $skipTlsVerify.ToString().ToLowerInvariant(),
        [string]$info.ID,
        [string]$info.ServerVersion,
        [string]$info.Name,
        [string]$info.DockerRootDir
    ) -join "`0"
    return [pscustomobject]@{
        docker_executable_path = [string]$dockerCli.path
        docker_executable_sha256 = [string]$dockerCli.sha256
        docker_executable_bytes = [int64]$dockerCli.bytes
        compose_executable_path = [string]$composeCli.path
        compose_executable_sha256 = [string]$composeCli.sha256
        compose_executable_bytes = [int64]$composeCli.bytes
        compose_version = [string]$composePlugin.Version
        context = $contextName
        endpoint = [string]$endpoint.Host
        skip_tls_verify = $skipTlsVerify
        engine_id = [string]$info.ID
        server_version = [string]$info.ServerVersion
        engine_name = [string]$info.Name
        docker_root_dir = [string]$info.DockerRootDir
        contract_sha256 = Get-Sha256Text -Value ($contract + "`n")
    }
}

function Assert-RepairDockerEngineBoundary {
    param(
        [Parameter(Mandatory = $true)][object] $RepairEngine,
        [Parameter(Mandatory = $true)][object] $CurrentEngine,
        [Parameter(Mandatory = $true)][string] $Label
    )
    Assert-NativeJsonObject -Value $RepairEngine -Label $Label
    Assert-ExactProperties -Value $RepairEngine -Label $Label -Expected @(
        'docker_executable_path', 'docker_executable_sha256',
        'docker_executable_bytes', 'context', 'endpoint', 'skip_tls_verify',
        'engine_id', 'server_version', 'engine_name', 'docker_root_dir',
        'contract_sha256'
    )
    foreach ($name in @(
        'docker_executable_path', 'docker_executable_sha256', 'context', 'endpoint',
        'engine_id', 'server_version', 'engine_name', 'docker_root_dir',
        'contract_sha256'
    )) {
        Assert-NativeJsonString -Value $RepairEngine.$name -Label "$Label $name"
    }
    Assert-NativeJsonInteger -Value $RepairEngine.docker_executable_bytes `
        -Label "$Label docker_executable_bytes"
    Assert-NativeJsonBoolean -Value $RepairEngine.skip_tls_verify `
        -Label "$Label skip_tls_verify"
    $legacyContract = @(
        [string]$RepairEngine.docker_executable_path,
        [string]$RepairEngine.docker_executable_sha256,
        [string]$RepairEngine.docker_executable_bytes,
        [string]$RepairEngine.context,
        [string]$RepairEngine.endpoint,
        ([bool]$RepairEngine.skip_tls_verify).ToString().ToLowerInvariant(),
        [string]$RepairEngine.engine_id,
        [string]$RepairEngine.server_version,
        [string]$RepairEngine.engine_name,
        [string]$RepairEngine.docker_root_dir
    ) -join "`0"
    if (
        $RepairEngine.contract_sha256 -cne
            (Get-Sha256Text -Value ($legacyContract + "`n")) -or
        $RepairEngine.docker_executable_sha256 -cne $approvedDockerExecutableSha256 -or
        $RepairEngine.docker_executable_path -ine $CurrentEngine.docker_executable_path -or
        $RepairEngine.docker_executable_bytes -ne $CurrentEngine.docker_executable_bytes -or
        $RepairEngine.context -cne $CurrentEngine.context -or
        $RepairEngine.endpoint -cne $CurrentEngine.endpoint -or
        $RepairEngine.skip_tls_verify -cne $CurrentEngine.skip_tls_verify -or
        $RepairEngine.engine_id -cne $CurrentEngine.engine_id -or
        $RepairEngine.server_version -cne $CurrentEngine.server_version -or
        $RepairEngine.engine_name -cne $CurrentEngine.engine_name -or
        $RepairEngine.docker_root_dir -cne $CurrentEngine.docker_root_dir
    ) {
        throw "$Label does not bind the current approved Docker engine."
    }
}

function Assert-RepairNetworkBoundary {
    param(
        [Parameter(Mandatory = $true)][object] $Network,
        [Parameter(Mandatory = $true)][string] $NetworkName,
        [Parameter(Mandatory = $true)][string] $Label
    )
    Assert-NativeJsonObject -Value $Network -Label $Label
    Assert-ExactProperties -Value $Network -Label $Label -Expected @(
        'name', 'id', 'compose_name', 'created', 'scope', 'driver', 'subnet', 'gateway',
        'enable_ipv4', 'enable_ipv6', 'internal', 'attachable', 'ingress',
        'attached_container_ids', 'labels_sha256', 'options_sha256',
        'canonical_sha256'
    )
    $expected = $productionNetworks[$NetworkName]
    foreach ($name in @(
        'name', 'id', 'compose_name', 'created', 'scope', 'driver', 'subnet', 'gateway',
        'labels_sha256', 'options_sha256', 'canonical_sha256'
    )) {
        Assert-NativeJsonString -Value $Network.$name -Label "$Label $name"
    }
    if ($Network.attached_container_ids -isnot [Array]) {
        throw "$Label attached_container_ids must be a native JSON array."
    }
    $body = [ordered]@{
        name = [string]$Network.name
        id = [string]$Network.id
        compose_name = [string]$Network.compose_name
        created = [string]$Network.created
        scope = [string]$Network.scope
        driver = [string]$Network.driver
        subnet = [string]$Network.subnet
        gateway = [string]$Network.gateway
        enable_ipv4 = [bool]$Network.enable_ipv4
        enable_ipv6 = [bool]$Network.enable_ipv6
        internal = [bool]$Network.internal
        attachable = [bool]$Network.attachable
        ingress = [bool]$Network.ingress
        attached_container_ids = @($Network.attached_container_ids)
        labels_sha256 = [string]$Network.labels_sha256
        options_sha256 = [string]$Network.options_sha256
    }
    foreach ($name in @('enable_ipv4', 'enable_ipv6', 'internal', 'attachable', 'ingress')) {
        Assert-NativeJsonBoolean -Value $Network.$name -Label "$Label $name"
    }
    if (
        $Network.name -cne $NetworkName -or
        $Network.id -cne [string]$expected.id -or
        $Network.compose_name -cne [string]$expected.compose_name -or
        $Network.created -cne [string]$expected.created -or
        $Network.scope -cne 'local' -or $Network.driver -cne 'bridge' -or
        $Network.subnet -cne [string]$expected.subnet -or
        $Network.gateway -cne [string]$expected.gateway -or
        -not $Network.enable_ipv4 -or $Network.enable_ipv6 -or
        $Network.internal -or $Network.attachable -or $Network.ingress -or
        @($Network.attached_container_ids).Count -ne 0 -or
        $Network.labels_sha256 -cne [string]$expected.labels_sha256 -or
        $Network.options_sha256 -cne $productionNetworkOptionsSha256 -or
        $Network.canonical_sha256 -cne (Get-Sha256Text -Value (
            ($body | ConvertTo-Json -Depth 8 -Compress)
        ))
    ) {
        throw "$Label is not the exact stopped production network boundary."
    }
}

function Assert-RepairAttachmentBoundary {
    param(
        [Parameter(Mandatory = $true)][object] $Attachment,
        [Parameter(Mandatory = $true)][string] $NetworkName,
        [Parameter(Mandatory = $true)][string] $Label
    )
    Assert-NativeJsonObject -Value $Attachment -Label $Label
    Assert-ExactProperties -Value $Attachment -Label $Label -Expected @(
        'name', 'configuration_state', 'network_id', 'ipam_configuration',
        'driver_options', 'gateway_priority', 'aliases', 'dns_names',
        'endpoint_id', 'gateway', 'ip_address', 'ip_prefix_length',
        'ipv6_gateway', 'global_ipv6_address', 'global_ipv6_prefix_length',
        'mac_address', 'canonical_sha256'
    )
    Assert-NativeJsonInteger -Value $Attachment.gateway_priority `
        -Label "$Label gateway_priority"
    Assert-NativeJsonInteger -Value $Attachment.ip_prefix_length `
        -Label "$Label ip_prefix_length"
    Assert-NativeJsonInteger -Value $Attachment.global_ipv6_prefix_length `
        -Label "$Label global_ipv6_prefix_length"
    foreach ($name in @(
        'name', 'configuration_state', 'network_id', 'ipam_configuration',
        'driver_options', 'endpoint_id', 'gateway', 'ip_address',
        'ipv6_gateway', 'global_ipv6_address', 'mac_address', 'canonical_sha256'
    )) {
        Assert-NativeJsonString -Value $Attachment.$name -Label "$Label $name"
    }
    if ($Attachment.aliases -isnot [Array] -or $Attachment.dns_names -isnot [Array]) {
        throw "$Label aliases and dns_names must be native JSON arrays."
    }
    foreach ($value in @($Attachment.aliases) + @($Attachment.dns_names)) {
        Assert-NativeJsonString -Value $value -Label "$Label attachment name"
    }
    $aliases = @($Attachment.aliases)
    [Array]::Sort($aliases, [StringComparer]::Ordinal)
    $dnsNames = @($Attachment.dns_names)
    [Array]::Sort($dnsNames, [StringComparer]::Ordinal)
    $expectedAliases = @('postgres', 'weatherpoly-postgres-1')
    [Array]::Sort($expectedAliases, [StringComparer]::Ordinal)
    $expectedDns = @(
        $productionPostgresId.Substring(0, 12), 'postgres', 'weatherpoly-postgres-1'
    )
    [Array]::Sort($expectedDns, [StringComparer]::Ordinal)
    $body = [ordered]@{
        name = [string]$Attachment.name
        configuration_state = [string]$Attachment.configuration_state
        network_id = [string]$Attachment.network_id
        ipam_configuration = [string]$Attachment.ipam_configuration
        driver_options = [string]$Attachment.driver_options
        gateway_priority = [int]$Attachment.gateway_priority
        aliases = $aliases
        dns_names = $dnsNames
        endpoint_id = [string]$Attachment.endpoint_id
        gateway = [string]$Attachment.gateway
        ip_address = [string]$Attachment.ip_address
        ip_prefix_length = [int]$Attachment.ip_prefix_length
        ipv6_gateway = [string]$Attachment.ipv6_gateway
        global_ipv6_address = [string]$Attachment.global_ipv6_address
        global_ipv6_prefix_length = [int]$Attachment.global_ipv6_prefix_length
        mac_address = [string]$Attachment.mac_address
    }
    $configurationExact = if ($Attachment.configuration_state -ceq 'ExactStopped') {
        $Attachment.network_id -ceq [string]$productionNetworks[$NetworkName].id -and
        $Attachment.ipam_configuration -ceq 'null' -and
        $Attachment.driver_options -ceq 'null'
    }
    elseif ($Attachment.configuration_state -ceq 'ConfiguredPending') {
        $NetworkName -ceq 'weatherpoly_safety-plane' -and
        [string]::IsNullOrEmpty([string]$Attachment.network_id) -and
        $Attachment.ipam_configuration -ceq 'empty_object' -and
        $Attachment.driver_options -ceq 'empty_object'
    }
    else {
        $false
    }
    if (
        $Attachment.name -cne $NetworkName -or
        -not $configurationExact -or
        $Attachment.gateway_priority -ne 0 -or
        ($aliases -join "`n") -cne ($expectedAliases -join "`n") -or
        ($dnsNames -join "`n") -cne ($expectedDns -join "`n") -or
        -not [string]::IsNullOrEmpty([string]$Attachment.endpoint_id) -or
        -not [string]::IsNullOrEmpty([string]$Attachment.gateway) -or
        -not [string]::IsNullOrEmpty([string]$Attachment.ip_address) -or
        $Attachment.ip_prefix_length -ne 0 -or
        -not [string]::IsNullOrEmpty([string]$Attachment.ipv6_gateway) -or
        -not [string]::IsNullOrEmpty([string]$Attachment.global_ipv6_address) -or
        $Attachment.global_ipv6_prefix_length -ne 0 -or
        -not [string]::IsNullOrEmpty([string]$Attachment.mac_address) -or
        $Attachment.canonical_sha256 -cne (Get-Sha256Text -Value (
            ($body | ConvertTo-Json -Depth 8 -Compress)
        ))
    ) {
        throw "$Label is not an exact stopped PostgreSQL attachment."
    }
}

function Assert-RepairSnapshotBoundary {
    param(
        [Parameter(Mandatory = $true)][object] $Snapshot,
        [Parameter(Mandatory = $true)][object] $CurrentEngine,
        [Parameter(Mandatory = $true)][string] $Label,
        [switch] $RequirePostConnectAttachment
    )
    Assert-NativeJsonObject -Value $Snapshot -Label $Label
    Assert-ExactProperties -Value $Snapshot -Label $Label -Expected @(
        'canonical_sha256', 'docker_engine', 'running_container_ids',
        'lifecycle_inventory', 'lifecycle_inventory_sha256', 'postgres',
        'production_networks', 'production_networks_sha256'
    )
    Assert-RepairDockerEngineBoundary -RepairEngine $Snapshot.docker_engine `
        -CurrentEngine $CurrentEngine -Label "$Label docker_engine"
    foreach ($name in @(
        'canonical_sha256', 'lifecycle_inventory_sha256', 'production_networks_sha256'
    )) {
        Assert-NativeJsonString -Value $Snapshot.$name -Label "$Label $name"
        if ($Snapshot.$name -notmatch '^[0-9a-f]{64}$') {
            throw "$Label $name is not an exact SHA-256."
        }
    }
    if (
        $Snapshot.running_container_ids -isnot [Array] -or
        $Snapshot.lifecycle_inventory -isnot [Array] -or
        @($Snapshot.running_container_ids).Count -ne 0
    ) {
        throw "$Label contains a running container."
    }
    $lifecycleInventory = @($Snapshot.lifecycle_inventory)
    if ($lifecycleInventory.Count -lt 1) {
        throw "$Label lifecycle inventory is empty."
    }
    $lifecycleIds = [Collections.Generic.List[string]]::new()
    $lifecycleById = @{}
    foreach ($entry in $lifecycleInventory) {
        Assert-ExactProperties -Value $entry -Label "$Label lifecycle entry" `
            -Expected @(
                'container_id', 'name', 'state', 'running', 'started_at',
                'finished_at', 'restart_count', 'oom_killed', 'compose_project',
                'compose_service', 'compose_labels_sha256'
            )
        foreach ($name in @(
            'container_id', 'name', 'state', 'started_at', 'finished_at',
            'compose_project', 'compose_service', 'compose_labels_sha256'
        )) {
            Assert-NativeJsonString -Value $entry.$name `
                -Label "$Label lifecycle $name"
        }
        foreach ($name in @('running', 'oom_killed')) {
            Assert-NativeJsonBoolean -Value $entry.$name `
                -Label "$Label lifecycle $name"
        }
        Assert-NativeJsonInteger -Value $entry.restart_count `
            -Label "$Label lifecycle restart_count"
        if (
            $entry.container_id -notmatch '^[0-9a-f]{64}$' -or
            $entry.running -or
            $entry.compose_labels_sha256 -notmatch '^[0-9a-f]{64}$'
        ) {
            throw "$Label lifecycle entry is malformed or running."
        }
        [void](ConvertTo-RequiredUtcTime -Value $entry.started_at `
            -Label "$Label lifecycle started_at")
        [void](ConvertTo-RequiredUtcTime -Value $entry.finished_at `
            -Label "$Label lifecycle finished_at")
        $composeLines = @(
            "com.docker.compose.project`0$($entry.compose_project)",
            "com.docker.compose.service`0$($entry.compose_service)"
        )
        [Array]::Sort($composeLines, [StringComparer]::Ordinal)
        if ($entry.compose_labels_sha256 -cne (Get-Sha256Text -Value (
            ($composeLines -join "`n") + "`n"
        ))) {
            throw "$Label lifecycle compose-label digest is invalid."
        }
        $id = [string]$entry.container_id
        if ($lifecycleById.ContainsKey($id)) {
            throw "$Label lifecycle inventory contains a duplicate container ID."
        }
        $lifecycleById[$id] = $entry
        $lifecycleIds.Add($id)
    }
    $sortedLifecycleIds = [string[]]@($lifecycleIds)
    [Array]::Sort($sortedLifecycleIds, [StringComparer]::Ordinal)
    if (($lifecycleIds -join "`n") -cne ($sortedLifecycleIds -join "`n")) {
        throw "$Label lifecycle inventory is not sorted by exact container ID."
    }
    $lifecycleJson = ConvertTo-Json -InputObject $lifecycleInventory `
        -Depth 8 -Compress
    if ($Snapshot.lifecycle_inventory_sha256 -cne (Get-Sha256Text -Value (
        $lifecycleJson
    ))) {
        throw "$Label lifecycle inventory digest is invalid."
    }
    if (-not $lifecycleById.ContainsKey($productionPostgresId)) {
        throw "$Label lifecycle inventory lacks the retained PostgreSQL ID."
    }
    Assert-NativeJsonObject -Value $Snapshot.postgres -Label "$Label postgres"
    Assert-ExactProperties -Value $Snapshot.postgres -Label "$Label postgres" -Expected @(
        'stable_identity', 'stable_identity_sha256', 'attachment_state',
        'attachments', 'attachments_sha256'
    )
    foreach ($name in @(
        'stable_identity_sha256', 'attachment_state', 'attachments_sha256'
    )) {
        Assert-NativeJsonString -Value $Snapshot.postgres.$name `
            -Label "$Label postgres $name"
    }
    foreach ($name in @('stable_identity_sha256', 'attachments_sha256')) {
        if ($Snapshot.postgres.$name -notmatch '^[0-9a-f]{64}$') {
            throw "$Label postgres $name is not an exact SHA-256."
        }
    }
    $stable = $Snapshot.postgres.stable_identity
    Assert-NativeJsonObject -Value $stable -Label "$Label stable PostgreSQL identity"
    Assert-ExactProperties -Value $stable -Label "$Label stable PostgreSQL identity" -Expected @(
        'container_id', 'name', 'image_id', 'config_image', 'command_sha256',
        'compose_config_hash', 'state', 'running', 'started_at', 'finished_at',
        'restart_count', 'oom_killed', 'exit_code', 'network_mode', 'hba_sha256',
        'hba_source', 'hba_mode', 'hba_propagation', 'data_volume',
        'data_volume_driver', 'data_volume_source', 'data_volume_mode',
        'data_volume_propagation', 'published_postgres', 'labels_sha256'
    )
    foreach ($name in @('running', 'oom_killed')) {
        Assert-NativeJsonBoolean -Value $stable.$name -Label "$Label stable $name"
    }
    foreach ($name in @(
        'container_id', 'name', 'image_id', 'config_image', 'command_sha256',
        'compose_config_hash', 'state', 'started_at', 'finished_at',
        'network_mode', 'hba_sha256', 'hba_source', 'hba_mode',
        'hba_propagation', 'data_volume', 'data_volume_driver',
        'data_volume_source', 'data_volume_mode', 'data_volume_propagation',
        'published_postgres', 'labels_sha256'
    )) {
        Assert-NativeJsonString -Value $stable.$name -Label "$Label stable $name"
    }
    $allowedHbaSources = @(Get-DockerHostPathAliases -Path (
        Join-Path $absoluteRoot 'ops\postgres\stratus-pg_hba.conf'
    ))
    foreach ($name in @('restart_count', 'exit_code')) {
        Assert-NativeJsonInteger -Value $stable.$name -Label "$Label stable $name"
    }
    if (
        $stable.container_id -cne $productionPostgresId -or
        $stable.name -cne '/weatherpoly-postgres-1' -or
        $stable.image_id -cne $postgres16ImageId -or
        $stable.config_image -cne $postgres16Image -or
        $stable.command_sha256 -cne $productionPostgresCommandSha256 -or
        $stable.compose_config_hash -cne $productionPostgresConfigHash -or
        $stable.state -cne 'exited' -or $stable.running -or
        $stable.restart_count -ne 0 -or $stable.oom_killed -or
        $stable.exit_code -ne 0 -or $stable.network_mode -cne 'weatherpoly_default' -or
        $stable.hba_sha256 -cne $fixedHbaSha256 -or
        ([string]$stable.hba_source).Replace('\', '/') -notin $allowedHbaSources -or
        $stable.hba_mode -cne 'ro' -or
        $stable.hba_propagation -cne 'rprivate' -or
        $stable.data_volume -cne 'weatherpoly_stratus-postgres' -or
        $stable.data_volume_driver -cne 'local' -or
        $stable.data_volume_source -cne $productionDataVolumeSource -or
        $stable.data_volume_mode -cne 'rw' -or
        $stable.data_volume_propagation -cne '' -or
        $stable.published_postgres -cne '127.0.0.1:5432' -or
        $Snapshot.postgres.stable_identity_sha256 -cne (Get-Sha256Text -Value (
            ($stable | ConvertTo-Json -Depth 10 -Compress)
        ))
    ) {
        throw "$Label retained PostgreSQL stable identity is not exact."
    }
    $retainedLifecycle = $lifecycleById[$productionPostgresId]
    if (
        $retainedLifecycle.name -cne $stable.name -or
        $retainedLifecycle.state -cne $stable.state -or
        $retainedLifecycle.running -ne $stable.running -or
        $retainedLifecycle.started_at -cne $stable.started_at -or
        $retainedLifecycle.finished_at -cne $stable.finished_at -or
        $retainedLifecycle.restart_count -ne $stable.restart_count -or
        $retainedLifecycle.oom_killed -ne $stable.oom_killed -or
        $retainedLifecycle.compose_project -cne 'weatherpoly' -or
        $retainedLifecycle.compose_service -cne 'postgres'
    ) {
        throw "$Label retained PostgreSQL lifecycle entry is not cross-bound."
    }
    [void](ConvertTo-RequiredUtcTime -Value ([string]$stable.started_at) `
        -Label "$Label PostgreSQL started_at")
    [void](ConvertTo-RequiredUtcTime -Value ([string]$stable.finished_at) `
        -Label "$Label PostgreSQL finished_at")
    Assert-NativeJsonObject -Value $Snapshot.postgres.attachments `
        -Label "$Label PostgreSQL attachments"
    $attachmentProperties = @($Snapshot.postgres.attachments.PSObject.Properties)
    $attachmentNames = @($attachmentProperties.Name)
    [Array]::Sort($attachmentNames, [StringComparer]::Ordinal)
    $expectedAttachmentNames = if ($RequirePostConnectAttachment) {
        @($productionNetworks.Keys)
    }
    elseif ($Snapshot.postgres.attachment_state -ceq 'default_only') {
        @('weatherpoly_default')
    }
    elseif ($Snapshot.postgres.attachment_state -ceq 'exact') {
        @($productionNetworks.Keys)
    }
    elseif ($Snapshot.postgres.attachment_state -ceq 'configured_pending') {
        @($productionNetworks.Keys)
    }
    else {
        throw "$Label has an invalid stopped attachment disposition."
    }
    [Array]::Sort($expectedAttachmentNames, [StringComparer]::Ordinal)
    if (
        ($RequirePostConnectAttachment -and
            $Snapshot.postgres.attachment_state -cnotin @('configured_pending', 'exact')) -or
        ($attachmentNames -join "`n") -cne ($expectedAttachmentNames -join "`n")
    ) {
        throw "$Label does not contain the required stopped attachment set."
    }
    foreach ($name in $expectedAttachmentNames) {
        $expectedConfiguration = if (
            $Snapshot.postgres.attachment_state -ceq 'configured_pending' -and
            $name -ceq 'weatherpoly_safety-plane'
        ) {
            'ConfiguredPending'
        }
        else {
            'ExactStopped'
        }
        if (
            $Snapshot.postgres.attachments.$name.configuration_state -cne
                $expectedConfiguration
        ) {
            throw "$Label attachment configuration state is inconsistent: $name"
        }
        Assert-RepairAttachmentBoundary `
            -Attachment $Snapshot.postgres.attachments.$name `
            -NetworkName $name -Label "$Label attachment $name"
    }
    if ($Snapshot.postgres.attachments_sha256 -cne (Get-Sha256Text -Value (
        ($Snapshot.postgres.attachments | ConvertTo-Json -Depth 12 -Compress)
    ))) {
        throw "$Label attachment-set digest is invalid."
    }
    Assert-NativeJsonObject -Value $Snapshot.production_networks `
        -Label "$Label production_networks"
    Assert-ExactProperties -Value $Snapshot.production_networks `
        -Label "$Label production_networks" -Expected @($productionNetworks.Keys)
    foreach ($name in @($productionNetworks.Keys)) {
        Assert-RepairNetworkBoundary -Network $Snapshot.production_networks.$name `
            -NetworkName $name -Label "$Label network $name"
    }
    if ($Snapshot.production_networks_sha256 -cne (Get-Sha256Text -Value (
        ($Snapshot.production_networks | ConvertTo-Json -Depth 12 -Compress)
    ))) {
        throw "$Label production-network digest is invalid."
    }
    $body = [ordered]@{
        docker_engine = $Snapshot.docker_engine
        running_container_ids = @($Snapshot.running_container_ids)
        lifecycle_inventory = @($Snapshot.lifecycle_inventory)
        lifecycle_inventory_sha256 = [string]$Snapshot.lifecycle_inventory_sha256
        postgres = $Snapshot.postgres
        production_networks = $Snapshot.production_networks
        production_networks_sha256 = [string]$Snapshot.production_networks_sha256
    }
    if ($Snapshot.canonical_sha256 -cne (Get-Sha256Text -Value (
        ($body | ConvertTo-Json -Depth 20 -Compress)
    ))) {
        throw "$Label canonical digest is invalid."
    }
}

function Assert-RepairDockerEventWitnessBoundary {
    param(
        [Parameter(Mandatory = $true)][object] $Witness,
        [Parameter(Mandatory = $true)][bool] $MutationIssued,
        [Parameter(Mandatory = $true)][DateTimeOffset] $AcceptedAt
    )

    Assert-NativeJsonObject -Value $Witness -Label 'network repair event witness'
    Assert-ExactProperties -Value $Witness -Label 'network repair event witness' `
        -Expected @(
            'format', 'session_id', 'process_id', 'process_started_at',
            'event_command', 'collector', 'barrier_volume_name', 'pre_barrier',
            'final_barrier', 'authority_as_of_time_nano', 'journal_bytes',
            'journal_sha256', 'event_count', 'events_sha256', 'events',
            'network_connect_count', 'network_connect_time_nano', 'stderr_bytes',
            'truncated', 'eof_before_seal', 'unexpected_event_count'
        )
    foreach ($name in @(
        'format', 'session_id', 'process_started_at', 'barrier_volume_name',
        'journal_sha256', 'events_sha256'
    )) {
        Assert-NativeJsonString -Value $Witness.$name `
            -Label "network repair event witness $name"
    }
    foreach ($name in @(
        'process_id', 'authority_as_of_time_nano', 'journal_bytes', 'event_count',
        'network_connect_count', 'stderr_bytes', 'unexpected_event_count'
    )) {
        Assert-NativeJsonInteger -Value $Witness.$name `
            -Label "network repair event witness $name"
    }
    foreach ($name in @('truncated', 'eof_before_seal')) {
        Assert-NativeJsonBoolean -Value $Witness.$name `
            -Label "network repair event witness $name"
    }
    if (
        $Witness.format -cne 'stratus-stopped-repair-docker-event-witness-v1' -or
        $Witness.session_id -cnotmatch '^[0-9a-f]{32}$' -or
        [int64]$Witness.process_id -le 0 -or
        [int64]$Witness.process_id -gt [int]::MaxValue -or
        $Witness.barrier_volume_name -cnotmatch '^[0-9a-f]{64}$' -or
        $Witness.journal_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        $Witness.events_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        [int64]$Witness.journal_bytes -le 0 -or
        [int64]$Witness.journal_bytes -gt 65536 -or
        [int64]$Witness.stderr_bytes -ne 0 -or
        $Witness.truncated -or $Witness.eof_before_seal -or
        [int64]$Witness.unexpected_event_count -ne 0
    ) {
        throw 'Network repair Docker event witness identity or sealed state is invalid.'
    }
    $processStartedAt = ConvertTo-RequiredUtcTime `
        -Value ([string]$Witness.process_started_at) `
        -Label 'network repair event witness process_started_at'
    if (
        $processStartedAt.ToString('o') -cne $Witness.process_started_at -or
        $processStartedAt -gt $AcceptedAt.AddSeconds($clockSkewSeconds)
    ) {
        throw 'Network repair Docker event witness process time is invalid.'
    }

    if ($Witness.event_command -isnot [Array]) {
        throw 'Network repair Docker event command must be a native JSON array.'
    }
    foreach ($token in @($Witness.event_command)) {
        Assert-NativeJsonString -Value $token `
            -Label 'network repair Docker event command token'
    }
    $eventFormat = (
        '{"type":{{json .Type}},"action":{{json .Action}},' +
        '"actor_id":{{json .Actor.ID}},"attributes":{{json .Actor.Attributes}},' +
        '"scope":{{json .Scope}},"time":{{.Time}},' +
        '"time_nano":{{.TimeNano}}}'
    )
    $expectedEventCommand = @(
        '--context', $approvedDockerContext, 'events', '--format', $eventFormat
    )
    if (
        (@($Witness.event_command).Count -ne $expectedEventCommand.Count) -or
        (@($Witness.event_command) -join "`n") -cne
            ($expectedEventCommand -join "`n")
    ) {
        throw 'Network repair Docker event command is not exact.'
    }

    Assert-NativeJsonObject -Value $Witness.collector `
        -Label 'network repair Docker event collector'
    Assert-ExactProperties -Value $Witness.collector `
        -Label 'network repair Docker event collector' -Expected @(
            'type_name', 'contract_identity', 'template_sha256', 'source_sha256',
            'assembly_full_name_sha256', 'assembly_is_dynamic', 'module_version_id'
        )
    foreach ($name in @(
        'type_name', 'contract_identity', 'template_sha256', 'source_sha256',
        'assembly_full_name_sha256', 'module_version_id'
    )) {
        Assert-NativeJsonString -Value $Witness.collector.$name `
            -Label "network repair Docker event collector $name"
    }
    Assert-NativeJsonBoolean -Value $Witness.collector.assembly_is_dynamic `
        -Label 'network repair Docker event collector assembly_is_dynamic'
    $moduleVersionId = [Guid]::Empty
    if (
        $Witness.collector.template_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        $Witness.collector.source_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        $Witness.collector.assembly_full_name_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        $Witness.collector.template_sha256 -cne
            $approvedRepairDockerCollectorTemplateSha256 -or
        $Witness.collector.source_sha256 -cne
            $approvedRepairDockerCollectorSourceSha256 -or
        $Witness.collector.type_name -cne (
            'StratusRepairDockerCollector_' +
            $Witness.collector.template_sha256.Substring(0, 16)
        ) -or
        $Witness.collector.contract_identity -cne
            ('repair-v2:' + $Witness.collector.template_sha256) -or
        $Witness.collector.assembly_is_dynamic -or
        -not [Guid]::TryParse(
            [string]$Witness.collector.module_version_id,
            [ref]$moduleVersionId
        ) -or
        $moduleVersionId -eq [Guid]::Empty -or
        $moduleVersionId.ToString('D') -cne $Witness.collector.module_version_id
    ) {
        throw 'Network repair Docker event collector provenance is invalid.'
    }

    foreach ($name in @('pre_barrier', 'final_barrier')) {
        Assert-NativeJsonObject -Value $Witness.$name `
            -Label "network repair event witness $name"
        Assert-ExactProperties -Value $Witness.$name `
            -Label "network repair event witness $name" `
            -Expected @('actor_id', 'time', 'time_nano')
        Assert-NativeJsonString -Value $Witness.$name.actor_id `
            -Label "network repair event witness $name actor_id"
        foreach ($timeName in @('time', 'time_nano')) {
            Assert-NativeJsonInteger -Value $Witness.$name.$timeName `
                -Label "network repair event witness $name $timeName"
        }
    }

    if ($Witness.events -isnot [Array]) {
        throw 'Network repair Docker event records must be a native JSON array.'
    }
    $events = @($Witness.events)
    $expectedCount = if ($MutationIssued) { 3 } else { 2 }
    $expectedConnectCount = if ($MutationIssued) { 1 } else { 0 }
    if (
        $events.Count -ne $expectedCount -or
        [int64]$Witness.event_count -ne $expectedCount -or
        [int64]$Witness.network_connect_count -ne $expectedConnectCount
    ) {
        throw 'Network repair Docker event witness has an invalid event count.'
    }
    if ($MutationIssued) {
        Assert-NativeJsonInteger -Value $Witness.network_connect_time_nano `
            -Label 'network repair event witness network_connect_time_nano'
    }
    elseif ($null -ne $Witness.network_connect_time_nano) {
        throw 'Network repair event witness has a connect time without a mutation.'
    }

    $expectedEvents = [Collections.Generic.List[object]]::new()
    $expectedEvents.Add([pscustomobject]@{
        type = 'volume'
        action = 'create'
        actor_id = [string]$Witness.barrier_volume_name
        attributes = [ordered]@{ driver = 'local' }
    })
    if ($MutationIssued) {
        $expectedEvents.Add([pscustomobject]@{
            type = 'network'
            action = 'connect'
            actor_id = [string]$productionNetworks['weatherpoly_safety-plane'].id
            attributes = [ordered]@{
                container = $productionPostgresId
                name = 'weatherpoly_safety-plane'
                type = 'bridge'
            }
        })
    }
    $expectedEvents.Add([pscustomobject]@{
        type = 'volume'
        action = 'destroy'
        actor_id = [string]$Witness.barrier_volume_name
        attributes = [ordered]@{ driver = 'local' }
    })

    $previousTimeNano = [int64]0
    $processStartedSeconds = $processStartedAt.ToUnixTimeSeconds()
    $acceptedSeconds = $AcceptedAt.ToUnixTimeSeconds()
    for ($index = 0; $index -lt $events.Count; $index += 1) {
        $event = $events[$index]
        $expected = $expectedEvents[$index]
        Assert-NativeJsonObject -Value $event `
            -Label "network repair Docker event record $index"
        Assert-ExactProperties -Value $event `
            -Label "network repair Docker event record $index" -Expected @(
                'type', 'action', 'actor_id', 'attributes', 'scope', 'time',
                'time_nano'
            )
        foreach ($name in @('type', 'action', 'actor_id', 'scope')) {
            Assert-NativeJsonString -Value $event.$name `
                -Label "network repair Docker event record $index $name"
        }
        Assert-NativeJsonObject -Value $event.attributes `
            -Label "network repair Docker event record $index attributes"
        Assert-ExactProperties -Value $event.attributes `
            -Label "network repair Docker event record $index attributes" `
            -Expected @($expected.attributes.Keys)
        foreach ($property in @($event.attributes.PSObject.Properties)) {
            Assert-NativeJsonString -Value $property.Value `
                -Label "network repair Docker event record $index attribute $($property.Name)"
        }
        foreach ($name in @('time', 'time_nano')) {
            Assert-NativeJsonInteger -Value $event.$name `
                -Label "network repair Docker event record $index $name"
        }
        $eventSeconds = [decimal]$event.time_nano / [decimal]1000000000
        if (
            $event.type -cne $expected.type -or
            $event.action -cne $expected.action -or
            $event.actor_id -cne $expected.actor_id -or
            $event.scope -cne 'local' -or
            [int64]$event.time -le 0 -or
            [int64]$event.time_nano -le $previousTimeNano -or
            [int64][Math]::Floor($eventSeconds) -ne [int64]$event.time -or
            [int64]$event.time -lt ($processStartedSeconds - $clockSkewSeconds) -or
            [int64]$event.time -gt ($acceptedSeconds + $clockSkewSeconds)
        ) {
            throw "Network repair Docker event record $index is not exact or timely."
        }
        foreach ($name in @($expected.attributes.Keys)) {
            if ($event.attributes.$name -cne [string]$expected.attributes[$name]) {
                throw "Network repair Docker event record $index has an invalid attribute."
            }
        }
        $previousTimeNano = [int64]$event.time_nano
    }

    $lastIndex = $events.Count - 1
    if (
        $Witness.pre_barrier.actor_id -cne $Witness.barrier_volume_name -or
        $Witness.final_barrier.actor_id -cne $Witness.barrier_volume_name -or
        [int64]$Witness.pre_barrier.time -ne [int64]$events[0].time -or
        [int64]$Witness.pre_barrier.time_nano -ne [int64]$events[0].time_nano -or
        [int64]$Witness.final_barrier.time -ne [int64]$events[$lastIndex].time -or
        [int64]$Witness.final_barrier.time_nano -ne
            [int64]$events[$lastIndex].time_nano -or
        [int64]$Witness.authority_as_of_time_nano -ne
            [int64]$Witness.final_barrier.time_nano -or
        ($MutationIssued -and [int64]$Witness.network_connect_time_nano -ne
            [int64]$events[1].time_nano)
    ) {
        throw 'Network repair Docker event barriers are not bound to their records.'
    }
    $acceptedUnixTicks = [int64](
        $AcceptedAt.UtcDateTime.Ticks - [int64]621355968000000000
    )
    $acceptedTimeNano = [int64]($acceptedUnixTicks * [int64]100)
    $finalTimeNano = [int64]$events[$lastIndex].time_nano
    $maximumSealLagNano = [int64](
        [int64]$networkRepairEventSealMaxLagSeconds * [int64]1000000000
    )
    $maximumFutureSkewNano = [int64](
        [int64]$clockSkewSeconds * [int64]1000000000
    )
    if (($acceptedTimeNano - $finalTimeNano) -gt $maximumSealLagNano) {
        throw 'Network repair Docker final event barrier is too old for its acceptance.'
    }
    if (($finalTimeNano - $acceptedTimeNano) -gt $maximumFutureSkewNano) {
        throw 'Network repair Docker final event barrier is in the future.'
    }

    $eventLines = @($events | ForEach-Object {
        ConvertTo-Json -InputObject $_ -Depth 8 -Compress
    })
    $journalText = ($eventLines -join "`n") + "`n"
    $eventsJson = ConvertTo-Json -InputObject $events -Depth 8 -Compress
    if (
        [int64]$Witness.journal_bytes -ne
            [Text.UTF8Encoding]::new($false).GetByteCount($journalText) -or
        $Witness.journal_sha256 -cne (Get-Sha256Text -Value $journalText) -or
        $Witness.events_sha256 -cne (Get-Sha256Text -Value $eventsJson)
    ) {
        throw 'Network repair Docker event witness hashes are not reproducible.'
    }
}

function Assert-NetworkRepairAttestation {
    param(
        [Parameter(Mandatory = $true)][string] $LiteralPath,
        [Parameter(Mandatory = $true)][object] $CurrentEngine
    )
    $document = Read-BoundedStrictJsonDocument -LiteralPath $LiteralPath `
        -Label 'stopped PostgreSQL network repair attestation' `
        -MaximumBytes 8388608
    $fileSha256 = [string]$document.sha256
    $envelope = $document.value
    Assert-ExactProperties -Value $envelope `
        -Label 'network repair attestation root' `
        -Expected @('format', 'accepted', 'payload', 'payload_sha256')
    Assert-NativeJsonString -Value $envelope.format -Label 'network repair format'
    Assert-NativeJsonBoolean -Value $envelope.accepted -Label 'network repair accepted'
    Assert-NativeJsonObject -Value $envelope.payload -Label 'network repair payload'
    Assert-NativeJsonString -Value $envelope.payload_sha256 `
        -Label 'network repair payload_sha256'
    $payload = $envelope.payload
    Assert-ExactProperties -Value $payload -Label 'network repair payload' -Expected @(
        'accepted_at', 'disposition', 'fixed_identity', 'docker_engine',
        'event_witness', 'snapshots', 'comparisons', 'mutation', 'authorization'
    )
    Assert-NativeJsonString -Value $payload.accepted_at `
        -Label 'network repair accepted_at'
    Assert-NativeJsonString -Value $payload.disposition `
        -Label 'network repair disposition'
    if (
        $envelope.format -cne 'stratus-stopped-postgres-safety-plane-repair-v2' -or
        -not $envelope.accepted -or
        $envelope.payload_sha256 -notmatch '^[0-9a-f]{64}$' -or
        $envelope.payload_sha256 -cne (Get-Sha256Text -Value (
            ($payload | ConvertTo-Json -Depth 30 -Compress)
        ))
    ) {
        throw 'Stopped PostgreSQL network repair envelope is not authentic.'
    }
    $acceptedAt = ConvertTo-RequiredUtcTime -Value ([string]$payload.accepted_at) `
        -Label 'network repair accepted_at'
    $now = [DateTimeOffset]::UtcNow
    $lastWrite = ConvertTo-RequiredUtcTime `
        -Value ([string]$document.last_write_time_utc) `
        -Label 'network repair file last_write_time_utc'
    if (
        $acceptedAt -gt $now.AddSeconds($clockSkewSeconds) -or
        $acceptedAt -lt $now.AddSeconds(-$networkRepairMaxAgeSeconds) -or
        $lastWrite -lt $acceptedAt.AddSeconds(-$clockSkewSeconds) -or
        $lastWrite -gt $now.AddSeconds($clockSkewSeconds)
    ) {
        throw 'Stopped PostgreSQL network repair evidence is stale or has invalid file time.'
    }
    Assert-NativeJsonObject -Value $payload.fixed_identity -Label 'network repair fixed_identity'
    Assert-ExactProperties -Value $payload.fixed_identity -Label 'network repair fixed_identity' `
        -Expected @(
            'postgres_container_id', 'postgres_container_name',
            'default_network_id', 'safety_network_id'
        )
    foreach ($name in @(
        'postgres_container_id', 'postgres_container_name',
        'default_network_id', 'safety_network_id'
    )) {
        Assert-NativeJsonString -Value $payload.fixed_identity.$name `
            -Label "network repair fixed_identity $name"
    }
    if (
        $payload.fixed_identity.postgres_container_id -cne $productionPostgresId -or
        $payload.fixed_identity.postgres_container_name -cne 'weatherpoly-postgres-1' -or
        $payload.fixed_identity.default_network_id -cne
            [string]$productionNetworks['weatherpoly_default'].id -or
        $payload.fixed_identity.safety_network_id -cne
            [string]$productionNetworks['weatherpoly_safety-plane'].id
    ) {
        throw 'Network repair fixed production identity is not exact.'
    }
    Assert-RepairDockerEngineBoundary -RepairEngine $payload.docker_engine `
        -CurrentEngine $CurrentEngine -Label 'network repair Docker engine'
    Assert-NativeJsonObject -Value $payload.snapshots -Label 'network repair snapshots'
    Assert-ExactProperties -Value $payload.snapshots -Label 'network repair snapshots' `
        -Expected @('pre', 'armed', 'post_first', 'post_second')
    Assert-RepairSnapshotBoundary -Snapshot $payload.snapshots.pre `
        -CurrentEngine $CurrentEngine -Label 'network repair pre snapshot'
    Assert-RepairSnapshotBoundary -Snapshot $payload.snapshots.armed `
        -CurrentEngine $CurrentEngine -Label 'network repair armed snapshot'
    Assert-RepairSnapshotBoundary -Snapshot $payload.snapshots.post_first `
        -CurrentEngine $CurrentEngine -Label 'network repair post_first snapshot' `
        -RequirePostConnectAttachment
    Assert-RepairSnapshotBoundary -Snapshot $payload.snapshots.post_second `
        -CurrentEngine $CurrentEngine -Label 'network repair post_second snapshot' `
        -RequirePostConnectAttachment
    Assert-NativeJsonObject -Value $payload.comparisons -Label 'network repair comparisons'
    $comparisonNames = @(
        'pre_equals_armed', 'post_first_equals_post_second',
        'docker_engine_unchanged', 'all_container_lifecycle_inventory_unchanged',
        'continuous_docker_event_history_exact', 'postgres_stopped_identity_unchanged',
        'production_network_identity_unchanged', 'post_attachment_admissible_twice'
    )
    Assert-ExactProperties -Value $payload.comparisons `
        -Label 'network repair comparisons' -Expected $comparisonNames
    foreach ($name in $comparisonNames) {
        Assert-NativeJsonBoolean -Value $payload.comparisons.$name `
            -Label "network repair comparison $name"
        if (-not $payload.comparisons.$name) {
            throw "Network repair comparison was not accepted: $name"
        }
    }
    if (
        $payload.snapshots.pre.canonical_sha256 -cne
            $payload.snapshots.armed.canonical_sha256 -or
        $payload.snapshots.post_first.canonical_sha256 -cne
            $payload.snapshots.post_second.canonical_sha256 -or
        $payload.snapshots.pre.docker_engine.contract_sha256 -cne
            $payload.snapshots.post_second.docker_engine.contract_sha256 -or
        $payload.snapshots.pre.lifecycle_inventory_sha256 -cne
            $payload.snapshots.post_second.lifecycle_inventory_sha256 -or
        $payload.snapshots.pre.postgres.stable_identity_sha256 -cne
            $payload.snapshots.post_second.postgres.stable_identity_sha256 -or
        $payload.snapshots.pre.production_networks_sha256 -cne
            $payload.snapshots.post_second.production_networks_sha256
    ) {
        throw 'Network repair snapshots do not prove exact stable stopped lineage.'
    }
    Assert-NativeJsonObject -Value $payload.mutation -Label 'network repair mutation'
    Assert-ExactProperties -Value $payload.mutation -Label 'network repair mutation' `
        -Expected @('issued', 'command', 'output_line_count')
    Assert-NativeJsonBoolean -Value $payload.mutation.issued `
        -Label 'network repair mutation issued'
    Assert-NativeJsonInteger -Value $payload.mutation.output_line_count `
        -Label 'network repair mutation output_line_count'
    if ($payload.mutation.command -isnot [Array]) {
        throw 'Network repair mutation command must be a native JSON array.'
    }
    foreach ($token in @($payload.mutation.command)) {
        Assert-NativeJsonString -Value $token `
            -Label 'network repair mutation command token'
    }
    $expectedMutation = @(
        'network', 'connect', '--alias', 'postgres', '--alias',
        'weatherpoly-postgres-1', '--gw-priority', '0',
        [string]$productionNetworks['weatherpoly_safety-plane'].id,
        $productionPostgresId
    )
    if (
        $payload.disposition -cnotin @(
            'connected_by_this_invocation', 'verified_configured_pending_existing',
            'verified_exact_existing'
        ) -or
        ($payload.mutation.command -join "`n") -cne ($expectedMutation -join "`n") -or
        $payload.mutation.output_line_count -ne 0 -or
        ($payload.disposition -ceq 'connected_by_this_invocation') -ne
            [bool]$payload.mutation.issued
    ) {
        throw 'Network repair mutation/disposition evidence is not exact.'
    }
    Assert-RepairDockerEventWitnessBoundary -Witness $payload.event_witness `
        -MutationIssued ([bool]$payload.mutation.issued) -AcceptedAt $acceptedAt
    if (
        ($payload.disposition -ceq 'connected_by_this_invocation' -and (
            $payload.snapshots.pre.postgres.attachment_state -cne 'default_only' -or
            $payload.snapshots.armed.postgres.attachment_state -cne 'default_only'
        )) -or
        ($payload.disposition -ceq 'verified_exact_existing' -and (
            $payload.snapshots.pre.postgres.attachment_state -cne 'exact' -or
            $payload.snapshots.armed.postgres.attachment_state -cne 'exact' -or
            $payload.snapshots.pre.canonical_sha256 -cne
                $payload.snapshots.post_second.canonical_sha256
        )) -or
        ($payload.disposition -ceq 'verified_configured_pending_existing' -and (
            $payload.snapshots.pre.postgres.attachment_state -cne 'configured_pending' -or
            $payload.snapshots.armed.postgres.attachment_state -cne 'configured_pending' -or
            $payload.snapshots.pre.canonical_sha256 -cne
                $payload.snapshots.post_second.canonical_sha256
        ))
    ) {
        throw 'Network repair disposition does not match its before/after attachment state.'
    }
    Assert-NativeJsonObject -Value $payload.authorization -Label 'network repair authorization'
    $authorizationNames = @(
        'postgres_start_permitted', 'api_start_permitted', 'worker_start_permitted',
        'safety_control_start_permitted', 'live_permitted',
        'binance_kill_switch_removal_permitted'
    )
    Assert-ExactProperties -Value $payload.authorization `
        -Label 'network repair authorization' -Expected $authorizationNames
    foreach ($name in $authorizationNames) {
        Assert-NativeJsonBoolean -Value $payload.authorization.$name `
            -Label "network repair authorization $name"
        if ($payload.authorization.$name) {
            throw "Network repair evidence granted forbidden authorization: $name"
        }
    }
    return [pscustomobject]@{
        path = [IO.Path]::GetFullPath($LiteralPath)
        sha256 = $fileSha256
        payload_sha256 = [string]$envelope.payload_sha256
        accepted_at = $acceptedAt.ToString('o')
        expires_at = $acceptedAt.AddSeconds($networkRepairMaxAgeSeconds).ToString('o')
        disposition = [string]$payload.disposition
        post_first_sha256 = [string]$payload.snapshots.post_first.canonical_sha256
        post_second_sha256 = [string]$payload.snapshots.post_second.canonical_sha256
        final_attachment_sha256 =
            [string]$payload.snapshots.post_second.postgres.attachments_sha256
        docker_engine_contract_sha256 = [string]$payload.docker_engine.contract_sha256
        post_second = $payload.snapshots.post_second
    }
}

function Assert-NetworkRepairEvidenceUnchanged {
    param([Parameter(Mandatory = $true)][object] $Evidence)
    [void](Assert-RegularFile -LiteralPath $Evidence.path)
    if ((Get-FileSha256 -LiteralPath $Evidence.path) -cne $Evidence.sha256) {
        throw 'Stopped PostgreSQL network repair evidence changed during transition.'
    }
    $expiresAt = ConvertTo-RequiredUtcTime -Value $Evidence.expires_at `
        -Label 'network repair expires_at'
    if ([DateTimeOffset]::UtcNow -gt $expiresAt.AddSeconds($clockSkewSeconds)) {
        throw 'Stopped PostgreSQL network repair evidence expired during transition.'
    }
}

function Assert-NetworkRepairCurrentPostgres {
    param(
        [Parameter(Mandatory = $true)][object] $Evidence,
        [Parameter(Mandatory = $true)][object] $CurrentPostgres
    )
    $repairPostgres = $Evidence.post_second.postgres
    $stable = $repairPostgres.stable_identity
    if (
        $CurrentPostgres.container_id -cne $stable.container_id -or
        $CurrentPostgres.image_id -cne $stable.image_id -or
        $CurrentPostgres.config_image -cne $stable.config_image -or
        $CurrentPostgres.started_at -cne $stable.started_at -or
        $CurrentPostgres.finished_at -cne $stable.finished_at -or
        $CurrentPostgres.restart_count -ne $stable.restart_count -or
        $CurrentPostgres.state -cne $stable.state -or
        $CurrentPostgres.oom_killed -ne $stable.oom_killed -or
        $CurrentPostgres.exit_code -ne $stable.exit_code -or
        $CurrentPostgres.network_mode -cne $stable.network_mode
    ) {
        throw 'Current retained PostgreSQL drifted from network repair evidence.'
    }
    foreach ($name in @($productionNetworks.Keys)) {
        $current = $CurrentPostgres.networks.$name
        $repair = $repairPostgres.attachments.$name
        if (
            $current.id -cne $repair.network_id -or
            ($current.aliases -join "`n") -cne ($repair.aliases -join "`n") -or
            ($current.dns_names -join "`n") -cne ($repair.dns_names -join "`n") -or
            -not [string]::IsNullOrEmpty([string]$current.endpoint_id) -or
            -not [string]::IsNullOrEmpty([string]$current.ip_address)
        ) {
            throw "Current stopped PostgreSQL attachment drifted from repair evidence: $name"
        }
    }
}

function Get-ProductionNetworkBoundaries {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('Stopped', 'Running')]
        [string] $ExpectedState,
        [AllowNull()][object] $CollectorType
    )
    $result = [ordered]@{}
    $contractLines = [Collections.Generic.List[string]]::new()
    foreach ($entry in $productionNetworks.GetEnumerator()) {
        $expectedName = [string]$entry.Key
        $expected = $entry.Value
        $networkArguments = @(
                'network', 'inspect', [string]$expected.id, '--format', '{{json .}}'
        )
        $networkLines = if ($null -eq $CollectorType) {
            Invoke-Docker -Arguments $networkArguments
        }
        else {
            (Invoke-BoundedPinnedDockerRead -CollectorType $CollectorType `
                -Arguments $networkArguments `
                -Label "bounded production Docker network $expectedName").lines
        }
        $network = Get-OneJsonObject `
            -Label "production Docker network $expectedName" -Lines $networkLines
        foreach ($name in @('IPAM', 'Labels', 'Containers', 'ConfigFrom')) {
            Assert-NativeJsonObject -Value $network.$name `
                -Label "production network $expectedName $name"
        }
        Assert-ExactProperties -Value $network.IPAM `
            -Label "production network $expectedName IPAM" `
            -Expected @('Driver', 'Options', 'Config')
        Assert-ExactProperties -Value $network.ConfigFrom `
            -Label "production network $expectedName ConfigFrom" `
            -Expected @('Network')
        foreach ($name in @(
            'EnableIPv4', 'EnableIPv6', 'Internal', 'Attachable', 'Ingress',
            'ConfigOnly'
        )) {
            Assert-NativeJsonBoolean -Value $network.$name `
                -Label "production network $expectedName $name"
        }
        Assert-NativeJsonString -Value $network.ConfigFrom.Network `
            -Label "production network $expectedName ConfigFrom.Network"
        if ($network.IPAM.Config -isnot [Array]) {
            throw "Production network $expectedName IPAM.Config must be a native JSON array."
        }
        $ipamConfig = @($network.IPAM.Config)
        if ($ipamConfig.Count -eq 1) {
            Assert-NativeJsonObject -Value $ipamConfig[0] `
                -Label "production network $expectedName IPAM.Config entry"
            Assert-ExactProperties -Value $ipamConfig[0] `
                -Label "production network $expectedName IPAM.Config entry" `
                -Expected @('Subnet', 'Gateway', 'IPRange', 'AuxiliaryAddresses')
            foreach ($name in @('Subnet', 'Gateway', 'IPRange')) {
                Assert-NativeJsonString -Value $ipamConfig[0].$name `
                    -Label "production network $expectedName IPAM.Config $name"
            }
        }
        $ipRange = $null
        $auxiliaryAddresses = $null
        if ($ipamConfig.Count -eq 1) {
            $ipRange = Get-OptionalPropertyValue -InputObject $ipamConfig[0] -Name 'IPRange'
            $auxiliaryAddresses = Get-OptionalPropertyValue `
                -InputObject $ipamConfig[0] -Name 'AuxiliaryAddresses'
        }
        $containerIds = @(
            $network.Containers.PSObject.Properties |
                ForEach-Object { [string]$_.Name }
        )
        [Array]::Sort($containerIds, [StringComparer]::Ordinal)
        $expectedContainerIds = if ($ExpectedState -eq 'Running') {
            @($productionPostgresId)
        }
        else {
            @()
        }
        foreach ($name in @('Name', 'Id', 'Created', 'Scope', 'Driver')) {
            Assert-NativeJsonString -Value $network.$name `
                -Label "production network $expectedName $name"
        }
        Assert-NativeJsonString -Value $network.IPAM.Driver `
            -Label "production network $expectedName IPAM.Driver"
        if (
            $network.Name -cne $expectedName -or
            $network.Id -cne [string]$expected.id -or
            $network.Created -cne [string]$expected.created -or
            $network.Scope -cne 'local' -or
            $network.Driver -cne 'bridge' -or
            -not [bool]$network.EnableIPv4 -or
            [bool]$network.EnableIPv6 -or
            [bool]$network.Internal -or
            [bool]$network.Attachable -or
            [bool]$network.Ingress -or
            $network.ConfigOnly -or
            $network.IPAM.Driver -cne 'default' -or
            $null -ne (Get-OptionalPropertyValue -InputObject $network.IPAM -Name 'Options') -or
            $network.ConfigFrom.Network -cne '' -or
            $ipamConfig.Count -ne 1 -or
            $ipamConfig[0].Subnet -cne [string]$expected.subnet -or
            $ipamConfig[0].Gateway -cne [string]$expected.gateway -or
            -not [string]::IsNullOrEmpty([string]$ipRange) -or
            $null -ne $auxiliaryAddresses -or
            $network.Labels.'com.docker.compose.project' -cne 'weatherpoly' -or
            $network.Labels.'com.docker.compose.network' -cne [string]$expected.compose_name -or
            ($containerIds -join "`n") -cne ($expectedContainerIds -join "`n")
        ) {
            throw "Production Docker network identity/topology is not exact: $expectedName"
        }
        $labelsHash = Get-CanonicalPropertyHash -Value $network.Labels `
            -Label "production network $expectedName labels"
        $optionsHash = Get-CanonicalPropertyHash `
            -Value (Get-OptionalPropertyValue -InputObject $network -Name 'Options') `
            -Label "production network $expectedName options"
        if (
            $labelsHash -cne [string]$expected.labels_sha256 -or
            $optionsHash -cne $productionNetworkOptionsSha256
        ) {
            throw "Production Docker network labels/options drifted: $expectedName"
        }
        $contractLines.Add((@(
            $expectedName, [string]$network.Id, [string]$network.Driver,
            [string]$network.Created, [string]$network.Scope,
            [string]$ipamConfig[0].Subnet,
            [string]$ipamConfig[0].Gateway, $labelsHash, $optionsHash
        ) -join '|'))
        $memberEndpoint = $null
        if ($ExpectedState -eq 'Running') {
            $memberEndpoint = $network.Containers.$productionPostgresId
            Assert-NativeJsonObject -Value $memberEndpoint `
                -Label "production network $expectedName PostgreSQL endpoint"
            Assert-ExactProperties -Value $memberEndpoint `
                -Label "production network $expectedName PostgreSQL endpoint" `
                -Expected @(
                    'Name', 'EndpointID', 'MacAddress', 'IPv4Address', 'IPv6Address'
                )
            foreach ($name in @(
                'Name', 'EndpointID', 'MacAddress', 'IPv4Address', 'IPv6Address'
            )) {
                Assert-NativeJsonString -Value $memberEndpoint.$name `
                    -Label "production network $expectedName PostgreSQL endpoint $name"
            }
            if (
                $memberEndpoint.Name -cne 'weatherpoly-postgres-1' -or
                [string]$memberEndpoint.EndpointID -notmatch '^[0-9a-f]{64}$' -or
                [string]$memberEndpoint.MacAddress -notmatch
                    '^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$' -or
                [string]$memberEndpoint.IPv4Address -notmatch
                    ([string]$expected.ipv4_pattern).TrimEnd('$') + '/16$' -or
                -not [string]::IsNullOrEmpty($memberEndpoint.IPv6Address)
            ) {
                throw "Production network $expectedName PostgreSQL endpoint is not exact."
            }
        }
        $result[$expectedName] = [pscustomobject]@{
            name = $expectedName
            id = [string]$network.Id
            created = [string]$network.Created
            compose_name = [string]$expected.compose_name
            driver = [string]$network.Driver
            scope = [string]$network.Scope
            subnet = [string]$ipamConfig[0].Subnet
            gateway = [string]$ipamConfig[0].Gateway
            labels_sha256 = $labelsHash
            options_sha256 = $optionsHash
            attached_container_ids = $containerIds
            postgres_endpoint = $memberEndpoint
        }
    }
    return [pscustomobject]@{
        entries = [pscustomobject]$result
        contract_sha256 = Get-Sha256Text -Value (
            ($contractLines.ToArray() -join "`n") + "`n"
        )
    }
}

function Get-DockerHostPathAliases {
    param([Parameter(Mandatory = $true)][string] $Path)
    $normalized = [IO.Path]::GetFullPath($Path).Replace('\', '/')
    $aliases = [Collections.Generic.List[string]]::new()
    $aliases.Add($normalized)
    if ($normalized -match '^([A-Za-z]):/(.*)$') {
        $drive = $Matches[1].ToLowerInvariant()
        $tail = $Matches[2]
        $aliases.Add("/run/desktop/mnt/host/$drive/$tail")
        $aliases.Add("/host_mnt/$drive/$tail")
    }
    return @($aliases)
}

function Get-PostgresBoundary {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('Stopped', 'Running')]
        [string] $ExpectedState,
        [Parameter(Mandatory = $true)]
        [ValidateSet('unless-stopped', 'no')]
        [string] $ExpectedRestartPolicy
    )
    $inspect = Get-OneJsonObject -Label 'retained PostgreSQL inspect' -Lines (
        Invoke-Docker -Arguments @('inspect', $PostgresContainer, '--format', '{{json .}}')
    )
    foreach ($name in @('Config', 'State', 'HostConfig', 'NetworkSettings')) {
        Assert-NativeJsonObject -Value $inspect.$name -Label "PostgreSQL $name"
    }
    Assert-NativeJsonObject -Value $inspect.Config.Healthcheck `
        -Label 'PostgreSQL Config.Healthcheck'
    if ($inspect.Config.Healthcheck.Test -isnot [Array]) {
        throw 'PostgreSQL Config.Healthcheck.Test must be a native JSON array.'
    }
    $healthcheckTest = @($inspect.Config.Healthcheck.Test)
    Assert-NativeJsonObject -Value $inspect.NetworkSettings.Networks `
        -Label 'PostgreSQL NetworkSettings.Networks'
    Assert-NativeJsonObject -Value $inspect.NetworkSettings.Ports `
        -Label 'PostgreSQL NetworkSettings.Ports'
    Assert-NativeJsonObject -Value $inspect.HostConfig.PortBindings `
        -Label 'PostgreSQL HostConfig.PortBindings'
    if ($inspect.Mounts -isnot [Array]) {
        throw 'PostgreSQL Mounts must be a native JSON array.'
    }
    foreach ($name in @('Id', 'Name', 'Image')) {
        Assert-NativeJsonString -Value $inspect.$name -Label "PostgreSQL $name"
    }
    Assert-NativeJsonString -Value $inspect.Config.Image -Label 'PostgreSQL Config.Image'
    Assert-NativeJsonString -Value $inspect.State.StartedAt -Label 'PostgreSQL StartedAt'
    Assert-NativeJsonString -Value $inspect.State.FinishedAt -Label 'PostgreSQL FinishedAt'
    Assert-NativeJsonBoolean -Value $inspect.State.Running -Label 'PostgreSQL Running'
    Assert-NativeJsonBoolean -Value $inspect.State.OOMKilled -Label 'PostgreSQL OOMKilled'
    Assert-NativeJsonInteger -Value $inspect.RestartCount -Label 'PostgreSQL RestartCount'
    $hbaMounts = @($inspect.Mounts | Where-Object {
        [string]$_.Destination -ceq '/etc/postgresql/stratus-pg_hba.conf'
    })
    $dataMounts = @($inspect.Mounts | Where-Object {
        [string]$_.Destination -ceq '/var/lib/postgresql/data'
    })
    $hbaSources = @(Get-DockerHostPathAliases -Path (
        Join-Path $absoluteRoot 'ops\postgres\stratus-pg_hba.conf'
    ))
    if ($hbaMounts.Count -eq 1) {
        Assert-NativeJsonObject -Value $hbaMounts[0] -Label 'PostgreSQL HBA mount'
        Assert-ExactProperties -Value $hbaMounts[0] -Label 'PostgreSQL HBA mount' `
            -Expected @('Type', 'Source', 'Destination', 'Mode', 'RW', 'Propagation')
    }
    if ($dataMounts.Count -eq 1) {
        Assert-NativeJsonObject -Value $dataMounts[0] -Label 'PostgreSQL data mount'
        Assert-ExactProperties -Value $dataMounts[0] -Label 'PostgreSQL data mount' `
            -Expected @(
                'Type', 'Name', 'Source', 'Destination', 'Driver', 'Mode', 'RW',
                'Propagation'
            )
    }
    $port = $inspect.HostConfig.PortBindings.'5432/tcp'
    if ($port -isnot [Array]) {
        throw 'PostgreSQL HostConfig.PortBindings 5432/tcp must be a native JSON array.'
    }
    if (@($port).Count -eq 1) {
        Assert-NativeJsonObject -Value $port[0] `
            -Label 'PostgreSQL HostConfig.PortBindings 5432/tcp entry'
        Assert-ExactProperties -Value $port[0] `
            -Label 'PostgreSQL HostConfig.PortBindings 5432/tcp entry' `
            -Expected @('HostIp', 'HostPort')
    }
    $configuredPortProperties = @(
        $inspect.HostConfig.PortBindings.PSObject.Properties |
            Where-Object { $null -ne $_.Value }
    )
    $runtimePortProperties = @($inspect.NetworkSettings.Ports.PSObject.Properties)
    $networkProperties = @($inspect.NetworkSettings.Networks.PSObject.Properties)
    $actualNetworkNames = @($networkProperties.Name)
    [Array]::Sort($actualNetworkNames, [StringComparer]::Ordinal)
    $expectedNetworkNames = @($productionNetworks.Keys)
    [Array]::Sort($expectedNetworkNames, [StringComparer]::Ordinal)
    if (
        $networkProperties.Count -ne 2 -or
        ($actualNetworkNames -join "`n") -cne ($expectedNetworkNames -join "`n")
    ) {
        throw 'Retained production PostgreSQL has an unexpected Docker network attachment.'
    }
    $networkBoundaries = Get-ProductionNetworkBoundaries -ExpectedState $ExpectedState
    if (
        $inspect.Id -cne $productionPostgresId -or
        $inspect.Name -cne '/weatherpoly-postgres-1' -or
        $inspect.Image -cne $postgres16ImageId -or
        $inspect.Config.Image -cne $postgres16Image -or
        $healthcheckTest.Count -ne 2 -or
        $healthcheckTest[0] -cne 'CMD-SHELL' -or
        $healthcheckTest[1] -cne 'pg_isready -U stratus -d stratus' -or
        $inspect.RestartCount -ne 0 -or
        $inspect.State.OOMKilled -or
        $inspect.State.Paused -or
        $inspect.State.Restarting -or
        $inspect.State.Dead -or
        -not [string]::IsNullOrWhiteSpace([string]$inspect.State.Error) -or
        $inspect.HostConfig.RestartPolicy.Name -cne $ExpectedRestartPolicy -or
        [int]$inspect.HostConfig.RestartPolicy.MaximumRetryCount -ne 0 -or
        $inspect.HostConfig.NetworkMode -cne 'weatherpoly_default' -or
        $configuredPortProperties.Count -ne 1 -or
        $configuredPortProperties[0].Name -cne '5432/tcp' -or
        @($port).Count -ne 1 -or
        $port[0].HostIp -cne '127.0.0.1' -or
        $port[0].HostPort -cne '5432' -or
        @($inspect.Mounts).Count -ne 2 -or
        $hbaMounts.Count -ne 1 -or
        $hbaMounts[0].Type -cne 'bind' -or
        [bool]$hbaMounts[0].RW -or
        $hbaMounts[0].Mode -cne 'ro' -or
        $hbaMounts[0].Propagation -cne 'rprivate' -or
        ([string]$hbaMounts[0].Source).Replace('\', '/') -notin $hbaSources -or
        $dataMounts.Count -ne 1 -or
        $dataMounts[0].Type -cne 'volume' -or
        $dataMounts[0].Name -cne 'weatherpoly_stratus-postgres' -or
        -not [bool]$dataMounts[0].RW -or
        $dataMounts[0].Mode -cne 'rw' -or
        $dataMounts[0].Driver -cne 'local' -or
        $dataMounts[0].Source -cne $productionDataVolumeSource -or
        $dataMounts[0].Propagation -cne ''
    ) {
        throw 'Retained production PostgreSQL identity/topology is not exact.'
    }
    $endpointLines = [Collections.Generic.List[string]]::new()
    $networkEvidence = [ordered]@{}
    foreach ($networkName in $expectedNetworkNames) {
        $networkAttachment = $inspect.NetworkSettings.Networks.$networkName
        Assert-NativeJsonObject -Value $networkAttachment `
            -Label "PostgreSQL network attachment $networkName"
        Assert-ExactProperties -Value $networkAttachment `
            -Label "PostgreSQL network attachment $networkName" -Expected @(
                'IPAMConfig', 'Links', 'Aliases', 'MacAddress', 'DriverOpts',
                'GwPriority', 'NetworkID', 'EndpointID', 'Gateway', 'IPAddress',
                'IPPrefixLen', 'IPv6Gateway', 'GlobalIPv6Address',
                'GlobalIPv6PrefixLen', 'DNSNames'
            )
        $expectedNetwork = $productionNetworks[$networkName]
        $networkBoundary = $networkBoundaries.entries.$networkName
        if (
            $networkAttachment.Aliases -isnot [Array] -or
            $networkAttachment.DNSNames -isnot [Array]
        ) {
            throw "PostgreSQL network attachment arrays are not native: $networkName"
        }
        foreach ($value in @($networkAttachment.Aliases) + @($networkAttachment.DNSNames)) {
            Assert-NativeJsonString -Value $value `
                -Label "PostgreSQL network attachment name $networkName"
        }
        foreach ($name in @(
            'NetworkID', 'EndpointID', 'Gateway', 'IPAddress', 'IPv6Gateway',
            'GlobalIPv6Address', 'MacAddress'
        )) {
            Assert-NativeJsonString -Value $networkAttachment.$name `
                -Label "PostgreSQL network attachment $networkName $name"
        }
        foreach ($name in @('GwPriority', 'IPPrefixLen', 'GlobalIPv6PrefixLen')) {
            Assert-NativeJsonInteger -Value $networkAttachment.$name `
                -Label "PostgreSQL network attachment $networkName $name"
        }
        $aliases = [string[]]@($networkAttachment.Aliases)
        $expectedAliases = [string[]]@('postgres', 'weatherpoly-postgres-1')
        $dnsNames = [string[]]@($networkAttachment.DNSNames)
        $expectedDnsNames = [string[]]@(
            $productionPostgresId.Substring(0, 12), 'postgres', 'weatherpoly-postgres-1'
        )
        [Array]::Sort($aliases, [StringComparer]::Ordinal)
        [Array]::Sort($expectedAliases, [StringComparer]::Ordinal)
        [Array]::Sort($dnsNames, [StringComparer]::Ordinal)
        [Array]::Sort($expectedDnsNames, [StringComparer]::Ordinal)
        $ipamConfiguration = if ($null -eq $networkAttachment.IPAMConfig) {
            'null'
        }
        elseif (
            $networkAttachment.IPAMConfig -is [pscustomobject] -and
            @($networkAttachment.IPAMConfig.PSObject.Properties).Count -eq 0
        ) {
            'empty_object'
        }
        else {
            'other'
        }
        $driverOptions = if ($null -eq $networkAttachment.DriverOpts) {
            'null'
        }
        elseif (
            $networkAttachment.DriverOpts -is [pscustomobject] -and
            @($networkAttachment.DriverOpts.PSObject.Properties).Count -eq 0
        ) {
            'empty_object'
        }
        else {
            'other'
        }
        $configuredPending =
            $ExpectedState -ceq 'Stopped' -and
            $networkName -ceq 'weatherpoly_safety-plane' -and
            $networkAttachment.NetworkID -ceq '' -and
            $ipamConfiguration -ceq 'empty_object' -and
            $driverOptions -ceq 'empty_object'
        $materializedConfiguration =
            $networkAttachment.NetworkID -ceq [string]$expectedNetwork.id -and
            $ipamConfiguration -ceq 'null' -and
            $driverOptions -ceq 'null'
        if (
            (-not $materializedConfiguration -and -not $configuredPending) -or
            $null -ne $networkAttachment.Links -or
            [int]$networkAttachment.GwPriority -ne 0 -or
            ($aliases -join "`n") -cne ($expectedAliases -join "`n") -or
            ($dnsNames -join "`n") -cne ($expectedDnsNames -join "`n") -or
            -not [string]::IsNullOrEmpty([string]$networkAttachment.IPv6Gateway) -or
            -not [string]::IsNullOrEmpty([string]$networkAttachment.GlobalIPv6Address) -or
            [int]$networkAttachment.GlobalIPv6PrefixLen -ne 0
        ) {
            throw "Retained PostgreSQL network attachment is not exact: $networkName"
        }
        if ($ExpectedState -eq 'Stopped') {
            if (
                -not [string]::IsNullOrEmpty([string]$networkAttachment.EndpointID) -or
                -not [string]::IsNullOrEmpty([string]$networkAttachment.Gateway) -or
                -not [string]::IsNullOrEmpty([string]$networkAttachment.IPAddress) -or
                [int]$networkAttachment.IPPrefixLen -ne 0 -or
                -not [string]::IsNullOrEmpty([string]$networkAttachment.MacAddress)
            ) {
                throw "Stopped PostgreSQL retains an active network endpoint: $networkName"
            }
        }
        else {
            if (
                [string]$networkAttachment.EndpointID -notmatch '^[0-9a-f]{64}$' -or
                $networkAttachment.Gateway -cne [string]$expectedNetwork.gateway -or
                [string]$networkAttachment.IPAddress -notmatch [string]$expectedNetwork.ipv4_pattern -or
                [int]$networkAttachment.IPPrefixLen -ne 16 -or
                [string]$networkAttachment.MacAddress -notmatch '^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$' -or
                $networkBoundary.postgres_endpoint.EndpointID -cne
                    [string]$networkAttachment.EndpointID -or
                $networkBoundary.postgres_endpoint.MacAddress -cne
                    [string]$networkAttachment.MacAddress -or
                $networkBoundary.postgres_endpoint.IPv4Address -cne
                    "$($networkAttachment.IPAddress)/$($networkAttachment.IPPrefixLen)" -or
                $networkBoundary.postgres_endpoint.Name -cne 'weatherpoly-postgres-1' -or
                -not [string]::IsNullOrEmpty(
                    [string]$networkBoundary.postgres_endpoint.IPv6Address
                )
            ) {
                throw "Running PostgreSQL network endpoint is not exact: $networkName"
            }
        }
        $endpointLine = @(
            $networkName, [string]$networkAttachment.NetworkID,
            [string]$networkAttachment.EndpointID, [string]$networkAttachment.Gateway,
            [string]$networkAttachment.IPAddress, [string]$networkAttachment.IPPrefixLen,
            [string]$networkAttachment.IPv6Gateway,
            [string]$networkAttachment.GlobalIPv6Address,
            [string]$networkAttachment.GlobalIPv6PrefixLen,
            [string]$networkAttachment.MacAddress,
            ($aliases -join ','), ($dnsNames -join ',')
        ) -join '|'
        $endpointLines.Add($endpointLine)
        $networkEvidence[$networkName] = [pscustomobject]@{
            id = [string]$networkAttachment.NetworkID
            endpoint_id = [string]$networkAttachment.EndpointID
            gateway = [string]$networkAttachment.Gateway
            ip_address = [string]$networkAttachment.IPAddress
            ip_prefix_length = [int]$networkAttachment.IPPrefixLen
            mac_address = [string]$networkAttachment.MacAddress
            aliases = $aliases
            dns_names = $dnsNames
        }
    }
    if ($ExpectedState -eq 'Stopped') {
        if (
            $runtimePortProperties.Count -ne 0 -or
            $inspect.State.Status -cne 'exited' -or
            $inspect.State.Running -or
            $inspect.State.Pid -ne 0 -or
            $inspect.State.ExitCode -ne 0
        ) {
            throw 'PreStart requires the retained production PostgreSQL to be cleanly stopped.'
        }
    }
    else {
        Assert-NativeJsonObject -Value $inspect.State.Health -Label 'PostgreSQL State.Health'
        Assert-NativeJsonString -Value $inspect.State.Health.Status `
            -Label 'PostgreSQL State.Health.Status'
        $runtimePort = $inspect.NetworkSettings.Ports.'5432/tcp'
        if ($runtimePort -isnot [Array]) {
            throw 'Running PostgreSQL 5432/tcp runtime port must be a native JSON array.'
        }
        if (@($runtimePort).Count -eq 1) {
            Assert-NativeJsonObject -Value $runtimePort[0] `
                -Label 'running PostgreSQL 5432/tcp runtime port'
            Assert-ExactProperties -Value $runtimePort[0] `
                -Label 'running PostgreSQL 5432/tcp runtime port' `
                -Expected @('HostIp', 'HostPort')
        }
        if (
            $runtimePortProperties.Count -ne 1 -or
            $runtimePortProperties[0].Name -cne '5432/tcp' -or
            @($runtimePort).Count -ne 1 -or
            $runtimePort[0].HostIp -cne '127.0.0.1' -or
            $runtimePort[0].HostPort -cne '5432' -or
            $inspect.State.Status -cne 'running' -or
            -not $inspect.State.Running -or
            $inspect.State.Pid -le 0 -or
            $inspect.State.FinishedAt -cne '0001-01-01T00:00:00Z' -or
            $inspect.State.ExitCode -ne 0 -or
            $inspect.State.Health.Status -cne 'healthy'
        ) {
            throw 'Live successor proof requires exact healthy PostgreSQL and loopback port state.'
        }
    }
    $mountContract = @(
        [string]$hbaMounts[0].Type, [string]$hbaMounts[0].Source,
        [string]$hbaMounts[0].Destination, [string]$hbaMounts[0].Mode,
        [string]$hbaMounts[0].RW, [string]$hbaMounts[0].Propagation,
        [string]$dataMounts[0].Type, [string]$dataMounts[0].Name,
        [string]$dataMounts[0].Driver, [string]$dataMounts[0].Source,
        [string]$dataMounts[0].Destination, [string]$dataMounts[0].Mode,
        [string]$dataMounts[0].RW, [string]$dataMounts[0].Propagation
    ) -join "`0"
    $portsContract = if ($ExpectedState -eq 'Stopped') {
        'stopped-empty'
    }
    else {
        '5432/tcp|127.0.0.1|5432'
    }
    return [pscustomobject]@{
        container_id = [string]$inspect.Id
        image_id = [string]$inspect.Image
        config_image = [string]$inspect.Config.Image
        restart_count = [int]$inspect.RestartCount
        started_at = [string]$inspect.State.StartedAt
        finished_at = [string]$inspect.State.FinishedAt
        state = [string]$inspect.State.Status
        oom_killed = [bool]$inspect.State.OOMKilled
        exit_code = [int]$inspect.State.ExitCode
        restart_policy = [string]$inspect.HostConfig.RestartPolicy.Name
        health_status = if ($ExpectedState -eq 'Running') {
            [string]$inspect.State.Health.Status
        }
        else {
            'not-running'
        }
        network_mode = [string]$inspect.HostConfig.NetworkMode
        mount_contract_sha256 = Get-Sha256Text -Value ($mountContract + "`n")
        ports_contract_sha256 = Get-Sha256Text -Value ($portsContract + "`n")
        networks = [pscustomobject]$networkEvidence
        network_contract_sha256 = [string]$networkBoundaries.contract_sha256
        active_endpoint_sha256 = Get-Sha256Text -Value (
            ($endpointLines.ToArray() -join "`n") + "`n"
        )
    }
}

function Assert-ExclusiveRuntimeTopology {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('Stopped', 'Running')]
        [string] $PostgresState
    )
    $services = @(
        Invoke-Docker -Arguments (
            $composeArguments + @('ps', '--status', 'running', '--services')
        ) | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    $runningIds = @(
        Invoke-Docker -Arguments @('ps', '-q', '--no-trunc') |
            Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    if ($PostgresState -eq 'Stopped') {
        if ($services.Count -ne 0 -or $runningIds.Count -ne 0) {
            throw 'PreStart requires zero running containers after proof-container cleanup.'
        }
    }
    else {
        if (
            $services.Count -ne 1 -or
            $services[0] -cne 'postgres' -or
            $runningIds.Count -ne 1 -or
            $runningIds[0] -cne $productionPostgresId
        ) {
            throw 'Live successor proof permits only the exact production PostgreSQL container.'
        }
    }
    return [pscustomobject]@{
        running_count = $runningIds.Count
        postgres_only = ($PostgresState -eq 'Running')
        application_writers_running = $false
    }
}

function Wait-PostgresContainerHealthy {
    param(
        [Parameter(Mandatory = $true)][string] $StartRevokePath,
        [Parameter(Mandatory = $true)][object] $Watchdog,
        [Parameter(Mandatory = $true)][object] $HeartbeatPublisher
    )
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(60)
    do {
        Assert-PostgresStartNotRevoked -RevokePath $StartRevokePath
        [void](Assert-WatchdogHeartbeatPublisher -Publisher $HeartbeatPublisher)
        Assert-TransitionWatchdogProcessConnected -Watchdog $Watchdog
        $health = Get-OneJsonObject -Label 'retained PostgreSQL health state' -Lines (
            Invoke-Docker -Arguments @(
                'inspect', $productionPostgresId, '--format', '{{json .State.Health}}'
            )
        )
        Assert-NativeJsonString -Value $health.Status -Label 'PostgreSQL health status'
        if ($health.Status -ceq 'healthy') {
            Assert-PostgresStartNotRevoked -RevokePath $StartRevokePath
            return [pscustomobject]@{
                status = 'healthy'
                checked_at = [DateTimeOffset]::UtcNow.ToString('o')
            }
        }
        if ($health.Status -cnotin @('starting', 'unhealthy')) {
            throw 'Retained PostgreSQL returned an unexpected health status.'
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw 'Retained PostgreSQL did not become healthy within the bounded start window.'
}

function Test-TransitionOwnedPostgresRunning {
    param([Parameter(Mandatory = $true)][DateTimeOffset] $NotBefore)
    $inspect = Get-OneJsonObject -Label 'rollback ownership inspect' -Lines (
        Invoke-Docker -Arguments @(
            'inspect', $productionPostgresId, '--format', '{{json .}}'
        )
    )
    foreach ($name in @('Config', 'State', 'HostConfig')) {
        Assert-NativeJsonObject -Value $inspect.$name -Label "rollback ownership $name"
    }
    if (-not [bool]$inspect.State.Running) {
        return $false
    }
    if (
        $inspect.Id -cne $productionPostgresId -or
        $inspect.Image -cne $postgres16ImageId -or
        $inspect.Config.Image -cne $postgres16Image -or
        [int]$inspect.RestartCount -ne 0 -or
        $inspect.HostConfig.RestartPolicy.Name -cne 'no' -or
        [int]$inspect.HostConfig.RestartPolicy.MaximumRetryCount -ne 0
    ) {
        throw 'Rollback ownership inspect found a running but non-exact PostgreSQL identity.'
    }
    $startedAt = ConvertTo-RequiredUtcTime -Value ([string]$inspect.State.StartedAt) `
        -Label 'rollback ownership StartedAt'
    if ($startedAt -le $NotBefore.AddSeconds(-$clockSkewSeconds)) {
        throw 'Rollback ownership inspect found a running PostgreSQL outside this transition.'
    }
    return $true
}

function Get-HeldHbaReadBoundary {
    if (
        $null -eq $script:hbaReadLock -or
        [string]::IsNullOrWhiteSpace([string]$script:hbaReadLockPath) -or
        $script:hbaReadLock.SafeFileHandle.IsClosed -or
        $script:hbaReadLock.SafeFileHandle.IsInvalid -or
        -not $script:hbaReadLock.CanRead
    ) {
        throw 'The transition no longer holds the exact host HBA read lock.'
    }
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $script:hbaReadLock.Position = 0
        $sha256 = [BitConverter]::ToString(
            $algorithm.ComputeHash($script:hbaReadLock)
        ).Replace('-', '').ToLowerInvariant()
        $script:hbaReadLock.Position = 0
    }
    finally {
        $algorithm.Dispose()
    }
    if ($sha256 -cne $fixedHbaSha256) {
        throw 'The held host HBA bytes are not the exact reviewed file.'
    }
    return [pscustomobject]@{
        held = $true
        path = [string]$script:hbaReadLockPath
        sha256 = $sha256
        bytes = [int64]$script:hbaReadLock.Length
        access = 'Read'
        share = 'Read'
        write_share_permitted = $false
        delete_share_permitted = $false
    }
}

function Open-HeldHbaReadBoundary {
    $path = [IO.Path]::GetFullPath(
        (Join-Path $absoluteRoot 'ops\postgres\stratus-pg_hba.conf')
    )
    [void](Assert-RegularFile -LiteralPath $path)
    if ($null -ne $script:hbaReadLock) {
        throw 'The host HBA read lock was already initialized.'
    }
    $stream = [IO.FileStream]::new(
        $path,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read,
        4096,
        [IO.FileOptions]::SequentialScan
    )
    $script:hbaReadLock = $stream
    $script:hbaReadLockPath = $path
    try {
        return Get-HeldHbaReadBoundary
    }
    catch {
        $stream.Dispose()
        $script:hbaReadLock = $null
        $script:hbaReadLockPath = $null
        throw
    }
}

function Get-MountedHbaSha256 {
    $lines = @(
        Invoke-Docker -Arguments @(
            'exec', $productionPostgresId, 'sha256sum',
            '/etc/postgresql/stratus-pg_hba.conf'
        ) | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    if ($lines.Count -ne 1 -or $lines[0] -notmatch '^([0-9a-f]{64})\s+') {
        throw 'Mounted HBA byte proof did not return one SHA-256.'
    }
    return $Matches[1]
}

function Get-FreshHbaState {
    param([Parameter(Mandatory = $true)][string] $Label)
    $command = @'
PGPASSWORD="$POSTGRES_PASSWORD" exec psql -XAtq --set=ON_ERROR_STOP=1 --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" <<'STRATUS_SQL'
SELECT 'META|' || concat_ws(
  '|', pg_conf_load_time()::text, pg_postmaster_start_time()::text,
  pg_backend_pid()::text,
  (SELECT backend_start::text FROM pg_stat_activity WHERE pid = pg_backend_pid()),
  current_setting('hba_file')
);
SELECT 'PATH|' || current_setting('hba_file');
SELECT 'RULE|' || concat_ws(
  '|', file_name, line_number::text, type,
  array_to_string(database, ','), array_to_string(user_name, ','),
  coalesce(address, ''), coalesce(netmask, ''), auth_method,
  coalesce(array_to_string(options, ','), ''), coalesce(error, '')
)
FROM pg_hba_file_rules
ORDER BY rule_number;
STRATUS_SQL
'@
    $lines = @(
        Invoke-Docker -Arguments @(
            'exec', $productionPostgresId, 'sh', '-ec', $command
        ) | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    $metadata = @($lines | Where-Object { $_ -like 'META|*' })
    $inventory = @($lines | Where-Object { $_ -notlike 'META|*' })
    $ruleLines = @($inventory | Where-Object { $_ -like 'RULE|*' })
    $errorRules = @($ruleLines | Where-Object { $_ -notmatch '\|$' })
    $inventoryHash = Get-Sha256Text -Value ($inventory -join "`n")
    if (
        $metadata.Count -ne 1 -or
        $inventory.Count -ne 16 -or
        $inventory[0] -cne 'PATH|/etc/postgresql/stratus-pg_hba.conf' -or
        $ruleLines.Count -ne 15 -or
        $errorRules.Count -ne 0 -or
        $inventoryHash -cne $fixedHbaInventorySha256
    ) {
        throw "$Label did not expose the exact fresh HBA inventory."
    }
    $parts = $metadata[0].Split('|')
    if ($parts.Count -ne 6 -or
        $parts[5] -cne '/etc/postgresql/stratus-pg_hba.conf' -or
        [int]$parts[3] -le 0) {
        throw "$Label returned malformed fresh HBA metadata."
    }
    $confLoadTime = ConvertTo-RequiredUtcTime -Value $parts[1] `
        -Label "$Label conf_load_time"
    $postmasterStartedAt = ConvertTo-RequiredUtcTime -Value $parts[2] `
        -Label "$Label postmaster_start_time"
    $backendStartedAt = ConvertTo-RequiredUtcTime -Value $parts[4] `
        -Label "$Label backend_start"
    if ($confLoadTime -gt $backendStartedAt) {
        throw "$Label returned a configuration load time after backend start."
    }
    return [pscustomobject]@{
        conf_load_time = $confLoadTime.ToString('o')
        postmaster_started_at = $postmasterStartedAt.ToString('o')
        backend_pid = [int]$parts[3]
        backend_started_at = $backendStartedAt.ToString('o')
        hba_file = $parts[5]
        inventory_sha256 = $inventoryHash
        inventory_line_count = $inventory.Count
        inventory_rule_count = $ruleLines.Count
        inventory_errors = $errorRules.Count
    }
}

function Assert-HbaFirstBoundary {
    $hbaPath = Join-Path $absoluteRoot 'ops\postgres\stratus-pg_hba.conf'
    [void](Assert-RegularFile -LiteralPath $hbaPath)
    $hostBeforeBoundary = Get-HeldHbaReadBoundary
    $hostBefore = [string]$hostBeforeBoundary.sha256
    $mountedBefore = Get-MountedHbaSha256
    if ($hostBefore -cne $fixedHbaSha256 -or $mountedBefore -cne $fixedHbaSha256) {
        throw 'HBA bytes drifted before the first successor database operation.'
    }
    # pg_conf_load_time() is backend-local. One psql process is retained, but
    # each sleep and sample is a separate frontend protocol command so the
    # backend can process SIGHUP between statements.
    $commandPrefix = @'
PGPASSWORD="$POSTGRES_PASSWORD" exec psql -XAtq --set=ON_ERROR_STOP=1 --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" <<'STRATUS_SQL'
SELECT 'BASE|' || concat_ws(
  '|', pg_conf_load_time()::text, clock_timestamp()::text,
  pg_postmaster_start_time()::text, pg_backend_pid()::text,
  (SELECT backend_start::text FROM pg_stat_activity WHERE pid = pg_backend_pid()),
  current_setting('hba_file')
);
SELECT 'RELOAD|' || pg_reload_conf()::text;
'@
    $pollCommands = [Text.StringBuilder]::new()
    foreach ($pollNumber in 1..40) {
        [void]$pollCommands.AppendLine('SELECT pg_sleep(0.25);')
        [void]$pollCommands.AppendLine((@"
SELECT 'POLL|$pollNumber|' || concat_ws(
  '|', pg_conf_load_time()::text, clock_timestamp()::text,
  pg_postmaster_start_time()::text, pg_backend_pid()::text,
  (SELECT backend_start::text FROM pg_stat_activity WHERE pid = pg_backend_pid()),
  current_setting('hba_file')
);
"@).TrimStart())
    }
    $commandSuffix = @'
SELECT 'PATH|' || current_setting('hba_file');
SELECT 'RULE|' || concat_ws(
  '|', file_name, line_number::text, type,
  array_to_string(database, ','), array_to_string(user_name, ','),
  coalesce(address, ''), coalesce(netmask, ''), auth_method,
  coalesce(array_to_string(options, ','), ''), coalesce(error, '')
)
FROM pg_hba_file_rules
ORDER BY rule_number;
STRATUS_SQL
'@
    $reloadAndInventoryCommand = $commandPrefix + "`n" +
        $pollCommands.ToString() + $commandSuffix
    $proofLines = @(
        Invoke-Docker -Arguments @(
            'exec', $productionPostgresId, 'sh', '-ec', $reloadAndInventoryCommand
        ) | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    $baseLines = @($proofLines | Where-Object { $_ -like 'BASE|*' })
    $reloadLines = @($proofLines | Where-Object { $_ -like 'RELOAD|*' })
    $pollLines = @($proofLines | Where-Object { $_ -like 'POLL|*' })
    $inventory = @($proofLines | Where-Object {
        $_ -notlike 'BASE|*' -and $_ -notlike 'RELOAD|*' -and $_ -notlike 'POLL|*'
    })
    $inventoryHash = Get-Sha256Text -Value ($inventory -join "`n")
    $ruleLines = @($inventory | Where-Object { $_ -like 'RULE|*' })
    $errorRules = @($ruleLines | Where-Object { $_ -notmatch '\|$' })
    if (
        $baseLines.Count -ne 1 -or
        $reloadLines.Count -ne 1 -or
        $reloadLines[0] -cne 'RELOAD|true' -or
        $pollLines.Count -ne 40 -or
        $inventory.Count -ne 16 -or
        $inventory[0] -cne 'PATH|/etc/postgresql/stratus-pg_hba.conf' -or
        $ruleLines.Count -ne 15 -or
        $errorRules.Count -ne 0 -or
        $inventoryHash -cne $fixedHbaInventorySha256
    ) {
        throw 'Loaded PostgreSQL HBA inventory is not the exact reviewed 16-line boundary.'
    }
    $baseParts = $baseLines[0].Split('|')
    if ($baseParts.Count -ne 7 -or
        $baseParts[6] -cne '/etc/postgresql/stratus-pg_hba.conf') {
        throw 'PostgreSQL HBA reload metadata is malformed.'
    }
    $confLoadBefore = ConvertTo-RequiredUtcTime -Value $baseParts[1] `
        -Label 'HBA conf_load_time before reload'
    $reloadRequestedAt = ConvertTo-RequiredUtcTime -Value $baseParts[2] `
        -Label 'HBA reload requested_at'
    $postmasterBefore = ConvertTo-RequiredUtcTime -Value $baseParts[3] `
        -Label 'HBA postmaster time before reload'
    $backendPid = [int]$baseParts[4]
    $backendStartedAt = ConvertTo-RequiredUtcTime -Value $baseParts[5] `
        -Label 'HBA proof backend start'
    $acknowledgedPoll = 0
    $confLoadAfter = [DateTimeOffset]::MinValue
    $reloadAcknowledgedAt = [DateTimeOffset]::MinValue
    foreach ($pollIndex in 0..39) {
        $parts = $pollLines[$pollIndex].Split('|')
        if ($parts.Count -ne 8 -or [int]$parts[1] -ne ($pollIndex + 1) -or
            $parts[7] -cne $baseParts[6]) {
            throw 'PostgreSQL HBA poll metadata is malformed.'
        }
        $sampleLoadTime = ConvertTo-RequiredUtcTime -Value $parts[2] `
            -Label 'HBA poll conf_load_time'
        $sampledAt = ConvertTo-RequiredUtcTime -Value $parts[3] `
            -Label 'HBA poll sampled_at'
        $samplePostmaster = ConvertTo-RequiredUtcTime -Value $parts[4] `
            -Label 'HBA poll postmaster time'
        $sampleBackendStart = ConvertTo-RequiredUtcTime -Value $parts[6] `
            -Label 'HBA poll backend start'
        if (
            $samplePostmaster -ne $postmasterBefore -or
            [int]$parts[5] -ne $backendPid -or
            $sampleBackendStart -ne $backendStartedAt -or
            $sampledAt -lt $reloadRequestedAt
        ) {
            throw 'PostgreSQL HBA poll escaped the original backend boundary.'
        }
        if ($acknowledgedPoll -eq 0 -and
            $sampleLoadTime -gt $confLoadBefore -and
            $sampleLoadTime -ge $reloadRequestedAt) {
            $acknowledgedPoll = $pollIndex + 1
            $confLoadAfter = $sampleLoadTime
            $reloadAcknowledgedAt = $sampledAt
        }
        elseif ($acknowledgedPoll -gt 0 -and $sampleLoadTime -ne $confLoadAfter) {
            throw 'PostgreSQL processed an additional configuration reload during HBA proof.'
        }
    }
    if (
        $acknowledgedPoll -eq 0 -or
        $reloadAcknowledgedAt -lt $confLoadAfter -or
        $backendStartedAt -gt $reloadRequestedAt -or
        $backendPid -le 0
    ) {
        throw 'PostgreSQL did not acknowledge the exact HBA reload in one bounded backend.'
    }
    $freshLoadedState = Get-FreshHbaState -Label 'post-reload fresh HBA state'
    $freshConfLoadTime = ConvertTo-RequiredUtcTime `
        -Value $freshLoadedState.conf_load_time -Label 'fresh postmaster HBA load time'
    $freshBackendStartedAt = ConvertTo-RequiredUtcTime `
        -Value $freshLoadedState.backend_started_at -Label 'fresh HBA backend start'
    if (
        $freshConfLoadTime -lt $reloadRequestedAt -or
        $freshConfLoadTime -gt $reloadAcknowledgedAt -or
        $freshBackendStartedAt -lt $reloadAcknowledgedAt -or
        $freshLoadedState.postmaster_started_at -cne $postmasterBefore.ToString('o')
    ) {
        throw 'Fresh PostgreSQL session did not inherit the acknowledged HBA generation.'
    }
    $hostAfterBoundary = Get-HeldHbaReadBoundary
    $hostAfter = [string]$hostAfterBoundary.sha256
    $mountedAfter = Get-MountedHbaSha256
    if ($hostAfter -cne $fixedHbaSha256 -or $mountedAfter -cne $fixedHbaSha256) {
        throw 'HBA bytes changed during the successor loaded-rule proof.'
    }
    return [pscustomobject]@{
        host_sha256 = $hostAfter
        host_read_lock_held = $true
        host_read_lock_path = [string]$hostAfterBoundary.path
        host_read_lock_access = [string]$hostAfterBoundary.access
        host_read_lock_share = [string]$hostAfterBoundary.share
        host_write_share_permitted = [bool]$hostAfterBoundary.write_share_permitted
        host_delete_share_permitted = [bool]$hostAfterBoundary.delete_share_permitted
        mounted_sha256 = $mountedAfter
        loaded_inventory_sha256 = $inventoryHash
        loaded_line_count = $inventory.Count
        loaded_rule_count = $ruleLines.Count
        loaded_rule_errors = $errorRules.Count
        reload_accepted = $true
        reload_acknowledged = $true
        conf_load_time_before = $confLoadBefore.ToString('o')
        reload_requested_at = $reloadRequestedAt.ToString('o')
        conf_load_time_after = $confLoadAfter.ToString('o')
        reload_acknowledged_at = $reloadAcknowledgedAt.ToString('o')
        reload_poll_count = $acknowledgedPoll
        postmaster_started_at = $postmasterBefore.ToString('o')
        proof_backend_pid = $backendPid
        proof_backend_started_at = $backendStartedAt.ToString('o')
        hba_file = $baseParts[6]
        fresh_loaded_state = $freshLoadedState
    }
}

function Get-ByteArraySha256 {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [byte[]] $Bytes
    )
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString(
            $algorithm.ComputeHash($Bytes)
        ).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $algorithm.Dispose()
    }
}

function Get-DockerEventJournalSnapshot {
    param(
        [Parameter(Mandatory = $true)][object] $Witness,
        [switch] $AllowStopped
    )
    $Witness.process.Refresh()
    if (
        -not [string]::IsNullOrEmpty([string]$Witness.collector.PumpError) -or
        [bool]$Witness.collector.OutputExceeded -or
        [int64]$Witness.collector.StderrBytes -ne 0
    ) {
        throw (
            'The Docker event witness output pump failed. ' +
            "collector_error='$([string]$Witness.collector.PumpError)'; " +
            "output_exceeded=$([bool]$Witness.collector.OutputExceeded); " +
            "stderr_bytes=$([int64]$Witness.collector.StderrBytes)."
        )
    }
    if (-not $AllowStopped -and (
        $Witness.process.HasExited -or $Witness.collector.StdoutEof -or
        $Witness.collector.StderrEof
    )) {
        throw 'The non-reconnecting Docker event witness exited before final sealing.'
    }
    if (-not (Test-Path -LiteralPath $Witness.stdout_path -PathType Leaf) -or
        -not (Test-Path -LiteralPath $Witness.stderr_path -PathType Leaf)) {
        throw 'The Docker event witness output files disappeared.'
    }
    $stdoutItem = Get-Item -LiteralPath $Witness.stdout_path -Force
    $stderrItem = Get-Item -LiteralPath $Witness.stderr_path -Force
    if (
        ($stdoutItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        ($stderrItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        $stderrItem.Length -ne [int64]$Witness.collector.StderrBytes -or
        $stderrItem.Length -ne 0 -or
        $stdoutItem.Length -gt $dockerEventJournalMaxBytes
    ) {
        throw 'The Docker event witness output is linked, errored, or unbounded.'
    }
    $stream = [IO.File]::Open(
        $Witness.stdout_path, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)
    )
    try {
        $bytes = [byte[]]::new([int]$stream.Length)
        $offset = 0
        while ($offset -lt $bytes.Length) {
            $read = $stream.Read($bytes, $offset, $bytes.Length - $offset)
            if ($read -le 0) {
                throw 'The Docker event journal could not be read atomically.'
            }
            $offset += $read
        }
    }
    finally {
        $stream.Dispose()
    }
    if ($bytes.Length -lt [int64]$Witness.prefix_length) {
        throw 'The Docker event journal was truncated.'
    }
    if ([int64]$Witness.prefix_length -gt 0) {
        $prefix = [byte[]]::new([int]$Witness.prefix_length)
        [Array]::Copy($bytes, 0, $prefix, 0, $prefix.Length)
        if ((Get-ByteArraySha256 -Bytes $prefix) -cne $Witness.prefix_sha256) {
            throw 'The accepted Docker event journal prefix was rewritten.'
        }
    }
    $completeLength = 0
    for ($index = $bytes.Length - 1; $index -ge 0; $index -= 1) {
        if ($bytes[$index] -eq 10) {
            $completeLength = $index + 1
            break
        }
    }
    $completeBytes = [byte[]]::new($completeLength)
    if ($completeLength -gt 0) {
        [Array]::Copy($bytes, 0, $completeBytes, 0, $completeLength)
    }
    $utf8 = [Text.UTF8Encoding]::new($false, $true)
    try {
        $text = $utf8.GetString($completeBytes)
    }
    catch {
        throw 'The Docker event journal is not canonical UTF-8.'
    }
    $events = [Collections.Generic.List[object]]::new()
    foreach ($line in @($text -split "`n")) {
        $candidate = $line.TrimEnd("`r")
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }
        $events.Add((ConvertFrom-StrictJsonObjectText -Json $candidate `
            -Label 'continuous Docker event'))
    }
    if ($events.Count -gt $dockerEventJournalMaxEvents) {
        throw 'The continuous Docker event journal exceeded its bounded event count.'
    }
    return [pscustomobject]@{
        byte_length = [int64]$bytes.Length
        complete_byte_length = [int64]$completeLength
        complete_sha256 = Get-ByteArraySha256 -Bytes $completeBytes
        event_count = [int]$events.Count
        events = $events.ToArray()
        has_partial_line = ($bytes.Length -ne $completeLength)
        # Any shortening below the sealed prefix and any byte/event bound
        # violation throws above, so a returned snapshot is proven untruncated.
        truncated = $false
    }
}

function Initialize-DockerEventCollectorType {
    $sourceTemplate = @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Threading;

public sealed class __STRATUS_TYPE__ : IDisposable
{
    public const string ContractIdentity = "__STRATUS_CONTRACT__";
    public sealed class BoundedResult
    {
        public int ExitCode;
        public bool TimedOut;
        public bool OutputExceeded;
        public bool ProcessStarted;
        public bool ProcessExited;
        public bool KillIssued;
        public int ProcessId;
        public string Stdout = String.Empty;
        public string Stderr = String.Empty;
        public string PumpError = String.Empty;
    }

    public sealed class HeartbeatPublisher : IDisposable
    {
        public sealed class HeartbeatEvidence
        {
            public long Sequence;
            public string WrittenAt;
            public string Sha256;
        }

        private Thread worker;
        private ManualResetEvent stopSignal = new ManualResetEvent(false);
        private Exception error;
        private int stopped;
        private object evidenceLock = new object();
        private string path;
        private string sessionId;
        private int ownerPid;
        private string ownerStartTimeUtc;
        private string leasePath;
        private string leaseSha256;
        private int intervalMilliseconds;
        private long sequence;
        private string lastWrittenAt = String.Empty;
        private string lastSha256 = String.Empty;
        private bool initialized;

        public long Sequence { get { lock (evidenceLock) { return sequence; } } }
        public string LastWrittenAt { get { lock (evidenceLock) { return lastWrittenAt; } } }
        public string LastSha256 { get { lock (evidenceLock) { return lastSha256; } } }
        public string Error
        {
            get
            {
                Exception candidate = Volatile.Read(ref error);
                return candidate == null ? String.Empty : candidate.Message;
            }
        }

        public HeartbeatEvidence Snapshot()
        {
            lock (evidenceLock)
            {
                return new HeartbeatEvidence {
                    Sequence = sequence,
                    WrittenAt = lastWrittenAt,
                    Sha256 = lastSha256
                };
            }
        }

        private static string JsonQuote(string value)
        {
            if (value == null) throw new ArgumentNullException("value");
            StringBuilder result = new StringBuilder();
            result.Append('"');
            foreach (char character in value)
            {
                switch (character)
                {
                    case '"': result.Append("\\\""); break;
                    case '\\': result.Append("\\\\"); break;
                    case '\b': result.Append("\\b"); break;
                    case '\f': result.Append("\\f"); break;
                    case '\n': result.Append("\\n"); break;
                    case '\r': result.Append("\\r"); break;
                    case '\t': result.Append("\\t"); break;
                    default:
                        if (character < 0x20)
                            result.Append("\\u").Append(
                                ((int)character).ToString("x4", CultureInfo.InvariantCulture));
                        else
                            result.Append(character);
                        break;
                }
            }
            result.Append('"');
            return result.ToString();
        }

        private static string Sha256(byte[] bytes)
        {
            using (SHA256 algorithm = SHA256.Create())
            {
                return BitConverter.ToString(algorithm.ComputeHash(bytes))
                    .Replace("-", String.Empty).ToLowerInvariant();
            }
        }

        private void Publish()
        {
            long next;
            lock (evidenceLock) { next = sequence + 1; }
            string writtenAt = DateTimeOffset.UtcNow.ToString("o", CultureInfo.InvariantCulture);
            string json = "{" +
                "\"format\":\"stratus-successor-transition-watchdog-heartbeat-v1\"," +
                "\"session_id\":" + JsonQuote(sessionId) + "," +
                "\"owner_pid\":" + ownerPid.ToString(CultureInfo.InvariantCulture) + "," +
                "\"owner_start_time_utc\":" + JsonQuote(ownerStartTimeUtc) + "," +
                "\"lease_path\":" + JsonQuote(leasePath) + "," +
                "\"lease_sha256\":" + JsonQuote(leaseSha256) + "," +
                "\"sequence\":" + next.ToString(CultureInfo.InvariantCulture) + "," +
                "\"written_at\":" + JsonQuote(writtenAt) + "}";
            byte[] bytes = new UTF8Encoding(false, true).GetBytes(json);
            string temporary = path + ".tmp-" + sessionId + "-" +
                next.ToString(CultureInfo.InvariantCulture);
            bool moved = false;
            try
            {
                using (FileStream stream = new FileStream(
                    temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None,
                    4096, FileOptions.WriteThrough))
                {
                    stream.Write(bytes, 0, bytes.Length);
                    stream.Flush(true);
                }
                if (!initialized)
                {
                    if (File.Exists(path))
                        throw new IOException("watchdog heartbeat path was pre-created");
                    File.Move(temporary, path);
                    moved = true;
                    initialized = true;
                }
                else
                {
                    IOException replaceError = null;
                    for (int attempt = 0; attempt < 200; attempt++)
                    {
                        try
                        {
                            File.Replace(temporary, path, null);
                            moved = true;
                            replaceError = null;
                            break;
                        }
                        catch (IOException candidate)
                        {
                            replaceError = candidate;
                            Thread.Sleep(10);
                        }
                    }
                    if (!moved)
                        throw new IOException(
                            "watchdog heartbeat atomic replace failed", replaceError);
                }
                string digest = Sha256(bytes);
                lock (evidenceLock)
                {
                    lastWrittenAt = writtenAt;
                    lastSha256 = digest;
                    sequence = next;
                }
            }
            finally
            {
                if (!moved && File.Exists(temporary))
                {
                    try { File.Delete(temporary); }
                    catch (Exception candidate)
                    {
                        Interlocked.CompareExchange(ref error, candidate, null);
                    }
                }
            }
        }

        private void Run()
        {
            try
            {
                while (!stopSignal.WaitOne(intervalMilliseconds)) Publish();
            }
            catch (Exception candidate)
            {
                Interlocked.CompareExchange(ref error, candidate, null);
            }
        }

        public void Start(
            string heartbeatPath, string transitionSessionId, int transitionOwnerPid,
            string transitionOwnerStartTimeUtc, string acceptedLeasePath,
            string acceptedLeaseSha256, int publishIntervalMilliseconds)
        {
            if (worker != null) throw new InvalidOperationException("heartbeat publisher already started");
            if (publishIntervalMilliseconds < 100 || publishIntervalMilliseconds > 5000)
                throw new ArgumentOutOfRangeException("publishIntervalMilliseconds");
            path = heartbeatPath;
            sessionId = transitionSessionId;
            ownerPid = transitionOwnerPid;
            ownerStartTimeUtc = transitionOwnerStartTimeUtc;
            leasePath = acceptedLeasePath;
            leaseSha256 = acceptedLeaseSha256;
            intervalMilliseconds = publishIntervalMilliseconds;
            Publish();
            worker = new Thread(Run);
            worker.IsBackground = true;
            worker.Name = "STRATUS successor watchdog heartbeat";
            worker.Start();
        }

        public void ThrowIfError()
        {
            Exception candidate = Volatile.Read(ref error);
            if (candidate != null)
                throw new InvalidOperationException(
                    "watchdog heartbeat publisher failed", candidate);
        }

        public void StopAndWait(int milliseconds)
        {
            stopSignal.Set();
            if (worker != null && !worker.Join(milliseconds))
                throw new TimeoutException("watchdog heartbeat publisher did not stop");
            Volatile.Write(ref stopped, 1);
            ThrowIfError();
        }

        public void Dispose()
        {
            List<string> failures = new List<string>();
            Exception first = null;
            if (Volatile.Read(ref stopped) == 0)
            {
                try { StopAndWait(5000); }
                catch (Exception candidate)
                {
                    first = candidate;
                    failures.Add("stop: " + candidate.Message);
                }
            }
            try { stopSignal.Dispose(); }
            catch (Exception candidate)
            {
                if (first == null) first = candidate;
                failures.Add("signal: " + candidate.Message);
            }
            if (first != null)
                throw new InvalidOperationException(
                    "watchdog heartbeat publisher cleanup failed: " +
                    String.Join(" | ", failures.ToArray()), first);
        }
    }

    private sealed class BoundedPump
    {
        private Stream input;
        private int maximumBytes;
        private MemoryStream captured = new MemoryStream();
        public bool Exceeded;
        public Exception Error;

        public BoundedPump(Stream value, int maximum)
        {
            input = value;
            maximumBytes = maximum;
        }

        public void Run()
        {
            try
            {
                byte[] buffer = new byte[4096];
                int count;
                while ((count = input.Read(buffer, 0, buffer.Length)) > 0)
                {
                    int remaining = maximumBytes - (int)captured.Length;
                    if (remaining > 0)
                        captured.Write(buffer, 0, Math.Min(remaining, count));
                    if (count > remaining) Exceeded = true;
                }
            }
            catch (Exception error) { Error = error; }
        }

        public string Text()
        {
            return new UTF8Encoding(false, true).GetString(captured.ToArray());
        }
    }

    public static BoundedResult RunBounded(
        string executable, string arguments, int timeoutMilliseconds,
        int stdoutMaximumBytes, int stderrMaximumBytes)
    {
        BoundedResult result = new BoundedResult();
        ProcessStartInfo info = new ProcessStartInfo();
        info.FileName = executable;
        info.Arguments = arguments;
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        info.RedirectStandardOutput = true;
        info.RedirectStandardError = true;
        using (Process child = new Process())
        {
            child.StartInfo = info;
            if (!child.Start()) throw new InvalidOperationException("bounded process did not start");
            result.ProcessStarted = true;
            result.ProcessId = child.Id;
            BoundedPump stdout = new BoundedPump(
                child.StandardOutput.BaseStream, stdoutMaximumBytes);
            BoundedPump stderr = new BoundedPump(
                child.StandardError.BaseStream, stderrMaximumBytes);
            Thread stdoutThread = new Thread(stdout.Run);
            Thread stderrThread = new Thread(stderr.Run);
            stdoutThread.IsBackground = true;
            stderrThread.IsBackground = true;
            stdoutThread.Start();
            stderrThread.Start();
            if (!child.WaitForExit(timeoutMilliseconds))
            {
                result.TimedOut = true;
                result.KillIssued = true;
                try { child.Kill(); }
                catch (Exception error)
                {
                    result.PumpError = "bounded child kill failed: " + error.Message;
                }
                try
                {
                    if (!child.WaitForExit(5000))
                    {
                        string detail = "bounded child remained live after kill";
                        result.PumpError = String.IsNullOrEmpty(result.PumpError) ?
                            detail : result.PumpError + " | " + detail;
                    }
                }
                catch (Exception error)
                {
                    string detail = "bounded child exit wait failed: " + error.Message;
                    result.PumpError = String.IsNullOrEmpty(result.PumpError) ?
                        detail : result.PumpError + " | " + detail;
                }
            }
            bool stdoutStopped = stdoutThread.Join(1000);
            bool stderrStopped = stderrThread.Join(1000);
            if (!stdoutStopped || !stderrStopped)
            {
                result.TimedOut = true;
                string detail = "bounded output pump did not stop";
                result.PumpError = String.IsNullOrEmpty(result.PumpError) ?
                    detail : result.PumpError + " | " + detail;
            }
            result.OutputExceeded = stdout.Exceeded || stderr.Exceeded;
            if (stdout.Error != null)
            {
                string detail = "stdout: " + stdout.Error.Message;
                result.PumpError = String.IsNullOrEmpty(result.PumpError) ?
                    detail : result.PumpError + " | " + detail;
            }
            if (stderr.Error != null)
            {
                string detail = "stderr: " + stderr.Error.Message;
                result.PumpError = String.IsNullOrEmpty(result.PumpError) ?
                    detail : result.PumpError + " | " + detail;
            }
            result.Stdout = stdout.Text();
            result.Stderr = stderr.Text();
            result.ProcessExited = child.HasExited;
            result.ExitCode = result.ProcessExited ? child.ExitCode : -1;
            if (!result.ProcessExited)
            {
                string detail = "bounded child did not exit after termination";
                result.PumpError = String.IsNullOrEmpty(result.PumpError) ?
                    detail : result.PumpError + " | " + detail;
            }
        }
        return result;
    }

    private Process process;
    private FileStream stdoutFile;
    private FileStream stderrFile;
    private Thread stdoutThread;
    private Thread stderrThread;
    private int processStarted;
    private int stdoutEof;
    private int stderrEof;
    private int stopping;
    private int stdoutExceeded;
    private int stderrExceeded;
    private Exception stdoutError;
    private Exception stderrError;
    private long stdoutBytes;
    private long stderrBytes;
    private string stdoutSha256 = String.Empty;
    private SHA256 stdoutHash = SHA256.Create();

    public Process Process { get { return process; } }
    public bool StdoutEof { get { return Volatile.Read(ref stdoutEof) != 0; } }
    public bool StderrEof { get { return Volatile.Read(ref stderrEof) != 0; } }
    public bool OutputExceeded
    {
        get
        {
            return Volatile.Read(ref stdoutExceeded) != 0 ||
                Volatile.Read(ref stderrExceeded) != 0;
        }
    }
    public string PumpError
    {
        get
        {
            Exception stdout = Volatile.Read(ref stdoutError);
            Exception stderr = Volatile.Read(ref stderrError);
            if (stdout != null) return "stdout: " + stdout.Message;
            if (stderr != null) return "stderr: " + stderr.Message;
            if (OutputExceeded) return "collector output exceeded its bound";
            return String.Empty;
        }
    }
    public long StdoutBytes { get { return Interlocked.Read(ref stdoutBytes); } }
    public long StderrBytes { get { return Interlocked.Read(ref stderrBytes); } }
    public string StdoutSha256 { get { return Volatile.Read(ref stdoutSha256); } }
    public bool OutputFilesClosed
    {
        get { return stdoutFile == null && stderrFile == null; }
    }

    public void Start(
        string executable, string arguments, string stdoutPath, string stderrPath,
        int stdoutMaximumBytes, int stderrMaximumBytes)
    {
        if (process != null) throw new InvalidOperationException("collector already started");
        if (stdoutMaximumBytes < 1 || stderrMaximumBytes < 1)
            throw new ArgumentOutOfRangeException("collector output bound");
        stdoutFile = new FileStream(
            stdoutPath, FileMode.CreateNew, FileAccess.Write,
            FileShare.Read, 4096, FileOptions.WriteThrough);
        stderrFile = new FileStream(
            stderrPath, FileMode.CreateNew, FileAccess.Write,
            FileShare.Read, 4096, FileOptions.WriteThrough);
        ProcessStartInfo info = new ProcessStartInfo();
        info.FileName = executable;
        info.Arguments = arguments;
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        info.RedirectStandardOutput = true;
        info.RedirectStandardError = true;
        process = new Process();
        process.StartInfo = info;
        if (!process.Start()) throw new InvalidOperationException("docker events did not start");
        Volatile.Write(ref processStarted, 1);
        stdoutThread = new Thread(delegate() {
            Pump(process.StandardOutput.BaseStream, stdoutFile, true, stdoutMaximumBytes);
        });
        stderrThread = new Thread(delegate() {
            Pump(process.StandardError.BaseStream, stderrFile, false, stderrMaximumBytes);
        });
        stdoutThread.IsBackground = true;
        stderrThread.IsBackground = true;
        stdoutThread.Start();
        stderrThread.Start();
    }

    private void PublishError(bool isStdout, Exception candidate)
    {
        if (isStdout)
            Interlocked.CompareExchange(ref stdoutError, candidate, null);
        else
            Interlocked.CompareExchange(ref stderrError, candidate, null);
    }

    private void CaptureBytes(
        byte[] buffer, int count, FileStream target, bool isStdout,
        int maximumBytes, ref long captured)
    {
        int accepted = 0;
        if (captured < maximumBytes)
            accepted = (int)Math.Min((long)count, maximumBytes - captured);
        if (accepted != count)
        {
            if (isStdout) Volatile.Write(ref stdoutExceeded, 1);
            else Volatile.Write(ref stderrExceeded, 1);
        }
        if (accepted > 0)
        {
            // Publish accepted bytes before capture I/O. A failed or delayed
            // write can therefore never look like a healthy, fully captured
            // prefix to the supervising thread.
            if (isStdout) Interlocked.Add(ref stdoutBytes, accepted);
            else Interlocked.Add(ref stderrBytes, accepted);
            target.Write(buffer, 0, accepted);
            target.Flush(true);
            captured += accepted;
            if (isStdout)
                stdoutHash.TransformBlock(buffer, 0, accepted, buffer, 0);
        }
    }

    private void Pump(Stream input, FileStream target, bool isStdout, int maximumBytes)
    {
        long captured = 0;
        bool pendingCarriageReturn = false;
        bool unterminatedLine = false;
        try
        {
            byte[] inputBuffer = new byte[4096];
            byte[] canonicalBuffer = new byte[4097];
            int count;
            while ((count = input.Read(inputBuffer, 0, inputBuffer.Length)) > 0)
            {
                int canonicalCount = 0;
                for (int index = 0; index < count; index++)
                {
                    byte value = inputBuffer[index];
                    if (pendingCarriageReturn)
                    {
                        canonicalBuffer[canonicalCount++] = 10;
                        pendingCarriageReturn = false;
                        unterminatedLine = false;
                        if (value == 10) continue;
                    }
                    if (value == 13)
                    {
                        pendingCarriageReturn = true;
                    }
                    else if (value == 10)
                    {
                        canonicalBuffer[canonicalCount++] = 10;
                        unterminatedLine = false;
                    }
                    else
                    {
                        canonicalBuffer[canonicalCount++] = value;
                        unterminatedLine = true;
                    }
                }
                CaptureBytes(
                    canonicalBuffer, canonicalCount, target, isStdout,
                    maximumBytes, ref captured);
            }
            if (pendingCarriageReturn || unterminatedLine)
            {
                byte[] finalNewline = new byte[] { 10 };
                CaptureBytes(
                    finalNewline, finalNewline.Length, target, isStdout,
                    maximumBytes, ref captured);
            }
        }
        catch (Exception candidate)
        {
            PublishError(isStdout, candidate);
        }
        finally
        {
            try { target.Flush(true); }
            catch (Exception candidate) { PublishError(isStdout, candidate); }
            if (isStdout)
            {
                try
                {
                    stdoutHash.TransformFinalBlock(new byte[0], 0, 0);
                    Volatile.Write(
                        ref stdoutSha256,
                        BitConverter.ToString(stdoutHash.Hash)
                            .Replace("-", String.Empty).ToLowerInvariant()
                    );
                }
                catch (Exception candidate) { PublishError(true, candidate); }
                Volatile.Write(ref stdoutEof, 1);
            }
            else Volatile.Write(ref stderrEof, 1);
        }
    }

    public void StopAndWait(int milliseconds)
    {
        Volatile.Write(ref stopping, 1);
        List<string> failures = new List<string>();
        Exception first = null;
        try
        {
            if (process != null && Volatile.Read(ref processStarted) != 0 &&
                !process.HasExited)
            {
                process.Kill();
                if (!process.WaitForExit(milliseconds))
                    throw new TimeoutException("docker events did not stop");
            }
        }
        catch (Exception candidate)
        {
            first = candidate;
            failures.Add("process stop: " + candidate.Message);
        }
        try
        {
            if (stdoutThread != null && !stdoutThread.Join(milliseconds))
                throw new TimeoutException("docker events stdout pump did not stop");
        }
        catch (Exception candidate)
        {
            if (first == null) first = candidate;
            failures.Add("stdout join: " + candidate.Message);
        }
        try
        {
            if (stderrThread != null && !stderrThread.Join(milliseconds))
                throw new TimeoutException("docker events stderr pump did not stop");
        }
        catch (Exception candidate)
        {
            if (first == null) first = candidate;
            failures.Add("stderr join: " + candidate.Message);
        }
        try { if (stdoutFile != null) stdoutFile.Flush(true); }
        catch (Exception candidate)
        {
            if (first == null) first = candidate;
            failures.Add("stdout flush: " + candidate.Message);
        }
        try { if (stderrFile != null) stderrFile.Flush(true); }
        catch (Exception candidate)
        {
            if (first == null) first = candidate;
            failures.Add("stderr flush: " + candidate.Message);
        }
        if (first != null)
            throw new InvalidOperationException(
                "collector stop failed: " +
                String.Join(" | ", failures.ToArray()), first);
    }

    public void CloseOutputFiles()
    {
        if ((stdoutThread != null && stdoutThread.IsAlive) ||
            (stderrThread != null && stderrThread.IsAlive))
            throw new InvalidOperationException(
                "cannot close process output files while a pump is running");
        List<string> failures = new List<string>();
        Exception first = null;
        try
        {
            if (stdoutFile != null)
            {
                stdoutFile.Dispose();
                stdoutFile = null;
            }
        }
        catch (Exception candidate)
        {
            first = candidate;
            failures.Add("stdout: " + candidate.Message);
        }
        try
        {
            if (stderrFile != null)
            {
                stderrFile.Dispose();
                stderrFile = null;
            }
        }
        catch (Exception candidate)
        {
            if (first == null) first = candidate;
            failures.Add("stderr: " + candidate.Message);
        }
        if (first != null)
            throw new InvalidOperationException(
                "collector output close failed: " +
                String.Join(" | ", failures.ToArray()), first);
    }

    public void Dispose()
    {
        List<string> failures = new List<string>();
        Exception first = null;
        try { StopAndWait(5000); }
        catch (Exception candidate)
        {
            first = candidate;
            failures.Add("stop: " + candidate.Message);
        }
        try { CloseOutputFiles(); }
        catch (Exception candidate)
        {
            if (first == null) first = candidate;
            failures.Add("output files: " + candidate.Message);
        }
        try
        {
            if (process != null) { process.Dispose(); process = null; }
        }
        catch (Exception candidate)
        {
            if (first == null) first = candidate;
            failures.Add("process: " + candidate.Message);
        }
        try
        {
            if (stdoutHash != null) { stdoutHash.Dispose(); stdoutHash = null; }
        }
        catch (Exception candidate)
        {
            if (first == null) first = candidate;
            failures.Add("hash: " + candidate.Message);
        }
        if (first != null)
            throw new InvalidOperationException(
                "collector cleanup failed: " + String.Join(" | ", failures.ToArray()),
                first);
    }
}
'@
    $contractSha256 = Get-Sha256Text -Value $sourceTemplate
    $typeName = 'StratusDockerEventCollector_' +
        $ExpectedRevision.Substring(0, 12) + '_' + $contractSha256.Substring(0, 12)
    $contractIdentity = "$ExpectedRevision`:$contractSha256"
    $source = $sourceTemplate.Replace('__STRATUS_TYPE__', $typeName)
    $source = $source.Replace('__STRATUS_CONTRACT__', $contractIdentity)
    $sourceSha256 = Get-Sha256Text -Value $source
    $type = $typeName -as [type]
    if ($null -eq $type) {
        $compiledTypes = @(Microsoft.PowerShell.Utility\Add-Type `
            -TypeDefinition $source -PassThru -ErrorAction Stop)
        $matches = @($compiledTypes | Where-Object { $_.FullName -ceq $typeName })
        if ($matches.Count -ne 1) {
            throw 'The Docker event collector did not compile to one exact revision-bound type.'
        }
        $type = $matches[0]
    }
    $field = $type.GetField(
        'ContractIdentity',
        [Reflection.BindingFlags]::Public -bor [Reflection.BindingFlags]::Static
    )
    if (
        $null -eq $field -or
        -not $field.IsLiteral -or
        [string]$field.GetRawConstantValue() -cne $contractIdentity
    ) {
        throw 'An existing Docker event collector type has the wrong source identity.'
    }
    return [pscustomobject]@{
        type = $type
        type_name = $typeName
        contract_identity = $contractIdentity
        template_sha256 = $contractSha256
        source_sha256 = $sourceSha256
        assembly_full_name_sha256 = Get-Sha256Text -Value (
            [string]$type.Assembly.FullName + "`n"
        )
        assembly_is_dynamic = [bool]$type.Assembly.IsDynamic
        module_version_id = [string]$type.Module.ModuleVersionId
    }
}

function Open-TransitionWatchdogProgramBoundary {
    param([Parameter(Mandatory = $true)][string] $AbsoluteRoot)

    $watchdogPath = Join-Path $AbsoluteRoot `
        'scripts\Invoke-StratusSuccessorTransitionWatchdog.ps1'
    $watchdogItem = Assert-RegularFile -LiteralPath $watchdogPath
    $watchdogHash = Get-DirectFileSha256 -LiteralPath $watchdogItem.FullName
    if ($watchdogHash -cne $approvedWatchdogSha256) {
        throw 'The successor transition watchdog program is not the approved reviewed bytes.'
    }
    $ownerProcess = [Diagnostics.Process]::GetCurrentProcess()
    try {
        $hostPath = [IO.Path]::GetFullPath($ownerProcess.MainModule.FileName)
        $ownerStartedAt = [DateTimeOffset]::new(
            $ownerProcess.StartTime.ToUniversalTime(), [TimeSpan]::Zero
        )
    }
    finally {
        $ownerProcess.Dispose()
    }
    $hostItem = Assert-RegularFile -LiteralPath $hostPath
    $hostHash = Get-DirectFileSha256 -LiteralPath $hostItem.FullName
    $watchdogLock = $null
    $hostLock = $null
    try {
        $watchdogLock = [IO.FileStream]::new(
            $watchdogItem.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read,
            [IO.FileShare]::Read
        )
        $hostLock = [IO.FileStream]::new(
            $hostItem.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read,
            [IO.FileShare]::Read
        )
        if (
            $watchdogLock.Length -ne $watchdogItem.Length -or
            $hostLock.Length -ne $hostItem.Length -or
            (Get-DirectFileSha256 -LiteralPath $watchdogItem.FullName) -cne $watchdogHash -or
            (Get-DirectFileSha256 -LiteralPath $hostItem.FullName) -cne $hostHash
        ) {
            throw 'The watchdog program or its PowerShell host changed while being locked.'
        }
        $script:watchdogScriptReadLock = $watchdogLock
        $script:watchdogHostReadLock = $hostLock
        return [pscustomobject]@{
            script_path = [IO.Path]::GetFullPath($watchdogItem.FullName)
            script_sha256 = $watchdogHash
            script_bytes = [int64]$watchdogItem.Length
            host_path = [IO.Path]::GetFullPath($hostItem.FullName)
            host_sha256 = $hostHash
            host_bytes = [int64]$hostItem.Length
            owner_pid = [int]$PID
            owner_start_time_utc = $ownerStartedAt.ToString('o')
            read_locks_held = $true
        }
    }
    catch {
        if ($null -ne $hostLock) { $hostLock.Dispose() }
        if ($null -ne $watchdogLock) { $watchdogLock.Dispose() }
        throw
    }
}

function Assert-TransitionWatchdogProgramUnchanged {
    param([Parameter(Mandatory = $true)][object] $Program)
    if (
        $null -eq $script:watchdogScriptReadLock -or
        $null -eq $script:watchdogHostReadLock -or
        $script:watchdogScriptReadLock.Length -ne $Program.script_bytes -or
        $script:watchdogHostReadLock.Length -ne $Program.host_bytes -or
        (Get-DirectFileSha256 -LiteralPath $Program.script_path) -cne $Program.script_sha256 -or
        (Get-DirectFileSha256 -LiteralPath $Program.host_path) -cne $Program.host_sha256
    ) {
        throw 'The read-locked watchdog program boundary changed during transition.'
    }
    return [pscustomobject]@{
        script_sha256 = [string]$Program.script_sha256
        host_sha256 = [string]$Program.host_sha256
        read_locks_held = $true
    }
}

function Start-TransitionWatchdogProcess {
    param(
        [Parameter(Mandatory = $true)][object] $Program,
        [Parameter(Mandatory = $true)][object] $CollectorType,
        [Parameter(Mandatory = $true)][string] $SessionId,
        [Parameter(Mandatory = $true)][DateTimeOffset] $HardDeadline,
        [Parameter(Mandatory = $true)][string] $LeasePath,
        [Parameter(Mandatory = $true)][string] $ReceiptPath,
        [Parameter(Mandatory = $true)][string] $HeartbeatPath,
        [Parameter(Mandatory = $true)][string] $CommitPath,
        [Parameter(Mandatory = $true)][string] $LivePath,
        [Parameter(Mandatory = $true)][string] $PreStartPath,
        [Parameter(Mandatory = $true)][string] $PreStartSha256,
        [Parameter(Mandatory = $true)][string] $StartFencePath,
        [Parameter(Mandatory = $true)][string] $StartFenceSha256,
        [Parameter(Mandatory = $true)][string] $StartRevokePath,
        [Parameter(Mandatory = $true)][string] $StartAttemptPath,
        [Parameter(Mandatory = $true)][string] $StartCompletionPath
    )
    $tempRoot = Join-Path ([IO.Path]::GetTempPath()) (
        'stratus-successor-watchdog-' + $SessionId
    )
    [void](New-Item -ItemType Directory -Path $tempRoot)
    $stdoutPath = Join-Path $tempRoot 'watchdog.stdout.json'
    $stderrPath = Join-Path $tempRoot 'watchdog.stderr.txt'
    $argumentValues = @(
        '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', [string]$Program.script_path,
        '-Root', $absoluteRoot,
        '-SessionId', $SessionId,
        '-OwnerPid', [string]$Program.owner_pid,
        '-OwnerStartTimeUtc', [string]$Program.owner_start_time_utc,
        '-DeadlineUtc', $HardDeadline.ToString('o'),
        '-HeartbeatTimeoutSeconds', [string]$watchdogHeartbeatTimeoutSeconds,
        '-LeasePath', $LeasePath,
        '-ReceiptPath', $ReceiptPath,
        '-HeartbeatPath', $HeartbeatPath,
        '-CommitPath', $CommitPath,
        '-LiveAcceptancePath', $LivePath,
        '-PreStartAttestationPath', $PreStartPath,
        '-ExpectedPreStartSha256', $PreStartSha256,
        '-StartFencePath', $StartFencePath,
        '-ExpectedStartFenceSha256', $StartFenceSha256,
        '-StartRevokePath', $StartRevokePath,
        '-StartAttemptPath', $StartAttemptPath,
        '-StartCompletionPath', $StartCompletionPath,
        '-PostgresContainer', $productionPostgresId
    )
    $arguments = (@($argumentValues | ForEach-Object {
        ConvertTo-WindowsCommandLineArgument -Value ([string]$_)
    }) -join ' ')
    $collector = $null
    try {
        $collector = [Activator]::CreateInstance($CollectorType.type)
        $requestedAt = [DateTimeOffset]::UtcNow
        $collector.Start(
            [string]$Program.host_path, $arguments, $stdoutPath, $stderrPath,
            [int]262144, [int]65536
        )
        $process = $collector.Process
        $process.Refresh()
        if ($process.HasExited) {
            throw 'The successor transition watchdog exited during launch.'
        }
        $processStartedAt = [DateTimeOffset]::new(
            $process.StartTime.ToUniversalTime(), [TimeSpan]::Zero
        )
        return [pscustomobject]@{
            collector = $collector
            process = $process
            process_id = [int]$process.Id
            process_started_at = $processStartedAt.ToString('o')
            requested_at = $requestedAt.ToString('o')
            stdout_path = $stdoutPath
            stderr_path = $stderrPath
            temp_root = $tempRoot
            argument_count = [int]$argumentValues.Count
            command_sha256 = Get-Sha256Text -Value ($arguments + "`n")
            intentionally_stopped = $false
        }
    }
    catch {
        $launchFailure = $_
        $cleanupFailures = [Collections.Generic.List[string]]::new()
        if ($null -ne $collector) {
            try { $collector.Dispose() }
            catch {
                $cleanupFailures.Add(
                    "watchdog collector cleanup failed: $($_.Exception.Message)"
                )
            }
        }
        if (Test-Path -LiteralPath $tempRoot) {
            try {
                Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction Stop
            }
            catch {
                $cleanupFailures.Add(
                    "watchdog capture cleanup failed: $($_.Exception.Message)"
                )
            }
        }
        if ($cleanupFailures.Count -gt 0) {
            throw [InvalidOperationException]::new(
                "Watchdog launch failed. Original: $($launchFailure.Exception.Message) Cleanup: $($cleanupFailures -join ' | ')",
                $launchFailure.Exception
            )
        }
        throw $launchFailure
    }
}

function Assert-TransitionWatchdogProcessConnected {
    param([Parameter(Mandatory = $true)][object] $Watchdog)
    $Watchdog.process.Refresh()
    if (
        $Watchdog.process.HasExited -or $Watchdog.collector.StdoutEof -or
        $Watchdog.collector.StderrEof -or
        $Watchdog.collector.OutputExceeded -or
        [int64]$Watchdog.collector.StderrBytes -ne 0 -or
        -not [string]::IsNullOrEmpty([string]$Watchdog.collector.PumpError)
    ) {
        throw 'The successor transition watchdog lost its continuous process boundary.'
    }
}

function Open-GateProgramBoundary {
    param([Parameter(Mandatory = $true)][string] $AbsoluteRoot)

    $gatePath = Join-Path $AbsoluteRoot `
        'scripts\Assert-StratusPostMigrationSuccessorGate.ps1'
    $gateItem = Assert-RegularFile -LiteralPath $gatePath
    $gateHash = Get-DirectFileSha256 -LiteralPath $gateItem.FullName
    $gateLock = $null
    try {
        $gateLock = [IO.FileStream]::new(
            $gateItem.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read,
            [IO.FileShare]::Read
        )
        if ($gateLock.Length -ne $gateItem.Length -or
            (Get-DirectFileSha256 -LiteralPath $gateItem.FullName) -cne $gateHash) {
            throw 'The successor gate program changed while being read-locked.'
        }
        $script:gateScriptReadLock = $gateLock
        return [pscustomobject]@{
            script_path = [IO.Path]::GetFullPath($gateItem.FullName)
            script_sha256 = $gateHash
            script_bytes = [int64]$gateItem.Length
            read_lock_held = $true
        }
    }
    catch {
        if ($null -ne $gateLock) { $gateLock.Dispose() }
        throw
    }
}

function Assert-GateProgramUnchanged {
    param([Parameter(Mandatory = $true)][object] $Program)
    if (
        $null -eq $script:gateScriptReadLock -or
        $script:gateScriptReadLock.Length -ne $Program.script_bytes -or
        (Get-DirectFileSha256 -LiteralPath $Program.script_path) -cne
            $Program.script_sha256
    ) {
        throw 'The successor gate program changed while read-locked.'
    }
    return $Program
}

function Stop-TransitionWatchdogForParentTakeover {
    param(
        [Parameter(Mandatory = $true)][object] $Watchdog,
        [ValidateRange(1, 60000)][int] $MaximumWaitMilliseconds = 10000,
        [ValidateRange(1, 2000)][int] $WaitSliceMilliseconds = 200
    )

    # Keep using the exact Process handle captured when the watchdog was
    # created.  A PID reacquisition here would permit PID-reuse ambiguity.
    $stopWatch = [Diagnostics.Stopwatch]::StartNew()
    $killAttemptCount = 0
    $lastStopError = $null
    $hasExited = $false
    do {
        try {
            $Watchdog.process.Refresh()
            $hasExited = [bool]$Watchdog.process.HasExited
        }
        catch {
            $lastStopError = $_.Exception.Message
            $hasExited = $false
        }
        if ($hasExited) { break }

        $killAttemptCount += 1
        try { $Watchdog.process.Kill() }
        catch { $lastStopError = $_.Exception.Message }

        $remainingMilliseconds = [int][Math]::Max(
            0,
            [Math]::Ceiling(
                $MaximumWaitMilliseconds - $stopWatch.Elapsed.TotalMilliseconds
            )
        )
        if ($remainingMilliseconds -gt 0) {
            try {
                [void]$Watchdog.process.WaitForExit(
                    [Math]::Min($WaitSliceMilliseconds, $remainingMilliseconds)
                )
            }
            catch { $lastStopError = $_.Exception.Message }
        }
    } while ($stopWatch.Elapsed.TotalMilliseconds -lt $MaximumWaitMilliseconds)
    $stopWatch.Stop()

    try {
        $Watchdog.process.Refresh()
        $hasExited = [bool]$Watchdog.process.HasExited
    }
    catch {
        $lastStopError = $_.Exception.Message
        $hasExited = $false
    }
    if (-not $hasExited) {
        $detail = if ([string]::IsNullOrWhiteSpace($lastStopError)) {
            'no process error was exposed'
        }
        else { $lastStopError }
        throw [InvalidOperationException]::new(
            "The exact successor transition watchdog could not be stopped within the bounded parent-takeover window; takeover and authorization remain prohibited. attempts=$killAttemptCount detail=$detail"
        )
    }

    $Watchdog.collector.StopAndWait(5000)
    $Watchdog.collector.CloseOutputFiles()
    if (-not [bool]$Watchdog.collector.OutputFilesClosed) {
        throw 'The stopped successor transition watchdog output handles remained open during parent takeover.'
    }
    $Watchdog.intentionally_stopped = $true
}

function Start-WatchdogHeartbeatPublisher {
    param(
        [Parameter(Mandatory = $true)][object] $CollectorType,
        [Parameter(Mandatory = $true)][string] $HeartbeatPath,
        [Parameter(Mandatory = $true)][string] $SessionId,
        [Parameter(Mandatory = $true)][object] $Program,
        [Parameter(Mandatory = $true)][string] $LeasePath,
        [Parameter(Mandatory = $true)][string] $LeaseSha256
    )
    $publisherType = $CollectorType.type.GetNestedType(
        'HeartbeatPublisher', [Reflection.BindingFlags]::Public
    )
    if ($null -eq $publisherType) {
        throw 'The revision-bound collector lacks its heartbeat publisher type.'
    }
    $publisher = [Activator]::CreateInstance($publisherType)
    try {
        $publisher.Start(
            $HeartbeatPath, $SessionId, [int]$Program.owner_pid,
            [string]$Program.owner_start_time_utc, $LeasePath, $LeaseSha256,
            [int]1000
        )
        $publisher.ThrowIfError()
        $evidence = $publisher.Snapshot()
        if (
            $evidence.Sequence -lt 1 -or
            [string]$evidence.Sha256 -notmatch '^[0-9a-f]{64}$' -or
            [string]::IsNullOrWhiteSpace([string]$evidence.WrittenAt)
        ) {
            throw 'The first lease-bound watchdog heartbeat was not durably published.'
        }
        return $publisher
    }
    catch {
        $publisher.Dispose()
        throw
    }
}

function Assert-WatchdogHeartbeatPublisher {
    param([Parameter(Mandatory = $true)][object] $Publisher)
    $Publisher.ThrowIfError()
    $evidence = $Publisher.Snapshot()
    if (
        $evidence.Sequence -lt 1 -or
        [string]$evidence.Sha256 -notmatch '^[0-9a-f]{64}$' -or
        [string]::IsNullOrWhiteSpace([string]$evidence.WrittenAt)
    ) {
        throw 'The lease-bound watchdog heartbeat publisher is incomplete.'
    }
    return [pscustomobject]@{
        sequence = [int64]$evidence.Sequence
        written_at = [string]$evidence.WrittenAt
        sha256 = [string]$evidence.Sha256
    }
}

function Assert-WatchdogHeartbeatDocument {
    param(
        [Parameter(Mandatory = $true)][object] $Document,
        [Parameter(Mandatory = $true)][object] $Evidence,
        [Parameter(Mandatory = $true)][string] $SessionId,
        [Parameter(Mandatory = $true)][object] $Program,
        [Parameter(Mandatory = $true)][object] $LeaseProof,
        [Parameter(Mandatory = $true)][string] $LeasePath
    )
    $heartbeat = $Document.value
    Assert-ExactProperties -Value $heartbeat -Label 'watchdog owner heartbeat' -Expected @(
        'format', 'session_id', 'owner_pid', 'owner_start_time_utc',
        'lease_path', 'lease_sha256', 'sequence', 'written_at'
    )
    foreach ($name in @(
        'format', 'session_id', 'owner_start_time_utc', 'lease_path',
        'lease_sha256', 'written_at'
    )) {
        Assert-NativeJsonString -Value $heartbeat.$name -Label "watchdog heartbeat $name"
    }
    foreach ($name in @('owner_pid', 'sequence')) {
        Assert-NativeJsonInteger -Value $heartbeat.$name -Label "watchdog heartbeat $name"
    }
    $writtenAt = ConvertTo-CanonicalUtcTime -Value $heartbeat.written_at `
        -Label 'watchdog heartbeat written_at'
    $armedAt = ConvertTo-CanonicalUtcTime -Value $LeaseProof.armed_at `
        -Label 'watchdog lease armed_at'
    if (
        $heartbeat.format -cne 'stratus-successor-transition-watchdog-heartbeat-v1' -or
        $heartbeat.session_id -cne $SessionId -or
        $heartbeat.owner_pid -ne $Program.owner_pid -or
        $heartbeat.owner_start_time_utc -cne $Program.owner_start_time_utc -or
        -not (Test-ExactPathEqual -Left $heartbeat.lease_path -Right $LeasePath) -or
        $heartbeat.lease_sha256 -cne $LeaseProof.sha256 -or
        $heartbeat.sequence -ne $Evidence.sequence -or
        $heartbeat.written_at -cne $Evidence.written_at -or
        $Document.sha256 -cne $Evidence.sha256 -or
        $writtenAt -lt $armedAt.AddSeconds(-$clockSkewSeconds) -or
        $writtenAt -gt [DateTimeOffset]::UtcNow.AddSeconds($clockSkewSeconds)
    ) {
        throw 'The first watchdog heartbeat is not exact, durable, and lease-bound.'
    }
    return $heartbeat
}

function Assert-TransitionWatchdogLease {
    param(
        [Parameter(Mandatory = $true)][object] $Document,
        [Parameter(Mandatory = $true)][object] $Watchdog,
        [Parameter(Mandatory = $true)][object] $Program,
        [Parameter(Mandatory = $true)][string] $SessionId,
        [Parameter(Mandatory = $true)][DateTimeOffset] $HardDeadline,
        [Parameter(Mandatory = $true)][string] $LeasePath,
        [Parameter(Mandatory = $true)][string] $ReceiptPath,
        [Parameter(Mandatory = $true)][string] $HeartbeatPath,
        [Parameter(Mandatory = $true)][string] $CommitPath,
        [Parameter(Mandatory = $true)][string] $LivePath,
        [Parameter(Mandatory = $true)][string] $PreStartPath,
        [Parameter(Mandatory = $true)][string] $PreStartSha256,
        [Parameter(Mandatory = $true)][string] $PreStartPayloadSha256,
        [Parameter(Mandatory = $true)][string] $StartFencePath,
        [Parameter(Mandatory = $true)][string] $StartFenceSha256,
        [Parameter(Mandatory = $true)][string] $StartRevokePath,
        [Parameter(Mandatory = $true)][string] $StartAttemptPath,
        [Parameter(Mandatory = $true)][string] $StartCompletionPath,
        [Parameter(Mandatory = $true)][object] $PreStartPostgres
    )
    $lease = $Document.value
    Assert-ExactProperties -Value $lease -Label 'transition watchdog lease' -Expected @(
        'format', 'armed', 'armed_at', 'session_id', 'watchdog', 'owner',
        'deadline', 'artifacts', 'docker_engine', 'postgres', 'containment'
    )
    Assert-NativeJsonString -Value $lease.format -Label 'watchdog lease format'
    Assert-NativeJsonBoolean -Value $lease.armed -Label 'watchdog lease armed'
    Assert-NativeJsonString -Value $lease.armed_at -Label 'watchdog lease armed_at'
    Assert-NativeJsonString -Value $lease.session_id -Label 'watchdog lease session_id'
    $armedAt = ConvertTo-CanonicalUtcTime -Value $lease.armed_at `
        -Label 'watchdog lease armed_at'
    $requestedAt = ConvertTo-CanonicalUtcTime -Value $Watchdog.requested_at `
        -Label 'watchdog launch requested_at'
    if (
        $lease.format -cne 'stratus-successor-transition-watchdog-lease-v3' -or
        -not $lease.armed -or $lease.session_id -cne $SessionId -or
        $armedAt -lt $requestedAt.AddSeconds(-$clockSkewSeconds) -or
        $armedAt -ge $HardDeadline -or
        $armedAt -gt [DateTimeOffset]::UtcNow.AddSeconds($clockSkewSeconds)
    ) {
        throw 'The watchdog lease is not a fresh accepted lease for this transition.'
    }

    foreach ($name in @('watchdog', 'owner', 'deadline', 'artifacts', 'docker_engine', 'postgres', 'containment')) {
        Assert-NativeJsonObject -Value $lease.$name -Label "watchdog lease $name"
    }
    Assert-ExactProperties -Value $lease.watchdog -Label 'watchdog lease process' `
        -Expected @('process_id', 'process_started_at')
    Assert-NativeJsonInteger -Value $lease.watchdog.process_id `
        -Label 'watchdog lease process_id'
    Assert-NativeJsonString -Value $lease.watchdog.process_started_at `
        -Label 'watchdog lease process_started_at'
    [void](ConvertTo-CanonicalUtcTime -Value $lease.watchdog.process_started_at `
        -Label 'watchdog lease process_started_at')
    if (
        $lease.watchdog.process_id -ne $Watchdog.process_id -or
        $lease.watchdog.process_started_at -cne $Watchdog.process_started_at
    ) {
        throw 'The watchdog lease was not issued by the launched exact process.'
    }

    Assert-ExactProperties -Value $lease.owner -Label 'watchdog lease owner' `
        -Expected @('process_id', 'process_started_at')
    Assert-NativeJsonInteger -Value $lease.owner.process_id -Label 'watchdog lease owner pid'
    Assert-NativeJsonString -Value $lease.owner.process_started_at `
        -Label 'watchdog lease owner start'
    if (
        $lease.owner.process_id -ne $Program.owner_pid -or
        $lease.owner.process_started_at -cne $Program.owner_start_time_utc
    ) {
        throw 'The watchdog lease is bound to another transition owner.'
    }

    Assert-ExactProperties -Value $lease.deadline -Label 'watchdog lease deadline' `
        -Expected @(
            'hard_deadline_utc', 'heartbeat_timeout_seconds', 'maximum_lease_seconds'
        )
    Assert-NativeJsonString -Value $lease.deadline.hard_deadline_utc `
        -Label 'watchdog lease hard deadline'
    foreach ($name in @('heartbeat_timeout_seconds', 'maximum_lease_seconds')) {
        Assert-NativeJsonInteger -Value $lease.deadline.$name `
            -Label "watchdog lease deadline $name"
    }
    if (
        $lease.deadline.hard_deadline_utc -cne $HardDeadline.ToString('o') -or
        $lease.deadline.heartbeat_timeout_seconds -ne $watchdogHeartbeatTimeoutSeconds -or
        $lease.deadline.maximum_lease_seconds -ne 600
    ) {
        throw 'The watchdog lease deadline contract is not exact.'
    }

    Assert-ExactProperties -Value $lease.artifacts -Label 'watchdog lease artifacts' `
        -Expected @(
            'lease_path', 'receipt_path', 'heartbeat_path', 'commit_path',
            'live_path', 'prestart_path', 'prestart_sha256', 'prestart_payload_sha256',
            'start_fence_path', 'start_fence_sha256', 'start_revoke_path',
            'start_attempt_path', 'start_completion_path'
        )
    foreach ($name in @(
        'lease_path', 'receipt_path', 'heartbeat_path', 'commit_path',
        'live_path', 'prestart_path', 'prestart_sha256', 'prestart_payload_sha256',
        'start_fence_path', 'start_fence_sha256', 'start_revoke_path',
        'start_attempt_path', 'start_completion_path'
    )) {
        Assert-NativeJsonString -Value $lease.artifacts.$name `
            -Label "watchdog lease artifact $name"
    }
    $pathPairs = @(
        @([string]$lease.artifacts.lease_path, $LeasePath),
        @([string]$lease.artifacts.receipt_path, $ReceiptPath),
        @([string]$lease.artifacts.heartbeat_path, $HeartbeatPath),
        @([string]$lease.artifacts.commit_path, $CommitPath),
        @([string]$lease.artifacts.live_path, $LivePath),
        @([string]$lease.artifacts.prestart_path, $PreStartPath),
        @([string]$lease.artifacts.start_fence_path, $StartFencePath),
        @([string]$lease.artifacts.start_revoke_path, $StartRevokePath),
        @([string]$lease.artifacts.start_attempt_path, $StartAttemptPath),
        @([string]$lease.artifacts.start_completion_path, $StartCompletionPath)
    )
    foreach ($pair in $pathPairs) {
        if (-not (Test-ExactPathEqual -Left $pair[0] -Right $pair[1])) {
            throw 'The watchdog lease artifact paths do not match the handoff.'
        }
    }
    if (
        $lease.artifacts.prestart_sha256 -cne $PreStartSha256 -or
        $lease.artifacts.prestart_payload_sha256 -cne $PreStartPayloadSha256 -or
        $lease.artifacts.start_fence_sha256 -cne $StartFenceSha256
    ) {
        throw 'The watchdog lease does not hash-bind the exact PreStart and start fence artifacts.'
    }

    Assert-ExactProperties -Value $lease.docker_engine -Label 'watchdog lease engine' `
        -Expected @(
            'docker_executable_path', 'docker_executable_sha256',
            'docker_executable_bytes', 'context', 'endpoint', 'skip_tls_verify',
            'engine_id', 'server_version', 'engine_name', 'docker_root_dir',
            'contract_sha256'
        )
    foreach ($name in @(
        'docker_executable_path', 'docker_executable_sha256', 'context', 'endpoint',
        'engine_id', 'server_version', 'engine_name', 'docker_root_dir', 'contract_sha256'
    )) {
        Assert-NativeJsonString -Value $lease.docker_engine.$name `
            -Label "watchdog lease engine $name"
    }
    Assert-NativeJsonInteger -Value $lease.docker_engine.docker_executable_bytes `
        -Label 'watchdog lease engine docker bytes'
    Assert-NativeJsonBoolean -Value $lease.docker_engine.skip_tls_verify `
        -Label 'watchdog lease engine SkipTLSVerify'
    $watchdogEngineContract = @(
        [string]$lease.docker_engine.docker_executable_path,
        [string]$lease.docker_engine.docker_executable_sha256,
        [string]$lease.docker_engine.docker_executable_bytes,
        [string]$lease.docker_engine.context,
        [string]$lease.docker_engine.endpoint,
        'false',
        [string]$lease.docker_engine.engine_id,
        [string]$lease.docker_engine.server_version,
        [string]$lease.docker_engine.engine_name,
        [string]$lease.docker_engine.docker_root_dir
    ) -join "`0"
    if (
        -not (Test-ExactPathEqual -Left $lease.docker_engine.docker_executable_path `
            -Right $script:dockerExecutable) -or
        $lease.docker_engine.docker_executable_sha256 -cne $approvedDockerExecutableSha256 -or
        $lease.docker_engine.docker_executable_bytes -ne $initialDockerEngine.docker_executable_bytes -or
        $lease.docker_engine.context -cne $approvedDockerContext -or
        $lease.docker_engine.endpoint -cne $approvedDockerEndpoint -or
        $lease.docker_engine.skip_tls_verify -or
        $lease.docker_engine.engine_id -cne $approvedDockerEngineId -or
        $lease.docker_engine.server_version -cne $approvedDockerServerVersion -or
        $lease.docker_engine.engine_name -cne $approvedDockerEngineName -or
        $lease.docker_engine.docker_root_dir -cne $approvedDockerRootDirectory -or
        $lease.docker_engine.contract_sha256 -cne (
            Get-Sha256Text -Value ($watchdogEngineContract + "`n")
        )
    ) {
        throw 'The watchdog lease Docker engine boundary is not exact.'
    }

    Assert-ExactProperties -Value $lease.postgres -Label 'watchdog lease PostgreSQL' `
        -Expected @(
            'container_id', 'image_id', 'config_image', 'restart_count',
            'restart_policy_before', 'restart_policy_armed', 'state',
            'started_at', 'finished_at'
        )
    foreach ($name in @(
        'container_id', 'image_id', 'config_image', 'restart_policy_before',
        'restart_policy_armed', 'state', 'started_at', 'finished_at'
    )) {
        Assert-NativeJsonString -Value $lease.postgres.$name `
            -Label "watchdog lease PostgreSQL $name"
    }
    Assert-NativeJsonInteger -Value $lease.postgres.restart_count `
        -Label 'watchdog lease PostgreSQL restart_count'
    if (
        $lease.postgres.container_id -cne $PreStartPostgres.container_id -or
        $lease.postgres.image_id -cne $PreStartPostgres.image_id -or
        $lease.postgres.config_image -cne $PreStartPostgres.config_image -or
        $lease.postgres.restart_count -ne $PreStartPostgres.restart_count -or
        $lease.postgres.restart_policy_before -cne 'unless-stopped' -or
        $lease.postgres.restart_policy_armed -cne 'no' -or
        $lease.postgres.state -cne 'exited' -or
        $lease.postgres.started_at -cne $PreStartPostgres.started_at -or
        $lease.postgres.finished_at -cne $PreStartPostgres.finished_at
    ) {
        throw 'The watchdog lease PostgreSQL identity is not the accepted PreStart identity.'
    }

    Assert-ExactProperties -Value $lease.containment -Label 'watchdog lease containment' `
        -Expected @(
            'stop_timeout_seconds', 'stop_wall_clock_timeout_seconds',
            'exact_container_only', 'ambiguous_start_requires_causal_resolution'
        )
    foreach ($name in @('stop_timeout_seconds', 'stop_wall_clock_timeout_seconds')) {
        Assert-NativeJsonInteger -Value $lease.containment.$name `
            -Label "watchdog lease containment $name"
    }
    foreach ($name in @(
        'exact_container_only', 'ambiguous_start_requires_causal_resolution'
    )) {
        Assert-NativeJsonBoolean -Value $lease.containment.$name `
            -Label "watchdog lease containment $name"
    }
    if (
        $lease.containment.stop_timeout_seconds -ne $postgresRollbackTimeoutSeconds -or
        $lease.containment.stop_wall_clock_timeout_seconds -ne 25 -or
        -not $lease.containment.exact_container_only -or
        -not $lease.containment.ambiguous_start_requires_causal_resolution
    ) {
        throw 'The watchdog lease containment boundary is not exact.'
    }
    return [pscustomobject]@{
        document = $Document
        lease = $lease
        armed_at = $armedAt.ToString('o')
        sha256 = [string]$Document.sha256
        docker_engine = $lease.docker_engine
    }
}

function Wait-TransitionWatchdogLease {
    param(
        [Parameter(Mandatory = $true)][object] $Watchdog,
        [Parameter(Mandatory = $true)][object] $Program,
        [Parameter(Mandatory = $true)][string] $SessionId,
        [Parameter(Mandatory = $true)][DateTimeOffset] $HardDeadline,
        [Parameter(Mandatory = $true)][string] $LeasePath,
        [Parameter(Mandatory = $true)][string] $ReceiptPath,
        [Parameter(Mandatory = $true)][string] $HeartbeatPath,
        [Parameter(Mandatory = $true)][string] $CommitPath,
        [Parameter(Mandatory = $true)][string] $LivePath,
        [Parameter(Mandatory = $true)][string] $PreStartPath,
        [Parameter(Mandatory = $true)][string] $PreStartSha256,
        [Parameter(Mandatory = $true)][string] $PreStartPayloadSha256,
        [Parameter(Mandatory = $true)][string] $StartFencePath,
        [Parameter(Mandatory = $true)][string] $StartFenceSha256,
        [Parameter(Mandatory = $true)][string] $StartRevokePath,
        [Parameter(Mandatory = $true)][string] $StartAttemptPath,
        [Parameter(Mandatory = $true)][string] $StartCompletionPath,
        [Parameter(Mandatory = $true)][object] $PreStartPostgres
    )
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($watchdogLeaseReadyTimeoutSeconds)
    if ($deadline -gt $HardDeadline) { $deadline = $HardDeadline }
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        Assert-TransitionWatchdogProcessConnected -Watchdog $Watchdog
        if (Test-Path -LiteralPath $LeasePath -PathType Leaf) {
            $item = Get-Item -LiteralPath $LeasePath -Force
            if ($item.Length -gt 0) {
                $document = $null
                try {
                    $document = Read-BoundedStrictJsonDocument -LiteralPath $LeasePath `
                        -Label 'transition watchdog lease' -MaximumBytes 262144
                }
                catch [IO.IOException] {
                    Start-Sleep -Milliseconds 50
                    continue
                }
                $accepted = Assert-TransitionWatchdogLease -Document $document `
                    -Watchdog $Watchdog -Program $Program -SessionId $SessionId `
                    -HardDeadline $HardDeadline -LeasePath $LeasePath `
                    -ReceiptPath $ReceiptPath -HeartbeatPath $HeartbeatPath `
                    -CommitPath $CommitPath -LivePath $LivePath `
                    -PreStartPath $PreStartPath -PreStartSha256 $PreStartSha256 `
                    -PreStartPayloadSha256 $PreStartPayloadSha256 `
                    -StartFencePath $StartFencePath `
                    -StartFenceSha256 $StartFenceSha256 `
                    -StartRevokePath $StartRevokePath `
                    -StartAttemptPath $StartAttemptPath `
                    -StartCompletionPath $StartCompletionPath `
                    -PreStartPostgres $PreStartPostgres
                if (-not (Test-Path -LiteralPath $ReceiptPath -PathType Leaf)) {
                    throw 'The watchdog did not reserve its create-new terminal receipt.'
                }
                $receiptItem = Get-Item -LiteralPath $ReceiptPath -Force
                if ($receiptItem.Length -ne 0) {
                    throw 'The watchdog reached a terminal state before PostgreSQL start.'
                }
                if ((Get-FileSha256 -LiteralPath $StartFencePath) -cne $StartFenceSha256) {
                    throw 'The watchdog-bound PostgreSQL start fence changed before lease acceptance.'
                }
                if (
                    (Test-Path -LiteralPath $HeartbeatPath) -or
                    (Test-Path -LiteralPath $CommitPath) -or
                    (Test-Path -LiteralPath $LivePath) -or
                    (Test-Path -LiteralPath $StartRevokePath) -or
                    (Test-Path -LiteralPath $StartAttemptPath) -or
                    (Test-Path -LiteralPath $StartCompletionPath)
                ) {
                    throw 'A post-lease transition artifact appeared before owner acceptance.'
                }
                return $accepted
            }
        }
        Start-Sleep -Milliseconds 50
    }
    throw 'The successor transition watchdog did not publish an accepted lease in time.'
}

function Assert-WatchdogCommitDocument {
    param(
        [Parameter(Mandatory = $true)][object] $Document,
        [Parameter(Mandatory = $true)][string] $SessionId,
        [Parameter(Mandatory = $true)][object] $Program,
        [Parameter(Mandatory = $true)][object] $LeaseProof,
        [Parameter(Mandatory = $true)][string] $LeasePath,
        [Parameter(Mandatory = $true)][string] $LivePath,
        [Parameter(Mandatory = $true)][string] $LiveSha256,
        [Parameter(Mandatory = $true)][DateTimeOffset] $HardDeadline
    )
    $commit = $Document.value
    Assert-ExactProperties -Value $commit -Label 'watchdog commit marker' -Expected @(
        'format', 'session_id', 'owner_pid', 'owner_start_time_utc',
        'lease_path', 'lease_sha256', 'live_path', 'live_sha256', 'committed_at'
    )
    foreach ($name in @(
        'format', 'session_id', 'owner_start_time_utc', 'lease_path',
        'lease_sha256', 'live_path', 'live_sha256', 'committed_at'
    )) {
        Assert-NativeJsonString -Value $commit.$name -Label "watchdog commit $name"
    }
    Assert-NativeJsonInteger -Value $commit.owner_pid -Label 'watchdog commit owner_pid'
    $committedAt = ConvertTo-CanonicalUtcTime -Value $commit.committed_at `
        -Label 'watchdog commit committed_at'
    $armedAt = ConvertTo-CanonicalUtcTime -Value $LeaseProof.armed_at `
        -Label 'accepted watchdog lease armed_at'
    if (
        $commit.format -cne 'stratus-successor-transition-watchdog-commit-v1' -or
        $commit.session_id -cne $SessionId -or
        $commit.owner_pid -ne $Program.owner_pid -or
        $commit.owner_start_time_utc -cne $Program.owner_start_time_utc -or
        -not (Test-ExactPathEqual -Left $commit.lease_path -Right $LeasePath) -or
        $commit.lease_sha256 -cne $LeaseProof.sha256 -or
        -not (Test-ExactPathEqual -Left $commit.live_path -Right $LivePath) -or
        $commit.live_sha256 -cne $LiveSha256 -or
        $committedAt -lt $armedAt -or $committedAt -gt $HardDeadline -or
        $committedAt -gt [DateTimeOffset]::UtcNow.AddSeconds($clockSkewSeconds)
    ) {
        throw 'The watchdog commit marker is not the exact lease-bound Live commit.'
    }
    return [pscustomobject]@{
        document = $Document
        value = $commit
        sha256 = [string]$Document.sha256
        committed_at = $committedAt.ToString('o')
    }
}

function Wait-TransitionWatchdogExit {
    param(
        [Parameter(Mandatory = $true)][object] $Watchdog,
        [AllowNull()][object] $HeartbeatPublisher,
        [Parameter(Mandatory = $true)][DateTimeOffset] $HardDeadline,
        [switch] $PermitContainmentExit
    )
    $waitDeadline = $HardDeadline.AddSeconds($watchdogProcessExitGraceSeconds)
    while ([DateTimeOffset]::UtcNow -lt $waitDeadline) {
        if ($null -ne $HeartbeatPublisher) { $HeartbeatPublisher.ThrowIfError() }
        $Watchdog.process.Refresh()
        if ($Watchdog.process.HasExited) { break }
        [void]$Watchdog.process.WaitForExit(200)
    }
    $Watchdog.process.Refresh()
    if (-not $Watchdog.process.HasExited) {
        throw 'The successor transition watchdog exceeded its hard terminal deadline.'
    }
    $Watchdog.collector.StopAndWait(5000)
    $Watchdog.collector.CloseOutputFiles()
    if (-not [bool]$Watchdog.collector.OutputFilesClosed) {
        throw 'The successor transition watchdog output handles remained open.'
    }
    $Watchdog.intentionally_stopped = $true
    if (
        -not [string]::IsNullOrEmpty([string]$Watchdog.collector.PumpError) -or
        [bool]$Watchdog.collector.OutputExceeded -or
        [int64]$Watchdog.collector.StderrBytes -ne 0
    ) {
        throw 'The successor transition watchdog output pump failed.'
    }
    $exitCode = [int]$Watchdog.process.ExitCode
    if ((-not $PermitContainmentExit -and $exitCode -ne 0) -or
        ($PermitContainmentExit -and $exitCode -notin @(0, 2, 3))) {
        throw "The successor transition watchdog returned unexpected exit $exitCode."
    }
    $stderrItem = Get-Item -LiteralPath $Watchdog.stderr_path -Force
    if ($stderrItem.Length -ne 0) {
        throw 'The successor transition watchdog emitted unexpected stderr.'
    }
    $stdout = Read-BoundedStrictJsonDocument -LiteralPath $Watchdog.stdout_path `
        -Label 'successor transition watchdog stdout' -MaximumBytes 262144
    if (
        $stdout.bytes -ne [int64]$Watchdog.collector.StdoutBytes -or
        $stdout.sha256 -cne [string]$Watchdog.collector.StdoutSha256
    ) {
        throw 'The successor transition watchdog stdout differs from the sealed output pump.'
    }
    return [pscustomobject]@{
        exit_code = $exitCode
        stdout = $stdout
        completed_at = [DateTimeOffset]::UtcNow.ToString('o')
    }
}

function Assert-TransitionWatchdogSuccessReceipt {
    param(
        [Parameter(Mandatory = $true)][object] $ExitProof,
        [Parameter(Mandatory = $true)][string] $ReceiptPath,
        [Parameter(Mandatory = $true)][string] $SessionId,
        [Parameter(Mandatory = $true)][object] $Program,
        [Parameter(Mandatory = $true)][object] $LeaseProof,
        [Parameter(Mandatory = $true)][object] $CommitProof,
        [Parameter(Mandatory = $true)][string] $CommitPath,
        [Parameter(Mandatory = $true)][string] $LivePath,
        [Parameter(Mandatory = $true)][string] $LiveSha256,
        [Parameter(Mandatory = $true)][string] $LivePayloadSha256,
        [Parameter(Mandatory = $true)][string] $PostgresStartedAt,
        [Parameter(Mandatory = $true)][DateTimeOffset] $HardDeadline
    )
    if ($ExitProof.exit_code -ne 0) {
        throw 'The transition watchdog did not accept the committed Live artifact.'
    }
    $receiptDocument = Read-BoundedStrictJsonDocument -LiteralPath $ReceiptPath `
        -Label 'successor transition watchdog terminal receipt' -MaximumBytes 262144
    $receipt = $receiptDocument.value
    Assert-ExactProperties -Value $receipt -Label 'watchdog terminal receipt' -Expected @(
        'format', 'status', 'terminal_at', 'session_id', 'owner_pid',
        'owner_start_time_utc', 'lease_path', 'lease_sha256', 'receipt_path',
        'commit_path', 'live_path', 'trigger', 'docker_engine', 'containment',
        'commit', 'authorization', 'failure'
    )
    foreach ($name in @(
        'format', 'status', 'terminal_at', 'session_id', 'owner_start_time_utc',
        'lease_path', 'lease_sha256', 'receipt_path', 'commit_path', 'live_path', 'trigger'
    )) {
        Assert-NativeJsonString -Value $receipt.$name -Label "watchdog receipt $name"
    }
    Assert-NativeJsonInteger -Value $receipt.owner_pid -Label 'watchdog receipt owner_pid'
    Assert-NativeJsonObject -Value $receipt.docker_engine -Label 'watchdog receipt engine'
    Assert-NativeJsonObject -Value $receipt.commit -Label 'watchdog receipt commit'
    Assert-NativeJsonObject -Value $receipt.authorization `
        -Label 'watchdog receipt authorization'
    Assert-ExactProperties -Value $receipt.authorization `
        -Label 'watchdog receipt authorization' -Expected @(
            'api_stage_permitted', 'worker_start_permitted',
            'safety_control_start_permitted', 'live_transition_permitted',
            'binance_kill_switch_removal_permitted', 'migration_container_permitted'
        )
    foreach ($name in @(
        'api_stage_permitted', 'worker_start_permitted',
        'safety_control_start_permitted', 'live_transition_permitted',
        'binance_kill_switch_removal_permitted', 'migration_container_permitted'
    )) {
        Assert-NativeJsonBoolean -Value $receipt.authorization.$name `
            -Label "watchdog receipt authorization $name"
    }
    $terminalAt = ConvertTo-CanonicalUtcTime -Value $receipt.terminal_at `
        -Label 'watchdog receipt terminal_at'
    $committedAt = ConvertTo-CanonicalUtcTime -Value $CommitProof.committed_at `
        -Label 'accepted watchdog commit time'
    if (
        $null -ne $receipt.containment -or $null -ne $receipt.failure -or
        $receipt.format -cne 'stratus-successor-transition-watchdog-terminal-v1' -or
        $receipt.status -cne 'success' -or $receipt.trigger -cne 'accepted_live_commit' -or
        $receipt.session_id -cne $SessionId -or
        $receipt.authorization.api_stage_permitted -or
        $receipt.authorization.worker_start_permitted -or
        $receipt.authorization.safety_control_start_permitted -or
        $receipt.authorization.live_transition_permitted -or
        $receipt.authorization.binance_kill_switch_removal_permitted -or
        $receipt.authorization.migration_container_permitted -or
        $receipt.owner_pid -ne $Program.owner_pid -or
        $receipt.owner_start_time_utc -cne $Program.owner_start_time_utc -or
        -not (Test-ExactPathEqual -Left $receipt.lease_path -Right $LeaseProof.document.path) -or
        $receipt.lease_sha256 -cne $LeaseProof.sha256 -or
        -not (Test-ExactPathEqual -Left $receipt.receipt_path -Right $ReceiptPath) -or
        -not (Test-ExactPathEqual -Left $receipt.commit_path -Right $CommitPath) -or
        -not (Test-ExactPathEqual -Left $receipt.live_path -Right $LivePath) -or
        $terminalAt -lt $committedAt -or $terminalAt -gt $HardDeadline.AddSeconds($clockSkewSeconds)
    ) {
        throw 'The watchdog terminal receipt is not the exact successful transition receipt.'
    }
    if (
        ($receipt.docker_engine | ConvertTo-Json -Depth 10 -Compress) -cne
            ($LeaseProof.docker_engine | ConvertTo-Json -Depth 10 -Compress)
    ) {
        throw 'The watchdog Docker engine changed between lease and terminal receipt.'
    }
    Assert-ExactProperties -Value $receipt.commit -Label 'watchdog terminal commit proof' `
        -Expected @(
            'marker_sha256', 'committed_at', 'live_sha256', 'live_payload_sha256',
            'postgres_started_at', 'docker_engine_contract_sha256'
        )
    foreach ($name in @(
        'marker_sha256', 'committed_at', 'live_sha256', 'live_payload_sha256',
        'postgres_started_at', 'docker_engine_contract_sha256'
    )) {
        Assert-NativeJsonString -Value $receipt.commit.$name `
            -Label "watchdog terminal commit $name"
    }
    if (
        $receipt.commit.marker_sha256 -cne $CommitProof.sha256 -or
        $receipt.commit.committed_at -cne $CommitProof.committed_at -or
        $receipt.commit.live_sha256 -cne $LiveSha256 -or
        $receipt.commit.live_payload_sha256 -cne $LivePayloadSha256 -or
        $receipt.commit.postgres_started_at -cne $PostgresStartedAt -or
        $receipt.commit.docker_engine_contract_sha256 -cne
            $LeaseProof.docker_engine.contract_sha256
    ) {
        throw 'The watchdog terminal receipt does not bind the committed Live proof.'
    }
    $normalizedReceiptJson = $receiptDocument.json.Replace("`r`n", "`n")
    if ($normalizedReceiptJson.Contains("`r") -or
        $ExitProof.stdout.json -cne ($normalizedReceiptJson + "`n")) {
        throw 'The watchdog stdout terminal receipt differs from its durable receipt.'
    }
    return [pscustomobject]@{
        format = [string]$receipt.format
        status = [string]$receipt.status
        trigger = [string]$receipt.trigger
        terminal_at = $terminalAt.ToString('o')
        path = [string]$receiptDocument.path
        bytes = [int64]$receiptDocument.bytes
        sha256 = [string]$receiptDocument.sha256
        lease_sha256 = [string]$receipt.lease_sha256
        commit_marker_sha256 = [string]$receipt.commit.marker_sha256
        live_sha256 = [string]$receipt.commit.live_sha256
        docker_engine_contract_sha256 =
            [string]$receipt.commit.docker_engine_contract_sha256
    }
}

function Stop-AcceptedTerminalHeartbeatPublisher {
    param([Parameter(Mandatory = $true)][object] $Publisher)

    try {
        $Publisher.StopAndWait(5000)
    }
    catch {
        # HeartbeatPublisher.StopAndWait joins the worker before surfacing its
        # retained publication error.  Once the exact watchdog process has
        # exited 0 and its terminal receipt has been validated, that one
        # post-terminal error is cleanup-only. A join timeout or any different
        # failure remains fatal and must not reach parent authorization.
        $candidateFailure = $_.Exception
        $joinedPublicationFailure = $false
        while ($null -ne $candidateFailure) {
            if (
                $candidateFailure.GetType().FullName -ceq
                    'System.InvalidOperationException' -and
                $candidateFailure.Message -ceq
                    'watchdog heartbeat publisher failed'
            ) {
                $joinedPublicationFailure = $true
                break
            }
            $candidateFailure = $candidateFailure.InnerException
        }
        if (-not $joinedPublicationFailure) {
            throw
        }
    }
    $Publisher.Dispose()
}

function Assert-TransitionWatchdogContainmentEvidence {
    param(
        [Parameter(Mandatory = $true)][object] $Containment,
        [Parameter(Mandatory = $true)][string] $Trigger,
        [Parameter(Mandatory = $true)][string] $StartFencePath,
        [Parameter(Mandatory = $true)][string] $StartFenceSha256,
        [Parameter(Mandatory = $true)][string] $StartRevokePath,
        [Parameter(Mandatory = $true)][string] $StartAttemptPath,
        [Parameter(Mandatory = $true)][string] $StartCompletionPath,
        [switch] $PermitInvalidProtocol
    )

    Assert-NativeJsonObject -Value $Containment `
        -Label 'failed transition watchdog containment'
    Assert-ExactProperties -Value $Containment `
        -Label 'failed transition watchdog containment' -Expected @(
            'trigger', 'start_protocol_status', 'start_attempt_path',
            'start_attempt_sha256', 'start_completion_path',
            'start_completion_sha256', 'causal_materialization_required',
            'materialization_observed', 'attempted_stop',
            'docker_stop_succeeded', 'stop_attempt_count',
            'stop_timeout_seconds', 'stop_wall_clock_timeout_seconds',
            'stopped_proof_count', 'transition_observation_count',
            'verified_stopped', 'before_state', 'after_state', 'last_error',
            'errors', 'start_revoke_path', 'start_revoke_present',
            'start_revoke_created', 'start_fence_path', 'start_fence_sha256',
            'start_fence_lock_acquired', 'start_revoke_attempt_count',
            'final_after_fence', 'fence_acquire_advisory_exceeded',
            'owner_termination_attempt_count',
            'owner_terminated_for_fence_recovery', 'owner_terminated_at',
            'owner_termination_failure'
        )
    foreach ($name in @(
        'trigger', 'start_protocol_status', 'start_attempt_path',
        'start_completion_path', 'before_state', 'after_state',
        'start_revoke_path', 'start_fence_path', 'start_fence_sha256'
    )) {
        Assert-NativeJsonString -Value $Containment.$name `
            -Label "failed transition containment $name"
    }
    foreach ($name in @(
        'causal_materialization_required', 'materialization_observed',
        'attempted_stop', 'docker_stop_succeeded', 'verified_stopped',
        'start_revoke_present', 'start_revoke_created',
        'start_fence_lock_acquired', 'final_after_fence',
        'fence_acquire_advisory_exceeded',
        'owner_terminated_for_fence_recovery'
    )) {
        Assert-NativeJsonBoolean -Value $Containment.$name `
            -Label "failed transition containment $name"
    }
    foreach ($name in @(
        'stop_attempt_count', 'stop_timeout_seconds',
        'stop_wall_clock_timeout_seconds', 'stopped_proof_count',
        'transition_observation_count', 'start_revoke_attempt_count',
        'owner_termination_attempt_count'
    )) {
        Assert-NativeJsonInteger -Value $Containment.$name `
            -Label "failed transition containment $name"
    }
    if ($Containment.errors -isnot [Array] -or @($Containment.errors).Count -ne 0) {
        throw 'Failed transition containment errors must be an empty native JSON array.'
    }
    if ($null -ne $Containment.last_error) {
        throw 'Failed transition containment retained a nonterminal Docker error.'
    }
    $ownerTerminationAt = $null
    if ($null -ne $Containment.owner_terminated_at) {
        Assert-NativeJsonString -Value $Containment.owner_terminated_at `
            -Label 'failed transition containment owner_terminated_at'
        $ownerTerminationAt = ConvertTo-CanonicalUtcTime `
            -Value $Containment.owner_terminated_at `
            -Label 'failed transition containment owner_terminated_at'
    }
    if ($null -ne $Containment.owner_termination_failure) {
        Assert-NativeJsonString -Value $Containment.owner_termination_failure `
            -Label 'failed transition containment owner_termination_failure'
    }
    if (
        ($Containment.owner_terminated_for_fence_recovery -and (
            $Containment.owner_termination_attempt_count -lt 1 -or
            $null -eq $ownerTerminationAt -or
            $null -ne $Containment.owner_termination_failure
        )) -or
        (-not $Containment.owner_terminated_for_fence_recovery -and
            $null -ne $ownerTerminationAt) -or
        ($null -ne $Containment.owner_termination_failure -and
            $Containment.owner_termination_attempt_count -lt 1)
    ) {
        throw 'Failed transition containment owner takeover evidence is inconsistent.'
    }
    $protocolStatus = [string]$Containment.start_protocol_status
    $allowedProtocolStatuses = if ($PermitInvalidProtocol) {
        @('none', 'unresolved', 'completed', 'invalid')
    }
    else { @('none', 'unresolved', 'completed') }
    if (
        $Containment.trigger -cne $Trigger -or
        $protocolStatus -cnotin $allowedProtocolStatuses -or
        -not (Test-ExactPathEqual -Left $Containment.start_fence_path `
            -Right $StartFencePath) -or
        $Containment.start_fence_sha256 -cne $StartFenceSha256 -or
        -not (Test-ExactPathEqual -Left $Containment.start_revoke_path `
            -Right $StartRevokePath) -or
        -not (Test-ExactPathEqual -Left $Containment.start_attempt_path `
            -Right $StartAttemptPath) -or
        -not (Test-ExactPathEqual -Left $Containment.start_completion_path `
            -Right $StartCompletionPath) -or
        $Containment.stop_timeout_seconds -ne $postgresRollbackTimeoutSeconds -or
        $Containment.stop_wall_clock_timeout_seconds -ne 25 -or
        $Containment.stop_attempt_count -lt 0 -or
        $Containment.stopped_proof_count -lt 1 -or
        $Containment.transition_observation_count -lt 0 -or
        $Containment.start_revoke_attempt_count -lt 1 -or
        $Containment.owner_termination_attempt_count -lt 0 -or
        -not $Containment.verified_stopped -or
        $Containment.after_state -cne 'exited' -or
        $Containment.before_state -cnotin @('stopped', 'ambiguous') -or
        -not $Containment.start_revoke_present -or
        -not $Containment.start_fence_lock_acquired -or
        -not $Containment.final_after_fence -or
        -not (Test-Path -LiteralPath $StartRevokePath -PathType Leaf) -or
        (Get-FileSha256 -LiteralPath $StartFencePath) -cne $StartFenceSha256
    ) {
        throw 'Failed transition containment is not exact, fenced, revoked, and stopped.'
    }
    if (
        ($Containment.attempted_stop -and (
            -not $Containment.docker_stop_succeeded -or
            $Containment.stop_attempt_count -lt 1 -or
            -not $Containment.materialization_observed
        )) -or
        (-not $Containment.attempted_stop -and (
            $Containment.docker_stop_succeeded -or
            $Containment.stop_attempt_count -ne 0
        )) -or
        ($Containment.materialization_observed -and
            $Containment.transition_observation_count -lt 1)
    ) {
        throw 'Failed transition containment stop evidence is internally inconsistent.'
    }
    switch ($protocolStatus) {
        'none' {
            if (
                $null -ne $Containment.start_attempt_sha256 -or
                $null -ne $Containment.start_completion_sha256 -or
                $Containment.causal_materialization_required
            ) {
                throw 'No-start containment carries contradictory protocol evidence.'
            }
        }
        'unresolved' {
            if (
                [string]$Containment.start_attempt_sha256 -notmatch
                    '^[0-9a-f]{64}$' -or
                $null -ne $Containment.start_completion_sha256 -or
                -not $Containment.causal_materialization_required -or
                -not $Containment.materialization_observed -or
                -not $Containment.attempted_stop -or
                -not $Containment.docker_stop_succeeded -or
                (Get-FileSha256 -LiteralPath $StartAttemptPath) -cne
                    [string]$Containment.start_attempt_sha256
            ) {
                throw 'Unresolved-start containment lacks causal materialization and stop proof.'
            }
        }
        'completed' {
            if (
                [string]$Containment.start_attempt_sha256 -notmatch
                    '^[0-9a-f]{64}$' -or
                [string]$Containment.start_completion_sha256 -notmatch
                    '^[0-9a-f]{64}$' -or
                $Containment.causal_materialization_required -or
                (Get-FileSha256 -LiteralPath $StartAttemptPath) -cne
                    [string]$Containment.start_attempt_sha256 -or
                (Get-FileSha256 -LiteralPath $StartCompletionPath) -cne
                    [string]$Containment.start_completion_sha256
            ) {
                throw 'Completed-start containment does not bind the immutable start protocol.'
            }
        }
        'invalid' {
            if (
                -not $PermitInvalidProtocol -or
                $null -ne $Containment.start_attempt_sha256 -or
                $null -ne $Containment.start_completion_sha256 -or
                -not $Containment.causal_materialization_required -or
                -not $Containment.materialization_observed -or
                -not $Containment.attempted_stop -or
                -not $Containment.docker_stop_succeeded
            ) {
                throw 'Invalid-start containment lacks a conservative causal stop proof.'
            }
        }
    }
    return $Containment
}

function Assert-TransitionWatchdogFailureReceipt {
    param(
        [Parameter(Mandatory = $true)][object] $ExitProof,
        [Parameter(Mandatory = $true)][string] $ReceiptPath,
        [Parameter(Mandatory = $true)][string] $SessionId,
        [Parameter(Mandatory = $true)][object] $Program,
        [AllowNull()][object] $LeaseProof,
        [Parameter(Mandatory = $true)][string] $LeasePath,
        [Parameter(Mandatory = $true)][string] $CommitPath,
        [Parameter(Mandatory = $true)][string] $LivePath,
        [Parameter(Mandatory = $true)][string] $StartFencePath,
        [Parameter(Mandatory = $true)][string] $StartFenceSha256,
        [Parameter(Mandatory = $true)][string] $StartRevokePath,
        [Parameter(Mandatory = $true)][string] $StartAttemptPath,
        [Parameter(Mandatory = $true)][string] $StartCompletionPath
    )

    $receiptDocument = Read-BoundedStrictJsonDocument -LiteralPath $ReceiptPath `
        -Label 'failed successor transition watchdog terminal receipt' `
        -MaximumBytes 262144
    $receipt = $receiptDocument.value
    Assert-ExactProperties -Value $receipt `
        -Label 'failed transition watchdog receipt' -Expected @(
            'format', 'status', 'terminal_at', 'session_id', 'owner_pid',
            'owner_start_time_utc', 'lease_path', 'lease_sha256',
            'receipt_path', 'commit_path', 'live_path', 'trigger',
            'docker_engine', 'containment', 'commit', 'authorization',
            'failure'
        )
    foreach ($name in @(
        'format', 'status', 'terminal_at', 'session_id',
        'owner_start_time_utc', 'lease_path', 'receipt_path', 'commit_path',
        'live_path', 'trigger'
    )) {
        Assert-NativeJsonString -Value $receipt.$name `
            -Label "failed transition watchdog receipt $name"
    }
    Assert-NativeJsonInteger -Value $receipt.owner_pid `
        -Label 'failed transition watchdog receipt owner_pid'
    Assert-NativeJsonObject -Value $receipt.authorization `
        -Label 'failed transition watchdog authorization'
    Assert-ExactProperties -Value $receipt.authorization `
        -Label 'failed transition watchdog authorization' -Expected @(
            'api_stage_permitted', 'worker_start_permitted',
            'safety_control_start_permitted', 'live_transition_permitted',
            'binance_kill_switch_removal_permitted',
            'migration_container_permitted'
        )
    foreach ($name in @(
        'api_stage_permitted', 'worker_start_permitted',
        'safety_control_start_permitted', 'live_transition_permitted',
        'binance_kill_switch_removal_permitted',
        'migration_container_permitted'
    )) {
        Assert-NativeJsonBoolean -Value $receipt.authorization.$name `
            -Label "failed transition watchdog authorization $name"
        if ($receipt.authorization.$name) {
            throw 'Failed transition watchdog receipt authorized a forbidden stage.'
        }
    }
    $terminalAt = ConvertTo-CanonicalUtcTime -Value $receipt.terminal_at `
        -Label 'failed watchdog receipt terminal_at'
    $expectedExitCode = if ($receipt.status -ceq 'contained') { 2 }
        elseif ($receipt.status -ceq 'failure') { 3 }
        else { -1 }
    if (
        $receipt.format -cne 'stratus-successor-transition-watchdog-terminal-v1' -or
        $expectedExitCode -lt 0 -or $ExitProof.exit_code -ne $expectedExitCode -or
        $receipt.session_id -cne $SessionId -or
        $receipt.owner_pid -ne $Program.owner_pid -or
        $receipt.owner_start_time_utc -cne $Program.owner_start_time_utc -or
        -not (Test-ExactPathEqual -Left $receipt.lease_path -Right $LeasePath) -or
        -not (Test-ExactPathEqual -Left $receipt.receipt_path -Right $ReceiptPath) -or
        -not (Test-ExactPathEqual -Left $receipt.commit_path -Right $CommitPath) -or
        -not (Test-ExactPathEqual -Left $receipt.live_path -Right $LivePath) -or
        $terminalAt -gt [DateTimeOffset]::UtcNow.AddSeconds($clockSkewSeconds) -or
        $null -ne $receipt.commit
    ) {
        throw 'Failed transition watchdog receipt is not exact and process-bound.'
    }
    if ($null -ne $LeaseProof) {
        Assert-NativeJsonString -Value $receipt.lease_sha256 `
            -Label 'failed watchdog receipt lease_sha256'
        Assert-NativeJsonObject -Value $receipt.docker_engine `
            -Label 'failed watchdog receipt Docker engine'
        $leaseArmedAt = ConvertTo-CanonicalUtcTime -Value $LeaseProof.armed_at `
            -Label 'failed watchdog accepted lease armed_at'
        if (
            $receipt.lease_sha256 -cne $LeaseProof.sha256 -or
            $terminalAt -lt $leaseArmedAt -or
            ($receipt.docker_engine | ConvertTo-Json -Depth 10 -Compress) -cne
                ($LeaseProof.docker_engine | ConvertTo-Json -Depth 10 -Compress)
        ) {
            throw 'Failed transition watchdog receipt does not bind the accepted lease and engine.'
        }
    }
    elseif ($null -ne $receipt.lease_sha256) {
        throw 'Pre-lease watchdog failure carries an unaccepted lease hash.'
    }
    $normalizedReceiptJson = $receiptDocument.json.Replace("`r`n", "`n")
    if ($normalizedReceiptJson.Contains("`r") -or
        $ExitProof.stdout.json -cne ($normalizedReceiptJson + "`n")) {
        throw 'Failed watchdog stdout terminal receipt differs from its durable receipt.'
    }
    if ($receipt.status -ceq 'contained') {
        if ($null -eq $receipt.containment -or
            ($null -ne $receipt.failure -and $receipt.failure.GetType() -ne [string])) {
            throw 'Contained watchdog receipt has an invalid status-specific payload.'
        }
        [void](Assert-TransitionWatchdogContainmentEvidence `
            -Containment $receipt.containment -Trigger $receipt.trigger `
            -StartFencePath $StartFencePath -StartFenceSha256 $StartFenceSha256 `
            -StartRevokePath $StartRevokePath `
            -StartAttemptPath $StartAttemptPath `
            -StartCompletionPath $StartCompletionPath)
    }
    else {
        Assert-NativeJsonString -Value $receipt.failure `
            -Label 'failed watchdog receipt failure detail'
        if ([string]::IsNullOrWhiteSpace([string]$receipt.failure)) {
            throw 'Failed watchdog receipt lacks a failure detail.'
        }
        if ($null -eq $receipt.containment) {
            if (
                $receipt.trigger -cne 'initialization_failure' -or
                (Test-Path -LiteralPath $StartAttemptPath) -or
                (Test-Path -LiteralPath $StartCompletionPath)
            ) {
                throw 'Containment-free watchdog failure is not an initialization failure.'
            }
        }
        else {
            [void](Assert-TransitionWatchdogContainmentEvidence `
                -Containment $receipt.containment -Trigger $receipt.trigger `
                -StartFencePath $StartFencePath `
                -StartFenceSha256 $StartFenceSha256 `
                -StartRevokePath $StartRevokePath `
                -StartAttemptPath $StartAttemptPath `
                -StartCompletionPath $StartCompletionPath `
                -PermitInvalidProtocol)
        }
    }
    return [pscustomobject]@{
        status = [string]$receipt.status
        trigger = [string]$receipt.trigger
        terminal_at = $terminalAt.ToString('o')
        sha256 = [string]$receiptDocument.sha256
    }
}

function ConvertTo-BoundedNativeArgumentToken {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string] $Value)

    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') {
        return $Value
    }
    $builder = [Text.StringBuilder]::new()
    [void]$builder.Append('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') {
            $slashes += 1
            continue
        }
        if ($character -eq '"') {
            [void]$builder.Append(('\' * (($slashes * 2) + 1)))
            [void]$builder.Append('"')
            $slashes = 0
            continue
        }
        if ($slashes -gt 0) {
            [void]$builder.Append(('\' * $slashes))
            $slashes = 0
        }
        [void]$builder.Append($character)
    }
    if ($slashes -gt 0) {
        [void]$builder.Append(('\' * ($slashes * 2)))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Invoke-BoundedPinnedDockerRead {
    param(
        [Parameter(Mandatory = $true)][object] $CollectorType,
        [Parameter(Mandatory = $true)][string[]] $Arguments,
        [Parameter(Mandatory = $true)][string] $Label,
        [int] $ProcessTimeoutMilliseconds = 5000,
        [int] $MaximumStdoutBytes = 1048576
    )

    if ($Arguments.Count -eq 0) {
        throw "$Label received an empty Docker command."
    }
    if (
        $ProcessTimeoutMilliseconds -lt 1 -or
        $ProcessTimeoutMilliseconds -gt 22000 -or
        $MaximumStdoutBytes -lt 1 -or
        $MaximumStdoutBytes -gt 2097152
    ) {
        throw "$Label received an invalid bounded-process limit."
    }
    $dockerCli = Get-PinnedApplicationBoundary `
        -LiteralPath $script:dockerExecutable `
        -ExpectedSha256 $script:dockerExecutableSha256 -Label $Label
    $method = $CollectorType.type.GetMethod(
        'RunBounded',
        [Reflection.BindingFlags]::Public -bor [Reflection.BindingFlags]::Static
    )
    if ($null -eq $method) {
        throw 'The revision-bound collector does not expose bounded Docker reads.'
    }
    $effectiveArguments = @('--context', $approvedDockerContext) + @($Arguments)
    $argumentText = (@($effectiveArguments | ForEach-Object {
        ConvertTo-BoundedNativeArgumentToken -Value ([string]$_)
    }) -join ' ')
    $requestedAt = [DateTimeOffset]::UtcNow
    try {
        $result = $method.Invoke($null, @(
            [string]$dockerCli.path,
            $argumentText,
            [int]$ProcessTimeoutMilliseconds,
            [int]$MaximumStdoutBytes,
            [int]65536
        ))
    }
    catch {
        $detail = if ($null -ne $_.Exception.InnerException) {
            $_.Exception.InnerException.Message
        }
        else {
            $_.Exception.Message
        }
        throw "$Label bounded Docker process failed: $detail"
    }
    $completedAt = [DateTimeOffset]::UtcNow
    [void](Get-PinnedApplicationBoundary `
        -LiteralPath $script:dockerExecutable `
        -ExpectedSha256 $script:dockerExecutableSha256 `
        -Label "$Label completion")
    if ($null -eq $result) {
        throw "$Label bounded Docker process returned no result."
    }
    if (
        -not [bool]$result.ProcessStarted -or
        -not [bool]$result.ProcessExited -or
        [bool]$result.TimedOut -or
        [bool]$result.OutputExceeded -or
        -not [string]::IsNullOrEmpty([string]$result.PumpError) -or
        [int]$result.ExitCode -ne 0 -or
        -not [string]::IsNullOrEmpty([string]$result.Stderr)
    ) {
        throw "$Label bounded Docker process timed out, overflowed, or failed."
    }
    $stdout = ([string]$result.Stdout).Replace("`r`n", "`n")
    if ($stdout.Contains("`r")) {
        throw "$Label bounded Docker output is not canonical text."
    }
    return [pscustomobject]@{
        command = [string[]]@($Arguments)
        requested_at = $requestedAt.ToString('o')
        completed_at = $completedAt.ToString('o')
        process_timeout_milliseconds = [int]$ProcessTimeoutMilliseconds
        stdout_maximum_bytes = [int]$MaximumStdoutBytes
        lines = [string[]]@($stdout -split "`n" | Where-Object {
            -not [string]::IsNullOrWhiteSpace([string]$_)
        })
    }
}

function Get-BoundedParentDockerEngineBoundary {
    param(
        [Parameter(Mandatory = $true)][object] $CollectorType,
        [Parameter(Mandatory = $true)][object] $ExpectedEngine
    )

    $contextRead = Invoke-BoundedPinnedDockerRead -CollectorType $CollectorType `
        -Arguments @('context', 'show') -Label 'parent containment Docker context'
    $contextName = Get-OneNativeLine -Label 'parent containment Docker context' `
        -Lines $contextRead.lines
    $endpointRead = Invoke-BoundedPinnedDockerRead -CollectorType $CollectorType `
        -Arguments @(
            'context', 'inspect', $contextName,
            '--format', '{{json .Endpoints.docker}}'
        ) -Label 'parent containment Docker endpoint'
    $endpoint = Get-OneJsonObject -Label 'parent containment Docker endpoint' `
        -Lines $endpointRead.lines
    $infoRead = Invoke-BoundedPinnedDockerRead -CollectorType $CollectorType `
        -Arguments @('info', '--format', '{{json .}}') `
        -Label 'parent containment Docker engine'
    $info = Get-OneJsonObject -Label 'parent containment Docker engine' `
        -Lines $infoRead.lines
    Assert-NativeJsonString -Value $endpoint.Host `
        -Label 'parent containment Docker endpoint Host'
    Assert-NativeJsonBoolean -Value $endpoint.SkipTLSVerify `
        -Label 'parent containment Docker endpoint SkipTLSVerify'
    foreach ($name in @('ID', 'ServerVersion', 'Name', 'DockerRootDir')) {
        Assert-NativeJsonString -Value $info.$name `
            -Label "parent containment Docker engine $name"
    }
    if (
        $contextName -cne [string]$ExpectedEngine.context -or
        $endpoint.Host -cne [string]$ExpectedEngine.endpoint -or
        $endpoint.SkipTLSVerify -ne [bool]$ExpectedEngine.skip_tls_verify -or
        $info.ID -cne [string]$ExpectedEngine.engine_id -or
        $info.ServerVersion -cne [string]$ExpectedEngine.server_version -or
        $info.Name -cne [string]$ExpectedEngine.engine_name -or
        $info.DockerRootDir -cne [string]$ExpectedEngine.docker_root_dir
    ) {
        throw 'Parent containment Docker context or engine identity drifted.'
    }
    return [pscustomobject]@{
        context = $contextName
        endpoint = [string]$endpoint.Host
        skip_tls_verify = [bool]$endpoint.SkipTLSVerify
        engine_id = [string]$info.ID
        server_version = [string]$info.ServerVersion
        engine_name = [string]$info.Name
        docker_root_dir = [string]$info.DockerRootDir
        contract_sha256 = [string]$ExpectedEngine.contract_sha256
    }
}

function Get-BoundedParentStoppedPostgresBoundary {
    param(
        [Parameter(Mandatory = $true)][object] $CollectorType,
        [Parameter(Mandatory = $true)][object] $ExpectedEngine,
        [Parameter(Mandatory = $true)][object] $ExpectedPostgres
    )

    $engineBefore = Get-BoundedParentDockerEngineBoundary `
        -CollectorType $CollectorType -ExpectedEngine $ExpectedEngine
    $inspectRead = Invoke-BoundedPinnedDockerRead -CollectorType $CollectorType `
        -Arguments @('inspect', $productionPostgresId, '--format', '{{json .}}') `
        -Label 'parent containment retained PostgreSQL inspect'
    $inspect = Get-OneJsonObject `
        -Label 'parent containment retained PostgreSQL inspect' `
        -Lines $inspectRead.lines
    foreach ($name in @('Config', 'State', 'HostConfig')) {
        Assert-NativeJsonObject -Value $inspect.$name `
            -Label "parent containment retained PostgreSQL $name"
    }
    Assert-NativeJsonObject -Value $inspect.HostConfig.RestartPolicy `
        -Label 'parent containment retained PostgreSQL restart policy'
    foreach ($name in @('Id', 'Name', 'Image')) {
        Assert-NativeJsonString -Value $inspect.$name `
            -Label "parent containment retained PostgreSQL $name"
    }
    Assert-NativeJsonString -Value $inspect.Config.Image `
        -Label 'parent containment retained PostgreSQL Config.Image'
    foreach ($name in @('Status', 'Error', 'StartedAt', 'FinishedAt')) {
        Assert-NativeJsonString -Value $inspect.State.$name `
            -Label "parent containment retained PostgreSQL State.$name"
    }
    foreach ($name in @('Running', 'Paused', 'Restarting', 'OOMKilled', 'Dead')) {
        Assert-NativeJsonBoolean -Value $inspect.State.$name `
            -Label "parent containment retained PostgreSQL State.$name"
    }
    foreach ($entry in @(
        [pscustomobject]@{ value = $inspect.RestartCount; label = 'RestartCount' },
        [pscustomobject]@{ value = $inspect.State.Pid; label = 'State.Pid' },
        [pscustomobject]@{ value = $inspect.State.ExitCode; label = 'State.ExitCode' }
    )) {
        Assert-NativeJsonInteger -Value $entry.value `
            -Label "parent containment retained PostgreSQL $($entry.label)"
    }
    Assert-NativeJsonString -Value $inspect.HostConfig.RestartPolicy.Name `
        -Label 'parent containment retained PostgreSQL restart policy Name'
    $runningRead = Invoke-BoundedPinnedDockerRead -CollectorType $CollectorType `
        -Arguments @('ps', '-q', '--no-trunc') `
        -Label 'parent containment running-container inventory'
    foreach ($containerId in @($runningRead.lines)) {
        if ([string]$containerId -notmatch '^[0-9a-f]{64}$') {
            throw 'Parent containment running-container inventory is malformed.'
        }
    }
    if (
        $inspect.Id -cne $productionPostgresId -or
        $inspect.Name -cne '/weatherpoly-postgres-1' -or
        $inspect.Image -cne [string]$ExpectedPostgres.image_id -or
        $inspect.Config.Image -cne [string]$ExpectedPostgres.config_image -or
        $inspect.RestartCount -ne [int]$ExpectedPostgres.restart_count -or
        $inspect.RestartCount -ne 0 -or
        $inspect.HostConfig.RestartPolicy.Name -cne 'no' -or
        $inspect.State.Status -cne 'exited' -or
        $inspect.State.Running -or $inspect.State.Paused -or
        $inspect.State.Restarting -or $inspect.State.OOMKilled -or
        $inspect.State.Dead -or $inspect.State.Pid -ne 0 -or
        -not [string]::IsNullOrEmpty([string]$inspect.State.Error) -or
        @($runningRead.lines).Count -ne 0
    ) {
        throw 'Parent containment did not prove the exact stopped PostgreSQL-only boundary.'
    }
    $engineAfter = Get-BoundedParentDockerEngineBoundary `
        -CollectorType $CollectorType -ExpectedEngine $ExpectedEngine
    if ($engineAfter.contract_sha256 -cne $engineBefore.contract_sha256) {
        throw 'Parent containment Docker engine drifted across the stopped proof.'
    }
    return [pscustomobject]@{
        container_id = [string]$inspect.Id
        image_id = [string]$inspect.Image
        config_image = [string]$inspect.Config.Image
        restart_count = [int]$inspect.RestartCount
        restart_policy = [string]$inspect.HostConfig.RestartPolicy.Name
        state = [string]$inspect.State.Status
        running = [bool]$inspect.State.Running
        pid = [int]$inspect.State.Pid
        oom_killed = [bool]$inspect.State.OOMKilled
        error = [string]$inspect.State.Error
        running_container_count = [int]@($runningRead.lines).Count
        docker_engine_contract_sha256 = [string]$engineAfter.contract_sha256
        verified_stopped = $true
    }
}

function Get-BoundedParentRunningPostgresBoundary {
    param(
        [Parameter(Mandatory = $true)][object] $CollectorType,
        [Parameter(Mandatory = $true)][object] $ExpectedEngine,
        [Parameter(Mandatory = $true)][object] $ExpectedPostgres
    )

    $engineBefore = Get-BoundedParentDockerEngineBoundary `
        -CollectorType $CollectorType -ExpectedEngine $ExpectedEngine
    $inspectRead = Invoke-BoundedPinnedDockerRead -CollectorType $CollectorType `
        -Arguments @('inspect', $productionPostgresId, '--format', '{{json .}}') `
        -Label 'parent authorization retained PostgreSQL inspect'
    $inspect = Get-OneJsonObject `
        -Label 'parent authorization retained PostgreSQL inspect' `
        -Lines $inspectRead.lines
    foreach ($name in @('Config', 'State', 'HostConfig', 'NetworkSettings')) {
        Assert-NativeJsonObject -Value $inspect.$name `
            -Label "parent authorization retained PostgreSQL $name"
    }
    Assert-NativeJsonObject -Value $inspect.State.Health `
        -Label 'parent authorization retained PostgreSQL health'
    Assert-NativeJsonObject -Value $inspect.HostConfig.RestartPolicy `
        -Label 'parent authorization retained PostgreSQL restart policy'
    Assert-NativeJsonObject -Value $inspect.HostConfig.PortBindings `
        -Label 'parent authorization retained PostgreSQL port bindings'
    Assert-NativeJsonObject -Value $inspect.NetworkSettings.Ports `
        -Label 'parent authorization retained PostgreSQL runtime ports'
    Assert-NativeJsonObject -Value $inspect.NetworkSettings.Networks `
        -Label 'parent authorization retained PostgreSQL networks'
    foreach ($name in @('Id', 'Name', 'Image')) {
        Assert-NativeJsonString -Value $inspect.$name `
            -Label "parent authorization retained PostgreSQL $name"
    }
    foreach ($name in @('Image')) {
        Assert-NativeJsonString -Value $inspect.Config.$name `
            -Label "parent authorization retained PostgreSQL Config.$name"
    }
    foreach ($name in @('Status', 'Error', 'StartedAt', 'FinishedAt')) {
        Assert-NativeJsonString -Value $inspect.State.$name `
            -Label "parent authorization retained PostgreSQL State.$name"
    }
    Assert-NativeJsonString -Value $inspect.State.Health.Status `
        -Label 'parent authorization retained PostgreSQL health status'
    foreach ($name in @('Running', 'Paused', 'Restarting', 'OOMKilled', 'Dead')) {
        Assert-NativeJsonBoolean -Value $inspect.State.$name `
            -Label "parent authorization retained PostgreSQL State.$name"
    }
    foreach ($entry in @(
        [pscustomobject]@{ value = $inspect.RestartCount; label = 'RestartCount' },
        [pscustomobject]@{ value = $inspect.State.Pid; label = 'State.Pid' },
        [pscustomobject]@{ value = $inspect.State.ExitCode; label = 'State.ExitCode' }
    )) {
        Assert-NativeJsonInteger -Value $entry.value `
            -Label "parent authorization retained PostgreSQL $($entry.label)"
    }
    Assert-NativeJsonString -Value $inspect.HostConfig.RestartPolicy.Name `
        -Label 'parent authorization retained PostgreSQL restart policy Name'
    Assert-NativeJsonString -Value $inspect.HostConfig.NetworkMode `
        -Label 'parent authorization retained PostgreSQL network mode'

    $configuredPortProperties = @(
        $inspect.HostConfig.PortBindings.PSObject.Properties |
            Where-Object { $null -ne $_.Value }
    )
    $runtimePortProperties = @($inspect.NetworkSettings.Ports.PSObject.Properties)
    $configuredPort = $inspect.HostConfig.PortBindings.'5432/tcp'
    $runtimePort = $inspect.NetworkSettings.Ports.'5432/tcp'
    if ($configuredPort -isnot [Array] -or $runtimePort -isnot [Array]) {
        throw 'Parent authorization PostgreSQL ports are not native JSON arrays.'
    }
    foreach ($entry in @($configuredPort) + @($runtimePort)) {
        Assert-NativeJsonObject -Value $entry `
            -Label 'parent authorization PostgreSQL port entry'
        Assert-ExactProperties -Value $entry `
            -Label 'parent authorization PostgreSQL port entry' `
            -Expected @('HostIp', 'HostPort')
        foreach ($name in @('HostIp', 'HostPort')) {
            Assert-NativeJsonString -Value $entry.$name `
                -Label "parent authorization PostgreSQL port entry $name"
        }
    }
    if ($inspect.Mounts -isnot [Array]) {
        throw 'Parent authorization PostgreSQL mounts are not a native JSON array.'
    }
    $hbaMounts = @($inspect.Mounts | Where-Object {
        [string]$_.Destination -ceq '/etc/postgresql/stratus-pg_hba.conf'
    })
    $dataMounts = @($inspect.Mounts | Where-Object {
        [string]$_.Destination -ceq '/var/lib/postgresql/data'
    })
    if ($hbaMounts.Count -eq 1) {
        Assert-NativeJsonObject -Value $hbaMounts[0] `
            -Label 'parent authorization PostgreSQL HBA mount'
        Assert-ExactProperties -Value $hbaMounts[0] `
            -Label 'parent authorization PostgreSQL HBA mount' `
            -Expected @('Type', 'Source', 'Destination', 'Mode', 'RW', 'Propagation')
        foreach ($name in @('Type', 'Source', 'Destination', 'Mode', 'Propagation')) {
            Assert-NativeJsonString -Value $hbaMounts[0].$name `
                -Label "parent authorization PostgreSQL HBA mount $name"
        }
        Assert-NativeJsonBoolean -Value $hbaMounts[0].RW `
            -Label 'parent authorization PostgreSQL HBA mount RW'
    }
    if ($dataMounts.Count -eq 1) {
        Assert-NativeJsonObject -Value $dataMounts[0] `
            -Label 'parent authorization PostgreSQL data mount'
        Assert-ExactProperties -Value $dataMounts[0] `
            -Label 'parent authorization PostgreSQL data mount' `
            -Expected @(
                'Type', 'Name', 'Driver', 'Source', 'Destination', 'Mode',
                'RW', 'Propagation'
            )
        foreach ($name in @(
            'Type', 'Name', 'Driver', 'Source', 'Destination', 'Mode', 'Propagation'
        )) {
            Assert-NativeJsonString -Value $dataMounts[0].$name `
                -Label "parent authorization PostgreSQL data mount $name"
        }
        Assert-NativeJsonBoolean -Value $dataMounts[0].RW `
            -Label 'parent authorization PostgreSQL data mount RW'
    }
    $hbaSources = @(Get-DockerHostPathAliases -Path (
        Join-Path $absoluteRoot 'ops\postgres\stratus-pg_hba.conf'
    ))
    $networkProperties = @($inspect.NetworkSettings.Networks.PSObject.Properties)
    $actualNetworkNames = @($networkProperties.Name)
    [Array]::Sort($actualNetworkNames, [StringComparer]::Ordinal)
    $expectedNetworkNames = @($productionNetworks.Keys)
    [Array]::Sort($expectedNetworkNames, [StringComparer]::Ordinal)
    $endpointLines = [Collections.Generic.List[string]]::new()
    foreach ($networkName in $expectedNetworkNames) {
        $attachment = $inspect.NetworkSettings.Networks.$networkName
        Assert-NativeJsonObject -Value $attachment `
            -Label "parent authorization PostgreSQL network $networkName"
        Assert-ExactProperties -Value $attachment `
            -Label "parent authorization PostgreSQL network $networkName" `
            -Expected @(
                'IPAMConfig', 'Links', 'Aliases', 'MacAddress', 'DriverOpts',
                'GwPriority', 'NetworkID', 'EndpointID', 'Gateway', 'IPAddress',
                'IPPrefixLen', 'IPv6Gateway', 'GlobalIPv6Address',
                'GlobalIPv6PrefixLen', 'DNSNames'
            )
        if ($attachment.Aliases -isnot [Array] -or
            $attachment.DNSNames -isnot [Array]) {
            throw "Parent authorization PostgreSQL network arrays are not native: $networkName"
        }
        $aliases = [string[]]@($attachment.Aliases)
        $dnsNames = [string[]]@($attachment.DNSNames)
        foreach ($value in @($aliases) + @($dnsNames)) {
            Assert-NativeJsonString -Value $value `
                -Label "parent authorization PostgreSQL network name $networkName"
        }
        foreach ($name in @(
            'NetworkID', 'EndpointID', 'Gateway', 'IPAddress', 'MacAddress',
            'IPv6Gateway', 'GlobalIPv6Address'
        )) {
            Assert-NativeJsonString -Value $attachment.$name `
                -Label "parent authorization PostgreSQL network $networkName $name"
        }
        foreach ($name in @('GwPriority', 'IPPrefixLen', 'GlobalIPv6PrefixLen')) {
            Assert-NativeJsonInteger -Value $attachment.$name `
                -Label "parent authorization PostgreSQL network $networkName $name"
        }
        $expectedAliases = [string[]]@('postgres', 'weatherpoly-postgres-1')
        $expectedDnsNames = [string[]]@(
            $productionPostgresId.Substring(0, 12), 'postgres',
            'weatherpoly-postgres-1'
        )
        [Array]::Sort($aliases, [StringComparer]::Ordinal)
        [Array]::Sort($dnsNames, [StringComparer]::Ordinal)
        [Array]::Sort($expectedAliases, [StringComparer]::Ordinal)
        [Array]::Sort($expectedDnsNames, [StringComparer]::Ordinal)
        $expectedNetwork = $productionNetworks[$networkName]
        if (
            $null -ne $attachment.IPAMConfig -or
            $null -ne $attachment.DriverOpts -or
            $null -ne $attachment.Links -or
            $attachment.NetworkID -cne [string]$expectedNetwork.id -or
            $attachment.GwPriority -ne 0 -or
            ($aliases -join "`n") -cne ($expectedAliases -join "`n") -or
            ($dnsNames -join "`n") -cne ($expectedDnsNames -join "`n") -or
            [string]$attachment.EndpointID -notmatch '^[0-9a-f]{64}$' -or
            $attachment.Gateway -cne [string]$expectedNetwork.gateway -or
            [string]$attachment.IPAddress -notmatch [string]$expectedNetwork.ipv4_pattern -or
            $attachment.IPPrefixLen -ne 16 -or
            [string]$attachment.MacAddress -notmatch '^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$' -or
            -not [string]::IsNullOrEmpty([string]$attachment.IPv6Gateway) -or
            -not [string]::IsNullOrEmpty([string]$attachment.GlobalIPv6Address) -or
            $attachment.GlobalIPv6PrefixLen -ne 0
        ) {
            throw "Parent authorization PostgreSQL network is not exact: $networkName"
        }
        $endpointLines.Add((@(
            $networkName, [string]$attachment.NetworkID,
            [string]$attachment.EndpointID, [string]$attachment.Gateway,
            [string]$attachment.IPAddress, [string]$attachment.IPPrefixLen,
            [string]$attachment.IPv6Gateway,
            [string]$attachment.GlobalIPv6Address,
            [string]$attachment.GlobalIPv6PrefixLen,
            [string]$attachment.MacAddress, ($aliases -join ','),
            ($dnsNames -join ',')
        ) -join '|'))
    }
    $networkBoundaries = Get-ProductionNetworkBoundaries `
        -ExpectedState Running -CollectorType $CollectorType
    foreach ($networkName in $expectedNetworkNames) {
        $containerAttachment = $inspect.NetworkSettings.Networks.$networkName
        $daemonEndpoint = $networkBoundaries.entries.$networkName.postgres_endpoint
        if (
            $daemonEndpoint.EndpointID -cne $containerAttachment.EndpointID -or
            $daemonEndpoint.MacAddress -cne $containerAttachment.MacAddress -or
            $daemonEndpoint.IPv4Address -cne
                "$($containerAttachment.IPAddress)/$($containerAttachment.IPPrefixLen)" -or
            -not [string]::IsNullOrEmpty($daemonEndpoint.IPv6Address)
        ) {
            throw "Parent authorization PostgreSQL endpoint views disagree: $networkName"
        }
    }
    $runningRead = Invoke-BoundedPinnedDockerRead -CollectorType $CollectorType `
        -Arguments @('ps', '-q', '--no-trunc') `
        -Label 'parent authorization running-container inventory'
    if (
        $inspect.Id -cne $productionPostgresId -or
        $inspect.Name -cne '/weatherpoly-postgres-1' -or
        $inspect.Image -cne [string]$ExpectedPostgres.image_id -or
        $inspect.Config.Image -cne [string]$ExpectedPostgres.config_image -or
        $inspect.RestartCount -ne [int]$ExpectedPostgres.restart_count -or
        $inspect.RestartCount -ne 0 -or
        $inspect.HostConfig.RestartPolicy.Name -cne 'no' -or
        $inspect.HostConfig.NetworkMode -cne 'weatherpoly_default' -or
        $inspect.State.Status -cne 'running' -or
        -not $inspect.State.Running -or $inspect.State.Paused -or
        $inspect.State.Restarting -or $inspect.State.OOMKilled -or
        $inspect.State.Dead -or $inspect.State.Pid -le 0 -or
        $inspect.State.ExitCode -ne 0 -or
        $inspect.State.Health.Status -cne 'healthy' -or
        $inspect.State.StartedAt -cne [string]$ExpectedPostgres.started_at -or
        $inspect.State.FinishedAt -cne '0001-01-01T00:00:00Z' -or
        -not [string]::IsNullOrEmpty([string]$inspect.State.Error) -or
        $configuredPortProperties.Count -ne 1 -or
        $runtimePortProperties.Count -ne 1 -or
        @($configuredPort).Count -ne 1 -or @($runtimePort).Count -ne 1 -or
        $configuredPort[0].HostIp -cne '127.0.0.1' -or
        $configuredPort[0].HostPort -cne '5432' -or
        $runtimePort[0].HostIp -cne '127.0.0.1' -or
        $runtimePort[0].HostPort -cne '5432' -or
        @($inspect.Mounts).Count -ne 2 -or $hbaMounts.Count -ne 1 -or
        $hbaMounts[0].Type -cne 'bind' -or [bool]$hbaMounts[0].RW -or
        $hbaMounts[0].Mode -cne 'ro' -or
        ([string]$hbaMounts[0].Source).Replace('\', '/') -notin $hbaSources -or
        $dataMounts.Count -ne 1 -or $dataMounts[0].Type -cne 'volume' -or
        $dataMounts[0].Name -cne 'weatherpoly_stratus-postgres' -or
        -not [bool]$dataMounts[0].RW -or
        $dataMounts[0].Source -cne $productionDataVolumeSource -or
        $networkProperties.Count -ne 2 -or
        ($actualNetworkNames -join "`n") -cne ($expectedNetworkNames -join "`n") -or
        $networkBoundaries.contract_sha256 -cne
            [string]$ExpectedPostgres.network_contract_sha256 -or
        @($runningRead.lines).Count -ne 1 -or
        $runningRead.lines[0] -cne $productionPostgresId
    ) {
        throw 'Parent authorization did not prove the exact running PostgreSQL-only boundary.'
    }
    $mountContract = @(
        [string]$hbaMounts[0].Type, [string]$hbaMounts[0].Source,
        [string]$hbaMounts[0].Destination, [string]$hbaMounts[0].Mode,
        [string]$hbaMounts[0].RW, [string]$hbaMounts[0].Propagation,
        [string]$dataMounts[0].Type, [string]$dataMounts[0].Name,
        [string]$dataMounts[0].Driver, [string]$dataMounts[0].Source,
        [string]$dataMounts[0].Destination, [string]$dataMounts[0].Mode,
        [string]$dataMounts[0].RW, [string]$dataMounts[0].Propagation
    ) -join "`0"
    $engineAfter = Get-BoundedParentDockerEngineBoundary `
        -CollectorType $CollectorType -ExpectedEngine $ExpectedEngine
    if ($engineAfter.contract_sha256 -cne $engineBefore.contract_sha256) {
        throw 'Parent authorization Docker engine drifted across the running proof.'
    }
    return [pscustomobject]@{
        container_id = [string]$inspect.Id
        image_id = [string]$inspect.Image
        config_image = [string]$inspect.Config.Image
        restart_count = [int]$inspect.RestartCount
        started_at = [string]$inspect.State.StartedAt
        finished_at = [string]$inspect.State.FinishedAt
        state = [string]$inspect.State.Status
        oom_killed = [bool]$inspect.State.OOMKilled
        exit_code = [int]$inspect.State.ExitCode
        restart_policy = [string]$inspect.HostConfig.RestartPolicy.Name
        health_status = [string]$inspect.State.Health.Status
        network_mode = [string]$inspect.HostConfig.NetworkMode
        mount_contract_sha256 = Get-Sha256Text -Value ($mountContract + "`n")
        ports_contract_sha256 = Get-Sha256Text -Value "5432/tcp|127.0.0.1|5432`n"
        network_contract_sha256 = [string]$networkBoundaries.contract_sha256
        active_endpoint_sha256 = Get-Sha256Text -Value (
            ($endpointLines.ToArray() -join "`n") + "`n"
        )
        docker_engine_contract_sha256 = [string]$engineAfter.contract_sha256
    }
}

function Get-ParentStartProtocolState {
    param(
        [Parameter(Mandatory = $true)][string] $SessionId,
        [Parameter(Mandatory = $true)][object] $Program,
        [Parameter(Mandatory = $true)][string] $StartFencePath,
        [Parameter(Mandatory = $true)][string] $StartFenceSha256,
        [Parameter(Mandatory = $true)][string] $StartAttemptPath,
        [Parameter(Mandatory = $true)][string] $StartCompletionPath,
        [AllowNull()][string] $ExpectedStartAttemptSha256,
        [AllowNull()][string] $ExpectedStartCompletionSha256,
        [AllowNull()][object] $StartCommandProof
    )

    try {
        $attemptPresent = Test-Path -LiteralPath $StartAttemptPath
        $completionPresent = Test-Path -LiteralPath $StartCompletionPath
        if (-not $attemptPresent) {
            if ($completionPresent -or
                -not [string]::IsNullOrWhiteSpace($ExpectedStartAttemptSha256)) {
                throw 'The durable start attempt is missing or inverted.'
            }
            return [pscustomobject]@{
                status = 'none'; attempt_sha256 = $null
                completion_sha256 = $null; failure = $null
            }
        }
        $attemptDocument = Read-BoundedStrictJsonDocument `
            -LiteralPath $StartAttemptPath `
            -Label 'parent containment start attempt' -MaximumBytes 131072
        $attempt = $attemptDocument.value
        Assert-ExactProperties -Value $attempt -Label 'parent containment start attempt' `
            -Expected @(
                'format', 'session_id', 'owner_pid', 'owner_start_time_utc',
                'start_fence_path', 'start_fence_sha256', 'command', 'issued_at'
            )
        if ($attempt.command -isnot [Array]) {
            throw 'Parent containment start command is not a native JSON array.'
        }
        $attemptCommand = @($attempt.command)
        if (
            [string]::IsNullOrWhiteSpace($ExpectedStartAttemptSha256) -or
            $attemptDocument.sha256 -cne $ExpectedStartAttemptSha256 -or
            $attempt.format -cne 'stratus-successor-transition-start-attempt-v1' -or
            $attempt.session_id -cne $SessionId -or
            $attempt.owner_pid -ne [int]$Program.owner_pid -or
            $attempt.owner_start_time_utc -cne [string]$Program.owner_start_time_utc -or
            -not (Test-ExactPathEqual -Left $attempt.start_fence_path `
                -Right $StartFencePath) -or
            $attempt.start_fence_sha256 -cne $StartFenceSha256 -or
            $attemptCommand.Count -ne 2 -or $attemptCommand[0] -cne 'start' -or
            $attemptCommand[1] -cne $productionPostgresId
        ) {
            throw 'Parent containment start attempt is not exact and process-bound.'
        }
        if (-not $completionPresent) {
            return [pscustomobject]@{
                status = 'unresolved'; attempt_sha256 = [string]$attemptDocument.sha256
                completion_sha256 = $null; failure = $null
            }
        }
        $completionDocument = Read-BoundedStrictJsonDocument `
            -LiteralPath $StartCompletionPath `
            -Label 'parent containment start completion' -MaximumBytes 262144
        $completion = $completionDocument.value
        Assert-ExactProperties -Value $completion `
            -Label 'parent containment start completion' -Expected @(
                'format', 'session_id', 'owner_pid', 'owner_start_time_utc',
                'start_attempt_path', 'start_attempt_sha256', 'outcome',
                'completed_at', 'bounded_start'
            )
        Assert-NativeJsonObject -Value $completion.bounded_start `
            -Label 'parent containment bounded start completion'
        if (
            [string]::IsNullOrWhiteSpace($ExpectedStartCompletionSha256) -or
            $null -eq $StartCommandProof -or
            $completionDocument.sha256 -cne $ExpectedStartCompletionSha256 -or
            $completion.format -cne 'stratus-successor-transition-start-complete-v1' -or
            $completion.session_id -cne $SessionId -or
            $completion.owner_pid -ne [int]$Program.owner_pid -or
            $completion.owner_start_time_utc -cne [string]$Program.owner_start_time_utc -or
            -not (Test-ExactPathEqual -Left $completion.start_attempt_path `
                -Right $StartAttemptPath) -or
            $completion.start_attempt_sha256 -cne $attemptDocument.sha256 -or
            $completion.outcome -cne 'started' -or
            ($completion.bounded_start | ConvertTo-Json -Depth 10 -Compress) -cne
                ($StartCommandProof | ConvertTo-Json -Depth 10 -Compress)
        ) {
            throw 'Parent containment start completion is not an exact positive acknowledgement.'
        }
        return [pscustomobject]@{
            status = 'completed'; attempt_sha256 = [string]$attemptDocument.sha256
            completion_sha256 = [string]$completionDocument.sha256; failure = $null
        }
    }
    catch {
        return [pscustomobject]@{
            status = 'invalid'; attempt_sha256 = $ExpectedStartAttemptSha256
            completion_sha256 = $ExpectedStartCompletionSha256
            failure = $_.Exception.Message
        }
    }
}

function Open-ParentContainmentStartFence {
    param(
        [Parameter(Mandatory = $true)][string] $FencePath,
        [Parameter(Mandatory = $true)][string] $ExpectedSha256
    )

    while ($true) {
        $stream = $null
        try {
            $stream = [IO.FileStream]::new(
                $FencePath, [IO.FileMode]::Open, [IO.FileAccess]::Read,
                [IO.FileShare]::None, 4096, [IO.FileOptions]::SequentialScan
            )
            $observedSha256 = Get-HeldFileStreamSha256 -Stream $stream `
                -Label 'parent containment PostgreSQL start fence'
            return [pscustomobject]@{
                stream = $stream
                observed_sha256 = $observedSha256
                sha256_exact = [bool]($observedSha256 -ceq $ExpectedSha256)
            }
        }
        catch {
            if ($null -ne $stream) { $stream.Dispose() }
            Start-Sleep -Milliseconds 100
        }
    }
}

function Publish-ParentStartRevocation {
    param(
        [Parameter(Mandatory = $true)][string] $RevokePath,
        [Parameter(Mandatory = $true)][string] $SessionId,
        [Parameter(Mandatory = $true)][object] $Program,
        [Parameter(Mandatory = $true)][string] $StartFencePath,
        [Parameter(Mandatory = $true)][string] $StartFenceSha256,
        [Parameter(Mandatory = $true)][string] $Trigger
    )

    $created = $false
    $attemptCount = 0
    while (-not (Test-Path -LiteralPath $RevokePath)) {
        $attemptCount += 1
        $marker = [ordered]@{
            format = 'stratus-successor-transition-start-revoked-v1'
            session_id = $SessionId
            watchdog_pid = [int]$PID
            owner_pid = [int]$Program.owner_pid
            trigger = $Trigger
            revoked_at = [DateTimeOffset]::UtcNow.ToString('o')
            start_fence_path = $StartFencePath
            start_fence_sha256 = $StartFenceSha256
            postgres_start_permitted = $false
        }
        try {
            Write-CreateNewJson -LiteralPath $RevokePath -Value $marker
            $created = $true
        }
        catch {
            if (-not (Test-Path -LiteralPath $RevokePath)) {
                Start-Sleep -Milliseconds 100
            }
        }
    }
    return [pscustomobject]@{
        path = $RevokePath
        present = $true
        created = [bool]$created
        attempt_count = [int]$attemptCount
    }
}

function Invoke-ParentCausalPostgresContainment {
    param(
        [AllowNull()][IO.FileStream] $HeldFence,
        [Parameter(Mandatory = $true)][string] $FencePath,
        [Parameter(Mandatory = $true)][string] $FenceSha256,
        [Parameter(Mandatory = $true)][string] $RevokePath,
        [Parameter(Mandatory = $true)][string] $StartAttemptPath,
        [Parameter(Mandatory = $true)][string] $StartCompletionPath,
        [AllowNull()][string] $ExpectedStartAttemptSha256,
        [AllowNull()][string] $ExpectedStartCompletionSha256,
        [AllowNull()][object] $StartCommandProof,
        [Parameter(Mandatory = $true)][string] $SessionId,
        [Parameter(Mandatory = $true)][object] $Program,
        [Parameter(Mandatory = $true)][object] $CollectorType,
        [Parameter(Mandatory = $true)][object] $ExpectedEngine,
        [Parameter(Mandatory = $true)][object] $ExpectedPostgres,
        [Parameter(Mandatory = $true)][string] $ContainmentPath,
        [Parameter(Mandatory = $true)][string] $Trigger
    )

    $fenceBoundary = $null
    $fenceStream = $HeldFence
    try {
        if ($null -eq $fenceStream) {
            $fenceBoundary = Open-ParentContainmentStartFence `
                -FencePath $FencePath -ExpectedSha256 $FenceSha256
            $fenceStream = $fenceBoundary.stream
        }
        else {
            $observedSha256 = Get-HeldFileStreamSha256 -Stream $fenceStream `
                -Label 'held parent containment PostgreSQL start fence'
            $fenceBoundary = [pscustomobject]@{
                stream = $fenceStream
                observed_sha256 = $observedSha256
                sha256_exact = [bool]($observedSha256 -ceq $FenceSha256)
            }
        }
        $revoke = Publish-ParentStartRevocation -RevokePath $RevokePath `
            -SessionId $SessionId -Program $Program `
            -StartFencePath $FencePath -StartFenceSha256 $FenceSha256 `
            -Trigger $Trigger
        $protocol = Get-ParentStartProtocolState -SessionId $SessionId `
            -Program $Program -StartFencePath $FencePath `
            -StartFenceSha256 $FenceSha256 `
            -StartAttemptPath $StartAttemptPath `
            -StartCompletionPath $StartCompletionPath `
            -ExpectedStartAttemptSha256 $ExpectedStartAttemptSha256 `
            -ExpectedStartCompletionSha256 $ExpectedStartCompletionSha256 `
            -StartCommandProof $StartCommandProof
        $requiresMaterialization = $protocol.status -cne 'completed'
        $materializationObserved = $false
        $stopSucceeded = $false
        $stopAttemptCount = 0
        $stoppedProofCount = 0
        $transitionObservationCount = 0
        $beforeState = 'unobserved'
        $lastError = $null
        $stopProof = $null
        $finalStopped = $null
        while ($true) {
            try {
                $candidate = Get-BoundedParentStoppedPostgresBoundary `
                    -CollectorType $CollectorType -ExpectedEngine $ExpectedEngine `
                    -ExpectedPostgres $ExpectedPostgres
                $stoppedProofCount += 1
                if ($beforeState -ceq 'unobserved') { $beforeState = 'stopped' }
                if (-not $requiresMaterialization -or $stopSucceeded) {
                    $finalStopped = $candidate
                    $lastError = $null
                    break
                }
                $lastError = $null
            }
            catch {
                if ($beforeState -ceq 'unobserved') { $beforeState = 'ambiguous' }
                $transitionObservationCount += 1
                $materializationObserved = $true
                $lastError = $_.Exception.Message
            }
            if ($materializationObserved) {
                $stopAttemptCount += 1
                try {
                    [void](Get-BoundedParentDockerEngineBoundary `
                        -CollectorType $CollectorType -ExpectedEngine $ExpectedEngine)
                    $stopProof = Invoke-BoundedPinnedDockerStop `
                        -ContainerId $productionPostgresId `
                        -ContainerStopTimeoutSeconds $postgresRollbackTimeoutSeconds
                    $stopSucceeded = $true
                    $lastError = $null
                }
                catch {
                    $lastError = $_.Exception.Message
                }
            }
            Start-Sleep -Milliseconds 100
        }
        $containment = [ordered]@{
            format = 'stratus-successor-transition-parent-containment-v1'
            status = 'contained'
            terminal_at = [DateTimeOffset]::UtcNow.ToString('o')
            session_id = $SessionId
            owner_pid = [int]$Program.owner_pid
            trigger = $Trigger
            start_fence_path = $FencePath
            start_fence_sha256 = $FenceSha256
            observed_start_fence_sha256 = [string]$fenceBoundary.observed_sha256
            start_fence_sha256_exact = [bool]$fenceBoundary.sha256_exact
            start_revoke_path = $RevokePath
            start_revoke_present = [bool]$revoke.present
            start_revoke_created = [bool]$revoke.created
            start_revoke_attempt_count = [int]$revoke.attempt_count
            start_attempt_path = $StartAttemptPath
            start_attempt_sha256 = $protocol.attempt_sha256
            start_completion_path = $StartCompletionPath
            start_completion_sha256 = $protocol.completion_sha256
            start_protocol_status = [string]$protocol.status
            causal_materialization_required = [bool]$requiresMaterialization
            materialization_observed = [bool]$materializationObserved
            attempted_stop = [bool]($stopAttemptCount -gt 0)
            docker_stop_succeeded = [bool]$stopSucceeded
            stop_attempt_count = [int]$stopAttemptCount
            stopped_proof_count = [int]$stoppedProofCount
            transition_observation_count = [int]$transitionObservationCount
            before_state = $beforeState
            last_error = $lastError
            stop = $stopProof
            final_stopped = $finalStopped
            authorization = [ordered]@{
                api_stage_permitted = $false
                worker_start_permitted = $false
                safety_control_start_permitted = $false
                live_transition_permitted = $false
                binance_kill_switch_removal_permitted = $false
            }
        }
        Write-CreateNewJson -LiteralPath $ContainmentPath -Value $containment
        $document = Read-BoundedStrictJsonDocument -LiteralPath $ContainmentPath `
            -Label 'parent causal PostgreSQL containment' -MaximumBytes 524288
        return [pscustomobject]@{
            path = $ContainmentPath
            sha256 = [string]$document.sha256
            value = $document.value
        }
    }
    finally {
        if ($null -ne $fenceStream) { $fenceStream.Dispose() }
    }
}

function Invoke-BoundedPinnedDockerStop {
    param(
        [Parameter(Mandatory = $true)][string] $ContainerId,
        [Parameter(Mandatory = $true)][int] $ContainerStopTimeoutSeconds
    )
    if ($ContainerId -notmatch '^[0-9a-f]{64}$') {
        throw 'Bounded PostgreSQL containment received an invalid container identity.'
    }
    if ($ContainerStopTimeoutSeconds -lt 1 -or $ContainerStopTimeoutSeconds -gt 20) {
        throw 'Bounded PostgreSQL containment received an invalid stop timeout.'
    }
    $dockerCli = Get-PinnedApplicationBoundary `
        -LiteralPath $script:dockerExecutable `
        -ExpectedSha256 $script:dockerExecutableSha256 `
        -Label 'bounded PostgreSQL containment'
    $collectorType = Initialize-DockerEventCollectorType
    $method = $collectorType.type.GetMethod(
        'RunBounded',
        [Reflection.BindingFlags]::Public -bor [Reflection.BindingFlags]::Static
    )
    if ($null -eq $method) {
        throw 'The revision-bound collector does not expose bounded containment.'
    }
    $arguments = "--context $approvedDockerContext stop --timeout $ContainerStopTimeoutSeconds $ContainerId"
    $requestedAt = [DateTimeOffset]::UtcNow
    try {
        $result = $method.Invoke($null, @(
            [string]$dockerCli.path,
            [string]$arguments,
            [int]22000,
            [int]4096,
            [int]4096
        ))
    }
    catch {
        $detail = if ($null -ne $_.Exception.InnerException) {
            $_.Exception.InnerException.Message
        }
        else {
            $_.Exception.Message
        }
        throw "The bounded PostgreSQL containment process failed: $detail"
    }
    $completedAt = [DateTimeOffset]::UtcNow
    [void](Get-PinnedApplicationBoundary `
        -LiteralPath $script:dockerExecutable `
        -ExpectedSha256 $script:dockerExecutableSha256 `
        -Label 'bounded PostgreSQL containment completion')
    if ($null -eq $result) {
        throw 'The bounded PostgreSQL containment process returned no result.'
    }
    if (
        -not [bool]$result.ProcessStarted -or
        -not [bool]$result.ProcessExited -or
        [bool]$result.TimedOut -or
        [bool]$result.OutputExceeded -or
        -not [string]::IsNullOrEmpty([string]$result.PumpError) -or
        [int]$result.ExitCode -ne 0 -or
        -not [string]::IsNullOrEmpty([string]$result.Stderr)
    ) {
        throw 'The bounded PostgreSQL containment process timed out, overflowed, or failed.'
    }
    $stdout = ([string]$result.Stdout).Replace("`r`n", "`n")
    if ($stdout.Contains("`r")) {
        throw 'The bounded PostgreSQL containment output is not canonical text.'
    }
    $lines = @($stdout -split "`n" | Where-Object {
        -not [string]::IsNullOrWhiteSpace([string]$_)
    })
    if ($lines.Count -ne 1 -or [string]$lines[0] -cne $ContainerId) {
        throw 'The bounded PostgreSQL containment stopped an unexpected identity.'
    }
    return [pscustomobject]@{
        command = @(
            'stop', '--timeout', [string]$ContainerStopTimeoutSeconds, $ContainerId
        )
        docker_executable_path = [string]$dockerCli.path
        docker_executable_sha256 = [string]$dockerCli.sha256
        process_timeout_milliseconds = 22000
        overall_hard_timeout_milliseconds = 25000
        stdout_maximum_bytes = 4096
        stderr_maximum_bytes = 4096
        requested_at = $requestedAt.ToString('o')
        completed_at = $completedAt.ToString('o')
        exit_code = [int]$result.ExitCode
        output_container_id = [string]$lines[0]
        timed_out = [bool]$result.TimedOut
        output_exceeded = [bool]$result.OutputExceeded
    }
}

function Invoke-BoundedPinnedDockerStart {
    param(
        [Parameter(Mandatory = $true)][string] $ContainerId,
        [Parameter(Mandatory = $true)][object] $CollectorType
    )
    if ($ContainerId -notmatch '^[0-9a-f]{64}$') {
        throw 'Bounded PostgreSQL start received an invalid container identity.'
    }
    $dockerCli = Get-PinnedApplicationBoundary `
        -LiteralPath $script:dockerExecutable `
        -ExpectedSha256 $script:dockerExecutableSha256 `
        -Label 'bounded PostgreSQL start'
    $method = $CollectorType.type.GetMethod(
        'RunBounded',
        [Reflection.BindingFlags]::Public -bor [Reflection.BindingFlags]::Static
    )
    if ($null -eq $method) {
        throw 'The revision-bound collector does not expose bounded PostgreSQL start.'
    }
    $arguments = "--context $approvedDockerContext start $ContainerId"
    $timeoutMilliseconds = [int]($postgresStartWallClockTimeoutSeconds * 1000)
    $requestedAt = [DateTimeOffset]::UtcNow
    try {
        Assert-ParentStageBoundary -Boundary 'retained PostgreSQL start PONR'
        $result = $method.Invoke($null, @(
            [string]$dockerCli.path,
            [string]$arguments,
            $timeoutMilliseconds,
            [int]4096,
            [int]4096
        ))
    }
    catch {
        $detail = if ($null -ne $_.Exception.InnerException) {
            $_.Exception.InnerException.Message
        }
        else {
            $_.Exception.Message
        }
        throw "The bounded PostgreSQL start process failed: $detail"
    }
    $completedAt = [DateTimeOffset]::UtcNow
    [void](Get-PinnedApplicationBoundary `
        -LiteralPath $script:dockerExecutable `
        -ExpectedSha256 $script:dockerExecutableSha256 `
        -Label 'bounded PostgreSQL start completion')
    if ($null -eq $result) {
        throw 'The bounded PostgreSQL start process returned no result.'
    }
    if (
        -not [bool]$result.ProcessStarted -or
        -not [bool]$result.ProcessExited -or
        [bool]$result.TimedOut -or
        [bool]$result.OutputExceeded -or
        -not [string]::IsNullOrEmpty([string]$result.PumpError) -or
        [int]$result.ExitCode -ne 0 -or
        -not [string]::IsNullOrEmpty([string]$result.Stderr)
    ) {
        throw 'The bounded PostgreSQL start process timed out, overflowed, or failed.'
    }
    $stdout = ([string]$result.Stdout).Replace("`r`n", "`n")
    if ($stdout.Contains("`r")) {
        throw 'The bounded PostgreSQL start output is not canonical text.'
    }
    $lines = @($stdout -split "`n" | Where-Object {
        -not [string]::IsNullOrWhiteSpace([string]$_)
    })
    if ($lines.Count -ne 1 -or [string]$lines[0] -cne $ContainerId) {
        throw 'The bounded PostgreSQL start returned an unexpected identity.'
    }
    return [pscustomobject]@{
        command = @('start', $ContainerId)
        docker_executable_path = [string]$dockerCli.path
        docker_executable_sha256 = [string]$dockerCli.sha256
        process_timeout_milliseconds = $timeoutMilliseconds
        stdout_maximum_bytes = 4096
        stderr_maximum_bytes = 4096
        requested_at = $requestedAt.ToString('o')
        completed_at = $completedAt.ToString('o')
        exit_code = [int]$result.ExitCode
        output_container_id = [string]$lines[0]
        timed_out = [bool]$result.TimedOut
        output_exceeded = [bool]$result.OutputExceeded
        process_started = [bool]$result.ProcessStarted
        process_exited = [bool]$result.ProcessExited
        process_id = [int]$result.ProcessId
        kill_issued = [bool]$result.KillIssued
    }
}

function Start-DockerEventWitness {
    $dockerCli = Get-PinnedApplicationBoundary `
        -LiteralPath $script:dockerExecutable `
        -ExpectedSha256 $script:dockerExecutableSha256 `
        -Label 'Docker event witness'
    $tempRoot = Join-Path ([IO.Path]::GetTempPath()) (
        'stratus-successor-events-' + [guid]::NewGuid().ToString('N')
    )
    [void](New-Item -ItemType Directory -Path $tempRoot)
    $stdoutPath = Join-Path $tempRoot 'events.jsonl'
    $stderrPath = Join-Path $tempRoot 'events.stderr'
    $startedAt = [DateTimeOffset]::UtcNow
    $collector = $null
    try {
        $collectorType = Initialize-DockerEventCollectorType
        $collector = [Activator]::CreateInstance($collectorType.type)
        $collector.Start(
            $dockerCli.path,
            ('--context ' + $approvedDockerContext +
                ' events --format "{{json .}}"'),
            $stdoutPath, $stderrPath,
            [int]$dockerEventJournalMaxBytes, [int]65536
        )
        $process = $collector.Process
        $process.Refresh()
        if (
            $process.HasExited -or
            -not (Test-Path -LiteralPath $stdoutPath -PathType Leaf) -or
            -not (Test-Path -LiteralPath $stderrPath -PathType Leaf)
        ) {
            throw 'The Docker event witness did not remain connected.'
        }
        return [pscustomobject]@{
            session_id = [guid]::NewGuid().ToString('N')
            collector = $collector
            process = $process
            process_id = [int]$process.Id
            process_started_at = $process.StartTime.ToUniversalTime().ToString('o')
            requested_at = $startedAt.ToString('o')
            docker_executable_path = [string]$dockerCli.path
            docker_executable_sha256 = [string]$dockerCli.sha256
            docker_executable_bytes = [int64]$dockerCli.bytes
            collector_type_name = [string]$collectorType.type_name
            collector_contract_identity = [string]$collectorType.contract_identity
            collector_template_sha256 = [string]$collectorType.template_sha256
            collector_source_sha256 = [string]$collectorType.source_sha256
            collector_assembly_full_name_sha256 =
                [string]$collectorType.assembly_full_name_sha256
            collector_assembly_is_dynamic = [bool]$collectorType.assembly_is_dynamic
            collector_module_version_id = [string]$collectorType.module_version_id
            stdout_path = $stdoutPath
            stderr_path = $stderrPath
            temp_root = $tempRoot
            prefix_length = [int64]0
            prefix_sha256 = ''
            reconnect_count = 0
            eof_before_seal = $false
            intentionally_stopped = $false
        }
    }
    catch {
        $launchFailure = $_
        $cleanupFailures = [Collections.Generic.List[string]]::new()
        if ($null -ne $collector) {
            try { $collector.Dispose() }
            catch {
                $cleanupFailures.Add(
                    "Docker witness collector cleanup failed: $($_.Exception.Message)"
                )
            }
        }
        if (Test-Path -LiteralPath $tempRoot) {
            try {
                Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction Stop
            }
            catch {
                $cleanupFailures.Add(
                    "Docker witness capture cleanup failed: $($_.Exception.Message)"
                )
            }
        }
        if ($cleanupFailures.Count -gt 0) {
            throw [InvalidOperationException]::new(
                "Docker event witness launch failed. Original: $($launchFailure.Exception.Message) Cleanup: $($cleanupFailures -join ' | ')",
                $launchFailure.Exception
            )
        }
        throw $launchFailure
    }
}

function Stop-DockerEventWitness {
    param(
        [AllowNull()][object] $Witness,
        [switch] $PreserveFiles,
        [switch] $Seal
    )
    if ($null -eq $Witness) {
        return
    }
    $stopFailure = $null
    $cleanupFailures = [Collections.Generic.List[string]]::new()
    try {
        $Witness.process.Refresh()
        if (
            $Witness.process.HasExited -or
            $Witness.collector.StdoutEof -or
            $Witness.collector.StderrEof -or
            $Witness.collector.OutputExceeded -or
            [int64]$Witness.collector.StderrBytes -ne 0 -or
            -not [string]::IsNullOrEmpty([string]$Witness.collector.PumpError)
        ) {
            $Witness.eof_before_seal = $true
        }
        if ($Seal -and (
            $Witness.process.HasExited -or
            $Witness.collector.StdoutEof -or
            $Witness.collector.StderrEof -or
            $Witness.collector.OutputExceeded -or
            [int64]$Witness.collector.StderrBytes -ne 0 -or
            -not [string]::IsNullOrEmpty([string]$Witness.collector.PumpError)
        )) {
            throw 'The Docker event witness reached EOF before the explicit final seal.'
        }
        if (-not $Witness.process.HasExited) {
            $Witness.collector.StopAndWait(5000)
        }
        $Witness.intentionally_stopped = $true
    }
    catch {
        $stopFailure = $_
    }
    if (-not $PreserveFiles) {
        try { $Witness.collector.Dispose() }
        catch {
            $cleanupFailures.Add(
                "collector cleanup failed: $($_.Exception.Message)"
            )
        }
        if (Test-Path -LiteralPath $Witness.temp_root) {
            try {
                Remove-Item -LiteralPath $Witness.temp_root -Recurse -Force `
                    -ErrorAction Stop
            }
            catch {
                $cleanupFailures.Add(
                    "capture cleanup failed: $($_.Exception.Message)"
                )
            }
        }
    }
    if ($null -ne $stopFailure) {
        if ($cleanupFailures.Count -gt 0) {
            throw [InvalidOperationException]::new(
                "Docker event witness stop failed. Original: $($stopFailure.Exception.Message) Cleanup: $($cleanupFailures -join ' | ')",
                $stopFailure.Exception
            )
        }
        throw $stopFailure
    }
    if ($cleanupFailures.Count -gt 0) {
        throw [InvalidOperationException]::new(
            "Docker event witness cleanup failed: $($cleanupFailures -join ' | ')"
        )
    }
}

function Invoke-DockerEventBarrier {
    param(
        [Parameter(Mandatory = $true)][object] $Witness,
        [Parameter(Mandatory = $true)][ValidateSet('prestart', 'final')]
        [string] $Phase
    )
    $barrierName = "stratus-successor-$Phase-barrier-$($Witness.session_id)"
    $barrierLabel = "stratus.successor.event-barrier=$($Witness.session_id):$Phase"
    $barrierId = ''
    $owned = $false
    try {
        $barrierId = Get-OneNativeLine -Label "$Phase event barrier container" -Lines (
            Invoke-Docker -Arguments @(
                'create', '--name', $barrierName, '--network', 'none',
                '--label', $barrierLabel, '--entrypoint', '/bin/false', $ExpectedImageId
            )
        )
        if ($barrierId -notmatch '^[0-9a-f]{64}$' -or $barrierId -ceq $productionPostgresId) {
            throw 'The event barrier did not return an owned exact container ID.'
        }
        $owned = $true
        $inspect = Get-OneJsonObject -Label "$Phase event barrier inspect" -Lines (
            Invoke-Docker -Arguments @('inspect', $barrierId, '--format', '{{json .}}')
        )
        if (
            $inspect.Id -cne $barrierId -or
            $inspect.Name -cne "/$barrierName" -or
            $inspect.Image -cne $ExpectedImageId -or
            $inspect.State.Status -cne 'created' -or
            $inspect.State.Running -or
            $inspect.State.Pid -ne 0 -or
            $inspect.HostConfig.NetworkMode -cne 'none' -or
            $inspect.Config.Labels.'stratus.successor.event-barrier' -cne
                "$($Witness.session_id):$Phase" -or
            @($inspect.Mounts).Count -ne 0
        ) {
            throw 'The event barrier container boundary is not exact.'
        }
    }
    finally {
        if ($owned) {
            $removed = Get-OneNativeLine -Label "$Phase event barrier cleanup" -Lines (
                Invoke-Docker -Arguments @('rm', $barrierId)
            )
            if ($removed -cne $barrierId) {
                throw 'The event barrier cleanup returned the wrong container ID.'
            }
        }
    }
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($dockerEventBarrierTimeoutSeconds)
    do {
        $snapshot = Get-DockerEventJournalSnapshot -Witness $Witness
        $barrierEvents = @($snapshot.events | Where-Object {
            [string](Get-OptionalPropertyValue -InputObject $_ -Name 'Type') -ceq 'container' -and
            [string]$_.Actor.ID -ceq $barrierId
        })
        $createEvents = @($barrierEvents | Where-Object { [string]$_.Action -ceq 'create' })
        $destroyEvents = @($barrierEvents | Where-Object { [string]$_.Action -ceq 'destroy' })
        if ($barrierEvents.Count -eq 2 -and $createEvents.Count -eq 1 -and
            $destroyEvents.Count -eq 1) {
            break
        }
        Start-Sleep -Milliseconds 50
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    if ($barrierEvents.Count -ne 2 -or $createEvents.Count -ne 1 -or
        $destroyEvents.Count -ne 1) {
        throw 'The continuous Docker event witness did not observe the exact barrier.'
    }
    foreach ($event in $barrierEvents) {
        Assert-NativeJsonObject -Value $event.Actor.Attributes `
            -Label "$Phase event barrier attributes"
        if (
            $event.Actor.Attributes.name -cne $barrierName -or
            $event.Actor.Attributes.'stratus.successor.event-barrier' -cne
                "$($Witness.session_id):$Phase"
        ) {
            throw 'The continuous Docker event barrier attributes drifted.'
        }
    }
    $timeNanos = @($barrierEvents | ForEach-Object { [int64]$_.timeNano })
    $createTimeNano = [int64]$createEvents[0].timeNano
    $destroyTimeNano = [int64]$destroyEvents[0].timeNano
    if ($createTimeNano -le 0 -or $destroyTimeNano -le $createTimeNano) {
        throw 'The Docker event barrier ordering is invalid.'
    }
    return [pscustomobject]@{
        phase = $Phase
        container_id = $barrierId
        create_time_nano = $createTimeNano
        destroy_time_nano = $destroyTimeNano
        observed_event_count = [int]$snapshot.event_count
        prefix_length = [int64]$snapshot.complete_byte_length
        prefix_sha256 = [string]$snapshot.complete_sha256
    }
}

function Wait-DockerEventBarrierObservation {
    param(
        [Parameter(Mandatory = $true)][object] $Witness,
        [Parameter(Mandatory = $true)][object] $Barrier
    )

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($dockerEventBarrierTimeoutSeconds)
    do {
        $snapshot = Get-DockerEventJournalSnapshot -Witness $Witness
        $events = @($snapshot.events | Where-Object {
            [string](Get-OptionalPropertyValue -InputObject $_ -Name 'Type') -ceq
                'container' -and [string]$_.Actor.ID -ceq $Barrier.container_id
        })
        $creates = @($events | Where-Object { [string]$_.Action -ceq 'create' })
        $destroys = @($events | Where-Object { [string]$_.Action -ceq 'destroy' })
        if ($events.Count -eq 2 -and $creates.Count -eq 1 -and
            $destroys.Count -eq 1) {
            if (
                [int64]$creates[0].timeNano -ne [int64]$Barrier.create_time_nano -or
                [int64]$destroys[0].timeNano -ne [int64]$Barrier.destroy_time_nano
            ) {
                throw 'The overlapping Docker witnesses disagree on barrier time.'
            }
            return
        }
        Start-Sleep -Milliseconds 25
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw 'The primary Docker witness did not observe the authorization overlap barrier.'
}

function Assert-DockerEventContract {
    param(
        [Parameter(Mandatory = $true)][object] $Witness,
        [Parameter(Mandatory = $true)][object] $InitialBarrier,
        [Parameter(Mandatory = $true)][object] $FinalBarrier,
        [Parameter(Mandatory = $true)][DateTimeOffset] $ExpectedStartedAt
    )
    $dockerCli = Get-PinnedApplicationBoundary `
        -LiteralPath $script:dockerExecutable `
        -ExpectedSha256 $script:dockerExecutableSha256 `
        -Label 'sealed Docker event witness'
    if (
        $Witness.docker_executable_path -cne $dockerCli.path -or
        $Witness.docker_executable_sha256 -cne $dockerCli.sha256 -or
        $Witness.docker_executable_bytes -ne $dockerCli.bytes
    ) {
        throw 'The Docker event witness executable identity drifted.'
    }
    $snapshot = Get-DockerEventJournalSnapshot -Witness $Witness -AllowStopped
    if (
        -not [bool]$Witness.intentionally_stopped -or
        -not $Witness.process.HasExited -or
        -not [bool]$Witness.collector.StdoutEof -or
        -not [bool]$Witness.collector.StderrEof -or
        [bool]$Witness.collector.OutputExceeded -or
        [int64]$Witness.collector.StderrBytes -ne 0 -or
        -not [string]::IsNullOrEmpty([string]$Witness.collector.PumpError) -or
        $snapshot.has_partial_line -or
        [int64]$Witness.collector.StdoutBytes -ne $snapshot.complete_byte_length -or
        [string]$Witness.collector.StdoutSha256 -cne $snapshot.complete_sha256
    ) {
        throw 'The Docker event journal was not stably sealed on a line boundary.'
    }
    $postgresStarts = [Collections.Generic.List[object]]::new()
    $postgresUpdates = [Collections.Generic.List[object]]::new()
    $healthyStatusEvents = [Collections.Generic.List[object]]::new()
    $volumeMounts = [Collections.Generic.List[object]]::new()
    $networkConnectIds = [Collections.Generic.List[string]]::new()
    $networkConnectEvents = [Collections.Generic.List[object]]::new()
    $execGroups = @{}
    $allowedBarrierIds = @($InitialBarrier.container_id, $FinalBarrier.container_id)
    foreach ($event in $snapshot.events) {
        $type = Get-OptionalPropertyValue -InputObject $event -Name 'Type'
        $action = Get-OptionalPropertyValue -InputObject $event -Name 'Action'
        $actor = Get-OptionalPropertyValue -InputObject $event -Name 'Actor'
        $timeNano = Get-OptionalPropertyValue -InputObject $event -Name 'timeNano'
        Assert-NativeJsonString -Value $type -Label 'continuous Docker event Type'
        Assert-NativeJsonString -Value $action -Label 'continuous Docker event Action'
        Assert-NativeJsonObject -Value $actor -Label 'continuous Docker event Actor'
        Assert-NativeJsonInteger -Value $timeNano -Label 'continuous Docker event timeNano'
        Assert-NativeJsonString -Value $actor.ID -Label 'continuous Docker event Actor.ID'
        if ($type -ceq 'container' -and $actor.ID -cin $allowedBarrierIds) {
            if ($action -cnotin @('create', 'destroy')) {
                throw 'A barrier container emitted an unexpected Docker event.'
            }
            continue
        }
        if ($type -ceq 'container' -and $actor.ID -ceq $productionPostgresId) {
            $baseAction = ([string]$action -split ':', 2)[0]
            if ($baseAction -ceq 'start') {
                Assert-NativeJsonObject -Value $actor.Attributes `
                    -Label 'PostgreSQL start event attributes'
                if ($action -cne 'start' -or
                    $actor.Attributes.name -cne 'weatherpoly-postgres-1' -or
                    $actor.Attributes.image -cne $postgres16Image) {
                    throw 'The retained PostgreSQL start event identity drifted.'
                }
                $postgresStarts.Add($event)
                continue
            }
            if ($baseAction -ceq 'update') {
                Assert-NativeJsonObject -Value $actor.Attributes `
                    -Label 'PostgreSQL update event attributes'
                if ($action -cne 'update' -or
                    $actor.Attributes.name -cne 'weatherpoly-postgres-1' -or
                    $actor.Attributes.image -cne $postgres16Image) {
                    throw 'The retained PostgreSQL update event identity drifted.'
                }
                $postgresUpdates.Add($event)
                continue
            }
            if ($baseAction -cin @('exec_create', 'exec_start', 'exec_die')) {
                Assert-NativeJsonObject -Value $actor.Attributes `
                    -Label 'PostgreSQL exec event attributes'
                $execId = [string](Get-OptionalPropertyValue `
                    -InputObject $actor.Attributes -Name 'execID')
                if ($execId -notmatch '^[0-9a-f]{64}$') {
                    throw 'PostgreSQL exec event did not have an exact execID.'
                }
                if (-not $execGroups.ContainsKey($execId)) {
                    $execGroups[$execId] = [Collections.Generic.List[object]]::new()
                }
                $execGroups[$execId].Add([pscustomobject]@{
                    base_action = $baseAction
                    action = [string]$action
                    attributes = $actor.Attributes
                    time_nano = [int64]$timeNano
                })
                continue
            }
            if ($baseAction -ceq 'health_status') {
                Assert-NativeJsonObject -Value $actor.Attributes `
                    -Label 'PostgreSQL health-status event attributes'
                if (
                    $action -cne 'health_status: healthy' -or
                    $actor.Attributes.name -cne 'weatherpoly-postgres-1' -or
                    $actor.Attributes.image -cne $postgres16Image
                ) {
                    throw 'PostgreSQL emitted a non-healthy or non-exact health-status event.'
                }
                $healthyStatusEvents.Add($event)
                continue
            }
            throw "Retained PostgreSQL emitted a forbidden Docker event: $action"
        }
        if ($type -ceq 'network') {
            Assert-NativeJsonObject -Value $actor.Attributes `
                -Label 'continuous Docker network event attributes'
            $expectedNetworkName = @($productionNetworks.GetEnumerator() |
                Where-Object { [string]$_.Value.id -ceq [string]$actor.ID }
            )
            if (
                $action -cne 'connect' -or
                [string]$actor.Attributes.container -cne $productionPostgresId -or
                [string]$actor.ID -cnotin @($productionNetworks.Values.id) -or
                $expectedNetworkName.Count -ne 1 -or
                [string]$actor.Attributes.name -cne [string]$expectedNetworkName[0].Key -or
                [string]$actor.Attributes.type -cne 'bridge'
            ) {
                throw 'The PostgreSQL network event contract drifted.'
            }
            $networkConnectIds.Add([string]$actor.ID)
            $networkConnectEvents.Add($event)
            continue
        }
        if ($type -ceq 'volume') {
            Assert-NativeJsonObject -Value $actor.Attributes `
                -Label 'continuous Docker volume event attributes'
            Assert-ExactProperties -Value $actor.Attributes -Expected @(
                'container', 'destination', 'driver', 'propagation', 'read/write'
            ) -Label 'continuous Docker volume event attributes'
            if (
                $action -cne 'mount' -or
                [string]$actor.ID -cne 'weatherpoly_stratus-postgres' -or
                [string]$actor.Attributes.container -cne $productionPostgresId -or
                [string]$actor.Attributes.destination -cne '/var/lib/postgresql/data' -or
                [string]$actor.Attributes.driver -cne 'local' -or
                -not [string]::IsNullOrEmpty([string]$actor.Attributes.propagation) -or
                [string]$actor.Attributes.'read/write' -cne 'true'
            ) {
                throw 'The retained PostgreSQL data-volume mount event drifted.'
            }
            $volumeMounts.Add($event)
            continue
        }
        throw 'The continuous Docker witness observed an unexpected global event.'
    }
    if ($postgresStarts.Count -ne 1) {
        throw 'Transition requires exactly one retained PostgreSQL start event.'
    }
    if ($postgresUpdates.Count -ne 1) {
        throw 'Transition requires exactly one retained PostgreSQL restart-policy update event.'
    }
    if ($healthyStatusEvents.Count -ne 1) {
        throw 'Transition requires exactly one healthy PostgreSQL status event.'
    }
    if ($volumeMounts.Count -ne 1) {
        throw 'Transition requires exactly one retained PostgreSQL data-volume mount event.'
    }
    $startTimeNano = [int64]$postgresStarts[0].timeNano
    $updateTimeNano = [int64]$postgresUpdates[0].timeNano
    $healthyTimeNano = [int64]$healthyStatusEvents[0].timeNano
    $volumeMountTimeNano = [int64]$volumeMounts[0].timeNano
    $startMilliseconds = [int64]([Math]::Floor(([double]$startTimeNano) / 1000000.0))
    if (
        [Math]::Abs($startMilliseconds - $ExpectedStartedAt.ToUnixTimeMilliseconds()) -gt 1000 -or
        $startTimeNano -le [int64]$InitialBarrier.destroy_time_nano -or
        $startTimeNano -ge [int64]$FinalBarrier.create_time_nano -or
        $updateTimeNano -le [int64]$InitialBarrier.destroy_time_nano -or
        $updateTimeNano -ge $volumeMountTimeNano -or
        $volumeMountTimeNano -le [int64]$InitialBarrier.destroy_time_nano -or
        $volumeMountTimeNano -ge $startTimeNano -or
        $healthyTimeNano -le $startTimeNano -or
        $healthyTimeNano -ge [int64]$FinalBarrier.create_time_nano
    ) {
        throw 'The witnessed PostgreSQL start is outside the exact transition barriers.'
    }
    $actualConnectIds = $networkConnectIds.ToArray()
    [Array]::Sort($actualConnectIds, [StringComparer]::Ordinal)
    $expectedConnectIds = [string[]]@($productionNetworks.Values.id)
    [Array]::Sort($expectedConnectIds, [StringComparer]::Ordinal)
    if (($actualConnectIds -join "`n") -cne ($expectedConnectIds -join "`n")) {
        throw 'Transition did not witness exactly one connect to each production network.'
    }
    foreach ($connectEvent in $networkConnectEvents) {
        if (
            [int64]$connectEvent.timeNano -le $volumeMountTimeNano -or
            [int64]$connectEvent.timeNano -ge $startTimeNano
        ) {
            throw 'PostgreSQL volume/connect/start event ordering is invalid.'
        }
    }
    $gateExecCount = 0
    $healthExecCount = 0
    $gateClasses = [ordered]@{
        sha256 = 0
        reload = 0
        fresh_hba = 0
        snapshot = 0
    }
    $gateSequence = [Collections.Generic.List[object]]::new()
    foreach ($execId in @($execGroups.Keys)) {
        $group = @($execGroups[$execId])
        $baseActions = @($group.base_action)
        [Array]::Sort($baseActions, [StringComparer]::Ordinal)
        if (($baseActions -join "`n") -cne "exec_create`nexec_die`nexec_start") {
            throw 'A PostgreSQL exec did not have one exact create/start/die triplet.'
        }
        $createEvent = @($group | Where-Object {
            $_.base_action -ceq 'exec_create'
        })[0]
        $startEvent = @($group | Where-Object {
            $_.base_action -ceq 'exec_start'
        })[0]
        $dieEvent = @($group | Where-Object {
            $_.base_action -ceq 'exec_die'
        })[0]
        $createAction = [string]$createEvent.action
        $dieAttributes = $dieEvent.attributes
        $exitCode = [string](Get-OptionalPropertyValue `
            -InputObject $dieAttributes -Name 'exitCode')
        if (
            $exitCode -cne '0' -or
            [int64]$createEvent.time_nano -le $startTimeNano -or
            [int64]$createEvent.time_nano -ge [int64]$startEvent.time_nano -or
            [int64]$startEvent.time_nano -ge [int64]$dieEvent.time_nano -or
            [int64]$dieEvent.time_nano -ge [int64]$FinalBarrier.create_time_nano
        ) {
            throw 'A PostgreSQL exec did not have exact successful in-barrier event ordering.'
        }
        if ($createAction -ceq
            'exec_create: /bin/sh -c pg_isready -U stratus -d stratus') {
            $healthExecCount += 1
        }
        elseif ($createAction -ceq
            'exec_create: sha256sum /etc/postgresql/stratus-pg_hba.conf') {
            $gateClasses.sha256 += 1
            $gateExecCount += 1
            $gateSequence.Add([pscustomobject]@{
                class = 'sha256'
                create_time_nano = [int64]$createEvent.time_nano
            })
        }
        elseif ($createAction -match 'pg_reload_conf\(\)::text') {
            $gateClasses.reload += 1
            $gateExecCount += 1
            $gateSequence.Add([pscustomobject]@{
                class = 'reload'
                create_time_nano = [int64]$createEvent.time_nano
            })
        }
        elseif ($createAction -match "SELECT 'META\|") {
            $gateClasses.fresh_hba += 1
            $gateExecCount += 1
            $gateSequence.Add([pscustomobject]@{
                class = 'fresh_hba'
                create_time_nano = [int64]$createEvent.time_nano
            })
        }
        elseif ($createAction -match
            'BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY') {
            $gateClasses.snapshot += 1
            $gateExecCount += 1
            $gateSequence.Add([pscustomobject]@{
                class = 'snapshot'
                create_time_nano = [int64]$createEvent.time_nano
            })
        }
        else {
            throw 'The witness observed an unowned PostgreSQL exec command.'
        }
    }
    if (
        $gateExecCount -ne $script:transitionExpectedPostgresExecCount -or
        $gateExecCount -ne 8 -or
        $gateClasses.sha256 -ne 3 -or
        $gateClasses.reload -ne 1 -or
        $gateClasses.fresh_hba -ne 2 -or
        $gateClasses.snapshot -ne 2 -or
        $healthExecCount -lt 1 -or $healthExecCount -gt 64
    ) {
        throw 'PostgreSQL exec event counts do not match the exact transition commands.'
    }
    $orderedGateSequence = @($gateSequence.ToArray() | Sort-Object `
        -Property @{ Expression = { [int64]$_.create_time_nano } })
    $expectedGateSequence = @(
        'sha256', 'reload', 'fresh_hba', 'sha256',
        'snapshot', 'snapshot', 'fresh_hba', 'sha256'
    )
    if (
        $orderedGateSequence.Count -ne $expectedGateSequence.Count -or
        (@($orderedGateSequence.class) -join "`n") -cne
            ($expectedGateSequence -join "`n")
    ) {
        throw 'PostgreSQL gate exec commands did not occur in the exact reviewed sequence.'
    }
    for ($sequenceIndex = 1; $sequenceIndex -lt $orderedGateSequence.Count;
        $sequenceIndex += 1) {
        if (
            [int64]$orderedGateSequence[$sequenceIndex].create_time_nano -le
                [int64]$orderedGateSequence[$sequenceIndex - 1].create_time_nano
        ) {
            throw 'PostgreSQL gate exec sequence timeNano values are not strictly ordered.'
        }
    }
    return [pscustomobject]@{
        session_id = [string]$Witness.session_id
        process_id = [int]$Witness.process_id
        process_started_at = [string]$Witness.process_started_at
        docker_executable_path = [string]$dockerCli.path
        docker_executable_sha256 = [string]$dockerCli.sha256
        docker_executable_bytes = [int64]$dockerCli.bytes
        collector_type_name = [string]$Witness.collector_type_name
        collector_contract_identity = [string]$Witness.collector_contract_identity
        collector_template_sha256 = [string]$Witness.collector_template_sha256
        collector_source_sha256 = [string]$Witness.collector_source_sha256
        collector_assembly_full_name_sha256 =
            [string]$Witness.collector_assembly_full_name_sha256
        collector_assembly_is_dynamic = [bool]$Witness.collector_assembly_is_dynamic
        collector_module_version_id = [string]$Witness.collector_module_version_id
        journal_bytes = [int64]$snapshot.complete_byte_length
        journal_sha256 = [string]$snapshot.complete_sha256
        event_count = [int]$snapshot.event_count
        postgres_start_count = 1
        postgres_start_time_nano = $startTimeNano
        postgres_restart_policy_update_count = 1
        postgres_restart_policy_update_time_nano = $updateTimeNano
        postgres_attach_count = 0
        network_connect_count = $actualConnectIds.Count
        network_ids = $actualConnectIds
        data_volume_mount_count = 1
        data_volume_mount_time_nano = $volumeMountTimeNano
        gate_exec_triplets = $gateExecCount
        gate_exec_classes = [pscustomobject]$gateClasses
        gate_exec_sequence = $orderedGateSequence
        health_exec_triplets = $healthExecCount
        healthy_status_event_count = 1
        healthy_status_time_nano = $healthyTimeNano
        reconnect_count = 0
        eof_before_seal = $false
        truncated = $false
        initial_barrier = $InitialBarrier
        final_barrier = $FinalBarrier
    }
}

function Invoke-BoundedAuthorizationEventBarrier {
    param(
        [Parameter(Mandatory = $true)][object] $Witness,
        [Parameter(Mandatory = $true)][object] $CollectorType
    )

    $barrierName = "stratus-successor-authorization-barrier-$($Witness.session_id)"
    $barrierLabel = "stratus.successor.event-barrier=$($Witness.session_id):authorization"
    $barrierId = ''
    $owned = $false
    try {
        $create = Invoke-BoundedPinnedDockerRead -CollectorType $CollectorType `
            -Arguments @(
                'create', '--name', $barrierName, '--network', 'none',
                '--label', $barrierLabel, '--entrypoint', '/bin/false',
                $ExpectedImageId
            ) -Label 'authorization event barrier create'
        $barrierId = Get-OneNativeLine `
            -Label 'authorization event barrier container' -Lines $create.lines
        if ($barrierId -notmatch '^[0-9a-f]{64}$' -or
            $barrierId -ceq $productionPostgresId) {
            throw 'The authorization event barrier returned an invalid identity.'
        }
        $owned = $true
        $inspectRead = Invoke-BoundedPinnedDockerRead -CollectorType $CollectorType `
            -Arguments @('inspect', $barrierId, '--format', '{{json .}}') `
            -Label 'authorization event barrier inspect'
        $inspect = Get-OneJsonObject `
            -Label 'authorization event barrier inspect' -Lines $inspectRead.lines
        if (
            $inspect.Id -cne $barrierId -or
            $inspect.Name -cne "/$barrierName" -or
            $inspect.Image -cne $ExpectedImageId -or
            $inspect.State.Status -cne 'created' -or
            $inspect.State.Running -or $inspect.State.Pid -ne 0 -or
            $inspect.HostConfig.NetworkMode -cne 'none' -or
            $inspect.Config.Labels.'stratus.successor.event-barrier' -cne
                "$($Witness.session_id):authorization" -or
            @($inspect.Mounts).Count -ne 0
        ) {
            throw 'The authorization event barrier boundary is not exact.'
        }
    }
    finally {
        if ($owned) {
            $remove = Invoke-BoundedPinnedDockerRead -CollectorType $CollectorType `
                -Arguments @('rm', $barrierId) `
                -Label 'authorization event barrier cleanup'
            $removedId = Get-OneNativeLine `
                -Label 'authorization event barrier cleanup' -Lines $remove.lines
            if ($removedId -cne $barrierId) {
                throw 'The authorization event barrier cleanup returned the wrong identity.'
            }
        }
    }
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($dockerEventBarrierTimeoutSeconds)
    do {
        $snapshot = Get-DockerEventJournalSnapshot -Witness $Witness
        $events = @($snapshot.events | Where-Object {
            [string](Get-OptionalPropertyValue -InputObject $_ -Name 'Type') -ceq
                'container' -and [string]$_.Actor.ID -ceq $barrierId
        })
        $createEvents = @($events | Where-Object { [string]$_.Action -ceq 'create' })
        $destroyEvents = @($events | Where-Object { [string]$_.Action -ceq 'destroy' })
        if ($createEvents.Count -eq 1 -and $destroyEvents.Count -eq 1) {
            return [pscustomobject]@{
                container_id = $barrierId
                create_time_nano = [int64]$createEvents[0].timeNano
                destroy_time_nano = [int64]$destroyEvents[0].timeNano
                observed_prefix_bytes = [int64]$snapshot.complete_byte_length
                observed_prefix_sha256 = [string]$snapshot.complete_sha256
            }
        }
        Start-Sleep -Milliseconds 25
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw 'The Docker authorization witness did not observe its exact final barrier.'
}

function Assert-AuthorizationDockerEventContract {
    param(
        [Parameter(Mandatory = $true)][object] $Witness,
        [Parameter(Mandatory = $true)][object] $InitialBarrier,
        [Parameter(Mandatory = $true)][object] $FinalBarrier
    )

    $snapshot = Get-DockerEventJournalSnapshot -Witness $Witness -AllowStopped
    if (
        -not [bool]$Witness.intentionally_stopped -or
        -not $Witness.process.HasExited -or $snapshot.has_partial_line -or
        -not [string]::IsNullOrEmpty([string]$Witness.collector.PumpError) -or
        -not [bool]$Witness.collector.StdoutEof -or
        -not [bool]$Witness.collector.StderrEof -or
        [bool]$Witness.collector.OutputExceeded -or
        [int64]$Witness.collector.StderrBytes -ne 0 -or
        [int]$Witness.reconnect_count -ne 0 -or
        [bool]$Witness.eof_before_seal -or
        [bool]$snapshot.truncated -or
        [int64]$Witness.collector.StdoutBytes -ne $snapshot.complete_byte_length -or
        [string]$Witness.collector.StdoutSha256 -cne $snapshot.complete_sha256
    ) {
        throw 'The authorization Docker event journal was not stably sealed.'
    }
    $allowed = @($InitialBarrier.container_id, $FinalBarrier.container_id)
    $counts = @{}
    foreach ($id in $allowed) {
        $counts["$id|create"] = 0
        $counts["$id|destroy"] = 0
    }
    foreach ($event in @($snapshot.events)) {
        Assert-NativeJsonString -Value $event.Type `
            -Label 'authorization Docker event Type'
        Assert-NativeJsonString -Value $event.Action `
            -Label 'authorization Docker event Action'
        Assert-NativeJsonObject -Value $event.Actor `
            -Label 'authorization Docker event Actor'
        Assert-NativeJsonString -Value $event.Actor.ID `
            -Label 'authorization Docker event Actor.ID'
        Assert-NativeJsonInteger -Value $event.timeNano `
            -Label 'authorization Docker event timeNano'
        $key = "$([string]$event.Actor.ID)|$([string]$event.Action)"
        if ($event.Type -cne 'container' -or -not $counts.ContainsKey($key)) {
            throw 'The authorization witness observed an unexpected Docker event.'
        }
        $counts[$key] += 1
    }
    foreach ($key in @($counts.Keys)) {
        if ($counts[$key] -ne 1) {
            throw 'The authorization witness barrier lifecycle is not exact.'
        }
    }
    if (
        [int64]$InitialBarrier.create_time_nano -ge
            [int64]$InitialBarrier.destroy_time_nano -or
        [int64]$InitialBarrier.destroy_time_nano -ge
            [int64]$FinalBarrier.create_time_nano -or
        [int64]$FinalBarrier.create_time_nano -ge
            [int64]$FinalBarrier.destroy_time_nano
    ) {
        throw 'The authorization witness barriers are not strictly ordered.'
    }
    return [pscustomobject]@{
        format = 'stratus-successor-transition-authorization-witness-v1'
        session_id = [string]$Witness.session_id
        process_id = [int]$Witness.process_id
        process_started_at = [string]$Witness.process_started_at
        docker_executable_path = [string]$Witness.docker_executable_path
        docker_executable_sha256 = [string]$Witness.docker_executable_sha256
        reconnect_count = [int]$Witness.reconnect_count
        eof_before_seal = [bool]$Witness.eof_before_seal
        truncated = [bool]$snapshot.truncated
        journal_bytes = [int64]$snapshot.complete_byte_length
        journal_sha256 = [string]$snapshot.complete_sha256
        event_count = [int]@($snapshot.events).Count
        initial_barrier_id = [string]$InitialBarrier.container_id
        initial_barrier_destroy_time_nano = [int64]$InitialBarrier.destroy_time_nano
        final_barrier_id = [string]$FinalBarrier.container_id
        final_barrier_create_time_nano = [int64]$FinalBarrier.create_time_nano
        final_barrier_destroy_time_nano = [int64]$FinalBarrier.destroy_time_nano
        unexpected_event_count = 0
    }
}

function Assert-FinalLiveBoundary {
    param(
        [Parameter(Mandatory = $true)][object] $InitialPostgres,
        [Parameter(Mandatory = $true)][object] $InitialDockerEngine,
        [Parameter(Mandatory = $true)][object] $InitialHba,
        [Parameter(Mandatory = $true)][DateTimeOffset] $PreStartExpiresAt
    )
    $finalPostgres = Get-PostgresBoundary -ExpectedState Running `
        -ExpectedRestartPolicy 'no'
    foreach ($name in @(
        'container_id', 'image_id', 'config_image', 'restart_count',
        'started_at', 'finished_at', 'state', 'oom_killed', 'exit_code',
        'restart_policy', 'health_status', 'network_mode',
        'mount_contract_sha256', 'ports_contract_sha256',
        'network_contract_sha256', 'active_endpoint_sha256'
    )) {
        if ($finalPostgres.$name -cne $InitialPostgres.$name) {
            throw 'Retained PostgreSQL identity or network endpoints drifted during Live proof.'
        }
    }
    $hbaPath = Join-Path $absoluteRoot 'ops\postgres\stratus-pg_hba.conf'
    [void](Assert-RegularFile -LiteralPath $hbaPath)
    $freshHbaState = Get-FreshHbaState -Label 'final fresh HBA state'
    foreach ($name in @(
        'conf_load_time', 'postmaster_started_at', 'hba_file',
        'inventory_sha256', 'inventory_line_count',
        'inventory_rule_count', 'inventory_errors'
    )) {
        if ($freshHbaState.$name -cne $InitialHba.fresh_loaded_state.$name) {
            throw 'Loaded PostgreSQL HBA generation drifted after database snapshots.'
        }
    }
    $finalFreshBackendStartedAt = ConvertTo-RequiredUtcTime `
        -Value $freshHbaState.backend_started_at -Label 'final fresh HBA backend start'
    $reloadAcknowledgedAt = ConvertTo-RequiredUtcTime `
        -Value $InitialHba.reload_acknowledged_at -Label 'accepted HBA reload time'
    if ($finalFreshBackendStartedAt -lt $reloadAcknowledgedAt) {
        throw 'Final fresh PostgreSQL session predates the accepted HBA reload.'
    }
    $hostLockBoundary = Get-HeldHbaReadBoundary
    $hostSha256 = [string]$hostLockBoundary.sha256
    $mountedSha256 = Get-MountedHbaSha256
    if ($hostSha256 -cne $fixedHbaSha256 -or $mountedSha256 -cne $fixedHbaSha256) {
        throw 'HBA bytes drifted after the successor database snapshots.'
    }
    $finalDockerEngine = Get-DockerEngineBoundary
    if ($finalDockerEngine.contract_sha256 -cne $InitialDockerEngine.contract_sha256) {
        throw 'Docker context or engine identity drifted during the transition.'
    }
    $finalTopology = Assert-ExclusiveRuntimeTopology -PostgresState Running
    $checkedAt = [DateTimeOffset]::UtcNow
    if ($checkedAt -gt $PreStartExpiresAt.AddSeconds($clockSkewSeconds)) {
        throw 'Successor PreStart authorization expired before final acceptance.'
    }
    return [pscustomobject]@{
        checked_at = $checkedAt.ToString('o')
        postgres = $finalPostgres
        topology = $finalTopology
        docker_engine = $finalDockerEngine
        hba_host_sha256 = $hostSha256
        hba_host_read_lock_held = [bool]$hostLockBoundary.held
        hba_host_read_lock_path = [string]$hostLockBoundary.path
        hba_mounted_sha256 = $mountedSha256
        fresh_hba_state = $freshHbaState
        endpoints_unchanged = $true
    }
}

function Get-StrictLiveSnapshot {
    $queryCommand = @'
PGPASSWORD="$POSTGRES_PASSWORD" exec psql -XAtq --set=ON_ERROR_STOP=1 --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --command="
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
WITH expected_indexes(
  index_name, table_name, table_regclass, key_names, key_attnums, opclass_names,
  collation_names, is_unique, normalized_predicate
) AS (
  VALUES
    ('ix_accuracy_predictions_eligible_expert_ts'::text, 'accuracy_predictions'::text, 'public.accuracy_predictions'::text, ARRAY['expert','ts']::text[], ARRAY[4,3]::smallint[], ARRAY['pg_catalog.text_ops','pg_catalog.timestamptz_ops']::text[], ARRAY['pg_catalog.default','']::text[], false, 'evidence_eligible IS TRUE'::text),
    ('ix_accuracy_predictions_eligible_expert_version_market_ts'::text, 'accuracy_predictions'::text, 'public.accuracy_predictions'::text, ARRAY['expert','expert_version','market_id','ts']::text[], ARRAY[4,5,2,3]::smallint[], ARRAY['pg_catalog.text_ops','pg_catalog.text_ops','pg_catalog.int8_ops','pg_catalog.timestamptz_ops']::text[], ARRAY['pg_catalog.default','pg_catalog.default','','']::text[], false, 'evidence_eligible IS TRUE'::text),
    ('ix_model_skill_window_evaluated'::text, 'model_skill_reports'::text, 'public.model_skill_reports'::text, ARRAY['window_days','evaluated_at']::text[], ARRAY[7,18]::smallint[], ARRAY['pg_catalog.int2_ops','pg_catalog.timestamptz_ops']::text[], ARRAY['','']::text[], false, ''::text),
    ('ix_signals_pending_stop_cleanup'::text, 'signals'::text, 'public.signals'::text, ARRAY['id']::text[], ARRAY[1]::smallint[], ARRAY['pg_catalog.int8_ops']::text[], ARRAY['']::text[], false, 'status = ''pending''::signal_status'::text),
    ('ix_orders_live_open_stop_cleanup'::text, 'orders'::text, 'public.orders'::text, ARRAY['id']::text[], ARRAY[1]::smallint[], ARRAY['pg_catalog.int8_ops']::text[], ARRAY['']::text[], false, 'mode = ''live''::execution_mode AND (status::text = ANY (ARRAY[''open''::character varying, ''pending''::character varying]::text[]))'::text),
    ('uq_paper_canary_single_active_lease'::text, 'paper_canary_safety_leases'::text, 'public.paper_canary_safety_leases'::text, ARRAY['state']::text[], ARRAY[12]::smallint[], ARRAY['pg_catalog.text_ops']::text[], ARRAY['pg_catalog.default']::text[], true, 'state::text = ''active''::text'::text),
    ('ix_paper_canary_active_expiry'::text, 'paper_canary_safety_leases'::text, 'public.paper_canary_safety_leases'::text, ARRAY['expires_at','id']::text[], ARRAY[16,1]::smallint[], ARRAY['pg_catalog.timestamptz_ops','pg_catalog.int8_ops']::text[], ARRAY['','']::text[], false, 'state::text = ''active''::text'::text)
), actual_indexes AS (
  SELECT
    index_relation.relname::text AS index_name,
    table_relation.relname::text AS table_name,
    table_relation.oid AS table_oid,
    index_namespace.nspname::text AS index_namespace,
    table_namespace.nspname::text AS table_namespace,
    access_method.amname::text AS access_method,
    ARRAY(
      SELECT attribute.attname::text
      FROM generate_series(0, index_row.indnkeyatts - 1) AS position
      JOIN pg_attribute attribute
        ON attribute.attrelid=index_row.indrelid
       AND attribute.attnum=index_row.indkey[position]
      ORDER BY position
    )::text[] AS key_names,
    ARRAY(
      SELECT index_row.indkey[position]::smallint
      FROM generate_series(0, index_row.indnkeyatts - 1) AS position
      ORDER BY position
    )::smallint[] AS key_attnums,
    ARRAY(
      SELECT opclass_namespace.nspname::text || '.' || opclass.opcname::text
      FROM generate_series(0, index_row.indnkeyatts - 1) AS position
      JOIN pg_opclass opclass ON opclass.oid=index_row.indclass[position]
      JOIN pg_namespace opclass_namespace ON opclass_namespace.oid=opclass.opcnamespace
      ORDER BY position
    )::text[] AS opclass_names,
    ARRAY(
      SELECT coalesce(collation_namespace.nspname::text || '.' || collation_row.collname::text, '')
      FROM generate_series(0, index_row.indnkeyatts - 1) AS position
      LEFT JOIN pg_collation collation_row ON collation_row.oid=index_row.indcollation[position]
      LEFT JOIN pg_namespace collation_namespace ON collation_namespace.oid=collation_row.collnamespace
      ORDER BY position
    )::text[] AS collation_names,
    NOT EXISTS (
      SELECT 1 FROM generate_series(0, index_row.indnkeyatts - 1) AS position
      WHERE index_row.indoption[position]<>0
    ) AS key_options_zero,
    index_row.indisunique AS is_unique,
    replace(
      replace(
        btrim(coalesce(pg_get_expr(index_row.indpred, index_row.indrelid, true), '')),
        E'\r\n', E'\n'
      ),
      E'\r', E'\n'
    ) AS normalized_predicate,
    index_relation.relkind::text AS relkind,
    table_relation.relkind::text AS table_relkind,
    pg_get_userbyid(index_relation.relowner)::text AS index_owner,
    pg_get_userbyid(table_relation.relowner)::text AS table_owner,
    index_relation.relpersistence::text AS index_persistence,
    table_relation.relpersistence::text AS table_persistence,
    index_relation.reloptions IS NULL AS default_reloptions,
    index_relation.reltablespace=0 AS default_tablespace,
    index_row.indnkeyatts,
    index_row.indnatts,
    index_row.indexprs IS NULL AS no_expressions,
    NOT index_row.indnullsnotdistinct AS nulls_distinct,
    NOT index_row.indisprimary AS not_primary,
    NOT index_row.indisexclusion AS not_exclusion,
    NOT index_row.indisclustered AS not_clustered,
    NOT index_row.indisreplident AS not_replica_identity,
    index_row.indimmediate AS immediate,
    NOT index_row.indcheckxmin AS no_check_xmin,
    index_row.indisvalid,
    index_row.indisready,
    index_row.indislive
  FROM pg_index AS index_row
  JOIN pg_class AS index_relation ON index_relation.oid=index_row.indexrelid
  JOIN pg_class AS table_relation ON table_relation.oid=index_row.indrelid
  JOIN pg_namespace AS index_namespace ON index_namespace.oid=index_relation.relnamespace
  JOIN pg_namespace AS table_namespace ON table_namespace.oid=table_relation.relnamespace
  JOIN pg_am AS access_method ON access_method.oid=index_relation.relam
  WHERE index_namespace.nspname='public'
    AND index_relation.relname IN (SELECT index_name FROM expected_indexes)
), expected_functions(
  signature, language_name, volatility, security_definer, acl_class,
  return_type, returns_set, default_count, normalized_prosrc_sha256
) AS (
  VALUES
    ('public.paper_canary_fence_armed()'::text, 'sql'::text, 's'::char, true, 'R'::char, 'boolean'::text, false, 0, 'bf52f1b1ec94ff12c0d86fe2021370784dd9de97ad2bea79856aa624444fa142'::text),
    ('public.paper_canary_active_lease_exists()'::text, 'sql'::text, 's'::char, true, 'R'::char, 'boolean'::text, false, 0, '977b394af6c45c274bc4d355689cfae324efa2ad22cde4a03b77c79f4a58c123'::text),
    ('public.paper_canary_execution_authorized()'::text, 'sql'::text, 'v'::char, true, 'R'::char, 'boolean'::text, false, 0, 'd7f0ac33344baf5003601ac475004e681f225e5658c8bba2a3c8bf28b7cb47d3'::text),
    ('public.enforce_paper_canary_lease_audit()'::text, 'plpgsql'::text, 'v'::char, false, 'O'::char, 'trigger'::text, false, 0, '65164a0ea9ccf00c3c3cd72cccc678c51a3cbb5fb7deabd03ca441dc20e0aa99'::text),
    ('public.enforce_paper_canary_runtime_fence()'::text, 'plpgsql'::text, 'v'::char, false, 'O'::char, 'trigger'::text, false, 0, '44803e43e5e154aa556967816606d26ceb64d9a0c3f4af5c694329ff5ca7ad73'::text),
    ('public.enforce_paper_canary_venue_fence()'::text, 'plpgsql'::text, 'v'::char, false, 'O'::char, 'trigger'::text, false, 0, '1d0ecc9b099132017b8f09cf4e04d53c2da780c83c5c0648a57d36742cc567a3'::text),
    ('public.enforce_paper_canary_execution_commit_fence()'::text, 'plpgsql'::text, 'v'::char, false, 'O'::char, 'trigger'::text, false, 0, '754e9608f08f770c719e7c19d0c40ace1ccdcbae3394e03d127a2a109515a7e5'::text),
    ('public.enforce_paper_canary_execution_identity_fence()'::text, 'plpgsql'::text, 'v'::char, false, 'O'::char, 'trigger'::text, false, 0, 'd9bf051176bec6d91fe9aea70a65fe5e369cd9aa5d6d7ff8a5240aac0eaea2c4'::text),
    ('public.paper_canary_stop_request_reserved(text)'::text, 'sql'::text, 's'::char, true, 'R'::char, 'boolean'::text, false, 0, 'c1091906ce61eb1b37348c5ce45191c1e6324dfb4aa16af61acee2128352d544'::text),
    ('public.paper_canary_resume_request_reserved(text)'::text, 'sql'::text, 's'::char, true, 'R'::char, 'boolean'::text, false, 0, '6f2fa5884b5e71a8c4b14e8708dbfd89ec9200195050ae1a382d4dfa43a48fd0'::text),
    ('public.paper_canary_resume_command_authorized(text,text,text)'::text, 'sql'::text, 'v'::char, true, 'R'::char, 'boolean'::text, false, 0, '1444bebaf12329a34fc1f2ec70e1f5c596ffca44da18472ea4433aa23e8517e3'::text),
    ('public.enforce_paper_canary_request_reservation()'::text, 'plpgsql'::text, 'v'::char, false, 'O'::char, 'trigger'::text, false, 0, '6ee1c90a5e9c0476742618ebdfc4037880a2f9f9f6f172d526aa06611596e203'::text),
    ('public.engage_paper_canary_runtime_stop(text,text,text)'::text, 'plpgsql'::text, 'v'::char, true, 'R'::char, 'integer'::text, false, 0, 'b213ba135e4157546d28358401532024043e2b9952f5b11b0092606a445aca3b'::text),
    ('public.resume_paper_canary_exact(bigint)'::text, 'plpgsql'::text, 'v'::char, true, 'R'::char, 'boolean'::text, false, 0, 'b5291fe09209d8446c66dfc49a9e8e2c1d96e9ce44524188d4230e3a461c7b5c'::text),
    ('public.paper_canary_stop_core(text,text,text,text)'::text, 'plpgsql'::text, 'v'::char, false, 'O'::char, 'record'::text, true, 0, '962b8300848aa28d316241641a3e7ee8649e0f5d159ec4d519ddfd7262e9f5b6'::text),
    ('public.safety_paper_canary_stop(text,text,text)'::text, 'plpgsql'::text, 'v'::char, true, 'S'::char, 'record'::text, true, 0, '5ece0778821e57436e9a087537e81a1e6321bce43e4399d1d10923667bd8d215'::text),
    ('public.safety_paper_canary_ready()'::text, 'plpgsql'::text, 'v'::char, true, 'S'::char, 'boolean'::text, false, 0, 'c25ebf350b5e9fbbfdcedaa4c484202ee92321b5b399caf1f56d84f9218d9d75'::text),
    ('public.safety_paper_canary_acquire(text,text,text,text,text,text,text,text,text,integer)'::text, 'plpgsql'::text, 'v'::char, true, 'S'::char, 'record'::text, true, 0, 'd56836d4c3f0e003d5da0c04f56a26456dd727b12fa2612f63723ab778d6c0b2'::text),
    ('public.safety_paper_canary_heartbeat(text,text,text,integer)'::text, 'plpgsql'::text, 'v'::char, true, 'S'::char, 'bigint'::text, false, 0, '5d45bb7f68ae615a6a2ca88daf3c76ef4f1ed084457da6ee0649531bcabe3eeb'::text),
    ('public.safety_paper_canary_release(text,text,text,text)'::text, 'plpgsql'::text, 'v'::char, true, 'S'::char, 'record'::text, true, 0, 'f64005d57f0515e51b3308f16f069ac81e3d783de8abaee5fc763cd91511809d'::text),
    ('public.safety_paper_canary_watchdog_tick()'::text, 'plpgsql'::text, 'v'::char, true, 'S'::char, 'record'::text, true, 0, '2b4cd3aac40815f091a1c04d52e0e4f3aeda7a35422936b288e2582536fc6c45'::text),
    ('public.assert_stratus_safety_boundary(boolean)'::text, 'plpgsql'::text, 'v'::char, false, 'S'::char, 'boolean'::text, false, 1, '888dd128fc696622c7b97beddfedcbc4a57b52856c73a09c0af6ebc58be451f9'::text),
    ('public.assert_stratus_runtime_boundary_v2(boolean)'::text, 'plpgsql'::text, 'v'::char, false, 'R'::char, 'boolean'::text, false, 1, '8ae11290ef847a82769ee6a87cc5a2f14a9ed66e59ee230139b63e50487c0349'::text),
    ('public.assert_stratus_runtime_boundary(boolean)'::text, 'plpgsql'::text, 'v'::char, false, 'R'::char, 'boolean'::text, false, 1, '5aa4d8d0e5a575284a4a3ac717ca07dd1cda9b109b9ec3ae84c02f6f89f6da6d'::text)
), expected_function_shapes(signature, arguments, function_result) AS (
  VALUES
    ('public.paper_canary_fence_armed()'::text, ''::text, 'boolean'::text),
    ('public.paper_canary_active_lease_exists()'::text, ''::text, 'boolean'::text),
    ('public.paper_canary_execution_authorized()'::text, ''::text, 'boolean'::text),
    ('public.enforce_paper_canary_lease_audit()'::text, ''::text, 'trigger'::text),
    ('public.enforce_paper_canary_runtime_fence()'::text, ''::text, 'trigger'::text),
    ('public.enforce_paper_canary_venue_fence()'::text, ''::text, 'trigger'::text),
    ('public.enforce_paper_canary_execution_commit_fence()'::text, ''::text, 'trigger'::text),
    ('public.enforce_paper_canary_execution_identity_fence()'::text, ''::text, 'trigger'::text),
    ('public.paper_canary_stop_request_reserved(text)'::text, 'requested_id text'::text, 'boolean'::text),
    ('public.paper_canary_resume_request_reserved(text)'::text, 'requested_id text'::text, 'boolean'::text),
    ('public.paper_canary_resume_command_authorized(text,text,text)'::text, 'requested_id text, requested_actor text, requested_reason text'::text, 'boolean'::text),
    ('public.enforce_paper_canary_request_reservation()'::text, ''::text, 'trigger'::text),
    ('public.engage_paper_canary_runtime_stop(text,text,text)'::text, 'requested_id text, requested_actor text, requested_reason text'::text, 'integer'::text),
    ('public.resume_paper_canary_exact(bigint)'::text, 'requested_command_id bigint'::text, 'boolean'::text),
    ('public.paper_canary_stop_core(text,text,text,text)'::text, 'requested_id text, requested_actor text, requested_reason text, lease_terminal_state text'::text, 'TABLE(command_id bigint, result_created boolean, result_idempotent boolean, expired_signals integer, cancel_requested_live_orders integer, rejected_pending_resume integer)'::text),
    ('public.safety_paper_canary_stop(text,text,text)'::text, 'requested_id text, requested_actor text, requested_reason text'::text, 'TABLE(command_id bigint, command_status text, command_detail text, command_created_at timestamp with time zone, command_executed_at timestamp with time zone, result_created boolean, result_idempotent boolean, expired_signals integer, cancel_requested_live_orders integer, rejected_pending_resume integer)'::text),
    ('public.safety_paper_canary_ready()'::text, ''::text, 'boolean'::text),
    ('public.safety_paper_canary_acquire(text,text,text,text,text,text,text,text,text,integer)'::text, 'input_lease_id text, input_run_id text, input_acquire_request_id text, input_resume_request_id text, input_direct_stop_request_id text, input_release_request_id text, input_resume_actor text, input_resume_reason text, input_nonce_sha256 text, input_ttl_seconds integer'::text, 'TABLE(lease_pk bigint, result_created boolean, result_idempotent boolean)'::text),
    ('public.safety_paper_canary_heartbeat(text,text,text,integer)'::text, 'input_lease_id text, input_run_id text, input_nonce_sha256 text, input_ttl_seconds integer'::text, 'bigint'::text),
    ('public.safety_paper_canary_release(text,text,text,text)'::text, 'input_lease_id text, input_run_id text, input_nonce_sha256 text, input_release_request_id text'::text, 'TABLE(lease_pk bigint, result_created boolean, result_idempotent boolean)'::text),
    ('public.safety_paper_canary_watchdog_tick()'::text, ''::text, 'TABLE(lease_pk bigint, terminalized boolean)'::text),
    ('public.assert_stratus_safety_boundary(boolean)'::text, 'require_login boolean DEFAULT true'::text, 'boolean'::text),
    ('public.assert_stratus_runtime_boundary_v2(boolean)'::text, 'require_login boolean DEFAULT true'::text, 'boolean'::text),
    ('public.assert_stratus_runtime_boundary(boolean)'::text, 'require_login boolean DEFAULT true'::text, 'boolean'::text)
), expected_control_triggers(relation_name, trigger_name, function_signature, trigger_type) AS (
  VALUES
    ('paper_canary_safety_leases'::text, 'trg_paper_canary_lease_audit'::text, 'public.enforce_paper_canary_lease_audit()'::text, 31::smallint),
    ('runtime_control'::text, 'trg_paper_canary_runtime_fence'::text, 'public.enforce_paper_canary_runtime_fence()'::text, 31::smallint),
    ('venue_runtime_control'::text, 'trg_paper_canary_venue_fence'::text, 'public.enforce_paper_canary_venue_fence()'::text, 31::smallint),
    ('operator_commands'::text, 'trg_paper_canary_request_reservation'::text, 'public.enforce_paper_canary_request_reservation()'::text, 31::smallint)
), expected_commit_triggers(relation_name, trigger_name, trigger_type) AS (
  VALUES
    ('signals'::text, 'trg_paper_canary_signal_commit_fence'::text, 21::smallint),
    ('orders'::text, 'trg_paper_canary_order_commit_fence'::text, 21::smallint),
    ('execution_order_intents'::text, 'trg_paper_canary_intent_commit_fence'::text, 5::smallint),
    ('execution_fill_legs'::text, 'trg_paper_canary_fill_leg_commit_fence'::text, 5::smallint),
    ('positions'::text, 'trg_paper_canary_position_commit_fence'::text, 21::smallint)
), expected_identity_triggers(relation_name, trigger_name, trigger_type) AS (
  VALUES
    ('bankroll_log'::text, 'trg_paper_canary_bankroll_identity_fence'::text, 31::smallint),
    ('orders'::text, 'trg_paper_canary_order_identity_fence'::text, 27::smallint),
    ('positions'::text, 'trg_paper_canary_position_identity_fence'::text, 27::smallint),
    ('signals'::text, 'trg_paper_canary_signal_identity_fence'::text, 27::smallint)
)
SELECT json_build_object(
  'database_clock', clock_timestamp(),
  'schema_head_count', (SELECT count(*) FROM alembic_version),
  'schema_head', (SELECT min(version_num) FROM alembic_version),
  'indexes_exact', (
    (SELECT count(*) FROM actual_indexes)=7
    AND NOT EXISTS (
      SELECT 1 FROM expected_indexes expected
      LEFT JOIN actual_indexes actual USING(index_name)
       WHERE actual.index_name IS NULL
          OR actual.table_name IS DISTINCT FROM expected.table_name
          OR actual.table_oid IS DISTINCT FROM to_regclass(expected.table_regclass)::oid
          OR actual.index_namespace IS DISTINCT FROM 'public'
          OR actual.table_namespace IS DISTINCT FROM 'public'
          OR actual.access_method IS DISTINCT FROM 'btree'
          OR actual.key_names IS DISTINCT FROM expected.key_names
          OR actual.key_attnums IS DISTINCT FROM expected.key_attnums
          OR actual.opclass_names IS DISTINCT FROM expected.opclass_names
          OR actual.collation_names IS DISTINCT FROM expected.collation_names
          OR NOT actual.key_options_zero
          OR actual.is_unique IS DISTINCT FROM expected.is_unique
          OR actual.normalized_predicate IS DISTINCT FROM expected.normalized_predicate
          OR actual.relkind <> 'i'
          OR actual.table_relkind <> 'r'
          OR actual.index_owner IS DISTINCT FROM 'stratus'
          OR actual.table_owner IS DISTINCT FROM 'stratus'
          OR actual.index_persistence IS DISTINCT FROM 'p'
          OR actual.table_persistence IS DISTINCT FROM 'p'
          OR NOT actual.default_reloptions OR NOT actual.default_tablespace
          OR actual.indnkeyatts <> cardinality(expected.key_names)
          OR actual.indnatts <> cardinality(expected.key_names)
          OR NOT actual.no_expressions OR NOT actual.nulls_distinct
          OR NOT actual.not_primary OR NOT actual.not_exclusion
          OR NOT actual.not_clustered OR NOT actual.not_replica_identity
          OR NOT actual.immediate
          OR NOT actual.no_check_xmin
          OR NOT actual.indisvalid OR NOT actual.indisready OR NOT actual.indislive
      )
  ),
  'functions_exact', (
    (SELECT count(*) FROM expected_functions)=24
    AND (SELECT count(*) FROM expected_function_shapes)=24
    AND NOT EXISTS (
      SELECT 1
      FROM expected_functions expected
      JOIN expected_function_shapes shape USING(signature)
      LEFT JOIN pg_proc procedure
        ON procedure.oid=to_regprocedure(expected.signature)
      LEFT JOIN pg_namespace namespace ON namespace.oid=procedure.pronamespace
      LEFT JOIN pg_language language_row ON language_row.oid=procedure.prolang
      WHERE procedure.oid IS NULL
         OR namespace.nspname IS DISTINCT FROM 'public'
         OR language_row.lanname IS DISTINCT FROM expected.language_name
         OR pg_get_userbyid(procedure.proowner) IS DISTINCT FROM 'stratus'
         OR procedure.provolatile::text IS DISTINCT FROM expected.volatility::text
         OR procedure.prosecdef IS DISTINCT FROM expected.security_definer
         OR procedure.proparallel::text IS DISTINCT FROM 'u'
         OR procedure.proleakproof OR procedure.proisstrict
         OR procedure.prokind::text IS DISTINCT FROM 'f'
         OR procedure.provariadic<>0 OR procedure.prosupport<>0
         OR procedure.prorettype::regtype::text IS DISTINCT FROM expected.return_type
         OR procedure.proretset IS DISTINCT FROM expected.returns_set
         OR procedure.pronargdefaults IS DISTINCT FROM expected.default_count
         OR (
              expected.default_count=0 AND procedure.proargdefaults IS NOT NULL
            )
         OR (
              expected.default_count=1
              AND pg_get_expr(procedure.proargdefaults, 0) IS DISTINCT FROM 'true'
            )
         OR pg_get_function_arguments(procedure.oid) IS DISTINCT FROM shape.arguments
         OR pg_get_function_result(procedure.oid) IS DISTINCT FROM shape.function_result
         OR procedure.proconfig IS DISTINCT FROM
              ARRAY['search_path=pg_catalog, public']::text[]
         OR procedure.procost<>100
         OR procedure.prorows<>CASE WHEN expected.returns_set THEN 1000 ELSE 0 END
         OR procedure.probin IS NOT NULL OR procedure.prosqlbody IS NOT NULL
         OR encode(
              sha256(
                convert_to(
                  btrim(
                    replace(
                      replace(procedure.prosrc, E'\r\n', E'\n'),
                      E'\r', E'\n'
                    ),
                    E' \t\r\n'
                  ),
                  'UTF8'
                )
              ),
              'hex'
            ) IS DISTINCT FROM expected.normalized_prosrc_sha256
         OR NOT (
              SELECT
                count(*)=CASE expected.acl_class WHEN 'O' THEN 1 ELSE 2 END
                AND count(*) FILTER (
                  WHERE privilege.grantee=procedure.proowner
                )=1
                AND count(*) FILTER (
                  WHERE privilege.grantee=to_regrole(
                    'stratus_runtime_' || md5(current_database())
                  )
                )=CASE expected.acl_class WHEN 'R' THEN 1 ELSE 0 END
                AND count(*) FILTER (
                  WHERE privilege.grantee=to_regrole(
                    'stratus_safety_' || md5(current_database())
                  )
                )=CASE expected.acl_class WHEN 'S' THEN 1 ELSE 0 END
                AND coalesce(bool_and(
                  privilege.grantor=procedure.proowner
                  AND privilege.privilege_type='EXECUTE'
                  AND NOT privilege.is_grantable
                  AND (
                    privilege.grantee=procedure.proowner
                    OR (
                      expected.acl_class='R'
                      AND privilege.grantee=to_regrole(
                        'stratus_runtime_' || md5(current_database())
                      )
                    )
                    OR (
                      expected.acl_class='S'
                      AND privilege.grantee=to_regrole(
                        'stratus_safety_' || md5(current_database())
                      )
                    )
                  )
                ), false)
              FROM aclexplode(
                coalesce(
                  procedure.proacl,
                  acldefault('f', procedure.proowner)
                )
              ) privilege
            )
    )
    AND (
      SELECT count(*)
      FROM pg_proc procedure
      JOIN pg_namespace namespace ON namespace.oid=procedure.pronamespace
      WHERE namespace.nspname='public'
        AND procedure.proname IN (
          SELECT replace(split_part(signature, '(', 1), 'public.', '')
          FROM expected_functions
        )
    )=24
  ),
  'lease_table_count', (
    SELECT count(*) FROM pg_class relation
    JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace
    WHERE namespace.nspname='public'
      AND relation.relname='paper_canary_safety_leases'
      AND relation.relkind='r'
  ),
  'lease_rows', (SELECT count(*) FROM paper_canary_safety_leases),
  'active_lease_rows', (SELECT count(*) FROM paper_canary_safety_leases WHERE state='active'),
  'runtime_boundary', public.assert_stratus_runtime_boundary(false),
  'safety_boundary', public.assert_stratus_safety_boundary(false),
  'control_triggers_exact', (
    (SELECT count(*) FROM expected_control_triggers expected
      JOIN pg_namespace namespace ON namespace.nspname='public'
      JOIN pg_class relation ON relation.relnamespace=namespace.oid AND relation.relname=expected.relation_name
      JOIN pg_trigger trigger_row ON trigger_row.tgrelid=relation.oid AND trigger_row.tgname=expected.trigger_name
      WHERE NOT trigger_row.tgisinternal
        AND trigger_row.tgenabled IN ('O','A')
        AND trigger_row.tgtype=expected.trigger_type
        AND trigger_row.tgfoid=to_regprocedure(expected.function_signature)
        AND trigger_row.tgnargs=0 AND octet_length(trigger_row.tgargs)=0
        AND trigger_row.tgqual IS NULL)=4
    AND (SELECT count(*) FROM pg_trigger trigger_row
      WHERE NOT trigger_row.tgisinternal
        AND trigger_row.tgfoid IN (
          to_regprocedure('public.enforce_paper_canary_lease_audit()'),
          to_regprocedure('public.enforce_paper_canary_runtime_fence()'),
          to_regprocedure('public.enforce_paper_canary_venue_fence()'),
          to_regprocedure('public.enforce_paper_canary_request_reservation()')
        ))=4
  ),
  'commit_triggers_exact', (
    (SELECT count(*) FROM expected_commit_triggers expected
      JOIN pg_namespace namespace ON namespace.nspname='public'
      JOIN pg_class relation ON relation.relnamespace=namespace.oid AND relation.relname=expected.relation_name
      JOIN pg_trigger trigger_row ON trigger_row.tgrelid=relation.oid AND trigger_row.tgname=expected.trigger_name
      WHERE NOT trigger_row.tgisinternal
        AND trigger_row.tgenabled IN ('O','A')
        AND trigger_row.tgtype=expected.trigger_type
        AND trigger_row.tgfoid=to_regprocedure('public.enforce_paper_canary_execution_commit_fence()')
        AND trigger_row.tgconstraint<>0 AND trigger_row.tgdeferrable AND trigger_row.tginitdeferred
        AND trigger_row.tgnargs=0 AND octet_length(trigger_row.tgargs)=0
        AND trigger_row.tgqual IS NULL)=5
    AND (SELECT count(*) FROM pg_trigger trigger_row
      WHERE NOT trigger_row.tgisinternal
        AND trigger_row.tgfoid=to_regprocedure('public.enforce_paper_canary_execution_commit_fence()'))=5
  ),
  'identity_triggers_exact', (
    (SELECT count(*) FROM expected_identity_triggers expected
      JOIN pg_namespace namespace ON namespace.nspname='public'
      JOIN pg_class relation ON relation.relnamespace=namespace.oid AND relation.relname=expected.relation_name
      JOIN pg_trigger trigger_row ON trigger_row.tgrelid=relation.oid AND trigger_row.tgname=expected.trigger_name
      WHERE NOT trigger_row.tgisinternal
        AND trigger_row.tgenabled IN ('O','A')
        AND trigger_row.tgtype=expected.trigger_type
        AND trigger_row.tgfoid=to_regprocedure('public.enforce_paper_canary_execution_identity_fence()')
        AND trigger_row.tgconstraint=0 AND NOT trigger_row.tgdeferrable AND NOT trigger_row.tginitdeferred
        AND trigger_row.tgnargs=0 AND octet_length(trigger_row.tgargs)=0
        AND trigger_row.tgqual IS NULL)=4
    AND (SELECT count(*) FROM pg_trigger trigger_row
      WHERE NOT trigger_row.tgisinternal
        AND trigger_row.tgfoid=to_regprocedure('public.enforce_paper_canary_execution_identity_fence()'))=4
  ),
  'global_rows', (SELECT count(*) FROM runtime_control WHERE id=1),
  'global_mode', (SELECT mode::text FROM runtime_control WHERE id=1),
  'global_kill_switch', (SELECT kill_switch FROM runtime_control WHERE id=1),
  'global_live_confirmation', (SELECT live_confirmation_until FROM runtime_control WHERE id=1),
  'polymarket_rows', (SELECT count(*) FROM venue_runtime_control WHERE venue='polymarket'::trading_venue),
  'polymarket_enabled', (SELECT enabled FROM venue_runtime_control WHERE venue='polymarket'::trading_venue),
  'polymarket_mode', (SELECT mode::text FROM venue_runtime_control WHERE venue='polymarket'::trading_venue),
  'polymarket_kill_switch', (SELECT kill_switch FROM venue_runtime_control WHERE venue='polymarket'::trading_venue),
  'polymarket_live_confirmation', (SELECT live_confirmation_until FROM venue_runtime_control WHERE venue='polymarket'::trading_venue),
  'binance_rows', (SELECT count(*) FROM venue_runtime_control WHERE venue='binance_spot'::trading_venue),
  'binance_enabled', (SELECT enabled FROM venue_runtime_control WHERE venue='binance_spot'::trading_venue),
  'binance_mode', (SELECT mode::text FROM venue_runtime_control WHERE venue='binance_spot'::trading_venue),
  'binance_kill_switch', (SELECT kill_switch FROM venue_runtime_control WHERE venue='binance_spot'::trading_venue),
  'binance_live_confirmation', (SELECT live_confirmation_until FROM venue_runtime_control WHERE venue='binance_spot'::trading_venue),
  'pending_commands', (SELECT count(*) FROM operator_commands WHERE status='pending'),
  'live_orders', (SELECT count(*) FROM orders WHERE mode='live'::execution_mode),
  'live_positions', (SELECT count(*) FROM positions WHERE mode='live'::execution_mode),
  'live_intents', (SELECT count(*) FROM execution_order_intents WHERE mode='live'::execution_mode),
  'live_fill_legs', (SELECT count(*) FROM execution_fill_legs WHERE mode='live'::execution_mode),
  'live_enrollments', (SELECT count(*) FROM prospective_validation_enrollments WHERE mode='live'::execution_mode),
  'live_outcomes', (
    SELECT count(*) FROM prospective_validation_outcomes outcome
    JOIN prospective_validation_enrollments enrollment ON enrollment.id=outcome.enrollment_id
    WHERE enrollment.mode='live'::execution_mode
  ),
  'runtime_sessions', (
    SELECT count(*) FROM pg_stat_activity
    WHERE pid<>pg_backend_pid() AND datname=current_database()
      AND backend_type='client backend'
  ),
  'idle_in_transaction_sessions', (
    SELECT count(*) FROM pg_stat_activity
    WHERE pid<>pg_backend_pid() AND datname=current_database()
      AND backend_type='client backend' AND state LIKE 'idle in transaction%'
  ),
  'lock_waiters', (
    SELECT count(*) FROM pg_stat_activity
    WHERE pid<>pg_backend_pid() AND datname=current_database()
      AND backend_type='client backend' AND wait_event_type='Lock'
  ),
  'active_pg_dump', (
    SELECT count(*) FROM pg_stat_activity
    WHERE pid<>pg_backend_pid() AND datname=current_database()
      AND backend_type='client backend' AND application_name LIKE 'pg_dump%'
  ),
  'bootstrap_audit_exact', (
    (SELECT count(*) FROM operator_commands WHERE request_id='paper-canary-bootstrap-stop-0047')=1
    AND (SELECT count(*) FROM operator_commands
      WHERE request_id='paper-canary-bootstrap-stop-0047'
        AND command='stop'
        AND payload=jsonb_build_object('venue', 'all')
        AND actor='migration-0047'
        AND reason='Fail closed before DB-clock PAPER canary authority.'
        AND status='succeeded'
        AND executed_at IS NOT NULL
        AND detail LIKE 'Migration 0047 forced global/Polymarket PAPER kills ON%')=1
  ),
  'pending_signals', (SELECT count(*) FROM signals WHERE status='pending'::signal_status),
  'live_open_or_pending_orders', (
    SELECT count(*) FROM orders
    WHERE mode='live'::execution_mode AND status IN ('open','pending')
  ),
  'fence_armed', public.paper_canary_fence_armed(),
  'active_lease_exists', public.paper_canary_active_lease_exists(),
  'execution_authorized', public.paper_canary_execution_authorized()
)::text;
COMMIT;"
'@
    return Get-OneJsonObject -Label 'strict successor live-state transaction' -Lines (
        Invoke-Docker -Arguments @(
            'exec', $productionPostgresId, 'sh', '-ec', $queryCommand
        )
    )
}

function Assert-StrictLiveSnapshot {
    param(
        [Parameter(Mandatory = $true)][object] $Snapshot,
        [Parameter(Mandatory = $true)][string] $Label
    )
    Assert-NativeJsonObject -Value $Snapshot -Label $Label
    Assert-ExactProperties -Value $Snapshot -Label $Label -Expected @(
        'database_clock', 'schema_head_count', 'schema_head', 'indexes_exact', 'functions_exact',
        'lease_table_count', 'lease_rows', 'active_lease_rows', 'runtime_boundary',
        'safety_boundary', 'control_triggers_exact', 'commit_triggers_exact',
        'identity_triggers_exact', 'global_rows', 'global_mode', 'global_kill_switch',
        'global_live_confirmation', 'polymarket_rows', 'polymarket_enabled',
        'polymarket_mode', 'polymarket_kill_switch', 'polymarket_live_confirmation',
        'binance_rows', 'binance_enabled', 'binance_mode', 'binance_kill_switch',
        'binance_live_confirmation', 'pending_commands', 'live_orders', 'live_positions',
        'live_intents', 'live_fill_legs', 'live_enrollments', 'live_outcomes',
        'runtime_sessions', 'idle_in_transaction_sessions', 'lock_waiters',
        'active_pg_dump', 'bootstrap_audit_exact', 'pending_signals',
        'live_open_or_pending_orders', 'fence_armed', 'active_lease_exists',
        'execution_authorized'
    )
    foreach ($name in @(
        'database_clock', 'schema_head', 'global_mode', 'polymarket_mode', 'binance_mode'
    )) {
        Assert-NativeJsonString -Value $Snapshot.$name -Label "$Label $name"
    }
    foreach ($name in @(
        'indexes_exact', 'functions_exact', 'runtime_boundary', 'safety_boundary', 'control_triggers_exact',
        'commit_triggers_exact', 'identity_triggers_exact', 'global_kill_switch',
        'polymarket_enabled', 'polymarket_kill_switch', 'binance_enabled',
        'binance_kill_switch', 'bootstrap_audit_exact', 'fence_armed',
        'active_lease_exists', 'execution_authorized'
    )) {
        Assert-NativeJsonBoolean -Value $Snapshot.$name -Label "$Label $name"
    }
    foreach ($name in @(
        'schema_head_count', 'lease_table_count', 'lease_rows', 'active_lease_rows',
        'global_rows', 'polymarket_rows', 'binance_rows', 'pending_commands',
        'live_orders', 'live_positions', 'live_intents', 'live_fill_legs',
        'live_enrollments', 'live_outcomes', 'runtime_sessions',
        'idle_in_transaction_sessions', 'lock_waiters', 'active_pg_dump',
        'pending_signals', 'live_open_or_pending_orders'
    )) {
        Assert-NativeJsonInteger -Value $Snapshot.$name -Label "$Label $name"
    }
    foreach ($name in @(
        'global_live_confirmation', 'polymarket_live_confirmation',
        'binance_live_confirmation'
    )) {
        if ($null -ne $Snapshot.$name) {
            throw "$Label $name must be native JSON null."
        }
    }
    if (
        $Snapshot.schema_head_count -ne 1 -or
        $Snapshot.schema_head -cne $targetSchemaHead -or
        -not $Snapshot.indexes_exact -or
        -not $Snapshot.functions_exact -or
        $Snapshot.lease_table_count -ne 1 -or
        $Snapshot.lease_rows -ne 0 -or
        $Snapshot.active_lease_rows -ne 0 -or
        -not $Snapshot.runtime_boundary -or
        -not $Snapshot.safety_boundary -or
        -not $Snapshot.control_triggers_exact -or
        -not $Snapshot.commit_triggers_exact -or
        -not $Snapshot.identity_triggers_exact -or
        $Snapshot.global_rows -ne 1 -or
        $Snapshot.global_mode -cne 'paper' -or
        -not $Snapshot.global_kill_switch -or
        $Snapshot.polymarket_rows -ne 1 -or
        -not $Snapshot.polymarket_enabled -or
        $Snapshot.polymarket_mode -cne 'paper' -or
        -not $Snapshot.polymarket_kill_switch -or
        $Snapshot.binance_rows -ne 1 -or
        -not $Snapshot.binance_enabled -or
        $Snapshot.binance_mode -cne 'backtest' -or
        -not $Snapshot.binance_kill_switch -or
        -not $Snapshot.bootstrap_audit_exact -or
        -not $Snapshot.fence_armed -or
        $Snapshot.active_lease_exists -or
        $Snapshot.execution_authorized
    ) {
        throw "$Label rejected schema, database objects, or fail-closed controls."
    }
    foreach ($name in @(
        'pending_commands', 'live_orders', 'live_positions', 'live_intents',
        'live_fill_legs', 'live_enrollments', 'live_outcomes', 'runtime_sessions',
        'idle_in_transaction_sessions', 'lock_waiters', 'active_pg_dump',
        'pending_signals', 'live_open_or_pending_orders'
    )) {
        if ($Snapshot.$name -ne 0) {
            throw "$Label rejected non-zero $name."
        }
    }
    return ConvertTo-RequiredUtcTime -Value ([string]$Snapshot.database_clock) `
        -Label "$Label database_clock"
}

function New-AttestationEnvelope {
    param(
        [Parameter(Mandatory = $true)][string] $Format,
        [Parameter(Mandatory = $true)][object] $Payload
    )
    $payloadJson = $Payload | ConvertTo-Json -Depth 20 -Compress
    return [ordered]@{
        format = $Format
        accepted = $true
        payload = $Payload
        payload_sha256 = Get-Sha256Text -Value $payloadJson
    }
}

$absoluteRoot = [IO.Path]::GetFullPath($Root)
$absoluteBuildContext = [IO.Path]::GetFullPath($BuildContext)
$absoluteNetworkRepairPath = [IO.Path]::GetFullPath($NetworkRepairAttestationPath)
[void](Assert-RegularDirectory -LiteralPath $absoluteRoot)
[void](Assert-RegularDirectory -LiteralPath $absoluteBuildContext)
$absolutePreStartPath = Assert-PathBelowRoot -LiteralPath $PreStartAttestationPath `
    -AbsoluteRoot $absoluteRoot -Label 'PreStartAttestationPath'
$absoluteLivePath = Assert-PathBelowRoot -LiteralPath $LiveAcceptancePath `
    -AbsoluteRoot $absoluteRoot -Label 'LiveAcceptancePath'
$watchdogLeasePath = Assert-PathBelowRoot `
    -LiteralPath ($absolutePreStartPath + '.watchdog-lease.json') `
    -AbsoluteRoot $absoluteRoot -Label 'watchdog LeasePath'
$watchdogReceiptPath = Assert-PathBelowRoot `
    -LiteralPath ($absolutePreStartPath + '.watchdog-terminal.json') `
    -AbsoluteRoot $absoluteRoot -Label 'watchdog ReceiptPath'
$watchdogHeartbeatPath = Assert-PathBelowRoot `
    -LiteralPath ($absolutePreStartPath + '.watchdog-heartbeat.json') `
    -AbsoluteRoot $absoluteRoot -Label 'watchdog HeartbeatPath'
$watchdogCommitPath = Assert-PathBelowRoot `
    -LiteralPath ($absolutePreStartPath + '.watchdog-commit.json') `
    -AbsoluteRoot $absoluteRoot -Label 'watchdog CommitPath'
$watchdogStartFencePath = Assert-PathBelowRoot `
    -LiteralPath ($absolutePreStartPath + '.watchdog-start-fence.lock') `
    -AbsoluteRoot $absoluteRoot -Label 'watchdog StartFencePath'
$watchdogStartRevokePath = Assert-PathBelowRoot `
    -LiteralPath ($absolutePreStartPath + '.watchdog-start-revoked.json') `
    -AbsoluteRoot $absoluteRoot -Label 'watchdog StartRevokePath'
$watchdogStartAttemptPath = Assert-PathBelowRoot `
    -LiteralPath ($absolutePreStartPath + '.watchdog-start-attempt.json') `
    -AbsoluteRoot $absoluteRoot -Label 'watchdog StartAttemptPath'
$watchdogStartCompletionPath = Assert-PathBelowRoot `
    -LiteralPath ($absolutePreStartPath + '.watchdog-start-complete.json') `
    -AbsoluteRoot $absoluteRoot -Label 'watchdog StartCompletionPath'
$parentContainmentPath = Assert-PathBelowRoot `
    -LiteralPath ($absolutePreStartPath + '.parent-containment.json') `
    -AbsoluteRoot $absoluteRoot -Label 'parent ContainmentPath'
$parentAuthorizationPath = Assert-PathBelowRoot `
    -LiteralPath ($absolutePreStartPath + '.parent-authorization.json') `
    -AbsoluteRoot $absoluteRoot -Label 'parent AuthorizationPath'
Assert-ToolEnvironmentBoundary
$dockerApplication = Resolve-PinnedApplication -Name 'docker' -Label 'Docker' `
    -ApprovedSha256 $approvedDockerExecutableSha256
$script:dockerExecutable = [string]$dockerApplication.path
$script:dockerExecutableSha256 = [string]$dockerApplication.sha256
$gitApplication = Resolve-PinnedApplication -Name 'git' -Label 'Git' `
    -ApprovedSha256 $approvedGitExecutableSha256
$script:gitExecutable = [string]$gitApplication.path
$script:gitExecutableSha256 = [string]$gitApplication.sha256
$composePath = Join-Path $absoluteRoot 'docker-compose.yml'
$composeEnvPath = Join-Path $absoluteRoot '.env'
[void](Assert-RegularFile -LiteralPath $composePath)
[void](Assert-RegularFile -LiteralPath $composeEnvPath)
foreach ($overrideName in @(
    'COMPOSE_FILE', 'COMPOSE_PROFILES', 'COMPOSE_PROJECT_NAME',
    'COMPOSE_PATH_SEPARATOR', 'COMPOSE_PROVIDER'
)) {
    if (Test-Path -LiteralPath "Env:$overrideName") {
        throw "Release Compose override environment variable is forbidden: $overrideName"
    }
}
if (@(
    Get-Content -LiteralPath $composeEnvPath | Where-Object {
        $_ -match '^\s*(?:export\s+)?(?:COMPOSE_(?:FILE|PROFILES|PROJECT_NAME|PATH_SEPARATOR|PROVIDER)|DOCKER_(?:CONFIG|CLI_PLUGIN_EXTRA_DIRS|HOST|CONTEXT|TLS_VERIFY|CERT_PATH|API_VERSION))\s*='
    }
).Count -ne 0) {
    throw 'Release .env must not define a reserved Compose override variable.'
}
$composeArguments = @(
    'compose', '--project-directory', $absoluteRoot, '-f', $composePath,
    '--env-file', $composeEnvPath, '--project-name', 'weatherpoly'
)
if ($CandidateImage -cne "stratus:candidate-$($ExpectedRevision.Substring(0, 12))") {
    throw 'CandidateImage is not the exact revision-derived successor tag.'
}
if ($PostgresContainer -cne $productionPostgresId) {
    throw 'PostgresContainer must be the exact retained production container ID.'
}
if (
    [StringComparer]::OrdinalIgnoreCase.Equals($absolutePreStartPath, $absoluteLivePath) -or
    [StringComparer]::OrdinalIgnoreCase.Equals(
        $absoluteNetworkRepairPath, $absolutePreStartPath
    ) -or
    [StringComparer]::OrdinalIgnoreCase.Equals(
        $absoluteNetworkRepairPath, $absoluteLivePath
    )
) {
    throw 'Repair, PreStart, and Live evidence paths must all be distinct.'
}
if (Test-Path -LiteralPath $absolutePreStartPath) {
    throw 'Transition refuses to overwrite an existing successor PreStart attestation.'
}
if (Test-Path -LiteralPath $absoluteLivePath) {
    throw 'Transition refuses to overwrite an existing successor Live acceptance.'
}
foreach ($path in @(
    $watchdogLeasePath, $watchdogReceiptPath, $watchdogHeartbeatPath,
    $watchdogCommitPath, $watchdogStartFencePath, $watchdogStartRevokePath,
    $watchdogStartAttemptPath, $watchdogStartCompletionPath,
    $parentContainmentPath, $parentAuthorizationPath
)) {
    if (Test-Path -LiteralPath $path) {
        throw "Transition refuses a pre-existing watchdog artifact path: $path"
    }
}
$watchdogArtifactPaths = @(
    $absolutePreStartPath, $absoluteLivePath, $watchdogLeasePath,
    $watchdogReceiptPath, $watchdogHeartbeatPath, $watchdogCommitPath,
    $watchdogStartFencePath, $watchdogStartRevokePath,
    $watchdogStartAttemptPath, $watchdogStartCompletionPath,
    $parentContainmentPath, $parentAuthorizationPath
)
if (@($watchdogArtifactPaths | Sort-Object -Unique).Count -ne 12) {
    throw 'Transition watchdog artifact paths must be pairwise distinct.'
}

$eventWitness = $null
$authorizationWitness = $null
$authorizationInitialBarrier = $null
$authorizationEventReceipt = $null
$script:transitionWitnessArmed = $false
$script:transitionExpectedPostgresExecCount = 0
$postgresStartCommandIssued = $false
$postgresStartedByTransition = $false
$emergencyContainmentTriggered = $false
$restartPolicyUpdateIssued = $false
$transitionSucceeded = $false
$preStartAcceptedAt = [DateTimeOffset]::MinValue
$gateProgram = $null
$watchdogProgram = $null
$watchdogCollectorType = $null
$watchdogProcess = $null
$watchdogLeaseProof = $null
$watchdogHeartbeatPublisher = $null
$watchdogCommitProof = $null
$watchdogExitProof = $null
$watchdogTerminalReceipt = $null
$watchdogHardDeadline = [DateTimeOffset]::MinValue
$watchdogStartFenceSha256 = $null
$watchdogStartAttemptSha256 = $null
$watchdogStartCompletionSha256 = $null
$startFenceLock = $null
$startCommandProof = $null
$parentContainmentProof = $null
$parentAuthorizationJson = $null
$parentAuthorizationPublished = $false
$transitionFailure = $null
$transitionCleanupFailures = [Collections.Generic.List[string]]::new()
$transitionSessionId = $null
$liveEnvelope = $null
$liveSha256 = $null
$liveJson = $null
$dockerApplication = Open-PinnedDockerApplicationLock -Application $dockerApplication
try {
    Open-ParentStageBoundary
    $gateProgram = Open-GateProgramBoundary -AbsoluteRoot $absoluteRoot
    $watchdogProgram = Open-TransitionWatchdogProgramBoundary `
        -AbsoluteRoot $absoluteRoot
    # This must be the first Docker proof in the invocation. Every later
    # Docker boundary is compared to it.
    $initialDockerEngine = Get-DockerEngineBoundary
    $hbaLockBoundary = Open-HeldHbaReadBoundary
    $networkRepair = Assert-NetworkRepairAttestation `
        -LiteralPath $absoluteNetworkRepairPath `
        -CurrentEngine $initialDockerEngine
    $networkRepairBinding = [ordered]@{
        path = [string]$networkRepair.path
        sha256 = [string]$networkRepair.sha256
        payload_sha256 = [string]$networkRepair.payload_sha256
        accepted_at = [string]$networkRepair.accepted_at
        expires_at = [string]$networkRepair.expires_at
        disposition = [string]$networkRepair.disposition
        post_first_sha256 = [string]$networkRepair.post_first_sha256
        post_second_sha256 = [string]$networkRepair.post_second_sha256
        final_attachment_sha256 = [string]$networkRepair.final_attachment_sha256
        docker_engine_contract_sha256 =
            [string]$networkRepair.docker_engine_contract_sha256
    }
    Assert-ParentStageBoundary -Boundary 'first candidate proof mutation'
    $historical = Assert-HistoricalLineage -ValidateBackupPayload -ValidateCatalogProbe
    $contract = Assert-ClosedSuccessorContract
    $composeMigrate = Assert-RenderedMigrateContract
    $postgres = Get-PostgresBoundary -ExpectedState Stopped `
        -ExpectedRestartPolicy 'unless-stopped'
    Assert-NetworkRepairCurrentPostgres -Evidence $networkRepair `
        -CurrentPostgres $postgres
    if (
        $postgres.started_at -cne [string]$historical.postgres.started_at -or
        $postgres.finished_at -eq '0001-01-01T00:00:00Z'
    ) {
        throw 'Stopped PostgreSQL does not continue the exact historical migration startup.'
    }
    $finishedAt = ConvertTo-RequiredUtcTime -Value $postgres.finished_at `
        -Label 'stopped PostgreSQL finished_at'
    $historicalStartedAt = ConvertTo-RequiredUtcTime -Value $postgres.started_at `
        -Label 'stopped PostgreSQL started_at'
    if ($finishedAt -le $historicalStartedAt) {
        throw 'Stopped PostgreSQL has an invalid retained start/finish boundary.'
    }
    $topology = Assert-ExclusiveRuntimeTopology -PostgresState Stopped
    $preWitnessDockerEngine = Get-DockerEngineBoundary
    if ($preWitnessDockerEngine.contract_sha256 -cne $initialDockerEngine.contract_sha256) {
        throw 'Docker context, engine, CLI, or Compose plugin drifted before witness arming.'
    }
    $eventWitness = Start-DockerEventWitness
    $transitionSessionId = [string]$eventWitness.session_id
    $script:transitionWitnessArmed = $true
    $initialBarrier = Invoke-DockerEventBarrier -Witness $eventWitness -Phase prestart
    $eventWitness.prefix_length = [int64]$initialBarrier.prefix_length
    $eventWitness.prefix_sha256 = [string]$initialBarrier.prefix_sha256
    [void](Get-DockerEventJournalSnapshot -Witness $eventWitness)
    $postBarrierPostgres = Get-PostgresBoundary -ExpectedState Stopped `
        -ExpectedRestartPolicy 'unless-stopped'
    foreach ($name in @(
        'container_id', 'image_id', 'config_image', 'restart_count',
        'started_at', 'finished_at', 'state', 'oom_killed', 'exit_code',
        'restart_policy', 'health_status', 'network_mode',
        'mount_contract_sha256', 'ports_contract_sha256',
        'network_contract_sha256', 'active_endpoint_sha256'
    )) {
        if ($postBarrierPostgres.$name -cne $postgres.$name) {
            throw 'Retained PostgreSQL drifted while arming the transition witness.'
        }
    }
    $topology = Assert-ExclusiveRuntimeTopology -PostgresState Stopped
    Assert-NetworkRepairEvidenceUnchanged -Evidence $networkRepair
    [void](Get-HeldHbaReadBoundary)

    Assert-ParentStageBoundary -Boundary 'retained PostgreSQL restart-policy mutation'
    $restartPolicyUpdateIssued = $true
    $updatedId = Get-OneNativeLine -Label 'retained PostgreSQL restart-policy update' `
        -Lines (Invoke-Docker -Arguments @(
            'update', '--restart=no', $productionPostgresId
        ))
    if ($updatedId -cne $productionPostgresId) {
        throw 'Docker restart-policy update returned an unexpected container identity.'
    }
    $armedPostgres = Get-PostgresBoundary -ExpectedState Stopped `
        -ExpectedRestartPolicy 'no'
    foreach ($name in @(
        'container_id', 'image_id', 'config_image', 'restart_count',
        'started_at', 'finished_at', 'state', 'oom_killed', 'exit_code',
        'health_status', 'network_mode', 'mount_contract_sha256',
        'ports_contract_sha256', 'network_contract_sha256',
        'active_endpoint_sha256'
    )) {
        if ($armedPostgres.$name -cne $postBarrierPostgres.$name) {
            throw 'Retained PostgreSQL drifted while disabling automatic restart.'
        }
    }
    if (
        $postBarrierPostgres.restart_policy -cne 'unless-stopped' -or
        $armedPostgres.restart_policy -cne 'no'
    ) {
        throw 'Retained PostgreSQL restart policy did not transition fail closed.'
    }
    $postgres = $armedPostgres
    $topology = Assert-ExclusiveRuntimeTopology -PostgresState Stopped
    $armedDockerEngine = Get-DockerEngineBoundary
    if ($armedDockerEngine.contract_sha256 -cne $initialDockerEngine.contract_sha256) {
        throw 'Docker context or engine changed while arming the transition witness.'
    }
    Assert-NetworkRepairEvidenceUnchanged -Evidence $networkRepair
    $hbaLockBoundary = Get-HeldHbaReadBoundary
    $watchdogCollectorType = Initialize-DockerEventCollectorType
    $startFenceCreatedAt = [DateTimeOffset]::UtcNow
    $startFenceMarker = [ordered]@{
        format = 'stratus-successor-transition-start-fence-v1'
        session_id = [string]$eventWitness.session_id
        owner_pid = [int]$watchdogProgram.owner_pid
        owner_start_time_utc = [string]$watchdogProgram.owner_start_time_utc
        created_at = $startFenceCreatedAt.ToString('o')
    }
    Write-CreateNewJson -LiteralPath $watchdogStartFencePath `
        -Value $startFenceMarker
    $startFenceDocument = Read-BoundedStrictJsonDocument `
        -LiteralPath $watchdogStartFencePath `
        -Label 'successor PostgreSQL start fence' -MaximumBytes 65536
    $watchdogStartFenceSha256 = [string]$startFenceDocument.sha256
    Assert-PostgresStartNotRevoked -RevokePath $watchdogStartRevokePath
    $acceptedAtValue = [DateTimeOffset]::UtcNow
    $preStartAcceptedAt = $acceptedAtValue
    $acceptedAt = $acceptedAtValue.ToString('o')
    $preStartExpiresAt = $acceptedAtValue.AddSeconds($preStartMaxAgeSeconds)
    $postgresStartDeadline = $acceptedAtValue.AddSeconds($postgresStartWindowSeconds)
    $watchdogHardDeadline = $postgresStartDeadline
    if ($watchdogHardDeadline -ge $script:parentStage.deadline) {
        throw 'The parent-stage lease must outlive the complete successor Transition deadline.'
    }
    $watchdogHandoff = [ordered]@{
        format = 'stratus-successor-transition-watchdog-handoff-v3'
        session_id = [string]$eventWitness.session_id
        owner_pid = [int]$watchdogProgram.owner_pid
        owner_start_time_utc = [string]$watchdogProgram.owner_start_time_utc
        hard_deadline_utc = $watchdogHardDeadline.ToString('o')
        heartbeat_timeout_seconds = [int]$watchdogHeartbeatTimeoutSeconds
        lease_path = $watchdogLeasePath
        receipt_path = $watchdogReceiptPath
        heartbeat_path = $watchdogHeartbeatPath
        commit_path = $watchdogCommitPath
        live_path = $absoluteLivePath
        prestart_path = $absolutePreStartPath
        start_fence_path = $watchdogStartFencePath
        start_fence_sha256 = $watchdogStartFenceSha256
        start_revoke_path = $watchdogStartRevokePath
        start_attempt_path = $watchdogStartAttemptPath
        start_completion_path = $watchdogStartCompletionPath
    }
    $lineageHashes = [ordered]@{}
    foreach ($entry in $lineageArtifacts.GetEnumerator()) {
        $lineageHashes[[string]$entry.Key] = [string]$entry.Value
    }
    $payload = [ordered]@{
        accepted_at = $acceptedAt
        validity = [ordered]@{
            max_age_seconds = $preStartMaxAgeSeconds
            postgres_start_window_seconds = $postgresStartWindowSeconds
            expires_at = $acceptedAtValue.AddSeconds($preStartMaxAgeSeconds).ToString('o')
            postgres_start_deadline = $acceptedAtValue.AddSeconds(
                $postgresStartWindowSeconds
            ).ToString('o')
        }
        historical = [ordered]@{
            pre_migration_attestation_sha256 = $historical.attestation_sha256
            pre_migration_payload_sha256 = $historical.payload_sha256
            predecessor_revision = $historical.predecessor_revision
            predecessor_image = $historical.predecessor_image
            predecessor_image_id = $historical.predecessor_image_id
            lineage_sha256 = $lineageHashes
            backup = $historical.backup
        }
        network_repair = $networkRepairBinding
        successor = [ordered]@{
            revision = $ExpectedRevision
            image = $CandidateImage
            image_id = $ExpectedImageId
        }
        closed_contract = [ordered]@{
            git_executable_path = $contract.git_executable_path
            git_executable_sha256 = $contract.git_executable_sha256
            git_executable_bytes = [int64]$contract.git_executable_bytes
            git_sha256 = $contract.git_contract_sha256
            git_entries = [int]$contract.git_contract_entries
            image_sha256 = $contract.image_contract_sha256
            image_files = [int]$contract.image_contract_files
            predecessor_contract_equals_successor = $true
        }
        compose_migrate = $composeMigrate
        postgres_prestart = $postgres
        hba_host_read_lock = $hbaLockBoundary
        topology = $topology
        transition_session = [ordered]@{
            mode = 'single-process'
            session_id = [string]$eventWitness.session_id
            collector_process_id = [int]$eventWitness.process_id
            collector_process_started_at = [string]$eventWitness.process_started_at
            collector_requested_at = [string]$eventWitness.requested_at
            docker_engine = $initialDockerEngine
            initial_barrier = $initialBarrier
            accepted_journal_prefix_bytes = [int64]$eventWitness.prefix_length
            accepted_journal_prefix_sha256 = [string]$eventWitness.prefix_sha256
            reconnect_permitted = $false
            watchdog_program = $watchdogProgram
            watchdog_handoff = $watchdogHandoff
            restart_policy_transition = [ordered]@{
                previous = 'unless-stopped'
                armed = 'no'
                command = @('update', '--restart=no', $productionPostgresId)
                command_issued = $true
                stopped_identity_rechecked = $true
                restore_on_success = $false
            }
            observation = [ordered]@{
                start_command_issued = $false
                start_completed = $false
            }
            start_proof = [ordered]@{
                start_owned_by_transition_process = $false
                successor_started_at = $null
                docker_start_event_time_nano = $null
            }
        }
        authorization = [ordered]@{
            postgres_start_permitted = $false
            external_postgres_start_permitted = $false
            transition_process_start_permitted = $true
            api_stage_permitted = $false
            worker_start_permitted = $false
            safety_control_start_permitted = $false
            live_transition_permitted = $false
            binance_kill_switch_removal_permitted = $false
        }
    }
    Assert-NetworkRepairEvidenceUnchanged -Evidence $networkRepair
    [void](Get-HeldHbaReadBoundary)
    $envelope = New-AttestationEnvelope `
        -Format 'stratus-post-migration-successor-transition-prestart-v2' -Payload $payload
    Write-CreateNewJson -LiteralPath $absolutePreStartPath -Value $envelope
    $preStartSha256 = Get-FileSha256 -LiteralPath $absolutePreStartPath
    [void](Get-DockerEventJournalSnapshot -Witness $eventWitness)

    Request-ParentStageTransitionLaunch -TransitionSessionId ([string]$eventWitness.session_id) `
        -PreStartSha256 $preStartSha256 -PreStartPayloadSha256 ([string]$envelope.payload_sha256) `
        -StartFenceSha256 $watchdogStartFenceSha256 -HardDeadline $watchdogHardDeadline
    $watchdogLeaseProof = Wait-TransitionWatchdogLease `
        -Watchdog $watchdogProcess -Program $watchdogProgram `
        -SessionId ([string]$eventWitness.session_id) `
        -HardDeadline $watchdogHardDeadline -LeasePath $watchdogLeasePath `
        -ReceiptPath $watchdogReceiptPath -HeartbeatPath $watchdogHeartbeatPath `
        -CommitPath $watchdogCommitPath -LivePath $absoluteLivePath `
        -PreStartPath $absolutePreStartPath -PreStartSha256 $preStartSha256 `
        -PreStartPayloadSha256 ([string]$envelope.payload_sha256) `
        -StartFencePath $watchdogStartFencePath `
        -StartFenceSha256 $watchdogStartFenceSha256 `
        -StartRevokePath $watchdogStartRevokePath `
        -StartAttemptPath $watchdogStartAttemptPath `
        -StartCompletionPath $watchdogStartCompletionPath `
        -PreStartPostgres $payload.postgres_prestart
    $watchdogHeartbeatPublisher = Start-WatchdogHeartbeatPublisher `
        -CollectorType $watchdogCollectorType -HeartbeatPath $watchdogHeartbeatPath `
        -SessionId ([string]$eventWitness.session_id) -Program $watchdogProgram `
        -LeasePath $watchdogLeasePath -LeaseSha256 $watchdogLeaseProof.sha256
    $firstHeartbeatEvidence = Assert-WatchdogHeartbeatPublisher `
        -Publisher $watchdogHeartbeatPublisher
    $firstHeartbeatDocument = Read-BoundedStrictJsonDocument `
        -LiteralPath $watchdogHeartbeatPath -Label 'first watchdog owner heartbeat' `
        -MaximumBytes 65536
    [void](Assert-WatchdogHeartbeatDocument -Document $firstHeartbeatDocument `
        -Evidence $firstHeartbeatEvidence -SessionId ([string]$eventWitness.session_id) `
        -Program $watchdogProgram -LeaseProof $watchdogLeaseProof `
        -LeasePath $watchdogLeasePath)
    Start-Sleep -Milliseconds 250
    [void](Assert-WatchdogHeartbeatPublisher -Publisher $watchdogHeartbeatPublisher)
    Assert-TransitionWatchdogProcessConnected -Watchdog $watchdogProcess
    if ((Get-FileSha256 -LiteralPath $watchdogLeasePath) -cne $watchdogLeaseProof.sha256) {
        throw 'The accepted watchdog lease changed before PostgreSQL start.'
    }
    if ((Get-FileSha256 -LiteralPath $absolutePreStartPath) -cne $preStartSha256) {
        throw 'The watchdog-bound PreStart artifact changed before PostgreSQL start.'
    }
    $watchdogArmedPostgres = Get-PostgresBoundary -ExpectedState Stopped `
        -ExpectedRestartPolicy 'no'
    foreach ($name in @(
        'container_id', 'image_id', 'config_image', 'restart_count',
        'started_at', 'finished_at', 'state', 'oom_killed', 'exit_code',
        'restart_policy', 'health_status', 'network_mode',
        'mount_contract_sha256', 'ports_contract_sha256',
        'network_contract_sha256', 'active_endpoint_sha256'
    )) {
        if ($watchdogArmedPostgres.$name -cne $payload.postgres_prestart.$name) {
            throw 'Retained PostgreSQL drifted after watchdog lease acceptance.'
        }
    }
    [void](Assert-ExclusiveRuntimeTopology -PostgresState Stopped)
    $watchdogArmedEngine = Get-DockerEngineBoundary
    if ($watchdogArmedEngine.contract_sha256 -cne $initialDockerEngine.contract_sha256) {
        throw 'Docker context or engine drifted after watchdog lease acceptance.'
    }
    Assert-NetworkRepairEvidenceUnchanged -Evidence $networkRepair
    [void](Get-HeldHbaReadBoundary)
    [void](Assert-TransitionWatchdogProgramUnchanged -Program $watchdogProgram)
    [void](Get-DockerEventJournalSnapshot -Witness $eventWitness)
    if ([DateTimeOffset]::UtcNow -ge $watchdogHardDeadline) {
        throw 'The watchdog lease expired before the retained PostgreSQL start.'
    }
    [void](Assert-WatchdogHeartbeatPublisher -Publisher $watchdogHeartbeatPublisher)
    Assert-TransitionWatchdogProcessConnected -Watchdog $watchdogProcess

    $startFenceAcquireBy = $watchdogHardDeadline.AddSeconds(-(
        $postgresStartWallClockTimeoutSeconds +
        $postgresStartContainmentReserveSeconds
    ))
    if ([DateTimeOffset]::UtcNow -ge $startFenceAcquireBy) {
        throw 'Insufficient watchdog containment reserve remains for PostgreSQL start.'
    }
    try {
        $startFenceLock = Open-ExclusivePostgresStartFence `
            -FencePath $watchdogStartFencePath `
            -ExpectedSha256 $watchdogStartFenceSha256 `
            -RevokePath $watchdogStartRevokePath `
            -AcquireBy $startFenceAcquireBy
        Assert-PostgresStartNotRevoked -RevokePath $watchdogStartRevokePath
        [void](Assert-WatchdogHeartbeatPublisher `
            -Publisher $watchdogHeartbeatPublisher)
        Assert-TransitionWatchdogProcessConnected -Watchdog $watchdogProcess
        if (
            (Get-FileSha256 -LiteralPath $watchdogLeasePath) -cne
                $watchdogLeaseProof.sha256 -or
            (Get-FileSha256 -LiteralPath $absolutePreStartPath) -cne
                $preStartSha256 -or
            (Get-HeldFileStreamSha256 -Stream $startFenceLock `
                -Label 'successor PostgreSQL start fence') -cne
                $watchdogStartFenceSha256
        ) {
            throw 'A watchdog-bound start artifact changed inside the start fence.'
        }
        $startReceiptItem = Get-Item -LiteralPath $watchdogReceiptPath -Force
        if (
            $startReceiptItem.Length -ne 0 -or
            (Test-Path -LiteralPath $watchdogCommitPath) -or
            (Test-Path -LiteralPath $absoluteLivePath) -or
            (Test-Path -LiteralPath $watchdogStartAttemptPath) -or
            (Test-Path -LiteralPath $watchdogStartCompletionPath)
        ) {
            throw 'The watchdog reached a terminal or commit state before PostgreSQL start.'
        }
        Assert-PostgresStartNotRevoked -RevokePath $watchdogStartRevokePath
        if (
            [DateTimeOffset]::UtcNow.AddSeconds(
                $postgresStartWallClockTimeoutSeconds +
                $postgresStartContainmentReserveSeconds
            ) -ge $watchdogHardDeadline
        ) {
            throw 'Insufficient watchdog containment reserve remains inside the start fence.'
        }
        $startAttemptIssuedAt = [DateTimeOffset]::UtcNow
        $startAttempt = [ordered]@{
            format = 'stratus-successor-transition-start-attempt-v1'
            session_id = [string]$eventWitness.session_id
            owner_pid = [int]$watchdogProgram.owner_pid
            owner_start_time_utc = [string]$watchdogProgram.owner_start_time_utc
            start_fence_path = $watchdogStartFencePath
            start_fence_sha256 = $watchdogStartFenceSha256
            command = @('start', $productionPostgresId)
            issued_at = $startAttemptIssuedAt.ToString('o')
        }
        Assert-ParentStageBoundary -Boundary 'retained PostgreSQL start-attempt publication'
        Write-CreateNewJson -LiteralPath $watchdogStartAttemptPath `
            -Value $startAttempt
        $startAttemptDocument = Read-BoundedStrictJsonDocument `
            -LiteralPath $watchdogStartAttemptPath `
            -Label 'successor PostgreSQL start attempt' -MaximumBytes 131072
        $watchdogStartAttemptSha256 = [string]$startAttemptDocument.sha256
        $postgresStartCommandIssued = $true
        $startCommandProof = Invoke-BoundedPinnedDockerStart `
            -ContainerId $productionPostgresId `
            -CollectorType $watchdogCollectorType
        $startCompletion = [ordered]@{
            format = 'stratus-successor-transition-start-complete-v1'
            session_id = [string]$eventWitness.session_id
            owner_pid = [int]$watchdogProgram.owner_pid
            owner_start_time_utc = [string]$watchdogProgram.owner_start_time_utc
            start_attempt_path = $watchdogStartAttemptPath
            start_attempt_sha256 = $watchdogStartAttemptSha256
            outcome = 'started'
            completed_at = [string]$startCommandProof.completed_at
            bounded_start = $startCommandProof
        }
        Write-CreateNewJson -LiteralPath $watchdogStartCompletionPath `
            -Value $startCompletion
        $startCompletionDocument = Read-BoundedStrictJsonDocument `
            -LiteralPath $watchdogStartCompletionPath `
            -Label 'successor PostgreSQL start completion' -MaximumBytes 262144
        $watchdogStartCompletionSha256 = [string]$startCompletionDocument.sha256
        $postgresStartedByTransition = $true
        Assert-ParentStageBoundary -Boundary 'retained PostgreSQL start completion'
        Assert-PostgresStartNotRevoked -RevokePath $watchdogStartRevokePath
        Assert-TransitionWatchdogProcessConnected -Watchdog $watchdogProcess
        $postStartReceiptItem = Get-Item -LiteralPath $watchdogReceiptPath -Force
        if ($postStartReceiptItem.Length -ne 0) {
            throw 'The watchdog became terminal during bounded PostgreSQL start.'
        }
    }
    catch {
        if ($postgresStartCommandIssued) {
            $emergencyContainmentTriggered = $true
            $parentContainmentProof = Invoke-ParentCausalPostgresContainment `
                -HeldFence $startFenceLock `
                -FencePath $watchdogStartFencePath `
                -FenceSha256 $watchdogStartFenceSha256 `
                -RevokePath $watchdogStartRevokePath `
                -StartAttemptPath $watchdogStartAttemptPath `
                -StartCompletionPath $watchdogStartCompletionPath `
                -ExpectedStartAttemptSha256 $watchdogStartAttemptSha256 `
                -ExpectedStartCompletionSha256 $watchdogStartCompletionSha256 `
                -StartCommandProof $startCommandProof `
                -SessionId ([string]$eventWitness.session_id) `
                -Program $watchdogProgram -CollectorType $watchdogCollectorType `
                -ExpectedEngine $initialDockerEngine `
                -ExpectedPostgres $payload.postgres_prestart `
                -ContainmentPath $parentContainmentPath `
                -Trigger 'parent_start_boundary_failure'
            $startFenceLock = $null
        }
        throw
    }
    finally {
        if ($null -ne $startFenceLock) {
            $startFenceLock.Dispose()
            $startFenceLock = $null
        }
    }
    $health = Wait-PostgresContainerHealthy `
        -StartRevokePath $watchdogStartRevokePath `
        -Watchdog $watchdogProcess `
        -HeartbeatPublisher $watchdogHeartbeatPublisher
    Assert-PostgresStartNotRevoked -RevokePath $watchdogStartRevokePath
    [void](Assert-WatchdogHeartbeatPublisher -Publisher $watchdogHeartbeatPublisher)
    Assert-TransitionWatchdogProcessConnected -Watchdog $watchdogProcess
    $liveValidationStartedAt = [DateTimeOffset]::UtcNow
    if ($liveValidationStartedAt -gt $preStartExpiresAt.AddSeconds($clockSkewSeconds)) {
        throw 'Successor PreStart authorization is outside its bounded Live window.'
    }
    $postgres = Get-PostgresBoundary -ExpectedState Running `
        -ExpectedRestartPolicy 'no'
    $topology = Assert-ExclusiveRuntimeTopology -PostgresState Running
    $preStartPostgres = $payload.postgres_prestart
if (
    $postgres.container_id -cne $preStartPostgres.container_id -or
    $postgres.image_id -cne $preStartPostgres.image_id -or
    $postgres.config_image -cne $preStartPostgres.config_image -or
    $postgres.restart_count -ne $preStartPostgres.restart_count -or
    $postgres.restart_policy -cne 'no' -or
    $preStartPostgres.restart_policy -cne 'no' -or
    $postgres.mount_contract_sha256 -cne $preStartPostgres.mount_contract_sha256
) {
    throw 'Running PostgreSQL identity drifted after successor PreStart acceptance.'
}
$newStartedAt = ConvertTo-RequiredUtcTime -Value $postgres.started_at `
    -Label 'successor PostgreSQL started_at'
$priorFinishedAt = ConvertTo-RequiredUtcTime -Value $preStartPostgres.finished_at `
    -Label 'prestart PostgreSQL finished_at'
if (
    $newStartedAt -le $preStartAcceptedAt -or
    $newStartedAt -gt $postgresStartDeadline -or
    $newStartedAt -gt $liveValidationStartedAt.AddSeconds($clockSkewSeconds) -or
    $newStartedAt -le $priorFinishedAt -or
    $postgres.started_at -ceq $preStartPostgres.started_at
) {
    throw 'Running PostgreSQL StartedAt does not prove the bounded post-PreStart restart.'
}
$initialDockerEngineAfterStart = Get-DockerEngineBoundary
if ($initialDockerEngineAfterStart.contract_sha256 -cne $initialDockerEngine.contract_sha256) {
    throw 'Docker context or engine changed during the retained PostgreSQL start.'
}
[void](Assert-HistoricalLineage -ValidateBackupPayload)
[void](Assert-WatchdogHeartbeatPublisher -Publisher $watchdogHeartbeatPublisher)
Assert-TransitionWatchdogProcessConnected -Watchdog $watchdogProcess
Assert-PostgresStartNotRevoked -RevokePath $watchdogStartRevokePath
$initialEventCheckedAt = [DateTimeOffset]::UtcNow
if ($initialEventCheckedAt -gt $preStartExpiresAt.AddSeconds($clockSkewSeconds)) {
    throw 'Successor PreStart authorization expired before the database proof.'
}
[void](Get-DockerEventJournalSnapshot -Witness $eventWitness)

# No PostgreSQL query is permitted above this point. The fixed byte proof,
# reload, normalized inventory, and byte recheck are the first privileged
# database operations after the retained container start.
Assert-PostgresStartNotRevoked -RevokePath $watchdogStartRevokePath
$hba = Assert-HbaFirstBoundary
[void](Assert-WatchdogHeartbeatPublisher -Publisher $watchdogHeartbeatPublisher)
Assert-PostgresStartNotRevoked -RevokePath $watchdogStartRevokePath
$firstSnapshot = Get-StrictLiveSnapshot
$firstClock = Assert-StrictLiveSnapshot -Snapshot $firstSnapshot `
    -Label 'initial successor live state'
$secondSnapshot = Get-StrictLiveSnapshot
[void](Assert-WatchdogHeartbeatPublisher -Publisher $watchdogHeartbeatPublisher)
Assert-PostgresStartNotRevoked -RevokePath $watchdogStartRevokePath
$secondClock = Assert-StrictLiveSnapshot -Snapshot $secondSnapshot `
    -Label 'immediate successor live state'
if ($secondClock -lt $firstClock -or ($secondClock - $firstClock).TotalSeconds -gt 60) {
    throw 'Successor live-state proof was not repeated within the bounded 60-second window.'
}
$finalBoundary = Assert-FinalLiveBoundary -InitialPostgres $postgres `
    -InitialDockerEngine $initialDockerEngine `
    -InitialHba $hba `
    -PreStartExpiresAt $preStartExpiresAt
[void](Assert-WatchdogHeartbeatPublisher -Publisher $watchdogHeartbeatPublisher)
Assert-TransitionWatchdogProcessConnected -Watchdog $watchdogProcess
Assert-PostgresStartNotRevoked -RevokePath $watchdogStartRevokePath
$authorizationWitness = Start-DockerEventWitness
$finalBarrier = Invoke-DockerEventBarrier -Witness $authorizationWitness -Phase final
$authorizationInitialBarrier = $finalBarrier
[void](Wait-DockerEventBarrierObservation -Witness $eventWitness `
    -Barrier $authorizationInitialBarrier)
Stop-DockerEventWitness -Witness $eventWitness -PreserveFiles -Seal
$eventReceipt = Assert-DockerEventContract -Witness $eventWitness `
    -InitialBarrier $initialBarrier -FinalBarrier $finalBarrier `
    -ExpectedStartedAt $newStartedAt
[void](Assert-RegularFile -LiteralPath $absolutePreStartPath)
if ((Get-FileSha256 -LiteralPath $absolutePreStartPath) -cne $preStartSha256) {
    throw 'The transition-owned PreStart artifact changed before Live acceptance.'
}
Assert-NetworkRepairEvidenceUnchanged -Evidence $networkRepair
[void](Get-HeldHbaReadBoundary)
$finalBoundaryMilliseconds = [int64]([Math]::Floor(
    ([double][int64]$finalBarrier.destroy_time_nano) / 1000000.0
))
$finalAcceptedAt = [DateTimeOffset]::FromUnixTimeMilliseconds($finalBoundaryMilliseconds)
$artifactWrittenAt = [DateTimeOffset]::UtcNow
$lastCheckedAt = ConvertTo-RequiredUtcTime -Value $finalBoundary.checked_at `
    -Label 'final checked_at'
if (
    $finalAcceptedAt -lt $lastCheckedAt.AddSeconds(-1) -or
    $finalAcceptedAt -gt $artifactWrittenAt.AddSeconds($clockSkewSeconds) -or
    $artifactWrittenAt -gt $preStartExpiresAt.AddSeconds($clockSkewSeconds) -or
    $artifactWrittenAt -gt $watchdogHardDeadline
) {
    throw 'Successor PreStart authorization expired before Live evidence creation.'
}
$watchdogHeartbeatAtLive = Assert-WatchdogHeartbeatPublisher `
    -Publisher $watchdogHeartbeatPublisher
Assert-TransitionWatchdogProcessConnected -Watchdog $watchdogProcess
Assert-PostgresStartNotRevoked -RevokePath $watchdogStartRevokePath
[void](Assert-TransitionWatchdogProgramUnchanged -Program $watchdogProgram)
if ((Get-FileSha256 -LiteralPath $watchdogLeasePath) -cne $watchdogLeaseProof.sha256) {
    throw 'The watchdog lease changed before Live evidence creation.'
}
if (
    [string]::IsNullOrWhiteSpace([string]$watchdogStartAttemptSha256) -or
    [string]::IsNullOrWhiteSpace([string]$watchdogStartCompletionSha256) -or
    (Get-FileSha256 -LiteralPath $watchdogStartAttemptPath) -cne
        $watchdogStartAttemptSha256 -or
    (Get-FileSha256 -LiteralPath $watchdogStartCompletionPath) -cne
        $watchdogStartCompletionSha256
) {
    throw 'The completed PostgreSQL start protocol changed before Live evidence creation.'
}
$watchdogBinding = [ordered]@{
    format = 'stratus-successor-transition-watchdog-binding-v3'
    session_id = [string]$eventWitness.session_id
    owner_pid = [int]$watchdogProgram.owner_pid
    owner_start_time_utc = [string]$watchdogProgram.owner_start_time_utc
    hard_deadline_utc = $watchdogHardDeadline.ToString('o')
    heartbeat_timeout_seconds = [int]$watchdogHeartbeatTimeoutSeconds
    lease_path = $watchdogLeasePath
    lease_sha256 = [string]$watchdogLeaseProof.sha256
    receipt_path = $watchdogReceiptPath
    heartbeat_path = $watchdogHeartbeatPath
    commit_path = $watchdogCommitPath
    start_fence_path = $watchdogStartFencePath
    start_fence_sha256 = $watchdogStartFenceSha256
    start_revoke_path = $watchdogStartRevokePath
    start_attempt_path = $watchdogStartAttemptPath
    start_attempt_sha256 = $watchdogStartAttemptSha256
    start_completion_path = $watchdogStartCompletionPath
    start_completion_sha256 = $watchdogStartCompletionSha256
    start_revoke_absent = $true
}

$livePayload = [ordered]@{
    accepted_at = $finalAcceptedAt.ToString('o')
    artifact_written_at = $artifactWrittenAt.ToString('o')
    historical = [ordered]@{
        pre_migration_attestation_sha256 = $historical.attestation_sha256
        pre_migration_payload_sha256 = $historical.payload_sha256
        predecessor_revision = $historical.predecessor_revision
        predecessor_image_id = $historical.predecessor_image_id
        backup = $historical.backup
    }
    network_repair = $networkRepairBinding
    prestart = [ordered]@{
        path = $absolutePreStartPath
        sha256 = $preStartSha256
        payload_sha256 = $envelope.payload_sha256
        accepted_at = $acceptedAt
    }
    successor = [ordered]@{
        revision = $ExpectedRevision
        image = $CandidateImage
        image_id = $ExpectedImageId
    }
    closed_contract = [ordered]@{
        git_executable_path = $contract.git_executable_path
        git_executable_sha256 = $contract.git_executable_sha256
        git_executable_bytes = [int64]$contract.git_executable_bytes
        git_sha256 = $contract.git_contract_sha256
        git_entries = [int]$contract.git_contract_entries
        image_sha256 = $contract.image_contract_sha256
        image_files = [int]$contract.image_contract_files
        predecessor_contract_equals_successor = $true
    }
    postgres_transition = [ordered]@{
        container_id = $postgres.container_id
        image_id = $postgres.image_id
        config_image = $postgres.config_image
        restart_count = $postgres.restart_count
        previous_started_at = $preStartPostgres.started_at
        previous_finished_at = $preStartPostgres.finished_at
        successor_started_at = $postgres.started_at
        state = $postgres.state
        oom_killed = $postgres.oom_killed
        restart_policy = $postgres.restart_policy
        health_status = $postgres.health_status
        health = $health
        network_mode = $postgres.network_mode
        mount_contract_sha256 = $postgres.mount_contract_sha256
        ports_contract_sha256 = $postgres.ports_contract_sha256
        network_contract_sha256 = $postgres.network_contract_sha256
        active_endpoint_sha256 = $postgres.active_endpoint_sha256
    }
    transition_session = [ordered]@{
        mode = 'single-process'
        session_id = [string]$eventWitness.session_id
        docker_engine = $initialDockerEngine
        event_witness = $eventReceipt
        watchdog_program = $watchdogProgram
        watchdog = $watchdogBinding
        watchdog_lease = [ordered]@{
            path = $watchdogLeasePath
            sha256 = [string]$watchdogLeaseProof.sha256
            armed_at = [string]$watchdogLeaseProof.armed_at
            docker_engine_contract_sha256 =
                [string]$watchdogLeaseProof.docker_engine.contract_sha256
        }
        heartbeat_at_live = $watchdogHeartbeatAtLive
        restart_policy_transition = [ordered]@{
            previous = 'unless-stopped'
            armed = 'no'
            command = @('update', '--restart=no', $productionPostgresId)
            command_issued = $true
            stopped_identity_rechecked = $true
            restore_on_success = $false
        }
        observation = [ordered]@{
            start_command_issued = $true
            start_completed = $true
        }
        start_proof = [ordered]@{
            start_owned_by_transition_process = $true
            successor_started_at = $postgres.started_at
            docker_start_event_time_nano = [int64]$eventReceipt.postgres_start_time_nano
            start_fence_path = $watchdogStartFencePath
            start_fence_sha256 = $watchdogStartFenceSha256
            start_attempt_path = $watchdogStartAttemptPath
            start_attempt_sha256 = $watchdogStartAttemptSha256
            start_completion_path = $watchdogStartCompletionPath
            start_completion_sha256 = $watchdogStartCompletionSha256
            start_revoke_absent = $true
            bounded_start = $startCommandProof
        }
        rollback = [ordered]@{
            on_failure = $true
            stop_timeout_seconds = $postgresRollbackTimeoutSeconds
            executed = $false
            emergency_containment = $false
        }
    }
    topology = $topology
    hba = $hba
    final_boundary = $finalBoundary
    database = [ordered]@{
        schema_head = $secondSnapshot.schema_head
        indexes_exact = $secondSnapshot.indexes_exact
        functions_exact = $secondSnapshot.functions_exact
        runtime_boundary = $secondSnapshot.runtime_boundary
        safety_boundary = $secondSnapshot.safety_boundary
        control_triggers_exact = $secondSnapshot.control_triggers_exact
        commit_triggers_exact = $secondSnapshot.commit_triggers_exact
        identity_triggers_exact = $secondSnapshot.identity_triggers_exact
        fail_closed_controls = $true
        zero_live_state = $true
        zero_pending_commands = $true
        zero_other_sessions = $true
        bootstrap_audit_exact = $secondSnapshot.bootstrap_audit_exact
        fence_armed = $secondSnapshot.fence_armed
        active_lease_exists = $secondSnapshot.active_lease_exists
        execution_authorized = $secondSnapshot.execution_authorized
        first_database_clock = [string]$firstSnapshot.database_clock
        second_database_clock = [string]$secondSnapshot.database_clock
    }
    authorization = [ordered]@{
        api_stage_permitted = $false
        worker_start_permitted = $false
        safety_control_start_permitted = $false
        live_transition_permitted = $false
        binance_kill_switch_removal_permitted = $false
        migration_container_permitted = $false
    }
}
$liveEnvelope = New-AttestationEnvelope `
    -Format 'stratus-post-migration-successor-transition-live-v2' -Payload $livePayload
$liveJson = $liveEnvelope | ConvertTo-Json -Depth 20 -Compress
Assert-PostgresStartNotRevoked -RevokePath $watchdogStartRevokePath
Write-CreateNewJson -LiteralPath $absoluteLivePath -Value $liveEnvelope
$liveSha256 = Get-FileSha256 -LiteralPath $absoluteLivePath
$startFenceLock = Open-ExclusivePostgresStartFence `
    -FencePath $watchdogStartFencePath `
    -ExpectedSha256 $watchdogStartFenceSha256 `
    -RevokePath $watchdogStartRevokePath `
    -AcquireBy ([DateTimeOffset]::UtcNow.AddSeconds(5))
Assert-PostgresStartNotRevoked -RevokePath $watchdogStartRevokePath
if (
    (Get-FileSha256 -LiteralPath $absolutePreStartPath) -cne $preStartSha256 -or
    (Get-FileSha256 -LiteralPath $absoluteLivePath) -cne $liveSha256 -or
    (Get-FileSha256 -LiteralPath $watchdogLeasePath) -cne $watchdogLeaseProof.sha256 -or
    (Get-HeldFileStreamSha256 -Stream $startFenceLock `
        -Label 'held precommit PostgreSQL start fence') -cne
        $watchdogStartFenceSha256 -or
    (Get-FileSha256 -LiteralPath $watchdogStartAttemptPath) -cne
        $watchdogStartAttemptSha256 -or
    (Get-FileSha256 -LiteralPath $watchdogStartCompletionPath) -cne
        $watchdogStartCompletionSha256 -or
    (Test-Path -LiteralPath $watchdogStartRevokePath)
) {
    throw 'A watchdog-bound transition artifact changed before commit publication.'
}
Assert-NetworkRepairEvidenceUnchanged -Evidence $networkRepair
[void](Get-HeldHbaReadBoundary)
$preCommitEngine = Get-DockerEngineBoundary
if ($preCommitEngine.contract_sha256 -cne $initialDockerEngine.contract_sha256) {
    throw 'Docker context or engine drifted before watchdog commit publication.'
}
$preCommitPostgres = Get-PostgresBoundary -ExpectedState Running `
    -ExpectedRestartPolicy 'no'
foreach ($name in @(
    'container_id', 'image_id', 'config_image', 'restart_count',
    'started_at', 'finished_at', 'state', 'oom_killed', 'exit_code',
    'restart_policy', 'health_status', 'network_mode',
    'mount_contract_sha256', 'ports_contract_sha256',
    'network_contract_sha256', 'active_endpoint_sha256'
)) {
    if ($preCommitPostgres.$name -cne $finalBoundary.postgres.$name) {
        throw 'Retained PostgreSQL drifted before watchdog commit publication.'
    }
}
[void](Assert-ExclusiveRuntimeTopology -PostgresState Running)
if ([DateTimeOffset]::UtcNow -gt $watchdogHardDeadline) {
    throw 'The watchdog deadline expired before the Live commit marker.'
}
Assert-PostgresStartNotRevoked -RevokePath $watchdogStartRevokePath
$commitMarker = [ordered]@{
    format = 'stratus-successor-transition-watchdog-commit-v1'
    session_id = [string]$eventWitness.session_id
    owner_pid = [int]$watchdogProgram.owner_pid
    owner_start_time_utc = [string]$watchdogProgram.owner_start_time_utc
    lease_path = $watchdogLeasePath
    lease_sha256 = [string]$watchdogLeaseProof.sha256
    live_path = $absoluteLivePath
    live_sha256 = $liveSha256
    committed_at = [DateTimeOffset]::UtcNow.ToString('o')
}
Write-CreateNewJson -LiteralPath $watchdogCommitPath -Value $commitMarker
$commitDocument = Read-BoundedStrictJsonDocument -LiteralPath $watchdogCommitPath `
    -Label 'successor transition watchdog commit' -MaximumBytes 65536
$watchdogCommitProof = Assert-WatchdogCommitDocument -Document $commitDocument `
    -SessionId ([string]$eventWitness.session_id) -Program $watchdogProgram `
    -LeaseProof $watchdogLeaseProof -LeasePath $watchdogLeasePath `
    -LivePath $absoluteLivePath -LiveSha256 $liveSha256 `
    -HardDeadline $watchdogHardDeadline
try { $startFenceLock.Dispose() }
finally { $startFenceLock = $null }
$watchdogExitProof = Wait-TransitionWatchdogExit -Watchdog $watchdogProcess `
    -HeartbeatPublisher $null `
    -HardDeadline $watchdogHardDeadline
$watchdogTerminalReceipt = Assert-TransitionWatchdogSuccessReceipt `
    -ExitProof $watchdogExitProof -ReceiptPath $watchdogReceiptPath `
    -SessionId ([string]$eventWitness.session_id) -Program $watchdogProgram `
    -LeaseProof $watchdogLeaseProof -CommitProof $watchdogCommitProof `
    -CommitPath $watchdogCommitPath -LivePath $absoluteLivePath `
    -LiveSha256 $liveSha256 -LivePayloadSha256 ([string]$liveEnvelope.payload_sha256) `
    -PostgresStartedAt ([string]$postgres.started_at) `
    -HardDeadline $watchdogHardDeadline
# The watchdog terminal receipt is deliberately non-actionable.  Only this
# owner process may publish the API-stage authorization, after proving exit 0,
# sealed stdout byte equality, and one final fenced running boundary.
[void](Stop-AcceptedTerminalHeartbeatPublisher `
    -Publisher $watchdogHeartbeatPublisher)
$watchdogHeartbeatPublisher = $null
Stop-DockerEventWitness -Witness $eventWitness
$eventWitness = $null
$watchdogProcessId = [int]$watchdogProcess.process_id
$watchdogProcessStartedAt = [string]$watchdogProcess.process_started_at
$watchdogProcess.collector.Dispose()
if (Test-Path -LiteralPath $watchdogProcess.temp_root) {
    Remove-Item -LiteralPath $watchdogProcess.temp_root -Recurse -Force `
        -ErrorAction Stop
}
$watchdogProcess.process.Dispose()
$watchdogProcess = $null

$startFenceLock = Open-ExclusivePostgresStartFence `
    -FencePath $watchdogStartFencePath `
    -ExpectedSha256 $watchdogStartFenceSha256 `
    -RevokePath $watchdogStartRevokePath `
    -AcquireBy ([DateTimeOffset]::UtcNow.AddSeconds(5))
Assert-PostgresStartNotRevoked -RevokePath $watchdogStartRevokePath
if (
    (Test-Path -LiteralPath $parentContainmentPath) -or
    (Test-Path -LiteralPath $parentAuthorizationPath) -or
    (Get-FileSha256 -LiteralPath $absolutePreStartPath) -cne $preStartSha256 -or
    (Get-FileSha256 -LiteralPath $absoluteLivePath) -cne $liveSha256 -or
    (Get-FileSha256 -LiteralPath $watchdogLeasePath) -cne
        $watchdogLeaseProof.sha256 -or
    (Get-FileSha256 -LiteralPath $watchdogReceiptPath) -cne
        $watchdogTerminalReceipt.sha256 -or
    (Get-FileSha256 -LiteralPath $watchdogCommitPath) -cne
        $watchdogCommitProof.sha256 -or
    (Get-HeldFileStreamSha256 -Stream $startFenceLock `
        -Label 'held terminal parent authorization start fence') -cne
        $watchdogStartFenceSha256 -or
    (Get-FileSha256 -LiteralPath $watchdogStartAttemptPath) -cne
        $watchdogStartAttemptSha256 -or
    (Get-FileSha256 -LiteralPath $watchdogStartCompletionPath) -cne
        $watchdogStartCompletionSha256
) {
    throw 'A terminal transition artifact changed before parent authorization.'
}
$parentStartProtocol = Get-ParentStartProtocolState `
    -SessionId $transitionSessionId -Program $watchdogProgram `
    -StartFencePath $watchdogStartFencePath `
    -StartFenceSha256 $watchdogStartFenceSha256 `
    -StartAttemptPath $watchdogStartAttemptPath `
    -StartCompletionPath $watchdogStartCompletionPath `
    -ExpectedStartAttemptSha256 $watchdogStartAttemptSha256 `
    -ExpectedStartCompletionSha256 $watchdogStartCompletionSha256 `
    -StartCommandProof $startCommandProof
if ($parentStartProtocol.status -cne 'completed') {
    throw 'Parent authorization lacks an exact completed start protocol.'
}
$terminalAt = ConvertTo-CanonicalUtcTime `
    -Value $watchdogTerminalReceipt.terminal_at `
    -Label 'accepted watchdog terminal time'
$authorizationPostgres = Get-BoundedParentRunningPostgresBoundary `
    -CollectorType $watchdogCollectorType `
    -ExpectedEngine $initialDockerEngine `
    -ExpectedPostgres $finalBoundary.postgres
foreach ($name in @(
    'container_id', 'image_id', 'config_image', 'restart_count', 'started_at',
    'finished_at', 'state', 'oom_killed', 'exit_code', 'restart_policy',
    'health_status', 'network_mode', 'mount_contract_sha256',
    'ports_contract_sha256', 'network_contract_sha256',
    'active_endpoint_sha256'
)) {
    if ($authorizationPostgres.$name -cne $finalBoundary.postgres.$name) {
        throw 'Retained PostgreSQL drifted before parent authorization.'
    }
}
Assert-NetworkRepairEvidenceUnchanged -Evidence $networkRepair
[void](Get-HeldHbaReadBoundary)
[void](Assert-GateProgramUnchanged -Program $gateProgram)
[void](Assert-TransitionWatchdogProgramUnchanged -Program $watchdogProgram)
Assert-PostgresStartNotRevoked -RevokePath $watchdogStartRevokePath
$authorizationFinalBarrier = Invoke-BoundedAuthorizationEventBarrier `
    -Witness $authorizationWitness -CollectorType $watchdogCollectorType
Stop-DockerEventWitness -Witness $authorizationWitness -PreserveFiles -Seal
$authorizationEventReceipt = Assert-AuthorizationDockerEventContract `
    -Witness $authorizationWitness `
    -InitialBarrier $authorizationInitialBarrier `
    -FinalBarrier $authorizationFinalBarrier
Stop-DockerEventWitness -Witness $authorizationWitness
$authorizationWitness = $null

$authorizationObservedNano = [int64]`
    $authorizationEventReceipt.final_barrier_destroy_time_nano
$authorizationObservedMilliseconds = [int64][Math]::Floor(
    ([double]$authorizationObservedNano) / 1000000.0
)
$authorizationAt = [DateTimeOffset]::FromUnixTimeMilliseconds(
    $authorizationObservedMilliseconds
).AddTicks([int64][Math]::Floor(
    ([double]($authorizationObservedNano -
        ($authorizationObservedMilliseconds * 1000000))) / 100.0
))
if (
    $authorizationAt -lt $terminalAt.AddSeconds(-$clockSkewSeconds) -or
    $authorizationAt -gt $watchdogHardDeadline.AddSeconds($clockSkewSeconds)
) {
    throw 'Parent authorization checkpoint is outside the terminal transition window.'
}

$parentAuthorization = [ordered]@{
    format = 'stratus-successor-transition-parent-authorization-v1'
    status = 'authorized'
    authorized_at = $authorizationAt.ToString('o')
    publication_started_at = $null
    valid_until = $watchdogHardDeadline.ToString('o')
    docker_state_observed_through_time_nano = $authorizationObservedNano
    session_id = $transitionSessionId
    owner_pid = [int]$watchdogProgram.owner_pid
    owner_start_time_utc = [string]$watchdogProgram.owner_start_time_utc
    authorizer = [ordered]@{
        gate_script_path = [string]$gateProgram.script_path
        gate_script_sha256 = [string]$gateProgram.script_sha256
        gate_script_bytes = [int64]$gateProgram.script_bytes
        host_executable_path = [string]$watchdogProgram.host_path
        host_executable_sha256 = [string]$watchdogProgram.host_sha256
        host_executable_bytes = [int64]$watchdogProgram.host_bytes
        process_id = [int]$watchdogProgram.owner_pid
        process_started_at = [string]$watchdogProgram.owner_start_time_utc
    }
    parent_authorization_path = $parentAuthorizationPath
    watchdog_program_path = [string]$watchdogProgram.script_path
    watchdog_program_sha256 = [string]$watchdogProgram.script_sha256
    watchdog_process_id = $watchdogProcessId
    watchdog_process_started_at = $watchdogProcessStartedAt
    watchdog_exit_code = [int]$watchdogExitProof.exit_code
    sealed_stdout_sha256 = [string]$watchdogExitProof.stdout.sha256
    sealed_stdout_bytes = [int64]$watchdogExitProof.stdout.bytes
    sealed_stderr_sha256 =
        'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
    sealed_stderr_bytes = [int64]0
    lease_path = $watchdogLeasePath
    lease_sha256 = [string]$watchdogLeaseProof.sha256
    receipt_path = $watchdogReceiptPath
    receipt_sha256 = [string]$watchdogTerminalReceipt.sha256
    commit_path = $watchdogCommitPath
    commit_sha256 = [string]$watchdogCommitProof.sha256
    live_path = $absoluteLivePath
    live_sha256 = $liveSha256
    live_payload_sha256 = [string]$liveEnvelope.payload_sha256
    start_fence_path = $watchdogStartFencePath
    start_fence_sha256 = $watchdogStartFenceSha256
    start_attempt_path = $watchdogStartAttemptPath
    start_attempt_sha256 = $watchdogStartAttemptSha256
    start_completion_path = $watchdogStartCompletionPath
    start_completion_sha256 = $watchdogStartCompletionSha256
    docker_engine_contract_sha256 =
        [string]$authorizationPostgres.docker_engine_contract_sha256
    postgres_container_id = [string]$authorizationPostgres.container_id
    postgres_image_id = [string]$authorizationPostgres.image_id
    postgres_config_image = [string]$authorizationPostgres.config_image
    postgres_restart_count = [int]$authorizationPostgres.restart_count
    postgres_started_at = [string]$authorizationPostgres.started_at
    authorization_witness = $authorizationEventReceipt
    parent_stage = $script:parentStage.binding
    authorization = [ordered]@{
        api_stage_permitted = $true
        worker_start_permitted = $false
        safety_control_start_permitted = $false
        live_transition_permitted = $false
        binance_kill_switch_removal_permitted = $false
        migration_container_permitted = $false
    }
}
Assert-ParentStageBoundary -Boundary 'parent authorization pre-publication'
$parentAuthorizationJson = Publish-ParentAuthorization `
    -LiteralPath $parentAuthorizationPath -Value $parentAuthorization `
    -Published ([ref]$parentAuthorizationPublished) `
    -ObservedAt $authorizationAt -NotAfter $watchdogHardDeadline `
    -MaximumDelaySeconds $parentAuthorizationPublishWindowSeconds
Assert-ParentStageBoundary -Boundary 'parent authorization post-publication'
$transitionSucceeded = $true
try {
    $startFenceLock.Dispose()
    $startFenceLock = $null
}
catch {
    $transitionCleanupFailures.Add(
        "post-authorization start fence cleanup failed: $($_.Exception.Message)"
    )
}
Invoke-ParentStageContinuation -AuthorizationJson $parentAuthorizationJson `
    -PostgresStartedAt ([string]$authorizationPostgres.started_at)
}
catch {
    $transitionFailure = $_
    if ($parentAuthorizationPublished) {
        # Keep the already-published authorization as immutable history, but
        # explicitly revoke this transition and causally contain its exact
        # completed PostgreSQL start when the last parent-supervision check
        # fails. No authorization is emitted to the direct caller. Consumers
        # must reject a durable authorization when its start revoke exists.
        $parentContainmentProof = Invoke-ParentCausalPostgresContainment `
            -HeldFence $startFenceLock `
            -FencePath $watchdogStartFencePath `
            -FenceSha256 $watchdogStartFenceSha256 `
            -RevokePath $watchdogStartRevokePath `
            -StartAttemptPath $watchdogStartAttemptPath `
            -StartCompletionPath $watchdogStartCompletionPath `
            -ExpectedStartAttemptSha256 $watchdogStartAttemptSha256 `
            -ExpectedStartCompletionSha256 $watchdogStartCompletionSha256 `
            -StartCommandProof $startCommandProof `
            -SessionId $transitionSessionId `
            -Program $watchdogProgram `
            -CollectorType $watchdogCollectorType `
            -ExpectedEngine $initialDockerEngine `
            -ExpectedPostgres $payload.postgres_prestart `
            -ContainmentPath $parentContainmentPath `
            -Trigger 'parent_authorization_postpublication_supervisor_loss'
        $startFenceLock = $null
        throw [InvalidOperationException]::new(
            "Successor transition lost parent supervision after authorization publication; the parent supervisor durably revoked this transition and completed causal PostgreSQL containment. Original: $($transitionFailure.Exception.Message)"
        )
    }
    $heartbeatStopFailure = $null
    if ($null -ne $watchdogHeartbeatPublisher) {
        try { $watchdogHeartbeatPublisher.StopAndWait(5000) }
        catch { $heartbeatStopFailure = $_.Exception.Message }
    }
    if (
        $postgresStartCommandIssued -and
        $preStartAcceptedAt -ne [DateTimeOffset]::MinValue
    ) {
        # Once the exact start attempt is durable, the watchdog owns causal
        # resolution.  The parent must not issue an early stop against a
        # daemon request that may still be queued or pre-running.
        $emergencyContainmentTriggered = $true
    }
    $watchdogTerminalFailure = $null
    $failureReceiptStatus = $null
    if ($emergencyContainmentTriggered) {
        if ($null -eq $parentContainmentProof) {
            $supervisionDeadline = [DateTimeOffset]::UtcNow.AddSeconds(
                $watchdogHeartbeatTimeoutSeconds + 5
            )
            if ($null -ne $watchdogProcess) {
                while ($true) {
                    $watchdogProcess.process.Refresh()
                    $connectionLost = [bool](
                        $watchdogProcess.process.HasExited -or
                        $watchdogProcess.collector.StdoutEof -or
                        $watchdogProcess.collector.StderrEof -or
                        $watchdogProcess.collector.OutputExceeded -or
                        [int64]$watchdogProcess.collector.StderrBytes -ne 0 -or
                        -not [string]::IsNullOrEmpty(
                            [string]$watchdogProcess.collector.PumpError
                        )
                    )
                    if (
                        -not $connectionLost -and
                        [DateTimeOffset]::UtcNow -lt $supervisionDeadline
                    ) {
                        [void]$watchdogProcess.process.WaitForExit(100)
                        continue
                    }
                    if (-not $watchdogProcess.process.HasExited) {
                        Stop-TransitionWatchdogForParentTakeover `
                            -Watchdog $watchdogProcess
                    }
                    try {
                        $failureExitProof = Wait-TransitionWatchdogExit `
                            -Watchdog $watchdogProcess -HeartbeatPublisher $null `
                            -HardDeadline ([DateTimeOffset]::UtcNow) `
                            -PermitContainmentExit
                        if ($failureExitProof.exit_code -eq 0) {
                            if (
                                $null -eq $watchdogCommitProof -or
                                $null -eq $liveEnvelope -or
                                [string]::IsNullOrWhiteSpace($liveSha256) -or
                                [string]::IsNullOrWhiteSpace($liveJson)
                            ) {
                                throw 'A watchdog success exit lacks the parent-bound Live commit state.'
                            }
                            [void](Assert-TransitionWatchdogSuccessReceipt `
                                -ExitProof $failureExitProof `
                                -ReceiptPath $watchdogReceiptPath `
                                -SessionId $transitionSessionId `
                                -Program $watchdogProgram `
                                -LeaseProof $watchdogLeaseProof `
                                -CommitProof $watchdogCommitProof `
                                -CommitPath $watchdogCommitPath `
                                -LivePath $absoluteLivePath -LiveSha256 $liveSha256 `
                                -LivePayloadSha256 ([string]$liveEnvelope.payload_sha256) `
                                -PostgresStartedAt ([string]$postgres.started_at) `
                                -HardDeadline $watchdogHardDeadline)
                            $failureReceiptStatus = 'non_authorizing_success'
                        }
                        else {
                            $failureReceiptProof = `
                                Assert-TransitionWatchdogFailureReceipt `
                                    -ExitProof $failureExitProof `
                                    -ReceiptPath $watchdogReceiptPath `
                                    -SessionId $transitionSessionId `
                                    -Program $watchdogProgram `
                                    -LeaseProof $watchdogLeaseProof `
                                    -LeasePath $watchdogLeasePath `
                                    -CommitPath $watchdogCommitPath `
                                    -LivePath $absoluteLivePath `
                                    -StartFencePath $watchdogStartFencePath `
                                    -StartFenceSha256 $watchdogStartFenceSha256 `
                                    -StartRevokePath $watchdogStartRevokePath `
                                    -StartAttemptPath $watchdogStartAttemptPath `
                                    -StartCompletionPath $watchdogStartCompletionPath
                            $failureReceiptStatus = [string]$failureReceiptProof.status
                        }
                    }
                    catch {
                        $watchdogTerminalFailure = $_.Exception.Message
                    }
                    break
                }
            }
            if ($null -eq $failureReceiptStatus -or
                $failureReceiptStatus -ceq 'non_authorizing_success') {
                $parentContainmentProof = Invoke-ParentCausalPostgresContainment `
                    -HeldFence $startFenceLock `
                    -FencePath $watchdogStartFencePath `
                    -FenceSha256 $watchdogStartFenceSha256 `
                    -RevokePath $watchdogStartRevokePath `
                    -StartAttemptPath $watchdogStartAttemptPath `
                    -StartCompletionPath $watchdogStartCompletionPath `
                    -ExpectedStartAttemptSha256 $watchdogStartAttemptSha256 `
                    -ExpectedStartCompletionSha256 $watchdogStartCompletionSha256 `
                    -StartCommandProof $startCommandProof `
                    -SessionId $transitionSessionId `
                    -Program $watchdogProgram `
                    -CollectorType $watchdogCollectorType `
                    -ExpectedEngine $initialDockerEngine `
                    -ExpectedPostgres $payload.postgres_prestart `
                    -ContainmentPath $parentContainmentPath `
                    -Trigger 'parent_watchdog_lost'
                $startFenceLock = $null
            }
        }
        if ($failureReceiptStatus -ceq 'contained') {
            throw [InvalidOperationException]::new(
                "Successor transition failed after a PostgreSQL start attempt; the watchdog durably revoked further starts and completed causal containment. Original: $($transitionFailure.Exception.Message)"
            )
        }
        if ($failureReceiptStatus -ceq 'failure') {
            throw [InvalidOperationException]::new(
                "Successor transition failed after a PostgreSQL start attempt; the watchdog returned a fail-closed terminal failure and authorized no stage. Original: $($transitionFailure.Exception.Message) Watchdog: $watchdogTerminalFailure"
            )
        }
        if ($null -ne $parentContainmentProof) {
            if ($null -ne $watchdogProcess) {
                Stop-TransitionWatchdogForParentTakeover -Watchdog $watchdogProcess
            }
            throw [InvalidOperationException]::new(
                "Successor transition failed after a PostgreSQL start attempt; the parent supervisor durably revoked further starts and completed causal containment after watchdog loss. Original: $($transitionFailure.Exception.Message)"
            )
        }
        throw 'Post-start failure reached no valid watchdog or parent containment state.'
    }
    if ($null -ne $heartbeatStopFailure) {
        throw "Successor transition failed with an incomplete watchdog terminal handoff. Original: $($transitionFailure.Exception.Message) Heartbeat: $heartbeatStopFailure Watchdog: $watchdogTerminalFailure"
    }
    throw $transitionFailure
}
finally {
    # A live/uncertain parent lifetime is not a local cleanup warning. Refuse
    # release BEFORE closing any local witness/heartbeat/file resource. The
    # fixed same-process continuation and separate cooperative terminal protocol
    # must eventually resolve this; no success JSON has been emitted above.
    try { Close-ParentStageBoundary }
    catch {
        if ($null -ne $transitionFailure) {
            throw [AggregateException]::new('Parent lifetime remains unresolved; primary Transition failure is retained.',
                [Exception[]]@($transitionFailure.Exception, $_.Exception))
        }
        throw
    }
    if ($null -ne $authorizationWitness) {
        try { Stop-DockerEventWitness -Witness $authorizationWitness }
        catch {
            $transitionCleanupFailures.Add(
                "authorization witness cleanup failed: $($_.Exception.Message)"
            )
        }
    }
    if ($null -ne $eventWitness) {
        try { Stop-DockerEventWitness -Witness $eventWitness }
        catch {
            $transitionCleanupFailures.Add(
                "transition witness cleanup failed: $($_.Exception.Message)"
            )
        }
    }
    if ($null -ne $watchdogHeartbeatPublisher) {
        try { $watchdogHeartbeatPublisher.Dispose() }
        catch {
            $transitionCleanupFailures.Add(
                "heartbeat publisher cleanup failed: $($_.Exception.Message)"
            )
        }
    }
    if ($null -ne $watchdogProcess) {
        try {
            $watchdogProcess.process.Refresh()
            if ($watchdogProcess.process.HasExited) {
                try { $watchdogProcess.collector.Dispose() }
                catch {
                    $transitionCleanupFailures.Add(
                        "watchdog collector cleanup failed: $($_.Exception.Message)"
                    )
                }
                if (Test-Path -LiteralPath $watchdogProcess.temp_root) {
                    try {
                        Remove-Item -LiteralPath $watchdogProcess.temp_root `
                            -Recurse -Force -ErrorAction Stop
                    }
                    catch {
                        $transitionCleanupFailures.Add(
                            "watchdog capture cleanup failed: $($_.Exception.Message)"
                        )
                    }
                }
            }
        }
        catch {
            $transitionCleanupFailures.Add(
                "watchdog process cleanup failed: $($_.Exception.Message)"
            )
        }
    }
    foreach ($lockCleanup in @(
        [pscustomobject]@{ name = 'start fence'; value = $startFenceLock },
        [pscustomobject]@{ name = 'HBA read lock'; value = $script:hbaReadLock },
        [pscustomobject]@{ name = 'watchdog script lock'; value = $script:watchdogScriptReadLock },
        [pscustomobject]@{ name = 'watchdog host lock'; value = $script:watchdogHostReadLock },
        [pscustomobject]@{ name = 'gate script lock'; value = $script:gateScriptReadLock },
        [pscustomobject]@{ name = 'Docker executable lock'; value = $script:dockerExecutableLock }
    )) {
        if ($null -ne $lockCleanup.value) {
            try { $lockCleanup.value.Dispose() }
            catch {
                $transitionCleanupFailures.Add(
                    "$($lockCleanup.name) cleanup failed: $($_.Exception.Message)"
                )
            }
        }
    }
    $startFenceLock = $null
    $script:hbaReadLock = $null
    $script:hbaReadLockPath = $null
    $script:watchdogScriptReadLock = $null
    $script:watchdogHostReadLock = $null
    $script:gateScriptReadLock = $null
    $script:dockerExecutableLock = $null
    if ($transitionCleanupFailures.Count -gt 0) {
        $cleanupDetail = $transitionCleanupFailures -join ' | '
        if ($null -ne $transitionFailure) {
            Write-Error -ErrorAction Continue -Message (
                "Successor transition cleanup retained the primary failure. Original: $($transitionFailure.Exception.Message) Cleanup: $cleanupDetail"
            )
        }
        else {
            throw [InvalidOperationException]::new(
                "Successor transition cleanup failed: $cleanupDetail"
            )
        }
    }
}
