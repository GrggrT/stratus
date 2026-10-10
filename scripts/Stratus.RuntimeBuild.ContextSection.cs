// UNQUALIFIED byte-correspondence/delivery ownership, not profile approval.
// The original managed P wrapper owns this unnamed section and its two exact
// read-only delivery copies. No path/name lookup or public raw-handle adoption.
// L prepares these bytes before W exists: a stalled L native operation is NOT
// bounded by W's authority to stop only P. Isolated/qualified L remains required.
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Cryptography;

namespace Stratus.RuntimeBuild {
    internal sealed class ContextSectionDelivery {
        internal readonly IntPtr Handle;
        internal readonly int Bytes;
        internal readonly string Sha256;
        internal ContextSectionDelivery(IntPtr handle, int bytes, string sha256) {
            Handle = handle; Bytes = bytes; Sha256 = sha256;
        }
    }

    internal sealed class ProducerContextSection {
        private sealed class HandleOutcome {
            internal IntPtr Raw;
            internal bool AcquireAttempted, AcquireReturned, AcquireSucceeded;
            internal bool InheritAttempted, InheritReturned, InheritSucceeded, InheritVerified;
            internal bool CloseAttempted, CloseReturned, CloseSucceeded;
        }
        private readonly DisposableProducerLaunch originalOwner;
        private readonly byte[] originalBytes;
        private readonly string expectedSha256;
        private readonly int expectedLength;
        private readonly object gate = new object();
        private readonly HandleOutcome mapping = new HandleOutcome(), producerCopy = new HandleOutcome(), guardianCopy = new HandleOutcome();
        private GuardianLaunch originalGuardian;
        private ProducerStopCapability originalGuardianCapability;
        private IntPtr writableView;
        private bool viewAttempted, viewReturned, viewSucceeded, unmapAttempted, unmapReturned, unmapSucceeded;
        private bool openAttempted, opened, failed, disposed, producerBorrowed, guardianBorrowed;
        private bool producerRetirementAttempted, guardianRetirementAttempted, producerRetired, guardianRetired;

