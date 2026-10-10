[CmdletBinding()]
param([string] $LibraryPath)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if (-not $LibraryPath) { $LibraryPath=Join-Path (Split-Path $PSScriptRoot -Parent) 'Stratus.ParentCommandRuntimeContract.ps1' }
# Pure managed byte/metadata tests. Synthetic assembly bytes are intentionally
# NOT a loadable PE; successful binding must not assert assembly/build acceptance.
# No source compilation, Assembly.Load, native member, process/Job, filesystem
# fixture, Docker, database or producer main body is executed.
$source=Get-Content -LiteralPath $LibraryPath -Raw
function Assert-RuntimeTest { param([bool] $Okay,[string] $Message) if (-not $Okay) { throw $Message } }
function Get-RuntimeTestProgram {
    param([string] $Text)
    $tokens=$null;$errors=$null
    $ast=[Management.Automation.Language.Parser]::ParseInput($Text,[ref]$tokens,[ref]$errors)
    Assert-RuntimeTest (@($errors).Count -eq 0) 'Pure library parse failed.'
    $names=@('Assert-StratusCommandRuntimePlainValue','Assert-StratusCommandRuntimeObject','Assert-StratusCommandRuntimeString','Assert-StratusCommandRuntimeHash',
        'Assert-StratusCommandRuntimeInteger','Assert-StratusCommandRuntimePath','Get-StratusCommandRuntimeUtc',
        'Assert-StratusCommandRuntimeFile','Assert-StratusCommandRuntimeEqual','Get-StratusCommandRuntimeHash',
        'ConvertTo-StratusCommandRuntimeJsonString','ConvertTo-StratusCommandRuntimeJson','Get-StratusCommandRuntimeContractBytes',
        'Assert-StratusParentCommandRuntimeContract')
    Assert-RuntimeTest ($ast.EndBlock.Statements.Count -eq $names.Count) 'Pure library top-level count differs.'
    $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($statement in $ast.EndBlock.Statements) {
        Assert-RuntimeTest ($statement -is [Management.Automation.Language.FunctionDefinitionAst] -and $statement.Name -cin $names -and $seen.Add($statement.Name)) 'Unreviewed top-level statement/function.'
    }
    foreach ($command in $ast.FindAll({param($n) $n -is [Management.Automation.Language.CommandAst]},$true)) {
        Assert-RuntimeTest ($command.InvocationOperator -eq [Management.Automation.Language.TokenKind]::Unknown -and
            ($command.GetCommandName() -cin $names -or $command.GetCommandName() -ceq 'ForEach-Object')) 'Unreviewed pure command.'
    }
    foreach ($type in $ast.FindAll({param($n) $n -is [Management.Automation.Language.TypeExpressionAst] -or $n -is [Management.Automation.Language.TypeConstraintAst]},$true)) {
        Assert-RuntimeTest ($type.TypeName.FullName -cin @('Array','BitConverter','bool','byte[]','char[]','Collections.Generic.HashSet[string]',
            'Collections.Generic.List[string]','DateTimeOffset','Globalization.CultureInfo','int','IO.Path','long','Management.Automation.PSMemberTypes','Management.Automation.PSAliasProperty',
            'object','pscustomobject','regex','Security.Cryptography.SHA256','string','string[]','StringComparer','StringComparison','Text.StringBuilder',
            'Text.UTF8Encoding','TimeSpan','void','System.Management.Automation.PSCustomObject')) 'Unreviewed pure type.'
    }
    foreach ($member in $ast.FindAll({param($n) $n -is [Management.Automation.Language.InvokeMemberExpressionAst]},$true)) {
        Assert-RuntimeTest ($member.Member -is [Management.Automation.Language.StringConstantExpressionAst] -and $member.Member.Value -cin @(
            'Add','Append','ComputeHash','Contains','Create','Dispose','EndsWith','GetBytes','GetFullPath','GetInvalidFileNameChars',
            'IndexOf','IndexOfAny','IsMatch','Join','new','ParseExact','Replace','Split','StartsWith','Substring','ToArray','ToCharArray','ToLowerInvariant','ToString')) 'Unreviewed pure member.'
    }
    Assert-RuntimeTest (@($ast.FindAll({param($n) $n -is [Management.Automation.Language.RedirectionAst]},$true)).Count -eq 0) 'Pure library redirection forbidden.'
    return $Text
}
. ([scriptblock]::Create((Get-RuntimeTestProgram $source)))
$results=[Collections.Generic.List[object]]::new()
function Case {
    param([string] $Name,[scriptblock] $Body)
    try { & $Body } catch { throw ('Runtime-contract case '+$Name+' failed: '+$_.Exception.Message) }
    $results.Add([pscustomobject]@{name=$Name;passed=$true})
}
function Reject {
    param([scriptblock] $Body,[string] $Pattern='.')
    $failure=$null; try { & $Body | Out-Null } catch { $failure=$_.Exception.Message }
    if ($null -eq $failure -or $failure -notmatch $Pattern) { throw ('Expected rejection '+$Pattern+'; observed '+$failure) }
}
function Clone-Data {
    param([object] $Value)
    # Preserve native strings across PS5/PS7; PS7 JSON decoding may infer dates.
    if ($Value -is [Array]) { $copy=@(); foreach ($item in $Value) { $copy+=,(Clone-Data $item) }; return ,$copy }
    if ($Value -is [System.Management.Automation.PSCustomObject]) {
        $copy=[ordered]@{};foreach ($property in $Value.PSObject.Properties) {$copy[$property.Name]=Clone-Data $property.Value};return [pscustomobject]$copy
    }
    return $Value
}
function New-FileRecord { param([string] $Path,[string] $Hash,[long] $Bytes) return [pscustomobject][ordered]@{path=$Path;sha256=$Hash;bytes=$Bytes} }
function New-Fixture {
    $root='C:\stratus'; $directory=$root+'\.stratus-cache\qualified-r1'
    $sourceBytes=[Text.UTF8Encoding]::new($false,$true).GetBytes('// synthetic source bytes; never compile')
    $assemblyBytes=[byte[]]@(77,90,1,2,3,4,5,6)
    $empty='e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
    $runtimeSource=New-FileRecord ($root+'\scripts\Stratus.ParentStage.Runtime.cs') (Get-StratusCommandRuntimeHash $sourceBytes 2097152 'fixture source') $sourceBytes.Length
    $assembly=New-FileRecord ($directory+'\Stratus.ParentStage.Runtime.dll') (Get-StratusCommandRuntimeHash $assemblyBytes 16777216 'fixture assembly') $assemblyBytes.Length
    $references=[object[]]@((New-FileRecord 'C:\reference\mscorlib.dll' ('4'*64) 100),(New-FileRecord 'C:\reference\System.dll' ('5'*64) 200))
    $arguments=[string[]]@('/target:library','/noconfig','/nostdlib+','/platform:x64',('/out:'+$assembly.path),('/reference:'+$references[0].path),('/reference:'+$references[1].path),$runtimeSource.path)
    $value=[pscustomobject][ordered]@{format='stratus-parent-command-runtime-build-v1';abi='stratus-parent-command-session-v1'
        revision=('a'*40);source_manifest_sha256=('b'*64);build_id=('c'*32);started_at='2026-09-15T00:00:00.0000000+00:00'
        completed_at='2026-09-15T00:01:00.0000000+00:00';target_framework='.NETFramework,Version=v4.8';architecture='x64';assembly_name='Stratus.ParentStage.Runtime'
        host=[pscustomobject][ordered]@{path='C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe';sha256=('1'*64);bytes=1000;ps_edition='Desktop';ps_version='5.1.26100.3624';clr_version='4.0.30319.42000'}
        source=$runtimeSource;assembly=$assembly;compiler=[pscustomobject][ordered]@{path='C:\compiler\csc.exe';sha256=('2'*64);bytes=2000;version='fixture compiler 1.0'}
        references=$references;compiler_arguments=$arguments;result=[pscustomobject][ordered]@{exit_code=0;stdout_sha256=$empty;stdout_bytes=0;stderr_sha256=$empty;stderr_bytes=0}}
    $expectedMap=[ordered]@{root=$root;revision=$value.revision;source_manifest_sha256=$value.source_manifest_sha256
        r1=[pscustomobject][ordered]@{directory=$directory;acceptance_sha256=('d'*64);evidence_manifest_sha256=('e'*64);sealed_sha256=('f'*64)}
        qualified_at='2026-09-15T00:02:00.0000000+00:00'}
    foreach ($name in @('build_id','started_at','completed_at','target_framework','architecture','assembly_name','host','source','assembly','compiler','references','compiler_arguments','result')) {
        $expectedMap[$name]=Clone-Data $value.$name
    }
    $expectedMap.build_receipt=New-FileRecord ($directory+'\command-runtime-build.json') ('0'*64) 1
    $fixture=[pscustomobject]@{document=[pscustomobject][ordered]@{path=$expectedMap.build_receipt.path;sha256=('0'*64);value=$value}
        contract_bytes=[byte[]]@(1);source_bytes=$sourceBytes;assembly_bytes=$assemblyBytes;expected=[pscustomobject]$expectedMap}
    Reseal-Fixture $fixture
    return $fixture
}
function Bind-RawReceipt {
    param([object] $Fixture)
    $sha=Get-StratusCommandRuntimeHash $Fixture.contract_bytes 262144 'fixture receipt'
    $Fixture.document.sha256=$sha; $Fixture.expected.build_receipt.sha256=$sha; $Fixture.expected.build_receipt.bytes=[long]$Fixture.contract_bytes.Length
}
function Reseal-Fixture { param([object] $Fixture) $Fixture.contract_bytes=Get-StratusCommandRuntimeContractBytes $Fixture.document.value; Bind-RawReceipt $Fixture }
function Invoke-Fixture {
    param([object] $Fixture)
    return Assert-StratusParentCommandRuntimeContract -ContractDocument $Fixture.document -ContractBytes $Fixture.contract_bytes -SourceBytes $Fixture.source_bytes -AssemblyBytes $Fixture.assembly_bytes -Expected $Fixture.expected
}
function Assert-BindingOnly {
    param([object] $Projection,[object] $Fixture)
    Assert-RuntimeTest ($Projection.document_value_correspondence_verified -eq $true -and $Projection.input_snapshot_digests_verified -eq $true -and
        $Projection.assembly_sha256 -ceq $Fixture.expected.assembly.sha256 -and $Projection.contract_sha256 -ceq $Fixture.expected.build_receipt.sha256 -and
        $Projection.source_sha256 -ceq $Fixture.expected.source.sha256 -and $Projection.assembly_bytes -eq $Fixture.assembly_bytes.Length) 'Actual byte-binding projection differs.'
    foreach ($name in @('approved_expected_provenance_verified','held_handle_provenance_verified','compiler_execution_verified','compiler_reference_bytes_verified',
        'build_causality_verified','assembly_metadata_verified','clr_compatibility_verified','same_held_bytes_import_verified','import_permitted',
        'session_job_ownership_verified','native_qualified','production_start_permitted','live_permitted','binance_kill_switch_removal_permitted','full_goal_accepted')) {
        Assert-RuntimeTest ($Projection.$name -is [bool] -and -not $Projection.$name) ('Projection granted '+$name)
    }
}
Case 'synthetic Desktop byte binding does not accept a build or loadable assembly' { $f=New-Fixture; Assert-BindingOnly (Invoke-Fixture $f) $f }
Case 'independently matching Core host profile is bound without compatibility inference' {
    $f=New-Fixture; $f.document.value.host.ps_edition='Core';$f.document.value.host.ps_version='7.6.5';$f.document.value.host.clr_version='10.0.0';$f.document.value.target_framework='.NETCoreApp,Version=v10.0'
    $f.expected.host=Clone-Data $f.document.value.host;$f.expected.target_framework=$f.document.value.target_framework;Reseal-Fixture $f
    Assert-BindingOnly (Invoke-Fixture $f) $f
}
Case 'approved optional compiler flags preserve exact argv binding' {
    $f=New-Fixture;$f.document.value.compiler_arguments=[string[]]@($f.document.value.compiler_arguments+@('/optimize+','/debug-','/deterministic+'))
    $f.expected.compiler_arguments=Clone-Data $f.document.value.compiler_arguments;Reseal-Fixture $f;Assert-BindingOnly (Invoke-Fixture $f) $f
}
Case 'quoted backslash and Unicode metadata has exact strict UTF8 wire correspondence' {
    $f=New-Fixture;$f.document.value.compiler.version='approved "compiler" \ '+[char]0x3bb;$f.expected.compiler.version=$f.document.value.compiler.version
    Reseal-Fixture $f;Assert-BindingOnly (Invoke-Fixture $f) $f
}
Case 'caller JSON value is accepted only when exact reserialization matches actual raw bytes' {
    $f=New-Fixture;$json=[Text.UTF8Encoding]::new($false,$true).GetString($f.contract_bytes)
    if ($PSVersionTable.PSEdition -ceq 'Core') { $f.document.value=Microsoft.PowerShell.Utility\ConvertFrom-Json -InputObject $json -DateKind String }
    else { $f.document.value=Microsoft.PowerShell.Utility\ConvertFrom-Json -InputObject $json }
    Assert-BindingOnly (Invoke-Fixture $f) $f
}
$fields=@('format','abi','revision','source_manifest_sha256','build_id','started_at','completed_at','target_framework','architecture','assembly_name','host','source','assembly','compiler','references','compiler_arguments','result')
foreach ($field in $fields) {
    Case ('missing manifest field '+$field) { $f=New-Fixture;$f.document.value.PSObject.Properties.Remove($field);Reseal-Fixture $f;Reject {Invoke-Fixture $f} 'property set' }
}
$negatives=@(
    @('extra manifest field',{ $f.document.value | Add-Member unexpected $true }),
    @('format',{ $f.document.value.format='other' }),
    @('ABI',{ $f.document.value.abi='stratus-parent-command-session-v0' }),
    @('assembly simple name',{ $f.document.value.assembly_name='Arbitrary' }),
    @('architecture',{ $f.document.value.architecture='x86' }),
    @('target framework grammar',{ $f.document.value.target_framework='net-any' }),
    @('revision syntax',{ $f.document.value.revision='A'*40 }),
    @('build ID syntax',{ $f.document.value.build_id='c'*31 }),
    @('source manifest syntax',{ $f.document.value.source_manifest_sha256='B'*64 }),
    @('noncanonical build time',{ $f.document.value.started_at='2026-09-15T00:00:00Z' }),
    @('completion before start',{ $f.document.value.completed_at='2026-09-14T23:59:59.0000000+00:00' }),
    @('completion after qualification',{ $f.document.value.completed_at='2026-09-15T00:03:00.0000000+00:00' }),
    @('arbitrary source path',{ $f.document.value.source.path='C:\other\runtime.cs' }),
    @('arbitrary assembly path',{ $f.document.value.assembly.path='C:\other\runtime.dll' }),
    @('assembly filename alias',{ $f.document.value.assembly.path=$f.expected.r1.directory+'\command-runtime.dll' }),
    @('noncanonical dot path',{ $f.document.value.source.path='C:\stratus\scripts\..\scripts\Stratus.ParentStage.Runtime.cs' }),
    @('ADS path',{ $f.document.value.compiler.path='C:\compiler\csc.exe:other' }),
    @('UNC path',{ $f.document.value.compiler.path='\\server\share\csc.exe' }),
    @('device path component',{ $f.document.value.compiler.path='C:\compiler\CON.exe' }),
    @('trailing-space path component',{ $f.document.value.compiler.path='C:\compiler \csc.exe' }),
    @('string byte count',{ $f.document.value.source.bytes=[string]$f.document.value.source.bytes }),
    @('boolean byte count',{ $f.document.value.assembly.bytes=$true }),
    @('oversized declared assembly',{ $f.document.value.assembly.bytes=16777217 }),
    @('negative declared source',{ $f.document.value.source.bytes=-1 }),
    @('host edition',{ $f.document.value.host.ps_edition='Unknown' }),
    @('Desktop version mismatch',{ $f.document.value.host.ps_version='7.6.5' }),
    @('CLR version grammar',{ $f.document.value.host.clr_version='latest' }),
    @('host extra property',{ $f.document.value.host | Add-Member cache_hint 'cached' }),
    @('host/compiler alias',{ $f.document.value.compiler.path=$f.document.value.host.path }),
    @('compiler extra property',{ $f.document.value.compiler | Add-Member trusted $true }),
    @('reference array scalar',{ $f.document.value.references=$f.document.value.references[0] }),
    @('empty references',{ $f.document.value.references=[object[]]@() }),
    @('duplicate reference',{ $f.document.value.references[1]=Clone-Data $f.document.value.references[0] }),
    @('reference not DLL',{ $f.document.value.references[0].path='C:\reference\source.cs' }),
    @('reference hash drift',{ $f.document.value.references[0].sha256='6'*64 }),
    @('reference order drift',{ $f.document.value.references=[object[]]@($f.document.value.references[1],$f.document.value.references[0]) }),
    @('compiler args scalar',{ $f.document.value.compiler_arguments='/target:library' }),
    @('response-file compiler argument',{ $f.document.value.compiler_arguments[0]='@unapproved.rsp' }),
    @('unknown compiler flag',{ $f.document.value.compiler_arguments[0]='/unsafe+' }),
    @('missing noconfig',{ $f.document.value.compiler_arguments=[string[]]@($f.document.value.compiler_arguments | Where-Object {$_ -cne '/noconfig'}) }),
    @('duplicate compiler flag',{ $f.document.value.compiler_arguments=[string[]]@($f.document.value.compiler_arguments+@('/noconfig')) }),
    @('reference argument omitted',{ $f.document.value.compiler_arguments=[string[]]@($f.document.value.compiler_arguments | Where-Object {$_ -cne ('/reference:'+$f.document.value.references[0].path)}) }),
    @('wrong output argument',{ $f.document.value.compiler_arguments[4]='/out:C:\other\other.dll' }),
    @('command order drift',{ $tmp=$f.document.value.compiler_arguments[0];$f.document.value.compiler_arguments[0]=$f.document.value.compiler_arguments[1];$f.document.value.compiler_arguments[1]=$tmp }),
    @('build nonzero exit',{ $f.document.value.result.exit_code=1 }),
    @('build exit boolean',{ $f.document.value.result.exit_code=$false }),
    @('build stderr nonempty',{ $f.document.value.result.stderr_bytes=1 }),
    @('build stderr digest',{ $f.document.value.result.stderr_sha256='6'*64 }),
    @('build stdout empty digest mismatch',{ $f.document.value.result.stdout_sha256='6'*64 }),
    @('build stdout count with empty digest',{ $f.document.value.result.stdout_bytes=1 }),
    @('expected revision differs',{ $f.expected.revision='9'*40 }),
    @('expected assembly digest differs',{ $f.expected.assembly.sha256='9'*64 }),
    @('expected source count differs',{ $f.expected.source.bytes+=1 }),
    @('expected compiler digest differs',{ $f.expected.compiler.sha256='9'*64 }),
    @('expected reference length differs',{ $f.expected.references[0].bytes+=1 }),
    @('expected R1 hash malformed',{ $f.expected.r1.acceptance_sha256='BAD' }),
    @('expected R1 directory escapes root',{ $f.expected.r1.directory='C:\elsewhere' }),
    @('expected nested script property',{ $f.expected.compiler.PSObject.Properties.Remove('version');$f.expected.compiler | Add-Member ScriptProperty version {throw 'Script property must not execute.'} }),
    @('document value native object required',{ $f.document.value=@{format='wrong'} })
)
foreach ($entry in $negatives) {
    Case ('reject '+$entry[0]) {
        $f=New-Fixture; & $entry[1]
        Reject { Reseal-Fixture $f; Invoke-Fixture $f }
    }
}
$rawCases=@(
    @('receipt native byte type',{ $f.contract_bytes=[object[]]@($f.contract_bytes) }),
    @('source native byte type',{ $f.source_bytes='source' }),
    @('assembly native byte type',{ $f.assembly_bytes=[object[]]@($f.assembly_bytes) }),
    @('empty source bytes',{ $f.source_bytes=[byte[]]@() }),
    @('empty assembly bytes',{ $f.assembly_bytes=[byte[]]@() }),
    @('source raw digest drift',{ $f.source_bytes[0]=1 }),
    @('assembly raw digest drift',{ $f.assembly_bytes[0]=1 }),
    @('receipt raw digest drift',{ $f.contract_bytes[0]=1 }),
    @('declared receipt length drift',{ $f.expected.build_receipt.bytes+=1 }),
    @('document path drift',{ $f.document.path='C:\stratus\other.json' }),
    @('document hash drift',{ $f.document.sha256='9'*64 })
)
foreach ($entry in $rawCases) { Case ('raw binding rejects '+$entry[0]) { $f=New-Fixture;& $entry[1];Reject {Invoke-Fixture $f} } }
$wireCases=@(
    @('BOM',{ $text=[string][char]0xfeff+$text }),
    @('CRLF',{ $text=$text.Replace("`n","`r`n") }),
    @('missing LF',{ $text=$text.TrimEnd([char]10) }),
    @('extra LF',{ $text+="`n" }),
    @('leading whitespace',{ $text=' '+$text }),
    @('property whitespace',{ $text=$text.Replace('"format":','"format": ') }),
    @('same-value duplicate property',{ $text=$text.Replace('"abi":','"format":"stratus-parent-command-runtime-build-v1","abi":') }),
    @('escaped equivalent string',{ $text=$text.Replace('"x64"','"\u007864"') }),
    @('extra JSON document',{ $text+='{}' }),
    @('different wire value',{ $text=$text.Replace('fixture compiler 1.0','fixture compiler 2.0') })
)
foreach ($entry in $wireCases) {
    Case ('exact raw correspondence rejects '+$entry[0]) {
        $f=New-Fixture;$text=[Text.UTF8Encoding]::new($false,$true).GetString($f.contract_bytes);. $entry[1]
        $f.contract_bytes=[Text.UTF8Encoding]::new($false,$true).GetBytes($text);Bind-RawReceipt $f
        Reject {Invoke-Fixture $f} 'exact compact wire bytes'
    }
}
$loaderCases=@(
    @('top-level body',($source+"`nGet-Process")),
    @('external command',$source.Replace('    $value=$ContractDocument.value',"    Get-Process`n    `$value=`$ContractDocument.value")),
    @('dynamic invocation',$source.Replace('    $value=$ContractDocument.value',"    & 'Get-Process'`n    `$value=`$ContractDocument.value")),
    @('assembly loader type',$source.Replace('[Text.StringBuilder]::new()','[Reflection.Assembly]::Load()')),
    @('filesystem reader member',$source.Replace('.ToCharArray()', '.ReadAllText()')),
    @('parse error',($source+"`nfunction {"))
)
foreach ($entry in $loaderCases) { Case ('safe extraction rejects '+$entry[0]) { Reject {Get-RuntimeTestProgram $entry[1]} } }
$poisonCases=@(
    @('manifest GetType method',{ $f.document.value | Add-Member ScriptMethod GetType {$script:poisonCalls++;throw 'Must not dispatch.'} -Force },{Invoke-Fixture $f}),
    @('document GetType method',{ $f.document | Add-Member ScriptMethod GetType {$script:poisonCalls++;throw 'Must not dispatch.'} -Force },{Invoke-Fixture $f}),
    @('expected GetType method',{ $f.expected | Add-Member ScriptMethod GetType {$script:poisonCalls++;throw 'Must not dispatch.'} -Force },{Invoke-Fixture $f}),
    @('nested expected GetType method',{ $f.expected.compiler | Add-Member ScriptMethod GetType {$script:poisonCalls++;throw 'Must not dispatch.'} -Force },{Invoke-Fixture $f}),
    @('actual scalar GetType method',{ $f.document.value.abi=[pscustomobject]@{};$f.document.value.abi | Add-Member ScriptMethod GetType {$script:poisonCalls++;throw 'Must not dispatch.'} -Force },{Invoke-Fixture $f}),
    @('expected scalar GetType method',{ $f.expected.revision=[pscustomobject]@{};$f.expected.revision | Add-Member ScriptMethod GetType {$script:poisonCalls++;throw 'Must not dispatch.'} -Force },{Invoke-Fixture $f}),
    @('manifest property getter',{ $f.document.value.PSObject.Properties.Remove('abi');$f.document.value | Add-Member ScriptProperty abi {$script:poisonCalls++;throw 'Must not dispatch.'} },{Invoke-Fixture $f}),
    @('nested expected property getter',{ $f.expected.compiler.PSObject.Properties.Remove('version');$f.expected.compiler | Add-Member ScriptProperty version {$script:poisonCalls++;throw 'Must not dispatch.'} },{Invoke-Fixture $f}),
    @('serializer GetType method',{ $f.document.value | Add-Member ScriptMethod GetType {$script:poisonCalls++;throw 'Must not dispatch.'} -Force },{Get-StratusCommandRuntimeContractBytes $f.document.value}),
    @('serializer property getter',{ $f.document.value | Add-Member ScriptProperty injected {$script:poisonCalls++;throw 'Must not dispatch.'} },{Get-StratusCommandRuntimeContractBytes $f.document.value})
)
foreach ($entry in $poisonCases) {
    Case ('plain data rejects without invocation '+$entry[0]) {
        $f=New-Fixture;$script:poisonCalls=0;& $entry[1];Reject {& $entry[2]}
        Assert-RuntimeTest ($script:poisonCalls -eq 0) 'Poisoned member executed.'
    }
}
function New-PoisonPrimitive {
    param([object] $Value,[string] $Kind,[string] $Name)
    $wrapped=[psobject]::AsPSObject($Value)
    # PS5 refuses Add-Member over its non-instance Array.Count type-data alias.
    # Change only this in-memory wrapper's type-name list, not session/global
    # type data, so the explicit malicious instance member can be constructed.
    if ($Name -ceq 'Count' -and $wrapped -is [Array]) { $wrapped.PSObject.TypeNames.Clear() }
    if ($Kind -ceq 'ScriptProperty') { Add-Member -InputObject $wrapped -MemberType ScriptProperty -Name $Name -Value {$script:primitivePoisonCalls++;throw 'Primitive getter must not execute.'} -Force }
    elseif ($Kind -ceq 'ScriptMethod') { Add-Member -InputObject $wrapped -MemberType ScriptMethod -Name $Name -Value {$script:primitivePoisonCalls++;throw 'Primitive method must not execute.'} -Force }
    elseif ($Kind -ceq 'NoteProperty') { Add-Member -InputObject $wrapped -MemberType NoteProperty -Name $Name -Value 0 -Force }
    elseif ($Kind -ceq 'AliasProperty') { Add-Member -InputObject $wrapped -MemberType ScriptProperty -Name poisonAliasTarget -Value {$script:primitivePoisonCalls++;throw 'Aliased getter must not execute.'} -Force;Add-Member -InputObject $wrapped -MemberType AliasProperty -Name $Name -Value poisonAliasTarget -Force }
    else { throw 'Unsupported fixed primitive poison kind.' }
    Assert-RuntimeTest ($null -ne $wrapped.PSObject.Members[$Name] -and [string]$wrapped.PSObject.Members[$Name].MemberType -ceq $Kind) 'Primitive wrapper fixture lost its extended member.'
    return ,$wrapped
}
$primitivePoisonCases=@(
    @('string Length getter',{ $f.document.value.build_id=New-PoisonPrimitive $f.document.value.build_id ScriptProperty Length },{Invoke-Fixture $f}),
    @('string fake Length data',{ $f.document.value.build_id=New-PoisonPrimitive $f.document.value.build_id NoteProperty Length },{Invoke-Fixture $f}),
    @('string IndexOfAny method',{ $f.document.value.compiler.version=New-PoisonPrimitive $f.document.value.compiler.version ScriptMethod IndexOfAny },{Invoke-Fixture $f}),
    @('path Contains method',{ $f.expected.root=New-PoisonPrimitive $f.expected.root ScriptMethod Contains },{Invoke-Fixture $f}),
    @('path IndexOf method',{ $f.document.value.compiler.path=New-PoisonPrimitive $f.document.value.compiler.path ScriptMethod IndexOf },{Invoke-Fixture $f}),
    @('path Substring method',{ $f.document.value.source.path=New-PoisonPrimitive $f.document.value.source.path ScriptMethod Substring },{Invoke-Fixture $f}),
    @('UTC string Length getter',{ $f.document.value.started_at=New-PoisonPrimitive $f.document.value.started_at ScriptProperty Length },{Invoke-Fixture $f}),
    @('integer ToString method',{ $f.document.value.source.bytes=New-PoisonPrimitive ([int]40) ScriptMethod ToString },{Get-StratusCommandRuntimeContractBytes $f.document.value}),
    @('long ToString method',{ $f.document.value.assembly.bytes=New-PoisonPrimitive ([long]8) ScriptMethod ToString },{Invoke-Fixture $f}),
    @('boolean ToString method',{ $script:primitiveValue=New-PoisonPrimitive $true ScriptMethod ToString },{ConvertTo-StratusCommandRuntimeJson $script:primitiveValue}),
    @('source byte array Length getter',{ $f.source_bytes=New-PoisonPrimitive $f.source_bytes ScriptProperty Length },{Invoke-Fixture $f}),
    @('assembly byte array fake LongLength',{ $f.assembly_bytes=New-PoisonPrimitive $f.assembly_bytes NoteProperty LongLength },{Invoke-Fixture $f}),
    @('receipt byte array GetEnumerator method',{ $f.contract_bytes=New-PoisonPrimitive $f.contract_bytes ScriptMethod GetEnumerator },{Invoke-Fixture $f}),
    @('references Rank getter',{ $f.document.value.references=New-PoisonPrimitive $f.document.value.references ScriptProperty Rank },{Invoke-Fixture $f}),
    @('references fake Count',{ $f.document.value.references=New-PoisonPrimitive $f.document.value.references NoteProperty Count },{Invoke-Fixture $f}),
    @('arguments fake Length',{ $f.document.value.compiler_arguments=New-PoisonPrimitive $f.document.value.compiler_arguments NoteProperty Length },{Invoke-Fixture $f}),
    @('arguments GetEnumerator method',{ $f.document.value.compiler_arguments=New-PoisonPrimitive $f.document.value.compiler_arguments ScriptMethod GetEnumerator },{Invoke-Fixture $f}),
    @('expected references Count getter',{ $f.expected.references=New-PoisonPrimitive $f.expected.references ScriptProperty Count },{Invoke-Fixture $f}),
    @('expected string Length getter',{ $f.expected.revision=New-PoisonPrimitive $f.expected.revision ScriptProperty Length },{Invoke-Fixture $f}),
    @('string serializer ToCharArray method',{ $script:primitiveValue=New-PoisonPrimitive 'poison char array' ScriptMethod ToCharArray },{ConvertTo-StratusCommandRuntimeJsonString $script:primitiveValue}),
    @('array serializer fake Rank',{ $script:primitiveValue=New-PoisonPrimitive ([object[]]@('one','two')) NoteProperty Rank },{ConvertTo-StratusCommandRuntimeJson $script:primitiveValue}),
    @('array serializer Count getter',{ $script:primitiveValue=New-PoisonPrimitive ([object[]]@('three','four')) ScriptProperty Count },{ConvertTo-StratusCommandRuntimeJson $script:primitiveValue}),
    @('actual equality string Equals method',{ $script:primitiveValue=New-PoisonPrimitive 'equal actual poison' ScriptMethod Equals },{Assert-StratusCommandRuntimeEqual $script:primitiveValue 'equal actual poison' 'fixture'}),
    @('expected equality integer ToString method',{ $script:primitiveValue=New-PoisonPrimitive ([int]501) ScriptMethod ToString },{Assert-StratusCommandRuntimeEqual ([int]501) $script:primitiveValue 'fixture'}),
    @('string Length alias getter',{ $script:primitiveValue=New-PoisonPrimitive 'alias poison' AliasProperty Length },{Assert-StratusCommandRuntimeString $script:primitiveValue 'fixture'}),
    @('byte array Length alias getter',{ $script:primitiveValue=New-PoisonPrimitive ([byte[]]@(3,4,5)) AliasProperty Length },{Get-StratusCommandRuntimeHash $script:primitiveValue 100 'fixture'})
)
foreach ($entry in $primitivePoisonCases) {
    Case ('wrapped scalar array rejects without invocation '+$entry[0]) {
        $f=New-Fixture;$script:primitivePoisonCalls=0;& $entry[1];Reject {& $entry[2]} 'non-data extended member'
        Assert-RuntimeTest ($script:primitivePoisonCalls -eq 0) 'Primitive poisoned member executed.'
    }
}
foreach ($target in @('Length','LongLength')) {
    Case ('instance array Count alias rejected even with native target '+$target) {
        $f=New-Fixture;$wrapped=[psobject]::AsPSObject($f.document.value.references)
        $wrapped.PSObject.TypeNames.Clear()
        Add-Member -InputObject $wrapped -MemberType AliasProperty -Name Count -Value $target -Force
        Assert-RuntimeTest ($wrapped.PSObject.Members['Count'].IsInstance -and $wrapped.PSObject.Members['Count'].ReferencedMemberName -ceq $target) 'Instance Count alias fixture differs.'
        $f.document.value.references=$wrapped;Reject {Invoke-Fixture $f} 'non-data extended member'
    }
}
$expectedCases=146
if ($results.Count -ne $expectedCases -or @($results | Where-Object {-not $_.passed}).Count -ne 0) { throw ('Fixed runtime-contract count differs: '+$results.Count+' vs '+$expectedCases) }
[pscustomobject]@{format='stratus-parent-command-runtime-contract-tests-v1';expected=$expectedCases;executed=$results.Count;passed=$results.Count;failed=0
    ps_version=$PSVersionTable.PSVersion.ToString();ps_edition=$PSVersionTable.PSEdition
    scope='pure byte and build-metadata binding; synthetic non-loadable assembly fixture; no compilation/import/native calls'
    assembly_loaded=$false;build_accepted=$false;native_qualified=$false;full_goal_accepted=$false;cases=$results.ToArray()} | Microsoft.PowerShell.Utility\ConvertTo-Json -Depth 5 -Compress
