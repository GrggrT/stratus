// UNQUALIFIED S-only original launcher transport. No numeric-handle adoption,
// approval installation, normal build acceptance or automatic native release.
// All blocking stream operations belong to retained workers, never the debugger
// thread. A recorded write is not evidence that the peer received its ACK.
using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using Microsoft.Win32.SafeHandles;

namespace Stratus.RuntimeBuild {
    internal sealed class SupervisorControlOwner {
        private sealed class Outcome {
            internal bool Attempted, Returned, Succeeded;
            internal int Error;
        }
        private sealed class Slot {
            internal IntPtr Raw;
            internal readonly Outcome Inheritance = new Outcome(), Close = new Outcome();
            internal SafeFileHandle Wrapper;
            internal FileStream Stream;
            internal bool WrapAttempted, WrapReturned, StreamAttempted, StreamReturned, FinalizerSuppressed;
        }
        private sealed class Pair {
            internal readonly Slot Parent = new Slot(), Child = new Slot();
            internal readonly Outcome Creation = new Outcome();
        }
        private sealed class Worker {
            internal Thread Thread;
            internal bool ConstructionAttempted, Constructed, StartAttempted, Started;
            internal bool ReadAttempted, ReadReturned, WriteAttempted, WriteReturned, FlushAttempted, FlushReturned;
            internal bool JoinAttempted, JoinReturned, Joined, Eof;
            internal int LastUseSettled, ReadCount;
        }
        [StructLayout(LayoutKind.Sequential)] private struct TimeParts { internal uint Low, High; }
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool CreatePipe(out IntPtr read, out IntPtr write, IntPtr security, uint size);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool SetHandleInformation(IntPtr handle, uint mask, uint flags);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool GetHandleInformation(IntPtr handle, out uint flags);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool DuplicateHandle(IntPtr from, IntPtr handle, IntPtr to, out IntPtr result, uint access, bool inherit, uint options);
        [DllImport("kernel32.dll")] private static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll")] private static extern uint GetCurrentProcessId();
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool GetProcessTimes(IntPtr process, out TimeParts created, out TimeParts exited, out TimeParts kernel, out TimeParts user);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool CloseHandle(IntPtr handle);

        internal sealed class LaunchEndpoints {
            private readonly SupervisorControlOwner original;
            internal readonly IntPtr Input, Output, Error, SupervisorObservation;
            internal readonly int SupervisorId;
            internal readonly long SupervisorBirth;
            internal LaunchEndpoints(SupervisorControlOwner original, IntPtr input, IntPtr output, IntPtr error,
                    IntPtr observer, int id, long birth) {
                this.original=original; Input=input; Output=output; Error=error;
                SupervisorObservation=observer; SupervisorId=id; SupervisorBirth=birth;
            }
            internal IntPtr[] CopyHandles() {
                original.AssertBirthSlots(this);
                return new IntPtr[] { Input, Output, Error, SupervisorObservation };
            }
            internal string[] CopyArguments(ulong startedTick, string nonce) {
                original.AssertBirthSlots(this);
                return SupervisorWire.EncodeEntryArguments(Input,Output,Error,SupervisorObservation,
                    SupervisorId,SupervisorBirth,startedTick,nonce);
            }
        }

