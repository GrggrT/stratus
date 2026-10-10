// UNQUALIFIED SOURCE: actual retained L-side P/W startup and preload ownership.
// The independent selector has no descriptor. The separate Open barrier also
// remains unconditional. Neither metadata declarations nor this loaded class
// authenticate the already-loaded L/bootstrap/runtime or qualify dependency
// resolution. No runtime-family defaults or candidate self-hash root exist.
// Existing real Inputs/Producer/Guardian/Bootstrap sources are dependencies;
// there are no fake borrower types or copied native input-owner primitives.
using System;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Threading;

namespace Stratus.RuntimeBuild {
    public sealed class StartupPreloadOwner : IDisposable {
        private const string DescriptorVersion = "stratus-runtime-build-startup-preload-v1";
        private const int ContextLimit = 8388608, InventoryCountLimit = 104;
        private const long FileLimit = 134217728, InventoryBytesLimit = 275251200;
        private enum PreparationPhase { None, Producer, Guardian }
        private enum PreloadRole {
            ProducerExecutable, ProducerRuntime, ProducerDependency, ProducerConfiguration,
            GuardianExecutable, GuardianRuntime, GuardianDependency, GuardianConfiguration
        }

        private sealed class PreloadRecord {
            internal readonly PreloadRole Role;
            internal readonly string Path, Sha256;
            internal readonly long Bytes;
            internal PreloadRecord(PreloadRole role, string path, string sha256, long bytes) {
                Role = role; Path = path; Sha256 = sha256; Bytes = bytes;
            }
        }

        // This private type is not an installation API, candidate profile, or
        // approval Boolean. A future independent qualification must supply its
        // exact scope outside candidate control; this revision creates none.
        private sealed class Descriptor {
            internal readonly string Version, SystemRoot, ProducerDirectory, GuardianDirectory;
            internal readonly string ContextSha256;
            internal readonly long ContextLength;
            private readonly PreloadRecord[] producerRecords, guardianRecords;
            internal Descriptor(string version, string systemRoot, string producerDirectory,
                    string guardianDirectory, string contextSha256, long contextLength,
                    PreloadRecord[] producerRecords, PreloadRecord[] guardianRecords) {
                Require(version == DescriptorVersion);
                BuildInputValidation.Path(systemRoot);
                BuildInputValidation.Path(producerDirectory);
                BuildInputValidation.Path(guardianDirectory);
                BuildInputValidation.Hash(contextSha256);
                Require(contextLength >= 1 && contextLength <= ContextLimit);
                Version = version; SystemRoot = systemRoot;
                ProducerDirectory = producerDirectory; GuardianDirectory = guardianDirectory;
                ContextSha256 = contextSha256; ContextLength = contextLength;
                this.producerRecords = CopyRecords(producerRecords, true);
                this.guardianRecords = CopyRecords(guardianRecords, false);
                AssertSharedDeclarations(this.producerRecords, this.guardianRecords);
            }
            internal Descriptor Copy() {
                return new Descriptor(Version, SystemRoot, ProducerDirectory, GuardianDirectory,
                    ContextSha256, ContextLength, producerRecords, guardianRecords);
            }
            internal PreloadRecord[] CopyProducerRecords() { return CopyRecords(producerRecords, true); }
            internal PreloadRecord[] CopyGuardianRecords() { return CopyRecords(guardianRecords, false); }
        }

        // Factories receive only original owner-derived data during the exact
        // private preparation call. Mutable arrays/maps never expose our copies.
        internal sealed class LaunchData {
            internal readonly BuildInputLeaseSet Inputs;
            internal readonly string Executable, Sha256, Directory, ContextSha256;
            internal readonly long ContextLength;
            private readonly Dictionary<string, string> environment;
            private readonly byte[] contextBytes;
            internal LaunchData(BuildInputLeaseSet inputs, string executable, string sha256,
                    string directory, string systemRoot, byte[] context, string contextSha256) {
                Inputs = inputs; Executable = executable; Sha256 = sha256;
                Directory = directory; ContextSha256 = contextSha256; ContextLength = context.LongLength;
                environment = ExactEnvironment(systemRoot, directory);
                contextBytes = (byte[])context.Clone();
            }
            internal Dictionary<string, string> Environment {
                get { return new Dictionary<string, string>(environment, StringComparer.Ordinal); }
            }
            internal byte[] ContextBytes { get { return (byte[])contextBytes.Clone(); } }
        }

