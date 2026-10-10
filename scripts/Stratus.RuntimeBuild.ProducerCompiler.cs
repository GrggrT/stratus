// UNQUALIFIED real P compiler worker, sharing the original invocation. Actual
// birth/root handoff/continuation are wired but separately qualification-blocked.
using System;
using System.Collections.Generic;
using System.Threading;

namespace Stratus.RuntimeBuild {
    internal sealed partial class ProducerInvocation {
        private bool compilerStartAttempted;
        private ProducerCompilerOwner compilerOwner;

        private void StartCompiler() {
            try {
                AssertInputAdmission();
                Require(!compilerStartAttempted && compilerOwner == null &&
                    Interlocked.CompareExchange(ref inputVerified, 0, 0) == 1);
                compilerStartAttempted = true;
                ProducerCompilerOwner prepared = ProducerCompilerOwner.Prepare(this);
                Require(Interlocked.CompareExchange(ref compilerOwner, prepared, null) == null);
                prepared.Start();
                AssertInputAdmission();
            } catch (Exception) { RecordFailure(); throw; }
        }
        internal void AssertCompilerPreparation() {
            AssertOriginal();
            Require(compilerStartAttempted && compilerOwner == null);
            AssertVerifiedCompilerInputs();
        }
        private void AssertVerifiedCompilerInputs() {
            AssertOriginal();
            Require(started && inputStartAttempted && Interlocked.CompareExchange(ref inputVerified, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref helloReceived, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref responseWritten, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref guardianAckReceived, 0, 0) == 1);
            Require(contextCapture != null && contextCapture.IsComplete &&
                Object.ReferenceEquals(contextCapture, payloadContextCapture) &&
                Object.ReferenceEquals(contextCapture.AdmittedMetadata, payloadMetadata) &&
                payloadInputRecords != null && payloadInputs != null && payloadSnapshots != null &&
                payloadInputRecords.Length == payloadSnapshots.Length);
            contextCapture.AssertHealthy(); AssertOriginal();
        }
        internal void AssertCompilerOwner(ProducerCompilerOwner owner) {
            AssertOriginal();
            Require(owner != null && compilerStartAttempted &&
                Object.ReferenceEquals(Interlocked.CompareExchange(ref compilerOwner, null, null), owner));
            AssertVerifiedCompilerInputs();
        }
        internal ProducerCompilerPreparation DeriveCompilerPreparation(ProducerCompilerOwner owner) {
            AssertCompilerOwner(owner); owner.AssertFactoryAdmission();
            // These are the actual worker-verified private fields. No caller
            // snapshot, inventory, Expected or compiler path enters this API.
            ContextInputRecord compiler = payloadMetadata.CopyCompilerRecord();
            ContextInputRecord inventoryCompiler = payloadInputRecords[5];
            Require(compiler.Path == inventoryCompiler.Path && compiler.Sha256 == inventoryCompiler.Sha256 &&
                compiler.Bytes == inventoryCompiler.Bytes && compiler.MaximumBytes == inventoryCompiler.MaximumBytes);
            Dictionary<string, string> environment = ProducerCompilerScope.DeriveEnvironment(payloadMetadata);
            ProducerCompilerPreparation prepared = new ProducerCompilerPreparation(payloadMetadata, payloadInputs,
                compiler, payloadMetadata.CopyCompilerArguments(), environment);
            AssertCompilerOwner(owner); owner.AssertFactoryAdmission();
            return prepared;
        }
        internal void AssertCompilerClock(ProducerCompilerOwner owner) {
            AssertCompilerOwner(owner); AssertInputAdmission(); AssertCompilerOwner(owner);
        }
        internal long CompilerElapsedMilliseconds(ProducerCompilerOwner owner) {
            AssertCompilerClock(owner);
            lock (clockGate) {
                AssertOriginal();
                Require(previousTick >= startedTick && previousTick < deadlineTick);
                return checked((long)(previousTick - startedTick));
            }
        }
    }

