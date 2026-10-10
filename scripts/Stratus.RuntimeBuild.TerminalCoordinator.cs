// UNQUALIFIED retained L coordinator. This version has no admitted context,
// preload or writer scope and cannot execute a build. A successful capture is
// still not build acceptance, import, publication or production permission.
// Pre-P preload IO and post-W output IO require independent external lifetime
// enforcement; an observed deadline is not an OS-call interruption mechanism.
using System;
using System.Threading;

namespace Stratus.RuntimeBuild {
    public sealed class BuildTerminalCoordinator {
        public const string Abi = "stratus-runtime-build-terminal-coordinator-v3";
        private enum Phase { Prepared, Starting, AwaitingReady, AwaitingSupervisorAck, Running, NotifyingTerminal, Capturing, Captured, Containing, Contained, Releasing, Released }
        private readonly byte[] context;
        private readonly ContextMetadata metadata;
        private readonly LauncherControlClient supervisor;
        private StartupPreloadOwner preload;
        private DisposableProducerLaunch producer;
        private GuardianLaunch guardian;
        private BuildOutputOwner output;
        private BuildTerminalClosure terminal;
        private BuildOutputCapture capture;
        private Phase phase;
        private int operation, operationThread, failed;
        private bool startAttempted, outputPreparationActive, contextAdmitted;
        private bool writerPreparationAttempted, writerPreparationReturned, writerPrepared;
        private bool preloadPreparationAttempted, preloadPreparationReturned;
        private bool producerPreparationAttempted, producerPreparationReturned, producerStartAttempted;
        private bool guardianPreparationAttempted, guardianPreparationReturned, guardianStartAttempted;
        private bool continuationAttempted, continuationReturned, captureAttempted, captureReturned;
        private bool terminalPublicationAttempted, terminalPublicationReturned;
        private bool guardianReleaseAttempted, guardianReleaseReturned;
        private bool producerReleaseAttempted, producerReleaseReturned;
        private bool preloadReleaseAttempted, preloadReleaseReturned;

