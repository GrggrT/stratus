// UNQUALIFIED W terminal ownership. The original reader, private writer, main
// and stdout writer own this chain; correspondence is not artifact acceptance.
using System;
using System.Runtime.InteropServices;
using System.Threading;

namespace Stratus.RuntimeBuild.GuardianEntry {
    internal static partial class Program {
        private const int MaximumGuardianStdoutBytes = 4096;
        private static byte[] originalReadyFrame, terminalStdoutFrame;
        private static string originalReadyNonce;
        private static int readyWriteAttempted, readyWritten, readyWriterSettled;
        private static bool readyWriteReturned;
        private static int normalProofCompleted, terminalOutputPermit, terminalOutputWriteAttempted, terminalOutputWritten;
        private static bool terminalOutputWriteReturned;
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool CloseHandle(IntPtr handle);

        private static void AssertTerminalOwners() {
            GuardianTerminalObservation observation = terminalObservation;
            Require(observation != null && jobTransfer != null && rootTransfer != null && contextCapture != null &&
                Object.ReferenceEquals(observation.Job, jobTransfer) && Object.ReferenceEquals(observation.Root, rootTransfer) &&
                observation.Producer == producer && observation.ProducerId == producerPid && observation.ProducerBirth == producerBirth &&
                Object.ReferenceEquals(observation.Context, contextCapture.AdmittedMetadata) &&
                observation.ReadHandle == controlReadHandle && observation.WriteHandle == controlWriteHandle &&
                Object.ReferenceEquals(observation.Reader, controlReader) && Object.ReferenceEquals(observation.Writer, controlWriter) &&
                Object.ReferenceEquals(observation.Input, controlInput) && Object.ReferenceEquals(observation.Output, controlOutput) &&
                Object.ReferenceEquals(observation.ReadSafe, controlReadSafe) && Object.ReferenceEquals(observation.WriteSafe, controlWriteSafe));
            Require(Interlocked.CompareExchange(ref controlFailed, 0, 0) == 0 &&
                Interlocked.CompareExchange(ref writerFailed, 0, 0) == 0 &&
                Interlocked.CompareExchange(ref stopAttempted, 0, 0) == 0 &&
                Interlocked.CompareExchange(ref normalTerminalFailed, 0, 0) == 0);
        }
        private static void AssertTerminalControlWorker(ref ulong lastTick) {
            AssertTerminalOwners();
            Require(Object.ReferenceEquals(Thread.CurrentThread, controlReader) ||
                Object.ReferenceEquals(Thread.CurrentThread, controlWriter) || Object.ReferenceEquals(Thread.CurrentThread, readyWriter));
            contextCapture.AssertHealthy(); Require(contextCapture.IsComplete);
            ulong now = GetTickCount64();
            Require(startedTick > 0 && startedTick <= UInt64.MaxValue - StartupMilliseconds &&
                deadlineTick == checked(startedTick + StartupMilliseconds) &&
                now >= startedTick && now >= lastTick && now < deadlineTick);
            lastTick = now;
            AssertIndependent();
            // Identity is still checked, but physical P exit is not a terminal
            // read failure: Main alone admits the armed bounded drain/exit code.
            ObserveProducer();
            contextCapture.AssertHealthy(); AssertTerminalOwners();
            ulong after = GetTickCount64();
            Require(after >= lastTick && after < deadlineTick); lastTick = after;
        }
        private static void ReceiveNormalTerminalPrepared(byte[] actual, int rootAckAttemptAtRead, ref ulong workerTick) {
            Require(Object.ReferenceEquals(Thread.CurrentThread, controlReader) && rootAckAttemptAtRead == 1 &&
                Interlocked.CompareExchange(ref terminalObservation.PreparedObserved, 0, 0) == 0 && terminalObservation.Record == null);
            AssertTerminalControlWorker(ref workerTick);
            Stratus.RuntimeBuild.TerminalRecord record = Stratus.RuntimeBuild.TerminalWire.ParsePrepared(
                actual, terminalObservation.Binding, rootTransfer.CopyVerifiedOfferForTerminal());
            Require(record.InputCount == terminalObservation.Context.CopyInputs().Length);
            // Neither this reader nor Main waits for a writer's post-Flush flag.
            // Both verified phase-2 transfers quiesce while P is still alive.
            jobTransfer.CloseAdmissionForNormalTerminal(); rootTransfer.CloseAdmissionForNormalTerminal();
            Require(jobTransfer.IsSettledForNormal && rootTransfer.IsSettledForNormal);
            terminalObservation.PreparedFrame = (byte[])actual.Clone(); terminalObservation.Record = record;
            AssertTerminalControlWorker(ref workerTick);
            Interlocked.Exchange(ref terminalObservation.PreparedObserved, 1);
        }
        private static void ReceiveNormalTerminalCommit(byte[] actual, Stratus.RuntimeBuild.TerminalRecord preparedAtRead,
                int ackAttemptAtRead, int closeAttemptAtRead, ref ulong workerTick) {
            Require(Object.ReferenceEquals(Thread.CurrentThread, controlReader) && preparedAtRead != null &&
                Object.ReferenceEquals(preparedAtRead, terminalObservation.Record) && ackAttemptAtRead == 1 && closeAttemptAtRead == 1 &&
                Interlocked.CompareExchange(ref terminalObservation.PreparedObserved, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref terminalObservation.CommitObserved, 0, 0) == 0);
            AssertTerminalControlWorker(ref workerTick);
            Stratus.RuntimeBuild.TerminalWire.RequireExact(actual, Stratus.RuntimeBuild.TerminalWire.Commit(preparedAtRead));
            terminalObservation.CommitFrame = (byte[])actual.Clone();
            AssertTerminalControlWorker(ref workerTick);
            Interlocked.Exchange(ref terminalObservation.CommitObserved, 1);
        }
        private static void SendNormalTerminalAcknowledgement(ref ulong workerTick) {
            Require(Object.ReferenceEquals(Thread.CurrentThread, controlWriter));
            while (Interlocked.CompareExchange(ref terminalObservation.PreparedObserved, 0, 0) == 0) {
                AssertTerminalControlWorker(ref workerTick); Thread.Sleep(10);
            }
            AssertTerminalControlWorker(ref workerTick);
            Require(Interlocked.CompareExchange(ref rootAckWritten, 0, 0) == 1 && rootAckWriteReturned &&
                jobTransfer.IsSettledForNormal && rootTransfer.IsSettledForNormal && terminalObservation.Record != null);
            byte[] acknowledgement = Stratus.RuntimeBuild.TerminalWire.Acknowledgement(terminalObservation.Record);
            Require(acknowledgement.Length <= Stratus.RuntimeBuild.TerminalWire.MaximumAckBytes);
            DemandQualifiedNormalTerminalObservation();
            Require(Interlocked.CompareExchange(ref terminalObservation.AckWriteAttempted, 1, 0) == 0);
            controlOutput.Write(acknowledgement, 0, acknowledgement.Length); controlOutput.Flush();
            terminalObservation.AckWriteReturned = true;
            AssertTerminalControlWorker(ref workerTick);
            Interlocked.Exchange(ref terminalObservation.AckWritten, 1);
            // The stream's SafePipeHandle is non-owning. Dispose is not physical
            // half-close; the original writer performs exactly one native close.
            Require(Interlocked.CompareExchange(ref terminalObservation.HalfCloseAttempted, 1, 0) == 0);
            terminalObservation.HalfCloseSucceeded = CloseHandle(terminalObservation.WriteHandle);
            terminalObservation.HalfCloseError = Marshal.GetLastWin32Error();
            Interlocked.Exchange(ref terminalObservation.HalfCloseReturned, 1);
            Require(terminalObservation.HalfCloseSucceeded);
            terminalObservation.WriteWrapperInvalidationAttempted = true;
            terminalObservation.WriteSafe.SetHandleAsInvalid();
            terminalObservation.WriteWrapperInvalidationReturned = true;
            // P can already observe EOF, send COMMIT and exit before the above
            // return is published. Do not require P alive after the native call.
            AssertTerminalControlWorker(ref workerTick);
        }
        private static bool MayDrainNormalTerminal() {
            GuardianTerminalObservation observation = terminalObservation;
            return observation != null &&
                Interlocked.CompareExchange(ref observation.PreparedObserved, 0, 0) == 1 &&
                observation.Record != null &&
                Interlocked.CompareExchange(ref observation.AckWriteAttempted, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref observation.HalfCloseAttempted, 0, 0) == 1 &&
                (Interlocked.CompareExchange(ref observation.HalfCloseReturned, 0, 0) == 0 || observation.HalfCloseSucceeded) &&
                jobTransfer != null && rootTransfer != null && jobTransfer.IsSettledForNormal && rootTransfer.IsSettledForNormal;
        }
        private static void AssertNormalTerminalDrainAdmission() {
            Require(Object.ReferenceEquals(Thread.CurrentThread, originalMainThread) && identityEstablished && MayDrainNormalTerminal());
            AssertTerminalOwners();
            Require(Interlocked.CompareExchange(ref rootAckWritten, 0, 0) == 1 && rootAckWriteReturned &&
                Interlocked.CompareExchange(ref terminalObservation.AckWritten, 0, 0) == 1 && terminalObservation.AckWriteReturned);
        }
        private static bool NormalTerminalChannelSettled() {
            GuardianTerminalObservation observation = terminalObservation;
            return observation != null && Interlocked.CompareExchange(ref observation.CommitObserved, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref observation.InputEofObserved, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref observation.HalfCloseReturned, 0, 0) == 1 && observation.HalfCloseSucceeded &&
                observation.WriteWrapperInvalidationAttempted && observation.WriteWrapperInvalidationReturned &&
                Interlocked.CompareExchange(ref observation.ReaderSettled, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref observation.WriterSettled, 0, 0) == 1 &&
                controlReaderStartAttempted && controlReaderStartReturned && controlWriterStartAttempted && controlWriterStartReturned &&
                controlReader.Join(0) && controlWriter.Join(0) && jobTransfer.IsWorkerSettledForNormal && rootTransfer.IsWorkerSettledForNormal &&
                contextCapture.IsWorkerSettledForTerminal;
        }
        private static bool TryCompleteNormalTerminal() {
            AssertNormalTerminalDrainAdmission(); AssertStartupDeadline();
            if (!NormalTerminalChannelSettled()) return false;
            AssertNormalTerminalProofAdmission(jobTransfer, rootTransfer);
            if (Interlocked.CompareExchange(ref normalProofCompleted, 0, 0) == 0) {
                jobTransfer.ProveNormalClosureAfterProducerExit(rootTransfer);
                rootTransfer.ProveNormalClosureAfterProducerExit(jobTransfer);
                Require(jobTransfer.NormalClosureProven && rootTransfer.NormalClosureProven && normalProducerExit != null && normalProducerExit.Proven &&
                    Object.ReferenceEquals(terminalObservation.StdoutWriter, readyWriter) && terminalObservation.ReadyNonce == originalReadyNonce &&
                    originalReadyFrame != null && Interlocked.CompareExchange(ref readyWritten, 0, 0) == 1 && readyWriteReturned);
                terminalStdoutFrame = Stratus.RuntimeBuild.TerminalWire.GuardianTerminal(
                    terminalObservation.Record, originalReadyNonce, normalProducerExit.Exited);
                Require(terminalStdoutFrame.Length <= Stratus.RuntimeBuild.TerminalWire.MaximumGuardianBytes &&
                    checked(originalReadyFrame.Length + terminalStdoutFrame.Length) <= MaximumGuardianStdoutBytes);
                AssertNormalTerminalProofAdmission(jobTransfer, rootTransfer);
                Interlocked.Exchange(ref normalProofCompleted, 1);
                Interlocked.Exchange(ref terminalOutputPermit, 1);
            }
            if (Interlocked.CompareExchange(ref terminalOutputWritten, 0, 0) == 0 ||
                    Interlocked.CompareExchange(ref readyWriterSettled, 0, 0) == 0 || !readyWriterStartAttempted ||
                    !readyWriterStartReturned || !readyWriter.Join(0)) return false;
            AssertNormalTerminalProofAdmission(jobTransfer, rootTransfer);
            Require(terminalOutputWriteReturned && jobTransfer.NormalClosureProven && rootTransfer.NormalClosureProven);
            AssertStartupDeadline();
            return true; // Physical normal closure only; L still owns acceptance.
        }
        private static void WriteNormalTerminalStdout(ref ulong workerTick) {
            Require(Object.ReferenceEquals(Thread.CurrentThread, readyWriter));
            while (Interlocked.CompareExchange(ref terminalOutputPermit, 0, 0) == 0) {
                AssertTerminalControlWorker(ref workerTick); Thread.Sleep(10);
            }
            AssertTerminalControlWorker(ref workerTick);
            Require(Object.ReferenceEquals(terminalObservation.StdoutWriter, readyWriter) && terminalObservation.ReadyNonce == originalReadyNonce &&
                Interlocked.CompareExchange(ref normalProofCompleted, 0, 0) == 1 && jobTransfer.NormalClosureProven && rootTransfer.NormalClosureProven &&
                normalProducerExit != null && normalProducerExit.Proven && terminalStdoutFrame != null && originalReadyFrame != null &&
                Interlocked.CompareExchange(ref readyWritten, 0, 0) == 1 && readyWriteReturned);
            Stratus.RuntimeBuild.TerminalWire.RequireExact(terminalStdoutFrame, Stratus.RuntimeBuild.TerminalWire.GuardianTerminal(
                terminalObservation.Record, originalReadyNonce, normalProducerExit.Exited));
            Require(checked(originalReadyFrame.Length + terminalStdoutFrame.Length) <= MaximumGuardianStdoutBytes);
            DemandQualifiedNormalTerminalObservation();
            Require(Interlocked.CompareExchange(ref terminalOutputWriteAttempted, 1, 0) == 0);
            readyStream.Write(terminalStdoutFrame, 0, terminalStdoutFrame.Length); readyStream.Flush();
            terminalOutputWriteReturned = true;
            AssertTerminalControlWorker(ref workerTick);
            Interlocked.Exchange(ref terminalOutputWritten, 1);
        }
    }
}
