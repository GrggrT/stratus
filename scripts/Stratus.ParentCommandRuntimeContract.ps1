# Pure build/import-input binding, never assembly loading or build acceptance.
# A trusted caller supplies independently approved R1/profile facts and snapshots
# read from its retained handles. This library neither creates those facts nor
# proves handle provenance, compilation, PE compatibility, import or Job ownership.
# Build-v1 wire is strict compact JSON in the record's property order plus one LF.
# Exact reserialization proves the supplied value corresponds to ContractBytes;
# no JSON parser, assembly resolver, compiler, filesystem or native API is used.
# The real loader must retain/recheck and load its SAME private byte snapshot,
# never reopen a path or resolve by assembly name/cache after this projection.

function Assert-StratusCommandRuntimePlainValue {
    param([object] $Value,[string] $Label)
    # Enumerate metadata, never member values. CLR type operators alone do not
    # reject ETS wrappers: a wrapped string/array can override Length or methods.
    $record=$Value -is [System.Management.Automation.PSCustomObject]
    if ($null -eq $Value -or (-not $record -and $Value -isnot [string] -and $Value -isnot [int] -and
        $Value -isnot [long] -and $Value -isnot [bool] -and $Value -isnot [Array])) { throw "$Label is not a supported plain-data value." }
    foreach ($member in $Value.PSObject.Members) {
        if ($record) {
            if ($member.MemberType -ne [Management.Automation.PSMemberTypes]::NoteProperty -and
                $member.MemberType -ne [Management.Automation.PSMemberTypes]::Method) { throw "$Label contains a non-data extended member." }
        } else {
            # Windows PowerShell 5 supplies Array.Count as this exact non-instance
            # alias. Its target is checked metadata, then the complete array is
            # scanned (so an extended Length cannot pass). Never permit an
            # instance alias, a converted alias, or a different target/name.
            $systemArrayCount=$Value -is [Array] -and $member -is [Management.Automation.PSAliasProperty] -and
                $member.MemberType -eq [Management.Automation.PSMemberTypes]::AliasProperty -and $member.Name -ceq 'Count' -and
                -not $member.IsInstance -and $member.ReferencedMemberName -ceq 'Length' -and $null -eq $member.ConversionType
            if (-not $systemArrayCount -and $member.MemberType -ne [Management.Automation.PSMemberTypes]::Property -and
                $member.MemberType -ne [Management.Automation.PSMemberTypes]::ParameterizedProperty -and
                $member.MemberType -ne [Management.Automation.PSMemberTypes]::Method) { throw "$Label contains a non-data extended member." }
        }
    }
}
function Assert-StratusCommandRuntimeObject {
    param([object] $Value,[string[]] $Fields,[string] $Label)
    # Type operators do not dispatch an attacker-supplied GetType ScriptMethod.
    if ($null -eq $Value -or $Value -isnot [System.Management.Automation.PSCustomObject]) { throw "$Label must be a native JSON object." }
    Assert-StratusCommandRuntimePlainValue $Value $Label
    $properties=@($Value.PSObject.Properties)
    if ($properties.Count -ne $Fields.Count) { throw "$Label property set differs." }
    foreach ($property in $properties) {
        if ($property.MemberType -ne [Management.Automation.PSMemberTypes]::NoteProperty -or $property.Name -cnotin $Fields) { throw "$Label property set differs." }
    }
}
function Assert-StratusCommandRuntimeString {
    param([object] $Value,[string] $Label)
    Assert-StratusCommandRuntimePlainValue $Value $Label
    if ($Value -isnot [string] -or $Value.Length -eq 0 -or $Value.Length -gt 32766 -or $Value.IndexOfAny([char[]]@(0,10,13)) -ge 0) { throw "$Label must be a bounded native string without line controls." }
}
function Assert-StratusCommandRuntimeHash {
    param([object] $Value,[string] $Label)
    Assert-StratusCommandRuntimeString $Value $Label
    if (-not [regex]::IsMatch($Value,'\A[0-9a-f]{64}\z')) { throw "$Label must be an exact lowercase SHA256." }
}
function Assert-StratusCommandRuntimeInteger {
    param([object] $Value,[long] $Minimum,[long] $Maximum,[string] $Label)
    Assert-StratusCommandRuntimePlainValue $Value $Label
    if (($Value -isnot [int] -and $Value -isnot [long]) -or $Value -lt $Minimum -or $Value -gt $Maximum) { throw "$Label must be a bounded native integer." }
}
function Assert-StratusCommandRuntimePath {
    param([object] $Value,[string] $Label)
    Assert-StratusCommandRuntimeString $Value $Label
    if (-not [regex]::IsMatch($Value,'\A[A-Za-z]:\\') -or $Value.IndexOf(':',2) -ge 0 -or $Value.Contains('/') -or
        [IO.Path]::GetFullPath($Value) -cne $Value) { throw "$Label must be an exact local absolute path." }
    foreach ($part in $Value.Substring(3).Split('\')) {
        if ($part.Length -eq 0 -or $part.EndsWith('.') -or $part.EndsWith(' ') -or $part.IndexOfAny([IO.Path]::GetInvalidFileNameChars()) -ge 0 -or
            [regex]::IsMatch($part.Split('.')[0],'\A(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])\z')) { throw "$Label contains an ambiguous path component." }
    }
}
function Get-StratusCommandRuntimeUtc {
    param([object] $Value,[string] $Label)
    Assert-StratusCommandRuntimeString $Value $Label
    $time=[DateTimeOffset]::ParseExact($Value,'o',[Globalization.CultureInfo]::InvariantCulture)
    if ($time.Offset -ne [TimeSpan]::Zero -or $time.ToString('o') -cne $Value) { throw "$Label must be canonical UTC." }
    return $time
}
function Assert-StratusCommandRuntimeFile {
    param([object] $Value,[long] $Maximum,[string] $Label)
    Assert-StratusCommandRuntimeObject $Value @('path','sha256','bytes') $Label
    Assert-StratusCommandRuntimePath $Value.path "$Label path"
    Assert-StratusCommandRuntimeHash $Value.sha256 "$Label hash"
    Assert-StratusCommandRuntimeInteger $Value.bytes 1 $Maximum "$Label byte count"
}
function Assert-StratusCommandRuntimeEqual {
    param([object] $Actual,[object] $Expected,[string] $Label)
    if ($null -eq $Actual -or $null -eq $Expected) { throw "$Label cannot be null." }
    Assert-StratusCommandRuntimePlainValue $Actual $Label
    Assert-StratusCommandRuntimePlainValue $Expected "$Label expected"
    if ($Expected -is [Array]) {
        if ($Actual -isnot [Array] -or $Actual.Rank -ne 1 -or $Expected.Rank -ne 1 -or $Actual.Count -ne $Expected.Count) { throw "$Label array differs." }
        for ($index=0;$index -lt $Expected.Count;$index++) { Assert-StratusCommandRuntimeEqual $Actual[$index] $Expected[$index] "$Label[$index]" }
    } elseif ($Expected -is [System.Management.Automation.PSCustomObject]) {
        $fields=@($Expected.PSObject.Properties | ForEach-Object {$_.Name})
        Assert-StratusCommandRuntimeObject $Expected $fields "$Label expected object"
        Assert-StratusCommandRuntimeObject $Actual $fields $Label
        foreach ($field in $fields) { Assert-StratusCommandRuntimeEqual $Actual.$field $Expected.$field "$Label.$field" }
    } elseif ($Expected -is [int] -or $Expected -is [long]) {
        Assert-StratusCommandRuntimeInteger $Actual 0 ([long]::MaxValue) $Label
        if ($Actual -ne $Expected) { throw "$Label integer differs." }
    } elseif ($Expected -is [string]) {
        if ($Actual -isnot [string] -or $Actual -cne $Expected) { throw "$Label scalar differs." }
    } elseif ($Expected -is [bool]) {
        if ($Actual -isnot [bool] -or $Actual -ne $Expected) { throw "$Label scalar differs." }
    } else { throw "$Label scalar differs." }
}
function Get-StratusCommandRuntimeHash {
    param([object] $Bytes,[long] $Maximum,[string] $Label)
    Assert-StratusCommandRuntimePlainValue $Bytes $Label
    if ($Bytes -isnot [byte[]] -or $Bytes.Length -lt 1 -or $Bytes.LongLength -gt $Maximum) { throw "$Label must be a bounded native byte array." }
    $sha=[Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash($Bytes)).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
}
function ConvertTo-StratusCommandRuntimeJsonString {
    param([object] $Value)
    Assert-StratusCommandRuntimePlainValue $Value 'Runtime contract JSON string'
    if ($Value -isnot [string]) { throw 'Runtime contract JSON string is not a native string.' }
    $text=[Text.StringBuilder]::new(); [void]$text.Append('"')
    foreach ($character in $Value.ToCharArray()) {
        $code=[int]$character
        if ($code -eq 34) { [void]$text.Append('\"') }
        elseif ($code -eq 92) { [void]$text.Append('\\') }
        elseif ($code -lt 32) { [void]$text.Append('\u'); [void]$text.Append($code.ToString('x4',[Globalization.CultureInfo]::InvariantCulture)) }
        else { [void]$text.Append($character) }
    }
    [void]$text.Append('"'); return $text.ToString()
}
function ConvertTo-StratusCommandRuntimeJson {
    param([object] $Value,[int] $Depth=0)
    if ($Depth -gt 8 -or $null -eq $Value) { throw 'Runtime contract JSON depth or null differs.' }
    Assert-StratusCommandRuntimePlainValue $Value 'Runtime contract JSON value'
    if ($Value -is [string]) { return ConvertTo-StratusCommandRuntimeJsonString $Value }
    if ($Value -is [bool]) { if ($Value) { return 'true' }; return 'false' }
    if ($Value -is [int] -or $Value -is [long]) { return $Value.ToString([Globalization.CultureInfo]::InvariantCulture) }
    $parts=[Collections.Generic.List[string]]::new()
    if ($Value -is [Array]) {
        if ($Value.Rank -ne 1 -or $Value.Count -gt 128) { throw 'Runtime contract JSON array bound differs.' }
        foreach ($item in $Value) { $parts.Add((ConvertTo-StratusCommandRuntimeJson $item ($Depth+1))) }
        return '['+[string]::Join(',',$parts.ToArray())+']'
    }
    if ($Value -isnot [System.Management.Automation.PSCustomObject]) { throw 'Runtime contract JSON value is not a native object.' }
    $fields=@($Value.PSObject.Properties | ForEach-Object {$_.Name})
    Assert-StratusCommandRuntimeObject $Value $fields 'Runtime contract JSON object'
    foreach ($property in $Value.PSObject.Properties) {
        if ($property.MemberType -ne [Management.Automation.PSMemberTypes]::NoteProperty) { throw 'Runtime contract JSON property is not plain data.' }
        $parts.Add((ConvertTo-StratusCommandRuntimeJsonString $property.Name)+':'+(ConvertTo-StratusCommandRuntimeJson $property.Value ($Depth+1)))
    }
    return '{'+[string]::Join(',',$parts.ToArray())+'}'
}
function Get-StratusCommandRuntimeContractBytes {
    param([object] $Value)
    # A producer may use this pure encoding after building the exact schema. The
    # helper only encodes: it does not validate or authorize its input.
    $json=(ConvertTo-StratusCommandRuntimeJson $Value)+"`n"
    $utf8=[Text.UTF8Encoding]::new($false,$true)
    return ,$utf8.GetBytes($json)
}
function Assert-StratusParentCommandRuntimeContract {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][AllowNull()][object] $ContractDocument,
        [Parameter(Mandatory=$true)][AllowNull()][object] $ContractBytes,
        [Parameter(Mandatory=$true)][AllowNull()][object] $SourceBytes,
        [Parameter(Mandatory=$true)][AllowNull()][object] $AssemblyBytes,
        [Parameter(Mandatory=$true)][AllowNull()][object] $Expected)
    Assert-StratusCommandRuntimeObject $Expected @('root','revision','source_manifest_sha256','r1','qualified_at','build_id',
        'started_at','completed_at','target_framework','architecture','assembly_name','host','source','assembly','compiler',
        'references','compiler_arguments','result','build_receipt') 'expected command runtime'
    Assert-StratusCommandRuntimePath $Expected.root 'expected root'
    Assert-StratusCommandRuntimeObject $Expected.r1 @('directory','acceptance_sha256','evidence_manifest_sha256','sealed_sha256') 'expected R1'
    Assert-StratusCommandRuntimePath $Expected.r1.directory 'expected R1 directory'
    foreach ($name in @('acceptance_sha256','evidence_manifest_sha256','sealed_sha256')) { Assert-StratusCommandRuntimeHash $Expected.r1.$name "expected R1 $name" }
    if (-not $Expected.r1.directory.StartsWith($Expected.root+'\',[StringComparison]::Ordinal)) { throw 'Expected R1 directory escapes the exact root.' }
    $qualified=Get-StratusCommandRuntimeUtc $Expected.qualified_at 'expected qualification time'
    Assert-StratusCommandRuntimeFile $Expected.build_receipt 262144 'expected build receipt'
    Assert-StratusCommandRuntimeObject $ContractDocument @('path','sha256','value') 'build document'
    Assert-StratusCommandRuntimePath $ContractDocument.path 'build document path'
    Assert-StratusCommandRuntimeHash $ContractDocument.sha256 'build document hash'
    $value=$ContractDocument.value
    Assert-StratusCommandRuntimeObject $value @('format','abi','revision','source_manifest_sha256','build_id','started_at','completed_at',
        'target_framework','architecture','assembly_name','host','source','assembly','compiler','references','compiler_arguments','result') 'runtime build'
    foreach ($name in @('format','abi','revision','source_manifest_sha256','build_id','started_at','completed_at','target_framework','architecture','assembly_name')) {
        Assert-StratusCommandRuntimeString $value.$name "runtime build $name"
    }
    if ($value.format -cne 'stratus-parent-command-runtime-build-v1' -or $value.abi -cne 'stratus-parent-command-session-v1' -or
        $value.architecture -cne 'x64' -or $value.assembly_name -cne 'Stratus.ParentStage.Runtime' -or
        -not [regex]::IsMatch($value.revision,'\A[0-9a-f]{40}\z') -or -not [regex]::IsMatch($value.build_id,'\A[0-9a-f]{32}\z') -or
        -not [regex]::IsMatch($value.target_framework,'\A\.NET(?:Framework|CoreApp|Standard),Version=v[0-9]+\.[0-9]+(?:\.[0-9]+)?\z')) { throw 'Runtime build fixed format/ABI/profile identity differs.' }
    Assert-StratusCommandRuntimeHash $value.source_manifest_sha256 'runtime build source manifest'
    $started=Get-StratusCommandRuntimeUtc $value.started_at 'runtime build start'
    $completed=Get-StratusCommandRuntimeUtc $value.completed_at 'runtime build completion'
    if ($completed -lt $started -or $completed -gt $qualified) { throw 'Runtime build chronology differs from qualification.' }
    Assert-StratusCommandRuntimeFile $value.source 2097152 'runtime source'
    Assert-StratusCommandRuntimeFile $value.assembly 16777216 'runtime assembly'
    Assert-StratusCommandRuntimeObject $value.host @('path','sha256','bytes','ps_edition','ps_version','clr_version') 'runtime host'
    Assert-StratusCommandRuntimeObject $value.compiler @('path','sha256','bytes','version') 'runtime compiler'
    foreach ($pair in @(@('host',$value.host),@('compiler',$value.compiler))) {
        Assert-StratusCommandRuntimePath $pair[1].path ($pair[0]+' path')
        Assert-StratusCommandRuntimeHash $pair[1].sha256 ($pair[0]+' hash')
        Assert-StratusCommandRuntimeInteger $pair[1].bytes 1 134217728 ($pair[0]+' bytes')
    }
    foreach ($name in @('ps_edition','ps_version','clr_version')) { Assert-StratusCommandRuntimeString $value.host.$name "runtime host $name" }
    Assert-StratusCommandRuntimeString $value.compiler.version 'runtime compiler version'
    if (($value.host.ps_edition -ceq 'Desktop' -and -not [regex]::IsMatch($value.host.ps_version,'\A5\.1\.[0-9]+\.[0-9]+\z')) -or
        ($value.host.ps_edition -ceq 'Core' -and -not [regex]::IsMatch($value.host.ps_version,'\A7\.[0-9]+\.[0-9]+(?:\.[0-9]+)?\z')) -or
        $value.host.ps_edition -cnotin @('Desktop','Core') -or -not [regex]::IsMatch($value.host.clr_version,'\A[0-9]+(?:\.[0-9]+){1,3}\z')) { throw 'Runtime host exact edition/version profile differs.' }
    if ($value.source.path -cne ($Expected.root+'\scripts\Stratus.ParentStage.Runtime.cs') -or
        $value.assembly.path -cne ($Expected.r1.directory+'\Stratus.ParentStage.Runtime.dll') -or
        $ContractDocument.path -cne ($Expected.r1.directory+'\command-runtime-build.json') -or
        $ContractDocument.path -cne $Expected.build_receipt.path -or $ContractDocument.sha256 -cne $Expected.build_receipt.sha256) { throw 'Runtime fixed source/assembly/receipt paths or receipt hash differ.' }
    $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($path in @($value.source.path,$value.assembly.path,$value.host.path,$value.compiler.path,$ContractDocument.path)) {
        if (-not $seen.Add($path)) { throw 'Runtime input/output paths alias.' }
    }
    Assert-StratusCommandRuntimePlainValue $value.references 'Runtime references'
    if ($value.references -isnot [Array] -or $value.references.Rank -ne 1 -or $value.references.Count -lt 1 -or $value.references.Count -gt 32) { throw 'Runtime references must be a bounded ordered native array.' }
    foreach ($reference in $value.references) {
        Assert-StratusCommandRuntimeFile $reference 134217728 'runtime reference'
        if (-not $reference.path.EndsWith('.dll',[StringComparison]::OrdinalIgnoreCase) -or -not $seen.Add($reference.path)) { throw 'Runtime reference path is not a distinct DLL.' }
    }
    Assert-StratusCommandRuntimePlainValue $value.compiler_arguments 'Runtime compiler arguments'
    if ($value.compiler_arguments -isnot [Array] -or $value.compiler_arguments.Rank -ne 1 -or
        $value.compiler_arguments.Count -lt 6 -or $value.compiler_arguments.Count -gt 128) { throw 'Runtime compiler arguments must be a bounded ordered native array.' }
    $required=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($argument in @('/target:library','/noconfig','/nostdlib+','/platform:x64',$value.source.path,('/out:'+$value.assembly.path))) { [void]$required.Add($argument) }
    foreach ($reference in $value.references) { [void]$required.Add('/reference:'+$reference.path) }
    $optional=@('/optimize+','/debug-','/deterministic+')
    $seenArguments=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($argument in $value.compiler_arguments) {
        Assert-StratusCommandRuntimeString $argument 'runtime compiler argument'
        if (-not $seenArguments.Add($argument) -or (-not $required.Contains($argument) -and $argument -cnotin $optional)) { throw 'Runtime compiler argument is duplicated or outside the exact direct-csc recipe.' }
    }
    foreach ($argument in $required) { if (-not $seenArguments.Contains($argument)) { throw 'Runtime compiler recipe omits a required input/output/flag.' } }
    Assert-StratusCommandRuntimeObject $value.result @('exit_code','stdout_sha256','stdout_bytes','stderr_sha256','stderr_bytes') 'runtime build result'
    Assert-StratusCommandRuntimeInteger $value.result.exit_code 0 0 'runtime build exit'
    Assert-StratusCommandRuntimeInteger $value.result.stdout_bytes 0 1048576 'runtime build stdout count'
    Assert-StratusCommandRuntimeInteger $value.result.stderr_bytes 0 0 'runtime build stderr count'
    Assert-StratusCommandRuntimeHash $value.result.stdout_sha256 'runtime build stdout hash'
    Assert-StratusCommandRuntimeHash $value.result.stderr_sha256 'runtime build stderr hash'
    $emptyHash='e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
    if ($value.result.stderr_sha256 -cne $emptyHash -or ($value.result.stdout_bytes -eq 0 -and $value.result.stdout_sha256 -cne $emptyHash) -or
        ($value.result.stdout_bytes -gt 0 -and $value.result.stdout_sha256 -ceq $emptyHash)) { throw 'Runtime empty stream digest differs.' }
    foreach ($name in @('revision','source_manifest_sha256','build_id','started_at','completed_at','target_framework','architecture','assembly_name',
        'host','source','assembly','compiler','references','compiler_arguments','result')) { Assert-StratusCommandRuntimeEqual $value.$name $Expected.$name "independent expected $name" }
    $contractHash=Get-StratusCommandRuntimeHash $ContractBytes 262144 'actual contract bytes'
    $sourceHash=Get-StratusCommandRuntimeHash $SourceBytes 2097152 'actual source bytes'
    $assemblyHash=Get-StratusCommandRuntimeHash $AssemblyBytes 16777216 'actual assembly bytes'
    if ($contractHash -cne $Expected.build_receipt.sha256 -or $ContractBytes.LongLength -ne $Expected.build_receipt.bytes -or
        $sourceHash -cne $value.source.sha256 -or $SourceBytes.LongLength -ne $value.source.bytes -or
        $assemblyHash -cne $value.assembly.sha256 -or $AssemblyBytes.LongLength -ne $value.assembly.bytes) { throw 'Actual byte snapshot hash or length differs.' }
    $encoded=Get-StratusCommandRuntimeContractBytes $value
    if ($encoded.LongLength -ne $ContractBytes.LongLength) { throw 'Build value is not the exact compact wire bytes.' }
    for ($index=0;$index -lt $encoded.Length;$index++) { if ($encoded[$index] -ne $ContractBytes[$index]) { throw 'Build value is not the exact compact wire bytes.' } }
    return [pscustomobject]@{format='stratus-parent-command-runtime-binding-v1';abi=$value.abi;assembly_name=$value.assembly_name
        revision=$value.revision;source_manifest_sha256=$value.source_manifest_sha256;r1_acceptance_sha256=$Expected.r1.acceptance_sha256
        build_id=$value.build_id;contract_path=$ContractDocument.path;contract_sha256=$contractHash;contract_bytes=$ContractBytes.LongLength
        source_path=$value.source.path;source_sha256=$sourceHash;source_bytes=$SourceBytes.LongLength
        assembly_path=$value.assembly.path;assembly_sha256=$assemblyHash;assembly_bytes=$AssemblyBytes.LongLength
        host_sha256=$value.host.sha256;target_framework=$value.target_framework;architecture=$value.architecture
        document_value_correspondence_verified=$true;input_snapshot_digests_verified=$true
        approved_expected_provenance_verified=$false;held_handle_provenance_verified=$false;compiler_execution_verified=$false
        compiler_reference_bytes_verified=$false;build_causality_verified=$false;assembly_metadata_verified=$false;clr_compatibility_verified=$false
        same_held_bytes_import_verified=$false;import_permitted=$false;session_job_ownership_verified=$false;native_qualified=$false
        production_start_permitted=$false;live_permitted=$false;binance_kill_switch_removal_permitted=$false;full_goal_accepted=$false}
}
