// UNQUALIFIED real P root publication/ACK path on the original serialized pipes.
// Native birth, transfer, continuation and terminal qualification remain closed.
using System;
using System.Threading;

namespace Stratus.RuntimeBuild {
    internal sealed partial class ProducerInvocation {
        private sealed class RetainedCompilerRootOffer {
            internal readonly CompilerJobOffer Job;
            internal readonly IntPtr SourceRoot;
            internal readonly int ProcessId;
            internal readonly long Birth;
            internal readonly byte[] Bytes, ExpectedAck;
            internal RetainedCompilerRootOffer(CompilerJobOffer job, IntPtr root, int pid, long birth) {
                Job = job; SourceRoot = root; ProcessId = pid; Birth = birth;
                Bytes = CompilerRootWire.Offer(job.Binding, job.SourceJob, root, pid, birth);
                ExpectedAck = CompilerRootWire.Acknowledgement(job.Binding, job.SourceJob, root, pid, birth);
            }
        }
        private RetainedCompilerRootOffer compilerRootOffer;
        private int compilerRootOfferAttempted, compilerRootOfferWriteAttempted, compilerRootOfferWritten, compilerRootAckReceived;

        internal void PublishCompilerRootOffer(ProducerCompilerOwner owner, CompilerCommand command,
                IntPtr sourceJob, IntPtr sourceRoot, int pid, long birth) {
            try {
                owner.AssertCommandAdmission(command); AssertCompilerJobAck(owner, command);
                CompilerJobOffer job = Interlocked.CompareExchange(ref compilerJobOffer, null, null);
                Require(job != null && job.SourceJob == sourceJob && Object.ReferenceEquals(job.Owner, owner) &&
                    Object.ReferenceEquals(job.Command, command));
                command.AssertOriginalRootOfferObservation(owner, sourceJob, sourceRoot, pid, birth);
                Require(Interlocked.CompareExchange(ref compilerRootOfferAttempted, 1, 0) == 0);
                RetainedCompilerRootOffer prepared = new RetainedCompilerRootOffer(job, sourceRoot, pid, birth);
                Require(Interlocked.CompareExchange(ref compilerRootOffer, prepared, null) == null);
                AssertRootOfferObservation(prepared);
            } catch (Exception) { RecordFailure(); throw; }
        }
        private void AssertRootOfferObservation(RetainedCompilerRootOffer offer) {
            Require(offer != null && Object.ReferenceEquals(Interlocked.CompareExchange(ref compilerRootOffer, null, null), offer) &&
                Interlocked.CompareExchange(ref compilerRootOfferAttempted, 0, 0) == 1);
            AssertJobOfferObservation(offer.Job);
            AssertCompilerJobAck(offer.Job.Owner, offer.Job.Command);
            offer.Job.Command.AssertOriginalRootOfferObservation(offer.Job.Owner, offer.Job.SourceJob,
                offer.SourceRoot, offer.ProcessId, offer.Birth);
            AssertInputAdmission();
        }
        private RetainedCompilerRootOffer WaitForPublishedCompilerRootOffer() {
            while (true) {
                AssertOwnIdentity(); AssertDeadline(); AssertClean(); contextCapture.AssertHealthy();
                RetainedCompilerRootOffer offer = Interlocked.CompareExchange(ref compilerRootOffer, null, null);
                if (offer != null) { AssertRootOfferObservation(offer); return offer; }
                Thread.Sleep(10);
            }
        }
        private void SendOriginalCompilerRootOffer() {
            Require(Object.ReferenceEquals(Thread.CurrentThread, writerThread));
            RetainedCompilerRootOffer offer = WaitForPublishedCompilerRootOffer();
            AssertRootOfferObservation(offer);
            Require(Interlocked.CompareExchange(ref compilerRootOfferWriteAttempted, 1, 0) == 0);
            outputStream.Write(offer.Bytes, 0, offer.Bytes.Length);
            outputStream.Flush();
            AssertRootOfferObservation(offer);
            Require(Interlocked.CompareExchange(ref compilerRootOfferWritten, 1, 0) == 0);
        }
        private void ReceiveOriginalCompilerRootAck() {
            Require(Object.ReferenceEquals(Thread.CurrentThread, readerThread));
            // Immediately pending after the actual Job ACK, including while the
            // worker waits for local Flush publication and performs native birth.
            // EOF/early bytes are never hidden behind root preparation or a flag.
            byte[] first = new byte[1];
            AssertOwnIdentity(); AssertDeadline(); AssertClean(); contextCapture.AssertHealthy();
            int firstCount = inputStream.Read(first, 0, 1);
            RetainedCompilerRootOffer offer = Interlocked.CompareExchange(ref compilerRootOffer, null, null);
            int writeAttemptedAtRead = Interlocked.CompareExchange(ref compilerRootOfferWriteAttempted, 0, 0);
            AssertOwnIdentity(); AssertDeadline(); AssertClean(); contextCapture.AssertHealthy();
            Require(firstCount == 1);
            Require(offer != null && writeAttemptedAtRead == 1);
            AssertRootOfferObservation(offer);
            byte[] actual = new byte[offer.ExpectedAck.Length];
            Require(actual.Length > 0 && actual.Length <= CompilerRootWire.MaximumBytes);
            actual[0] = first[0]; Require(actual[0] == offer.ExpectedAck[0]);
            int used = 1;
            while (used < actual.Length) {
                AssertRootOfferObservation(offer);
                int count = inputStream.Read(actual, used, actual.Length - used);
                AssertRootOfferObservation(offer);
                Require(count > 0 && count <= actual.Length - used);
                used = checked(used + count);
            }
            CompilerRootWire.RequireExact(actual, offer.ExpectedAck);
            AssertRootOfferObservation(offer);
            Require(Interlocked.CompareExchange(ref compilerRootAckReceived, 1, 0) == 0);
            // Do not wait on the writer here: the same reader immediately enters
            // the existing surplus/EOF read; the compiler worker waits both facts.
        }
        internal void WaitForCompilerRootAck(ProducerCompilerOwner owner, CompilerCommand command) {
            owner.AssertCommandAdmission(command);
            RetainedCompilerRootOffer offer = Interlocked.CompareExchange(ref compilerRootOffer, null, null);
            Require(offer != null && Object.ReferenceEquals(offer.Job.Owner, owner) && Object.ReferenceEquals(offer.Job.Command, command));
            while (Interlocked.CompareExchange(ref compilerRootAckReceived, 0, 0) != 1 ||
                    Interlocked.CompareExchange(ref compilerRootOfferWritten, 0, 0) != 1) {
                owner.AssertCommandAdmission(command); AssertRootOfferObservation(offer); Thread.Sleep(10);
            }
            AssertCompilerRootAck(owner, command); owner.AssertCommandAdmission(command);
        }
        internal void AssertCompilerRootAck(ProducerCompilerOwner owner, CompilerCommand command) {
            RetainedCompilerRootOffer offer = Interlocked.CompareExchange(ref compilerRootOffer, null, null);
            Require(offer != null && Object.ReferenceEquals(offer.Job.Owner, owner) && Object.ReferenceEquals(offer.Job.Command, command) &&
                Interlocked.CompareExchange(ref compilerRootOfferWriteAttempted, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref compilerRootOfferWritten, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref compilerRootAckReceived, 0, 0) == 1);
            AssertRootOfferObservation(offer);
        }
    }