        private readonly Descriptor descriptor;
        private readonly byte[] contextBytes;
        private readonly PreloadRecord[] producerRecords, guardianRecords;
        private readonly BuildInputLeaseSet producerInputs, guardianInputs;
        private readonly LaunchData producerData, guardianData;
        private BuildInputSnapshot[] producerSnapshots, guardianSnapshots;
        private DisposableProducerLaunch producer;
        private GuardianLaunch guardian;
        private int operation, failed, ready, releaseStarted, disposed;
        private int preparationThreadId;
        private PreparationPhase preparationPhase;
        private bool openAttempted, producerPreparationAttempted, guardianPreparationAttempted;
        private bool producerInputsDisposeAttempted, producerInputsDisposeReturned, producerInputsDisposed;
        private bool guardianInputsDisposeAttempted, guardianInputsDisposeReturned, guardianInputsDisposed;

        private StartupPreloadOwner(byte[] capturedContext, Descriptor selected) {
            descriptor = selected.Copy();
            contextBytes = (byte[])capturedContext.Clone();
            producerRecords = descriptor.CopyProducerRecords();
            guardianRecords = descriptor.CopyGuardianRecords();
            // Both managed-only real owners are retained before any Open. They
            // cannot share the existing command/guardian borrower slot.
            producerInputs = BuildInputLeaseSet.Prepare(Specifications(producerRecords));
            guardianInputs = BuildInputLeaseSet.Prepare(Specifications(guardianRecords));
            Require(!Object.ReferenceEquals(producerInputs, guardianInputs));
            producerData = new LaunchData(producerInputs, producerRecords[0].Path, producerRecords[0].Sha256, descriptor.ProducerDirectory,
                descriptor.SystemRoot, contextBytes, descriptor.ContextSha256);
            guardianData = new LaunchData(guardianInputs, guardianRecords[0].Path, guardianRecords[0].Sha256, descriptor.GuardianDirectory,
                descriptor.SystemRoot, contextBytes, descriptor.ContextSha256);
        }

        public static StartupPreloadOwner Prepare(byte[] rawContext) {
            Require(rawContext != null && rawContext.Length >= 1 && rawContext.Length <= ContextLimit);
            byte[] capturedContext = (byte[])rawContext.Clone();
            // Context is data to compare, never an argument to authority selection.
            Descriptor selected = SelectIndependentDescriptor();
            Require(selected != null);
            return new StartupPreloadOwner(capturedContext, selected);
        }

        private static Descriptor SelectIndependentDescriptor() {
            // No installed root, adjacent-file/CLI/environment override, fallback
            // pin, v2 payload approval reuse or operational descriptor exists.
            return null;
        }
        private static void DemandQualifiedPreloadOpen() {
            throw new InvalidOperationException("P/W preload Open requires independently qualified L/bootstrap, preload, environment and execution scope.");
        }
        private static void Require(bool condition) {
            if (!condition) throw new InvalidOperationException("Original startup preload admission or ownership differs.");
        }
        private void RecordFailure() { Interlocked.Exchange(ref failed, 1); }
        private void Enter() {
            if (Interlocked.CompareExchange(ref operation, 1, 0) != 0) {
                RecordFailure(); throw new InvalidOperationException("Concurrent startup preload operations are forbidden.");
            }
        }
        private void Leave() { Interlocked.Exchange(ref operation, 0); }
        private void AssertAdmission(bool requireReady) {
            Require(Interlocked.CompareExchange(ref failed, 0, 0) == 0 &&
                Interlocked.CompareExchange(ref releaseStarted, 0, 0) == 0 &&
                Interlocked.CompareExchange(ref disposed, 0, 0) == 0 &&
                (!requireReady || Interlocked.CompareExchange(ref ready, 0, 0) == 1));
        }

