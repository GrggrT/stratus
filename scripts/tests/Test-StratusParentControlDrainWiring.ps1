[CmdletBinding()]
param([string] $WatchdogPath)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $WatchdogPath) { $WatchdogPath = Join-Path $PSScriptRoot '..\Invoke-StratusParentStageWatchdog.ps1' }
$WatchdogPath = [IO.Path]::GetFullPath($WatchdogPath)
$source = [IO.File]::ReadAllText($WatchdogPath)
# AST plus ONE actual helper against an explicitly isolated managed endpoint
# double and no-wait sleep double. Never execute the watchdog/runtime main body,
# real endpoint, pipe/thread, process/Job, Docker or historical fault harness.
# Script latches/ordering are tested; EOF, flushing, deadlines, native identity,
# original terminal exit proof and full-protocol acceptance are NOT established.
function Assert-Test { param([bool] $Condition, [string] $Message) if (-not $Condition) { throw $Message } }
function Parse-TestAst {
    param([string] $Text)
    $tokens=$null; $errors=$null
    $ast=[Management.Automation.Language.Parser]::ParseInput($Text,[ref]$tokens,[ref]$errors)
    Assert-Test (@($errors).Count -eq 0) 'Watchdog/test mutation failed AST parsing.'
    return $ast
}
function Get-DrainFunction {
    param([object] $Ast)
    $functions=@($Ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $n.Name -ceq 'Complete-ParentControlTransport'},$true))
    Assert-Test ($functions.Count -eq 1) 'Drain helper missing or ambiguous.'
    $ancestor=$functions[0].Parent
    while ($null -ne $ancestor) {
        Assert-Test ($ancestor -isnot [Management.Automation.Language.FunctionDefinitionAst]) 'Drain helper cannot be nested.'
        $ancestor=$ancestor.Parent
    }
    return $functions[0]
}
function Assert-DrainCallSite {
    param([string] $Text)
    $tree=Parse-TestAst $Text
    $calls=@($tree.FindAll({param($n) $n -is [Management.Automation.Language.CommandAst] -and
        $n.GetCommandName() -ceq 'Complete-ParentControlTransport'},$true))
    Assert-Test ($calls.Count -eq 1) 'Drain must have exactly one handoff call.'
    $statement=$calls[0]
    while ($null -ne $statement.Parent -and $statement.Parent -isnot [Management.Automation.Language.StatementBlockAst]) { $statement=$statement.Parent }
    Assert-Test ($null -ne $statement.Parent) 'Drain call has no direct statement block.'
    $statements=@($statement.Parent.Statements); $at=-1
    for ($i=0;$i -lt $statements.Count;$i++) { if ($statements[$i].Extent.StartOffset -eq $statement.Extent.StartOffset) { $at=$i } }
    Assert-Test ($at -ge 3 -and $at+2 -lt $statements.Count) 'Drain is not inside complete handoff block.'
    Assert-Test ($statements[$at-1].Extent.Text -ceq '$script:controlDrainEligible = $true') 'Eligibility is not directly before drain.'
    Assert-Test ($statements[$at-2].Extent.Text.Contains('[void](Write-ParentDocument $paths.terminal ([ordered]@{') -and
        $statements[$at-2].Extent.Text.Contains('status = ''handoff_accepted''')) 'Actual terminal publication must precede eligibility.'
    Assert-Test ($statements[$at-3].Extent.Text -ceq 'Assert-ParentSuccessorSupervisor') 'Supervisor proof must directly precede terminal publication.'
    Assert-Test ($at -ge 5 -and $statements[$at-4].Extent.Text -ceq '$boundary = Assert-ParentHandoff $handoff' -and
        $statements[$at-5].Extent.Text -ceq 'Assert-ParentHeartbeat') 'Final heartbeat/handoff rechecks differ.'
    Assert-Test ($statements[$at+1].Extent.Text -ceq '$script:exitCode = 0' -and
        $statements[$at+2] -is [Management.Automation.Language.BreakStatementAst]) 'Drain must precede zero exit and loop break.'
    $tries=@($tree.EndBlock.Statements | Where-Object {$_ -is [Management.Automation.Language.TryStatementAst]})
    $main=@($tries | Where-Object { $_.Body.Extent.StartOffset -lt $calls[0].Extent.StartOffset -and $_.Body.Extent.EndOffset -gt $calls[0].Extent.EndOffset })
    Assert-Test ($main.Count -eq 1 -and $main[0].CatchClauses.Count -eq 1 -and
        $main[0].CatchClauses[0].Body.Extent.Text.Contains('Invoke-ParentContainment -Trigger $trigger')) 'Drain failure must reach original containment catch.'
    foreach ($block in @($main[0].CatchClauses[0].Body,$main[0].Finally)) {
        Assert-Test (@($block.FindAll({param($n) $n -is [Management.Automation.Language.CommandAst] -and
            $n.GetCommandName() -ceq 'Complete-ParentControlTransport'},$true)).Count -eq 0) 'Contained/unarmed/finally path cannot drain.'
    }
    $eligibilities=@($tree.FindAll({param($n) $n -is [Management.Automation.Language.AssignmentStatementAst] -and
        $n.Left.Extent.Text -ceq '$script:controlDrainEligible' -and $n.Right.Extent.Text -ceq '$true'},$true))
    Assert-Test ($eligibilities.Count -eq 1 -and $eligibilities[0].Extent.StartOffset -eq $statements[$at-1].Extent.StartOffset) 'Eligibility is manufactured elsewhere.'
}
function Assert-DrainHelperSource {
    param([string] $Text)
    $body=(Get-DrainFunction (Parse-TestAst $Text)).Extent.Text
    foreach ($required in @('-not $script:armed','-not $script:controlDrainEligible','$script:controlDrainAttempted -or',
        '$script:controlDrainCompleted -or','$null -eq $script:controlEndpoint','$endpoint = $script:controlEndpoint',
        '$endpoint.GetType() -ne [Stratus.ParentStage.PrivateControlEndpoint]', '$script:controlDrainAttempted = $true',
        '$endpoint.BeginTerminalDrain()', '$complete = $endpoint.PollTerminalDrain()', '$complete -isnot [bool]',
        'if ($complete) { break }', 'Start-Sleep -Milliseconds 10', '$script:controlDrainCompleted = $true')) {
        Assert-Test ($body.Contains($required)) ('Required helper rule missing: '+$required)
    }
    Assert-Test ($body.IndexOf('$script:controlDrainAttempted = $true') -lt $body.IndexOf('$endpoint.BeginTerminalDrain()')) 'Attempt must latch before native Begin.'
    Assert-Test (@([regex]::Matches($body,'\$endpoint\.BeginTerminalDrain\(\)')).Count -eq 1) 'Begin cannot restart deadline.'
    $refs=@([regex]::Matches($body,'\[object\]::ReferenceEquals\(\$script:controlEndpoint, \$endpoint\)'))
    $poll=$body.IndexOf('$complete = $endpoint.PollTerminalDrain()')
    Assert-Test ($refs.Count -eq 2 -and $refs[0].Index -lt $poll -and $refs[1].Index -gt $poll) 'Same endpoint checks must bracket poll.'
    Assert-Test ($body.IndexOf('$script:controlDrainCompleted = $true') -gt $body.IndexOf('Start-Sleep -Milliseconds 10')) 'Completion latch moved before loop.'
    $fn=Get-DrainFunction (Parse-TestAst $Text)
    Assert-Test (@($fn.FindAll({param($n) $n -is [Management.Automation.Language.CatchClauseAst] -or
        $n -is [Management.Automation.Language.ReturnStatementAst]},$true)).Count -eq 0) 'Helper suppresses failure or returns authority.'
}
$ast=Parse-TestAst $source; $fn=Get-DrainFunction $ast
$expectedCases=30; $script:cases=0; $script:passed=0; $script:failures=[Collections.Generic.List[string]]::new()
function Case {
    param([string] $Name,[scriptblock] $Body)
    $script:cases++
    try { & $Body; $script:passed++; Write-Output ('PASS parent-control-drain-wiring: '+$Name) }
    catch { $script:failures.Add($Name+': '+$_.Exception.GetBaseException().Message); Write-Output ('FAIL parent-control-drain-wiring: '+$Name) }
}
function Reject {
    param([scriptblock] $Body)
    $rejected=$false; try { & $Body | Out-Null } catch { $rejected=$true }
    Assert-Test $rejected 'Required drain rejection was absent.'
}
Case 'single actual success handoff calls drain after publication and before exit0 within containment try' { Assert-DrainCallSite $source }
Case 'actual helper owns one-shot latches and exact endpoint through every observation' { Assert-DrainHelperSource $source }
Case 'entry successor handoff and Transition containment WIP barriers remain unchanged' {
    Assert-Test ($source.Contains('STRATUS parent-stage watchdog is WIP and non-authorizing.') -and
        $source.Contains('throw ''Continuous next-phase ownership is unimplemented; terminal handoff and HBA release are forbidden.''') -and
        $source.Contains('throw ''Independent Transition terminal/containment takeover is unqualified; retain guard, HBA and fences.''')) 'A WIP barrier changed.'
}
Case 'endpoint remains retained for final cleanup and late cleanup still vetoes successful exit' {
    Assert-Test ($source.Contains('$locks.Add($script:controlEndpoint)') -and
        $source.Contains('try { $locks[$index].Dispose() } catch { $failures.Add(''held file cleanup: '' + $_.Exception.Message) }') -and
        $source.Contains('if ($script:exitCode -eq 0) { $script:exitCode = 3 }')) 'Endpoint ownership or cleanup failure exit changed.'
}
Case 'closed extraction permits only the one endpoint helper with explicit script state and sleep' {
    foreach ($command in @($fn.FindAll({param($n) $n -is [Management.Automation.Language.CommandAst]},$true))) {
        Assert-Test ($command.InvocationOperator -eq [Management.Automation.Language.TokenKind]::Unknown -and
            $command.GetCommandName() -ceq 'Start-Sleep') ('Unsafe helper command: '+$command.Extent.Text)
    }
    foreach ($type in @($fn.FindAll({param($n) $n -is [Management.Automation.Language.TypeExpressionAst] -or
        $n -is [Management.Automation.Language.TypeConstraintAst]},$true))) {
        Assert-Test ($type.TypeName.FullName -cin @('object','bool','Stratus.ParentStage.PrivateControlEndpoint')) ('Unsafe helper type: '+$type.TypeName.FullName)
    }
    foreach ($method in @($fn.FindAll({param($n) $n -is [Management.Automation.Language.InvokeMemberExpressionAst]},$true))) {
        Assert-Test ($method.Member -is [Management.Automation.Language.StringConstantExpressionAst] -and
            $method.Member.Value -cin @('GetType','ReferenceEquals','BeginTerminalDrain','PollTerminalDrain')) ('Unsafe helper method: '+$method.Extent.Text)
    }
    foreach ($variable in @($fn.FindAll({param($n) $n -is [Management.Automation.Language.VariableExpressionAst]},$true))) {
        Assert-Test ($variable.VariablePath.UserPath -cin @('script:armed','script:controlDrainEligible','script:controlDrainAttempted',
            'script:controlDrainCompleted','script:controlEndpoint','null','false','true','endpoint','complete')) ('Unsafe helper state: '+$variable.Extent.Text)
    }
}
foreach ($mutation in @(
    @('eligibility before terminal','$script:controlDrainEligible = $true','$script:controlDrainEligible = $false'),
    @('actual terminal publication','[void](Write-ParentDocument $paths.terminal ([ordered]@{','[void](Write-ParentDocument $paths.ready ([ordered]@{'),
    @('existing supervisor validation','                    Assert-ParentSuccessorSupervisor','                    $null'),
    @('existing heartbeat validation','                    Assert-ParentHeartbeat','                    $null'),
    @('original containment catch','Invoke-ParentContainment -Trigger $trigger','$null')
)) {
    $change=$mutation
    Case ('AST negative rejects missing '+$change[0]) { Reject { Assert-DrainCallSite ($source.Replace($change[1],$change[2])) } }
}
foreach ($mutation in @(
    @('attempt latch','$script:controlDrainAttempted = $true','$script:controlDrainAttempted = $false'),
    @('native endpoint type','[Stratus.ParentStage.PrivateControlEndpoint]','[object]'),
    @('single native Begin','$endpoint.BeginTerminalDrain()','$null'),
    @('poll identity','$complete = $endpoint.PollTerminalDrain()','$complete = $true'),
    @('completion boolean','$complete -isnot [bool]','$false'),
    @('same endpoint reference','[object]::ReferenceEquals($script:controlEndpoint, $endpoint)','$true')
)) {
    $change=$mutation
    Case ('AST negative rejects missing '+$change[0]) { Reject { Assert-DrainHelperSource ($source.Replace($change[1],$change[2])) } }
}
if ($script:failures.Count -ne 0) { throw ('Safe extraction refused: '+($script:failures -join '; ')) }
# The single type replacement below is explicit. This fixture has no pipes,
# threads, handles, filesystem, process APIs or wall-clock deadline behavior.
$namespace='StratusParentDrainFixture'+[guid]::NewGuid().ToString('N')
$fixture=@'
public sealed class Endpoint {
 public int BeginCalls, PollCalls, FalsePolls;
 public bool FailBegin, FailPoll;
 public System.Action PollHook;
 public void BeginTerminalDrain() { BeginCalls++; if(FailBegin) throw new System.InvalidOperationException("fixture Begin failure"); }
 public bool PollTerminalDrain() {
  PollCalls++; if(PollCalls>100) throw new System.InvalidOperationException("fixture runaway loop guard");
  if(PollHook!=null) PollHook();
  if(FailPoll) throw new System.TimeoutException("fixture original deadline failure; not a clock experiment");
  return PollCalls>FalsePolls;
 }
}
'@
$types=@(Add-Type -TypeDefinition ('namespace '+$namespace+' { '+$fixture+' }') -Language CSharp -PassThru)
$endpointType=@($types | Where-Object FullName -ceq ($namespace+'.Endpoint'))[0]
$helper=$fn.Extent.Text.Replace('[Stratus.ParentStage.PrivateControlEndpoint]','['+$namespace+'.Endpoint]')
. ([scriptblock]::Create($helper))
$script:sleepCalls=0; $script:swapOnSleep=$false
function Start-Sleep {
    param([int] $Milliseconds)
    Assert-Test ($Milliseconds -eq 10) 'Only the exact no-wait sleep fixture is allowed.'
    $script:sleepCalls++
    if ($script:sleepCalls -gt 100) { throw 'Fixture runaway sleep guard.' }
    if ($script:swapOnSleep) { $script:controlEndpoint=$endpointType::new() }
}
function Reset-Fixture {
    $script:armed=$true; $script:controlDrainEligible=$true; $script:controlDrainAttempted=$false; $script:controlDrainCompleted=$false
    $script:controlEndpoint=$endpointType::new(); $script:sleepCalls=0; $script:swapOnSleep=$false
}
Case 'managed double completion invokes Begin once Poll once and emits no authority' {
    Reset-Fixture
    Assert-Test (@(Complete-ParentControlTransport).Count -eq 0 -and $script:controlDrainAttempted -and
        $script:controlDrainCompleted -and $script:controlEndpoint.BeginCalls -eq 1 -and $script:controlEndpoint.PollCalls -eq 1) 'Single-use happy path differs.'
}
Case 'managed pending polls do not renew Begin and use only no-wait ten-ms sleeps' {
    Reset-Fixture; $script:controlEndpoint.FalsePolls=3
    Complete-ParentControlTransport
    Assert-Test ($script:controlEndpoint.BeginCalls -eq 1 -and $script:controlEndpoint.PollCalls -eq 4 -and $script:sleepCalls -eq 3) 'Pending poll reset deadline or bypassed sleep.'
}
Case 'managed unarmed state cannot initiate graceful close' { Reset-Fixture; $script:armed=$false; Reject { Complete-ParentControlTransport }; Assert-Test ($script:controlEndpoint.BeginCalls -eq 0) 'Unarmed state began close.' }
Case 'managed terminal eligibility absent cannot initiate graceful close' { Reset-Fixture; $script:controlDrainEligible=$false; Reject { Complete-ParentControlTransport }; Assert-Test ($script:controlEndpoint.BeginCalls -eq 0) 'Missing terminal began close.' }
Case 'managed missing endpoint fails before attempt' { Reset-Fixture; $script:controlEndpoint=$null; Reject { Complete-ParentControlTransport }; Assert-Test (-not $script:controlDrainAttempted) 'Missing endpoint latched attempt.' }
Case 'managed wrong endpoint type cannot substitute a capability' { Reset-Fixture; $script:controlEndpoint=[pscustomobject]@{}; Reject { Complete-ParentControlTransport }; Assert-Test (-not $script:controlDrainAttempted) 'Wrong type latched attempt.' }
Case 'managed Begin failure retains attempt without completion and cannot retry' {
    Reset-Fixture; $script:controlEndpoint.FailBegin=$true; Reject { Complete-ParentControlTransport }; Reject { Complete-ParentControlTransport }
    Assert-Test ($script:controlDrainAttempted -and -not $script:controlDrainCompleted -and $script:controlEndpoint.BeginCalls -eq 1) 'Uncertain Begin retried or completed.'
}
Case 'managed Poll failure retains attempt without completion and cannot retry' {
    Reset-Fixture; $script:controlEndpoint.FailPoll=$true; Reject { Complete-ParentControlTransport }; Reject { Complete-ParentControlTransport }
    Assert-Test ($script:controlDrainAttempted -and -not $script:controlDrainCompleted -and
        $script:controlEndpoint.BeginCalls -eq 1 -and $script:controlEndpoint.PollCalls -eq 1) 'Uncertain Poll retried or completed.'
}
Case 'managed completed drain cannot be called again' {
    Reset-Fixture; Complete-ParentControlTransport; Reject { Complete-ParentControlTransport }
    Assert-Test ($script:controlEndpoint.BeginCalls -eq 1) 'Completed drain was replayed.'
}
Case 'managed preexisting attempted latch rejects before touching endpoint' {
    Reset-Fixture; $script:controlDrainAttempted=$true; Reject { Complete-ParentControlTransport }
    Assert-Test ($script:controlEndpoint.BeginCalls -eq 0) 'Preexisting attempt called Begin.'
}
Case 'managed endpoint replacement between polls is rejected without promoting completion' {
    Reset-Fixture; $original=$script:controlEndpoint; $original.FalsePolls=2; $script:swapOnSleep=$true
    Reject { Complete-ParentControlTransport }
    Assert-Test ($original.BeginCalls -eq 1 -and $original.PollCalls -eq 1 -and
        $script:controlEndpoint.BeginCalls -eq 0 -and -not $script:controlDrainCompleted) 'Replacement was used or promoted.'
}
Case 'managed endpoint replacement during completed Poll is rejected by post-observation guard' {
    Reset-Fixture; $original=$script:controlEndpoint
    $original.PollHook=[Action]{ $script:controlEndpoint=$endpointType::new() }
    Reject { Complete-ParentControlTransport }
    Assert-Test ($original.PollCalls -eq 1 -and -not $script:controlDrainCompleted) 'Completion swallowed endpoint replacement.'
}
Case 'managed endpoint loss during Poll remains fatal instead of granting completion' {
    Reset-Fixture; $script:controlEndpoint.PollHook=[Action]{ $script:controlEndpoint=$null }
    Reject { Complete-ParentControlTransport }; Assert-Test (-not $script:controlDrainCompleted) 'Missing endpoint accepted as completion.'
}
Case 'attempt and completion flags are initialized false only once before resource acquisition' {
    foreach ($name in @('controlDrainEligible','controlDrainAttempted','controlDrainCompleted')) {
        $nodes=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.AssignmentStatementAst] -and
            $n.Left.Extent.Text -ceq ('$script:'+$name) -and $n.Right.Extent.Text -ceq '$false'},$true))
        Assert-Test ($nodes.Count -eq 1 -and $nodes[0].Extent.StartOffset -lt $source.IndexOf('$script:controlEndpoint = [Stratus.ParentStage.PrivateControlEndpoint]::Open(')) 'Drain state can reset after endpoint acquisition.'
    }
}
if ($script:cases -ne $expectedCases) { throw ('Drain wiring count differs: expected '+$expectedCases+', actual '+$script:cases) }
Write-Output ('Parent control drain wiring: '+$script:passed+'/'+$expectedCases+' passed. AST and managed endpoint double only; native EOF/drain/deadline/protocol unqualified.')
if ($script:failures.Count -ne 0) { throw ($script:failures -join '; ') }
