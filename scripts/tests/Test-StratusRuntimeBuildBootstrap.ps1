[CmdletBinding()]
param([string] $LibraryPath)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if (-not $LibraryPath) { $LibraryPath=Join-Path (Split-Path $PSScriptRoot -Parent) 'Stratus.RuntimeBuild.Bootstrap.ps1' }
# Source-only contracts plus the one pure request validator. No Import or Read
# helper is invoked; no file fixture, assembly, compiler, native process or pipe
# is created. Typed request arguments are trusted-argument validation, not a
# raw-JSON or hostile ETS object boundary.
$functionNames=@('Assert-StratusBuildBootstrapRequest','Read-StratusBuildBootstrapBytes','Import-StratusPinnedBuildBootstrap')
function Assert-BootstrapTest { param([bool] $Value,[string] $Message) if (-not $Value) { throw $Message } }
function Get-BootstrapProgram {
    param([string] $Text)
    $tokens=$null;$errors=$null
    $ast=[Management.Automation.Language.Parser]::ParseInput($Text,[ref]$tokens,[ref]$errors)
    Assert-BootstrapTest (@($errors).Count -eq 0) 'Bootstrap source parse differs.'
    Assert-BootstrapTest ($null -eq $ast.ParamBlock -and $null -eq $ast.BeginBlock -and $null -eq $ast.ProcessBlock -and
        $null -eq $ast.DynamicParamBlock -and @($ast.UsingStatements).Count -eq 0 -and
        $null -ne $ast.EndBlock -and $ast.EndBlock.Statements.Count -eq 3) 'Bootstrap root must contain exactly three function definitions.'
    $clean=$ast.PSObject.Properties['CleanBlock']
    Assert-BootstrapTest ($null -eq $clean -or $null -eq $clean.Value) 'Root clean block is forbidden.'
    $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach($statement in $ast.EndBlock.Statements) {
        Assert-BootstrapTest ($statement -is [Management.Automation.Language.FunctionDefinitionAst] -and
            $statement.Name -cin $functionNames -and $seen.Add($statement.Name)) 'Unexpected or duplicate bootstrap function.'
        Assert-BootstrapTest ($null -eq $statement.Body.BeginBlock -and $null -eq $statement.Body.ProcessBlock -and
            $null -eq $statement.Body.DynamicParamBlock) 'Function special blocks are forbidden.'
        $innerClean=$statement.Body.PSObject.Properties['CleanBlock']
        Assert-BootstrapTest ($null -eq $innerClean -or $null -eq $innerClean.Value) 'Function clean block is forbidden.'
        foreach($parameter in $statement.Body.ParamBlock.Parameters) {
            Assert-BootstrapTest ($null -eq $parameter.DefaultValue) 'Parameter defaults are forbidden before defining subject functions.'
        }
    }
    Assert-BootstrapTest (@($ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)).Count -eq 3) 'Nested functions are forbidden.'
    foreach($attribute in $ast.FindAll({param($n) $n -is [Management.Automation.Language.AttributeAst]},$true)) {
        Assert-BootstrapTest ($attribute.TypeName.FullName -cin @('CmdletBinding','Parameter')) 'Unreviewed function attribute.'
        foreach($argument in $attribute.PositionalArguments) { throw 'Positional attribute arguments are forbidden.' }
        foreach($argument in $attribute.NamedArguments) {
            Assert-BootstrapTest ($argument.ArgumentName -ceq 'Mandatory' -and
                $argument.Argument -is [Management.Automation.Language.VariableExpressionAst] -and
                $argument.Argument.VariablePath.UserPath -ceq 'true') 'Unreviewed attribute expression.'
        }
    }
    # Only this body is ever called. Import/Read bodies are inspected below but
    # are never evaluated or substituted with fake implementations.
    $request=@($ast.EndBlock.Statements | Where-Object {$_.Name -ceq 'Assert-StratusBuildBootstrapRequest'})[0]
    Assert-BootstrapTest (@($request.FindAll({param($n) $n -is [Management.Automation.Language.CommandAst] -or
        $n -is [Management.Automation.Language.RedirectionAst]},$true)).Count -eq 0) 'The request validator cannot invoke any command.'
    foreach($type in $request.FindAll({param($n) $n -is [Management.Automation.Language.TypeExpressionAst] -or
        $n -is [Management.Automation.Language.TypeConstraintAst]},$true)) {
        Assert-BootstrapTest ($type.TypeName.FullName -cin @('string','long','regex','IO.Path','StringComparison','Text.RegularExpressions.RegexOptions')) 'Unreviewed pure request type.'
    }
    foreach($member in $request.FindAll({param($n) $n -is [Management.Automation.Language.InvokeMemberExpressionAst]},$true)) {
        Assert-BootstrapTest ($member.Member -is [Management.Automation.Language.StringConstantExpressionAst] -and
            $member.Member.Value -cin @('IsMatch','IndexOf','GetFullPath','Substring','Split','IsNullOrEmpty','EndsWith','GetFileName','Equals')) 'Unreviewed pure request method.'
    }
    return $ast
}
function Get-BootstrapFunction { param([object] $Ast,[string] $Name) $found=@($Ast.EndBlock.Statements | Where-Object {$_.Name -ceq $Name});Assert-BootstrapTest ($found.Count -eq 1) 'Function selection is ambiguous.';return $found[0].Extent.Text }
function Compact-Bootstrap {
    param([string] $Text)
    $tokens=$null;$errors=$null
    $null=[Management.Automation.Language.Parser]::ParseInput($Text,[ref]$tokens,[ref]$errors)
    return (@($tokens | Where-Object {$_.Kind -notin @('Comment','NewLine','EndOfInput')} | ForEach-Object {$_.Text}) -join '')
}
function Has-Bootstrap { param([string] $Text,[string] $Snippet) return (Compact-Bootstrap $Text).Contains((Compact-Bootstrap $Snippet)) }
function InOrder-Bootstrap {
    param([string] $Text,[string[]] $Snippets)
    $compact=Compact-Bootstrap $Text;$previous=-1
    foreach($snippet in $Snippets){$next=$compact.IndexOf((Compact-Bootstrap $snippet),($previous+1),[StringComparison]::Ordinal);if($next -lt 0){return $false};$previous=$next}
    return $true
}
$source=Get-Content -LiteralPath $LibraryPath -Raw
$sourceBefore=(Get-FileHash -LiteralPath $LibraryPath -Algorithm SHA256).Hash.ToLowerInvariant()
$program=Get-BootstrapProgram $source
$requestText=Get-BootstrapFunction $program 'Assert-StratusBuildBootstrapRequest'
$readText=Get-BootstrapFunction $program 'Read-StratusBuildBootstrapBytes'
$importText=Get-BootstrapFunction $program 'Import-StratusPinnedBuildBootstrap'
# Definitions only, after exact function-only root and pure-body guard. These
# imports merely define the Read/Import functions; neither is called by this test.
. ([scriptblock]::Create($source))
$results=[Collections.Generic.List[object]]::new()
function Case { param([string] $CaseLabel,[scriptblock] $Body) try { & $Body } catch { throw ('Bootstrap case '+$CaseLabel+' failed: '+$_.Exception.Message) };$results.Add([pscustomobject]@{name=$CaseLabel;passed=$true}) }
function Reject { param([scriptblock] $Body) $failed=$false;try{& $Body|Out-Null}catch{$failed=$true};Assert-BootstrapTest $failed 'Expected rejection did not occur.' }
function New-Request {
    return @{BootstrapPath='C:\approved\Stratus.RuntimeBuild.Bootstrap.dll';ExpectedBootstrapSha256=('a'*64);ExpectedBootstrapBytes=4096L
        ExpectedHostPath='C:\qualified\powershell.exe';ExpectedHostSha256=('b'*64);ExpectedHostBytes=8192L
        ExpectedPSEdition='Desktop';ExpectedPSVersion='5.1.26100.3624';ExpectedCLRVersion='4.0.30319.42000'}
}
Case 'literal Desktop request is pure and returns no authority' {$q=New-Request;$r=@(Assert-StratusBuildBootstrapRequest @q);Assert-BootstrapTest ($r.Count -eq 0) 'Request validator returned authority.'}
Case 'literal Core request is syntactically accepted without runtime claim' {$q=New-Request;$q.ExpectedHostPath='C:\qualified\pwsh.exe';$q.ExpectedPSEdition='Core';$q.ExpectedPSVersion='7.6.5';$q.ExpectedCLRVersion='10.0.9';Assert-StratusBuildBootstrapRequest @q}
Case 'exact minimum byte lengths are allowed' {$q=New-Request;$q.ExpectedBootstrapBytes=1L;$q.ExpectedHostBytes=1L;Assert-StratusBuildBootstrapRequest @q}
Case 'exact maximum byte lengths are allowed' {$q=New-Request;$q.ExpectedBootstrapBytes=16777216L;$q.ExpectedHostBytes=134217728L;Assert-StratusBuildBootstrapRequest @q}
Case 'uppercase drive and executable extension do not relax fixed DLL role' {$q=New-Request;$q.ExpectedHostPath='D:\Qualified Host\PWSH.EXE';Assert-StratusBuildBootstrapRequest @q}
$requestMutations=@(
    @('relative bootstrap path',{$q.BootstrapPath='Stratus.RuntimeBuild.Bootstrap.dll'}),
    @('UNC bootstrap path',{$q.BootstrapPath='\\server\share\Stratus.RuntimeBuild.Bootstrap.dll'}),
    @('device bootstrap path',{$q.BootstrapPath='\\?\C:\approved\Stratus.RuntimeBuild.Bootstrap.dll'}),
    @('drive-relative host path',{$q.ExpectedHostPath='C:powershell.exe'}),
    @('forward slash path',{$q.BootstrapPath='C:/approved/Stratus.RuntimeBuild.Bootstrap.dll'}),
    @('dot component path',{$q.BootstrapPath='C:\approved\.\Stratus.RuntimeBuild.Bootstrap.dll'}),
    @('traversal component path',{$q.BootstrapPath='C:\approved\..\Stratus.RuntimeBuild.Bootstrap.dll'}),
    @('repeated separator path',{$q.BootstrapPath='C:\approved\\Stratus.RuntimeBuild.Bootstrap.dll'}),
    @('ADS host path',{$q.ExpectedHostPath='C:\qualified\powershell.exe:stream'}),
    @('trailing dot component',{$q.BootstrapPath='C:\approved.\Stratus.RuntimeBuild.Bootstrap.dll'}),
    @('trailing space component',{$q.BootstrapPath='C:\approved \Stratus.RuntimeBuild.Bootstrap.dll'}),
    @('reserved CON directory',{$q.BootstrapPath='C:\CON\Stratus.RuntimeBuild.Bootstrap.dll'}),
    @('reserved COM9 dotted directory',{$q.BootstrapPath='C:\COM9.any\Stratus.RuntimeBuild.Bootstrap.dll'}),
    @('reserved mixed-case NUL host',{$q.ExpectedHostPath='C:\qualified\nUl.exe'}),
    @('wildcard directory',{$q.BootstrapPath='C:\appr*ved\Stratus.RuntimeBuild.Bootstrap.dll'}),
    @('line-control directory',{$q.BootstrapPath="C:\approved`n\Stratus.RuntimeBuild.Bootstrap.dll"}),
    @('NUL directory',{$q.BootstrapPath=('C:\approved'+[char]0+'\Stratus.RuntimeBuild.Bootstrap.dll')}),
    @('overlong path',{$q.BootstrapPath=('C:\'+('a'*230)+'\Stratus.RuntimeBuild.Bootstrap.dll')}),
    @('wrong DLL filename',{$q.BootstrapPath='C:\approved\other.dll'}),
    @('wrong DLL filename case',{$q.BootstrapPath='C:\approved\stratus.RuntimeBuild.Bootstrap.dll'}),
    @('non-executable host role',{$q.ExpectedHostPath='C:\qualified\powershell.dll'}),
    @('uppercase bootstrap hash',{$q.ExpectedBootstrapSha256='A'*64}),
    @('short bootstrap hash',{$q.ExpectedBootstrapSha256='a'*63}),
    @('long host hash',{$q.ExpectedHostSha256='b'*65}),
    @('zero bootstrap hash',{$q.ExpectedBootstrapSha256='0'*64}),
    @('zero host hash',{$q.ExpectedHostSha256='0'*64}),
    @('hash trailing LF',{$q.ExpectedHostSha256=('b'*64)+"`n"}),
    @('bootstrap zero bytes',{$q.ExpectedBootstrapBytes=0L}),
    @('bootstrap negative bytes',{$q.ExpectedBootstrapBytes=-1L}),
    @('bootstrap oversized bytes',{$q.ExpectedBootstrapBytes=16777217L}),
    @('host zero bytes',{$q.ExpectedHostBytes=0L}),
    @('host oversized bytes',{$q.ExpectedHostBytes=134217729L}),
    @('unknown edition',{$q.ExpectedPSEdition='Other'}),
    @('edition casing',{$q.ExpectedPSEdition='desktop'}),
    @('PS version range',{$q.ExpectedPSVersion='7.*'}),
    @('PS version preview',{$q.ExpectedPSVersion='7.6.5-preview.1'}),
    @('PS version too short',{$q.ExpectedPSVersion='7.6'}),
    @('CLR leading-zero component',{$q.ExpectedCLRVersion='04.0.30319.42000'}),
    @('CLR too many components',{$q.ExpectedCLRVersion='4.0.1.2.3'}),
    @('CLR oversized component',{$q.ExpectedCLRVersion='4.0.1000000000'}),
    @('version trailing CR',{$q.ExpectedPSVersion="7.6.5`r"})
)
foreach($mutation in $requestMutations){Case ('pure request rejects '+$mutation[0]) {$q=New-Request;& $mutation[1];Reject {Assert-StratusBuildBootstrapRequest @q}}}

# Each source condition has an explicit mutation that must fail that SAME
# predicate. Mutants are parsed/inspected only and are never dot-sourced.
$contracts=@(
    @('private reader allocation hash and return identity', 'read', '[byte[]]$snapshot=[byte[]]::new([int]$ExpectedBytes)', '[byte[]]$snapshot=$externalBytes', {
        param($t) (InOrder-Bootstrap $t @('[byte[]]$snapshot=[byte[]]::new([int]$ExpectedBytes)', '$read=$Stream.Read($snapshot,$offset,$snapshot.Length-$offset)', '$hash.ComputeHash($snapshot)', 'if ($actual -cne $ExpectedSha256)', 'return ,$snapshot'))
    }),
    @('reader rejects access handle length and excess bytes', 'read', '$Stream.CanWrite', '$false', {
        param($t) (Has-Bootstrap $t '-not $Stream.CanRead -or $Stream.CanWrite -or -not $Stream.CanSeek') -and
            (Has-Bootstrap $t '$Stream.SafeFileHandle.IsClosed -or $Stream.SafeFileHandle.IsInvalid') -and
            (Has-Bootstrap $t '$ExpectedBytes -lt 1 -or $ExpectedBytes -gt $MaximumBytes -or $MaximumBytes -gt 134217728') -and
            (Has-Bootstrap $t '$Stream.ReadByte() -ne -1 -or $Stream.Length -ne $ExpectedBytes')
    }),
    @('exact held bootstrap snapshot precedes sole private array load', 'import', '[Reflection.Assembly]::Load($bootstrapBytes)', '[Reflection.Assembly]::Load($otherBytes)', {
        param($t) (InOrder-Bootstrap $t @('[byte[]]$bootstrapBytes=Read-StratusBuildBootstrapBytes $bootstrapLease $ExpectedBootstrapBytes $ExpectedBootstrapSha256 16777216', '$assembly=[Reflection.Assembly]::Load($bootstrapBytes)'))
    }),
    @('readonly host and bootstrap leases are returned to owner', 'import', '[IO.FileAccess]::Read,[IO.FileShare]::Read)', '[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)', {
        param($t) (Has-Bootstrap $t '$hostLease=[IO.FileStream]::new($ExpectedHostPath,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)') -and
            (Has-Bootstrap $t '$bootstrapLease=[IO.FileStream]::new($BootstrapPath,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)') -and
            (Has-Bootstrap $t '[byte[]]$hostBytes=Read-StratusBuildBootstrapBytes $hostLease $ExpectedHostBytes $ExpectedHostSha256 134217728') -and
            (Has-Bootstrap $t 'bootstrap_lease=$bootstrapLease;host_lease=$hostLease')
    }),
    @('both API types are from returned assembly with exact ABI', 'import', '$commandType=$assembly.GetType', '$commandType=$resident.GetType', {
        param($t) (Has-Bootstrap $t "$('$commandType')=$('$assembly').GetType('Stratus.RuntimeBuild.CompilerCommand',`$true,`$false)") -and
            (Has-Bootstrap $t "$('$resultType')=$('$assembly').GetType('Stratus.RuntimeBuild.CompilerResult',`$true,`$false)") -and
            (Has-Bootstrap $t '[object]::ReferenceEquals($type.Assembly,$assembly)') -and
            (Has-Bootstrap $t '$abi.IsLiteral') -and (Has-Bootstrap $t "$('$abi').GetRawConstantValue() -cne 'stratus-runtime-build-bootstrap-v2'")
    }),
    @('AppDomain attempt is checked before resources and committed once under Monitor', 'import', '$domain.SetData($attemptKey,$true)', '$domain.SetData($attemptKey,$false)', {
        param($t) (InOrder-Bootstrap $t @("`$attemptKey='Stratus.RuntimeBuild.Bootstrap.ImportAttempt.v1'", '$domain=[AppDomain]::CurrentDomain',
            'if ($null -ne $domain.GetData($attemptKey))', '$hostLease=[IO.FileStream]::new', '[Threading.Monitor]::Enter($domain)',
            'if ($null -ne $domain.GetData($attemptKey))', '$domain.SetData($attemptKey,$true)', '[Threading.Monitor]::Exit($domain)', '$assembly=[Reflection.Assembly]::Load($bootstrapBytes)')) -and
            ([regex]::Matches((Compact-Bootstrap $t),[regex]::Escape((Compact-Bootstrap '$domain.SetData('))).Count -eq 1)
    }),
    @('current process host edition version CLR and x64 are independent checks', 'import', '[IntPtr]::Size -ne 8', '[IntPtr]::Size -ne 4', {
        param($t) (Has-Bootstrap $t '[IntPtr]::Size -ne 8') -and (Has-Bootstrap $t '$PSVersionTable.PSEdition -cne $ExpectedPSEdition') -and
            (Has-Bootstrap $t '$PSVersionTable.PSVersion.ToString() -cne $ExpectedPSVersion') -and
            (Has-Bootstrap $t '[Environment]::Version.ToString() -cne $ExpectedCLRVersion') -and
            (Has-Bootstrap $t '$hostPath=$current.MainModule.FileName') -and
            (Has-Bootstrap $t '[string]::Equals($hostPath,$ExpectedHostPath,[StringComparison]::OrdinalIgnoreCase)')
    }),
    @('resident assembly is rejection only', 'import', "throw 'Bootstrap already resident; cached assemblies and load retries are forbidden.'", 'return $resident', {
        param($t) (Has-Bootstrap $t 'foreach ($resident in [AppDomain]::CurrentDomain.GetAssemblies())') -and
            (Has-Bootstrap $t "throw 'Bootstrap already resident; cached assemblies and load retries are forbidden.'")
    }),
    @('cleanup retains exact primary and cleanup exception array', 'import', '[Exception[]]@($primary,$_.Exception)', '[Exception[]]@($_.Exception)', {
        param($t) (InOrder-Bootstrap $t @('$primary=$_.Exception','foreach ($lease in @($bootstrapLease,$hostLease))',
            'try { $lease.Dispose() }','catch { $primary=[AggregateException]::new([Exception[]]@($primary,$_.Exception)) }','throw $primary'))
    }),
    @('returned record does not assert profile dependency or operational qualification', 'import', 'candidate_qualified=$false', 'candidate_qualified=$true', {
        param($t) (Has-Bootstrap $t 'same_private_bootstrap_bytes_loaded=$true') -and
            (Has-Bootstrap $t 'approved_profile_origin_proven=$false;dependency_closure_proven=$false') -and
            (Has-Bootstrap $t 'compiler_executed=$false;candidate_qualified=$false;production_permitted=$false')
    })
)
foreach($contract in $contracts) {
    $subject=if($contract[1] -ceq 'read'){$readText}else{$importText}
    Case ('source invariant '+$contract[0]) {Assert-BootstrapTest (& $contract[4] $subject) 'Actual source invariant missing.'}
    Case ('negative control '+$contract[0]) {
        Assert-BootstrapTest ($subject.Contains($contract[2])) 'Required negative-control anchor missing.'
        $mutant=$subject.Replace($contract[2],$contract[3])
        Assert-BootstrapTest (-not (& $contract[4] $mutant)) 'Source predicate did not reject its targeted mutation.'
    }
}
Case 'no compiler path/name loader or dynamic call fallback in import source' {
    $ast=(Get-BootstrapProgram $source)
    $import=@($ast.EndBlock.Statements | Where-Object {$_.Name -ceq 'Import-StratusPinnedBuildBootstrap'})[0]
    $commands=@($import.FindAll({param($n) $n -is [Management.Automation.Language.CommandAst]},$true))
    foreach($command in $commands){Assert-BootstrapTest ($command.InvocationOperator -eq [Management.Automation.Language.TokenKind]::Unknown -and
        $command.GetCommandName() -cin @('Assert-StratusBuildBootstrapRequest','Read-StratusBuildBootstrapBytes')) 'Unreviewed import command or dynamic fallback.'}
    $loads=@($import.FindAll({param($n) $n -is [Management.Automation.Language.InvokeMemberExpressionAst] -and
        $n.Member -is [Management.Automation.Language.StringConstantExpressionAst] -and $n.Member.Value -in @('Load','LoadFrom','LoadFile','UnsafeLoadFrom')},$true))
    Assert-BootstrapTest ($loads.Count -eq 1 -and (Has-Bootstrap $loads[0].Extent.Text '[Reflection.Assembly]::Load($bootstrapBytes)')) 'Alternate or repeated assembly load found.'
}
$unsafeMutations=@(
    @('top-level statement',("Write-Output 'inert forbidden body'`n"+$source)),
    @('using declaration',("using namespace System`n"+$source)),
    @('root parameter',("param()`n"+$source)),
    @('begin block',("begin { 'inert' }`nend {`n"+$source+"`n}")),
    @('process block',("process { 'inert' }`nend {`n"+$source+"`n}")),
    @('dynamicparam block',("dynamicparam { 'inert' }`nend {`n"+$source+"`n}")),
    @('clean block',("end {`n"+$source+"`n}`nclean { 'inert' }")),
    @('duplicate function',($source+"`nfunction Assert-StratusBuildBootstrapRequest {}")),
    @('unknown function',($source.Replace('function Assert-StratusBuildBootstrapRequest','function UnreviewedRequest'))),
    @('pure request command',($source.Replace('function Assert-StratusBuildBootstrapRequest {',"function Assert-StratusBuildBootstrapRequest {`nWrite-Output 'inert'"))),
    @('pure request method',($source.Replace('[IO.Path]::GetFullPath($path)','[IO.File]::ReadAllText($path)'))),
    @('parameter default expression',($source.Replace('[string] $BootstrapPath,',"[string] `$BootstrapPath=('inert'),")))
)
foreach($mutation in $unsafeMutations){Case ('safe definition guard rejects '+$mutation[0]) {Reject {Get-BootstrapProgram $mutation[1]}}}
$expected=79
Assert-BootstrapTest ($results.Count -eq $expected) ('Fixed bootstrap test count differs: '+$results.Count)
$sourceAfter=(Get-FileHash -LiteralPath $LibraryPath -Algorithm SHA256).Hash.ToLowerInvariant()
Assert-BootstrapTest ($sourceAfter -ceq $sourceBefore) 'Bootstrap source changed during source/pure request tests.'
[pscustomobject][ordered]@{format='stratus-runtime-build-bootstrap-tests-v1';expected=$expected;executed=$results.Count;passed=$results.Count;failed=0
    ps_version=$PSVersionTable.PSVersion.ToString();ps_edition=$PSVersionTable.PSEdition
    source_sha256=$sourceAfter;test_sha256=(Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
    source_and_pure_request_only=$true;import_helper_executed=$false;read_helper_executed=$false
    assembly_loaded=$false;compiler_executed=$false;native_executed=$false;filesystem_fixture_created=$false
    bootstrap_qualified=$false;production_permitted=$false;full_goal_accepted=$false;results=$results.ToArray()} | ConvertTo-Json -Depth 6 -Compress