    // A private data projection within the typed owner's factory, not an approval
    // object accepted from a caller or a replacement invocation/clock.
    internal sealed class ProducerCompilerPreparation {
        internal readonly ContextMetadata Metadata;
        internal readonly BuildInputLeaseSet Inputs;
        internal readonly ContextInputRecord Compiler;
        internal readonly string[] Arguments;
        internal readonly Dictionary<string, string> Environment;
        internal ProducerCompilerPreparation(ContextMetadata metadata, BuildInputLeaseSet inputs,
                ContextInputRecord compiler, string[] arguments, Dictionary<string, string> environment) {
            Metadata = metadata; Inputs = inputs; Compiler = compiler; Arguments = arguments; Environment = environment;
        }
    }

    internal sealed partial class ProducerCompilerOwner {
        private readonly ProducerInvocation invocation;
        private Thread worker;
        private CompilerCommand command;
        private ProducerCompilerPreparation preparation;
        private int startAttempted, threadStartAttempted, threadStartReturned, failed, phase, jobPrepared;
        private int registrationAttempted, registrationReturned, registrationSucceeded;
        private ProducerCompilerOwner(ProducerInvocation invocation) { this.invocation = invocation; }
        private static void Require(bool value) {
            if (!value) throw new InvalidOperationException("Original P compiler ownership/phase differs.");
        }
        internal static ProducerCompilerOwner Prepare(ProducerInvocation invocation) {
            Require(invocation != null); invocation.AssertCompilerPreparation();
            return new ProducerCompilerOwner(invocation);
        }
        internal void Start() {
            try {
                invocation.AssertCompilerOwner(this);
                Require(Interlocked.CompareExchange(ref startAttempted, 1, 0) == 0 && worker == null);
                worker = new Thread(Run); worker.IsBackground = true;
                invocation.AssertCompilerOwner(this);
                Require(Interlocked.CompareExchange(ref threadStartAttempted, 1, 0) == 0);
                worker.Start();
                Interlocked.Exchange(ref threadStartReturned, 1);
                invocation.AssertCompilerOwner(this);
            } catch (Exception) { RecordFailure(); throw; }
        }
        private void RecordFailure() { Interlocked.Exchange(ref failed, 1); invocation.RecordFailure(); }
        private void AssertWorker(int expectedPhase) {
            Require(Interlocked.CompareExchange(ref failed, 0, 0) == 0 &&
                Interlocked.CompareExchange(ref startAttempted, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref threadStartAttempted, 0, 0) == 1 &&
                worker != null && Object.ReferenceEquals(Thread.CurrentThread, worker) &&
                Thread.CurrentThread.ManagedThreadId == worker.ManagedThreadId &&
                Interlocked.CompareExchange(ref phase, 0, 0) == expectedPhase);
            invocation.AssertCompilerOwner(this);
        }
        internal void AssertFactoryAdmission() { AssertWorker(1); }
        internal ProducerCompilerPreparation BorrowPreparation() {
            AssertFactoryAdmission(); Require(preparation == null && command == null);
            preparation = invocation.DeriveCompilerPreparation(this);
            AssertFactoryAdmission(); return preparation;
        }
        internal void RetainCommandBeforeRegistration(CompilerCommand original) {
            AssertFactoryAdmission(); Require(original != null && preparation != null && registrationAttempted == 0);
            Require(Interlocked.CompareExchange(ref command, original, null) == null);
            // No fallible tail. Recovery can find the exact object even when
            // registration throws or is interrupted after mutating the lease set.
        }
        internal void RegisterOriginalCommand(CompilerCommand original) {
            AssertFactoryAdmission();
            Require(Object.ReferenceEquals(command, original) && original != null && preparation != null);
            Require(Interlocked.CompareExchange(ref registrationAttempted, 1, 0) == 0);
            try {
                preparation.Inputs.RegisterCommand(original);
                Interlocked.Exchange(ref registrationSucceeded, 1);
                Interlocked.Exchange(ref registrationReturned, 1);
            } catch (Exception) {
                // A throwing registration is not proof that no borrower was
                // installed. Keep the original command/input owner; no retry.
                Interlocked.Exchange(ref registrationReturned, 1); RecordFailure(); throw;
            }
        }
        internal void AssertCommandAdmission(CompilerCommand original) {
            AssertWorker(2);
            Require(original != null && Object.ReferenceEquals(command, original) && preparation != null &&
                Interlocked.CompareExchange(ref registrationAttempted, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref registrationReturned, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref registrationSucceeded, 0, 0) == 1);
            invocation.AssertCompilerClock(this);
        }
        internal long ElapsedMilliseconds(CompilerCommand original) {
            AssertCommandAdmission(original); return invocation.CompilerElapsedMilliseconds(this);
        }
        // Historical recovery reference: deliberately remains readable after
        // failure, expiry or unknown registration. It grants no success admission.
        internal CompilerCommand GetOriginalCommandForRecovery() {
            return Interlocked.CompareExchange(ref command, null, null);
        }
        internal void AssertKnownRegistrationForRelease(CompilerCommand original) {
            Require(original != null && Object.ReferenceEquals(command, original) &&
                Interlocked.CompareExchange(ref registrationAttempted, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref registrationReturned, 0, 0) == 1 &&
                Interlocked.CompareExchange(ref registrationSucceeded, 0, 0) == 1);
        }
        private static void DemandQualifiedCompilerJobPreparation() {
            throw new InvalidOperationException("Compiler Job preparation needs separately qualified P/W, environment, writer and nested-Job scope.");
        }
        private void Run() {
            try {
                Require(Interlocked.CompareExchange(ref phase, 1, 0) == 0);
                AssertFactoryAdmission(); invocation.AssertCompilerClock(this);
                CompilerCommand prepared = CompilerCommand.PrepareProducerCompiler(this);
                Require(Object.ReferenceEquals(command, prepared));
                Require(Interlocked.CompareExchange(ref phase, 2, 1) == 1);
                AssertCommandAdmission(prepared);
                DemandQualifiedCompilerJobPreparation();
                prepared.PrepareProducerCompilerJob(this);
                AssertCommandAdmission(prepared); Interlocked.Exchange(ref jobPrepared, 1);
                prepared.PublishOriginalCompilerJobOffer(this);
                WaitForOriginalCompilerJobAck(prepared);
                AssertCommandAdmission(prepared);
                PrepareTerminalArtifactsBeforeBirth(prepared);
                prepared.StartProducerCompiler(this);
                AssertCommandAdmission(prepared);
                prepared.PublishOriginalCompilerRootOffer(this);
                WaitForOriginalCompilerRootAck(prepared);
                AssertContinuationAdmission(prepared);
                prepared.ResumeProducerCompiler(this);
                // Phase 2 remains valid across birth, writer post-Flush, ACK and
                // continuation. A short worker return must not invalidate the
                // reader/writer while an original publication is still pending.
                while (prepared.PollResult() == null) {
                    AssertContinuationAdmission(prepared); Thread.Sleep(10);
                }
                AssertContinuationAdmission(prepared);
                DemandQualifiedCompilerTerminal();
                PrepareLocalTerminal(prepared);
                // Local preparation is not wire completion, build acceptance or
                // permission for Main to return success. No W-exit wait occurs.
            } catch (Exception) { RecordFailure(); }
            finally {
                if (Interlocked.CompareExchange(ref failed, 0, 0) != 0) Interlocked.Exchange(ref phase, 0);
                Interlocked.Exchange(ref workerSettled, 1);
            }
            // Successful local preparation remains immutable phase 3. Offered
            // Job/root source slots remain pinned even after payload release.
        }
    }
}
