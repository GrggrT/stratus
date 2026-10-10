// UNQUALIFIED SOURCE: disposable-producer birth/ownership foundation only.
// This uses the separate CompilerCommand's retained suspended creation and Job;
// it does not clone that launcher, start a guardian, or grant a run permission.
// There is deliberately NO public producer Resume, PID/handle adoption or raw
// public stop-capability export. The internal one-use guardian copy is bound to
// an exact retained GuardianLaunch. Its typed first-continuation path remains
// qualification-blocked; full compiler-in-P Job/root ACK/Resume is separate.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Threading;

namespace Stratus.RuntimeBuild {
    public sealed class DisposableProducerLaunch : IDisposable {
        private readonly StartupPreloadOwner preload;
        private readonly BuildInputLeaseSet inputs;
        private readonly ProducerDeadline startupDeadline;
        private readonly ProducerGuardianChannel controlChannel;
        private readonly ProducerContextSection contextSection;
        private readonly string executable, executableHash, directory;
        private readonly string[] arguments;
        private readonly Dictionary<string, string> environment;
        private readonly object failureGate = new object();
        private readonly List<Exception> failures = new List<Exception>();
        private CompilerCommand command;
        private ProducerStopCapability stopCapability;
        private ProducerTransferCapability transferCapability;
        private GuardianLaunch guardian;
        private BuildTerminalCoordinator terminalCoordinator;
        private int operation;
        private bool startAttempted, nativeStartAttempted, startCompleted, capabilityAttempted;
        private bool transferCapabilityAttempted;
        private bool continuationAttempted, continuationActive, continuationCompleted;
        private int continuationThreadId;
        private bool failed, containmentStarted, containmentProven, releaseStarted, disposed;
        private bool normalClosureProven;
        private CompilerResult normalResult;
        private long normalExitFileTime;