        public void Open() {
            Enter();
            try {
                AssertAdmission(false); Require(!openAttempted);
                openAttempted = true;
                Require(contextBytes.LongLength == descriptor.ContextLength &&
                    String.Equals(HashBytes(contextBytes), descriptor.ContextSha256, StringComparison.Ordinal));
                AssertAdmission(false);
                DemandQualifiedPreloadOpen();
                producerInputs.Open();
                AssertAdmission(false);
                producerSnapshots = producerInputs.CaptureSnapshots();
                VerifySnapshots(producerRecords, producerSnapshots);
                AssertAdmission(false);
                guardianInputs.Open();
                AssertAdmission(false);
                guardianSnapshots = guardianInputs.CaptureSnapshots();
                VerifySnapshots(guardianRecords, guardianSnapshots);
                // No P command or W registration exists before both actual
                // captures pass. These are the real retained input owners.
                producerInputs.AssertHeld(); guardianInputs.AssertHeld();
                AssertAdmission(false);
                Interlocked.Exchange(ref ready, 1);
            } catch (Exception) { RecordFailure(); throw; }
            finally { Leave(); }
            // No cleanup, worker, helper host, native timeout or hold loop here.
            // Pre-W L stalls are not bounded by W or by a stored timestamp.
        }

        public DisposableProducerLaunch PrepareProducer() {
            Enter();
            try {
                AssertAdmission(true);
                Require(!producerPreparationAttempted && producer == null && guardian == null &&
                    preparationPhase == PreparationPhase.None);
                producerPreparationAttempted = true;
                preparationThreadId = Thread.CurrentThread.ManagedThreadId;
                preparationPhase = PreparationPhase.Producer;
                // Factory is managed-only. No fallible admission call follows
                // atomic publication. Normal return or the historical getter
                // supplies the exact wrapper for explicit caller-driven recovery.
                DisposableProducerLaunch prepared = DisposableProducerLaunch.PreparePreloaded(this);
                Interlocked.Exchange(ref producer, prepared);
                return prepared;
            } catch (Exception) { RecordFailure(); throw; }
            finally { preparationPhase = PreparationPhase.None; preparationThreadId = 0; Leave(); }
        }

        public GuardianLaunch PrepareGuardian() {
            Enter();
            try {
                AssertAdmission(true);
                Require(producer != null && producerPreparationAttempted && !guardianPreparationAttempted &&
                    guardian == null && preparationPhase == PreparationPhase.None);
                AssertProducer(producer, producerInputs);
                guardianPreparationAttempted = true;
                preparationThreadId = Thread.CurrentThread.ManagedThreadId;
                preparationPhase = PreparationPhase.Guardian;
                // As above, return the retained wrapper without a post-factory
                // admission assertion. Every actual launch rechecks admission.
                GuardianLaunch prepared = GuardianLaunch.PreparePreloaded(this, producer);
                Interlocked.Exchange(ref guardian, prepared);
                return prepared;
            } catch (Exception) { RecordFailure(); throw; }
            finally { preparationPhase = PreparationPhase.None; preparationThreadId = 0; Leave(); }
        }

        // Historical exact references for explicit caller-driven recovery after
        // a failed or interrupted preparation, including assignment-before-return.
        // Null is possible. These getters adopt nothing and prove neither current
        // admission nor containment. Wrapper starts still check the live owner;
        // no lease, native handle, LaunchData or readiness capability is exported.
        public DisposableProducerLaunch RetainedProducer {
            get { return Interlocked.CompareExchange(ref producer, null, null); }
        }
        public GuardianLaunch RetainedGuardian {
            get { return Interlocked.CompareExchange(ref guardian, null, null); }
        }

