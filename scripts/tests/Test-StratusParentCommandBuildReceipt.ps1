[CmdletBinding()]
param([string] $LibraryPath)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if (-not $LibraryPath) { $LibraryPath=Join-Path (Split-Path $PSScriptRoot -Parent) 'Stratus.ParentCommandBuildReceipt.ps1' }
# All fixtures below are literal in-memory data. "DLL" bytes are intentionally
# nonloadable. Reviewed closed pure function bodies only; no subject compilation,
# PE reader, import, producer, filesystem fixture or process/pipe/Job experiment.
$helperNames=@('Assert-StratusCommandRuntimePlainValue','Assert-StratusCommandRuntimeObject','Assert-StratusCommandRuntimeString',
    'Assert-StratusCommandRuntimeHash','Assert-StratusCommandRuntimeInteger','Assert-StratusCommandRuntimePath','Get-StratusCommandRuntimeUtc',
    'Assert-StratusCommandRuntimeFile','Assert-StratusCommandRuntimeEqual','Get-StratusCommandRuntimeHash','ConvertTo-StratusCommandRuntimeJsonString',
    'ConvertTo-StratusCommandRuntimeJson','Get-StratusCommandRuntimeContractBytes','Assert-StratusParentCommandRuntimeContract')
$inputNames=@('Assert-StratusBuildInputDocument','Assert-StratusBuildInputArray','Get-StratusBuildManifestRuntimeSource',
    'Get-StratusParentCommandBuildPreflight','Get-StratusParentCommandBuildInputSpecifications','Assert-StratusParentCommandBuildInputs')
