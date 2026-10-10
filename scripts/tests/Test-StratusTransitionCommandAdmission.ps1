[CmdletBinding()]
param([string] $WatchdogPath)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if (-not $WatchdogPath) { $WatchdogPath=Join-Path (Split-Path $PSScriptRoot -Parent) 'Invoke-StratusSuccessorTransitionWatchdog.ps1' }
# Only the exact reviewed six-statement admission preamble is executed, with a
# local fake PSBoundParameters map and string values (one fixed test-only
# assignment inside the extracted block installs the map after automatic binding).
# Parameter binding and all
# remaining watchdog source are AST-only. No target file body, runtime import,
# native method, command handle adoption, process, Docker or fixture IO executes.
# The script-scoped mode assignment affects this isolated test script only.
$sourceBytes=[IO.File]::ReadAllBytes([IO.Path]::GetFullPath($WatchdogPath))
$source=[Text.UTF8Encoding]::new($false,$true).GetString($sourceBytes)
$sha=[Security.Cryptography.SHA256]::Create()
try {$sourceHash=[BitConverter]::ToString($sha.ComputeHash($sourceBytes)).Replace('-','').ToLowerInvariant()} finally {$sha.Dispose()}
$reviewedPreamble=@'
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:parentTransitionMode = $PSBoundParameters.ContainsKey('ParentStageNonce')
if ($script:parentTransitionMode -ne $PSBoundParameters.ContainsKey('ParentCommandSessionHandle')) {
    throw 'Parent Transition nonce and command-session handle must be supplied together.'
}
if ($script:parentTransitionMode -and -not [regex]::IsMatch($ParentStageNonce, '\A[0-9a-f]{64}\z')) {
    throw 'Parent Transition mode requires an explicit lowercase 64-hex nonce.'
}
if ($script:parentTransitionMode) {
    [uint64] $commandHandleNumber = 0
    if (-not [uint64]::TryParse($ParentCommandSessionHandle, [Globalization.NumberStyles]::None,
        [Globalization.CultureInfo]::InvariantCulture, [ref]$commandHandleNumber) -or
        $commandHandleNumber -eq 0 -or
        $commandHandleNumber.ToString([Globalization.CultureInfo]::InvariantCulture) -cne $ParentCommandSessionHandle -or
        ([IntPtr]::Size -eq 4 -and $commandHandleNumber -ge [uint32]::MaxValue) -or
        ([IntPtr]::Size -eq 8 -and $commandHandleNumber -ge [int64]::MaxValue)) {
        throw 'Parent Transition command-session handle must use canonical native-width syntax.'
    }
    throw 'Parent Transition qualified runtime import and command adoption are WIP; no command may run.'
}
'@
function Assert-AdmissionTest { param([bool] $Okay,[string] $Message) if (-not $Okay) {throw ('ADMISSION contract: '+$Message)} }
function Read-AdmissionAst {
    param([string] $Text)
    $tokens=$null;$errors=$null
    $ast=[Management.Automation.Language.Parser]::ParseInput($Text,[ref]$tokens,[ref]$errors)
    Assert-AdmissionTest (@($errors).Count -eq 0) 'Parse failed.'
    return [pscustomobject]@{ast=$ast;tokens=$tokens}
}
function Get-AdmissionTokenText {
    param([string] $Text)
    $parsed=Read-AdmissionAst $Text
    return [string]::Join("`n",[string[]]@($parsed.tokens | Where-Object {$_.Kind -cnotin @('Comment','NewLine','EndOfInput')} | ForEach-Object {$_.Text}))
}
function Get-AdmissionPreamble {
    param([string] $Text)
    $ast=(Read-AdmissionAst $Text).ast
    Assert-AdmissionTest ($null -eq $ast.BeginBlock -and $null -eq $ast.ProcessBlock -and $null -eq $ast.DynamicParamBlock -and $null -ne $ast.ParamBlock) 'Only the reviewed parameter/end-block script shape is allowed.'
    Assert-AdmissionTest ($ast.ParamBlock.Attributes.Count -eq 1 -and $ast.ParamBlock.Attributes[0].TypeName.FullName -ceq 'CmdletBinding' -and
        $ast.ParamBlock.Attributes[0].PositionalArguments.Count -eq 0 -and $ast.ParamBlock.Attributes[0].NamedArguments.Count -eq 0) 'Only the empty reviewed CmdletBinding header is allowed.'
    $expectedNames=@('Root','SessionId','OwnerPid','OwnerStartTimeUtc','DeadlineUtc','HeartbeatTimeoutSeconds','LeasePath','ReceiptPath',
        'HeartbeatPath','CommitPath','LiveAcceptancePath','PreStartAttestationPath','ExpectedPreStartSha256','StartFencePath',
        'ExpectedStartFenceSha256','StartRevokePath','StartAttemptPath','StartCompletionPath','PostgresContainer','ParentStageNonce','ParentCommandSessionHandle')
    Assert-AdmissionTest ($ast.ParamBlock.Parameters.Count -eq $expectedNames.Count) 'Exact domain plus two private parameter inventory differs.'
    for ($index=0;$index -lt $expectedNames.Count;$index++) {
        $parameter=$ast.ParamBlock.Parameters[$index]
        Assert-AdmissionTest ($parameter.Name.VariablePath.UserPath -ceq $expectedNames[$index] -and $null -eq $parameter.DefaultValue) 'Parameter order/default executable surface differs.'
        foreach ($attribute in $parameter.Attributes) {
            Assert-AdmissionTest ($attribute.TypeName.FullName -cin @('string','int','Parameter','ValidatePattern','ValidateRange')) 'Unreviewed parameter attribute/type.'
        }
    }
    $handle=$ast.ParamBlock.Parameters[20]
    Assert-AdmissionTest ($handle.Attributes.Count -eq 1 -and $handle.Attributes[0] -is [Management.Automation.Language.TypeConstraintAst] -and $handle.Attributes[0].TypeName.FullName -ceq 'string') 'Handle must be an optional native string with no default or executable validator.'
    foreach ($command in $ast.ParamBlock.FindAll({param($n) $n -is [Management.Automation.Language.CommandAst] -or $n -is [Management.Automation.Language.InvokeMemberExpressionAst] -or $n -is [Management.Automation.Language.ScriptBlockExpressionAst]},$true)) {
        throw 'ADMISSION contract: Executable parameter surface is forbidden.'
    }
    $statements=@($ast.EndBlock.Statements)
    Assert-AdmissionTest ($statements.Count -gt 7) 'Remaining watchdog body marker absent.'
    $types=@('PipelineAst','AssignmentStatementAst','AssignmentStatementAst','IfStatementAst','IfStatementAst','IfStatementAst')
    for ($index=0;$index -lt 6;$index++) { Assert-AdmissionTest ($statements[$index].GetType().Name -ceq $types[$index]) 'Admission top-level order differs.' }
    Assert-AdmissionTest ($statements[6].Extent.Text -ceq '$script:parentTransitionStartedAt = $null' -and
        $statements[7].Extent.Text -ceq '$script:parentTransitionTerminalAttempted = $false') 'Admission barrier must precede all remaining top-level state/body.'
    $start=$statements[0].Extent.StartOffset;$end=$statements[5].Extent.EndOffset
    $preamble=$Text.Substring($start,$end-$start)
    Assert-AdmissionTest ((Get-AdmissionTokenText $preamble) -ceq (Get-AdmissionTokenText $reviewedPreamble)) 'Exact reviewed admission grammar/order differs.'
    $prefixAst=(Read-AdmissionAst $preamble).ast
    foreach ($command in $prefixAst.FindAll({param($n) $n -is [Management.Automation.Language.CommandAst]},$true)) {
        Assert-AdmissionTest ($command.InvocationOperator -eq [Management.Automation.Language.TokenKind]::Unknown -and $command.GetCommandName() -ceq 'Set-StrictMode') 'Unreviewed preamble command.'
    }
    foreach ($member in $prefixAst.FindAll({param($n) $n -is [Management.Automation.Language.InvokeMemberExpressionAst]},$true)) {
        Assert-AdmissionTest ($member.Member -is [Management.Automation.Language.StringConstantExpressionAst] -and $member.Member.Value -cin @('ContainsKey','IsMatch','TryParse','ToString')) 'Unreviewed preamble member.'
    }
    foreach ($type in $prefixAst.FindAll({param($n) $n -is [Management.Automation.Language.TypeExpressionAst] -or $n -is [Management.Automation.Language.TypeConstraintAst]},$true)) {
        Assert-AdmissionTest ($type.TypeName.FullName -cin @('regex','uint64','uint32','int64','IntPtr','ref','Globalization.NumberStyles','Globalization.CultureInfo')) 'Unreviewed preamble type.'
    }
    foreach ($variable in $prefixAst.FindAll({param($n) $n -is [Management.Automation.Language.VariableExpressionAst]},$true)) {
        Assert-AdmissionTest ($variable.VariablePath.UserPath -cin @('ErrorActionPreference','script:parentTransitionMode','PSBoundParameters','ParentStageNonce','ParentCommandSessionHandle','commandHandleNumber')) 'Unreviewed preamble variable.'
    }
    Assert-AdmissionTest (@($prefixAst.FindAll({param($n) $n -is [Management.Automation.Language.RedirectionAst] -or $n -is [Management.Automation.Language.FunctionDefinitionAst] -or $n -is [Management.Automation.Language.TryStatementAst]},$true)).Count -eq 0) 'Preamble execution surface differs.'
    # Dot-sourcing establishes a fresh automatic PSBoundParameters. Install the
    # fixture map INSIDE the test block, then execute the six original unmodified
    # statements. This literal harness assignment is not target source/behavior.
    return [scriptblock]::Create('$PSBoundParameters=$fixtureBoundParameters'+"`n"+$preamble)
}
$preamble=Get-AdmissionPreamble $source
$results=[Collections.Generic.List[object]]::new()
function Case {
    param([string] $Name,[scriptblock] $Body)
    try {& $Body} catch {throw ('Admission case '+$Name+' failed: '+$_.Exception.Message)}
    $results.Add([pscustomobject]@{name=$Name;passed=$true})
}
function Invoke-PreambleFixture {
    param([hashtable] $Bound,[AllowNull()][string] $Nonce,[AllowNull()][string] $Handle)
    $fixtureBoundParameters=$Bound;$ParentStageNonce=$Nonce;$ParentCommandSessionHandle=$Handle
    . $preamble
    return [pscustomobject]@{legacy_syntax_only=$true;parent_mode=$script:parentTransitionMode;authorization=$false}
}
function Reject-Preamble {
    param([hashtable] $Bound,[AllowNull()][string] $Nonce,[AllowNull()][string] $Handle,[string] $Message)
    $failure=$null;try {Invoke-PreambleFixture $Bound $Nonce $Handle | Out-Null} catch {$failure=$_.Exception.Message}
    Assert-AdmissionTest ($failure -ceq $Message) ('Wrong rejection: '+$failure)
}
$pairMessage='Parent Transition nonce and command-session handle must be supplied together.'
$nonceMessage='Parent Transition mode requires an explicit lowercase 64-hex nonce.'
$handleMessage='Parent Transition command-session handle must use canonical native-width syntax.'
$wipMessage='Parent Transition qualified runtime import and command adoption are WIP; no command may run.'
$validNonce='a'*64;$both=@{ParentStageNonce=$true;ParentCommandSessionHandle=$true}
Case 'absent parent fields preserve legacy syntax only, no authorization' {
    $result=Invoke-PreambleFixture @{} $null $null
    Assert-AdmissionTest ($result.legacy_syntax_only -and -not $result.parent_mode -and -not $result.authorization) 'Legacy guard projection differs.'
}
Case 'absent dictionary keys do not promote ambient scalar values to admission' {
    $result=Invoke-PreambleFixture @{} $validNonce '4'
    Assert-AdmissionTest (-not $result.parent_mode -and -not $result.authorization) 'Ambient values admitted parent mode.'
}
$pairCases=@(
    @('nonce only',@{ParentStageNonce=$true},$validNonce,$null),
    @('empty nonce only',@{ParentStageNonce=$true},'',$null),
    @('handle only',@{ParentCommandSessionHandle=$true},$null,'4'),
    @('empty handle only',@{ParentCommandSessionHandle=$true},$null,'')
)
foreach ($entry in $pairCases) {Case ('pair rejection '+$entry[0]) {Reject-Preamble $entry[1] $entry[2] $entry[3] $pairMessage}}
$badNonces=@('',('a'*63),('a'*65),('A'*64),('g'*64),(' '+$validNonce),($validNonce+' '),($validNonce+"`n"),($validNonce+"`r"),$null)
for ($index=0;$index -lt $badNonces.Count;$index++) {Case ('nonce syntax rejection '+$index) {Reject-Preamble $both $badNonces[$index] '4' $nonceMessage}}
$badHandles=@('','0','00','01','+1','-1',' 1','1 ',"1`n","1`r",'1.0','1,000','0x10','1e3',[string][char]0x0661,[string][char]0xff14,
    '18446744073709551616','18446744073709551615','9223372036854775807','9223372036854775808',$null)
