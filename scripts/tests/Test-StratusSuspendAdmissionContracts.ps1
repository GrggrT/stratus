[CmdletBinding()]
param([string] $RuntimePath)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if (-not $RuntimePath) { $RuntimePath=Join-Path $PSScriptRoot '..\Stratus.ParentStage.Runtime.cs' }
# C# source tokens only: comment/literal-aware lexing, balanced blocks and exact
# class/direct-method selection. This is not a semantic C# AST or Windows proof.
# No target compile/import/evaluation, native call, process/Job/pipe or FS fixture.
$raw=[IO.File]::ReadAllBytes([IO.Path]::GetFullPath($RuntimePath))
$source=[Text.UTF8Encoding]::new($false,$true).GetString($raw)
$sha=[Security.Cryptography.SHA256]::Create()
try {$sourceHash=[BitConverter]::ToString($sha.ComputeHash($raw)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
function Assert-SuspendTest {param([bool] $Okay,[string] $Message) if(-not $Okay){throw ('Suspend source contract: '+$Message)}}
function Get-SuspendTokens {
    param([string] $Text)
    $pattern=@'
//[^\r\n]* | /\*.*?\*/ | @"(?:""|[^"])*" | "(?:\\.|[^"\\])*" | '(?:\\.|[^'\\])*'
| [A-Za-z_][A-Za-z_0-9]* | [0-9]+ | == | != | >= | <= | \+\+ | && | \|\| | => | [^\s]
'@
    $tokens=[Collections.Generic.List[string]]::new()
    foreach($m in [regex]::Matches($Text,$pattern,[Text.RegularExpressions.RegexOptions]::Singleline -bor [Text.RegularExpressions.RegexOptions]::IgnorePatternWhitespace)){
        if(-not $m.Value.StartsWith('//') -and -not $m.Value.StartsWith('/*')){$tokens.Add($m.Value)}
    }
    return ,$tokens.ToArray()
}
function Find-SuspendSequence {
    param([string[]] $Tokens,[string] $Sequence)
    $wanted=Get-SuspendTokens $Sequence;Assert-SuspendTest ($wanted.Count -gt 0) 'Empty source predicate.'
    $found=[Collections.Generic.List[int]]::new()
    for($i=0;$i -le $Tokens.Count-$wanted.Count;$i++){
        $same=$true
        for($j=0;$j -lt $wanted.Count;$j++){if($Tokens[$i+$j] -cne $wanted[$j]){$same=$false;break}}
        if($same){$found.Add($i)}
    }
    return ,$found.ToArray()
}
function Require-SuspendSequence {param([string[]] $Tokens,[string] $Sequence,[int] $Count=1) Assert-SuspendTest ((Find-SuspendSequence $Tokens $Sequence).Count -eq $Count) ('Required sequence differs: '+$Sequence)}
function Get-SuspendBlock {
    param([string[]] $Tokens,[string] $Signature,[switch] $DirectMember)
    $matches=Find-SuspendSequence $Tokens $Signature
    if($DirectMember){
        $direct=[Collections.Generic.List[int]]::new();$depth=0
        for($i=0;$i -lt $Tokens.Count;$i++){
            if($depth -eq 1 -and $i -in $matches){$direct.Add($i)}
            if($Tokens[$i] -ceq '{'){$depth++}elseif($Tokens[$i] -ceq '}'){$depth--}
        }
        $matches=$direct.ToArray()
    }
    Assert-SuspendTest ($matches.Count -eq 1) ('Missing or ambiguous exact block: '+$Signature)
    $start=$matches[0];$open=$start
    while($open -lt $Tokens.Count -and $Tokens[$open] -cne '{'){$open++}
    Assert-SuspendTest ($open -lt $Tokens.Count) 'Opening brace missing.'
    $depth=0
    for($i=$open;$i -lt $Tokens.Count;$i++){
        if($Tokens[$i] -ceq '{'){$depth++}elseif($Tokens[$i] -ceq '}'){$depth--}
        if($depth -eq 0){return [pscustomobject]@{Start=$start;End=$i;Tokens=[string[]]$Tokens[$start..$i]}}
    }
    throw 'Suspend source contract: unbalanced source block.'
}
function Get-SuspendClass {param([string[]] $Tokens,[string] $Name) return (Get-SuspendBlock $Tokens ('public sealed class '+$Name+' : IDisposable'))}
function Get-SuspendMethod {param([string[]] $Class,[string] $Signature) return (Get-SuspendBlock $Class $Signature -DirectMember)}
function Get-SuspendIf {
    param([string[]] $Tokens,[string] $Condition)
    $sequence='if ('+$Condition+')';$matches=Find-SuspendSequence $Tokens $sequence
    Assert-SuspendTest ($matches.Count -eq 1) ('Missing/duplicate exact rejection condition: '+$Condition)
    $start=$matches[0];$after=$start+(Get-SuspendTokens $sequence).Count
    Assert-SuspendTest ($after -lt $Tokens.Count) 'Missing if body.'
    if($Tokens[$after] -ceq '{'){
        $depth=0
        for($i=$after;$i -lt $Tokens.Count;$i++){
            if($Tokens[$i] -ceq '{'){$depth++}elseif($Tokens[$i] -ceq '}'){$depth--}
            if($depth -eq 0){return [pscustomobject]@{Start=$start;End=$i;Tokens=[string[]]$Tokens[$start..$i]}}
        }
    }else{
        for($i=$after;$i -lt $Tokens.Count;$i++){if($Tokens[$i] -ceq ';'){return [pscustomobject]@{Start=$start;End=$i;Tokens=[string[]]$Tokens[$start..$i]}}}
    }
    throw 'Suspend source contract: incomplete rejection statement.'
}
function Replace-SuspendSlice {
    param([string[]] $Tokens,[int] $Start,[int] $End,[string[]] $Replacement)
    Assert-SuspendTest ($Start -ge 0 -and $End -ge $Start -and $End -lt $Tokens.Count) 'Invalid source-only mutation range.'
    $result=[Collections.Generic.List[string]]::new()
    for($i=0;$i -lt $Start;$i++){$result.Add($Tokens[$i])}
    foreach($token in $Replacement){$result.Add($token)}
    for($i=$End+1;$i -lt $Tokens.Count;$i++){$result.Add($Tokens[$i])}
    return ,$result.ToArray()
}
function Replace-SuspendSequence {
    param([string[]] $Tokens,[string] $Old,[string] $New)
    $matches=Find-SuspendSequence $Tokens $Old
    Assert-SuspendTest ($matches.Count -eq 1) ('Mutation anchor missing or ambiguous: '+$Old)
    return ,(Replace-SuspendSlice $Tokens $matches[0] ($matches[0]+(Get-SuspendTokens $Old).Count-1) (Get-SuspendTokens $New))
}
function Change-SuspendMember {
    param([string[]] $Class,[string] $Signature,[string] $Old,[string] $New)
    $method=Get-SuspendMethod $Class $Signature
    $mutant=Replace-SuspendSequence $method.Tokens $Old $New
    return ,(Replace-SuspendSlice $Class $method.Start $method.End $mutant)
}
function Require-SuspendOrder {
    param([string[]] $Tokens,[string[]] $Sequences)
    $last=-1
    foreach($sequence in $Sequences){$found=Find-SuspendSequence $Tokens $sequence;Assert-SuspendTest ($found.Count -eq 1 -and $found[0] -gt $last) ('Sequence order differs: '+$sequence);$last=$found[0]}
}
function Assert-ProbeAdmission {
    param([string[]] $Method,[string] $ErrorStyle,[int] $ResumeCalls)
    Require-SuspendSequence $Method 'SuspendThread(thread)' 1
    Require-SuspendSequence $Method 'ResumeThread(thread)' $ResumeCalls
    $probe=(Find-SuspendSequence $Method 'uint previous=SuspendThread(thread);')[0]
    $reject=Get-SuspendIf $Method 'previous!=1'
    Require-SuspendSequence $reject.Tokens 'throw new InvalidOperationException(' 1
    Require-SuspendSequence $reject.Tokens 'ResumeThread(' 0
    Require-SuspendSequence $reject.Tokens 'return' 0
    $firstResume=(Find-SuspendSequence $Method 'ResumeThread(thread)')[0]
    Assert-SuspendTest ($reject.Start -gt $probe -and $reject.End -lt $firstResume) 'Wrong prior count must throw before any restoration.'
    if($ErrorStyle -ceq 'Check'){
        Require-SuspendOrder $Method @('uint previous=SuspendThread(thread);','Check(previous!=UInt32.MaxValue,','if(previous!=1)','uint restored=ResumeThread(thread);','Check(restored!=UInt32.MaxValue,','if(restored!=2)')
    }else{
        Require-SuspendOrder $Method @('uint previous=SuspendThread(thread);','if(previous==UInt32.MaxValue) Check(false,','if(previous!=1)','uint restored=ResumeThread(thread);','if(restored==UInt32.MaxValue) Check(false,','if(restored!=2)')
    }
    $restored=Get-SuspendIf $Method 'restored!=2'
    Require-SuspendSequence $restored.Tokens 'throw new InvalidOperationException(' 1
    if($ResumeCalls -eq 2){Assert-SuspendTest ($restored.End -lt (Find-SuspendSequence $Method 'uint resumedCount=ResumeThread(thread);')[0]) 'Actual command resume preceded restoration validation.'}
}
function Assert-AbortAdmission {
    param([string[]] $Method)
    Require-SuspendSequence $Method 'SuspendThread(thread)' 1
    Require-SuspendSequence $Method 'ResumeThread(' 0
    Require-SuspendOrder $Method @('if(resumeAttempted)','uint previous=SuspendThread(thread);','if(previous==UInt32.MaxValue)','if(previous!=1)','TerminateProcess(process,202)','WaitForSingleObject(process,5000)')
    foreach($condition in @('previous==UInt32.MaxValue','previous!=1')){
        $branch=Get-SuspendIf $Method $condition
        Require-SuspendOrder $branch.Tokens @('resumeAttempted=true;','Exception failure=','Record(failure);','throw failure;')
        Require-SuspendSequence $branch.Tokens 'TerminateProcess(' 0
        Require-SuspendSequence $branch.Tokens 'return' 0
    }
}
function Assert-StickySuspendFailure {
    param([string[]] $Class,[string] $Kind,[string] $Signature)
    $method=(Get-SuspendMethod $Class $Signature).Tokens
    Require-SuspendSequence $Class 'resumeAttempted=false' 0
    if($Kind -cin @('IndependentWatchdogLaunch','IndependentTransitionLaunch')){
        Require-SuspendOrder $method @('if(resumeAttempted || !IsAlive())','uint previous=SuspendThread(thread);','resumeAttempted=true; Record(failure); throw;')
        $resume=(Get-SuspendMethod $Class 'public void Resume()').Tokens
        Require-SuspendOrder $resume @('if(resumeAttempted)','AssertSuspended();','resumeAttempted=true;','uint previous=ResumeThread(thread);')
        $record=(Get-SuspendMethod $Class 'private void Record(Exception failure)').Tokens
        Require-SuspendSequence $record 'faults.Add(failure);'
        Require-SuspendSequence $Class 'faults.Clear(' 0
    }elseif($Kind -ceq 'NativeCommand'){
        Require-SuspendSequence $method 'catch(Exception error) { Record(error); throw; }'
        Require-SuspendOrder $method @('if(!creationSucceeded || resumeAttempted)','SuccessBoundary(); HeldExecutableBoundary(); RootIdentity(false);','uint previous=SuspendThread(thread);','resumeAttempted=true;','uint resumedCount=ResumeThread(thread);')
        $boundary=(Get-SuspendMethod $Class 'private void SuccessBoundary()').Tokens
        Require-SuspendSequence $boundary 'SuccessState();'
        $state=(Get-SuspendMethod $Class 'private void SuccessState()').Tokens
        Require-SuspendSequence $state 'if(failures.Count!=0) throw new AggregateException(failures.ToArray());'
        $record=(Get-SuspendMethod $Class 'private void Record(Exception error)').Tokens
        Require-SuspendSequence $record 'failures.Add(new InvalidOperationException(text));'
        Require-SuspendSequence $Class 'failures.Clear(' 0
    }else{
        Require-SuspendOrder $method @('if(disposed || resumed || resumeAttempted)','if(Failed) throw new AggregateException(FailureSnapshot());','uint previous=SuspendThread(thread);','catch(Exception failure) { Record(failure); throw; }')
        $resume=(Get-SuspendMethod $Class 'public void Resume()').Tokens
        Require-SuspendOrder $resume @('if(disposed || resumed || resumeAttempted)','if(Failed) throw new AggregateException(FailureSnapshot());','AssertSuspendedOwned();','resumeAttempted=true;','uint previous=ResumeThread(thread);')
        Require-SuspendSequence $Class 'private readonly List<Exception> failures=new List<Exception>();'
        Require-SuspendSequence $Class 'private bool Failed { get { lock(failuresGate) { return failures.Count!=0; } } }'
        $record=(Get-SuspendMethod $Class 'private void Record(Exception error)').Tokens
        Require-SuspendSequence $record 'failures.Add(error);'
        Require-SuspendSequence $Class 'failures.Clear(' 0
    }
}
function Assert-ChildResumeLatch {
    param([string[]] $Class)
    $resume=(Get-SuspendMethod $Class 'public void Resume()').Tokens
    Require-SuspendOrder $resume @('if(disposed || resumed || resumeAttempted)','if(Failed) throw new AggregateException(FailureSnapshot());',
        'AssertSuspendedOwned();','runClock.Restart();','resumeAttempted=true;','uint previous=ResumeThread(thread);','resumed=true;')
    Require-SuspendSequence $resume 'catch(Exception failure) { Record(failure); throw; }'
    Require-SuspendSequence $Class 'resumeAttempted=false' 0
    Require-SuspendSequence $Class 'private bool resumed,resumeAttempted,disposed;'
}
$tokens=Get-SuspendTokens $source
$specs=@(
    @('IndependentWatchdogLaunch','public void AssertSuspended()','Check',1),
    @('IndependentTransitionLaunch','public void AssertSuspended()','Check',1),
    @('NativeCommand','public void Resume()','If',2),
    @('NativeChild','public void AssertSuspendedOwned()','If',1)
)
$classes=@{}
foreach($spec in $specs){$classes[$spec[0]]=(Get-SuspendClass $tokens $spec[0]).Tokens}
$results=[Collections.Generic.List[object]]::new()
function Case {param([string] $CaseLabel,[scriptblock] $Body) try{& $Body}catch{throw ('Suspend case '+$CaseLabel+' failed: '+$_.Exception.Message)};$results.Add([pscustomobject]@{name=$CaseLabel;passed=$true})}
function Reject {param([scriptblock] $Body) $failure=$null;try{& $Body|Out-Null}catch{$failure=$_.Exception.Message};Assert-SuspendTest ($null -ne $failure) 'Negative source mutation was accepted.'}
foreach($spec in $specs){
    $class=$classes[$spec[0]];$method=(Get-SuspendMethod $class $spec[1]).Tokens
    Case ($spec[0]+' rejects prior count before restoration') {Assert-ProbeAdmission $method $spec[2] $spec[3]}
    Case ($spec[0]+' negative restoration moved before rejection') {
        $restore=Find-SuspendSequence $method 'uint restored=ResumeThread(thread);'
        Assert-SuspendTest ($restore.Count -eq 1) 'Restore mutation anchor differs.'
        $length=(Get-SuspendTokens 'uint restored=ResumeThread(thread);').Count
        $mutant=Replace-SuspendSlice $method $restore[0] ($restore[0]+$length-1) ([string[]]@())
        $reject=Get-SuspendIf $mutant 'previous!=1'
        $mutant=Replace-SuspendSlice $mutant $reject.Start $reject.End ([string[]]@((Get-SuspendTokens 'uint restored=ResumeThread(thread);')+$reject.Tokens))
        Reject {Assert-ProbeAdmission $mutant $spec[2] $spec[3]}
    }
    Case ($spec[0]+' negative prior-count rejection removed') {
        $reject=Get-SuspendIf $method 'previous!=1';$mutant=Replace-SuspendSlice $method $reject.Start $reject.End ([string[]]@())
        Reject {Assert-ProbeAdmission $mutant $spec[2] $spec[3]}
    }
    Case ($spec[0]+' negative UINT_MAX error check disabled') {
        $old=if($spec[2] -ceq 'Check'){'previous!=UInt32.MaxValue'}else{'previous==UInt32.MaxValue'}
        $new=if($spec[2] -ceq 'Check'){'previous!=0'}else{'previous==0'}
        $mutant=Replace-SuspendSequence $method $old $new
        Reject {Assert-ProbeAdmission $mutant $spec[2] $spec[3]}
    }
    Case ($spec[0]+' negative restored-count rejection removed') {
        $reject=Get-SuspendIf $method 'restored!=2';$mutant=Replace-SuspendSlice $method $reject.Start $reject.End ([string[]]@())
        Reject {Assert-ProbeAdmission $mutant $spec[2] $spec[3]}
    }
}
foreach($kind in @('IndependentWatchdogLaunch','IndependentTransitionLaunch')){
    $method=(Get-SuspendMethod $classes[$kind] 'public void AbortBeforeResume()').Tokens
    Case ($kind+' abort never restores an unproved thread') {Assert-AbortAdmission $method}
    Case ($kind+' negative abort resumes wrong-count thread') {
        $mutant=Replace-SuspendSequence $method 'if(previous!=1) {' 'if(previous!=1) { ResumeThread(thread);'
        Reject {Assert-AbortAdmission $mutant}
    }
    Case ($kind+' negative abort loses wrong-count latch') {
        $branch=Get-SuspendIf $method 'previous!=1'
        $changed=Replace-SuspendSequence $branch.Tokens 'resumeAttempted=true;' 'resumeAttempted=false;'
        $mutant=Replace-SuspendSlice $method $branch.Start $branch.End $changed
        Reject {Assert-AbortAdmission $mutant}
    }
    Case ($kind+' negative abort loses recorded uncertainty') {
        $branch=Get-SuspendIf $method 'previous!=1'
        $changed=Replace-SuspendSequence $branch.Tokens 'Record(failure);' ''
        $mutant=Replace-SuspendSlice $method $branch.Start $branch.End $changed
        Reject {Assert-AbortAdmission $mutant}
    }
}
foreach($spec in $specs){
    $class=$classes[$spec[0]]
    Case ($spec[0]+' suspend uncertainty is sticky across retry') {Assert-StickySuspendFailure $class $spec[0] $spec[1]}
    Case ($spec[0]+' negative sticky fault recording removed') {
        $call=if($spec[0] -ceq 'NativeCommand'){'Record(error);'}else{'Record(failure);'}
        $mutant=Change-SuspendMember $class $spec[1] $call ''
        Reject {Assert-StickySuspendFailure $mutant $spec[0] $spec[1]}
    }
    Case ($spec[0]+' negative retry entry gate removed') {
        if($spec[0] -cin @('IndependentWatchdogLaunch','IndependentTransitionLaunch')){$old='if(resumeAttempted || !IsAlive())';$new='if(!IsAlive())'}
        elseif($spec[0] -ceq 'NativeCommand'){$old='SuccessBoundary(); HeldExecutableBoundary(); RootIdentity(false);';$new='HeldExecutableBoundary(); RootIdentity(false);'}
        else{$old='if(Failed) throw new AggregateException(FailureSnapshot());';$new=''}
        $mutant=Change-SuspendMember $class $spec[1] $old $new
        Reject {Assert-StickySuspendFailure $mutant $spec[0] $spec[1]}
    }
}
Case 'NativeChild actual resume attempt is latched before kernel and clock cannot restart on retry' {Assert-ChildResumeLatch $classes.NativeChild}
Case 'NativeChild negative actual resume latch removed' {
    $mutant=Change-SuspendMember $classes.NativeChild 'public void Resume()' 'resumeAttempted=true;' ''
    Reject {Assert-ChildResumeLatch $mutant}
}
Case 'NativeChild negative actual resume retry gate removed' {
    $mutant=Change-SuspendMember $classes.NativeChild 'public void Resume()' 'if(disposed || resumed || resumeAttempted)' 'if(disposed || resumed)'
    Reject {Assert-ChildResumeLatch $mutant}
}
Case 'same method in another class cannot replace target class method' {
    $original=Get-SuspendClass $tokens 'IndependentWatchdogLaunch'
    $changed=Replace-SuspendSequence $original.Tokens 'public void AssertSuspended()' 'public void MissingSuspended()'
    $mutant=Replace-SuspendSlice $tokens $original.Start $original.End $changed
    $selected=Get-SuspendClass $mutant 'IndependentWatchdogLaunch'
    Reject {Get-SuspendMethod $selected.Tokens 'public void AssertSuspended()'}
}
Case 'duplicate exact method in its class is rejected' {
    $class=$classes.IndependentWatchdogLaunch
    $mutant=Replace-SuspendSlice $class ($class.Count-1) ($class.Count-1) (Get-SuspendTokens 'public void AssertSuspended() {} }')
    Reject {Get-SuspendMethod $mutant 'public void AssertSuspended()'}
}
Case 'duplicate exact class is rejected' {
    $mutant=[string[]]@($tokens+(Get-SuspendTokens 'public sealed class NativeChild : IDisposable {}'))
    Reject {Get-SuspendClass $mutant 'NativeChild'}
}
Case 'commented class and method signatures cannot satisfy selectors' {
    $mutant=Get-SuspendTokens ('/* public sealed class NativeChild : IDisposable { public void AssertSuspendedOwned() {} } */'+$source)
    $class=(Get-SuspendClass $mutant 'NativeChild').Tokens
    Assert-ProbeAdmission (Get-SuspendMethod $class 'public void AssertSuspendedOwned()').Tokens 'If' 1
}
Case 'string-literal class and method signatures cannot satisfy selectors' {
    $mutant=Get-SuspendTokens ('string ignored="public sealed class NativeChild : IDisposable { public void AssertSuspendedOwned() {} }";'+$source)
    $class=(Get-SuspendClass $mutant 'NativeChild').Tokens
    Assert-ProbeAdmission (Get-SuspendMethod $class 'public void AssertSuspendedOwned()').Tokens 'If' 1
}
$expected=48
Assert-SuspendTest ($results.Count -eq $expected) ('Fixed case count differs: '+$results.Count)
Assert-SuspendTest ((Get-FileHash -LiteralPath $RuntimePath -Algorithm SHA256).Hash.ToLowerInvariant() -ceq $sourceHash) 'Runtime source changed after the exact byte snapshot.'
[pscustomobject][ordered]@{format='stratus-suspend-admission-source-contract-tests-v1';expected=$expected;executed=$results.Count;passed=$results.Count;failed=0
    ps_version=$PSVersionTable.PSVersion.ToString();ps_edition=$PSVersionTable.PSEdition;source_sha256=$sourceHash
    test_sha256=(Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
    source_tokens_and_mutations_only=$true;semantic_CSharp_AST=$false;runtime_compiled=$false;runtime_loaded=$false;native_executed=$false
    filesystem_fixture_created=$false;native_qualified=$false;full_protocol_accepted=$false;full_goal_accepted=$false;results=$results.ToArray()}|ConvertTo-Json -Depth 6 -Compress
