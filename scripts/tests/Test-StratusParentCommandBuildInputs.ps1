[CmdletBinding()]
param([string] $LibraryPath,[string] $HelperPath)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if (-not $LibraryPath) { $LibraryPath=Join-Path (Split-Path $PSScriptRoot -Parent) 'Stratus.ParentCommandBuildInputs.ps1' }
if (-not $HelperPath) { $HelperPath=Join-Path (Split-Path $PSScriptRoot -Parent) 'Stratus.ParentCommandRuntimeContract.ps1' }
# Execute only reviewed function-only pure libraries and literal byte fixtures.
# Synthetic executable/reference/metadata-reader bytes are deliberately NOT valid
# binaries. No compilation, target loading, filesystem fixtures or native work.
function Assert-BuildInputTest { param([bool] $Value,[string] $Message) if (-not $Value) { throw $Message } }
function Get-BuildInputTestProgram {
    param([string] $Text,[string[]] $Names,[string[]] $Commands)
    $tokens=$null;$errors=$null
    $ast=[Management.Automation.Language.Parser]::ParseInput($Text,[ref]$tokens,[ref]$errors)
    Assert-BuildInputTest (@($errors).Count -eq 0) 'Pure source parse error.'
    Assert-BuildInputTest ($null -eq $ast.ParamBlock -and $null -eq $ast.BeginBlock -and $null -eq $ast.ProcessBlock -and
        $null -eq $ast.DynamicParamBlock -and $ast.EndBlock.Statements.Count -eq $Names.Count) 'Pure top-level shape differs.'
    Assert-BuildInputTest (@($ast.UsingStatements).Count -eq 0) 'Pure using directives forbidden.'
    if (@($ast.PSObject.Properties.Name) -contains 'CleanBlock') { Assert-BuildInputTest ($null -eq $ast.CleanBlock) 'Pure clean block forbidden.' }
    $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($statement in $ast.EndBlock.Statements) {
        Assert-BuildInputTest ($statement -is [Management.Automation.Language.FunctionDefinitionAst] -and
            $statement.Name -cin $Names -and $seen.Add($statement.Name)) 'Unreviewed pure function/top-level statement.'
    }
    foreach ($command in $ast.FindAll({param($n) $n -is [Management.Automation.Language.CommandAst]},$true)) {
        Assert-BuildInputTest ($command.InvocationOperator -eq [Management.Automation.Language.TokenKind]::Unknown -and
            $command.GetCommandName() -cin $Commands) 'Unreviewed pure command.'
    }
    foreach ($type in $ast.FindAll({param($n) $n -is [Management.Automation.Language.TypeExpressionAst] -or $n -is [Management.Automation.Language.TypeConstraintAst]},$true)) {
        Assert-BuildInputTest ($type.TypeName.FullName -cin @('Array','BitConverter','bool','byte[]','char[]','Collections.Generic.HashSet[string]',
            'Collections.Generic.List[string]','Collections.Generic.List[object]','DateTimeOffset','Globalization.CultureInfo','Globalization.NumberStyles',
            'int','IO.Path','long','Management.Automation.PSMemberTypes','Management.Automation.PSAliasProperty','object','ordered','pscustomobject','ref',
            'regex','Security.Cryptography.SHA256','string','string[]','StringComparer','StringComparison','Text.StringBuilder','Text.UTF8Encoding',
            'TimeSpan','void','System.Management.Automation.PSCustomObject')) ('Unreviewed pure type: '+$type.TypeName.FullName)
    }
    foreach ($member in $ast.FindAll({param($n) $n -is [Management.Automation.Language.InvokeMemberExpressionAst]},$true)) {
        Assert-BuildInputTest ($member.Member -is [Management.Automation.Language.StringConstantExpressionAst] -and $member.Member.Value -cin @(
            'Add','Append','Compare','ComputeHash','Contains','Create','Dispose','EndsWith','Equals','GetBytes','GetFullPath','GetInvalidFileNameChars',
            'GetString','IndexOf','IndexOfAny','IsMatch','Join','new','ParseExact','Replace','Split','StartsWith','Substring','ToArray','ToCharArray',
            'ToLowerInvariant','ToString','TryParse')) 'Unreviewed pure member.'
    }
    foreach ($attribute in $ast.FindAll({param($n) $n -is [Management.Automation.Language.AttributeAst]},$true)) {
        Assert-BuildInputTest ($attribute.TypeName.FullName -cin @('CmdletBinding','Parameter','AllowNull','AllowEmptyCollection')) 'Unreviewed pure attribute.'
    }
    Assert-BuildInputTest (@($ast.FindAll({param($n) $n -is [Management.Automation.Language.RedirectionAst]},$true)).Count -eq 0) 'Pure redirection forbidden.'
    return $Text
}
$helperNames=@('Assert-StratusCommandRuntimePlainValue','Assert-StratusCommandRuntimeObject','Assert-StratusCommandRuntimeString','Assert-StratusCommandRuntimeHash',
    'Assert-StratusCommandRuntimeInteger','Assert-StratusCommandRuntimePath','Get-StratusCommandRuntimeUtc','Assert-StratusCommandRuntimeFile',
    'Assert-StratusCommandRuntimeEqual','Get-StratusCommandRuntimeHash','ConvertTo-StratusCommandRuntimeJsonString','ConvertTo-StratusCommandRuntimeJson',
    'Get-StratusCommandRuntimeContractBytes','Assert-StratusParentCommandRuntimeContract')
