// UNQUALIFIED private P<->W transport ownership only. These inherited endpoints
// do not transfer a Job capability, prove an ACK, or authorize P/compiler Resume.
// No public handle adoption or caller-supplied nonce/endpoint API exists.
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Cryptography;

namespace Stratus.RuntimeBuild {
    internal sealed class ChannelEndpoints {
        internal readonly IntPtr Read, Write;
        internal readonly string Nonce;
        internal ChannelEndpoints(IntPtr read, IntPtr write, string nonce) { Read = read; Write = write; Nonce = nonce; }
    }

    internal sealed class ProducerGuardianChannel {
        [StructLayout(LayoutKind.Sequential)] private struct SecurityAttributes {
            internal int Length;
            internal IntPtr Descriptor;
            [MarshalAs(UnmanagedType.Bool)] internal bool Inherit;
        }
        private sealed class Endpoint {
            // Raw is retained forever as the original CreatePipe output. Native
            // use is forbidden after CloseAttempted without a known failed return.
            internal IntPtr Raw;
            internal bool InheritAttempted, InheritReturned, InheritSucceeded, InheritVerified;
            internal bool CloseAttempted, CloseReturned, CloseSucceeded;
        }
        private sealed class PipePair {
            internal readonly Endpoint Read = new Endpoint(), Write = new Endpoint();
            internal bool Attempted, Returned, Succeeded;
        }
        private readonly DisposableProducerLaunch originalOwner;
        private readonly object gate = new object();
        private readonly PipePair producerToGuardian = new PipePair(), guardianToProducer = new PipePair();
        private GuardianLaunch originalGuardian;
        private ProducerStopCapability originalGuardianCapability;
        private bool openAttempted, opened, failed, disposed, producerRetirementAttempted, guardianRetirementAttempted;
        private bool producerRetired, guardianRetired, producerBorrowed, guardianBorrowed;
        private string nonce;
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool CreatePipe(out IntPtr read, out IntPtr write, ref SecurityAttributes security, uint size);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetHandleInformation(IntPtr handle, out uint flags);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool SetHandleInformation(IntPtr handle, uint mask, uint flags);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint GetFileType(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool CloseHandle(IntPtr handle);

        internal ProducerGuardianChannel(DisposableProducerLaunch originalOwner) {
            if (originalOwner == null) throw new ArgumentNullException("originalOwner");
            this.originalOwner = originalOwner;
        }
        private static bool Valid(IntPtr value) {
            return value != IntPtr.Zero && value != new IntPtr(-1) && value != new IntPtr(-2) && value != new IntPtr(-3) &&
                value != new IntPtr(-4) && value != new IntPtr(-5) && value != new IntPtr(-6);
        }
        private static void Check(bool okay, string message) { if (!okay) throw new Win32Exception(Marshal.GetLastWin32Error(), message); }
        private void RequireOwner(DisposableProducerLaunch candidate) {
            if (candidate == null || !Object.ReferenceEquals(originalOwner, candidate) || !candidate.OwnsControlChannel(this))
                throw new InvalidOperationException("Control channel belongs to another original P wrapper.");
        }
        private static void RequirePair(PipePair pair) {
            if (!pair.Attempted || !pair.Returned || !pair.Succeeded || !Valid(pair.Read.Raw) || !Valid(pair.Write.Raw) || pair.Read.Raw == pair.Write.Raw)
                throw new InvalidOperationException("Unknown or incomplete CreatePipe outputs cannot be queried, closed or reused.");
        }
        private void RequireDistinct() {
            RequirePair(producerToGuardian); RequirePair(guardianToProducer);
            if (producerToGuardian.Read.Raw == guardianToProducer.Read.Raw || producerToGuardian.Read.Raw == guardianToProducer.Write.Raw ||
                    producerToGuardian.Write.Raw == guardianToProducer.Read.Raw || producerToGuardian.Write.Raw == guardianToProducer.Write.Raw)
                throw new InvalidOperationException("Original P/W pipe endpoint roles alias.");
        }
        private void RequireOpened() {
            if (failed || disposed || !openAttempted || !opened || nonce == null || nonce.Length != 64)
                throw new InvalidOperationException("Original control channel is failed, closed, or unprepared.");
            RequireDistinct();
        }
        private static void RequireUsable(Endpoint endpoint) {
            if (!Valid(endpoint.Raw) || endpoint.CloseSucceeded || (endpoint.CloseAttempted && !endpoint.CloseReturned))
                throw new InvalidOperationException("Original control endpoint has a closed or unknown close outcome.");
        }
        private static void AssertInheritedPipe(Endpoint endpoint) {
            RequireUsable(endpoint);
            uint flags; Check(GetHandleInformation(endpoint.Raw, out flags), "Read original control endpoint inheritance");
            if ((flags & 3) != 1 || GetFileType(endpoint.Raw) != 3)
                throw new InvalidOperationException("Original control endpoint must be an unprotected inheritable pipe.");
        }
        private static void CreateOriginalPair(PipePair pair) {
            if (pair.Attempted) throw new InvalidOperationException("Original control pipe creation is single-use.");
            SecurityAttributes security = new SecurityAttributes(); security.Length = Marshal.SizeOf(typeof(SecurityAttributes)); security.Inherit = true;
            pair.Attempted = true;
            bool created = CreatePipe(out pair.Read.Raw, out pair.Write.Raw, ref security, 4096);
            int error = Marshal.GetLastWin32Error(); pair.Succeeded = created; pair.Returned = true;
            // Microsoft specifies indeterminate outputs on FALSE. Even all-zero
            // failed outputs are quarantined, never interpreted as known absence.
            if (!created) throw new Win32Exception(error, "Create original private P/W control pipe");
            RequirePair(pair); AssertInheritedPipe(pair.Read); AssertInheritedPipe(pair.Write);
        }
        internal void OpenForProducer(DisposableProducerLaunch candidate) {
            lock (gate) {
                try {
                    RequireOwner(candidate);
                    if (failed || disposed || openAttempted) throw new InvalidOperationException("Original control transport open is single-use.");
                    openAttempted = true;
                    byte[] entropy = new byte[32]; using (RandomNumberGenerator random = RandomNumberGenerator.Create()) random.GetBytes(entropy);
                    nonce = BitConverter.ToString(entropy).Replace("-", "").ToLowerInvariant();
                    CreateOriginalPair(producerToGuardian); CreateOriginalPair(guardianToProducer);
                    RequireDistinct(); opened = true;
                } catch (Exception) { failed = true; throw; }
            }
        }
        internal void AssertPreparedOwner(DisposableProducerLaunch candidate) {
            lock (gate) {
                try { RequireOwner(candidate); RequireOpened(); }
                catch (Exception) { failed = true; throw; }
            }
        }
        internal string Nonce { get { lock (gate) { RequireOpened(); return nonce; } } }
        internal ChannelEndpoints BorrowProducerEndpoints(DisposableProducerLaunch candidate) {
            lock (gate) {
                try {
                    RequireOwner(candidate); RequireOpened();
                    if (producerBorrowed || producerRetirementAttempted) throw new InvalidOperationException("The exact P endpoint delivery is single-use.");
                    AssertInheritedPipe(guardianToProducer.Read); AssertInheritedPipe(producerToGuardian.Write);
                    producerBorrowed = true;
                    return new ChannelEndpoints(guardianToProducer.Read.Raw, producerToGuardian.Write.Raw, nonce);
                } catch (Exception) { failed = true; throw; }
            }
        }
        internal void AssertProducerDeliveryPending(DisposableProducerLaunch candidate) {
            lock (gate) {
                try {
                    RequireOwner(candidate); RequireOpened();
                    if (!producerBorrowed || producerRetirementAttempted || producerRetired)
                        throw new InvalidOperationException("Original P delivery is not pending.");
                    AssertInheritedPipe(guardianToProducer.Read); AssertInheritedPipe(producerToGuardian.Write);
                } catch (Exception) { failed = true; throw; }
            }
        }
        private void RequireGuardian(GuardianLaunch candidate, ProducerStopCapability capability, bool requireBound) {
            RequireOwner(originalOwner);
            if (candidate == null || capability == null || !originalOwner.OwnsGuardian(candidate, capability))
                throw new InvalidOperationException("Only original P's exact registered W may use this channel.");
            if (requireBound && (!Object.ReferenceEquals(originalGuardian, candidate) || !Object.ReferenceEquals(originalGuardianCapability, capability)))
                throw new InvalidOperationException("Original W channel binding changed.");
        }
        private static void RequireClosed(Endpoint endpoint) {
            if (!endpoint.CloseAttempted || !endpoint.CloseReturned || !endpoint.CloseSucceeded)
                throw new InvalidOperationException("The original L endpoint has not been proved closed.");
        }
        private void RequireProducerRetired() {
            if (!producerRetired) throw new InvalidOperationException("Original P delivery has not retired both L endpoints.");
            RequireClosed(guardianToProducer.Read); RequireClosed(producerToGuardian.Write);
        }
        internal void AssertGuardianAdmission(GuardianLaunch candidate, ProducerStopCapability capability) {
            lock (gate) {
                try { RequireGuardian(candidate, capability, originalGuardian != null); RequireOpened(); RequireProducerRetired(); }
                catch (Exception) { failed = true; throw; }
            }
        }
        internal ChannelEndpoints BorrowGuardianEndpoints(GuardianLaunch candidate, ProducerStopCapability capability) {
            lock (gate) {
                try {
                    RequireGuardian(candidate, capability, originalGuardian != null); RequireOpened(); RequireProducerRetired();
                    if (guardianBorrowed || guardianRetirementAttempted || originalGuardian != null)
                        throw new InvalidOperationException("The exact W endpoint delivery is single-use.");
                    originalGuardian = candidate; originalGuardianCapability = capability;
                    AssertInheritedPipe(producerToGuardian.Read); AssertInheritedPipe(guardianToProducer.Write);
                    guardianBorrowed = true;
                    return new ChannelEndpoints(producerToGuardian.Read.Raw, guardianToProducer.Write.Raw, nonce);
                } catch (Exception) { failed = true; throw; }
            }
        }
        internal void AssertGuardianDeliveryPending(GuardianLaunch candidate, ProducerStopCapability capability) {
            lock (gate) {
                try {
                    RequireGuardian(candidate, capability, true); RequireOpened(); RequireProducerRetired();
                    if (!guardianBorrowed || guardianRetirementAttempted || guardianRetired)
                        throw new InvalidOperationException("Original W delivery is not pending.");
                    AssertInheritedPipe(producerToGuardian.Read); AssertInheritedPipe(guardianToProducer.Write);
                } catch (Exception) { failed = true; throw; }
            }
        }
        private static void RetireInheritance(Endpoint endpoint) {
            RequireUsable(endpoint);
            if (endpoint.InheritAttempted) throw new InvalidOperationException("Control endpoint inheritance retirement is single-use.");
            endpoint.InheritAttempted = true;
            bool changed = SetHandleInformation(endpoint.Raw, 1, 0); int error = Marshal.GetLastWin32Error();
            endpoint.InheritSucceeded = changed; endpoint.InheritReturned = true;
            if (!changed) throw new Win32Exception(error, "Retire original L control endpoint inheritance");
            uint flags; Check(GetHandleInformation(endpoint.Raw, out flags), "Verify retired control endpoint flags");
            if ((flags & 3) != 0) throw new InvalidOperationException("Retired control endpoint flags differ.");
            endpoint.InheritVerified = true;
        }
        private static void CloseOriginal(Endpoint endpoint) {
            // SET inheritance cannot release/reuse a handle; its failed outcome
            // does not prohibit containment close. Unknown CLOSE does prohibit it.
            if (endpoint.CloseAttempted && !endpoint.CloseReturned) throw new InvalidOperationException("Unknown original control close outcome cannot be retried.");
            if (endpoint.CloseReturned && endpoint.CloseSucceeded) return;
            RequireUsable(endpoint);
            endpoint.CloseAttempted = true; endpoint.CloseReturned = false;
            bool closed = CloseHandle(endpoint.Raw); int error = Marshal.GetLastWin32Error(); endpoint.CloseSucceeded = closed; endpoint.CloseReturned = true;
            if (!closed) throw new Win32Exception(error, "Close original L control endpoint");
        }
        internal void RetireProducerDelivery(DisposableProducerLaunch candidate) {
            lock (gate) {
                try {
                    RequireOwner(candidate); RequireOpened();
                    if (!producerBorrowed || producerRetirementAttempted) throw new InvalidOperationException("Original P delivery retirement is single-use.");
                    producerRetirementAttempted = true;
                    RetireInheritance(guardianToProducer.Read); CloseOriginal(guardianToProducer.Read);
                    RetireInheritance(producerToGuardian.Write); CloseOriginal(producerToGuardian.Write);
                    producerRetired = true;
                } catch (Exception) { failed = true; throw; }
            }
        }
        internal void RetireGuardianDelivery(GuardianLaunch candidate, ProducerStopCapability capability) {
            lock (gate) {
                try {
                    RequireGuardian(candidate, capability, true); RequireOpened(); RequireProducerRetired();
                    if (!guardianBorrowed || guardianRetirementAttempted) throw new InvalidOperationException("Original W delivery retirement is single-use.");
                    guardianRetirementAttempted = true;
                    RetireInheritance(producerToGuardian.Read); CloseOriginal(producerToGuardian.Read);
                    RetireInheritance(guardianToProducer.Write); CloseOriginal(guardianToProducer.Write);
                    guardianRetired = true;
                } catch (Exception) { failed = true; throw; }
            }
        }
        internal void AssertBothDeliveriesRetired(GuardianLaunch candidate, ProducerStopCapability capability) {
            lock (gate) {
                try {
                    RequireGuardian(candidate, capability, true); RequireOpened(); RequireProducerRetired();
                    if (!guardianRetired) throw new InvalidOperationException("Original W delivery has not retired both L endpoints.");
                    RequireClosed(producerToGuardian.Read); RequireClosed(guardianToProducer.Write);
                    // Managed outcome checks only: never query a closed value.
                } catch (Exception) { failed = true; throw; }
            }
        }
        private static void CloseKnownPair(PipePair pair) {
            if (!pair.Attempted) return;
            RequirePair(pair); // FALSE/unknown CreatePipe outputs are quarantined.
            CloseOriginal(pair.Read); CloseOriginal(pair.Write);
        }
        internal void DisposeAfterContainment(DisposableProducerLaunch candidate) {
            lock (gate) {
                try {
                    RequireOwner(candidate); candidate.AssertChannelReleaseAllowed(this);
                    if (disposed) return;
                    if (producerToGuardian.Returned && producerToGuardian.Succeeded && guardianToProducer.Returned && guardianToProducer.Succeeded)
                        RequireDistinct();
                    CloseKnownPair(producerToGuardian); CloseKnownPair(guardianToProducer);
                    disposed = true;
                } catch (Exception) { failed = true; throw; }
            }
        }
    }
}
