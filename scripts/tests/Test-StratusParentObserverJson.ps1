[CmdletBinding()]
param([string] $RuntimePath)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $PSBoundParameters.ContainsKey('RuntimePath')) { $RuntimePath = Join-Path $PSScriptRoot '..\Stratus.ParentStage.Runtime.cs' }
# Compile ONLY the actual managed StrictJson class and a UTF8 encoding dependency.
# No NativeChild/ProcessLease/Files filesystem code, P/Invoke, process or runtime
# body is included. This is parser behavior, not native/full-protocol qualification.
$source = [IO.File]::ReadAllText([IO.Path]::GetFullPath($RuntimePath))
$begin = $source.IndexOf('    public sealed class StrictJson {', [StringComparison]::Ordinal)
$end = $source.IndexOf('    // The timer belongs to the owning PowerShell process.', [StringComparison]::Ordinal)
if ($begin -lt 0 -or $end -le $begin -or
    $source.LastIndexOf('    public sealed class StrictJson {', [StringComparison]::Ordinal) -ne $begin) { throw 'StrictJson source slice is not unique.' }
$parserSource = $source.Substring($begin, $end - $begin)
if ($parserSource -match 'DllImport|NativeChild|ProcessLease|Environment|FileStream|Console|Thread|Reflection|Activator' -or
    $parserSource -notmatch 'public static void ValidateObserverOutput\(byte\[\] bytes\)') { throw 'Managed parser slice contains an unreviewed dependency.' }
$namespace = 'StratusObserverJsonTest' + [guid]::NewGuid().ToString('N')
$program = 'using System; using System.IO; using System.Text; using System.Collections.Generic; using System.Globalization; namespace ' +
    $namespace + ' { internal static class Files { internal static readonly UTF8Encoding Utf8 = new UTF8Encoding(false,true); } ' + $parserSource + ' }'
$types = @(Add-Type -TypeDefinition $program -Language CSharp -PassThru)
$parser = @($types | Where-Object Name -ceq 'StrictJson')[0]
$utf8 = [Text.UTF8Encoding]::new($false, $true)
$results = [Collections.Generic.List[object]]::new()
function Invoke-ParserCase {
    param([string] $Name, [scriptblock] $Action)
    try { & $Action; $results.Add([pscustomobject]@{name=$Name;passed=$true}) }
    catch { $results.Add([pscustomobject]@{name=$Name;passed=$false;reason=$_.Exception.GetBaseException().Message}) }
}
function Assert-ParserReject {
    param([byte[]] $Bytes, [string] $Pattern, [switch] $Observer)
    $caught = $null
    try { if ($Observer) { $parser::ValidateObserverOutput($Bytes) } else { $parser::Validate($Bytes) } }
    catch { $caught = $_.Exception.GetBaseException().Message }
    if ($null -eq $caught -or $caught -notlike $Pattern) { throw ('Wrong parser rejection: ' + $caught) }
}
Invoke-ParserCase 'normal object accepted by both fixed entry points' {
    $bytes = $utf8.GetBytes('{"ok":true,"n":1.5,"nested":[null,false]}')
    $parser::Validate($bytes); $parser::ValidateObserverOutput($bytes)
}
Invoke-ParserCase 'default protocol cap remains exactly one MiB' {
    $bytes = $utf8.GetBytes('{}' + (' ' * (1048576 - 2)))
    $parser::Validate($bytes)
}
Invoke-ParserCase 'only observer entry admits complete artifact larger than one MiB' {
    $bytes = $utf8.GetBytes('{"data":"' + ('x' * 1048576) + '"}')
    Assert-ParserReject $bytes '*byte bound*'
    $parser::ValidateObserverOutput($bytes)
}
Invoke-ParserCase 'observer cap rejects one byte above 64 MiB before decoding' {
    Assert-ParserReject ([byte[]]::new(67108865)) '*byte bound*' -Observer
}
Invoke-ParserCase 'null is rejected by both entries' {
    Assert-ParserReject $null '*bytes*'
    Assert-ParserReject $null '*bytes*' -Observer
}
Invoke-ParserCase 'empty data is rejected by both entries' {
    Assert-ParserReject ([byte[]]@()) '*byte bound*'
    Assert-ParserReject ([byte[]]@()) '*byte bound*' -Observer
}
Invoke-ParserCase 'observer duplicate and case-alias keys remain rejected' {
    foreach ($text in @('{"a":1,"a":2}', '{"a":1,"A":2}')) { Assert-ParserReject ($utf8.GetBytes($text)) '*Duplicate JSON key*' -Observer }
}
Invoke-ParserCase 'observer BOM remains rejected' { Assert-ParserReject ([byte[]]@(239,187,191,123,125)) '*BOM*' -Observer }
Invoke-ParserCase 'observer malformed UTF8 remains rejected' { Assert-ParserReject ([byte[]]@(123,34,120,34,58,34,255,34,125)) '*' -Observer }
Invoke-ParserCase 'observer trailing object remains rejected' { Assert-ParserReject ($utf8.GetBytes('{}{}')) '*Trailing JSON data*' -Observer }
Invoke-ParserCase 'observer nonobject root remains rejected' { Assert-ParserReject ($utf8.GetBytes('[]')) '*One JSON object*' -Observer }
Invoke-ParserCase 'observer unpaired Unicode escape remains rejected' { Assert-ParserReject ($utf8.GetBytes('{"x":"\uD800"}')) '*' -Observer }
Invoke-ParserCase 'observer non-JSON numeric tokens remain rejected' { Assert-ParserReject ($utf8.GetBytes('{"x":NaN}')) '*Invalid JSON number*' -Observer }
Invoke-ParserCase 'observer nesting depth remains bounded' { Assert-ParserReject ($utf8.GetBytes(('{"x":' * 42) + '{}' + ('}' * 42))) '*nesting bound*' -Observer }
$failures = @($results | Where-Object {-not $_.passed})
[pscustomobject]@{suite='parent-observer-managed-json';expected=14;executed=$results.Count;passed=$results.Count-$failures.Count
    failed=$failures.Count;results=@($results.ToArray());actual_strict_json_class_executed=$true;native_runtime_executed=$false
    filesystem_fixtures_written=$false;process_or_docker_executed=$false;full_protocol_accepted=$false} | ConvertTo-Json -Depth 5 -Compress
if ($results.Count -ne 14 -or $failures.Count -ne 0) { throw 'Managed observer parser regressions failed.' }
