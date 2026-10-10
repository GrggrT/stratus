[CmdletBinding()]
param([string] $WatchdogPath)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $WatchdogPath) { $WatchdogPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'Invoke-StratusParentStageWatchdog.ps1' }
# Execute ONLY the selected containment decision helper with all six external
# commands replaced by in-memory stubs. No watchdog body, Docker, filesystem
# fixture, native process, fence, deadline or causal daemon behavior is exercised.
# This is a regression for decision ordering, not containment qualification.
$source = Get-Content -LiteralPath $WatchdogPath -Raw
function Get-CausalStopTestHelper {
    param([string] $Text)
    $tokens=$null; $errors=$null
    $ast=[Management.Automation.Language.Parser]::ParseInput($Text,[ref]$tokens,[ref]$errors)
    if (@($errors).Count -ne 0) { throw 'Selected helper parse failed.' }
    $found=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $n.Name -ceq 'Invoke-ParentContainerContainmentRound'},$true))
    if ($found.Count -ne 1) { throw 'Exactly one selected helper is required.' }
    $fn=$found[0]
    if (@($fn.Body.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)).Count -ne 0) {
        throw 'Nested helper definitions are forbidden.'
    }
    foreach ($command in $fn.FindAll({param($n) $n -is [Management.Automation.Language.CommandAst]},$true)) {
        if ($command.InvocationOperator -ne [Management.Automation.Language.TokenKind]::Unknown -or
            $command.GetCommandName() -cnotmatch '\A(?:Test-Path|Test-ParentValidatedCompletionUnchanged|Assert-ParentEngine|Get-ParentContainmentContainer|Invoke-ParentDocker|Add-ParentFailure)\z') {
            throw 'Unreviewed selected helper command.'
        }
    }
    if (@($fn.FindAll({param($n) $n -is [Management.Automation.Language.InvokeMemberExpressionAst] -or
        $n -is [Management.Automation.Language.RedirectionAst]},$true)).Count -ne 0) { throw 'Member invocation or redirection is forbidden.' }
    foreach ($type in $fn.FindAll({param($n) $n -is [Management.Automation.Language.TypeExpressionAst] -or
        $n -is [Management.Automation.Language.TypeConstraintAst]},$true)) {
        if ($type.TypeName.FullName -cnotin @('Collections.IDictionary','string')) { throw 'Unreviewed selected helper type.' }
    }
    foreach ($variable in $fn.FindAll({param($n) $n -is [Management.Automation.Language.VariableExpressionAst]},$true)) {
        if ($variable.VariablePath.UserPath -cnotin @('_','attempted','candidate','completePath','false','id','kind','null','paths',
            'Progress','proof','startSeen','state','stop','true','unresolvedStart')) { throw 'Unreviewed selected helper variable.' }
    }
    return $fn.Extent.Text
}
$helperSource=Get-CausalStopTestHelper $source
. ([scriptblock]::Create($helperSource))
$results=[Collections.Generic.List[object]]::new()
function Assert-CausalStopTest { param([bool] $Condition,[string] $Message) if (-not $Condition) { throw $Message } }
function Invoke-CausalStopCase {
    param([string] $Name,[scriptblock] $Body)
    try { & $Body } catch { throw ('Causal STOP case '+$Name+' failed: '+$_.Exception.Message) }
    $results.Add([pscustomobject]@{name=$Name;passed=$true})
}
function Assert-CausalStopRejected {
    param([scriptblock] $Body,[string] $Pattern)
    $failure=$null; try { & $Body | Out-Null } catch { $failure=$_.Exception.Message }
    if ($null -eq $failure -or $failure -notmatch $Pattern) { throw ('Expected rejection '+$Pattern+'; observed '+$failure) }
}
function New-CausalStopContainer {
    param([string] $Kind,[bool] $Running=$false,[int] $PidValue=0,[string] $Status='exited')
    return [pscustomobject]@{Id=$script:ids[$Kind];State=[pscustomobject]@{Running=$Running;Pid=$PidValue;Status=$Status}}
}
function Reset-CausalStopFixture {
    $script:ids=@{provision=('a'*64);api=('b'*64);postgres=('c'*64)}
    $script:paths=[pscustomobject]@{provision_attempt='memory:provision-attempt';provision_complete='memory:provision-complete'
        api_attempt='memory:api-create-attempt';api_start_attempt='memory:api-start-attempt';api_start_complete='memory:api-complete'
        transition_start_attempt='memory:postgres-attempt';transition_start_completion='memory:postgres-complete'}
    $script:present=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $script:progress=[ordered]@{}; $script:observations=@{}; $script:reads=@{}; $script:completed=@{}
    $script:stopOutputs=@{}; $script:engineFailures=@{}; $script:activeKind=$null
    $script:stopCalls=[Collections.Generic.List[object]]::new(); $script:failures=[Collections.Generic.List[string]]::new()
    foreach ($kind in @('provision','api','postgres')) {
        $script:progress[$kind]=[ordered]@{stopped=$false;result=$null;attempt_seen=$false;start_seen=$false;materialized=$false}
        $script:observations[$kind]=[object[]]@((New-CausalStopContainer $kind),(New-CausalStopContainer $kind))
        $script:reads[$kind]=0; $script:completed[$kind]=$false; $script:stopOutputs[$kind]=$script:ids[$kind]
        $script:engineFailures[$kind]=$false
    }
}
function Set-CausalStopAttempt {
    param([string] $Kind,[switch] $CompletionOnly)
    $path=if ($CompletionOnly) {
        if ($Kind -ceq 'postgres') { $paths.transition_start_completion } elseif ($Kind -ceq 'api') { $paths.api_start_complete } else { $paths.provision_complete }
    } else {
        if ($Kind -ceq 'postgres') { $paths.transition_start_attempt } elseif ($Kind -ceq 'api') { $paths.api_start_attempt } else { $paths.provision_attempt }
    }
    [void]$script:present.Add($path)
}
# The selected helper resolves these six local names, never their real producers.
function Test-Path {
    param([string] $LiteralPath)
    if ($LiteralPath -cnotin @($paths.PSObject.Properties | ForEach-Object {$_.Value})) { throw 'Unexpected in-memory artifact key.' }
    return $script:present.Contains($LiteralPath)
}
function Test-ParentValidatedCompletionUnchanged { param([string] $Kind) return $script:completed[$Kind] }
function Assert-ParentEngine {
    # The helper visits exactly three kinds in this fixed order once per round.
    $index=$script:engineCallCount % 3; $script:engineCallCount+=1
    $script:activeKind=@('provision','api','postgres')[$index]
    if ($script:engineFailures[$script:activeKind]) { throw 'Stub engine failure.' }
}
function Get-ParentContainmentContainer {
    param([string] $Kind,[switch] $AllowAbsent)
    if ($Kind -cnotin @('provision','api','postgres')) { throw 'Unexpected in-memory container kind.' }
    $index=[int]$script:reads[$Kind]; $script:reads[$Kind]=$index+1
    $items=$script:observations[$Kind]; $value=$items[[Math]::Min($index,$items.Count-1)]
    if ($value -is [string]) { throw $value }
    return $value
}
function Invoke-ParentDocker {
    param([string[]] $Arguments,[int] $TimeoutMilliseconds)
    $kind=$script:activeKind
    if ($Arguments.Count -ne 4 -or $Arguments[0] -cne 'stop' -or $Arguments[1] -cne '--timeout' -or
        $Arguments[2] -cne '15' -or $Arguments[3] -cne $script:ids[$kind] -or $TimeoutMilliseconds -ne 25000) {
        throw 'Stub only permits the exact existing bounded STOP signature.'
    }
    $script:stopCalls.Add([pscustomobject]@{kind=$kind;arguments=@($Arguments);timeout=$TimeoutMilliseconds})
    if ($script:stopOutputs[$kind] -ceq 'throw') { throw 'Stub STOP failure.' }
    return $script:stopOutputs[$kind]
}
function Add-ParentFailure { param([string] $Message) $script:failures.Add($Message) }
function Invoke-CausalStopRound { $script:engineCallCount=0; return Invoke-ParentContainerContainmentRound $script:progress }
function Get-CausalStopCount { param([string] $Kind) return @($script:stopCalls | Where-Object {$_.kind -ceq $Kind}).Count }
function Assert-CausalStopPending {
    param([string] $Kind,[bool] $Result)
    Assert-CausalStopTest (-not $Result -and -not $script:progress[$Kind].stopped -and $null -eq $script:progress[$Kind].result) 'Unresolved container was admitted.'
    foreach ($other in @('provision','api','postgres')) {
        if ($other -cne $Kind) { Assert-CausalStopTest $script:progress[$other].stopped 'Another container was starved.' }
    }
}
$common=@('clean stopped','accepted completion stopped','unresolved never materialized','unresolved materialized stopped',
    'unresolved running','STOP failure','STOP wrong identity','fresh running','fresh nonzero PID','fresh restarting',
    'fresh wrong identity','fresh read failure','initial read failure','engine failure','completion without attempt')