        internal LaunchData BorrowProducerPreparation() {
            AssertAdmission(true);
            Require(Interlocked.CompareExchange(ref operation, 0, 0) == 1 &&
                preparationPhase == PreparationPhase.Producer &&
                preparationThreadId == Thread.CurrentThread.ManagedThreadId &&
                producerPreparationAttempted && producer == null && guardian == null);
            AssertAdmission(true); return producerData;
        }
        internal LaunchData BorrowGuardianPreparation(DisposableProducerLaunch candidate) {
            AssertAdmission(true);
            Require(Interlocked.CompareExchange(ref operation, 0, 0) == 1 &&
                preparationPhase == PreparationPhase.Guardian &&
                preparationThreadId == Thread.CurrentThread.ManagedThreadId &&
                guardianPreparationAttempted && guardian == null && candidate != null &&
                Object.ReferenceEquals(candidate, producer));
            AssertAdmission(true); return guardianData;
        }

        // Called by the original wrappers' own admission boundaries. Managed
        // state/reference checks only: no native input operation, callback into
        // wrapper Admission, Enter reentrancy, or blocking owner lock.
        internal void AssertProducer(DisposableProducerLaunch candidate, BuildInputLeaseSet inputs) {
            AssertAdmission(true);
            Require(candidate != null && inputs != null && producerPreparationAttempted &&
                Object.ReferenceEquals(candidate, producer) && Object.ReferenceEquals(inputs, producerInputs));
            AssertAdmission(true);
        }
        internal void AssertGuardian(GuardianLaunch candidate, DisposableProducerLaunch p, BuildInputLeaseSet inputs) {
            AssertAdmission(true);
            Require(candidate != null && p != null && inputs != null && guardianPreparationAttempted &&
                Object.ReferenceEquals(candidate, guardian) && Object.ReferenceEquals(p, producer) &&
                Object.ReferenceEquals(inputs, guardianInputs));
            AssertAdmission(true);
        }

        public void Dispose() {
            Enter();
            try {
                if (Interlocked.CompareExchange(ref disposed, 0, 0) == 1) return;
                // Close new admission before checking irreversible wrapper
                // closure. Failed closure checks do not reopen admission.
                Interlocked.Exchange(ref releaseStarted, 1);
                if (guardian != null) guardian.AssertPreloadReleaseAllowed(this);
                if (producer != null) producer.AssertPreloadReleaseAllowed(this);
                // These callbacks only verify existing complete disposal. This
                // owner never starts wrapper containment, joins, or cleans them
                // up automatically. Their exact borrower releases come first.
                DisposeInputs(guardianInputs, ref guardianInputsDisposeAttempted,
                    ref guardianInputsDisposeReturned, ref guardianInputsDisposed);
                DisposeInputs(producerInputs, ref producerInputsDisposeAttempted,
                    ref producerInputsDisposeReturned, ref producerInputsDisposed);
                Interlocked.Exchange(ref disposed, 1);
            } catch (Exception) { RecordFailure(); throw; }
            finally { Leave(); }
        }
        private static void DisposeInputs(BuildInputLeaseSet inputs, ref bool attempted,
                ref bool returned, ref bool succeeded) {
            if (succeeded) return;
            Require(!attempted || returned);
            attempted = true; returned = false;
            try { inputs.Dispose(); succeeded = true; returned = true; }
            catch (Exception) { returned = true; throw; }
            // A later explicit retry delegates only a known-return failure to
            // the existing owner's own release flags. Unknown calls are retained.
            // No field is cleared, owner replaced, Open retried or native close
            // inferred here; all original references remain available as evidence.
        }

