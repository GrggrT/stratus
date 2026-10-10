// UNQUALIFIED local P terminal ownership. This is not a wire receipt, normal
// P/W exit proof, independently qualified output acceptance or permission to run.
using System;
using System.ComponentModel;
using System.IO;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Threading;

namespace Stratus.RuntimeBuild {
    internal sealed class ProducerCompilerClosure {
        private readonly ProducerCompilerOwner owner;
        private readonly CompilerCommand command;
        internal readonly CompilerResult Result;
        internal readonly IntPtr SourceJob, SourceRoot;
        internal readonly int ProcessId;
        internal readonly long Birth, NativeExitFileTime;
        internal ProducerCompilerClosure(ProducerCompilerOwner originalOwner, CompilerCommand originalCommand,
                CompilerResult result, IntPtr sourceJob, IntPtr sourceRoot, int pid, long birth, long nativeExitFileTime) {
            owner = originalOwner; command = originalCommand; Result = result;
            SourceJob = sourceJob; SourceRoot = sourceRoot; ProcessId = pid; Birth = birth; NativeExitFileTime = nativeExitFileTime;
        }
        internal bool BelongsTo(ProducerCompilerOwner originalOwner, CompilerCommand originalCommand) {
            return Object.ReferenceEquals(owner, originalOwner) && Object.ReferenceEquals(command, originalCommand);
        }
    }

    internal sealed partial class ProducerInvocation {
        private sealed class RetainedProducerTerminal {
            internal readonly ProducerCompilerOwner Owner;
            internal readonly ProducerCompilerClosure Closure;
            internal readonly CompilerJobOffer Job;
            internal readonly RetainedCompilerRootOffer Root;
            internal readonly TerminalRecord Record;
            internal readonly byte[] Prepared, ExpectedAck, Commit, Stdout;
            internal readonly Thread Reader, Writer;
            internal readonly AnonymousPipeClientStream Input, Output;
            internal RetainedProducerTerminal(ProducerCompilerOwner owner, ProducerCompilerClosure closure,
                    CompilerJobOffer job, RetainedCompilerRootOffer root, TerminalRecord record,
                    Thread reader, Thread writer, AnonymousPipeClientStream input, AnonymousPipeClientStream output) {
                Owner = owner; Closure = closure; Job = job; Root = root; Record = record;
                Reader = reader; Writer = writer; Input = input; Output = output;
                Prepared = TerminalWire.Prepared(record); ExpectedAck = TerminalWire.Acknowledgement(record);
                Commit = TerminalWire.Commit(record); Stdout = TerminalWire.ProducerTerminal(record);
            }
        }
        private RetainedProducerTerminal producerTerminal;
        private int terminalPreparedAttempted, terminalPreparedWriteAttempted, terminalPreparedWriteReturned, terminalPreparedWritten;
        private int terminalPreparedFlushAttempted, terminalPreparedFlushReturned;
        private int terminalAckReceived, terminalGuardianEof, terminalCommitWriteAttempted, terminalCommitWriteReturned, terminalCommitWritten;
        private byte[] terminalActualAck;
        private int terminalAckBytes, terminalEofReadAttempted, terminalEofReadReturned;
        private int terminalEofReadCount = -1;
        private int terminalCommitFlushAttempted, terminalCommitFlushReturned;
        private int terminalHalfCloseAttempted, terminalHalfCloseReturned, terminalHalfCloseSucceeded;
        private IntPtr terminalHalfCloseOriginal;
        private int terminalHalfCloseError;
        private Stream terminalStdout;
        private int terminalStdoutOpenAttempted, terminalStdoutOpenReturned, terminalStdoutWriteAttempted, terminalStdoutWriteReturned, terminalStdoutWritten;
        private int terminalStdoutFlushAttempted, terminalStdoutFlushReturned;
        private int terminalReaderSettled, terminalWriterSettled;
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool CloseHandle(IntPtr handle);

