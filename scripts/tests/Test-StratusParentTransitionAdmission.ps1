[CmdletBinding()]
param([string] $WatchdogPath)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $PSBoundParameters.ContainsKey('WatchdogPath')) { $WatchdogPath = Join-Path $PSScriptRoot '..\Invoke-StratusParentStageWatchdog.ps1' }
# SAFE SCOPE: source/AST inspection, then only the exact reviewed launch tail
# and small containment helper, using an explicitly substituted inert type.
# The full watchdog, prerequisites, native runtime and all real launch methods
# are NEVER evaluated. Fake failures prove PowerShell retention/order only,
# not Windows creation, Job inheritance, process death, disposal or STOP.
$source = Get-Content -LiteralPath $WatchdogPath -Raw
$results = [Collections.Generic.List[object]]::new()
function Assert-Admission { param([bool] $Value,[string] $Message) if (-not $Value) { throw ('Admission contract: ' + $Message) } }
function Read-AdmissionAst {
    param([string] $Text)
    $tokens=$null; $errors=$null
    $tree=[Management.Automation.Language.Parser]::ParseInput($Text,[ref]$tokens,[ref]$errors)
    Assert-Admission (@($errors).Count -eq 0) 'Source parse failed.'
    return $tree
}
function Get-AdmissionFunction {
    param([object] $Tree,[string] $Name)
    $found=@($Tree.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $Name},$true))
    Assert-Admission ($found.Count -eq 1) ('Missing/ambiguous function: '+$Name)
    return $found[0]
}
function Get-AdmissionTokenSignature {
    param([string] $Text)
    $tokens=$null; $errors=$null
    [void][Management.Automation.Language.Parser]::ParseInput($Text,[ref]$tokens,[ref]$errors)
    Assert-Admission (@($errors).Count -eq 0) 'Token source parse failed.'
    return ((@($tokens | Where-Object {$_.Kind -notin @('Comment','NewLine','LineContinuation','EndOfInput')} |
        ForEach-Object {$_.Kind.ToString()+':'+$_.Text})) -join [char]31)
}
$expectedTail=@'
$script:transitionLaunch = [Stratus.ParentStage.IndependentTransitionLaunch]::Prepare(
    $contract.watchdog.host_path, $contract.watchdog.host_sha256, $plan.source_path, $plan.source_sha256,
    $contract.watchdog.runtime_path, $RuntimeSha256, $ContractPath, $ContractSha256, $plan.arguments, $environment)
