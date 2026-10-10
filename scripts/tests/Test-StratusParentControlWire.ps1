[CmdletBinding()]
param([string] $RuntimePath)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $RuntimePath) { $RuntimePath = Join-Path $PSScriptRoot '..\Stratus.ParentStage.Runtime.cs' }
# Executes ONLY the actual pure ControlWire and StrictJson source slices with a
# UTF8 dependency stub. No pipes, threads, P/Invoke, process or whole runtime body.
$sourceBytes = [IO.File]::ReadAllBytes([IO.Path]::GetFullPath($RuntimePath))
$utf8 = [Text.UTF8Encoding]::new($false, $true)
$source = $utf8.GetString($sourceBytes)
$sha = [Security.Cryptography.SHA256]::Create()
try { $sourceHash = [BitConverter]::ToString($sha.ComputeHash($sourceBytes)).Replace('-','').ToLowerInvariant() }
finally { $sha.Dispose() }
function Get-ControlSlice {
    param([string] $Start,[string] $End)
    $a=$source.IndexOf($Start,[StringComparison]::Ordinal)
    $b=$source.IndexOf($End,[StringComparison]::Ordinal)
    if ($a -lt 0 -or $b -le $a -or $source.LastIndexOf($Start,[StringComparison]::Ordinal) -ne $a) {
        throw 'Pure control source slice must be unique and bounded.'
    }
    return $source.Substring($a,$b-$a)
}
$json = Get-ControlSlice '    public sealed class StrictJson {' '    // The timer belongs to the owning PowerShell process.'
$wire = Get-ControlSlice '    internal static class ControlWire {' '    internal sealed class BoundedControlIo : IDisposable {'
foreach ($slice in @($json,$wire)) {
    if ($slice -match 'DllImport|NativeChild|ProcessLease|Environment|FileStream|Console|Thread|Reflection|Activator|PipeStream') {
        throw 'Pure source slice has an unreviewed dependency.'
    }
}
$ns = 'StratusControlWireTest' + [guid]::NewGuid().ToString('N')
$wrapper = @'
public static class WireFixture {
 public static byte[] Request(string s,int n,string c,byte[] p) {return ControlWire.Request(s,n,c,p);}
 public static byte[] Response(string s,int n,string c,string h,byte[] p) {return ControlWire.Response(s,n,c,h,p);}
 public static string Decode(byte[] f,bool r,string s) {int n;byte[] p;ControlWire.Decode(f,r,s,out n,out p);return Files.Utf8.GetString(p);}
 public static byte[] Payload(byte[] p) {return ControlWire.Payload(p);}
}
'@
$program = 'using System; using System.IO; using System.Text; using System.Collections.Generic; using System.Globalization; namespace ' +
    $ns + ' { internal static class Files { internal static readonly UTF8Encoding Utf8=new UTF8Encoding(false,true); } ' + $json + $wire + $wrapper + ' }'