        private static void DemandQualifiedProducerTerminalTransport() {
            throw new InvalidOperationException("Actual terminal transport, half-close and normal P/W exit require independent native qualification.");
        }
        private void AssertTerminalChannelPending() {
            AssertOriginal(); AssertOwnIdentity(); AssertDeadline(); AssertClean();
            Require(started && contextCapture != null && contextCapture.IsComplete);
            contextCapture.AssertHealthy();
            // A pending ACK read is admitted before local closure and remains
            // valid after phase 2/input release. It does not inspect live inputs.
        }
        private void AssertPreparedTerminalObservation(RetainedProducerTerminal original) {
            AssertTerminalChannelPending();
            Require(original != null && Object.ReferenceEquals(Interlocked.CompareExchange(ref producerTerminal, null, null), original) &&
                Interlocked.CompareExchange(ref terminalPreparedAttempted, 0, 0) == 1 &&
                Object.ReferenceEquals(Interlocked.CompareExchange(ref compilerOwner, null, null), original.Owner) &&
                Object.ReferenceEquals(Interlocked.CompareExchange(ref compilerJobOffer, null, null), original.Job) &&
                Object.ReferenceEquals(Interlocked.CompareExchange(ref compilerRootOffer, null, null), original.Root) &&
                Object.ReferenceEquals(original.Root.Job, original.Job) && Object.ReferenceEquals(original.Job.Owner, original.Owner) &&
                Object.ReferenceEquals(readerThread, original.Reader) && Object.ReferenceEquals(writerThread, original.Writer) &&
                Object.ReferenceEquals(inputStream, original.Input) && Object.ReferenceEquals(outputStream, original.Output) &&
                original.Job.SourceJob == original.Closure.SourceJob && original.Root.SourceRoot == original.Closure.SourceRoot &&
                original.Root.ProcessId == original.Closure.ProcessId && original.Root.Birth == original.Closure.Birth &&
                original.Record.Binding == original.Job.Binding && original.Record.InputCount == payloadInputRecords.Length &&
                Interlocked.CompareExchange(ref compilerJobOfferWritten, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref compilerJobAckReceived, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref compilerRootOfferWritten, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref compilerRootAckReceived, 0, 0) == 1);
            original.Owner.AssertTerminalObservation(original.Closure);
            AssertProducerTerminalInputsReleased(original.Owner, original.Closure);
        }
        private void ReceiveOriginalTerminalAcknowledgement() {
            Require(Object.ReferenceEquals(Thread.CurrentThread, readerThread) &&
                Interlocked.CompareExchange(ref compilerRootAckReceived, 0, 0) == 1);
            byte[] first = new byte[1]; AssertTerminalChannelPending();
            int firstCount = inputStream.Read(first, 0, 1);
            RetainedProducerTerminal originalAtRead = Interlocked.CompareExchange(ref producerTerminal, null, null);
            int writeAttemptedAtRead = Interlocked.CompareExchange(ref terminalPreparedWriteAttempted, 0, 0);
            AssertTerminalChannelPending();
            Require(firstCount == 1 && originalAtRead != null && writeAttemptedAtRead == 1);
            AssertPreparedTerminalObservation(originalAtRead);
            byte[] expected = originalAtRead.ExpectedAck;
            Require(expected.Length > 0 && expected.Length <= TerminalWire.MaximumAckBytes);
            byte[] actual = new byte[expected.Length];
            Require(Interlocked.CompareExchange(ref terminalActualAck, actual, null) == null);
            actual[0] = first[0]; Interlocked.Exchange(ref terminalAckBytes, 1); Require(actual[0] == expected[0]);
            int used = 1;
            while (used < actual.Length) {
                AssertPreparedTerminalObservation(originalAtRead);
                int count = inputStream.Read(actual, used, actual.Length - used);
                AssertPreparedTerminalObservation(originalAtRead);
                Require(count > 0 && count <= actual.Length - used); used = checked(used + count);
                Interlocked.Exchange(ref terminalAckBytes, used);
            }
            TerminalWire.RequireExact(actual, expected); AssertPreparedTerminalObservation(originalAtRead);
            Require(Interlocked.CompareExchange(ref terminalAckReceived, 1, 0) == 0);
            // Receipt does not wait for the sender's post-Flush publication. The
            // same reader immediately observes actual W write-half EOF.
            AssertPreparedTerminalObservation(originalAtRead);
            Require(Interlocked.CompareExchange(ref terminalEofReadAttempted, 1, 0) == 0);
            int eof = inputStream.Read(first, 0, 1);
            Interlocked.Exchange(ref terminalEofReadCount, eof); Interlocked.Exchange(ref terminalEofReadReturned, 1);
            AssertPreparedTerminalObservation(originalAtRead); Require(eof == 0);
            Require(Interlocked.CompareExchange(ref terminalGuardianEof, 1, 0) == 0);
        }
        private void SendOriginalTerminal() {
            Require(Object.ReferenceEquals(Thread.CurrentThread, writerThread));
            ProducerCompilerOwner owner = Interlocked.CompareExchange(ref compilerOwner, null, null);
            Require(owner != null);
            ProducerCompilerClosure closure;
            while ((closure = owner.PollPreparedLocalClosure()) == null) {
                AssertTerminalChannelPending(); Thread.Sleep(10);
            }
            AssertTerminalChannelPending();
            CompilerJobOffer job = Interlocked.CompareExchange(ref compilerJobOffer, null, null);
            RetainedCompilerRootOffer root = Interlocked.CompareExchange(ref compilerRootOffer, null, null);
            Require(job != null && root != null && Object.ReferenceEquals(root.Job, job));
            TerminalRecord record = CreateOriginalTerminalRecord(owner, closure, job, root);
            Require(Interlocked.CompareExchange(ref terminalPreparedAttempted, 1, 0) == 0);
            RetainedProducerTerminal original = new RetainedProducerTerminal(owner, closure, job, root, record,
                readerThread, writerThread, inputStream, outputStream);
            Require(Interlocked.CompareExchange(ref producerTerminal, original, null) == null);
            AssertPreparedTerminalObservation(original);
            DemandQualifiedProducerTerminalTransport();
            Require(Interlocked.CompareExchange(ref terminalPreparedWriteAttempted, 1, 0) == 0);
            outputStream.Write(original.Prepared, 0, original.Prepared.Length);
            Interlocked.Exchange(ref terminalPreparedWriteReturned, 1);
            Require(Interlocked.CompareExchange(ref terminalPreparedFlushAttempted, 1, 0) == 0);
            outputStream.Flush(); Interlocked.Exchange(ref terminalPreparedFlushReturned, 1);
            AssertPreparedTerminalObservation(original);
            Require(Interlocked.CompareExchange(ref terminalPreparedWritten, 1, 0) == 0);
            while (Interlocked.CompareExchange(ref terminalAckReceived, 0, 0) != 1 ||
                    Interlocked.CompareExchange(ref terminalGuardianEof, 0, 0) != 1) {
                AssertPreparedTerminalObservation(original); Thread.Sleep(10);
            }
            AssertPreparedTerminalObservation(original);
            Require(Interlocked.CompareExchange(ref terminalPreparedWritten, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref terminalCommitWriteAttempted, 1, 0) == 0);
            outputStream.Write(original.Commit, 0, original.Commit.Length);
            Interlocked.Exchange(ref terminalCommitWriteReturned, 1);
            Require(Interlocked.CompareExchange(ref terminalCommitFlushAttempted, 1, 0) == 0);
            outputStream.Flush(); Interlocked.Exchange(ref terminalCommitFlushReturned, 1);
            AssertPreparedTerminalObservation(original);
            Require(Interlocked.CompareExchange(ref terminalCommitWritten, 1, 0) == 0);
            HalfCloseOriginalTerminalOutput(original);
            WriteOriginalTerminalStdout(original);
        }
        private TerminalRecord CreateOriginalTerminalRecord(ProducerCompilerOwner owner, ProducerCompilerClosure closure,
                CompilerJobOffer job, RetainedCompilerRootOffer root) {
            Require(Object.ReferenceEquals(Thread.CurrentThread, writerThread) && owner != null && closure != null &&
                job != null && root != null && Object.ReferenceEquals(Interlocked.CompareExchange(ref compilerOwner, null, null), owner) &&
                Object.ReferenceEquals(Interlocked.CompareExchange(ref compilerJobOffer, null, null), job) &&
                Object.ReferenceEquals(Interlocked.CompareExchange(ref compilerRootOffer, null, null), root) &&
                Object.ReferenceEquals(job.Owner, owner) && Object.ReferenceEquals(root.Job, job) &&
                closure.BelongsTo(owner, job.Command) && closure.SourceJob == job.SourceJob && closure.SourceRoot == root.SourceRoot &&
                closure.ProcessId == root.ProcessId && closure.Birth == root.Birth);
            owner.AssertTerminalObservation(closure);
            Require(Object.ReferenceEquals(owner.PollPreparedLocalClosure(), closure));
            string inputHash = owner.CopyTerminalInputCommitment(closure);
            TerminalArtifactCommitments artifacts = owner.CopyTerminalArtifactCommitments(closure);
            CompilerResult result = closure.Result;
            Require(result != null && result.ProcessId == closure.ProcessId && result.ExitCode == 0 && result.StderrBytes == 0 &&
                artifacts != null && artifacts.Stdout != null && result.StdoutBytes == artifacts.Stdout.Bytes);
            // Only this P-side original owner projects the actual retained result.
            // The shared wire layer accepts no compiler owner or result object.
            TerminalRecord record = TerminalWire.Create(job.Binding,
                new CompilerRootOffer(closure.SourceJob, closure.SourceRoot, closure.ProcessId, closure.Birth),
                closure.NativeExitFileTime, result.StartedAt, result.CompletedAt, result.ElapsedMilliseconds,
                result.EnvironmentSha256, payloadInputRecords.Length, inputHash, artifacts);
            owner.AssertTerminalObservation(closure); return record;
        }
        private void HalfCloseOriginalTerminalOutput(RetainedProducerTerminal original) {
            AssertPreparedTerminalObservation(original);
            Require(Object.ReferenceEquals(Thread.CurrentThread, original.Writer) &&
                Interlocked.CompareExchange(ref terminalCommitWritten, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref terminalHalfCloseAttempted, 0, 0) == 0 && outputHandle != null);
            terminalHalfCloseOriginal = outputValue;
            Require(Interlocked.CompareExchange(ref terminalHalfCloseAttempted, 1, 0) == 0);
            bool closed = CloseHandle(terminalHalfCloseOriginal);
            terminalHalfCloseError = Marshal.GetLastWin32Error();
            if (closed) Interlocked.Exchange(ref terminalHalfCloseSucceeded, 1);
            Interlocked.Exchange(ref terminalHalfCloseReturned, 1);
            if (!closed) throw new Win32Exception(terminalHalfCloseError, "Close only the original P terminal write endpoint");
            outputHandle.SetHandleAsInvalid();
            // Never query, close, write, Flush or Dispose this old endpoint again.
            AssertPreparedTerminalObservation(original);
        }
        private void WriteOriginalTerminalStdout(RetainedProducerTerminal original) {
            AssertPreparedTerminalObservation(original);
            Require(Object.ReferenceEquals(Thread.CurrentThread, original.Writer) &&
                Interlocked.CompareExchange(ref terminalHalfCloseReturned, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref terminalHalfCloseSucceeded, 0, 0) == 1 &&
                original.Stdout.Length > 0 && original.Stdout.Length <= TerminalWire.MaximumProducerBytes &&
                terminalStdout == null && Interlocked.CompareExchange(ref terminalStdoutOpenAttempted, 1, 0) == 0);
            Require(GetStdHandle(-11) == standardOutputValue && GetFileType(standardOutputValue) == FileTypePipe);
            terminalStdout = Console.OpenStandardOutput();
            Require(terminalStdout != null); GC.SuppressFinalize(terminalStdout);
            Interlocked.Exchange(ref terminalStdoutOpenReturned, 1);
            AssertPreparedTerminalObservation(original);
            Require(Interlocked.CompareExchange(ref terminalStdoutWriteAttempted, 1, 0) == 0);
            terminalStdout.Write(original.Stdout, 0, original.Stdout.Length);
            Interlocked.Exchange(ref terminalStdoutWriteReturned, 1);
            Require(Interlocked.CompareExchange(ref terminalStdoutFlushAttempted, 1, 0) == 0);
            terminalStdout.Flush(); Interlocked.Exchange(ref terminalStdoutFlushReturned, 1);
            AssertPreparedTerminalObservation(original);
            Require(Interlocked.CompareExchange(ref terminalStdoutWritten, 1, 0) == 0);
            // No compiler stdout forwarding and no disposal after an unknown
            // write. Physical P death supplies the outer stdout EOF to L.
        }
        internal bool PollTerminalExit() {
            Enter();
            try {
                AssertOriginal(); AssertOwnIdentity(); AssertDeadline(); AssertClean();
                RetainedProducerTerminal original = Interlocked.CompareExchange(ref producerTerminal, null, null);
                if (original == null || Interlocked.CompareExchange(ref terminalReaderSettled, 0, 0) != 1 ||
                        Interlocked.CompareExchange(ref terminalWriterSettled, 0, 0) != 1) return false;
                AssertPreparedTerminalObservation(original);
                Require(readerThread != null && writerThread != null && inputWorker != null &&
                    Interlocked.CompareExchange(ref inputWorkerSettled, 0, 0) == 1);
                if (!readerThread.Join(0) || !writerThread.Join(0) || !inputWorker.Join(0) ||
                        !contextCapture.IsWorkerSettledForTerminal) return false;
                Require(Object.ReferenceEquals(original.Owner.PollPreparedLocalClosure(), original.Closure) &&
                    Interlocked.CompareExchange(ref terminalPreparedWriteAttempted, 0, 0) == 1 &&
                    Interlocked.CompareExchange(ref terminalPreparedWriteReturned, 0, 0) == 1 &&
                    Interlocked.CompareExchange(ref terminalPreparedFlushAttempted, 0, 0) == 1 &&
                    Interlocked.CompareExchange(ref terminalPreparedFlushReturned, 0, 0) == 1 &&
                    Interlocked.CompareExchange(ref terminalPreparedWritten, 0, 0) == 1 &&
                    Interlocked.CompareExchange(ref terminalAckReceived, 0, 0) == 1 &&
                    terminalActualAck != null && Interlocked.CompareExchange(ref terminalAckBytes, 0, 0) == original.ExpectedAck.Length &&
                    Interlocked.CompareExchange(ref terminalEofReadAttempted, 0, 0) == 1 &&
                    Interlocked.CompareExchange(ref terminalEofReadReturned, 0, 0) == 1 &&
                    Interlocked.CompareExchange(ref terminalEofReadCount, 0, 0) == 0 &&
                    Interlocked.CompareExchange(ref terminalGuardianEof, 0, 0) == 1 &&
                    Interlocked.CompareExchange(ref terminalCommitWriteAttempted, 0, 0) == 1 &&
                    Interlocked.CompareExchange(ref terminalCommitWriteReturned, 0, 0) == 1 &&
                    Interlocked.CompareExchange(ref terminalCommitFlushAttempted, 0, 0) == 1 &&
                    Interlocked.CompareExchange(ref terminalCommitFlushReturned, 0, 0) == 1 &&
                    Interlocked.CompareExchange(ref terminalCommitWritten, 0, 0) == 1 &&
                    terminalHalfCloseOriginal == outputValue && Interlocked.CompareExchange(ref terminalHalfCloseAttempted, 0, 0) == 1 &&
                    Interlocked.CompareExchange(ref terminalHalfCloseReturned, 0, 0) == 1 &&
                    Interlocked.CompareExchange(ref terminalHalfCloseSucceeded, 0, 0) == 1 &&
                    terminalStdout != null && Interlocked.CompareExchange(ref terminalStdoutOpenAttempted, 0, 0) == 1 &&
                    Interlocked.CompareExchange(ref terminalStdoutOpenReturned, 0, 0) == 1 &&
                    Interlocked.CompareExchange(ref terminalStdoutWriteAttempted, 0, 0) == 1 &&
                    Interlocked.CompareExchange(ref terminalStdoutWriteReturned, 0, 0) == 1 &&
                    Interlocked.CompareExchange(ref terminalStdoutFlushAttempted, 0, 0) == 1 &&
                    Interlocked.CompareExchange(ref terminalStdoutFlushReturned, 0, 0) == 1 &&
                    Interlocked.CompareExchange(ref terminalStdoutWritten, 0, 0) == 1);
                TerminalWire.RequireExact(terminalActualAck, original.ExpectedAck);
                AssertPreparedTerminalObservation(original); AssertDeadline();
                return true;
            } catch (Exception) { RecordFailure(); throw; }
            finally { Leave(); }
        }
        internal void AssertCompilerTerminalOwnerManaged(ProducerCompilerOwner owner) {
            AssertOriginal();
            Require(owner != null && started && compilerStartAttempted &&
                Object.ReferenceEquals(Interlocked.CompareExchange(ref compilerOwner, null, null), owner) &&
                contextCapture != null && Object.ReferenceEquals(contextCapture, payloadContextCapture) &&
                payloadMetadata != null && payloadInputRecords != null && payloadInputs != null && payloadSnapshots != null &&
                Interlocked.CompareExchange(ref inputVerified, 0, 0) == 1);
        }
        internal void AssertCompilerTerminalClock(ProducerCompilerOwner owner) {
            AssertCompilerTerminalOwnerManaged(owner);
            AssertOwnIdentity(); AssertDeadline(); AssertClean(); contextCapture.AssertHealthy();
            Require(contextCapture.IsComplete && Object.ReferenceEquals(contextCapture.AdmittedMetadata, payloadMetadata));
            AssertCompilerTerminalOwnerManaged(owner);
        }
    }

