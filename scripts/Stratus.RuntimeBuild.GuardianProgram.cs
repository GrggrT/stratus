// UNQUALIFIED standalone owner-watch entrypoint. Compile separately from the
// bootstrap library, only in an explicitly qualified build/execution window.
// This first stage NEVER returns success and does NOT authorize producer Resume.
// Guarded W-local compiler Job/root transfer is wired but remains unqualified;
// compiler birth/root handoff and success terminal are not enabled.
// ContextCapture, CompilerJobWire/CompilerRootWire and both transfer owners belong to this
// separate entry assembly. Its private raw
// bytes/digest are transport correspondence, never context approval or permission.
using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;

namespace Stratus.RuntimeBuild.GuardianEntry {
    internal static partial class Program {
        private const ulong StartupMilliseconds = 60000;
        private const int StopMilliseconds = 25000;
        private const uint WaitObject = 0, WaitTimeout = 258, WaitFailed = 0xffffffff;
        private static IntPtr producer;
        private static IntPtr producerTransfer;
        private static IntPtr contextSection;
        private static uint producerPid;
        private static long producerBirth;
        private static ulong startedTick, deadlineTick, previousTick;
        private static bool identityEstablished;
        private static int writerFailed;
        private static Stream readyStream; // Held until this disposable process exits.
        private static Thread readyWriter;
        private static IntPtr controlReadHandle, controlWriteHandle;
        private static SafePipeHandle controlReadSafe, controlWriteSafe;
        private static AnonymousPipeClientStream controlInput, controlOutput;
        private static Thread controlReader, controlWriter;
        private static bool controlReaderStartAttempted, controlReaderStartReturned, controlWriterStartAttempted, controlWriterStartReturned;
        private static bool readyWriterStartAttempted, readyWriterStartReturned;
        private static int controlFailed, controlHelloObserved, controlHelloWritten, controlAckPermit, controlAckWritten;
        private static int controlAckWriteAttempted;
        private static bool controlAckAttempted;
        private static Stratus.RuntimeBuild.ContextCapture contextCapture;
        private static bool protocolStartAttempted;
        private static GuardianJobTransfer jobTransfer;
        private static GuardianRootTransfer rootTransfer;
        private static byte[] jobAcknowledgement;
        private static int jobAckWritten, jobAckWriteAttempted;
        private static bool jobAckWriteReturned;
        private static byte[] rootAcknowledgement;
        private static int rootAckWritten, rootAckWriteAttempted;
        private static bool rootAckWriteReturned;
        private static int stopAttempted;
        private static Stopwatch stopClock;
        private static bool producerStopAttempted, producerStopReturned, producerStopSucceeded;
        private static int producerStopError;
        private static bool transferCompareAttempted, transferCompareReturned, transferCompareSucceeded;
        private static int transferCompareError;
        private static Thread originalMainThread;
        private static GuardianTerminalObservation terminalObservation;
        private static NormalProducerExitObservation normalProducerExit;
        private static int normalTerminalFailed;

        // Retained with the original channel owners, not constructed from a
        // caller's receipt/Boolean. Only their actual IO publishes these facts.
        private sealed class GuardianTerminalObservation {
            internal readonly GuardianJobTransfer Job;
            internal readonly GuardianRootTransfer Root;
            internal readonly IntPtr Producer, ReadHandle, WriteHandle;
            internal readonly uint ProducerId;
            internal readonly long ProducerBirth;
            internal readonly Stratus.RuntimeBuild.ContextMetadata Context;
            internal readonly Thread Reader, Writer;
            internal readonly AnonymousPipeClientStream Input, Output;
            internal readonly SafePipeHandle ReadSafe, WriteSafe;
            internal readonly string Binding;
            internal Stratus.RuntimeBuild.TerminalRecord Record;
            internal byte[] PreparedFrame, CommitFrame;
            internal int PreparedObserved, CommitObserved, InputEofObserved;
            internal int AckWriteAttempted, AckWritten;
            internal bool AckWriteReturned;
            internal int HalfCloseAttempted, HalfCloseReturned;
            internal bool HalfCloseSucceeded;
            internal int HalfCloseError;
            internal bool WriteWrapperInvalidationAttempted, WriteWrapperInvalidationReturned;
            internal int ReaderSettled, WriterSettled;
            internal Thread StdoutWriter;
            internal string ReadyNonce;

