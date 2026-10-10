// UNQUALIFIED W-only implementation. This source belongs to the standalone W
// closure with GuardianProgram, ContextCapture and CompilerJobWire, not L's
// bootstrap assembly. No native qualification or compiler-birth permission is
// conferred by this retained transfer owner or by its private wire ACK.
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;

namespace Stratus.RuntimeBuild.GuardianEntry {
    internal static partial class Program {
        // Process-owned until physical W exit. There is deliberately no public
        // raw-handle adoption, export, Dispose, close retry or alternate reader.
        private sealed class GuardianJobTransfer {
            private readonly IntPtr originalTransfer;
            private readonly string binding;
            // 0=open; 1=claimed (possibly blocked/unknown); 2=claimed and settled;
            // 3=STOP won before any claim. STOP never erases claim history.
            private int phase, verified, stopClosed;
            private IntPtr sourceHandle, localJob;
            private byte[] originalOffer, acknowledgement;
            private Thread worker;
            private bool workerStartAttempted, workerStartReturned;
            private bool duplicateAttempted, duplicateReturned, duplicateSucceeded;
            private int duplicateError;
            private bool preCompareAttempted, preCompareReturned, preCompareSucceeded;
            private int preCompareError;
            private bool postCompareAttempted, postCompareReturned, postCompareSucceeded;
            private int postCompareError;
            private bool flagsAttempted, flagsReturned, flagsSucceeded;
            private uint observedFlags;
            private int flagsError;
            private bool limitsAttempted, limitsReturned, limitsSucceeded;
            private ExtendedLimit initialLimits;
            private uint limitsBytes;
            private int limitsError;
            private bool accountingAttempted, accountingReturned, accountingSucceeded;
            private Accounting initialAccounting;
            private uint accountingBytes;
            private int accountingError;
            private bool terminateAttempted, terminateReturned, terminateSucceeded;
            private int terminateError;
            private bool stopTypeAttempted, stopTypeReturned, stopTypeSucceeded;
            private ExtendedLimit stopTypeLimits;
            private uint stopTypeBytes;
            private int stopTypeError;
            private bool stopQueryAttempted, stopQueryReturned, stopQuerySucceeded;
            private Accounting stoppedAccounting;
            private uint stoppedAccountingBytes;
            private int stopQueryError;
            private int exactJobStopped;
            private int normalAdmissionClosed, normalClosureAttempted, normalClosureProven;
            private bool normalTypeAttempted, normalTypeReturned, normalTypeSucceeded;
            private ExtendedLimit normalTypeLimits;
            private uint normalTypeBytes;
            private int normalTypeError;
            private bool normalQueryAttempted, normalQueryReturned, normalQuerySucceeded;
            private Accounting normalAccounting;
            private uint normalAccountingBytes;
            private int normalQueryError;

            [StructLayout(LayoutKind.Sequential)] private struct BasicLimit {
                public long processTime, jobTime; public uint flags; public UIntPtr min, max;
                public uint active; public UIntPtr affinity; public uint priority, scheduling;
            }
            [StructLayout(LayoutKind.Sequential)] private struct IoCounters { public ulong a, b, c, d, e, f; }
            [StructLayout(LayoutKind.Sequential)] private struct ExtendedLimit {
                public BasicLimit basic; public IoCounters io; public UIntPtr processMemory, jobMemory, peakProcess, peakJob;
            }
            [StructLayout(LayoutKind.Sequential)] private struct Accounting {
                public long user, kernel, periodUser, periodKernel; public uint faults, total, active, terminated;
            }
            [DllImport("kernel32.dll", SetLastError = true)] private static extern bool DuplicateHandle(
                IntPtr sourceProcess, IntPtr source, IntPtr targetProcess, out IntPtr target,
                uint access, bool inherit, uint options);
            [DllImport("kernel32.dll", EntryPoint = "QueryInformationJobObject", SetLastError = true)]
            private static extern bool QueryLimits(IntPtr job, int type, out ExtendedLimit value, uint size, out uint returned);
            [DllImport("kernel32.dll", EntryPoint = "QueryInformationJobObject", SetLastError = true)]
            private static extern bool QueryAccounting(IntPtr job, int type, out Accounting value, uint size, out uint returned);
            [DllImport("kernel32.dll", SetLastError = true)] private static extern bool TerminateJobObject(IntPtr job, uint exitCode);

