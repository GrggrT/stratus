// UNQUALIFIED original L endpoint and entry. Wire correspondence does not prove
// shared writer exclusion. Original-session binding and native qualification
// remain unconditional denials; no success/sealing result is manufactured here.
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;

namespace Stratus.RuntimeBuild {
    internal sealed class LauncherControlClient {
        private sealed class StreamSlot {
            internal readonly IntPtr Raw;
            internal SafeFileHandle Handle;
            internal FileStream Stream;
            internal bool InheritanceAttempted, InheritanceReturned, InheritanceOkay;
            internal int InheritanceError;
            internal bool WrapperAttempted, WrapperReturned, StreamAttempted, StreamReturned, FinalizerSuppressed;
            internal StreamSlot(IntPtr raw) { Raw=raw; }
        }
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool SetHandleInformation(IntPtr h, uint mask, uint flags);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool GetHandleInformation(IntPtr h, out uint flags);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern uint GetProcessId(IntPtr h);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern uint WaitForSingleObject(IntPtr h, uint ms);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool GetProcessTimes(IntPtr h, out long created, out long exited, out long kernel, out long user);
        [DllImport("kernel32.dll")] private static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll")] private static extern uint GetCurrentProcessId();
        [DllImport("kernel32.dll")] private static extern ulong GetTickCount64();
        [DllImport("kernel32.dll", SetLastError=true)] private static extern IntPtr GetStdHandle(int kind);
        private readonly SupervisorEntry entry;
        private readonly StreamSlot input, output, error, supervisor;
        private readonly int originalThread;
        private BuildTerminalCoordinator coordinator;
        private SupervisorStart start;
        private BuildTerminalClosure terminalClosure;
        private SupervisorTerminal terminalNotice;
        private byte[] context, observation, offered;
        private bool openAttempted, opened, failed, bindingAttempted, beginSent, offerSent, ackReadAttempted, ackReceived, captured;
        private bool terminalPublicationAttempted, terminalSent;
        private bool readAttempted, readReturned, writeAttempted, writeReturned, flushAttempted, flushReturned;
        private int lastRead, inputBytes, outputBytes;
        private ulong lastTick, producerOrigin, producerDeadline;
        private LauncherControlClient(SupervisorEntry original) {
            entry=original; originalThread=Thread.CurrentThread.ManagedThreadId;
            input=new StreamSlot(original.Input); output=new StreamSlot(original.Output);
            error=new StreamSlot(original.Error); supervisor=new StreamSlot(original.SupervisorProcess);
        }
        internal static LauncherControlClient PrepareOriginalEntry(string[] arguments) {
            return new LauncherControlClient(SupervisorWire.DecodeEntryArguments(arguments));
        }
        private static void Require(bool value) { if(!value) throw new InvalidOperationException("Original L/S control ownership differs."); }
        private static void DemandQualifiedNativeEntry() {
            throw new InvalidOperationException("Original S/L native entry and shared writer profile remain independently unqualified.");
        }
        private static void DemandActualSharedSessionBinding() {
            // This must become actual native token/namespace/endpoint lineage,
            // never acceptance of the opaque START observation bytes themselves.
            throw new InvalidOperationException("Original shared S/L/P writer namespace and session binding are not implemented.");
        }
        private void Boundary() {
            Require(!failed&&Thread.CurrentThread.ManagedThreadId==originalThread);
            ulong tick=GetTickCount64();
            Require(tick>=entry.StartedTick&&tick>=lastTick&&tick<entry.DeadlineTick);
            if(!beginSent) Require(tick<entry.PreProducerDeadlineTick);
            if(beginSent) Require(tick<producerDeadline);
            if(terminalNotice!=null) Require(tick>=terminalNotice.TerminalStartedTick&&tick<terminalNotice.TerminalDeadlineTick);
            lastTick=tick;
            Require(WaitForSingleObject(supervisor.Raw,0)==258&&GetProcessId(supervisor.Raw)==(uint)entry.SupervisorProcessId);
            long created,exited,kernel,user;
            Require(GetProcessTimes(supervisor.Raw,out created,out exited,out kernel,out user)&&created==entry.SupervisorBirth);
        }
        private static void RetireInheritance(StreamSlot slot) {
            uint flags; Require(GetHandleInformation(slot.Raw,out flags)&&(flags&1)!=0);
            slot.InheritanceAttempted=true;
            bool okay=SetHandleInformation(slot.Raw,1,0); slot.InheritanceError=Marshal.GetLastWin32Error();
            slot.InheritanceOkay=okay; slot.InheritanceReturned=true; Require(okay);
            Require(GetHandleInformation(slot.Raw,out flags)&&(flags&1)==0);
        }
        private static void OpenStream(StreamSlot slot, FileAccess access) {
            slot.WrapperAttempted=true; slot.Handle=new SafeFileHandle(slot.Raw,false); slot.WrapperReturned=true;
            slot.StreamAttempted=true; slot.Stream=new FileStream(slot.Handle,access,4096,false); slot.StreamReturned=true;
            GC.SuppressFinalize(slot.Stream); slot.FinalizerSuppressed=true;
        }
        private byte[] ReadExactly(int count) {
            Require(count>0&&count<=SupervisorWire.MaximumContextBytes&&inputBytes<=SupervisorWire.MaximumInputBytes-count);
            byte[] bytes=new byte[count]; int offset=0;
            while(offset<count) {
                Boundary(); readAttempted=true; readReturned=false;
                lastRead=input.Stream.Read(bytes,offset,Math.Min(4096,count-offset)); readReturned=true;
                Require(lastRead>0&&lastRead<=count-offset); offset+=lastRead; inputBytes+=lastRead; Boundary();
            }
            return bytes;
        }
        private byte[] ReadRecord() {
            byte[] bytes=new byte[SupervisorWire.MaximumRecordBytes]; int count=0;
            while(count<bytes.Length) {
                byte[] one=ReadExactly(1); bytes[count++]=one[0];
                if(one[0]==10) { byte[] result=new byte[count]; Buffer.BlockCopy(bytes,0,result,0,count); return result; }
            }
            throw new InvalidDataException("Original L record exceeded its fixed bound.");
        }
        private void WriteRecord(byte[] bytes) {
            Require(bytes!=null&&bytes.Length>0&&bytes.Length<=SupervisorWire.MaximumRecordBytes&&outputBytes<=SupervisorWire.MaximumOutputBytes-bytes.Length);
            Boundary(); writeAttempted=true; writeReturned=false;
            output.Stream.Write(bytes,0,bytes.Length); writeReturned=true;
            flushAttempted=true; flushReturned=false; output.Stream.Flush(); flushReturned=true;
            outputBytes+=bytes.Length; Boundary();
        }
        internal void OpenOriginalStart() {
            Require(!openAttempted&&!failed); openAttempted=true;
            try {
                DemandQualifiedNativeEntry();
                Require(GetStdHandle(-10)==input.Raw&&GetStdHandle(-11)==output.Raw&&GetStdHandle(-12)==error.Raw);
                foreach(StreamSlot slot in new StreamSlot[]{input,output,error,supervisor}) RetireInheritance(slot);
                Boundary(); OpenStream(input,FileAccess.Read); OpenStream(output,FileAccess.Write);
                start=SupervisorWire.DecodeStartHeader(ReadRecord());
                long born,exited,kernel,user;
                Require(start.Nonce==entry.Nonce&&start.StartedTick==entry.StartedTick&&
                    start.LauncherProcessId==(int)GetCurrentProcessId()&&
                    GetProcessTimes(GetCurrentProcess(),out born,out exited,out kernel,out user)&&born==start.LauncherBirth);
                context=ReadExactly(start.ContextBytes); observation=ReadExactly(start.ObservationBytes);
                Require(SupervisorWire.Hash(context)==start.ContextSha256&&SupervisorWire.Hash(observation)==start.ObservationSha256);
                DemandActualSharedSessionBinding();
                Boundary(); opened=true;
            } catch { failed=true; throw; }
        }
        internal BuildTerminalCoordinator PrepareOriginalCoordinator() {
            Boundary(); Require(opened&&!bindingAttempted&&coordinator==null); bindingAttempted=true;
            coordinator=BuildTerminalCoordinator.PrepareSupervised(context,this); return coordinator;
        }
        internal void AssertPreparation(byte[] raw) {
            Boundary(); Require(opened&&bindingAttempted&&coordinator==null&&Object.ReferenceEquals(context,raw));
        }
        private void RequireCoordinator(BuildTerminalCoordinator original) {
            Require(Object.ReferenceEquals(coordinator,original)); original.AssertSupervisorOperation(this); Boundary();
        }
        internal void PublishProducerBegin(BuildTerminalCoordinator original, ProducerDeadlineSnapshot clock) {
            RequireCoordinator(original); Require(!beginSent&&clock!=null&&clock.StartedTick>=entry.StartedTick&&
                clock.StartedTick<entry.PreProducerDeadlineTick&&clock.DeadlineTick==checked(clock.StartedTick+60000));
            producerOrigin=clock.StartedTick; producerDeadline=clock.DeadlineTick;
            WriteRecord(SupervisorWire.EncodeBeginP(entry.Nonce,producerOrigin,producerDeadline)); beginSent=true;
        }
        internal void PublishOriginalOffer(BuildTerminalCoordinator original, byte[] actualOffer) {
            RequireCoordinator(original); Require(beginSent&&!offerSent&&actualOffer!=null);
            SupervisorPwOffer value=SupervisorWire.DecodePwOffer(actualOffer);
            Require(value.Nonce==entry.Nonce&&value.ContextSha256==start.ContextSha256&&
                value.ProducerStartedTick==producerOrigin&&value.ProducerDeadlineTick==producerDeadline);
            offered=(byte[])actualOffer.Clone(); WriteRecord(offered); offerSent=true;
        }
        internal string OriginalNonce(BuildTerminalCoordinator original) { RequireCoordinator(original); return entry.Nonce; }
        internal void ReceiveOriginalAck(BuildTerminalCoordinator original) {
            RequireCoordinator(original); Require(offerSent&&!ackReadAttempted); ackReadAttempted=true;
            try {
                SupervisorPwAck ack=SupervisorWire.DecodePwAck(ReadRecord());
                Require(ack.Nonce==entry.Nonce&&ack.OfferSha256==SupervisorWire.Hash(offered));
                Boundary(); readAttempted=true; readReturned=false; lastRead=input.Stream.ReadByte(); readReturned=true;
                Require(lastRead==-1); Boundary(); ackReceived=true;
            } catch { failed=true; throw; }
        }
        internal void AssertOriginalResume(BuildTerminalCoordinator original) {
            RequireCoordinator(original); Require(beginSent&&offerSent&&ackReadAttempted&&ackReceived&&!terminalPublicationAttempted&&!captured);
        }
        internal void PublishOriginalTerminal(BuildTerminalCoordinator original, BuildTerminalClosure value) {
            RequireCoordinator(original);
            Require(beginSent&&offerSent&&ackReceived&&!terminalPublicationAttempted&&!captured&&value!=null&&value.Record!=null);
            original.AssertOriginalTerminalPublication(this,value);
            terminalPublicationAttempted=true; terminalClosure=value;
            Require(value.TerminalStartedTick>=producerOrigin&&value.TerminalStartedTick<=lastTick&&
                value.TerminalStartedTick<producerDeadline&&
                value.TerminalDeadlineTick==checked(value.TerminalStartedTick+SupervisorWire.PostTerminalMilliseconds));
            byte[] frame=SupervisorWire.EncodeTerminal(entry.Nonce,value.TerminalStartedTick,
                value.ProducerExitFileTime,value.GuardianExitFileTime,start.ContextSha256,
                SupervisorWire.Hash(offered),SupervisorWire.Hash(Encoding.ASCII.GetBytes(value.Record.Body)));
            terminalNotice=SupervisorWire.DecodeTerminal(frame);
            // Retain the exact original origin before a potentially blocking
            // write/flush. Sending or S receipt never starts a fresh interval.
            Boundary(); WriteRecord(frame); terminalSent=true;
        }
        internal void PublishCaptured(BuildTerminalCoordinator original, BuildOutputCapture value) {
            RequireCoordinator(original); Require(ackReceived&&terminalSent&&terminalNotice!=null&&!captured&&value!=null&&
                value.Terminal!=null&&Object.ReferenceEquals(value.Terminal,terminalClosure)&&value.Terminal.Record!=null);
            string bodyHash=SupervisorWire.Hash(Encoding.ASCII.GetBytes(value.Terminal.Record.Body));
            Require(bodyHash==terminalNotice.TerminalBodySha256);
            byte[] frame=SupervisorWire.EncodeCaptured(entry.Nonce,start.ContextSha256,
                bodyHash,value.OutputBytes,value.OutputSha256,value.StdoutBytes,value.StdoutSha256,terminalNotice.TerminalSha256);
            WriteRecord(frame); captured=true;
        }
        // Source custody remains with this original entry. No finalizer/Dispose
        // inference, foreign handle adoption or success release is provided.
    }

    internal static class LauncherEntryProgram {
        // Separate pinned L executable entry; not an alternate P/W entry flag.
        internal static int Main(string[] arguments) {
            LauncherControlClient control=null; BuildTerminalCoordinator coordinator=null;
            try {
                control=LauncherControlClient.PrepareOriginalEntry(arguments);
                control.OpenOriginalStart(); coordinator=control.PrepareOriginalCoordinator(); coordinator.Start();
                for(;;) {
                    BuildOutputCapture captured=coordinator.Poll();
                    if(captured!=null) throw new InvalidOperationException("Captured output is not a sealed accepted build; the success handoff is not implemented.");
                    Thread.Sleep(1);
                }
            } catch {
                // Returning from original L releases its process-owned state;
                // it does not prove P/W containment. S retains original handles
                // and owns the external P-first failure/physical observations.
                if(coordinator!=null) {
                    try { while(!coordinator.PollContainment()) Thread.Sleep(1); coordinator.ReleaseAfterContainment(); }
                    catch { /* Unknown outcomes remain non-accepted under S. */ }
                }
                return 71;
            }
        }
    }
}
