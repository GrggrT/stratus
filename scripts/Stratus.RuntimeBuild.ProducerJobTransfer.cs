// UNQUALIFIED actual P invocation/worker link, not a caller-supplied receipt.
// A real reader-owned Job ACK does not qualify compiler birth or Resume.
using System;
using System.Threading;

namespace Stratus.RuntimeBuild {
    internal sealed partial class ProducerInvocation {
        private sealed class CompilerJobOffer {
            internal readonly ProducerCompilerOwner Owner;
            internal readonly CompilerCommand Command;
            internal readonly IntPtr SourceJob;
            internal readonly string Binding;
            internal readonly byte[] Bytes, ExpectedAck;
            internal CompilerJobOffer(ProducerCompilerOwner owner, CompilerCommand command,
                    IntPtr sourceJob, string binding) {
                Owner = owner; Command = command; SourceJob = sourceJob; Binding = binding;
                Bytes = CompilerJobWire.Offer(binding, sourceJob);
                ExpectedAck = CompilerJobWire.Acknowledgement(binding, sourceJob);
            }
        }
        private CompilerJobOffer compilerJobOffer;
        private int compilerJobOfferAttempted, compilerJobOfferWriteAttempted, compilerJobOfferWritten, compilerJobAckReceived;

        internal void PublishCompilerJobOffer(ProducerCompilerOwner owner, CompilerCommand command, IntPtr sourceJob) {
            try {
                AssertCompilerOwner(owner); owner.AssertCommandAdmission(command);
                command.AssertOriginalJobOfferObservation(owner, sourceJob);
                AssertInputAdmission();
                Require(Interlocked.CompareExchange(ref compilerJobOfferAttempted, 1, 0) == 0);
                string binding = CompilerJobWire.Binding(channelNonce, producerPid, producerBirth,
                    startedTick, deadlineTick, contextCapture.Length, contextCapture.Sha256);
                CompilerJobOffer prepared = new CompilerJobOffer(owner, command, sourceJob, binding);
                // All bytes and exact command links are retained before the writer
                // can observe publication. No public handle getter/approval input.
                Require(Interlocked.CompareExchange(ref compilerJobOffer, prepared, null) == null);
                AssertJobOfferObservation(prepared);
            } catch (Exception) { RecordFailure(); throw; }
        }
        private void AssertJobOfferObservation(CompilerJobOffer offer) {
            AssertInputAdmission();
            Require(offer != null && Object.ReferenceEquals(Interlocked.CompareExchange(ref compilerJobOffer, null, null), offer) &&
                Interlocked.CompareExchange(ref compilerJobOfferAttempted, 0, 0) == 1);
            AssertCompilerOwner(offer.Owner);
            offer.Owner.AssertTransferObservation(offer.Command);
            offer.Command.AssertOriginalJobOfferObservation(offer.Owner, offer.SourceJob);
            AssertInputAdmission();
        }
        private CompilerJobOffer WaitForPublishedCompilerJobOffer() {
            while (true) {
                // A writer can reach this before W's admission ACK. Wait on the
                // original identity/clock, not an input-admission flag guessed true.
                AssertOwnIdentity(); AssertDeadline(); AssertClean();
                contextCapture.AssertHealthy();
                CompilerJobOffer offer = Interlocked.CompareExchange(ref compilerJobOffer, null, null);
                if (offer != null) { AssertJobOfferObservation(offer); return offer; }
                Thread.Sleep(10);
            }
        }
        private void SendOriginalCompilerJobOffer() {
            Require(Object.ReferenceEquals(Thread.CurrentThread, writerThread));
            CompilerJobOffer offer = WaitForPublishedCompilerJobOffer();
            AssertJobOfferObservation(offer);
            Require(Interlocked.CompareExchange(ref compilerJobOfferWriteAttempted, 1, 0) == 0);
            outputStream.Write(offer.Bytes, 0, offer.Bytes.Length);
            outputStream.Flush();
            AssertJobOfferObservation(offer);
            Require(Interlocked.CompareExchange(ref compilerJobOfferWritten, 1, 0) == 0);
        }
        private void ReceiveOriginalCompilerJobAck() {
            Require(Object.ReferenceEquals(Thread.CurrentThread, readerThread));
            // Keep the actual reader pending immediately after admission ACK,
            // including while input/Job preparation is still in progress. EOF or
            // unsolicited bytes must not wait behind those local operations.
            byte[] first = new byte[1];
            AssertOwnIdentity(); AssertDeadline(); AssertClean(); contextCapture.AssertHealthy();
            int firstCount = inputStream.Read(first, 0, 1);
            CompilerJobOffer offer = Interlocked.CompareExchange(ref compilerJobOffer, null, null);
            int writeAttemptedAtRead = Interlocked.CompareExchange(ref compilerJobOfferWriteAttempted, 0, 0);
            AssertOwnIdentity(); AssertDeadline(); AssertClean(); contextCapture.AssertHealthy();
            Require(firstCount == 1);
            // A legitimate peer ACK cannot precede our retained publication and
            // offer bytes. An early frame is a fault, not a queue for future use.
            Require(offer != null && writeAttemptedAtRead == 1);
            AssertJobOfferObservation(offer);
            byte[] actual = new byte[offer.ExpectedAck.Length];
            Require(actual.Length > 0 && actual.Length <= CompilerJobWire.MaximumBytes);
            actual[0] = first[0]; Require(actual[0] == offer.ExpectedAck[0]);
            int used = 1;
            while (used < actual.Length) {
                AssertJobOfferObservation(offer);
                int count = inputStream.Read(actual, used, actual.Length - used);
                AssertJobOfferObservation(offer);
                Require(count > 0 && count <= actual.Length - used);
                used = checked(used + count);
            }
            CompilerJobWire.RequireExact(actual, offer.ExpectedAck);
            // Received bytes and the sender's post-Flush flag are separate facts.
            // Publish reception now, then enter the next pending read immediately;
            // only the compiler worker waits for BOTH before original birth.
            AssertJobOfferObservation(offer);
            Require(Interlocked.CompareExchange(ref compilerJobAckReceived, 1, 0) == 0);
        }
        internal void WaitForCompilerJobAck(ProducerCompilerOwner owner, CompilerCommand command) {
            owner.AssertCommandAdmission(command);
            CompilerJobOffer offer = Interlocked.CompareExchange(ref compilerJobOffer, null, null);
            Require(offer != null && Object.ReferenceEquals(offer.Owner, owner) && Object.ReferenceEquals(offer.Command, command));
            while (Interlocked.CompareExchange(ref compilerJobAckReceived, 0, 0) != 1 ||
                    Interlocked.CompareExchange(ref compilerJobOfferWritten, 0, 0) != 1) {
                owner.AssertCommandAdmission(command); AssertJobOfferObservation(offer); Thread.Sleep(10);
            }
            AssertJobOfferObservation(offer);
            Require(Interlocked.CompareExchange(ref compilerJobOfferWritten, 0, 0) == 1);
            owner.AssertCommandAdmission(command);
        }
        internal void AssertCompilerJobAck(ProducerCompilerOwner owner, CompilerCommand command) {
            CompilerJobOffer offer = Interlocked.CompareExchange(ref compilerJobOffer, null, null);
            Require(offer != null && Object.ReferenceEquals(offer.Owner, owner) && Object.ReferenceEquals(offer.Command, command) &&
                Interlocked.CompareExchange(ref compilerJobOfferWriteAttempted, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref compilerJobOfferWritten, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref compilerJobAckReceived, 0, 0) == 1);
            AssertJobOfferObservation(offer);
        }
    }