$script:transitionLaunchAttempted = $true
Assert-ParentHeartbeat
$script:transitionLaunch.StartSuspended()
$script:transitionLaunch.AssertSuspended()
Assert-ParentHeartbeat
$script:transitionLaunchResumed = $true
$script:transitionLaunch.Resume()
'@
$expectedContainment=@'
function Assert-ParentTransitionCaptureContained {
    if ($null -eq $script:transitionLaunch) {
        if ($script:transitionLaunchAttempted -and -not $script:transitionLaunchAbortProven) {
            throw 'Transition creation outcome is ambiguous; missing capability is not containment proof.'
        }
        return
    }
    if (-not $script:transitionLaunchResumed) {
        $launch = $script:transitionLaunch
        $launch.AbortBeforeResume()
        if (-not [object]::ReferenceEquals($script:transitionLaunch, $launch)) {
            throw 'Transition abort lost the original retained launch capability.'
        }
        $launch.Dispose()
        if (-not [object]::ReferenceEquals($script:transitionLaunch, $launch)) {
            throw 'Transition release lost the original retained launch capability.'
        }
        $script:transitionLaunchAbortProven = $true
        $script:transitionLaunch = $null
        return
    }
    throw 'Independent Transition terminal/containment takeover is unqualified; retain guard, HBA and fences.'
}
'@
function Assert-AdmissionTail {
    param([string] $Text)
    $tree=Read-AdmissionAst $Text
    Assert-Admission ($tree.EndBlock.Statements.Count -eq 8) 'Exact eight-statement tail differs.'
    foreach ($command in $tree.FindAll({param($n) $n -is [Management.Automation.Language.CommandAst]},$true)) {
        Assert-Admission ($command.GetCommandName() -ceq 'Assert-ParentHeartbeat' -and
            $command.InvocationOperator -eq [Management.Automation.Language.TokenKind]::Unknown -and
            $command.CommandElements.Count -eq 1 -and $command.Redirections.Count -eq 0) 'Unreviewed tail command.'
    }
    $members=@($tree.FindAll({param($n) $n -is [Management.Automation.Language.InvokeMemberExpressionAst]},$true))
    Assert-Admission ($members.Count -eq 4) 'Tail member count differs.'
    foreach ($member in $members) {
        Assert-Admission ($member.Member -is [Management.Automation.Language.StringConstantExpressionAst]) 'Dynamic tail member.'
        if ($member.Static) {
            Assert-Admission ($member.Expression.Extent.Text -ceq '[Stratus.ParentStage.IndependentTransitionLaunch]' -and
                $member.Member.Value -ceq 'Prepare' -and $member.Arguments.Count -eq 10) 'Only exact managed Prepare factory is permitted.'
        }
        else {
            Assert-Admission ($member.Expression.Extent.Text -ceq '$script:transitionLaunch' -and
                $member.Member.Value -cin @('StartSuspended','AssertSuspended','Resume') -and
                ($null -eq $member.Arguments -or $member.Arguments.Count -eq 0)) 'Substituted tail capability or method.'
        }
    }
    foreach ($type in $tree.FindAll({param($n) $n -is [Management.Automation.Language.TypeExpressionAst]},$true)) {
        Assert-Admission ($type.TypeName.FullName -ceq 'Stratus.ParentStage.IndependentTransitionLaunch') 'Unreviewed tail type.'
    }
    Assert-Admission ((Get-AdmissionTokenSignature $Text) -ceq (Get-AdmissionTokenSignature $expectedTail)) 'Reviewed tail token/order/argument contract differs.'
}
function Assert-AdmissionContainment {
    param([string] $Text)
    $tree=Read-AdmissionAst $Text
    $fn=Get-AdmissionFunction $tree 'Assert-ParentTransitionCaptureContained'
    Assert-Admission (@($fn.FindAll({param($n) $n -is [Management.Automation.Language.CommandAst]},$true)).Count -eq 0) 'Unreviewed containment command.'
    foreach ($type in $fn.FindAll({param($n) $n -is [Management.Automation.Language.TypeExpressionAst]},$true)) {
        Assert-Admission ($type.TypeName.FullName -ceq 'object') 'Unreviewed containment type.'
    }
    foreach ($member in $fn.FindAll({param($n) $n -is [Management.Automation.Language.InvokeMemberExpressionAst]},$true)) {
        Assert-Admission ($member.Member -is [Management.Automation.Language.StringConstantExpressionAst]) 'Dynamic containment member.'
        if ($member.Static) { Assert-Admission ($member.Extent.Text -ceq '[object]::ReferenceEquals($script:transitionLaunch, $launch)') 'Unreviewed containment static call.' }
        else { Assert-Admission ($member.Expression.Extent.Text -ceq '$launch' -and $member.Member.Value -cin @('AbortBeforeResume','Dispose') -and
            ($null -eq $member.Arguments -or $member.Arguments.Count -eq 0)) 'Unreviewed containment instance call.' }
    }
    Assert-Admission ((Get-AdmissionTokenSignature $Text) -ceq (Get-AdmissionTokenSignature $expectedContainment)) 'Reviewed containment token/order contract differs.'
}
function Get-AdmissionTail {
    param([string] $Text)
    $fn=Get-AdmissionFunction (Read-AdmissionAst $Text) 'Start-ParentTransitionLaunch'
    $statements=@($fn.Body.EndBlock.Statements)
    $retained=@($statements | Where-Object {$_ -is [Management.Automation.Language.AssignmentStatementAst] -and $_.Left.Extent.Text -ceq '$script:transitionLaunch'})
    Assert-Admission ($retained.Count -eq 1) 'Exactly one direct retained launch assignment is required.'
    $start=$retained[0].Extent.StartOffset; $end=$statements[-1].Extent.EndOffset
    $tail=$Text.Substring($start,$end-$start)
    Assert-AdmissionTail $tail
    return $tail
}
function Invoke-AdmissionCase {
    param([string] $Name,[scriptblock] $Body)
    try { & $Body } catch { throw ('Admission case '+$Name+' failed: '+$_.Exception.Message) }
    $results.Add([pscustomobject]@{name=$Name;passed=$true})
}
function Assert-AdmissionRejected {
    param([scriptblock] $Body,[string] $Pattern)
    $failure=$null; try { & $Body | Out-Null } catch { $failure=$_.Exception.Message }
    Assert-Admission ($null -ne $failure -and $failure -match $Pattern) ('Expected rejection '+$Pattern+'; observed '+$failure)
}
$tree=Read-AdmissionAst $source
$actualTail=Get-AdmissionTail $source
$actualContainment=(Get-AdmissionFunction $tree 'Assert-ParentTransitionCaptureContained').Extent.Text
Assert-AdmissionContainment $actualContainment
Invoke-AdmissionCase 'actual tail has exact retained Prepare then native Start and Resume order' { Assert-AdmissionTail $actualTail }
Invoke-AdmissionCase 'actual cleanup retains one original reference across abort and disposal' { Assert-AdmissionContainment $actualContainment }
Invoke-AdmissionCase 'whole launch is never extracted and plan prevents retry after managed prepare failure' {
    $fn=Get-AdmissionFunction $tree 'Start-ParentTransitionLaunch'
    Assert-Admission ($fn.Body.EndBlock.Statements[0].Extent.Text.Contains('$script:transitionLaunchAttempted -or $null -ne $script:transitionLaunch -or $null -ne $script:transitionLaunchPlan')) 'Original one-shot plan guard differs.'
    $plan=@($fn.Body.EndBlock.Statements | Where-Object {$_ -is [Management.Automation.Language.AssignmentStatementAst] -and $_.Left.Extent.Text -ceq '$script:transitionLaunchPlan'})
    Assert-Admission ($plan.Count -eq 1 -and $plan[0].Right.Extent.Text -ceq '$plan' -and $plan[0].Extent.EndOffset -lt $source.IndexOf($actualTail,[StringComparison]::Ordinal)) 'Plan is not latched before managed Prepare.'
    Assert-Admission (-not $actualTail.Contains('Invoke-ParentDocker') -and -not $actualTail.Contains('Read-ParentHeldBytes') -and -not $actualTail.Contains('Assert-ParentEngine')) 'Prerequisite body leaked into executable fixture.'
}
$tailMutations=@(
    @('legacy static native constructor','::Prepare(','::StartSuspended('),
    @('retention replaced by local variable','$script:transitionLaunch =','$temporaryLaunch ='),
    @('native attempt not latched','$script:transitionLaunchAttempted = $true','$script:transitionLaunchAttempted = $false'),
    @('prepare assigned after native start','$script:transitionLaunch = [Stratus.ParentStage.IndependentTransitionLaunch]::Prepare(',"`$script:transitionLaunch.StartSuspended()`n`$script:transitionLaunch = [Stratus.ParentStage.IndependentTransitionLaunch]::Prepare("),
    @('missing heartbeat admission','Assert-ParentHeartbeat',''),
    @('substituted native receiver','$script:transitionLaunch.StartSuspended()','$other.StartSuspended()'),
    @('missing suspended proof','$script:transitionLaunch.AssertSuspended()','$null'),
    @('resume uncertainty latch removed','$script:transitionLaunchResumed = $true','$script:transitionLaunchResumed = $false'),
    @('substituted resume receiver','$script:transitionLaunch.Resume()','$other.Resume()'),
    @('unexpected executable command','$script:transitionLaunch.Resume()',"`$script:transitionLaunch.Resume()`nGet-Process"),
    @('unexpected side effect in argument','$RuntimeSha256','$(Get-Process)'),
    @('unexpected static type','[Stratus.ParentStage.IndependentTransitionLaunch]','[IO.File]'))