        private BuildTerminalCoordinator(byte[] rawContext, LauncherControlClient originalSupervisor) {
            context = (byte[])rawContext.Clone();
            metadata = ContextMetadata.ParseAndDerive(context);
            supervisor = originalSupervisor;
            phase = Phase.Prepared;
        }
        // Managed-only: caller retains this object before Start. Input metadata
        // is untrusted data, never a selector argument or permission to execute.
        public static BuildTerminalCoordinator Prepare(byte[] rawContext) {
            if (rawContext == null || rawContext.Length < 1 || rawContext.Length > ContextCapture.MaximumBytes)
                throw new ArgumentException("One bounded original build context is required.");
            return new BuildTerminalCoordinator(rawContext, null);
        }
        internal static BuildTerminalCoordinator PrepareSupervised(byte[] rawContext, LauncherControlClient original) {
            Require(original != null); original.AssertPreparation(rawContext);
            return new BuildTerminalCoordinator(rawContext, original);
        }
        private static void Require(bool value) {
            if (!value) throw new InvalidOperationException("Original terminal coordinator ownership or phase differs.");
        }
        private static void DemandQualifiedCoordinatorExecution() {
            throw new InvalidOperationException("The complete L/P/W, writer isolation and external L-lifetime scope require independent qualification.");
        }
        private void RecordFailure() { Interlocked.Exchange(ref failed, 1); }
        private void Enter() {
            if (Interlocked.CompareExchange(ref operation, 1, 0) != 0) {
                RecordFailure(); throw new InvalidOperationException("Concurrent original build coordinator operations are forbidden.");
            }
            operationThread = Thread.CurrentThread.ManagedThreadId;
        }
        private void Leave() { operationThread = 0; Interlocked.Exchange(ref operation, 0); }
        private void AssertOperation() {
            Require(Interlocked.CompareExchange(ref operation, 0, 0) == 1 &&
                operationThread == Thread.CurrentThread.ManagedThreadId);
        }
        private void Admission() {
            AssertOperation();
            Require(Interlocked.CompareExchange(ref failed, 0, 0) == 0 &&
                phase != Phase.Containing && phase != Phase.Contained && phase != Phase.Releasing && phase != Phase.Released);
        }
        public void Start() {
            Enter();
            try {
                Admission(); Require(phase == Phase.Prepared && !startAttempted);
                startAttempted = true; phase = Phase.Starting;
                Require(supervisor != null);
                // Retain the output lifetime owner even if later independent
                // admission fails. No late post-terminal scope adoption exists.
                outputPreparationActive = true;
                try { output = BuildOutputOwner.Prepare(this); }
                finally { outputPreparationActive = false; }
                ContextTrust.DemandAdmission(metadata); contextAdmitted = true;
                DemandQualifiedCoordinatorExecution();
                writerPreparationAttempted = true;
                output.PrepareWriterScopeBeforeProducerBirth();
                writerPreparationReturned = true; writerPrepared = true;
                output.AssertWriterScopeHeldBeforeProducerBirth();
                preloadPreparationAttempted = true;
                preload = StartupPreloadOwner.Prepare(context);
                preloadPreparationReturned = true;
                output.AssertWriterScopeHeldBeforeProducerBirth();
                preload.Open(); Admission();
                producerPreparationAttempted = true;
                producer = preload.PrepareProducer(); producerPreparationReturned = true;
                Require(producer != null && Object.ReferenceEquals(producer, preload.RetainedProducer));
                producer.BindTerminalCoordinator(this);
                output.AssertWriterScopeHeldBeforeProducerBirth();
                producerStartAttempted = true; producer.StartSuspended();
                producer.PrepareGuardianStopCapability(); Admission();
                guardianPreparationAttempted = true;
                guardian = preload.PrepareGuardian(); guardianPreparationReturned = true;
                Require(guardian != null && Object.ReferenceEquals(guardian, preload.RetainedGuardian));
                guardian.BindTerminalCoordinator(this);
                guardianStartAttempted = true; guardian.StartRunning();
                Admission(); phase = Phase.AwaitingReady;
            } catch (Exception) { RecordFailure(); throw; }
            finally { Leave(); }
            // No catch/finally cleanup, retries, fallback scope or process restart.
        }
        public BuildOutputCapture Poll() {
            Enter();
            try {
                Admission(); Require(guardian != null && producer != null && preload != null && writerPrepared);
                if (phase == Phase.Captured) {
                    Require(captureReturned && capture != null);
                    output.AssertHeldCapture(capture); Admission(); return capture;
                }
                if (phase == Phase.AwaitingReady) {
                    Require(!continuationAttempted);
                    if (guardian.PollReady() == null) return null;
                    byte[] offered = guardian.PrepareOriginalSupervisorOffer(this, supervisor.OriginalNonce(this));
                    supervisor.PublishOriginalOffer(this, offered);
                    phase = Phase.AwaitingSupervisorAck;
                }
                if (phase == Phase.AwaitingSupervisorAck) {
                    Require(!continuationAttempted);
                    // The actual original pipe read is externally covered by S;
                    // a public READY observation cannot substitute for its ACK.
                    supervisor.ReceiveOriginalAck(this);
                    continuationAttempted = true;
                    guardian.ContinueProducer(); continuationReturned = true;
                    Admission(); phase = Phase.Running;
                }
                // PollReady is deliberately never called again: valid terminal
                // stdout is no longer the exact READY-only prefix it accepts.
                Require(phase == Phase.Running && continuationAttempted && continuationReturned && !captureAttempted);
                BuildTerminalClosure observed = guardian.PollNormalTerminal();
                if (observed == null) return null;
                // Guardian publishes the first original terminal boundary from
                // its active proof operation, before fallible native retirement.
                // A later return/capture cannot create or reset that origin.
                Require(phase == Phase.Capturing && terminalPublicationAttempted && terminalPublicationReturned &&
                    observed.BelongsTo(guardian) && Object.ReferenceEquals(terminal, observed));
                AssertOutputBoundary(output);
                captureAttempted = true;
                capture = output.CaptureOriginalTerminalOutputs(); captureReturned = true;
                Require(capture != null && capture.IsOwnedBy(this, output)); phase = Phase.Captured;
                supervisor.PublishCaptured(this, capture);
                output.AssertHeldCapture(capture); Admission(); return capture;
            } catch (Exception) { RecordFailure(); throw; }
            finally { Leave(); }
        }
        public byte[] ReadOutput() {
            Enter();
            try {
                Admission(); Require(phase == Phase.Captured && captureReturned && capture != null);
                byte[] bytes = output.CopyOutput(capture); Admission(); return bytes;
            } catch (Exception) { RecordFailure(); throw; }
            finally { Leave(); }
        }
        public byte[] ReadStdout() {
            Enter();
            try {
                Admission(); Require(phase == Phase.Captured && captureReturned && capture != null);
                byte[] bytes = output.CopyStdout(capture); Admission(); return bytes;
            } catch (Exception) { RecordFailure(); throw; }
            finally { Leave(); }
        }
        internal void AssertOutputPreparation() {
            Admission(); Require(phase == Phase.Starting && startAttempted && outputPreparationActive && output == null &&
                !preloadPreparationAttempted && !producerStartAttempted && !guardianStartAttempted);
        }
        internal void AssertSupervisorOperation(LauncherControlClient original) {
            Admission(); Require(original != null && Object.ReferenceEquals(supervisor, original));
        }
        internal bool OwnsPreparedProducer(DisposableProducerLaunch original) {
            AssertOperation();
            return phase == Phase.Starting && Interlocked.CompareExchange(ref failed, 0, 0) == 0 &&
                Object.ReferenceEquals(producer, original) && producerPreparationReturned && !producerStartAttempted;
        }
        internal void PublishOriginalProducerBegin(DisposableProducerLaunch original, ProducerDeadlineSnapshot clock) {
            Admission(); Require(phase == Phase.Starting && producerStartAttempted &&
                Object.ReferenceEquals(producer, original) && supervisor != null && !guardianStartAttempted);
            supervisor.PublishProducerBegin(this, clock);
        }
        internal void AssertOriginalSupervisorOffer(GuardianLaunch original) {
            Admission(); Require(phase == Phase.AwaitingReady && Object.ReferenceEquals(guardian, original) &&
                supervisor != null && !continuationAttempted && !continuationReturned);
        }
        internal void AssertOriginalSupervisorContinuation(GuardianLaunch original) {
            Admission(); Require(phase == Phase.AwaitingSupervisorAck && Object.ReferenceEquals(guardian, original) &&
                supervisor != null && continuationAttempted && !continuationReturned);
            supervisor.AssertOriginalResume(this);
        }
        internal void PublishOriginalNormalTerminal(GuardianLaunch original, BuildTerminalClosure observed) {
            Admission(); Require(phase == Phase.Running && Object.ReferenceEquals(guardian, original) &&
                continuationAttempted && continuationReturned && !terminalPublicationAttempted && terminal == null &&
                !captureAttempted && supervisor != null && observed != null && observed.BelongsTo(original) &&
                observed.Record != null && observed.Record.InputCount == metadata.CopyInputs().Length);
            original.AssertOriginalTerminalPublication(this, observed);
            terminalPublicationAttempted = true; terminal = observed; phase = Phase.NotifyingTerminal;
            supervisor.PublishOriginalTerminal(this, observed);
            terminalPublicationReturned = true; Admission(); phase = Phase.Capturing;
        }
        internal void AssertOriginalTerminalPublication(LauncherControlClient original, BuildTerminalClosure observed) {
            Admission(); Require(phase == Phase.NotifyingTerminal && Object.ReferenceEquals(supervisor, original) &&
                terminalPublicationAttempted && !terminalPublicationReturned && !captureAttempted && guardian != null &&
                observed != null && Object.ReferenceEquals(terminal, observed));
            // Reentrant validation of Guardian's active publication window;
            // never enter its owner again or require post-publication closes.
            guardian.AssertOriginalTerminalPublication(this, observed);
        }
        internal void AssertOutputScopePreparation(BuildOutputOwner original) {
            Admission(); Require(original != null && Object.ReferenceEquals(output, original) && phase == Phase.Starting &&
                contextAdmitted && writerPreparationAttempted && !writerPreparationReturned && !writerPrepared &&
                !preloadPreparationAttempted && preload == null && !producerStartAttempted && !guardianStartAttempted);
            ContextTrust.DemandAdmission(metadata);
        }
        internal void AssertOutputPreBirthBoundary(BuildOutputOwner original) {
            Admission(); Require(original != null && Object.ReferenceEquals(output, original) && phase == Phase.Starting &&
                contextAdmitted && writerPreparationAttempted && writerPreparationReturned && writerPrepared &&
                !producerStartAttempted && !guardianStartAttempted && terminal == null && !captureAttempted);
            ContextTrust.DemandAdmission(metadata);
        }
        internal ContextMetadata BorrowOutputMetadata(BuildOutputOwner original) {
            Admission(); Require(original != null && Object.ReferenceEquals(output, original) &&
                (phase == Phase.Starting || phase == Phase.Capturing || phase == Phase.Captured));
            return metadata;
        }
        internal void AssertOutputAdmission(BuildOutputOwner original) {
            Admission(); Require(original != null && Object.ReferenceEquals(output, original) &&
                (phase == Phase.Capturing || phase == Phase.Captured) && contextAdmitted && writerPrepared &&
                continuationReturned && terminal != null && guardian != null && producer != null && preload != null &&
                terminal.BelongsTo(guardian) && Object.ReferenceEquals(preload.RetainedGuardian, guardian) &&
                Object.ReferenceEquals(preload.RetainedProducer, producer));
        }
        internal BuildTerminalClosure BorrowOutputTerminal(BuildOutputOwner original) {
            AssertOutputAdmission(original); return terminal;
        }
        internal void AssertOutputBoundary(BuildOutputOwner original) {
            AssertOutputAdmission(original); ContextTrust.DemandAdmission(metadata);
            guardian.AssertCoordinatorOutputBoundary(this, terminal, metadata, context.Length);
            AssertOutputAdmission(original);
        }
        internal bool OwnsPreparedGuardian(GuardianLaunch original) {
            AssertOperation();
            return phase == Phase.Starting && Interlocked.CompareExchange(ref failed, 0, 0) == 0 &&
                original != null && Object.ReferenceEquals(guardian, original) && guardianPreparationAttempted &&
                guardianPreparationReturned && !guardianStartAttempted && preload != null &&
                Object.ReferenceEquals(preload.RetainedGuardian, original);
        }
        internal bool OwnsNormalGuardian(GuardianLaunch original, BuildTerminalClosure observed, ContextMetadata originalMetadata) {
            AssertOperation();
            return Interlocked.CompareExchange(ref failed, 0, 0) == 0 &&
                (phase == Phase.Capturing || phase == Phase.Captured) && Object.ReferenceEquals(guardian, original) &&
                observed != null && Object.ReferenceEquals(terminal, observed) && Object.ReferenceEquals(metadata, originalMetadata);
        }
        private void RecoverOriginalWrappers() {
            AssertOperation();
            if (preload == null) {
                Require(!producerStartAttempted && !guardianStartAttempted && producer == null && guardian == null);
                return;
            }
            DisposableProducerLaunch p = preload.RetainedProducer;
            GuardianLaunch w = preload.RetainedGuardian;
            if (producer == null) producer = p; else Require(Object.ReferenceEquals(producer, p));
            if (guardian == null) guardian = w; else Require(Object.ReferenceEquals(guardian, w));
            Require(!producerStartAttempted || producer != null);
            Require(!guardianStartAttempted || guardian != null);
        }
        public bool PollContainment() {
            Enter();
            try {
                Require(phase != Phase.Releasing && phase != Phase.Released);
                if (phase == Phase.Contained) return true;
                RecordFailure(); phase = Phase.Containing;
                RecoverOriginalWrappers();
                // W owns the exact P-first containment sequence and waits for
                // its own natural exit. Never kill W or race W and P cleanup.
                if (guardian != null) { if (!guardian.PollContainment()) return false; }
                else if (producer != null) { if (!producer.PollContainment()) return false; }
                else Require(!producerStartAttempted && !guardianStartAttempted);
                phase = Phase.Contained; return true;
            } catch (Exception) { RecordFailure(); throw; }
            finally { Leave(); }
        }
        public void ReleaseAfterContainment() {
            Enter();
            try {
                Require(phase == Phase.Contained);
                phase = Phase.Releasing;
                // This releases only proven process/preload ownership. Output
                // handles/scope remain quarantined until the original L dies;
                // process cleanup cannot clear unknown output IO or qualify it.
                if (guardian != null) {
                    Require(!guardianReleaseAttempted); guardianReleaseAttempted = true;
                    guardian.Dispose(); guardianReleaseReturned = true;
                }
                if (producer != null) {
                    Require(!producerReleaseAttempted); producerReleaseAttempted = true;
                    producer.Dispose(); producerReleaseReturned = true;
                }
                if (preload != null) {
                    Require(!preloadReleaseAttempted); preloadReleaseAttempted = true;
                    preload.Dispose(); preloadReleaseReturned = true;
                }
                Require((guardian == null || guardianReleaseReturned) && (producer == null || producerReleaseReturned) &&
                    (preload == null || preloadReleaseReturned));
                phase = Phase.Released;
            } catch (Exception) { RecordFailure(); throw; }
            finally { Leave(); }
        }
    }
}
