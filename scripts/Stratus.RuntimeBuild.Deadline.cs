// UNQUALIFIED shared P/W STARTUP deadline. Its origin is the retained P wrapper's
// StartSuspended, not earlier caller inputs.Open/preflight, and not yet the future
// compiler-in-P pipeline. This is neither profile approval nor Resume authority.
// GetTickCount64 values share the same native host/boot only because the actual
// retained local P/W launch is required; no public numeric deadline adoption exists.
using System;
using System.Runtime.InteropServices;

namespace Stratus.RuntimeBuild {
    internal sealed class ProducerDeadlineSnapshot {
        internal readonly ulong StartedTick, DeadlineTick;
        internal ProducerDeadlineSnapshot(ulong startedTick, ulong deadlineTick) {
            StartedTick = startedTick; DeadlineTick = deadlineTick;
        }
    }

    internal sealed class ProducerDeadline {
        internal const ulong StartupMilliseconds = 60000;
        private readonly DisposableProducerLaunch originalOwner;
        private readonly object gate = new object();
        private bool beginAttempted, beginReturned, begun, failed;
        private ulong startedTick, deadlineTick, lastObservedTick;
        [DllImport("kernel32.dll")] private static extern ulong GetTickCount64();

        // Managed-only, created by P's private constructor. No clock is sampled
        // until the caller has retained P and invokes its actual StartSuspended.
        internal ProducerDeadline(DisposableProducerLaunch originalOwner) {
            if (originalOwner == null) throw new ArgumentNullException("originalOwner");
            this.originalOwner = originalOwner;
        }
        private void RequireOwner(DisposableProducerLaunch candidate) {
            if (candidate == null || !Object.ReferenceEquals(originalOwner, candidate) || !candidate.OwnsDeadline(this))
                throw new InvalidOperationException("The startup deadline belongs to another original P wrapper.");
        }
        private void RequireBegun() {
            if (failed || !beginAttempted || !beginReturned || !begun || startedTick == 0 ||
                    deadlineTick <= startedTick || deadlineTick - startedTick != StartupMilliseconds)
                throw new InvalidOperationException("The original P startup deadline is failed, unstarted or has an unknown native origin.");
        }
        internal void Begin(DisposableProducerLaunch candidate) {
            lock (gate) {
                try {
                    RequireOwner(candidate);
                    if (failed || beginAttempted) throw new InvalidOperationException("The original P startup clock is single-use and cannot be reset.");
                    beginAttempted = true;
                    startedTick = GetTickCount64(); beginReturned = true;
                    if (startedTick == 0) throw new InvalidOperationException("A positive native startup clock origin is required.");
                    deadlineTick = checked(startedTick + StartupMilliseconds);
                    lastObservedTick = startedTick; begun = true;
                } catch (Exception) { failed = true; throw; }
            }
        }
        // This managed validation is allowed in the registered command factory;
        // actual native time reads remain in retained Start/success boundaries.
        internal void AssertPreparedOwner(DisposableProducerLaunch candidate) {
            lock (gate) {
                try { RequireOwner(candidate); RequireBegun(); }
                catch (Exception) { failed = true; throw; }
            }
        }
        private void AssertActiveCore() {
            RequireBegun();
            ulong now = GetTickCount64();
            if (now < startedTick || now < lastObservedTick)
                throw new InvalidOperationException("The native startup clock regressed or no longer belongs to the original clock domain.");
            if (now >= deadlineTick) throw new TimeoutException("The original shared 60-second P/W startup deadline expired.");
            lastObservedTick = now;
        }
        internal void AssertActive(DisposableProducerLaunch candidate) {
            lock (gate) {
                try { RequireOwner(candidate); AssertActiveCore(); }
                catch (Exception) { failed = true; throw; }
            }
        }
        private void RequireGuardian(GuardianLaunch guardian, ProducerStopCapability capability) {
            RequireOwner(originalOwner);
            if (!originalOwner.OwnsGuardian(guardian, capability))
                throw new InvalidOperationException("Only the exact registered original guardian may observe P's startup deadline.");
        }
        internal void AssertGuardianActive(GuardianLaunch guardian, ProducerStopCapability capability) {
            lock (gate) {
                try { RequireGuardian(guardian, capability); AssertActiveCore(); }
                catch (Exception) { failed = true; throw; }
            }
        }
        internal ProducerDeadlineSnapshot SnapshotForGuardian(GuardianLaunch guardian, ProducerStopCapability capability) {
            lock (gate) {
                try {
                    RequireGuardian(guardian, capability); AssertActiveCore();
                    return new ProducerDeadlineSnapshot(startedTick, deadlineTick);
                } catch (Exception) { failed = true; throw; }
            }
        }
        internal ProducerDeadlineSnapshot SnapshotForProducer(DisposableProducerLaunch candidate) {
            lock (gate) {
                try {
                    RequireOwner(candidate); AssertActiveCore();
                    return new ProducerDeadlineSnapshot(startedTick, deadlineTick);
                } catch (Exception) { failed = true; throw; }
            }
        }
    }
}
