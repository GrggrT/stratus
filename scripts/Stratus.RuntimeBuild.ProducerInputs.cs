// UNQUALIFIED SOURCE: the actual standalone P input phase, not an L-side adapter.
// This partial class is compiled with ProducerProgram.cs and the independently
// qualified bootstrap closure. Inputs.cs has real CompilerCommand/GuardianLaunch
// dependencies: it cannot be compiled alone or with fake borrower stubs. The
// shared closure includes Inputs, Bootstrap, Producer, ProducerTransfer,
// GuardianLaunch, Deadline, ControlChannel, ContextSection, StartupPreload,
// ProducerInvocation, ProducerCompiler, ProducerCompilerScope, ProducerJobTransfer,
// ProducerRootTransfer, CompilerJobWire, CompilerRootWire, TerminalWire,
// TerminalCommitments and capture/metadata/trust sources. P implementation lives in
// ProducerProgram, ProducerTerminal and TerminalArtifacts; W implementation lives in
// GuardianProgram, GuardianJobTransfer, GuardianRootTransfer and GuardianTerminal.
// The L-side terminal/output path additionally requires TerminalCoordinator and
// OutputOwner. GuardianLaunch now binds that coordinator; the inclusive P/L build
// closure therefore includes those files, even though W's narrow entry closure
// does not use GuardianLaunch or Bootstrap. See the separate source inventory.
// These role labels do not define separately compilable source sets: the existing
// Bootstrap/Inputs graph still has P-only compiler/terminal dependencies to review.
// Shared terminal value records must
// not require a P compiler/artifact owner merely to parse peer correspondence.
// This dependency inventory is not a claim of a minimal isolated W assembly.
// These source dependencies are
// not an approved build/load recipe, compiled binary or qualification manifest.
// No compilation or native qualification is established by this source.
using System;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Threading;

namespace Stratus.RuntimeBuild {
    internal sealed partial class ProducerInvocation {
        private bool inputStartAttempted;
        private int inputVerified;
        private int inputWorkerSettled;
        private Thread inputWorker;
        private Stratus.RuntimeBuild.ContextCapture payloadContextCapture;
        private Stratus.RuntimeBuild.ContextMetadata payloadMetadata;
        private Stratus.RuntimeBuild.ContextInputRecord[] payloadInputRecords;
        private Stratus.RuntimeBuild.BuildInputLeaseSet payloadInputs;
        private Stratus.RuntimeBuild.BuildInputSnapshot[] payloadSnapshots;
        private Stratus.RuntimeBuild.BuildInputSnapshot[] terminalPayloadSnapshots;
        private int terminalInputCaptureAttempted, terminalInputCaptureReturned, terminalInputCaptureSucceeded;
        private int terminalInputVerified, terminalInputReleaseAttempted, terminalInputReleaseReturned, terminalInputReleaseSucceeded;
        private string terminalInputCommitment;

        private void AssertInputAdmission() {
            AssertOriginal();
            AssertOwnIdentity(); AssertDeadline(); AssertClean();
            Require(contextCapture != null && contextCapture.IsComplete);
            Require(Interlocked.CompareExchange(ref helloReceived, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref responseWritten, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref guardianAckReceived, 0, 0) == 1);
            contextCapture.AssertHealthy();
            AssertOwnIdentity(); AssertDeadline(); AssertClean();
        }

        private void AssertRetainedInputs(Stratus.RuntimeBuild.ContextCapture originalCapture,
                Stratus.RuntimeBuild.ContextMetadata originalMetadata,
                Stratus.RuntimeBuild.ContextInputRecord[] originalRecords,
                Stratus.RuntimeBuild.BuildInputLeaseSet originalInputs) {
            AssertInputAdmission();
            Require(inputStartAttempted && originalCapture != null && originalMetadata != null &&
                originalRecords != null && originalInputs != null);
            Require(Object.ReferenceEquals(contextCapture, originalCapture) &&
                Object.ReferenceEquals(payloadContextCapture, originalCapture) &&
                Object.ReferenceEquals(payloadMetadata, originalMetadata) &&
                Object.ReferenceEquals(payloadInputRecords, originalRecords) &&
                Object.ReferenceEquals(payloadInputs, originalInputs) &&
                Object.ReferenceEquals(originalCapture.AdmittedMetadata, originalMetadata));
            AssertInputAdmission();
        }