        [DllImport("kernel32.dll", EntryPoint = "CreateFileMappingW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateFileMapping(IntPtr file, IntPtr security, uint protection, uint maximumHigh, uint maximumLow, string name);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern IntPtr MapViewOfFile(IntPtr mapping, uint access, uint offsetHigh, uint offsetLow, UIntPtr bytes);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool UnmapViewOfFile(IntPtr address);
        [DllImport("kernel32.dll")] private static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool DuplicateHandle(IntPtr sourceProcess, IntPtr sourceHandle,
            IntPtr targetProcess, out IntPtr targetHandle, uint access, bool inherit, uint options);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetHandleInformation(IntPtr handle, out uint flags);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool SetHandleInformation(IntPtr handle, uint mask, uint flags);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool CloseHandle(IntPtr handle);

        // Managed-only copy/hash correspondence. The supplied digest/length must
        // come from an independently selected context; this API cannot prove its
        // approval origin, semantics, compiled bridge, or any Resume permission.
        internal ProducerContextSection(DisposableProducerLaunch owner, byte[] raw, string expectedSha, long expectedBytes) {
            if (owner == null || raw == null) throw new ArgumentNullException("original context");
            BuildInputValidation.Hash(expectedSha);
            if (expectedBytes < 1 || expectedBytes > 8388608 || raw.LongLength != expectedBytes)
                throw new ArgumentException("Original context must be exactly one through 8388608 bytes.");
            originalOwner = owner; expectedLength = checked((int)expectedBytes); expectedSha256 = expectedSha;
            originalBytes = (byte[])raw.Clone();
            string actual;
            using (SHA256 hash = SHA256.Create()) actual = BitConverter.ToString(hash.ComputeHash(originalBytes)).Replace("-", "").ToLowerInvariant();
            if (actual != expectedSha256) throw new ArgumentException("Copied original context differs from its independent byte pin.");
        }
        private static bool Valid(IntPtr value) {
            return value != IntPtr.Zero && value != new IntPtr(-1) && value != new IntPtr(-2) && value != new IntPtr(-3) &&
                value != new IntPtr(-4) && value != new IntPtr(-5) && value != new IntPtr(-6);
        }
        private static void Check(bool okay, string message) { if (!okay) throw new Win32Exception(Marshal.GetLastWin32Error(), message); }
        private void RequireOwner(DisposableProducerLaunch candidate) {
            if (candidate == null || !Object.ReferenceEquals(originalOwner, candidate) || !candidate.OwnsContextSection(this))
                throw new InvalidOperationException("Context section belongs to another original P wrapper.");
        }
        private static void RequireAcquired(HandleOutcome outcome) {
            if (!outcome.AcquireAttempted || !outcome.AcquireReturned || !outcome.AcquireSucceeded || !Valid(outcome.Raw))
                throw new InvalidOperationException("Original context handle acquisition is unknown or incomplete.");
        }
        private static void RequireUsable(HandleOutcome outcome) {
            RequireAcquired(outcome);
            if (outcome.CloseSucceeded || (outcome.CloseAttempted && !outcome.CloseReturned))
                throw new InvalidOperationException("Original context handle has a closed or unknown close outcome.");
        }
        private void RequireDistinct() {
            if (mapping.AcquireSucceeded && producerCopy.AcquireSucceeded && mapping.Raw == producerCopy.Raw ||
                    mapping.AcquireSucceeded && guardianCopy.AcquireSucceeded && mapping.Raw == guardianCopy.Raw ||
                    producerCopy.AcquireSucceeded && guardianCopy.AcquireSucceeded && producerCopy.Raw == guardianCopy.Raw)
                throw new InvalidOperationException("Original context handle roles alias.");
        }
        private static void AssertFlags(HandleOutcome outcome, uint expected) {
            RequireUsable(outcome);
            uint flags; Check(GetHandleInformation(outcome.Raw, out flags), "Read original context handle flags");
            if ((flags & 3) != expected) throw new InvalidOperationException("Original context handle inheritance/protection differs.");
        }
        private static void RequireClosed(HandleOutcome outcome) {
            RequireAcquired(outcome);
            if (!outcome.CloseAttempted || !outcome.CloseReturned || !outcome.CloseSucceeded)
                throw new InvalidOperationException("Original context handle closure is unproved.");
        }
        private void RequireWritableRetired() {
            if (!viewAttempted || !viewReturned || !viewSucceeded || !Valid(writableView) ||
                    !unmapAttempted || !unmapReturned || !unmapSucceeded)
                throw new InvalidOperationException("Original writable context view is not proved retired.");
            RequireClosed(mapping);
        }
        private void RequireOpened() {
            if (failed || disposed || !openAttempted || !opened) throw new InvalidOperationException("Original context admission is unavailable.");
            RequireAcquired(producerCopy); RequireAcquired(guardianCopy); RequireDistinct(); RequireWritableRetired();
        }
        private static void DuplicateReadOnly(HandleOutcome source, HandleOutcome target) {
            RequireUsable(source);
            if (target.AcquireAttempted) throw new InvalidOperationException("Original context delivery acquisition is single-use.");
            target.AcquireAttempted = true;
            // FILE_MAP_READ/SECTION_MAP_READ only, exact local table, inherited
            // delivery copy; neither SAME_ACCESS nor CLOSE_SOURCE is allowed.
            bool copied = DuplicateHandle(GetCurrentProcess(), source.Raw, GetCurrentProcess(), out target.Raw, 4, true, 0);
            int error = Marshal.GetLastWin32Error(); target.AcquireSucceeded = copied; target.AcquireReturned = true;
            if (!copied) throw new Win32Exception(error, "Duplicate original read-only context delivery");
            RequireAcquired(target); AssertFlags(target, 1);
        }
        private void UnmapWritable() {
            if (unmapAttempted && !unmapReturned) throw new InvalidOperationException("Unknown context unmap cannot be retried or dereferenced.");
            if (unmapReturned && unmapSucceeded) return;
            if (!viewAttempted || !viewReturned || !viewSucceeded || !Valid(writableView))
                throw new InvalidOperationException("Original writable context view is not known owned.");
            unmapAttempted = true; unmapReturned = false; unmapSucceeded = false;
            bool released = UnmapViewOfFile(writableView); int error = Marshal.GetLastWin32Error();
            unmapSucceeded = released; unmapReturned = true;
            if (!released) throw new Win32Exception(error, "Retire original writable context view");
            // Retain the original numeric value as metadata only. No later read,
            // write, map or unmap may use this successfully released pointer.
        }
        private static void CloseOriginal(HandleOutcome outcome) {
            if (outcome.CloseAttempted && !outcome.CloseReturned) throw new InvalidOperationException("Unknown context close cannot be queried or retried.");
            if (outcome.CloseReturned && outcome.CloseSucceeded) return;
            RequireUsable(outcome);
            outcome.CloseAttempted = true; outcome.CloseReturned = false; outcome.CloseSucceeded = false;
            bool released = CloseHandle(outcome.Raw); int error = Marshal.GetLastWin32Error();
            outcome.CloseSucceeded = released; outcome.CloseReturned = true;
            if (!released) throw new Win32Exception(error, "Close original context handle");
        }
        internal void OpenForProducer(DisposableProducerLaunch candidate) {
            lock (gate) {
                try {
                    RequireOwner(candidate);
                    if (failed || disposed || openAttempted) throw new InvalidOperationException("Original context section opening is single-use.");
                    openAttempted = true;
                    mapping.AcquireAttempted = true;
                    mapping.Raw = CreateFileMapping(new IntPtr(-1), IntPtr.Zero, 4, 0, checked((uint)expectedLength), null);
                    int mappingError = Marshal.GetLastWin32Error(); mapping.AcquireSucceeded = mapping.Raw != IntPtr.Zero; mapping.AcquireReturned = true;
                    if (!mapping.AcquireSucceeded) throw new Win32Exception(mappingError, "Create unnamed original pagefile context section");
                    RequireAcquired(mapping); AssertFlags(mapping, 0);
                    viewAttempted = true;
                    writableView = MapViewOfFile(mapping.Raw, 2, 0, 0, new UIntPtr(checked((uint)expectedLength)));
                    int viewError = Marshal.GetLastWin32Error(); viewSucceeded = writableView != IntPtr.Zero; viewReturned = true;
                    if (!viewSucceeded) throw new Win32Exception(viewError, "Map exact original writable context bytes");
                    if (!Valid(writableView)) throw new InvalidOperationException("Original context view address is invalid.");
                    Marshal.Copy(originalBytes, 0, writableView, expectedLength);
                    DuplicateReadOnly(mapping, producerCopy); RequireDistinct();
                    DuplicateReadOnly(mapping, guardianCopy); RequireDistinct();
                    UnmapWritable(); CloseOriginal(mapping);
                    RequireWritableRetired(); AssertFlags(producerCopy, 1); AssertFlags(guardianCopy, 1);
                    opened = true;
                } catch (Exception) { failed = true; throw; }
            }
        }
        internal void AssertPreparedOwner(DisposableProducerLaunch candidate) {
            lock (gate) {
                try { RequireOwner(candidate); RequireOpened(); }
                catch (Exception) { failed = true; throw; }
            }
        }
        internal ContextSectionDelivery BorrowProducerDelivery(DisposableProducerLaunch candidate) {
            lock (gate) {
                try {
                    RequireOwner(candidate); RequireOpened();
                    if (producerBorrowed || producerRetirementAttempted) throw new InvalidOperationException("Original P context delivery is single-use.");
                    AssertFlags(producerCopy, 1); producerBorrowed = true;
                    return new ContextSectionDelivery(producerCopy.Raw, expectedLength, expectedSha256);
                } catch (Exception) { failed = true; throw; }
            }
        }
        internal void AssertProducerDeliveryPending(DisposableProducerLaunch candidate) {
            lock (gate) {
                try {
                    RequireOwner(candidate); RequireOpened();
                    if (!producerBorrowed || producerRetirementAttempted || producerRetired) throw new InvalidOperationException("Original P context delivery is not pending.");
                    AssertFlags(producerCopy, 1);
                } catch (Exception) { failed = true; throw; }
            }
        }
        private void RequireGuardian(GuardianLaunch candidate, ProducerStopCapability capability, bool bound) {
            RequireOwner(originalOwner);
            if (candidate == null || capability == null || !originalOwner.OwnsGuardian(candidate, capability))
                throw new InvalidOperationException("Only original P's exact registered W may receive its context.");
            if (bound && (!Object.ReferenceEquals(originalGuardian, candidate) || !Object.ReferenceEquals(originalGuardianCapability, capability)))
                throw new InvalidOperationException("Original guardian context binding differs.");
        }
        private void RequireProducerRetired() {
            if (!producerRetired) throw new InvalidOperationException("Original P context delivery is not retired.");
            RequireClosed(producerCopy);
        }
        internal ContextSectionDelivery BorrowGuardianDelivery(GuardianLaunch candidate, ProducerStopCapability capability) {
            lock (gate) {
                try {
                    RequireGuardian(candidate, capability, originalGuardian != null); RequireOpened(); RequireProducerRetired();
                    if (guardianBorrowed || guardianRetirementAttempted || originalGuardian != null) throw new InvalidOperationException("Original W context delivery is single-use.");
                    originalGuardian = candidate; originalGuardianCapability = capability;
                    AssertFlags(guardianCopy, 1); guardianBorrowed = true;
                    return new ContextSectionDelivery(guardianCopy.Raw, expectedLength, expectedSha256);
                } catch (Exception) { failed = true; throw; }
            }
        }
        internal void AssertGuardianDeliveryPending(GuardianLaunch candidate, ProducerStopCapability capability) {
            lock (gate) {
                try {
                    RequireGuardian(candidate, capability, true); RequireOpened(); RequireProducerRetired();
                    if (!guardianBorrowed || guardianRetirementAttempted || guardianRetired) throw new InvalidOperationException("Original W context delivery is not pending.");
                    AssertFlags(guardianCopy, 1);
                } catch (Exception) { failed = true; throw; }
            }
        }
        private static void RetireInheritance(HandleOutcome outcome) {
            RequireUsable(outcome);
            if (outcome.InheritAttempted) throw new InvalidOperationException("Original context inheritance retirement is single-use.");
            outcome.InheritAttempted = true;
            bool changed = SetHandleInformation(outcome.Raw, 1, 0); int error = Marshal.GetLastWin32Error();
            outcome.InheritSucceeded = changed; outcome.InheritReturned = true;
            if (!changed) throw new Win32Exception(error, "Retire original context delivery inheritance");
            AssertFlags(outcome, 0); outcome.InheritVerified = true;
        }
        internal void RetireProducerDelivery(DisposableProducerLaunch candidate) {
            lock (gate) {
                try {
                    RequireOwner(candidate); RequireOpened();
                    if (!producerBorrowed || producerRetirementAttempted) throw new InvalidOperationException("Original P context retirement is single-use.");
                    producerRetirementAttempted = true;
                    RetireInheritance(producerCopy); CloseOriginal(producerCopy); producerRetired = true;
                } catch (Exception) { failed = true; throw; }
            }
        }
        internal void RetireGuardianDelivery(GuardianLaunch candidate, ProducerStopCapability capability) {
            lock (gate) {
                try {
                    RequireGuardian(candidate, capability, true); RequireOpened(); RequireProducerRetired();
                    if (!guardianBorrowed || guardianRetirementAttempted) throw new InvalidOperationException("Original W context retirement is single-use.");
                    guardianRetirementAttempted = true;
                    RetireInheritance(guardianCopy); CloseOriginal(guardianCopy); guardianRetired = true;
                } catch (Exception) { failed = true; throw; }
            }
        }
        internal void AssertGuardianAdmission(GuardianLaunch candidate, ProducerStopCapability capability) {
            lock (gate) {
                try { RequireGuardian(candidate, capability, originalGuardian != null); RequireOpened(); RequireProducerRetired(); }
                catch (Exception) { failed = true; throw; }
            }
        }
        internal void AssertBothDeliveriesRetired(GuardianLaunch candidate, ProducerStopCapability capability) {
            lock (gate) {
                try {
                    RequireGuardian(candidate, capability, true); RequireOpened(); RequireProducerRetired();
                    if (!guardianRetired) throw new InvalidOperationException("Original W context delivery is not retired.");
                    RequireClosed(guardianCopy); // Retained outcomes only; no closed-handle query.
                } catch (Exception) { failed = true; throw; }
            }
        }
        private static void RequireCleanupAcquisition(HandleOutcome outcome, bool mappingCreation) {
            if (!outcome.AcquireAttempted) return;
            // CreateFileMapping's recorded NULL is known failed absence. A failed
            // DuplicateHandle output is conservatively quarantined, even if zero;
            // it never establishes ownership or permission to close that value.
            if (mappingCreation && outcome.AcquireReturned && !outcome.AcquireSucceeded && outcome.Raw == IntPtr.Zero) return;
            RequireAcquired(outcome);
            if (outcome.CloseAttempted && !outcome.CloseReturned) throw new InvalidOperationException("Unknown original context close remains retained.");
        }
        internal void DisposeAfterContainment(DisposableProducerLaunch candidate) {
            lock (gate) {
                try {
                    RequireOwner(candidate); candidate.AssertContextReleaseAllowed(this);
                    if (disposed) return;
                    // Ignore failed/expired admission. Still reject every unknown
                    // native acquisition/release before using any retained value.
                    RequireCleanupAcquisition(mapping, true); RequireCleanupAcquisition(producerCopy, false); RequireCleanupAcquisition(guardianCopy, false);
                    RequireDistinct();
                    if (viewAttempted) {
                        if (!viewReturned || (viewSucceeded && !Valid(writableView)) || (!viewSucceeded && writableView != IntPtr.Zero))
                            throw new InvalidOperationException("Unknown original context view remains retained.");
                        if (viewSucceeded) UnmapWritable();
                    }
                    if (producerCopy.AcquireAttempted) CloseOriginal(producerCopy);
                    if (guardianCopy.AcquireAttempted) CloseOriginal(guardianCopy);
                    if (mapping.AcquireSucceeded) CloseOriginal(mapping);
                    disposed = true;
                } catch (Exception) { failed = true; throw; }
            }
        }
    }
}