foreach ($row in $tailMutations) {
    Invoke-AdmissionCase ('tail mutation rejected: '+$row[0]) {
        Assert-Admission ($actualTail.Contains($row[1])) 'Missing tail mutation target.'
        Assert-AdmissionRejected {Assert-AdmissionTail ($actualTail.Replace($row[1],$row[2]))} 'Admission contract:'
    }
}
$argumentExpressions=@('$contract.watchdog.host_path','$contract.watchdog.host_sha256','$plan.source_path','$plan.source_sha256',
    '$contract.watchdog.runtime_path','$RuntimeSha256','$ContractPath','$ContractSha256','$plan.arguments','$environment')
foreach ($argumentExpression in $argumentExpressions) {
    Invoke-AdmissionCase ('exact original factory argument retained: '+$argumentExpression) {
        Assert-Admission ($actualTail.Contains($argumentExpression)) 'Missing factory argument mutation target.'
        Assert-AdmissionRejected {Assert-AdmissionTail ($actualTail.Replace($argumentExpression,'$wrongArgument'))} 'Admission contract:'
    }
}
$containmentMutations=@(
    @('abort original reference','$launch.AbortBeforeResume()','$script:transitionLaunch.AbortBeforeResume()'),
    @('dispose original reference','$launch.Dispose()','$script:transitionLaunch.Dispose()'),
    @('reference checks removed','[object]::ReferenceEquals($script:transitionLaunch, $launch)','$true'),
    @('capture original replaced','$launch = $script:transitionLaunch','$launch = $other'),
    @('abort proof incorrectly latched','$script:transitionLaunchAbortProven = $true','$script:transitionLaunchAbortProven = $false'),
    @('retained slot cleared before abort','$launch.AbortBeforeResume()',"`$script:transitionLaunch = `$null`n`$launch.AbortBeforeResume()"),
    @('resumed guard enters abort','if (-not $script:transitionLaunchResumed)','if ($true)'),
    @('ambiguous missing capability accepted','$script:transitionLaunchAttempted -and -not $script:transitionLaunchAbortProven','$false'))