$libraryNames=@('Assert-StratusBuildInputDocument','Assert-StratusBuildInputArray','Get-StratusBuildManifestRuntimeSource',
    'Get-StratusParentCommandBuildPreflight','Get-StratusParentCommandBuildInputSpecifications','Assert-StratusParentCommandBuildInputs')
$utf8=[Text.UTF8Encoding]::new($false,$true)
$helperBytes=[IO.File]::ReadAllBytes([IO.Path]::GetFullPath($HelperPath));$helperSource=$utf8.GetString($helperBytes)
$libraryBytes=[IO.File]::ReadAllBytes([IO.Path]::GetFullPath($LibraryPath));$librarySource=$utf8.GetString($libraryBytes)
. ([scriptblock]::Create((Get-BuildInputTestProgram $helperSource $helperNames @($helperNames+'ForEach-Object'))))
. ([scriptblock]::Create((Get-BuildInputTestProgram $librarySource $libraryNames @($helperNames+$libraryNames))))
$results=[Collections.Generic.List[object]]::new()
function Case { param([string] $CaseLabel,[scriptblock] $Body) try { & $Body } catch { throw ('Build-input case '+$CaseLabel+' failed: '+$_.Exception.Message) }; $results.Add([pscustomobject]@{name=$CaseLabel;passed=$true}) }
function Reject { param([scriptblock] $Body) $caught=$false;try { & $Body | Out-Null } catch {$caught=$true};if (-not $caught) {throw 'Expected closed rejection.'} }
function New-BuildInputFile {
    param([string] $Path,[byte[]] $Bytes)
    return [pscustomobject][ordered]@{path=$Path;sha256=(Get-StratusCommandRuntimeHash $Bytes 134217728 'fixture');bytes=[long]$Bytes.Length}
}
function New-StratusBuildInputFixture {
    $encoding=[Text.UTF8Encoding]::new($false,$true);$root='C:\stratus';$worktree='C:\build-worktree';$id='c'*32
    $directory=$root+'\.stratus-cache\runtime-builds\'+$id+'.in-progress'
    $sourceBytes=$encoding.GetBytes('// literal fixture, never compiled')
    $source=New-BuildInputFile ($worktree+'\scripts\Stratus.ParentStage.Runtime.cs') $sourceBytes
    $manifestBytes=$encoding.GetBytes('scripts/Stratus.ParentStage.Runtime.cs'+"`t"+$source.bytes.ToString([Globalization.CultureInfo]::InvariantCulture)+"`t"+$source.sha256+"`n")
    $snapshots=[object[]]@(
        [pscustomobject][ordered]@{path='C:\tools\host.exe';bytes=$encoding.GetBytes('synthetic host')},
        [pscustomobject][ordered]@{path='C:\tools\csc.exe';bytes=$encoding.GetBytes('synthetic compiler')},
        [pscustomobject][ordered]@{path='C:\tools\producer.ps1';bytes=$encoding.GetBytes('synthetic producer')},
        [pscustomobject][ordered]@{path='C:\tools\metadata.dll';bytes=$encoding.GetBytes('synthetic metadata reader')},
        [pscustomobject][ordered]@{path='C:\references\mscorlib.dll';bytes=$encoding.GetBytes('synthetic ref1')},
        [pscustomobject][ordered]@{path='C:\references\System.dll';bytes=$encoding.GetBytes('synthetic ref2')},
        [pscustomobject][ordered]@{path='C:\tools\runtime.dll';bytes=$encoding.GetBytes('synthetic declared runtime')},
        [pscustomobject][ordered]@{path='C:\tools\csc.exe.config';bytes=$encoding.GetBytes('synthetic config')})
    $files=@(foreach($snapshot in $snapshots){New-BuildInputFile $snapshot.path $snapshot.bytes})
    $hostRecord=[pscustomobject][ordered]@{path=$files[0].path;sha256=$files[0].sha256;bytes=$files[0].bytes;ps_edition='Desktop';ps_version='5.1.26100.3624';clr_version='4.0.30319.42000'}
    $compiler=[pscustomobject][ordered]@{path=$files[1].path;sha256=$files[1].sha256;bytes=$files[1].bytes;version='synthetic compiler v1'}
    $dependencies=[object[]]@([pscustomobject][ordered]@{role='compiler_runtime';path=$files[6].path;sha256=$files[6].sha256;bytes=$files[6].bytes},
        [pscustomobject][ordered]@{role='compiler_config';path=$files[7].path;sha256=$files[7].sha256;bytes=$files[7].bytes})
    $profile=[pscustomobject][ordered]@{format='stratus-parent-command-build-profile-v2';profile_id='synthetic-unqualified-v2';host=$hostRecord;compiler=$compiler
        references=[object[]]@($files[4],$files[5]);target_framework='.NETFramework,Version=v4.8';architecture='x64';assembly_name='Stratus.ParentStage.Runtime'
        producer=$files[2];metadata_verifier=$files[3];dependencies=$dependencies;compiler_recipe='direct-csc-exe-v1'}
    $profileBytes=Get-StratusCommandRuntimeContractBytes $profile;$profileHash=Get-StratusCommandRuntimeHash $profileBytes 262144 'fixture profile'
    $manifestHash=Get-StratusCommandRuntimeHash $manifestBytes 4194304 'fixture manifest'
    $output=$directory+'\Stratus.ParentStage.Runtime.dll'
    $plan=[pscustomobject][ordered]@{format='stratus-parent-command-build-plan-v2';build_id=$id;revision=('a'*40);source_manifest_sha256=$manifestHash;profile_sha256=$profileHash
        build_worktree=$worktree;build_directory=$directory;source=$source;output=[pscustomobject][ordered]@{path=$output}
        compiler_arguments=[string[]]@('/target:library','/noconfig','/nostdlib+','/platform:x64',('/out:'+$output),('/reference:'+$files[4].path),('/reference:'+$files[5].path),$source.path)}
    $planBytes=Get-StratusCommandRuntimeContractBytes $plan
    $expected=[pscustomobject][ordered]@{root=$root;revision=$plan.revision;source_manifest_sha256=$manifestHash;profile_sha256=$profileHash;build_id=$id
        build_worktree=$worktree;build_directory=$directory;source=(New-BuildInputFile $source.path $sourceBytes)}
    return [pscustomobject]@{profile=[pscustomobject][ordered]@{path='C:\profiles\runtime-profile.json';sha256=$profileHash;value=$profile};profile_bytes=$profileBytes
        plan=[pscustomobject][ordered]@{path=$directory+'\command-runtime-plan.json';sha256=(Get-StratusCommandRuntimeHash $planBytes 262144 'fixture plan');value=$plan};plan_bytes=$planBytes
        source_manifest_bytes=$manifestBytes;source_bytes=$sourceBytes;snapshots=$snapshots;expected=$expected}
}
function Sync-BuildInputFixture {
    param([object] $Fixture,[switch] $ApproveProfile,[switch] $ApproveManifest)
    $Fixture.profile_bytes=Get-StratusCommandRuntimeContractBytes $Fixture.profile.value
    $Fixture.profile.sha256=Get-StratusCommandRuntimeHash $Fixture.profile_bytes 262144 'fixture profile'
    if ($ApproveProfile) {$Fixture.expected.profile_sha256=$Fixture.profile.sha256;$Fixture.plan.value.profile_sha256=$Fixture.profile.sha256}
    if ($ApproveManifest) {$h=Get-StratusCommandRuntimeHash $Fixture.source_manifest_bytes 4194304 'fixture manifest';$Fixture.expected.source_manifest_sha256=$h;$Fixture.plan.value.source_manifest_sha256=$h}
    $Fixture.plan_bytes=Get-StratusCommandRuntimeContractBytes $Fixture.plan.value
    $Fixture.plan.sha256=Get-StratusCommandRuntimeHash $Fixture.plan_bytes 262144 'fixture plan'
}
function Invoke-BuildInputFixture {
    param([object] $Fixture)
    return Assert-StratusParentCommandBuildInputs -ProfileDocument $Fixture.profile -ProfileBytes $Fixture.profile_bytes -PlanDocument $Fixture.plan -PlanBytes $Fixture.plan_bytes `
        -SourceManifestBytes $Fixture.source_manifest_bytes -SourceBytes $Fixture.source_bytes -InputSnapshots $Fixture.snapshots -Expected $Fixture.expected
}
function Invoke-BuildPreflightFixture {
    param([object] $Fixture)
    return Get-StratusParentCommandBuildPreflight -ProfileDocument $Fixture.profile -ProfileBytes $Fixture.profile_bytes `
        -PlanDocument $Fixture.plan -PlanBytes $Fixture.plan_bytes -SourceManifestBytes $Fixture.source_manifest_bytes -Expected $Fixture.expected
}
function Invoke-BuildSpecificationsFixture {
    param([object] $Fixture,[object] $ManifestPath='C:\manifests\source-manifest.tsv')
    return ,(Get-StratusParentCommandBuildInputSpecifications -ProfileDocument $Fixture.profile -ProfileBytes $Fixture.profile_bytes `
        -PlanDocument $Fixture.plan -PlanBytes $Fixture.plan_bytes -SourceManifestBytes $Fixture.source_manifest_bytes `
        -Expected $Fixture.expected -SourceManifestPath $ManifestPath)
}
function Assert-NonAuthorizing {
    param([object] $Value)
    foreach($name in @('profile_approval_origin_verified','candidate_checkout_cleanliness_verified','actual_dependency_resolution_verified','fresh_build_directory_verified',
        'held_handle_provenance_verified','compiler_execution_verified','build_causality_verified','assembly_metadata_verified','clr_compatibility_verified',
        'same_held_bytes_import_verified','import_permitted','native_qualified','production_start_permitted','live_permitted','binance_kill_switch_removal_permitted','full_goal_accepted')) {
        Assert-BuildInputTest ($Value.$name -is [bool] -and $Value.$name -eq $false) ('Unexpected authority '+$name)
    }
}
function New-BuildInputPoison {
    param([object] $Value,[string] $MemberType,[string] $MemberName)
    # Retain the actual ETS wrapper, including for scalar strings. Remove only
    # this array wrapper's type names to replace PS5's system Count alias.
    $wrapped=[psobject]::AsPSObject($Value)
    if ($MemberName -ceq 'Count' -and $wrapped -is [Array]) { $wrapped.PSObject.TypeNames.Clear() }
    Add-Member -InputObject $wrapped -MemberType $MemberType -Name $MemberName -Value {$script:poison++;throw 'POISON'} -Force
    Assert-BuildInputTest ($wrapped.PSObject.Members[$MemberName].MemberType.ToString() -ceq $MemberType) 'Poison wrapper fixture lost its member.'
    return ,$wrapped
}
Case 'synthetic input byte closure is nonauthorizing' { $f=New-StratusBuildInputFixture;$v=Invoke-BuildInputFixture $f;Assert-NonAuthorizing $v;Assert-BuildInputTest ($v.declared_input_count -eq 8 -and $v.source_sha256 -ceq $f.expected.source.sha256) 'Wrong projection.' }
Case 'declared Core host does not claim compatibility' {$f=New-StratusBuildInputFixture;$f.profile.value.host.ps_edition='Core';$f.profile.value.host.ps_version='7.6.5';$f.profile.value.host.clr_version='10.0.0';Sync-BuildInputFixture $f -ApproveProfile;Assert-NonAuthorizing (Invoke-BuildInputFixture $f)}
Case 'source manifest permits separately bound sorted positive source entry' {$f=New-StratusBuildInputFixture;$prefix='README.md'+"`t1`t"+(Get-StratusCommandRuntimeHash ([byte[]]@(120)) 1 'fixture other source')+"`n";$f.source_manifest_bytes=$utf8.GetBytes($prefix+$utf8.GetString($f.source_manifest_bytes));Sync-BuildInputFixture $f -ApproveManifest;Assert-NonAuthorizing (Invoke-BuildInputFixture $f)}
foreach($role in @('producer_runtime','producer_dependency','producer_config','verifier_runtime','verifier_dependency','verifier_config')) {
    Case ('explicit dependency role remains nonauthorizing '+$role) {$f=New-StratusBuildInputFixture;$f.profile.value.dependencies[0].role=$role;Sync-BuildInputFixture $f -ApproveProfile;Assert-NonAuthorizing (Invoke-BuildInputFixture $f)}
}
foreach($name in @('profile_bytes','plan_bytes','source_manifest_bytes','source_bytes')) {
    Case ('actual bytes reject mutation '+$name) { $f=New-StratusBuildInputFixture;$f.$name[0]=$f.$name[0] -bxor 1;Reject {Invoke-BuildInputFixture $f} }
    Case ('null bytes reject '+$name) { $f=New-StratusBuildInputFixture;$f.$name=$null;Reject {Invoke-BuildInputFixture $f} }
    Case ('nonbyte coercion rejects '+$name) { $f=New-StratusBuildInputFixture;$f.$name=[int[]]@(1,2);Reject {Invoke-BuildInputFixture $f} }
}
foreach($index in 0..7) {
    Case ('each declared snapshot byte is checked '+$index) {$f=New-StratusBuildInputFixture;$f.snapshots[$index].bytes[0]=$f.snapshots[$index].bytes[0] -bxor 1;Reject {Invoke-BuildInputFixture $f}}
    Case ('each declared snapshot length is checked '+$index) {$f=New-StratusBuildInputFixture;$f.snapshots[$index].bytes=[byte[]]@(1);Reject {Invoke-BuildInputFixture $f}}
}
foreach($name in @('profile_sha256','revision','source_manifest_sha256','build_id')) {
    Case ('independent anchor cannot be replaced '+$name) {$f=New-StratusBuildInputFixture;$f.expected.$name='0'*$f.expected.$name.Length;Reject {Invoke-BuildInputFixture $f}}
}
Case 'missing snapshot rejects' {$f=New-StratusBuildInputFixture;$f.snapshots=$f.snapshots[0..6];Reject {Invoke-BuildInputFixture $f}}
Case 'extra snapshot rejects' {$f=New-StratusBuildInputFixture;$f.snapshots+=,$f.snapshots[0];Reject {Invoke-BuildInputFixture $f}}
Case 'snapshot order rejects' {$f=New-StratusBuildInputFixture;$t=$f.snapshots[0];$f.snapshots[0]=$f.snapshots[1];$f.snapshots[1]=$t;Reject {Invoke-BuildInputFixture $f}}
Case 'snapshot case alias rejects' {$f=New-StratusBuildInputFixture;$f.snapshots[0].path=$f.snapshots[0].path.ToUpperInvariant();Reject {Invoke-BuildInputFixture $f}}
Case 'duplicate declared roles path rejects' {$f=New-StratusBuildInputFixture;$f.profile.value.dependencies[1].path=$f.profile.value.dependencies[0].path;$f.snapshots[7].path=$f.snapshots[6].path;Sync-BuildInputFixture $f -ApproveProfile;Reject {Invoke-BuildInputFixture $f}}
Case 'missing dependency inventory rejects' {$f=New-StratusBuildInputFixture;$f.profile.value.dependencies=[object[]]@();Sync-BuildInputFixture $f -ApproveProfile;Reject {Invoke-BuildInputFixture $f}}
Case 'unknown dependency role rejects' {$f=New-StratusBuildInputFixture;$f.profile.value.dependencies[0].role='discovered';Sync-BuildInputFixture $f -ApproveProfile;Reject {Invoke-BuildInputFixture $f}}
foreach($key in @('format','compiler_recipe','architecture','assembly_name','target_framework')) {
    Case ('invalid profile identity '+$key) {$f=New-StratusBuildInputFixture;$f.profile.value.$key='invalid';Sync-BuildInputFixture $f -ApproveProfile;Reject {Invoke-BuildInputFixture $f}}
}
Case 'profile copied into receipt does not self-approve' {$f=New-StratusBuildInputFixture;$f.profile.value.profile_id='another-profile';Sync-BuildInputFixture $f;Reject {Invoke-BuildInputFixture $f}}
Case 'hosted compiler is not direct executable recipe' {$f=New-StratusBuildInputFixture;$f.profile.value.compiler.path='C:\tools\csc.dll';$f.snapshots[1].path='C:\tools\csc.dll';Sync-BuildInputFixture $f -ApproveProfile;Reject {Invoke-BuildInputFixture $f}}
Case 'declared executable size bounded before snapshot hash' {$f=New-StratusBuildInputFixture;$f.profile.value.compiler.bytes=134217729;Sync-BuildInputFixture $f -ApproveProfile;Reject {Invoke-BuildInputFixture $f}}
Case 'producer size bound remains distinct' {$f=New-StratusBuildInputFixture;$f.profile.value.producer.bytes=16777217;Sync-BuildInputFixture $f -ApproveProfile;Reject {Invoke-BuildInputFixture $f}}
foreach($replacement in @('C:\stratus','C:\stratus\build','C:\')) {
    Case ('worktree equality or nested ambiguity rejects '+$replacement) {$f=New-StratusBuildInputFixture;$f.expected.build_worktree=$replacement;Reject {Invoke-BuildInputFixture $f}}
}
Case 'unfixed private output directory rejects' {$f=New-StratusBuildInputFixture;$f.expected.build_directory='C:\other';Reject {Invoke-BuildInputFixture $f}}
Case 'final accepted output is not actual build output' {$f=New-StratusBuildInputFixture;$f.plan.value.output.path='C:\stratus\.stratus-cache\release-transitions\r1-'+('a'*40)+'.accepted\Stratus.ParentStage.Runtime.dll';Sync-BuildInputFixture $f;Reject {Invoke-BuildInputFixture $f}}
Case 'plan record must live in actual build directory' {$f=New-StratusBuildInputFixture;$f.plan.path='C:\other\command-runtime-plan.json';Reject {Invoke-BuildInputFixture $f}}
Case 'production source cannot substitute detached source' {$f=New-StratusBuildInputFixture;$f.expected.source.path='C:\stratus\scripts\Stratus.ParentStage.Runtime.cs';Reject {Invoke-BuildInputFixture $f}}
Case 'manifest exact source hash beats Expected copy' {$f=New-StratusBuildInputFixture;$f.expected.source.sha256='0'*64;$f.plan.value.source.sha256='0'*64;Sync-BuildInputFixture $f;Reject {Invoke-BuildInputFixture $f}}
Case 'manifest source length beats Expected copy' {$f=New-StratusBuildInputFixture;$f.expected.source.bytes++;$f.plan.value.source.bytes++;Sync-BuildInputFixture $f;Reject {Invoke-BuildInputFixture $f}}
foreach($mode in @('bom','cr','missinglf','emptyline','missingruntime','backslash','traversal','leadingzero','zero','negative','overflow','unsorted','casealias')) {
    Case ('strict manifest rejects '+$mode) {$f=New-StratusBuildInputFixture;$text=$utf8.GetString($f.source_manifest_bytes)
        switch($mode) {
            'bom' {$text=[string][char]65279+$text} 'cr' {$text=$text.Replace("`n","`r`n")} 'missinglf' {$text=$text.TrimEnd([char]10)}
            'emptyline' {$text+="`n"} 'missingruntime' {$text=$text.Replace('Stratus.ParentStage.Runtime.cs','Other.cs')}
            'backslash' {$text=$text.Replace('scripts/','scripts\')} 'traversal' {$text=$text.Replace('scripts/','scripts/../scripts/')}
            'leadingzero' {$text=$text.Replace("`t"+$f.expected.source.bytes+"`t","`t0"+$f.expected.source.bytes+"`t")}
            'zero' {$text='README.md'+"`t0`t"+('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')+"`n"+$text}
            'negative' {$text=$text.Replace("`t"+$f.expected.source.bytes+"`t","`t-1`t")}
            'overflow' {$text=$text.Replace("`t"+$f.expected.source.bytes+"`t","`t9223372036854775808`t")}
            'unsorted' {$text+=('README.md'+"`t1`t"+('0'*64)+"`n")}
            'casealias' {$text=$text.Replace('scripts/','SCRIPTS/')+$text}
        };$f.source_manifest_bytes=$utf8.GetBytes($text);Sync-BuildInputFixture $f -ApproveManifest;Reject {Invoke-BuildInputFixture $f}}
}
foreach($relative in @('README file.md','READMEé.md')) {
    Case ('closed R1 manifest rejects otherwise canonical path '+$relative) {$f=New-StratusBuildInputFixture;$f.source_manifest_bytes=$utf8.GetBytes($relative+"`t1`t"+('0'*64)+"`n"+$utf8.GetString($f.source_manifest_bytes));Sync-BuildInputFixture $f -ApproveManifest;Reject {Invoke-BuildInputFixture $f}}
}
foreach($mode in @('missing','extra','swap','response','implicitstdlib','sourcecopy','outputcopy')) {
    Case ('exact compiler argv rejects '+$mode) {$f=New-StratusBuildInputFixture
        switch($mode) {
            'missing' {$f.plan.value.compiler_arguments=$f.plan.value.compiler_arguments[1..7]}
            'extra' {$f.plan.value.compiler_arguments+= '/deterministic+'}
            'swap' {$t=$f.plan.value.compiler_arguments[0];$f.plan.value.compiler_arguments[0]=$f.plan.value.compiler_arguments[1];$f.plan.value.compiler_arguments[1]=$t}
            'response' {$f.plan.value.compiler_arguments[0]='@C:\args.rsp'}
            'implicitstdlib' {$f.plan.value.compiler_arguments[2]='/nostdlib-'}
            'sourcecopy' {$f.plan.value.compiler_arguments[7]='C:\else\runtime.cs'}
            'outputcopy' {$f.plan.value.compiler_arguments[4]='/out:C:\else\runtime.dll'}
        };Sync-BuildInputFixture $f;Reject {Invoke-BuildInputFixture $f}}
}
Case 'document value cannot differ from actual wire' {$f=New-StratusBuildInputFixture;$f.profile.value.profile_id='mutated';Reject {Invoke-BuildInputFixture $f}}
Case 'trailing profile bytes with rebound digest rejects canonicality' {$f=New-StratusBuildInputFixture;$f.profile_bytes=[byte[]]($f.profile_bytes+[byte]32);$h=Get-StratusCommandRuntimeHash $f.profile_bytes 262144 'fixture';$f.profile.sha256=$h;$f.expected.profile_sha256=$h;Reject {Invoke-BuildInputFixture $f}}
Case 'plan document path cannot alias an input' {$f=New-StratusBuildInputFixture;$f.profile.value.producer.path=$f.plan.path;$f.snapshots[2].path=$f.plan.path;Sync-BuildInputFixture $f -ApproveProfile;Reject {Invoke-BuildInputFixture $f}}
Case 'script property never executes' {$f=New-StratusBuildInputFixture;$script:poison=0;$f.expected|Add-Member ScriptProperty bad {$script:poison++;throw 'POISON'};Reject {Invoke-BuildInputFixture $f};Assert-BuildInputTest ($script:poison -eq 0) 'Getter ran.'}
Case 'wrapped source byte Length never executes' {$f=New-StratusBuildInputFixture;$script:poison=0;$f.source_bytes=New-BuildInputPoison $f.source_bytes ScriptProperty Length;Reject {Invoke-BuildInputFixture $f};Assert-BuildInputTest ($script:poison -eq 0) 'Getter ran.'}
Case 'wrapped expected path method never executes' {$f=New-StratusBuildInputFixture;$script:poison=0;$f.expected.root=New-BuildInputPoison $f.expected.root ScriptMethod StartsWith;Reject {Invoke-BuildInputFixture $f};Assert-BuildInputTest ($script:poison -eq 0) 'Method ran.'}
Case 'wrapped snapshot array Count never executes' {$f=New-StratusBuildInputFixture;$script:poison=0;$f.snapshots=New-BuildInputPoison $f.snapshots ScriptProperty Count;Reject {Invoke-BuildInputFixture $f};Assert-BuildInputTest ($script:poison -eq 0) 'Getter ran.'}
Case 'unsafe source command is rejected before load' {Reject {Get-BuildInputTestProgram ($librarySource+"`nGet-Process") $libraryNames @($helperNames+$libraryNames)}}
Case 'unsafe source type is rejected before load' {Reject {Get-BuildInputTestProgram ($librarySource.Replace('[Text.UTF8Encoding]','[IO.File]')) $libraryNames @($helperNames+$libraryNames)}}
Case 'unsafe dynamic call is rejected before load' {Reject {Get-BuildInputTestProgram ($librarySource.Replace('Assert-StratusCommandRuntimeHash $ExpectedHash','& Assert-StratusCommandRuntimeHash $ExpectedHash')) $libraryNames @($helperNames+$libraryNames)}}
Case 'using namespace is rejected before load' {Reject {Get-BuildInputTestProgram ("using namespace System`n"+$librarySource) $libraryNames @($helperNames+$libraryNames)}}
Case 'clean block is rejected before load' {Reject {Get-BuildInputTestProgram ("clean {}`n"+$librarySource) $libraryNames @($helperNames+$libraryNames)}}
Assert-BuildInputTest ($results.Count -eq 100) 'The original full-byte and ETS case inventory changed.'
# The new entry points exercise only declared metadata before Open. They neither
# read executable/source files nor establish actual byte/lease/approval facts.
Case 'preflight needs no payload bytes and exposes only fresh metadata' {
    $f=New-StratusBuildInputFixture;$f.source_bytes=$null;$f.snapshots=$null;$v=Invoke-BuildPreflightFixture $f
    Assert-StratusCommandRuntimeObject $v @('profile_id','profile_path','profile_sha256','profile_bytes','plan_path','plan_sha256','plan_bytes',
        'revision','build_id','source_manifest_sha256','source_manifest_bytes','source','build_worktree','build_directory','output_path',
        'declared_inputs','declared_input_count','declared_input_bytes') 'preflight metadata only'
    Assert-BuildInputTest ($v.declared_input_count -eq 8 -and $v.source.sha256 -ceq $f.expected.source.sha256) 'Preflight metadata differs.'
}
Case 'specifications derive exact ordered paths hashes lengths and role bounds' {
    $f=New-StratusBuildInputFixture;$specs=Invoke-BuildSpecificationsFixture $f
    $paths=@($f.profile.path,$f.plan.path,'C:\manifests\source-manifest.tsv',$f.expected.source.path)+@($f.snapshots | ForEach-Object {$_.path})
    $hashes=@($f.profile.sha256,$f.plan.sha256,$f.expected.source_manifest_sha256,$f.expected.source.sha256)+@(foreach($s in $f.snapshots){Get-StratusCommandRuntimeHash $s.bytes 134217728 'expected input'})
    $lengths=@($f.profile_bytes.LongLength,$f.plan_bytes.LongLength,$f.source_manifest_bytes.LongLength,$f.source_bytes.LongLength)+@($f.snapshots | ForEach-Object {$_.bytes.LongLength})
    $maxima=@(262144,262144,4194304,2097152,134217728,134217728,16777216,16777216,134217728,134217728,134217728,134217728)
    Assert-BuildInputTest ($specs -is [object[]] -and $specs.Count -eq 12) 'Derived array shape differs.'
    for($i=0;$i -lt 12;$i++) {
        Assert-StratusCommandRuntimeObject $specs[$i] @('path','sha256','bytes','maximum_bytes') 'derived specification'
        Assert-BuildInputTest ($specs[$i].path -ceq $paths[$i] -and $specs[$i].sha256 -ceq $hashes[$i] -and
            $specs[$i].bytes -eq $lengths[$i] -and $specs[$i].maximum_bytes -eq $maxima[$i]) ('Derived role differs '+$i)
    }
}
Case 'preflight records do not alias supplied host records' {
    $f=New-StratusBuildInputFixture;$v=Invoke-BuildPreflightFixture $f;$original=$f.profile.value.host.path
    $v.declared_inputs[0].path='C:\mutated.exe';Assert-BuildInputTest ($f.profile.value.host.path -ceq $original) 'Mutable caller record escaped.'
    $f.profile.value.compiler.path='C:\changed.exe';Assert-BuildInputTest ($v.declared_inputs[1].path -ceq 'C:\tools\csc.exe') 'Projection retained caller record.'
}
Case 'preflight source is a fresh manifest-derived record' {
    $f=New-StratusBuildInputFixture;$v=Invoke-BuildPreflightFixture $f;$original=$f.expected.source.sha256
    $v.source.sha256='0'*64;Assert-BuildInputTest ($f.expected.source.sha256 -ceq $original -and $f.plan.value.source.sha256 -ceq $original) 'Source projection aliases input.'
}
Case 'specification records are fresh across repeated derivations' {
    $f=New-StratusBuildInputFixture;$a=Invoke-BuildSpecificationsFixture $f;$a[4].sha256='0'*64;$a[6].maximum_bytes=1
    $b=Invoke-BuildSpecificationsFixture $f
    Assert-BuildInputTest ($b[4].sha256 -ceq $f.profile.value.host.sha256 -and $b[6].maximum_bytes -eq 16777216) 'Derived records retained a mutable result.'
}
foreach($name in @('profile_bytes','plan_bytes','source_manifest_bytes')) {
    Case ('preflight and specifications reject changed raw metadata '+$name) {$f=New-StratusBuildInputFixture;$f.$name[0]=$f.$name[0] -bxor 1;Reject {Invoke-BuildPreflightFixture $f};Reject {Invoke-BuildSpecificationsFixture $f}}
    Case ('preflight and specifications reject null metadata '+$name) {$f=New-StratusBuildInputFixture;$f.$name=$null;Reject {Invoke-BuildPreflightFixture $f};Reject {Invoke-BuildSpecificationsFixture $f}}
    Case ('preflight and specifications reject coerced metadata '+$name) {$f=New-StratusBuildInputFixture;$f.$name=[int[]]@(1,2);Reject {Invoke-BuildPreflightFixture $f};Reject {Invoke-BuildSpecificationsFixture $f}}
}
foreach($kind in @('profile','plan')) {
    Case ('preflight document value must match raw bytes '+$kind) {$f=New-StratusBuildInputFixture;if($kind -ceq 'profile'){$f.profile.value.profile_id='different'}else{$f.plan.value.build_id='0'*32};Reject {Invoke-BuildPreflightFixture $f};Reject {Invoke-BuildSpecificationsFixture $f}}
    Case ('preflight rejects repinned noncanonical document '+$kind) {
        $f=New-StratusBuildInputFixture;$rawName=$kind+'_bytes';$f.$rawName=[byte[]]($f.$rawName+[byte]32)
        $f.$kind.sha256=Get-StratusCommandRuntimeHash $f.$rawName 262144 'repinned fixture'
        if($kind -ceq 'profile'){$f.expected.profile_sha256=$f.profile.sha256;$f.plan.value.profile_sha256=$f.profile.sha256;$f.plan_bytes=Get-StratusCommandRuntimeContractBytes $f.plan.value;$f.plan.sha256=Get-StratusCommandRuntimeHash $f.plan_bytes 262144 'plan'}
        Reject {Invoke-BuildPreflightFixture $f};Reject {Invoke-BuildSpecificationsFixture $f}
    }
}
Case 'preflight cannot replace the independently supplied approval digest' {$f=New-StratusBuildInputFixture;$f.expected.profile_sha256='0'*64;Reject {Invoke-BuildPreflightFixture $f};Reject {Invoke-BuildSpecificationsFixture $f}}
foreach($index in 0..11) {
    Case ('manifest cannot alias any derived input or output '+$index) {
        $f=New-StratusBuildInputFixture;$aliases=@($f.profile.path,$f.plan.path,$f.expected.source.path,$f.plan.value.output.path)+@($f.snapshots | ForEach-Object {$_.path})
        Reject {Invoke-BuildSpecificationsFixture $f $aliases[$index]}
    }
}
Case 'manifest alias comparison is Windows case insensitive' {$f=New-StratusBuildInputFixture;Reject {Invoke-BuildSpecificationsFixture $f $f.profile.path.ToUpperInvariant()}}
Case 'manifest source path must be canonical before derivation' {$f=New-StratusBuildInputFixture;Reject {Invoke-BuildSpecificationsFixture $f 'C:\manifests\..\manifest.tsv'}}
Case 'preflight manifest still rejects copied Expected source hash' {$f=New-StratusBuildInputFixture;$f.expected.source.sha256='0'*64;$f.plan.value.source.sha256='0'*64;Sync-BuildInputFixture $f;Reject {Invoke-BuildPreflightFixture $f};Reject {Invoke-BuildSpecificationsFixture $f}}
Case 'declared metadata aggregate accepts exact 256 MiB without allocating payload' {
    $f=New-StratusBuildInputFixture;$v=Invoke-BuildPreflightFixture $f
    $rest=$v.declared_input_bytes-$f.profile.value.host.bytes-$f.profile.value.compiler.bytes
    $f.profile.value.host.bytes=[long]134217728;$f.profile.value.compiler.bytes=[long](268435456-134217728-$rest)
    Sync-BuildInputFixture $f -ApproveProfile;$v=Invoke-BuildPreflightFixture $f;$specs=Invoke-BuildSpecificationsFixture $f
    Assert-BuildInputTest ($v.declared_input_bytes -eq 268435456 -and $specs.Count -eq 12) 'Exact metadata bound differs.'
    Reject {Invoke-BuildInputFixture $f}
}
Case 'declared metadata aggregate rejects over 256 MiB before payload hashing' {
    $f=New-StratusBuildInputFixture;$f.profile.value.host.bytes=[long]134217728;$f.profile.value.compiler.bytes=[long]134217728
    Sync-BuildInputFixture $f -ApproveProfile;Reject {Invoke-BuildPreflightFixture $f};Reject {Invoke-BuildSpecificationsFixture $f}
}
foreach($role in @('producer','metadata_verifier')) {
    Case ('preflight retains distinct 16 MiB role bound '+$role) {$f=New-StratusBuildInputFixture;$f.profile.value.$role.bytes=[long]16777217;Sync-BuildInputFixture $f -ApproveProfile;Reject {Invoke-BuildPreflightFixture $f};Reject {Invoke-BuildSpecificationsFixture $f}}
}
Case 'successful preflight cannot substitute for source bytes' {$f=New-StratusBuildInputFixture;$null=Invoke-BuildPreflightFixture $f;$f.source_bytes[0]=$f.source_bytes[0] -bxor 1;Reject {Invoke-BuildInputFixture $f}}
Case 'successful specifications cannot substitute for actual snapshots' {$f=New-StratusBuildInputFixture;$null=Invoke-BuildSpecificationsFixture $f;$f.snapshots[0].bytes[0]=$f.snapshots[0].bytes[0] -bxor 1;Reject {Invoke-BuildInputFixture $f}}
Case 'preflight Expected script getter never executes' {$f=New-StratusBuildInputFixture;$script:poison=0;$f.expected|Add-Member ScriptProperty injected {$script:poison++;throw 'POISON'};Reject {Invoke-BuildPreflightFixture $f};Reject {Invoke-BuildSpecificationsFixture $f};Assert-BuildInputTest ($script:poison -eq 0) 'Getter ran.'}
foreach($name in @('profile_bytes','plan_bytes','source_manifest_bytes')) {
    Case ('preflight wrapped metadata Length never executes '+$name) {$f=New-StratusBuildInputFixture;$script:poison=0;$f.$name=New-BuildInputPoison $f.$name ScriptProperty Length;Reject {Invoke-BuildPreflightFixture $f};Reject {Invoke-BuildSpecificationsFixture $f};Assert-BuildInputTest ($script:poison -eq 0) 'Getter ran.'}
}
Case 'preflight wrapped expected path method never executes' {$f=New-StratusBuildInputFixture;$script:poison=0;$f.expected.root=New-BuildInputPoison $f.expected.root ScriptMethod StartsWith;Reject {Invoke-BuildPreflightFixture $f};Reject {Invoke-BuildSpecificationsFixture $f};Assert-BuildInputTest ($script:poison -eq 0) 'Method ran.'}
Case 'specification manifest path method never executes' {$f=New-StratusBuildInputFixture;$script:poison=0;$path=New-BuildInputPoison 'C:\manifests\source.tsv' ScriptMethod IndexOfAny;Reject {Invoke-BuildSpecificationsFixture $f $path};Assert-BuildInputTest ($script:poison -eq 0) 'Method ran.'}
Case 'unreviewed function attribute rejects before load' {Reject {Get-BuildInputTestProgram ($librarySource.Replace('[CmdletBinding()]','[Obsolete()]')) $libraryNames @($helperNames+$libraryNames)}}
Case 'unreviewed member invocation rejects before load' {Reject {Get-BuildInputTestProgram ($librarySource.Replace('.ToArray()','.ReadAllText()')) $libraryNames @($helperNames+$libraryNames)}}
$expectedCases=148
Assert-BuildInputTest ($results.Count -eq $expectedCases) ('Fixed case count differs: '+$results.Count)
[pscustomobject]@{format='stratus-parent-command-build-input-tests-v2';expected=$expectedCases;executed=$results.Count;passed=$results.Count;failed=0
    ps_version=$PSVersionTable.PSVersion.ToString();ps_edition=$PSVersionTable.PSEdition;library_sha256=(Get-StratusCommandRuntimeHash $libraryBytes 1048576 'library source')
    helpers_sha256=(Get-StratusCommandRuntimeHash $helperBytes 1048576 'helper source');source_and_pure_byte_fixtures_only=$true;native_executed=$false
    compiler_executed=$false;assembly_loaded=$false;filesystem_fixtures_written=$false;approval_origin_verified=$false;full_protocol_accepted=$false;results=@($results)}|ConvertTo-Json -Depth 5 -Compress