for ($index=0;$index -lt $badHandles.Count;$index++) {Case ('handle syntax rejection '+$index) {Reject-Preamble $both $validNonce $badHandles[$index] $handleMessage}}
$validHandles=@('1','4','8','2147483647','2147483648','4294967294')
foreach ($handle in $validHandles) {Case ('valid syntax still blocked before import '+$handle) {Reject-Preamble $both $validNonce $handle $wipMessage}}
foreach ($handle in @('4294967295','4294967296','9223372036854775806')) {
    Case ('native width boundary remains non-authorizing '+$handle) {
        $expected=if ([IntPtr]::Size -eq 4) {$handleMessage} else {$wipMessage}
        Reject-Preamble $both $validNonce $handle $expected
    }
}
$normalized=$source.Replace("`r`n","`n")
$barrier="    throw 'Parent Transition qualified runtime import and command adoption are WIP; no command may run.'"
$first="Set-StrictMode -Version Latest"
$marker='$script:parentTransitionStartedAt = $null'
$mutations=@(
    @('removed WIP',$normalized.Replace($barrier,'')),
    @('moved WIP after state',$normalized.Replace($barrier,'').Replace($marker,($marker+"`n"+$barrier))),
    @('conditional WIP',$normalized.Replace($barrier,('    if ($false) {'+$barrier+'}'))),
    @('return instead of WIP',$normalized.Replace($barrier,'    return')),
    @('Docker before guard',$normalized.Replace($first,('docker ps'+"`n"+$first))),
    @('Add-Type before guard',$normalized.Replace($first,('Add-Type -Path C:\unapproved.cs'+"`n"+$first))),
    @('assembly import before guard',$normalized.Replace($first,('[Reflection.Assembly]::Load([byte[]]@(1))'+"`n"+$first))),
    @('filesystem before guard',$normalized.Replace($first,('[IO.File]::ReadAllBytes("C:\unapproved")'+"`n"+$first))),
    @('process before guard',$normalized.Replace($first,('[Diagnostics.Process]::Start("bad.exe")'+"`n"+$first))),
    @('native adoption before guard',$normalized.Replace($first,('[Stratus.ParentStage.CommandSessionClient]::AdoptInherited("4")'+"`n"+$first))),
    @('dynamic command before guard',$normalized.Replace($first,('& "Get-Process"'+"`n"+$first))),
    @('parameter default command',$normalized.Replace('[string] $ParentCommandSessionHandle',('[string] $ParentCommandSessionHandle = $(Get-Process)'))),
    @('extra parameter',$normalized.Replace('[string] $ParentCommandSessionHandle',('[string] $ParentCommandSessionHandle, [string] $Unreviewed'))),
    @('pair condition weakened',$normalized.Replace("`$script:parentTransitionMode -ne `$PSBoundParameters.ContainsKey('ParentCommandSessionHandle')",'$false')),
    @('nonce condition weakened',$normalized.Replace("if (`$script:parentTransitionMode -and -not [regex]::IsMatch",'if ($false -and -not [regex]::IsMatch')),
    @('width bound weakened',$normalized.Replace('$commandHandleNumber -ge [int64]::MaxValue','$commandHandleNumber -gt [int64]::MaxValue')),
    @('culture changed',$normalized.Replace('[Globalization.CultureInfo]::InvariantCulture','[Globalization.CultureInfo]::CurrentCulture')),
    @('caught WIP',$normalized.Replace($barrier,('    try {'+$barrier+'} catch {}'))),
    @('IO between guard and state',$normalized.Replace($marker,('[IO.File]::ReadAllBytes("C:\unapproved")'+"`n"+$marker))),
    @('parse error',($normalized+"`nfunction {"))
)
foreach ($entry in $mutations) {
    Case ('closed source extraction rejects '+$entry[0]) {
        Assert-AdmissionTest ($entry[1] -cne $normalized) 'Mutation did not change source.'
        $failure=$null;try {Get-AdmissionPreamble $entry[1] | Out-Null} catch {$failure=$_.Exception.Message}
        Assert-AdmissionTest ($null -ne $failure -and $failure.StartsWith('ADMISSION contract:')) ('Unsafe source mutation survived: '+$failure)
    }
}
$expectedCases=66
Assert-AdmissionTest ($results.Count -eq $expectedCases) ('Fixed count differs: '+$results.Count)
[pscustomobject]@{format='stratus-transition-command-admission-tests-v1';expected=$expectedCases;executed=$results.Count;passed=$results.Count;failed=0
    ps_version=$PSVersionTable.PSVersion.ToString();ps_edition=$PSVersionTable.PSEdition;native_pointer_bytes=[IntPtr]::Size
    source_sha256_at_read=$sourceHash;scope='exact six-statement preamble only, fake supplied strings and presence map; remaining source AST-only'
    producer_body_executed=$false;assembly_imported=$false;handle_adopted=$false;native_executed=$false;docker_executed=$false
    legacy_body_qualified=$false;native_width_other_architecture_qualified=$false;parent_mode_authorized=$false;full_goal_accepted=$false
    cases=$results.ToArray()} | Microsoft.PowerShell.Utility\ConvertTo-Json -Depth 5 -Compress
