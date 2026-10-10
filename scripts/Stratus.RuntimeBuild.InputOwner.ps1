# Owned input adapter for an independently qualified bootstrap, not an entrypoint.
# Load this trusted source with the existing pure RuntimeContract/BuildInputs
# helpers. The caller supplies the exact Assembly returned by the pinned loader;
# this file does not authenticate an Assembly, approve a profile or load any code.
# Prepare returns a managed owner BEFORE Open. The caller keeps that original
# object on every failure and through compiler closure; a projection is not a lease.
# Candidate specifications are derived here from the checked raw metadata, never
# supplied as a caller-chosen inventory. This is not independently approved
# P-side context delivery: the actual producer bridge remains blocked/unqualified.

function Get-StratusBuildInputTypes {
    param([Parameter(Mandatory=$true)][Reflection.Assembly] $BootstrapAssembly)
    if ($BootstrapAssembly.FullName -cne 'Stratus.RuntimeBuild.Bootstrap, Version=1.0.0.0, Culture=neutral, PublicKeyToken=null') {
        throw 'Build input APIs require the selected independent bootstrap identity.'
    }
    $specType=$BootstrapAssembly.GetType('Stratus.RuntimeBuild.BuildInputSpec',$true,$false)
    $ownerType=$BootstrapAssembly.GetType('Stratus.RuntimeBuild.BuildInputLeaseSet',$true,$false)
    $snapshotType=$BootstrapAssembly.GetType('Stratus.RuntimeBuild.BuildInputSnapshot',$true,$false)
    foreach ($type in @($specType,$ownerType,$snapshotType)) {
        if (-not [object]::ReferenceEquals($type.Assembly,$BootstrapAssembly) -or -not $type.IsPublic -or
            -not $type.IsClass -or -not $type.IsSealed -or $type.IsAbstract) {
            throw 'Input APIs must come from the exact returned bootstrap assembly.'
        }
    }
    $abi=$ownerType.GetField('Abi',[Reflection.BindingFlags]'Public,Static,DeclaredOnly')
    if ($null -eq $abi -or -not $abi.IsLiteral -or $abi.FieldType -ne [string] -or
        $abi.GetRawConstantValue() -cne 'stratus-runtime-build-inputs-v1') { throw 'Input owner ABI differs.' }
    return [pscustomobject]@{spec_type=$specType;owner_type=$ownerType;snapshot_type=$snapshotType}
}