        // A private ACK is only an observation at its sending/receiving boundaries.
        // It does not prove sustained, native-qualified W supervision or authorize
        // payload access. This independent blocker must remain unconditional until
        // an explicitly scoped future change qualifies supervision/lifetime and
        // the exact preload, environment and writer boundaries. Installing a
        // ContextTrust descriptor alone must never silently enable payload Open.
        private static void DemandQualifiedPayloadOpen() {
            throw new InvalidOperationException("Payload Open requires separately qualified P/W supervision and execution scope.");
        }

        // No caller arguments, Expected projection, supplied specifications,
        // approval Boolean or alternate owner can enter the real P phase.
        private void StartInputs() {
            try {
                AssertInputAdmission();
                Require(!inputStartAttempted && inputWorker == null && payloadContextCapture == null &&
                    payloadMetadata == null && payloadInputRecords == null && payloadInputs == null &&
                    payloadSnapshots == null && Interlocked.CompareExchange(ref inputVerified, 0, 0) == 0);
                inputStartAttempted = true;
                payloadContextCapture = contextCapture;
                payloadMetadata = payloadContextCapture.AdmittedMetadata;
                Require(Object.ReferenceEquals(contextCapture, payloadContextCapture) &&
                    Object.ReferenceEquals(contextCapture.AdmittedMetadata, payloadMetadata));
                payloadInputRecords = payloadMetadata.CopyInputs();
                Require(payloadInputRecords != null && payloadInputRecords.Length >= 10 && payloadInputRecords.Length <= 104);
                Stratus.RuntimeBuild.BuildInputSpec[] specifications =
                    new Stratus.RuntimeBuild.BuildInputSpec[payloadInputRecords.Length];
                long total = 0, declared = 0;
                for (int i = 0; i < payloadInputRecords.Length; ++i) {
                    Stratus.RuntimeBuild.ContextInputRecord record = payloadInputRecords[i];
                    long maximum = i < 2 ? 262144 : (i == 2 ? 4194304 : (i == 3 ? 2097152 :
                        (i == 6 || i == 7 ? 16777216 : 134217728)));
                    Require(record != null && record.MaximumBytes == maximum && record.Bytes >= 1 && record.Bytes <= maximum);
                    total = checked(total + record.Bytes); Require(total <= 275251200);
                    if (i >= 4) { declared = checked(declared + record.Bytes); Require(declared <= 268435456); }
                    specifications[i] = new Stratus.RuntimeBuild.BuildInputSpec(record.Path, record.Sha256, record.Bytes, maximum);
                }
                AssertInputAdmission();
                Require(Object.ReferenceEquals(contextCapture, payloadContextCapture) &&
                    Object.ReferenceEquals(contextCapture.AdmittedMetadata, payloadMetadata));
                // Managed-only Prepare returns before any input file/native work.
                // Retain this exact owner in P before creating or starting a worker.
                payloadInputs = Stratus.RuntimeBuild.BuildInputLeaseSet.Prepare(specifications);
                Stratus.RuntimeBuild.ContextCapture originalCapture = payloadContextCapture;
                Stratus.RuntimeBuild.ContextMetadata originalMetadata = payloadMetadata;
                Stratus.RuntimeBuild.ContextInputRecord[] originalRecords = payloadInputRecords;
                Stratus.RuntimeBuild.BuildInputLeaseSet originalInputs = payloadInputs;
                AssertRetainedInputs(originalCapture, originalMetadata, originalRecords, originalInputs);
                inputWorker = new Thread(delegate() {
                    RunInputs(originalCapture, originalMetadata, originalRecords, originalInputs);
                });
                inputWorker.IsBackground = true;
                AssertRetainedInputs(originalCapture, originalMetadata, originalRecords, originalInputs);
                inputWorker.Start();
                AssertRetainedInputs(originalCapture, originalMetadata, originalRecords, originalInputs);
            } catch (Exception) { RecordFailure(); throw; }
        }