    internal sealed partial class ProducerCompilerOwner {
        private ProducerCompilerClosure terminalClosure;
        private ProducerTerminalArtifacts terminalArtifacts;
        private TerminalArtifactCommitments terminalArtifactCommitments;
        private int terminalArtifactPreparationAttempted, terminalLocalPrepared, workerSettled;

        private void AssertRetainedCompilerManaged(int expectedPhase) {
            Require(Interlocked.CompareExchange(ref failed, 0, 0) == 0 &&
                Interlocked.CompareExchange(ref phase, 0, 0) == expectedPhase &&
                Interlocked.CompareExchange(ref startAttempted, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref threadStartAttempted, 0, 0) == 1 &&
                command != null && preparation != null &&
                Interlocked.CompareExchange(ref registrationAttempted, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref registrationReturned, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref registrationSucceeded, 0, 0) == 1);
            invocation.AssertCompilerTerminalOwnerManaged(this);
        }
        private void AssertOriginalCompilerWorkerManaged() {
            Require(worker != null && Object.ReferenceEquals(Thread.CurrentThread, worker) &&
                Thread.CurrentThread.ManagedThreadId == worker.ManagedThreadId);
        }
        internal void AssertTerminalObservation(ProducerCompilerClosure closure) {
            AssertRetainedCompilerManaged(3);
            Require(closure != null && Object.ReferenceEquals(Interlocked.CompareExchange(ref terminalClosure, null, null), closure));
            command.AssertProducerTerminalObservation(this, closure);
            invocation.AssertCompilerTerminalClock(this);
        }
        internal void AssertTerminalWorkerManaged(ProducerCompilerClosure closure) {
            AssertRetainedCompilerManaged(3); AssertOriginalCompilerWorkerManaged();
            Require(closure != null && Object.ReferenceEquals(Interlocked.CompareExchange(ref terminalClosure, null, null), closure));
            command.AssertProducerTerminalObservation(this, closure);
        }
        internal void AssertTerminalWorker(ProducerCompilerClosure closure) {
            AssertTerminalWorkerManaged(closure); invocation.AssertCompilerTerminalClock(this);
        }
        internal void AssertTerminalInputRegistrationReleased(ProducerCompilerClosure closure) {
            AssertTerminalWorker(closure); command.AssertProducerInputRegistrationReleased(this, closure);
        }
        internal void AssertTerminalArtifactPreparation() {
            AssertRetainedCompilerManaged(2); AssertOriginalCompilerWorkerManaged();
            Require(Interlocked.CompareExchange(ref terminalArtifactPreparationAttempted, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref terminalArtifacts, null, null) == null && terminalClosure == null);
        }
        internal void AssertTerminalArtifactAdmission(ProducerTerminalArtifacts original) {
            AssertRetainedCompilerManaged(2); AssertOriginalCompilerWorkerManaged();
            Require(original != null && Object.ReferenceEquals(Interlocked.CompareExchange(ref terminalArtifacts, null, null), original) &&
                Interlocked.CompareExchange(ref terminalArtifactPreparationAttempted, 0, 0) == 1 && terminalClosure == null);
        }
        internal void AssertTerminalArtifactCapture(ProducerTerminalArtifacts original, ProducerCompilerClosure closure) {
            AssertTerminalWorkerManaged(closure);
            Require(original != null && Object.ReferenceEquals(Interlocked.CompareExchange(ref terminalArtifacts, null, null), original) &&
                Interlocked.CompareExchange(ref terminalArtifactPreparationAttempted, 0, 0) == 1);
        }
        internal void AssertTerminalArtifactDistinctFromInputs(ProducerTerminalArtifacts original, TerminalArtifactCommitments commitments) {
            AssertTerminalArtifactCapture(original, terminalClosure);
            invocation.AssertTerminalArtifactsDistinctFromInputs(this, terminalClosure, commitments);
        }
        internal ContextMetadata BorrowTerminalMetadataForArtifacts(ProducerTerminalArtifacts original) {
            int currentPhase = Interlocked.CompareExchange(ref phase, 0, 0);
            if (currentPhase == 2) AssertTerminalArtifactAdmission(original);
            else {
                Require(currentPhase == 3); AssertTerminalArtifactCapture(original, terminalClosure);
            }
            return preparation.Metadata;
        }
        internal void AssertTerminalArtifactBoundary(ProducerTerminalArtifacts original) {
            int currentPhase = Interlocked.CompareExchange(ref phase, 0, 0);
            if (currentPhase == 2) { AssertTerminalArtifactAdmission(original); AssertCommandAdmission(command); }
            else {
                Require(currentPhase == 3); AssertTerminalArtifactCapture(original, terminalClosure);
                invocation.AssertCompilerTerminalClock(this);
            }
        }
        private void PrepareTerminalArtifactsBeforeBirth(CompilerCommand original) {
            AssertCommandAdmission(original);
            Require(Interlocked.CompareExchange(ref terminalArtifactPreparationAttempted, 1, 0) == 0);
            ProducerTerminalArtifacts prepared = ProducerTerminalArtifacts.Prepare(this);
            Require(prepared != null && Interlocked.CompareExchange(ref terminalArtifacts, prepared, null) == null);
            AssertTerminalArtifactAdmission(prepared);
            prepared.OpenBeforeCompilerBirth();
            AssertCommandAdmission(original);
        }
        private void PrepareLocalTerminal(CompilerCommand original) {
            AssertContinuationAdmission(original);
            ProducerCompilerClosure closure = original.SealProducerLocalClosure(this);
            Require(closure != null && Interlocked.CompareExchange(ref terminalClosure, closure, null) == null);
            Require(Interlocked.CompareExchange(ref phase, 3, 2) == 2);
            AssertTerminalWorker(closure);
            original.ReleaseProducerInputRegistration(this);
            invocation.RecaptureProducerTerminalInputs(this, closure);
            AssertTerminalArtifactCapture(terminalArtifacts, closure);
            terminalArtifacts.CaptureAfterCompilerClosure(closure);
            TerminalArtifactCommitments commitments = terminalArtifacts.CopyCommitments();
            AssertTerminalArtifactDistinctFromInputs(terminalArtifacts, commitments);
            Require(commitments != null && Interlocked.CompareExchange(ref terminalArtifactCommitments, commitments, null) == null);
            invocation.ReleaseProducerTerminalInputs(this, closure);
            AssertTerminalWorker(closure);
            Require(Interlocked.CompareExchange(ref terminalLocalPrepared, 1, 0) == 0);
            // No terminal bytes or normal process exit are inferred here. The
            // forthcoming actual handshake consumes these retained local facts.
        }
        internal ProducerCompilerClosure PollPreparedLocalClosure() {
            invocation.AssertCompilerTerminalClock(this);
            if (Interlocked.CompareExchange(ref terminalLocalPrepared, 0, 0) != 1 ||
                    Interlocked.CompareExchange(ref workerSettled, 0, 0) != 1 ||
                    Interlocked.CompareExchange(ref threadStartReturned, 0, 0) != 1) return null;
            Require(worker != null);
            if (!worker.Join(0)) return null;
            ProducerCompilerClosure closure = Interlocked.CompareExchange(ref terminalClosure, null, null);
            AssertTerminalObservation(closure); invocation.AssertProducerTerminalInputsReleased(this, closure);
            Require(Interlocked.CompareExchange(ref terminalArtifactCommitments, null, null) != null);
            return closure;
        }
        internal string CopyTerminalInputCommitment(ProducerCompilerClosure closure) {
            AssertTerminalObservation(closure);
            Require(Interlocked.CompareExchange(ref terminalLocalPrepared, 0, 0) == 1);
            return invocation.CopyProducerTerminalInputCommitment(this, closure);
        }
        internal TerminalArtifactCommitments CopyTerminalArtifactCommitments(ProducerCompilerClosure closure) {
            AssertTerminalObservation(closure);
            Require(Interlocked.CompareExchange(ref terminalLocalPrepared, 0, 0) == 1);
            TerminalArtifactCommitments commitments = Interlocked.CompareExchange(ref terminalArtifactCommitments, null, null);
            Require(commitments != null); return commitments;
        }
    }
}