    internal sealed partial class ProducerCompilerOwner {
        internal void PublishOriginalJob(CompilerCommand original, IntPtr originalJob) {
            AssertCommandAdmission(original);
            Require(Interlocked.CompareExchange(ref jobPrepared, 0, 0) == 1);
            original.AssertOriginalJobOfferObservation(this, originalJob);
            invocation.PublishCompilerJobOffer(this, original, originalJob);
            AssertCommandAdmission(original);
        }
        internal void AssertTransferObservation(CompilerCommand original) {
            // Pipe workers are different original threads. This read-only phase
            // check deliberately does not grant AssertWorker(2)/birth authority.
            Require(Interlocked.CompareExchange(ref failed, 0, 0) == 0 &&
                Interlocked.CompareExchange(ref phase, 0, 0) == 2 &&
                Interlocked.CompareExchange(ref jobPrepared, 0, 0) == 1 &&
                original != null && Object.ReferenceEquals(command, original) && preparation != null &&
                Interlocked.CompareExchange(ref registrationAttempted, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref registrationReturned, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref registrationSucceeded, 0, 0) == 1);
            invocation.AssertCompilerOwner(this); invocation.AssertCompilerClock(this);
        }
        private void WaitForOriginalCompilerJobAck(CompilerCommand original) {
            AssertCommandAdmission(original);
            invocation.WaitForCompilerJobAck(this, original);
            AssertCommandAdmission(original);
        }
        internal void AssertBirthAdmission(CompilerCommand original) {
            AssertCommandAdmission(original); invocation.AssertCompilerJobAck(this, original);
            AssertCommandAdmission(original);
        }
        internal void DemandQualifiedCompilerBirth(CompilerCommand original) {
            AssertBirthAdmission(original);
            throw new InvalidOperationException("Compiler birth requires separate native qualification and complete root/terminal supervision.");
        }
    }
}
