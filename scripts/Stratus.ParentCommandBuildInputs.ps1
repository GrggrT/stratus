# Pure v2 runtime build-input correspondence. The trusted caller loads the exact
# reviewed Stratus.ParentCommandRuntimeContract helpers before these functions.
# No dynamic loading, filesystem access, compiler or target native API is used.
# Profile approval origin, actual resolution/execution and qualification remain
# unproved. Matching caller data never grants build/import/production authority.

function Assert-StratusBuildInputDocument {
    param([object] $Document,[object] $Bytes,[string] $Label)
    Assert-StratusCommandRuntimeObject $Document @('path','sha256','value') $Label
    Assert-StratusCommandRuntimePath $Document.path "$Label path"
    Assert-StratusCommandRuntimeHash $Document.sha256 "$Label hash"
    $hash=Get-StratusCommandRuntimeHash $Bytes 262144 "$Label bytes"
    if ($hash -cne $Document.sha256) { throw "$Label raw digest differs." }
    $encoded=Get-StratusCommandRuntimeContractBytes $Document.value
    if ($encoded.LongLength -ne $Bytes.LongLength) { throw "$Label canonical wire length differs." }
    for ($i=0;$i -lt $encoded.Length;$i++) {
        if ($encoded[$i] -ne $Bytes[$i]) { throw "$Label is not exact canonical UTF8 plus LF." }
    }
}
function Assert-StratusBuildInputArray {
    param([object] $Value,[int] $Minimum,[int] $Maximum,[string] $Label)
    Assert-StratusCommandRuntimePlainValue $Value $Label
    if ($Value -isnot [Array] -or $Value.Rank -ne 1 -or $Value.Count -lt $Minimum -or $Value.Count -gt $Maximum) {
        throw "$Label must be a bounded one-dimensional native array."
    }
}
function Get-StratusBuildManifestRuntimeSource {
    param([object] $Bytes,[object] $ExpectedHash,[object] $BuildWorktree)
    Assert-StratusCommandRuntimeHash $ExpectedHash 'source manifest expected hash'
    Assert-StratusCommandRuntimePath $BuildWorktree 'source manifest build worktree'
    $hash=Get-StratusCommandRuntimeHash $Bytes 4194304 'source manifest bytes'
    if ($hash -cne $ExpectedHash) { throw 'Source manifest digest differs from the independent candidate anchor.' }
    $text=[Text.UTF8Encoding]::new($false,$true).GetString($Bytes)
    if ($text.Length -eq 0 -or [int]$text[0] -eq 65279 -or -not $text.EndsWith("`n",[StringComparison]::Ordinal) -or
        $text.IndexOfAny([char[]]@(0,13)) -ge 0) { throw 'Source manifest must be strict UTF8, no BOM/CR/NUL, with final LF.' }
    $lines=$text.Split("`n")
    if ($lines.Length -lt 2 -or $lines.Length -gt 16385) { throw 'Source manifest record count is outside the fixed bound.' }
    $previous=$null;$runtime=$null
    $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    for ($i=0;$i -lt ($lines.Length-1);$i++) {
        $fields=$lines[$i].Split("`t")
        if ($fields.Length -ne 3) { throw 'Source manifest record must have exactly three tab-separated fields.' }
        $relative=$fields[0]
        if (-not [regex]::IsMatch($relative,'\A[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\z') -or
            $relative.Contains('\') -or $relative.StartsWith('/') -or $relative.Contains(':') -or
            $relative.EndsWith('/') -or -not $seen.Add($relative) -or
            ($null -ne $previous -and [StringComparer]::Ordinal.Compare($previous,$relative) -ge 0)) {
            throw 'Source manifest path is not unique and strictly ordinal-sorted.'
        }
        Assert-StratusCommandRuntimePath ($BuildWorktree+'\'+$relative.Replace('/','\')) 'source manifest canonical path'
        if (-not [regex]::IsMatch($fields[1],'\A[1-9][0-9]{0,18}\z')) { throw 'Source manifest length is not canonical positive decimal.' }
        [long]$count=0
        if (-not [long]::TryParse($fields[1],[Globalization.NumberStyles]::None,[Globalization.CultureInfo]::InvariantCulture,[ref]$count)) {
            throw 'Source manifest length exceeds native Int64.'
        }
        Assert-StratusCommandRuntimeHash $fields[2] 'source manifest entry hash'
        if ($relative -ceq 'scripts/Stratus.ParentStage.Runtime.cs') {
            if ($count -lt 1 -or $count -gt 2097152) { throw 'Manifest runtime source length is outside the fixed bound.' }
            $runtime=[pscustomobject][ordered]@{path=$BuildWorktree+'\scripts\Stratus.ParentStage.Runtime.cs';sha256=$fields[2];bytes=$count}
        }
        $previous=$relative
    }
    if ($null -eq $runtime) { throw 'Source manifest omits the exact runtime source entry.' }
    return $runtime
}
function Get-StratusParentCommandBuildPreflight {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][AllowNull()][object] $ProfileDocument,
        [Parameter(Mandatory=$true)][AllowNull()][object] $ProfileBytes,
        [Parameter(Mandatory=$true)][AllowNull()][object] $PlanDocument,
        [Parameter(Mandatory=$true)][AllowNull()][object] $PlanBytes,
        [Parameter(Mandatory=$true)][AllowNull()][object] $SourceManifestBytes,
        [Parameter(Mandatory=$true)][AllowNull()][object] $Expected)
    Assert-StratusCommandRuntimeObject $Expected @('root','revision','source_manifest_sha256','profile_sha256','build_id',
        'build_worktree','build_directory','source') 'independent build inputs'
    foreach ($name in @('root','build_worktree','build_directory')) { Assert-StratusCommandRuntimePath $Expected.$name "expected $name" }
    foreach ($name in @('source_manifest_sha256','profile_sha256')) { Assert-StratusCommandRuntimeHash $Expected.$name "expected $name" }
    foreach ($name in @('revision','build_id')) { Assert-StratusCommandRuntimeString $Expected.$name "expected $name" }
    if (-not [regex]::IsMatch($Expected.revision,'\A[0-9a-f]{40}\z') -or -not [regex]::IsMatch($Expected.build_id,'\A[0-9a-f]{32}\z')) {
        throw 'Expected revision or build ID is not canonical.'
    }
    if ([string]::Equals($Expected.root,$Expected.build_worktree,[StringComparison]::OrdinalIgnoreCase) -or
        $Expected.root.StartsWith($Expected.build_worktree+'\',[StringComparison]::OrdinalIgnoreCase) -or
        $Expected.build_worktree.StartsWith($Expected.root+'\',[StringComparison]::OrdinalIgnoreCase)) {
        throw 'Build worktree must be a distinct non-nested checkout.'
    }
    $fixedDirectory=$Expected.root+'\.stratus-cache\runtime-builds\'+$Expected.build_id+'.in-progress'
    if ($Expected.build_directory -cne $fixedDirectory) { throw 'Actual build directory differs from the fixed private scope.' }
    Assert-StratusCommandRuntimeFile $Expected.source 2097152 'expected source'
    $manifestSource=Get-StratusBuildManifestRuntimeSource $SourceManifestBytes $Expected.source_manifest_sha256 $Expected.build_worktree
    Assert-StratusCommandRuntimeEqual $Expected.source $manifestSource 'expected source versus actual manifest'

    Assert-StratusCommandRuntimeObject $ProfileDocument @('path','sha256','value') 'profile document'
    Assert-StratusCommandRuntimeObject $ProfileDocument.value @('format','profile_id','host','compiler','references','target_framework',
        'architecture','assembly_name','producer','metadata_verifier','dependencies','compiler_recipe') 'build profile'
    $profile=$ProfileDocument.value
    foreach ($name in @('format','profile_id','target_framework','architecture','assembly_name','compiler_recipe')) {
        Assert-StratusCommandRuntimeString $profile.$name "profile $name"
    }
    if ($profile.format -cne 'stratus-parent-command-build-profile-v2' -or
        -not [regex]::IsMatch($profile.profile_id,'\A[a-z0-9][a-z0-9.-]{0,63}\z') -or
        -not [regex]::IsMatch($profile.target_framework,'\A\.NET(?:Framework|CoreApp|Standard),Version=v[0-9]+\.[0-9]+(?:\.[0-9]+)?\z') -or
        $profile.architecture -cne 'x64' -or $profile.assembly_name -cne 'Stratus.ParentStage.Runtime' -or
        $profile.compiler_recipe -cne 'direct-csc-exe-v1') { throw 'Build profile format, identity or exact recipe differs.' }
    Assert-StratusCommandRuntimeObject $profile.host @('path','sha256','bytes','ps_edition','ps_version','clr_version') 'profile host'
    Assert-StratusCommandRuntimeObject $profile.compiler @('path','sha256','bytes','version') 'profile compiler'
    foreach ($record in @($profile.host,$profile.compiler)) {
        Assert-StratusCommandRuntimePath $record.path 'profile executable path'
        Assert-StratusCommandRuntimeHash $record.sha256 'profile executable hash'
        Assert-StratusCommandRuntimeInteger $record.bytes 1 134217728 'profile executable length'
        if (-not $record.path.EndsWith('.exe',[StringComparison]::OrdinalIgnoreCase)) { throw 'The exact recipe requires executable host and compiler paths.' }
    }
    foreach ($name in @('ps_edition','ps_version','clr_version')) { Assert-StratusCommandRuntimeString $profile.host.$name "host $name" }
    Assert-StratusCommandRuntimeString $profile.compiler.version 'compiler version'
    if (($profile.host.ps_edition -ceq 'Desktop' -and -not [regex]::IsMatch($profile.host.ps_version,'\A5\.1\.[0-9]+\.[0-9]+\z')) -or
        ($profile.host.ps_edition -ceq 'Core' -and -not [regex]::IsMatch($profile.host.ps_version,'\A7\.[0-9]+\.[0-9]+(?:\.[0-9]+)?\z')) -or
        $profile.host.ps_edition -cnotin @('Desktop','Core') -or -not [regex]::IsMatch($profile.host.clr_version,'\A[0-9]+(?:\.[0-9]+){1,3}\z')) {
        throw 'Host declared edition/version/CLR profile is malformed.'
    }
    Assert-StratusCommandRuntimeFile $profile.producer 16777216 'profile producer'
    Assert-StratusCommandRuntimeFile $profile.metadata_verifier 16777216 'profile metadata verifier'
    Assert-StratusBuildInputArray $profile.references 1 32 'profile references'
    Assert-StratusBuildInputArray $profile.dependencies 1 64 'profile dependencies'
    $records=[Collections.Generic.List[object]]::new()
    foreach ($name in @('host','compiler','producer','metadata_verifier')) {
        $record=$profile.$name
        $maximum=if ($name -cin @('producer','metadata_verifier')) {16777216} else {134217728}
        $records.Add([pscustomobject][ordered]@{path=$record.path;sha256=$record.sha256;bytes=$record.bytes;maximum_bytes=$maximum})
    }
    foreach ($record in $profile.references) {
        Assert-StratusCommandRuntimeFile $record 134217728 'profile reference'
        if (-not $record.path.EndsWith('.dll',[StringComparison]::OrdinalIgnoreCase)) { throw 'A compiler reference must be a declared DLL.' }
        $records.Add([pscustomobject][ordered]@{path=$record.path;sha256=$record.sha256;bytes=$record.bytes;maximum_bytes=134217728})
    }
    foreach ($record in $profile.dependencies) {
        Assert-StratusCommandRuntimeObject $record @('role','path','sha256','bytes') 'profile dependency'
        Assert-StratusCommandRuntimeString $record.role 'dependency role'
        if ($record.role -cnotin @('host_runtime','host_dependency','host_config','compiler_runtime','compiler_dependency','compiler_config',
            'producer_runtime','producer_dependency','producer_config','verifier_runtime','verifier_dependency','verifier_config')) {
            throw 'Dependency role is outside the closed declared inventory.'
        }
        Assert-StratusCommandRuntimePath $record.path 'dependency path'
        Assert-StratusCommandRuntimeHash $record.sha256 'dependency hash'
        Assert-StratusCommandRuntimeInteger $record.bytes 1 134217728 'dependency length'
        $records.Add([pscustomobject][ordered]@{path=$record.path;sha256=$record.sha256;bytes=$record.bytes;maximum_bytes=134217728})
    }
    Assert-StratusBuildInputDocument $ProfileDocument $ProfileBytes 'profile document'
    if ($ProfileDocument.sha256 -cne $Expected.profile_sha256) { throw 'Profile differs from the independently supplied approval digest.' }

    Assert-StratusCommandRuntimeObject $PlanDocument @('path','sha256','value') 'plan document'
    Assert-StratusCommandRuntimeObject $PlanDocument.value @('format','build_id','revision','source_manifest_sha256','profile_sha256',
        'build_worktree','build_directory','source','output','compiler_arguments') 'build plan'
    $plan=$PlanDocument.value
    Assert-StratusCommandRuntimeString $plan.format 'plan format'
    if ($plan.format -cne 'stratus-parent-command-build-plan-v2') { throw 'Build plan format differs.' }
    foreach ($name in @('build_id','revision','source_manifest_sha256','profile_sha256','build_worktree','build_directory','source')) {
        Assert-StratusCommandRuntimeEqual $plan.$name $Expected.$name "plan $name"
    }
    Assert-StratusCommandRuntimeObject $plan.output @('path') 'planned output'
    Assert-StratusCommandRuntimePath $plan.output.path 'planned output path'
    if ($plan.output.path -cne ($fixedDirectory+'\Stratus.ParentStage.Runtime.dll') -or
        $PlanDocument.path -cne ($fixedDirectory+'\command-runtime-plan.json')) { throw 'Plan output/document actual build locations differ.' }
    Assert-StratusBuildInputArray $plan.compiler_arguments 7 39 'compiler argv'
    $argv=[Collections.Generic.List[string]]::new()
    foreach ($argument in @('/target:library','/noconfig','/nostdlib+','/platform:x64',('/out:'+$plan.output.path))) { $argv.Add($argument) }
    foreach ($reference in $profile.references) { $argv.Add('/reference:'+$reference.path) }
    $argv.Add($manifestSource.path)
    Assert-StratusCommandRuntimeEqual $plan.compiler_arguments $argv.ToArray() 'exact direct compiler argv'
    Assert-StratusBuildInputDocument $PlanDocument $PlanBytes 'plan document'
    $paths=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($path in @($ProfileDocument.path,$PlanDocument.path,$manifestSource.path,$plan.output.path)) {
        if (-not $paths.Add($path)) { throw 'Input and document/output paths alias.' }
    }
    [long]$total=0
    # The complete metadata inventory is bounded and unique before any payload
    # source/input hashing or ownership preparation. These are lexical checks,
    # not physical link/lease or independent approval provenance.
    foreach ($record in $records) {
        if (-not $paths.Add($record.path)) { throw 'Input and document/output paths alias.' }
        $total+=$record.bytes
        if ($total -gt 268435456) { throw 'Declared input snapshot aggregate exceeds 256 MiB.' }
    }
    # Fresh scalar/file projections only. No mutable caller record or raw byte
    # array is returned. This result is never accepted as a replacement for the
    # original raw context by either the full validator or the owner factory.
    return [pscustomobject][ordered]@{profile_id=$profile.profile_id;profile_path=$ProfileDocument.path
        profile_sha256=$ProfileDocument.sha256;profile_bytes=$ProfileBytes.LongLength
        plan_path=$PlanDocument.path;plan_sha256=$PlanDocument.sha256;plan_bytes=$PlanBytes.LongLength
        revision=$plan.revision;build_id=$plan.build_id;source_manifest_sha256=$plan.source_manifest_sha256
        source_manifest_bytes=$SourceManifestBytes.LongLength
        source=[pscustomobject][ordered]@{path=$manifestSource.path;sha256=$manifestSource.sha256;bytes=$manifestSource.bytes}
        build_worktree=$plan.build_worktree;build_directory=$plan.build_directory;output_path=$plan.output.path
        declared_inputs=$records.ToArray();declared_input_count=$records.Count;declared_input_bytes=$total}
}

function Get-StratusParentCommandBuildInputSpecifications {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][AllowNull()][object] $ProfileDocument,
        [Parameter(Mandatory=$true)][AllowNull()][object] $ProfileBytes,
        [Parameter(Mandatory=$true)][AllowNull()][object] $PlanDocument,
        [Parameter(Mandatory=$true)][AllowNull()][object] $PlanBytes,
        [Parameter(Mandatory=$true)][AllowNull()][object] $SourceManifestBytes,
        [Parameter(Mandatory=$true)][AllowNull()][object] $Expected,
        [Parameter(Mandatory=$true)][AllowNull()][object] $SourceManifestPath)
    Assert-StratusCommandRuntimePath $SourceManifestPath 'owned source-manifest path'
    $preflight=Get-StratusParentCommandBuildPreflight -ProfileDocument $ProfileDocument -ProfileBytes $ProfileBytes `
        -PlanDocument $PlanDocument -PlanBytes $PlanBytes -SourceManifestBytes $SourceManifestBytes -Expected $Expected
    $specifications=[Collections.Generic.List[object]]::new()
    $specifications.Add([pscustomobject][ordered]@{path=$preflight.profile_path;sha256=$preflight.profile_sha256
        bytes=$preflight.profile_bytes;maximum_bytes=262144})
    $specifications.Add([pscustomobject][ordered]@{path=$preflight.plan_path;sha256=$preflight.plan_sha256
        bytes=$preflight.plan_bytes;maximum_bytes=262144})
    $specifications.Add([pscustomobject][ordered]@{path=$SourceManifestPath;sha256=$preflight.source_manifest_sha256
        bytes=$preflight.source_manifest_bytes;maximum_bytes=4194304})
    $specifications.Add([pscustomobject][ordered]@{path=$preflight.source.path;sha256=$preflight.source.sha256
        bytes=$preflight.source.bytes;maximum_bytes=2097152})
    foreach ($record in $preflight.declared_inputs) {
        $specifications.Add([pscustomobject][ordered]@{path=$record.path;sha256=$record.sha256
            bytes=$record.bytes;maximum_bytes=$record.maximum_bytes})
    }
    # Include the previously path-less manifest and the excluded output in this
    # exact closed set. No caller-supplied specifications or preflight projection
    # can enter this function. The owned CLR factory still has to enforce actual
    # file identities and capture every declared byte after Open.
    if ($specifications.Count -lt 10 -or $specifications.Count -gt 104) { throw 'Owned input specification count differs.' }
    $paths=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $null=$paths.Add($preflight.output_path)
    [long]$total=0
    foreach ($record in $specifications) {
        if (-not $paths.Add($record.path)) { throw 'Owned manifest/document/source/input or output paths alias.' }
        Assert-StratusCommandRuntimeInteger $record.bytes 1 $record.maximum_bytes 'owned role length'
        $total+=$record.bytes
        if ($total -gt 275251200) { throw 'Owned input specification aggregate exceeds the fixed bound.' }
    }
    # One array result, not pipeline-unrolled records. Its values confer no
    # approval, watchdog state, P Open permission or native qualification.
    return ,$specifications.ToArray()
}

function Assert-StratusParentCommandBuildInputs {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][AllowNull()][object] $ProfileDocument,
        [Parameter(Mandatory=$true)][AllowNull()][object] $ProfileBytes,
        [Parameter(Mandatory=$true)][AllowNull()][object] $PlanDocument,
        [Parameter(Mandatory=$true)][AllowNull()][object] $PlanBytes,
        [Parameter(Mandatory=$true)][AllowNull()][object] $SourceManifestBytes,
        [Parameter(Mandatory=$true)][AllowNull()][object] $SourceBytes,
        [Parameter(Mandatory=$true)][AllowNull()][object] $InputSnapshots,
        [Parameter(Mandatory=$true)][AllowNull()][object] $Expected)
    $preflight=Get-StratusParentCommandBuildPreflight -ProfileDocument $ProfileDocument -ProfileBytes $ProfileBytes `
        -PlanDocument $PlanDocument -PlanBytes $PlanBytes -SourceManifestBytes $SourceManifestBytes -Expected $Expected
    $manifestSource=$preflight.source
    $records=$preflight.declared_inputs
    $sourceHash=Get-StratusCommandRuntimeHash $SourceBytes 2097152 'actual build source'
    if ($sourceHash -cne $manifestSource.sha256 -or $SourceBytes.LongLength -ne $manifestSource.bytes) {
        throw 'Actual source bytes differ from the independently bound manifest entry.'
    }
    Assert-StratusBuildInputArray $InputSnapshots 6 100 'declared input snapshots'
    if ($InputSnapshots.Count -ne $records.Count) { throw 'Input snapshot inventory has missing or extra entries.' }
    # The shared preflight above checks every metadata bound/path/aggregate;
    # actual snapshots still require the full type/order/length/hash closure.
    # No preflight-only projection can substitute for these original byte arrays.
    for ($i=0;$i -lt $records.Count;$i++) {
        $record=$records[$i];$snapshot=$InputSnapshots[$i]
        Assert-StratusCommandRuntimeObject $snapshot @('path','bytes') 'input snapshot'
        Assert-StratusCommandRuntimePath $snapshot.path 'snapshot path'
        Assert-StratusCommandRuntimePlainValue $snapshot.bytes 'snapshot bytes'
        if ($snapshot.path -cne $record.path -or
            $snapshot.bytes -isnot [byte[]] -or $snapshot.bytes.LongLength -ne $record.bytes) {
            throw 'Input snapshot order/path/type/length or unique identity differs.'
        }
    }
    for ($i=0;$i -lt $records.Count;$i++) {
        if ((Get-StratusCommandRuntimeHash $InputSnapshots[$i].bytes 134217728 'actual declared input') -cne $records[$i].sha256) {
            throw 'Actual declared input bytes differ from the profile.'
        }
    }
    return [pscustomobject][ordered]@{format='stratus-parent-command-build-input-binding-v2';profile_id=$preflight.profile_id
        profile_sha256=$preflight.profile_sha256;plan_sha256=$preflight.plan_sha256;revision=$preflight.revision;build_id=$preflight.build_id
        source_manifest_sha256=$preflight.source_manifest_sha256;source_sha256=$sourceHash;source_bytes=$SourceBytes.LongLength
        build_worktree=$preflight.build_worktree;build_directory=$preflight.build_directory;output_path=$preflight.output_path
        declared_input_count=$records.Count;declared_input_bytes=$preflight.declared_input_bytes
        canonical_document_bytes_verified=$true;source_manifest_entry_correspondence_verified=$true;declared_input_snapshot_correspondence_verified=$true
        profile_approval_origin_verified=$false;candidate_checkout_cleanliness_verified=$false;actual_dependency_resolution_verified=$false
        fresh_build_directory_verified=$false;held_handle_provenance_verified=$false;compiler_execution_verified=$false;build_causality_verified=$false
        assembly_metadata_verified=$false;clr_compatibility_verified=$false;same_held_bytes_import_verified=$false;import_permitted=$false
        native_qualified=$false;production_start_permitted=$false;live_permitted=$false;binance_kill_switch_removal_permitted=$false;full_goal_accepted=$false}
}