            internal GuardianTerminalObservation(string originalBinding) {
                Require(Object.ReferenceEquals(Thread.CurrentThread, originalMainThread) &&
                    jobTransfer != null && rootTransfer != null && contextCapture != null &&
                    controlReader != null && controlWriter != null && controlInput != null && controlOutput != null &&
                    originalBinding != null && terminalObservation == null);
                Job = jobTransfer; Root = rootTransfer; Producer = producer;
                ProducerId = producerPid; ProducerBirth = producerBirth;
                Context = contextCapture.AdmittedMetadata; Binding = originalBinding;
                ReadHandle = controlReadHandle; WriteHandle = controlWriteHandle;
                Reader = controlReader; Writer = controlWriter; Input = controlInput; Output = controlOutput;
                ReadSafe = controlReadSafe; WriteSafe = controlWriteSafe;
            }
        }
        private sealed class NormalProducerExitObservation {
            internal readonly GuardianTerminalObservation Terminal;
            internal bool Attempted, Proven;
            internal bool PidAttempted, PidReturned;
            internal uint Pid;
            internal int PidError;
            internal bool BeforeWaitAttempted, BeforeWaitReturned, AfterWaitAttempted, AfterWaitReturned;
            internal uint BeforeWait, AfterWait;
            internal int BeforeWaitError, AfterWaitError;
            internal bool TimesAttempted, TimesReturned, TimesSucceeded;
            internal long Created, Exited, Kernel, User;
            internal int TimesError;
            internal bool ExitAttempted, ExitReturned, ExitSucceeded;
            internal uint ExitCode;
            internal int ExitError;
            internal NormalProducerExitObservation(GuardianTerminalObservation originalTerminal) {
                Require(originalTerminal != null && Object.ReferenceEquals(terminalObservation, originalTerminal));
                Terminal = originalTerminal;
            }
        }
        [DllImport("kernel32.dll")] private static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll")] private static extern uint GetCurrentProcessId();
        [DllImport("kernel32.dll")] private static extern ulong GetTickCount64();
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint GetFileType(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern IntPtr GetStdHandle(int kind);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool member);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint GetProcessId(IntPtr process);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetProcessTimes(IntPtr process, out long created, out long exited, out long kernel, out long user);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetExitCodeProcess(IntPtr process, out uint exitCode);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint WaitForSingleObject(IntPtr process, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool TerminateProcess(IntPtr process, uint exitCode);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetHandleInformation(IntPtr handle, out uint flags);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool SetHandleInformation(IntPtr handle, uint mask, uint flags);
        [DllImport("Kernelbase.dll", SetLastError = true)] private static extern bool CompareObjectHandles(IntPtr first, IntPtr second);

        private static void Require(bool condition) {
            if (!condition) throw new InvalidOperationException("Unqualified owner-watch admission/observation failed.");
        }
        private static ulong Decimal(string value) {
            ulong number;
            Require(value != null && value.Length > 0 && value.Length <= 20 &&
                UInt64.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out number));
            // Keep the out assignment definite on all supported C# compiler versions.
            number = UInt64.Parse(value, NumberStyles.None, CultureInfo.InvariantCulture);
            Require(number > 0 && number.ToString(CultureInfo.InvariantCulture) == value);
            return number;
        }
        private static void AssertIndependent() {
            bool member;
            Require(IsProcessInJob(GetCurrentProcess(), IntPtr.Zero, out member) && !member);
        }
        private static void AssertStartupDeadline() {
            // Same Windows boot clock as the original retained launcher. No
            // UTC conversion or fresh timeout at W entry/READY/retry is allowed.
            ulong now = GetTickCount64();
            Require(startedTick > 0 && startedTick <= UInt64.MaxValue - StartupMilliseconds &&
                deadlineTick == checked(startedTick + StartupMilliseconds));
            Require(now >= startedTick && now >= previousTick && now < deadlineTick);
            previousTick = now;
        }
        private static bool ObserveProducer() {
            Require(producer != IntPtr.Zero && producer != new IntPtr(-1) && producerPid > 0 &&
                producerPid != GetCurrentProcessId() && GetProcessId(producer) == producerPid);
            uint before = WaitForSingleObject(producer, 0);
            Require(before == WaitObject || before == WaitTimeout);
            long created, exited, kernel, user;
            Require(GetProcessTimes(producer, out created, out exited, out kernel, out user));
            Require(created == producerBirth && created > 0);
            if (before == WaitObject) Require(exited > 0 && exited >= created);
            uint after = WaitForSingleObject(producer, 0);
            Require(after == WaitObject || after == WaitTimeout);
            if (before == WaitObject) Require(after == WaitObject);
            // A death between queries is not yet a physical-death proof. Re-read
            // native times on the next observation, without reopening by PID.
            return before == WaitObject && after == WaitObject;
        }
        private static void DemandQualifiedNormalTerminalObservation() {
            // A new deny-all gate, independent of transfer/context admission.
            // Physical closure will not by itself qualify a build or output.
            throw new InvalidOperationException("W normal terminal observation is not independently qualified.");
        }
        private static void AssertNormalTerminalProofAdmission(GuardianJobTransfer originalJobOwner,
                GuardianRootTransfer originalRootOwner) {
            GuardianTerminalObservation observation = terminalObservation;
            Require(Object.ReferenceEquals(Thread.CurrentThread, originalMainThread) && identityEstablished &&
                originalJobOwner != null && originalRootOwner != null && observation != null &&
                Object.ReferenceEquals(jobTransfer, originalJobOwner) && Object.ReferenceEquals(rootTransfer, originalRootOwner) &&
                Object.ReferenceEquals(observation.Job, originalJobOwner) && Object.ReferenceEquals(observation.Root, originalRootOwner) &&
                observation.Producer == producer && observation.ProducerId == producerPid && observation.ProducerBirth == producerBirth &&
                contextCapture != null && Object.ReferenceEquals(observation.Context, contextCapture.AdmittedMetadata) &&
                observation.ReadHandle == controlReadHandle && observation.WriteHandle == controlWriteHandle &&
                Object.ReferenceEquals(observation.Reader, controlReader) && Object.ReferenceEquals(observation.Writer, controlWriter) &&
                Object.ReferenceEquals(observation.Input, controlInput) && Object.ReferenceEquals(observation.Output, controlOutput) &&
                Object.ReferenceEquals(observation.ReadSafe, controlReadSafe) && Object.ReferenceEquals(observation.WriteSafe, controlWriteSafe));
            Require(Interlocked.CompareExchange(ref stopAttempted, 0, 0) == 0 &&
                Interlocked.CompareExchange(ref controlFailed, 0, 0) == 0 &&
                Interlocked.CompareExchange(ref writerFailed, 0, 0) == 0 &&
                Interlocked.CompareExchange(ref normalTerminalFailed, 0, 0) == 0);
            Require(originalJobOwner.IsWorkerSettledForNormal && originalRootOwner.IsWorkerSettledForNormal &&
                Interlocked.CompareExchange(ref rootAckWritten, 0, 0) == 1 && rootAckWriteReturned &&
                Interlocked.CompareExchange(ref observation.PreparedObserved, 0, 0) == 1 && observation.PreparedFrame != null &&
                Interlocked.CompareExchange(ref observation.CommitObserved, 0, 0) == 1 && observation.CommitFrame != null &&
                Interlocked.CompareExchange(ref observation.InputEofObserved, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref observation.AckWriteAttempted, 0, 0) == 1 && observation.AckWriteReturned &&
                Interlocked.CompareExchange(ref observation.AckWritten, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref observation.HalfCloseAttempted, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref observation.HalfCloseReturned, 0, 0) == 1 && observation.HalfCloseSucceeded &&
                observation.WriteWrapperInvalidationAttempted && observation.WriteWrapperInvalidationReturned &&
                Interlocked.CompareExchange(ref observation.ReaderSettled, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref observation.WriterSettled, 0, 0) == 1 &&
                observation.Record != null && controlReaderStartAttempted && controlReaderStartReturned &&
                controlWriterStartAttempted && controlWriterStartReturned && controlReader.Join(0) && controlWriter.Join(0) &&
                contextCapture.IsWorkerSettledForTerminal);
            // Actual owner-bound readiness still needs independent qualification.
            DemandQualifiedNormalTerminalObservation();
            AssertStartupDeadline(); contextCapture.AssertHealthy(); AssertIndependent(); AssertStartupDeadline();
        }
        private static void RequireOriginalProducerNormalExit() {
            AssertNormalTerminalProofAdmission(jobTransfer, rootTransfer);
            RequireOriginalProducerNormalExitForArmedDrain();
        }
        private static void RequireOriginalProducerNormalExitForArmedDrain() {
            // P may exit before W's pending COMMIT/EOF read is scheduled. Only
            // the actual irreversible ACK/half-close phase admits that drain.
            AssertNormalTerminalDrainAdmission();
            DemandQualifiedNormalTerminalObservation();
            AssertStartupDeadline();
            if (normalProducerExit != null) {
                // Known physical death on an unchanged retained handle is stable.
                // A failed/unknown first attempt is never retried or inferred dead.
                Require(Object.ReferenceEquals(normalProducerExit.Terminal, terminalObservation) &&
                    normalProducerExit.Attempted && normalProducerExit.Proven);
                return;
            }
            NormalProducerExitObservation observation = new NormalProducerExitObservation(terminalObservation);
            normalProducerExit = observation; observation.Attempted = true;
            try {
                Require(producer != IntPtr.Zero && producer != new IntPtr(-1) && producerPid > 0 && producerBirth > 0 &&
                    producerPid != GetCurrentProcessId());
                AssertStartupDeadline();
                observation.PidAttempted = true; observation.Pid = GetProcessId(producer);
                observation.PidError = Marshal.GetLastWin32Error(); observation.PidReturned = true;
                Require(observation.Pid == producerPid); AssertStartupDeadline();
                observation.BeforeWaitAttempted = true; observation.BeforeWait = WaitForSingleObject(producer, 0);
                observation.BeforeWaitError = Marshal.GetLastWin32Error(); observation.BeforeWaitReturned = true;
                Require(observation.BeforeWait == WaitObject); AssertStartupDeadline();
                observation.TimesAttempted = true;
                observation.TimesSucceeded = GetProcessTimes(producer, out observation.Created, out observation.Exited,
                    out observation.Kernel, out observation.User);
                observation.TimesError = Marshal.GetLastWin32Error(); observation.TimesReturned = true;
                Require(observation.TimesSucceeded && observation.Created == producerBirth && observation.Created > 0 &&
                    observation.Exited > 0 && observation.Exited >= observation.Created); AssertStartupDeadline();
                observation.ExitAttempted = true; observation.ExitSucceeded = GetExitCodeProcess(producer, out observation.ExitCode);
                observation.ExitError = Marshal.GetLastWin32Error(); observation.ExitReturned = true;
                // Exit zero means physical normal closure only, never build acceptance.
                Require(observation.ExitSucceeded && observation.ExitCode == 0); AssertStartupDeadline();
                observation.AfterWaitAttempted = true; observation.AfterWait = WaitForSingleObject(producer, 0);
                observation.AfterWaitError = Marshal.GetLastWin32Error(); observation.AfterWaitReturned = true;
                Require(observation.AfterWait == WaitObject); AssertStartupDeadline();
                observation.Proven = true;
            } catch (Exception) { Interlocked.Exchange(ref normalTerminalFailed, 1); throw; }
        }
        private static bool StopOriginalProducer() {
            // STOP is irreversible: close new control admission before any
            // identity probe or native stop operation can block or fail.
            // The one clock rejects late completion; it cannot preempt a stalled
            // native identity/termination/query call on this main thread.
            Interlocked.Exchange(ref controlFailed, 1);
            if (jobTransfer != null) jobTransfer.CloseAdmission();
            if (rootTransfer != null) rootTransfer.CloseAdmission();
            if (Interlocked.CompareExchange(ref stopAttempted, 1, 0) != 0) return false;
            stopClock = Stopwatch.StartNew();
            if (!identityEstablished) return false;
            try {
                bool dead = ObserveProducer();
                if (!dead) {
                    // Only the inherited original P stop capability is used.
                    // DUP-only producerTransfer is never queried or waited upon.
                    producerStopAttempted = true;
                    producerStopSucceeded = TerminateProcess(producer, 203);
                    producerStopError = Marshal.GetLastWin32Error(); producerStopReturned = true;
                    if (!producerStopSucceeded && !ObserveProducer()) return false;
                }
                while (!dead && stopClock.ElapsedMilliseconds < StopMilliseconds) {
                    dead = ObserveProducer();
                    if (dead) break;
                    uint wait = WaitForSingleObject(producer, 50);
                    Require(wait == WaitObject || wait == WaitTimeout);
                }
                if (!dead || stopClock.ElapsedMilliseconds >= StopMilliseconds) return false;
                // Physical P death prevents future births. BOTH claimed workers
                // must publish their last native use before ANY Job mutation;
                // the root worker may still be using the exact held Job.
                if (jobTransfer != null && !jobTransfer.SettleAfterProducerDeath(stopClock)) return false;
                if (rootTransfer != null && !rootTransfer.SettleAfterProducerDeath(stopClock)) return false;
                if (jobTransfer != null && !jobTransfer.StopAfterProducerDeath(stopClock)) return false;
                if (rootTransfer != null && !rootTransfer.ObservePhysicalDeathAfterJobStop(stopClock)) return false;
                return stopClock.ElapsedMilliseconds < StopMilliseconds;
            } catch (Exception) { return false; }
        }
        private static IntPtr ControlEndpoint(string value) {
            ulong raw = Decimal(value);
            Require(IntPtr.Size == 8 ? raw <= Int64.MaxValue : raw < UInt32.MaxValue);
            IntPtr valueHandle = IntPtr.Size == 8 ? new IntPtr((long)raw) : new IntPtr(unchecked((int)(uint)raw));
            Require(Stratus.RuntimeBuild.CompilerJobWire.HandleText(valueHandle) == value);
            return valueHandle;
        }
        private static void AdoptProducerTransfer(string raw, string read, string write) {
            producerTransfer = ControlEndpoint(raw);
            Require(producerTransfer != producer && producerTransfer != ControlEndpoint(read) && producerTransfer != ControlEndpoint(write));
            Require(producerTransfer != GetStdHandle(-10) && producerTransfer != GetStdHandle(-11) && producerTransfer != GetStdHandle(-12));
            uint flags;
            Require(GetHandleInformation(producerTransfer, out flags) && (flags & 1) == 1 && (flags & 2) == 0);
            Require(SetHandleInformation(producerTransfer, 1, 0));
            Require(GetHandleInformation(producerTransfer, out flags) && (flags & 3) == 0);
            // Compare kernel objects without requesting process query/sync rights
            // on the separate 0x40 capability. No fallback API or PID reopening.
            transferCompareAttempted = true;
            transferCompareSucceeded = CompareObjectHandles(producer, producerTransfer);
            transferCompareError = Marshal.GetLastWin32Error(); transferCompareReturned = true;
            Require(transferCompareSucceeded);
            Require(!ObserveProducer());
        }
        private static string ControlFields(string nonce) {
            Require(contextCapture != null && contextCapture.IsComplete);
            return nonce + " " + producerPid.ToString(CultureInfo.InvariantCulture) + " " +
                producerBirth.ToString(CultureInfo.InvariantCulture) + " " +
                startedTick.ToString(CultureInfo.InvariantCulture) + " " + deadlineTick.ToString(CultureInfo.InvariantCulture) + " " +
                contextCapture.Length.ToString(CultureInfo.InvariantCulture) + " " + contextCapture.Sha256 + "\n";
        }
        private static void AssertControlWorker(ref ulong lastTick) {
            // Worker-local clock state only. Never lock or mutate main's previousTick;
            // a blocked native/pipe operation on this thread cannot hold W main.
            Require(Interlocked.CompareExchange(ref controlFailed, 0, 0) == 0 &&
                Interlocked.CompareExchange(ref writerFailed, 0, 0) == 0);
            contextCapture.AssertHealthy(); Require(contextCapture.IsComplete);
            ulong now = GetTickCount64();
            Require(startedTick > 0 && startedTick <= UInt64.MaxValue - StartupMilliseconds &&
                deadlineTick == checked(startedTick + StartupMilliseconds));
            Require(now >= startedTick && now >= lastTick && now < deadlineTick);
            lastTick = now;
            AssertIndependent(); Require(!ObserveProducer());
            contextCapture.AssertHealthy();
            Require(Interlocked.CompareExchange(ref controlFailed, 0, 0) == 0 &&
                Interlocked.CompareExchange(ref writerFailed, 0, 0) == 0);
            // Identity/native checks may consume the remaining original budget.
            ulong after = GetTickCount64();
            Require(after >= lastTick && after < deadlineTick); lastTick = after;
        }
        private static void AdoptControl(string read, string write, string nonce) {
            Require(nonce != null && nonce.Length == 64);
            foreach (char c in nonce) Require((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'));
            controlReadHandle = ControlEndpoint(read); controlWriteHandle = ControlEndpoint(write);
            Require(controlReadHandle != controlWriteHandle && controlReadHandle != producer && controlWriteHandle != producer &&
                controlReadHandle != producerTransfer && controlWriteHandle != producerTransfer);
            foreach (IntPtr endpoint in new IntPtr[] { controlReadHandle, controlWriteHandle }) {
                Require(endpoint != GetStdHandle(-10) && endpoint != GetStdHandle(-11) && endpoint != GetStdHandle(-12));
                Require(GetFileType(endpoint) == 3);
                uint flags;
                Require(GetHandleInformation(endpoint, out flags) && (flags & 1) == 1 && (flags & 2) == 0);
                Require(SetHandleInformation(endpoint, 1, 0));
                Require(GetHandleInformation(endpoint, out flags) && (flags & 3) == 0);
            }
            // The disposable process owns the inherited handles until kernel
            // exit. No independent SafeHandle finalizer may close/reuse them.
            controlReadSafe = new SafePipeHandle(controlReadHandle, false);
            controlWriteSafe = new SafePipeHandle(controlWriteHandle, false);
            controlInput = new AnonymousPipeClientStream(PipeDirection.In, controlReadSafe);
            controlOutput = new AnonymousPipeClientStream(PipeDirection.Out, controlWriteSafe);
            // HELLO <= 237 bytes; ACK <= 245 bytes, within the shared 256 cap.
            byte[] expected = Encoding.ASCII.GetBytes("STRATUS_PRODUCER_HELLO_V6 " + ControlFields(nonce));
            byte[] hello = Encoding.ASCII.GetBytes("STRATUS_GUARDIAN_HELLO_V6 " + ControlFields(nonce));
            byte[] acknowledgement = Encoding.ASCII.GetBytes("STRATUS_GUARDIAN_ADMISSION_ACK_V6 " + ControlFields(nonce));
            Require(expected.Length <= 256 && hello.Length <= 256 && acknowledgement.Length <= 256);
            // This publication is main-only, before either reader/writer starts.
            // STOP also runs only on main, so no owner can be published after it.
            Require(jobTransfer == null && rootTransfer == null && Interlocked.CompareExchange(ref stopAttempted, 0, 0) == 0 &&
                Interlocked.CompareExchange(ref controlFailed, 0, 0) == 0);
            string binding = Stratus.RuntimeBuild.CompilerJobWire.Binding(
                nonce, producerPid, producerBirth, startedTick, deadlineTick, contextCapture.Length, contextCapture.Sha256);
            jobTransfer = new GuardianJobTransfer(producerTransfer, binding);
            rootTransfer = new GuardianRootTransfer(producerTransfer, jobTransfer, contextCapture.AdmittedMetadata, binding);
            controlReader = new Thread(delegate() {
                try {
                    int storageMaximum = Math.Max(Stratus.RuntimeBuild.TerminalWire.MaximumPreparedBytes,
                        Math.Max(Stratus.RuntimeBuild.TerminalWire.MaximumCommitBytes, Math.Max(256,
                        Math.Max(Stratus.RuntimeBuild.CompilerJobWire.MaximumBytes, Stratus.RuntimeBuild.CompilerRootWire.MaximumBytes))));
                    byte[] buffer = new byte[storageMaximum];
                    byte[] frame = new byte[storageMaximum];
                    int offset = 0, stage = 0, count;
                    ulong workerTick = startedTick;
                    while (true) {
                        if (stage < 3) AssertControlWorker(ref workerTick); else AssertTerminalControlWorker(ref workerTick);
                        count = controlInput.Read(buffer, 0, buffer.Length);
                        // Snapshot arrival stage before processing any frame in
                        // this read. Coalesced early surplus cannot gain admission
                        // from work caused by an earlier frame in the same read.
                        int admissionAttemptAtRead = Interlocked.CompareExchange(ref controlAckWriteAttempted, 0, 0);
                        int jobAckAttemptAtRead = Interlocked.CompareExchange(ref jobAckWriteAttempted, 0, 0);
                        int rootAckAttemptAtRead = Interlocked.CompareExchange(ref rootAckWriteAttempted, 0, 0);
                        int terminalAckAttemptAtRead = Interlocked.CompareExchange(ref terminalObservation.AckWriteAttempted, 0, 0);
                        int terminalCloseAttemptAtRead = Interlocked.CompareExchange(ref terminalObservation.HalfCloseAttempted, 0, 0);
                        int preparedObservedAtRead = Interlocked.CompareExchange(ref terminalObservation.PreparedObserved, 0, 0);
                        Stratus.RuntimeBuild.TerminalRecord preparedAtRead = preparedObservedAtRead == 1 ? terminalObservation.Record : null;
                        if (stage < 3) AssertControlWorker(ref workerTick); else AssertTerminalControlWorker(ref workerTick);
                        if (count == 0) {
                            Require(stage == 5 && offset == 0 && terminalAckAttemptAtRead == 1 && terminalCloseAttemptAtRead == 1 &&
                                preparedAtRead != null && Object.ReferenceEquals(preparedAtRead, terminalObservation.Record) &&
                                Interlocked.CompareExchange(ref terminalObservation.CommitObserved, 0, 0) == 1);
                            Interlocked.Exchange(ref terminalObservation.InputEofObserved, 1);
                            return;
                        }
                        for (int i = 0; i < count; ++i) {
                            int maximum = stage == 0 ? 256 : (stage == 1 ? Stratus.RuntimeBuild.CompilerJobWire.MaximumBytes :
                                (stage == 2 ? Stratus.RuntimeBuild.CompilerRootWire.MaximumBytes :
                                (stage == 3 ? Stratus.RuntimeBuild.TerminalWire.MaximumPreparedBytes : Stratus.RuntimeBuild.TerminalWire.MaximumCommitBytes)));
                            Require(stage < 5 && offset < maximum && (buffer[i] == 10 || (buffer[i] >= 32 && buffer[i] <= 126)));
                            if (stage == 1) Require(admissionAttemptAtRead == 1);
                            if (stage == 2) Require(jobAckAttemptAtRead == 1);
                            if (stage == 3) Require(rootAckAttemptAtRead == 1);
                            if (stage == 4) Require(terminalAckAttemptAtRead == 1 && terminalCloseAttemptAtRead == 1 &&
                                preparedAtRead != null && Object.ReferenceEquals(preparedAtRead, terminalObservation.Record));
                            frame[offset++] = buffer[i];
                            if (buffer[i] != 10) continue;
                            byte[] actual = new byte[offset]; Buffer.BlockCopy(frame, 0, actual, 0, offset);
                            if (stage == 0) {
                                Stratus.RuntimeBuild.CompilerJobWire.RequireExact(actual, expected);
                                AssertControlWorker(ref workerTick);
                                Interlocked.Exchange(ref controlHelloObserved, 1);
                            } else if (stage == 1) {
                                // Actual offer bytes, not a caller ticket. The
                                // claimed worker waits for ACK write publication.
                                Require(Interlocked.CompareExchange(ref controlAckPermit, 0, 0) == 1 &&
                                    Interlocked.CompareExchange(ref controlHelloWritten, 0, 0) == 1);
                                AssertControlWorker(ref workerTick);
                                jobTransfer.ClaimOffer(actual);
                            } else if (stage == 2) {
                                // Claim actual third-frame bytes only after the
                                // verified Job ACK's real write attempt. Waiting
                                // for its post-Flush flag belongs to root worker.
                                Require(jobAckAttemptAtRead == 1);
                                AssertControlWorker(ref workerTick);
                                rootTransfer.ClaimOffer(actual);
                            } else if (stage == 3) {
                                ReceiveNormalTerminalPrepared(actual, rootAckAttemptAtRead, ref workerTick);
                            } else {
                                ReceiveNormalTerminalCommit(actual, preparedAtRead, terminalAckAttemptAtRead,
                                    terminalCloseAttemptAtRead, ref workerTick);
                            }
                            ++stage; offset = 0;
                        }
                    }
                } catch (Exception) { Interlocked.Exchange(ref controlFailed, 1); }
                finally { Interlocked.Exchange(ref terminalObservation.ReaderSettled, 1); }
            });
            controlWriter = new Thread(delegate() {
                try {
                    ulong workerTick = startedTick;
                    AssertControlWorker(ref workerTick);
                    controlOutput.Write(hello, 0, hello.Length); controlOutput.Flush();
                    AssertControlWorker(ref workerTick);
                    Interlocked.Exchange(ref controlHelloWritten, 1);
                    // One retained writer owns all five messages; no concurrent
                    // pipe writes or main-loop IO/Join can interleave them.
                    while (Interlocked.CompareExchange(ref controlAckPermit, 0, 0) == 0) {
                        AssertControlWorker(ref workerTick); Thread.Sleep(10);
                    }
                    AssertControlWorker(ref workerTick);
                    Require(Interlocked.CompareExchange(ref controlAckWriteAttempted, 1, 0) == 0);
                    controlOutput.Write(acknowledgement, 0, acknowledgement.Length); controlOutput.Flush();
                    AssertControlWorker(ref workerTick);
                    Interlocked.Exchange(ref controlAckWritten, 1);
                    while ((jobAcknowledgement = jobTransfer.CopyAcknowledgementIfSettled()) == null) {
                        AssertControlWorker(ref workerTick); Thread.Sleep(10);
                    }
                    AssertControlWorker(ref workerTick);
                    Require(Interlocked.CompareExchange(ref jobAckWriteAttempted, 1, 0) == 0);
                    controlOutput.Write(jobAcknowledgement, 0, jobAcknowledgement.Length); controlOutput.Flush();
                    jobAckWriteReturned = true;
                    AssertControlWorker(ref workerTick);
                    Interlocked.Exchange(ref jobAckWritten, 1);
                    while ((rootAcknowledgement = rootTransfer.CopyAcknowledgementIfSettled()) == null) {
                        AssertControlWorker(ref workerTick); Thread.Sleep(10);
                    }
                    AssertControlWorker(ref workerTick);
                    Require(Interlocked.CompareExchange(ref rootAckWriteAttempted, 1, 0) == 0);
                    controlOutput.Write(rootAcknowledgement, 0, rootAcknowledgement.Length); controlOutput.Flush();
                    rootAckWriteReturned = true;
                    AssertControlWorker(ref workerTick);
                    Interlocked.Exchange(ref rootAckWritten, 1);
                    SendNormalTerminalAcknowledgement(ref workerTick);
                    // STOP is not atomic with an already-entered pipe write. P's
                    // physical death precedes final Job cleanup; late bytes never
                    // substitute for that proof or authorize a success terminal.
                }
                catch (Exception) { Interlocked.Exchange(ref controlFailed, 1); }
                finally { Interlocked.Exchange(ref terminalObservation.WriterSettled, 1); }
            });
            terminalObservation = new GuardianTerminalObservation(binding);
            controlReader.IsBackground = true; controlWriter.IsBackground = true;
            controlReaderStartAttempted = true; controlReader.Start(); controlReaderStartReturned = true;
            controlWriterStartAttempted = true; controlWriter.Start(); controlWriterStartReturned = true;
        }
        private static void StartReadyWriter(string nonce, string controlNonce) {
            Require(contextCapture != null && contextCapture.IsComplete);
            byte[] frame = Encoding.ASCII.GetBytes("STRATUS_OWNER_READY_V8 " + nonce + " " +
                producerPid.ToString(CultureInfo.InvariantCulture) + " " +
                producerBirth.ToString(CultureInfo.InvariantCulture) + " " +
                startedTick.ToString(CultureInfo.InvariantCulture) + " " +
                deadlineTick.ToString(CultureInfo.InvariantCulture) + " " + controlNonce + " " +
                contextCapture.Length.ToString(CultureInfo.InvariantCulture) + " " + contextCapture.Sha256 + " " +
                Stratus.RuntimeBuild.CompilerJobWire.HandleText(producerTransfer) + "\n");
            // Exact maximum 320 bytes, under the versioned 352-byte READY cap.
            Require(frame.Length <= 352);
            // Publication never runs on, or blocks, the owner-watch/STOP loop.
            // The same retained writer serializes READY and the eventual terminal.
            // A blocked/unfinished write cannot authorize normal process exit.
            Require(readyWriter == null && originalReadyFrame == null && originalReadyNonce == null);
            originalReadyFrame = (byte[])frame.Clone(); originalReadyNonce = nonce;
            readyWriter = new Thread(delegate() {
                try {
                    ulong workerTick = startedTick;
                    AssertTerminalControlWorker(ref workerTick);
                    readyStream = Console.OpenStandardOutput();
                    Require(Interlocked.CompareExchange(ref readyWriteAttempted, 1, 0) == 0);
                    readyStream.Write(frame, 0, frame.Length);
                    readyStream.Flush();
                    readyWriteReturned = true;
                    AssertTerminalControlWorker(ref workerTick);
                    Interlocked.Exchange(ref readyWritten, 1);
                    WriteNormalTerminalStdout(ref workerTick);
                } catch (Exception) { Interlocked.Exchange(ref writerFailed, 1); }
                finally { Interlocked.Exchange(ref readyWriterSettled, 1); }
            });
            Require(terminalObservation != null && terminalObservation.StdoutWriter == null && terminalObservation.ReadyNonce == null);
            terminalObservation.StdoutWriter = readyWriter; terminalObservation.ReadyNonce = nonce;
            readyWriter.IsBackground = true;
            readyWriterStartAttempted = true; readyWriter.Start(); readyWriterStartReturned = true;
        }
        private static int Main(string[] args) {
            try {
                Require(originalMainThread == null); originalMainThread = Thread.CurrentThread;
                Require(args != null && args.Length == 14 && args[0] == "--stratus-owner-watch-v8");
                ulong raw = Decimal(args[1]), pid = Decimal(args[2]), birth = Decimal(args[3]);
                Require((IntPtr.Size == 8 ? raw <= Int64.MaxValue : raw < UInt32.MaxValue) && pid <= Int32.MaxValue && birth <= Int64.MaxValue);
                Require(args[4] != null && args[4].Length == 64);
                foreach (char c in args[4]) Require((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'));
                producer = ControlEndpoint(args[1]);
                producerPid = (uint)pid; producerBirth = (long)birth;
                uint flags;
                Require(GetHandleInformation(producer, out flags) && (flags & 1) == 1 && (flags & 2) == 0);
                Require(SetHandleInformation(producer, 1, 0));
                Require(GetHandleInformation(producer, out flags) && (flags & 3) == 0);
                // Identity is established before the independent-state assertion,
                // so a failed no-Job check can still stop only the exact P.
                bool dead = ObserveProducer(); identityEstablished = true;
                AssertIndependent();
                Require(!dead); // Even an already-dead owner takes common STOP.
                // Establish original-P identity BEFORE parsing/rejecting the
                // deadline/context: late or malformed input must STOP known P.
                startedTick = Decimal(args[5]); deadlineTick = Decimal(args[6]);
                AssertStartupDeadline();
                AdoptProducerTransfer(args[13], args[7], args[8]);
                AssertStartupDeadline();
                contextSection = ControlEndpoint(args[10]);
                contextCapture = Stratus.RuntimeBuild.ContextCapture.Prepare(args[10], args[11], args[12],
                    new IntPtr[] { producer, ControlEndpoint(args[7]), ControlEndpoint(args[8]), producerTransfer });
                contextCapture.Start();
                AssertStartupDeadline();
                while (true) {
                    AssertStartupDeadline();
                    AssertIndependent();
                    bool ownerExited = ObserveProducer();
                    AssertStartupDeadline();
                    if (ownerExited) {
                        // Expected physical exit may race pending COMMIT/EOF or
                        // the writer's post-Close publication. No fresh deadline.
                        if (!MayDrainNormalTerminal()) break;
                        RequireOriginalProducerNormalExitForArmedDrain();
                        if (TryCompleteNormalTerminal()) return 0;
                        Thread.Sleep(10);
                        continue;
                    }
                    contextCapture.AssertHealthy();
                    if (!protocolStartAttempted && contextCapture.IsComplete) {
                        // Map/copy/hash/unmap run only on the retained background
                        // worker. Main continues exact-P watch/STOP while pending.
                        AssertStartupDeadline(); AssertIndependent();
                        if (ObserveProducer()) break;
                        AssertStartupDeadline(); contextCapture.AssertHealthy();
                        protocolStartAttempted = true;
                        AdoptControl(args[7], args[8], args[9]);
                        AssertStartupDeadline(); AssertIndependent();
                        if (ObserveProducer()) break;
                        AssertStartupDeadline(); contextCapture.AssertHealthy();
                        StartReadyWriter(args[4], args[9]);
                        AssertStartupDeadline();
                    }
                    if (Interlocked.CompareExchange(ref writerFailed, 0, 0) != 0 || Interlocked.CompareExchange(ref controlFailed, 0, 0) != 0) break;
                    if (!controlAckAttempted && Interlocked.CompareExchange(ref controlHelloObserved, 0, 0) == 1 &&
                            Interlocked.CompareExchange(ref controlHelloWritten, 0, 0) == 1) {
                        AssertStartupDeadline(); AssertIndependent();
                        if (ObserveProducer()) break;
                        AssertStartupDeadline(); contextCapture.AssertHealthy();
                        Require(contextCapture.IsComplete && Interlocked.CompareExchange(ref controlFailed, 0, 0) == 0 &&
                            Interlocked.CompareExchange(ref writerFailed, 0, 0) == 0);
                        controlAckAttempted = true;
                        Interlocked.Exchange(ref controlAckPermit, 1);
                        AssertStartupDeadline();
                    }
                    uint wait = WaitForSingleObject(producer, 50);
                    Require(wait != WaitFailed && (wait == WaitObject || wait == WaitTimeout));
                    AssertStartupDeadline();
                }
                return StopOriginalProducer() ? 204 : 205;
            } catch (Exception) {
                return StopOriginalProducer() ? 204 : 205;
            }
            // Kernel closes this process's inherited copy and stdio on physical
            // exit. No uncertain CloseHandle retry or claimed cleanup proof here.
        }
    }
}
