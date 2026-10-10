[CmdletBinding()]
param([string] $RuntimePath)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if (-not $RuntimePath) { $RuntimePath=Join-Path $PSScriptRoot '..\Stratus.ParentStage.Runtime.cs' }
# ONLY the actual BoundedControlIo class executes, against memory-only Stream
# fixtures and bounded managed threads. No OS pipe/process/Job/native runtime is
# constructed. Reflection below reads just this isolated fixture's owned worker
# Thread fields for bounded cleanup; it cannot reach a native runtime assembly.
$bytes=[IO.File]::ReadAllBytes([IO.Path]::GetFullPath($RuntimePath))
$text=[Text.UTF8Encoding]::new($false,$true).GetString($bytes)
$sha=[Security.Cryptography.SHA256]::Create()
try { $sourceHash=[BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
$start='    internal sealed class BoundedControlIo : IDisposable {'
$end='    public sealed class PrivateControlResponse {'
$a=$text.IndexOf($start,[StringComparison]::Ordinal);$b=$text.IndexOf($end,[StringComparison]::Ordinal)
if ($a -lt 0 -or $b -le $a -or $text.LastIndexOf($start,[StringComparison]::Ordinal) -ne $a) { throw 'Exact managed IO slice missing/ambiguous.' }
$slice=$text.Substring($a,$b-$a)
if ($slice -match 'DllImport|PipeStream|NativeChild|FileStream|Reflection|Environment|Activator|Process\.') { throw 'Unreviewed dependency in managed IO slice.' }
$ns='StratusControlDrainManaged'+[guid]::NewGuid().ToString('N')
$fixture=@'
internal static class ControlWire { internal const int FrameMaximum=8192; }
internal static class Files { internal static Exception Combine(Exception a,Exception b) {return a==null ? b : new AggregateException(a,b);} }
internal sealed class MemoryChannel {
 internal readonly object Sync=new object(); internal readonly Queue<byte> Bytes=new Queue<byte>(); internal bool Ended;
 internal void End() {lock(Sync){Ended=true;Monitor.PulseAll(Sync);}}
 internal int Read() {lock(Sync){while(Bytes.Count==0&&!Ended)Monitor.Wait(Sync);return Bytes.Count==0 ? -1 : Bytes.Dequeue();}}
 internal void Put(byte[] bytes,int offset,int count) {lock(Sync){if(Ended)throw new IOException("Fixture closed");if(Bytes.Count+count>8192)throw new IOException("Fixture byte bound");for(int i=0;i<count;i++)Bytes.Enqueue(bytes[offset+i]);Monitor.PulseAll(Sync);}}
}
internal sealed class MemoryEnd : Stream {
 internal readonly MemoryChannel Channel; internal readonly bool Reader;
 internal readonly ManualResetEventSlim FlushEntered=new ManualResetEventSlim(false),FlushAllowed=new ManualResetEventSlim(true);
 internal bool FailFlush,FailClose; internal int Closes,CloseThread;
 internal MemoryEnd(MemoryChannel channel,bool reader){Channel=channel;Reader=reader;}
 public override bool CanRead {get{return Reader;}} public override bool CanSeek{get{return false;}} public override bool CanWrite{get{return !Reader;}}
 public override long Length{get{throw new NotSupportedException();}} public override long Position{get{throw new NotSupportedException();}set{throw new NotSupportedException();}}
 public override int ReadByte(){if(!Reader)throw new InvalidOperationException();return Channel.Read();}
 public override int Read(byte[] b,int o,int c){if(c==0)return 0;int n=ReadByte();if(n<0)return 0;b[o]=(byte)n;return 1;}
 public override void Write(byte[] b,int o,int c){if(Reader)throw new InvalidOperationException();Channel.Put(b,o,c);}
 public override void Flush(){if(Reader)return;FlushEntered.Set();if(!FlushAllowed.Wait(2000))throw new TimeoutException("Fixture flush bound");if(FailFlush)throw new IOException("Fixture flush fault");}
 protected override void Dispose(bool disposing){if(!Reader){if(FailClose)throw new IOException("Fixture close fault");if(Interlocked.CompareExchange(ref Closes,1,0)==0){CloseThread=Thread.CurrentThread.ManagedThreadId;Channel.End();}}base.Dispose(disposing);}
 public override long Seek(long o,SeekOrigin origin){throw new NotSupportedException();}public override void SetLength(long n){throw new NotSupportedException();}
}
internal sealed class Pair : IDisposable {
 internal readonly MemoryChannel ToServer=new MemoryChannel(),ToClient=new MemoryChannel();
 internal readonly MemoryEnd ClientRead,ClientWrite,ServerRead,ServerWrite;
 internal readonly BoundedControlIo Client,Server;
 internal Pair(){ClientRead=new MemoryEnd(ToClient,true);ClientWrite=new MemoryEnd(ToServer,false);ServerRead=new MemoryEnd(ToServer,true);ServerWrite=new MemoryEnd(ToClient,false);Client=new BoundedControlIo(ClientRead,ClientWrite);Server=new BoundedControlIo(ServerRead,ServerWrite);try{Client.Start();Server.Start();}catch(Exception original){try{Dispose();}catch(Exception cleanup){throw new AggregateException(original,cleanup);}throw;}}
 public void Dispose(){
  ClientWrite.FlushAllowed.Set();ServerWrite.FlushAllowed.Set();ToServer.End();ToClient.End();
  Client.Fail(new IOException("Fixture teardown only"));Server.Fail(new IOException("Fixture teardown only"));
  foreach(BoundedControlIo io in new[]{Client,Server})foreach(string name in new[]{"reader","writer"}){
   Thread thread=(Thread)typeof(BoundedControlIo).GetField(name,System.Reflection.BindingFlags.Instance|System.Reflection.BindingFlags.NonPublic).GetValue(io);
   if(thread!=null&&(thread.ThreadState&System.Threading.ThreadState.Unstarted)==0&&!thread.Join(2000))throw new TimeoutException("Owned managed fixture worker did not exit");
  }
  ClientRead.Dispose();ClientWrite.FailClose=false;ClientWrite.Dispose();ServerRead.Dispose();ServerWrite.FailClose=false;ServerWrite.Dispose();
  foreach(MemoryEnd end in new[]{ClientRead,ClientWrite,ServerRead,ServerWrite}){end.FlushEntered.Dispose();end.FlushAllowed.Dispose();}
 }
}
public static class DrainFixture {
 static void Check(bool yes,string message){if(!yes)throw new Exception(message);}
 static void Reject(Action action){bool rejected=false;try{action();}catch{rejected=true;}Check(rejected,"Invalid operation accepted");}
 static void Until(Func<bool> predicate){Stopwatch timer=Stopwatch.StartNew();while(!predicate()){if(timer.ElapsedMilliseconds>=1500)throw new TimeoutException("Managed fixture observation bound");Thread.Sleep(1);}}
 static void Drain(Pair p){Until(delegate{bool c=p.Client.PollDrain(),s=p.Server.PollDrain();return c&&s;});}
 static void Test(List<string> results,string name,Action<Pair> action){try{using(Pair p=new Pair()){action(p);}results.Add("PASS "+name);}catch(Exception e){results.Add("FAIL "+name+": "+e.GetBaseException().Message);}}
 public static string[] Run(){
  List<string> r=new List<string>();
  Test(r,"two-way drain closes each writer on worker and joins both",delegate(Pair p){
   int caller=Thread.CurrentThread.ManagedThreadId;p.Client.BeginDrain(false);p.Server.BeginDrain(true);Drain(p);
   Check(p.ClientWrite.Closes==1&&p.ServerWrite.Closes==1,"Writer closed more/less than once");
   Check(p.ClientWrite.CloseThread!=caller&&p.ServerWrite.CloseThread!=caller,"Close ran on supervisor thread");
   Check(p.Client.PollDrain()&&p.Server.PollDrain(),"Completed drain not stable");p.Client.Dispose();p.Server.Dispose();
  });
  Test(r,"busy final write must flush without discard before half-close",delegate(Pair p){
   p.ServerWrite.FlushAllowed.Reset();p.Server.Enqueue(new byte[]{123,125,10});Check(p.ServerWrite.FlushEntered.Wait(1000),"Writer did not enter flush");
   byte[] frame=null;Until(delegate{frame=p.Client.Take();return frame!=null;});Check(frame.Length==3,"Final frame discarded");
   p.Client.BeginDrain(false);p.Server.BeginDrain(true);Check(!p.Server.PollDrain()&&!p.Client.PollDrain(),"Busy writer became drained");
   Check(p.ServerWrite.Closes==0,"Writer closed before final flush");p.ServerWrite.FlushAllowed.Set();Drain(p);
  });
  Test(r,"retained peer writer is pending not EOF proof",delegate(Pair p){
   p.Client.BeginDrain(false);Check(!p.Client.PollDrain(),"Live peer became drained");Reject(delegate{p.Client.Dispose();});
   p.Server.BeginDrain(true);Drain(p);
  });
  Test(r,"active EOF cannot be promoted to a clean terminal drain",delegate(Pair p){
   p.ToClient.End();Until(delegate{try{p.Client.AssertHealthy();return false;}catch{return true;}});
   Reject(delegate{p.Client.BeginDrain(false);});Reject(delegate{p.Client.PollDrain();});
  });
  Test(r,"complete unconsumed inbound frame is not discarded",delegate(Pair p){
   p.ToClient.Put(new byte[]{123,125,10},0,3);Until(delegate{try{p.Client.AssertNoTrailing();return false;}catch{return true;}});
   Reject(delegate{p.Client.BeginDrain(false);});Reject(delegate{p.Client.PollDrain();});
  });
  Test(r,"partial inbound frame is not discarded",delegate(Pair p){
   p.ToClient.Put(new byte[]{123},0,1);Until(delegate{try{p.Client.AssertNoTrailing();return false;}catch{return true;}});
   Reject(delegate{p.Client.BeginDrain(false);});Reject(delegate{p.Client.PollDrain();});
  });
  Test(r,"new byte after terminal admission faults permanently",delegate(Pair p){
   p.Client.BeginDrain(false);p.ToClient.Put(new byte[]{123},0,1);
   Until(delegate{try{p.Client.PollDrain();return false;}catch{return true;}});Reject(delegate{p.Client.PollDrain();});
  });
  Test(r,"normal IO cannot resume after drain admission",delegate(Pair p){
   p.Client.BeginDrain(false);Reject(delegate{p.Client.AssertHealthy();});Reject(delegate{p.Client.Enqueue(new byte[]{123,125,10});});Reject(delegate{p.Client.Take();});
   p.Server.BeginDrain(true);Drain(p);
  });
  Test(r,"duplicate drain admission is permanently rejected",delegate(Pair p){
   p.Client.BeginDrain(false);Reject(delegate{p.Client.BeginDrain(false);});Reject(delegate{p.Client.PollDrain();});
  });
  Test(r,"pre-existing fault cannot be recast as drain",delegate(Pair p){
   p.Client.Fail(new IOException("Original fault"));Reject(delegate{p.Client.BeginDrain(false);});Reject(delegate{p.Client.PollDrain();});
  });
  Test(r,"half-close flush failure never marks writer closed",delegate(Pair p){
   p.Client.BeginDrain(false);p.ServerWrite.FailFlush=true;p.Server.BeginDrain(true);
   Until(delegate{try{p.Server.PollDrain();return false;}catch{return true;}});Check(p.ServerWrite.Closes==0,"Failed flush closed writer");Reject(delegate{p.Server.Dispose();});
  });
  Test(r,"half-close close failure is not clean completion",delegate(Pair p){
   p.Client.BeginDrain(false);p.ServerWrite.FailClose=true;p.Server.BeginDrain(true);
   Until(delegate{try{p.Server.PollDrain();return false;}catch{return true;}});Check(p.ServerWrite.Closes==0,"Failed close counted as closed");Reject(delegate{p.Server.Dispose();});
  });
  return r.ToArray();
 }
}
'@
$program='using System;using System.IO;using System.Threading;using System.Diagnostics;using System.Collections.Generic; namespace '+$ns+' {'+$slice+$fixture+'}'
$types=@(Add-Type -TypeDefinition $program -Language CSharp -PassThru)
$runner=@($types | Where-Object Name -ceq 'DrainFixture')[0]
$results=@($runner::Run());$failed=@($results | Where-Object {$_ -notlike 'PASS *'})
[pscustomobject]@{suite='parent-control-managed-drain';runtime_sha256_at_read=$sourceHash;expected=12;executed=$results.Count
 passed=$results.Count-$failed.Count;failed=$failed.Count;results=$results;actual_managed_io_slice_executed=$true
 memory_streams_and_managed_threads_only=$true;os_pipe_or_process_executed=$false;whole_native_runtime_executed=$false
 native_timing_or_full_protocol_accepted=$false}|ConvertTo-Json -Depth 5 -Compress
if($results.Count -ne 12 -or $failed.Count -ne 0){throw 'Isolated managed drain checks failed.'}