function New-StratusBuildInputOwner {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][Reflection.Assembly] $BootstrapAssembly,
        [Parameter(Mandatory=$true)][AllowNull()][object] $ProfileDocument,
        [Parameter(Mandatory=$true)][AllowNull()][object] $ProfileBytes,
        [Parameter(Mandatory=$true)][AllowNull()][object] $PlanDocument,
        [Parameter(Mandatory=$true)][AllowNull()][object] $PlanBytes,
        [Parameter(Mandatory=$true)][AllowNull()][object] $SourceManifestPath,
        [Parameter(Mandatory=$true)][AllowNull()][object] $SourceManifestBytes,
        [Parameter(Mandatory=$true)][AllowNull()][object] $Expected)
    # This pure call revalidates the original canonical metadata and derives all
    # roles, lengths, hashes and bounds before selecting any CLR constructor.
    # No preflight projection or caller SpecRecords parameter is accepted.
    $SpecRecords=Get-StratusParentCommandBuildInputSpecifications -ProfileDocument $ProfileDocument -ProfileBytes $ProfileBytes `
        -PlanDocument $PlanDocument -PlanBytes $PlanBytes -SourceManifestPath $SourceManifestPath `
        -SourceManifestBytes $SourceManifestBytes -Expected $Expected
    Assert-StratusBuildInputArray $SpecRecords 10 104 'owned build input specifications'
    $types=Get-StratusBuildInputTypes $BootstrapAssembly
    $constructor=$types.spec_type.GetConstructor([Type[]]@([string],[string],[long],[long]))
    if ($null -eq $constructor) { throw 'Input specification constructor differs.' }
    $specs=[Array]::CreateInstance($types.spec_type,$SpecRecords.Count)
    for ($i=0;$i -lt $SpecRecords.Count;$i++) {
        $record=$SpecRecords[$i]
        Assert-StratusCommandRuntimeObject $record @('path','sha256','bytes','maximum_bytes') 'owned input record'
        Assert-StratusCommandRuntimePath $record.path 'owned input path'
        Assert-StratusCommandRuntimeHash $record.sha256 'owned input hash'
        $maximum=if($i -lt 2){262144}elseif($i -eq 2){4194304}elseif($i -eq 3){2097152}elseif($i -eq 6 -or $i -eq 7){16777216}else{134217728}
        Assert-StratusCommandRuntimeInteger $record.maximum_bytes $maximum $maximum 'fixed role byte bound'
        Assert-StratusCommandRuntimeInteger $record.bytes 1 $maximum 'owned input length'
        $spec=$constructor.Invoke([object[]]@($record.path,$record.sha256,[long]$record.bytes,[long]$maximum))
        $specs.SetValue($spec,$i)
    }
    $prepare=$types.owner_type.GetMethod('Prepare',[Reflection.BindingFlags]'Public,Static,DeclaredOnly',
        $null,[Type[]]@($types.spec_type.MakeArrayType()),$null)
    if ($null -eq $prepare -or $prepare.ReturnType -ne $types.owner_type) { throw 'Managed owner factory differs.' }
    # The qualified implementation does no file/native work here. Never combine
    # this return with Open in a factory which could lose a partially opened owner.
    $owner=$prepare.Invoke($null,[object[]]@(,$specs))
    if ($null -eq $owner -or -not $types.owner_type.IsInstanceOfType($owner)) { throw 'Owner factory returned another type.' }
    return $owner
}

function Open-StratusBuildInputOwner {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][Reflection.Assembly] $BootstrapAssembly,
        [Parameter(Mandatory=$true)][object] $Owner)
    $types=Get-StratusBuildInputTypes $BootstrapAssembly
    if (-not $types.owner_type.IsInstanceOfType($Owner)) { throw 'Retained original input owner required.' }
    $open=$types.owner_type.GetMethod('Open',[Reflection.BindingFlags]'Public,Instance,DeclaredOnly',
        $null,[Type[]]@(),$null)
    if ($null -eq $open -or $open.ReturnType -ne [void]) { throw 'Input owner Open signature differs.' }
    # Invoke the CLR method selected from the exact assembly, not an ETS method.
    # Do not dispose on failure: the caller already retained this exact object.
    $null=$open.Invoke($Owner,[object[]]@())
}