$receiptNames=@('Get-StratusCommandBuildStreamHash','Assert-StratusParentCommandBuildReceipt')
$allNames=$helperNames+$inputNames+$receiptNames
function Assert-ReceiptTest { param([bool] $Okay,[string] $Message) if (-not $Okay) { throw $Message } }
function Get-ReceiptProgram {
    param([string] $Text,[string[]] $Names)
    $tokens=$null;$errors=$null
    $ast=[Management.Automation.Language.Parser]::ParseInput($Text,[ref]$tokens,[ref]$errors)
    Assert-ReceiptTest (@($errors).Count -eq 0) 'Pure build receipt program parse differs.'
    Assert-ReceiptTest ($null -eq $ast.ParamBlock -and $null -eq $ast.BeginBlock -and $null -eq $ast.ProcessBlock -and
        $null -eq $ast.DynamicParamBlock -and @($ast.UsingStatements).Count -eq 0 -and
        $ast.EndBlock.Statements.Count -eq $Names.Count) 'Unreviewed pure top-level statement count or block.'
    $cleanProperty=$ast.PSObject.Properties['CleanBlock']
    Assert-ReceiptTest ($null -eq $cleanProperty -or $null -eq $cleanProperty.Value) 'Unreviewed top-level clean block.'
    $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($statement in $ast.EndBlock.Statements) {
        Assert-ReceiptTest ($statement -is [Management.Automation.Language.FunctionDefinitionAst] -and $statement.Name -cin $Names -and $seen.Add($statement.Name)) 'Unreviewed pure function.'
    }
    foreach ($command in $ast.FindAll({param($n) $n -is [Management.Automation.Language.CommandAst]},$true)) {
        Assert-ReceiptTest ($command.InvocationOperator -eq [Management.Automation.Language.TokenKind]::Unknown -and
            ($command.GetCommandName() -cin $allNames -or $command.GetCommandName() -ceq 'ForEach-Object')) 'Unreviewed pure command.'
    }
    foreach ($type in $ast.FindAll({param($n) $n -is [Management.Automation.Language.TypeExpressionAst] -or $n -is [Management.Automation.Language.TypeConstraintAst]},$true)) {
        Assert-ReceiptTest ($type.TypeName.FullName -cin @('Array','BitConverter','bool','byte[]','char[]','Collections.Generic.HashSet[string]',
            'Collections.Generic.List[string]','Collections.Generic.List[object]','DateTimeOffset','Globalization.CultureInfo','Globalization.NumberStyles',
            'int','IO.Path','long','Management.Automation.PSMemberTypes','Management.Automation.PSAliasProperty','object','ordered','pscustomobject','ref','regex',
            'Security.Cryptography.SHA256','string','string[]','StringComparer','StringComparison','Text.StringBuilder','Text.UTF8Encoding','TimeSpan','void',
            'System.Management.Automation.PSCustomObject')) 'Unreviewed pure type.'
    }
    foreach ($attribute in $ast.FindAll({param($n) $n -is [Management.Automation.Language.AttributeAst]},$true)) {
        Assert-ReceiptTest ($attribute.TypeName.FullName -cin @('CmdletBinding','Parameter','AllowNull','AllowEmptyCollection')) 'Unreviewed function attribute.'
    }
    foreach ($member in $ast.FindAll({param($n) $n -is [Management.Automation.Language.InvokeMemberExpressionAst]},$true)) {
        Assert-ReceiptTest ($member.Member -is [Management.Automation.Language.StringConstantExpressionAst] -and $member.Member.Value -cin @(
            'Add','Append','Compare','ComputeHash','Contains','Create','Dispose','EndsWith','Equals','GetBytes','GetString','GetFullPath','GetInvalidFileNameChars',
            'IndexOf','IndexOfAny','IsMatch','Join','new','ParseExact','Replace','Split','StartsWith','Substring','ToArray','ToCharArray','ToLowerInvariant','ToString','TryParse')) 'Unreviewed pure member.'
    }
    Assert-ReceiptTest (@($ast.FindAll({param($n) $n -is [Management.Automation.Language.RedirectionAst]},$true)).Count -eq 0) 'Pure redirection forbidden.'
    return $Text
}
$helperPath=Join-Path (Split-Path $PSScriptRoot -Parent) 'Stratus.ParentCommandRuntimeContract.ps1'
$inputsPath=Join-Path (Split-Path $PSScriptRoot -Parent) 'Stratus.ParentCommandBuildInputs.ps1'
$helperSource=Get-Content -LiteralPath $helperPath -Raw
$inputsSource=Get-Content -LiteralPath $inputsPath -Raw
$receiptSource=Get-Content -LiteralPath $LibraryPath -Raw
. ([scriptblock]::Create((Get-ReceiptProgram $helperSource $helperNames)))
. ([scriptblock]::Create((Get-ReceiptProgram $inputsSource $inputNames)))
. ([scriptblock]::Create((Get-ReceiptProgram $receiptSource $receiptNames)))
$results=[Collections.Generic.List[object]]::new()
function Case { param([string] $Name,[scriptblock] $Body) try { & $Body } catch { throw ('Receipt case '+$Name+' failed: '+$_.Exception.Message) };$results.Add([pscustomobject]@{name=$Name;passed=$true}) }
function Reject { param([scriptblock] $Body,[string] $Pattern='.') $failure=$null;try {& $Body|Out-Null}catch{$failure=$_.Exception.Message};Assert-ReceiptTest ($null -ne $failure -and $failure -match $Pattern) ('Expected rejection '+$Pattern+'; actual '+$failure) }
function Bytes { param([string] $Text) return ,([Text.UTF8Encoding]::new($false,$true).GetBytes($Text)) }
function FileRecord { param([string] $Path,[byte[]] $Raw) return [pscustomobject][ordered]@{path=$Path;sha256=Get-StratusCommandRuntimeHash $Raw 134217728 'fixture';bytes=$Raw.Length} }
function Rebind-Inputs {
    param([object] $F)
    $F.ProfileBytes=Get-StratusCommandRuntimeContractBytes $F.ProfileDocument.value
    $F.ProfileDocument.sha256=Get-StratusCommandRuntimeHash $F.ProfileBytes 262144 'profile fixture'
    $F.Expected.profile_sha256=$F.ProfileDocument.sha256;$F.PlanDocument.value.profile_sha256=$F.ProfileDocument.sha256
    $F.PlanBytes=Get-StratusCommandRuntimeContractBytes $F.PlanDocument.value
    $F.PlanDocument.sha256=Get-StratusCommandRuntimeHash $F.PlanBytes 262144 'plan fixture'
}
function Rebind-Receipt {
    param([object] $F)
    $F.ReceiptBytes=Get-StratusCommandRuntimeContractBytes $F.ReceiptDocument.value
    $F.ReceiptDocument.sha256=Get-StratusCommandRuntimeHash $F.ReceiptBytes 262144 'receipt fixture'
    $F.ExpectedReceipt.sha256=$F.ReceiptDocument.sha256
}
function New-ReceiptFixture {
    $root='C:\stratus';$worktree='C:\candidate';$id='b'*32;$build=$root+'\.stratus-cache\runtime-builds\'+$id+'.in-progress'
    $sourceBytes=Bytes '// literal fixture source; never compile'
    $source=FileRecord ($worktree+'\scripts\Stratus.ParentStage.Runtime.cs') $sourceBytes
    $manifest=Bytes ("scripts/Stratus.ParentStage.Runtime.cs`t"+$source.bytes+"`t"+$source.sha256+"`n")
    $raw=@((Bytes 'host'),(Bytes 'compiler'),(Bytes 'producer'),(Bytes 'verifier'),(Bytes 'reference'),(Bytes 'compiler-runtime'))
    $paths=@('C:\tools\powershell.exe','C:\tools\csc.exe','C:\tools\producer.ps1','C:\tools\verifier.dll','C:\tools\reference.dll','C:\tools\compiler-runtime.dll')
    $hostFile=FileRecord $paths[0] $raw[0];$compilerFile=FileRecord $paths[1] $raw[1]
    $profile=[pscustomobject][ordered]@{format='stratus-parent-command-build-profile-v2';profile_id='literal-fixture';
        host=[pscustomobject][ordered]@{path=$hostFile.path;sha256=$hostFile.sha256;bytes=$hostFile.bytes;ps_edition='Desktop';ps_version='5.1.26100.3624';clr_version='4.0.30319.42000'}
        compiler=[pscustomobject][ordered]@{path=$compilerFile.path;sha256=$compilerFile.sha256;bytes=$compilerFile.bytes;version='fixture compiler'}
        references=[object[]]@((FileRecord $paths[4] $raw[4]));target_framework='.NETFramework,Version=v4.8';architecture='x64';assembly_name='Stratus.ParentStage.Runtime'
        producer=FileRecord $paths[2] $raw[2];metadata_verifier=FileRecord $paths[3] $raw[3]
        dependencies=[object[]]@([pscustomobject][ordered]@{role='compiler_runtime';path=$paths[5];sha256=(FileRecord $paths[5] $raw[5]).sha256;bytes=$raw[5].Length})
        compiler_recipe='direct-csc-exe-v1'}
    $expected=[pscustomobject][ordered]@{root=$root;revision=('a'*40);source_manifest_sha256=Get-StratusCommandRuntimeHash $manifest 4194304 'manifest fixture'
        profile_sha256=('0'*64);build_id=$id;build_worktree=$worktree;build_directory=$build;source=$source}
    $output=$build+'\Stratus.ParentStage.Runtime.dll'
    $plan=[pscustomobject][ordered]@{format='stratus-parent-command-build-plan-v2';build_id=$id;revision=$expected.revision;source_manifest_sha256=$expected.source_manifest_sha256
        profile_sha256=('0'*64);build_worktree=$worktree;build_directory=$build;source=$source;output=[pscustomobject][ordered]@{path=$output}
        compiler_arguments=[string[]]@('/target:library','/noconfig','/nostdlib+','/platform:x64',('/out:'+$output),('/reference:'+$paths[4]),$source.path)}
    $snapshots=@();for($i=0;$i -lt $paths.Count;$i++){$snapshots+=,[pscustomobject][ordered]@{path=$paths[$i];bytes=$raw[$i]}}
    $f=[pscustomobject]@{ProfileDocument=[pscustomobject][ordered]@{path='C:\approval\runtime-profile.json';sha256=('0'*64);value=$profile};ProfileBytes=[byte[]]@(1)
        PlanDocument=[pscustomobject][ordered]@{path=$build+'\command-runtime-plan.json';sha256=('0'*64);value=$plan};PlanBytes=[byte[]]@(1)
        SourceManifestBytes=$manifest;SourceBytes=$sourceBytes;InputSnapshots=[object[]]$snapshots;Expected=$expected
        ReceiptDocument=[pscustomobject][ordered]@{path=$build+'\command-runtime-build.json';sha256=('0'*64);value=$null};ReceiptBytes=[byte[]]@(1)
        BuildOutputBytes=[byte[]]@(77,90,1,2,3,4);SealedOutputBytes=[byte[]]@(77,90,1,2,3,4);StdoutBytes=[byte[]]@();StderrBytes=[byte[]]@()
        ExpectedReceipt=[pscustomobject][ordered]@{sha256=('0'*64);qualified_at='2026-09-15T00:02:00.0000000+00:00';sealed_directory=$root+'\.stratus-cache\release-transitions\r1-'+$expected.revision+'.accepted'}}
    Rebind-Inputs $f
    $actual=FileRecord $output $f.BuildOutputBytes
    $empty='e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
    $f.ReceiptDocument.value=[pscustomobject][ordered]@{format='stratus-parent-command-runtime-build-v2';abi='stratus-parent-command-session-v1';build_id=$id
        revision=$expected.revision;source_manifest_sha256=$expected.source_manifest_sha256;profile_sha256=$f.ProfileDocument.sha256;plan_sha256=$f.PlanDocument.sha256
        started_at='2026-09-15T00:00:00.0000000+00:00';completed_at='2026-09-15T00:01:00.0000000+00:00';compiler_arguments=[string[]]$plan.compiler_arguments.Clone()
        output=$actual;sealed_artifact=[pscustomobject][ordered]@{name='Stratus.ParentStage.Runtime.dll';sha256=$actual.sha256;bytes=$actual.bytes}
        result=[pscustomobject][ordered]@{exit_code=0;stdout_sha256=$empty;stdout_bytes=0;stderr_sha256=$empty;stderr_bytes=0}}
    Rebind-Receipt $f
    return $f
}
function Invoke-ReceiptFixture {
    param([object] $F)
    Assert-StratusParentCommandBuildReceipt -ProfileDocument $F.ProfileDocument -ProfileBytes $F.ProfileBytes -PlanDocument $F.PlanDocument -PlanBytes $F.PlanBytes `
        -SourceManifestBytes $F.SourceManifestBytes -SourceBytes $F.SourceBytes -InputSnapshots $F.InputSnapshots -Expected $F.Expected `
        -ReceiptDocument $F.ReceiptDocument -ReceiptBytes $F.ReceiptBytes -BuildOutputBytes $F.BuildOutputBytes -SealedOutputBytes $F.SealedOutputBytes `
        -StdoutBytes $F.StdoutBytes -StderrBytes $F.StderrBytes -ExpectedReceipt $F.ExpectedReceipt
}
function Invoke-ReceiptPreflightFixture {
    param([object] $F)
    Get-StratusParentCommandBuildPreflight -ProfileDocument $F.ProfileDocument -ProfileBytes $F.ProfileBytes `
        -PlanDocument $F.PlanDocument -PlanBytes $F.PlanBytes -SourceManifestBytes $F.SourceManifestBytes -Expected $F.Expected
}
function Invoke-ReceiptSpecificationsFixture {
    param([object] $F)
    return ,(Get-StratusParentCommandBuildInputSpecifications -ProfileDocument $F.ProfileDocument -ProfileBytes $F.ProfileBytes `
        -PlanDocument $F.PlanDocument -PlanBytes $F.PlanBytes -SourceManifestBytes $F.SourceManifestBytes `
        -Expected $F.Expected -SourceManifestPath 'C:\manifests\source.tsv')
}
Case 'same synthetic bytes bind distinct actual and sealed locations' {
    $f=New-ReceiptFixture;$r=Invoke-ReceiptFixture $f
    Assert-ReceiptTest ($r.actual_output_path -ceq $f.Expected.build_directory+'\Stratus.ParentStage.Runtime.dll' -and
        $r.sealed_output_path -ceq $f.ExpectedReceipt.sealed_directory+'\Stratus.ParentStage.Runtime.dll' -and
        $r.source_path -ceq 'C:\candidate\scripts\Stratus.ParentStage.Runtime.cs' -and $r.actual_output_path -cne $r.sealed_output_path) 'Actual/sealed paths were conflated.'
    foreach($name in @('raw_input_binding_revalidated','raw_stream_digests_verified','document_value_correspondence_verified','build_and_sealed_snapshot_bytes_equal')) {
        Assert-ReceiptTest ($r.$name -is [bool] -and $r.$name) ('Missing scoped correspondence '+$name)
    }
}
Case 'nonloadable synthetic output never gains execution or import authority' {
    $r=Invoke-ReceiptFixture (New-ReceiptFixture)
    foreach($name in @('approved_expected_provenance_verified','actual_dependency_closure_qualified','held_handle_provenance_verified','compiler_execution_verified',
        'compiler_root_tree_eof_causality_verified','build_causality_verified','assembly_metadata_verified','clr_compatibility_verified','sealed_publication_verified',
        'same_held_bytes_import_verified','import_permitted','native_qualified','production_start_permitted','live_permitted','binance_kill_switch_removal_permitted','full_goal_accepted')) {
        Assert-ReceiptTest ($r.$name -is [bool] -and -not $r.$name) ('Unexpected authority '+$name)
    }
}
Case 'nonempty actual stdout binds its full bytes' {
    $f=New-ReceiptFixture;$f.StdoutBytes=Bytes "compiler banner`n";$f.ReceiptDocument.value.result.stdout_bytes=$f.StdoutBytes.Length
    $f.ReceiptDocument.value.result.stdout_sha256=Get-StratusCommandBuildStreamHash $f.StdoutBytes 1048576 'fixture stdout';Rebind-Receipt $f
    $r=Invoke-ReceiptFixture $f;Assert-ReceiptTest $r.raw_stream_digests_verified 'Raw stdout not bound.'
}
$mutations=@(
    @('old receipt version',{$f.ReceiptDocument.value.format='stratus-parent-command-runtime-build-v1'}),
    @('wrong ABI',{$f.ReceiptDocument.value.abi='stratus-parent-command-session-v2'}),
    @('build identity',{$f.ReceiptDocument.value.build_id='c'*32}),
    @('candidate revision',{$f.ReceiptDocument.value.revision='c'*40}),
    @('source manifest identity',{$f.ReceiptDocument.value.source_manifest_sha256='c'*64}),
    @('profile identity',{$f.ReceiptDocument.value.profile_sha256='c'*64}),
    @('plan identity',{$f.ReceiptDocument.value.plan_sha256='c'*64}),
    @('backward chronology',{$f.ReceiptDocument.value.completed_at='2026-09-14T23:59:59.0000000+00:00'}),
    @('after qualification',{$f.ReceiptDocument.value.completed_at='2026-09-15T00:03:00.0000000+00:00'}),
    @('noncanonical UTC',{$f.ReceiptDocument.value.started_at='2026-09-15T00:00:00Z'}),
    @('compiler extra flag',{$f.ReceiptDocument.value.compiler_arguments+= '/unsafe+'}),
    @('compiler argv uses production source',{$f.ReceiptDocument.value.compiler_arguments[-1]=$f.Expected.root+'\scripts\Stratus.ParentStage.Runtime.cs'}),
    @('compiler argv uses accepted output',{$f.ReceiptDocument.value.compiler_arguments[4]='/out:'+$f.ExpectedReceipt.sealed_directory+'\Stratus.ParentStage.Runtime.dll'}),
    @('compiler output is final accepted file',{$f.ReceiptDocument.value.output.path=$f.ExpectedReceipt.sealed_directory+'\Stratus.ParentStage.Runtime.dll'}),
    @('compiler output is production file',{$f.ReceiptDocument.value.output.path=$f.Expected.root+'\Stratus.ParentStage.Runtime.dll'}),
    @('logical output name',{$f.ReceiptDocument.value.sealed_artifact.name='other.dll'}),
    @('logical output traversal',{$f.ReceiptDocument.value.sealed_artifact.name='..\Stratus.ParentStage.Runtime.dll'}),
    @('logical output digest',{$f.ReceiptDocument.value.sealed_artifact.sha256='c'*64}),
    @('logical output length',{$f.ReceiptDocument.value.sealed_artifact.bytes++}),
    @('string output length',{$f.ReceiptDocument.value.output.bytes='6'}),
    @('nonzero compiler exit',{$f.ReceiptDocument.value.result.exit_code=1}),
    @('string compiler exit',{$f.ReceiptDocument.value.result.exit_code='0'}),
    @('declared stderr',{$f.ReceiptDocument.value.result.stderr_bytes=1}),
    @('declared stderr digest',{$f.ReceiptDocument.value.result.stderr_sha256='c'*64}),
    @('declared stdout length',{$f.ReceiptDocument.value.result.stdout_bytes=1}),
    @('declared stdout digest',{$f.ReceiptDocument.value.result.stdout_sha256='c'*64}),
    @('extra receipt authority property',{$f.ReceiptDocument.value|Add-Member NoteProperty accepted $true}),
    @('extra output property',{$f.ReceiptDocument.value.output|Add-Member NoteProperty imported $true})
)
foreach($entry in $mutations) { Case ('reject '+$entry[0]) { $f=New-ReceiptFixture;& $entry[1];Rebind-Receipt $f;Reject {Invoke-ReceiptFixture $f} } }
$rawMutations=@(
    @('independent receipt digest',{$f.ExpectedReceipt.sha256='c'*64}),
    @('receipt document digest',{$f.ReceiptDocument.sha256='c'*64}),
    @('receipt is final path',{$f.ReceiptDocument.path=$f.ExpectedReceipt.sealed_directory+'\command-runtime-build.json'}),
    @('wrong sealed R1 revision',{$f.ExpectedReceipt.sealed_directory=$f.ExpectedReceipt.sealed_directory.Replace(('a'*40),('c'*40))}),
    @('invalid qualification time',{$f.ExpectedReceipt.qualified_at='2026-09-15T00:02:00Z'}),
    @('changed receipt bytes',{$f.ReceiptBytes[0]=32}),
    @('changed output bytes',{$f.BuildOutputBytes[2]=9}),
    @('changed sealed bytes',{$f.SealedOutputBytes[2]=9}),
    @('truncated output',{$f.BuildOutputBytes=[byte[]]@(77,90)}),
    @('non-byte output',{$f.BuildOutputBytes=[int[]]@(77,90,1,2,3,4)}),
    @('null output',{$f.BuildOutputBytes=$null}),
    @('non-byte sealed output',{$f.SealedOutputBytes='MZ'}),
    @('actual nonempty stderr',{$f.StderrBytes=Bytes 'warning'}),
    @('actual unbound stdout',{$f.StdoutBytes=Bytes 'banner'}),
    @('non-byte stdout',{$f.StdoutBytes=[int[]]@()}),
    @('null stderr',{$f.StderrBytes=$null}),
    @('underlying profile bytes revalidated',{$f.ProfileBytes[0]=32}),
    @('underlying plan bytes revalidated',{$f.PlanBytes[0]=32}),
    @('underlying manifest bytes revalidated',{$f.SourceManifestBytes[0]=65}),
    @('underlying source bytes revalidated',{$f.SourceBytes[0]=65}),
    @('underlying compiler snapshot revalidated',{$f.InputSnapshots[1].bytes[0]=65}),
    @('underlying input inventory revalidated',{$f.InputSnapshots=$f.InputSnapshots[0..4]})
)
foreach($entry in $rawMutations) { Case ('reject '+$entry[0]) { $f=New-ReceiptFixture;& $entry[1];Reject {Invoke-ReceiptFixture $f} } }
foreach($entry in @(
    @('receipt/profile collision',{$f.ProfileDocument.path=$f.ReceiptDocument.path}),
    @('receipt/dependency collision',{$f.ProfileDocument.value.dependencies[0].path=$f.ReceiptDocument.path;$f.InputSnapshots[5].path=$f.ReceiptDocument.path}),
    @('receipt/dependency case collision',{$p=$f.ReceiptDocument.path.ToUpperInvariant();$f.ProfileDocument.value.dependencies[0].path=$p;$f.InputSnapshots[5].path=$p}),
    @('sealed/reference collision',{$p=$f.ExpectedReceipt.sealed_directory+'\Stratus.ParentStage.Runtime.dll';$f.ProfileDocument.value.references[0].path=$p;$f.InputSnapshots[4].path=$p;$f.PlanDocument.value.compiler_arguments[5]='/reference:'+$p})
)) {
    Case ('reject '+$entry[0]) {
        $f=New-ReceiptFixture;& $entry[1];Rebind-Inputs $f;$f.ReceiptDocument.value.profile_sha256=$f.ProfileDocument.sha256
        $f.ReceiptDocument.value.plan_sha256=$f.PlanDocument.sha256;Rebind-Receipt $f
        Reject {Invoke-ReceiptFixture $f} 'alias'
    }
}
foreach($entry in @(
    @('receipt BOM',[byte[]]@(239,187,191)),@('receipt space',[byte[]]@(32))
)) {
    Case ('reject pinned '+$entry[0]) {
        $f=New-ReceiptFixture;$f.ReceiptBytes=[byte[]]($entry[1]+$f.ReceiptBytes)
        $f.ReceiptDocument.sha256=Get-StratusCommandRuntimeHash $f.ReceiptBytes 262144 'changed pinned receipt';$f.ExpectedReceipt.sha256=$f.ReceiptDocument.sha256
        Reject {Invoke-ReceiptFixture $f} 'wire'
    }
}
Case 'receipt metadata cannot dispatch poisoned members' {
    $f=New-ReceiptFixture;$script:receiptPoison=0
    $f.ReceiptDocument.value|Add-Member ScriptProperty poison {$script:receiptPoison++;throw 'poison invoked'}
    Reject {Invoke-ReceiptFixture $f} 'non-data|property set'
    Assert-ReceiptTest ($script:receiptPoison -eq 0) 'Untrusted receipt getter ran.'
}
$unsafeSources=@(
    $receiptSource.Replace('    $inputBinding=Assert-StratusParentCommandBuildInputs',"    & 'Get-Process'`n    `$inputBinding=Assert-StratusParentCommandBuildInputs"),
    $receiptSource.Replace('[Security.Cryptography.SHA256]::Create()','[Reflection.Assembly]::Load()'),
    $receiptSource.Replace('.ToLowerInvariant()', '.ReadAllText()'),
    ("begin { throw 'not executed' }`nend {`n"+$receiptSource+"`n}"),
    ("using namespace System`n"+$receiptSource)
)
foreach($text in $unsafeSources) { Case 'unsafe source mutation rejected before evaluation' {Reject {Get-ReceiptProgram $text $receiptNames}} }
Assert-ReceiptTest ($results.Count -eq 65) 'The original full receipt/byte-rejection inventory changed.'
# A successful metadata-only calculation is deliberately not passed into the
# receipt API. Every call below still has to revalidate all original raw inputs.
Case 'receipt rechecks source bytes after successful preflight' {
    $f=New-ReceiptFixture;$null=Invoke-ReceiptPreflightFixture $f;$f.SourceBytes[0]=$f.SourceBytes[0] -bxor 1
    Reject {Invoke-ReceiptFixture $f} 'Actual source bytes'
}
foreach($index in 0..5) {
    Case ('receipt rechecks each input after successful preflight '+$index) {
        $f=New-ReceiptFixture;$null=Invoke-ReceiptPreflightFixture $f;$f.InputSnapshots[$index].bytes[0]=$f.InputSnapshots[$index].bytes[0] -bxor 1
        Reject {Invoke-ReceiptFixture $f} 'Actual declared input bytes'
    }
}
Case 'receipt still requires source bytes after specification derivation' {
    $f=New-ReceiptFixture;$null=Invoke-ReceiptSpecificationsFixture $f;$f.SourceBytes=$null
    Reject {Invoke-ReceiptFixture $f}
}
Case 'specification projection is not receipt source bytes' {
    $f=New-ReceiptFixture;$f.SourceBytes=Invoke-ReceiptSpecificationsFixture $f
    Reject {Invoke-ReceiptFixture $f}
}
Case 'specification projection is not receipt input snapshots' {
    $f=New-ReceiptFixture;$f.InputSnapshots=Invoke-ReceiptSpecificationsFixture $f
    Reject {Invoke-ReceiptFixture $f}
}
Case 'mutated preflight projection cannot change receipt input binding' {
    $f=New-ReceiptFixture;$projection=Invoke-ReceiptPreflightFixture $f
    $projection.source.sha256='0'*64;$projection.declared_inputs[0].sha256='0'*64
    $r=Invoke-ReceiptFixture $f
    Assert-ReceiptTest ($r.source_path -ceq $f.Expected.source.path -and $r.raw_input_binding_revalidated -eq $true -and $r.full_goal_accepted -eq $false) 'Receipt consumed metadata projection or granted authority.'
}
foreach($field in @('ProfileBytes','SourceManifestBytes')) {
    Case ('receipt metadata byte wrapper never invokes getter '+$field) {
        $f=New-ReceiptFixture;$null=Invoke-ReceiptPreflightFixture $f;$script:receiptPoison=0
        $wrapped=[psobject]::AsPSObject($f.$field)
        Add-Member -InputObject $wrapped -MemberType ScriptProperty -Name Length -Value {$script:receiptPoison++;throw 'poison invoked'} -Force
        Assert-ReceiptTest ($wrapped.PSObject.Members['Length'].MemberType -eq [Management.Automation.PSMemberTypes]::ScriptProperty) 'Poison wrapper lost the requested member.'
        $f.$field=$wrapped;Reject {Invoke-ReceiptFixture $f}
        Assert-ReceiptTest ($script:receiptPoison -eq 0) 'Metadata byte getter ran.'
    }
}
Case 'unreviewed pure attribute is rejected before evaluation' {Reject {Get-ReceiptProgram ($receiptSource.Replace('[CmdletBinding()]','[Obsolete()]')) $receiptNames}}
$expectedCases=79
Assert-ReceiptTest ($results.Count -eq $expectedCases) ('Fixed receipt test count differs: '+$results.Count)
[pscustomobject]@{suite='parent-command-build-receipt-v2';expected=$expectedCases;executed=$results.Count;passed=$results.Count;failed=0
    ps_version=$PSVersionTable.PSVersion.ToString();ps_edition=$PSVersionTable.PSEdition
    helper_sha256=(Get-FileHash -LiteralPath $helperPath -Algorithm SHA256).Hash.ToLowerInvariant()
    inputs_sha256=(Get-FileHash -LiteralPath $inputsPath -Algorithm SHA256).Hash.ToLowerInvariant()
    receipt_sha256=(Get-FileHash -LiteralPath $LibraryPath -Algorithm SHA256).Hash.ToLowerInvariant()
    static_and_pure_only=$true;synthetic_nonloadable_output=$true;producer_executed=$false;compiler_or_runtime_loaded=$false
    native_executed=$false;filesystem_fixture_created=$false;full_goal_accepted=$false;results=@($results.ToArray())}|ConvertTo-Json -Depth 5 -Compress