        private static PreloadRecord[] CopyRecords(PreloadRecord[] source, bool producerRole) {
            Require(source != null && source.Length >= 1 && source.Length <= InventoryCountLimit);
            PreloadRecord[] copy = new PreloadRecord[source.Length];
            HashSet<string> paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            long total = 0; int executables = 0;
            for (int i = 0; i < source.Length; ++i) {
                PreloadRecord item = source[i]; Require(item != null);
                bool executable = item.Role == (producerRole ? PreloadRole.ProducerExecutable : PreloadRole.GuardianExecutable);
                bool allowed = producerRole ?
                    (item.Role == PreloadRole.ProducerExecutable || item.Role == PreloadRole.ProducerRuntime ||
                     item.Role == PreloadRole.ProducerDependency || item.Role == PreloadRole.ProducerConfiguration) :
                    (item.Role == PreloadRole.GuardianExecutable || item.Role == PreloadRole.GuardianRuntime ||
                     item.Role == PreloadRole.GuardianDependency || item.Role == PreloadRole.GuardianConfiguration);
                Require(allowed && executable == (i == 0));
                BuildInputValidation.Path(item.Path); BuildInputValidation.Hash(item.Sha256);
                Require(paths.Add(item.Path) && item.Bytes >= 1 && item.Bytes <= FileLimit);
                if (executable) { ++executables; Require(item.Path.EndsWith(".exe", StringComparison.OrdinalIgnoreCase)); }
                total = checked(total + item.Bytes); Require(total <= InventoryBytesLimit);
                copy[i] = new PreloadRecord(item.Role, item.Path, item.Sha256, item.Bytes);
            }
            Require(executables == 1); return copy;
        }
        private static void AssertSharedDeclarations(PreloadRecord[] p, PreloadRecord[] w) {
            foreach (PreloadRecord left in p) {
                foreach (PreloadRecord right in w) {
                    if (!String.Equals(left.Path, right.Path, StringComparison.OrdinalIgnoreCase)) continue;
                    Require(left.Bytes == right.Bytes && String.Equals(left.Sha256, right.Sha256, StringComparison.Ordinal));
                }
            }
            // Cross-owner paths may overlap when their explicit declarations
            // agree. Separate owners do not prove physical cross-role nonaliasing
            // or complete native/managed dependency-resolution closure.
        }
        private static BuildInputSpec[] Specifications(PreloadRecord[] records) {
            BuildInputSpec[] specs = new BuildInputSpec[records.Length];
            for (int i = 0; i < records.Length; ++i)
                specs[i] = new BuildInputSpec(records[i].Path, records[i].Sha256, records[i].Bytes, FileLimit);
            return specs;
        }
        private static Dictionary<string, string> ExactEnvironment(string systemRoot, string directory) {
            Dictionary<string, string> result = new Dictionary<string, string>(StringComparer.Ordinal);
            result.Add("SystemRoot", systemRoot); result.Add("TEMP", directory);
            result.Add("TMP", directory); result.Add("WINDIR", systemRoot);
            return result;
        }
        private void VerifySnapshots(PreloadRecord[] records, BuildInputSnapshot[] snapshots) {
            Require(snapshots != null && snapshots.Length == records.Length);
            for (int i = 0; i < records.Length; ++i) {
                AssertAdmission(false);
                BuildInputSnapshot snapshot = snapshots[i]; PreloadRecord expected = records[i];
                Require(snapshot != null && String.Equals(snapshot.Path, expected.Path, StringComparison.Ordinal));
                byte[] actual = snapshot.Bytes;
                Require(actual != null && actual.LongLength == expected.Bytes && actual.LongLength <= FileLimit &&
                    snapshot.Length == actual.LongLength && String.Equals(HashBytes(actual), expected.Sha256, StringComparison.Ordinal));
                // Snapshot.Sha256 copies the specification. It is deliberately
                // not substituted for hashing these actual captured bytes.
                AssertAdmission(false);
            }
        }
        private static string HashBytes(byte[] actual) {
            using (SHA256 hash = SHA256.Create()) {
                return BitConverter.ToString(hash.ComputeHash(actual)).Replace("-", "").ToLowerInvariant();
            }
        }
    }
}