$types = @(Add-Type -TypeDefinition $program -Language CSharp -PassThru)
$fixture = @($types | Where-Object Name -ceq 'WireFixture')[0]
$session = 'a' * 32; $challenge='b' * 64; $hash='c' * 64
$body=$utf8.GetBytes('{"ok":true,"message":"test"}')
$request=$fixture::Request($session,1,$challenge,$body)
$response=$fixture::Response($session,1,$challenge,$hash,$body)
$requestText=$utf8.GetString($request)
$results=[Collections.Generic.List[object]]::new()
function Case { param([string] $Name,[scriptblock] $Action)
    try { & $Action; $results.Add([pscustomobject]@{name=$Name;passed=$true}) }
    catch { $results.Add([pscustomobject]@{name=$Name;passed=$false;reason=$_.Exception.GetBaseException().Message}) }
}
function Reject { param([scriptblock] $Action)
    $rejected=$false
    try { & $Action | Out-Null } catch { $rejected=$true }
    if (-not $rejected) { throw 'Invalid control input was accepted.' }
}
Case 'actual request and response round trip preserve exact payload' {
    if ($fixture::Decode($request,$false,$session) -cne $utf8.GetString($body) -or
        $fixture::Decode($response,$true,$session) -cne $utf8.GetString($body)) { throw 'Payload changed.' }
}
Case 'payload is cloned, not caller owned' {
    $copy=$fixture::Payload($body)
    if ([object]::ReferenceEquals($copy,$body)) { throw 'Mutable payload alias.' }
    $copy[0]=0
    if ($body[0] -ne 123) { throw 'Payload mutation escaped.' }
}
Case 'exact 4096 payload and sequence 1024 accepted' {
    $bytes=$utf8.GetBytes('{"x":"'+('z'*4088)+'"}')
    if ($bytes.Length -ne 4096) { throw 'Fixture size differs.' }
    $frame=$fixture::Request($session,1024,$challenge,$bytes)
    if ($fixture::Decode($frame,$false,$session) -cne $utf8.GetString($bytes)) { throw 'Boundary roundtrip differs.' }
}
Case 'UTF8 payload is encoded in ASCII-only LF envelope' {
    $bytes=$utf8.GetBytes('{"text":"'+[char]0x044F+'"}')
    $frame=$fixture::Request($session,2,$challenge,$bytes)
    if (@($frame[0..($frame.Length-2)] | Where-Object {$_ -lt 32 -or $_ -gt 126}).Count -ne 0 -or $frame[-1] -ne 10) { throw 'Non-ASCII envelope.' }
    if ($fixture::Decode($frame,$false,$session) -cne $utf8.GetString($bytes)) { throw 'UTF8 changed.' }
}
foreach ($entry in @(
    @{name='zero sequence';n=0}, @{name='negative sequence';n=-1}, @{name='over-limit sequence';n=1025}
)) { $item=$entry; Case $item.name { Reject { $fixture::Request($session,$item.n,$challenge,$body) } } }
foreach ($entry in @(
    @{name='uppercase session';s=('A'*32);c=$challenge}, @{name='short session';s=('a'*31);c=$challenge},
    @{name='session suffix LF';s=($session+"`n");c=$challenge}, @{name='uppercase challenge';s=$session;c=('B'*64)},
    @{name='short challenge';s=$session;c=('b'*63)}
)) { $item=$entry; Case $item.name { Reject { $fixture::Request($item.s,1,$item.c,$body) } } }
Case 'malformed response hash rejected' { Reject { $fixture::Response($session,1,$challenge,('C'*64),$body) } }
foreach ($entry in @(
    @{name='null payload';bytes=$null}, @{name='empty payload';bytes=[byte[]]@()},
    @{name='one-byte payload';bytes=$utf8.GetBytes('0')},
    @{name='over-limit payload';bytes=$utf8.GetBytes('{}'+(' '*4095))},
    @{name='duplicate JSON key';bytes=$utf8.GetBytes('{"a":1,"a":2}')},
    @{name='case-alias JSON key';bytes=$utf8.GetBytes('{"a":1,"A":2}')},
    @{name='nonobject JSON';bytes=$utf8.GetBytes('[]')},
    @{name='trailing JSON';bytes=$utf8.GetBytes('{}{}')},
    @{name='BOM payload';bytes=[byte[]]@(239,187,191,123,125)},
    @{name='invalid UTF8 payload';bytes=[byte[]]@(123,34,120,34,58,34,255,34,125)}
)) { $item=$entry; Case $item.name { Reject { $fixture::Request($session,1,$challenge,$item.bytes) } } }
$encoded=[Convert]::ToBase64String($body)
foreach ($entry in @(
    @{name='missing final LF';text=$requestText.TrimEnd([char]10)},
    @{name='CRLF envelope';text=$requestText.Replace("`n","`r`n")},
    @{name='second complete frame';text=($requestText+$requestText)},
    @{name='trailing partial frame';text=($requestText+'X')},
    @{name='wrong session';text=$requestText.Replace($session,('d'*32))},
    @{name='wrong role';text=$requestText.Replace('REQUEST','RESPONSE')},
    @{name='unknown version';text=$requestText.Replace('REQUEST/1','REQUEST/2')},
    @{name='extra field';text=$requestText.Replace("`n","|x`n")},
    @{name='noncanonical sequence';text=$requestText.Replace('|1|','|01|')},
    @{name='signed sequence';text=$requestText.Replace('|1|','|+1|')},
    @{name='encoded payload whitespace';text=$requestText.Replace($encoded,(' '+$encoded))},
    @{name='invalid base64';text=$requestText.Replace($encoded,'?')},
    @{name='decoded invalid JSON';text=$requestText.Replace($encoded,[Convert]::ToBase64String($utf8.GetBytes('[]')))},
    @{name='control character';text=$requestText.Replace('|1|',("|1"+[char]0+"|"))},
    @{name='non-ASCII envelope';text=$requestText.Replace('|1|',("|1"+[char]0x00e9+"|"))}
)) { $item=$entry; Case $item.name { Reject { $fixture::Decode($utf8.GetBytes($item.text),$false,$session) } } }
Case 'frame over 8192 rejected before payload parse' { Reject { $fixture::Decode($utf8.GetBytes(('X'*8192)+"`n"),$false,$session) } }
Case 'request not accepted as response' { Reject { $fixture::Decode($request,$true,$session) } }
Case 'response not accepted as request' { Reject { $fixture::Decode($response,$false,$session) } }
$failed=@($results | Where-Object {-not $_.passed})
[pscustomobject]@{suite='parent-control-pure-wire';runtime_sha256_at_read=$sourceHash;expected=41;executed=$results.Count
    passed=$results.Count-$failed.Count;failed=$failed.Count;results=@($results.ToArray())
    actual_pure_slices_executed=$true;whole_runtime_executed=$false;native_or_io_executed=$false
    full_protocol_accepted=$false} | ConvertTo-Json -Depth 5 -Compress
if ($results.Count -ne 41 -or $failed.Count -ne 0) { throw 'Pure control wire regressions failed.' }