        private DisposableProducerLaunch(StartupPreloadOwner preload, BuildInputLeaseSet inputs, string executable, string hash,
                string[] arguments, string directory, Dictionary<string, string> environment,
                byte[] contextBytes, string contextSha256, long contextLength) {
            this.preload = preload; this.inputs = inputs; this.executable = executable; executableHash = hash;
            this.arguments = arguments; this.directory = directory; this.environment = environment;
            startupDeadline = new ProducerDeadline(this);
            controlChannel = new ProducerGuardianChannel(this);
            contextSection = new ProducerContextSection(this, contextBytes, contextSha256, contextLength);
        }
        // Managed-only. The wrapper MUST be retained before StartSuspended assigns
        // the original registered command and invokes its first native operation.
        // Only the original startup owner can prepare and retain this wrapper.
        // No raw public factory can substitute an executable, environment or set.
        internal static DisposableProducerLaunch PreparePreloaded(StartupPreloadOwner preload) {
            if (preload == null) throw new ArgumentNullException("original startup preload owner");
            StartupPreloadOwner.LaunchData selected = preload.BorrowProducerPreparation();
            BuildInputLeaseSet inputs = selected.Inputs;
            string executable = selected.Executable, hash = selected.Sha256, directory = selected.Directory;
            Dictionary<string, string> environment = selected.Environment;
            byte[] contextBytes = selected.ContextBytes;
            string contextSha256 = selected.ContextSha256; long contextLength = selected.ContextLength;
            string[] arguments = new string[] { "--stratus-producer-entry-v6" };
            if (inputs == null || arguments == null || environment == null) throw new ArgumentNullException("producer inputs");
            if (environment.GetType() != typeof(Dictionary<string, string>)) throw new ArgumentException("An ordinary producer environment dictionary is required.");
            BuildInputValidation.Path(executable); BuildInputValidation.Path(directory); BuildInputValidation.Hash(hash);
            if (!executable.EndsWith(".exe", StringComparison.OrdinalIgnoreCase) || arguments.Length < 1 || arguments.Length > 256)
                throw new ArgumentException("A bounded direct producer executable and argument array are required.");
            if (arguments.Length != 1 || arguments[0] != "--stratus-producer-entry-v6")
                throw new ArgumentException("The current producer accepts only its fixed private-control entry ABI.");
            string[] copiedArguments = (string[])arguments.Clone();
            foreach (string argument in copiedArguments) {
                if (argument == null || argument.Length > 32766) throw new ArgumentException("Invalid bounded producer argument.");
            }
            if (environment.Count != 4) throw new ArgumentException("The current bootstrap requires its fixed four-entry environment.");
            Dictionary<string, string> copiedEnvironment = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (KeyValuePair<string, string> entry in environment) copiedEnvironment.Add(entry.Key, entry.Value);
            return new DisposableProducerLaunch(preload, inputs, executable, hash, copiedArguments, directory, copiedEnvironment,
                contextBytes, contextSha256, contextLength);
        }
        private void Record(Exception error) {
            string text = error.GetType().Name + ": " + error.Message;
            if (text.Length > 512) text = text.Substring(0, 512);
            lock (failureGate) { failed = true; if (failures.Count < 8) failures.Add(new InvalidOperationException(text)); }
        }
        private void Enter() {
            if (Interlocked.CompareExchange(ref operation, 1, 0) != 0) {
                Exception error = new InvalidOperationException("Concurrent disposable producer lifecycle operations are forbidden."); Record(error); throw error;
            }
        }
        private void Leave() { Interlocked.Exchange(ref operation, 0); }
        private void RequireAdmission() {
            if (disposed || releaseStarted || containmentStarted) throw new InvalidOperationException("Disposable producer admission is closed.");
            lock (failureGate) { if (failed) throw new AggregateException(failures.ToArray()); }
            preload.AssertProducer(this, inputs);
            if (startAttempted) startupDeadline.AssertActive(this);
        }
        // Called by the exact retained command at its own success boundaries,
        // including immediately before native birth. Managed state/reference
        // checks only: do not recurse into command admission or perform IO here.
        internal void AssertPreloadAdmission(CompilerCommand candidate) {
            if (candidate == null || !Object.ReferenceEquals(command, candidate) ||
                    disposed || releaseStarted || containmentStarted)
                throw new InvalidOperationException("Original producer command/preload admission is closed.");
            lock (failureGate) { if (failed) throw new AggregateException(failures.ToArray()); }
            preload.AssertProducer(this, inputs);
        }
        internal void AssertPreloadReleaseAllowed(StartupPreloadOwner candidate) {
            // The operation observation alone is not a release proof. Disposed
            // is irreversible and follows exact command/context/channel closure.
            if (!Object.ReferenceEquals(preload, candidate) ||
                    Interlocked.CompareExchange(ref operation, 0, 0) != 0 ||
                    !disposed || (!containmentProven && !normalClosureProven) || guardian != null ||
                    (stopCapability != null && !stopCapability.IsClosed) ||
                    (transferCapability != null && !transferCapability.IsClosed))
                throw new InvalidOperationException("Original P closure must precede preload release.");
        }
        public void StartSuspended() {
            Enter();
            try {
                RequireAdmission();
                if (terminalCoordinator == null) throw new InvalidOperationException("Original external S/L producer supervision is required.");
                if (startAttempted) throw new InvalidOperationException("Disposable producer start is single-use.");
                startAttempted = true;
                // This is the shared STARTUP origin, after caller retention and
                // before P's command/native creation. Earlier input.Open is not
                // covered; no full compiler-in-P deadline integration is claimed.
                startupDeadline.Begin(this);
                terminalCoordinator.PublishOriginalProducerBegin(this, startupDeadline.SnapshotForProducer(this));
                // Exact-byte correspondence is not approved-context provenance.
                // Retain and seal the section before either child may inherit it;
                // this does not provide preemptive protection for L's native work.
                contextSection.OpenForProducer(this);
                RequireAdmission();
                controlChannel.OpenForProducer(this);
                RequireAdmission();
                command = CompilerCommand.PrepareOwnedWithDeadline(inputs, executable, executableHash, arguments, directory, environment, startupDeadline, this, controlChannel, contextSection);
                // The assigned original command is retained before any native work,
                // including its separate Job preparation. This is P's outer Job,
                // not a compiler Job receipt from the not-yet-created guardian.
                nativeStartAttempted = true;
                command.PrepareBirthJob();
                RequireAdmission();
                command.StartSuspended();
                RequireAdmission(); startCompleted = true;
            } catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        public void PrepareGuardianStopCapability() {
            Enter();
            try {
                RequireAdmission();
                if (!startCompleted || command == null || capabilityAttempted)
                    throw new InvalidOperationException("One capability preparation requires the original completed suspended producer birth.");
                capabilityAttempted = true;
                stopCapability = new ProducerStopCapability(this, command);
                command.AcquireProducerStopCapability(this, stopCapability);
                RequireAdmission();
            } catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        internal void BindTerminalCoordinator(BuildTerminalCoordinator original) {
            Enter();
            try {
                RequireAdmission();
                if (original == null || terminalCoordinator != null || startAttempted || !original.OwnsPreparedProducer(this))
                    throw new InvalidOperationException("Original retained supervised coordinator must bind before P start.");
                terminalCoordinator = original;
            } catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        // Called by the actual retained W startup path, not a new caller-supplied
        // capability factory. P retains the new object before any native attempt.
        internal ProducerTransferCapability PrepareGuardianTransferCapability(GuardianLaunch candidate, ProducerStopCapability originalStop) {
            Enter();
            try {
                RequireAdmission();
                if (!OwnsGuardian(candidate, originalStop) || command == null || !startCompleted ||
                        transferCapabilityAttempted || transferCapability != null)
                    throw new InvalidOperationException("One separate transfer capability requires the exact original P and registered W.");
                transferCapabilityAttempted = true;
                ProducerTransferCapability prepared = new ProducerTransferCapability(this, command);
                if (Interlocked.CompareExchange(ref transferCapability, prepared, null) != null)
                    throw new InvalidOperationException("Original P transfer capability retention differs.");
                command.AcquireProducerTransferCapability(this, prepared);
                RequireAdmission();
                return prepared;
            } catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        // Only W's original, active same-thread operation may continue this P.
        // This cannot accept a READY observation, caller Boolean, PID or handle.
        internal void ContinueUnderGuardian(GuardianLaunch candidate, ProducerStopCapability capability) {
            Enter();
            try {
                RequireAdmission();
                if (!startCompleted || command == null || continuationAttempted ||
                        !OwnsGuardian(candidate, capability))
                    throw new InvalidOperationException("One first continuation requires the original completed P and registered W.");
                continuationAttempted = true;
                continuationThreadId = Thread.CurrentThread.ManagedThreadId;
                continuationActive = true;
                AssertGuardianContinuation(command, candidate);
                command.ResumeProducer(this, candidate);
                AssertGuardianContinuation(command, candidate);
                continuationCompleted = true;
            } catch (Exception error) { Record(error); throw; }
            finally { continuationActive = false; continuationThreadId = 0; Leave(); }
        }
        // Called while W -> P -> exact command operations remain held. Do not
        // Enter again, call command admission, or hold a lock across W callbacks.
        internal void AssertGuardianContinuation(CompilerCommand candidate, GuardianLaunch originalGuardian) {
            RequireAdmission();
            if (Interlocked.CompareExchange(ref operation, 0, 0) != 1 || !continuationActive ||
                    continuationThreadId != Thread.CurrentThread.ManagedThreadId || !continuationAttempted ||
                    continuationCompleted || candidate == null || !Object.ReferenceEquals(command, candidate) ||
                    !startCompleted || !OwnsGuardian(originalGuardian, stopCapability))
                throw new InvalidOperationException("Original P continuation operation or command binding differs.");
            originalGuardian.AssertProducerContinuation(this, stopCapability);
            RequireAdmission();
        }
        // Internal native acquisition validates the exact retained objects; there
        // is no public caller-supplied 'disposable' assertion or process identity.
        internal bool OwnsCommand(CompilerCommand candidate, ProducerStopCapability capability) {
            lock (failureGate) {
                return !failed && !disposed && !releaseStarted && !containmentStarted && startCompleted && capabilityAttempted &&
                    candidate != null && capability != null && Object.ReferenceEquals(command, candidate) &&
                    Object.ReferenceEquals(stopCapability, capability);
            }
        }
        internal bool OwnsTransferCommand(CompilerCommand candidate, ProducerTransferCapability capability) {
            lock (failureGate) {
                return !failed && !disposed && !releaseStarted && !containmentStarted && startCompleted &&
                    transferCapabilityAttempted && guardian != null && candidate != null && capability != null &&
                    Object.ReferenceEquals(command, candidate) && Object.ReferenceEquals(transferCapability, capability);
            }
        }
        internal bool OwnsTransferGuardian(GuardianLaunch candidate, ProducerTransferCapability capability) {
            return capability != null && Object.ReferenceEquals(transferCapability, capability) &&
                OwnsGuardian(candidate, stopCapability);
        }
        // Managed-only registration performed after the caller retains W. Its
        // distinct input owner must not share P's existing compiler borrower slot.
        internal ProducerStopCapability RegisterGuardian(GuardianLaunch candidate, BuildInputLeaseSet guardianInputs) {
            Enter();
            try {
                RequireAdmission();
                if (candidate == null || guardianInputs == null || Object.ReferenceEquals(inputs, guardianInputs) ||
                        guardian != null || !startCompleted || stopCapability == null)
                    throw new InvalidOperationException("One retained guardian requires the original P capability and separate inputs.");
                guardian = candidate;
                return stopCapability;
            } catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        internal bool OwnsGuardian(GuardianLaunch candidate, ProducerStopCapability capability) {
            return candidate != null && capability != null && Object.ReferenceEquals(guardian, candidate) &&
                Object.ReferenceEquals(stopCapability, capability) && !failed && !containmentStarted && !disposed && !releaseStarted;
        }
        internal bool OwnsDeadline(ProducerDeadline candidate) { return Object.ReferenceEquals(startupDeadline, candidate); }
        internal bool OwnsControlChannel(ProducerGuardianChannel candidate) { return Object.ReferenceEquals(controlChannel, candidate); }
        internal bool OwnsContextSection(ProducerContextSection candidate) { return Object.ReferenceEquals(contextSection, candidate); }
        internal ProducerContextSection BorrowContextSection(GuardianLaunch candidate, ProducerStopCapability capability) {
            Enter();
            try {
                RequireAdmission();
                if (!OwnsGuardian(candidate, capability)) throw new InvalidOperationException("Only the original registered guardian may use P's context section.");
                contextSection.AssertPreparedOwner(this);
                return contextSection;
            } catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        internal void AssertContextReleaseAllowed(ProducerContextSection candidate) {
            if (!Object.ReferenceEquals(contextSection, candidate) || (!containmentProven && !normalClosureProven) || guardian != null)
                throw new InvalidOperationException("Context section resources require original P containment and complete guardian release.");
        }
        internal ProducerGuardianChannel BorrowControlChannel(GuardianLaunch candidate, ProducerStopCapability capability) {
            Enter();
            try {
                RequireAdmission();
                if (!OwnsGuardian(candidate, capability)) throw new InvalidOperationException("Only the original registered guardian may use P's private control channel.");
                controlChannel.AssertPreparedOwner(this);
                return controlChannel;
            } catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        internal void AssertChannelReleaseAllowed(ProducerGuardianChannel candidate) {
            if (!Object.ReferenceEquals(controlChannel, candidate) || (!containmentProven && !normalClosureProven) || guardian != null)
                throw new InvalidOperationException("Private channel copies require original P containment and complete guardian release.");
        }
        internal ProducerDeadline BindGuardianDeadline(GuardianLaunch candidate, ProducerStopCapability capability) {
            Enter();
            try {
                RequireAdmission();
                if (!OwnsGuardian(candidate, capability)) throw new InvalidOperationException("Only the original registered guardian may bind P's exact startup deadline.");
                startupDeadline.AssertActive(this);
                return startupDeadline;
            } catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        internal void AcquireGuardianLaunchCopy(GuardianLaunch candidate, ProducerStopCapability.LaunchCopy copy) {
            Enter();
            try {
                RequireAdmission();
                if (!OwnsGuardian(candidate, stopCapability) || copy == null || !copy.BelongsTo(stopCapability, candidate))
                    throw new InvalidOperationException("Only the original registered W may acquire its exact P launch copy.");
                // Serialize the full native duplicate acquisition with original
                // P containment/cap retirement; a Boolean precheck alone is not
                // sufficient to protect the source duplicate from handle reuse.
                copy.Acquire();
                RequireAdmission();
            } catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        internal void AcquireSupervisorSourceCopy(GuardianLaunch candidate, ProducerStopCapability.SupervisorCopy copy) {
            Enter();
            try {
                RequireAdmission();
                if (!OwnsGuardian(candidate, stopCapability) || copy == null || !copy.BelongsTo(stopCapability, candidate))
                    throw new InvalidOperationException("Only the original W owner may retain the separate P source copy offered to S.");
                copy.Acquire(); RequireAdmission();
            } catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        internal void AcquireGuardianTransferLaunchCopy(GuardianLaunch candidate, ProducerTransferCapability.LaunchCopy copy) {
            Enter();
            try {
                RequireAdmission();
                if (!OwnsTransferGuardian(candidate, transferCapability) || copy == null || !copy.BelongsTo(transferCapability, candidate))
                    throw new InvalidOperationException("Only the original registered W may acquire its exact P transfer launch copy.");
                // Serialize native duplication with P containment/source retirement.
                // A detached liveness Boolean cannot protect against handle reuse.
                copy.Acquire();
                RequireAdmission();
            } catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        internal void ReleaseGuardian(GuardianLaunch candidate) {
            Enter();
            try {
                if (candidate == null || !Object.ReferenceEquals(guardian, candidate))
                    throw new InvalidOperationException("Only the original fully closed guardian can release P ownership.");
                candidate.AssertProducerReleaseAllowed(this);
                guardian = null;
            } catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        internal CompilerResult PollNormalTerminal(GuardianLaunch originalGuardian) {
            Enter();
            try {
                RequireAdmission();
                if (!continuationCompleted || !OwnsGuardian(originalGuardian, stopCapability) || command == null)
                    throw new InvalidOperationException("Normal P terminal requires its original continued command and guardian.");
                originalGuardian.AssertNormalProducerObservation(this);
                CompilerResult observed = command.PollResult();
                if (observed == null) return null;
                if (normalResult != null && !Object.ReferenceEquals(normalResult, observed))
                    throw new InvalidOperationException("Original P result changed during terminal observation.");
                // PollResult observes original P zero exit, outer Job empty and
                // both actual stdout/stderr EOF with settled original collectors.
                normalResult = observed;
                long nativeExit = command.ObserveProducerNormalExit(this, observed);
                if (normalExitFileTime != 0 && normalExitFileTime != nativeExit)
                    throw new InvalidOperationException("Original P native exit identity changed.");
                normalExitFileTime = nativeExit;
                // Local source capabilities must be retired before W disposal.
                // Inherited W copies are separate, retained capabilities, not a
                // reason to keep L's source slots open after original P death.
                if (stopCapability != null) stopCapability.CloseAfterProducerDeath();
                if (transferCapability != null) transferCapability.CloseAfterProducerDeath();
                RequireAdmission(); originalGuardian.AssertNormalProducerObservation(this);
                normalClosureProven = true; return normalResult;
            } catch (Exception failure) { Record(failure); throw; }
            finally { Leave(); }
        }
        internal long OriginalNormalExitFileTime(GuardianLaunch originalGuardian, CompilerResult originalResult) {
            if (!OwnsGuardian(originalGuardian, stopCapability) || !normalClosureProven || normalExitFileTime <= 0 ||
                    originalResult == null || !Object.ReferenceEquals(normalResult, originalResult))
                throw new InvalidOperationException("Normal P exit time lacks original native closure.");
            return normalExitFileTime;
        }
        public bool PollContainment() {
            Enter();
            try {
                if (disposed || releaseStarted) throw new InvalidOperationException("Producer release has begun; retry Dispose only.");
                containmentStarted = true;
                if (command == null) {
                    if (nativeStartAttempted || stopCapability != null || transferCapability != null)
                        throw new InvalidOperationException("Missing original producer command after a native attempt must remain retained.");
                    // No P process start was attempted on this path. Private
                    // channel/section acquisition may already have occurred; retained
                    // outcomes are released only by Dispose, not by this proof.
                    containmentProven = true; return true;
                }
                if (!command.PollContainment()) return false;
                // Command's exact root, private Job and EOF/collector proof comes
                // first. A local duplicate failure must retain the command and its
                // registered input ownership, rather than bypassing that lifetime.
                if (stopCapability != null) stopCapability.CloseAfterProducerDeath();
                if (transferCapability != null) transferCapability.CloseAfterProducerDeath();
                containmentProven = true; return true;
            } catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        public void Dispose() {
            Enter();
            try {
                if (disposed) return;
                if (!containmentProven && !normalClosureProven) throw new InvalidOperationException("Original producer closure must complete before resource release.");
                if (guardian != null) throw new InvalidOperationException("Retain original P identity until its exact guardian has naturally exited and released ownership.");
                if (stopCapability != null && !stopCapability.IsClosed)
                    throw new InvalidOperationException("The original producer stop duplicate remains retained.");
                if (transferCapability != null && !transferCapability.IsClosed)
                    throw new InvalidOperationException("The original producer transfer duplicate remains retained.");
                releaseStarted = true;
                controlChannel.DisposeAfterContainment(this);
                contextSection.DisposeAfterContainment(this);
                if (command != null) command.Dispose();
                // CompilerCommand releases its exact input registration only after
                // all its resources close. The wrapper never disposes caller inputs.
                disposed = true;
            } catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
    }

    internal sealed class ProducerStopCapability {
        private enum DuplicateDisposition { None, Owned, Ambiguous }
        private readonly DisposableProducerLaunch owner;
        private readonly CompilerCommand command;
        private bool acquisitionStarted, duplicateAttempted, duplicateReturned, duplicateSucceeded, closed;
        private bool launchCopyPrepared;
        private bool closeAttempted, closeReturned, closeSucceeded;
        private IntPtr originalProcess, duplicate;
        private int originalPid;
        private long originalBirth;
        [DllImport("kernel32.dll")] private static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll")] private static extern uint GetCurrentProcessId();
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool DuplicateHandle(IntPtr sourceProcess, IntPtr sourceHandle,
            IntPtr targetProcess, out IntPtr targetHandle, uint desiredAccess, bool inherit, uint options);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint GetProcessId(IntPtr process);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetProcessTimes(IntPtr process, out long created, out long exited, out long kernel, out long user);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint WaitForSingleObject(IntPtr process, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool CloseHandle(IntPtr handle);
        internal ProducerStopCapability(DisposableProducerLaunch owner, CompilerCommand command) {
            if (owner == null || command == null) throw new ArgumentNullException("original producer ownership");
            this.owner = owner; this.command = command;
        }
        internal bool IsClosed { get { return closed; } }
        internal sealed class SupervisorCopy {
            [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetHandleInformation(IntPtr handle, out uint flags);
            private readonly ProducerStopCapability source;
            private readonly GuardianLaunch guardian;
            private IntPtr raw;
            private bool attempted, returned, succeeded;
            private int error;
            internal SupervisorCopy(ProducerStopCapability source, GuardianLaunch guardian) {
                if (source == null || guardian == null || !source.owner.OwnsGuardian(guardian, source))
                    throw new InvalidOperationException("Separate S source copy requires original P/W ownership.");
                this.source=source; this.guardian=guardian;
            }
            internal bool BelongsTo(ProducerStopCapability original, GuardianLaunch candidate) {
                return Object.ReferenceEquals(source,original)&&Object.ReferenceEquals(guardian,candidate);
            }
            internal void Acquire() {
                if (attempted || source.closed || source.closeAttempted ||
                    !source.owner.OwnsGuardian(guardian,source) || source.Disposition()!=DuplicateDisposition.Owned)
                    throw new InvalidOperationException("One retained original P source-copy acquisition is required.");
                source.AssertIdentity(source.duplicate,true);
                attempted=true;
                bool okay=DuplicateHandle(GetCurrentProcess(),source.duplicate,GetCurrentProcess(),out raw,0x101001,false,0);
                error=Marshal.GetLastWin32Error(); succeeded=okay; returned=true;
                if (!okay) throw new Win32Exception(error,"Retain separate original P source slot for S");
                if (!Valid(raw)||raw==source.duplicate||raw==source.originalProcess)
                    throw new InvalidOperationException("Separate original P source-copy output is ambiguous.");
                uint flags;
                if(!GetHandleInformation(raw,out flags)||(flags&1)!=0)
                    throw new InvalidOperationException("Separate original P source copy must be noninheritable.");
                source.AssertIdentity(raw,true);
            }
            internal IntPtr Borrow(GuardianLaunch candidate) {
                if (!Object.ReferenceEquals(guardian,candidate)||!attempted||!returned||!succeeded||!Valid(raw))
                    throw new InvalidOperationException("Separate original P source copy is unavailable.");
                return raw;
            }
            // Intentionally no Close/Dispose/finalizer. This new L-process-owned
            // source slot is quarantined through L physical exit, independently
            // of the old P cap retirement and any failed/unknown S transfer.
        }
        // No public handle export. A launch-copy object is retained by W before
        // its separate inheritable duplicate acquisition; the durable cap remains
        // non-inheritable and P's Job/thread/L handles are never exported.
        internal LaunchCopy PrepareLaunchCopy(GuardianLaunch guardian) {
            if (guardian == null || launchCopyPrepared || !owner.OwnsGuardian(guardian, this)) throw new InvalidOperationException("One exact retained guardian launch copy is required.");
            launchCopyPrepared = true;
            return new LaunchCopy(this, guardian);
        }
        private static bool Valid(IntPtr handle) { return handle != IntPtr.Zero && handle != new IntPtr(-1); }
        private static void Check(bool okay, string message) { if (!okay) throw new Win32Exception(Marshal.GetLastWin32Error(), message); }
        private DuplicateDisposition Disposition() {
            if (duplicate == IntPtr.Zero && (!duplicateAttempted || (duplicateReturned && !duplicateSucceeded))) return DuplicateDisposition.None;
            if (acquisitionStarted && duplicateAttempted && duplicateReturned && duplicateSucceeded && Valid(duplicate) &&
                    Valid(originalProcess) && duplicate != originalProcess && originalPid > 0 && originalBirth > 0)
                return DuplicateDisposition.Owned;
            return DuplicateDisposition.Ambiguous;
        }
        private void AssertIdentity(IntPtr handle, bool alive) {
            if (!Valid(handle) || originalPid <= 0 || originalBirth <= 0)
                throw new InvalidOperationException("Original disposable producer identity is unavailable.");
            uint expectedWait = alive ? 258u : 0u;
            uint before = WaitForSingleObject(handle, 0); Check(before != UInt32.MaxValue, "Original producer identity wait");
            if (before != expectedWait || GetProcessId(handle) != (uint)originalPid || (uint)originalPid == GetCurrentProcessId())
                throw new InvalidOperationException("Held disposable producer identity/lifetime differs or targets the shared current host.");
            long created, exited, kernel, user;
            Check(GetProcessTimes(handle, out created, out exited, out kernel, out user), "Original disposable producer native times");
            if (created != originalBirth || (!alive && (exited <= 0 || exited < created)))
                throw new InvalidOperationException("Held disposable producer birth or physical death differs.");
            uint after = WaitForSingleObject(handle, 0); Check(after != UInt32.MaxValue, "Original producer identity wait recheck");
            if (after != expectedWait) throw new InvalidOperationException("Original disposable producer lifetime changed across the boundary.");
        }
        // Called ONLY by CompilerCommand after its original successful suspended
        // birth, same-owner reference, input, root and Job assertions. This method
        // is internal and never adopts a public PID, arbitrary process or Boolean.
        internal void AcquireOriginal(IntPtr process, int pid, long birth) {
            if (closed || acquisitionStarted || !owner.OwnsCommand(command, this))
                throw new InvalidOperationException("Original producer stop acquisition is unavailable or already attempted.");
            acquisitionStarted = true;
            if (!Valid(process) || pid <= 0 || birth <= 0) throw new InvalidOperationException("Original new producer process is required.");
            originalProcess = process; originalPid = pid; originalBirth = birth;
            AssertIdentity(originalProcess, true);
            // These pseudohandles are source/target PROCESS TABLES only. The
            // duplicated object is exclusively the retained original new P handle.
            duplicateAttempted = true;
            bool copied = DuplicateHandle(GetCurrentProcess(), originalProcess, GetCurrentProcess(), out duplicate, 0x101001, false, 0);
            int error = Marshal.GetLastWin32Error();
            duplicateSucceeded = copied; duplicateReturned = true;
            if (!copied) throw new Win32Exception(error, "Duplicate only the original new producer stop capability");
            if (Disposition() != DuplicateDisposition.Owned) throw new InvalidOperationException("Ambiguous producer duplicate output must remain retained.");
            AssertIdentity(duplicate, true); AssertIdentity(originalProcess, true);
        }
        internal void CloseAfterProducerDeath() {
            if (closed) return;
            // An interrupted native close may already have released the value for
            // reuse. Never query or close it again without a recorded return.
            if (closeAttempted && !closeReturned)
                throw new InvalidOperationException("Unknown producer duplicate close outcome must remain retained without another handle operation.");
            if (closeReturned && closeSucceeded) { duplicate = IntPtr.Zero; closed = true; return; }
            DuplicateDisposition disposition = Disposition();
            if (disposition == DuplicateDisposition.None) { closed = true; return; }
            if (disposition != DuplicateDisposition.Owned)
                throw new InvalidOperationException("Unknown producer duplicate disposition cannot be queried, closed or replaced.");
            // The wrapper has already obtained actual command containment. Recheck
            // BOTH still-retained original process and local duplicate before close.
            AssertIdentity(originalProcess, false); AssertIdentity(duplicate, false);
            closeAttempted = true; closeReturned = false; closeSucceeded = false;
            bool released = CloseHandle(duplicate);
            int error = Marshal.GetLastWin32Error();
            closeSucceeded = released; closeReturned = true;
            if (!released) throw new Win32Exception(error, "Close original local producer stop duplicate after physical death");
            duplicate = IntPtr.Zero; closed = true;
        }

        internal sealed class LaunchCopy {
            private readonly ProducerStopCapability source;
            private readonly GuardianLaunch guardian;
            private bool attempted, returned, succeeded, closeAttempted, closeReturned, closeSucceeded, closed;
            private bool inheritanceRetirementAttempted, inheritanceRetirementReturned, inheritanceRetirementSucceeded, inheritanceRetirementVerified;
            private IntPtr handle;
            private int pid;
            private long birth;
            [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetHandleInformation(IntPtr handle, out uint flags);
            [DllImport("kernel32.dll", SetLastError = true)] private static extern bool SetHandleInformation(IntPtr handle, uint mask, uint flags);
            internal LaunchCopy(ProducerStopCapability source, GuardianLaunch guardian) { this.source = source; this.guardian = guardian; }
            internal bool BelongsTo(ProducerStopCapability capability, GuardianLaunch candidate) {
                return Object.ReferenceEquals(source, capability) && Object.ReferenceEquals(guardian, candidate);
            }
            internal bool IsClosed { get { return closed; } }
            private void RequireKnownOwned() {
                if (closed || (closeAttempted && !closeReturned) || (closeReturned && closeSucceeded) ||
                        !attempted || !returned || !succeeded || !Valid(handle) || handle == source.originalProcess ||
                        handle == source.duplicate || pid <= 0 || birth <= 0)
                    throw new InvalidOperationException("Unknown, closed or unavailable original P launch-copy outcome.");
            }
            internal void Acquire() {
                if (attempted || closed || !source.owner.OwnsGuardian(guardian, source) ||
                        source.closeAttempted || source.closed || source.Disposition() != DuplicateDisposition.Owned)
                    throw new InvalidOperationException("One exact original P launch-copy acquisition is required.");
                source.AssertIdentity(source.originalProcess, true); source.AssertIdentity(source.duplicate, true);
                uint flags; Check(GetHandleInformation(source.duplicate, out flags), "Read durable P capability inheritance");
                if ((flags & 1) != 0) throw new InvalidOperationException("The durable original P capability must remain non-inheritable.");
                pid = source.originalPid; birth = source.originalBirth;
                attempted = true;
                bool copied = DuplicateHandle(GetCurrentProcess(), source.duplicate, GetCurrentProcess(), out handle, 0x101001, true, 0);
                int error = Marshal.GetLastWin32Error(); succeeded = copied; returned = true;
                if (!copied) throw new Win32Exception(error, "Create separate inherited original P launch copy");
                AssertLive();
                Check(GetHandleInformation(handle, out flags), "Read original P launch-copy inheritance");
                if ((flags & 1) == 0) throw new InvalidOperationException("The one-use P launch copy must be inheritable at birth.");
            }
            internal void AssertLive() {
                RequireKnownOwned();
                if (!source.owner.OwnsGuardian(guardian, source)) throw new InvalidOperationException("Original guardian/P ownership changed.");
                source.AssertIdentity(source.originalProcess, true); source.AssertIdentity(handle, true);
            }
            internal IntPtr BorrowForLaunch(GuardianLaunch candidate) {
                if (!Object.ReferenceEquals(guardian, candidate)) throw new InvalidOperationException("The P launch copy belongs to another guardian.");
                if (inheritanceRetirementAttempted) throw new InvalidOperationException("The original launch copy cannot be exported again after inheritance retirement begins.");
                AssertLive(); return handle;
            }
            internal void RetireLaunchInheritance(GuardianLaunch candidate) {
                if (!Object.ReferenceEquals(guardian, candidate) || inheritanceRetirementAttempted)
                    throw new InvalidOperationException("Original launch-copy inheritance retirement is single-use.");
                RequireKnownOwned(); inheritanceRetirementAttempted = true;
                // Only this separate local launch copy changes flags. The durable
                // source capability remains non-inheritable throughout. W already
                // owns its independently inherited entry once CreateProcess returns.
                bool changed = SetHandleInformation(handle, 1, 0); int error = Marshal.GetLastWin32Error();
                inheritanceRetirementSucceeded = changed; inheritanceRetirementReturned = true;
                if (!changed) throw new Win32Exception(error, "Retire original P launch-copy inheritance after recorded W creation return");
                uint flags; Check(GetHandleInformation(handle, out flags), "Verify original P launch-copy inheritance retirement");
                if ((flags & 3) != 0) throw new InvalidOperationException("Original P launch-copy inheritance/protected-close flags differ after retirement.");
                inheritanceRetirementVerified = true;
            }
            internal void AssertLaunchInheritanceRetired() {
                RequireKnownOwned();
                if (!inheritanceRetirementAttempted || !inheritanceRetirementReturned ||
                        !inheritanceRetirementSucceeded || !inheritanceRetirementVerified)
                    throw new InvalidOperationException("Original P launch-copy inheritance retirement is not proved.");
                uint flags; Check(GetHandleInformation(handle, out flags), "Recheck retired original P launch-copy flags");
                if ((flags & 3) != 0) throw new InvalidOperationException("Retired original P launch-copy flags changed.");
            }
            internal int ProducerId { get { RequireKnownOwned(); return pid; } }
            internal long ProducerBirth { get { RequireKnownOwned(); return birth; } }
            internal void CloseAfterContainment() {
                if (closed) return;
                guardian.AssertCopyReleaseAllowed(this);
                if (closeAttempted && !closeReturned) throw new InvalidOperationException("Unknown P launch-copy close outcome cannot be retried.");
                if (closeReturned && closeSucceeded) { handle = IntPtr.Zero; closed = true; return; }
                if (handle == IntPtr.Zero && (!attempted || (returned && !succeeded))) { closed = true; return; }
                RequireKnownOwned();
                // A failed/unknown SetHandleInformation outcome never grants
                // admission, but it does not release/reuse this process handle.
                // Exact P/W physical closure still permits its owned final close.
                source.AssertIdentity(source.originalProcess, false); source.AssertIdentity(handle, false);
                closeAttempted = true; closeReturned = false; closeSucceeded = false;
                bool released = CloseHandle(handle); int error = Marshal.GetLastWin32Error(); closeSucceeded = released; closeReturned = true;
                if (!released) throw new Win32Exception(error, "Close original P launch copy after actual P/W containment");
                handle = IntPtr.Zero; closed = true;
            }
        }
    }
}