foreach ($row in $containmentMutations) {
    Invoke-AdmissionCase ('cleanup mutation rejected: '+$row[0]) {
        Assert-Admission ($actualContainment.Contains($row[1])) 'Missing containment mutation target.'
        Assert-AdmissionRejected {Assert-AdmissionContainment ($actualContainment.Replace($row[1],$row[2]))} 'Admission contract:'
    }
}
# Compile ONLY this literal, pure test fixture. Never the source under test.
Add-Type -TypeDefinition @'
namespace StratusTransitionAdmissionTest {
    public sealed class FakeLaunch {
        public static FakeLaunch Returned;
        public static object[] LastArguments;
        public static System.Action<string,FakeLaunch> Hook;
        public static FakeLaunch Prepare(object a,object b,object c,object d,object e,object f,object g,object h,object i,object j) {
            LastArguments=new object[] {a,b,c,d,e,f,g,h,i,j}; Hook("prepare",null); return Returned;
        }
        public void StartSuspended() { Hook("start",this); }
        public void AssertSuspended() { Hook("suspended",this); }
        public void Resume() { Hook("resume",this); }
        public void AbortBeforeResume() { Hook("abort",this); }
        public void Dispose() { Hook("dispose",this); }
    }
}
'@
# These are the only evaluated source fragments. Token identity and closed
# AST allowlists above are checked BEFORE this explicit one-type substitution.
$tailBlock=[scriptblock]::Create($actualTail.Replace('[Stratus.ParentStage.IndependentTransitionLaunch]','[StratusTransitionAdmissionTest.FakeLaunch]'))
. ([scriptblock]::Create($actualContainment))
function Reset-AdmissionFixture {
    $script:transitionLaunch=$null; $script:transitionLaunchAttempted=$false; $script:transitionLaunchResumed=$false
    $script:transitionLaunchAbortProven=$false; $script:transitionLaunchPlan=[pscustomobject]@{prepared=$true}
    $script:events=[Collections.Generic.List[string]]::new(); $script:failureStage=''; $script:swapStage=''; $script:heartbeatCount=0
    $script:contract=[pscustomobject]@{watchdog=[pscustomobject]@{host_path='host-path';host_sha256='host-hash';runtime_path='runtime-path'}}
    $script:plan=[pscustomobject]@{source_path='source-path';source_sha256='source-hash';arguments=[Collections.Generic.Dictionary[string,string]]::new()}
    $script:environment=[Collections.Generic.Dictionary[string,string]]::new()
    $script:RuntimeSha256='runtime-hash'; $script:ContractPath='contract-path'; $script:ContractSha256='contract-hash'
    $script:fake=[StratusTransitionAdmissionTest.FakeLaunch]::new()
    [StratusTransitionAdmissionTest.FakeLaunch]::Returned=$script:fake
    [StratusTransitionAdmissionTest.FakeLaunch]::LastArguments=$null
    [StratusTransitionAdmissionTest.FakeLaunch]::Hook={param($event,$capability)
        $script:events.Add($event)
        if ($event -ceq 'prepare') {
            Assert-Admission (-not $script:transitionLaunchAttempted -and $null -eq $script:transitionLaunch) 'Prepare acquired native-attempt or retained authority early.'
        }
        elseif ($event -cin @('start','suspended','resume')) {
            Assert-Admission ([object]::ReferenceEquals($capability,$script:transitionLaunch) -and $script:transitionLaunchAttempted) 'Native-method fake observed missing original retention/attempt.'
            Assert-Admission (($event -ceq 'resume') -eq $script:transitionLaunchResumed) 'Resume latch is not exact before fake method.'
        }
        if ($event -ceq $script:swapStage) { $script:transitionLaunch=[StratusTransitionAdmissionTest.FakeLaunch]::new() }
        if ($event -ceq $script:failureStage) { throw ('inert '+$event+' failure') }
    }
}
function Assert-ParentHeartbeat {
    $script:heartbeatCount++; $event='heartbeat'+$script:heartbeatCount; $script:events.Add($event)
    Assert-Admission ([object]::ReferenceEquals($script:fake,$script:transitionLaunch)) 'Heartbeat preceded exact retention.'
    if ($event -ceq $script:failureStage) { throw ('inert '+$event+' failure') }
}
Invoke-AdmissionCase 'managed happy path observes exact retained object before every fake native step' {
    Reset-AdmissionFixture; & $tailBlock
    Assert-Admission (($script:events -join ',') -ceq 'prepare,heartbeat1,start,suspended,heartbeat2,resume') 'Managed event order differs.'
    Assert-Admission ([object]::ReferenceEquals($script:fake,$script:transitionLaunch) -and $script:transitionLaunchResumed -and -not $script:transitionLaunchAbortProven) 'Managed success changed lifetime/cleanup authority.'
}
Invoke-AdmissionCase 'managed factory receives all ten original values with plan and environment identity' {
    Reset-AdmissionFixture; & $tailBlock
    $argsReceived=[StratusTransitionAdmissionTest.FakeLaunch]::LastArguments
    Assert-Admission ($argsReceived.Count -eq 10 -and ($argsReceived[0..7] -join ',') -ceq 'host-path,host-hash,source-path,source-hash,runtime-path,runtime-hash,contract-path,contract-hash') 'Factory values/order differ.'
    Assert-Admission ([object]::ReferenceEquals($argsReceived[8],$script:plan.arguments) -and [object]::ReferenceEquals($argsReceived[9],$script:environment)) 'Plan/environment were copied or substituted by parent tail.'
}
Invoke-AdmissionCase 'managed prepare failure has no native attempt but retains one-shot outer plan' {
    Reset-AdmissionFixture; $script:failureStage='prepare'; Assert-AdmissionRejected {& $tailBlock} 'inert prepare failure'
    Assert-Admission ($null -eq $script:transitionLaunch -and -not $script:transitionLaunchAttempted -and -not $script:transitionLaunchResumed -and $null -ne $script:transitionLaunchPlan -and ($script:events -join ',') -ceq 'prepare') 'Prepare failure changed native state or plan latch.'
    Assert-ParentTransitionCaptureContained
    Assert-Admission (-not $script:transitionLaunchAbortProven) 'No-attempt branch fabricated completed native cleanup.'
}
foreach ($stage in @('heartbeat1','start','suspended','heartbeat2')) {
    Invoke-AdmissionCase ('pre-resume failure retains original capability: '+$stage) {
        Reset-AdmissionFixture; $script:failureStage=$stage; Assert-AdmissionRejected {& $tailBlock} ('inert '+$stage+' failure')
        Assert-Admission ([object]::ReferenceEquals($script:fake,$script:transitionLaunch) -and $script:transitionLaunchAttempted -and -not $script:transitionLaunchResumed -and -not $script:transitionLaunchAbortProven -and -not $script:events.Contains('resume')) 'Pre-resume failure lost exact lifetime or latches.'
        $script:failureStage=''; Assert-ParentTransitionCaptureContained
        Assert-Admission ($script:transitionLaunchAbortProven -and $null -eq $script:transitionLaunch) 'Inert pre-resume cleanup failed.'
    }
}
Invoke-AdmissionCase 'uncertain resume failure retains guard and refuses cleanup even without terminal proof' {
    Reset-AdmissionFixture; $script:failureStage='resume'; Assert-AdmissionRejected {& $tailBlock} 'inert resume failure'
    $before=$script:events.Count; Assert-AdmissionRejected {Assert-ParentTransitionCaptureContained} 'takeover is unqualified'
    Assert-Admission ($script:transitionLaunchResumed -and [object]::ReferenceEquals($script:fake,$script:transitionLaunch) -and $script:events.Count -eq $before -and -not $script:transitionLaunchAbortProven) 'Resumed uncertainty retired fake guard.'
}
foreach ($stage in @('abort','dispose')) {
    Invoke-AdmissionCase ('cleanup failure retains original and never latches proof: '+$stage) {
        Reset-AdmissionFixture; $script:failureStage='start'; Assert-AdmissionRejected {& $tailBlock} 'inert start failure'
        $script:failureStage=$stage; Assert-AdmissionRejected {Assert-ParentTransitionCaptureContained} ('inert '+$stage+' failure')
        Assert-Admission ([object]::ReferenceEquals($script:fake,$script:transitionLaunch) -and -not $script:transitionLaunchAbortProven) 'Cleanup failure released original or promoted proof.'
        $script:failureStage=''; Assert-ParentTransitionCaptureContained; $after=$script:events.Count; Assert-ParentTransitionCaptureContained
        Assert-Admission ($script:transitionLaunchAbortProven -and $null -eq $script:transitionLaunch -and $after -eq $script:events.Count) 'Completed fake cleanup is not idempotently latched.'
    }
}
foreach ($stage in @('abort','dispose')) {
    Invoke-AdmissionCase ('cleanup same-type slot substitution rejected after '+$stage) {
        Reset-AdmissionFixture; $script:failureStage='start'; Assert-AdmissionRejected {& $tailBlock} 'inert start failure'
        $script:failureStage=''; $script:swapStage=$stage
        Assert-AdmissionRejected {Assert-ParentTransitionCaptureContained} 'lost the original retained launch capability'
        Assert-Admission (-not $script:transitionLaunchAbortProven -and $null -ne $script:transitionLaunch) 'Substitution fabricated cleanup proof or cleared retained slot.'
        if ($stage -ceq 'abort') { Assert-Admission (-not $script:events.Contains('dispose')) 'Disposal followed failed abort-reference proof.' }
    }
}
Invoke-AdmissionCase 'missing retained capability after native attempt is not containment proof' {
    Reset-AdmissionFixture; $script:transitionLaunchAttempted=$true
    Assert-AdmissionRejected {Assert-ParentTransitionCaptureContained} 'creation outcome is ambiguous'
    Assert-Admission (-not $script:transitionLaunchAbortProven -and $script:events.Count -eq 0) 'Ambiguous branch invoked cleanup or granted proof.'
}
Invoke-AdmissionCase 'successfully resumed fake guard remains retained behind takeover barrier' {
    Reset-AdmissionFixture; & $tailBlock; $before=$script:events.Count
    Assert-AdmissionRejected {Assert-ParentTransitionCaptureContained} 'takeover is unqualified'
    Assert-Admission ([object]::ReferenceEquals($script:fake,$script:transitionLaunch) -and $script:events.Count -eq $before) 'Resumed guard lost retention.'
}
$expectedCases=47
Assert-Admission ($results.Count -eq $expectedCases) ('Fixed case count differs: '+$results.Count)
[pscustomobject]@{suite='parent-transition-admission-ast-inert';expected=$expectedCases;executed=$results.Count;passed=$results.Count;failed=0
    watchdog_sha256=(Get-FileHash -LiteralPath $WatchdogPath -Algorithm SHA256).Hash.ToLowerInvariant()
    test_sha256=(Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
    native_executed=$false;runtime_compiled=$false;watchdog_body_executed=$false;docker_executed=$false;filesystem_fixtures_written=$false
    literal_inert_type_compiled=$true;actual_tail_type_remapped=$true;full_protocol_accepted=$false;results=@($results.ToArray())} | ConvertTo-Json -Depth 5 -Compress