        private void RunInputs(Stratus.RuntimeBuild.ContextCapture originalCapture,
                Stratus.RuntimeBuild.ContextMetadata originalMetadata,
                Stratus.RuntimeBuild.ContextInputRecord[] originalRecords,
                Stratus.RuntimeBuild.BuildInputLeaseSet originalInputs) {
            try {
                AssertRetainedInputs(originalCapture, originalMetadata, originalRecords, originalInputs);
                DemandQualifiedPayloadOpen();
                originalInputs.Open();
                AssertRetainedInputs(originalCapture, originalMetadata, originalRecords, originalInputs);
                // CaptureSnapshots rereads each original held file, before any
                // compiler registration. These are not caller-provided snapshots.
                payloadSnapshots = originalInputs.CaptureSnapshots();
                Stratus.RuntimeBuild.BuildInputSnapshot[] originalSnapshots = payloadSnapshots;
                AssertRetainedInputs(originalCapture, originalMetadata, originalRecords, originalInputs);
                VerifyInputSnapshots(originalCapture, originalMetadata, originalRecords, originalInputs, originalSnapshots);
                originalInputs.AssertHeld();
                AssertRetainedInputs(originalCapture, originalMetadata, originalRecords, originalInputs);
                Require(Object.ReferenceEquals(payloadSnapshots, originalSnapshots));
                Interlocked.Exchange(ref inputVerified, 1);
            } catch (Exception) { RecordFailure(); }
            finally { Interlocked.Exchange(ref inputWorkerSettled, 1); }
            // Retain original owner and snapshots even on partial/unknown failure.
            // No owner cleanup, retry, competing operation, thread join, process
            // lookup or W-exit wait occurs here. Main remains able to fail under
            // its original deadline; physical P death retires its handle table.
            // Future compiler cleanup must precede release of this exact owner;
            // this phase has no compiler borrower or successful build terminal.
        }

        private void VerifyInputSnapshots(Stratus.RuntimeBuild.ContextCapture originalCapture,
                Stratus.RuntimeBuild.ContextMetadata originalMetadata,
                Stratus.RuntimeBuild.ContextInputRecord[] originalRecords,
                Stratus.RuntimeBuild.BuildInputLeaseSet originalInputs,
                Stratus.RuntimeBuild.BuildInputSnapshot[] originalSnapshots) {
            Require(originalSnapshots != null && originalSnapshots.Length == originalRecords.Length &&
                Object.ReferenceEquals(payloadSnapshots, originalSnapshots));
            for (int i = 0; i < originalRecords.Length; ++i) {
                AssertRetainedInputs(originalCapture, originalMetadata, originalRecords, originalInputs);
                Stratus.RuntimeBuild.ContextInputRecord expected = originalRecords[i];
                Stratus.RuntimeBuild.BuildInputSnapshot snapshot = originalSnapshots[i];
                Require(snapshot != null && String.Equals(snapshot.Path, expected.Path, StringComparison.Ordinal));
                byte[] actualBytes = snapshot.Bytes;
                Require(actualBytes != null && actualBytes.LongLength == expected.Bytes &&
                    actualBytes.LongLength <= expected.MaximumBytes && snapshot.Length == actualBytes.LongLength);
                // Snapshot.Sha256 is copied from its specification; it is not an
                // independent digest. Hash these actual defensive-copy bytes.
                Require(String.Equals(InputBytesHash(actualBytes), expected.Sha256, StringComparison.Ordinal));
                if (i == 0) RequireInputBytesEqual(actualBytes, originalMetadata.CopyProfileBytes());
                else if (i == 1) RequireInputBytesEqual(actualBytes, originalMetadata.CopyPlanBytes());
                else if (i == 2) RequireInputBytesEqual(actualBytes, originalMetadata.CopyManifestBytes());
                // Exact first-three correspondence preserves the original
                // compiled parser's canonical/schema/cross-relation checks.
                // Every remaining payload is still checked against its own
                // manifest/profile-derived path, length, digest and role bound.
                AssertRetainedInputs(originalCapture, originalMetadata, originalRecords, originalInputs);
            }
        }