    internal sealed partial class ProducerCompilerOwner {
        internal void PublishOriginalRoot(CompilerCommand original, IntPtr originalJob, IntPtr originalRoot, int pid, long birth) {
            AssertBirthAdmission(original);
            original.AssertOriginalRootOfferObservation(this, originalJob, originalRoot, pid, birth);
            invocation.PublishCompilerRootOffer(this, original, originalJob, originalRoot, pid, birth);
            AssertCommandAdmission(original);
        }
        private void WaitForOriginalCompilerRootAck(CompilerCommand original) {
            AssertCommandAdmission(original); invocation.WaitForCompilerRootAck(this, original); AssertCommandAdmission(original);
        }
        internal void AssertContinuationAdmission(CompilerCommand original) {
            AssertCommandAdmission(original); invocation.AssertCompilerRootAck(this, original); AssertCommandAdmission(original);
        }
        internal void DemandQualifiedCompilerRootTransfer(CompilerCommand original) {
            AssertBirthAdmission(original);
            throw new InvalidOperationException("Original compiler root transfer requires separately qualified P/W native capabilities and STOP.");
        }
        internal void DemandQualifiedCompilerContinuation(CompilerCommand original) {
            AssertContinuationAdmission(original);
            throw new InvalidOperationException("Compiler continuation requires separately qualified root ACK, native scheduling and terminal supervision.");
        }
        private static void DemandQualifiedCompilerTerminal() {
            throw new InvalidOperationException("Compiler completion is not qualified terminal success: P/W physical exit, EOF, input recapture/release and output writer acceptance remain unavailable.");
        }
    }
}