            internal GuardianJobTransfer(IntPtr transfer, string originalBinding) {
                Require(transfer != IntPtr.Zero && transfer == producerTransfer && originalBinding != null);
                originalTransfer = transfer; binding = originalBinding;
            }
            private static bool ValidHandle(IntPtr value) {
                return value != IntPtr.Zero && value != new IntPtr(-1) && value != new IntPtr(-2) &&
                    value != new IntPtr(-3) && value != new IntPtr(-4) && value != new IntPtr(-5) && value != new IntPtr(-6);
            }
            private static void DemandQualifiedCompilerJobTransfer() {
                // Independent native/runtime/closure qualification is absent.
                // An observed private ACK or context digest cannot replace it.
                throw new InvalidOperationException("W compiler Job transfer is not independently qualified.");
            }
            internal void ClaimOffer(byte[] actual) {
                IntPtr offered = Stratus.RuntimeBuild.CompilerJobWire.ParseOffer(actual, binding);
                Require(Interlocked.CompareExchange(ref stopClosed, 0, 0) == 0 &&
                    Interlocked.CompareExchange(ref phase, 1, 0) == 0);
                // Once claimed, STOP must wait for this exact worker's settlement.
                // Construction/Start interruption is retained as pending, not None.
                sourceHandle = offered; originalOffer = (byte[])actual.Clone();
                worker = new Thread(Run); worker.IsBackground = true;
                workerStartAttempted = true;
                worker.Start();
                workerStartReturned = true;
            }
            private void Run() {
                try {
                    ulong workerTick = startedTick;
                    AssertControlWorker(ref workerTick);
                    Require(Interlocked.CompareExchange(ref phase, 0, 0) == 1 &&
                        Object.ReferenceEquals(jobTransfer, this) && originalTransfer == producerTransfer);
                    // P can receive admission ACK before the writer publishes its
                    // post-write observation; wait here, never on W's watch loop.
                    while (Interlocked.CompareExchange(ref controlAckWritten, 0, 0) == 0) {
                        AssertControlWorker(ref workerTick); Thread.Sleep(10);
                    }
                    AssertControlWorker(ref workerTick);
                    Stratus.RuntimeBuild.CompilerJobWire.RequireExact(originalOffer,
                        Stratus.RuntimeBuild.CompilerJobWire.Offer(binding, sourceHandle));
                    DemandQualifiedCompilerJobTransfer();
                    AssertControlWorker(ref workerTick);
                    preCompareAttempted = true;
                    preCompareSucceeded = CompareObjectHandles(producer, originalTransfer);
                    preCompareError = Marshal.GetLastWin32Error(); preCompareReturned = true;
                    Require(preCompareSucceeded);
                    AssertControlWorker(ref workerTick);
                    // The DUP-only P capability is never queried or waited upon.
                    // Retain the actual out slot and native result before any
                    // fallible admission/query check. No SAME_ACCESS/CLOSE_SOURCE.
                    duplicateAttempted = true;
                    duplicateSucceeded = DuplicateHandle(originalTransfer, sourceHandle, GetCurrentProcess(),
                        out localJob, 0x000C, false, 0);
                    duplicateError = Marshal.GetLastWin32Error();
                    duplicateReturned = true;
                    Require(duplicateSucceeded && ValidHandle(localJob));
                    AssertNoLocalAlias();
                    postCompareAttempted = true;
                    postCompareSucceeded = CompareObjectHandles(producer, originalTransfer);
                    postCompareError = Marshal.GetLastWin32Error(); postCompareReturned = true;
                    Require(postCompareSucceeded);
                    AssertControlWorker(ref workerTick);
                    flagsAttempted = true;
                    flagsSucceeded = GetHandleInformation(localJob, out observedFlags);
                    flagsError = Marshal.GetLastWin32Error(); flagsReturned = true;
                    Require(flagsSucceeded && (observedFlags & 3) == 0);
                    AssertControlWorker(ref workerTick);
                    limitsAttempted = true;
                    limitsSucceeded = QueryLimits(localJob, 9, out initialLimits,
                        (uint)Marshal.SizeOf(typeof(ExtendedLimit)), out limitsBytes);
                    limitsError = Marshal.GetLastWin32Error(); limitsReturned = true;
                    Require(limitsSucceeded && limitsBytes == (uint)Marshal.SizeOf(typeof(ExtendedLimit)));
                    AssertInitialLimits();
                    AssertControlWorker(ref workerTick);
                    accountingAttempted = true;
                    accountingSucceeded = QueryAccounting(localJob, 1, out initialAccounting,
                        (uint)Marshal.SizeOf(typeof(Accounting)), out accountingBytes);
                    accountingError = Marshal.GetLastWin32Error(); accountingReturned = true;
                    Require(accountingSucceeded && accountingBytes == (uint)Marshal.SizeOf(typeof(Accounting)));
                    Require(initialAccounting.active == 0 && initialAccounting.total == 0 && initialAccounting.terminated == 0 &&
                        initialAccounting.user == 0 && initialAccounting.kernel == 0 && initialAccounting.periodUser == 0 &&
                        initialAccounting.periodKernel == 0 && initialAccounting.faults == 0);
                    AssertControlWorker(ref workerTick);
                    acknowledgement = Stratus.RuntimeBuild.CompilerJobWire.Acknowledgement(binding, sourceHandle);
                    Interlocked.Exchange(ref verified, 1);
                } catch (Exception) { Interlocked.Exchange(ref controlFailed, 1); }
                finally {
                    // Last worker action: no Job call, handle use or verification
                    // follows this release publication, even on a failed path.
                    Interlocked.Exchange(ref phase, 2);
                }
            }
            private void AssertInitialLimits() {
                Require(initialLimits.basic.flags == 0x2000 && initialLimits.basic.processTime == 0 && initialLimits.basic.jobTime == 0 &&
                    initialLimits.basic.min == UIntPtr.Zero && initialLimits.basic.max == UIntPtr.Zero && initialLimits.basic.active == 0 &&
                    initialLimits.basic.affinity == UIntPtr.Zero && initialLimits.basic.priority == 0 && initialLimits.basic.scheduling == 0 &&
                    initialLimits.processMemory == UIntPtr.Zero && initialLimits.jobMemory == UIntPtr.Zero &&
                    initialLimits.peakProcess == UIntPtr.Zero && initialLimits.peakJob == UIntPtr.Zero &&
                    initialLimits.io.a == 0 && initialLimits.io.b == 0 && initialLimits.io.c == 0 &&
                    initialLimits.io.d == 0 && initialLimits.io.e == 0 && initialLimits.io.f == 0);
            }
            private void AssertNoLocalAlias() {
                Require(localJob != producer && localJob != producerTransfer && localJob != controlReadHandle &&
                    localJob != controlWriteHandle && localJob != contextSection && localJob != GetStdHandle(-10) &&
                    localJob != GetStdHandle(-11) && localJob != GetStdHandle(-12));
            }
            internal byte[] CopyAcknowledgementIfSettled() {
                // Managed immutable data only; the writer does not query a Job
                // which STOP may already own. Its own fresh admission surrounds IO.
                if (Interlocked.CompareExchange(ref stopClosed, 0, 0) != 0 ||
                    Interlocked.CompareExchange(ref phase, 0, 0) != 2 ||
                    Interlocked.CompareExchange(ref verified, 0, 0) != 1) return null;
                Require(acknowledgement != null);
                byte[] result = (byte[])acknowledgement.Clone();
                return Interlocked.CompareExchange(ref stopClosed, 0, 0) == 0 ? result : null;
            }
            internal void AssertRootSourceForClaim(IntPtr offeredSourceJob) {
                // Managed-only acquire of the original Job worker's publication.
                // This does not wait for the writer's post-Flush Job-ACK flag.
                Require(Object.ReferenceEquals(jobTransfer, this) &&
                    Interlocked.CompareExchange(ref stopClosed, 0, 0) == 0 &&
                    Interlocked.CompareExchange(ref phase, 0, 0) == 2 &&
                    Interlocked.CompareExchange(ref verified, 0, 0) == 1 &&
                    sourceHandle == offeredSourceJob && originalOffer != null && acknowledgement != null);
            }
            internal IntPtr BorrowOriginalJobForRoot(GuardianRootTransfer originalRootOwner, IntPtr offeredSourceJob) {
                Require(originalRootOwner != null && Object.ReferenceEquals(rootTransfer, originalRootOwner));
                AssertRootSourceForClaim(offeredSourceJob);
                Require(duplicateAttempted && duplicateReturned && duplicateSucceeded && ValidHandle(localJob));
                // Only the retained root worker borrows this exact held Job; no
                // second Job duplication, replacement source slot or public export.
                return localJob;
            }
            internal bool IsSettledForStop {
                get {
                    int settled = Interlocked.CompareExchange(ref phase, 0, 0);
                    return Interlocked.CompareExchange(ref stopClosed, 0, 0) == 1 && (settled == 2 || settled == 3);
                }
            }
            internal void CloseAdmissionForNormalTerminal() {
                // PREPARED-side managed quiescence is valid while P is alive.
                // Do not call the physical-death STOP settlement helper here.
                Require(Object.ReferenceEquals(Thread.CurrentThread, controlReader) && Object.ReferenceEquals(jobTransfer, this) &&
                    originalTransfer == producerTransfer && Interlocked.CompareExchange(ref phase, 0, 0) == 2 &&
                    Interlocked.CompareExchange(ref verified, 0, 0) == 1 &&
                    Interlocked.CompareExchange(ref stopAttempted, 0, 0) == 0 &&
                    Interlocked.CompareExchange(ref controlFailed, 0, 0) == 0);
                CloseAdmission();
                Interlocked.Exchange(ref normalAdmissionClosed, 1);
            }
            internal bool IsSettledForNormal {
                get {
                    // A phase-3/no-copy STOP outcome cannot satisfy this fact.
                    return Interlocked.CompareExchange(ref normalAdmissionClosed, 0, 0) == 1 &&
                        Interlocked.CompareExchange(ref phase, 0, 0) == 2 &&
                        Interlocked.CompareExchange(ref verified, 0, 0) == 1;
                }
            }
            internal bool NormalClosureProven { get { return Interlocked.CompareExchange(ref normalClosureProven, 0, 0) == 1; } }
            internal bool IsWorkerSettledForNormal {
                get {
                    Require(Object.ReferenceEquals(Thread.CurrentThread, originalMainThread));
                    return IsSettledForNormal && workerStartAttempted && workerStartReturned && worker != null && worker.Join(0);
                }
            }
            internal void AssertNormalRootBinding(GuardianRootTransfer originalRootOwner, IntPtr offeredSourceJob, IntPtr heldJob) {
                Require(originalRootOwner != null && Object.ReferenceEquals(rootTransfer, originalRootOwner) &&
                    Object.ReferenceEquals(jobTransfer, this) && IsSettledForNormal && originalRootOwner.IsSettledForNormal &&
                    originalTransfer == producerTransfer && sourceHandle == offeredSourceJob && localJob == heldJob &&
                    duplicateAttempted && duplicateReturned && duplicateSucceeded && ValidHandle(localJob));
            }
            internal void ProveNormalClosureAfterProducerExit(GuardianRootTransfer originalRootOwner) {
                // Main-only, single-use read-only proof. No new worker can race
                // later STOP mutation; every unknown native outcome is retained.
                try {
                    AssertNormalTerminalProofAdmission(this, originalRootOwner);
                    Require(Interlocked.CompareExchange(ref normalClosureAttempted, 1, 0) == 0 &&
                        originalTransfer == producerTransfer && binding == terminalObservation.Binding &&
                        duplicateAttempted && duplicateReturned && duplicateSucceeded && ValidHandle(localJob));
                    originalRootOwner.AssertNormalJobBinding(this, sourceHandle, localJob);
                    RequireOriginalProducerNormalExit();
                    AssertStartupDeadline(); AssertNoLocalAlias(); AssertStartupDeadline();
                    normalTypeAttempted = true;
                    normalTypeSucceeded = QueryLimits(localJob, 9, out normalTypeLimits,
                        (uint)Marshal.SizeOf(typeof(ExtendedLimit)), out normalTypeBytes);
                    normalTypeError = Marshal.GetLastWin32Error(); normalTypeReturned = true;
                    Require(normalTypeSucceeded && normalTypeBytes == (uint)Marshal.SizeOf(typeof(ExtendedLimit)));
                    // Dynamic IO/peak counters may now be nonzero; immutable
                    // configured limits must still match the original exact Job.
                    Require(normalTypeLimits.basic.flags == initialLimits.basic.flags &&
                        normalTypeLimits.basic.processTime == initialLimits.basic.processTime && normalTypeLimits.basic.jobTime == initialLimits.basic.jobTime &&
                        normalTypeLimits.basic.min == initialLimits.basic.min && normalTypeLimits.basic.max == initialLimits.basic.max &&
                        normalTypeLimits.basic.active == initialLimits.basic.active && normalTypeLimits.basic.affinity == initialLimits.basic.affinity &&
                        normalTypeLimits.basic.priority == initialLimits.basic.priority && normalTypeLimits.basic.scheduling == initialLimits.basic.scheduling &&
                        normalTypeLimits.processMemory == initialLimits.processMemory && normalTypeLimits.jobMemory == initialLimits.jobMemory);
                    AssertStartupDeadline();
                    normalQueryAttempted = true;
                    normalQuerySucceeded = QueryAccounting(localJob, 1, out normalAccounting,
                        (uint)Marshal.SizeOf(typeof(Accounting)), out normalAccountingBytes);
                    normalQueryError = Marshal.GetLastWin32Error(); normalQueryReturned = true;
                    Require(normalQuerySucceeded && normalAccountingBytes == (uint)Marshal.SizeOf(typeof(Accounting)) &&
                        normalAccounting.active == 0 && normalAccounting.total == 1 && normalAccounting.terminated == 0);
                    AssertNormalTerminalProofAdmission(this, originalRootOwner);
                    Interlocked.Exchange(ref normalClosureProven, 1);
                } catch (Exception) { Interlocked.Exchange(ref normalTerminalFailed, 1); throw; }
            }
            internal bool ExactJobStopped { get { return Interlocked.CompareExchange(ref exactJobStopped, 0, 0) == 1; } }
            internal void CloseAdmission() {
                Interlocked.Exchange(ref stopClosed, 1);
                Interlocked.CompareExchange(ref phase, 3, 0);
            }
            internal bool SettleAfterProducerDeath(Stopwatch originalStopClock) {
                Require(originalStopClock != null && identityEstablished && ObserveProducer());
                while (Interlocked.CompareExchange(ref phase, 0, 0) == 1) {
                    if (originalStopClock.ElapsedMilliseconds >= StopMilliseconds) return false;
                    Thread.Sleep(10);
                }
                if (originalStopClock.ElapsedMilliseconds >= StopMilliseconds) return false;
                return IsSettledForStop;
            }
            internal bool StopAfterProducerDeath(Stopwatch originalStopClock) {
                Require(originalStopClock != null && identityEstablished && ObserveProducer() && IsSettledForStop);
                // Root verification uses this held Job. BOTH workers must have
                // published their last native use before any Job mutation.
                Require(rootTransfer == null || rootTransfer.IsSettledForStop);
                if (originalStopClock.ElapsedMilliseconds >= StopMilliseconds) return false;
                int settled = Interlocked.CompareExchange(ref phase, 0, 0);
                if (settled == 3) return true; // STOP atomically prevented a claim.
                Require(settled == 2);
                if (!duplicateAttempted) return true; // Settled before the native call.
                if (!duplicateReturned) return false; // Unknown, never retried/inferred absent.
                if (!duplicateSucceeded) return false; // No absence proof from a failed transfer.
                if (!ValidHandle(localJob)) return false;
                // Known successful ownership survives all later admission/query
                // failures. P is physically dead and the worker has made its LAST
                // Job use, so this is now the sole Job mutation/observation path.
                AssertNoLocalAlias();
                // A known duplicate is owned, but may not yet have passed the
                // worker's Job type query. Independently establish actual Job
                // type before mutation; failed/unknown type is fatal, no fallback.
                Require(!stopTypeAttempted);
                stopTypeAttempted = true;
                stopTypeSucceeded = QueryLimits(localJob, 9, out stopTypeLimits,
                    (uint)Marshal.SizeOf(typeof(ExtendedLimit)), out stopTypeBytes);
                stopTypeError = Marshal.GetLastWin32Error(); stopTypeReturned = true;
                if (!stopTypeSucceeded || stopTypeBytes != (uint)Marshal.SizeOf(typeof(ExtendedLimit)) ||
                    originalStopClock.ElapsedMilliseconds >= StopMilliseconds) return false;
                Require(!terminateAttempted);
                terminateAttempted = true;
                terminateSucceeded = TerminateJobObject(localJob, 203);
                terminateError = Marshal.GetLastWin32Error(); terminateReturned = true;
                if (!terminateSucceeded || originalStopClock.ElapsedMilliseconds >= StopMilliseconds) return false;
                while (originalStopClock.ElapsedMilliseconds < StopMilliseconds) {
                    // A fresh completed observation may be repeated; an unknown
                    // or failed query never authorizes a retry of its outcome.
                    Require(!stopQueryAttempted || (stopQueryReturned && stopQuerySucceeded));
                    stopQueryAttempted = true; stopQueryReturned = false;
                    stopQuerySucceeded = QueryAccounting(localJob, 1, out stoppedAccounting,
                        (uint)Marshal.SizeOf(typeof(Accounting)), out stoppedAccountingBytes);
                    stopQueryError = Marshal.GetLastWin32Error(); stopQueryReturned = true;
                    if (!stopQuerySucceeded || stoppedAccountingBytes != (uint)Marshal.SizeOf(typeof(Accounting))) return false;
                    if (stoppedAccounting.active == 0) {
                        if (originalStopClock.ElapsedMilliseconds >= StopMilliseconds) return false;
                        Interlocked.Exchange(ref exactJobStopped, 1);
                        return true;
                    }
                    Thread.Sleep(10);
                }
                return false;
            }
        }
    }
}