function Get-StratusOwnedBuildInputBinding {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][Reflection.Assembly] $BootstrapAssembly,
        [Parameter(Mandatory=$true)][object] $Owner,
        [Parameter(Mandatory=$true)][AllowNull()][object] $ProfileDocument,
        [Parameter(Mandatory=$true)][AllowNull()][object] $PlanDocument,
        [Parameter(Mandatory=$true)][AllowNull()][object] $Expected,
        [Parameter(Mandatory=$true)][AllowNull()][object] $SourceManifestPath)
    $types=Get-StratusBuildInputTypes $BootstrapAssembly
    if (-not $types.owner_type.IsInstanceOfType($Owner)) { throw 'Retained original input owner required.' }
    Assert-StratusCommandRuntimePath $SourceManifestPath 'held source-manifest path'
    Assert-StratusCommandRuntimeObject $ProfileDocument @('path','sha256','value') 'profile document'
    Assert-StratusCommandRuntimeObject $PlanDocument @('path','sha256','value') 'plan document'
    Assert-StratusCommandRuntimeObject $Expected @('root','revision','source_manifest_sha256','profile_sha256','build_id',
        'build_worktree','build_directory','source') 'independent build inputs'
    Assert-StratusCommandRuntimeFile $Expected.source 2097152 'independent source'
    foreach ($path in @($ProfileDocument.path,$PlanDocument.path)) { Assert-StratusCommandRuntimePath $path 'held document path' }
    $capture=$types.owner_type.GetMethod('CaptureSnapshots',[Reflection.BindingFlags]'Public,Instance,DeclaredOnly',
        $null,[Type[]]@(),$null)
    $held=$types.owner_type.GetMethod('AssertHeld',[Reflection.BindingFlags]'Public,Instance,DeclaredOnly',
        $null,[Type[]]@(),$null)
    if ($null -eq $capture -or $capture.ReturnType -ne $types.snapshot_type.MakeArrayType() -or
        $null -eq $held -or $held.ReturnType -ne [void]) { throw 'Owned capture API differs.' }
    $snapshots=$capture.Invoke($Owner,[object[]]@())
    if ($null -eq $snapshots -or -not $types.snapshot_type.MakeArrayType().IsInstanceOfType($snapshots) -or
        $snapshots.Length -lt 10 -or $snapshots.Length -gt 104) { throw 'Owned snapshot inventory differs.' }
    $pathField=$types.snapshot_type.GetField('Path',[Reflection.BindingFlags]'Public,Instance,DeclaredOnly')
    $bytesProperty=$types.snapshot_type.GetProperty('Bytes',[Reflection.BindingFlags]'Public,Instance,DeclaredOnly')
    if ($null -eq $pathField -or -not $pathField.IsInitOnly -or $pathField.FieldType -ne [string] -or
        $null -eq $bytesProperty -or $bytesProperty.PropertyType -ne [byte[]] -or $bytesProperty.CanWrite) {
        throw 'Owned snapshot immutable API differs.'
    }
    $paths=[Collections.Generic.List[string]]::new()
    $arrays=[Collections.Generic.List[byte[]]]::new()
    foreach ($snapshot in $snapshots) {
        if ($null -eq $snapshot -or -not $types.snapshot_type.IsInstanceOfType($snapshot)) { throw 'Snapshot type differs.' }
        # CLR Field/PropertyInfo access avoids caller-attached ETS getters. Bytes
        # are defensive copies of the owner's same-handle captured private array.
        $paths.Add([string]$pathField.GetValue($snapshot))
        $arrays.Add([byte[]]$bytesProperty.GetValue($snapshot,$null))
    }
    $documentPaths=@($ProfileDocument.path,$PlanDocument.path,$SourceManifestPath,$Expected.source.path)
    for ($i=0;$i -lt 4;$i++) {
        if ($paths[$i] -cne $documentPaths[$i]) { throw 'Held document/source order differs.' }
    }
    $inventory=[Collections.Generic.List[object]]::new()
    for ($i=4;$i -lt $paths.Count;$i++) { $inventory.Add([pscustomobject]@{path=$paths[$i];bytes=$arrays[$i]}) }
    # The existing v2 validator receives actual owned bytes, never a caller's
    # successful-looking byte-binding projection or substituted snapshot array.
    $binding=Assert-StratusParentCommandBuildInputs -ProfileDocument $ProfileDocument -ProfileBytes $arrays[0] `
        -PlanDocument $PlanDocument -PlanBytes $arrays[1] -SourceManifestBytes $arrays[2] -SourceBytes $arrays[3] `
        -InputSnapshots $inventory.ToArray() -Expected $Expected
    $null=$held.Invoke($Owner,[object[]]@())
    # Original Owner stays with caller. PrepareOwned takes that CLR owner, not this
    # returned data, and registers its borrow through physical compiler cleanup.
    return [pscustomobject]@{input_binding=$binding;profile_bytes=$arrays[0];plan_bytes=$arrays[1]
        source_manifest_bytes=$arrays[2];source_bytes=$arrays[3];input_snapshots=$inventory.ToArray()
        profile_approval_proven=$false;watchdog_armed=$false;output_writer_scope_proven=$false
        compiler_executed=$false;bootstrap_qualified=$false;full_goal_accepted=$false}
}