foreach ($kind in @('provision','api','postgres')) {
    foreach ($scenario in $common) {
        Invoke-CausalStopCase ($kind+': '+$scenario) {
            Reset-CausalStopFixture
            if ($scenario -cne 'clean stopped') { Set-CausalStopAttempt $kind }
            switch -CaseSensitive ($scenario) {
                'accepted completion stopped' { $script:completed[$kind]=$true }
                'unresolved materialized stopped' { $script:progress[$kind].materialized=$true }
                'unresolved running' { $script:observations[$kind][0]=New-CausalStopContainer $kind $true 42 'running' }
                'STOP failure' { $script:progress[$kind].materialized=$true; $script:stopOutputs[$kind]='throw' }
                'STOP wrong identity' { $script:progress[$kind].materialized=$true; $script:stopOutputs[$kind]='d'*64 }
                'fresh running' { $script:progress[$kind].materialized=$true; $script:observations[$kind][1]=New-CausalStopContainer $kind $true 0 'exited' }
                'fresh nonzero PID' { $script:progress[$kind].materialized=$true; $script:observations[$kind][1]=New-CausalStopContainer $kind $false 42 'exited' }
                'fresh restarting' { $script:progress[$kind].materialized=$true; $script:observations[$kind][1]=New-CausalStopContainer $kind $false 0 'restarting' }
                'fresh wrong identity' { $script:progress[$kind].materialized=$true; $script:observations[$kind][1].Id='d'*64 }
                'fresh read failure' { $script:progress[$kind].materialized=$true; $script:observations[$kind][1]='Stub fresh read failed.' }
                'initial read failure' { $script:observations[$kind][0]='Stub initial read failed.' }
                'engine failure' { $script:engineFailures[$kind]=$true }
                'completion without attempt' { $script:present.Clear(); Set-CausalStopAttempt $kind -CompletionOnly }
            }
            $result=Invoke-CausalStopRound
            if ($scenario -cin @('clean stopped','accepted completion stopped')) {
                Assert-CausalStopTest ($result -and $script:progress[$kind].result -ceq 'exact_stopped' -and
                    (Get-CausalStopCount $kind) -eq 0 -and $script:reads[$kind] -eq 1) 'Stopped fast path differs.'
            } elseif ($scenario -cin @('unresolved materialized stopped','unresolved running')) {
                Assert-CausalStopTest ($result -and $script:progress[$kind].result -ceq 'causal_exact_stop' -and
                    (Get-CausalStopCount $kind) -eq 1 -and $script:reads[$kind] -eq 2) 'Successful STOP did not require fresh proof.'
            } else {
                Assert-CausalStopPending $kind $result
                $expectedStops=if ($scenario -cin @('STOP failure','STOP wrong identity','fresh running','fresh nonzero PID','fresh restarting','fresh wrong identity','fresh read failure')) {1} else {0}
                Assert-CausalStopTest ((Get-CausalStopCount $kind) -eq $expectedStops) 'STOP attempt count differs.'
            }
        }
    }
}
Invoke-CausalStopCase 'API create attempt alone is not a start attempt' {
    Reset-CausalStopFixture; [void]$script:present.Add($paths.api_attempt)
    $result=Invoke-CausalStopRound
    Assert-CausalStopTest ($result -and $script:progress.api.attempt_seen -and -not $script:progress.api.start_seen -and (Get-CausalStopCount 'api') -eq 0) 'API create/start distinction changed.'
}
Invoke-CausalStopCase 'API start attempt alone requires causal STOP after materialization' {
    Reset-CausalStopFixture; [void]$script:present.Add($paths.api_start_attempt); $script:progress.api.materialized=$true
    Assert-CausalStopTest ((Invoke-CausalStopRound) -and $script:progress.api.start_seen -and (Get-CausalStopCount 'api') -eq 1) 'API start attempt was ignored.'
}
Invoke-CausalStopCase 'never attempted absent application containers' {
    Reset-CausalStopFixture; $script:observations.provision=[object[]]@($null); $script:observations.api=[object[]]@($null)
    Assert-CausalStopTest ((Invoke-CausalStopRound) -and $script:progress.provision.result -ceq 'never_attempted_absent' -and $script:progress.api.result -ceq 'never_attempted_absent') 'Never-attempted absence changed.'
}
Invoke-CausalStopCase 'attempted absent application containers remain unresolved' {
    Reset-CausalStopFixture; Set-CausalStopAttempt 'provision'; Set-CausalStopAttempt 'api'
    $script:observations.provision=[object[]]@($null); $script:observations.api=[object[]]@($null)
    Assert-CausalStopTest (-not (Invoke-CausalStopRound) -and -not $script:progress.provision.stopped -and -not $script:progress.api.stopped -and $script:progress.postgres.stopped) 'Attempted absence was admitted or starved PostgreSQL.'
}
Invoke-CausalStopCase 'attempt and materialization history survive artifact disappearance' {
    Reset-CausalStopFixture; Set-CausalStopAttempt 'postgres'; $script:observations.postgres[0]=New-CausalStopContainer 'postgres' $true 42 'running'; $script:stopOutputs.postgres='throw'
    Assert-CausalStopPending 'postgres' (Invoke-CausalStopRound)
    $script:present.Clear(); $script:stopOutputs.postgres=$script:ids.postgres
    Assert-CausalStopTest ((Invoke-CausalStopRound) -and $script:progress.postgres.start_seen -and $script:progress.postgres.materialized -and (Get-CausalStopCount 'postgres') -eq 2 -and $script:reads.postgres -eq 3) 'Historical unresolved start bypassed fresh STOP.'
}
Invoke-CausalStopCase 'a later round cannot reuse prior STOP success as queue closure' {
    Reset-CausalStopFixture; Set-CausalStopAttempt 'postgres'; $script:progress.postgres.materialized=$true; $script:engineFailures.provision=$true
    Assert-CausalStopTest (-not (Invoke-CausalStopRound) -and $script:progress.postgres.stopped) 'First-round fixture differs.'
    $script:engineFailures.provision=$false; $script:stopOutputs.postgres='throw'
    Assert-CausalStopPending 'postgres' (Invoke-CausalStopRound)
    Assert-CausalStopTest ((Get-CausalStopCount 'postgres') -eq 2) 'Prior STOP success was reused.'
}
$unsafe=@(
    @('extra command', $helperSource.Replace('    foreach ($kind', "    Get-Process`n    foreach (`$kind"),'command'),
    @('dynamic command', $helperSource.Replace('    foreach ($kind', "    & 'Get-Process'`n    foreach (`$kind"),'command'),
    @('member call', $helperSource.Replace('    foreach ($kind', "    `$Progress.Clear()`n    foreach (`$kind"),'invocation'),
    @('unreviewed variable', $helperSource.Replace('    foreach ($kind', "    `$ExecutionContext`n    foreach (`$kind"),'variable'),
    @('unreviewed type', $helperSource.Replace('    foreach ($kind', "    [IO.File]`n    foreach (`$kind"),'type'),
    @('duplicate selected function', ($helperSource+"`n"+$helperSource),'Exactly one'),
    @('parse failure', 'function Invoke-ParentContainerContainmentRound {','parse'),
    @('nested function', $helperSource.Replace('    foreach ($kind', "    function Unexpected-Helper {}`n    foreach (`$kind"),'Nested')
)
foreach ($entry in $unsafe) {
    Invoke-CausalStopCase ('closed extraction rejects '+$entry[0]) { Assert-CausalStopRejected { Get-CausalStopTestHelper $entry[1] } $entry[2] }
}
$mutants=@(
    @('old materialization shortcut', $helperSource.Replace('-and -not $unresolvedStart)', '-and (-not $unresolvedStart -or $state.materialized))'), 'old'),
    @('missing fresh read', $helperSource.Replace('$proof = Get-ParentContainmentContainer $kind', '$proof = $candidate'), 'fresh'),
    @('missing STOP identity check', $helperSource.Replace('$stop -cne $id', '$false'), 'stop'),
    @('missing fresh identity check', $helperSource.Replace('$proof.Id -cne $id -or ', ''), 'identity')
)
foreach ($entry in $mutants) {
    Invoke-CausalStopCase ('negative control detects '+$entry[0]) {
        Assert-CausalStopTest ($entry[1] -cne $helperSource) 'Negative control did not mutate the source.'
        . ([scriptblock]::Create((Get-CausalStopTestHelper $entry[1])))
        Reset-CausalStopFixture; Set-CausalStopAttempt 'postgres'; $script:progress.postgres.materialized=$true
        if ($entry[2] -ceq 'fresh') { $script:observations.postgres[1]=New-CausalStopContainer 'postgres' $true 42 'running' }
        if ($entry[2] -ceq 'stop') { $script:stopOutputs.postgres='d'*64 }
        if ($entry[2] -ceq 'identity') { $script:observations.postgres[1].Id='d'*64 }
        $result=Invoke-CausalStopRound
        Assert-CausalStopRejected {
            if ($entry[2] -ceq 'old') {
                Assert-CausalStopTest ($result -and $script:progress.postgres.result -ceq 'causal_exact_stop' -and (Get-CausalStopCount 'postgres') -eq 1 -and $script:reads.postgres -eq 2) 'Regression detected.'
            } else { Assert-CausalStopPending 'postgres' $result }
        } 'Regression detected|Unresolved container was admitted'
    }
}
$expectedCases=63
if ($results.Count -ne $expectedCases -or @($results | Where-Object {-not $_.passed}).Count -ne 0) { throw 'Fixed causal STOP case count or result gate differs.' }
[pscustomobject]@{format='stratus-parent-causal-stop-decision-tests-v1';expected=$expectedCases;executed=$results.Count;passed=$results.Count;failed=0
    scope='one actual helper with six in-memory command stubs; source-removal controls; no native or daemon execution'
    native_qualified=$false;containment_accepted=$false;full_goal_accepted=$false;cases=@($results)} | Microsoft.PowerShell.Utility\ConvertTo-Json -Depth 5