        private static void RequireInputBytesEqual(byte[] actual, byte[] expected) {
            Require(actual != null && expected != null && actual.Length == expected.Length);
            for (int i = 0; i < actual.Length; ++i) Require(actual[i] == expected[i]);
        }
        private void AssertTerminalInputOwner(ProducerCompilerOwner owner, ProducerCompilerClosure closure) {
            AssertCompilerTerminalOwnerManaged(owner); owner.AssertTerminalInputRegistrationReleased(closure);
            Require(inputWorker != null && Interlocked.CompareExchange(ref inputVerified, 0, 0) == 1 &&
                payloadInputRecords.Length == payloadSnapshots.Length &&
                Object.ReferenceEquals(contextCapture.AdmittedMetadata, payloadMetadata));
        }
        internal void RecaptureProducerTerminalInputs(ProducerCompilerOwner owner, ProducerCompilerClosure closure) {
            try {
                AssertTerminalInputOwner(owner, closure);
                // inputVerified is published just before the input worker's final
                // return. Observe both settlement and actual termination, under
                // the same invocation deadline, before recapturing its streams.
                while (Interlocked.CompareExchange(ref inputWorkerSettled, 0, 0) != 1 || !inputWorker.Join(0)) {
                    AssertTerminalInputOwner(owner, closure); Thread.Sleep(10);
                }
                AssertTerminalInputOwner(owner, closure);
                Require(Interlocked.CompareExchange(ref terminalInputCaptureAttempted, 1, 0) == 0 &&
                    terminalPayloadSnapshots == null && Interlocked.CompareExchange(ref terminalInputReleaseAttempted, 0, 0) == 0);
                try {
                    // No input path is reopened and no original snapshot reference
                    // is overwritten. CaptureSnapshots rereads the retained files.
                    BuildInputSnapshot[] captured = payloadInputs.CaptureSnapshots();
                    Require(captured != null && Interlocked.CompareExchange(ref terminalPayloadSnapshots, captured, null) == null);
                    Interlocked.Exchange(ref terminalInputCaptureSucceeded, 1);
                    Interlocked.Exchange(ref terminalInputCaptureReturned, 1);
                } catch (Exception) { Interlocked.Exchange(ref terminalInputCaptureReturned, 1); throw; }
                AssertTerminalInputOwner(owner, closure);
                BuildInputSnapshot[] finalSnapshots = terminalPayloadSnapshots;
                Require(finalSnapshots.Length == payloadSnapshots.Length && !Object.ReferenceEquals(finalSnapshots, payloadSnapshots));
                for (int i = 0; i < finalSnapshots.Length; ++i) {
                    AssertTerminalInputOwner(owner, closure);
                    BuildInputSnapshot before = payloadSnapshots[i], after = finalSnapshots[i];
                    ContextInputRecord expected = payloadInputRecords[i];
                    Require(before != null && after != null && !Object.ReferenceEquals(before, after) &&
                        String.Equals(after.Path, before.Path, StringComparison.Ordinal) &&
                        String.Equals(after.Path, expected.Path, StringComparison.Ordinal) &&
                        after.Length == before.Length && after.Length == expected.Bytes && after.Length <= expected.MaximumBytes &&
                        after.VolumeSerialNumber == before.VolumeSerialNumber && after.FileIndex == before.FileIndex &&
                        after.LinkCount == before.LinkCount && after.Attributes == before.Attributes);
                    byte[] actual = after.Bytes;
                    Require(actual != null && actual.LongLength == expected.Bytes && actual.LongLength <= expected.MaximumBytes);
                    // Neither copied Snapshot.Sha256 nor a caller-supplied digest
                    // establishes recapture. Hash the actual newly captured bytes.
                    Require(String.Equals(InputBytesHash(actual), expected.Sha256, StringComparison.Ordinal));
                    RequireInputBytesEqual(actual, before.Bytes);
                    if (i == 0) RequireInputBytesEqual(actual, payloadMetadata.CopyProfileBytes());
                    else if (i == 1) RequireInputBytesEqual(actual, payloadMetadata.CopyPlanBytes());
                    else if (i == 2) RequireInputBytesEqual(actual, payloadMetadata.CopyManifestBytes());
                    AssertTerminalInputOwner(owner, closure);
                }
                payloadInputs.AssertHeld(); AssertTerminalInputOwner(owner, closure);
                string commitment = CreateTerminalInputCommitment(owner, closure, finalSnapshots);
                Require(Interlocked.CompareExchange(ref terminalInputCommitment, commitment, null) == null);
                Require(Interlocked.CompareExchange(ref terminalInputVerified, 1, 0) == 0);
            } catch (Exception) { RecordFailure(); throw; }
        }
        private string CreateTerminalInputCommitment(ProducerCompilerOwner owner, ProducerCompilerClosure closure,
                BuildInputSnapshot[] snapshots) {
            Require(Object.ReferenceEquals(snapshots, terminalPayloadSnapshots));
            // Fixed little-endian integers; raw 32-byte SHA256, not hex text;
            // strict UTF8 paths have an explicit byte count and no terminator.
            using (MemoryStream encoded = new MemoryStream()) {
                using (BinaryWriter writer = new BinaryWriter(encoded, new UTF8Encoding(false, true))) {
                    writer.Write(Encoding.ASCII.GetBytes("STRATUS_FINAL_INPUTS_V1\n")); writer.Write(snapshots.Length);
                    for (int i = 0; i < snapshots.Length; ++i) {
                        AssertTerminalInputOwner(owner, closure);
                        BuildInputSnapshot snapshot = snapshots[i]; byte[] actual = snapshot.Bytes;
                        byte[] path = new UTF8Encoding(false, true).GetBytes(snapshot.Path);
                        writer.Write(i); writer.Write(path.Length); writer.Write(path); writer.Write(actual.LongLength);
                        using (SHA256 hash = SHA256.Create()) { writer.Write(hash.ComputeHash(actual)); }
                        writer.Write(snapshot.VolumeSerialNumber); writer.Write(snapshot.FileIndex);
                        writer.Write(snapshot.LinkCount); writer.Write(snapshot.Attributes);
                    }
                    writer.Flush(); AssertTerminalInputOwner(owner, closure);
                    return InputBytesHash(encoded.ToArray());
                }
            }
        }
        internal void AssertTerminalArtifactsDistinctFromInputs(ProducerCompilerOwner owner,
                ProducerCompilerClosure closure, TerminalArtifactCommitments commitments) {
            AssertTerminalInputOwner(owner, closure);
            Require(commitments != null && commitments.Output != null && commitments.Stdout != null &&
                terminalPayloadSnapshots != null && terminalInputCommitment != null &&
                Interlocked.CompareExchange(ref terminalInputVerified, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref terminalInputReleaseAttempted, 0, 0) == 0);
            payloadInputs.AssertHeld();
            for (int i = 0; i < terminalPayloadSnapshots.Length; ++i) {
                BuildInputSnapshot snapshot = terminalPayloadSnapshots[i];
                Require(!(snapshot.VolumeSerialNumber == commitments.Output.Volume && snapshot.FileIndex == commitments.Output.FileIndex) &&
                    !(snapshot.VolumeSerialNumber == commitments.Stdout.Volume && snapshot.FileIndex == commitments.Stdout.FileIndex));
            }
            payloadInputs.AssertHeld(); AssertTerminalInputOwner(owner, closure);
        }
        internal void AssertTerminalPayloadReleaseAdmission(BuildInputLeaseSet inputs,
                ProducerCompilerOwner owner, ProducerCompilerClosure closure) {
            // Called inside the lease-set operation: managed-only backlinks and
            // retained outcomes, never a held-input walk or lifecycle reentry.
            AssertCompilerTerminalOwnerManaged(owner); owner.AssertTerminalWorkerManaged(closure);
            Require(Object.ReferenceEquals(payloadInputs, inputs) && terminalPayloadSnapshots != null &&
                Interlocked.CompareExchange(ref inputWorkerSettled, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref terminalInputCaptureAttempted, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref terminalInputCaptureReturned, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref terminalInputCaptureSucceeded, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref terminalInputVerified, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref terminalInputReleaseAttempted, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref terminalInputReleaseReturned, 0, 0) == 0);
        }
        internal void ReleaseProducerTerminalInputs(ProducerCompilerOwner owner, ProducerCompilerClosure closure) {
            try {
                AssertTerminalInputOwner(owner, closure);
                Require(Interlocked.CompareExchange(ref terminalInputVerified, 0, 0) == 1 &&
                    Interlocked.CompareExchange(ref terminalInputReleaseAttempted, 1, 0) == 0);
                try {
                    payloadInputs.ReleaseForProducerTerminal(this, owner, closure);
                    Interlocked.Exchange(ref terminalInputReleaseSucceeded, 1);
                    Interlocked.Exchange(ref terminalInputReleaseReturned, 1);
                } catch (Exception) { Interlocked.Exchange(ref terminalInputReleaseReturned, 1); throw; }
                AssertProducerTerminalInputsReleased(owner, closure);
            } catch (Exception) { RecordFailure(); throw; }
        }
        internal void AssertProducerTerminalInputsReleased(ProducerCompilerOwner owner, ProducerCompilerClosure closure) {
            AssertCompilerTerminalOwnerManaged(owner); owner.AssertTerminalObservation(closure);
            Require(terminalPayloadSnapshots != null && Interlocked.CompareExchange(ref terminalInputVerified, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref terminalInputReleaseAttempted, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref terminalInputReleaseReturned, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref terminalInputReleaseSucceeded, 0, 0) == 1);
            payloadInputs.AssertProducerTerminalReleased(this, owner, closure);
        }
        internal string CopyProducerTerminalInputCommitment(ProducerCompilerOwner owner, ProducerCompilerClosure closure) {
            AssertProducerTerminalInputsReleased(owner, closure);
            string commitment = Interlocked.CompareExchange(ref terminalInputCommitment, null, null);
            Require(commitment != null); return commitment;
        }
        private static string InputBytesHash(byte[] bytes) {
            // This scope releases only the BCL digest, never an input owner,
            // snapshot, inherited pipe, process or native protocol capability.
            using (SHA256 hash = SHA256.Create()) {
                return BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
            }
        }
    }
}