        private readonly BuildSupervisor owner;
        private readonly Pair input = new Pair(), output = new Pair(), error = new Pair();
        private readonly Slot observer = new Slot();
        private readonly Outcome duplicate = new Outcome(), identity = new Outcome();
        private readonly Worker writer = new Worker(), outputReader = new Worker(), errorReader = new Worker();
        private readonly object captureGate = new object();
        private readonly byte[] stdout = new byte[SupervisorWire.MaximumOutputBytes], stderr = new byte[SupervisorWire.MaximumErrorBytes];
        private readonly byte[] stdoutAckAttempt = new byte[SupervisorWire.MaximumOutputBytes];
        private int ackWriteAttempted;
        private int stdoutCount, stderrCount, parsed, records;
        private bool opened, openAttempted, childRetirementAttempted, childRetired, workersAttempted, failed, inputClosing;
        private bool ackQueued, ackWritten;
        private byte[] startPacket, pendingAck, offerBytes;
        private string nonce;
        private LaunchEndpoints endpoints;
        private SupervisorBeginP begin;
        private SupervisorPwOffer offer;
        private SupervisorTerminal terminal;
        private SupervisorCaptured capture;
        private TimeParts created, exited, kernel, user;
        private SupervisorControlOwner(BuildSupervisor owner) { this.owner=owner; }
        internal static SupervisorControlOwner Prepare(BuildSupervisor owner) {
            if (owner==null) throw new ArgumentNullException("original supervisor");
            return new SupervisorControlOwner(owner);
        }
        private static bool Valid(IntPtr value) { return value.ToInt64()>0; }
        private static void Require(bool okay) {
            if (!okay) throw new InvalidOperationException("Original S/L transport ownership or native outcome differs.");
        }
        private void Admission(BuildSupervisor candidate) {
            Require(Object.ReferenceEquals(owner,candidate)); owner.AssertControlOperation(this);
            lock(captureGate) Require(!failed);
        }
        private void Fail() { lock(captureGate) { failed=true; inputClosing=true; Monitor.PulseAll(captureGate); } }
        private static void Record(Outcome operation, bool okay) {
            operation.Error=Marshal.GetLastWin32Error(); operation.Succeeded=okay; operation.Returned=true;
            Require(okay);
        }
        private void OpenPair(Pair pair, bool childReads, IntPtr security) {
            Require(!pair.Creation.Attempted); pair.Creation.Attempted=true;
            bool okay;
            if (childReads) okay=CreatePipe(out pair.Child.Raw,out pair.Parent.Raw,security,4096);
            else okay=CreatePipe(out pair.Parent.Raw,out pair.Child.Raw,security,4096);
            Record(pair.Creation,okay); Require(Valid(pair.Child.Raw)&&Valid(pair.Parent.Raw)&&pair.Child.Raw!=pair.Parent.Raw);
            SetInheritance(pair.Parent,false); SetInheritance(pair.Child,true);
        }
        private static void SetInheritance(Slot slot, bool inherit) {
            Require(Valid(slot.Raw)&&!slot.Inheritance.Attempted); slot.Inheritance.Attempted=true;
            Record(slot.Inheritance,SetHandleInformation(slot.Raw,1,inherit?1u:0u));
            uint flags; Require(GetHandleInformation(slot.Raw,out flags)&&((flags&1)!=0)==inherit);
        }
        internal void OpenForOriginalBirth(BuildSupervisor candidate) {
            Admission(candidate);
            try {
                Require(!openAttempted); openAttempted=true;
                IntPtr security=owner.BorrowOriginalControlSecurity(this);
                Require(security!=IntPtr.Zero);
                OpenPair(input,true,security); OpenPair(output,false,security); OpenPair(error,false,security);
                duplicate.Attempted=true;
                Record(duplicate,DuplicateHandle(GetCurrentProcess(),GetCurrentProcess(),GetCurrentProcess(),out observer.Raw,0x101000,true,0));
                Require(Valid(observer.Raw));
                identity.Attempted=true;
                Record(identity,GetProcessTimes(observer.Raw,out created,out exited,out kernel,out user));
                uint id=GetCurrentProcessId(); long birth=checked((long)(((ulong)created.High<<32)|created.Low));
                Require(id>0&&id<=Int32.MaxValue&&birth>0);
                endpoints=new LaunchEndpoints(this,input.Child.Raw,output.Child.Raw,error.Child.Raw,observer.Raw,(int)id,birth);
                opened=true; AssertBirthSlots(endpoints);
            } catch { Fail(); throw; }
        }
        private void AssertBirthSlots(LaunchEndpoints candidate) {
            owner.AssertControlOperation(this);
            Require(opened&&!childRetirementAttempted&&Object.ReferenceEquals(endpoints,candidate));
            Slot[] all={input.Parent,input.Child,output.Parent,output.Child,error.Parent,error.Child,observer};
            HashSet<long> values=new HashSet<long>();
            foreach(Slot slot in all) Require(Valid(slot.Raw)&&!slot.Close.Attempted&&values.Add(slot.Raw.ToInt64()));
            foreach(Slot slot in new Slot[]{input.Child,output.Child,error.Child,observer}) {
                uint flags; Require(GetHandleInformation(slot.Raw,out flags)&&(flags&1)!=0);
            }
        }
        internal LaunchEndpoints BorrowLaunchEndpoints(BuildSupervisor candidate) {
            Admission(candidate); AssertBirthSlots(endpoints); return endpoints;
        }
        private static void CloseOriginal(Slot slot) {
            Require(Valid(slot.Raw)&&!slot.Close.Attempted); slot.Close.Attempted=true;
            Record(slot.Close,CloseHandle(slot.Raw));
        }
        internal void RetireChildEndsAfterBirth(BuildSupervisor candidate) {
            Admission(candidate);
            try {
                Require(opened&&!childRetirementAttempted); childRetirementAttempted=true;
                // The original S launch calls this only after known L birth.
                // No role is reused after a failed/unknown close, even on failure.
                List<Exception> failures=new List<Exception>();
                foreach(Slot slot in new Slot[]{input.Child,output.Child,error.Child,observer}) {
                    try { CloseOriginal(slot); } catch(Exception e) { failures.Add(e); }
                }
                if(failures.Count!=0) throw new AggregateException(failures);
                childRetired=true;
            } catch { Fail(); throw; }
        }
        private static void MakeStream(Slot slot, FileAccess access) {
            Require(Valid(slot.Raw)&&!slot.Close.Attempted&&!slot.WrapAttempted); slot.WrapAttempted=true;
            slot.Wrapper=new SafeFileHandle(slot.Raw,false); slot.WrapReturned=true;
            slot.StreamAttempted=true; slot.Stream=new FileStream(slot.Wrapper,access,4096,false); slot.StreamReturned=true;
            GC.SuppressFinalize(slot.Stream); slot.FinalizerSuppressed=true;
        }
        private static void StartWorker(Worker worker, ThreadStart action, string name) {
            Require(!worker.ConstructionAttempted); worker.ConstructionAttempted=true;
            worker.Thread=new Thread(action); worker.Constructed=true;
            worker.Thread.IsBackground=true; worker.Thread.Name=name;
            worker.StartAttempted=true; worker.Thread.Start(); worker.Started=true;
        }
        internal void StartOriginalWorkers(BuildSupervisor candidate, byte[] sessionObservation, ulong startedTick,
                string originalNonce, int launcherId, long launcherBirth) {
            Admission(candidate);
            try {
                Require(childRetired&&!workersAttempted); workersAttempted=true; nonce=originalNonce;
                byte[] context=owner.CopyOriginalContext(this);
                Require(sessionObservation!=null&&sessionObservation.Length>0&&sessionObservation.Length<=SupervisorWire.MaximumObservationBytes);
                byte[] observation=(byte[])sessionObservation.Clone();
                byte[] header=SupervisorWire.EncodeStart(nonce,launcherId,launcherBirth,startedTick,
                    context.Length,SupervisorWire.Hash(context),observation.Length,SupervisorWire.Hash(observation));
                int total=checked(header.Length+context.Length+observation.Length);
                Require(total<=SupervisorWire.MaximumInputBytes);
                startPacket=new byte[total]; Buffer.BlockCopy(header,0,startPacket,0,header.Length);
                Buffer.BlockCopy(context,0,startPacket,header.Length,context.Length);
                Buffer.BlockCopy(observation,0,startPacket,header.Length+context.Length,observation.Length);
                MakeStream(input.Parent,FileAccess.Write); MakeStream(output.Parent,FileAccess.Read); MakeStream(error.Parent,FileAccess.Read);
                StartWorker(outputReader,delegate { ReadWorker(output,outputReader,false); },"STRATUS-S-L-stdout");
                StartWorker(errorReader,delegate { ReadWorker(error,errorReader,true); },"STRATUS-S-L-stderr");
                StartWorker(writer,WriteWorker,"STRATUS-S-L-stdin");
            } catch { Fail(); throw; }
        }
        private void ReadWorker(Pair pair, Worker worker, bool standardError) {
            try {
                byte[] chunk=new byte[1024];
                for(;;) {
                    worker.ReadAttempted=true; worker.ReadReturned=false;
                    int count=pair.Parent.Stream.Read(chunk,0,chunk.Length);
                    int ackAtReadReturn=Interlocked.CompareExchange(ref ackWriteAttempted,0,0);
                    worker.ReadCount=count; worker.ReadReturned=true;
                    lock(captureGate) {
                        Require(count>=0&&count<=chunk.Length);
                        if(count==0) { worker.Eof=true; return; }
                        byte[] target=standardError?stderr:stdout; int offset=standardError?stderrCount:stdoutCount;
                        Require(count<=target.Length-offset); Buffer.BlockCopy(chunk,0,target,offset,count);
                        if(!standardError) for(int i=offset;i<offset+count;++i) stdoutAckAttempt[i]=(byte)ackAtReadReturn;
                        if(standardError) stderrCount+=count; else stdoutCount+=count;
                    }
                }
            } catch { Fail(); }
            finally { Interlocked.Exchange(ref worker.LastUseSettled,1); }
        }
        private void WritePacket(byte[] bytes) {
            Require(bytes!=null&&bytes.Length>0);
            writer.WriteAttempted=true; writer.WriteReturned=false; writer.FlushAttempted=false; writer.FlushReturned=false;
            input.Parent.Stream.Write(bytes,0,bytes.Length); writer.WriteReturned=true;
            writer.FlushAttempted=true; input.Parent.Stream.Flush(); writer.FlushReturned=true;
        }
        private void WriteWorker() {
            try {
                WritePacket(startPacket);
                byte[] ack;
                lock(captureGate) {
                    while(pendingAck==null&&!inputClosing&&!failed) Monitor.Wait(captureGate,50);
                    if(inputClosing||failed) return;
                    ack=pendingAck;
                }
                Interlocked.Exchange(ref ackWriteAttempted,1);
                WritePacket(ack);
                lock(captureGate) ackWritten=true;
                // Only this settled writer retires its parent input end. Flush
                // return and exact native close are separate retained outcomes.
                CloseOriginal(input.Parent);
                input.Parent.Wrapper.SetHandleAsInvalid();
            } catch { Fail(); }
            finally { Interlocked.Exchange(ref writer.LastUseSettled,1); }
        }
        internal void PollOriginalMessages(BuildSupervisor candidate) {
            Admission(candidate);
            try {
                lock(captureGate) {
                    Require(stderrCount==0);
                    bool capturePublicationPending=false;
                    while(parsed<stdoutCount) {
                        int end=parsed;
                        while(end<stdoutCount&&stdout[end]!=10) { Require(end-parsed<SupervisorWire.MaximumRecordBytes-1); ++end; }
                        if(end==stdoutCount) { Require(!outputReader.Eof); break; }
                        byte[] row=new byte[end-parsed+1]; Buffer.BlockCopy(stdout,parsed,row,0,row.Length);
                        Require(records<SupervisorWire.MaximumOutputRecords);
                        if(records==0) begin=SupervisorWire.DecodeBeginP(row);
                        else if(records==1) { offer=SupervisorWire.DecodePwOffer(row); offerBytes=row; }
                        else {
                            Require(ackQueued);
                            for(int i=parsed;i<=end;++i) Require(stdoutAckAttempt[i]==1);
                            if(records==2) {
                                // The original deadline must be visible even if
                                // the ACK writer's return/close publication lags.
                                // This is correspondence, not writer settlement
                                // or independent proof of physical P/W closure.
                                terminal=SupervisorWire.DecodeTerminal(row);
                            } else {
                                Require(terminal!=null);
                                // Preserve the arrival marker and defer only the
                                // captured result during ACK-write publication.
                                if(!ackWritten) { capturePublicationPending=true; break; }
                                capture=SupervisorWire.DecodeCaptured(row);
                            }
                        }
                        parsed=end+1; ++records;
                    }
                    if(outputReader.Eof&&!capturePublicationPending) Require(parsed==stdoutCount);
                }
            } catch { Fail(); throw; }
        }
        internal SupervisorBeginP ReadOriginalBegin(BuildSupervisor candidate) { Admission(candidate); lock(captureGate) return begin; }
        internal SupervisorPwOffer ReadOriginalOffer(BuildSupervisor candidate) { Admission(candidate); lock(captureGate) return offer; }
        internal SupervisorTerminal ReadOriginalTerminal(BuildSupervisor candidate) { Admission(candidate); lock(captureGate) return terminal; }
        internal SupervisorCaptured ReadOriginalCapture(BuildSupervisor candidate) { Admission(candidate); lock(captureGate) return capture; }
        internal void AssertOriginalTerminal(BuildSupervisor candidate, SupervisorTerminal original) {
            Admission(candidate); lock(captureGate) Require(original!=null&&Object.ReferenceEquals(terminal,original));
        }
        internal bool OriginalAckClosedAndWriterSettled(BuildSupervisor candidate) {
            Admission(candidate);
            if(!Settled(writer)) return false;
            lock(captureGate) {
                Require(!failed&&ackQueued&&ackWritten&&input.Parent.Close.Attempted&&
                    input.Parent.Close.Returned&&input.Parent.Close.Succeeded&&
                    input.Parent.Wrapper!=null&&input.Parent.Wrapper.IsInvalid);
                return true;
            }
        }
        internal bool OriginalNormalOutputComplete(BuildSupervisor candidate) {
            Admission(candidate);
            bool outputSettled=Settled(outputReader), errorSettled=Settled(errorReader);
            if(!outputSettled||!errorSettled) return false;
            lock(captureGate) {
                Require(!failed&&outputReader.Eof&&errorReader.Eof&&stderrCount==0);
                // A reader can publish its final bounded bytes after this S
                // operation's parser snapshot. The next clock-bounded poll
                // must parse them, rejecting partial/surplus records at EOF;
                // do not mislabel this publication race as a missing frame.
                if(parsed<stdoutCount) return false;
                Require(parsed==stdoutCount&&records==SupervisorWire.MaximumOutputRecords&&
                    begin!=null&&offer!=null&&terminal!=null&&capture!=null);
                return true;
            }
        }
        internal void AssertOriginalOffer(BuildSupervisor candidate, SupervisorPwOffer original) {
            Admission(candidate); lock(captureGate) Require(original!=null&&Object.ReferenceEquals(offer,original)&&offerBytes!=null);
        }
        internal void PublishOriginalAck(BuildSupervisor candidate, SupervisorPwOffer original) {
            AssertOriginalOffer(candidate,original);
            try {
                lock(captureGate) {
                    Require(!ackQueued&&!inputClosing&&!outputReader.Eof&&!errorReader.Eof&&stderrCount==0);
                    pendingAck=SupervisorWire.EncodePwAck(nonce,SupervisorWire.Hash(offerBytes));
                    Require(startPacket.Length+pendingAck.Length<=SupervisorWire.MaximumInputBytes);
                    ackQueued=true; Monitor.PulseAll(captureGate);
                }
            } catch { Fail(); throw; }
        }
        internal void CloseInputOnFailure(BuildSupervisor candidate) {
            Require(Object.ReferenceEquals(owner,candidate)); owner.AssertOriginalControlOwner(this);
            lock(captureGate) { inputClosing=true; Monitor.PulseAll(captureGate); }
            // This only requests writer settlement. It cannot interrupt a blocked
            // native write or manufacture close/EOF/physical STOP evidence.
        }
        private static bool Settled(Worker worker) {
            if(!worker.StartAttempted) return !worker.ConstructionAttempted;
            if(!worker.Started||worker.Thread==null||Interlocked.CompareExchange(ref worker.LastUseSettled,0,0)!=1) return false;
            if(worker.JoinReturned&&worker.Joined) return true;
            worker.JoinAttempted=true; worker.JoinReturned=false;
            worker.Joined=worker.Thread.Join(0); worker.JoinReturned=true; return worker.Joined;
        }
        internal bool OriginalWorkersSettled(BuildSupervisor candidate) {
            Require(Object.ReferenceEquals(owner,candidate)); owner.AssertOriginalControlOwner(this);
            bool a=Settled(writer), b=Settled(outputReader), c=Settled(errorReader); return a&&b&&c;
        }
        // No Dispose/finalizer, new-open adoption or success release. Parent IO
        // ends and unknown worker/native outcomes remain original S-owned until
        // the separately reviewed terminal retirement path is implemented.
    }
}
